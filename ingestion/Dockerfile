FROM python:3.11

# 配置 Debian 和 PyPI 镜像源，减少服务器构建超时
RUN sed -i 's|http://deb.debian.org|https://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's|http://security.debian.org|https://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources

# 系统依赖：PyMuPDF 需要 libmupdf-dev + 字体支持
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmupdf-dev \
    mupdf-tools \
    libfreetype6-dev \
    libharfbuzz-dev \
    libjpeg-dev \
    libopenjp2-7-dev \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ENV PIP_TRUSTED_HOST=mirrors.aliyun.com
ENV PIP_DEFAULT_TIMEOUT=1000
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先复制依赖文件，利用 Docker layer 缓存
COPY ingestion/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制 shared 层（跨模块共享配置和 schema）
COPY shared/ ./shared/

# 复制应用代码（build context 是项目根目录，所以路径要加 ingestion/ 前缀）
COPY ingestion/ ./

# 创建挂载目录
RUN mkdir -p /app/output /app/input

# 默认命令：显示帮助（实际运行时通过 docker run 覆盖参数）
CMD ["python", "manual_ingest_agent.py", "--help"]
