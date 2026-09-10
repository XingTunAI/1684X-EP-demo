# RK3588 + BM1684X EP Demo

在 RK3588 Linux ARM64 主机上通过 PCIe 使用 BM1684X，提供视频检测、HDMI 多路视频墙、硬件解码和模型 / 传输诊断。代码用于开发、演示和性能验证；每份实测报告单独注明输入、配置及验证范围。

## 从哪里开始

1. [进入设备并准备环境](docs/setup.md)：ADB / SSH、在 `/userdata` 取得仓库、SDK 和构建工具。
2. [准备模型与素材](data/README.md)：官方资源、自有输入、缺失文件恢复和输出位置。Git 不包含 SDK 包、模型、视频和运行日志。
3. 根据下面的用途选择 Demo，先完成短时运行，再进行多路或长时测试。

## Demo 列表

| 你要做什么 | 入口与说明 |
|---|---|
| 多路画面显示到 HDMI，多卡分页、展示和压测 | **[HDMI 视频墙](demos/hdmi_wall/README.md)** |
| 用 YOLOv8 做单卡 / 多卡视频检测，保存结果 | [YOLOv8](demos/yolov8/README.md) |
| 用 YOLO26 做单卡 / 多卡视频检测 | [YOLO26](demos/yolo26/README.md) |
| 单独测量硬件解码进度和速率 | [硬件解码](demos/decode/README.md) |
| 检查模型执行、结果回传和 PCIe 传输 | [模型与传输诊断](tools/diagnostics/README.md) |

各入口的设备、路数和时长参数以自身文档为准。例如 HDMI 单卡用 `--device`，多卡用 `--devices`；正式测量时长不包含初始化、预热与收尾。

## HDMI 效果

![双卡各 32 路 HDMI 视频墙与解码、检测、TPU 指标](demos/hdmi_wall/images/hdmi-wall-decode-metrics.png)

上图是当前板端实际截图：两张卡各运行 32 路，当前显示 device 0，device 1 在后台继续处理。顶部按钮可切页，每路显示解码 / 检测 FPS 和时间指标。素材为 SOPHON 官方 1080p24 车辆 / 行人视频，各通道独立读取同一文件；布局支持最多 4 张卡，实体硬件验证目前为 2 张卡。

| HDMI 使用场景 | 查看文档 |
|---|---|
| 第一次运行，或不知道选哪个入口 | [快速开始与入口选择](demos/hdmi_wall/README.md#选择运行入口) |
| 每卡 32 路展示；按 PCIe 链路选择压测档位 | [showcase / stress](demos/hdmi_wall/docs/showcase.md) |
| 比较 TPU 高负载前后解码速度、停顿与落后 | [解码观测](demos/hdmi_wall/docs/decoder-observation.md) |
| 理解 DEC、INF、LAG、AGE、STALE 等读数 | [屏幕指标](demos/hdmi_wall/docs/wall-indicators.md) |
| 理解命令中的配置参数 | [命令参数](demos/hdmi_wall/docs/parameters.md) |
| 查实测数据和 PCIe 对比原因 | [实测索引](demos/hdmi_wall/docs/results.md) · [PCIe 对比](demos/hdmi_wall/docs/pcie-comparison.md) |

输入帧率、解码帧率、检测帧率和拼屏刷新率是不同指标。当前官方素材的 32 路目标为 32×24＝768 解码 FPS；输出为一幅 1920×1080 视频墙，预览上限 10 FPS。四小时是长时入口的默认配置，尚未完成四小时稳定性验收。截图来源及历史公路素材署名见 [素材说明](demos/hdmi_wall/docs/results.md#截图素材)。

## 文档与结果

| 内容 | 文档 |
|---|---|
| 环境、连接、SDK | [环境准备](docs/setup.md) |
| 视频、模型、SDK 包和运行产物存放位置 | [数据目录](data/README.md) |
| FPS、阶段耗时、帧年龄、完整性与统计口径 | [输出指标](docs/metrics.md) |
| HDMI 当前验证与历史记录 | [HDMI 实测索引](demos/hdmi_wall/docs/results.md) |
| 其他 Demo 的实测 | [YOLOv8](demos/yolov8/README.md#实际运行数据) · [YOLO26](demos/yolo26/README.md#实际运行数据) · [解码](demos/decode/README.md#实际运行数据) · [诊断](tools/diagnostics/README.md#实际运行数据) |

## 仓库结构

```text
demos/
  hdmi_wall/       HDMI 视频墙、展示 / 压测、解码观测
  yolov8/          YOLOv8 源码、构建与运行
  yolo26/          YOLO26 源码、构建与运行
  decode/          视频硬件解码
  common/          两个 YOLO 入口共用的进程管理
docs/              环境、输出指标等共享说明
scripts/           SDK 安装与官方资源准备
tools/diagnostics/ 模型与传输诊断
data/              本地输入、模型、结果及 SDK 包
third_party/       下载到本地的官方依赖源码和资源
```

官方参考：[SOPHON 示例](https://github.com/sophgo/sophon-demo)、[开发资料](https://developer.sophgo.com/site/index/material/all/all.html)。
