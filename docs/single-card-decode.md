# 单卡解码压测操作

更新日期：2026-09-07。本文描述当前脚本；历史结果见[解码测试结论](decode-results-20260907.md)，指标和状态解释见[报告阅读指南](benchmark-report-guide.md)，演示进度见[Demo 总览](demo-summary-20260907.md)。

## 1. 测试范围与当前状态

本 Demo 使用 SOPHON FFmpeg 硬件解码器测试一张 BM1684X 的多路解码性能。一路对应一个独立 FFmpeg 进程，32 路即 32 个解码进程；Python 负责启动、采样、监控、收尾和生成报告。每路使用指定卡上的独立解码实例，输出到 null，不画框、不编码、不显示、不做算法推理。

当前默认 `--measure-only`：记录实际性能，不因低于 25 FPS 判失败。25 FPS 是默认输入规格；运行异常、停帧和用户中断仍会记录。只有显式 `--acceptance` 才启用逐路窗口门槛，默认门槛 24.5 FPS。历史报告保留原来的 `fps_pass/fps_fail`，不回改。

已完成阶梯测试、32 路短测和 30 路正式采集 15 分钟：device 1 的 32 路总平均为 815.21 FPS、最差逐路窗口 23.26 FPS；30 路长测总平均为 749.96 FPS，中间一个窗口波动、最差逐路窗口 22.59 FPS。上述数据不是全程稳定容量承诺，也不能外推为算法容量。[解码＋推理](single-card-analysis.md)使用另一条图像处理链路，需单独查看结果。

## 2. 环境、设备与素材

硬件测试命令在 RK3588 的工程根目录执行。要求 Python 3.9+、SOPHON FFmpeg/ffprobe、驱动及可用设备；Python 调度器只依赖标准库。Windows 上的普通 FFmpeg 可以准备素材，不能验证板端硬解能力。

| 用户物理命名 | 软件参数 | PCI 地址 | 已核对的协商链路 |
|---|---|---|---|
| 卡 1 | `--device 1` | `0001:11:00.0` | PCIe 3.0 ×1 |
| 卡 2 | `--device 0` | `0004:41:00.0` | PCIe 2.0 ×1 |

上表为此前双卡测试的历史枚举。18:23 的 ×2 配置中，`0001:11:00.0` 已变为 device 0；下面复测命令显式选择 device 0。纯解码脚本默认 device 0，分析脚本默认 device 1，因此分析时应显式覆盖。历史成绩不随设备编号变化而改写。

| 素材 | 时长 | 使用场景 |
|---|---:|---|
| `datasets/stress/bbb_1080p25_h264_8mbps.mp4` | 596.44 秒 | 默认短测；每档重新启动输入 |
| `datasets/stress/bbb_1080p25_h264_8mbps_20min.mp4` | 1200.08 秒 | 3 分钟预热＋15 分钟正式采集 |

两份均为 BBB 动画派生的 1080p25 H.264 素材，32 路重复读取同一文件，不等于 32 路真实摄像头。视频、模型、原始结果不随 Git 同步，需单独准备；来源、大小、哈希与传输方式见[素材说明](test-media.md)。

本地默认使用 `-stream_loop -1` 循环、`-re` 按源时间节奏读取。短素材在文件结尾附近曾出现解码送包错误，根因未修复。长测应选连续 20 分钟版本；总运行时间超过该素材时长的测试仍可能触及循环，不能仅增加 duration 就认为已避开该问题。

先检查环境：

```bash
bm-smi
which ffmpeg ffprobe
ffmpeg -decoders | grep -E 'h264_bm|hevc_bm'
python3 -m unittest discover -s src/single_card_decode/tests -v
```

配套 18 项测试已在板端通过，用于验证调度和统计逻辑，不代表硬件性能测试通过。预检会检查解码器、输入文件、1920×1080、编码类型及与 `--target-fps` 一致的声明帧率，不匹配会拒绝运行。若使用 HEVC 素材需显式 `--codec hevc`。`--target-fps` 不会把输入视频转成该帧率。

## 3. 一键执行与复测命令

同一张卡的测试依次运行。以下命令均在设备工程根目录执行，正式测试期间避免其他同卡任务及大文件传输。

```bash
# 一路检查：预热 30 秒，采集 60 秒
bash scripts/run_single_card_decode_auto.sh --device 0 --steps 1 \
  --warmup 30 --duration 60 --window 30

# 阶梯摸底：每档约 3 分钟，四档总计约 12 分钟，另计启动和收尾
bash scripts/run_single_card_decode_auto.sh --device 0 --steps 8,16,24,32

# 仅测 32 路：约 3 分钟
bash scripts/run_single_card_decode_auto.sh --device 0 --steps 32 \
  --measure-only --warmup 60 --duration 120 --window 60

# 复测 30 路：预热 3 分钟，正式采集 15 分钟
bash scripts/run_single_card_decode_auto.sh --device 0 --steps 30 \
  --input datasets/stress/bbb_1080p25_h264_8mbps_20min.mp4 \
  --measure-only --warmup 180 --duration 900 --window 60
```

这些是分别运行的示例，不需要全部重复执行。每档启动后统一预热，预热帧不计入正式 FPS。每档结果正常写入后释放本档进程，再启动下一档；低帧率在测量模式下继续加压。运行异常、中断或显式验收不通过时停止后续档位。

复现历史帧率判定时，把 `--measure-only` 换成 `--acceptance --min-fps 24.5`。24.5 是本次历史参考值，不是客户确认的业务验收条件。

一键 Python 入口为 `python3 scripts/run_single_card_decode_auto.py`，与 Shell 入口等效。它自动切换到工程目录，相对输入和输出路径以工程根目录为准。直接运行 `python3 src/single_card_decode/stress_decode.py` 时，相对路径以当前工作目录为准；旧的 `tools/stress_decode.py` 与 `src/single_card_decode/run_auto.py` 仅保留兼容。

### 主要参数

| 参数 | 一键脚本默认 | 含义 |
|---|---|---|
| `--device` | 0 | 软件设备编号 |
| `--steps` | 8,16,24,32 | 正整数，去重且严格递增 |
| `--input` | 596.44 秒短素材 | 同一文件显式复制为多路输入 |
| `--warmup` | 60 | 每档预热秒数 |
| `--duration` | 120 | 每档正式采集秒数，不含预热 |
| `--window` | 60 | 窗口目标秒数，duration 必须不小于 window |
| `--target-fps` | 25 | 输入声明帧率校验值 |
| `--min-fps` | 24.5 | 仅显式验收时用于判定；解析仍要求不大于 target-fps |
| `--stall-timeout` | 15 | 距最后新帧的超时秒数，包含启动/预热检查 |
| `--output` | results/decode | 自动在其下创建唯一结果目录 |
| `--dry-run` | 关闭 | 仅打印命令，不启动硬件预检或压测 |

直接运行核心脚本时，默认档位为 1、采集 300 秒，且必须指定输入；与一键入口不同。完整参数可执行 `python3 scripts/run_single_card_decode_auto.py --help` 查看。需要指定 SDK 工具时使用 `--ffmpeg /实际路径/ffmpeg --ffprobe /实际路径/ffprobe`。

## 4. 运行期间怎么看、怎么停

控制台预检通过后打印唯一的 `Results:` 目录。新终端进入这轮目录执行：

```bash
watch -n 5 cat report.md
```

报告在开始、每档结束和最终收尾时自动更新。单档运行期间可能一直显示等待该档完成，这是正常行为；watch 每 5 秒读取文件，不代表报告每 5 秒产生最终统计。原始进度持续写入 `step_XX/stream_XX/progress.log`，原始资源采样在 `step_XX/monitor.jsonl`。

需要后台运行时，使用唯一控制台日志名，保存该次启动 PID：

```bash
mkdir -p results
console_log="results/decode_console_$(date +%Y%m%d_%H%M%S).log"
nohup bash scripts/run_single_card_decode_auto.sh --device 0 --steps 32 \
  > "$console_log" 2>&1 < /dev/null &
decode_pid=$!
printf 'PID=%s log=%s\n' "$decode_pid" "$console_log"
tail -f "$console_log"
```

前台压测用 Ctrl+C；后台压测可对确认仍属于本轮的 PID 执行 `kill -INT 实际PID`，由脚本收尾自己的子进程。`tail -f` 中 Ctrl+C 只结束查看日志，不结束后台测试。不要重复启动同一压测或通过批量终止全部 FFmpeg 来停止某一轮。

## 5. 结果、异常和归档

先看 `report.md`，确认设备、素材、档位、模式和运行状态，再看总吞吐、各路平均及最低窗口。完整文件清单、计算方式、状态和常见问题见[报告阅读指南](benchmark-report-guide.md)。

| 情况 | 排查方法 |
|---|---|
| 没有 Results 目录 | 先读终端预检报错，检查素材与 SOPHON 解码器；不是性能不达标 |
| measured 但 FPS 低 | 测量已完成，默认不应用性能门槛；记录数值与资源趋势 |
| 旧报告 fps_fail | 逐路窗口低于旧门槛，不等于进程崩溃 |
| error / 长时间无新帧 | 查看档位 summary 的 reason、对应 stderr 和 progress；不要只放大超时掩盖停帧 |
| 文件结尾附近 ret=-13 | 对照素材时长和最后进度，使用 20 分钟文件复测；本轮未证明根因 |
| 总 FPS 接近或高于 800 | 核对窗口和各路，不据此推断每路持续 25 FPS；可能存在节奏追赶 |
| bm-smi TPU 利用率低 | 纯解码未运行模型，TPU 利用率不能代表解码单元负载 |

Windows PowerShell 在本地工程根目录拉回一轮结果（替换测试编号）：

```powershell
New-Item -ItemType Directory -Force results/board-auto | Out-Null
adb -s bf43cc5e0819e5ad pull /home/linaro/1684X-EP-demo/results/decode/20260907_111959_35624 results/board-auto/
```

保留整轮目录，包括 config、run_state、summary、报告、CSV、逐路日志和资源记录，勿只交付截图。结果目录与素材均不提交 Git，仓库保存代码、操作文档和测试结论。报告重新生成命令见阅读指南；不要修改旧配置后覆盖历史结论。

## 6. 可选能力与本轮未测项目

`--throughput` 取消本地读取限速，标记为 `offline_throughput`；它与当前限速基线不同，本轮没有给出离线解码极限结论。

`--inputs-file` 可提供每行一个独立本地文件或 RTSP 地址，数量至少覆盖最大档位且地址不能重复。文件内相对路径以清单所在目录为准，空行和以 # 开头的注释跳过。RTSP 使用 TCP，脚本不额外加 -re；本轮结果均来自本地文件，不是 RTSP 性能验证。若后续分享含 RTSP 的原始配置和日志，应检查其中是否保存了连接凭据。

当前阶段演示已完成，未追加 28 路长测、抽帧或实时显示开发。生产长稳、真实丢帧、端到端时延和多卡联合容量均未由本轮单卡测试证明。
