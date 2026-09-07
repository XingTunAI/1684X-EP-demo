#!/usr/bin/env python3
"""Separate resident-tensor compute, output copy and their combination. No video FPS."""
import argparse
import csv
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def positive(value):
    number = float(value)
    if not 0 < number < 3600:
        raise argparse.ArgumentTypeError('Expected seconds between 0 and 3600')
    return number


def steps(value):
    try:
        values = [int(x) for x in value.split(',')]
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Expected comma-separated integers') from exc
    if not values or values != sorted(set(values)) or values[0] < 1:
        raise argparse.ArgumentTypeError('Steps must be positive, unique and increasing')
    return values


def save(directory, results, state):
    config_file = directory / 'config.json'
    config = json.loads(config_file.read_text(encoding='utf-8')) if config_file.exists() else {}
    (directory / 'summary.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
    (directory / 'run_state.json').write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
    fields = ['mode', 'processes', 'total_iterations_per_second', 'read_MB_per_second',
              'submit_mean_ms', 'sync_mean_ms', 'copy_mean_ms', 'outputs_ok']
    rows = []
    for result in results:
        workers = result['workers']
        row = {k: result[k] for k in ('mode', 'processes', 'total_iterations_per_second', 'read_MB_per_second', 'outputs_ok')}
        for key in ('submit_mean_ms', 'sync_mean_ms', 'copy_mean_ms'):
            values = [w[key] for w in workers if w[key] is not None]
            row[key] = sum(values) / len(values) if values else None
        rows.append(row)
    with (directory / 'stages.csv').open('w', newline='', encoding='utf-8-sig') as out:
        writer = csv.DictWriter(out, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    lines = ['# 推理与输出读取隔离测试', '', f'状态：{state["status"]}', '',
             f'输出读取分块：{config.get("copy_chunk_bytes", 0)} 字节（0 为完整张量）；主机 CPU 亲和性：{config.get("host_cpu_affinity", "未记录")}。', '',
             '输入为驻留显存的全零张量，复用输入、输出缓冲。没有视频解码、预处理、NMS 或逐帧输入上传。',
             '`compute` 仅提交模型并同步；`copy` 仅重复回传输出；`compute-copy` 每次执行模型、同步并回传。',
             '结果是诊断循环次数/秒，不代表视频分析 FPS，也不代表物理 PCIe 带宽上限。', '',
             '| 模式 | 进程数 | 总循环/秒 | 回传 MB/s | 提交 ms | 同步 ms | 回传 ms | 输出核对 |',
             '|---|---:|---:|---:|---:|---:|---:|---|']
    def fmt(value):
        return '未测' if value is None else f'{value:.2f}'
    for row in rows:
        lines.append('| ' + ' | '.join([row['mode'], str(row['processes'])] +
            [fmt(row[k]) for k in fields[2:-1]] + ['一致' if row['outputs_ok'] else '异常']) + ' |')
    lines += ['', '各进程就绪后通过单调时钟屏障同时开始；每进程按实际测量时长计算吞吐，再求和。',
              '完成最后一次循环的超时计入测量时长。阶段耗时为各进程均值等权平均。',
              '输出核对为固定输入下首末输出逐字节比较，不是检测精度测试。原始结果包含时间区间。',
              '请在目标卡没有其他工作负载时运行。结果保存在 summary.json、stages.csv 和逐进程日志中。']
    if state.get('error'):
        lines += ['', '错误：' + state['error']]
    (directory / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def run_stage(a, directory, mode, count):
    step = directory / f'{mode}_{count:02d}'
    step.mkdir()
    gate = step / 'start.txt'
    processes, logs = [], []
    monitor_stop, monitor_thread = threading.Event(), None
    tpu_samples = []
    def monitor():
        with (step / 'monitor.jsonl').open('w', encoding='utf-8') as file:
            while not monitor_stop.is_set():
                row = {'monotonic_s': time.monotonic()}
                try:
                    proc = subprocess.run(['bm-smi', '--text_format', '--noloop',
                        f'--start_dev={a.device}', f'--last_dev={a.device}'],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=5)
                    row.update({'returncode': proc.returncode, 'bm_smi': proc.stdout})
                    values = re.findall(r'(\d+)%', proc.stdout)
                    if proc.returncode == 0 and len(values) == 1:
                        tpu_samples.append(int(values[0]))
                except Exception as exc:
                    row['error'] = str(exc)
                file.write(json.dumps(row) + '\n')
                file.flush()
                monitor_stop.wait(1)
    try:
        for i in range(count):
            log = (step / f'worker_{i:02d}.log').open('w')
            logs.append(log)
            processes.append(subprocess.Popen([str(a.app), str(a.device), str(a.bmodel), mode,
                str(a.warmup), str(a.duration), str(gate), str(step / f'worker_{i:02d}.json')] +
                ([str(a.copy_chunk_bytes)] if getattr(a, 'copy_chunk_bytes', 0) else []),
                stdout=log, stderr=subprocess.STDOUT))
        deadline = time.monotonic() + 100
        while len(list(step.glob('*.ready'))) != count:
            if any(p.poll() is not None for p in processes):
                raise RuntimeError('Worker exited before start barrier; inspect logs')
            if time.monotonic() >= deadline:
                raise RuntimeError('Ready barrier timeout')
            time.sleep(0.05)
        monitor_thread = threading.Thread(target=monitor, daemon=True)
        monitor_thread.start()
        temporary = step / 'start.tmp'
        temporary.write_text(str(time.monotonic() + 1), encoding='ascii')
        temporary.replace(gate)
        deadline = time.monotonic() + a.warmup + a.duration + 60
        while any(p.poll() is None for p in processes):
            if any(p.poll() not in (None, 0) for p in processes):
                raise RuntimeError('Worker failed; inspect logs')
            if time.monotonic() >= deadline:
                raise RuntimeError('Worker timeout')
            time.sleep(0.1)
        if any(p.returncode != 0 for p in processes):
            raise RuntimeError('Worker failed; inspect logs')
        workers = [json.loads((step / f'worker_{i:02d}.json').read_text()) for i in range(count)]
        if not all(w['output_matches_reference'] and w['iterations'] > 0 and w['mode'] == mode for w in workers):
            raise RuntimeError('Invalid worker result')
        if not all(w.get('copy_chunk_bytes', 0) == getattr(a, 'copy_chunk_bytes', 0) for w in workers):
            raise RuntimeError('Worker copy policy does not match requested policy')
        monitor_stop.set()
        monitor_thread.join()
        return {'mode': mode, 'processes': count, 'workers': workers, 'outputs_ok': True,
                'copy_chunk_bytes': getattr(a, 'copy_chunk_bytes', 0),
                'tpu_samples_percent': tpu_samples,
                'exit_codes': [p.returncode for p in processes],
                'total_iterations_per_second': sum(w['iterations_per_second'] for w in workers),
                'read_MB_per_second': None if mode == 'compute' else
                    sum(w['iterations_per_second'] * w['output_bytes'] / 1e6 for w in workers)}
    finally:
        monitor_stop.set()
        for p in processes:
            if p.poll() is None:
                p.terminate()
        for p in processes:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        for log in logs:
            log.close()
        if monitor_thread is not None:
            monitor_thread.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', type=int, default=1)
    parser.add_argument('--steps', type=steps, default=[1, 2, 4, 8])
    parser.add_argument('--modes', nargs='+', choices=('compute', 'copy', 'compute-copy'),
                        default=['compute', 'copy', 'compute-copy'])
    parser.add_argument('--warmup', type=positive, default=3)
    parser.add_argument('--duration', type=positive, default=10)
    parser.add_argument('--copy-chunk-bytes', type=int, default=0, help='Experimental output read chunk size; 0 reads the full tensor')
    parser.add_argument('--app', type=Path, default=ROOT / 'src/single_card_pipeline/build/inference_probe.pcie')
    parser.add_argument('--bmodel', type=Path, default=ROOT / 'third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel')
    a = parser.parse_args()
    if a.device < 0 or not 0 <= a.copy_chunk_bytes <= 64*1024**2 or not a.app.is_file() or not a.bmodel.is_file():
        parser.error('Check device, compiled probe and model')
    a.app, a.bmodel = a.app.resolve(), a.bmodel.resolve()
    directory = ROOT / 'results/inference-diagnostics' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    directory.mkdir(parents=True)
    metadata = {k: str(v) if isinstance(v, Path) else v for k, v in vars(a).items()}
    metadata['host_cpu_affinity'] = sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None
    for key in ('app', 'bmodel'):
        metadata[key + '_sha256'] = hashlib.sha256(getattr(a, key).read_bytes()).hexdigest()
    (directory / 'config.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    print('Results: ' + str(directory), flush=True)
    results, state = [], {'status': 'running'}
    def interrupt(*_):
        raise KeyboardInterrupt()
    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        save(directory, results, state)
        for mode in a.modes:
            for count in a.steps:
                print(f'{mode}: {count} processes', flush=True)
                results.append(run_stage(a, directory, mode, count))
                save(directory, results, state)
        state = {'status': 'completed'}
        return 0
    except KeyboardInterrupt:
        state = {'status': 'incomplete', 'error': 'Interrupted'}
        return 130
    except Exception as exc:
        state = {'status': 'failed', 'error': str(exc)}
        print(str(exc), flush=True)
        return 1
    finally:
        save(directory, results, state)
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    raise SystemExit(main())
