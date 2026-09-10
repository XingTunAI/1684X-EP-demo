# 输出指标与状态

本页定义各 demo 共用的概念。具体文件树和参数以各 demo 的 README 为准，不同程序的计数位置和计时区间不能直接混用。

## 帧率

HDMI 的[解码观测入口](../demos/hdmi_wall/docs/decoder-observation.md)在抽帧过滤之前统计所有成功解码事件。`summary.json` 的 `total_decoded_fps` / `minimum_stream_decoded_fps` 和 `streams[].decode_observation` 用于比较关闭推理与开启推理的负载；页面的近期滚动解码 / 推理 FPS 独立计数，不能将选中预览画面的采样率当作解码速度。

| 指标 | 定义 | 不能替代的内容 |
|---|---|---|
| 源帧率 | 文件或输入流的标称帧率 | 实际解码或检测完成速度 |
| 解码 FPS | 指定区间内完成解码的帧数除以区间时长 | 模型检测速度 |
| 检测完成 FPS | 指定区间内完成检测流程的帧数除以区间时长 | 模型内部纯计算速度 |
| 总平均 FPS | 同一运行区间内各路完成速率之和 | 最慢一路或最低窗口 |
| 逐路窗口 FPS | 某一路在该实际窗口内的完成帧数增量除以经过时间 | 全程平均 |
| 显示更新率 | 拼屏或播放器发布画面的节奏 | 每路推理速度 |
| 模型迭代速度 | 固定张量上重复执行模型的次数除以时间 | 包含解码、预处理和输出的完整视频链路 |

`decode` 和 YOLOv8 后端的报告使用进度采样及各自的统计区间。YOLO26 与 `hdmi_wall` 都分别记录解码 FPS 和检测完成 FPS：解码按解码完成时刻计数；YOLO26 的检测完成按检测记录写入时刻计数。HDMI 的检测完成计数位于预览处理及可选逐帧记录之后：`full` 模式保留原记录写入位置，`summary` 模式在相同流程位置计数而不写逐帧 JSON。HDMI 的 `completion_accounting_event=after_preview_and_optional_frame_record` 明示这个位置。

两个 YOLO 入口按 `--devices` 为每卡启动独立进程，使用各自的预热和测量区间，不保证多卡同时开始采样。`--duration` 是传给后端的测量窗口，`--warmup` 另行配置。YOLOv8 的预热从 worker 启动时计算，模型初始化可能占用预热，过长时也可能进入测量窗口；YOLO26 等流和模型就绪后再开始预热与测量。

预热区间不计入正式平均；窗口实际时长可能不是整数秒。截止时已开始的工作可能在收尾期间完成，全程计数与正式区间计数因此可能不同。比较结果前先确认同一输入、模型、batch、计数位置和时间范围。不同卡的平均值不能直接当作同一个公共时间区间内的总吞吐。

## 时间字段

| 字段 | 含义 |
|---|---|
| `decode_ms` | 取帧/解码调用区间 |
| `image_bridge_ms` | 图像格式桥接与设备图像准备 |
| `preprocess_ms` | 检测器预处理 |
| `inference_ms` | 程序记录的模型提交、同步等区间 |
| `inference_submit_ms` / `inference_sync_ms` | HDMI 检测器的模型提交调用 / 等待设备同步调用区间 |
| `input_release_ms` | HDMI 检测器单帧推理后的输入释放耗时；使用常驻输入缓冲时为 0，实际释放发生在检测器析构时 |
| `postprocess_ms` | 结果读取与 CPU 后处理等区间 |
| `output_copy_ms` / `output_transfer_ms` | 相应输出读取阶段；HDMI 开启 score gate 时还包含辅助模型执行、候选规划与主机处理，不能全称为纯 DMA 耗时 |
| `transfer_wait_ms` | HDMI 检测器进入输出读取阶段前，等待可选传输锁的时间 |
| `cpu_postprocess_ms` | CPU 候选过滤、NMS 或坐标还原 |
| `service_ms` / `pipeline_ms` | 各 demo 明确记录的一段处理区间 |
| `schedule_lateness_ms` | 相对本地读取计划的落后时间 |
| `frame_age_ms` | HDMI 视频墙中从解码完成至 Detect 完成的时间 |
| `source_age_ms` | HDMI 视频墙中从本地帧计划读取时刻至 Detect 完成的时间；直播为 `null` |
| `drain_seconds` | 规定结束时刻之后的收尾时间 |

不同 demo 的 `service_ms` 边界不完全相同：YOLOv8 从取帧到写出调用返回；YOLO26/视频墙从选定处理时刻到检测完成。以对应代码和文件说明为准。

子项已包含在对应总项中，不要重复累加。P95 表示样本的第 95 百分位，不是摄像头到显示器的延迟。空值表示未记录或不适用，不应当作零。

这些阶段记录的是主机观察到的调用耗时。预处理、模型提交与同步、设备回读以及预览 VPP 调用都可能包含 SDK 队列等待和其他通道造成的资源竞争，不能把耗时全部解释成该算子的纯计算时间。跨路汇总阶段均值时，应按有效帧数加权；各路并行调用的耗时不能直接相加作为整机经过时间。

## 记录与状态

`detections.jsonl` 是逐行 JSON，不是一个整体数组。一般每行对应一帧，检测为空时也应有帧记录；`xyxy` 坐标以原图像素表示。`class_id` 是模型类别索引，需使用匹配的名称文件。

| 状态或字段 | 含义 |
|---|---|
| `measured` | 完成规定记录，未自动承诺某个业务帧率 |
| `completed` | 外层启动器完成计划；两个 YOLO 主入口还要求所有指定卡正常退出 |
| `timed_out` | 超出外层保护时间，触发子进程清理 |
| `interrupted` / `cancelled` | 用户中断，或因同次运行其他卡失败而取消 |
| `frame_limit` | 达到显式帧数上限，适用于 YOLO26 |
| `source_ended` | 按策略遇到输入结束 |
| `incomplete` / `incomplete_duration` | 中断或没有完成规定时长 |
| `failed` / `error` | 运行异常，需要检查原因字段及日志 |
| `records_complete` / 输出核验 | 应用记录连续性或已编码文件完整性检查 |
| HDMI `accounting_complete` | 已解码帧能否由完成帧与主动丢帧解释，并且逐路无错误；不依赖是否输出逐帧 JSON |
| HDMI `incomplete_records` / `incomplete_accounting` | `full` / `summary` 模式的帧核算未完成，需查逐路计数和错误 |

两个 YOLO 主入口的根级 `summary.json` 只报告进程完成情况，不计算跨卡总 FPS，也不应用性能门槛；测量数据位于各 `device_<id>/` 中。仅在显式设置了判定条件时，其他相关入口才按帧率、计划落后等配置给出通过/未通过状态。进程退出码、运行状态和逐路错误应一起查看。

## 结果解释

除 HDMI 视频墙的 `--policy latest` 外，当前视频入口仍按各自原有策略处理帧，不能把低完成 FPS 自动解释成“实时选取最新帧”。改变模型输入大小也不会自动降低原视频的解码工作量。

应用层的丢帧计数只覆盖程序主动丢弃的帧，不能判断相机、网络或解码器内部是否丢帧。重复读取同一文件也不等于相同数量的独立实时输入。真实端到端延迟需要额外的源时间戳及显示端测量。

`bm-smi` 的 TPU 利用率不是 VPU 解码利用率。进程数、显存或瞬时利用率不能单独证明实际处理能力。固定输入张量首末结果一致、JSON 连续或输出视频完整，都不能代替检测准确率评估。

## HDMI 视频墙的抽帧与年龄

`demos/hdmi_wall` 默认 `--policy latest`：每路独立持续解码，一个等待槽仅保留最新帧，检测线程空闲并达到 `--infer-fps` 间隔后再取帧。`--infer-fps` 是每路检测启动速率上限，`0` 表示按处理能力运行；它既不是源 FPS，也不承诺检测完成 FPS。`--policy all` 保留原来的串行逐帧逻辑，且要求检测限频和最大帧年龄参数都为 0。

本地文件按源 FPS 安排读帧；RTSP 不另加软件节流。`latest` 不省去中间帧的解码，不能降低 VPU 解码工作量，也无法清除相机、网络及 RTSP 后端内部的缓冲。

| 每路计数字段 | 定义 |
|---|---|
| `decoded` / `completed` | 全程成功解码 / 到达预览及可选逐帧记录之后完成计数点的帧数，包含预热和收尾 |
| `decoded_measured` / `completed_measured` | 正式区间内分别按解码完成 / 上述完成计数时刻计数，用于各自的 FPS |
| `dropped_overwrite` | 新帧覆盖等待槽中尚未处理的旧帧 |
| `dropped_stale` | 解码后发布或取帧时，年龄超过启用的 `--max-frame-age-ms` 门槛而丢弃；本地按计划读取时刻起算，RTSP 按解码完成起算 |
| `dropped_shutdown` | 结束或停止时放弃的待检测帧 |
| `policy_drops` | 上述三类主动丢帧之和，属于全程计数 |
| `unprocessed_decoded_frames` | `decoded - completed - policy_drops`；正常收尾应为 0 |
| `queue_high_watermark` | 等待槽内帧数的最大值；`latest` 不超过 1，不包含正在检测的帧 |
| `decoder_drops` | `null`，表示未测量解码器内部丢帧，不能解释成 0 |

根级 `application_drops` 为各路 `policy_drops` 之和。`accounting_complete` 检查每路无错误且 `decoded = completed + policy_drops`；有主动丢帧仍可为 `true`。`full` 模式的 `records_complete` 还要求 `records_written = completed`；`summary` 不写逐帧记录，`records_complete` 为 `null`，应查看 `accounting_complete`。两项都不表示源视频的每一帧接受了检测，也不代表达到实时性能要求。

启用 `full` 记录时，`detections.jsonl` 只记录实际完成检测的帧。`processed_index` 连续递增，用于已处理记录的连续性核验；`frame` 是跨本地循环的解码序号，`source_frame_id` 是当前循环内的源帧号，`source_loop` 标记循环次数。`latest` 下这些源帧序号可以跳跃，不能按 `frame` 必须连续来判断记录损坏。

| 年龄或耗时 | 计时边界与解释 |
|---|---|
| `queue_age_ms` | 解码完成到选取送检，反映应用等待时间 |
| `frame_age_ms` | 解码完成到 Detect 返回，包含等待、图像桥接及检测 |
| `source_age_ms` | 本地帧计划读取时刻到 Detect 返回，包含源调度落后；直播为 `null` |
| `schedule_lateness_ms` | 本地计划读取到实际开始读取的非负落后量；直播为 `null` |
| `pipeline_ms` / `pipeline_to_record_ms` | `full` 模式逐帧记录中从开始解码到 Detect 返回 / summary 中从开始解码到记录写入后计数点；`summary` 模式的 `pipeline_to_record_ms` 为 `null` |
| `pipeline_to_completion_accounting_ms` | 两种模式通用：从开始解码到预览及可选逐帧记录之后的完成计数点 |
| 预览 `age_seconds` | 从最近预览更新时间计算，反映屏上内容多久未更新 |
| 预览 `frame_age_ms` / `source_age_ms` | 与检测记录的起点一致，终点延续到当前预览时刻，包含预览准备与画面停留时间 |
| 预览 `stale` | 本地按 `source_age_ms`、RTSP 按 `frame_age_ms` 判断，超过 2 秒为 `true` |

`full` 模式在检测记录中给出每帧年龄；两种模式的逐路 summary 都按正式区间内的完成计数点选取已完成帧，未处理的丢帧不会进入年龄分布。无适用样本时按空统计解释，直播的 `source_age_ms` 不应当作零延迟。P95 只描述完成帧年龄，不能代替首帧等待或无结果间隔检查。

`--max-frame-age-ms` 在 `latest` 下默认 250 ms，`0` 关闭。本地从帧的计划读取时刻起算，RTSP 从解码完成起算；解码后发布和取帧时均检查。随后仍需完成图像桥接与检测，因此检测完成时的对应年龄可以超过门槛。若本地解码持续落后，可能大量丢弃超龄帧，关闭门槛后 `source_age_ms` 仍可持续增长。若 RTSP 内部已缓存旧帧，较低的 `frame_age_ms` 也不能证明相机到 HDMI 的低延迟。抽帧策略已于 2026-09-09 在本地视频输入下完成[上板验证](../demos/hdmi_wall/docs/realtime-board-validation.md)，推荐配置与对照命令见 [HDMI 视频墙](../demos/hdmi_wall/README.md#抽帧与实时处理)。

## HDMI 长时汇总与分位数

`--record-mode full|summary` 由 `config.json` 和 `summary.json` 的 `record_mode` 记录，`config.json.frame_records_enabled` 表示是否启用逐帧文件。普通 HDMI 入口默认 `full`；[四小时展示入口](../demos/hdmi_wall/docs/showcase.md)固定使用 `summary`。两者都执行检测、同帧画框和预览，区别在记录和统计存储方式，不应把省去记录开销前后的性能变化归因于模型本身。

| 内容 | `full` | `summary` |
|---|---|---|
| 逐路 `detections.jsonl` | 写每帧检测记录，空检测同样写入 | 不创建，也不构造逐帧检测 JSON |
| `records_written` / `records_written_measured` | 全程 / 正式区间成功插入记录的帧数 | 均为 0 |
| `records_complete` | 帧核算完整且写入记录数等于完成数 | `null`，不是记录损坏 |
| `accounting_complete` | 按全部计数及逐路错误检查 | 同左 |
| `summary.json`、逐路 summary、`streams.csv`、窗口计数 | 保留 | 保留 |
| 每项 Metric 的分位数样本 | 保留全部观测 | 最多保留 4,096 个样本 |

Metric 的 `count`、`sum`、`mean`、`max` 始终覆盖该指标的全部有效观测，`mean=sum/count`。`summary` 使用确定性 Algorithm R 蓄水池抽样保存最多 4,096 个值；分位数为保留样本排序后的 nearest-rank，即索引 `ceil(q × sample_size) - 1`。它不是只留最近 4,096 帧，也没有承诺分位数误差上限。`full` 对全部观测采用同样的 nearest-rank 定义。

| Metric 元数据 | 含义 |
|---|---|
| `count` / `sample_size` | 全部有效观测数 / 实际保留的样本数 |
| `sample_capacity` | `summary` 为 4096；`full` 为 `null`，表示不限定保留数量 |
| `percentile_method` | `reservoir_algorithm_r_nearest_rank` 或 `exact_nearest_rank` |
| `percentiles_approximate` | `count > sample_size` 时为 `true`；未超过容量则为 `false` |
| `mean` / `p50` / `p95` / `p99` / `max` | 没有观测时为 `null`，不是 0 |

`config.json` 与根级 `summary.json` 的 `metric_statistics` 同时注明 `count_sum_mean_max=all_observations`、分位数算法和 `sample_capacity_per_metric`。年龄和各阶段耗时、score-gate 子阶段都遵循这套规则；帧数、字节数、调用次数与每个时间窗口的计数不由样本估算。Metric 样本内存不随完成帧数继续增长；窗口数组仍按配置的正式时长及窗口大小分配。

比较长测 P95 时应一起给出 `percentiles_approximate` 与样本容量；不能把逐路 P95 取平均称为全卡 P95。全卡 mean 可按每路 `count` 加权或用 `sum` 合并；全卡精确 P95 则需要相同范围的全部原始观测，summary 没有提供这一能力。

## HDMI 检测完成连续性

每路 `analysis_completion_continuity` 单独观察 `Detect` 成功返回时刻，`time_source=analysis_completed_before_preview_and_optional_record`。其样本区间为 `[measurement_start, observed_measurement_end)`，不包含预热，也不等待预览和可选记录写入；计数与最大间隔使用全部事件，不使用分位数抽样。

| 字段 | 定义 |
|---|---|
| `seconds` / `completed` / `no_results` | 实际正式区间时长 / 其中检测完成数 / 是否一次结果也没有 |
| `first_completion_wait_s` | 正式开始至第一帧完成的等待；没有结果时为 `null` |
| `first_completion_wait_censored_s` | 没有结果时为已观察的区间时长，表示至少等了这么久；有结果时为 `null` |
| `max_adjacent_completion_gap_s` | 两端都在区间内的相邻完成事件最大间隔；不足两次结果时为 `null` |
| `last_completion_to_end_s` | 最后结果到实际正式结束的间隔；没有结果时为 `null` |
| `max_gap_including_edges_s` | 首帧等待、相邻间隔和末尾间隔的最大值；无结果时为整个区间长度 |

`completed_measured` 使用较晚的完成计数点，正式边界处可能与上述 `completed` 不同。源时钟和区间端点以根级 `source_clock_start_monotonic_s`、`measurement_start_monotonic_s`、`measurement_end_monotonic_s` 及 `observed_measurement_end_monotonic_s` 为准；最后一项反映提前停止时实际观察到的终点。

连续性描述的是检测结果产出，不是页面或 HDMI 刷新。正式起点到首帧的等待需要加上预热，才是相对源时钟启动的首帧时间；若从用户执行命令计算，还要包括模型和解码器初始化。不能只展示完成帧 P95 来隐藏启动等待、长断档或仍在屏上保留的旧画面。

## HDMI 本地解码预取元数据

`--prime-local-decoders off|on` 在普通入口默认关闭，[showcase 两种模式](../demos/hdmi_wall/docs/showcase.md#展示与压测档位)固定开启。`on` 对每个本地文件在全体就绪屏障及共享源时钟启动前取一次首帧，之后由正常取帧路径消费一次；RTSP 不预取。它保留实际的解码前后时间戳，不把早已解码的帧伪装成刚到达，也不在 EOF 循环时重置源时钟。

`config.json.prime_local_decoders` 记录请求值，`sources[].decoder_priming` 对直播写 `not_applicable_rtsp`，本地写请求的 `off` / `on`；`decoder_priming_note` 说明计数。根级 summary 保留 `prime_local_decoders` 与 `decoder_priming_counting`。每路 summary 的 `decoder_priming` 内容为：

| 字段 | 含义 |
|---|---|
| `requested` / `applicability` | 请求的 `off` / `on`；`local_file` 或 `not_applicable_rtsp` |
| `attempted` | 已开始预取调用 |
| `read_returned` | 预取读取调用已返回，不代表取到了有效图像 |
| `succeeded` | 预取取得非空首帧 |
| `consumed` | 正常帧路径已接手该预取帧，随后按原逻辑增加一次解码和序号计数 |
| `before_decode_monotonic_s` / `after_decode_monotonic_s` | 实际预取开始 / 返回时间；未发生相应事件时为 `null` |

预取帧的实际解码完成时间可能早于共享源时钟及正式区间，所以“全程已解码”与“正式已解码”应按各自时间点理解，不能为了匹配完成数补造正式解码事件。较早完成的预取帧还可能经历屏障等待，真实 `frame_age_ms` 会保留这段时间。此选项是否改善某个路数的启动需要独立实测，元数据存在不等于它已通过验证。

## HDMI score gate 与合并回传

`config.json.gate_merge_budget_kib` 对应 `--gate-merge-budget-kib 0..1024`，默认 0；非零需要 `--score-gate on`。这是**每帧允许额外读取的非候选行字节预算**，1 KiB = 1024 字节，不是总输出大小上限或每秒带宽限制。当前布局为 FP32 `[1,8400,84]`，每行 336 字节，maxima 为 `8400 × 4 = 33,600` 字节，全输出为 `8400 × 84 × 4 = 2,822,400` 字节。

辅助模型先计算每行类别最大分数，回读 maxima 后按 `score > conf` 形成原候选范围。合并策略优先覆盖最小行间隔，同样大小按原范围顺序确定；增加连续读取字节以减少小调用。CPU 最终仍只处理原候选：dense 路径将合并带入的间隔行重新清零，selected 路径只遍历原候选行。额外读到的行不扩大候选集合。

`full` 模式逐帧 `score_gate_metrics` 记录本帧数据；各路及 `slots[]` 的 `score_gate_measured` 累计正式**完成计数区间**中的数据。后者的 `frames` 是该区间计入的完成帧数；即使 gate 关闭也会记录整块读取，所以判断是否开启应查配置，不能只看对象或字段是否存在。

| 字段 | 定义 |
|---|---|
| `selected_rows` / `ranges` | maxima 阈值筛选后的原候选行数 / 连续候选范围数 |
| `planned_read_ranges` | 合并后计划读取的范围数；发生整块回退时不等于实际行读取调用数 |
| `row_read_calls` / `copied_rows` | 实际发起的行读取调用数 / 成功回读的行数，包含合并覆盖的非候选间隔 |
| `extra_row_bytes` | 已成功行回读中，超出原候选行的额外字节；逐帧受合并预算约束 |
| `score_read_calls` / `score_bytes` | maxima 回读调用数 / 成功回读的字节数 |
| `row_bytes` | 成功行回读总字节，包含 `extra_row_bytes`；不能再次相加 |
| `full_read_calls` / `full_bytes` | 整块输出读取调用数 / 成功读取字节，包含 gate 关闭或回退路径 |
| `total_d2h_bytes` | `score_bytes + row_bytes + full_bytes`，只包括此结果读取路径 |
| `execute_calls` / `execute_ms` | 辅助模型调用次数 / 从提交开始到同步完成的耗时 |
| `submit_ms` / `sync_ms` | 上述辅助模型提交 / 同步子项，已包含在 `execute_ms` 内 |
| `score_read_ms` / `row_read_ms` / `full_read_ms` | 对应回读 API 的主机墙钟耗时；行读取为本帧全部行调用耗时之和 |
| `host_zero_ms` | dense 候选路径在行读取前清零主机整块输出缓冲的耗时；不等于全部候选规划或间隔清零成本 |
| `fallback` / `fallback_reason` | 逐帧是否整块回退及原因；正式汇总以 `fallback_reasons` 计数 |

调用计数在调用前增加，字节和行数在成功返回后增加；未成功完成的帧通常不会进入逐帧记录或正式完成汇总。这些数值不能当成设备物理总线监控。`slots[].output_copy_bytes` 是检测器生命周期内该结果回读路径的成功累计，包含预热及收尾，范围不同于 `score_gate_measured.total_d2h_bytes`。两者均不包括视频解码、模型输入和预览缩略图回读；预览字节另见下节。

候选过多或异常时保留整块回退：非有限 maxima、原候选超过 8,000 行或 64 个范围、计划行字节加 maxima 达到整输出的 80%，以及回读后原候选行含非有限值。64 范围上限针对原候选，合并没有放宽此限制。回退若发生在 maxima 或候选回读之后，`total_d2h_bytes` 也包含已经完成的这些读取，不能只算最后的整块输出。SDK 调用失败直接报错，不冒充正常筛选回退。

汇总中的调用次数、候选数、字节数是总量，除以同一对象的 `frames` 才是每帧平均；耗时 Metric 的 `mean` 已按其全部观测计算，未执行某阶段的完成帧可能贡献 0。若要统计“每次实际行读取”的均值，需明确分母为 `row_read_calls`，不能混用“每帧”和“每次调用”。`output_copy_ms` 包括辅助执行、同步、回读和相关主机工作，与各子项存在包含关系，不能重复累加或用它直接推导 PCIe 物理带宽。

## HDMI 图像路径与预览耗时

`config.json` 的 `image_path` 记录请求的 `auto`、`yuv` 或 `bgr` 路径；逐帧 `detections.jsonl` 的同名字段记录该帧实际使用的 `yuv` 或 `bgr`。`auto` 在解码图像布局满足要求时直接使用设备 YUV，否则回退到 BGR；显式 `yuv` 遇到不支持的布局会报错。直接 YUV 路径在预处理时完成颜色转换与缩放，检测输入仍使用原始解码图像，不因预览缩略图而降低源分辨率。

| 逐帧字段 | 定义 |
|---|---|
| `image_path` | 本帧实际使用的设备图像路径：`yuv` 或 `bgr` |
| `preprocess_csc` | 传入预处理及预览的 SDK `csc_type_t` 数值；`-1` 为参考 BGR 路径，YUV 路径使用对应色彩转换枚举，或在元数据未明确指定时使用 SDK 默认值 |
| `source_colorspace` / `source_color_range` | 解码 AVFrame 的 FFmpeg 色彩空间 / 范围枚举数值；无 AVFrame 时为 `-1`。枚举内的“未指定”值不等于已经确定色彩空间 |
| `preview_submitted` | 本帧是否完成缩略图准备并提交给视频墙；受每路最多约 10 次/秒的预览节奏限制 |
| `preview_ms` | 从开始准备缩略图到视频墙提交调用返回的总耗时 |
| `preview_vpp_ms` | 在设备上转换、缩放为 256×144 BGR 缩略图的调用耗时 |
| `preview_readback_ms` | 将缩略图像素从设备回读至主机缓冲的调用耗时 |
| `preview_draw_ms` | 主机准备缩略图视图并绘制缩放后的检测框所用时间 |
| `preview_submit_ms` | 提交缩略图和状态到视频墙的调用耗时，包含相应锁等待 |
| `preview_readback_bytes` | 本帧预览像素回读字节数；已提交时为 `256 × 144 × 3 = 110592` 字节，否则为 0，不包含检测结果或其他 PCIe 传输 |

统计预览平均耗时和分位数时，**只选择 `preview_submitted=true` 的记录**。未提交帧的 `preview_ms`、各预览子项和回读字节数为 0，这些零值不能混入“每次预览成本”的平均值；旧记录缺少提交标志时，也不能据此认定预览成本为零。预览子项已包含在 `preview_ms` 内。

预览准备发生在 Detect 完成之后，因此不计入该帧的 `frame_age_ms`、`source_age_ms` 或 `service_ms`，但可能影响检测记录写入时间及下一次检测的启动。`preview_submitted=true` 仅表示视频墙接收了更新，不保证该帧已经被播放器或 HDMI 扫描输出，不能替代屏幕端延时测量。预览 VPP 和回读耗时同样可能包含 SDK 排队等待。

`config.json` 的 `preprocess_buffer=resident_per_detector` 表示每个检测器复用固定网络尺寸的 resize、convert 和输入张量缓冲。预览另行复用每路的缩略图与主机像素缓冲；首次初始化成本与稳定运行成本应区分，并排除预热后再比较。

`summary` 模式没有逐帧预览记录，但逐路 summary 的 `previews_submitted`、`previews_submitted_measured` 分别保留全程和正式完成计数区间内的提交次数，`preview_readback_bytes_measured = previews_submitted_measured × 110592`。各 `preview_*_ms` Metric 已只纳入正式完成帧中实际提交预览的帧，`preview_metric_population=measured_completion_accounting_events_with_preview_submitted` 明示这一集合；分位数是否近似仍按该 Metric 的元数据判断。
