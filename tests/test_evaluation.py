"""Compression evaluation invariants and end-to-end report checks; no timing thresholds."""
from contextlib import closing
import csv
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

from crystal_geo.evaluation import (
    CANDIDATES, CandidateReader, EvaluationError, artifact_inventory, build_candidates,
    decode_control, evaluate, percentile, validate_candidates, workloads,
)
from crystal_geo.evaluation_worker import measure
from crystal_geo.fixture import CONTAINER_PATH, FixtureError, canonical, digest, verify

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO/'testdata/tiny_valid_generation'
SOURCES = REPO/'testdata/source_images'


class EvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.artifacts = Path(cls.temporary.name)/'artifacts'
        cls.catalogue = build_candidates(cls.artifacts,source_dir=SOURCES,generation=FIXTURE)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def mutable_copy(self):
        destination = self.root/'artifacts'
        shutil.copytree(self.artifacts,destination)
        for path in destination.rglob('*'):
            if path.is_file():
                path.chmod(0o644)
        return destination

    def test_all_candidates_have_identical_source_pixels_and_matched_codec_payloads(self):
        validate_candidates(self.artifacts,self.catalogue)
        key = ['before',18,61440,108284]
        with Image.open(SOURCES/'TX003_2016_NAIP.png') as original:
            with original.convert('RGBA') as rgba:
                with rgba.crop((0,0,256,256)) as crop:
                    expected = crop.tobytes()
        payloads = {}
        for name in CANDIDATES:
            with CandidateReader(self.artifacts,name) as reader:
                payloads[name] = reader.read(key)
                profile = 'webp' if name.endswith('webp') else 'raw'
                self.assertEqual(expected,decode_control(payloads[name],profile))
        self.assertEqual(payloads['crystal-webp'],payloads['mbtiles-webp'])
        self.assertEqual(payloads['crystal-raw'],payloads['mbtiles-raw'])
        self.assertEqual(256*256*4,len(payloads['crystal-raw']))

    def test_mbtiles_uses_standard_columns_separate_variants_and_tms_rows(self):
        for profile,format_name in [('webp','webp'),('raw','application/octet-stream')]:
            for variant,date in [('before','2016-09-28'),('after','2017-08-30')]:
                with closing(sqlite3.connect(self.artifacts/f'mbtiles-{profile}/{variant}.mbtiles')) as db:
                    self.assertEqual(['zoom_level','tile_column','tile_row','tile_data'],
                                     [row[1] for row in db.execute('PRAGMA table_info(tiles)')])
                    metadata = dict(db.execute('SELECT name,value FROM metadata'))
                    self.assertEqual(format_name,metadata['format'])
                    self.assertEqual(date,metadata['crystal:acquisition_date'])
                    self.assertEqual('CC-BY-NC-4.0',metadata['crystal:dataset_card_license'])
                    payload = db.execute('SELECT tile_data FROM tiles WHERE zoom_level=18 AND tile_column=61440 AND tile_row=153859').fetchone()[0]
                with CandidateReader(self.artifacts,f'crystal-{profile}') as reference:
                    self.assertEqual(reference.read([variant,18,61440,108284]),payload)

    def test_raw_control_is_rejected_by_production_fixture_verifier(self):
        with self.assertRaises(FixtureError):
            verify(self.artifacts/'crystal-raw')

    def test_candidate_build_is_byte_reproducible(self):
        again = self.root/'replay'
        catalogue = build_candidates(again,source_dir=SOURCES,generation=FIXTURE)
        self.assertEqual(self.catalogue,catalogue)
        self.assertEqual(artifact_inventory(self.artifacts),artifact_inventory(again))

    def test_payload_corruption_rejected_even_with_refreshed_outer_hash(self):
        root = self.mutable_copy()
        container = root/'crystal-raw'/CONTAINER_PATH
        with container.open('r+b') as stream:
            stream.seek(286)
            stream.write(b'corrupt')
        manifest = json.loads((root/'crystal-raw/manifest.json').read_text())
        manifest['containers'][0]['sha256'] = digest(container.read_bytes())
        (root/'crystal-raw/manifest.json').write_bytes(canonical(manifest))
        with self.assertRaisesRegex(EvaluationError,'stored tile hash mismatch'):
            validate_candidates(root,self.catalogue)

    def test_mbtiles_wrong_capture_date_is_rejected_with_refreshed_file_hash(self):
        root = self.mutable_copy()
        path = root/'mbtiles-webp/before.mbtiles'
        with closing(sqlite3.connect(path)) as db:
            db.execute("UPDATE metadata SET value='2020-01-01' WHERE name='crystal:acquisition_date'")
            db.commit()
        manifest = json.loads((root/'mbtiles-webp/manifest.json').read_text())
        manifest['files'][0].update(size=path.stat().st_size,sha256=digest(path.read_bytes()))
        (root/'mbtiles-webp/manifest.json').write_bytes(canonical(manifest))
        with self.assertRaisesRegex(EvaluationError,'source metadata mismatch'):
            validate_candidates(root,self.catalogue)

    def test_checked_ranges_prevent_out_of_bounds_reads(self):
        root = self.mutable_copy()
        with closing(sqlite3.connect(root/'crystal-raw/index.sqlite')) as db:
            db.execute('UPDATE tiles SET payload_offset=9223372036854775807 WHERE tile_id=1')
            db.commit()
        with CandidateReader(root,'crystal-raw') as reader:
            with self.assertRaisesRegex(EvaluationError,'payload outside container'):
                reader.read(['before',18,61440,108284])

    def test_source_hashes_pass_before_decoding_either_image(self):
        source = self.root/'sources'
        shutil.copytree(SOURCES,source)
        (source/'TX003_2017_NOAA.png').write_bytes(b'bad source')
        with patch('crystal_geo.evaluation.Image.open',side_effect=AssertionError('decoded before hash validation')):
            with self.assertRaisesRegex(FixtureError,'source SHA-256 mismatch'):
                build_candidates(self.root/'bad',source_dir=source,generation=FIXTURE)

    def test_quantiles_and_seeded_workloads(self):
        self.assertEqual(25,percentile([10,20,30,40],0.5))
        self.assertAlmostEqual(38.5,percentile([10,20,30,40],0.95))
        self.assertAlmostEqual(39.7,percentile([10,20,30,40],0.99))
        with self.assertRaises(EvaluationError):
            percentile([],0.5)
        self.assertEqual(workloads(self.catalogue,30,5,4),workloads(self.catalogue,30,5,4))
        self.assertNotEqual(workloads(self.catalogue,30,5,4),workloads(self.catalogue,30,5,5))

    def test_worker_stage_accounting_and_connection_policies(self):
        for policy in ('reopen_each_request','reuse_after_warmup'):
            job = {'artifacts':str(self.artifacts),'candidate':'mbtiles-webp','workload':'single_tile',
                   'connection_policy':policy,'catalogue':self.catalogue,
                   'requests':workloads(self.catalogue,3,1,0)['single_tile'],'warmup':1}
            result = measure(job)
            self.assertEqual(3,len(result['samples']))
            self.assertGreater(result['memory']['peak_rss_bytes'],0)
            self.assertGreaterEqual(result['memory']['peak_rss_bytes'],result['memory']['baseline_peak_rss_bytes'])
            if policy=='reuse_after_warmup':
                self.assertGreater(result['initial_open_ns'],0)
                self.assertTrue(all(s['open_ns']==s['close_ns']==0 for s in result['samples']))
            for sample in result['samples']:
                self.assertEqual(sample['end_to_end_ns'],sum(sample[field] for field in
                                 ('open_ns','lookup_read_ns','verification_ns','decode_ns','close_ns')))

    def test_complete_evaluation_outputs_reports_and_rejects_unsafe_force(self):
        output = self.root/'evaluation'
        report = evaluate(output,source_dir=SOURCES,generation=FIXTURE,iterations=2,warmup=1)
        self.assertTrue(report['reproducibility']['identical'])
        self.assertEqual(16,len(report['measurements']))
        self.assertEqual(32,report['pixel_validation']['exact_rgba_matches'])
        self.assertTrue(report['measurement_scope'].startswith('smoke'))
        self.assertIsNone(report['recognition_accuracy'])
        self.assertNotIn(b'\r',(output/'samples.csv').read_bytes())
        with (output/'samples.csv').open(newline='') as stream:
            self.assertEqual(32,len(list(csv.DictReader(stream))))
        self.assertIn('Reopening resets connections',(output/'report.md').read_text())
        for name,size in report['sizes'].items():
            self.assertEqual(sum(f['size'] for f in report['artifacts'][name]),size['total_bytes'])
        replacement = evaluate(output,source_dir=SOURCES,generation=FIXTURE,iterations=1,warmup=0,force=True)
        self.assertEqual(1,replacement['protocol']['iterations'])
        before = (output/'report.json').read_bytes()
        with self.assertRaisesRegex(EvaluationError,'output exists'):
            evaluate(output,generation=FIXTURE,iterations=1)
        self.assertEqual(before,(output/'report.json').read_bytes())
        (output/'artifacts/notes.txt').write_text('preserve')
        # Unexpected files at the artifact root must also be protected.
        with self.assertRaises(EvaluationError):
            evaluate(output,generation=FIXTURE,iterations=1,force=True)

    def test_unrelated_output_and_symlink_are_preserved(self):
        output = self.root/'unrelated'
        output.mkdir()
        (output/'precious.txt').write_text('keep')
        with self.assertRaises(FixtureError):
            evaluate(output,force=True)
        self.assertEqual('keep',(output/'precious.txt').read_text())
        link = self.root/'link'
        link.symlink_to(output,target_is_directory=True)
        with self.assertRaisesRegex(EvaluationError,'symlink'):
            evaluate(link,force=True)

    def test_invalid_cli_iterations_fail_without_creating_output(self):
        output = self.root/'bad'
        result = subprocess.run([sys.executable,'-m','crystal_geo','evaluate-compression',str(output),
                                 '--iterations','0'],cwd=REPO,capture_output=True,text=True)
        self.assertEqual(1,result.returncode)
        self.assertIn('iterations must be',result.stderr)
        self.assertNotIn('Traceback',result.stderr)
        self.assertFalse(output.exists())


if __name__=='__main__':
    unittest.main()
