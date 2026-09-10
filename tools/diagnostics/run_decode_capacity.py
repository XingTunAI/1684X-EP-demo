#!/usr/bin/env python3
"""Compare independent hardware decoding with and without resident model load.

Runs one card at a time, no HDMI/VPP/NMS. Throughput is explicitly unpaced;
it does not certify camera latency or the full video-analysis pipeline.
"""
import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('capacity_decode', ROOT / 'demos/decode/stress_decode.py')
decode = importlib.util.module_from_spec(spec)
spec.loader.exec_module(decode)


def summarize(result, samples, loaded, threshold=95):
    windows = result.get('windows', [])
    seconds = sum(w['seconds'] for w in windows)
    total = sum(sum(w['fps']) * w['seconds'] for w in windows) / seconds if seconds else None
    begin, end = result.get('measure_start_monotonic'), result.get('measure_end_monotonic')
    formal = [r for r in samples if begin is not None and end is not None and begin <= r['monotonic_s'] < end]
    valid = [r['tpu_percent'] for r in formal if not r.get('error') and isinstance(r.get('tpu_percent'), (float, int))
             and not isinstance(r.get('tpu_percent'), bool) and math.isfinite(r['tpu_percent']) and 0 <= r['tpu_percent'] <= 100]
    mean = sum(valid) / len(valid) if valid else None
    fraction = sum(v >= threshold for v in valid) / len(valid) if valid else None
    return {'decode_total_fps': total, 'minimum_stream_window_fps': result.get('min_stream_window_fps'),
            'formal_seconds': seconds, 'tpu_mean_percent': mean, 'tpu_min_percent': min(valid) if valid else None,
            'tpu_max_percent': max(valid) if valid else None, 'tpu_samples': len(formal), 'tpu_valid_samples': len(valid),
            'tpu_read_failures': len(formal) - len(valid), 'fraction_tpu_at_least_threshold': fraction,
            'full_load_threshold_percent': threshold,
            'full_load_observed': bool(loaded and len(valid) >= 3 and len(valid) == len(formal) and fraction >= .9),
            'status': result['status'], 'reason': result.get('reason')}


def save(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temp.replace(path)


def write_report(directory, rows, throughput):
    def fmt(value):
        return '--' if value is None else f'{value:.2f}'
    lines = ['# 解码与独立 TPU 负载对照', '',
             '模式：' + ('本地不限速解码吞吐' if throughput else '按本地源时间戳节流'), '',
             '推理使用驻留张量上的真实模型计算，不使用解码帧作为输入；没有 HDMI、预览、预处理或 NMS。',
             'TPU 只统计解码正式区间内的查询；缺失、异常采样保留，不算作 0%。', '',
             '| 路数 | 组别 | 总解码 FPS | 最低逐路窗口 FPS | TPU 平均 % | 有效/全部采样 | 满载条件 | 状态 |',
             '|---:|---|---:|---:|---:|---|---|---|']
    for r in rows:
        load = ('满足' if r['full_load_observed'] else '未满足') if r['loaded'] else '不适用'
        lines.append(f"| {r['streams']} | {'推理负载' if r['loaded'] else '纯解码'} | "
                     f"{fmt(r['decode_total_fps'])} | {fmt(r['minimum_stream_window_fps'])} | "
                     f"{fmt(r['tpu_mean_percent'])} | {r['tpu_valid_samples']}/{r['tpu_samples']} | {load} | {r['status']} |")
    lines += ['', '满载条件：正式区间至少 3 个有效采样、无失败采样，且至少 90% 样本达到配置阈值（默认 95%）。',
              '这不是每个瞬间的连续利用率保证。扫描最大值仅代表已测路数范围，不自动认定硬件最大容量。',
              '逐路进度、窗口和错误见各组 decode/；正式区间与原始 TPU 采样见 telemetry.jsonl。',
              'FFmpeg 进度更新具有采样粒度；本工具不输出源帧年龄或相机到显示器的端到端延时。']
    (directory/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def stop_children(processes):
    for p in processes:
        if p.poll() is None:
            p.terminate()
    for p in processes:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=5)


def stage(a, count, loaded, directory, interrupted):
    directory.mkdir()
    processes, logs, samples = [], [], []
    monitor_stop = threading.Event()
    load_error = []
    def monitor():
        with (directory / 'telemetry.jsonl').open('w', encoding='utf-8') as f:
            while not monitor_stop.is_set():
                row = {'monotonic_s': time.monotonic(), 'tpu_percent': None}
                try:
                    p = subprocess.run([a.smi, '--text_format', '--noloop', f'--start_dev={a.device}',
                                        f'--last_dev={a.device}'], capture_output=True, text=True, timeout=3)
                    values = re.findall(r'(\d+)%', p.stdout)
                    row.update(returncode=p.returncode, raw=p.stdout, loadavg=Path('/proc/loadavg').read_text())
                    if p.returncode or len(values) != 1:
                        raise RuntimeError('Invalid bm-smi result')
                    row['tpu_percent'] = int(values[0])
                except Exception as exc:
                    row['error'] = str(exc)
                samples.append(row)
                f.write(json.dumps(row) + '\n'); f.flush()
                if loaded and any(p.poll() is not None for p in processes):
                    load_error.append('Model worker exited before decoder measurement ended')
                    interrupted.set()
                monitor_stop.wait(1)
    thread = None
    result = {'status': 'error', 'reason': 'Stage did not start', 'windows': []}
    try:
        if loaded:
            gate = directory / 'load-start.txt'
            commands = []
            for i in range(a.load_processes):
                logfile = (directory / f'load_{i}.log').open('w')
                logs.append(logfile)
                cmd = [str(a.probe), str(a.device), str(a.bmodel), 'compute', '0',
                       str(a.warmup + a.duration + 180), str(gate), str(directory / f'load_{i}.json')]
                commands.append(cmd)
                processes.append(subprocess.Popen(cmd, stdout=logfile, stderr=subprocess.STDOUT))
            save(directory / 'load.json', {'commands': commands, 'pids': [p.pid for p in processes],
                 'input_kind': 'resident_zero_tensor', 'stop_policy': 'terminate exact children after decode measurement; no final probe throughput claimed'})
            deadline = time.monotonic() + 60
            while len(list(directory.glob('load_*.json.ready'))) != a.load_processes:
                if interrupted.wait(.05):
                    raise InterruptedError('Interrupted during model initialization')
                if time.monotonic() >= deadline or any(p.poll() is not None for p in processes):
                    raise RuntimeError('Model worker readiness failed')
            gate.write_text(str(time.monotonic() + .5), encoding='ascii')
            if interrupted.wait(3):
                raise InterruptedError('Interrupted during model warmup')
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        result = decode.run_step(a, count, directory / 'decode', interrupted)
        monitor_stop.set(); thread.join(timeout=5)
        if load_error:
            result.update(status='error', reason=load_error[0])
        summary = summarize(result, samples, loaded, a.full_load_threshold)
        summary.update(device=a.device, streams=count, loaded=loaded, throughput=a.throughput,
                       load_processes=a.load_processes if loaded else 0, output=str(directory))
        save(directory / 'summary.json', summary)
        return summary
    finally:
        monitor_stop.set()
        if thread is not None:
            thread.join(timeout=5)
        stop_children(processes)
        for f in logs:
            f.close()
        save(directory / 'load-cleanup.json', {'returncodes': [p.returncode for p in processes],
              'intentional_stop': True, 'decode_status': result['status']})


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device', type=int, required=True)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--steps', default='8,16,32')
    p.add_argument('--duration', type=decode.positive, default=30)
    p.add_argument('--warmup', type=decode.nonnegative, default=10)
    p.add_argument('--window', type=decode.positive, default=10)
    p.add_argument('--target-fps', type=decode.positive, default=24)
    p.add_argument('--throughput', action='store_true', help='Unpaced offline throughput; otherwise source-paced')
    p.add_argument('--load-processes', type=int, default=2)
    p.add_argument('--full-load-threshold', type=decode.positive, default=95)
    p.add_argument('--groups', choices=['both','baseline','loaded'], default='both')
    p.add_argument('--ffmpeg', default='/opt/sophon/sophon-ffmpeg_0.14.0/bin/ffmpeg')
    p.add_argument('--ffprobe', default='/opt/sophon/sophon-ffmpeg_0.14.0/bin/ffprobe')
    p.add_argument('--smi', default='/opt/sophon/libsophon-current/bin/bm-smi')
    p.add_argument('--probe', type=Path, default=ROOT/'tools/diagnostics/build/inference_probe.pcie')
    p.add_argument('--bmodel', type=Path, default=ROOT/'third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel')
    p.add_argument('--output', type=Path, default=ROOT/'data/results/decode-capacity')
    a=p.parse_args(argv)
    if not 1 <= a.load_processes <= 8 or not 0 < a.full_load_threshold <= 100:
        p.error('load-processes must be 1..8 and threshold in (0,100]')
    options=['--device',str(a.device),'--input',str(a.input),'--steps',a.steps,'--duration',str(a.duration),
             '--warmup',str(a.warmup),'--window',str(a.window),'--target-fps',str(a.target_fps),
             '--min-fps',str(a.target_fps),'--ffmpeg',a.ffmpeg,'--ffprobe',a.ffprobe,'--measure-only']
    if a.throughput: options.append('--throughput')
    d=decode.arguments(options)
    if max(d.steps)>64: p.error('This scan is bounded to at most 64 decoder processes')
    for key in ['steps','sources','codec','stall_timeout','measure_only','min_fps']:
        setattr(a,key,getattr(d,key))
    if a.groups!='baseline' and (not a.probe.is_file() or not a.bmodel.is_file()):
        p.error('Build inference_probe and prepare the model first')
    interrupted=threading.Event()
    previous={s:signal.signal(s,lambda *_:interrupted.set()) for s in [signal.SIGINT,signal.SIGTERM]}
    directory=a.output.resolve()/(datetime.now().strftime('%Y%m%d_%H%M%S')+f'_{os.getpid()}')
    directory.mkdir(parents=True)
    rows=[]
    state={'status':'running'}
    save(directory/'run_state.json',state)
    print('Results:',directory,flush=True)
    try:
        metadata=decode.preflight(a)
        config={k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()}
        config['metadata']=metadata
        config['source_size_bytes']=a.input.stat().st_size
        config['bmodel_sha256']=hashlib.sha256(a.bmodel.read_bytes()).hexdigest() if a.bmodel.exists() else None
        config['probe_sha256']=hashlib.sha256(a.probe.read_bytes()).hexdigest() if a.probe.exists() else None
        config['note']='No HDMI, preview, preprocessing or NMS. Resident model load is independent of decoded frames. No camera latency measured.'
        save(directory/'config.json',config)
        for count in a.steps:
            for loaded in ([False,True] if a.groups=='both' else [a.groups=='loaded']):
                label=f'{count:02d}_'+('loaded' if loaded else 'baseline')
                print('START',a.device,label,flush=True)
                row=stage(a,count,loaded,directory/label,interrupted)
                rows.append(row);save(directory/'summary.json',rows)
                write_report(directory,rows,a.throughput)
                print('RESULT',json.dumps(row),flush=True)
                if interrupted.is_set() or row['status']!='measured':
                    raise RuntimeError('Stopped at incomplete/error stage; inspect its logs')
        state['status']='completed'
        return 0
    except Exception as exc:
        state.update(status='interrupted' if interrupted.is_set() else 'error',error=str(exc))
        print('CAPACITY_ERROR',str(exc),flush=True)
        return 1
    finally:
        save(directory/'run_state.json',state)
        for s,handler in previous.items(): signal.signal(s,handler)


if __name__=='__main__':
    raise SystemExit(main())
