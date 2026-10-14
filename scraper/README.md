# 智联爬虫微服务 · 运维手册（Step 1）

## 一、它解决什么问题（大白话）
n8n 跑在 Docker 里，容器里没有浏览器，跑不了智联这种「React 单页 + 反爬 + 登录墙 + 滑块验证」的网站。
所以把抓取动作单独抽成一个 **FastAPI 微服务**，跑在**宿主机**（您本机 Windows，已有 Playwright + 登录态），
n8n 只需要用一条 `HTTP Request` 调它：`POST http://host.docker.internal:7800/search`。
这就是「复用现有项目经验」——里面的城市码、卡片解析、详情页 JD 提取、反爬节奏，都是从 `jobhunter/zhilian_crawler.py` 搬过来的成熟逻辑。

## 二、目录与端口
- `main.py`：FastAPI 服务，端口 **7800**
- `requirements.txt`：fastapi / uvicorn / playwright / pydantic
- `Dockerfile`：可选项，纳入 n8nwp 同网络跑容器版
- `run_host.bat` / `run_host.sh`：宿主机一键启动脚本
- `.env.feishu`：飞书凭证（**仅本机、不入库、不提交**）

## 三、首次安装（在宿主机 PowerShell / Terminal）
```bash
cd scraper
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # 装依赖
# 浏览器已缓存在 C:\Users\admin\.cache\ms-playwright，无需重下；
# 若换机器：.venv\Scripts\playwright install chromium
```

## 四、启动（两种）
1. 脚本启动：双击 `run_host.bat`（Windows）或 `bash run_host.sh`（Linux/Mac）
2. 手动：` .venv\Scripts\uvicorn main:app --host 0.0.0.0 --port 7800`

健康检查：`curl http://127.0.0.1:7800/health` → `{"status":"ok",...}`

## 五、调用方式（n8n 里这么用）
```http
POST http://host.docker.internal:7800/search
Content-Type: application/json
{
  "kw": "AI解决方案架构师",
  "city": "广州",
  "max_pages": 3,
  "max_jobs": 50,
  "use_profile": true,
  "headless": true
}
```
返回：`{ "keyword", "city", "count", "warning", "jobs": [ {公司名/职位名/描述/原始链接/薪资范围/工作地点/经验/学历...} ] }`

## 六、登录态与验证码（关键运维点）
- 智联对未登录会弹「登录墙」，详情页偶发「真人验证」滑块。
- **推荐**：把 `run_host.bat` 里的 `PROFILE_DIR` 指向你**已登录智联**的 Chrome 用户数据目录（如 `C:\Users\admin\AppData\Local\Google\Chrome\User Data`），服务会复用登录态，绝大多数情况直接过。
- 若没配 Profile：搜索页被拦时返回 `warning:"search_login_wall"`，详情页验证码会跳过该条 JD（description 留空），下次补采即可。
- **不要试图让 n8n 自动解滑块**——那是人工动作；要么预登录，要么接受偶发空 JD。

## 七、故障排查（洋葱模型）
| 现象 | 根因 | 处理 |
|---|---|---|
| 连接被拒 | 服务没起 / 端口不对 | 先 `curl /health`；确认 7800 在监听 |
| 返回 0 条 + warning=search_login_wall | 未登录被拦 | 配置 PROFILE_DIR 复用登录态 |
| 描述普遍为空 | 详情页触发反爬/验证码 | 降并发、加 Profile、人工解一次滑块 |
| 字段错位 | 智联改版 DOM | 改 `main.py` 选择器（参考 zhilian_crawler 的備选选择器列表） |
| 宿主连不上 | n8n 在 Docker 用 host.docker.internal | 确认 compose 有 `extra_hosts: host.docker.internal:host-gateway` |

## 八、与整体流水线的衔接
Step1 输出 → n8n 规范化 → 写飞书多维表格(Step2) → Ollama 评估+发群(Step3) → 落 MySQL 看板(Step4)。
爬虫服务自身**不碰飞书**，只负责把结构化职位数据交还给 n8n。
