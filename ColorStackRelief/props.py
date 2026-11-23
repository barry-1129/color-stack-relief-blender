# -*- coding: utf-8 -*-
"""插件属性定义（PropertyGroup）。

界面文案随语言选择切换（v1.15.0 起）：所有 EnumProperty 统一用**动态 items
回调**（(self, context) 两参、default 用整数下标 —— 项目硬约束 19：
同一文件里静态/动态两种写法不能混，本文件自 v1.15.0 起全部为动态写法），
回调在每次重绘时经 ``i18n.t`` 出当前语言的文案。

【PropertyGroup 的 name/description 是注册期写入、RNA 层只读】
切语言必须**重建并重新注册**本类（见 ``retranslate``），且重建前保存用户已调的
参数值、重建后写回，避免清空用户调参。v1.15.0 因未处理这一点而把悬停提示推迟到
本版（v1.16.0）统一接入翻译。
"""

import bpy
from bpy.props import (
    BoolProperty, EnumProperty, FloatProperty, IntProperty,
    PointerProperty, StringProperty,
)

from . import constants as C
from . import i18n as I
from .core import color_engine as CE

# 【v1.16.1 BLT 免疫】所有属性统一声明 translation_context=_TC：
# 用户 Blender 界面语言为简体中文时，Blender 自带词典会把我们的英文属性名/
# 悬停描述/枚举项二次翻译（如 Resolution→分辨率、Balanced→均衡）。换了自定义
# 上下文后查不到自带词典条目，界面显示的就是我们产出的原文。
_TC = C.I18N_CONTEXT


# 切语言时保存/恢复的属性清单（保持用户已调参数，避免重建类时清空）
#
# 【v1.48.0 / R65：补齐漏项】原先漏了明信片与配色链路的一整批参数
# （back_image / back_image_path / back_mirror / post_front_mm / post_engrave_mm /
# mix_model / color_match），以及 4 个界面文案属性。
# 实测**当前并未真的丢失** —— retranslate 里读的是 `getattr(old, attr)`，而 Blender
# 对同名 IDProperty 会按名字继承，所以新类注册后值还在（探针 _review_retranslate
# 写 9 个非默认值再切语言，LOST_COUNT = 0）。但这是"依赖未写入文档的继承行为"，
# 一旦 Blender 改了实现就会静默回到出厂值。清单必须与属性声明逐项对齐，
# 新加属性时别忘了往这里也加一条。
_PRESERVE_ATTRS = (
    "language", "image_path", "image", "mode", "resolution", "size_mm",
    "max_thickness_mm", "mix_group", "color_basis",
    "nozzle_mm", "base_mm", "relief_mm", "stack_bands", "band_mm", "quality",
    "relief_side_own",
    "color_count", "mosaic_min_mm", "mosaic_base_key",
    "dither_pattern", "sampling", "quantize_colors", "despeckle",
    "auto_palette", "use_cyan", "use_magenta", "use_yellow", "use_white",
    "base_key", "band_ratio", "use_bambu_palette", "height_mode",
    "brightness_weight", "invert", "gamma", "anchor_mode", "lanyard",
    "lanyard_inner_r_mm", "lanyard_outer_r_mm", "lanyard_thickness_mm",
    "export_dir", "file_stem", "export_3mf", "export_obj",
    "split_by_material",
    # 明信片 / 反面图（v1.44.0 / R54；v1.45.2 加了 back_mirror）
    "back_image", "back_image_path", "back_mirror",
    "post_front_mm", "post_engrave_mm",
    # 配色链路（决定"选哪个叠法/怎么比色"，丢了会静默换结果）
    "mix_model", "color_match",
    # 界面文案（面板上的状态 / 详情 / 风险提示，丢了只是空白，不影响几何）
    "status_text", "detail_text", "risk_text", "preview_image",
    # 拓竹模式（切语言重注册时必须保留，否则会悄悄回到出厂值）
    # v1.39.0 起只剩开关 + 墙层数：其余 7 项已改为固定写入 constants.BAMBU_FIXED
    "bambu_mode", "bambu_wall_loops",
    # v1.25.0 生成代价（重注册时必须跟着走，否则语言切换后读数清零）
    "last_grams", "last_polys", "last_swaps", "last_volume_cm3",
)


# ---------------------------------------------------------------------------
# 枚举项（全部动态回调：参数 (self, context)，文案经 i18n.t 切语言）
# ---------------------------------------------------------------------------
def _color_items(self, context):
    """底板色 / 底色的可选颜色（v1.44.0：补齐 CMYK + RYBW 两组的主色）。

    【为什么只有这 6 个】用户手上就是这两套四色（CMYK / RYBW）。底板不是
    图案的一部分，多给绿/黑只会让菜单变长而没人选 —— 但 RYBW 用户需要
    "红底 / 蓝底"，所以必须把 R、B 补进来；否则 RYBW 组只有青品红黄白可选，
    和实际装料对不上（这正是 R54 修的那条）。
    """
    return [
        ("W", I.t("白色"),
         I.t("白色 —— 默认。照片还原、明信片反面都建议用它")),
        ("C", I.t("青色"),
         I.t("青色 —— 冷色底。CMYK 组的冷色基底")),
        ("M", I.t("品红"),
         I.t("品红色 —— CMYK 组的暖色基底")),
        ("Y", I.t("黄色"),
         I.t("黄色 —— CMYK / RYBW 都有这卷")),
        ("R", I.t("红色"),
         I.t("红色 —— RYBW 组的暖色；当底板会带明显的红边")),
        ("B", I.t("蓝色"),
         I.t("蓝色 —— RYBW 组的冷色；当底板会带明显的蓝边")),
    ]


def _mix_model_items(self, context):
    """混色模型两档（R19 引入，R29 调整顺序与文案）。

    v1.24.0：把「叠层混色」提到第一项并设为默认 —— 用户对照拓竹切片器的
    混色功能（同一位置一层青一层黄 → 远看是绿）后明确要求这种效果。之前的
    默认「单色平铺」是把每个像素压成一种耗材色、靠相邻点并排混，近看就是
    "青柱黄柱"。
    """
    return [
        ("translucent", I.t("叠层混色（推荐）"),
         I.t_desc("同一个点上下叠几层不同颜色的薄片，像拓竹的混色那样远看"
                  "混成新颜色；需要耗材能透一点光（PLA Basic 可以）",
                  "Stacked thin films mix by transmitted light, like Bambu "
                  "Studio Color Mixing; needs slightly translucent filament "
                  "(PLA Basic works)")),
        ("surface", I.t("兼容模式（不用透光）"),
         I.t_desc("每个点只用一种耗材色，靠相邻点并排混色；近看能看出"
                  "一颗颗色块，暗部会偏亮，但完全不依赖耗材透光 —— 换了不透光"
                  "的耗材（PETG、哑光 PLA）就选它",
                  "Each dot uses one filament colour; adjacent dots blend "
                  "spatially. Chunky up close and shadows look brighter, but "
                  "needs no translucency — pick this for opaque filament")),
    ]


def _color_match_items(self, context):
    """上色方式两档（v1.30.0）：整块上色 / 网点混色。"""
    items = []
    for key in C.COLOR_MATCH_KEYS:
        d = C.COLOR_MATCH_MODES[key]
        items.append((key, I.t(d["cn"]), I.t_desc(d["desc"], d["desc_en"])))
    return items


def _quality_items(self, context):
    """质量等级三档（名称用档位自带的 cn 字段，长描述用 t_desc 切语言）。"""
    items = []
    for key in C.QUALITY_KEYS:
        lv = C.QUALITY_LEVELS[key]
        items.append((key, I.t(lv["cn"]), I.t_desc(lv["desc"], lv["desc_en"])))
    return items


def _anchor_mode_items(self, context):
    """柱内叠色锚点模式三档（基础 / 扩增 / 柱内混色）。"""
    items = []
    for key in C.ANCHOR_MODE_KEYS:
        d = C.ANCHOR_MODES[key]
        items.append((key, I.t(d["cn"]), I.t_desc(d["desc"], d["desc_en"])))
    return items


def _mode_items(self, context):
    """三种生成模式。中文名统一从 constants.MODE_CN 取（唯一来源）。

    顺序按用户列举的 ① 全色平板 ② 叠色浮雕 ③ 双面平板 排列 ——
    面板下拉的可读顺序就是用户的思考顺序，不给"要先理解技术分类"的负担。
    """
    order = (
        ("mosaic",
         I.t("照片直接聚成 N 个色块，每块用一卷对应颜色的耗材平打，"
             "预览＝实物、最省心；代价是 N 卷料、换色约 N 次"),
         "Photo clustered into N flat colour blocks, one spool each. "
         "Preview equals the print; needs N spools and about N changes."),
        ("relief",
         I.t("明暗变柱高、柱顶叠色。4~8 卷料还原整张照片，换色少、打得快；"
             "暗部会略亮"),
         "Brightness becomes height, colours stack on the column top. "
         "4-8 spools cover the whole photo; shadows look slightly brighter."),
        ("postcard",
         I.t("正面叠色浮雕 + 反面单色凹刻（文字/邮戳），正反厚度分开设；"
             "比浮雕多一层背板"),
         "Relief front plus a single-colour engraved back (text / stamp). "
         "Front and back thickness are set separately."),
        ("card",
         I.t("正反两面各叠 6 层色片，两面同图、完全平整，像一张卡片；换色偏多"),
         "Six colour layers on each face, same image on both sides, flat. "
         "More colour changes than relief."),
        ("single",
         I.t("只在正面叠色，下面垫白挡串色，反面纯白；适合只看一面的卡片"),
         "Colour only on the front, white spacer underneath, plain white back. "
         "For one-sided cards."),
    )
    return [(mid, I.t_desc(C.MODE_CN[mid], C.MODE_EN[mid]),
             I.t_desc(desc, desc_en)) for mid, desc, desc_en in order]


def _mix_group_items(self, context):
    """四档混色色组（v1.23.0）。每档直接说明"要几卷、代价是什么"。"""
    out = []
    for key in C.COLOR_GROUP_KEYS:
        g = C.COLOR_GROUPS[key]
        out.append((key, I.t(g["cn"]),
                    I.t_desc(g["desc"], g.get("desc_en", g["desc"]))))
    return out


def _color_basis_items(self, context):
    """配色依据（v1.0.9）：按色板实测 / 按理论计算。"""
    return [(k, I.t(C.COLOR_BASES[k]["cn"]),
             I.t(C.COLOR_BASES[k]["desc"]))
            for k in C.COLOR_BASIS_KEYS]


def _height_mode_items(self, context):
    return [
        ("region", I.t("分区配色（推荐）"),
         I.t("颜色按像素独立分配、同一层里可以出现不同颜色，中间色相（绿/橙/紫）"
             "靠相邻像素的空间混色还原。颜色最准；代价是同一层内换色次数会增加")),
        ("coloraware", I.t("色带叠色（省换色）"),
         I.t("色相选色带、亮度定带内厚度，同一个高度上只有一种颜色。"
             "换色次数最少、打印最快，但色相只有四档，中间色会被并到最近的一档")),
        ("luminance", I.t("纯亮度"),
         I.t("只看明暗不看颜色，经典灰度浮雕做法。黑白图/剪影/线稿适用")),
    ]


def _nozzle_items(self, context):
    return [
        ("0.2", I.t("0.2 喷嘴"),
         I.t("挤出线宽按 %.2f mm 计，面板柱宽提醒也用这一档")
         % C.NOZZLE_LINE_WIDTH_MM["0.2"]),
        ("0.4", I.t("0.4 喷嘴"),
         I.t("挤出线宽按 %.2f mm 计，面板柱宽提醒也用这一档")
         % C.NOZZLE_LINE_WIDTH_MM["0.4"]),
    ]


def _dither_items(self, context):
    return [
        ("bluenoise", I.t("蓝噪声（推荐）"),
         I.t("32×32 蓝噪声：低频功率近 0、无离散谱峰，颗粒分布自然。"
             "还原度与 Bayer 完全相同（边缘分布相同），只是规则网格感消失")),
        ("bayer", I.t("有序方格"),
         I.t("传统 Bayer 矩阵（按质量等级用 4×4 或 8×8）。图案规则、每 2^n 格"
             "精确重复，近看有方格纹理。想复现旧版本效果时用")),
    ]


def _sampling_items(self, context):
    return [
        ("area", I.t("面积平均（照片）"),
         I.t("源图分辨率通常远高于柱子数，一格盖住几十个像素；取整块平均就是"
             "「这一小块整体看上去的颜色」。线稿上会抹掉细线")),
        ("nearest", I.t("最近邻（线稿 / 像素画）"),
         I.t("只取格心那一个源像素，不做抗锯齿 —— 细线和纯色块不会被冲淡。"
             "照片用它会出现边缘毛刺（逐格随机偏色）")),
    ]


def _lanyard_items(self, context):
    return [
        ("none", I.t("无"), I.t("不添加挂绳孔")),
        ("external", I.t("外置挂绳孔"),
         I.t("外置：圆环凸出在模型顶边外，与模型融合成一体")),
        ("internal", I.t("内置挂绳孔"),
         I.t("内置：在模型内打通一个竖直圆孔")),
    ]


def _language_items(self, context):
    # 语言名保持双语原样（选择器本身就是切换文案的开关，无需翻译）
    return [
        ("zh_CN", "简体中文 / Chinese", "界面使用简体中文"),
        ("en_US", "English / 英语", "Use English for the interface"),
    ]


# ---- 拓竹模式（v1.39.0：面板只留「墙层数」一个旋钮）----
# 层高/填充/Brim/支撑/擦料塔/温度都改由 constants.BAMBU_FIXED 固定写入，
# 不再做选择器 —— 用户口径："去掉红框内的说明和功能键"。
# 唯一保留的是墙层数：它直接决定模型外圈有几圈实墙，是本功能里用户唯一
# 凭肉眼能验证效果的旋钮。

def _on_language_change(self, context):
    """语言下拉的 update：切 i18n 全局 + 延迟重注册面板/算子/属性组。"""
    if I.LANG == self.language:
        return  # v1.16.1：值没变不重注册（load_post 恢复路径不触发 update，
                # 但防御性跳过可以避免多余的类重建）
    I.set_lang(self.language)
    I.request_retranslate()


def _clamp_lanyard_radii(props):
    """滑杆联动：保证「外环半径 ≥ 内孔半径 + 最小壁厚」（v1.48.0 / R65）。

    【为什么要有】这两个滑杆原先完全独立。用户把内孔拖到比外环还大时，
    外置圆环会按"外圈比内圈小"建面 —— 四条边反向外翻，切出来是堵死的实心饼
    （实测体积反而变大）或者一堆零面积面。算子层已会抛错兜底，但那时用户已经
    点了「生成挂绳孔」才看到报错；在滑杆这一层直接拉正，用户根本走不到错误态。

    内置孔的「外环半径」只是孔心离顶边的距离基准，同样取这个下限：
    孔顶就不会冲出模型顶边（把边啃成缺口），方向是安全的。
    """
    try:
        r_in = float(props.lanyard_inner_r_mm)
        r_out = float(props.lanyard_outer_r_mm)
    except (AttributeError, TypeError, ValueError):
        return
    need = r_in + float(C.LANYARD_MIN_WALL_MM)
    if r_out < need - 1e-9:
        # 只往上钳，不往回拖内孔 —— 用户刚拖的那个滑杆要留在原地
        props.lanyard_outer_r_mm = min(need, 10.0)


def _on_lanyard_inner_change(self, context):
    _clamp_lanyard_radii(self)


def _on_lanyard_outer_change(self, context):
    _clamp_lanyard_radii(self)


def _on_detail_level_change(self, context):
    """v1.0.14：只要用户动过档位，就以档位为准，同步写回 resolution。

    【为什么需要】旧工程只设过 resolution（比如 455），detail_level 仍是默认 4。
    as_params 为了不偷偷降采样会沿用 455；但如果不同步，用户把滑杆拖到 5
    再拖回 4，as_params 又会落回"旧格数 455"，面板却显示第 4 档 —— 档位失效。
    读文件恢复属性不会触发 update，所以旧工程的 455 仍然安全。
    """
    try:
        self.resolution = C.detail_level_res(self.detail_level)
    except Exception:  # noqa: BLE001 —— 面板回调绝不能把界面弄崩
        pass


# ---------------------------------------------------------------------------
# 属性组构造（用当前语言 I.LANG 生成 —— 切语言时整类重建）
# ---------------------------------------------------------------------------
def _make_props_class():
    """构造 CSTACK_Properties 类，name/description 取当前语言的译文。"""

    class CSTACK_Properties(bpy.types.PropertyGroup):

        # ---------------- 语言（v1.15.0，置于一切之前） ----------------
        language: EnumProperty(
            translation_context=_TC,
            name=I.t("语言选择"),
            description="切换整个插件界面的显示语言 / Switch the whole add-on UI",
            items=_language_items,
            default=0,
            update=_on_language_change,
        )

        # ---------------- 输入 ----------------
        image: PointerProperty(
            translation_context=_TC,
            name=I.t("图片"),
            description=I.t("选择一张已载入 Blender 的图片作为输入"),
            type=bpy.types.Image,
        )

        image_path: StringProperty(
            translation_context=_TC,
            name=I.t("图片文件"),
            description=I.t("从磁盘载入图片"),
            subtype="FILE_PATH",
            default="",
        )

        # ---- 明信片浮雕的反面图（v1.44.0 / R54）----
        # 只有 postcard 模式用得到。留空时背面是平的（只打背板），不报错 ——
        # 用户可能只想先看正面效果。
        back_image: PointerProperty(
            translation_context=_TC,
            name=I.t("反面图片"),
            description=I.t("明信片浮雕的反面图案（文字/邮戳/二维码等）。"
                            "越暗的地方刻得越深；留空则背面是平的"),
            type=bpy.types.Image,
        )

        back_image_path: StringProperty(
            translation_context=_TC,
            name=I.t("反面图片文件"),
            description=I.t("从磁盘载入明信片反面的图片"),
            subtype="FILE_PATH",
            default="",
        )

        # v1.45.2 / R58：反面图左右镜像（用户点名要的按钮）。
        # 反面是"纸的背面"：拿现成的邮戳/文字图直接刻，字常是反的；
        # 或者想让反面和正面图案左右对称。给一个开关，别让用户去 PS 里翻。
        back_mirror: BoolProperty(
            translation_context=_TC,
            name=I.t("反面图左右镜像"),
            description=I.t("把反面图片绕竖直中轴线左右翻转后再凹刻。"
                            "字是反的、或想和正面图案左右对称时打开"),
            default=False,
        )

        # ---------------- 模型 ----------------
        mode: EnumProperty(
            translation_context=_TC,
            name=I.t("生成模式"),
            description=I.t("选择模型的几何形态。五种模式都能设尺寸、精细度和"
                            "质量等级；厚度按模式自动计算（明信片另有两项）。"
                            "混色组的档数不同（全色平板不用混色）"),
            items=_mode_items,
            default=C.MODE_IDS.index(C.DEFAULT_MODE),
        )

        resolution: IntProperty(
            translation_context=_TC,
            name=I.t("模型精细度"),            description=I.t("图片最长边采成多少个格子 —— 格子越密细节越多，"
                            "面数和生成时间按平方增长。\n"
                            "但颜色能细到多少由喷嘴决定：0.4 喷嘴一条挤出线"
                            "约 0.42mm（100mm 长边约 238 个色点），0.2 喷嘴"
                            "约 0.22mm（约 454 个）。超过这个数只会让模型更重，"
                            "画面不会更细。\n"
                            "（它也不决定「用几卷耗材」—— 耗材卷数由质量等级"
                            "和混色组决定）"),
            default=C.DEFAULT_RES,
            min=C.MIN_RES,
            max=C.MAX_RES,
            # 软上限刻意停在 400（v1.7.4 之前的硬上限）——滑块拖到这儿就够日常用了，
            # 再往上要手输数值，避免手一滑拖出百万柱把机器卡住。
            soft_min=16,
            soft_max=400,
        )

        # ---- 分辨率档位（v1.0.14，用户 2026-11-20）----
        # 面板只暴露 1~10 档；旧的 resolution（格数）保留，供旧工程/旧脚本。
        detail_level: IntProperty(
            translation_context=_TC,
            name=I.t("分辨率"),
            description=I.t("分辨率越高，细节越高；生成模型时耗费更多的时间和系统内存"),
            default=C.DEFAULT_DETAIL_LEVEL,
            min=1,
            max=C.MAX_DETAIL_LEVEL,
            update=_on_detail_level_change,
        )

        size_mm: FloatProperty(
            translation_context=_TC,
            name=I.t("最长边尺寸 (mm)"),
            description=I.t("模型最长边的实际打印尺寸，另一边按图片比例自动换算"),
            default=C.DEFAULT_SIZE_MM,
            min=C.MIN_SIZE_MM,
            max=C.MAX_SIZE_MM,
            soft_min=20.0,
            soft_max=220.0,
        )

        # ---- 外框层数（v1.0.14，用户 2026-11-20）----
        # 位置紧跟在「最长边尺寸」下面（面板绘制顺序见 panels）。
        # 1 层 = 0.40mm（一个 0.1mm 打印层高的 4 倍），不再是固定 1.5mm。
        rim_layers: IntProperty(
            translation_context=_TC,
            name=I.t("外框层数"),
            description=I.t("模型四周的物理外框层数：每层 0.40mm。\n"
                            "1 层 = 0.40mm，2 层 = 0.80mm，0 = 不加外框。\n"
                            "成品最长边 = 最长边尺寸 + 2×层数×0.40mm。\n"
                            "（这是模型外框，不是切片器里的墙圈数）"),
            default=C.MODEL_RIM_LAYERS_DEFAULT,
            min=0,
            max=C.MODEL_RIM_MAX_LAYERS,
        )

        # ---- 明信片浮雕的两项厚度（v1.44.0 / R54，仅 postcard 模式显示）----
        # 【为什么正面用"总厚"而不是"起伏层厚"】用户说的是"正面叠色浮雕的最大
        # 厚度" —— 他关心的是"正面这一层有多厚"，不是内部怎么拆。0.40 底板
        # 是固定结构，起伏层 = 这个值 − 0.40，与 relief 完全同一条公式。
        post_front_mm: FloatProperty(
            translation_context=_TC,
            name=I.t("正面叠色浮雕厚度 (mm)"),
            description=I.t("明信片正面这一层的总厚（柱顶色膜 + 能上下起伏的层）。"
                            "反面已经是白板，所以正面不再垫白底板。"
                            "越厚起伏越大、画面越有立体感，也越费料和时间；"
                            "1.2mm 只有色膜（平板），3mm 有 1.8mm 落差"),
            default=C.POST_FRONT_MM,
            min=C.POST_FRONT_MIN_MM,
            max=C.POST_FRONT_MAX_MM,
            step=10,
            precision=2,
            subtype="DISTANCE",
        )

        post_engrave_mm: FloatProperty(
            translation_context=_TC,
            name=I.t("反面凹刻深度 (mm)"),
            description=I.t("明信片反面文字/图案刻下去的最大深度。"
                            "越深摸起来越明显、侧光下越清楚；"
                            "但每加深 0.1mm，整件就要多 0.1mm 背板，"
                            "太深会让正面起伏变薄"),
            default=C.POST_ENGRAVE_MM,
            min=C.POST_ENGRAVE_MIN_MM,
            max=C.POST_ENGRAVE_MAX_MM,
            step=5,
            precision=2,
            subtype="DISTANCE",
        )

        # 模型厚度（v1.23.0 引入，v1.27.0 起**从面板收起、不再参与计算**）。
        # 用户口径（2026-10-23）：三种模式都按"最优厚度"自动算，不再手设。
        # RNA 保留只为兼容旧 .blend / 旧脚本 —— as_params 已忽略它，改用
        # ``C.auto_thickness_total``。删掉属性会让旧工程加载时丢数据。
        max_thickness_mm: FloatProperty(
            translation_context=_TC,
            name=I.t("模型厚度 (mm，已改为自动)"),
            description=I.t("v1.27.0 起厚度由插件按模式与幅面自动计算"
                            "（全色平板按幅面取色块层、叠色浮雕按长边的 2.2% "
                            "取起伏层、双面平板固定 1.2mm）。\n"
                            "本项仅保留用于兼容旧工程，改了不生效。"),
            default=C.DEFAULT_MAX_THICKNESS_MM,
            min=C.MIN_MAX_THICKNESS_MM,
            max=C.MAX_MAX_THICKNESS_MM,
            subtype="DISTANCE",
        )

        # 喷嘴档位（v1.7.4）：面板上「0.2 喷嘴」「0.4 喷嘴」两个键各设一档并对齐。
        # 它**不参与建模**，所以不进 as_params() —— 只决定对齐目标与面板的柱宽提醒。
        nozzle_mm: EnumProperty(
            translation_context=_TC,
            name=I.t("喷嘴档位"),
            description=I.t("当前切片用的喷嘴孔径。只影响「对齐喷嘴」的目标柱宽与面板的"
                           "柱宽提醒，不参与建模；换喷嘴后点一下对应按钮即可"),
            items=_nozzle_items,
            default=("0.2", "0.4").index(C.DEFAULT_NOZZLE),
        )

        base_mm: FloatProperty(
            translation_context=_TC,
            name=I.t("底板厚度 (mm)"),
            description=I.t("最底下那层实心白板的厚度，保证底部平整、不透光"),
            default=C.DEFAULT_BASE_MM,
            min=C.MIN_BASE_MM,
            max=C.MAX_BASE_MM,
        )

        relief_mm: FloatProperty(
            translation_context=_TC,
            name=I.t("叠色层总厚 (mm)"),
            description=I.t("四色叠色区的总高度。越厚颜色层次越丰富，但打印越久"),
            default=C.DEFAULT_RELIEF_MM,
            min=C.MIN_RELIEF_MM,
            max=C.MAX_RELIEF_MM,
        )

        stack_bands: IntProperty(
            translation_context=_TC,
            name=I.t("每面层数"),
            description=I.t("叠色层数。单面平板固定 8 层彩膜（不用调，8 层是"
                           "色阶收益的拐点：165 种叠法，比 4 层的 35 种多 4.7 倍）；"
                           "双面叠色固定每面 6 层（背面自动镜像，总 12 层、"
                           "1.20mm 厚）—— 白可以排到彩膜外侧，6 层能叠出整片"
                           "浅色与中间调。本滑杆只影响浮雕等其它模式"),
            default=C.DEFAULT_STACK_BANDS,
            min=C.MIN_STACK_BANDS,
            max=C.MAX_STACK_BANDS,
        )

        band_mm: FloatProperty(
            translation_context=_TC,
            name=I.t("单片厚度 (mm)"),
            description=I.t("叠色卡：每层色片的厚度，建议等于打印层高（0.08~0.12）"),
            default=C.DEFAULT_BAND_MM,
            min=C.MIN_BAND_MM,
            max=C.MAX_BAND_MM,
        )

        # v1.32.0（R39）：浮雕的竖墙（高低落差那面）用什么颜色。
        relief_side_own: BoolProperty(
            translation_context=_TC,
            name=I.t("侧面跟着自己的颜色"),
            description=I.t("浮雕起伏的侧面（高低落差那一面）用这块图案自己的颜色，"
                           "而不是底板白。关掉的话落差大的地方会露出白色竖条、"
                           "细的彩色线条会发白；代价是每一层都要换 2~4 次料，"
                           "耗材和打印时间都会增加"),
            default=C.DEFAULT_RELIEF_SIDE_OWN,
        )

        # ---- 平整色块板（v1.22.0）----
        color_count: IntProperty(
            translation_context=_TC,
            name=I.t("色块数"),
            description=I.t("把照片聚成多少个色块 —— 等于这项打印要用的彩色耗材卷数。"
                           "越大越接近原图，但换色次数随之线性上升：8 色大致在一台"
                           "AMS（4 槽 ×2 级）能自动完成的范围内，16 色需要四级串联。"
                           "每加一色带来的还原改善会迅速变小，8 色之后通常已看不出差别"),
            default=C.DEFAULT_COLOR_COUNT,
            min=C.MIN_COLOR_COUNT,
            max=C.MAX_COLOR_COUNT,
        )

        mosaic_min_mm: FloatProperty(
            translation_context=_TC,
            name=I.t("最小色块 (mm)"),
            description=I.t("小于这个尺寸的碎色斑会被并给相邻大色块。"
                           "不合并的碎斑在实物上有两个害处：被切片器的小特征过滤"
                           "（色块直接消失）、以及每进出一次就换一次料。"
                           "0 = 关闭（原样保留每一个色斑）"),
            default=C.DEFAULT_MOSAIC_MIN_MM,
            min=C.MIN_MOSAIC_MIN_MM,
            max=C.MAX_MOSAIC_MIN_MM,
            subtype="DISTANCE",
        )

        # 色块板的底板色**单独一项**，不复用 base_key。
        # 理由：base_key 是叠色链路的「柱底共用色」，默认天蓝（C）——那是
        # CMYK 叠色的语义（青是冷色基底）。色块板不做叠色，底板是**露在外面**
        # 的：底面 + 侧壁 + 色块之间的缝，全是这个颜色。默认天蓝会让一块还原
        # 照片的板子带一圈青边（实测 R27 首版交付件就是这样）。照片还原的自然
        # 默认是白色。
        #
        # v1.44.0（R54）：明信片浮雕**共用这一项** —— 明信片的「底板色」就是
        # 反面那块单色凹刻板（露在反面 + 四周侧壁）。两者语义完全一致
        # （露在外面的板体色），所以不新增属性、避免出现两个底板色互相打架。
        mosaic_base_key: EnumProperty(
            translation_context=_TC,
            name=I.t("底板色"),
            description=I.t("底板（露在外面的板体）的颜色：全色平板用在底面、"
                            "侧壁和色块缝隙；明信片浮雕用在**整个反面**。"
                            "照片还原建议白色；选彩色会让侧边/反面整片带上那个"
                            "颜色，而且要多备一卷那种料"),
            items=_color_items,
            default=0,
        )

        # ---------------- 质量等级 ----------------
        quality: EnumProperty(
            translation_context=_TC,
            name=I.t("质量等级"),
            description=I.t("一次设定色彩还原与换色次数的整体取舍。\n"
                           "极致 = 优先色彩还原；均衡 = 两者兼顾；像素 = 优先减少换色"),
            items=_quality_items,
            default=C.QUALITY_KEYS.index(C.DEFAULT_QUALITY),
        )

        # 四档混色色组（v1.23.0 / R28）：②③ 专用，① 全色平板不画这一项。
        # 色组是**硬约束**（生成侧不再自动挑色）：用户买了几卷什么色，就按那几卷算。
        mix_group: EnumProperty(
            translation_context=_TC,
            name=I.t("混色色组"),
            description=I.t("叠色浮雕 / 双面平板用哪几卷耗材。\n"
                            "CMYK = 青 / 品红 / 黄 / 白（减色混合）\n"
                            "RYBW = 红 / 黄 / 蓝 / 白（视觉叠色）\n"
                            "CMYWGK = 青 / 品红 / 黄 / 白 / 绿 / 黑\n"
                            "8色MAX = 青 / 品红 / 黄 / 白 / 红 / 绿 / 蓝 / 黑"),
            items=_mix_group_items,
            default=C.COLOR_GROUP_KEYS.index(C.DEFAULT_COLOR_GROUP),
        )

        # v1.0.9：配色**依据**（用户 2026-11-15 要求加在混色色组里）。
        # 它只决定"用不用实拍反解出来的参数表"，不影响选哪几卷料。
        color_basis: EnumProperty(
            translation_context=_TC,
            name=I.t("计算依据"),
            description=I.t("按色板计算：用打印出来的标定板实拍反解参数，"
                            "跟手上这批耗材最接近（推荐）。\n"
                            "按理论计算：完全按上面色组给定的颜色做理想减色叠加，"
                            "不套用任何实拍修正 —— 换色组或改色值都能算，"
                            "但与实物的偏差没有校正。"),
            items=_color_basis_items,
            default=C.COLOR_BASIS_KEYS.index(C.DEFAULT_COLOR_BASIS),
        )

        mix_model: EnumProperty(
            translation_context=_TC,
            name=I.t("混色模型"),
            description=I.t("颜色怎么混出来：\n"
                           "叠层混色 = 同一个点上下叠几层不同颜色的 0.1mm 薄片，"
                           "光穿过去后混成新颜色（与拓竹切片器的混色同一原理）；"
                           "耗材要能透一点光，PLA Basic 可以。\n"
                           "兼容模式 = 每个点只用一种耗材色，靠相邻点并排混色；"
                           "完全不依赖透光，暗部会偏亮、近看能看出色块，"
                           "换不透光的耗材时用它。\n"
                           "「全色平板」不用混色，本项对它无效"),
            items=_mix_model_items,
            default=0,
        )

        # 【v1.30.0 / R37 新增】上色方式。
        # 实拍发现旧链路（逐像素凸包投影 + 有序抖动）在 0.4mm 喷嘴上根本站不住：
        # 每个色点周边都会溢料，满图色点互相污染 → 远看彩噪、近看碎渣。
        # 「整块上色」改成：整图先按可打印叠法做最近邻匹配，再把小于
        # min_feature_mm 的碎色块并给邻近大块，一个点内不再混色 ——
        # 打印路径连续、换色少、边界干净，与 Lumina Studio 的做法一致。
        color_match: EnumProperty(
            translation_context=_TC,
            name=I.t("上色方式"),
            description=I.t("同一片区域里的颜色怎么落到点上：\n"
                           "整块上色 = 一块区域只叠一种颜色，边界清楚、不撒碎点；"
                           "近看是干净的色块，最接近实拍的干净观感。推荐。\n"
                           "网点混色 = 相邻点用不同叠法交错排列，远看混出新颜色；"
                           "近看是密密麻麻的色点，0.4mm 喷嘴会把色点边缘糊在一起，"
                           "实测容易发脏、发彩噪。\n"
                           "「全色平板」不用混色，本项对它无效"),
            items=_color_match_items,
            default=C.COLOR_MATCH_KEYS.index(C.DEFAULT_COLOR_MATCH),
        )

        # 【v1.26.0 / R32 面板已收起】颗粒图案 / 颜色量化 / 清理孤立点三项
        # 继续保留 RNA（旧 .blend、旧脚本、探针都还在写它们），但**面板不再
        # 暴露**：实测三项都不能改变"远看像不像"（蓝噪声与 Bayer 的还原度
        # 逐位相同，只差颗粒观感；量化 16 只是快两个数量级；去孤立点只改
        # 3.89% 的格、远看 dE 不变），留在面板上只会让人以为要调。
        # 生产口径由 constants 的三个默认值钉死：DEFAULT_DITHER_PATTERN /
        # DEFAULT_QUANTIZE_COLORS / DEFAULT_DESPECKLE。
        dither_pattern: EnumProperty(
            translation_context=_TC,
            name=I.t("颗粒图案"),
            description=I.t("抖动阈值用哪种图案。两种图案的「远看平均色」完全相同（都是"
                           "均匀分布、每个阈值恰好用一次），区别只在颗粒怎么排布："
                           "蓝噪声 = 点彼此尽量远离、没有规则网格，像胶片噪点，观感更自然；"
                           "有序方格 = 传统 Bayer 矩阵，颗粒呈规则方格，近看能看出格子纹理"),
            items=_dither_items,
            default=("bluenoise", "bayer").index(C.DEFAULT_DITHER_PATTERN),
        )

        sampling: EnumProperty(
            translation_context=_TC,
            name=I.t("采样方式"),
            description=I.t("一个格子怎么代表它盖住的那块源像素\n"
                           "面积平均 = 取整块平均，接近人眼在观看距离上的空间积分，"
                           "照片/插画用这个\n"
                           "最近邻 = 取格心那一个源像素，不抗锯齿。线稿、像素画用这个："
                           "面积平均会把 1 像素宽的细线冲淡到几乎全白，最近邻能把原色保住"),
            items=_sampling_items,
            default=("area", "nearest").index(C.DEFAULT_SAMPLING),
        )

        quantize_colors: IntProperty(
            translation_context=_TC,
            name=I.t("颜色量化"),
            description=I.t("先把图片的目标色聚成这么多簇，再做凸包投影与抖动。"
                           "相邻格落进同一簇后权重完全相同 → 抖动排布更干净；"
                           "同时逐格投影的计算次数从「几千种颜色」降到「几十簇」，"
                           "生成速度快两个数量级。0 = 关闭"),
            default=C.DEFAULT_QUANTIZE_COLORS,
            min=0,
            max=C.QUANTIZE_COLORS_MAX,
            subtype="NONE",
        )

        despeckle: BoolProperty(
            translation_context=_TC,
            name=I.t("清理孤立点（保色）"),
            description=I.t("把「8 个邻居全都和自己不同」的孤立格换成邻域多数色。"
                           "判据自带保色性：1 像素宽的细线沿线邻居同类，不会被误伤。"
                           "主要收益是少几个孤零零的色点、顺带省换色。"
                           "关闭颜色量化时它照样生效 —— 那时只把孤立格换成邻居已有的"
                           "颜色，不引入任何新色（v1.7.4）"),
            default=C.DEFAULT_DESPECKLE,
        )

        # ---------------- 配色（自由配色：颜色没有层序要求） ----------------
        auto_palette: BoolProperty(
            translation_context=_TC,
            name=I.t("自动挑选颜色"),
            description=I.t("统计图片的色相分布，在允许的颜色里自动挑出还原最好的一套。"
                           "关闭后严格按下面勾选的颜色使用"),
            default=True,
        )

        use_cyan: BoolProperty(
            translation_context=_TC,
            name=I.t("允许使用 天蓝"), description=I.t("允许青色参与配色"),
            default=True)
        use_magenta: BoolProperty(
            translation_context=_TC,
            name=I.t("允许使用 品红"), description=I.t("允许品红色参与配色"),
            default=True)
        use_yellow: BoolProperty(
            translation_context=_TC,
            name=I.t("允许使用 黄色"), description=I.t("允许黄色参与配色"),
            default=True)
        use_white: BoolProperty(
            translation_context=_TC,
            name=I.t("允许使用 白色"),
            description=I.t("允许白色参与配色。灰、白像素只有靠白色才还原得出来，"
                           "一般不要关闭"), default=True)

        base_key: EnumProperty(
            translation_context=_TC,
            name=I.t("底色"),
            description=I.t("底板使用的颜色（柱子最底下那一段）"),
            items=_color_items,
            default=("W", "C", "M", "Y").index("C"),
        )

        band_ratio: FloatProperty(
            translation_context=_TC,
            name=I.t("共用底带"),
            description=I.t("柱子底部按「叠色层总厚」的这个比例统一用底色 —— 也就是"
                           "同一根柱子沿 Z 轴分成两段颜色（下段底色 / 上段像素自己的颜色）。"
                           "0 = 关闭（默认）。仅对「分区配色」生效。"
                           "注意：实测它并不能减少换色次数（原因见说明文档），"
                           "慎调大，会削弱侧壁的混色观感"),
            default=0.0,
            min=0.0,
            max=0.6,
            subtype="FACTOR",
        )

        use_bambu_palette: BoolProperty(
            translation_context=_TC,
            name=I.t("按拓竹套装色显示"),
            description=I.t("只影响切片软件里看到的颜色（把四色换成拓竹 CMYK "
                           "套装的实测色值），让 3MF 打开时更接近实物。\n"
                           "**不影响打出来的颜色，也不影响用料**：混色计算对 "
                           "C/M/Y/W 恒定使用同一张实拍标定表，与这里勾不勾无关。"),
            default=False,
        )

        # v1.9.0（R5）：「每色阶数」功能已移除 —— 实测对呈现效果几乎无可见差异。

        # ---------------- 高度映射 ----------------
        height_mode: EnumProperty(
            translation_context=_TC,
            name=I.t("配色与高度方式"),
            description=I.t("决定颜色怎么分配、高度怎么算"),
            items=_height_mode_items,
            default=C.HEIGHT_MODES.index(C.DEFAULT_HEIGHT_MODE),
        )

        brightness_weight: FloatProperty(
            translation_context=_TC,
            name=I.t("明暗层次"),
            description=I.t("分区配色下控制高度起伏的强弱：1 = 完整明暗层次，"
                           "越小越接近等厚（颜色完全靠空间混色表现）；"
                           "色带叠色模式下则是亮度对高度的贡献比例"),
            default=1.0,
            min=0.0,
            max=1.0,
            subtype="FACTOR",
        )

        invert: BoolProperty(
            translation_context=_TC,
            name=I.t("反转明暗"),
            description=I.t("把明暗颠倒，亮的地方变矮、暗的地方变高"),
            default=False,
        )

        gamma: FloatProperty(
            translation_context=_TC,
            name=I.t("对比度"),
            description=I.t("调整高度分布的对比度。大于 1 拉大明暗差距，小于 1 更平缓"),
            default=1.0,
            min=0.2,
            max=4.0,
            soft_min=0.5,
            soft_max=2.5,
        )

        anchor_mode: EnumProperty(
            translation_context=_TC,
            name=I.t("锚点模式"),
            description=I.t("柱内叠色的可达色域档位（分层浮雕的分区配色用；"
                           "v1.10.0 起叠色链路常开、不再有开关）：基础（8 色）＝白底 + "
                           "每色至多一片；扩增（白底叠色 64 色）＝白底固定、3 层自由色片"
                           "可重复可换序（4³=64）；柱内混色（自由底 256 色）＝底色不固定、"
                           "共 4 层（4⁴=256），完全覆盖旧 45 色档。档位越高色域越广、"
                           "暗部与高饱和区改善越大，生成越慢。"
                           "「平整色块板」不做叠色投影，本项对它无效"),
            items=_anchor_mode_items,
            default=C.ANCHOR_MODE_KEYS.index(C.DEFAULT_ANCHOR_MODE),
        )

        # ---------------- 后处理（v1.15.0 起） ----------------
        lanyard: EnumProperty(
            translation_context=_TC,
            name=I.t("挂绳孔"),
            description=I.t("在照片顶部居中加一个挂绳孔。外置 = 圆环凸出在模型顶边外"
                           "并融合成一体，环底与浮雕底面齐平；内置 = 在模型内打通"
                           "一个竖直圆孔。内孔 / 外环 / 厚度可在下方微调"),
            items=_lanyard_items,
            default=0,
        )

        # 挂绳孔几何参数（v1.16.0：从常量提升为可调，覆盖默认值）
        # v1.48.0（R65）：两个半径滑杆联动 —— 外环必须比内孔大至少
        # LANYARD_MIN_WALL_MM，否则圆环会自己压住自己（详见 _clamp_lanyard_radii）
        lanyard_inner_r_mm: FloatProperty(
            translation_context=_TC,
            name=I.t("内孔半径 (mm)"),
            description=I.t("挂绳孔的内孔半径。外置=圆孔内缘、内置=圆孔半径"),
            default=float(C.LANYARD_INNER_R_MM),
            min=0.5,
            max=6.0,
            soft_min=1.0,
            soft_max=3.0,
            update=_on_lanyard_inner_change,
        )
        lanyard_outer_r_mm: FloatProperty(
            translation_context=_TC,
            name=I.t("外环半径 (mm)"),
            description=I.t("挂绳孔的外环半径（外置圆环的外缘；"
                           "内置时作孔心到顶边的距离基准）"),
            default=float(C.LANYARD_OUTER_R_MM),
            min=1.0,
            max=10.0,
            soft_min=1.5,
            soft_max=5.0,
            update=_on_lanyard_outer_change,
        )
        lanyard_thickness_mm: FloatProperty(
            translation_context=_TC,
            name=I.t("挂绳孔厚度 (mm)"),
            description=I.t("挂绳孔轴向厚度（仅外置生效：外置圆环的高度；内置孔始终贯通）。"
                           "0 或大于模型厚度则取模型厚度"),
            default=float(C.LANYARD_DEFAULT_THICKNESS_MM),
            min=0.0,
            max=10.0,
            soft_min=1.0,
            soft_max=5.0,
        )

        # ---------------- 导出 ----------------
        export_dir: StringProperty(
            translation_context=_TC,
            name=I.t("导出目录"),
            description=I.t("3MF / OBJ 的保存位置。留空则导出时落到桌面"
                           "「叠色浮雕工坊文件夹」并自动创建"),
            subtype="DIR_PATH",
            default="",
        )

        file_stem: StringProperty(
            translation_context=_TC,
            name=I.t("文件名"),
            description=I.t("导出文件的基础名（不含扩展名）。留空则使用默认名"),
            default="",
        )

        export_3mf: BoolProperty(
            translation_context=_TC,name=I.t("导出 3MF"), default=True)
        export_obj: BoolProperty(
            translation_context=_TC,name=I.t("导出 OBJ"), default=True)

        split_by_material: BoolProperty(
            translation_context=_TC,
            name=I.t("3MF 按颜色拆分部件"),
            description=I.t("每种颜色一个独立部件并标好耗材槽，"
                           "拓竹打开后可直接多色打印。关闭则输出单件多材质"),
            default=True,
        )

        # v1.39.0：OBJ 材质开关并入「导出 OBJ」—— OBJ 本体不存颜色，颜色只能
        # 由同名 .mtl 承载。分两个开关的结果就是"导出了 OBJ 却没有颜色"，
        # 所以只要勾了导出 OBJ 就必然写 .mtl（见 generation.export_all）。

        # ---------------- 拓竹模式（v1.38.0） ----------------
        # 【设计口径】开 = 3MF 按颜色拆成独立部件、每个部件绑一个耗材槽，并写入
        # 切片参数；关 = 只输出几何 + 颜色，耗材与参数一个字都不写，切片器用它
        # 自己的预设。用户要的是"能不能让插件替我配好拓竹"，不是"再来一堆旋钮"。
        bambu_mode: BoolProperty(
            translation_context=_TC,
            name=I.t("拓竹模式"),
            description=I.t("开启后：3MF 按颜色拆成独立部件，每个部件绑定一个耗材槽，"
                           "并把下面的切片参数写进文件 —— 拓竹打开就能直接切片。\n"
                           "关闭后：3MF 只带几何与颜色，不写耗材分配、不写切片参数，"
                           "切片器完全用它自己的预设（多色需要你自己分配耗材）"),
            default=True,
        )

        bambu_wall_loops: IntProperty(
            translation_context=_TC,
            name=I.t("墙层数"),
            description=I.t("模型最外圈打印几层实墙。0 = 不生成墙（外圈只剩填充，"
                           "边缘会更软、能看到填充纹路）；1 = 只有一圈外墙，薄板上"
                           "与填充同色同高、肉眼几乎看不出来；2 起才有内墙，边缘更"
                           "结实也更耐磕。圈数越多越费时（每圈都要沿整圈走一遍）"),
            default=C.BAMBU_WALL_LOOPS_DEFAULT,
            min=C.BAMBU_WALL_LOOPS_MIN,
            max=C.BAMBU_WALL_LOOPS_MAX,
        )

        # ---------------- 状态（只读展示） ----------------
        status_text: StringProperty(
            translation_context=_TC, default="")
        # v1.0.17：默认值改成空串 —— 原来写死中文「尚未生成」，英文界面会露中文。
        # 空占位由面板用 I.t("尚未生成") 补，跟着语言走。
        detail_text: StringProperty(
            translation_context=_TC,default="")
        risk_text: StringProperty(
            translation_context=_TC,default="")

        preview_image: PointerProperty(
            translation_context=_TC,
            name=I.t("配色预览图"),
            description=I.t("按当前配色估算出的呈现色对照图"),
            type=bpy.types.Image,
        )

        # ---- 上一版生成的代价（v1.25.0，只读展示）----
        # 用户的原话："每个参数要能一眼看出代价（换色次数／耗材卷数／打印时间）"。
        # 生成完立刻把这三个数摆在参数面板上，不用去切片器里才发现要打 10 小时。
        last_grams: FloatProperty(
            translation_context=_TC, default=0.0,
            description=I.t("上一版模型的耗材重量（按实心体积 × 1.24 g/cm³ 算，"
                            "不含擦料塔）"))
        last_polys: IntProperty(
            translation_context=_TC, default=0,
            description=I.t("上一版模型的三角面数（越多切片越慢）"))
        last_swaps: IntProperty(
            translation_context=_TC, default=0,
            description=I.t("上一版模型预计的换色次数（每次换色要擦料，"
                            "约多花 1 分钟）"))
        last_volume_cm3: FloatProperty(
            translation_context=_TC, default=0.0,
            description=I.t("上一版模型的实体体积"))

        # ---- 拓竹模式：覆盖层构造（导出器只消费这一个方法） ----
        def bambu_process_overrides(self):
            """面板 → ``project_settings.config`` 的工艺覆盖值。

            v1.0.15（用户 2026-11-20 第 9 条）：**停止覆盖 wall_loops**。
            模型四周的物理墙由「外框层数」实现，切片器自己的墙层数应当回到
            用户预设 / 模板值 —— 面板上那个旋钮已经删除，再偷偷写一个值进去
            就是"面板没有、文件里有"，最难排查的一类分叉。
            其余键仍由 :data:`constants.BAMBU_FIXED` 写死（值与出厂默认相同），
            剩下 560+ 键由 ``resources/bambu_a1_template.json`` 提供。
            """
            return dict(C.BAMBU_FIXED)

        @property
        def allowed_colors(self):
            """允许参与配色的颜色集合。

            【注意】这里返回的是**集合**，没有顺序含义 —— 用户已确认颜色没有层序
            要求（机器可在任意高度换色、同一层也可不同色）。早期版本的"从最底层 →
            最顶层"四段色序是多余的约束，v1.2 已去掉。
            """
            out = []
            if self.use_cyan:
                out.append("C")
            if self.use_magenta:
                out.append("M")
            if self.use_yellow:
                out.append("Y")
            if self.use_white:
                out.append("W")
            return out or ["W"]

        @property
        def quality_level(self):
            """当前质量等级的参数包（供面板只读展示）。"""
            return C.QUALITY_LEVELS.get(self.quality) or C.QUALITY_LEVELS[C.DEFAULT_QUALITY]

        def as_params(self):
            """打包成 generation.generate 需要的参数字典。

            【厚度只在这里折算一次】v1.27.0 起面板**不再让用户设厚度** ——
            总厚由 ``C.auto_thickness_total(mode, 长边, 叠色层数)`` 按模式与
            幅面自动算，再交给唯一公式 ``C.thickness_split`` 拆分。
            生成 / 预览 / 质量报告读的都是同一份 as_params，口径不会分叉。

            【全色平板的色块数】用户口径是不手改，由质量等级决定
            （``C.MOSAIC_QUALITY``）。这里就地写进 color_count / mosaic_min_mm，
            下游不必再判断"这次是不是该听档位的"。
            """
            auto_total = C.auto_thickness_total(
                self.mode, self.size_mm, self.stack_bands)
            # v1.44.0（明信片）：正面厚度与反面凹刻深度是两个独立参数，
            # 走同一条 thickness_split。注意 total_mm 在这里是**整件**厚度，
            # 但 max_thickness_mm 必须回传**正面**厚度 —— generate() 会拿它
            # 再拆一次，传整件会把反面板重复算进起伏层。
            if self.mode == "postcard":
                split = C.thickness_split(
                    self.mode, self.post_front_mm, self.stack_bands,
                    self.post_engrave_mm)
                _front_total = split["base_mm"] + split["relief_mm"]
            else:
                split = C.thickness_split(self.mode, auto_total,
                                          self.stack_bands)
                _front_total = split["total_mm"]
            mq = C.MOSAIC_QUALITY.get(self.quality) \
                or C.MOSAIC_QUALITY[C.DEFAULT_QUALITY]
            return {
                "image": self.image,
                "mode": self.mode,
                "resolution": (
                    # v1.0.14：面板改成 1~10 档。旧工程 / 旧脚本只设过
                    # resolution（没碰 detail_level）时，按原格数走，不被
                    # 默认档位悄悄降采样；一旦动了档位就按档位表算。
                    int(self.resolution)
                    if (int(self.detail_level) == C.DEFAULT_DETAIL_LEVEL
                        and int(self.resolution) != C.DEFAULT_RES)
                    else C.detail_level_res(self.detail_level)),
                "detail_level": int(self.detail_level),
                "size_mm": self.size_mm,
                "rim_layers": int(self.rim_layers),
                "base_mm": split["base_mm"],
                "relief_mm": split["relief_mm"],
                "stack_bands": split["stack_bands"] or self.stack_bands,
                "band_mm": split["band_mm"] or self.band_mm,
                # ---- 明信片浮雕（v1.44.0）----
                "back_image": self.back_image,
                "back_mirror": bool(self.back_mirror),
                "back_depth_mm": float(self.post_engrave_mm),
                "post_front_mm": float(self.post_front_mm),
                "post_engrave_mm": float(self.post_engrave_mm),
                "max_thickness_mm": _front_total,
                "auto_thickness_mm": split["total_mm"],
                "color_count": (int(mq["color_count"]) if self.mode == "mosaic"
                                else self.color_count),
                "mosaic_min_mm": (float(mq["min_mm"]) if self.mode == "mosaic"
                                  else self.mosaic_min_mm),
                "quality": self.quality,
                "mix_group": self.mix_group,
                "color_basis": self.color_basis,
                # v1.0.14（用户 2026-11-20）：混色模型删除，固定「叠层混色」。
                # （RNA 保留，兼容旧工程与探针；生成侧仍支持 surface 分支。）
                "mix_model": "translucent",
                # 【v1.30.0 / R37】上色方式。「整块上色」时碎色块合并阈值
                # 由质量档的 min_feature_mm 决定（极致 0.6 / 均衡 1.2 /
                # 像素 2.4mm），这里一并导出，消费方不必再去查表。
                "color_match": self.color_match,
                # v1.32.0（R39）：浮雕竖墙用局部颜色（默认开）
                "relief_side_own": self.relief_side_own,
                "min_feature_mm": float(
                    C.QUALITY_LEVELS.get(self.quality,
                                         C.QUALITY_LEVELS[C.DEFAULT_QUALITY])
                    .get("min_feature_mm", C.MIN_PRINT_FEATURE_MM)),
                "nozzle_mm": self.nozzle_mm,
                "dither_pattern": self.dither_pattern,
                "sampling": self.sampling,
                # 平整色块板的「色块数」就是量化簇数 —— 两块参数是一回事。
                # 在这里就地统一，是为了让**所有**消费方（生成 / 分析 / 预览）
                # 拿到同一个值：早先若只让 generation.generate 覆写，分析按钮
                # 会按 quantize_colors（默认 16）去聚类，分析结果与实际生成的
                # 色块数对不上。
                "quantize_colors": (int(mq["color_count"]) if self.mode == "mosaic"
                                    else self.quantize_colors),
                "despeckle": self.despeckle,
                "allowed_colors": self.allowed_colors,
                # v1.0.14（用户 2026-11-20）：全色平板 / 明信片浮雕的「底板色」
                # 功能删除，固定白色（RNA 保留，旧工程不炸）。
                # 叠色链路仍用 base_key（柱底共用色），两者语义不同。
                "base_key": ("W" if self.mode in ("mosaic", "postcard")
                             else self.base_key),
                "band_ratio": self.band_ratio,
                # 【v1.28.0 / R35】这几项已从面板撤掉，值固定。旧工程里
                # 存的值不再生效（与厚度同一处理：RNA 保留、计算不用），
                # 否则用户会看到一个再也改不回来的旧状态。
                "height_mode": C.DEFAULT_HEIGHT_MODE,
                "anchor_mode": C.DEFAULT_ANCHOR_MODE,
                "brightness_weight": 1.0,
                "invert": False,
                "gamma": 1.0,
                "use_bambu_palette": self.use_bambu_palette,
                "auto_palette": self.auto_palette,
                "lanyard": self.lanyard,
                "lanyard_inner_r_mm": self.lanyard_inner_r_mm,
                "lanyard_outer_r_mm": self.lanyard_outer_r_mm,
                "lanyard_thickness_mm": self.lanyard_thickness_mm,
            }

    CSTACK_Properties.__name__ = "CSTACK_Properties"
    CSTACK_Properties.__qualname__ = "CSTACK_Properties"
    return CSTACK_Properties


# 模块导入即构造（默认语言），保证测试/探针在 register 之前也能取到类。
CSTACK_Properties = _make_props_class()
classes = (CSTACK_Properties,)


def register():
    """注册属性组；类在模块导入时按当前语言构造，这里直接注册。"""
    global CSTACK_Properties, classes
    # 幂等保护：若之前已注册则先退出
    for cls in classes:
        try:
            bpy.utils.unregister_class(cls)
        except Exception:  # noqa: BLE001
            pass
    if hasattr(bpy.types.Scene, "cstack"):
        try:
            del bpy.types.Scene.cstack
        except Exception:  # noqa: BLE001
            pass
    CSTACK_Properties = _make_props_class() if CSTACK_Properties is None else CSTACK_Properties
    classes = (CSTACK_Properties,)
    bpy.utils.register_class(CSTACK_Properties)
    bpy.types.Scene.cstack = PointerProperty(
            translation_context=_TC,
        type=CSTACK_Properties,
        name=I.t("叠色浮雕工坊"),
    )


def unregister():
    if hasattr(bpy.types.Scene, "cstack"):
        del bpy.types.Scene.cstack
    for cls in classes:
        try:
            bpy.utils.unregister_class(cls)
        except Exception:  # noqa: BLE001
            pass


def retranslate():
    """语言切换后重建属性组类，保留用户已调参数。

    RNA 的 name/description 是只读的，只能在类（重新）定义时写入 —— 因此切语言
    必须整类重建；重建前把用户已设值存下来、重建后写回，避免清空调参。
    """
    global CSTACK_Properties, classes
    sc = getattr(bpy.context, "scene", None)
    saved = {}
    old = getattr(sc, "cstack", None) if sc is not None else None
    if old is not None:
        for attr in _PRESERVE_ATTRS:
            try:
                saved[attr] = getattr(old, attr)
            except Exception:  # noqa: BLE001
                pass

    # 退出旧类 + 旧指针
    try:
        bpy.utils.unregister_class(CSTACK_Properties)
    except Exception:  # noqa: BLE001
        pass
    if hasattr(bpy.types.Scene, "cstack"):
        try:
            del bpy.types.Scene.cstack
        except Exception:  # noqa: BLE001
            pass

    # 重建（此时 I.LANG 已是目标语言）
    CSTACK_Properties = _make_props_class()
    classes = (CSTACK_Properties,)
    bpy.utils.register_class(CSTACK_Properties)
    bpy.types.Scene.cstack = PointerProperty(
            translation_context=_TC,
        type=CSTACK_Properties,
        name=I.t("叠色浮雕工坊"),
    )

    # 写回用户值
    new = getattr(bpy.context.scene, "cstack", None)
    if new is not None:
        for attr, val in saved.items():
            try:
                setattr(new, attr, val)
            except Exception:  # noqa: BLE001
                pass
