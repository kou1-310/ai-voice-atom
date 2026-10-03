"""ケースのプレビュー画像生成と、実機形状（公式 STL）との干渉チェック。

実行（このディレクトリで、先に figure_case.py を実行しておく）:
    uv run --with cadquery --with trimesh --with pillow --with rtree python preview.py

公式 STL（M5Stack/M5_Hardware）を .cache/ にダウンロードし、フィギュア座標へ配置する。
OpenGL 不要の簡易ソフトウェアレンダラで images/ に PNG を出力する。
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image, ImageDraw

import figure_case as fc

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
RAW = "https://raw.githubusercontent.com/m5stack/M5_Hardware/master/Products/"
URLS = {
    "AtomS3-Lite.stl": RAW + "C124_AtomS3-Lite/Structures/AtomS3-Lite.stl",
    # AtomS3R + Atomic Echo Base のキット。Echo Base 単体の STL が無いためここから取り出す
    "K147.stl": RAW + "K147_AtomS3R-AI_Chatbot/Structures/AtomS3R-AI_Chatbot.stl",
}

WHITE = (236, 236, 232)
GREY = (96, 102, 112)
DEV_WHITE = (205, 215, 235)
DEV_GREY = (70, 74, 82)


def fetch(name: str) -> Path:
    CACHE.mkdir(exist_ok=True)
    p = CACHE / name
    if not p.exists():
        urllib.request.urlretrieve(URLS[name], p)
    return p


def to_figure(v: np.ndarray, stack_z0: float) -> np.ndarray:
    """公式 STL 座標（xy 中心, z=積層方向）→ フィギュア座標。
    stack_z0: その部品の z=0 が積層（Echo Base 底=0, Atom 前面=19.51）のどこにあるか。"""
    x, y, z = v[:, 0], v[:, 1], v[:, 2] + stack_z0
    return np.column_stack([y, fc.DEV_D - z, -x])


def device_meshes() -> list[tuple[trimesh.Trimesh, tuple]]:
    lite = trimesh.load(fetch("AtomS3-Lite.stl")).split(only_watertight=False)
    k147 = trimesh.load(fetch("K147.stl")).split(only_watertight=False)
    out = []
    # AtomS3-Lite: 本体(厚み 9.51)と上面カバー(厚み 4.3)のうち原点付近の組
    for m, col in ((lite[3], DEV_WHITE), (lite[0], DEV_WHITE)):
        v = m.vertices.copy()
        v[:, 2] += 30.0  # z: -30..-20.49 → 0..9.51
        out.append((trimesh.Trimesh(to_figure(v, fc.BASE_D), m.faces), col))
    # Echo Base（K147 の積層の最下段, 厚み 10）
    e = k147[4]
    v = e.vertices.copy()
    v[:, :2] -= 12.02
    v[:, 2] += 19.51
    out.append((trimesh.Trimesh(to_figure(v, 0.0), e.faces), DEV_GREY))
    return out


def case_meshes() -> dict[str, trimesh.Trimesh]:
    return _export_load({"front": fc.build_front(), "back": fc.build_back()})


def _export_load(shapes) -> dict[str, trimesh.Trimesh]:
    import cadquery as cq

    CACHE.mkdir(exist_ok=True)
    res = {}
    for name, s in shapes.items():
        p = CACHE / f"_{name}.stl"
        cq.exporters.export(s, str(p), tolerance=0.01, angularTolerance=0.1)
        res[name] = trimesh.load(p)
    return res


# ---------------------------------------------------------------------------
# 簡易レンダラ（正射影 + z-buffer + 輪郭線）
# ---------------------------------------------------------------------------
def _rot(az: float, el: float) -> np.ndarray:
    az, el = np.radians(az), np.radians(el)
    rz = np.array([[np.cos(az), -np.sin(az), 0], [np.sin(az), np.cos(az), 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, np.cos(el), -np.sin(el)], [0, np.sin(el), np.cos(el)]])
    base = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]])  # -Y（正面）から見る
    return rx @ base @ rz


def render(parts, az=-30.0, el=20.0, size=600, ss=2, fit=None, bg=(250, 250, 248)) -> Image.Image:
    R = _rot(az, el)
    W = size * ss
    allv = np.vstack([m.vertices for m, _ in parts]) if fit is None else fit
    pv = allv @ R.T
    lo, hi = pv[:, :2].min(0), pv[:, :2].max(0)
    span = (hi - lo).max() * 1.16
    ctr = (lo + hi) / 2
    scale = W / span
    img = np.ones((W, W, 3)) * np.array(bg) / 255.0
    zbuf = np.full((W, W), -np.inf)
    light = np.array([-0.45, 0.75, 0.5])
    light /= np.linalg.norm(light)
    for m, rgb in parts:
        v = m.vertices @ R.T
        sx = (v[:, 0] - ctr[0]) * scale + W / 2
        sy = W / 2 - (v[:, 1] - ctr[1]) * scale
        sz = v[:, 2]
        tri = v[m.faces]
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        nl = np.linalg.norm(n, axis=1)
        ok = nl > 1e-12
        n[ok] /= nl[ok, None]
        col = np.array(rgb) / 255.0
        for i in np.nonzero(ok)[0]:
            f = m.faces[i]
            nn = n[i] if n[i][2] >= 0 else -n[i]
            shade = 0.42 + 0.5 * max(0.0, float(nn @ light)) + 0.12 * nn[2]
            c = np.clip(col * shade, 0, 1)
            x0, y0 = sx[f], sy[f]
            xmin, xmax = int(max(0, np.floor(x0.min()))), int(min(W - 1, np.ceil(x0.max())))
            ymin, ymax = int(max(0, np.floor(y0.min()))), int(min(W - 1, np.ceil(y0.max())))
            if xmin > xmax or ymin > ymax:
                continue
            xs, ys = np.meshgrid(np.arange(xmin, xmax + 1) + 0.5, np.arange(ymin, ymax + 1) + 0.5)
            d = (y0[1] - y0[2]) * (x0[0] - x0[2]) + (x0[2] - x0[1]) * (y0[0] - y0[2])
            if abs(d) < 1e-12:
                continue
            a = ((y0[1] - y0[2]) * (xs - x0[2]) + (x0[2] - x0[1]) * (ys - y0[2])) / d
            b = ((y0[2] - y0[0]) * (xs - x0[2]) + (x0[0] - x0[2]) * (ys - y0[2])) / d
            g = 1 - a - b
            msk = (a >= -1e-6) & (b >= -1e-6) & (g >= -1e-6)
            if not msk.any():
                continue
            z = a * sz[f[0]] + b * sz[f[1]] + g * sz[f[2]]
            sub = zbuf[ymin : ymax + 1, xmin : xmax + 1]
            upd = msk & (z > sub)
            sub[upd] = z[upd]
            img[ymin : ymax + 1, xmin : xmax + 1][upd] = c
    zb = np.where(np.isfinite(zbuf), zbuf, -1e6)
    edge = np.zeros(zb.shape, bool)
    thr = span * 0.01
    edge[1:, :] |= np.abs(zb[1:, :] - zb[:-1, :]) > thr
    edge[:, 1:] |= np.abs(zb[:, 1:] - zb[:, :-1]) > thr
    img[edge] *= 0.3
    return Image.fromarray((img * 255).astype(np.uint8)).resize((size, size), Image.LANCZOS)


def labeled(im: Image.Image, text: str) -> Image.Image:
    ImageDraw.Draw(im).text((12, 10), text, fill=(90, 90, 90))
    return im


def grid(images, cols) -> Image.Image:
    w, h = images[0].size
    rows = (len(images) + cols - 1) // cols
    out = Image.new("RGB", (w * cols, h * rows), (255, 255, 255))
    for i, im in enumerate(images):
        out.paste(im, ((i % cols) * w, (i // cols) * h))
    return out


def shifted(m: trimesh.Trimesh, d) -> trimesh.Trimesh:
    return trimesh.Trimesh(m.vertices + np.array(d), m.faces)


def interference(case: dict[str, trimesh.Trimesh], dev, n_per_mesh: int = 3000, chunk: int = 500) -> None:
    """デバイス表面の点がケースの肉の中に入っていないかを調べる。

    点の内外判定は少数ずつ処理する（まとめて投げるとレイ判定の中間配列でメモリを使い切る）。"""
    rng = np.random.default_rng(0)
    pts = []
    for m, _ in dev:
        pts.append(m.sample(n_per_mesh))
        v = m.vertices
        pts.append(v[rng.choice(len(v), min(len(v), n_per_mesh), replace=False)])
    pts = np.vstack(pts)
    for name, m in case.items():
        inside = np.concatenate([m.contains(pts[i : i + chunk]) for i in range(0, len(pts), chunk)])
        worst = 0.0
        if inside.any():
            q = pts[inside][:200]
            worst = float(trimesh.proximity.signed_distance(m, q).max())
        print(f"干渉チェック {name}: 食い込み点 {int(inside.sum())} / {len(pts)}, 最大食い込み {worst:.3f} mm")
    dv = np.vstack([m.vertices for m, _ in dev])
    print("デバイス外形(フィギュア座標):", dv.min(0).round(2), dv.max(0).round(2))


def main() -> None:
    (HERE / "images").mkdir(exist_ok=True)
    case = case_meshes()
    dev = device_meshes()
    interference(case, dev)

    fr, bk = case["front"], case["back"]
    assembled = [(fr, WHITE), (bk, GREY)]
    fitv = np.vstack([fr.vertices, bk.vertices])

    views = [("front", 0, 8), ("3/4 view", -35, 22), ("left side (USB-C / Grove / label)", 90, 8),
             ("back", 180, 8), ("rear 3/4", 145, 25), ("top", -20, 60)]
    ims = [labeled(render(assembled + dev, az, el, fit=fitv), t) for t, az, el in views]
    grid(ims, 3).save(HERE / "images" / "views.png")
    render(assembled + dev, -35, 22, size=900, fit=fitv).save(HERE / "images" / "hero.png")

    # 分解図（前後パーツを引き離し、中のデバイスを見せる）
    ex = [(shifted(fr, (0, -14, 0)), WHITE), (shifted(bk, (0, 16, 0)), GREY)] + dev
    exv = np.vstack([m.vertices for m, _ in ex])
    grid([labeled(render(ex, -40, 20, fit=exv), "exploded"),
          labeled(render(ex, 140, 20, fit=exv), "exploded (rear)")], 2).save(HERE / "images" / "exploded.png")

    # 表情バリエーション（フロントシェルの前面）
    faces = _export_load({f"face_{n}": fc.build_front(n) for n in fc.EXPRESSIONS})
    grid([labeled(render([(m, WHITE), (bk, GREY)], -18, 10, fit=fitv), n.removeprefix("face_"))
          for n, m in faces.items()], 5).save(HERE / "images" / "expressions.png")

    # 部品単体（印刷向き）
    grid([labeled(render([(fr, WHITE)], -30, 25), "front_shell"),
          labeled(render([(bk, GREY)], 150, 25), "back_shell"),
          labeled(render([(bk, GREY)], -30, -35), "back_shell (bottom)"),
          labeled(render([(fr, WHITE)], 160, 15), "front_shell (inside: hinge / press boss)")], 4).save(HERE / "images" / "parts.png")


if __name__ == "__main__":
    main()
