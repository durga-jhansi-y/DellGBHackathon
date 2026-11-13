"""One isolated memory/latency measurement; consumes a bounded internal JSON job."""
from __future__ import annotations

import json
from pathlib import Path
import resource
import sqlite3
import sys
import time

from .evaluation import CandidateReader, EvaluationError, check, decode_control, distribution
from .fixture import digest


def _physical_reads():
    try:
        values = dict(line.split(':',1) for line in Path('/proc/self/io').read_text().splitlines())
        return int(values['read_bytes'])
    except (OSError,KeyError,ValueError):
        return None


def _memory():
    """VmHWM belongs to this exec's memory map; ru_maxrss can retain fork history."""
    try:
        status = dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines())
        return int(status['VmHWM'].split()[0])*1024
    except (OSError,KeyError,ValueError) as exc:
        raise EvaluationError('Linux VmHWM is required for this memory measurement') from exc


def measure(job):
    check(sys.platform.startswith('linux'),'RSS measurement currently supports Linux only')
    check(job['connection_policy'] in ('reopen_each_request','reuse_after_warmup'),'invalid connection policy')
    requests = job['requests']
    warmup = job['warmup']
    check(type(warmup) is int and 0<=warmup<=100 and warmup<len(requests)<=1100,'invalid request count')
    catalogue = {tuple(item['key']):item for item in job['catalogue']}
    check(len(catalogue)==32,'invalid tile catalogue')
    profile = 'raw' if job['candidate'].endswith('raw') else 'webp'
    baseline = _memory()
    reads_before = _physical_reads()
    persistent = None
    initial_open_ns = None
    samples = []
    try:
        if job['connection_policy']=='reuse_after_warmup':
            started = time.perf_counter_ns()
            persistent = CandidateReader(job['artifacts'],job['candidate'])
            initial_open_ns = time.perf_counter_ns()-started
        for i,keys in enumerate(requests):
            check(1<=len(keys)<=4,'invalid workload size')
            begin = time.perf_counter_ns()
            reader = persistent or CandidateReader(job['artifacts'],job['candidate'])
            after_open = time.perf_counter_ns() if persistent is None else begin
            try:
                payloads = [reader.read(key) for key in keys]
                after_read = time.perf_counter_ns()
                for key,payload in zip(keys,payloads):
                    check(digest(payload)==catalogue[tuple(key)][profile+'_sha256'],'payload hash mismatch during benchmark')
                after_verify = time.perf_counter_ns()
                decoded = [decode_control(payload,profile) for payload in payloads]
                after_decode = time.perf_counter_ns()
            finally:
                if persistent is None:
                    reader.close()
            end = time.perf_counter_ns() if persistent is None else after_decode
            # Correctness validation is outside the measured retrieval/decode interval.
            for key,pixels in zip(keys,decoded):
                check(digest(pixels)==catalogue[tuple(key)]['rgba_sha256'],'pixels changed during benchmark')
            if i>=warmup:
                samples.append({'iteration':i-warmup,'keys':keys,'open_ns':after_open-begin,
                                'lookup_read_ns':after_read-after_open,'verification_ns':after_verify-after_read,
                                'decode_ns':after_decode-after_verify,'close_ns':end-after_decode,
                                'end_to_end_ns':end-begin,'payload_bytes':sum(map(len,payloads))})
    finally:
        if persistent:
            persistent.close()
    peak = _memory()
    reads_after = _physical_reads()
    fields = ('open_ns','lookup_read_ns','verification_ns','decode_ns','close_ns','end_to_end_ns')
    return {'candidate':job['candidate'],'workload':job['workload'],'connection_policy':job['connection_policy'],
            'initial_open_ns':initial_open_ns,'latency':{field:distribution([s[field] for s in samples]) for field in fields},
            'memory':{'baseline_peak_rss_bytes':baseline,'peak_rss_bytes':peak,'peak_increase_bytes':max(0,peak-baseline),
                      'ru_maxrss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                      'scope':'current exec memory map, including imports, warmup and workload; Linux /proc/self/status VmHWM'},
            'io':{'physical_read_bytes':reads_after-reads_before if reads_after is not None and reads_before is not None else None,
                  'scope':'worker after configuration load, including setup, warmup, workload and pixel validation',
                  'timed_payload_bytes':sum(sample['payload_bytes'] for sample in samples)},
            'samples':samples}


def main():
    try:
        raw = sys.stdin.buffer.read(1024*1024+1)
        check(len(raw)<=1024*1024,'worker input exceeds limit')
        result = measure(json.loads(raw))
        print(json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False))
        return 0
    except (EvaluationError,OSError,sqlite3.Error,ValueError,TypeError,KeyError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr)
        return 1


if __name__=='__main__':
    sys.exit(main())
