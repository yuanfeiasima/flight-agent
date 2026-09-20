"""flight-agent 桌面启动器:双击 .app 后自动准备好一切并打开中文 GUI。

它在启动时依次完成(每一步失败都会用系统弹窗明确告诉用户,而不是静默退出):

1. 准备可写目录 ``~/Library/Application Support/flight-agent/``
   (放 Chrome 独立资料目录、查询历史、日志);
2. 找到你已安装的 **Google Chrome**;
3. 用**独立资料目录**拉起一个带调试端口(CDP)的 Chrome —— 这个资料目录和你的日常
   Chrome 完全隔离,登录态保留在里面,下次启动直接复用;
4. 等 CDP 就绪;
5. 后台启动本地查询服务(只监听 127.0.0.1);
6. 用一个**无地址栏的应用窗口**打开 GUI(失败则退回默认浏览器)。

设计边界:launcher 只负责“把环境准备好 + 开界面”,不碰任何登录与支付逻辑;
抓取仍然完全复用你人工登录的那个 Chrome。
"""

from __future__ import annotations

import json
import os

import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from flight_agent import __version__
from flight_agent.config import Settings
from flight_agent.webapp.server import DEFAULT_HOST, DEFAULT_PORT, build_server

APP_NAME = "flight-agent"
DISPLAY_NAME = "flight-agent 机票比价"
DEFAULT_CDP_PORT = 9222

_CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


# --------------------------------------------------------------------------- #
# 路径 / 日志
def app_support_dir() -> Path:
    """可写数据目录(冻结后不能写进 .app 内部)。"""
    override = os.environ.get("FLIGHT_AGENT_HOME")
    base = Path(override) if override else (
        Path.home() / "Library" / "Application Support" / APP_NAME
    )
    base.mkdir(parents=True, exist_ok=True)
    return base


def log_path() -> Path:
    return app_support_dir() / "launcher.log"


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        with log_path().open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001 日志失败不能影响启动
        pass
    if not getattr(sys, "frozen", False):      # 源码运行时同时打到终端
        print(line, flush=True)


def alert(title: str, message: str) -> None:
    """没有终端时用系统弹窗告知用户(打包成 .app 后尤其重要)。"""
    log(f"ALERT {title}: {message}")
    if sys.platform != "darwin":
        return
    try:
        script = (
            f'display dialog {json.dumps(message)} with title {json.dumps(title)} '
            'buttons {"好"} default button 1 with icon caution'
        )
        subprocess.run(["osascript", "-e", script], timeout=120, check=False)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Chrome / CDP
def find_chrome() -> Path | None:
    for raw in _CHROME_CANDIDATES:
        path = Path(raw).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def cdp_alive(port: int = DEFAULT_CDP_PORT, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=timeout
        ) as resp:
            return bool(json.loads(resp.read().decode("utf-8")).get("Browser"))
    except Exception:  # noqa: BLE001
        return False


def chrome_flags(port: int) -> list[str]:
    flags = [
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,MediaRouter",
    ]
    # 仅用于受限沙箱/CI 里代跑(Chrome 自身沙箱无法嵌套时会启动失败)。
    # 普通用户双击启动**不需要**,保持默认即可。
    if os.environ.get("FLIGHT_AGENT_CHROME_NO_SANDBOX") == "1":
        flags += ["--no-sandbox", "--disable-gpu"]
        log("警告:FLIGHT_AGENT_CHROME_NO_SANDBOX=1,已关闭 Chrome 自身沙箱")
    return flags


def launch_chrome(chrome: Path, profile: Path, port: int, url: str) -> None:
    profile.mkdir(parents=True, exist_ok=True)
    cmd = [str(chrome), f"--user-data-dir={profile}", *chrome_flags(port), url]
    log("启动调试 Chrome: " + " ".join(cmd))
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,      # 与 .app 生命周期解绑:退出 App 不关这个 Chrome
        cwd=str(Path.home()),
    )


def wait_for_cdp(port: int, timeout_s: float = 60.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if cdp_alive(port):
            return True
        time.sleep(0.5)
    return False


def ensure_debug_chrome(profile: Path, port: int) -> tuple[bool, str]:
    """返回 (是否就绪, 说明)。已经有一个调试 Chrome 在跑就直接复用。"""
    if cdp_alive(port):
        log(f"复用已在运行的调试 Chrome(端口 {port})")
        return True, "reused"
    chrome = find_chrome()
    if chrome is None:
        return False, "missing-chrome"
    launch_chrome(chrome, profile, port, "https://flights.ctrip.com")
    return (True, "launched") if wait_for_cdp(port) else (False, "cdp-timeout")


# --------------------------------------------------------------------------- #
# GUI
def close_stale_gui(port: int, cdp_port: int = DEFAULT_CDP_PORT) -> int:
    """关掉上一次遗留的旧界面窗口。

    抓取用的 Chrome 在 App 退出后会保留(为了复用登录态),它上面那个界面窗口
    却已经连不上服务了。不清掉的话,每次重新启动都会多留一个死窗口。
    """
    base = f"http://127.0.0.1:{port}/"
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{cdp_port}/json/list", timeout=3
        ) as resp:
            targets = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 Chrome 没起来也不用管
        return 0
    closed = 0
    for t in targets:
        if t.get("type") != "page" or not str(t.get("url", "")).startswith(base):
            continue
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{cdp_port}/json/close/{t['id']}", timeout=3
            ).read()
            closed += 1
        except Exception:  # noqa: BLE001
            pass
    if closed:
        log(f"已关闭 {closed} 个上次遗留的旧界面窗口")
    return closed


def open_gui(url: str, profile: Path, chrome: Path | None) -> str:
    """优先用 Chrome 的“应用窗口”(无地址栏,像原生 App);失败退回默认浏览器。"""
    if chrome is not None:
        try:
            subprocess.Popen(
                [str(chrome), f"--user-data-dir={profile}", *chrome_flags(DEFAULT_CDP_PORT),
                 "--window-size=1560,1020", f"--app={url}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                cwd=str(Path.home()),
            )
            time.sleep(1.5)
            return "app-window"
        except Exception as exc:  # noqa: BLE001
            log(f"应用窗口方式打开失败,退回默认浏览器: {exc}")
    try:
        webbrowser.open(url)
        return "browser"
    except Exception as exc:  # noqa: BLE001
        log(f"打开浏览器失败: {exc}")
        return "failed"


# --------------------------------------------------------------------------- #
def pick_port(host: str, preferred: int) -> tuple[int, bool]:
    """返回 (可用端口, 该端口上是否已有本程序实例在跑)。"""
    def free(p: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, p))
                return True
            except OSError:
                return False

    if free(preferred):
        return preferred, False
    # 端口被占:若占用者就是我们自己的服务,直接复用它(避免开两份)
    try:
        with urllib.request.urlopen(
            f"http://{host}:{preferred}/api/health", timeout=2
        ) as resp:
            if json.loads(resp.read().decode("utf-8")).get("version"):
                return preferred, True
    except Exception:  # noqa: BLE001
        pass
    for cand in range(preferred + 1, preferred + 20):
        if free(cand):
            return cand, False
    return 0, False


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    port_override = None
    if "--port" in argv:
        try:
            port_override = int(argv[argv.index("--port") + 1])
        except (IndexError, ValueError):
            port_override = None
    no_open = "--no-open" in argv
    no_chrome = "--no-chrome" in argv

    home = app_support_dir()
    log("=" * 60)
    log(f"{DISPLAY_NAME} v{__version__} 启动(frozen={getattr(sys, 'frozen', False)})")
    log(f"数据目录: {home}")

    settings = Settings(
        cdp_host="127.0.0.1",
        cdp_port=DEFAULT_CDP_PORT,
        artifacts_dir=str(home / "artifacts"),   # 结果/历史写到用户可写目录
    )

    # —— 1) 调试 Chrome ——
    chrome = find_chrome()
    if no_chrome:
        log("--no-chrome:跳过 Chrome 准备")
    else:
        ok, why = ensure_debug_chrome(home / "chrome-profile", DEFAULT_CDP_PORT)
        if not ok:
            if why == "missing-chrome":
                alert(
                    DISPLAY_NAME,
                    "没有找到 Google Chrome。\n\n"
                    "请先安装 Google Chrome(https://www.google.com/chrome/),"
                    "然后重新打开本应用。",
                )
            else:
                alert(
                    DISPLAY_NAME,
                    "Chrome 已在启动,但调试端口没有就绪(可能被安全软件拦截)。\n\n"
                    f"可查看日志:{log_path()}",
                )
            return 1

    # —— 2) 本地服务 ——
    host = DEFAULT_HOST
    port, already = pick_port(host, port_override or DEFAULT_PORT)
    if port == 0:
        alert(DISPLAY_NAME, "找不到可用的本地端口,无法启动界面。")
        return 1
    url = f"http://{host}:{port}"

    if already:
        log(f"{url} 上已有本程序在运行,直接打开界面")
        if not no_open:
            open_gui(url, home / "chrome-profile", chrome)
        return 0

    try:
        httpd = build_server(host, port, settings)
    except OSError as exc:
        alert(DISPLAY_NAME, f"本地服务启动失败:{exc}")
        return 1

    log(f"本地服务已就绪: {url}")
    # —— 3) 打开界面 ——
    if not no_open:
        close_stale_gui(port)
        how = open_gui(url, home / "chrome-profile", chrome)
        log(f"界面打开方式: {how}")
        if how == "failed":
            alert(DISPLAY_NAME, f"无法自动打开界面,请手动在浏览器访问:{url}")

    # —— 4) 常驻;收到退出信号或 /api/shutdown 后收尾 ——
    stop = threading.Event()

    def _bye(signum, _frame):
        log(f"收到信号 {signum},准备退出")
        stop.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _bye)
        except (ValueError, OSError):  # noqa: BLE001
            pass

    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
        log("已退出(调试 Chrome 保持运行;下次启动会直接复用)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
