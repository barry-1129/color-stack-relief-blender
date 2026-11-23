# -*- coding: utf-8 -*-
"""平整色块板（mosaic，v1.22.0）—— 照片聚类 N 色 → 逐像素分区 → 等高板。

【这个模块解决什么】
"单面叠色"（single）下线后，同一个位置换成**不依赖叠色**的直接上色链路：
  ① 照片先经 ``prepare_grid`` 的 K-Means 量化聚成 N 簇（N = 用户设的色块数）；
  ② 每一格归到最近的一簇 → 该格的**材质键**就是这一簇的色（十六进制键）；
  ③ 几何 = 底板 + 一层等高色块层（顶面全平），一个色块就是一块材质区域。

和叠色链路的本质区别（这是它存在的理由）：
  * 叠色链路要经过"四色凸包投影 + 空间抖动"把一个目标色**逼近**到可达域，
    R26-C 实测该逼近在亮白端留 13.8 ΔE、近黑端留 29.3 ΔE 的系统性残差；
  * 本链路打出来的就是被分配的那个耗材色 —— 没有中间模型，没有逼近残差，
    唯一的误差就是**聚类损失**（N 色能表达照片到什么程度）。预览即实物。
  代价：需要 N 卷耗材与 N 次换色（叠色链路只 4 色）。

【为什么键用十六进制色码，不用 P0/P1】
``get_or_create_material`` 按**名字**复用材质。若键是序号（P0…），换一张照片时
"P0" 会命中上一张照片建的材质 —— 同一份 .blend 里两次生成的模型会共享材质，
第二张照片的颜色会把第一张的模型**改色**（静默、且只在重开文件后才看得出来）。
改用 ``RRGGBB`` 作键：颜色相同才复用（正确），颜色不同必然新建（安全）。

【确定性】全部排序都带并列决胜键（占比 → 色码），同一张图重复生成逐位一致。

【不 import bpy】纯算法模块，可离线跑。
"""

from collections import Counter

from .. import constants as C
from . import quantize as QZ
from .quantize import _np          # numpy（Blender 自带；离线环境可能为 None）


# 材质键 = 6 位大写十六进制色码（如 "8A3C2B"）。见模块头部的理由。
KEY_RE = 6


def _to_key(rgb):
    """sRGB 0..1 三元组 → 6 位十六进制键（四舍五入到 8bit）。"""
    r, g, b = (max(0, min(255, int(round(float(v) * 255.0)))) for v in rgb[:3])
    return "%02X%02X%02X" % (r, g, b)


def key_to_srgb(key):
    """十六进制键 → sRGB 0..1 三元组。"""
    return tuple(int(key[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def plan(grid, w, h):
    """把（已量化的）网格变成配色方案。

    返回 ``(keys, palette, key_grid, stats)``：
      * ``keys``      —— 材质键列表，**按像素占比降序**（并列按色码升序，确定）
      * ``palette``   —— ``{key: (r,g,b)}``，直接喂给 ``resolve_palette``
      * ``key_grid``  —— 逐像素材质键（``key_grid[row][col]``）
      * ``stats``     —— 各色像素数与占比（供面板/报告显示）
    """
    key_grid = [[_to_key(px) for px in line] for line in grid]
    keys, palette, stats = recount(key_grid, w, h)
    return keys, palette, key_grid, stats


def recount(key_grid, w, h):
    """按**已有的键网格**重列配色（合并小色块之后用）。

    返回 ``(keys, palette, stats)`` —— 与 ``plan`` 的后三项口径一致，只是不再
    生成 key_grid（调用方已经有了，而且它是合并结果，不能重算）。
    """
    cnt = Counter()
    for line in key_grid:
        cnt.update(line)
    total = float(w * h) or 1.0
    keys = sorted(cnt, key=lambda k: (-cnt[k], k))
    palette = {k: key_to_srgb(k) for k in keys}
    stats = {
        "n_colors": len(keys),
        "pixels": {k: int(cnt[k]) for k in keys},
        "shares": {k: cnt[k] / total for k in keys},
    }
    return keys, palette, stats


# ---------------------------------------------------------------------------
# 分区构造（生成与预览**唯一共用入口**）
# ---------------------------------------------------------------------------
def plan_for_params(grid, w, h, pixel_mm, min_mm):
    """网格 → 分区方案（聚类 + 最小色块合并 + 重列配色）。

    ⚠ **生成侧与预览侧必须共用本函数** —— 合并参数（min_mm 经 pixel_mm 换算成
    格数）只要两边算得不一样，预览里有的色块在实物上就没有，正是项目里反复
    踩过的"预览一套、打印另一套"。

    返回 ``(keys, palette, key_grid, stats)``；``stats["merged_px"]`` 是本轮
    被合并的格数（0 = 没动过），``stats["min_px"]`` 是换算出的面积判据。
    """
    keys, palette, key_grid, stats = plan(grid, w, h)
    merged_px = 0
    min_px = 0
    if float(min_mm) > 0.0 and float(pixel_mm) > 0.0:
        min_px = int(max(1, round((float(min_mm) / float(pixel_mm)) ** 2)))
        key_grid, merged_px, _passes = merge_small_regions(key_grid, w, h, min_px)
        if merged_px:
            # 合并可能把小簇整簇抹掉 → 按**最终**网格重列一次配色，否则材质表里
            # 会留一个 0 像素的色槽（AMS 槽位白白占一格）。
            keys, palette, stats = recount(key_grid, w, h)
    stats = dict(stats)
    stats["merged_px"] = int(merged_px)
    stats["min_px"] = int(min_px)
    stats["min_mm"] = float(min_mm)
    return keys, palette, key_grid, stats


# ---------------------------------------------------------------------------
# 最小色块合并
# ---------------------------------------------------------------------------
def _label_components(key_grid, w, h, skip_mask=None):
    """8 邻域连通域标注。返回 ``(labels, comps)``。

    ``labels`` 是 h×w 的 int32（-1 = 已跳过/不属于任何域），``comps`` 是
    ``{comp_id: {"key": k, "area": n, "pixels": [(row, col), ...]}}``。

    ⚠ "跳过" 由 ``skip_mask`` 给出（上一轮已判定的像素不再重复聚类），
    这样多轮合并不会把已经并进去的像素再当成新连通域。
    """
    lab = [[-1] * w for _ in range(h)]
    comps = {}
    cid = 0
    for r0 in range(h):
        row = key_grid[r0]
        for c0 in range(w):
            if lab[r0][c0] != -1:
                continue
            if skip_mask is not None and skip_mask[r0][c0]:
                lab[r0][c0] = -2
                continue
            k = row[c0]
            stack = [(r0, c0)]
            lab[r0][c0] = cid
            pixels = []
            while stack:
                r, c = stack.pop()
                pixels.append((r, c))
                for dr in (-1, 0, 1):
                    rr = r + dr
                    if rr < 0 or rr >= h:
                        continue
                    nrow = key_grid[rr]
                    lrow = lab[rr]
                    for dc in (-1, 0, 1):
                        cc = c + dc
                        if cc < 0 or cc >= w or (dr == 0 and dc == 0):
                            continue
                        if lrow[cc] != -1:
                            continue
                        if skip_mask is not None and skip_mask[rr][cc]:
                            lrow[cc] = -2
                            continue
                        if nrow[cc] == k:
                            lrow[cc] = cid
                            stack.append((rr, cc))
            comps[cid] = {"key": k, "area": len(pixels), "pixels": pixels}
            cid += 1
    return lab, comps


def merge_small_regions(key_grid, w, h, min_px, max_passes=6):
    """把连通面积 < ``min_px`` 的色块并给**相邻的最大色块**。

    【为什么需要】照片聚类出来的碎色斑在实物上有两个害处：① 被切片器的小特征
    过滤并回主导色（色块直接消失）；② 每进出一次就是一次换色。合并的判据用
    **连通面积**而不是边长 —— 切片丢色的物理判据是"这条挤出线有没有人跟着
    走"，等价的量就是面积。

    【确定性】每轮：整体标注连通域 → 对**所有**小域同时决定归属（不按扫描序
    逐个改），归属判据 = 相邻域中面积最大者（并列取域号小者）。同时改避免
    "先改的那块影响了后改的"这种顺序依赖。

    返回 ``(key_grid, changed_pixels, passes)``。
    """
    min_px = int(min_px)
    if min_px <= 1 or w <= 0 or h <= 0:
        return key_grid, 0, 0
    total = 0
    out = [list(line) for line in key_grid]
    for p in range(max_passes):
        lab, comps = _label_components(out, w, h)
        small = [cid for cid, c in comps.items() if c["area"] < min_px]
        if not small:
            return out, total, p
        small_set = set(small)
        plan_move = {}
        for cid in small:
            comp = comps[cid]
            # 统计外邻域各域的出现像素数（= 边界接触长度）
            touch = Counter()
            for (r, c) in comp["pixels"]:
                for dr in (-1, 0, 1):
                    rr = r + dr
                    if rr < 0 or rr >= h:
                        continue
                    for dc in (-1, 0, 1):
                        cc = c + dc
                        if cc < 0 or cc >= w or (dr == 0 and dc == 0):
                            continue
                        nb = lab[rr][cc]
                        if nb < 0 or nb == cid:
                            continue
                        touch[nb] += 1
            if not touch:
                continue
            # 目标 = 邻域里面积最大的域（并列取域号小者）；先按接触长度做次级排序，
            # 但**主判据是面积** —— "并给大块"比"并给接触最多的小块"更能收敛。
            best = None
            for nb in sorted(touch):
                nc = comps.get(nb)
                if nc is None:
                    continue
                cand = (nc["area"], touch[nb], -nb)
                if best is None or cand > best[0]:
                    best = (cand, nc["key"])
            if best is None:
                continue
            plan_move[cid] = best[1]
        if not plan_move:
            return out, total, p + 1
        moved = 0
        for cid, newkey in plan_move.items():
            for (r, c) in comps[cid]["pixels"]:
                if out[r][c] != newkey:
                    out[r][c] = newkey
                    moved += 1
        total += moved
        if moved == 0:
            return out, total, p + 1
    return out, total, max_passes


# ---------------------------------------------------------------------------
# 全色域分析
# ---------------------------------------------------------------------------
def _lab_parts(arr_lab):
    L = arr_lab[..., 0]
    a = arr_lab[..., 1]
    b = arr_lab[..., 2]
    chroma = (a * a + b * b) ** 0.5
    hue = _np.arctan2(b, a) * 180.0 / 3.141592653589793
    return L, chroma, (hue + 360.0) % 360.0


def gamut_report(raw_grid, q_grid, quant_stats=None):
    """全色域分析：照片真实色 → N 色还原色的**逐像素误差分布** + 色彩构成。

    ``raw_grid`` = 未量化的目标网格（照片原始目标色），``q_grid`` = 量化后的
    网格。两者的逐格 CIE76 色差就是"这套 N 色能把这张照片还原到什么程度"的
    直接度量 —— 比只看量化损失的均值有用得多，因为**误差分布**才是观感来源
    （中位小、p90 大 = 大部分像、局部有明显色偏）。

    返回 dict（全部可直接显示/落进 meta）：``dE_*`` / ``cover_*`` / ``lab_*``
    / ``hue_hist``。
    """
    if _np is None:
        return {}
    a0 = QZ.grid_to_array(raw_grid)
    a1 = QZ.grid_to_array(q_grid)
    l0 = QZ.srgb_to_lab(a0)
    l1 = QZ.srgb_to_lab(a1)
    dE = QZ.delta_e76(l0, l1)
    L0, C0, H0 = _lab_parts(l0)
    _, _, H1 = _lab_parts(l1)

    # 色相直方图：12 个 30° 桶，只用"有彩度"的像素（chroma < 3 的灰/白/黑
    # 归进"中性"桶 —— 它们的色相在 Lab 里是噪声，分桶统计没有意义）。
    neutral = C0 < 3.0
    bins = _np.zeros(13, dtype=_np.float64)
    idx = (H0 / 30.0).astype(_np.int32) % 12
    for i in range(12):
        bins[i] = float(((idx == i) & (~neutral)).sum())
    bins[12] = float(neutral.sum())
    tot = float(max(1, bins.sum()))
    bins = bins / tot

    out = {
        "dE_mean": float(dE.mean()),
        "dE_med": float(_np.median(dE)),
        "dE_p90": float(_np.percentile(dE, 90)),
        "dE_max": float(dE.max()),
        # 覆盖率 = 该误差以内的像素占比。1/2/3 ΔE 大致对应"看不出来 / 仔细看 /
        # 看得出但可接受"三档（CIE76 在低值区近似 JND≈2.3）。
        "cover_1": float((dE <= 1.0).mean()),
        "cover_2": float((dE <= 2.0).mean()),
        "cover_3": float((dE <= 3.0).mean()),
        "cover_5": float((dE <= 5.0).mean()),
        "lab_L_med": float(_np.median(L0)),
        "lab_L_p10": float(_np.percentile(L0, 10)),
        "lab_L_p90": float(_np.percentile(L0, 90)),
        "lab_chroma_med": float(_np.median(C0)),
        "neutral_frac": float(neutral.mean()),
        "hue_hist": [round(float(v), 4) for v in bins],
        "n_pixels": int(a0.reshape(-1, 3).shape[0]),
    }
    if quant_stats:
        out["quant_dE_mean"] = float(quant_stats.get("dE_mean") or 0.0)
        out["quant_dE_max"] = float(quant_stats.get("dE_max") or 0.0)
        out["unique_in"] = int(quant_stats.get("unique_in") or 0)
        out["despeckled"] = int(quant_stats.get("despeckled") or 0)
    return out


def hue_hist_labels():
    """色相直方图 13 个桶的标签（12 个 30° 桶 + 中性桶）。"""
    out = []
    for i in range(12):
        out.append("%d-%d" % (i * 30, (i + 1) * 30))
    out.append("N")
    return out


def suggest_color_count(raw_grid, max_k=None):
    """给出"加到第几色就不划算了"的建议（收益/代价的膝点）。

    做法：对 k = 2..max_k 各跑一次量化，看**覆盖率的边际增益** —— 当再加一色
    只能把 ``cover_3`` 提高不到 0.5 个百分点时，认为已经饱和，返回第一个饱和
    的 k。只是个建议值（面板上仍是用户说了算），失败返回 None。
    """
    if _np is None:
        return None
    try:
        k_max = int(max_k or min(C.MAX_COLOR_COUNT, 12))
        arr = QZ.grid_to_array(raw_grid)
        l0 = QZ.srgb_to_lab(arr)
        best = None
        prev = -1.0
        for k in range(2, k_max + 1):
            qg, _st = QZ.quantize_grid(raw_grid, k, despeckle=False)
            a1 = QZ.grid_to_array(qg)
            dE = QZ.delta_e76(l0, QZ.srgb_to_lab(a1))
            cov = float((dE <= 3.0).mean())
            gain = cov - prev
            if prev >= 0 and gain < 0.005:
                return k - 1
            prev = cov
            best = k
        return best
    except Exception:  # noqa: BLE001
        return None
