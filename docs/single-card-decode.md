# 单卡解码压测操作

## 当前默认：记录性能，不做帧率验收

新测试默认只测性能，完整运行显示 `measured`，不因低于 25 FPS 判失败。25 FPS 仅代表输入视频帧率；每路平均/最低窗口和总吞吐按实际值报告。进程退出、停帧与用户中断仍单独记录。历史报告的 fps_pass/fps_fail 保留，不回改原始数据；需要复现旧门槛时显式加 `--acceptance`。

第一阶段只测硬件解码：单张卡持续解码 32 路 1920×1080、25 FPS 视频。算法推理、检测结果、编码输出及端到端延迟不在本阶段内。

客户同时要求分析结果和带框视频输出时，使用[单卡完整链路压测 demo](../src/single_card_pipeline/README.md)。本工具只作为解码对照，不能代替完整链路验收。

自动测试入口：在板端工程根目录运行 `bash scripts/run_single_card_decode_auto.sh`，也可使用 `python3 scripts/run_single_card_decode_auto.py`。默认卡 0，8/16/24/32 路，每档预热 60 秒、采集 120 秒；失败停止。结果目录自动生成 `report.md`、`stages.csv`、`streams.csv`，同时保留原始 JSON 和日志。详细参数见[demo 说明](../src/single_card_decode/README.md)。

测试视频位于工程内 `datasets/stress/bbb_1080p25_h264_8mbps.mp4`。下文硬件压测命令在 RK3588 上执行，不是在 Windows CMD 中执行。

`src/single_card_decode/stress_decode.py` 使用 Python 3.9+ 标准库，调用 RK3588 上安装的 SOPHON FFmpeg/ffprobe。普通 Windows FFmpeg 只用于素材准备，不能用于验证算力卡性能。

## 准备素材

本地下载和准备过程见[测试素材说明](test-media.md)。把 `datasets/stress/bbb_1080p25_h264_8mbps.mp4` 与本项目脚本复制到 RK3588，保持路径一致。媒体文件和运行结果已忽略，不会随 Git 同步。

本地文件默认循环播放，并使用 `-re` 按源帧率读取。每个进程建立独立硬件解码实例，但读取的是同一份素材，结果属于本地文件解码基线，不代表 32 路摄像头网络接入、输入内容多样性或算法分析已验收。

## 1. 先检查环境和一路

在 RK3588 的项目根目录执行：

```bash
bm-smi
which ffmpeg ffprobe
ffmpeg -decoders | grep -E 'h264_bm|hevc_bm'

python3 src/single_card_decode/stress_decode.py \
  --device 0 \
  --input datasets/stress/bbb_1080p25_h264_8mbps.mp4 \
  --steps 1 --warmup 30 --duration 120 --window 60
```

脚本先核对硬件解码器、输入编码、分辨率和声明帧率，再创建结果目录。输入不符合 1080P/25 FPS 时拒绝运行，避免把其他视频规格的成绩混入结果。输入码率等探测信息保存在配置快照中。

若系统默认 FFmpeg 不是 SOPHON 版本，用 `--ffmpeg /实际路径/ffmpeg --ffprobe /实际路径/ffprobe` 指定。SDK 版本不兼容、设备不匹配等错误会写入日志，不会自动改用软件解码。

## 2. 再逐步增加到 32 路

```bash
python3 src/single_card_decode/stress_decode.py \
  --device 0 \
  --input datasets/stress/bbb_1080p25_h264_8mbps.mp4 \
  --steps 8,16,24,32 \
  --warmup 180 --duration 900 --window 60 \
  --target-fps 25 --min-fps 24.5
```

每档启动完成后统一预热，再按共同时间窗口统计各路实际完成帧数。任一路提前退出、超过默认 15 秒无新帧、统计错误时停止继续加压；低帧率只记录，不停止。Ctrl+C 会保存未完成状态，只停止本轮创建的子进程。

显式验收模式的 `24.5 FPS` 是历史参考门槛，为 FFmpeg 进度上报和窗口边界留出容差，不是客户已认可的验收门槛。正式验收前应约定并固定。启动较慢时可调整 `--stall-timeout`，不能用它掩盖持续停帧。

每个进程使用输入端 `h264_bm/hevc_bm` 解码器和 `sophon_idx` 选择卡，输出到 null，不编码、不写视频、不设置输出 FPS。硬件参数参考[算能视频编解码性能测试](https://doc.sophgo.com/sdk-docs/v23.07.01/docs_latest_release/docs/SophonSDK_doc/zh/html/performance_test/2_video_codec.html)，仍需在实际 SDK 版本上验证。

## 3. 查看结果

默认输出：`results/decode/日期时间_进程号/`。

| 文件 | 内容 |
|---|---|
| `config.json` | 本次参数、各输入视频元数据、FFmpeg 版本 |
| `summary.json` | 各档状态和逐窗口 FPS |
| `step_XX/summary.json` | 当前档实际采集时间、各路 FPS、最差窗口 FPS及失败原因 |
| `step_XX/samples.csv` | 共同采样时刻、逐路累计帧数和距最近新帧的时间 |
| `step_XX/stream_XX/progress.log` | FFmpeg 原始进度 |
| `step_XX/stream_XX/stderr.log` | 该路解码警告和错误 |
| `step_XX/monitor.jsonl` | 约每 5 秒采集一次 bm-smi 和 Linux CPU/内存/网络原始计数 |

状态说明：默认 `measured` 表示测量完成；显式验收模式中，`fps_pass` 仅表示逐路窗口 FPS 达到配置门槛；`fps_fail` 表示存在未达标窗口；`error` 表示进程退出、停帧等异常；`incomplete` 表示用户中断。按计划终止 FFmpeg 后的非零退出码记录在 `exit_codes_after_cleanup`，不要与测试中提前退出混淆。

监控缺失记为 null，不等于零占用。CPU/网络计数是原始数据，后续分析需按时间差计算。逐帧端到端时延、解码器内部队列和真实丢帧尚未测量，不能由 `fps_pass` 推断整条业务链路稳定达标；FFmpeg 的部分警告仍需检查原始日志。

当前一条流对应一个 FFmpeg 进程。它测的是这一配置的实际能力，不是硬件架构极限。窗口 FPS 使用进度管道的完成帧计数，有上报粒度误差；用较长窗口减少影响。

## 4. RTSP 与离线吞吐对照

RTSP 测试在本地基线通过后进行。创建一个文件，每行一个独立 RTSP 地址，至少提供与最大档位相同数量的不同地址，再运行：

```bash
python3 src/single_card_decode/stress_decode.py \
  --device 0 --inputs-file /实际路径/streams.txt \
  --steps 1,8,16,24,32 --warmup 180 --duration 900
```

RTSP 使用 TCP 传输，脚本不额外限速。结果目录会包含输入地址和 FFmpeg 日志，分享前去除账号密码。

本地视频添加 `--throughput` 可取消读取限速，结果会标记为 `offline_throughput`。这个模式测离线解码速度，不能作为实时接入或算法容量结论。

使用 `--dry-run` 仅打印各路参数数组，检查绑定卡及输入，不启动任何解码进程。

## 下一步

实机解码测试已完成，详见[解码结论记录](decode-results-20260907.md)。30 路、15 分钟长测平均约 25 FPS/路，但有一个正式窗口出现波动，判定 fps_fail；不能标记为全程稳定通过。现在进入[单卡解码＋推理压测](single-card-analysis.md)。

## 5. 复现 30 路长测及同步

物理卡 1（PCIe 3.0 ×1）对应 `--device 1`；物理卡 2（PCIe 2.0 ×1）对应 `--device 0`。历史报告的卡编号均为软件编号。

长测用连续 20 分钟素材，避免本次 18 分钟总运行期间触及文件结尾。原 596.44 秒文件的循环附近曾发生送包异常，问题尚未修复，不应直接用原文件重复长测后归因于卡性能。

```bash
cd /home/linaro/1684X-EP-demo
bash scripts/run_single_card_decode_auto.sh \
  --device 1 --steps 30 \
  --input datasets/stress/bbb_1080p25_h264_8mbps_20min.mp4 \
  --warmup 180 --duration 900 --window 60
```

每档结束和整轮收尾自动写入 `report.md`，单档长测期间它可能保持“等待当前档完成”；以控制台打印的 Results 目录为准。先看报告结论，再看 `streams.csv` 的逐路最差窗口；运行异常查对应流的 `stderr.log`。正常计划终止的 FFmpeg 退出码不直接算异常。

在 Windows PowerShell 同步某一轮结果（把测试编号换成控制台显示值）：

```powershell
# 先在 PowerShell 中进入工程根目录
adb -s bf43cc5e0819e5ad pull /home/linaro/1684X-EP-demo/results/decode/20260907_111959_35624 results/board-auto/
```

素材同步与校验命令见[素材说明](test-media.md)。传输应安排在压测前后，避免额外 I/O 干扰。本地 `datasets/` 与 `results/` 不随 Git 提交，但交付时应保留对应素材、报告和原始日志。
