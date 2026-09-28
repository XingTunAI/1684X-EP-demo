# 2026-09-28 VPP 根因调查快照

[最终报告](../../../../docs/vpp-root-cause.md) · [原始归档索引](../../../../docs/vpp-evidence-index.md)

此目录逐字节保存当时在 `local/rootcause-20260928/` 使用的诊断源码、采集计划与选定 JSON 摘要。`snapshot-manifest.json` 记录每个文件的原始路径、大小和 SHA256。它是实验记录，没有修改业务程序、SDK 或默认解码配置。

## 文件用途

| 文件 | 用途 |
|---|---|
| `preprocess_timing.cpp`、`vpp_attribution.cpp` | 临时进程内 API / 驱动提交计时，记录原始参数对应关系与返回码 |
| `vpp_attribution_stack.cpp` | 独立短测中采集未分类 VPP 请求的调用栈 |
| `decoder_format_override*.cpp` | 诊断进程内解码格式及缓冲参数对照；不是生产修复入口 |
| `run_*.py`、`*_probe.py`、`dual_wall_validation.py` | 当时的硬件采集脚本，有固定设备、目录、模型和显示环境配置 |
| `analyze_*.py` | 原始事件、函数图与格式消融分析 |
| `verify_*.py` | 线程计时、PCIe 理论、VPP 名额预算和最终表格复算 |
| `test_attribution_analysis.py` | 跨线程配对、父子计时去重、不一致拒绝的三项测试 |
| `vpp-attribution-v2/analysis-v2/summary.json` | 驱动分账摘要，完整事件和排除表在原始包中 |
| `decoder-format32*/format-analysis.json` | 默认缓冲退化与额外缓冲 8 的对照摘要 |
| `samecard-format/` | 同卡变速摘要、完成状态及恢复检查 |
| `final-verification.json`、`vpp-pool-model.json` | 已复算结果及假设；前者还记录输入 SHA256 |

## 复算路径与依赖

保留原脚本路径假设，不把存档移动误当成工具重构。复算时在新的工作副本中恢复以下结构：

```text
工作副本/
├── local/rootcause-20260928/   ← 本目录的副本
└── tools/diagnostics/         ← 仓库同名目录的 Python 工具
```

需要 Python 3.9+。`analyze_vpp_attribution.py` 使用仓库已有的 `analyze_dispatch_graph.py` 和 `analyze_dispatch_wait.py`。

本目录选定摘要足以运行 `verify_final_attribution.py` 和 `verify_vpp_pool_model.py`。在副本中先将已有 `final-verification.json` / `vpp-pool-model.json` 改名留底，再运行这两个脚本，比较重新生成的 JSON。最终验证脚本拒绝覆盖既有输出；不要为重算删除原始数据目录中的结果。

重新解析原始 API / 内核事件、早期对照或 PCIe D2H 实验，则需要从[证据索引](../../../../docs/vpp-evidence-index.md)所列归档恢复对应数据目录。先核对归档 SHA256，保存已有分析输出后再运行；不能只凭这里的摘要声称重新处理了全部事件。

采集脚本按当时服务器路径 `/userdata/1684X-EP-demo` 编写，部分会开启函数追踪或切换 device 2 的 PCIe 速率；模块顶层即开始工作。重新采集前须按计划检查设备、空闲状态和输出目录，不能通过导入这些脚本进行普通测试。此次 Git 提交只保存记录，没有重新启动硬件实验。

## 分析测试

从仓库根目录运行以下检查，不访问设备：

```bash
PYTHONPATH=tools/diagnostics:tools/diagnostics/studies/rootcause-20260928 \
  python3 -m unittest discover \
  -s tools/diagnostics/studies/rootcause-20260928 \
  -p test_attribution_analysis.py

python3 -m unittest discover -s tools/diagnostics/tests -p 'test_analyze_dispatch*.py'
```

完整日志、原始内核追踪、观察器 CSV、大视频、模型、已安装库及驱动副本保留在原数据目录和 `analysis-kit.tar.gz`，不重复纳入 Git。目录内路径字符串是原始证据，不保证是另一台机器上的可访问位置。
