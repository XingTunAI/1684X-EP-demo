# 3588 + BM1684X EP Demo

这个目录用于整理一个面向客户演示的 `RK3588 主机 + 多张 BM1684X PCIe 从卡` demo。

## 当前基底

- 官方资料入口：https://developer.sophgo.com/site/index/material/all/all.html
- 官方 GitHub：https://github.com/sophgo
- 已拉取官方 demo 仓库：`sophon-demo`

## 文档

- [Demo 设计说明](docs/demo-design.md)
- [使用文档](docs/usage.md)
- [操作记录](docs/operation-log.md)
- [开发路线图](docs/roadmap.md)
- [板端状态记录](docs/board-status.md)
- [YOLOv8 跑通准备文档](docs/run-yolo.md)

## 仓库结构

```text
.
├── configs/                  # 后续 demo 配置文件
├── docs/                     # 设计、使用、操作记录、路线图
├── scripts/                  # 板端安装和辅助脚本
├── src/                      # 后续自研 C++/Web demo 代码
├── tools/                    # 当前可用工具脚本
├── .gitignore
└── README.md
```

`sophon-demo/` 是本地参考用的官方仓库，已在 `.gitignore` 中排除。

## 推荐首版 demo

首版建议做成一个多路视频 AI 分析 demo：

1. RK3588 作为主机，系统里安装 BM1684X PCIe 驱动和 SOPHON SDK 运行库。
2. 每张 BM1684X 从卡负责一路或多路视频。
3. 视频在 1684X 上解码，BMCV 做预处理，BMRuntime/SAIL 跑 YOLOv8/YOLOv5，最后把检测框画回视频并编码输出。
4. 用 `bm-smi` 展示多张 1684X 的 TPU/VPU/JPU/内存占用，证明算力和视频编解码能力都被调起来了。

## 官方样例选择

- `sophon-demo/sample/YOLOv8_plus_det`
  - 推荐作为模型推理基底。
  - 支持 BM1684X。
  - 有 Python 和 C++，参数里已有 `--dev_id`。
- `sophon-demo/tutorial/yolov8_ffmpeg_encode`
  - 推荐作为端到端视频链路基底。
  - 流程是 `ffmpeg decode + bmcv preprocess + bmrt yolov8 inference + cpu postprocess + bmcv rectangle + ffmpeg encode`。
- `sophon-demo/application/YOLOv8_multi_QT`
  - 推荐作为多路视频显示或展台 UI 参考。
  - 配置里已有 `dev_id`。

## 快速验证路线

先跑 Python 版，确认 RK3588 能看到并调用 BM1684X：

```bash
cd sophon-demo/sample/YOLOv8_plus_det
chmod -R +x scripts/
./scripts/download.sh
python3 python/yolov8_bmcv.py \
  --input datasets/test_car_person_1080P.mp4 \
  --bmodel models/BM1684X/yolov8s_int8_1b.bmodel \
  --dev_id 0 \
  --conf_thresh 0.25 \
  --nms_thresh 0.7
```

多卡验证可以使用本目录的启动器：

```bash
python3 tools/run_multicard_yolov8.py \
  --demo-dir sophon-demo/sample/YOLOv8_plus_det \
  --devices 0,1 \
  --input datasets/test_car_person_1080P.mp4 \
  --bmodel models/BM1684X/yolov8s_int8_1b.bmodel
```

## 交付形态

客户演示时建议包含三个窗口或指标：

- 多路视频窗口：展示每路视频检测框和 FPS。
- 设备监控窗口：`bm-smi` 展示每张 1684X 的占用。
- 输出流或文件：展示推理后视频可以重新编码输出，如 RTSP 或 `output.mp4`。

## 后续要做

- 在真实 RK3588 + BM1684X EP 环境上安装 SDK 并跑通单卡。
- 确认 `bm-smi` 能枚举所有 PCIe 从卡。
- 把 `tutorial/yolov8_ffmpeg_encode` 改成支持 `--dev_id`、多输入、多输出 RTSP。
- 按客户实际场景替换模型：人车检测、工服安全帽、车牌、烟火、OCR 都可以沿用同一套 pipeline。
