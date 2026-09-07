# 推理与输出读取定位

最新实测见[推理、回传与主控开销对照](analysis-inference-diagnostics-20260907.md)。

该工具用于区分模型执行、同步等待和输出回传的开销。它不处理视频，不输出目标检测 FPS，不用于业务准确率验收。

## 视频链路细分计时

正常解码＋推理测试的 `detections.jsonl` 和 `streams.csv` 包含以下字段：

| 字段 | 范围 |
|---|---|
| `inference_submit_ms` | 模型提交，包括所选路径的输出 tensor 分配 |
| `inference_sync_ms` | 提交后 `bm_thread_sync` 调用的等待 |
| `input_release_ms` | 推理输入显存释放 |
| `output_allocation_ms` | 主机输出缓冲取得或申请 |
| `output_copy_ms` | `bm_memcpy_d2s_partial` 调用，包括 SDK 内部等待 |
| `cpu_postprocess_ms` | 候选框筛选、NMS 和坐标还原 |

CSV 对应字段以 `_mean_ms` 结尾。旧结果没有采集的字段留空，不补零。`inference_ms` 保留整个提交、同步和输入释放区间，不能当作芯片内部算子执行时间。

```bash
bash scripts/run_single_card_analysis_auto.sh --device 1 --steps 1,2,4,6,8 \
  --warmup 30 --duration 60 --window 30
```

## 驻留张量隔离测试

先结束目标卡上其他测试，再编译并执行：

```bash
cmake -S src/single_card_pipeline -B src/single_card_pipeline/build
cmake --build src/single_card_pipeline/build -j2
python3 scripts/run_inference_diagnostics.py --device 1 --steps 1,2,4,8
```

默认每组预热 3 秒、测量 10 秒，顺序运行以下三种模式：

| 模式 | 测量循环 |
|---|---|
| `compute` | 复用显存输入和输出，提交模型并等待完成 |
| `copy` | 不重复执行模型，只重复回传已生成的输出张量 |
| `compute-copy` | 每次提交模型、等待完成、回传完整输出 |

输入固定为全零 FP32 张量，首次上传、模型加载、缓冲申请和参考输出生成都在计时之外。每个进程保留独立模型及缓冲；所有进程就绪后通过单调时钟屏障同时开始。当前只支持单网络、静态单 stage、batch 1、单个 FP32 输入和输出的模型。

可以用 `--modes copy --steps 1,2,4,8 --warmup 5 --duration 30` 单独复测输出回传。`--bmodel` 可以指定符合约束的其他模型。测试不会自动停止其他进程。

控制台打印结果目录，`report.md` 每档结束后更新；同时保存配置、模型与程序 SHA256、CSV、逐进程日志和 JSON。结果按每个进程的实际测量时长计算，再汇总；最终循环超过目标结束时间的部分计入分母。异常退出会终止同档其他 worker，报告保留失败或未完成状态。

输出核对是固定输入下首末输出逐字节比较，不能代替真实视频上的检测结果对照。`copy` 模式报告的是该 SDK 调用、内存和并发条件下的有效回传速度，不是 PCIe 物理链路带宽上限。

## 如何解释

- `compute` 快，而 `compute-copy` 明显变慢：输出读取路径值得优先定位，但仍需原厂 profiler 区分驱动等待、DMA 和主机开销。
- `copy` 在去掉逐帧推理后仍然慢：不能把全部读取耗时归因于正在执行的模型；应进一步检查 SDK 回传路径及平台配置。
- 独立计算和完整视频链路之间的差额，也包含视频处理、输入管理和 CPU 后处理，不应全部算成 PCIe 消耗。
- 只有完整视频测试及检测结果核对都完成，才能报告某项改动带来的实际分析吞吐收益。
