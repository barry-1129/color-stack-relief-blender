# -*- coding: utf-8 -*-
"""生成流程编排：图片 → 配色 → 几何 → 材质 → 场景对象 → 导出。

本模块依赖 bpy。纯算法都在 color_engine / image_analysis / mesh_builder 里，
便于离线单测。
"""

import math
import os
import time

import bmesh
import bpy

try:
    import numpy as _np
except Exception:  # pragma: no cover - Blender 一定带 numpy
    _np = None

from .. import constants as C
from .. import progress as PG
from . import color_engine as CE
from . import exporters as EX
from . import image_analysis as IA
from . import mesh_builder as MB
from . import mosaic as MO
from . import quantize as QZ
from . import stack_match as SM


# ---------------------------------------------------------------------------
# 材质
# ---------------------------------------------------------------------------
def get_or_create_material(key, srgb, prefix="CSTACK_", store=None):
    """按颜色键创建/复用 Principled 材质，视口显示色也一并设好。"""
    name = "%s%s" % (prefix, key)
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = None
    for n in nt.nodes:
        if n.type == "BSDF_PRINCIPLED":
            bsdf = n
            break
    lin = tuple(C.srgb_to_linear(v) for v in srgb)
    if bsdf is not None:
        bc = bsdf.inputs.get("Base Color")
        if bc is not None:
            bc.default_value = (lin[0], lin[1], lin[2], 1.0)
        rough = bsdf.inputs.get("Roughness")
        if rough is not None:
            rough.default_value = 0.65
        metal = bsdf.inputs.get("Metallic")
        if metal is not None:
            metal.default_value = 0.0
    mat.diffuse_color = (lin[0], lin[1], lin[2], 1.0)
    mat.roughness = 0.65
    if store is not None:
        store[name] = mat
    return mat


def build_material_list(order_keys, palette=None, base_key="W", prefix="CSTACK_"):
    """返回 ``(materials, key_to_index, meta)``。

    同色只建一个材质，避免在切片软件里出现重复的耗材槽。
    """
    unique = []
    for k in list(order_keys) + [base_key]:
        if k not in unique:
            unique.append(k)

    pal = CE.resolve_palette(unique, palette)
    materials = []
    key_to_index = {}
    meta = []
    for i, k in enumerate(unique):
        srgb = pal[k]
        cn = C.BASE_COLORS.get(k, {}).get("cn", k)
        materials.append(get_or_create_material(k, srgb, prefix=prefix))
        key_to_index[k] = i
        meta.append({"key": k, "name": cn, "srgb": srgb})
    return materials, key_to_index, meta


def dither_block_for(w, h, size_x, size_y, nozzle=None):
    """块状抖动的块边长（R18；v1.34.0 改为「至少一条挤出线」）—— 预览与生成**必须共用**本函数。

    【为什么】抖动斑点 = 1 像素。像素尺寸小于喷嘴线宽时（75mm 卡 / 313px
    = 0.24mm < 0.42mm 线宽），斑点在切片器的小特征过滤下被并回主导色，
    彩色整个消失（实测 2026-09-30，25mm 卡对照实验：solid 四色全出、
    dither 彩色全灭）。

    【v1.34.0 更正：块宽 = 1× 线宽，不再是 2×】
    R18 当时取的 2× 来自"1 像素 = 0.1mm"的极端场景（25mm 卡 / 250px），
    那种斑点比线宽细 4 倍，当然被过滤。R40 实拍复查（100mm、600 精细度、
    兼容模式）发现 2× 反而制造了新的糊：块边长 6 格 = **1.0mm**，等于
    长边只有 100 个色点，精细度从 128 拉到 600 也一点没变细 —— 用户看到
    的就是"太糊、看不清内容"，同时还付了 270,000 根柱子 / 378 万面的代价。

    物理下限只有一条：**色点至少占一条挤出线宽**（0.4 喷嘴 = 0.42mm）。
    一条线宽的方块正好是一段可打印的挤出路径，切片器不会把它当小特征丢掉。
    所以块边长取 ``ceil(线宽 / 像素)``，像素本来就 ≥ 线宽时返回 1。
    实测（同一张实拍图、100mm、600 精细度、兼容模式、极致档）：
    色点平均宽 1.12mm → **0.67mm**，相邻异色边 15.0% → 24.2%。

    返回值同时写进生成参数与预览链路（``operators._dither_kw``），
    两边口径漂移 = "预览一套、打印另一套"。
    """
    px = min(size_x / float(w), size_y / float(h))
    return C.dither_block_at(px, nozzle=nozzle)



def build_relief_stacker(order, palette, prof, anchor_mode=None,
                         dither_block=1):
    """柱内叠色的配色器 —— **唯一构造处**，预览与实际生成共用。

    ⚠ 预览与生成必须走同一份构造，否则会分叉：预览图看到的黑/绿/蓝/红是真的
    会打出来，还是"预览一套、打印另一套"。同理见 operators._dither_kw。
    所以面板与算子都不许自己 ``CE.CardStacker(...)``，一律走这里。

    基底固定为 ``C.RELIEF_SUBSTRATE_KEY``（白）—— 滤光叠色模型的透射率标定物
    就是"白底板 + 4 层色片"（``_STACK_LAM``），基底不是白会把整块板染成底色调。
    单面/双面叠色卡同样把基底固定为白。

    ``prof`` 是质量等级档位（``weights/_dither_kw`` 的来源），键缺失即取默认值，
    这样预览（只有 prof）与生成（有完整 params）不会因为取值口径不同而漂移。

    ``anchor_mode``（R7 重定义）经 ``C.ANCHOR_MODES`` 分派三档。**只有
    relief 与 card（镜像分支）走锚点链路**（v1.22.0 起 mosaic 是照片聚类
    直出，不建 CardStacker、不做凸包投影，故与此处无关）：
      * ``basic`` 基础（8 色）：子集模型（legacy 路径，逐位兼容 v1.7.9）；
      * ``stack`` 扩增（白底叠色 64 色）：定长 3 层网格、白基底固定（R7-①）；
      * ``mix``   柱内混色（自由底 256 色）：定长 4 层网格、基底位四色放开
        （R7-②），混色走「最近 32 锚点预选 + 精确投影」（near_k）。
    层序搜索只在多集路径有意义（构造器内有 guard：order_perm → 强制 allow_repeat）。
    """
    am = C.ANCHOR_MODES.get(anchor_mode or C.DEFAULT_ANCHOR_MODE,
                           C.ANCHOR_MODES[C.DEFAULT_ANCHOR_MODE])
    near_k = int(am.get("near_k", 0))
    # R8：质量档的 near_k 是**上限** —— 只在锚点档本身启用 kNN 时收窄
    # （基础 8 锚点 / 双面镜像 near_k=0，全量枚举本就最小，恒不受影响）。
    _qk = int(prof.get("near_k", 0) or 0)
    if near_k and _qk:
        near_k = min(near_k, _qk)
    _mc = int(prof.get("max_components", 0) or 0)
    if am.get("kind") == "grid":
        return CE.CardStacker(
            order, palette,
            grid_depth=int(am["grid_depth"]),
            base_key=C.RELIEF_SUBSTRATE_KEY,
            weight_levels=float(prof.get("weight_levels", 0.0)),
            merge_below=float(prof.get("merge_below", 0.0)),
            dither_size=int(prof.get("dither", CE.DEFAULT_DITHER_SIZE)),
            white_snap=float(prof.get("white_snap", 1.01)),
            mirror=False,
            dither_pattern=prof.get("dither_pattern"),
            near_k=near_k, max_components=_mc,
            dither_block=dither_block)
    md = int(am["max_depth"])
    allow_repeat = bool(am.get("allow_repeat", False))
    order_perm = bool(am.get("order_perm", False))
    return CE.CardStacker(
        order, palette,
        max_depth=md,
        allow_repeat=allow_repeat,
        order_perm=order_perm,
        base_key=C.RELIEF_SUBSTRATE_KEY,
        weight_levels=float(prof.get("weight_levels", 0.0)),
        merge_below=float(prof.get("merge_below", 0.0)),
        dither_size=int(prof.get("dither", CE.DEFAULT_DITHER_SIZE)),
        white_snap=float(prof.get("white_snap", 1.01)),
        mirror=False,
        dither_pattern=prof.get("dither_pattern"),
        max_components=_mc,
        dither_block=dither_block)


def build_card_stacker(keys, palette, prof, bands, base_key="W",
                       mirror=False, anchor_mode=None, dither_block=1,
                       white_outer=False):
    """单面/双面叠色卡的配色器构造 —— **预览与生成共用**（R7 抽取）。

    与 ``build_relief_stacker`` 同理：面板/算子不许自己 ``CE.CardStacker(...)``
    —— v1.9.0 的单面预览就是漏传 ``anchor_mode`` 的分叉：预览永远 8 锚点、
    生成却按所选档位，"所见"与"所打"不一致。
    """
    am = C.ANCHOR_MODES.get(anchor_mode or C.DEFAULT_ANCHOR_MODE,
                           C.ANCHOR_MODES[C.DEFAULT_ANCHOR_MODE])
    allow_repeat = bool(am.get("allow_repeat", False)) and (not mirror)
    order_perm = bool(am.get("order_perm", False)) and allow_repeat
    grid_depth = int(am["grid_depth"]) if (am.get("kind") == "grid"
                                           and not mirror) else None
    if grid_depth and grid_depth > int(bands):
        # 叠色层数不足以放下定长堆叠时按层数收敛（expand 只保留前 bands 层）
        grid_depth = int(bands)
    near_k = int(am.get("near_k", 0)) if not mirror else 0
    # R8：质量档 near_k 上限只在锚点档启用 kNN 时收窄（0 = 无上限）
    _qk = int(prof.get("near_k", 0) or 0)
    if near_k and _qk:
        near_k = min(near_k, _qk)
    # v1.29.0：镜像链路也放开「每像素最多混几种」—— 否则三档质量只剩权重
    # 量化这一点差异，极致/均衡实测逐像素一模一样（用户直接看出"看着一样"）。
    _mc = int(prof.get("max_components", 0) or 0)
    return CE.CardStacker(
        keys, palette, max_depth=int(bands), base_key=base_key,
        weight_levels=float(prof.get("weight_levels", 0.0)),
        merge_below=float(prof.get("merge_below", 0.0)),
        dither_size=int(prof.get("dither", CE.DEFAULT_DITHER_SIZE)),
        white_snap=float(prof.get("white_snap", 1.01)),
        mirror=mirror, dither_pattern=prof.get("dither_pattern"),
        allow_repeat=allow_repeat, order_perm=order_perm,
        grid_depth=grid_depth, near_k=near_k, max_components=_mc,
        dither_block=dither_block, white_outer=white_outer)


# ---------------------------------------------------------------------------
# 高度场 -> 色带级别
# ---------------------------------------------------------------------------
def build_stack_matcher(keys, palette, prof, bands, grid=None, w=0, h=0,
                        base_key="W", mirror=True, pixel_mm=1.0, progress=None,
                        block_px=None, below=(), base_first=False, pad_to=0,
                        white_outer=False):
    """「整块上色」的配色器 —— 逐像素最近堆叠 + 大像素块 + 小区域合并（v1.30.0）。

    **预览与生成唯一构造处**（与 ``build_card_stacker`` 同一纪律）：预览若自己
    另建一个 matcher，合并阈值或像素尺寸算错一处，预览里的色块在实物上就不存在。

    ``grid`` 给出时立即整图规划（匹配 + 合并），``pick`` 只是查表；不给时
    ``pick`` 退回逐次最近匹配（探针/单色调用）。
    """
    m = SM.StackMatcher(keys, palette, bands, base_key=base_key, mirror=mirror,
                        below=below, base_first=base_first, pad_to=pad_to,
                        white_outer=white_outer)
    if grid is not None:
        _blk = (prof.get("block_px", 1) if block_px is None
                else block_px)
        m.prepare(grid, w, h,
                  min_feature_mm=float(prof.get("min_feature_mm",
                                                C.MIN_PRINT_FEATURE_MM)),
                  pixel_mm=float(pixel_mm or 1.0),
                  block_px=int(_blk or 1),
                  max_stacks=int(prof.get("max_stacks", 0) or 0),
                  min_width_mm=float(prof.get("min_width_mm",
                                              C.MIN_PRINT_WIDTH_MM)),
                  progress=progress)
    return m


DISPLAY_COLOR_ATTR = "StackColor"


def apply_display_colors(obj, stack_grid, palette=None, mirror=True,
                         attr_name=DISPLAY_COLOR_ATTR,
                         solid_below_z=None, solid_rgb=None,
                         frame_rect=None, frame_rgb=None, stack_below=(),
                         stack_bands=4, white_outer=False):
    """把「打出来的颜色」写成**顶点色**，供视口直接查看（v1.31.0 / R38）。

    【为什么需要】模型的材质是按**耗材**建的（``build_material_list`` 每个
    颜色键一个材质）—— 这是切片软件分配四卷料所必需的。代价是视口里看到
    永远是一张"白/青/品红/黄"的四色拼贴，**看不到叠色后的真实呈色**。
    用户 2026-10-26 截图正是这个：两个质量档的模型看着一样，因为视口显示
    的本来就不是成品的颜色。

    本函数**只加一层顶点色**，不动材质、不动 ``material_index``：
      * 导出的分色/水密/体积完全不受影响（OBJ/3MF 不写顶点色）；
      * 视口切到 ``Solid + 顶点色`` 就能看到和预览图、和实物一致的顶面颜色。

    颜色按面中心落在哪个网格取 —— 与 ``mesh_builder.build_stack_card``
    的行列方向严格对齐（row 0 在最上方，col 0 在最左）。

    【v1.45.0 / R56：分体面必须单独给色】浮雕明信片的反面是一块**单色**背板，
    它和正面共用同一个 bm、同一个 XY 网格。若照旧"按 XY 格子取色"，背板上
    每一格都会被涂成正面对应格的叠色 —— 视口里反面就整片印着正面的照片
    （用户 2026-11-04 截图：翻到底面还是那张图）。所以这里加了
    ``solid_below_z``：中心 z **不高于**该平面的面（= 反面板）统一涂
    ``solid_rgb``（该板耗材自己的颜色，线性空间），只有正面才逐格取色。

    返回写入的面数（0 = 没写，例如网格为空）。
    """
    me = getattr(obj, "data", None)
    if me is None or not stack_grid:
        return 0
    if not me.polygons:
        return 0
    h = len(stack_grid)
    w = len(stack_grid[0]) if h else 0
    if w <= 0:
        return 0

    keys = []
    for line in stack_grid:
        for st in line:
            for k in st:
                if k not in keys:
                    keys.append(k)
    # 【不要把色板裁到"只含实际用到的键"】v1.44.0 / R55。
    # 呈色模型不足 4 层时用 "W" 补位，而 `_reflect_st` 靠**色板键集**判断是否
    # 走 RYBW 专属表（`set(palette) <= {R,Y,B,W}`）。纯白格子是**空叠**，
    # 键集里根本没有 W —— 一个 {"Y"} 色板正好是 RYBW 的子集，于是白膜被当成
    # RYBW 的强散射白，整片算成纯白（r38 C3/C5 实测 5504 面全错）。
    # 修法：把调用方给的整组色板并回来（生产调用都传整组），再最少补一个 W。
    keys_full = list(keys)
    for k in (palette or ()):
        if k not in keys_full:
            keys_full.append(k)
    if "W" not in keys_full:
        keys_full.append("W")
    pal = CE.resolve_palette(keys_full, palette)

    # 每格的颜色（线性空间，顶点色按线性存储）
    # v1.40.0：白点中性化必须与锚点/预览同一口径，否则视口又比实物绿
    _gain = CE.white_neutral_gain(keys_full, pal)
    cell_rgb = {}
    for r in range(h):
        for c in range(w):
            st = tuple(stack_grid[r][c] or ())
            cell_rgb[(r, c)] = _stack_linear(st, pal, mirror, _gain,
                                             stack_below,
                                             bands=stack_bands,
                                             keep_order=white_outer)

    verts = me.vertices
    if frame_rect is not None:
        # v1.45.1 / R57：明信片四周有一圈**外扩**边框，bbox 已经不等于画面
        # 区域了。若仍按 bbox 折算格子，整张图会被拉宽 3mm、逐格错位。
        x_lo, x_hi, y_lo, y_hi = [float(v) for v in frame_rect]
    else:
        xs = [v.co.x for v in verts]
        ys = [v.co.y for v in verts]
        x_lo, x_hi = min(xs), max(xs)
        y_lo, y_hi = min(ys), max(ys)
    dx = (x_hi - x_lo) / float(w)
    dy = (y_hi - y_lo) / float(h)
    if dx <= 0.0 or dy <= 0.0:
        return 0

    attr = me.color_attributes.get(attr_name)
    if attr is None:
        attr = me.color_attributes.new(name=attr_name, type="FLOAT_COLOR",
                                       domain="CORNER")
    data = attr.data

    written = 0
    cache = {}
    # 分体面（明信片反面板 / 四周外框）统一色：一次算好，逐面直接写
    _solid = None
    if solid_below_z is not None:
        _srgb = tuple(solid_rgb or (1.0, 1.0, 1.0))[:3]
        _solid = (_srgb[0], _srgb[1], _srgb[2], 1.0)
    _frame = None
    if frame_rect is not None and frame_rgb is not None:
        _frgb = tuple(frame_rgb)[:3]
        _frame = (_frgb[0], _frgb[1], _frgb[2], 1.0)
    for poly in me.polygons:
        if _solid is not None and poly.center.z <= float(solid_below_z) + 1e-6:
            for li in poly.loop_indices:
                data[li].color = _solid
            written += 1
            continue
        # ``poly.center`` 是 Blender 算好的面心，比在 Python 里遍历 loop 顶点快
        # 一个量级（300 格的浮雕有几十万面，逐顶点求和会拖到十几秒）。
        cx = poly.center.x
        cy = poly.center.y
        if _frame is not None and (cx < x_lo - 1e-6 or cx > x_hi + 1e-6
                                   or cy < y_lo - 1e-6 or cy > y_hi + 1e-6):
            # 画面以外 = 外扩边框，整圈涂底板色
            for li in poly.loop_indices:
                data[li].color = _frame
            written += 1
            continue
        col = int((cx - x_lo) / dx)
        row = h - 1 - int((cy - y_lo) / dy)
        col = 0 if col < 0 else (w - 1 if col >= w else col)
        row = 0 if row < 0 else (h - 1 if row >= h else row)
        rgb = cell_rgb.get((row, col))
        if rgb is None:
            continue
        rgba = cache.get((row, col))
        if rgba is None:
            rgba = (rgb[0], rgb[1], rgb[2], 1.0)
            cache[(row, col)] = rgba
        for li in poly.loop_indices:
            data[li].color = rgba
        written += 1

    try:
        me.color_attributes.active_color = attr
    except Exception:
        pass
    try:
        idx = me.color_attributes.find(attr_name)
        if idx >= 0:
            me.color_attributes.active_color_index = idx
            me.color_attributes.render_color_index = idx
    except Exception:
        pass
    return written


def _stack_linear(st, palette, mirror, gain=None, below=(), bands=4,
                  keep_order=False):
    """堆叠 → 线性空间呈现色（顶点色用）。

    ``gain`` = ``CE.white_neutral_gain(palette)`` 的结果（v1.40.0 白点中性化）。
    调用方在循环外算一次传进来 —— 这里逐格调用，不能每次都重算色板签名。
    ``below`` = 彩膜下方的固定层（单面叠色卡的 4 层白）。
    ``bands`` / ``keep_order`` 只在镜像链路有意义：v1.0.6 起双面卡每面 6 层，
    且堆叠里白可以排在外侧，必须与锚点构造同一口径，否则视口颜色与预览分叉。
    """
    if mirror:
        srgb = CE.stack_present_mirror(st, palette, gain, bands=bands,
                                       keep_order=keep_order)
    else:
        srgb = CE.stack_present(st, palette, gain, below)
    return (C.srgb_to_linear(srgb[0]), C.srgb_to_linear(srgb[1]),
            C.srgb_to_linear(srgb[2]))


def show_true_colors(context=None):
    """把 3D 视口切到「Solid + 顶点色」并把视图变换设为 Standard。

    为什么连视图变换一起改：Blender 默认 AgX 会压暗高饱和色，顶点色显示
    出来会比预览图/实物灰一档 —— 那就等于又给了用户一个错的参考。

    后台模式（无 screen）直接返回 0，不报错。
    """
    ctx = context or bpy.context
    n = 0
    screen = getattr(ctx, "screen", None)
    if screen is not None:
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type != "VIEW_3D":
                    continue
                sh = space.shading
                try:
                    sh.type = "SOLID"
                    sh.color_type = "VERTEX"
                    n += 1
                except Exception:
                    pass
    try:
        vs = ctx.scene.view_settings
        items = [i.identifier for i in
                 vs.bl_rna.properties["view_transform"].enum_items]
        if "Standard" in items:
            vs.view_transform = "Standard"
    except Exception:
        pass
    return n


def compute_levels(grid, w, h, order, stack, height_mode="coloraware",
                   brightness_weight=1.0, invert=False, gamma=1.0,
                   progress=None):
    """把每个像素映射成色带下标。"""
    levels = []
    for row in range(h):
        line = []
        gline = grid[row]
        for col in range(w):
            hv = CE.map_height(gline[col], order, mode=height_mode,
                               brightness_weight=brightness_weight,
                               invert=invert, gamma=gamma)
            line.append(CE.height_to_layer_index(hv, stack))
        levels.append(line)
        if progress is not None and (row % 16 == 0):
            progress(row / float(max(1, h)))

    # 消除"两柱沿棱相切"的高度组合，否则浮雕会产生非流形棱
    fixed = MB.resolve_manifold_conflicts(levels)
    if fixed:
        print("[叠色浮雕工坊] 已消解 %d 处非流形棱冲突" % fixed)
    return levels


def surface_quality_params(prof):
    """表面混色（mix_model=surface）下的质量档参数适配（R21，v1.19.1）。

    「像素」档的 ``dither=0`` + ``max_components=1`` 是为**透光锚点**设计的：
    锚点集里有黑/绿/红等深色复合锚，「每像素取最近 1 锚、不抖动」仍能表达
    暗部。表面混色的可达域只有 4 张膜 —— 这套参数在 surface 下退化成
    「最近膜查找」：暗部整片摊给同一张膜（用户照片实测：Y 膜占比 0%、
    view_dRGB 128 vs 均衡档 65，满屏粉/青大色块，2026-10-03）。
    所以 surface 下保底有序抖动、放开单分量限制；权重粗量化
    （weight_levels / merge_below）保留 —— 它只让混色组合更少、画面更平整，
    不会锁死色相。
    """
    p = dict(prof)
    if int(p.get("dither", 0) or 0) <= 0:
        p["dither"] = CE.DEFAULT_DITHER_SIZE
    if int(p.get("max_components", 0) or 0) == 1:
        p["max_components"] = 0
    return p


def build_surface_mixer(keys, palette, prof, dither_block=1):
    """表面混色（R19）的配色器构造 —— **预览与生成共用**。

    【物理依据】不透明耗材（PLA Basic 等）顶面只见最上层色膜，层下膜片
    全被盖住 —— 透光叠色模型（stack_present 系）承诺的棕/绿/深色不会出现
    （实测 2026-09-30：官方预览与原图几乎一致，实物顶面却满屏饱和 CMY 色块）。

    ⚠ v1.24.0（R29）更正：上面这条"物理依据"的真因**已查明一半**——
    当时 100% 的像素柱在 4 片色膜之上还压着 0.6~1.7mm 的**同一种颜色**
    （``_stacked_wall_mat`` 把 seq 贴在底板、余高填最外层色），光当然透不到
    叠层。修成「色片贴柱顶」后，同一张照片的顶面呈现色 dE 中位 40.10→8.91。
    所以"透光叠色在 PLA Basic 上不成立"这句话**不成立于几何 bug**；本函数
    仍然是「不想赌耗材透光」时的安全档，不再是唯一正确档。
    表面混色改用与浮雕分区配色同一引擎（凸包投影 + 抖动）：色点空间平均
    =「印报纸」半色调，预览即实物顶面。
    """
    sp = surface_quality_params(prof)
    return CE.Mixer(keys, palette,
                    weight_levels=float(sp.get("weight_levels", 0.0)),
                    merge_below=float(sp.get("merge_below", 0.0)),
                    dither_size=int(sp.get("dither", CE.DEFAULT_DITHER_SIZE)),
                    white_snap=float(sp.get("white_snap", 1.01)),
                    dither_pattern=sp.get("dither_pattern"),
                    max_components=int(sp.get("max_components", 0) or 0),
                    dither_block=dither_block)


def compute_card_plan(grid, w, h, keys, bands, palette=None,
                      weight_levels=0.0, merge_below=0.0,
                      dither_size=8, white_snap=1.01, base_key="W",
                      mirror=False, progress=None, dither_pattern=None,
                      anchor_mode=None, near_k_cap=0, max_components=0,
                      dither_block=1, mix_model="translucent",
                      color_match=None, min_feature_mm=0.0, pixel_mm=1.0,
                      block_px=1, max_stacks=0, min_width_mm=0.0, below=(),
                      base_first=False, pad_to=0, white_outer=False):
    """色深叠色卡：逐像素决定「外表面往里的颜色堆叠」。

    返回 ``(stacker, stack_grid)``：``stack_grid[row][col]`` 是颜色键元组
    （外→内），长度 0..bands；空元组 = 纯白（双面模式=白×8，普通模式=
    只有底板，不叠色片）。

    混色机制见 ``CE.CardStacker``：滤光锚点的凸包投影 + Bayer 有序抖动。
    ``bands`` 同时是搜索深度上限（能叠出的最深滤光组合）与每面层数。
    ``mirror=True`` 时为**双面镜像八层**结构（v1.5）：四色可重复的多集
    堆叠，呈现色 = 序列乘积的平方（参考 3MF 的叠色方案，2026-09 解码）。

    ``anchor_mode``（R7 重定义）经 ``C.ANCHOR_MODES`` 分派：单面（mirror=False）
    真正受控（基础 8 / 扩增 64 / 自由底 256）；双面（mirror=True）走
    ``mirror_anchors``（自带多集枚举），下拉仅作展示、不改变双面的锚点集。
    """
    am = C.ANCHOR_MODES.get(anchor_mode or C.DEFAULT_ANCHOR_MODE,
                           C.ANCHOR_MODES[C.DEFAULT_ANCHOR_MODE])
    # 双面镜像分支强制走 mirror_anchors（自带多集），allow_repeat 在此被忽略。
    allow_repeat = bool(am.get("allow_repeat", False)) and (not mirror)
    order_perm = bool(am.get("order_perm", False)) and allow_repeat
    grid_depth = int(am["grid_depth"]) if (am.get("kind") == "grid"
                                           and not mirror) else None
    if grid_depth and grid_depth > int(bands):
        # 「叠色层数」不足以放下定长堆叠时按层数收敛（与旧 max_depth=bands
        # 口径一致）；展开时每个半程只取 bands 层，放着不管会静默截断。
        grid_depth = int(bands)
    near_k = int(am.get("near_k", 0)) if not mirror else 0
    # R8：质量档 near_k 上限只在锚点档启用 kNN 时收窄（0 = 无上限）
    if near_k and near_k_cap:
        near_k = min(near_k, int(near_k_cap))
    # R19：表面混色（不透明耗材）—— 不走滤光锚点，直接凸包投影 + 块抖动
    # 选**单色膜**；堆叠表示仍用元组（每面 bands 层全部同色），几何展开
    # 与水密逻辑零改动。
    if mix_model == "surface":
        mixer = build_surface_mixer(keys, palette, dict(
            weight_levels=weight_levels, merge_below=merge_below,
            dither=dither_size, white_snap=white_snap,
            dither_pattern=dither_pattern, max_components=max_components),
            dither_block=dither_block)
        stack_grid = []
        for row in range(h):
            gline = grid[row]
            line = []
            for col in range(w):
                k = mixer.pick(gline[col], row=row, col=col)
                line.append((k,) * max(1, int(bands)))
            stack_grid.append(line)
            if progress is not None and (row % 16 == 0):
                progress(row / float(max(1, h)))
        return mixer, stack_grid

    # v1.30.0（R37）：整块上色 —— 一像素一叠法，不跨像素抖动。
    # 抖动的物理前提（眼睛把相邻像素平均）在 0.4mm 喷嘴上不成立：实拍里每个
    # 色点都被溢料包了一圈，远看是彩噪。这里改为 Lumina Studio 的做法：
    # 最近堆叠 + 小区域合并。见 core/stack_match.py 顶部说明。
    #
    # ⚠ ``color_match=None`` 保留**旧行为**（CardStacker 抖动）：历史脚本 /
    # 旧探针直接调本函数时没有这个参数，语义不能悄悄变。面板路径永远显式传值
    # （``props.as_params`` 有 color_match，默认 solid）。
    if str(color_match or "") == "solid":
        matcher = build_stack_matcher(
            keys, palette,
            {"min_feature_mm": float(min_feature_mm or 0.0),
             "block_px": int(block_px or 1),
             "max_stacks": int(max_stacks or 0),
             "min_width_mm": float(min_width_mm or 0.0)},
            bands, grid=grid, w=w, h=h, base_key=base_key, mirror=mirror,
            pixel_mm=pixel_mm, progress=progress, below=below,
            base_first=base_first, pad_to=pad_to, white_outer=white_outer)
        return matcher, matcher._picks
    stacker = CE.CardStacker(keys, palette, max_depth=bands, base_key=base_key,
                             weight_levels=weight_levels,
                             merge_below=merge_below,
                             dither_size=dither_size, white_snap=white_snap,
                             mirror=mirror, dither_pattern=dither_pattern,
                             allow_repeat=allow_repeat, order_perm=order_perm,
                             grid_depth=grid_depth, near_k=near_k,
                             max_components=max_components,
                             dither_block=dither_block, below=below,
                             white_outer=white_outer)
    stack_grid = []
    for row in range(h):
        gline = grid[row]
        line = []
        for col in range(w):
            line.append(stacker.pick(gline[col], row=row, col=col))
        stack_grid.append(line)
        if progress is not None and (row % 16 == 0):
            progress(row / float(max(1, h)))
    return stacker, stack_grid


def expand_mirror_stack(stack_grid, w, h, bands, key_to_mat):
    """双面镜像：多集堆叠 → 2×bands 个子层的材质索引网格。

    每根柱子的正面（外→内）= 堆叠按色轮序排列、不足 bands 层用白垫足；
    整柱（顶→底）= 正面 + 正面的逆序（镜像回文）。返回
    ``band_mats[i][row][col]``（i=0 是最外/顶层），共 2×bands 层。
    """
    n = 2 * int(bands)
    out = []
    for _ in range(n):
        out.append([[0] * w for _ in range(h)])
    for row in range(h):
        grow = stack_grid[row]
        for col in range(w):
            # 不再强制 canonical_key_order：pick 返回的顺序（含 mix 档层序搜索
            # 的排列优化）就是几何该用的顺序——重排会让实际打印色与建模呈现色
            # 分叉（C 在顶的 C+M=蓝灰 vs M 在顶=紫红）。双面镜像的锚点本就按
            # 色轮序构造，这里去规范化是 no-op。
            st = list(grow[col])
            front = list(st[:bands])
            if len(front) < bands:
                # v1.0.6：不足层数补的白放**最外**，与 mirror_anchors 的
                # white_outer 同一口径。补在内侧时白永远挡不住颜色，
                # 皮肤这类低饱和色会被甩到高饱和壳上（发紫发黑）。
                front = ["W"] * (bands - len(front)) + front
            full = front + front[::-1]          # 顶→底，回文
            for i in range(n):
                out[i][row][col] = key_to_mat[full[i]]
    return out


def expand_single_stack(stack_grid, w, h, bands, key_to_mat, white_layers=4):
    """单面叠色：正面 bands 层彩膜 + 下方 ``white_layers`` 层纯白。

    【为什么不是镜像】双面卡的背面 4 层是正面颜色的镜像 —— 那一半的散射要
    再穿过正面 4 层才到观察者眼里，颜色被自己蒙了一层灰，而且反面也会印上图。
    单面结构把 8 个子层的预算全部给"观察者这一侧 + 一层白隔离垫"：
    顶面更纯、反面是纯白，正是用户要的形态。

    返回 ``band_mats[i][row][col]``（i=0 最外/顶层），共 ``bands + white_layers`` 层。
    """
    n = int(bands) + int(white_layers)
    out = []
    for _ in range(n):
        out.append([[0] * w for _ in range(h)])
    white_mat = key_to_mat.get("W", 0)
    for row in range(h):
        grow = stack_grid[row]
        for col in range(w):
            st = list(grow[col])[:bands]
            while len(st) < bands:
                st.append("W")
            full = st + ["W"] * int(white_layers)
            for i in range(n):
                out[i][row][col] = key_to_mat.get(full[i], white_mat)
    return out


def interlock_core_bands(grids, skin, pixel_mm, nozzle_mm=None):
    """把芯层的色界逐层错开，做出砌砖式的「错缝咬合」。

    【为什么换掉填白】R43 把芯层填成整片基底色，确实断掉了贯穿缝，但代价是：
    侧面一圈 0.4mm 白带（立面一半是白）、白料占一半、每块图案只剩 0.2mm 薄皮。
    用户实拍判断"稍微增加了强度，但仍有较大风险"，并给出画法：在明显的颜色
    分层处让某几层交叉叠过去 —— 这就是砌墙的错缝。

    【怎么错】皮（最外 ``skin`` 层）一格不动，保证顶面/底面外观逐格不变；
    芯层按厚度分成两段，前一段朝一个对角平移、后一段朝相反对角平移 —— 整条
    色界在板厚中间处错一个台阶，就像砌墙的丁砖，Z 向直缝被这一步台阶打断。

    【为什么只做两段、而不是每层各错一个方向】每多一段就多一层内部分界面，
    三角数跟着涨。实测四层各错一向：贯穿 13.4%，但三角 591,440→872,306
    （+47%）；两段错位只会产生 3 层内界面。

    【错开量必须跟着线宽走】错得比一条挤出线宽还细，切片器会把它当噪声并回去，
    等于没咬合（R40/R42 的教训：所有"最小可打印尺寸"都按喷嘴线宽缩放，写死
    毫米会让 0.2 与 0.4 喷嘴输出逐格相同）。

    就地改 ``grids`` 的芯层，返回统计字典。
    """
    n = len(grids)
    h = len(grids[0])
    w = len(grids[0][0])
    stat = {"step_px": 0, "step_mm": 0.0, "changed": 0, "layers": 0,
            "core_px": 0, "steps": 0}
    if skin <= 0 or n <= 2 * skin:
        return stat

    lw = float(C.nozzle_width(nozzle_mm))
    # 容差 0.2 格：0.417mm 的格 vs 0.42mm 的线宽是"一样粗"，不该让 ceil 把
    # 错台从 1 格抬到 2 格（那样错台宽度平白翻倍、面数也翻倍）。
    step = max(1, int(math.ceil(lw / max(1e-9, float(pixel_mm)) - 0.2)))

    # 基准 = 皮的最内层（图案原样）。芯层全部从这一张图派生，避免"层 2 平移完
    # 再拿层 2 当基准"造成逐层累积漂移。
    src = grids[skin - 1]
    core = list(range(skin, n - skin))
    half = (len(core) + 1) // 2
    # 两段各朝对角、方向相反：竖缝被 +x 段打断，横缝被 +y 段打断，中间那一处
    # 两段之间错开 2×step，是整条缝最硬的一处"丁砖"。
    shifts = ((step, step), (-step, -step))

    for idx, i in enumerate(core):
        dx, dy = shifts[0] if idx < half else shifts[1]
        g = [[0] * w for _ in range(h)]
        for r in range(h):
            sr = r - dy
            if sr < 0:
                sr = 0
            elif sr >= h:
                sr = h - 1
            srow = src[sr]
            grow = g[r]
            for c in range(w):
                sc = c - dx
                if sc < 0:
                    sc = 0
                elif sc >= w:
                    sc = w - 1
                grow[c] = srow[sc]
        grids[i] = g

    # 统计"最外层芯层相对图案挪动了多少格"，用来报"改动 N 格"
    changed = 0
    for r in range(h):
        for c in range(w):
            if (grids[skin][r][c] != src[r][c]
                    or grids[-skin - 1][r][c] != src[r][c]):
                changed += 1
    stat["step_px"] = step
    stat["step_mm"] = step * float(pixel_mm)
    stat["changed"] = changed
    stat["layers"] = len(core)
    stat["core_px"] = h * w
    stat["steps"] = 2 if len(core) > 1 else 1
    return stat


def estimate_card_swaps(band_mats, base_mat):
    """色深叠色卡的换色估算：每层统计出现过的耗材数。

    每片色层正好是一层切片（band_mm = 层高时），AMS 在该层内要把出现过的
    颜色各走一遍，换色 = (颜色数 - 1)，逐层累加。返回 (总次数, 单层最多)。
    """
    total = 0
    worst = 0
    for cells in band_mats:
        used = set()
        for line in cells:
            used.update(line)
        total += max(0, len(used) - 1)
        worst = max(worst, len(used))
    return (total, worst)


def stack_is_blank(st, base_key="W"):
    """这条叠法是否"一片彩膜都没用"（= 纯白 / 空叠）。

    【为什么需要这个函数（v1.0.7）】镜像链路补白到每面层数后，空叠的表示
    **不再**是空元组 `()`，而是 `("W",) * bands`（白也是要被打印的膜）。
    任何"白色留白"的语义判断都不能再写 `if not st` —— 明信片的白色下沉
    就是因此静默失效（r54 实测 0/0 格，白色区不再低 0.2mm）。
    """
    return (not st) or all(k == base_key for k in st)


def _copy_faces(src, dst, keep):
    """把 ``src`` 中 ``keep(face)`` 为真的面复制到 ``dst``（顶点在 dst 内去重）。

    v1.0.14：底板 / 色膜拆对象的底层操作。项目里的几何本来就是"每种颜色
    一个独立封闭壳"（见 mesh_builder.build_stack_card / build_relief），
    所以按面筛选复制出来的每个壳仍然闭合 —— 不会因为拆对象产生破洞。
    """
    vmap = {}
    for f in src.faces:
        if not keep(f):
            continue
        vs = []
        for v in f.verts:
            nv = vmap.get(v)
            if nv is None:
                nv = dst.verts.new(v.co)
                vmap[v] = nv
            vs.append(nv)
        try:
            nf = dst.faces.new(vs)
        except ValueError:
            # 重复面（同顶点序列）在逐色独立壳里不会出现，防御性跳过。
            continue
        nf.material_index = int(f.material_index)
        nf.smooth = bool(f.smooth)
    dst.normal_update()


def _shell_split_sets(bm, base_idx, split_z):
    """按「独立封闭壳」把面分成 (底板面集, 色膜面集)。

    【为什么不能逐面按 z 判】外框是一圈从 z=0 贯通到模型最高处的环，逐面
    判定会把它的侧壁中段判给色膜、底面判给底板 —— 两边各留 4 条开放边
    （实测拆分后两个对象 boundary=8）。项目几何本来就是**每色一个独立
    封闭壳**，所以先求面连通分量、按整壳归属：
      * 材质 = 底板色 且 壳顶 ≤ 底板顶面        → 底板；
      * 材质 = 底板色 且 壳从 z=0 贯通到最高处  → 外框，也归底板；
      * 其余（含正面白色膜柱）                  → 色膜。
    """
    z_all = [v.co.z for v in bm.verts]
    z_top = max(z_all) if z_all else 0.0
    z_bot = min(z_all) if z_all else 0.0
    seen = set()
    base_faces = set()
    for f0 in bm.faces:
        if f0 in seen:
            continue
        stack = [f0]
        seen.add(f0)
        comp = []
        while stack:
            g = stack.pop()
            comp.append(g)
            for e in g.edges:
                for h in e.link_faces:
                    if h not in seen:
                        seen.add(h)
                        stack.append(h)
        if int(comp[0].material_index) != int(base_idx):
            continue
        zs = [v.co.z for f in comp for v in f.verts]
        zlo, zhi = (min(zs), max(zs)) if zs else (z_bot, z_bot)
        is_slab = zhi <= split_z + 1e-4
        is_rim = (zlo <= z_bot + 1e-4) and (zhi >= z_top - 1e-4) \
            and (z_top - z_bot > split_z + 1e-4)
        if is_slab or is_rim:
            base_faces.update(comp)
    film_faces = set(f for f in bm.faces if f not in base_faces)
    return base_faces, film_faces


def compute_region(grid, w, h, order, base_mm, relief_mm,
                   brightness_weight=1.0, invert=False, gamma=1.0,
                   quant_mm=C.REGION_QUANT_MM, progress=None,
                   palette=None, weight_levels=0.0, merge_below=0.0,
                   dither_size=CE.DEFAULT_DITHER_SIZE, white_snap=1.01,
                   flat=False, dither_pattern=None,
                   stacker=None, layer_mm=0.0, empty_key="W",
                   max_components=0, dither_block=1):
    """分区配色：逐像素算出「目标色层厚度」与「颜色键 / 柱内色片堆叠」。

    这是「同一层可以不同颜色」「同一根柱子可以是多色叠加」的落地 —— 材质由
    像素颜色在色板凸包上的投影权重决定，不再由高度决定，所以同一个 Z 高度上
    可以出现不同颜色，换色发生在 XY 平面内。

    返回 ``(z_grid, key_grid, stack_grid)``：顶面绝对 Z 高度、顶面颜色键、
    柱内色片堆叠（``stack_grid`` 为 None 表示走单色路径）。

    ``stacker``（v1.7.9 柱内叠色）给出时改用**滤光锚点**配色：每个像素得到的
    不是单一颜色，而是一叠色片（外→内，最多 ``RELIEF_STACK_DEPTH`` 片）。
    ⚠ R20（v1.19.0）：滤光锚点的"呈现色"按**透射积**预测（C+Y=绿、M+Y=红棕
    …）——这只在半透明耗材上成立。不透明耗材顶面只露最上层膜，所以默认的
    表面混色下调用方应传 ``stacker=None``：每像素由 ``mixer`` 凸包投影直接
    选**单张膜色** + 块抖动（预览即实物顶面）。``stacker=None`` 时
    ``stack_grid`` 返回 None，几何走非叠色分支。
    柱高取 ``max(层数 × layer_mm, 明暗厚度)``：
      * 一方面保证叠层放得下（否则色片会被柱顶截断，颜色就丢了）；
      * 另一方面明暗厚度仍在，浮雕的起伏感不被抹平。

    ``empty_key`` 是"该像素一片色层都不用"时顶面用的材质键。**必须存在于
    调用方的 ``key_to_mat``**：柱内叠色的基底恒为
    ``constants.RELIEF_SUBSTRATE_KEY``（白，见该常量的说明），所以传它即可。
    写死字面量会在"白色被自动挑色丢掉"时直接 KeyError。

    【为什么必须叠层】旧的单色柱只有一个不透明色，从正上方看就是那一种耗材
    的颜色，色彩表达完全靠抖动做**空间加色平均**；而 {W,C,M,Y} 四个色板色的
    凸包最暗处 ≈ 中灰紫（亮度仅约 0.5），照片里所有亮度 < 0.5 的像素都会被
    抬亮并染上紫调（实测黑→紫、深绿→蓝灰、棕发→粉紫）。改用滤光锚点后，
    C+M+Y 叠印 = 黑、C+Y = 绿、C+M = 蓝、M+Y = 红都在可达域内 —— 实测同一张
    照片远看 CIE76 色差中位由 9.78 降到 6.01，暗部（亮度<0.4）由 48.5/28.8
    降到 36.5/18.4。证据：tests/t_diag_relief_color.py。

    高度按 ``quant_mm`` 量化：一是贴合切片层高，二是把高度收敛到有限集合，
    显著减少"四格高度区间存在公共交集"所造成的非流形棱。

    ``flat=True`` 是**休眠路径**（v1.7.7 起无调用方）：明暗只映射到一段很窄的
    高度区间（``FLAT_RELIEF_RATIO``），板面看起来基本是平的，但顶面仍是逐像素
    混合色 → 俯视就能看见整张图。它曾支撑旧的「平整分层板」模式，该模式已在
    v1.7.7 改成「单面叠色」（走叠色卡链路），这里保留纯算法便于日后回头做
    "接近平板的浮雕"实验。

    ``dither_size`` / ``weight_levels`` / ``merge_below`` 来自质量等级，
    决定混色细腻度与同层用色种类（见 constants.QUALITY_LEVELS）。
    """
    step = max(1e-4, float(quant_mm))
    floor_z = float(base_mm)
    relief_mm = float(relief_mm)
    span = relief_mm
    if flat:
        span = relief_mm * C.FLAT_RELIEF_RATIO
        floor_z = float(base_mm) + (relief_mm - span)

    # 一张图只建一次投影器（含查表缓存），逐像素复用
    mixer = None
    if stacker is None:
        # R21：表面混色（stacker=None）下质量档语义适配 —— 「像素」档的
        # dither=0 / max_components=1 是透光锚点语义，在 4 膜可达域上退化成
        # 「最近膜查找」（暗部整片单膜色）。见 surface_quality_params。
        # 注意本函数后续不再使用 dither_size / max_components，就地覆盖安全。
        _sq = surface_quality_params(
            {"dither": dither_size, "max_components": max_components})
        dither_size = int(_sq["dither"])
        max_components = int(_sq["max_components"])
        mixer = CE.Mixer(order, palette, weight_levels, merge_below,
                         dither_size, white_snap=white_snap,
                         dither_pattern=dither_pattern,
                         max_components=max_components,
                         dither_block=dither_block)
    layer_mm = max(0.0, float(layer_mm or 0.0))

    # v1.28.0（R35）：把明暗**拉满**整个起伏层。
    # 原因：用户口径是"总厚 3.60mm"，而 cov = 1-亮度 只用到图像实际的明暗范围
    # （麻将照最暗像素亮度约 0.17 → cov 上限 0.83），实测最高柱只有 3.20mm，
    # 比承诺少 0.4mm。这里按**这张图自己的** cov 区间线性归一化：最亮 → 0、
    # 最暗 → 1，于是最高柱正好等于 base + relief。
    # 归一化只改高度，不碰配色（颜色仍按原 rgb 选）。
    _cov = []
    _lo, _hi = 1.0, 0.0
    for row in range(h):
        line = []
        for col in range(w):
            c = CE.region_coverage(grid[row][col], brightness_weight,
                                   invert, gamma)
            line.append(c)
            if c < _lo:
                _lo = c
            if c > _hi:
                _hi = c
        _cov.append(line)
    _span = _hi - _lo

    def _norm(c):
        return (c - _lo) / _span if _span > 1e-6 else 0.5

    z_grid = []
    key_grid = []
    stack_grid = [] if stacker is not None else None
    for row in range(h):
        gline = grid[row]
        zl = []
        kl = []
        sl = [] if stacker is not None else None
        for col in range(w):
            rgb = gline[col]
            cov = _norm(_cov[row][col])
            if stacker is not None:
                st = tuple(stacker.pick(rgb, row, col))
                # 空堆叠 = 该像素一片色层都不用（近白像素），顶面就是基底。
                # ⚠ 这个兜底键**必须**是 key_to_mat 里真实存在的材质键 ——
                # 写死 "W" 会在"白被自动挑色丢掉 + 底色≠白"时直接 KeyError
                # （2026-09-22 实机：自动挑色给的天蓝/品红/黄三色，底板默认
                # 天蓝 → key_to_mat 没有 W，默认参数一生成就崩）。
                key = st[0] if st else empty_key
            else:
                st = None
                key = CE.region_plan(
                    rgb, order, row=row, col=col,
                    brightness_weight=brightness_weight,
                    invert=invert, gamma=gamma, mixer=mixer)[0]
            k = int(round(cov * span / step))
            th = k * step
            if th < C.MIN_REGION_MM:
                th = C.MIN_REGION_MM
            if st and layer_mm > 0.0:
                # 叠层必须放得下：柱高不得低于"色片总厚"，否则顶面会截断色片、
                # 呈现色不再是整叠的透射积（颜色直接丢失）
                need = len(st) * layer_mm
                if th < need:
                    th = need
            th = round(th, 6)
            # 先归一到 6 位小数：顶点缓存按 round(...,6) 去重，这里保证
            # "数学上相等的高度"在浮点上也严格相等，避免假的 T 型接点
            zl.append(round(floor_z + th, 6))
            kl.append(key)
            if sl is not None:
                sl.append(st)
        z_grid.append(zl)
        key_grid.append(kl)
        if sl is not None:
            stack_grid.append(sl)
        if progress is not None and (row % 16 == 0):
            progress(row / float(max(1, h)))
    return z_grid, key_grid, stack_grid


def estimate_swaps(mat_grid, z_grid, base_mm, layer_h=C.DEFAULT_LAYER_H,
                   band_mm=0.0, band_mat=None,
                   keep_mm=MB.MIN_OWN_STUB_MM):
    """估算切片后的换色次数 —— 「同层多色」的代价在这里。

    逐层统计该层内出现的不同耗材数量，取 (数量-1) 累加。返回值是
    ``(总换色次数, 单层最多用色数)``。换色次数直接决定打印时间和废料量，
    用户需要拿它跟还原度做取舍。

    ``band_mm`` / ``band_mat``：**共用底带**。该高度以下所有柱子统一用底色。
    能力保留，但实测它对换色次数的贡献为 0（底带 0% 与 20% 都是 25 次）——
    因为每层里总有一批"刚好在这一层到顶"的柱子露出自己的颜色，这部分泄漏
    正好抵消底带省下的次数。**真正决定换色次数的是调色板大小**，不是高度
    分层策略。详见 constants.QUALITY_LEVELS 的实测表。
    """
    if not z_grid:
        return 0, 0
    z0 = float(base_mm)
    band_has = band_mat is not None and float(band_mm) > 1e-9
    band_end = z0 + max(0.0, float(band_mm))
    z_max = 0.0
    for row in z_grid:
        m = max(row)
        if m > z_max:
            z_max = m
    if z_max <= z0 + 1e-9:
        return 0, 0

    n_layers = int(math.ceil((z_max - z0) / max(1e-6, float(layer_h))))
    total = 0
    worst = 0
    for li in range(n_layers):
        z_lo = z0 + li * layer_h
        z_mid = z_lo + layer_h * 0.5
        mats = set()
        for row in range(len(z_grid)):
            zr = z_grid[row]
            mr = mat_grid[row]
            for col in range(len(zr)):
                zc = zr[col]
                if zc <= z_lo + 1e-9:
                    continue
                if band_has:
                    stub = min(keep_mm, max(0.0, zc * 0.5))
                    bb = min(band_end, zc - stub)
                    if z_mid < bb - 1e-9:
                        mats.add(band_mat)
                        continue
                mats.add(mr[col])
        c = len(mats)
        if c > worst:
            worst = c
        if c > 1:
            total += c - 1
    return total, worst


def estimate_swaps_stacked(layer_mats, z_grid, base_mm,
                           layer_mm=C.RELIEF_LAYER_MM,
                           layer_h=C.DEFAULT_LAYER_H,
                           top_mat_grid=None, body_own=True,
                           base_mat=None):
    """柱内叠色（v1.7.9）的换色次数估算。

    与 ``estimate_swaps`` 同一口径 —— 逐切片层统计该层出现过的耗材数、
    取 (种类数-1) 累加，区别在于每根柱子沿 Z 是**一叠色片**：某个切片层出现
    哪种料，取决于它落在柱内第几片。

    【v1.32.0 / R39 修正：口径必须贴着几何算】色片自 v1.24.0 起是**贴柱顶**
    排的（``_stacked_wall_mat`` 的 ``k = floor((zt - z)/nb)``），而这里原来按
    "从底板往上数"取 ``seq[k]`` —— 两边方向相反，报出来的换色次数与实物根本
    不是一回事（同一张图：旧口径 12 次，按几何逐层数 9 次；打开 ``body_own``
    后 38 次）。现在按柱顶往下数，与 ``_stacked_wall_mat`` 逐分支对齐。

    ``top_mat_grid``：每格的**顶面材质**（``generation`` 里的 ``mat_grid``）。
    **空堆叠（近白格）的柱子整根都是基底色** —— ``_stacked_wall_mat`` 对空
    ``seq`` 返回 ``lambda z: top_mat``，所以它在每个切片层同样要占用基底那卷料。
    早期版本这里对空 ``seq`` 直接跳过 → "白底 + 彩色图案"这类图**少算、甚至
    漏光**换色次数（最坏情形实测报 0 次、实为 9 次，见
    ``tests/out/_exp_stack_swaps_empty.py``）。传 ``None`` 则退回旧的"忽略空堆叠"
    口径，仅供不需要精度的场合。

    ``body_own`` / ``base_mat``：色片**以下**那一截用什么料，必须与几何的
    ``body_own`` 同步 —— True 用该柱最外色 ``seq[-1]``，False 用基底色
    （``base_mat`` 缺省时退回 ``top_mat_grid``）。

    ``layer_h`` 用切片层高（0.1mm），与 ``layer_mm``（色片厚）同量级 ——
    一片色层正好是一层切片，AMS 在该层内只需把该片用到的料备齐。

    返回 ``(总换色次数, 单层最多用色数)``。
    """
    if not z_grid or not layer_mats:
        return 0, 0
    z0 = float(base_mm)
    nb = max(1e-6, float(layer_mm))
    z_max = 0.0
    for row in z_grid:
        m = max(row)
        if m > z_max:
            z_max = m
    if z_max <= z0 + 1e-9:
        return 0, 0

    n_layers = int(math.ceil((z_max - z0) / max(1e-6, float(layer_h))))
    total = 0
    worst = 0
    for li in range(n_layers):
        z_lo = z0 + li * layer_h
        z_mid = z_lo + layer_h * 0.5
        mats = set()
        for row in range(len(z_grid)):
            zr = z_grid[row]
            lr = layer_mats[row]
            for col in range(len(zr)):
                zt = float(zr[col])
                if zt <= z_lo + 1e-9:
                    continue
                seq = lr[col]
                if not seq:
                    # 空堆叠 = 整根柱子都是基底色，**这一层照样占一卷基底料**。
                    if top_mat_grid is not None:
                        mats.add(top_mat_grid[row][col])
                    continue
                k = int(math.floor((zt - z_mid) / nb + 1e-9))
                if k < 0:
                    k = 0
                if k >= len(seq):
                    if body_own:
                        mats.add(seq[-1])
                    elif base_mat is not None:
                        mats.add(base_mat)
                    elif top_mat_grid is not None:
                        mats.add(top_mat_grid[row][col])
                else:
                    mats.add(seq[len(seq) - 1 - k])
        c = len(mats)
        if c > worst:
            worst = c
        if c > 1:
            total += c - 1
    return total, worst


# ---------------------------------------------------------------------------
# 质量等级 / 自由配色 解析
# ---------------------------------------------------------------------------
def quality_profile(params):
    """取出质量等级的参数包（未知值回落到默认等级）。

    每个等级是一组**互相配合**的参数（见 constants.QUALITY_LEVELS）：
    ``dither`` 决定混色细腻度（还原端）；``weight_levels`` / ``merge_below``
    决定一个像素会用几种颜色、进而决定同层用色种类（换色次数端）。
    用户要的"极致 / 均衡 / 像素"就是这个包的三档预设。

    【v1.3：三档都用满 4 色】不再用"整机只允许 2/3/4 色"来拉开换色次数 ——
    实测那样会让浅灰调照片的"像素"档塌缩成只有白色。降换色改走"色相相近就
    并项"这条路（merge_below）。
    """
    q = params.get("quality") or C.DEFAULT_QUALITY
    if q not in C.QUALITY_KEYS:
        q = C.DEFAULT_QUALITY
    prof = dict(C.QUALITY_LEVELS[q])
    prof["key"] = q
    # v1.33.0（R40）：每档的**最小可打印宽度**（mm）。与 min_feature（面积）
    # 是两个不同的物理量：面积默认放过"1 格宽、上百格长"的窄条，而窄条在
    # 切片器里就是两色之间的空隙。这里按档位写进 prof，生成 / 预览 / 探针
    # 都从同一处读，避免两边阈值算错（见 constants.MIN_PRINT_WIDTH_MM）。
    #
    # v1.35.0（R42）：改成 **按喷嘴线宽换算**，不再写死毫米。实测旧写死
    # 版本在 0.2 喷嘴上输出与 0.4 逐格相同（换了喷嘴白换）；而且精细度越高，
    # 固定毫米折算成格子越多，细节被并掉的比例反而上升。
    _noz = params.get("nozzle_mm")
    prof["min_width_mm"] = float(C.min_print_width(q, _noz))
    prof["min_feature_mm"] = float(C.min_print_feature(q, _noz))
    # ------------------------------------------------------------------
    # v1.0.12：**精细度高于质量档建议值 2 倍时，取消并块与色阶上限**
    # ------------------------------------------------------------------
    # 根因（用户 2026-11-17：精细度 455，模型还是一格一格的锯齿）：
    # 「均衡」档默认 block_px=2（每 2×2 格并成一块）、max_stacks=6（只留 6 种
    # 叠法）、min_feature 1.6mm。用户手输 455，实际色块只有约 228 格，还被压到
    # 6 种颜色 —— 精细度白加了 4 倍，模型却按 96 档的粒度在画。
    # 口径：既然用户明确要更细，质量档就不许偷偷降采样；并块/色阶上限归零，
    # 阈值回落到「极致」那条物理下限（仍按喷嘴线宽缩放）。
    _res = int(params.get("resolution") or 0)
    if _res and _res >= 2 * int(prof.get("suggested_res", 0) or 0):
        prof["block_px"] = 1
        prof["max_stacks"] = 0
        prof["min_feature_mm"] = float(C.min_print_feature("ultimate", _noz))
        prof["min_width_mm"] = float(C.min_print_width("ultimate", _noz))
        prof["detail_override"] = True
    else:
        prof["detail_override"] = False
    # 抖动图案（v1.7.2）与质量等级**正交**：等级决定"色阶多细 / 是否并项"，
    # 图案只决定颗粒的空间排布（两者边缘分布相同，还原度不变）。所以它是
    # 独立参数，不塞进 QUALITY_LEVELS 的三档里。
    pat = params.get("dither_pattern") or C.DEFAULT_DITHER_PATTERN
    prof["dither_pattern"] = pat if pat in C.DITHER_PATTERNS \
        else C.DEFAULT_DITHER_PATTERN
    return prof


def active_color_group(params):
    """当前生效的混色色组 id（v1.23.0）。

    【v1.29.0 取消「透光强制回落 CMYK」】旧版认为"只有 CMYW 有实拍标定，
    其它组没有物理参数"，于是把 RYBW / 6色 / 8色 全部静默换成 CMYK —— 用户
    实测发现"选了 RYBW，打出来却还是 CMYW"。现在：RYBW 有自己的颜料式
    减法模型（color_engine._PIGMENT_T，R+Y=橙 / Y+B=绿 / R+B=紫），6/8 色组
    的绿黑也有近似谱；色组选什么就按什么算。代价要说清：只有 CMYW 是实拍
    标定，其余组是模型近似（文案已如实标注）。
    """
    g = params.get("mix_group")
    if g not in C.COLOR_GROUPS:
        g = None
    if g is None and params.get("mode") == "card":
        # 旧脚本 / 旧工程（没有 mix_group）的双面卡：沿用历史上的固定四色，
        # 与 v1.22.0 的 MODE_STACK_KEYS 行为逐位一致。
        return C.DEFAULT_COLOR_GROUP
    return g


def geometry_palette(params):
    """生成与预览共用的**耗材色板**决议（v1.25.0）。

    【为什么必须收敛成一处】R30 实测发现预览侧和生成侧各自写了一份色板
    决议：生成侧走 ``color_group_palette()``（色组的真实色值），预览侧在
    没勾「拓竹实测色板」时直接传 ``None``（落到模块内默认）。同一张图，
    两边算出的顶面颜色因此分叉 —— 这是"预览比实物好看"的残留根因。
    任何"预览要不要用这个色板"的判断，都必须经这里。

    返回 ``{键: sRGB}``；``None`` 表示不覆盖（下游落 BASE_COLORS 兜底）。
    """
    group = active_color_group(params)
    palette = (C.color_group_palette(group, basis=params.get("color_basis"))
               if group else None)
    if params.get("use_bambu_palette") and group in (None, "cmyk"):
        # 拓竹 CMYK 套装实测色只对 CMYK 组成立（它就是那四卷的实测值）。
        if palette is None:
            palette = {}
        palette.update({k: C.BAMBU_CMYK_PRESET[k] for k in C.BAMBU_CMYK_PRESET})
    return palette


def estimate_color_fidelity(grid, stack_grid, palette=None, dark_lum=0.3,
                            window=3, mirror=False, below=(),
                            stack_bands=4, white_outer=False):
    """按标定反射模型估算「远看时和原图差多少」（v1.26.0 / R32 改口径）。

    返回 ``{dE_med, dE_p90, dE_dark_med, dark_share, n, window, dE_pix_med}``，
    单位 CIE76 ΔE；``stack_grid`` 为空（非叠色链路）时返回 ``None``。

    【为什么不能逐像素算（v1.25.0 的错误口径）】抖动的本质是**相邻点空间
    互补**：每个点都刻意偏离目标色，靠邻居一起平均回来。逐像素比较等于
    "贴着屏幕看单个点"，必然判抖动更差 —— 实测同一张照片，逐像素
    dE 中位：像素档（不抖动）7.33 < 均衡档（抖动）9.54，方向完全是反的；
    按 3×3 窗口平均则 4.58 vs 9.54，抖动好一倍。所以 v1.25.0 那版报告
    会把用户往"像素档"上引，是**结论级**的错误，不是精度问题。

    【现口径】把模型顶面色和原图都做 ``window×window`` 格（默认 3×3）的
    矩形均值，再比 ΔE —— 模拟一臂距离看模型时眼睛的空间积分。两侧都平均
    才是对等的比较（只平均模型侧会拿"平滑过的模型"去比"带细节的原图"）。
    ``dE_pix_med`` 保留逐像素值只作内部诊断，**不展示给用户**。

    【暗部单独统计的理由】实测（麻将桌照片，200mm/2.4mm，CMYK）：
    亮度<0.2 的像素 ΔE 中位 33.9，亮度 0.4~0.6 只有 9.2。光看总值会把
    "暗部到不了黑"这个物理上限藏起来 —— 它恰恰是最该告诉用户的一条。
    """
    if not stack_grid:
        return None
    h = min(len(grid), len(stack_grid))
    if h <= 0:
        return None
    w = min(len(grid[0]), len(stack_grid[0]))
    for r in range(h):
        w = min(w, len(grid[r]), len(stack_grid[r]))
    if w <= 0:
        return None

    # 预测色先全部算出来：积分图要两遍扫（预测 / 目标各一张）
    # v1.28.0：叠色浮雕与双面卡共用镜像 8 层呈色模型（mirror=True）；
    # 只有早期单面口径才走 stack_present。
    if mirror:
        # v1.0.7：必须与锚点 / 顶点色用**同一个**每面层数与白的位置 ——
        # 早先这里固定按 4 层、白在内侧算，双面卡（6 层、白在外）报出的
        # 色差是另一套结构的，数字偏乐观。
        def _present(st, pal, g):
            return CE.stack_present_mirror(st, pal, g,
                                           bands=int(stack_bands or 4),
                                           keep_order=bool(white_outer))
    else:
        def _present(st, pal, g):
            return CE.stack_present(st, pal, g, below)
    _stack_keys = set(k for line in stack_grid for st in line for k in (st or ()))
    _gain = CE.white_neutral_gain(_stack_keys, palette)   # v1.40.0：与锚点同一白点口径
    pred = [[_present(stack_grid[r][c] or (), palette, _gain)
             for c in range(w)] for r in range(h)]

    # 积分图（sRGB 三通道）：窗口均值 = 矩形和 / 面积。
    # 不这么做的话 k>0 时每格重复求和，O(n·k²) 会把 300×300 的生成拖慢几秒。
    S = [[[0.0, 0.0, 0.0] for _ in range(w + 1)] for _ in range(h + 1)]
    T = [[[0.0, 0.0, 0.0] for _ in range(w + 1)] for _ in range(h + 1)]
    for r in range(h):
        for c in range(w):
            a = pred[r][c]
            b = grid[r][c]
            for i in range(3):
                S[r + 1][c + 1][i] = (S[r][c + 1][i] + S[r + 1][c][i]
                                      - S[r][c][i] + a[i])
                T[r + 1][c + 1][i] = (T[r][c + 1][i] + T[r + 1][c][i]
                                      - T[r][c][i] + b[i])

    k = max(0, (int(window) - 1) // 2)
    ds = []
    pix = []
    dark = []
    for r in range(h):
        r0 = r - k if r > k else 0
        r1 = min(h, r + k + 1)
        for c in range(w):
            c0 = c - k if c > k else 0
            c1 = min(w, c + k + 1)
            nbox = float((r1 - r0) * (c1 - c0))
            lt = []
            lp = []
            for i in range(3):
                lt.append((T[r1][c1][i] - T[r0][c1][i]
                           - T[r1][c0][i] + T[r0][c0][i]) / nbox)
                lp.append((S[r1][c1][i] - S[r0][c1][i]
                           - S[r1][c0][i] + S[r0][c0][i]) / nbox)
            la = CE._srgb_to_lab(tuple(lt))
            lb = CE._srgb_to_lab(tuple(lp))
            d = ((la[0] - lb[0]) ** 2 + (la[1] - lb[1]) ** 2
                 + (la[2] - lb[2]) ** 2) ** 0.5
            ds.append(d)
            if k:
                lpa = CE._srgb_to_lab(grid[r][c])
                lpb = CE._srgb_to_lab(pred[r][c])
                pix.append(((lpa[0] - lpb[0]) ** 2 + (lpa[1] - lpb[1]) ** 2
                            + (lpa[2] - lpb[2]) ** 2) ** 0.5)
            lum = 0.2126 * lt[0] + 0.7152 * lt[1] + 0.0722 * lt[2]
            if lum < dark_lum:
                dark.append(d)
    if not ds:
        return None
    ds.sort()
    n = len(ds)

    def _med(a):
        return a[len(a) // 2] if a else 0.0

    pix.sort()
    return {
        "dE_med": round(_med(ds), 2),
        "dE_p90": round(ds[min(n - 1, int(n * 0.9))], 2),
        "dE_dark_med": round(_med(sorted(dark)), 2) if dark else 0.0,
        "dark_share": round(len(dark) / float(n), 4),
        "n": n,
        # 窗口边长（格）；1 = 逐像素（旧口径，仅作对照，不用于展示）
        "window": 2 * k + 1,
        # 逐像素中位：抖动档必然比远看差，用来解释"近看有颗粒"
        "dE_pix_med": round(_med(pix) if pix else _med(ds), 2),
    }


def fidelity_word(dE):
    """ΔE → 人话（用户明确说过不懂色彩术语，禁止直接抛 ΔE）。"""
    if dE <= 5.0:
        return "很像"
    if dE <= 10.0:
        return "接近，细看有差"
    if dE <= 20.0:
        return "一眼能看出不同"
    return "明显不像"


def resolve_palette(grid, params):
    """自由配色：决定"用哪几种颜色参与"。

    【为什么不再有"色序"】用户实机确认：机器可以在任意 Z 高度换色、同一层可以
    出现不同颜色、颜色没有层序要求。于是**只有"用哪几种颜色"是有意义的决策**，
    它们怎么排列毫不影响结果（见 color_engine._wheel_colors 的注释）。

    选色按**色相覆盖**而不是按用量 —— 能不能还原某个色相，取决于"色板的凸包
    有没有把这个色相围进去"。

    【v1.23.0 四档混色组】②③ 两模式不再由算法"猜"用哪几色，而是**用户选定
    一组**（CMYK / RYBW / CMYWGK / 8色MAX）。色组是硬约束：keys 与色值都从
    ``C.COLOR_GROUPS[g]`` 取，自动挑色对它不生效。这样做的理由是"可预测"——
    用户买了几卷什么色，就该按那几卷算；算法擅自少用一卷，等于把用户已经
    买好的料晾着。① 全色平板不走这里（它的颜色来自照片聚类，见 mosaic.py）。

    返回 ``(keys, profile, analysis)``。
    """
    prof = quality_profile(params)
    group = active_color_group(params)
    if group:
        g = C.COLOR_GROUPS[group]
        keys = CE.canonical_key_order(list(g["keys"]))
        analysis = {
            "keys": list(keys), "coverage": None, "group": group,
            "shares": {}, "hist": [], "gray_share": 0.0,
            "note": "固定色组：%s（%d 卷）。色组由你选定，算法不再自动挑色 —— "
                    "买了几卷什么色，就按那几卷算。" % (g["cn"], len(keys)),
        }
        return keys, prof, analysis

    allowed = [k for k in (params.get("allowed_colors") or C.ALL_COLOR_KEYS)
               if k in C.ALL_COLOR_KEYS]
    if not allowed:
        allowed = list(C.ALL_COLOR_KEYS)
    cap = max(1, int(prof["max_palette"]))

    if params.get("auto_palette", True):
        analysis = CE.suggest_default_stack(grid, max_colors=cap, allowed=allowed)
        keys = list(analysis.get("keys") or [])
    else:
        # 手工模式：照用户勾选的来，但不超过等级上限
        keys = list(allowed)[:cap]
        analysis = CE.analyze_palette(grid, max_colors=cap, allowed=allowed)

    keys = keys[:cap]
    if not keys:
        keys = list(allowed)[:1]
    # 输出成稳定的"色轮顺序 + 白最后"。顺序本身不影响结果（权重只看集合），
    # 但有序抖动按权重的累积分布切分，固定顺序才能保证"用户怎么勾选都一样"。
    keys = CE.canonical_key_order(keys)
    return keys, prof, analysis


# ---------------------------------------------------------------------------
# 读图 + 目标色量化（三处调用点共用）
# ---------------------------------------------------------------------------
def _prepare_grid(image, out_w, out_h, params, want_raw):
    """``prepare_grid`` / ``prepare_grid_raw`` 的**唯一实现**，返回 5 元组。

    ``want_raw`` 决定第 5 个元素是"量化前的原始目标网格"还是 ``None`` ——
    原始网格必须在 ``quantize_grid`` **之前**留一份（那个函数会原地换色）。
    对外只暴露两个显式入口，不让调用方去猜第 5 个元素在不在。
    """
    sampling = params.get("sampling") or C.DEFAULT_SAMPLING
    if sampling not in C.SAMPLING_MODES:
        sampling = C.DEFAULT_SAMPLING
    grid, w, h = IA.read_image_grid(image, out_w, out_h, to_srgb=True,
                                    sampling=sampling)
    # 原始目标网格必须在量化之前留一份（下面 quantize_grid 会原地换色）
    raw_grid = [[tuple(px) for px in line] for line in grid] if want_raw else None

    kq = int(params.get("quantize_colors", C.DEFAULT_QUANTIZE_COLORS) or 0)
    kq = max(0, min(C.QUANTIZE_COLORS_MAX, kq))
    want_despeckle = bool(params.get("despeckle", C.DEFAULT_DESPECKLE))

    if kq <= 0:
        # v1.7.4：量化关闭时**不再**把去斑一起跳过。
        # 去斑的判据需要"颜色等价类"，量化正好提供；量化关了就在这里临时
        # 归并一次，**只取标签、不改颜色** —— 所以这一步严格保色（输出里出现
        # 的每一种颜色，原图里都有）。k_ref 用默认量化档，这样用户切换量化
        # 开关时，去斑命中的格子集合不变（实测 203 格 = 203 格）。
        if not want_despeckle:
            return grid, w, h, None, raw_grid
        grid, dstats = QZ.despeckle_grid(grid, k_ref=C.DEFAULT_QUANTIZE_COLORS,
                                         seed=C.QUANTIZE_SEED)
        dstats["k"] = 0
        dstats["despeckle_only"] = True
        return grid, w, h, dstats, raw_grid

    grid, stats = QZ.quantize_grid(grid, kq, despeckle=want_despeckle,
                                   seed=C.QUANTIZE_SEED)
    stats["k"] = kq
    return grid, w, h, stats, raw_grid


def prepare_grid(image, out_w, out_h, params):
    """读图并做目标色量化，返回 ``(grid, w, h, quant_stats)``。

    **生成 / 配色分析 / 参数报告三处都走这里**，否则"面板上建议用哪几种颜色"
    和"真正生成时用的网格"会是两份不同的数据（量化会改变色相分布）。
    """
    grid, w, h, stats, _raw = _prepare_grid(image, out_w, out_h, params, False)
    return grid, w, h, stats


def prepare_grid_raw(image, out_w, out_h, params):
    """与 ``prepare_grid`` **同一条管线**，额外返回**量化前**的原始目标网格。

    返回 ``(grid, w, h, quant_stats, raw_grid)``。仅「平整色块板」需要 ——
    它的全色域分析要拿"照片原始色"与"N 色还原色"逐格比对（量化损失的均值 /
    最大值两个数不够，**误差分布**才是观感的来源）。
    """
    return _prepare_grid(image, out_w, out_h, params, True)


def quant_note(stats):
    """量化 / 去斑结果的人话摘要（空字符串 = 什么都没做）。"""
    if not stats or stats.get("skipped"):
        return ""
    parts = []
    if stats.get("despeckle_only"):
        n = int(stats.get("changed", 0))
        if n > 0:
            parts.append("颜色量化已关闭；只清理孤立点 %d 格"
                         "（保色：换成邻居已有的颜色，不引入新色）" % n)
        else:
            parts.append("颜色量化已关闭；未发现孤立点")
    elif stats.get("k"):
        # 只有 quantize 真跑过（stats 带 k）才有量化摘要，不能印出
        # "0 种 → 0 簇"这种空转行。
        note = ("颜色量化：%d 种目标色 → %d 簇（量化损失 %.2f ΔE）"
                % (stats.get("unique_in", 0), stats.get("k_used", 0),
                   stats.get("dE_mean", 0.0)))
        if stats.get("despeckled"):
            note += "，清理孤立点 %d 格" % stats["despeckled"]
        parts.append(note)
    return "；".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# 浮雕明信片：反面凹刻高度场（v1.44.0 / R54）
# ---------------------------------------------------------------------------
def _box_mean(a, r):
    """二维盒式平均（积分图，O(n)）。用 numpy 实现，不引入 scipy。"""
    h, w = a.shape
    pad = _np.pad(a, ((r, r), (r, r)), mode="edge")
    ii = _np.cumsum(_np.cumsum(pad, axis=0), axis=1)
    ii = _np.pad(ii, ((1, 0), (1, 0)), mode="constant")
    k = 2 * r + 1
    s = (ii[k:, k:] - ii[:-k, k:] - ii[k:, :-k] + ii[:-k, :-k]) / float(k * k)
    return s[:h, :w]


def engrave_depth_grid(back_image, w, h, max_mm, sampling="area", mirror=False):
    """反面图片 → 逐格凹刻深度（mm，``h`` 行 × ``w`` 列）。

    白的纸面 = 0（不刻），越黑刻得越深，最深 = ``max_mm``。

    两步几乎都是必须的：
      1. **去光照**：除以大尺度低频。用户拍的反面照片带斜射光/暗角，
         直接取亮度会把"整张纸的明暗梯度"当成图案刻出来（这坑在预览
         脚本里踩过：出来是三条斜色带）。
      2. **按分位拉伸**：纸白取 p97、最黑取 p3。固定 0~255 阈值在手机
         照片（对比度被压过）上会刻不出东西。
    低于 6% 的残余起伏按纸纹处理（不刻）—— 否则整面都是 0.02mm 的麻点。

    ``mirror=True`` 时先把图片绕**竖直中轴线**左右翻转再算深度（v1.45.2 /
    R58）。翻转放在采样之后做：``w × h`` 网格本身就是"画面"，图片按
    居中裁剪进这个网格，而居中裁剪关于中轴线对称，所以"翻网格"与"翻原图
    再裁剪"逐格相同，不必让 image_analysis 多一条分支。
    """
    if back_image is None:
        return [[0.0] * w for _ in range(h)]
    if _np is None:
        raise RuntimeError("本插件需要 numpy（Blender 自带），当前环境缺失")
    g, _w, _h = IA.read_image_grid(back_image, w, h, to_srgb=True,
                                   sampling=sampling)
    arr = _np.asarray(g, dtype=_np.float32)
    if mirror:
        arr = _np.ascontiguousarray(arr[:, ::-1])
    luma = (0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1]
            + 0.0722 * arr[:, :, 2])
    r = max(2, int(round(min(w, h) * 0.18)))
    flat = luma / _np.maximum(_box_mean(luma, r), 1e-3)
    # 【白 = 0，别用高分位当白点】去光照后的纸面 ratio ≈ 1，直接拿 1.0 当
    # 白参考；只有墨色 ratio 才明显 < 1。早先用 p97 当"白"会出事：紧挨着
    # 黑字的纸面 ratio 会 > 1（局部均值被黑拉低），把 p97 顶到 1.2，于是
    # 一整片纯白纸被算成 0.1mm 深（实测 2127/3600 格）。这里用固定 1.0，
    # 只用低分位定"最黑"，纸面自然归零。
    lo = float(_np.percentile(flat, 2.0))
    span = max(1e-4, 1.0 - lo)
    d = _np.clip((1.0 - flat) / span, 0.0, 1.0)
    # 再留 5% 的死区：扫描噪点 / 纸纹不刻
    d = _np.clip((d - 0.05) / 0.95, 0.0, 1.0)
    if float(d.max()) < 0.02:    # 整张差不多一个亮度：没有可刻的图案
        return [[0.0] * w for _ in range(h)]
    d *= float(max_mm)
    return [[float(v) for v in row] for row in d]


# ---------------------------------------------------------------------------
# 主生成入口
# ---------------------------------------------------------------------------
def _generate_impl(params, _bm_slot=None):
    """按参数字典生成模型（实际实现；对外请调 :func:`generate`）。

    params 需要包含：
        image, mode, resolution, size_mm, base_mm, relief_mm,
        quality, allowed_colors(list[str]), base_key, height_mode,
        anchor_mode, brightness_weight, invert, gamma,
        use_bambu_palette, auto_palette
    返回统计 dict。

    mode 有五种几何：``relief`` 分层浮雕 / ``mosaic`` 平整色块板 /
    ``card`` 双面叠色 / ``postcard`` 浮雕明信片 / ``single`` 单面叠色。
    （v1.3 已按用户要求删除 ``blocks`` 像素方块阵列；v1.7.7 把 ``flat``
    「平整分层板」改造成 ``single``「单面叠色」，v1.22.0 把它换成 ``mosaic``；
    v1.0.2 用户明确要"单面叠色 + 反面白 + 4 层白隔离"，``single`` 以**新几何**
    复活 —— 与 v1.22.0 之前那版不是同一套结构，旧 id 照旧接受。）
    """
    t0 = time.time()
    image = params["image"]
    mode = params.get("mode", "relief")
    if mode == "flat":
        # v1.7.6 及以前的 id（平整分层板）。那个 id 在 v1.7.7 被翻译成「单面
        # 叠色」，v1.22.0 单面叠色下线、同位置换成「平整色块板」—— 语义上
        # flat（一块平板）与 mosaic（一块平板）反而更近，所以旧值继续翻译到
        # 新模式，而不是掉回浮雕。
        mode = "mosaic"
    mode = mode if mode in C.MODE_IDS else "relief"
    # v1.23.0：只给"模型最大厚度"时，在这里统一折算成 base/relief/band。
    # **显式传了 max_thickness_mm 才覆盖** —— 旧脚本/旧测试只传
    # base_mm/relief_mm 时逐位不变（这是最小改动原则，不是两套口径：
    # GUI 走 as_params 也只写这四个派生值，公式唯一定义在 thickness_split）。
    if params.get("max_thickness_mm"):
        params = dict(params)
        # v1.0.6：双面卡的层数是固定值（CARD_BANDS_PER_SIDE = 6），不跟面板
        # 滑杆走。厚度拆分必须用同一个值 —— 否则按 4 层拆出 0.15mm/片，
        # 几何按 6 层建出 12 片 → 实际厚度 1.80mm，与面板读数 1.20 不符。
        _tb = params.get("stack_bands", C.DEFAULT_STACK_BANDS)
        if mode == "card":
            _tb = C.CARD_BANDS_PER_SIDE
        split = C.thickness_split(
            mode, params["max_thickness_mm"], _tb,
            # v1.44.0：明信片的反面厚度由"最大凹刻深度"决定，与正面厚度分开
            params.get("back_depth_mm"))
        params["base_mm"] = split["base_mm"]
        params["relief_mm"] = split["relief_mm"]
        if mode in ("card", "single"):
            params["band_mm"] = split["band_mm"]
            params["stack_bands"] = split["stack_bands"]
        if mode == "postcard":
            params["post_back_mm"] = split["back_mm"]
            params["post_engrave_mm"] = split["engrave_mm"]
    if mode == "mosaic":
        # 全色平板的"色块数 N"就是量化簇数 —— 两块参数是一回事，所以参数
        # 就地覆写：面板上只有一个「色块数」滑杆，不做事后二次量化（二次量化
        # 会在已有簇心上再聚一次，颜色白白变糊）。用副本，不改调用方的字典。
        #
        # v1.23.0：色块数**不再手改**，由质量等级决定（MOSAIC_QUALITY）。
        # 这里保留一条兜底：调用方没给 color_count 时按档位取 —— 避免
        # "面板上换了质量档、生成侧却还用旧默认 8 色"。
        params = dict(params)
        n_col = int(params.get("color_count") or 0)
        if n_col <= 0:
            mq = C.MOSAIC_QUALITY.get(params.get("quality") or C.DEFAULT_QUALITY) \
                or C.MOSAIC_QUALITY[C.DEFAULT_QUALITY]
            n_col = int(mq["color_count"])
            params["mosaic_min_mm"] = float(mq["min_mm"])
        params["quantize_colors"] = max(C.MIN_COLOR_COUNT,
                                        min(C.MAX_COLOR_COUNT, n_col))
    res = int(params.get("resolution", C.DEFAULT_RES))
    res = max(C.MIN_RES, min(C.MAX_RES, res))

    # ---- 1. 读图 + 目标色量化（统一走 prepare_grid 咽喉点）----
    # v1.13.0 收口：v1.12 曾在本函数留一份内联"读图+量化"链路，导致上游
    # 预处理（当时是去背景）只有预览消费、模型没消费。本函数现在必须以
    # prepare_grid 为唯一网格来源，不许再开第二份链路。
    src_w, src_h = int(image.size[0]), int(image.size[1])
    if src_w <= 0 or src_h <= 0:
        raise ValueError("图片没有有效像素")

    def _grid_of(value):
        """精细度 → (宽格数, 高格数)。保持图片长宽比，短边不小于 MIN_RES。"""
        if src_w >= src_h:
            return int(value), max(C.MIN_RES,
                                   int(round(value * src_h / float(src_w))))
        return max(C.MIN_RES,
                   int(round(value * src_w / float(src_h)))), int(value)

    out_w, out_h = _grid_of(res)

    # v1.0.16（用户 2026-11-21）：内存估算**只做提示，不再拦截、不再降档**。
    # 用户原话：「分辨率 11、12 均按理论计算格数并进行渲染，当本机电脑内存不足
    # 时，不进行降档降格，同时不体现 1000 格为本机上限」。
    # v1.0.17（用户 2026-11-22）：连"提示"也不要了 —— 换台机器结论就变，
    # 提醒只会误导。这里仍算一遍（结果放在 res["mem"] 里供调试），但不打印、
    # 不展示、不影响任何几何。
    # mem_gate=False 仍保留 —— 内部回归靠它跳过这次估算。
    _mem = {"ram_gb": 0.0, "budget_gb": 0.0, "est_gb": 0.0,
            "res_req": int(res), "res_used": int(res), "fitted": False,
            "over_budget": False}
    if params.get("mem_gate", True):
        _budget = C.mem_budget_bytes()
        _ram = C.total_ram_bytes()
        _est = C.est_peak_bytes(int(out_w) * int(out_h), mode)
        _mem.update({"ram_gb": round(_ram / 1073741824.0, 2),
                     "budget_gb": round(_budget / 1073741824.0, 2),
                     "est_gb": round(_est / 1073741824.0, 2),
                     "over_budget": bool(_est > _budget)})

    sampling = params.get("sampling") or C.DEFAULT_SAMPLING
    if sampling not in C.SAMPLING_MODES:
        sampling = C.DEFAULT_SAMPLING
    PG.update(0.04, "读取图片、配色量化")
    # mosaic 需要"量化前"的原始目标网格做全色域分析（误差分布），走带原始
    # 网格的入口；其余模式走 4 元组入口，不付这份拷贝的代价。
    if mode == "mosaic":
        grid, w, h, quant_stats, raw_grid = prepare_grid_raw(
            image, out_w, out_h, params)
    else:
        grid, w, h, quant_stats = prepare_grid(image, out_w, out_h, params)
        raw_grid = None
    qnote = quant_note(quant_stats)
    PG.update(0.12, "计算配色")

    # ---- 2. 自由配色 + 质量等级 ----
    keys, prof, analysis = resolve_palette(grid, params)
    # v1.25.0：色板决议收敛到 ``geometry_palette`` —— 预览侧调同一个函数，
    # 否则"预览用 BAMBU 预设 / 生成用色组色值"这类分叉会让预览偏好看。
    palette = geometry_palette(params)
    PG.update(0.16, "准备材质与几何参数")

    full_order = CE.expand_order(keys)
    if mode in ("card", "single"):
        # v1.5 双面镜像叠色卡 / v1.0.2 单面叠色：色组已由 resolve_palette 决定
        # （旧脚本没有 mix_group 时回落 CMYK，等价于 v1.22.0 的
        # MODE_STACK_KEYS 行为）。报告里的"参与配色"也照实写成这一组。
        full_order = CE.canonical_key_order(list(keys))

    # 【v1.37.2 / R45】材质表（= 3MF 材质槽 / 拓竹 AMS 槽位 / filament_colour
    # 的顺序）必须按**色组声明顺序**建，不能按 canonical_key_order。
    #
    # 根因（2026-11-01 实拍 RYBW）：canonical 的固定秩表是 C/M/Y/R/G/B/W，
    # RYBW 的 R(秩3) 排在 Y(秩2) 之后 → 材质表变成 [Y, R, B, W]：第 1 槽是黄、
    # 第 2 槽是红。用户按面板「RYBW = 红/黄/蓝/白」装料（红在第 1 槽）后，
    # 红↔黄**整槽互换**，实拍就是「红桌印成黄桌、黄区印成红」（实测同一张
    # 四象限测试图：源=红(0.85,0.10,0.10) 的格子材质是 R、颜色也确实是红，
    # 说明选色/几何都对，错的只有槽位顺序）。CMYK 组不受影响：C/M/Y/W 本来
    # 就是这个序，所以多年来 CMYK 正常、RYBW 一换色就翻车。
    #
    # 几何/抖动仍用 canonical 的 full_order —— Mixer / StackMatcher 内部会再
    # 做一次 canonical_key_order，键序只影响材质下标，不影响逐格颜色、换色
    # 次数与几何。这里只改材质与槽位顺序。
    _grp = active_color_group(params)
    if _grp and _grp in C.COLOR_GROUPS:
        _decl = [k for k in C.COLOR_GROUPS[_grp]["keys"] if k in set(keys)]
        _decl += [k for k in keys if k not in _decl]
        mat_order = _decl
    else:
        mat_order = list(keys)

    base_mm = float(params.get("base_mm", C.DEFAULT_BASE_MM))
    relief_mm = float(params.get("relief_mm", C.DEFAULT_RELIEF_MM))
    base_key = params.get("base_key", "W")
    if base_key not in C.BASE_COLORS:
        base_key = "W"
    # v1.0.14（用户 2026-11-20）：全色平板 / 明信片浮雕的「底板色」功能删除，
    # 底板固定白色。这里在**入口**统一，直接调 generate 的旧脚本传彩色也
    # 不会建出彩色底板（否则 meta 报彩色、几何却是白，两边打架）。
    if mode in ("mosaic", "postcard"):
        base_key = "W"
    # 实际用于**几何与材质表**的基色。柱内叠色会把基底固定成白（见 region
    # 分支的说明），报告与元信息都按这个"实用值"写 —— 否则会出现"面板显示
    # 天蓝、模型其实全白"的错位。
    base_key_used = base_key

    # ---- v1.0.15：底板拆成独立对象（用户 2026-11-20 第 2/3 条）----
    # 用户口径：「明信片浮雕、叠色浮雕和单面平板切片后，反面/底板不是独立的
    # 对象，无法单独编辑，需要与全色平板一样分为两个层级」。
    # 全色平板 / 明信片浮雕靠"整壳归属"拆（见下面的 _shell_split_sets）；
    # 叠色浮雕 / 单面平板的底板是一整块 0.4mm 白板，与柱体连成一壳、拆不开，
    # 所以改成**从建模就分开**：柱体从 z=0 起建（`_build_base = 0`），建完整体
    # 上抬 `base_mm`，底下再补一块同外形的白板。于是：
    #   * 白板 = 独立对象（切片里可单独换料）；
    #   * 柱体底面就是这块白板，反面天然纯色；
    #   * 总厚完全不变（白板 base_mm + 柱体 relief_mm）。
    _split_flat_base = (mode in ("relief", "single") and float(base_mm) > 1e-6)
    _build_base = 0.0 if _split_flat_base else float(base_mm)

    # 共用底带（band_ratio）：该厚度内所有柱子统一用底色 —— 即"同一根柱子沿
    # Z 轴分段换色"。用户可以调它（面板上的「共用底带」滑杆），质量等级只是
    # 提供一个默认值（三个等级都是 0）。
    #
    # 【实测结论】它对换色次数**没有收益**：底带 0% / 20% 都是 25 次。原因见
    # constants.QUALITY_LEVELS 的实测表 —— 每层总有"刚好在这一层到顶"的柱子
    # 露出自己的颜色，泄漏量正好抵消底带省下的次数。所以它只适合"我就是想让
    # 柱子下部也换一次色"的观感需求，默认关闭。band_ratio = 0 时下面退化成
    # "基座面以下用底色"，与 v1.1 的竖墙行为完全一致。
    user_band = params.get("band_ratio")
    if user_band is None:
        user_band = prof.get("band_ratio", 0.0)
    band_mm = relief_mm * max(0.0, float(user_band))
    band_mm = max(0.0, min(band_mm, relief_mm * 0.85))
    band_top = base_mm + band_mm

    base_layer, stack = CE.build_stack(
        order=full_order, layer_ths=None, base_mm=base_mm,
        relief_mm=relief_mm, base_key=base_key, palette=palette)

    # 四周外扩边框宽度（v1.0.13：**所有模式**统一加，不再只有明信片）
    # v1.0.14：改成「外框层数 × 0.40mm」（用户 2026-11-20），不再是固定 1.5mm。
    # 旧脚本不传 rim_layers 时按默认 1 层 = 0.40mm。
    _rim_layers = max(0, min(int(C.MODEL_RIM_MAX_LAYERS),
                             int(params.get("rim_layers",
                                            C.MODEL_RIM_LAYERS_DEFAULT)
                                 or 0)))
    _rim_mm = float(C.rim_mm_for_layers(_rim_layers))

    # ---- 3. 材质 ----
    materials, key_to_mat, mat_meta = build_material_list(mat_order, palette, base_key)
    mat_by_level = [key_to_mat[layer.key] for layer in stack]
    z_bounds = [layer.z_hi for layer in stack]
    side_mats = list(mat_by_level)
    base_mat = key_to_mat[base_key]

    # ---- 4. 尺寸 ----
    size_mm = float(params.get("size_mm", C.DEFAULT_SIZE_MM))
    if w >= h:
        size_x = size_mm
        size_y = size_mm * h / float(w)
    else:
        size_y = size_mm
        size_x = size_mm * w / float(h)

    # ---- 4.5 块状抖动（R18；v1.34.0 改为「色点 = 一条挤出线宽」）----
    # 像素尺寸 < 喷嘴线宽时，1 像素抖动斑点物理上打不出来（切片器小特征
    # 过滤把它并回主导色 —— 实测 card 模式彩色全灭只剩白）。块边长把斑点
    # 提到 ≥ 1× 线宽（R18 当时取 2×，在 600 精细度上反而把色点撑成 1.0mm，
    # 长边只剩 100 个色点 → 实拍"太糊"）；预览侧经 operators._dither_kw
    # 调 dither_block_for 共用。
    dblk = dither_block_for(w, h, size_x, size_y,
                            nozzle=params.get("nozzle_mm"))
    pixel_mm = min(size_x / float(w), size_y / float(h))
    if mode == "mosaic":
        # 色块板不抖动（每个像素只属于一个色块），"块状抖动"那行对它是误导 ——
        # 它关心的是**色块本身**够不够大，由 mosaic_min_mm 的合并来保证。
        print("[叠色浮雕工坊] 像素 %.2fmm（喷嘴线宽 %.2fmm）；色块板不抖动，"
              "小色块由「最小色块」参数合并"
              % (pixel_mm, C.nozzle_width(params.get("nozzle_mm"))))
    else:
        _lw = C.nozzle_width(params.get("nozzle_mm"))
        _dot = dblk * pixel_mm
        print("[叠色浮雕工坊] 像素 %.2fmm，色点 %dx%d = %.2fmm（喷嘴线宽 %.2fmm，"
              "长边约 %d 个色点）"
              % (pixel_mm, dblk, dblk, _dot, _lw, int(round(size_x / max(1e-6, _dot)))))
        if dblk > 1:
            print("[叠色浮雕工坊] 提示：色点已补到「一条挤出线宽」%.2fmm；"
                  "再想更细只能放大模型或换 0.2 喷嘴（%.2fmm 线）。"
                  % (_dot, C.nozzle_width("0.2")))
    if params.get("mix_model", "surface") == "surface" \
            and str(params.get("nozzle_mm") or "") == "0.2":
        print("[叠色浮雕工坊] 提示：0.2 喷嘴可在拓竹里选 "
              "'Bambu Lab A1 0.2 nozzle' 预设，线宽更细、色点更细腻。")
    if prof.get("detail_override"):
        print("[叠色浮雕工坊] 精细度 %d 高于「%s」档建议值 %d 的 2 倍 → "
              "已按逐格上色：不并块、不压色阶（每格 %.2fmm，台阶就是这个尺寸）"
              % (int(res), prof.get("cn") or prof.get("key"),
                 int(prof.get("suggested_res") or 0), pixel_mm))

    # ---- 5. 几何 ----
    height_mode = params.get("height_mode") or C.DEFAULT_HEIGHT_MODE
    if height_mode not in C.HEIGHT_MODES:
        height_mode = C.DEFAULT_HEIGHT_MODE
    bw = float(params.get("brightness_weight", 1.0))
    inv = bool(params.get("invert", False))
    gam = float(params.get("gamma", 1.0))

    # v1.7.7：只有高度方式选「分区配色」才走逐像素材质链路 —— relief 用它；
    # mosaic / card 都不用（色块板按分区、叠色卡按"叠哪几片"决定材质，
    # 与高度映射无关）。
    # v1.44.0：postcard 的正面就是 relief，**强制**走这条链路 —— 即使旧工程/
    # 旧脚本把 height_mode 传成别的，也不能掉到"色带叠色"那条已经下线的路。
    use_region = (height_mode == "region") or (mode == "postcard")

    bm = bmesh.new()
    if _bm_slot is not None:
        # 登记给 generate() 的 finally（异常路径也能确定性释放，见该函数说明）
        _bm_slot.append(bm)
    extra = {}
    swaps = (0, 0)

    if mode == "card":
        # 色深叠色卡（v1.5 双面镜像八层）：没有底板，前后各 bands 片 0.1mm
        # 色层，背面是正面绕中缝的 z 镜像（参考 3MF 解码结论）。四色可重复
        # 多集堆叠，呈现色 = 序列透射积的平方 —— 深色由此而来。
        # 高度模式/明暗厚度在这张卡上没有意义 —— 颜色全部由「叠哪几片、
        # 叠多深」表达，几何是完全平整的。
        # v1.0.6：双面卡的层数固定每面 6 层（回文 12 层，总厚 1.20mm），
        # 与"单面固定 8 层"同一个口径 —— 这个模式的层数由还原度决定，
        # 不跟面板滑杆走（滑杆留给浮雕等其它模式）。
        bands = max(C.MIN_STACK_BANDS, min(C.MAX_STACK_BANDS,
                                          C.CARD_BANDS_PER_SIDE))
        band_mm_c = float(params.get("band_mm", C.DEFAULT_BAND_MM))
        band_mm_c = max(C.MIN_BAND_MM, min(C.MAX_BAND_MM, band_mm_c))
        # v1.29.0：色板一律跟用户选的色组走（曾经在透光下强制套拓竹 CMYK，
        # 导致"选 RYBW 实际用 CMYW"）。RYBW 的颜料谱在 color_engine 里。
        materials, key_to_mat, mat_meta = build_material_list(
            mat_order, palette, "W")
        stacker, stack_grid = compute_card_plan(
            grid, w, h, full_order, bands, palette=palette,
            weight_levels=float(prof.get("weight_levels", 0.0)),
            merge_below=float(prof.get("merge_below", 0.0)),
            dither_size=int(prof.get("dither", CE.DEFAULT_DITHER_SIZE)),
            white_snap=float(prof.get("white_snap", 1.01)),
            base_key=base_key, mirror=True,
            dither_pattern=prof.get("dither_pattern"),
            anchor_mode=params.get("anchor_mode", C.DEFAULT_ANCHOR_MODE),
            near_k_cap=int(prof.get("near_k", 0) or 0),
            max_components=int(prof.get("max_components", 0) or 0),
            dither_block=dblk,
            mix_model=params.get("mix_model", "surface"),
            color_match=params.get("color_match", C.DEFAULT_COLOR_MATCH),
            min_feature_mm=float(prof.get("min_feature_mm",
                                          C.MIN_PRINT_FEATURE_MM)),
            block_px=int(prof.get("block_px", 1) or 1),
            max_stacks=int(prof.get("max_stacks", 0) or 0),
            min_width_mm=float(prof.get("min_width_mm",
                                        C.MIN_PRINT_WIDTH_MM)),
            pixel_mm=pixel_mm, white_outer=True,
            progress=PG.stage(0.20, "逐格配色", 0.32))
        if params.get("color_match", C.DEFAULT_COLOR_MATCH) == "solid" \
                and getattr(stacker, "stats", None):
            _st = stacker.stats
            print("[叠色浮雕工坊] 整块上色：%d 种叠法（可打印 %d 种），"
                  "碎块合并 %d 格、并块 %d 格"
                  % (_st["unique_stacks"],
                     len(stacker.stacks), _st["merged_px"],
                     _st.get("blocked_px", 0)))
            print("[叠色浮雕工坊] 色块：每 %d 格并成一个（%.2fmm），"
                  "小于 %.1fmm 的碎块并给邻区"
                  % (_st.get("block_px", 1),
                     _st.get("block_px", 1) * float(pixel_mm),
                     _st["min_feature_mm"]))
            # v1.35.0：宽度阈不足 2 格时是**空操作**（网格本来就比线宽粗），
            # 不能再打印"窄于 0.22mm（0 格）"这种自相矛盾的行。
            if int(_st.get("min_width_px") or 0) >= 2:
                print("[叠色浮雕工坊] 细缝：窄于 %.2fmm（%d 格）的色条已并给"
                      "邻区 %d 格 —— 再窄切片器印不出来，会在两色之间留空隙"
                      % (_st.get("min_width_mm", 0.0),
                         _st.get("min_width_px", 0),
                         _st.get("thinned_px", 0)))
        grids = expand_mirror_stack(stack_grid, w, h, bands, key_to_mat)
        # 8 个子层：grids[0] = 正面最外(顶) ... grids[-1] = 背面最外(底)。
        # 几何：最底子层当"底面"（逐像素材质），上面 2*bands-1 层当色带。
        #
        # 【v1.37.0 / R44】兼容模式改成「错缝咬合」（替换 R43 的填白芯）。
        # surface 模式每格 8 层是同一卷耗材，颜色柱体 8 层同色 → 色界从顶面
        # 贯穿到底面。实测用户的 1.3mf：64,426 个内部分界点里 24,962 个
        # （38.7%）在全部 8 层都是分界，折合约 4.16 m 贯穿弱线 —— 沿色界一掰就裂。
        # R43 把芯层填成整片白确实断了缝，但用户实拍判定"仍有较大风险"，因为
        # 图案只剩每面 0.2mm 薄皮、两片皮之间夹白，剖面依旧是一条直墙；同时
        # 侧面多出 0.4mm 一圈白带（立面一半是白），观感上"精细度下降"。
        # 现在改成砌墙的错缝：皮不动（外观逐格不变），芯层每层把色界按棋盘
        # 翻向邻居、相位随层交替 → 相邻层色界互补，直缝被切成一齿一齿。
        # ⚠ 只对 surface 生效：solid 模式的中间层参与呈色（叠色透光），不能动。
        _core_layers = 0
        _il = {"step_px": 0, "step_mm": 0.0, "changed": 0, "layers": 0,
               "core_px": 0}
        if str(params.get("mix_model", "surface")) == "surface":
            _skin = int(C.CARD_SKIN_LAYERS)
            if 0 < _skin < bands:
                _il = interlock_core_bands(grids, _skin, pixel_mm,
                                           params.get("nozzle_mm"))
                _core_layers = len(grids) - 2 * _skin
                print("[叠色浮雕工坊] 错缝咬合：内部 %d 层（%.2fmm）逐层朝相反"
                      "方向错开 %.2fmm，改动 %d 格 —— 颜色交界不再是一条从上到"
                      "下贯通的直缝（外观不变）"
                      % (_core_layers, _core_layers * float(band_mm_c),
                         _il["step_mm"], _il["changed"]))
        MB.build_stack_card(bm, w, h, size_x, size_y, band_mm_c, band_mm_c,
                            grids[:-1], key_to_mat["W"],
                            base_mats=grids[-1],
                            progress=PG.stage(0.56, "建几何", 0.30))
        swaps = estimate_card_swaps(grids, key_to_mat["W"])
        extra = {"stack_grid": stack_grid, "stacker": stacker,
                 "band_mats": grids, "stack_bands": bands,
                 "band_mm_card": band_mm_c, "mirror_card": True,
                 "core_layers": _core_layers, "interlock": _il}
    elif mode == "single":
        # 单面叠色卡（v1.0.2，v1.0.4 定版）：结构与 card 同一个建模器，但**不做
        # 背面镜像** —— 8 层彩膜全在观察者一侧，下面直接是 0.40mm 白底板
        # （切片出来正好 4 层白，反面纯白、同时挡横向串色）。
        #
        # 为什么用透光叠色（mix_model="translucent"）而不是兼容模式：兼容模式
        # 每格只一种料、靠相邻格并排混色，暗部天生偏亮（R26-C 实测近黑端留
        # 29.3 ΔE）；用户这一版要的是"最大程度还原照片颜色"，所以强制走
        # 滤光锚点那条能把颜色叠深的链路。
        #
        # v1.0.3：上色方式从「网点抖动」改成「整块上色」。用户 2026-11-09 看
        # 单面卡的视口/实拍后指出："不是点阵的，是一层一层叠色成需要的颜色"。
        # 抖动的物理前提（相邻像素被眼睛平均）在 0.4mm 喷嘴上不成立：每个色点
        # 都被溢料包一圈，成品是彩色碎点而不是混色。整块上色 = 每格只选**一种**
        # 叠法（最近锚点），颜色干净、边界利落，代价是色阶变成几十级台阶 ——
        # 这正是叠色工艺的真实形态。
        # v1.0.4：彩膜层数固定 8（不读面板滑杆）。用户原话「不考虑叠色多少层，
        # 最大程度还原照片颜色」—— 4 层只有 35 种叠法，撑不起照片的色阶；
        # 8 层 165 种，实测色域体积从 30958 涨到 68696（+122%），6→8 之后
        # 收益饱和（8→10 只多 0.2%）。白隔离层同时从 4 层归零：0.40mm 底板
        # 本身就是 4 层白，之前等于叠了 8 层白，白比颜色还厚一倍。
        bands = int(C.SINGLE_BANDS)
        band_mm_c = float(params.get("band_mm", C.DEFAULT_BAND_MM))
        band_mm_c = max(C.MIN_BAND_MM, min(C.MAX_BAND_MM, band_mm_c))
        _wl = int(C.SINGLE_WHITE_LAYERS)
        _below = ("W",) * _wl
        _base = float(params.get("base_mm", C.SINGLE_BASE_MM) or C.SINGLE_BASE_MM)
        materials, key_to_mat, mat_meta = build_material_list(
            mat_order, palette, "W")
        stacker, stack_grid = compute_card_plan(
            grid, w, h, full_order, bands, palette=palette,
            weight_levels=float(prof.get("weight_levels", 0.0)),
            merge_below=float(prof.get("merge_below", 0.0)),
            dither_size=int(prof.get("dither", CE.DEFAULT_DITHER_SIZE)),
            white_snap=float(prof.get("white_snap", 1.01)),
            base_key="W", mirror=False,
            dither_pattern=prof.get("dither_pattern"),
            anchor_mode=params.get("anchor_mode", C.DEFAULT_ANCHOR_MODE),
            near_k_cap=int(prof.get("near_k", 0) or 0),
            max_components=int(prof.get("max_components", 0) or 0),
            dither_block=dblk,
            # v1.0.14（用户 2026-11-20）：单面平板也开放「上色方式」，
            # 不再写死整块上色；默认仍取全局默认（solid）。
            mix_model="translucent",
            color_match=params.get("color_match", C.DEFAULT_COLOR_MATCH),
            min_feature_mm=float(prof.get("min_feature_mm",
                                          C.MIN_PRINT_FEATURE_MM)),
            block_px=int(prof.get("block_px", 1) or 1),
            max_stacks=int(prof.get("max_stacks", 0) or 0),
            min_width_mm=float(prof.get("min_width_mm",
                                        C.MIN_PRINT_WIDTH_MM)),
            pixel_mm=pixel_mm, below=_below,
            base_first=bool(C.SINGLE_WHITE_AS_LAYER), pad_to=bands,
            progress=PG.stage(0.20, "逐格配色", 0.32))
        if getattr(stacker, "stats", None):
            _st = stacker.stats
            print("[叠色浮雕工坊] 单面整块上色：%d 种叠法（可打印 %d 种），"
                  "碎块合并 %d 格、并块 %d 格"
                  % (_st["unique_stacks"], len(stacker.stacks),
                     _st["merged_px"], _st.get("blocked_px", 0)))
        grids = expand_single_stack(stack_grid, w, h, bands, key_to_mat, _wl)
        MB.build_stack_card(bm, w, h, size_x, size_y,
                            0.0 if _split_flat_base else _base,
                            band_mm_c, grids, key_to_mat["W"],
                            progress=PG.stage(0.56, "建几何", 0.30))
        if _split_flat_base:
            # 白底板另建（见入口 _split_flat_base 的说明），柱体整体上抬。
            for _v in bm.verts:
                _v.co.z += _base
        swaps = estimate_card_swaps(grids, key_to_mat["W"])
        extra = {"stack_grid": stack_grid, "stacker": stacker,
                 "band_mats": grids, "stack_bands": bands,
                 "band_mm_card": band_mm_c, "mirror_card": False,
                 "single_below": _below, "single_white_mm": _wl * band_mm_c,
                 # 底板 + 白隔离垫的总高：顶点色在这条平面以下统一涂白，
                 # 否则视口翻到反面会看到正面的照片（v1.0.3 用户反馈）。
                 "single_pad_mm": _base + _wl * band_mm_c,
                 "core_layers": 0,
                 "interlock": {"step_px": 0, "step_mm": 0.0,
                               "changed": 0, "layers": 0, "core_px": 0}}
    elif mode == "mosaic":
        # 平整色块板（v1.22.0）：照片聚类 N 色 → 逐像素分区 → **顶面等高**的板。
        #
        # 【为什么不走叠色链路】叠色链路要把目标色逼近到"四色凸包"可达域（投影 +
        # 空间抖动），R26-C 实测该逼近在亮白端留 13.8 ΔE、近黑端留 29.3 ΔE 的
        # 系统性残差；而本模式打出来的**就是被分配的那个耗材色**，没有中间模型、
        # 没有逼近残差，唯一误差是聚类损失。代价是耗材槽数 = N、换色 ≈ N。
        #
        # 【几何为什么复用 build_stack_card 的 nb=1 形态】底板（base_mm，统一
        # 底色）+ 一层色块层（relief_mm，逐像素异色）→ 顶面全平。复用而不是新写，
        # 是因为那条链路已经解决两件难事：① 每色独立顶点缓存 → 界面棱恰好 2 面，
        # 合并后边界边 0；② 只在相邻材质不同处建墙（不重复建面）。
        # 最小色块合并：把面积不到 (min_mm)² 的连通色块并给相邻最大色块。判据用
        # **面积**而不是边长 —— 切片丢色的物理判据是"这条挤出线有没有人跟着走"，
        # 等价的量就是面积。min_mm = 0 直接关（不合并）。
        # ⚠ 这段构造必须与预览侧共用（``MO.plan_for_params``），否则预览里有、
        # 实物上没有（合并阈值经 pixel_mm 换算成格数，两边算错一处就分叉）。
        min_mm = float(params.get("mosaic_min_mm", C.DEFAULT_MOSAIC_MIN_MM) or 0.0)
        PG.update(0.22, "聚类成色块")
        keys, palette, key_grid, mstats = MO.plan_for_params(
            grid, w, h, pixel_mm, min_mm)
        PG.update(0.44, "合并小色块")
        merged_px = int(mstats.get("merged_px") or 0)
        if merged_px:
            print("[叠色浮雕工坊] 色块合并：小于 %.1fmm 的色块已并给相邻"
                  "大块（判据 %d 格，共改动 %d 格）"
                  % (min_mm, int(mstats.get("min_px") or 0), merged_px))

        materials, key_to_mat, mat_meta = build_material_list(
            keys + [base_key], palette, base_key)
        # 动态色键在 BASE_COLORS 里查不到 → build_material_list 的名字会退化成
        # 键本身。统一写成 "#RRGGBB"：语言无关，用户能直接照着色码找耗材。
        for _ent in mat_meta:
            if _ent["key"] in palette and _ent["key"] != base_key:
                _ent["name"] = "#" + _ent["key"]
        base_mat = key_to_mat[base_key]
        mat_grid = [[key_to_mat[k] for k in line] for line in key_grid]
        MB.build_stack_card(bm, w, h, size_x, size_y, base_mm, relief_mm,
                            [mat_grid], base_mat,
                            progress=PG.stage(0.46, "建几何", 0.40))
        swaps = estimate_card_swaps([mat_grid], base_mat)
        # 色块板的几何里没有"叠色层序"，stack 清空 —— 否则元信息会把浮雕的
        # 色带高度表印给色块板，用户照着去切片软件设换料高度（数值完全对不上）。
        stack = []
        extra = {"mosaic_keys": keys, "mosaic_stats": mstats,
                 "mosaic_grid": key_grid, "merged_px": merged_px,
                 "mosaic_min_mm": min_mm, "stack_grid": None, "stacker": None,
                 "band_mats": [mat_grid], "stack_bands": 1,
                 "band_mm_card": relief_mm, "mirror_card": False}
        if raw_grid is not None:
            extra["gamut"] = MO.gamut_report(raw_grid, grid, quant_stats)
    elif use_region:
        # 逐像素投影出叠加权重 → 逐像素独立选材质。同一个 Z 高度上可以出现
        # 不同颜色，换色发生在 XY 平面。
        #
        # R20（v1.19.0）【混色模型接入浮雕】：透光叠色模型（v1.7.9 滤光锚点，
        # C+Y=绿 / M+Y=红棕 / C+M+Y=黑的承诺）在**不透明耗材**（PLA Basic 等）
        # 上不成立 —— 顶面只露最上层色膜，规划出的"绿"印出来是青、"红棕"是
        # 品红（实测 2026-09-30：用户浮雕满屏粉/青，与原图色相完全对不上；
        # 与 R19 叠色卡翻车同根因，当时只修了卡式两条路）。
        # 默认「表面混色」改走与 region_plan 同一引擎（凸包投影 + 块抖动）：
        # 顶面 = 单膜半色调，预览即实物。代价要讲清楚：四色凸包最暗只到中灰、
        # 色片并排无叠印双重吸收，暗部照片整体被抬亮。
        #
        # ⚠ v1.24.0（R29）**推翻上面这条归因**：那次翻车不是"透光叠色在
        # 不透明耗材上不成立"，而是几何把叠层埋在了柱底 —— ``_stacked_wall_mat``
        # 让 4 片色膜贴着底板、柱顶 0.6~1.7mm 全用最外层色填充，光根本进不到
        # 叠层。改成「色片贴柱顶」后，同一张照片的顶面呈现色 dE 中位 40.10→
        # 8.91、p90 66.82→16.65（按 Palette_256 实拍标定的反射模型算）。
        # 所以 v1.24.0 起默认回到「叠层混色」（mix_model="translucent"），
        # 「单色平铺」作为不依赖耗材透光的安全档保留。
        # 三档锚点模型只在叠层混色下生效（单色平铺下顶面只有膜色半色调）。
        # 混色模型不改变几何与水密口径：单色平铺下 stack_grid 为 None，
        # 走非叠色的 build_relief 分支（layer_mats=None 路径）。
        # R7（v1.10.0）：「柱内叠色」复选框已删除 —— 透光模型下叠色链路
        # **常开**，可达色域由「锚点模式」下拉控制（基础 8 / 扩增 64 / 自由底
        # 256）。旧的 relief_stack 参数不再读取（旧脚本传了也会被忽略）。
        # 柱内叠色的基底恒为白（C.RELIEF_SUBSTRATE_KEY）：透射率模型的
        # 标定物就是"白底板 + 4 层色片"，基底若不是白，底色的滤光会把
        # 整块板染成底色调（默认底色天蓝 → 发青），模型与实打分叉。
        # 所以这条链路的材质表按白基底重建一次，底板也随即变成白的。
        substrate = C.RELIEF_SUBSTRATE_KEY
        # v1.44.0：明信片的反面板是**底板色**（默认白），它可能不在这组配色
        # 的 keys 里（例如 RYBW 组把底板设成青）—— 建表时一并并进去，
        # 否则几何里会引用一个不存在的材质下标。
        _mat_order = list(mat_order)
        if mode == "postcard" and base_key not in _mat_order:
            _mat_order.append(base_key)
        materials, key_to_mat, mat_meta = build_material_list(
            _mat_order, palette, substrate)
        base_mat = key_to_mat[substrate]
        base_key_used = base_key if mode == "postcard" else substrate
        # R20：表面混色不走锚点叠色器（stacker=None → compute_region 的
        # Mixer 凸包投影分支）。⚠ Mixer 的键集必须含白：自动配色选出的
        # keys 可能没有 W，而键集缺 W 时近白像素没有"纯白顶点"可投影
        # （white_snap 也会因 W 不在键集被跳过）→ 近白被拆成彩色混合。
        # 材质表已按 base_key 强制并入 W，这里给配色器补上同一个键。
        _mix_model = str(params.get("mix_model", "surface"))
        mix_order = full_order
        stacker = None
        if _mix_model == "surface":
            if substrate not in mix_order:
                mix_order = list(mix_order) + [substrate]
        else:
            # v1.28.0（R35）用户口径：**叠色浮雕的色彩形成与双面平板叠色一致**
            # —— 不再用单面 4 片锚点（8/64/256），改成双面卡那套镜像多集配色器
            # （mirror_anchors，35 个回文多集），几何上把「正面 4 片 + 镜像 4 片」
            # 共 8 层回文贴在柱顶。两个叠色模式从此同一套色板 / 锚点 / 呈色模型。
            #
            # v1.30.0（R37）：默认改「整块上色」—— 一像素一叠法，整图先匹配再
            # 合并碎色块；只有用户显式选「网点混色」才走旧的 CardStacker 抖动。
            _cm = str(params.get("color_match", C.DEFAULT_COLOR_MATCH))
            if _cm == "solid":
                stacker = build_stack_matcher(
                    full_order, palette, prof,
                    int(params.get("stack_bands", C.DEFAULT_STACK_BANDS)),
                    grid=grid, w=w, h=h, base_key=substrate, mirror=True,
                    pixel_mm=pixel_mm, progress=PG.stage(0.20, "逐格配色", 0.16),
                    white_outer=True)
                print("[叠色浮雕工坊] 整块上色：%d 种叠法，合并碎色块 %d 格"
                      % (stacker.stats["unique_stacks"],
                         stacker.stats["merged_px"]))
            else:
                stacker = build_card_stacker(
                    full_order, palette, prof,
                    int(params.get("stack_bands", C.DEFAULT_STACK_BANDS)),
                    base_key=substrate, mirror=True, anchor_mode=None,
                    dither_block=dblk, white_outer=True)
        z_grid, key_grid, stack_grid = compute_region(
            grid, w, h, mix_order, _build_base, relief_mm,
            brightness_weight=bw, invert=inv, gamma=gam,
            palette=palette,
            weight_levels=float(prof.get("weight_levels", 0.0)),
            merge_below=float(prof.get("merge_below", 0.0)),
            white_snap=float(prof.get("white_snap", 1.01)),
            dither_size=int(prof.get("dither", CE.DEFAULT_DITHER_SIZE)),
            dither_pattern=prof.get("dither_pattern"),
            stacker=stacker, layer_mm=C.RELIEF_LAYER_MM,
            empty_key=C.RELIEF_SUBSTRATE_KEY,
            max_components=int(prof.get("max_components", 0) or 0),
            dither_block=dblk,
            progress=PG.stage(0.36, "逐格配色与定高", 0.24))
        if _mix_model == "surface":
            print("[叠色浮雕工坊] 混色模型=表面混色：顶面按单膜半色调规划"
                  "（透光叠色的棕/绿/深色在 PLA Basic 等不透明耗材上不存在；"
                  "暗部偏亮是四色的物理上限）")
        fixed = MB.resolve_manifold_conflicts(z_grid, max_passes=40)
        if fixed:
            print("[叠色浮雕工坊] 已消解 %d 处非流形棱冲突" % fixed)
        # 顶面材质 = 堆叠的**最外层**（光最先穿过的那一片）
        mat_grid = [[key_to_mat[k] for k in line] for line in key_grid]

        layer_mats = None
        _white_sink_mm = 0.0        # v1.45.0 / R56：白色下沉了多少（0 = 没动）
        _white_px = 0
        if stack_grid is not None:
            # v1.28.0：与双面卡完全同一套展开 —— 正面 up to bands 片 + 正面的
            # 逆序回文（顶→底）。回文反转后与自身相同，所以直接喂给
            # _stacked_wall_mat（它要「底→顶」）不会错位。
            _bands = max(C.MIN_STACK_BANDS,
                         min(C.MAX_STACK_BANDS,
                             int(params.get("stack_bands",
                                            C.DEFAULT_STACK_BANDS))))
            _depth = 2 * _bands
            _need = _depth * C.RELIEF_LAYER_MM
            # 柱高必须放得下 8 片（0.8mm）—— compute_region 只按「配色器给的
            # 片数（4）」钳到 0.4mm，不改的话低位柱的色片会被裁掉。
            z_grid = [[max(float(z), float(_build_base) + _need) for z in line]
                      for line in z_grid]
            # ---- 白色下沉两层（v1.45.0 / R56）----
            # 用户口径：彩色部分要比白色部分高 2 层（0.2mm），也就是白色部分
            # 下降两层。实现上只降**空叠**格（一片色膜都没用 = 纯白留白），
            # 有 1 片膜的浅色格保持原高 —— 否则浅色区会被打成一片 0.2mm 麻点。
            # 只在浮雕明信片上生效：relief 的既有工程不能被无声改掉高度。
            if mode == "postcard" and C.POST_WHITE_SINK_MM > 0:
                _sink = float(C.POST_WHITE_SINK_MM)
                _wpx = 0
                _new = []
                for line_st, line_z in zip(stack_grid, z_grid):
                    _row = []
                    for st, z in zip(line_st, line_z):
                        # v1.0.7：空叠是 ("W",)*bands 而不是 ()，必须用统一判据
                        if stack_is_blank(st, substrate):
                            _row.append(float(z) - _sink)
                            _wpx += 1
                        else:
                            _row.append(float(z))
                    _new.append(_row)
                z_grid = _new
                _white_sink_mm = _sink
                _white_px = _wpx
            layer_mats = []
            for line in stack_grid:
                row = []
                for st in line:
                    front = list(st[:_bands])
                    while len(front) < _bands:
                        front.append(substrate)
                    full = front + front[::-1]      # 顶→底，回文
                    row.append([key_to_mat[k] for k in full])
                layer_mats.append(row)

        # ---- 边框内侧那一圈抬到正面最高处（v1.45.1 / R57）----
        # 只在浮雕明信片上做。只改高度、不改颜色也不改堆叠：一旦动颜色，
        # 按材质分出来的壳就可能不闭合（3MF 分色会出问题）。
        # 抬平之后，外侧那圈实心边框与画面齐平，不会在相框和照片之间留下
        # 一圈 1.5mm 宽、最深 1mm 的沟。
        #
        # 【v1.48.0 / R65 修正】这两行原先无条件赋值 C.POST_RIM_MM，只靠
        # 下面 `if mode == "postcard"` 挡住「抬边」这一件事 —— 但 `_rim_mm`
        # 本身已经变成 1.50 了。而默认浮雕走的就是 `use_region`，于是 relief
        # 拿到 _rim_mm=1.5 却没有建边框（build_rim 只在 postcard 分支调），
        # 最终 `res["size_mm"]` 比真实包围盒多报 3mm（实测 100mm 图报 103.0）。
        # 面板与质量报告读的是 meta，所以用户看不见；但任何按返回值排版/报价
        # 的脚本都会多算。现在把赋值本身收进 postcard。
        _is_post = (mode == "postcard")
        # v1.0.13：外框从"只有明信片"扩到**全部模式**（用户 2026-11-18：
        # "墙层数需要应用于所有模式"）。边框在公共段统一建（见 center_and_ground
        # 之前），这里只负责"抬边"这一件明信片专属的事。
        _rim_mm = float(C.rim_mm_for_layers(_rim_layers))
        _lip = (max(0, int(C.POST_RIM_LIP_CELLS)) if _is_post else 0)
        if _rim_mm > 0 and _lip > 0:
            _ztop = float(base_mm) + float(relief_mm)
            for _r in range(h):
                _edge_r = (_r < _lip) or (_r >= h - _lip)
                for _c in range(w):
                    if _edge_r or _c < _lip or _c >= w - _lip:
                        if z_grid[_r][_c] < _ztop:
                            z_grid[_r][_c] = _ztop

        # 竖墙的分段边界 = 实际出现过的所有高度值（外加 0、基座面）。
        # 必须这样：同一根竖直棱会被外墙与内墙共同使用，只有所有墙用同一组
        # 切分点，顶点才重合；否则错位成 T 型接点，产生成片的边界边。
        # v1.7.9 追加「柱内各色片的分界平面」—— 它们同样是全局共用的。
        all_z = {0.0, float(base_mm)}
        if layer_mats is not None:
            # 分界平面数按**实际出现的最深堆叠**取，不能写死 RELIEF_STACK_DEPTH：
            # 多集锚点（v1.8.0）允许同色重复，层数会超过该常量，硬编码会让最外
            # 色片的起始平面缺席 → 竖墙跨越分界平面 → 分段边界不齐、侧面色带
            # 起点偏低。实测 35 锚点链路：修复前 372 个竖墙跨越、修复后 0。
            # 取 max(..., 常量) 保证原有平面的集合不变（更浅的输入逐位等价）。
            _nmax = max((len(st) for line in layer_mats for st in line),
                        default=C.RELIEF_STACK_DEPTH)
            # v1.24.0：色片改成贴柱顶后，分界平面也必须从**各自柱顶**往下取。
            # 仍用全局平面集合（同一根竖直棱会被外墙与内墙共用，只有所有墙
            # 用同一组切分点，顶点才重合）—— 只是集合改成「每档柱高向下 n 层」。
            # 柱高已按 RELIEF_LAYER_MM 量化，档数远少于像素数，先按
            # (柱高, 层数) 去重再展开。
            _heights = set()
            for line, zl in zip(layer_mats, z_grid):
                for st, zs in zip(line, zl):
                    _heights.add((round(float(zs), 6), len(st)))
            for zs, ns in _heights:
                for k in range(1, ns + 1):
                    zc = round(zs - k * C.RELIEF_LAYER_MM, 6)
                    if zc > float(base_mm) + 1e-9:
                        all_z.add(zc)
            del _nmax
        else:
            if band_top is not None:
                all_z.add(round(float(band_top), 6))
        for line in z_grid:
            all_z.update(line)
        zb_region = sorted(all_z)

        # v1.32.0（R39）：色片以下的柱体用什么颜色。浮雕的高度场必然产生
        # 竖墙（落差），旧实现竖墙全是白基底 —— 实测 37.8% 的竖直暴露面积是
        # 白的，实物上就是"颜色交界的白缝 / 该青的地方发白"。默认改成该柱
        # 自己的最外色（视觉连续），代价是每层都要用到 2~4 种料。
        _side_own = bool(params.get("relief_side_own",
                                    C.DEFAULT_RELIEF_SIDE_OWN))

        if layer_mats is not None:
            # 柱内叠色与「共用底带」互斥：两者抢 Z 轴上同一段空间，底带会把
            # 色片盖掉。叠色链路下底带一律退回到基座面。
            MB.build_relief(bm, None, w, h, size_x, size_y, None,
                            zb_region, [base_mat], [base_mat],
                            solid_base=True, top_cap=True,
                            z_grid=z_grid, mat_grid=mat_grid,
                            base_band_mm=base_mm,
                            layer_mats=layer_mats,
                            layer_band_mm=C.RELIEF_LAYER_MM,
                            body_own=_side_own,
                            progress=PG.stage(0.62, "建几何", 0.24))
            swaps = estimate_swaps_stacked(layer_mats, z_grid, _build_base,
                                           layer_h=C.RELIEF_LAYER_MM,
                                           top_mat_grid=mat_grid,
                                           body_own=_side_own,
                                           base_mat=base_mat)
        else:
            MB.build_relief(bm, None, w, h, size_x, size_y, None,
                            zb_region, [base_mat], [base_mat],
                            solid_base=True, top_cap=True,
                            z_grid=z_grid, mat_grid=mat_grid,
                            base_band_mm=band_top,
                            progress=PG.stage(0.62, "建几何", 0.24))
            swaps = estimate_swaps(mat_grid, z_grid, _build_base,
                                   band_mm=band_mm, band_mat=base_mat)

        if _split_flat_base:
            # v1.0.15：柱体从 z=0 建（底板另建），这里整体上抬 base_mm，
            # 让柱体正好坐在白底板上 —— 总厚不变、反面变成一整块纯白。
            # z_grid 必须同步上抬：元信息 / 报告 / 探针读的都是这个数组，
            # 留在"建模坐标系"里会让读数比实物低 0.4mm。
            for _v in bm.verts:
                _v.co.z += base_mm
            z_grid = [[float(z) + float(base_mm) for z in line]
                      for line in z_grid]

        extra = {"z_grid": z_grid, "key_grid": key_grid, "mat_grid": mat_grid,
                 "stack_grid": stack_grid, "stacker": stacker,
                 "layer_mats": layer_mats, "side_own": _side_own,
                 "white_sink_mm": _white_sink_mm, "white_px": _white_px,
                 # v1.0.7：顶点色 / 色差评估都要按**实际用的**每面层数算，
                 # 不能固定按 4 层（浮雕的层数是滑杆，明信片同浮雕）。
                 "stack_bands": int(params.get("stack_bands",
                                               C.DEFAULT_STACK_BANDS))}

        if mode == "postcard":
            # ---- 反面：单色凹刻板（v1.44.0 / R54）----
            # 正面那套几何是从 z=0 长起来的；整件往下再挂一块 back_mm 的板，
            # 所以先把已有的正面顶点整体抬起来，再建反面（否则两块重叠）。
            # 反面板是**底板色**（默认白）：明信片反面就是一张白纸。
            _back = float(params.get("post_back_mm") or 0.0)
            if _back <= 0.0:
                _sp = C.thickness_split(
                    "postcard", params.get("max_thickness_mm")
                    or C.POST_FRONT_MM,
                    params.get("stack_bands", C.DEFAULT_STACK_BANDS),
                    params.get("back_depth_mm"))
                _back = float(_sp["back_mm"])
            _eng = float(params.get("post_engrave_mm") or 0.0)
            if _eng <= 0.0:
                _eng = float(params.get("back_depth_mm") or C.POST_ENGRAVE_MM)
            for _v in bm.verts:
                _v.co.z += _back

            _depth = engrave_depth_grid(
                params.get("back_image"), w, h, _eng, sampling=sampling,
                mirror=bool(params.get("back_mirror")))
            # 底板色的材质必须进材质表（可能不在配色 keys 里，例如 RYBW 组
            # 选了青底板）—— 在下面建材质表时就把它并进去。
            _plate_mat = key_to_mat[base_key]
            MB.build_engraved_back(bm, w, h, size_x, size_y, _back,
                                   _depth, _plate_mat,
                                   level_mm=C.POST_ENGRAVE_LEVEL_MM,
                                   progress=PG.stage(0.86, "反面凹刻", 0.06))
            # ---- 四周外扩的实心边框（v1.0.13：挪到公共段，全模式统一建）----
            # 明信片这里不再单独调用，否则会叠出第二圈。
            # 刻了几格按**几何实际用的量化 z** 数，不能按原始浮点深度数
            # （否则 0.01mm 的噪声也算一格，面板数字会大于真实台阶数）。
            _used = sum(1 for line in _depth for d in line
                        if C.engrave_z(d, C.POST_ENGRAVE_LEVEL_MM,
                                       _back) > 1e-9)
            # v1.0.13：正面 1.80 = 色膜 1.20 + 真起伏 0.60，一共 **18 层**
            # （用户 2026-11-18："18 层是色膜 + 起伏一共 18 层"）。
            _film = 2.0 * float(params.get("stack_bands",
                                           C.DEFAULT_STACK_BANDS)) \
                * C.RELIEF_LAYER_MM
            _vis = max(0.0, float(relief_mm) - _film)
            print("[叠色浮雕工坊] 浮雕明信片：正面 %.2fmm（色膜 %.2f + 起伏 %.2f"
                  " = %d 层，无白底板）→ 可见起伏 %.2fmm"
                  "｜反面白 %.2fmm（凹刻 ≤%.2f）｜整件 %.2fmm"
                  % (base_mm + relief_mm, _film, _vis,
                     int(round(relief_mm / C.RELIEF_LAYER_MM)), _vis,
                     _back, _eng, base_mm + relief_mm + _back))
            if _white_sink_mm > 0 and _white_px > 0:
                print("[叠色浮雕工坊] 白色留白 %d 格比彩色低 %.2fmm（%d 层）"
                      "—— 彩色区自然凸出。" % (_white_px, _white_sink_mm,
                                                C.POST_WHITE_SINK_LAYERS))
            _eff_min = C.postcard_min_front_mm(params.get("stack_bands"))
            if (base_mm + relief_mm) < _eff_min - 1e-6:
                print("[叠色浮雕工坊] 注意：正面设的 %.2fmm 放不下柱顶 %d 片"
                      " 0.1mm 色膜，实际按 %.2fmm 建（想更薄请把「叠色层数」"
                      "调小）。" % (base_mm + relief_mm,
                                    2 * int(params.get("stack_bands") or 4),
                                    _eff_min))
            if params.get("back_image") is None:
                print("[叠色浮雕工坊] 提示：没有选「反面图片」，背面是平的"
                      "（只保留 %.2fmm 背板）。" % _back)
            elif _used == 0:
                print("[叠色浮雕工坊] 提示：反面图片没有可刻的明暗差异，"
                      "背面仍是平的。")
            extra.update({"post_back_mm": _back, "post_engrave_mm": _eng,
                          "post_depth": _depth, "post_px": _used,
                          "post_back_key": base_key})

    else:
        levels = compute_levels(
            grid, w, h, full_order, stack,
            height_mode=height_mode, brightness_weight=bw,
            invert=inv, gamma=gam,
            progress=PG.stage(0.36, "逐格配色", 0.24))

        z_by_level = [layer.z_hi for layer in stack]
        MB.build_relief(bm, levels, w, h, size_x, size_y, z_by_level,
                        z_bounds, mat_by_level, side_mats,
                        solid_base=True, top_cap=True,
                        progress=PG.stage(0.62, "建几何", 0.24))

        # 色带模式下同一 Z 高度必然同材质：换色次数 = 跨越的色带数
        used = set()
        for line in levels:
            used.update(line)
        swaps = (max(0, len(used) - 1), len(used))
        extra = {"levels": levels}

    # ---- 四周竖直外框（v1.0.13：五种模式统一加，用户 2026-11-18）----
    PG.update(0.88, "加外框")
    # 位置在画面外侧 0..size_x × 0..size_y 之外，高度取**实际包围盒**
    # （浮雕/明信片的最高点各不相同，不能写死某个厚度）。
    # 顶面与模型最高处齐平、用底板色 → 四边是一整圈竖直平墙；
    # 画面本身一格不动，成品外形 = 画面 + 2×MODEL_RIM_MM。
    if _rim_mm > 0.0 and bm.verts:
        _rz0 = min(v.co.z for v in bm.verts)
        _rz1 = max(v.co.z for v in bm.verts)
        # ⚠ card / single 分支会用**自己的**底板（恒为白）重建材质表，此时
        # 用户选的 base_key（如天蓝 "C"）可能不在 key_to_mat 里 —— 直接索引
        # 会 KeyError: 'C'（r28 / r45 实测）。外框取"模型实际底板色"：
        # 表里有用户基色就用它，否则退回白，再不行退回第一个材质。
        _rim_key = base_key if base_key in key_to_mat else "W"
        MB.build_rim(bm, 0.0, size_x, 0.0, size_y, _rim_mm,
                     _rz0, _rz1, key_to_mat.get(_rim_key, 0))

    # 叠色卡（单面/双面）必须跳过 remove_doubles：成对界面靠"每色独立顶点缓存"
    # 保持每条棱恰好 2 面，焊接会把它们重新共享、制造 4 面棱（实测踩过，见
    # mesh_builder.build_stack_card 的注释）。
    # v1.16.4：relief 也走「逐色封闭壳」，壳间有重合界面 —— 与 mosaic/card 一样
    # 必须跳过 remove_doubles（焊接会把重合顶点合并，制造 4 面棱）。统一关掉 weld。
    MB.cleanup_mesh(bm, weld=False)
    # center_and_ground 会把所有顶点整体下移 min(z) —— 记下原始最低点，
    # 后面算明信片"正面/背板分界平面"要用（见 v1.45.0 的显示色分体处理）。
    _z_min_before = min((v.co.z for v in bm.verts), default=0.0)
    MB.center_and_ground(bm, size_x, size_y)

    # ---- v1.0.15：底板单独成一块实体 ----
    # 叠色浮雕 / 单面平板的柱体已从 z=0 建好（见入口 _split_flat_base），
    # center_and_ground 又把最低点归到 0 —— 这里把柱体整体抬回 base_mm，
    # 底下补一块同外形（含外框宽度）的白板。两块各自水密、只在一个平面上
    # 相贴，切片器读成两个可分别选中的部件。
    _bm_base = None
    if _split_flat_base and bm.verts:
        for _v in bm.verts:
            _v.co.z += base_mm
        _bxs = [v.co.x for v in bm.verts]
        _bys = [v.co.y for v in bm.verts]
        _bm_base = bmesh.new()
        MB.build_box(_bm_base, min(_bxs), max(_bxs), min(_bys), max(_bys),
                     0.0, float(base_mm), base_mat)

    boundary, nonmanifold, pinch = MB.count_bad_edges(bm, split=True)
    degenerate = MB.count_degenerate_faces(bm, C.EPS_AREA2)
    if _bm_base is not None:
        # 两块都要计入质量报告：底板也是实物的一部分（破洞 / 非流形同样致命）。
        _b2, _n2, _p2 = MB.count_bad_edges(_bm_base, split=True)
        boundary += _b2
        nonmanifold += _n2
        pinch += _p2
        degenerate += MB.count_degenerate_faces(_bm_base, C.EPS_AREA2)

    # 共用底带只在"逐像素材质"的链路里生效（色带叠色模式下"色带"本身就是
    # 颜色结构，没有底带的位置）。柱内叠色与它**互斥** —— 两者抢 Z 轴上同一
    # 段空间，色片会被底带盖掉，所以叠色链路下底带一律不生效，元信息也照实
    # 报 0（曾有版本报 0.75 而几何根本没用它，用户以为设了却没效果）。
    band_used = (round(band_mm, 4)
                 if (use_region and band_top and extra.get("layer_mats") is None)
                 else 0.0)

    # ---- 6. 落成对象 ----
    PG.update(0.91, "写入场景")
    obj = params.get("target_object")
    name = params.get("object_name") or "叠色浮雕"
    if obj is None:
        me = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, me)
        bpy.context.scene.collection.objects.link(obj)
    else:
        me = obj.data
        obj.name = name

    obj.data.materials.clear()
    for m in materials:
        obj.data.materials.append(m)

    # v1.25.0：实体体积（mm³）—— 耗材用量的**确定性**来源。
    # 必须在 to_mesh 之前算：几何是逐色独立封闭壳、壳间不重叠，bmesh 一次
    # 算完就是各壳体积之和。用它反推耗材克数（PLA 1.24 g/cm³）比任何拟合
    # 都可靠：200mm/2.4mm 的实测 63.9 g 与 V×1.24 相差 <3%。
    try:
        volume_mm3 = abs(float(bm.calc_volume(signed=False)))
        if _bm_base is not None:
            # v1.0.15：底板是独立对象，克数必须一并算进去（否则报少 15~30%）。
            volume_mm3 += abs(float(_bm_base.calc_volume(signed=False)))
    except Exception:
        volume_mm3 = 0.0

    # ---- v1.0.14：底板 / 色膜拆成两个对象 ----
    # 用户 2026-11-20：「与色膜层分为两个层级（便于后续切片时自由调整颜色）」。
    # 只拆全色平板与明信片浮雕（这两种有"一整块板 + 上面的色块/浮雕"的结构）。
    # 判定用 (材质 == 底板材质) 且 (面中心 z ≤ 底板顶面)：只按材质拆会把
    # 明信片正面那些白膜柱也分到底板里；加上 z 分界就干净了。
    # v1.0.15：叠色浮雕 / 单面平板的底板已在上面用 build_box 建好（_bm_base），
    # 不走这条整壳拆分，所以这里只对 mosaic / postcard 生效。
    if mode in ("mosaic", "postcard") and bm.faces:
        _sz = ((float(base_mm) if mode == "mosaic"
                else float(extra.get("post_back_mm") or 0.0)) - _z_min_before)
        if _sz > 1e-9:
            _bm_base = bmesh.new()
            _bm_film = bmesh.new()
            _bset, _fset = _shell_split_sets(bm, base_mat, _sz)
            _copy_faces(bm, _bm_base, lambda f: f in _bset)
            _copy_faces(bm, _bm_film, lambda f: f in _fset)
            if len(_bm_base.faces) and len(_bm_film.faces):
                bm.free()
                bm = _bm_film
            else:
                # 退化情形（比如 0.4mm 板被量化成同一高度）：不拆，保持单对象。
                _bm_base.free()
                _bm_base = None
                _bm_film.free()

    bm.to_mesh(me)
    bm.free()
    me.update()

    # ---- 视口显示「打出来的颜色」（v1.31.0 / R38）----
    PG.update(0.94, "视口着色")
    # 材质是按耗材分的（切片要用），所以视口只能看到四色拼贴。这里额外写一层
    # 顶点色 = 该柱顶的预测呈色；不动材质、不改 material_index（导出分色不变）。
    display_faces = 0
    if mode in ("card", "single", "relief", "postcard") \
            and extra.get("stack_grid"):
        # v1.45.0 / R56：明信片的反面是一整块**单色**背板，不能让"按 XY 格子
        # 取色"把它涂成正面的照片（用户截图：翻到底面还是那张图）。
        # 分界平面 = 背板顶面（贴合面）；center_and_ground 已整体下移，
        # 所以减去当时的最低点。反面板统一涂底板色（该卷耗材自己的颜色）。
        _split_z = None
        _split_rgb = None
        # v1.0.15：叠色浮雕的柱体被整体上抬了 base_mm（底板另建），分界平面
        # 因此要补回这个位移 —— 否则顶点色会把柱体底面也涂成正面的图案。
        _z_shift = float(base_mm) if _split_flat_base else 0.0
        if mode == "postcard" and extra.get("post_back_mm"):
            _split_z = float(extra["post_back_mm"]) - _z_min_before + _z_shift
            try:
                _srgb = CE.resolve_palette([base_key_used], palette)[base_key_used]
            except Exception:
                _srgb = (1.0, 1.0, 1.0)
            _split_rgb = (C.srgb_to_linear(_srgb[0]),
                          C.srgb_to_linear(_srgb[1]),
                          C.srgb_to_linear(_srgb[2]))
        elif mode == "relief" and _split_flat_base:
            # 用户 2026-11-20：「叠色浮雕的反面需要纯色，不是双面色膜」。
            # 反面的材质本来就是一整块白底板（独立对象），但视口顶点色是按
            # XY 格子逐面涂的 —— 翻到底面仍会看到正面的照片，看着像"两面
            # 都有色膜"。这里把底板平面以下统一涂底板色，视口与实物一致。
            _split_z = float(base_mm) - _z_min_before + _z_shift
            try:
                _srgb = CE.resolve_palette([base_key_used], palette)[base_key_used]
            except Exception:
                _srgb = (1.0, 1.0, 1.0)
            _split_rgb = (C.srgb_to_linear(_srgb[0]),
                          C.srgb_to_linear(_srgb[1]),
                          C.srgb_to_linear(_srgb[2]))
        elif mode == "single" and extra.get("single_pad_mm"):
            # v1.0.3：单面卡反面的底板 + 4 层白隔离垫统一涂白。
            # 之前只按 XY 格子取色，翻到反面仍印着正面的照片（用户
            # 2026-11-09 反馈"单面的模型在反面仍然上色了"）。
            _split_z = float(extra["single_pad_mm"]) - _z_min_before + _z_shift
            try:
                _srgb = CE.resolve_palette(["W"], palette)["W"]
            except Exception:
                _srgb = (1.0, 1.0, 1.0)
            _split_rgb = (C.srgb_to_linear(_srgb[0]),
                          C.srgb_to_linear(_srgb[1]),
                          C.srgb_to_linear(_srgb[2]))
        try:
            display_faces = apply_display_colors(
                obj, extra["stack_grid"], palette,
                mirror=(mode in ("relief", "postcard", "card")),
                solid_below_z=_split_z, solid_rgb=_split_rgb,
                # v1.45.1 / R57：明信片外扩了一圈边框，bbox 已经比画面大，
                # 必须把「画面区域」和「边框色」显式告诉着色器，否则整张图
                # 会被按 bbox 拉伸、边框还会被涂成边缘像素的颜色。
                frame_rect=((-size_x * 0.5, size_x * 0.5,
                                 -size_y * 0.5, size_y * 0.5)
                            if (mode == "postcard" and _rim_mm > 0) else None),
                frame_rgb=(_split_rgb if (mode == "postcard" and _rim_mm > 0)
                           else None),
                # 单面叠色的彩膜下方还有 4 层白垫 —— 顶点色必须按同一结构算，
                # 否则视口里比实物鲜艳（白垫的散射会把颜色拉浅一点）。
                stack_below=extra.get("single_below") or (),
                # v1.0.7：每面层数取**本次生成实际用的**（单面 8 / 双面卡 6 /
                # 浮雕与明信片按滑杆）。
                stack_bands=int(extra.get("stack_bands")
                                or C.DEFAULT_STACK_BANDS),
                # 三种镜像模式（浮雕 / 明信片 / 双面卡）白都排在彩膜外侧，
                # 视口必须与锚点、几何同一口径，否则"预览不像实物"。
                white_outer=(mode in ("relief", "postcard", "card")))
        except Exception as _exc:      # 显示层失败绝不能拖垮生成
            print("[叠色浮雕工坊] 顶点色写入失败（不影响建模与导出）：%s" % _exc)

    # ---- 挂绳孔（v1.16.3 起）----
    PG.update(0.97, "打挂绳孔")
    # 生成流程**不再自动加孔**：挂绳孔只能通过「生成挂绳孔」按钮
    # （cstack.lanyard）在已生成模型上添加，relief / mosaic / card 三种
    # 模式都支持。保留结果键以兼容面板/导出的 meta 结构。
    lanyard_info = None
    lanyard_note = ""

    # v1.25.0：颜色还原度（模型预测）。只对叠色链路算 —— mosaic 的
    # "预览=实物"另有 gamut 报告，card 的顶面是镜像回文（另一套模型）。
    # v1.28.0：②③ 都是镜像 8 层（叠色浮雕已与双面卡统一），mosaic 无此模型
    fidelity = estimate_color_fidelity(
        grid, extra.get("stack_grid"), palette,
        mirror=(mode in ("relief", "postcard", "card")),
        below=extra.get("single_below") or (),
        stack_bands=int(extra.get("stack_bands")
                        or C.DEFAULT_STACK_BANDS),
        white_outer=(mode in ("relief", "postcard", "card")))

    # v1.31.0（R38）：把「整块上色」实际生效的参数写进 meta。
    # 【为什么要写】三档现在的差别是 block_px（色块多大）与 max_stacks（色阶
    # 几种），但这两种参数在**几何结果**上不一定单调可辨（竖向并块会让换色次数
    # 反弹，见 _diag_r38i.py）。测试要钉住"参数真的接通"，就得读实际生效值，
    # 不能靠"换色次数应该递减"这种方向性推断。
    _fit = getattr(extra.get("stacker"), "stats", None) or {}
    _cf = None
    if _fit and mode in ("relief", "postcard", "card", "single"):
        _cf = {
            "block_px": int(_fit.get("block_px", 1) or 1),
            "blocked_px": int(_fit.get("blocked_px", 0) or 0),
            "max_stacks": int(_fit.get("max_stacks", 0) or 0),
            "capped": int(_fit.get("capped", 0) or 0),
            "stacks": len({s for line in (extra.get("stack_grid") or [])
                           for s in line}),
        }
    obj["cstack_meta"] = _json_dumps({
        "versions": C.ADDON_VERSION_STR,
        "mode": mode,
        "height_mode": height_mode,
        "quality": prof["key"],
        "quality_cn": prof["cn"],
        "palette": list(keys),
        "base_key": base_key_used,
        "base_key_requested": base_key,
        "band_mm": band_used,
        "grid": [w, h],
        "size_mm": [round(size_x, 3), round(size_y, 3)],
        "pixel_mm": round(pixel_mm, 3),
        "dither_block": dblk,
        "stack": [{"key": l.key, "z_lo": round(l.z_lo, 4), "z_hi": round(l.z_hi, 4)}
                  for l in stack],
        "swap_heights": [round(l.z_hi, 3) for l in stack],
        "materials": mat_meta,
        "swaps": {"estimate": swaps[0], "max_per_layer": swaps[1]},
        "stack_bands": extra.get("stack_bands", 0),
        "card_band_mm": extra.get("band_mm_card", 0.0),
        # 单面叠色（v1.0.2）：白隔离层与底板厚度，打印说明要用它报结构。
        "single_white_mm": round(float(extra.get("single_white_mm") or 0.0), 4),
        "single_base_mm": round(float(base_mm), 4),
        "sampling": sampling,
        # 平整色块板（v1.22.0）：色块配色表 + 最小色块合并 + 全色域分析。
        # 其它模式为 None —— 面板/报告按 mode 判分支，不靠"键存不存在"。
        "mosaic": ({
            "colors": list(extra.get("mosaic_keys") or []),
            "shares": (extra.get("mosaic_stats") or {}).get("shares") or {},
            "min_mm": float(extra.get("mosaic_min_mm") or 0.0),
            "merged_px": int(extra.get("merged_px") or 0),
            "gamut": extra.get("gamut") or {},
        } if mode == "mosaic" else None),
        # 浮雕明信片（v1.44.0）：反面板的实际尺寸与凹刻统计。
        "postcard": ({
            "front_mm": round(float(base_mm + relief_mm), 4),
            "back_mm": round(float(extra.get("post_back_mm") or 0.0), 4),
            "engrave_mm": round(float(extra.get("post_engrave_mm") or 0.0), 4),
            "total_mm": round(float(base_mm + relief_mm
                                    + (extra.get("post_back_mm") or 0.0)), 4),
            "engraved_px": int(extra.get("post_px") or 0),
            "back_key": extra.get("post_back_key") or "",
            "has_back_image": bool(params.get("back_image") is not None),
            "white_sink_mm": round(float(extra.get("white_sink_mm") or 0.0), 4),
            "white_px": int(extra.get("white_px") or 0),
            "rim_mm": round(float(_rim_mm), 4),
            "outer_mm": (round(size_x + 2.0 * _rim_mm, 3),
                         round(size_y + 2.0 * _rim_mm, 3)),
        } if mode == "postcard" else None),
        "quantize": (None if not quant_stats or quant_stats.get("skipped")
                     or not quant_stats.get("k")
                     else {"k": quant_stats.get("k"),
                           "clusters": quant_stats.get("k_used"),
                           "dE_mean": round(quant_stats.get("dE_mean", 0.0), 3),
                           "despeckled": quant_stats.get("despeckled", 0)}),
        "lanyard": lanyard_info,
        "stats": {"boundary": boundary, "nonmanifold": nonmanifold,
                  "degenerate": degenerate,
                  # v1.32.0（R39）：同材质的"斜对角台阶捏合边"。不算缺陷，
                  # 单独记账，免得把它当成破洞（见 count_bad_edges 的说明）。
                  "pinch": pinch},
        # v1.31.0（R38）：上色方式实际生效的参数（mosaic 或非 solid 链路为 None）
        "color_fit": _cf,
        # v1.25.0：颜色还原度（按实拍标定的反射模型预测，单位 ΔE）
        "fidelity": fidelity,
    })

    # ---- v1.0.14：底板对象（挂在主对象下，成为第二个层级）----
    # 两块几何各自水密；材质表与主对象一致（面材质索引不用重映射）。
    # 共用一份 cstack_meta → 「清除生成结果」与导出都能识别；cstack_part
    # 用来区分"别把底板当成主对象"。
    _base_obj = None
    if _bm_base is not None and len(_bm_base.faces):
        _bme = bpy.data.meshes.new(name + "_底板")
        _bm_base.to_mesh(_bme)
        _bm_base.free()
        _bme.update()
        _base_obj = bpy.data.objects.new(name + "_底板", _bme)
        bpy.context.scene.collection.objects.link(_base_obj)
        for _m in materials:
            _bme.materials.append(_m)
        # 底板**不写** cstack_meta：它不是一个独立生成结果，只是主对象的
        # 子层级。这样报告 / 挂绳孔 / 导出选对象时都不会误选它；「清除生成
        # 结果」会连同父对象的子对象一起删（见 operators.CSTACK_OT_clear）。
        # v1.0.15：但导出时要能可靠认出它（以前按名字后缀猜，改名就失效），
        # 所以打一个轻量标记。
        _base_obj["cstack_part"] = "base"
        _base_obj.parent = obj

    PG.update(1.0, "完成")
    return {
        "ok": True,
        "object": obj,
        "base_object": _base_obj,
        "mode": mode,
        # v1.45.4（R61）：内存预算与自动降档的实际结果（面板/导出报告都说这个）
        "mem": _mem,
        "height_mode": height_mode,
        "quality": prof["key"],
        "quality_cn": prof["cn"],
        "quality_profile": prof,
        "bands_mm": band_used,
        # v1.23.0：把**实际用到的**底板/起伏厚度也返回。面板只给「模型最大
        # 厚度」，这三个派生值可能被 thickness_split 钳制（card 单层 0.30 上限），
        # 探针/报告不该再去猜 props.base_mm。
        "base_mm": float(base_mm),
        "relief_mm": float(relief_mm),
        # v1.44.0：明信片的正面/反面/整件厚度（其它模式 back_mm = 0）
        "front_mm": float(base_mm + relief_mm),
        "back_mm": float(extra.get("post_back_mm") or 0.0),
        "engrave_mm": float(extra.get("post_engrave_mm") or 0.0),
        "total_mm": float(base_mm + relief_mm
                          + (extra.get("post_back_mm") or 0.0)),
        "engraved_px": int(extra.get("post_px") or 0),
        "base_key": base_key_used,
        "grid": (w, h),
        # v1.45.1 / R57：明信片四周外扩了一圈边框，**外形**比画面大 2×rim。
        # 这里报的是实物的外形尺寸（用户量的是成品），画面区域另给 image_mm。
        "size_mm": (size_x + 2.0 * _rim_mm, size_y + 2.0 * _rim_mm),
        "image_mm": (size_x, size_y),
        "rim_mm": float(_rim_mm),
        "rim_layers": int(_rim_layers),
        "pixel_mm": round(pixel_mm, 3),
        "dither_block": dblk,
        "materials": materials,
        "mat_meta": mat_meta,
        "base_layer": base_layer,
        "stack": stack,
        "boundary": boundary,
        "nonmanifold": nonmanifold,
        "pinch": pinch,
        "degenerate": degenerate,
        "vertices": len(me.vertices),
        "polygons": len(me.polygons),
        # v1.31.0：已写成顶点色的面数（0 = 该模式没有叠色呈色可显示）
        "display_faces": display_faces,
        "display_attr": DISPLAY_COLOR_ATTR if display_faces else "", 
        # v1.25.0：耗材代价的确定性口径。grams = 体积 × 1.24 g/cm³（PLA 实心），
        # 与切片器实测（200mm/2.4mm → 63.9 g）差 <3%，不含擦料塔/裙边。
        "volume_mm3": float(volume_mm3),
        "grams": float(volume_mm3) * C.PLA_DENSITY_G_CM3 / 1000.0,
        # v1.25.0：颜色还原度（ΔE 统计，None = 非叠色链路不适用）
        "fidelity": fidelity,
        "analysis": analysis,
        "palette": list(keys),
        "order": list(keys),
        "stack_bands": extra.get("stack_bands", 0),
        "card_band_mm": extra.get("band_mm_card", 0.0),
        # v1.36.0（R43）：双面卡「连接芯」实际填了几层（0 = 没填）。
        # 探针/报告读这个值，不再靠"band_mats 中间是不是全白"猜。
        # v1.37.0（R44）：芯层改成错缝咬合后 core_layers 语义是"参与错缝的层数"，
        # 另给 interlock（错开几格 / 改动几格）供报告核对。
        "core_layers": int(extra.get("core_layers", 0) or 0),
        "interlock": extra.get("interlock") or {},
        "stacker": extra.get("stacker"),
        "stack_grid": extra.get("stack_grid"),
        # v1.31.0（R38）：整块上色的实际生效参数（供探针/报告核对，不用再猜）
        "color_fit": _cf,
        "band_mats": extra.get("band_mats"),
        "full_order": full_order,
        "levels": extra.get("levels"),
        "key_grid": extra.get("key_grid"),
        "z_grid": extra.get("z_grid"),
        "swaps": swaps[0],
        "max_colors_per_layer": swaps[1],
        "sampling": sampling,
        "quant_stats": quant_stats,
        "quant_note": qnote,
        "mosaic": (extra.get("mosaic_keys") and {
            "colors": list(extra["mosaic_keys"]),
            "stats": extra.get("mosaic_stats"),
            "min_mm": extra.get("mosaic_min_mm"),
            "merged_px": extra.get("merged_px"),
            "gamut": extra.get("gamut") or {},
        }) or None,
        # v1.44.0：浮雕明信片的反面信息（非 postcard 为 None）
        "postcard": ({
            "front_mm": round(float(base_mm + relief_mm), 4),
            "back_mm": round(float(extra.get("post_back_mm") or 0.0), 4),
            "engrave_mm": round(float(extra.get("post_engrave_mm") or 0.0), 4),
            "total_mm": round(float(base_mm + relief_mm
                                    + (extra.get("post_back_mm") or 0.0)), 4),
            "engraved_px": int(extra.get("post_px") or 0),
            "back_key": extra.get("post_back_key") or "",
            "has_back_image": bool(params.get("back_image") is not None),
            "white_sink_mm": round(float(extra.get("white_sink_mm") or 0.0), 4),
            "white_px": int(extra.get("white_px") or 0),
            "rim_mm": round(float(_rim_mm), 4),
            "outer_mm": (round(size_x + 2.0 * _rim_mm, 3),
                         round(size_y + 2.0 * _rim_mm, 3)),
        } if mode == "postcard" else None),
        "lanyard": lanyard_info,
        "lanyard_note": lanyard_note,
        "seconds": time.time() - t0,
    }


def generate(params):
    """按参数字典生成模型（公共入口）。

    v1.48.0 / R65：外面这层只做一件事 —— **保证 bmesh 一定被释放**。
    `_generate_impl` 里有 500 多行几何构建，异常时 `bm.free()` 走不到。
    CPython 通常在栈帧销毁时把 BMesh 回收掉，但如果调用方把 traceback 存进
    变量（算子里的 `except ... as exc` 在函数返回前一直持有），那么大块网格
    内存就会被多留一段时间；在"生成失败 → 调参数 → 再失败"的循环里会叠加。
    这里用一层 finally 做确定性释放，不改动 `_generate_impl` 的缩进与逻辑
    （500 行重排的风险远大于这点收益）。

    重复释放是安全的：`_generate_impl` 正常路径已经 `free()` 过，
    此时 `bm.is_valid` 为 False，这里跳过。
    """
    slot = []
    PG.start("生成模型")
    try:
        return _generate_impl(params, slot)
    finally:
        PG.finish()
        for bm in slot:
            try:
                if bm.is_valid:
                    bm.free()
            except Exception:  # noqa: BLE001
                # 释放失败不能盖掉真正的生成错误
                pass


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------
# 文件名里不允许出现的字符（Windows 保留字符 + 路径分隔符）。
_BAD_STEM_CHARS = '<>:"|?*\\/'


def safe_file_stem(raw, fallback="叠色浮雕"):
    """把用户填的文件名净化为**纯文件名**（v1.48.0 / R65）。

    【为什么必须做】``file_stem`` 是面板上的自由文本框，原先直接
    ``os.path.join(out_dir, base_name + ".3mf")``。用户（或脚本）填
    ``..\\..\\x`` 就能把文件写到导出目录之外 —— 这不是安全边界被突破
    （毕竟是他自己的机器），但会让人"点了导出却找不到文件"，并可能在
    目标位置静默覆盖同名文件。

    处理顺序：先把反斜杠统一成 ``/`` 再取 basename（否则 Windows 路径的
    ``..\\..\\x`` 在 POSIX 语义下只是"文件名里带反斜杠"），然后逐字符替换
    Windows 保留字符，最后去掉首尾空格与点（Windows 不允许文件名以点结尾）。
    """
    s = str(raw if raw is not None else "").replace("\\", "/")
    s = os.path.basename(s)
    for _ch in _BAD_STEM_CHARS:
        s = s.replace(_ch, "_")
    s = s.strip().strip(". ")
    return s or fallback


def export_all(params):
    """把目标对象导出为 3MF / OBJ。返回统计 dict。"""
    obj = params["object"]
    # v1.0.14：全色平板 / 明信片浮雕的底板是子对象（导出时在下面合并）。
    out_dir = params["output_dir"]
    base_name = safe_file_stem(params.get("file_stem"))
    wanted = params.get("formats") or ["3mf", "obj"]

    os.makedirs(out_dir, exist_ok=True)

    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    # ---- v1.0.15：底板作为**独立部件**导出 ----
    # 用户 2026-11-20 第 2 条：切片后底板必须能单独选中、单独换料。v1.0.14
    # 虽然把底板拆成了子对象，但导出时又把两条几何合并成一份 —— 切片器里
    # 只看到按颜色分的 4 个部件，底板和白色膜混在一起。
    # 现在 3MF 里底板单独写成一个 object（名字「底板」），OBJ 里单独一个 o 组。
    # bambu_mode 关掉时仍合并成一个对象（那种情况本来就只输出单一几何）。
    _base_obj = None
    for _ch in obj.children_recursive:
        if _ch.type == "MESH" and _ch.data is not None and _ch.data is not me \
                and _ch.get("cstack_part") == "base":
            _base_obj = _ch
            break
    _bambu_mode = bool(params.get("bambu_mode", True))
    # 只有"按颜色拆部件 + 拓竹模式"这一条路径才把底板单列；否则（例如关掉
    # 拓竹模式、或用户不拆部件）底板必须并进主几何，不能凭空消失。
    _want_split = _bambu_mode and bool(params.get("split_by_material", True))
    _extra_parts = []
    if _base_obj is not None and _want_split:
        _bbm = bmesh.new()
        _bbm.from_mesh(_base_obj.data)
        _bv, _bt, _bmats = EX.extract_mesh_data(_bbm)
        _bbm.free()
        if _bt:
            _bmi = 0
            try:
                _bmi = int(_base_obj.data.polygons[0].material_index)
            except Exception:
                _bmi = 0
            _extra_parts.append({"name": "底板", "obj_name": "base_plate",
                                 "verts": _bv, "tris": _bt, "mat": _bmi})
    # 旧路径（bambu_mode 关 / 没有底板对象）：照旧把子对象几何合并进来。
    # 两个对象的材质表完全相同，面材质索引不需要重映射，直接追加即可。
    if not _extra_parts:
        for _ch in obj.children_recursive:
            if _ch.type == "MESH" and _ch.data is not None and _ch.data is not me:
                bm.from_mesh(_ch.data)
    # v1.37.1：去掉导出前的三角化。exporters.extract_mesh_data 本来就会把任意
    # 多边形按扇形拆三角（for i in range(1, n-1)），先三角化纯属重复劳动。
    # 实测 130 万面：省 2.3 s、3MF 小 18%，回读三角数完全一致（几何不变）。

    meta = _json_loads(obj.get("cstack_meta", "")) or {}
    mat_meta = meta.get("materials") or [
        {"key": m.name, "name": m.name, "srgb": (1.0, 1.0, 1.0)} for m in me.materials
    ]

    results = {"triangulated": True, "outputs": {}}
    # v1.38.0 拓竹模式：关掉时连"按颜色拆部件"一起关 —— 拆了部件却不写耗材槽，
    # 拓竹会把每个部件都指派到 1 号槽，用户看到的多部件其实是同一卷料，
    # 比"单件"更误导。所以两种状态各自自洽：
    #   开 → 每色一部件 + 每部件绑槽 + 写工艺参数
    #   关 → 单一对象（三角形自带材质号）+ 不写任何拓竹元数据
    bambu_mode = bool(params.get("bambu_mode", True))
    split_parts = bool(params.get("split_by_material", True)) and bambu_mode
    try:
        if "3mf" in wanted:
            path3 = os.path.join(out_dir, base_name + ".3mf")
            guide = _make_guide(obj, meta, mat_meta, params.get("mode", ""))
            results["outputs"]["3mf"] = EX.export_3mf(
                path3, bm, mat_meta, title=base_name,
                split_by_material=split_parts,
                color_guide=guide,
                bambu_mode=bambu_mode,
                extra_settings=params.get("bambu_settings"),
                extra_parts=_extra_parts)
        if "obj" in wanted:
            patho = os.path.join(out_dir, base_name + ".obj")
            # v1.39.0：OBJ 本体不存颜色，颜色只能由同名 .mtl 承载。所以只要
            # 导 OBJ 就必然写 .mtl —— 面板上那个独立开关已并入「导出 OBJ」。
            results["outputs"]["obj"] = EX.export_obj(
                patho, bm, mat_meta, write_materials=True,
                extra_parts=_extra_parts)
    finally:
        bm.free()
    return results


def _make_guide(obj, meta, mat_meta, mode):
    # 【v1.48.0 / R65】第 0 层原先恒为 None → build_color_guide 里的
    # `base_layer and ... or "基底"` 永远走兜底分支，换色说明上第 0 层印的是
    # "基底"而不是真正的底板色（meta 里明明存着 base_key）。用户没有 AMS、
    # 照着这张表手工装料时，最底层该装哪一卷就没人告诉他了。
    # 这里照 stack 的做法造一个轻量对象，键取 meta 的底板色。
    base_layer = None
    _bkey = meta.get("base_key")
    if _bkey:
        class _BL(object):
            pass
        base_layer = _BL()
        base_layer.key = _bkey
        base_layer.z_hi = 0.0
    stack = []
    for item in meta.get("stack", []):
        class _L(object):
            pass
        l = _L()
        l.key = item["key"]
        l.z_hi = item["z_hi"]
        stack.append(l)
    if mode == "mosaic":
        # 平整色块板：颜色 = 照片聚类出来的 N 个色，一个色一个耗材槽 ——
        # 没有"按高度换料"这回事（整块板只有一个色层），所以不再印高度表。
        # 真正要交代的是**色号清单**（用户得照着找耗材）与底板色。
        mo = meta.get("mosaic") or {}
        cols = list(mo.get("colors") or [])
        shares = mo.get("shares") or {}
        base = meta.get("base_key", "W")
        lines = ["叠色浮雕工坊 —— 打印说明（%s）" % C.MODE_CN.get(mode, mode), ""]
        lines.append("颜色分配：3MF 已按对象绑定耗材，切片软件自动分色，")
        lines.append("无需手工换料。本件共需 %d 卷彩色耗材 + 1 卷底板色。"
                     % len(cols))
        lines.append("")
        lines.append("色号清单（按用量从多到少，照此装槽即可）：")
        for i, k in enumerate(cols):
            lines.append("   %2d. #%s   约占 %4.1f%%"
                         % (i + 1, k, float(shares.get(k, 0.0)) * 100.0))
        lines.append("")
        lines.append("底板色：%s（%s）"
                     % (C.BASE_COLORS.get(base, {}).get("cn", base),
                        "#" + base if base not in ("W", "C", "M", "Y") else base))
        lines.append("")
        lines.append("切片要点：色块是**平顶等厚**的，没有高度信息，")
        lines.append("层高按耗材常规设置即可（推荐 0.16~0.20mm）。")
        blk = float(mo.get("min_mm") or 0.0)
        if blk > 0.0:
            lines.append("已把小于 %.1fmm 的色块并给相邻大块（共 %d 格），"
                         % (blk, int(mo.get("merged_px") or 0)))
            lines.append("避免小色斑被切片器的小特征过滤掉。")
        return "\n".join(lines) + "\n"
    if mode == "postcard":
        # 浮雕明信片：正面是叠色浮雕（颜色在柱顶 8 片色膜里），反面是一块
        # 单色底板、按反面图的明暗刻下去。要守住的两件：
        #   ① 层高 ≤ 0.1mm（否则 0.1mm 色膜被相邻层吞掉，颜色整片丢）；
        #   ② 反面凹刻最深 = 背板厚 − 0.60mm，别在切片里加"底面补偿"。
        po = meta.get("postcard") or {}
        # 底板色优先读 postcard 块：正常情况下与 cstack_meta.base_key 是同一个
        # 值（postcard 下 base_key_used 就是底板色），这里只是把口径写死 ——
        # 反面那块板读的是"底板色"，不是叠色模型的基底白。
        base = po.get("back_key") or meta.get("base_key", "W")
        lines = ["叠色浮雕工坊 —— 打印说明（%s）"
                 % C.MODE_CN.get(mode, mode), ""]
        lines.append("结构：正面叠色浮雕 %.2fmm ＋ 反面凹刻背板 %.2fmm"
                     "（凹刻 ≤%.2fmm）＝ 总厚 %.2fmm"
                     % (float(po.get("front_mm") or 0.0),
                        float(po.get("back_mm") or 0.0),
                        float(po.get("engrave_mm") or 0.0),
                        float(po.get("total_mm") or 0.0)))
        lines.append("反面板颜色：%s（单色）"
                     % C.BASE_COLORS.get(base, {}).get("cn", base))
        # v1.45.1 / R57：四周那圈外扩边框 —— 实物比设定尺寸大 2×rim，
        # 用户切 3MF 前必须知道，否则排版会撞边。
        _rim_g = float(po.get("rim_mm") or 0.0)
        if _rim_g > 0.0:
            _om = po.get("outer_mm") or ()
            lines.append("四周边框：外加 %.2fmm %s实心边框，四边为竖直平墙%s"
                         % (_rim_g,
                            C.BASE_COLORS.get(base, {}).get("cn", base),
                            ("；成品外形 %.1f × %.1f mm" % (_om[0], _om[1]))
                            if len(_om) == 2 else ""))
        # v1.45.0 / R56：白色下沉两层 —— 这是设计要求，不是模型坏了。
        if C.POST_WHITE_SINK_MM > 0:
            lines.append("白色留白：比彩色低 %.2fmm（%d 层），彩色区自然凸出。"
                         % (C.POST_WHITE_SINK_MM, C.POST_WHITE_SINK_LAYERS))
            lines.append("这是按你的要求做的台阶，不是缺料/塌陷。")
        lines.append("")
        lines.append("颜色分配：3MF 已按对象绑定耗材，切片软件自动分色，")
        lines.append("无需手工换料。")
        lines.append("")
        lines.append("切片要点：层高必须 ≤ 0.10mm（推荐 0.10mm）。")
        lines.append("层高大于 0.10mm 时，柱顶那几片 0.1mm 色膜会被相邻层吞掉，")
        lines.append("颜色整片丢失（只剩底板色）。")
        if not po.get("has_back_image"):
            lines.append("")
            lines.append("注意：本次没有指定「反面图片」，反面是平的 ——")
            lines.append("只打了 %.2fmm 的背板。" % float(po.get("back_mm") or 0.0))
        else:
            lines.append("")
            lines.append("反面凹刻：共 %d 格参与，最深 %.2fmm；"
                         "凹槽底部仍留 0.60mm 实心白层。"
                         % (int(po.get("engraved_px") or 0),
                            float(po.get("engrave_mm") or 0.0)))
            lines.append("切片器里不要开「底面补偿/首层压扁」类选项，"
                         "否则凹槽会被压浅甚至填平。")
        blk = int(meta.get("dither_block") or 1)
        if blk > 1:
            lines.append("")
            lines.append("混色说明：像素尺寸小于喷嘴线宽，已启用 %dx%d 块状"
                         % (blk, blk))
            lines.append("抖动保证色斑可打印；想更细腻请加大模型或降低分辨率。")
        return "\n".join(lines) + "\n"
    if mode in ("card", "single"):
        # 叠色卡（R18 修正）：颜色由 3MF 对象级耗材分配（AMS 自动），不存在
        # "手工按高度换料"的层表 —— 旧文本把 relief 的换色高度表印给卡片，
        # 数值与几何（band_mm×8 薄层）完全对不上，误导用户。卡片真正要守住
        # 的是**切片层高 ≤ 色片厚度**，否则薄色片在切片里被相邻层吞掉。
        band = float(meta.get("card_band_mm") or 0.0)
        lines = ["叠色浮雕工坊 —— 打印说明（%s）"
                 % C.MODE_CN.get(mode, mode), ""]
        lines.append("颜色分配：3MF 已按对象绑定耗材，切片软件自动分色，")
        lines.append("无需手工换料。")
        if band > 0.0:
            lines.append("")
            lines.append("切片要点：层高必须 ≤ 色片厚度 %.2fmm"
                         "（推荐 %.2fmm）。" % (band, min(0.08, band)))
            lines.append("层高大于色片厚度时，薄色片会被相邻层吞掉导致丢色。")
        blk = int(meta.get("dither_block") or 1)
        if blk > 1:
            lines.append("")
            lines.append("混色说明：像素尺寸小于喷嘴线宽，已启用 %dx%d 块状"
                         % (blk, blk))
            lines.append("抖动保证色斑可打印；想更细腻请加大模型或降低分辨率。")
        _st = getattr(meta.get("stacker"), "stats", None) or {}
        if int(_st.get("thinned_px") or 0) > 0:
            lines.append("")
            lines.append("细缝处理：窄于 %.2fmm 的色条已并给相邻颜色（共 %d 格）。"
                         % (float(_st.get("min_width_mm") or 0.0),
                            int(_st["thinned_px"])))
            lines.append("这些窄条比挤出线还细，印不出来就是两色之间的空隙，"
                         "成品一掰就断。")
        if mode == "single":
            _sb = float(meta.get("single_base_mm") or 0.0)
            _nb = int(meta.get("stack_bands") or 0)
            _bm = float(meta.get("card_band_mm") or 0.0)
            lines.append("")
            # v1.0.4：白隔离层归零，白底板本身就是那 4 层白。文案必须跟着改，
            # 否则会印出「0.00mm 纯白隔离层」这种让人以为配置坏了的句子。
            lines.append("结构（从正面到反面）：%d 层彩色叠色（%d×%.2fmm）"
                         " → %.2fmm 白底板（正好 %d 层白）。"
                         % (_nb, _nb, _bm, _sb, max(1, int(round(_sb / max(1e-6, _bm))))))
            lines.append("反面是一整片白，不印图；这 %.2fmm 白板同时挡住颜色"
                         "横向串色，不要删掉它 —— 删了深色会渗到隔壁浅色格里。"
                         % _sb)
        return "\n".join(lines) + "\n"
    return EX.build_color_guide(stack, base_layer, mat_meta,
                                mode_note="生成模式：%s"
                                          % C.MODE_CN.get(mode, mode))


# ---------------------------------------------------------------------------
# 预览图（把每个像素映射成它的呈现色，生成一张对照图）
# ---------------------------------------------------------------------------
def make_preview_image(grid, w, h, order, stack, name="叠色预览",
                       height_mode="coloraware", brightness_weight=1.0,
                       invert=False, gamma=1.0,
                       base_srgb=(1.0, 1.0, 1.0),
                       palette=None, weight_levels=0.0, merge_below=0.0,
                       dither_size=CE.DEFAULT_DITHER_SIZE, white_snap=1.01,
                       card_stacker=None, dither_pattern=None,
                       max_components=0, dither_block=1,
                       surface_mixer=None, mosaic_grid=None,
                       stack_grid=None, stack_mirror=False, stack_below=(),
                       stack_bands=4, white_outer=False):
    """按当前配色估算打印后的呈现色，渲染成一张 sRGB 对照图。

    分区配色走**加色平均**（色板凸包投影），色带叠色走"顶端露出顶层多少"。
    用户据此判断配色是否合理（这是唯一能"看见"结果的途径）。

    ``weight_levels`` / ``merge_below`` / ``dither_size`` 必须与生成时一致，
    否则预览与实际不符。

    v1.25.0 新增 ``stack_grid``：给出时**逐像素按真实堆叠序列**走
    ``CE.stack_present``（与几何里贴顶的 4 片色膜一一对应），预览即实物顶面。
    这是叠层混色下唯一严格成立的口径 —— 旧路径按 ``present(目标色)`` 算
    "理想混色"，R30 实测比实物偏好看 7.7 dE（中位）、73.2% 的像素差 >5。
    """
    img = bpy.data.images.get(name)
    if img is not None and tuple(img.size) != (w, h):
        bpy.data.images.remove(img)
        img = None
    if img is None:
        img = bpy.data.images.new(name, width=w, height=h, alpha=False)

    # 【必须在这里设色彩空间，不能放到写像素之后】
    # bpy.data.images.new() 建出来的是 source='GENERATED' 的图像。给这种图像赋
    # colorspace_settings.name，Blender 会用"生成参数"重建缓冲，把刚写进去的
    # 像素整片抹掉。实测（tests/t_probe3.py V2）：
    #     赋值前回读 = [0.902, 0.400, 0.200, 1.0]
    #     赋值后回读 = [0.000, 0.000, 0.000, 1.0]
    # 之前无条件赋一次同值 "sRGB"，导致预览图永远是一张纯黑图。
    # 现在改成：先设（且只在确实不同时才设），再写像素。
    if img.colorspace_settings.name != "sRGB":
        img.colorspace_settings.name = "sRGB"

    # 分区配色逐像素要跑凸包投影，这里建一次投影器贯穿整张图
    mixer = None
    if height_mode == "region":
        mixer = CE.Mixer(order, palette, weight_levels, merge_below, dither_size,
                         white_snap=white_snap, dither_pattern=dither_pattern,
                         max_components=max_components,
                         dither_block=dither_block)

    to_lin = C.srgb_to_linear
    # v1.40.0：叠层混色的预览必须与锚点/顶点色用同一个白点中性化增益
    _pgain = CE.white_neutral_gain(order, palette)
    buf = [0.0] * (w * h * 4)
    for row in range(h):
        out_row = h - 1 - row  # Blender 图像原点在左下
        gline = grid[row]
        base_i = out_row * w * 4
        for col in range(w):
            if stack_grid is not None:
                # v1.25.0「预览 = 实物顶面」：真实堆叠序列 + 同一个反射模型。
                # v1.28.0：叠色浮雕改成双面卡那套镜像 8 层（stack_mirror=True），
                # 预览必须跟着换呈色模型，否则又变成"预览一套、实物一套"。
                _seq = stack_grid[row][col] or ()
                if stack_mirror:
                    r, g, b = CE.stack_present_mirror(
                        _seq, palette, _pgain, bands=stack_bands,
                        keep_order=white_outer)
                else:
                    r, g, b = CE.stack_present(_seq, palette, _pgain,
                                               stack_below)
            elif mosaic_grid is not None:
                # 平整色块板：预览 = 被分配的那个耗材色本身 —— 这里**没有**呈色
                # 模型（不做透射积、不做凸包投影、不做抖动），所以这是全插件
                # 唯一一个"预览 = 实物"严格成立的模式。
                r, g, b = mosaic_grid[row][col]
            elif surface_mixer is not None:
                # R19 表面混色：预览 = 顶面膜色（与切片器/实物同口径）。
                # 透光模型的 present() 会承诺不透明耗材打不出的棕/绿/深色。
                k = surface_mixer.pick(gline[col], row, col)
                r, g, b = (palette or {}).get(k, (1.0, 1.0, 1.0))[:3]
            elif card_stacker is not None:
                # 色深叠色卡：呈现色 = 滤光锚点权重的加色平均（与抖动一致）
                r, g, b = card_stacker.present(gline[col])
            else:
                r, g, b = CE.color_for_pixel(
                    gline[col], order, stack, mode=height_mode,
                    brightness_weight=brightness_weight, invert=invert,
                    gamma=gamma, base_srgb=base_srgb, row=row, col=col,
                    palette=palette, weight_levels=weight_levels,
                    merge_below=merge_below, dither_size=dither_size,
                    mixer=mixer, white_snap=white_snap,
                    dither_pattern=dither_pattern)
            i = base_i + col * 4
            buf[i] = to_lin(r)
            buf[i + 1] = to_lin(g)
            buf[i + 2] = to_lin(b)
            buf[i + 3] = 1.0
    img.pixels.foreach_set(buf)
    img.update()

    # 写入校验：全黑说明缓冲被重建过（见上），此时把原因说清楚而不是静默出黑图
    probe = (img.pixels[0], img.pixels[1], img.pixels[2])
    if (max(probe) <= 1e-6
            and max(CE.clamp(v) for v in (buf[0], buf[1], buf[2])) > 1e-3):
        print("[叠色浮雕工坊] 警告：预览图像素写入未生效（缓冲被重建），"
              "结果可能显示为纯黑。请反馈该现象。")
    return img


# ---------------------------------------------------------------------------
# JSON 小工具（避免依赖外部模块）
# ---------------------------------------------------------------------------
def _json_dumps(data):
    import json
    return json.dumps(data, ensure_ascii=False)


def _json_loads(text):
    import json
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None
