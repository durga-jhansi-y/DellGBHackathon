"""Run with python3 -m crystal_geo; fixtures are never production-trusted."""
import argparse
import sqlite3
import sys
import urllib.error

from .fixture import FixtureError, build_fixture, verify


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build-fixture', help='build unsigned TX003 demonstration data')
    build.add_argument('output')
    build.add_argument('--force', action='store_true', help='replace an existing valid fixture')
    build.add_argument('--source-dir', help='use locally cached PNGs; hashes are still mandatory')
    check = commands.add_parser('verify', help='verify a development fixture (does not authenticate its publisher)')
    check.add_argument('generation')
    args = parser.parse_args(argv)
    try:
        if args.command == 'build-fixture':
            print(build_fixture(args.output, force=args.force, source_dir=args.source_dir))
        else:
            print(verify(args.generation))
    except (FixtureError, OSError, sqlite3.Error, urllib.error.URLError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
