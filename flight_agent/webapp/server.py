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
import sys
import threading
import time
import urllib.request
import webbrowser
from dataclasses import replace
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from flight_agent import __version__
from flight_agent.browser import BrowserSession
from flight_agent.city_codes import CITY_CODES
from flight_agent.config import LLMSettings, Settings
from flight_agent.engine.compare import recommend
from flight_agent.engine.search import search_sites
from flight_agent.llm import FlightQueryLLM, LLMError, with_overrides
from flight_agent.models import SearchQuery
from flight_agent.query import make_search_query, query_to_dict

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8712

# 各网站的登录页 URL（用于引导用户登录）
LOGIN_URLS = {
    "ctrip": "https://flights.ctrip.com",
    "qunar": "https://flight.qunar.com",
    "tongcheng": "https://www.ly.com/flights/home",
    "fliggy": "https://www.fliggy.com",
}


# --------------------------------------------------------------------------- #
# 工具
def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _make_query(d: dict) -> SearchQuery:
    """兼容旧调用点，实际校验集中在 query 模块。"""
    return make_search_query(d)


def _parse_top(d: dict, settings: Settings) -> int:
    try:
        top = int(d.get("top") or settings.top_n)
    except (TypeError, ValueError):
        top = settings.top_n
    return max(1, min(top, 200))


def _window_text(q: SearchQuery) -> str:
    """时刻约束的人话描述(全天就不啰嗦)。"""
    parts: list[str] = []
    if q.dep_after != "00:00" or q.dep_before != "23:59":
        parts.append(f"出发 {q.dep_after}-{q.dep_before}")
    if q.arr_before != "23:59":
        parts.append(f"到达≤{q.arr_before}")
    return " ".join(parts) if parts else "全天"


# --------------------------------------------------------------------------- #
class TaskManager:
    """后台单线程查询队列。task = {id, state, created, query, summary|result|error}。"""

    def __init__(self, settings: Settings, llm_settings: LLMSettings | None = None):
        self.settings = settings
        self.llm_settings = llm_settings or LLMSettings()
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
        all_sites = bool(payload.get("all_sites"))
        with self._lock:
            self._seq += 1
            tid = f"{_now_stamp()}-{self._seq:02d}"
            task = {
                "id": tid,
                "state": "queued",
                "created": datetime.now().isoformat(timespec="seconds"),
                "query": payload,
                "top_n": top,
                "all_sites": all_sites,
                "query_summary": (
                    f"{q.dep_city} → {q.arr_city}  {q.dep_date_str}  "
                    f"(直飞≤{q.max_stops} | {q.cabin_keyword}舱 | {_window_text(q)})"
                    + ("  [全渠道比价]" if all_sites else "")
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

    def parse_text(self, text: str) -> dict:
        return query_to_dict(FlightQueryLLM(self.llm_settings).parse(text))

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
        settings = replace(
            self.settings,
            top_n=task.get("top_n") or self.settings.top_n,
            # 勾了“全渠道比价”就抓完全部渠道再合并去重,否则维持“成功即停”的回退模式
            stop_after_first_success=not task.get("all_sites"),
        )
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
                    "dep_before": q.dep_before,
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
            site_results = search_sites(session, q, settings)
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
        if path == "/api/login-urls":
            return self._json(200, {"urls": LOGIN_URLS})
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
        if path == "/api/parse":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                payload = json.loads(raw.decode("utf-8") or "{}")
                return self._json(200, {"query": self.manager.parse_text(payload.get("text", ""))})
            except (ValueError, LLMError) as exc:
                return self._json(400, {"error": str(exc)})
            except json.JSONDecodeError:
                return self._json(400, {"error": "请求体不是合法 JSON"})
        if path == "/api/open-login":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                payload = json.loads(raw.decode("utf-8") or "{}")
                site = payload.get("site", "")
                if site not in LOGIN_URLS:
                    return self._json(400, {"error": f"不支持的网站: {site}"})
                # 在已连接的 Chrome 中打开登录页
                try:
                    with BrowserSession.connect(self.settings.cdp_host, self.settings.cdp_port) as session:
                        page = session.new_page()
                        page.goto(LOGIN_URLS[site], wait_until="domcontentloaded", timeout=15000)
                        page.bring_to_front()
                    return self._json(200, {"ok": True, "site": site, "url": LOGIN_URLS[site]})
                except Exception as exc:
                    return self._json(500, {"error": f"打开登录页失败: {exc}"})
            except json.JSONDecodeError:
                return self._json(400, {"error": "请求体不是合法 JSON"})
        if path == "/api/shutdown":
            # 先回响应,再在别的线程里关服务器(否则响应发不出去)
            self._json(200, {"ok": True, "message": "服务正在退出"})
            threading.Thread(target=self._shutdown_server, daemon=True).start()
            return
        self._json(404, {"error": "not found"})

    def _shutdown_server(self) -> None:
        time.sleep(0.25)
        srv = getattr(type(self), "httpd", None)
        if srv is not None:
            srv.shutdown()

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
            "llm_model": self.manager.llm_settings.model,
            "llm_configured": bool(self.manager.llm_settings.api_key),
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
def build_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    settings: Settings | None = None,
    llm_settings: LLMSettings | None = None,
) -> ThreadingHTTPServer:
    """构造(但不启动)HTTP 服务;供命令行入口和打包后的 .app 启动器复用。"""
    settings = settings or Settings()
    manager = TaskManager(settings, llm_settings)

    class BoundHandler(Handler):
        pass

    BoundHandler.manager = manager  # noqa: 类体不闭合外层作用域,改用赋值注入
    BoundHandler.settings = settings
    httpd = ThreadingHTTPServer((host, port), BoundHandler)
    BoundHandler.httpd = httpd      # /api/shutdown 需要拿到 server 才能关自己
    return httpd


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="flight-agent-web", description=__doc__)
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    p.add_argument("--cdp-port", type=int, default=9222, help="抓取用的 Chrome 调试端口")
    p.add_argument("--sites", help="渠道顺序，逗号分隔；默认 ctrip,qunar")
    p.add_argument("--all-sites", action="store_true", help="抓取全部渠道")
    p.add_argument("--qunar-pages", type=int, help="去哪儿翻页数(默认 3,每页 20 条)")
    p.add_argument("--model", help="覆盖 FLIGHT_AGENT_LLM_MODEL")
    p.add_argument("--llm-base-url", help="覆盖 FLIGHT_AGENT_LLM_BASE_URL")
    args = p.parse_args(argv)

    defaults = Settings()
    sites = tuple(x.strip().lower() for x in (args.sites or ",".join(defaults.sites)).split(",") if x.strip())
    settings = replace(defaults, sites=sites, stop_after_first_success=not args.all_sites,
                       cdp_port=args.cdp_port,
                       qunar_max_pages=args.qunar_pages or defaults.qunar_max_pages)
    llm_settings = with_overrides(LLMSettings(), model=args.model, base_url=args.llm_base_url)

    try:
        httpd = build_server(args.host, args.port, settings, llm_settings)
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
