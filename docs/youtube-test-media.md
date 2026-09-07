# YouTube 车流演示素材

准备日期：2026-09-07。本页记录新增真实道路视频，用于后续 Demo 演示或与 BBB 动画对照。现有 114.10 FPS 等历史性能结果仍来自 BBB，不是本视频的成绩。当前脚本的默认输入保持 BBB，新素材通过 --input 显式选择。

## 来源与署名

- 标题：Cars On Highway - Free Stock Creative Commons Video。
- 发布频道：Freestocks；上传日期：2018-08-16。
- 原视频：[YouTube 来源](https://www.youtube.com/watch?v=-vLTFQv2_Vo)。
- 频道：[Freestocks](https://www.youtube.com/channel/UCgcmkT6xhHyoNy-yjO7O7MA)。
- 读取到的许可证字段：`Creative Commons Attribution license (reuse allowed)`。
- 视频说明明确允许个人与商业用途。来源、频道、描述和许可证字段已保存于 `datasets/street/highway_source_metadata.json`。

演示或分发派生素材时保留上述标题、作者、来源及许可证信息，并说明已重新编码和重复拼接。不能把素材称为自摄内容或客户摄像头直播。本次读取的许可证字段未给出版本号，未自行补写版本。

画面为道路车辆经过的固定视角，已抽取画面人工检查；它是公开视频片段，不是本次实时录制的摄像头流。另两段人行候选因失焦或近距离腿部特写未选为演示输入。布达佩斯 16 分钟街景仅保存候选元数据，没有作为已确认复用来源下载。

## 工程文件

所有文件位于 `datasets/street/`，素材不提交 Git，来源说明随仓库保存。

| 文件 | 用途 |
|---|---|
| highway_source_-vLTFQv2_Vo.mp4 | 从 YouTube 下载的 1080p25 H.264 视频轨，无音频 |
| highway_1080p25_h264_8mbps.mp4 | 保留分辨率和 25 FPS，重新编码为约 8 Mbps、2 秒 GOP |
| highway_1080p25_h264_8mbps_20min.mp4 | 将上述短片重复拼接为 20 分钟连续文件 |
| highway_preview.jpg | 原始视频第 12 秒的预览画面 |
| highway_source_metadata.json | 下载时的来源、许可证与视频规格 |
| highway_manifest.json | 文件大小、SHA-256、制作命令和软件校验记录 |
| highway_board_probe.json | 板端 ffprobe 的实际视频规格与帧数 |

20 分钟文件不增加场景多样性：它重复相同的短片，连接点内容会跳回开头。这样可以在 3 分钟预热＋15 分钟正式采集期间避开容器 EOF，但不等于已经修复播放器循环路径，也不等于 20 分钟真实监控采集。

## 制作方式

以下在有 FFmpeg 的素材准备环境执行，输入使用前述已下载视频轨。使用 -n 防止覆盖已有文件。

```bash
ffmpeg -hide_banner -nostdin -n \
  -i datasets/street/highway_source_-vLTFQv2_Vo.mp4 \
  -map 0:v:0 -an -sn -dn -c:v libx264 -preset veryfast \
  -pix_fmt yuv420p -profile:v high -level:v 4.1 \
  -b:v 8M -minrate 8M -maxrate 8M -bufsize 16M \
  -g 50 -keyint_min 50 -sc_threshold 0 -x264-params nal-hrd=cbr \
  -movflags +faststart datasets/street/highway_1080p25_h264_8mbps.mp4

ffmpeg -hide_banner -nostdin -n -stream_loop -1 \
  -i datasets/street/highway_1080p25_h264_8mbps.mp4 \
  -map 0:v:0 -an -c:v copy -t 1200 -movflags +faststart \
  datasets/street/highway_1080p25_h264_8mbps_20min.mp4
```

## 手动使用

在设备工程根目录运行。以下是后续复测示例，不表示本轮已经完成对应压力测试。

```bash
# 一路分析检查新素材
bash scripts/run_single_card_analysis_auto.sh --device 1 --steps 1 \
  --input datasets/street/highway_1080p25_h264_8mbps_20min.mp4 \
  --warmup 30 --duration 60 --window 30

# 32 路解码＋推理，同原优化对照的时间参数
bash scripts/run_single_card_analysis_auto.sh --device 1 --steps 32 \
  --input datasets/street/highway_1080p25_h264_8mbps_20min.mp4 \
  --warmup 180 --duration 120 --window 60 --stall-timeout 180

# 纯解码对照
bash scripts/run_single_card_decode_auto.sh --device 1 --steps 32 \
  --input datasets/street/highway_1080p25_h264_8mbps_20min.mp4 \
  --warmup 60 --duration 120 --window 60
```

同卡测试依次执行，比较时保持卡号、模型、图像路径、路数和时间参数一致，并另存测试编号。报告解读见[阅读指南](benchmark-report-guide.md)，原 BBB 素材仍见[素材说明](test-media.md)。

## 文件校验

20 分钟版本已完成本地软件 FFmpeg 全片解码检查，退出码 0、错误日志为空。这确认文件可解码，不是板卡性能成绩。

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| highway_source_-vLTFQv2_Vo.mp4 | 15106235 | `4decaae434bd410e6169b9bea4de44c5d1f689692688962dcf0bbbf52bfffe17` |
| highway_1080p25_h264_8mbps.mp4 | 60213548 | `1d77313d93dea82e3a3fe705858851593d9cfecf4a42281b6ced3c5d0771ec34` |
| highway_1080p25_h264_8mbps_20min.mp4 | 1202805972 | `75d0182f73365277cca26ff0b13ffc884a2940a62a720f17fc39ac3c483b4599` |

## 板端核对结果

三份 MP4 已同步到板端 `datasets/street/`，与本地 SHA-256 全部一致。板端 ffprobe 确认均为 H.264、1920×1080、25/1 FPS。短版视频时长 60.08 秒、1,502 帧；重复长版视频时长 1200.08 秒、30,002 帧，容器时长 1200.16 秒，视频码率约 8.016 Mbps。原始下载文件的码率探测字段可能与容器大小不一致，不把它作为恒定 8 Mbps 输入；对照用重新编码版本。

本轮完成素材筛选、下载、转码、软件全片解码、板端规格与哈希核对，未运行新的 32 路压力测试。
