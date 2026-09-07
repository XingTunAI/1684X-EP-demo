#!/usr/bin/env python3
"""Measure SDK transfer bandwidth with the installed, allocating CDMA test."""
import argparse
import csv
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def parse_metrics(output, size, device):
    if not re.search(rf'dev = {device}, cdma transfer test Success\.', output):
        raise ValueError('CDMA data comparison did not succeed')
    rows = []
    pattern = (r'(S2D|D2S) (sys|real):Transfer size:0x([0-9a-fA-F]+) byte\. '
               r'Cost time:(\d+) us, Write Bandwidth:([\d.]+) MB/s')
    for direction, scope, hex_size, usec, mib in re.findall(pattern, output):
        if int(hex_size, 16) != size or int(usec) <= 0 or float(mib) <= 0:
            raise ValueError('Invalid transfer size or timing')
        # The upstream tool divides by 1024**2 despite printing "MB/s".
        rows.append({'direction': direction, 'scope': scope, 'bytes': size,
                     'mean_microseconds': int(usec), 'reported_MiB_s': float(mib),
                     'MB_s': float(mib) * 1024**2 / 1e6})
    if len(rows) != 4 or {(r['direction'], r['scope']) for r in rows} != {
            ('S2D', 'sys'), ('S2D', 'real'), ('D2S', 'sys'), ('D2S', 'real')}:
        raise ValueError('Missing or duplicate transfer metrics')
    return rows


def snapshot(device):
    data = {'timestamp': datetime.now().isoformat(), 'links': {},
            'host_cpu_affinity': sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None}
    for path in Path('/sys/bus/pci/devices').glob('*'):
        if (path / 'current_link_width').exists():
            data['links'][path.name] = {k: (path / k).read_text().strip()
                                      for k in ('current_link_width', 'current_link_speed')}
    for name, cmd in [('smi', ['bm-smi', '--text_format', '--noloop',
                             f'--start_dev={device}', f'--last_dev={device}']),
                      ('pci', ['lspci', '-nn'])]:
        try:
            p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=10)
            data[name] = {'exit_code': p.returncode, 'output': p.stdout}
        except (OSError, subprocess.TimeoutExpired) as exc:
            data[name] = {'error': str(exc)}
    return data


def save(directory, state, records):
    (directory / 'summary.json').write_text(json.dumps(
        {'state': state, 'records': records}, indent=2) + '\n', encoding='utf-8')
    fields = ['repeat', 'direction', 'scope', 'bytes', 'mean_microseconds', 'reported_MiB_s', 'MB_s']
    with (directory / 'bandwidth.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        for record in records:
            writer.writerows(dict(row, repeat=record['repeat']) for row in record.get('metrics', []))
    lines = ['# PCIe SDK 数据传输测试', '', '状态：' + state['status'], '',
             'sys 为官方测试的应用侧调用计时；real 为 SDK profile 的 CDMA 阶段计时。',
             '两者都不等同于 PCIe 理论带宽。官方工具每个方向循环 10 次，每轮核对回传数据。',
             '原工具标签写 MB/s，实际使用 MiB/s；下表换算为十进制 MB/s。', '',
             '| 字节/次 | 重复 | 方向 | 范围 | 平均微秒 | MB/s |',
             '|---:|---:|---|---|---:|---:|']
    for record in records:
        for m in record.get('metrics', []):
            lines.append(f"| {m['bytes']} | {record['repeat']} | {m['direction']} | {m['scope']} | {m['mean_microseconds']} | {m['MB_s']:.2f} |")
    if state.get('error'):
        lines += ['', '错误：' + state['error']]
    lines += ['', '原始输出及命令保存在 summary.json；设备枚举和链路快照在 before.json、after.json。',
              '此测试没有解码或推理，不能换算为已验证的视频分析 FPS。']
    (directory / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', type=int, required=True, help='Current bm-smi software index')
    parser.add_argument('--sizes', default='262144,2822400,16777216')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--app', type=Path, default=Path('/opt/sophon/libsophon-0.5.1/bin/test_cdma_perf'))
    a = parser.parse_args()
    try:
        sizes = [int(x) for x in a.sizes.split(',')]
        if not sizes or any(x <= 0 or x > 64*1024**2 or x % 4 for x in sizes):
            raise ValueError()
    except ValueError:
        parser.error('Sizes must be positive multiples of 4, at most 64 MiB')
    if a.device < 0 or not 1 <= a.repeats <= 20 or not a.app.is_file():
        parser.error('Check device, repeats (1..20), and installed CDMA test')
    directory = ROOT / 'results/bandwidth' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    directory.mkdir(parents=True)
    print('Results: ' + str(directory), flush=True)
    config = {'device': a.device, 'sizes': sizes, 'repeats': a.repeats,
              'app': str(a.app), 'app_sha256': hashlib.sha256(a.app.read_bytes()).hexdigest()}
    (directory / 'config.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
    (directory / 'before.json').write_text(json.dumps(snapshot(a.device), indent=2), encoding='utf-8')
    state, records = {'status': 'running'}, []
    try:
        for size in sizes:
            for repeat in range(1, a.repeats + 1):
                # Zero address tells the official test to allocate its own device memory.
                cmd = [str(a.app), 'chip', str(a.device), format(size, 'x'), '0']
                p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, timeout=60)
                record = {'command': cmd, 'repeat': repeat, 'exit_code': p.returncode, 'output': p.stdout}
                records.append(record)
                if p.returncode:
                    raise RuntimeError('CDMA tool failed; inspect raw output')
                record['metrics'] = parse_metrics(p.stdout, size, a.device)
                save(directory, state, records)
        state['status'] = 'completed'
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        state.update(status='incomplete' if isinstance(exc, KeyboardInterrupt) else 'failed', error=str(exc))
        return 1
    finally:
        save(directory, state, records)
        (directory / 'after.json').write_text(json.dumps(snapshot(a.device), indent=2), encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
