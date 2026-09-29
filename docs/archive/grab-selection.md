# VPP 展开前选帧实验（2026-09-28）

本实验保持现有解码配置，用 SOPHON OpenCV 的 `grab()` 连续解码，仅对固定间隔选中的帧调用 `retrieve()`。它不是自适应选择最新帧；选中帧仍进入原有 latest 队列、年龄过滤、推理和预览路径。

## 实现及调用

新增底层参数 `--retrieve-every N`，范围 1–120，默认 1 保留原 `read()` 路径。N>1 使用 `grab()`，只在零起始帧序号满足 `sequence % N == 0` 时 retrieve。只允许 latest 推理模式，暂不与全解码观测或分段追赶同时使用。首帧预热照旧；解码帧序号和源时间不因抽帧重置。

`decoded`/`decoded_measured` 统计所有成功解码帧，`skipped_before_vpp`/`skipped_before_vpp_measured` 单列主动跳过 retrieve 的帧。原有 `policy_drops` 仍只记录 latest 覆盖、过期和退出丢帧。完整账目为 decoded = completed + policy_drops + skipped_before_vpp。JSON 是本实验完整计数来源，旧 CSV 没有新增跳帧列。

使用独立构建，默认 `build/hdmi_wall.pcie` 和普通启动器不替换。以下在板端仓库根目录执行；先通过原运行的停止入口结束占用同卡的任务：

```bash
HDMI_BUILD_DIR=/userdata/1684X-EP-demo/demos/hdmi_wall/build-grab-select \
  bash demos/hdmi_wall/build.sh

sudo python3 tools/diagnostics/legacy/run_grab_selection.py \
  --device 0 --streams 32 --duration 60 --steps 1,3,2 \
  --output /userdata/1684X-EP-demo/data/results/grab-selection-new
```

输出目录必须不存在。脚本逐档启动 HDMI 与播放器，采集遥测并在结束后清理本次子进程。仅跑每3帧取1帧的300秒展示可用 `--steps 3 --duration 300`，同样需新输出目录。本入口依赖本板已有长素材、YOLOv8s INT8和辅助模型，实际路径保存于每档 `command.json`。

## 验证口径

板端探针 `tools/diagnostics/grab_retrieve_probe.cpp` 拦截实际 `bm_trigger_vpp` 调用并按 grab/retrieve 阶段计数。首次实测120次grab、40次retrieve：grab阶段0次VPP，retrieve阶段40次VPP。这是读取路径的展开请求，不含推理预处理、预览和其他VPP用途。

业务对照采用同一实验二进制，device 0 / PCIe2 ×1、32路本地1080p24、2400秒素材、YOLOv8s INT8、10秒预热、60秒正式窗口、reuse、gate预算64KiB、同步预览上限3 FPS/路、summary记录。长素材不会在本轮结束前循环；本次未修复短片EOF后的追赶问题。

固定每3帧取1帧在24FPS输入下最多提供8帧/秒/路，32路最多256FPS；每2帧最多384FPS。不能将这种采样解释为全帧分析，也不能由吞吐提升推断检测召回率不变。测试顺序固定、单次短测，场景及初始化波动可能影响结果。

## 正式结果

三档均完成60秒正式统计，退出成功，逐路账目完整，无整段零分析通道：

| 配置 | 总解码FPS | 总推理FPS | 相对原路径 | TPU均值 | 最低逐路推理FPS | 最长无分析结果间隔 |
|---|---:|---:|---:|---:|---:|---:|
| 原路径，每帧retrieve | 769.78 | 246.97 | — | 88.98% | 7.30 | 3.199秒 |
| 每3帧取1帧 | 768.02 | 255.23 | +3.35% | 88.24% | 7.92 | 0.335秒 |
| 每2帧取1帧 | 768.00 | 269.88 | +9.28% | 94.93% | 8.28 | 0.260秒 |

每3帧/每2帧配置在正式窗口分别跳过30,721/23,040次retrieve，约512/384次每秒；根据已验证的读取路径，这些帧避免了展开调用，不代表省去了所有VPP请求。平均应用预处理耗时分别为69.30、27.43、27.10ms（线程调用墙钟，含排队，非硬件纯执行时间）。原路径全运行有966次过期丢帧，两种选帧路径为0；全运行与正式窗口不能混用。

原路径的3.199秒空窗是本轮真实异常，不能忽略；本轮只有一次顺序对照，不能将全部增益认定为稳定收益。原路径解码略高于768FPS也可能包含早期滞后后的追赶。选帧两档结束前采样均无STALE；短测不能证明长稳。播放器日志仍有既有的Rockchip GL驱动加载报错，ffplay没有以失败退出；未将此写成显示链路完全无异常。

## 像素与调用次数核对

探针使用120帧，以原路径每帧retrieve为参考：每3帧取1帧的40对、每2帧取1帧的60对同序号256×144 BGR缩略图全部SHA256一致。保存流程是设备YUV → BMCV转换 → 显式device-to-host → PNG。此项检查支持所选帧缩略图一致，不是全分辨率逐像素或检测结果/召回率验收。

读取阶段计数：参考120次grab/120次retrieve，对应0/120次VPP；选帧120次grab/40次retrieve，对应0/40次VPP。保存缩略图额外产生的VPP单列在other阶段，不计入读取展开。早期直接imwrite设备YUV Mat的校验无效，旧产物保留作排错记录，不作为画面一致性证据。

本轮代码已经在板端独立构建并完成HDMI短测，默认启动器和默认二进制未替换。本次先实现固定间隔方案，尚未实现“仅在推理线程空闲时取最新解码帧”的自适应需求选帧。

原始结果位于板端 `data/results/grab-selection-20260928/`，包括各档command/state、worker/summary、telemetry、comparison、verification、pixel-verification和探针日志；本地同路径保存汇总副本。对照脚本为 `tools/diagnostics/legacy/run_grab_selection.py`，源实现为 `demos/hdmi_wall/main.cpp`。本轮源文件已在板端保留修改前备份 `main.cpp.before-grab-select`。
## 预览效果

三档单路预览上限均为3FPS，整墙刷新上限10FPS；两者与推理FPS不同。

| 配置 | 每路实际平均预览FPS | 最低单路预览FPS | 平均预览处理墙钟 |
|---|---:|---:|---:|
| 原路径 | 2.3125 | 2.20 | 35.93ms |
| 每3帧取1帧 | 2.4036 | 2.35 | 14.60ms |
| 每2帧取1帧 | 2.3938 | 2.35 | 15.53ms |

预览FPS由正式窗口内previews_submitted_measured除以测量秒数计算，为生成/提交计数，不是显示器扫描输出实测。每2帧路径最后保存的wall.bmp中32路都有图像与检测框，各路标记OK，无可见花屏或STALE。该文件是应用生成的拼屏，不是物理HDMI截屏，不能仅凭一帧判断运动流畅度。低回传3FPS上限仍使运动呈现明显跳帧，实际每路约0.42秒更新一次；此次优化主要降低等待和停顿，未将预览升级为实时视频帧率。