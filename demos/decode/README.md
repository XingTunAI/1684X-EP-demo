# 视频硬件解码

[仓库首页](../../README.md) / [文档索引](../../docs/README.md)

在指定 BM1684X 上启动独立的 FFmpeg 解码进程，记录逐路进度、统计窗口和资源采样。本 demo 不加载检测模型，也不编码视频。

本目录的 `run.sh` 是操作入口，`run.py` 提供默认参数，`stress_decode.py` 实现调度，`report.py` 生成报告，`tests/` 保存离线检查。

## 依赖与输入

先完成[环境准备](../../docs/setup.md)。需要 Python 3.9+、SOPHON FFmpeg/ffprobe，以及可见的 BM1684X 设备；系统软件 FFmpeg 不能代替带 `h264_bm` / `hevc_bm` 的硬件版本。

| 输入 | 要求与位置 |
|---|---|
| 本地视频 | 放在 `data/inputs/`；当前预检要求 1920×1080。 |
| 编码 | H.264 或 H.265，分别使用 `--codec h264` / `--codec hevc`。 |
| 帧率 | 必须与 `--target-fps` 一致；该参数不会转码或转换帧率。 |
| 多个输入 | `--inputs-file` 指定本地清单，每行一个不同路径或 RTSP URL。相对路径基于清单目录。 |
| 模型 | 无需 bmodel 或类别文件。 |

默认示例为 `third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/test_car_person_1080P.mp4`，由 [SOPHON 官方脚本](https://github.com/sophgo/sophon-demo/blob/release/sample/YOLOv8_plus_det/scripts/download.sh)提供。完整清单、获取命令和下载中断后的恢复步骤见[资源说明](../../data/README.md#官方资源)。自有视频和实时地址清单留在 `data/inputs/`。

## 首次运行

按[公共工具与依赖源码步骤](../../docs/setup.md#公共工具与依赖源码)取得官方依赖目录后，从仓库根目录获取视频。此官方下载命令不带模型参数，只准备视频、图片与类别数据；解码 Demo 无需下载 bmodel：

```bash
bash third_party/sophon-demo/sample/YOLOv8_plus_det/scripts/download.sh
bash demos/decode/run.sh \
  --device 0 \
  --codec h264 --target-fps 25 --steps 1 \
  --warmup 5 --duration 20 --window 10 --measure-only \
  --output data/results/decode
```

已经通过 `scripts/prepare.sh` 准备过官方资源时，可跳过下载。未指定 `--input` 时直接使用官方示例；已下载部分文件但视频缺失时，按[资源说明](../../data/README.md#官方资源)单独补齐，不必重新下载整个数据集。

`--steps 1,2,4` 按顺序启动不同路数。每路独立打开同一个文件，程序不会复制视频文件本身。自有视频需满足上表格式，例如：

```bash
bash demos/decode/run.sh \
  --device 0 --input data/inputs/input.mp4 \
  --codec h264 --target-fps 25 --steps 1 \
  --warmup 5 --duration 20 --window 10
```

用 `--inputs-file data/inputs/sources.txt` 替代 `--input` 可提供独立输入。

| 参数 | 含义 |
|---|---|
| `--warmup` | 每档预热秒数，不计入正式统计 |
| `--duration` / `--window` | 正式记录时长 / 统计窗口秒数 |
| `--measure-only` | 只记录，不应用帧率判定条件 |
| `--acceptance --min-fps` | 显式配置逐路窗口的判定条件 |
| `--throughput` | 本地文件不限速读取；默认按源时间戳读取 |
| `--stall-timeout` | 进度停滞的超时秒数 |
| `--dry-run` | 只打印命令 |
| `--help` | 查看完整参数 |

`duration` 应不小于 `window`，`min-fps` 应不大于 `target-fps`。使用较低帧率输入时，两者都需匹配。按 Ctrl+C 请求收尾，等待启动器清理其解码进程。

## 输出

程序在 `--output` 下创建唯一运行目录，并在控制台打印位置：

```text
data/results/decode/<run-id>/
  report.md
  config.json
  run_state.json
  summary.json
  stages.csv
  streams.csv
  step_<N>/
    summary.json
    samples.csv
    monitor.jsonl
    stream_<id>/
      progress.log
      stderr.log
```

`config.json` 保存参数及输入元数据；`stages.csv` 汇总每档，`streams.csv` 汇总逐路。`summary.json` 包含窗口帧数、时长、状态及原因。`samples.csv` 是累计帧数采样，`monitor.jsonl` 是原始资源记录。此入口不单独生成 `windows.csv`。

## 实际运行数据

下表保留 2026-09-08 的历史纯解码结果。2026-09-10 的官方 1080p24 不限速扫描、独立 TPU 负载组见 [容量报告](../hdmi_wall/docs/capacity-validation.md)；与 HDMI 真实视频推理的区别见 [数据总览](../hdmi_wall/docs/current-data.md)。两类素材与并发条件不同，不直接比较峰值。

以下为 2026-09-08 的纯解码历史记录：RK3588 + 单张 BM1684X，PCIe 3.0 ×2，同一份本地 H.264、1920×1080、25 FPS、约 8 Mbps 视频由 30 路独立读取。视频为本地准备的 20 分钟版本，不是首次运行所用的官方默认样例。

| 读取方式 | 预热 | 实际测量时长 | 解码总平均 FPS | 最差逐路窗口 FPS | 状态 |
|---|---:|---:|---:|---:|---|
| 按源时间戳读取 | 180 秒 | 900.05 秒 | 749.94 | 22.14 | `measured` |
| `--throughput` 不限速读取 | 30 秒 | 120.18 秒 | 790.01 | 23.15 | `measured` |

窗口配置为 60 秒；总平均按各窗口实际时长加权计算。两次均只记录性能，没有应用 FPS 验收条件。749.94 FPS 接近 30 路输入帧率之和，但最差窗口仍低于 25 FPS；790.01 FPS 是不限速的离线解码吞吐，两者都不包含推理。

本次整理后未重新上板执行；替换素材、编码参数或链路后，应重新运行并查看 `report.md`、`summary.json` 的逐路结果。初次运行先确认 `run_state.json` 正常完成、报告中没有失败或停滞；单看进程退出或总 FPS 不足以判断每路稳定性。

## 指标与限制

窗口 FPS 是完成帧数增量除以实际窗口时间。总平均和最差逐路窗口应分开看；`measured` 表示完成记录，并不表示达到某个业务帧率。

本地重复文件不等同于独立相机输入。文件循环、实时源中断、解码器内部缓冲和源端丢帧需要单独检查；日志为空不能证明端到端没有丢帧。完整指标定义见[指标说明](../../docs/metrics.md)。
