"""Generation-pinned recognition, scheduling, persistence, and local adapter checks."""
from contextlib import closing
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import jsonschema

from crystal_geo.findings import FindingsStore
from crystal_geo.fixture import CONTAINER_PATH, GENERATION_ID, canonical, digest, header, verify
from crystal_geo.recognition import LocalVLMBackend, RecognitionQueue
from crystal_geo.recognition_types import ModelResult, RecognitionError, RecognitionRequest, VerifiedImage
from crystal_geo.rust_reader import RustTileReader
from crystal_geo.store import GenerationStore

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO/'testdata/tiny_valid_generation'
REGION = (-95.625,29.844217466091493,-95.62362670898438,29.84540862642843)
REQUEST = RecognitionRequest(REGION,'both','fixture-model','test-deployment-v1')


class Backend:
    def __init__(self,blocking=False):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = []
        if not blocking:
            self.release.set()

    def infer(self,request,images,cancellation):
        self.calls.append(images)
        self.started.set()
        if not self.release.wait(30):
            raise RecognitionError('ModelTimeout','test backend was not released')
        return ModelResult('Experimental visible change; uncertain.',request.model_id)


@unittest.skipUnless(os.environ.get('CRYSTAL_RUST_BINARY'),'set CRYSTAL_RUST_BINARY to test recognition over real Rust reads')
class RecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.fixture_b = Path(cls.temporary.name)/'generation-b'
        shutil.copytree(FIXTURE,cls.fixture_b)
        for path in cls.fixture_b.rglob('*'):
            if path.is_file():
                path.chmod(0o644)
        with closing(sqlite3.connect(cls.fixture_b/'index.sqlite')) as db:
            db.execute("UPDATE generation_metadata SET generation_id='tx003-dev-v2'")
            db.execute("UPDATE tiles SET generation_id='tx003-dev-v2'")
            db.commit()
        with (cls.fixture_b/CONTAINER_PATH).open('r+b') as stream:
            stream.write(header('tx003-dev-v2'))
        manifest = json.loads((cls.fixture_b/'manifest.json').read_text())
        manifest['generation_id'] = 'tx003-dev-v2'
        manifest['index']['sha256'] = digest((cls.fixture_b/'index.sqlite').read_bytes())
        manifest['containers'][0]['sha256'] = digest((cls.fixture_b/CONTAINER_PATH).read_bytes())
        (cls.fixture_b/'manifest.json').write_bytes(canonical(manifest))
        verify(cls.fixture_b)
        cls.schema = json.loads((REPO/'schemas/findings.schema.json').read_text())
        jsonschema.Draft202012Validator.check_schema(cls.schema)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = GenerationStore(self.root/'store',allow_unsigned_development=True)
        self.store.install(FIXTURE)
        self.results = self.root/'findings'

    def queue(self,backend=None,**kwargs):
        backend = backend or Backend()
        queue = RecognitionQueue(self.store,self.results,backend,**kwargs)
        self.addCleanup(queue.close)
        self.addCleanup(backend.release.set)
        return queue,backend

    def assert_schema(self,record):
        jsonschema.Draft202012Validator(self.schema,format_checker=jsonschema.FormatChecker()).validate(record)

    def test_completed_finding_retains_inputs_model_settings_and_uncertainty(self):
        queue,backend = self.queue()
        request_id = queue.submit(REQUEST)
        record = queue.wait(request_id,15)
        self.assertEqual('completed',record['status'])
        self.assertEqual(record,queue.findings.load(request_id))
        self.assert_schema(record)
        self.assertEqual(GENERATION_ID,record['imagery']['generation_id'])
        self.assertEqual(['2016-09-28','2017-08-30'],[t['acquisition_date'] for t in record['imagery']['tiles']])
        self.assertEqual('test-deployment-v1',record['request']['model_version'])
        self.assertFalse(record['processing']['model_version_verified'])
        self.assertIsNone(record['result']['uncertainty']['confidence'])
        self.assertEqual('unreviewed',record['result']['review_status'])
        self.assertEqual(2,len(backend.calls[0]))
        for image,provenance in zip(backend.calls[0],record['imagery']['tiles']):
            self.assertEqual(digest(image.payload),provenance['stored_sha256'])

    def test_activation_during_recognition_preserves_original_generation_and_map_access(self):
        queue,backend = self.queue(Backend(blocking=True))
        first = queue.submit(REQUEST)
        self.assertTrue(backend.started.wait(10))
        self.store.install(self.fixture_b)
        with self.store.open_active() as map_reader:
            tile = map_reader.read_tile(generation_id=map_reader.generation_id,variant='after',zoom=18,x=61440,y=108284)
            self.assertEqual('tx003-dev-v2',tile.generation_id)
        second = queue.submit(REQUEST)
        backend.release.set()
        old,new = queue.wait(first,15),queue.wait(second,15)
        self.assertEqual(GENERATION_ID,old['imagery']['generation_id'])
        self.assertEqual('tx003-dev-v2',new['imagery']['generation_id'])
        self.assertEqual({GENERATION_ID},{image.generation_id for image in backend.calls[0]})
        self.assertEqual({'tx003-dev-v2'},{image.generation_id for image in backend.calls[1]})

    def test_queue_is_bounded_and_queued_cancel_never_calls_model(self):
        queue,backend = self.queue(Backend(blocking=True),capacity=1)
        first = queue.submit(REQUEST)
        self.assertTrue(backend.started.wait(10))
        second = queue.submit(REQUEST)
        reader = queue._jobs[second].reader
        with self.assertRaisesRegex(RecognitionError,'QueueFull'):
            queue.submit(REQUEST)
        self.assertTrue(queue.cancel(second))
        self.assertTrue(reader.closed)
        self.assertEqual('cancelled',queue.wait(second,1)['status'])
        backend.release.set()
        self.assertEqual('completed',queue.wait(first,15)['status'])
        self.assertEqual(1,len(backend.calls))

    def test_running_cancel_discards_model_result(self):
        queue,backend = self.queue(Backend(blocking=True))
        request_id = queue.submit(REQUEST)
        self.assertTrue(backend.started.wait(10))
        queue.cancel(request_id)
        backend.release.set()
        record = queue.wait(request_id,10)
        self.assertEqual('cancelled',record['status'])
        self.assertIsNone(record['result'])
        self.assert_schema(record)

    def test_cancelled_history_is_bounded_and_remains_available_on_disk(self):
        queue,backend = self.queue(Backend(blocking=True),capacity=1,history_limit=2)
        first = queue.submit(REQUEST)
        self.assertTrue(backend.started.wait(10))
        cancelled = []
        for _ in range(3):
            request_id = queue.submit(REQUEST)
            queue.cancel(request_id)
            cancelled.append(request_id)
        self.assertLessEqual(len(queue._jobs),2)
        self.assertEqual('cancelled',queue.get(cancelled[0])['status'])
        backend.release.set()
        queue.wait(first,15)

    def test_corrupt_input_is_rejected_before_any_inference(self):
        queue,backend = self.queue()
        path = self.store.generations/GENERATION_ID/'package'/CONTAINER_PATH
        path.chmod(0o644)
        with path.open('r+b') as stream:
            stream.seek(286)
            stream.write(b'corruption')
        with self.assertRaises(RecognitionError):
            queue.submit(REQUEST)
        self.assertEqual([],backend.calls)

    def test_corruption_after_pin_fails_queued_job_before_its_inference(self):
        queue,backend = self.queue(Backend(blocking=True))
        first = queue.submit(REQUEST)
        self.assertTrue(backend.started.wait(10))
        second = queue.submit(REQUEST)
        path = self.store.generations/GENERATION_ID/'package'/CONTAINER_PATH
        path.chmod(0o644)
        with closing(sqlite3.connect(self.store.generations/GENERATION_ID/'package/index.sqlite')) as db:
            offset = db.execute("SELECT payload_offset FROM tiles WHERE variant='after' AND tile_x=61440 AND tile_y=108284").fetchone()[0]
        with path.open('r+b') as stream:
            stream.seek(offset+30)
            stream.write(b'corruption')
        backend.release.set()
        self.assertEqual('completed',queue.wait(first,15)['status'])
        record = queue.wait(second,15)
        self.assertEqual('failed',record['status'])
        self.assertEqual('PayloadHashMismatch',record['error']['code'])
        self.assertEqual(1,len(backend.calls))
        self.assertIsNone(record['result'])

    def test_region_and_resolution_limits_fail_without_inference(self):
        queue,backend = self.queue()
        for request,code in [(replace(REQUEST,zoom=19),'ResolutionUnsupported'),
                             (replace(REQUEST,region=(-96,29,-95,30)),'RegionUnavailable'),
                             (replace(REQUEST,region=(-95.625,29.84064389983442,-95.6195068359375,29.84540862642843)),'RegionTooLarge')]:
            with self.subTest(code=code):
                with self.assertRaisesRegex(RecognitionError,code):
                    queue.submit(request)
        self.assertEqual([],backend.calls)

    def test_restart_marks_incomplete_job_interrupted_without_replay(self):
        queue,backend = self.queue()
        request_id = queue.submit(REQUEST)
        record = queue.wait(request_id,15)
        queue.close()
        record.update(status='running',result=None,finished_at=None)
        FindingsStore(self.results).save(record)
        restarted,_ = self.queue()
        recovered = restarted.get(request_id)
        self.assertEqual('interrupted',recovered['status'])
        self.assertEqual('ProcessInterrupted',recovered['error']['code'])
        self.assert_schema(recovered)

    def test_single_scheduler_ownership_and_safe_request_paths(self):
        queue,_ = self.queue()
        with self.assertRaisesRegex(RecognitionError,'QueueInUse'):
            RecognitionQueue(self.store,self.results,Backend())
        with self.assertRaisesRegex(RecognitionError,'RequestInvalid'):
            queue.get('../outside')

    def test_unsigned_policy_is_enforced_and_closed_queue_rejects_new_jobs(self):
        backend = Backend()
        production = GenerationStore(self.store.root)
        with RecognitionQueue(production,self.results,backend) as queue:
            with self.assertRaisesRegex(ValueError,'UnsignedGenerationRejected'):
                queue.submit(REQUEST)
            self.assertEqual([],backend.calls)
        with self.assertRaisesRegex(RecognitionError,'QueueClosed'):
            queue.submit(REQUEST)

    def test_model_failure_and_wrong_model_are_not_completed_findings(self):
        queue,backend = self.queue()
        with patch.object(backend,'infer',side_effect=RecognitionError('ModelUnavailable','model stopped')):
            record = queue.wait(queue.submit(REQUEST),15)
        self.assertEqual('failed',record['status'])
        self.assertEqual('ModelUnavailable',record['error']['code'])
        with patch.object(backend,'infer',return_value=ModelResult('text','other-model')):
            record = queue.wait(queue.submit(REQUEST),15)
        self.assertEqual('failed',record['status'])
        self.assertEqual('ModelOutputInvalid',record['error']['code'])
        self.assertIsNone(record['result'])

    def test_findings_write_failure_stops_inference_and_is_reported(self):
        queue,backend = self.queue(Backend(blocking=True))
        first = queue.submit(REQUEST)
        self.assertTrue(backend.started.wait(10))
        second = queue.submit(REQUEST)
        with patch.object(queue.findings,'save',side_effect=OSError('storage unavailable')):
            backend.release.set()
            record = queue.wait(second,15)
            queue.wait(first,15)
        self.assertEqual('failed',record['status'])
        self.assertEqual('FindingsWriteFailed',record['error']['code'])
        self.assertEqual(1,len(backend.calls))
        with self.assertRaisesRegex(RecognitionError,'FindingsWriteFailed'):
            queue.submit(REQUEST)

    def test_bridge_timeout_cleans_up_child_process(self):
        executable = self.root/'slow-reader'
        executable.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n')
        executable.chmod(0o700)
        with self.assertRaisesRegex(RecognitionError,'ReaderTimeout'):
            RustTileReader(self.store.active_package_reference(),binary=executable,timeout=0.05)


class LocalAdapterTests(unittest.TestCase):
    def test_endpoint_cannot_send_imagery_to_remote_host(self):
        for endpoint in ['http://example.com/v1','http://user:password@localhost/v1','file:///tmp/server']:
            with self.assertRaisesRegex(RecognitionError,'ModelEndpointInvalid'):
                LocalVLMBackend(endpoint)

    def test_model_adapter_sends_exact_verified_webp_and_capture_labels(self):
        image = VerifiedImage('generation','before',18,1,2,(0,0,1,1),'source.png','2016-09-28',digest(b'image'),b'image')
        response = io.BytesIO(json.dumps({'model':REQUEST.model_id,'choices':[
            {'finish_reason':'stop','message':{'content':'Uncertain visible change.'}}]}).encode())
        with patch('crystal_geo.recognition.urllib.request.OpenerDirector.open',return_value=response) as send:
            result = LocalVLMBackend().infer(REQUEST,(image,),threading.Event())
        body = json.loads(send.call_args.args[0].data)
        self.assertEqual('Uncertain visible change.',result.text)
        self.assertEqual(REQUEST.max_tokens,body['max_tokens'])
        self.assertIn('2016-09-28',body['messages'][1]['content'][1]['text'])
        self.assertEqual('data:image/webp;base64,aW1hZ2U=',body['messages'][1]['content'][2]['image_url']['url'])

    def test_truncated_model_output_is_rejected(self):
        response = io.BytesIO(json.dumps({'model':REQUEST.model_id,'choices':[
            {'finish_reason':'length','message':{'content':'Partial response'}}]}).encode())
        with patch('crystal_geo.recognition.urllib.request.OpenerDirector.open',return_value=response):
            with self.assertRaisesRegex(RecognitionError,'ModelOutputIncomplete'):
                LocalVLMBackend().infer(REQUEST,(),threading.Event())

    def test_request_validation_rejects_nonfinite_bounds_and_missing_version(self):
        for request in [replace(REQUEST,region=(0,float('nan'),1,2)),replace(REQUEST,model_version='')]:
            with self.assertRaises(RecognitionError):
                request.validate()

    def test_malformed_saved_findings_return_a_typed_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            request_id = 'a'*32
            Path(temporary,request_id+'.json').write_text('invalid JSON')
            with self.assertRaisesRegex(RecognitionError,'FindingsInvalid'):
                FindingsStore(temporary).load(request_id)
            Path(temporary,request_id+'.json').write_bytes(canonical({
                'request_id':request_id,'schema_version':1,'status':[]}))
            with self.assertRaisesRegex(RecognitionError,'FindingsInvalid'):
                FindingsStore(temporary).load(request_id)

    def test_thread_start_failure_releases_scheduler_ownership(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = GenerationStore(Path(temporary)/'store',allow_unsigned_development=True)
            findings = Path(temporary)/'findings'
            with patch('crystal_geo.recognition.threading.Thread.start',side_effect=RuntimeError('cannot start thread')):
                with self.assertRaisesRegex(RuntimeError,'cannot start thread'):
                    RecognitionQueue(store,findings,Backend())
            with RecognitionQueue(store,findings,Backend()):
                pass

    def test_oversized_model_response_is_rejected(self):
        response = io.BytesIO(b' '*65537)
        with patch('crystal_geo.recognition.urllib.request.OpenerDirector.open',return_value=response):
            with self.assertRaisesRegex(RecognitionError,'ModelOutputInvalid'):
                LocalVLMBackend().infer(REQUEST,(),threading.Event())
