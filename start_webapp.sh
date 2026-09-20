#!/bin/bash
# 启动 flight-agent webapp
# 使用方法: bash start_webapp.sh

cd "$(dirname "$0")"

# 激活虚拟环境
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

# 启动 webapp
echo "启动 flight-agent webapp..."
python -m flight_agent.webapp.server "$@"
