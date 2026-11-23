# -*- coding: utf-8 -*-
"""叠色配色引擎 —— 纯算法，不依赖 bpy，可离线单测。

机器的真实能力（用户实机确认，v1.2 起按此设计）
================================================
1. **每一层都可以用天蓝 / 黄色 / 品红 / 白色**，同一 Z 高度上允许出现不同颜色
   （换色发生在 XY 平面内）。
2. **Z 轴方向也可以换色** —— 同一根像素柱沿高度可以分段用不同颜色。
3. **颜色没有层序要求** —— 不存在"必须从 C 叠到 W"这类强制顺序。

所以真正要解决的问题是：在"一套机器能打出来的颜色序列"里，怎么选才能让
**远看像原图**、同时**换色次数尽量少**。

两条互不冲突的技术路线
----------------------
``分区配色（region，默认）``
    **v1.3 起是本插件的核心**：把像素颜色投影到色板 sRGB 空间的凸包上，得到
    "哪几种线材各占多少"的叠加比例，再用有序抖动按比例分配。这是"先理解这个
    色相、再用四色叠加逼近它"的算法化 —— 数学上就是**加色最优解**，因为眼睛
    从远处看混色区域时做的正是加色平均。

``色带叠色（coloraware，保留）``
    把色相编码进"顶端落在哪条色带"。同一高度必然同色 → 换色最少，但色相被
    硬量化成 4 档，中间色相（绿/橙/紫）只能并到最近端点。**这条路是"每个高度
    只上一种颜色"，与用户要的"四色叠加"相反**，只作为"极限省换色"的对照保留。

为什么"凸包投影"比"色轮分段抖动"严格更好
------------------------------------------
旧算法把像素色相定位到色轮上相邻的两个彩色再按比例抖动，能还原的颜色**必然
落在 r+g+b = 2 的平面上** —— 天蓝/品红/黄的三通道和都等于 2。于是"往彩色里
掺白"做不到，浅色、粉彩色全部还原不出来。

新算法用 4 个顶点（白 / 天蓝 / 品红 / 黄）张成的**四面体**，覆盖 r+g+b ∈ [2,3]，
严格包含旧的三角形平面。少一种颜色就少一个顶点、凸包变小、能还原的颜色变少，
这个代价是可量化的。

物理边界：为什么深色还原不了
----------------------------
四个线材色都很亮（三通道和是 2 或 3），加色混合**不可能**得到 r+g+b < 2 的
颜色。深色只能靠几何起伏产生的阴影表现 —— 这是耗材限制，不是算法缺陷。

亮度与厚度
----------
厚度**不承担色相**：``region_coverage`` 用 ``1 - 亮度`` 给出"目标色层要多厚"，
越深越厚（越不透白底）。色相归色相、明暗归厚度，两件事彻底解耦。

质量等级
--------
``QUALITY_LEVELS``（constants.py）给出"极致 / 均衡 / 像素"三档。**三档都用满
4 色**（用户要求：颜色够用就不该被砍）；差别只在两处 ——

``dither``        抖动瓦片阶数，决定混色的空间细腻度（还原）。
``weight_levels`` / ``merge_below``
    权重的量化分辨率与并项阈值。**这才是"换色次数"的开关**：把过小的权重并给
    最强的颜色，一个像素就只用一种颜色，整层里出现的颜色种类随之下降。这正是
    用户要的「色相相近就不需要额外换颜色」。
"""

import math
from itertools import permutations

from ..constants import (ALL_COLOR_KEYS, BASE_COLORS, BAND_INSET, BRIGHT_DROP,
                         BLUE_NOISE32_SIZE, DEFAULT_COLOR_BASIS,
                         DEFAULT_DITHER_PATTERN,
                         DEFAULT_STACK_ORDER, DITHER_MATRIX_BY_SIZE,
                         DITHER_PATTERNS, FULL_COVER_MM, RELIEF_ANCHOR_ORDER_CAP,
                         SAT_THRESHOLD, StackPalette, blue_noise_matrix,
                         srgb_to_linear)


# ---------------------------------------------------------------------------
# 基础颜色工具
# ---------------------------------------------------------------------------
def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)


def luminance(rgb):
    """sRGB 相对亮度（Rec.709 系数，在 sRGB 空间近似即可）。"""
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def rgb_to_cmyw(rgb):
    """sRGB -> CMYW 四分量（各 0..1）。

    与油墨叠色的朴素对应：
        w = min(r, g, b)          —— 三通道共同亮度即"白色用量"
        c = 1 - r, m = 1 - g, y = 1 - b

    校验：纯红 (1,0,0) -> w=0, c=0, m=1, y=1（品红+黄=红）✓
          纯白 (1,1,1) -> w=1, c=m=y=0 ✓
          纯黑 (0,0,0) -> w=0, c=m=y=1（三色全叠，厚了就是黑）✓
    """
    r, g, b = (clamp(v) for v in rgb)
    return (min(r, g, b), 1.0 - r, 1.0 - g, 1.0 - b)


def color_distance(a, b):
    """感知加权的 RGB 距离（简单近似，够用于最近色分类）。"""
    dr = a[0] - b[0]
    dg = a[1] - b[1]
    db = a[2] - b[2]
    return math.sqrt(2.0 * dr * dr + 4.0 * dg * dg + 3.0 * db * db)


def mix(a, b, t):
    t = clamp(t)
    return (a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t)


# ---------------------------------------------------------------------------
# 颜色表 / 色带
# ---------------------------------------------------------------------------
def resolve_palette(keys=None, palette=None):
    """把颜色键（'C','M','Y','W'）解析成 sRGB 三元组。

    v1.0.10：**必须保留 ``basis``**。这里返回普通 dict 会把「按理论计算」
    丢掉 → 顶点色静默退回实测表，表现是"叠法明明只有 8 种，视口却是
    一片灰蓝"。generation 的顶点色链路正是走这个函数取的色板。
    """
    keys = list(keys or DEFAULT_STACK_ORDER)
    out = {}
    for k in keys:
        if palette and k in palette:
            out[k] = tuple(float(v) for v in palette[k])
        elif k in BASE_COLORS:
            out[k] = tuple(BASE_COLORS[k]["srgb"])
        else:
            raise ValueError("未知颜色键：%s" % k)
    return StackPalette(out, basis=getattr(palette, "basis", DEFAULT_COLOR_BASIS))


class StackLayer(object):
    """一条色带：颜色 + 它在 Z 轴上占据的区间。"""

    __slots__ = ("index", "key", "srgb", "z_lo", "z_hi")

    def __init__(self, index, key, srgb, z_lo, z_hi):
        self.index = index
        self.key = key
        self.srgb = srgb
        self.z_lo = z_lo
        self.z_hi = z_hi

    @property
    def thickness(self):
        return self.z_hi - self.z_lo

    @property
    def srgb_linear(self):
        return tuple(srgb_to_linear(v) for v in self.srgb)

    def __repr__(self):
        return "<StackLayer %d %s z=%.3f..%.3f>" % (self.index, self.key, self.z_lo, self.z_hi)


def build_stack(order=None, layer_ths=None, base_mm=0.6, relief_mm=3.0,
                base_key="W", palette=None):
    """构造色带序列。

    参数
    ----
    order : list[str]
        从**最底层到最顶层**的颜色键顺序，例如 ['C','M','Y','W']。
    layer_ths : list[float] | float | None
        每条色带的厚度（mm）。传 None 表示各带等厚。
    base_mm : float
        垫底白板的厚度（保证底面平整、不透、能贴板）。
    relief_mm : float
        色带区总起伏高度。
    base_key : str
        垫底材质颜色键。

    返回 ``(base_layer, stack)``：base_layer 是垫底带，stack 是色带列表。
    """
    order = list(order or DEFAULT_STACK_ORDER)
    if not order:
        raise ValueError("色序不能为空")
    pal = resolve_palette(order, palette)

    n = len(order)
    if layer_ths is None:
        ths = [float(relief_mm) / n] * n
    elif isinstance(layer_ths, (int, float)):
        unit = float(layer_ths)
        ths = [unit] * n
    else:
        ths = [float(t) for t in layer_ths]
        if len(ths) != n:
            raise ValueError("色带厚度数量(%d)与色序长度(%d)不一致" % (len(ths), n))

    total = sum(ths)
    if total <= 0:
        raise ValueError("色带总厚度必须大于 0")

    base_layer = StackLayer(-1, base_key, pal.get(base_key, (1.0, 1.0, 1.0)),
                            0.0, max(0.0, float(base_mm)))

    stack = []
    z = base_layer.z_hi
    for i, key in enumerate(order):
        layer = StackLayer(i, key, pal[key], z, z + ths[i])
        stack.append(layer)
        z = layer.z_hi
    return base_layer, stack


def expand_order(order, repeat_per_color=1):
    """把色序展开成更多色阶（每个颜色连续重复 n 次），提升色彩梯度分辨率。"""
    order = list(order or DEFAULT_STACK_ORDER)
    r = max(1, int(repeat_per_color))
    out = []
    for k in order:
        out.extend([k] * r)
    return out


# ---------------------------------------------------------------------------
# 高度映射
# ---------------------------------------------------------------------------
# 青/品红/黄在 RGB 色轮上的色相角（度）
HUE_ANGLE = {"C": 180.0, "M": 300.0, "Y": 60.0}
# 供 hue_band（色带叠色 / 纯亮度路径）使用的"彩色"键 —— 只认 C/M/Y。
# v1.23.0 新增的 R/G/B 不进这里：那条路径在非 CMY 色组下会退化成"整图一个
# 色带"（colors<2 → 白色带），所以面板对非 CMYK 组不提供 coloraware 选项。
_HUE_KEYS = ("C", "M", "Y")

# 颜色键的稳定排序位（canonical_key_order 的唯一依据）。
# ⚠ 前三位 C/M/Y 与 W 的相对次序**不要再动**：所有 3MF/几何/预览的既有基线
# 都是按 C,M,Y,W 的抖动切分点录的，插在 W 之前的 R/G/B 不影响这四键的相对序。
_KEY_RANK = {"C": 0, "M": 1, "Y": 2, "R": 3, "G": 4, "B": 5, "W": 6, "K": 7}


def _unroll_angles(colors):
    """把色序里的彩色按**最短弧**展开成单调序列。

    例：``[C, M, Y]`` -> ``[180, 300, 420]``（顺时针，各跨 120°）
        ``[C, Y, M]`` -> ``[180,  60, -60]``（逆时针，各跨 120°）

    这一步保证了色序被解释成色轮上一段确定的、长度 240° 的弧 ——
    正是它决定了这套配色**能还原哪一段色相**。
    """
    if not colors:
        return []
    angles = [HUE_ANGLE[colors[0]]]
    for k in colors[1:]:
        a = HUE_ANGLE[k]
        prev = angles[-1]
        while a - prev > 180.0:
            a -= 360.0
        while prev - a > 180.0:
            a += 360.0
        angles.append(a)
    return angles


def _rgb_hue(rgb):
    """RGB -> 色相角（度, 0..360）。"""
    r, g, b = rgb
    mx = max(r, g, b)
    mn = min(r, g, b)
    d = mx - mn
    if d <= 1e-9:
        return 0.0
    if mx == r:
        hh = 60.0 * (((g - b) / d) % 6.0)
    elif mx == g:
        hh = 60.0 * (((b - r) / d) + 2.0)
    else:
        hh = 60.0 * (((r - g) / d) + 4.0)
    return hh % 360.0


def _saturation(rgb):
    mx = max(rgb)
    mn = min(rgb)
    return 0.0 if mx <= 1e-9 else (mx - mn) / mx


def hue_band(rgb, order, sat_threshold=0.15):
    """像素颜色 → 色序里的连续色带位置 ∈ [0, N-1]。

    【为什么不用 CMY 分量】第一版用 CMY 加权算色相位置，实测把整张风景图
    渲染成一片品红（93% 的像素挤进同一条色带）。原因：减法分解里"暗"等价于
    "三色叠加" —— 深绿 (0.22,0.42,0.30) 分解出 c=0.78/m=0.58/y=0.70 三个
    都高，于是被判成接近品红。**颜色深浅不该被当成色相**。

    正确做法：用 HSV 色相角。把色序里的 C/M/Y 换算成色相角、沿最短弧展开成
    一条路径，再把像素色相投影到这条路径上；路径之外的色相 clamp 到最近端点。
    低饱和（白/灰）走白色带。
    """
    n = len(order)
    if n <= 1:
        return 0.0
    w_pos = float(order.index("W")) if "W" in order else float(n - 1)

    rgb = tuple(clamp(v) for v in rgb)
    if _saturation(rgb) < sat_threshold:
        return w_pos

    colors = [k for k in order if k in _HUE_KEYS]
    if len(colors) < 2:
        return w_pos

    angles = _unroll_angles(colors)
    a0, a1 = angles[0], angles[-1]
    span = abs(a1 - a0)
    if span <= 1e-9:
        return float(order.index(colors[0]))

    hh = _rgb_hue(rgb)
    rel = (hh - a0) % 360.0 if a1 >= a0 else (a0 - hh) % 360.0
    t = 1.0 if rel >= span else (rel / span)

    positions = [float(order.index(k)) for k in colors]
    f = t * (len(positions) - 1)
    i = int(f)
    if i >= len(positions) - 1:
        return positions[-1]
    frac = f - i
    return positions[i] + (positions[i + 1] - positions[i]) * frac


def hue_position(rgb, order):
    """归一化到 [0,1] 的色带位置（UI/测试展示用）。"""
    n = len(order)
    if n <= 1:
        return 0.5
    return clamp(hue_band(rgb, order) / float(n - 1))


# ---------------------------------------------------------------------------
# 分区配色（region）：四色叠加混色 —— 色相驱动、逐像素独立选色
# ---------------------------------------------------------------------------
# 【v1.3 的核心改动：从"色轮分段抖动"改为"色板凸包投影"】
#
# 旧做法：把像素色相定位到色轮上相邻的两个彩色（如 黄→青），再按段内比例抖动。
# 它能还原的颜色**必然落在 r+g+b = 2 的平面上** —— 天蓝/品红/黄三个端点的三通道
# 和都等于 2，两两加色混合还是 2。于是"往彩色里掺一点白"这个最常见的提亮动作
# 根本做不到，所有浅色、粉彩色都还原不出来。
#
# 新做法（用户的原话："应该先理解每个颜色的色相，再基于 4 色叠加产生接近于这个
# 色相"）：把目标色**投影到色板在 sRGB 空间里的凸包**上，得到一组"叠加比例"
# 权重，再按这组比例做有序抖动。数学上这就是"用这几种线材能叠出来的、离目标色
# 最近的颜色"的最优解；而且用的是**加色**口径 —— 眼睛从远处看混色区域时做的
# 正是加色平均，所以这个模型和物理观感一致。
#
# 4 个顶点（白 (1,1,1) / 天蓝 (0,1,1) / 品红 (1,0,1) / 黄 (1,1,0)）张成一个
# 四面体，覆盖 r+g+b ∈ [2, 3] 的全部颜色 —— 比旧的三角形平面严格更大。少一种
# 颜色就是少一个顶点、凸包变小、能还原的颜色变少，这个代价是**量化可测**的。
#
# 【顺带解决了"色相相近就不需要额外换颜色"】
#   目标色越贴近某个线材色，投影权重就越集中在那个顶点；权重低于阈值就并掉
#   → 该像素只用一种颜色 → 不需要为它换色。这正是用户要的那条规则，而且是
#   投影算法自然给出的结果，不需要任何特判。
#
# 【物理边界：为什么深色还原不了】
#   四个线材色都很亮（三通道和是 2 或 3），加色混合**不可能**得到 r+g+b < 2
#   的颜色。深色只能靠几何起伏产生的阴影去表现 —— 这是耗材本身的限制，不是
#   算法缺陷。要真正的深色必须换深色耗材。
DEFAULT_DITHER_SIZE = 4


def dither_threshold(row, col, size=DEFAULT_DITHER_SIZE, pattern=None,
                     block=1):
    """抖动阈值 ∈ [0,1)。

    ``pattern`` 决定阈值图案（v1.7.2）：

    * ``"bayer"`` —— Bayer 有序矩阵，``size`` = 4（16 级）/ 8（64 级）。
      阈值图案是**规则方格**，每 2^n 格精确重复 —— 人眼对规则图案特别敏感，
      同一振幅的颗粒看起来更"脏"。
    * ``"bluenoise"`` —— 32×32 蓝噪声（void-and-cluster），低频功率近 0、
      无离散谱峰，颗粒像胶片噪点。``size`` 对它无意义（忽略）。

    【为什么换图案不影响还原度】有序抖动的呈现色只取决于阈值的**边缘分布**
    （每个像素抽到的 τ 的分布）。两种图案都是均匀分布 U[0,1) 且每个值恰好
    出现一次，所以"远看平均色"逐位相同；变的只是颗粒的空间排布。

    ``block``（R18 块状抖动）：>1 时整块 ``block×block`` 像素共用**块锚点格**
    （块中心）的 τ —— 抖动色斑的最小宽度从 1 像素提升到 ``block`` 像素。
    【为什么取块锚点而不是块平均】锚点格的 τ 仍是原图案的均匀采样，块级
    边际分布**严格**保持 U[0,1)（每块恰好抽一个值），混合比 7:3 依然精确；
    块平均会把分布向 0.5 收拢，小权重颜色（如 5% 的点缀色）会被系统性吞掉。
    【为什么需要】像素尺寸小于喷嘴线宽时（如 75mm 卡 / 313px = 0.24mm <
    0.42mm），1 像素抖动斑点在切片器的小特征过滤下被并回主导色 —— 实测
    2026-09-30，25mm 卡对照：同尺寸 dither 卡彩色全灭、solid 卡四色全出。
    块宽由 ``generation.dither_block_for`` 按像素/线宽比自适应。

    纯查表：无随机数、无串行依赖，同一张图重复生成的结果逐像素一致。
    """
    pat = pattern or DEFAULT_DITHER_PATTERN
    if pat not in DITHER_PATTERNS:
        pat = DEFAULT_DITHER_PATTERN
    b = max(1, int(block or 1))
    if b > 1:
        row = (int(row) // b) * b + b // 2
        col = (int(col) // b) * b + b // 2
    if pat == "bluenoise":
        mat = blue_noise_matrix()
        n = BLUE_NOISE32_SIZE
        return mat[int(row) % n][int(col) % n]
    entry = DITHER_MATRIX_BY_SIZE.get(int(size))
    if entry is None:
        entry = DITHER_MATRIX_BY_SIZE[DEFAULT_DITHER_SIZE]
    mat, levels = entry
    mask = len(mat) - 1
    return mat[int(row) & mask][int(col) & mask] / float(levels)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _dist2(a, b):
    d0 = a[0] - b[0]
    d1 = a[1] - b[1]
    d2 = a[2] - b[2]
    return d0 * d0 + d1 * d1 + d2 * d2


def _seg_weights(t, a, b):
    """最近点落在一条线段上时的权重 ``(wa, wb)``。"""
    ab = _sub(b, a)
    den = _dot(ab, ab)
    if den <= 1e-12:
        return (1.0, 0.0)
    u = clamp(_dot(_sub(t, a), ab) / den)
    return (1.0 - u, u)


def _tri_weights(t, a, b, c):
    """最近点落在一个三角形上时的重心权重 ``(wa, wb, wc)``。

    标准算法（Ericson, Real-Time Collision Detection）：先判断投影落在哪个
    顶点/边区域，落在内部时才解平面重心坐标。必须这样分情况，直接解平面方程
    再 clamp 会在钝角三角形上给出错误的最近点。
    """
    ab = _sub(b, a)
    ac = _sub(c, a)
    ap = _sub(t, a)
    d1 = _dot(ab, ap)
    d2 = _dot(ac, ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return (1.0, 0.0, 0.0)
    bp = _sub(t, b)
    d3 = _dot(ab, bp)
    d4 = _dot(ac, bp)
    if d3 >= 0.0 and d4 <= d3:
        return (0.0, 1.0, 0.0)
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        # 【v1.4.4 防御】a、b 重合（锚点呈现色重复）时 d1=d3=0，直接除会崩；
        # 退化时退回顶点 a 的候选，外层 mix_weights 会用其他子集取最小距离兜底
        den = d1 - d3
        if den > 1e-12:
            v = d1 / den
            return (1.0 - v, v, 0.0)
        return (1.0, 0.0, 0.0)
    cp = _sub(t, c)
    d5 = _dot(ab, cp)
    d6 = _dot(ac, cp)
    if d6 >= 0.0 and d5 <= d6:
        return (0.0, 0.0, 1.0)
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        den = d2 - d6
        if den > 1e-12:
            w = d2 / den
            return (1.0 - w, 0.0, w)
        return (1.0, 0.0, 0.0)
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        den = (d4 - d3) + (d5 - d6)
        if den > 1e-12:
            w = (d4 - d3) / den
            return (0.0, 1.0 - w, w)
        return (0.0, 1.0, 0.0)
    tot = va + vb + vc
    if tot > 1e-12:
        den = 1.0 / tot
        v = vb * den
        w = vc * den
        return (1.0 - v - w, v, w)
    # 完全退化（三点共线/重合且都落不进上面任何分支）→ 顶点候选
    return (1.0, 0.0, 0.0)


def _tet_weights(t, a, b, c, d):
    """最近点在四面体内部时的重心权重；任一分量为负则返回 None。

    None 表示"最近点不在内部、而在某个面上"，外层会退到三点/两点/单点候选。
    """
    e1 = _sub(b, a)
    e2 = _sub(c, a)
    e3 = _sub(d, a)
    m00, m01, m02 = e1[0], e2[0], e3[0]
    m10, m11, m12 = e1[1], e2[1], e3[1]
    m20, m21, m22 = e1[2], e2[2], e3[2]
    det = (m00 * (m11 * m22 - m12 * m21)
           - m01 * (m10 * m22 - m12 * m20)
           + m02 * (m10 * m21 - m11 * m20))
    if abs(det) <= 1e-12:
        return None
    r0, r1, r2 = _sub(t, a)
    x = (r0 * (m11 * m22 - m12 * m21)
         - m01 * (r1 * m22 - m12 * r2)
         + m02 * (r1 * m21 - m11 * r2)) / det
    y = (m00 * (r1 * m22 - m12 * r2)
         - r0 * (m10 * m22 - m12 * m20)
         + m02 * (m10 * r2 - r1 * m20)) / det
    z = (m00 * (m11 * r2 - r1 * m21)
         - m01 * (m10 * r2 - r1 * m20)
         + r0 * (m10 * m21 - m11 * m20)) / det
    w0 = 1.0 - x - y - z
    if w0 < -1e-9 or x < -1e-9 or y < -1e-9 or z < -1e-9:
        return None
    return (w0, x, y, z)


_SUBSET_CACHE = {}


def _subsets(n):
    """大小 1..min(n,4) 的全部下标子集。四面体的最近点只可能是顶点/棱/面/内部，
    所以枚举这些子集就能得到精确解。"""
    got = _SUBSET_CACHE.get(n)
    if got is not None:
        return got
    from itertools import combinations
    out = []
    for size in range(1, min(n, 4) + 1):
        out.extend(combinations(range(n), size))
    _SUBSET_CACHE[n] = out
    return out


def mix_weights(target, verts):
    """目标色 → 色板上的**叠加比例**（色板凸包上的最近点投影）。

    返回与 ``verts`` 等长的权重列表：非负、和为 1、非零项就是该像素实际要用
    的线材。``verts`` 是 ``[(key, (r,g,b)), ...]``。

    这是"理解色相 → 用 4 色叠加逼近它"的具体算法：
    - 目标色正好是某个线材色 → 权重是 (1,0,0,0)，**不需要为它换色**；
    - 目标色在两个线材色之间（如绿 = 黄+青）→ 权重各占一半，靠抖动混出来；
    - 目标色偏亮 → 白色顶点自动分到较大权重（这是旧算法做不到的）。

    ⚠ **必须在 sRGB（对权重线性的）空间里求最近点，不要改成 CIELAB。**
    呈现色 = 各顶点 sRGB 的**加权平均**（抖动做空间加色平均），所以
    "权重 → 达成色"在这组顶点坐标里是**线性**映射，闭式重心解才成立。
    v1.7.8 一度把顶点换成 Lab 坐标再照样线性加权平均 —— 那求得的是"Lab
    坐标的线性插值"，而 sRGB→Lab 是非线性映射，这个插值点**任何权重都做不
    出来**，等于在优化一个幻影点，真实达成色反而更差。实测
    （`tests/out/_exp_metric_matrix.py`，照片 + 2000 个随机色）：

    ====================  ==============  ==============
    顶点集                真实达成 ΔE 中位
    --------------------  --------------  --------------
                           sRGB 求解       Lab 求解
    4 单色（随机色）        16.85           24.51
    4 单色（照片,块级）     6.52            18.86
    8 滤光锚点（照片）      5.41            5.91
    ====================  ==============  ==============

    即"感知空间投影"看着更合理，实测是负收益；色差的真正改善全部来自
    **锚点集由 4 个单色扩到 8 个滤光锚点**（见 ``stack_anchors``）。
    也试过"几何仍解 sRGB、候选子集之间改用 CIE76 排序"，实测只在噪声内
    （照片中位 5.41 → 5.26，暗部反而 31.91 → 31.98），故不采用。
    """
    n = len(verts)
    if n <= 0:
        return []
    if n == 1:
        return [1.0]
    t = (clamp(target[0]), clamp(target[1]), clamp(target[2]))
    best_w = None
    best_d = None
    for combo in _subsets(n):
        pts = [verts[i][1] for i in combo]
        m = len(pts)
        if m == 1:
            ws = (1.0,)
        elif m == 2:
            ws = _seg_weights(t, pts[0], pts[1])
        elif m == 3:
            ws = _tri_weights(t, pts[0], pts[1], pts[2])
        else:
            ws = _tet_weights(t, pts[0], pts[1], pts[2], pts[3])
            if ws is None:
                continue
        p = (sum(ws[i] * pts[i][0] for i in range(m)),
             sum(ws[i] * pts[i][1] for i in range(m)),
             sum(ws[i] * pts[i][2] for i in range(m)))
        d = _dist2(t, p)
        if best_d is None or d < best_d:
            best_d = d
            full = [0.0] * n
            for i, idx in enumerate(combo):
                full[idx] = ws[i]
            best_w = full
    if best_w is None:
        far = min(range(n), key=lambda i: _dist2(t, verts[i][1]))
        best_w = [0.0] * n
        best_w[far] = 1.0
    return best_w


def cap_components(weights, keys, cap):
    """【R8（v1.11.0）】权重分量数上限：保留最大的 ``cap`` 个分量，重新归一。

    这是"像素"档省换色的主杠杆：每像素最多用 ``cap`` 种锚点堆叠（cap=1 即
    每像素只取最近的 1 种），堆叠种类从 35~40 降到 12~15。cap<=0 原样返回；
    已 ≤cap 的权重原样返回（不重归一，保持与旧链路逐位一致）。
    平局按键序（keys 的顺序）破——确定性要求。

    【为什么必须重新归一到和 1】``Mixer.pick`` 的抖动 CDF 以权重和=1 为前提
    （tau 超出累计和时会兜底到 keys[-1]，产生假色）。丢掉小分量后把保留下来的
    分量按原比例放大，远看呈现色只朝保留色方向轻微偏移，行为可预期。
    """
    if not cap or cap <= 0:
        return weights
    nz = [(k, weights.get(k, 0.0)) for k in keys if weights.get(k, 0.0) > 1e-9]
    if len(nz) <= cap:
        return weights
    nz.sort(key=lambda kv: (-kv[1], keys.index(kv[0])))
    s = 0.0
    for _, v in nz[:cap]:
        s += v
    if s <= 0.0:
        return weights
    out = dict((k, 0.0) for k in keys)
    for k, v in nz[:cap]:
        out[k] = v / s
    return out


def quantize_weights(weights, keys, weight_levels=0.0, merge_below=0.0):
    """权重 → 量化 + 并项 → 该像素实际"用几种颜色"。

    ``weight_levels``
        权重的最小分辨率。0 = 连续不量化（还原最准，用色最多）。
        1/4 → 权重只取 0/.25/.5/.75/1，量化后次要项容易掉到阈值以下被并掉。
    ``merge_below``
        小于它的权重直接并给最强色并重新归一 —— 这就是用户要的
        「色相相近就不需要额外换颜色」：目标色贴近某个线材色时，投影权重本来
        就集中在那个顶点上，量化后其余项掉到阈值以下，该像素只用一种颜色，
        整层里出现的颜色种类随之减少，换色次数下降。

    返回 ``{key: 权重}``（非零项之和为 1）。
    """
    keys = list(keys)
    raw = dict(zip(keys, weights))
    q = float(weight_levels or 0.0)
    if q > 1e-9:
        out = dict((k, round(raw.get(k, 0.0) / q) * q) for k in keys)
    else:
        out = dict((k, raw.get(k, 0.0)) for k in keys)

    th = float(merge_below or 0.0)
    if th > 1e-9:
        for k in keys:
            if out[k] < th:
                out[k] = 0.0
    total = sum(out.values())
    if total <= 1e-12:
        # 全被并掉了（理论上只在阈值为 1/2 时出现）→ 保留原始最强的那一色
        strong = max(keys, key=lambda k: raw.get(k, 0.0))
        return dict((k, 1.0 if k == strong else 0.0) for k in keys)
    return dict((k, out[k] / total) for k in keys)


class Mixer(object):
    """按固定色板把像素颜色投影成叠加权重的可复用器。

    生成/预览一张图要跑几十万次投影，这里做两件事：
    1. 目标色先量化到 6 bit 再查表 —— 权重结果与查表键严格一致（不是"近似命中"），
       既去掉浮点抖动，又让重复颜色直接命中缓存；
    2. 顶点表与子集列表只算一次。
    """

    QUANT = 63.0          # 6 bit：色板投影的输入分辨率，远高于抖动粒度

    def __init__(self, keys, palette=None, weight_levels=0.0, merge_below=0.0,
                 dither_size=DEFAULT_DITHER_SIZE, white_snap=1.01,
                 white_key="W", dither_pattern=None, near_k=0,
                 max_components=0, dither_block=1):
        self.keys = canonical_key_order(keys)
        self.levels = float(weight_levels or 0.0)
        self.merge_below = float(merge_below or 0.0)
        # R8：每像素混色分量上限（0 = 不限）。见 cap_components。
        self.max_components = int(max_components or 0)
        self.dither_size = int(dither_size or 0)
        self.dither_pattern = dither_pattern or DEFAULT_DITHER_PATTERN
        # R18：块状抖动块边长（1 = 逐像素，行为与 R17 及以前逐位一致）
        self.dither_block = max(1, int(dither_block or 1))
        self.white_snap = float(white_snap)
        self.white_key = white_key
        # R7：大锚点集（n>ANCHOR_NEAR_K 的 grid 档）用「最近 K 预选 + 精确
        # 投影」；0 = 全量精确投影（4 色板 / 基础 8 锚点 / 双面镜像 36 锚点）。
        self.near_k = int(near_k or 0)
        pal = resolve_palette(self.keys, palette)
        self.verts = [(k, tuple(pal[k])) for k in self.keys]
        # 顶点只转一次 Lab（投影判据用），逐像素复用 —— 否则每个像素都要
        # 重算 n 个顶点的色空间转换
        self.verts_lab = [(k, _srgb_to_lab(v)) for k, v in self.verts]
        self._cache = {}

    def weights(self, rgb):
        q = self.QUANT
        ck = (int(clamp(rgb[0]) * q + 0.5),
              int(clamp(rgb[1]) * q + 0.5),
              int(clamp(rgb[2]) * q + 0.5))
        got = self._cache.get(ck)
        if got is None:
            tgt = (ck[0] / q, ck[1] / q, ck[2] / q)
            if self.near_k:
                raw = mix_weights_near(tgt, self.verts, self.near_k,
                                       self.white_key)
            else:
                raw = mix_weights(tgt, self.verts)
            wk = self.white_key
            snap = (self.white_snap < 1.0
                    and wk in self.keys and len(self.keys) > 1
                    and raw[self.keys.index(wk)] >= self.white_snap)
            if snap:
                # 【近白像素直接给纯白 —— 这是省换色最有效的一招】
                # 浅灰/近白的色相本来就不可辨（饱和度趋近 0，"色相"没有意义）。
                # 凸包投影为了追求数值精确，会把它拆成"三种彩色各占两成 + 白
                # 四成"，结果每一层都被迫在四种耗材之间切换 —— 纯属白换色。
                # 直接给纯白：观感几乎没差别（本来就近白），而该像素所在层少
                # 出三种颜色。用户要的「色相相近就不需要额外换颜色」正是这条。
                got = dict((k, 1.0 if k == wk else 0.0) for k in self.keys)
            else:
                got = quantize_weights(raw, self.keys, self.levels,
                                       self.merge_below)
                if self.max_components:
                    got = cap_components(got, self.keys, self.max_components)
            self._cache[ck] = got
        return got

    def pick(self, rgb, row=0, col=0):
        """按权重做有序抖动，选出该像素实际使用的那一种颜色。

        抖动按**权重的累积分布**切分：权重 0.7 / 0.3 的两种颜色，会在每个
        抖动瓦片里按 7:3 的格数分配 —— 远看就是按 7:3 加色混合的结果。
        ``dither_size <= 0`` 时不做抖动，直接取权重最大的那一色（大片纯色块）。
        阈值图案由 ``dither_pattern`` 决定（bayer / bluenoise，见
        ``dither_threshold``）。
        """
        w = self.weights(rgb)
        size = self.dither_size
        if size <= 0:
            return max(self.keys, key=lambda k: w.get(k, 0.0))
        tau = dither_threshold(row, col, size, self.dither_pattern,
                               block=self.dither_block)
        cum = 0.0
        for k in self.keys:
            cum += w.get(k, 0.0)
            if tau < cum - 1e-12:
                return k
        return self.keys[-1]

    def present(self, rgb):
        """远看呈现色 = 权重加色平均 = 凸包投影点（预览图用）。"""
        w = self.weights(rgb)
        r = g = b = 0.0
        for k, srgb in self.verts:
            wk = w.get(k, 0.0)
            if wk > 0.0:
                r += srgb[0] * wk
                g += srgb[1] * wk
                b += srgb[2] * wk
        return (clamp(r), clamp(g), clamp(b))

    def used_keys(self, rgb=None):
        """该像素实际用到的颜色（权重非零项）。rgb 省略时返回全色板。"""
        if rgb is None:
            return list(self.keys)
        return [k for k in self.keys if self.weights(rgb).get(k, 0.0) > 0.0]


def canonical_key_order(keys):
    """把颜色键列表规范成固定顺序（色轮顺序 + 白最后 + 黑最末）。

    【为什么必须规范】四色叠加的**权重**只取决于"哪几种颜色参与"（凸包与顺序
    无关），但有序抖动是按权重的**累积分布**切分的，切分点会随键的顺序移动。
    所以只有把键固定成同一顺序，才谈得上"颜色没有层序要求"—— 用户怎么勾选、
    传进来的列表怎么排列，结果都逐像素一致。

    【v1.7.0】**保留重复键**：色深叠色卡的深色靠同色重复层（参考卡 95.2%
    的列含重复色），镜像展开与呈现计算必须保住重复；此前的 dict.fromkeys
    去重会让几何悄悄丢掉重复层，预览与实际打印脱节。去重依赖方（分区配色
    等）传入的键本来就唯一，行为不变。

    【v1.23.0 任意色组】此前只认 C/M/Y/W（其余键被静默丢弃），四档混色组
    （RYBW 的 R/B、CMYWGK 的 G/K、8色MAX）会被整组丢成空集。现改为按
    ``_KEY_RANK`` 排序。**既有 C/M/Y/W 的相对顺序一位不动**（C→M→Y→W），
    所以所有旧基线逐位不变；新增键插在 W 之前，W 仍固定最后（它是"白顶点/
    垫底色"），K 排在 W 之后（最暗端，抖动切分上让黑落在分布末尾）。
    """
    known = [k for k in keys if k in _KEY_RANK]
    return sorted(known, key=lambda k: _KEY_RANK[k])


def region_coverage(rgb, brightness_weight=1.0, invert=False, gamma=1.0):
    """像素亮度 → 目标色层厚度比例 ∈ [0,1]。

    叠色的物理是"颜色越深 = 目标色层越厚（越不透白底）"，所以默认用
    ``1 - 亮度``，与 HueForge 的"暗 = 叠更多层 = 更高"一致；``invert`` 翻转。

    ``brightness_weight`` 控制**厚度动态范围**：1 = 完整明暗层次；
    越小越接近等厚（明暗差被压平，颜色完全靠抖动表现）。
    """
    lum = clamp(luminance(rgb))
    cov = lum if invert else (1.0 - lum)
    w = clamp(brightness_weight)
    if w < 1.0:
        cov = cov * w + 0.5 * (1.0 - w)
    if gamma != 1.0:
        g = max(1e-3, float(gamma))
        cov = cov ** g
    return clamp(cov)


def region_plan(rgb, order, row=0, col=0, brightness_weight=1.0,
                invert=False, gamma=1.0, palette=None,
                weight_levels=0.0, merge_below=0.0,
                dither_size=DEFAULT_DITHER_SIZE, mixer=None,
                white_snap=1.01, dither_pattern=None, dither_block=1):
    """一次算出该像素的 ``(颜色键, 目标色层厚度比例)``。

    分区配色的唯一入口 —— 生成几何、生成预览、统计诊断都走它，
    保证"所见即所打"。

    ``mixer`` 给出时复用它（含投影查表缓存）—— 逐像素调用时务必传，否则
    每个像素都要重建色板与子集表。``palette`` 是可选的实际耗材色覆盖
    （拓竹套装色），不给就用 ``constants.BASE_COLORS``。
    """
    if mixer is None:
        mixer = Mixer(order, palette, weight_levels, merge_below, dither_size,
                      white_snap=white_snap, dither_pattern=dither_pattern,
                      dither_block=dither_block)
    key = mixer.pick(rgb, row, col)
    cov = region_coverage(rgb, brightness_weight, invert, gamma)
    return key, cov


# ---------------------------------------------------------------------------
# 自由配色：挑选用哪几种颜色（没有层序概念）
# ---------------------------------------------------------------------------
def hue_stats(grid, bins=36, sat_threshold=SAT_THRESHOLD, sample_limit=8000):
    """图片的色相直方图 + 低饱和占比。供 choose_palette_keys 与 UI 展示使用。

    返回 ``{"hist": [...36], "colored": int, "gray_share": float}``。
    """
    flat = []
    for row in grid:
        flat.extend(row)
    hist = [0] * bins
    colored = 0
    low_sat = 0
    if not flat:
        return {"hist": hist, "colored": 0, "gray_share": 0.0}
    step = max(1, len(flat) // max(1, int(sample_limit)))
    samples = flat[::step]
    for px in samples:
        if _saturation(px) >= sat_threshold:
            colored += 1
            idx = int(_rgb_hue(px) / (360.0 / bins)) % bins
            hist[idx] += 1
        else:
            low_sat += 1
    return {"hist": hist, "colored": colored,
            "gray_share": low_sat / float(len(samples))}


def _cmy_coverage(colors, hist, bins):
    """给定参与配色的 CMY 子集，估算"色相覆盖得分"（加权直方图占比）。

    段跨度 ≤ 180°：该段内的色相都能靠两端点抖动还原 → 权重 1.0。
    段跨度 > 180°：只有一条"绕远路"的混合路径，中间会发灰 → 权重 0.35。
    彩色不足 2 种：无法抖动 → 全部落到那一种颜色上，权重 0.15。
    """
    total = float(sum(hist)) or 1.0
    if len(colors) < 2:
        return 0.15
    angs = sorted(HUE_ANGLE[k] for k in colors)
    n = len(angs)
    gaps = []
    for i in range(n):
        gap = (angs[(i + 1) % n] - angs[i]) % 360.0
        gaps.append((angs[i], gap))
    score = 0.0
    for b, cnt in enumerate(hist):
        if not cnt:
            continue
        hh = b * (360.0 / bins) + 180.0 / bins
        w = 0.15
        for a, gap in gaps:
            if (hh - a) % 360.0 <= gap + 1e-9:
                w = 1.0 if gap <= 180.0 else 0.35
                break
        score += cnt * w
    return score / total


def choose_palette_keys(grid, max_colors=None, allowed=None,
                        prefer_white=True, sat_threshold=SAT_THRESHOLD):
    """在候选颜色里挑出"色彩还原最好"的一套（≤ max_colors 种）。

    【为什么按覆盖挑而不是按用量排】色相能不能还原，取决于"该色相所在色轮段的
    两个端点颜色是否都参与"。段端点叠印产物就是该段的中间色相（黄+青=绿、
    青+品红=蓝、品红+黄=红）。所以挑色 = 挑一组色轮覆盖最广的颜色。

    白色单独算：低饱和像素（灰/黑/白）只有靠白色才能还原，所以画面灰调占比
    可观时白会被算成"必要"。覆盖率几乎相同时**用色少的胜** —— 少一种颜色
    就少一类换色。

    ``allowed``：用户勾选的"允许使用"颜色（顺序无意义，只有集合有意义）。
    ``max_colors``：质量等级给的整机用色上限。两者共同约束搜索。
    """
    allowed = [k for k in (ALL_COLOR_KEYS if allowed is None else allowed)
               if k in ALL_COLOR_KEYS]
    if not allowed:
        allowed = list(ALL_COLOR_KEYS)
    cap = len(allowed) if max_colors is None else int(max_colors)
    cap = max(1, min(cap, len(allowed)))

    st = hue_stats(grid, sat_threshold=sat_threshold)
    hist = st["hist"]
    gray = float(st["gray_share"])

    cmy_pool = [k for k in _HUE_KEYS if k in allowed]
    has_white = bool(prefer_white and "W" in allowed)

    best, best_score, best_n = None, -1.0, 0
    for use_white in ([False, True] if has_white else [False]):
        budget = cap - (1 if use_white else 0)
        if budget < 0:
            continue
        for mask in range(1 << len(cmy_pool)):
            sub = [cmy_pool[i] for i in range(len(cmy_pool)) if mask & (1 << i)]
            if len(sub) > budget:
                continue
            keys = list(sub) + (["W"] if use_white else [])
            if not keys:
                continue
            cov_color = _cmy_coverage(sub, hist, len(hist))
            if use_white:
                cov = cov_color * (1.0 - gray) + gray * 1.0
            elif gray > 0.0:
                cov = cov_color * (1.0 - gray) + gray * 0.20
            else:
                cov = cov_color
            n = len(keys)
            better = (best is None or cov > best_score + 5e-3
                      or (abs(cov - best_score) <= 5e-3 and n < best_n))
            if better:
                best, best_score, best_n = keys, cov, n

    if best is None:
        best = list(allowed[:cap])

    # 按色轮顺序输出（与用户勾选顺序无关，纯粹为了展示稳定）
    out = [k for k in _HUE_KEYS if k in best]
    if "W" in best:
        out.append("W")
    return out



def map_height(rgb, order, mode="coloraware", brightness_weight=1.0,
               invert=False, gamma=1.0):
    """把单个像素颜色映射成色带区内的相对高度 h ∈ [0,1]。

    mode
    ----
    ``region``（默认，推荐）
        ``h = region_coverage(rgb, ...)``。色相**不再编码进高度**，而是逐像素
        独立选材质；高度只表示"目标色层要多厚"。中间色相靠 XY 平面的有序
        抖动还原。还原度最高，代价是同层可能要多次换色。
    ``coloraware``
        ``t = hb + 1 - BAND_INSET - (1-lum) * BRIGHT_DROP * weight``，
        ``h = t / N``。色相选带、亮度定带内厚度。四条锚点色都落回自己的带。
        同一 Z 高度必然同材质 —— 换色次数最少，但色相只有 4 档。
    ``luminance``
        ``h = brightness``。经典 HueForge 灰度模式，只看明暗。

    【v1.3 删除了 ``hybrid``（混合过渡）】它的公式是
    ``h = hue_position * (1-w) + brightness * w``，默认 ``w = 1`` 时退化成
    ``h = brightness``，与 ``luminance`` 逐像素完全一致；把 w 扫到 0 色相偏差
    反而从 32.4° 涨到 60.2°，在任何滑块位置都不优于纯亮度。证据见
    ``tests/t_probe_modes.py``。
    """
    n = max(1, len(order))
    lum = luminance(rgb)
    if invert:
        lum = 1.0 - lum
    lum = clamp(lum)

    if mode == "region":
        # 注意：region_coverage 内部已经应用过 gamma，这里不能再应用一次，
        # 否则 gamma=2 会变成 4 次方（实测对比度会过冲）。
        return clamp(region_coverage(rgb, brightness_weight, invert, gamma))
    if mode == "luminance":
        h = lum
    else:  # coloraware：色相选带、亮度定带内厚度
        # 【为什么基准档位是 hb + 1 而不是 hb】
        # color_at_height 的呈现模型是"落在色带 k 内、露出比例 u 时显示
        # mix(c_{k-1}, c_k, u)"——即**混合发生在上面那条带的内部**。
        # 于是"显示纯色 c_k"对应 u=1、z=z_hi(带 k)，也就是色带边界；
        # 而 hue_band 把纯色放在整数 k 上。两者相差整整一条带：
        #     hb = k     → t = k+1     → 带 k 顶端          → 纯色 c_k        ✓
        #     hb = k + f → t = k+1+f   → 带 k+1 内露出 f    → c_k 与 c_{k+1} 混合 f ✓
        # 早先写成 t = hb + lum，等于把亮度也当成"额外档位"，于是绿
        # (144°, hb≈1.30) 被压进青带、草地打印成蓝紫 —— 这是上一版整片偏色的根因。
        hb = hue_band(rgb, order)
        w = clamp(brightness_weight)
        t = (hb + 1.0 - BAND_INSET
             - (1.0 - lum) * BRIGHT_DROP * w)
        h = t / float(n)

    h = clamp(h)
    if gamma != 1.0:
        g = max(1e-3, float(gamma))
        h = h ** g
    return clamp(h)


def height_to_layer_index(h, stack):
    """给定 h ∈ [0,1]，返回它落在色带列表中的下标。"""
    if not stack:
        return 0
    z_lo = stack[0].z_lo
    z_span = stack[-1].z_hi - z_lo
    if z_span <= 0:
        return 0
    z = z_lo + clamp(h) * z_span
    for layer in stack:
        if z < layer.z_hi:
            return layer.index
    return stack[-1].index


def h_to_z(h, base_layer, stack):
    """h ∈ [0,1] -> 绝对 Z 坐标（mm）。"""
    z_lo = stack[0].z_lo
    z_span = stack[-1].z_hi - z_lo
    return z_lo + clamp(h) * z_span


# ---------------------------------------------------------------------------
# 叠色呈现色预测（用于预览图）
# ---------------------------------------------------------------------------
# 为什么不是 Beer-Lambert
# ----------------------
# 第一版用 ``1 - exp(-t/TD)`` 做四层透射加权，实测预览整张糊成白色：
# 真实 PLA 在 0.2mm 量级就已基本不透光，图像其实来自"顶端**露出**了
# 多厚的顶层"，而不是四层均匀相加。
#
# 正确的近似：像素顶端落在第 k 条色带内、露出比例为 u 时，
#   u 小 → 顶层太薄，透出下层颜色
#   u 大 → 顶层够厚，完全被本层颜色主导
# 于是呈现色就是 [下层色, 本层色] 之间按 u 的插值。既简单又符合直觉，
# 且天然给出"带内连续渐变"——这正是叠色能表现连续色域的原因。
def color_at_height(z, stack, base_srgb=(1.0, 1.0, 1.0)):
    """给定绝对高度 z，返回从正上方看大致呈现的颜色（sRGB）。"""
    if not stack:
        return tuple(base_srgb)
    for k, layer in enumerate(stack):
        if z < layer.z_hi - 1e-9:
            u = (z - layer.z_lo) / max(layer.thickness, 1e-9)
            below = stack[k - 1].srgb if k > 0 else tuple(base_srgb)
            return mix(below, layer.srgb, clamp(u))
    return tuple(stack[-1].srgb)


def region_present_color(rgb, order, stack, brightness_weight=1.0,
                         invert=False, gamma=1.0, base_srgb=(1.0, 1.0, 1.0),
                         row=0, col=0, palette=None,
                         weight_levels=0.0, merge_below=0.0,
                         dither_size=DEFAULT_DITHER_SIZE, mixer=None,
                         white_snap=1.01, dither_pattern=None):
    """分区配色的呈现色估算：**四色的加色平均**，再按目标层厚度往白底方向回混。

    【为什么是加色平均】抖动把相邻像素分别刷成不同线材，眼睛从远处看是把它们
    的光**相加**平均，所以呈现色就是权重加色平均 —— 也正是凸包投影点。这个
    口径与生成时"权重 → 抖动分配"严格对应，做到"所见即所打"。

    【另一层回混：白底透出】目标色层越薄，下面的白色基座越容易透出来，所以再
    按 ``cov`` 往 ``base_srgb`` 方向回混一次。

    【为什么与共用底带无关】底带只改变**侧壁**在下方那一截的颜色；从正上方
    看到的是柱子的顶面，仍是像素自己的颜色。所以预览（正上方视角）不需要
    为底带做任何修正 —— 这也是"减换色而不牺牲顶面还原"的根据。
    """
    if palette is None:
        palette = {}
        for l in (stack or ()):
            palette.setdefault(l.key, l.srgb)
    if mixer is None:
        mixer = Mixer(order, palette, weight_levels, merge_below, dither_size,
                      white_snap=white_snap, dither_pattern=dither_pattern)
    present = mixer.present(rgb)
    cov = region_coverage(rgb, brightness_weight, invert, gamma)
    relief_mm = (stack[-1].z_hi - stack[0].z_lo) if stack else 0.0
    u = clamp(cov * relief_mm / FULL_COVER_MM)
    return mix(tuple(base_srgb), present, u)


def color_for_pixel(rgb, order, stack, mode="coloraware",
                    brightness_weight=1.0, invert=False, gamma=1.0,
                    base_srgb=(1.0, 1.0, 1.0), row=0, col=0,
                    palette=None, weight_levels=0.0, merge_below=0.0,
                    dither_size=DEFAULT_DITHER_SIZE, mixer=None,
                    white_snap=1.01, dither_pattern=None):
    """像素颜色 → 它打印出来的呈现色（sRGB）。预览图就是逐像素调它。"""
    if mode == "region":
        return region_present_color(
            rgb, order, stack, brightness_weight=brightness_weight,
            invert=invert, gamma=gamma, base_srgb=base_srgb, row=row, col=col,
            palette=palette, weight_levels=weight_levels,
            merge_below=merge_below, dither_size=dither_size, mixer=mixer,
            white_snap=white_snap, dither_pattern=dither_pattern)
    h = map_height(rgb, order, mode=mode, brightness_weight=brightness_weight,
                   invert=invert, gamma=gamma)
    z = h_to_z(h, None, stack)
    return color_at_height(z, stack, base_srgb)


def layer_swatch_colors(stack, base_srgb=(1.0, 1.0, 1.0)):
    """每条色带中点处的呈现色，供 UI 色板显示。"""
    out = []
    for layer in stack:
        z = layer.z_hi - layer.thickness * 0.5
        out.append(color_at_height(z, stack, base_srgb))
    return out


def linear_to_srgb_tuple(rgb):
    from ..constants import linear_to_srgb
    return tuple(clamp(linear_to_srgb(clamp(v))) for v in rgb)


# ---------------------------------------------------------------------------
# 自动配色分析：根据图片推荐"用哪几种颜色"
# ---------------------------------------------------------------------------
def _color_shares(grid, sample_limit=6000):
    """各色的朴素用量占比（把像素按 CMYW 分解后统计），仅用于 UI 展示。

    【免责】它只是"看起来各色占多少"，**不参与选色决策** —— 选色走的是色轮
    覆盖（见 choose_palette_keys），因为能不能还原某个色相取决于"该色轮段两端
    的颜色是否都参与"，而不是用量多少。
    """
    flat = []
    for row in grid:
        flat.extend(row)
    if not flat:
        return {"W": 0.25, "C": 0.25, "M": 0.25, "Y": 0.25}, (0.5, 0.5, 0.5)
    step = max(1, len(flat) // sample_limit)
    samples = flat[::step]
    acc = {"W": 0.0, "C": 0.0, "M": 0.0, "Y": 0.0}
    sr = sg = sb = 0.0
    for px in samples:
        sr += px[0]
        sg += px[1]
        sb += px[2]
        w, c, m, y = rgb_to_cmyw(px)
        acc["W"] += w
        acc["C"] += c
        acc["M"] += m
        acc["Y"] += y
    n = float(len(samples))
    grand = sum(acc.values()) or 1.0
    return ({k: v / grand for k, v in acc.items()}, (sr / n, sg / n, sb / n))


def analyze_palette(grid, max_colors=None, allowed=None, sample_limit=6000):
    """统计图片颜色，推荐一套**色轮覆盖最好**的参与色。

    返回 dict：``avg``（平均色）、``shares``（各色朴素占比，仅供展示）、
    ``keys``（推荐参与配色的颜色键）、``coverage``、``hist``、``note``。

    与 v1.1 的区别：不再输出"色序"。用户已确认颜色没有层序要求，所以只有
    "用哪几种颜色"是有意义的决策。
    """
    shares, avg = _color_shares(grid, sample_limit=sample_limit)
    keys = choose_palette_keys(grid, max_colors=max_colors, allowed=allowed)
    st = hue_stats(grid, sat_threshold=SAT_THRESHOLD, sample_limit=sample_limit)
    cov = _cmy_coverage([k for k in keys if k in _HUE_KEYS], st["hist"],
                        len(st["hist"])) * 100.0
    names = " + ".join(BASE_COLORS[k]["cn"] for k in keys)
    note = ("选用：%s（共 %d 种）。颜色没有层序要求 —— 机器可以在任意高度换色，"
            "所以只需决定「用哪几种颜色」，色相覆盖由这几种颜色的组合决定。"
            % (names, len(keys)))
    if max_colors is not None and max_colors < 4:
        note += "当前等级限制同层用色 ≤ %d 种，色相覆盖会相应下降。" % max_colors
    return {"avg": avg, "keys": keys, "shares": shares, "coverage": cov,
            "hist": st["hist"], "gray_share": st["gray_share"], "note": note}


def suggest_default_stack(grid, max_colors=None, allowed=None):
    """在 analyze_palette 基础上，对"近似黑白"的图片给出更合适的建议。"""
    info = analyze_palette(grid, max_colors=max_colors, allowed=allowed)
    t = info.get("shares", {})
    chroma = max(t.get("C", 0.0), t.get("M", 0.0), t.get("Y", 0.0))
    if chroma < 0.02:
        info["keys"] = ["W"]
        info["note"] = ("图片接近黑白：建议只用白色，靠明暗（高度）还原层次，"
                        "并把「配色与高度方式」切换到「纯亮度」。")
    return info



# ---------------------------------------------------------------------------
# 色深叠色卡（card，v1.4）—— 参考 MakerWorld「151 宝可梦叠色卡」方案
# ---------------------------------------------------------------------------
# 【参考方案解码结论】该 3MF（BambuStudio 2.7 切片）的结构是：
#   · 每个图案 = 上下两块 4×0.1mm 色片（双面显色，总厚 0.8mm）；
#   · 每个颜色是一块「棱柱 mask」，同一 XY 上不同颜色占据不同深度的子层，
#     混色靠**透光叠加**（作者注明：白色要高透度、禁用哑光耗材）；
#   · 全程无抖动 —— 色块边缘是矢量级清晰。
# 【本实现的混色模型】v1.20.0 起单面路径用**反射叠色模型**（见
#   ``stack_present`` 与 _STACK_REFLECT_*）：白纸托底反射观看，白底项
#   双程 ∏t² + 各层背散射单程 —— 理想 C/M/Y 滤片下 M×Y=红、C×Y=绿、
#   C×M=蓝、C×M×Y=黑的定性机制不变，这正是「红黄蓝白」四色能叠出
#   紫/绿/深色的原因 —— 凸包投影（同层混色）到不了的深色区，纵向叠层可达。
# 【标定史】v1.7.0 单程透射积（_STACK_T_LIN，同数据 ΔE≈73→≈7）；
#   v1.20.0 反射模型重标定（_STACK_REFLECT_*，35 色 dE 中位 3.2 vs 旧 17.3）；
#   v1.21.0 双面镜像卡（stack_present_mirror）也切换到 _STACK_REFLECT_* 的
#   8 层回文推广 —— _STACK_T_LIN/_STACK_LAM* 全部退役为历史参考。
# ---------------------------------------------------------------------------

_STACK_QUANT = 63.0  # 堆叠搜索的输入分辨率（与 Mixer.QUANT 同口径）


# ---------------------------------------------------------------------------
# 【v1.7.0 实测标定】叠色呈现模型（2026-09-19，Palette_256 标定板）
# 标定物：MakerWorld「4-color 4-layer color overlay test plate」——4 色 4 层
# 全部有序叠层（4^4=256 格），0.1mm/层 + 白底。用户实机打印后拍照，
# 3MF 逐格解码出每格精确叠层，与照片实测色做对数线性回归（tests/calib/）。
# 两个结论写进模型：
#   1. 每层 0.1mm 耗材的有效透射率（线性光空间，规范: 白=恒等滤光片）：
#        C(天蓝) (0.1711, 0.5226, 0.4505)   M(品红) (0.6093, 0.1384, 0.3094)
#        Y(黄)   (0.7978, 0.6842, 0.2493)   W(白)   恒等
#      （旧「sRGB 直作透射率」假设严重偏暗：同数据 ΔE≈73 → 标定后 ≈7）
#   2. 层序影响颜色：顶层权重 1，向内每层 ×λ（标定 λ=0.50）。
#      顶层主导（表面散射+覆盖），深层贡献衰减。纯乘积模型(λ=1) 整卡
#      ΔE 中位 15.1 → 深度加权后 6.8。
# 局限：品红重系列余饱和度不足（疑似罗丹明荧光增益，被动滤光模型表达不了），
# 标定基于手机拍摄（自动白平衡/色调映射未受控），绝对精度 ΔE≈10（均值）。
# ---------------------------------------------------------------------------
_STACK_LAM = 0.5          # 单面卡（白底板上方 4 层）实测深度衰减
_STACK_LAM_MIRROR = 1.0   # 镜像卡旧深度权重（v1.21.0 起 stack_present_mirror
                          # 改用 _STACK_REFLECT_* 反射推广，本值仅存档）
# 【v1.20.0】单面路径（stack_present）已改用 _STACK_REFLECT_* 反射模型；
# 【v1.21.0】双面镜像（stack_present_mirror）同样切换；本表与 λ 均为历史
# 标定参考，代码内已无调用方（_stack_filter_lin 保留供标定对照）。
_STACK_T_LIN = {
    "C": (0.1711, 0.5226, 0.4505),
    "M": (0.6093, 0.1384, 0.3094),
    "Y": (0.7978, 0.6842, 0.2493),
    "W": (1.0, 1.0, 1.0),
}

# ---------------------------------------------------------------------------
# 【v1.20.0 反射叠色模型】（2026-10-04，Palette_256 全 256 格实拍重标定）
# 观看口径经用户确认为「桌面反射、白纸托底」→ v1.7.0 的单程透射积模型
# （λ^d 深度加权）与实物呈色系统性不符（光照归一化 35 物理色均值表上
# dE 中位 17.3，R23b 黑底拟合同表 23.8），单面路径改用反射模型：
#
#     I = b·(∏_全部层 t)²  +  Σ_k s_{c_k}·(∏_{上方层} t)
#
#   · 白底项：环境光穿透全部色膜 → 白纸漫反射（b，灰底约束 = 逐通道同值）
#     → 再穿回全部膜（双程 ∏t²）。
#   · 散射项：第 k 层膜体内的背散射，经其上方各膜衰减（单程）后返回观察者。
# 层序仍敏感：顶层散射不经衰减，深层散射被上方膜压暗（与色板实测的
# 「层序真实影响呈色」一致）。
# 标定数据：tests/out/_calib_r23b/（Palette_256 3MF 逐格解码 + 实拍照片，
# 光照归一化）。M2 灰底拟合：35 色 dE 中位 3.2 / p90 5.0 / max 6.6；
# 256 格逐格 dE 中位 5.6。b 触上界 1.2 —— 白纸接近全反射，观测色已按
# 组均值归一化，b 只是有效白点增益。薄堆叠 sanity（无实测格，方向性记录）：
# 单层 Y=(235,196,149)、M=(241,158,211)、C=(153,241,247)，符合白纸上的
# 单层薄膜观感；旧单程模型给 (94,83,0)（暗橄榄）、彩底变体给浅绿，均不物理。
# ---------------------------------------------------------------------------
_STACK_REFLECT_B = 1.2     # 白纸反射增益（有效白点，标定触上界）
_STACK_REFLECT_T = {       # 单层透射率（线性光域，0.1mm/层）
    "C": (0.450, 0.825, 0.859),
    "M": (0.809, 0.525, 0.715),
    "Y": (0.790, 0.585, 0.500),
    "W": (0.809, 0.724, 0.875),
}
_STACK_REFLECT_S = {       # 单层背散射系数（线性光域）
    "C": (0.075, 0.061, 0.045),
    "M": (0.093, 0.012, 0.038),
    "Y": (0.084, 0.141, 0.000),
    "W": (0.143, 0.231, 0.098),
}
_STACK_REFLECT_DEPTH = 4   # 标定域深度：Palette_256 每格恒 4 层 × 0.1mm

# ---------------------------------------------------------------------------
# 【v1.0.4】CMYK 四卷料的**实拍反解参数**（35 格标定板 → 最小二乘）
# ---------------------------------------------------------------------------
# 根因（用户 2026-11-13 反馈「叠色出来完全不贴近照片」）：
#   `_STACK_REFLECT_T/S` 来自 Palette_256 官方叠色卡（通用水性墨水），与用户
#   手上这四卷 PLA 不是同一种料。用镜像模型逐格复算 35 格实拍锚点：
#   **平均 ΔE 46.25、中位 46.01、最大 68.78，平均 L* 偏暗 19.4** ——
#   不是「略灰」，是整套参数都不对。选色器按错的呈色挑叠法，实物当然不像。
#
# 做法：模型形式不变（8 层回文 + 背景项），只用实拍值反解 4 色 × (s,t) + B
#   （25 个未知、105 个观测），scipy least_squares（trf，带 s+t≤1 惩罚）。
#   拟合后 35 格 **平均 ΔE 3.71 / 中位 3.24 / 最大 9.28**，残差 RMS 6.42/255。
#   → 模型形式够用，错的只是参数。这是「换标定数据」，不是「编系数」。
#
# 生效范围（关键）：
#   · 镜像链路**不受影响** —— `stack_present_mirror` 优先查 `_CMYK_MEASURED_SRGB`
#     （≤4 层全覆盖），逐位不变，已验证的东西不许动。
#   · 单面链路（`stack_present`，非镜像）与 >4 层的镜像兜底走这套参数。
#   · 只在 CMYK 色组（含 C 和 M、不含 R/B/G/K）生效；RYBW 有自己的表。
#
# 关于 W 的 s=0、t≈1：不是拟合跑飞了。标定板里「C」那一格实际是
#   CWWW|WWWC（8 层回文），8 层里 6 层是白，实测仍是高饱和青 —— 说明 0.1mm
#   的白色薄层在光学上近乎透光，白色反射由背景项 B 承担，不由薄白膜承担。
#   这也解释了「4 层白不会把颜色冲淡」：白膜挡的是横向串色，不是纵向光路。
# ---------------------------------------------------------------------------
# 【v1.0.8】单面 8 层重新标定（覆盖上面 v1.0.4 的数值；来历保留在下面）
# ---------------------------------------------------------------------------
# 数据：2026-11-15 用户实拍的 6/8 层**单面**合板，CMYK / RYBW 各一块、每块
#   704 格（6 层 224 + 8 层 480），4 张正反重复照片（tests/photos/calib_1115/）。
#   板的结构就是插件 single 的实物结构：0.4mm 白底板 + N×0.1mm 色膜，色块顶在
#   front[-1]（见下方"层序"）。
#
# 三条把旧参数打穿的硬事实：
#   ① **层序一直是反的**。标定板生成脚本按 front[0]→z 最低建几何，而
#      stack_present 把 seq[0] 当最外 —— 同一串序列两边解释相反。实测证据：
#      全青格 CCCCCC 是饱和青，而"1 层白在最外"的 WCCCCC 也是饱和青（不是白），
#      "1 层白在最内"的 CCCCCW 才是浅青。用原序拟合 ΔE 47，反序 12.7，模型一字未改。
#   ② **白色不是透明膜**。旧表 W 的 t≈0.82 让 7 层白后还能看见最内的品红；
#      实测 4 层白就把下面的彩完全压住，8 层全白 = (1.000,1.000,1.000)。
#      W 换成 s≈0.47/t≈0.53 之后，8 层白从 0.861 回到 0.997（旧参数连纯白都打不到）。
#   ③ **b 不该是 3.0**。白底反射率是物理定义（≤1），旧值 3.0 是在补偿"白太透明"。
#      钉成 1.0 后 8 层白才落在 1.0 附近，暗部也不再被虚抬。
#
# 拟合口径（calib/fit_lab.py）：序列反序（外→内）、b=1.0、Lab 域 ΔE² 损失、
#   s=(1−t)·u 保能量守恒、只用 8 层段 480 格（生产 single 恒 8 层），6 层段留作验证。
#   逐格先剔掉 4 张照片里偏 >10ΔE 的那一张（CMYK3 有反光，纯青格实测 0.56/0.90/1.00
#   而其余三张 0.09/0.62/0.85），再取中位。
#   结果 ΔE76：CMYK 480 格 均 4.58 / 中位 4.30 / p90 7.81（旧参数 23.65 / 21.88）；
#              RYBW 480 格 均 5.45 / 中位 4.85 / p90 9.92（旧参数 10.21 / 9.40）。
#   6 层段交叉验证：CMYK 6.85、RYBW 10.62（没有参与拟合）。
#   留一法（8 张轮流留一张）参数极差 ≤0.031，不是拟合到单张照片的噪声上。
#
# ⚠ 与镜像链路的关系：≤4 层镜像照旧走 `_CMYK_MEASURED_SRGB`（35 格实测，逐位不变）；
#   只有单面 `stack_present` 与 >4 层镜像兜底用本表。b 从 3.0 变 1.0 必须与 s/t 同时
#   生效 —— 单独改任何一个都会让 8 层以上的外推整片发灰。
_CMYK_FIT_B = 1.0000
_CMYK_FIT_T = {
    "C": (0.1634, 0.4727, 0.5230),
    "M": (0.7500, 0.1346, 0.3685),
    "Y": (0.6768, 0.6905, 0.1000),
    # 【v1.0.5】W 从 (1.000, 0.889, 0.887) 改成物理值。原来的值让白膜在 R 通道
    # 100% 透光 ⇒ 白膜永远不反射红、也永远不能把颜色冲淡，8 层叠色的色域于是
    # 塌成一层"高饱和壳"（C*<25 的锚点只有纯白一个），肤色只能被映射成蓝紫。
    #
    # 为什么可以改（不是编系数）：35 格里 W 永远夹在外层彩膜**中间**（CWWW|WWWC），
    # 白层散射出不来 → 这组参数在拟合中**不可辨识**，解到 (0,0.111,0.113) 只是
    # 初值附近的退化解。改成"低散射 0.18 / 高透射 0.82"（能量守恒 s+t=1）后
    # 用生产函数复算 35 格：平均 ΔE 5.06→4.84、中位 4.63 不变、**最大
    # 16.78→10.42**（WHITE_MAX_CHROMA 的门禁与黑白锚点都不动）；8 层全白
    # 仍是 (250,250,250)，肉眼与耗材白无异。
    "W": (0.5300, 0.4417, 0.3045),
}
_CMYK_FIT_S = {
    "C": (0.0503, 0.2594, 0.4404),
    "M": (0.1613, 0.0378, 0.1550),
    "Y": (0.3232, 0.2779, 0.0809),
    # 见上：s = 1 − t，白膜 = 82% 透过 + 18% 漫反射（不吸收）。
    # 为什么是 0.18 而不是更强的 0.40：在用户这张照片上扫过 5 档（0.12~0.40），
    # 整图平均 ΔE 14.87/14.71/14.73/14.84 几乎持平，但 0.40 会把龙身那种深绿
    # 一起冲淡（视觉上"整体泛白"）。0.18 取"够稀释、不过曝"：脸部 ΔE
    # 38.4→28.0，整图中位 ΔE 15.96→10.12（−37%），龙身不再被冲淡。
    "W": (0.4700, 0.5583, 0.6955),
}


def _is_cmyk_group(palette):
    """当前色板是不是 CMYK 四卷（含 C、M，且不含 R/B/G/K）。

    与 :func:`cmyk_measured_present` 同判据：只含 {Y,W} 的窄色板既可能来自
    CMYK 也可能来自 RYBW，必须要求 C 与 M **同时**出现才认。
    """
    if palette is None:
        return False
    ks = set(palette.keys())
    return {"C", "M"} <= ks and not (ks & frozenset("RBGK"))


# 无实拍标定的色键 → 颜料式**宽谱**透射率（v1.29.0 / R36）。
# 【为什么不能直接拿 sRGB 当透射率】纯红的 sRGB 是 (1,0,0)，G/B 通道为 0，
# 乘上任何黄都还是纯红 —— R+Y 出不了橙、Y+B 直接变黑，"RYB 视觉叠色"就
# 不存在了。真实颜料是宽谱的：红颜料也透一点蓝、蓝颜料透相当多绿（这正是
# 黄+蓝能出绿的原因）。下表按经典 RYB 混色结果反推（镜像 8 层模型实测）：
#   R+Y → 45° 橙 ｜ Y+B → 137° 绿 ｜ R+B → 紫（偏暗）｜ 三者叠加 → 橄榄/近黑
# ⚠ 这是**模型近似**，不是实拍标定；只有 CMYW 有 Palette_256 实测数据。
#
# 【v1.41.0 / R49】上面三处 1.00 是**错的**，一律被 _STACK_T_MAX 截住。
# 原因（恒真式，不需要实测）：单层膜压在白纸上 I = b·t² + s，s ≥ 0，而膜不可能
# 比白纸更亮 ⇒ t ≤ 1/√b = 0.9129。写成 1.00 意味着"这层膜对红光 100% 透过"，
# 于是一卷红料叠 4 层、8 层都还是满值红 —— 实测表现为：
#     4 层红 = (255,0,0)、6 层红 = (255,0,0)、8 层红 = (255,0,0)
# 暗部永远压不下去，RYBW 整片发飘、和照片对不上。封顶后同一序列变成
#     1 层 (195,87,88) → 4 层 (144,0,0) → 8 层不再反弹
_PIGMENT_T = {
    "R": (1.00, 0.18, 0.32),
    "B": (0.12, 0.55, 1.00),
    "G": (0.15, 1.00, 0.35),
    "K": (0.05, 0.05, 0.05),
}

# ===========================================================================
# RYBW 实拍标定参数（v1.43.0 / R53，重拍版）—— 来自用户打印的 8 格标定板反解。
# 标定板：板 A 红/黄/白片 ×(黑底/白底 × 1/2 层)，板 B 蓝 ×(黑底/白底 × 1/2/3 层)，
#   每行另有一个裸露参考格测该行的底色反射率。
# 反解：I_n(底) = b_底·t^(2n) + s·(1+t+...+t^(n-1))（纯 n 层线性光域，逐通道独立）。
#   三张照片分别归一化到 b=1.2 后**联合**最小二乘（12/18/6 个残差解 6 个未知数），
#   不逐图单独解再平均 —— 单图 2 个观测是"恰好确定"，会把噪声放大成 t 乱跳。
#   b_底 用该行自己的参考格（A 板实测逐行黑底差 1.2~1.8 倍）。
#   **白底权重 1、黑底权重 0.5** —— 白底才是插件真正渲染的域（色膜叠在白底上）；
#   黑底只是辅助观测，A 板黑条有反光。扫描证实：黑底权重从 1.0 降到 0.5，白底
#   残差 R 20.7→14.9 / Y 16.0→11.4 / B 9.5→6.7，代价是黑底各升 3~6。
#
# 物理合理性：R 透红(0.87)吸绿蓝(0.05/0.02)；Y 透红绿(0.84/0.77)吸蓝(0.23)；
#   B 吸红(0.08)透绿蓝(0.60/0.84)；W 散射(0.75~0.85)大于透射(0.19~0.30)。
# 与 v1.42 的差别：v1.42 数据里"强吸收通道"都被反光抬了个底（R 绿 0.21、
#   Y 蓝 0.17、B 红 0.33），本版明显压低（R 绿 0.053、B 红 0.083）。
#   打印板 sRGB 残差（rms），**按底色分域**（合成一个数会误判）：
#     白底（渲染域）R 60.6→14.9、B 41.4→6.8、Y 13.5→11.4
#     黑底（辅助）  R 39.5→22.5、B 25.3→17.1、W 12.7→8.1、Y 16.8→19.6
#   注意旧表白底 R 高达 60.6、黑底却只有 39.5 —— 旧表其实"只对了黑底"。
#
# ⚠ 已知边界：黑底观测与白底观测不能完全自洽（A 板黑条有反光，黄的黑底蓝通道
#   比模型高约 6 倍）→ Y 的蓝通道只能折中到 0.227（白底要求更高、黑底要求更低），
#   s 靠黑底约束、精度有限；B 板 3 层留出误差 17~29 sRGB。

# 【为什么独立表而非改写 _STACK_REFLECT_T】Y/W 在 _STACK_REFLECT_T 里是 CMYK
#   （黄墨/白墨）的联合标定，已被 R48 白点中性化依赖；RYBW 用黄 PLA/白 PLA
#   耗材，数值不同。共用会破坏已验证的 CMYK。
_RYBW_KEYS = frozenset("RYBW")
# 【v1.0.8】同一批单面 8 层实拍重拟合（见 _CMYK_FIT_B 上方的完整来历）。
#   旧表的 (s,t) 由"红/黄/蓝 × 黑底/白底 × 1~3 层"的 8 格小板反解，深度不够，
#   8 层外推会把红压成暗砖（L* 21）、把蓝压成墨蓝。新版直接用 480 个 8 层格：
#     旧 ΔE 均 10.21 / 中位 9.40 → 新 5.45 / 4.85（p90 19.5 → 9.9）。
#   物理合理性仍成立：R 透红 0.80、吸绿蓝 0.02/0.00；Y 透红绿 0.67、吸蓝 0.11；
#   B 吸红 0.14、透绿蓝 0.58/0.62；W 散射 (0.53,0.69,0.81) 大于透射。
_RYBW_T = {
    #       R       G       B
    "R": (0.8020, 0.0217, 0.0019),
    "Y": (0.6684, 0.6598, 0.1120),
    "W": (0.4748, 0.3098, 0.1744),
    "B": (0.1402, 0.5821, 0.6205),
}
_RYBW_S = {   # b=1.0 尺度（与 _CMYK_FIT_B 同口径；旧版是 b=1.2）
    "R": (0.1433, 0.0447, 0.0584),
    "Y": (0.3316, 0.3091, 0.1165),
    "W": (0.5252, 0.6902, 0.8134),
    "B": (0.0220, 0.1903, 0.3795),
}
# RYBW 的白底反射系数。与 _STACK_REFLECT_B(1.2) 分开：那一套是 mosaic / 未标定色组
# 的通用值，改它会牵动所有非 CMYK/RYBW 链路；RYBW 的新 s/t 是 b=1.0 尺度拟合的。
_RYBW_B = 1.0

# 单层透射率上界（见上）。标定表 C/M/Y/W 的 12 个通道全在 0.45~0.875，
# 本来就不越界，所以 CMYK/CMYWGK(部分)/8色 的既有输出逐位不变。
_STACK_T_MAX = 1.0 / math.sqrt(_STACK_REFLECT_B)


def _is_rybw_group(palette):
    """当前色板是不是 RYBW 四卷（键集 ⊆ RYBW，且至少 3 个键）。

    与 :func:`_reflect_st` 里选表那段的判据**必须一致**：白底反射系数与 (s,t) 是
    同一次拟合的两个部分，判据分叉就会出现"用 RYBW 的 s/t + 通用 b"的错配。
    """
    return (palette is not None and len(palette) >= 3
            and set(palette.keys()) <= _RYBW_KEYS)


def _stack_bg(palette):
    """堆叠模型里的白底反射系数 b（逐色组）。

    CMYK / RYBW 各用自己那套（都是 b=1.0 尺度的单面 8 层实拍拟合）；
    其余色组沿用通用的 ``_STACK_REFLECT_B``（Palette_256 标定，1.2）。

    v1.0.9：「按理论计算」时白就是白纸，b = 1.0（不引入任何实测增益）。
    """
    if _is_theory(palette):
        return 1.0
    if _is_cmyk_group(palette):
        return _CMYK_FIT_B
    if _is_rybw_group(palette):
        return _RYBW_B
    return _STACK_REFLECT_B


def _t_clamp(t):
    """把单层透射率收进物理可行域：0 ≤ t ≤ 1/√b（v1.41.0）。"""
    return tuple(min(_STACK_T_MAX, max(0.0, v)) for v in t)


def _srgb_to_lin_c(v):
    """标量 sRGB -> linear（叠色模型专用；数组版转换归 image_analysis）。"""
    v = min(1.0, max(0.0, float(v)))
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _lin_to_srgb_c(v):
    """标量 linear -> sRGB。"""
    v = max(0.0, float(v))
    return v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1.0 / 2.4) - 0.055


def _srgb_to_lab(rgb):
    """标量 sRGB(0..1) → CIELAB（D65）。与 ``quantize.srgb_to_lab`` 同一组常数。

    【为什么投影要在 Lab 里算】投影问的是"哪个色板点**看起来**最接近目标色"。
    sRGB 是设备空间，欧氏距离与人眼感知不成比例：同一个数值差，落在亮部几乎
    看不出、落在暗部却是天壤之别。实测把判据从 sRGB 欧氏换成 CIE76 之后，
    同一张照片的块级呈现色差（中位/均值/暗部）在两种图型上都变好，暗部尤其
    明显 —— 因为 sRGB 距离会认为"深灰"离某个中亮度彩色比离近黑更近。
    """
    lin = tuple(_srgb_to_lin_c(c) for c in rgb)
    x = (0.4124564 * lin[0] + 0.3575761 * lin[1] + 0.1804375 * lin[2]) / 0.95047
    y = (0.2126729 * lin[0] + 0.7151522 * lin[1] + 0.0721750 * lin[2])
    z = (0.0193339 * lin[0] + 0.1191920 * lin[1] + 0.9503041 * lin[2]) / 1.08883
    d = 6.0 / 29.0

    def f(t):
        return t ** (1.0 / 3.0) if t > d ** 3 else t / (3.0 * d * d) + 4.0 / 29.0

    fx, fy, fz = f(x), f(y), f(z)
    return (116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))


def _stack_filter_lin(key, palette):
    """单层滤光片透射率（线性空间）。

    优先级: 实测标定表 > palette 覆盖 > 理想色。标定表对标准四键恒生效
    （旧「观感色 sRGB 直作透射率」的语义被 v1.7.0 取代）；palette 只对
    标定表以外的键生效。
    """
    t = _STACK_T_LIN.get(key)
    if t is not None:
        return t
    if palette and key in palette:
        return tuple(_srgb_to_lin_c(c) for c in palette[key])
    base = BASE_COLORS.get(key)
    if base is not None:
        return tuple(_srgb_to_lin_c(c) for c in base["srgb"])
    return (1.0, 1.0, 1.0)


def palette_basis(palette):
    """色板的计算依据：``"calibrated"``（默认，走实测表）或 ``"theory"``。"""
    return getattr(palette, "basis", DEFAULT_COLOR_BASIS)


def _is_theory(palette):
    return palette_basis(palette) == "theory"


def clone_palette(palette):
    """重建色板（统一成 float 元组）时**保留计算依据**。

    锚点枚举里有三处 ``dict(...)`` 重建色板；直接 dict 会把 ``basis``
    丢掉、静默退回实测表 —— 那正是"选了理论却还是按色板算"的根因。
    """
    if palette is None:
        return None
    return StackPalette(((k, tuple(float(c) for c in palette[k]))
                         for k in palette), basis=palette_basis(palette))


def _given_lin(key, palette):
    """色组给定的这卷料颜色（线性域）—— theory 路径唯一的数据来源。"""
    if palette and key in palette:
        return tuple(_srgb_to_lin_c(c) for c in palette[key])
    base = BASE_COLORS.get(key)
    if base is not None:
        return tuple(_srgb_to_lin_c(c) for c in base["srgb"])
    return (1.0, 1.0, 1.0)


# 理想白颜料的单层参数：**纯散射、无吸收** ⇒ s = t = 0.5（反射一半、透过一半）。
# 这不是拍脑袋的数：实拍反解出来的这批白料是 s=0.47 / t=0.53，几乎就是 0.5/0.5。
_THEORY_WHITE_ST = 0.5
# 判"这卷料是白"的备用判据：三通道线性值都 ≥ 它也算白。**主判据是键名 "W"**
# （四档色组的白键都叫 W）；这条只兜住"某个色组把白改叫别的键"的情形。
_THEORY_WHITE_MIN = 0.97


def _theory_st(key, palette):
    """理论模型的单层参数：颜色即"这层膜在白纸上的样子"。

    **彩色料**：``t = lin(c)``、``s = lin(c) − t²``（b = 1）⇒ 单层贴白纸的
    呈现色**恰好等于**色组给的那个颜色；叠起来就是透光率逐层相乘（理想减色）：
    C+M = 蓝、C+Y = 绿、M+Y = 红、C+M+Y = 近黑。

    **白色料（v1.0.11 修正）**：颜色 (1,1,1) 里**没有密度信息** —— 早先按
    ``t = lin(c) = 1`` 算，等于把白当成了"透明的纸"，于是「白+黄」和「黄」
    一模一样，6/8 层的 224/480 个叠法会塌成 8 个纯色。白颜料是**散射体**，
    物理上 s + t = 1（不吸收），取 s = t = 0.5 后：
    白盖黄 → 浅黄、白盖青 → 浅青、白盖黑 → 灰；叠 8 层白 → 0.996（仍是白）。

    与实测表的唯一区别：这里没有任何「这批料实际偏多少」的修正 —— 理想色
    只吃色组给的那三个数，色值一改结果立刻跟着变。
    """
    lin = _given_lin(key, palette)
    if key == "W" or min(lin) >= _THEORY_WHITE_MIN:
        _w = _THEORY_WHITE_ST
        return (_w, _w, _w), (_w, _w, _w)
    # v1.0.12：**R/B（以及 G/K）不能直接拿 sRGB 当透射率**。
    # 纯红的 (1,0,0) 绿/蓝通道是 0，乘上任何黄还是纯红；纯蓝 (0,0,1) 的红/绿
    # 是 0，黄+蓝 三个通道全灭 = 纯黑 —— RYBW 理论档因此永远出不了绿
    # （用户 2026-11-17：RYBW 的龙没有绿色，CMYK 有）。这不是新发现：
    # R36 当年在实测档就踩过同一个坑，结论写在 _PIGMENT_T 的注释里
    # （"真实颜料是宽谱的：红也透一点蓝、蓝透相当多绿"）。
    # 这里把同一条物理约定接到理论档：t 取 _PIGMENT_T 的宽谱透射率，
    # s 仍从**色组给定的观感色**反推（s = lin(c) − t²），所以单层贴白纸
    # 仍≈那个颜色 —— 只有给定值为 0 的通道会被 t² 抬起来（蓝的绿通道
    # 0 → 0.30），这正是"蓝料漏绿"的物理代价。C/M/Y 仍走理想的 t = lin(c)。
    t_pig = _PIGMENT_T.get(key)
    if t_pig is not None:
        t = tuple(_t_clamp(t_pig))
        s = tuple(max(0.0, lin[i] - t[i] * t[i]) for i in range(3))
        return s, t
    t = tuple(min(1.0, max(0.0, v)) for v in lin)
    s = tuple(max(0.0, lin[i] - t[i] * t[i]) for i in range(3))
    return s, t


def _reflect_st(key, palette):
    """反射模型的单层参数 ``(s, t)``（线性光域）。

    优先级：实拍标定表（C/M/Y/W）> 颜料式宽谱（R/B/G/K）> sRGB 观感色近似。
    标定表以外的键用 ``s = L − b·t²`` 让"白纸上的单层膜 ≈ 观感色"，
    这样单张膜看起来仍是那卷料的颜色，叠起来才按透射率乘积混色。

    v1.41.0：返回值统一经 :func:`_t_clamp` 收进 ``t ≤ 1/√b`` 的物理可行域。
    """
    # v1.0.9：用户选「按理论计算」时，**整条实测表链路都不走**。放在最前面
    # 是有意的：下面的实测分支互相之间有优先级，漏掉任一分支都会让"理论"
    # 变成"半实测"。
    if _is_theory(palette):
        return _theory_st(key, palette)
    # v1.0.4：CMYK 四卷的实拍反解参数（见 _CMYK_FIT_* 的注释）。放在最前面
    # 是有意的 —— 它和 RYBW 分支互斥（RYBW 不含 C/M），谁先都一样，但这条
    # 是"用户手上这批料"的唯一真值来源，优先级应当最高。
    # 注意**不做 _t_clamp**：拟合出的 t 可以超过 1/√b（b 已从 1.2 变成 2.62），
    # clamp 会把亮部压暗、把 ΔE 3.71 打回 20+。
    if key in _CMYK_FIT_T and _is_cmyk_group(palette):
        return _CMYK_FIT_S[key], _CMYK_FIT_T[key]
    # RYBW 实拍标定（v1.42.0 / R52）：RYBW 色组走专属表，避免与 CMYK 共享 Y/W 冲突。
    # 判定：传入的 palette 键集是 RYBW 子集（即当前正在算 RYBW 叠色）。
    #
    # ⚠ v1.44.0 / R55：**至少要 3 个键**才算 RYBW 组。CMYK 与 RYBW 的交集只有
    # {Y, W}，所以"被裁窄的 CMYK 色板"最多剩 2 个键 —— 一个 {"Y"} 色板既可能是
    # RYBW 组也可能来自 CMYK 组，单靠子集判定会把 CMYK 的白膜算成 RYBW 的强散射白
    # （r38 C3/C5：5504 个面全变成纯白）。生产链路现在不再裁窄色板，这条只是护栏。
    if key in _RYBW_T and _is_rybw_group(palette):
        return _RYBW_S[key], _t_clamp(_RYBW_T[key])
    t = _STACK_REFLECT_T.get(key)
    if t is not None:
        return _STACK_REFLECT_S[key], _t_clamp(t)
    # 颜料式宽谱：直接拿 sRGB 当透射率会把 R 的 G/B 置 0（红+黄=纯红），
    # RYB 的减法混色就出不来。宽谱 + 观感色回填散射，两个目标同时满足。
    t = _PIGMENT_T.get(key)
    if t is not None:
        if palette and key in palette:
            lin = tuple(_srgb_to_lin_c(c) for c in palette[key])
        else:
            base = BASE_COLORS.get(key)
            lin = (tuple(_srgb_to_lin_c(c) for c in base["srgb"])
                   if base is not None else (1.0, 1.0, 1.0))
        t = _t_clamp(t)
        s_ = tuple(max(0.0, lin[i] - _STACK_REFLECT_B * t[i] * t[i])
                   for i in range(3))
        return s_, t
    if palette and key in palette:
        lin = tuple(_srgb_to_lin_c(c) for c in palette[key])
    else:
        base = BASE_COLORS.get(key)
        lin = (tuple(_srgb_to_lin_c(c) for c in base["srgb"])
               if base is not None else (1.0, 1.0, 1.0))
    t = _t_clamp(tuple(min(0.999, v) for v in lin))
    s = tuple(max(0.0, lin[i] - _STACK_REFLECT_B * t[i] * t[i])
              for i in range(3))
    return s, t


def stack_present(stack_keys, palette=None, gain=None, below=()):
    """有序颜色堆叠（**外表面往里**）→ 白纸托底反射观看的呈现色（sRGB）。

    v1.20.0 反射模型：``I = b·(∏t)² + Σ s_k·∏_{上方}t``（逐通道，线性光域，
    Palette_256 256 格实拍标定，35 色 dE 中位 3.2 —— 旧单程模型 17.3）。

    【不足 4 层内垫白】堆叠先内垫白到 ``_STACK_REFLECT_DEPTH``（4）层再计算。
    两个理由：① 与打印几何严格一致——``expand_mirror_stack`` / 卡片子层
    恒垫白到 bands，垫出来的物理结构就是「色膜 + 白膜 + 白纸」；② 恰好落回
    Palette_256 的标定域——含白格（WWCY 之类）全部被 256 格实测覆盖，
    不依赖任何外推。空堆叠 = 四层全白 = 色板 WWWW 格。

    ``below``（v1.0.2）：夹在彩膜**更内层**的固定层（单面叠色卡的 4 层白隔离
    垫）。它们不是恒等滤光片 —— 白膜的 t<1、s>0，既挡一部分底板反射、又把
    自己的散射加进来，所以必须进模型真算，不能当成"透明的空气"跳过。
    默认空元组 → 全部旧调用逐位不变。
    """
    st = list(stack_keys) + list(below)
    # v1.0.11：**理论档不垫白**。实测档的"垫白"代表「色膜 + 白膜 + 白纸」
    # 的实物结构，垫的 W 有 s>0、t<1，必须真算；理论档的托底是**理想白纸**
    # （b=1，已经由 ``_bg·q²`` 项表达），再垫 4 层白膜等于凭空多加四次散射，
    # 「单层贴白 = 这个颜色」就不成立了。空堆叠在理论档 = 纯纸 = 纯白。
    if not _is_theory(palette):
        while len(st) < _STACK_REFLECT_DEPTH:
            st.append("W")
    acc = [0.0, 0.0, 0.0]
    q = [1.0, 1.0, 1.0]           # 逐通道 ∏_{上方} t，循环结束时 = ∏_全部 t
    _bg = _stack_bg(palette)
    for k in st:                   # 顶层先
        s, t = _reflect_st(k, palette)
        for ch in range(3):
            acc[ch] += s[ch] * q[ch]
            q[ch] *= t[ch]
    for ch in range(3):
        acc[ch] += _bg * q[ch] * q[ch]
    if gain:      # v1.40.0 白点增益：在线性域乘，省掉逐像素的 sRGB↔线性往返
        acc = [acc[ch] * gain[ch] for ch in range(3)]
    return tuple(min(1.0, _lin_to_srgb_c(v)) for v in acc)


# ---------------------------------------------------------------------------
# 【v1.46.0 / R62】CMYK 四卷料的**实拍锚点表**（35 个镜像多集）
# ---------------------------------------------------------------------------
# 来源：桌面 `CMYK标定板/`，6x6 = 1 个纯白参考 + 35 个叠法；每格几何与生产
# 完全一致（0.6mm 柱体 + 8x0.1mm 色膜）。照片正俯视，按白参考做线性白平衡
# （von Kries），四张不同角度照片交叉核验：与主表最近邻 dRGB p50 ~= 0.06。
#
# 【为什么直接替换锚点色，而不是反解 (s,t)】镜像链路只有这 35 个锚点，量全了
# 就够；而且实测值把柱体颜色、白底反射、耗材批次差异一起含进去了。旧模型
# 用 Palette_256 官方叠色卡的数据，实测 35 格平均偏差 dRGB 0.539：亮度低估
# +0.212、饱和度低估 +0.351 —— 这就是 CMYK 印出来发灰发白的根因。
#
# 只在 CMYK 色组生效；RYBW 有专属表，CMYWGK/MAX8 未标定，绝不混用。
# 表里的键是 canonical 序的叠法字符串（CM 而不是 MC）。
_CMYK_MEASURED_SRGB = {
    "W": (0.9952, 0.9957, 0.9881),
    "C": (0.2363, 0.8394, 1.0000),
    "M": (1.0000, 0.3575, 0.8572),
    "Y": (1.0000, 1.0000, 0.4091),
    "CC": (0.0000, 0.7960, 1.0000),
    "CM": (0.5370, 0.6006, 0.9167),
    "CY": (0.3652, 0.8611, 0.7938),
    "MM": (1.0000, 0.1665, 0.7383),
    "MY": (1.0000, 0.4270, 0.5241),
    "YY": (1.0000, 0.9957, 0.3575),
    "CCC": (0.0000, 0.7873, 1.0000),
    "CCM": (0.3317, 0.7048, 0.9960),
    "CCY": (0.1170, 0.8177, 0.9564),
    "CMM": (0.5704, 0.5789, 0.8612),
    "CMY": (0.5513, 0.6223, 0.8136),
    "CYY": (0.4415, 0.8828, 0.8176),
    "MMM": (1.0000, 0.2056, 0.7422),
    "MMY": (1.0000, 0.3054, 0.6470),
    "MYY": (1.0000, 0.4617, 0.5717),
    "YYY": (1.0000, 1.0000, 0.3694),
    "CCCC": (0.0979, 0.8220, 1.0000),
    "CCCM": (0.2745, 0.7873, 1.0000),
    "CCCY": (0.2076, 0.8264, 1.0000),
    "CCMM": (0.3747, 0.7482, 1.0000),
    "CCMY": (0.3699, 0.7699, 1.0000),
    "CCYY": (0.2602, 0.8698, 1.0000),
    "CMMM": (0.5752, 0.6310, 0.9207),
    "CMMY": (0.5943, 0.6484, 0.8969),
    "CMYY": (0.5943, 0.6788, 0.8691),
    "CYYY": (0.4893, 0.9175, 0.8810),
    "MMMM": (1.0000, 0.2881, 0.7581),
    "MMMY": (1.0000, 0.3011, 0.7184),
    "MMYY": (1.0000, 0.3315, 0.6946),
    "MYYY": (1.0000, 0.5008, 0.6114),
    "YYYY": (1.0000, 1.0000, 0.4250),
}
_CMYK_KEYS = frozenset("CMYW")


def cmyk_measured_present(stack_keys, palette=None):
    """CMYK 实拍锚点查表；非 CMYK 色组返回 None（走原反射模型）。

    判定刻意要求 **C 与 M 同时出现**：只含 {Y,W} 的窄色板既可能来自 CMYK
    也可能来自 RYBW（R55 踩过：把白膜算成 RYBW 的强散射白，整片纯白）。
    """
    if palette is None:
        return None
    if _is_theory(palette):
        # 理论模式不查任何实拍锚点表（否则 35 格 CMYK 会整块被实拍值覆盖）。
        return None
    ks = set(palette.keys())
    if not {"C", "M"} <= ks or (ks & frozenset("RBGK")):
        return None
    st = "".join(canonical_key_order(list(stack_keys))) or "W"
    return _CMYK_MEASURED_SRGB.get(st)


def _stack_present_mirror_model(stack_keys, palette=None, gain=None, bands=4,
                                keep_order=False):
    """双面镜像叠色卡的呈现色：正面 ``bands`` 层 + 背面 z 镜像的回文 2×bands 层。

    ``bands`` = 每面层数（v1.0.6 起双面卡默认 6 → 回文 12 层）。旧调用不传时
    恒为 4，与 v1.0.5 逐位一致。

    ``keep_order=True`` 时**不把键重排成色轮序** —— 白色要被允许排在彩膜
    **外侧**（否则它永远垫在内侧，挡不住也冲不淡任何颜色）。几何展开
    （``generation.expand_mirror_stack``）本来就不重排，这里必须同口径，
    否则"预览 / 视口 / 实物"三处会各自算一套。

    【物理依据】参考 3MF（151 宝可梦叠色卡，2026-09 逐列射线解码）：
    每列恒 8 个 0.1mm 子层 = 正面 4 层 + 背面 4 层 z 镜像（回文序列），
    无白底夹心。观看 = 平放桌面反射（与单面标定同口径）。

    v1.21.0 起 = R24 反射模型的 8 层推广（Palette_256 白纸标定参数，
    ``_STACK_REFLECT_*``）::

        I = b·(∏_8 t)² + Σ_{正面 d} s_d·∏_{上方}t
                        + (∏_正面 t)·Σ_{背面 d} s_d·∏_{上方}t

    正面各层散射直达观察者；背面各层散射要穿正面板 4 层；背景项 =
    光穿全部 8 层 → 桌面反射 → 穿回的双程。旧模型（λ=1.0 全层等权
    透射积）在 70 锚点中 32 个 lum<0.25（整体过暗、色相窄），对亮部
    照片最近投影塌白（R22 实测 85.5%）；新模型暗锚点 0、lum 中位
    0.55（_diag_r25_mirror）。

    展开（与 ``generation.expand_mirror_stack`` 严格一致）：色轮序排列、
    不足 4 层补白、正面 + 正面逆序回文。补白位置必须参与计算——
    白膜非恒等（t_W<1、s_W>0），垫白既挡背面贡献也贡献散射。
    """
    _b = max(1, int(bands or 4))
    st = (list(stack_keys) if keep_order
          else list(canonical_key_order(list(stack_keys))))
    # 每面层数上限 = ``_b``（v1.0.6 起双面卡取 C.CARD_BANDS_PER_SIDE = 6）。
    # 旧注释里"第 5 层起零增益"是**不含白**的结论：那时白色永远补在内侧，
    # 加层只等于同色重复得更暗，落在旧凸包里。白进外层后 6 层能补出整片
    # 浅色/低饱和区，这条结论不再成立（见 tests/out/_diag_white_outer.py）。
    front = st[:_b]
    while len(front) < _b:
        front.append("W")
    full = front + front[::-1]
    pf = [1.0, 1.0, 1.0]          # 正面板透射积 ∏_front t
    for k in front:
        _, t = _reflect_st(k, palette)
        for ch in range(3):
            pf[ch] *= t[ch]
    acc = [0.0, 0.0, 0.0]
    q = [1.0, 1.0, 1.0]           # ∏_{上方} t，循环结束 = ∏_全部 8 层 t
    for depth, k in enumerate(full):
        s, t = _reflect_st(k, palette)
        w = pf if depth >= _b else (1.0, 1.0, 1.0)
        for ch in range(3):
            acc[ch] += s[ch] * q[ch] * w[ch]
            q[ch] *= t[ch]
    _bg = _stack_bg(palette)
    for ch in range(3):
        acc[ch] += _bg * q[ch] * q[ch]
    if gain:      # v1.40.0 白点增益：在线性域乘，省掉逐像素的 sRGB↔线性往返
        acc = [acc[ch] * gain[ch] for ch in range(3)]
    out = tuple(min(1.0, _lin_to_srgb_c(v)) for v in acc)
    # 【白点是上限，不是"比白更白"】v1.0.7。
    # 白在外侧放开后，模型会把「白 + 黄 + 红」这类堆法外推到 1.0 以上再截断，
    # 结果**比全白还亮** —— 实测 RYBW 4 层：WWWW 0.9952、WWYR (1.0, 0.9987, 1.0)。
    # 于是纯白像素的最近锚点变成"白白黄河"（多印 2 片彩膜、耗时耗料），
    # 而物理上白底 + 只吸收不增亮的色膜不可能比白更亮。
    # 在白点处封顶：超过实测白点的堆法一律拉回白点，与全白锚点同色 → 去重合并。
    # 只在给了色板（生产路径）时生效，历史探针不传色板的调用逐位不变。
    # v1.0.9：理论模式的白点就是色组给的白（通常是 1,1,1），不借用实拍白点。
    if palette is not None:
        _wp = (tuple(float(c) for c in palette.get("W", (1.0, 1.0, 1.0)))
               if _is_theory(palette) else _CMYK_MEASURED_SRGB["W"])
        if (out[0] > _wp[0] + 1e-9 or out[1] > _wp[1] + 1e-9
                or out[2] > _wp[2] + 1e-9):
            out = tuple(min(float(out[i]), float(_wp[i])) for i in range(3))
    return out


def stack_present_mirror(stack_keys, palette=None, gain=None, bands=4,
                         keep_order=False):
    """镜像叠色呈色 —— 对外唯一入口（v1.46.0 / R62）。

    CMYK 走 **35 格实拍锚点表**，其它色组仍走反射模型。镜像链路的三处
    消费者（mirror_anchors 锚点、generation._stack_linear 顶点色、诊断脚本）
    全部调这里，保证「预览 / 视口 / 实物」是同一套颜色。

    ⚠ 实拍表只在 ``bands == 4`` 时可用：那 35 格是**每面 4 层**的标定板。
    每面 6 层时结构变成回文 12 层，拿 4 层的实测色会系统性偏亮（少算了两层
    白对光路的衰减），必须整条走模型 —— 模型对这 35 格的复算 ΔE 4.66，
    口径一致比"一半实测一半模型"更重要。

    【全白必须特判】模型对"12 层全白"外推出来是 0.899（灰），而实拍白点是
    0.9952。**不能**用"把整体拉亮到白点"来修：拿 35 格实拍板当裁判，全局
    白点校正会把模型平均 ΔE 从 5.48 推到 10.35（``_diag_whitepoint_judge.py``）
    —— 那是把已标定的中间调一起推偏。白点本身是**定义**不是拟合参数
    （"白耗材在标准光下就是白"），所以只把全白/空叠这一个锚点钉到实拍白点，
    其余锚点保持模型原样。
    """
    _st = tuple(k for k in (stack_keys or ()) if k)
    if palette is not None and set(_st) <= {"W"}:
        # 【白点是定义，不是拟合参数】v1.0.7：对所有色组都用实拍白点。
        # 早先只有 CMYK 能查到（cmyk_measured_present 要求色板含 C+M 且不含
        # R/G/B/K），CMYWGK 与 8 色 MAX 于是拿模型外推的 8 层全白 —— 实测
        # (195,212,200) 灰绿、ΔE≈16，所有浅色区整片发绿灰。白耗材在标准
        # 光下就是白，这条与色组无关。
        # v1.0.9：理论模式用色组给定的白（不借用实拍白点）。
        if _is_theory(palette):
            return tuple(float(c) for c in palette.get("W", (1.0, 1.0, 1.0)))
        return _CMYK_MEASURED_SRGB["W"]
    if int(bands or 4) == 4 and not keep_order:
        hit = cmyk_measured_present(stack_keys, palette)
        if hit is not None:
            return hit
    # v1.0.7：白可以排外侧后，锚点一律按 keep_order=True 计算（顺序就是实物
    # 顺序）。但凡**白全在内侧**、且去掉白之后是 canonical 序的堆法，就是 35 格
    # 标定板量过的结构（标定板每面恒 4 层：1 张彩膜 + 3 张白垫 = 表里的 "C"）。
    # 这些仍要优先用实拍值 —— 模型对 35 格的平均误差 ΔE 4.66，实拍值更准；
    # 少了这条 CMYK 暗部会从 47.6 掉到 51.0、且 19 个实测色直接丢失。
    if int(bands or 4) == 4 and palette is not None and _st:
        _core = tuple(k for k in _st if k != "W")
        _tail = ("W",) * (len(_st) - len(_core))
        if (_core and _st[len(_core):] == _tail
                and list(_st) == canonical_key_order(list(_st))):
            hit = cmyk_measured_present(_core, palette)
            if hit is not None:
                return hit
    return _stack_present_mirror_model(stack_keys, palette, gain,
                                       bands=bands, keep_order=keep_order)


# ---------------------------------------------------------------------------
# 【v1.40.0 / R48】白点中性化 —— 只对"没有青、没有品红"的色组（RYBW）
# ---------------------------------------------------------------------------
# 根因（2026-11-01 用户实拍对比：同图 CMYK 接近原图，RYBW 整片偏黄绿）：
#
#   ``_STACK_REFLECT_T/S`` 是 **CMYK 四色联合标定**的结果。联合拟合里"白"这一档
#   吸收了 C/M 的残差，于是空叠（=8 层全白）预测成 (195,212,200)，
#   Lab(83.4, −8.0, +4.1) —— **既偏暗又偏绿**（青方向 −8）。
#   CMYK 看不出来，是因为它的锚点集在中间调有 4 个近中性色（CM / CCMM / CCCM /
#   CCM），青与品红互相把绿偏压回去；RYBW 只有红黄蓝白，**没有任何青/品红来
#   纠偏**，而它的亮部与中调全靠"白 + 黄"，于是整张图被染成黄绿。
#
# 实测（16 色标准片，整块上色、极致档）：
#   RYBW 原样           Δa* −3.1  Δb* +3.7  中性 5 色 C* 10.6
#   RYBW + 保亮中性化    Δa* +1.9  Δb* +1.9  中性 5 色 C*  6.6
#   CMYK 原样           Δa* +0.6  Δb* +0.9  中性 5 色 C*  9.0
#   CMYK + 保亮中性化    Δa* +6.5  Δb* −1.3  中性 5 色 C*  8.2   ← 反而更差
#
# 【为什么不是重新标定】"白耗材在标准光下就是白"是白点**定义**，不是拟合参数：
# 空叠预测带色相本身就说明模型的白点错了。这里只做**保亮度的通道增益**
# （把空叠的色偏拉平、明度不动），不改任何已标定的透射率/散射系数。
# 若改成"满量白平衡"（把空叠拉到纯白）会把整张图提亮，ΔE 反而 17.3→19.6，
# 所以只允许改色相、不允许改亮度。
#
# 【为什么只在缺 C/M 时生效】CMYK / CMYWGK / 8色MAX 都有青和品红，联合标定的
# 白点偏差被这两色在中间调抵消（实测 CMYK 加中性化反而 Δa* +6.5）。
# 这条判据同时保证**CMYK 的输出逐位不变** —— 已经验证过的东西不许动。
_WHITE_GAIN_CACHE = {}


def white_neutral_gain(keys, palette=None):
    """返回白点的保亮度中性化增益（三通道乘子）。

    ``keys`` 是该色组的色键（决定"有没有青/品红"）；``palette`` 给实际耗材色。
    色组里有 C 或 M 时返回 (1,1,1)（不干预）；否则把空叠预测色的色偏去掉、
    **亮度保持不动**。结果按（色键 + 色板签名）缓存，热循环里可以逐次调用。
    """
    ks = set(keys or ())
    if not ks or not palette:
        # 没有实际耗材色就不做校正：历史探针 / 不传色板的调用保持原语义
        # （r32-B5「预测==目标时 dE 恒为 0」就是靠这条恒真式守着的）。
        return (1.0, 1.0, 1.0)
    if palette:
        sig = tuple(sorted((k, round(float(v[0]), 4), round(float(v[1]), 4),
                            round(float(v[2]), 4)) for k, v in palette.items()))
        ks |= set(palette.keys())
    else:
        sig = ()
    ck = (tuple(sorted(ks)), sig)
    got = _WHITE_GAIN_CACHE.get(ck)
    if got is not None:
        return got
    if "C" in ks or "M" in ks:
        gain = (1.0, 1.0, 1.0)
    else:
        w = stack_present_mirror((), palette)
        lin = [_srgb_to_lin_c(c) for c in w]
        y = 0.2126729 * lin[0] + 0.7151522 * lin[1] + 0.0721750 * lin[2]
        gain = tuple(min(4.0, y / max(1e-6, v)) for v in lin)
    _WHITE_GAIN_CACHE[ck] = gain
    return gain


def apply_white_gain(srgb, gain):
    """把保亮度中性化增益作用到一个 sRGB 呈现色上（线性光域相乘）。"""
    if gain == (1.0, 1.0, 1.0):
        return tuple(srgb)
    return tuple(min(1.0, _lin_to_srgb_c(min(1.0, _srgb_to_lin_c(srgb[i]) * gain[i])))
                 for i in range(3))


# 全序锚点缓存：键 = (色键, 色板, 层数, 白点增益)。面板每次重画都会重建
# matcher，没有这层缓存就是每次 4096~46656 次模型计算。
_ORDERED_ANCHOR_CACHE = {}


def _ordered_mirror_anchors(ks, palette, gain, bands):
    """镜像叠色的**全序**锚点集（v1.0.13）：每个位置都可以是任意色键。

    返回 [(stack_tuple, present_srgb), ...]，按"非白层数升序 + 序列字典序"
    排序 —— 第 0 位恒为全白（``CardStacker.white_anchor`` 与
    ``StackMatcher._white_first`` 都依赖这一点）。

    与 ``mirror_anchors`` 的多集枚举的关系：多集只生成"彩膜在前、白垫在后"
    以及"白全部提到最前"两种排法；全序把它们都包含进来，另外补上
    "白夹在彩膜中间"的整片中间调。**超集 ⇒ 最近邻只会更准。**
    """
    from itertools import product

    # ⚠ 缓存键必须带 basis：CMYK 的给定色值在两档里**完全相同**（都是
    # (0,1,1)/(1,0,1)/(1,1,0)/(1,1,1)），只按颜色做键会让"理论档"拿到
    # "标定档"的缓存 —— 实测表现就是理论档的白点变成 (0.995,0.996,0.988)、
    # 锚点数与标定档逐位相同。
    key = (tuple(ks), palette_basis(palette),
           tuple((k, tuple(round(float(c), 6) for c in palette[k]))
                 for k in sorted(palette)),
           int(bands),
           tuple(round(float(g), 6) for g in (gain or (1.0, 1.0, 1.0))))
    hit = _ORDERED_ANCHOR_CACHE.get(key)
    if hit is not None:
        return list(hit)

    seen = set()
    keep = []
    for seq in product(ks, repeat=int(bands)):
        sig = _stack_present_mirror_model(seq, palette, gain, bands=bands,
                                          keep_order=True)
        sk = tuple(round(v, 6) for v in sig)
        if sk in seen:
            continue
        seen.add(sk)
        keep.append(seq)
    keep.sort(key=lambda s: (sum(1 for k in s if k != "W"), s))
    out = [(st, stack_present_mirror(st, palette, gain, bands=bands,
                                     keep_order=True)) for st in keep]
    if len(_ORDERED_ANCHOR_CACHE) > 6:
        _ORDERED_ANCHOR_CACHE.clear()
    _ORDERED_ANCHOR_CACHE[key] = list(out)
    return out


def mirror_anchors(keys, palette=None, max_depth=4, bands=None,
                   white_outer=False, cap=None, ordered=False):
    """双面镜像叠色卡的**锚点集**：颜色可重复的**多集**堆叠。

    与 ``stack_anchors``（每色最多一层的子集模型）的本质区别：
    参考文件实测 95.2% 的列含重复色（蓝-黄-白-蓝、红-白-白-红…），
    重复才是深色的来源。锚点 = 从全部四键（含白）取 0..max_depth 层的
    全部多集，呈现色按 v1.7.0 深度加权模型（回文 8 子层）计算。

    【v1.0.6：白为什么必须能排到外侧】
    旧实现枚举虽然"包含白"，但呈现模型把每面补足到 ``bands`` 层时把白
    一律补在**内侧**，而 ``canonical_key_order`` 又把 W 固定排在最后 ——
    于是「C」与「CW」「CWWW」在模型里逐位同色，去重时含白条目全被当成
    重复丢弃。实测（``tests/out/_diag_mirror_white.py``）：35 个锚点里
    **含白的 0 个**，白色从来没有参与过调色；皮肤这类低饱和色无处可去，
    只能被甩到高饱和的壳上（ΔE 30+，视觉上发紫发黑）。

    ``white_outer=True``（v1.0.7，**三种镜像模式全部启用**）时，每个多集额外
    追加一个「白全部提到最外」的变体：

        (C,M,W,W)  旧结构：彩膜朝向观察者，白垫在内侧 —— 饱和色
        (W,W,C,M)  新结构：白在观察者这侧 —— 把颜色冲淡，浅色/肤色靠它

    两套并集，所以可达色**只增不减**。实测（用户照片 96×64，整图平均 ΔE，
    旧 → 新）：CMYK 4 层 17.9→14.5、6 层 18.3→14.7；RYBW 17.2→14.9 / 13.9；
    CMYWGK 22.2→20.5 / 20.0；8 色 MAX 14.8→12.2 / 12.4。暗部
    （亮度<0.3）CMYK 47.6→47.5 不退化。

    ``bands`` = 每面层数（默认 = ``max_depth``）。旧调用不传且
    ``white_outer=False`` 时逐位不变。

    ``cap`` = 锚点数上限（默认 ``_STACK_ANCHOR_CAP`` = 36，与旧行为一致）。
    **``cap=0`` 表示不裁剪**：最近邻（整块上色）链路用这个 —— 多一个锚点
    绝不会让最近邻变差，实测 8 色 MAX / 每面 6 层从 36 个放到 2494 个，
    整图中位 ΔE 15.9 → 12.0。凸包抖动链路（``CardStacker``）仍要封顶：
    ``mix_weights`` 是 O(n⁴) 子集枚举，上千个锚点不可用。

    返回 [(stack_tuple, present_srgb), ...]，顺序固定：按层数升序、再按键序。
    """
    if palette:
        flt = clone_palette(palette)
    else:
        flt = dict((k, tuple(BASE_COLORS[k]["srgb"])) for k in BASE_COLORS)
    ks = [k for k in canonical_key_order(keys)]
    _b = max(1, int(bands or max_depth or 4))
    # v1.40.0：白点中性化（只对缺 C/M 的色组生效，CMYK 增益恒为 1）
    gain = white_neutral_gain(keys, flt)

    def _multisets(items, size):
        if size <= 0:
            return [()]
        from itertools import combinations_with_replacement
        return list(combinations_with_replacement(range(len(items)), size))

    # ------------------------------------------------------------------
    # v1.0.13：**全序枚举**（ordered=True）—— 最近邻专用的超集
    # ------------------------------------------------------------------
    # 旧枚举只取"多集 + 把白全提到最外"的两种排法，中间夹白（C W C W C W）
    # 这类排法从来不在锚点里。但呈色模型对顺序敏感（s_k·∏_{上方}t），
    # 中间夹白正是"半饱和中间调"的唯一来源。
    # 实测（tests/out/_diag_color_v1013.py，6 层镜像）：
    #   RYBW 深蓝斗篷 ΔE 27.9 → 4.8、蓝上衣 18.4 → 7.3、灰裤 29.1 → 18.5；
    #   CMYK 理论上衣 19.2 → 16.5。锚点数 RYBW 136 → 2184、CMYK 65 → 293。
    # 全序是旧集合的**超集**（旧排法只是其中两条），最近邻只会更准。
    # 代价是锚点数最多涨到 颜色数^层数，所以由调用方按规模决定是否启用；
    # 结果按（色键 + 色板 + 层数）缓存 —— 面板预览会反复重建，不能每次重算。
    if ordered and not int(cap or 0):
        return _ordered_mirror_anchors(ks, flt, gain, _b)

    seen = {}
    order = []

    if white_outer:
        # 【枚举口径】先按旧口径枚举**全键多集**（白可以在任意位置，含
        # 「B W W K」这种白夹在中间的堆法），再为每个多集追加一个「白全部
        # 提到最外」的变体。两套并集 = 旧可达域 ⊇ + 稀释色，**只会多不会少**。
        #
        # 为什么不能只留"白在外"：白在外是把颜色冲淡（浅色/肤色要它），
        # 白在内才是饱和色。只留外侧时「单层纯蓝」变成「白白白蓝」，实测
        # 8 色组深蓝天 ΔE 18.2 → 25.0、中天蓝 14.7 → 19.8，天空整片发紫；
        # 只留内侧则回到"白永远冲不淡颜色"（v1.0.6 的老问题）。
        for size in range(0, min(int(max_depth), _b) + 1):
            for combo in _multisets(ks, size):
                filled = tuple(ks[i] for i in combo) + ("W",) * (_b - size)
                n_w = filled.count("W")
                variants = [filled]
                if n_w:
                    variants.append(("W",) * n_w
                                    + tuple(k for k in filled if k != "W"))
                for st in variants:
                    signature = _stack_present_mirror_model(
                        st, flt, gain, bands=_b, keep_order=True)
                    key = tuple(round(v, 6) for v in signature)
                    if key in seen:
                        continue
                    seen[key] = st
                    order.append((st, stack_present_mirror(
                        st, flt, gain, bands=_b, keep_order=True)))
        _cap = _STACK_ANCHOR_CAP if cap is None else int(cap)
        if _cap > 0 and len(order) > _cap:
            order = _prune_anchors(order, _cap, keys=ks)
        return order

    # 【有意钳制，勿当 bug 修】上限取 min(max_depth, len(ks))，即"每面层数 ≤ 颜色数"。
    # 层数超过颜色数只会产生"同色重复更多层"的更暗配方，全部落在已有锚点凸包的
    # 内部/下方 —— 凸包不变 ⇒ 抖动混色能力不变。实测（tests/diag_bands_res.py，
    # sRGB 16³ 目标色、CIE76）：放开后配方 35 → 165，而单锚点平均 ΔE 39.80 → 39.75、
    # 最差 113.18 → 113.18、两锚点混合极限 31.23 → 31.21，全部零增益；代价是
    # mix_weights 的子集枚举从 C(35,4) 涨到 C(165,4)（≈350 倍）+ 模型翻倍厚。
    for size in range(0, min(max(1, int(max_depth)), len(ks)) + 1):
        for combo in _multisets(ks, size):
            st = tuple(ks[i] for i in combo)
            # v1.46.0 / R62：去重签名必须用**旧模型色** —— 「含白多集 ≡ 去白
            # 多集」的塌缩关系由反射模型定义。若拿实测色去重，35 个锚点可能
            # 塌缩或触发 _STACK_ANCHOR_CAP 裁剪，破坏「35 叠法 ↔ 标定板 35 格」
            # 的一一对应。返回色才换成 CMYK 实拍值。
            signature = _stack_present_mirror_model(st, flt, gain, bands=_b)
            key = tuple(round(v, 6) for v in signature)
            if key in seen:
                # 保留层数更少的等价堆叠（生成序按层数升序，先到先得）
                continue
            seen[key] = st
            order.append((st, stack_present_mirror(st, flt, gain, bands=_b)))
    # 【v1.7.0】层序模型下「含白多集 ≡ 去白多集」塌缩失效（白层占的深度槽
    # 会改变其余层的权重），锚点可达 C(n+k-1,k) 全量 ~70 个；
    # mix_weights 是 O(n^4) 子集枚举（70 锚点 ≈ 97 万子集/像素，不可行）。
    # 按色保底的远点采样封顶（``cap=0`` 表示不裁 —— 最近邻链路自己承担）。
    _cap = _STACK_ANCHOR_CAP if cap is None else int(cap)
    if _cap > 0 and len(order) > _cap:
        order = _prune_anchors(order, _cap, keys=ks)
    return order


_STACK_ANCHOR_CAP = 36


def _prune_anchors(anchors, cap, keys=None):
    """远点采样封顶 —— **按色保底**，绝不让某一卷料被整体裁掉。

    【为什么不能只按距离挑（v1.0.7 修）】纯远点采样的种子是"前 4 个锚点"，
    之后每次挑离已选集最远的那个。锚点多到要裁剪时（8 色 MAX 每面 6 层
    枚举 1716 个候选、去重后 1213 个），被裁掉的恰好是**颜色相近的那一族**：
    实测 K（黑）在 36 个锚点里剩 0 个 —— 选了八色耗材，黑色一次都不会被
    打印，暗部自然压不下去。
    现在先给每个色键各留一个"在已选集合里最远"的代表（优先取该色排在
    外侧的叠法），再用远点采样把剩下的名额铺满色域。
    第 0 位恒为白/空叠锚点（``CardStacker.white_anchor`` 依赖这个位置）。

    确定性：候选遍历顺序固定、平局取小下标，同输入必得同输出。
    """
    if cap <= 0 or len(anchors) <= cap:
        return list(anchors)

    def _d2(a, b):
        return ((a[1][0] - b[1][0]) ** 2 + (a[1][1] - b[1][1]) ** 2 +
                (a[1][2] - b[1][2]) ** 2)

    n = len(anchors)
    chosen = [0]                       # 白 / 空叠固定第 0 位
    taken = set(chosen)

    # ---- ① 每卷料保底一个锚点 -------------------------------------------
    if keys:
        for k in keys:
            if k in ("W",) or len(chosen) >= cap:
                continue
            best_i, best_rank = None, None
            for i in range(n):
                if i in taken or k not in anchors[i][0]:
                    continue
                st = anchors[i][0]
                # 该色排在外侧（观察者这一侧）= 这卷料真的会影响顶面颜色
                outer = st.index(k) * 2 < len(st)
                dd = min(_d2(anchors[i], anchors[j]) for j in chosen)
                rank = (1 if outer else 0, dd)
                if best_rank is None or rank > best_rank:
                    best_rank, best_i = rank, i
            if best_i is not None:
                chosen.append(best_i)
                taken.add(best_i)

    # ---- ② 剩余名额按远点采样铺满色域 -------------------------------------
    while len(chosen) < cap and len(chosen) < n:
        best_i, best_d = None, -1.0
        for i in range(n):
            if i in taken:
                continue
            dd = min(_d2(anchors[i], anchors[j]) for j in chosen)
            if dd > best_d:
                best_d, best_i = dd, i
        if best_i is None:
            break
        chosen.append(best_i)
        taken.add(best_i)
    chosen.sort()
    return [anchors[i] for i in chosen]


def stack_anchors(keys, palette=None, max_depth=4, base_key="W",
                  allow_repeat=False, order_perm=False, below=(),
                  base_first=False, pad_to=0):
    """滤光叠色的**锚点集**：每个锚点 = 一种堆叠方案 + 它的呈现色。

    滤光片串联满足交换律且同色重复层在理想滤片下无效果，所以锚点 = 彩色键
    的全部子集（层数 ≤ min(max_depth, 彩色数)）。理想 C/M/Y 下锚点恰为：
        白 / C / M / Y / 蓝(CM) / 绿(CY) / 红(MY) / 黑(CMY)
    —— 比凸包投影（只有前 4 个锚点）多出 4 个深色锚点，这正是参考 3MF
    「红黄蓝白能叠出紫绿深色」的机制，也是色深叠色卡的还原度来源。

    ``allow_repeat=True`` 走**多集**（v1.8.0）：同色可重复叠，锚点数 8 → 35。
    物理依据：理想滤片 t²=t 无效果，但本插件的透射率是 Palette_256 实拍标定
    值（C/M/Y 都是小数），t^1.5 ≠ t —— **重复 = 更深一档的同色相**，不是冗余。
    实测（``tests/out/_exp_anchor_multiset.py``）：d=4 的 35 个锚点里 27 个
    落在旧 8 锚点凸包之外，色域网格投影 ΔE 36.09 → 28.66（−21%）。
    ⚠ **必须到 d=4 才进入正收益**：d=3（20 锚点）在色域网格上看着好（−16%），
    真实照片块级中位反而退化 +7%（凸包形状被浅锚点拉偏）。

    ``order_perm=True`` 走**层序搜索**（v1.8.1，仅多集路径）：``stack_present``
    是有序模型（顶层权重 1，向内 ×λ），同一多集的不同排列呈现色不同 ——
    C 在顶的 C+M = 蓝灰 (0.40,0.48,0.54)，M 在顶 = 紫红 (0.54,0.35,0.49)。
    旧实现固定 canonical 色轮序，这个自由度被扔掉了。开启后每个多集枚举全部
    排列为候选，再播种远点采样裁到 ``C.RELIEF_ANCHOR_ORDER_CAP``（35 个旧锚点
    全保 = 新凸包 ⊇ 旧凸包，任何目标色投影距离单调不增，零退化的恒真式保证）。
    实测（``tests/out/_exp_order_search.py``）：真实照片块级中位 ΔE 6.94 → 5.60
    （−19%），改善超 JND 的块 50/336、退化 0。
    ⚠ 只在 ``allow_repeat`` 时生效（子集模型每色最多一片，排列自由度很小）。

    返回 [(stack_tuple, present_srgb), ...]，顺序固定：按层数升序、再按键序
    （层序搜索开启时，裁剪新增段为远点序——同样确定）。

    ``base_first``（v1.0.5）：把基底色（白）也放进可叠层，并排在**外侧**。
    单面叠色卡用它 —— 白膜盖在彩膜外面会把颜色**冲淡**，这正是照片里肤色、
    云、白衣服需要的低饱和区。默认 False → 旧调用逐位不变（白只在末尾补、
    永远打不到外侧，色域因此缺整个低饱和带）。

    ``pad_to``（v1.0.5）：算呈色前把堆叠补白到该层数。单面卡是 8 层结构
    （``expand_single_stack`` 恒补到 bands），而 ``stack_present`` 默认只补到
    标定域 4 层 —— 不补到 8 就会"少算 4 层白"，短叠法的亮度被系统性高估。
    """
    if palette:
        flt = clone_palette(palette)
    else:
        flt = dict((k, tuple(BASE_COLORS[k]["srgb"])) for k in BASE_COLORS)
    chroma = [k for k in canonical_key_order(keys) if k != base_key]
    layers = ([base_key] + chroma) if base_first else chroma
    gain = white_neutral_gain(keys, flt)   # v1.40.0 白点中性化（CMYK 恒为 1）
    _pad = int(pad_to or 0)

    # 【v1.4.4 关键修复：呈现色去重】理想白是恒等滤光片（1,1,1），底色不是白
    # 时会出现「再叠一层白、颜色不变」的重复锚点：底色=天蓝时 M ≡ M+W、
    # Y ≡ Y+W、M+Y ≡ M+Y+W（实测 3 对）。重复锚点两两重合，mix_weights
    # 枚举的三角形出现 ab=(0,0,0)，_tri_weights 边分支 d1=d3=0 → 除零崩溃
    # （2026-09-16 实机：v1.4.2 起底色默认天蓝，card 模式第一次生成就崩）。
    # 去重保留**层数更少**的堆叠（生成序按层数升序）——呈现色集合不变，
    # 凸包投影结果等价，还顺带让该像素少打一层。
    seen = set()
    anchors = []
    extra_pool = []  # 层序搜索的候选（排列锚点），不直接进结果
    # 层数上限：子集模型下超过「彩色数」只会重复既有组合（每色最多一片）；
    # 多集模型下同色可重复，层数不再受颜色数限制，上限直接取 max_depth。
    cap = (max(1, int(max_depth)) if allow_repeat
           else min(max(1, int(max_depth)), len(layers)))
    for size in range(0, cap + 1):
        combos = [()] if not size else (
            _multisets_idx(layers, size) if allow_repeat
            else _subsets_idx(layers, size))
        for combo in combos:
            st0 = tuple(layers[i] for i in combo) if size else ()
            # 层序搜索（v1.8.1）：同一多集的不同排列是不同的呈现色
            # （stack_present 有序，顶层权重 1）。canonical 序先到先得
            # （保持旧行为逐位等价），其余排列进候选池，由远点采样补入。
            perms = ([st0]
                     if (not order_perm or not allow_repeat or not size)
                     else sorted(set(permutations(st0))))
            for pi, perm in enumerate(perms):
                st = tuple(perm)
                # 空堆叠 = 只看到底板。生产配置基底恒白（RELIEF_SUBSTRATE_KEY
                # 断言）：v1.20.0 起与反射模型同口径 = 垫白到 4 层（呈色 =
                # 色板 WWWW 格 (211,218,223)），与打印几何（expand 恒垫白到
                # bands）一致，basic 8 锚点呈现色也重新落回 grid 64/256 值域
                # （超集链恢复）。非白基底（非生产配置、仅历史探针使用）保持
                # v1.4.4 旧语义 = 直视底板色。
                _st = st
                if _pad and len(_st) < _pad:
                    # 补齐的白**并进锚点的堆叠本身**（不是只用于算色）：几何
                    # 侧 ``expand_single_stack`` 也是这样补到 bands 的。两边
                    # 都拿到同一个 8 层序列，预览/顶点色/实物才逐层一致。
                    _st = _st + (base_key,) * (_pad - len(_st))
                if base_key == "W":
                    present = stack_present(_st, flt, gain, below)
                else:
                    present = (flt.get(base_key, (1.0, 1.0, 1.0)) if not st
                               else stack_present(_st, flt, gain, below))
                key = tuple(round(v, 6) for v in present)
                if key in seen:
                    continue
                seen.add(key)
                if pi == 0:
                    anchors.append((_st, present))
                else:
                    extra_pool.append((_st, present))
    if extra_pool:
        # 播种远点裁剪：35 个 canonical 锚点**全保**（凸包 ⊇ 旧凸包 = 零退化
        # 的恒真式保证），再按最远点补排列锚点至 cap。
        anchors = _prune_seeded(anchors, extra_pool,
                                RELIEF_ANCHOR_ORDER_CAP)
    return anchors


def _prune_seeded(anchors, pool, cap):
    """播种远点采样：``anchors`` 全部保留，从 ``pool`` 补到 ``cap`` 个。

    与 ``_prune_anchors``（镜像路径）同一最远点策略，区别是种子 = 全部旧
    锚点而非前 4 个 —— 保证新锚点集是旧集的**超集**，凸包单调扩张。
    确定性：pool 顺序固定（枚举序），每轮取"离已选集最远"者，平局取先到。
    """
    if len(anchors) >= cap or not pool:
        return anchors

    def _d2(a, b):
        return ((a[1][0] - b[1][0]) ** 2 + (a[1][1] - b[1][1]) ** 2 +
                (a[1][2] - b[1][2]) ** 2)

    chosen = list(anchors)
    while len(chosen) < cap and pool:
        best_i, best_d = None, -1.0
        for i in range(len(pool)):
            dd = min(_d2(pool[i], c) for c in chosen)
            if dd > best_d:
                best_d, best_i = dd, i
        if best_i is None:
            break
        chosen.append(pool.pop(best_i))
    return chosen


def grid_anchors(keys, palette=None, depth=3):
    """R7 定长网格锚点集：depth 层、每层 ∈ 全部四键（含白），外→内。

    与 ``stack_anchors``（子集/多集枚举 + 远点裁剪）的本质区别：**定长全
    枚举、不裁剪**。白滤光片恒等（t_W=(1,1,1)），所以：
      * 尾部垫白不改变颜色 ⇒ 任意 ≤depth 层的真实堆叠都被定长表达覆盖
        （如 (C,M) ≡ (C,M,W)）；非尾部的白片占深度槽、压深下层权重
        （λ=0.5 模型），这就是"同一色相的浅档"来源；
      * depth=3：白基底固定，第 1~3 层自由 ⇒ 4³=64 种堆叠（R7-① 白底叠色）；
      * depth=4：第 4 层（最内层=基底位）也放开 ⇒ 4⁴=256 种堆叠
        （R7-② 自由底），实测完全覆盖 v1.9.0 的 35/45 锚点集（0 缺失）。
    呈现色按 6 位小数去重（实测 64/256 全部互异、无一重合）；白锚点
    （全白堆叠）固定排在第 0 位 —— ``CardStacker.white_anchor`` 依赖该约定。
    返回 [(stack_tuple, present_srgb), ...]，顺序确定（product 序，白优先）。
    """
    if palette:
        flt = clone_palette(palette)
    else:
        flt = dict((k, tuple(BASE_COLORS[k]["srgb"])) for k in BASE_COLORS)
    ks = list(canonical_key_order(keys))
    depth = max(1, int(depth))
    gain = white_neutral_gain(keys, flt)   # v1.40.0 白点中性化（CMYK 恒为 1）
    from itertools import product
    seen = set()
    anchors = []
    white = tuple(ks[-1]) * depth      # canonical 序白在最后 ⇒ 全白元组
    for tup in product(ks, repeat=depth):
        present = stack_present(list(tup), flt, gain)
        key = tuple(round(v, 6) for v in present)
        if key in seen:
            continue
        seen.add(key)
        anchors.append((tup, present))
    # 白锚点提到第 0 位（其余保持 product 序 —— 确定性不变）
    anchors.sort(key=lambda a: 0 if a[0] == white else 1)
    return anchors


def mix_weights_near(target, verts, k, white_key=None):
    """大锚点集的**有界精确投影**：最近 K 预选 + 子集穷举。

    精确投影 ``mix_weights`` 要枚举 C(n,≤4) 个子集，n=256 时约 1.75 亿个、
    不可行。这里先按「到目标色的 sRGB 平方距离」取最近 K 个锚点（平局按
    锚点序号，确定性），白锚点强制入选（近白吸附与提亮混合都靠它），再在
    这 ≤K+1 个顶点上做与 ``mix_weights`` **完全相同**的精确投影。
    锚点集全量参与预选 —— 没有任何一个锚点被事前裁掉，谁离目标近谁上，
    与 v1.8.1 的远点裁剪（事前把集合砍到 45）有本质区别。
    k <= 0 或 k >= n 时等价于全量精确投影。实测（``tests/out/
    _exp_anchor_grid.txt``）：K=32 与 K=64 在照片块上中位 ΔE 差 <0.01，
    是耗时/质量膝点（常数 ``C.ANCHOR_NEAR_K``）。
    """
    n = len(verts)
    if k <= 0 or n <= k:
        return mix_weights(target, verts)
    t = (clamp(target[0]), clamp(target[1]), clamp(target[2]))
    order = sorted(range(n), key=lambda i: (_dist2(t, verts[i][1]), i))
    chosen = list(order[:k])
    if white_key is not None:
        for i in range(n):
            if verts[i][0] == white_key and i not in chosen:
                chosen.append(i)
                break
    sub = [verts[i] for i in chosen]
    rw = mix_weights(t, sub)
    out = [0.0] * n
    for i, w in zip(chosen, rw):
        out[i] = w
    return out


def _subsets_idx(items, size):
    """长度为 size 的下标子集（升序），顺序确定。"""
    from itertools import combinations
    n = len(items)
    if size <= 0 or size > n:
        return []
    return list(combinations(range(n), size))


def _multisets_idx(items, size):
    """长度为 size 的下标**多集**（升序、可重复），顺序确定。

    与 ``_subsets_idx`` 的唯一区别是允许同一下标出现多次 —— 多集锚点模型
    （``allow_repeat``）用。空 size 返回 []，由调用方补 ``[()]``。
    """
    from itertools import combinations_with_replacement
    n = len(items)
    if size <= 0:
        return []
    return list(combinations_with_replacement(range(n), size))


class CardStacker(object):
    """色深叠色卡的配色器：滤光锚点凸包投影 + 有序抖动。

    【为什么抖动要在锚点凸包上做】单个像素看到的 = 该柱外层堆叠的**滤光
    乘积**；但相邻像素抖成不同堆叠时，眼睛做的是**加色平均**。所以可 达域 =
    锚点呈现色的凸包 —— 与 v1.3 分区配色同一套数学（mix_weights 投影 +
    quantize_weights 并项 + Bayer 累积分布抖动），只是顶点从 {W,C,M,Y} 四个
    换成滤光锚点八至十六个。色相相近就并项、近白直接给白，两条省换色规则
    在这里同样生效。

    【为什么手工装配 Mixer】锚点键是「CM」「MY」这类复合键，
    ``canonical_key_order`` 只认单字母键，会把它们整批丢掉 —— 所以绕过
    ``Mixer.__init__`` 的键规范化，其余行为（缓存/量化/并项/近白吸附/抖动）
    与分区配色完全一致。
    """

    def __init__(self, keys, palette=None, max_depth=4, base_key="W",
                 weight_levels=0.0, merge_below=0.0, dither_size=8,
                 white_snap=1.01, mirror=False, dither_pattern=None,
                 allow_repeat=False, order_perm=False,
                 grid_depth=None, near_k=0, max_components=0,
                 dither_block=1, below=(), white_outer=False,
                 anchor_cap=None):
        # v1.0.6：``white_outer`` 只在镜像链路有意义 —— 白必须能排到彩膜
        # 外侧才能把颜色冲淡（见 mirror_anchors 的注释）。
        self.white_outer = bool(white_outer) and bool(mirror)
        if mirror:
            anchors = mirror_anchors(keys, palette, max_depth,
                                     bands=max_depth,
                                     white_outer=self.white_outer,
                                     cap=anchor_cap)
        elif grid_depth:
            # R7 定长网格档（扩增 64 / 柱内混色 256）：定长全枚举、不裁剪
            anchors = grid_anchors(keys, palette, grid_depth)
        else:
            anchors = stack_anchors(keys, palette, max_depth, base_key,
                                    allow_repeat=allow_repeat,
                                    order_perm=order_perm, below=below)
        self.allow_repeat = bool(allow_repeat) and not mirror
        self.base_key = base_key if base_key in canonical_key_order(keys) else "W"
        self.mirror = bool(mirror)
        self.stacks = [st for st, _ in anchors]
        # 锚点 id = "S"+序号（稳定），Mixer 只见 id 与呈现色
        akeys = ["S%d" % i for i in range(len(anchors))]
        present = dict((akeys[i], anchors[i][1]) for i in range(len(anchors)))
        self.white_anchor = akeys[0]  # 空堆叠/全白堆叠 = 白（grid 档白固定第 0 位）
        m = Mixer.__new__(Mixer)
        m.keys = akeys                      # 已是构造序，无需规范化
        m.levels = float(weight_levels or 0.0)
        m.merge_below = float(merge_below or 0.0)
        m.dither_size = int(dither_size or 0)
        m.dither_pattern = dither_pattern or DEFAULT_DITHER_PATTERN
        m.dither_block = max(1, int(dither_block or 1))
        m.white_snap = float(white_snap)
        m.white_key = self.white_anchor
        m.near_k = int(near_k or 0)
        # R8：像素档的"每像素 1 种堆叠"走同一 cap_components 机制
        m.max_components = int(max_components or 0)
        m.verts = [(k, present[k]) for k in akeys]
        m._cache = {}
        self._mixer = m
        self.anchors = anchors

    def weights(self, rgb):
        return self._mixer.weights(rgb)

    def pick(self, rgb, row=0, col=0):
        """像素 → 实际使用的堆叠（颜色元组，外→内）。"""
        k = self._mixer.pick(rgb, row, col)
        return self.stacks[int(k[1:])]

    def present(self, rgb):
        """远看呈现色 = 权重加色平均（预览图用，与抖动口径一致）。"""
        return self._mixer.present(rgb)
