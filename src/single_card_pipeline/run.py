#!/usr/bin/env python3
"""Run single-card decode + YOLOv8, with optional annotations and H264 output."""
import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import signal
import threading
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report import write_report

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('decode_bench', ROOT / 'src/single_card_decode/stress_decode.py')
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def verify_outputs(directory, result, a):
    encode = getattr(a, 'mode', 'encode') == 'encode'
    verification = []
    for stream in sorted(directory.glob('stream_*')):
        entry = {'stream': stream.name, 'ok': False}
        try:
            worker = json.loads((stream / 'worker_summary.json').read_text())
            records = 0
            services, lateness = [], []
            stages = {key: [] for key in ('decode_ms', 'analysis_ms', 'image_bridge_ms',
                      'preprocess_ms', 'inference_ms', 'inference_submit_ms', 'inference_sync_ms', 'input_release_ms', 'postprocess_ms', 'output_allocation_ms', 'output_copy_ms', 'transfer_wait_ms', 'output_transfer_ms', 'cpu_postprocess_ms', 'draw_ms', 'encode_submit_ms')}
            with (stream / 'detections.jsonl').open(encoding='utf-8') as records_file:
                for line in records_file:
                    row = json.loads(line)
                    if row['frame'] != records:
                        raise ValueError('Missing or duplicate detection result frame')
                    records += 1
                    if result.get('measure_start_monotonic', math.inf) <= row['completed_monotonic_s'] <= result.get('measure_end_monotonic', -math.inf):
                        services.append(row['service_ms'])
                        lateness.append(row['schedule_lateness_ms'])
                        for key in stages:
                            if row.get(key) is not None:
                                stages[key].append(row[key])
            meta, encoded = None, None
            if encode:
                output = bench.capture([a.ffprobe, '-v', 'error', '-select_streams', 'v:0',
                '-count_frames', '-show_entries', 'stream=codec_name,width,height,nb_read_frames,avg_frame_rate',
                '-of', 'json', str(stream / 'output.mp4')], timeout=max(60, a.duration + a.warmup))
                meta = bench.probe_json(output)['streams'][0]
                encoded = int(meta['nb_read_frames'])
            services.sort()
            entry.update({'analyzed_frames': worker['frames_analyzed'], 'result_records': records,
                          'verified_encoded_frames': encoded, 'video': meta,
                          'service_p95_ms': services[max(0, math.ceil(len(services)*0.95)-1)] if services else None,
                          'max_schedule_lateness_ms': max(lateness) if lateness else None,
                          'mean_stage_ms': {key: sum(values)/len(values) if values else None for key, values in stages.items()}})
            entry['buffer_management'] = {k: worker.get(k) for k in ('output_buffer', 'host_output_allocations', 'device_output_allocations', 'output_copy_bytes')}
            entry['ok'] = records == worker['frames_analyzed'] and records > 0
            if hasattr(a, 'output_buffer'):
                entry['ok'] = entry['ok'] and worker.get('output_buffer') == a.output_buffer
                if a.output_buffer == 'reuse':
                    entry['ok'] = entry['ok'] and worker.get('host_output_allocations') == 1 and worker.get('device_output_allocations') == 1
            if encode:
                entry['ok'] = (entry['ok'] and records == worker['frames_submitted'] == encoded and
                               meta['codec_name'] == 'h264' and (meta['width'], meta['height']) == (1920, 1080))
            else:
                entry['ok'] = (entry['ok'] and worker.get('mode') == 'analysis' and
                               worker['frames_submitted'] == 0 and not (stream / 'output.mp4').exists())
        except Exception as exc:
            entry['error'] = str(exc)
        verification.append(entry)
    return verification


def evaluate_result(result, verified, count, measure_only, max_lateness_ms):
    ok = (len(verified) == count and all(x['ok'] for x in verified) and
          result.get('exit_codes_after_cleanup') == [0] * count)
    ontime = all(x.get('max_schedule_lateness_ms') is not None and
                 x['max_schedule_lateness_ms'] <= max_lateness_ms for x in verified)
    if result['status'] in ('fps_pass', 'fps_fail'):
        if measure_only:
            result['status'] = 'measured' if ok else 'error'
            result['fps_status'] = 'not_evaluated'
            result['reason'] = {'outputs_ok': ok, 'fps_acceptance_applied': False, 'schedule_acceptance_applied': False}
        else:
            result['status'] = 'pass' if result['status'] == 'fps_pass' and ok and ontime else 'fail'
            result['reason'] = {'fps_ok': result['fps_status'] == 'fps_pass', 'outputs_ok': ok, 'schedule_ok': ontime}


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--bmodel', type=Path, required=True)
    parser.add_argument('--classnames', type=Path, default=ROOT / 'third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/coco.names')
    parser.add_argument('--app', type=Path, default=ROOT / 'src/single_card_pipeline/build/pipeline_worker.pcie')
    parser.add_argument('--output-buffer', choices=('baseline', 'reuse'), default='baseline', help='Reuse host and device output buffers (experimental)')
    parser.add_argument('--transfer-slots', type=int, default=0, help='Experimental concurrent output transfers; 0 disables gate')
    parser.add_argument('--bitrate', type=int, default=4000, help='H264 output kbps per stream')
    parser.add_argument('--mode', choices=('analysis', 'encode'), default='encode')
    parser.add_argument('--image-path', choices=('bgr', 'yuv', 'device-bgr'), default=None)
    acceptance = parser.add_mutually_exclusive_group()
    acceptance.add_argument('--measure-only', dest='measure_only', action='store_true', help='Measure performance without acceptance (default)')
    acceptance.add_argument('--acceptance', dest='measure_only', action='store_false', help='Explicitly apply FPS and lateness acceptance')
    parser.set_defaults(measure_only=True)
    parser.add_argument('--max-lateness-ms', type=bench.positive, default=1000)
    extra, remaining = parser.parse_known_args(argv)
    if extra.image_path is None:
        extra.image_path = 'device-bgr' if extra.mode == 'analysis' else 'bgr'
    a = bench.arguments(remaining)
    # The runner applies its own output integrity and optional FPS/schedule policy.
    a.measure_only = False
    a.mode = extra.mode
    a.output_buffer = extra.output_buffer
    if extra.image_path != 'bgr' and a.mode != 'analysis':
        parser.error('device image paths support analysis mode only')
    if a.throughput or any('://' in x for x in a.sources):
        parser.error('First full-pipeline baseline uses paced local video only')
    if extra.transfer_slots < 0:
        parser.error('transfer-slots must be non-negative')
    if extra.bitrate < 1:
        parser.error('bitrate must be positive')
    if a.output == Path('results/decode'):
        a.output = Path('results/analysis' if a.mode == 'analysis' else 'results/pipeline')
    def make_command(_, source, stream):
        return [str(extra.app.resolve()), f'--input={source}', f'--bmodel={extra.bmodel.resolve()}',
                f'--classnames={extra.classnames.resolve()}', f'--device={a.device}',
                f'--output={stream.resolve()}', f'--fps={a.target_fps}', f'--bitrate={extra.bitrate}',
                f'--mode={a.mode}', f'--image_path={extra.image_path}', f'--output_buffer={extra.output_buffer}'] + ([
                '--transfer_lock=' + str((stream.parent / ('transfer_%02d.lock' % (int(stream.name.rsplit('_', 1)[1]) % extra.transfer_slots))).resolve())
                ] if extra.transfer_slots else [])
    if a.dry_run:
        print(json.dumps({'steps': a.steps, 'example': make_command(a, a.sources[0], a.output / 'stream_00')}, indent=2))
        return 0
    for path in (extra.app, extra.bmodel, extra.classnames):
        if not path.is_file():
            parser.error('Missing file: ' + str(path))
    interrupted = threading.Event()
    previous = {s: signal.signal(s, lambda *_: interrupted.set()) for s in (signal.SIGINT, signal.SIGTERM)}
    directory = None
    state = {'status': 'running'}
    kind = 'local_file_decode_analysis' if a.mode == 'analysis' else 'local_file_decode_analysis_encode'
    try:
        metadata = bench.preflight(a)
        a.output.mkdir(parents=True, exist_ok=True)
        directory = a.output.resolve() / (datetime.now().strftime('%Y%m%d_%H%M%S') + f'_{os.getpid()}')
        directory.mkdir()
        metadata.update({'arguments': {k: str(v) if isinstance(v, Path) else v for k,v in vars(a).items()},
                         'pipeline_arguments': {k: str(v) if isinstance(v, Path) else v for k,v in vars(extra).items()},
                         'model_sha256': hashlib.sha256(extra.bmodel.read_bytes()).hexdigest(),
                         'worker_sha256': hashlib.sha256(extra.app.read_bytes()).hexdigest(),
                         'kind': kind, 'batch': 1})
        (directory / 'config.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        print('Results: ' + str(directory), flush=True)
        write_report(directory, state)
        results = []
        for count in a.steps:
            if interrupted.is_set():
                state = {'status': 'incomplete', 'reason': 'user interrupted'}
                break
            # Conservative disk estimate plus fixed reserve; worker also checks live free space.
            # Analysis JSON estimate: 4 KiB/frame; actual detection count varies.
            # Each worker retains the independent 512 MiB runtime reserve check.
            needed = count * (extra.bitrate * 1000 / 8 * 1.5 if a.mode == 'encode' else 4096 * a.target_fps) * (a.warmup + a.duration + 60) + 1024**3
            if shutil.disk_usage(directory).free < needed:
                raise RuntimeError(f'Insufficient disk space for {count} output streams (estimate {needed/1024**3:.1f} GiB)')
            step = directory / f'step_{count:02d}'
            print(f'Device {a.device}: {count} {a.mode} pipelines; {a.warmup}s warmup + {a.duration}s measurement', flush=True)
            result = bench.run_step(a, count, step, interrupted, make_command)
            result['kind'] = kind
            result['algorithm_fps'] = [w['fps'] for w in result['windows']]
            result['fps_status'] = result['status']
            result['measure_only'] = extra.measure_only
            result['fps_semantics'] = ('analysis result produced; JSON records checked after close' if a.mode == 'analysis' else
                                       'analysis result produced and VideoWriter.write returned; encoded completeness checked after close')
            print('Verifying detection records' + (' and encoded video...' if a.mode == 'encode' else '...'), flush=True)
            verified = verify_outputs(step, result, a)
            result['outputs'] = verified
            evaluate_result(result, verified, count, extra.measure_only, extra.max_lateness_ms)
            (step / 'summary.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            results.append(result)
            (directory / 'summary.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
            write_report(directory, state)
            print(f'{result["status"]}: {result["reason"]}', flush=True)
            if result['status'] not in ('pass', 'measured'):
                break
        passed = len(results) == len(a.steps) and all(r['status'] in ('pass', 'measured') for r in results)
        if state['status'] == 'running':
            state = {'status': 'completed' if passed else ('incomplete' if interrupted.is_set() else 'failed')}
        return 0 if passed else 1
    except Exception as exc:
        state = {'status': 'error', 'reason': str(exc)}
        print('Error: ' + str(exc), flush=True)
        return 2
    finally:
        if directory is not None and (directory / 'config.json').exists():
            write_report(directory, state)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    raise SystemExit(main())
