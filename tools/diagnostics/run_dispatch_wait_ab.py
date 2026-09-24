#!/usr/bin/env python3
"""Bounded read-only driver tracing; never change or replace a driver.

Board-specific diagnostic. Refuses active tracers/loads. Original evidence is
never overwritten. Function filtering keeps graph overhead bounded.
"""
import argparse
import hashlib
import json
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from run_readback_study import ROOT, run_stage, save

TRACE = Path('/sys/kernel/debug/tracing')
KEYS = ['tracing_on', 'current_tracer', 'buffer_size_kb', 'max_graph_depth',
        'options/funcgraph-abstime', 'options/funcgraph-proc',
        'options/funcgraph-duration', 'options/sleep-time']
GROUPS = {
    'vpp': (['bm1686_trigger_vpp'], ['bm1686_trigger_vpp', 'down_interruptible',
        'bmdev_memcpy_s2d_internal', 'bm1684_cdma_transfer', 'mutex_lock',
        'mutex_unlock', 'schedule_timeout', 'wait_for_completion_timeout']),
    'dma': (['bmdev_memcpy_s2d', 'bmdev_memcpy_d2s'], ['bmdev_memcpy_s2d',
        'bmdev_memcpy_d2s', 'bm1684_cdma_transfer', 'mutex_lock', 'mutex_unlock',
        'schedule_timeout', 'wait_for_completion_timeout']),
    'api': (['bmdrv_send_api', 'bmdrv_thread_sync_api'], ['bmdrv_send_api',
        'bmdrv_thread_sync_api', 'mutex_lock', 'mutex_unlock', 'bmdev_wait_msgfifo',
        'bmdev_copy_to_msgfifo', 'wait_for_completion_timeout', 'schedule_timeout']),
    'mmio': (['bm1686_trigger_vpp', 'bmdrv_send_api'], ['bm1686_trigger_vpp',
        'bmdrv_send_api', 'bmdev_memcpy_s2d_internal', 'bm1684_cdma_transfer',
        'bmdev_copy_to_msgfifo', 'bmdev_wait_msgfifo', 'vpp0_reg_read',
        'vpp1_reg_read', 'vpp0_reg_write', 'vpp1_reg_write', 'shmem_reg_write',
        'gp_reg_write', 'cdma_reg_read', 'cdma_reg_write', 'nv_timer_reg_read']),
}


def command(*args):
    return subprocess.check_output(args, text=True, timeout=15).strip()


def conflicts():
    found = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            name = (proc / 'exe').resolve().name
            if name.endswith('.pcie') or name in ('ffplay', 'ffmpeg', 'test_cdma_perf'):
                found.append([proc.name, name])
        except OSError:
            pass
    return found


class Capture:
    def __init__(self):
        self.old = {k: (TRACE/k).read_text().strip() for k in KEYS}
        self.old['buffer_size_kb'] = self.old['buffer_size_kb'].split()[0]
        self.clock = next(w[1:-1] for w in (TRACE/'trace_clock').read_text().split()
                          if w.startswith('['))
        assert self.old['current_tracer'] == 'nop', 'Existing tracer is active'
        for key in ('set_ftrace_filter', 'set_graph_function'):
            assert 'all functions enabled' in (TRACE/key).read_text(), key
        assert (TRACE/'set_ftrace_pid').read_text().strip() in ('', 'no pid'), 'Existing PID filter'
        self.available = {s.split()[0] for s in (TRACE/'available_filter_functions').read_text().splitlines()}

    @staticmethod
    def put(key, value):
        (TRACE/key).write_text(str(value))

    def restore(self):
        self.put('tracing_on', 0)
        self.put('current_tracer', 'nop')
        self.put('set_graph_function', '')
        self.put('set_ftrace_filter', '')
        self.put('trace_clock', self.clock)
        for k, v in self.old.items():
            if k not in ('tracing_on', 'current_tracer'):
                self.put(k, v)
        self.put('current_tracer', self.old['current_tracer'])
        self.put('tracing_on', self.old['tracing_on'])

    def run(self, dest, group, seconds):
        roots, functions = GROUPS[group]
        assert all(f in self.available for f in functions), set(functions)-self.available
        meta = {'group': group, 'roots': roots, 'functions': functions,
                'requested_seconds': seconds, 'depth': 12, 'sleep_time': True}
        try:
            self.put('tracing_on', 0)
            self.put('set_ftrace_filter', '\n'.join(functions))
            self.put('set_graph_function', '\n'.join(roots))
            self.put('max_graph_depth', 12)
            self.put('buffer_size_kb', 8192)
            self.put('trace_clock', 'mono')
            self.put('current_tracer', 'function_graph')
            for key in ('options/funcgraph-abstime', 'options/funcgraph-proc',
                        'options/funcgraph-duration', 'options/sleep-time'):
                self.put(key, 1)
            self.put('trace', '')
            meta['before_enable_monotonic'] = time.monotonic()
            self.put('tracing_on', 1)
            meta['after_enable_monotonic'] = time.monotonic()
            time.sleep(seconds)
            meta['before_disable_monotonic'] = time.monotonic()
            self.put('tracing_on', 0)
            meta['after_disable_monotonic'] = time.monotonic()
            (dest/(group+'.trace')).write_text((TRACE/'trace').read_text())
            meta['cpu_stats'] = {p.parent.name: p.read_text() for p in (TRACE/'per_cpu').glob('cpu*/stats')}
            save(dest/(group+'.capture.json'), meta)
        finally:
            self.restore()
        return meta


def stop_handler(*_):
    raise KeyboardInterrupt('requested stop')


def matrix(out, state, trace):
    endpoint = Path('/sys/bus/pci/devices/0003:31:00.0')
    port = '0003:30:00.0'
    original = command('setpci', '-s', port, 'CAP_EXP+30.w')
    assert int(original, 16) & 15 == 3
    assert (endpoint/'current_link_speed').read_text().startswith('8.0')
    assert (endpoint/'current_link_width').read_text().strip() == '1'
    smi = command('bm-smi', '--noloop', '--text_format', '--start_dev=2', '--last_dev=2')
    assert '003:31:00.0' in smi
    source = ROOT/'data/results/same-card-link32-20260923/b0'
    reference = json.loads((source/'preflight.json').read_text())
    binary = ROOT/'demos/hdmi_wall/build-async/hdmi_wall.pcie'
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == reference['diagnostic_binary_sha256']
    assets = []
    for a in reference['artifacts']:
        path = Path(a['path'])
        if path.suffix in ('.mp4', '.bmodel'):
            h = hashlib.sha256()
            with path.open('rb') as f:
                for chunk in iter(lambda: f.read(4*1024*1024), b''):
                    h.update(chunk)
            assert h.hexdigest() == a['sha256'], str(path)
            assets.append(a)
    save(out/'config.json', {'device': 2, 'endpoint': endpoint.name, 'port': port,
         'original_target': original, 'binary_sha256': reference['diagnostic_binary_sha256'],
         'assets': assets, 'speeds': [8, 5, 5, 8], 'warmup_s': 20, 'duration_s': 60,
         'trace_windows_after_first_inference_s': {'vpp': 40, 'dma': 50, 'api': 60, 'mmio': 70},
         'scope': 'short intrusive attribution windows; not replacement performance benchmark'})
    state['original_target'] = original

    def speed(target):
        assert not conflicts(), conflicts()
        command('setpci', '-s', port, 'CAP_EXP+30.w='+target+':000f')
        command('setpci', '-s', port, 'CAP_EXP+10.w=0020:0020')
        time.sleep(2)
        actual = (endpoint/'current_link_speed').read_text().strip()
        assert (endpoint/'current_link_width').read_text().strip() == '1'
        assert actual.startswith('8.0' if int(target, 16) & 15 == 3 else '5.0'), actual
        return actual

    try:
        for batch, target in enumerate(['0003','0002','0002','0003']):
            actual = speed(target)
            modes = ['none','sync10'] if batch < 2 else ['sync10','none']
            for mode in modes:
                name = f'b{batch}_{mode}'
                state.update(batch=batch, mode=mode, actual_speed=actual, stage=name)
                save(out/'state.json', state)
                cmd = json.loads((source/f'r0_{mode}/command.json').read_text())
                for key, value in [('--warmup','20'),('--duration','60'),('--output','{stage}/worker'),
                                   ('--readback-control','{stage}/readback.json')]:
                    cmd[cmd.index(key)+1] = value
                stop = threading.Event()
                errors = []
                folder = out/name

                def watch():
                    try:
                        while not folder.exists():
                            if stop.wait(.1): return
                        anchor = None
                        done = set()
                        with (folder/'status-samples.jsonl').open('w') as output:
                            while not stop.is_set():
                                now = time.monotonic()
                                try:
                                    status = json.loads((folder/'worker/status.json').read_text())
                                    output.write(json.dumps({'monotonic': now, 'status': status})+'\n')
                                    output.flush()
                                    if anchor is None and sum(s['inferred_count'] for s in status['streams']) > 32:
                                        anchor = now
                                        save(folder/'trace-anchor.json', {'monotonic': anchor,
                                             'note':'first sampled inferred_count > 32; compare exact formal clock after run'})
                                except (OSError, ValueError, KeyError):
                                    pass
                                if anchor is not None:
                                    for group, offset in [('vpp',40),('dma',50),('api',60),('mmio',70)]:
                                        if group not in done and now >= anchor+offset:
                                            trace.run(folder, group, .3 if group == 'mmio' else 2)
                                            done.add(group)
                                stop.wait(.5)
                        if len(done) != 4:
                            raise RuntimeError(f'Incomplete capture groups: {done}')
                    except BaseException as exc:
                        errors.append(repr(exc))

                watcher = threading.Thread(target=watch)
                watcher.start()
                try:
                    run_stage(out, name, cmd, 2, timeout=180, readback=True)
                finally:
                    stop.set()
                    watcher.join()
                if errors:
                    raise RuntimeError(errors)
                summary = json.loads((folder/'worker/summary.json').read_text())
                assert summary['accounting_complete'] and not summary['error']
                for group in GROUPS:
                    cap = json.loads((folder/(group+'.capture.json')).read_text())
                    assert cap['before_enable_monotonic'] >= summary['measurement_start_monotonic_s']
                    assert cap['after_disable_monotonic'] < summary['measurement_end_monotonic_s']
                    for stats in cap['cpu_stats'].values():
                        for line in stats.splitlines():
                            if line.startswith(('overrun:', 'commit overrun:', 'dropped events:')):
                                assert int(line.split(':')[1]) == 0, line
                print('completed', name, flush=True)
    finally:
        state['restored_speed'] = speed(original)
        save(out/'state.json', state)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--smoke', action='store_true')
    args = p.parse_args()
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    assert not conflicts(), conflicts()
    trace = Capture()
    args.output.mkdir(parents=True, exist_ok=False)
    state = {'status': 'running', 'pid': __import__('os').getpid(), 'trace_original': trace.old}
    save(args.output/'state.json', state)
    try:
        if not args.smoke:
            matrix(args.output, state, trace)
        else:
            binary = ROOT/'tools/diagnostics/build-vpp/vpp_fixed_probe.pcie'
            save(args.output/'config.json', {'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                'device': 2, 'threads': 2, 'seconds': 5, 'groups': ['vpp', 'mmio']})
            with (args.output/'probe.log').open('w') as log:
                worker = subprocess.Popen([str(binary), '2', '2', '5', str(args.output/'probe.json')],
                                          stdout=log, stderr=subprocess.STDOUT)
                try:
                    time.sleep(4)
                    assert worker.poll() is None, 'Probe exited before tracing'
                    trace.run(args.output, 'vpp', .25)
                    time.sleep(.5)
                    trace.run(args.output, 'mmio', .25)
                    assert worker.wait(timeout=30) == 0
                finally:
                    if worker.poll() is None:
                        worker.terminate()
                        worker.wait(timeout=10)
        state['status'] = 'completed'
    except BaseException as exc:
        state.update(status='failed', error=repr(exc))
        raise
    finally:
        trace.restore()
        state['trace_restored'] = {k: (TRACE/k).read_text().strip() for k in ('current_tracer','tracing_on','set_ftrace_filter','set_graph_function')}
        save(args.output/'state.json', state)


if __name__ == '__main__':
    main()
