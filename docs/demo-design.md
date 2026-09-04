# Demo 设计说明

## 目标

给客户展示 `RK3588 主机 + 多张 BM1684X PCIe 从卡` 的典型使用方式：

- RK3588 负责系统控制、任务编排、业务 UI、网络输入输出。
- BM1684X 负责视频解码、BMCV 图像预处理、NPU 模型推理、视频编码。
- 多张 BM1684X 通过设备号 `dev_id` 被主机调度，每张卡承载一路或多路视频任务。

## 首版场景

建议首版选择“多路视频目标检测”：

1. 输入：本地 MP4、RTSP 摄像头流，或多路测试视频。
2. 处理：BM1684X 硬件解码，BMCV resize/format convert，YOLOv8/YOLOv5 推理，画检测框。
3. 输出：保存检测后视频，或编码推送到 RTSP 服务。
4. 监控：使用 `bm-smi` 展示多张卡的负载、显存和编解码资源占用。

这个场景客户容易理解，也能同时覆盖算力和音视频处理能力。

## 推荐技术基底

### 模型推理

优先用：

```text
sophon-demo/sample/YOLOv8_plus_det
```

原因：

- 官方支持 BM1684X。
- 有 Python 和 C++ 两套实现。
- 已提供 `--dev_id` 参数，适合多卡验证。
- 官方下载脚本会准备测试视频、COCO 类别名和 BM1684X bmodel。

备选：

```text
sophon-demo/sample/YOLOv5
```

YOLOv5 更经典，客户认知成本低；YOLOv8 更新一些。首版建议用 YOLOv8，若客户明确要 YOLOv5 再切换。

### 视频编解码链路

优先参考：

```text
sophon-demo/tutorial/yolov8_ffmpeg_encode
```

官方说明的处理流程是：

```text
ffmpeg decode -> bmcv preprocess -> bmrt yolov8 inference -> cpu postprocess -> bmcv rectangle -> ffmpeg encode
```

这条链路比单纯图片推理更适合展示 1684X 的实际工程价值。

### 多路显示

如需要 HDMI/QT 展示，参考：

```text
sophon-demo/application/YOLOv8_multi_QT
sophon-demo/application/YOLOv5_multi_QT
```

这类样例适合展会或客户现场演示，但首版调试成本高于命令行 demo。

## 多卡调度思路

首版采用“一个进程绑定一张卡”的方式：

```text
Process 0 -> dev_id 0 -> input stream 0
Process 1 -> dev_id 1 -> input stream 1
Process 2 -> dev_id 2 -> input stream 2
```

优点：

- 改动小，最容易跑通。
- 和官方 `--dev_id` 参数一致。
- 单张卡异常时不影响其它卡的进程结构。

后续版本再做统一调度器：

- 一个主进程读取配置。
- 为每个 `dev_id` 创建 worker。
- worker 内部管理解码线程、推理线程、编码线程。
- 汇总 FPS、延迟、设备状态到 Web UI。

## 建议演示指标

- 总路数：例如 2/4/8 路 1080P 视频。
- 每路 FPS：展示端到端处理帧率。
- 单路延迟：从解码到编码输出的耗时。
- 每张卡利用率：由 `bm-smi` 展示。
- 输出形态：保存文件或 RTSP 推流。

## 风险点

- RK3588 的 PCIe 拓扑和供电会影响能接几张 1684X。
- ARM PCIe 环境需要对应版本的 libsophon、sophon-ffmpeg、sophon-opencv、sophon-sail。
- Python 首版易验证，但高并发和低延迟演示应转 C++。
- 模型、视频和运行结果体积较大，不建议提交到 git。
