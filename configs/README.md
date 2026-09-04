# Configs

本目录用于存放演示配置文件。

参考双卡拓扑中，两张 1686 的 PCIe 链路能力不同：

```text
TPU-ID 0 -> 0004:41:00.0 -> PCIe Gen2 x1, 5GT/s
TPU-ID 1 -> 0001:11:00.0 -> PCIe Gen3 x1, 8GT/s
```

因此配置中不建议简单按 `0,1` 均分任务。建议将主路、高码率或更多路数优先分配到 `dev_id: 1`，将 `dev_id: 0` 作为副路或轻载任务设备。

```yaml
model: third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel
classnames: third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/coco.names
devices:
  - id: 1
    role: primary
    pcie_bdf: "0001:11:00.0"
    pcie_link: "Gen3 x1"
    input: rtsp://example/main-stream
    output: rtsp://demo-server:8554/card1
  - id: 0
    role: secondary
    pcie_bdf: "0004:41:00.0"
    pcie_link: "Gen2 x1"
    input: rtsp://example/secondary-stream
    output: rtsp://demo-server:8554/card0
thresholds:
  confidence: 0.25
  nms: 0.7
```
