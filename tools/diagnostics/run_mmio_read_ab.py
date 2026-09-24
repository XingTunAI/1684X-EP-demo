#!/usr/bin/env python3
"""Same-card timer-read ioctl study; independent of the video workload.

Requires the audited native 64-bit mmio_read_probe.pcie. Probe only reads a
fixed timer register. This supervisor temporarily changes link target speed,
and optionally enables bounded function-graph tracing, restoring both.
"""
import argparse
import hashlib
import json
import signal
import subprocess
import threading
import time
from pathlib import Path

from run_dispatch_wait_ab import Capture, GROUPS, TRACE, command, conflicts, stop_handler

ROOT = Path('/userdata/1684X-EP-demo')
ENDPOINT = Path('/sys/bus/pci/devices/0003:31:00.0')
PORT = '0003:30:00.0'


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def context():
    policies = {}
    for policy in Path('/sys/devices/system/cpu/cpufreq').glob('policy*'):
        policies[policy.name] = {k: (policy/k).read_text().strip() for k in
            ('affected_cpus', 'scaling_governor', 'scaling_cur_freq',
             'scaling_min_freq', 'scaling_max_freq') if (policy/k).exists()}
    return {'monotonic_s': time.monotonic(), 'cpufreq': policies,
            'link_speed': (ENDPOINT/'current_link_speed').read_text().strip(),
            'link_width': (ENDPOINT/'current_link_width').read_text().strip()}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    assert not conflicts(), conflicts()
    trace = Capture()
    original = command('setpci', '-s', PORT, 'CAP_EXP+30.w')
    assert int(original, 16) & 15 == 3
    assert context()['link_speed'].startswith('8.0')
    smi = command('bm-smi', '--noloop', '--text_format', '--start_dev=2', '--last_dev=2')
    assert '003:31:00.0' in smi, 'Device 2 does not map to the expected endpoint'
    binary = ROOT/'tools/diagnostics/build-mmio/mmio_read_probe.pcie'
    assert binary.is_file()
    args.output.mkdir(parents=True, exist_ok=False)
    state = {'status': 'running', 'original_target': original}
    (args.output/'device-map.txt').write_text(smi+'\n')
    save(args.output/'config.json', {'device': 2, 'endpoint': ENDPOINT.name,
         'rootport': PORT, 'cpu': 6, 'targets': [3,2,2,3],
         'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
         'source_sha256': hashlib.sha256((ROOT/'tools/diagnostics/mmio_read_probe.cpp').read_bytes()).hexdigest(),
         'trace_original': trace.old, 'scope': 'ioctl latency, not TLP wire latency'})
    for bdf, name in [(ENDPOINT.name, 'endpoint'), (PORT, 'rootport')]:
        (args.output/(name+'-lspci.txt')).write_text(command('lspci','-s',bdf,'-vv'))
    GROUPS['timer'] = (['bm_get_reg'], ['bm_get_reg','bm_read32'])

    def speed(target):
        assert not conflicts(), conflicts()
        command('setpci','-s',PORT,'CAP_EXP+30.w='+target+':000f')
        command('setpci','-s',PORT,'CAP_EXP+10.w=0020:0020')
        time.sleep(2)
        c = context()
        assert c['link_width'] == '1'
        assert c['link_speed'].startswith('8.0' if int(target,16)&15 == 3 else '5.0')
        return c

    def probe(dest):
        before = context()
        cmd = [str(binary),'--device','2','--cpu','6','--mode','both',
               '--output',str(dest/'probe.json')]
        save(dest/'command.json', cmd)
        with (dest/'probe.log').open('x') as log:
            result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=15)
        save(dest/'context.json', {'before': before, 'after': context(), 'returncode': result.returncode})
        assert result.returncode == 0
        data = json.loads((dest/'probe.json').read_text())
        assert data['success'] and all(r['complete'] and r['timer_value_changed']
                                      and r['samples_with_cpu_migration']==0 for r in data['runs'])

    try:
        for batch,target in enumerate(['0003','0002','0002','0003']):
            state.update(batch=batch, actual=speed(target))
            save(args.output/'state.json', state)
            for traced in ([False,True] if batch < 2 else [True,False]):
                dest = args.output/('b%d_%s' % (batch, 'traced' if traced else 'plain'))
                dest.mkdir()
                if not traced:
                    probe(dest)
                else:
                    errors = []
                    def capture():
                        try: trace.run(dest,'timer',3)
                        except BaseException as exc: errors.append(repr(exc))
                    worker = threading.Thread(target=capture)
                    worker.start()
                    try:
                        deadline = time.monotonic()+10
                        while not ((TRACE/'current_tracer').read_text().strip()=='function_graph'
                                   and (TRACE/'tracing_on').read_text().strip()=='1'):
                            assert worker.is_alive() and time.monotonic()<deadline, errors
                            time.sleep(.01)
                        probe(dest)
                    finally:
                        worker.join()
                    assert not errors, errors
                    cap = json.loads((dest/'timer.capture.json').read_text())
                    c = json.loads((dest/'context.json').read_text())
                    assert c['before']['monotonic_s'] >= cap['before_enable_monotonic']
                    assert c['after']['monotonic_s'] < cap['before_disable_monotonic']
                    for stats in cap['cpu_stats'].values():
                        for line in stats.splitlines():
                            if line.startswith(('overrun:','commit overrun:','dropped events:')):
                                assert int(line.split(':')[1])==0, line
                print('completed',dest.name,flush=True)
        state['status'] = 'measurements_completed'
    except BaseException as exc:
        state.update(status='failed',error=repr(exc))
        raise
    finally:
        recovery_errors = []
        try:
            state['restored'] = speed(original)
        except BaseException as exc:
            recovery_errors.append('link: '+repr(exc))
        try:
            trace.restore()
            state['trace_restored'] = {k:(TRACE/k).read_text().strip() for k in
                ('current_tracer','tracing_on','set_ftrace_filter','set_graph_function')}
        except BaseException as exc:
            recovery_errors.append('trace: '+repr(exc))
        if recovery_errors:
            state.update(status='failed', recovery_errors=recovery_errors)
        elif state['status'] == 'measurements_completed':
            state['status'] = 'completed'
        save(args.output/'state.json',state)
        if recovery_errors:
            raise RuntimeError(recovery_errors)


if __name__ == '__main__':
    main()
