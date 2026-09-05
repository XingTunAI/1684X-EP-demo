# 验证参考

本文仅整理官方样例的单卡和多卡验证参考，不代表后续开发计划或个人待办。

## 单卡验证

目标：证明 RK3588 能通过 PCIe 调用一张 BM1684X。

- 安装 ARM PCIe 版本 SDK。
- `bm-smi` 能看到设备。
- 跑通 `sophon-demo/sample/YOLOv8_plus_det/cpp/yolov8_bmcv/yolov8_bmcv.pcie`。
- 生成检测后视频或统计日志。

## 多卡并发验证

目标：证明 RK3588 能同时使用多张 BM1684X。

- 使用 C++ 脚本或 `tools/run_multicard_yolov8.py` 按 `dev_id` 启动多个官方 YOLOv8 C++ 进程。
- 每个进程绑定一张卡。
- 使用 `bm-smi` 观察多张卡同时有负载。
- 记录每张卡 FPS 和端到端耗时。
