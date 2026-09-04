# 使用文档

## 1. 准备官方 demo

如果本地还没有 `sophon-demo`，在仓库根目录执行：

```bash
git clone --depth 1 -b release https://github.com/sophgo/sophon-demo.git
```

## 2. 准备 RK3588 + BM1684X PCIe 环境

在 RK3588 主机上安装以下算能运行库：

- libsophon
- sophon-ffmpeg
- sophon-opencv
- sophon-sail

安装方式以算能官网对应 SDK 版本的 ARM PCIe 文档为准。

当前板端 PCIe 枚举和 `sophon-debs-0.5.1_LTS` 安装建议见：

```text
docs/board-status.md
```

安装完成后先确认设备可见：

```bash
bm-smi
```

如果有多张 BM1684X，从 `bm-smi` 中确认设备号，例如 `0,1,2,3`。

## 3. 下载 YOLOv8 模型和测试数据

推荐直接按 [YOLOv8 跑通准备文档](run-yolo.md) 执行。板端一键准备命令：

```bash
bash scripts/prepare_yolov8_demo.sh
bash scripts/build_yolov8_cpp.sh
bash scripts/run_yolov8_cpp_single.sh 0
```

下面保留官方手动步骤，方便排查问题。

```bash
cd sophon-demo/sample/YOLOv8_plus_det
chmod -R +x scripts/
./scripts/download.sh
```

确认以下文件存在：

```text
datasets/test_car_person_1080P.mp4
models/BM1684X/yolov8s_int8_1b.bmodel
```

## 4. 单卡验证

```bash
cd sophon-demo/sample/YOLOv8_plus_det
python3 python/yolov8_bmcv.py \
  --input datasets/test_car_person_1080P.mp4 \
  --bmodel models/BM1684X/yolov8s_int8_1b.bmodel \
  --dev_id 0 \
  --conf_thresh 0.25 \
  --nms_thresh 0.7
```

成功后会在样例目录下生成 `results/output.mp4`，并在终端打印解码、预处理、推理、后处理等耗时。

## 5. 多卡验证

回到本仓库根目录，执行：

```bash
python3 tools/run_multicard_yolov8.py \
  --demo-dir sophon-demo/sample/YOLOv8_plus_det \
  --devices 0,1 \
  --input datasets/test_car_person_1080P.mp4 \
  --bmodel models/BM1684X/yolov8s_int8_1b.bmodel
```

参数说明：

- `--devices`：要使用的 BM1684X 设备号，多个设备用英文逗号分隔。
- `--input`：输入视频路径，可以是相对 `--demo-dir` 的路径，也可以是绝对路径。
- `--bmodel`：bmodel 路径，可以是相对 `--demo-dir` 的路径，也可以是绝对路径。
- `--conf-thresh`：检测置信度阈值，默认 `0.25`。
- `--nms-thresh`：NMS 阈值，默认 `0.7`。

另开一个终端观察设备状态：

```bash
watch -n 1 bm-smi
```

## 6. 编解码推流验证

如果客户重点关注视频编码和 RTSP 输出，参考官方样例：

```bash
cd sophon-demo/tutorial/yolov8_ffmpeg_encode
```

准备 RTSP 服务，例如 mediamtx，然后运行官方命令：

```bash
./yolov8_bmcv.soc \
  --output=rtsp://<server-ip>:8554/test \
  --bmodel=BM1684X/yolov8s_int8_1b.bmodel \
  --input=test_car_person_1080P.mp4
```

后续本仓库会把这个样例改造成支持多 `dev_id`、多输入和多输出地址的版本。

## 7. 推荐演示流程

1. 先运行 `bm-smi`，展示 RK3588 已发现多张 1684X PCIe 从卡。
2. 运行单卡 YOLOv8 视频推理，展示基本能力。
3. 运行 `tools/run_multicard_yolov8.py`，展示多卡并发。
4. 切换到 `yolov8_ffmpeg_encode`，展示检测后视频可以重新编码输出。
5. 根据客户场景替换视频和模型，例如人车检测、安全帽检测、车牌识别或 OCR。
