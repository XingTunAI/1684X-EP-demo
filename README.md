# RK3588 + BM1684X EP Demo

[路数递增测试与 TPU 曲线](docs/analysis-ladder-20260907.md)：从 1 路逐档增加到 32 路，本轮最高总吞吐出现在 8 路（143.22 FPS）。

本仓库演示 RK3588 主机通过 PCIe 调用 BM1684X 进行视频解码与 YOLO 目标检测，包含单卡自动压测、图像路径优化及已有多卡功能验证代码。当前阶段以 **单卡性能摸底与 Demo 演示** 为主，更新至 2026-09-07。

后处理拆分和 6/32 路并发对照见[输出回传分析与并发对照](docs/analysis-transfer-20260907.md)。6 路实测总平均 135.43 FPS；32 路限回传并发未提高总吞吐，默认配置保持不变。

新增 YouTube 道路车流素材已单独整理，来源、制作和切换命令见[车流素材说明](docs/youtube-test-media.md)。现有性能结论仍来自 BBB 动画，新素材没有替代历史测试，也未改默认输入。

先读[Demo 总览](docs/demo-summary-20260907.md)，运行看[纯解码操作](docs/single-card-decode.md)或[解码＋推理操作](docs/single-card-analysis.md)，查看输出看[报告阅读指南](docs/benchmark-report-guide.md)。

## 实测结果

| 项目 | 实测结果 | 范围 |
|---|---|---|
| 单卡 32 路纯解码 | 总平均 815.21 FPS，最低逐路窗口 23.26 FPS | device 1，1080p25 H.264，短测 |
| 单卡 30 路纯解码，正式采集 15 分钟 | 总平均 749.96 FPS，最低逐路窗口 22.59 FPS | 有一个统计窗口波动，历史帧率验收未通过 |
| 单卡 32 路解码＋推理 | **总平均 114.10 FPS，平均每路 3.57 FPS** | YOLOv8s INT8 batch 1，预热 180 秒、正式采集约 120 秒 |
| 图像路径优化 | 总吞吐约为原 BGR 路径的 **3.49 倍** | 34,006 条检测列表与原路径对应帧一致 |
| 独立模型计算 | batch 1：354.50 图/秒；batch 4：370.11 图/秒 | 两者 TPU 采样峰值 100%，不含完整视频链路 |
| 调度与结果检查 | 解码 18 项、流水线 12 项测试通过 | 不代表生产容量或算法精度验收 |

上述视频使用同一份 BBB 动画素材复制为多路本地输入，不是 32 路摄像头。纯解码、完整分析、独立模型计算是不同测试，不能互相替代。当前分析每帧执行，处理慢时会落后输入计划；未实现抽帧，也不包含实时画框显示或编码。实际承载能力取决于模型、输入规格、分析频率和输出要求。

默认只记录性能，完成为 `measured`，不以每路 25 FPS 判通过/失败。运行异常仍会停止测试。旧报告保留原门槛与状态。多卡联合容量、客户业务精度、抽帧和实时显示不在本轮实测范围内。

## 快速运行

以下命令在已准备好 SDK、素材与模型的 RK3588 工程根目录运行。新环境先看[环境与官方样例准备](docs/usage.md)、[模型准备](docs/run-yolo.md)和[素材说明](docs/test-media.md)。第三方 SDK、模型、视频和历史结果不随 Git 克隆下载。

物理卡 1 对应软件 `device 1`（PCIe 3.0 ×1）；物理卡 2 对应 `device 0`（PCIe 2.0 ×1）。示例显式指定 device 1。同一张卡上的测试依次执行，避免相互干扰。

```bash
# 单卡 32 路纯解码，约 3 分钟
bash scripts/run_single_card_decode_auto.sh --device 1 --steps 32

# 编译解码＋推理 worker（新环境或源码更新后）
cmake -S src/single_card_pipeline -B src/single_card_pipeline/build
cmake --build src/single_card_pipeline/build -j2

# 单卡解码＋推理：从 1 路逐档增加，约 14 分钟
bash scripts/run_single_card_analysis_auto.sh --device 1 \
  --steps 1,2,4,6,8,12,16,24,32 --measure-only --image-path device-bgr \
  --warmup 30 --duration 60 --window 30 --stall-timeout 180

# 独立模型计算，在同卡视频测试结束后执行
python3 scripts/run_single_card_tpu_bench.py --device 1
```

| 不带覆盖参数的一键入口 | 默认设备与档位 | 默认素材 |
|---|---|---|
| run_single_card_decode_auto.sh | device 0；8/16/24/32 路 | 596.44 秒短视频 |
| run_single_card_analysis_auto.sh | device 1；1/2/4/8 路 | 连续 20 分钟视频 |

30 路 15 分钟解码复测要显式选择 20 分钟素材，完整命令见[纯解码操作](docs/single-card-decode.md)。短素材循环附近曾发生错误，不能在长测中忽略该边界。

## 测试输出

[公开测试数据](benchmarks/20260907/README.md)包含参数、逐路帧率和窗口统计，可在 GitHub 直接查看。

控制台打印唯一 Results 目录。`report.md` 在开始、每档结束和收尾时更新；单档未结束时等待最终数据属于正常状态。先读报告，再查逐路 CSV 和原始日志。纯解码当前自动生成器不输出 windows.csv，逐窗口数据保存在 summary.json 中；分析模式会自动生成 windows.csv。

| 测试类型 | 运行输出目录 |
|---|---|
| 解码 | `results/decode/测试编号/` |
| 解码＋推理 | `results/analysis/测试编号/` |
| 独立模型计算 | `results/tpu/测试编号/` |

## 文档索引

| 用途 | 文档 |
|---|---|
| 功能与测试结果 | [Demo 总览](docs/demo-summary-20260907.md) |
| 纯解码复测 | [操作说明](docs/single-card-decode.md) / [源码入口](src/single_card_decode/README.md) / [实测结论](docs/decode-results-20260907.md) |
| 解码＋推理复测 | [操作说明](docs/single-card-analysis.md) / [源码与 encode 模式](src/single_card_pipeline/README.md) |
| 分析优化与计算对照 | [最新优化结论](docs/analysis-optimization-20260907.md) / [优化前单路基线](docs/analysis-results-20260907.md) |
| 报告、CSV、状态与资源解释 | [报告阅读指南](docs/benchmark-report-guide.md) |
| 视频与模型准备 | [素材说明](docs/test-media.md) |
| 环境安装、官方样例 | [使用文档](docs/usage.md) / [模型准备](docs/run-yolo.md) / [硬件参考](docs/board-status.md) |
| 多卡功能验证 | [单进程多卡验证](docs/single-process-multicard.md) / [源码说明](src/README.md) |
| 历史设计与显示参考 | [设计说明](docs/demo-design.md) / [HDMI 说明](docs/hdmi-display.md) / [验证参考](docs/roadmap.md) |
| 操作历史 | [操作记录](docs/operation-log.md) |

## 工程结构

```text
.
├── configs/                      # 现有多卡样例配置
├── benchmarks/                   # 可直接浏览的测试统计数据
├── docs/                         # 操作、结论、演示与历史参考
├── scripts/                      # 一键压测、编译和环境辅助入口
├── src/
│   ├── single_card_decode/       # 纯解码调度与报告
│   ├── single_card_pipeline/     # 分析 worker、可选编码模式与报告
│   ├── yolov8_bmcv/              # 官方样例的设备绑定修复
│   └── yolov8_multicard/         # 已有单进程多卡功能验证
├── tools/                        # 辅助脚本与兼容入口
├── third_party/sophon-demo/      # 官方依赖，单独准备
├── datasets/                     # 素材，不提交 Git
└── results/                      # 原始运行结果，不提交 Git
```

已有官方样例修复及多卡验证保持可用，相关指令见各自文档；其设备数量和测试条件按对应历史记录解释，不视为本轮 32 路单卡测试已验证多卡容量。实时显示相关文件为独立样例参考，不包含在当前压测吞吐数字中。

官方参考：[资料入口](https://developer.sophgo.com/site/index/material/all/all.html)、[SOPHGO GitHub](https://github.com/sophgo)。
