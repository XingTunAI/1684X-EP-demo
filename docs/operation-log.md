# 操作记录

本文记录本仓库初始化阶段做过的操作，方便后续复现和交接。

## 2026-09-04

1. 阅读算能官方资料入口：
   - https://developer.sophgo.com/site/index/material/all/all.html
   - 关注 SDK、SAIL、BMLIB、BMRuntime、BMCV、多媒体开发和 Multimedia 使用手册。
2. 阅读算能 GitHub 组织：
   - https://github.com/sophgo
   - 确认官方 demo 主要在 `sophon-demo` 仓库。
3. 拉取官方 demo 仓库到本地参考目录：

   ```bash
   git clone --depth 1 -b release https://github.com/sophgo/sophon-demo.git
   ```

4. 重点阅读以下官方样例：
   - `sophon-demo/README.md`
   - `sophon-demo/sample/YOLOv5/README.md`
   - `sophon-demo/sample/YOLOv8_plus_det/cpp/README.md`
   - `sophon-demo/sample/YOLOv8_plus_det/python/README.md`
   - `sophon-demo/tutorial/yolov8_ffmpeg_encode/README.md`
   - `sophon-demo/application/YOLOv5_multi_QT/README.md`
5. 得到初步结论：
   - YOLOv5/YOLOv8 都适合做首版客户 demo。
   - `YOLOv8_plus_det` 适合证明 BM1684X 推理算力。
   - `yolov8_ffmpeg_encode` 适合证明解码、预处理、推理、画框、编码的完整视频链路。
   - 多卡调度可以先用多进程按 `--dev_id` 分发，后续再收敛成 C++ pipeline。
6. 创建本仓库自己的文档和工具：
   - `README.md`
   - `docs/demo-design.md`
   - `docs/usage.md`
   - `docs/operation-log.md`
   - `tools/run_multicard_yolov8.py`
7. 添加 `.gitignore`，避免把官方 demo、大模型、视频数据和运行结果直接提交进本仓库。
8. 将代码仓迁移到：

   ```text
   C:\QIU\XingTunAI\1684X-EP-demo
   ```

   迁移时保留 `.git`、`README.md`、`docs/`、`tools/`、`src/`、`configs/` 等仓库内容。`sophon-demo/` 是官方外部参考仓库，已被 `.gitignore` 排除，未复制到新仓库目录；需要时可按下方命令重新拉取。

## 当前本地依赖状态

当前目录下的 `sophon-demo/` 是为了阅读和改造参考临时拉取的官方仓库。本项目默认不把它提交进自己的 git 历史。

如果新机器上没有该目录，可执行：

```bash
git clone --depth 1 -b release https://github.com/sophgo/sophon-demo.git
```
