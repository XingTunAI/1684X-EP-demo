# PCIe 2.0 ×1：32 路分析、结果视频与实时预览对照

状态：2026-09-23 已完成，见[正式结果与视频](pipeline32-results.md)。确认 device 0（0002:21:00.0）和 device 1（0004:41:00.0）均为 PCIe 2.0 ×1；device 2 为 PCIe 3.0 ×1，本轮保持空闲。

目的：比较用户需要的“32 路无视频墙、保存带框结果视频”和“32 路实时视频墙”，并区分图像路径、编码、预览及过期策略的影响。

## 实验矩阵

| 名称 | 处理路径 | 输出 | 用途 |
|---|---|---|---|
| analysis_device | 普通逐帧分析，device-bgr | 检测 JSON | 复核约 276 FPS 的已有基线 |
| analysis_bgr | 普通逐帧分析，bgr | 检测 JSON | 与编码组保持相同图像路径 |
| encode_bgr | 普通逐帧分析，bgr，画框及编码 | 每路独立 output.mp4 和检测 JSON | 用户要求的结果视频；32 个 H.264 1080p25 文件 |
| wall_no_preview | 视频墙 latest，250 ms 门槛，无缩略图 | 逐路汇总 | 隔离预览成本，仍启动同一显示框架 |
| wall_preview | 视频墙 latest，250 ms 门槛，每路预览上限 10 FPS | 板端实时拼屏和逐路汇总 | 正常实时预览 |
| wall_preview_no_age_limit | 同上一组，仅把过期门槛改成 0 | 板端实时拼屏和逐路汇总 | 检验过期门槛是否导致推理缺少输入 |

统一同一卡、1080p25 H.264 公路素材、YOLOv8s INT8 B1、score gate、64 KiB 合并预算、输出缓冲复用。默认每组预热 30 秒、测量 60 秒，运行正反序两轮，其他卡不运行测试。前后运行独立启动，不同时执行两种工作负载。

普通分析与视频墙有进程/线程组织、图像路径、源时钟和记录方式差异。这是完整业务路径对比；不能将二者差值全部解释为预览成本。analysis_bgr 与 encode_bgr、wall_no_preview 与 wall_preview、wall_preview 与 wall_preview_no_age_limit 才是各自针对一个配置变化的对照。

## 为什么要单测过期门槛

当前视频墙代码以本地素材的计划播放时间 `frame->due` 判定是否过期，分别在解码后和送检前检查。解码落后时，刚解码成功的帧也可能立即过期。若关闭门槛后恢复吞吐且零检测通道减少，支持过期策略参与掉速；仍需查看源帧年龄，不能将旧帧堆积后的高吞吐认定为实时恢复，也不能据此断言驱动或硬件的唯一根因。

## 板端执行

在 `/userdata/1684X-EP-demo` 运行。先用 `bm-smi --noloop` 和 sysfs 确认设备编号/BDF/链路，脚本还会再次核验。以下沿用上一轮映射；不适用于未经确认的其他板子。

```bash
python3 tools/diagnostics/run_pipeline32_comparison.py \
  --device 1 --bdf 0004:41:00.0 \
  --output data/results/pipeline32-20260923 \
  --dry-run
```

正式运行去掉 `--dry-run`。运行目录必须不存在，脚本不会覆盖旧结果。默认素材/模型位置参见 `--help`，可显式覆盖。需要当前 YOLOv8 和 HDMI 编译产物、SOPHON SDK、ffprobe，以及可使用 `DISPLAY=:0`、`/var/run/lightdm/root/:0` 的板端 HDMI 会话；播放环境沿用已有专项工具。

首次恢复设备连接后，先执行单路编码烟测，确认编码器和实际视频文件可用，再执行 32 路：

```bash
python3 tools/diagnostics/run_pipeline32_comparison.py \
  --device 1 --bdf 0004:41:00.0 --streams 1 \
  --warmup 10 --duration 10 --repeats 1 --cases encode_bgr \
  --output data/results/pipeline32-encode-smoke-20260923
```

脚本保留所有编码轮次，默认按码率和收尾余量预留约 9 GiB 磁盘空间。每组启动命令、日志和 TPU 采样写入独立目录；失败组保留错误，不编造为零 FPS 成功结果。无需更改现有 Demo 默认行为。

## 输出与比较口径

- `comparison.md`：每轮总检测 FPS、最低单路 FPS、零完成通道、TPU、检测结果及预览有效载荷 MB/s。
- `comparison.json`：以上数据以及逐路 FPS、阶段平均计时、源帧年龄/播放计划落后、视频路径和完整性核验帧数。
- `r*_encode_bgr/worker/<run-id>/step_32/stream_*/output.mp4`：32 路带框结果视频。ffprobe 核验 H.264、1080p 和实际帧数，要求与检测记录及编码提交数一致。
- `r*_wall_*/worker/summary.json`：解码 FPS、逐路窗口、过期/覆盖丢帧、帧年龄、阶段和回传数据。

性能仅统计正式窗口；视频和完整性核验包括预热及收尾。视频封装为 25 FPS 不代表处理速度达到每路 25 FPS：慢速逐帧分析在测量期间只处理源视频的一段，输出并非整段输入处理完毕。

普通逐帧入口没有独立解码完成计数，比较表将该项留空，不能用检测 FPS 冒充独立解码能力。视频墙丢帧计数是整个进程生命周期，不直接除以正式时长。源帧年龄仅覆盖成功完成检测的帧，零完成通道不代表年龄为零。

回传字节只覆盖显式计数的模型结果与预览，不包含 BGR 图像桥接、编码输入/码流、协议等全部流量。SDK 阶段时间包含并发排队，不能相加当成硬件独占时间。

编码位置：现有 `encode_bgr` 使用 SOPHON OpenCV VideoWriter，并传入 BM1684X 设备编号。2026-09-23 的运行日志明确出现 `vpu_EncInit`、`sophon_idx 0`、`VPU core index 4`；主控执行调用、BGR 图像处理和画框，实际编码由 BM1684X VPU 执行。本实验不是 RK3588 MPP 编码测试，也不是主控 CPU 软件编码测试。

视频墙保持 decode observation 关闭，因为现有观察模式会额外生成比较预览，改变工作负载。若要测量所有解码帧的源时间落后，需要后续加入不生成额外预览的纯计时采样。

## 双卡同时运行与交换角色

用户要求利用两张 Gen2 ×1 卡同时对比。使用 `run_dual_pipeline32.py`，依次运行以下配对，每对交换卡号再运行一次：

1. 保存带框视频 / 正常视频墙。
2. 设备内图像分析 / 正常视频墙。
3. 无过期门槛视频墙 / 正常视频墙。
4. BGR 分析 / 保存带框视频。
5. 无预览视频墙 / 正常视频墙。

每张卡 32 路，预热 30 秒、正式 60 秒。两卡独立完成初始化，正式窗口不保证完全对齐，`overlap.json` 明确保存窗口重叠时长及起点差。两卡共享 RK3588 的 CPU、内存、显示和磁盘；结果属于双卡并发负载，不能当作各卡独占主机的严格单变量实验。两组视频墙同时运行时各有一个播放器，屏幕前后遮挡不代表后台那组停止处理。

```bash
python3 tools/diagnostics/run_dual_pipeline32.py \
  --devices 0 1 --bdfs 0002:21:00.0 0004:41:00.0 \
  --output data/results/dual-pipeline32-20260923-v2
```

首轮 `dual-pipeline32-20260923` 的编码组被 15 秒无进度保护提前停止：worker 每 25 帧才输出进度，低速编码不能使用该默认门槛。修正后的实验命令为所有普通分析/编码组显式传入至少 120 秒 `--stall-timeout`，保留有界测试时长及退出保护。检测性能直接从逐帧 JSON 的正式完成时间计算，不用每 25 帧的进度日志估算。失败轮次不参与正式并发对比。

切换双卡前，独占 device 1 的 `pipeline32-20260923/r0_analysis_device` 已完成：279.85 检测 FPS、最低单路 8.72 FPS、TPU 96.97%。后续单卡组因改为双卡实验被主动中断，不计入结果。
