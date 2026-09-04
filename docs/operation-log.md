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
18. 在 RK3588 板端重新清理 demo 环境并按文档手动验证：
   - 删除旧的 `/home/linaro/1684X-EP-demo` 和 `/home/linaro/sophon-demo`，避免软链接和历史文件影响。
   - 从 Windows 本地目录 `C:\QIU\XingTunAI\1684X-EP-demo` 通过 `scp` 传到板端 `/home/linaro/1684X-EP-demo`。
   - 传输后目录属主为 `root:root`，建议执行：

     ```bash
     sudo chown -R linaro:linaro ~/1684X-EP-demo
     ```

   - Windows 传输后的 shell 脚本出现 CRLF 换行问题，执行脚本时报：

     ```text
     /bin/bash^M: bad interpreter: No such file or directory
     ```

     使用以下命令修复：

     ```bash
     find ~/1684X-EP-demo -type f -name "*.sh" -exec sed -i 's/\r$//' {} \;
     ```

   - 在 `third_party/sophon-demo/sample/YOLOv8_plus_det` 执行 `scripts/download.sh` 后，测试视频和 `coco.names` 下载成功；模型目录一度只创建了空 `models/`，后续手动下载并解压 `BM1684X.tar.gz` 后确认模型完整。
   - 当前板端 `bm-smi` 能看到两张 BM1684X PCIe 卡，Lib/Driver 版本均为 `0.5.1 LTS SP5`。
   - 官方 `YOLOv8_plus_det/cpp/yolov8_bmcv` 已重新编译生成 `yolov8_bmcv.pcie`。
19. 修复卡 1 运行 YOLOv8 视频样例时的 BMCV handle 不一致错误：
   - 根因是官方样例的 `VideoCapture` 绑定了 `dev_id`，但 `VideoWriter` 没有绑定同一张卡。
   - 新增 `src/yolov8_bmcv/main.cpp`，在仓库内保存已修好的 YOLOv8 C++ 入口源码。
   - `scripts/build_yolov8_cpp.sh` 会在编译前自动把仓库源码同步到官方 demo 目录，不需要手动执行 patch。
   - 板端手动验证双卡运行正常，`bm-smi` 可看到 `dev_id=0` 和 `dev_id=1` 上的 `yolov8_bmcv.pcie` 进程。
   - 单卡 `dev_id=0` 视频推理验证通过，结果：

     ```text
     SUMMARY: yolov8 test
     [   yolov8 preprocess]  loops: 592 avg: 1.944000 ms
     [    yolov8 inference]  loops: 592 avg: 28.256000 ms
     [  yolov8 postprocess]  loops: 592 avg: 60.754000 ms
     ```

   - 单卡 `dev_id=1` 视频推理验证通过，结果：

     ```text
     SUMMARY: yolov8 test
     [   yolov8 preprocess]  loops: 592 avg: 1.884000 ms
     [    yolov8 inference]  loops: 592 avg: 28.127000 ms
     [  yolov8 postprocess]  loops: 592 avg: 53.173000 ms
     ```

   - 运行 `--dev_id=1` 时，`bm-smi` 的进程列表中仍可能出现 TPU-ID 0 的少量内存占用。这来自底层库枚举/初始化多张设备和 on-chip CPU/usercpu 资源，不代表主推理跑在 0 号卡；实际推理设备以对应卡的 `TPU-Util`、功耗和内存增长为准。
   - 修复前运行过程中出现过以下 BMCV 警告：

     ```text
     [BMCV][error] Error, please check if the handle used for handle and bm_image are the same
     ```

     当时程序仍持续输出 `det_nums` 并最终打印 `SUMMARY`，但后续已通过绑定 `VideoWriter` 的 `dev_id` 修复该告警路径。

## 外部依赖说明

`sophon-demo/` 用作官方参考仓库。本项目默认不将其提交进自身 git 历史。

如果部署环境中没有该目录，可执行：

```bash
git clone --depth 1 -b release https://github.com/sophgo/sophon-demo.git
```
