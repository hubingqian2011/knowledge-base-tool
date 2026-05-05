FROM python:3.11

# 配置镜像源
RUN sed -i 's|http://deb.debian.org|https://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's|http://security.debian.org|https://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources

# 安装系统级依赖
RUN apt-get update && apt-get install -y \
    antiword \
    poppler-utils \
    curl \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ENV PIP_TRUSTED_HOST=mirrors.aliyun.com

WORKDIR /manubot_backend

# 复制依赖文件并安装（build context 是项目根目录）
COPY serving/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制环境变量文件并重命名为.env（Docker构建时使用）
COPY serving/.env.local .env

# 复制 shared 层（跨模块共享配置和 schema）
COPY shared/ ./shared/

# 复制应用代码
COPY serving/api/ ./api/
COPY serving/config/ ./config/
COPY serving/database/ ./database/
COPY serving/script/ ./script/
COPY serving/service/ ./service/
COPY serving/util/ ./util/
COPY serving/tests/ ./tests/
COPY serving/tasks/ ./tasks/
COPY serving/__init__.py .

# 创建日志和临时文件目录
RUN mkdir -p logs temp_files

# 暴露端口
EXPOSE 10090

# 启动命令
CMD ["uvicorn", "script.start_server:app", "--host", "0.0.0.0", "--port", "10090", "--log-level", "debug"]
