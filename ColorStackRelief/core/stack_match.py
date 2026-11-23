# -*- coding: utf-8 -*-
"""叠色卡 / 叠色浮雕的**逐像素最近堆叠匹配**（v1.30.0 / R37）。

【为什么另起一条链路】
旧链路（``CardStacker``）做的是「把目标色投影到锚点凸包上，再按权重做
有序抖动」。凸包投影本身没错，但它的前提是**相邻像素的颜色能被眼睛平均**。
实机在 0.4mm 喷嘴、0.78mm 像素上打印后（2026-10-24 用户实拍）：抖动出来的
是一颗颗品红/黄/青的碎点，边界还被喷头拉出一圈溢料晕圈 —— 远看是花屏，
近看是彩噪。用户的判断没错：那不是"混色"，是"彩色碎点"。

参考 Lumina Studio 的实现（``_lumina/core/image_processing.py``）：
  * 颜色匹配 = **每个像素在 LUT 里找最近的一种**（Lab 空间），
  * **不做任何跨像素抖动**，
  * 匹配后做**孤立像素清理**（把与 8 邻域都不同的堆叠改成邻域众数）。
  他们的 4 色板是 4⁵=1024 个**实测**堆叠，我们的是 4 层多集 35 个解析堆叠，
  但"一像素一叠法、不抖动"这条是共通的，也是这次翻车的直接原因。

因此本模块提供：
  * ``StackMatcher``：锚点集 + 逐像素最近匹配 + 小区域合并（可关）；
  * 接口与 ``CE.CardStacker`` 对齐（``pick`` / ``present`` / ``stacks``），
    所以生成、预览、探针都只换构造处，不改调用方。

【为什么必须做小区域合并】
打印的换色单位是"一层里的一段连续同色"，不是像素。一个只占 1~2 格的孤岛
会让喷头专门跑一趟换色，还在边缘留下溢料晕圈（实拍里黄色小块四周的品红边
就是这么来的）。合并阈值按**物理尺寸**给（``min_feature_mm``），换算成格数
用 ``pixel_mm``，与分辨率解耦。
"""

import math
from collections import deque

from . import color_engine as CE
from .. import constants as C

try:                     # Blender 自带 numpy；没有就回退纯 Python（结果逐位一致）
    import numpy as _np
except Exception:        # pragma: no cover
    _np = None

# 锚点数超过这个值才启用向量化：小集合上 numpy 的固定开销反而更慢
_NP_MIN_ANCHORS = 48

# 近白吸附阈值（CIE76 ΔE）：中性目标与空叠只差这么多时改选空叠（少印几层）。
# 1.0 是"刚好看不出"的量级；再大就会把浅奶油/浅粉也吞成纯白。
# v1.0.13：1.0 → 2.5。全序锚点里多出大量"近白但带一点点色"的叠法（例如
# 0.97 的灰），ΔE 2 以内就会把纯白格抢走 —— 后果不只是白格多印 6 片膜，
# 还让明信片的"白色留白下沉 2 层"整条失效（实测 r54：白格 0 个）。
# 2.5 仍然只对**中性**目标生效（色度门禁 10 不变），浅奶油/浅粉进不来。
_WHITE_TIE_DE = 2.5


class StackMatcher(object):
    """锚点集 + 逐像素最近匹配（可选小区域合并）。

    ``anchors`` 由 ``CE.mirror_anchors`` 给出（回文多集，顺序固定），
    因此同一目标色永远选到同一种堆叠 —— 确定性回归的前提。
    """

    def __init__(self, keys, palette=None, depth=4, base_key="W", mirror=True,
                 below=(), base_first=False, pad_to=0, white_outer=False,
                 anchor_cap=0):
        self.mirror = bool(mirror)
        self.depth = int(depth)
        self.base_key = base_key
        # v1.0.6：双面卡的白必须能排到彩膜**外侧**才能冲淡颜色；见
        # color_engine.mirror_anchors 的注释（旧实现里白的 35 个锚点全是 0 个）。
        self.white_outer = bool(white_outer) and bool(mirror)
        # v1.0.3：单面叠色彩膜下面还垫着 ``below``（4 层白隔离垫）。
        # 白垫既散射又挡光，不并进锚点的呈色就会「选色按无垫算、实物发浅」。
        self.below = tuple(below or ())
        # v1.0.7：整块上色是**最近邻**，锚点越多只会更准（∪ 包含关系），
        # 所以默认 ``anchor_cap=0`` = 全量枚举，不再被 36 个封顶砍掉整卷料
        # （8 色 MAX 曾因此一个黑色锚点都没有）。抖动链路的 Mixer 仍封顶。
        self.anchor_cap = int(anchor_cap or 0)
        if mirror:
            # v1.0.13：**全序枚举**（超集）。多集枚举只给"彩膜在前/白全在前"
            # 两种排法，中间夹白（C W C W C W）从来不在锚点里 —— 而它正是
            # 半饱和中间调的唯一来源。实测 6 层镜像：RYBW 深蓝 ΔE 27.9→4.8、
            # 蓝上衣 18.4→7.3；CMYK 65→293 个锚点。
            # 只对"整块上色"（anchor_cap=0）启用；凸包抖动链路仍是多集 + 封顶。
            # 规模护栏：8 色 × 6 层 = 262144 个序列，枚举与最近邻都太贵，
            # 超过 5 万个就退回多集（8 色组仍能用，只是稀释色少一些）。
            _nk = len(set(keys or ()))
            _ordered = (self.anchor_cap == 0 and self.depth >= 5
                        and _nk ** int(self.depth) <= 50000)
            self.anchors = CE.mirror_anchors(
                keys, palette, self.depth, bands=self.depth,
                white_outer=self.white_outer, cap=self.anchor_cap,
                ordered=_ordered)
        else:
            self.anchors = CE.stack_anchors(
                keys, palette, self.depth, base_key, allow_repeat=True,
                below=self.below, base_first=bool(base_first),
                pad_to=int(pad_to or 0))
        self.stacks = [st for st, _ in self.anchors]
        self.colors = [rgb for _, rgb in self.anchors]
        self.labs = [CE._srgb_to_lab(rgb) for rgb in self.colors]
        # 纯白 = 整柱都是基底色。镜像链路里 W 不进多集记号，所以"全白"就是
        # 空元组；单面链路（v1.0.5 起白参与叠色 + 补满 bands）是 8 个 W。
        self._white = set(i for i, st in enumerate(self.stacks)
                          if not st or all(k == self.base_key for k in st))
        # 白锚点里取**第一个**（= 层数最少的空叠）：近白目标吸附到它，少印几层。
        self._white_first = min(self._white) if self._white else 0
        self._chromatic = [i for i in range(len(self.stacks))
                           if i not in self._white]
        self._lab_cache = {}
        self._picks = None
        self.stats = {}
        # v1.0.7：8 色 MAX / 单面 8 层全量枚举有上万个锚点，纯 Python 逐点
        # 扫描 16000 像素要一分多钟。这里预先转成 ndarray，最近邻一次向量化；
        # 没有 numpy 时仍走原来的循环（同样的下标，同样的平局规则）。
        self._lab_np = None
        self._white_np = None
        if _np is not None and len(self.labs) >= _NP_MIN_ANCHORS:
            self._lab_np = _np.asarray(self.labs, dtype=_np.float64)
            if self._white and len(self._white) < len(self.labs):
                self._white_np = _np.fromiter(sorted(self._white),
                                              dtype=_np.int64,
                                              count=len(self._white))

    # ------------------------------------------------------------------ 匹配
    def _lab_of(self, rgb):
        k = (round(float(rgb[0]), 4), round(float(rgb[1]), 4),
             round(float(rgb[2]), 4))
        got = self._lab_cache.get(k)
        if got is None:
            got = CE._srgb_to_lab(k)
            self._lab_cache[k] = got
        return got

    def nearest_index(self, rgb):
        """目标色 → 最近的锚点下标（CIE76，Lab 内欧氏距离）。

        v1.33.0（R40）：**纯白门禁** —— 目标色的 Lab 色度超过
        ``C.WHITE_MAX_CHROMA`` 时，空叠（全白）不参选。原因见
        ``constants.WHITE_MAX_CHROMA`` 的实测注释：呈色模型给八层全白的
        预测是个偏绿的浅灰，导致浅青/浅蓝/奶油统统塌成纯白（用户实拍
        "麻将牌该青却印成白"）。这是一条**选择规则**，不改已标定的呈色
        模型；代价是那些浅色的 ΔE 最多升一点点，换来色相不再丢失。

        v1.0.7：**近白吸附** —— 中性目标（色度 ≤ 门禁）如果最近的叠法与
        空叠只差 ``_WHITE_TIE_DE`` 以内，就选空叠。起因是白在外侧放开后，
        模型会把「白+黄+红」这类堆法算得**比白点还亮**（裁剪到 1.0），
        纯白像素的最近锚点变成"白白黄河"—— 多印 2~4 片彩膜，看着仍是白。
        物理上白底 + 只吸收的色膜不可能比白亮，ΔE 1 以内也看不出差别，
        所以这里按"少印几层"裁决。
        """
        t = self._lab_of(rgb)
        cand = range(len(self.labs))
        if self._white and self._chromatic:
            if math.hypot(t[1], t[2]) > C.WHITE_MAX_CHROMA:
                cand = self._chromatic
        if self._lab_np is not None:
            # 向量化：一次算完全部锚点的平方距离，argmin 取**首个**最小值 ——
            # 与下面纯 Python 的 ``d < bd``（严格小于）平局规则一致。
            d = self._lab_np - _np.asarray(t, dtype=_np.float64)
            d = (d * d).sum(axis=1)
            if (self._white_np is not None
                    and math.hypot(t[1], t[2]) > C.WHITE_MAX_CHROMA):
                d[self._white_np] = _np.inf
            best = int(d.argmin())
            return self._snap_white(t, best, float(d[best]))
        best, bd = 0, float("inf")
        labs = self.labs
        for i in cand:
            p = labs[i]
            d = (p[0] - t[0]) ** 2 + (p[1] - t[1]) ** 2 + (p[2] - t[2]) ** 2
            if d < bd:
                bd, best = d, i
        return self._snap_white(t, best, bd)

    def _nearest_row(self, rgbs):
        """整行目标色 → 锚点下标（向量化版 ``nearest_index``）。

        v1.0.13 新增：全序锚点到 4096 个以后，逐像素 Python 循环成为瓶颈。
        这里一次算一批，规则与逐点版**逐位一致**：
          * 纯白门禁（色度 > WHITE_MAX_CHROMA 时白锚点不参选）；
          * ``argmin`` 取首个最小值 = 原实现的严格小于；
          * 近白吸附（ΔE 1.0 以内且目标为中性色）。
        """
        n = len(rgbs)
        t = _np.asarray([self._lab_of(c) for c in rgbs], dtype=_np.float64)
        chroma = _np.hypot(t[:, 1], t[:, 2])
        d = self._lab_np[None, :, :] - t[:, None, :]
        d2 = (d * d).sum(axis=2)
        if self._white_np is not None:
            gate = chroma > C.WHITE_MAX_CHROMA
            if bool(gate.any()):
                d2[_np.ix_(gate, self._white_np)] = _np.inf
        best = d2.argmin(axis=1).astype(_np.int64)
        bestd = _np.sqrt(d2[_np.arange(n), best])
        if self._white:
            _w = (self._white_np if self._white_np is not None
                  else _np.fromiter(sorted(self._white), dtype=_np.int64,
                                    count=len(self._white)))
            is_w = _np.isin(best, _w)
            wl = _np.asarray(self.labs[self._white_first], dtype=_np.float64)
            dw = _np.sqrt(((t - wl) ** 2).sum(axis=1))
            swap = ((~is_w) & (chroma <= C.WHITE_MAX_CHROMA)
                    & ((dw - bestd) < _WHITE_TIE_DE))
            best = _np.where(swap, self._white_first, best)
        return [int(i) for i in best]

    def _snap_white(self, t, best, best_d2):
        """近白目标吸附到空叠（见 ``nearest_index`` 的 v1.0.7 说明）。"""
        if not self._white or best in self._white:
            return best
        if math.hypot(t[1], t[2]) > C.WHITE_MAX_CHROMA:
            return best
        p = self.labs[self._white_first]
        dw = math.sqrt((p[0] - t[0]) ** 2 + (p[1] - t[1]) ** 2
                       + (p[2] - t[2]) ** 2)
        if dw - math.sqrt(best_d2) < _WHITE_TIE_DE:
            return self._white_first
        return best

    def present(self, rgb):
        """该目标色对应的**实际呈现色**（最近锚点的预测色）。"""
        return self.colors[self.nearest_index(rgb)]

    # ------------------------------------------------------------------ 规划
    def prepare(self, grid, w, h, min_feature_mm=0.0, pixel_mm=1.0,
                block_px=1, max_stacks=0, min_width_mm=0.0, progress=None):
        """大像素块 → 整图匹配 → 色阶上限 → 小区域合并，缓存每像素堆叠。

        顺序是硬约束：先并块（目标色粗化）再匹配，块内才会只有一种叠法；反过来
        先匹配再并块只能靠"块内取众数"表决，实测会让平滑区切阶梯、换色变多。

        返回 ``stack_grid[row][col]``（颜色键元组，外→内），与
        ``CardStacker`` 链路同构，几何侧无需区分。
        """
        # v1.31.0（R38）：「均衡 / 像素」档每 k×k 格只保留**中心那一格**的颜色，
        # 整块都用它（不是块平均，也不是块内众数 —— 两种都实测更差，见
        # block_sample 的注释）。块内因此只剩一种叠法。
        blocked = 0
        k = max(1, int(block_px or 1))
        work = grid
        if k > 1:
            work, blocked = block_sample(grid, w, h, k)

        idx = []
        for row in range(h):
            gline = work[row]
            if self._lab_np is not None and w >= 64:
                # v1.0.13：全序锚点最多 4096 个，逐像素走 Python 循环要
                # 4096×格数次比较（600 精细度 ≈ 6500 万次，几十秒）。
                # 这里按 256 格一批做向量化最近邻，结果与逐点逐位一致
                # （argmin 取首个最小值 = 原来的严格小于规则）。
                line = self._nearest_row(gline)
            else:
                line = [self.nearest_index(gline[col]) for col in range(w)]
            idx.append(line)
            if progress is not None and (row % 16 == 0):
                progress(row / float(max(1, h)))

        # 色阶上限（海报化）：把叠法种类从 S 压到 N。⚠ 它**不保证**换色次数不增：
        # 换色按「每层出现的耗材种类」统计，而压缩只是把每个格子映射到另一种
        # 叠法 —— 被映射到的叠法可能带入该层原本没有的耗材（如 CC → MY）。
        # 实测多数情况是减少（19→18 / 22→17 / 36→24），但那是观测、不是定理。
        capped = 0
        if max_stacks and int(max_stacks) > 0:
            idx, capped = cap_stacks(idx, self.labs, int(max_stacks))

        merged = 0
        min_px = 0
        if min_feature_mm and min_feature_mm > 0.0:
            # 阈值 → 格数用 **ceil**：round 会把 1.2mm/0.78mm 的 2.37 格压成
            # 2 格，与「极致」档的 0.6mm（也是 2 格）撞在一起 —— 三档质量
            # 输出逐格相同，用户一眼就看出"档位没用"（R36 踩过同一坑）。
            _side = float(min_feature_mm) / max(1e-6, pixel_mm)
            min_px = max(2, int(math.ceil(_side * _side)))
            idx, merged = merge_small_regions(idx, w, h, min_px)

        # v1.33.0（R40）：再按**宽度**并一次。面积阈值放过窄条（1 格宽、
        # 200 格长的色条面积 200 格 >> 阈值），而窄条在切片器里要么被丢掉
        # （= 两色之间的空隙，成品一掰就断），要么糊成一条线。见
        # ``constants.MIN_PRINT_WIDTH_MM`` 的实测注释。
        thin = 0
        width_px = 0
        if min_width_mm and min_width_mm > 0.0:
            # ⚠ 不能像面积那样钳到 2 格：粗网格上 1 格本身就是 1mm 以上
            # （浮雕 96 格 / 100mm → 1.04mm/格），早就能印；只有"1 格比线宽
            # 还细"时才该动手，所以 1 格就退回空操作。
            # v1.37.0（R44）：换算改走 C.px_from_mm（带 0.2 格容差）。原来用
            # ceil，100mm/240 精细度 的 0.4167mm 格会被 0.42mm 线宽抬成 2 格，
            # 于是"1 格宽"这种正好一条挤出线的细节被当窄条并掉 —— 用户反馈的
            # "精细度降低"有一半来自这里。
            width_px = C.px_from_mm(float(min_width_mm), pixel_mm)
            if width_px >= 2:
                idx, thin = enforce_min_width(idx, w, h, width_px)
            else:
                width_px = 0

        self._picks = [[self.stacks[i] for i in line] for line in idx]
        used = {}
        for line in idx:
            for i in line:
                used[i] = used.get(i, 0) + 1
        self.stats = {
            "unique_stacks": len(used),
            "merged_px": merged,
            "blocked_px": blocked,
            "block_px": k,
            "capped": capped,
            "max_stacks": int(max_stacks or 0),
            "min_feature_mm": float(min_feature_mm or 0.0),
            "min_px": int(min_px),
            "min_width_mm": float(min_width_mm or 0.0),
            "min_width_px": int(width_px),
            "thinned_px": thin,
            "pixel_mm": float(pixel_mm),
        }
        return self._picks

    # -------------------------------------------------------------- 调用接口
    def pick(self, rgb, row=0, col=0):
        if self._picks is not None:
            if 0 <= row < len(self._picks) and 0 <= col < len(self._picks[row]):
                return self._picks[row][col]
        return self.stacks[self.nearest_index(rgb)]

    def used_keys(self, rgb=None):
        if rgb is None:
            return list(set(k for st in self.stacks for k in st))
        return list(set(self.stacks[self.nearest_index(rgb)]))


def block_sample(grid, w, h, k):
    """像素化：每 ``k×k`` 格取**中心格**的颜色，整块用这个颜色。

    【为什么不是平均、也不是众数（两次实测打脸）】
      * **众数**（先匹配再把块内多数叠法写满）：平滑渐变本来一整片同色，取众数
        后每块各自选了不同叠法 → 把平滑区切成阶梯：换色 24 → 38、面数变多。
      * **平均**（先块平均再匹配）：平均色落在锚点之间，反而引出更多锚点
        （叠法 12 → 26），换色 24 → 37，同样变差。
      * **中心格采样**：每个块的颜色就是原网格里**真实存在的一格**，不会像平均
        那样凭空造出中间色，也不会像众数那样在平滑区切出阶梯。
    ⚠ 单调性只成立一半（R38 实测修正）：**同一行内**（x 方向）输出是原行颜色
    序列的子序列 ⇒ 该行游程数不增；但 **y 方向**是"整行复制到相邻层"，被复制的
    那一行如果比它覆盖的行更杂，换色次数会上升（换色按逐切片层的耗材种类统计，
    复制一行等于把该行的复杂度乘以层数）。实测：合成图 19→21（块 2）、实拍照
    36→40。所以"块越大越省换色"不成立，省换色靠的是 ``cap_stacks``。

    返回值 ``(新网格, 被改动的格数)``；不改动传入的 ``grid``。
    """
    k = max(1, int(k or 1))
    if k <= 1 or w <= 0 or h <= 0:
        return grid, 0

    out = [list(line) for line in grid]
    changed = 0
    for by in range(0, h, k):
        y_end = min(h, by + k)
        sy = min(h - 1, by + (y_end - by) // 2)
        for bx in range(0, w, k):
            x_end = min(w, bx + k)
            sx = min(w - 1, bx + (x_end - bx) // 2)
            rep = grid[sy][sx]
            for y in range(by, y_end):
                line = out[y]
                for x in range(bx, x_end):
                    cur = line[x]
                    if (cur[0] != rep[0] or cur[1] != rep[1]
                            or cur[2] != rep[2]):
                        changed += 1
                    line[x] = rep
    return out, changed


def cap_stacks(idx, labs, max_stacks):
    """把用到的叠法压到最多 ``max_stacks`` 种（色阶上限 / 海报化）。

    【为什么要这一步】换色次数 = 每个切片层里**同时出现的耗材种类数**之和。
    把大块像素化（``block_sample``）会让同一层里多出一种颜色共存，实测在
    合成渐变上 24 → 35 次 / 19 → 21 次 —— 用户选「像素」是为了打得更快，不能
    反而更慢。先把色阶压到 N 种（``max_stacks``），让每层同时出现的叠法变少。

    ⚠ 单调性边界（R38 实测修正）：**在「叠法种类」这一层**是恒真式 —— 每个格子
    被映射成一种叠法，像的势 ≤ 原集合的势，所以任何一层的**叠法种类数**不增。
    但换色次数统计的是**耗材种类**：被映射到的叠法可能带入该层原本没有的耗材
    （CC 的一种料 → MY 的两种料），所以「换色次数必然不增」不成立，只是实测
    多数情形在减少（19→18 / 22→17 / 36→24）。别再把它当成恒真式写进断言。

    选代表用**最远点采样**（Lab 空间，确定性）：先取用得最多的叠法，之后每次
    取"离已选代表最远"的那个；距离并列时取编号小的。

    返回 ``(新 idx, 压掉的种类数)``。
    """
    n_cap = int(max_stacks or 0)
    if n_cap <= 0:
        return idx, 0
    used = {}
    for line in idx:
        for v in line:
            used[v] = used.get(v, 0) + 1
    if len(used) <= n_cap:
        return idx, 0

    order = sorted(used.items(), key=lambda kv: (-kv[1], kv[0]))
    reps = [order[0][0]]
    while len(reps) < n_cap:
        best, best_d = None, -1.0
        for v in order:
            v = v[0]
            if v in reps:
                continue
            d = min(sum((labs[v][k] - labs[r][k]) ** 2 for k in range(3))
                    for r in reps)
            if d > best_d + 1e-12:
                best, best_d = v, d
        if best is None:
            break
        reps.append(best)
    mapping = {}
    for v, _n in order:
        if v in reps:
            mapping[v] = v
            continue
        best, best_d = reps[0], None
        for r in reps:
            d = sum((labs[v][k] - labs[r][k]) ** 2 for k in range(3))
            if best_d is None or d < best_d - 1e-12:
                best, best_d = r, d
        mapping[v] = best
    out = [[mapping[v] for v in line] for line in idx]
    return out, len(used) - len(reps)


def merge_small_regions(idx, w, h, min_px):
    """把面积小于 ``min_px`` 的连通区域并给**边界最长**的邻区。

    4 邻域连通。同一片网格里可能有多个小区域，按「行优先扫描」的确定性顺序
    处理；并列时取编号小的邻区（不随机、不看字典顺序），保证同输入同输出。
    迭代到没有可合并的区域为止（合并后可能让邻区变小）。
    """
    if w <= 0 or h <= 0 or min_px <= 1:
        return idx, 0

    grid = [list(line) for line in idx]
    total_merged = 0
    for _ in range(8):
        seen = [[False] * w for _ in range(h)]
        small = []
        for r in range(h):
            for c in range(w):
                if seen[r][c]:
                    continue
                val = grid[r][c]
                stack = [(r, c)]
                seen[r][c] = True
                cells = []
                border = {}
                while stack:
                    y, x = stack.pop()
                    cells.append((y, x))
                    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        ny, nx = y + dy, x + dx
                        if ny < 0 or ny >= h or nx < 0 or nx >= w:
                            continue
                        nv = grid[ny][nx]
                        if nv == val:
                            if not seen[ny][nx]:
                                seen[ny][nx] = True
                                stack.append((ny, nx))
                        else:
                            border[nv] = border.get(nv, 0) + 1
                if len(cells) < min_px and border:
                    small.append((cells, border, val))

        if not small:
            break
        changed = 0
        # 【本轮已确定去向的色块，不能再被并走】否则两块互为唯一邻居时会
        # 来回翻转：A 并进 B（A 的格全变 B），随后 B 那条（用同一轮的旧
        # 边界）又并进 A，图案回到原样，8 轮下来一格没少、改动数翻 8 倍。
        # absorbed = 本轮被并掉的代表值；protected = 本轮吸收过别人的值。
        absorbed = set()
        protected = set()
        for cells, border, val in small:
            if val in absorbed or val in protected:
                continue
            # 前面的合并可能已经改过这片格子（同一轮快照 → 必须复核）
            if any(grid[y][x] != val for y, x in cells):
                continue
            cand = [kv for kv in border.items() if kv[0] not in absorbed]
            if not cand:
                continue
            # 边界最长的邻区；并列取编号小的（确定性）
            target = max(cand, key=lambda kv: (kv[1], -kv[0]))[0]
            for y, x in cells:
                grid[y][x] = target
            changed += len(cells)
            absorbed.add(val)
            protected.add(target)
        total_merged += changed
        if not changed:
            break

    return grid, total_merged


def _runs(grid, w, h):
    """每个格子所在「同值游程」的长度：先横后纵，取较小值当**局部宽度**。

    对一条 1 格宽、200 格长的横条：横向游程 200、纵向 1 → 宽度 1，正是
    我们要抓的那种"面积够大、宽度不够"的窄条。
    """
    runh = [[w] * w for _ in range(h)]
    for r in range(h):
        line = grid[r]
        row = runh[r]
        c = 0
        while c < w:
            v = line[c]
            j = c + 1
            while j < w and line[j] == v:
                j += 1
            for k in range(c, j):
                row[k] = j - c
            c = j
    width = [[w] * w for _ in range(h)]
    for c in range(w):
        r = 0
        while r < h:
            v = grid[r][c]
            i = r + 1
            while i < h and grid[i][c] == v:
                i += 1
            span = i - r
            for k in range(r, i):
                width[k][c] = min(runh[k][c], span)
            r = i
    return width


def enforce_min_width(idx, w, h, min_px):
    """把**宽度**小于 ``min_px`` 的窄条并给最近的"够宽"区域。

    【为什么必须做，而不是调面积阈值】``merge_small_regions`` 只看面积：
    一条 1 格宽的窄条可以有上百格面积，于是被完整保留 —— 但它在切片器里
    只有 0.167mm 宽，比 0.376mm 的挤出线还窄。切片器对这种窄条的两种处理
    （丢掉 / 糊成一条线）都会毁掉图案：丢掉就是**两色之间的空隙**，成品
    一掰就断（用户 2026-10-29 连两轮反馈的问题）。

    做法：算局部宽度 → 找出够宽的格子当种子 → 从种子向外做 4 邻域 BFS，
    窄格子一律并给最先到达的那个种子（多源最短路，等价于形态学的
    "先腐蚀到只剩宽区、再按最近邻膨胀回来"）。确定性、不看字典顺序。

    返回 ``(新网格, 被改动的格数)``。全图都窄时不动（没有种子可依）。
    """
    if w <= 0 or h <= 0 or min_px <= 1:
        return idx, 0

    grid = [list(line) for line in idx]
    width = _runs(grid, w, h)
    seed = [[width[r][c] >= min_px for c in range(w)] for r in range(h)]

    q = deque()
    seen = [[False] * w for _ in range(h)]
    for r in range(h):
        for c in range(w):
            if seed[r][c]:
                seen[r][c] = True
                q.append((r, c))
    if not q:
        return idx, 0

    changed = 0
    while q:
        r, c = q.popleft()
        v = grid[r][c]
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            rr, cc = r + dr, c + dc
            if rr < 0 or rr >= h or cc < 0 or cc >= w or seen[rr][cc]:
                continue
            seen[rr][cc] = True
            if grid[rr][cc] != v:
                grid[rr][cc] = v
                changed += 1
            q.append((rr, cc))
    return grid, changed
