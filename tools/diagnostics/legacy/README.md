# 历史诊断与复算

[常用诊断入口](../README.md) · [文档导航](../../../docs/README.md)

本目录集中保留 41 个阶段实验与分析脚本，供复算历史报告。目录迁移已调整仓库根目录计算和相互调用；实验条件未升级为当前业务配置。历史实验并不等于可直接运行的通用工具；其默认设备、模型、输入、构建目录和显示环境需按原报告核对。

## 按问题找原报告

| 调查主题 | 使用说明与实验条件 |
|---|---|
| 回传成本与业务对照 | [回传调查](../../../docs/archive/readback-performance.md) |
| 逐帧分析、编码和视频墙 | [流程对照](../../../docs/archive/pipeline32-comparison.md) |
| 本地视频落后与追赶 | [追赶实验](../../../docs/archive/local-catchup.md) |
| 预览、选帧与线性路径 | [选帧](../../../docs/archive/grab-selection.md)、[预览因素](../../../docs/archive/grab-preview-factors.md)、[线性调优](../../../docs/archive/linear-fps-tuning.md) |
| 更换素材 | [素材诊断](../../../docs/archive/material-diagnosis.md)、[正式入口验证](../../../demos/hdmi_wall/docs/linear-materials.md) |
| PCIe 链路与驱动 | [调查导航](../../../docs/pcie-reading-guide.md)、[根因报告](../../../docs/vpp-root-cause.md) |
| 根因调查快照 | [恢复路径与复算方式](../studies/rootcause-20260928/README.md) |

## 当前常用工具（位于上级目录）

| 脚本 | 用途 |
|---|---|
| [run_inference_diagnostics.py](../run_inference_diagnostics.py) | 分离驻留模型计算、完整输出读取和组合耗时；详见 README。 |
| [run_pcie_bandwidth.py](../run_pcie_bandwidth.py) | 调用已安装 SDK 的 CDMA 传输测试，按设备、大小与方向记录；详见 README。 |
| [run_single_card_tpu_bench.py](../run_single_card_tpu_bench.py) | 调用 bmrt_test 比较官方 batch 1 / 4 模型；不读取视频。 |
| [run_decode_capacity.py](../run_decode_capacity.py) | 比较纯解码与独立 TPU 负载下的解码吞吐；不是完整检测流水线。 |

## 历史回传与视频流程实验

| 脚本 | 用途 |
|---|---|
| [run_readback_study.py](run_readback_study.py) | 采集单卡分阶段回传实验，是多个历史脚本共用的运行组件。 |
| [run_wall32_study.py](run_wall32_study.py) | 32 路视频墙回传专项入口。 |
| [run_readback32_ab.py](run_readback32_ab.py) | 官方 24p 素材 32 路回传配置正反顺序对照。 |
| [run_wall_readback_comparison.py](run_wall_readback_comparison.py) | 视频墙回传方式对照。 |
| [run_yolov8_readback_comparison.py](run_yolov8_readback_comparison.py) | 普通 YOLOv8 旧版与共享检测器的同源帧输出对照。 |
| [finish_readback_study.py](finish_readback_study.py) | 继续指定历史回传调查，包含后续业务检查；不是通用断点续跑。 |
| [run_pipeline32_comparison.py](run_pipeline32_comparison.py) | 单卡逐帧分析、编码文件与 latest 视频墙对照。 |
| [run_dual_pipeline32.py](run_dual_pipeline32.py) | 两张 Gen2 ×1 卡并发并交换实验角色。 |
| [finalize_dual_pipeline32.py](finalize_dual_pipeline32.py) | 整理已完成的双卡实验、视频索引、代表帧和文件校验。 |
| [prepare_catchup_segments.py](prepare_catchup_segments.py) | 准备并逐帧核验用于追赶实验的分段视频。 |
| [run_catchup_comparison.py](run_catchup_comparison.py) | 对比原流程与本地关键帧追赶实验。 |

## 已合并的重复实现

`run_linear_source_comparison.py`、`run_linear_source_guarded.py`、`run_linear_source_stride.py` 共用 `linear_source.py`。三个旧入口保留，参数和默认实验条件不变；统计窗口取测试时长与 10 秒的较小值，修复历史脚本在不足 10 秒时被原生程序拒绝的问题：comparison 不自动按 STALE 停止，guarded 固定逐帧取图并启用停止条件，stride 在同样的停止条件下允许 `--retrieve-every 1/2`。停止条件仍为至少 `max(1, 路数整除 2)` 路连续五次采样显示 STALE；它不是流畅度验收标准。

另外六个入口共用 `preview_experiments.py`：`run_preview_budget_ab.py`、`run_async_preview_ab.py`、`run_vpp_limit_ab.py`、`run_preview_stages.py`、`run_link_card_ab.py`、`run_same_card_ab.py`。每个入口保留原有实验组合、构建路径、链路校验参数和正反顺序。当前共保留 41 个历史脚本入口，新增的两个共享模块不是额外运行入口。

上限探索中的关闭结果回传实验、固定素材的预览实验和不同统计口径的分析器继续独立，不能仅因为代码相似就互换。共享实现仍依赖历史构建与注入库，不是当前推荐的视频墙运行方式。研究快照不随这次重构改写。

## 历史预览、线性输出与素材实验

| 脚本 | 用途 |
|---|---|
| [run_preview_ab.py](run_preview_ab.py) | 复现历史 32 路配置后改变预览频率。 |
| [run_preview_stages.py](run_preview_stages.py) | 隔离预览处理、回传与显示阶段。 |
| [run_preview_budget_ab.py](run_preview_budget_ab.py) | 预览预算参数对照。 |
| [run_async_preview_ab.py](run_async_preview_ab.py) | 异步预览对照；不代表当前默认推荐。 |
| [run_vpp_limit_ab.py](run_vpp_limit_ab.py) | VPP 准入限流对照。 |
| [run_grab_selection.py](run_grab_selection.py) | 比较 retrieve 前按固定间隔选帧与原路径。 |
| [run_grab_preview_comparison.py](run_grab_preview_comparison.py) | 固定每两帧取一帧时比较预览限频。 |
| [run_linear_grab_comparison.py](run_linear_grab_comparison.py) | 线性解码条件下的固定间隔选帧对照。 |
| [run_linear_preview_comparison.py](run_linear_preview_comparison.py) | 线性路径的预览频率对照。 |
| [run_linear_fps_tuning.py](run_linear_fps_tuning.py) | 线性输出与额外缓冲固定后，比较预览及整墙限频。 |
| [run_linear_fps_ceiling.py](run_linear_fps_ceiling.py) | 准备的上限探索脚本；原报告明确未执行，不能引用为已测上限。 |
| [run_linear_buffer_diagnosis.py](run_linear_buffer_diagnosis.py) | 线性输出缓冲专项实验。 |
| [run_linear_source_comparison.py](run_linear_source_comparison.py) | 线性输出下不同素材的对照。 |
| [run_linear_source_guarded.py](run_linear_source_guarded.py) | 素材对照的受限运行变体，复现前核对代码中的阶段配置。 |
| [run_linear_source_stride.py](run_linear_source_stride.py) | 素材对照的取帧间隔变体。 |
| [run_material_business_diagnosis.py](run_material_business_diagnosis.py) | 指定素材业务连续性专项。 |

## 链路与驱动专项实验

| 脚本 | 用途 |
|---|---|
| [run_link_card_ab.py](run_link_card_ab.py) | 按指定卡、BDF 和预期速率采集对照；必须匹配实际设备。 |
| [run_same_card_ab.py](run_same_card_ab.py) | 同卡速率条件对照。 |
| [run_dma_vpp_matrix.py](run_dma_vpp_matrix.py) | 固定端口的同卡变速、DMA/VPP 矩阵；包含 setpci 写入及恢复逻辑。 |
| [run_pcie2_bandwidth.py](run_pcie2_bandwidth.py) | 特定平台的 PCIe2 带宽实验；先检查固定路径和设备。 |
| [run_dispatch_wait_ab.py](run_dispatch_wait_ab.py) | 有界驱动调用追踪；追踪会扰动性能，不是日常压测入口。 |
| [run_mmio_read_ab.py](run_mmio_read_ab.py) | 同卡寄存器读取 ioctl 计时及追踪。 |

## 已有数据的分析与报告

| 脚本 | 用途 |
|---|---|
| [analyze_readback_study.py](analyze_readback_study.py) | 从已完成回传阶段和正式窗口遥测生成汇总。 |
| [report_readback_study.py](report_readback_study.py) | 生成指定历史回传调查的简报。 |
| [analyze_dispatch_graph.py](analyze_dispatch_graph.py) | 解析函数图完整调用，避免嵌套耗时重复累加。 |
| [analyze_dispatch_wait.py](analyze_dispatch_wait.py) | 分析取回的驱动等待矩阵，保留各轮差异。 |
| [analyze_mmio_reads.py](analyze_mmio_reads.py) | 汇总寄存器读取与函数图追踪记录。 |
| [analyze_grab_preview_factors.py](analyze_grab_preview_factors.py) | 分析单路预览与整墙限频的双因素实验。 |
| [analyze_linear_fps_tuning.py](analyze_linear_fps_tuning.py) | 核验线性输出实验账目并汇总同等窗口。 |
| [summarize_catchup.py](summarize_catchup.py) | 汇总追赶实验的源帧年龄与最长无结果间隔。 |

## C++ 探针与研究快照

`inference_probe.cpp`、`detector_check.cpp` 是常用入口对应的原生程序。其余 `*_probe.cpp`、`output_read.hpp` 用于特定实验或共享实现，构建方式以 CMake 和对应报告为准，不按文件名猜命令。

`studies/rootcause-20260928/` 保存当时的代码与校验清单，详细用途见该目录 README。`tests/` 是开发测试，不是板端压力测试入口。

## 运行与复算前核对

1. 确定问题和对应报告，检查脚本的设备编号、BDF、输入、模型和二进制路径。
2. 采集脚本在板端执行并占用设备；分析脚本读取已有结果，但可能依赖 FFmpeg、固定目录或其他脚本，不保证仅凭单个 JSON 就能运行。
3. 使用新的输出目录，保留配置、命令、原始日志和失败阶段；不要覆盖历史结果。
4. 按报告检查状态、计数完整性、正式测量窗口及逐路异常，再解释数字。

## 原始快照的复算兼容

`../studies/rootcause-20260928/` 内的源码和校验清单保持原样。按该目录 README 恢复到工作副本后，在仓库根目录设置 `PYTHONPATH="$PWD/tools/diagnostics/legacy"` 再运行快照脚本，使其能找到迁移后的分析器。快照记录的旧路径是当时证据，不改写为新实验。

这些历史入口有固定 BDF、模型和原始数据目录；个别脚本在导入时即执行实验，不能批量执行它们的 `--help`。日常操作请使用上级 `diagnose.py`。

## 实际运行数据

最新逐卡传输、驻留模型和解码扫描见 [2026-09-10 容量报告](../../../demos/hdmi_wall/docs/archive/capacity-validation.md)，计算方法见 [公式与复算](../../../docs/performance-calculations.md)。下面保留 09-07 的单进程历史记录，不覆盖新数据；这些探针的次/秒不能作为 HDMI 检测 FPS。

以下为 2026-09-07 在 RK3588 + 单张 BM1684X 上完成的诊断记录。模型为官方 YOLOv8s INT8 batch 1，单进程，预热 3 秒。输入为常驻的全零张量，输入/输出边界为 FP32；不读取或解码视频。

| 模式 | 实际测量时长 | 迭代次数 | 结果 | 输出检查 |
|---|---:|---:|---|---|
| `compute` 模型提交与同步 | 10.0020 秒 | 3,356 | 335.53 次/秒 | 参考输出一致，进程正常退出 |
| `copy` 已生成结果回传 | 10.0039 秒 | 1,234 | 348.15 MB/s | 参考输出一致，进程正常退出 |

每次回传 2,822,400 字节，带宽按 `迭代次数 × 每次字节数 ÷ 实测秒数 ÷ 1,000,000` 计算。`compute` 的迭代速率不包含视频解码、预处理和逐帧结果回传，不能作为视频 FPS。

本次目录整理后未重新上板执行；这些是历史单进程记录，固定输入参考一致也不代表真实图像检测准确。首次运行后从终端打印的目录打开 `report.md`、`summary.json` 和 `run_state.json`，确认运行完成、各项输出核验通过，再解释计时结果。

## 指标边界

固定输入结果首末一致不代表真实视频检测准确。有效回传速度包含 SDK、内存和调用等待，不等于 PCIe 理论带宽。批量模型的图像数量与模型调用次数需分开计算；独立模型速度不能当作完整视频 FPS。共享定义见[指标说明](../../../docs/metrics.md)。

源码检查位于 `tests/`；所有运行日志、结果及图像留在本地 `data/results/`。
## 32 路分析、编码与预览对照

针对本地视频落后后推理断供的可选追赶方案，准备、双卡对照和验收边界见[本地追赶实验](../../../docs/archive/local-catchup.md)。入口为 `prepare_catchup_segments.py`、`run_catchup_comparison.py`、`summarize_catchup.py`。

两张 PCIe 2.0 ×1 卡同时运行并交换角色的实测，见 [结果与视频](../../../docs/archive/pipeline32-results.md) 和 [实验设计](../../../docs/archive/pipeline32-comparison.md)。入口为 `run_dual_pipeline32.py`；单卡入口为 `run_pipeline32_comparison.py`，均支持 `--dry-run`。实际编码使用 BM1684X VPU；不要把当前逐帧离线分析吞吐当成实时 25 FPS 验收。
