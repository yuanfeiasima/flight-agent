# Flight Agent 快速启动指南

## 🚀 快速开始

### 1. 首次使用（自动完成所有设置）

```bash
cd /Users/wangwt/Documents/opencode/flight-agent
bash start_launcher.sh
```

首次运行会自动：
- ✅ 检查并创建虚拟环境
- ✅ 安装 Python 依赖
- ✅ 下载 Playwright 浏览器
- ✅ 启动 Chrome（调试模式）
- ✅ 打开 GUI 界面

### 2. 登录网站

启动后会看到蓝色的登录引导卡片，点击按钮打开登录页：

- 🛫 **携程** - flights.ctrip.com
- ✈️ **去哪儿** - flight.qunar.com  
- 🍔 **美团** - i.meituan.com
- 🎫 **同程** - ly.com
- 🦘 **飞猪** - fliggy.com

**登录一次后，下次启动会自动复用登录态。**

### 3. 开始查询

登录完成后：
1. 填写出发/到达城市、日期
2. 选择舱位、经停条件
3. 点击"开始查询"
4. 等待 30-60 秒查看结果

## 📝 启动方式

### 方式 1：完整应用（推荐）

```bash
bash start_launcher.sh
```

自动打开 Chrome 和 GUI，体验最佳。

### 方式 2：仅 Web 服务

```bash
bash start_webapp.sh
```

然后手动打开浏览器访问：http://127.0.0.1:8712

## ⚙️ 可选参数

```bash
# 指定端口
bash start_launcher.sh --port 9000

# 不自动打开浏览器
bash start_launcher.sh --no-open

# 跳过 Chrome 准备（调试用）
bash start_launcher.sh --no-chrome
```

## 🌐 支持的网站

目前支持 5 个机票网站：

1. **携程** (ctrip) - 已完成 ✅
2. **去哪儿** (qunar) - 已完成 ✅
3. **美团** (meituan) - 框架已完成，需调试 🚧
4. **同程** (tongcheng) - 框架已完成，需调试 🚧
5. **飞猪** (fliggy) - 框架已完成，需调试 🚧

## 🔧 手动操作（高级用户）

如果需要手动操作：

```bash
# 激活虚拟环境
source venv/bin/activate

# 安装依赖（首次）
pip install -r requirements.txt
playwright install chromium

# 启动应用
python -m flight_agent.launcher

# 或仅启动 web 服务
python -m flight_agent.webapp.server
```

## 💡 提示

- **Chrome 窗口不要关闭** - 查询期间需要保持打开
- **登录态会保存** - 使用独立的 Chrome 配置文件，不影响日常浏览
- **数据存储位置** - `~/Library/Application Support/flight-agent/`
- **查询历史** - 在右侧面板可以查看和重新加载

## ❓ 常见问题

### Q: 端口被占用？
```bash
bash start_launcher.sh --port 9000
```

### Q: Chrome 连接失败？
确保没有其他程序占用 9222 端口：
```bash
lsof -i :9222
```

### Q: 如何卸载？
```bash
rm -rf venv
rm -rf ~/Library/Application\ Support/flight-agent
```
