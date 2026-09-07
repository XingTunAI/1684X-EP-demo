# 源码说明

这里保存本仓库用于复现和验证的 demo 源码。

## 单卡分析与可选编码：single_card_pipeline

[single_card_pipeline](single_card_pipeline/README.md) 提供 `analysis` 和 `encode` 两种模式。当前一键入口 `bash scripts/run_single_card_analysis_auto.sh` 默认 analysis、device 1、device-bgr 图像路径；每帧检测，输出 JSON 和报告，不画框、不编码。最新 32 路总吞吐 114.10 FPS、平均每路 3.57 FPS，详见[优化结论](../docs/analysis-optimization-20260907.md)。

encode 模式额外画框并输出 H.264 文件，属于单独的历史链路验证，不能套用上述分析帧率。当前视频 worker 仅支持 batch 1，batch 4 只在独立计算脚本中对照。操作见[分析压测](../docs/single-card-analysis.md)，状态和文件解释见[报告指南](../docs/benchmark-report-guide.md)。

## 单卡解码压测：single_card_decode

[single_card_decode](single_card_decode/README.md) 使用标准库 Python 调度 SOPHON FFmpeg，每路独立进程，硬解输出到 null。入口为 `scripts/run_single_card_decode_auto.sh`，默认 device 0、8/16/24/32 路。显式指定 `--device 1` 可复测当前主卡。

当前默认只测性能，低帧率继续后续档位；运行异常或中断时停止。32 路短测与 30 路 15 分钟长测已经完成，后者平均每路约 25 FPS 但存在窗口波动，不能称为稳定通过。完整命令和证据见[操作说明](../docs/single-card-decode.md)及[解码结论](../docs/decode-results-20260907.md)。

两个压测 Demo 均把素材和结果保存在工程根目录的 `datasets/`、`results/`，不放进源码目录。当前解码测试 18 项、流水线测试 10 项已通过。整体状态见[演示总览](../docs/demo-summary-20260907.md)。

## 官方样例修复版：yolov8_bmcv

[yolov8_bmcv/main.cpp](yolov8_bmcv/main.cpp) 基于官方 `YOLOv8_plus_det/cpp/yolov8_bmcv/main.cpp`，将 `VideoWriter` 绑定到当前 `dev_id`，修复卡 1 视频写出阶段的 BMCV handle 不一致问题。

[build_yolov8_cpp.sh](../scripts/build_yolov8_cpp.sh) 会在编译前把这份源码同步到官方样例目录，再构建 `yolov8_bmcv.pcie`。每个进程指定一张卡；使用 [run_yolov8_cpp_dynamic.sh](../scripts/run_yolov8_cpp_dynamic.sh) 启动多卡时，采用多进程方式。

## 单进程多卡验证版：yolov8_multicard

- [yolov8_multicard/main.cpp](yolov8_multicard/main.cpp)：在一个进程中为每张卡创建独立工作线程、模型实例和解码器，全部准备好后统一开始处理。
- [yolov8_multicard/CMakeLists.txt](yolov8_multicard/CMakeLists.txt)：复用 `third_party/sophon-demo` 中的 YOLOv8 检测实现，独立构建 `yolov8_multicard.pcie`。

此版本已在 SDK 0.5.1 的 RK3588 + 三张 BM1684X 环境完成单进程三卡验证，仅执行视频解码和检测，不画框、不编码、不输出视频。默认处理 60 秒，视频提前结束时重新打开。

当前板端 `bm-smi` 的 PID 列记录工作线程 ID，需要结合 `ps -T` 核对线程所属主进程。编译运行命令、三个终端窗口的监控方式和验证证据见 [单进程多卡并发验证](../docs/single-process-multicard.md)。
