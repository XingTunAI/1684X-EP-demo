# 单卡解码压测 Demo

Python 标准库调度 SOPHON FFmpeg 硬件解码器，每路一个独立进程，输出到 null。仅测解码，不推理、不编码、不显示。当前默认记录性能，低 FPS 不停止；运行异常、中断或显式验收不通过时停止后续档位。

## 一键入口

在设备工程根目录执行：

```bash
bash scripts/run_single_card_decode_auto.sh --device 1 --steps 32
```

不带覆盖参数时，一键脚本默认 device 0、8/16/24/32 路、预热 60 秒、采集 120 秒、窗口 60 秒，读取 596.44 秒短视频。物理卡 1 对应 device 1；不要把物理卡名当作默认参数。30 路 15 分钟复测需显式指定连续 20 分钟素材，命令见[操作说明](../../docs/single-card-decode.md)。

`--measure-only` 是默认行为，正常完成为 `measured`；`--acceptance --min-fps 24.5` 可复现历史帧率判定。25 FPS 是默认输入规格，既不代表推理帧率，也不是当前强制验收线。

## 文档入口

- [操作说明](../../docs/single-card-decode.md)：环境、短测/长测、参数、后台运行、停止和同步。
- [报告阅读指南](../../docs/benchmark-report-guide.md)：文件清单、窗口统计、状态与资源监控。
- [实测结论](../../docs/decode-results-20260907.md)：32 路短测与已完成的 30 路 15 分钟测试。
- [素材说明](../../docs/test-media.md)：两份动画文件、哈希与循环问题。
- [演示总览](../../docs/demo-summary-20260907.md)：功能和测试结果。

## 源码结构与检查

```text
src/single_card_decode/
├── stress_decode.py             # 调度、采样、监控、收尾
├── report.py                    # Markdown/CSV 报告
├── run_auto.py                  # 兼容入口，转到 scripts 中的一键脚本
├── tests/test_stress_decode.py  # 模拟进程、异常和参数检查
├── tests/test_report.py         # 统计、测量模式和历史报告检查
└── README.md
```

Shell/Python 一键入口在 `scripts/run_single_card_decode_auto.sh` / `.py`，会定位工程根目录；`tools/stress_decode.py` 仅转发到核心脚本。核心脚本直接运行时默认 1 路、采集 300 秒且要求显式输入，与一键入口不同。

```bash
python3 -m unittest discover -s src/single_card_decode/tests -v
python3 scripts/run_single_card_decode_auto.py --device 1 --steps 32 --dry-run
```

现有 18 项测试已在板端通过，不需要用新压测验证纯文档修改。dry-run 只打印命令，不证明设备和视频预检通过。代码、脚本与文档可随 Git 同步；`datasets/` 和 `results/` 另行传输。

## 已完成测试的范围

device 1 的 32 路短测总平均 815.21 FPS，最低逐路窗口 23.26 FPS。30 路正式采集 900.47 秒，总平均 749.96 FPS，最低逐路窗口 22.59 FPS；一个窗口波动，历史状态为 fps_fail，运行中无进程异常退出。不能写成“长测尚未执行”，也不能写成“稳定通过”。原短素材 EOF 附近曾报错，连续长素材复测未触及 EOF，并未证明循环问题修复。详细证据以结论文档及原始结果为准。
