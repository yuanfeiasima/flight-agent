#!/usr/bin/env bash
# 一键自检:比价引擎 + 各渠道离线解析回归 + GUI 脚本语法校验
# (不需要网络;GUI 的 JS 语法校验需要调试 Chrome,不在线时那条会自动跳过)
#
# 用法: bash scripts/test.sh [pytest 额外参数]
# 说明: 本机 ~/.cache 可能无写权限,uv 缓存固定到工程内 .uv-cache/
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.uv-cache}"

# 工程内 .venv 优先:它由 scripts/bootstrap.sh 保证是匹配本机架构的 3.12
if [[ -x .venv/bin/python ]] && .venv/bin/python -c 'import sys, pytest; assert sys.version_info >= (3, 10)' >/dev/null 2>&1; then
  exec .venv/bin/python -m pytest tests/ -q "$@"
fi
if command -v uv >/dev/null 2>&1; then
  exec uv run python -m pytest tests/ -q "$@"
fi
if python3 -c 'import sys, pytest; assert sys.version_info >= (3, 10)' >/dev/null 2>&1; then
  exec python3 -m pytest tests/ -q "$@"
fi

echo "没有可用的 Python 3.10+ pytest 环境。请先运行:bash scripts/bootstrap.sh" >&2
exit 1
