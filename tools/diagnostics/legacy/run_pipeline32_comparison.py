#!/usr/bin/env python3
"""Compare sequential analysis/encoded files and latest-frame HDMI on one Gen2 x1 card."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys

from run_readback_study import ROOT, run_stage, save

CASES = ('analysis_device', 'analysis_bgr', 'encode_bgr', 'wall_no_preview',
         'wall_preview', 'wall_preview_no_age_limit')
STAGES = ('decode_ms', 'image_bridge_ms', 'preprocess_ms', 'inference_ms',
          'inference_submit_ms', 'inference_sync_ms', 'output_copy_ms',
          'cpu_postprocess_ms', 'draw_ms', 'encode_submit_ms',
          'preview_vpp_ms', 'preview_readback_ms')


def command(a, case):
    common = ['--device', str(a.device), '--input', str(a.input), '--bmodel', str(a.bmodel),
              '--classnames', str(a.classnames), '--warmup', str(a.warmup),
              '--duration', str(a.duration), '--window', str(min(10,a.duration)), '--output', '{stage}/worker',
              '--output-buffer', 'reuse', '--score-gate', 'on',
              '--score-gate-model', str(a.gate_model), '--gate-merge-budget-kib', '64']
    if case.startswith('wall_'):
        return [str(ROOT / 'demos/hdmi_wall/build/hdmi_wall.pcie')] + common + [
            '--streams', str(a.streams), '--local-eof', 'loop', '--policy', 'latest',
            '--infer-fps', '0', '--max-frame-age-ms',
            '0' if case.endswith('no_age_limit') else '250', '--image-path', 'auto',
            '--record-mode', 'summary', '--prime-local-decoders', 'on',
            '--cpu-post', 'selected', '--preview-fps', '0' if case == 'wall_no_preview' else '10',
            '--observe-decode', 'off', '--readback-control', '{stage}/readback.json']
    return [sys.executable, str(ROOT / 'demos/yolov8/runner.py')] + common + [
        '--steps', str(a.streams), '--measure-only', '--mode',
        'encode' if case == 'encode_bgr' else 'analysis', '--image-path',
        'device-bgr' if case == 'analysis_device' else 'bgr',
        '--stall-timeout', str(max(120,a.warmup+a.duration+30))]


def mean(values):
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else None


def telemetry(folder, start, end):
    values = []
    for line in (folder / 'telemetry.jsonl').read_text().splitlines():
        row = json.loads(line)
        found = re.findall(r'(\d+)%', row['smi'])
        if start <= row['monotonic'] < end and row['returncode'] == 0 and len(found) == 1:
            values.append(int(found[0]))
    return {'tpu_percent': mean(values), 'tpu_samples': len(values)}


def summarize(folder, case, streams):
    result = {'case': case, 'stage': folder.name}
    if case.startswith('wall_'):
        data = json.loads((folder / 'worker/summary.json').read_text())
        if data['status'] != 'measured' or not data['accounting_complete']:
            raise RuntimeError('Incomplete wall measurement')
        start, end = data['measurement_start_monotonic_s'], data['observed_measurement_end_monotonic_s']
        rows = data['streams']
        if len(rows) != streams:
            raise RuntimeError('Incorrect stream count')
        seconds = data['measured_seconds']
        counts = [s['completed_measured'] for s in rows]
        result.update(decode_fps=data['total_decoded_fps'],
                      preview_MB_s=sum(s['preview_readback_bytes_measured'] for s in rows)/seconds/1e6,
                      result_MB_s=sum(s['score_gate_measured']['total_d2h_bytes'] for s in rows)/seconds/1e6,
                      dropped_stale_lifetime=sum(s['dropped_stale'] for s in rows),
                      dropped_overwrite_lifetime=sum(s['dropped_overwrite'] for s in rows),
                      source_age_max_ms=max((s['source_age_ms']['max'] for s in rows
                                             if s['source_age_ms']['max'] is not None), default=None),
                      decoder_source_lag_max_ms=None,
                      videos=[])
        timings = {}
        for key in STAGES:
            stats = [s.get(key, {}) for s in rows]
            count = sum(s.get('count', 0) for s in stats)
            timings[key] = sum(s.get('sum', 0) for s in stats)/count if count else None
    else:
        files = list((folder / 'worker').glob('*/step_*/summary.json'))
        if len(files) != 1:
            raise RuntimeError('Expected one runner measurement')
        data = json.loads(files[0].read_text())
        if data['status'] != 'measured' or not all(s['ok'] for s in data['outputs']):
            raise RuntimeError('Failed detection/video integrity verification')
        start, end = data['measure_start_monotonic'], data['measure_end_monotonic']
        seconds = end-start
        counts, selected, videos = [], [], []
        directories = sorted(files[0].parent.glob('stream_*'))
        if len(directories) != streams:
            raise RuntimeError('Incorrect stream count')
        for directory in directories:
            records = [json.loads(line) for line in (directory/'detections.jsonl').read_text().splitlines()]
            if not records or records[0]['completed_monotonic_s'] >= start:
                raise RuntimeError('Worker initialization entered formal window; increase warmup')
            formal = [r for r in records if start <= r['completed_monotonic_s'] < end]
            counts.append(len(formal))
            selected.extend(formal)
            video = directory/'output.mp4'
            if video.exists():
                videos.append(str(video))
        timings = {key: mean([r.get(key) for r in selected]) for key in STAGES}
        result.update(decode_fps=None, preview_MB_s=0,
                      result_MB_s=sum(r['score_gate_read_bytes'] for r in selected)/seconds/1e6,
                      schedule_lateness_max_ms=max((r['schedule_lateness_ms'] for r in selected), default=None),
                      dropped_stale_lifetime=None, dropped_overwrite_lifetime=None,
                      videos=videos, verified_encoded_frames=sum(s.get('verified_encoded_frames') or 0 for s in data['outputs']))
    result.update(seconds=seconds, fps=sum(counts)/seconds,
                  minimum_stream_fps=min(counts)/seconds, zero_streams=counts.count(0),
                  per_stream_fps=[n/seconds for n in counts], stage_mean_ms=timings,
                  measurement_start=start, measurement_end=end)
    result.update(telemetry(folder, start, end))
    if not result['tpu_samples']:
        raise RuntimeError('No valid TPU samples in formal window')
    save(folder/'comparison.json', result)
    return result


def report(root, rows):
    save(root/'comparison.json', rows)
    lines = ['# 32-stream pipeline comparison', '',
             'Sequential file analysis/encoding and latest-frame wall are different workloads.',
             'Rates use formal windows; video integrity counts include warmup and shutdown.',
             'Drop counters include warmup/shutdown. Result/preview MB/s exclude image bridging, encoder traffic and protocol overhead.',
             'Stage times include waiting and can overlap across streams. Missing metrics are not zero.', '',
             '| Stage | Detection FPS | Min stream | Zero streams | TPU % | Result MB/s | Preview MB/s |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['stage']} | {r['fps']:.2f} | {r['minimum_stream_fps']:.2f} | {r['zero_streams']} | {r['tpu_percent']:.2f} | {r['result_MB_s']:.2f} | {r['preview_MB_s']:.2f} |")
    lines += ['', 'Video paths, per-stream rates, source lateness and stage timings: comparison.json.',
              'A no-age-limit recovery supports an age-filter contribution, not a unique hardware/driver root cause.',
              'No-age-limit may show old frames; higher throughput is not proof of realtime operation.']
    (root/'comparison.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')


def preflight(a):
    if sys.platform != 'linux':
        raise RuntimeError('Run on the Linux board; --dry-run works without hardware')
    link = Path('/sys/bus/pci/devices')/a.bdf
    speed, width = (link/'current_link_speed').read_text().strip(), (link/'current_link_width').read_text().strip()
    expected_speed = getattr(a, 'expected_link_speed', '5.0')
    if not speed.startswith(expected_speed) or width != '1':
        raise RuntimeError(f'Expected negotiated {expected_speed} GT/s x1, got {speed} x{width}')
    smi = subprocess.run(['bm-smi','--noloop','--text_format',f'--start_dev={a.device}',f'--last_dev={a.device}'],
                         capture_output=True, text=True, check=True, timeout=10).stdout
    if a.bdf.lstrip('0') not in smi:
        raise RuntimeError('Device/BDF mismatch')
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            executable = (path/'exe').resolve().name
        except OSError:
            continue
        if executable.endswith('.pcie') or executable in ('ffmpeg', 'ffplay', 'test_cdma_perf'):
            raise RuntimeError(f'Competing process {path.name}: {executable}; stop it before measurement')
    paths = [a.input, a.bmodel, a.classnames, a.gate_model,
             ROOT/'demos/yolov8/build/pipeline_worker.pcie', ROOT/'demos/hdmi_wall/build/hdmi_wall.pcie']
    artifacts = []
    for path in paths:
        h = hashlib.sha256()
        with path.open('rb') as file:
            for block in iter(lambda: file.read(1024*1024), b''):
                h.update(block)
        artifacts.append({'path':str(path), 'bytes':path.stat().st_size, 'sha256':h.hexdigest()})
    required = a.repeats*a.streams*750000*(a.warmup+a.duration+60)+2*1024**3
    if shutil.disk_usage(ROOT).free < required:
        raise RuntimeError(f'Need approximately {required/1024**3:.1f} GiB free for retained encoded runs')
    return {'link_speed':speed, 'link_width':width, 'smi':smi, 'artifacts':artifacts}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device', type=int, required=True)
    p.add_argument('--bdf', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--streams', type=int, default=32)
    p.add_argument('--warmup', type=int, default=30)
    p.add_argument('--duration', type=int, default=60)
    p.add_argument('--repeats', type=int, default=2)
    p.add_argument('--input', type=Path, default=ROOT/'data/inputs/highway_1080p25.mp4')
    p.add_argument('--bmodel', type=Path, default=ROOT/'third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel')
    p.add_argument('--classnames', type=Path, default=ROOT/'data/models/coco.names')
    p.add_argument('--gate-model', type=Path, default=ROOT/'data/models/score_gate_reducemax_f32.bmodel')
    p.add_argument('--cases', nargs='+', choices=CASES, default=list(CASES))
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    if not 1 <= a.streams <= 32 or min(a.warmup,a.duration,a.repeats) < 1 or a.device < 0:
        p.error('Invalid stream count, device or timing')
    if len(set(a.cases)) != len(a.cases):
        p.error('Duplicate cases')
    for name in ('output','input','bmodel','classnames','gate_model'):
        setattr(a, name, getattr(a,name).resolve())
    plan = [{'name':f'r{rep}_{case}', 'case':case, 'command':command(a,case)}
            for rep in range(a.repeats) for case in (a.cases if rep%2 == 0 else list(reversed(a.cases)))]
    if a.dry_run:
        print(json.dumps(plan, indent=2))
        return
    if a.output.exists():
        p.error('Use a new output directory')
    evidence = preflight(a)
    a.output.mkdir(parents=True)
    save(a.output/'preflight.json', evidence)
    save(a.output/'plan.json', plan)
    save(a.output/'config.json', {k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()})
    rows, failures = [], []
    try:
        for item in plan:
            try:
                run_stage(a.output,item['name'],item['command'],a.device,
                          timeout=a.warmup+a.duration+600,readback=True if item['case'].startswith('wall_') else None)
                rows.append(summarize(a.output/item['name'],item['case'],a.streams))
                report(a.output,rows)
            except Exception as exc:
                failures.append({'stage':item['name'],'error':str(exc)})
                save(a.output/item['name']/'comparison_failure.json',failures[-1])
                print(str(exc),file=sys.stderr,flush=True)
        save(a.output/'state.json',{'status':'failed' if failures else 'completed','failures':failures})
    except BaseException as exc:
        save(a.output/'state.json',{'status':'interrupted','error':str(exc)})
        raise
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
