# ADB / SSH 连接与 HDMI 显示

[仓库首页](../../../README.md) / [HDMI 文档导航](../README.md#文档导航) / [实测索引](results.md)


电脑端的 `adb shell`、`ssh` 连接步骤见[进入设备](../../../docs/setup.md#从电脑进入设备)。下面的命令均在进入设备后的 Linux 终端执行，示例仓库位于 `/userdata/1684X-EP-demo`，且已完成资源准备和编译。

连接方式和显示目标是两回事。启动器优先继承当前终端的 `DISPLAY`；SSH 的 `localhost:10.0`、`localhost:11.0` 等地址会把窗口送到电脑端的 X11 转发服务。板子本地 X11 桌面常为 `:0`，还必须配套使用该桌面的认证文件。仅修改 `DISPLAY` 不能解决认证不匹配。

先检查当前身份、显示设置和本地 Xorg：

```bash
id
printf 'DISPLAY=%s\nXAUTHORITY=%s\nSDL_VIDEODRIVER=%s\n' \
  "$DISPLAY" "$XAUTHORITY" "$SDL_VIDEODRIVER"
ls -l /tmp/.X11-unix/
ps -eo user,args | grep '[X]org'
```

### 板子停在 LightDM 登录界面

2026-09-09 重刷 rootfs 后，本板 Xorg 为 `:0`，启动参数包含 `-auth /var/run/lightdm/root/:0`，HDMI-1 为 1920×1080、60 Hz。此时 `/home/linaro/.Xauthority` 中的认证不能访问这个登录界面；播放器报 `No protocol specified` 和 `Could not initialize SDL - x11 not available`。以下命令适用于已确认相同显示地址、认证路径的系统。

**从 ADB 进入，且 `id` 显示当前为 root：**

```bash
cd /userdata/1684X-EP-demo
env DISPLAY=:0 \
  XAUTHORITY=/var/run/lightdm/root/:0 \
  SDL_VIDEODRIVER=x11 \
  PLAYER_LIB_PATH=/usr/lib/aarch64-linux-gnu \
  bash demos/hdmi_wall/run.sh \
  --streams 1 --device 1 --model s --score-gate off \
  --duration 60 \
  --policy latest --infer-fps 5 --max-frame-age-ms 250
```

**从 SSH 以 linaro 等普通用户进入：** 使用 `sudo env`，以有权读取上述 root 认证文件的身份启动，并显式传入显示设置。

```bash
cd /userdata/1684X-EP-demo
sudo env DISPLAY=:0 \
  XAUTHORITY=/var/run/lightdm/root/:0 \
  SDL_VIDEODRIVER=x11 \
  PLAYER_LIB_PATH=/usr/lib/aarch64-linux-gnu \
  bash demos/hdmi_wall/run.sh \
  --streams 1 --device 1 --model s --score-gate off \
  --duration 60 \
  --policy latest --infer-fps 5 --max-frame-age-ms 250
```

ADB 若以普通用户进入，同样使用第二条命令；SSH 若已是 root，使用第一条。`--device 1` 是本次实测设备，其他机器先用 `bm-smi --noloop` 确认编号。上面的环境变量仅作用于这次启动。提前停止时，ADB root 使用 `bash demos/hdmi_wall/run.sh --stop`，普通用户结束以 sudo 启动的运行时使用 `sudo bash demos/hdmi_wall/run.sh --stop`。

上述显示配置已通过 ADB root 复跑验证：正式运行 30 秒，单路 YOLOv8s、score gate 关闭，检测完成 5 FPS；从板子 `:0` 桌面采集的截图确认了视频和检测框，结束后检测程序和播放器均退出。记录位于板端 `data/results/hdmi-wall/20260909T035307Z-df79fc6f/`，属于本地输出，不随 Git 下载。

### 已登录用户桌面或使用其他显示管理器

在板子的图形桌面终端检查该会话实际使用的 `DISPLAY` 和 `XAUTHORITY`，使用有权访问此会话的用户启动。SSH 进入后需要使用本地桌面的值；SSH 的 `~/.Xauthority` 文件存在，并不说明它包含本地桌面的有效认证。其他显示管理器或 Wayland 会话需按实际图形环境配置，不能直接套用上述 LightDM 路径。

### 推理正常但 HDMI 没画面

控制台的 `HDMI wall is running` 是启动提示；检测达到 5 FPS 也不能单独证明 HDMI 已显示。先查看本次输出目录的 `player.log`，并核对 `run.json` 中的 `player_environment`：

| 现象 | 处理 |
|---|---|
| `DISPLAY` 为 `localhost:10.0` 等 SSH 转发地址 | 改用已确认的板子本地显示地址，并选择匹配的认证文件 |
| 已是 `:0`，但报 `No protocol specified` / SDL x11 初始化失败 | 检查实际桌面会话和认证文件的访问权限；当前 LightDM 登录界面可按上面的命令启动 |
| 显示器提示无信号 | 检查连接线、输入源及板端显示输出；有权限连接桌面后用 `xrandr --current` 检查 HDMI 是否 connected 且启用分辨率 |


## 启动排查

| 现象 | 检查位置与处理 |
|---|---|
| 提示 `Required file is missing` | 缺少 `hdmi_wall.pcie` 时重新构建；缺少 `/usr/bin/ffplay` 时准备系统播放器；视频、类别或模型缺失时按[获取说明](../../../data/README.md#官方资源)补齐 |
| 检测已启动但 HDMI 没有画面 | 查看当前运行目录的 `player.log`、`run.json` 中的显示环境，按 [ADB / SSH 显示说明](display.md)区分 SSH 转发和本地桌面认证 |
| 提示已有 HDMI 运行或锁被占用 | 用当前入口的 `--stop` 结束对应运行，再重新启动 |
| 等待 FIFO 超时 | 查看 `worker.log`；若尚未创建运行目录，启动日志位置会打印到终端 |


多设备入口的播放器日志为根目录 `viewer.log`，各卡错误查看 `device_<id>/worker.log`；单设备入口为 `player.log` 和 `worker.log`。完整输出说明见 [单设备](single-device.md#输出)和 [多设备](multi-device.md#输出与后台处理)。
