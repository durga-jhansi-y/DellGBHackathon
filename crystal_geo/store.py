"""POSIX development store: staged import, atomic activation, pinned tile reads.

Unsigned development packages require explicit opt-in on every store instance.
All generations are retained; deletion and garbage collection are out of scope.
"""
from __future__ import annotations

from contextlib import contextmanager, closing
from dataclasses import dataclass
import errno
import fcntl
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import threading
import uuid
import weakref

from .fixture import (
    CONTAINER_PATH, DATASET_ID, HEADER_SIZE, MAX_CONTAINER, MAX_INDEX,
    FixtureError, _no_duplicate_keys, canonical, decode_payload, digest,
    header, safe_file, validate_generation_id, verified_manifest,
)


class StoreError(FixtureError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(f'{code}: {message}')


def check(condition, code, message):
    if not condition:
        raise StoreError(code, message)


def errors(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except StoreError:
            raise
        except FixtureError as exc:
            raise StoreError('GenerationInvalid', str(exc)) from exc
        except sqlite3.Error as exc:
            raise StoreError('IndexInvalid', str(exc)) from exc
        except OSError as exc:
            code = 'StorageFull' if exc.errno == errno.ENOSPC else 'StorageError'
            raise StoreError(code, str(exc)) from exc
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
            raise StoreError('StateInvalid', str(exc)) from exc
    return call


def read_record(path, limit=1024):
    path = safe_file(path.parent, path.name)
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    check(len(raw) <= limit, 'StateInvalid', 'record exceeds size limit')
    value = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
    check(isinstance(value, dict) and raw == canonical(value), 'StateInvalid', 'noncanonical record')
    return value


def fingerprint(stream, limit):
    size = os.fstat(stream).st_size
    check(0 < size <= limit, 'GenerationInvalid', 'file size exceeds limit')
    result = hashlib.sha256()
    offset = 0
    while offset < size:
        block = os.pread(stream, min(65536, size-offset), offset)
        check(block, 'GenerationInvalid', 'truncated file')
        result.update(block)
        offset += len(block)
    return result.hexdigest()


@dataclass(frozen=True)
class DecodedTile:
    generation_id: str
    variant: str
    zoom: int
    x: int
    y: int
    bounds: tuple[float, float, float, float]
    source_image: str
    acquisition_date: str
    rgba: bytes
    width: int = 256
    height: int = 256


class GenerationLease:
    """A pinned container descriptor and an immutable, verified index snapshot."""
    def __init__(self, package, manifest):
        self.generation_id = manifest['generation_id']
        self._size = manifest['containers'][0]['size']
        self._lock = threading.Lock()
        index = safe_file(package, 'index.sqlite')
        with index.open('rb') as stream:
            data = stream.read(MAX_INDEX + 1)
        check(len(data) == manifest['index']['size'] and digest(data) == manifest['index']['sha256'],
              'IndexInvalid', 'index changed while opening generation')
        # Deserialize the exact hashed bytes: subsequent on-disk index changes cannot
        # change descriptors already captured by this lease.
        with closing(sqlite3.connect(':memory:')) as db:
            db.deserialize(data)
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            rows = db.execute(
                'SELECT t.variant,t.zoom,t.tile_x,t.tile_y,t.west,t.south,t.east,t.north,'
                't.payload_offset,t.payload_length,t.stored_sha256,s.source_image,s.acquisition_date '
                'FROM tiles t JOIN sources s USING(source_id) ORDER BY t.tile_id LIMIT 33').fetchall()
        check(len(rows) == 32, 'IndexInvalid', 'incomplete tile snapshot')
        self._tiles = {(row[0],row[1],row[2],row[3]): row[4:] for row in rows}
        path = safe_file(package, CONTAINER_PATH)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            check(os.fstat(fd).st_size == self._size
                  and fingerprint(fd, MAX_CONTAINER) == manifest['containers'][0]['sha256'],
                  'GenerationInvalid', 'container changed while opening generation')
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        self._finalizer = weakref.finalize(self, os.close, fd)

    @property
    def closed(self):
        return not self._finalizer.alive

    def close(self):
        with self._lock:
            self._finalizer()

    def __enter__(self):
        check(not self.closed, 'LeaseClosed', 'generation lease is closed')
        return self

    def __exit__(self, *args):
        self.close()

    @errors
    def read_tile(self, *, generation_id, variant, zoom, x, y):
        with self._lock:
            check(not self.closed, 'LeaseClosed', 'generation lease is closed')
            check(generation_id == self.generation_id, 'GenerationChanged', 'request does not match pinned generation')
            check(variant in ('before','after') and all(type(v) is int for v in (zoom,x,y)),
                  'TileNotFound', 'invalid tile key')
            row = self._tiles.get((variant,zoom,x,y))
            check(row is not None, 'TileNotFound', 'tile is not in this generation')
            west,south,east,north,offset,length,expected_hash,source,date = row
            check(os.fstat(self._fd).st_size == self._size, 'PayloadOutOfBounds', 'container size changed')
            check(os.pread(self._fd, HEADER_SIZE, 0) == header(self.generation_id),
                  'GenerationChanged', 'container header changed')
            check(HEADER_SIZE <= offset <= self._size and 0 < length <= min(1048576,self._size-offset),
                  'PayloadOutOfBounds', 'invalid tile range')
            payload = os.pread(self._fd, length, offset)
            check(len(payload) == length, 'PayloadOutOfBounds', 'short tile read')
            check(hashlib.sha256(payload).digest() == expected_hash, 'PayloadHashMismatch', 'tile bytes changed')
            rgba = decode_payload(payload)
            return DecodedTile(self.generation_id, variant, zoom, x, y,
                               (west,south,east,north), source, date, rgba)


class GenerationStore:
    def __init__(self, root, *, allow_unsigned_development=False, checkpoint=None):
        self.root = Path(root).absolute()
        self.dataset = self.root/'datasets'/DATASET_ID
        self.generations = self.dataset/'generations'
        self.staging = self.dataset/'staging'
        self.allow_unsigned_development = allow_unsigned_development
        # Fault injection is a library/testing hook, never an environment setting.
        self._checkpoint = checkpoint or (lambda event: None)

    def _allow(self):
        check(self.allow_unsigned_development is True, 'UnsignedGenerationRejected',
              'explicit --allow-unsigned-development is required; never use these packages in production')

    def _sync_dir(self, path, event):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        self._checkpoint(event)

    def _directory(self, path, *, create=False, label='directory'):
        check(not path.is_symlink(), 'UnsafePath', f'symlink directory: {path}')
        if not path.exists() and create:
            check(path.parent.is_dir(), 'StorageError', 'store parent directory must already exist')
            # The store root is created before its lock exists. Two first-time
            # writers may reach mkdir together; both then take the same file lock.
            path.mkdir(mode=0o700, exist_ok=True)
            self._sync_dir(path, label+'.self')
            self._sync_dir(path.parent, label+'.parent')
        check(path.is_dir(), 'GenerationUnavailable', f'missing directory: {path}')

    def _layout(self, create=False):
        for path,label in [(self.root,'root'),(self.root/'datasets','datasets'),
                           (self.dataset,'dataset'),(self.generations,'generations'),(self.staging,'staging')]:
            self._directory(path, create=create, label=label)

    @contextmanager
    def _writer(self):
        self._directory(self.root, create=True, label='root')
        fd = os.open(self.root/'.writer.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            check(stat.S_ISREG(os.fstat(fd).st_mode), 'UnsafePath', 'writer lock is not a regular file')
            fcntl.flock(fd, fcntl.LOCK_EX)
            self._layout(create=True)
            yield
        finally:
            os.close(fd)

    def _write(self, path, data, event):
        with path.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), 0o444)
            os.fsync(stream.fileno())
        self._checkpoint(event)

    def _copy(self, source, destination, limit, event):
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as incoming:
            check(stat.S_ISREG(os.fstat(incoming.fileno()).st_mode), 'UnsafePath', 'source is not a regular file')
            with destination.open('xb') as outgoing:
                count = 0
                while block := incoming.read(65536):
                    count += len(block)
                    check(count <= limit, 'GenerationInvalid', 'source file exceeds limit')
                    outgoing.write(block)
                outgoing.flush()
                os.fchmod(outgoing.fileno(), 0o444)
                os.fsync(outgoing.fileno())
        self._checkpoint(event)

    def _ready(self, generation_id):
        validate_generation_id(generation_id)
        location = self.generations/generation_id
        self._directory(location)
        self._directory(location/'package')
        check({p.name for p in location.iterdir()} == {'package','READY'},
              'GenerationInvalid', 'incomplete published generation')
        manifest = verified_manifest(location/'package')
        expected = {'generation_id':generation_id, 'manifest_sha256':digest(canonical(manifest))}
        check(manifest['generation_id'] == generation_id and read_record(location/'READY') == expected,
              'GenerationInvalid', 'READY marker or generation mismatch')
        return manifest

    @errors
    def install(self, source):
        self._allow()
        source = Path(source).absolute()
        # Use a bounded allowlist; paths from the unsigned manifest never direct copying.
        paths = [(name,safe_file(source,name),limit) for name,limit in
                 [('manifest.json',65536),('index.sqlite',MAX_INDEX),(CONTAINER_PATH,MAX_CONTAINER)]]
        check({p.name for p in source.iterdir()} == {'manifest.json','index.sqlite','containers'}
              and {p.name for p in (source/'containers').iterdir()} == {'container-000001.bin'},
              'GenerationInvalid', 'unexpected package files')
        with self._writer():
            stage = self.staging/(uuid.uuid4().hex+'.partial')
            stage.mkdir(mode=0o700)
            self._sync_dir(self.staging, 'stage.created')
            package = stage/'package'
            package.mkdir()
            (package/'containers').mkdir()
            for name,path,limit in paths:
                self._copy(path,package/name,limit,'copy.'+name)
            self._sync_dir(package/'containers', 'package.containers')
            self._sync_dir(package, 'package.synced')
            manifest = verified_manifest(package)
            generation_id = manifest['generation_id']
            record = {'generation_id':generation_id,'manifest_sha256':digest(canonical(manifest))}
            self._checkpoint('package.verified')
            self._write(stage/'READY',canonical(record),'ready.file')
            self._sync_dir(stage,'ready.directory')
            destination = self.generations/generation_id
            if destination.exists() or destination.is_symlink():
                existing = self._ready(generation_id)
                check(digest(canonical(existing)) == record['manifest_sha256'],
                      'GenerationConflict', 'generation identifier already has different content')
                shutil.rmtree(stage)
            else:
                stage.rename(destination)
                self._checkpoint('generation.renamed')
            self._sync_dir(self.generations, 'generation.published')
            self._sync_dir(self.staging, 'stage.removed')
            active_record = {'dataset_id':DATASET_ID, **record}
            pointer = {'record':active_record,'sha256':digest(canonical(active_record))}
            temporary = self.dataset/('.active-'+uuid.uuid4().hex+'.tmp')
            check(not (self.dataset/'active').is_symlink(), 'UnsafePath', 'symlink active pointer')
            self._write(temporary,canonical(pointer),'active.file')
            os.replace(temporary,self.dataset/'active')
            try:
                self._checkpoint('active.replaced')
                self._sync_dir(self.dataset,'active.directory')
            except OSError as exc:
                raise StoreError('ActivationUncertain',
                                 'pointer was replaced but its durability is unconfirmed; '
                                 'inspect the active generation before retrying') from exc
            return active_record

    @errors
    def active_info(self):
        self._allow()
        self._layout()
        active = self.dataset/'active'
        check(not active.is_symlink(), 'UnsafePath', 'symlink active pointer')
        if not active.exists():
            return None
        pointer = read_record(active)
        check(set(pointer) == {'record','sha256'} and isinstance(pointer['record'],dict),
              'StateInvalid', 'invalid active pointer')
        record = pointer['record']
        check(set(record) == {'dataset_id','generation_id','manifest_sha256'}
              and record['dataset_id'] == DATASET_ID
              and pointer['sha256'] == digest(canonical(record)), 'StateInvalid', 'active checksum mismatch')
        validate_generation_id(record['generation_id'])
        check(isinstance(record['manifest_sha256'],str)
              and re.fullmatch('[0-9a-f]{64}',record['manifest_sha256']), 'StateInvalid', 'invalid manifest hash')
        return record

    @errors
    def open_active(self):
        record = self.active_info()
        check(record is not None, 'GenerationUnavailable', 'no active generation')
        manifest = self._ready(record['generation_id'])
        check(digest(canonical(manifest)) == record['manifest_sha256'],
              'GenerationInvalid', 'active manifest hash mismatch')
        return GenerationLease(self.generations/record['generation_id']/'package', manifest)

    @errors
    def recover(self):
        """Clean interrupted temporary work; never infer or change the active generation."""
        self._allow()
        with self._writer():
            removed = 0
            for path in self.staging.iterdir():
                check(re.fullmatch('[0-9a-f]{32}\\.partial',path.name) and path.is_dir()
                      and not path.is_symlink(), 'UnsafePath', 'unexpected staging entry')
                check(not any(p.is_symlink() for p in path.rglob('*')), 'UnsafePath', 'symlink in staging')
                shutil.rmtree(path)
                removed += 1
            self._sync_dir(self.staging,'recovery.staging')
            for path in self.dataset.glob('.active-*.tmp'):
                check(re.fullmatch(r'\.active-[0-9a-f]{32}\.tmp',path.name)
                      and path.is_file() and not path.is_symlink(), 'UnsafePath', 'unexpected pointer temp file')
                path.unlink()
                removed += 1
            self._sync_dir(self.dataset,'recovery.pointers')
        return removed
