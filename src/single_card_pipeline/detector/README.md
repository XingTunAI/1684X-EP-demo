# 检测器适配代码

基于 [SOPHON-DEMO YOLOv8 BMCV](https://github.com/sophgo/sophon-demo/tree/485e8a0dd21e3bba6cfa3c4c0241c8e28f76541b/sample/YOLOv8_plus_det/cpp/yolov8_bmcv)，保留原版权声明和本目录 LICENSE。来源为本工程已部署的官方示例适配版本，包含原有运行错误检查。

在项目内保存检测器源码，使分阶段计时和性能优化可随仓库复现；utils.hpp、bm_wrapper.hpp 仍使用第三方示例依赖。

本轮新增输出读取、CPU 后处理和回传排队计时；实验性 transfer-slots 使用每轮结果目录内的文件锁限制同时读取输出的进程数。每路按编号分配到一个槽，同槽读取串行；文件描述符关闭或进程退出时自动释放，不跨测试轮次共享锁。不会修改模型、筛框、NMS 或主动跳帧。
