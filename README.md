# 3588 + BM1684X EP Demo

本仓库提供面向 `RK3588 主机 + 多张 BM1684X PCIe 从卡` 场景的 YOLO 视频分析演示工程，用于验证多卡设备识别、任务分配、模型推理、视频结果输出和 HDMI 展示能力。

## 参考资源

- 官方资料入口：https://developer.sophgo.com/site/index/material/all/all.html
- 官方 GitHub：https://github.com/sophgo
- 官方 demo 仓库：`third_party/sophon-demo`

## 文档

- [Demo 设计说明](docs/demo-design.md)
- [使用文档](docs/usage.md)
- [操作记录](docs/operation-log.md)
- [验证参考](docs/roadmap.md)
- [板端状态记录](docs/board-status.md)
- [YOLOv8 跑通准备文档](docs/run-yolo.md)
- [HDMI/XFCE 显示说明](docs/hdmi-display.md)

## 仓库结构

```text
.
├── configs/                  # demo 配置文件
├── docs/                     # 设计、使用、操作记录、验证参考
├── scripts/                  # 板端安装和辅助脚本
├── src/                      # 自研 C++/Web demo 代码
├── third_party/              # 外部官方参考仓库，本地保留但不提交
├── tools/                    # 工具脚本
├── .gitignore
└── README.md
```

`third_party/sophon-demo/` 是外部官方参考仓库，已在 `.gitignore` 中排除。

## 演示方案

推荐采用多路视频 AI 分析方案：

1. RK3588 作为主机，系统里安装 BM1684X PCIe 驱动和 SOPHON SDK 运行库。
2. 每张 BM1684X 从卡负责一路或多路视频。
3. 视频在 1684X 上解码，BMCV 做预处理，BMRuntime/SAIL 跑 YOLOv8/YOLOv5，最后把检测框画回视频并编码输出。
4. 使用 `bm-smi` 展示多张 BM1684X 的 TPU 利用率、内存、温度和功耗；视频编解码链路通过程序日志和输出文件验证。

## 官方样例选择

- `third_party/sophon-demo/sample/YOLOv8_plus_det`
  - 推荐作为模型推理基础样例。
  - 支持 BM1684X。
  - 当前只使用 C++ 版作为验证主线；官方 Python 版仅作参考，暂不作为执行路径。
  - C++ 参数里已有 `--dev_id`，适合单卡和多卡手动验证。
- `third_party/sophon-demo/tutorial/yolov8_ffmpeg_encode`
  - 推荐作为端到端视频链路基础样例。
  - 流程是 `ffmpeg decode + bmcv preprocess + bmrt yolov8 inference + cpu postprocess + bmcv rectangle + ffmpeg encode`。
- `third_party/sophon-demo/application/YOLOv8_multi_QT`
  - 可作为多路视频显示或展台 UI 参考。
  - 配置里已有 `dev_id`。

## 快速验证

可先运行 C++ 版样例，确认 RK3588 能够调用 BM1684X，并避开 Python `sophon.sail` 依赖：

```bash
cd third_party/sophon-demo/sample/YOLOv8_plus_det
chmod -R +x scripts/
./scripts/download.sh --BM1684X
cd cpp/yolov8_bmcv
mkdir -p build && cd build
cmake ..
make -j$(nproc)
cd ..
./yolov8_bmcv.pcie \
  --input=../../datasets/test_car_person_1080P.mp4 \
  --bmodel=../../models/BM1684X/yolov8s_fp32_1b.bmodel \
  --dev_id=0 \
  --conf_thresh=0.25 \
  --nms_thresh=0.7 \
  --classnames=../../datasets/coco.names
```

多卡验证可以使用本仓库提供的启动器：

```bash
python3 tools/run_multicard_yolov8.py \
  --demo-dir "third_party/sophon-demo/sample/YOLOv8_plus_det" \
  --devices '1|2|primary|0001:11:00.0|Gen3_x1,0|1|secondary|0004:41:00.0|Gen2_x1' \
  --input datasets/test_car_person_1080P.mp4,datasets/test_car_person_1080P.mp4 \
  --bmodel models/BM1684X/yolov8s_fp32_1b.bmodel
```

注意：这里的 `tools/run_multicard_yolov8.py` 只是启动多个 C++ 可执行文件的调度脚本，不调用官方 Python 推理 demo，也不依赖 `sophon.sail`。

参考硬件拓扑中两张设备链路能力不同：`dev_id 1` 为 Gen3 主卡，`dev_id 0` 为 Gen2 副卡。多路任务应通过 `--devices` 的权重和任务顺序指定，例如主路优先分配给 `dev_id 1`。

## 展示内容

演示时建议包含以下内容：

- 多路视频窗口：展示每路视频检测框和 FPS。
- 设备监控窗口：`bm-smi` 展示每张 1684X 的 TPU 利用率、内存、温度和功耗。
- 输出流或文件：展示推理后视频可以重新编码输出，如 RTSP 或 `output.mp4`。

在 XFCE/Xorg 桌面环境中，官方 YOLOv8 C++ 样例可先生成带框视频文件，再播放到 HDMI：

```bash
bash scripts/play_yolov8_hdmi.sh \
  "$SOPHON_DEMO_DIR/sample/YOLOv8_plus_det/cpp/yolov8_bmcv/results/output.mp4"
```

## 当前状态

- 已在真实 RK3588 + BM1684X EP 环境上安装 SOPHON SDK，并跑通单卡 YOLOv8 C++ 推理。
- 已确认 `bm-smi` 能枚举两张 BM1684X PCIe 从卡。
- 已完成双卡并发验证，`dev_id=1` 主卡和 `dev_id=0` 副卡均可运行 `yolov8_bmcv.pcie`。
- 已修复卡 1 视频写出阶段的 BMCV handle 不一致问题，仓库内置修复后的 `src/yolov8_bmcv/main.cpp`。
