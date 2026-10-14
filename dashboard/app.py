# -*- coding: utf-8 -*-
"""
职位采集流水线 · 看板服务 (FastAPI 后端)
========================================
职责：
  1. 接收 n8n「落看板数据」节点 POST 过来的职位评估结果，幂等写入 MySQL(jobs_pipeline 表)；
  2. 提供 /api/jobs/stats 统计与 /api/jobs 列表，供前端可视化；
  3. 渲染 /jobs 可视化看板页面(Chart.js)。

数据库：复用 n8nwp 的 MySQL（容器内部地址 n8nwp-mysql:3306，库 n8n，凭据 root/n8nwp_root_2026）。
网络：与 n8n 同在 n8nwp_n8nwp-net，n8n 通过 http://jobhunter2-dashboard:8100 调用。

镜像基于本地 n8nwp-dashboard:latest（已含 fastapi / pymysql / uvicorn / requests），
本文件与 jobs.html 直接覆盖 /app 同名文件即可，无需重新安装依赖。
"""
import os
import datetime
import pymysql
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

app = FastAPI(title="职位采集流水线 · 看板")

# ---------------- 配置（全部来自环境变量，便于 Docker / 本地切换） ----------------
DB = dict(
    host=os.getenv("MYSQL_HOST", "n8nwp-mysql"),
    port=int(os.getenv("MYSQL_PORT", "3306")),
    user=os.getenv("MYSQL_USER", "root"),
    password=os.getenv("MYSQL_PASSWORD", "n8nwp_root_2026"),
    database=os.getenv("MYSQL_DB", "n8n"),
    charset="utf8mb4",
)


def get_conn():
    return pymysql.connect(
        host=DB["host"], port=DB["port"], user=DB["user"],
        password=DB["password"], database=DB["database"],
        charset=DB["charset"], cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
    )


def query(sql, args=None):
    c = get_conn()
    try:
        with c.cursor() as cur:
            cur.execute(sql, args or ())
            return cur.fetchall()
    finally:
        c.close()


def execute(sql, args=None):
    c = get_conn()
    try:
        with c.cursor() as cur:
            cur.execute(sql, args or ())
        c.commit()
    finally:
        c.close()


# ---------------- 建表（幂等，启动时与每次写入前都尝试） ----------------
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS jobs_pipeline (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    platform      VARCHAR(32),
    title         VARCHAR(255),
    company_name  VARCHAR(255),
    salary_raw    VARCHAR(64),
    city          VARCHAR(64),
    district      VARCHAR(64),
    experience    VARCHAR(64),
    education     VARCHAR(64),
    job_url       TEXT,
    description   TEXT,
    evaluation    TEXT,
    match_level   VARCHAR(16),
    crawled_at    VARCHAR(64),
    evaluated_at  VARCHAR(64),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_job (platform, company_name, title, city)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_table():
    try:
        execute(CREATE_SQL)
    except Exception as e:  # noqa
        app.logger.warning("建表/连接失败(稍后重试): %s", e)


ensure_table()


# ---------------- 健康检查 ----------------
@app.get("/api/health")
def health():
    try:
        query("SELECT 1")
        mysql_ok = True
    except Exception:  # noqa
        mysql_ok = False
    return {"status": "ok", "mysql": mysql_ok}


# ---------------- 接收职位数据（n8n 推送） ----------------
@app.post("/api/jobs/ingest")
async def ingest(req: Request):
    ensure_table()
    body = await req.json()
    items = body if isinstance(body, list) else [body]
    cnt = 0
    for j in items:
        try:
            execute(
                """INSERT INTO jobs_pipeline
                   (platform,title,company_name,salary_raw,city,district,experience,education,
                    job_url,description,evaluation,match_level,crawled_at,evaluated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE
                     salary_raw=VALUES(salary_raw), experience=VALUES(experience),
                     education=VALUES(education), description=VALUES(description),
                     evaluation=VALUES(evaluation), match_level=VALUES(match_level),
                     evaluated_at=VALUES(evaluated_at)""",
                (j.get("platform", "zhilian"), j.get("title"), j.get("company_name"),
                 j.get("salary_raw"), j.get("city"), j.get("district"), j.get("experience"),
                 j.get("education"), j.get("job_url"), j.get("description"), j.get("evaluation"),
                 j.get("match"), j.get("crawled_at"),
                 j.get("evaluated_at") or datetime.datetime.now().isoformat(timespec="seconds")),
            )
            cnt += 1
        except Exception as e:  # noqa
            app.logger.error("写入单条失败: %s | %s", e, j.get("title"))
    return {"ok": True, "inserted": cnt}


# ---------------- 统计 ----------------
@app.get("/api/jobs/stats")
def stats():
    ensure_table()
    by_match = query("SELECT match_level, COUNT(*) c FROM jobs_pipeline GROUP BY match_level")
    by_city = query("SELECT city, COUNT(*) c FROM jobs_pipeline GROUP BY city ORDER BY c DESC")
    by_edu = query("SELECT education, COUNT(*) c FROM jobs_pipeline GROUP BY education ORDER BY c DESC")
    by_company = query(
        "SELECT company_name, COUNT(*) c FROM jobs_pipeline "
        "GROUP BY company_name ORDER BY c DESC LIMIT 15")
    daily = query(
        "SELECT DATE(created_at) d, COUNT(*) c FROM jobs_pipeline "
        "GROUP BY DATE(created_at) ORDER BY d")
    total = query("SELECT COUNT(*) c FROM jobs_pipeline")[0]["c"]
    high = query("SELECT COUNT(*) c FROM jobs_pipeline WHERE match_level='高'")[0]["c"]
    return {"total": total, "high_match": high, "by_match": by_match,
            "by_city": by_city, "by_edu": by_edu, "by_company": by_company, "daily": daily}


# ---------------- 列表 ----------------
@app.get("/api/jobs")
def list_jobs(limit: int = 200, match: str = None):
    ensure_table()
    sql = "SELECT * FROM jobs_pipeline"
    args = []
    if match:
        sql += " WHERE match_level=%s"
        args.append(match)
    sql += " ORDER BY created_at DESC LIMIT %s"
    args.append(limit)
    return {"jobs": query(sql, args)}


# ---------------- 前端页面 ----------------
BASE = os.path.dirname(os.path.abspath(__file__))


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page():
    with open(os.path.join(BASE, "jobs.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())
