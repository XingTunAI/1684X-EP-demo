# 文档索引

[仓库首页](../README.md)

从运行指南找到可执行命令，从指标文档确认口径，再阅读对应测试报告。所有运行命令除另有标注外，均在板端仓库根目录 `/userdata/1684X-EP-demo` 执行。

## 准备与运行

| 内容 | 文档 |
|---|---|
| ADB / SSH、刷机后恢复、SDK、源码更新 | [环境准备](setup.md) |
| 模型、视频、长素材缓存、输出目录 | [数据与资源](../data/README.md) |
| HDMI 单卡、多卡与演示入口选择 | [HDMI README](../demos/hdmi_wall/README.md) |
| 双卡各 32 路低回传展示、R 键开关 | [回传开关与推荐命令](../demos/hdmi_wall/docs/readback-toggle.md) |
| 旧默认 showcase / stress、四小时设置 | [展示与压测](../demos/hdmi_wall/docs/showcase.md) |
| YOLOv8 / YOLO26 检测并保存结果 | [YOLOv8](../demos/yolov8/README.md) · [YOLO26](../demos/yolo26/README.md) |
| 纯解码、模型计算和传输隔离 | [硬件解码](../demos/decode/README.md) · [诊断工具](../tools/diagnostics/README.md) |

普通 YOLOv8 主入口固定要求 1080p25，本板官方默认素材为 1080p24；按其 README 显式准备兼容输入。HDMI 与 YOLO26 使用各自的输入契约，不要混用不同入口的参数。

## 参数、读数和计算

| 要解决的问题 | 文档 |
|---|---|
| 每个命令参数怎么用 | [HDMI 参数](../demos/hdmi_wall/docs/parameters.md)；其他入口见各自 README |
| 屏幕 DEC、INF、TPU、LAG、AGE、STALE 的含义 | [屏幕指标](../demos/hdmi_wall/docs/wall-indicators.md) |
| JSON / CSV 字段、帧计数、耗时和回传开关统计 | [指标定义](metrics.md) |
| TPU / VPU、理论算力与真实业务有什么区别 | [卡的能力说明](card-performance-explained.md) |
| PCIe 带宽、输出字节数、调用时间与 FPS 怎么计算 | [计算流程](performance-calculations.md) |

## 数据与证据

| 范围 | 文档 |
|---|---|
| 当前配置、旧默认、ON / OFF 与隔离测试放在一起比较 | **[当前数据总览](../demos/hdmi_wall/docs/current-data.md)** |
| 所有 HDMI 测试的日期、时长、run ID 与原报告 | [实测索引](../demos/hdmi_wall/docs/results.md) |
| 真实解码图像与推理结果的放大对照 | [解码观测](../demos/hdmi_wall/docs/decoder-observation.md) |
| 旧默认 20 / 32 路差异与调用耗时 | [PCIe 对比分析](../demos/hdmi_wall/docs/pcie-comparison.md) |
| 驻留模型、完整输出回读、TPU 满载时独立解码 | [容量与瓶颈实测](../demos/hdmi_wall/docs/capacity-validation.md) |
| 四小时未通过的已知证据 | [长跑故障记录](../demos/hdmi_wall/docs/incident-20260910.md) |

历史报告保留原始数字与命令，标题或开头注明测试阶段；不代表当前推荐。新报告需记录输入实际 FPS、模型、链路、单卡 / 双卡并发、所有关键配置、预热 / 正式时长、计数区间、TPU 样本和已知限制。跨版本比较说明同时变化的条件，不把配置允许值或短测峰值写成已验收能力。

## 开发与排错

| 内容 | 文档 |
|---|---|
| 通过 ADB / SSH 启动到 HDMI、显示权限 | [显示排错](../demos/hdmi_wall/docs/display.md) |
| 多卡切页、进程与停止 | [多设备运行](../demos/hdmi_wall/docs/multi-device.md) |
| latest 调度、帧所有权、计数与测试 | [实时调度实现](../demos/hdmi_wall/docs/realtime.md) |
| 检测器来源与输出缓冲适配 | [检测器代码说明](../demos/yolov8/detector/README.md) |

Git 保存代码、文档、截图及选定的汇总 JSON；完整日志、模型、视频、SDK 和故障原始归档保留在本地数据目录。文档中的本地证据路径是定位说明，不应做成 GitHub 上不存在的下载链接。
