"""Bounded JSON-lines client for a single generation-pinned Rust process."""
import base64
import hashlib
import json
import os
from pathlib import Path
import selectors
import re
import subprocess
import tempfile
import threading
import time

from .fixture import _no_duplicate_keys
from .recognition_types import RecognitionError, VerifiedImage, require


class RustTileReader:
    def __init__(self, reference, *, binary=None, timeout=30):
        require(type(timeout) in (int,float) and 0 < timeout <=60,'ReaderTimeout','invalid reader timeout')
        self.reference = reference
        self.timeout = timeout
        self._lock = threading.RLock()
        self._buffer = bytearray()
        self._sequence = 0
        self._closed = False
        self._stderr = tempfile.TemporaryFile()
        self._process = None
        executable = Path(binary or os.environ.get('CRYSTAL_RUST_BINARY','target/debug/crystal-core')).absolute()
        try:
            self._process = subprocess.Popen(
                [str(executable),'serve',str(reference.path),'--allow-unsigned-development'],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self._stderr,bufsize=0)
            hello = self._response(65536)
            require(hello.get('protocol_version')==1 and hello.get('generation_id')==reference.generation_id
                    and hello.get('manifest_sha256')==reference.manifest_sha256,
                    'GenerationChanged','Rust reader does not match the active pointer snapshot')
            require(isinstance(hello.get('tiles'),list) and len(hello['tiles'])==32,
                    'ReaderProtocolInvalid','invalid tile catalogue')
            self.descriptors = tuple(hello['tiles'])
        except BaseException:
            self.close()
            raise

    @property
    def closed(self):
        return self._closed

    def _response(self, limit):
        deadline = time.monotonic()+self.timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self._process.stdout,selectors.EVENT_READ)
            while b'\n' not in self._buffer:
                remaining = deadline-time.monotonic()
                require(remaining>0 and selector.select(max(0,remaining)),
                        'ReaderTimeout','Rust reader timed out')
                block = os.read(self._process.stdout.fileno(),65536)
                if not block:
                    self._stderr.seek(0)
                    diagnostic = self._stderr.read(4096).decode('utf-8',errors='replace')
                    match = re.search(r'ERROR: ([A-Za-z]+)(?::|\n|$)',diagnostic)
                    code = match.group(1) if match else 'ReaderUnavailable'
                    raise RecognitionError(code,'Rust verification failed or the reader exited')
                self._buffer.extend(block)
                require(len(self._buffer)<=limit,'ReaderProtocolInvalid','response exceeds limit')
        line,_,tail = self._buffer.partition(b'\n')
        self._buffer = bytearray(tail)
        try:
            value = json.loads(line,object_pairs_hook=_no_duplicate_keys)
        except (ValueError,RecursionError) as exc:
            raise RecognitionError('ReaderProtocolInvalid','invalid response JSON') from exc
        require(isinstance(value,dict),'ReaderProtocolInvalid','response must be an object')
        return value

    def read(self, descriptor):
        with self._lock:
            require(not self.closed,'ReaderUnavailable','reader is closed')
            self._sequence += 1
            request = {'request_id':self._sequence,'generation_id':self.reference.generation_id,**descriptor['key']}
            try:
                self._process.stdin.write((json.dumps(request)+'\n').encode())
                self._process.stdin.flush()
                reply = self._response(2*1024*1024)
                require(reply.get('request_id')==self._sequence,'ReaderProtocolInvalid','request ID mismatch')
                if not reply.get('ok'):
                    error = reply.get('error',{})
                    raise RecognitionError(error.get('code','ReaderUnavailable'),'Rust rejected the tile request')
                require(reply.get('descriptor')==descriptor,'GenerationChanged','tile descriptor changed')
                payload = base64.b64decode(reply['payload_base64'],validate=True)
                sha = bytes(descriptor['stored_sha256']).hex()
                require(len(payload)<=1024*1024 and hashlib.sha256(payload).hexdigest()==sha,
                        'PayloadHashMismatch','bridge payload does not match its verified descriptor')
                key = descriptor['key']
                return VerifiedImage(self.reference.generation_id,key['variant'],key['zoom'],key['x'],key['y'],
                                     tuple(descriptor['bounds']),descriptor['source_image'],
                                     descriptor['acquisition_date'],sha,payload)
            except BaseException:
                self.close()
                raise

    def close(self):
        with self._lock:
            if self.closed:
                return
            self._closed = True
            try:
                if self._process:
                    try:
                        self._process.stdin.close()
                    except OSError:
                        pass
                    try:
                        self._process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait(timeout=5)
            finally:
                if self._process:
                    self._process.stdout.close()
                self._stderr.close()

    def __enter__(self):
        return self

    def __exit__(self,*args):
        self.close()
