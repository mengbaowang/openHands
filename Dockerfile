FROM python:3.9-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# 复制应用代码
COPY . .

# 创建数据目录
RUN mkdir -p /app/data

# 暴露端口
EXPOSE 35008

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:35008/api/market/prices', timeout=5)"

# 使用gunicorn运行（生产环境）
# 如果需要开发模式，可以在docker-compose.yml中覆盖CMD
CMD ["gunicorn", "--bind", "0.0.0.0:35008", "--workers", "4", "--timeout", "120", "app:app"]

