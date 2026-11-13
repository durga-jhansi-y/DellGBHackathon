"""Private, atomic JSON job records; one scheduler owns a directory at a time."""
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import stat

from .fixture import _no_duplicate_keys, canonical
from .recognition_types import RecognitionError, require

TERMINAL = {'completed','failed','cancelled','interrupted'}


class FindingsStore:
    def __init__(self, root):
        self.root = Path(root).absolute()
        require(not self.root.is_symlink(),'UnsafePath','findings directory may not be a symlink')
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        self._owner = None

    def acquire(self):
        require(self._owner is None,'QueueInUse','findings directory already owned')
        fd = os.open(self.root/'.queue.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            require(stat.S_ISREG(os.fstat(fd).st_mode),'UnsafePath','queue lock must be a regular file')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BaseException as exc:
            os.close(fd)
            if not isinstance(exc,OSError):
                raise
            raise RecognitionError('QueueInUse','another scheduler owns this findings directory') from exc
        self._owner = fd

    def close(self):
        if self._owner is not None:
            os.close(self._owner)
            self._owner = None

    def _path(self, request_id):
        require(isinstance(request_id,str) and re.fullmatch('[0-9a-f]{32}',request_id),
                'RequestInvalid','invalid request ID')
        path = self.root/(request_id+'.json')
        require(not path.is_symlink(),'UnsafePath','symlink findings file')
        return path

    def load(self, request_id):
        path = self._path(request_id)
        with path.open('rb') as stream:
            data = stream.read(65537)
        require(len(data)<=65536,'FindingsInvalid','record exceeds limit')
        try:
            record = json.loads(data,object_pairs_hook=_no_duplicate_keys)
            normalized = canonical(record)
        except (ValueError,TypeError,RecursionError) as exc:
            raise RecognitionError('FindingsInvalid','invalid findings JSON') from exc
        require(isinstance(record,dict) and record.get('request_id')==request_id
                and type(record.get('schema_version')) is int and record['schema_version']==1
                and isinstance(record.get('status'),str) and record['status'] in TERMINAL|{'queued','running'}
                and data==normalized,
                'FindingsInvalid','invalid findings record')
        return record

    def save(self, record):
        destination = self._path(record['request_id'])
        data = canonical(record)
        require(len(data)<=65536,'FindingsInvalid','record exceeds limit')
        fd,name = tempfile.mkstemp(prefix='.job-',dir=self.root)
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name,destination)
            directory = os.open(self.root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(name).unlink(missing_ok=True)

    def reconcile(self, now):
        require(self._owner is not None,'QueueInUse','recovery requires scheduler ownership')
        for path in self.root.glob('*.json'):
            request_id = path.stem
            require(re.fullmatch('[0-9a-f]{32}',request_id),'FindingsInvalid','unexpected findings filename')
            record = self.load(request_id)
            if record['status'] not in TERMINAL:
                record.update(status='interrupted',finished_at=now,result=None,
                              error={'code':'ProcessInterrupted','message':'Scheduler stopped; explicit resubmission required.'})
                self.save(record)
        for path in self.root.glob('.job-*'):
            require(re.fullmatch(r'\.job-[a-z0-9_]{8}',path.name) and path.is_file() and not path.is_symlink(),
                    'UnsafePath','unexpected temporary findings file')
            path.unlink()
