# RK3588 + BM1684X EP 硬件状态

本文记录一套 RK3588 + 双 BM1684X PCIe 从卡环境的参考 PCIe 拓扑，并给出 `sophon-debs-0.5.1_LTS` 安装与验证建议。

## PCIe 枚举结果

在 RK3588 主机上执行：

```bash
sudo -s
lspci
lspci -vvv
```

参考输出中包含 3 个 RK3588 PCIe Root Port：

```text
0001:10:00.0 PCI bridge: Rockchip Electronics Co., Ltd Device 3588
0002:20:00.0 PCI bridge: Rockchip Electronics Co., Ltd Device 3588
0004:40:00.0 PCI bridge: Rockchip Electronics Co., Ltd Device 3588
```

其中两个 Root Port 下挂了算能加速设备：

```text
0001:11:00.0 Processing accelerators: Device 1f1c:1686
0004:41:00.0 Processing accelerators: Device 1f1c:1686
```

另一个 Root Port 下挂了 Realtek 网卡：

```text
0002:21:00.0 Ethernet controller: Realtek RTL8111/8168/8411
```

## 硬件结论

系统已识别到两张算能 PCIe 从卡：

| PCIe BDF | 类型 | Vendor/Device | 驱动状态 | 链路状态 |
| --- | --- | --- | --- | --- |
| `0001:11:00.0` | Processing accelerator | `1f1c:1686` | `Kernel driver in use: bmdrv` | `Speed 8GT/s, Width x1` |
| `0004:41:00.0` | Processing accelerator | `1f1c:1686` | `Kernel driver in use: bmdrv` | `Speed 5GT/s, Width x1` |

说明：

- PCIe 枚举已成功。
- `bmdrv` 已绑定到两张算能设备。
- 可继续进行 libsophon/runtime/SAIL/多媒体栈验证。
- 两张卡均为 `x1` 链路；其中 `0001:11:00.0` 为 8GT/s，`0004:41:00.0` 为 5GT/s。多路视频吞吐测试中应关注 PCIe 链路带宽。

## 使用 sophon-debs-0.5.1_LTS 的建议

计划使用 `sophon-debs-0.5.1_LTS` 时，建议确认 deb 包架构和板端系统一致：

```bash
uname -a
dpkg --print-architecture
lsb_release -a || cat /etc/os-release
```

RK3588 一般是 `arm64/aarch64`，所以 deb 包也应是 arm64 版本。

推荐准备以下 deb 包：

```text
sophon-driver_0.5.1-LTS-rk3588fix2_arm64.deb
sophon-libsophon_0.5.1-LTS_arm64.deb
sophon-mw-sophon-ffmpeg_0.14.0_arm64.deb
sophon-mw-sophon-ffmpeg-dev_0.14.0_arm64.deb
sophon-mw-sophon-opencv_0.14.0_arm64.deb
sophon-mw-sophon-opencv-dev_0.14.0_arm64.deb
```

如果安装包中不包含 `sophon-sail`，则：

- 只用这批包，优先跑 C++ BMCV/BMRT/FFmpeg 路线。
- 如果要跑 `sample/YOLOv8_plus_det/python/yolov8_bmcv.py`，还需要补充 sophon-sail 的 arm64 安装包或对应 Python wheel。

## 安装顺序建议

在 deb 目录中查看包名：

```bash
ls -lh sophon-debs-0.5.1_LTS
```

本仓库提供了安装脚本：

```bash
bash scripts/install_sophon_debs.sh sophon-debs-0.5.1_LTS
```

脚本会按下面顺序安装，并执行基础检查。

如果目录里包含 driver、libsophon、dev 包，建议按下面顺序安装：

```bash
cd sophon-debs-0.5.1_LTS
sudo apt install ./sophon-driver_0.5.1-LTS-rk3588fix2_arm64.deb
sudo apt install ./sophon-libsophon_0.5.1-LTS_arm64.deb
```

继续安装多媒体相关包：

```bash
sudo apt install ./sophon-mw-sophon-ffmpeg_0.14.0_arm64.deb
sudo apt install ./sophon-mw-sophon-ffmpeg-dev_0.14.0_arm64.deb
sudo apt install ./sophon-mw-sophon-opencv_0.14.0_arm64.deb
sudo apt install ./sophon-mw-sophon-opencv-dev_0.14.0_arm64.deb
```

如果 apt 提示依赖缺失：

```bash
sudo apt --fix-broken install
```

安装完成后重启：

```bash
sudo reboot
```

## 安装后验证

重启后执行：

```bash
lspci | grep -i -E "1f1c|processing"
lsmod | grep -E "bm|sophon"
bm-smi
```

期望结果：

- `lspci` 仍能看到两个 `1f1c:1686`。
- `bmdrv` 或相关算能驱动模块已加载。
- `bm-smi` 能看到两张设备，并分配出 device id，例如 `0` 和 `1`。

继续验证 runtime：

```bash
which bm-smi
ldconfig -p | grep -E "bmrt|bmlib|bmcv"
ls /opt/sophon
```

如果已经补装 sophon-sail，再验证 Python SAIL：

```bash
python3 - <<'PY'
import sophon.sail as sail
print("sophon.sail import ok")
print("device count:", sail.get_available_tpu_num())
PY
```

如果 `sophon.sail` 导入失败，需要检查 sophon-sail 是否安装，以及 Python 版本是否和 wheel/deb 匹配。

## Demo 验证步骤

确认 `bm-smi` 能看到两张卡后，回到本项目使用文档继续：

```bash
python3 tools/run_multicard_yolov8.py \
  --demo-dir "$SOPHON_DEMO_DIR/sample/YOLOv8_plus_det" \
  --devices '1|2|primary|0001:11:00.0|Gen3_x1,0|1|secondary|0004:41:00.0|Gen2_x1' \
  --input datasets/test_car_person_1080P.mp4,datasets/test_car_person_1080P.mp4 \
  --bmodel models/BM1684X/yolov8s_int8_1b.bmodel
```

另开终端观察：

```bash
watch -n 1 bm-smi
```

## 注意事项

- `lspci -vvv` 中出现 `lspci: Unable to load libkmod resources: error -2`，这通常不影响 PCIe 枚举本身；但建议确认系统中 `kmod`/`libkmod` 安装完整。
- 第一张卡 Root Port 标称 `Width x2`，实际 endpoint 链路是 `Width x1`；第二张卡也是 `Width x1`。这可能由转接板、线缆、插槽、设备树或硬件设计决定。
- 如果后续多路视频吞吐不达预期，优先排查 PCIe 带宽、CPU 内存拷贝、解码/编码是否真正走 1684X 硬件路径。
