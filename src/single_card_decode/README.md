# 单卡解码压测 Demo

验证一张 BM1684X 同时解码多路 1080P/25 FPS 视频的能力。当前只测解码，算法分析容量另测。Python 仅负责调度，实际解码由 SOPHON FFmpeg 的硬件解码器执行。

已完成的单卡阶梯和细测结果见[2026-09-07 解码测试记录](../../docs/decode-results-20260907.md)。卡 1 的 26/28/30 路通过本轮短测帧率门槛，32 路此前未通过；长稳和业务链路仍待验证。

## 一键自动测试与报告

在板端执行：

```bash
cd /home/linaro/1684X-EP-demo
bash scripts/run_single_card_decode_auto.sh
```

默认测试卡 0，读取已同步的 1080P/25 FPS 视频，按 8、16、24、32 路逐档加压。每档预热 60 秒、采集 120 秒，默认记录各路实际 FPS，完成后进入下一档；运行异常或中断则停止。显式 --acceptance 才启用帧率门槛。参数可覆盖，例如 `--device 1` 或 `--steps 8,16 --duration 900`。测试前确认没有其他压测进程并行运行，以免相互干扰。

一键入口在工程根目录的 `scripts/run_single_card_decode_auto.sh`（Shell）和 `scripts/run_single_card_decode_auto.py`（Python），会自动定位工程目录。可从其他目录通过绝对路径启动；所有相对输入/输出参数以工程根目录为基准。`src/single_card_decode/run_auto.py` 仅保留兼容转发。

每档结束后自动更新 `results/decode/日期时间_PID/` 中的：

- `report.md`：可读汇总、每档完成状态、平均及最低窗口 FPS、运行异常。
- `stages.csv`：档位统计，适合表格查看。
- `streams.csv`：逐路加权平均 FPS、最差窗口；默认不判定门槛。
- `summary.json`、`config.json` 和逐路原始日志：保留复现依据。

退出码 0 表示本轮所有计划档位测量完成，1 表示中断/运行异常或显式验收未通过，2 表示配置或环境错误。预检失败时终端打印原因，没有可用性能数据，不能生成通过报告。该默认短测用于摸底，不是生产长稳验收。

已有测试结果也可以补生成报告：

```bash
python3 src/single_card_decode/report.py results/decode/实际测试编号
```

对运行中的旧版本测试，附加 `--watch` 可每 5 秒更新报告，直到本轮结束；它只读取结果，不启动额外解码进程。

```text
src/single_card_decode/
├── stress_decode.py          # 单卡多路、阶梯加压、指标与结果
├── run_auto.py               # 旧入口兼容转发，主入口在 scripts/
├── report.py                 # Markdown/CSV 报告与历史结果转换
├── tests/test_stress_decode.py # 模拟进程测试，不需要算力卡
├── tests/test_report.py      # 加权统计、失败与未测状态验证
└── README.md
```

在 RK3588 工程根目录运行一路验证：

```bash
python3 src/single_card_decode/stress_decode.py \
  --device 0 --input datasets/stress/bbb_1080p25_h264_8mbps.mp4 \
  --steps 1 --warmup 30 --duration 120 --window 60
```

随后将档位改成 `--steps 8,16,24,32`，建议 `--warmup 180 --duration 900`。先按序验证，失败时工具自动停止继续加压。

本地或板端运行配套测试：

```bash
python3 -m unittest discover -s src/single_card_decode/tests -v
```

完整配置和结果说明见[单卡解码压测操作](../../docs/single-card-decode.md)。视频保留在 `datasets/stress/`，结果保留在 `results/decode/`，不放入源码目录。旧入口 `tools/stress_decode.py` 仅作兼容转发。

## 板端同步与短测记录（2026-09-07）

已通过 ADB 同步到 `192.168.5.93` 的 `/home/linaro/1684X-EP-demo/src/single_card_decode/`，视频已同步并核对 SHA256。配套 9 项测试在板端通过。

以 linaro 用户运行卡 0 单路解码，预热 5 秒、采集约 10 秒，两段约 5 秒窗口的 FPS 为 25.12 和 24.90，解码错误日志为空。此短测只验证脚本和设备链路可用，不代表 32 路或长稳测试已通过。原始结果在板端 `results/decode/20260907_094449_5499/`，本地副本在 `results/board-smoke/20260907_094449_5499/`。
