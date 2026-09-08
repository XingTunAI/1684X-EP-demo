# HDMI 视频墙

各路独立解码并执行 YOLOv8 检测，将最近完成的画面、同帧检测框和状态信息组成视频墙，通过系统 ffplay 输出至 HDMI。

![HDMI 视频墙实际运行截图](images/hdmi-wall.png)

这是历史实际运行截图：YOLOv8n INT8 batch 1，同一份本地视频由 32 个通道独立处理。各格 infer FPS 是当时的检测速率，顶部 HDMI preview 10 FPS 是显示更新上限。图用于说明布局和显示功能，不作为检测精度或并发达标依据；Device 1 只是当时的逻辑设备编号。

素材署名：Freestocks，[Cars On Highway - Free Stock Creative Commons Video](https://www.youtube.com/watch?v=-vLTFQv2_Vo)。截图所用视频经过重编码和循环延长，并叠加本程序的检测框；详见[截图素材](#截图素材)。

本目录包含 C++ 程序、检测器、拼屏模块、`build.sh`、`run.sh` / `run.py`。启动器负责成对管理检测程序和播放器。

## 依赖与输入

完成[环境准备](../../docs/setup.md)，另需可访问的 Linux 图形桌面和系统 ffplay。构建使用 SOPHON SDK、CMake 与 C++11；启动器需要 Python 3.9+。

| 数据 | 位置与要求 |
|---|---|
| 输入视频 | 默认 `third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/test_car_person_1080P.mp4`；自有文件用 `--input` 指定。 |
| YOLOv8 模型 | `--model s` / `--model n` 选择官方 INT8 batch 1 模型。 |
| 模型目录 | `third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/`。 |
| 类别文件 | 同官方样例下的 `datasets/coco.names`。 |
| 可选辅助模型 | `data/models/score_gate/score_gate_reducemax_f32.bmodel`，仅在开启 score gate 时使用。 |

`scripts/prepare.sh` 准备默认 YOLOv8s 模型、类别及示例视频，数据由 [SOPHON 官方脚本](https://github.com/sophgo/sophon-demo/blob/release/sample/YOLOv8_plus_det/scripts/download.sh)获取。已有模型、仅缺视频时，可按[资源获取与缺失文件恢复](../../data/README.md#官方资源)单独补齐。YOLOv8n 需要另运行 `scripts/prepare_yolov8n.sh`。辅助模型必须与检测器的输出布局一致；未准备时保持 `--score-gate off`。

当前 Python 启动器只提供 s/n 官方模型预设，不接受自定义 `--bmodel` 或 `--classnames` 参数。

## 首次运行

完成 SDK 和图形桌面准备后，从仓库根目录执行。首次使用官方视频即可，无需自行创建 `data/inputs/input.mp4`：

```bash
bash scripts/prepare.sh
bash demos/hdmi_wall/build.sh
bash demos/hdmi_wall/run.sh \
  --streams 1 --device 0 --model s --score-gate off --duration 20
```

`prepare.sh` 会安装基础工具并下载官方资源；已经准备好的环境和资源可跳过这一步。运行命令加 `--dry-run` 可以先查看计划，不启动检测或播放器。正常启动后显示带检测框的视频墙，控制台打印日志目录。

`--duration` 设置正式测量秒数；程序在各路就绪后另预热 3 秒，总耗时还包含初始化与收尾。省略时默认为 1800 秒。默认单路、device 0、YOLOv8s、score gate 关闭；参数支持范围可查看：

```bash
bash demos/hdmi_wall/run.sh --help
bash demos/hdmi_wall/run.sh --list-streams
```

`--streams N` 或 `-n N` 选择 1–32 内的路数。启动器为每路独立读取同一个输入文件；程序支持范围不表示某个路数已达到指定处理速度。

播放器优先使用用户设置的 `DISPLAY`、`XAUTHORITY`、`PLAYER_LIB_PATH` 等环境变量。以有权限访问设备和当前桌面的用户运行；如环境需要提升权限，启动和停止应保持一致的身份。

## 自有视频与截图素材

自有视频放到 `data/inputs/` 后显式选择；本地视频需要可读取的帧率信息，程序按源帧率调度：

```bash
bash demos/hdmi_wall/run.sh \
  --input data/inputs/input.mp4 --streams 4 --device 0 --duration 60
```

### 截图素材

页首画面使用 Freestocks 发布的 [Cars On Highway](https://www.youtube.com/watch?v=-vLTFQv2_Vo)。原视频获取入口及作者信息见原页面；通过作者允许的方式取得本地文件后，再传给 `--input`。素材声明为 Creative Commons，具体许可说明以原页面为准，保留作者署名和来源链接。

截图使用的本地版本为 1920×1080、25 FPS、H.264，重编码到约 8 Mbps，并将短片循环为 20 分钟。修改仅涉及编码和时长，没有增加拍摄场景。该衍生文件保留在本地，仓库只保存截图和来源说明。

首次运行使用上面的官方示例视频即可显示相同的视频墙布局；如需使用高速公路画面，准备原素材后替换输入。无需为了运行 Demo 额外生成 20 分钟文件，启动器会自动循环本地视频。

## 停止

```bash
bash demos/hdmi_wall/run.sh --stop
```

启动器按保存的进程身份结束对应程序和播放器，切换路数或模型前先停止当前运行。

## 首次运行排查

| 现象 | 检查位置与处理 |
|---|---|
| 提示 `Required file is missing` | 缺少 `hdmi_wall.pcie` 时重新构建；缺少 `/usr/bin/ffplay` 时准备系统播放器；视频、类别或模型缺失时按[获取说明](../../data/README.md#官方资源)补齐 |
| 检测已启动但 HDMI 没有画面 | 查看当前运行目录的 `player.log`，检查 `/usr/bin/ffplay`、图形桌面及当前用户的 `DISPLAY`、`XAUTHORITY` |
| 提示已有 HDMI 运行或锁被占用 | 先用本页 `--stop` 结束对应运行，再重新启动 |
| 等待 FIFO 超时 | 查看 `worker.log`；若尚未创建运行目录，启动日志位置会打印到终端 |

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
| `summary.json` / `streams.csv` | 解码、检测完成、窗口、处理阶段和异常统计 |
| `detections.jsonl` | 每帧检测项，包含类别、置信度、原图像素坐标 xyxy 及阶段耗时 |
| `preview.bgr` | 命名管道，传递原始拼屏画面，不是可独立播放的视频文件 |
| `wall.bmp` | 最近发布的整幅视频墙截图 |
| `status.json` | 各路最近帧号、检测数、推理 FPS、画面年龄和 stale 状态 |

`latest.json` 供停止入口定位对应运行；不要手动改成其他目录。截图和运行身份信息属于本地输出。

## 实际运行数据

以下为 2026-09-08 在 RK3588 + 单张 BM1684X 上完成的历史运行：YOLOv8n INT8 batch 1，32 路独立读取本页所述高速公路 1080p25 H.264 视频，`device-bgr`，预热 3 秒，开启 HDMI 预览。

| 路数 | 测量时长 | 检测完成总 FPS | 最慢一路全程 FPS | 显示更新上限 | 状态 |
|---:|---:|---:|---:|---:|---|
| 32 | 1,800 秒 | 270.93 | 8.43 | 10 FPS | `measured`，`records_complete=true` |

该次运行开启了 **score gate 辅助模型**；当前首次运行命令使用 YOLOv8s 且关闭 score gate，配置不同，不能直接套用表中速度。辅助模型不随仓库发布，未准备时保持关闭。

这是持续 30 分钟的运行与记录数据，不表示 32 路逐帧 25 FPS，也不表示检测准确率已验收。预览的 10 FPS 与检测完成 FPS 分开计数。本次目录及启动入口整理后未上板复测，当前默认配置应以实际生成的报告为准。

## 指标与限制

画布为 1920×1080，显示更新上限为 10 FPS；它与每路检测 FPS 是不同指标。画面中的 infer FPS 是最近处理速率，最终报告使用正式记录窗口。`stale` 按画面最近更新时间判断，表示预览内容较旧。

完整检测计数在结果记录写入后推进，包含解码和显示准备相关工作；程序不输出完整编码视频，也未实现跟踪。每路逐帧处理，当前没有可配置抽帧功能。

多个通道重复读取同一视频不能代表独立相机输入；预览画面年龄也不是相机到屏幕的延迟。详见[指标说明](../../docs/metrics.md)。
