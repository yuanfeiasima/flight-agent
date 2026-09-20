#!/usr/bin/env bash
# 把 flight-agent 打成可双击的 macOS .app,并生成可分发的 DMG。
#
# 用法:
#   bash scripts/build_macos_app.sh              # 出 .app + .dmg
#   bash scripts/build_macos_app.sh --app-only   # 只出 .app(调试用,快)
#
# 产物:
#   dist/flight-agent.app        可直接双击(也已 ad-hoc 签名)
#   dist/flight-agent-<版本>-arm64.dmg   发给别人用
#
# 说明:
# * 只构建 **arm64**(Apple Silicon)。Intel Mac 需要另建 x86_64 环境后重跑。
# * 采用 **ad-hoc 签名**:免费,但对方首次打开需右键→打开(见 DMG 内说明)。
#   若以后拿到 Apple 开发者账号,把 SIGN_ID 换成
#   "Developer ID Application: xxx (TEAMID)" 并追加 notarytool 公证即可。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

APP_ONLY=0
[ "${1:-}" = "--app-only" ] && APP_ONLY=1

# PyInstaller 默认往 ~/Library/Application Support 写缓存,这里固定到工程内
export PYINSTALLER_CONFIG_DIR="$ROOT/.pyinstaller-cache"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$ROOT/.uv-cache}"
mkdir -p "$PYINSTALLER_CONFIG_DIR"

VERSION="$(.venv/bin/python -c 'import flight_agent; print(flight_agent.__version__)')"
ARCH="$(uname -m)"
if [ "$ARCH" != "arm64" ]; then
  echo "警告:当前机器是 $ARCH,而本脚本按 arm64 打包。" >&2
fi

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- 前置检查 ----
say "前置检查"
[ -x .venv/bin/python ] || { echo "缺少 .venv,请先运行: bash scripts/bootstrap.sh" >&2; exit 1; }
.venv/bin/python -c 'import PyInstaller' 2>/dev/null || {
  echo "缺少 PyInstaller,正在安装…"
  .uvtools-venv/bin/uv pip install --python .venv/bin/python pyinstaller
}
echo "版本: $VERSION · 架构: $ARCH · Python: $(.venv/bin/python -V 2>&1)"

# ---------------------------------------------------------------- 图标 --------
say "准备图标"
if [ ! -f packaging/flight-agent.icns ]; then
  .venv/bin/python tools/make_icon.py
else
  echo "已存在 packaging/flight-agent.icns(想重画就删掉它再跑)"
fi

# ---------------------------------------------------------------- 打包 --------
say "PyInstaller 打包(--windowed,含 Playwright Node 驱动)"
rm -rf build dist/flight-agent.app
.venv/bin/python -m PyInstaller --noconfirm --clean packaging/flight-agent.spec 2>&1 | tail -5

APP="dist/flight-agent.app"
[ -d "$APP" ] || { echo "打包失败:$APP 不存在" >&2; exit 1; }
rm -rf dist/flight-agent        # PyInstaller 的 onedir 中间产物,已被装进 .app
echo "已生成 $APP($(du -sh "$APP" | cut -f1))"

# ---------------------------------------------------------------- 签名 --------
# ad-hoc 签名:让应用在“未公证”前提下仍能通过基本的完整性校验。
# --deep 会把内部所有 Mach-O(含 Python 与 Playwright 的 node)一起签掉。
say "ad-hoc 签名"
codesign --force --deep --sign - --timestamp=none "$APP" 2>&1 | tail -3 || true
codesign --verify --deep --verbose=1 "$APP" 2>&1 | tail -3 || true

# 清掉本机构建产生的隔离属性,避免本地测试时被 Gatekeeper 反复拦
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true

# ---------------------------------------------------------------- 冒烟 ----
say "冒烟测试(--help 级别的启动自检)"
# 用独立 HOME,避免污染真实用户目录;--no-open 不弹窗
SMOKE_HOME="$ROOT/.app-smoke"
rm -rf "$SMOKE_HOME"; mkdir -p "$SMOKE_HOME"
if FLIGHT_AGENT_HOME="$SMOKE_HOME/data" \
   "$APP/Contents/MacOS/flight-agent" --no-open --no-chrome --port 8791 &
then
  sleep 6
  if curl -sS --max-time 5 http://127.0.0.1:8791/api/health >/dev/null 2>&1; then
    echo "冒烟通过:冻结后的服务能正常起来并响应 /api/health ✓"
    curl -sS --max-time 5 -X POST http://127.0.0.1:8791/api/shutdown >/dev/null 2>&1 || true
  else
    echo "冒烟失败:服务没有响应,请查看 $SMOKE_HOME/data/launcher.log" >&2
    cat "$SMOKE_HOME/data/launcher.log" 2>/dev/null | tail -20 >&2
    exit 1
  fi
  sleep 2
fi

if [ "$APP_ONLY" = "1" ]; then
  say "完成(仅 .app)"
  echo "双击打开: $ROOT/$APP"
  exit 0
fi

# ---------------------------------------------------------------- DMG ---------
say "制作 DMG"
STAGE="$ROOT/build/dmg"
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/应用程序"
cp packaging/DMG-ReadMe.txt "$STAGE/首次打开必读.txt"

DMG="dist/flight-agent-$VERSION-$ARCH.dmg"
rm -f "$DMG"
# 注意:在受限沙箱里(例如由 agent/CI 代跑)hdiutil 需要挂载磁盘映像设备,
# 会被沙箱拒绝并报 "操作不被允许"。此时用真实终端直接跑本脚本即可,
# 或者改用下面的 zip 分发方式(不需要设备权限)。
if hdiutil create -volname "flight-agent $VERSION" -srcfolder "$STAGE" \
     -ov -format UDZO -fs HFS+ "$DMG" 2>&1 | tail -3; then
  echo "DMG 已生成:$DMG"
else
  echo "hdiutil 失败(受限沙箱常见),退回 zip 分发方式…" >&2
  ZIP="dist/flight-agent-$VERSION-$ARCH.zip"
  rm -f "$ZIP"
  ( cd "$STAGE" && zip -qry "$ROOT/$ZIP" "flight-agent.app" "应用程序" "首次打开必读.txt" )
  DMG="$ZIP"
  echo "ZIP 已生成:$ZIP(对方解压后把 .app 拖进「应用程序」即可)"
fi

say "完成"
cat <<EOF
可直接双击:  $ROOT/dist/flight-agent.app
可分发安装包: $ROOT/$DMG  ($(du -sh "$DMG" | cut -f1))

发给别人时提醒对方(见 DMG 里的「首次打开必读.txt」):
  * 需要 Apple Silicon 的 Mac + 已安装 Google Chrome
  * 首次打开要「右键 → 打开」放行一次(未购买 Apple 签名,属正常提示)
  * 想去哪儿出结果,需要在应用拉起的那个 Chrome 里登录一次
EOF
