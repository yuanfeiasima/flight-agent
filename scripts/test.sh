#!/usr/bin/env bash
# 一键自检:比价引擎 + 携程离线解析回归(不需要浏览器/网络/图片)
#
# 用法: bash scripts/test.sh [pytest 额外参数]
# 说明: 本机 ~/.cache 可能无写权限,uv 缓存固定到工程内 .uv-cache/
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.uv-cache}"
exec uv run python -m pytest tests/ -q "$@"
