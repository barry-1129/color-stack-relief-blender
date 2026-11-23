# -*- coding: utf-8 -*-
"""导出器 —— 3MF / OBJ+MTL / 换色高度说明。

为什么手写 3MF
==============
Blender 官方**不提供** 3MF 导出（只有第三方插件），而 3MF 恰恰是拓竹
Bambu Studio 最认的格式。好在 3MF 本质是 OPC 包（zip）+ 一份 XML，
完全可以按规范手写，且能顺带写入 Bambu 的 part/extruder 元数据 ——
用户打开切片软件时 4 种耗材已经分配好，不用手工配。

结构（实测可被 Python 解析回读）：

    [Content_Types].xml
    _rels/.rels
    3D/3dmodel.model                 ← 几何 + basematerials + assembly
    Metadata/model_settings.config   ← 拓竹：object/part/extruder 映射
    Metadata/project_settings.config ← 拓竹：切片工艺参数（JSON，v1.6 新增）
    Metadata/color_guide.txt         ← 手工换色高度表（无 AMS 也能用）

OBJ 的中文路径陷阱（来自实测踩坑记录）
======================================
拓竹 GUI 的 OBJ 导入器在**非 ASCII 目录 + OBJ 含 mtllib/usemtl 行**时会
无限递归 → 栈溢出闪退。

v1.39.0 起面板不再提供「OBJ 写入材质」开关（并入「导出 OBJ」）：勾了导出 OBJ
就必然写 .mtl —— OBJ 本体不存颜色，颜色只能由同名 .mtl 承载。路径若含非 ASCII
字符，返回值的 ``bambu_risk`` 为真，由界面提示"换英文目录，或者直接用 3MF"。
不自动丢材质：宁可提示，也别让用户拿到一个没颜色的 OBJ 却不知道原因。
"""

import json
import os
import zipfile
from xml.sax.saxutils import escape, quoteattr

from ..constants import (
    BAMBU_APP_TAG, BAMBU_TEMPLATE_FILE, BASE_COLORS, BAMBU_NS,
    BAMBU_PROCESS_PRESET, THREEMF_CORE_NS, THREEMF_UNIT,
    srgb_tuple_to_hex,
)


# ---------------------------------------------------------------------------
# 网格数据提取
# ---------------------------------------------------------------------------
def extract_mesh_data(bm):
    """把 bmesh 摊平成 ``(verts, tris, tri_mats)``，面按三角扇拆分。"""
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    # Blender 5.x 行为变化（5.1.2 实测）：ensure_lookup_table 只建序列查找表、
    # 不再同步元素 .index；只有 bmesh.ops 调用会顺带刷索引。v1.16.4 起 relief
    # 关闭 weld（不再跑 remove_doubles），隐式依赖断裂 → 导出 3MF 所有三角
    # 退化成 (0,0,0)、Bambu 报 "plate is empty"。这里显式刷新索引。
    bm.verts.index_update()
    verts = [(float(v.co.x), float(v.co.y), float(v.co.z)) for v in bm.verts]

    tris = []
    tri_mats = []
    for f in bm.faces:
        idx = [v.index for v in f.verts]
        n = len(idx)
        if n < 3:
            continue
        mat = f.material_index
        for i in range(1, n - 1):
            tris.append((idx[0], idx[i], idx[i + 1]))
            tri_mats.append(mat)
    return verts, tris, tri_mats


def group_by_material(verts, tris, tri_mats, material_count):
    """按材质把三角形分桶。返回 ``[[tri_index, ...], ...]``。"""
    buckets = [[] for _ in range(max(1, material_count))]
    for k, t in enumerate(tris):
        m = tri_mats[k]
        if m < 0 or m >= len(buckets):
            m = 0
        buckets[m].append(k)
    return buckets


# ---------------------------------------------------------------------------
# 3MF
# ---------------------------------------------------------------------------
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    '<Default Extension="config" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    '</Types>'
)

_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
    '</Relationships>'
)


def _fmt(v):
    """紧凑浮点格式（3MF 体积敏感，去掉多余尾数）。"""
    s = "%.5f" % float(v)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def build_3mf_model_xml(verts, tris, tri_mats, materials,
                        title="叠色浮雕", split_by_material=True,
                        extra_parts=None):
    """生成 ``3D/3dmodel.model`` 的 XML。

    materials : list[dict]
        每项 ``{"key","name","srgb"}``，下标即材质索引。
    split_by_material : bool
        True  → 每种颜色一个 object，外面套一层 assembly（拓竹会读成
                一个多部件模型，每个部件一个耗材）
        False → 单一 object，靠每个三角形的 pindex 表达颜色
    extra_parts : list[dict]
        v1.0.15：额外部件（目前只有「底板」）。每项
        ``{"name", "verts", "tris", "mat"}`` —— 自带顶点/三角，材质索引用
        ``mat``。它们**排在按颜色分的部件之后**，于是
        ``build_model_settings_config`` 的 extruder 映射只要按同样顺序续上
        就行（见 export_3mf 里的 obj_used 拼法）。
    """
    n_mat = max(1, len(materials))
    extra_parts = list(extra_parts or [])
    v_lines = []
    for (x, y, z) in verts:
        v_lines.append('<vertex x="%s" y="%s" z="%s"/>' % (_fmt(x), _fmt(y), _fmt(z)))
    v_block = "".join(v_lines)

    # 居中变换（Issue 1 根因修复）：模型几何已被 mesh_builder.center_and_ground
    # 居中到 XY 原点、Z 从 0 起；但 Bambu 板材原点在「角」，A1(256) 中心 =
    # (128,128)。若 build item 不写 transform，模型整体掉到板材一角，被 GUI
    # 自动拖入后才显示偏位。这里整体平移到板材中心（按几何 bbox 中心算，
    # 即便将来 center_and_ground 没跑也能兜底居中）。
    if verts:
        xs = [v[0] for v in verts]
        ys = [v[1] for v in verts]
        tx = 128.0 - (min(xs) + max(xs)) * 0.5
        ty = 128.0 - (min(ys) + max(ys)) * 0.5
    else:
        tx = ty = 128.0
    _center = "%.5f %.5f 0" % (tx, ty)

    # 三角形按材质预分组（两种模式都要用）
    by_mat = group_by_material(verts, tris, tri_mats, n_mat)

    parts = []
    parts.append(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="%s" xml:lang="zh-CN" '
        'xmlns="%s" xmlns:BambuStudio="%s">\n'
        % (THREEMF_UNIT, THREEMF_CORE_NS, BAMBU_NS)
    )
    parts.append(' <metadata name="Title">%s</metadata>\n' % escape(title))
    # Application 必须以 "BambuStudio-" 开头 —— 这是拓竹 bbs_3mf.cpp 判定
    # 「本工程文件」的开关（m_is_bbl_3mf）；否则 project_settings.config
    # 会被整体忽略（实测 varG 实验）。真实作者是我们，这里声明为
    # 兼容生成器并保留 Title/Description 里的产品信息。
    parts.append(' <metadata name="Application">%s</metadata>\n' % escape(BAMBU_APP_TAG))
    parts.append(' <metadata name="BambuStudio:3mfVersion">1</metadata>\n')
    parts.append(' <resources>\n')

    # basematerials
    #
    # 【v1.48.0 / R65】属性值必须用 quoteattr，不能用 escape。
    # escape 只处理 & < >，不处理属性分隔符 `"` —— 材质名里出现一个引号，
    # 整个 3D/3dmodel.model 就变成非法 XML（实测：材质名 bad"name →
    # verify_3mf 报 ParseError('not well-formed (invalid token)')，拓竹直接
    # 拒绝打开）。当前名称都来自常量或 #RRGGBB，触发不了；但「四档色组色值
    # 可编辑 UI」在待办里，一旦用户能自己填色名，就是"填个引号就导不出"。
    # 注意 quoteattr 会自己加首尾引号，所以格式串里不能再写 "..."，也不能
    # 先 escape 一遍（会变成 &amp;amp; 双重转义）。
    parts.append('  <basematerials id="1">\n')
    for m in materials:
        nm = str(m.get("name", m.get("key", "material")))
        col = srgb_tuple_to_hex(m.get("srgb", (1.0, 1.0, 1.0)))
        parts.append('   <base name=%s displaycolor=%s/>\n'
                     % (quoteattr(nm), quoteattr(col)))
    parts.append('  </basematerials>\n')

    if split_by_material:
        # 每个材质一个 mesh object
        obj_ids = []
        next_id = 2
        for mi in range(n_mat):
            tri_idx = by_mat[mi]
            if not tri_idx:
                continue
            obj_ids.append(next_id)
            # v1.16.4 优化：每个材质 mesh 只写「自有顶点」（去重+重映射），
            # 而非把整份顶点缓冲原样塞进每个 object（即便该材质只用到其中
            # 一小撮三角）。v1.16.3 实测 2.57M 顶点缓冲 ×4 份使文件虚胖到
            # 62MB、切片加载显著变慢。注意：丢色的真正根因是 relief 几何
            # 的开放面片（v1.16.4 已在 mesh_builder.build_relief 改逐色封闭
            # 壳）+ extract_mesh_data 的索引未同步（见该函数内注释），
            # 此处只是体积/速度优化。
            used = set()
            for k in tri_idx:
                a, b, c = tris[k]
                used.add(a); used.add(b); used.add(c)
            used_list = sorted(used)
            remap = {old: new for new, old in enumerate(used_list)}
            parts.append('  <object id="%d" type="model" pid="1" pindex="%d">\n'
                         % (next_id, mi))
            parts.append('   <mesh>\n    <vertices>')
            for vi in used_list:
                x, y, z = verts[vi]
                parts.append('<vertex x="%s" y="%s" z="%s"/>' % (_fmt(x), _fmt(y), _fmt(z)))
            parts.append('</vertices>\n    <triangles>\n')
            for k in tri_idx:
                a, b, c = tris[k]
                parts.append('     <triangle v1="%d" v2="%d" v3="%d"/>\n'
                             % (remap[a], remap[b], remap[c]))
            parts.append('    </triangles>\n   </mesh>\n  </object>\n')
            next_id += 1

        # ---- v1.0.15：额外部件（底板）----
        # 排在所有颜色部件之后，拓竹里会多出一个名字叫「底板」的部件，
        # 可以单独选中换料；几何自带顶点，不与颜色部件共享索引。
        for _ep in extra_parts:
            _ev = _ep.get("verts") or []
            _et = _ep.get("tris") or []
            if not _et:
                continue
            obj_ids.append(next_id)
            parts.append('  <object id="%d" type="model" pid="1" pindex="%d">\n'
                         % (next_id, max(0, int(_ep.get("mat", 0)))))
            parts.append('   <mesh>\n    <vertices>')
            for (x, y, z) in _ev:
                parts.append('<vertex x="%s" y="%s" z="%s"/>'
                             % (_fmt(x), _fmt(y), _fmt(z)))
            parts.append('</vertices>\n    <triangles>\n')
            for (a, b, c) in _et:
                parts.append('     <triangle v1="%d" v2="%d" v3="%d"/>\n'
                             % (a, b, c))
            parts.append('    </triangles>\n   </mesh>\n  </object>\n')
            next_id += 1

        # assembly
        asm_id = next_id
        parts.append('  <object id="%d" type="model">\n   <components>\n' % asm_id)
        for oid in obj_ids:
            parts.append('    <component objectid="%d" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n'
                         % oid)
        parts.append('   </components>\n  </object>\n')
        build_item = asm_id
    else:
        parts.append('  <object id="2" type="model" pid="1" pindex="0">\n')
        parts.append('   <mesh>\n    <vertices>%s</vertices>\n' % v_block)
        parts.append('    <triangles>\n')
        for k, (a, b, c) in enumerate(tris):
            parts.append('     <triangle v1="%d" v2="%d" v3="%d" pid="1" pindex="%d"/>\n'
                         % (a, b, c, min(tri_mats[k], n_mat - 1)))
        parts.append('    </triangles>\n   </mesh>\n  </object>\n')
        build_item = 2
        obj_ids = [2]

    parts.append(' </resources>\n')
    # build item 必须带 transform 把整体平移到板材中心（见上方 _center 说明）
    parts.append(' <build>\n  <item objectid="%d" transform="1 0 0 0 1 0 0 0 1 %s" printable="1"/>\n </build>\n'
                 % (build_item, _center))
    parts.append('</model>\n')

    xml = "".join(parts)
    return xml, obj_ids, build_item


def build_model_settings_config(obj_ids, materials, used_materials,
                                root_id=None, part_names=None):
    """Bambu Studio 的 ``Metadata/model_settings.config``。

    ``obj_ids`` 是**子对象**（每种材质一个 mesh object）的 id，
    ``used_materials`` 是与之一一对应的材质索引。
    ``root_id`` 是 ``<plate>`` 的 ``model_instance`` 要指向的对象 id ——
    必须传 ``build_3mf_model_xml`` 返回的 ``build_item``：拆部件时它是
    套在外面的 assembly id，不拆部件时就是那唯一的 object id。
    不传则退回旧口径 ``max(obj_ids) + 1``（只对"拆部件"成立）。

    结构要点（对照真实拓竹工程实测，踩过坑）：

    * ``<part>`` 的 id = 子对象 id，``extruder`` 放这里（每个部件一种耗材）；
    * 3MF 里还有一个**根装配对象**（``build_3mf_model_xml`` 中由
      ``<components>`` 引用所有子对象，id = ``max(obj_ids)+1``）；
    * ``<plate>`` 的 ``model_instance.object_id`` **必须指向根装配对象**，
      且只有一个实例 —— 早期版本错写成每个子对象一个实例，子对象同时又是
      组件，拓竹解析模型直接失败：
          return_code -6
          "The input model file to the slicer can not be parsed."

    【v1.48.0 / R65】不拆部件时（``split_by_material=False``）没有 assembly，
    ``obj_ids`` 只有 ``[2]``、``build_item`` 也是 2。旧代码仍按
    ``max(obj_ids)+1 = 3`` 写 object_id，指向一个不存在的对象 —— 拓竹对
    悬空引用的处理是整包解析失败（return_code -6 那一类）。面板走不到这个
    组合（``export_all`` 用 ``split_by_material and bambu_mode`` 一处收口），
    但导出器 API 允许，等于留了个"谁直接调谁踩"的坑。
    """
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<config>']
    if root_id is None:
        root_id = (max(obj_ids) + 1) if obj_ids else 1
    root_id = int(root_id)
    lines.append('  <object id="%d">' % root_id)
    # v1.48.0 / R65：属性值一律 quoteattr（escape 不转义引号，见 build_3mf_model_xml）
    lines.append('    <metadata key="name" value=%s/>' % quoteattr("叠色浮雕"))
    lines.append('    <metadata key="extruder" value="1"/>')
    for _i, (oid, mi) in enumerate(zip(obj_ids, used_materials)):
        m = materials[mi] if 0 <= mi < len(materials) else {}
        # v1.0.15：额外部件（底板）有自己的名字，不能跟着材质名（否则切片器
        # 里会看到两个「白色」，用户分不出哪个是底板）。
        if part_names and _i < len(part_names) and part_names[_i]:
            nm = str(part_names[_i])
        else:
            nm = str(m.get("name", m.get("key", "part")))
        lines.append('    <part id="%d" subtype="normal_part">' % oid)
        lines.append('      <metadata key="name" value=%s/>' % quoteattr(nm))
        lines.append('      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>')
        lines.append('      <metadata key="extruder" value="%d"/>' % (mi + 1))
        lines.append('    </part>')
    lines.append('  </object>')
    lines.append('  <plate>')
    lines.append('    <metadata key="plater_id" value="1"/>')
    lines.append('    <metadata key="plater_name" value=""/>')
    lines.append('    <metadata key="locked" value="false"/>')
    lines.append('    <metadata key="filament_map_mode" value="Auto For Flush"/>')
    lines.append('    <model_instance>')
    lines.append('      <metadata key="object_id" value="%d"/>' % root_id)
    lines.append('      <metadata key="instance_id" value="0"/>')
    lines.append('      <metadata key="identify_id" value="1"/>')
    lines.append('    </model_instance>')
    lines.append('  </plate>')
    lines.append('</config>')
    return "\n".join(lines)


def build_color_guide(stack, base_layer, materials, mode_note=""):
    """生成换色高度说明（无 AMS 也能手工换丝）。"""
    lines = []
    lines.append("叠色浮雕工坊 —— 换色高度说明")
    lines.append("=" * 44)
    if mode_note:
        lines.append(mode_note)
        lines.append("")
    lines.append("在切片软件中，于下列 Z 高度插入暂停/换料指令：")
    lines.append("")
    lines.append("  层序   高度(mm)   颜色        说明")
    lines.append("  ----   --------   ---------   ----------------")
    lines.append("  第0层  %.2f       %-10s  %s" % (
        0.0, base_layer and BASE_COLORS.get(base_layer.key, {}).get("cn", "基底") or "基底",
        "底板（不换料，直接用）"))
    for i, layer in enumerate(stack):
        cn = BASE_COLORS.get(layer.key, {}).get("cn", layer.key)
        note = ""
        if i == 0:
            note = "在此高度换入第一种叠色"
        lines.append("  第%d层  %.2f       %-10s  %s" % (i + 1, layer.z_hi, cn, note))
    lines.append("")
    lines.append("提示：")
    lines.append("  1. 层高建议 0.08~0.12mm，层越薄叠色过渡越细腻。")
    lines.append("  2. 100% 填充，关闭支撑，正面朝下打印效果最好。")
    lines.append("  3. 拓竹机器装了 AMS 的话，直接在 Bambu Studio 里按 3MF 自带")
    lines.append("     的耗材分配打印即可，无需手工换料。")
    return "\n".join(lines)


_TEMPLATE_CACHE = None


def bambu_template():
    """读取随包分发的拓竹工程模板（真实 A1 四色工程快照，570 键）。

    模板不会被打包脚本复制到别处；它是 :mod:`constants.BAMBU_PROCESS_PRESET`
    的**底座**：身份键（printer/print/filament settings_id、机型能力、耗材
    数组长度）全部由它提供，我们只覆盖工艺值。
    """
    global _TEMPLATE_CACHE
    if _TEMPLATE_CACHE is None:
        import os.path
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(here, BAMBU_TEMPLATE_FILE)
        with open(path, "r", encoding="utf-8") as f:
            _TEMPLATE_CACHE = json.load(f)
    return dict(_TEMPLATE_CACHE)


def _align_value(tpl_value, new_value):
    """按模板的数组长度对齐覆盖值。

    ``project_settings.config`` 里的向量键长度由机型/耗材数决定（实测 A1 的
    ``outer_wall_speed`` 是长度 1，参考 P1S 工程是长度 2；``filament_colour``
    则等于耗材数）。直接写长度不符的数组会让切片阶段失败（CLI -100），
    所以这里统一以模板长度为基准：超长截断、不足循环补齐。
    """
    if not isinstance(tpl_value, list):
        return new_value
    if not isinstance(new_value, list):
        return new_value
    n = len(tpl_value)
    # v1.48.0 / R65：空数组要单独挡掉。下面的循环取 `i % len(new_value)`，
    # new_value 为空时是 ZeroDivisionError（实测 `_align_value([1,2,3], [])`）。
    # 面板只产出标量覆盖值，所以还没被触发过；但 build_project_settings 是公开
    # 入口，脚本传一个空数组就会让整个导出失败，报错还只有
    # "integer modulo by zero"，用户完全看不懂。
    if not new_value:
        return []
    if n == 0 or len(new_value) == n:
        return new_value
    return [new_value[i % len(new_value)] for i in range(n)]


# 模板里长度恰好等于耗材槽数、但**不是**逐耗材数组的键（床面几何 / 头缠绕探测区）。
# 扩槽必须显式排除它们 —— `printable_area` 的 4 是"床面四角"，把它扩到 13 项
# 会让切片直接失败（实测 CLI -100）。
_NOT_PER_FILAMENT = frozenset((
    "printable_area", "head_wrap_detect_zone",
))


def _extend_filament_arrays(cfg, n_new, n_old):
    """把按"耗材槽数"排列的数组从模板的 ``n_old`` 槽扩到 ``n_new`` 槽。

    【为什么需要】模板是**四色** A1 工程快照，逐耗材数组都只有 4 项。旧代码
    ``n = len(模板 filament_colour)`` 把实际颜色**循环填满 4 个槽**，于是第 5 个
    耗材起没有档案 —— 拓竹按 4 槽解析、**彩色被静默压成 4 色**（实测 2026-10-16：
    12 色块板 3MF 交给 CLI 只报 4 条耗材）。「平整色块板」支持到 16 色（+ 底板
    = 17 槽），必须按实际槽数写。

    【长度规则】模板 570 键里，按槽数排列的有五种长度（全部实测确认）：

    ================================================  ==========
    长度                                              含义
    ================================================  ==========
    n                                                 逐耗材数组（142 个键）
    n + 1                                             ``filament_nozzle_map`` / ``filament_volume_map``
    2n                                                ``flush_volumes_vector`` / ``filament_dev_ams_drying_ams_limitations``
    n²                                                ``flush_volumes_matrix`` / 干燥温度 / 干燥时间
    n + 2                                             ``different_settings_to_system``（由 ``_sync_different_settings`` 重建）
    ================================================  ==========

    **不能只扩 ``filament_`` 前缀的键**：实测只扩前缀会让切片直接失败（CLI -100），
    因为 ``nozzle_temperature`` / ``pressure_advance`` / 各板温 / 风扇这些**工艺侧**
    的逐耗材数组同样要和槽数一致。所以这里的判据是"长度等于 n"，用
    :data:`_NOT_PER_FILAMENT` 显式排除床面几何键。

    ``filament_self_index`` 单独重写成 1..N（槽位序号，循环填充会得到 1,2,3,4,1,2…）。
    """
    if n_new <= n_old:
        return

    def cyc(v, n):
        return [v[i % len(v)] for i in range(n)]

    for k in list(cfg.keys()):
        if k in _NOT_PER_FILAMENT:
            continue
        v = cfg.get(k)
        if not isinstance(v, list) or not v:
            continue
        if k == "filament_self_index":
            cfg[k] = [str(i + 1) for i in range(n_new)]
        elif len(v) == n_old:
            cfg[k] = cyc(v, n_new)
        elif len(v) == n_old + 1:
            cfg[k] = cyc(v, n_new + 1)
        elif len(v) == 2 * n_old:
            cfg[k] = cyc(v, 2 * n_new)
        elif len(v) == n_old * n_old:
            # n×n 矩阵（冲洗矩阵 / 干燥曲线）：按 (行, 列) 双循环取旧值
            cfg[k] = [v[(i // n_new % n_old) * n_old + (i % n_new % n_old)]
                      for i in range(n_new * n_new)]


def _material_hex(m):
    """材质 dict → ``#RRGGBB``（拓竹 ``filament_colour`` 只认 6 位）。

    注意 :func:`constants.srgb_tuple_to_hex` 产出的是 8 位 ``#RRGGBBAA``
    （3MF ``displaycolor`` 的写法），这里要砍掉 alpha 通道。
    """
    return srgb_tuple_to_hex(m.get("srgb", (1.0, 1.0, 1.0)))[:7].upper()


# 颜色键不进 ``different_settings_to_system`` 工艺差异项：实测它们由工程层
# 单独恢复（模板差异清单为空时颜色仍然生效），放错槽位反而会被当成
# 工艺预设差异写进 print 项。
_COLOR_ONLY_KEYS = ("filament_colour", "filament_multi_colour")


def _sync_different_settings(cfg, overlay):
    """v1.6.3 根因修复：重算 ``different_settings_to_system``。

    拓竹 GUI 打开 3MF 的链路（v02.08 源码核实）是
    ``Plater`` → ``PresetBundle::load_config_model`` →
    ``load_config_file_config`` → ``PresetCollection::load_external_preset``
    → ``update_non_diff_values_to_base_config``：只要文件里的预设名能在
    本地**系统预设库**中找到，**凡是不在 ``different_settings_to_system``
    清单里的键，一律被替换回系统预设的值**。而 CLI 切片走全量快照，不查
    这份清单 —— 这就是"CLI 切片层高 0.1 / GUI 显示 0.2"分叉的根因。

    模板残留的旧清单只有 ``sparse_infill_density`` 一项，层高/墙/壳等
    覆盖键全部被 GUI 静默丢弃（v1.6.2 实机复现）。

    清单结构 = [工艺差异, 耗材1..N 差异, 打印机差异]（分号分隔的键名）。
    我们不改耗材/打印机预设，对应项维持模板原值；工艺项 =
    **覆盖层 ``overlay`` 的全部键** ∪ 模板原有差异键 ∪ 任何与模板不同的键。

    为什么必须列出"覆盖层全部键"而非"仅与模板不同的键"
    ==================================================
    模板取自用户机器 02.07 工程，而用户 GUI 是 02.08：若某覆盖键的
    值与 02.07 模板默认相同，按"仅差异"算法会被漏掉；一旦 02.08 的
    **系统预设默认值**与 02.07 不同，GUI 就会用 02.08 默认值覆盖我们的值
    —— 这正是 v1.6.2 的失败模式（层高 0.2 复现）。因此宁多勿漏：把所有
    覆盖键都写进清单，强制 GUI 逐键恢复。颜色键（``filament_colour`` 等）
    由工程层单独恢复，绝不进工艺差异项。
    """
    tpl = bambu_template()
    n_fil = len(cfg.get("filament_colour") or [])
    total = n_fil + 2
    old = tpl.get("different_settings_to_system")
    old = list(old) if isinstance(old, list) else [""]
    while len(old) < total:
        old.append("")

    overlay_keys = set(overlay.keys()) if isinstance(overlay, dict) else set()
    diff_keys = [
        k for k in sorted(cfg)
        if k != "different_settings_to_system"
        and k not in _COLOR_ONLY_KEYS
        and (k in overlay_keys or cfg[k] != tpl.get(k))
    ]
    # 模板自带差异键（分号分隔，键名不含分号）
    entry0 = [s for s in old[0].split(";") if s]
    merged = list(dict.fromkeys(entry0 + diff_keys))

    out = [";".join(merged)] + old[1:total]
    cfg["different_settings_to_system"] = out


def build_project_settings(preset=None, materials=None, extra=None,
                           filament_extra=None):
    """``Metadata/project_settings.config`` —— 拓竹切片工艺参数。

    虽然后缀是 ``.config``，拓竹实际存的是 **JSON**（键值全为字符串/字符串
    数组）。

    v1.6.2 起不再是"几十个孤立工艺键"，而是 **真实拓竹工程模板 + 工艺覆盖**：

    1. 以 :func:`bambu_template` 载入的 570 键工程快照为底（身份键 = 真实
       存在的预设名 ``Bambu Lab A1 0.4 nozzle`` / ``Bambu PLA Basic @BBL A1``，
       耗材数组长度天然为 4）；
    2. 用 :data:`constants.BAMBU_PROCESS_PRESET`（层高/线宽/墙/壳体/填充/
       速度/冲洗塔/Brim 等）覆盖，覆盖时按模板长度对齐数组；
    3. 若有 ``materials``，把 ``filament_colour`` 换成实际耗材色。**槽数 =
       实际耗材数**：超过模板的 4 槽时会先用 :func:`_extend_filament_arrays`
       把逐耗材数组扩到位（「平整色块板」可到 16 色 + 底板 = 17 槽），否则
       第 5 槽起没有档案、拓竹会把彩色压成 4 色。

    ``preset`` 传 dict 可替换覆盖层；传 ``None`` 用默认预设。

    v1.38.0 拓竹模式：
      * ``extra``          —— 面板暴露的工艺项（层高/墙/填充/Brim/支撑/擦料塔），
        普通键，覆盖时按模板数组长度对齐；
      * ``filament_extra`` —— 温度类**逐耗材**键，值给标量，这里按最终槽数
        展开成数组。**必须在扩槽之后**处理，否则 17 色的第 5 槽起没有温度值。
    """
    cfg = bambu_template()
    overlay = dict(BAMBU_PROCESS_PRESET if preset is None else preset)
    if extra:
        overlay.update(extra)
    per_fil = dict(filament_extra or {})

    for k, v in overlay.items():
        # 逐耗材温度单独走下面的展开逻辑，这里先跳过 —— 现在还不知道槽数
        if k in per_fil:
            continue
        cfg[k] = _align_value(cfg.get(k), v)

    if materials:
        base_cols = [_material_hex(m) for m in materials]
        n_old = len(cfg.get("filament_colour") or []) or len(base_cols)
        # 实际用到的耗材数 > 模板槽数（4）时先扩逐耗材数组 —— 否则第 5 槽起
        # 没有耗材档案，拓竹按 4 槽解析、彩色被静默压成 4 色（见
        # :func:`_extend_filament_arrays` 的实测记录）。
        _extend_filament_arrays(cfg, len(base_cols), n_old)
        n = max(n_old, len(base_cols))
        cols = [base_cols[i % len(base_cols)] for i in range(n)]
        cfg["filament_colour"] = cols
        if isinstance(cfg.get("filament_multi_colour"), list):
            cfg["filament_multi_colour"] = list(cols)

    if per_fil:
        n_slot = len(cfg.get("filament_colour") or []) or 1
        for k, val in per_fil.items():
            cur = cfg.get(k)
            # 模板里没有这个键就别凭空造：拓竹对未知键的容忍度未验证
            if isinstance(cur, list) and cur:
                cfg[k] = [str(val)] * len(cur)
            elif cur is not None:
                cfg[k] = str(val)
            else:
                continue
            overlay[k] = str(val)

    # v1.6.3：GUI 只按差异清单恢复工艺值（详见函数 docstring）
    _sync_different_settings(cfg, overlay)

    # 稳定输出：按键排序（拓竹原文件就是全字符串/字符串数组）
    return json.dumps(dict(sorted(cfg.items())), ensure_ascii=False, indent=1)


def export_3mf(filepath, bm, materials, title="叠色浮雕",
               split_by_material=True, color_guide=None,
               bambu_mode=True, extra_settings=None, filament_settings=None,
               extra_parts=None):
    """把 bmesh 写成 3MF 文件。返回统计信息 dict。

    ``bambu_mode=False``（v1.38.0）时**一个拓竹专用条目都不写**：
      不写 ``Metadata/model_settings.config``（部件↔耗材槽映射），
      不写 ``Metadata/project_settings.config``（切片工艺）。
    这样切片器完全用用户自己的预设，插件不替他做任何耗材/参数决定。
    此时 ``split_by_material`` 也没有意义（拆了部件却全指向 1 号槽只会误导），
    由调用方强制关掉。
    """
    verts, tris, tri_mats = extract_mesh_data(bm)
    if not tris:
        raise ValueError("网格为空，无法导出")

    xml, obj_ids, build_item = build_3mf_model_xml(
        verts, tris, tri_mats, materials, title=title,
        split_by_material=split_by_material,
        extra_parts=(extra_parts if split_by_material else None))

    used = []
    n_mat = max(1, len(materials))
    by_mat = group_by_material(verts, tris, tri_mats, n_mat)
    for mi in range(n_mat):
        if by_mat[mi]:
            used.append(mi)
    # obj_ids 的顺序 = 颜色部件（按材质升序）在前、额外部件在后，extruder
    # 映射必须按同一顺序续上，否则底板的耗材槽会指错（v1.0.15）。
    obj_used = used[:len(obj_ids)]
    _part_names = [str(materials[mi].get("name", materials[mi].get("key", "")))
                   for mi in obj_used]
    if split_by_material:
        for _ep in (extra_parts or []):
            if _ep.get("tris"):
                obj_used.append(max(0, int(_ep.get("mat", 0))))
                _part_names.append(str(_ep.get("name") or "底板"))

    settings = None
    project_settings = None
    if bambu_mode:
        # root_id 一律用 build_item —— 拆部件时是 assembly，不拆时就是那个
        # 唯一的 object（见 build_model_settings_config 的说明）
        settings = build_model_settings_config(obj_ids, materials, obj_used,
                                               root_id=build_item,
                                               part_names=_part_names)
        project_settings = build_project_settings(
            materials=materials, extra=extra_settings,
            filament_extra=filament_settings)

    os.makedirs(os.path.dirname(os.path.abspath(filepath)) or ".", exist_ok=True)
    with zipfile.ZipFile(filepath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", xml)
        if settings is not None:
            z.writestr("Metadata/model_settings.config", settings)
        if project_settings is not None:
            z.writestr("Metadata/project_settings.config", project_settings)
        if color_guide:
            z.writestr("Metadata/color_guide.txt", color_guide)

    return {
        "path": os.path.abspath(filepath),
        "vertices": len(verts),
        "triangles": len(tris),
        "materials_used": len(used),
        "objects": len(obj_ids),
        "bambu_mode": bool(bambu_mode),
        "has_settings": settings is not None,
        "has_project_settings": project_settings is not None,
        "bytes": os.path.getsize(filepath),
    }


# ---------------------------------------------------------------------------
# OBJ + MTL
# ---------------------------------------------------------------------------
def _has_non_ascii(path):
    try:
        str(path).encode("ascii")
        return False
    except UnicodeEncodeError:
        return True


def export_obj(filepath, bm, materials, write_materials=True,
               object_name="ColorStackRelief", extra_parts=None):
    """手写 OBJ（+ 可选 MTL）。

    自己写而不是用 ``bpy.ops.wm.obj_export`` 是为了完全掌控编码与
    材质行 —— 拓竹 GUI 对非 ASCII 路径下的材质引用有已知崩溃缺陷。
    """
    verts, tris, tri_mats = extract_mesh_data(bm)
    if not tris:
        raise ValueError("网格为空，无法导出")

    path = os.path.abspath(filepath)
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]

    non_ascii = _has_non_ascii(path)
    # v1.48.0 / R65：材质表为空时不写 .mtl（否则是一份没有 newmtl 的空表，
    # OBJ 里的 usemtl 全是悬空引用）
    use_mtl = bool(write_materials) and bool(materials)
    mtl_name = base + ".mtl"

    if use_mtl:
        mtl_lines = ["# 叠色浮雕工坊 —— 材质库", ""]
        for i, m in enumerate(materials):
            nm = _obj_safe_name(m.get("name", m.get("key", "mat%d" % i)))
            r, g, b = m.get("srgb", (1.0, 1.0, 1.0))
            mtl_lines.append("newmtl %s" % nm)
            mtl_lines.append("Ka 0.000 0.000 0.000")
            mtl_lines.append("Kd %.4f %.4f %.4f" % (r, g, b))
            mtl_lines.append("Ks 0.000 0.000 0.000")
            mtl_lines.append("d 1.0")
            mtl_lines.append("illum 1")
            mtl_lines.append("")
        with open(os.path.join(folder, mtl_name), "w", encoding="utf-8") as fh:
            fh.write("\n".join(mtl_lines))

    lines = []
    lines.append("# 叠色浮雕工坊 (ColorStack Relief) 导出")
    lines.append("# 顶点 %d / 三角面 %d" % (len(verts), len(tris)))
    if use_mtl:
        lines.append("mtllib %s" % mtl_name)
    lines.append("")
    for (x, y, z) in verts:
        lines.append("v %s %s %s" % (_fmt(x), _fmt(y), _fmt(z)))

    lines.append("")
    lines.append("o %s" % object_name)
    lines.append("")

    # v1.48.0 / R65：材质表为空时不能拿 len(materials)=0 去分组 ——
    # _group_triangles_by_material 会把所有三角形都并到 0 号桶，接下来
    # materials[0] 就是 IndexError（实测 export_obj(materials=[]) 直接崩）。
    # 可达路径：对象的 cstack_meta 被清掉、或用户手工删过材质槽，export_all
    # 退化成"从 me.materials 取名"，槽位为空就走到这里。3MF 侧一直有
    # max(1, ...) 兜底，OBJ 这条没有 —— 两条链路口径必须一致。
    n_mat = max(1, len(materials))
    contiguity = _group_triangles_by_material(tris, tri_mats, n_mat)
    for mi, indices in contiguity:
        # 没有材质表时不要写 usemtl（会引用 .mtl 里不存在的条目），
        # 退回用 group 行，至少 OBJ 本身还是合法的
        if use_mtl and materials:
            m = materials[mi] if 0 <= mi < len(materials) else {}
            nm = _obj_safe_name(m.get("name", m.get("key", "mat%d" % mi)))
            lines.append("usemtl %s" % nm)
        else:
            lines.append("g group_%d" % mi)
        for k in indices:
            a, b, c = tris[k]
            lines.append("f %d %d %d" % (a + 1, b + 1, c + 1))
        lines.append("")

    # ---- v1.0.15：额外部件（底板）单独一个 o 组 ----
    # OBJ 的顶点序号是全局的，所以先记住已有顶点数，再按偏移写 f。
    _voff = len(verts)
    for _ep in (extra_parts or []):
        _ev = _ep.get("verts") or []
        _et = _ep.get("tris") or []
        if not _et:
            continue
        lines.append("o %s" % (_ep.get("obj_name")
                               or _obj_safe_name(_ep.get("name", "base"))))
        for (x, y, z) in _ev:
            lines.append("v %s %s %s" % (_fmt(x), _fmt(y), _fmt(z)))
        _mi = max(0, int(_ep.get("mat", 0)))
        if use_mtl and materials:
            m = materials[_mi] if 0 <= _mi < len(materials) else {}
            lines.append("usemtl %s"
                         % _obj_safe_name(m.get("name", m.get("key", "mat"))))
        else:
            lines.append("g base")
        for (a, b, c) in _et:
            lines.append("f %d %d %d"
                         % (a + 1 + _voff, b + 1 + _voff, c + 1 + _voff))
        lines.append("")
        _voff += len(_ev)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))

    return {
        "path": path,
        "mtl_path": os.path.join(folder, mtl_name) if use_mtl else None,
        "vertices": len(verts),
        "triangles": len(tris),
        "groups": len(contiguity),
        "bytes": os.path.getsize(path),
        "non_ascii_path": non_ascii,
        "bambu_risk": bool(non_ascii and use_mtl),
    }


def _obj_safe_name(name):
    """OBJ/MTL 里只用 ASCII 标识符 —— 避免中文名踩拓竹导入器。"""
    out = []
    for ch in str(name):
        if ch.isascii() and (ch.isalnum() or ch in "_-."):
            out.append(ch)
        else:
            out.append("_")
    s = "".join(out).strip("_")
    return s or "material"


def _group_triangles_by_material(tris, tri_mats, material_count):
    """返回 ``[(material_index, [tri_index,...]), ...]``，按材质聚成连续块。"""
    buckets = {}
    for k, m in enumerate(tri_mats):
        if m < 0 or m >= material_count:
            m = 0
        buckets.setdefault(m, []).append(k)
    return sorted(buckets.items())


# ---------------------------------------------------------------------------
# 自检：回读 3MF
# ---------------------------------------------------------------------------
def verify_3mf(filepath):
    """解析刚写出的 3MF，返回结构统计（用于无头验证脚本断言）。"""
    import xml.etree.ElementTree as ET

    info = {"ok": False, "objects": 0, "materials": 0, "triangles": 0,
            "vertices": 0, "has_settings": False,
            "has_project_settings": False, "project_keys": 0, "errors": []}
    try:
        with zipfile.ZipFile(filepath, "r") as z:
            names = set(z.namelist())
            info["entries"] = sorted(names)
            for required in ("[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"):
                if required not in names:
                    info["errors"].append("缺少必需条目：%s" % required)
            if "Metadata/model_settings.config" in names:
                info["has_settings"] = True
            if "Metadata/project_settings.config" in names:
                info["has_project_settings"] = True
                cfg = json.loads(
                    z.read("Metadata/project_settings.config").decode("utf-8"))
                info["project_keys"] = len(cfg)
                for k, v in cfg.items():
                    if not isinstance(v, (str, list)):
                        info["errors"].append(
                            "工艺参数 %s 的值不是字符串/数组：%r" % (k, v))
            xml = z.read("3D/3dmodel.model")
            root = ET.fromstring(xml)
            ns = {"m": THREEMF_CORE_NS}
            resources = root.find("m:resources", ns)
            if resources is None:
                info["errors"].append("没有 resources 节点")
            else:
                bases = resources.findall(".//m:base", ns)
                info["materials"] = len(bases)
                objs = resources.findall("m:object", ns)
                info["objects"] = len(objs)
                for o in objs:
                    mesh = o.find("m:mesh", ns)
                    if mesh is None:
                        continue
                    info["vertices"] += len(mesh.findall(".//m:vertex", ns))
                    info["triangles"] += len(mesh.findall(".//m:triangle", ns))
                if info["triangles"] == 0:
                    info["errors"].append("没有任何三角形")
            info["ok"] = not info["errors"]
    except Exception as exc:  # noqa: BLE001
        info["errors"].append("解析失败：%r" % (exc,))
    return info


def verify_obj(filepath):
    """解析 OBJ：统计 v / f / usemtl，并检查索引合法性。"""
    info = {"ok": False, "vertices": 0, "faces": 0, "groups": 0, "errors": []}
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("v "):
                    info["vertices"] += 1
                elif line.startswith("f "):
                    info["faces"] += 1
                    for tok in line.split()[1:]:
                        idx = int(tok.split("/")[0])
                        if idx < 1 or idx > max(1, info["vertices"]):
                            info["errors"].append("面索引越界：%s" % tok)
                            break
                elif line.startswith("usemtl "):
                    info["groups"] += 1
        info["ok"] = (info["faces"] > 0 and not info["errors"])
    except Exception as exc:  # noqa: BLE001
        info["errors"].append("读取失败：%r" % (exc,))
    return info
