# flight-agent — 机票自动比价·最低价筛选 Agent

人工提前登录机票网站;agent 复用该登录态操作真实 Chrome,抓取航班价格,按你的约束(直飞/舱位/时刻窗)过滤排序,最终推荐最低价航班。**Agent 只查询与推荐,绝不代替你下单/支付。**

当前实现使用 Python + Playwright，默认先查**携程**，没有拿到有效价格时自动回退到**去哪儿**。DeepSeek 只用于理解自然语言查询，可替换为任意 OpenAI 兼容模型；航班字段始终从网页 DOM/HTML 文本解析，不使用截图或 OCR。

## 目录结构

```
flight-agent/
├── scripts/
│   ├── bootstrap.sh           # 一键准备 Python 3.12 环境(幂等;修悬空软链/架构不符/Gatekeeper)
│   ├── open_chrome_debug.sh   # 用独立资料目录启动带调试端口的 Chrome(登录态保存在 .chrome-profile/)
│   ├── build_macos_app.sh     # 打包 .app + DMG(可分发给别人)
│   └── test.sh                # 一键离线自检(引擎 + 各渠道解析回归,uv 缓存指向工程内 .uv-cache/)
├── packaging/
│   ├── flight-agent.spec      # PyInstaller 打包配置(含 Playwright 驱动收集说明)
│   ├── flight-agent.icns      # App 图标(由 tools/make_icon.py 生成)
│   └── DMG-ReadMe.txt         # 随 DMG 发出去的「首次打开必读」
├── flight_agent/
│   ├── launcher.py            # 桌面版入口:拉起 Chrome + 起服务 + 开界面(双击 .app 跑的就是它)
│   ├── browser.py             # 浏览器会话层:CDP 附加你已登录的 Chrome
│   ├── models.py              # 航班/查询/结果 数据模型
│   ├── config.py              # 运行配置(约束默认值、滚动/等待参数都在这里)
│   ├── query.py               # CLI/Web/模型共用的查询校验与归一化
│   ├── llm.py                 # DeepSeek/OpenAI 兼容的自然语言参数提取
│   ├── city_codes.py          # 城市 → 携程 IATA 城市码映射
│   ├── adapters/
│   │   ├── base.py            # 站点适配器抽象接口 + 公共解析工具
│   │   ├── ctrip.py           # 携程适配器
│   │   └── qunar.py           # 去哪儿适配器(价格只读 DOM 属性,见下文“滚动数字轮盘”)
│   ├── engine/
│   │   ├── compare.py         # 约束过滤/去重/排序
│   │   └── search.py          # 渠道顺序、失败回退和全渠道抓取
│   ├── webapp/
│   │   ├── server.py          # 本地网页版:HTTP 服务 + 后台查询队列 + 历史存档
│   │   └── static/index.html  # 中文 GUI(表单/结果表格/历史,零外部依赖)
│   └── cli.py                 # 命令行入口
├── tools/
│   ├── probe.py               # 调试探针:抓真实页面 HTML 看 DOM 结构(只存 HTML,不截图)
│   ├── make_fixture.py        # 从真实页面提取航班列表子树 → 离线测试 fixture
│   └── make_icon.py           # 纯标准库绘制 App 图标 → packaging/flight-agent.icns
├── tests/
│   ├── test_compare.py        # 比价引擎单元测试(约束/去重/排序,无需浏览器)
│   ├── test_ctrip_parse.py    # 携程卡片纯文本解析 + fixture 结构回归
│   ├── test_qunar_parse.py    # 去哪儿解析 + 价格属性(fix_price / aria-label)回归
│   ├── test_query.py          # 查询参数校验与归一化
│   ├── test_llm.py            # 自然语言 → 查询(桩掉 HTTP)
│   ├── test_search.py         # 渠道顺序与失败回退
│   ├── test_webapp_gui.py     # GUI 静态检查 + 用真实 Chrome 解析内联 JS(防“整页脚本挂掉”)
│   └── fixtures/              # 真实页面子树:ctrip_flight_list_fragment.html、qunar_flight_cards.html
└── artifacts/                 # 运行产物(不入库):latest_query.json、失败现场 HTML
```

> 设计约定:**解析与调试只依赖 DOM/HTML 文本,不解析任何图片/截图**。
> 抓取失败只转储 HTML(便于修选择器);探针工具也不再生成 PNG。

## 架构五层(对齐书里 agent 设计的落地)

| 层 | 本工程对应 | 说明 |
|---|---|---|
| 浏览器会话 | `browser.py` + `open_chrome_debug.sh` | 复用你人工登录的 Chrome(独立 profile),agent 不碰登录 |
| 需求理解 | `llm.py` + `query.py` | 模型提取参数，确定性代码校验，不让模型参与价格识别 |
| 站点适配 | `adapters/ctrip.py` + `adapters/qunar.py` | 每站点一个 adapter，产出统一 `Flight` 结构 |
| agent 主循环 | `engine/search.py` | 决定抓哪个渠道、失败回退、失败转储诊断 |
| 比价决策 | `engine/compare.py` | 确定性逻辑:约束过滤 → 去重 → 价格排序 |
| 界面/安全 | `cli.py` 输出 + README 边界 | 只报告不买票;每步可中断 |

## 快速开始

```bash
cd /Users/wangwt/Documents/opencode/flight-agent

# 1) 准备环境(幂等,可重复跑):建 .venv、装依赖、修 macOS 各种坑
bash scripts/bootstrap.sh

# 可选：启用自然语言查询（不要把密钥写进仓库）
export FLIGHT_AGENT_LLM_API_KEY="你的 DeepSeek API Key"
export FLIGHT_AGENT_LLM_MODEL="deepseek-chat"
export FLIGHT_AGENT_LLM_BASE_URL="https://api.deepseek.com"

# 2) 启动“人工登录”Chrome(独立资料目录,不影响你日常 Chrome)
bash scripts/open_chrome_debug.sh
#   在打开的 Chrome 里登录机票网站,登录态保存在 .chrome-profile/
#   注意:携程不登录也能出列表;**去哪儿必须登录**,否则页面只会返回“暂无符合条件的机票信息”
#   受限沙箱/CI 里由 agent 代跑时改用: CHROME_NO_SANDBOX=1 bash scripts/open_chrome_debug.sh

# 3) 离线自检(比价引擎 + 各渠道解析回归,不需要网络)
bash scripts/test.sh

# 4) 跑一次真实比价查询(.venv 已建好,不需要 uv 在 PATH 里)
.venv/bin/python -m flight_agent.cli \
  --dep 北京 --arr 上海 --date 2026-09-25 \
  --max-stops 0 --cabin 经济 --top 10

# 或让 DeepSeek 从自然语言填写查询条件
.venv/bin/python -m flight_agent.cli --ask "9月25日上午北京到上海，经济舱直飞"

# 常见参数
#   --dep-after 08:00 --arr-before 22:00   出发/到达时刻窗(默认全天)
#   --max-stops 1                              允许 1 次中转
#   --sites qunar                              只查去哪儿
#   --all-sites                                携程和去哪儿都查并合并比价
#   --no-dump                                  抓取失败时不保存诊断 HTML
#   --self-test                                不连浏览器,跑一次引擎示例
```

### 环境踩坑备忘(都已由 `scripts/bootstrap.sh` 自动处理)

1. **`uv` 不在 PATH**:脚本会在工程内 `.uvtools-venv/` 自备一份 uv,不污染系统 Python。
2. **架构不符**:仓库自带的 `.uv-python/` 是 **x86_64** 版,Apple Silicon 上直接报
   `Bad CPU type in executable`(且本机没装 Rosetta)。脚本改为下载 **aarch64** 独立发行版。
3. **悬空软链**:`.venv/bin/python` 原先指向 `/Users/grace/...`(从别人机器拷来的),
   `python -V` 都跑不起来。脚本检测到不可用就整体重建。
4. **Gatekeeper 拦截 greenlet(最坑)**:从 PyPI 装的 `_greenlet*.so` 未签名,会被判定为
   “无法验证的恶意软件”。更麻烦的是系统**缓存了该判定结果**,之后每次 `dlopen` 都卡死在
   `fcntl(F_CHECK_LV)` 上 —— 症状是 `import greenlet` **永久挂起且不报错**(Playwright
   同步 API 依赖它,于是所有抓取都卡住)。解法:清掉 `com.apple.quarantine` 属性,并用
   **源码重编译** greenlet 换掉 cdhash 让系统重新评估。
5. **受限沙箱里起 Chrome**:Chrome 自身沙箱无法嵌套初始化时会报
   `sandbox initialization failed` 并以 `GPU process isn't usable. Goodbye.` 退出。
   你本人终端里手敲**不需要**任何额外参数;若由 agent/CI 代跑,用
   `CHROME_NO_SANDBOX=1 bash scripts/open_chrome_debug.sh`。

> uv 缓存:本机 `~/.cache` 可能无写权限,脚本会把 uv 缓存指到工程内 `.uv-cache/`。

输出:终端打印**符合约束的航班按价格升序**的表格 + 最低价推荐;同时把结构化结果写入 `artifacts/latest_query.json`。

## 桌面版 App(双击即用,可分发给别人)

打包成一个自包含的 `flight-agent.app`:双击就自动把一切准备好 —— 不需要装 Python、
不需要敲命令、不需要先手动开调试 Chrome。

```bash
bash scripts/build_macos_app.sh            # 出 dist/flight-agent.app + dist/*.dmg
bash scripts/build_macos_app.sh --app-only # 只出 .app(调试用,快)
```

产物(已在 macOS 26 / Apple Silicon 实测):

| 文件 | 用途 |
|---|---|
| `dist/flight-agent.app` | 本机直接双击(约 150 MB,已 ad-hoc 签名) |
| `dist/flight-agent-<版本>-arm64.dmg` | 发给别人(约 56 MB,内含 `.app` + 「应用程序」快捷方式 + 首次打开必读) |

**双击后它自己做了什么**(`flight_agent/launcher.py`):

1. 准备 `~/Library/Application Support/flight-agent/`(Chrome 独立资料目录、查询历史、日志);
2. 找到你已装的 Google Chrome,用**独立资料目录**拉起一个带调试端口的实例
   （和你日常 Chrome 完全隔离,登录态存在里面,下次直接复用);
3. 等调试端口就绪 → 启动本地查询服务(只监听 127.0.0.1)→
   打开一个**无地址栏的应用窗口**(看起来就是个原生 App,失败则退回默认浏览器);
4. 重复双击不会开两份:检测到已在运行就直接把界面调出来。

**关于分发的现实约束**(免费方案):

* 只支持 **Apple Silicon**(M 系列);Intel Mac 需要在 x86_64 环境下重新构建。
* **没有 Apple 开发者签名**,所以对方首次打开会被 Gatekeeper 拦一次,
  按 DMG 里「首次打开必读.txt」右键→打开即可放行。若以后拿到开发者账号($99/年),
  把 `scripts/build_macos_app.sh` 里的签名换成 Developer ID 并加 `notarytool` 公证,
  就能做到双击无提示。
* 对方需要**自己装 Google Chrome**(抓取复用真实 Chrome:体积小,且比自带
  Playwright Chromium 更不容易被网站判定为自动化)。
* 想让**去哪儿**也出结果,需要在 App 拉起的那个 Chrome 里登录一次去哪儿;
  没登录时结果里会明确提示「需要登录」,而不是让用户误以为没票。

> 打包要点:PyInstaller 用 `--collect-all playwright`(Node 驱动不是 import 出来的资源)、
> `datas` 里保持 `flight_agent/webapp/static` 的层级(运行时按 `__file__` 找静态文件)、
> 图标由 `tools/make_icon.py` 纯标准库绘制。详见 `packaging/flight-agent.spec`。
> 受限沙箱里 `hdiutil` 可能被拒,构建脚本会自动退回 zip 分发。

## 网页版(开发调试用;日常使用请用上面的桌面版 App)

本地起一个中文网页 GUI:填写行程与筛选条件 → 点查询 → 查看按价格排序的表格与最低价推荐;
查询在后台排队执行,历史自动存档、可点击回看。**零新增依赖**(Python 标准库)。

```bash
cd /Users/wangwt/Documents/opencode/flight-agent
# 前置:先保持“调试 Chrome”开着(见上文第 2 步)
.venv/bin/python -m flight_agent.webapp.server      # 默认 http://127.0.0.1:8712 ,自动开浏览器
# 可选: --port 9000 换端口; --no-open 不自动开浏览器
```

- 可直接填写出发/到达城市、日期、舱位、经停、展示条数和时间范围；
- 勾选 **“全渠道比价(携程 + 去哪儿)”** 就会抓完两个渠道再合并去重,同一航班取两站最低价(耗时约翻倍);不勾选则维持“携程成功即停、失败才回退去哪儿”的快速模式;
- 查询期间页面显示进度,完成后展示:最低价高亮卡 + 全量表格(价格升序/跨天 +1天/中转标注)+ 提示与告警;
- 查询期间页面显示运行状态;关闭前请勿关掉调试 Chrome;
- 结果同时写入 `artifacts/latest_query.json` 与 `artifacts/history/`,历史面板可直接点看;
- 若 Chrome 未连接,页面会给出 `bash scripts/open_chrome_debug.sh` 的提示。

> GUI 是内联 JS,**语法错误会让整页脚本失效**(按钮/健康检查/历史全都不工作,页面永远停在
> “检测 Chrome…”)。所以 `tests/test_webapp_gui.py` 会把这些 `<script>` 交给真实 Chrome 的
> JS 引擎解析;没开调试 Chrome 时该用例自动跳过。改前端后请务必跑 `bash scripts/test.sh`。

## 比价口径与约束(可在命令行覆盖,默认值见 `config.py`)

- 默认**只看直飞**(`--max-stops 0`);看中转改 1
- 默认**经济舱**
- 默认**全天时刻**;用出发时间窗 `--dep-after 18:00 --dep-before 23:00` 和
  `--arr-before` 限制(网页版对应「出发不早于 / 出发不晚于 / 到达不晚于」)
- “最低价”= 满足上述约束的航班中，页面展示的含税价最低；同航班多来源时取最低价
- **比价明细**:同一航班在多个渠道出现时,结果里会列出**每个渠道各自的报价**,
  并标出最低的那个与差价(例如「去哪儿 ¥380 ✓最低 / 携程 ¥500 / 省 ¥120」);
  终端表格末列、网页版「比价明细」列、最低价卡里都能看到,点渠道标签可跳回该渠道结果页复核
- 默认是故障回退模式：携程有有效价格即停止，携程失败才查去哪儿；使用 `--all-sites`
  (网页版勾选“全渠道比价”)才会同时抓取两个来源做跨渠道比价
- **去哪儿翻页**:去哪儿每页只有 20 条且按价格升序,默认翻 **3 页**(`--qunar-pages`)。
  只看第一页会漏掉整段晚班机 —— 实测深圳→北京第一页最晚只到 16:30,所以你筛
  “18:00 以后出发”时它会一条都返回不了
- **跨渠道判重按「航班号 + 起降时刻」**,不按机场名 —— 同一航班两站写法不同
  (携程“大兴国际机场” vs 去哪儿“北京大兴机场”),用机场名判重会重复计数

## 各渠道的数据获取现实(重要)

| 渠道 | 现状 | 备注 |
|---|---|---|
| 携程 | ✅ 稳定,免登录即可出列表 | 渐进渲染 + 滚动懒加载;实测同一查询稳定 127 条 |
| 去哪儿 | ⚠️ **必须登录** | 未登录时页面返回“暂无符合条件的机票信息”,很容易被误判成“解析失败/改版” |

**抓取稳定性的坑(已修,很关键)**:用 CDP 新开的标签页**默认不是活动标签**,
而 Chrome 会限制后台标签页的定时器与渲染 —— 航班列表靠滚动懒加载触发,于是列表
常常只加载出首屏十几条就停住。表现是同一查询**时而 13 条、时而 185 条**,最低价
自然也就时对时错。修法是抓取前 `page.bring_to_front()`(见 `adapters/base.py`
的 `_focus`),实测同一查询连跑 5 次都稳定 127 条。

懒加载“拉满”的判定也不能只看一轮:`base.load_until_stable` 要求**连续多轮**
卡片数不再增长才算加载完 —— 懒加载在两次批量渲染之间有平台期,一轮不涨就收手
会严重漏抓。

**去哪儿价格解析的坑(已修)**:去哪儿把价格渲染成“滚动数字轮盘”(odometer):

```html
<p class="prc" aria-label="报价：400元。…">
  <span class="fix_price" title="400">
    <em class="rel">
      <b><i title="400">4</i><i title="400">0</i><i title="400">7</i></b>  <!-- 隐藏的滚轮数字 -->
      <b title="400">0</b>                                              <!-- 当前可见数字 -->
    </em>
  </span>
</p>
```

`inner_text` 会把滚轮里所有数字按 DOM 顺序拼起来(如 `¥ 374 0 4 0`,甚至 `¥85`),
而滚轮停位每次渲染都不同 —— 于是**文本解析出来的是随机数**。真实金额只存在于
`aria-label` 与 `title` 属性里。现在适配器**只读属性**;读不到就标“无价”,
绝不用文本猜价格(一个错误低价会直接毁掉“最低价推荐”)。

## 已知限制与路线图

- [x] 卡片定位限定真实列表容器(`.flight-list .flight-item`),不再误抓头部下拉等同类名节点
- [x] 首屏常见“空占位卡片”:自动跳过,并内置**滚动懒加载**多轮拉取
- [x] 滚动懒加载已用真实 Chrome 联调:同查询从旧版“2 条”提升到 **79~97 条全价航班**(视页面/缓存浮动),价格/时刻/航司/机场/跨天字段全部正确
- [x] 字段解析改为 **DOM 节点优先**(航班号 `.plane-No`、logo alt、`.depart-box/.arrive-box` 时刻机场、`.flight-price`);inner_text 会漏的卡用**元素 id 兜底**(`airlineNameCA8341_…`)
- [x] 卡片解析保留纯文本函数 `flight_from_card_text()` + 离线 fixture 回归 —— 改选择器不用每次开浏览器
- [x] 不再生成/依赖任何截图,失败只转储 HTML
- [x] DeepSeek 自然语言查询，模型名称、接口地址和密钥均可配置
- [x] 去哪儿适配器 + 携程失败自动回退
- [x] **去哪儿价格改读 `aria-label`/`title` 属性**(滚动数字轮盘的文本是随机数),并加 fixture 回归
- [x] **跨渠道判重改用「航班号 + 起降时刻」**:修复同一航班因两站机场名不同而重复出现(实测 32 行→20 行)
- [x] **修复 GUI 内联 JS 的三元括号错位**(整页脚本失效,页面卡在“检测 Chrome…”),并加真实 JS 引擎语法回归
- [x] 网页版支持“全渠道比价”勾选(原先只有命令行能跨渠道)
- [x] **比价明细**:同一航班列出各渠道报价、最低价标记与省下的差价,可点回渠道结果页复核
- [x] **修复后台标签页限流导致的漏抓**(同一查询时而 13 条时而 185 条 → 稳定 127 条)
- [x] **去哪儿翻页**(默认 3 页 = 60 条),修掉“只看第一页就漏掉整段晚班机”的问题
- [x] 新增**出发时间窗上界**(`--dep-before` / 网页版「出发不晚于」),修掉时间窗设置不生效的存档键不匹配 bug
- [x] 网页版视觉重做:卡片式布局、比价标签、统计条、真实 JS 语法回归
- [x] 修复 `BrowserSession.check()` 返回方法对象而非标题(打印成 `<bound method …>`)
- [x] `scripts/bootstrap.sh`:一键修环境(悬空软链 / x86_64 架构不符 / Gatekeeper 拦 greenlet)
- [x] **桌面版 `flight-agent.app`**:双击自动拉起调试 Chrome + 本地服务 + 应用窗口;一键出 DMG
- [x] 去哪儿未登录时给出「需要登录」的明确提示(而不是让用户误以为没票)
- [ ] 携程页面结构变动仍会导致抽取失败 —— 流程:改 `adapters/ctrip.py` 选择器 → `tools/probe.py` 抓新页 → `tools/make_fixture.py` 刷新 fixture;其它层不受影响
- [ ] 列表里仍有若干固定“非航班”行被跳过(横幅/杂项),不丢真实航班但未逐类识别
- [ ] 一些低价可能被网站折叠(“更多低价”),首版只抓列表主区
- [ ] 去哪儿**依赖登录态**,且默认只翻 3 页(60 条);携程一页能出 100+ 条,两边覆盖仍不对称
- [ ] 同一航班未记录“两站各自的历史最低价”趋势(二期价格监控要用)
- [ ] 分发包只支持 Apple Silicon;Intel Mac 需另建 x86_64 环境构建(脚本尚未自动化)
- [ ] 未做代码签名/公证:对方首次打开需右键放行一次(需要 Apple 开发者账号才能消除)
- [ ] App 目前靠 Dock 图标/Cmd-Q 退出,没有原生菜单栏;也还不能“点 Dock 图标重新唤起窗口”
- [ ] 更多渠道：同程、美团、飞猪或航司官网
- [ ] 持续监控:同一查询按天/小时跑,积累价格历史(二期)
- [ ] 登录墙/滑块等风控:真实登录 + 低频人工节奏缓解;不承诺绕过任何验证码

## 使用边界

本项目用于个人低频购票比价参考。请遵守各网站服务条款;不要改造成高并发爬虫或商业用途。所有支付动作保留给你本人人工完成。
