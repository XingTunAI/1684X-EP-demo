# 项目记录

本文记录本仓库初始化阶段的主要信息，便于复现和交接。

## 2026-09-04

1. 阅读算能官方资料入口：
   - https://developer.sophgo.com/site/index/material/all/all.html
   - 关注 SDK、SAIL、BMLIB、BMRuntime、BMCV、多媒体开发和 Multimedia 使用手册。
2. 阅读算能 GitHub 组织：
   - https://github.com/sophgo
   - 确认官方 demo 主要在 `sophon-demo` 仓库。
3. 拉取官方 demo 仓库作为参考：

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
7. 添加 `.gitignore`，避免将官方 demo、大模型、视频数据和运行结果直接提交进本仓库。
8. 将本项目整理为独立仓库，只保留本文档、脚本、配置和自研代码。`sophon-demo/` 是官方外部参考仓库，已被 `.gitignore` 排除；需要时可按下方命令重新拉取。
9. 根据板端 `lspci` / `lspci -vvv` 输出新增 `docs/board-status.md`，记录：
   - RK3588 已枚举到两张 `1f1c:1686` 算能 PCIe 加速设备。
   - 两张设备均已绑定 `bmdrv`。
   - 第一张设备链路为 `Speed 8GT/s, Width x1`。
   - 第二张设备链路为 `Speed 5GT/s, Width x1`。
   - 补充 `sophon-debs-0.5.1_LTS` 安装顺序和安装后验证命令。
10. 确认 `sophon-debs-0.5.1_LTS/` 推荐包含：
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
13. 在 `docs/run-yolo.md` 中补充编译和运行建议，推荐在 RK3588 板端完成 C++ 编译与验证。
14. 补充 C++ 版编译和运行脚本，并把默认官方 demo 路径改为 `$HOME/sophon-demo`，方便不同 RK3588 板端复现。
15. 增加双卡动态分配脚本，使用 `DEVICE_SPECS` 明确描述 `dev_id`、权重、角色、BDF 和 PCIe 链路。
16. 增加 HDMI/XFCE 显示说明和播放脚本，用于把 YOLO 检测后的结果视频显示到 HDMI 屏幕。
17. 增加 YOLOv8 C++ HDMI 实时预览补丁和 FIFO 播放脚本。当前板端验证结果：
   - SOPHON OpenCV HighGUI/X11 后端不可用时，`--display=1` 不作为推荐路线。
   - `--display_fifo` + 系统 `ffplay` 可用于 HDMI 实时预览。
   - `dev_id=1` 主卡和 `dev_id=0` 副卡均已完成短时推理预览验证。

## 外部依赖说明

`sophon-demo/` 用作官方参考仓库。本项目默认不将其提交进自身 git 历史。

如果部署环境中没有该目录，可执行：

```bash
git clone --depth 1 -b release https://github.com/sophgo/sophon-demo.git
```
