"""Readable reports for decode + analysis and optional encoding benchmarks."""
import csv
import io
import json
from datetime import datetime
from pathlib import Path


def save(path, text):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    tmp.replace(path)


def csv_file(path, fields, rows):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    save(path, '\ufeff' + out.getvalue())


def num(value):
    return '未测' if value is None else f'{value:.2f}'


def write_report(directory, state):
    directory = Path(directory)
    metadata = json.loads((directory / 'config.json').read_text(encoding='utf-8'))
    a, extra = metadata['arguments'], metadata['pipeline_arguments']
    measure_only = extra.get('measure_only', False)
    summary = directory / 'summary.json'
    results = json.loads(summary.read_text(encoding='utf-8')) if summary.exists() else []
    rows, stages, windows = [], [], []
    threshold = a['min_fps']
    for result in results:
        duration = sum(w['seconds'] for w in result['windows'])
        details = {v['stream']: v for v in result.get('outputs', [])}
        stage_rows = []
        for i in range(result['streams']):
            rates = [w['fps'][i] for w in result['windows']]
            detail = details.get(f'stream_{i:02d}', {})
            timing = detail.get('mean_stage_ms', {})
            row = {'streams': result['streams'], 'stream_id': i,
                   'average_fps': sum(w['fps'][i]*w['seconds'] for w in result['windows']) / duration if duration else None,
                   'minimum_window_fps': min(rates) if rates else None,
                   'below_threshold_windows': sum(r < threshold for r in rates) if rates and not measure_only else None,
                   'service_p95_ms': detail.get('service_p95_ms'),
                   'max_schedule_lateness_ms': detail.get('max_schedule_lateness_ms'),
                   'decode_mean_ms': timing.get('decode_ms'), 'analysis_mean_ms': timing.get('analysis_ms'),
                   'image_bridge_mean_ms': timing.get('image_bridge_ms'),
                   'preprocess_mean_ms': timing.get('preprocess_ms'),
                   'inference_mean_ms': timing.get('inference_ms'),
                   'inference_submit_mean_ms': timing.get('inference_submit_ms'),
                   'inference_sync_mean_ms': timing.get('inference_sync_ms'),
                   'input_release_mean_ms': timing.get('input_release_ms'),
                   'postprocess_mean_ms': timing.get('postprocess_ms'),
                   'transfer_wait_mean_ms': timing.get('transfer_wait_ms'),
                   'output_transfer_mean_ms': timing.get('output_transfer_ms'),
                   'output_allocation_mean_ms': timing.get('output_allocation_ms'),
                   'output_copy_mean_ms': timing.get('output_copy_ms'),
                   'cpu_postprocess_mean_ms': timing.get('cpu_postprocess_ms'),
                   'draw_mean_ms': timing.get('draw_ms'), 'encode_submit_mean_ms': timing.get('encode_submit_ms'),
                   'output_ok': detail.get('ok', False)}
            rows.append(row)
            stage_rows.append(row)
        def largest(key):
            values = [r[key] for r in stage_rows if r[key] is not None]
            return max(values) if values else None
        minimum = [r['minimum_window_fps'] for r in stage_rows if r['minimum_window_fps'] is not None]
        stages.append({'streams': result['streams'], 'status': result['status'],
                       'measured_seconds': result['measured_seconds'],
                       'total_average_fps': sum(r['average_fps'] for r in stage_rows) if duration else None,
                       'minimum_stream_window_fps': min(minimum) if minimum else None,
                       'below_threshold_streams': sum(r['below_threshold_windows'] > 0 for r in stage_rows) if duration and not measure_only else None,
                       'worst_service_p95_ms': largest('service_p95_ms'),
                       'max_schedule_lateness_ms': largest('max_schedule_lateness_ms')})
        elapsed = 0
        for i, w in enumerate(result['windows'], 1):
            windows.append({'streams': result['streams'], 'window': i, 'start_seconds': elapsed,
                            'seconds': w['seconds'], 'total_fps': sum(w['fps']),
                            'minimum_stream_fps': min(w['fps']),
                            'below_threshold_streams': sum(f < threshold for f in w['fps']) if not measure_only else None})
            elapsed += w['seconds']
    csv_file(directory / 'stages.csv', ['streams', 'status', 'measured_seconds', 'total_average_fps',
             'minimum_stream_window_fps', 'below_threshold_streams', 'worst_service_p95_ms', 'max_schedule_lateness_ms'], stages)
    csv_file(directory / 'streams.csv', ['streams', 'stream_id', 'average_fps', 'minimum_window_fps',
             'below_threshold_windows', 'service_p95_ms', 'max_schedule_lateness_ms', 'decode_mean_ms',
             'analysis_mean_ms', 'image_bridge_mean_ms', 'preprocess_mean_ms', 'inference_mean_ms',
             'inference_submit_mean_ms', 'inference_sync_mean_ms', 'input_release_mean_ms', 'postprocess_mean_ms', 'transfer_wait_mean_ms', 'output_transfer_mean_ms', 'output_allocation_mean_ms', 'output_copy_mean_ms', 'cpu_postprocess_mean_ms', 'draw_mean_ms', 'encode_submit_mean_ms', 'output_ok'], rows)
    csv_file(directory / 'windows.csv', ['streams', 'window', 'start_seconds', 'seconds', 'total_fps',
             'minimum_stream_fps', 'below_threshold_streams'], windows)
    save(directory / 'run_state.json', json.dumps(state, ensure_ascii=False, indent=2))
    mode = '解码＋推理' if extra['mode'] == 'analysis' else '解码＋推理＋画框＋编码'
    labels = {'pass': '通过', 'measured': '测量完成', 'fail': '未达标', 'error': '运行异常', 'incomplete': '未完成'}
    lines = [f'# 单卡{mode}测试报告', '', f'- 测试编号：`{directory.name}`',
             f'- 更新时间：{datetime.now().isoformat(timespec="seconds")}', f'- 状态：{state["status"]}',
             f'- 软件设备编号：{a["device"]}；每路独立进程和模型实例；batch 1。',
             f'- 模型：`{Path(extra["bmodel"]).name}`；SHA-256：`{metadata["model_sha256"]}`。',
             f'- 输入：`{Path(a["sources"][0]).name}`；1920×1080、{a["target_fps"]} FPS；本地限速、每帧推理。',
             f'- 输出缓冲：{extra.get("output_buffer", "baseline")}。',
             f'- 图像路径：{extra.get("image_path", "bgr")}；回传并发槽：{extra.get("transfer_slots", 0)}（0 为不限）。',
             f'- 档位：{a["steps"]}；预热 {a["warmup"]} 秒；采集 {a["duration"]} 秒。',
             ('- 性能摸底：不应用 FPS 或计划落后验收门槛；检查输出完整性和正常收尾。' if measure_only else f'- 门槛：每路每个窗口 ≥{threshold} FPS；计划落后 ≤{extra["max_lateness_ms"]} ms；输出完整、正常收尾。'), '',
             '## 档位汇总', '',
             '| 路数 | 结论 | 采集秒数 | 总平均 FPS | 最差逐路窗口 FPS | 未达标路数 | 最差处理 P95 ms | 最大计划落后 ms |',
             '|---:|---|---:|---:|---:|---:|---:|---:|']
    for s in stages:
        lines.append(f'| {s["streams"]} | {labels.get(s["status"], s["status"])} | {num(s["measured_seconds"])} | {num(s["total_average_fps"])} | {num(s["minimum_stream_window_fps"])} | {s["below_threshold_streams"] if s["below_threshold_streams"] is not None else "不判定"} | {num(s["worst_service_p95_ms"])} | {num(s["max_schedule_lateness_ms"])} |')
    if not stages:
        lines += ['| — | 等待当前档完成 | — | — | — | — | — | — |']
    remaining = [count for count in a['steps'] if count not in {s['streams'] for s in stages}]
    if remaining:
        lines += ['', f'尚未执行完的档位：{remaining}。' + ('本轮已结束，这些档位不能视为通过。' if state['status'] != 'running' else '当前档结束后更新。')]
    lines += ['', '## 分阶段与输出核对', '',
              '| 路数/流 | 平均 FPS | 解码均值 ms | 分析均值 ms | 处理 P95 ms | 结果完整 |',
              '|---|---:|---:|---:|---:|---|']
    for r in rows:
        lines.append(f'| {r["streams"]}/{r["stream_id"]:02d} | {num(r["average_fps"])} | {num(r["decode_mean_ms"])} | {num(r["analysis_mean_ms"])} | {num(r["service_p95_ms"])} | {"是" if r["output_ok"] else "否"} |')
    lines += ['', '### 后处理拆分（各路均值等权平均）', '',
              '| 路数 | 回传排队 ms | 输出读取 ms | CPU 筛框/NMS ms |',
              '|---:|---:|---:|---:|']
    for stage in stages:
        selected = [r for r in rows if r['streams'] == stage['streams']]
        def mean(key):
            values = [r[key] for r in selected if r[key] is not None]
            return sum(values) / len(values) if len(values) == len(selected) and values else None
        lines.append(f'| {stage["streams"]} | {num(mean("transfer_wait_mean_ms"))} | {num(mean("output_transfer_mean_ms"))} | {num(mean("cpu_postprocess_mean_ms"))} |')
    lines += ['', '输出读取包含主机缓冲申请、SDK 取回张量及必要的数据类型转换；CPU 后处理包含筛框、NMS 和坐标还原。它们是后处理的子项，不能再次累加到总处理时间。旧记录没有这些计时则显示未测。']
    lines += ['', '## 结论边界与异常', '',
              '分析耗时包括图像转换、预处理、模型推理和后处理，并非 TPU 纯推理时间。处理耗时不含限速等待和 JSON 写入；FPS 计数在结果写入后更新。',
              '计划落后是相对本地 25 FPS 读取计划的延后，不是实测网络队列长度或摄像头端到端时延。未测的编码阶段填空，不填零。',
              '成绩属于当前模型、素材与每路独立进程实现；不代表客户算法精度或芯片最大容量。']
    for r in results:
        lines.append(f'- {r["streams"]} 路：`{json.dumps(r.get("reason"), ensure_ascii=False)}`；收尾退出码：`{r.get("exit_codes_after_cleanup")}`。')
    if state.get('reason'):
        lines.append('- 运行说明：' + state['reason'])
    lines += ['', '## 文件', '',
              '- [档位 CSV](stages.csv)、[逐路 CSV](streams.csv)、[窗口 CSV](windows.csv)。',
              '- [原始结果](summary.json)、[配置及模型指纹](config.json)。',
              '- `step_XX/stream_XX/detections.jsonl`：逐帧检测与耗时；`worker_summary.json`：完成帧数。',
              '- `step_XX/monitor.jsonl`：资源原始采样；各流 `stderr.log`：SDK 日志。', '']
    if measure_only:
        lines = [line.replace('未达标路数', '帧率判定') for line in lines]
    save(directory / 'report.md', '\n'.join(lines))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    state_file = args.directory / 'run_state.json'
    state = json.loads(state_file.read_text(encoding='utf-8')) if state_file.exists() else {'status': 'unknown'}
    write_report(args.directory, state)
    print(args.directory / 'report.md')
