#!/usr/bin/env python3
"""Run YOLOv8 or YOLO26 on one or more cards, with separate per-card results."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
STARTUP_ALLOWANCE = 600.0
FINALIZE_ALLOWANCE = 120.0
TERMINATE_GRACE = 15.0
KILL_GRACE = 5.0


def finite_seconds(value, *, zero=False):
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('expected seconds as a number') from exc
    if not math.isfinite(number) or number < 0 or (number == 0 and not zero):
        raise argparse.ArgumentTypeError('expected finite ' + ('nonnegative' if zero else 'positive') + ' seconds')
    return number


def devices(value):
    fields = [part.strip() for part in value.split(',')]
    if not fields or any(re.fullmatch(r'[0-9]+', part) is None for part in fields):
        raise argparse.ArgumentTypeError('use comma-separated nonnegative device IDs, for example 0 or 0,1')
    parsed = [int(part) for part in fields]
    if any(device > 3 for device in parsed):
        raise argparse.ArgumentTypeError('device IDs must be in 0..3')
    if len(set(parsed)) != len(parsed):
        raise argparse.ArgumentTypeError('device IDs must be unique')
    return parsed


def repo_path(value):
    """Resolve relative arguments from the repository, without checking files."""
    path = Path(value).expanduser()
    return Path(os.path.abspath(path if path.is_absolute() else ROOT / path))


def arguments(model, argv=None):
    parser = argparse.ArgumentParser(description='Run ' + model + ' on selected cards and save per-card results.', allow_abbrev=False,
        epilog='Duration configures each backend measurement window, not total wall time. '
               'Cards start independently. YOLOv8 warmup starts after worker launch; '
               'YOLO26 warmup starts after initialization. Protection timeout per card: '
               'duration + warmup + 600 s startup allowance + 120 s finalization allowance. '
               'Cancellation sends TERM to owned process groups, then KILL after 15 s '
               '(up to 5 s to reap). These limits do not represent measured video time.')
    if model not in ('yolov8', 'yolo26'):
        raise ValueError('Unsupported model family: ' + model)
    parser.set_defaults(model=model)
    parser.add_argument('--devices', type=devices, default=[0], help='Unique device IDs in 0..3, e.g. 0 or 0,1,2,3 (default: 0)')
    parser.add_argument('--duration', type=finite_seconds, default=60.0, help='Measured seconds per card (default: 60)')
    parser.add_argument('--warmup', type=lambda value: finite_seconds(value, zero=True), default=5.0,
                        help='Warmup seconds per card (default: 5)')
    parser.add_argument('--streams', type=int, default=1, help='Independent input streams per card, 1..32 (default: 1)')
    parser.add_argument('--input', help='Local video; defaults to the selected family official example. '
                        + ('YOLOv8 currently requires 1920x1080 H.264 at 25 FPS. ' if model == 'yolov8' else '')
                        + 'Relative paths use the repository root')
    parser.add_argument('--bmodel', help='Override the selected family official B1 model')
    parser.add_argument('--classnames', help='Override the selected family class names file')
    parser.add_argument('--output', default='data/results/' + model, help='Parent directory for unique runs (default: data/results/' + model + ')')
    parser.add_argument('--dry-run', action='store_true', help='Print the plan without checking files, creating directories or starting processes')
    args = parser.parse_args(argv)
    if not 1 <= args.streams <= 32:
        parser.error('--streams must be in 1..32')
    if not math.isfinite(args.duration + args.warmup + STARTUP_ALLOWANCE + FINALIZE_ALLOWANCE):
        parser.error('duration and warmup are too large to form a finite protection timeout')
    if args.input is not None and '://' in args.input:
        parser.error('--input must be a local video file')
    family = 'YOLOv8_plus_det' if args.model == 'yolov8' else 'YOLO26'
    official = 'third_party/sophon-demo/sample/' + family
    if args.input is None:
        args.input = official + '/datasets/test_car_person_1080P.mp4'
    if args.bmodel is None:
        args.bmodel = official + '/models/BM1684X/' + (
            'yolov8s_int8_1b.bmodel' if args.model == 'yolov8' else 'yolo26s_fp32_1b.bmodel')
    if args.classnames is None:
        args.classnames = official + '/datasets/coco.names'
    for name in ('input', 'bmodel', 'classnames', 'output'):
        if not str(getattr(args, name)).strip():
            parser.error('--' + name + ' cannot be empty')
        setattr(args, name, repo_path(getattr(args, name)))
    return args


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def build_plan(args, run_id=None):
    run_id = run_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:12]
    directory = args.output / run_id
    executable = ROOT / 'demos' / args.model / 'build' / (
        'pipeline_worker.pcie' if args.model == 'yolov8' else 'yolo26_streams.pcie')
    runner = ROOT / 'demos/yolov8/runner.py'
    timing = {
        'duration_seconds': args.duration, 'warmup_seconds': args.warmup,
        'window_seconds': min(10.0, args.duration),
        'card_start': 'independent; no cross-card measurement barrier',
        'duration_scope': 'backend configured measurement window; not total launcher wall time',
        'backend': ('YOLOv8 warmup starts after worker processes are launched. Model initialization '
                    'uses warmup time and can extend into the measurement window.' if args.model == 'yolov8' else
                    'YOLO26 initializes its stream workers before starting warmup and measurement.'),
        'fps_policy': ('YOLOv8 backend retains its source validation and target FPS; no frame conversion or sampling is requested.'
                       if args.model == 'yolov8' else 'YOLO26 paces local input using source metadata FPS; no sampling is requested.'),
    }
    plan = {
        'schema_version': 1, 'run_id': run_id, 'status': 'planned', 'created_at_utc': utc_now(),
        'run_directory': str(directory), 'model': args.model, 'streams_per_card': args.streams,
        'input': str(args.input), 'bmodel': str(args.bmodel), 'classnames': str(args.classnames),
        'executable': str(executable), 'timing_semantics': timing,
        'protection_timeout_seconds': args.duration + args.warmup + STARTUP_ALLOWANCE + FINALIZE_ALLOWANCE,
        'startup_allowance_seconds': STARTUP_ALLOWANCE, 'finalize_allowance_seconds': FINALIZE_ALLOWANCE,
        'termination_grace_seconds': TERMINATE_GRACE, 'kill_reap_seconds': KILL_GRACE,
        'cards': [],
    }
    for device in args.devices:
        output = directory / ('device_' + str(device))
        common = ['--input', str(args.input), '--bmodel', str(args.bmodel), '--classnames', str(args.classnames),
                  '--device', str(device), '--warmup', str(args.warmup), '--duration', str(args.duration),
                  '--window', str(timing['window_seconds']), '--output', str(output)]
        if args.model == 'yolov8':
            command = [sys.executable, str(runner), '--mode', 'analysis', '--measure-only',
                       '--steps', str(args.streams), '--app', str(executable)] + common
        else:
            command = [str(executable), '--streams', str(args.streams), '--image-path', 'bgr',
                       '--pace-fps', '0', '--local-eof', 'loop'] + common
        plan['cards'].append({'device': device, 'command': command, 'output_directory': str(output),
                              'log_file': str(directory / ('device_' + str(device) + '.log')),
                              'status': 'pending', 'exit_code': None, 'pid': None})
    return plan


def preflight(plan):
    if not sys.platform.startswith('linux'):
        raise RuntimeError('Actual execution requires Linux with the SOPHON SDK; use --dry-run to inspect commands elsewhere')
    paths = [plan[key] for key in ('input', 'bmodel', 'classnames', 'executable')]
    if plan['model'] == 'yolov8':
        paths.append(plan['cards'][0]['command'][1])
    for name in paths:
        if not Path(name).is_file() or not os.access(name, os.R_OK):
            raise RuntimeError('Required readable file is missing: ' + name)
    if not os.access(plan['executable'], os.X_OK):
        raise RuntimeError('Backend is not executable: ' + plan['executable'])


def save_state(plan):
    directory = Path(plan['run_directory'])
    summary = {key: plan[key] for key in ('run_id', 'status', 'model', 'streams_per_card', 'timing_semantics',
               'protection_timeout_seconds', 'termination_grace_seconds', 'kill_reap_seconds', 'cards')}
    summary['result_semantics'] = 'Process completion and exit status only. Consult each backend output for measurements; no aggregate FPS is calculated.'
    for key in ('reason', 'signal', 'started_at_utc', 'finished_at_utc', 'wall_seconds', 'cleanup_errors'):
        if key in plan:
            summary[key] = plan[key]
    for filename, content in (('run.json', plan), ('summary.json', summary)):
        temporary = directory / (filename + '.tmp')
        temporary.write_text(json.dumps(content, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(directory / filename)


def group_alive(pid):
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False


def signal_group(pid, signum):
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        pass


def owned_group_alive(child):
    # Once a group disappears, its numeric ID can be reused by an unrelated job.
    if child.get('group_closed', False):
        return False
    if not group_alive(child['process'].pid):
        child['group_closed'] = True
        child['card']['process_group_released'] = True
        return False
    return True


def stop_children(children):
    """Only signal groups created here by Popen(start_new_session=True)."""
    errors, pending = [], []
    for child in children:
        process, card = child['process'], child['card']
        try:
            process.poll()  # Reap the group leader before checking for remaining descendants.
            if owned_group_alive(child):
                if card['status'] == 'completed':
                    errors.append('device %s backend exited but its process group remains' % card['device'])
                signal_group(process.pid, signal.SIGTERM)
                card['termination_requested'] = True
                pending.append(child)
        except OSError as exc:
            errors.append('device %s TERM: %s' % (card['device'], exc))
    deadline = time.monotonic() + TERMINATE_GRACE
    while pending and time.monotonic() < deadline:
        remaining = []
        for child in pending:
            child['process'].poll()
            try:
                if owned_group_alive(child):
                    remaining.append(child)
            except OSError as exc:
                errors.append('device %s group check: %s' % (child['card']['device'], exc))
        pending = remaining
        if pending:
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
    for child in pending:
        try:
            if owned_group_alive(child):
                signal_group(child['process'].pid, signal.SIGKILL)
                child['card']['kill_requested'] = True
        except OSError as exc:
            errors.append('device %s KILL: %s' % (child['card']['device'], exc))
    reap_deadline = time.monotonic() + KILL_GRACE
    for child in children:
        try:
            child['process'].wait(timeout=max(0.0, reap_deadline - time.monotonic()))
        except (subprocess.TimeoutExpired, OSError) as exc:
            errors.append('device %s reap: %s' % (child['card']['device'], exc))
        child['card']['exit_code'] = child['process'].poll()
    # A reaped group leader does not prove that its descendants have exited.
    remaining = list(children)
    while remaining:
        active = []
        for child in remaining:
            try:
                if owned_group_alive(child):
                    active.append(child)
            except OSError as exc:
                errors.append('device %s final group check: %s' % (child['card']['device'], exc))
        remaining = active
        if not remaining or time.monotonic() >= reap_deadline:
            break
        time.sleep(min(0.1, max(0.0, reap_deadline - time.monotonic())))
    for child in remaining:
        errors.append('device %s process group remains after bounded cleanup' % child['card']['device'])
    return errors


def supervise(plan, interrupted, received_signal):
    directory = Path(plan['run_directory'])
    directory.mkdir(parents=True, exist_ok=False)
    children, logs = [], []
    started = time.monotonic()
    status, reason = 'completed', None
    plan.update(status='running', started_at_utc=utc_now())
    try:
        save_state(plan)
        for card in plan['cards']:
            if interrupted.is_set():
                break
            log = Path(card['log_file']).open('x', encoding='utf-8')
            logs.append(log)
            try:
                process = subprocess.Popen(card['command'], cwd=ROOT, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            except Exception as exc:
                card.update(status='failed', error='Could not start backend: ' + str(exc))
                raise
            child = {'card': card, 'process': process, 'started': time.monotonic(), 'group_closed': False}
            children.append(child)
            card.update(status='running', pid=process.pid, started_at_utc=utc_now())
            save_state(plan)
        while children and not interrupted.is_set():
            running, changed = False, False
            for child in children:
                card, process = child['card'], child['process']
                if card['status'] != 'running':
                    continue
                code = process.poll()
                if code is not None:
                    card.update(status='completed' if code == 0 else 'failed', exit_code=code,
                                finished_at_utc=utc_now())
                    changed = True
                    if owned_group_alive(child):
                        card.update(status='failed', error='Backend exited while its process group is still active')
                        status, reason = 'failed', 'Device %s left active descendants after backend exit' % card['device']
                        break
                    if code != 0:
                        status, reason = 'failed', 'Device %s backend exited with code %s' % (card['device'], code)
                        break
                elif time.monotonic() - child['started'] >= plan['protection_timeout_seconds']:
                    card.update(status='timed_out', error='Per-card protection timeout exceeded')
                    status, reason, changed = 'timed_out', 'Device %s exceeded the protection timeout' % card['device'], True
                    break
                else:
                    running = True
            if changed:
                save_state(plan)
            if status != 'completed' or not running:
                break
            interrupted.wait(0.1)
        if interrupted.is_set():
            status, reason = 'interrupted', 'Cancellation requested'
    except Exception as exc:
        status, reason = 'failed', str(exc)
    finally:
        for card in plan['cards']:
            if card['status'] in ('pending', 'running'):
                card['status'] = 'interrupted' if status == 'interrupted' else 'cancelled'
        try:
            cleanup_errors = stop_children(children)
        finally:
            for log in logs:
                log.close()
        if cleanup_errors:
            plan['cleanup_errors'] = cleanup_errors
            if status == 'completed':
                status, reason = 'failed', 'Could not finish child-process cleanup'
        if status == 'completed' and not all(card['status'] == 'completed' and card['exit_code'] == 0 for card in plan['cards']):
            status, reason = 'failed', 'Not every requested card completed successfully'
        plan.update(status=status, reason=reason, finished_at_utc=utc_now(), wall_seconds=time.monotonic() - started)
        if received_signal:
            plan['signal'] = received_signal[0]
        save_state(plan)
    if status == 'completed':
        return 0
    if status == 'interrupted':
        return 128 + (received_signal[0] if received_signal else signal.SIGINT)
    return 124 if status == 'timed_out' else 1


def main(model, argv=None):
    args = arguments(model, argv)
    plan = build_plan(args)
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    try:
        preflight(plan)
    except RuntimeError as exc:
        print('Error: ' + str(exc), file=sys.stderr)
        return 2
    interrupted, received_signal = threading.Event(), []
    def request_stop(signum, _frame):
        if not received_signal:
            received_signal.append(signum)
        interrupted.set()
    previous = {signum: signal.signal(signum, request_stop) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        print('Results: ' + plan['run_directory'], flush=True)
        return supervise(plan, interrupted, received_signal)
    except OSError as exc:
        print('Error writing run files: ' + str(exc), file=sys.stderr)
        return 1
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
