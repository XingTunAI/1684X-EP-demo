# 单进程多卡并发验证

`yolov8_multicard.pcie` 是一个进程，每个设备各用一个 C++ 工作线程。每个线程独立持有 YOLO 模型、BMRuntime、设备 handle、VideoCapture 和计时数据。所有线程初始化成功后统一开始处理，同一段视频分别在各卡处理，不拆分视频帧。

原有 `run_yolov8_cpp_dynamic.sh` 是多进程验证，不能用来验证一个 PID 同时调用多张卡。

## Linux 编译和运行

需要匹配 SDK 的 libsophon 开发文件和 SOPHON OpenCV/FFmpeg。可先确认官方单卡 C++ 样例能够编译运行。

```bash
cd /userdata/1684X-EP-demo
git pull --ff-only
cmake -S src/yolov8_multicard -B build/yolov8_multicard \
  -DCMAKE_BUILD_TYPE=Release \
  -Dlibsophon_DIR=/opt/sophon/libsophon-0.5.1/data
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
- 分别查询三张卡的 Processes。当前板端 `bm-smi` 的 PID 列实际记录工作线程 ID（TID）；用 `ps -T -p <主进程PID> -o pid,spid,comm` 核对它们属于同一 PID。不能要求该列的数字一定相同。
- 结束时每卡各输出一条非零帧数的 `SUMMARY`。
- `MAX_OVERLAPPING_DETECT_CALLS` 表示主机侧同时进入且尚未返回的 Detect 调用峰值；达到 3 说明三个工作线程的检测调用重叠，不等于三个 TPU 的硬件执行时间严格重合。

本版本仅验证视频解码和检测并发，不画框、不编码、不输出视频。`pipeline_fps` 包含解码、检测和循环重新打开视频的耗时，不能与原来包含画框编码的端到端性能直接比较。

## 2026-09-05 板端验证结果

在 RK3588 / Ubuntu 22.04 / aarch64 / GCC 11.4、libsophon 和驱动 0.5.1、SOPHON OpenCV/FFmpeg 0.10.0 上完成编译及两次 60 秒三卡运行。编译产生官方代码浮点转整数及 SDK 共享库 `.dynsym` 链接警告，但成功生成程序，两次任务均正常结束。

第二次运行主进程 PID 为 `487277`，`READY` 和 `SUMMARY` 的 PID 均相同：

| dev_id | bm-smi PID 列 / ps SPID | 所属 PID | 处理帧数 | 处理秒数 | pipeline FPS |
|---|---|---|---|---|---|
| 0 | 487281 | 487277 | 253 | 60.042227 | 4.213701 |
| 1 | 487282 | 487277 | 261 | 60.035394 | 4.347435 |
| 2 | 487283 | 487277 | 356 | 60.176649 | 5.915916 |

`MAX_OVERLAPPING_DETECT_CALLS=3`。`ps -T` 和逐卡 `bm-smi` 的线程 ID 一一匹配，证明同一进程内三个工作线程分别使用三张设备。板端还有其他业务进程占用设备，这些数字仅作为并发功能验证，不是空载性能基准。

板端日志：`/tmp/codex-single-process-20260905-145931.log`、`/tmp/codex-single-process-confirm.log`；设备快照：`/tmp/codex-native-smi-{0,1,2}.txt`。

`bm-smi` 的标准输出可能含终端控制字符，采集可读文本应使用它自己的 `--file` 参数，例如：

```bash
for id in 0 1 2; do
  TERM=xterm bm-smi --dev="$id" --noloop --file="/tmp/multicard-smi-$id.txt" >/dev/null 2>&1
done
grep yolov8_multicard /tmp/multicard-smi-*.txt
```

这些查询是顺序采样。需在任务运行期间执行，并结合 `ps -T` 核实线程所属进程。

官方检测类内部存在断言，底层致命错误可能终止整个进程。此次验证不保证其他 SDK 版本或模型具备相同的多线程行为。
