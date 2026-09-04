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
9. 根据板端 `lspci` / `lspci -vvv` 输出新增 `docs/board-status.md`，记录：
   - RK3588 已枚举到两张 `1f1c:1686` 算能 PCIe 加速设备。
   - 两张设备均已绑定 `bmdrv`。
   - 第一张设备链路为 `Speed 8GT/s, Width x1`。
   - 第二张设备链路为 `Speed 5GT/s, Width x1`。
   - 补充 `sophon-debs-0.5.1_LTS` 安装顺序和安装后验证命令。
10. 读取本地 `sophon-debs-0.5.1_LTS/` 目录，确认当前包含：
   - `sophon-driver_0.5.1-LTS-rk3588fix2_arm64.deb`
   - `sophon-libsophon_0.5.1-LTS_arm64.deb`
   - `sophon-mw-sophon-ffmpeg_0.14.0_arm64.deb`
   - `sophon-mw-sophon-ffmpeg-dev_0.14.0_arm64.deb`
   - `sophon-mw-sophon-opencv_0.14.0_arm64.deb`
   - `sophon-mw-sophon-opencv-dev_0.14.0_arm64.deb`
11. 添加 `scripts/install_sophon_debs.sh`，用于在 RK3588 板端安装上述 deb 包并执行基础验证。
12. 为开始跑 YOLO 做准备，新增：
   - `docs/run-yolo.md`：RK3588 + BM1684X PCIe 跑通 YOLOv8 C++ demo 的完整步骤。
   - `scripts/prepare_yolov8_demo.sh`：安装基础工具、拉取官方 `sophon-demo`、下载 BM1684X YOLOv8 模型和测试数据。
   - `scripts/build_yolov8_cpp.sh`：编译官方 `sample/YOLOv8_plus_det/cpp/yolov8_bmcv`。
   - `scripts/run_yolov8_cpp_single.sh`：单卡运行 YOLOv8 C++ demo。
   - `scripts/run_yolov8_cpp_dual.sh`：双卡并发运行 YOLOv8 C++ demo。
13. 在 `docs/run-yolo.md` 中补充编译位置建议；后续根据服务器 `.bashrc` 中的 Docker 习惯，调整为服务器 Docker 编译/准备，RK3588 板端运行验证。
14. 将本地仓库打包并迁移到 Ubuntu 服务器：
   - 本地路径：`C:\QIU\XingTunAI\1684X-EP-demo`
   - 初始服务器路径：`~/XingTunAI/1684X-EP-demo`
   - 迁移内容：仓库源码、文档、脚本和 `.git` 历史。
   - 排除内容：`sophon-debs-0.5.1_LTS/`、`tools/__pycache__/`。
   - 迁移后在服务器上执行 `chmod +x scripts/*.sh`，确保板端/服务器 Linux 环境可直接运行脚本。
15. 根据服务器 workspace 目录结构，将仓库移动到更合适的算能目录：
   - 最终服务器路径：`/data/users/ubuntu/workspace/Sophgo/bm1684/1684X-EP-demo`
   - `~/workspace` 是 `/data/users/ubuntu/workspace` 的软链接。
   - 服务器已有 `Sophgo/bm1684`、`Sophgo/bm1688`、`Sophgo/sophon-tools` 等目录；考虑 BM1684/BM1684X 归类关系，本项目放入 `Sophgo/bm1684`。
16. 用户已手动创建 Docker 容器 `1684x_ep_demo`，镜像为 `bm1688_docker:latest`。不再保留 Docker 创建脚本；文档改为使用 `sudo docker start 1684x_ep_demo` 和 `sudo docker exec -it 1684x_ep_demo /bin/bash` 进入已有容器。

## 当前本地依赖状态

当前目录下的 `sophon-demo/` 是为了阅读和改造参考临时拉取的官方仓库。本项目默认不把它提交进自己的 git 历史。

如果新机器上没有该目录，可执行：

```bash
git clone --depth 1 -b release https://github.com/sophgo/sophon-demo.git
```
