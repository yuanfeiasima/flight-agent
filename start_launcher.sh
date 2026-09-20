#!/bin/bash
# 启动 flight-agent launcher (推荐使用)
# 使用方法: bash start_launcher.sh

cd "$(dirname "$0")"

# 1. 先停止已有的进程
echo "🔍 检查已有进程..."
PIDS=$(ps aux | grep '[p]ython.*flight_agent.launcher' | awk '{print $2}')
if [ -n "$PIDS" ]; then
    echo "🛑 停止已有进程: $PIDS"
    echo "$PIDS" | xargs kill -9 2>/dev/null
    sleep 1
fi

# 检查端口占用
PORT=8712
if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "🛑 停止占用端口 $PORT 的进程..."
    lsof -ti:$PORT | xargs kill -9 2>/dev/null
    sleep 1
fi

# 2. 激活虚拟环境
if [ ! -d "venv" ]; then
    echo "虚拟环境不存在，正在创建..."
    python3 -m venv venv
    source venv/bin/activate
    echo "安装依赖..."
    pip install -r requirements.txt
    echo "安装 Playwright 浏览器..."
    playwright install chromium
else
    source venv/bin/activate
fi

# 3. 清理浏览器缓存（可选）
# rm -rf ~/Library/Application\ Support/flight-agent/chrome-profile/Default/Cache 2>/dev/null

# 4. 启动 launcher
echo "🚀 启动 flight-agent launcher..."
python -m flight_agent.launcher "$@"
