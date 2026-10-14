"""
生成 n8n 工作流 job-pipeline.json（四步自动化）
所有节点参数用 Python 字典构造，json.dumps 输出，避免手写 JSON 括号错位。
Step1 抓取 → Step2 写飞书多维表格 → Step3 Ollama 评估+发飞书群 → Step4 落看板(MySQL)
飞书凭证与 Bitable 目标从 n8n Variables 读取（$vars.xxx），由 scripts/setup_n8n.py 注入。
"""
import json, uuid, os

# 始终写到本脚本同目录，避免在不同 cwd 下误写到别处
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "job-pipeline.json")

def nid():
    return uuid.uuid4().hex

def wid():
    # n8n 工作流 id 形如 16 位 base62，这里取 uuid 前 16 位保证唯一且合规
    return uuid.uuid4().hex[:16]

nodes = []
conns = {}

def add(node):
    nodes.append(node)
    conns.setdefault(node["name"], {})
    return node

def http(name, method, url, json_body, extra=None, headers=None):
    p = {"method": method, "url": url, "sendBody": True, "specifyBody": "json",
         "jsonBody": json_body,
         "options": {"response": {"response": {"responseFormat": "json"}}}}
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

# ---------- 配置 ----------
add({"id": nid(), "name": "配置", "type": "n8n-nodes-base.set", "typeVersion": 3.2,
     "position": [240, 400],
     "parameters": {"mode": "manual",
                    "assignments": {"assignments": [
                        {"id": nid(), "name": "keywords", "type": "array",
                         "value": ["AI解决方案架构师", "云计算架构师", "解决方案架构师"]},
                        {"id": nid(), "name": "city", "type": "string", "value": "广州"},
                        {"id": nid(), "name": "maxPages", "type": "number", "value": 3},
                        {"id": nid(), "name": "maxJobs", "type": "number", "value": 50},
                    ]}, "options": {}}})

# ---------- 飞书 token（独立分支，下游用 $('获取飞书Token').first() 引用）----------
add(http("获取飞书Token", "POST",
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        '{"app_id":"={{ $vars.feishuAppId }}","app_secret":"={{ $vars.feishuAppSecret }}"}',
        extra={"position": [240, 640]}))

# ---------- 拆分关键词 ----------
add({"id": nid(), "name": "拆分关键词", "type": "n8n-nodes-base.code", "typeVersion": 2,
     "position": [480, 400],
     "parameters": {"mode": "runOnceForAllItems", "jsCode": """
const c = $input.first().json;
return (c.keywords || []).map(kw => ({ json: { kw, city: c.city, maxPages: c.maxPages, maxJobs: c.maxJobs } }));
"""}})

# ---------- Step1：调智联爬虫微服务 ----------
add(http("调用智联爬虫", "POST", "http://host.docker.internal:7800/search",
        '{"kw":"={{ $json.kw }}","city":"={{ $json.city }}","max_pages":{{ $json.maxPages }},"max_jobs":{{ $json.maxJobs }}}',
        extra={"position": [720, 400]}))

# ---------- 展开职位列表 ----------
add({"id": nid(), "name": "展开职位", "type": "n8n-nodes-base.code", "typeVersion": 2,
     "position": [960, 400],
     "parameters": {"mode": "runOnceForAllItems", "jsCode": """
const out = [];
for (const item of $input.all()) {
  const jobs = item.json.jobs || [];
  for (const j of jobs) out.push({ json: j });
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

# ---------- 组装 Bitable 字段 + 飞书消息 + 看板行 ----------
add({"id": nid(), "name": "组装负载", "type": "n8n-nodes-base.code", "typeVersion": 2,
     "position": [1440, 400],
     "parameters": {"mode": "runForEachItem", "jsCode": """
const j = item.json;
const evalText = (j.text || "").trim();
let match = "中";
const m = evalText.match(/匹配度[：:]\\s*(高|中|低)/);
if (m) match = m[1];
const feishu_text = `【职位评估简报】\\n职位：${j.title}\\n公司：${j.company_name}`
  + `\\n薪资：${j.salary_raw || '面议'}\\n地点：${j.city || ''}${j.district ? ('·' + j.district) : ''}`
  + `\\n经验：${j.experience || '-'} | 学历：${j.education || '-'}\\n\\n${evalText}`
  + `\\n\\n🔗 ${j.job_url || ''}`;
const bitable_fields = {
  "平台": j.platform || "智联",
  "职位名": j.title,
  "公司名": j.company_name,
  "薪资范围": j.salary_raw || "",
  "工作地点": (j.city || "") + (j.district ? ("·" + j.district) : ""),
  "经验": j.experience || "",
  "学历": j.education || "",
  "描述": (j.description || "").slice(0, 4000),
  "原始链接": j.job_url || "",
  "评估结论": evalText.slice(0, 2000),
  "匹配度": match,
  "抓取时间": new Date(j.crawled_at || Date.now()).getTime()
};
item.json = Object.assign({}, j, { feishu_text, bitable_fields, match, evaluation: evalText });
return item;
"""}})

AUTH = "=Bearer {{ $('获取飞书Token').first().json.tenant_access_token }}"

# ---------- Step2：写飞书多维表格（逐条 append）----------
add(http("写飞书多维表格", "POST",
        "https://open.feishu.cn/open-apis/bitable/v1/apps/{{ $vars.feishuBitableAppToken }}/tables/{{ $vars.feishuBitableTableId }}/records/batch_create",
        '{"records":[{"fields": {{ $json.bitable_fields }}}]}',
        extra={"position": [1680, 280]}, headers={"Authorization": AUTH}))

# ---------- Step3b：发飞书群（逐条）----------
add(http("发飞书群", "POST",
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        '{"receive_id":"={{ $vars.feishuGroupChatId }}","msg_type":"text","content":"={{ JSON.stringify({text: $json.feishu_text}) }}"}',
        extra={"position": [1680, 440]}, headers={"Authorization": AUTH}))

# ---------- Step4：落看板(MySQL, 经 dashboard API)----------
add(http("落看板数据", "POST", "http://jobhunter2-dashboard:8100/api/jobs/ingest",
        ('{"platform":"={{ $json.platform }}","title":"={{ $json.title }}",'
         '"company_name":"={{ $json.company_name }}","salary_raw":"={{ $json.salary_raw }}",'
         '"city":"={{ $json.city }}","district":"={{ $json.district }}",'
         '"experience":"={{ $json.experience }}","education":"={{ $json.education }}",'
         '"job_url":"={{ $json.job_url }}","description":"={{ $json.description }}",'
         '"evaluation":"={{ $json.evaluation }}","match":"={{ $json.match }}",'
         '"crawled_at":"={{ $json.crawled_at }}","evaluated_at":"={{ $now.toISO() }}"}'),
        extra={"position": [1680, 600]}))

# ===================== 连接 =====================
def link(src, dst, t="main"):
    conns.setdefault(src, {}).setdefault(t, [[]])
    conns[src][t][0].append({"node": dst, "type": t, "index": 0})

link("手动触发", "配置")
link("每日定时触发", "配置")
link("配置", "拆分关键词")
link("配置", "获取飞书Token")
link("拆分关键词", "调用智联爬虫")
link("调用智联爬虫", "展开职位")
link("展开职位", "Ollama评估")
link("Ollama评估", "组装负载")
link("组装负载", "写飞书多维表格")
link("组装负载", "发飞书群")
link("组装负载", "落看板数据")

workflow = {
    "id": wid(),
    "name": "智联职位采集→飞书→Ollama评估→看板",
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
