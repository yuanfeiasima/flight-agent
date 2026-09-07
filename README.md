# flight-agent — 机票自动比价·最低价筛选 Agent(MVP)

人工提前登录机票网站;agent 复用该登录态操作真实 Chrome,抓取航班价格,按你的约束(直飞/舱位/时刻窗)过滤排序,最终推荐最低价航班。**Agent 只查询与推荐,绝不代替你下单/支付。**

> 当前为 MVP:Python + Playwright,先只对接**携程机票**一个渠道,单日单次查询。架构预留了多渠道(航司官网等)与持续监控的扩展位。

## 目录结构

```
flight-agent/
├── scripts/
│   ├── open_chrome_debug.sh   # 用独立资料目录启动带调试端口的 Chrome(登录态保存在 .chrome-profile/)
│   └── test.sh                # 一键离线自检(引擎 + 携程解析回归,uv 缓存指向工程内 .uv-cache/)
├── flight_agent/
│   ├── browser.py             # 浏览器会话层:CDP 附加你已登录的 Chrome
│   ├── models.py              # 航班/查询/结果 数据模型
│   ├── config.py              # 运行配置(约束默认值、滚动/等待参数都在这里)
│   ├── city_codes.py          # 城市 → 携程 IATA 城市码映射
│   ├── adapters/
│   │   ├── base.py            # 站点适配器抽象接口 + 公共解析工具
│   │   └── ctrip.py           # 携程适配器:搜索 → 等待 → 滚动懒加载 → 抽取航班列表
│   ├── engine/
│   │   └── compare.py         # 比价决策引擎:归一化/约束过滤/去重/排序(纯逻辑,可单测)
│   └── cli.py                 # 命令行入口
├── tools/
│   ├── probe.py               # 调试探针:抓真实页面 HTML 看 DOM 结构(只存 HTML,不截图)
│   └── make_fixture.py        # 从真实页面提取航班列表子树 → 离线测试 fixture
├── tests/
│   ├── test_compare.py        # 比价引擎单元测试(无需浏览器/网络)
│   ├── test_ctrip_parse.py    # 携程卡片纯文本解析 + fixture 结构回归
│   └── fixtures/ctrip_flight_list_fragment.html   # 真实页面子树(改版后重新生成)
└── artifacts/                 # 运行产物(不入库):latest_query.json、失败现场 HTML
```

> 设计约定:**解析与调试只依赖 DOM/HTML 文本,不解析任何图片/截图**。
> 抓取失败只转储 HTML(便于修选择器);探针工具也不再生成 PNG。

## 架构五层(对齐书里 agent 设计的落地)

| 层 | 本工程对应 | 说明 |
|---|---|---|
| 浏览器会话 | `browser.py` + `open_chrome_debug.sh` | 复用你人工登录的 Chrome(独立 profile),agent 不碰登录 |
| 站点适配 | `adapters/ctrip.py` | 每站点一个 adapter,产出统一 `Flight` 结构 |
| agent 主循环 | `cli.py` 里的搜索流程 | 决定抓哪个渠道、等待、重试、失败转储诊断 |
| 比价决策 | `engine/compare.py` | 确定性逻辑:约束过滤 → 去重 → 价格排序 |
| 界面/安全 | `cli.py` 输出 + README 边界 | 只报告不买票;每步可中断 |

## 快速开始

前置:已安装 [uv](https://docs.astral.sh/uv/)(本机环境由 uv 提供独立 Python 3.12,不依赖系统 CLT)。

```bash
cd /Users/grace/Documents/open_code/flight-agent

# 1) 装依赖(首次,会下载独立 Python 3.12 + playwright)
uv python install 3.12
uv venv
uv pip install -r requirements.txt -r requirements-dev.txt

# 2) 启动“人工登录”Chrome(独立资料目录,不影响你日常 Chrome)
bash scripts/open_chrome_debug.sh
#   在打开的 Chrome 里访问 https://flights.ctrip.com ,登录你的账号(登录态会保存在 .chrome-profile/)
#   确认能正常看到机票搜索页后,保持该 Chrome 开着

# 3) 离线自检(比价引擎 + 携程解析回归,不需要浏览器)
bash scripts/test.sh

# 4) 跑一次真实比价查询
uv run python -m flight_agent.cli \
  --dep 北京 --arr 上海 --date 2026-09-05 \
  --max-stops 0 --cabin 经济 --top 10

# 常见参数
#   --dep-after 08:00 --arr-before 22:00   出发/到达时刻窗(默认全天)
#   --max-stops 1                           允许 1 次中转
#   --dump                                    抓取失败时把 HTML 存到 artifacts/ 便于修选择器
#   --self-test                               不连浏览器,跑一次引擎示例
```

> uv 缓存:本机 `~/.cache` 可能无写权限,`scripts/test.sh` 会把 uv 缓存指到工程内
> `.uv-cache/`;直接敲 `uv run` 前也建议 `export UV_CACHE_DIR="$PWD/.uv-cache"`。

输出:终端打印**符合约束的航班按价格升序**的表格 + 最低价推荐;同时把结构化结果写入 `artifacts/latest_query.json`。

## 比价口径与约束(可在命令行覆盖,默认值见 `config.py`)

- 默认**只看直飞**(`--max-stops 0`);看中转改 1
- 默认**经济舱**
- 默认**全天时刻**(用 `--dep-after/--arr-before` 限制)
- “最低价”= 满足上述约束的航班中,含税价最低;同航班多来源时取最低价(当前单渠道,去重逻辑已为多渠道就绪)

## 已知限制与路线图

- [x] 卡片定位限定真实列表容器(`.flight-list .flight-item`),不再误抓头部下拉等同类名节点
- [x] 首屏常见“空占位卡片”:自动跳过,并内置**滚动懒加载**多轮拉取(需真实 Chrome 联调确认拉满)
- [x] 卡片解析拆成纯文本函数 `flight_from_card_text()`,可用离线 fixture 回归 —— 改选择器不用每次开浏览器
- [x] 不再生成/依赖任何截图,失败只转储 HTML
- [ ] 携程页面结构变动仍会导致抽取失败 —— 流程:改 `adapters/ctrip.py` 选择器 → `tools/probe.py` 抓新页 → `tools/make_fixture.py` 刷新 fixture;其它层不受影响
- [ ] 一些低价可能被网站折叠(“更多低价”),首版只抓列表主区
- [ ] 滚动懒加载的收尾验证(真实 Chrome 联调一轮,确认列表能拉满)
- [ ] 多渠道:加一个继承 `SiteAdapter` 的类即可(去哪儿/航司官网…)
- [ ] 持续监控:同一查询按天/小时跑,积累价格历史(二期)
- [ ] 登录墙/滑块等风控:真实登录 + 低频人工节奏缓解;不承诺绕过任何验证码

## 使用边界

本项目用于个人低频购票比价参考。请遵守各网站服务条款;不要改造成高并发爬虫或商业用途。所有支付动作保留给你本人人工完成。
