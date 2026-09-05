# 单进程多卡并发验证

`yolov8_multicard.pcie` 是一个进程，每个设备各用一个 C++ 工作线程。每个线程独立持有 YOLO 模型、BMRuntime、设备 handle、VideoCapture 和计时数据。所有线程初始化成功后统一开始处理，同一段视频分别在各卡处理，不拆分视频帧。

原有 `run_yolov8_cpp_dynamic.sh` 是多进程验证，不能用来验证一个 PID 同时调用多张卡。

## Linux 编译和运行

需要匹配 SDK 的 libsophon 开发文件和 SOPHON OpenCV/FFmpeg。可先确认官方单卡 C++ 样例能够编译运行。

```bash
cd /userdata/1684X-EP-demo
git pull --ff-only
cmake -S src/yolov8_multicard -B build/yolov8_multicard -DCMAKE_BUILD_TYPE=Release
cmake --build build/yolov8_multicard -j"$(nproc)"

DEMO="$PWD/third_party/sophon-demo/sample/YOLOv8_plus_det"
./build/yolov8_multicard/yolov8_multicard.pcie \
  --devices=0,1,2 \
  --input="$DEMO/datasets/test_car_person_1080P.mp4" \
  --bmodel="$DEMO/models/BM1684X/yolov8s_fp32_1b.bmodel" \
  --classnames="$DEMO/datasets/coco.names" \
  --seconds=60
```

默认视频结束后重新打开，处理至少 60 秒（不含初始化，实际退出还受 SDK 调用耗时影响）。`--seconds=0` 表示每卡仅处理一遍。Ctrl+C 请求结束；正在执行的 SDK 调用返回后退出。

## 验证依据

- 三条 `READY` 使用同一个 PID，分别对应 `dev_id=0,1,2`，之后输出 `START ... workers=3`。
- 运行时 `pgrep -af '[y]olov8_multicard.pcie'` 应看到一个推理进程。
- 分别运行 `bm-smi --dev=0 --noloop`、`bm-smi --dev=1 --noloop`、`bm-smi --dev=2 --noloop`，各卡 Processes 中应出现同一个 PID。不同 SDK 版本的名称可能显示为截断后的进程名。
- 结束时每卡各输出一条非零帧数的 `SUMMARY`。
- `MAX_OVERLAPPING_DETECT_CALLS` 表示主机侧同时进入且尚未返回的 Detect 调用峰值；达到 3 说明三个工作线程的检测调用重叠，不等于三个 TPU 的硬件执行时间严格重合。

本版本仅验证视频解码和检测并发，不画框、不编码、不输出视频。`pipeline_fps` 包含解码、检测和循环重新打开视频的耗时，不能与原来包含画框编码的端到端性能直接比较。

目前未在 BM1684X 硬件上完成此版本编译和运行验证。依赖 SDK 内部的多线程支持；官方检测类内部存在断言，底层致命错误可能终止整个进程。验证时保留完整日志，以区分设备初始化、解码和推理故障。
