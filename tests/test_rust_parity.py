"""Cross-language checks against the existing Python corruption cases.

Set CRYSTAL_RUST_BINARY to the built crystal-core executable to enable these
tests. CI requires that variable; a Python-only checkout can still run its tests.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

from PIL import Image

import test_fixture

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO/'testdata/tiny_valid_generation'
BINARY = os.environ.get('CRYSTAL_RUST_BINARY')


@unittest.skipUnless(BINARY, 'set CRYSTAL_RUST_BINARY to enable Rust/Python parity')
class RustParityTests(unittest.TestCase):
    def run_core(self, *arguments, development=True):
        command = [str(Path(BINARY).resolve()), *map(str,arguments)]
        if development:
            command.append('--allow-unsigned-development')
        return subprocess.run(command,cwd=REPO,capture_output=True,text=True,timeout=30)

    def test_production_policy_rejects_unsigned_fixture(self):
        result = self.run_core('verify',FIXTURE,development=False)
        self.assertEqual(1,result.returncode)
        self.assertIn('UnsignedGenerationRejected',result.stderr)

    def test_all_tile_bytes_metadata_and_decoded_pixels_match_python(self):
        result = self.run_core('inspect',FIXTURE)
        self.assertEqual(0,result.returncode,result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual('tx003-dev-v1',report['generation_id'])
        self.assertEqual(32,len(report['tiles']))
        with closing(sqlite3.connect(FIXTURE/'index.sqlite')) as db:
            with (FIXTURE/'containers/container-000001.bin').open('rb') as container:
                for tile in report['tiles']:
                    descriptor = tile['descriptor']
                    key = descriptor['key']
                    row = db.execute('SELECT t.payload_offset,t.payload_length,t.stored_sha256,'
                                     's.source_image,s.acquisition_date,t.west,t.south,t.east,t.north '
                                     'FROM tiles t JOIN sources s USING(source_id) WHERE '
                                     't.variant=? AND t.zoom=? AND t.tile_x=? AND t.tile_y=?',
                                     (key['variant'],key['zoom'],key['x'],key['y'])).fetchone()
                    self.assertIsNotNone(row)
                    offset,length,sha,source,date,*bounds = row
                    container.seek(offset)
                    payload = container.read(length)
                    self.assertEqual(sha.hex(),tile['stored_sha256'])
                    self.assertEqual(hashlib.sha256(payload).hexdigest(),tile['stored_sha256'])
                    self.assertEqual(source,descriptor['source_image'])
                    self.assertEqual(date,descriptor['acquisition_date'])
                    self.assertEqual(bounds,descriptor['bounds'])
                    self.assertEqual(report['generation_id'],descriptor['generation_id'])
                    with Image.open(REPO/'testdata/source_images'/source) as original:
                        with original.convert('RGBA') as rgba:
                            x,y = key['x']-61440,key['y']-108284
                            with rgba.crop((x*256,y*256,(x+1)*256,(y+1)*256)) as crop:
                                self.assertEqual(hashlib.sha256(crop.tobytes()).hexdigest(),tile['decoded_rgba_sha256'])

    def test_export_returns_exact_stored_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)/'tile.webp'
            result = self.run_core('read-tile',FIXTURE,'before',18,61440,108284,output)
            self.assertEqual(0,result.returncode,result.stderr)
            with closing(sqlite3.connect(FIXTURE/'index.sqlite')) as db:
                offset,length = db.execute('SELECT payload_offset,payload_length FROM tiles WHERE tile_id=1').fetchone()
            with (FIXTURE/'containers/container-000001.bin').open('rb') as container:
                container.seek(offset)
                self.assertEqual(container.read(length),output.read_bytes())
            repeated = self.run_core('read-tile',FIXTURE,'before',18,61440,108284,output)
            self.assertEqual(1,repeated.returncode)

    def test_persistent_protocol_rejects_wrong_generation_and_unknown_fields(self):
        command = [str(Path(BINARY).resolve()),'serve',str(FIXTURE),'--allow-unsigned-development']
        with subprocess.Popen(command,cwd=REPO,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,text=True) as process:
            hello = json.loads(process.stdout.readline())
            self.assertEqual(digest_manifest(),hello['manifest_sha256'])
            request = {'request_id':1,'generation_id':'wrong-generation','variant':'before','zoom':18,'x':61440,'y':108284}
            process.stdin.write(json.dumps(request)+'\n')
            process.stdin.flush()
            reply = json.loads(process.stdout.readline())
            self.assertFalse(reply['ok'])
            self.assertEqual('GenerationChanged',reply['error']['code'])
            request['unexpected'] = True
            process.stdin.write(json.dumps(request)+'\n')
            process.stdin.flush()
            self.assertEqual('ProtocolInvalid',json.loads(process.stdout.readline())['error']['code'])
            process.stdin.close()
            self.assertEqual(0,process.wait(timeout=10))

    def test_persistent_protocol_bounds_input_lines(self):
        command = [str(Path(BINARY).resolve()),'serve',str(FIXTURE),'--allow-unsigned-development']
        process = subprocess.run(command,input='x'*4097+'\n',cwd=REPO,capture_output=True,text=True,timeout=30)
        self.assertEqual(1,process.returncode)
        self.assertIn('ProtocolInvalid',process.stderr)

    def test_existing_python_rejection_cases_are_also_rejected_by_rust(self):
        cases = [
            'modified_tile_bytes','modified_index','truncated_container','incorrect_container_size',
            'trailing_container_data','invalid_container_header',
            'generation_mismatch_in_header_with_valid_checksum','offsets_outside_container',
            'overlapping_offsets','generation_mismatch_in_manifest','generation_mismatch_in_index',
            'tile_generation_mismatch','path_traversal','symlink_file_and_directory',
            'schema_tampering','bad_geographic_bounds','bad_spatial_index','unsupported_codec_parameters',
            'unsigned_status_cannot_be_promoted','duplicate_and_oversized_manifest',
        ]
        for name in cases:
            with self.subTest(case=name):
                case = test_fixture.FixtureTests('test_'+name)
                case.setUp()
                original_reject = case.reject
                def reject(message):
                    original_reject(message)
                    result = self.run_core('verify',case.root)
                    self.assertEqual(1,result.returncode,result.stdout+result.stderr)
                    self.assertIn('ERROR:',result.stderr)
                    self.assertNotIn('panicked',result.stderr)
                case.reject = reject
                try:
                    getattr(case,'test_'+name)()
                finally:
                    case.doCleanups()


def digest_manifest():
    return hashlib.sha256((FIXTURE/'manifest.json').read_bytes()).hexdigest()
