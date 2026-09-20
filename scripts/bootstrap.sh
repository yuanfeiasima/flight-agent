#!/usr/bin/env bash
# 一键准备好本工程的 Python 3.12 运行环境(幂等,可重复执行)。
#
# 为什么需要它:本仓库是从另一台机器拷过来的,`.venv/bin/python` 是一个指向
# `/Users/grace/...` 的**悬空软链**;工程内自带的 `.uv-python/` 又是 **x86_64** 版本,
# 在 Apple Silicon 上会报 "Bad CPU type in executable"。所以这里统一重建:
#
#   1. 找一个 arm64 的 Python 3.12(优先 uv 下载的独立发行版);
#   2. 重建 .venv 并装依赖;
#   3. 清理 macOS 隔离属性,并把 greenlet 用源码重编译。
#
# 关于第 3 步(重要):从 PyPI 装下来的 greenlet 扩展是未签名二进制,会被 Gatekeeper
# 判定为"无法验证的恶意软件"。更麻烦的是 macOS 会把该文件的判定结果缓存下来,之后
# **每次 dlopen 都会卡死在 fcntl(F_CHECK_LV) 上**(表现为 `import greenlet` 永久挂起,
# 不报错)。用 pip 从源码重编译后 cdhash 变了,系统会重新评估并放行。
#
# 用法: bash scripts/bootstrap.sh
set -euo pipefail
cd "$(dirname "$0")/.."

UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.uv-cache}"
UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$PWD/.uv-python}"
export UV_CACHE_DIR UV_PYTHON_INSTALL_DIR
export UV_PYTHON_BIN_DIR="${UV_PYTHON_BIN_DIR:-$UV_PYTHON_INSTALL_DIR/bin}"
PY_MINOR="3.12"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- uv ----------
find_uv() {
  if command -v uv >/dev/null 2>&1; then command -v uv; return 0; fi
  if [ -x .uvtools-venv/bin/uv ]; then echo "$PWD/.uvtools-venv/bin/uv"; return 0; fi
  return 1
}

UV="$(find_uv || true)"
if [ -z "$UV" ]; then
  say "未找到 uv,先用系统 Python 装一个到 .uvtools-venv/(工程内,不污染系统)"
  /usr/bin/python3 -m venv .uvtools-venv
  .uvtools-venv/bin/pip install -q --upgrade pip
  .uvtools-venv/bin/pip install -q uv
  UV="$PWD/.uvtools-venv/bin/uv"
fi
echo "uv: $UV ($("$UV" --version))"

# ------------------------------------------------------------- Python ---------
ARCH="$(uname -m)"
say "准备 Python $PY_MINOR(本机架构: $ARCH)"

# 找一个"能真正跑起来"的解释器:必须匹配本机架构
PY=""
probe() {  # probe <path> -> 0 表示可用
  [ -x "$1" ] || return 1
  "$1" -c "import sys; assert sys.version_info[:2] >= (3,10)" >/dev/null 2>&1
}
for cand in \
  "$UV_PYTHON_INSTALL_DIR/cpython-$PY_MINOR-macos-$([ "$ARCH" = arm64 ] && echo aarch64 || echo x86_64)-none/bin/python3.12" \
  "$UV_PYTHON_INSTALL_DIR"/cpython-$PY_MINOR.*-"$([ "$ARCH" = arm64 ] && echo aarch64 || echo x86_64)"-none/bin/python3.12
do
  if probe "$cand"; then PY="$cand"; break; fi
done

if [ -z "$PY" ]; then
  echo "本机没有可用的 $ARCH Python $PY_MINOR,用 uv 下载一个独立发行版…"
  "$UV" python install "cpython-$PY_MINOR-macos-$([ "$ARCH" = arm64 ] && echo aarch64 || echo x86_64)-none"
  for cand in "$UV_PYTHON_INSTALL_DIR"/cpython-$PY_MINOR.*-"$([ "$ARCH" = arm64 ] && echo aarch64 || echo x86_64)"-none/bin/python3.12; do
    if probe "$cand"; then PY="$cand"; break; fi
  done
fi
[ -n "$PY" ] || { echo "仍找不到可用的 Python $PY_MINOR,请手动安装后重试。" >&2; exit 1; }
echo "python: $PY ($("$PY" -V 2>&1))"

# ---------------------------------------------------------------- venv --------
say "重建 .venv 并安装依赖"
if [ -x .venv/bin/python ] && ! probe .venv/bin/python; then
  echo "检测到现有的 .venv/bin/python 不可用(多半是悬空软链),删除重建。"
  rm -rf .venv
fi
if [ ! -x .venv/bin/python ]; then
  "$UV" venv --python "$PY" .venv
fi
"$UV" pip install --python .venv/bin/python -r requirements.txt -r requirements-dev.txt

# ------------------------------------------------- macOS 隔离属性 / greenlet ---
if [ "$(uname -s)" = "Darwin" ]; then
  say "清理 macOS 隔离属性(quarantine),避免 Gatekeeper 拦截未签名扩展"
  for d in .venv .uv-python; do
    [ -d "$d" ] || continue
    find "$d" -print0 2>/dev/null | xargs -0 xattr -d com.apple.quarantine 2>/dev/null || true
  done

  # greenlet 被系统缓存为"已拦截"时,import 会永久挂起,必须源码重编译换 cdhash
  if ! .venv/bin/python -c "import greenlet" >/dev/null 2>&1; then
    say "greenlet 无法加载(被 Gatekeeper 拦截),从源码重编译"
    "$UV" pip install --python .venv/bin/python --no-binary greenlet --no-cache \
      --force-reinstall greenlet
  fi
  .venv/bin/python -c "import greenlet" >/dev/null 2>&1 \
    && echo "greenlet 可用 ✓" \
    || { echo "greenlet 仍不可用,请检查 macOS 安全性设置。" >&2; exit 1; }
fi

# ---------------------------------------------------------------- 验收 --------
say "验收:导入依赖 + 跑离线自检"
.venv/bin/python -c "import sys, pytest, playwright, greenlet; print('依赖导入 OK ·', sys.version.split()[0])"
.venv/bin/python -m pytest tests/ -q

cat <<'EOF'

环境就绪 ✓  以后直接用(不需要 uv 在 PATH 里):
  .venv/bin/python -m flight_agent.cli --dep 北京 --arr 上海 --date 2026-09-25
  .venv/bin/python -m flight_agent.webapp.server      # 网页版
  bash scripts/test.sh                                # 离线自检
EOF
