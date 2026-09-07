# 本地压测素材

## 来源与用途

- 原片：Big Buck Bunny，© 2008 Blender Foundation，Creative Commons Attribution 3.0。
- [项目及署名说明](https://peach.blender.org/about/)
- [Blender 官方下载目录](https://download.blender.org/peach/bigbuckbunny_movies/)
- [本次下载压缩包](https://download.blender.org/peach/bigbuckbunny_movies/big_buck_bunny_1080p_h264.mov.zip)
- 下载日期：2026-09-07。
- 本次素材用于公开演示视频的受控解码基线；动画内容不代表客户真实人车检测场景。后续算法验收仍应使用代表性业务视频。

## 原始视频

原片保留在素材准备目录 `素材准备目录中的 big_buck_bunny_1080p_h264.mov`。当前工程 `工程根目录` 已复制下述 25 FPS 测试成品及校验日志；没有重复复制原片或转换工具。

已检查视频头信息并试解码：1920×1080、H.264 Main、24 FPS，容器时长约 9 分 56 秒，文件 725,106,140 字节，视频声明码率约 9.28 Mbps。原片是 24 FPS，不能直接拿它验证每路 25 FPS。

## 25 FPS 派生测试版本

已生成路径：`datasets/stress/bbb_1080p25_h264_8mbps.mp4`。

实际文件大小 596,719,760 字节（约 597 MB）；视频时长 596.44 秒，1920×1080、H.264 High、25 FPS，视频码率约 8.001 Mbps。已在本地使用软件 FFmpeg 全片解码校验，完成 14,911 帧，退出码 0；此校验仅确认素材可解码，不是算力卡性能测试。

SHA256：`2ee4a635717d7349ac611825818fe957cc45233ab8c842db97ba9eb1eaaf7cf0`。

通过帧率转换生成 25 FPS，保留 1080P 分辨率，H.264 High、YUV420P，8 Mbps 目标 CBR、2 秒 GOP，无音频。帧率转换包含重复帧，不是原生 25 FPS 拍摄。编码参数刻意固定，便于复现和对比。

生成命令（FFmpeg 7.1；本地 Windows 使用随 imageio-ffmpeg 安装的工具）：

```bash
ffmpeg -hide_banner -nostdin -n \
  -i datasets/stress/big_buck_bunny_1080p_h264.mov \
  -map 0:v:0 -an -sn -dn -vf fps=25 \
  -c:v libx264 -preset veryfast -pix_fmt yuv420p \
  -profile:v high -level:v 4.1 \
  -b:v 8M -minrate 8M -maxrate 8M -bufsize 16M \
  -g 50 -keyint_min 50 -sc_threshold 0 -x264-params nal-hrd=cbr \
  -movflags +faststart datasets/stress/bbb_1080p25_h264_8mbps.mp4
```

当前工程的 `datasets/stress/` 保存测试成品、全片解码校验日志 `verify.log` 和成品 SHA256；原始转换日志留在素材准备目录。素材目录已被 Git 忽略，交付到 RK3588 时需单独复制视频。具体测试命令见[单卡解码压测](single-card-decode.md)。

## 长测用连续 20 分钟版本

2026-09-07 的 30 路长测在原片首次循环附近遇到 `h264_bm` 送包错误 `ret=-13`，因此另准备连续长文件作对照，避免正式长测期间到达输入文件结尾。原失败记录保留，根因尚未确认。

本地和板端文件：`datasets/stress/bbb_1080p25_h264_8mbps_20min.mp4`。已同步到本地工程 `datasets\stress\`，并核对两端 SHA-256 一致。

通过原 25 FPS 测试视频的压缩码流重复拼接生成，不重新编码：

```bash
ffmpeg -hide_banner -nostdin -n -stream_loop 2 \
  -i datasets/stress/bbb_1080p25_h264_8mbps.mp4 \
  -map 0:v:0 -an -sn -dn -map_metadata -1 -c:v copy \
  -t 1200 -movflags +faststart \
  datasets/stress/bbb_1080p25_h264_8mbps_20min.mp4
```

实际时长 1200.08 秒，30,002 帧，1,201,128,061 字节，H.264、1920×1080、25 FPS。SHA256：`3f1eabc8737274def3bf6a36fa5d524bc391f436a529263bb441c97ba686d092`。已在卡 1 单路硬解检查中解出全部 30,002 帧，跨过文件内的拼接位置；这不等于 30 路长测通过。

15 分钟正式采集加 3 分钟预热时，显式通过 `--input datasets/stress/bbb_1080p25_h264_8mbps_20min.mp4` 选择此文件。它仍是重复的动画内容，不代表 30 路真实摄像头输入。更长的测试应准备足够长的连续输入或独立实时推流，并另行验证循环/重连行为。

## Windows 同步及核对

两份用于实际测试的 MP4 已同时保存在本地工程和设备，2026-09-07 核对大小与 SHA-256 一致。原 24 FPS MOV 仍在前述素材准备目录，不是本轮输入。

```powershell
# 先在 PowerShell 中进入工程根目录
adb -s bf43cc5e0819e5ad pull /home/linaro/1684X-EP-demo/datasets/stress/bbb_1080p25_h264_8mbps_20min.mp4 datasets/stress/
Get-FileHash datasets/stress/bbb_1080p25_h264_8mbps.mp4,datasets/stress/bbb_1080p25_h264_8mbps_20min.mp4 -Algorithm SHA256
adb -s bf43cc5e0819e5ad shell "sha256sum /home/linaro/1684X-EP-demo/datasets/stress/bbb_1080p25_h264_8mbps*.mp4"
```

大文件传输与哈希校验安排在压测开始之前或结束之后。

本轮推理模型也已从设备同步到本地 `third_party/sophon-demo/sample/YOLOv8_plus_det/models/BM1684X/yolov8s_int8_1b.bmodel`，文件 15,632,624 字节，SHA-256 为 `b09b7c5bc0a8c18c67f839c33551ba35fa37748d10eafb6a8f9d41ee87f086b4`，与运行配置记录一致。视频校验清单保存在 `datasets/stress/manifest.json`。

独立 TPU 计算对照使用的 `yolov8s_int8_4b.bmodel` 也已同步到同一模型目录，15,729,392 字节，SHA-256：`1b3ab229fdcd1fd1e5d39b862c30eb0600ebf81ce5fe7c30dba816bc128f1987`。此模型只用于独立 BMRuntime 对照，当前视频 worker 不支持 batch 4。
