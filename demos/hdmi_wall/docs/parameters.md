# HDMI 命令参数

[仓库首页](../../../README.md) / [HDMI 文档导航](../README.md#文档导航) / [实测索引](results.md)

本页解释运行设置；屏幕上的 DEC、INF、LAG、AGE 等观测数值见 [屏幕指标](wall-indicators.md)。命令中的参数只适用于列明的入口；封装入口的固定设置不能通过追加其他入口的参数覆盖。

## 先区分入口默认值

| 入口 | 设备 / 每卡路数 | 正式时长 | 推理与记录 |
|---|---|---:|---|
| `run.sh` | device 0 / 1 路 | 1800 秒 | s / gate off / full |
| `multi_run.sh` | devices 0,1 / 32 路 | 1800 秒 | s / gate off / full |
| `showcase.sh --mode showcase` | auto / 32 路 | 14400 秒 | s / gate on / summary |
| `showcase.sh --mode stress` | auto / Gen2 ×1 为 20 路，Gen3 ×2 为 32 路 | 14400 秒 | s / gate on / summary |
| `observe.sh` | auto / 32 路 | 300 秒 | 默认 inference on；summary |
| `benchmark.sh` | device 1 / 30 路 | 300 秒 | 历史 s / gate on 基准；full |

当前各入口默认采用 `latest`、送检年龄门槛 250 ms。**普通 multi_run 默认每路推理上限 5 FPS；run、showcase、observe 和 benchmark 默认推理限频为 0。** showcase / observe 固定开启本地首帧预取；普通 run / multi_run 默认关闭。benchmark 默认关闭预取、合并预算为 0，用于复现其历史基准。`--duration` 之外另有 3 秒预热、初始化与收尾。

## 普通单卡和多卡入口

下表除明确注明外，适用于 `run.sh` 和 `multi_run.sh`。

| 参数 | 含义和取值 |
|---|---|
| `--device 0` | 单卡入口选择一个设备编号。 |
| `--devices 0,1` | 多卡入口选择 1–4 个设备编号；普通 multi_run 不接受 `auto`。 |
| `--streams 32` / `-n 32` | 每卡路数，1–32；两卡各 32 路共 64 路。 |
| `--duration 60` | 正式测量秒数，正整数。 |
| `--input PATH` | 视频输入，默认官方 `test_car_person_1080P.mp4`；本地多路独立读取同一文件。RTSP 及输入清单限制见 [单设备输入](single-device.md#自有视频与截图素材)。 |
| `--model s` | 官方模型预设 `s` 或 `n`，均为 INT8 batch 1；不接受自定义 bmodel 路径参数。 |
| `--score-gate off` | `on` 使用辅助模型筛选候选输出，减少检测结果回读；需准备兼容的辅助 bmodel。普通入口默认 off。 |
| `--policy latest` | 解码与检测分开，待检测槽只保留最新帧。`all` 为串行逐帧对照。 |
| `--infer-fps 5` | 每路检测启动速率上限，非负有限数；0 不限频，5 表示至多约每 200 ms 启动一次。它不是实际完成速率保证。 |
| `--max-frame-age-ms 250` | 在解码后发布与取帧送检时淘汰超龄帧，0 关闭。latest 默认 250，all 默认 0；完成检测后的年龄仍可能超过此值。 |
| `--record-mode full` | full 保存逐帧检测 JSONL；summary 仅保存汇总，适合减少长测记录开销。 |
| `--preview-fps 3` | 每路检测缩略图回传上限，大于 0 且不超过 10，默认 10；不限制解码 / 模型推理。实际预览速率可能更低。 |
| `--output-buffer reuse` | 复用模型输出设备内存；baseline 每次分配 / 释放，为默认值。不影响模型输出内容。 |
| `--image-path auto` | 优先适用的设备 YUV 路径，不适用时回退 BGR；显式 yuv 不支持时会报错，bgr 用于对照。 |
| `--prime-local-decoders on` | 源时钟开始前预取各本地解码器首帧；保留实际时间戳，直播不适用。默认 off。 |
| `--gate-merge-budget-kib 64` | 每帧允许额外回读非候选行的合并预算，0–1024 KiB；非零要求 gate on。用于减少小块调用，不是总线带宽限额。 |
| `--fifo-timeout 90` | 等待预览管道启动的最大秒数，正整数；不是运行时长。 |
| `--telemetry-interval 5` | 仅多卡入口：每卡 TPU 采样间隔，秒；默认 0 关闭，启用后显示 TPU 并保存采样。 |
| `--device-config PATH` | 仅多卡入口：逐卡覆盖路数、合并预算、preview_fps、output_buffer 的 JSON，格式见 [每卡配置](showcase.md#每卡独立配置)。 |
| `--root /userdata/1684X-EP-demo` | 板端仓库根目录；Linux 通常自动定位，Windows 只做计划预览时需指定 Linux 路径。 |
| `--dry-run` | 打印计划，不启动任务。普通入口不因此确认设备或所需文件实际可用。 |
| `--stop` | 停止对应运行管理器记录的任务；使用与启动一致的权限。 |
| `--list-streams` | 仅 run.sh：查看支持的路数。 |
| `--help` | 查看该入口接受的参数。 |

`--policy all` 只接受 `--infer-fps 0 --max-frame-age-ms 0`，不能保留 latest 示例的限频和年龄参数。`infer-fps 0` 也不代表逐帧检测：latest 仍会丢弃被新帧覆盖或超龄的候选帧。

## 展示和压测入口

`showcase.sh` 接受 `--root`、`--devices`、`--duration`、`--mode`、`--profile`、`--telemetry-interval`、`--dry-run`、`--stop`、`--help`。

| 参数 / 固定配置 | 含义 |
|---|---|
| `--devices auto` | 从板端 sysfs 发现实际设备，也可指定 `0,1`。 |
| `--mode showcase` | 默认每卡 32 路，优先完整页面；Gen2 ×1 合并预算 128 KiB，Gen3 ×2 为 64 KiB。 |
| `--mode stress` | 使用已测负载档位：Gen2 ×1 为 20 路 / 64 KiB，Gen3 ×2 为 32 路 / 64 KiB。 |
| `--profile PATH` | 按设备覆盖路数和预算；这里不直接接受 `--streams`，格式见 [每卡配置](showcase.md#每卡独立配置)。 |
| `--telemetry-interval 5` | 默认每卡约 5 秒采样，0 关闭。 |
| 固定配置 | YOLOv8s、gate on、latest、infer-fps 0、年龄 250 ms、image auto、prime on、summary。 |

未知链路默认回退到 32 路 / 64 KiB，并不表示该链路已完成性能验证。showcase 的 `--dry-run` 会读取实际设备和链路，但不准备视频或启动 worker。具体启动、素材空间和停止方法见 [展示与压测](showcase.md)。

## 解码观测入口

`observe.sh` 接受 `--root`、`--devices`、`--streams`、`--duration`、`--inference`、`--compare-streams`、`--observe-preview-fps`、`--dry-run`、`--stop`、`--help`。

| 参数 | 含义 |
|---|---|
| `--inference off` | 运行解码基线，不创建推理实例；on 为真实模型推理负载组。 |
| `--compare-streams 0,1` | 选择 1–4 路放大对照，通道编号从 0 开始；不减少后台配置路数。 |
| `--observe-preview-fps 5` | 所选解码原图预览的采样上限，取值大于 0 且不超过 10，默认 5；不限制全部通道的解码速率。 |

普通 run / multi_run 也接受 `--observe-decode on` 和上述三项观测参数；`--inference off` 要求 `--observe-decode on --policy latest --score-gate off` 且合并预算为 0。为便于复现两组条件，优先使用 [observe 专用入口](decoder-observation.md)。

## 历史基准入口

`benchmark.sh` 接受 `--root`、`--devices`、`--streams`、`--duration`、`--gate-merge-budget-kib`、`--prime-local-decoders`、`--dry-run`、`--stop`、`--help`；参数含义与上表一致。它不接受 `auto`、`--mode` 或任意检测配置，默认复现 device 1 的 30 路基准。固定配置和完整命令见 [30 路基准](performance-30.md#每次使用统一基准入口)。

## 显示环境变量

| 设置 | 作用 |
|---|---|
| `DISPLAY=:0` | 指定板端 X11 显示目标；SSH 的 localhost:10.0 等通常是电脑端转发。 |
| `XAUTHORITY=/var/run/lightdm/root/:0` | 指定与目标桌面匹配的认证文件；此路径仅适用于已确认的本板 LightDM 会话。 |
| `SDL_VIDEODRIVER=x11` | 让 SDL 使用 X11 显示。 |
| `PLAYER_LIB_PATH=/usr/lib/aarch64-linux-gnu` | 为播放器选择系统动态库路径，避免混用其他库环境。 |
| `sudo env ...` | 以相应权限启动，并显式传入上述变量；ADB root 可省略 sudo。 |

逐步检查和完整可复制命令见 [ADB / SSH 与 HDMI 显示](display.md)。
