# 本地数据目录

[仓库首页](../README.md) / [文档索引](../docs/README.md)

`data/` 集中存放自行准备的数据和程序输出。除本说明外，其中视频、模型、SDK 包、日志和设备配置均保留在本地，不随源码发布。

```text
data/
  inputs/          自有视频、图像和实时输入清单
  models/          自有 bmodel、类别名称及辅助模型
  results/         各 demo 与诊断工具的运行输出
  sdk/             与主机匹配的本地 SDK 安装包
  README.md
```

## inputs

视频示例路径为 `data/inputs/input.mp4`，图像可使用 `data/inputs/image.jpg`。文件格式、分辨率和帧率要求由对应 demo 决定，先用 ffprobe 检查再运行。

支持输入清单的入口使用文本文件，每行一个路径或 RTSP URL；相对路径通常基于清单所在目录。实时地址及认证信息应留在本地清单中。

## models

自有模型示例为 `data/models/model.bmodel`，类别名称为 `data/models/classes.names`，每行一个名称。需确认目标芯片、SDK、batch、输入输出布局及类别顺序与程序匹配。

HDMI 可选 score gate 的约定位置是 `data/models/score_gate/score_gate_reducemax_f32.bmodel`。没有兼容的辅助模型时保持此功能关闭。

## 官方资源

官方提供视频、图片、类别文件和预编译模型，获取入口为 [YOLOv8 官方说明](https://github.com/sophgo/sophon-demo/tree/release/sample/YOLOv8_plus_det) 与 [YOLO26 官方说明](https://github.com/sophgo/sophon-demo/tree/release/sample/YOLO26)。实际下载由各样例的 [YOLOv8 download.sh](https://github.com/sophgo/sophon-demo/blob/release/sample/YOLOv8_plus_det/scripts/download.sh)、[YOLO26 download.sh](https://github.com/sophgo/sophon-demo/blob/release/sample/YOLO26/scripts/download.sh) 通过 `dfss` 完成。

先按[环境准备](../docs/setup.md)准备官方仓库，再从本仓库根目录选择需要的样例。只用一种 YOLO 时选择对应命令；同时使用两种时分别准备各自的默认资源：

```bash
# YOLOv8 视频、图片、类别和 BM1684X 模型
bash third_party/sophon-demo/sample/YOLOv8_plus_det/scripts/download.sh --BM1684X

# YOLO26 视频、图片、类别和 BM1684X 模型
bash third_party/sophon-demo/sample/YOLO26/scripts/download.sh --BM1684X
```

省略 `--BM1684X` 时只下载数据集，不下载模型。`scripts/prepare.sh` 是 YOLOv8 的完整准备辅助入口。官方资源保持原样例目录，不自动复制到 `data/`；下表中的 `<sample>` 为 `YOLOv8_plus_det` 或 `YOLO26`：

```text
third_party/sophon-demo/sample/<sample>/
  models/BM1684X/
  datasets/
```

| 资源 | 下载后位置（相对各样例目录） | 用途与适用入口 |
|---|---|---|
| `test_car_person_1080P.mp4` | `datasets/test_car_person_1080P.mp4` | 官方人物、车辆测试视频；两个 YOLO 主入口各用本家副本，解码和 HDMI 默认使用 YOLOv8 副本 |
| `test.tar.gz` | 解压到 `datasets/test/` | 测试图片；可选单张图片用于[诊断工具](../tools/diagnostics/README.md)的 `detector_check` |
| `coco.names` | `datasets/coco.names` | COCO 类别名称，须与所选模型匹配 |
| `coco128.tar.gz` | 解压到 `datasets/coco128/` | 量化校准图片及图像测试素材；普通视频运行不需要读取它 |
| `coco_val2017_1000.tar.gz` | `datasets/coco/val2017_1000/` 与 `datasets/coco/instances_val2017_1000.json` | 官方提供的 COCO 验证子集和标注；本仓库不自动计算 AP |
| BM1684X 模型包 | `models/BM1684X/` | YOLOv8 默认 `yolov8s_int8_1b.bmodel`；YOLO26 默认 `yolo26s_fp32_1b.bmodel` |

图片供单张检测检查或模型开发使用；两个 YOLO 主 `run.sh` 接收视频，不直接接收图片目录。自有素材使用 `data/inputs/`，自有模型使用 `data/models/`，通过对应命令行参数指定。视频是否满足分辨率、编码和帧率条件，以各 demo README 为准。

## 下载中断与完整性检查

官方脚本按 `datasets/`、`models/BM1684X/` 是否存在决定跳过，因此“目录存在”或再次运行脚本成功，不保证内容完整。先检查所需视频、类别、模型均存在且非空，再检查视频元数据、图像能否打开、模型能否被对应 SDK 加载。若发布方提供校验值，应比对校验值。

目录尚未创建时，可以重新运行对应官方下载命令；目录已存在但文件缺失或损坏时，按下面的方法修复。

缺少或损坏单个资源时，优先单独补齐。以下例子在 YOLO26 的 `datasets/` 内建立临时目录下载视频，检查后保留旧文件再替换；YOLOv8 改用 `YOLOv8_plus_det`。需已安装官方脚本使用的 `dfss` 和可用的 FFmpeg/ffprobe：

```bash
(
  set -e
  cd third_party/sophon-demo/sample/YOLO26/datasets
  repair_dir=$(mktemp -d .repair.XXXXXX)
  (
    cd "$repair_dir"
    python3 -m dfss --url=open@sophgo.com:sophon-demo/common/test_car_person_1080P.mp4
  )
  video=test_car_person_1080P.mp4
  test -s "$repair_dir/$video"
  ffprobe -v error -select_streams v:0 \
    -show_entries stream=codec_name,width,height,avg_frame_rate -of json "$repair_dir/$video"
  # 完整读取检查，避免仅元数据可读但视频尾部损坏
  ffmpeg -v error -xerror -i "$repair_dir/$video" -f null -
  if [ -e "$video" ]; then
    mv -- "$video" "$video.incomplete.$(date +%Y%m%dT%H%M%S).$$"
  fi
  mv -- "$repair_dir/$video" "$video"
)
```

其他数据使用同一公开前缀 `open@sophgo.com:sophon-demo/common/`，将文件名换成上表中的 `coco.names`、`test.tar.gz`、`coco128.tar.gz` 或 `coco_val2017_1000.tar.gz`。归档先用 `tar -tzf` 检查，再解压到临时目录，确认图片可读后补回缺失内容；保留已有自有素材。

模型包地址分别为 `open@sophgo.com:sophon-demo/YOLOv8_plus_det/BM1684X.tar.gz` 和 `open@sophgo.com:sophon-demo/YOLO26_det/BM1684X.tar.gz`。同样先下载并解压到独立临时目录，核对所需模型后再补回对应 `models/BM1684X/`，不要删除整个现有模型目录。单纯 `test -s`、压缩包可解压或视频元数据可读都不是完整性保证。

## HDMI 连续演示素材

官方 `test_car_person_1080P.mp4` 只有约 24.67 秒。在多路并发演示中，短片结束后集中关闭、重开解码器会造成结果断档。可以使用[循环素材准备脚本](../scripts/prepare_hdmi_loop.sh)先生成更长的本地文件，在板子的本仓库根目录执行：

```bash
bash scripts/prepare_hdmi_loop.sh --seconds 600
```

默认读取 YOLOv8 官方样例目录中的 `datasets/test_car_person_1080P.mp4`，生成：

```text
data/inputs/hdmi_wall_demo_loop_600s.mp4
data/inputs/hdmi_wall_demo_loop_600s.mp4.manifest.json
```

脚本使用系统 `/usr/bin/ffprobe` 检查输入，再由 `/usr/bin/ffmpeg` 通过 `-stream_loop` 和 `-c copy` 复制视频包，不重新编码、不调整分辨率或帧率；仅保留第一个视频流。系统工具的库环境与 SOPHON 库隔离。默认官方素材原样保持 **1920×1080、24 FPS**，本次生成的约 600 秒文件占用约 **1.09 GB**，请留足本地磁盘空间。

生成成功后，标准输出只打印产物的绝对路径。`manifest.json` 保存源文件和成品的 SHA256、请求时长、视频信息及生成命令；再次运行时，仅当源 SHA256、时长和成品 SHA256 都匹配本脚本的 manifest 才复用，不覆盖无法验证的已有文件。失败时清理本次临时文件；视频和 manifest 均保留在本地，不加入 Git。

输入、时长、输出路径均可指定，先用 `--dry-run` 查看命令：

```bash
bash scripts/prepare_hdmi_loop.sh \
  --input data/inputs/input.mp4 --seconds 900 \
  --output data/inputs/my_hdmi_loop_900s.mp4 --dry-run
```

实际生成时去掉 `--dry-run`。运行 HDMI demo 时通过 `--input data/inputs/hdmi_wall_demo_loop_600s.mp4` 使用产物，并确保**素材时长大于运行时长加预热时长**，例如 600 秒素材用于 300 秒正式测量。自有输入保留各自的原始帧率，不能统一按 24 或 25 FPS 解释。

本次板端已验证生成及缓存复用成功；对成品前 1184 帧进行 `framemd5` 比较，与原片 592 帧重复两遍的解码帧校验一致。该检查覆盖两个完整重复片段，不代表已逐帧校验整个 600 秒文件。

长文件中的画面仍是同一本地片段反复出现，不代表独立摄像头或真实直播输入。**短片 EOF 后的解码恢复问题尚未修复**；长素材是在本次运行期间避开文件末尾，运行超过素材长度仍会遇到 EOF 重开。其他实时输入的恢复能力和端到端延时需另行验证。

### 四小时展示素材的实际产物

[四小时多设备入口](../demos/hdmi_wall/docs/showcase.md)按“正式时长 + 3 秒预热 + 60 秒余量”向上取整到 600 秒，默认使用 15,000 秒素材。2026-09-09，本板已完成生成、校验并发布：

```text
data/inputs/hdmi_wall_demo_loop_15000s.mp4
data/inputs/hdmi_wall_demo_loop_15000s.mp4.manifest.json
```

| 项目 | 本次实际值 |
|---|---|
| 成品大小 | 27,340,576,143 字节，约 27.34 GB / 25.46 GiB |
| 探测时长 | 15,000.000203 秒 |
| 视频格式 | H.264，yuv420p，1920×1080 |
| 标称帧率 | `r_frame_rate=24/1` |
| 源文件 SHA256 | `dfd39ae2aff14f43bb170a64f751b4268faaafb782a870c74a327dc82134ac9f` |
| 成品 SHA256 | `b1c80c58eacc785983077ba6a71bbdfbac0afa5cd27d179dc586cf8e293522b1` |

新生成前，脚本按**源文件字节数 ÷ 探测时长 × 目标时长**估算输出，再加 **5% + 1 GiB** 余量检查输出盘空间。此次实际打印的需求为 **30,042,628,319 字节（约 27.98 GiB）**；这是预估生成需求，与最终成品大小分开。空间不足时不启动 ffmpeg、不创建本次部分素材；已验证匹配的缓存先复用，不因磁盘剩余小于新生成需求而被拒绝。

首次大文件准备会进行视频包复制、faststart 整理和完整哈希校验，产生大量磁盘 I/O，**不要与性能压测同时进行**。再次使用缓存仍需完整读取源文件和成品计算 SHA256，不是看到文件存在就立即跳过；请等待校验结束。准备和校验时间不计入正式运行时长。视频与 manifest 都留在本地、不入 Git；素材准备完成不代表四小时设备测试已经完成或验收通过。

## HDMI 截图所用的可选素材

仓库 HDMI 效果截图使用过 Freestocks 发布的 [Cars On Highway - Free Stock Creative Commons Video](https://www.youtube.com/watch?v=-vLTFQv2_Vo)。这是可选外部素材，获取方式及使用授权以原作者页面为准，与官方默认测试视频分开。

截图所用本地版本经过 H.264、1920×1080、25 FPS、约 8 Mbps 转换，并循环组成约 20 分钟文件；原始资源不保证具备这些参数。自行取得后放在 `data/inputs/`，先检查格式再按 [HDMI README](../demos/hdmi_wall/README.md)指定输入；仓库不附带该视频文件。

## results

一般输出位置：

| 程序 | 位置 |
|---|---|
| 解码 | `data/results/decode/<run-id>/` |
| YOLOv8 主入口 | `data/results/yolov8/<run-id>/`，每卡结果在 `device_<id>/<backend-run-id>/` |
| YOLO26 主入口 | `data/results/yolo26/<run-id>/`，每卡结果在 `device_<id>/` |
| HDMI 视频墙 | `data/results/hdmi-wall/<run-id>/` |
| HDMI 多设备分页 | `data/results/hdmi-wall-multi/<run-id>/`，各卡在 `device_<id>/`，页面状态见根目录 `viewer-status.json` |
| 诊断 | `data/results/inference-diagnostics/`、`bandwidth/`、`tpu/`、`decode-capacity/` |

两个 YOLO 主入口的 `--output` 指定父目录，每次自动创建唯一运行目录；根级 `run.json`、`summary.json` 记录进程状态，`device_<id>.log` 保存后端日志，各卡目录保存检测统计和逐帧 JSONL。具体字段见 [YOLOv8](../demos/yolov8/README.md) 和 [YOLO26](../demos/yolo26/README.md)。

HDMI 回传开关另保存 `readback-control.json`、`readback-events.jsonl`；每卡 `status.json` 记录累计解码 / 推理次数和模型 / 预览回传字节数。含 ON / OFF 切换的整场 summary 不能直接当作单一模式的性能，按 [区间统计](../demos/hdmi_wall/docs/readback-toggle.md#怎么读数)拆分。新低回传 profile 是源码配置文件，不是额外素材，位于 `demos/hdmi_wall/profiles/`。

底层工具的输出约定可能不同，直接调用前查看对应说明。不要让不同运行复用同一结果目录。输出可能包含输入路径、设备信息、逐帧结果和截图，应保持本地存储。

## sdk

放置与主机、内核和 SDK 版本匹配的安装包。`scripts/install_sdk.sh data/sdk` 只适用于脚本列出的包名组合，运行前按[环境说明](../docs/setup.md)检查。

文件迁移或备份时可以记录 SHA256，确认模型和输入未改变；哈希清单也保存在本地数据目录。
