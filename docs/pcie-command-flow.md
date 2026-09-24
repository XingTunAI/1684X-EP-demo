# 主控通过 PCIe 调用 VPU、VPP、TPU 的指令流程

更新：2026-09-24。[阅读导航](pcie-reading-guide.md) / [图像和张量的数据量流程](pcie-data-flow.md)。

32路每次调用用了多久、每秒调用多少次，见[调用开销复算与待测项](pcie-command-costs.md)。其中SDK墙钟与驱动等待、PCIe物理传输时间分别说明。

计算单元和图像可以在卡上，但主控仍要提交任务、传递参数、查询状态和等待完成。这里的“指令”指 SDK/驱动提交的任务与控制事务，不是主控逐条执行 TPU 内部的机器指令。

本页按当前应用源码、板端配套驱动 `/opt/sophon/driver-0.5.1`、已安装 SDK 接口及部分库反汇编整理。不是 PCIe 抓包，也没有新增性能实验；不能从静态路径直接算每帧事务数、锁等待占比或纯硬件耗时。读取时运行模块 srcversion 为 `9C14781EC1530C39E702F94`，不以此代替配套源码与运行二进制的完整构建一致性证明。

## 先区分三种动作

| 动作 | 含义 | 与 PCIe 的关系 |
|---|---|---|
| 主控本地动作 | 准备参数、选候选、排队、等待锁、CPU NMS、主控画框 | 动作本身不一定产生 PCIe 事务；等待长不代表一直在传数据 |
| 控制事务 | 提交命令描述符、写寄存器、写消息队列、读状态、接收完成通知 | 当前主控与设备之间需要 PCIe 交互；不同单元路径不同 |
| 数据搬运 | 压缩码流上传、分数/候选/小图回传 | 通过设备传输路径搬运有效数据，涉及 DMA 等机制 |

`ioctl` 本身是主控用户态进入内核的调用；其后驱动执行的 BAR 访问或 DMA 才涉及跨 PCIe 交互。BAR/MMIO 可以理解为“主控通过 PCIe 访问卡上的寄存器或消息区”。

## 1. VPU：视频解码

```text
RK3588：OpenCV VideoCapture / SOPHON FFmpeg 读取、拆分压缩视频
  → 解码 SDK 接收压缩码流（公开接口 bmvpu_dec_decode）
  → [PCIe 数据] 把码流写入设备侧输入缓冲
  → [PCIe 控制] SDK 访问映射的 VPU 寄存器，提交/推进解码
  → 卡上 VPU 读取码流，在卡内生成解码帧
  → 中断/状态通知，驱动唤醒等待者
  → SDK 取得输出帧信息；当前配置将设备侧 YUV 帧交给后续处理
```

已确认的底层能力：

- `VDI_IOCTL_WRITE_VMEM → bmdev_memcpy_s2d(..., true, KERNEL_NOT_USE_IOMMU)`，设备内存上传进入 CDMA 路径。
- VPU 寄存器映射通过 `bm_get_bar_offset/base` 转为 PCIe BAR，控制寄存器访问不等于 CDMA 数据拷贝。
- `VDI_IOCTL_WAIT_INTERRUPT` 等待中断队列，VPU 中断处理设置实例标志并唤醒等待者。
- 公开接口还有 `bmvpu_dec_get_output`、`bmvpu_dec_clear_output`，分别用于取得/归还输出。

边界：没有完整展开 FFmpeg 与 bmvpu 用户态实现，不能断言每次 `VideoCapture` 或 `bmvpu_dec_decode` 调用恰好对应一次 DMA、一个视频帧或一条硬件命令。码流包、解码顺序和输出帧也不必一一对应。不能将内核关闭/恢复流程中的寄存器写示例当作正常逐帧提交的完整证据。

## 2. VPP：推理预处理与预览缩图

```text
RK3588：bmcv_image_vpp_basic / 相应 VPP 接口
  → 驱动 BMDEV_TRIGGER_VPP
  → trigger_vpp → bm1686_trigger_vpp → vpp_handle_setup
  → [主控等待] 取得 vpp_core_sem 名额及 core_id
  → 准备描述符：原图地址、目标地址、尺寸、格式等
  → [主控等待] 命令上传等待同设备 cdma_mutex
  → [PCIe 数据/命令] bmdev_memcpy_s2d_internal 上传描述符
  → [PCIe 控制] VPP 复位、读取空闲及中断状态
  → [PCIe 控制] 设置 VPP_CMD_BASE、VPP_CMD_BASE_EXT、VPP_INT_EN
  → [PCIe 控制] 写 VPP_CONTROL0 启动
  → 卡上 VPP 处理卡内图像
  → 中断处理设置 got_event_vpp，并 wake_up 等待队列
  → 驱动释放 VPP 名额，SDK 调用返回
```

这是目前拆得最完整的路径。驱动初始化两个 VPP 准入名额；**先占名额，再上传命令**。命令上传和常规结果/小图数据拷贝进入同设备的 CDMA 串行路径，可能出现“名额已占用，但处理尚未启动”的窗口。

寄存器写由 `vpp0/1_reg_write → iowrite32(PCIe BAR 地址)` 实现，不能把这些寄存器写都算作 CDMA 传输。硬件工作后，驱动 `wait_event_timeout` 等完成；等待时间并不全部等于 VPP 计算时间。

应用显式 VPP 请求既有推理预处理，又有预览缩图。关闭预览会去掉后者及其小图回传，保留前者。归一化/类型转换另由 `bmcv_image_convert_to` 发起，本页不将其未展开的内部后端硬套成上面的 VPP 路径。

## 3. TPU：主模型推理

```text
RK3588：输入图像已在卡上，准备输入/输出地址及模型执行参数
  → bmrt_launch_tensor_ex（复用输出模式）
  → BM1684X Runtime 后端封装执行任务
  → tpu_kernel_launch_async → bm_send_api → BMDEV_SEND_API
  → 驱动 bmdrv_send_api
  → [主控等待] api_mutex、消息 FIFO 可用空间
  → [PCIe 控制] 写消息区和写指针，提交任务
  → 卡上固件消费任务，推进 TPU 等卡内执行单元
  → 完成消息/中断，驱动更新完成序号并通知等待者
  → 应用 bm_thread_sync 等待本线程已提交任务完成
  → 输出张量留在卡内；之后是否回传由应用决定
```

该提交链由当前静态 BM1684X Runtime 后端的库反汇编和驱动源码共同支持；未展开卡上固件内部排程，也没有动态统计每帧全部内部提交。Runtime 可能包含其他准备和数据搬运，不能把这一条链当作所有内部操作。

TPU 命令路径中可见的是 `api_mutex` 与消息 FIFO，**不能说每条 TPU 提交都先等 VPP 的 `cdma_mutex`**。模型加载/初始化和逐帧执行也不同：当前路径不会每次重新上传整个模型，卡内中间张量不逐层回传主控。

在源码默认的中断同步模式（`SYNC_API_INT_MODE=1`）中，同步函数的等待由驱动完成序号/completion 管理，不代表主控一直通过 PCIe 轮询，也不意味着调用一次 sync 就读取一次完整输出。本轮没有单独核验运行模块的全部编译开关，不能将源码中的这一分支解释成对每次运行行为的动态追踪。

## 4. Score gate、检测结果和预览回传

```text
主模型输出仍在卡上
  → 再提交一次辅助 TPU 模型（score gate）并同步
  → [PCIe 数据] 回读 33,600 B 最大类别分数
  → [RK3588 本地] 筛选候选位置，合并读取区间
  → [PCIe 数据] 按区间读取候选内容
  → [RK3588 本地] 后处理 / NMS
  → 若需要预览：提交同一被检测帧的 VPP 缩图任务
  → [PCIe 数据] 回读 110,592 B 小图
  → [RK3588 本地] 画框、拼屏、HDMI 显示
```

结果拷贝接口为 `bm_memcpy_d2s_partial/partial_offset`；小图为 `bm_image_copy_device_to_host`。底层 `BMDEV_MEMCPY → bmdev_memcpy` 的 D2S 路径采用 CDMA；同设备 `cdma_mutex` 保护传输设置与完成过程。完成检测结果回传并非 TPU 主推理自然附带的动作，而是应用后续明确调用。

同步预览需要当前一路做完上述预览处理后再推进后续帧；其他路仍可并发。图示不是全卡全局串行执行。

## 与 PCIe2/3 性能问题的关系

“计算在卡内”与“控制路径经过 PCIe”可以同时成立。当前同卡变速实验已证明速率影响完整视频墙，源码还确认 VPP 命令与数据拷贝的共享路径，以及 TPU 消息队列等待。但不能据此宣称所有硬件单元都等同一把锁、PCIe 带宽已满，或某一类事务独自造成 TPU 利用率差异。仍需对真实视频的锁等待、寄存器/消息提交和完成等待做同口径测量。

后续[控制访问与驱动等待实测](pcie-dispatch-wait.md)已测得无追踪 GET_REG 连续读取约 PCIe2 2.29 µs、PCIe3 1.70 µs，并找到“VPP 已取得名额、描述符上传仍等 CDMA”的完整函数图案例。32 路数据尚有负载波动与过期丢帧，不能把这个案例当作全部调用的平均占比。

## 可审阅的源码位置

以下驱动行号对应本轮板端源码快照，升级后需重新核对。SDK/驱动原文件未复制到公开仓库。

| 路径 | 核对位置 |
|---|---|
| 应用 VPU 入口 | [main.cpp](../demos/hdmi_wall/main.cpp)：480起，device YUV 输出配置 |
| 应用 VPP / 归一化 | [yolov8_det.cpp](../demos/hdmi_wall/detector/yolov8_det.cpp)：422、446；预览 [main.cpp](../demos/hdmi_wall/main.cpp)：542 |
| 应用 TPU / gate | `yolov8_det.cpp`：243/247，468/477；候选回读259/278 |
| 应用小图回传/主控画框 | `main.cpp`：551/565 |
| VPU SDK 公开接口 | `/opt/sophon/libsophon-0.5.1/include/bm_vpudec_interface.h`：508–512 |
| VPU 内存、寄存器、完成通知 | 驱动 `vpu/vpu.c`：2029/2042（WRITE_VMEM），2648（BAR映射），1604（WAIT_INTERRUPT），1243/1244（通知） |
| VPP 驱动入口 | `bm_fops.c`：225；`vpp/vpp_platform.c` 中的 `trigger_vpp` 映射到1686实现 |
| VPP 上传、启动、准入和完成 | `vpp/bm1686_vpp.c`：332、260、396、215、503；`bm_io.c`：433/443，BAR寄存器写 |
| CDMA 共享路径 | `bm_memcpy.c`：44–46、361、432；`bm1684/bm1684_cdma.c`：73、240，持锁至传输完成 |
| TPU 提交入口及排队 | `bm_fops.c`：722/749；`bm_api.c`：713（send）、819（api_mutex）、900（等FIFO空间）、935（写FIFO） |
| TPU 消息写入与完成 | `bm_msgfifo.c`：208（copy_to_msgfifo）、226–275（写消息和WP）、14/139/146（完成处理）；`bm_io.c`：256/278（BAR写） |
| TPU 同步等待 | `bm_api.c`：991起，1015–1018比较完成序号并等待completion（中断同步模式分支） |

库反汇编核对：`/opt/sophon/libsophon-0.5.1/lib/libbmrt.so` 的 BM1684X `_bmdnn_multi_fullnet_` 在偏移 `0x2c6b0` 调用 `tpu_kernel_launch_async`；`libbmlib.so` 的该函数封装 function id/参数并以 API ID `0x90000003` 调用 `bm_send_api`。`bm_send_api` 的 ioctl 为 `0x40087020`（BMDEV_SEND_API），`bm_thread_sync` 为 `0x40087021`（BMDEV_THREAD_SYNC_API）。这些地址只适用于本轮库版本，不应当作跨版本接口契约。

两库 SHA256：

- libbmrt.so：`cabdab44ba2158549d0e38a49a24f6f16af486ebe44955f35477460bcb2afd99`
- libbmlib.so：`cb9f4618687996124a141c358de441b0d9daebab14549ee5d0c01695cf1939af`
