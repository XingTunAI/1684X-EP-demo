# YOLOv8 跑通准备文档

本文用于在 RK3588 + 两张 BM1684X PCIe 从卡上先跑通官方 YOLOv8 C++ demo。

服务器侧工作目录：

```text
/data/users/ubuntu/workspace/Sophgo/bm1684/1684X-EP-demo
```

## 为什么先跑 C++ 版

当前 `sophon-debs-0.5.1_LTS` 中已有：

- `sophon-driver`
- `sophon-libsophon`
- `sophon-ffmpeg`
- `sophon-opencv`

但暂时没有 `sophon-sail`，所以先跑 C++ BMCV/BMRuntime 路线。Python SAIL 版等补齐 `sophon-sail` 后再跑。

## 0. 前置状态

板端 `lspci -vvv` 已看到两张算能设备，并且都绑定了 `bmdrv`：

```text
0001:11:00.0 Processing accelerators: Device 1f1c:1686
0004:41:00.0 Processing accelerators: Device 1f1c:1686
Kernel driver in use: bmdrv
```

## 1. 安装 SOPHON deb

把 `sophon-debs-0.5.1_LTS` 放在本仓库根目录，然后执行：

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

这个脚本会：

- 安装 `git/cmake/make/g++/pkg-config/python3-pip`。
- 拉取官方 `sophon-demo` 的 `release` 分支。
- 进入 `sophon-demo/sample/YOLOv8_plus_det`。
- 下载 BM1684X 的 YOLOv8 bmodel、COCO 类别文件和测试视频。

准备完成后应存在：

```text
sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel
sophon-demo/sample/YOLOv8_plus_det/datasets/test_car_person_1080P.mp4
sophon-demo/sample/YOLOv8_plus_det/datasets/coco.names
```

## 3. 编译 YOLOv8 C++ demo

### Docker 编译入口

当前采用服务器上的 1688 Docker BSP 环境准备和编译，最终把产物放到 RK3588 板子上运行。

用户已创建容器：

```text
1684x_ep_demo
```

进入服务器工作目录：

```bash
cd /data/users/ubuntu/workspace/Sophgo/bm1684/1684X-EP-demo
```

如果容器处于退出状态，先启动：

```bash
sudo docker start 1684x_ep_demo
```

进入容器：

```bash
sudo docker exec -it 1684x_ep_demo /bin/bash
```

进入容器后工作目录是 `/workspace`。YOLO 最终运行仍在 RK3588 板子上，因为 BM1684X PCIe 设备在板子上。

```bash
bash scripts/build_yolov8_cpp.sh
```

注意：RK3588 是主机，BM1684X 是 PCIe 从卡，所以这里使用官方样例的 `pcie` 编译路径，输出文件是：

```text
sophon-demo/sample/YOLOv8_plus_det/cpp/yolov8_bmcv/yolov8_bmcv.pcie
```

## 4. 单卡运行

先跑 `dev_id=0`：

```bash
bash scripts/run_yolov8_cpp_single.sh 0
```

再跑 `dev_id=1`：

```bash
bash scripts/run_yolov8_cpp_single.sh 1
```

输出视频位置：

```text
sophon-demo/sample/YOLOv8_plus_det/cpp/yolov8_bmcv/results/output.mp4
```

## 5. 双卡并发运行

确认单卡都能跑后，再跑双卡：

```bash
bash scripts/run_yolov8_cpp_dual.sh
```

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

先确认驱动和 PCIe：

```bash
lspci | grep -i -E "1f1c|processing"
lsmod | grep -i -E "bm|sophon"
dmesg | grep -i -E "bm|sophon|pci" | tail -n 100
```

### Python YOLO 跑不了

当前 deb 包里没有 `sophon-sail`，所以 Python 版大概率会在这里失败：

```python
import sophon.sail as sail
```

首版先跑 C++ 版。
