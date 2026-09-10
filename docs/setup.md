# 环境与依赖准备

[仓库首页](../README.md) / [文档索引](../docs/README.md)

以下操作面向 Linux ARM64 的 RK3588 主机与 BM1684X PCIe 设备。下方明确标注的 ADB、SSH 连接命令在电脑终端执行；连接成功后的 SDK 安装、构建和运行命令都在板端 Linux 执行。Windows 可用于编辑文件，以及使用 Python 运行两个 YOLO 入口的 `--dry-run` 查看命令计划；它不会启动硬件任务。

## 从电脑进入设备

ADB 和 SSH 二选一即可。进入后终端提示符通常变为 `linaro@Rockchip:...$` 或 root 的 `#`；以 `id` 输出确认实际用户，不要只凭提示符判断权限。

### 使用 ADB

电脑需要安装 Android Platform Tools，并能调用 `adb`；板子的固件需启用 ADB，USB 线连接到支持 ADB 的接口。在**电脑终端**（如 Windows PowerShell）执行：

```powershell
adb devices -l
adb shell
```

先确认列表中的目标设备状态为 `device`。只有一台在线设备时可直接使用 `adb shell`；多台设备时，将下方 `YOUR_SERIAL` 替换为 `adb devices -l` 第一列的实际序列号，再执行：

```powershell
adb -s YOUR_SERIAL shell
```

连接后，在**板端 Linux shell** 执行：

```bash
id
hostname -I
```

`id` 输出中的 `uid=0(root)` 表示当前是 root；ADB 是否默认以 root 登录由固件决定。`hostname -I` 显示板子的网络地址，可用于下面的 SSH 连接；请选择电脑能够访问的板端地址。执行 `exit` 可退出板端 shell，回到电脑终端。

### 使用 SSH

板子与电脑需要网络可达，rootfs 中应已有可登录的用户，并已启用 SSH 服务。下面以本次固件的 `linaro` 用户为例；若固件使用其他用户名，替换为实际用户。将 `BOARD_IP` 替换为板端 `hostname -I` 查到的可达地址，在**电脑终端**执行：

```powershell
ssh -x linaro@BOARD_IP
```

首次连接核对设备的主机密钥指纹，按终端提示使用自己的凭据登录。`-x` 关闭本次 SSH 连接的 X11 转发，避免播放器窗口被转发到电脑。登录后，在**板端 Linux shell** 执行：

```bash
id
```

普通用户安装系统包时需要相应的 `sudo` 权限。ADB 或 SSH 登录成功仅表示终端可用，不代表获得 HDMI 桌面的显示权限；板端桌面显示地址与认证文件的选择见 [通过 ADB 或 SSH 运行到 HDMI](../demos/hdmi_wall/docs/display.md)。

## 取得仓库

本次板子使用 `/userdata/1684X-EP-demo`，把仓库、编译产物和下载资源放在较大的 `/userdata` 分区。以下命令都在**板端 Linux shell** 执行。先确认可用空间和当前用户的目录权限：

```bash
df -h /userdata
ls -ld /userdata
```

若仓库已经放好，直接进入：

```bash
cd /userdata/1684X-EP-demo
test -w . && echo '仓库目录可写' || echo '当前用户不能写入仓库目录'
```

构建和下载资源需要当前用户能写入仓库及相应子目录。如果仓库由 ADB 的 root 用户创建，而后改用 SSH 普通用户，请先检查权限；无写权限时由管理员为该仓库配置合适的目录所有者或写权限，再继续。

首次使用且已安装 Git、当前用户可写入 `/userdata` 时执行：

```bash
cd /userdata
git clone https://github.com/XingTunAI/1684X-EP-demo.git
cd 1684X-EP-demo
```

后续命令均在仓库根目录执行。仓库也支持其他可写路径，无需复制到固定位置。Git 仅取得本项目源码和文档；SDK 安装包、官方依赖源码、模型和测试视频不会随本仓库的 `git clone` 下载，需要按下文分别准备。刷写 rootfs 后也应重新检查系统内的驱动、SDK 和构建工具，即使 `/userdata` 中的文件仍在。

## 更新已有仓库

先通过对应入口停止正在运行的 Demo，在板端仓库目录执行：

```bash
git status --short
git pull --ff-only
bash demos/hdmi_wall/build.sh
```

有本地改动时先保存、比较并处理冲突，不用强制重置覆盖。这里只示例 HDMI 构建；更新其他 Demo 时使用其 build.sh。模型和素材保持在本地，不因文档更新重新下载；刷写 rootfs 后仍要重新检查系统 SDK 和显示环境。

## SDK 和构建工具

按硬件和官方 SDK 版本准备驱动、libsophon、SOPHON FFmpeg/OpenCV 及相应开发文件。C++ 程序需要 `bmruntime_interface.h`、`bmcv_api_ext.h`、`bmlib_runtime.h` 等头文件。

需要 CMake 3.13+、C++11 编译器、Make、pkg-config、Git 和 Python 3.9+。视频墙另需 Linux 图形桌面与系统 ffplay。

SDK 安装包不包含在本仓库中，需通过所用模组或 SDK 对应的算能技术支持渠道取得，并确认适用于当前主机、内核及硬件。已安装匹配 SDK 的系统可直接进入下方检查。

本仓库的 [install_sdk.sh](../scripts/install_sdk.sh) 仅适用于以下固定包名组合，不是空白系统的通用一键安装程序：

```text
sophon-driver_0.5.1-LTS-rk3588fix2_arm64.deb
sophon-libsophon_0.5.1-LTS_arm64.deb
sophon-libsophon-dev_0.5.1-LTS_arm64.deb
sophon-mw-sophon-ffmpeg_0.14.0_arm64.deb
sophon-mw-sophon-ffmpeg-dev_0.14.0_arm64.deb
sophon-mw-sophon-opencv_0.14.0_arm64.deb
sophon-mw-sophon-opencv-dev_0.14.0_arm64.deb
```

确认这组版本和硬件补丁与系统相符后，将文件放在 `data/sdk/` 中执行：

```bash
bash scripts/install_sdk.sh data/sdk
```

`sophon-libsophon-dev` 提供构建所需的头文件和 `libsophon-config.cmake`，仅安装运行库不能在干净 rootfs 上编译 Demo。其他版本按对应 SDK 的官方安装流程操作。安装完成后按驱动要求重启，再进行检查。

## 开始前检查

在 Linux 主机终端执行以下只读命令。尚未安装公共工具时，先完成[公共工具与依赖源码](#公共工具与依赖源码)中的工具安装，再回到本节检查：

```bash
bm-smi --noloop
command -v ffmpeg ffprobe cmake python3
cmake --version
python3 --version
ffmpeg -hide_banner -decoders
ls -ld /opt/sophon/sophon-ffmpeg-latest/lib/cmake \
  /opt/sophon/sophon-opencv-latest/lib/cmake/opencv4
```

| 检查项 | 通过标准 |
|---|---|
| 设备 | `bm-smi` 正常列出本次要使用的设备；单卡至少有一个，双卡需能看到两个。按实际编号选择 `--devices` 或 `--device` |
| 命令与版本 | `command -v` 列出的四个命令均有路径；CMake 至少 3.13，Python 至少 3.9 |
| 硬件解码 | FFmpeg 解码器列表包含与输入匹配的 `h264_bm` 或 `hevc_bm`；默认 H.264 样例需要 `h264_bm` |
| SDK 开发文件 | 上述两个默认 CMake 目录存在，构建配置能找到 FFMPEG、OpenCV 和 libsophon；安装在其他位置时按 SDK 路径配置 |

构建脚本默认查找以上 SOPHON FFmpeg/OpenCV 路径，libsophon 通过 CMake 的 `find_package(libsophon)` 查找。若 SDK 安装位置不同，构建时传入相应的 `FFMPEG_DIR`、`OpenCV_DIR`、`libsophon_DIR`；这些值应指向实际 SDK 的 CMake 配置目录。

HDMI 还要求板子的图形显示服务和显示器可用、播放器具有访问该显示会话的权限，并安装系统播放器。可以在已登录的图形桌面运行；本次固件的 LightDM 登录界面也已通过显式指定对应显示地址和认证文件验证，操作见 [通过 ADB 或 SSH 运行到 HDMI](../demos/hdmi_wall/docs/display.md)。`prepare.sh` 不安装播放器；使用 apt 的系统缺少 `/usr/bin/ffplay` 时执行：

```bash
sudo apt update
sudo apt install -y ffmpeg
```

此处系统 FFmpeg 包用于提供 `/usr/bin/ffplay`，硬件解码和 C++ 构建仍使用 SOPHON SDK。安装后按上表重新核对 SOPHON FFmpeg 的命令路径和解码器。在已登录桌面的终端检查：

```bash
test -x /usr/bin/ffplay && echo 'ffplay: OK'
printf 'DISPLAY=%s\n' "$DISPLAY"
```

应能看到 `ffplay: OK` 和有效的 `DISPLAY`。仅有 ADB、SSH 终端或只设置一个 `DISPLAY` 字符串，不代表播放器已获得桌面访问权限。SSH 中的 `DISPLAY=localhost:11.0` 一类地址表示 X11 转发；改为板端 `:0` 后仍需匹配的认证文件，当前用户的 `.Xauthority` 不一定适用于 LightDM 登录界面。具体启动和排查见 [通过 ADB 或 SSH 运行到 HDMI](../demos/hdmi_wall/docs/display.md)。

## 官方依赖与模型

资源来自 [SOPHGO 官方 SOPHON-DEMO](https://github.com/sophgo/sophon-demo)。可以按需准备某个样例，也可以使用下方的 YOLOv8 一键流程。

### 公共工具与依赖源码

仅需要 YOLO26 时，执行本节后选择 YOLO26 下载命令即可，无需先下载 YOLOv8 模型。以下命令面向使用 apt 的 Linux 主机；已有工具可跳过安装部分：

```bash
sudo apt update
sudo apt install -y git cmake make g++ pkg-config python3 python3-pip

mkdir -p third_party
if [ ! -d third_party/sophon-demo ]; then
  git -c core.autocrlf=false clone --depth 1 -b release \
    https://github.com/sophgo/sophon-demo.git third_party/sophon-demo
fi
test -d third_party/sophon-demo/sample
```

已有 `third_party/sophon-demo/` 时复用当前源码，不自动执行 `git pull`。若目录内没有 `sample/` 或下方选定的样例，先核对本地 checkout 是否完整、是否包含该样例。

### 选择资源下载

按需要执行其中一组；同时使用两种 YOLO 时可分别执行。

```bash
# YOLOv8：官方 BM1684X 模型、类别、视频与图像数据
test -d third_party/sophon-demo/sample/YOLOv8_plus_det && \
  bash third_party/sophon-demo/sample/YOLOv8_plus_det/scripts/download.sh --BM1684X
```

```bash
# YOLO26：官方 BM1684X 模型、类别、视频与图像数据
test -d third_party/sophon-demo/sample/YOLO26 && \
  bash third_party/sophon-demo/sample/YOLO26/scripts/download.sh --BM1684X
```

对应渠道为 [YOLOv8 官方下载脚本](https://github.com/sophgo/sophon-demo/blob/release/sample/YOLOv8_plus_det/scripts/download.sh)和 [YOLO26 官方下载脚本](https://github.com/sophgo/sophon-demo/blob/release/sample/YOLO26/scripts/download.sh)。官方脚本会调用 `pip3` 安装或更新 `dfss`，通过 SOPHGO 公开资源服务获取文件；需网络和当前 Python 环境的安装权限。

### YOLOv8 一键流程

需要一次完成公共工具、源码、YOLOv8 模型和数据准备时，可用以下命令代替上述手动步骤：

```bash
bash scripts/prepare.sh
```

该脚本会安装基础构建工具，首次克隆官方 release 分支，并下载 YOLOv8 模型、类别文件和公开示例数据。它需要网络及系统包安装权限，不替代驱动和 SDK 安装。

官方下载内容保持原样例目录：

```text
third_party/sophon-demo/
  sample/YOLOv8_plus_det/
    models/BM1684X/
    datasets/
  sample/YOLO26/
    models/BM1684X/
    datasets/
```

示例视频、测试图片、COCO128 和 COCO 验证子集都保存在对应样例的 `datasets/`，不会自动复制到 `data/inputs/`。具体文件、用途、资源渠道和下载中断恢复步骤见[本地数据与官方资源清单](../data/README.md)。

需要可选 YOLOv8n 模型时：

```bash
bash scripts/prepare_yolov8n.sh
```

此脚本下载并核验官方模型；输入与模型文件均保留在本地。

## 文件准备

自有视频放到 `data/inputs/`，自有 bmodel 和类别名称放到 `data/models/`，参见[data 分类](../data/README.md)。先检查视频：

```bash
ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,width,height,avg_frame_rate,bit_rate,pix_fmt,color_space,color_range \
  -show_entries format=duration -of json data/inputs/input.mp4
```

每个 demo 的输入约束不同，运行前以其 README 为准。特别是本板官方车辆素材为 1080p24，普通 YOLOv8 主入口固定要求 1080p25，需显式指定兼容的本地文件；HDMI 按其自身输入规则运行。模型必须面向 BM1684X，且 batch、输入布局、输出格式、类别顺序与程序相符。

Windows 编辑的 Shell 脚本应使用 LF 换行后再在 Linux 执行。默认在各 demo 的 `build/` 生成程序；源码更新后重新构建对应 demo。

## 选择入口

- [解码](../demos/decode/README.md)：Python 入口，无需 C++ 构建。
- [YOLOv8](../demos/yolov8/README.md)：检测记录与可选编码。
- [HDMI 视频墙](../demos/hdmi_wall/README.md)：实时拼屏与同帧检测框。
- [YOLO26](../demos/yolo26/README.md)：紧凑检测结果。
- [诊断工具](../tools/diagnostics/README.md)：单独检查模型、结果和传输。

YOLOv8 和 YOLO26 使用各自的 `run.sh --devices 0` 或 `--devices 0,1` 选择设备，使用 `--duration` 设置后端测量窗口，无需切换到另一个多卡 demo。

各目录的 README 同时说明输入、命令和输出；不另维护平行的逐 demo 文档。

## 常见问题

| 症状 | 先检查什么 |
|---|---|
| `bm-smi` 找不到设备，或所选编号不存在 | 确认驱动版本、安装后重启情况及设备枚举；回到“开始前检查” |
| 找不到 `h264_bm`，或提示解码器不可用 | 用 `command -v ffmpeg` 确认正在使用 SOPHON FFmpeg，检查其解码器列表 |
| CMake 提示找不到 FFMPEG、OpenCV 或 libsophon | 检查 SDK 开发包及实际 CMake 配置目录；不要用普通桌面 OpenCV 代替 SOPHON 版本 |
| 提示模型、类别或视频文件缺失 | 对照所选 Demo 的默认路径，按[资源说明](../data/README.md)补齐；目录存在不代表下载完整 |
| HDMI 没有窗口或不能连接显示 | 检查图形桌面、`/usr/bin/ffplay` 和显示权限，按 [HDMI 文档](../demos/hdmi_wall/README.md)排查 |
