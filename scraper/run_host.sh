#!/usr/bin/env bash
# 宿主机启动智联爬虫微服务（Linux/Mac）
set -e
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"
# 可选：复用已登录智联的浏览器 Profile
# export PROFILE_DIR="$HOME/.config/google-chrome"
cd "$(dirname "$0")"
source .venv/bin/activate
exec uvicorn main:app --host 0.0.0.0 --port 7800
