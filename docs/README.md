# 文档导航

[仓库首页](../README.md) · [全部文档目录](catalog.md)

## 准备与运行

| 目的 | 文档 |
|---|---|
| 连接设备、准备 SDK 与构建环境 | [环境准备](setup.md) |
| 下载模型与素材、查找输出位置 | [数据与资源](../data/README.md) |
| 选择路数、看实际画面与流畅度 | [4 / 8 / 16 / 24 / 32 路效果](../demos/hdmi_wall/docs/layouts.md) |
| 运行多卡视频墙与当前优化配置 | [HDMI 入口](../demos/hdmi_wall/README.md)、[线性解码与素材](../demos/hdmi_wall/docs/linear-materials.md) |
| 运行其他 Demo | [YOLOv8](../demos/yolov8/README.md)、[YOLO26](../demos/yolo26/README.md)、[硬件解码](../demos/decode/README.md) |
| 不出画面、设备或进程异常 | [显示排错](../demos/hdmi_wall/docs/display.md)、[多设备运行](../demos/hdmi_wall/docs/multi-device.md) |

## 理解结果与排查性能

| 目的 | 文档 |
|---|---|
| 理解路数、FPS、TPU 与测试范围 | [性能说明](performance-overview.md) |
| 看最新实测与历史差异 | [三卡五分钟测试](../demos/hdmi_wall/docs/three-card.md)、[数据总览](../demos/hdmi_wall/docs/current-data.md) |
| 选择诊断工具 | [常用诊断入口](../tools/diagnostics/README.md)、[完整脚本清单](../tools/diagnostics/SCRIPT_GUIDE.md) |
| 理解线性输出＋额外缓冲解决的问题 | [VPP 根因报告](vpp-root-cause.md) |
| 追溯 PCIe 调查过程 | [调查阅读顺序](pcie-reading-guide.md)、[证据索引](vpp-evidence-index.md) |
| 查看参数和指标定义 | [命令参数](../demos/hdmi_wall/docs/parameters.md)、[屏幕指标](../demos/hdmi_wall/docs/wall-indicators.md)、[输出字段](metrics.md) |
| 核对公式与硬件规格 | [计算流程](performance-calculations.md)、[卡的参数说明](card-performance-explained.md) |

## 维护约定

运行入口与模块测试放在对应 Demo；共享定义和跨模块调查放在 `docs/`。常用诊断入口与历史实验的用途分别在诊断 README 和脚本清单说明。

新增报告记录输入、模型、设备链路、关键配置、正式时长和逐路连续性，注明适用范围。历史结果保留原条件；当前推荐只在运行说明维护，其他页面用链接引用。修改后检查受影响测试、文档链接及 `git diff --check`。

模型、视频、SDK、完整日志与本地工作资料按 `.gitignore` 保存，忽略不代表可以删除。日期报告的忽略例外需逐项添加。历史脚本和证据清单保留路径，移动前检查导入、命令引用和校验清单；本地证据路径用代码文字表示，避免形成无法在线访问的下载链接。
