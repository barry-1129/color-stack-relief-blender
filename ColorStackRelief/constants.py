# -*- coding: utf-8 -*-
"""叠色浮雕工坊 —— 全局常量与基础色定义。

本模块不依赖 bpy，可被离线测试脚本单独导入。
"""

import math
import os

# ---------------------------------------------------------------------------
# 版本与标识
# ---------------------------------------------------------------------------
ADDON_ID = "color_stack_relief"
ADDON_NAME = "叠色浮雕工坊"
ADDON_NAME_EN = "ColorStack Relief"
ADDON_VERSION = (1, 0, 17)
ADDON_VERSION_STR = "1.0.17"

# 【v1.16.1】BLT 免疫上下文。用户 Blender 界面语言 = 简体中文时，Blender 自带
# zh_HANS 词典会把我们英文模式输出的**常见英文词**二次翻译（Language→语言、
# Resolution→分辨率、Balanced→均衡、White→白色……实测 18 条碰撞）。对策：
# 全部 RNA 属性声明 translation_context=本值，面板显式文本加 text_ctxt=本值 ——
# BLT 查表用 (上下文, 原文) 键，我们从不往该上下文里注册任何条目 → 查不到 →
# 界面显示的就是我们产出的原文（探针 t_probe_blt_v161 验证）。
I18N_CONTEXT = "cstack_ui"

ADDON_DESC = "导入图片 → 自动分析颜色 → 按叠色原理生成可打印 3D 模型 → 导出 3MF / OBJ"
ADDON_AUTHOR = "barry"
ADDON_BLENDER_MIN = (4, 2, 0)

# 生成文件（3MF / OBJ / MTL）的默认落点：桌面「叠色浮雕工坊文件夹」。
# 【v1.7.7 起是**唯一**默认落点】此前 operators._default_export_dir 还有一条
# "已打开 .blend 就落到 .blend 旁边"的兜底 —— 于是用户存过工程之后，导出文件
# 悄悄换了地方，桌面那个文件夹反而是空的。现在与 .blend 无关；面板上的
# 「导出目录」仍可手改（那是用户显式选择）。
# 路径在这里算一次，props 与 operators 共用，避免两份字面量各自漂移。
DEFAULT_EXPORT_DIR = os.path.join(
    os.path.expanduser("~"), "Desktop", "叠色浮雕工坊文件夹")

# ---------------------------------------------------------------------------
# 基础色键表（sRGB 0..1，用于写入材质与 3MF 颜色）
# ---------------------------------------------------------------------------
# 【v1.23.0 扩展】此前只有 C/M/Y/W 四键（CMYW）。用户口径下的四档混色需要
# 更多色键 —— RYBW 要红/黄/蓝/白，CMYWGK 要绿/黑，8色MAX 要 8 个。所以这里
# 补齐 R/G/B/K。**每个键的色值不是全局唯一的**：同一个 "B" 在 RYBW 里是纯蓝、
# 在 8色MAX 里是克莱因蓝 —— 具体组的色值由 ``COLOR_GROUPS[g]["srgb"]`` 通过
# palette 覆盖（见 color_engine.resolve_palette 的优先级），BASE_COLORS 只是
# 缺省兜底。
#
# order 决定色序（从底到顶）中该颜色的默认位置
BASE_COLORS = {
    "W": {"cn": "白色",   "en": "White",       "srgb": (1.0, 1.0, 1.0)},
    "C": {"cn": "青色",   "en": "Cyan",        "srgb": (0.0, 1.0, 1.0)},
    "M": {"cn": "品红",   "en": "Magenta",     "srgb": (1.0, 0.0, 1.0)},
    "Y": {"cn": "黄色",   "en": "Yellow",      "srgb": (1.0, 1.0, 0.0)},
    "R": {"cn": "红色",   "en": "Red",         "srgb": (1.0, 0.0, 0.0)},
    "G": {"cn": "绿色",   "en": "Green",       "srgb": (0.0, 1.0, 0.0)},
    "B": {"cn": "蓝色",   "en": "Blue",        "srgb": (0.0, 0.0, 1.0)},
    "K": {"cn": "黑色",   "en": "Black",       "srgb": (0.0, 0.0, 0.0)},
}

# 默认色序：从最底层到最顶层
DEFAULT_STACK_ORDER = ["W", "C", "M", "Y"]

# ---------------------------------------------------------------------------
# 四档混色色组（v1.23.0）
# ---------------------------------------------------------------------------
# 用户口径（2026-10-18 确认）：
#   CMYK  = 青 / 品红 / 黄 / 白  → **4 色**（不是"青品红黄 3 色 + 补白"）
#   RYBW  = 红 / 黄 / 蓝 / 白    → 4 色
#   CMYWGK= 青 / 品红 / 黄 / 白 / 绿 / 黑 → 6 色
#   8色MAX= C/M/Y/W/R/G/B/K      → 8 色（蓝用克莱因蓝 #002FA7）
#
# 【色值来源】除克莱因蓝为实测指定色号外，其余按标称纯色填。⚠ 这不是"耗材实测
# 色"——同一厂家不同批次的黄色能差 8 ΔE。真实标定的杠杆在
# ``BAMBU_CMYK_PRESET``（拓竹 CMYK 套装实测色），四档里只有 CMYK 组有对应的
# 套装色可选（``use_bambu_palette``）；其余三组目前是标称值。
#
# 【色组只对"表面混色"有意义】表面混色是把目标色投影到该组色板的凸包、再靠
# 顶面抖动混出来，任意色组都能用（不透明耗材）。「透光叠色」的透射率模型只有
# CMYK/CMYW 一套 Palette_256 实拍标定，换组等于换物理、没有数据支撑 —— 所以
# 透光模型下强制回落到 CMYK 组（面板同时置灰色组下拉）。
COLOR_GROUPS = {
    "cmyk": {
        "cn": "CMYK 四色",
        "en": "CMYK (4)",
        "desc": "青 / 品红 / 黄 / 白（减色混合）",
        "desc_en": "Cyan / Magenta / Yellow / White (subtractive mixing)",
        "keys": ("C", "M", "Y", "W"),
        "srgb": {
            "C": (0.0, 1.0, 1.0),
            "M": (1.0, 0.0, 1.0),
            "Y": (1.0, 1.0, 0.0),
            "W": (1.0, 1.0, 1.0),
        },
    },
    "rybw": {
        "cn": "RYBW 四色",
        "en": "RYBW (4)",
        "desc": "红 / 黄 / 蓝 / 白（视觉叠色）",
        "desc_en": "Red / Yellow / Blue / White (visual mixing)",
        "keys": ("R", "Y", "B", "W"),
        "srgb": {
            "R": (1.0, 0.0, 0.0),
            "Y": (1.0, 1.0, 0.0),
            # v1.0.12：这里仍是纯蓝 (0,0,1)，但**理论档不再直接拿它当透射率**
            # （那会让 黄+蓝 = 黑，见 color_engine._theory_st 的颜料分支）。
            "B": (0.0, 0.0, 1.0),
            "W": (1.0, 1.0, 1.0),
        },
    },
    "cmywgk": {
        "cn": "CMYWGK 六色",
        "en": "CMYWGK (6)",
        "desc": "青 / 品红 / 黄 / 白 / 绿 / 黑",
        "desc_en": "Cyan / Magenta / Yellow / White / Green / Black",
        "keys": ("C", "M", "Y", "W", "G", "K"),
        "srgb": {
            "C": (0.0, 1.0, 1.0),
            "M": (1.0, 0.0, 1.0),
            "Y": (1.0, 1.0, 0.0),
            "W": (1.0, 1.0, 1.0),
            "G": (0.0, 1.0, 0.0),
            "K": (0.0, 0.0, 0.0),
        },
    },
    "max8": {
        "cn": "8 色 MAX",
        "en": "8-color MAX",
        "desc": "青 / 品红 / 黄 / 白 / 红 / 绿 / 蓝（克莱因蓝 #002FA7）/ 黑",
        "desc_en": "C / M / Y / W / R / G / B (Klein blue #002FA7) / K",
        "keys": ("C", "M", "Y", "W", "R", "G", "B", "K"),
        "srgb": {
            "C": (0.0, 1.0, 1.0),
            "M": (1.0, 0.0, 1.0),
            "Y": (1.0, 1.0, 0.0),
            "W": (1.0, 1.0, 1.0),
            "R": (1.0, 0.0, 0.0),
            "G": (0.0, 1.0, 0.0),
            # 用户指定：8色MAX 的蓝 = 克莱因蓝 #002FA7
            "B": (0x00 / 255.0, 0x2F / 255.0, 0xA7 / 255.0),
            "K": (0.0, 0.0, 0.0),
        },
    },
}
COLOR_GROUP_KEYS = ("cmyk", "rybw", "cmywgk", "max8")
DEFAULT_COLOR_GROUP = "cmyk"
# 透光叠色只认这一组（Palette_256 实拍标定物）
TRANSLUCENT_COLOR_GROUP = "cmyk"

# ---------------------------------------------------------------------------
# 配色计算依据（v1.0.9 / 用户 2026-11-15）
# ---------------------------------------------------------------------------
# 用户原话：「在混色色组中添加一个功能，该功能有两个选项：按色板计算和按理论
# 计算；当选择按色板计算时，依据前期的颜色标定板进行计算；当选择按理论计算，
# 则完全按照选择混色色组给定的颜色进行标准计算。」
#
# 两者的分界只有一条：**要不要用实拍反解出来的那几张参数表**。
#   * calibrated：_CMYK_FIT_* / _RYBW_* / _CMYK_MEASURED_SRGB / _STACK_REFLECT_*
#     —— 都是"这批料 + 这台相机 + 这次白平衡"的实测值，跟实物最像；
#     换料/换机/换批次就会系统性偏，只能重打标定板。
#   * theory：完全不查实测表，把色组里每个颜色**本身**当单层透光率，
#     按理想减色叠加算（白 = 纸，b = 1）。换色组/自配色值时结果可预测。
COLOR_BASES = {
    "calibrated": {
        "cn": "按色板计算",
        "desc": "用打印出来的标定板实拍数据算：CMYK / RYBW 各有一套实测反解"
                "参数，跟手上这批耗材最接近。换料或换批次后实测会偏，"
                "那就重打一次标定板。",
    },
    "theory": {
        "cn": "按理论计算",
        "desc": "完全按混色色组给定的颜色算：每卷料的颜色直接当它的透光率，"
                "白当纸，层与层相乘。不套用任何实拍修正 —— 换色组、换颜色"
                "都能算，代价是没有「这批料实际偏多少」的校正。",
    },
}
COLOR_BASIS_KEYS = ("calibrated", "theory")
DEFAULT_COLOR_BASIS = "calibrated"


class StackPalette(dict):
    """色板 ``{键: sRGB}`` + **计算依据**（``basis``）。

    为什么做成 dict 子类而不是多传一个参数：色板本来就会一路传到
    ``stack_present`` / ``mirror_anchors`` / ``StackMatcher``，把依据挂在
    它身上，这些调用点一个都不用改签名；中途重建色板的几处用
    ``StackPalette(palette)`` 也能把依据带过去（否则会静默退回实测表）。
    """

    def __init__(self, data=(), basis=None, **kw):
        super().__init__(data, **kw)
        self.basis = basis or DEFAULT_COLOR_BASIS


def color_group_palette(group=None, basis=None):
    """返回该色组的 ``{键: sRGB}``；未知组回落到默认组。"""
    g = COLOR_GROUPS.get(group or DEFAULT_COLOR_GROUP) \
        or COLOR_GROUPS[DEFAULT_COLOR_GROUP]
    return StackPalette(g["srgb"], basis=basis)


def color_group_keys(group=None):
    """返回该色组的色键元组（已按 ``canonical_key_order`` 的稳定顺序）。"""
    g = COLOR_GROUPS.get(group or DEFAULT_COLOR_GROUP) \
        or COLOR_GROUPS[DEFAULT_COLOR_GROUP]
    return tuple(g["keys"])

# 拓竹 CMYK 耗材套装的实际观感色（青色偏深、品红偏暖），供"拓竹套装"预设使用
BAMBU_CMYK_PRESET = {
    "W": (1.000, 1.000, 1.000),
    "C": (0.000, 0.741, 0.827),
    "M": (0.925, 0.000, 0.549),
    "Y": (1.000, 0.898, 0.000),
}

# 拓竹 3MF 的「工程文件」识别开关：Application 元数据必须以 "BambuStudio-"
# 开头（bbs_3mf.cpp _handle_end_metadata 里 starts_with 判定），否则导出的
# project_settings.config 会被 Bambu Studio 整体忽略（v1.6.1 实测）。
BAMBU_APP_TAG = "BambuStudio-02.07.01.57"

# ---------------------------------------------------------------------------
# 拓竹工程模板（v1.6.2 架构）
# ---------------------------------------------------------------------------
# 血泪教训：`project_settings.config` **不是稀疏覆盖**，而是一份完整的
# 「机型 + 工艺 + 耗材」快照。只写几十个工艺键、身份键随便编（v1.6.0/1.6.1
# 写的 "Generic Bambu Printer" / "Generic PLA" / "0.20mm Standard @BBL X1C"）
# 会被拓竹判定为：
#     return_code -17
#     "The selected printer is not compatible with the process preset in the 3mf."
# 于是**整份配置被丢弃** —— 层高退回 0.2、耗材变成 Default Filament。
#
# 因此 v1.6.2 改为：以一份**真实拓竹工程**（Bambu Studio 02.07.01.57 在
# Bambu Lab A1 0.4 nozzle + 4 色耗材下保存的 570 键快照）为底，再把用户调好
# 的工艺值覆盖上去。这样身份、机型能力、耗材数组长度全部天然自洽。
#
# 数组长度规则（实测，改耗材数必踩）：flush_volumes_matrix = N²、
# flush_volumes_vector = 2N；其余向量键长度随机型/耗材数变化，合并时
# 一律以模板长度为基准对齐（见 exporters.build_project_settings）。
BAMBU_TEMPLATE_FILE = "resources/bambu_a1_template.json"
BAMBU_PRINTER_PROFILE = "Bambu Lab A1 0.4 nozzle"
BAMBU_FILAMENT_PROFILE = "Bambu PLA Basic @BBL A1"

STACK_PRESETS = {
    "经典CMYW": ["W", "C", "M", "Y"],
    "暗底亮顶": ["C", "M", "Y", "W"],
    "暖调优先": ["W", "M", "Y", "C"],
    "冷调优先": ["W", "Y", "C", "M"],
    "白底三色": ["W", "C", "M", "Y"],
    "渐变平滑": ["W", "Y", "M", "C"],
}

# ---------------------------------------------------------------------------
# 拓竹切片工艺参数（导出 3MF 时写入 Metadata/project_settings.config）
# ---------------------------------------------------------------------------
# 键名与取值形态逐一对照真实拓竹 3MF（151forbambu.3mf）的 project_settings.config
# 实测（571 键）。v1.6.2 起这些值作为**覆盖层**，叠加在
# resources/bambu_a1_template.json（真实 A1 四色工程快照）之上 —— 未列出的
# 机器/耗材键一律沿用模板，保证身份与数组长度自洽。
BAMBU_PROCESS_PRESET = {
    # ---- 质量页：层高 / 线宽 / 接缝 ----
    "layer_height": "0.1",
    "initial_layer_print_height": "0.1",
    "line_width": "0.42",
    "initial_layer_line_width": "0.42",
    "outer_wall_line_width": "0.42",
    "inner_wall_line_width": "0.45",
    "top_surface_line_width": "0.3",
    "sparse_infill_line_width": "0.3",
    "internal_solid_infill_line_width": "0.3",
    "support_line_width": "0.42",
    "seam_position": "aligned",                       # 接缝位置：对齐
    "seam_placement_away_from_overhangs": "0",        # 远离悬垂点（实验）关

    # ---- 强度页：墙 / 壳体 / 填充 ----
    "wall_loops": "1",                                # 墙层数 1
    "alternate_extra_wall": "0",                      # 交替额外墙层关
    "top_surface_pattern": "alignedrectilinear",      # 顶面图案：直线排列
    "top_surface_density": "100%",
    "top_shell_layers": "4",
    "top_shell_thickness": "1",
    "top_color_penetration_layers": "5",              # 顶部涂色渗透层数
    "bottom_surface_pattern": "alignedrectilinear",
    "bottom_surface_density": "100%",
    "bottom_shell_layers": "4",
    "bottom_shell_thickness": "0",
    "bottom_color_penetration_layers": "3",
    "internal_solid_infill_pattern": "alignedrectilinear",
    "sparse_infill_density": "15%",
    "sparse_infill_anchor_max": "20",                 # 稀疏填充铆线最大长度
    "minimum_sparse_infill_area": "15",               # 稀疏填充最小阈值 mm²
    "infill_wall_overlap": "10%",                     # 填充/墙重叠
    "bridge_angle": "0",                              # 桥接方向
    "infill_combination": "0",                        # 合并填充关
    "detect_narrow_internal_solid_infill": "0",       # 识别狭窄内部实心填充关
    "ensure_vertical_shell_thickness": "enabled",     # 确保垂直外壳厚度开

    # ---- 速度页（数组形态与拓竹一致：[常规值, 降速上限]）----
    "initial_layer_speed": ["50", "50"],
    "initial_layer_infill_speed": ["50", "105"],
    "outer_wall_speed": ["100", "350"],
    "inner_wall_speed": ["300", "400"],
    "small_perimeter_speed": ["50%", "50%"],
    "small_perimeter_threshold": ["0", "0"],
    "sparse_infill_speed": ["50", "370"],
    "internal_solid_infill_speed": ["50", "350"],
    "top_surface_speed": ["50", "300"],

    # ---- 其他页：冲洗塔（换料塔）----
    "enable_prime_tower": "1",
    "prime_tower_width": "35",
    "prime_tower_max_speed": "50",
    "prime_tower_brim_width": "3",
    "prime_tower_infill_gap": "150%",
    "prime_tower_rib_wall": "0",                      # 内支撑肋关
    "prime_tower_fillet_wall": "0",                   # 斜肋外墙关
    # 塔位必须给出**单值**，且 y 要给塔身留出纵深：实测本工程塔深 65.4mm
    # （wipe_tower_y 是塔的**前缘**），沿用模板（6 盘工程）的 6 元素数组
    # 会让塔摆到板外 → CLI -104
    # "Found G-code outside of the printable area."。
    # 扫描结论（tests/t_sweep_tower.py）：206 失败，180/150/120 均通过；
    # 取 150 使塔落在 150~216，前后各留约 150/40mm 空区，避让居中摆放的模型。
    "wipe_tower_x": "15",
    "wipe_tower_y": "150",

    # ---- Brim 与支撑 ----
    "brim_type": "auto_brim",
    "brim_width": "5",
    "brim_object_gap": "0.1",
    "enable_support": "0",                            # 浮雕/卡类无需支撑

    # ---- 换料冲刷选项 ----
    "flush_into_infill": "0",
    "flush_into_objects": "0",
    "flush_into_support": "1",

    # ---- 预设身份键：不在这里写！ ----
    # printer_settings_id / print_settings_id / filament_settings_id /
    # nozzle_diameter / printable_height / version 由
    # resources/bambu_a1_template.json（真实拓竹工程快照）提供。
    # v1.6.0/1.6.1 曾在这里手写 "Generic Bambu Printer" 之类的假身份键，
    # 结果被拓竹以 -17 拒绝整份配置（参数全部不生效）。
}

# ---------------------------------------------------------------------------
# 拓竹模式（v1.38.0）—— 面板上暴露的少数几个工艺开关
# ---------------------------------------------------------------------------
# 【为什么只暴露这几项】project_settings.config 有 570 个键。整份搬进 Blender
# 面板既没人看得懂（用户自述不懂术语），也会和他在 Bambu Studio 里调好的预设
# 打架。这里只给「换图 / 换料 / 换台面确实要改」的 8 项，其余沿用 A1 实机模板。
#
# 【层高只有两档，不是漏了 0.08】色片厚度 = 0.1mm，层高必须是 0.1 的整数分之一
# 才能让每片色膜恰好占整数层。0.08 会让 4 片色膜跨 5 层，中间的色膜被劈成两半
# —— 叠色混色直接失真。0.05 = 每片占 2 层，更细但打印时间约翻倍。
# v1.39.0：面板只留「墙层数」一个旋钮（用户明确要求砍掉其余项），
# 以下 8 项改为**固定写死**。取值与 v1.38.0 的出厂默认逐项相同，所以老用户
# 升级后不点任何东西，导出件的切片结果不变。
#
# 【层高为什么只有 0.1】色片厚 0.1mm，层高必须是 0.1 的整数分之一，否则色膜
# 会被相邻层劈开、叠色混色失真。0.1 = 一片色膜一层，是唯一不需要额外解释的档。
BAMBU_FIXED = {
    "layer_height": "0.1",
    "initial_layer_print_height": "0.1",
    "sparse_infill_density": "15%",
    "brim_type": "auto_brim",
    "brim_width": "5",
    "brim_object_gap": "0.1",
    "enable_support": "0",
    "enable_prime_tower": "1",
}

# 墙层数：0 = **不生成墙**（用户 2026-11-06 要求放开到这个档：外圈只剩填充，
# 侧面会软、能看到填充纹路，是他自己要做"全填充/无墙"的活儿用的）；
# 1 = 只有一圈外墙（薄板上与填充同色同高，**肉眼看不出来**）；
# 2 起才有内墙。默认 2 与拓竹 A1 自带预设一致 —— v1.38.0 默认 1 时用户
# 反馈"没加墙"，实测切片 feature_type_times：1 圈内墙 0s、4 圈内墙 276s。
BAMBU_WALL_LOOPS_DEFAULT = 2
BAMBU_WALL_LOOPS_MIN = 0
BAMBU_WALL_LOOPS_MAX = 4

# ---------------------------------------------------------------------------
# 数值约束
# ---------------------------------------------------------------------------
MIN_RES = 8          # 单边最小采样像素
# v1.7.4：由 400 提到 1000；v1.0.16（用户 2026-11-21）：再提到 1440。
# 【为什么要放开】柱宽 = 最长边 ÷ 采样精度。400 档在 100mm 片子上只有 0.25mm
# 柱宽，想在细密处（眼睛、文字、纹理）保留更多色点就顶住了上限。
# 【代价必须说清】柱数按**平方**增长：400 → 16 万柱，1440 → 207 万柱。
# 卡片模式每柱是一根独立实体柱，整链路面数 ≈ 柱数 × 14（实测 12 万柱 ≈ 163 万面），
# 207 万柱 ≈ 2900 万面 —— 生成时间与内存都会显著上升。面板在柱数过大时给黄字提示。
MAX_RES = 1440
DEFAULT_RES = 96

# ---------------------------------------------------------------------------
# 分辨率档位（v1.0.14，用户 2026-11-20）
# ---------------------------------------------------------------------------
# 用户口径：「需要参考 3D 轨迹的那套分辨率模式，而不是现在以 0.2 喷嘴、
# 0.4 喷嘴等调节具体数值；而是以分辨率 1~10 的档位进行选择」。
# 所以面板只暴露 1~10 档，每档对应一个**固定的长边格数**（不外露像素数）。
# 档位间隔约 1.5 倍：4 档 = 96 = 原默认精细度，9 档 ≈ 原来手输的 455/512。
DETAIL_LEVEL_RES = {
    1: 32,
    2: 48,
    3: 64,
    4: 96,
    5: 128,
    6: 192,
    7: 256,
    8: 384,
    9: 512,
    10: 720,
    # v1.0.15（用户 2026-11-20「另外分辨率是否能更高」）：加第 11 档 1000。
    # v1.0.16（用户 2026-11-21「分辨率最高调整为 12」）：加第 12 档 1440。
    # 11/12 两档都**按档位对应的理论格数直接渲染**，不再因为内存估算超预算
    # 自动降档降格（用户明确要求：内存不够也不降）。档位表是唯一出处，
    # 多出来的 440 格 = 长边像素密度再 +44%。
    11: 1000,
    12: 1440,
}
DEFAULT_DETAIL_LEVEL = 4        # = 96，与旧默认精细度逐位一致
MAX_DETAIL_LEVEL = max(DETAIL_LEVEL_RES)


def detail_level_res(level):
    """分辨率档位（1~12）→ 长边格数。越界自动钳回。"""
    try:
        n = int(level)
    except (TypeError, ValueError):
        n = DEFAULT_DETAIL_LEVEL
    n = max(1, min(MAX_DETAIL_LEVEL, n))
    return int(DETAIL_LEVEL_RES[n])


def level_for_res(res):
    """长边格数 → 最接近的分辨率档位（1~12）。

    用途：把格数说回用户认得的档位，否则"从 720 降到 541"会被当成两个陌生数字。
    注意：v1.0.16 起生成端**不再因内存自动降档**，本函数只用于读数换算与提示。
    """
    try:
        n = int(res)
    except (TypeError, ValueError):
        return int(DEFAULT_DETAIL_LEVEL)
    best_lv, best_d = int(DEFAULT_DETAIL_LEVEL), None
    for lv in sorted(DETAIL_LEVEL_RES):
        d = abs(int(DETAIL_LEVEL_RES[lv]) - n)
        if best_d is None or d < best_d:
            best_lv, best_d = int(lv), d
    return int(best_lv)
# 柱数过多时面板黄字提示（v1.7.4 放开上限后新增的护栏）。
# 25 万柱已经远超旧 400 档的上限（16 万柱），属于"要等一会儿、内存也吃紧"的量级。
BIG_PILLAR_WARN = 250000

# v1.45.4（R61）：内存闸门从"硬报错"改成"按本机内存算预算 + 自动降档继续"。
#
# 【根因】旧闸门（v1.37.1）两处不对：
#   ① 预算写死 6GB，不看机器 —— 32GB 机器也被同一道闸拦死，且超限只能报错，
#      用户看到"无法生成"就卡住了，没有任何出路；
#   ② 三角数系数把双面卡估成 **30 三角/格**，实测只有 6.6~7.5/格。于是一张
#      2:1 照片、精细度 1000 被报成"1395 万面 / 13.0GB"，而实际约 310 万面、
#      1.7GB —— 白拦。
#
# 【实测（无头 Blender，100mm，CMYK，逐格统计 loop_triangles）】
#   card 120 → 7.45/格、card 400 → 6.61、mosaic 400 → 8.08、
#   relief 400 → 16.85、postcard 300 → 21.50
# 进程峰值（GetProcessMemoryInfo.PeakWorkingSetSize）：
#   card 107k 三角 237MB、card 1.05M 三角 674MB（空载基线 171MB）
#   → 边际约 500~640 字节/三角（小网格含固定开销，占比偏高）。
#
# 【现在的口径】预算 = 本机物理内存 × 0.55（下限 2GB；检测失败回落 6GB）；
# 估算 = 0.30GB 基线 + 格数 × 每格三角 × 1.5 安全系数 × 700 字节；
# 超预算时**自动降到装得下的最大精细度继续生成**，并把实际用了多少写进
# 结果 res["mem"]，面板/生成报告都说清楚。只有连 MIN_RES 都装不下才报错。
CELL_TRIS = {                      # 每格三角数（实测值，已含 1.5 倍安全余量）
    "card": 11.0,                  # 实测 6.6~7.5
    "single": 13.0,                # 比 card 多 4 层纯白（无逐格色界），约 7~9
    "relief": 25.0,                # 实测 16.85
    "mosaic": 13.0,                # 实测 8.08
    "postcard": 32.0,              # 实测 21.5
}
PEAK_BYTES_PER_TRI = 700.0         # 实测 500~640，向上留余量
MEM_BASELINE_BYTES = 0.30 * (1024 ** 3)
MEM_BUDGET_RATIO = 0.55
MEM_BUDGET_MIN_BYTES = 2.0 * (1024 ** 3)
MEM_BUDGET_FALLBACK_BYTES = 6.0 * (1024 ** 3)   # 读不到物理内存时沿用旧值


_RAM_CACHE = [0]


def total_ram_bytes():
    """本机物理内存字节数；检测失败返回 0（调用方回落保守默认）。

    只用**物理总量**、不看当前可用量：可用量随时在变，拿它做判定会让同参数
    两次生成得到不同精细度（破坏确定性，也让用户无法复现成品）。
    结果缓存 —— 面板每次重绘都要读，物理内存不会在会话中途变化。
    """
    if _RAM_CACHE[0] > 0:
        return _RAM_CACHE[0]
    val = _detect_ram_bytes()
    if val > 0:
        _RAM_CACHE[0] = int(val)
    return int(val)


def _detect_ram_bytes():
    try:
        import sys
        if sys.platform.startswith("win"):
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            m = _MS()
            m.dwLength = ctypes.sizeof(_MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return int(m.ullTotalPhys)
            return 0
        if sys.platform == "darwin":
            import subprocess
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"],
                                          timeout=5)
            return int(out.strip())
        with open("/proc/meminfo", "r") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except Exception:  # noqa: BLE001  纯估算，读不到就回落
        return 0
    return 0


def mem_budget_bytes():
    """本次生成允许的进程峰值内存（字节）。"""
    total = total_ram_bytes()
    if total <= 0:
        return MEM_BUDGET_FALLBACK_BYTES
    return max(MEM_BUDGET_MIN_BYTES, float(total) * MEM_BUDGET_RATIO)


def est_peak_bytes(cells, mode):
    """按格数估算进程峰值内存（与 generate() 的闸门同一口径）。"""
    return MEM_BASELINE_BYTES + float(cells) * CELL_TRIS.get(
        mode, CELL_TRIS["mosaic"]) * PEAK_BYTES_PER_TRI


def fit_cells_for_budget(budget, mode):
    """给定预算能装下多少格（下限 1）。"""
    per = CELL_TRIS.get(mode, CELL_TRIS["mosaic"]) * PEAK_BYTES_PER_TRI
    return max(1.0, (float(budget) - MEM_BASELINE_BYTES) / max(1.0, per))


# 每根柱子在整链路面数上的经验系数（实测 12 万柱 ≈ 163 万面 → 13.6 面/柱）。
# 只用于给用户一个量级概念，**不参与任何建模计算**。
FACES_PER_PILLAR = 14

# ---------------------------------------------------------------------------
# 柱宽 ↔ 喷嘴线宽（v1.7.2：面板给出实际柱宽并支持"对齐喷嘴"）
# ---------------------------------------------------------------------------
# 【为什么要管这件事】一根像素柱的宽度 = 最长边尺寸 ÷ 采样精度。FDM 喷嘴把
# 熔丝挤成一条线，**线宽约等于喷嘴孔径**；当柱宽明显小于线宽时，切片器没法
# 在每根柱子上单独走线，只能把相邻几根糊成一片 —— 我们精心算出的色点根本没
# 被打出来，白提升网格量（全图 12 万柱 ≈ 163 万面、生成要一两分钟）。
#
# 反之柱宽远大于线宽则"颗粒变大"，但那是可打印的代价，不是错误。
# 所以面板口径：柱宽 < 线宽 → 黄色提醒；≈ 线宽 → 正好，打得出最多的色点。
NOZZLE_WIDTH_MM = 0.42       # 0.4 喷嘴的实测挤出线宽（= 下表 "0.4" 档，兼容旧名）
NOZZLE_NOZZLE_MM = 0.4       # 标称孔径（文案里提到机型时用）

# ---- 喷嘴档位表（v1.7.4）：面板「0.2 喷嘴」「0.4 喷嘴」两个键各取一档 ----
# 【数字来源】挤出线宽 ≈ 孔径 + 0.02mm 铺展，这是拓竹官方工艺配置的口径
# （0.4 喷嘴默认线宽 0.42mm、0.2 喷嘴默认线宽 0.22mm），与 A1 实机实测的
# 0.42mm 一致。换喷嘴 / 换品牌只改这一张表，算子与面板里都不许写死数字。
NOZZLE_LINE_WIDTH_MM = {
    "0.2": 0.22,
    "0.4": 0.42,
}
NOZZLE_OPTIONS = ("0.2", "0.4")      # 面板按钮顺序
DEFAULT_NOZZLE = "0.4"

# 低于这个宽度就认为"打不出独立色点"，面板转黄提示。按档位取用见 min_safe_pillar()。
# 数值约为对应线宽的 0.72 倍；"0.4" 档刻意与旧常量 MIN_SAFE_PILLAR_MM 同值（0.30），
# 免得 v1.7.2 的行为发生无声漂移。
NOZZLE_SAFE_PILLAR_MM = {
    "0.2": 0.16,
    "0.4": 0.30,
}
MIN_SAFE_PILLAR_MM = NOZZLE_SAFE_PILLAR_MM[DEFAULT_NOZZLE]
# 采样精度的"对齐喷嘴"建议上限：小于它就给到对齐值即可
SUGGESTED_TOLERANCE = 0.06   # mm，柱宽落在 [线宽-容差, 线宽] 内不再提示


def nozzle_width(nozzle=None):
    """取某档喷嘴的挤出线宽（mm）。``None`` = 默认档。"""
    key = DEFAULT_NOZZLE if nozzle is None else str(nozzle)
    return NOZZLE_LINE_WIDTH_MM.get(key, NOZZLE_LINE_WIDTH_MM[DEFAULT_NOZZLE])


def min_safe_pillar(nozzle=None):
    """取某档喷嘴的"安全柱宽"（低于它：切片器走不出独立挤出线）。"""
    key = DEFAULT_NOZZLE if nozzle is None else str(nozzle)
    return NOZZLE_SAFE_PILLAR_MM.get(key, NOZZLE_SAFE_PILLAR_MM[DEFAULT_NOZZLE])


def dither_block_at(pixel_mm, nozzle=None):
    """「色点」要补到一条挤出线宽所需的块边长（格数）—— 唯一实现处。

    【v1.34.0 / R41】抖动色点的物理下限只有一条：**至少占一条挤出线宽**。
    比线宽细的斑点会被切片器的小特征过滤并回主导色（实测 2026-09-30）；
    正好一条线宽的方块就是一段可打印的挤出路径，不会丢。

    R18 曾按 2× 线宽补（怕被过滤），但那让 100mm / 600 精细度的色点变成
    1.0mm = 长边只有 100 个点，精细度再高也不变细 —— 实拍就是"太糊"。
    现在按 1× 线宽补：600 精细度 → 3 格 = 0.50mm，238 精细度 → 1 格 0.42mm。

    预览（operators._dither_kw）、生成（generation.dither_block_for）与面板
    读数都调本函数，不许各自算一遍。
    """
    px = max(1e-6, float(pixel_mm))
    lw = nozzle_width(nozzle)
    # 用带容差的 px_from_mm，不要 ceil：格宽 0.4167 与线宽 0.42 是同一条挤出线，
    # ceil 会把它抬成 2 格（0.83mm），长边色点数直接砍半（R44 实测）。
    return max(1, min(px_from_mm(lw, px), 8))

MIN_SIZE_MM = 5.0
MAX_SIZE_MM = 400.0
DEFAULT_SIZE_MM = 100.0

MIN_BASE_MM = 0.2
MAX_BASE_MM = 10.0
DEFAULT_BASE_MM = 0.6

MIN_RELIEF_MM = 0.4
MAX_RELIEF_MM = 60.0
DEFAULT_RELIEF_MM = 3.0

# 色深叠色卡（card，v1.5）：双面镜像，每面 N 片 band_mm 厚的色片，透光叠色混色。
# 参考方案（MakerWorld 2185254 Palette_256）：每面 4 片 × 0.1mm、双面共 0.8mm。
#
# 【v1.7.1：上限 8 → 4（每面 4 层已饱和）】
# 色板只有 C/M/Y/W 四种色相，每面 4 层就能表达全部可达色阶 —— 包括 C²M²
# 这种"同色重复层加深"的组合（v1.7.0 修好 canonical_key_order 去重后才真正
# 打得出来）。
#
# ⚠ 根因订正（2026-09-19 复核）：此前把"5~8 层与 4 层逐元素等价"归因于
# _STACK_ANCHOR_CAP=36 封顶，**这是错的**。真正的原因是 mirror_anchors 里的
# `min(max_depth, len(ks))` 把层数钳在**颜色数 4** 上（外加
# stack_present_mirror 的 `st[:4]` 硬编码）。放开钳制后锚点确实会涨到
# 56 / 84 / 120 / 165 个 —— 但**画质并不改善**：新增配方全是"同色重复更多
# 层"= 更暗，落在已有锚点凸包的内部/下方，凸包没变大 → 抖动混色能力不变。
# 实测（sRGB 16³ 目标色网格、CIE76，脚本 tests/diag_bands_res.py）：
#   配方数 35 → 165，单锚点平均 ΔE 39.80 → 39.75、
#   最差 ΔE 113.18 → 113.18、两锚点混合极限 31.23 → 31.21（全部零增益）。
# 所以上限收敛到 4 是**有意的**：再加层只把模型从 0.8mm 加厚到 1.6mm，还会让
# mix_weights 的子集枚举从 C(35,4) 涨到 C(165,4)（≈350 倍）。
# 引擎（CardStacker/mirror_anchors）仍接受更大的 max_depth 以便实验；
# 此处收敛的是**用户可设范围**，避免面板上留一个拉不动的滑杆。
#
# 【v1.0.6：上面这段"零增益"只在**白不参与**时成立】
# 上面的实测全是"白色永远补在内侧"的前提下做的 —— 那时新增层只会同色重复
# 得更暗。白被允许排到外侧之后，第 5/6 层能补出整片浅色与中间调：
#   暖肤色最近锚点 ΔE 30.15 → **21.60**，浅粉 17.06 → **7.38**。
# 双面卡（card）因此固定 6 层/面（见 CARD_BANDS_PER_SIDE），MAX 同步放宽到 6
# 供浮雕等其它模式实验；**默认值仍是 4**，浮雕的起伏幅度不受影响。
#
# 【v1.0.10：用户拍板把浮雕 / 明信片统一到 6 层/面】
# 依据：4 层每面只有 80 个可打印结构、且白冲淡的档位太粗（浅色只能靠"少叠
# 彩膜"硬凑）；6 层有 224 个结构，浅色/肤色的中间调才够用。三者（card /
# relief / postcard）从此**同一套层数**，镜像链路只有一种口径。
# ⚠ 代价两条，都不是 bug：
#   ① 色膜从 2×4=8 片增到 2×6=12 片 → 正/反面各厚 0.20mm，
#      ``relief_min_front_mm`` 由 1.20 抬到 1.60mm（厚度滑杆的有效下限）；
#   ② 35 格 CMYK 实拍表只对「每面 4 层」有效，6 层一律走模型
#      （模型对 6 层的复算 ΔE 6.85，见 CHANGELOG v1.0.8）。
DEFAULT_STACK_BANDS = 6
DEFAULT_BAND_MM = 0.1
MIN_STACK_BANDS = 1
MAX_STACK_BANDS = 6
MIN_BAND_MM = 0.06
MAX_BAND_MM = 0.30

# 单层标称高度（与拓竹混色建议的 0.12/0.2 对齐）
DEFAULT_LAYER_H = 0.2
# 叠色微层高度（拓竹 0.4 喷嘴建议叠层层高 0.2，固定层 0.12）
DEFAULT_OVERLAY_H = 0.12

MAX_COLOR_LAYERS = 8    # 色序展开后的最大层数 = 每面层数上限 4 × 双面（当前未被引用）

# ---------------------------------------------------------------------------
# 平整色块板（mosaic，v1.22.0）
# ---------------------------------------------------------------------------
# 【它是什么】照片 → 自动聚类 N 色 → 每个像素归到最近的一簇 → 一块**顶面等高**
# 的板：底板（base_mm）+ 一层 (relief_mm) 厚的色块层，板顶按簇色分区上色。
# 打印需要的耗材槽数 = N（+ 底板色，若底板色不在 N 里）。
#
# 【为什么上限是 16】Bambu 一台 AMS 是 4 槽，四级串联 16 槽；超过 16 色在整机
# 上就要手工干预（且换色次数线性上升）。N 越大还原越准，但**收益迅速饱和**：
# 照片的 Lab 聚类损失在 N≈8 之后每加一色只降零点几 ΔE，而 N 就是这台机器上
# 的换色次数下限。默认 8 是"还原肉眼可辨"与"一台机器打得出"的折中。
MIN_COLOR_COUNT = 2
MAX_COLOR_COUNT = 16
DEFAULT_COLOR_COUNT = 8

# 最小色块尺寸（mm）。照片聚类出来的小色斑在实物上会：
#   ① 被切片器的小特征过滤并回主导色（色块消失）；② 每次进出都是一次换色。
# 本值把**连通色块面积**小于 (min_mm)² 的区域并给相邻最大色块。0 = 关（不合并）。
# 为什么用面积而不是边长：切片的丢色判据是"这条挤出线有没有人跟着走"，等价的
# 物理量是面积（≈ 线宽²）。默认 1.0mm → 约 2.5 条 0.4 挤出线的宽度。
DEFAULT_MOSAIC_MIN_MM = 1.0
MIN_MOSAIC_MIN_MM = 0.0
MAX_MOSAIC_MIN_MM = 10.0

# ---------------------------------------------------------------------------
# 高度映射修正系数（推导过程见 color_engine.map_height）
# ---------------------------------------------------------------------------
# 纯色位置在数学上正好落在色带边界上，浮点比较会把它判给上一条带。
# 向内缩一点点（单位=色带数）消除归属歧义，视觉上完全不可见。
BAND_INSET = 0.01
# 「亮度定厚度」的下拉系数：色相与厚度共用同一根 Z 轴，亮度项过大会把颜色
# 拖出自己的色带（实测 0.5 就会让青色落进品红带）。0.25 是"亮度可见"与
# "色相不乱"的平衡点；用户可用 brightness_weight 滑杆在 0~1 之间调整强弱。
BRIGHT_DROP = 0.25

# ---------------------------------------------------------------------------
# 分区配色（region 模式）：色相逐像素独立选色 + 有序抖动
# ---------------------------------------------------------------------------
# 【为什么需要它】coloraware 把色相编码进"高度落在哪条带"，色相分辨率被硬
# 量化成 4 档，中间色相（绿/橙/紫）只能 clamp 到最近端点 —— 这是实机还原度
# 低的根因。region 把材质从高度轴**解耦**：色相逐像素独立选色，再用 Bayer
# 有序抖动在空间上混相邻色。绿(120°) = 一半黄柱 + 一半青柱，远看即绿。
#
# 【为什么按"色轮分段"抖动在物理上自洽】C(180)/M(300)/Y(60) 三点把色轮均分
# 成 3 段各 120°，每段两端点恰好是"叠印能调出该段产物"的组合：
#     Y(60) → C(180)  段 → 黄+青叠印 = 绿   （这段的中间色相正是绿）
#     C(180) → M(300) 段 → 青+品红叠印 = 蓝 （中间色相正是蓝）
#     M(300) → Y(420) 段 → 品红+黄叠印 = 红 （中间色相正是红）
# 所以"落在哪段 + 段内比例"直接用抖动分配，颜色学上完全正确。
DITHER_MATRIX = (
    (0, 8, 2, 10),
    (12, 4, 14, 6),
    (3, 11, 1, 9),
    (15, 7, 13, 5),
)
DITHER_LEVELS = 16          # Bayer 4x4 的量化级数

# Bayer 8x8（64 级）—— 色阶更细腻，"极致"等级使用（见 QUALITY_LEVELS）
BAYER8 = (
    (0, 32, 8, 40, 2, 34, 10, 42),
    (48, 16, 56, 24, 50, 18, 58, 26),
    (12, 44, 4, 36, 14, 46, 6, 38),
    (60, 28, 52, 20, 62, 30, 54, 22),
    (3, 35, 11, 43, 1, 33, 9, 41),
    (51, 19, 59, 27, 49, 17, 57, 25),
    (15, 47, 7, 39, 13, 45, 5, 37),
    (63, 31, 55, 23, 61, 29, 53, 21),
)
BAYER8_LEVELS = 64
# 抖动阶数 -> (矩阵, 级数)。0 表示不做抖动（直接取最近色）
DITHER_MATRIX_BY_SIZE = {
    4: (DITHER_MATRIX, DITHER_LEVELS),
    8: (BAYER8, BAYER8_LEVELS),
}

# ---------------------------------------------------------------------------
# 蓝噪声阈值矩阵（v1.7.2，抖动图案可选）
# ---------------------------------------------------------------------------
# 【为什么要有它】Bayer 矩阵的阈值图案是**规则方格**（每 2^n 格精确重复），
# 人眼对规则图案特别敏感 —— 同一振幅的颗粒，规则图案看起来更"脏"、更像网格。
# 蓝噪声的阈値在频谱上**低频能量近 0、且没有离散谱峰**（像胶片噪点），
# 空间上点尽量互相远离。两者的**边缘分布都是均匀分布 U[0,1)**，所以
# "远看平均色"（＝还原度）完全不变，变的只是颗粒的**空间图案**。
#
# 【怎么来的】void-and-cluster（Ulichney 1993）：随机原型 → 反复把"最拥挤的 1"
# 与"最空的 0"对调直到稳定 → 按移除/放回的次序给每个格子编号（rank）。
# 32x32、sigma=1.5、固定种子，脚本 tests/make_blue_noise.py（重跑须逐位一致）。
#
# 【为什么是 32x32】Bayer8 的周期是 8 像素（0.42mm 柱宽下仅 3.4mm 就重复一次，
# 肉眼能看出"格子"）；32 像素 = 13.4mm，远大于手指/视距能分辨的颗粒尺度。
# 再大就要存更多数据，收益递减。
BLUE_NOISE32_SIZE = 32
BLUE_NOISE32_LEVELS = 1024
# void-and-cluster（Ulichney 1993），32x32、sigma=1.5、种子 20260919。
# 生成脚本 tests/make_blue_noise.py（重跑须得到完全相同的表）。
BLUE_NOISE32_B64 = (
    "eNoFwQNgEAAABMBHtm3b1qpl27Ztu5Zt27Zt27Zt3cXkYMdQFoT3AV5TPkbzKWx1KVxmSadHfj7m"
    "UD9VSLTTUo/nWyZgS59FWc72W2XSAsVAZRXzK0xiXxRRT6fCaJbWKvTkabfVDzVBMS3GDubiXdzQ"
    "YOTwLN5VBLzkM3TCel72TZbiOr11JYfnQ573PDVyaMfUG0xAXX73MFZ2ZI1yCqfTKQ9SHeTRJU9X"
    "f/bwP/3DeRXDGURUaIzUPDRneyRmdOxXH+9lJu9SUvzCNNVEP6bELl5AP9dmCKZ0PjTWAuTzKq5x"
    "R/3VEK5zIh9SRu3kS9fgQBVFQm1HTW12cB/GE3dVHobybZ5SLxziVIdUI7ZAek1DC2djbfwheACt"
    "/Utj8ZcnsM9xGeQlTMdXrMz42q2F2OY4iK0PXo0U3KRjHqfPqsYz3IMybqpNvOdyaK+orO7HGsqo"
    "Xqb8yOtxeKYuboRCbs8vmKIiri06GC5iBIc5Ef44rVZrkeOjMONpHDe4OaooOJ5yEfLrroMUHvMZ"
    "gQ9YQLM8kZVRjCtZz49YF6FZXXeUim3Rm/eU1lN1Ghd1gSX53o/R0dV5ke8w2W3013AOnNM5fOFO"
    "P0JePXZSB1d5z8cL5vRt/OIYzHAalPJrDFEGx9IxVUB8d2dUXUV3BLC3ZmOgI3iRMuC4CrCfbmK9"
    "VzMPbjifmjkS97MFk+qEC2MuhyM4t3uO5qCqL+gIs3q/tzMOs6sP7nOcZ6ALo2C9KtEepUIMg+76"
    "6mDujG1Kyt+a63rawvRMgCsuoZJoxURKgaaux9sOw7X8qeu+ylYWw2gphnsyT2gTyukWnrGJU6kh"
    "s6OArbAew0OY5L36is56pTWYoIxuiYo6jriuj9ba4UB80ms2RXvX5HwvYmz8wGltxBv30Hus0XNm"
    "ZkXPwmtPIRQWydTbWTlTcdSXO5DfG5iJd/wbIZQLib1Qu91MnXyHg1gVbT0VJy3U0UkWRHo/wS63"
    "5GVlw2jH50cfVjY9xXIVV2k/UQelZH/04Hk0RhxHZ6BeqhYTKIo78qaWODcXK6ZH4wPncSByOyOO"
    "cToWOybacRiu4SxfOKW+6DNveb4iY4X3sBVGaLzXwwzQdVZDWcXiMn9XYT1HL71TGTb2PR7VKs9g"
    "dxvLUcPxOJ1ZVZ55/BBfnIGlsY7Z3QN9PYq/fRD9VZRn/Z2ducfNUFdhnJCVMFiRXYcHVF9ZMd1D"
    "cVVLUEMfWdeXFEkPPUlXcEQJUdQHmRcbcFshUMI/sYxH8ctRtBI59RYbecFHEMJv2MtjuVm50ZV3"
    "0FJx0ZrvGN3J1Ywr1M2xPI0xuESZNdLJMFGdWMGHXQvpHEoVOFrBmATnnIhJvNCTENHJ2dzV8Qcz"
    "3RsvEU3lUVQPfIldkNA/mYMvlBNPOEtpOcIBWMAAV9BxdkVPJdZinvEpbtReN9FS3tdz52RBT0Qb"
    "n0QJHvIWTUEHL2REftMgFNIZ/fAIPNcxNHR9hsIjvUA/pFARzkQtR8MVttBW7lEHflRD1cAJfsNN"
    "l9cuLPcA1vEW/GZxLnB07dMrh+VYl1E5J0c8f2U0jtVmTHBmfERoxcMcD2QMD1AkBFMu91FiHOVf"
    "rFREBPEz6uEw52oZenG8NvOzD6oPj/q9GjC1P3mMyjoJc2g/Grgxz6obKvKpMyiZD7qRBjizCyo7"
    "A/2JBdDOobgVRVBS99AGqRWFPXGaK/BQZfHMuxmOm3yXl/FAJZCRrbGG6/BGabHFI3FF11yFcisN"
    "cRr/40qvVWd3Ujke92xOFhWED6qI2RrjOaymrg6nk0rGW+ymplzOqrqOCKqCG0zDqSQCuY/F8UdJ"
    "FA79/QDJPcXDmUt5Wdjj8QFfVZrzPBnvfAnhPM1RkcUztNrXNQCPPFwhfQMjudYheUul1I3p0EBr"
    "8c/AN3bRfZ9nLAxTFlVxIWzQJ5xjQc7FYJI1HVd7MdG1lMrbcI0dUN+p+YNbVc3f8Vpt2A4JvBMT"
    "mM0dMZNSVeZWbX9zJUVyGWzjdrVlJrznEOxUEx5wX+3GVTfACobXUA1ybK5SGm30fSxVZF50bDfE"
    "PuxQaoTVETfXKBR3Fv70fxBKBB8="
)
# 抖动图案：bayer = 有序方格（旧行为）；bluenoise = 蓝噪声（颗粒更自然）。
DITHER_PATTERNS = ("bayer", "bluenoise")
# 【默认值选 bluenoise 的实测依据】同一张实拍人像、同一批参数下（tests/diag_dither.py）：
#   相邻柱平均色差（颗粒振幅）与 8x8 块平均 ΔE（还原度）两者与 bayer **逐位一致**
#   （边缘分布相同），但蓝噪声的**低频功率**低两个数量级、无离散谱峰 →
#   规则网格纹理消失。这是"参数不变、观感变好"的免费改进。
DEFAULT_DITHER_PATTERN = "bluenoise"

# ---------------------------------------------------------------------------
# 采样方式（v1.7.3）—— 一个格子怎么代表它盖住的那块源像素
# ---------------------------------------------------------------------------
# Lumina Studio 对**两种模式都强制 NEAREST**（不抗锯齿），理由是抗锯齿会在边缘
# 造出浅色过渡像素、映射到"白色底座堆叠"会让网格浮空。我们底板是连续实体，
# 那个理由不成立 —— 所以不照搬默认值，而是按输入类型选（tests/diag_sampling.py）：
#
#   输入        面积平均            最近邻
#   线稿        1 个暗格（线没了）   90 个暗格（线保住，但断续）
#   像素画      364 个混色格         0 个混色格
#   照片        逐格偏差 0（基准）   逐格偏差中位 0.16→0.66 ΔE（随源像素比上升）
#
# 结论：照片用面积平均（点采样抽到的是块里某一个像素，可能落在边缘暗侧/亮侧，
# 逐格随机偏色 → 边缘毛刺）；线稿/像素画用最近邻。
SAMPLING_MODES = ("area", "nearest")
DEFAULT_SAMPLING = "area"

# ---------------------------------------------------------------------------
# 目标色量化（v1.7.3）—— CIELAB K-Means，见 core/quantize.py
# ---------------------------------------------------------------------------
# 【为什么值得做】它在两个方向上同时改善，而且**都不动配色算法本身**：
#   1. 生成耗时（主要收益）：CardStacker 的权重缓存按 **6bit 色码** 分桶
#      （Mixer.QUANT=63），每个**新桶**都要枚举 C(n,1)+C(n,2)+C(n,3)+C(n,4)
#      个子集做凸包投影（n=35 时 59535 个）。桶数实测（tests/out/diag_cache_photo120.txt，
#      直接读真实缓存条数）：照片 90x120 网格有 **1376** 个桶
#      （注意：不同 RGB 值是 6365 个，被 6bit 码合并了 4.6 倍 —— 别把两者混为一谈），
#      合成渐变 1117 个，平涂插画只有 65 个（大色块平均后颜色重合）。
#      → 同一张照片：关闭量化 105.1s，k=16 → 1.41s（×75），k=32 → 2.70s（×39）。
#      ⚠ 耗时数字必须在机器空闲时单独测。曾记到 485s 的异常读数（机器负载干扰），
#        与微基准 0.077s/桶、桶数反推 105s、逐次计时分布三份证据全部矛盾，已作废。
#   2. 局部一致性：相邻格落进同一簇 → 权重完全相同 → 抖动排布更干净，
#      顺带把换色次数压下来（照片 24→22；插画 20→16）。
#
# 【为什么 k=16 而不是 32】——诚信口径：两者在**用户可见还原度上等价**。
#   用户真正看的 8x8 块平均 ΔE（五组实测，越小越好）：
#     照片 90x120：关闭2.59 / k8 3.03 / k16 2.43 / k32 2.58 / k64 2.64
#     照片 30x40 ：k8 2.51 / k16 2.82 / k32 2.91 / k64 2.89
#     渐变 30x40 ：k8 51.95 / k16 50.31 / k32 49.74 / k64 49.95
#     插画 30x40 ：关闭13.42 / k8 14.56 / k16 13.29 / k32 13.49 / k64 13.45
#     插画 22x30 ：关闭13.14 / k8 13.47 / k16 13.71 / k32 13.03 / k64 13.12
#   → 16 与 32 **互有胜负、差距都在采样噪声内**（块数只有 12~180 个）。
#     所以选 16 的理由不是"更准"，而是**同样的画质便宜一倍以上**：
#     照片 90x120 的逐格选堆叠耗时 k16 1.41s / k32 2.70s / k64 5.29s（关闭 105.1s）。
#     另外 k=16 的换色次数下降也更稳（照片 24→22、插画 20→16）。
#   反面：k=8 的量化损失明显变大（照片 3.12、插画 5.05~7.16），会看到色带，别当默认。
#   ⚠ 别再往上调当"更保险"：k 越大越接近"关闭量化"，等于把耗时悬崖还回来。
#     量化损失随 k 单调下降（照片 3.12→2.25→1.61→1.15），但那点收益在最终 ΔE 上看不出来。
#
# 实测数据见 tests/out/diag_quant_photo120_clean.txt（README「颜色量化」一节有表）。
#
# 0 = 关闭（保留旧行为，用于对照与复现）。
QUANTIZE_COLORS_MAX = 64
DEFAULT_QUANTIZE_COLORS = 16
# 孤立点清理（判据照 Lumina：8 邻域与自己全不同 → 换成邻域众数）。
# **在抖动之前做** —— 因此它天然保色：抖动图案是后画的，替换目标色不会抹掉
# 承担混色的抖动点。1 格宽的细线因沿线邻居同类而不满足"全不同"，不会被误伤
# （tests/smoke_quant.py 第 ⑥ 节固化）。
#
# v1.7.4：**不再依赖量化开关**。v1.7.3 里 prepare_grid 在 quantize_colors<=0 时
# 直接返回，把去斑一起跳过了；现在量化关了就在原图上临时归并一次（只取**标签**
# 判孤立、颜色一个都不改）—— 见 core/quantize.despeckle_grid。
# 两条路径的判据是同一个等价关系，命中的格子集合逐格相同（实测 203 格 = 203 格，
# 见 tests/diag_despeckle.py）；区别只在"换成什么颜色"：量化路径换簇心色
# （有量化损失），独立路径换成**邻居已有的颜色** → 严格保色。
# 实测代价：独立路径只动 1.88% 的格子、块均 ΔE 0.1296；量化路径块均 ΔE 0.8168
# （但其中混着量化本身改色的贡献，不能全算去斑的账）。
DEFAULT_DESPECKLE = True
# 固定种子 → 同一张图重复生成逐位一致（全项目约定）
QUANTIZE_SEED = 20260919

_BLUE_NOISE_CACHE = None


def blue_noise_matrix():
    """解码 32x32 蓝噪声阈值矩阵，返回**已归一化到 [0,1)** 的嵌套元组。

    解压只做一次（首次调用），之后返回缓存 —— 逐像素查询不能有额外开销。
    用标准库 zlib/base64/struct，不引入任何第三方依赖。
    """
    global _BLUE_NOISE_CACHE
    if _BLUE_NOISE_CACHE is None:
        import base64 as _b64
        import struct as _struct
        import zlib as _zlib

        raw = _zlib.decompress(_b64.b64decode("".join(BLUE_NOISE32_B64)))
        vals = _struct.unpack("<%dH" % BLUE_NOISE32_LEVELS, raw)
        n = BLUE_NOISE32_SIZE
        inv = 1.0 / float(BLUE_NOISE32_LEVELS)
        _BLUE_NOISE_CACHE = tuple(
            tuple(vals[r * n + c] * inv for c in range(n)) for r in range(n))
    return _BLUE_NOISE_CACHE


# 饱和度低于此值的像素走白色（灰/黑/白统一由白带 + 厚度表现）
SAT_THRESHOLD = 0.15
# 目标色层达到这个厚度即视为完全遮住白底（PLA 实测不透光量级，用于预览）
FULL_COVER_MM = 0.45
# 目标色层厚度的下限（太薄的墙打不出来）与高度量化步长
MIN_REGION_MM = 0.10
REGION_QUANT_MM = 0.10      # 高度量化到该步长，收敛非流形冲突并贴合切片层

# ---------------------------------------------------------------------------
# 柱内叠色（v1.7.9）：分区配色的柱子内部按「滤光锚点」叠色片
# ---------------------------------------------------------------------------
# 【为什么必须叠】旧的单色实心柱只有一个不透明色，从正上方看就是那一种耗材
# 的颜色 —— 色彩表达完全靠抖动做**空间加色平均**，而 {W,C,M,Y} 四个色板色的
# 凸包最暗处 ≈ 中灰紫（亮度仅约 0.5）。于是照片里所有亮度 < 0.5 的像素
# （本例占 28%）都被抬亮并染上紫调：黑(0.05) → (0.50,0.38,0.66)、深绿格布
# → 蓝灰、棕发 → 粉紫。换用滤光锚点（C+Y=绿 / C+M=蓝 / M+Y=红 / C+M+Y≈黑）
# 后，同一张照片远看 CIE76 色差（块级 8×8、med/mean/P90/暗部<0.35）
#   4 单色顶点 6.52 / 14.75 / 43.94 / 42.29  →  8 滤光锚点 5.41 / 11.05 / 33.67 / 31.91
# （照片 128×171、k=16 量化；证据 tests/out/_exp_metric_matrix.py，
#   对照图 tests/out/diag_relief_color.png）
# ⚠ 改善**全部**来自锚点集，不来自把投影判据换成 Lab —— 后者实测是负收益
#   （幻影点，见 color_engine.mix_weights 的注释）。
# ⚠ 这是**薄层透射色域**，不是"想要什么色都行"：最暗锚点（C+M+Y 三色叠印）
#   呈现 ≈ (0.39,0.46,0.46) 中暗青灰，亮度只从 0.51 降到 0.44，**不是黑**；
#   而单层青的呈现色 (0.45,0.75,0.70) 比线材本身 (0,0.74,0.83) 淡得多。
#   原因是 0.1mm 薄层本身透光（标定表里青的红通道透射率只有 0.17）。
#   实测取舍（同一口径、两张图）：
#     · 暗部（亮度<0.35）**一律改善**：照片 42.29 → 31.91、合成扫掠 40.80 → 36.78
#     · 整体中位**看图**：偏暗的照片 6.52 → 5.41（−17%）；
#       偏亮、高饱和的合成图 30.46 → 36.61（+20%，亮色被冲淡发灰）
#   所以面板/说明里必须写清"偏亮、饱和色多的图可以先关掉对比"，别宣传成
#   "色域补齐、什么都打得出来"。
# ⚠ 再往上只有两条路：允许**同色重复**（多集锚点，锚点数 8→35，mix_weights 的
#   子集枚举 C(35,4)≈6 万/色桶 ≈0.076s/桶 —— 靠 k=16 量化才压到 1.4s），或者
#   加厚最外层色片。两条都是另一次改造，别顺手加进这一版。
RELIEF_STACK_DEPTH = 3       # 柱内最多叠几片（C/M/Y 的子集，白不做色片）
# 每片色层的厚度。**必须等于 REGION_QUANT_MM**：柱高按 REGION_QUANT_MM 量化、
# 色片分界平面按本常量取，两者同为 0.1 才能保证色片边界与柱高量化点对齐，
# 不会出现"柱顶把最后一片色层截掉一截"的浮点残差（那会丢掉一层颜色）。
RELIEF_LAYER_MM = 0.10
assert RELIEF_LAYER_MM == REGION_QUANT_MM, \
    "柱内色片厚必须等于高度量化步长，否则色片会被柱顶截断"

# v1.32.0（R39）：色片**以下**的柱体用哪种颜色。
# True  = 该柱自己的最外色片颜色 —— 竖墙（落差）与顶面同色，实物上不再有
#         "颜色交界的白缝 / 该青的地方发白"。实测（96×96、100mm、CMYK 麻将桌
#         类图）竖直暴露面积里原来是 37.8% 白基底，开启后 0%。
#         代价：柱体每一层都可能要用 2~4 种料，同一张图换色 9 → 38 次。
# False = 旧行为（白基底）。省料，但落差处露白。
DEFAULT_RELIEF_SIDE_OWN = True
# R7（v1.10.0）：「柱内叠色」复选框已删除，relief-region 下叠色链路**常开**、
# 由「锚点模式」下拉控制可达色域 —— 旧的 DEFAULT_RELIEF_STACK 总开关一并移除。

# ---------------------------------------------------------------------------
# 【legacy，v1.10.0 起不再被锚点模式引用】多集锚点（v1.8.0）/ 层序搜索（v1.8.1）
# ---------------------------------------------------------------------------
# R7 把「扩增 / 柱内混色」两档重定义为定长网格枚举（见下方 ANCHOR_MODES），
# 这组常量只剩 ``stack_anchors`` 的多集/层序分支还在读（该分支保留给历史
# 探针与实验脚本复用，产品链路不再走到）。新代码不要引用它们。
# 「柱内叠色」把可呈现色从 4 个单色顶点扩到 8 个滤光锚点（C/M/Y 的子集）。
# 再往上唯一不涨高度的路线是**允许同色重复**（多集）：锚点 8 → 35。
#
# 【为什么重复有意义】理想滤片 t²=t，重复无效果；但透射率是 Palette_256 实拍
#   标定值（C/M/Y 都是小数），t^1.5 ≠ t —— 重复 = 更深一档的同色相。
# 【实测收益】色域网格投影 ΔE 36.09 → 28.66（−21%）；真实链路块级中间调 ΔE
#   11.65 → 5.55（−52%）；照片块级中位 5.41 → 5.35、P90 33.67 → 29.22（−13%）。
# 【实测代价】生成 0.33s → 1.88s（mix_weights 子集枚举 162 → 59535，368×，
#   靠 k=16 量化压住）；换色 25 → 27（+2）；面数 +1.9%；柱高下限 0.3 → 0.4mm。
# ⚠ **深度必须 ≥ 4**：d=3（20 锚点）在色域网格上看着好（−16%），真实照片块级
#   中位反而退化 +7%（凸包形状被浅锚点拉偏）。d=5/6 只再降 2~3%，成本 ×6、
#   厚度再 +0.1mm —— 边际收益迅速衰减，**不要超过 4**。
# ⚠ 开启后某些柱会叠到 4 片，柱高下限从 0.3mm 抬到 0.4mm（模型最高点实测不变，
#   因为明暗厚度通常大于叠层厚度）。
RELIEF_ANCHOR_MULTISET_DEPTH = 4
assert RELIEF_ANCHOR_MULTISET_DEPTH > RELIEF_STACK_DEPTH, \
    "多集锚点的深度必须大于子集锚点，否则多集退化成 20 锚点的退化解"
DEFAULT_RELIEF_ANCHOR_MULTISET = False  # 默认关：先让用户按图对比再决定

# ---------------------------------------------------------------------------
# 锚点层序搜索（v1.8.1，默认关闭，依赖多集锚点）
# ---------------------------------------------------------------------------
# ``stack_present`` 是**有序**模型（v1.20.0 反射模型：顶层散射不经衰减、
# 深层被上方膜压暗，白底项与层序无关——同多集不同排列仍给出不同呈现色）：
# C+M（C 在顶）= 蓝灰 (144,150,180)，M+C = 紫灰 (152,144,179)。
# 旧实现把堆叠顺序固定成 canonical 色轮序，这个自由度被扔掉了。
# 层序搜索 = 对每个多集枚举全部排列作为候选锚点。
#
# 【实测收益】tests/out/_exp_order_search.py（真实照片 8x8 块，336 块）：
#   35 锚点中位 ΔE 6.94 → 播种远点 45 锚点 5.60（−19%）；暗饱和区 −4.5%；
#   亮粉白区也有改善；改善超 JND 的块 50、退化 0。
# 【零退化是恒真式保证】远点采样**播种保留全部 35 个旧锚点**，新锚点集是旧集
#   的超集 → 凸包只扩不缩 → 任何目标色的投影距离单调不增（不依赖抽样）。
# 【代价】cap=45 时 mix_weights 子集枚举 59,535 → 164,220（2.8×）。cap=60
#   还能再降 1.5%（5.60→5.52）但枚举 523,685（8.8×），边际收益不划算；
#   全排列 121 锚点需 879 万子集，不可行。**别盲目上调 cap。**
RELIEF_ANCHOR_ORDER_CAP = 45
DEFAULT_RELIEF_ANCHOR_ORDER = False  # 默认关：与多集锚点同策略，按图对比再开

# 柱内叠色的**基底色**（必白，唯一来源）。
#
# 【为什么必须写死成白，不能让用户用「底色」】反射叠色模型
# （``stack_present``，v1.20.0）的标定物就是「**白纸托底** + 4 层 0.1mm
# 色片」的 Palette_256 实拍板，模型里的白底项 b·(∏t)² 隐含基底是白。
# 基底若不是白，实打会与模型分叉：底色的滤光把所有颜色染成底色调
# （默认底色是天蓝 → 整块板发青），这正是"颜色失真"的另一来源。
# 单面/双面叠色卡同样把基底固定为白（``build_material_list(..., "W")``），
# 这里与它们同源。柱子顶面的材质取"堆叠最外层"，所以底板只在**底面**与
# **底板高度以下的外侧壁**露出，改成白对观感影响很小。
RELIEF_SUBSTRATE_KEY = "W"
assert RELIEF_SUBSTRATE_KEY == "W", \
    "柱内叠色的基底色必须存在于 key_to_mat（build_material_list 恒定并入 base_key）"

# ---------------------------------------------------------------------------
# 锚点模式（v1.10.0）：柱内叠色的「可达色域」三档（R7 重定义）
# ---------------------------------------------------------------------------
# R7 按用户原始需求把三档重定义为**定长网格枚举**（不再用「多集 + 层序搜索 +
# 远点裁剪」）。白滤光片恒等（t_W=(1,1,1)），所以任意 ≤N 层的真实堆叠都能用
# 白片垫到定长 N 表达（尾部垫白不移动既有层的深度权重），三档构成严格的
# 超集链：基础8 ⊆ 扩增64 ⊆ 柱内混色256（恒真式，实测逐色验证）。
#
#   basic  子集模型（legacy 路径，逐位兼容 v1.7.9）：白底 + C/M/Y 各至多一片
#          → 8 锚点（白/C/M/Y/蓝/绿/红/最暗青灰）
#   stack  定长 3 层网格（R7-①「白底叠色」）：白基底固定，3 层自由色片
#          （C/M/Y/W 可重复、顺序自由）→ 4³=64 种堆叠，实测 64 色全部互异
#   mix    定长 4 层网格（R7-②「自由底」）：第 4 层即基底位、四色都可做底
#          → 4⁴=256 种堆叠，实测 256 色全部互异；**完全覆盖 v1.9.0 的 35/45
#          锚点集（实测 0 缺失）**，旧档的 4 片深色档由它找回
#
# ⚠ 关于"4³=128"：用户原话估算 128，实为 4³=64（2⁷ 才是 128）；4⁴=256 正确。
#   上面的 64/256 是**去重前=去重后**的真实锚点数（呈现色 6 位小数逐色验证）。
#
# 【混色成本与 k-NN 预选】mix_weights 的精确投影要枚举 C(n,≤4) 个子集：
#   n=8→163、n=45→16.4 万、n=64→63.7 万、n=256→1.75 亿（不可行）。
#   所以 grid 两档走「最近 K 锚点预选 + 有界穷举」：按到目标色的 sRGB 距离
#   取最近 K 个（平局按锚点序号，确定性），白锚点强制入选，再在这 ≤K+1 个
#   上做精确投影。锚点集全量参与"谁近用谁"，没有任何锚点被事前裁掉 ——
#   这与 v1.8.1 的远点裁剪（事前把 256 砍到 45）有本质区别。
#   实测（tests/out/_exp_anchor_grid.txt，真实照片 336 块、均衡档量化）：
#   K=32 时块级 ΔE 中位 新①1.41 / 新②1.13，远好于旧 35/45 全量（2.74/2.79）；
#   K=16/24/32/48 单调收敛，K=32 是耗时/质量膝点；每桶枚举量 C(32,≤4)+n ≈
#   4.1 万，比旧 45 全量（16.4 万）还快 4 倍。
ANCHOR_NEAR_K = 32              # grid 档的 k-NN 预选数（实验膝点，别盲目上调）
ANCHOR_MODE_KEYS = ("basic", "stack", "mix")
ANCHOR_MODES = {
    "basic": {
        "kind": "subset",
        "cn": "基础（8色）", "en": "Basic",
        "desc": "白基底 + 每色至多一片：4 色滤光子集，8 个可达锚点"
                "（白/天青/品红/黄/蓝/绿/红/最暗青灰）。生成最快、换色最少。",
        "desc_en": "White base + at most one sheet per color: 4-color filter "
                   "subset, 8 reachable anchors (white/cyan/magenta/yellow/blue/"
                   "green/red/darkest teal-grey). Fastest, fewest changes.",
        "max_depth": RELIEF_STACK_DEPTH,
        "allow_repeat": False,
        "order_perm": False,
        "near_k": 0,
    },
    "stack": {
        "kind": "grid", "grid_depth": 3, "free_base": False,
        "cn": "扩增（白底叠色 64 色）", "en": "Stack",
        "desc": "白基底固定，柱内叠 3 层自由色片（天青/品红/黄/白，可重复、"
                "顺序自由）：4³=64 种堆叠、64 种呈现色全部不同。同一色相有了"
                "深浅多档、层序参与配色，照片色差比基础档明显下降。",
        "desc_en": "Fixed white base, 3 free sheets per column (cyan/magenta/"
                   "yellow/white, repeatable, any order): 4^3=64 stacks, 64 distinct "
                   "colors. Each hue gains light/dark steps and sheet order joins "
                   "the shading; photo color error drops clearly vs Basic.",
        "near_k": ANCHOR_NEAR_K,
    },
    "mix": {
        "kind": "grid", "grid_depth": 4, "free_base": True,
        "cn": "柱内混色（自由底 256 色）", "en": "Mix",
        "desc": "底色不固定（白/天青/品红/黄都可做最底层），柱内共 4 层："
                "4⁴=256 种堆叠、256 种呈现色全部不同，完全覆盖旧 45 色档，"
                "暗部与高饱和区改善最大。混色用「最近 32 锚点预选 + 精确投影」，"
                "生成耗时与旧 45 色档相当。",
        "desc_en": "Unfixed base (any of white/cyan/magenta/yellow can be the "
                   "bottom), 4 sheets per column: 4^4=256 stacks, 256 distinct "
                   "colors, fully covering the old 45-color tier; biggest gains in "
                   "dark and highly-saturated areas. Blending uses nearest-32 anchor "
                   "preselection + exact projection, on par with the old 45 tier.",
        "near_k": ANCHOR_NEAR_K,
    },
}
DEFAULT_ANCHOR_MODE = "basic"   # 对齐 v1.7.9 默认 8 锚点

# ---------------------------------------------------------------------------
# 生成模式：id ↔ 中文名（唯一来源）
# ---------------------------------------------------------------------------
# 面板下拉、质量报告的「生成模式」行、3MF 颜色指引都从这里取名字 —— 改名只需改
# 一处，不会再出现"面板叫单面叠色、报告里还印 flat"的错位。
#
# ⚠ id 沿革（两次改名，都在这里留痕）：
#   v1.7.6 及以前："flat" = 平整分层板（分区配色 + 起伏压平到 25%）。
#   v1.7.7：改造成 "single" = 单面叠色（白底板上叠色片、不做背面镜像）。
#   v1.22.0（本轮）：**"single" 功能整体下线**，同位置换成 "mosaic" = 平整
#     色块板 —— 照片自动聚类 N 色、逐像素分区、顶面等高，**完全不走光学叠色
#     链路**（不做透射积、不做凸包投影、不做抖动混色）。理由是叠色链路的光学
#     模型对"照片直出彩色"这件事本身就是多余的中间层：既然打出来的就是被分配
#     的那个耗材色，就不该再经一层"四色凸包可达域"的近似（R26-C 实测该层在
#     亮白/近黑两端各留下 13.8 / 29.3 ΔE 的系统性误差）。
#   ``generation.generate`` 仍接受旧 id "flat" / "single" 作为别名（旧 .blend /
#   旧脚本用），一并翻译成 "mosaic" —— 旧文件不会掉回浮雕，也不会报错。
#   v1.23.0（R28）：**三模式按用户口径改名**（id 不变，旧工程/脚本不受影响）：
#     relief → 叠色浮雕 / mosaic → 全色平板 / card → 双面平板叠色。
#     顺序也按用户列举的 ① 全色平板 ② 叠色浮雕 ③ 双面平板叠色 排列，
#     并把默认模式设为 ①（用户把它列在第一位，且"精准还原颜色"是他的首诉求）。
#   v1.44.0（R54）：**新增第四种 postcard = 浮雕明信片**（插在 relief 之后，
#     mosaic / relief 的下拉索引不变，旧工程不会跳档）：
#       正面 = 叠色浮雕（与 relief 同一条配色/几何链路）；
#       反面 = 单色凹刻（另建一块底板，底面按反面图的高度场凹进去）。
#     两种厚度分开给：正面叠色浮雕厚度（默认 2.0）+ 反面最大凹刻深度（默认 0.4）。
#     总厚 = 正面厚度 + 凹刻深度 + 安全白层。
MODE_IDS = ("mosaic", "relief", "postcard", "card", "single")
# v1.0.14（用户 2026-11-20）：三个模式改名 —— ID 与顺序不动（旧工程/旧脚本
# 仍按 mosaic/relief/postcard/card/single 存取），只改对外显示名。
MODE_CN = {
    "mosaic": "全色平板",
    "relief": "叠色浮雕",
    "postcard": "明信片浮雕",
    "card": "双面平板",
    "single": "单面平板",
}
# 英文名单独一张表：不能用 i18n.t(MODE_CN[...]) —— "叠色浮雕" 同时是插件分类名
# （i18n 表里映射成 "ColorStack"），直接 t() 会把模式名翻成插件名。
MODE_EN = {
    "mosaic": "Full-color Flat",
    "relief": "Stacked Relief",
    "postcard": "Postcard Relief",
    "card": "Dual-sided Flat",
    "single": "Single-sided Flat",
}
DEFAULT_MODE = "mosaic"
# 叠色模式（card）强制使用的颜色键（不受"参与配色的颜色"勾选影响）：透光叠色的
# 物理需要红黄蓝白齐全 —— 白既当图案的白，也当层与层之间的垫片。
# ⚠ v1.22.0 起只剩 card 一种叠色模式（single 已下线），mosaic 用的是照片聚类
# 出来的 N 个动态色键（见 core/mosaic.py），与本表无关。
MODE_STACK_KEYS = ("C", "M", "Y", "W")

# ---------------------------------------------------------------------------
# 平整分层板：起伏压平比例
# ---------------------------------------------------------------------------
# 【为什么不做成绝对 0 起伏】顶面如果完全等高，模型就是一块纯色平板 —— 图片的
# 明暗信息全丢了（颜色虽然还在，但阴影层次没了，反而更不像照片）。用户要的是
# "接近平整"：把起伏压到"叠色层总厚"的 25%（3mm 总厚 → 0.75mm 起伏，约 4 层
# 0.2mm 层高），俯视基本是平的，同时明暗层次保留、手感也有。
#
# 另外它还有个几何上的好处：高度被量化成很少的几个值，竖墙只有几种高度，
# 网格比浮雕模式小得多。
FLAT_RELIEF_RATIO = 0.25

# ⚠ v1.7.7 起本常量**已无常驻调用方**：「平整分层板」模式被改造成「单面叠色」，
# 不再走"分区配色 + 起伏压平"这条链路。compute_region(flat=True) 作为休眠路径
# 保留（纯算法，便于以后回头做"接近平板的浮雕"实验），所以这个常量跟着留下。
# 面板上已无任何入口能改到它。
#
# v1.22.0 补充：那个「单面叠色」位置现在换成了「平整色块板」（mosaic），后者是
# **真平顶**（零起伏），不复用本比例；所以 compute_region(flat=True) 与本常量
# 依然是休眠状态，别因为"又有平整板了"就去接它。

# ---------------------------------------------------------------------------
# 自由配色：颜色没有层序要求
# ---------------------------------------------------------------------------
# 【为什么不再有"色序"】用户实机确认：机器可以在**任意 Z 高度**使用**任意颜色**，
# 同一层里可以出现不同颜色，Z 轴方向也可以换色，颜色顺序没有任何强制要求。
# 于是色相覆盖能力只取决于"哪几种颜色参与配色"，与它们的排列顺序无关 ——
# 三种彩色 C(180°)/M(300°)/Y(60°) 只要有 2 种以上，色轮的段就被"两端点叠印
# 产物"覆盖（黄+青=绿、青+品红=蓝、品红+黄=红）；4 色齐全时抖动可覆盖整圈 360°。
ALL_COLOR_KEYS = ("C", "M", "Y", "W")   # 可供选择的全部颜色
BASE_COLOR_KEYS = ("W", "C", "M", "Y")  # 可选作"底板/共用底带"的颜色

# ---------------------------------------------------------------------------
# 质量等级（极致 / 均衡 / 像素）
# ---------------------------------------------------------------------------
# 【v1.3：三档都用满 4 色】旧版用"整机只允许 2 / 3 / 4 色"来拉开换色次数，实测
# 在那张浅灰调照片上"像素"档会塌缩成**只有白色** → 整块模型一片白。用户明确
# 要求"三档等级均用 4 色"，所以颜色不再被砍。
#
# 于是三档的差别只在两处，各自对应一端：
#
#   dither          抖动瓦片阶数：8 = Bayer 8x8（64 级色阶，最细腻），
#                   4 = Bayer 4x4（16 级）。决定**混色的空间细腻度**（还原端）。
#   weight_levels   权重量化分辨率。0 = 连续不量化（最准）；
#                   1/8 = 权重取 0/⅛/…/1；1/4 = 更粗。
#   merge_below     权重小于它就并给最强色 —— 这就是用户要的
#                   「色相相近就不需要额外换颜色」。决定**换色次数**（省料端）。
#
# 三个参数的意义：目标色被投影成一组"四色叠加比例"（见 color_engine.Mixer），
# 量化越粗、并项阈值越高，一个像素越倾向于只用一种颜色 → 该层出现的颜色种类
# 越少 → 换色次数越低，代价是色相被并到最接近的线材色上，偏差变大。
#
#   band_ratio      共用底带占"叠色层总厚"的比例（该厚度内所有柱子统一用底色，
#                   即"同一根柱子沿 Z 轴分段换色"）。
#   white_snap      近白像素（投影里白的权重 ≥ 该值）直接并成纯白。
#                   1.01 = 关闭。实测它对"每层不同颜色数"没有贡献（见下），
#                   但能减少实际的切换次数，需要时可用。
#
# 【实测（tests/t_probe_levels_v13.py，合成色相扫掠图 96x64 / 实拍照片 64x85）】
#
#   等级    换色次数(扫掠/照片)   色相偏差(扫掠/照片)   单色像素(照片)
#   极致        37 / 32            0.4°  / 0.4°          0%
#   均衡        33 / 31            1.4°  / 2.4°          1%
#   像素        31 / 24            22.2° / 24.3°         100%
#
# 【重要：换色次数在 4 色全开时存在物理上限，调参拉不开】
# 实测把权重量化从"连续"一路加到 1/8、并项阈值从 0 加到 0.22，换色次数一直
# 停在 31~33 —— 纹丝不动。只有把权重粗暴砍到 1/4 并项 0.30 才掉到 24。
#
# 原因：换色次数 = Σ(每层出现的颜色数 − 1) ÷ 层高。而"最底层包含全部柱子"，
# 所以只要图片里三个色相段都有人用，每一层就必然要过 C/M/Y/W 四种耗材 ——
# 3 次 × 15 层 ≈ 40 次。配色的精细程度改变的是"每个像素用几种"，改变不了
# "这一层里有几种"。**换色次数由图片本身的色相分布决定。**
#
# 想让换色次数真正大幅下降，只有三条路（都不属于本参数组）：
#   ① 减少参与配色的颜色数（旧版做法，会让浅灰调图片塌缩成纯白，已废弃）；
#   ② 让色相和高度相关（即 coloraware 路线，同一高度只有一种颜色）；
#   ③ 降低采样精度 / 层数。
#
# 【实测结论：band_ratio 对换色次数没有贡献，所以三档全部设为 0】
# 同一张图 64x48、relief、叠色层 3.0mm、底板 0.6mm，实测：
#
#   用色 4 色 / 底带 0%   → 换色 34 次   色相偏差 7.9°
#   用色 3 色 / 底带 0%   → 换色 25 次   色相偏差 8.8°
#   用色 3 色 / 底带 20%  → 换色 25 次   色相偏差 8.8°   ← 与上一行完全相同
#   用色 2 色 / 底带 45%  → 换色 21 次   色相偏差 50.0°  ← 反而比无底带的 14 次多
#
# 为什么底带无效：一根柱子的底带会被夹到"柱高 − 顶端留色"，于是**每一层里总有
# 一批"刚好在这一层到顶"的柱子露出自己的颜色**。底带越深、被夹掉的柱子越多，
# 这批泄漏就越多，正好抵消底带本身省下的次数。所以换色次数只能靠**减少同层
# 出现的颜色种类**来降 —— 高度分层策略对它没有影响。代码里保留了底带能力
# （用户确认机器支持 Z 轴换色），但三个等级都不启用，免得白给侧壁加一圈底色。
QUALITY_LEVELS = {
    # 【R8（v1.11.0）区分度重设计】v1.10.0 三档只差 weight_levels / merge_below，
    # 在 R7 的 kNN 肥权重下失去咬合力 —— 实拍两图 + 合成扫掠的换色次数完全相同
    # （26/27/29 三档一致，tests/out/_diag_quality_r8.py）。重设计的三个杠杆：
    #   1. near_k（kNN 预选宽度）：32→16 投影提速约 5 倍且还原度不劣化
    #      （kNN 瘦候选的组合权重更肥、抗量化 —— 与 R7 的"量化-正则化"现象一致）；
    #   2. max_components（每像素混色分量上限）：像素档 = 1，即每像素只取最近的
    #      1 种锚点堆叠，堆叠种类 35~40 → 12~15，是唯一能把换色真正压下来的杠杆；
    #   3. suggested_res：像素档 64（粗网格本就是"像素"的语义）。
    # 参考值（ref_swaps/ref_err/ref_hue）= 左/右两张实拍照片 + 合成扫掠、各档
    # 建议分辨率下的 v1.11.0 实测中位（方法与生成链路同参：
    # tests/out/_diag_quality_r8.py）。⚠ 换色绝对值随图片内容变化，参考值
    # 只用于横向比较三档；色相/ΔE 的下限由四色耗材的色域决定，与档位无关。
    "ultimate": {
        "cn": "极致", "en": "Ultimate",
        "desc": "最细：每格单独上色，细节最多，换色也最多",
        "desc_en": "Finest: one colour per cell; most detail, most colour changes",
        "max_palette": 4,
        "dither": 8,
        "weight_levels": 0.0,
        "merge_below": 0.0,
        "near_k": 32,
        "max_components": 0,
        "white_snap": 1.01,
        "band_ratio": 0.00,
        "suggested_res": 128,
        # v1.30.0：小区域合并阈值（mm）。极致档只并掉 1 格孤岛，保留细节。
        "min_feature_mm": 0.6,
        # v1.31.0：每 block_px 格并成一个大色块（1 = 不并，保留每一格）。
        "block_px": 1,
        # v1.31.0：色阶上限（0 = 不限）。极致档不压色阶。
        "max_stacks": 0,
        "ref_swaps": 28,
        "ref_err": 9.0,
        "ref_hue": 13.0,
    },
    "balanced": {
        "cn": "均衡", "en": "Balanced",
        "desc": "日常首选：2 格并一块，远看几乎不变，换色更少",
        "desc_en": "Daily pick: 2 cells per block; looks the same from a "
                   "distance with fewer colour changes",
        "max_palette": 4,
        "dither": 8,
        "weight_levels": 0.0625,
        "merge_below": 0.065,
        "near_k": 16,
        "max_components": 2,
        "white_snap": 1.01,
        "band_ratio": 0.00,
        "suggested_res": 96,
        # v1.30.0：1.6mm 以下的碎色块并给邻区 —— 0.4mm 喷嘴上再小的色块
        # 会被溢料吃掉，还各自触发一次换色。
        "min_feature_mm": 1.6,
        # v1.31.0：2 格一块（0.78mm 像素下 1.56mm）。实测（100mm/128px/CMYK）
        # 远看 dE 9.60 → 9.83（肉眼不可分辨）；⚠ 换色**不一定**更少（19→20 实测
        # 反弹，因为竖向并块把整行复制到相邻切片层），省换色要靠下面的色阶上限。
        "block_px": 2,
        # 最多 6 种叠法：色阶减半（实测换色 19→18、36→24 多为下降，但不保证）
        "max_stacks": 6,
        "ref_swaps": 26,
        "ref_err": 9.0,
        "ref_hue": 13.0,
    },
    "pixel": {
        "cn": "像素", "en": "Pixel",
        "desc": "海报风：4 格并一块，细节明显变粗，最省换色",
        "desc_en": "Poster look: 4 cells per block; coarser detail, fewest "
                   "colour changes",
        "max_palette": 4,
        "dither": 0,
        "weight_levels": 0.125,
        "merge_below": 0.12,
        "near_k": 12,
        "max_components": 1,
        "white_snap": 1.01,
        "band_ratio": 0.00,
        "suggested_res": 64,
        # v1.30.0：3.2mm —— 海报式大色块，斑点最粗。
        "min_feature_mm": 3.2,
        # v1.31.0：3 格一块（2.34mm）—— 「像素」这名字该有的粗颗粒。
        # v1.0.16（用户 2026-11-21 实拍面板）：0.84mm = 4 × 0.21mm，细线阈值
        # 既然是「极致 0.21 / 均衡 0.42 / 像素 0.84」，并块也必须同尺：4 格。
        # 旧值 3 格与面板同一行里的 0.84mm 对不上，用户一眼就看出矛盾。
        "block_px": 4,
        # 最多 3 种叠法：海报式，色块 + 色阶都粗
        "max_stacks": 3,
        "ref_swaps": 22,
        "ref_err": 10.0,
        "ref_hue": 19.0,
    },
}
DEFAULT_QUALITY = "balanced"
QUALITY_KEYS = ("ultimate", "balanced", "pixel")

# ---------------------------------------------------------------------------
# 上色方式（v1.30.0 / R37）
# ---------------------------------------------------------------------------
# 【为什么新增这一项】旧链路只有一个做法：把目标色投影到锚点凸包，再按权重
# 做**逐像素有序抖动**。原理上没错（人眼会把相邻像素平均），但打印的落点单位
# 不是像素：0.4mm 喷嘴在实际 0.78mm 像素上换色，喷头会在每个色点边界溢出，
# 于是"远看混色"变成"近看彩色碎点 + 一圈溢料晕"。用户 2026-10-24 实拍两张
# （极致 = 碎点、像素 = 大片偏色）就是这条链路的直接后果。
#
# Lumina Studio 的做法（_lumina/core/image_processing.py）是**不抖动**：
# 每个像素在 LUT 里取最近的一种堆叠，匹配后只做孤立点清理。打印结果是一张
# 干净的"色块画"，代价是色阶台阶明显、渐变变硬。
#
# 所以这里把两种做法都留着，默认用"整块上色"：
#   solid 整块上色 = 一像素一叠法，不抖动（默认；干净、可预测）
#   dither 网点混色 = 相邻像素用不同叠法凑色（远看更准，近看是颗粒；换色多）
COLOR_MATCH_MODES = {
    "solid": {
        "cn": "整块上色",
        "en": "Solid blocks (recommended)",
        "desc": "每个像素只用一种叠法，打出来是大片干净颜色、边缘利落；"
                "完全没有彩色噪点。代价：颜色只有几十级，渐变会变成台阶，"
                "原图里特别亮/特别暗的部分会被压到最近的那一档。",
        "desc_en": "One stack per pixel, no dithering: clean flat colour areas "
                   "with crisp edges and zero colour noise. Cost: only a few "
                   "dozen levels, so gradients become steps and extreme "
                   "highlights/shadows snap to the nearest level.",
    },
    "dither": {
        "cn": "网点混色",
        "en": "Halftone dithering",
        "desc": "相邻像素用不同叠法凑出中间色，远看更接近原图；近看是一颗颗"
                "彩色色点，打印机在每个色点边界会溢料，换色次数与打印时间"
                "明显增加。只在色阶不够用时才值得开。",
        "desc_en": "Neighbouring pixels alternate stacks to fake in-between "
                   "colours. Closer to the photo from a distance, but grainy up "
                   "close, with ooze at colour boundaries and far more filament "
                   "changes / print time.",
    },
}
DEFAULT_COLOR_MATCH = "solid"
COLOR_MATCH_KEYS = ("solid", "dither")

# 最小可打印色块（mm）—— 小区域合并的物理阈值。
# 取 1.5 倍喷嘴直径：再小的色块在 0.4mm 喷嘴上打印时会被溢料吃掉、
# 还会单独触发一次换色（实拍里那些 1mm 的黄块四周的品红边就是证据）。
MIN_PRINT_FEATURE_MM = 0.6

# ---------------------------------------------------------------------------
# 最小可打印**宽度**（mm）—— v1.33.0 / R40
# ---------------------------------------------------------------------------
# 【为什么面积阈值不够】``MIN_PRINT_FEATURE_MM`` 只按**面积**并碎块：一条
# 1 格宽、200 格长的色条面积 200 格，远超阈值，于是被完整保留 —— 但它的
# **宽度**只有 1 格。实测用户 2026-10-29 那张 75×100mm 双面卡（分辨率 600，
# 1 格 = 0.167mm、切片实测线宽 0.376mm）：顶层 **7.7% 的面积落在宽度 <
# 0.75mm 的细结构里**，其中大量是两色之间的 1 格窄条。
#
# 切片器对这种窄条只有两种处理：丢掉（那就是**空隙**，两色之间露一条缝，
# 成品一掰就断），或者强行挤成一条 0.376mm 的线（颜色糊开）。用户连两轮
# 反馈的"两个颜色间的空隙导致断裂"就是前者。
#
# 【v1.35.0 / R42：阈值必须跟喷嘴线宽一起缩放，不能再写死毫米数】
# 旧版把 0.6/0.8/1.2 写成绝对毫米，与喷嘴无关。实测（同一张图、同一精细度，
# 只把喷嘴从 0.4 换成 0.2）输出**逐格完全相同**：宽度阈 4 格、面积阈 13 格、
# 相邻异色边 3.1%，三项一个不变 —— 0.2 喷嘴明明能印 0.22mm 的线，却仍按
# 0.6mm 的门槛在删细节，等于白换喷嘴。用户"0.2/0.4 喷嘴都不比之前细"就是它。
#
# 第二个后果：精细度越高，同样的毫米阈值折算成格子越多（600 → 4 格、
# 1000 → 6 格），被并掉的比例反而上升 —— 所以"把精细度往上调"不但不更细，
# 还会更糊（实测异色边 5.3% → 3.1% → 1.6%）。现在改成「倍数 × 线宽」，
# 换喷嘴、改幅面都自动跟随。
#
# 宽度倍数（v1.0.15 用户 2026-11-20 定稿）：
#   极致 0.5 条线（0.21mm）/ 均衡 1.0 条（0.42mm）/ 像素 2.0 条（0.84mm）。
# 【为什么极致从 1.0 改成 0.5】旧口径下三档是 0.42 / 0.84 / 1.20mm，用户在
# 极致档看到"窄于 0.42mm 的细线并入旁边"，而 0.42mm 正好是**一条挤出线**的
# 宽度 —— 等于把"还能单独印出来"的 1 格细线也并掉了。用户实测口径：极致要
# 保留到半条线（0.21mm，靠相邻两格共享一条挤出线也能看出轮廓），均衡才是
# 一条线，像素两条线。换喷嘴（0.22mm）时三档自动跟着缩放。
QUALITY_MIN_WIDTH_FACTOR = {"ultimate": 0.5, "balanced": 1.0, "pixel": 2.0}
QUALITY_MIN_FEATURE_FACTOR = {"ultimate": 1.43, "balanced": 3.81, "pixel": 7.62}
# 历史常量，仅作读不到喷嘴时的兜底（旧探针也还在引用）。
MIN_PRINT_WIDTH_MM = 0.80
QUALITY_MIN_WIDTH_MM = {"ultimate": 0.6, "balanced": 0.8, "pixel": 1.2}


def min_print_width(quality=None, nozzle=None):
    """某档质量 + 某档喷嘴下的**最小可打印宽度**（mm）。

    唯一实现处：生成链路（``generation.quality_profile``）、面板文案、
    探针都从这里取 —— 三处各写一份就会漂移（R42 之前就是三处各写死）。
    """
    q = quality if quality in QUALITY_KEYS else DEFAULT_QUALITY
    f = QUALITY_MIN_WIDTH_FACTOR.get(q, QUALITY_MIN_WIDTH_FACTOR[DEFAULT_QUALITY])
    return float(f) * float(nozzle_width(nozzle))


def min_print_feature(quality=None, nozzle=None):
    """某档质量 + 某档喷嘴下的**最小可打印碎块边长**（mm，面积阈值用平方）。"""
    q = quality if quality in QUALITY_KEYS else DEFAULT_QUALITY
    f = QUALITY_MIN_FEATURE_FACTOR.get(
        q, QUALITY_MIN_FEATURE_FACTOR[DEFAULT_QUALITY])
    return float(f) * float(nozzle_width(nozzle))


# 【v1.37.0 / R44】毫米阈值 → "至少几格"的**唯一**换算处，带 0.2 格容差。
#
# 为什么不能直接用 ceil：100mm / 240 精细度 的格宽是 0.4167mm，0.4 喷嘴的线宽
# 是 0.42mm —— 两者物理上是同一条挤出线（差 0.8%），但 ceil(0.42/0.4167) = 2，
# 于是：
#   · 色点从 1 格被撑成 2 格 = 0.83mm，长边只剩 120 个颜色点（本该 240）；
#   · 最小可印宽度从 1 格变 2 格，"1 格宽"的细节被当碎条并掉。
# 两条叠起来就是用户说的"精细度降低了"。
# 容差取 0.2 格（≤20% 的超出算"一样粗"）：0.4167 → ceil(1.008−0.2)=1 格，
# 600 精细度（0.1667/格）仍是 ceil(2.52−0.2)=3 格 = 0.5mm，与 R41/R42 实测一致。
def px_from_mm(mm, pixel_mm, tol=0.2):
    """毫米阈值折成"至少几格"，下限 1 格。"""
    return max(1, int(math.ceil(float(mm) / max(1e-9, float(pixel_mm)) - tol)))

# 纯白（空叠）只留给**真正接近中性**的目标色（v1.33.0 / R40）。
# 【实测依据】呈色模型给「八层全白」的预测是 Lab(83.4, −8.0, +4.1) ——
# 一个**偏绿**的浅灰，而实物白是奶油白（用户实拍最亮 4% 的像素
# sRGB(207,206,184) → Lab 约 (83,−2,+14)，L 对得上、色相相反）。后果：
# 每个浅色目标（浅青、浅蓝、奶油）在 Lab 里离这个"绿白"比离最近的青色
# 叠法还近 —— 实测浅蓝 (78.6,−5.4,−16.3) 被判给纯白，色相差 21。这就是
# 用户说的"麻将牌该青却印成白"。
# 修法不动呈色模型（它是对 35 个锚点整体标定过的，动一处全歪），只在
# **选色**这一步加一条：目标色的 Lab 色度 C = √(a²+b²) 超过阈值时，纯白
# 不参选。阈值 10 让奶油(9.3)/浅灰(0)/白(1.3) 仍可用纯白，浅青(12.3) 以上
# 一律取最近的彩色叠法。
WHITE_MAX_CHROMA = 10.0


# ---------------------------------------------------------------------------
# 全色平板（mosaic）的质量等级（v1.23.0 / R28）
# ---------------------------------------------------------------------------
# 【用户口径】色块数**不允许手动改**，只由质量等级决定。理由（用户原话）：
# 他不懂色彩术语，给他一个 16 色滑杆也判断不出"该几色"，压到三档预设里
# 让"选了会怎样"变成一句可读的话更实用。
#
# 三个值的关系不是线性拍脑袋，有 R27 实测支撑（12 色：ΔE 中位 2.76 / ≤3 覆盖
# 54.2%；8 色之后每加一色的边际改善迅速变小）：
#   极致 16 色：ΔE 3.31 / p90 8.85（R28 复测），16 色后再加收益 <2%
#   均衡 10 色：日常照片的分水岭 —— 主体色相齐全，碎斑用 1.0mm 合并
#   像素  6 色：海报式块面，颗粒最粗但换色/耗材最省（单 AMS 4 槽 + 2 色手动）
#
# min_mm 与色块数**联动**：色块少了，每块面积自然变大，碎斑判据也该放宽 ——
# 否则"像素档 6 色"还会把大量小碎斑并来并去，白做一轮合并。
MOSAIC_QUALITY = {
    "ultimate": {"color_count": 16, "min_mm": 0.6,
                 "desc": "16 个色块 / 碎斑 0.6mm 起保留"},
    "balanced": {"color_count": 10, "min_mm": 1.0,
                 "desc": "10 个色块 / 碎斑 1.0mm 起保留"},
    "pixel":    {"color_count": 6,  "min_mm": 2.0,
                 "desc": "6 个色块 / 碎斑 2.0mm 起保留"},
}

# ---------------------------------------------------------------------------
# 模型最大厚度（v1.23.0 / R28）
# ---------------------------------------------------------------------------
# 【为什么改成"只给总厚度"】用户要的是三模式都能设"模型最大厚度"——他关心的是
# 打出来多厚，不是"底板 0.6 + 叠色层 3.0"这种内部拆分。所以面板上只留一个
# 总厚度滑杆，拆分由这里统一算。**公式只有这一份**：props.as_params 与
# generation 都调 thickness_split()，避免"面板算 3.6、生成算 3.5"的分叉。
MOSAIC_BASE_MM = 0.40          # 全色平板：底板固定 0.40（色块层 = T − 0.40）
RELIEF_BASE_MM = 0.40          # 叠色浮雕：底板固定 0.40（起伏层 = T − 0.40）
CARD_BANDS_PER_SIDE = 6   # 双面：每面 6 层（回文 12 层，总厚 = 12×0.10 = 1.20mm）
# v1.0.6：4 → 6。用户要求"双面叠色由 4 层拓展为 6 层，最大化还原颜色"。
# 与"单面固定 8 层"同一个决策口径：这个模式的层数由还原度决定，不跟面板
# 滑杆走。⚠ 旧注释说"再加层零增益"是**不含白**时的结论 —— 那时白色永远
# 补在内侧，多出来的层只是同色重复得更暗、落在旧凸包里；白能排到外侧之后，
# 第 5/6 层才真正补出浅色与中间调（暖肤色最近锚点 ΔE 30.12 → 21.60，
# 浅粉 17.05 → 7.38，见 tests/out/_diag_white_outer.py）。

# ---------------------------------------------------------------------------
# 单面叠色卡（v1.0.2 / 新模式 "single"）
# ---------------------------------------------------------------------------
# 结构（从底往上）：
#     0.40mm 白底板  →  4×0.10mm 纯白隔离层  →  N×0.10mm 彩色叠色层（N = 每面层数）
# 反面 = 白底板本身（一整片白），所以成品翻过来是纯白，不印图。
#
# 【为什么中间要 4 层纯白】彩膜下面是"透光 + 散射"的，如果彩膜直接贴底板，
# 相邻格的颜色会沿板体横向串色，深色区还会把旁边的浅色"染灰"。4 层白
# （0.40mm）相当于一层光学隔离垫：它的散射贡献把颜色往白拉一点（呈色模型里
# 真实计算，不是忽略），但把横向污染挡在板体里。
#
# 【为什么不是双面镜像】双面卡的 8 层里，背面 4 层是**镜像的颜色**：那一半的
# 散射要把正面 4 层再穿一遍才到眼睛里，等于给颜色蒙了一层灰。单面结构里
# 8 个子层全在观察者这一侧，同样的叠法颜色更纯、更接近标定值。
SINGLE_BASE_MM = 0.40                       # 白底板 = 4 层 × 0.10mm（反面）
# 【v1.0.4】白隔离层从 4 层改成 0 层。原来 0.40 底板 + 4 层白隔离 = 8 层白，
# 比彩膜还厚一倍，切片软件里数出来就是「底部 8 层白」—— 用户实测直接指出
# 这不对：白色只需要 4 层（0.40mm）当底板兼隔离，多出来的 4 层纯属浪费厚度。
# 现在 0.40mm 底板本身就是那 4 层白，它在光学上照样挡横向串色。
SINGLE_WHITE_LAYERS = 0
SINGLE_WHITE_MM = SINGLE_WHITE_LAYERS * DEFAULT_BAND_MM
# 单面彩膜层数：**固定 8 层，不跟面板滑杆走**（用户原话「不考虑叠色多少层，
# 最大程度还原照片颜色」）。实测（4112 色 CIE76，整块上色）4/6/8/10 层的
# 平均 ΔE = 34.77 / 33.14 / 32.81 / 32.73 —— 4→6 收益 4.7%、6→8 再 0.9%、
# 8→10 只有 0.2%，而每加 2 层厚 0.20mm。8 层是收益拐点。
SINGLE_BANDS = 8

# 【v1.0.5】白色层参与「稀释」：允许白膜出现在**彩膜外侧**。
#
# 根因（用户 2026-11-14 反馈「站在龙身上的人脸发紫发黑」）：
#   锚点构造把白排除在可叠层之外（``layers = [k for k in keys if k != "W"]``），
#   8 层永远是「彩色在前、白补齐在后（最内层）」。于是 165 个锚点的色域在 Lab
#   里是一层**高饱和壳**：C* < 25 的锚点只有 1 个（纯白），色相集中在 237~282°
#   （蓝紫）与 100°（黄）。真实肤色 (191,153,140) = L*66 C*17 h45° 落在这层壳
#   里面，最近邻只能选到 MYYYY 粉红（ΔE 38.4）甚至 CY 青色（ΔE 39.5）—— 那就
#   是「人脸发紫发黑」。
#
# 放开的依据（不是拍脑袋）：白膜本身有 t<1，盖在彩膜外侧就会把颜色冲淡。
#   生产模型实测「1 层白盖黄」(255,247,132) → 「3 层白盖黄」(255,247,168)，
#   而现状「黄在外」(255,247,102)。把白放进锚点后 C*<25 的低饱和锚点从 1 个
#   涨到 288 个，肤色 ΔE 38.4 → 20.7。
SINGLE_WHITE_AS_LAYER = True

# 【v1.36.0 / R43 → v1.37.0 / R44】双面卡的「图案皮」层数：每面最外保留几层原色。
#
# 为什么必须有这个常量：兼容模式（``mix_model="surface"``）下每格的 8 层是
# **同一卷耗材**（``(k,)*bands``），于是每种颜色的柱体从顶面一路贯穿到底面。
# 实测用户导出的 1.3mf（0.80mm / 8×0.1mm）：内部分界点 64,426 个，其中
# **24,962 个（38.7%）在全部 8 层都是分界**，按 0.167mm 格距折合约 **4.16 m**
# 贯穿弱线；整块板没有任何一层是整片同色的 —— 沿色块边界一掰就裂（用户
# 实拍反馈"颜色分层处没有一层是相连的"）。
#
# R43 先把芯层填成整片基底色：缝确实断了，但用户实拍判定"仍有较大风险"——
# 图案只剩每面 0.2mm 薄皮、两片皮之间夹白，剖面依旧是一条直墙；侧面还多出
# 0.4mm 一圈白带，观感上"精细度下降"。
# R44 改成**错缝咬合**（``generation.interlock_core_bands``）：皮一格不动
# （顶/底逐格材质不变，外观零影响），芯层分两段朝相反对角各错一格，色界在
# 板厚中间错一个台阶。实测同一张图同一参数：贯穿（同一位置且两侧颜色都相同
# 的分界）64,574 → **475（0.3%）**，三角 +16%，换色不变，水密 0 坏边。
# 上限仍取 2：皮再厚就只是多耗彩料、芯更薄、错台更少。
CARD_SKIN_LAYERS = 2
DEFAULT_MAX_THICKNESS_MM = 3.6
MIN_MAX_THICKNESS_MM = 0.5
MAX_MAX_THICKNESS_MM = 12.0

# ---- 打印代价（v1.25.0）----
# 用户的原话是"每个参数要能一眼看出代价（换色次数／耗材卷数／打印时间）"。
# 耗材克数是**确定性**的：几何是实心封闭壳，切片器 100% 填充，所以
#   grams = 体积(mm³) × PLA_DENSITY_G_CM3 / 1000
# 实测对账：300px/200mm/2.4mm → 模型 63.9 g，V×1.24 = 64.4 g（差 0.8%）；
# 420px/200mm/2.4mm → 63.3 g。不含擦料塔与裙边（那部分由切片器另算）。
PLA_DENSITY_G_CM3 = 1.24

# 打印时间的**量级参考**（不是承诺）。本机 Bambu A1 + 0.1mm 层高实测：
#   63.9 g → 643.6 分钟 = 10.1 min/g；63.3 g → 714 分钟 = 11.3 min/g。
# 取 10.5 作中值。实际取决于层高／速度档／机器／擦料塔，面板文案必须写"参考"。
PRINT_MINUTES_PER_GRAM = 10.5

# 每次换色的擦料 + 擦料塔分摊（克）。**只算模型体积会低估约 30%** ——
# 用户按"48 g"买料，实际切片报 64 g。两个实测点反推：
#   300px：总 63.9 g − 模型 48.7 g = 15.2 g / 46 次 = 0.33 g/次
#   420px：总 63.3 g − 模型 48.6 g = 14.7 g / 41 次 = 0.36 g/次
# 取 0.35。它随 flush 体积倍数／颜色深浅变化，所以面板写"约"。
FLUSH_GRAMS_PER_SWAP = 0.35


def thickness_split(mode, total_mm, bands=DEFAULT_STACK_BANDS,
                    back_depth_mm=None):
    """把"模型最大厚度"按模式拆成各层厚度（唯一公式）。

    返回 ``{base_mm, relief_mm, band_mm, stack_bands, total_mm}``，两个模式
    还会多出 ``back_mm`` / ``engrave_mm``（其它模式恒为 0），
    ``total_mm`` 是**钳制后**的实际总厚（可能小于传入值，例如双面卡受
    单层 0.30mm 上限约束）。

    ⚠ ``postcard``：``total_mm`` 是**正面**厚度（色膜 + 底板 + 起伏），
    不是整件厚度 —— 反面板厚由 ``back_depth_mm`` 决定，见下面的注释。
    """
    t = float(total_mm or DEFAULT_MAX_THICKNESS_MM)
    t = max(MIN_MAX_THICKNESS_MM, min(MAX_MAX_THICKNESS_MM, t))
    if mode == "card":
        # v1.0.6：双面卡的层数是固定值，**不接受调用方传入的 bands** ——
        # 面板滑杆默认 4，若这里还听它，总厚会被算成 0.80 而不是 1.20。
        b = CARD_BANDS_PER_SIDE
        band = max(MIN_BAND_MM, min(MAX_BAND_MM, t / (2.0 * b)))
        total = band * 2.0 * b
        # ⚠ relief_mm 不能给 0：generate() 在 mode 分支**之前**先用
        # build_stack(relief_mm=...) 建一份色带表（报告/侧壁材质用），
        # 0 会抛"色带总厚度必须大于 0"。card 的真实几何由 band_mm 决定，
        # 这里给总厚只是让那条共用构造能跑通，不参与 card 的几何。
        return {"base_mm": 0.0, "relief_mm": total, "band_mm": band,
                "stack_bands": b, "total_mm": total,
                "back_mm": 0.0, "engrave_mm": 0.0}
    if mode == "single":
        # 单面叠色卡：0.40mm 白底板（=4 层 0.10，反面纯白）+ N 层彩膜。
        # v1.0.4 起 N 固定 8（见 SINGLE_BANDS 的实测注释），底板与彩膜厚度
        # 都由它决定，不再读面板滑杆 —— 用户要的是「不用管层数，颜色最准」。
        b = SINGLE_BANDS
        band = (t - SINGLE_BASE_MM - SINGLE_WHITE_MM) / float(b)
        band = max(MIN_BAND_MM, min(MAX_BAND_MM, band))
        total = SINGLE_BASE_MM + SINGLE_WHITE_MM + band * b
        return {"base_mm": SINGLE_BASE_MM, "relief_mm": SINGLE_WHITE_MM + band * b,
                "band_mm": band, "stack_bands": b, "total_mm": total,
                "back_mm": 0.0, "engrave_mm": 0.0}
    if mode == "postcard":
        # 正面：**没有白底板**（v1.0.12）—— 反面那块白板就是它的底板。
        # 整段厚度全部用来放「2×bands 片色膜 + 用户要的起伏层数」。
        base = 0.0
        relief = min(MAX_RELIEF_MM, max(MIN_RELIEF_MM, t - base))
        front = base + relief
        # 反面：深度 = 用户给的最大凹刻深度；板厚 = 深度 + 安全白层。
        # 凹刻最深的那一格，离正面起伏层底还有 POST_BACK_MARGIN_MM 实心白，
        # 所以凹槽再深也碰不到正面图案。
        d = POST_ENGRAVE_MM if back_depth_mm is None else float(back_depth_mm)
        d = max(POST_ENGRAVE_MIN_MM, min(POST_ENGRAVE_MAX_MM, d))
        back = d + POST_BACK_MARGIN_MM
        return {"base_mm": base, "relief_mm": relief, "band_mm": 0.0,
                "stack_bands": 0, "total_mm": front + back,
                "front_mm": front, "back_mm": back, "engrave_mm": d}
    if mode == "relief":
        base, floor_mm = RELIEF_BASE_MM, MIN_RELIEF_MM
    else:  # mosaic 及其它
        base, floor_mm = MOSAIC_BASE_MM, MIN_RELIEF_MM
    relief = max(floor_mm, t - base)
    relief = min(MAX_RELIEF_MM, relief)
    return {"base_mm": base, "relief_mm": relief, "band_mm": 0.0,
            "stack_bands": 0, "total_mm": base + relief,
            "back_mm": 0.0, "engrave_mm": 0.0}


# ---------------------------------------------------------------------------
# 自动厚度（v1.28.0 / R35：三模式统一固定值；v1.0.11 改浮雕/明信片）
# ---------------------------------------------------------------------------
# 用户口径：不再按幅面缩放，直接给固定总厚（单位 = 0.10mm 的打印层）。
#   * 全色平板 = **2.00mm**（0.40 底板 + 1.60 色块层）
#       颜色与厚度无关（一块就是一卷料），厚度只买刚度；2mm 足够平挺。
#   * 叠色浮雕 = **2.80mm = 4 层白底板 0.40 + 24 层颜色层 2.40**（v1.0.13）
#       用户 2026-11-18 口径："24 层是色膜 + 起伏一共 24 层"。
#       24 层颜色层 = 柱顶 2×bands 片色膜（6 层/面 → 12 片 1.20mm）
#                   + 能上下起伏的 12 层（1.20mm）。
#       v1.0.12 把它读成"24 层真起伏（色膜另算）"，总量变成 36 层 4.00mm ——
#       本轮用户明确否定。可见落差 = 24 − 12 = **1.20mm**（不是 2.40）。
#   * 双面平板叠色 = **1.20mm**（每面 6 片 ×0.10；v1.0.6 由 0.80 / 4 片加厚）
#       ⚠ 呈色模型不含膜厚，12 层用的是同一套参数外推（实拍板只有 4 层/面）。
#       唯一落在 Palette_256 实拍标定域内的厚度；R33 实测 0.8/1.2/2.4/3.6
#       四档的预测 dE 逐位相同（呈色模型不含膜厚），打更厚实物会比预览暗。
AUTO_CARD_FILM_MM = 0.10
AUTO_MOSAIC_TOTAL_MM = 2.00
AUTO_RELIEF_TOTAL_MM = 2.80

# ---------------------------------------------------------------------------
# 浮雕明信片（v1.44.0 / R54）
# ---------------------------------------------------------------------------
# 结构（一体件、不用胶）：
#   正面：0.40 底板 + 起伏层 + 柱顶 4×0.1 色膜  ← 与 relief 完全同一条链路
#   反面：一块独立底板，底面按"反面图片"的明暗凹进去
#
# 【为什么反面必须单独一块板，不能把 relief 翻过来刻】
# relief 的背面是正面颜色的 z 镜像（回文 8 层）—— 那正是叠色卡的做法。
# 明信片的反面是一张白纸上的文字/邮戳/二维码，与正面图案无关，所以必须
# 另起一块**单色**板；凹刻碰不到正面，靠的是板厚里留出的安全白层。
# 明信片整件 = **28 层 2.80mm**（v1.0.13 用户口径）：
#   反面 10 层白（1.00mm = 凹刻 0.40 + 安全白 0.60）
#   正面 18 层（1.80mm = 柱顶 12 片色膜 1.20 + **6 层真起伏** 0.60）
# 用户 2026-11-18："18 层是色膜 + 起伏一共 18 层"。v1.0.12 读成"18 层真起伏
# （色膜另算）"→ 正面 3.00、整件 4.00，本轮否定。
# 【正面为什么没有 0.40 白底板】用户 2026-11-17 指出：反面本来就是一块白板，
# 正面再垫 4 层白属于重复。`thickness_split("postcard")` 的 base_mm = 0，
# 色膜直接坐在反面白板的顶面上。⚠ 面板/日志里的"整件"仍是 正面 + 反面。
POST_FRONT_MM = 1.80          # 正面叠色浮雕总厚（无白底板；12 片色膜 + 6 层起伏）
POST_FRONT_MIN_MM = 1.20      # 至少要放得下柱顶 2×bands 片色膜（见 postcard_min_front_mm）
POST_FRONT_MAX_MM = 5.00
POST_ENGRAVE_MM = 0.40        # 反面最大凹刻深度
POST_ENGRAVE_MIN_MM = 0.20
POST_ENGRAVE_MAX_MM = 0.60
# 凹刻最深处到"正面起伏谷底"之间必须留的实心白层。0.60mm = 0.4 喷嘴打
# 0.20 层高时的 3 层，太薄会在凹槽底部透出正面的色膜（实拍会看到脏点）。
POST_BACK_MARGIN_MM = 0.60
# 凹刻深度的量化步长（几何用）。切片层高通常 0.10/0.20，0.05 已经比层高细，
# 再细只会让台阶立面碎成大量薄片 —— 而且会让相邻格的台阶在格点竖棱上错位
# 产生 T 型接点（见 mesh_builder.build_engraved_back 的注释）。
POST_ENGRAVE_LEVEL_MM = 0.05

# 浮雕明信片：「没上色的格子」比「有色的格子」低几层（v1.45.0 / R56）。
# 用户 2026-11-04 的要求：彩色区相对白色区高 2 层（0.2mm），等价于白色部分
# 下沉两层。只降**空叠**格（一片色膜都没有的纯白）—— 有 1 片膜的浅色格也降
# 的话，浅色区会被打成一片 0.2mm 的麻点。
POST_WHITE_SINK_LAYERS = 2
POST_WHITE_SINK_MM = POST_WHITE_SINK_LAYERS * 0.10

# 四周向外加一圈**底板色实心边框**（v1.45.1 / R57 明信片 → v1.0.13 全模式）。
# 用户 2026-11-05 的要求："四周的侧壁墙没有实现"。改前四面其实都有面
# （水密 0 破洞），但侧面是画面自己的颜色、随起伏高低 —— 看着不像墙。
# 现在向外长一圈 rim，顶面与模型最高处齐平、底板色，四边就是竖直平墙。
# 用户 2026-11-18："墙层数需要应用于所有模式" → 平板 / 浮雕 / 双面卡 /
# 单面 / 明信片五种模式统一加这一圈（之前只有明信片有）。
# 用户 2026-11-20：改「外框层数」，**1 层 = 0.40mm**（一个打印层高），
# 不再一刀切 1.5mm；0 层 = 不加外框。
# 代价：成品外形尺寸 = 最长边 + 2×层数×0.40（画面本身不动，已确认接受）。
RIM_LAYER_MM = 0.40             # 外框每层厚度 = 一个 0.10mm×4 的实心块
MODEL_RIM_LAYERS_DEFAULT = 1    # 默认 1 层 = 0.40mm
MODEL_RIM_MAX_LAYERS = 5        # 上限 5 层 = 2.00mm
# 兼容旧名（v1.0.13 之前是"固定 1.5mm"）：现在表示**默认**外框宽度。
MODEL_RIM_MM = RIM_LAYER_MM * MODEL_RIM_LAYERS_DEFAULT
POST_RIM_MM = MODEL_RIM_MM      # 旧名保留：明信片专用分支仍在用


def rim_mm_for_layers(layers=None):
    """外框层数 → 实际毫米。0 层返回 0（不加外框）。"""
    try:
        n = MODEL_RIM_LAYERS_DEFAULT if layers is None else int(layers)
    except (TypeError, ValueError):
        n = MODEL_RIM_LAYERS_DEFAULT
    n = max(0, min(MODEL_RIM_MAX_LAYERS, n))
    return float(n) * RIM_LAYER_MM

# 边框内侧那 1 格画面也抬到最高处（否则边框与画面之间会出现一圈
# 1.5mm 宽、最深处 1mm 的沟）。抬起只改高度、不改颜色。
POST_RIM_LIP_CELLS = 1


def relief_min_front_mm(bands=DEFAULT_STACK_BANDS):
    """浮雕正面**建得出来**的最薄厚度（v1.45.0）。

    = 底板 0.40 + 柱顶 2×bands 片色膜（6 层/面 → 12 片 = 1.20）→ 1.60mm。
    低于这个值几何会被 ``z_grid`` 的下限钳回去 —— 滑杆能设更小，但实际只能
    建到 1.60mm。
    面板必须把这个"有效下限"说出来，不能让用户以为设了 0.8 就是 0.8。
    """
    b = int(bands or DEFAULT_STACK_BANDS)
    b = max(MIN_STACK_BANDS, min(MAX_STACK_BANDS, b))
    return round(RELIEF_BASE_MM + 2 * b * RELIEF_LAYER_MM, 6)


def postcard_min_front_mm(bands=DEFAULT_STACK_BANDS):
    """明信片正面**建得出来**的最薄厚度（v1.0.12）。

    = 柱顶 2×bands 片色膜（12 片 = 1.20mm），**没有白底板**（反面那块白板
    就是底板，见 POST_FRONT_MM 的注释）。
    """
    b = int(bands or DEFAULT_STACK_BANDS)
    b = max(MIN_STACK_BANDS, min(MAX_STACK_BANDS, b))
    return round(2 * b * RELIEF_LAYER_MM, 6)


def engrave_z(depth_mm, level_mm=POST_ENGRAVE_LEVEL_MM, top_mm=None):
    """把「反面图片算出的深度」换成**几何上真正用的底面 z**（唯一实现处）。

    v1.44.0 / R54：这个量化以前散在三处（建几何、统计刻了几格、探针校验），
    口径一旦不同就会出现"报告 274 格、几何只有 264 格"这种对不上的数字。
    现在三处都调这里：

    * ``mesh_builder.build_engraved_back`` 的 ``zb()`` —— 建面用；
    * ``generation.generate`` 的 ``engraved_px`` —— 面板显示用；
    * ``tests/t_probe_r54.py`` —— 校验用。

    ``top_mm`` 给定时再钳一道上限 ``top − 步长``：凹刻台阶不能顶到板顶面，
    否则那一格会被挖成通孔（板厚归零）。``top_mm=None`` 时不做这道钳制，
    只在统计/校验里用。
    """
    step = max(1e-4, float(level_mm or POST_ENGRAVE_LEVEL_MM))
    d = max(0.0, float(depth_mm))
    d = round(round(d / step) * step, 6)
    if top_mm is not None:
        d = min(d, max(0.0, float(top_mm) - step))
    return d


def auto_thickness_total(mode, long_edge_mm=None, bands=DEFAULT_STACK_BANDS):
    """三种模式的固定总厚（v1.28.0 / R35）+ 明信片的正面厚度（v1.44.0）。

    ``long_edge_mm`` 只为兼容旧调用方保留，**不参与计算** —— R34 曾按幅面
    缩放（0.8% / 2.2%），用户明确要统一值，遂收回成常量。
    返回值直接喂 :func:`thickness_split`，仍是唯一一条厚度链路。

    ⚠ ``postcard`` 返回的是**正面厚度**（不含反面底板）—— 反面的厚度由
    凹刻深度决定，是用户可调的第二个参数，不能塞进同一个标量里。
    """
    if mode == "card":
        # v1.0.6：同上 —— 层数固定 6，总厚恒为 2×6×0.10 = 1.20mm。
        b = CARD_BANDS_PER_SIDE
        total = 2.0 * b * AUTO_CARD_FILM_MM
    elif mode == "single":
        total = SINGLE_BASE_MM + SINGLE_WHITE_MM + SINGLE_BANDS * AUTO_CARD_FILM_MM
    elif mode == "relief":
        total = AUTO_RELIEF_TOTAL_MM
    elif mode == "postcard":
        total = POST_FRONT_MM
    else:
        total = AUTO_MOSAIC_TOTAL_MM
    return max(MIN_MAX_THICKNESS_MM, min(MAX_MAX_THICKNESS_MM, total))


# 默认高度映射方式。region = 分区配色（色相逐像素独立、还原度最高）
DEFAULT_HEIGHT_MODE = "region"
#
# 【为什么只有三种，没有第四种】v1.3 曾有一项 ``hybrid``（混合过渡），实测后被删除：
#   h = 色相位置 × (1 - w) + 亮度 × w，而滑块默认值 w = 1 → 退化成 h = 亮度，
#   与 ``luminance`` 逐像素完全相同（实测：色相偏差 / RGB 色差 / 换色次数 /
#   高度取值个数 / 顶面材质分布 五项全等）。把 w 从 1.0 扫到 0.0，色相偏差
#   反而从 32.4° 涨到 60.2° —— 它在任何滑块位置都不优于纯亮度，被全面支配。
#   证据：tests/t_probe_modes.py
HEIGHT_MODES = ("region", "coloraware", "luminance")

# ---------------------------------------------------------------------------
# 3MF 相关
# ---------------------------------------------------------------------------
THREEMF_CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
THREEMF_MATERIAL_NS = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"
BAMBU_NS = "http://schemas.bambulab.com/package/2021"
THREEMF_UNIT = "millimeter"

# ---------------------------------------------------------------------------
# 挂绳孔（v1.15.0 后处理）
# ---------------------------------------------------------------------------
# 位置固定在照片顶部居中（模型顶边中点）。外置=圆环凸出顶边外并与模型布尔并，
# 且环底与模型底面（z 最小）齐平 —— 摆放/打印时挂耳贴着底面，不悬空；
# 内置=模型内打通一个竖直圆孔（布尔差）。v1.16.3 起 relief/card/single 三种
# 模式都支持（v1.22.0 起 single 由 mosaic 取代，同样支持；只能经「生成挂绳孔」
# 按钮在已生成模型上添加）。
LANYARD_INNER_R_MM = 1.5      # 内孔半径（默认，可被面板参数覆盖）
LANYARD_OUTER_R_MM = 2.5      # 外环半径（默认，可被面板参数覆盖）
LANYARD_DEFAULT_THICKNESS_MM = 3.0  # 挂绳孔特征轴向厚度默认值（外置环高度；内置孔始终贯通）
LANYARD_EDGE_MARGIN_MM = 0.6  # 内置孔心在「顶边-外环半径」基础上再往下沉的余量
                              # （保证孔壁到模型顶边 ≥ 1.1mm 实料）
LANYARD_SEGMENTS = 48         # 圆环/圆孔的圆周分段数
LANYARD_THICKNESS_MIN_MM = 1.0 # 厚度下限（太薄环/孔不结实）
# 外环壁厚下限（v1.48.0 / R65）。外环半径必须比内孔半径大至少这么多，否则
# _ring_mesh 会按「外圈比内圈小」建面 —— 四条边全部反向外翻（自相交的蝴蝶结），
# 布尔/追加后得到被自己堵死的实心饼，或者一堆零面积面。实测（40×30×2 方块）：
#   内 6.0 / 外 1.0 → 孔不贯通，体积反而从 2425 涨到 2619 mm³；
#   内 2.5 / 外 2.5 → 孔通了，但多出 96 个零面积面（切片软件闪退的元凶）。
# 0.4mm = 两条 0.2 喷嘴挤出线，是能打出来又不至于太脆的最小壁厚。
LANYARD_MIN_WALL_MM = 0.4

# ---------------------------------------------------------------------------
# 水密性 / 退化面判定阈值（与 blender-addon-headless-verify 的实测口径一致）
# ---------------------------------------------------------------------------
EPS_AREA2 = 1e-10        # 零面积面判定（area^2）
EPS_WELD = 1e-5          # 顶点合并距离（mm）
EPS_NUDGE = 2.0e-3       # 退化面微扰位移


def srgb_to_linear(c):
    """sRGB → linear。Blender 材质 base_color 与 3MF 顶点色走的是 linear。"""
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def linear_to_srgb(c):
    """linear → sRGB。"""
    if c <= 0.0031308:
        return c * 12.92
    return 1.055 * (c ** (1.0 / 2.4)) - 0.055


def srgb_tuple_to_linear(rgb):
    return tuple(srgb_to_linear(v) for v in rgb)


def srgb_tuple_to_hex(rgb):
    """sRGB 0..1 → '#RRGGBBAA'（3MF displaycolor 用，8 位十六进制）。"""
    parts = []
    for v in rgb:
        parts.append("%02X" % max(0, min(255, int(round(v * 255.0)))))
    parts.append("FF")
    return "#" + "".join(parts)
