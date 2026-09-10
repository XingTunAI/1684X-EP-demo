# 抽帧设置与历史画面对照

[仓库首页](../../../README.md) / [HDMI 文档导航](../README.md#文档导航) / [实测索引](results.md)

本页先解释当前抽帧参数，再保留 2026-09-09 的 YOLOv8n / 公路 1080p25 历史对照。该组的 32 路未达标结论仅适用于当时条件；当前普通 32 路入口与验证见 [showcase](showcase.md)和 [实测索引](results.md)。

## 抽帧与实时处理

`latest` 将每路解码与检测分开执行。解码线程按输入节奏持续读帧，待检测槽容量为 1；新帧覆盖仍在等待的旧帧。检测线程空闲且达到限频间隔后，才取出当时最新的一帧。正在检测的帧会正常完成，检测框与画面始终对应同一帧。

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--policy latest\|all` | `latest` | `latest` 优先处理新帧；`all` 保留原来的串行逐帧处理方式，便于对照。 |
| `--infer-fps` | `0` | 每路检测启动速率上限，支持非负有限小数；`0` 按实际处理能力运行，不设置上限。 |
| `--max-frame-age-ms` | `latest` 为 `250`，`all` 为 `0` | 送检前按本地帧的计划读取时刻或 RTSP 帧的解码完成时刻检查年龄，超龄则丢弃；`0` 关闭检查。支持非负有限小数。 |

`--policy all` 仅接受 `--infer-fps 0 --max-frame-age-ms 0`；省略这两个参数时自动使用 0。`--infer-fps 8` 表示每路至多约每 125 ms 启动一次检测，实际完成 FPS 仍取决于解码、模型、设备与输出开销。

### 上板实测的 HDMI 效果

2026-09-09 在 RK3588 + 单张 BM1684X（device 1，PCIe Gen3 ×2）上对比同一份 1080p25 公路视频，使用 YOLOv8n INT8 batch 1、score gate 开启、HDMI 预览开启，得到以下结果：

| 配置 | 正式时长 | 每路实际检测 FPS 范围 | 最差一路结果源帧年龄 P95 | 观察 |
|---|---:|---:|---:|---|
| 24 路逐帧 `all` | 60 秒 | 11.25–11.52 | 33.54 秒 | 画面在更新，但持续落后源视频，出现 STALE |
| 24 路 `latest`，检测上限 8 FPS | 120 秒 | 5.57–6.19 | 341 ms | 最终各路都有结果；首个 10 秒窗口有 1 路未出结果 |
| **24 路 `latest`，检测上限 5 FPS** | **60 秒** | **4.97–5.00** | **116 ms** | **源时间线持续推进，适合作为本次演示设置** |
| 16 路 `latest`，检测上限 8 FPS | 60 秒 | 7.98–8.00 | 96 ms | 需要更高检测频率时可选择较少路数 |

这里的 P95 取各路统计中的最大值，计时从本地帧计划读取到检测完成。24 路逐帧模式结束前最旧结果已落后约 **35 秒**，改为 latest / 5 后正式区间所有结果的最大源帧年龄为 **293 ms**。抽帧使画面能跟上当前源时间；HDMI 预览上限仍为 10 FPS，每路新画面的频率也受检测完成速率影响。这不是摄像头到显示器的端到端延迟测量。

<details>
<summary>查看 24 路 HDMI 实际画面对照</summary>

逐帧处理：画面持续更新，但源年龄已经增长到数十秒，各格标记为 STALE。

![24 路逐帧处理 HDMI 实际截图](../images/hdmi-wall-all24.png)

抽帧、每路检测上限 5 FPS：处理当前帧，显示主动丢帧数，保持源时间线推进。

![24 路抽帧 5 FPS HDMI 实际截图](../images/hdmi-wall-latest24-5fps.png)

两张截图来自各自运行中的一次采样，取样时刻不同；定量比较以上表的完整正式区间统计为准。画面素材出处和署名见[截图素材](results.md#截图素材)。

</details>

**本组历史配置的 32 路未满足实时要求。** 本轮 latest / 8 测得解码约 746 FPS，低于 32×25 的 800 FPS 输入需求，19 路始终没有检测结果。抽帧发生在解码之后，不能消除这部分解码不足；不能把少量幸存结果的低年龄当作全部通道实时达标。完整配置、原始 run ID、短测限制和复现方法见[ADB 上板验证报告](realtime-board-validation.md)。

### 复现本组历史配置

以下使用本次已验证的 24 路 / 5 FPS 配置，需已准备 YOLOv8n、score gate 辅助模型和同名本地视频。未准备辅助模型时使用 `--score-gate off` 并重新测量，不能直接套用上表结果；YOLOv8n 可用 `bash scripts/prepare_yolov8n.sh` 准备。

```bash
bash demos/hdmi_wall/run.sh \
  --streams 24 --device 1 --model n --score-gate on --duration 60 \
  --input data/inputs/highway_1080p25_h264_8mbps_20min.mp4 \
  --policy latest --infer-fps 5 --max-frame-age-ms 250
```

需要每路约 8 FPS 时，先改为 `--streams 16 --infer-fps 8`。更换设备、素材、模型或输入规格后，查看逐路 `completed_fps`、`frame_age_ms`、`source_age_ms` 和丢帧统计再调整。解码后发布和取帧时都会检查年龄；检测完成时的年龄包含处理耗时，可以超过送检前的 250 ms 门槛。门槛过小或本地解码持续落后时，可能出现大量丢帧甚至没有检测结果。

停止当前运行后，用相同输入、模型、路数和时长执行逐帧对照：

```bash
bash demos/hdmi_wall/run.sh --stop
bash demos/hdmi_wall/run.sh \
  --streams 24 --device 1 --model n --score-gate on --duration 60 \
  --input data/inputs/highway_1080p25_h264_8mbps_20min.mp4 --policy all
```

抽帧发生在解码之后，不能减少源视频的解码负担。本地文件仍按源 FPS 读取；解码本身跟不上时，程序会丢弃超过年龄门槛的帧，关闭门槛后 `source_age_ms` 仍可能持续增长。RTSP 的相机、网络与解码器内部缓冲不在这个槽容量限制内，因此不能仅凭 `latest` 或帧年龄统计保证相机到 HDMI 的端到端延迟。

线程与设备帧所有权、独立调度测试及上板验收步骤见[实时调度实现与验证](realtime.md)。
