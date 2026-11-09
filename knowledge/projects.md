# 作品集项目（作品集首页 http://47.117.29.74/ 的 6 个在线演示 + 本助手）

演示地址只用下面写的 IP 地址（域名 edgebot.vip 没有备案，打不开）。

每个项目：id（工具里用）、演示地址、源码、一句话、要点。细节用 search_docs 查各项目的 README 和文档。

## vision · ai-vision 智能视频分析
- 演示：http://47.117.29.74:8090/ ；源码：https://github.com/rfs151/ai-vision
- 实时视频 AI 告警平台：接入摄像头和直播流，YOLO11 + OpenVINO 检测人和车辆，ByteTrack 跟踪，按区域入侵、越线、逗留、人数超限、摔倒（姿态识别）告警，保存截图和前后 10 秒录像，推送企业微信/钉钉。
- ZLMediaKit 转发，WebRTC / HTTP-fMP4 低延迟播放；Worker 进程内 x264 编码，支持动态关键帧、ROI 编码、静止画面跳过推理（运动矢量），视频实验室可按路开关并和基准流对比画质与 CPU。
- 技术栈：Python、FastAPI、WebSocket、Vue、PyAV/FFmpeg、OpenVINO、ZLMediaKit、Docker。

## qt · 网络拓扑监控上位机（Qt 6 · WebAssembly）
- 演示：http://47.117.29.74:8080/demo/topology/ ；源码：https://github.com/rfs151/ai-qt
- 拓扑图可编辑、可撤销，2 万节点压测流畅。设备数据经自定义帧协议或 Modbus RTU / TCP 在独立线程收发，处理粘包、CRC 错误、断线重连和超时重发，可在页面上注入故障；自绘实时曲线和仪表盘，SQLite 历史回放与 CSV 导出。桌面版和浏览器版是同一套代码。
- 技术栈：C++17、Qt Graphics View、Qt Quick、QThread、Modbus、SQLite、WebAssembly。

## ems · 储能 EMS 边缘网关
- 演示：http://47.117.29.74:8095/ ；源码：https://github.com/rfs151/ai-ems
- 1MW/2MWh 储能站仿真：三家虚构厂商的 PCS/BMS 经 Modbus TCP 接入，C 语言边缘网关完成采集、功率控制和本地存储，MQTT 上云。页面上可下发 mFRR 调度、制造频率扰动、注入故障，实时看 FCR / LFSM-O 响应。
- 技术栈：C99、Modbus TCP、SQLite WAL、MQTT、FastAPI、Vue。

## cam · 工业相机采集
- 演示：http://47.117.29.74:8098/ ；源码：https://github.com/rfs151/ai-cam
- 两台 Aravis 虚拟 GigE 相机接入 C++ 采集引擎：帧池、有界队列、写盘线程，断线后指数退避重连。网页上可以拔网线、设丢包率、让取帧线程或磁盘变慢，丢帧按网络包、拼帧、SDK 缓冲、帧号、队列、文件六层实时计数，录像自动做 CRC 和帧号校验。
- 技术栈：C++17、GigE Vision、Aravis、GenICam、FastAPI、WebSocket、PyQt5。

## gvtest · GVTest 自动化测试平台 · 架构全景
- 演示：http://47.117.29.74:8097/ ；仓库：https://github.com/rfs151/ai-gvtest （只含架构演示页，不含平台源码）
- 对应简历里"自动化测试平台全栈开发"。测试平台和被测的能源交易 / 储能调度系统共 22 个模块，HTTP、SQL、Redis、MQTT、SSH 等 7 种连接分色显示，可逐步回放 8 个场景的请求。
- 技术栈：FastAPI、pytest、Playwright、Dramatiq、MQTT、Redis、InfluxDB、Kafka、Vue 3。

## medlens · MedLens 医疗影像 AI 检测
- 演示：http://47.117.29.74:8010/ ；源码：https://github.com/rfs151/ai-medlens
- 5 类医疗影像（X 光、血细胞、脑 MRI、口腔全景片、结肠镜）各用一个公开预训练模型，支持上传本地文件和 DICOM；血细胞计数可与传统 OpenCV 算法对比；Python 和 C++ 双后端可切换对比延迟；脑 MRI 支持 3D 体渲染和三视图，同一套 C++/OpenGL 代码编译成桌面程序和 WebAssembly。仅供技术演示。
- 技术栈：YOLO11、ONNX Runtime、C++ Drogon、DICOM、OpenGL/WebGL2、WebAssembly。

## agent · 作品集 AI 助手（就是你自己）
- 首页右侧的问答助手；源码：https://github.com/rfs151/ai-agent
- LangGraph 编排多个 Agent：路由 Agent 判断意图，分给问答 Agent（工具调用循环）、JD 匹配工作流（拆解 → 逐条检索证据 → 打分 → 报告）或联系方式；RAG 知识库用 BM25 + 本地向量模型混合检索；SQLite checkpointer 保存会话，长对话自动摘要，记住访客的公司和岗位。SSE 流式输出，页面可展开看执行路径、工具调用、耗时、token 和费用。
- 技术栈：Python、FastAPI、LangGraph、DeepSeek、fastembed、SQLite。
