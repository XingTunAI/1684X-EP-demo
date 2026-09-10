# 回传开关与双卡 32 路对照

[HDMI 文档导航](../README.md) / [当前数据总览](current-data.md) / [展示与压测](showcase.md)

多设备播放器左上角新增 **READBACK ON / OFF** 按钮，点击或按 **R**，所有选中的卡一起切换。切换不重新加载模型、不减少后台通道；仍可选择其他设备页面。启动时默认 ON。

| 状态 | 每张卡实际工作 | HDMI 内容 |
|---|---|---|
| ON | 视频解码 → 预处理 → 模型推理 → 结果筛选 / 回传 → CPU 后处理；按预览频率生成缩略图并回传 | 原来的多路视频墙、同帧检测框和指标 |
| OFF | 视频解码 → 预处理 → 模型推理；跳过 score gate、模型输出读取、CPU 检测后处理和像素预览生成 | 各卡的路数、PCIe 链路、总 DEC / INF、TPU 与累计回传字节数；隐藏视频墙 |

OFF 推理输入来自这些通道的真实解码帧，沿用 `latest` 和送检年龄限制。它不是固定张量循环制造 TPU 负载，也不是纯解码。由于不读取输出，不能判断检测结果；完整逐帧记录用 `results_readback: false`、`detections: null` 标记，不能把它解释成“没有目标”。

当前样例没有视频编码阶段，界面标为 **ENCODE: N/A**。OFF 也不是 PCIe 零流量：压缩码流输入、任务提交、同步和设备状态查询仍然存在。关闭的是模型输出与预览像素的设备到主机回传。

## 启动

现有 `showcase.sh --mode showcase` / `--mode stress` 和 `multi_run.sh` 都有此按钮。注意 stress 的旧默认仍是 Gen2 ×1 20 路、Gen3 ×2 32 路。要两张卡都 32 路，并减少 ON 时的预览回传，可使用提供的 profile：

```bash
cd /userdata/1684X-EP-demo
sudo env DISPLAY=:0 \
  XAUTHORITY=/var/run/lightdm/root/:0 \
  SDL_VIDEODRIVER=x11 \
  PLAYER_LIB_PATH=/usr/lib/aarch64-linux-gnu \
  bash demos/hdmi_wall/showcase.sh --devices 0,1 --mode showcase \
  --profile demos/hdmi_wall/profiles/dual32-low-readback.json
```

默认正式运行 4 小时；短测可追加 `--duration 300 --telemetry-interval 1`。已有任务时先使用 `sudo bash demos/hdmi_wall/showcase.sh --stop`。更新代码后需要重新编译 `bash demos/hdmi_wall/build.sh`。

该 profile 对设备 0、1 分别设置 `streams: 32`、`gate_merge_budget_kib: 64`、`preview_fps: 3`、`output_buffer: "reuse"`。其他设备编号需相应修改 JSON；不通过设备编号猜测 PCIe 链路。

`preview_fps: 3` 只限制每路检测缩略图回传，约每 333 ms 才允许一次，实际还受推理完成时机影响；不会把解码或推理限到 3 FPS。页面合成上限仍为 10 FPS，物理 HDMI 刷新率是另一项指标。`output_buffer: reuse` 复用模型输出设备内存，减少反复分配。

## 怎么读数

1. ON 稳定运行后记录两张卡的 DEC / INF 和 TPU；不要只挑选 100% 的瞬时值。
2. 按 R。`SWITCHING / WAITING` 表示正在切换或等待新状态；已经开始的回传允许完成。每张卡显示 `READBACK OFF`，且累计 OUTPUT BYTES / PREVIEW BYTES 不再增长后，再开始 OFF 采样。
3. OFF 中 DEC 是抽帧前实际解码帧数，INF 是真实帧模型调用完成数。二者不是每路相加的理论值，也不代表编码能力。
4. 再按 R 恢复回传，检查所有视频格与检测框恢复，重新取稳定区间进行对照。

普通状态文件 `device_N/status.json` 每秒更新，速率来自最近 20 个完整 100 ms 时间桶。`streams[].decoded_count / inferred_count` 为累计成功次数；某区间平均 FPS = 累计次数增量 ÷ `snapshot_monotonic_s` 增量。模型输出和预览回传的区间 MB/s = 对应字节计数增量 ÷ 秒数 ÷ 1,000,000。

`readback-control.json` 保存所有卡共同的目标状态；`readback-events.jsonl` 记录按钮切换时间。各卡 `readback_enabled` 为已观察到的模式，`readback_inflight` 是此前已开始、尚未退出的回传处理次数。OFF 且 inflight 为 0 后，两个累计字节数应停止增长。字节数统计程序实际请求的有效载荷，不含 PCIe 包头等协议开销。

`model_only_completed / results_readback_completed` 分别累计两种路径的完成次数；正式汇总各路对应 `model_only_measured / results_readback_measured`。混合模式整场 summary 的 FPS 和阶段耗时不可直接当作单独 ON 或 OFF 性能，应按切换时间拆分稳定区间。

OFF 隐藏的是预览，不能把不更新的缓存图当作解码 STALL；解码仍通过 DEC 和解码状态独立观测。

## 双卡实测（2026-09-10）

两张卡同时运行，每卡 32 路，官方 1080p **24 FPS** 本地素材，YOLOv8s INT8 batch 1，latest、infer-fps 0、年龄门槛 250 ms、gate on、合并预算 64 KiB、输出内存 reuse、每路预览上限 3 FPS。运行 ID 为 `20260910T075010Z-b5b1bd80`，正式运行 240 秒，两卡均正常完成且计数核对通过。

期间有多次按钮切换。下表 OFF 使用第一个稳定关闭区间，ON 使用最后恢复后的稳定区间；切换后前 5 秒、正式测量边界与收尾不计入区间统计。解码 / 推理平均值由累计次数差分计算，TPU 使用区间内所有有效样本平均，未挑选峰值。

全部可用区间、采样数和回传增量保存在 [对照数据 JSON](data/readback-toggle-20260910.json)。原始按秒快照、按钮事件、逐卡状态与汇总保留在板端该运行目录内。

| 状态 / 卡 | 有效帧计数区间 | 总解码 FPS | 总推理 FPS | TPU 平均 / 样本数 | 输出回传增量 | 预览回传增量 |
|---|---:|---:|---:|---:|---:|---:|
| OFF / Gen2 ×1（device 0） | 17.00 s | 767.88 | 312.59 | 100% / 18 | 0 B | 0 B |
| OFF / Gen3 ×2（device 1） | 19.00 s | 768.00 | 314.37 | 100% / 18 | 0 B | 0 B |
| ON / Gen2 ×1（device 0） | 113.63 s | 768.02 | 261.14 | 95.18% / 110 | 3,540,136,320 B | 928,641,024 B |
| ON / Gen3 ×2（device 1） | 113.76 s | 767.94 | 276.04 | 99.98% / 110 | 3,478,117,104 B | 1,032,818,688 B |

OFF 区间两卡全部 32 路都在解码和推理，最慢单路推理分别为 9.76 / 9.79 FPS；`results_readback_completed` 增量为 0。ON 恢复了全部视频格及检测框，最慢单路推理分别为 8.09 / 8.59 FPS；模型输出和预览回传合计约 39.33 / 39.65 MB/s。

这说明该配置中去掉结果消费和预览回传后，两卡可同时让 TPU 达到满载采样，真实视频推理吞吐也提高了。**OFF 同时省去了 score gate、CPU 后处理和预览处理，因此这不是只隔离 PCIe DMA 的单变量实验**，不能将全部增益都归因于总线带宽。

32 × 24 = 768 FPS 是本次每卡输入供给上限；本次没有搜索解码器最大容量，也没有测视频编码。17–19 秒 OFF 区间只用于功能与短测对照，不能宣称四小时稳定性。帧计数与 TPU 为独立采样，区间端点和样本数不必完全相同。

此外验证了默认 baseline 输出分配路径、gate off、full 记录的双卡 45 秒兼容性：每卡 2 路，OFF 分别完成 966 / 960 次实际模型推理，记录中输出回传和预览字节为 0、detections 为 null；恢复 ON 后检测框正常，整场计数核对通过。此项仅为兼容性检查，不是 32 路性能结果。

开发验证：141 项 Python 测试（Windows 跳过 1 项 Linux FIFO 测试）、板端编译和 4 项 C++ 测试通过；实机验证鼠标点击、R 键切换与恢复。
