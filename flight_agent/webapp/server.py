"""flight-agent 本地网页版(GUI 外壳,复用它本身的引擎 + 已登录 Chrome)。

用法:
  uv run python -m flight_agent.webapp.server [--port 8712] [--no-open]
然后浏览器打开 http://127.0.0.1:8712(默认自动打开)。

设计:
- 只监听 127.0.0.1,单机自用;
- 查询在后台线程按队列顺序执行(每次仍只开一个页面,低频、贴近人工);
- 结果同时写入 artifacts/latest_query.json 与 artifacts/history/ 供回看;
- 保持“只查询/推荐,不下单支付”的边界,UI 明示人工确认。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import urllib.request
import webbrowser
from dataclasses import replace
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from flight_agent import __version__
from flight_agent.adapters import ADAPTERS
from flight_agent.browser import BrowserSession
from flight_agent.city_codes import CITY_CODES, resolve_code
from flight_agent.config import Settings
from flight_agent.engine.compare import recommend
from flight_agent.models import SearchQuery

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8712
_HHMM_RE = re.compile(r"^\d{1,2}:\d{2}$")


# --------------------------------------------------------------------------- #
# 工具
def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _make_query(d: dict) -> SearchQuery:
    """校验并构造一次查询。失败抛 ValueError(带中文原因)。"""
    dep = (d.get("dep") or "").strip()
    arr = (d.get("arr") or "").strip()
    if not dep or not arr:
        raise ValueError("请填写出发城市与到达城市(中文或三位 IATA 码)。")
    dep_code = resolve_code(dep)
    arr_code = resolve_code(arr)
    if dep_code == arr_code:
        raise ValueError("出发与到达城市不能相同。")

    day_str = (d.get("date") or "").strip()
    if not day_str:
        day_str = (date.today() + timedelta(days=7)).isoformat()
    try:
        dep_date = date.fromisoformat(day_str)
    except ValueError:
        raise ValueError(f"日期格式应为 YYYY-MM-DD,收到: {day_str!r}")
    if dep_date < date.today():
        raise ValueError("出发日期不能早于今天。")

    def _h(key: str, fallback: str) -> str:
        v = (d.get(key) or "").strip() or fallback
        if not _HHMM_RE.match(v):
            raise ValueError(f"时刻 {key}={v!r} 应为 HH:MM 格式。")
        h, m = (int(x) for x in v.split(":"))
        if h > 23 or m > 59:
            raise ValueError(f"时刻 {key}={v!r} 不合法。")
        return f"{h:02d}:{m:02d}"

    raw_stops = d.get("max_stops")
    if raw_stops in (None, ""):
        max_stops = 0
    else:
        try:
            max_stops = int(raw_stops)
        except (TypeError, ValueError):
            raise ValueError("经停上限必须是整数。")
        if max_stops < 0:
            raise ValueError("经停上限不能为负数。")

    cabin = (d.get("cabin") or "经济").strip()
    if not cabin:
        raise ValueError("请选择舱位。")
    return SearchQuery(
        dep_city=dep,
        arr_city=arr,
        dep_code=dep_code,
        arr_code=arr_code,
        dep_date=dep_date,
        max_stops=max_stops,
        cabin_keyword=cabin,
        dep_after=_h("dep_after", "00:00"),
        arr_before=_h("arr_before", "23:59"),
    )


def _parse_top(d: dict, settings: Settings) -> int:
    try:
        top = int(d.get("top") or settings.top_n)
    except (TypeError, ValueError):
        top = settings.top_n
    return max(1, min(top, 200))


# --------------------------------------------------------------------------- #
class TaskManager:
    """后台单线程查询队列。task = {id, state, created, query, summary|result|error}。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._tasks: dict[str, dict] = {}
        self._queue: list[str] = []
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._seq = 0

    # ---------------- 对外 ---------------- #
    def enqueue(self, payload: dict) -> dict:
        q = _make_query(payload)  # 校验失败抛 ValueError
        top = _parse_top(payload, self.settings)
        with self._lock:
            self._seq += 1
            tid = f"{_now_stamp()}-{self._seq:02d}"
            task = {
                "id": tid,
                "state": "queued",
                "created": datetime.now().isoformat(timespec="seconds"),
                "query": payload,
                "top_n": top,
                "query_summary": (
                    f"{q.dep_city} → {q.arr_city}  {q.dep_date_str}  "
                    f"(直飞≤{q.max_stops} | {q.cabin_keyword}舱 | "
                    f"{q.dep_after}~{q.arr_before})"
                ),
                "result": None,
                "error": None,
            }
            self._tasks[tid] = task
            self._queue.append(tid)
        self._wake.set()
        self._start_worker()
        return {"id": tid, "query_summary": task["query_summary"]}

    def get(self, tid: str) -> dict | None:
        with self._lock:
            t = self._tasks.get(tid)
            return dict(t) if t else None

    def recent(self, n: int = 10) -> list[dict]:
        with self._lock:
            items = sorted(
                self._tasks.values(), key=lambda t: t["created"], reverse=True
            )
            return [
                {
                    "id": t["id"],
                    "state": t["state"],
                    "created": t["created"],
                    "query_summary": t["query_summary"],
                    "summary": t.get("summary"),
                    "error": (t.get("error") or "")[:200] if t["state"] == "failed" else None,
                }
                for t in items[:n]
            ]

    # ---------------- 内部 ---------------- #
    def _start_worker(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._worker, name="query-worker", daemon=True
                )
                self._thread.start()

    def _worker(self) -> None:
        while True:
            tid = None
            with self._lock:
                if self._queue:
                    tid = self._queue.pop(0)
                    self._tasks[tid]["state"] = "running"
                    self._tasks[tid]["started"] = datetime.now().isoformat(
                        timespec="seconds"
                    )
            if tid is None:
                self._wake.clear()
                self._wake.wait(2.0)  # 防假醒
                continue
            self._execute(tid)

    def _execute(self, tid: str) -> None:
        task = self._tasks[tid]
        q = _make_query(task["query"])
        settings = replace(self.settings, top_n=task.get("top_n") or self.settings.top_n)
        try:
            result = self._run(q, settings)
            payload = {
                "query": {
                    "dep_city": q.dep_city,
                    "arr_city": q.arr_city,
                    "dep_code": q.dep_code,
                    "arr_code": q.arr_code,
                    "dep_date": q.dep_date_str,
                    "max_stops": q.max_stops,
                    "cabin_keyword": q.cabin_keyword,
                    "dep_after": q.dep_after,
                    "arr_before": q.arr_before,
                },
                "sites": [r.to_dict() for r in result["site_results"]],
                "recommendation": result["rec"].to_dict(),
            }
            self._archive(tid, q, payload)
            best = result["rec"].best
            with self._lock:
                task["state"] = "done"
                task["result"] = payload
                task["finished"] = datetime.now().isoformat(timespec="seconds")
                task["summary"] = {
                    "total": len(result["rec"].ranked),
                    "best_flight": f"{best.flight_no} {best.dep_airport}→{best.arr_airport}"
                    if best
                    else None,
                    "best_price": best.price if best else None,
                }
        except ConnectionError as exc:
            self._fail(task, str(exc))
        except ValueError as exc:
            self._fail(task, str(exc))
        except Exception as exc:  # noqa: BLE001 兜底
            self._fail(task, f"查询失败: {exc}")
        finally:
            self._wake.set()

    def _fail(self, task: dict, msg: str) -> None:
        with self._lock:
            task["state"] = "failed"
            task["error"] = msg
            task["finished"] = datetime.now().isoformat(timespec="seconds")

    # ---------------- 真实查询(复用命令行同款流程) ---------------- #
    def _run(self, q: SearchQuery, settings: Settings) -> dict:
        site_results = []
        with BrowserSession.connect(settings.cdp_host, settings.cdp_port) as session:
            for site in settings.sites:
                adapter_cls = ADAPTERS[site]
                site_results.append(adapter_cls().search(session, q, settings))
        rec = recommend(site_results, q)
        return {"site_results": site_results, "rec": rec}

    def _archive(self, tid: str, q: SearchQuery, payload: dict) -> None:
        out = Path(self.settings.artifacts_dir)
        hist = out / "history"
        hist.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        (out / "latest_query.json").write_text(text, encoding="utf-8")
        (hist / f"{tid}_{q.dep_code.lower()}-{q.arr_code.lower()}.json").write_text(
            text, encoding="utf-8"
        )


# --------------------------------------------------------------------------- #
# 简单 HTTP 服务
class Handler(BaseHTTPRequestHandler):
    manager: TaskManager
    settings: Settings

    # 减少刷屏
    def log_message(self, fmt, *args):  # noqa: A003
        if self.path.startswith("/api/"):
            print(f"[web {time.strftime('%H:%M:%S')}] {fmt % args}")

    # ---------------- 路由 ---------------- #
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            return self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        if path == "/api/health":
            return self._json(200, self._health())
        if path == "/api/history":
            return self._json(200, {"tasks": self.manager.recent(15)})
        if path == "/api/cities":
            return self._json(200, {"cities": sorted(CITY_CODES)})
        if path.startswith("/api/task/"):
            tid = path.rsplit("/", 1)[-1]
            t = self.manager.get(tid)
            if t is None:
                return self._json(404, {"error": "任务不存在"})
            return self._json(200, t)
        self._json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/query":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                payload = json.loads(raw.decode("utf-8") or "{}")
                resp = self.manager.enqueue(payload)
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            except json.JSONDecodeError:
                return self._json(400, {"error": "请求体不是合法 JSON"})
            return self._json(200, resp)
        self._json(404, {"error": "not found"})

    # ---------------- 底层 ---------------- #
    def _health(self) -> dict:
        chrome_ok = False
        detail = ""
        try:
            with urllib.request.urlopen(
                f"http://{self.settings.cdp_host}:{self.settings.cdp_port}/json/version",
                timeout=2,
            ) as r:
                info = json.loads(r.read().decode("utf-8"))
                chrome_ok = bool(info.get("Browser"))
                detail = info.get("Browser", "")
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}"
        return {
            "chrome": chrome_ok,
            "chrome_detail": detail,
            "version": __version__,
            "hint": (
                "请先运行 bash scripts/open_chrome_debug.sh 并保持该 Chrome 打开"
                if not chrome_ok
                else None
            ),
        }

    def _send_file(self, path: Path, ctype: str):
        if not path.is_file():
            return self._json(404, {"error": "not found"})
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="flight-agent-web", description=__doc__)
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = p.parse_args(argv)

    settings = Settings()
    manager = TaskManager(settings)

    class BoundHandler(Handler):
        pass

    BoundHandler.manager = manager  # noqa: 类体不闭合外层作用域,改用赋值注入
    BoundHandler.settings = settings

    try:
        httpd = ThreadingHTTPServer((args.host, args.port), BoundHandler)
    except OSError as exc:
        if exc.errno == 48:  # Address already in use
            print(
                f"\n端口 {args.port} 已被占用 —— 可能网页版已在运行。\n"
                f"  · 直接打开 http://{args.host}:{args.port} 即可;\n"
                f"  · 或另起一个实例用其它端口: --port 9000\n",
                file=sys.stderr,
            )
            return 1
        raise
    url = f"http://{args.host}:{args.port}"
    print("=" * 62)
    print(f" flight-agent 网页版 v{__version__}")
    print(f" 打开: {url}")
    print(" 退出: Ctrl+C(窗口关闭不影响查询,但会断开本页)")
    print(" 提示: 查询需连接调试 Chrome —— 请保持 open_chrome_debug.sh 的窗口开着")
    print("=" * 62)
    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
