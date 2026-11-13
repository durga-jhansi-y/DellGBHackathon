"""Run with python3 -m crystal_geo; fixtures are never production-trusted."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import urllib.error

from PIL import Image

from .fixture import GENERATION_ID, WARNING, FixtureError, build_fixture, verify
from .store import GenerationStore
from .findings import FindingsStore
from .recognition import LocalVLMBackend, RecognitionQueue
from .recognition_types import RecognitionRequest, RecognitionError
from .rust_reader import RustTileReader


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build-fixture', help='build unsigned TX003 demonstration data')
    build.add_argument('output')
    build.add_argument('--force', action='store_true', help='replace an existing valid fixture')
    build.add_argument('--source-dir', help='use locally cached PNGs; hashes are still mandatory')
    build.add_argument('--generation-id', default=GENERATION_ID, help='unique development generation identifier')
    check = commands.add_parser('verify', help='verify a development fixture (does not authenticate its publisher)')
    check.add_argument('generation')
    evaluate = commands.add_parser('evaluate-compression',help='build and compare raw/WebP Crystal and MBTiles development controls')
    evaluate.add_argument('output')
    evaluate.add_argument('--source-dir',help='local TX003 source PNG directory; hashes are mandatory')
    evaluate.add_argument('--generation',help='reuse a verified WebP generation instead of encoding a fresh one')
    evaluate.add_argument('--iterations',type=int,default=30)
    evaluate.add_argument('--warmup',type=int,default=5)
    evaluate.add_argument('--seed',type=int,default=0)
    evaluate.add_argument('--force',action='store_true',help='replace a recognized, unchanged evaluation output')
    recognize = commands.add_parser('recognize',help='run one experimental, generation-pinned local VLM job')
    recognize.add_argument('store')
    recognize.add_argument('findings')
    recognize.add_argument('--region',type=float,nargs=4,required=True,metavar=('WEST','SOUTH','EAST','NORTH'))
    recognize.add_argument('--variant',choices=['before','after','both'],default='both')
    recognize.add_argument('--zoom',type=int,default=18)
    recognize.add_argument('--model-id',default='Qwen/Qwen3-VL-8B-Instruct')
    recognize.add_argument('--model-version',required=True,help='explicit deployment/checkpoint label; not cryptographically verified')
    recognize.add_argument('--question',default=RecognitionRequest.__dataclass_fields__['question'].default)
    recognize.add_argument('--max-tokens',type=int,default=160)
    recognize.add_argument('--base-url',default='http://127.0.0.1:8000/v1')
    recognize.add_argument('--timeout',type=float,default=30)
    recognize.add_argument('--rust-binary')
    recognize.add_argument('--allow-unsigned-development',action='store_true')
    finding_status = commands.add_parser('recognize-status',help='read a persisted experimental job record')
    finding_status.add_argument('findings')
    finding_status.add_argument('request_id')
    for name, help_text in [('install','verify, install and activate a package'),
                            ('status','show the active development generation'),
                            ('recover','remove interrupted temporary work; retain all published generations'),
                            ('read-tile','export a verified tile from a pinned active generation')]:
        command = commands.add_parser(name, help=help_text)
        command.add_argument('store')
        command.add_argument('--allow-unsigned-development', action='store_true',
                             help='explicitly permit unsigned demonstration data; NEVER use in production')
        if name == 'install':
            command.add_argument('package')
        if name == 'read-tile':
            command.add_argument('--generation-id', help='reject if the active generation differs')
            command.add_argument('--variant', choices=['before','after'], required=True)
            command.add_argument('--zoom', type=int, required=True)
            command.add_argument('--x', type=int, required=True)
            command.add_argument('--y', type=int, required=True)
            command.add_argument('--output', required=True, help='new PNG path; never overwrite existing files')
    args = parser.parse_args(argv)
    try:
        if args.command == 'build-fixture':
            print(build_fixture(args.output, force=args.force, source_dir=args.source_dir,
                                generation_id=args.generation_id))
        elif args.command == 'verify':
            print(verify(args.generation))
        elif args.command == 'evaluate-compression':
            from .evaluation import evaluate as run_evaluation
            report = run_evaluation(args.output,source_dir=args.source_dir,generation=args.generation,
                                    iterations=args.iterations,warmup=args.warmup,seed=args.seed,force=args.force)
            print(json.dumps({'report':str(Path(args.output)/'report.md'),
                              'measurement_scope':report['measurement_scope'],
                              'reproducible_artifacts':report['reproducibility']['identical'],
                              'webp_to_raw_payload_ratio':report['webp_to_raw_payload_ratio'],
                              'sizes':report['sizes']},sort_keys=True))
            print(WARNING,file=sys.stderr)
        elif args.command == 'recognize-status':
            print(json.dumps(FindingsStore(args.findings).load(args.request_id),sort_keys=True))
        elif args.command == 'recognize':
            store = GenerationStore(args.store,allow_unsigned_development=args.allow_unsigned_development)
            backend = LocalVLMBackend(args.base_url,timeout=args.timeout,api_key=os.environ.get('CRYSTAL_LLM_API_KEY'))
            request = RecognitionRequest(tuple(args.region),args.variant,args.model_id,args.model_version,
                                         args.question,args.zoom,args.max_tokens)
            with RecognitionQueue(store,args.findings,backend,
                                  reader_factory=lambda reference: RustTileReader(reference,binary=args.rust_binary)) as queue:
                request_id = queue.submit(request)
                record = queue.wait(request_id,timeout=args.timeout+30)
            print(json.dumps(record,sort_keys=True))
            print(WARNING,file=sys.stderr)
            if record['status']!='completed':
                return 1
        else:
            store = GenerationStore(args.store, allow_unsigned_development=args.allow_unsigned_development)
            if args.command == 'install':
                print(json.dumps(store.install(args.package), sort_keys=True))
            elif args.command == 'status':
                print(json.dumps(store.active_info(), sort_keys=True))
            elif args.command == 'recover':
                print(f'Removed {store.recover()} temporary entries; published generations retained.')
            else:
                with store.open_active() as lease:
                    tile = lease.read_tile(generation_id=args.generation_id or lease.generation_id,
                                           variant=args.variant, zoom=args.zoom, x=args.x, y=args.y)
                output = Path(args.output)
                # Exclusive creation prevents accidental modification of source/store files.
                with output.open('xb') as stream:
                    try:
                        with Image.frombytes('RGBA',(tile.width,tile.height),tile.rgba) as image:
                            image.save(stream,format='PNG')
                    except BaseException:
                        output.unlink(missing_ok=True)
                        raise
                print(json.dumps({'generation_id':tile.generation_id,'variant':tile.variant,
                                  'source_image':tile.source_image,'acquisition_date':tile.acquisition_date,
                                  'bounds':tile.bounds,'output':str(output)},sort_keys=True))
            print(WARNING, file=sys.stderr)
    except (FixtureError, RecognitionError, OSError, sqlite3.Error, urllib.error.URLError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
