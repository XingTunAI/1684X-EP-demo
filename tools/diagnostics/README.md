# 模型与传输诊断

[仓库首页](../../README.md) / [文档索引](../../docs/README.md)

本目录包含独立模型执行、输出回传、PCIe 传输和 YOLOv8 图像结果检查。所有命令从仓库根目录执行，运行依赖对应 SOPHON SDK；这些工具不启动视频演示。

## 构建与入口

先完成[环境准备](../../docs/setup.md)。首次使用以下默认模型时，从仓库根目录准备官方 YOLOv8 模型及图像资源，然后构建：

```bash
bash scripts/prepare.sh
bash tools/diagnostics/build.sh
```

已经准备过官方资源时跳过 `prepare.sh`。模型和图片的具体路径、下载渠道及缺失文件恢复见[资源说明](../../data/README.md#官方资源)。

生成 `tools/diagnostics/build/inference_probe.pcie` 和 `detector_check.pcie`。`BUILD_DIR`、`BUILD_JOBS` 可覆盖默认构建目录和并行度；额外参数传给 CMake。

`detector_check` 复用 `demos/yolov8/detector/`，其输出读取辅助头文件也由本目录维护。依赖准备见[环境说明](../../docs/setup.md)。

| 入口 | 输入与用途 | 输出 |
|---|---|---|
| `run_inference_diagnostics.py` | 驻留张量上的模型执行、输出读取及组合 | `data/results/inference-diagnostics/<run-id>/` |
| `run_pcie_bandwidth.py` | SDK 的传输诊断程序、设备号和传输大小 | `data/results/bandwidth/<run-id>/` |
| `run_single_card_tpu_bench.py` | 官方 YOLOv8s INT8 batch 1/4 模型及 bmrt_test | `data/results/tpu/<run-id>/` |
| `run_decode_capacity.py` | 独立解码与驻留模型计算同时运行，比较纯解码 / TPU 高负载下的吞吐 | `data/results/decode-capacity/<run-id>/` |
| `build/detector_check.pcie` | 设备、YOLOv8模型、类别文件、输出路径、图像列表 | 用户指定的新 JSON 文件 |

运行前确认所选设备没有其他工作负载。使用 `--help` 查看 Python 入口参数；工具不会替使用者停止已有程序。

## 模型执行与输出读取

```bash
python3 tools/diagnostics/run_inference_diagnostics.py \
  --device 0 --steps 1 --warmup 3 --duration 10 \
  --bmodel third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel
```

当前探针要求单网络、静态单 stage、batch 1、单个 FP32 输入和输出。它使用固定输入，首次上传、初始化及缓冲准备在计时之外。

| 模式 | 循环 |
|---|---|
| `compute` | 模型提交与同步等待 |
| `copy` | 重复读取已经生成的输出张量 |
| `compute-copy` | 模型执行、同步及完整结果读取 |

`--modes` 选择其中一种或多种；`--steps` 指定递增进程数。`--copy-chunk-bytes` 可配置读取分块，0 表示完整读取。使用自定义构建目录时通过 `--app` 指定对应探针。

输出包括 `config.json`、`summary.json`、`run_state.json`、`report.md`、`stages.csv` 和逐进程日志，记录循环次数、时长、输出一致性和异常。

## 传输与独立模型计时

```bash
python3 tools/diagnostics/run_pcie_bandwidth.py --device 0
python3 tools/diagnostics/run_single_card_tpu_bench.py --device 0
```

传输工具使用安装的 `test_cdma_perf`；路径不同可用 `--app` 指定。输出 `bandwidth.csv`、`summary.json`、前后快照及原始记录，区分方向、大小、调用计时和数据核对结果。

模型计时工具依赖 PATH 中的 `bmrt_test` 及官方 `yolov8s_int8_1b.bmodel`、`yolov8s_int8_4b.bmodel`。它记录 batch、循环耗时、模型标识及资源采样，不读取视频。

## TPU 高负载下的解码吞吐

两张卡的传输 / 计算隔离和解码扫描结果见 [2026-09-10 实测报告](../../demos/hdmi_wall/docs/capacity-validation.md)。

先结束目标卡上的其他任务。这个工具每次只测一张卡，将解码与推理负载独立控制，不启动 HDMI。依赖 SOPHON FFmpeg、已构建的 `inference_probe.pcie` 和官方 YOLOv8s 模型；下例使用已准备的官方 600 秒循环素材，素材准备见 [本地数据说明](../../data/README.md#hdmi-连续演示素材)。

```bash
python3 tools/diagnostics/run_decode_capacity.py \
  --device 0 --input data/inputs/hdmi_wall_demo_loop_600s.mp4 \
  --steps 8,16,32 --warmup 10 --duration 30 --window 10 \
  --target-fps 24 --throughput
```

每档依次运行纯解码和带模型负载两组。`--throughput` 取消本地源节流，避免被 32×24 FPS 的输入供应量限制；去掉它则按源时间戳读取。`--groups baseline|loaded` 可只运行一组，`--load-processes` 默认 2，`--steps` 最大允许 64 路；允许配置不代表该卡支持相应数量的解码实例。切换到 device 1 应在 device 0 测试结束后执行，避免共享主机负载混入链路对比。

负载为驻留全零张量上的真实模型提交与同步，不逐帧回传检测输出，也不消费这些解码帧。它回答“TPU 计算忙碌时解码受多少影响”，不等于完整视频检测流水线；后者还包含预处理、结果回读、NMS 和预览。每个模型 worker 的初始输出先核对后才发布就绪标志；解码测量结束后定向停止这些负载进程，不把提前停止的模型进程当作完整推理吞吐测试。

输出 `report.md`、`summary.json`、`config.json` 和 `run_state.json`；各组保存解码窗口、逐路进度、错误日志和原始 TPU 采样。TPU 只按解码正式区间筛选，默认要求至少 3 个有效采样、无失败采样、至少 90% 样本达到 95%，才标记 `full_load_observed=true`。这不是每个瞬间的连续满载保证。没有达到该条件时，不能把该组称为满载下的解码能力。

应比较同一路数的两组结果；扫描峰值只代表已测配置中的最高吞吐，不能直接换算成已验收的实时摄像头路数。此工具没有源帧年龄和相机端到端延时测量，FFmpeg 进度更新也具有采样粒度。按 Ctrl+C 请求收尾，只清理本次启动的子进程。

## 图像检测结果检查

准备完本页的官方资源后，下例自动选取 `datasets/test/` 中的一张 JPG 测试图：

```bash
mkdir -p data/results
diagnostic_image="$(find third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/test \
  -type f -iname '*.jpg' -print -quit)"
test -f "$diagnostic_image" && tools/diagnostics/build/detector_check.pcie \
  0 \
  third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_fp32_1b.bmodel \
  third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/coco.names \
  data/results/detector-check.json \
  "$diagnostic_image"
```

未找到测试图时，按[资源说明](../../data/README.md#官方资源)补齐 `test.tar.gz` 并解压后重试。自有图像可放在 `data/inputs/`，替换最后的图像参数即可。

参数是位置参数，输出文件必须尚不存在；重复运行时换一个输出文件名。结果为 JSON 数组，每个图像项包含 `image_name` 与 `bboxes`；框内含 `category_id`、`score`、`bbox=[x,y,width,height]`。类别 ID 是模型索引，不自动映射为数据集类别编号。

此程序导出结果供对照，不自动计算准确率或 AP。

## 实际运行数据

最新逐卡传输、驻留模型和解码扫描见 [2026-09-10 容量报告](../../demos/hdmi_wall/docs/capacity-validation.md)，计算方法见 [公式与复算](../../docs/performance-calculations.md)。下面保留 09-07 的单进程历史记录，不覆盖新数据；这些探针的次/秒不能作为 HDMI 检测 FPS。

以下为 2026-09-07 在 RK3588 + 单张 BM1684X 上完成的诊断记录。模型为官方 YOLOv8s INT8 batch 1，单进程，预热 3 秒。输入为常驻的全零张量，输入/输出边界为 FP32；不读取或解码视频。

| 模式 | 实际测量时长 | 迭代次数 | 结果 | 输出检查 |
|---|---:|---:|---|---|
| `compute` 模型提交与同步 | 10.0020 秒 | 3,356 | 335.53 次/秒 | 参考输出一致，进程正常退出 |
| `copy` 已生成结果回传 | 10.0039 秒 | 1,234 | 348.15 MB/s | 参考输出一致，进程正常退出 |

每次回传 2,822,400 字节，带宽按 `迭代次数 × 每次字节数 ÷ 实测秒数 ÷ 1,000,000` 计算。`compute` 的迭代速率不包含视频解码、预处理和逐帧结果回传，不能作为视频 FPS。

本次目录整理后未重新上板执行；这些是历史单进程记录，固定输入参考一致也不代表真实图像检测准确。首次运行后从终端打印的目录打开 `report.md`、`summary.json` 和 `run_state.json`，确认运行完成、各项输出核验通过，再解释计时结果。

## 指标边界

固定输入结果首末一致不代表真实视频检测准确。有效回传速度包含 SDK、内存和调用等待，不等于 PCIe 理论带宽。批量模型的图像数量与模型调用次数需分开计算；独立模型速度不能当作完整视频 FPS。共享定义见[指标说明](../../docs/metrics.md)。

源码检查位于 `tests/`；所有运行日志、结果及图像留在本地 `data/results/`。
