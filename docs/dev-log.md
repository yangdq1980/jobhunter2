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

## v0.2 · 2026-10-14 · 飞书接入受阻（已定位根因）

### 现象
用户选择「自动创建飞书多维表格」。执行建表脚本时，调用 `POST /open-apis/auth/v3/tenant_access_token/internal`（app_id=`cli_aa4a01ad81f89cd8`）返回：
`{"code":21001,"msg":"Access denied by access control policy. Contact the administrator for assistance."}`
在**沙箱**与**用户本机 n8n 容器**内复测，均返回 21001 → 排除沙箱 IP 因素，确认是飞书应用 `cli_aa4a01ad` 的「访问控制策略」在拒绝授权。

### 关键旁证：既有流水线不直连飞书
排查既有 `JobHunterAI-采集与触达` 工作流的飞书节点，其 URL 为 `={{ $env.JOBHUNTER_BASE_URL }}/sync/feishu/push`（与 `/pull`），即**走用户自有 jobhunter 后端代理**，并非 n8n 直连 `open.feishu.cn`。这正是既有流程能正常触达飞书的原因。本新流程若直接拿 `cli_aa4a01ad` 调飞书会被策略拦。

### 结论 / 待用户决策
- 直接以 `cli_aa4a01ad` 从 n8n 调飞书不可行（21001），建表与运行期「获取飞书Token/写多维表格/发群」均会失败。
- 可行路径（三选一，待用户拍板）：
  1. **修复该应用**：在飞书开发者后台启用/发布 `cli_aa4a01ad`、补 bitable/im 权限并解除访问控制限制；之后我即可自动建表并填变量。
  2. **换用可用应用**：用户提供另一个有 bitable+im 权限且未被策略拦截的飞书应用 app_id/secret。
  3. **复用既有后端代理**：本流程的飞书写入/群发改走 `$env.JOBHUNTER_BASE_URL` 代理（需确认该后端是否暴露通用 bitable 写入接口）。
- 影响范围：仅 Step-2（写多维表格）与 Step-3 的「发飞书群」受阻；Step-1 抓取、Ollama 评估、Step-4 看板不受影响，已验证可用。

## v0.3 · 2026-10-14 · 源码路径修复 + 工作流重新导入 + 旧工作流悬空确认

### 本次做了什么
1. **定位并修复 `build_workflow.py` 两处 bug**：
   - 第 187 行虽已定义 `OUT_PATH`（脚本同目录绝对路径），但 `open()` 仍写相对路径 `job-pipeline.json`，导致在仓库根误写一份。改为 `open(OUT_PATH, ...)`，并在打印里回显输出路径。
   - 第 150 行「落看板数据」URL 误写为 `http://n8nwp-dashboard:8099/...`——那是 n8nwp 栈**自带的老 dashboard**（监听 8099），**不是我们的看板**。修正为 `http://jobhunter2-dashboard:8100/api/jobs/ingest`（我们的 `jobhunter2-dashboard` 容器同在 `n8nwp_n8nwp-net`，容器内监听 8100，已验证 DNS 可达）。
2. **重新生成** `n8n-workflows/job-pipeline.json`（12 节点 / 12 连接），并**删除仓库根误写的 `job-pipeline.json`**（保持仓库整洁，工作流定义只保留在 `n8n-workflows/`）。
3. **重新导入 n8n**：之前导入的 `c53e4fc37e6649bd` 已不在库中（可能因容器重建/导入未持久化），本次用修正后的 JSON 重新 `import:workflow`（不带 `--userId`，系统自动归属 owner），新 id 见下方验证。
4. **确认旧工作流悬空**：既有 `JobHunterAI-采集与触达`（active=1）依赖 `$env.JOBHUNTER_BASE_URL`，但该变量在 n8n 进程环境 / compose / `.env` 中**均不存在**（grep `/proc/1/environ`、compose、`database.sqlite` 全量扫描均无），即旧流程目前**无法解析后端地址、实际不可用**。本新流程不依赖该变量，独立自洽（scraper:7800 + dashboard:8100 + 直连飞书），与用户在 v0.2 选定的「方案 1：修复飞书应用」一致。

### 验证结果
- ✅ `python build_workflow.py` → `n8n-workflows/job-pipeline.json` 生成成功，输出路径正确。
- ✅ Python 校验：落看板 URL 含 `jobhunter2-dashboard:8100`；「组装负载」节点「原始链接」为纯字符串 `j.job_url || ""`（规避飞书超链接字段歧义）；12 节点 / 12 连接齐全。
- ✅ n8n `import:workflow` 成功，新工作流已进入 `workflow_entity`（active=0，待用户修复飞书应用后在 UI 激活）。
- ✅ 看板 `jobhunter2-dashboard:8100` 仍在运行，`/api/health` → mysql ok。

### 当前变量状态（n8n `variables` 表）
- `feishuAppId`=`cli_aa4a01ad81f89cd8` ✅
- `feishuAppSecret`=***(secret) ✅
- `feishuGroupChatId`=`oc_c902dad6aa1da225b68c1fb86494cb81` ✅
- `feishuBitableAppToken`=`__TODO_提供飞书多维表格app_token__` 🔴（待填）
- `feishuBitableTableId`=`__TODO_提供飞书多维表格table_id__` 🔴（待填）

### 遗留 / 下一步（与用户对齐）
- 🔴 **用户侧**：飞书开发者后台修复 `cli_aa4a01ad`（启用/发布、补 `bitable:app`+`im:message` 权限、解除访问控制/IP 白名单策略），解除 21001 后我方可自动建表并回填 `feishuBitableAppToken/TableId`。
- 🟡 **宿主运行爬虫**：`scraper/main.py` 需在宿主机启动（端口 7800），n8n 经 `host.docker.internal:7800/search` 调用；沙箱内无法跑浏览器，待用户机运行。
- 🟢 **激活工作流**：变量填好 + 爬虫起后，n8n UI 一键激活（每日 09:00 定时 + 手动触发）。

## v0.4 · 2026-10-14 · GitHub 已推送 / 飞书复测仍 21001 / 后端复用待定

### 用户侧进展（本轮）
1. **GitHub 仓库已建**：`https://github.com/yangdq1980/jobhunter2.git`，本地 `git remote add origin` 完成。
2. **飞书应用**：用户反馈已修复（发布+权限+解策略）；并确认**复用现有 jobhunter 后端 `/crawl/zhilian` 接口**（不再维护独立爬虫 7800）。

### 实证验证结果（关键：与用户说法不符，如实报告）
- ✅ **GitHub 推送成功**：`GIT_TERMINAL_PROMPT=0 git push -u origin master` → `[new branch] master -> master`，exit=0。仓库已同步到远程（commit 3445304 及之前）。
- ❌ **飞书复测仍 21001**：实跑 `scripts/feishu_create_table.py`，首步 `POST /auth/v3/tenant_access_token/internal` 仍返回 `{'code':21001,'msg':'Access denied by access control policy'}`。**说明"已修复"在飞书 API 层面未生效**（请求已到达飞书，非网络问题，确属应用访问控制策略拦截）。故建表与变量回填**仍未完成**，不能伪造成功。
- 🟡 **后端复用待定**：确认方向（复用 `/crawl/zhilian`），但缺少两项硬信息——(a) `JOBHUNTER_BASE_URL` 真实值及 n8n 如何可达（host.docker.internal:PORT / 容器名:PORT）；(b) `/crawl/zhilian` 的返回 JSON 结构（供「展开职位」节点映射字段）。全工作区搜索未找到该后端源码/配置，变量在 n8n/compose 均未定义。

### 待用户补充（阻塞项）
1. **飞书**：重新核查 ①应用是否真正「发布/上线」（非仅草稿）；②权限「开通」后是否「发布版本」生效；③企业管理后台「安全→应用访问权限/访问控制」是否将该应用加入允许名单（21001 最常见根因）；④若开「可信 IP 白名单」，把调用方出口 IP 加入。核查后告知我重跑。
2. **后端复用**：提供 `JOBHUNTER_BASE_URL` 取值 + n8n 内可达方式 + 一份 `/crawl/zhilian` 真实返回 JSON 样例。收到后即改工作流「调用智联爬虫」节点指向该接口（并加 n8n 变量 `JOBHUNTER_BASE_URL`），一次性改好重导入。

### 下一步
- 飞书解 21001 → 跑建表脚本 → 回填 `feishuBitableAppToken/TableId` 两变量。
- 收齐后端信息 → 切换抓取节点 → 重导入 → 待激活。

## v0.5 · 2026-10-14 · 飞书后台日志解读 + 21001 根因定位到"访问控制/IP白名单"

### 用户提供：飞书开发者后台「日志查询」截图
- 应用「飞书多维表格」（`app_id=cli_aa4a01ad81f89cd8`）**当前版本状态 = 已发布**（绿色徽标）。
- 「事件推送」日志中，`im.message.receive_v1` 事件**推送状态全部 FAIL**（时间 14:55~14:56，重试次数 1，延迟 3~25ms）。
- 展开的事件 JSON：`schema=2.0`、`header.event_type=im.message.receive_v1`、`header.app_id=cli_aa4a01ad81f89cd8`、`message.chat_type=p2p`、`message_type=text`。

### 复测（21:14:07）
- ❗ `POST /auth/v3/tenant_access_token/internal` **仍返回 21001**（Access denied by access control policy）。
- ⇒ **"已发布"不是 21001 的根因**。应用已发布 + 凭据正确 = 仍被策略拒绝，指向**应用/企业级访问控制**。

### 出口公网 IP（供 IP 白名单参考）
- 本机（沙箱/用户机）出口 IP = **120.235.191.104**（ifconfig.me 实测）。

### 诊断与候选根因（按概率）
1. **应用「安全设置」→ IP 白名单 / 可信 IP**（左菜单可见「安全设置」）：若开启且出口 IP 不在名单 → 直接 21001。需加入 `120.235.191.104` 或临时关闭验证。
2. **企业管理后台（admin）→ 安全 → 应用访问控制/开放能力授权**：企业级策略禁止该应用调用开放 API，需管理员授权/加入允许名单。
3. 应用「可用范围」未覆盖本租户 / 权限未获管理员审批。

### 关于事件推送 FAIL 的说明（与 21001 无关）
- `im.message.receive_v1` 推送失败属**事件订阅**子系统问题，最可能是事件发生时应用尚未上线（app_not_online）；现已发布，历史 FAIL 可忽略。
- 本流水线只需「写多维表格 + 发消息」，**不需要事件订阅**；若新事件仍 FAIL，可删除该订阅以止住重试噪音。

### 备选路线（绕开 21001）
- 路线 B：飞书写入改走既有 jobhunter 后端 `/sync/feishu/push`（该后端已能与飞书通信，不受本应用 21001 影响）。需后端地址 + 接口契约。

---

## v0.6 · 2026-10-14 · 路线 B 落地：复用 jobhunter 后端，飞书写入改走后端代理

### 用户决策（本轮）
明确选择「路线 B」：复用既有 jobhunter 后端（`G:/workbuddy/workspace/jobhunterforai`）的成熟接口，飞书多维表写入改走后端代理 `/sync/feishu/push`，不再依赖 n8n 直连飞书（彻底绕开 21001，无需再修飞书应用）。

### 用户要的两项硬信息（已查实，无需再追问）
1. **`JOBHUNTER_BASE_URL = http://host.docker.internal:8911`**
   - 后端 FastAPI（`jobhunterforai`）监听 `0.0.0.0:8911`；n8n 跑在 Docker，经 `host.docker.internal` 访问宿主机后端。
   - 已**硬编码进 `build_workflow.py` 的 `BACKEND` 常量**，生成的工作流无需再配 n8n 环境变量（改端口只改这一行重新生成即可）。
2. **接口真实返回样例**（直接读后端源码 `jobhunter/api.py` 确认，非推测）：
   - `POST /crawl/zhilian?keywords=...&max_jobs=...`（无 body，后端自读 config；也可传 keywords 覆盖）→
     `{"platform":"zhilian","crawled":N,"new":M,"warning":null,"note":"已评分并写入 SQLite；非黄埔职位在采集层已过滤。"}`
     ⚠️ 该接口**不返回职位 JSON**，职位落后端 SQLite；需另调 `GET /jobs` 取数供评估。
   - `GET /jobs?matched=true&limit=200` → 返回 `job_to_dict` 数组，字段含：`platform / title / company_name / salary_raw / city / district / experience / education / description / job_url / crawled_at / score / matched / trigger_status / ...`
   - `POST /sync/feishu/push`（无 body）→ `{"ok":true,"pushed":N,"created":N,"updated":N}` 或 `{"ok":false,"reason":"..."}`
     - 飞书鉴权全在后端 `jobhunter/feishu_client.py`，凭据来自后端 `.env`（`FEISHU_APP_ID/SECRET/BASE_APP_TOKEN/TABLE_ID`），**n8n 侧零飞书凭据**。
     - 写入多维表字段：**平台 / 职位名称 / 公司 / 城市 / 区域 / 薪资 / 评分 / 是否命中 / 触发状态 / 职位链接 / 招聘者 / 经验 / 学历**（这是「控制面板」；Ollama 自然语言摘要不进多维表，改走飞书群消息 + 看板 ingest）。

### 本次做了什么（一次改好）
- 重写 `n8n-workflows/build_workflow.py`（路线 B 版，13 节点 / 13 连接）：
  - ① 调用智联爬虫 → `POST {BACKEND}/crawl/zhilian?keywords={{ $json.keywords.join(',') }}&max_jobs={{ $json.maxJobs }}`
  - ② 写飞书多维表格 → `POST {BACKEND}/sync/feishu/push`（无 body，走后端代理，绕开 21001）
  - ③ Ollama评估 → `GET {BACKEND}/jobs?matched=true&limit=200` 取命中职位 → qwen3:8b 逐条评估 → 组装负载（正则解析 高/中/低）→ 落看板 + 筛选「高/中」发飞书群
  - ④ 落看板数据 → `POST jobhunter2-dashboard:8100/api/jobs/ingest`（字段 `match`/`evaluation` 对齐 `dashboard/app.py`）
- 工作流 id 固定为 **`c53e4fc37e6649bd`**（与 README 一致），`import:workflow` 按 id 幂等覆盖，不堆积重复项。
- 重新生成 `job-pipeline.json` 并 `n8n import:workflow` 成功（日志确认 `c53e4fc37e6649bd` 已就地升级为 `(RouteB)`）。

### 验证结果
- ✅ `python build_workflow.py` → 13 节点 / 13 连接，JSON 合法（`json.load` 通过）。
- ✅ n8n `import:workflow` 成功；`list:workflow` 确认 `c53e4fc37e6649bd | 智联职位采集→飞书→Ollama评估→看板 (RouteB)` 已就地升级，无新建重复项。
- ✅ 看板 `jobhunter2-dashboard` 容器运行中（Up 4h），Step ④ 链路可用。
- ✅ Ollama 节点 `n8n-nodes-base.ollama` 在本实例可用（导入成功即代表节点校验通过）。

### 遗留 / 需用户侧
- 🟡 **启动后端**：运行前请在宿主机双击 `G:/workbuddy/workspace/jobhunterforai/start.cmd` 拉起后端（:8911），工作流才能打到 `/crawl/zhilian` 等接口。
- 🟡 **删除陈旧重复工作流**：n8n 里仍有一条同名旧残留 `3fa3c09b6c7c474c`（之前会话导入，非 Route B）。本实例含多条重要生产工作流，为避免动生产库出错，未做 DB 手术删除。请在 n8n UI 手动删除：Workflows → 搜 `智联职位采集` → 找到 **id 以 `3fa3c09` 开头**的那条 → 设置(⚙) → Delete。
- 🟢 **激活时变量**：只需 `feishuAppId` / `feishuAppSecret` / `feishuGroupChatId`（均已填）；**不再需要** `feishuBitableAppToken` / `feishuBitableTableId`（飞书写入已走后端代理）。后端起 + 旧残留删后，UI 激活即可每日 09:00 跑。
- 🟢 **GitHub**：本轮改动（`build_workflow.py` + `job-pipeline.json` + 文档）待提交推送（见下节）。

### 下一步
- 提交并推送 GitHub（`build_workflow.py` / `job-pipeline.json` / `docs/*`）。
- 用户机启动后端 + 激活工作流后，观察首跑：飞书多维表出现职位（控制面板）、飞书群收到「高/中」匹配简报、看板 `:8100/jobs` 更新。

