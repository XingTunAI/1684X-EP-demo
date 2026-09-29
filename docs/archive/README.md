# 历史调查与验证记录

[当前文档导航](../README.md) · [当前视频墙结果](../../demos/hdmi_wall/docs/current-data.md) · [历史诊断脚本](../../tools/diagnostics/legacy/README.md)

归档报告保存各阶段的条件、失败与证据；其中“当前”“默认”“尚未定位”指报告当时状态。使用当前程序请回到运行入口。原始研究快照及其校验清单保持不变，目录调整不表示重新执行硬件测试。

## PCIe、VPP 与视频流程调查

| 记录 | 文档 |
|---|---|
| 同卡DMA上限与VPP/DMA混合负载（已完成） | [dma-vpp-link-20260924.md](dma-vpp-link-20260924.md) |
| 双卡推理与预览性能诊断（2026-09-28） | [dual-card-root-cause.md](dual-card-root-cause.md) |
| 抽帧后预览限频对照 | [grab-preview-comparison.md](grab-preview-comparison.md) |
| 预览与整墙限频拆分对照 | [grab-preview-factors.md](grab-preview-factors.md) |
| VPP 展开前选帧实验（2026-09-28） | [grab-selection.md](grab-selection.md) |
| 1080p25换素材失败记录：32路全部STALE | [linear-1080p25-stale.md](linear-1080p25-stale.md) |
| 线性输出＋8个缓冲后的总推理FPS优化 | [linear-fps-tuning.md](linear-fps-tuning.md) |
| 线性解码输出叠加每2帧取1帧：ABBA验证 | [linear-grab-comparison.md](linear-grab-comparison.md) |
| 本地视频墙落后追赶实验 | [local-catchup.md](local-catchup.md) |
| 两份1080p素材差异与STALE诊断 | [material-diagnosis.md](material-diagnosis.md) |
| 32路调用耗时、调用频率与待测指令开销 | [pcie-command-costs.md](pcie-command-costs.md) |
| 主控通过 PCIe 调用 VPU、VPP、TPU 的指令流程 | [pcie-command-flow.md](pcie-command-flow.md) |
| 视频墙数据流程：每帧大小与 PCIe 回传量 | [pcie-data-flow.md](pcie-data-flow.md) |
| PCIe 控制访问与驱动等待的进一步拆解 | [pcie-dispatch-wait.md](pcie-dispatch-wait.md) |
| PCIe2 / PCIe3 性能调查：已知结论与数据总览 | [pcie-findings-summary.md](pcie-findings-summary.md) |
| PCIe2 x1 与 PCIe3 x1 跨卡对照（已完成） | [pcie-link-card-comparison-20260923.md](pcie-link-card-comparison-20260923.md) |
| VPU / VPP / TPU：资源需求、占用与等待怎样计算 | [pcie-resource-accounting.md](pcie-resource-accounting.md) |
| PCIe2/3传输成本计算 | [pcie-transfer-cost-20260924.md](pcie-transfer-cost-20260924.md) |
| 无飞线PCIe2 x1带宽测试（2026-09-24） | [pcie2-direct-bandwidth-20260924.md](pcie2-direct-bandwidth-20260924.md) |
| 32 路并发与 PCIe 2.0 理论时间核验 | [pcie2-theory-and-concurrency.md](pcie2-theory-and-concurrency.md) |
| PCIe3 x1：24路不限频视频墙一小时结果 | [pcie3-wall24-1h-20260924.md](pcie3-wall24-1h-20260924.md) |
| PCIe2 32 路全流程耗时与开销 | [pipeline-timing-20260923.md](pipeline-timing-20260923.md) |
| PCIe 2.0 ×1：32 路分析、结果视频与实时预览对照 | [pipeline32-comparison.md](pipeline32-comparison.md) |
| PCIe 2.0 ×1：32 路分析、编码与视频墙实测 | [pipeline32-results.md](pipeline32-results.md) |
| 预处理为何受 PCIe 影响：调用分账与证据边界（2026-09-28） | [preprocess-wait-verification.md](preprocess-wait-verification.md) |
| 32 路预览开销归因（两轮已完成） | [preview-root-cause-20260923.md](preview-root-cause-20260923.md) |
| PCIe 2.0 ×1 回传与 TPU 对照（2026-09-22） | [readback-performance.md](readback-performance.md) |
| PCIe2 32 路：恢复历史素材与预览对照（2026-09-23） | [restore24-preview-results.md](restore24-preview-results.md) |
| 同卡同插槽PCIe速率对照（已完成，链路已恢复） | [same-card-link-20260923.md](same-card-link-20260923.md) |
| VPP 驱动归因与解码格式对照（2026-09-28） | [vpp-attribution-20260928.md](vpp-attribution-20260928.md) |

## 早期 HDMI 与容量记录

| 记录 | 文档 |
|---|---|
| 为什么部分 32 路配置抽帧后没有画面 | [32-channel-realtime.md](../../demos/hdmi_wall/docs/archive/32-channel-realtime.md) |
| PCIe 回传与 TPU 满载解码测试 | [capacity-validation.md](../../demos/hdmi_wall/docs/archive/capacity-validation.md) |
| 2026-09-10 数据归档 | [data-20260910.md](../../demos/hdmi_wall/docs/archive/data-20260910.md) |
| 设备 0 的路数、启动与持续输出测试 | [device0-capacity.md](../../demos/hdmi_wall/docs/archive/device0-capacity.md) |
| 2026-09-08 单卡显示记录 | [display-20260908.md](../../demos/hdmi_wall/docs/archive/display-20260908.md) |
| 双卡长跑故障记录：2026-09-10 取证 | [incident-20260910.md](../../demos/hdmi_wall/docs/archive/incident-20260910.md) |
| PCIe 2.0 ×1 与 PCIe 3.0 ×2：带宽、路数及 TPU 利用率 | [pcie-comparison.md](../../demos/hdmi_wall/docs/archive/pcie-comparison.md) |
| 单卡 30 路优化实测与连续输出限制 | [performance-30.md](../../demos/hdmi_wall/docs/archive/performance-30.md) |
| 2026-09-09 ADB 上板验证 | [realtime-board-validation.md](../../demos/hdmi_wall/docs/archive/realtime-board-validation.md) |
| 抽帧设置与历史画面对照 | [realtime-usage.md](../../demos/hdmi_wall/docs/archive/realtime-usage.md) |
