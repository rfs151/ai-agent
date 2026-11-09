# MedLens 医疗影像 AI 检测 · 项目说明

## 概况
- 个人作品集项目，由我一个人完成，仅供技术演示，不用于临床诊断。演示：http://47.117.29.74:8010/ ；源码：https://github.com/rfs151/ai-medlens
- 做什么：5 类医疗影像（X 光骨折、血细胞计数、脑 MRI 肿瘤、口腔全景片、结肠镜息肉）各用一个公开预训练 YOLO 模型做推理（不需要训练），支持上传本地文件和 DICOM，视频实时检测；血细胞计数可与传统 OpenCV 算法对比；Python 和 C++ 双后端同一套接口；脑 MRI 3D 体渲染。
- 和我工作经历的关系：体现 YOLO 推理部署、C++ 服务化和 OpenGL 渲染能力，对应求职方向里的机器视觉。

## 架构
- 浏览器 → FastAPI → ONNX Runtime → JSON 检测框 → 前端 canvas 画框；另一条路径浏览器 → Drogon（C++）→ OpenCV DNN。两个后端共用 models/<name>.onnx + <name>.json，接口契约一致，检测结果两端完全一致。
- 3D 体渲染：同一套 C++17 编译成桌面 OpenGL 3.3 和网页 WebGL2（wasm），GPU 光线投射 142×178×136 的 MRI 体数据；网页版 JS 58 KB + wasm 62 KB。

## 性能与并发
- 单请求推理（i5-13500H，CPU，12 次中位数）：fracture Python·ONNX Runtime 36 ms、C++·OpenCV DNN 105 ms；dental 37 / 82 ms；polyp 107 / 183 ms。
- 结论：CPU 上 OpenCV DNN 比 ONNX Runtime 慢 2–3 倍，所以 C++ 端的价值不在 OpenCV DNN 本身，而在于提供一个不依赖 Python、可以单独交付的推理服务框架；下一步提速可换 ONNX Runtime C++ API、OpenVINO 或 GPU TensorRT。
- 视频链路：前端降采样后再发，JPEG 质量 0.8，单帧在途、自动跳帧，延迟稳定，不会越来越卡。
- C++ 服务的并发：cv::dnn::Net 不是线程安全的，所以用固定大小的 DetectorPool（多个检测器共享一个模型文件，全部忙时 borrow() 阻塞等待）处理并发请求。压测脚本 benchmark.py 可以对两个后端并发压测（例如 -n 200 -c 4）。设计文档里还规划了"解码 / 推理 / 编码"多线程流水线。

## 难点与解决（模型选型靠实测）
- 息肉：最初选的 REAL-Colon YOLO11m 在示例视频（HyperKvasir）上只有约 1% 的帧能检出，原因是两者来自不同医院、不同内镜设备（domain shift）；换成 Kvasir-SEG 训练的 YOLOv8（和 HyperKvasir 同一家医院）后约 50% 的帧能检出。
- 脑肿瘤：在 27 张肿瘤图和 9 张正常图上实测，阈值 0.25 时正常图误报 5/9，默认阈值调到 0.35（检出 21/27，误报 3/9）。
- 医疗模型换医院、换设备效果可能明显下降，这是真实落地时最需要验证的地方。
- 息肉和脑肿瘤模型只发布了 PyTorch .pt，用 Ultralytics 导出成 ONNX。

## 关键数据与技术栈
- 8 个 pytest 用例，不依赖模型文件即可运行。
- 技术栈：YOLO11 / YOLOv8、ONNX Runtime、OpenCV DNN、C++ Drogon、FastAPI、WebSocket、DICOM、OpenGL / WebGL2、WebAssembly（Emscripten）。
