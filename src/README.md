# 源码说明

这里保存本仓库用于复现和验证的 demo 源码。

## 单卡完整链路压测：single_card_pipeline

当前新增“解码＋推理”模式：`bash scripts/run_single_card_analysis_auto.sh`，每帧检测，不画框、不编码，自动保存报告及逐路/逐窗口统计。操作见[单卡解码＋推理](../docs/single-card-analysis.md)。

[single_card_pipeline/](single_card_pipeline/README.md) 同时运行解码、YOLOv8 检测、画框和 H.264 编码，输出逐帧检测 JSON 与视频文件。对每路统计窗口 FPS，结束后核对视频实际帧数。客户同时需要分析结果和视频输出时使用此 demo；纯解码 demo 作为对照。

## 单卡解码压测：single_card_decode

[single_card_decode/](single_card_decode/README.md) 是独立的单卡压测 demo，包含 Python 调度脚本和配套测试，调用 SOPHON FFmpeg 硬件解码，支持 1/8/16/24/32 路阶梯负载、逐路窗口 FPS、设备监控及结果记录。当前测解码，不包含算法推理。素材和结果分别保存在工程根目录的 `datasets/`、`results/`。

## 官方样例修复版：yolov8_bmcv

[yolov8_bmcv/main.cpp](yolov8_bmcv/main.cpp) 基于官方 `YOLOv8_plus_det/cpp/yolov8_bmcv/main.cpp`，将 `VideoWriter` 绑定到当前 `dev_id`，修复卡 1 视频写出阶段的 BMCV handle 不一致问题。

[build_yolov8_cpp.sh](../scripts/build_yolov8_cpp.sh) 会在编译前把这份源码同步到官方样例目录，再构建 `yolov8_bmcv.pcie`。每个进程指定一张卡；使用 [run_yolov8_cpp_dynamic.sh](../scripts/run_yolov8_cpp_dynamic.sh) 启动多卡时，采用多进程方式。

## 单进程多卡验证版：yolov8_multicard

- [yolov8_multicard/main.cpp](yolov8_multicard/main.cpp)：在一个进程中为每张卡创建独立工作线程、模型实例和解码器，全部准备好后统一开始处理。
- [yolov8_multicard/CMakeLists.txt](yolov8_multicard/CMakeLists.txt)：复用 `third_party/sophon-demo` 中的 YOLOv8 检测实现，独立构建 `yolov8_multicard.pcie`。

此版本已在 SDK 0.5.1 的 RK3588 + 三张 BM1684X 环境完成单进程三卡验证，仅执行视频解码和检测，不画框、不编码、不输出视频。默认处理 60 秒，视频提前结束时重新打开。

当前板端 `bm-smi` 的 PID 列记录工作线程 ID，需要结合 `ps -T` 核对线程所属主进程。编译运行命令、三个终端窗口的监控方式和验证证据见 [单进程多卡并发验证](../docs/single-process-multicard.md)。
