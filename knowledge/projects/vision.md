# ai-vision 智能视频分析告警平台 · 项目说明

## 概况
- 个人作品集项目，从方案、开发到云上部署由我一个人完成。演示：http://47.117.29.74:8090/ ；源码：https://github.com/rfs151/ai-vision
- 做什么：实时接入摄像头 / 直播流，检测人和车辆，按区域入侵、越线、逗留、人数超限、摔倒（姿态识别）告警，保存截图和前后 10 秒录像，推送企业微信、钉钉。
- 和我工作经历的关系：博立车端运维工具里做过 FFmpeg RTSP 拉流、解码、RTMP 推流和低延迟优化，这个项目把视频接入扩展到 AI 推理、告警和网页播放的完整产品。

## 架构
- 摄像头或推流 → ZLMediaKit（只转封装不转码，任何协议推上去都能用任何协议拉）→ 每路一个 Worker 进程（PyAV 解码 → YOLO11n + OpenVINO 推理 → ByteTrack 跟踪 → 规则 → 画框 → 进程内 libx264 编码）→ 推回 ZLM 的 ai/<流名> → 浏览器用 WebRTC 或 HTTP-fMP4 播放。
- 控制服务 FastAPI：设备管理、规则配置、告警入库、WebSocket 推送；通过 ZLM 的 Hook（on_stream_changed、on_stream_not_found、on_server_started）按需启停 Worker 和拉流。前端 Vue。
- 告警录像：Worker 的同一份编码输出同时推流和写 2 秒一段的 MPEG-TS 切片到内存盘（只保留最近 60 秒，每路约 9 MB）；告警后约 13 秒把覆盖 [告警前 10 秒, 告警后 10 秒] 的切片直接拼成 MP4（-c copy 不重新编码），相近的告警共用一段录像（最长 40 秒）。没有用 ZLM 的持续录制，因为要一直写盘，而且录的是不带框的原始画面。

## 多路并发与性能
- 每路一个独立 Worker 进程，互不影响；一路崩溃由控制服务的监督循环处理。
- 6 路同时跑时推理从 40 ms 变成 380 ms，而 CPU 还有 80% 空闲：OpenVINO 默认把推理线程绑到固定核（ENABLE_CPU_PINNING），6 个进程各自决定，绑到了同样几个核上排队。关掉绑核交给系统调度后降到 62 ms。
- 推理线程数实测：自动 42.9 ms / 158% CPU，2 线程 44.2 ms / 43% CPU，所以默认每路 2 线程——一台机器跑多路时每路少分线程，总吞吐更高。
- 6 路（768x432 到 960x540，每路每秒检测 5 帧）整机 CPU 约 44%，内存约 1.6 GB，估算 i5-13500H 能跑约 10 路；Vultr 8 vCPU 云服务器 6 路 AI 全开整机 CPU 45%，每路约 0.6 核，不需要 GPU。
- 磁盘一直在写：用 /proc/<pid>/io 统计，发现 ZLM 给 12 路流都实时生成 HLS（约 1.2 MB/s），改 hls_demand=1 后降到约 0.3 MB/s。
- 告警推送每条是后台异步任务，不阻塞入库；每渠道每分钟最多 20 条，超出只计数不排队。

## 难点与解决
- **画框总跟在人后面（1.4 秒延迟）**：先用 requestVideoFrameCallback 测得浏览器显示只要约 5 ms，问题在 Worker；逐帧记录"收到数据包 → 解出画面"，发现 PyAV 默认帧级多线程解码要 1388 ms，改成片级（SLICE）后 1.5 ms，AI 画面和告警时间上的延迟一起消除。
- **前端画框按帧对齐**：检测结果带上被检测那一帧的 RTP 时间戳（ZLM 原样转给 RTSP 和 WebRTC 播放器），浏览器用 requestVideoFrameCallback 取屏幕上那一帧的结果；fMP4 时间线不同，用 RTSP 和 fMP4 交替探测配对，偏移稳定到 1 ms。WebRTC 下框和真实位置的平均误差从 4.1 px 降到 1.1 px。
- **重新推流后 Worker 不恢复**：Worker 退出时 OpenVINO 线程 abort 写 core 约 5 秒，Hook 处理同步等待，期间 ZLM 后续 Hook 发送失败、"上线"通知丢了。改成停止不阻塞，并每 3 秒和 ZLM 的 getMediaList 对账（按状态而不是按事件）。
- **WebRTC 卡顿**：RTP 包超过 MTU 被丢（-pkt_size 1200）；Windows 浏览器上关键帧前没有 SPS/PPS（h264_mp4toannexb / dump_extra）。
- **摔倒检测换编号**：倒地瞬间检测断几帧，跟踪器给地上的人分配新编号，丢了"刚才站着"的历史；新出现的人继承 1.5 秒内同一位置消失的人的站立时间。演示视频每轮 9 次摔倒全部报出，3 段日常动作没有误报。
- 一共整理了 16 个问题定位记录，每个都有现象、根因和修复。

## 视频编码优化
- 编码从 ffmpeg 子进程改到 Worker 进程内调 libx264，由程序决定关键帧时机和 ROI 区域；同机 A/B CPU 相差 3% 以内。
- 动态关键帧：间隔 2 秒拉长到 10 秒，有人起播、出现目标、触发告警时立即插入，平均 QP 低约 2。
- ROI 编码：检测框外扩 20% 降 QP，码率相同时目标区域 PSNR 44.9 dB（基准 37.2，+7.8），代价是背景 −7.2 dB。
- 画面静止时跳过推理：用解码时导出的运动矢量判断画面变化面积（每帧 0.06 ms），静止时 Worker CPU 72% → 14%。

## 关键数据
- 单路 YOLO11n OpenVINO FP32 640 输入 37 ms/帧；24 fps 视频边检测边输出 23.9 fps 不掉帧。
- 56 个 pytest 用例：规则、跟踪、编码、录像切片、进程管理、数据保留。
- 技术栈：Python、FastAPI、WebSocket、Vue、PyAV/FFmpeg、OpenVINO、YOLO11、ByteTrack、ZLMediaKit、Docker。
