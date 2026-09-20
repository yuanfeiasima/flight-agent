# flight-agent
<img width="2400" height="1648" alt="12320a76478bb4b6121f680b4cfa2425" src="https://github.com/user-attachments/assets/378fd8cf-64a4-41e5-ba6f-bfcb6d713e4e" />

<img width="2400" height="1648" alt="074d6297ced5dc4a9fc53f89f32f5814" src="https://github.com/user-attachments/assets/cb056dd2-7d0d-4294-ad11-352023ff376d" />


一个基于 Python 的本地机票比价软件。它会复用你在独立 Chrome 窗口中的登录状态，查询携程和去哪儿的航班信息，并按照日期、舱位、经停和时间范围筛选、排序。

软件只负责查询和比价，不会代替用户下单或支付。

## 使用条件

- macOS
- Python 3.10 或更高版本
- Google Chrome

去哪儿需要先登录；携程通常可以直接查询。

## 从源码启动网页

```bash
git clone git@github.com:yuanfeiasima/flight-agent.git
cd flight-agent

# 创建 Python 环境并安装依赖
bash scripts/bootstrap.sh

# 启动专用 Chrome
bash scripts/open_chrome_debug.sh
```

Chrome 启动后，在这个新窗口中登录携程或去哪儿，并保持窗口打开。

然后打开另一个终端，启动本地网页：

```bash
cd flight-agent
.venv/bin/python -m flight_agent.webapp.server
```

浏览器会自动打开网页；如果没有自动打开，请访问：

```text
http://127.0.0.1:8712
```

## 网页使用

1. 填写出发城市、到达城市和出发日期。
2. 根据需要设置舱位、经停、展示条数和时间范围。
3. 如需同时比较携程和去哪儿，勾选“全渠道比价”。
4. 点击“开始查询”，等待结果返回。
5. 查询结果会按价格排序，并显示最低价推荐和各渠道报价。

网页中的“历史查询”可以查看之前保存的查询结果。

## 注意事项

- 查询过程中不要关闭专用 Chrome 窗口。
- 去哪儿没有登录时，可能无法获取航班结果。
- 网页服务只监听本机地址 `127.0.0.1`。
- 查询结果仅供购票参考，最终价格以下单网站显示为准。
