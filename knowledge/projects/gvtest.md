# GVTest 自动化测试平台 · 项目说明

## 概况
- 对应我在江苏极光云能做的"自动化测试平台全栈开发"（Python / AI 辅助开发）。平台源码和业务数据不能公开，作品集里放的是交互式架构演示页：http://47.117.29.74:8097/ ；仓库：https://github.com/rfs151/ai-gvtest （只含架构演示页和模拟数据）。
- 平台做什么：全栈自动化测试平台，被测对象是一套能源交易 / 储能调度业务系统；目标是推动自动化测试在软件版本验证中的应用，减少重复性人工测试，提高版本交付质量。
- 我的工作（简历）：基于 Python、MySQL 做平台全栈开发，结合 AI 辅助开发完成业务功能和测试流程；集成 MQTT、Redis、InfluxDB、Kafka 等中间件，实现测试任务处理、消息通信、测试数据采集与存储。

## 平台规模与架构
- 49 个页面、32 个 API 路由、29 个数据模型，7 种协议与中间件。
- 演示页画出 22 个模块（左边测试平台，右边被测环境），HTTP、SQL、Redis、MQTT、SSH、进程内调用、CI 七种连接分色显示。
- 技术栈：FastAPI、Vue 3、Dramatiq + Redis（任务队列）、MySQL、pytest、Playwright、httpx、Allure、paho-mqtt、paramiko、APScheduler、Nacos、InfluxDB、Jenkins、Docker Compose。

## 8 个可回放场景（体现的能力）
- 执行业务组（UI + API + 数据库断言）：队列调度、Playwright、失败截图与重跑、结果实时回写。
- 计划下发与网关 ACK：MQTT 设备模拟。
- 模拟 SOC 上报：定时任务、能量计算、时序数据。
- 远程日志检索：MQTT + 主机 Agent、retained 配置推送。
- 三方接口打桩：SSH 反向隧道、规则匹配 Mock。
- 业务数据查询：MySQL / InfluxDB、连接池。
- 定时回归与 CI：APScheduler、Jenkins、Allure。
- Nacos 配置切换：配置中心 Open API、差异与回滚。

## 演示页实现
- 单个静态 HTML，nginx 提供，带 /healthz 健康检查；数据包沿连线流动，右侧"请求日志"显示 JSON、MQTT 报文、SQL、pytest 输出，支持暂停、单步、0.5–3× 调速。
- 平台里的并发处理、具体实现细节资料里没有展开，可以面谈。
