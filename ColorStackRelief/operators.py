# -*- coding: utf-8 -*-
"""操作符 —— 插件所有按钮的逻辑。"""

import os
import json
import traceback

import bpy
from bpy.types import Operator

from . import constants as C
from . import i18n as I
from .core import color_engine as CE
from .core import exporters as EX
from .core import generation as GEN
from .core import image_analysis as IA
from .core import lanyard as LAN
from .core import mesh_builder as MB
from .core import mosaic as MO


# ---------------------------------------------------------------------------
# 公共助手
# ---------------------------------------------------------------------------
def _props(context):
    return context.scene.cstack


def _replace_image(props, attr, new_img):
    """把 ``props.<attr>`` 换成 new_img，并释放上一张没人用的图（v1.48.0 / R65）。

    【为什么】载入图片原先只是 `props.image = img`，旧图仍留在
    ``bpy.data.images`` 里（0 用户但不释放）。反复换图/换反面图后，内存里会
    堆一摞不再使用的整图缓冲 —— 一张 4000×3000 的图约 48MB，换十次就是 480MB，
    而这些图对象在界面上完全看不见，用户只会觉得"插件越用越卡"。

    只删 ``users == 0`` 的旧图：如果它还被别处引用（例如预览、其他对象），
    删了会变成悬空指针，那比多占点内存糟得多。
    """
    old = getattr(props, attr, None)
    setattr(props, attr, new_img)
    if old is None or old == new_img:
        return
    try:
        if old.users == 0:
            bpy.data.images.remove(old)
    except Exception:  # noqa: BLE001
        # 释放失败只是多占内存，绝不能因此让"载入图片"这个操作失败
        pass


def _has_window():
    """当前是否有**真实 GUI 窗口**（--background / 批处理脚本下没有）。

    这一步不能省：没有真实窗口时调 popup_menu 会在 C 层触发
    EXCEPTION_ACCESS_VIOLATION（段错误），Python 的 try/except 完全拦不住，
    整个 Blender 进程直接挂掉（实测退出码 11）。

    【踩过的坑】不能用 ``len(window_manager.windows) > 0`` 来判断 —— Blender 5.1.2
    在 --background 下依然报告 windows 数 = 1，但那个 window 没有真实 GUI，
    popup 照样崩。唯一可靠的判据是 ``bpy.app.background``。
    """
    if getattr(bpy.app, "background", False):
        return False
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return False
    try:
        return len(wm.windows) > 0
    except Exception:  # noqa: BLE001
        return False


def _popup(title, message, icon="ERROR"):
    if not _has_window():
        return
    def draw(menu, ctx):
        for line in message.split("\n"):
            menu.layout.label(text=line)
    try:
        bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)
    except Exception:
        pass


def _fail(op, title, message):
    op.report({"ERROR"}, message)
    _popup(title, message, icon="ERROR")
    return {"CANCELLED"}


def _dither_kw(prof, dither_block=1):
    """预览用的抖动参数（必须与生成时**完全一致**）。

    【v1.7.3 修漏】预览这条链路原先只传了 ``dither_size``，没传 ``dither_pattern``
    —— 用户把「颗粒图案」选成有序方格时，预览仍按默认蓝噪声画，预览与实际不符。
    ``generation.make_preview_image`` 的文档本来就写着"必须与生成时一致"。
    抽成一处，避免四份复制各自漂移。
    【R18】``dither_block`` 块状抖动块边长（生成侧 = ``GEN.dither_block_for``，
    按像素/线宽比自适应），两边漂移 = 预览斑点与实际色斑不一致。
    """
    return {
        "dither_size": int(prof.get("dither", CE.DEFAULT_DITHER_SIZE)),
        "dither_pattern": prof.get("dither_pattern"),
        "dither_block": int(dither_block),
    }


def _preview_dither_block(w, h, props):
    """预览链路的块状抖动块边长 —— 与生成侧同一口径（GEN.dither_block_for）。"""
    size_mm = float(props.size_mm)
    if w >= h:
        sx, sy = size_mm, size_mm * h / float(w)
    else:
        sy, sx = size_mm, size_mm * w / float(h)
    return GEN.dither_block_for(w, h, sx, sy,
                                nozzle=getattr(props, "nozzle_mm", None))


def _preview_pixel_mm(w, h, props):
    """预览链路的单格物理边长 —— 与生成侧 ``min(size_x/w, size_y/h)`` 同一算法。

    「整块上色」的碎色块合并阈值是按毫米给的，换算成格数必须用**同一个**
    pixel_mm；预览这边算错一点，预览里看着够大的色块在实物上就会被并掉。
    """
    size_mm = float(props.size_mm)
    if w >= h:
        return size_mm / float(w)
    return size_mm / float(h)


def _stack_preview_image(grid, w, h, props, prof, single=False):
    """双面叠色卡（card）的配色预览图：与 ``GEN.generate`` 同一套堆叠模型。

    【为什么抽成一处】这段原先在「分析配色」和「生成配色预览图」里各写了一份
    ``CardStacker(...)``，两份复制必然漂移（v1.7.3 的"预览漏传抖动图案"就是这么
    来的）。v1.10.0 起构造收敛到 ``GEN.build_card_stacker``（R7 抽取，与
    ``build_relief_stacker`` 同理）。锚点模式（props.anchor_mode）由共用构造
    透传 —— v1.9.0 的预览就因为自己构造漏了它，预览永远 8 锚点、生成却按所选档位。
    强制用拓竹实测色板 —— 叠色的深色能力只在分数透射率下成立。

    ⚠ v1.22.0：「单面叠色」下线后本函数只剩双面一支（``mirror=True``）。
    v1.23.0：色组跟随面板下拉（RYBW / CMYWGK / 8色MAX）；透光模型回落 CMYK。
    """
    gkey = getattr(props, "mix_group", C.DEFAULT_COLOR_GROUP)
    if gkey not in C.COLOR_GROUPS:
        gkey = C.DEFAULT_COLOR_GROUP
    # v1.29.0：不再把透光模式强制回落 CMYK —— 选 RYBW 就用 RYBW。
    # v1.0.9：预览必须跟生成同一个「计算依据」，否则预览按理论、模型按色板，
    # 用户会看到预览与实物对不上。
    pal = C.color_group_palette(
        gkey, basis=getattr(props, "color_basis", C.DEFAULT_COLOR_BASIS))
    if gkey == "cmyk" and getattr(props, "use_bambu_palette", False):
        pal.update({k: C.BAMBU_CMYK_PRESET[k] for k in C.BAMBU_CMYK_PRESET})
    keys = list(C.color_group_keys(gkey))
    dblk = _preview_dither_block(w, h, props)
    if single:
        # 单面叠色（v1.0.2）：必须与生成侧走**同一个** compute_card_plan。
        # v1.0.4：层数固定 C.SINGLE_BANDS（8），白隔离归零（0.40 底板即
        # 4 层白），与 generate 的 single 分支逐参数对齐。
        bands = int(C.SINGLE_BANDS)
        below = ("W",) * int(C.SINGLE_WHITE_LAYERS)
        _, seq = GEN.compute_card_plan(
            grid, w, h, keys, bands, palette=pal,
            weight_levels=float(prof.get("weight_levels", 0.0)),
            merge_below=float(prof.get("merge_below", 0.0)),
            dither_size=int(prof.get("dither", CE.DEFAULT_DITHER_SIZE)),
            white_snap=float(prof.get("white_snap", 1.01)),
            base_key="W", mirror=False,
            dither_pattern=prof.get("dither_pattern"),
            anchor_mode=getattr(props, "anchor_mode", C.DEFAULT_ANCHOR_MODE),
            near_k_cap=int(prof.get("near_k", 0) or 0),
            max_components=int(prof.get("max_components", 0) or 0),
            dither_block=dblk, mix_model="translucent",
            color_match=str(getattr(props, "color_match",
                                    C.DEFAULT_COLOR_MATCH)),
            min_feature_mm=float(prof.get("min_feature_mm",
                                          C.MIN_PRINT_FEATURE_MM)),
            block_px=int(prof.get("block_px", 1) or 1),
            max_stacks=int(prof.get("max_stacks", 0) or 0),
            min_width_mm=float(prof.get("min_width_mm", C.MIN_PRINT_WIDTH_MM)),
            pixel_mm=_preview_pixel_mm(w, h, props),
            below=below,
            base_first=bool(C.SINGLE_WHITE_AS_LAYER), pad_to=bands)
        return GEN.make_preview_image(
            grid, w, h, keys, None, palette=pal,
            stack_grid=seq, stack_mirror=False, stack_below=below)
    # R19：表面混色（不透明耗材）走凸包 Mixer，预览画顶面膜色（=切片器视角）；
    # 透光叠色按「上色方式」分两支（v1.30.0：整块上色 = StackMatcher、
    # 网点混色 = CardStacker）。三支都与生成共用构造，不许在预览侧另开第二份。
    # v1.0.14（用户 2026-11-20）：混色模型已固定「叠层混色」（面板删除该
    # 功能）。预览不再按 props.mix_model 分叉 —— 旧工程里存着 surface 时
    # 预览会与生成不一致，这里统一按生成口径走。
    if str(getattr(props, "color_match", C.DEFAULT_COLOR_MATCH)) == "solid":
        # v1.30.0（R37）整块上色：构造与生成侧同一入口（build_stack_matcher，
        # 含整图匹配 + 碎色块合并），再把**真实 stack_grid** 喂给预览，
        # 走 stack_present_mirror 算呈现色。
        #
        # 【为什么不能再用 card_stacker=...】那条分支调 present(目标色)，
        # 是"理想混色"的连续权重平均：颜色过渡平滑、没有离散叠法，
        # 而实物是 4 片色膜的离散组合 —— 预览必然比实物好看。R30 在浮雕
        # 侧已经踩过这个坑（实测差 7.7 dE），这里一并收敛掉。
        # v1.0.6：双面卡固定每面 6 层，且白可排到彩膜外侧 —— 与 generate 的
        # card 分支逐参数对齐。这里若还用 props.stack_bands（默认 4），
        # 预览会按 8 层结构算色，而实物是 12 层，看到的就是另一张卡。
        bands = max(C.MIN_STACK_BANDS, min(C.MAX_STACK_BANDS,
                                          C.CARD_BANDS_PER_SIDE))
        matcher = GEN.build_stack_matcher(
            keys, pal, prof, bands, grid=grid, w=w, h=h, base_key="W",
            mirror=True, pixel_mm=_preview_pixel_mm(w, h, props),
            progress=None, white_outer=True)
        return GEN.make_preview_image(
            grid, w, h, keys, None,
            palette=CE.resolve_palette(keys, pal),
            stack_grid=matcher._picks, stack_mirror=True,
            stack_bands=bands, white_outer=True)
    stacker = GEN.build_card_stacker(keys, pal, prof, bands,
                                     base_key="W", mirror=True,
                                     anchor_mode=props.anchor_mode,
                                     dither_block=dblk,
                                     white_outer=True)
    return GEN.make_preview_image(grid, w, h, keys, None, card_stacker=stacker)


def _mosaic_preview_image(grid, w, h, props, prof):
    """平整色块板（mosaic）的配色预览图 —— **不需要任何呈色模型**。

    打出来的就是被分配的那个耗材色，所以预览 = 分区网格本身。整条链路上唯一
    可能"预览一套、打印另一套"的地方是**分区构造**（聚类 + 最小色块合并），
    因此这里必须走 ``MO.plan_for_params`` —— 与 ``GEN.generate`` 同一入口、
    同一 pixel_mm 换算，合并阈值算错一格都会让预览里的色块在实物上不存在。
    """
    size_mm = float(props.size_mm)
    if w >= h:
        sx, sy = size_mm, size_mm * h / float(w)
    else:
        sy, sx = size_mm, size_mm * w / float(h)
    pixel_mm = min(sx / float(w), sy / float(h))
    keys, palette, key_grid, _st = MO.plan_for_params(
        grid, w, h, pixel_mm,
        float(getattr(props, "mosaic_min_mm", 0.0) or 0.0))
    mgrid = [[palette[k] for k in line] for line in key_grid]
    return GEN.make_preview_image(grid, w, h, keys, None, mosaic_grid=mgrid)


def _mosaic_plan_for(props, grid, w, h):
    """给面板/分析用的分区结果 —— 与生成侧同一入口（``MO.plan_for_params``）。"""
    size_mm = float(props.size_mm)
    if w >= h:
        sx, sy = size_mm, size_mm * h / float(w)
    else:
        sy, sx = size_mm, size_mm * w / float(h)
    pixel_mm = min(sx / float(w), sy / float(h))
    return MO.plan_for_params(grid, w, h, pixel_mm,
                              float(getattr(props, "mosaic_min_mm", 0.0) or 0.0))


def _mosaic_n(props):
    """当前设置的色块数（钳到合法区间）。"""
    n = int(getattr(props, "color_count", C.DEFAULT_COLOR_COUNT) or 0)
    return max(C.MIN_COLOR_COUNT, min(C.MAX_COLOR_COUNT, n))


def _mosaic_analysis_lines(props, grid, raw, w, h, qstats):
    """平整色块板的「色块 + 全色域」分析文本。

    分四段：① 要几卷耗材；② 色号清单（用户照着装槽）；③ 碎块合并情况；
    ④ 全色域分析（照片原色 vs N 色还原的误差分布 + 色彩构成）+ 建议色块数。
    """
    keys, _palette, _kg, st = _mosaic_plan_for(props, grid, w, h)
    # v1.0.14：底板色固定白（功能已删），分析文案照实报白。
    base_cn = I.t(C.BASE_COLORS.get("W", {}).get("cn", "白色"))
    shares = st.get("shares") or {}
    out = [I.t("色块分析（平整色块板）"),
           I.t("需要耗材：%d 卷彩色 + 1 卷底板色（%s）") % (len(keys), base_cn),
           I.t("换色次数约 %d 次（每层按色块数走一遍）")
           % max(0, len(keys) - 1),
           "",
           I.t("色号清单（按用量从多到少，照此装槽即可）：")]
    for i, k in enumerate(keys):
        out.append("   %2d. #%s   %5.1f%%" % (i + 1, k,
                                              float(shares.get(k, 0.0)) * 100.0))
    out.append("")
    out.append(I.t("最小色块 %.1f mm（判据 %d 格）｜已合并 %d 格")
               % (float(st.get("min_mm") or 0.0), int(st.get("min_px") or 0),
                  int(st.get("merged_px") or 0)))
    g = MO.gamut_report(raw, grid, qstats) if raw is not None else {}
    if g:
        out.append("")
        out.append(I.t("全色域分析（照片原色 vs %d 色还原）：") % len(keys))
        out.append(I.t("   色差 ΔE：中位 %.2f ｜ p90 %.2f ｜ 最大 %.2f")
                   % (g.get("dE_med", 0.0), g.get("dE_p90", 0.0),
                      g.get("dE_max", 0.0)))
        out.append(I.t("   覆盖率：ΔE≤1 %.0f%% ｜ ≤2 %.0f%% ｜ ≤3 %.0f%% ｜ ≤5 %.0f%%")
                   % (g.get("cover_1", 0.0) * 100.0, g.get("cover_2", 0.0) * 100.0,
                      g.get("cover_3", 0.0) * 100.0, g.get("cover_5", 0.0) * 100.0))
        out.append(I.t("   色彩构成：亮度中位 L*%.0f（p10 %.0f / p90 %.0f）")
                   % (g.get("lab_L_med", 0.0), g.get("lab_L_p10", 0.0),
                      g.get("lab_L_p90", 0.0)))
        out.append(I.t("   彩度中位 %.1f ｜ 中性色（灰黑白）占 %.0f%%")
                   % (g.get("lab_chroma_med", 0.0),
                      g.get("neutral_frac", 0.0) * 100.0))
        out.append(I.t("   量化：%d 种目标色 → %d 簇（平均损失 %.2f ΔE）")
                   % (int(g.get("unique_in", 0)), len(keys),
                      g.get("quant_dE_mean", 0.0)))
        hist = g.get("hue_hist") or []
        if hist:
            names = MO.hue_hist_labels()
            top = sorted(range(len(hist)), key=lambda i: -hist[i])[:4]
            out.append(I.t("   主色调（色相分布前四）：%s")
                       % "，".join("%s° %.0f%%" % (names[i], hist[i] * 100.0)
                                   for i in top))
    sug = MO.suggest_color_count(raw) if raw is not None else None
    if sug:
        out.append("")
        out.append(I.t("建议色块数：%d（再加色的覆盖率增益已 <0.5 个百分点）") % sug)
    return out


def _show_in_image_editor(img):
    """把图像编辑器切到指定图像（没有 GUI / 没有编辑器就静默跳过）。"""
    try:
        if bpy.context.screen is not None:
            for area in bpy.context.screen.areas:
                if area.type == "IMAGE_EDITOR":
                    area.spaces.active.image = img
                    break
    except Exception:
        pass


def _relief_preview_image(grid, w, h, props, prof, keys):
    """分层浮雕（分区配色 + 柱内叠色）的配色预览图。

    R20（v1.19.0）起浮雕分区配色同样接「混色模型」：
      * 表面混色（默认）→ ``build_surface_mixer`` 凸包投影，预览画**顶面膜色**
        （= 切片器/实物口径）。⚠ 两个必须：① 键集补白 —— 自动配色的 keys 可能
        不含 W，缺 W 则近白像素没有纯白顶点可投影（与生成侧同一处理）；②
        色板必须先 ``resolve_palette`` 再传入 —— ``make_preview_image`` 的
        surface 分支按 ``(palette or {}).get(k, 白)`` 取膜色，浮雕自动配色时
        palette 为 None，不解析会把整张预览画成白图。
      * 透光叠色 → ``build_relief_stacker`` 原链路（呈色 = 透射积加色平均），
        只在半透明耗材上与实物一致。

    【必须共用构造】配色顺序与配色器都由 ``GEN`` 提供（``expand_order`` +
    ``build_relief_stacker`` / ``build_surface_mixer``），否则预览与实际会
    分叉 —— 尤其是基底色：生成侧把叠色基底固定成白（``C.RELIEF_SUBSTRATE_KEY``），
    预览若还用「底色」构造，预览里的白/绿就都不是真会打出来的颜色。

    ⚠ v1.25.0（R30）：叠层混色分支**不再**用 ``stacker.present(目标色)``。
    那是"理想混色"（连续权重），而实物是 4 片离散色膜贴柱顶。R30 实测两者
    直接差 7.7 dE（中位）、73.2% 的像素 >5 dE —— 预览比实物好看一截，等于
    换了个地方重演 R29 的"预览与实物不一致"。现在改成：跑一遍与
    ``generate`` 完全相同的 ``compute_region`` 规划，拿真实 ``stack_grid``
    走同一个 ``CE.stack_present``。色板决议也收敛到 ``GEN.geometry_palette``。
    """
    params = props.as_params()
    pal = GEN.geometry_palette(params)
    order = CE.expand_order(keys)
    dblk = _preview_dither_block(w, h, props)
    if getattr(props, "mix_model", "surface") == "surface":
        # 键集补白（与生成侧 mix_order 同一处理）后再解析色板
        sub = C.RELIEF_SUBSTRATE_KEY
        if sub not in order:
            order = list(order) + [sub]
        mixer = GEN.build_surface_mixer(order, pal, prof, dither_block=dblk)
        return GEN.make_preview_image(grid, w, h, order, None,
                                      palette=CE.resolve_palette(order, pal),
                                      surface_mixer=mixer)
    # v1.28.0：浮雕与双面卡共用同一套镜像多集配色器（mirror_anchors）。
    # 预览必须与生成同参 —— 这里改回单面锚点就会重新分叉。
    # v1.30.0（R37）：默认「整块上色」走 StackMatcher（最近叠法 + 合并碎色块），
    # 只有显式选「网点混色」才回到 CardStacker 的抖动链路。
    if str(getattr(props, "color_match", C.DEFAULT_COLOR_MATCH)) == "solid":
        stacker = GEN.build_stack_matcher(
            order, pal, prof, props.stack_bands, grid=grid, w=w, h=h,
            base_key=C.RELIEF_SUBSTRATE_KEY, mirror=True,
            pixel_mm=_preview_pixel_mm(w, h, props), progress=None,
            white_outer=True)
    else:
        stacker = GEN.build_card_stacker(order, pal, prof, props.stack_bands,
                                         base_key=C.RELIEF_SUBSTRATE_KEY,
                                         mirror=True, anchor_mode=None,
                                         dither_block=dblk,
                                         white_outer=True)
    pal_resolved = CE.resolve_palette(order, pal)
    _zg, _kg, sgrid = GEN.compute_region(
        grid, w, h, order,
        float(params.get("base_mm", C.DEFAULT_BASE_MM)),
        float(params.get("relief_mm", C.DEFAULT_RELIEF_MM)),
        brightness_weight=float(params.get("brightness_weight", 1.0)),
        invert=bool(params.get("invert", False)),
        gamma=float(params.get("gamma", 1.0)),
        palette=pal_resolved,
        weight_levels=float(prof.get("weight_levels", 0.0)),
        merge_below=float(prof.get("merge_below", 0.0)),
        white_snap=float(prof.get("white_snap", 1.01)),
        dither_size=int(prof.get("dither", CE.DEFAULT_DITHER_SIZE)),
        dither_pattern=prof.get("dither_pattern"),
        stacker=stacker, layer_mm=C.RELIEF_LAYER_MM,
        empty_key=C.RELIEF_SUBSTRATE_KEY,
        max_components=int(prof.get("max_components", 0) or 0),
        dither_block=dblk)
    return GEN.make_preview_image(grid, w, h, order, None,
                                  palette=pal_resolved, stack_grid=sgrid,
                                  stack_mirror=True,
                                  stack_bands=int(props.stack_bands),
                                  white_outer=True)


def _fit_output_size(image, res):
    """按图片宽高把目标分辨率换算成采样网格 (ow, oh)。

    【为什么必须有 ensure_image + 显式防御】图片文件失联、选到「渲染结果 /
    查看器节点」这类空图像、或图片尚未加载完时，``image.size`` 返回 (0, 0)，
    直接拿它当分母会抛 ``float division by zero``（2026-09-13 实机报错）。
    这里先 ensure_image 尝试重载并给出中文指引，防御失败再抛可读的
    ValueError，而不是让裸的 ZeroDivisionError 冒给用户。
    """
    image = IA.ensure_image(image)
    iw, ih = int(image.size[0]), int(image.size[1])
    if iw <= 0 or ih <= 0:
        raise ValueError(I.t("图片没有有效像素（%d × %d），请重新载入图片。")
                         % (iw, ih))
    if iw >= ih:
        return res, max(C.MIN_RES, int(round(res * ih / float(max(1, iw)))))
    return max(C.MIN_RES, int(round(res * iw / float(max(1, ih))))), res


def _info(op, message):
    op.report({"INFO"}, message)


def _is_ascii(path):
    try:
        str(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _default_export_dir():
    """导出目录的兜底值：桌面「叠色浮雕工坊文件夹」。

    【v1.7.7 改口径】此前这里是"若已打开 .blend 就落在 .blend 旁边，否则桌面"。
    于是同一个插件在不同会话里会把文件吐到两个地方：用户存过工程文件之后，
    导出的 3MF/OBJ 就悄悄跑到工程目录去了，桌面那个文件夹反而空了。
    用户要求"后续所有生成的文件都放在桌面的叠色浮雕工坊文件夹" —— 现在这条
    路径是**唯一**的默认落点，与 .blend 在哪、有没有打开工程都无关。
    （面板上的「导出目录」仍可手改，那是用户显式选择，当然尊重。）
    """
    return C.DEFAULT_EXPORT_DIR


def _focus_object(obj):
    """把视图对准生成结果（纯装饰，失败绝不能影响主流程）。"""
    try:
        for o in bpy.context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
    except Exception:
        pass
    try:
        if bpy.context.screen is not None:
            for area in bpy.context.screen.areas:
                if area.type == "VIEW_3D":
                    for space in area.spaces:
                        if space.type == "VIEW_3D":
                            space.shading.type = "MATERIAL"
                            space.overlay.show_overlays = True
                    break
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 载入图片
# ---------------------------------------------------------------------------
class CSTACK_OT_load_image(Operator):
    bl_idname = "cstack.load_image"
    bl_label = "载入图片"
    bl_description = "从磁盘读取图片作为叠色建模的输入"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = _props(context)
        raw = props.image_path
        if not raw:
            return _fail(self, I.t("载入图片"), I.t("请先选择一张图片文件。"))
        path = bpy.path.abspath(raw)
        try:
            img = IA.load_image_from_path(path)
        except Exception as exc:  # noqa: BLE001
            return _fail(self, I.t("载入图片失败"), "%s" % exc)
        _replace_image(props, "image", img)
        try:
            w, h = int(img.size[0]), int(img.size[1])
        except Exception:
            w, h = 0, 0
        props.status_text = I.t("已载入图片：%s（%d × %d）") % (img.name, w, h)
        _info(self, I.t("已载入 %s") % img.name)
        return {"FINISHED"}


class CSTACK_OT_load_back_image(Operator):
    """浮雕明信片的反面图（v1.44.0 / R54）。与正面图完全分开的两份数据。"""

    bl_idname = "cstack.load_back_image"
    bl_label = "载入反面图片"
    bl_description = "从磁盘读取明信片反面的图片（文字 / 邮戳 / 二维码）"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = _props(context)
        raw = props.back_image_path
        if not raw:
            return _fail(self, I.t("载入反面图片"),
                         I.t("请先选择一张图片文件。"))
        path = bpy.path.abspath(raw)
        try:
            img = IA.load_image_from_path(path)
        except Exception as exc:  # noqa: BLE001
            return _fail(self, I.t("载入反面图片失败"), "%s" % exc)
        _replace_image(props, "back_image", img)
        try:
            w, h = int(img.size[0]), int(img.size[1])
        except Exception:
            w, h = 0, 0
        props.status_text = I.t("已载入反面图片：%s（%d × %d）") % (img.name, w, h)
        _info(self, I.t("已载入反面图 %s") % img.name)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# 分析配色
# ---------------------------------------------------------------------------
class CSTACK_OT_analyze(Operator):
    bl_idname = "cstack.analyze"
    bl_label = "分析图片配色"
    bl_description = "统计图片的颜色构成，推荐用哪几种颜色参与配色，并显示各色用量占比"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = _props(context)
        if props.image is None:
            return _fail(self, I.t("分析配色"), I.t("请先指定一张图片。"))

        try:
            res = max(C.MIN_RES, min(C.MAX_RES, int(props.resolution)))
            ow, oh = _fit_output_size(props.image, res)
            params = props.as_params()
            is_mosaic = (props.mode == "mosaic")
            # 色块板的分析要拿"照片原始色 vs N 色还原色"逐格比（误差分布才是
            # 观感来源），所以走带原始网格的入口；其余模式走 4 元组入口。
            if is_mosaic:
                grid, w, h, qstats, raw = GEN.prepare_grid_raw(
                    props.image, ow, oh, params)
            else:
                grid, w, h, qstats = GEN.prepare_grid(
                    props.image, ow, oh, params)
                raw = None
            keys, prof, info = ((None, None, None) if is_mosaic
                                else GEN.resolve_palette(grid, params))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return _fail(self, I.t("分析配色失败"), "%s" % exc)

        if is_mosaic:
            lines = _mosaic_analysis_lines(props, grid, raw, w, h, qstats)
            props.detail_text = "\n".join(lines)
            props.status_text = I.t("色块分析完成：%d 个色块。") % _mosaic_n(props)
            _popup(I.t("色块与全色域分析"), "\n".join(lines), icon="INFO")
            try:
                props.preview_image = _mosaic_preview_image(grid, w, h, props, prof)
            except Exception:
                pass
            return {"FINISHED"}

        if info.get("group"):
            # 四档混色组（v1.23.0）：色组是硬约束，没有"用量占比 / 色相覆盖"
            # 这类自动选色的指标可讲 —— 直接说清"这组是几卷、哪几卷"。
            g = C.COLOR_GROUPS[info["group"]]
            lines = [I.t("配色分析（%s 等级）") % prof["cn"]]
            lines.append(I.t("混色色组：%s，固定 %d 卷耗材")
                         % (I.t(g["cn"]), len(keys)))
            lines.append(I.t("这 %d 卷：%s")
                         % (len(keys),
                            "、".join(I.t(C.BASE_COLORS[k]["cn"]) for k in keys)))
            lines.append(I.t("色组由你选定：买了几卷什么色，就按那几卷算"))
            lines.append("")
            lines.append(info.get("note", ""))
        else:
            shares = info.get("shares", {})
            lines = [I.t("配色分析（%s 等级）") % prof["cn"]]
            lines.append(I.t("各色用量占比（仅供参考，选色不按用量）："))
            for k in keys:
                cn = I.t(C.BASE_COLORS[k]["cn"])
                lines.append(I.t("   %s：%.1f%%") % (cn, shares.get(k, 0.0) * 100.0))
            lines.append("")
            lines.append(I.t("实际使用：%s（%d 种）")
                         % (" + ".join(I.t(C.BASE_COLORS[k]["cn"]) for k in keys),
                            len(keys)))
            lines.append(I.t("可用色上限：%d 种（三档都是 4 色）") % prof["max_palette"])
            wl = prof.get("weight_levels", 0.0)
            lines.append(I.t("混色档位：%s")
                         % (I.t("连续（最准）") if not wl
                            else I.t("1/%.0f（色相接近就并档，少换色）") % round(1.0 / wl)))
            lines.append(I.t("色相覆盖估算：%.0f%%") % (info.get("coverage") or 0.0))
            lines.append("")
            lines.append(info.get("note", ""))

        props.detail_text = "\n".join(lines)
        props.status_text = I.t("配色分析完成：使用 %s 共 %d 种颜色。") % (
            "、".join(I.t(C.BASE_COLORS[k]["cn"]) for k in keys), len(keys))
        _popup(I.t("配色分析"), "\n".join(lines), icon="INFO")

        # 顺带刷新一张配色预览图（纯附加功能，失败不影响主流程）
        try:
            if props.mode in ("card", "single"):
                props.preview_image = _stack_preview_image(
                    grid, w, h, props, prof, single=(props.mode == "single"))
            elif props.mode == "postcard" or props.height_mode == "region":
                # 分层浮雕（分区配色）：预览必须走锚点模型（R7 起叠色常开），
                # 否则"所见"与"所打"分叉（旧的凸包投影口径会把暗部画成紫灰）
                props.preview_image = _relief_preview_image(
                    grid, w, h, props, prof, keys)
            else:
                _, stack = CE.build_stack(keys, None, props.base_mm,
                                          props.relief_mm,
                                          base_key=props.base_key)
                props.preview_image = GEN.make_preview_image(
                    grid, w, h, keys, stack,
                    height_mode=props.height_mode,
                    brightness_weight=props.brightness_weight,
                    invert=props.invert, gamma=props.gamma,
                    weight_levels=float(prof.get("weight_levels", 0.0)),
                    merge_below=float(prof.get("merge_below", 0.0)),
                    max_components=int(prof.get("max_components", 0) or 0),
                    **_dither_kw(prof,
                                 dither_block=_preview_dither_block(w, h, props)))
        except Exception:
            pass
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# 生成模型
# ---------------------------------------------------------------------------
class CSTACK_OT_generate(Operator):
    bl_idname = "cstack.generate"
    bl_label = "生成模型"
    bl_description = "读取图片，分析颜色，按叠色原理生成可打印的 3D 模型"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = _props(context)
        if props.image is None:
            return _fail(self, I.t("生成模型"),
                         I.t("请先指定一张图片（可在上方选择或点“载入图片”）。"))

        params = props.as_params()
        try:
            result = GEN.generate(params)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return _fail(self, I.t("生成失败"), "%s" % exc)

        obj = result["object"]
        _focus_object(obj)

        # v1.31.0：模型已写入顶点色 = 打出来的颜色。把视口切过去，否则用户
        # 看到的还是四卷耗材的本色拼贴（白/青/品红/黄），根本判断不了像不像。
        if result.get("display_faces"):
            try:
                GEN.show_true_colors(context)
            except Exception:  # noqa: BLE001  纯显示，失败不影响结果
                pass

        # v1.25.0：把"这一版打出来要多少料"摆到面板上（确定性口径）。
        # 用户之前要跑到切片器里才发现 200mm/2.4mm 要 10 小时——参数和代价
        # 脱节。这里只在生成后回写，预览/分析不动它（避免显示上一次的数字）。
        try:
            props.last_grams = float(result.get("grams", 0.0))
            props.last_polys = int(result.get("polygons", 0))
            props.last_swaps = int(result.get("swaps", 0))
            props.last_volume_cm3 = float(result.get("volume_mm3", 0.0)) / 1000.0
        except Exception:  # noqa: BLE001  纯展示，失败不影响生成
            pass

        props.status_text = (
            I.t("已生成：%s｜网格 %d×%d｜顶点 %d｜面 %d｜用时 %.2f 秒")
            % (C.ADDON_NAME, result["grid"][0], result["grid"][1],
               result["vertices"], result["polygons"], result["seconds"])
        )
        defects = []
        if result["boundary"]:
            defects.append(I.t("边界边 %d") % result["boundary"])
        if result["nonmanifold"]:
            defects.append(I.t("非流形边 %d") % result["nonmanifold"])
        if result["degenerate"]:
            defects.append(I.t("零面积面 %d") % result["degenerate"])
        if defects:
            props.detail_text = I.t("⚠ 网格存在缺陷：") + "、".join(defects)
            props.detail_text += I.t(
                "\n建议降低采样精度或调整叠色层厚度后重试。")
        else:
            props.detail_text = I.t(
                "✓ 网格检查通过：水密、无边界边、无非流形边、无零面积面。")
        # v1.32.0（R39）：「同材质台阶对角相碰」的棱不计入缺陷（它不产生孔洞，
        # 每色壳仍封闭、边界边 0），但如实报出来 —— 打开"侧面跟着自己的颜色"
        # 后这类棱会变多（同一张图 54 → 171 条），不说明会被当成新毛病。
        if result.get("pinch"):
            props.detail_text += I.t(
                "\n说明：另有 %d 条棱属于「同色台阶斜对角相碰」，不产生孔洞、"
                "每色壳仍封闭，切片不受影响。") % result["pinch"]
        if result.get("lanyard_note"):
            props.detail_text += "\n" + result["lanyard_note"]
        if result.get("display_faces"):
            props.detail_text += I.t(
                "\n已切到「打出来的颜色」显示：视口里就是成品顶面的颜色"
                "（视图变换 Standard）。要回到按耗材分色的视图，"
                "把 3D 视图右上角的着色方式改回 Material。")
        elif result.get("lanyard"):
            _l = result["lanyard"]
            props.detail_text += (
                "\n" + I.t("挂绳孔") + "：%s（%.1f / %.1f mm）"
                % (I.t("外置挂绳孔") if _l["kind"] == "external"
                   else I.t("内置挂绳孔"),
                   _l["inner_r"], _l["outer_r"]))
        # v1.7.3：量化摘要（做了什么、损失多少），让"颜色量化"这个参数看得见
        _q = result.get("quant_note") or ""
        if _q:
            props.detail_text += "\n" + _q
        if result.get("sampling") == "nearest":
            props.detail_text += "\n" + I.t("采样方式：最近邻（线稿 / 像素画适用）")

        # v1.0.17（用户 2026-11-22）：**生成结果不再输出任何内存提示**。
        # 内存预算是按本机物理内存估的，换台机器结论就变；提示既不可操作，
        # 还会让人误以为自己的机器不行。耗时/内存的代价改由「分辨率」的
        # 悬停文字统一说清。res["mem"] 里的估算值仍保留（调试用），不展示。

        _info(self, props.status_text)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------
class CSTACK_OT_export(Operator):
    bl_idname = "cstack.export"
    bl_label = "导出可打印文件"
    bl_description = "把生成的模型导出为 3MF / OBJ"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = _props(context)

        obj = None
        for o in context.selected_objects:
            if "cstack_meta" in o.keys():
                obj = o
                break
        if obj is None:
            for o in bpy.data.objects:
                if "cstack_meta" in o.keys():
                    obj = o
                    break
        if obj is None:
            return _fail(self, I.t("导出失败"),
                         I.t("还没有生成模型，请先点击“生成模型”。"))

        # v1.16.2：导出目录不再预填默认路径（面板默认留空）。为空时落到桌面
        # 「叠色浮雕工坊文件夹」兜底，但不写回面板 —— 保持用户看到的空白状态。
        out_dir = props.export_dir or _default_export_dir()
        out_dir = bpy.path.abspath(out_dir)
        # v1.7.5：目录缺失时自动创建（默认目录就是桌面「叠色浮雕工坊文件夹」，
        # 首次导出即建好，用户不必手动建目录）
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            return _fail(self, I.t("导出失败"),
                         I.t("无法创建导出目录：\n%s\n%s") % (out_dir, exc))

        formats = []
        if props.export_3mf:
            formats.append("3mf")
        if props.export_obj:
            formats.append("obj")
        if not formats:
            return _fail(self, I.t("导出失败"),
                         I.t("请至少勾选一种导出格式（3MF 或 OBJ）。"))

        try:
            res = GEN.export_all({
                "object": obj,
                "output_dir": out_dir,
                "file_stem": props.file_stem or "叠色浮雕",
                "formats": formats,
                "split_by_material": props.split_by_material,
                "mode": props.mode,
                # v1.38.0 拓竹模式：关掉时**不传**任何覆盖层，导出器连
                # model_settings / project_settings 都不会写。
                "bambu_mode": bool(props.bambu_mode),
                "bambu_settings": (props.bambu_process_overrides()
                                   if props.bambu_mode else None),
            })
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return _fail(self, I.t("导出失败"), "%s" % exc)

        outs = res.get("outputs", {})
        lines = []
        for key, info in outs.items():
            lines.append("%s → %s" % (key.upper(), info.get("path", "")))
            lines.append(I.t("   顶点 %d / 三角面 %d")
                         % (info.get("vertices", 0), info.get("triangles", 0)))
        warnings = []

        # v1.38.0：把"拓竹模式到底做了什么"写清楚 —— 用户要能一眼看出
        # 这次导出带没带耗材分配与切片参数。
        _b3 = outs.get("3mf")
        if _b3 is not None:
            if _b3.get("bambu_mode"):
                # v1.0.15：墙层数旋钮已按用户要求删除（外框层数接管了"墙"的
                # 职责），覆盖层不再写 wall_loops —— 报告也就不能再声称"墙 N 圈
                # 已写入"，否则是假话。现在只说固定项。
                lines.append(
                    I.t("拓竹模式：开 — %d 个颜色部件，每个绑一个耗材槽；"
                        "层高 %s mm 等固定参数已写入；墙圈数用切片器预设")
                    % (int(_b3.get("objects", 0)),
                       C.BAMBU_FIXED["layer_height"]))
            else:
                lines.append(I.t("拓竹模式：关 — 只输出几何与颜色，"
                                 "不写耗材分配、不写切片参数"))
                warnings.append(I.t("⚠ 拓竹模式已关闭：3MF 里没有耗材槽分配，"
                                    "切片器会按单色处理，多色需要你自己配。"))

        for key, info in outs.items():
            if info.get("bytes", 0) <= 0:
                warnings.append(I.t("⚠ %s 文件大小为 0，请检查。") % key.upper())

        # v1.39.0：OBJ 材质不再是可关的开关 —— 颜色只能存在同名 .mtl 里。
        # 中文目录 + 材质行会踩拓竹 OBJ 导入器的递归崩溃，所以这里必须提示。
        _bo = outs.get("obj")
        if _bo is not None and _bo.get("bambu_risk"):
            warnings.append(I.t("⚠ 导出目录含中文：拓竹导入这个 OBJ 可能闪退。"
                                "OBJ 的颜色存在同名 .mtl 里，要带颜色就换成英文"
                                "目录；或者直接用 3MF（不受影响）。"))

        props.status_text = I.t("导出完成：%d 个文件 → %s") % (len(outs), out_dir)
        props.risk_text = "\n".join(warnings)
        props.detail_text = "\n".join(lines)

        msg = props.status_text
        if warnings:
            msg += "\n\n" + "\n\n".join(warnings)
        # 注意：Blender 5.1 的图标枚举里没有 "WARNING"，带警告时用 "ERROR" 吸引注意
        _popup(I.t("导出完成"), msg, icon="ERROR" if warnings else "INFO")
        _info(self, props.status_text)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# 一键生成并导出
# ---------------------------------------------------------------------------
class CSTACK_OT_generate_and_export(Operator):
    bl_idname = "cstack.generate_and_export"
    bl_label = "一键生成并导出"
    bl_description = "分析图片 → 建模 → 直接导出为可打印文件"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        r1 = bpy.ops.cstack.generate()
        if "FINISHED" not in r1:
            return {"CANCELLED"}
        r2 = bpy.ops.cstack.export()
        if "FINISHED" not in r2:
            return {"CANCELLED"}
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# 预览
# ---------------------------------------------------------------------------
class CSTACK_OT_preview(Operator):
    bl_idname = "cstack.preview"
    bl_label = "生成配色预览图"
    bl_description = "按当前配色估算打印后的呈现色，生成一张对照预览图"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = _props(context)
        if props.image is None:
            return _fail(self, I.t("预览失败"), I.t("请先指定一张图片。"))
        try:
            res = max(C.MIN_RES, min(C.MAX_RES, int(props.resolution)))
            ow, oh = _fit_output_size(props.image, res)
            grid, w, h, _qstats = GEN.prepare_grid(props.image, ow, oh,
                                                   props.as_params())
            if props.mode == "mosaic":
                # 色块板的预览不需要任何呈色模型（=分区网格本身），
                # 所以解析四色板这一步对它也没意义。
                img = _mosaic_preview_image(grid, w, h, props, None)
            else:
                keys, prof, _ = GEN.resolve_palette(grid, props.as_params())
                if props.mode in ("card", "single"):
                    img = _stack_preview_image(
                        grid, w, h, props, prof,
                        single=(props.mode == "single"))
                elif props.mode == "postcard" or props.height_mode == "region":
                    # 分层浮雕（分区配色）：预览必须走与生成同一套锚点模型，
                    # 否则预览画的是凸包投影、实物按膜色半色调（R20）。
                    img = _relief_preview_image(grid, w, h, props, prof, keys)
                else:
                    _, stack = CE.build_stack(keys, None, props.base_mm,
                                              props.relief_mm,
                                              base_key=props.base_key)
                    img = GEN.make_preview_image(
                        grid, w, h, keys, stack,
                        height_mode=props.height_mode,
                        brightness_weight=props.brightness_weight,
                        invert=props.invert, gamma=props.gamma,
                        weight_levels=float(prof.get("weight_levels", 0.0)),
                        merge_below=float(prof.get("merge_below", 0.0)),
                        max_components=int(prof.get("max_components", 0) or 0),
                        **_dither_kw(
                            prof,
                            dither_block=_preview_dither_block(w, h, props)))
            props.preview_image = img
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return _fail(self, I.t("预览失败"), "%s" % exc)

        props.status_text = I.t("已生成预览图：%s（可在图像编辑器里查看）") % img.name
        try:
            if bpy.context.screen is not None:
                for area in bpy.context.screen.areas:
                    if area.type == "IMAGE_EDITOR":
                        area.spaces.active.image = img
                        break
        except Exception:
            pass
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# 质量报告
# ---------------------------------------------------------------------------
class CSTACK_OT_report(Operator):
    bl_idname = "cstack.report"
    bl_label = "查看模型质量报告"
    bl_description = "查看当前生成模型的水密性、非流形与零面积面检查结果"
    bl_options = {"REGISTER"}

    def execute(self, context):
        obj = None
        for o in bpy.data.objects:
            if "cstack_meta" in o.keys():
                obj = o
                break
        if obj is None:
            return _fail(self, I.t("质量报告"), I.t("还没有生成模型。"))

        import json
        meta = {}
        try:
            meta = json.loads(obj.get("cstack_meta", "{}"))
        except Exception:
            pass
        stats = meta.get("stats", {})
        grid = meta.get("grid", [0, 0])
        size = meta.get("size_mm", [0, 0])
        swaps = meta.get("swap_heights", [])
        palette = meta.get("palette", [])
        qcn = meta.get("quality_cn", "?")
        band = meta.get("band_mm", 0.0)
        swapstat = meta.get("swaps", {})
        mo = meta.get("mosaic") or {}
        is_mosaic = (meta.get("mode") == "mosaic")

        lines = [
            I.t("模型：%s") % obj.name,
            I.t("生成模式：%s") % I.t_desc(
                C.MODE_CN.get(meta.get("mode", ""), meta.get("mode", "?")),
                C.MODE_EN.get(meta.get("mode", ""), meta.get("mode", "?"))),
        ]
        if is_mosaic:
            # 色块板没有"质量等级/共用底带"这些叠色概念 —— 照抄会误导。
            # 换成它真正要讲的三件事：几个色块、每块多大、还原到什么程度。
            cols = list(mo.get("colors") or [])
            sh = mo.get("shares") or {}
            lines.append(I.t("色块数：%d 个") % len(cols))
            lines.append(I.t("底板色：%s")
                         % I.t(C.BASE_COLORS.get(meta.get("base_key", "W"), {})
                               .get("cn", "?")))
            lines.append(I.t("色号清单（按用量）："))
            for i, k in enumerate(cols):
                lines.append("   %2d. #%s   %5.1f%%"
                             % (i + 1, k, float(sh.get(k, 0.0)) * 100.0))
            lines.append(I.t("最小色块：%.1f mm ｜ 已合并 %d 格")
                         % (float(mo.get("min_mm") or 0.0),
                            int(mo.get("merged_px") or 0)))
            g = mo.get("gamut") or {}
            if g:
                lines.append(I.t("全色域分析：ΔE 中位 %.2f / p90 %.2f / 最大 %.2f；"
                                 "覆盖 ΔE≤3 达 %.0f%%")
                             % (g.get("dE_med", 0.0), g.get("dE_p90", 0.0),
                                g.get("dE_max", 0.0),
                                g.get("cover_3", 0.0) * 100.0))
        else:
            lines.append(I.t("质量等级：%s") % I.t(qcn))
            lines.append(I.t("参与配色：%s")
                         % ("、".join(I.t(C.BASE_COLORS.get(k, {}).get("cn", k))
                                      for k in palette) or "?"))
            lines.append(I.t("底色：%s")
                         % I.t(C.BASE_COLORS.get(meta.get("base_key", "W"), {})
                               .get("cn", "?")))
            lines.append(I.t("共用底带：%.2f mm") % float(band or 0.0))
        lines += [
            I.t("采样网格：%d × %d") % (grid[0], grid[1]),
            I.t("外形尺寸：%.2f × %.2f mm") % (size[0], size[1]),
            I.t("当前网格：顶点 %d / 面 %d")
            % (len(obj.data.vertices), len(obj.data.polygons)),
            "",
            I.t("换色代价："),
            I.t("   预计换色次数：%s 次") % swapstat.get("estimate", "?"),
            I.t("   单层最多用色：%s 种") % swapstat.get("max_per_layer", "?"),
        ]
        if is_mosaic:
            # 色块板整件只有一个色层，"色带切换高度"不存在；换色发生在 XY 平面。
            lines.append(I.t("   （色块板的换色发生在同一层内，不按 Z 高度换料）"))
        # v1.25.0：颜色还原度 —— 用户问"像不像"时唯一的量化答案。
        # 必须翻成人话：用户明确说过不懂色彩术语，不能直接抛 ΔE。
        # v1.26.0：改成「远看」口径（3×3 格平均）—— 逐像素会把抖动档判得比
        # 像素档更差，方向是反的（详见 GEN.estimate_color_fidelity 的说明）。
        fid = meta.get("fidelity")
        if fid:
            lines.append("")
            lines.append(I.t("颜色还原度（远看，按 3×3 格取平均）："))
            lines.append(I.t("   整体：%s（ΔE 中位 %.1f）")
                         % (I.t(GEN.fidelity_word(fid.get("dE_med", 0.0))),
                            float(fid.get("dE_med", 0.0))))
            if float(fid.get("dE_pix_med", 0.0)) > float(fid.get("dE_med", 0.0)) + 1.0:
                lines.append(I.t("   近看每颗色点都有偏差，一臂距离看才是上面的效果"))
            if float(fid.get("dark_share", 0.0)) >= 0.02:
                lines.append(I.t("   暗部（占画面 %.0f%%）：%s（ΔE 中位 %.1f）")
                             % (float(fid.get("dark_share", 0.0)) * 100.0,
                                I.t(GEN.fidelity_word(
                                    fid.get("dE_dark_med", 0.0))),
                                float(fid.get("dE_dark_med", 0.0))))
                lines.append(I.t("   暗部偏色是四色叠色的物理上限：四卷料叠到底也"
                                 "到不了纯黑，深色会偏紫。"))
                lines.append(I.t("   要更深的黑得加黑色耗材（8色MAX 组），"
                                 "但那一组还没有实拍标定数据。"))
        lines += [
            "",
            I.t("几何检查："),
            I.t("   边界边（应为 0）：%s") % stats.get("boundary", "?"),
            I.t("   非流形边（应为 0）：%s") % stats.get("nonmanifold", "?"),
            I.t("   零面积面（应为 0）：%s") % stats.get("degenerate", "?"),
        ]
        if not is_mosaic:
            lines.append("")
            lines.append(I.t("色带切换高度（色带叠色模式在切片软件里按这些 Z 值换料）：")
                         )
            for i, z in enumerate(swaps):
                lines.append(I.t("   第 %d 次换色：%.2f mm") % (i + 1, z))
            if not swaps:
                lines.append(I.t("   （无）"))

        text = "\n".join(lines)
        props = _props(context)
        props.detail_text = text
        _popup(I.t("模型质量报告"), text, icon="INFO")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# 清空历史生成物
# ---------------------------------------------------------------------------
class CSTACK_OT_clear(Operator):
    bl_idname = "cstack.clear"
    bl_label = "清除所有生成结果"
    bl_description = "删除插件此前生成的所有模型对象（图片与材质保留）"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        removed = 0
        # v1.0.15：**先存名字、再按名字重取**。底板是子对象，删父对象时 Blender
        # 会连带把它删掉；若直接遍历 `list(bpy.data.objects)` 的快照，快照里那些
        # 已被连带删除的对象再取 `.keys()` 会抛
        # `ReferenceError: StructRNA of type Object has been removed`
        # （实测 t_install 在「清空后重新生成」这一步整脚本中断）。
        names = [o.name for o in bpy.data.objects
                 if "cstack_meta" in o.keys()]
        for nm in names:
            o = bpy.data.objects.get(nm)
            if o is None:
                continue
            # 底板/色膜拆成父子两层，底板没有 meta —— 跟着父对象删。
            for ch in list(o.children_recursive):
                if ch.name in bpy.data.objects:
                    bpy.data.objects.remove(ch, do_unlink=True)
                    removed += 1
            bpy.data.objects.remove(o, do_unlink=True)
            removed += 1
        props = _props(context)
        props.status_text = I.t("已清除 %d 个生成对象。") % removed
        props.detail_text = ""
        props.risk_text = ""
        _info(self, props.status_text)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# 挂绳孔生成（v1.16.0）：在已生成的模型上直接加孔，不重新生成
# ---------------------------------------------------------------------------
class CSTACK_OT_lanyard(Operator):
    bl_idname = "cstack.lanyard"
    bl_label = "生成挂绳孔"
    bl_description = ("在已生成的模型上直接添加挂绳孔（使用下方设置的半径与厚度）")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = _props(context)
        if props.lanyard == "none":
            return _fail(self, I.t("生成挂绳孔"),
                        I.t("请先在「后处理」选择挂绳孔类型（外置 / 内置）"))

        # 找已生成的模型（优先选中对象）
        obj = None
        for o in context.selected_objects:
            if "cstack_meta" in o.keys():
                obj = o
                break
        if obj is None:
            for o in bpy.data.objects:
                if "cstack_meta" in o.keys():
                    obj = o
                    break
        if obj is None:
            return _fail(self, I.t("生成挂绳孔"),
                        I.t("还没有生成模型，请先点击“生成模型”。"))

        # 解析已有 meta（用于回写；v1.16.3 起三种模式都支持挂绳孔）
        meta = {}
        try:
            meta = json.loads(obj.get("cstack_meta", "{}"))
        except Exception:  # noqa: BLE001
            pass

        import bmesh
        try:
            info = LAN.apply_lanyard(obj, props.lanyard,
                                    props.lanyard_inner_r_mm,
                                    props.lanyard_outer_r_mm,
                                    props.lanyard_thickness_mm)
            _focus_object(obj)
            bm2 = bmesh.new()
            bm2.from_mesh(obj.data)
            boundary, nonmanifold, pinch = MB.count_bad_edges(bm2, split=True)
            degenerate = MB.count_degenerate_faces(bm2, C.EPS_AREA2)
            bm2.free()
        except Exception as exc:  # noqa: BLE001 —— 后处理失败不阻塞
            traceback.print_exc()
            return _fail(self, I.t("生成挂绳孔"), "%s" % exc)

        # 回写 meta（lanyard 信息 + 最终水密三项）
        meta["lanyard"] = info
        meta["stats"] = {"boundary": boundary, "nonmanifold": nonmanifold,
                         "degenerate": degenerate, "pinch": pinch}
        obj["cstack_meta"] = json.dumps(meta, ensure_ascii=False)

        props.status_text = I.t("挂绳孔添加成功（内孔 %.1fmm ／ 外环 %.1fmm）") % (
            info["inner_r"], info["outer_r"])
        _hole_ok = bool(info.get("open", True))
        props.detail_text = (
            I.t("✓ 网格检查通过：水密、无边界边、无非流形边、无零面积面。")
            if not (boundary or nonmanifold or degenerate) else
            I.t("⚠ 网格存在缺陷：") + " " + I.t("边界边 %d / 非流形边 %d / 零面积面 %d")
            % (boundary, nonmanifold, degenerate))
        # v1.32.0（R39）：把「孔是不是真的通了」直接报出来 —— 用户报过
        # "切片后孔还是堵的"，那时只能靠肉眼在切片软件里找。现在生成时就用
        # 射线穿过孔心自检；不通会明确提示。
        props.detail_text += (
            "\n" + I.t("✓ 挂绳孔已贯通（射线穿过孔心无阻挡）") if _hole_ok
            else "\n" + I.t("⚠ 挂绳孔可能被堵：射线穿过孔心时打到了实体"))
        _info(self, props.status_text)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# 对齐喷嘴（v1.7.4：拆成 0.2 / 0.4 两档）
# ---------------------------------------------------------------------------
class _AlignNozzleBase(Operator):
    """把采样精度设成「一根柱子 ≈ 一条挤出线宽」，让每个色点都打得出来。

    ⚠ 这是**共用实现**，不是可注册算子（故意不写 bl_idname）—— classes 元组
    里只登记下面两个具体算子。以后加档位只需再继承一个、改一行 nozzle。
    """

    bl_options = {"REGISTER", "UNDO"}
    nozzle = C.DEFAULT_NOZZLE        # 子类覆盖

    def execute(self, context):
        props = _props(context)
        nozzle = str(self.nozzle)

        # 柱宽 = 最长边 ÷ 采样精度（长边方向采样 resolution 个像素）。
        # 目标：柱宽 ≈ 喷嘴挤出的线宽 —— 再细切片器也走不出来，只会把相邻
        # 柱子糊成一片，色点没打出来却白白多出几十万面网格。
        width = C.nozzle_width(nozzle)
        size = max(1e-6, float(props.size_mm))
        target = int(round(size / width))
        target = max(C.MIN_RES, min(C.MAX_RES, target))
        old = max(1, int(props.resolution))
        props.resolution = target
        # 记住这次点的是哪一档 → 面板的柱宽读数与提醒都跟着它走
        try:
            props.nozzle_mm = nozzle
        except Exception:      # 属性缺失（旧场景）也不该影响对齐本身
            pass
        new_mm = size / float(target)

        if target == old:
            msg = I.t("采样精度已是 %d，柱宽 %.2f mm —— 已按 %s 喷嘴对齐。") % (
                target, new_mm, nozzle)
        else:
            msg = I.t("采样精度 %d → %d：柱宽 %.3f → %.2f mm"
                      "（%s 喷嘴挤出线宽按 %.2f mm 计）。"
                      "现在每个色点都是一条走得出的挤出线。") % (
                old, target, size / float(old), new_mm, nozzle, width)
        _popup(I.t("%s 喷嘴对齐") % nozzle, msg, icon="INFO")
        return {"FINISHED"}


class CSTACK_OT_align_nozzle(_AlignNozzleBase):
    """0.4 喷嘴。**沿用原 idname**，旧脚本/旧探针调用它仍按 0.4 档工作。"""

    bl_idname = "cstack.align_nozzle"
    bl_label = "0.4 喷嘴"
    bl_description = ("采样精度对齐到 0.4 喷嘴的挤出线宽（%.2f mm），"
                      "让每根柱子都是一条走得出的挤出线"
                      % C.nozzle_width("0.4"))
    nozzle = "0.4"


class CSTACK_OT_align_nozzle_n02(_AlignNozzleBase):
    """0.2 喷嘴（v1.7.4 新增）。线宽更细 → 同样尺寸下采样精度翻倍。"""

    bl_idname = "cstack.align_nozzle_n02"
    bl_label = "0.2 喷嘴"
    bl_description = ("采样精度对齐到 0.2 喷嘴的挤出线宽（%.2f mm）。"
                      "0.2 喷嘴线宽只有 0.4 的一半，同样尺寸下能打到约两倍的"
                      "采样精度，代价是打印时间大幅上升"
                      % C.nozzle_width("0.2"))
    nozzle = "0.2"


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------
classes = (
    CSTACK_OT_load_image,
    CSTACK_OT_load_back_image,
    CSTACK_OT_analyze,
    CSTACK_OT_generate,
    CSTACK_OT_export,
    CSTACK_OT_generate_and_export,
    CSTACK_OT_preview,
    CSTACK_OT_report,
    CSTACK_OT_clear,
    CSTACK_OT_lanyard,
    CSTACK_OT_align_nozzle,
    CSTACK_OT_align_nozzle_n02,
)

# 注册期文案的翻译键：bl_idname → (label 原文, description 原文)
_OP_L10N = {
    "cstack.load_image": ("载入图片", "从磁盘读取图片作为叠色建模的输入"),
    "cstack.load_back_image": ("载入反面图片",
                               "从磁盘读取明信片反面的图片（文字 / 邮戳 / 二维码）"),
    "cstack.analyze": ("分析图片配色", "统计图片的颜色构成，推荐用哪几种颜色参与配色，并显示各色用量占比"),
    "cstack.generate": ("生成模型", "读取图片，分析颜色，按叠色原理生成可打印的 3D 模型"),
    "cstack.export": ("导出可打印文件", "把生成的模型导出为 3MF / OBJ"),
    "cstack.generate_and_export": ("一键生成并导出", "分析图片 → 建模 → 直接导出为可打印文件"),
    "cstack.preview": ("生成配色预览图", "按当前配色估算打印后的呈现色，生成一张对照预览图"),
    "cstack.report": ("查看模型质量报告", "查看当前生成模型的水密性、非流形与零面积面检查结果"),
    "cstack.clear": ("清除所有生成结果", "删除插件此前生成的所有模型对象（图片与材质保留）"),
    "cstack.lanyard": ("生成挂绳孔", "在已生成的模型上直接添加挂绳孔（使用下方设置的半径与厚度）"),
}
_OP_BY_ID = {cls.bl_idname: cls for cls in classes}


def _apply_labels():
    """按当前语言重写算子类属性（注册前调用）。"""
    for idname, (label, desc) in _OP_L10N.items():
        cls = _OP_BY_ID[idname]
        cls.bl_label = I.t(label)
        cls.bl_description = I.t(desc)


def retranslate():
    """语言切换后调用：重注册算子让 bl_label / bl_description 生效。"""
    for cls in classes:
        try:
            bpy.utils.unregister_class(cls)
        except Exception:  # noqa: BLE001
            pass
    _apply_labels()
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception:  # noqa: BLE001
            pass


def register():
    _apply_labels()
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
