# -*- coding: utf-8 -*-
"""i18n —— 插件界面双语（v1.15.0）。

【机制】源码内所有用户可见文案保持**简体中文原文**，本模块维护一张
中文 → 英文映射表；``t(原文)`` 在英文模式下返回译文、中文模式下原样返回。
语言选择（props.language 的 update 回调）改写本模块的 ``LANG`` 全局，并经
``bpy.app.timers`` 延迟调用 ``retranslate_ui()`` 重注册面板/算子 —— 让
``bl_label`` / ``bl_category`` 这类"注册期"文案跟着切换。

【为什么用自建映射而不是 bpy.app.translations】Blender 官方翻译机制只在
「Blender 界面语言 ≠ English」时才查表 —— 我们要的是**插件内部下拉开关**
（与 Blender 全局语言无关），官方机制做不到。

【范围（v1.15.0）】面板标题/分区/标签/按钮、算子按钮名与全部反馈消息、
质量报告、弹窗、枚举下拉项的**名称**全部双语；PropertyGroup 的属性
description（悬停提示）与枚举项的长描述暂仍为中文原文 —— 切语言需要重注册
PropertyGroup，会清空用户已调的参数值，权衡后下一版再处理（README 已注明）。
"""

LANG_DEFAULT = "zh_CN"
LANG = LANG_DEFAULT          # 当前语言："zh_CN" | "en_US"

# 面板/算子重注册的延迟执行标记（防止同一帧重复排队）
_TIMER_PENDING = {"v": False}


# ---------------------------------------------------------------------------
# 中文 → 英文映射表
# 键 = 源码里的中文原文（含格式化模板，%s/%d 等占位符两侧文案原样对应）。
# ---------------------------------------------------------------------------
_ZH2EN = {
    # ---- 面板骨架 ----
    "叠色浮雕": "ColorStack",
    "语言选择": "Language",
    "第 1 步 · 选择图片": "Step 1 · Choose Image",
    "第 1 步 · 导入照片": "Step 1 · Import Photo",
    "第 2 步 · 参数设置": "Step 2 · Settings",
    "参数与质量": "Parameters & Quality",
    "模型与尺寸": "Model & Size",
    "混色与配色": "Mixing & Colors",
    "配色与高度": "Colors & Height",
    # 【v1.16.1】"Post-Processing" 会被 Blender 自带 zh_HANS 词典二次翻译成
    # "后期处理"，改用惰性表述 "Post-Process"（探针实测不在词典里）。
    "后处理": "Post-Process",
    "第 3 步 · 后期处理": "Step 3 · Post-Process",
    "第 3 步 · 导出设置": "Step 3 · Export",
    "第 4 步 · 导出": "Step 4 · Export",
    # ---- v1.44.0 明信片浮雕（R54）----
    "明信片浮雕": "Postcard Relief",
    "正面叠色浮雕厚度 (mm)": "Front Relief Thickness (mm)",
    "反面凹刻深度 (mm)": "Back Engraving Depth (mm)",
    "反面图片": "Back Image",
    "反面图片文件": "Back Image File",
    "载入反面图片": "Load Back Image",
    "载入反面图片失败": "Failed to load back image",
    "请先选择一张图片文件。": "Choose an image file first.",
    "已载入反面图 %s": "Loaded back image %s",
    "已载入反面图片：%s（%d × %d）": "Loaded back image: %s (%d × %d)",
    "厚度（正反分开设）": "Thickness (front / back)",
    "正面叠色浮雕厚度": "Front relief thickness",
    "反面凹刻深度": "Back engraving depth",
    "正面 %.2f ＋ 反面背板 %.2f ｜ 总厚 %.2f mm":
        "Front %.2f + back plate %.2f | total %.2f mm",
    "凹刻最深 %.2f mm，槽底仍留 %.2f mm 实心白层（碰不到正面）":
        "Engraving up to %.2f mm, leaving %.2f mm of solid white underneath",
    "四周各加 %.1f mm 边框（底板色），成品最长边 %.1f mm":
        "%.1f mm base-color rim on each side; final long edge %.1f mm",
    "没上色的地方比彩色低 %.1f mm（%d 层），彩色区自然凸出":
        "Unpainted areas sit %.1f mm (%d layers) lower, so color pops out",
    "⚠ 低于 %.2f mm 建不出来（柱顶要放 %d 片 0.1mm 色膜），实际会按 %.2f mm 建":
        "Below %.2f mm cannot be built (needs %d x 0.1 mm films on top); "
        "it will be built at %.2f mm",
    "反面图案": "Back Artwork",
    # ---- v1.45.2 反面图左右镜像（R58）----
    "反面图左右镜像": "Mirror Back Image",
    "左右镜像（中轴线翻转）": "Mirror left/right",
    "把反面图片绕竖直中轴线左右翻转后再凹刻。"
    "字是反的、或想和正面图案左右对称时打开":
        "Flips the back image about its vertical center axis before engraving. "
        "Use it when the text comes out mirrored or you want the back to "
        "mirror the front",
    "未选：反面是平的（只打背板，不刻图案）":
        "Not set: back stays flat (plate only)",
    "反面底板色（不参与叠色）": "Back Plate Color (no stacking)",
    "白色：反面就是白纸，文字凹刻最干净":
        "White: the back reads as paper and engraving looks cleanest",
    "%s：反面与四周侧壁都会是这个颜色，需要多备一卷这种料":
        "%s: back and all side walls take this color; one more spool needed",
    "红色": "Red",
    "蓝色": "Blue",
    "明信片浮雕：正面叠色浮雕（明暗→起伏、柱顶混色），反面单色凹刻":
        "Relief postcard: stacked relief on the front, single-color engraved back",
    "正反两种厚度在下面分开设；反面图案另选一张图":
        "Front and back thickness are set separately below; pick a second image for the back",
    "反面另有一块单色背板，按反面图凹刻":
        "A single-color back plate is engraved from the back image",
    "凹刻不碰正面：槽底固定留 0.60mm 白层":
        "Engraving never reaches the front: 0.60 mm of white is kept",
    # ---- v1.23.0 新参数 ----
    "模型精细度": "Model Detail",
    "模型最大厚度 (mm)": "Max Thickness (mm)",
    "混色色组": "Color Group",
    "混色色组（要买几卷耗材）": "Color Group (spools needed)",
    # ---- v1.25.0 打印代价 ----
    "上一版用料：模型 %.1f g ＋ 换色擦料约 %.0f g ＝ 约 %.0f g":
        "Last build: model %.1f g + ~%.0f g flush = ~%.0f g",
    "参考打印时间约 %.1f 小时（0.1mm 层高、常规速度）":
        "Est. print time ~%.1f h (0.1 mm layers, normal speed)",
    "参考打印时间约 %d 分钟（0.1mm 层高、常规速度）":
        "Est. print time ~%d min (0.1 mm layers, normal speed)",
    "%.1f cm³ · 面数 %s · 换色 %d 次":
        "%.1f cm³ · %s triangles · %d color changes",
    # ---- v1.25.0 颜色还原度 ----
    "颜色还原度（按实拍标定模型预测）：":
        "Color accuracy (predicted by the calibrated model):",
    "   整体：%s（ΔE 中位 %.1f）": "   Overall: %s (median ΔE %.1f)",
    "   暗部（占画面 %.0f%%）：%s（ΔE 中位 %.1f）":
        "   Dark areas (%.0f%% of frame): %s (median ΔE %.1f)",
    "   暗部偏色是四色叠色的物理上限：四卷料叠到底也到不了纯黑，深色会偏紫。":
        "   Dark color shift is a physical limit of 4-color stacking: four "
        "spools never reach pure black; dark areas turn purple.",
    "   要更深的黑得加黑色耗材（8色MAX 组），但那一组还没有实拍标定数据。":
        "   Deeper blacks need a black spool (8-color MAX group), which has "
        "no physical calibration yet.",
    "很像": "very close",
    "接近，细看有差": "close; differs on close look",
    "一眼能看出不同": "clearly different at a glance",
    "明显不像": "far off",
    "上一版模型的耗材重量（按实心体积 × 1.24 g/cm³ 算，不含擦料塔）":
        "Filament weight of the last build (solid volume × 1.24 g/cm³, "
        "excludes prime tower)",
    "上一版模型的三角面数（越多切片越慢）":
        "Triangle count of the last build (more = slower slicing)",
    "上一版模型预计的换色次数（每次换色要擦料，约多花 1 分钟）":
        "Estimated color changes of the last build (each flush costs ~1 min)",
    "上一版模型的实体体积": "Solid volume of the last build",
    "全色平板": "Full-color Flat",
    "双面平板": "Dual-sided Flat",
    "单面平板": "Single-sided Flat",
    "单面平板：正面叠 %d 层彩膜，下面是 0.40mm 白底板（正好 4 层白），反面纯白":
        "Single-sided stack: %d colour films on the front, a 0.40mm white "
        "base (exactly four white layers) underneath, plain white back",
    "层数固定，不用你算；每格只叠一种组合，不撒彩点":
        "Layer count is fixed; one stack per cell, no colour speckles",
    "白底板 4×0.10 + 彩膜 %d×%.2f ｜ 总厚 %.2f mm（自动）":
        "White base 4x0.10 + films %d x %.2f | total %.2f mm (auto)",
    "反面是白底板、不能印图；4 层白挡颜色横向串到隔壁格":
        "The back is the white base and cannot carry an image; the four "
        "white layers block horizontal colour bleed between cells",
    "混色模型：固定「透光叠色 + 网点混色」":
        "Mixing model: fixed to translucent stacking + dithered mixing",
    "颜色叠得深才有还原度 —— 本模式不吃下面两个选项":
        "Deeper stacking is what gives fidelity; the two options below do "
        "not apply to this mode",
    "混色模型：固定「透光叠色 + 整块上色」":
        "Mixing model: fixed to translucent stacking + solid blocks",
    "每个位置只叠一种组合，不撒彩色点 —— 本模式不吃下面两个选项":
        "One stack per position, no colour speckles; the two options below "
        "do not apply to this mode",
    "青色": "Cyan",
    "红色": "Red",
    "绿色": "Green",
    "蓝色": "Blue",
    "黑色": "Black",
    "CMYK 四色": "CMYK (4)",
    "RYBW 四色": "RYBW (4)",
    "CMYWGK 六色": "CMYWGK (6)",
    "8 色 MAX": "8-color MAX",
    "⚠ 运行的仍是旧版 v%s！": "⚠ Still running old v%s!",
    "请完全退出并重启 Blender，新版本才生效":
        "Please fully quit and restart Blender for the new version to apply",

    # ---- 第 1 步 ----
    "当前：%s（%d × %d）": "Current: %s (%d × %d)",
    "当前：%s": "Current: %s",
    "尚未选择图片": "No image selected",
    "载入图片": "Load Image",

    # ---- 参数与质量 ----
    "阶梯浮雕：明暗变成高低起伏": "Layered relief: brightness becomes height",
    "双面叠色：前后各叠几层，两面同图":
        "Dual-side card: stacked layers on both faces, same image",
    "平整色块板：照片聚成 N 个色块，逐块直接上色":
        "Flat color blocks: photo clustered into N blocks, each printed directly",
    "底板 %.2f + 色块层 %.2f ｜ 总厚 %.2f mm":
        "Base %.2f + color layer %.2f | total %.2f mm",
    "需 %d 卷彩色耗材（+ 底板色）｜ 换色约 %d 次":
        "Needs %d color spools (+ base color) | ~%d filament changes",
    "颜色所见即所得：预览就是实物，不做混色推算":
        "What you see is what prints: no color-mixing model involved",
    "最小色块 = 0：碎色斑会原样保留，小斑点可能被切片器过滤掉":
        "Min block = 0: tiny patches are kept as-is and may be filtered out"
        " by the slicer",
    "色块清理": "Block Cleanup",
    "色块板不做抖动混色：颜料的分配就是最终颜色":
        "Flat color blocks use no dithering: the assigned spool IS the color",
    "质量等级与颗粒图案对它无效":
        "Quality level and dither pattern do not apply",
    "颜色来源": "Color Source",
    "画面自动聚成 N 个色块（见上方「色块数」）":
        "The image is clustered into N blocks (see \"Color Blocks\" above)",
    "每个色块用一卷对应颜色的耗材平打，不做混色推算":
        "Each block is printed flat with one matching spool; no mixing model",
    "底板色": "Base Color",
    "分析色块与全色域": "Analyze Blocks & Gamut",
    "顶面全平（等高），一个色块就是一块材质区域":
        "Flat top (uniform height): one block = one material region",
    "预览图上的颜色就是实物会打出来的颜色":
        "Preview colors are exactly what will print",
    "色块数 = 耗材卷数 = 换色次数量级":
        "Color blocks = spools = order of magnitude of filament changes",
    "碎色斑由「最小色块」合并，避免切片器丢色":
        "\"Min Block\" merges tiny patches so the slicer keeps them",
    "已达上限：再加层只加厚、不会多出颜色":
        "At the cap: more layers only add thickness, not colors",
    "每面 %d 层 ｜ 总厚 %.2f mm（双面共 %d 层）":
        "%d layer(s) per side | total %.2f mm (%d layers both sides)",
    "≈ %s 个像素柱": "≈ %s pixel pillars",
    "尚未生成": "Nothing generated yet",
    "分辨率越高，细节越高；生成模型时耗费更多的时间和系统内存":
        "Higher resolution means finer detail, but generation takes more time "
        "and system memory",
    "柱数偏多：整链路面数约 %s 万":
        "High pillar count: total faces ≈ %s × 10k",
    "生成会明显变慢并占用大量内存。":
        "Generation will be much slower and memory-heavy.",
    "确实需要就继续；否则点上面的喷嘴按钮减柱。":
        "Continue if you really need it; otherwise use the nozzle buttons above.",
    "选择图片后显示预估柱数": "Pick an image to see the estimated pillar count",
    "最近邻：细线/纯色块保得住，照片会有边缘毛刺":
        "Nearest: keeps thin lines / solid patches; photos get edge jaggies",
    "面积平均：一格取整块平均，接近人眼观感":
        "Area average: per-cell mean, close to what the eye sees",
    "实际柱宽 %.2f mm（%.2f 个 %s 喷嘴宽）":
        "Pillar width %.2f mm (%.2f × %s-nozzle widths)",
    "0.2 喷嘴": "0.2 Nozzle",
    "0.4 喷嘴": "0.4 Nozzle",
    "提醒按 %s 喷嘴线宽 %.2f mm 计（Ctrl+点数值可手输）":
        "Warnings use %s-nozzle line width %.2f mm (Ctrl+click to type)",
    "柱宽 %.2f mm，细于 %s 喷嘴线宽 %.2f mm":
        "Pillar %.2f mm is thinner than %s-nozzle width %.2f mm",
    "切片器会糊掉相邻柱子，色点打不出来，":
        "the slicer will smear adjacent pillars and color dots won't print;",
    "点上面的「%s 喷嘴」即可调到 %.2f mm":
        "click \"%s Nozzle\" above to set %.2f mm",
    "略细于线宽，可点「%s 喷嘴」省网格":
        "Slightly thin: \"%s Nozzle\" can save mesh",
    "≈ 一条挤出线宽，色点打得出":
        "≈ one extrusion line wide; dots print fine",
    "柱子较粗：颗粒大但打得实，数字更大则更细腻":
        "Thick pillars: chunky but solid; larger numbers = finer grain",
    "质量等级": "Quality Level",
    "混色模型": "Color Model",
    # ---- v1.30.0 上色方式 ----
    "上色方式": "Color Placement",
    "整块上色（推荐）": "Solid blocks (recommended)",
    "网点混色": "Halftone dithering",
    "整块上色 ｜ 小于 %.1f mm 的碎色块并入邻近颜色":
        "Solid blocks | specks under %.1f mm merged into neighbours",
    # ---- v1.31.0 色块大小（三档真正分开）----
    "整块上色 ｜ 每 %d 格并成一个色块，小于 %.1fmm 的碎块并入邻近颜色":
        "Solid blocks | every %d cells become one block, specks under %.1fmm merged",
    "整块上色 ｜ 每格单独上色，小于 %.1fmm 的碎块并入邻近颜色":
        "Solid blocks | one colour per cell, specks under %.1fmm merged",
    "生成后视口显示的是打出来的颜色（不是四卷耗材的本色）":
        "After generating, the viewport shows the printed colour, not the four "
        "raw filaments",
    "\n已切到「打出来的颜色」显示：视口里就是成品顶面的颜色"
    "（视图变换 Standard）。要回到按耗材分色的视图，"
    "把 3D 视图右上角的着色方式改回 Material。":
        "\nSwitched the viewport to the printed colour (view transform "
        "Standard). To go back to the per-filament colour view, set the 3D "
        "view shading back to Material.",
    "一块区域只叠一种颜色：边界干净、不撒色点":
        "One stack per region: crisp edges, no speckle",
    # ---- v1.33.0 细缝（防两色之间的空隙） ----
    "窄于 %.2fmm 的细缝/细线并入旁边颜色，否则会印成两色之间的空隙":
        "Gaps and lines thinner than %.2f mm merge into the neighbouring "
        "colour; otherwise they print as voids between two colours",
    "细缝处理：窄于 %.2fmm 的色条已并给相邻颜色（共 %d 格）。":
        "Thin strips: colour bands thinner than %.2f mm were merged into "
        "the neighbouring colour (%d cells).",
    "这些窄条比挤出线还细，印不出来就是两色之间的空隙，成品一掰就断。":
        "Those strips are thinner than one extrusion line; if they cannot "
        "print they leave a void between the two colours and the part snaps.",
    "细缝：窄于 %.2fmm（%d 格）的色条已并给邻区 %d 格 —— 再窄切片器印不出来，"
    "会在两色之间留空隙":
        "Thin strips: bands under %.2f mm (%d cells) merged into neighbours "
        "(%d cells) - anything thinner prints as a void between two colours",
    # ---- v1.34.0 色点（一条挤出线宽） ----
    "实际色点 %.2f mm（= %d 格）；长边约 %d 个色点":
        "Actual colour dot %.2f mm (%d cells); about %d dots along the long edge",
    "每格一个颜色（%.2f mm）；长边约 %d 个色点":
        "One colour per cell (%.2f mm); about %d colour cells along the long edge",
    "精细度再高也不会更清楚，只会让模型更重":
        "More detail will not look sharper - it only makes the mesh heavier",
    "像素 %.2fmm，色点 %dx%d = %.2fmm（喷嘴线宽 %.2fmm，长边约 %d 个色点）":
        "Pixel %.2f mm, colour dot %dx%d = %.2f mm (line width %.2f mm, "
        "about %d dots along the long edge)",
    "提示：色点已补到「一条挤出线宽」%.2fmm；再想更细只能放大模型或换 0.2 喷嘴"
    "（%.2fmm 线）。":
        "Note: colour dots were padded to one extrusion line (%.2f mm); for "
        "finer detail enlarge the model or switch to a 0.2 mm nozzle (%.2f mm line).",
    "网点混色 ｜ 相邻点交错叠色，抖动 %s":
        "Halftone | neighbouring pixels alternate stacks, dither %s",
    "远看混出新颜色；0.4mm 喷嘴会把色点边缘糊在一起，实测容易发脏":
        "Mixes from a distance, but a 0.4 mm nozzle smears dot edges and muddies",
    "整块上色不做抖动：颜色直接取最接近的可打印叠法":
        "Solid blocks do not dither: each pixel takes the closest printable stack",
    "整块上色：一块区域只叠一种颜色，不撒碎点":
        "Solid blocks: one stack per region, no speckle",
    # ---- v1.32.0 竖墙颜色 / 挂绳孔自检 ----
    "侧面跟着自己的颜色": "Sides take their own colour",
    "浮雕起伏的侧面（高低落差那一面）用这块图案自己的颜色，"
    "而不是底板白。关掉的话落差大的地方会露出白色竖条、"
    "细的彩色线条会发白；代价是每一层都要换 2~4 次料，"
    "耗材和打印时间都会增加":
        "Paint the vertical faces of the relief (the height steps) with each "
        "area's own colour instead of the white base. Turning it off leaves "
        "white vertical stripes at big steps and washes out thin coloured "
        "lines; it saves 2-4 filament changes per layer, so less material "
        "and time.",
    "竖墙用图案自己的颜色（落差处不再露白）":
        "Vertical faces use their own colour (no white at the steps)",
    "代价：每层可能换 2~4 次料，耗材与时间会增加":
        "Cost: 2-4 filament changes per layer — more material and time",
    "竖墙用底板白：省料，但落差处会露白条":
        "Vertical faces stay white: cheaper, but steps show white stripes",
    "✓ 挂绳孔已贯通（射线穿过孔心无阻挡）":
        "Lanyard hole is through (a ray passes the centre unobstructed)",
    "⚠ 挂绳孔可能被堵：射线穿过孔心时打到了实体":
        "Lanyard hole may be blocked: a ray through the centre hit solid",
    "网点混色：相邻点交错叠色，近看是密集色点":
        "Halftone: neighbouring pixels differ; dense dots up close",
    "同一片区域里的颜色怎么落到点上：\n"
    "整块上色 = 一块区域只叠一种颜色，边界清楚、不撒碎点；"
    "近看是干净的色块，最接近实拍的干净观感。推荐。\n"
    "网点混色 = 相邻点用不同叠法交错排列，远看混出新颜色；"
    "近看是密密麻麻的色点，0.4mm 喷嘴会把色点边缘糊在一起，"
    "实测容易发脏、发彩噪。\n"
    "「全色平板」不用混色，本项对它无效":
        "How colours land on pixels:\n"
        "Solid blocks = one stack per region: crisp edges, no speckle, fewer "
        "colour changes, faster print. Recommended.\n"
        "Halftone = neighbouring pixels alternate stacks to fake in-between "
        "colours; grainy up close, and a 0.4 mm nozzle smears the dots.\n"
        "Ignored by Full-color Flat mode.",
    "叠层混色（推荐）": "Layered Mix (recommended)",
    "单色平铺（不用透光）": "Single Film (no translucency)",
    "表面混色（不透明耗材）": "Surface Mix (opaque)",
    "透光叠色（半透明耗材）": "Translucent Stack",
    "预览即实物顶面（不透明 PLA 适用）":
        "Preview = printed top face (for opaque PLA)",
    "需半透明耗材，否则实物远逊于预览":
        "Needs translucent filament, else print is far worse than preview",
    "每个点只用一种耗材色，靠相邻点并排混色":
        "One filament colour per dot, blended by neighbours",
    "不依赖耗材透光，但近看能看出色块":
        "No translucency needed, but lumps are visible up close",
    "同一个点上下叠 0.1mm 薄片，光穿过混色":
        "0.1 mm films stacked in place, blended by transmitted light",
    "与拓竹切片器的混色同一原理；近看是细条纹":
        "Same principle as Bambu Studio Color Mixing; fine stripes up close",
    "单色平铺：每个点一种色，近看能看出色块":
        "Single film: one colour per dot, lumps visible up close",
    "完全不依赖耗材透光，暗部会整体偏亮":
        "No translucency needed; shadows come out brighter",
    "叠层混色：同一点上下叠 0.1mm 薄片":
        "Layered mix: 0.1 mm films stacked in place",
    "光穿过几层后混成新颜色（青+黄=绿）":
        "Light passes the films and mixes (cyan + yellow = green)",
    "（层序参与配色；换色次数随层数上升）":
        "(layer order takes part; swaps grow with layer count)",
    "三档都用满 4 色 ｜ 抖动 %s": "All levels use 4 colors | dither %s",
    "关闭": "off",
    "蓝噪声：无规则网格，颗粒更像噪点":
        "Blue noise: no regular grid, grain looks like film noise",
    "有序方格：近看有规则格子纹理":
        "Ordered Bayer: regular grid texture up close",
    "去孤立点": "Despeckle",
    "量化已关闭：每个像素独立投影，最慢":
        "Quantization off: per-pixel projection, slowest",
    "孤立点清理仍生效 —— 只换成邻居已有的颜色，不引入新色":
        "Despeckle still active — swaps in neighbor colors only, no new colors",
    "目标色聚成 %d 簇后再投影（抖动照旧）":
        "Targets clustered into %d clusters before projection (dither unchanged)",
    "簇越少越快、局部越干净；越少也越容易出色带":
        "Fewer clusters = faster and cleaner locally, but more color banding",
    "参考：换色约 %d 次 ｜ 色相偏差约 %.0f° ｜ 平均色差 ΔE≈%.0f":
        "Ref: ~%d color changes | hue error ~%.0f° | mean ΔE≈%.0f",
    "（参考值为实拍照片×2+合成扫掠在建议分辨率下的实测中位；"
    "换色随图片内容变化。色相/ΔE 下限由 C/M/Y/W 四色色域"
    "决定，与档位无关）":
        "(Reference medians from 2 real photos + a synthetic sweep at the"
        " suggested resolution; color changes vary by image. The hue/ΔE floor"
        " is set by the CMYW gamut, not by the level)",

    # ---- 配色与高度 ----
    "只有 CMYK 做过实拍标定；这组用的是估算颜料谱":
        "Only CMYK is physically calibrated; this group uses an estimated "
        "pigment spectrum",
    "这套没有青/品红：白与浅色已校正成中性，中灰/肤色"
    "仍会偏橄榄（四色叠不出中性灰）":
        "No cyan/magenta here: whites and light tones are neutralised, but "
        "mid-greys and skin will still lean olive (four colors cannot stack "
        "a neutral grey)",
    "参与配色的颜色（没有先后要求）":
        "Colors used for shading (no order requirement)",
    "%s固定使用红黄蓝白四色": "%s always uses the four CMYW colors",
    "（透光叠色的物理需要四色齐全）":
        "(light-transmission stacking physically needs all four)",
    "底色（柱内叠色下固定白）": "Base color (fixed white with in-pillar stacking)",
    "底板固定白色：叠色光学需要白色基底":
        "Base plate fixed white: stacking optics need a white substrate",
    "自动挑选": "Auto pick",
    "说明：": "Notes:",
    "颜色逐像素独立分配，同一层可有不同色":
        "Colors assigned per pixel; one layer can hold many colors",
    "柱内叠色片：新增绿/蓝/红，暗部不再发紫":
        "In-pillar sheets add green/blue/red; darks no longer go purple",
    "薄层透光会让高饱和亮色略灰（物理取舍）":
        "Thin-sheet transmission mutes bright saturated colors (physics trade)",
    "亮度决定柱子高度（越深越高），起伏保留":
        "Brightness sets pillar height (darker = taller); relief kept",
    "底板与叠色基底固定为白（叠色光学需要）":
        "Base plate & substrate fixed white (stacking optics)",
    "锚点模式=基础：8 个可达色，换色最少":
        "Anchor = Basic: 8 reachable colors, fewest changes",
    "锚点模式=扩增：白底叠色 64 色，层序参与配色":
        "Anchor = Stack: 64 colors on white base, sheet order participates",
    "锚点模式=柱内混色：自由底 256 色，暗部改善最大":
        "Anchor = Mix: free base, 256 colors, biggest dark-region gains",
    "（仅半透明耗材成立；PLA 上颜色会印成顶层膜色）":
        "(holds only for translucent filament; on PLA each color prints as"
        " its top sheet)",
    "表面混色：顶面=单膜半色调，预览即实物":
        "Surface mix: top face = single-film halftone; preview = print",
    "暗部整体偏亮是四色不透明耗材的物理上限":
        "Lifted darks are the physical limit of four opaque colors",
    "表面混色下锚点不参与配色（顶面只有膜色半色调）":
        "Anchors don't participate in surface mix (top face is film"
        " halftone only)",
    "只看明暗，不带颜色信息": "Brightness only, no color information",
    "色相决定用哪层颜色（配色）": "Hue picks the color layer (shading)",
    "亮度决定该层堆多厚（厚度）": "Brightness sets that layer's thickness (height)",

    # ---- 后处理 ----
    "挂绳孔": "Lanyard Hole",
    "无": "None",
    "外置挂绳孔": "External Lanyard Hole",
    "内置挂绳孔": "Internal Lanyard Hole",
    "位置：照片底部居中": "Position: bottom-center of the image",
    "内孔半径 1.5 mm ｜ 外环半径 2.5 mm":
        "Inner hole 1.5 mm | outer ring 2.5 mm",
    "外置：圆环凸出在模型顶边外，与模型融合成一体":
        "External: the ring protrudes beyond the top edge, fused with the model",
    "内置：在模型内打通一个竖直圆孔":
        "Internal: a vertical through-hole drilled inside the model",
    # ---- 第 3 步 ----
    "生成与导出": "Generate & Export",
    "生成模型": "Generate Model",
    "导出可打印文件": "Export Printable Files",
    "查看模型质量报告": "Quality Report",
    "一键生成并导出": "Generate & Export in One Click",
    "导出设置": "Export Settings",
    "分析图片配色": "Analyze Palette",
    "清除所有生成结果": "Clear Generated Objects",
    "拓竹 2.5.3+ 可直接识别耗材分配":
        "Bambu Studio 2.5.3+ reads filament mapping directly",
    "3MF 已内置整套切片工艺": "3MF embeds a full slicer profile",
    "层高 0.1｜墙 1 层｜顶底壳 4 层":
        "Layer 0.1 | walls 1 | top/bottom shells 4",
    "填充 15%｜冲洗塔开": "Infill 15% | flush tower on",
    "换机打印时选「保留当前打印机」即可":
        "On another printer just choose \"Keep current printer\"",
    "⚠ 路径含中文": "⚠ Path contains non-ASCII characters",
    "拓竹导入 OBJ 可能崩溃": "Bambu Studio OBJ import may crash",
    "建议改为纯英文目录，": "Switch to an ASCII-only directory,",
    "或关闭上面的材质选项": "or turn off the OBJ material option above",
    "生成结果": "Generation Result",

    # ---- 算子：载入图片 / 分析 ----
    "载入图片失败": "Load Image Failed",
    "请先选择一张图片文件。": "Select an image file first.",
    "已载入图片：%s（%d × %d）": "Loaded image: %s (%d × %d)",
    "已载入 %s": "Loaded %s",
    "分析配色": "Palette Analysis",
    "分析配色失败": "Palette Analysis Failed",
    "请先指定一张图片。": "Select an image first.",
    "配色分析（%s 等级）": "Palette analysis (%s level)",
    "各色用量占比（仅供参考，选色不按用量）：":
        "Per-color coverage (reference only; selection is not by coverage):",
    "   %s：%.1f%%": "   %s: %.1f%%",
    "实际使用：%s（%d 种）": "Actually used: %s (%d colors)",
    "可用色上限：%d 种（三档都是 4 色）":
        "Palette cap: %d colors (all levels: 4)",
    "混色档位：%s": "Mixing granularity: %s",
    "连续（最准）": "Continuous (most accurate)",
    "1/%.0f（色相接近就并档，少换色）":
        "1/%.0f (near hues merge, fewer changes)",
    "色相覆盖估算：%.0f%%": "Estimated hue coverage: %.0f%%",
    "配色分析完成：使用 %s 共 %d 种颜色。":
        "Palette analysis done: using %s (%d colors).",

    # ---- 算子：生成 ----
    "生成失败": "Generation Failed",
    "请先指定一张图片（可在上方选择或点“载入图片”）。":
        "Select an image first (choose above or click \"Load Image\").",
    "已生成：%s｜网格 %d×%d｜顶点 %d｜面 %d｜用时 %.2f 秒":
        "Generated: %s | grid %d×%d | verts %d | faces %d | %.2f s",
    "边界边 %d": "boundary edges %d",
    "非流形边 %d": "non-manifold edges %d",
    "零面积面 %d": "zero-area faces %d",
    "⚠ 网格存在缺陷：": "⚠ Mesh defects:",
    "　说明：叠色卡的非流形棱来自同材质色壳的对角自相切（每色壳仍封闭、边界边"
    " 0），切片不受影响；":
        "  Note: the card's non-manifold edges come from same-color shells"
        " touching diagonally (each shell stays closed, boundary edges 0);"
        " slicing is unaffected; ",
    "\n建议降低采样精度或调整叠色层厚度后重试。":
        "\nTry lowering resolution or adjusting the layer thickness.",
    # v1.32.0（R39）：同材质台阶对角相碰（pinch）的说明 —— 不是缺陷
    "\n说明：另有 %d 条棱属于「同色台阶斜对角相碰」，不产生孔洞、"
    "每色壳仍封闭，切片不受影响。":
        "\nNote: %d more edges are same-color step corners meeting diagonally;"
        " they create no holes, every color shell stays closed, and slicing is"
        " unaffected.",
    "✓ 网格检查通过：水密、无边界边、无非流形边、无零面积面。":
        "✓ Mesh checks passed: watertight, no boundary / non-manifold /"
        " zero-area faces.",
    "采样方式：最近邻（线稿 / 像素画适用）":
        "Sampling: nearest (line art / pixel art)",

    # ---- 算子：导出 ----
    "导出": "Export",
    "还没有生成模型，请先点击“生成模型”。":
        "No generated model yet; click \"Generate Model\" first.",
    "无法创建导出目录：\n%s\n%s": "Cannot create export dir:\n%s\n%s",
    "请至少勾选一种导出格式（3MF 或 OBJ）。":
        "Pick at least one export format (3MF or OBJ).",
    "导出失败": "Export Failed",
    "   顶点 %d / 三角面 %d": "   verts %d / triangles %d",
    "⚠ OBJ 所在路径包含中文，且文件里带材质引用。\n"
    "   拓竹 Bambu Studio 的 OBJ 导入器在“中文路径 + 材质行”\n"
    "   组合下会崩溃（已确认的官方缺陷）。\n"
    "   建议：把导出目录改成纯英文路径，或关闭“OBJ 写入材质”。":
        "⚠ The OBJ path contains non-ASCII characters and the file carries"
        " material references.\n   Bambu Studio's OBJ importer crashes on the"
        " \"non-ASCII path + materials\" combination (confirmed upstream"
        " bug).\n   Fix: export to an ASCII-only directory, or turn off"
        " \"OBJ materials\".",
    "⚠ %s 文件大小为 0，请检查。": "⚠ %s file is 0 bytes; please check.",
    "导出完成：%d 个文件 → %s": "Export done: %d file(s) → %s",
    "导出完成": "Export Complete",

    # ---- 算子：预览 / 报告 / 清除 / 对齐 ----
    "生成配色预览图": "Preview Colors",
    "预览": "Preview",
    "预览失败": "Preview Failed",
    "已生成预览图：%s（可在图像编辑器里查看）":
        "Preview ready: %s (see the Image Editor)",
    "质量报告": "Quality Report",
    "还没有生成模型。": "No generated model yet.",
    "模型质量报告": "Model Quality Report",
    "模型：%s": "Model: %s",
    "生成模式：%s": "Mode: %s",
    "质量等级：%s": "Quality: %s",
    "参与配色：%s": "Palette: %s",
    "底色：%s": "Base color: %s",
    "共用底带：%.2f mm": "Shared base band: %.2f mm",
    "采样网格：%d × %d": "Sampling grid: %d × %d",
    "外形尺寸：%.2f × %.2f mm": "Size: %.2f × %.2f mm",
    "当前网格：顶点 %d / 面 %d": "Mesh now: %d verts / %d faces",
    "换色代价：": "Color-change cost:",
    "   预计换色次数：%s 次": "   Estimated color changes: %s",
    "   单层最多用色：%s 种": "   Max colors per layer: %s",
    "几何检查：": "Geometry checks:",
    "   边界边（应为 0）：%s": "   Boundary edges (want 0): %s",
    "   非流形边（应为 0）：%s": "   Non-manifold edges (want 0): %s",
    "   零面积面（应为 0）：%s": "   Zero-area faces (want 0): %s",
    "色带切换高度（色带叠色模式在切片软件里按这些 Z 值换料）：":
        "Band change heights (for band mode, change filament at these Z in"
        " the slicer):",
    "   第 %d 次换色：%.2f mm": "   Change #%d: %.2f mm",
    "   （无）": "   (none)",
    "已清除 %d 个生成对象。": "Removed %d generated object(s).",
    "采样精度已是 %d，柱宽 %.2f mm —— 已按 %s 喷嘴对齐。":
        "Resolution already %d, pillar %.2f mm — aligned to %s nozzle.",
    "采样精度 %d → %d：柱宽 %.3f → %.2f mm"
    "（%s 喷嘴挤出线宽按 %.2f mm 计）。"
    "现在每个色点都是一条走得出的挤出线。":
        "Resolution %d → %d: pillar %.3f → %.2f mm"
        " (%s-nozzle line width = %.2f mm). Every color dot is now a"
        " printable extrusion line.",
    "%s 喷嘴对齐": "%s Nozzle Alignment",

    # ---- 枚举项名称 ----
    "分层浮雕": "Layered Relief",
    "平整色块板": "Flat Color Blocks",
    "双面叠色": "Dual-Side Card",
    "极致": "Ultimate",
    "均衡": "Balanced",
    "像素": "Pixel",
    "分区配色（推荐）": "Per-Pixel (recommended)",
    "色带叠色（省换色）": "Color Bands (fewer changes)",
    "纯亮度": "Luminance Only",
    "面积平均（照片）": "Area Average (photos)",
    "最近邻（线稿 / 像素画）": "Nearest (line art / pixel art)",
    "蓝噪声（推荐）": "Blue Noise (recommended)",
    "有序方格": "Ordered Bayer",
    "基础（8色）": "Basic (8 colors)",
    "扩增（白底叠色 64 色）": "Stack (64 colors on white)",
    "柱内混色（自由底 256 色）": "Mix (free base, 256 colors)",
    "白色": "White",
    "天蓝": "Cyan",
    "品红": "Magenta",
    "黄色": "Yellow",
    "生成模式": "Mode",
    "喷嘴档位": "Nozzle Preset",
    "采样方式": "Sampling",
    "底色": "Base Color",
    "配色与高度方式": "Color & Height Mode",
    "锚点模式": "Anchor Mode",
    "颗粒图案": "Dither Pattern",
    "0.2 喷嘴（档位名）": "0.2 Nozzle",
    "0.4 喷嘴（档位名）": "0.4 Nozzle",
    "简体中文 / Chinese": "简体中文 / Chinese",
    "English / 英语": "English / 英语",
    "不添加挂绳孔": "No lanyard hole",
    "采样精度对齐到 0.4 喷嘴的挤出线宽（0.42 mm），让每根柱子都是一条走得出的挤出线":
        "Align resolution to the 0.4-nozzle extrusion width (0.42 mm) so every"
        " pillar prints as a real extrusion line",
    "采样精度对齐到 0.2 喷嘴的挤出线宽（0.22 mm）。\n0.2 喷嘴线宽只有 0.4 的一半，同样尺寸下能打到约两倍的采样精度，代价是打印时间大幅上升":
        "Align resolution to the 0.2-nozzle extrusion width (0.22 mm).\nThe"
        " 0.2 nozzle's line width is half of the 0.4's — roughly double the"
        " resolution at the same size, at a large print-time cost",

    # ---- 属性名（面板标签 / RNA 名）----
    "采样精度": "Resolution",
    "最长边尺寸 (mm)": "Longest Side (mm)",
    "底板厚度 (mm)": "Base Thickness (mm)",
    "叠色层总厚 (mm)": "Color-Layer Thickness (mm)",
    "叠色层数": "Layers per Side",
    "单片厚度 (mm)": "Sheet Thickness (mm)",
    "色块数": "Color Blocks",
    "色块层厚度 (mm)": "Color-Layer Thickness (mm)",
    "最小色块 (mm)": "Min Block Size (mm)",
    "平整色块板的底板颜色。它只出现在底面、侧壁和色块缝隙，"
    "不会被色块盖住 —— 照片还原建议用白色；用彩色底会让整块"
    "板的侧边带上那个颜色":
        "Base color of the flat block board. It shows on the bottom face, the side "
        "walls and the gaps between blocks; the blocks never cover it. White is the "
        "right default for photo reproduction — a colored base tints the whole rim.",
    "每面层数": "Layers per Side",
    "质量等级": "Quality Level",
    "颗粒图案": "Dither Pattern",
    "颜色量化": "Color Quantization",
    "去孤立点": "Despeckle",
    "底色（柱内叠色下固定白）": "Base Color (fixed white in in-pillar mode)",
    "明暗层次": "Brightness Levels",
    "对比度": "Contrast",
    "反转明暗": "Invert Brightness",
    "锚点模式": "Anchor Mode",
    "导出 3MF": "Export 3MF",
    "导出 OBJ": "Export OBJ",
    "3MF 按颜色拆分部件": "Split 3MF by Color",
    "OBJ 写入材质": "OBJ Write Materials",
    # ---- v1.38.0 拓竹模式 ----
    "拓竹模式": "Bambu Mode",
    "✓ 已启用：按颜色拆部件，拓竹打开即带耗材与参数":
        "On: split by color; Bambu Studio opens with filaments and settings",
    "已关闭：只输出几何与颜色，不调耗材、不调切片参数":
        "Off: geometry and colors only — no filament or slicer settings",
    "耗材槽顺序：%s": "Filament slot order: %s",
    "请按这个顺序装料（第 1 卷放第 1 个 AMS 槽）":
        "Load spools in this order (spool 1 in AMS slot 1)",
    "切片参数（写进 3MF，不改你电脑上的预设）":
        "Slicing settings (written into the 3MF; your presets stay untouched)",
    "层高": "Layer Height",
    "墙层数": "Wall Loops",
    "填充密度": "Infill Density",
    "热床附着": "Bed Adhesion (Brim)",
    "加支撑": "Supports",
    "擦料塔": "Prime Tower",
    "喷嘴温度 (°C)": "Nozzle Temp (°C)",
    "热床温度 (°C)": "Bed Temp (°C)",
    "恢复推荐值": "Restore Defaults",
    "擦料塔关掉后换色处会串色": "Without a prime tower, colors bleed at changes",
    "多色件建议保持开启；只是多耗一点料":
        "Keep it on for multi-color prints; it only costs a little filament",
    "0.05mm 层高：每片色膜占两层，时间约翻倍":
        "0.05 mm layers: each film takes two layers; roughly double the time",
    "加支撑会在表面留痕、并多耗料；浮雕/卡片一般不需要":
        "Supports leave marks and use extra filament; reliefs/cards rarely need them",
    "切片器会用它自己的预设；多色需你自己分配耗材":
        "The slicer uses its own presets; you assign filaments yourself",
    # v1.0.15：墙层数旋钮删除，报告不再提"墙 N 圈"
    "拓竹模式：开 — %d 个颜色部件，每个绑一个耗材槽；"
    "层高 %s mm 等固定参数已写入；墙圈数用切片器预设":
        "Bambu Mode: on — %d color parts, one filament slot each; "
        "layer height %s mm and other fixed settings written; "
        "wall loops come from your slicer preset",
    "0 = 不生成墙：外圈只剩填充，边缘更软、能看到填充纹路":
        "0 = no walls: only infill at the edge, softer rim and visible infill lines",
    "拓竹模式：关 — 只输出几何与颜色，不写耗材分配、不写切片参数":
        "Bambu Mode: off — geometry and colors only, no filament or settings",
    "⚠ 导出目录含中文：拓竹导入这个 OBJ 可能闪退。"
    "OBJ 的颜色存在同名 .mtl 里，要带颜色就换成英文"
    "目录；或者直接用 3MF（不受影响）。":
        "⚠ The export folder has non-ASCII characters: Bambu Studio may crash "
        "importing this OBJ. Its colors live in the matching .mtl — use an "
        "English folder to keep them, or export 3MF instead (unaffected).",
    "⚠ 拓竹模式已关闭：3MF 里没有耗材槽分配，切片器会按单色处理，"
    "多色需要你自己配。":
        "⚠ Bambu Mode is off: the 3MF has no filament assignment, so the slicer "
        "treats it as single-color. Assign filaments manually.",
    # v1.48.0 / R65：中文侧原写作 "15%%"、英文侧 "15%"，占位符口径不一致 ——
    # 若哪天这条被当成 %-格式串使用，英文侧会抛 "unsupported format character"。
    # 这条当前没有任何引用（纯文案），统一成不带转义的 "15%"。
    "拓竹模式已恢复推荐值（层高 0.10mm、1 圈墙、15% 填充）":
        "Bambu Mode restored to defaults (0.10 mm layers, 1 wall, 15% infill)",
    "图片": "Image",
    "图片文件": "Image File",
    "生成挂绳孔": "Add Lanyard Hole",

    # ---- 属性悬停说明（description）----
    "切换整个插件界面的显示语言": "Switch the entire add-on interface language",
    "选择一张已载入 Blender 的图片作为输入": "Pick an already-loaded Blender image as input",
    "从磁盘载入图片": "Load an image from disk",
    "选择模型的几何形态": "Choose the model's geometry form",
    "图片最长边的采样像素数。数值越高细节越多，但网格也越大。"
    "柱数按平方增长：1000 档 ≈ 100 万柱，生成时间与内存都显著上升":
        "Number of sample pixels along the image's longest side. Higher = more "
        "detail but a larger mesh. Pillar count grows with the square: the 1000 "
        "step ≈ 1M pillars, generation time and memory rise sharply",
    "模型最长边的实际打印尺寸，另一边按图片比例自动换算":
        "Actual print size of the longest side; the other side is derived from "
        "the image aspect ratio",
    "当前切片用的喷嘴孔径。只影响「对齐喷嘴」的目标柱宽与面板的柱宽提醒，"
    "不参与建模；换喷嘴后点一下对应按钮即可":
        "Nozzle bore used for slicing. Only affects the target pillar width of "
        "'Align Nozzle' and the panel's pillar-width hint; does not affect "
        "modeling. Click the matching button after changing nozzle",
    "最底下那层实心白板的厚度，保证底部平整、不透光":
        "Thickness of the solid white base plate at the bottom — keeps the base "
        "flat and opaque",
    "四色叠色区的总高度。越厚颜色层次越丰富，但打印越久":
        "Total height of the four-color stacking region. Thicker = richer color "
        "steps but longer prints",
    "叠色层数。双面叠色 = 每面几层"
    "（背面自动镜像，总层数 = 每面层数 × 2）。4 层已能表达全部可叠"
    "色阶（含同色重复层加深），再往上加只会加厚模型、不会多出颜色。"
    "参考方案用 4 层（双面共 8 层 0.8mm）":
        "Number of color sheets. Dual-side = layers per face (back auto-mirrored, "
        "total = per-face × 2). 4 layers already express all stackable steps "
        "(incl. same-color repeat deepening); more only thickens the model, not "
        "more colors. Reference: 4 layers (dual-side 8 layers, 0.8mm)",
    "叠色卡：每层色片的厚度，建议等于打印层高（0.08~0.12）":
        "Card: thickness of each color sheet; set ≈ print layer height (0.08~0.12)",
    "一次设定色彩还原与换色次数的整体取舍。\n"
    "极致 = 优先色彩还原；均衡 = 两者兼顾；像素 = 优先减少换色":
        "One setting for the trade-off between color fidelity and color changes.\n"
        "Ultimate = fidelity first; Balanced = both; Pixel = fewest changes",
    "抖动阈值用哪种图案。两种图案的「远看平均色」完全相同（都是"
    "均匀分布、每个阈值恰好用一次），区别只在颗粒怎么排布："
    "蓝噪声 = 点彼此尽量远离、没有规则网格，像胶片噪点，观感更自然；"
    "有序方格 = 传统 Bayer 矩阵，颗粒呈规则方格，近看能看出格子纹理":
        "Which dither pattern. Both have the identical far-view average color "
        "(uniform, each threshold used once); they differ only in grain layout: "
        "Blue Noise = points kept far apart, no grid, film-like; Ordered Bayer = "
        "regular grid texture visible up close",
    "一个格子怎么代表它盖住的那块源像素\n"
    "面积平均 = 取整块平均，接近人眼在观看距离上的空间积分，照片/插画用这个\n"
    "最近邻 = 取格心那一个源像素，不抗锯齿。线稿、像素画用这个："
    "面积平均会把 1 像素宽的细线冲淡到几乎全白，最近邻能把原色保住":
        "How one cell represents the source pixels it covers\n"
        "Area Average = mean of the whole block, close to what the eye integrates "
        "at viewing distance — use for photos/illustrations\n"
        "Nearest = takes the single center source pixel, no anti-alias. Use for "
        "line art / pixel art: Area Average washes a 1px line to near-white, "
        "Nearest keeps the original color",
    "先把图片的目标色聚成这么多簇，再做凸包投影与抖动。"
    "相邻格落进同一簇后权重完全相同 → 抖动排布更干净；同时逐格投影的"
    "计算次数从「几千种颜色」降到「几十簇」，生成速度快两个数量级。0 = 关闭":
        "Cluster the image's target colors into this many groups before convex "
        "hull projection and dithering. Cells in the same cluster get identical "
        "weights → cleaner dither; per-cell projection drops from thousands of "
        "colors to dozens of clusters, ~100x faster. 0 = off",
    "把「8 个邻居全都和自己不同」的孤立格换成邻域多数色。"
    "判据自带保色性：1 像素宽的细线沿线邻居同类，不会被误伤。"
    "主要收益是少几个孤零零的色点、顺带省换色。关闭颜色量化时它照样生效 —— "
    "那时只把孤立格换成邻居已有的颜色，不引入任何新色（v1.7.4）":
        "Replace 'isolated' cells (all 8 neighbors differ) with the neighbor "
        "majority. The rule is color-preserving: a 1px line's neighbors are "
        "identical along the line, so it is safe. Mainly removes stray dots and "
        "saves changes; also works with quantization off — then it only swaps in "
        "neighbor colors, introducing none (v1.7.4)",
    "统计图片的色相分布，在允许的颜色里自动挑出还原最好的一套。"
    "关闭后严格按下面勾选的颜色使用":
        "Analyze the image's hue distribution and auto-pick the best-restoring "
        "set among allowed colors. Off = strictly use the colors ticked below",
    "允许青色参与配色": "Allow cyan in the palette",
    "允许品红色参与配色": "Allow magenta in the palette",
    "允许黄色参与配色": "Allow yellow in the palette",
    "允许白色参与配色。灰、白像素只有靠白色才还原得出来，一般不要关闭":
        "Allow white in the palette. Grey/white pixels need white to restore; "
        "usually leave on",
    "底板使用的颜色（柱子最底下那一段）": "Base plate color (bottom segment of each pillar)",
    "柱子底部按「叠色层总厚」的这个比例统一用底色 —— 也就是"
    "同一根柱子沿 Z 轴分成两段颜色（下段底色 / 上段像素自己的颜色）。"
    "0 = 关闭（默认）。仅对「分区配色」生效。注意：实测它并不能减少换色次数"
    "（原因见说明文档），慎调大，会削弱侧壁的混色观感":
        "Bottom portion of each pillar uses the base color by this fraction of "
        "the color-layer thickness — i.e. one pillar is split into two Z segments "
        "(bottom base / top pixel color). 0 = off (default). Only affects "
        "Per-Pixel mode. Note: measured to NOT reduce color changes; raising it "
        "weakens the side-wall blend look",
    "把四色替换成拓竹 CMYK 耗材套装的实测观感色，打出来的颜色更接近预览":
        "Replace the four colors with Bambu CMYK filament set's measured look — "
        "prints closer to the preview",
    "决定颜色怎么分配、高度怎么算": "Decides how colors are assigned and height is computed",
    "分区配色下控制高度起伏的强弱：1 = 完整明暗层次，越小越接近等厚"
    "（颜色完全靠空间混色表现）；色带叠色模式下则是亮度对高度的贡献比例":
        "Per-Pixel: strength of height relief (1 = full brightness levels, lower "
        "= flatter, color shown by spatial blending). Color-Band: brightness's "
        "share of height",
    "调整高度分布的对比度。大于 1 拉大明暗差距，小于 1 更平缓":
        "Adjust contrast of the height distribution. >1 widens, <1 flattens",
    "把明暗颠倒，亮的地方变矮、暗的地方变高": "Invert brightness: bright→short, dark→tall",
    "柱内叠色的可达色域档位（分层浮雕的分区配色用；"
    "v1.10.0 起叠色链路常开、不再有开关）：基础（8 色）＝白底 + 每色至多一片；"
    "扩增（白底叠色 64 色）＝白底固定、3 层自由色片可重复可换序（4³=64）；"
    "柱内混色（自由底 256 色）＝底色不固定、共 4 层（4⁴=256），完全覆盖旧 45 色档。"
    "档位越高色域越广、暗部与高饱和区改善越大，生成越慢。"
    "「平整色块板」不做叠色投影，本项对它无效":
        "Reachable gamut tier for in-pillar blending (relief per-pixel; on since "
        "v1.10.0): Basic (8) = white base + ≤1 sheet per color; Stack (64) = fixed "
        "white base, 3 free sheets (4³=64); Mix (256) = free base, 4 layers "
        "(4⁴=256), fully covers the old 45 tier. Higher tier = wider gamut, better "
        "dark/saturated areas, slower. Not used by Flat Color Blocks",
    "在照片顶部居中加一个挂绳孔。外置 = 圆环凸出在模型顶边外并融合成一体，"
    "环底与浮雕底面齐平；内置 = 在模型内打通一个竖直圆孔。"
    "内孔 / 外环 / 厚度可在下方微调":
        "Add a lanyard hole centered at the photo top. External = a ring "
        "protruding past the top edge, fused into the model, with the ring "
        "bottom flush with the relief base; Internal = a vertical through-hole "
        "drilled inside. Inner/Outer/Thickness可调 below",
    "3MF / OBJ 的保存位置。留空则导出时落到桌面「叠色浮雕工坊文件夹」并自动创建":
        "Where 3MF / OBJ are saved. If left empty, export falls back to the "
        "desktop 'ColorStack Relief' folder (auto-created)",
    "导出文件的基础名（不含扩展名）。留空则使用默认名":
        "Base filename for exports (no extension). Uses the default name if empty",
    "每种颜色一个独立部件并标好耗材槽，拓竹打开后可直接多色打印。"
    "关闭则输出单件多材质":
        "One component per color with filament slots — Bambu Studio prints "
        "multi-color directly. Off = a single mesh with multiple materials",
    "同时输出 .mtl 材质文件": "Also write a .mtl material file",

    # ---- 枚举项说明（下拉项悬停）----
    "白色 —— 提亮色，也是低饱和（灰/白）像素唯一能还原的颜色":
        "White — a brightening color and the only way to restore low-saturation "
        "(grey/white) pixels",
    "青色 —— 冷色基底。与黄色相叠出绿、与品红相叠出蓝":
        "Cyan — cool base. Overlaps yellow→green, magenta→blue",
    "品红色 —— 与黄色相叠出红橙、与青色相叠出蓝":
        "Magenta — overlaps yellow→red-orange, cyan→blue",
    "黄色 —— 暖色。与青色相叠出绿、与品红相叠出红":
        "Yellow — warm. Overlaps cyan→green, magenta→red",
    "阶梯状高度场：图片明暗变成高低起伏，顶面每一格按该像素的混色叠加上色。"
    "效果最接近照片，推荐首选":
        "Stepped height field: image brightness becomes relief; each top cell is "
        "colored by that pixel's blended stack. Closest to the photo — recommended",
    "照片直接上色：把画面自动聚成 N 个色块，每块用一卷对应颜色的耗材"
    "平打出来，顶面全平、不做任何混色推算。"
    "颜色所见即所得（预览就是实物），代价是需要 N 卷耗材、换色约 N 次。"
    "适合色块分明的插画、Logo、卡通图；照片用它会变成海报式的少数色块":
        "Direct photo coloring: the image is clustered into N color blocks, each "
        "printed flat with one matching spool — flat top, no mixing model. "
        "What you see is what prints; cost: N spools and ~N filament changes. "
        "Great for flat-color art, logos, cartoons; photos become poster-like "
        "few-color blocks",
    "参考 MakerWorld 叠色卡结构：前后各几片 0.1mm 色层，颜色可重复堆叠"
    "（如红-白-红），两面都能看到同一套叠色效果。"
    "适合卡通图/色块图。注意：换色次数比浮雕多（每层四色齐全）":
        "MakerWorld-style card: a few 0.1mm layers front and back, repeatable "
        "(red-white-red), same look on both faces. For cartoon/blocky images. "
        "Note: more changes than relief (all four colors per layer)",
    "颜色按像素独立分配、同一层里可以出现不同颜色，中间色相（绿/橙/紫）"
    "靠相邻像素的空间混色还原。颜色最准；代价是同一层内换色次数会增加":
        "Color assigned per pixel; one layer can hold different colors; mid hues "
        "(green/orange/purple) restored by neighboring pixel blending. Most "
        "accurate; cost: more changes within a layer",
    "色相选色带、亮度定带内厚度，同一个高度上只有一种颜色。"
    "换色次数最少、打印最快，但色相只有四档，中间色会被并到最近的一档":
        "Hue picks the band, brightness sets in-band thickness; one height = one "
        "color. Fewest changes, fastest, but only 4 hue bands — mid hues snap "
        "to the nearest",
    "只看明暗不看颜色，经典灰度浮雕做法。黑白图/剪影/线稿适用":
        "Brightness only, no color — classic greyscale relief. For B&W/silhouette/"
        "line art",
    "挤出线宽按 %.2f mm 计，面板柱宽提醒也用这一档":
        "Extrusion width counted as %.2f mm; the panel's pillar-width hint uses "
        "this tier too",
    "32×32 蓝噪声：低频功率近 0、无离散谱峰，颗粒分布自然。"
    "还原度与 Bayer 完全相同（边缘分布相同），只是规则网格感消失":
        "32×32 blue noise: near-zero low-frequency power, no spectral peak, "
        "natural grain. Same fidelity as Bayer (same edge distribution), only the "
        "grid feel is gone",
    "传统 Bayer 矩阵（按质量等级用 4×4 或 8×8）。图案规则、每 2^n 格"
    "精确重复，近看有方格纹理。想复现旧版本效果时用":
        "Classic Bayer matrix (4×4 or 8×8 by quality). Regular pattern repeating "
        "every 2^n cells, grid texture up close. Use to reproduce the old look",
    "源图分辨率通常远高于柱子数，一格盖住几十个像素；取整块平均就是"
    "「这一小块整体看上去的颜色」。线稿上会抹掉细线":
        "Source resolution is usually far above pillar count; one cell covers "
        "dozens of pixels; block mean = the color this patch looks like overall. "
        "Washes out thin lines on line art",
    "只取格心那一个源像素，不做抗锯齿 —— 细线和纯色块不会被冲淡。"
    "照片用它会出现边缘毛刺（逐格随机偏色）":
        "Takes only the center source pixel, no anti-alias — thin lines and solid "
        "blocks stay crisp. On photos it gives edge jaggies (per-cell random tint)",

    # ---- 后处理 / 导出 说明文字（删除块但保留中性标签）----
    "位置：照片顶部居中（环底对齐浮雕底面）":
        "Position: top-center of the image (ring bottom flush with the base)",
    "内孔半径 1.5 mm ｜ 外环半径 2.5 mm":
        "Inner hole 1.5 mm | outer ring 2.5 mm",
    "外置：圆环凸出在模型顶边外，与模型融合成一体":
        "External: ring protrudes past the top edge, fused with the model",
    "内置：在模型内打通一个竖直圆孔": "Internal: a vertical through-hole inside",
    "3MF 已内置整套切片工艺": "3MF embeds a full slicer profile",
    "层高 0.1｜墙 1 层｜顶底壳 4 层": "Layer 0.1 | walls 1 | top/bottom shells 4",
    "填充 15%｜冲洗塔开": "Infill 15% | flush tower on",
    "换机打印时选「保留当前打印机」即可": "On another printer just choose 'Keep current printer'",

    # ---- 挂绳孔参数名 / 说明（新增）----
    "内孔半径 (mm)": "Inner Radius (mm)",
    "外环半径 (mm)": "Outer Radius (mm)",
    "挂绳孔厚度 (mm)": "Hole Thickness (mm)",
    "挂绳孔的内孔半径。外置=圆孔内缘、内置=圆孔半径":
        "Lanyard inner radius. External = ring inner edge; Internal = bore radius",
    "挂绳孔的外环半径（外置圆环的外缘；内置时作孔心到顶边的距离基准）":
        "Lanyard outer radius (external ring outer edge; internal uses it as the "
        "hole-center-to-top-edge distance base)",
    "挂绳孔轴向厚度（仅外置生效：外置圆环的高度；内置孔始终贯通）。"
    "0 或大于模型厚度则取模型厚度":
        "Lanyard axial thickness (external ring height only; internal is always "
        "through). 0 or larger than model thickness → use model thickness",

    # ---- 属性名（面板标签 / RNA 名，v1.16.0 补齐）----
    "清理孤立点（保色）": "Despeckle (color-preserving)",
    "自动挑选颜色": "Auto Pick Colors",
    "允许使用 天蓝": "Allow Cyan",
    "允许使用 品红": "Allow Magenta",
    "允许使用 黄色": "Allow Yellow",
    "允许使用 白色": "Allow White",
    "使用拓竹 CMYK 套装色": "Use Bambu CMYK Filament Set",
    "文件名": "Filename",
    "导出目录": "Export Directory",
    "共用底带": "Shared Base Band",
    "配色预览图": "Palette Preview",
    "配色分析": "Palette Analysis",
    "叠色浮雕工坊": "ColorStack Relief",

    # ---- 长描述（v1.16.0 补齐）----
    # ⚠ v1.22.0 顺手清了一处**重复键**：「叠色层数。…」这条在"参数与质量"段
    # 与本段各写了一份，内容逐字相同。Python 字典字面量后者覆盖前者 ——
    # 只改一处等于没改（改完界面还是旧译文）。这里保留上面那一份，删掉本段副本。
    "按当前配色估算出的呈现色对照图": "Preview of the rendered colors under the current palette",

    # ---- 新增算子：挂绳孔生成功能键（v1.16.0）----
    "在已生成的模型上直接添加挂绳孔（使用下方设置的半径与厚度）":
        "Add the lanyard hole directly to the already-generated model (using the "
        "radii and thickness set below)",
    "请先在「后处理」选择挂绳孔类型（外置 / 内置）":
        "Select a lanyard hole type (External / Internal) under Post-Processing first",
    "挂绳孔添加成功（内孔 %.1fmm ／ 外环 %.1fmm）":
        "Lanyard hole added (inner %.1fmm / outer %.1fmm)",

    # ---- 平整色块板（v1.22.0）：属性长描述 ----
    "把照片聚成多少个色块 —— 等于这项打印要用的彩色耗材卷数。"
    "越大越接近原图，但换色次数随之线性上升：8 色大致在一台"
    "AMS（4 槽 ×2 级）能自动完成的范围内，16 色需要四级串联。"
    "每加一色带来的还原改善会迅速变小，8 色之后通常已看不出差别":
        "How many color blocks the photo is clustered into — equal to the number "
        "of colored spools this print needs. More = closer to the original, but "
        "filament changes grow linearly: 8 fits one AMS (4 slots ×2) roughly, "
        "16 needs four chained. Gains shrink fast; past 8 you rarely see a "
        "difference",
    "小于这个尺寸的碎色斑会被并给相邻大色块。"
    "不合并的碎斑在实物上有两个害处：被切片器的小特征过滤"
    "（色块直接消失）、以及每进出一次就换一次料。"
    "0 = 关闭（原样保留每一个色斑）":
        "Patches smaller than this are merged into the neighboring larger block. "
        "Unmerged specks hurt twice: the slicer's small-feature filter drops them "
        "(the block simply disappears), and every entry/exit is a filament "
        "change. 0 = off (keep every speck as-is)",

    # ---- 平整色块板（v1.22.0）：色块与全色域分析 ----
    "色块分析（平整色块板）": "Block Analysis (Flat Color Blocks)",
    "需要耗材：%d 卷彩色 + 1 卷底板色（%s）":
        "Spools needed: %d colored + 1 base (%s)",
    "换色次数约 %d 次（每层按色块数走一遍）":
        "≈ %d filament changes (each layer walks all blocks once)",
    "色号清单（按用量从多到少，照此装槽即可）：":
        "Color list (by usage, load your AMS in this order):",
    "最小色块 %.1f mm（判据 %d 格）｜已合并 %d 格":
        "Min block %.1f mm (criterion %d cells) | %d cells merged",
    "全色域分析（照片原色 vs %d 色还原）：":
        "Full-gamut analysis (photo colors vs %d-color reconstruction):",
    "   色差 ΔE：中位 %.2f ｜ p90 %.2f ｜ 最大 %.2f":
        "   ΔE: median %.2f | p90 %.2f | max %.2f",
    "   覆盖率：ΔE≤1 %.0f%% ｜ ≤2 %.0f%% ｜ ≤3 %.0f%% ｜ ≤5 %.0f%%":
        "   Coverage: ΔE≤1 %.0f%% | ≤2 %.0f%% | ≤3 %.0f%% | ≤5 %.0f%%",
    "   色彩构成：亮度中位 L*%.0f（p10 %.0f / p90 %.0f）":
        "   Composition: median L* %.0f (p10 %.0f / p90 %.0f)",
    "   彩度中位 %.1f ｜ 中性色（灰黑白）占 %.0f%%":
        "   Median chroma %.1f | neutral (gray/black/white) %.0f%%",
    "   量化：%d 种目标色 → %d 簇（平均损失 %.2f ΔE）":
        "   Quantization: %d target colors → %d clusters (mean loss %.2f ΔE)",
    "   主色调（色相分布前四）：%s":
        "   Dominant hues (top 4 bins): %s",
    "建议色块数：%d（再加色的覆盖率增益已 <0.5 个百分点）":
        "Suggested blocks: %d (further colors gain <0.5 pp coverage)",
    "色块分析完成：%d 个色块。": "Block analysis done: %d blocks.",
    "色块与全色域分析": "Blocks & Full-Gamut Analysis",
    "色块数：%d 个": "Color blocks: %d",
    "色号清单（按用量）：": "Color list (by usage):",
    "最小色块：%.1f mm ｜ 已合并 %d 格":
        "Min block: %.1f mm | %d cells merged",
    "全色域分析：ΔE 中位 %.2f / p90 %.2f / 最大 %.2f；覆盖 ΔE≤3 达 %.0f%%":
        "Full-gamut: ΔE median %.2f / p90 %.2f / max %.2f; ΔE≤3 coverage %.0f%%",
    "   （色块板的换色发生在同一层内，不按 Z 高度换料）":
        "   (Flat color blocks change filament within a layer, not by Z height)",

    # ---- v1.26.0（R32）：面板收缩 + 还原度改远看口径 ----
    "兼容模式（不用透光）": "Compatibility Mode (no translucency)",
    "兼容模式：每个点只用一种耗材色，靠相邻点并排混色":
        "Compatibility mode: each dot uses a single filament color; neighbors "
        "blend spatially",
    "不透光耗材也能打，但近看有色块、暗部整体偏亮":
        "Works with opaque filament, but blocks show up close and shadows look "
        "brighter",
    "兼容模式：每个点一种色，近看能看出色块":
        "Compatibility mode: one color per dot, blocks visible up close",
    "细碎孤立色点会自动并入周围颜色（已固定开启）":
        "Tiny isolated specks are merged into surrounding colors (always on)",
    "颗粒排布、颜色合并、碎点清理已固定为推荐值，不需要调":
        "Grain pattern, color merging and speck cleanup are fixed at the "
        "recommended values — nothing to adjust",
    "按拓竹套装色显示": "Show Bambu filament-set colors",
    "只改切片器里显示的颜色，不影响用料和打出来的颜色":
        "Only changes the colors shown in the slicer — not the filament used, "
        "not the printed result",
    "拓竹实测色值只对应 CMYK 四卷，本组按标称色显示":
        "Bambu measured colors exist for the CMYK four only; this group uses "
        "nominal colors",
    "颜色还原度（远看，按 3×3 格取平均）：":
        "Color fidelity (viewed from a distance, 3×3 cells averaged):",
    "   近看每颗色点都有偏差，一臂距离看才是上面的效果":
        "   Up close every dot is slightly off; the value above is what you see "
        "at arm's length",
    "颜色怎么混出来：\n"
    "叠层混色 = 同一个点上下叠几层不同颜色的 0.1mm 薄片，"
    "光穿过去后混成新颜色（与拓竹切片器的混色同一原理）；"
    "耗材要能透一点光，PLA Basic 可以。\n"
    "兼容模式 = 每个点只用一种耗材色，靠相邻点并排混色；"
    "完全不依赖透光，暗部会偏亮、近看能看出色块，"
    "换不透光的耗材时用它。\n"
    "「全色平板」不用混色，本项对它无效":
        "How colors are mixed:\n"
        "Stacked mixing = several 0.1mm films of different colors stacked at the "
        "same spot; light passes through and blends into a new color (the same "
        "principle as Bambu Studio color mixing). Filament must be slightly "
        "translucent — PLA Basic works.\n"
        "Compatibility mode = each dot uses one filament color and neighbors "
        "blend spatially; no translucency needed, shadows look brighter and "
        "blocks are visible up close. Use it for opaque filament.\n"
        "\"Full-color flat\" does not mix — this setting has no effect there",
    "只影响切片软件里看到的颜色（把四色换成拓竹 CMYK "
    "套装的实测色值），让 3MF 打开时更接近实物。\n"
    "**不影响打出来的颜色，也不影响用料**：混色计算对 "
    "C/M/Y/W 恒定使用同一张实拍标定表，与这里勾不勾无关。":
        "Only affects the colors shown in the slicer (uses measured Bambu CMYK "
        "values so the 3MF looks closer to the real print).\n"
        "Does NOT change the printed colors or the filament used: mixing always "
        "uses the same calibrated table for C/M/Y/W regardless of this switch.",

    # ---- v1.27.0（R34）：厚度改为自动 ----
    "每面 %d 层 × %.2f mm ｜ 总厚 %.2f mm（自动）":
        "%d layers/side × %.2f mm | total %.2f mm (auto)",
    "固定 0.8mm：这是叠色实测标定过的唯一厚度，打更厚实物会比预览暗":
        "Fixed at 0.8 mm — the only thickness the color calibration covers; "
        "thicker cards print darker than the preview",
    # ---- v1.0.6：双面卡改为每面固定 6 层 ----
    "每面 6 层、白可叠在颜色外层：层数固定不用调，浅色和肤色比 4 层准得多":
        "6 layers per side, white may sit outside the color films — no need to "
        "tune; pale tones and skin tones are much closer than the 4-layer card",
    "双面平板：每面固定 6 层，两面同图，表面平整；白色可以叠在颜色外层，"
    "浅色更准":
        "Dual-side flat stack: 6 fixed layers per side, same image both faces, "
        "perfectly flat; white can go outside the color films for accurate pale "
        "tones",
    "底板 %.2f + 色块层 %.2f ｜ 总厚 %.2f mm（自动）":
        "Base %.2f + color layer %.2f | total %.2f mm (auto)",
    "底板 %.2f + 起伏层 %.2f ｜ 总厚 %.2f mm（自动）":
        "Base %.2f + relief layer %.2f | total %.2f mm (auto)",
    # ---- v1.0.13：颜色层总数 = 色膜 + 真起伏（用户口径）----
    "白底板 4 层 0.40 + 颜色层 %d 层 %.2f（色膜 %d + 起伏 %d）｜ 总厚 %.2f mm":
        "White base 4×0.40 + %d color layers %.2f "
        "(%d film + %d relief) | total %.2f mm",
    "色膜每格都要叠满、不参与起伏；真正上下的落差 = %.2f mm":
        "Film is stacked on every cell and does not undulate; "
        "real relief = %.2f mm",
    "正面 %.2f（共 %d 层：色膜 %d 层 %.2f + 起伏 %d 层 %.2f，无白底板）＋ 反面白 %.2f（%d 层）｜ 整件 %.2f mm":
        "Front %.2f (%d layers: %d film %.2f + %d relief %.2f, no white base) "
        "+ back white %.2f (%d layers) | total %.2f mm",
    "四周各加 %.1f mm 外框（底板色），成品最长边 %.1f mm":
        "%.1f mm rim on all four sides (base color); outer size %.1f mm",
    "精细度 ≥ %d（该质量档建议值的 2 倍）→ 已按逐格上色：不并块、不压色阶，台阶 = 柱宽 %.2f mm":
        "Detail ≥ %d (2× this quality tier's suggestion) → one cell per colour: "
        "no block merge, no posterization; step = cell width %.2f mm",
    "⚠ 柱顶 %d 层色膜占 %.2f，画面实际只有 %.2f mm 落差（约 %d 层），接近平板":
        "Warning: %d color-film layers take %.2f; real relief is only "
        "%.2f mm (~%d layers), nearly flat",
    "顶面完全平整，厚度固定 0.8mm（每面 4 片 ×0.1）":
        "Perfectly flat top; fixed 0.8 mm (4 films ×0.1 per side)",
    # ---- v1.29.0（R36）：色组描述只留用色 + 两个备注 ----
    "青 / 品红 / 黄 / 白（减色混合）":
        "Cyan / Magenta / Yellow / White (subtractive mixing)",
    "红 / 黄 / 蓝 / 白（视觉叠色）": "Red / Yellow / Blue / White (visual mixing)",
    "青 / 品红 / 黄 / 白 / 绿 / 黑": "Cyan / Magenta / Yellow / White / Green / Black",
    "青 / 品红 / 黄 / 白 / 红 / 绿 / 蓝（克莱因蓝 #002FA7）/ 黑":
        "C / M / Y / W / R / G / B (Klein blue #002FA7) / K",
    "叠色浮雕 / 双面平板用哪几卷耗材。\n"
    "CMYK = 青 / 品红 / 黄 / 白（减色混合）\n"
    "RYBW = 红 / 黄 / 蓝 / 白（视觉叠色）\n"
    "CMYWGK = 青 / 品红 / 黄 / 白 / 绿 / 黑\n"
    "8色MAX = 青 / 品红 / 黄 / 白 / 红 / 绿 / 蓝 / 黑":
        "Spools used by relief / two-sided card.\n"
        "CMYK = Cyan / Magenta / Yellow / White (subtractive mixing)\n"
        "RYBW = Red / Yellow / Blue / White (visual mixing)\n"
        "CMYWGK = C / M / Y / W / Green / Black\n"
        "8-color MAX = C / M / Y / W / R / G / B / K",
    "叠层混色：与双面平板同一套原理":
        "Stacked mixing: same principle as the two-sided card",
    "柱顶叠 8 片 0.1mm 色膜（正面 4 + 镜像 4）":
        "Eight 0.1 mm films at the top (4 front + 4 mirrored)",
    "模型厚度 (mm，已改为自动)": "Model Thickness (mm, now automatic)",
    "v1.27.0 起厚度由插件按模式与幅面自动计算"
    "（全色平板按幅面取色块层、叠色浮雕按长边的 2.2% "
    "取起伏层、双面平板固定 0.8mm）。\n"
    "本项仅保留用于兼容旧工程，改了不生效。":
        "Since v1.27.0 the thickness is computed automatically from the mode and "
        "the model size (flat blocks: layer by size; relief: 2.2% of the long "
        "edge; two-sided card: fixed 0.8 mm).\n"
        "Kept only for old projects — changing it has no effect.",
    "选择模型的几何形态。三种模式都能设尺寸、精细度和"
    "质量等级；厚度按幅面自动计算。"
    "混色组的档数不同（全色平板不用混色）":
        "Pick the model shape. All three modes share size, detail and quality "
        "settings; thickness is computed from the model size. The color groups "
        "differ (flat blocks use no mixing at all).",
    # ---- v1.35.0（R42）：质量等级面板精简后的新文案 ----
    "%s ｜ %d 个色块 ｜ 小于 %.1fmm 的碎斑并入旁边":
        "%s | %d color blocks | specks under %.1f mm merged away",
    "色块数 ≈ 彩色耗材卷数；档位越高越像原图、越费料":
        "Block count ≈ number of color spools; higher levels look closer to the "
        "photo and use more material",
    "%s ｜ 换色约 %d 次 ｜ 平均色差 ΔE≈%.0f":
        "%s | about %d color changes | average ΔE≈%.0f",
    "档位越低越省料：色块更大、换色更少、细节更少":
        "Lower levels save material: bigger blocks, fewer color changes, "
        "less detail",
    "兼容模式：相邻点并排混色，每格只用一种耗材色":
        "Compatible mode: adjacent dots blend side by side, one filament "
        "color per cell",
    "色块交界做了错缝搭接：内侧 %.1fmm 分两段、各朝相反方向错开，像砌墙的丁砖":
        "Color borders are staggered: the inner %.1f mm is split in two halves "
        "offset in opposite directions, like brickwork",
    "颜色交界不再是一条从上到下贯通的直缝，掰的时候裂缝要拐弯":
        "A color border is no longer one straight seam from front to back, so a "
        "crack has to turn a corner",
    "代价：换色次数不变；模型面数多约 16%（文件更大、切片稍慢）":
        "Cost: the number of color changes is unchanged; about 16% more "
        "triangles (bigger file, slightly slower slicing)",
    "整块上色：一块区域只叠一种颜色":
        "Solid blocks: one stack color per region",
    "整块上色：每 %d 格并一块；窄于 %.2fmm 的细线并入旁边":
        "Solid blocks: %d cells per block; lines under %.2f mm merge into the "
        "neighbouring color",
    "整块上色：每格一块；窄于 %.2fmm 的细线并入旁边":
        "Solid blocks: one cell per block; lines under %.2f mm merge into the "
        "neighbouring color",
    "窄于 %.2fmm 的细线并入旁边颜色（再细印不出来）":
        "Lines thinner than %.2f mm merge into the neighbouring color "
        "(anything thinner cannot print)",
    "网点混色：相邻点交错叠色，近看是颗粒":
        "Halftone mixing: adjacent dots alternate stacks; grainy up close",
    "可印色点 %.2f mm；长边约 %d 个色点":
        "Printable color dot %.2f mm; about %d dots along the long edge",
    "%s 喷嘴最细 %.2f mm；精细度再高也不会更细，只会让模型更重":
        "%s nozzle bottoms out at %.2f mm; more detail cells will not make it "
        "sharper, only heavier",
    "图片最长边采成多少个格子 —— 格子越密细节越多，"
    "面数和生成时间按平方增长。\n"
    "但颜色能细到多少由喷嘴决定：0.4 喷嘴一条挤出线"
    "约 0.42mm（100mm 长边约 238 个色点），0.2 喷嘴"
    "约 0.22mm（约 454 个）。超过这个数只会让模型更重，"
    "画面不会更细。\n"
    "（它也不决定「用几卷耗材」—— 耗材卷数由质量等级"
    "和混色组决定）":
        "How many cells the long edge is sampled into — denser cells keep more "
        "detail, and face count grows with the square.\n"
        "How fine the colours can get is set by the nozzle: a 0.4 nozzle prints "
        "one extrusion line of about 0.42 mm (about 238 colour dots along "
        "100 mm), a 0.2 nozzle about 0.22 mm (about 454). Going past that only "
        "makes the model heavier, not sharper.\n"
        "(It does not decide how many spools you use — the quality level and "
        "colour group do.)",

    # ---- v1.0.14 分辨率档位 / 外框层数 / 删除的功能 ----
    "分辨率": "Resolution",
    "外框层数": "Rim layers",
    "混色模型：叠层混色（固定）": "Mixing model: stacked films (fixed)",
    "底板固定白色（与色膜分成两个对象，切片时可单独改色）":
        "The base is fixed white (a separate object from the colour films, so "
        "you can recolour it in the slicer)",
    "%d 层外框 = 四周各加 %.2f mm（底板色），成品最长边 %.2f mm":
        "%d rim layers = %.2f mm on each side (base colour); final longest "
        "edge %.2f mm",
    "外框 0 层：不加外框，成品最长边就是 %.1f mm":
        "0 rim layers: no rim; the final longest edge is %.1f mm",
    "第 %d 档：长边约 %d 格｜≈ %s 个像素柱":
        "Level %d: about %d cells on the long edge | ≈ %s pixel columns",
    # ---- v1.0.15（用户 2026-11-20）----
    "总厚 %.2f mm ｜ 长边约 %d 格 · 约 %s 个像素柱":
        "Total %.2f mm | about %d cells on the long edge · ≈ %s pixel columns",
    "总厚 %.2f mm ｜ 选图后显示预估像素柱":
        "Total %.2f mm | pick an image to see the estimated column count",
    "按色板：用标定板实拍反解的颜料参数":
        "Calibrated: pigment parameters back-solved from the printed swatch",
    "按色板计算": "Calibrated",
    "按理论计算": "Theoretical",
    "整块上色": "Solid blocks",
    # ---- v1.0.17（用户 2026-11-22）：补齐 v1.0.14/15 改写后漏译的面板与悬停文案 ----
    'v1.27.0 起厚度由插件按模式与幅面自动计算（全色平板按幅面取色块层、叠色浮雕按长边的 2.2% 取起伏层、双面平板固定 1.2mm）。\n本项仅保留用于兼容旧工程，改了不生效。': "Since v1.27.0 the thickness is computed automatically from the mode and the model size (flat blocks: layers by size; relief: 2.2% of the long edge; two-sided card: fixed 1.2 mm).\nKept only for compatibility with old projects — changing it has no effect.",
    '从磁盘载入明信片反面的图片': "Load the back-side image from disk",
    '全色平板：照片聚成 N 个色块，每块直接上色，顶面全平': "Full-color flat: the photo is clustered into N color blocks, each painted directly; the top stays flat",
    '叠色层数。单面平板固定 8 层彩膜（不用调，8 层是色阶收益的拐点：165 种叠法，比 4 层的 35 种多 4.7 倍）；双面叠色固定每面 6 层（背面自动镜像，总 12 层、1.20mm 厚）—— 白可以排到彩膜外侧，6 层能叠出整片浅色与中间调。本滑杆只影响浮雕等其它模式': "Number of stacked films. Single-sided flat fixes 8 films (no need to tune — 8 layers is the sweet spot: 165 stacks vs. 35 at 4 layers); the two-sided card fixes 6 per side (mirrored automatically, 12 in total, 1.20 mm thick) — white can sit outside the films, so 6 layers still reach pale tints and mid-tones. This slider only affects relief and the other modes",
    '叠色浮雕：明暗变成高低起伏，顶面按颜色抖动混色': "Stacked relief: brightness becomes height, and the top surface mixes colors by dithering",
    '只在正面叠色，下面垫白挡串色，反面纯白；适合只看一面的卡片': "Colors on the front only, with a white under-layer that blocks bleed-through; the back is plain white — best for one-sided cards",
    '品红色 —— CMYK 组的暖色基底': "Magenta — the warm base of the CMYK set",
    '导出设置（3MF / OBJ）': "Export settings (3MF / OBJ)",
    '底板色：%s': "Base color: %s",
    '底板（露在外面的板体）的颜色：全色平板用在底面、侧壁和色块缝隙；明信片浮雕用在**整个反面**。照片还原建议白色；选彩色会让侧边/反面整片带上那个颜色，而且要多备一卷那种料': "Color of the base (the plate you see from outside): on the full-color flat it shows on the bottom, the side walls and the gaps between blocks; on the postcard relief it covers the whole back. White is recommended for photos; a colored base tints every side wall and the entire back, and needs one more spool of that color",
    '开启后：3MF 按颜色拆成独立部件，每个部件绑定一个耗材槽，并把下面的切片参数写进文件 —— 拓竹打开就能直接切片。\n关闭后：3MF 只带几何与颜色，不写耗材分配、不写切片参数，切片器完全用它自己的预设（多色需要你自己分配耗材）': "On: the 3MF is split into one part per color, each bound to a filament slot, with the slicer settings below written into the file — Bambu Studio can slice it right away.\nOff: the 3MF carries geometry and colors only — no filament assignment, no slicer settings; the slicer uses its own preset (you assign filaments yourself for multi-color)",
    '挂绳孔（默认放在模型上部）': "Lanyard hole (placed at the top of the model by default)",
    '按理论：直接用上面这几个颜色算，不做实测校正': "Theoretical: use the colors above as-is, with no measured correction",
    '按色板计算：用打印出来的标定板实拍反解参数，跟手上这批耗材最接近（推荐）。\n按理论计算：完全按上面色组给定的颜色做理想减色叠加，不套用任何实拍修正 —— 换色组或改色值都能算，但与实物的偏差没有校正。': "Calibrated: back-solve the pigment parameters from photos of the printed swatch — closest to the filament you actually have.\nTheoretical: ideal subtractive stacking of the colors above with no measured correction — works for any color set or custom values, but is not calibrated against the real print.",
    '换了色组或改了色值也能算，但跟实物的偏差没有校正': "Works for any color set or custom values, but is not calibrated against the real print",
    '明信片反面文字/图案刻下去的最大深度。越深摸起来越明显、侧光下越清楚；但每加深 0.1mm，整件就要多 0.1mm 背板，太深会让正面起伏变薄': "Maximum depth of the text/pattern engraved into the back. Deeper feels more pronounced and reads better in raking light; every extra 0.1 mm also adds 0.1 mm to the plate, and too deep leaves less room for the front relief",
    '明信片正面这一层的总厚（柱顶色膜 + 能上下起伏的层）。反面已经是白板，所以正面不再垫白底板。越厚起伏越大、画面越有立体感，也越费料和时间；1.2mm 只有色膜（平板），3mm 有 1.8mm 落差': "Total thickness of the front side (color films on the pillar tops plus the layers available for relief). The back is already a white plate, so the front needs no white under-layer. Thicker means deeper relief and more presence, at the cost of filament and time; 1.2 mm is films only (flat), 3 mm gives 1.8 mm of relief",
    '明信片浮雕的反面图案（文字/邮戳/二维码等）。越暗的地方刻得越深；留空则背面是平的': "Artwork for the back of the postcard relief (text / postmark / QR code). Darker areas are engraved deeper; leave it empty for a flat back",
    '明暗变柱高、柱顶叠色。4~8 卷料还原整张照片，换色少、打得快；暗部会略亮': "Brightness drives pillar height, colors stack on the pillar tops. 4–8 spools reproduce a whole photo with few color changes and fast printing; shadows come out slightly brighter",
    '模型四周的物理外框层数：每层 0.40mm。\n1 层 = 0.40mm，2 层 = 0.80mm，0 = 不加外框。\n成品最长边 = 最长边尺寸 + 2×层数×0.40mm。\n（这是模型外框，不是切片器里的墙圈数）': "Physical rim around the model, 0.40 mm per layer.\n1 layer = 0.40 mm, 2 layers = 0.80 mm, 0 = no rim.\nFinal longest edge = size + 2 × layers × 0.40 mm.\n(This is the model rim, not the slicer's wall loops)",
    '模型最外圈打印几层实墙。0 = 不生成墙（外圈只剩填充，边缘会更软、能看到填充纹路）；1 = 只有一圈外墙，薄板上与填充同色同高、肉眼几乎看不出来；2 起才有内墙，边缘更结实也更耐磕。圈数越多越费时（每圈都要沿整圈走一遍）': "How many solid wall loops the slicer prints on the outer edge. 0 = no walls (infill only at the edge, softer rim, infill lines visible); 1 = one outer wall only, which on a thin plate matches the infill in color and height and is almost invisible; 2 and up add inner walls for a tougher edge. More loops cost more time",
    '正反两面各叠 6 层色片，两面同图、完全平整，像一张卡片；换色偏多': "6 color films on each side, same image on both sides, perfectly flat like a card; more color changes",
    '正面叠色浮雕 + 反面单色凹刻（文字/邮戳），正反厚度分开设；比浮雕多一层背板': "Stacked relief on the front plus single-color engraving on the back (text / postmark); front and back thickness set separately; one more plate than relief",
    '混色色组：%s，固定 %d 卷耗材': "Color set: %s, fixed %d spools",
    '照片直接聚成 N 个色块，每块用一卷对应颜色的耗材平打，预览＝实物、最省心；代价是 N 卷料、换色约 N 次': "The photo is clustered into N color blocks, each printed flat with one matching spool — preview = print, the simplest path; the cost is N spools and about N color changes",
    '画面自动聚成 N 个色块，每个色块用一卷对应颜色的耗材平打，不做混色推算': "The picture is clustered into N color blocks; each block is printed flat with one matching spool, no mixing involved",
    '白色 —— 默认。照片还原、明信片反面都建议用它': "White — the default. Recommended for photos and for the back of postcards",
    '碎色斑由质量档位里的「最小色块」自动合并': "Speckles are merged via \"Min block\" in the quality level",
    '红色 —— RYBW 组的暖色；当底板会带明显的红边': "Red — the warm color of the RYBW set; as a base it leaves a visible red edge",
    '色组由你选定：买了几卷什么色，就按那几卷算': "You pick the set: whatever spools you bought is what gets used",
    '蓝色 —— RYBW 组的冷色；当底板会带明显的蓝边': "Blue — the cool color of the RYBW set; as a base it leaves a visible blue edge",
    '计算依据': "Calculation basis",
    '质量等级（三选一）': "Quality level (pick one)",
    '这 %d 卷：%s': "These %d spools: %s",
    '这组要 %d 卷：%s': "This set needs %d spools: %s",
    '选择模型的几何形态。五种模式都能设尺寸、精细度和质量等级；厚度按模式自动计算（明信片另有两项）。混色组的档数不同（全色平板不用混色）': "Pick the geometric shape. All five modes share size, resolution and quality level; thickness is computed per mode (the postcard has two extra fields). The color-set options differ (the full-color flat does not mix)",
    '青色 —— 冷色底。CMYK 组的冷色基底': "Cyan — the cool base of the CMYK set",
    '黄色 —— CMYK / RYBW 都有这卷': "Yellow — present in both CMYK and RYBW",
    # v1.0.17：两处此前漏翻译的运行时文案（异常消息 + 质量报告缺陷行）
    '图片没有有效像素（%d × %d），请重新载入图片。': "The image has no valid pixels (%d × %d). Please load it again.",
    '边界边 %d / 非流形边 %d / 零面积面 %d': "Boundary edges %d / non-manifold edges %d / zero-area faces %d",
}


def set_lang(lang):
    """设置当前语言（props.language 的 update 回调调用）。"""
    global LANG
    LANG = "en_US" if lang == "en_US" else LANG_DEFAULT


def t(s):
    """翻译一条文案：英文模式查表（查不到原样返回），中文模式原样返回。"""
    if LANG == "en_US":
        return _ZH2EN.get(s, s)
    return s


def t_desc(cn, en):
    """翻译一段同时带有中/英两版的说明（用于常量里已存好 en 的下拉项说明）。"""
    return en if LANG == "en_US" else cn


def retranslate_ui():
    """语言切换后重注册面板与算子，让注册期文案（bl_label / bl_category /
    bl_description）切换到目标语言。

    【必须经 bpy.app.timers 延迟调用】update 回调可能发生在 UI 交互过程中，
    直接改类注册表有崩溃风险；定时器回调在空闲态执行，安全。
    """
    from . import operators as _ops
    from . import panels as _panels
    from . import props as _props
    try:
        _panels.retranslate()
        _ops.retranslate()
        _props.retranslate()
    except Exception as e:  # noqa: BLE001 —— 界面刷新失败不影响数据
        print("[ColorStack Relief] retranslate failed: %s" % e)


def _ui_busy():
    """是否有模态算子在跑（文件选择对话框会把它调用的算子转成 modal）。"""
    import bpy
    try:
        wm = bpy.context.window_manager
        for win in wm.windows:
            for _op in win.modal_operators:
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _retranslate_deferred():
    global _TIMER_PENDING
    # 【v1.16.1 崩溃护栏】文件选择对话框打开期间**绝对不能**重注册类：
    # 对话框把它调用的算子（如 cstack.load_image）挂在 window.modal_operators
    # 上，此时反注册/重注册会让对话框持有的算子类型悬空 —— 用户点"接受"时
    # Blender 野地址崩溃（2026-09-27 实机 crash log：EXCEPTION_ACCESS_VIOLATION
    # 于文件浏览器 Accept，前序操作 = 切语言）。忙时保持排队标记、0.5s 后重试。
    if _ui_busy():
        return 0.5
    _TIMER_PENDING["v"] = False
    retranslate_ui()
    return None


def request_retranslate():
    """排队一次延迟重注册（幂等：同一帧内只排一次）。"""
    import bpy
    if not _TIMER_PENDING["v"]:
        _TIMER_PENDING["v"] = True
        try:
            bpy.app.timers.register(_retranslate_deferred, first_interval=0.0)
        except Exception:
            _TIMER_PENDING["v"] = False
            retranslate_ui()
