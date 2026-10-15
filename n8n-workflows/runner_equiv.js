'use strict';
// 忠实复刻 n8n 工作流 c53e4fc37e6649bd (RouteB) 的执行链路，
// 在 n8n 容器内运行（host.docker.internal / 服务 DNS 可直接解析，且能读自身飞书凭据库）。
// 链路：①配置 → ②获取飞书Token → ③调智联爬虫 → ④写飞书多维表 → ⑤拉取命中职位
//       → ⑥展开 → ⑦Ollama评估 → ⑧组装负载 → ⑨落看板 → ⑩筛选高/中 → ⑪发飞书群

const http = require('http');
const https = require('https');
const sqlite3 = require('/usr/local/lib/node_modules/n8n/node_modules/sqlite3');

const BACKEND = 'http://host.docker.internal:8911';
const DASHBOARD = 'http://jobhunter2-dashboard:8100';
const OLLAMA = 'http://host.docker.internal:11434';

const KEYWORDS = ['AI解决方案架构师', '云计算架构师', '解决方案架构师', '售前解决方案工程师'];
const MAX_JOBS = 50;

function req(method, url, { headers = {}, body = null, timeout = 60000 } = {}) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const lib = u.protocol === 'https:' ? https : http;
    const data = body == null ? null : (typeof body === 'string' ? body : JSON.stringify(body));
    const options = {
      method, hostname: u.hostname, port: u.port || (u.protocol === 'https:' ? 443 : 80),
      path: u.pathname + u.search, headers: { ...headers }, timeout,
    };
    if (data) options.headers['Content-Type'] = options.headers['Content-Type'] || 'application/json';
    const r = lib.request(options, (res) => {
      let d = '';
      res.on('data', (c) => (d += c));
      res.on('end', () => resolve({ status: res.statusCode, body: d }));
    });
    r.on('error', reject);
    r.on('timeout', () => { r.destroy(new Error('timeout')); });
    if (data) r.write(data);
    r.end();
  });
}
const jget = (s) => { try { return JSON.parse(s); } catch { return null; } };

function getVars() {
  return new Promise((resolve, reject) => {
    const db = new sqlite3.Database('/home/node/.n8n/database.sqlite', sqlite3.OPEN_READONLY);
    db.all('SELECT key,value FROM variables', (e, rows) => {
      db.close();
      if (e) return reject(e);
      const o = {};
      rows.forEach((r) => (o[r.key] = r.value));
      resolve(o);
    });
  });
}

const EVAL_PROMPT_TMPL = (j) =>
  `你是一名资深招聘顾问。请基于以下职位信息做综合评估并生成简报（用中文）：\n` +
  `职位名：${j.title}\n公司：${j.company_name}\n` +
  `薪资：${j.salary_raw}\n地点：${j.city}${j.district}\n` +
  `经验：${j.experience}\n学历：${j.education}\n` +
  `描述：${j.description}\n\n请输出：\n` +
  `1) 技术方向判断；\n2) 学历要求；\n3) 经验要求；\n` +
  `4) 与「广州黄埔 AI/云计算/解决方案架构师」目标的匹配度(高/中/低)及一句话理由；\n` +
  `5) 给求职者的行动建议。`;

(async () => {
  const log = (...a) => console.log(...a);
  const summary = { steps: {} };

  // ② 飞书凭据
  const vars = await getVars();
  const appId = vars.feishuAppId, appSecret = vars.feishuAppSecret, groupChatId = vars.feishuGroupChatId;
  log('② 飞书凭据: appId=%s groupChatId=%s secretLen=%s', appId, groupChatId, appSecret ? appSecret.length : 0);

  // ③ 调智联爬虫
  try {
    const url = `${BACKEND}/crawl/zhilian?keywords=${encodeURIComponent(KEYWORDS.join(','))}&max_jobs=${MAX_JOBS}`;
    const r = await req('POST', url, { timeout: 180000 });
    summary.steps.crawl = { status: r.status, body: jget(r.body) };
    log('③ 智联爬虫: HTTP %s  %s', r.status, r.body.slice(0, 300));
  } catch (e) {
    summary.steps.crawl = { error: e.message };
    log('③ 智联爬虫失败(非致命): %s', e.message);
  }

  // ④ 写飞书多维表（后端代理）
  try {
    const r = await req('POST', `${BACKEND}/sync/feishu/push`, { timeout: 60000 });
    summary.steps.push = { status: r.status, body: jget(r.body) };
    log('④ 写飞书多维表: HTTP %s  %s', r.status, r.body.slice(0, 300));
  } catch (e) {
    summary.steps.push = { error: e.message };
    log('④ 写飞书多维表失败(非致命): %s', e.message);
  }

  // ⑤ 拉取命中职位
  const rj = await req('GET', `${BACKEND}/jobs?matched=true&limit=200`, { timeout: 30000 });
  const jobs = jget(rj.body) || [];
  summary.steps.pull = { status: rj.status, count: jobs.length };
  log('⑤ 拉取命中职位: HTTP %s  命中数=%d', rj.status, jobs.length);

  // ⑦ Ollama 评估 + ⑧ 组装负载
  const evaluated = [];
  for (const j of jobs) {
    let evalText = '', match = '中';
    try {
      const orr = await req('POST', `${OLLAMA}/api/generate`, {
        timeout: 120000,
        body: JSON.stringify({ model: 'qwen3:8b', prompt: EVAL_PROMPT_TMPL(j), stream: false, options: { temperature: 0.3 } }),
      });
      const oj = jget(orr.body) || {};
      evalText = (oj.response || '').trim();
      const m = evalText.match(/匹配度[：:]\s*(高|中|低)/);
      if (m) match = m[1];
    } catch (e) {
      evalText = '[Ollama调用失败: ' + e.message + ']';
      match = '低';
    }
    const feishu_text = `【职位评估简报】\n职位：${j.title}\n公司：${j.company_name}` +
      `\n薪资：${j.salary_raw || '面议'}\n地点：${j.city || ''}${j.district ? ('·' + j.district) : ''}` +
      `\n经验：${j.experience || '-'} | 学历：${j.education || '-'}\n\n${evalText}` +
      `\n\n🔗 ${j.job_url || ''}`;
    evaluated.push({ ...j, evaluation: evalText, match, feishu_text, evaluated_at: new Date().toISOString() });
  }
  summary.steps.eval = { total: evaluated.length, dist: evaluated.reduce((a, x) => ((a[x.match] = (a[x.match] || 0) + 1), a), {}) };
  log('⑦⑧ Ollama评估完成: 共%d条, 匹配分布=%j', evaluated.length, summary.steps.eval.dist);

  // ⑨ 落看板
  let dashOk = 0, dashFail = 0;
  for (const j of evaluated) {
    try {
      const body = {
        platform: j.platform, title: j.title, company_name: j.company_name, salary_raw: j.salary_raw,
        city: j.city, district: j.district, experience: j.experience, education: j.education,
        job_url: j.job_url, description: j.description, evaluation: j.evaluation, match: j.match,
        crawled_at: j.crawled_at, evaluated_at: j.evaluated_at,
      };
      const r = await req('POST', `${DASHBOARD}/api/jobs/ingest`, { timeout: 30000, body });
      if (r.status >= 200 && r.status < 300) dashOk++; else dashFail++;
    } catch { dashFail++; }
  }
  summary.steps.dashboard = { ok: dashOk, fail: dashFail };
  log('⑨ 落看板: 成功=%d 失败=%d', dashOk, dashFail);

  // ⑪ 发飞书群（仅高/中）
  const highMid = evaluated.filter((x) => x.match === '高' || x.match === '中');
  summary.steps.group = { candidates: highMid.length };
  try {
    const tr = await req('POST', 'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal', {
      timeout: 20000, body: { app_id: appId, app_secret: appSecret },
    });
    const tj = jget(tr.body) || {};
    const token = tj.tenant_access_token;
    summary.steps.group.tokenStatus = tr.status;
    if (!token) throw new Error('无 tenant_access_token: ' + tr.body);
    let sent = 0;
    for (const j of highMid) {
      const content = JSON.stringify({ text: j.feishu_text });
      const mr = await req('POST', `https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id`, {
        timeout: 20000,
        headers: { Authorization: 'Bearer ' + token },
        body: { receive_id: groupChatId, msg_type: 'text', content },
      });
      if (mr.status >= 200 && mr.status < 300) sent++;
      else log('   群消息发送失败(%s): %s', j.title, mr.body.slice(0, 200));
    }
    summary.steps.group.sent = sent;
    log('⑪ 发飞书群: 候选%d条 成功发送%d条', highMid.length, sent);
  } catch (e) {
    summary.steps.group.error = e.message;
    log('⑪ 发飞书群失败: %s', e.message);
  }

  log('\n===== 执行摘要 =====');
  log(JSON.stringify(summary, null, 2));
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
