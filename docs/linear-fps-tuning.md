# 线性输出＋8个缓冲后的总推理FPS优化

当前使用入口：线性输出＋8 块额外缓冲已接入正式 showcase 默认配置，见[运行配置与验收](../demos/hdmi_wall/docs/linear-materials.md)。本文保留接入前的诊断方法与原始实验条件。

后续换用1080p25公路素材时，32路在PCIe2和PCIe3均出现大面积STALE，见[失败记录](linear-1080p25-stale.md)。下列结果限定本次1080p24素材，不能推广为通用稳定配置。

2026-09-28。本轮固定线性解码输出、8个额外缓冲、YOLOv8s INT8 batch1、32路1080p24长素材、每帧retrieve（不抽帧）、latest、250ms门槛、reuse、score gate与64KiB合并。检测结果和NMS始终保留。每档预热30秒、正式60秒，同一个独立实验二进制，ffplay统一60FPS参数。

## 实测结论

| 配置 | preview-fps / wall-fps | PCIe2 总FPS / TPU / 每路预览 | PCIe3 总FPS / TPU / 每路预览 |
|---|---|---|---|
| 基准 | 10 / 10 | 272.36 / 97.68% / 5.41 | 278.41 / 99.85% / 6.50 |
| 仅放开整墙 | 10 / 0 | 276.52 / 99.17% / 5.30 | 未测 |
| 推理优先 | 3 / 0 | 280.53 / 100% / 2.57 | 281.38 / 100% / 2.56 |
| 两种预览限频均取消 | -1 / 0 | 276.05 / 100% / 8.63 | 未测 |

基准、推理优先均测两次，其余单次。PCIe2顺序为基准→仅放开整墙→推理优先→双不限频→推理优先→基准；PCIe3顺序为基准→推理优先→推理优先→基准。没有同时跑两张卡。

PCIe2推理优先两次280.617/280.433 FPS，基准272.100/272.617 FPS，均值增幅3.00%。这以降低每路实际预览频率为代价，不是免费提速。若保持每路预览上限10FPS，仅放开整墙的首轮收益约1.62%，尚未重复。

PCIe3推理优先两次281.450/281.300 FPS，仅比PCIe2同配置均值高0.303%。使用device0（5.0GT/s×1）与device2（8.0GT/s×1）两张卡，不能将全部差异严格分配给链路速率。基准配置PCIe3吞吐比PCIe2高约2.22%，实际预览也更高。

这些结果说明当前完整工作流的可重复吞吐在280FPS附近；不能宣称280是芯片或YOLO模型的绝对上限。TPU采样包括辅助模型等工作。后续明显提速需要进一步隔离计算成本、优化辅助模型或模型执行方式，而非由本轮数据直接保证更高FPS。

## 连续性与限制

十档均正常完成、32路都有结果、逐路账目完整，每次完成推理都有结果回传，全运行过期丢帧为0，正式区间状态采样无STALE。推理优先两轮最差分析空窗PCIe2为0.173秒、PCIe3为0.165秒；最慢单路分别8.717/8.750 FPS。总FPS不是32×24全帧推理能力。

单路预览是实际生成/提交计数，不是显示器扫描输出；整墙重复刷新不能当作每路新图FPS。已有Rockchip GL加载提示仍保留于日志，不将正常退出写成物理HDMI验收。所有试验为短测，未覆盖RTSP、长稳、内存增长或不同场景。

仅更改运行参数，默认二进制、SDK和驱动均未替换。线性输出使用进程内诊断包装库，不作为正式解码参数接口的替代。

## 原始证据与复现

板端和本地 `data/results/` 下：

- `linear-fps-screen-20260928/`：PCIe2四档筛选。
- `linear-fps-confirm-20260928/`：PCIe2推理优先与基准复测。
- `linear-fps-pcie3-20260928/`：PCIe3四档ABBA。
- 首目录的 `combined-analysis.json` 汇总逐档与分组数据；每档保留command、config、summary、telemetry、status采样及stdout/stderr。

`tools/diagnostics/analyze_linear_fps_tuning.py`校验逐路计数、实际格式/缓冲日志及每帧结果回传后汇总，输出路径必须不存在。

本板单独运行推理优先方案的诊断入口如下，输出目录必须是新目录；同卡业务应先退出：

```bash
cd /userdata/1684X-EP-demo
sudo python3 tools/diagnostics/run_linear_fps_tuning.py \
  --device 0 --modes wall-only --duration 300 \
  --output /userdata/1684X-EP-demo/data/results/linear-fps-priority-new
```

其中`wall-only`表示单路预览上限3FPS、取消整墙主动限频。更高预览频率可选`unlimited`，但本轮仅单次验证。已准备的`run_linear_fps_ceiling.py`没有执行，不将其当作已测主模型上限证据。
