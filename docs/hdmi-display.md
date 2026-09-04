# HDMI/XFCE 显示说明

RK3588 主机可通过 HDMI 输出显示内容。YOLOv8 推理程序负责检测和画框，HDMI 显示由后续渲染或播放器链路完成。

## 结果视频播放

官方 YOLOv8 C++ 样例默认把检测框画到视频帧，然后写成：

```text
${SOPHON_DEMO_DIR}/sample/YOLOv8_plus_det/cpp/yolov8_bmcv/results/output.mp4
```

单卡推理完成后，可将结果视频播放到 HDMI：

```bash
cd <repo-dir>
export SOPHON_DEMO_DIR="${SOPHON_DEMO_DIR:-$HOME/sophon-demo}"
bash scripts/run_yolov8_cpp_single.sh 1
bash scripts/play_yolov8_hdmi.sh \
  "$SOPHON_DEMO_DIR/sample/YOLOv8_plus_det/cpp/yolov8_bmcv/results/output.mp4"
```

脚本默认使用：

```text
DISPLAY=:0
XAUTHORITY=/var/run/lightdm/root/:0
PLAYER_LIB_PATH=/usr/lib/aarch64-linux-gnu
```

某些系统中 `/usr/bin/ffplay` 和 `/usr/bin/mpv` 可能加载 SOPHON ffmpeg 库。桌面播放时可指定 Ubuntu 系统 ffmpeg 库路径：

```bash
DISPLAY=:0 \
XAUTHORITY=/var/run/lightdm/root/:0 \
LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu \
ffplay -fs -autoexit \
  "$SOPHON_DEMO_DIR/sample/YOLOv8_plus_det/cpp/yolov8_bmcv/results/output.mp4"
```

## 双卡结果显示

双卡脚本按以下规则分配任务：

```text
task0 -> dev_id 1 -> 0001:11:00.0 -> Gen3 x1 -> 主卡
task1 -> dev_id 0 -> 0004:41:00.0 -> Gen2 x1 -> 副卡
```

运行：

```bash
cd <repo-dir>
bash scripts/run_yolov8_cpp_dual.sh
```

结果会在：

```text
${RUN_ROOT}/<run_id>/task0_dev1/results/output.mp4
${RUN_ROOT}/<run_id>/task1_dev0/results/output.mp4
```

播放主卡结果：

```bash
bash scripts/play_yolov8_hdmi.sh \
  "${RUN_ROOT}/<run_id>/task0_dev1/results/output.mp4"
```

播放副卡结果：

```bash
bash scripts/play_yolov8_hdmi.sh \
  "${RUN_ROOT}/<run_id>/task1_dev0/results/output.mp4"
```

## 实时 HDMI 预览

如需边推理边显示，需要改造官方 C++ 样例。`cpp/yolov8_bmcv/main.cpp` 的默认视频流程为：

```text
VideoCapture -> toBMI -> Detect -> draw_result -> VideoWriter
```

本仓库提供 `patches/yolov8_bmcv_hdmi_display.patch` 和 `patches/yolov8_bmcv_hdmi_fifo.patch`，用于增加可选实时显示参数：

```text
--display=1        开启 OpenCV HighGUI/X11 实时显示
--display_wait=1   每帧窗口事件等待时间，单位 ms
--display_width=1280
--display_fifo=<path>
--save_video=0     仅实时显示，不写 output.mp4
```

在 SOPHON OpenCV 未启用 GTK/X11 HighGUI 后端的环境中，推荐使用 FIFO + `ffplay` 方式，而不是 `--display=1`。`scripts/run_yolov8_cpp_hdmi_preview.sh` 会自动创建 FIFO，并启动系统 `ffplay` 将 BGR24 视频帧显示到 HDMI。

当前 RK3588 + XFCE 环境已验证该路线可用：`dev_id=1` 主卡和 `dev_id=0` 副卡均可连续推理并向 HDMI 播放链路输出带框画面。`ffplay` 可能打印 Rockchip GL/Dri2/Dri3 相关警告；只要画面正常输出且 C++ 日志持续出现 `det_nums`，该警告可先作为桌面渲染层兼容性提示处理。

应用补丁并重新编译：

```bash
cd <repo-dir>
export SOPHON_DEMO_DIR="${SOPHON_DEMO_DIR:-$HOME/sophon-demo}"
(
  cd "$SOPHON_DEMO_DIR/sample/YOLOv8_plus_det/cpp/yolov8_bmcv"
  patch -p1 <"<repo-dir>/patches/yolov8_bmcv_hdmi_display.patch"
  patch -p1 <"<repo-dir>/patches/yolov8_bmcv_hdmi_fifo.patch"
)
bash scripts/build_yolov8_cpp.sh "$SOPHON_DEMO_DIR"
```

运行主卡实时预览：

```bash
SOPHON_DEMO_DIR="$SOPHON_DEMO_DIR" \
SAVE_VIDEO=0 \
DISPLAY_SIZE=960x540 \
bash scripts/run_yolov8_cpp_hdmi_preview.sh 1
```

运行副卡实时预览：

```bash
SOPHON_DEMO_DIR="$SOPHON_DEMO_DIR" \
SAVE_VIDEO=0 \
DISPLAY_SIZE=960x540 \
bash scripts/run_yolov8_cpp_hdmi_preview.sh 0
```

在双卡场景中，建议主路画面绑定 `dev_id=1`，副路或轻载画面绑定 `dev_id=0`。如需双窗口实时预览，可分别启动两个进程，并为两路设置不同 FIFO，例如 `/tmp/yolov8_dev1.bgr` 和 `/tmp/yolov8_dev0.bgr`。

本仓库默认保留“生成结果视频 + HDMI 播放”的稳定方式。实时预览适合本地 HDMI 演示；需要更完整的多路 UI 时，可基于 `sophon-demo/application/YOLOv8_multi_QT` 或 `tutorial/yolov8_ffmpeg_encode` 进一步改造。
