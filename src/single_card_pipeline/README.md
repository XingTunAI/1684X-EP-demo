# 单卡视频分析压测 Demo

推理性能定位见[计时与隔离测试](../../docs/inference-diagnostics.md)，可分别测模型执行、输出回传及两者组合。

当前默认采用性能摸底，不应用 FPS 或计划落后验收门槛，正常完成状态为 `measured`。低帧率继续后续档位，运行异常/结果不完整则停止。历史验收模式需显式 `--acceptance`。从 1 路开始的逐档测试命令见[操作文档](../../docs/single-card-analysis.md)。新增分阶段统计，analysis 默认使用已完成 32 路对照的 `device-bgr` 图像路径；`bgr` 保留原路径，`yuv` 保留实验路径。

180 秒预热、120 秒采集的 32 路结果为总平均 114.10 FPS、平均每路 3.57 FPS，见[优化结论](../../docs/analysis-optimization-20260907.md)。报告含义见[阅读指南](../../docs/benchmark-report-guide.md)，功能与配置见[演示总览](../../docs/demo-summary-20260907.md)。

## 当前阶段：解码＋推理

一键执行 `bash scripts/run_single_card_analysis_auto.sh`，默认软件设备 1，使用连续 20 分钟 1080p25 H.264 视频和 YOLOv8s INT8 batch 1 模型，按 1/2/4/8 路逐档测试；每档预热 30 秒、采集 120 秒，运行异常时停止加压。每帧推理，输出检测 JSON 和报告，不画框、不编码。操作与指标定义见[解码＋推理压测操作](../../docs/single-card-analysis.md)。

`run.py --mode analysis` 使用该模式，默认结果位于 `results/analysis/`；`--mode encode` 保留下述完整链路，默认结果位于 `results/pipeline/`。直接运行 `run.py` 为兼容旧命令仍默认 encode，一键 analysis 入口默认 analysis。两种模式均自动生成 `report.md`、`stages.csv`、`streams.csv`、`windows.csv` 和最终状态。

以下为完整编码链路的说明及历史验证。

业务目标：32 路 1080P/25 FPS 视频同时解码、算法分析、画框并重新编码，同时输出结构化检测结果。本目录提供完整链路的本地文件基线，逐级验证实际容量。

## 规格边界

算能 [BM1684X 官方产品资料 V1.7](https://sophon-file.sophon.cn/sophon-prod-s3/drive/24/03/11/20/BM1684X%20%E4%BA%A7%E5%93%81%E5%BD%A9%E9%A1%B5_V1.7.pdf)列出 32×1080P@25 FPS 解码、12×1080P@25 FPS 编码。32 路全部重新编码超出标称编码路数，不能从解码指标推导可行。需要测量指定模型的完整链路容量，再评估多卡分配或其他编码资源；不会自动降低客户要求的帧率、分辨率或输出路数。

## 当前实现

- Python 负责按指定设备启动各路 C++ 进程，复用 `../single_card_decode/` 的生命周期和采样逻辑。
- 每路独立解码器、YOLOv8 模型实例、画框、H.264 编码器、输出文件及检测 JSONL。
- 每帧都推理，不主动抽帧；本地文件按源帧率读取，处理慢时记录相对读取计划的落后时间，不通过重置时钟掩盖积压。
- 输入本地视频可循环；目前不包含 RTSP 输入/输出、真实摄像头接入、业务告警规则、跨流 batch 和模型共享。JSON 提供检测框、类别 ID 和置信度供后续业务使用。
- 目前仅支持 batch=1 的模型，默认目标 25 FPS、编码目标码率每路 4000 kbps。多进程会重复加载模型，其瓶颈不能等同于优化后的硬件极限。

## 编译和一路验证

在 RK3588 执行：

```bash
cd /home/linaro/1684X-EP-demo
cmake -S src/single_card_pipeline -B src/single_card_pipeline/build
cmake --build src/single_card_pipeline/build -j2

python3 src/single_card_pipeline/run.py \
  --device 0 \
  --input datasets/stress/bbb_1080p25_h264_8mbps.mp4 \
  --bmodel third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel \
  --steps 1 --warmup 30 --duration 120 --window 60 --stall-timeout 60
```

一路通过后，先测试 `--steps 2,4,8,12`，必要时再测 16、24、32 路。建议先用每档 120 秒验证输出，再进行 900 秒及更长的稳定性测试。默认完整记录各档性能；运行异常时停止继续加压。

输出视频会持续占用磁盘。板端只有约数 GB 可用空间，32 路、每路 4 Mbps、15 分钟仅视频就约 14.4 GB，不能直接使用板端根分区进行这种长测。可以通过 `--output /已挂载外部磁盘/pipeline-results` 指定输出目录。工具检查估算空间，每路也会在运行中检测剩余空间；估算不等于码率上限保证。

## 结果与通过条件

结果在 `results/pipeline/日期时间_PID/step_XX/`：

- `stream_XX/output.mp4`：该路 H.264 带框视频。
- `stream_XX/detections.jsonl`：逐帧框坐标、类别 ID、置信度、解码/分析/画框/编码提交耗时、处理耗时及计划落后时间。
- `stream_XX/worker_summary.json`：进程正常收尾后保存的分析帧数、编码提交帧数。
- `summary.json`：窗口 FPS、检测记录与视频实际帧数校验、处理耗时 P95 和最大计划落后时间。
- 其他原始采样和监控与纯解码 demo 相同。

每帧输出计数发生在推理、画框、编码提交及 JSON 写入之后。`VideoWriter.write()` 返回不等于帧已编码并落盘，因此程序结束后逐个解码检查输出文件帧数，并核对其等于算法结果行数和编码提交数。输出文件声明的 25 FPS 不能当作实际处理速度。

仅在显式 `--acceptance` 模式下，`pass` 要求全部流窗口 FPS ≥ `--min-fps`（默认 24.5）、结果与编码帧数完整匹配、测量区间的计划落后时间 ≤ `--max-lateness-ms`（默认 1000 ms）。默认门槛用于初步验证，正式验收需与客户约定。`service_p95_ms` 是开始读取该帧到编码提交返回的处理耗时，不是摄像头到显示器的端到端时延。尚未测量的真实丢帧和端到端时延保持 null。

`fps_status` 保留原始帧率结果；`reason` 分别给出 fps、输出校验和时序是否通过。进程错误、超时、强制终止或未完成收尾都不能算通过。应同时检查 `stderr.log` 的 SDK 错误。

本地测试素材为动画，仅用于解码和链路基线；客户算法精度和典型检测负载应另用真实业务素材验证。

## 历史 encode 模式一路短测（2026-09-07）

已在 RK3588 编译并以 linaro 用户测试卡 0、YOLOv8s INT8 batch 1，预热 5 秒、采集约 10 秒。结果目录为 `results/pipeline/20260907_095821_9501/`，两窗口约 4.55/4.74 FPS，判定 fail。69 条逐帧检测结果与 69 帧可解码 H.264 视频一致，输出完整性通过；处理耗时 P95 约 245 ms，最大计划落后约 11.83 秒，实时性未通过。

全程粗分阶段平均耗时：解码/取帧 57.87 ms，分析（含输入转换、预处理、推理、后处理）65.44 ms，画框 0.12 ms，编码提交 92.58 ms。不能把分析阶段耗时直接当成 TPU 纯推理耗时，也不能把编码提交耗时全部归因于编码硬件。上述为 encode 模式的历史诊断。此后 analysis 模式已完成图像路径优化和 32 路对照，encode 模式未按同配置重测，不能混用两种成绩。

SDK 日志确认编码器使用设备 0 的 VPU；MP4 的 H264 标签自动回退为 avc1 属于封装提示，输出已通过实际解码帧数校验。短测不代表稳定容量结论。

运行逻辑与输出完整性测试：

```bash
python3 -m unittest discover -s src/single_card_decode/tests -v
python3 -m unittest discover -s src/single_card_pipeline/tests -v
```

## 纯解码对照记录

2026-09-07 卡 0 的 32 路纯解码结果 `20260907_094905_6732`：预热 180 秒后采集约 90.31 秒，总计约 816.28 FPS，但 7 路低于 24.5 FPS，最慢一路约 23.12 FPS，解码错误日志为空。该结果反映逐路不均衡，未证明芯片总解码吞吐不足，也不代表完整算法链路成绩。

## 输出回传诊断

`--transfer-slots 0`（默认）不限回传并发；正数按流编号分配回传槽。32 路短测中 4/8 槽未提高总吞吐，因此保留为实验参数。报告新增回传排队、输出读取和 CPU 后处理子项，详见[并发对照](../../docs/analysis-transfer-20260907.md)。检测器适配代码及版权信息位于 [detector](detector/README.md)。

## 输出缓冲区复用

`--output-buffer baseline` 保留默认分配方式；`--output-buffer reuse` 在首次处理时申请主机和卡端输出缓冲并在后续帧复用。限定静态 PCIe batch 1 单个三维 FP32 输出，其他配置会报错。结果和申请次数由验证器检查。[1/8 路对照](../../docs/analysis-buffer-reuse-20260907.md)未观察到吞吐提升，因此未更改默认值。

轻量模型可通过一键脚本的 `--model-preset yolov8n` 选择，准备方法见[操作说明](../../docs/single-card-analysis.md#可选轻量模型)。
