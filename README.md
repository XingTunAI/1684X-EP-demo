# RK3588 + BM1684X EP Demo

用于 RK3588 通过 PCIe 调用 BM1684X 的开发与功能验证。YOLOv8 和 YOLO26 提供独立入口，支持单卡、多卡及运行时长设置；另提供 HDMI 视频墙、硬件解码和诊断工具。

首次使用先完成[环境与依赖准备](docs/setup.md)，再按[资源说明](data/README.md)获取视频、图片和模型。每个 Demo 的文档包含运行指令、输出文件说明和实际运行数据入口。

## Demo 列表

| Demo | 用途 | 运行指令 | 实际运行数据 |
|---|---|---|---|
| YOLOv8 | 单卡或多卡视频检测，保存逐帧检测结果 | [操作文档](demos/yolov8/README.md) | [查看数据](demos/yolov8/README.md#实际运行数据) |
| YOLO26 | 使用 YOLO26 进行单卡或多卡视频检测 | [操作文档](demos/yolo26/README.md) | [查看数据](demos/yolo26/README.md#实际运行数据) |
| **HDMI 视频墙** | **多路视频拼屏，显示检测框和各路状态** | **[操作文档](demos/hdmi_wall/README.md)** | [查看数据](demos/hdmi_wall/README.md#实际运行数据) |
| 硬件解码 | 单独检查逐路解码进度与速率 | [操作文档](demos/decode/README.md) | [查看数据](demos/decode/README.md#实际运行数据) |
| 模型与传输诊断 | 模型执行、结果回传、PCIe 传输和图像检测检查 | [操作文档](tools/diagnostics/README.md) | [查看数据](tools/diagnostics/README.md#实际运行数据) |

两个 YOLO 入口用 `--devices 0` 或 `--devices 0,1` 选择设备，`--streams` 指定每卡路数，`--duration` 指定后端测量窗口。HDMI 和解码入口使用单数 `--device` 选择一个设备，其他参数以各自文档为准。测量窗口与启动器总耗时不同，预热及计时差异见对应说明。

不同 FPS 和状态的含义见[指标说明](docs/metrics.md)，本地输出位置见[数据目录](data/README.md#results)。

## HDMI 效果

![HDMI 视频墙实际运行截图](demos/hdmi_wall/images/hdmi-wall.png)

HDMI 历史实际运行截图：YOLOv8n INT8 batch 1，同一份本地视频由 32 个通道独立处理。格内 infer FPS 是检测速率，顶部 preview 10 FPS 是显示更新上限；本图用于说明布局和显示功能，不作为检测精度或并发达标依据。图中的 Device 1 仅为该次运行的逻辑编号。

截图素材来自 Freestocks 的 [Cars On Highway](https://www.youtube.com/watch?v=-vLTFQv2_Vo)，经过重编码、循环延长及检测框叠加。素材出处与获取方式见 [HDMI 截图素材说明](demos/hdmi_wall/README.md#截图素材)。

<details>
<summary>仓库结构</summary>

```text
demos/
  yolov8/          YOLOv8 源码、构建、运行和操作文档
  yolo26/          YOLO26 源码、构建、运行和操作文档
  hdmi_wall/       HDMI 视频墙
  decode/          视频硬件解码
  common/          两个 YOLO 入口共用的进程管理
scripts/           SDK 安装与官方资源准备
tools/diagnostics/ 模型与传输诊断
docs/              环境、指标等共享说明
data/              自有输入、模型、结果及 SDK 包
third_party/       官方依赖源码和资源
```

官方资源保持下载目录，自有素材使用 `data/inputs/`，自有模型使用 `data/models/`，运行结果使用 `data/results/`；这些本地资源不随源码发布。

</details>

官方参考：[SOPHON 示例](https://github.com/sophgo/sophon-demo)、[开发资料](https://developer.sophgo.com/site/index/material/all/all.html)。
