# Qt 网络拓扑监控上位机（Qt 6 · WebAssembly）· 项目说明

## 概况
- 个人作品集项目，由我一个人完成。演示（浏览器版）：http://47.117.29.74:8080/demo/topology/ ；源码：https://github.com/rfs151/ai-qt
- 做什么：可编辑的网络拓扑图 + 设备监控上位机：节点拖拽连线、撤销重做、告警仿真、属性面板、鸟瞰图；设备通信（自定义帧协议、Modbus RTU/TCP）、实时曲线、仪表盘、SQLite 历史回放、Qt Quick 监控大屏。桌面版和浏览器版是同一套 C++ 代码。
- 和我工作经历的关系：博立的消防照明疏散系统上位机里用 Qt Graphics View 做过设备拓扑监控和 RS232 串口协议解析，车端运维工具里做过多线程和实时曲线；这个项目把这些能力集中做成一个可在线体验的演示。

## 架构
- 界面线程：Graphics View 拓扑编辑器、属性面板（Model/View）、曲线和仪表盘；Qt Quick 大屏是独立窗口。
- 通信线程 CommWorker：虚拟设备 / TCP / 串口三种链路都是 QIODevice，上层不区分；FrameParser 解帧 → 采样按 33 ms 攒批 → 信号跨线程投递到 TelemetryStore（环形缓冲）。
- 写库线程 HistoryWorker：低优先级线程，QSqlDatabase 连接在该线程内创建和使用。
- 跨平台：只在文件读写处用 #ifdef Q_OS_WASM 区分；WASM 单线程版工作对象留在主线程，接口不变。

## 大规模场景和高频数据（性能）
- 2 万节点保持流畅：BspTreeIndex 场景索引 + 视口裁剪；levelOfDetailFromTransform 三级 LOD，缩小后节点退化为色块，不画文字和图标。
- 局部刷新：一个全局定时器驱动告警闪烁，只 update() 处于告警状态的节点；连线在节点移动时增量更新；鸟瞰图 200 ms 节流刷新。
- 高频遥测不拖垮界面：通信线程按 30 Hz 攒批投递，QueuedConnection 自动跨线程排队。
- 曲线：min/max 降采样（20 万点 → 约 3000 点，峰值不丢）。
- 入库：遥测按"设备 × 1 秒"聚合（CPU/温度取最大值保留峰值，流量取平均），38 台 10 Hz 设备从 380 行/秒降到 38 行/秒；每秒一个事务批量提交；WAL + synchronous=NORMAL；(device_id, ts_ms) 主键 WITHOUT ROWID，区间查询是连续扫描。
- 仪表盘静态部分缓存为 QPixmap，每帧只画指针和读数，指针到位后停掉定时器。

## 难点与解决
- **协议解析健壮性**：帧格式 AA 55 | cmd | seq | len | payload | CRC16；解析器处理半包、粘包、垃圾字节、CRC 错误、离谱长度，单元测试逐字节喂入验证。虚拟设备可注入分包粘包、误码、丢应答、断线。
- **链路状态机**：未连接 → 连接中 → 已连接 → 重连中；3 秒无有效帧判心跳超时，重连间隔 0.5 s 起指数退避到 8 s；写参数按 seq 匹配应答，300 ms 超时重发 2 次（设备端幂等）。告警判定带回差 5，避免阈值附近抖动。
- **Modbus 自己实现编解码**（不依赖 QtSerialBus，浏览器版也能跑），单元测试里和 QtSerialBus 的 QModbusTcpServer/Client 双向互操作验证。RTU 没有长度字段，按功能码推算帧长；从站连续 3 次无应答暂停轮询 5 秒，避免拖慢整条总线；广播地址无应答，改为逐台写并汇总结果。
- **Qt 6.8 在 WebGL 下的问题**：CurveRenderer 首帧前路径属性变化会丢、超过 180° 的单段弧画不出来，改成固定 200×200 坐标系缩放并拆成两段弧；主窗口含 QQuickWidget 时 WASM 版局部刷新持续报 WebGL 纹理错误，所以大屏改成独立的 QQuickView 窗口。
- **自定义 Scene Graph 渲染**：趋势曲线 TrendItem 在 updatePaintNode 里直接生成顶点，折线沿法线挤出成三角形、两侧 1 px 羽化抗锯齿，不依赖 MSAA；无 GPU 时改用 QSGRenderNode + QPainter。
- **WASM 中文显示**：没有系统字体，GB2312 一级常用字 + 源码用到的字做成约 1 MB 的子集字体打包进 qrc。

## 工程与部署
- 多阶段 Dockerfile（emsdk 3.1.56 + Qt 6.8.3 → 编译 → nginx），wasm 预压缩 15 MB → 5.7 MB；本机构建后 docker save | ssh docker load 传到服务器，服务器不需要 Qt 工具链。
- 测试：tst_comm 覆盖 CRC、解析器异常输入、环形缓冲、降采样、跨线程投递、重发超时、断线重连和真实 devsim 进程的 TCP 端到端；tst_history 覆盖聚合、SQL 聚合保峰值、过期清理、跨线程写入后重启可查。qmllint 零警告。
- 技术栈：C++17、Qt 6 Graphics View、Qt Quick/QML、QThread、Modbus、SQLite、WebAssembly、CMake。
