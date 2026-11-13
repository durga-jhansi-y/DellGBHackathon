"""Offline regression tests, including attacks with refreshed outer hashes."""
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from crystal_geo.fixture import (
    CODEC, CONTAINER_PATH, GENERATION_ID, HEADER_SIZE, PROVENANCE,
    FixtureError, HTTPSOnlyRedirect, build_fixture, canonical, download_source,
    source_paths, tile_bounds, verify,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / 'testdata/tiny_valid_generation'
SOURCES = REPO / 'testdata/source_images'


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'generation'
        shutil.copytree(FIXTURE, self.root)
        for path in self.root.rglob('*'):
            if path.is_file():
                path.chmod(0o644)

    def manifest(self):
        return json.loads((self.root/'manifest.json').read_text())

    def save_manifest(self, manifest):
        (self.root/'manifest.json').write_bytes(canonical(manifest))

    def refresh(self, field):
        manifest = self.manifest()
        record = manifest[field] if field == 'index' else manifest[field][0]
        data = (self.root/record['path']).read_bytes()
        record.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
        self.save_manifest(manifest)

    def sql(self, statement, parameters=()):
        with closing(sqlite3.connect(self.root/'index.sqlite')) as db:
            db.execute(statement, parameters)
            db.commit()
        self.refresh('index')

    def reject(self, message):
        with self.assertRaisesRegex(FixtureError, message):
            verify(self.root)

    def test_valid_generation(self):
        self.assertIn('32 lossless WebP tiles', verify(self.root))
        self.assertIn('MUST NEVER BE ACCEPTED IN PRODUCTION', verify(self.root))

    def test_every_tile_preserves_source_pixels(self):
        with closing(sqlite3.connect(self.root/'index.sqlite')) as db:
            with (self.root/CONTAINER_PATH).open('rb') as container:
                for source in PROVENANCE['sources']:
                    with Image.open(SOURCES/Path(source['image_path']).name) as original:
                        with original.convert('RGBA') as pixels:
                            rows = db.execute('SELECT tile_x,tile_y,payload_offset,payload_length '
                                              'FROM tiles WHERE variant=? ORDER BY tile_id',
                                              (source['variant'],)).fetchall()
                            self.assertEqual(16, len(rows))
                            for i, (x,y,offset,length) in enumerate(rows):
                                container.seek(offset)
                                with io.BytesIO(container.read(length)) as encoded:
                                    with Image.open(encoded) as tile:
                                        with tile.convert('RGBA') as decoded:
                                            with pixels.crop((i%4*256,i//4*256,(i%4+1)*256,(i//4+1)*256)) as crop:
                                                self.assertEqual(crop.tobytes(), decoded.tobytes())
                                bounds = tile_bounds(x,y)
                                self.assertLess(bounds[0], bounds[2])
                                self.assertLess(bounds[1], bounds[3])

    def test_reproducible_build_and_force(self):
        output = Path(self.temporary.name)/'rebuilt'
        build_fixture(output, source_dir=SOURCES)
        for relative in ['manifest.json', 'index.sqlite', CONTAINER_PATH]:
            self.assertEqual((FIXTURE/relative).read_bytes(), (output/relative).read_bytes())
        with self.assertRaisesRegex(FixtureError, 'output exists'):
            build_fixture(output, source_dir=SOURCES)
        build_fixture(output, force=True, source_dir=SOURCES)
        self.assertIn('VERIFIED', verify(output))

    def test_modified_tile_bytes(self):
        path = self.root/CONTAINER_PATH
        with path.open('r+b') as stream:
            stream.seek(HEADER_SIZE + 30)
            value = stream.read(1)[0]
            stream.seek(-1, 1)
            stream.write(bytes([value ^ 1]))
        self.reject('file hash mismatch')
        self.refresh('containers')
        with patch('crystal_geo.fixture.Image.open', side_effect=AssertionError('decoded unverified data')):
            self.reject('tile payload SHA-256 mismatch')

    def test_modified_index(self):
        with (self.root/'index.sqlite').open('r+b') as stream:
            stream.seek(100)
            stream.write(b'corrupt')
        self.reject('file hash mismatch')

    def test_truncated_container(self):
        path = self.root/CONTAINER_PATH
        with path.open('r+b') as stream:
            stream.truncate(path.stat().st_size-1)
        self.reject('file size mismatch')
        self.refresh('containers')
        self.reject('payload outside container')

    def test_incorrect_container_size(self):
        manifest = self.manifest()
        manifest['containers'][0]['size'] += 1
        self.save_manifest(manifest)
        self.reject('file size mismatch')

    def test_trailing_container_data(self):
        with (self.root/CONTAINER_PATH).open('ab') as stream:
            stream.write(b'unindexed')
        self.refresh('containers')
        self.reject('unindexed trailing')

    def test_invalid_container_header(self):
        for offset in [0,8,12,16,80,144,224]:
            with self.subTest(offset=offset):
                path = self.root/CONTAINER_PATH
                original = path.read_bytes()
                modified = bytearray(original)
                modified[offset] ^= 1
                path.write_bytes(modified)
                self.refresh('containers')
                self.reject('invalid container header')
                path.write_bytes(original)

    def test_generation_mismatch_in_header_with_valid_checksum(self):
        path = self.root/CONTAINER_PATH
        data = bytearray(path.read_bytes())
        data[16:80] = b'other-generation'.ljust(64,b'\0')
        data[224:256] = hashlib.sha256(data[:224]).digest()
        path.write_bytes(data)
        self.refresh('containers')
        self.reject('generation mismatch')

    def test_offsets_outside_container(self):
        for offset in [2**63-1, (self.root/CONTAINER_PATH).stat().st_size+1]:
            with self.subTest(offset=offset):
                self.sql('UPDATE tiles SET payload_offset=? WHERE tile_id=1', (offset,))
                self.reject('payload outside container')

    def test_overlapping_offsets(self):
        self.sql('UPDATE tiles SET payload_offset=256 WHERE tile_id=2')
        self.reject('overlapping payloads')

    def test_generation_mismatch_in_manifest(self):
        manifest = self.manifest()
        manifest['generation_id'] = 'other-generation'
        self.save_manifest(manifest)
        self.reject('generation_id mismatch')

    def test_generation_mismatch_in_index(self):
        self.sql('UPDATE generation_metadata SET generation_id=?', ('other-generation',))
        self.reject('foreign key failure|generation mismatch')

    def test_tile_generation_mismatch(self):
        self.sql('UPDATE tiles SET generation_id=? WHERE tile_id=1', ('other-generation',))
        self.reject('foreign key failure')

    def test_path_traversal(self):
        for field in ['index','containers']:
            for path in ['../outside.sqlite','/tmp/outside','containers/../../outside',
                         'containers\\outside', './index.sqlite', 'containers//file', 'a\x00b']:
                with self.subTest(field=field, path=path):
                    manifest = json.loads((FIXTURE/'manifest.json').read_text())
                    record = manifest[field] if field == 'index' else manifest[field][0]
                    record['path'] = path
                    self.save_manifest(manifest)
                    self.reject('unsafe path')

    def test_symlink_file_and_directory(self):
        path = self.root/'index.sqlite'
        path.unlink()
        path.symlink_to(FIXTURE/'index.sqlite')
        self.reject('symlink')
        path.unlink()
        shutil.copyfile(FIXTURE/'index.sqlite',path)
        shutil.rmtree(self.root/'containers')
        (self.root/'containers').symlink_to(FIXTURE/'containers',target_is_directory=True)
        self.reject('symlink')

    def test_schema_is_strict(self):
        self.assertEqual((REPO/'schemas/index.sql').read_bytes(), (REPO/'crystal_geo/index.sql').read_bytes())
        with closing(sqlite3.connect(self.root/'index.sqlite')) as db:
            for statement in ['UPDATE tiles SET payload_offset=-1 WHERE tile_id=1',
                              "UPDATE tiles SET payload_offset='oops' WHERE tile_id=1",
                              'UPDATE tiles SET payload_length=0 WHERE tile_id=1']:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)

    def test_schema_tampering(self):
        self.sql('CREATE TABLE unexpected (value TEXT)')
        self.reject('index schema mismatch')

    def test_bad_geographic_bounds(self):
        self.sql('UPDATE tiles SET west=west+0.00001 WHERE tile_id=1')
        self.reject('tile bounds mismatch')

    def test_bad_spatial_index(self):
        self.sql('DELETE FROM tile_bounds WHERE tile_id=1')
        self.reject('R-tree count mismatch')

    def test_unsupported_codec_parameters(self):
        self.sql("UPDATE tiles SET codec_parameters='{}' WHERE tile_id=1")
        self.reject('unsupported tile profile')

    def test_unsigned_status_cannot_be_promoted(self):
        manifest = self.manifest()
        manifest['production_acceptable'] = True
        self.save_manifest(manifest)
        self.reject('production_acceptable mismatch')

    def test_duplicate_and_oversized_manifest(self):
        (self.root/'manifest.json').write_text('{"a":1,"a":2}')
        self.reject('duplicate JSON key')
        (self.root/'manifest.json').write_bytes(b' '*65537)
        self.reject('manifest exceeds size limit')

    def test_bad_source_hash_before_decode(self):
        folder = Path(self.temporary.name)/'sources'
        shutil.copytree(SOURCES,folder)
        (folder/'TX003_2016_NAIP.png').write_bytes(b'bad PNG')
        with patch('crystal_geo.fixture.Image.open',side_effect=AssertionError('decoded unverified source')):
            with self.assertRaisesRegex(FixtureError, 'source SHA-256 mismatch'):
                source_paths(folder)

    def test_failed_download_does_not_publish_cache(self):
        destination = Path(self.temporary.name)/'source.png'
        response = io.BytesIO(b'wrong bytes')
        response.url = 'https://example.com/image.png'
        with patch('crystal_geo.fixture.urllib.request.OpenerDirector.open', return_value=response):
            with self.assertRaisesRegex(FixtureError, 'source SHA-256 mismatch'):
                download_source(PROVENANCE['sources'][0],destination)
        self.assertFalse(destination.exists())
        self.assertEqual([],list(destination.parent.glob('.download-*')))

    def test_https_downgrade_is_rejected(self):
        with self.assertRaisesRegex(FixtureError,'insecure redirect'):
            HTTPSOnlyRedirect().redirect_request(None,None,302,'',{},'http://example.com/a')

    def test_failed_download_preserves_existing_cache(self):
        destination = Path(self.temporary.name)/'source.png'
        destination.write_bytes(b'previous cache')
        with patch('crystal_geo.fixture.urllib.request.OpenerDirector.open',side_effect=OSError('network failed')):
            with self.assertRaisesRegex(OSError,'network failed'):
                download_source(PROVENANCE['sources'][0],destination)
        self.assertEqual(b'previous cache',destination.read_bytes())
        self.assertEqual([],list(destination.parent.glob('.download-*')))

    def test_tls_validation_is_enabled(self):
        import ssl
        import urllib.request
        destination = Path(self.temporary.name)/'source.png'
        with patch('crystal_geo.fixture.urllib.request.build_opener') as factory:
            factory.return_value.open.side_effect = OSError('stop before network')
            with self.assertRaises(OSError):
                download_source(PROVENANCE['sources'][0],destination)
        handler = next(h for h in factory.call_args.args if isinstance(h, urllib.request.HTTPSHandler))
        self.assertTrue(handler._context.check_hostname)
        self.assertEqual(ssl.CERT_REQUIRED, handler._context.verify_mode)

    def test_force_preserves_unrelated_directory(self):
        output = Path(self.temporary.name)/'unrelated'
        output.mkdir()
        (output/'precious.txt').write_text('preserve')
        with self.assertRaises(FixtureError):
            build_fixture(output,force=True,source_dir=SOURCES)
        self.assertEqual('preserve',(output/'precious.txt').read_text())

    def test_cli_failure_has_nonzero_exit(self):
        (self.root/'manifest.json').write_text('invalid JSON')
        result = subprocess.run([sys.executable,'-m','crystal_geo','verify',str(self.root)],
                                cwd=REPO,capture_output=True,text=True)
        self.assertEqual(1,result.returncode)
        self.assertIn('ERROR:',result.stderr)
        self.assertNotIn('Traceback',result.stderr)


if __name__ == '__main__':
    unittest.main()
