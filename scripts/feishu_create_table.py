#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书多维表格自动创建脚本（Step-2 落地表）
========================================
用途：基于现有飞书应用 cli_aa4a01ad 调开放 API，新建一个多维表格 + 数据表 + 12 个字段，
      输出 app_token / table_id 用于回填 n8n Variables（feishuBitableAppToken / feishuBitableTableId）。

前置条件（务必先满足，否则会返回 21001 访问策略拒绝）：
  1. 飞书开发者后台已「启用/发布」应用 cli_aa4a01ad，并发布新版本使权限生效；
  2. 已开通权限：bitable:app（或 bitable:app:readonly + bitable:app），im:message、im:message:send_as_bot；
  3. 应用「访问控制 / 可信IP / 安全设置」未限制调用来源；
  4. 本机可访问 open.feishu.cn（脚本从宿主机运行，出口 IP 需在飞书应用允许范围内）。

用法：
  python scripts/feishu_create_table.py
输出：
  打印 app_token / table_id，并写入 .feishu_bitable.txt（gitignore 忽略）。
随后把这两个值分别填入 n8n Variables：feishuBitableAppToken / feishuBitableTableId。
"""
import json
import re
import urllib.request
import urllib.error
import os

# 凭证来源：scraper/.env.feishu（不入库）。也可直接改下方 ENV_PATH。
ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scraper", ".env.feishu")
BASE = "https://open.feishu.cn/open-apis"

# 字段全部建为文本(type=1)，避免飞书「超链接/单选」类型枚举歧义导致写入失败。
# 原始链接也用文本，对应 n8n 工作流「组装负载」节点已改为发送纯字符串 URL。
FIELDS = ["平台", "职位名", "公司名", "薪资范围", "工作地点", "经验", "学历",
          "描述", "原始链接", "评估结论", "匹配度", "抓取时间"]


def load_cfg(path):
    cfg = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\s*([A-Z_]+)\s*=\s*(.+?)\s*$", line)
            if m:
                cfg[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return cfg


def call(method, path, body=None, token=None, retry=0):
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def main():
    cfg = load_cfg(ENV_PATH)
    app_id = cfg["FEISHU_APP_ID"]
    app_secret = cfg["FEISHU_APP_SECRET"]

    print("== 1) tenant_access_token ==")
    st, js = call("POST", "/auth/v3/tenant_access_token/internal",
                  {"app_id": app_id, "app_secret": app_secret})
    if st != 200 or js.get("code") != 0:
        print("FAILED:", st, js)
        raise SystemExit("请先按脚本头部『前置条件』修复飞书应用（常见：未发布/未开权限/访问策略拦截，错误码 21001）")
    token = js["tenant_access_token"]
    print("  OK")

    print("== 2) root folder token ==")
    st, js = call("GET", "/drive/v1/root_folder/meta", token=token)
    if st != 200 or js.get("code") != 0:
        print("  root_folder 失败(可能缺 drive 权限):", st, js.get("msg"))
        raise SystemExit(2)
    folder_token = js["data"]["folder_token"]

    print("== 3) 新建多维表格 ==")
    st, js = call("POST", "/bitable/v1/apps",
                  {"name": "职位采集流水线-智联招聘", "folder_token": folder_token}, token=token)
    if st != 200 or js.get("code") != 0:
        print("FAILED create app:", st, js)
        raise SystemExit(1)
    app_token = js["data"]["app"]["app_token"]
    print("  app_token =", app_token)

    print("== 4) 取默认数据表 ==")
    st, js = call("GET", f"/bitable/v1/apps/{app_token}/tables", token=token)
    table_id = js["data"]["items"][0]["table_id"]
    print("  table_id =", table_id)

    print("== 5) 建字段 ==")
    for fname in FIELDS:
        st, js = call("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
                      {"field_name": fname, "type": 1}, token=token)
        ok = st == 200 and js.get("code") == 0
        print(f"  [{'OK' if ok else 'FAIL'}] {fname} -> {st} {js.get('code')} {js.get('msg','')}")

    print("\n=== 回填 n8n Variables ===")
    print("feishuBitableAppToken =", app_token)
    print("feishuBitableTableId  =", table_id)
    with open(".feishu_bitable.txt", "w", encoding="utf-8") as f:
        f.write(app_token + "\n" + table_id + "\n")
    print("(已写入 .feishu_bitable.txt)")


if __name__ == "__main__":
    main()
