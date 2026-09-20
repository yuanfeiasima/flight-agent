#!/usr/bin/env bash
# 用独立资料目录启动一个带 CDP 调试端口的 Chrome。
# 在这个 Chrome 里人工登录机票网站;登录态保存在 .chrome-profile/,agent 通过 CDP 复用。
#
# 用法: bash scripts/open_chrome_debug.sh [起始URL]
#
# 环境变量:
#   CDP_PORT=9222            调试端口
#   CHROME_EXTRA_FLAGS="..." 追加启动参数
#   CHROME_NO_SANDBOX=1      追加 --no-sandbox --disable-gpu
#
# CHROME_NO_SANDBOX 的用途:在受限沙箱里(例如由 agent/CI 代跑,Chrome 自身的
# 沙箱无法初始化,报 "sandbox initialization failed: Operation not permitted" 并
# 以 "GPU process isn't usable. Goodbye." 退出)需要它才能起来。你本人在终端里
# 手动启动时**不需要**,保持默认即可(Chrome 沙箱是重要防护)。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${DIR}/.chrome-profile"
PORT="${CDP_PORT:-9222}"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
URL="${1:-https://flights.ctrip.com}"

if [ ! -x "$CHROME" ]; then
  echo "找不到 Chrome: $CHROME" >&2
  exit 1
fi

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "端口 $PORT 已有调试实例在运行 —— 直接复用即可。"
  echo "请确认那个 Chrome 窗口里已登录: $URL"
  exit 0
fi

EXTRA=()
if [ "${CHROME_NO_SANDBOX:-0}" = "1" ]; then
  EXTRA+=(--no-sandbox --disable-gpu)
  echo "注意:已按 CHROME_NO_SANDBOX=1 关闭 Chrome 自身沙箱(仅用于受限沙箱环境)。"
fi
# shellcheck disable=SC2206
[ -n "${CHROME_EXTRA_FLAGS:-}" ] && EXTRA+=(${CHROME_EXTRA_FLAGS})

mkdir -p "$PROFILE"
echo "启动人工登录 Chrome(独立资料目录: $PROFILE, 调试端口: $PORT)"
echo "请在弹出的 Chrome 里登录目标网站;登录后保持该窗口打开,不要关闭。"
exec "$CHROME" \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE" \
  --no-first-run \
  --no-default-browser-check \
  --disable-features=Translate,MediaRouter \
  "${EXTRA[@]}" \
  "$URL"
