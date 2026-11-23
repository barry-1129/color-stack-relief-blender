# -*- coding: utf-8 -*-
"""UI 面板 —— 3D 视图侧栏（N 面板），全中文。

四步式布局（v1.23.0 / R28 按用户习惯重排）：
  第 1 步 · 导入照片   → 主面板（CSTACK_PT_main）
  第 2 步 · 参数设置   → CSTACK_PT_step2，下挂三个可折叠子面板：
                          模型与尺寸   → CSTACK_PT_params_quality
                          质量等级     → CSTACK_PT_quality
                          混色与配色   → CSTACK_PT_color_height
  第 3 步 · 后期处理   → CSTACK_PT_post（挂绳孔）
  第 4 步 · 导出       → CSTACK_PT_step3

【为什么从"按技术模块"改成"按用户动作"】v1.22.0 及以前是"参数与质量 / 配色与
高度"两个技术分区 —— 用户（不懂色彩术语、不熟 Blender）反馈找不到"最大厚度"、
不知道"采样精度"和"质量等级"是什么关系。现在的分区对齐他描述的工作顺序：
导入 → 调参数 → 加挂绳孔 → 导出。每个子面板只回答一个问题。

说明：
  * 早期版本把"图片预览框（props.image 的 Image 数据块选择器）"放在第 1 步下面，
    它只是冗余的第二入口（生成管线读的 props.image 由 image_path + 载入图片按钮
    填充），已于 v1.7.5 移除。
  * 子面板用 bl_parent_id 实现 —— Blender 会给带父面板的面板加折叠箭头，
    三段可各自收起，面板不再一长到底。
"""

import math

import bpy

from . import constants as C
from . import i18n as I

_CATEGORY = "叠色浮雕"

# 【v1.16.1 BLT 免疫】显式绘制"会被 Blender 自带 zh_HANS 词典二次翻译的英文串"
# 的调用点，必须带 text_ctxt=_TC（自定义上下文查不到自带词典 → 显示我们产出的
# 原文）。全部属性侧已由 translation_context 免疫；这里只覆盖显式 text= 的
# 少数碰撞点（语言/精细度/去孤立点/对比度/底色）与动态状态栏。
_TC = C.I18N_CONTEXT


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = _CATEGORY


def _mode_mq(props):
    """当前质量等级对应的全色平板档位（mosaic 专用）。"""
    return C.MOSAIC_QUALITY.get(props.quality) \
        or C.MOSAIC_QUALITY[C.DEFAULT_QUALITY]


def _group_keys_cn(keys):
    """把色键串成给用户看的颜色名（青、品红、黄、白）。"""
    return "、".join(I.t(C.BASE_COLORS.get(k, {}).get("cn", k)) for k in keys)


def _bambu_slots_cn(props):
    """拓竹模式的 AMS 槽位读数：「1 红 · 2 黄 · 3 蓝 · 4 白」。

    【为什么面板要写这一行】用户只能凭视觉判断像不像，而"槽位顺序"在视口里
    完全看不出来（R45 的坑：颜色选对了、槽位排错了，3D 视图一模一样，实物
    却红黄互换）。把槽位顺序直接印在面板上，是他唯一能不拆机就核对的口径。

    顺序取色组的**声明顺序**，与 ``generate()`` 写进材质表的 ``mat_order``
    完全同源（那是 3MF 槽位的唯一真相）。
    """
    if props.mode == "mosaic":
        return ""       # 色块由照片聚类决定，生成前不知道是几号色
    g = C.COLOR_GROUPS.get(props.mix_group) or C.COLOR_GROUPS[C.DEFAULT_COLOR_GROUP]
    names = [I.t(C.BASE_COLORS.get(k, {}).get("cn", k)) for k in g["keys"]]
    return " · ".join("%d %s" % (i + 1, n) for i, n in enumerate(names))


# ---------------------------------------------------------------------------
# 主面板：语言选择 + 第 1 步 · 导入照片
# ---------------------------------------------------------------------------
class CSTACK_PT_main(_Base, bpy.types.Panel):
    bl_idname = "CSTACK_PT_main"
    bl_label = "%s v%s" % (C.ADDON_NAME, C.ADDON_VERSION_STR)

    def draw(self, context):
        layout = self.layout
        props = context.scene.cstack

        # ---- 作者信息（v1.16.2）----
        box = layout.box()
        box.label(text="barry / 尼罗河的泥螺", icon="USER")

        # ---- 语言选择（v1.15.0，置于所有步骤之前）----
        # 标题就是属性自身 name（经 i18n 随语言切换为「语言选择」/「Language」），
        # 不再额外画一个 label，避免中英双标题并存。
        box = layout.box()
        box.prop(props, "language")

        # ---- 旧版残留警告（v1.4.3）----
        # 会话内"移除 → 重装"扩展不会替换内存里的旧代码，用户极易在旧逻辑上
        # 误判新版本仍有 bug。这里在最顶部用红色警告框把这件事喊出来。
        stale = bpy.app.driver_namespace.get("CSTACK_STALE_FROM")
        if stale:
            box = layout.box()
            box.alert = True
            box.label(text=I.t("⚠ 运行的仍是旧版 v%s！") % stale, icon="ERROR")
            box.label(text=I.t("请完全退出并重启 Blender，新版本才生效"))

        # ---- 第 1 步 · 导入照片 ----
        box = layout.box()
        box.label(text=I.t("第 1 步 · 导入照片"), icon="IMAGE_DATA")

        col = box.column(align=True)
        col.prop(props, "image_path", text="")
        col.operator("cstack.load_image", icon="FILEBROWSER",
                     text=I.t("载入图片"))

        if props.image is not None:
            try:
                w, h = int(props.image.size[0]), int(props.image.size[1])
                box.label(text=I.t("当前：%s（%d × %d）")
                          % (props.image.name, w, h), icon="CHECKMARK")
            except Exception:
                box.label(text=I.t("当前：%s") % props.image.name,
                          icon="CHECKMARK")
        else:
            box.label(text=I.t("尚未选择图片"), icon="INFO")


# ---------------------------------------------------------------------------
# 第 2 步 · 参数设置（容器面板：内容全在下挂的三个可折叠子面板里）
# ---------------------------------------------------------------------------
class CSTACK_PT_step2(_Base, bpy.types.Panel):
    bl_idname = "CSTACK_PT_step2"
    bl_label = "第 2 步 · 参数设置"
    bl_parent_id = "CSTACK_PT_main"

    def draw(self, context):
        # 只保留折叠头，内容由三个子面板渲染
        # （Blender 自动排到本面板下方并各带一个折叠箭头）。
        pass


class CSTACK_PT_params_quality(_Base, bpy.types.Panel):
    bl_idname = "CSTACK_PT_params_quality"
    bl_label = "模型与尺寸"
    bl_parent_id = "CSTACK_PT_step2"

    def draw(self, context):
        _draw_params_quality(self.layout, context.scene.cstack)


class CSTACK_PT_quality(_Base, bpy.types.Panel):
    bl_idname = "CSTACK_PT_quality"
    bl_label = "质量等级"
    bl_parent_id = "CSTACK_PT_step2"

    def draw(self, context):
        _draw_quality(self.layout, context.scene.cstack)


class CSTACK_PT_color_height(_Base, bpy.types.Panel):
    bl_idname = "CSTACK_PT_color_height"
    bl_label = "混色与配色"
    bl_parent_id = "CSTACK_PT_step2"

    def draw(self, context):
        _draw_color_height(self.layout, context.scene.cstack)


# ---------------------------------------------------------------------------
# 「模型与尺寸」绘制体：三模式共有入口（精细度 / 尺寸；厚度自 v1.27.0 自动）
# ---------------------------------------------------------------------------
def _draw_params_quality(layout, props):
    """模型与尺寸（由 CSTACK_PT_params_quality 调用）。

    v1.23.0 起这里只画**三模式共有**的东西：模式、精细度、最长边；
    v1.27.0 起厚度改为自动计算，只显示结果。
    外加柱宽/喷嘴的可打性提醒。质量等级与混色分别挪到另外两个子面板 ——
    用户按"先定形状、再定精度、最后定用几卷料"的顺序看，不会互相干扰。
    """
    box = layout

    # ---- 生成模式 ----
    col = box.column(align=True)
    col.prop(props, "mode", text="")
    mode = props.mode
    if mode == "mosaic":
        box.label(text=I.t("全色平板：照片聚成 N 个色块，每块直接上色，顶面全平"),
                  icon="INFO")
        box.label(text=I.t("颜色所见即所得：预览就是实物，不做混色推算"),
                  icon="CHECKMARK")
    elif mode == "card":
        box.label(text=I.t("双面平板：每面固定 6 层，两面同图，表面平整；"
                           "白色可以叠在颜色外层，浅色更准"),
                  icon="INFO")
    elif mode == "single":
        box.label(text=I.t("单面平板：正面叠 %d 层彩膜，下面是 0.40mm 白底板"
                           "（正好 4 层白），反面纯白")
                  % int(C.SINGLE_BANDS), icon="INFO")
        box.label(text=I.t("层数固定，不用你算；每格只叠一种组合，不撒彩点"),
                  icon="CHECKMARK")
    elif mode == "postcard":
        box.label(text=I.t("明信片浮雕：正面叠色浮雕（明暗→起伏、柱顶混色），"
                           "反面单色凹刻"), icon="INFO")
        box.label(text=I.t("正反两种厚度在下面分开设；反面图案另选一张图"),
                  icon="CHECKMARK")
    else:
        box.label(text=I.t("叠色浮雕：明暗变成高低起伏，顶面按颜色抖动混色"),
                  icon="INFO")

    box.separator()
    # ---- 三模式共有：精细度 / 尺寸 ----
    # v1.27.0：厚度不再给用户设 —— 由 C.auto_thickness_total 按模式与幅面算，
    # 面板只把结果显示出来（用户关心的是"打出来多厚"，不是内部拆分）。
    col = box.column(align=True)
    # v1.0.14（用户 2026-11-20）：精细度改成 1~10 档（参考 3D 轨迹），
    # 不再暴露像素数、喷嘴孔径与色点数。
    col.prop(props, "detail_level", slider=True, text=I.t("分辨率"),
             text_ctxt=_TC)
    col.prop(props, "size_mm", text=I.t("最长边尺寸 (mm)"))
    col.prop(props, "rim_layers", text=I.t("外框层数"))
    if int(getattr(props, "rim_layers", C.MODEL_RIM_LAYERS_DEFAULT)) > 0:
        _rim_w = C.rim_mm_for_layers(props.rim_layers)
        box.label(text=I.t("%d 层外框 = 四周各加 %.2f mm（底板色），成品最长边 %.2f mm")
                       % (int(props.rim_layers), _rim_w,
                          props.size_mm + 2.0 * _rim_w),
                  icon="FULLSCREEN_ENTER")
    else:
        box.label(text=I.t("外框 0 层：不加外框，成品最长边就是 %.1f mm")
                       % props.size_mm, icon="INFO")

    # ---- 明信片浮雕：两项厚度 + 反面图片（v1.44.0 / R54）----
    post_split = None
    if mode == "postcard":
        sub = box.box()
        sub.label(text=I.t("厚度（正反分开设）"), icon="LAYER_ACTIVE")
        c2 = sub.column(align=True)
        c2.prop(props, "post_front_mm", slider=True,
                text=I.t("正面叠色浮雕厚度"), text_ctxt=_TC)
        c2.prop(props, "post_engrave_mm", slider=True,
                text=I.t("反面凹刻深度"), text_ctxt=_TC)
        post_split = C.thickness_split(
            "postcard", props.post_front_mm, props.stack_bands,
            props.post_engrave_mm)
        _post_film = 2.0 * int(props.stack_bands) * 0.10
        _post_layers = int(round(float(post_split["relief_mm"]) / 0.10))
        _post_var = max(0, _post_layers - int(round(_post_film / 0.10)))
        sub.label(text=I.t("正面 %.2f（共 %d 层：色膜 %d 层 %.2f + 起伏 %d 层 %.2f，"
                           "无白底板）＋ 反面白 %.2f（%d 层）｜ 整件 %.2f mm")
                  % (post_split["base_mm"] + post_split["relief_mm"],
                     _post_layers,
                     int(round(_post_film / 0.10)), _post_film,
                     _post_var, _post_var * 0.10,
                     post_split["back_mm"],
                     int(round(float(post_split["back_mm"]) / 0.10)),
                     post_split["total_mm"]),
                  icon="INFO")
        # v1.0.12：色膜是固定的 12 层、不参与起伏；能上下的只有剩下的层数。
        # 这个数必须当面说清，别等打出来才发现是平板。
        _post_vis = max(0.0, float(post_split["relief_mm"]) - _post_film)
        if _post_vis < 0.40:
            sub.label(text=I.t("⚠ 柱顶 %d 层色膜占 %.2f，画面实际只有 %.2f mm "
                               "落差（约 %d 层），接近平板")
                      % (int(props.stack_bands) * 2, _post_film, _post_vis,
                         int(round(_post_vis / 0.10))),
                      icon="ERROR")
        # v1.45.1 / R57：四周外扩边框 —— 成品外形比设定的最长边大 2×rim，
        # 排盘前必须知道（不是文案，是尺寸读数）。v1.0.13 起**所有模式**都有
        # 外框，所以这一行改在公共段统一画（见下面），这里不再重复。
        # v1.45.1 / R57：用户要求删掉"凹刻最深…"与"没上色的地方比彩色低…"
        # 两行说明（面板太吵）。凹刻深度已经有滑杆读数，白下沉在打印说明里。
        # 滑杆能滑到 0.8，但柱顶要放 2×bands 片色膜 —— 低于有效下限时几何会被
        # 抬回去（实测 0.8/1.0/1.2 出的是同一个模型），必须当面说清。
        _eff_min = C.postcard_min_front_mm(props.stack_bands)
        if props.post_front_mm < _eff_min - 1e-6:
            sub.label(text=I.t("⚠ 低于 %.2f mm 建不出来（柱顶要放 %d 片 0.1mm "
                               "色膜），实际会按 %.2f mm 建")
                      % (_eff_min, 2 * int(props.stack_bands), _eff_min),
                      icon="ERROR")

        sub.separator()
        # v1.45.2 / R58：用户要求去掉「（文字 / 邮戳 / 二维码）」这串例子，
        # 标题只留「反面图案」；例子移到载入按钮的悬浮说明里，不占版面。
        sub.label(text=I.t("反面图案"), icon="IMAGE_DATA")
        cc = sub.column(align=True)
        cc.prop(props, "back_image_path", text="")
        cc.operator("cstack.load_back_image", icon="FILEBROWSER",
                    text=I.t("载入反面图片"))
        # v1.45.2 / R58：左右镜像开关（按钮态）。toggle=True 让它显示成按下去
        # 的按钮，而不是勾选框 —— 用户点名要"按钮"。
        cc.prop(props, "back_mirror", text=I.t("左右镜像（中轴线翻转）"),
                icon="MOD_MIRROR", toggle=True)
        if props.back_image is not None:
            try:
                bw, bh = int(props.back_image.size[0]), int(props.back_image.size[1])
                sub.label(text=I.t("当前：%s（%d × %d）")
                          % (props.back_image.name, bw, bh), icon="CHECKMARK")
            except Exception:
                sub.label(text=I.t("当前：%s") % props.back_image.name,
                          icon="CHECKMARK")
        else:
            sub.label(text=I.t("未选：反面是平的（只打背板，不刻图案）"),
                      icon="INFO")

    # ---- 厚度 + 预估像素（v1.0.15 用户 2026-11-20：只留这两个数）----
    # 用户原话：「精简这段说明，只需要体现整个模型厚度和预计像素」。
    # 旧版每个模式各 2~3 行（底板/色块层拆分、色膜层数、色块数…），面板太吵。
    # 现在一行报总厚 + 长边格数 + 像素柱数；明信片因为正反厚度可调，读数留在
    # 上面的「厚度（正反分开设）」里，这里不重复。
    # v1.0.6：双面卡的层数是固定值（CARD_BANDS_PER_SIDE），不跟 stack_bands
    # 滑杆走。这里必须传同一个值，否则读数会算成"4 层 × 0.15mm"。
    _bands = (C.CARD_BANDS_PER_SIDE if mode == "card" else props.stack_bands)
    split = C.thickness_split(
        mode, C.auto_thickness_total(mode, props.size_mm, _bands),
        _bands,
        (props.post_engrave_mm if mode == "postcard" else None))

    # 注意：不能写成 `int(a), int(b) if cond else (1,1)` —— 逗号优先级低于
    # 条件表达式，会先解引用 props.image 再判断，图片为空时直接抛异常。
    if props.image is not None:
        iw, ih = int(props.image.size[0]), int(props.image.size[1])
        # v1.0.14：档位是唯一入口，柱数按档位映射后的格数算。
        _res = (int(props.resolution)
                if (int(props.detail_level) == C.DEFAULT_DETAIL_LEVEL
                    and int(props.resolution) != C.DEFAULT_RES)
                else C.detail_level_res(props.detail_level))
        if iw >= ih:
            n = _res * max(1, int(round(_res * ih / float(max(1, iw)))))
        else:
            n = _res * max(1, int(round(_res * iw / float(max(1, ih)))))
        _total = (float(post_split["total_mm"]) if mode == "postcard"
                  else float(split["total_mm"]))
        box.label(text=I.t("总厚 %.2f mm ｜ 长边约 %d 格 · 约 %s 个像素柱")
                       % (_total, _res, format(n, ",")), icon="SORTBYEXT")
        # v1.0.17（用户 2026-11-22）：**面板不再出现任何内存警示**。
        # 理由：内存预算是按本机物理内存估的，别人的机器可能更大，提醒只多余；
        # 而且生成端本来就不降档，提示"仍按第 N 档生成"没有可操作价值。
        # 代价说明统一放进「分辨率」的悬停文字（分辨率越高越慢、越吃内存）。
    else:
        box.label(text=I.t("总厚 %.2f mm ｜ 选图后显示预估像素柱")
                       % float(split["total_mm"]), icon="LAYER_ACTIVE")

    # v1.0.14：外框读数已挪到「最长边尺寸」下面（跟随外框层数控件）。

    # ---- 采样方式（v1.7.3）----
    col = box.column(align=True)
    col.prop(props, "sampling", text="")
    if props.sampling == "nearest":
        box.label(text=I.t("最近邻：细线/纯色块保得住，照片会有边缘毛刺"),
                  icon="INFO")
    else:
        box.label(text=I.t("面积平均：一格取整块平均，接近人眼观感"),
                  icon="CHECKMARK")

    # v1.0.14（用户 2026-11-20）：喷嘴档位（0.2/0.4 对齐按钮）、实际柱宽、
    # 色点数、柱宽告警**整段删除** —— 用户要求只用 1~10 档分辨率，不再按
    # 喷嘴孔径换算具体数值。柱宽/线宽的护栏仍在生成侧（min_print_width）。

    # ---- 上一版的实际代价（v1.25.0）----
    # 用户的原话："每个参数要能一眼看出代价（换色次数／耗材卷数／打印时间）"。
    # 模型本体克数是**确定性**口径（实心体积 × 1.24 g/cm³）；
    # 换色擦料按实测 0.35 g/次补进去（只报本体会低估约 30% —— 用户按 48 g
    # 买料、切片器却要 64 g）；时间只有量级参考，文案必须带"参考"。
    if props.last_grams > 0.0:
        box.separator()
        c = box.column(align=True)
        flush_g = props.last_swaps * C.FLUSH_GRAMS_PER_SWAP
        c.label(text=I.t("上一版用料：模型 %.1f g ＋ 换色擦料约 %.0f g ＝ 约 %.0f g")
                       % (props.last_grams, flush_g,
                          props.last_grams + flush_g), icon="INFO")
        mins = (props.last_grams + flush_g) * C.PRINT_MINUTES_PER_GRAM
        if mins >= 60.0:
            c.label(text=I.t("参考打印时间约 %.1f 小时（0.1mm 层高、常规速度）")
                           % (mins / 60.0), icon="TIME")
        else:
            c.label(text=I.t("参考打印时间约 %d 分钟（0.1mm 层高、常规速度）")
                           % int(round(mins)), icon="TIME")
        c.label(text=I.t("%.1f cm³ · 面数 %s · 换色 %d 次")
                       % (props.last_volume_cm3, format(props.last_polys, ","),
                          props.last_swaps), icon="MESH_DATA")


# ---------------------------------------------------------------------------
# 「质量等级」绘制体：三档预设 —— 每档"选了会怎样"写成人话
# ---------------------------------------------------------------------------
def _draw_quality(layout, props):
    box = layout.box()
    box.label(text=I.t("质量等级（三选一）"), icon="PRESET")
    box.prop(props, "quality", text="")

    lv = props.quality_level
    # v1.35.0（R42）：阈值随喷嘴线宽走，面板文案必须显示**当前喷嘴**下的真值，
    # 否则用户换 0.2 喷嘴会看到文案还写着 0.4 的数（旧版就是写死 0.6/0.8/1.2）。
    _noz = str(getattr(props, "nozzle_mm", C.DEFAULT_NOZZLE))
    _wmm = C.min_print_width(getattr(props, "quality", None), _noz)

    if props.mode == "mosaic":
        # 全色平板：档位只决定「聚成几个色块 / 碎斑多大」。
        mq = _mode_mq(props)
        box.label(text=I.t("%s ｜ %d 个色块 ｜ 小于 %.1fmm 的碎斑并入旁边")
                       % (I.t(lv.get("cn", "")), mq["color_count"],
                          mq["min_mm"]), icon="CHECKMARK")
        box.label(text=I.t("色块数 ≈ 彩色耗材卷数；档位越高越像原图、越费料"),
                  icon="INFO")
        return

    # ---- 叠色浮雕 / 双面平板：档位决定「色块有多细 ↔ 换色多少次」 ----
    # v1.35.0（R42）面板精简：原来每档 8~10 行说明，用户明确要求"精简"。
    # 现在只留三行 —— 当前档位换来什么、代价大概多少、这一档会怎么处理细节。
    _cm = str(getattr(props, "color_match", C.DEFAULT_COLOR_MATCH))
    if lv.get("ref_swaps"):
        box.label(text=I.t("%s ｜ 换色约 %d 次 ｜ 平均色差 ΔE≈%.0f")
                       % (I.t(lv.get("cn", "")), lv["ref_swaps"], lv["ref_err"]),
                  icon="CHECKMARK")
    box.label(text=I.t("档位越低越省料：色块更大、换色更少、细节更少"),
              icon="INFO")

    # v1.0.14：混色模型固定「叠层混色」，兼容模式不再从面板进入 ——
    # 这里只说明当前上色方式会怎么处理细节。
    if _cm == "solid":
        # 并块 + 细线合并压成一行：这两件都是"这一档会怎么处理细节"，
        # 分成两行只是为了排版，用户要的是少而准（R42 反馈）。
        _blk = int(lv.get("block_px", 1) or 1)
        if _blk > 1:
            box.label(text=I.t("整块上色：每 %d 格并一块；窄于 %.2fmm 的细线"
                               "并入旁边") % (_blk, _wmm), icon="INFO")
        else:
            box.label(text=I.t("整块上色：每格一块；窄于 %.2fmm 的细线并入旁边")
                           % _wmm, icon="INFO")
    else:
        box.label(text=I.t("网点混色：相邻点交错叠色，近看是颗粒"), icon="INFO")


# ---------------------------------------------------------------------------
# 「混色与配色」绘制体：全色平板 → 底板色；②③ → 四档混色组
# ---------------------------------------------------------------------------
def _draw_color_height(layout, props):
    box = layout
    mode = props.mode

    if mode == "mosaic":
        # ---- 全色平板：颜色来自照片聚类，没有"选哪几种颜色"这回事 ----
        box.label(text=I.t("颜色来源"), icon="COLOR")
        box.label(text=I.t("画面自动聚成 N 个色块，每个色块用一卷对应颜色的耗材"
                           "平打，不做混色推算"), icon="CHECKMARK")
        box.label(text=I.t("底板固定白色（与色膜分成两个对象，切片时可单独改色）"),
                  icon="INFO")
        row = box.row(align=True)
        row.operator("cstack.analyze", icon="COLOR", text=I.t("分析色块与全色域"))
        row.prop(props, "auto_palette", toggle=True, text=I.t("自动挑选"))

        if props.detail_text and ("色块" in props.detail_text
                                  or "色域" in props.detail_text):
            sub = box.box()
            for line in props.detail_text.split("\n"):
                if line.strip():
                    sub.label(text=line[:70], text_ctxt=_TC)

        hbox = box.box()
        hbox.label(text=I.t("说明："), icon="INFO")
        hbox.label(text=I.t("顶面全平（等高），一个色块就是一块材质区域"))
        hbox.label(text=I.t("预览图上的颜色就是实物会打出来的颜色"))
        hbox.label(text=I.t("色块数 = 耗材卷数 = 换色次数量级"))
        hbox.label(text=I.t("碎色斑由质量档位里的「最小色块」自动合并"))
        return

    # v1.0.14（用户 2026-11-20）：明信片浮雕的反面底板色**删除**，固定白色；
    # 反面白板与正面浮雕分成两个对象，切片时可单独给底板换料。

    # ---- ②③：四档混色组（v1.23.0，硬约束，不再自动挑色） ----
    # v1.0.15（用户 2026-11-20）：删掉标题里的「（要买几卷耗材）」—— 下一行
    # 已经写明"这组要 N 卷"，标题里再写一次是重复。
    box.label(text=I.t("混色色组"), icon="COLOR")
    box.prop(props, "mix_group", text="")
    gkey = props.mix_group if props.mix_group in C.COLOR_GROUPS \
        else C.DEFAULT_COLOR_GROUP
    g = C.COLOR_GROUPS[gkey]
    n_keys = len(g["keys"])
    box.label(text=I.t("这组要 %d 卷：%s") % (n_keys, _group_keys_cn(g["keys"])),
              icon="CHECKMARK")

    # ---- 计算依据（v1.0.9）：按色板实测 / 按理论 ----
    box.prop(props, "color_basis", text="")
    if str(getattr(props, "color_basis", C.DEFAULT_COLOR_BASIS)) == "theory":
        box.label(text=I.t("按理论：直接用上面这几个颜色算，不做实测校正"),
                  icon="CHECKMARK")
        box.label(text=I.t("换了色组或改了色值也能算，但跟实物的偏差没有校正"),
                  icon="INFO")
    else:
        box.label(text=I.t("按色板：用标定板实拍反解的颜料参数"),
                  icon="CHECKMARK")

    # ---- 混色模型（v1.0.14 功能删除、固定「叠层混色」；v1.0.15 连标题也删）----
    # 用户 2026-11-20：「混色模型默认为叠层混色，并且把这个功能删除」，随后
    # 又点名把「混色模型：叠层混色（固定）」这行也去掉 —— 用户没有别的选择，
    # 写出来只是噪音。兼容模式（surface）的代码链路保留，旧工程仍能跑。
    # 上色方式：五种模式统一开放（v1.0.14 起单面平板也能选）。
    box.prop(props, "color_match")
    if str(getattr(props, "color_match", C.DEFAULT_COLOR_MATCH)) == "solid":
        box.label(text=I.t("整块上色：一块区域只叠一种颜色，不撒碎点"),
                  icon="CHECKMARK")
    else:
        box.label(text=I.t("网点混色：相邻点交错叠色，近看是密集色点"),
                  icon="CHECKMARK")

    # v1.32.0（R39）：浮雕的竖墙颜色 —— 用户实拍反馈"颜色交界的白缝 / 该青的
    # 地方发白"就是它（实测 37.8% 的竖直暴露面积是白基底）。默认开。
    if mode in ("relief", "postcard"):
        box.prop(props, "relief_side_own")
        if props.relief_side_own:
            box.label(text=I.t("竖墙用图案自己的颜色（落差处不再露白）"),
                      icon="CHECKMARK")
            box.label(text=I.t("代价：每层可能换 2~4 次料，耗材与时间会增加"),
                      icon="INFO")
        else:
            box.label(text=I.t("竖墙用底板白：省料，但落差处会露白条"),
                      icon="ERROR")

    # v1.29.0：删掉「按拓竹套装色显示」复选框与两条拓竹说明 —— 它只改显示色、
    # 不影响打印结果，用户明确要求去掉这类噪音。属性保留（旧工程不炸）。
    # v1.0.9（用户 2026-11-15 截图点名）：全模式下删掉「分析图片配色」按钮与
    # 它的结果区 —— 配色现在由「混色色组 + 计算依据」两档决定，不需要再让
    # 用户点一次"分析"。算子保留（旧脚本仍可调用），只是面板不再暴露。

    # 【v1.28.0 / R35 面板收缩】锚点模式 / 高度方式 / 明暗层次 / 对比度 /
    # 反转明暗 五项**整体撤掉**：
    #   * 锚点模式：②③ 现在共用双面卡那套镜像多集配色器，没有档位可选；
    #   * 高度方式：固定「分区配色」（色带/纯亮度是旧实验档，实测都更差）；
    #   * 明暗层次 / 对比度 / 反转：固定 1.0 / 1.0 / 关 —— 用户要的是
    #     "照片什么样就什么样"，不再提供会把起伏压平或反过来的旋钮。
    # RNA 全部保留（旧 .blend 不炸），但 as_params 已固定成上面这些值。

    # v1.45.1 / R57：末尾的「说明：」整块删掉（用户截图点名）。
    # 机理说明留在 README 与打印说明里，面板只留可操作的参数与读数。


# ---------------------------------------------------------------------------
# 第 3 步 · 后期处理（v1.15.0：挂绳孔）
# ---------------------------------------------------------------------------
class CSTACK_PT_post(_Base, bpy.types.Panel):
    bl_idname = "CSTACK_PT_post"
    bl_label = "第 3 步 · 后期处理"
    bl_parent_id = "CSTACK_PT_main"

    def draw(self, context):
        layout = self.layout
        props = context.scene.cstack

        box = layout.box()
        box.label(text=I.t("挂绳孔（默认放在模型上部）"), icon="MESH_CIRCLE")
        box.prop(props, "lanyard", text="")
        # 挂绳孔几何参数（v1.16.0：可调，覆盖默认 1.5 / 2.5 / 3.0 mm）
        col = box.column(align=True)
        col.prop(props, "lanyard_inner_r_mm", text=I.t("内孔半径 (mm)"))
        col.prop(props, "lanyard_outer_r_mm", text=I.t("外环半径 (mm)"))
        col.prop(props, "lanyard_thickness_mm", text=I.t("挂绳孔厚度 (mm)"))
        # 生成功能键：在已生成的模型上直接加孔（不重新生成）
        box.operator("cstack.lanyard", icon="MESH_CIRCLE",
                     text=I.t("生成挂绳孔"))


# ---------------------------------------------------------------------------
# 第 4 步 · 导出
#   生成与导出按钮 + 导出设置 + 生成结果 + 清除
# ---------------------------------------------------------------------------
class CSTACK_PT_step3(_Base, bpy.types.Panel):
    bl_idname = "CSTACK_PT_step3"
    bl_label = "第 4 步 · 导出"
    bl_parent_id = "CSTACK_PT_main"

    def draw(self, context):
        layout = self.layout
        props = context.scene.cstack

        # ---- 生成与导出 ----
        box = layout.box()
        box.label(text=I.t("生成与导出"), icon="EXPORT")
        col = box.column(align=True)
        col.scale_y = 1.5
        col.operator("cstack.generate", icon="MESH_GRID",
                     text=I.t("生成模型"))
        col.scale_y = 1.0
        row = box.row(align=True)
        row.operator("cstack.export", icon="EXPORT",
                     text=I.t("导出可打印文件"))
        row.operator("cstack.report", icon="CHECKMARK",
                     text=I.t("查看模型质量报告"))
        col = box.column(align=True)
        col.operator("cstack.generate_and_export",
                     icon="PLAY", text=I.t("一键生成并导出"))

        # ---- 导出设置 ----
        box = layout.box()
        box.label(text=I.t("导出设置（3MF / OBJ）"), icon="FILE_FOLDER")
        box.prop(props, "export_dir", text="")
        box.prop(props, "file_stem", text="")
        row = box.row(align=True)
        row.prop(props, "export_3mf", text=I.t("导出 3MF"))
        row.prop(props, "export_obj", text=I.t("导出 OBJ"))

        # ---- 拓竹模式（v1.38.0）----
        # 用户要求：按钮放在「导出 3MF / 导出 OBJ」正下方，作为**一个开关**：
        # 开 = 拆部件 + 绑耗材槽 + 写切片参数；关 = 一个字都不写。
        brow = box.row()
        brow.scale_y = 1.4
        brow.enabled = props.export_3mf      # 不导 3MF 时本开关无意义
        brow.prop(props, "bambu_mode", toggle=True, icon="CONSOLE",
                  text=I.t("拓竹模式"))
        box.label(text=(I.t("✓ 已启用：按颜色拆部件，拓竹打开即带耗材与参数")
                        if props.bambu_mode else
                        I.t("已关闭：只输出几何与颜色，不调耗材、不调切片参数")),
                  icon="CHECKMARK" if props.bambu_mode else "INFO")

        if props.bambu_mode:
            sub = box.box()
            _slots = _bambu_slots_cn(props)
            if _slots:
                # 只印槽序，不再印"请按这个顺序装料"那行说明（v1.39.0 用户要求去掉）
                sub.label(text=I.t("耗材槽顺序：%s") % _slots, icon="COLOR")
            # v1.0.15（用户 2026-11-20）：整段「切片参数（写进 3MF）／墙层数」
            # **删除** —— 模型四周的物理墙已经由「外框层数」实现，切片器自己的
            # 墙层数交给用户预设。RNA 属性 bambu_wall_loops 保留（旧工程不炸），
            # 只是不再写进 3MF、也不再出现在面板上。
        else:
            box.label(text=I.t("切片器会用它自己的预设；多色需你自己分配耗材"),
                      icon="INFO")

        # ---- 生成结果 ----
        box = layout.box()
        box.label(text=I.t("生成结果"), icon="CHECKMARK")
        if props.status_text:
            for line in props.status_text.split("\n"):
                box.label(text=line[:70], text_ctxt=_TC)
        else:
            # v1.0.17：默认值不再是写死的中文，占位文案在这里按语言补。
            box.label(text=I.t("尚未生成"), icon="INFO")
        if props.detail_text:
            sub = box.box()
            for line in props.detail_text.split("\n")[:12]:
                sub.label(text=line[:70], text_ctxt=_TC)
        if props.risk_text:
            sub = box.box()
            sub.alert = True
            for line in props.risk_text.split("\n")[:10]:
                sub.label(text=line[:70], text_ctxt=_TC)
        box.operator("cstack.clear", icon="TRASH",
                     text=I.t("清除所有生成结果"))


classes = (
    CSTACK_PT_main,
    CSTACK_PT_step2,
    CSTACK_PT_params_quality,
    CSTACK_PT_quality,
    CSTACK_PT_color_height,
    CSTACK_PT_post,
    CSTACK_PT_step3,
)

# 注册期文案的翻译键（retranslate 时按当前语言重写类属性）
_PANEL_LABEL_KEYS = {
    "CSTACK_PT_step2": "第 2 步 · 参数设置",
    "CSTACK_PT_params_quality": "模型与尺寸",
    "CSTACK_PT_quality": "质量等级",
    "CSTACK_PT_color_height": "混色与配色",
    "CSTACK_PT_post": "第 3 步 · 后期处理",
    "CSTACK_PT_step3": "第 4 步 · 导出",
}
_PANEL_BY_ID = {cls.bl_idname: cls for cls in classes}


def _apply_labels():
    """按当前语言重写面板类属性（注册前调用）。"""
    global _CATEGORY
    _CATEGORY = I.t("叠色浮雕")
    CSTACK_PT_main.bl_label = "%s v%s" % (I.t(C.ADDON_NAME),
                                          C.ADDON_VERSION_STR)
    for cls in classes:
        cls.bl_category = _CATEGORY
    for idname, key in _PANEL_LABEL_KEYS.items():
        _PANEL_BY_ID[idname].bl_label = I.t(key)


def retranslate():
    """语言切换后调用：重注册面板让 bl_label / bl_category 生效。"""
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
