# YOLO26 单卡与多卡检测

用同一入口选择设备、每卡路数和运行时长。每张卡运行独立后端，各路独立解码、预处理和推理，保存原图坐标的检测框、置信度与类别。

验证状态：当前默认 FP32 + BGR 路径尚无完成检测正确性验收及连续吞吐复测的记录。历史 INT8 + YUV 数据及其限制见[实际运行数据](#实际运行数据)。

## 依赖与数据

先完成[环境准备](../../docs/setup.md)。需要 Linux、C++11、CMake 3.13+、Python 3.9+、libsophon 和 SOPHON FFmpeg/OpenCV；普通桌面 OpenCV 不能替代设备 VideoCapture 与 BMImage 扩展。

首次使用时，按[环境准备中的公共工具与依赖源码步骤](../../docs/setup.md#公共工具与依赖源码)克隆 `third_party/sophon-demo/`；已有目录则复用并确认存在 `sample/YOLO26/`。只使用 YOLO26 时无需先下载 YOLOv8 模型。从仓库根目录执行：

```bash
bash third_party/sophon-demo/sample/YOLO26/scripts/download.sh --BM1684X
bash demos/yolo26/build.sh
```

资源渠道：[SOPHGO 官方 YOLO26 样例](https://github.com/sophgo/sophon-demo/tree/release/sample/YOLO26)与[官方下载脚本](https://github.com/sophgo/sophon-demo/blob/release/sample/YOLO26/scripts/download.sh)。示例视频、测试图片、COCO128、COCO 验证子集和类别文件保存在 `third_party/sophon-demo/sample/YOLO26/datasets/`。完整清单、用途和下载中断后的恢复步骤见[数据与官方资源说明](../../data/README.md)。

构建产物为 `demos/yolo26/build/yolo26_streams.pcie`。`build.sh` 的额外参数传给 CMake，`BUILD_JOBS` 默认 2；`BUILD_DIR` 可调整构建位置，但下述运行入口固定查找默认 `build/`。

| 数据 | 默认位置或要求 |
|---|---|
| 输入视频 | `third_party/sophon-demo/sample/YOLO26/datasets/test_car_person_1080P.mp4` |
| 视频格式 | 主入口接收本地文件，按元数据 FPS 调度；解码图像宽度须能被 64 整除 |
| 模型 | `third_party/sophon-demo/sample/YOLO26/models/BM1684X/yolo26s_fp32_1b.bmodel` |
| 类别 | `third_party/sophon-demo/sample/YOLO26/datasets/coco.names`，一行一个名称 |
| 自有资源 | 视频放在 `data/inputs/`，兼容的 bmodel 和类别文件放在 `data/models/` |

默认使用官方 YOLO26s FP32 batch 1；可通过 `--bmodel` 显式指定官方 `yolo26s_int8_1b.bmodel`。模型须满足本页下方的输入输出契约。

## 运行

```bash
# 单卡，测量 60 秒
bash demos/yolo26/run.sh --devices 0 --duration 60

# 双卡，每卡 2 路，测量 300 秒
bash demos/yolo26/run.sh --devices 0,1 --streams 2 --duration 300

# 自有输入、模型与输出父目录
bash demos/yolo26/run.sh --devices 0 --duration 60 \
  --input data/inputs/input.mp4 \
  --bmodel data/models/yolo26.bmodel --classnames data/models/classes.names \
  --output data/results/yolo26
```

也可使用 `python3 demos/yolo26/run.py`。所有相对路径均以仓库根目录为基准。

启动后终端打印 `Results:` 和本次结果目录。等待测量与收尾结束，再检查外层 `summary.json` 的 `status` 是否为 `completed`、各卡 `exit_code` 是否为 0；卡级 `summary.json` 中查看 FPS 与 `records_complete`。若失败，先读同目录的 `device_<id>.log`。首次单路跑通后再增加路数或设备。

| 参数 | 含义与默认值 |
|---|---|
| `--devices` | 不重复的设备编号列表，范围 0–3；默认 `0`，例如 `0,1` 或 `0,1,2,3`。设备须实际存在 |
| `--streams` | 每卡独立路数，1–32，默认 1；每路分别打开同一输入文件 |
| `--duration` | 每卡后端测量窗口秒数，正数，默认 60 |
| `--warmup` | 预热秒数，非负数，默认 5 |
| `--input` | 本地视频，默认使用上表官方样例 |
| `--bmodel / --classnames` | 覆盖默认模型和类别文件 |
| `--output` | 结果父目录，默认 `data/results/yolo26`；每次自动创建唯一子目录 |
| `--dry-run` | 打印命令计划；不检查文件、创建目录或启动硬件进程 |
| `--help` | 查看完整入口参数 |

主入口固定使用 `bgr` 设备图像桥接、本地源帧率调度和文件循环，按顺序处理帧。通过 `--dry-run` 可检查实际后端命令。

YOLO26 在此卡的流与模型就绪后开始预热和测量。各卡独立开始，`--duration` 不等于启动器总运行时间。Ctrl+C 或某张卡失败会触发本次运行所属子进程的清理。

## 输出文件与字段

```text
data/results/yolo26/<run-id>/
  run.json                         # 命令、路径、计时配置、各卡状态
  summary.json                     # 外层进程退出与清理结果
  device_<id>.log                   # 对应后端的标准输出和错误
  device_<id>/
    config.json
    summary.json                   # 此卡的测量结果
    streams.csv
    stream_<id>/
      detections.jsonl
      summary.json
```

`<run-id>` 使用 UTC 时间与随机标识。外层 `summary.json` 的 `status`、`cards[].status`、`exit_code` 表示进程结果，不计算多卡总 FPS。卡级汇总中的 `total_completed_fps` 是该卡各路完成速率之和，`minimum_stream_fps` 是该卡最慢一路；不同卡的测量区间不保证重合。

`detections.jsonl` 每行含 `stream_id`、递增的 `frame`、文件内 `source_frame_id`、`source_loop`、阶段耗时及 `detections`。检测项为 `class_id`、`score`、原图像素坐标 `xyxy`；没有目标时仍写入空列表。

`decoded` / `completed` 是全程计数；`decoded_measured` / `completed_measured` 只计入正式测量区间。解码 FPS 按解码完成时刻计数，完成 FPS 按检测记录写入时刻计数。`streams.csv` 和逐路汇总包含帧率、窗口及耗时统计；`records_complete` 检查已解码帧与输出记录一致性，`drain_seconds` 记录测量截止后的收尾时间。

正常进程完成返回 0；运行失败返回 1；参数或启动前检查失败返回 2；保护超时返回 124；Ctrl+C 返回 130，SIGTERM 返回 143。保护超时为每卡 `duration + warmup + 600 + 120` 秒，只用于异常保护。底层状态 `measured` 表示完成规定时长，不代表达到某项性能目标。

## 实际运行数据

以下为 2026-09-08 在 RK3588 + 单张 BM1684X 上留下的历史吞吐记录：官方 YOLO26s INT8 batch 1，模型输入 640×640，使用 **YUV** 图像路径。同一份本地 1920×1080、25 FPS 视频由 30 路独立读取，逐帧处理，预热 15 秒。

| 路数 | 测量时长 | 检测完成总 FPS | 最慢一路全程 FPS | 最差逐路 10 秒窗口 FPS |
|---:|---:|---:|---:|---:|
| 30 | 60 秒 | 217.38 | 6.98 | 6.40 |

正式区间完成 13,043 帧，状态为 `measured`，`records_complete=true`。这些字段只证明吞吐测量与记录核验完成。同阶段图像核对出现过检测框坐标异常，检测正确性尚未完成验收，因此此数据不能作为可用检测性能结论。

上表配置与当前默认的 **FP32 + BGR** 不同；当前默认没有可引用的连续吞吐实测值，本次目录和多卡入口整理也未上板复测。两者不能直接比较，更不能把总 FPS 当成每路 FPS。首次运行应先核对检测结果，再讨论并发性能。

## 模型契约与底层功能

| 项目 | 要求 |
|---|---|
| 网络与输入 | 单网络、静态单 stage，一个 NCHW 输入 `[1,3,H,W]`，边界类型 FP32、INT8 或 UINT8，batch 1 |
| 预处理 | RGB、居中 letterbox、填充值 114，宽高从模型读取，按 `input_scale / 255` 转换 |
| 输出 | 恰好一个 FP32 张量 `[1,N,6]`，每行为 `[x1,y1,x2,y2,score,class_id]`，坐标为模型输入像素 |
| CPU 后处理 | 置信度过滤、letterbox 坐标还原及裁剪；不追加 NMS、sigmoid 或 objectness 相乘 |

FP32 输入应为 `[0,1]` RGB 且 scale 为 1；类别文件顺序须与模型一致。图像处理改编自 SOPHON-DEMO，许可见 `detector/LICENSE`。

底层 `demos/yolo26/build/yolo26_streams.pcie` 仍提供单卡输入清单、RTSP 和单帧张量保存功能，其参数与主入口不同；直接调用时需显式提供模型、类别及尚不存在的输出目录。参数定义见 [main.cpp](main.cpp)。底层没有 RTSP 自动重连。日常单卡、多卡定时运行使用上面的 `run.sh` 即可。

不依赖 SDK 的输出解析检查可单独构建：

```bash
BUILD_DIR=demos/yolo26/build/parser bash demos/yolo26/build.sh -DPARSER_ONLY=ON
(cd demos/yolo26/build/parser && ctest --output-on-failure)
```

逐帧 JSON 和日志持续占用磁盘，阶段耗时列表保存在内存用于计算分位数；更长时长、更多路数会增加内存和收尾耗时。长时间运行需预留足够的内存、磁盘及收尾时间。

本 demo 不包含编码、跟踪或 HDMI 显示。逐帧记录完整不能代替检测准确率检查，模型输入大小也不会自动减少原始视频解码量。其他计时与 FPS 说明见[共享指标说明](../../docs/metrics.md)。
