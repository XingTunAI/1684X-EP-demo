# 实时调度实现与验证

[仓库首页](../../../README.md) / [HDMI 文档导航](../README.md#文档导航) / [实测索引](results.md)

运行入口和参数见 [HDMI 视频墙](../README.md)。实现位于 `main.cpp`、`realtime_policy.hpp` 和 `wall_renderer.hpp`。

## 数据流与计数

`latest` 每路使用一个解码线程和一个检测线程。解码器按本地源 FPS 连续推进；RTSP 不加软件节流。一个容量为 1 的槽保存最新待处理帧，覆盖时立即释放旧帧。检测限频等待期间帧仍放在槽中，真正开始处理时才取走最新帧。每路应用同时最多持有 3 个源帧：正在解码的帧、槽中的帧、正在检测的帧；这不包含 SDK 内部缓冲。

丢弃发生在完整解码后、BGR 桥接和模型预处理前，不减少视频解码工作量。每个检测器、缩略图、运行时及输出缓冲只由本路检测线程使用。`all` 使用原有串行读取/处理路径，不启动独立解码线程。

解码序号每成功解码一次递增，跨文件循环不重置；`source_frame_id` 在循环后从 0 重新计数。`frame` 可以跳跃，`processed_index` 从 0 连续递增。正常结束核对 `decoded = completed + dropped_overwrite + dropped_stale + dropped_shutdown`；异常中未完成记录的在途帧计入 `unprocessed_decoded_frames`。正式窗口解码和完成计数各以实际发生时刻归属，不能直接对两个窗口计数做减法推算丢帧。

`max-frame-age-ms` 在发布和送检前检查，本地视频从计划读取时间算起，RTSP 从解码完成算起。它不是整个处理链路的延迟上限；检测本身仍需要时间。若持续解码速度低于源速率，本地过期帧会不断被丢弃，可能不再生成新的检测画面；需要降低路数、分辨率或调整资源后再次测量。没有实现关键帧跳转、RTSP 重连或摄像头时钟同步。

## 设备帧所有权（SOPHON v0.14.0）

每次读取都创建空的 `cv::Mat`，再移动包含它的 `unique_ptr<Frame>`。在检测、缩略图和 `bm_image` 包装使用结束以前保持这个 Mat 存活。不重复写入消费者正在引用的 Mat，不手动释放其 AVFrame，不设置 `AVFRAME_ATTACHED`。

此处理依据 SOPHON v0.14.0 的设备内存所有权实现：

- [VideoCapture](https://github.com/sophgo/sophon_opencv/blob/v0.14.0/modules/videoio/src/cap_ffmpeg_impl.hpp#L1925) 把解码 AVFrame 交给输出 Mat，然后清空内部 picture 指针。
- [Mat 分配与引用](https://github.com/sophgo/sophon_opencv/blob/v0.14.0/modules/core/src/matrix.cpp#L318) 和 [AVFrame 释放器](https://github.com/sophgo/sophon_opencv/blob/v0.14.0/modules/core/src/av.cpp#L22) 保持设备帧到最后一个 Mat 引用释放。
- FFmpeg 的[每帧引用](https://github.com/sophgo/sophon_ffmpeg/blob/v0.14.0/libavcodec/bm_dec.c#L803)、[解码器引用计数](https://github.com/sophgo/sophon_ffmpeg/blob/v0.14.0/libavcodec/bm_dec.c#L204)和[关闭操作](https://github.com/sophgo/sophon_ffmpeg/blob/v0.14.0/libavcodec/bm_dec.c#L1629)让已交出的设备帧可跨后续 read 及 EOF reopen 存活。

不同 SDK 二进制版本仍需验证相同行为。退出时先停止并 join 解码线程，再释放捕获器及它引用的局部对象；已在 SDK 中阻塞的读取需等待 SDK 超时，外层启动器仍保留终止保护。

## 本机测试

仅测试调度模块，不需要 SOPHON SDK：

```bash
cmake -S demos/hdmi_wall -B demos/hdmi_wall/build/realtime-tests -DREALTIME_TESTS_ONLY=ON
cmake --build demos/hdmi_wall/build/realtime-tests
ctest --test-dir demos/hdmi_wall/build/realtime-tests --output-on-failure
python3 -m unittest discover -s demos/hdmi_wall/tests -p 'test_*.py'
```

C++ 测试覆盖慢消费者取得最新帧、旧帧及时释放、限频时继续覆盖、5/8 FPS 时间间隔、极小 FPS 防溢出、过期边界、EOF、截止/停止及 10,000 帧并发计数。Python 测试覆盖 CLI 参数、互斥约束、RTSP URI 和 dry-run。

2026-09-09 已在 Windows 使用便携 Zig 0.13.0 的 C++ 编译器以 `-std=c++11 -O2 -Wall -Wextra -Werror` 构建并通过调度测试，CMake 的 `REALTIME_TESTS_ONLY` 配置、构建和 CTest（1/1）也通过。同日通过 ADB 在目标 ARM64 板子完成 SOPHON 主程序构建、CTest、Python 启动器测试以及实际 HDMI 验证。各路数结果和发现的问题见[上板验证报告](realtime-board-validation.md)。

## 上板验收

先构建并用单路短视频验证同帧检测框与反复 EOF 循环，再以相同输入/模型对比 `all` 与 `latest`。当前已测起点为 24 路 / 5 FPS 或 16 路 / 8 FPS，32 路尚未满足本轮实时要求。关注连续窗口内的解码 FPS、检测 FPS、源年龄 P95、主动丢帧数和内存使用；最新槽高水位应不超过 1，正常结束未处理帧数应为 0。以更低检测限频制造慢消费者，验证不会回放历史积压帧。

在负载下验证关闭播放器、正常截止和 Ctrl+C 能退出；RTSP 另验证断流行为与实际摄像头到 HDMI 延迟。源年龄应保持稳定而不是随运行时长持续增长；可接受的实际数值需按业务要求和上板测量确定。本机测试不代表设备链路性能或实时延迟验收。
