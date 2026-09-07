#!/usr/bin/env bash
# 用独立资料目录启动一个带 CDP 调试端口的 Chrome。
# 在这个 Chrome 里人工登录机票网站;登录态保存在 .chrome-profile/,agent 通过 CDP 复用。
# 用法: bash scripts/open_chrome_debug.sh [起始URL]
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

mkdir -p "$PROFILE"
echo "启动人工登录 Chrome(独立资料目录: $PROFILE, 调试端口: $PORT)"
echo "请在弹出的 Chrome 里登录目标网站;登录后保持该窗口打开,不要关闭。"
exec "$CHROME" \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE" \
  --no-first-run \
  --no-default-browser-check \
  --disable-features=Translate,MediaRouter \
  "$URL"
