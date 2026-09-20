# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置:把 flight-agent 打成可直接双击的 macOS .app。

为什么这么配:
- ``--collect-all playwright``:Playwright 的 Node 驱动(driver/node + package/)
  不是 import 出来的资源,PyInstaller 默认收不到,必须整包收集,否则运行时报
  "Executable doesn't exist"。
- ``flight_agent/webapp/static``:GUI 是静态文件,运行时按
  ``Path(__file__).parent/'static'`` 找;PyInstaller 会把冻结模块的 __file__ 指到
  sys._MEIPASS 下,所以 datas 的目标路径必须保持同样的层级。
- 排除 tkinter/unittest 等大头,减小体积。
- 不打包浏览器:抓取用的是用户自己装的 Chrome(体积小,且真实 Chrome 更不容易
  被网站判定为自动化),由 launcher.py 负责拉起。

用法(一般不用手敲,走 scripts/build_macos_app.sh):
  PYINSTALLER_CONFIG_DIR="$PWD/.pyinstaller-cache" \
  .venv/bin/python -m PyInstaller --noconfirm packaging/flight-agent.spec
"""

import os
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent          # noqa: F821 (SPECPATH 由 PyInstaller 注入)
CONSOLE = os.environ.get("FLIGHT_AGENT_APP_CONSOLE") == "1"

datas = [
    (str(ROOT / "flight_agent" / "webapp" / "static"), "flight_agent/webapp/static"),
]

hiddenimports = [
    "flight_agent.launcher",
    "flight_agent.adapters.ctrip",
    "flight_agent.adapters.qunar",
    "flight_agent.engine.search",
    "flight_agent.engine.compare",
    "flight_agent.webapp.server",
]

excludes = [
    "tkinter", "unittest", "pydoc_data", "test", "distutils",
    "numpy", "pandas", "matplotlib", "PIL", "setuptools", "pip",
]

a = Analysis(                                     # noqa: F821
    [str(ROOT / "flight_agent" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)                                 # noqa: F821

exe = EXE(                                        # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="flight-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=CONSOLE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",                          # 只发 Apple Silicon
    codesign_identity=None,                       # 由构建脚本统一 ad-hoc 签名
    entitlements_file=None,
)

coll = COLLECT(                                   # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="flight-agent",
)

app = BUNDLE(                                     # noqa: F821
    coll,
    name="flight-agent.app",
    icon=str(ROOT / "packaging" / "flight-agent.icns"),
    bundle_identifier="com.flightagent.desktop",
    version="0.1.0",
    info_plist={
        "CFBundleName": "flight-agent",
        "CFBundleDisplayName": "flight-agent 机票比价",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "NSHighResolutionCapable": True,
        # 需要读取用户自己装的 Chrome;不联网上传任何数据
        "LSMinimumSystemVersion": "11.0",
        "NSHumanReadableCopyright": "仅供个人比价参考,不下单不支付",
        "LSUIElement": False,
    },
)
