@echo off
REM 每日同步脚本：把最新代码与文档提交并推送到 GitHub
REM 用法： scripts\sync.bat [可选提交说明]
cd /d "%~dp0.."
if "%~1"=="" (set MSG=chore: 每日同步 %date%) else (set MSG=%~1)
git add -A
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "%MSG%"
  echo 已提交: %MSG%
) else (
  echo 无变更，跳过提交
)
git remote get-url origin >nul 2>&1
if errorlevel 1 (
  echo ⚠️ 未配置 origin 远程，仅完成本地提交。请先执行: git remote add origin ^<仓库地址^>
) else (
  git push origin HEAD || echo ⚠️ 推送失败：请确认远程仓库已添加且凭证可用
)
