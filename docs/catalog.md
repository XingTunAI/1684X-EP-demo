# 全部文档目录

[文档导航](README.md) · [仓库首页](../README.md)

按模块列出文档。首次使用先看文档导航；历史报告保留其日期、配置和验证范围。

## 共享说明与性能调查

| 文档 | 路径 |
|---|---|
| [文档导航](README.md) | `docs/README.md` |
| [BM1684X-EP：理论计算、实际表现与参数说明](card-performance-explained.md) | `docs/card-performance-explained.md` |
| [同卡DMA上限与VPP/DMA混合负载（已完成）](dma-vpp-link-20260924.md) | `docs/dma-vpp-link-20260924.md` |
| [双卡推理与预览性能诊断（2026-09-28）](dual-card-root-cause.md) | `docs/dual-card-root-cause.md` |
| [抽帧后预览限频对照](grab-preview-comparison.md) | `docs/grab-preview-comparison.md` |
| [预览与整墙限频拆分对照](grab-preview-factors.md) | `docs/grab-preview-factors.md` |
| [VPP 展开前选帧实验（2026-09-28）](grab-selection.md) | `docs/grab-selection.md` |
| [1080p25换素材失败记录：32路全部STALE](linear-1080p25-stale.md) | `docs/linear-1080p25-stale.md` |
| [线性输出＋8个缓冲后的总推理FPS优化](linear-fps-tuning.md) | `docs/linear-fps-tuning.md` |
| [线性解码输出叠加每2帧取1帧：ABBA验证](linear-grab-comparison.md) | `docs/linear-grab-comparison.md` |
| [本地视频墙落后追赶实验](local-catchup.md) | `docs/local-catchup.md` |
| [两份1080p素材差异与STALE诊断](material-diagnosis.md) | `docs/material-diagnosis.md` |
| [输出指标与状态](metrics.md) | `docs/metrics.md` |
| [32路调用耗时、调用频率与待测指令开销](pcie-command-costs.md) | `docs/pcie-command-costs.md` |
| [主控通过 PCIe 调用 VPU、VPP、TPU 的指令流程](pcie-command-flow.md) | `docs/pcie-command-flow.md` |
| [视频墙数据流程：每帧大小与 PCIe 回传量](pcie-data-flow.md) | `docs/pcie-data-flow.md` |
| [PCIe 控制访问与驱动等待的进一步拆解](pcie-dispatch-wait.md) | `docs/pcie-dispatch-wait.md` |
| [PCIe2 / PCIe3 性能调查：已知结论与数据总览](pcie-findings-summary.md) | `docs/pcie-findings-summary.md` |
| [PCIe2 x1 与 PCIe3 x1 跨卡对照（已完成）](pcie-link-card-comparison-20260923.md) | `docs/pcie-link-card-comparison-20260923.md` |
| [PCIe性能问题：阅读顺序与证据导航](pcie-reading-guide.md) | `docs/pcie-reading-guide.md` |
| [VPU / VPP / TPU：资源需求、占用与等待怎样计算](pcie-resource-accounting.md) | `docs/pcie-resource-accounting.md` |
| [PCIe2/3传输成本计算](pcie-transfer-cost-20260924.md) | `docs/pcie-transfer-cost-20260924.md` |
| [无飞线PCIe2 x1带宽测试（2026-09-24）](pcie2-direct-bandwidth-20260924.md) | `docs/pcie2-direct-bandwidth-20260924.md` |
| [32 路并发与 PCIe 2.0 理论时间核验](pcie2-theory-and-concurrency.md) | `docs/pcie2-theory-and-concurrency.md` |
| [PCIe3 x1：24路不限频视频墙一小时结果](pcie3-wall24-1h-20260924.md) | `docs/pcie3-wall24-1h-20260924.md` |
| [BM1684X：PCIe、推理、解码和抽帧指标的计算流程](performance-calculations.md) | `docs/performance-calculations.md` |
| [如何理解 Demo 的性能数据](performance-overview.md) | `docs/performance-overview.md` |
| [PCIe2 32 路全流程耗时与开销](pipeline-timing-20260923.md) | `docs/pipeline-timing-20260923.md` |
| [PCIe 2.0 ×1：32 路分析、结果视频与实时预览对照](pipeline32-comparison.md) | `docs/pipeline32-comparison.md` |
| [PCIe 2.0 ×1：32 路分析、编码与视频墙实测](pipeline32-results.md) | `docs/pipeline32-results.md` |
| [预处理为何受 PCIe 影响：调用分账与证据边界（2026-09-28）](preprocess-wait-verification.md) | `docs/preprocess-wait-verification.md` |
| [32 路预览开销归因（两轮已完成）](preview-root-cause-20260923.md) | `docs/preview-root-cause-20260923.md` |
| [PCIe 2.0 ×1 回传与 TPU 对照（2026-09-22）](readback-performance.md) | `docs/readback-performance.md` |
| [PCIe2 32 路：恢复历史素材与预览对照（2026-09-23）](restore24-preview-results.md) | `docs/restore24-preview-results.md` |
| [同卡同插槽PCIe速率对照（已完成，链路已恢复）](same-card-link-20260923.md) | `docs/same-card-link-20260923.md` |
| [环境与依赖准备](setup.md) | `docs/setup.md` |
| [VPP 驱动归因与解码格式对照（2026-09-28）](vpp-attribution-20260928.md) | `docs/vpp-attribution-20260928.md` |
| [1684X 根因诊断证据索引（2026-09-28）](vpp-evidence-index.md) | `docs/vpp-evidence-index.md` |
| [1684X 预处理耗时差异：根因、计算与验证](vpp-root-cause.md) | `docs/vpp-root-cause.md` |

## HDMI 视频墙

[视频墙路数、布局与流畅度](../demos/hdmi_wall/docs/layouts.md)（`demos/hdmi_wall/docs/layouts.md`）

| 文档 | 路径 |
|---|---|
| [HDMI 视频墙](../demos/hdmi_wall/README.md) | `demos/hdmi_wall/README.md` |
| [为什么部分 32 路配置抽帧后没有画面](../demos/hdmi_wall/docs/32-channel-realtime.md) | `demos/hdmi_wall/docs/32-channel-realtime.md` |
| [PCIe 回传与 TPU 满载解码测试](../demos/hdmi_wall/docs/capacity-validation.md) | `demos/hdmi_wall/docs/capacity-validation.md` |
| [HDMI 与 PCIe 数据总览](../demos/hdmi_wall/docs/current-data.md) | `demos/hdmi_wall/docs/current-data.md` |
| [对比解码输出与推理结果](../demos/hdmi_wall/docs/decoder-observation.md) | `demos/hdmi_wall/docs/decoder-observation.md` |
| [设备 0 的路数、启动与持续输出测试](../demos/hdmi_wall/docs/device0-capacity.md) | `demos/hdmi_wall/docs/device0-capacity.md` |
| [ADB / SSH 连接与 HDMI 显示](../demos/hdmi_wall/docs/display.md) | `demos/hdmi_wall/docs/display.md` |
| [双卡长跑故障记录：2026-09-10 取证](../demos/hdmi_wall/docs/incident-20260910.md) | `demos/hdmi_wall/docs/incident-20260910.md` |
| [线性解码接入与指定素材验收（2026-09-28）](../demos/hdmi_wall/docs/linear-materials.md) | `demos/hdmi_wall/docs/linear-materials.md` |
| [多设备分页视频墙](../demos/hdmi_wall/docs/multi-device.md) | `demos/hdmi_wall/docs/multi-device.md` |
| [HDMI 命令参数](../demos/hdmi_wall/docs/parameters.md) | `demos/hdmi_wall/docs/parameters.md` |
| [PCIe 2.0 ×1 与 PCIe 3.0 ×2：带宽、路数及 TPU 利用率](../demos/hdmi_wall/docs/pcie-comparison.md) | `demos/hdmi_wall/docs/pcie-comparison.md` |
| [单卡 30 路优化实测与连续输出限制](../demos/hdmi_wall/docs/performance-30.md) | `demos/hdmi_wall/docs/performance-30.md` |
| [回传开关与双卡 32 路对照](../demos/hdmi_wall/docs/readback-toggle.md) | `demos/hdmi_wall/docs/readback-toggle.md` |
| [2026-09-09 ADB 上板验证](../demos/hdmi_wall/docs/realtime-board-validation.md) | `demos/hdmi_wall/docs/realtime-board-validation.md` |
| [抽帧设置与历史画面对照](../demos/hdmi_wall/docs/realtime-usage.md) | `demos/hdmi_wall/docs/realtime-usage.md` |
| [实时调度实现与验证](../demos/hdmi_wall/docs/realtime.md) | `demos/hdmi_wall/docs/realtime.md` |
| [HDMI 实测索引与历史资料](../demos/hdmi_wall/docs/results.md) | `demos/hdmi_wall/docs/results.md` |
| [四小时多设备展示](../demos/hdmi_wall/docs/showcase.md) | `demos/hdmi_wall/docs/showcase.md` |
| [单设备运行与输出](../demos/hdmi_wall/docs/single-device.md) | `demos/hdmi_wall/docs/single-device.md` |
| [三卡并发 96 路测试（2026-09-29）](../demos/hdmi_wall/docs/three-card.md) | `demos/hdmi_wall/docs/three-card.md` |
| [32 路视频墙指标说明](../demos/hdmi_wall/docs/wall-indicators.md) | `demos/hdmi_wall/docs/wall-indicators.md` |

## 其他 Demo

| 文档 | 路径 |
|---|---|
| [视频硬件解码](../demos/decode/README.md) | `demos/decode/README.md` |
| [YOLO26 单卡与多卡检测](../demos/yolo26/README.md) | `demos/yolo26/README.md` |
| [YOLOv8 单卡与多卡检测](../demos/yolov8/README.md) | `demos/yolov8/README.md` |
| [检测器适配代码](../demos/yolov8/detector/README.md) | `demos/yolov8/detector/README.md` |

## 资源与诊断

| 文档 | 路径 |
|---|---|
| [本地数据目录](../data/README.md) | `data/README.md` |
| [模型与传输诊断](../tools/diagnostics/README.md) | `tools/diagnostics/README.md` |
| [诊断脚本清单](../tools/diagnostics/SCRIPT_GUIDE.md) | `tools/diagnostics/SCRIPT_GUIDE.md` |
| [2026-09-28 VPP 根因调查快照](../tools/diagnostics/studies/rootcause-20260928/README.md) | `tools/diagnostics/studies/rootcause-20260928/README.md` |
| [Decoder-format ablation — plan before execution](../tools/diagnostics/studies/rootcause-20260928/decoder_format_plan.md) | `tools/diagnostics/studies/rootcause-20260928/decoder_format_plan.md` |
| [VPP attribution v2 — plan before measurement](../tools/diagnostics/studies/rootcause-20260928/vpp_attribution_plan.md) | `tools/diagnostics/studies/rootcause-20260928/vpp_attribution_plan.md` |
