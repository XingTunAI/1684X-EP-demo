# YOLOv8 跑通准备文档

本文说明如何在 RK3588 + 双 BM1684X PCIe 从卡环境中编译和运行官方 YOLOv8 C++ demo。

文档中的 `<repo-dir>` 表示本仓库目录。官方 `sophon-demo` 默认放在 `<repo-dir>/third_party/sophon-demo`；如需使用其它位置，可手动设置 `$SOPHON_DEMO_DIR`。

## C++ 版优先级

基础运行环境通常包含：

- `sophon-driver`
- `sophon-libsophon`
- `sophon-ffmpeg`
- `sophon-opencv`

C++ 编译需要 `sophon-libsophon-dev` 中的开发头文件：

```text
bmruntime_interface.h
bmcv_api_ext.h
bmlib_runtime.h
```

这些文件通常来自 `sophon-libsophon-dev`，也可从同版本 SDK 包中的 libsophon `include` 目录获取。C++ BMCV/BMRuntime 路线依赖较少，适合作为首轮验证路径。

## 0. 硬件识别

板端 `lspci -vvv` 应能看到两张算能 PCIe 设备，并绑定 `bmdrv`：

```text
0001:11:00.0 Processing accelerators: Device 1f1c:1686
0004:41:00.0 Processing accelerators: Device 1f1c:1686
Kernel driver in use: bmdrv
```

## 1. 安装 SOPHON deb

将 `sophon-debs-0.5.1_LTS` 放在本仓库根目录，然后执行：

```bash
bash scripts/install_sophon_debs.sh sophon-debs-0.5.1_LTS
sudo reboot
```

重启后确认：

```bash
bm-smi
lspci | grep -i -E "1f1c|processing"
```

## 2. 准备官方 YOLOv8 demo、模型和测试数据

```bash
bash scripts/prepare_yolov8_demo.sh
```

该脚本会执行以下操作：

- 安装 `git/cmake/make/g++/pkg-config/python3-pip`。
- 拉取官方 `sophon-demo` 的 `release` 分支。
- 进入 `sophon-demo/sample/YOLOv8_plus_det`。
- 下载 BM1684X 的 YOLOv8 bmodel、COCO 类别文件和测试视频。

准备完成后应存在：

```text
third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel
third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_fp32_1b.bmodel
third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/test_car_person_1080P.mp4
third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/coco.names
```

## 3. 编译 YOLOv8 C++ demo

### 板端编译入口

RK3588 是主机，BM1684X 是 PCIe 从卡；推荐直接在 RK3588 板端编译和运行。进入本仓库目录：

```bash
cd <repo-dir>
bash scripts/build_yolov8_cpp.sh
```

注意：RK3588 是主机，BM1684X 是 PCIe 从卡，所以这里使用官方样例的 `pcie` 编译路径，输出文件是：

```text
third_party/sophon-demo/sample/YOLOv8_plus_det/cpp/yolov8_bmcv/yolov8_bmcv.pcie
```

如果脚本报缺少：

```text
bmruntime_interface.h
bmcv_api_ext.h
bmlib_runtime.h
```

如果缺少以上文件，说明系统未安装 libsophon 开发包。需要从同版本 SDK 补装 `sophon-libsophon-dev`，或将 SDK 中的 include 目录复制到：

```text
/opt/sophon/libsophon-current/include
```

## 4. 单卡运行

当前单卡验证先运行 `dev_id=0`。该设备对应 `0004:41:00.0`，链路为 PCIe Gen2 x1：

```bash
bash scripts/run_yolov8_cpp_single.sh 0
```

后续再单独回归 `dev_id=1`。该设备对应 `0001:11:00.0`，链路为 PCIe Gen3 x1：

```bash
bash scripts/run_yolov8_cpp_single.sh 1
```

输出视频位置：

```text
third_party/sophon-demo/sample/YOLOv8_plus_det/cpp/yolov8_bmcv/results/output.mp4
```

在 HDMI + XFCE/Xorg 环境中，可将结果视频直接播放到 HDMI：

```bash
cd <repo-dir>
bash scripts/play_yolov8_hdmi.sh \
  "third_party/sophon-demo/sample/YOLOv8_plus_det/cpp/yolov8_bmcv/results/output.mp4"
```

## 5. 双卡并发运行

单卡验证通过后，运行双卡并发：

```bash
bash scripts/run_yolov8_cpp_dual.sh
```

`run_yolov8_cpp_dual.sh` 内部会调用动态分配脚本，并明确指定：

```text
task0 -> dev_id 1 -> 主卡/Gen3
task1 -> dev_id 0 -> 副卡/Gen2
```

多路输入可通过 `INPUTS` 指定视频列表，通过 `DEVICE_SPECS` 指定设备权重：

```bash
DEVICE_SPECS="1|2|primary|0001:11:00.0|Gen3_x1,0|1|secondary|0004:41:00.0|Gen2_x1" \
INPUTS="<main-video>,<side-video-1>,<side-video-2>" \
bash scripts/run_yolov8_cpp_dynamic.sh
```

上述配置的分配结果为：

```text
<main-video>   -> dev_id 1
<side-video-1> -> dev_id 1
<side-video-2> -> dev_id 0
```

每个任务会复制一份独立工作目录到 `${RUN_ROOT}/<run_id>/`，输出文件分别保存在各自的 `results/output.mp4`，不会互相覆盖。`RUN_ROOT` 默认在系统临时目录下，也可以通过环境变量改到其他位置。

双卡运行完成后，可指定播放其中一路结果到 HDMI：

```bash
bash scripts/play_yolov8_hdmi.sh \
  "${RUN_ROOT}/<run_id>/task0_dev1/results/output.mp4"
```

如需为不同卡指定不同工作，可按输入顺序和 `DEVICE_SPECS` 权重配置：

```text
主路/高码率/重要画面 -> dev_id 1 -> 0001:11:00.0 -> Gen3 x1
副路/低码率/备用画面 -> dev_id 0 -> 0004:41:00.0 -> Gen2 x1
```

注意：官方 C++ 样例采用“检测后写文件”模式，并非实时 HDMI 预览。本仓库提供补丁，在 `cpp/yolov8_bmcv/main.cpp` 的 `draw_result` 后增加实时显示输出。

补丁应用后，C++ 可执行文件支持：

```text
--display=1
--display_wait=1
--display_width=1280
--display_fifo=<path>
--save_video=0
```

如果 SOPHON OpenCV 未启用 GTK/X11 HighGUI 后端，推荐使用 `--display_fifo` 配合系统 `ffplay`。本仓库的 HDMI 预览脚本默认使用该方式，并已在 `dev_id=1` 主卡和 `dev_id=0` 副卡上完成短时验证。

应用补丁并重新编译：

```bash
cd <repo-dir>
bash scripts/patch_yolov8_cpp_hdmi_display.sh
bash scripts/build_yolov8_cpp.sh
```

运行 HDMI 实时预览：

```bash
SAVE_VIDEO=0 DISPLAY_SIZE=960x540 bash scripts/run_yolov8_cpp_hdmi_preview.sh 1
```

其中最后一个参数为卡号：`1` 表示主卡/Gen3，`0` 表示副卡/Gen2。

另开一个终端观察：

```bash
watch -n 1 bm-smi
```

## 6. 常见问题

### 找不到 `/opt/sophon/sophon-ffmpeg-latest`

说明 sophon-ffmpeg deb 没安装成功，重新执行：

```bash
sudo apt install ./sophon-debs-0.5.1_LTS/sophon-mw-sophon-ffmpeg_0.14.0_arm64.deb
sudo apt install ./sophon-debs-0.5.1_LTS/sophon-mw-sophon-ffmpeg-dev_0.14.0_arm64.deb
```

### 找不到 `/opt/sophon/sophon-opencv-latest`

说明 sophon-opencv deb 没安装成功，重新执行：

```bash
sudo apt install ./sophon-debs-0.5.1_LTS/sophon-mw-sophon-opencv_0.14.0_arm64.deb
sudo apt install ./sophon-debs-0.5.1_LTS/sophon-mw-sophon-opencv-dev_0.14.0_arm64.deb
```

### `bm-smi` 看不到两张卡

确认驱动和 PCIe 状态：

```bash
lspci | grep -i -E "1f1c|processing"
lsmod | grep -i -E "bm|sophon"
dmesg | grep -i -E "bm|sophon|pci" | tail -n 100
```

### 官方 Python YOLO 暂不作为执行路径

当前首版验证只跑 C++ 版。官方 Python demo 可保留在 `third_party/sophon-demo` 中作参考，但不作为本项目默认执行路径。如果环境中未安装 `sophon-sail`，Python 版可能在以下位置失败：

```python
import sophon.sail as sail
```

因此基础验证、单卡、多卡和 HDMI 相关流程均优先使用 C++ 版完成。

### 从 Windows 传到板端后脚本报 `/bin/bash^M`

这是 CRLF 换行导致的。执行：

```bash
find ~/1684X-EP-demo -type f -name "*.sh" -exec sed -i 's/\r$//' {} \;
```

然后重新运行脚本。

### `download.sh` 后 `models/BM1684X` 不存在

如果 `datasets/test_car_person_1080P.mp4` 和 `datasets/coco.names` 已存在，但模型目录为空，可手动下载模型包：

```bash
cd ~/1684X-EP-demo/third_party/sophon-demo/sample/YOLOv8_plus_det/models
python3 -m dfss --url=open@sophgo.com:sophon-demo/YOLOv8_plus_det/BM1684X.tar.gz
tar xvf BM1684X.tar.gz
rm BM1684X.tar.gz
cd ..
find models/BM1684X -maxdepth 1 -type f -name "*.bmodel" -printf "%f %s\n" | sort
```

### `--dev_id=1` 时 `bm-smi` 仍显示 TPU-ID 0 进程

官方样例和底层库启动时会枚举多张设备，并尝试初始化 on-chip CPU/usercpu 相关资源。因此同一个进程可能在非目标卡上出现少量内存占用。判断实际推理设备时，以目标卡的 `TPU-Util`、功耗和内存增长为准。

### BMCV handle 警告

运行过程中可能出现：

```text
[BMCV][error] Error, please check if the handle used for handle and bm_image are the same
```

如果程序仍持续输出 `det_nums` 并最终打印 `SUMMARY: yolov8 test`，说明推理链路已经跑通。该问题后续可通过修改官方 C++ 样例的 BMCV handle 和输出路径绑定来进一步收敛。
