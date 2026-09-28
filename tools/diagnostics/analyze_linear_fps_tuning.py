#!/usr/bin/env python3
"""Validate saved linear-output throughput experiments and aggregate equal windows."""
import argparse
import hashlib
import json
import statistics
from pathlib import Path


def analyze(roots):
    rows = []
    verified = 0
    for root in roots:
        manifest = root / 'manifest.json'
        if manifest.exists():
            for name, expected in json.loads(manifest.read_text()).items():
                if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
                    raise ValueError('Hash mismatch: ' + str(root / name))
                verified += 1
        metadata = json.loads((root / 'metadata.json').read_text())
        if json.loads((root / 'state.json').read_text())['status'] != 'completed':
            raise ValueError('Incomplete experiment: ' + str(root))
        for row in json.loads((root / 'comparison.json').read_text()):
            folder = root / row['stage']
            summary = json.loads((folder / 'worker/summary.json').read_text())
            streams = summary['streams']
            if summary['status'] != 'measured' or not summary['accounting_complete']:
                raise ValueError('Incomplete frame accounting')
            if len(streams) != metadata['streams'] or any(s['error'] for s in streams):
                raise ValueError('Stream count/error verification failed')
            logs = (folder / 'stderr.log').read_text()
            for marker in ('output_format requested=101 effective=0',
                           'extra_frame_buffer_num requested=2 effective=8'):
                if logs.count(marker) != len(streams):
                    raise ValueError('Decoder overrides missing: ' + str(folder))
            if sum(s['results_readback_measured'] for s in streams) != sum(s['completed_measured'] for s in streams):
                raise ValueError('Not all completed frames returned detection results')
            start = summary['measurement_start_monotonic_s']
            end = summary['observed_measurement_end_monotonic_s']
            samples = [json.loads(line) for line in (root / (row['stage'] + '-wall-samples.jsonl')).read_text().splitlines()]
            samples = [s for s in samples if start <= s['snapshot_monotonic_s'] < end]
            row.update(experiment=root.name, device=metadata['device'],
                       formal_status_samples=len(samples),
                       formal_stale_channel_samples=sum(sum(c['stale'] for c in s['streams']) for s in samples))
            rows.append(row)
    groups = []
    for device, mode in sorted({(r['device'], r['mode']) for r in rows}):
        subset = [r for r in rows if r['device'] == device and r['mode'] == mode]
        group = {'device': device, 'mode': mode, 'runs': len(subset)}
        for field in ('infer_fps', 'tpu_percent', 'preview_fps_per_stream', 'result_MB_s', 'preview_MB_s'):
            group[field] = statistics.mean(r[field] for r in subset)
        group.update(worst_gap_s=max(r['maximum_no_result_interval_s'] for r in subset),
                     minimum_stream_fps=min(r['minimum_stream_fps'] for r in subset),
                     stale_drops_lifetime=sum(r['stale_drops_lifetime'] for r in subset),
                     formal_stale_channel_samples=sum(r['formal_stale_channel_samples'] for r in subset))
        groups.append(group)
    return {'verified_files': verified, 'rows': rows, 'groups': groups}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roots', nargs='+', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = analyze(args.roots)
    with args.output.open('x') as out:
        json.dump(result, out, indent=2)
    print(json.dumps(result['groups'], indent=2))
