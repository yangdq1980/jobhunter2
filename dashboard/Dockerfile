# 职位采集流水线 · 看板服务镜像
# 基于本地已存在的 n8nwp-dashboard:latest（已含 fastapi/pymysql/uvicorn/requests），
# 仅覆盖 app.py 与 jobs.html，无需联网拉取基础镜像。
FROM n8nwp-dashboard:latest

WORKDIR /app

COPY app.py /app/app.py
COPY jobs.html /app/jobs.html

EXPOSE 8100

# 复用基础镜像的 uvicorn，仅改端口为 8100
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port 8100"]
