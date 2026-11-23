# -*- coding: utf-8 -*-
"""图片读取与降采样。

依赖 bpy（仅本模块的 read_image_grid / ensure_image）。

【v1.47.0 / R64 更正一处历史误判】旧注释写「Blender 中 image.pixels 返回的
是 linear 浮点值」，那只对 **float buffer 图** 成立。本机 Blender 5.1.2 实测
（2026-11-07，``tests/out/_diag_pixel_space.py``，手写 8-bit PNG (128,64,64)）：

* 8-bit byte 图（JPG/PNG，``is_float=False`` / ``depth=24``）的 ``pixels``
  恒为 **byte/255 的 sRGB 编码值** —— 读到 (0.502, 0.251, 0.251)，
  把 colorspace 设成 sRGB / Non-Color 再 ``reload()`` 三种读数完全相同；
* float buffer 图（``is_float=True`` / ``depth=96``，默认 ``Linear Rec.709``）
  的 ``pixels`` 才是 scene-linear。

颜色分析（色相分类、减法混色）必须在 sRGB 感知空间里做，所以 **只有 float
图需要 linear→sRGB**。旧实现无条件转换，等于给所有 8-bit 照片多做一次 gamma，
输入色被整体提亮洗淡：R64 实测麻将原图桌面 (103,69,51) 被洗成 (169,141,122)，
中性灰褐在 CMYK 实测锚点里最近的是灰蓝 CMY —— 这就是「桌面红棕打成灰蓝」的根因。
"""

import os

try:
    import numpy as _np
except Exception:  # pragma: no cover - Blender 一定带 numpy，离线脚本可能没有
    _np = None


# ---------------------------------------------------------------------------
# sRGB / linear 转换
# ---------------------------------------------------------------------------
def _lin_to_srgb_array(a):
    """linear ndarray -> sRGB ndarray（向量化）。"""
    if _np is None:
        return a
    low = a * 12.92
    high = 1.055 * _np.power(_np.maximum(a, 1e-8), 1.0 / 2.4) - 0.055
    return _np.where(a <= 0.0031308, low, high)


def is_srgb_colorspace(image):
    """保留给外部/探针查询色彩空间标注。

    ⚠ v1.47.0 起**不再**用它决定是否做 linear→sRGB：8-bit byte 图即使标着
    sRGB，``pixels`` 也已经是编码值；真正决定要不要转的是 ``is_float``。
    """
    cs = getattr(image, "colorspace_settings", None)
    if cs is None:
        return True
    return str(getattr(cs, "name", "sRGB")).upper() == "SRGB"


def needs_lin_to_srgb(image):
    """该图的 ``pixels`` 是否 scene-linear（需要转回 sRGB 才能做颜色分析）。

    判据只有一条：**float buffer**。Blender 5.1.2 实测依据见模块 docstring。
    8-bit byte 图一律 False —— 无条件转就是 R64 那个「双重 gamma」bug。

    对外公开是为了让生成链路与探针共用同一判据，不各写一份。
    """
    return bool(getattr(image, "is_float", False))


# ---------------------------------------------------------------------------
# 图片对象获取
# ---------------------------------------------------------------------------
def ensure_image(image, reload=False):
    """确保 bpy Image 的像素数据已加载并可用。"""
    if image is None:
        raise ValueError("没有指定图片")
    if image.size[0] <= 0 or image.size[1] <= 0:
        try:
            image.reload()
        except Exception:
            pass
    if image.size[0] <= 0 or image.size[1] <= 0:
        # 打包进 .blend 或仅在磁盘上的图片
        if image.filepath:
            path = image.filepath
            if not os.path.isabs(path) and getattr(image, "filepath_raw", ""):
                path = image.filepath_raw
            path = os.path.abspath(path)
            if os.path.isfile(path):
                image.filepath = path
                image.reload()
    if image.size[0] <= 0 or image.size[1] <= 0:
        raise ValueError("图片没有有效像素，请确认文件路径正确：%s" % (image.filepath or image.name))
    return image


def read_image_grid(image, out_w, out_h, to_srgb=True, bg_color=(1.0, 1.0, 1.0),
                    sampling="area"):
    """把 bpy Image 降采样成 ``out_h`` 行 × ``out_w`` 列 的 sRGB 网格。

    返回 ``(grid, out_w, out_h)``，其中 ``grid[row][col] = (r, g, b)``，
    均为 sRGB ``0..1``；**row 0 对应图像顶部**（即常规看图顺序）。

    ``to_srgb``：是否允许把 scene-linear 像素转回 sRGB（默认 True）。
    **v1.47.0 起只按图像类型自动判定**：8-bit byte 图的 pixels 本来就是
    sRGB 编码值，不转；只有 float buffer 图才转。传 False 则一律不转
    （给需要原始线性数据的调用方/诊断脚本用）。两条链路
    （``_prepare_grid`` 主图、``engrave_depth_grid`` 明信片反面）都传 True，
    因此口径自动一致，不会出现「正面修了反面没修」。

    ``sampling``（v1.7.3）决定"一个格子怎么代表它盖住的那块源像素"：

    * ``"area"``（默认）—— **面积平均**。一块 0.42mm 的柱子盖住源图里几十个
      像素，取平均就是"这一小块整体看上去的颜色"，接近人眼在观看距离上的
      空间积分。照片、插画都用它。
    * ``"nearest"`` —— **最近邻**（取格心那一个源像素）。这条是照 Lumina Studio
      的做法加的（其 `image_processing.py` 对两种模式**都**强制 NEAREST，理由是
      抗锯齿会在边缘造出浅色过渡像素，浅色映射到"白色底座堆叠"会让网格浮起来）。
      对我们而言那个具体理由不成立（我们的底板是连续实体，不存在浮空），但它在
      **线稿 / 像素画**上确实有独立价值：面积平均会把 1 像素宽的细黑线冲淡成灰，
      而灰在我们的色板里离黑很远；最近邻能把原色硬保下来。
      ⚠ **照片不要用它** —— 源图分辨率远高于柱子数，最近邻等于无过滤点采样，
      摩尔纹和锯齿会直接变成一根根错色的柱子。
    """
    image = ensure_image(image)
    src_w, src_h = int(image.size[0]), int(image.size[1])
    ch = int(image.channels)

    if _np is None:
        raise RuntimeError("本插件需要 numpy（Blender 自带），当前环境缺失")

    buf = _np.empty(src_w * src_h * ch, dtype=_np.float32)
    image.pixels.foreach_get(buf)
    arr = buf.reshape(src_h, src_w, ch)
    # Blender 像素第 0 行在最下方，翻转成"顶部优先"
    arr = arr[::-1, :, :]

    rgb = arr[:, :, 0:3].astype(_np.float32)
    if to_srgb and needs_lin_to_srgb(image):
        rgb = _lin_to_srgb_array(rgb)
    rgb = _np.clip(rgb, 0.0, 1.0)

    # alpha 合成到背景
    if ch >= 4:
        alpha = _np.clip(arr[:, :, 3:4].astype(_np.float32), 0.0, 1.0)
        if float(alpha.min()) < 0.999:
            bg = _np.array(bg_color, dtype=_np.float32).reshape(1, 1, 3)
            rgb = rgb * alpha + bg * (1.0 - alpha)

    if sampling == "nearest":
        grid_arr = _resize_nearest(rgb, out_h, out_w)
    else:
        grid_arr = _resize_block_mean(rgb, out_h, out_w)
    grid = [
        [tuple(float(v) for v in grid_arr[y, x]) for x in range(out_w)]
        for y in range(out_h)
    ]
    return grid, out_w, out_h


def _resize_nearest(rgb, out_h, out_w):
    """最近邻降采样：每个格子取格心落在的那个源像素（v1.7.3）。

    与 ``_resize_block_mean`` 共用同一套"区间等分"口径（格心 = 区间中点），
    这样两种采样方式在几何上盖住的是同一块区域，只是"怎么代表它"不同。
    """
    src_h, src_w = rgb.shape[0], rgb.shape[1]
    ys = _np.clip(((_np.arange(out_h) + 0.5) * src_h / out_h).astype(_np.int64),
                  0, src_h - 1)
    xs = _np.clip(((_np.arange(out_w) + 0.5) * src_w / out_w).astype(_np.int64),
                  0, src_w - 1)
    return rgb[ys][:, xs]


def _resize_block_mean(rgb, out_h, out_w):
    """块平均降采样（自带抗锯齿，避免色阶采样产生摩尔纹）。

    v1.37.1：改用前缀和一次算完（两次 reduceat 等价），把 43,200 次 numpy
    调用（每次几个元素，开销全在 Python 侧）压成几次大数组运算，1.4 s → 0.05 s。
    与旧版逐块 mean 数学上完全等价（块宽 = max(1, 界差)，与旧版 y1<=y0 时的
    兜底一致）；用 float64 累加避免大图下的精度/溢出问题。
    """
    if _np is None:  # 离线无 numpy 时退回旧逻辑
        return _resize_block_mean_slow(rgb, out_h, out_w)
    src_h, src_w = rgb.shape[0], rgb.shape[1]
    ys = _np.linspace(0, src_h, out_h + 1).astype(_np.int64)
    xs = _np.linspace(0, src_w, out_w + 1).astype(_np.int64)
    ys[-1] = src_h
    xs[-1] = src_w
    wy = _np.maximum(1, _np.diff(ys))   # 每块行数（兜底 1，对应旧版 y1<=y0）
    wx = _np.maximum(1, _np.diff(xs))   # 每块列数
    row_a = ys[:-1]
    row_b = ys[:-1] + wy                # 末块 row_b <= src_h，P0 多留一行不会越界
    # 行前缀和：P0[k] = rgb[0:k] 的累加（k 取 0..src_h），float64
    P0 = _np.concatenate(
        (_np.zeros((1, src_w, 3), dtype=_np.float64),
         _np.cumsum(rgb, axis=0).astype(_np.float64)), axis=0)
    block_rows = P0[row_b] - P0[row_a]  # (out_h, src_w, 3)
    col_a = xs[:-1]
    col_b = xs[:-1] + wx
    P1 = _np.concatenate(
        (_np.zeros((out_h, 1, 3), dtype=_np.float64),
         _np.cumsum(block_rows, axis=1).astype(_np.float64)), axis=1)
    block = P1[:, col_b, :] - P1[:, col_a, :]  # (out_h, out_w, 3)
    counts = _np.outer(wy, wx).astype(_np.float64)
    return (block / counts[:, :, _np.newaxis]).astype(_np.float32)


def _resize_block_mean_slow(rgb, out_h, out_w):
    """numpy 缺失时的纯 Python 兜底（与旧版一致）。"""
    src_h, src_w = rgb.shape[0], rgb.shape[1]
    ys = _np.linspace(0, src_h, out_h + 1)
    xs = _np.linspace(0, src_w, out_w + 1)
    ys[-1] = src_h
    xs[-1] = src_w
    import math as _m
    out = _np.empty((out_h, out_w, 3), dtype=_np.float32)
    for j in range(out_h):
        y0, y1 = int(_m.floor(ys[j])), int(_m.floor(ys[j + 1]))
        if y1 <= y0:
            y1 = min(src_h, y0 + 1)
        strip = rgb[y0:y1, :, :]
        for i in range(out_w):
            x0, x1 = int(_m.floor(xs[i])), int(_m.floor(xs[i + 1]))
            if x1 <= x0:
                x1 = min(src_w, x0 + 1)
            out[j, i] = strip[:, x0:x1, :].reshape(-1, 3).mean(axis=0)
    return out


# ---------------------------------------------------------------------------
# 从磁盘加载图片
# ---------------------------------------------------------------------------
def load_image_from_path(path, name=None):
    """把磁盘图片加载进 bpy.data.images（已存在则复用）。"""
    import bpy

    if not path or not os.path.isfile(path):
        raise ValueError("找不到图片文件：%s" % path)

    base = name or os.path.basename(path)
    existing = bpy.data.images.get(base)
    if existing is not None:
        if os.path.normcase(os.path.abspath(existing.filepath or "")) == \
           os.path.normcase(os.path.abspath(path)) and existing.size[0] > 0:
            return existing
        try:
            bpy.data.images.remove(existing)
        except Exception:
            pass

    img = bpy.data.images.load(path, check_existing=True)
    if name and img.name != name:
        img.name = name
    return img
