# YOLOv8n INT8 模型对照

## 条件

使用原厂预编译 YOLOv8n INT8 batch 1，模型输入保持 640×640、COCO 80 类。RK3588 + BM1684X device 1，输入视频为 1080p25 H.264 BBB，device-bgr，每帧推理，不画框、不编码。

模型来源为 [SOPHON Demo 的 nano 下载脚本](https://github.com/sophgo/sophon-demo/blob/485e8a0dd21e3bba6cfa3c4c0241c8e28f76541b/sample/YOLOv8_plus_det/scripts/download_yolov8_nano.sh)。本轮模型由 TPU-MLIR v1.27-20260206 编译；此前 YOLOv8s 基线为 v1.14-20241231，因此比较的是两份预编译交付模型，不能将全部差异仅归因于网络规模。

- BModel 文件大小：9,494,528 字节。
- SHA256：`a9d6cec7390072402f6ea5bedef18c9262d9566983a713268b16908a3a63be32`。
- 输入：`[1,3,640,640]`，FP32；INT8 指模型内部计算配置。
- 输出：`[1,8400,84]`，FP32，每帧 2,822,400 字节，与 YOLOv8s 基线相同。

## 实测结果

每档预热 30 秒，采集 60 秒，窗口 30 秒。以下为完整解码、预处理、推理、输出回传和 CPU 后处理的测量结果，未启用抽帧。

| 路数 | 总平均 FPS | 平均每路 FPS | 最差逐路窗口 FPS |
| ---: | ---: | ---: | ---: |
| 1 | 25.00 | 25.00 | 24.97 |
| 4 | 99.99 | 25.00 | 24.98 |
| 8 | 140.21 | 17.53 | 17.20 |
| 12 | 127.05 | 10.59 | 10.01 |
| 16 | 119.65 | 7.48 | 6.94 |
| 20 | 116.20 | 5.81 | 5.17 |

[档位与逐路统计](../benchmarks/20260907/20260907_172643_129047/stages.csv)。20 路每路 20 FPS 的目标未达到。8 路 YOLOv8s 基线为 141.94 FPS，本轮 YOLOv8n 为 140.21 FPS；更换 Nano 没有改善完整视频链路吞吐。单次短测不代表生产稳定容量。

独立诊断使用常驻零输入和输出缓冲，不含解码、图像预处理或 CPU 检测后处理：

| 进程数 | 纯计算循环/秒 | 计算并回传循环/秒 | 回传平均毫秒/次 |
| ---: | ---: | ---: | ---: |
| 1 | 444.87 | 57.07 | 15.24 |
| 2 | 477.15 | 120.29 | 14.26 |
| 4 | 474.37 | 195.72 | 17.61 |
| 8 | 476.71 | 140.26 | 54.12 |

[诊断数据](../benchmarks/20260907/20260907_172417_579221/stages.csv)。纯计算 2/4/8 进程 TPU 采样中位数均为 100%；8 进程计算并回传时为 30%。采样包含启动、预热及收尾。诊断循环吞吐不能当作实际视频分析 FPS。上述结果将瓶颈定位进一步指向输出回传及其调度，尚不能分离驱动等待、内存复制和 PCIe 传输各自占比。

## 输出一致性与精度边界

- 六档正常结束。56,254 条检测记录全部与同轮单路相同帧结果一致，没有超出参考帧范围的记录。
- 使用原厂图片例程和本项目 `detector_check.pcie`，同一 Nano 模型、置信度 0.25、NMS 0.7，对 4 张官方测试图片得到相同的 85 个检测结果；类别、框坐标、置信度及框顺序逐项一致。[核对记录](../benchmarks/20260907/20260907_172643_129047/reference_check.json)。该检查不等同于完整视频路径或业务准确率验证。
- 与 YOLOv8s 的 2,256 个重叠视频帧相比，检测数为 S：2,635，N：33,302；2,244 帧检测数不同。没有真实标签，不能把更多框解释为精度更高，也不能据此直接判定误检率。[跨模型计数](../benchmarks/20260907/20260907_172643_129047/detection_comparison.json)。

## 输出数据量约束

设备 1 当前链路为 PCIe Gen3 ×1（`0001:11:00.0`，8.0 GT/s，宽度 1）。保持每帧完整 FP32 输出，400 次分析/秒仅输出回传就需要 `2,822,400 × 400 = 1,128,960,000` 字节/秒。Gen3 ×1 在 128b/130b 编码后理论单向上限约 984,615,385 字节/秒，尚未扣除协议开销，因而当前完整输出格式无法通过这条链路达到 400 次/秒。编码参数参见 [Intel PCIe 文档](https://cdrdv2-public.intel.com/335195/335195_005.pdf)。这并不意味着当前 116～140 FPS 已经达到链路极限。

可验证的改造方向是：卡端筛选/后处理后回传紧凑结果，或重新编译更低精度的输出并核对检测精度。仅把 `[8400,84]` 拆成框与类别两个 FP32 输出，总字节数不变。双缓冲、受控并发和流水线可以尝试隐藏等待，但无法突破链路字节量上限；本项目此前单独复用输出缓冲未获得吞吐提升。这些进一步改造尚未计入本轮成绩。

## 复测

以运行程序的同一账号在设备执行：

```bash
bash scripts/prepare_yolov8n_model.sh
python3 scripts/run_inference_diagnostics.py \
  --bmodel third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8n_int8_1b.bmodel \
  --modes compute compute-copy
bash scripts/run_single_card_analysis_auto.sh --model-preset yolov8n \
  --steps 1,4,8,12,16,20 --warmup 30 --duration 60 --window 30
```

预设只决定模型路径，不更改阈值、图像处理、抽帧策略或输出格式。默认模型仍为 YOLOv8s；`--bmodel` 可显式指定其他兼容模型。模型文件通过准备脚本获取，测试报告保存模型和 worker 的 SHA256。

图片输出核对工具可随项目 CMake 构建：

```bash
cmake --build src/single_card_pipeline/build --target detector_check.pcie -j4
src/single_card_pipeline/build/detector_check.pcie 1 \
  third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8n_int8_1b.bmodel \
  third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/coco.names \
  results/nano_image_check.json \
  third_party/sophon-demo/sample/YOLOv8_plus_det/datasets/test/*.jpg
```

输出路径必须不存在；工具拒绝覆盖已有结果。参考程序为上述固定版本的 `sample/YOLOv8_plus_det/cpp/yolov8_bmcv`，使用相同图片、设备及阈值。比较 JSON 时只按图片名排序，不修改框顺序或数值。
