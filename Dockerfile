# ai-agent：作品集 AI 助手后端（FastAPI + LangGraph + DeepSeek）
# 构建参数（可选，国内在 .env 里设置）：REGISTRY 镜像仓库，PIP_INDEX pip 源
ARG REGISTRY=docker.io
FROM ${REGISTRY}/library/python:3.12-slim
ARG PIP_INDEX=https://pypi.org/simple
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -i "$PIP_INDEX" -r requirements.txt
COPY app app
COPY knowledge knowledge
# 向量模型（bge-small-zh-v1.5，ONNX，约 91 MB）：先在本机运行 scripts/build_index.py 下载到 models/
COPY models models
ENV PYTHONUNBUFFERED=1 DATA_DIR=/app/data HF_HUB_OFFLINE=1
EXPOSE 8099
HEALTHCHECK --interval=30s --timeout=4s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8099/agent/api/health', timeout=3)"
CMD ["python", "-m", "app.main"]
