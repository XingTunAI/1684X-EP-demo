#!/usr/bin/env python3
"""Produce human-readable and CSV reports from saved decode test results."""
import argparse
import csv
from datetime import datetime
import io
import json
from pathlib import Path
import time

LABELS = {'measured': '测量完成', 'fps_pass': '帧率通过', 'fps_fail': '帧率未达标', 'error': '运行异常',
          'incomplete': '未完成', 'pending': '等待执行', 'skipped': '前档未通过，未执行'}


def stage_metrics(result, threshold, measure_only=False):
    count = result['streams']
    windows = result.get('windows', [])
    rows = []
    for index in range(count):
        samples = [(w['seconds'], w['fps'][index]) for w in windows
                   if w.get('seconds', 0) > 0 and len(w.get('fps', [])) == count]
        duration = sum(s for s, _ in samples)
        avg = sum(s * fps for s, fps in samples) / duration if duration else None
        minimum = min((fps for _, fps in samples), default=None)
        rows.append({'streams': count, 'stream_id': index, 'average_fps': avg,
                     'minimum_window_fps': minimum, 'threshold_fps': None if measure_only else threshold,
                     'below_threshold_windows': None if measure_only else sum(fps < threshold for _, fps in samples),
                     'measured_window_seconds': duration,
                     'fps_ok': minimum >= threshold if minimum is not None and not measure_only else None})
    measured = [r for r in rows if r['average_fps'] is not None]
    return {'streams': count, 'status': result.get('status', 'incomplete'),
            'measured_seconds': result.get('measured_seconds', 0),
            'total_average_fps': sum(r['average_fps'] for r in measured) if len(measured) == count else None,
            'minimum_stream_window_fps': min((r['minimum_window_fps'] for r in measured), default=None),
            'below_threshold_streams': sum(r['fps_ok'] is False for r in rows) if measured and not measure_only else None}, rows


def atomic_text(path, content, encoding='utf-8'):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(content, encoding=encoding)
    temp.replace(path)


def save_csv(path, fields, rows):
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    atomic_text(path, buffer.getvalue(), 'utf-8-sig')


def number(value):
    return '未测' if value is None else f'{value:.2f}'


def write_report(directory):
    directory = Path(directory)
    configuration = json.loads((directory / 'config.json').read_text(encoding='utf-8'))
    config = configuration['config']
    measure_only = config.get('measure_only', False)
    summary_path = directory / 'summary.json'
    results = json.loads(summary_path.read_text(encoding='utf-8')) if summary_path.exists() else []
    threshold = float(config.get('min_fps', 24.5))
    plans = config.get('steps', [])
    state_path = directory / 'run_state.json'
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    finished = bool(results) and (results[-1]['status'] not in ('fps_pass', 'measured') or len(results) == len(plans))
    finished = finished or state.get('status') in ('completed', 'failed', 'error', 'incomplete')
    summaries, streams = [], []
    for result in results:
        metrics, details = stage_metrics(result, threshold, measure_only)
        summaries.append(metrics)
        streams.extend(details)
    save_csv(directory / 'stages.csv', ['streams', 'status', 'measured_seconds',
             'total_average_fps', 'minimum_stream_window_fps', 'below_threshold_streams'], summaries)
    save_csv(directory / 'streams.csv', ['streams', 'stream_id', 'average_fps',
             'minimum_window_fps', 'threshold_fps', 'below_threshold_windows',
             'measured_window_seconds', 'fps_ok'], streams)
    mode = '离线不限速解码吞吐' if config.get('throughput') else '本地限速/实时输入解码'
    lines = ['# 单卡自动解码测试报告', '',
             f'- 测试编号：`{directory.name}`',
             f'- 更新时间：{datetime.now().isoformat(timespec="seconds")}',
             f'- 状态：{"本轮未完成" if state.get("status") == "incomplete" else ("本轮已结束" if finished else "运行中或尚未写入最终结果")}',
             f'- 设备：卡 {config.get("device")}；模式：{mode}；每路独立进程。',
             f'- 视频目标：1920×1080，{config.get("codec")}，{config.get("target_fps")} FPS。',
             f'- 档位：{plans}；每档预热 {config.get("warmup")} 秒，采集 {config.get("duration")} 秒。',
             (f'- 性能摸底：不应用 FPS 验收门槛；统计窗口 {config.get("window")} 秒。' if measure_only else
              f'- 门槛：每路每个统计窗口 ≥ {threshold} FPS；窗口目标 {config.get("window")} 秒。'), '',
             '本报告仅说明解码帧率；没有测试算法推理、视频编码、真实丢帧、摄像头到显示器时延或生产稳定容量。', '',
             '## 档位汇总', '',
             '| 路数 | 结论 | 采集秒数 | 总平均 FPS | 最差逐路窗口 FPS | 未达标路数 |',
             '|---:|---|---:|---:|---:|---:|']
    done = {s['streams']: s for s in summaries}
    for count in plans:
        s = done.get(count)
        if s:
            lines.append(f'| {count} | {LABELS.get(s["status"], s["status"])} | {number(s["measured_seconds"])} | {number(s["total_average_fps"])} | {number(s["minimum_stream_window_fps"])} | {s["below_threshold_streams"] if s["below_threshold_streams"] is not None else "未测"} |')
        else:
            label = '本轮结束，未执行' if state.get('status') in ('incomplete', 'error') else LABELS['skipped' if finished else 'pending']
            lines.append(f'| {count} | {label} | — | — | — | — |')
    passed = [s['streams'] for s in summaries if s['status'] == 'fps_pass']
    lines += ['', f'本轮已通过帧率门槛的最大测试档位：{str(max(passed)) + " 路" if passed else "暂无"}。这不是已验证的生产承载容量。', '',
              '## 未达标与异常', '']
    if measure_only:
        lines = [line for line in lines if not line.startswith('本轮已通过帧率门槛')]
        lines = [line.replace('## 未达标与异常', '## 运行异常') for line in lines]
        lines = [line.replace('| 未达标路数 |', '| 帧率判定 |') for line in lines]
        lines = [line.replace('| 未测 |', '| 不判定 |') if line.startswith('|') else line for line in lines]
    issues = []
    if state.get('status') in ('incomplete', 'error'):
        issues.append('- 本轮结束原因：' + str(state.get('reason', '未记录')).replace('\n', ' '))
    for result in results:
        count = result['streams']
        slow = [r for r in streams if r['streams'] == count and r['fps_ok'] is False]
        if slow:
            detail = '，'.join(f'流 {r["stream_id"]:02d}: {r["minimum_window_fps"]:.2f} FPS' for r in slow)
            issues.append(f'- {count} 路：{detail}。')
        if result['status'] not in ('fps_pass', 'fps_fail', 'measured'):
            reason = str(result.get('reason', '无具体原因')).replace('\n', ' ')
            issues.append(f'- {count} 路：{reason}')
    lines.extend(issues or [('尚无已记录的运行异常；测量完成不代表容量验收通过。' if measure_only else '尚无已记录的未达标或异常；待执行档位不代表通过。')])
    lines += ['', '## 输出文件', '',
              '- [档位 CSV](stages.csv)：每档总吞吐、最差逐路窗口、未达标路数。',
              '- [逐路 CSV](streams.csv)：每路加权平均 FPS、最差窗口及低于门槛的窗口数。',
              '- [原始汇总 JSON](summary.json)：保留逐窗口数据和运行状态，首档完成后生成。',
              '- [配置快照](config.json)：测试参数、视频元数据及 FFmpeg 版本。',
              '- `step_XX/samples.csv`、`monitor.jsonl`、`stream_XX/stderr.log`：原始采样、资源监控、错误日志。', '',
              '统计使用实际窗口时长加权；短尾窗口可能与前一窗口合并。' + ('总吞吐与逐路帧率分别记录。' if measure_only else '总 FPS 达标不能抵消个别流不达标。') + '正常收尾时被终止的 FFmpeg 退出码不能直接算运行中异常。',
              'SDK 警告和设备资源趋势仍需结合原始日志分析；没有数据的项标为未测，不填零。', '']
    atomic_text(directory / 'report.md', '\n'.join(lines))
    return finished


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--watch', action='store_true', help='Update every 5 seconds until completed')
    parser.add_argument('--mark-incomplete', help='Record a verified stopped legacy run as incomplete, with a reason')
    args = parser.parse_args()
    if args.mark_incomplete:
        atomic_text(args.directory / 'run_state.json', json.dumps({'status': 'incomplete', 'reason': args.mark_incomplete}, ensure_ascii=False))
    while True:
        try:
            finished = write_report(args.directory)
        except json.JSONDecodeError:
            if not args.watch:
                raise
            finished = False  # Writer may be updating an older run's JSON.
        if not args.watch or finished:
            print(args.directory / 'report.md')
            return
        time.sleep(5)


if __name__ == '__main__':
    main()
