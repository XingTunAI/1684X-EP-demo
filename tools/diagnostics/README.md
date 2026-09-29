# 诊断工具

[仓库首页](../../README.md) · [性能说明](../../docs/performance-overview.md) · [历史实验与复算](legacy/README.md)

只需记住一个入口：`python3 tools/diagnostics/diagnose.py`。按问题选择子命令，参数帮助用 `子命令 --help`。硬件诊断在已准备 SDK 和模型的 Linux 板端运行，先确认目标卡空闲；`report` 只读取已有数据，也可在电脑执行。

| 问题 | 子命令 | 结果含义 |
|---|---|---|
| 模型计算还是完整输出读取慢 | `compute` | 驻留张量的 compute / copy / compute-copy 对照，不是视频 FPS |
| 主机与卡之间传输多快 | `bandwidth` | SDK 上传 / 下载有效吞吐，分块大小与计时范围；不修改 PCIe 速率 |
| batch 1 / 4 模型计算速度 | `model` | bmrt_test 计算计时，不含完整视频流程 |
| TPU 负载是否影响解码 | `decode` | 纯解码与独立模型负载对照；模型不消费这些解码帧 |
| 视频墙测试结束后怎么看结果 | `report` | 核对每卡正式计数、逐路连续性和正式窗口 TPU |

想运行视频墙压测，直接用 [showcase](../../demos/hdmi_wall/docs/layouts.md)，不需要依次运行所有诊断。

## 准备

按[环境说明](../../docs/setup.md)安装 SDK，按[资源说明](../../data/README.md)准备模型与素材，再构建原生探针：

```bash
bash tools/diagnostics/build.sh
```

`compute` 使用生成的 `build/inference_probe.pcie`，要求静态单 stage、batch 1、单个 FP32 输入和输出。`bandwidth` 使用 SDK 的 `test_cdma_perf`，`model` 使用 PATH 中的 `bmrt_test`；路径不同可按各子命令帮助指定。

## 常用命令

从仓库根目录执行，一次只运行所需的一项，设备编号以现场为准：

```bash
python3 tools/diagnostics/diagnose.py compute --device 0 \
  --steps 1 --modes compute copy compute-copy --warmup 3 --duration 10

python3 tools/diagnostics/diagnose.py bandwidth --device 0

python3 tools/diagnostics/diagnose.py model --device 0 --calculate-times 5000

python3 tools/diagnostics/diagnose.py decode --device 0 \
  --input data/inputs/hdmi_wall_demo_loop_600s.mp4 \
  --steps 8,16,32 --warmup 10 --duration 30 --target-fps 24 --throughput
```

以上四项都支持 `--output <结果父目录>`，每次创建新的运行子目录，终端打印实际路径。原来的四个 `run_*.py` 入口继续可用。模型路径可用 `compute/decode --bmodel` 指定；`model` 比较官方 YOLOv8s batch 1/4 模型，固定读取资源目录内的文件。

- `compute`：先读 `run_state.json` 和 `report.md`，再查看 `stages.csv`、各进程日志和输出一致性。
- `bandwidth`：先确认状态与数据校验成功，再读 `bandwidth.csv`；区分 sys / real 计时和十进制 MB/s。
- `model`：先看 `valid_compute_measurement`，TPU 采样含加载和收尾，不能称作正式窗口均值。
- `decode`：比较同路数的 baseline / loaded；只有满足采样条件才可引用 `full_load_observed`，扫描峰值不是摄像头容量验收。

## 汇总已有视频墙运行

将下面占位路径换成实际运行目录（包含 `run.json` 和 `device_N/`）：

```bash
python3 tools/diagnostics/diagnose.py report data/results/hdmi-wall-multi/RUN_ID

# 可选：写入一个尚不存在的报告目录。
python3 tools/diagnostics/diagnose.py report data/results/hdmi-wall-multi/RUN_ID \
  --output data/results/review-RUN_ID
```

工具不启动硬件任务，也不改写输入。缺卡、未完成、计数不符或通道无结果时返回非零状态，不给出合计吞吐。TPU 缺失显示为缺失；只采用与工作进程正式窗口匹配的样本。完整测量不自动等于业务、流畅度或长稳验收通过；年龄淘汰与覆盖等待帧按全运行计数单列。

## 检查单张图的检测结果

原生 `build/detector_check.pcie` 的参数依次为：设备编号、模型、类别文件、新输出 JSON、一个或多个图像路径。需要模型匹配的输入资源；输出框的类别 ID 是模型索引。它用于人工或程序对照，不自动计算准确率。示例中路径需换成已准备的资源：

```bash
tools/diagnostics/build/detector_check.pcie 0 \
  third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_fp32_1b.bmodel \
  third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/coco.names \
  data/results/detector-check.json data/inputs/test.jpg
```

## 开发与历史复算

`tests/` 是开发回归测试；`legacy/` 是带原实验条件的脚本，`studies/` 是带校验值的研究快照。实验脚本不是当前运行推荐。历史数字与复现条件见[归档索引](../../docs/archive/README.md)。
