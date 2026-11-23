# -*- coding: utf-8 -*-
"""目标图颜色量化（v1.7.3）—— CIELAB K-Means + 孤立点清理。

【这个模块解决什么】
投影/抖动之前，每一格的目标色是**连续**的（一张照片降采样后有上千种不同
颜色）。连续目标 → 相邻格的权重差一点点 → 抖动阈值一翻，就跳到不同配方，
颗粒因此"抖"。把目标色先聚成 K 个簇，相邻格落进同一个簇 → 权重完全相同 →
局部排布变干净，同时"整图出现过的堆叠编码种类"变少。

【做法来源与改动】
思路取自 Lumina Studio（GPL-3.0，`core/image_processing.py` 的
`_process_high_fidelity_mode`：K-Means++ 量化到 N 色 → 映射回原图 → 中值去斑）。
我们改了**两处**，都有实测理由：

1. **在 CIELAB 里聚类，不是 RGB**。Lumina 直接对 uint8 RGB 跑 cv2.kmeans；
   而 RGB 距离里绿通道和亮度权重过大，"肉眼看着一样"的两色可能被分到两簇，
   "肉眼差很多"的两色可能被并成一簇。聚类是用眼睛验收的，就该在感知均匀空间做。
2. **量化对象是我们的**输出网格**，不是原图**。Lumina 先量化高分辨率原图再降到
   目标分辨率；我们直接量化目标网格 —— 少一次缩放、快得多，而且量化的正是
   "最终要打出来的那一格"，不引入额外的重采样误差。

【确定性】
固定种子 + 先按唯一色合并计数，同一张图重复生成结果逐位一致（全项目约定）。

【不 import bpy】纯算法模块，可离线跑（Blender 自带 numpy；本机 managed Python 没有）。
"""

try:
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None

import random
import time


# ---------------------------------------------------------------------------
# 色空间转换（D65 白点，与 sRGB 标准一致）
# ---------------------------------------------------------------------------
_WHITE_X = 0.95047
_WHITE_Y = 1.00000
_WHITE_Z = 1.08883

# sRGB 0..1（已 gamma 编码）-> linear
def srgb_to_linear(a):
    a = _np.asarray(a, dtype=_np.float64)
    low = a / 12.92
    high = _np.power((a + 0.055) / 1.055, 2.4)
    return _np.where(a <= 0.04045, low, high)


def linear_to_srgb(a):
    a = _np.asarray(a, dtype=_np.float64)
    low = a * 12.92
    high = 1.055 * _np.power(_np.maximum(a, 1e-12), 1.0 / 2.4) - 0.055
    return _np.where(a <= 0.0031308, low, high)


def _lin_to_xyz(lin):
    r, g, b = lin[..., 0], lin[..., 1], lin[..., 2]
    x = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b
    return _np.stack([x, y, z], axis=-1)


def _xyz_to_lin(xyz):
    x, y, z = xyz[..., 0], xyz[..., 1], xyz[..., 2]
    r = 3.2404542 * x - 1.5371385 * y - 0.4985314 * z
    g = -0.9692660 * x + 1.8760108 * y + 0.0415560 * z
    b = 0.0556434 * x - 0.2040259 * y + 1.0572252 * z
    return _np.stack([r, g, b], axis=-1)


def _f_lab(t):
    d = 6.0 / 29.0
    return _np.where(t > d ** 3, _np.cbrt(t), t / (3.0 * d * d) + 4.0 / 29.0)


def _f_lab_inv(t):
    d = 6.0 / 29.0
    return _np.where(t > d, t ** 3, 3.0 * d * d * (t - 4.0 / 29.0))


def srgb_to_lab(rgb01):
    """sRGB 0..1 -> CIELAB。输入 (..., 3)，输出同形（L 0..100, a/b 约 ±128）。"""
    lin = srgb_to_linear(rgb01)
    xyz = _lin_to_xyz(lin) / _np.array([_WHITE_X, _WHITE_Y, _WHITE_Z])
    f = _f_lab(xyz)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return _np.stack([L, a, b], axis=-1)


def lab_to_srgb(lab):
    """CIELAB -> sRGB 0..1（超出色域的部分被裁到 [0,1]）。"""
    L = lab[..., 0]
    a = lab[..., 1]
    b = lab[..., 2]
    fy = (L + 16.0) / 116.0
    fx = fy + a / 500.0
    fz = fy - b / 200.0
    xyz = _np.stack([_f_lab_inv(fx), _f_lab_inv(fy), _f_lab_inv(fz)], axis=-1)
    xyz = xyz * _np.array([_WHITE_X, _WHITE_Y, _WHITE_Z])
    return _np.clip(linear_to_srgb(_xyz_to_lin(xyz)), 0.0, 1.0)


def delta_e76(lab1, lab2):
    """CIE76 色差（与项目其余地方的口径一致）。"""
    d = _np.asarray(lab1, dtype=_np.float64) - _np.asarray(lab2, dtype=_np.float64)
    return _np.sqrt(_np.sum(d * d, axis=-1))


# ---------------------------------------------------------------------------
# 网格 <-> 数组
# ---------------------------------------------------------------------------
def grid_to_array(grid):
    """``grid[row][col] = (r,g,b)`` -> float64 ndarray (h, w, 3)，取值 0..1。"""
    return _np.array(grid, dtype=_np.float64)


def array_to_grid(arr):
    h, w = arr.shape[0], arr.shape[1]
    return [[(float(arr[y, x, 0]), float(arr[y, x, 1]), float(arr[y, x, 2]))
             for x in range(w)] for y in range(h)]


def _pack_codes(arr):
    """(N,3) 0..1 -> (N,) int32 的 24 位色码，用于去重。"""
    q = _np.clip(_np.rint(arr * 255.0), 0, 255).astype(_np.int32)
    return (q[:, 0] << 16) | (q[:, 1] << 8) | q[:, 2]


# ---------------------------------------------------------------------------
# 加权 K-Means++（在 Lab 空间）
# ---------------------------------------------------------------------------
def _kmeans_plus_plus(lab, weights, k, rng):
    """加权 K-Means++ 初始化，返回 k 个中心在 lab 里的下标。"""
    n = lab.shape[0]
    first = rng.randrange(n)
    centers = [first]
    if k <= 1:
        return centers
    # 到最近中心的**加权**平方距离（权重 = 该唯一色出现次数）
    d2 = delta_e76(lab, lab[first]) ** 2 * weights
    for _ in range(k - 1):
        total = float(d2.sum())
        if total <= 1e-12:
            # 剩下的唯一色全都与已有中心重合（同一颜色），随便挑一个未用的
            rest = [i for i in range(n) if i not in centers]
            if not rest:
                break
            centers.append(rest[0])
            continue
        target = rng.random() * total
        acc = 0.0
        pick = n - 1
        for i in range(n):
            acc += float(d2[i])
            if acc >= target:
                pick = i
                break
        if pick in centers:
            # 极端浮点情形：退化为取最大的那个
            pick = int(_np.argmax(d2))
            if pick in centers:
                rest = [i for i in range(n) if i not in centers]
                if not rest:
                    break
                pick = rest[0]
        centers.append(pick)
        nd = delta_e76(lab, lab[pick]) ** 2 * weights
        d2 = _np.minimum(d2, nd)
    return centers


def _assign(lab, centers_lab):
    """返回每个唯一色到中心的最近距离与下标（逐块算，省内存）。"""
    n = lab.shape[0]
    k = len(centers_lab)
    idx = _np.zeros(n, dtype=_np.int32)
    dist = _np.zeros(n, dtype=_np.float64)
    block = max(1, int(2_000_000 // max(1, k)))
    for s in range(0, n, block):
        e = min(n, s + block)
        d = _np.sqrt(_np.maximum(
            ((lab[s:e, None, :] - centers_lab[None, :, :]) ** 2).sum(axis=-1), 0.0))
        j = _np.argmin(d, axis=1)
        idx[s:e] = j
        dist[s:e] = d[_np.arange(e - s), j]
    return idx, dist


def _kmeans(lab, weights, k, seed, max_iter=40, tol=0.20):
    """加权 Lloyd 迭代。返回 (centers_lab, label_of_unique, dE_of_unique)。"""
    rng = random.Random(int(seed))
    picks = _kmeans_plus_plus(lab, weights, k, rng)
    centers = lab[_np.array(picks, dtype=_np.int64)].copy()

    label = None
    prev = centers.copy()
    for _ in range(max_iter):
        label, dist = _assign(lab, centers)
        # 加权均值（空簇保留原位，避免抖动）
        new = centers.copy()
        for j in range(centers.shape[0]):
            m = label == j
            wsum = float(weights[m].sum())
            if wsum <= 0.0:
                continue
            new[j] = (lab[m] * weights[m, None]).sum(axis=0) / wsum
        shift = float(_np.max(delta_e76(new, prev))) if len(prev) else 0.0
        centers = new
        if shift <= tol:
            break
        prev = centers.copy()

    label, dist = _assign(lab, centers)
    return centers, label, dist


# ---------------------------------------------------------------------------
# 孤立点清理（多数投票）
# ---------------------------------------------------------------------------
_NB8 = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


def cleanup_isolated(labels, h, w, strict=True):
    """把"8 邻域全与自己不同"的孤立格替换成邻域众数。

    判据直接取自 Lumina 的 ``core/isolated_pixel_cleanup.py``：**八邻域全不同**
    才替换。这个判据自带保色性 —— 一条 1 格宽的细线，沿线方向的邻居与自己同类，
    不满足"全不同"，所以**不会**被抹掉；只有真正的孤立噪点会被清掉。

    ``strict=False`` 时放宽成"邻域里自己是少数"，会明显更狠，默认不用。

    实现：先用 8 次整块比较**批量筛出候选格**（`same == 0`），再只对候选格跑
    Python 循环。候选通常只占千分之几，因此 400x400 网格也在毫秒级。

    返回 ``(新 labels, 被替换的格数)``。
    """
    lab = _np.asarray(labels)
    if lab.shape[0] != h or lab.shape[1] != w:
        raise ValueError("labels 形状与 h/w 不符")
    if h < 3 or w < 3:
        return lab.copy(), 0

    # ---- 1. 整块统计"邻居里有几个和自己同色" ----
    same = _np.zeros((h, w), dtype=_np.int32)
    cnt = _np.zeros((h, w), dtype=_np.int32)          # 实际存在的邻居个数
    for (dy, dx) in _NB8:
        # 把 lab 沿 (dy,dx) 平移后与自身比较，取交集区域
        y0a, y1a = max(0, -dy), min(h, h - dy)
        x0a, x1a = max(0, -dx), min(w, w - dx)
        if y0a >= y1a or x0a >= x1a:
            continue
        a = lab[y0a:y1a, x0a:x1a]
        b = lab[y0a + dy:y1a + dy, x0a + dx:x1a + dx]
        same[y0a:y1a, x0a:x1a] += (a == b)
        cnt[y0a:y1a, x0a:x1a] += 1

    cand = _np.argwhere((same == 0) & (cnt > 0) if strict else (same * 2 < cnt))

    # ---- 2. 只对候选格求邻域众数 ----
    out = lab.copy()
    changed = 0
    for y, x in cand:
        y, x = int(y), int(x)
        vals = []
        for dy, dx in _NB8:
            yy, xx = y + dy, x + dx
            if 0 <= yy < h and 0 <= xx < w:
                vals.append(int(lab[yy, xx]))
        if not vals:
            continue
        best, best_n = vals[0], 0
        for v in sorted(set(vals)):            # 排序 → 并列时取编号小的
            c = vals.count(v)
            if c > best_n:
                best, best_n = v, c
        if best != int(lab[y, x]):
            out[y, x] = best
            changed += 1
    return out, changed


# ---------------------------------------------------------------------------
# 只清孤立点、不做量化（v1.7.4）
# ---------------------------------------------------------------------------
# 去斑判据用的"参考归并档位"：把目标色聚成这么多个等价类，再判哪个格孤立。
# 为什么必须归并、不能直接比颜色 —— 见 despeckle_grid 的 docstring（实测教训）。
#
# ⚠ 数值必须与 constants.DEFAULT_QUANTIZE_COLORS 一致：generation.prepare_grid
# 会**显式传**那个常量，这里的默认值只服务"离线脚本直接调用本模块"的场景。
# t_probe_v174 有一条断言把两者锁在一起，改一处忘了另一处会立刻报红。
DESPECKLE_REF_K = 16


def _cluster_indices(grid, k, seed):
    """把网格目标色聚成 k 簇，返回逐格簇编号（只求"标签"，不管代表色）。

    去斑路径专用：拿标签判孤立点，颜色一个都不改。
    ``k_eff`` 的算法与 ``quantize_grid`` 保持一致（``min(k, 唯一色数)``），
    这样"量化开去斑"与"只去斑"两条路径的判据是**同一个等价关系**，
    命中的格子集合应当逐格相同（t_probe_v174 有交叉断言）。

    返回 ``(labels(h,w) int32, n_uniq, k_eff)``。
    """
    h, w = len(grid), len(grid[0])
    arr = grid_to_array(grid)
    codes = _pack_codes(arr.reshape(-1, 3))
    uniq_codes, inverse, counts = _np.unique(codes, return_inverse=True,
                                             return_counts=True)
    n_uniq = int(uniq_codes.shape[0])
    if n_uniq <= 1:
        return _np.zeros((h, w), dtype=_np.int32), n_uniq, 0

    k_eff = min(max(2, int(k)), n_uniq)
    uq = _np.stack([(uniq_codes >> 16) & 255, (uniq_codes >> 8) & 255,
                    uniq_codes & 255], axis=1).astype(_np.float64) / 255.0
    uq_lab = srgb_to_lab(uq)
    _, label, _ = _kmeans(uq_lab, counts.astype(_np.float64), k_eff, seed)
    return label[inverse].reshape(h, w).astype(_np.int32), n_uniq, k_eff


def despeckle_grid(grid, k_ref=DESPECKLE_REF_K, seed=20260919,
                   max_isolated_ratio=0.20):
    """清掉孤立点，但**不做**颜色量化（v1.7.4）。

    与 ``quantize_grid(..., despeckle=True)`` 的区别只在"替换成什么颜色"：
    那条路径会把**每一格**的颜色换成簇心色（引入原图没有的颜色、有量化损失），
    这条**只动被判孤立的那些格**，且换成邻居里已经出现过的颜色 → 严格保色。

    【为什么不能直接按颜色判"是不是同类"】v1.7.4 第一版想省掉聚类，改成把
    sRGB 颜色立方体按容差 tol 分桶当等价类。实测（``tests/diag_despeckle.py``，
    照片 120×90、4055 种颜色）**直接否掉了这条路**：tol 从 0 一路放到 12，
    替换率始终在 10.8%~46.3%，而量化路径只有 1.88%；更糟的是高频能量保留率
    高达 146%~175%（不降反升）—— 被"替换"的格远多于真孤立点，换进去的邻域色
    反而制造出新的高频跳变。根因：照片是**连续色**，任何固定分桶都做不出
    "邻域同类"，于是"八邻域全不同"这个判据对几乎每一格都成立。
    → 判据必须先经过一次真正的**颜色归并**（K-Means 到 ``k_ref`` 簇）才有意义。
    归并只用来**取标签**，颜色一个都不改，所以不引入量化损失。

    【可信度护栏】``max_isolated_ratio``：算完先看命中**占比**，超过它就认为
    判据在这张图上不成立、原样返回。这是**直接针对失效模式**的守卫 ——
    同一张照片，K-Means 归并后命中 1.88%（通过），被否掉的"颜色分桶"方案是
    31%~46%（拦下）。比"唯一色数够不够"之类的启发式可靠得多。

    【保守性】判据仍是"8 邻域全不同"，1 格宽的细线沿线有同类邻居，不会被抹掉。

    返回 ``(new_grid, stats)``，stats 含 ``changed`` / ``skipped`` / ``reason``。
    """
    stats = {"changed": 0, "skipped": True, "k_ref": int(k_ref),
             "unique_in": 0, "k_used": 0, "reason": ""}
    if _np is None:
        raise RuntimeError("孤立点清理需要 numpy（Blender 自带）")
    if not grid or not grid[0]:
        stats["reason"] = "空网格"
        return grid, stats

    h, w = len(grid), len(grid[0])
    if h < 3 or w < 3:
        stats["reason"] = "网格太小"
        return grid, stats

    labels, n_uniq, k_eff = _cluster_indices(grid, k_ref, seed)
    stats["unique_in"] = n_uniq
    stats["k_used"] = k_eff
    if k_eff <= 0:
        stats["reason"] = "网格只有一种颜色"
        return grid, stats

    arr = grid_to_array(grid)
    codes = _pack_codes(arr.reshape(-1, 3))
    new_labels, _ = cleanup_isolated(labels, h, w, strict=True)
    diff = _np.argwhere(new_labels != labels)
    if diff.shape[0] == 0:
        stats["skipped"] = False
        stats["reason"] = "没有孤立点"
        return grid, stats

    ratio = diff.shape[0] / float(h * w)
    if ratio > float(max_isolated_ratio):
        stats["reason"] = ("命中占比 %.1f%% 超过 %.0f%%，判据在此图不可信，已跳过"
                           % (ratio * 100.0, float(max_isolated_ratio) * 100.0))
        return grid, stats

    # 只改被判定孤立的格子，其余格保留**原始浮点值**。
    # ⚠ 早先的写法是把整张网格经 8bit 色码往返一次（out = codes 再还原），
    # 结果**未被命中的格子也被悄悄量化到 1/255 台阶上**（实测构造网格里
    # 0.22 → 0.219607843，144 格里 141 格被动过）—— 用户明明关掉了颜色量化，
    # 颜色却还是被改了。面积平均采样出来的就是连续浮点值，这种"无声降精度"
    # 尤其不该发生。t_probe_v174 有一条"非孤立格一格没动"的断言钉住它。
    out = arr.copy()
    changed = 0
    for y, x in diff:
        y, x = int(y), int(x)
        want = int(new_labels[y, x])
        vals = []
        for dy, dx in _NB8:
            yy, xx = y + dy, x + dx
            if 0 <= yy < h and 0 <= xx < w and int(labels[yy, xx]) == want:
                vals.append(int(codes[yy * w + xx]))
        if not vals:
            continue
        # 众数簇里再挑"出现最多的具体颜色"；sorted 保证并列时取编号小的（确定性）
        best, best_n = vals[0], 0
        for v in sorted(set(vals)):
            c = vals.count(v)
            if c > best_n:
                best, best_n = v, c
        if best == int(codes[y * w + x]):
            continue
        # 取该色码在图中**真实出现过**的一格的浮点颜色 —— 这样输出里出现的每一种
        # 颜色，原图里都有一个逐位相同的格子（浮点级保色，不只是 8bit 色码级）。
        pos = int(_np.argmax(codes == best))
        out[y, x] = arr[pos // w, pos % w]
        changed += 1

    stats["changed"] = int(changed)
    stats["skipped"] = False
    if changed == 0:
        return grid, stats
    return array_to_grid(out), stats


# v1.48.0 / R65：这里原本有 32 行旧实现（基于 codes/labels 的第二代去斑），
# 落在上面那条 return 之后 —— AST 扫描确认是整个仓库唯一一处不可达代码。
# 风险不是"多跑一遍"，而是**改错地方**：将来有人调去斑逻辑，很可能改到这段
# 死代码上，跑测试发现没变化。已删除。


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def quantize_grid(grid, k, despeckle=False, seed=20260919):
    """把网格的目标色聚成 ``k`` 个簇。

    ``k <= 0`` 或唯一色本来就 ≤ k 时**原样返回**（零成本短路）。

    返回 ``(new_grid, stats)``；``stats`` 里的 ``dE_mean`` / ``dE_max`` 是
    "量化前后逐格 CIE76 色差"，即量化引入的**目标色误差**（不是最终还原误差 ——
    最终误差还要过投影与抖动，见 tests/diag_quant.py 的表）。
    """
    if _np is None:
        raise RuntimeError("颜色量化需要 numpy（Blender 自带）")
    if not grid or not grid[0]:
        return grid, {"k_requested": int(k), "k_used": 0, "skipped": True}

    h, w = len(grid), len(grid[0])
    t0 = time.time()
    arr = grid_to_array(grid)
    flat = arr.reshape(-1, 3)
    codes = _pack_codes(flat)

    uniq_codes, inverse, counts = _np.unique(codes, return_inverse=True,
                                             return_counts=True)
    n_uniq = int(uniq_codes.shape[0])

    want_k = int(k)
    stats = {
        "k_requested": want_k,
        "unique_in": n_uniq,
        "skipped": True,
        "despeckled": 0,
    }
    if want_k <= 0 or n_uniq <= want_k:
        # 唯一色已经不多，量化没有意义（也避免了空簇/退化）
        stats["k_used"] = n_uniq
        stats["dE_mean"] = 0.0
        stats["dE_max"] = 0.0
        stats["seconds"] = time.time() - t0
        return grid, stats

    # 唯一色的代表性 sRGB（用色码还原，和原像素一致到 1/255）
    uq = _np.stack([(uniq_codes >> 16) & 255, (uniq_codes >> 8) & 255,
                    uniq_codes & 255], axis=1).astype(_np.float64) / 255.0
    uq_lab = srgb_to_lab(uq)

    k_eff = min(want_k, n_uniq)
    centers, label, _ = _kmeans(uq_lab, counts.astype(_np.float64), k_eff, seed)
    # 真正被用到的簇（可能少于 k_eff）
    used = _np.unique(label)
    k_used = int(used.shape[0])

    # 簇代表色 = 簇心的 Lab 转回 sRGB（与 Lumina 用簇心一致）
    rep_lab = centers[used]
    rep_srgb = lab_to_srgb(rep_lab)
    remap = _np.zeros(label.shape[0], dtype=_np.int32)
    for pos, j in enumerate(used):
        remap[label == j] = pos

    # 逐格色差（量化损失）
    d_uniq = delta_e76(uq_lab, rep_lab[remap])
    d_pix = d_uniq[inverse]
    stats["dE_mean"] = float(d_pix.mean())
    stats["dE_max"] = float(d_pix.max())

    # 孤立点清理在"簇编号"上做（等价于在堆叠编码上做，因为编码只由簇决定）
    if despeckle:
        lab_img = remap[inverse].reshape(h, w)
        lab_img, n_changed = cleanup_isolated(lab_img, h, w, strict=True)
        stats["despeckled"] = int(n_changed)
        remap_pix = lab_img.reshape(-1)
    else:
        remap_pix = remap[inverse]

    out_arr = rep_srgb[remap_pix].reshape(h, w, 3)
    # 去斑可能把某些小簇整簇抹掉 → 实际留在图里的簇数要重新数（否则面板与
    # 耗时诊断会高估簇数）
    k_final = int(_np.unique(remap_pix).shape[0])
    stats.update({
        "k_used": k_final,
        "k_used_before_despeckle": k_used,
        "unique_out": k_final,
        "skipped": False,
        "seconds": time.time() - t0,
    })
    return array_to_grid(out_arr), stats
