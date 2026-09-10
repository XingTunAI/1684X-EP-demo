# 单设备运行与输出

[仓库首页](../../../README.md) / [HDMI 文档导航](../README.md#文档导航) / [实测索引](results.md)

`run.sh` 管理一张卡的 worker 与系统 ffplay。默认 1 路、device 0、正式 1800 秒；需要多个设备页面时使用 [multi_run.sh](multi-device.md)。

## 依赖与输入

完成[环境准备](../../../docs/setup.md)，另需可访问的 Linux 图形桌面和系统 ffplay。构建使用 SOPHON SDK、CMake 与 C++11；启动器需要 Python 3.9+。

| 数据 | 位置与要求 |
|---|---|
| 输入视频 | 默认 `third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/test_car_person_1080P.mp4`；自有文件或单路 `rtsp://` / `rtsps://` 地址用 `--input` 指定。 |
| YOLOv8 模型 | `--model s` / `--model n` 选择官方 INT8 batch 1 模型。 |
| 模型目录 | `third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/`。 |
| 类别文件 | 同官方样例下的 `datasets/coco.names`。 |
| 可选辅助模型 | `data/models/score_gate/score_gate_reducemax_f32.bmodel`，仅在开启 score gate 时使用。 |

`scripts/prepare.sh` 准备默认 YOLOv8s 模型、类别及示例视频，数据由 [SOPHON 官方脚本](https://github.com/sophgo/sophon-demo/blob/release/sample/YOLOv8_plus_det/scripts/download.sh)获取。已有模型、仅缺视频时，可按[资源获取与缺失文件恢复](../../../data/README.md#官方资源)单独补齐。YOLOv8n 需要另运行 `scripts/prepare_yolov8n.sh`。辅助模型必须与检测器的输出布局一致；未准备时保持 `--score-gate off`。

当前 Python 启动器只提供 s/n 官方模型预设，不接受自定义 `--bmodel` 或 `--classnames` 参数。

## 首次运行

完成 SDK 和图形桌面准备后，从仓库根目录执行。以下直接启动命令用于有权访问板子桌面的终端；通过 ADB 或 SSH 启动前，先按[连接与显示说明](display.md)选择显示地址和认证文件。首次使用官方视频即可，无需自行创建 `data/inputs/input.mp4`：

```bash
bash scripts/prepare.sh
bash demos/hdmi_wall/build.sh
bash demos/hdmi_wall/run.sh \
  --streams 1 --device 0 --model s --score-gate off --duration 20
```

`prepare.sh` 会安装基础工具并下载官方资源；已经准备好的环境和资源可跳过这一步。运行命令加 `--dry-run` 可以先查看计划，不启动检测或播放器。正常启动后显示带检测框的视频墙，控制台打印日志目录。

`--duration` 设置正式测量秒数；程序在各路就绪后另预热 3 秒，总耗时还包含初始化与收尾。省略时默认为 1800 秒。默认单路、device 0、YOLOv8s、score gate 关闭、`latest`、不限检测启动帧率、送检前最大帧年龄 250 ms；参数支持范围可查看：

```bash
bash demos/hdmi_wall/run.sh --help
bash demos/hdmi_wall/run.sh --list-streams
```

`--streams N` 或 `-n N` 选择 1–32 内的路数。使用本地文件时，启动器为每路独立读取同一个输入文件；程序支持范围不表示某个路数已达到指定处理速度。

播放器优先使用用户设置的 `DISPLAY`、`XAUTHORITY`、`PLAYER_LIB_PATH` 等环境变量。以有权限访问设备和当前桌面的用户运行；如环境需要提升权限，启动和停止应保持一致的身份。

## 自有视频与截图素材

自有视频放到 `data/inputs/` 后显式选择；本地视频需要可读取的帧率信息，程序按源帧率调度：

```bash
bash demos/hdmi_wall/run.sh \
  --input data/inputs/input.mp4 --streams 4 --device 0 --duration 60
```

单路相机输入可直接传入 URI，启动器保留原地址并跳过本地视频文件检查：

```bash
bash demos/hdmi_wall/run.sh \
  --input 'rtsp://camera.example/live' --streams 1 --policy latest --duration 60
```

RTSP 不额外按软件源帧率节流。多个不同相机使用 C++ 程序的 `--inputs-file` 入口，每行一个不同地址；Python 启动器暂不提供这个参数，也不支持把同一 RTSP 地址重复为多路。RTSP 断流重连尚未实现。

## 停止

```bash
bash demos/hdmi_wall/run.sh --stop
```

启动器按保存的进程身份结束对应程序和播放器，切换路数或模型前先停止当前运行。

## 输出

```text
data/results/hdmi-wall/
  latest.json
  launcher.lock
  <run-id>/
    run.json
    worker.log
    player.log
    config.json
    summary.json
    streams.csv
    preview.bgr
    wall.bmp
    status.json
    stream_<id>/
      detections.jsonl
      summary.json
```

| 文件 | 内容 |
|---|---|
| `run.json` | 启动参数、进程身份及启动/结束状态 |
| `worker.log` / `player.log` | 检测程序与播放器日志 |
| `config.json` | 模型、输入、设备与处理配置 |
| `summary.json` / `streams.csv` | 解码、检测完成、主动丢帧、帧年龄、窗口、处理阶段和异常统计 |
| `detections.jsonl` | 每个已处理帧的检测项，包含类别、置信度、原图像素坐标 xyxy、帧年龄及阶段耗时 |
| `preview.bgr` | 命名管道，传递原始拼屏画面，不是可独立播放的视频文件 |
| `wall.bmp` | 最近发布的整幅视频墙截图 |
| `status.json` | 总解码 / 推理 FPS，以及每路帧号、检测数、DEC / INF、LAG / AGE、解码状态、画面年龄和 stale 状态 |

`latest.json` 供停止入口定位对应运行；不要手动改成其他目录。截图和运行身份信息属于本地输出。


普通单卡入口默认 `--record-mode full`，会写 `detections.jsonl`；选择 `summary` 时只保存汇总，不生成逐帧检测 JSONL。

## 指标与限制

画布为 1920×1080，显示更新上限为 10 FPS；它与每路检测 FPS 是不同指标。画面中的 infer FPS 是最近处理速率，最终报告使用正式记录窗口。屏上 `src age` 表示本地源计划到当前预览的年龄，RTSP 的 `dec age` 从解码完成计起，年龄超过 2 秒显示 `stale`。

正式检测完成计数在预览准备和可选记录写入后推进；屏幕 INF 则在 Detect 成功返回时计数，两者在计时边界可能不同。程序不输出完整编码视频，也未实现跟踪。`latest` 的 `frame` / `source_frame_id` 可以跳号，连续性应检查 `processed_index`；主动丢弃的帧不生成检测记录，也不算推理失败。

每路 summary 中，`dropped_overwrite` 是待检测旧帧被新帧覆盖的数量，`dropped_stale` 是送检前超龄丢弃的数量，`dropped_shutdown` 是收尾时放弃的待检测帧数量；`policy_drops` 为三者之和。`unprocessed_decoded_frames` 已扣除这些主动丢帧，正常收尾应为 0。`queue_high_watermark` 只统计等待槽，`latest` 最大为 1，不包含正在检测的帧。

检测记录中的 `frame_age_ms` 从解码完成计至 Detect 完成；`source_age_ms` 从本地帧计划读取时刻计至 Detect 完成，直播为 `null`。逐路 summary 提供同名年龄统计。`status.json` 的这两个年龄还包含预览准备和画面停留时间；原有 `age_seconds` 只表示最近预览更新后经过的时间，完整口径见[指标说明](../../../docs/metrics.md#hdmi-视频墙的抽帧与年龄)。

预览显示的 `policy_drops` 是最近一次提交检测画面时的计数；没有新检测画面时它不会刷新，完整丢帧总数以结束后的 summary 为准。

多个通道重复读取同一视频不能代表独立相机输入；预览画面年龄也不是相机到屏幕的延迟。详见[指标说明](../../../docs/metrics.md)。
