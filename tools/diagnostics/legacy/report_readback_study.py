#!/usr/bin/env python3
"""Generate a compact report from the retained September readback experiment."""
import argparse
import json
from pathlib import Path
import statistics
from analyze_readback_study import summarize


def table(lines, rows, labels, fields):
    lines += ['| '+' | '.join(labels)+' |','|'+'|'.join(['---']*len(labels))+'|']
    for row in rows:
        def fmt(value): return f'{value:.2f}' if isinstance(value,float) else '未测' if value is None else str(value)
        lines.append('| '+' | '.join(fmt(row.get(k)) for k in fields)+' |')
    lines.append('')


def averages(rows):
    groups={}
    for row in rows:
        name=row['stage'].split('_',1)[1] if row['stage'].startswith('r') else row['stage']
        groups.setdefault(name,[]).append(row)
    result=[]
    for name,items in groups.items():
        row={'stage':name,'repeats':len(items)}
        for key,value in items[0].items():
            if isinstance(value,(int,float)) and not isinstance(value,bool):
                row[key]=statistics.mean(x[key] for x in items)
        result.append(row)
    return result


def main():
    p=argparse.ArgumentParser(); p.add_argument('root',type=Path); p.add_argument('--output',type=Path,required=True); a=p.parse_args()
    all_rows={}
    for suite in ['inference','transfer','paired','wall16','admission','yolo26_probe','yolo26_video']:
        if (a.root/suite).exists():
            rows=summarize(a.root/suite); all_rows[suite]=rows
            (a.root/suite/'analysis.json').write_text(json.dumps(rows,indent=2)+'\n')
    lines=['# PCIe 2.0 ×1 回传与 TPU 对照（2026-09-22）','',
      '本轮可落地的优化是普通 YOLOv8 复用已有 score gate、合并读取和缓冲复用：8 路从 94.80 到 199.97 FPS，16 路从 84.17 到 275.10 FPS；分别核对 3,654 / 3,162 对相同源帧，检测结果完全一致。8 路接近素材供给上限；16 路平均约 17.2 FPS，并未达到每路 25 FPS。','',
      '固定输入 YOLOv8 单进程的计算/回传重叠约提高 35%；等量双向传输的收发对速率约提高 90%，这两种收益不能互换，更不能直接套到已经多路并发的 HDMI。HDMI 已有筛选路径约 260 FPS，降预览与继续加大合并预算收益有限；限制处理并发虽然让单次计时变短，却降低吞吐。YOLO26 紧凑输出仅 7,200 B，回传约 0.28 ms，重叠收益不到 1%。','',
      '设备：本轮 device 1，BDF `0004:41:00.0`，实测链路 5.0 GT/s ×1；另外两张卡不运行推理。主机 RK3588，Linux 5.10.198，SOPHON 0.5.1 LTS SP5。此设备编号与旧报告不同，不能沿用编号猜链路。','',
      '视频对照使用本地公路素材，1920×1080、H.264、25 FPS、BT.709 limited，同一卡同模型依次测量。HDMI 为 YOLOv8s INT8、16 路、latest；各路独立解码，输入目标总计 400 FPS。latest 会跳过未送检帧，不能把解码 400 FPS 当成每帧都已检测。普通 YOLOv8 和 YOLO26 对照为 all 逐帧处理。','',
      '表中重复组取各轮均值；完整逐轮结果和时间边界见原始 JSON。TPU 是正式窗口内每秒有效采样均值，SDK 调用耗时是包含排队的墙钟时间。短测不是长稳、检测精度或摄像头验收。','',
      '## 固定输入：计算与结果回传重叠','',
      '输入驻留卡上，输出 2,822,400 B；每组 3 秒预热、15 秒测量、正反序两轮。overlap 用常驻回传线程、独立 handle 和双输出缓冲。没有逐帧上传，因而不是双向 PCIe 测试，也不是视频 FPS。首末输出与参考逐字节一致。','']
    table(lines,averages(all_rows.get('inference',[])),['模式/块大小','循环/s','TPU %','提交+同步 ms','回传 ms'],['stage','iterations_per_second','tpu_mean_percent','submit_sync_ms','copy_mean_ms'])
    lines += ['## 真正双向：等量上传与下载','',
      '每次每方向 2,822,400 B，独立缓冲。serial 为上传后下载，paired 为两个常驻线程每轮同时收发并等待两者完成。15 秒 × 2 轮，数据检查通过。各方向分别报告 MB/s；一次来回的完成速度受较慢方向限制。','']
    table(lines,averages(all_rows.get('paired',[])),['模式','上传 MB/s','下载 MB/s','上传调用 ms','下载调用 ms'],['stage','upload_MB_s','download_MB_s','upload_mean_ms','download_mean_ms'])
    lines += ['此合成负载每次上传与下载同样大的数据；实际视频是上传压缩码流、回传筛选结果与缩略图，收发比例不同，不能把本表加速比例套到视频墙。独立自由运行双向测试及小块结果另见 transfer/analysis.json。','',
      '## HDMI：拆分结果、筛选、预览和缓冲开销','',
      '5 秒预热、30 秒正式窗口、正反序两轮。full 为完整模型输出且无预览；gate0/64/256 为筛选结果且无预览，数字为允许合并读取的额外 KiB。preview10/3 为 gate64 + 输出缓冲复用 + 对应预览上限；baseline 与 preview10 相同但不复用输出缓冲。model_only 不读取结果，同时跳过辅助模型与后处理，因此不是仅关闭 DMA 的单变量实验。','']
    table(lines,averages(all_rows.get('wall16',[])),['配置','检测 FPS','最低单路 FPS','TPU %','回传相关 ms','辅助计算及等待 ms','候选读取 ms','结果 MB/s','预览 MB/s'],['stage','fps','minimum_stream_fps','tpu_mean_percent','output_copy_ms','gate_execute_ms','gate_row_read_ms','output_MB_s','preview_MB_s'])
    lines += ['“回传相关”包含辅助模型执行/排队、最高分读取、CPU 规划、候选读取等，不是纯 PCIe DMA。辅助模型等待随并发变化；从本表不能直接推断 TPU 核心执行变慢或内存带宽已经饱和。','',
      '## HDMI：限制处理并发的反例','',
      '仍为 16 路解码、gate64、reuse、每路预览上限 10 FPS，只改变同时处理的路数。0 表示不限制；5 秒预热 + 20 秒测量，两轮。取得处理名额后才选择最新帧。','']
    table(lines,averages(all_rows.get('admission',[])),['并发限制','检测 FPS','TPU %','回传相关 ms','辅助计算及等待 ms'],['stage','fps','tpu_mean_percent','output_copy_ms','gate_execute_ms'])
    lines += ['降低计时数字可能只是减少排队；是否优化必须同时看总吞吐和逐路公平性，不能只看一帧 SDK 调用变短。默认保持不限制并发。','', '## 普通 YOLOv8：共享筛选回传实现','']
    yolo=a.root/'yolov8_8/summary.json'
    if yolo.exists():
        summary=json.loads(yolo.read_text())
        table(lines,summary['results'],['配置','8 路总 FPS','最低单路 FPS','TPU %','回传相关 ms'],['name','fps','minimum_stream_fps','tpu_mean_percent','copy_mean_ms'])
        table(lines,summary['same_frame_comparisons'],['对照原版','相同源帧对数','结果不同帧数'],['variant','matched_frames','different_frames'])
        lines += ['每组 10 秒预热、30 秒正式窗口。legacy_full 是保留的修改前二进制；shared_full 使用共享检测器及缓冲复用；shared_gate64 再启用 gate 和合并读取。同帧核对覆盖所有可配对记录（含预热），不等于带标注精度验收。','']
    lines += ['## YOLO26：紧凑输出','']
    yolo16=a.root/'yolov8_16/summary.json'
    if yolo16.exists():
        # Keep this additional all-frame video test next to the YOLOv8 results.
        lines[-2:] = ['### 普通 YOLOv8：16 路补测','']
        summary=json.loads(yolo16.read_text())
        table(lines,summary['results'],['配置','16 路总 FPS','最低单路 FPS','TPU %','回传相关 ms'],['name','fps','minimum_stream_fps','tpu_mean_percent','copy_mean_ms'])
        table(lines,summary['same_frame_comparisons'],['对照原版','相同源帧对数','结果不同帧数'],['variant','matched_frames','different_frames'])
        lines += ['同样为逐帧处理、10 秒预热 + 30 秒正式窗口；单轮补测，未达到每路 25 FPS 时会落后源播放时钟，不主动跳过推理帧。','', '## YOLO26：紧凑输出','']
    table(lines,averages(all_rows.get('yolo26_probe',[])),['模式','模型调用/s','TPU %','提交+同步 ms','回传 ms'],['stage','iterations_per_second','tpu_mean_percent','submit_sync_ms','copy_mean_ms'])
    table(lines,averages(all_rows.get('yolo26_video',[])),['并发限制','16 路总 FPS','最低单路 FPS','TPU %','输出复制 ms','输出 MB/s'],['stage','fps','minimum_stream_fps','tpu_mean_percent','output_copy_ms','output_MB_s'])
    lines += ['YOLO26 使用官方 FP32 模型，与 YOLOv8s INT8 不同，不应直接比较 FPS 作为同精度模型优化收益。视频对照保持 BGR、逐帧处理，分别不限制并发与限制 4 路；单次 20 秒窗口。此旧后端未写正式起点，分析器用逐帧解码时间、计划落后、帧号和源帧率重构，并要求各路一致。','',
      '## 复核和限制','',
      '原始目录：`data/results/readback-20260922/`。保留各阶段命令、模型标识、SDK 日志、每秒 TPU 原始采样、阶段结果及同帧检测 JSONL。diagnostic 只验证固定输入输出一致；视频对照不含人工标注准确率。纯解码另外保存在 decode16，走 zero_copy 到 null，无检测结果回传。','',
      '带框视频编码额外完成 1 路兼容性检查：5 秒预热、10 秒窗口，检测记录与编码输出核验通过；这是回归检查，不是编码容量测量。纯解码首轮 5 秒预热的计数偏高，不能排除启动追赶或进度采样影响；另用 decode16-steady 的 30 秒预热复测，实际测量 60.22 秒，总平均 400.63 FPS，最差逐路窗口 24.46 FPS。该项是限速素材测试，不是解码极限，也不代表所有窗口严格达到 25 FPS。纯解码没有模型结果或预览回传，不适用输出回传优化。','',
      '应用吞吐、推理同步墙钟时间、TPU 利用率、双向有效负载速度是不同指标。未做内核时间线或 DDR/驱动锁测量，因此不将所有额外开销归因于某个硬件单元。没有把失败或变慢的实验改成默认配置。']
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    (a.root/'all-analysis.json').write_text(json.dumps(all_rows,indent=2)+'\n')


if __name__=='__main__': main()
