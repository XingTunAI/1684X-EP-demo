# YOLOv8 单卡与多卡检测

[仓库首页](../../README.md) / [文档索引](../../docs/README.md)

用同一入口选择设备、每卡路数和运行时长。每张卡启动独立后端进程，每路完成硬件解码、预处理、YOLOv8 推理与检测记录写出。

## 依赖与数据

先完成[环境准备](../../docs/setup.md)。需要 Linux、匹配的 libsophon 与 SOPHON FFmpeg/OpenCV、C++11、CMake 3.13+ 和 Python 3.9+。以下一键流程会安装基础工具、准备官方源码并下载 YOLOv8 模型和示例数据；已按环境准备页获取资源时，只需执行构建。从仓库根目录执行：

```bash
bash scripts/prepare.sh
bash demos/yolov8/build.sh
```

资源渠道：[SOPHGO 官方 YOLOv8 样例](https://github.com/sophgo/sophon-demo/tree/release/sample/YOLOv8_plus_det)与[官方下载脚本](https://github.com/sophgo/sophon-demo/blob/release/sample/YOLOv8_plus_det/scripts/download.sh)。示例视频、测试图片、COCO128、COCO 验证子集和类别文件保存在 `third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/`。完整清单、用途和下载中断后的恢复步骤见[数据与官方资源说明](../../data/README.md)。

构建产物为 `demos/yolov8/build/pipeline_worker.pcie`。`build.sh` 的额外参数传给 CMake，`BUILD_JOBS` 默认 2；`BUILD_DIR` 可调整构建位置，但下述运行入口固定查找默认 `build/`。

| 数据 | 默认位置或要求 |
|---|---|
| 输入视频 | `third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/test_car_person_1080P.mp4` |
| 视频格式 | 主入口要求本地 H.264、1920×1080、25 FPS；自行准备的视频放在 `data/inputs/` |
| 模型 | `third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel` |
| 类别 | `third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/coco.names`，一行一个名称 |
| 自有模型 | BM1684X YOLOv8 bmodel，batch 1，输入输出布局须匹配本目录检测器；与匹配类别文件放在 `data/models/` |

官方资源留在 `third_party/`，不会复制到 `data/`。可用 `bash scripts/prepare_yolov8n.sh` 准备官方 YOLOv8n，再通过 `--bmodel` 指定下载后的 `yolov8n_int8_1b.bmodel`。

## 运行

本板下载的官方 `test_car_person_1080P.mp4` 实际为 **24 FPS**，而本入口的后端固定要求 **1080p25 H.264**；不传 `--input` 会选中官方文件，可能因帧率不符而退出。以下示例要求先准备 `data/inputs/input_1080p25.mp4`，用 ffprobe 确认编码、尺寸及实际帧率。需要直接使用官方 24 FPS 素材时，使用 [HDMI 单卡入口](../hdmi_wall/docs/single-device.md)。本次仅修正文档，不改变后端输入校验。

```bash
# 单卡，测量 60 秒
bash demos/yolov8/run.sh --devices 0 --duration 60 --input data/inputs/input_1080p25.mp4

# 双卡，每卡 2 路，测量 300 秒
bash demos/yolov8/run.sh --devices 0,1 --streams 2 --duration 300 --input data/inputs/input_1080p25.mp4

# 自有输入、模型与输出父目录
bash demos/yolov8/run.sh --devices 0 --duration 60 \
  --input data/inputs/input_1080p25.mp4 \
  --bmodel data/models/yolov8.bmodel --classnames data/models/classes.names \
  --output data/results/yolov8
```

也可使用 `python3 demos/yolov8/run.py`。所有相对路径均以仓库根目录为基准。

启动后终端打印 `Results:` 和本次结果目录。等待测量与收尾结束，再检查该目录 `summary.json` 的 `status` 是否为 `completed`，以及各卡的 `exit_code` 是否为 0；检测 FPS 要到卡级报告查看。若失败，先读同目录的 `device_<id>.log`。首次单路跑通后再增加路数或设备。

| 参数 | 含义与默认值 |
|---|---|
| `--devices` | 不重复的设备编号列表，范围 0–3；默认 `0`，例如 `0,1` 或 `0,1,2,3`。设备须实际存在 |
| `--streams` | 每卡独立路数，1–32，默认 1；每路分别打开同一输入文件 |
| `--duration` | 每卡后端测量窗口秒数，正数，默认 60 |
| `--warmup` | 预热秒数，非负数，默认 5 |
| `--input` | 本地 1080p25 H.264；默认路径指向官方样例，但该文件在本板为 24 FPS，运行时应显式选择符合要求的视频 |
| `--bmodel / --classnames` | 覆盖默认模型和类别文件 |
| `--output` | 结果父目录，默认 `data/results/yolov8`；每次自动创建唯一子目录 |
| `--dry-run` | 打印命令计划；不检查文件、创建目录或启动硬件进程 |
| `--help` | 查看完整入口参数 |

主入口使用 `analysis` 和 `device-bgr` 路径，按顺序处理帧、循环本地文件，不输出视频。通过 `--dry-run` 可检查实际后端命令。

YOLOv8 的预热从 worker 启动时开始，初始化可能占用预热，过长时也可能进入测量窗口。各卡独立开始，`--duration` 不等于启动器总运行时间。Ctrl+C 或某张卡失败会触发本次运行所属子进程的清理。

## 输出文件与字段

```text
data/results/yolov8/<run-id>/
  run.json                         # 命令、路径、计时配置、各卡状态
  summary.json                     # 外层进程退出与清理结果
  device_<id>.log                   # 对应后端的标准输出和错误
  device_<id>/<backend-run-id>/
    report.md
    config.json
    summary.json                   # 此卡的测量结果和输出核验
    run_state.json
    stages.csv
    streams.csv
    windows.csv
    step_<N>/
      summary.json
      samples.csv
      monitor.jsonl
      stream_<id>/
        detections.jsonl
        worker_summary.json
        progress.log
        stderr.log
```

`<run-id>` 使用 UTC 时间与随机标识；YOLOv8 后端另创建时间与进程号组成的 `<backend-run-id>`。外层 `summary.json` 的 `status`、`cards[].status`、`exit_code` 表示进程结果，不计算多卡总 FPS。查看每张卡后端目录下的报告、汇总及逐路 CSV 获取检测速率；不同卡的测量区间不保证重合。

`detections.jsonl` 每行对应一帧，`frame` 从零递增；即使没有目标，也写入空检测列表。`detections` 中每项包含 `class_id`、`score` 和原图像素坐标 `xyxy=[x1,y1,x2,y2]`。类别索引使用对应名称文件解释。

记录中的 `decode_ms`、`image_bridge_ms`、`preprocess_ms`、`inference_ms`、`postprocess_ms` 是阶段耗时；`schedule_lateness_ms` 是相对本地读取计划的落后时间。`worker_summary.json` 保存处理帧数及缓冲信息，卡级汇总核验记录连续性。

正常进程完成返回 0；运行失败返回 1；参数或启动前检查失败返回 2；保护超时返回 124；Ctrl+C 返回 130，SIGTERM 返回 143。保护超时为每卡 `duration + warmup + 600 + 120` 秒，只用于异常保护。

## 实际运行数据

以下为 2026-09-07 在 RK3588 + 单张 BM1684X 上留下的后端实测记录。输入为同一份本地 H.264、1920×1080、25 FPS 视频，各路独立读取；YOLOv8s INT8 batch 1，`analysis`、`device-bgr`，不编码视频。

| 路数 | 预热 | 实际测量时长 | 检测完成总 FPS | 记录状态 |
|---:|---:|---:|---:|---|
| 4 | 30 秒 | 60.14 秒 | 100.01 | `measured`，4 路记录连续且进程正常退出 |

总 FPS 使用正式窗口内的 6,014 帧除以 60.1351 秒计算，不能把含预热的全部 JSON 行数用于这一计算。100.01 是四路合计，不能据此判断每路每个窗口都达到 25 FPS。记录完整也不代表检测准确率已经验收。

本次目录与单卡/多卡启动入口整理后尚未上板复测；上表是历史后端结果，不是当前入口或双卡的复测结论。按本页命令运行后，以新生成的卡级报告为准。

## 底层单卡编码

保留的 `runner.py` 提供单卡高级功能，参数与主入口不同。需要画框并保存 H.264 视频时，可直接调用：

```bash
python3 demos/yolov8/runner.py --device 0 --steps 1 \
  --bmodel third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel \
  --input data/inputs/input_1080p25.mp4 --mode encode --image-path bgr \
  --warmup 5 --duration 60 --measure-only \
  --output data/results/yolov8-encode
```

该命令在唯一运行目录下直接生成卡级文件树，逐路另含 `output.mp4`。视频写出调用返回表示提交完成，收尾后才核验实际编码帧数。主入口只接受上表列出的参数，高级参数应传给 `runner.py`。

逐帧 JSON 和日志持续占用磁盘；更长时长、更多路数也会增加收尾核验的内存和耗时。长时间运行需预留足够的内存、磁盘及收尾时间。

完整帧记录和正常退出不能代替检测准确率检查。图像与模型输出核对见[诊断工具](../../tools/diagnostics/README.md)，计数位置、计时区间和 FPS 解释见[指标说明](../../docs/metrics.md)。
