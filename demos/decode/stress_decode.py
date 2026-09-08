#!/usr/bin/env python3
"""Single-device SOPHON decode benchmark. Python 3.9+, standard library only."""
import argparse
import csv
from datetime import datetime
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import shutil
import runpy
import signal
import subprocess
import threading
import time


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('must be finite and positive')
    return number


def nonnegative(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError('must be finite and nonnegative')
    return number


def arguments(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--input', help='One local video, explicitly replicated per stream')
    source.add_argument('--inputs-file', type=Path, help='One RTSP URL or local path per line')
    p.add_argument('--device', type=int, default=0)
    p.add_argument('--steps', default='1', help='Increasing stream counts, e.g. 1,8,16,24,32')
    p.add_argument('--codec', choices=['h264', 'hevc'], default='h264')
    p.add_argument('--warmup', type=nonnegative, default=60)
    p.add_argument('--duration', type=positive, default=300, help='Measured seconds per step')
    p.add_argument('--window', type=positive, default=60, help='FPS statistics window seconds')
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--measure-only', dest='measure_only', action='store_true', help='Record performance without FPS acceptance (default)')
    mode.add_argument('--acceptance', dest='measure_only', action='store_false', help='Explicitly apply min-fps acceptance')
    p.set_defaults(measure_only=True)
    p.add_argument('--target-fps', type=positive, default=25)
    p.add_argument('--min-fps', type=positive, default=24.5)
    p.add_argument('--stall-timeout', type=positive, default=15)
    p.add_argument('--throughput', action='store_true', help='Unpaced local-file throughput only')
    p.add_argument('--ffmpeg', default='ffmpeg')
    p.add_argument('--ffprobe', default='ffprobe')
    p.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[2] / 'data/results/decode')
    p.add_argument('--dry-run', action='store_true', help='Print commands without starting processes')
    a = p.parse_args(argv)
    try:
        a.steps = [int(x) for x in a.steps.split(',')]
    except ValueError:
        p.error('--steps must contain integers')
    if not a.steps or a.steps[0] < 1 or a.steps != sorted(set(a.steps)):
        p.error('--steps must be positive, unique and increasing')
    if a.device < 0 or a.duration < a.window or a.min_fps > a.target_fps:
        p.error('require device >= 0, duration >= window and min-fps <= target-fps')
    if a.input:
        if '://' in a.input:
            p.error('use --inputs-file with separate RTSP URLs for live streams')
        a.sources = [str(Path(a.input).resolve())] * max(a.steps)
    else:
        lines = a.inputs_file.read_text(encoding='utf-8-sig').splitlines()
        a.sources = [s.strip() for s in lines if s.strip() and not s.lstrip().startswith('#')]
        a.sources = [s if '://' in s else str((a.inputs_file.resolve().parent / s).resolve())
                     for s in a.sources]
        if len(a.sources) < max(a.steps) or len(set(a.sources)) != len(a.sources):
            p.error('inputs-file must contain enough distinct sources')
    if any('://' in s and not s.startswith(('rtsp://', 'rtsps://')) for s in a.sources):
        p.error('only local files and RTSP inputs are supported')
    if a.throughput and any('://' in s for s in a.sources):
        p.error('--throughput requires local files')
    return a


def command(a, source):
    cmd = [a.ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'warning', '-xerror',
           '-nostats', '-progress', 'pipe:1']
    if '://' in source:
        cmd += ['-rtsp_transport', 'tcp']
    else:
        cmd += ['-stream_loop', '-1']
        if not a.throughput:
            cmd += ['-re']
    # Input decoder and card selection follow SOPHON's codec performance guide.
    cmd += ['-extra_frame_buffer_num', '5', '-output_format', '0', '-zero_copy', '1',
            '-sophon_idx', str(a.device), '-c:v', a.codec + '_bm', '-i', source,
            '-map', '0:v:0', '-an', '-sn', '-dn', '-vsync', '0', '-f', 'null', os.devnull]
    return cmd


def capture(cmd, timeout=20):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{cmd[0]} failed: {result.stderr[-2000:]}')
    return result.stdout


def probe_json(output):
    # SOPHON 0.14.0 prints these library diagnostics to stdout inside JSON.
    prefixes = ('so addr :', 'vpu firmware addr:', 'VERSION=')
    clean = '\n'.join(line for line in output.splitlines()
                      if not line.lstrip().startswith(prefixes))
    return json.loads(clean)


def preflight(a):
    version = capture([a.ffmpeg, '-version'])
    decoders = capture([a.ffmpeg, '-decoders'])
    if a.codec + '_bm' not in decoders:
        raise RuntimeError('SOPHON hardware decoder missing; do not use system/software FFmpeg')
    metadata = {}
    for source in dict.fromkeys(a.sources[:max(a.steps)]):
        cmd = [a.ffprobe, '-v', 'error']
        if '://' in source:
            cmd += ['-rtsp_transport', 'tcp']
        elif not Path(source).is_file():
            raise RuntimeError('Input file not found: ' + source)
        cmd += ['-select_streams', 'v:0', '-show_entries',
                'stream=codec_name,width,height,avg_frame_rate,r_frame_rate,bit_rate',
                '-of', 'json', source]
        streams = probe_json(capture(cmd)).get('streams', [])
        if not streams:
            raise RuntimeError('No video stream: ' + source)
        meta = streams[0]
        rate = meta.get('avg_frame_rate', '0/0')
        if rate in ('0/0', '0'):
            rate = meta.get('r_frame_rate', '0/0')
        try:
            fps = float(Fraction(rate))
        except (ValueError, ZeroDivisionError):
            raise RuntimeError('Unknown input frame rate; verify source before testing')
        if (meta.get('width'), meta.get('height')) != (1920, 1080):
            raise RuntimeError('Input must be 1920x1080: ' + source)
        if meta.get('codec_name') != a.codec or abs(fps - a.target_fps) > 0.05:
            raise RuntimeError('Input codec/FPS does not match test configuration: ' + source)
        metadata[source] = meta
    return {'ffmpeg_version': version, 'inputs': metadata}


class Worker:
    def __init__(self, cmd, directory):
        self.lock = threading.Lock()
        self.frame = 0
        self.last_frame_time = time.monotonic()
        self.error = None
        self.log = (directory / 'stderr.log').open('w', encoding='utf-8')
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=self.log,
                                         text=True, encoding='utf-8', errors='replace')
        except BaseException:
            self.log.close()
            raise
        self.thread = threading.Thread(target=self.read, args=(directory,), daemon=True)
        self.thread.start()

    def read(self, directory):
        try:
            with (directory / 'progress.log').open('w', encoding='utf-8') as raw:
                for line in self.proc.stdout:
                    raw.write(line)
                    if line.startswith('frame='):
                        frame = int(line.split('=', 1)[1])
                        with self.lock:
                            if frame < self.frame:
                                self.error = 'frame counter decreased'
                            if frame > self.frame:
                                self.last_frame_time = time.monotonic()
                            self.frame = frame
        except Exception as exc:
            with self.lock:
                self.error = str(exc)

    def snapshot(self):
        with self.lock:
            return self.frame, self.last_frame_time, self.error


def stop_workers(workers):
    # Only terminate the exact children created by this run; never killall/pkill.
    for w in workers:
        if w.proc.poll() is None:
            w.proc.terminate()
    deadline = time.monotonic() + 5
    for w in workers:
        try:
            w.proc.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            w.proc.kill()
            w.proc.wait()
        w.thread.join(timeout=2)
        w.proc.stdout.close()
        w.log.close()


def monitor(directory, stop, device=0):
    with (directory / 'monitor.jsonl').open('w', encoding='utf-8') as out:
        while not stop.is_set():
            entry = {'time': time.time()}
            for name in ('stat', 'meminfo', 'net/dev', 'loadavg'):
                path = Path('/proc') / name
                entry[name] = path.read_text() if path.exists() else None
            try:
                entry['bm_smi'] = capture(['bm-smi', '--text_format', '--noloop',
                                           f'--start_dev={device}', f'--last_dev={device}'], timeout=3) if shutil.which('bm-smi') else None
            except Exception as exc:
                entry['bm_smi_error'] = str(exc)
            out.write(json.dumps(entry) + '\n')
            out.flush()
            stop.wait(5)


def run_step(a, count, directory, interrupted, make_command=None):
    directory.mkdir()
    workers = []
    stop = threading.Event()
    monitoring = threading.Thread(target=monitor, args=(directory, stop, a.device), daemon=True)
    result = {'streams': count, 'status': 'incomplete', 'reason': None,
              'kind': 'offline_throughput' if a.throughput else 'decode_fps_check',
              'algorithm_fps': None, 'end_to_end_latency': None, 'decoder_drops': None,
              'windows': [], 'measured_seconds': 0}
    monitoring.start()
    try:
        for index, source in enumerate(a.sources[:count]):
            if interrupted.is_set():
                raise InterruptedError('user interrupted')
            stream_dir = directory / f'stream_{index:02d}'
            stream_dir.mkdir()
            cmd = command(a, source) if make_command is None else make_command(a, source, stream_dir)
            workers.append(Worker(cmd, stream_dir))
        # Common warmup and measurement boundaries AFTER all processes are started.
        measure_at = time.monotonic() + a.warmup
        baseline = None
        window_at = None
        started = None
        with (directory / 'samples.csv').open('w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(['monotonic_seconds', 'stream', 'frames', 'last_frame_age_seconds'])
            while True:
                now = time.monotonic()
                if interrupted.is_set():
                    raise InterruptedError('user interrupted')
                snapshots = [w.snapshot() for w in workers]
                for index, (w, (frame, last, error)) in enumerate(zip(workers, snapshots)):
                    writer.writerow([now, index, frame, now - last])
                    if w.proc.poll() is not None:
                        raise RuntimeError(f'stream {index} exited early ({w.proc.returncode}); see stderr.log')
                    if error or now - last > a.stall_timeout:
                        raise RuntimeError(f'stream {index}: {error or "no frame progress / stalled"}')
                file.flush()
                frames = [s[0] for s in snapshots]
                if baseline is None and now >= measure_at:
                    baseline, window_at, started = frames, now, now
                    result['measure_start_monotonic'] = now
                elif baseline is not None:
                    result['measured_seconds'] = now - started
                    ending = now - started >= a.duration
                    # Avoid a tiny tail window when scheduler jitter accumulates.
                    if now - window_at >= a.window and (a.duration - (now - started) >= a.window * 0.5 or ending):
                        elapsed = now - window_at
                        fps = [(f - b) / elapsed for f, b in zip(frames, baseline)]
                        result['windows'].append({'seconds': elapsed, 'fps': fps})
                        baseline, window_at = frames, now
                    elif ending:
                        elapsed = now - window_at
                        fps = [(f - b) / elapsed for f, b in zip(frames, baseline)]
                        result['windows'].append({'seconds': elapsed, 'fps': fps})
                    if ending:
                        result['measure_end_monotonic'] = now
                        minimum = min(min(w['fps']) for w in result['windows'])
                        result['min_stream_window_fps'] = minimum
                        result['measure_only'] = getattr(a, 'measure_only', False)
                        result['status'] = 'measured' if result['measure_only'] else ('fps_pass' if minimum >= a.min_fps else 'fps_fail')
                        result['reason'] = ('Performance recorded; no FPS acceptance applied' if result['measure_only'] else
                                            'FPS criterion only; latency/drop/stability acceptance requires further measurement')
                        break
                interrupted.wait(min(0.5, a.window / 4))
    except InterruptedError as exc:
        result['reason'] = str(exc)
    except Exception as exc:
        result['status'], result['reason'] = 'error', str(exc)
    finally:
        stop_workers(workers)
        stop.set()
        monitoring.join(timeout=5)
        result['exit_codes_after_cleanup'] = [w.proc.returncode for w in workers]
        (directory / 'summary.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def main(argv=None):
    a = arguments(argv)
    if a.dry_run:
        print(json.dumps({'device': a.device, 'steps': a.steps,
                          'commands': [command(a, s) for s in a.sources[:max(a.steps)]]}, indent=2))
        return 0
    interrupted = threading.Event()
    directory = None
    write_report = None
    run_status = 'incomplete'
    run_reason = 'Run ended before all planned stages completed'
    previous = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous[sig] = signal.signal(sig, lambda *_: interrupted.set())
    try:
        print('Checking SOPHON FFmpeg and input metadata...', flush=True)
        metadata = preflight(a)
        directory = a.output.resolve() / (datetime.now().strftime('%Y%m%d_%H%M%S') + f'_{os.getpid()}')
        directory.mkdir(parents=True)
        config = {k: str(v) if isinstance(v, Path) else v for k, v in vars(a).items()}
        (directory / 'config.json').write_text(json.dumps({'config': config, 'metadata': metadata}, indent=2), encoding='utf-8')
        (directory / 'run_state.json').write_text(json.dumps({'status': 'running'}), encoding='utf-8')
        write_report = runpy.run_path(str(Path(__file__).with_name('report.py')))['write_report']
        write_report(directory)
        print('Results: ' + str(directory), flush=True)
        results = []
        for count in a.steps:
            if interrupted.is_set():
                break
            print(f'Device {a.device}: {count} streams, warmup {a.warmup}s + measurement {a.duration}s', flush=True)
            result = run_step(a, count, directory / f'step_{count:02d}', interrupted)
            results.append(result)
            (directory / 'summary.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
            write_report(directory)
            print(f'{result["status"]}: {result["reason"]}', flush=True)
            print('Report: ' + str(directory / 'report.md'), flush=True)
            if result['status'] not in ('fps_pass', 'measured'):
                break
        complete = len(results) == len(a.steps) and all(r['status'] in ('fps_pass', 'measured') for r in results)
        run_status = 'completed' if complete else ('failed' if results and results[-1]['status'] in ('fps_fail', 'error') else 'incomplete')
        run_reason = ('All planned measurements completed' if a.measure_only else 'All planned FPS checks passed') if complete else ('User interrupted' if interrupted.is_set() else 'Stopped at an abnormal or incomplete stage')
        return 0 if complete else 1
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        run_status, run_reason = 'error', str(exc)
        print('Error: ' + str(exc))
        return 2
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if directory is not None and directory.exists():
            (directory / 'run_state.json').write_text(json.dumps({'status': run_status, 'reason': run_reason}), encoding='utf-8')
            if write_report:
                write_report(directory)


if __name__ == '__main__':
    raise SystemExit(main())
