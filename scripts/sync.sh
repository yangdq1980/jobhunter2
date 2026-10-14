#!/usr/bin/env bash
# 每日同步脚本：把最新代码与文档提交并推送到 GitHub
# 用法： ./scripts/sync.sh "可选提交说明"
set -e
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"
MSG="${1:-chore: 每日同步 $(date +%Y-%m-%d)}"
git add -A
if git diff --cached --quiet; then
  echo "无变更，跳过提交"
else
  git commit -m "$MSG"
  echo "已提交: $MSG"
fi
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if git remote get-url origin >/dev/null 2>&1; then
  git push origin "$BRANCH" || echo "⚠️ 推送失败：请确认远程仓库已添加且凭证可用"
else
  echo "⚠️ 未配置 origin 远程，仅完成本地提交。请先: git remote add origin <仓库地址>"
fi
