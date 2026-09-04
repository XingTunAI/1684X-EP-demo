# 开发路线图

## V0：资料确认和单卡跑通

目标：证明 RK3588 能通过 PCIe 调用一张 BM1684X。

- 安装 ARM PCIe 版本 SDK。
- `bm-smi` 能看到设备。
- 跑通 `sophon-demo/sample/YOLOv8_plus_det/python/yolov8_bmcv.py`。
- 生成检测后视频或统计日志。

## V1：多卡并发验证

目标：证明 RK3588 能同时使用多张 BM1684X。

- 使用 `tools/run_multicard_yolov8.py` 按 `dev_id` 启动多个官方 YOLOv8 进程。
- 每个进程绑定一张卡。
- 使用 `bm-smi` 观察多张卡同时有负载。
- 记录每张卡 FPS 和端到端耗时。

## V2：视频编解码闭环

目标：证明 1684X 的视频解码、BMCV、NPU 推理、编码能力可以形成完整业务链路。

- 基于 `sophon-demo/tutorial/yolov8_ffmpeg_encode` 改造。
- 增加显式 `--dev_id` 参数。
- 增加 `--input` 多路输入。
- 增加 `--output` 多路输出，支持 RTSP。
- 输出每路 FPS、解码耗时、推理耗时、编码耗时。

## V3：客户演示版

目标：形成可用于演示展示的 demo。

- 增加配置文件，例如 `configs/demo.yaml`。
- 启动脚本一键启动多路视频。
- 增加 Web 或 QT 展示界面。
- 页面展示视频画面、检测结果、设备状态和性能指标。

## V4：客户场景替换

目标：从通用 COCO YOLO 演示切到客户业务场景。

- 替换模型，例如安全帽、工服、烟火、人车、车牌或 OCR。
- 替换输入源为客户摄像头或录像。
- 根据客户需求调整阈值、类别显示和告警逻辑。
