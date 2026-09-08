#!/usr/bin/env python3
"""Isolated BMRuntime model compute comparison; no video or detection pipeline."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[2]


def write_report(directory):
    results = json.loads((directory / 'summary.json').read_text(encoding='utf-8'))
    lines = ['# 单卡独立模型计算测试', '',
             '仅重复执行模型，输入为工具默认数据；不包含视频解码、逐帧输入输出传输、检测后处理或准确率验证。',
             'TPU 利用率每秒采样，包含模型加载与收尾，不是全程平均利用率。', '',
             '| Batch | 状态 | 计算秒数 | 计算吞吐（图/秒） | TPU 采样范围 |',
             '|---:|---|---:|---:|---|']
    for item in results:
        batch = item['batch']
        log = (directory / f'batch_{batch}.log').read_text(errors='replace')
        times = re.findall(r'calculate\s+time\(s\):\s*([0-9.]+)', log)
        samples = []
        for line in (directory / f'batch_{batch}_monitor.jsonl').read_text().splitlines():
            samples.extend(int(v) for v in re.findall(r'(\d+)%', json.loads(line).get('bm_smi', '')))
        seconds = float(times[0]) if len(times) == 1 else None
        iterations = int(item['command'][item['command'].index('--calculate_times') + 1])
        valid = item['returncode'] == 0 and seconds is not None and seconds > 0
        fps = iterations * batch / seconds if valid else None
        item.update({'valid_compute_measurement': valid, 'calculate_seconds': seconds,
                     'compute_images_per_second': fps, 'tpu_samples_percent': samples})
        lines.append(f"| {batch} | {'测量完成' if valid else '计算计时缺失或运行异常'} | "
                     + (f'{seconds:.6f}' if seconds is not None else '未测') + ' | '
                     + (f'{fps:.2f}' if fps is not None else '未测') + ' | '
                     + (f'{min(samples)}～{max(samples)}%（{len(samples)} 次）' if samples else '未测') + ' |')
    lines += ['', '原始日志、逐秒资源采样、模型哈希和完整命令保存在本目录。计算吞吐不能作为实际视频分析吞吐。']
    (directory / 'summary.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    (directory / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', type=int, default=0)
    parser.add_argument('--calculate-times', type=int, default=5000)
    args = parser.parse_args()
    if args.device < 0 or args.calculate_times < 1:
        parser.error('device must be nonnegative and calculate-times positive')
    directory = ROOT / 'data/results/tpu' / datetime.now().strftime('%Y%m%d_%H%M%S')
    directory.mkdir(parents=True, exist_ok=False)
    print('Results: ' + str(directory), flush=True)
    results = []
    for batch in (1, 4):
        model = ROOT / f'third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_{batch}b.bmodel'
        command = ['bmrt_test', '--bmodel', str(model), '--devid', str(args.device),
                   '--loopnum', '1', '--calculate_times', str(args.calculate_times)]
        metadata = {'batch': batch, 'device': args.device, 'model': model.name,
                    'model_sha256': hashlib.sha256(model.read_bytes()).hexdigest(), 'command': command,
                    'kind': 'isolated_model_compute_no_video'}
        stop = threading.Event()
        def monitor():
            with (directory / f'batch_{batch}_monitor.jsonl').open('w') as out:
                while not stop.is_set():
                    try:
                        smi = subprocess.run(['bm-smi', '--text_format', '--noloop',
                            f'--start_dev={args.device}', f'--last_dev={args.device}'],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=5)
                        out.write(json.dumps({'time': time.time(), 'bm_smi': smi.stdout}) + '\n')
                        out.flush()
                    except Exception as exc:
                        out.write(json.dumps({'time': time.time(), 'error': str(exc)}) + '\n')
                    stop.wait(1)
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        started = time.monotonic()
        print(f'Batch {batch}: isolated compute, {args.calculate_times} internal iterations', flush=True)
        try:
            with (directory / f'batch_{batch}.log').open('w') as log:
                proc = subprocess.run(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT)
            metadata.update({'returncode': proc.returncode, 'wall_seconds': time.monotonic()-started})
        finally:
            stop.set()
            thread.join(timeout=6)
        results.append(metadata)
        (directory / 'summary.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
        write_report(directory)
        print(f'Batch {batch}: exit {proc.returncode}', flush=True)
        if proc.returncode:
            return proc.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
