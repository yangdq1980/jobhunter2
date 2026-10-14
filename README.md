# jobhunter2 · 职位采集→飞书→Ollama评估→看板 自动化流水线

> 在 **Docker n8n** 中编排的一条四步自动化流水线，复用既有环境（n8n / 本地 Ollama / 飞书应用 / MySQL），
> 把「智联招聘按关键词采集 → 写入飞书多维表格 → 本地 LLM 评估并推送飞书群 → 看板统计可视化」串成定时任务。
>
> 项目从第一天起即按 **文档化管理 + GitHub 每日同步** 要求维护（见 `docs/`）。

---

## 一、四步流程

| 步 | 能力 | 关键组件 |
|----|------|----------|
| ① | 智联招聘按关键词采集（复用既有 jobhunter 后端成熟爬虫） | 既有 jobhunter 后端 `POST /crawl/zhilian`（宿主机 :8911） |
| ② | 写入飞书多维表格（控制面板） | n8n → 既有 jobhunter 后端 `POST /sync/feishu/push` 代理（飞书鉴权在后端 `.env`，绕开 21001） |
| ③ | 本地 Ollama(`qwen3:8b`) 综合评估 + 推送飞书群 | n8n Ollama 节点（取 `GET /jobs` 命中职位）+ 飞书 `im/v1`（n8n 直连，仅高/中匹配） |
| ④ | 看板统计与可视化（匹配度/城市/学历/趋势） | `dashboard/` FastAPI + Chart.js（:8100） |

---

## 二、目录结构

```
jobhunter2/
├── docs/                     # 文档化管理（硬性要求）
│   ├── 01-需求分析.md
│   ├── 02-架构设计.md
│   └── dev-log.md            # 各阶段开发调试记录（持续追加）
├── scraper/                  # 第一步：智联爬虫微服务
│   ├── main.py               # FastAPI + Playwright
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── run_host.bat / run_host.sh
│   ├── .env.feishu           # 飞书凭证（gitignore，不入库）
│   └── README.md
├── dashboard/                # 第四步：看板服务
│   ├── app.py                # FastAPI：ingest / stats / jobs
│   ├── jobs.html             # Chart.js 可视化
│   └── Dockerfile            # 基于本地 n8nwp-dashboard:latest
├── n8n-workflows/            # 编排定义
│   ├── build_workflow.py     # 生成工作流 JSON（字典构造，避免手写括号错位）
│   └── job-pipeline.json     # 12 节点 / 12 连接
├── scripts/                  # 运维脚本
│   ├── sync.sh / sync.bat    # 每日同步：提交 + 推送 GitHub
├── .gitignore
└── README.md
```

---

## 三、快速开始

### 1. 启动既有 jobhunter 后端（宿主机，路线 B 抓取源）
```bash
# 最简单：双击 G:/workbuddy/workspace/jobhunterforai/start.cmd
# 或手动起：
cd G:/workbuddy/workspace/jobhunterforai
C:/Users/admin/.workbuddy/binaries/python/envs/default/Scripts/python.exe -m jobhunter.main serve
```
> 后端监听 `0.0.0.0:8911`；n8n 经 `host.docker.internal:8911` 调用 `/crawl/zhilian` 等接口。
> 保持窗口常驻；飞书多维表凭据已在后端 `.env`（`FEISHU_BASE_APP_TOKEN` / `FEISHU_TABLE_ID`），**无需在 n8n 侧填**。

### 2. 启动看板服务（Docker）
```bash
cd dashboard
docker build -t jobhunter2-dashboard:latest .
docker run -d --name jobhunter2-dashboard --network n8nwp_n8nwp-net -p 127.0.0.1:8100:8100 \
  -e MYSQL_HOST=n8nwp-mysql -e MYSQL_USER=root -e MYSQL_PASSWORD=n8nwp_root_2026 -e MYSQL_DB=n8n \
  jobhunter2-dashboard:latest
# 看板页面： http://127.0.0.1:8100/jobs
```

### 3. 导入并激活 n8n 工作流
- 工作流已随项目提供：`n8n-workflows/job-pipeline.json`（也可由 `build_workflow.py` 重新生成）。
- 在 n8n UI（http://127.0.0.1:5678）导入；或容器内 `n8n import:workflow --input=job-pipeline.json`。
- 工作流 id = **`c53e4fc37e6649bd`**（路线 B）。若列表里还有一条同名旧残留 `3fa3c09b6c7c474c`，请手动删除（设置 ⚙ → Delete）。
- 设置 n8n Variables（Settings → Variables）：
  `feishuAppId` / `feishuAppSecret`（已填，供「获取飞书Token」取 tenant_access_token）；
  `feishuGroupChatId`（已填，飞书群 chat_id）。
  ⚠️ **不再需要** `feishuBitableAppToken` / `feishuBitableTableId` —— 飞书多维表写入已改走后端代理 `/sync/feishu/push`，鉴权在后端 `.env`。
- 确认后端（start.cmd）已起后，激活工作流（每日 09:00 定时 + 手动触发）。

---

## 四、每日同步 GitHub

项目要求每天把最新代码与文档推送 GitHub。已提供一键脚本：

```bash
# Linux / macOS
./scripts/sync.sh "可选提交说明"

# Windows
scripts\sync.bat
```

脚本会自动 `git add -A && git commit && git push`。首次使用前需一次性：
1. 在 GitHub 新建仓库（如 `jobhunter2`）；
2. `git remote add origin <你的仓库地址>`；
3. 配置推送凭证（GitHub CLI `gh auth login` 或 SSH key）。

也可启用本项目配套的 **WorkBuddy 每日同步自动化**（见对话中创建的定时任务），由助手每天自动提交并推送。

---

## 五、待办 / 状态

- 🟢 n8n 工作流已导入（路线 B，id `c53e4fc37e6649bd`），当前未激活；启动后端 + 删除旧残留 `3fa3c09b6c7c474c` 后激活即可每日 09:00 跑。
- 🟢 飞书多维表写入已改走后端代理 `/sync/feishu/push`，**无需** `app_token`/`table_id` 等 n8n 变量（凭据在后端 `.env`）。
- 🟢 看板服务已构建并运行（:8100），端到端验证通过。
- 🟢 文档体系（需求/架构/开发日志）已建立并随仓库提交。
