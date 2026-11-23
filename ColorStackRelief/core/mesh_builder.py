# -*- coding: utf-8 -*-
"""几何生成 —— 把高度场/色带规划变成可打印的网格。

三种模式
========

1. ``relief`` 分层浮雕
   阶梯高度场：每个像素一个柱，柱顶高度由明暗决定、柱顶材质由该像素的
   混色权重决定。顶面 → 侧面竖墙（按高度分段）→ 底面，构成单一闭合实体。

2. ``single`` 单面叠色（v1.7.7 起；v1.6 及以前是 ``flat`` 平整分层板）
   白底板 + N 片 0.1mm 色片的体素实体：**每种颜色一个独立封闭壳**，界面两侧
   各有一张面。不做背面镜像，背面就是底板的白 —— 与 ``card`` 同一套几何，
   只是少一半层数（见 ``build_stack_card``）。

3. ``card`` 双面叠色
   同一套叠色卡几何，但正面 N 层 + 背面 z 镜像（共 2N 层回文），两面同图。

（v1.3 按用户要求删除了 ``blocks`` 像素方块阵列 —— 它的几何是每个像素一个
独立小方块，与"四色叠加混色"的路线重复，而且方块间隙让水密性和强度都变差。
旧的 ``flat`` 平整分层板走"分区配色 + 起伏压平"，v1.7.7 改造成 ``single``
单面叠色，v1.22.0 又把 ``single`` 换成不依赖叠色的 ``mosaic`` 平整色块板
（复用 ``build_stack_card`` 的 nb=1 形态：底板 + 一层逐像素异色块）；
``compute_region(flat=True)`` 那条纯算法路径保留为休眠。）

水密性保障
==========
顶点用**整数角点索引 + 高度值**做键去重（而不是事后 remove_doubles）：
- 同高度相邻像素的顶面角点 → 键相同 → 共享顶点 → 顶面连成一片
- 高度不同 → 键不同 → 不共享 → 由竖墙补上
- 底面细分与顶面一致 → 外墙上沿/下沿不会产生 T 型接点

这样得到的网格在构造上就是封闭的，无需依赖后期的启发式修复。
"""

import bisect
import math

import bmesh

from .. import constants as C


# ---------------------------------------------------------------------------
# 共用底带（"同一根柱子沿 Z 轴分段用不同颜色"的落地参数）
# ---------------------------------------------------------------------------
# 柱子顶端至少留这么高属于它自己的颜色。若柱子比共用底带还矮就整根刷成底色，
# 而顶面仍是像素自己的颜色 → 侧壁全白、顶面有色，观感割裂，所以必须夹一下。
MIN_OWN_STUB_MM = 0.10


# ---------------------------------------------------------------------------
# 顶点缓存 / 建面助手
# ---------------------------------------------------------------------------
class _VertexCache(object):
    """按 (x, y, z) 量化键缓存 BMVert，从源头消除重复顶点。"""

    __slots__ = ("bm", "_cache", "count")

    def __init__(self, bm):
        self.bm = bm
        self._cache = {}
        self.count = 0

    def get(self, x, y, z):
        key = (round(x, 6), round(y, 6), round(z, 6))
        v = self._cache.get(key)
        if v is None:
            v = self.bm.verts.new((x, y, z))
            self._cache[key] = v
            self.count += 1
        return v


def _safe_face(bm, verts, material_index=0):
    """建面，重复顶点环/已存在面时跳过（返回 None），绝不中断整个导出。"""
    if len(verts) < 3:
        return None
    seen = set()
    for v in verts:
        if v in seen:
            return None
        seen.add(v)
    try:
        f = bm.faces.new(verts)
    except ValueError:
        return None
    f.material_index = material_index
    return f


def _split_by_bounds(z_lo, z_hi, bounds):
    """把 [z_lo, z_hi] 按层边界切成子区间，返回 [(a, b), ...]。

    【为什么 bounds 必须是"所有实际出现过的高度值"】这是水密性的隐含前提。
    同一根竖直棱会被多面墙共享：外墙（从 0 到柱高）与内墙（从矮邻居到高
    邻居）在 XY 格点处相接。只有**所有墙都用同一组切分点**，这些墙在棱上的
    顶点才重合，否则错位成 T 型接点 → 边界边（实测 region 模式漏了 624 条）。

    色带模式天然满足这点（柱高都取自 z_bounds）；分区模式的柱高是连续量化
    的，必须显式把这些高度值也并进 bounds。用 bisect 定位区间，bounds 很大
    时（起伏 60mm / 0.1mm 步长 = 600 个值）也不会退化。
    """
    if z_hi - z_lo <= 1e-9:
        return []
    if not bounds:
        return [(z_lo, z_hi)]
    i = bisect.bisect_right(bounds, z_lo + 1e-9)
    j = bisect.bisect_left(bounds, z_hi - 1e-9)
    zs = [z_lo] + list(bounds[i:j]) + [z_hi]
    out = []
    for k in range(len(zs) - 1):
        a, b = zs[k], zs[k + 1]
        if b - a > 1e-9:
            out.append((a, b))
    return out


def _mat_of_z(z, bounds, mats):
    """z 落在哪个材质区间。bounds/mats 长度一致，bounds 为区间上界列表。"""
    for i, hi in enumerate(bounds):
        if z <= hi + 1e-9:
            return mats[i]
    return mats[-1]


# ---------------------------------------------------------------------------
# 模式 1：分层浮雕
# ---------------------------------------------------------------------------
def _stacked_wall_mat(seq, top_mat, base_mat, base_band_mm, band_mm,
                      top_mm=None, body_own=False):
    """柱内叠色的竖墙分段：``seq`` 贴着**柱顶**（外表面）往下排，下方填基底。

    ``seq`` 是**从底到顶**的材质索引序列（最靠近底板的那层排在最前），由
    ``generation`` 把配色器的「外→内」堆叠反转后给出。因此第 0 个可见层
    （柱顶那一层）是 ``seq[-1]``。

    ⚠ v1.24.0 修复（R29）：原实现把 ``seq`` 贴在**底板之上**从下往上排，
    比叠层高出的一截用最外层色填满。柱高 1.0~2.1mm，而叠层总厚只有
    ``n × 0.1mm``（4 层 = 0.4mm），于是柱顶恒有 0.6~1.7mm 的**单一颜色**
    盖在叠层上 —— 实测 100% 的像素柱都被 ≥0.5mm 的同色封顶，"青+黄=绿"
    只在最底下 0.4mm 的侧壁露出，正上方看到的永远是青色。这就是 R19/R20
    把「顶面只露最上层色膜」判成物理不成立的真正原因：不是叠色模型错，
    是几何把叠层埋在了柱底。改成贴顶后，顶面 0.4mm 就是那几片色膜，
    呈现色 = ``stack_present(seq 反转回外→内)``，与预览模型一致。

    ``base_band_mm`` 以下（底板范围）统一用 ``base_mat``；柱顶往下放完
    ``seq`` 仍有富余的高度，也用 ``base_mat`` 填充（白基底，不参与滤光）。
    ``top_mm`` 是该柱的顶面高度；缺省时退回旧的贴底行为（仅为兼容调用方，
    正常链路必须给）。

    ``body_own``（v1.32.0 / R39）：柱内叠层**以下**那一截用什么颜色。
      * ``False``（旧行为）= 基底白。
      * ``True`` = 该柱自己最外层的色片颜色（``seq[-1]``）。

    【为什么必须能改成 True】浮雕的高度场是连续的，相邻柱的落差就是一道
    **竖墙**；竖墙只有柱顶 0.8mm 属于色片，再往下全是白基底。实测一张
    麻将桌照片（96×96、100mm、CMYK）：所有竖直暴露墙里 **37.8% 的面积是
    白基底**。那些白墙在实物上就是"颜色交界的白缝"和"明明该是青色却发白"
    —— 用户 2026-10-29 的三条反馈里有两条是它。改成自己的最外色后，竖墙
    的颜色与顶面同源，视觉上连续。代价：柱体每层都可能出现 2~4 种料，
    换色次数明显上升（同一张图 9 → 38 次），所以留成可关的开关。
    """
    if not seq:
        return lambda z: top_mat
    bb = float(base_band_mm or 0.0)
    nb = max(1e-6, float(band_mm or 0.0))
    n = len(seq)
    zt = None if top_mm is None else float(top_mm)

    def f(z):
        if z < bb - 1e-9:
            return base_mat
        if zt is None:
            k = int((z - bb) / nb + 1e-9)
            if k < 0:
                k = 0
            return seq[k] if k < n else seq[-1]
        # 从柱顶往下数：k = 0 是顶面那一层（最外层 = seq[-1]）。
        # 用 floor 而不是 round —— z 与层界都是 0.1 的整数倍，恰好落在层界上
        # 是常态；round 的银行家舍入 + 浮点误差会把它随机拨到上/下一层。
        k = int(math.floor((zt - z) / nb + 1e-9))
        if k < 0:
            k = 0
        if k >= n:
            return seq[-1] if body_own else base_mat
        return seq[n - 1 - k]

    return f


def build_relief(bm, levels, w, h, size_x, size_y, z_by_level,
                 z_bounds, mat_by_level, side_mats,
                 solid_base=True, top_cap=True,
                 z_grid=None, mat_grid=None, base_band_mm=None,
                 layer_mats=None, layer_band_mm=None, body_own=False,
                 progress=None):
    """逐色封闭壳版 relief（v1.16.4 修复 Issue 2 根因）。

    旧实现把整张高度场建成「单一连通网格」，导出时再按材质拆成 4 个对象 ——
    每个材质对象只是曲面上的**开放面片**，体积≈0，Bambu Studio 直接丢弃
    体积小的（实测只剩天蓝+白色两色）。

    新实现仿 :func:`build_stack_card`：每个材质一个**独立水密封闭壳**（各自
    持有独立顶点缓存），壳间几何重合、拓扑独立。Bambu 按「对象」分配耗材，
    每个壳体积正确 → 四色齐全。

    几何语义（与旧版完全一致，仅拓扑不同）：每根像素柱按全局 z 分界
    ``z_bounds`` 切成若干 [a,b] 段，每段材质由 ``wall_mat_fn``（柱内叠色）或
    ``_mat_of_z``（色带）决定；段与段、柱与柱之间只在「异色或越界」处建墙，
    同色相邻的内部界面不建面（那是壳内部，不是边界）。于是每种颜色恰好得到
    一个（或多个）闭合实体。
    """
    caches = {}

    def V(mat):
        g = caches.get(mat)
        if g is None:
            g = _VertexCache(bm)
            caches[mat] = g
        return g

    dx = size_x / float(w)
    dy = size_y / float(h)

    # ---- 列高 / 逐列材质回调：与旧版同一套判定 ----
    def zof(row, col):
        if z_grid is not None:
            return float(z_grid[row][col])
        lv = levels[row][col]
        if lv < 0:
            lv = 0
        if lv >= len(z_by_level):
            lv = len(z_by_level) - 1
        return z_by_level[lv]

    def matof(row, col):
        if mat_grid is not None:
            return int(mat_grid[row][col])
        lv = levels[row][col]
        if lv < 0:
            lv = 0
        return min(lv, len(mat_by_level) - 1)

    def wall_mat_fn(row, col):
        if layer_mats is not None:
            return _stacked_wall_mat(
                layer_mats[row][col], matof(row, col),
                side_mats[0] if side_mats else matof(row, col),
                base_band_mm, layer_band_mm,
                top_mm=zof(row, col), body_own=body_own)
        if mat_grid is None:
            return None
        m = matof(row, col)
        if base_band_mm is None:
            return lambda z: m
        base_m = side_mats[0] if side_mats else m
        bb = float(base_band_mm)
        zs = float(zof(row, col))
        stub = min(MIN_OWN_STUB_MM, max(0.0, zs * 0.5))
        bb = min(bb, zs - stub)
        if bb <= 1e-9:
            return lambda z: m
        return lambda z: (base_m if z < bb - 1e-9 else m)

    def mat_at_z(row, col, z):
        mfn = wall_mat_fn(row, col)
        if mfn is not None:
            return mfn(z)
        return _mat_of_z(z, z_bounds, side_mats)

    H = [[zof(r, c) for c in range(w)] for r in range(h)]

    def _wall_segs(a, b, Hn):
        """把竖墙区间 [a,b] 按邻居柱高 Hn 切分，使任何墙立面都不跨过邻居的
        顶（否则邻居顶盖在该处的边会悬空成边界边）。

        返回 [(s0, s1, neighbor_present)]：
          * ``neighbor_present`` 表示该子段上方邻居是否仍有实体（z≤Hn）。
          * 子段上方邻居缺失 → 裸露外墙（必建本侧立面）；
          * 子段上方邻居存在且同色 → 内部合并，不建面；
          * 子段上方邻居存在且异色 → 界面（建本侧立面，邻居在其缓存里建配对面）。
        """
        if Hn <= a + 1e-9:
            return [(a, b, False)]          # 邻居整段更矮 → 裸露外墙
        if Hn >= b - 1e-9:
            return [(a, b, True)]           # 邻居整段更高/齐平 → 异色才建界面
        return [(a, Hn, True), (Hn, b, False)]  # 跨邻居顶 → 下界面 + 上裸露

    # ---- 面助手：与 build_stack_card 同口径（显式环绕，不用 recalc）----
    def face_ccw_z(mat, xa, ya, xb, yb, z):
        c = V(mat)
        _safe_face(bm, [c.get(xa, ya, z), c.get(xb, ya, z),
                        c.get(xb, yb, z), c.get(xa, yb, z)], mat)

    def face_cw_z(mat, xa, ya, xb, yb, z):
        c = V(mat)
        _safe_face(bm, [c.get(xa,ya,z), c.get(xa,yb,z),
                        c.get(xb,yb,z), c.get(xb,ya,z)], mat)

    def face_x(mat, x, ya, yb, zl, zh, normal_pos):
        c = V(mat)
        p0 = c.get(x, ya, zl); p1 = c.get(x, yb, zl)
        p2 = c.get(x, yb, zh); p3 = c.get(x, ya, zh)
        _safe_face(bm, [p0, p1, p2, p3] if normal_pos
                   else [p1, p0, p3, p2], mat)

    def face_y(mat, y, xa, xb, zl, zh, normal_pos):
        c = V(mat)
        p0 = c.get(xa, y, zl); p1 = c.get(xb, y, zl)
        p2 = c.get(xb, y, zh); p3 = c.get(xa, y, zh)
        # 正序 (p0,p1,p2,p3) 法线 -y；normal_pos(+y) 用反序
        _safe_face(bm, [p0, p1, p2, p3] if not normal_pos
                   else [p1, p0, p3, p2], mat)

    for row in range(h):
        iy0 = h - row - 1
        iy1 = h - row
        ya = iy0 * dy
        yb = iy1 * dy
        for col in range(w):
            xa = col * dx
            xb = (col + 1) * dx
            Hc = H[row][col]
            if Hc <= 1e-9:
                continue
            # 该柱按全局 z 分界切段，顶端截断到 Hc
            cinters = _split_by_bounds(0.0, Hc, z_bounds)
            prev_mat = None
            niv = len(cinters)
            for idx, (a, b) in enumerate(cinters):
                mid = (a + b) * 0.5
                mat = mat_at_z(row, col, mid)
                is_last = (idx == niv - 1)
                next_mat = None
                if not is_last:
                    a2, b2 = cinters[idx + 1]
                    next_mat = mat_at_z(row, col, (a2 + b2) * 0.5)
                # 顶盖（+z）：仅「本柱顶」或「上方异色界面」（同色相邻的内部
                # 界面不建面，那是壳内部、不是边界）
                if top_cap and (is_last or (next_mat is not None
                                           and next_mat != mat)):
                    face_ccw_z(mat, xa, ya, xb, yb, b)
                # 底盖（-z）：仅「全局底（a≈0）」或「下方异色界面」
                if a <= 1e-9:
                    if solid_base:
                        face_cw_z(mat, xa, ya, xb, yb, a)
                elif prev_mat is not None and prev_mat != mat:
                    face_cw_z(mat, xa, ya, xb, yb, a)
                prev_mat = mat
                # ---- 四侧竖墙：按邻居柱高切分，杜绝顶盖悬空边界边 ----
                # 左（col-1）
                if col == 0:
                    face_x(mat, xa, ya, yb, a, b, normal_pos=False)
                else:
                    for (s0, s1, present) in _wall_segs(a, b, H[row][col - 1]):
                        if present and mat_at_z(row, col - 1,
                                               0.5 * (s0 + s1)) == mat:
                            continue
                        face_x(mat, xa, ya, yb, s0, s1, normal_pos=False)
                # 右（col+1）
                if col == w - 1:
                    face_x(mat, xb, ya, yb, a, b, normal_pos=True)
                else:
                    for (s0, s1, present) in _wall_segs(a, b, H[row][col + 1]):
                        if present and mat_at_z(row, col + 1,
                                               0.5 * (s0 + s1)) == mat:
                            continue
                        face_x(mat, xb, ya, yb, s0, s1, normal_pos=True)
                # 上（row-1，几何 y1 一侧）
                if row == 0:
                    face_y(mat, yb, xa, xb, a, b, normal_pos=True)
                else:
                    for (s0, s1, present) in _wall_segs(a, b, H[row - 1][col]):
                        if present and mat_at_z(row - 1, col,
                                               0.5 * (s0 + s1)) == mat:
                            continue
                        face_y(mat, yb, xa, xb, s0, s1, normal_pos=True)
                # 下（row+1，几何 y0 一侧）
                if row == h - 1:
                    face_y(mat, ya, xa, xb, a, b, normal_pos=False)
                else:
                    for (s0, s1, present) in _wall_segs(a, b, H[row + 1][col]):
                        if present and mat_at_z(row + 1, col,
                                               0.5 * (s0 + s1)) == mat:
                            continue
                        face_y(mat, ya, xa, xb, s0, s1, normal_pos=False)
        # v1.0.16：逐行回报进度（每 8 行一次，函数本身不做节流）
        if progress is not None and (row % 8 == 0 or row == h - 1):
            progress((row + 1) / float(max(1, h)))

def _vertical_wall(bm, V, p0, p1, z_lo, z_hi, z_bounds, side_mats, flip=False,
                   mat_fn=None):
    """在两个 XY 端点之间建竖直墙，按 z_bounds 分层着色。

    ``mat_fn(z_mid)`` 给出时优先用它决定每段材质（分区配色下逐像素着色）。
    """
    segs = _split_by_bounds(z_lo, z_hi, z_bounds)
    if not segs:
        return
    (x0, y0), (x1, y1) = p0, p1
    for a, b in segs:
        va0 = V.get(x0, y0, a)
        va1 = V.get(x1, y1, a)
        vb1 = V.get(x1, y1, b)
        vb0 = V.get(x0, y0, b)
        mid = (a + b) * 0.5
        mat = mat_fn(mid) if mat_fn is not None else _mat_of_z(mid, z_bounds, side_mats)
        loop = [va0, va1, vb1, vb0] if not flip else [va0, vb0, vb1, va1]
        _safe_face(bm, loop, mat)


# ---------------------------------------------------------------------------
# 高度场预处理
# ---------------------------------------------------------------------------
def resolve_manifold_conflicts(levels, max_passes=12):
    """消除会在角点处产生非流形棱的高度组合。

    【判据的由来】最初以为是"对角等高"（经典鞍点），但实测那只是特例。
    真正的充分必要条件是：**角点周围四条竖墙的高度区间存在公共交集**
    ::

        lo = max(四条墙各自的下端)      hi = min(四条墙各自的上端)
        lo < hi  →  该高度段被 4 个面共享  →  非流形

    实测命中的三处四像素高度是 (3.8,1.4,1.4,2.2)、(2.2,1.4,1.4,3.8)、
    (1.4,2.2,3.8,1.4) —— 都不是"两对相等"，用旧判据一条都抓不到。

    几何含义：实体在该处是"两个柱体沿一条棱相切"。这**无法靠顶点分裂
    修复** —— 分裂后每条棱只剩 1 个面，反而变成破洞。所以只能从高度场
    侧消除：把最低的那个格点抬到 ``hi``（``hi`` 必然是某个合法色带高度），
    让公共交集退化成一个点。

    代价是极少量格点跨了一个色带，在 110×82 分辨率下肉眼不可见，
    远优于留下非流形棱。

    返回被修改的格点数。
    """
    h = len(levels)
    if h < 2:
        return 0
    w = len(levels[0])
    if w < 2:
        return 0

    total = 0
    for _ in range(max(1, int(max_passes))):
        changed = 0
        for iy in range(1, h):
            ru = h - iy - 1
            rd = h - iy
            if ru < 0 or rd >= h:
                continue
            for ix in range(1, w):
                cl = ix - 1
                cr = ix

                za = levels[ru][cl]
                zb = levels[ru][cr]
                zc = levels[rd][cl]
                zd = levels[rd][cr]

                # 四条墙（左列、右列、上行、下行）的高度区间
                m1 = za if za < zc else zc       # 左列
                m2 = zb if zb < zd else zd       # 右列
                m3 = za if za < zb else zb       # 上行
                m4 = zc if zc < zd else zd       # 下行
                lo = m1 if m1 > m2 else m2
                if m3 > lo:
                    lo = m3
                if m4 > lo:
                    lo = m4

                x1 = za if za > zc else zc
                x2 = zb if zb > zd else zd
                x3 = za if za > zb else zb
                x4 = zc if zc > zd else zd
                hi = x1 if x1 < x2 else x2
                if x3 < hi:
                    hi = x3
                if x4 < hi:
                    hi = x4

                if lo >= hi:
                    continue

                # 有冲突：把四格中最低的那个抬到 hi
                best_r, best_c, best_v = ru, cl, za
                if zb < best_v:
                    best_r, best_c, best_v = ru, cr, zb
                if zc < best_v:
                    best_r, best_c, best_v = rd, cl, zc
                if zd < best_v:
                    best_r, best_c, best_v = rd, cr, zd
                levels[best_r][best_c] = hi
                changed += 1
        total += changed
        if changed == 0:
            break
    return total


# ---------------------------------------------------------------------------
# 网格清理与校验
# ---------------------------------------------------------------------------
def cleanup_mesh(bm, weld_dist=1e-4, weld=True):
    """合并重合顶点 + 清理松散几何（不删除任何有面积的退化处理见 mesh_audit）。

    ``weld=False``：跳过 remove_doubles。色深叠色卡的成对界面靠"全新顶点"
    保持每条棱恰好 2 面 —— 焊接会把它们重新共享，制造 4 面棱（实测已踩）。
    """
    if weld:
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=weld_dist)
    loose_edges = [e for e in bm.edges if not e.link_faces]
    if loose_edges:
        bmesh.ops.delete(bm, geom=loose_edges, context='EDGES')
    loose_verts = [v for v in bm.verts if not v.link_edges]
    if loose_verts:
        bmesh.ops.delete(bm, geom=loose_verts, context='VERTS')


def count_bad_edges(bm, split=False):
    """返回 (边界边数, 非流形边数)；``split=True`` 时返回到三个数。

    为什么要把非流形边拆成两种（v1.32.0 / R39 实测）：
      * **跨材质**（同一条棱上挂着不同颜色的面）：壳与壳之间真的没对齐，
        导出后就是破洞，必须修。
      * **同材质**（棱上所有面同属一个材质）：这是"斜对角台阶相碰"——
        四个格子两高两低成棋盘状时，两片竖墙在同一根竖棱上交叉，形成
        捏合边（pinch）。它**不产生任何孔洞**，切片器按层求交时照常处理。
    实测一张 48×48 合成图：``body_own`` 关 → 边界 0 / 同材质捏合 54 /
    跨材质 0；开 → 边界 0 / 同材质捏合 171 / 跨材质 0。也就是说开关只把
    "更多斜对角台阶并成了同一种颜色的内部捏合"，没有新增任何真正的破洞。
    报告和面板只该拿**跨材质**那一个当缺陷用。
    """
    boundary = 0
    cross = 0
    pinch = 0
    for e in bm.edges:
        n = len(e.link_faces)
        if n == 1:
            boundary += 1
        elif n > 2:
            if len({f.material_index for f in e.link_faces}) > 1:
                cross += 1
            else:
                pinch += 1
    if split:
        return boundary, cross, pinch
    return boundary, cross + pinch


def face_area2(face):
    """与法线无关的多边形叉积面积平方 —— 共线面严格得 0。"""
    vs = face.verts
    n = len(vs)
    cx = cy = cz = 0.0
    for i in range(n):
        a = vs[i].co
        b = vs[(i + 1) % n].co
        cx += a.y * b.z - a.z * b.y
        cy += a.z * b.x - a.x * b.z
        cz += a.x * b.y - a.y * b.x
    return 0.25 * (cx * cx + cy * cy + cz * cz)


def count_degenerate_faces(bm, eps2=1e-10):
    """零面积（sliver）面计数 —— 切片软件闪退的元凶。

    v1.37.1：改用 ``me.polygons.foreach_get("area")`` 一次性从 C 侧取面积，
    比逐面 Python 求叉积快两个数量级（1.3M 面 3.5 s → 0.1 s）。
    阈值 ``eps2`` 是面积平方，换算成面积要开方。
    """
    if not bm.faces:
        return 0
    import bpy
    me = bpy.data.meshes.new("__cstack_deg__")
    try:
        bm.to_mesh(me)
        n = len(me.polygons)
        try:
            import numpy as _np
        except Exception:  # pragma: no cover
            _np = None
        thr = math.sqrt(eps2)
        if _np is not None:
            areas = _np.empty(n, dtype=_np.float32)
            me.polygons.foreach_get("area", areas)
            return int((areas < thr).sum())
        cnt = 0
        for p in me.polygons:
            if p.area < thr:
                cnt += 1
        return cnt
    finally:
        bpy.data.meshes.remove(me)


def build_engraved_back(bm, w, h, size_x, size_y, top_mm, depth_grid, mat,
                        level_mm=0.05, progress=None):
    """反面单色凹刻板（v1.44.0 / R54，浮雕明信片专用）。

    几何语义：板体占 ``[depth, top_mm]`` —— **顶面**是 z = ``top_mm`` 的平面
    （与正面浮雕底面贴合），**底面**是 z = ``depth_grid[row][col]`` 的凹刻面
    （0 = 不刻、贴到最底；越大刻得越深）。一整块单材质实体，一个闭合壳，
    边界边必须为 0。

    ⚠ 方向别搞反：凹刻是从**底面往上**挖掉材料，所以底面高度 = 深度本身，
    不是 ``top − 深度``。写成后者等于从顶面挖，会把与正面贴合的界面挖空。

    【为什么深度要先量化成 level_mm 的整数倍】凹刻面的相邻格高度不同时，
    台阶立面会相交在格点竖棱上。如果每格高度都是任意浮点数，同一条竖棱会
    被两侧的墙切在不同高度 → T 型接点 → 边界边（实测未量化时 60×60 的图
    有 6102 条边界边、559 个零面积面）。量化后用**全局高度集合**切分每段
    竖墙，所有墙在同一组平面上断点，顶点自然重合。这与 relief 的
    ``zb_region`` 是同一个道理（见 build_relief 的注释）。

    【只从一个方向建墙】内部边界如果两侧都建，第二张面会因"同一顶点集合
    已存在"被跳过，法线方向就取决于谁先建。这里规定：±x 只在右边界（xb）
    建、±y 只在 y0 建，另加左/上两条外边界 —— 每条内部边恰好建一次。

    【行方向】与 build_stack_card / build_relief 一致：``row 0`` 是图片上方，
    对应几何 y 的最大侧 —— 否则反面图案会上下颠倒。
    """
    dx = size_x / float(w)
    dy = size_y / float(h)
    top = float(top_mm)
    step = max(1e-4, float(level_mm or 0.05))

    def zb(r, col):
        # 量化收敛在 constants.engrave_z 一处（generation 统计与探针共用同一口径）
        return C.engrave_z(depth_grid[r][col], step, top)

    # 全局高度集合（含 0 与顶面）：所有竖墙都在这组平面上断开
    levels = {0.0, round(top, 6)}
    for r in range(h):
        for c in range(w):
            levels.add(zb(r, c))
    levels = sorted(levels)

    def cuts(zlo, zhi):
        """[zlo, zhi] 内按全局高度切成的子段。"""
        out = []
        for k in range(len(levels) - 1):
            a, b = levels[k], levels[k + 1]
            if b <= zlo + 1e-9 or a >= zhi - 1e-9:
                continue
            out.append((max(a, zlo), min(b, zhi)))
        if not out:
            out.append((zlo, zhi))
        return out

    c = _VertexCache(bm)

    def V(x, y, z):
        return c.get(x, y, z)

    def face_up(xa, ya, xb, yb, z):
        """法线 +z。"""
        _safe_face(bm, [V(xa, ya, z), V(xb, ya, z),
                        V(xb, yb, z), V(xa, yb, z)], mat)

    def face_down(xa, ya, xb, yb, z):
        """法线 -z。"""
        _safe_face(bm, [V(xa, ya, z), V(xa, yb, z),
                        V(xb, yb, z), V(xb, ya, z)], mat)

    def face_x(x, ya, yb, zl, zh, normal_pos):
        p0 = V(x, ya, zl)
        p1 = V(x, yb, zl)
        p2 = V(x, yb, zh)
        p3 = V(x, ya, zh)
        _safe_face(bm, [p0, p1, p2, p3] if normal_pos
                   else [p1, p0, p3, p2], mat)

    def face_y(y, xa, xb, zl, zh, normal_pos):
        p0 = V(xa, y, zl)
        p1 = V(xb, y, zl)
        p2 = V(xb, y, zh)
        p3 = V(xa, y, zh)
        # 与 build_stack_card 同一套叉积结论：正序 = -y
        _safe_face(bm, [p0, p1, p2, p3] if not normal_pos
                   else [p1, p0, p3, p2], mat)

    def wall_x(x, ya, yb, zlo, zhi, normal_pos):
        for a, b in cuts(zlo, zhi):
            if b - a > 1e-9:
                face_x(x, ya, yb, a, b, normal_pos)

    def wall_y(y, xa, xb, zlo, zhi, normal_pos):
        for a, b in cuts(zlo, zhi):
            if b - a > 1e-9:
                face_y(y, xa, xb, a, b, normal_pos)

    for row in range(h):
        y0 = (h - row - 1) * dy
        y1 = y0 + dy
        for col in range(w):
            xa = col * dx
            xb = (col + 1) * dx
            z = zb(row, col)

            # 顶面（外表面，法线 +z）/ 底面（凹刻面，法线 -z）
            face_up(xa, y0, xb, y1, top)
            face_down(xa, y0, xb, y1, z)

            # ---- x 方向 ----
            if col == 0:                      # 左外壁（唯一的 -x 面）
                wall_x(xa, y0, y1, z, top, normal_pos=False)
            if col == w - 1:                  # 右外壁
                wall_x(xb, y0, y1, z, top, normal_pos=True)
            else:                             # 与右邻的台阶（只在这里建一次）
                zn = zb(row, col + 1)
                if abs(zn - z) > 1e-9:
                    wall_x(xb, y0, y1, min(z, zn), max(z, zn),
                           normal_pos=(z < zn))

            # ---- y 方向 ----
            if row == 0:                      # 上外壁（唯一的 +y 面）
                wall_y(y1, xa, xb, z, top, normal_pos=True)
            if row == h - 1:                  # 下外壁
                wall_y(y0, xa, xb, z, top, normal_pos=False)
            else:                             # 与下方邻格的台阶
                zn = zb(row + 1, col)
                if abs(zn - z) > 1e-9:
                    wall_y(y0, xa, xb, min(z, zn), max(z, zn),
                           normal_pos=(z > zn))
        # v1.0.16：逐行回报进度
        if progress is not None and (row % 8 == 0 or row == h - 1):
            progress((row + 1) / float(max(1, h)))


def build_box(bm, x0, x1, y0, y1, z0, z1, mat):
    """一整块闭合长方体（v1.0.15：底板独立成对象时用）。

    只建 6 个四边形、共享 8 个顶点 —— 与 ``build_rim`` 一样用 ``_VertexCache``
    保证相邻面共用顶点，于是每条棱恰好 2 面（boundary 0 / 非流形 0）。
    法线统一朝外：底面 -z、顶面 +z、四侧各朝外。
    """
    c = _VertexCache(bm)
    # 顶点按 (x, y, z) 取，顺时针/逆时针由下面各面的顺序决定
    p000 = c.get(x0, y0, z0)
    p100 = c.get(x1, y0, z0)
    p110 = c.get(x1, y1, z0)
    p010 = c.get(x0, y1, z0)
    p001 = c.get(x0, y0, z1)
    p101 = c.get(x1, y0, z1)
    p111 = c.get(x1, y1, z1)
    p011 = c.get(x0, y1, z1)
    # 底（-z）
    _safe_face(bm, [p000, p010, p110, p100], mat)
    # 顶（+z）
    _safe_face(bm, [p001, p101, p111, p011], mat)
    # 前（-y）
    _safe_face(bm, [p000, p100, p101, p001], mat)
    # 右（+x）
    _safe_face(bm, [p100, p110, p111, p101], mat)
    # 后（+y）
    _safe_face(bm, [p110, p010, p011, p111], mat)
    # 左（-x）
    _safe_face(bm, [p010, p000, p001, p011], mat)


def build_rim(bm, x0, x1, y0, y1, rim_mm, z0, z1, mat):
    """四周向外加一圈实心边框（v1.45.1 / R57，浮雕明信片专用）。
    用户 2026-11-05："四周的侧壁墙没有实现"。改前四面其实都有面、水密 0 破洞，
    但侧面颜色跟着画面走、顶边随起伏 —— 看不出是"墙"。这里在画面外侧加一圈
    **底板色实心环**，顶面与正面最高处齐平，于是四边是竖直平墙。

    环体是一整块闭合实体（4 段顶环 + 4 段底环 + 4 段外墙 + 4 段内墙 = 16 面）：
      * 内墙与正面的外边界墙、背板的外边界墙**位置重合、法线相反** ——
        与项目里"两个颜色壳在交界处各建一张面"是同一套做法，不影响水密；
      * 外扩只往外长，画面本身一格不动（``x0..x1 × y0..y1`` 是画面区域）。
    """
    r = float(rim_mm)
    za, zb = float(z0), float(z1)
    if r <= 1e-9 or zb - za <= 1e-9:
        return

    c = _VertexCache(bm)
    outer = [(x0 - r, y0 - r), (x1 + r, y0 - r),
             (x1 + r, y1 + r), (x0 - r, y1 + r)]
    inner = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    for i in range(4):
        ax0, ay0 = outer[i]
        ax1, ay1 = outer[(i + 1) % 4]
        bx0, by0 = inner[i]
        bx1, by1 = inner[(i + 1) % 4]
        # 顶环（法线 +z）／底环（法线 -z）
        _safe_face(bm, [c.get(ax0, ay0, zb), c.get(ax1, ay1, zb),
                        c.get(bx1, by1, zb), c.get(bx0, by0, zb)], mat)
        _safe_face(bm, [c.get(ax0, ay0, za), c.get(bx0, by0, za),
                        c.get(bx1, by1, za), c.get(ax1, ay1, za)], mat)
        # 外墙（朝外）／内墙（朝画面）
        _safe_face(bm, [c.get(ax0, ay0, za), c.get(ax1, ay1, za),
                        c.get(ax1, ay1, zb), c.get(ax0, ay0, zb)], mat)
        _safe_face(bm, [c.get(bx0, by0, za), c.get(bx0, by0, zb),
                        c.get(bx1, by1, zb), c.get(bx1, by1, za)], mat)


def center_and_ground(bm, size_x, size_y, margin=0.0):
    """把网格居中到 XY 原点，Z 从 0 起（贴板）。"""
    if not bm.verts:
        return
    xs = [v.co.x for v in bm.verts]
    ys = [v.co.y for v in bm.verts]
    zs = [v.co.z for v in bm.verts]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    cz = min(zs)
    for v in bm.verts:
        v.co.x -= cx
        v.co.y -= cy
        v.co.z -= cz


def build_stack_card(bm, w, h, size_x, size_y, base_mm, band_mm, band_mats,
                     base_mat, bottom_mat=None, base_mats=None, progress=None):
    """色深叠色卡（v1.4）：底板 + N 片色层的体素实体（每色一个封闭壳）。

    参考方案（MakerWorld「151 宝可梦叠色卡」）的表示方式：**每种颜色一个
    独立封闭体**，界面两侧各有一张面（各属于自己颜色的壳）。这与导出器的
    ``split_by_material``（每色一个 object、各绑一个耗材）严格对应 ——
    切片器按体积分派耗材，每种耗材的壳自身水密，不依赖网格修复。

    ``band_mats[i][row][col]`` = 第 i 层（0 = 最外/顶层）在该像素的材质索引；
    堆叠比层数短的柱子空档填 ``base_mat``（白），保证每根柱子从 z=0 到顶
    全实心。

    两个模式共用本函数：
      * ``mosaic``（平整色块板）：``base_mm`` 是**统一底色的底板**厚度，
        ``band_mats`` 只有一层（逐像素材质索引，nb=1 → 顶面全平）；
        ``base_mats=None``（底板逐格同色）。
      * ``card``（双面叠色）：没有"统一底色的底板"，最底那片子层的颜色因像素
        而异 —— 传 ``base_mats`` 覆盖底面材质网格，``base_mm`` 此时就是该
        子层的厚度。

    ``base_mats``（v1.5 双面镜像）：逐像素的底面材质网格。双面镜像结构没有
    统一底色的"底板"——最底下一个子层（背面最外层）的颜色因像素而异，
    传 2D 网格覆盖 ``bottom_mat``；``base_mm`` 此时就是该子层的厚度。

    【为什么每种材质一个独立顶点缓存】成对界面的两张面几何重合。若共用
    顶点缓存，第二张面会因"同一顶点集合已存在"被 bmesh 拒绝（faces.new
    抛 ValueError）；若用全新顶点，这张面与其所在壳的邻居面不共享棱，
    壳体碎成孤面（边界边暴涨，两种写法都已实测踩坑）。让每个颜色壳持有
    自己的顶点缓存：壳内棱棱共享（每棱恰好 2 面），壳间几何重合但拓扑
    独立 —— 合并网格边界边 0、非流形 0，与单体水密同口径通过。

    【法线】所有面的环绕在此显式写对（face_y 的叉积方向容易口算错，见
    函数内注释），不再依赖 recalc_face_normals。
    """
    nb = len(band_mats)
    bottom_mat = base_mat if bottom_mat is None else bottom_mat
    # v1.0.15：允许 base_mm = 0（叠色浮雕 / 单面平板的底板另建独立对象）。
    # 旧写法会得到 zs=[0,0,...] —— 第 0 段高度为 0，既建出一批零面积竖墙，
    # 又在 z=0 叠两套盖面，实测单面平板 boundary=52。这里直接不建底板段。
    _zb = float(base_mm)
    _has_base = _zb > 1e-9
    zs = [0.0]
    if _has_base:
        zs.append(_zb)
    for i in range(nb):
        zs.append(round(_zb + float(band_mm) * (i + 1), 6))
    _nseg = len(zs) - 1

    caches = {}

    def V(mat):
        got = caches.get(mat)
        if got is None:
            got = _VertexCache(bm)
            caches[mat] = got
        return got

    def cell_mat(k, row, col):
        """k=0 是底面（白/逐像素），k=1..nb 依次是最内层…最外层。

        base_mm=0 时没有底面那一段，k=0 直接是最内层色片。
        """
        if _has_base and k <= 0:
            if base_mats is not None:
                return base_mats[row][col]
            return bottom_mat
        j = k - (1 if _has_base else 0)
        j = 0 if j < 0 else j
        return band_mats[nb - 1 - j][row][col]

    def face_ccw_z(mat, xa, ya, xb, yb, z):
        """法线 +z 的水平面。"""
        c = V(mat)
        _safe_face(bm, [c.get(xa, ya, z), c.get(xb, ya, z),
                        c.get(xb, yb, z), c.get(xa, yb, z)], mat)

    def face_cw_z(mat, xa, ya, xb, yb, z):
        """法线 -z 的水平面。"""
        c = V(mat)
        _safe_face(bm, [c.get(xa, ya, z), c.get(xa, yb, z),
                        c.get(xb, yb, z), c.get(xb, ya, z)], mat)

    def face_x(mat, x, ya, yb, zl, zh, normal_pos):
        """法线 ±x 的竖墙。normal_pos=True → +x。"""
        c = V(mat)
        p0 = c.get(x, ya, zl)
        p1 = c.get(x, yb, zl)
        p2 = c.get(x, yb, zh)
        p3 = c.get(x, ya, zh)
        _safe_face(bm, [p0, p1, p2, p3] if normal_pos
                   else [p1, p0, p3, p2], mat)

    def face_y(mat, y, xa, xb, zl, zh, normal_pos):
        """法线 ±y 的竖墙。normal_pos=True → +y。

        【叉积核对】(p1-p0)=(dx,0,0) 与 (p2-p1)=(0,0,dz) 的叉积 = (0,-dx·dz,0)
        = -y，所以正序环绕其实是 -y；normal_pos=True 必须用反序。
        """
        c = V(mat)
        p0 = c.get(xa, y, zl)
        p1 = c.get(xb, y, zl)
        p2 = c.get(xb, y, zh)
        p3 = c.get(xa, y, zh)
        _safe_face(bm, [p0, p1, p2, p3] if not normal_pos
                   else [p1, p0, p3, p2], mat)

    dx = size_x / float(w)
    dy = size_y / float(h)
    for row in range(h):
        y0 = (h - row - 1) * dy
        y1 = y0 + dy
        for col in range(w):
            xa = col * dx
            xb = (col + 1) * dx
            for k in range(_nseg):
                zl = zs[k]
                zh = zs[k + 1]
                m = cell_mat(k, row, col)
                # ---- ±z 面 ----
                if k == 0:
                    # 底盖（外表面，法线 -z）
                    face_cw_z(m, xa, y0, xb, y1, zl)
                else:
                    m_below = cell_mat(k - 1, row, col)
                    if m_below != m:
                        # 界面成对：两色各自的壳在自己的缓存里建面，
                        # 几何重合、拓扑独立，互不冲突
                        face_cw_z(m, xa, y0, xb, y1, zl)          # 上色底面
                        face_ccw_z(m_below, xa, y0, xb, y1, zl)   # 下色顶面
                if k == _nseg - 1:
                    # 顶盖（外表面，法线 +z）
                    face_ccw_z(m, xa, y0, xb, y1, zh)
                # ---- ±x 面 ----
                if col == 0:
                    face_x(m, xa, y0, y1, zl, zh, normal_pos=False)
                else:
                    m_nb = cell_mat(k, row, col - 1)
                    if m_nb != m:
                        face_x(m, xa, y0, y1, zl, zh, normal_pos=False)
                if col == w - 1:
                    face_x(m, xb, y0, y1, zl, zh, normal_pos=True)
                else:
                    m_nb = cell_mat(k, row, col + 1)
                    if m_nb != m:
                        face_x(m, xb, y0, y1, zl, zh, normal_pos=True)
                # ---- ±y 面 ----
                # 注意图像行方向：row-1（图上方）对应几何 y1 一侧
                if row == 0:
                    face_y(m, y1, xa, xb, zl, zh, normal_pos=True)
                else:
                    m_nb = cell_mat(k, row - 1, col)
                    if m_nb != m:
                        face_y(m, y1, xa, xb, zl, zh, normal_pos=True)
                if row == h - 1:
                    face_y(m, y0, xa, xb, zl, zh, normal_pos=False)
                else:
                    m_nb = cell_mat(k, row + 1, col)
                    if m_nb != m:
                        face_y(m, y0, xa, xb, zl, zh, normal_pos=False)
        # v1.0.16：逐行回报进度（每 8 行一次）
        if progress is not None and (row % 8 == 0 or row == h - 1):
            progress((row + 1) / float(max(1, h)))
