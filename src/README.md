# Source Layout

这里预留给后续自研 demo 代码。

首版不会直接复制官方 `sophon-demo` 的大量源码，而是先用 `tools/run_multicard_yolov8.py` 调度官方样例完成多卡验证。

后续建议在这里落地：

- `src/multicard_pipeline/`：C++ 多卡视频分析 pipeline。
- `src/device_monitor/`：`bm-smi` 或 BMLIB 状态采集。
- `src/web_ui/`：客户演示界面。
