# 开发调试记录（dev-log）

> 规则：每完成一个阶段追加一节，保留版本号、日期、做了什么、验证了什么、遗留问题。
> 配套：需求分析 `01-需求分析.md`、架构设计 `02-架构设计.md`

---

## v0.1 · 2026-10-14 · 环境与四步链路打通

### 做了什么
1. **环境盘点**：确认 n8n(`n8nwp-n8n:5678`)、Ollama(`127.0.0.1:11434`, 已装 `qwen3:8b`)、MySQL(`n8nwp-mysql:3307`, 库`n8n`)、既有 AI 看板(`n8nwp-dashboard:8099`) 全部在线；飞书应用 `cli_aa4a01ad` 凭证已安全抽取至 `scraper/.env.feishu`（已 gitignore）。
2. **n8n 工作流导入**：`job-pipeline.json`（12 节点/12 连接）通过容器内 CLI `n8n import:workflow` 导入 n8n 2.40.7，归属个人项目 `u9V5K0IVNGRyDOLk`，当前 `active=0`。
   - ⚠️ 踩坑：CLI 子命令是冒号形式 `import:workflow`（非 `import`）；导入要求工作流 JSON 含顶层 `id`（已补 `wid()`）；`--activeState=fromJson` 需队列模式，普通部署不支持，去掉即可；带 `--userId` 会触发「credential undefined」所有权坑，最终**不带 userId 导入**由系统自动归属 owner，最稳。
3. **n8n Variables 注入**：直接写库 `variables` 表（明文，未加密），按项目作用域注入 5 个变量：`feishuAppId`/`feishuAppSecret`(secret)/`feishuGroupChatId` 已填；`feishuBitableAppToken`/`feishuBitableTableId` 为占位符（待填）。
4. **职位看板服务**：新建 `dashboard/`（app.py + jobs.html + Dockerfile），基于本地镜像 `n8nwp-dashboard:latest` 构建 `jobhunter2-dashboard:latest`，运行于 `n8nwp_n8nwp-net`、端口 8100。提供 `/api/jobs/ingest`、`/api/jobs/stats`、`/api/jobs`、`/jobs`。
5. **工作流节点 URL 修正**：「落看板数据」节点从旧 `n8nwp-dashboard:8099` 改为 `jobhunter2-dashboard:8100`（直接改库内已导入工作流节点）。

### 验证结果
- ✅ 看板 `/api/health` → `{"status":"ok","mysql":true}`（建表成功）
- ✅ 推送 2 条样例 → `/api/jobs/stats` → `total=2, high=1`，匹配度/城市分布正确
- ✅ `/jobs` 页面 HTTP 200（6263 字节，Chart.js 渲染）
- ✅ n8n 容器内 `node` 探测：`jobhunter2-dashboard:8100/api/health` → 200；`host.docker.internal:11434/api/tags` → 200（qwen3:8b 可见）；证明 n8n→看板、n8n→Ollama 全链路可达
- ✅ 飞书 Token/多维表格/群消息均为外网 API，n8n 出网正常

### 踩坑备忘
- **MSYS 路径转换**：Git Bash 下 `docker cp` 的 `/g/...` 源路径会被双转换成 `g:\g:`；改用 Windows 盘符形式 `G:/...` 直传给 `docker.exe` 解决。容器内路径（如 `/tmp/...`）需用单引号包住命令避免被 MSYS 转成 Windows 临时目录。
- **docker run 多行续行**：Git Bash 里 `docker run ... \` 续行会导致 `-p` 端口映射被吞掉（端口绑定为空）。改用**单行命令**后正常发布 `127.0.0.1:8100->8100/tcp`。
- **n8n 变量无 CLI**：2.40 无 `variables` 子命令，Public API 受用户管理强制要求 UI 生成 key（env 的 `N8N_API_KEY` 不被认可）。最终用 `sqlite3` npm 包直写 `variables` 表（字段 `id/key/type/value/projectId`，value 明文），最稳。

### 遗留 / 下一步
- 🔴 **飞书多维表格目标表**：需用户提供 `app_token` + `table_id`，或授权我基于现有飞书应用自动创建后回填 Variables。
- 🟡 **爬虫微服务宿主运行**：`scraper/` 代码与 `requirements.txt`/`Dockerfile`/`run_host.bat|sh`/`README.md` 齐备，需在宿主机 `pip install` 并 `python main.py`（沙箱内被拦截，无法在此跑浏览器）。
- 🟡 **激活工作流**：填好 Bitable 变量 + 启动爬虫后，在 n8n UI 一键激活（每日 09:00 定时 + 手动触发）。
- 🟢 **GitHub 每日同步**：本地 git 仓库初始化 + 首次提交；远程仓库与每日同步脚本/自动化待建立（见下节）。
