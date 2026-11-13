"""Reproducible TX003 compression candidates and measured Python storage controls.

Raw RGBA and MBTiles companions are evaluation artifacts, not installable Crystal
generations. Production readers keep their existing strict lossless-WebP policy.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import csv
import hashlib
import html
import json
import math
import os
from pathlib import Path
import platform
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

import PIL
from PIL import Image, features

from .fixture import (
    CODEC, CONTAINER_ID, CONTAINER_PATH, COVERAGE, DATASET_ID, HEADER_SIZE,
    MAX_CONTAINER, MAX_INDEX, PROVENANCE, SCHEMA, START_X, START_Y, WARNING,
    FixtureError, _no_duplicate_keys, build_fixture, canonical, decode_payload,
    digest, file_hash, header, manifest_base, safe_file, schema_signature,
    source_paths, verified_manifest,
)

FORMAT = 'crystal-compression-evaluation-v1'
RAW_GENERATION = 'tx003-eval-raw-v1'
RAW_CODEC = {'profile':'raw-test-v1','pixel_format':'RGBA','width':256,'height':256}
require_schema = "CHECK (codec = 'webp')"
if SCHEMA.count(require_schema) != 1:
    raise RuntimeError('evaluation raw schema requires one known codec constraint')
RAW_SCHEMA = SCHEMA.replace(require_schema,"CHECK (codec = 'raw-rgba')")
CANDIDATES = ('crystal-webp','crystal-raw','mbtiles-webp','mbtiles-raw')
MAX_FILE = 16*1024*1024
TOP_FILES = {'artifacts','report.json','report.md','samples.csv','protocol.json'}


class EvaluationError(FixtureError):
    pass


def check(condition, message):
    if not condition:
        raise EvaluationError(message)


def load_json(path, limit=65536):
    check(path.is_file() and not path.is_symlink(),'missing or unsafe JSON file')
    with path.open('rb') as stream:
        raw = stream.read(limit+1)
    check(len(raw)<=limit,'evaluation JSON exceeds limit')
    try:
        value = json.loads(raw,object_pairs_hook=_no_duplicate_keys)
        check(raw==canonical(value),'noncanonical evaluation JSON')
        return value
    except (ValueError,TypeError,RecursionError) as exc:
        raise EvaluationError('invalid evaluation JSON') from exc


def readonly_db(path, cache_kib=2048):
    db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('PRAGMA mmap_size=0')
        db.execute(f'PRAGMA cache_size=-{int(cache_kib)}')
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,2*1024*1024)
        return db
    except BaseException:
        db.close()
        raise


def _source_pixels(source_dir):
    paths = source_paths(source_dir)
    pixels = {}
    for source,path in zip(PROVENANCE['sources'],paths):
        with Image.open(path) as original:
            check(original.format=='PNG' and original.size==(1024,1024),'expected a 1024x1024 PNG')
            with original.convert('RGBA') as rgba:
                for y in range(4):
                    for x in range(4):
                        with rgba.crop((x*256,y*256,(x+1)*256,(y+1)*256)) as tile:
                            pixels[(source['variant'],18,START_X+x,START_Y+y)] = tile.tobytes()
    return pixels


def _rows(root):
    with closing(readonly_db(safe_file(root,'index.sqlite'))) as db:
        rows = db.execute('SELECT * FROM tiles ORDER BY tile_id LIMIT 33').fetchall()
    check(len(rows)==32,'expected 32 tiles')
    return rows


def _finish_manifest(root, manifest):
    manifest['index'] = {'path':'index.sqlite','size':(root/'index.sqlite').stat().st_size,
                         'sha256':file_hash(root/'index.sqlite',MAX_INDEX)}
    manifest['containers'] = [{'id':CONTAINER_ID,'path':CONTAINER_PATH,
                              'size':(root/CONTAINER_PATH).stat().st_size,
                              'sha256':file_hash(root/CONTAINER_PATH,MAX_CONTAINER)}]
    (root/'manifest.json').write_bytes(canonical(manifest))


def _build_raw(root, webp, pixels):
    (root/'containers').mkdir(parents=True)
    rows = _rows(webp)
    with closing(sqlite3.connect(root/'index.sqlite')) as db:
        db.executescript(RAW_SCHEMA)
        db.execute('INSERT INTO generation_metadata VALUES (?,?,?,?,?,?)',
                   (DATASET_ID,RAW_GENERATION,1,'EPSG:4326','EPSG:3857',18))
        with closing(readonly_db(webp/'index.sqlite')) as source:
            for row in source.execute('SELECT * FROM sources ORDER BY variant DESC'):
                db.execute('INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)',row)
        with (root/CONTAINER_PATH).open('xb') as stream:
            stream.write(header(RAW_GENERATION))
            for original in rows:
                row = list(original)
                key = tuple(row[3:7])
                payload = pixels[key]
                row[1],row[14],row[15] = RAW_GENERATION,stream.tell(),len(payload)
                row[16],row[17],row[18] = 'raw-rgba',canonical(RAW_CODEC).decode().strip(),hashlib.sha256(payload).digest()
                stream.write(payload)
                db.execute('INSERT INTO tiles VALUES ('+','.join('?' for _ in row)+')',row)
                db.execute('INSERT INTO tile_bounds VALUES (?,?,?,?,?)',(row[0],row[7],row[9],row[8],row[10]))
            stream.flush()
            os.fsync(stream.fileno())
        db.commit()
    manifest = manifest_base(RAW_GENERATION)
    manifest.update(format='crystal-geo-evaluation-raw-v1',codec_parameters=RAW_CODEC,
                    toolchain=load_json(webp/'manifest.json')['toolchain'])
    _finish_manifest(root,manifest)


def _build_mbtiles(root, source, profile):
    root.mkdir()
    rows = _rows(source)
    manifest = load_json(source/'manifest.json')
    files = []
    with (source/CONTAINER_PATH).open('rb') as container:
        for source_meta in PROVENANCE['sources']:
            variant = source_meta['variant']
            path = root/(variant+'.mbtiles')
            with closing(sqlite3.connect(path)) as db:
                db.execute('PRAGMA page_size=4096')
                db.executescript('''
                    CREATE TABLE metadata (name TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE tiles (zoom_level INTEGER NOT NULL, tile_column INTEGER NOT NULL,
                                        tile_row INTEGER NOT NULL, tile_data BLOB NOT NULL);
                    CREATE UNIQUE INDEX tile_index ON tiles (zoom_level,tile_column,tile_row);
                ''')
                metadata = {
                    'name':f'TX003 {variant} {profile} development control',
                    'format':'webp' if profile=='webp' else 'application/octet-stream',
                    'bounds':','.join(str(v) for v in COVERAGE),'minzoom':'18','maxzoom':'18',
                    'type':'baselayer','version':'1','attribution':html.escape(source_meta['attribution']),
                    'description':'Unsigned noncommercial development evaluation; raw RGBA requires a custom decoder.'
                                  if profile=='raw' else 'Unsigned noncommercial development evaluation.',
                    'crystal:dataset_id':DATASET_ID,'crystal:generation_id':manifest['generation_id'],
                    'crystal:source_image':Path(source_meta['image_path']).name,
                    'crystal:acquisition_date':source_meta['acquisition_date'],
                    'crystal:source_sha256':source_meta['sha256'],'crystal:reuse':source_meta['reuse'],
                    'crystal:dataset_card_license':PROVENANCE['dataset_card_license'],
                    'crystal:pixel_format':'RGBA',
                }
                db.executemany('INSERT INTO metadata VALUES (?,?)',sorted(metadata.items()))
                for row in rows:
                    if row[3]!=variant:
                        continue
                    container.seek(row[14])
                    payload = container.read(row[15])
                    check(len(payload)==row[15] and hashlib.sha256(payload).digest()==row[18],'source payload mismatch')
                    db.execute('INSERT INTO tiles VALUES (?,?,?,?)',(row[4],row[5],(1<<row[4])-1-row[6],payload))
                db.commit()
                db.execute('VACUUM')
            files.append({'path':path.name,'size':path.stat().st_size,'sha256':file_hash(path,MAX_FILE)})
    sidecar = {'format':'crystal-evaluation-mbtiles-v1','generation_id':manifest['generation_id'],
               'signature_status':'unsigned-development','production_acceptable':False,'warning':WARNING,
               'coverage':manifest['coverage'],'provenance':PROVENANCE,
               'codec_parameters':manifest['codec_parameters'],'files':files,
               'tile_hashes':[{'key':list(row[3:7]),'stored_sha256':row[18].hex()} for row in rows]}
    (root/'manifest.json').write_bytes(canonical(sidecar))


def build_candidates(root, *, source_dir=None, generation=None):
    """Build all four controls; hashes are checked before source PNG decoding."""
    pixels = _source_pixels(source_dir)
    root.mkdir()
    webp = root/'crystal-webp'
    if generation is None:
        build_fixture(webp,source_dir=source_dir)
    else:
        generation = Path(generation).absolute()
        verified_manifest(generation)
        webp.mkdir()
        (webp/'containers').mkdir()
        for relative in ('manifest.json','index.sqlite',CONTAINER_PATH):
            shutil.copyfile(safe_file(generation,relative),webp/relative)
    verified_manifest(webp)
    _build_raw(root/'crystal-raw',webp,pixels)
    _build_mbtiles(root/'mbtiles-webp',webp,'webp')
    _build_mbtiles(root/'mbtiles-raw',root/'crystal-raw','raw')
    catalogue = []
    raw_rows = _rows(root/'crystal-raw')
    for row,raw in zip(_rows(webp),raw_rows):
        key = tuple(row[3:7])
        catalogue.append({'key':list(key),'rgba_sha256':digest(pixels[key]),
                          'webp_sha256':row[18].hex(),'raw_sha256':raw[18].hex(),
                          'bounds':list(row[7:11]),'source_id':row[2]})
    validate_candidates(root,catalogue)
    for path in root.rglob('*'):
        if path.is_file():
            path.chmod(0o444)
    return catalogue


class CandidateReader:
    """Benchmark-only reader. Validate generated candidates before constructing."""
    def __init__(self, root, name):
        check(name in CANDIDATES,'unknown candidate')
        self.root,self.name = Path(root)/name,name
        self.databases = {}
        self.container = None
        try:
            if name.startswith('crystal-'):
                self.databases['index'] = readonly_db(safe_file(self.root,'index.sqlite'))
                self.container = safe_file(self.root,CONTAINER_PATH).open('rb')
                self.size = os.fstat(self.container.fileno()).st_size
                check(self.size<=MAX_CONTAINER,'container exceeds size limit')
            else:
                for variant in ('before','after'):
                    self.databases[variant] = readonly_db(safe_file(self.root,variant+'.mbtiles'),1024)
        except BaseException:
            self.close()
            raise

    def read(self,key):
        variant,zoom,x,y = key
        check(variant in ('before','after') and zoom==18 and START_X<=x<START_X+4
              and START_Y<=y<START_Y+4,'invalid evaluation tile key')
        if self.container:
            row = self.databases['index'].execute(
                'SELECT payload_offset,payload_length FROM tiles WHERE variant=? AND zoom=? AND tile_x=? AND tile_y=?',key).fetchone()
            check(row is not None,'missing tile')
            offset,length = row
            check(type(offset) is int and type(length) is int and HEADER_SIZE<=offset<=self.size
                  and 0<length<=min(1024*1024,self.size-offset),'payload outside container')
            payload = os.pread(self.container.fileno(),length,offset)
            check(len(payload)==length,'short payload read')
            return payload
        row = self.databases[variant].execute(
            'SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?',
            (zoom,x,(1<<zoom)-1-y)).fetchone()
        check(row is not None and isinstance(row[0],bytes) and 0<len(row[0])<=1024*1024,'missing or oversized MBTiles payload')
        return row[0]

    def close(self):
        if self.container:
            self.container.close()
            self.container = None
        for db in self.databases.values():
            db.close()
        self.databases.clear()

    def __enter__(self):
        return self

    def __exit__(self,*args):
        self.close()


def decode_control(payload, profile):
    if profile=='raw':
        check(len(payload)==256*256*4,'invalid raw RGBA byte count')
        return payload
    check(profile=='webp','unknown evaluation codec')
    return decode_payload(payload)


def _validate_files(root, manifest):
    records = ([manifest['index'],*manifest['containers']] if 'index' in manifest else manifest['files'])
    for record in records:
        path = safe_file(root,record['path'])
        check(type(record['size']) is int and 0<record['size']<=MAX_FILE
              and path.stat().st_size==record['size'],'artifact size mismatch')
        check(file_hash(path,MAX_FILE)==record['sha256'],'artifact file hash mismatch')


def validate_candidates(root, catalogue):
    check(len(catalogue)==32 and len({tuple(row['key']) for row in catalogue})==32,'incomplete tile catalogue')
    verified_manifest(root/'crystal-webp')
    for name in CANDIDATES:
        location = root/name
        manifest = load_json(safe_file(location,'manifest.json'))
        _validate_files(location,manifest)
        if name=='crystal-raw':
            expected = manifest_base(RAW_GENERATION)
            expected.update(format='crystal-geo-evaluation-raw-v1',codec_parameters=RAW_CODEC)
            check(all(manifest.get(key)==value for key,value in expected.items()),'raw manifest mismatch')
            with closing(readonly_db(location/'index.sqlite')) as db, closing(sqlite3.connect(':memory:')) as expected_db:
                expected_db.executescript(RAW_SCHEMA)
                check(schema_signature(db)==schema_signature(expected_db),'raw schema mismatch')
                check(db.execute('PRAGMA integrity_check').fetchall()==[('ok',)],'raw index integrity failure')
                check(not db.execute('PRAGMA foreign_key_check').fetchall(),'raw foreign key failure')
                check(db.execute('SELECT * FROM generation_metadata').fetchall()==[
                    (DATASET_ID,RAW_GENERATION,1,'EPSG:4326','EPSG:3857',18)],'raw generation metadata mismatch')
                with closing(readonly_db(root/'crystal-webp/index.sqlite')) as reference:
                    check(db.execute('SELECT * FROM sources ORDER BY source_id').fetchall()==
                          reference.execute('SELECT * FROM sources ORDER BY source_id').fetchall(),
                          'raw source metadata mismatch')
                    check(db.execute('SELECT * FROM tile_bounds ORDER BY tile_id').fetchall()==
                          reference.execute('SELECT * FROM tile_bounds ORDER BY tile_id').fetchall(),
                          'raw spatial index mismatch')
            with (location/CONTAINER_PATH).open('rb') as stream:
                check(stream.read(HEADER_SIZE)==header(RAW_GENERATION),'raw container header mismatch')
            previous = HEADER_SIZE
            webp_rows,raw_rows = _rows(root/'crystal-webp'),_rows(location)
            for webp,raw in zip(webp_rows,raw_rows):
                expected_row = list(webp)
                expected_row[1],expected_row[14],expected_row[15] = RAW_GENERATION,previous,256*256*4
                expected_row[16],expected_row[17],expected_row[18] = 'raw-rgba',canonical(RAW_CODEC).decode().strip(),raw[18]
                check(raw==tuple(expected_row),'raw index tile metadata mismatch')
                previous += 256*256*4
            check(previous==(location/CONTAINER_PATH).stat().st_size,'raw payload coverage mismatch')
        elif name.startswith('mbtiles-'):
            profile = 'webp' if name.endswith('webp') else 'raw'
            check(manifest.get('format')=='crystal-evaluation-mbtiles-v1'
                  and manifest.get('production_acceptable') is False
                  and manifest.get('provenance')==PROVENANCE
                  and manifest.get('codec_parameters')==(CODEC if profile=='webp' else RAW_CODEC)
                  and manifest.get('tile_hashes')==[{'key':item['key'],'stored_sha256':item[profile+'_sha256']}
                                                   for item in catalogue], 'MBTiles sidecar mismatch')
            for variant in ('before','after'):
                with closing(readonly_db(location/(variant+'.mbtiles'))) as db:
                    check(db.execute('PRAGMA integrity_check').fetchall()==[('ok',)],'MBTiles integrity failure')
                    check(db.execute('SELECT count(*) FROM tiles').fetchone()==(16,),'MBTiles tile count mismatch')
                    metadata = dict(db.execute('SELECT name,value FROM metadata'))
                    check(metadata.get('format')==('webp' if name.endswith('webp') else 'application/octet-stream'),
                          'MBTiles codec metadata mismatch')
                    check(metadata.get('bounds')==','.join(str(v) for v in COVERAGE),'MBTiles coverage mismatch')
                    source = next(item for item in PROVENANCE['sources'] if item['variant']==variant)
                    check(metadata.get('crystal:dataset_id')==DATASET_ID
                          and metadata.get('crystal:acquisition_date')==source['acquisition_date']
                          and metadata.get('crystal:source_sha256')==source['sha256'],
                          'MBTiles source metadata mismatch')
        with CandidateReader(root,name) as reader:
            for item in catalogue:
                payload = reader.read(item['key'])
                profile = 'raw' if name.endswith('raw') else 'webp'
                check(digest(payload)==item[profile+'_sha256'],'stored tile hash mismatch')
                check(digest(decode_control(payload,profile))==item['rgba_sha256'],'decoded pixels differ from source')


def artifact_inventory(root):
    check(root.is_dir() and not root.is_symlink() and {p.name for p in root.iterdir()}==set(CANDIDATES),
          'unexpected artifact-root entries')
    inventory = {}
    for name in CANDIDATES:
        location = root/name
        check(location.is_dir() and not location.is_symlink(),'unsafe candidate directory')
        entries = list(location.rglob('*'))
        check(not any(p.is_symlink() for p in entries),'symlink artifact')
        expected = {'manifest.json','index.sqlite',CONTAINER_PATH} if name.startswith('crystal-') else {
            'manifest.json','before.mbtiles','after.mbtiles'}
        check({p.relative_to(location).as_posix() for p in entries if p.is_file()}==expected,
              'unexpected candidate files')
        files = []
        for path in sorted(entries):
            if path.is_file():
                check(not path.is_symlink(),'symlink artifact')
                files.append({'path':path.relative_to(root).as_posix(),'size':path.stat().st_size,
                              'sha256':file_hash(path,MAX_FILE)})
        inventory[name] = files
    return inventory


def workloads(catalogue, iterations, warmup, seed):
    count = iterations+warmup
    rng = random.Random(seed)
    keys = [item['key'] for item in catalogue]
    rng.shuffle(keys)
    singles = [[keys[i%len(keys)]] for i in range(count)]
    viewports = []
    for i in range(count):
        variant = ('before','after')[i%2]
        x,y = START_X+rng.randrange(3),START_Y+rng.randrange(3)
        viewports.append([[variant,18,x+dx,y+dy] for dy in range(2) for dx in range(2)])
    return {'single_tile':singles,'viewport_2x2':viewports}


def percentile(values, fraction):
    check(values and 0<=fraction<=1,'invalid percentile inputs')
    ordered = sorted(values)
    position = (len(ordered)-1)*fraction
    low,high = math.floor(position),math.ceil(position)
    return ordered[low]+(ordered[high]-ordered[low])*(position-low)


def distribution(values):
    return {'count':len(values),'min_ns':min(values),'p50_ns':percentile(values,0.5),
            'p95_ns':percentile(values,0.95),'p99_ns':percentile(values,0.99),
            'max_ns':max(values),'mean_ns':sum(values)/len(values)}


def _markdown(report):
    lines = ['# TX003 compression evaluation','',
             'Development and noncommercial demonstration. Dataset card: CC-BY-NC-4.0.',
             WARNING+'.','',
             f"Scope: {report['measurement_scope']}; {report['protocol']['iterations']} timed iterations after "
             f"{report['protocol']['warmup']} warm-up iterations per candidate/workload/cache policy.",'',
             '| Candidate | Payload bytes | SQLite bytes | Manifest bytes | Total bytes | Exact source pixels |',
             '| --- | ---: | ---: | ---: | ---: | --- |']
    for name in CANDIDATES:
        size = report['sizes'][name]
        lines.append(f"| {name} | {size['payload_bytes']} | {size['sqlite_bytes']} | {size['manifest_bytes']} | {size['total_bytes']} | 32/32 |")
    lines += ['',f"WebP payload bytes / raw RGBA bytes on TX003: {report['webp_to_raw_payload_ratio']:.6f}.",'',
              '| Candidate | Workload | Connection policy | P50 total ms | P95 ms | P99 ms | Peak worker RSS MiB |',
              '| --- | --- | --- | ---: | ---: | ---: | ---: |']
    for result in report['measurements']:
        total = result['latency']['end_to_end_ns']
        lines.append(f"| {result['candidate']} | {result['workload']} | {result['connection_policy']} | "
                     f"{total['p50_ns']/1e6:.4f} | {total['p95_ns']/1e6:.4f} | {total['p99_ns']/1e6:.4f} | "
                     f"{result['memory']['peak_rss_bytes']/1048576:.2f} |")
    lines += ['','## Interpretation','']+[f'- {text}' for text in report['limitations']]
    lines += ['', 'Raw samples and stage timings are in `samples.csv` and `report.json`. Inputs, '
              'ordering and configuration are in `protocol.json`. Artifact byte reproducibility: '
              +str(report['reproducibility']['identical'])+'.','']
    return '\n'.join(lines)


def evaluate(output, *, source_dir=None, generation=None, iterations=30, warmup=5, seed=0, force=False):
    check(sys.platform.startswith('linux'),'compression evaluation memory measurements currently require Linux')
    check(type(iterations) is int and 1<=iterations<=1000,'iterations must be 1..1000')
    check(type(warmup) is int and 0<=warmup<=100,'warmup must be 0..100')
    check(type(seed) is int and 0<=seed<2**32,'seed must be an unsigned 32-bit integer')
    output = Path(output).absolute()
    previous_report_hash = None
    check(not output.is_symlink(),'symlink output rejected')
    if output.exists():
        check(force,'output exists; choose a new directory or use --force')
        old = load_json(safe_file(output,'report.json'),16*1024*1024)
        check(isinstance(old,dict) and old.get('format')==FORMAT and {p.name for p in output.iterdir()}==TOP_FILES,
              'refusing to replace an unrelated directory')
        check(not any(p.is_symlink() for p in output.rglob('*')),'symlink in existing evaluation')
        check(artifact_inventory(output/'artifacts')==old.get('artifacts'),'existing evaluation artifacts changed')
        previous_report_hash = file_hash(output/'report.json',16*1024*1024)
    output.parent.mkdir(parents=True,exist_ok=True)
    check(shutil.disk_usage(output.parent).free>=128*1024*1024,'evaluation requires at least 128 MiB free space')
    started = time.perf_counter_ns()
    with tempfile.TemporaryDirectory(prefix='.crystal-evaluation-',dir=output.parent) as temporary:
        temporary = Path(temporary)
        stage = temporary/'evaluation'
        stage.mkdir()
        catalogue = build_candidates(stage/'artifacts',source_dir=source_dir,generation=generation)
        first = artifact_inventory(stage/'artifacts')
        first_build_ns = time.perf_counter_ns()-started
        replay_started = time.perf_counter_ns()
        replay_catalogue = build_candidates(temporary/'replay',source_dir=source_dir,generation=generation)
        check(catalogue==replay_catalogue and first==artifact_inventory(temporary/'replay'),
              'candidate artifacts are not byte-reproducible on this toolchain')
        replay_ns = time.perf_counter_ns()-replay_started
        protocol = {'format':FORMAT,'iterations':iterations,'warmup':warmup,'seed':seed,
                    'catalogue':catalogue,'workloads':workloads(catalogue,iterations,warmup,seed),
                    'connection_policies':['reopen_each_request','reuse_after_warmup'],
                    'sqlite_cache_budget_kib':2048,'sqlite_mmap':False,
                    'os_cache_policy':'warmed by construction and validation; no OS cache eviction',
                    'timer':'time.perf_counter_ns','quantile_method':'linear interpolation at (n-1)*q'}
        (stage/'protocol.json').write_bytes(canonical(protocol))
        jobs = [(name,workload,policy) for name in CANDIDATES for workload in protocol['workloads']
                for policy in protocol['connection_policies']]
        random.Random(seed+1).shuffle(jobs)
        measurements = []
        for name,workload,policy in jobs:
            job = {'artifacts':str(stage/'artifacts'),'candidate':name,'workload':workload,
                   'connection_policy':policy,'catalogue':catalogue,
                   'requests':protocol['workloads'][workload],'warmup':warmup}
            try:
                child = subprocess.run([sys.executable,'-m','crystal_geo.evaluation_worker'],
                                       input=canonical(job),capture_output=True,timeout=180)
            except subprocess.TimeoutExpired as exc:
                raise EvaluationError(f'benchmark timed out: {name}/{workload}/{policy}') from exc
            check(child.returncode==0,'benchmark worker failed: '+child.stderr.decode(errors='replace')[:1024])
            check(len(child.stdout)<=16*1024*1024,'worker result exceeds limit')
            measurements.append(json.loads(child.stdout))
        measurements.sort(key=lambda item:(item['candidate'],item['workload'],item['connection_policy']))
        sizes = {}
        for name,files in first.items():
            with CandidateReader(stage/'artifacts',name) as reader:
                payload_bytes = sum(len(reader.read(item['key'])) for item in catalogue)
            sizes[name] = {'payload_bytes':payload_bytes,'total_bytes':sum(f['size'] for f in files),
                           'sqlite_bytes':sum(f['size'] for f in files if f['path'].endswith(('.sqlite','.mbtiles'))),
                           'manifest_bytes':sum(f['size'] for f in files if f['path'].endswith('manifest.json'))}
            sizes[name]['overhead_bytes'] = sizes[name]['total_bytes']-payload_bytes
        report = {'format':FORMAT,'created_at':datetime.now(timezone.utc).isoformat(),
                  'measurement_scope':'exploratory' if iterations>=30 else 'smoke; illustrative timings only',
                  'implementation':'Python SQLite + Pillow; no Rust/mobile/GPU timing claim',
                  'protocol':{k:v for k,v in protocol.items() if k not in ('catalogue','workloads')},
                  'toolchain':{'python':platform.python_version(),'pillow':PIL.__version__,
                               'webp':features.version('webp'),'sqlite':sqlite3.sqlite_version,
                               'platform':platform.platform(),'architecture':platform.machine(),
                               'logical_cpu_count':os.cpu_count()},
                  'source_provenance':PROVENANCE,'warning':WARNING,
                  'build':{'mode':'fresh encode' if generation is None else 'reuse verified WebP generation',
                           'first_build_and_validation_ns':first_build_ns,'replay_build_and_validation_ns':replay_ns},
                  'artifacts':first,'sizes':sizes,'measurements':measurements,
                  'reproducibility':{'identical':True,'scope':'artifact bytes on this toolchain; timings and RSS vary'},
                  'pixel_validation':{'tiles_per_candidate':32,'exact_rgba_matches':32,
                                      'max_absolute_channel_error':0,'mean_absolute_channel_error':0},
                  'webp_to_raw_payload_ratio':sizes['crystal-webp']['payload_bytes']/sizes['crystal-raw']['payload_bytes'],
                  'recognition_accuracy':None,'energy_joules':None,
                  'limitations':[
                      'TX003 only: 32 tiles at one zoom; measurements do not establish general compression gains.',
                      'MBTiles stores exactly the same payloads as its Crystal counterpart; each date has a separate file and TMS rows.',
                      'Totals include unsigned manifest companions. MBTiles SQLite bytes include payloads; Crystal index bytes do not.',
                      'Raw RGBA is an evaluation-only control, not an installable Crystal codec or portable MBTiles image format.',
                      'OS caches were not evicted. Reopening resets connections, not the operating-system page cache.',
                      'Lookup and payload reads are combined; hash verification is applied equally from a trusted evaluation catalogue.',
                      'End-to-end includes connection open/close for reopen policy. Reused-connection initial open is reported separately.',
                      'Linux VmHWM is the current exec memory-map peak, including Python/native libraries and warmup; it is a coarse counter, not decoder-exclusive allocation. Raw ru_maxrss is retained as context.',
                      'Physical read-byte counters cover the worker workload and may be zero for cached I/O; payload bytes exclude SQLite page overhead.',
                      'Thirty samples yield descriptive P50/P95/P99 values, not statistical proof of performance superiority.',
                      'Recognition accuracy, battery, GPU, Flutter frame rate and true cold-start disk behavior are not measured.'
                  ]}
        (stage/'report.json').write_bytes(canonical(report))
        (stage/'report.md').write_text(_markdown(report))
        with (stage/'samples.csv').open('w',newline='') as stream:
            fields = ['candidate','workload','connection_policy','iteration','keys','open_ns','lookup_read_ns',
                      'verification_ns','decode_ns','close_ns','end_to_end_ns','payload_bytes']
            writer = csv.DictWriter(stream,fieldnames=fields,lineterminator='\n')
            writer.writeheader()
            for result in measurements:
                for sample in result['samples']:
                    row = {key:result[key] for key in ('candidate','workload','connection_policy')}
                    row.update(sample)
                    row['keys'] = json.dumps(row['keys'],separators=(',',':'))
                    writer.writerow(row)
        backup = temporary/'previous'
        if output.exists():
            check(force and previous_report_hash is not None
                  and file_hash(safe_file(output,'report.json'),16*1024*1024)==previous_report_hash,
                  'output changed while evaluation was running; existing results preserved')
            output.rename(backup)
        try:
            stage.rename(output)
        except OSError:
            if backup.exists():
                backup.rename(output)
            raise
    return report
