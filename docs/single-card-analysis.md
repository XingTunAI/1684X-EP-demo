# 单卡解码＋推理压测操作

以下路径均相对于工程根目录。硬件测试命令在设备执行。当前 32 路优化测试已完成，总平均 114.10 FPS、平均每路 3.57 FPS。详见[演示总览](demo-summary-20260907.md)、[优化结论](analysis-optimization-20260907.md)和[报告阅读指南](benchmark-report-guide.md)。

## 当前默认：只测性能

默认不以 25 FPS 判定通过/失败。25 FPS 是输入视频的源帧率，报告记录每路实际帧率、最低统计窗口、处理耗时和运行异常。正常完成显示 `measured`（测量完成），不等于验收通过。只有显式加 `--acceptance`，才应用历史 FPS 和计划落后门槛。

本次 32 路摸底命令：

```bash
bash scripts/run_single_card_analysis_auto.sh --device 1 --steps 32 \
  --measure-only --image-path device-bgr --warmup 180 --duration 120 --window 60 --stall-timeout 180
```

analysis 默认采用设备内 BGR 转换路径 `device-bgr`，已完成单路及 32 路对照，详见[优化结论](analysis-optimization-20260907.md)。`--image-path bgr` 保留原路径作为对照。直接 YUV 路径通过 `--image-path yuv` 选择，目前处于验证阶段，只支持 analysis 模式，不作为默认优化。逐帧结果新增图像交付、预处理、推理调用及同步、后处理耗时；这些分项也保存在 `streams.csv`。暂不支持 batch 4。

本阶段测试硬件解码 → 图像转换与预处理 → YOLOv8 推理 → 后处理 → 检测 JSON。每帧分析，不主动抽帧，不画框、不编码。使用现有 YOLOv8s INT8 batch 1 模型作为可复现基线；客户最终模型、输入尺寸和每路分析帧率确定后，需要重新测试。

每路一个 C++ 进程、独立解码器与模型实例。当前实现是 SOPHON OpenCV 取帧后交给官方 BMCV YOLOv8 检测代码；它与纯解码 FFmpeg 的 null 输出路径不同，不能直接把两者 FPS 差值全部算作 TPU 推理开销。

## 1. 准备和编译（设备执行）

确认没有其他压测正在使用同一张卡。当前物理卡 1 对应软件 `device 1`（PCIe 3.0 ×1），物理卡 2 对应 `device 0`（PCIe 2.0 ×1）。

```bash
cd /home/linaro/1684X-EP-demo
cmake -S src/single_card_pipeline -B src/single_card_pipeline/build
cmake --build src/single_card_pipeline/build -j2
python3 -m unittest discover -s src/single_card_pipeline/tests -v
```

默认素材：`datasets/stress/bbb_1080p25_h264_8mbps_20min.mp4`，1080p、H.264、25 FPS，连续 20 分钟。准备方式与校验值见[素材说明](test-media.md)。默认模型：`third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel`。配置会保存模型 SHA-256。

## 2. 一键执行

```bash
bash scripts/run_single_card_analysis_auto.sh
```

默认设备 1、阶梯 1/2/4/8 路，每档预热 30 秒、采集 120 秒、窗口 60 秒。低帧率仍继续后续档位；运行异常、输出不完整或中断时停止。`--acceptance` 可显式启用逐路每窗口 ≥24.5 FPS、最大计划落后 ≤1000 ms 的历史验收方式。

只测单路：

```bash
bash scripts/run_single_card_analysis_auto.sh --steps 1
```

指定模型、卡和输入：

```bash
bash scripts/run_single_card_analysis_auto.sh \
  --device 1 --steps 1,2,4,8 \
  --input datasets/stress/bbb_1080p25_h264_8mbps_20min.mp4 \
  --bmodel third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel
```

需要延长某档测试时，将该档设为 `--steps 路数 --warmup 180 --duration 900 --window 60`。正式压力测试不要同时拉取大文件、启动显示或其他基准，保持负载条件可比。当前模式是限速本地输入，帧率落后时后续会追赶；不通过抽帧或重置计划来隐藏落后。

需要后台运行时：

```bash
mkdir -p results
nohup bash scripts/run_single_card_analysis_auto.sh --steps 1 \
  > results/analysis_console.log 2>&1 < /dev/null &
tail -f results/analysis_console.log
```

不要重复执行启动命令。控制台会打印唯一 Results 目录；前台 Ctrl+C 会保留未完成状态并收尾本轮子进程。

## 3. 看结果

默认目录为 `results/analysis/日期时间_PID/`。`report.md` 在开始、每档结束和收尾时自动生成，运行期间可查看，当前档未结束时不提前判通过。

| 文件 | 主要用途 |
|---|---|
| `report.md` | 中文汇总、逐路 FPS、分阶段耗时、失败项 |
| `stages.csv` | 每档总 FPS、最差窗口；门槛字段默认不判定 |
| `streams.csv` | 每路加权平均 FPS、最低窗口、处理 P95、最大计划落后 |
| `windows.csv` | 各窗口总 FPS、最低逐路 FPS；门槛字段默认不判定 |
| `config.json` | 参数、视频元数据、FFmpeg 版本、模型路径和 SHA-256 |
| `summary.json` | 最终测量状态、可选验收判断及输出核对 |
| `step_XX/stream_XX/detections.jsonl` | 每帧检测框、分阶段耗时、计划落后 |
| `step_XX/stream_XX/worker_summary.json` | 正常收尾后的完成帧数、模式 |
| `step_XX/monitor.jsonl`、逐路 `stderr.log` | 资源采样和 SDK 日志 |

解码耗时是 OpenCV 取帧耗时；分析耗时包含图像转换、预处理、推理、后处理。处理 P95 是一帧从开始取帧到分析完成的耗时（编码模式则到编码提交），不含限速等待和 JSON 写入，不是摄像头到显示器延迟。计划落后是相对于本地源帧率计划的延后，不能当作实测网络队列或真实丢帧。

`analysis` 模式不会生成 `output.mp4`；未运行的画框和编码阶段在 JSON 为 null、CSV 为空。帧率计数在检测结果写入后更新，结束时核对连续帧编号与 worker 汇总，强制终止、记录缺失不能通过。

## 4. 同步回 Windows

在 PowerShell 执行，将 `<测试编号>` 替换为控制台的目录名：

```powershell
# 先在 PowerShell 中进入工程根目录
New-Item -ItemType Directory -Force results/board-analysis | Out-Null
adb -s bf43cc5e0819e5ad pull /home/linaro/1684X-EP-demo/results/analysis/<测试编号> results/board-analysis/
```

原始数据需与报告一起保存，避免只留下平均 FPS。`datasets/`、`results/` 在 Git 中忽略，需要单独同步。

## 5. 后续完整业务链路

加入画框与 H.264 编码可使用 `--mode encode`，结果转存到 `results/pipeline/`。该模式会持续写视频并检查磁盘空间，需重新测量，成绩不能与本阶段混用。完整说明见[源码 Demo](../src/single_card_pipeline/README.md)。

本阶段没有验证真实摄像头网络接入、算法精度、多流 batch 或共享模型。当前单卡演示和图像路径优化已完成。本阶段不追加抽帧、实时显示或客户素材测试，后续工作按新的业务要求另行确定。

## 独立模型计算对照

在同卡没有视频任务运行时执行 `python3 scripts/run_single_card_tpu_bench.py --device 1`。自动串行测试 batch 1 与 batch 4，并输出 `results/tpu/日期时间/report.md`。它用于区分模型计算与视频链路开销，不能代表实际视频分析帧率。详见[优化结论](analysis-optimization-20260907.md)。
