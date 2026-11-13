"""Offline installation, pinned reads, corruption and real process-exit recovery."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import errno
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
    CONTAINER_PATH, GENERATION_ID, HEADER_SIZE, build_fixture, canonical, digest,
)
from crystal_geo.store import GenerationStore, StoreError

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO/'testdata/tiny_valid_generation'
B_ID = 'tx003-dev-v2'
CRASH_SCRIPT = '''
import os,sys
from crystal_geo.store import GenerationStore
def checkpoint(event):
    if event == sys.argv[3]:
        os._exit(73)
GenerationStore(sys.argv[1],allow_unsigned_development=True,checkpoint=checkpoint).install(sys.argv[2])
'''


class StoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixture_temporary.cleanup)
        cls.fixture_b = Path(cls.fixture_temporary.name)/'generation-b'
        build_fixture(cls.fixture_b, generation_id=B_ID, source_dir=REPO/'testdata/source_images')

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)/'store'
        self.store = GenerationStore(self.root,allow_unsigned_development=True)

    def read(self, lease, variant='before'):
        return lease.read_tile(generation_id=lease.generation_id, variant=variant,
                               zoom=18,x=61440,y=108284)

    def assert_source_pixels(self, tile):
        with Image.open(REPO/'testdata/source_images'/tile.source_image) as original:
            with original.convert('RGBA') as rgba:
                with rgba.crop((0,0,256,256)) as crop:
                    self.assertEqual(crop.tobytes(),tile.rgba)

    def mutable_copy(self, fixture=FIXTURE):
        target = Path(self.temporary.name)/'modified'
        shutil.copytree(fixture,target)
        for path in target.rglob('*'):
            if path.is_file():
                path.chmod(0o644)
        return target

    def test_unsigned_rejected_without_any_store_writes(self):
        production = GenerationStore(self.root)
        with self.assertRaisesRegex(StoreError,'UnsignedGenerationRejected'):
            production.install(FIXTURE)
        self.assertFalse(self.root.exists())
        self.store.install(FIXTURE)
        for method in [production.active_info,production.open_active,production.recover]:
            with self.assertRaisesRegex(StoreError,'UnsignedGenerationRejected'):
                method()

    def test_install_restart_and_exact_pixels_for_both_variants(self):
        record = self.store.install(FIXTURE)
        restarted = GenerationStore(self.root,allow_unsigned_development=True)
        self.assertEqual(record,restarted.active_info())
        with restarted.open_active() as lease:
            for variant,date in [('before','2016-09-28'),('after','2017-08-30')]:
                tile = self.read(lease,variant)
                self.assertEqual(GENERATION_ID,tile.generation_id)
                self.assertEqual(date,tile.acquisition_date)
                self.assert_source_pixels(tile)
        self.assertTrue(lease.closed)
        for path in (self.store.generations/GENERATION_ID).rglob('*'):
            if path.is_file():
                self.assertEqual(0o444,path.stat().st_mode & 0o777)

    def test_update_retains_pinned_reader_and_old_generation(self):
        self.store.install(FIXTURE)
        with self.store.open_active() as old:
            with ThreadPoolExecutor(max_workers=2) as executor:
                update = executor.submit(self.store.install,self.fixture_b)
                old_reads = [self.read(old,'after') for _ in range(5)]
                self.assertEqual(B_ID,update.result()['generation_id'])
            for tile in old_reads:
                self.assertEqual(GENERATION_ID,tile.generation_id)
                self.assert_source_pixels(tile)
            self.assertEqual(GENERATION_ID,self.read(old).generation_id)
            with self.store.open_active() as new:
                self.assertEqual(B_ID,new.generation_id)
                self.assert_source_pixels(self.read(new))
                with self.assertRaisesRegex(StoreError,'GenerationChanged'):
                    new.read_tile(generation_id=GENERATION_ID,variant='before',zoom=18,x=61440,y=108284)
        self.assertTrue((self.store.generations/GENERATION_ID/'READY').is_file())
        self.assertTrue((self.store.generations/B_ID/'READY').is_file())

    def test_generation_pinned_across_process_activation(self):
        self.store.install(FIXTURE)
        with self.store.open_active() as lease:
            result = subprocess.run([sys.executable,'-m','crystal_geo','install',str(self.root),
                                     str(self.fixture_b),'--allow-unsigned-development'],
                                    cwd=REPO,capture_output=True,text=True)
            self.assertEqual(0,result.returncode,result.stderr)
            self.assertEqual(GENERATION_ID,self.read(lease).generation_id)
            self.assertEqual(B_ID,self.store.active_info()['generation_id'])

    def test_invalid_package_never_replaces_active(self):
        original = self.store.install(FIXTURE)
        broken = self.mutable_copy(self.fixture_b)
        path = broken/CONTAINER_PATH
        with path.open('r+b') as stream:
            stream.seek(HEADER_SIZE+30)
            stream.write(b'corrupt tile')
        with self.assertRaises(StoreError):
            self.store.install(broken)
        self.assertEqual(original,self.store.active_info())
        with self.store.open_active() as lease:
            self.assert_source_pixels(self.read(lease))
        self.assertGreater(self.store.recover(),0)

    def test_mixed_generation_artifacts_rejected(self):
        self.store.install(FIXTURE)
        mixed = self.mutable_copy(self.fixture_b)
        shutil.copyfile(FIXTURE/CONTAINER_PATH,mixed/CONTAINER_PATH)
        manifest = json.loads((mixed/'manifest.json').read_text())
        manifest['containers'][0]['sha256'] = digest((mixed/CONTAINER_PATH).read_bytes())
        (mixed/'manifest.json').write_bytes(canonical(manifest))
        with self.assertRaisesRegex(StoreError,'generation mismatch'):
            self.store.install(mixed)
        self.assertEqual(GENERATION_ID,self.store.active_info()['generation_id'])

    def test_invalid_payload_range_rejected_with_refreshed_index_hash(self):
        self.store.install(FIXTURE)
        invalid = self.mutable_copy(self.fixture_b)
        with closing(sqlite3.connect(invalid/'index.sqlite')) as db:
            db.execute('UPDATE tiles SET payload_offset=9223372036854775807 WHERE tile_id=1')
            db.commit()
        manifest = json.loads((invalid/'manifest.json').read_text())
        manifest['index']['sha256'] = digest((invalid/'index.sqlite').read_bytes())
        (invalid/'manifest.json').write_bytes(canonical(manifest))
        with self.assertRaisesRegex(StoreError,'payload outside container'):
            self.store.install(invalid)
        self.assertEqual(GENERATION_ID,self.store.active_info()['generation_id'])

    def test_reinstall_is_idempotent_but_generation_collision_is_rejected(self):
        original = self.store.install(FIXTURE)
        self.assertEqual(original,self.store.install(FIXTURE))
        altered = self.mutable_copy()
        manifest = json.loads((altered/'manifest.json').read_text())
        manifest['toolchain']['sqlite'] = 'different-toolchain'
        (altered/'manifest.json').write_bytes(canonical(manifest))
        with self.assertRaisesRegex(StoreError,'GenerationConflict'):
            self.store.install(altered)
        self.assertEqual(original,self.store.active_info())

    def test_missing_tile_and_closed_lease(self):
        self.store.install(FIXTURE)
        lease = self.store.open_active()
        with self.assertRaisesRegex(StoreError,'TileNotFound'):
            lease.read_tile(generation_id=GENERATION_ID,variant='before',zoom=18,x=0,y=0)
        lease.close()
        lease.close()
        with self.assertRaisesRegex(StoreError,'LeaseClosed'):
            self.read(lease)

    def test_post_open_payload_corruption_rejected_before_decode(self):
        self.store.install(FIXTURE)
        with self.store.open_active() as lease:
            path = self.store.generations/GENERATION_ID/'package'/CONTAINER_PATH
            path.chmod(0o644)
            with path.open('r+b') as stream:
                stream.seek(HEADER_SIZE+30)
                stream.write(b'corrupt')
            with patch('crystal_geo.store.decode_payload',side_effect=AssertionError('unsafe decode')):
                with self.assertRaisesRegex(StoreError,'PayloadHashMismatch'):
                    self.read(lease)

    def test_post_open_truncation_rejected(self):
        self.store.install(FIXTURE)
        with self.store.open_active() as lease:
            path = self.store.generations/GENERATION_ID/'package'/CONTAINER_PATH
            path.chmod(0o644)
            with path.open('r+b') as stream:
                stream.truncate(HEADER_SIZE)
            with self.assertRaisesRegex(StoreError,'PayloadOutOfBounds'):
                self.read(lease)

    def test_index_snapshot_is_pinned_and_new_open_rejects_changed_index(self):
        self.store.install(FIXTURE)
        with self.store.open_active() as lease:
            path = self.store.generations/GENERATION_ID/'package/index.sqlite'
            path.chmod(0o644)
            with closing(sqlite3.connect(path)) as db:
                db.execute('UPDATE tiles SET payload_offset=9223372036854775807 WHERE tile_id=1')
                db.commit()
            self.assert_source_pixels(self.read(lease))
            with self.assertRaisesRegex(StoreError,'file hash mismatch'):
                self.store.open_active()

    def test_pointer_checksum_and_ready_marker_enforced(self):
        self.store.install(FIXTURE)
        pointer = self.store.dataset/'active'
        data = json.loads(pointer.read_text())
        data['record']['generation_id'] = B_ID
        pointer.chmod(0o644)
        pointer.write_bytes(canonical(data))
        with self.assertRaisesRegex(StoreError,'checksum mismatch'):
            self.store.open_active()
        self.store.install(FIXTURE)
        marker = self.store.generations/GENERATION_ID/'READY'
        marker.chmod(0o644)
        marker.write_bytes(canonical({'generation_id':GENERATION_ID,'manifest_sha256':'0'*64}))
        with self.assertRaisesRegex(StoreError,'READY marker'):
            self.store.open_active()

    def test_pointer_path_traversal_rejected_even_with_fresh_checksum(self):
        self.store.install(FIXTURE)
        pointer = self.store.dataset/'active'
        data = json.loads(pointer.read_text())
        data['record']['generation_id'] = '../escape'
        data['sha256'] = digest(canonical(data['record']))
        pointer.chmod(0o644)
        pointer.write_bytes(canonical(data))
        with self.assertRaisesRegex(StoreError,'unsafe generation identifier'):
            self.store.open_active()

    def test_package_path_traversal_rejected(self):
        self.store.install(FIXTURE)
        package = self.mutable_copy(self.fixture_b)
        manifest = json.loads((package/'manifest.json').read_text())
        manifest['index']['path'] = '../outside.sqlite'
        (package/'manifest.json').write_bytes(canonical(manifest))
        with self.assertRaisesRegex(StoreError,'unsafe path'):
            self.store.install(package)
        self.assertEqual(GENERATION_ID,self.store.active_info()['generation_id'])

    def test_symlink_package_and_store_paths_are_rejected(self):
        package = self.mutable_copy()
        (package/'index.sqlite').unlink()
        (package/'index.sqlite').symlink_to(FIXTURE/'index.sqlite')
        with self.assertRaisesRegex(StoreError,'symlink'):
            self.store.install(package)
        self.store.install(FIXTURE)
        (self.store.dataset/'active').unlink()
        (self.store.dataset/'active').symlink_to('/tmp/outside-pointer')
        with self.assertRaisesRegex(StoreError,'symlink'):
            self.store.open_active()
        with self.assertRaisesRegex(StoreError,'symlink'):
            self.store.install(FIXTURE)

    def test_storage_full_during_copy_preserves_active(self):
        original = self.store.install(FIXTURE)
        with patch.object(self.store,'_copy',side_effect=OSError(errno.ENOSPC,'disk full')):
            with self.assertRaisesRegex(StoreError,'StorageFull'):
                self.store.install(self.fixture_b)
        self.assertEqual(original,self.store.active_info())
        with self.store.open_active() as lease:
            self.assert_source_pixels(self.read(lease))

    def test_recovery_never_selects_an_orphan_generation(self):
        self.store.install(FIXTURE)
        def stop(event):
            if event == 'generation.published':
                raise OSError('interrupted before pointer')
        interrupted = GenerationStore(self.root,allow_unsigned_development=True,checkpoint=stop)
        with self.assertRaises(StoreError):
            interrupted.install(self.fixture_b)
        self.assertTrue((self.store.generations/B_ID/'READY').exists())
        self.store.recover()
        self.assertEqual(GENERATION_ID,self.store.active_info()['generation_id'])
        self.assertEqual(B_ID,self.store.install(self.fixture_b)['generation_id'])

    def test_sync_failure_after_pointer_replace_reports_uncertain_activation(self):
        self.store.install(FIXTURE)
        def fail(event):
            if event == 'active.replaced':
                raise OSError(errno.EIO,'directory sync failed')
        failing = GenerationStore(self.root,allow_unsigned_development=True,checkpoint=fail)
        with self.assertRaisesRegex(StoreError,'ActivationUncertain'):
            failing.install(self.fixture_b)
        self.assertEqual(B_ID,self.store.active_info()['generation_id'])
        self.assertTrue((self.store.generations/GENERATION_ID/'READY').is_file())
        with self.store.open_active() as lease:
            self.assert_source_pixels(self.read(lease))

    def test_two_writers_serialize_without_replacing_generation_files(self):
        self.store.install(FIXTURE)
        self._concurrent_installs()

    def test_two_first_install_writers_serialize(self):
        self._concurrent_installs()

    def _concurrent_installs(self):
        def install():
            result = subprocess.run([sys.executable,'-m','crystal_geo','install',str(self.root),
                                     str(self.fixture_b),'--allow-unsigned-development'],
                                    cwd=REPO,capture_output=True,text=True)
            self.assertEqual(0,result.returncode,result.stderr)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(install) for _ in range(2)]
            for future in futures:
                future.result()
        self.assertEqual(B_ID,self.store.active_info()['generation_id'])
        self.assertEqual([],list(self.store.staging.iterdir()))

    def test_process_exit_at_every_update_boundary(self):
        self._crash_matrix(initial=FIXTURE)

    def test_process_exit_at_every_first_install_boundary(self):
        self._crash_matrix(initial=None)

    def _crash_matrix(self, initial):
        trace_root = Path(self.temporary.name)/'trace'
        trace = GenerationStore(trace_root,allow_unsigned_development=True)
        if initial:
            trace.install(initial)
        events = []
        trace._checkpoint = events.append
        trace.install(self.fixture_b)
        self.assertEqual(len(events),len(set(events)), 'fault checkpoints must be unique')
        self.assertIn('active.replaced',events)
        for i,event in enumerate(events):
            with self.subTest(event=event, initial=bool(initial)):
                root = Path(self.temporary.name)/f'crash-{i}'
                store = GenerationStore(root,allow_unsigned_development=True)
                if initial:
                    store.install(initial)
                process = subprocess.run([sys.executable,'-c',CRASH_SCRIPT,str(root),str(self.fixture_b),event],
                                         cwd=REPO,capture_output=True,text=True,timeout=30)
                self.assertEqual(73,process.returncode,process.stderr)
                restarted = GenerationStore(root,allow_unsigned_development=True)
                # Cleanup finishes any missing layout but never promotes staged/orphan data.
                restarted.recover()
                active = restarted.active_info()
                expected = B_ID if i >= events.index('active.replaced') else (GENERATION_ID if initial else None)
                self.assertEqual(expected,active['generation_id'] if active else None)
                if active:
                    with restarted.open_active() as lease:
                        self.assertEqual(expected,self.read(lease).generation_id)
                        self.assert_source_pixels(self.read(lease))
                else:
                    with self.assertRaisesRegex(StoreError,'GenerationUnavailable'):
                        restarted.open_active()
                # Retry also exercises reuse of an already-published orphan generation.
                self.assertEqual(B_ID,restarted.install(self.fixture_b)['generation_id'])
                with restarted.open_active() as lease:
                    self.assert_source_pixels(self.read(lease))

    def test_cli_opt_in_and_export_without_overwriting(self):
        command = [sys.executable,'-m','crystal_geo']
        denied = subprocess.run([*command,'install',str(self.root),str(FIXTURE)],
                                cwd=REPO,capture_output=True,text=True)
        self.assertEqual(1,denied.returncode)
        self.assertIn('UnsignedGenerationRejected',denied.stderr)
        self.assertNotIn('Traceback',denied.stderr)
        self.store.install(FIXTURE)
        output = Path(self.temporary.name)/'tile.png'
        export = [*command,'read-tile',str(self.root),'--variant','after','--zoom','18',
                  '--x','61440','--y','108284','--output',str(output),'--allow-unsigned-development']
        success = subprocess.run(export,cwd=REPO,capture_output=True,text=True)
        self.assertEqual(0,success.returncode,success.stderr)
        before = output.read_bytes()
        repeated = subprocess.run(export,cwd=REPO,capture_output=True,text=True)
        self.assertEqual(1,repeated.returncode)
        self.assertEqual(before,output.read_bytes())
        with Image.open(output) as image:
            self.assertEqual((256,256),image.size)

    def test_generation_identifiers_cannot_traverse_paths(self):
        for generation_id in ['../escape','/tmp/escape','a/b','','a'*65]:
            with self.subTest(generation_id=generation_id):
                with self.assertRaisesRegex(ValueError,'unsafe generation identifier'):
                    build_fixture(Path(self.temporary.name)/'bad',generation_id=generation_id)


if __name__ == '__main__':
    unittest.main()
