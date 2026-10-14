# 智联爬虫微服务镜像（可选：纳入 n8nwp Docker 网络）
FROM python:3.11-slim

WORKDIR /app

# 系统依赖（Playwright chromium 运行所需）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 libatspi2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

COPY main.py .

ENV PORT=7800
EXPOSE 7800
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
