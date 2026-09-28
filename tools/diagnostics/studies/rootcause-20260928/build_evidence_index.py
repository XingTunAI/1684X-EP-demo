"""Write an auditable catalog after all collections have finished."""
from pathlib import Path
import hashlib
import json

root = Path(__file__).resolve().parent


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


archives = {
    'evidence.tar.gz': '原始双卡基线、预览消融和重复对照',
    'preprocess-split-evidence.tar.gz': '两步预处理 API 拆分与前后观察器对照',
    'vpp-attribution-v2-evidence.tar.gz': 'API/驱动事件、原始内核函数图、缓冲统计及前后对照',
    'decoder-format-evidence.tar.gz': '默认 2 缓冲格式 ABBA、240 帧正确性、真实解码调用栈；保留时延退化',
    'decoder-buffers8-evidence.tar.gz': '双卡额外 8 缓冲，线性→压缩→线性',
    'samecard-format-evidence.tar.gz': 'device 2 同卡同槽 8→5→8 GT/s，两种格式交叉验证与恢复检查',
}
rows = []
for name, purpose in archives.items():
    p = root / name
    rows.append({'archive': name, 'purpose': purpose, 'bytes': p.stat().st_size, 'sha256': digest(p)})
index = '''# 1684X 根因诊断证据索引（2026-09-28）

本地：`/Users/timxia/Workspace/tun-star/1684X-EP-demo/local/rootcause-20260928/`

服务器：`/userdata/1684X-EP-demo/data/results/rootcause-20260928/`

最终解释：`../../docs/vpp-root-cause.md`。本地 `analysis-kit.tar.gz` 另存分析代码、配套解析器、驱动副本和报告快照。原始数据包不因重算修改。

## 原始归档

| 归档 | 内容 | SHA256 |
|---|---|---|
'''
index += ''.join(f"| {r['archive']} | {r['purpose']} | `{r['sha256']}` |\n" for r in rows)
index += '''
## 从问题到证据

| 问题 | 主要数据与分析 |
|---|---|
| 229/272 FPS 是否算对、32 路与 139 ms 的关系 | `evidence/dual-wall/`、`verify_calculations.py`、`verified-calculations.json` |
| PCIe 线速能否解释传输时间 | `pcie2-small-copy-20260928/`、`verify_pcie2_theory.py`、`pcie2-theory-verification.json` |
| 时间是否在主机 API 准备 | `preprocess-split/`、`analyze_preprocess_split.py` |
| 驱动内等名额、CDMA 与完成等待怎么分 | `vpp-attribution-v2/analysis-v2/`、`analyze_vpp_attribution.py` |
| 隐藏 VPP 调用是谁提交的 | `vpp-attribution-stack-smoke/events/*.stack`、`installed-decoder-vpp.txt` |
| 改解码格式是否改变检测结果 | `decoder-format-smoke/` 两份完整检测 JSON、`comparison.json` |
| 改格式是否恢复性能、是否引入退化 | `decoder-format32/format-analysis.json`，必须保留默认缓冲不足的失败指标 |
| 增加缓冲是否消除该退化 | `decoder-format32-buffers8/format-analysis.json` |
| 是否只是两张卡/插槽本身不同 | `samecard-format/format-analysis.json`、`state.json`、`postflight.json` |
| 资源预算和最终数字如何重算 | `verify_vpp_pool_model.py`、`verify_final_attribution.py`、`final-verification.json` |

每档保存实际命令、stderr/stdout、遥测、逐路摘要和配置；观察器 CSV 保存原始单调时钟、线程和父子调用 ID、返回码与记录丢失数。内核追踪另存每 CPU 缓冲统计、原始函数图、解析排除项与对应表。

## 重算注意

- `analyze_vpp_attribution.py` 和 `analyze_decoder_format.py` 拒绝覆盖既有输出；重算应在原始归档的新解压副本中运行，或先另存已有分析结果。
- `verify_final_attribution.py` 同样拒绝覆盖 `final-verification.json`，输出含输入摘要的 SHA256。脚本需要 Python 3.9+；配套解析器在项目 `tools/diagnostics/`。
- 测试入口：`test_attribution_analysis.py`（3 项），`tools/diagnostics/tests/test_analyze_dispatch_graph.py` 与 `test_analyze_dispatch_wait.py`（合计 10 项）。
- 正式吞吐用完成次数/正式窗口；过期和覆盖丢帧计数是全运行口径。最大解码滞后是状态采样最大值，不是逐帧时延分位数。
- `10 FPS` 是预览上限，分析另外列出实际小图 FPS。VPP 多线程等待不能相加当成单个硬件纯运行时间。
- 两次内核捕获有可见扰动；预算公式使用实测服务时间，只作条件上界一致性检查。
- 原始输入大视频和模型未复制进每个包；各次 preflight 保存服务器路径、大小和 SHA256。包内 `command.json` 指向实际运行的 `build-async/hdmi_wall.pcie`；通用 preflight 还列出其他探针，实际业务二进制另存哈希。
- 诊断包装器只作用于临时测试进程；未部署成默认配置。最后恢复了 device 2 的 8 GT/s ×1，内核 tracer 为 nop，设备无诊断进程占用。

## 版本保留

初轮报告、API 拆分报告、驱动分账报告及本次最终报告各自保存，后续证据通过链接衔接。不能用历史 9.755 ms 的单次 CDMA 长尾代表当前平均值；不能将当前追踪样本当成最初 45 秒窗口的逐帧分解。线性默认 2 缓冲虽然 TPU 接近满载，但出现过期丢帧，已明确保留为不合格配置。
'''
(root / 'EVIDENCE-INDEX.md').write_text(index)
(root / 'archive-checksums.json').write_text(json.dumps(rows, indent=2, ensure_ascii=False) + '\n')
print(f'Indexed {len(rows)} raw archives.')
