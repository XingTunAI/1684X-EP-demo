# Source Layout

这里放仓库自带的 demo 源码和后续自研代码。

首版不会直接复制官方 `sophon-demo` 的大量源码，但会保存需要稳定复现的改动源码：

- `src/yolov8_bmcv/main.cpp`：基于官方 `YOLOv8_plus_det/cpp/yolov8_bmcv/main.cpp` 的已修版本，`VideoWriter` 会绑定当前 `dev_id`，避免卡 1 视频写出阶段出现 BMCV handle 不一致错误。

`scripts/build_yolov8_cpp.sh` 会在编译前把上述源码同步到官方样例目录，再构建 `yolov8_bmcv.pcie`。

后续建议在这里落地：

- `src/multicard_pipeline/`：C++ 多卡视频分析 pipeline。
- `src/device_monitor/`：`bm-smi` 或 BMLIB 状态采集。
- `src/web_ui/`：客户演示界面。
