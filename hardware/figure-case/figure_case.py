"""AtomS3-Lite + Atomic Echo Base 用「一体型フィギュアケース」のパラメトリックモデル（CadQuery）。

実行（このディレクトリで）:
    uv run --with cadquery python figure_case.py

出力:
    stl/  … 印刷向きに回転済みの STL（スライサーにそのまま読み込める）
    step/ … 組立座標系の STEP（Fusion 等で編集する用）

座標系（フィギュア基準, mm）:
    X: 右(+) / 左(-)  … フィギュアを正面から見て
    Y: 奥(+) / 手前(-) … デバイス前面（AtomS3-Lite のボタン面）が Y=0
    Z: 上(+) / 下(-)   … デバイス中心が Z=0

デバイスの向き: ボタン面を正面、USB-C / Grove とラベルを左、リセットボタン（側面の小タブ）を上にする。
寸法は M5Stack 公式 STL（M5_Hardware: C124_AtomS3-Lite / K147_AtomS3R-AI_Chatbot）と
Atomic Echo Base 寸法図（本体 24x24x10mm, 14.14mm はピン込み）から採った。
"""

from __future__ import annotations

import math
from itertools import pairwise
from pathlib import Path

import cadquery as cq

# ---------------------------------------------------------------------------
# デバイス寸法
# ---------------------------------------------------------------------------
DEV_W = 24.0  # AtomS3-Lite / Echo Base 共通の幅・高さ
DEV_R = 3.0  # 平面の角 R
ATOM_D = 9.51  # AtomS3-Lite の厚み
BASE_D = 10.0  # Atomic Echo Base 本体の厚み（ピンを除く）
DEV_D = ATOM_D + BASE_D

# ---------------------------------------------------------------------------
# 印刷公差（まず fit_test.stl で確認してから調整する）
# ---------------------------------------------------------------------------
CLR = 0.25  # 片側クリアランス（幅・高さ方向）
CLR_D = 0.3  # 奥行き方向のクリアランス（合計）
LAP_CLR = 0.1  # 前後パーツ嵌合部の片側クリアランス
SNAP = 0.25  # スナップ突起の高さ（固すぎれば 0.15、緩ければ 0.35）

# ---------------------------------------------------------------------------
# ケース形状
# ---------------------------------------------------------------------------
WALL = 2.0  # 側壁の厚み
LIP_T = 1.8  # 前面（顔の面）の厚み
LIP_OVER = 1.5  # 左窓の縁でリップがボタン面の縁にかかる量
BACK_T = 1.6  # 背面の厚み
SEAM_Y = 8.5  # 前後パーツの分割位置
LAP_L = 1.8  # 嵌合の差し込み長
TONGUE_T = 0.9  # フロント側の差し込み（舌）の厚み
EDGE_R = 1.0  # 前面・背面外周の角丸
SEAM_CH = 0.3  # 分割線の面取り（見切りの V 溝）

# 脚
FOOT_W = 7.0  # 幅（X）
FOOT_D = 8.5  # 奥行き（Y）
FOOT_H = 5.5  # 胴体下面からの高さ
FOOT_IN = 5.25  # 中心から脚の内側面までの距離（底面の通気穴を避ける）
FOOT_TOE = 0.0  # 前脚のつま先の張り出し（>0 にすると前面を下にした印刷でサポートが要る）
FOOT_R = 1.5  # 脚の縦エッジの角丸

# ---------------------------------------------------------------------------
# 開口部（デバイスの実物の穴・ポート位置に合わせる）
# ---------------------------------------------------------------------------
# 左側面: AtomS3-Lite の USB-C / Grove 開口（中心 ±6.5）と Echo Base のラベル凹部（中心 ±7.5,
# Y=9.6〜15.4、マイク穴とみられる小穴4つを含む）を一続きの窓にする。前面リップまで抜いて
# USB-C プラグのモールドが当たらないようにする。
SIDE_WIN_Z = 7.5  # 窓の半高
SIDE_WIN_Y1 = 15.6  # 窓の後端
SIDE_WIN_X = -(DEV_W / 2 - LIP_OVER) + 0.1  # この X より外側を抜く（リップも開口の縁まで抜き、細片を残さない）

# 天面右前: AtomS3-Lite のリセットボタン（側面の小タブ, X=1〜7）。前面の枠は残して天面だけ切り欠く
RESET_X = (0.3, 7.7)
RESET_Y = (0.0, 8.0)

# 右側面: AtomS3-Lite の小窓（IR 系とみられる, Z≈7, Y≈2.2〜3.6）
IR_POS = (2.9, 7.0)  # (Y, Z)
IR_D = 3.4

# 背面: Echo Base のスピーカー開口（X=-9〜1.5, Z=6.5〜8.5）を覆うスリット
SPK_X = 9.5
SPK_Z = (5.6, 9.4)
BACK_PANEL = 21.0  # 背面の化粧パネル（溝で描く四角）の一辺
BACK_PANEL_GROOVE = (0.8, 0.4)  # 溝の幅・深さ（面で凹ませるとベッド側がブリッジになるため溝にする）

# 底面: Echo Base の通気スリット3本（X=±3, 0 / 奥端から 4.8mm）
VENT_XS = (-3.0, 0.0, 3.0)
VENT_W = 2.2
VENT_Y = (DEV_D - 5.3, DEV_D + 0.2)

# 天面の飾りスリット（コンセプト画のスリット。貫通）
TOP_SLOT_X = 5.0
TOP_SLOT_Y = (14.4, 15.6)

# ---------------------------------------------------------------------------
# 顔パネル（前面）: 上端のヒンジだけで枠につながる板。押すとたわみ、裏の突起が AtomS3-Lite の
# ボタン（前面のコの字スリットで切られた舌片, X=-5〜6.5 / Z=-6〜6）を押す。
# 表情は前面に彫り込む（前面を下にした印刷で、ベッド面の彫り込みはサポート不要。凸はサポートが要る）
# ---------------------------------------------------------------------------
PANEL = 8.6  # パネルの半幅
PANEL_R = 2.5  # パネルの角 R
SLOT = 0.7  # パネル周りの溝（すき間）の幅。0.5 未満だとスライサーで埋まりやすい
HINGE_W = 6.0  # ヒンジの幅（上端中央）
HINGE_TOP = 11.2  # ヒンジが枠につながる高さ（ヒンジ長 = HINGE_TOP - PANEL）
HINGE_T = 0.6  # ヒンジの厚み（前面側に残す）。押すのが固ければ 0.5、弱ければ 0.8
FACE_RELIEF = 0.6  # パネル裏面の逃げ（ボタン舌片以外に触れないようにする）
PRESS_POS = (2.0, 0.0)  # 押し突起の位置 (X, Z)。舌片の中央付近で、中央の小穴（X=±0.5）を避ける
PRESS_D = 4.0  # 押し突起の直径
FACE_PRELOAD = 0.0  # 突起をデバイス前面より奥へ出す量。反応が鈍ければ 0.1〜0.2
FACE_DEPTH = 0.5  # 表情の彫り込み深さ
FACE_LINE = 0.9  # 表情の線幅
EXPRESSION = "normal"  # 既定の表情（EXPRESSIONS のキー）

# ---------------------------------------------------------------------------
# 派生寸法
# ---------------------------------------------------------------------------
CAV_W = DEV_W + 2 * CLR
CAV_R = DEV_R + CLR
CAV_D = DEV_D + CLR_D
OUT_W = CAV_W + 2 * WALL
OUT_R = CAV_R + WALL
H = OUT_W / 2
Y_FRONT = -LIP_T
Y_BACK = CAV_D + BACK_T
Z_BOT = -H
TONGUE_OUT = CAV_W / 2 + TONGUE_T


def rbox(x0, x1, y0, y1, z0, z1, r=0.0, axis="Y") -> cq.Workplane:
    """軸 axis に平行なエッジを半径 r で丸めた直方体。"""
    s = (
        cq.Workplane("XY")
        .box(x1 - x0, y1 - y0, z1 - z0)
        .translate(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    )
    if r > 0:
        s = s.edges(f"|{axis}").fillet(r)
    return s


def square_prism(half, y0, y1, r) -> cq.Workplane:
    return rbox(-half, half, y0, y1, -half, half, r, "Y")


def foot(y0, y1, cut_front_above=False) -> cq.Workplane:
    """左右一対の脚。上端は胴体に埋め込み、丸い下角と自然につながるようにする。"""
    feet = None
    for sx in (-1, 1):
        x0, x1 = sorted((sx * FOOT_IN, sx * (FOOT_IN + FOOT_W)))
        f = rbox(x0, x1, y0, y1, Z_BOT - FOOT_H, Z_BOT + 2.5, FOOT_R, "Z")
        f = f.faces("<Z").edges().chamfer(0.4)
        feet = f if feet is None else feet.union(f)
    if cut_front_above:
        # つま先（胴体より手前）の上面は胴体下面の高さまでにする
        feet = feet.cut(rbox(-H - 1, H + 1, y0 - 1, Y_FRONT, Z_BOT, H))
    return feet


def front_feet() -> cq.Workplane:
    return foot(Y_FRONT - FOOT_TOE, Y_FRONT - FOOT_TOE + FOOT_D, cut_front_above=FOOT_TOE > 0)


def back_feet() -> cq.Workplane:
    return foot(Y_BACK - FOOT_D, Y_BACK)


def side_window() -> cq.Workplane:
    return rbox(-H - 2, SIDE_WIN_X, Y_FRONT - 2, SIDE_WIN_Y1, -SIDE_WIN_Z, SIDE_WIN_Z, 1.5, "X")


# ---------------------------------------------------------------------------
# 表情（顔パネル前面の彫り込み）。座標は (X, Z)、パネル中心が原点
# ---------------------------------------------------------------------------
def _arc(cx, cz, r, a0, a1, n=8):
    """中心 (cx, cz)・半径 r の円弧上の点列（角度は度、X 軸から反時計回り）。"""
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cz + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


def _mirror(pts):
    return [(-x, z) for x, z in pts]


EYE_X, EYE_Z = 3.8, 1.2
CHEEK = [[(4.7 + 1.2 * i, -4.2), (5.4 + 1.2 * i, -2.8)] for i in range(3)]  # 右ほほの斜線 3 本
MOUTH_Z = -3.6


def _pill(x, z, w, h):
    return ("pill", x, z, w, h)


# 各表情: ("pill", x, z, 幅, 高さ) の塗り形状と、折れ線（線幅 FACE_LINE で彫る）の並び
EXPRESSIONS = {
    # 通常: 縦長の目・小さな V の口
    "normal": [_pill(-EYE_X, EYE_Z, 2.0, 5.2), _pill(EYE_X, EYE_Z, 2.0, 5.2),
               [(-0.9, MOUTH_Z + 0.6), (0.0, MOUTH_Z - 0.5), (0.9, MOUTH_Z + 0.6)]],
    # にっこり: 目を閉じた笑顔（∩ の目と ‿ の口）
    "smile": [_arc(-EYE_X, EYE_Z - 0.8, 1.5, 10, 170), _arc(EYE_X, EYE_Z - 0.8, 1.5, 10, 170),
              _arc(0.0, MOUTH_Z + 1.2, 1.5, 200, 340)],
    # びっくり: 縦長の目・まるい口
    "surprised": [_pill(-EYE_X, EYE_Z + 0.3, 2.2, 5.6), _pill(EYE_X, EYE_Z + 0.3, 2.2, 5.6),
                  _pill(0.0, MOUTH_Z - 0.2, 2.2, 2.4)],
    # ウインク: 右目（向かって右）を「<」で閉じる
    "wink": [_pill(-EYE_X, EYE_Z, 2.0, 5.2),
             [(EYE_X + 1.1, EYE_Z + 1.4), (EYE_X - 1.0, EYE_Z), (EYE_X + 1.1, EYE_Z - 1.4)],
             _arc(0.0, MOUTH_Z + 1.2, 1.3, 210, 330)],
    # ねむい: まぶたを下げた目（‿）と小さな口
    "sleepy": [_arc(-EYE_X, EYE_Z + 0.6, 1.6, 200, 340), _arc(EYE_X, EYE_Z + 0.6, 1.6, 200, 340),
               [(-0.6, MOUTH_Z), (0.6, MOUTH_Z)]],
}


def face_cut(expression: str) -> cq.Workplane:
    """表情の彫り込み用ソリッド（前面から FACE_DEPTH）。ほほの斜線はどの表情にも入れる。"""
    items = list(EXPRESSIONS[expression]) + CHEEK + [_mirror(c) for c in CHEEK]
    wp = cq.Workplane("XZ", origin=(0, Y_FRONT + FACE_DEPTH, 0))  # 押し出しは -Y（前面の外）へ
    cut = None
    for it in items:
        if isinstance(it, tuple) and it[0] == "pill":
            _, x, z, w, h = it
            s = wp.center(x, z).slot2D(max(w, h), min(w, h), 90 if h > w else 0).extrude(FACE_DEPTH + 1)
            cut = s if cut is None else cut.union(s)
            continue
        for (x0, z0), (x1, z1) in pairwise(it):
            length = math.hypot(x1 - x0, z1 - z0) + FACE_LINE
            ang = math.degrees(math.atan2(z1 - z0, x1 - x0))
            s = wp.center((x0 + x1) / 2, (z0 + z1) / 2).slot2D(length, FACE_LINE, ang).extrude(FACE_DEPTH + 1)
            cut = s if cut is None else cut.union(s)
    return cut


def snap_points():
    y = SEAM_Y + LAP_L / 2 - 0.2  # 球の極（Y 方向）が舌の後端面から出ないよう少し前に寄せる
    return [(x, y, z) for x in (-6.0, 6.0) for z in (-TONGUE_OUT, TONGUE_OUT)]


def sphere_at(p, r) -> cq.Workplane:
    # 球の極を Y 方向に向ける（極が突起の頂点に来ると STL の三角形が縮退して穴が開く）。
    # "YZ"（極が X 方向）だと OCC が前面開口の面取りに失敗するため "XZ" を使う
    return cq.Workplane("XZ").sphere(r).translate(p)


# ---------------------------------------------------------------------------
# フロントシェル（白）: 前面リップ + AtomS3-Lite を包むスリーブ + 前脚 + 差し込み舌
# ---------------------------------------------------------------------------
def build_front(expression: str = EXPRESSION) -> cq.Workplane:
    body = square_prism(H, Y_FRONT, SEAM_Y, OUT_R)
    body = body.faces("<Y").edges().fillet(EDGE_R)
    body = body.faces(">Y").edges().chamfer(SEAM_CH)

    tongue = square_prism(TONGUE_OUT, SEAM_Y - 0.5, SEAM_Y + LAP_L - 0.15, CAV_R + TONGUE_T)
    body = body.union(tongue)

    # スナップ突起（舌の外面に 4 箇所）。球の中心を面から沈めて高さ SNAP の半球状にする
    r = 0.8
    for x, y, z in snap_points():
        zc = z - (r - SNAP) if z > 0 else z + (r - SNAP)
        body = body.union(sphere_at((x, y, zc), r))

    # デバイス収納部
    cavity = square_prism(CAV_W / 2, 0.0, SEAM_Y + LAP_L + 1, CAV_R)
    body = body.cut(cavity)

    # 脚（上端の埋め込み分が収納部に食い込むので収納部を切り直す）
    body = body.union(front_feet()).cut(cavity)

    # 顔パネル: 周りの溝（上端中央のヒンジだけ残す）+ ヒンジ脇のスリット
    ring = square_prism(PANEL + SLOT, Y_FRONT - 1, 0.5, PANEL_R + SLOT).cut(
        square_prism(PANEL, Y_FRONT - 2, 1, PANEL_R)
    ).cut(rbox(-HINGE_W / 2, HINGE_W / 2, Y_FRONT - 2, 1, 0, H))
    for sx in (-1, 1):
        x0, x1 = sorted((sx * HINGE_W / 2, sx * (HINGE_W / 2 + SLOT)))
        ring = ring.union(rbox(x0, x1, Y_FRONT - 1, 0.5, PANEL - 1, HINGE_TOP))
    body = body.cut(ring)
    # ヒンジを裏から薄くし、パネル裏面に逃げを付けて押し突起だけがボタンに触れるようにする
    body = body.cut(rbox(-HINGE_W / 2, HINGE_W / 2, Y_FRONT + HINGE_T, 0.5, PANEL - 0.5, HINGE_TOP))
    body = body.cut(square_prism(PANEL + 0.1, -FACE_RELIEF, 0.5, PANEL_R))
    px, pz = PRESS_POS
    body = body.union(
        cq.Workplane("XZ", origin=(0, FACE_PRELOAD, 0)).center(px, pz).circle(PRESS_D / 2).extrude(FACE_RELIEF + 0.2)
    )
    # 表情
    body = body.cut(face_cut(expression))

    # 左: USB-C / Grove（ラベル窓と一続き）
    body = body.cut(side_window())
    # 上: リセットボタン
    body = body.cut(rbox(RESET_X[0], RESET_X[1], *RESET_Y, CAV_W / 2 - 2.25, H + 2, 1.0, "Z"))
    # 右: 小窓
    body = body.cut(
        cq.Workplane("YZ").circle(IR_D / 2).extrude(6).translate((H - 4, IR_POS[0], IR_POS[1]))
    )
    return body


# ---------------------------------------------------------------------------
# バックシェル（グレー）: Echo Base を包むフード + 背面 + 後脚
# ---------------------------------------------------------------------------
def build_back(with_feet: bool = True) -> cq.Workplane:
    body = square_prism(H, SEAM_Y, Y_BACK, OUT_R)
    body = body.faces(">Y").edges().fillet(EDGE_R)
    body = body.faces("<Y").edges().chamfer(SEAM_CH)
    if with_feet:
        body = body.union(back_feet())

    body = body.cut(square_prism(CAV_W / 2, SEAM_Y - 1, CAV_D, CAV_R))
    lap = TONGUE_OUT + LAP_CLR
    body = body.cut(square_prism(lap, SEAM_Y - 1, SEAM_Y + LAP_L, CAV_R + TONGUE_T + LAP_CLR))

    # スナップの受け（くぼみ）
    r = 0.8
    for x, y, z in snap_points():
        zc = z - (r - SNAP) if z > 0 else z + (r - SNAP)
        body = body.cut(sphere_at((x, y, zc), r + 0.1))

    # 左: ラベル窓（フロントの USB-C 窓と一続き）
    body = body.cut(side_window())
    # 天面: 飾りスリット
    w = TOP_SLOT_Y[1] - TOP_SLOT_Y[0]
    body = body.cut(rbox(-TOP_SLOT_X, TOP_SLOT_X, *TOP_SLOT_Y, CAV_W / 2 - 1, H + 1, w / 2 - 0.01, "Z"))
    # 背面: 化粧パネル（溝の四角）+ スピーカー開口
    p = BACK_PANEL / 2
    gw, gd = BACK_PANEL_GROOVE
    groove = rbox(-p, p, Y_BACK - gd, Y_BACK + 1, -p, p, 3.0, "Y").cut(
        rbox(-p + gw, p - gw, Y_BACK - gd - 1, Y_BACK + 2, -p + gw, p - gw, 3.0 - gw, "Y")
    )
    body = body.cut(groove)
    sh = (SPK_Z[1] - SPK_Z[0]) / 2
    body = body.cut(rbox(-SPK_X, SPK_X, CAV_D - 1, Y_BACK + 1, SPK_Z[0], SPK_Z[1], sh - 0.01, "Y"))
    # 底面: 通気スリット
    for x in VENT_XS:
        body = body.cut(
            rbox(x - VENT_W / 2, x + VENT_W / 2, *VENT_Y, Z_BOT - 1, -CAV_W / 2 + 0.5, VENT_W / 2 - 0.01, "Z")
        )
    return body


def build_fit_test() -> cq.Workplane:
    """公差確認用の輪（高さ 4mm）。デバイスがすっと入り、ガタが小さければ OK。"""
    ring = square_prism(H, 0, 4, OUT_R).cut(square_prism(CAV_W / 2, -1, 5, CAV_R))
    return ring


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------
def to_print_front(s: cq.Workplane) -> cq.Workplane:
    # 前面を下にする。左窓の上下の壁が柱状に立ち上がるのでサポート不要、顔がベッド面で平滑になる
    return s.rotate((0, 0, 0), (1, 0, 0), 90)


def to_print_back(s: cq.Workplane) -> cq.Workplane:
    return s.rotate((0, 0, 0), (1, 0, 0), -90)  # 背面を下にする（サポート不要）


def main() -> None:
    here = Path(__file__).resolve().parent
    (here / "stl").mkdir(exist_ok=True)
    (here / "step").mkdir(exist_ok=True)

    front = build_front()
    back = build_back()
    back_body = build_back(with_feet=False)
    back_feet_only = back.cut(back_body)  # 本体と重ねると back_shell と完全に一致する（マルチカラー用）
    fit = build_fit_test().rotate((0, 0, 0), (1, 0, 0), -90)  # 輪を寝かせて印刷

    # 表情違いのフロントシェル（既定の表情は front_shell.stl）
    (here / "stl" / "expressions").mkdir(exist_ok=True)
    for name in EXPRESSIONS:
        if name != EXPRESSION:
            cq.exporters.export(to_print_front(build_front(name)), str(here / "stl" / "expressions" / f"front_shell_{name}.stl"),
                                tolerance=0.01, angularTolerance=0.1)

    stl = {"front_shell": to_print_front(front), "back_shell": to_print_back(back),
           "back_shell_body_only": to_print_back(back_body), "back_feet_only": to_print_back(back_feet_only),
           "fit_test": fit}
    for name, s in stl.items():
        cq.exporters.export(s, str(here / "stl" / f"{name}.stl"), tolerance=0.01, angularTolerance=0.1)
    for name, s in {"front_shell": front, "back_shell": back}.items():
        cq.exporters.export(s, str(here / "step" / f"{name}.step"))

    for name, s in {"front_shell": front, "back_shell": back}.items():
        bb = s.val().BoundingBox()
        print(f"{name}: {bb.xlen:.2f} x {bb.ylen:.2f} x {bb.zlen:.2f} mm, volume {s.val().Volume() / 1000:.2f} cm3")


if __name__ == "__main__":
    main()
