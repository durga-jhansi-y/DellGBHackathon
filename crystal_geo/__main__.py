"""Run with python3 -m crystal_geo; fixtures are never production-trusted."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import urllib.error

from PIL import Image

from .fixture import GENERATION_ID, WARNING, FixtureError, build_fixture, verify
from .store import GenerationStore


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
    except (FixtureError, OSError, sqlite3.Error, urllib.error.URLError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
