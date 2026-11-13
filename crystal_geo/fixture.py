"""A deliberately narrow, bounded TX003 fixture format, not a production reader."""
from __future__ import annotations

import hashlib
from contextlib import closing
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import ssl
import struct
import tempfile
import urllib.parse
import urllib.request

import PIL
from PIL import Image, features

ROOT = Path(__file__).resolve().parent
PROVENANCE = json.loads((ROOT / 'tx003.json').read_text())
SCHEMA = (ROOT / 'index.sql').read_text()
DATASET_ID = '6971d9bc-6c17-5edc-bfc4-725e35642141'
GENERATION_ID = 'tx003-dev-v1'
CONTAINER_ID = 'container-000001'
CONTAINER_PATH = 'containers/container-000001.bin'
COVERAGE = [-95.625, 29.84064389983442, -95.6195068359375, 29.84540862642843]
ZOOM, START_X, START_Y = 18, 61440, 108284
HEADER_SIZE = 256
MAX_CONTAINER = 16 * 1024 * 1024
MAX_INDEX = 1024 * 1024
MAX_SOURCE = 8 * 1024 * 1024
CODEC = {'profile': 'analysis-webp-lossless-v1', 'lossless': True,
         'quality': 100, 'method': 6, 'exact': True, 'pixel_format': 'RGBA'}
WARNING = 'UNSIGNED DEVELOPMENT ONLY; MUST NEVER BE ACCEPTED IN PRODUCTION'


class FixtureError(ValueError):
    """Invalid or unsupported fixture; no unverified pixels are returned."""


def require(condition, message):
    if not condition:
        raise FixtureError(message)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path, limit):
    require(path.stat().st_size <= limit, f'file exceeds size limit: {path.name}')
    result = hashlib.sha256()
    total = 0
    with path.open('rb') as stream:
        while block := stream.read(65536):
            total += len(block)
            require(total <= limit, 'file exceeds size limit')
            result.update(block)
    return result.hexdigest()


def safe_file(root, relative):
    require(isinstance(relative, str) and len(relative) <= 128, 'invalid path')
    parts = PurePosixPath(relative).parts
    require(parts and not relative.startswith('/') and '\\' not in relative
            and all(re.fullmatch(r'[A-Za-z0-9_.-]+', p) and p not in ('.', '..') for p in parts)
            and '/'.join(parts) == relative, 'unsafe path')
    current = root
    require(root.is_dir() and not root.is_symlink(), 'unsafe generation directory')
    for part in parts:
        current = current / part
        require(not current.is_symlink(), 'symlink paths are forbidden')
    require(current.is_file(), f'missing regular file: {relative}')
    return current


def tile_bounds(x, y):
    def lon(t):
        return t / (1 << ZOOM) * 360 - 180
    def lat(t):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * t / (1 << ZOOM)))))
    return (lon(x), lat(y + 1), lon(x + 1), lat(y))


def header():
    # 8-byte magic, big-endian version and size, fixed-width NUL-padded IDs,
    # 80 reserved zero bytes, then SHA-256 over the preceding 224 bytes.
    prefix = struct.pack('>8sII64s64s80s', b'CGEOdev1', 1, HEADER_SIZE,
                         GENERATION_ID.encode(), CONTAINER_ID.encode(), b'')
    return prefix + hashlib.sha256(prefix).digest()


class HTTPSOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require(urllib.parse.urlsplit(newurl).scheme == 'https', 'insecure redirect rejected')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_source(source, destination):
    """Use normal CA/hostname validation; publish cached data only after hashing."""
    require(urllib.parse.urlsplit(source['download_url']).scheme == 'https', 'HTTPS required')
    opener = urllib.request.build_opener(
        HTTPSOnlyRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    destination.parent.mkdir(parents=True, exist_ok=True)
    require(not destination.is_symlink(), 'symlink source cache rejected')
    fd, temporary = tempfile.mkstemp(prefix='.download-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            with opener.open(source['download_url'], timeout=60) as response:
                require(urllib.parse.urlsplit(response.url).scheme == 'https', 'HTTPS required')
                total = 0
                while block := response.read(65536):
                    total += len(block)
                    require(total <= MAX_SOURCE, 'source download exceeds size limit')
                    out.write(block)
            out.flush()
            os.fsync(out.fileno())
        require(file_hash(Path(temporary), MAX_SOURCE) == source['sha256'], 'source SHA-256 mismatch')
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def source_paths(source_dir=None):
    folder = Path(source_dir) if source_dir else Path('testdata/source_images')
    result = []
    for source in PROVENANCE['sources']:
        path = folder / Path(source['image_path']).name
        if source_dir is None and not path.exists():
            download_source(source, path)
        require(path.is_file() and not path.is_symlink(), 'missing or unsafe source image')
        require(file_hash(path, MAX_SOURCE) == source['sha256'], 'source SHA-256 mismatch')
        result.append(path)
    # Both hashes are checked before either image is decoded.
    return result


def manifest_base():
    return {'format': 'crystal-geo-unsigned-dev-v1', 'format_version': 1,
            'dataset_id': DATASET_ID, 'generation_id': GENERATION_ID,
            'signature_status': 'unsigned-development', 'production_acceptable': False,
            'warning': WARNING, 'purpose': 'development and noncommercial demonstration',
            'coverage': {'crs': 'EPSG:4326', 'bounds': COVERAGE, 'grid_crs': 'EPSG:3857'},
            'tile_count': 32, 'tile_size': 256, 'zoom': ZOOM,
            'codec_parameters': CODEC, 'provenance': PROVENANCE}


def schema_signature(db):
    return db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name').fetchall()


def build_fixture(output, *, force=False, source_dir=None):
    output = Path(output).absolute()
    require(not output.is_symlink(), 'symlink output rejected')
    # Never recursively delete arbitrary data through --force. Only replace a valid fixture.
    if output.exists():
        require(force, 'output exists; use --force to replace a verified fixture')
        verify(output)
    images = source_paths(source_dir)
    require(features.check('webp'), 'Pillow has no WebP support')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.crystal-build-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'generation'
        stage.mkdir()
        (stage / 'containers').mkdir()
        container = stage / CONTAINER_PATH
        db = sqlite3.connect(stage / 'index.sqlite')
        try:
            db.executescript(SCHEMA)
            db.execute('INSERT INTO generation_metadata VALUES (?,?,?,?,?,?)',
                       (DATASET_ID, GENERATION_ID, 1, 'EPSG:4326', 'EPSG:3857', ZOOM))
            with container.open('xb') as stream:
                stream.write(header())
                tile_id = 0
                for source, path in zip(PROVENANCE['sources'], images):
                    db.execute('INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)',
                               (source['image_id'], source['variant'], source['acquisition_date'],
                                Path(source['image_path']).name, bytes.fromhex(source['sha256']),
                                source['source_agency'], source['attribution'], source['reuse']))
                    with Image.open(path) as original:
                        require(original.format == 'PNG' and original.size == (1024, 1024),
                                'expected a 1024x1024 PNG')
                        with original.convert('RGBA') as image:
                            for y in range(4):
                                for x in range(4):
                                    tile_id += 1
                                    with image.crop((x*256, y*256, (x+1)*256, (y+1)*256)) as tile:
                                        with io.BytesIO() as encoded:
                                            tile.save(encoded, format='WEBP', lossless=True, quality=100,
                                                      method=6, exact=True)
                                            payload = encoded.getvalue()
                                    offset = stream.tell()
                                    require(offset + len(payload) <= MAX_CONTAINER, 'container limit exceeded')
                                    stream.write(payload)
                                    bounds = tile_bounds(START_X + x, START_Y + y)
                                    row = (tile_id, GENERATION_ID, source['image_id'], source['variant'],
                                           ZOOM, START_X+x, START_Y+y, *bounds, 256, 256, CONTAINER_ID,
                                           offset, len(payload), 'webp', canonical(CODEC).decode().strip(),
                                           hashlib.sha256(payload).digest())
                                    db.execute('INSERT INTO tiles VALUES (' + ','.join('?' for _ in row) + ')', row)
                                    west, south, east, north = bounds
                                    db.execute('INSERT INTO tile_bounds VALUES (?,?,?,?,?)',
                                               (tile_id, west, east, south, north))
                stream.flush()
                os.fsync(stream.fileno())
            db.commit()
        finally:
            db.close()
        manifest = manifest_base()
        manifest['toolchain'] = {'pillow': PIL.__version__, 'webp': features.version('webp'),
                                 'sqlite': sqlite3.sqlite_version}
        manifest['index'] = {'path': 'index.sqlite', 'size': (stage/'index.sqlite').stat().st_size,
                             'sha256': file_hash(stage/'index.sqlite', MAX_INDEX)}
        manifest['containers'] = [{'id': CONTAINER_ID, 'path': CONTAINER_PATH,
                                  'size': container.stat().st_size,
                                  'sha256': file_hash(container, MAX_CONTAINER)}]
        (stage/'manifest.json').write_bytes(canonical(manifest))
        result = verify(stage)
        for path in (container, stage/'index.sqlite', stage/'manifest.json'):
            path.chmod(0o444)
        # Two directory renames, with restoration on ordinary failure. No active-pointer
        # or crash-durable activation claim is made by this development CLI.
        backup = Path(temporary) / 'previous'
        if output.exists():
            output.rename(backup)
        try:
            stage.rename(output)
        except OSError:
            if backup.exists():
                backup.rename(output)
            raise
    return result


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def _verify(root):
    root = Path(root).absolute()
    manifest_path = safe_file(root, 'manifest.json')
    with manifest_path.open('rb') as stream:
        raw = stream.read(65537)
    require(len(raw) <= 65536, 'manifest exceeds size limit')
    manifest = json.loads(raw, object_pairs_hook=_no_duplicate_keys,
                          parse_constant=lambda _: (_ for _ in ()).throw(FixtureError('non-finite JSON')))
    require(isinstance(manifest, dict), 'manifest must be an object')
    require(raw == canonical(manifest), 'manifest is not canonical development JSON')
    base = manifest_base()
    require(set(manifest) == set(base) | {'toolchain', 'index', 'containers'}, 'manifest fields invalid')
    for key, expected in base.items():
        require(canonical(manifest[key]) == canonical(expected), f'manifest {key} mismatch')
    tools = manifest['toolchain']
    require(isinstance(tools, dict) and set(tools) == {'pillow', 'webp', 'sqlite'}
            and all(isinstance(v, str) and 0 < len(v) <= 64 for v in tools.values()), 'invalid toolchain')
    containers = manifest['containers']
    require(isinstance(containers, list) and len(containers) == 1, 'expected one container')
    records = [(manifest['index'], 'index.sqlite', MAX_INDEX),
               (containers[0], CONTAINER_PATH, MAX_CONTAINER)]
    paths = []
    for record, expected_path, limit in records:
        require(isinstance(record, dict), 'invalid file record')
        expected_keys = {'path', 'size', 'sha256'} | ({'id'} if expected_path == CONTAINER_PATH else set())
        require(set(record) == expected_keys, 'invalid file record fields')
        path = safe_file(root, record['path'])
        require(record['path'] == expected_path, 'unexpected package path')
        require(type(record['size']) is int and 0 < record['size'] <= limit, 'invalid file size')
        require(path.stat().st_size == record['size'], f'file size mismatch: {expected_path}')
        require(isinstance(record['sha256'], str) and re.fullmatch('[0-9a-f]{64}', record['sha256']),
                'invalid file hash')
        require(file_hash(path, limit) == record['sha256'], f'file hash mismatch: {expected_path}')
        paths.append(path)
    require(containers[0]['id'] == CONTAINER_ID, 'container identifier mismatch')
    require({p.name for p in root.iterdir()} == {'manifest.json', 'index.sqlite', 'containers'}
            and {p.name for p in (root/'containers').iterdir()} == {'container-000001.bin'},
            'unexpected package files')
    index, container = paths
    with container.open('rb') as stream:
        require(stream.read(HEADER_SIZE) == header(), 'invalid container header or generation mismatch')
        db = sqlite3.connect(index.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
        try:
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_INDEX)
            db.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
            budget = 0
            def progress():
                nonlocal budget
                budget += 1
                return int(budget > 20000)
            db.set_progress_handler(progress, 100)
            with closing(sqlite3.connect(':memory:')) as expected_db:
                expected_db.executescript(SCHEMA)
                require(schema_signature(db) == schema_signature(expected_db), 'index schema mismatch')
            require(db.execute('PRAGMA user_version').fetchone() == (1,), 'index version mismatch')
            require(db.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'index integrity failure')
            require(not db.execute('PRAGMA foreign_key_check').fetchall(), 'index foreign key failure')
            require(db.execute('SELECT * FROM generation_metadata').fetchall() ==
                    [(DATASET_ID, GENERATION_ID, 1, 'EPSG:4326', 'EPSG:3857', ZOOM)],
                    'index generation mismatch')
            source_rows = db.execute('SELECT * FROM sources ORDER BY variant DESC').fetchall()
            expected_sources = [(s['image_id'], s['variant'], s['acquisition_date'],
                                 Path(s['image_path']).name, bytes.fromhex(s['sha256']),
                                 s['source_agency'], s['attribution'], s['reuse'])
                                for s in PROVENANCE['sources']]
            require(source_rows == expected_sources, 'index source metadata mismatch')
            rows = db.execute('SELECT * FROM tiles ORDER BY tile_id LIMIT 33').fetchall()
            require(len(rows) == 32, 'expected exactly 32 tiles')
            require(db.execute('SELECT count(*) FROM tile_bounds').fetchone() == (32,), 'R-tree count mismatch')
            previous_end = HEADER_SIZE
            size = containers[0]['size']
            for i, row in enumerate(rows):
                (tile_id, generation, source_id, variant, zoom, x, y, west, south, east, north,
                 width, height, container_id, offset, length, codec, parameters, stored_hash) = row
                source = PROVENANCE['sources'][i // 16]
                tx, ty = START_X + i % 4, START_Y + (i % 16) // 4
                require((tile_id, generation, source_id, variant, zoom, x, y) ==
                        (i+1, GENERATION_ID, source['image_id'], source['variant'], ZOOM, tx, ty),
                        'tile generation or coordinate mismatch')
                require(all(math.isfinite(v) and abs(v-e) <= 1e-12
                            for v, e in zip((west,south,east,north), tile_bounds(tx,ty))), 'tile bounds mismatch')
                spatial = db.execute('SELECT west,east,south,north FROM tile_bounds WHERE tile_id=?',
                                     (tile_id,)).fetchone()
                require(spatial is not None and all(abs(v-e) < 2e-5
                        for v,e in zip(spatial,(west,east,south,north))), 'R-tree bounds mismatch')
                require((width, height, container_id, codec, parameters) ==
                        (256, 256, CONTAINER_ID, 'webp', canonical(CODEC).decode().strip()), 'unsupported tile profile')
                # Python integers cannot wrap; subtraction also makes the u64-porting contract explicit.
                require(type(offset) is int and type(length) is int and HEADER_SIZE <= offset <= size
                        and 0 < length <= min(1048576, size-offset), 'payload outside container')
                require(offset == previous_end, 'overlapping payloads or unindexed container bytes')
                stream.seek(offset)
                payload = stream.read(length)
                require(len(payload) == length, 'truncated tile payload')
                require(hashlib.sha256(payload).digest() == stored_hash, 'tile payload SHA-256 mismatch')
                # Integrity precedes every decode. Dimensions are checked before loading pixels.
                require(payload[:4] == b'RIFF' and payload[8:12] == b'WEBP'
                        and payload[12:16] == b'VP8L', 'expected lossless WebP')
                with io.BytesIO(payload) as encoded:
                    with Image.open(encoded) as image:
                        require(image.format == 'WEBP' and image.size == (256,256), 'decode dimensions exceeded')
                        image.load()
                previous_end = offset + length
            require(previous_end == size, 'unindexed trailing container bytes')
        finally:
            db.close()
    return f'VERIFIED: {GENERATION_ID}; 32 lossless WebP tiles (16 before, 16 after); {WARNING}'


def verify(root):
    try:
        return _verify(root)
    except FixtureError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, IndexError,
            OverflowError, RecursionError, Image.DecompressionBombError) as exc:
        raise FixtureError(f'invalid fixture: {exc}') from exc
