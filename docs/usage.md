# 使用文档

## 1. 准备官方 demo

如果环境中还没有 `third_party/sophon-demo`，在仓库根目录执行：

```bash
mkdir -p third_party
git clone --depth 1 -b release https://github.com/sophgo/sophon-demo.git third_party/sophon-demo
```

## 2. 准备 RK3588 + BM1684X PCIe 环境

在 RK3588 主机上安装以下算能运行库：

- libsophon
- sophon-ffmpeg
- sophon-opencv
- sophon-sail（仅 Python 版需要；C++ 验证路线可以先不装）

安装方式以算能官网对应 SDK 版本的 ARM PCIe 文档为准。

PCIe 枚举和 `sophon-debs-0.5.1_LTS` 安装建议见：

```text
docs/board-status.md
```

安装完成后确认设备可见：

```bash
bm-smi
```

如果系统包含多张 BM1684X，从 `bm-smi` 中确认设备号。参考双卡拓扑如下：

```text
dev_id 1 -> 0001:11:00.0 -> PCIe Gen3 x1 -> 主卡，优先跑主路/高码率/重任务
dev_id 0 -> 0004:41:00.0 -> PCIe Gen2 x1 -> 副卡，跑副路/轻载任务
```

编译 C++ demo 时还要确认 libsophon 开发头文件存在：

```bash
find -L /opt/sophon/libsophon-current/include /usr/include /usr/local/include \
  -name 'bmruntime_interface.h' -o \
  -name 'bmcv_api_ext.h' -o \
  -name 'bmlib_runtime.h'
```

如果找不到，需要先安装 `sophon-libsophon-dev` 或从匹配 SDK 包复制 libsophon 的 `include` 目录。

## 3. 下载 YOLOv8 模型和测试数据

推荐直接按 [YOLOv8 跑通准备文档](run-yolo.md) 执行。板端一键准备命令：

```bash
bash scripts/prepare_yolov8_demo.sh
bash scripts/build_yolov8_cpp.sh
bash scripts/run_yolov8_cpp_single.sh 0
```

下面保留官方手动步骤，方便排查问题。

```bash
cd third_party/sophon-demo/sample/YOLOv8_plus_det
chmod -R +x scripts/
./scripts/download.sh --BM1684X
```

确认以下文件存在：

```text
datasets/test_car_person_1080P.mp4
models/BM1684X/yolov8s_int8_1b.bmodel
```

## 4. 单卡验证（C++ 版）

```bash
cd <repo-dir>
bash scripts/build_yolov8_cpp.sh
bash scripts/run_yolov8_cpp_single.sh 1
```

成功后会在样例目录下生成 `results/output.mp4`，并在终端打印预处理、推理、后处理等耗时。这里不使用 Python `sophon.sail`。

## 5. 多卡验证

回到本仓库根目录，执行：

```bash
python3 tools/run_multicard_yolov8.py \
  --demo-dir "third_party/sophon-demo/sample/YOLOv8_plus_det" \
  --devices '1|2|primary|0001:11:00.0|Gen3_x1,0|1|secondary|0004:41:00.0|Gen2_x1' \
  --input datasets/test_car_person_1080P.mp4,datasets/test_car_person_1080P.mp4 \
  --bmodel models/BM1684X/yolov8s_int8_1b.bmodel
```

参数说明：

- `--devices`：设备拓扑和权重，格式是 `dev_id|weight|role|pcie_bdf|pcie_link`，多个设备用英文逗号分隔。
- `--input`：输入视频路径，多个输入用英文逗号分隔；任务会按设备权重分配。
- `--bmodel`：bmodel 路径，可以是相对 `--demo-dir` 的路径，也可以是绝对路径。
- `--conf-thresh`：检测置信度阈值，默认 `0.25`。
- `--nms-thresh`：NMS 阈值，默认 `0.7`。

例如 3 路输入时，默认权重 `dev_id 1|2`、`dev_id 0|1` 会得到：

```text
stream0 -> dev_id 1
stream1 -> dev_id 1
stream2 -> dev_id 0
```

C++ 版推荐使用动态分配脚本：

```bash
bash scripts/run_yolov8_cpp_dynamic.sh \
  "third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/test_car_person_1080P.mp4" \
  "third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/test_car_person_1080P.mp4"
```

也可以显式指定任务输入和设备权重：

```bash
DEVICE_SPECS="1|2|primary|0001:11:00.0|Gen3_x1,0|1|secondary|0004:41:00.0|Gen2_x1" \
INPUTS="<main-video>,<side-video-1>,<side-video-2>" \
bash scripts/run_yolov8_cpp_dynamic.sh
```

另开一个终端观察设备状态：

```bash
watch -n 1 bm-smi
```

## 6. HDMI/XFCE 显示

RK3588 主机可通过 HDMI 输出显示内容。在 XFCE/Xorg `:0` 环境中，官方 YOLOv8 C++ demo 默认将检测框写入结果视频，不直接弹出 HDMI 窗口。先运行推理：

```bash
cd <repo-dir>
bash scripts/run_yolov8_cpp_single.sh 1
```

再将结果视频播放到 HDMI：

```bash
bash scripts/play_yolov8_hdmi.sh \
  "third_party/sophon-demo/sample/YOLOv8_plus_det/cpp/yolov8_bmcv/results/output.mp4"
```

脚本默认使用 `DISPLAY=:0`。在 LightDM/XFCE 环境下会优先尝试 `/var/run/lightdm/root/:0`，并让播放器优先使用系统 ffmpeg 库，避免误加载 SOPHON ffmpeg 库。

双卡动态运行后，每路输出位于独立目录，可指定任一路结果输出到 HDMI：

```bash
bash scripts/play_yolov8_hdmi.sh \
  "${RUN_ROOT}/<run_id>/task0_dev1/results/output.mp4"
```

实时 HDMI 预览需要改造官方 C++ 样例：在 `draw_result` 后将带框图像送到 X11 窗口、GStreamer sink 或 DRM/KMS 输出。本仓库默认采用“生成结果视频 + HDMI 播放”的稳定方式。

## 7. 编解码推流验证

如果客户重点关注视频编码和 RTSP 输出，参考官方样例：

```bash
cd third_party/sophon-demo/tutorial/yolov8_ffmpeg_encode
```

准备 RTSP 服务，例如 mediamtx，然后运行官方命令：

```bash
./yolov8_bmcv.soc \
  --output=rtsp://<server-ip>:8554/test \
  --bmodel=BM1684X/yolov8s_int8_1b.bmodel \
  --input=test_car_person_1080P.mp4
```

后续可将该样例改造成支持多 `dev_id`、多输入和多输出地址的版本。

## 8. 推荐演示流程

1. 运行 `bm-smi`，展示 RK3588 已发现多张 1684X PCIe 从卡。
2. 运行单卡 YOLOv8 视频推理，展示基本能力。
3. 运行 `tools/run_multicard_yolov8.py`，展示多卡并发。
4. 切换到 `yolov8_ffmpeg_encode`，展示检测后视频可以重新编码输出。
5. 根据客户场景替换视频和模型，例如人车检测、安全帽检测、车牌识别或 OCR。
