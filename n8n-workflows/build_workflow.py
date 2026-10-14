"""
生成 n8n 工作流 job-pipeline.json（四步自动化 · 路线 B）
============================================================================
路线 B（复用已有 jobhunter 后端，飞书写入改走后端代理，绕开飞书 21001）：

  ① 抓取      → POST {BACKEND}/crawl/zhilian            （后端成熟接口，职位落后端 SQLite，自带评分）
  ② 飞书多维表 → POST {BACKEND}/sync/feishu/push         （走后端代理，鉴权在后端 .env，不经 n8n）
  ③ Ollama评估 → 本地 qwen3:8b                          （GET /jobs?matched=true 取命中职位）
     发飞书群  → im/v1/messages（n8n 直连，仅高/中匹配）  （后端无发群能力，保留 n8n 直连）
  ④ 看板      → POST jobhunter2-dashboard:8100/api/jobs/ingest

说明：
  - BACKEND 硬编码为 http://host.docker.internal:8911（n8n 跑 Docker、后端跑本机）。
    若后端改端口，只改本文件 BACKEND 常量重新生成即可。
  - 飞书多维表鉴权完全在后端 jobhunter/feishu_client.py，凭据来自后端 .env
    （FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_BASE_APP_TOKEN / FEISHU_TABLE_ID），
    n8n 侧无需任何飞书凭据变量（只需 feishuAppId/feishuAppSecret 给「发飞书群」用）。
  - 后端 /sync/feishu/push 会把 SQLite 全部职位按指纹 upsert 到飞书多维表
    （字段：平台/职位名称/公司/城市/区域/薪资/评分/是否命中/触发状态/职位链接/招聘者/经验/学历），
    作为「控制面板」。Ollama 的自然语言评估摘要不进多维表，改走「飞书群消息 + 看板 ingest」。
============================================================================
"""
import json, uuid, os

# 始终写到本脚本同目录，避免在不同 cwd 下误写到别处
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "job-pipeline.json")

# 后端 API 地址（n8n 容器通过 host.docker.internal 访问本机后端）
BACKEND = "http://host.docker.internal:8911"
# 看板服务（与 n8n 同在 n8nwp_n8nwp-net）
DASHBOARD = "http://jobhunter2-dashboard:8100"

def nid():
    return uuid.uuid4().hex

def wid():
    # n8n 工作流 id 形如 16 位 base62，这里取 uuid 前 16 位保证唯一且合规
    return uuid.uuid4().hex[:16]

# 固定工作流 id：让每次重生成+导入都幂等覆盖同一条（最初创建的 c53e4fc37e6649bd，README 已引用）。
# 这样不会在 n8n 里堆积重复工作流；旧残留 3fa3c09b6c7c474c 由本脚本配套清理步骤处理。
WF_ID = "c53e4fc37e6649bd"

nodes = []
conns = {}

def add(node):
    nodes.append(node)
    conns.setdefault(node["name"], {})
    return node

def http(name, method, url, json_body=None, extra=None, headers=None, send_body=True):
    p = {"method": method, "url": url}
    if send_body and json_body is not None:
        p["sendBody"] = True
        p["specifyBody"] = "json"
        p["jsonBody"] = json_body
    p["options"] = {"response": {"response": {"responseFormat": "json"}}}
    if extra:
        p.update(extra)
    if headers:
        p["headerParameters"] = {"parameters": [{"name": h, "value": v} for h, v in headers.items()]}
    return {"id": nid(), "name": name, "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2, "position": [0, 0], "parameters": p}

# ---------- 触发器 ----------
add({"id": nid(), "name": "手动触发", "type": "n8n-nodes-base.manualTrigger",
     "typeVersion": 1, "position": [0, 400], "parameters": {}})
add({"id": nid(), "name": "每日定时触发", "type": "n8n-nodes-base.scheduleTrigger",
     "typeVersion": 1.3, "position": [0, 560],
     "parameters": {"rule": {"interval": [{"field": "days", "triggerAtHour": 9, "triggerAtMinute": 0}]}}})

# ---------- 配置（keywords / maxJobs，喂给后端 /crawl/zhilian）----------
add({"id": nid(), "name": "配置", "type": "n8n-nodes-base.set", "typeVersion": 3.2,
     "position": [240, 400],
     "parameters": {"mode": "manual",
                    "assignments": {"assignments": [
                        {"id": nid(), "name": "keywords", "type": "array",
                         "value": ["AI解决方案架构师", "云计算架构师", "解决方案架构师", "售前解决方案工程师"]},
                        {"id": nid(), "name": "maxJobs", "type": "number", "value": 50},
                    ]}, "options": {}}})

# ---------- 飞书 token（仅供「发飞书群」用；多维表写走后端代理，不用它）----------
add(http("获取飞书Token", "POST",
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        '{"app_id":"={{ $vars.feishuAppId }}","app_secret":"={{ $vars.feishuAppSecret }}"}',
        extra={"position": [240, 720]}))

# ---------- Step1：调后端 /crawl/zhilian（POST，无 body，关键词走 query）----------
CRAWL_URL = (f"{BACKEND}/crawl/zhilian"
             "?keywords={{ $json.keywords.join(',') }}&max_jobs={{ $json.maxJobs }}")
add(http("调用智联爬虫", "POST", CRAWL_URL, None,
        extra={"position": [480, 400]}, send_body=False))

# ---------- Step2：写飞书多维表格（走后端代理 /sync/feishu/push，POST 无 body）----------
PUSH_URL = f"{BACKEND}/sync/feishu/push"
add(http("写飞书多维表格", "POST", PUSH_URL, None,
        extra={"position": [720, 560]}, send_body=False))

# ---------- 拉取命中职位供 Ollama 评估（GET /jobs?matched=true）----------
JOBS_URL = f"{BACKEND}/jobs?matched=true&limit=200"
add(http("拉取职位", "GET", JOBS_URL, None,
        extra={"position": [720, 400]}, send_body=False))

# ---------- 展开职位列表（兼容 HTTP 直接返回数组 / {jobs:[...]} / 已拆分多项）----------
add({"id": nid(), "name": "展开职位", "type": "n8n-nodes-base.code", "typeVersion": 2,
     "position": [960, 400],
     "parameters": {"mode": "runOnceForAllItems", "jsCode": """
const out = [];
for (const item of $input.all()) {
  const d = item.json;
  if (Array.isArray(d)) { for (const j of d) out.push({ json: j }); }
  else if (Array.isArray(d.jobs)) { for (const j of d.jobs) out.push({ json: j }); }
  else if (Array.isArray(d.data)) { for (const j of d.data) out.push({ json: j }); }
  else { out.push({ json: d }); }
}
return out;
"""}})

# ---------- Step3a：Ollama 综合评估 ----------
EVAL_PROMPT = (
    "你是一名资深招聘顾问。请基于以下职位信息做综合评估并生成简报（用中文）：\\n"
    "职位名：{{ $json.title }}\\n公司：{{ $json.company_name }}\\n"
    "薪资：{{ $json.salary_raw }}\\n地点：{{ $json.city }}{{ $json.district }}\\n"
    "经验：{{ $json.experience }}\\n学历：{{ $json.education }}\\n"
    "描述：{{ $json.description }}\\n\\n请输出：\\n"
    "1) 技术方向判断；\\n2) 学历要求；\\n3) 经验要求；\\n"
    "4) 与「广州黄埔 AI/云计算/解决方案架构师」目标的匹配度(高/中/低)及一句话理由；\\n"
    "5) 给求职者的行动建议。")
add({"id": nid(), "name": "Ollama评估", "type": "n8n-nodes-base.ollama", "typeVersion": 1,
     "position": [1200, 400],
     "parameters": {"prompt": EVAL_PROMPT, "model": "qwen3:8b",
                    "ollamaHost": "http://host.docker.internal:11434",
                    "options": {"temperature": 0.3}}})

# ---------- 组装负载：抽取评估结论 + 匹配度 + 飞书群消息正文 ----------
add({"id": nid(), "name": "组装负载", "type": "n8n-nodes-base.code", "typeVersion": 2,
     "position": [1440, 400],
     "parameters": {"mode": "runForEachItem", "jsCode": """
const j = item.json;
const evalText = (j.text || j.output || "").trim();
let match = "中";
const m = evalText.match(/匹配度[：:]\\s*(高|中|低)/);
if (m) match = m[1];
const feishu_text = `【职位评估简报】\\n职位：${j.title}\\n公司：${j.company_name}`
  + `\\n薪资：${j.salary_raw || '面议'}\\n地点：${j.city || ''}${j.district ? ('·' + j.district) : ''}`
  + `\\n经验：${j.experience || '-'} | 学历：${j.education || '-'}\\n\\n${evalText}`
  + `\\n\\n🔗 ${j.job_url || ''}`;
item.json = Object.assign({}, j, { feishu_text, match, evaluation: evalText });
return item;
"""}})

# ---------- Step4：落看板(MySQL, 经 dashboard API) ----------
add(http("落看板数据", "POST", f"{DASHBOARD}/api/jobs/ingest",
        ('{"platform":"={{ $json.platform }}","title":"={{ $json.title }}",'
         '"company_name":"={{ $json.company_name }}","salary_raw":"={{ $json.salary_raw }}",'
         '"city":"={{ $json.city }}","district":"={{ $json.district }}",'
         '"experience":"={{ $json.experience }}","education":"={{ $json.education }}",'
         '"job_url":"={{ $json.job_url }}","description":"={{ $json.description }}",'
         '"evaluation":"={{ $json.evaluation }}","match":"={{ $json.match }}",'
         '"crawled_at":"={{ $json.crawled_at }}","evaluated_at":"={{ $now.toISO() }}"}'),
        extra={"position": [1680, 400]}))

# ---------- 筛选高/中匹配（避免群消息刷屏）----------
add({"id": nid(), "name": "筛选高匹配", "type": "n8n-nodes-base.code", "typeVersion": 2,
     "position": [1680, 560],
     "parameters": {"mode": "runOnceForAllItems", "jsCode": """
return $input.all().filter(it => {
  const mm = (it.json.match || "");
  return mm === "高" || mm === "中";
});
"""}})

# ---------- Step3b：发飞书群（逐条，仅高/中匹配；鉴权用「获取飞书Token」）----------
AUTH = "=Bearer {{ $('获取飞书Token').first().json.tenant_access_token }}"
add(http("发飞书群", "POST",
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        '{"receive_id":"={{ $vars.feishuGroupChatId }}","msg_type":"text","content":"={{ JSON.stringify({text: $json.feishu_text}) }}"}',
        extra={"position": [1920, 560]}, headers={"Authorization": AUTH}))

# ===================== 连接 =====================
def link(src, dst, t="main"):
    conns.setdefault(src, {}).setdefault(t, [[]])
    conns[src][t][0].append({"node": dst, "type": t, "index": 0})

link("手动触发", "配置")
link("每日定时触发", "配置")
link("配置", "获取飞书Token")
link("配置", "调用智联爬虫")
link("调用智联爬虫", "拉取职位")
link("调用智联爬虫", "写飞书多维表格")
link("拉取职位", "展开职位")
link("展开职位", "Ollama评估")
link("Ollama评估", "组装负载")
link("组装负载", "落看板数据")
link("组装负载", "筛选高匹配")
link("筛选高匹配", "发飞书群")

workflow = {
    "id": WF_ID,
    "name": "智联职位采集→飞书→Ollama评估→看板 (RouteB)",
    "nodes": nodes,
    "connections": conns,
    "settings": {"executionOrder": "v1"},
    "active": False,
    "versionId": nid(),
}

with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(workflow, f, ensure_ascii=False, indent=2)
print(f"输出路径: {OUT_PATH}")
print(f"已生成 job-pipeline.json：{len(nodes)} 个节点, {len(conns)} 条连接")
