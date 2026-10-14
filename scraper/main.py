"""
智联招聘爬虫微服务 (Step 1 of n8n Job Pipeline)
================================================
把 jobhunter/zhilian_crawler.py 的成熟解析逻辑（城市码、卡片解析、详情页 JD 提取、反爬节奏）
封装为 FastAPI 服务，供 n8n 通过 HTTP Request 调用。

设计要点（大白话）：
- 智联新版是 React 单页，列表和详情都要等 JS 渲染完再抓，所以全程用 Playwright 真实浏览器。
- 搜索页可能弹「登录墙」、详情页可能弹「真人验证」滑块。本服务不自动解验证码，而是：
  * 若配置了已登录的浏览器 Profile（PROFILE_DIR），复用登录态，绝大多数情况能直接过；
  * 若没登录且被拦，返回已抓到的部分数据 + warning 标记，交由人工或下一轮补采。
- 输出字段与 n8n → 飞书多维表格 → Ollama 评估链路严格对齐（见 docs/02-架构设计.md）。

端口：7800（宿主机运行；n8n Docker 内用 http://host.docker.internal:7800 访问）
"""
import logging
import os
import re
import time
import random
import datetime
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

try:
    from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
except ImportError:
    sync_playwright = None  # 仅语法检查环境可能未装，运行环境必装

logging.basicConfig(level=logging.INFO, format="%(asctime)s [zhilian-svc] %(levelname)s %(message)s")
logger = logging.getLogger("zhilian-svc")

app = FastAPI(title="Zhilian Scraper Service", version="1.0.0")

# ============ 智联城市数字码（与 zhilian_crawler.py 一致，jl 只认数字码）============
CITY_CODES = {
    "广州": "763", "深圳": "765", "上海": "538", "北京": "530", "杭州": "653",
    "成都": "801", "南京": "635", "武汉": "738", "西安": "854", "苏州": "639",
    "佛山": "566", "东莞": "560", "珠海": "775", "长沙": "749", "合肥": "601",
    "郑州": "759", "青岛": "686", "厦门": "742", "天津": "533", "重庆": "551", "全国": "000",
}
SEARCH_URL = "https://www.zhaopin.com/sou/"
DETAIL_WAIT = ("networkidle", 30000)

# 浏览器 Profile（可选，放已登录智联的 Chrome User Data，复用登录态避免登录墙）
PROFILE_DIR = os.getenv("PROFILE_DIR", "")
HEADLESS = os.getenv("HEADLESS", "true").lower() != "false"


class SearchRequest(BaseModel):
    kw: str                                  # 搜索关键词，如 "AI解决方案架构师"
    city: str = "广州"                       # 城市中文名
    max_pages: int = 3                       # 最多翻页数
    max_jobs: int = 50                       # 单关键词最多职位数
    use_profile: bool = False                # 是否复用已登录 Profile
    headless: bool = True                    # 是否无头


# ============ 薪资解析 ============
def parse_salary(raw: str) -> (Optional[int], Optional[int]):
    """'1.5-3万/月' -> (15000, 30000)；'20-30k' -> (20000,30000)；'面议' -> (None,None)"""
    if not raw:
        return None, None
    s = raw.replace(" ", "").lower()
    if "面议" in s or "协商" in s:
        return None, None
    # 单位
    unit = 1000 if ("k" in s or "千" in s) else (10000 if ("万" in s) else 1)
    if "万" in s:
        unit = 10000
    elif "k" in s:
        unit = 1000
    nums = re.findall(r"(\d+(?:\.\d+)?)", s)
    try:
        if len(nums) >= 2:
            lo = float(nums[0]) * unit
            hi = float(nums[1]) * unit
            return int(lo), int(hi)
        if len(nums) == 1:
            v = int(float(nums[0]) * unit)
            return v, v
    except Exception:
        return None, None
    return None, None


# ============ 核心爬虫 ============
class ZhilianScraper:
    def __init__(self, use_profile: bool, headless: bool):
        self.use_profile = use_profile
        self.headless = headless
        self.pw = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def __enter__(self):
        self.pw = sync_playwright().start()
        launch_kwargs = {"headless": self.headless, "args": ["--disable-blink-features=AutomationControlled"]}
        if self.use_profile and PROFILE_DIR:
            self.context = self.pw.chromium.launch_persistent_context(PROFILE_DIR, **launch_kwargs)
            self.page = self.context.new_page()
        else:
            self.browser = self.pw.chromium.launch(**launch_kwargs)
            self.context = self.browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
            self.page = self.context.new_page()
        return self

    def __exit__(self, *a):
        try:
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.pw:
                self.pw.stop()
        except Exception as e:
            logger.debug(f"关闭浏览器异常(忽略): {e}")

    def _is_captcha(self, page: Page) -> bool:
        try:
            url = page.url.lower()
            if any(k in url for k in ["captcha", "verify", "geetest", "tcaptcha", "yidun"]):
                return True
            body = page.evaluate("() => document.body?.innerText?.substring(0,800) || ''")
            return any(k in body for k in ["人机验证", "拖动滑块", "请完成验证", "滑块验证"])
        except Exception:
            return False

    def _extract_jobs_from_page(self, city: str) -> List[Dict[str, Any]]:
        page = self.page
        cards = page.query_selector_all(".joblist-box__item, [class*='joblist'] > [class*='item'], .positionlist .position, [class*='job-card']")
        logger.info(f"本页卡片数: {len(cards)}")
        jobs = []
        for card in cards:
            try:
                def txt(sel):
                    el = card.query_selector(sel)
                    return el.inner_text().strip() if el else ""
                title = txt(".jobname, .job-name, .position-name, a[href*='jobdetail']")
                company = txt(".company-name, .company, a[href*='company']")
                if not title or not company:
                    continue
                salary_raw = txt(".salary, .money, [class*='salary'], [class*='money']")
                # 地点
                loc = txt(".location, [class*='city'], [class*='location'], [class*='address']")
                if not loc:
                    full = card.inner_text()
                    m = re.search(r"(广州|深圳|北京|上海|杭州|成都|武汉|南京|佛山|东莞|珠海|长沙)[·\-]?[^\s·\-]{1,6}", full)
                    loc = m.group(0) if m else city
                # 经验/学历
                exp = txt(".experience, .work-years, [class*='exp']")
                edu = txt(".education, .edu, [class*='edu']")
                tags = txt("[class*='tag'], [class*='label']")
                if (not exp or not edu) and tags:
                    tm = re.findall(r"(\d+[—\-~～]*\d*年|经验不限|应届生).*?(大专|本科|硕士|博士|学历不限)", tags)
                    if tm:
                        exp = exp or tm[0][0]
                        edu = edu or tm[0][1]
                # 链接
                link_el = card.query_selector("a[href*='jobdetail'], a[href*='/job/']")
                job_url = link_el.get_attribute("href") if link_el else ""
                if job_url and not job_url.startswith("http"):
                    job_url = "https://www.zhaopin.com" + job_url if job_url.startswith("/") else "https://" + job_url
                s_min, s_max = parse_salary(salary_raw)
                city_clean = re.sub(r"[省市]$", "", loc.split("·")[0].strip()) or city
                district = loc.split("·")[1].strip() if "·" in loc else ""
                jobs.append({
                    "platform": "zhilian",
                    "title": title,
                    "company_name": company,
                    "salary_raw": salary_raw,
                    "salary_min": s_min,
                    "salary_max": s_max,
                    "city": city_clean,
                    "district": district,
                    "experience": exp,
                    "education": edu,
                    "job_url": job_url,
                    "description": "",
                    "crawled_at": datetime.datetime.now().isoformat(timespec="seconds"),
                })
            except Exception as e:
                logger.debug(f"解析卡片失败: {e}")
        return jobs

    def _fetch_detail(self, url: str) -> str:
        if not url:
            return ""
        try:
            p = self.context.new_page()
            try:
                p.goto(url, wait_until=DETAIL_WAIT[0], timeout=DETAIL_WAIT[1])
            except Exception:
                p.goto(url, wait_until="domcontentloaded", timeout=DETAIL_WAIT[1])
                time.sleep(4)
            if self._is_captcha(p):
                logger.warning(f"详情页验证码(未自动解): {url[:80]}")
                p.close()
                return ""
            desc = ""
            for sel in [".describtion-card__detail-content", ".describtion-card", "[class*='describtion']",
                        ".job-detail-section .content", ".job-description", "[class*='job-desc']", "[class*='description']"]:
                el = p.query_selector(sel)
                if el:
                    t = el.inner_text().strip()
                    if len(t) > 50:
                        desc = t
                        break
            if not desc:
                body = p.evaluate("() => document.body?.innerText?.substring(0,10000) || ''")
                for pat in [r"职位描述[：:]\s*(.{100,})", r"岗位职责[：:]\s*(.{100,})", r"任职要求[：:]\s*(.{50,})"]:
                    m = re.search(pat, body, re.DOTALL)
                    if m:
                        desc = m.group(1).strip()[:3000]
                        break
            p.close()
            return desc[:3000]
        except Exception as e:
            logger.debug(f"详情页抓取失败 {url[:60]}: {e}")
            return ""

    def search_jobs(self, kw: str, city: str, max_pages: int, max_jobs: int) -> Dict[str, Any]:
        city_code = CITY_CODES.get(city, "000")
        url = f"{SEARCH_URL}?jl={city_code}&kw={kw}&p=1"
        logger.info(f"搜索: {kw} @ {city}(jl={city_code})")
        jobs: List[Dict[str, Any]] = []
        warning = None
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(2, 4))
            # 登录墙检测
            body = self.page.evaluate("() => document.body?.innerText?.substring(0,1500) || ''")
            if any(s in body for s in ["请先登录", "登录后搜索", "手机号登录", "请登录"]):
                warning = "search_login_wall"
                logger.warning("搜索页登录墙：建议配置已登录 Profile 后重试")
            page_num = 1
            while page_num <= max_pages and len(jobs) < max_jobs:
                page_jobs = self._extract_jobs_from_page(city)
                if not page_jobs:
                    break
                jobs.extend(page_jobs)
                logger.info(f"第{page_num}页 {len(page_jobs)} 条(累计 {len(jobs)})")
                if len(jobs) >= max_jobs:
                    break
                # 翻页
                nxt = self.page.query_selector(".soupager .btn-next:not(.disable), a.next:not(.disable), [aria-label='下一页']")
                if not nxt:
                    break
                nxt.click()
                time.sleep(random.uniform(2, 4))
                page_num += 1
            # 补全 JD（节流，最多 30 条详情页，避免触发反爬）
            need = [j for j in jobs if not j["description"] and j["job_url"]][:30]
            for j in need:
                j["description"] = self._fetch_detail(j["job_url"])
                time.sleep(random.uniform(1.5, 3))
        except Exception as e:
            warning = f"exception:{e}"
            logger.error(f"搜索异常: {e}")
        return {"keyword": kw, "city": city, "count": len(jobs), "warning": warning, "jobs": jobs}


@app.get("/health")
def health():
    return {"status": "ok", "playwright": sync_playwright is not None}


@app.post("/search")
def search(req: SearchRequest):
    if sync_playwright is None:
        raise HTTPException(500, "playwright 未安装")
    use_profile = req.use_profile or bool(PROFILE_DIR)
    try:
        with ZhilianScraper(use_profile=use_profile, headless=req.headless) as sc:
            result = sc.search_jobs(req.kw, req.city, req.max_pages, req.max_jobs)
        logger.info(f"完成 [{req.kw}@{req.city}] 共 {result['count']} 条")
        return result
    except Exception as e:
        logger.exception("search 失败")
        raise HTTPException(500, f"爬虫异常: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7800)
