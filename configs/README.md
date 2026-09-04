# Configs

这里预留 demo 配置文件。

建议后续增加 `demo.yaml`：

```yaml
model: sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel
classnames: sophon-demo/sample/YOLOv8_plus_det/datasets/coco.names
devices:
  - id: 0
    input: rtsp://example/stream0
    output: rtsp://demo-server:8554/card0
  - id: 1
    input: rtsp://example/stream1
    output: rtsp://demo-server:8554/card1
thresholds:
  confidence: 0.25
  nms: 0.7
```
