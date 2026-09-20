"""生成 App 图标(不依赖 Pillow / numpy,只用标准库 zlib+struct 写 PNG)。

产物:
  packaging/flight-agent.iconset/   —— 各尺寸 PNG(给 iconutil 用)
  packaging/flight-agent.icns       —— 最终图标(PyInstaller 用它)

画法:圆角矩形(用 SDF 解析抗锯齿)+ 白色纸飞机(扫描线填充 + 超采样)。
一次性构建脚本,画质优先,慢一点无所谓。

用法: .venv/bin/python tools/make_icon.py
"""

from __future__ import annotations

import math
import struct
import subprocess
import sys
import zlib
from pathlib import Path

SIZE = 1024          # 主画布边长
SS = 3               # 纸飞机的每像素超采样倍数

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "packaging"
ICONSET = OUT_DIR / "flight-agent.iconset"

# 背景渐变(上→下):亮蓝 → 深蓝
TOP = (0x4C, 0x86, 0xFF)
BOTTOM = (0x14, 0x3C, 0xB4)
# 纸飞机(归一化坐标,x 向右、y 向下)
PLANE = [(0.18, 0.20), (0.86, 0.50), (0.18, 0.80), (0.40, 0.50)]
PLANE_SHADOW = (0x0E, 0x2E, 0x8C)


# --------------------------------------------------------------------------- #
def _png(width: int, height: int, rgba: bytearray) -> bytes:
    """把 RGBA 原始像素写成 PNG 字节流。"""
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)                       # 每行滤波器类型 0(None)
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def _rounded_rect_coverage(size: int) -> list[float]:
    """圆角矩形覆盖率(SDF 解析抗锯齿),返回 size*size 的 0..1 数组。"""
    radius = size * 0.225
    half = size / 2.0
    inner = half - radius
    cov = [0.0] * (size * size)
    for y in range(size):
        py = abs((y + 0.5) - half)
        dy = max(py - inner, 0.0)
        base = y * size
        for x in range(size):
            px = abs((x + 0.5) - half)
            dx = max(px - inner, 0.0)
            if dx == 0.0 and dy == 0.0:
                cov[base + x] = 1.0
                continue
            dist = math.hypot(dx, dy) - radius
            # dist<0 在内部:把边界 1px 过渡映射成 0..1
            cov[base + x] = min(max(0.5 - dist, 0.0), 1.0)
    return cov


def _polygon_coverage(size: int, points: list[tuple[float, float]]) -> list[float]:
    """多边形覆盖率:扫描线 + 每像素超采样(对 x 方向做分数重叠)。"""
    pts = [(x * size, y * size) for x, y in points]
    n = len(pts)
    cov = [0.0] * (size * size)
    for y in range(size):
        base = y * size
        for s in range(SS):
            sy = y + (s + 0.5) / SS
            xs: list[float] = []
            for i in range(n):
                x1, y1 = pts[i]
                x2, y2 = pts[(i + 1) % n]
                if (y1 <= sy < y2) or (y2 <= sy < y1):
                    xs.append(x1 + (sy - y1) * (x2 - x1) / (y2 - y1))
            if len(xs) < 2:
                continue
            xs.sort()
            for i in range(0, len(xs) - 1, 2):
                xa, xb = xs[i], xs[i + 1]
                ia, ib = int(math.floor(xa)), int(math.ceil(xb))
                for px in range(max(ia, 0), min(ib, size)):
                    left = max(xa, px)
                    right = min(xb, px + 1)
                    if right > left:
                        cov[base + px] += (right - left) / SS
    return cov


def render(size: int = SIZE) -> bytearray:
    mask = _rounded_rect_coverage(size)
    plane = _polygon_coverage(size, PLANE)
    # 折痕:机身中线偏下一点点,做一条更深的窄带,让“纸”更有立体感
    fold = _polygon_coverage(size, [(0.40, 0.50), (0.86, 0.50), (0.40, 0.545)])
    px = bytearray(size * size * 4)
    for y in range(size):
        base = y * size
        t = (y + 0.5) / size
        for x in range(size):
            i = base + x
            a = mask[i]
            if a <= 0.0:
                continue
            g = 0.62 * ((x + 0.5) / size) + 0.38 * t
            r = TOP[0] + (BOTTOM[0] - TOP[0]) * g
            gg = TOP[1] + (BOTTOM[1] - TOP[1]) * g
            b = TOP[2] + (BOTTOM[2] - TOP[2]) * g
            p = min(plane[i], 1.0)
            if p > 0.0:
                f = min(fold[i], 1.0) * 0.55
                pr = 255 - (255 - PLANE_SHADOW[0]) * f
                pg = 255 - (255 - PLANE_SHADOW[1]) * f
                pb = 255 - (255 - PLANE_SHADOW[2]) * f
                r += (pr - r) * p
                gg += (pg - gg) * p
                b += (pb - b) * p
            o = i * 4
            px[o] = int(r + 0.5)
            px[o + 1] = int(gg + 0.5)
            px[o + 2] = int(b + 0.5)
            px[o + 3] = int(a * 255 + 0.5)
    return px


# --------------------------------------------------------------------------- #
def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    iconset_png = ICONSET / "icon_512x512@2x.png"
    ICONSET.mkdir(parents=True, exist_ok=True)

    print(f"绘制 {SIZE}x{SIZE} 图标…")
    data = render(SIZE)
    iconset_png.write_bytes(_png(SIZE, SIZE, data))
    print(f"  已写出 {iconset_png.relative_to(ROOT)}")

    if sys.platform != "darwin":
        print("非 macOS:只生成 PNG,跳过 .icns。")
        return 0

    for size in (16, 32, 128, 256, 512):
        for scale, suffix in ((1, ""), (2, "@2x")):
            target = ICONSET / f"icon_{size}x{size}{suffix}.png"
            subprocess.run(
                ["sips", "-z", str(size * scale), str(size * scale),
                 str(iconset_png), "--out", str(target)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    icns = OUT_DIR / "flight-agent.icns"
    subprocess.run(["iconutil", "-c", "icns", str(ICONSET), "-o", str(icns)], check=True)
    print(f"  已写出 {icns.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
