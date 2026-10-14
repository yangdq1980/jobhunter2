@echo off
REM 宿主机启动智联爬虫微服务（Windows）
REM 前置：已用系统 Python 建好 .venv 并 pip install -r requirements.txt
REM 浏览器已缓存在 C:\Users\admin\.cache\ms-playwright，无需重新下载

setlocal
set PW_CACHE=C:\Users\admin\.cache\ms-playwright
set PLAYWRIGHT_BROWSERS_PATH=%PW_CACHE%

REM 可选：复用已登录智联的 Chrome 用户数据（避免登录墙）。把路径填到下面并取消注释
REM set PROFILE_DIR=C:\Users\admin\AppData\Local\Google\Chrome\User Data

cd /d %~dp0
call .venv\Scripts\activate.bat
uvicorn main:app --host 0.0.0.0 --port 7800
endlocal
