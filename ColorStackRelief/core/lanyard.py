# -*- coding: utf-8 -*-
"""挂绳孔（v1.15.0）—— 生成后的网格后处理。

两种形态（位置都在**照片顶部居中**，即模型顶边中点；内孔半径 1.5mm、
外环半径 2.5mm，均为常量，见 constants）：

* ``external`` 外置：一个圆环**凸出在模型顶边外**并与模型融合成一体。
  环心放在 ``y_max + 内孔半径`` —— 内孔恰好与模型顶边相切（孔完全敞开），
  外环有 1.0mm 深的月牙嵌进模型，融合后就是经典的挂牌挂耳。
  圆环高度 = 挂绳孔厚度参数，且**环底与模型底面（z 最小）齐平** ——
  摆放 / 打印时挂耳贴着底面，不悬空。
* ``internal`` 内置：在模型内部**打通一个竖直圆孔**（Z 向贯穿），孔心在
  顶边往下 ``外环半径 + 余量`` 处 —— 保证孔壁到顶边至少留 1mm 实料。

【布尔求解器】统一用 Blender 的 ``BOOLEAN`` 修改器 + ``MANIFOLD`` 求解器
（manifold3d），应用方式走 ``evaluated_depsgraph`` + ``new_from_object``
—— **不经 bpy.ops**，无头模式（--background 探针）同样可跑可验证。

【模式支持】v1.16.3 起 relief / card / single 三种模式都可用（mosaic 于
v1.22.0 取代 single 后同样可用；布尔对多色壳
叠合网格同样成立，MANIFOLD 求解器自会求交）。挂绳孔**只能**通过
「生成挂绳孔」按钮在已生成模型上添加，生成模型流程不再自动加孔。
"""

import json

import bmesh
import bpy

from math import cos, sin, pi

from mathutils.bvhtree import BVHTree

from .. import constants as C


def _snapshot(obj):
    """布尔前的网格快照（顶点 / 多边形 / 逐面材质），供刀口面回填材质。"""
    me = obj.data
    verts = [v.co.copy() for v in me.vertices]
    polys = [tuple(p.vertices) for p in me.polygons]
    mats = [p.material_index for p in me.polygons]
    return verts, polys, mats


def _sample_material(bvh, mats, p, radius=0.40):
    """点 p 落在哪个材质里 —— 用刀前网格的最近面判定。

    【为什么要处理"重合面"】本插件的模型是**每色一个封闭壳**：两个颜色交界
    处有两张几何重合、法线相反的面（各属一个壳）。最近面查询会在两张里随便
    返回一张，直接取它就会把白色刀口面判成邻居的颜色。判据：采样点在实体
    内部，**法线背离采样点**的那张面才属于该实体（另一张法线朝向采样点）。
    """
    cand = bvh.find_nearest_range(p, radius)
    if not cand:
        return None
    best = None
    for loc, nrm, idx, dist in cand:
        facing = nrm.dot(p - loc)
        key = (round(dist, 4), 0 if facing < 0.0 else 1)
        if best is None or key < best[0]:
            best = (key, mats[idx] if idx < len(mats) else None)
    return best[1]


def _reassign_cut_materials(obj, snap, region, tol=0.05):
    """把布尔新生成的面（默认材质 0）改回**它所贴着的那块实体**的材质。

    【为什么必须做】v1.32.0（R39）实测：MANIFOLD 布尔切出来的圆柱壁全部落在
    材质 0 上，于是被切到的其它颜色壳各自少了一圈面 → 逐壳数边界边从 0 变成
    228（白色壳直接从 8608 面掉到 192 面、只剩 456 条边界边）。导出 3MF 后
    每个颜色是一个 component，Bambu 读到"破洞"就会自动修复 —— 修复很容易
    把刚打的挂绳孔重新封上，这正是用户报的"切片后孔还是堵的"。
    回填之后每个壳重新闭合（逐壳边界边回到 0），孔也就保住了。
    """
    verts, polys, mats = snap
    bvh = BVHTree.FromPolygons(verts, polys, all_triangles=False)
    changed = 0
    for p in obj.data.polygons:
        c = p.center
        if not region(c):
            continue
        # 沿法线反向退 0.08mm —— 刀口面的法线朝孔（空气侧），反向才是实体
        probe = c - p.normal * 0.08
        m = _sample_material(bvh, mats, probe)
        if m is not None and m != p.material_index:
            p.material_index = m
            changed += 1
    return changed


def _bbox(obj):
    """世界包围盒（生成物体恒在原点、无变换，local==world）。"""
    xs = [v.co.x for v in obj.data.vertices]
    ys = [v.co.y for v in obj.data.vertices]
    zs = [v.co.z for v in obj.data.vertices]
    if not xs:
        raise ValueError("空网格")
    return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))


def _ring_mesh(name, cx, cy, z0, z1, r_in, r_out, segments, mat_index=0):
    """建一个竖直圆环（管）网格：内半径 r_in、外半径 r_out，z 从 z0 到 z1。

    手工建面（外圈/内圈侧面 + 上下环形端面），闭合流形；法线用
    recalc_face_normals 统一朝外。``mat_index`` = 挂耳用哪种耗材 ——
    v1.32.0 起传基底色的下标（原来是硬编码 0，材质表第一位往往是青色，
    挂耳会莫名其妙变成青色）。
    """
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    ring_o = []
    ring_i = []
    for i in range(segments):
        a = 2.0 * pi * i / segments
        ca, sa = cos(a), sin(a)
        ring_o.append(bm.verts.new((cx + r_out * ca, cy + r_out * sa, z0)))
        ring_i.append(bm.verts.new((cx + r_in * ca, cy + r_in * sa, z0)))
    top_o = [bm.verts.new((v.co.x, v.co.y, z1)) for v in ring_o]
    top_i = [bm.verts.new((v.co.x, v.co.y, z1)) for v in ring_i]
    bm.verts.ensure_lookup_table()
    for i in range(segments):
        j = (i + 1) % segments
        # 底面（法线 -z）：外环逆序、内环顺序
        bm.faces.new((ring_o[j], ring_o[i], ring_i[i], ring_i[j]))
        # 顶面（法线 +z）
        bm.faces.new((top_o[i], top_o[j], top_i[j], top_i[i]))
        # 外壁 / 内壁
        bm.faces.new((ring_o[i], ring_o[j], top_o[j], top_o[i]))
        bm.faces.new((ring_i[j], ring_i[i], top_i[i], top_i[j]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    for f in bm.faces:
        f.material_index = int(mat_index)
    bm.to_mesh(me)
    bm.free()
    return me


def _cyl_mesh(name, cx, cy, z_mid, r, depth, segments):
    """实心圆柱切割器（贯穿模型用，上下各多出 1mm 余量）。"""
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False,
                          segments=segments, radius1=r, radius2=r,
                          depth=depth)
    bmesh.ops.translate(bm, verts=bm.verts[:],
                        vec=(cx, cy, z_mid))
    bm.to_mesh(me)
    bm.free()
    return me


def _boolean_apply(obj, cutter, operation):
    """对 obj 应用布尔修改器并**落成实网格**（不经 bpy.ops，无头可跑）。

    流程：加 BOOLEAN(MANIFOLD) 修改器 → depsgraph 求值 → new_from_object
    取结果网格 → 换掉 obj.data → 删修改器与切割物。

    v1.48.0（R65）：删除修改器放进 ``finally``。原先只有成功路径会删，
    求值/取网格抛错时 BOOLEAN 修改器会留在对象上、且仍指向调用方马上要删掉的
    切割物 —— 用户之后再导出/再生成，对象就带着一个指向已删除物体的失效修改器
    （表现为导出报错或整个网格消失），而错误现场早已被 finally 清理掉，很难查。
    """
    view_layer = bpy.context.view_layer
    mod = obj.modifiers.new(name="CSTACK_LANYARD", type="BOOLEAN")
    mod.object = cutter
    mod.operation = operation          # 'UNION' | 'DIFFERENCE'
    mod.solver = "MANIFOLD"
    mod.show_viewport = True
    mod.show_render = True
    old_me = obj.data
    try:
        view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(dg)
        new_me = bpy.data.meshes.new_from_object(eval_obj)
        obj.data = new_me
    finally:
        # 按名字重查一次：obj.data 换过之后，不要依赖旧的 mod 指针
        leftover = obj.modifiers.get(mod.name)
        if leftover is not None:
            obj.modifiers.remove(leftover)
    if old_me.users == 0:
        bpy.data.meshes.remove(old_me)


def _spawn_temp(obj, me):
    """把切割/融合网格放进 obj 所在集合（布尔修改器要求它在 depsgraph 里）。"""
    coll = (obj.users_collection[0] if obj.users_collection
            else bpy.context.scene.collection)
    ob = bpy.data.objects.new(me.name, me)
    coll.objects.link(ob)
    return ob


def _remove_temp(ob):
    me = ob.data
    bpy.data.objects.remove(ob, do_unlink=True)
    if me.users == 0:
        bpy.data.meshes.remove(me)


def _base_material_index(obj):
    """模型里「基底色」的材质下标（挂耳用它；找不到就 0）。"""
    try:
        meta = json.loads(obj.get("cstack_meta", "") or "{}")
    except Exception:  # noqa: BLE001
        return 0
    bkey = meta.get("base_key")
    for i, mm in enumerate(meta.get("materials") or []):
        if mm.get("key") == bkey:
            return i
    return 0


def _append_object_mesh(obj, other):
    """把另一个物体的网格**追加**进 obj（不布尔、不焊接）。

    【为什么外置挂绳环不能用 UNION】布尔求并会把外环与模型相交处的那一圈
    模型表面**删掉**，却不给被删的那几个颜色壳补面 —— 实测每个颜色壳各留
    下 40~49 条边界边，导出后又变成"破洞"，Bambu 一修复孔就没了。
    外环本来就有 1mm 月牙嵌进模型（体积重叠），切片器自己会求并；追加进来
    后两个壳都还是闭合的，孔和外形都稳。
    """
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.from_mesh(other.data)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def _is_open(obj, cx, cy, r_in):
    """孔是否真的贯通：从模型下方竖直射线穿过孔心与孔内 4 个采样点。"""
    z0 = min(v.co.z for v in obj.data.vertices) - 1.0
    pts = [(0.0, 0.0)]
    for a in range(4):
        ang = 2.0 * pi * a / 4.0
        pts.append((0.8 * r_in * cos(ang), 0.8 * r_in * sin(ang)))
    for dx, dy in pts:
        ok, loc, nrm, idx = obj.ray_cast((cx + dx, cy + dy, z0), (0, 0, 1))
        if ok:
            return False
    return True


def _clean_zero_area(obj, eps=None):
    """清掉布尔切割留下的零面积面（共线四边形）。

    【为什么需要】圆柱贯穿一路切开色膜层的水平界面时，Blender 布尔会产生
    "四点共线"的四边形：面积恰为 0，但边长不为 0，``dissolve_degenerate``
    单独用清不掉（实测 dist 1e-3 也无效）。这类面是切片软件闪退的已知诱因
    （v1.48.0 实测 96 个 → Bambu 闪退）。
    【做法】只把零面积面及其 1 环三角化，再对这批边做 dissolve —— 面数影响
    极小；实测 boundary / 跨材质非流形仍为 0（全模型三角化在 rim 4 层时会
    制造 5 条跨材质非流形，所以必须局部做）。
    """
    # ⚠ C.EPS_AREA2 是**面积平方**（count_degenerate_faces 的契约），
    # 换算成面积要开方（1e-10 → 1e-5 mm²），否则只抓得到面积精确为 0 的面。
    eps = float(C.EPS_AREA2 if eps is None else eps) ** 0.5
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bad = [f for f in bm.faces if f.calc_area() <= eps]
    if not bad:
        bm.free()
        return 0
    faces = set()
    edges = set()
    for f in bad:
        faces.add(f)
        for e in f.edges:
            edges.add(e)
            for g in e.link_faces:
                faces.add(g)
                for e2 in g.edges:
                    edges.add(e2)
    n = len(bad)
    bmesh.ops.triangulate(bm, faces=list(faces))
    bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=list(edges))
    bm.to_mesh(me)
    bm.free()
    me.update()
    return n


def apply_lanyard(obj, kind, inner_r=None, outer_r=None, thickness_mm=None):
    """给生成物体加挂绳孔。返回信息 dict（含位置与半径，供 meta/报告）。

    inner_r / outer_r / thickness_mm 均可覆盖常量默认值（来自面板参数）。
    thickness 仅对**外置**生效（外置圆环的高度）；内置孔始终 Z 向贯穿。
    厚度为 0 或 ≥ 模型厚度时取模型厚度。

    抛异常 = 失败（调用方捕获后降级为提示，不阻塞主流程）。
    """
    x0, x1, y0, y_max, z0, z1 = _bbox(obj)
    cx = (x0 + x1) * 0.5
    # v1.0.15：底板是独立对象、位于主对象下方。挂耳要"贴底面"，必须用**整件**
    # 的最低点，否则外置圆环会从底板顶面（0.40）才开始，整体悬空 0.4mm。
    _z_all_lo = z0
    try:
        for _ch in obj.children_recursive:
            if _ch.type == "MESH" and _ch.get("cstack_part") == "base":
                _z_all_lo = min(_z_all_lo, _bbox(_ch)[4])
    except Exception:  # noqa: BLE001
        pass
    r_in = float(inner_r if inner_r is not None else C.LANYARD_INNER_R_MM)
    r_out = float(outer_r if outer_r is not None else C.LANYARD_OUTER_R_MM)
    seg = int(C.LANYARD_SEGMENTS)
    # v1.0.15：底板拆成独立对象后，主对象的 z0 不再是整件最低点（底板更低）。
    # 厚度必须按**整件**算，否则"厚 3.0 ≥ 模型厚"的收敛判断会偏小 0.4mm。
    model_th = z1 - _z_all_lo
    if model_th <= 0.1:
        raise ValueError("模型厚度不足（%.2f mm）" % model_th)

    # ---- 半径合法性（v1.48.0 / R65）----
    # 面板上「内孔半径」「外环半径」是两个独立滑杆，代码原先不校验谁大谁小。
    # 外置圆环一旦「外环 ≤ 内孔」，`_ring_mesh` 就按「外圈比内圈小」建面，
    # 四条边全部反向外翻（自相交的蝴蝶结）；实测两种坏结果：
    #   内 6.0 / 外 1.0 → 孔不贯通，体积反而从 2425 涨到 2619 mm³（实心饼）；
    #   内 2.5 / 外 2.5 → 孔通了，但多出 96 个零面积面（切片软件闪退元凶）。
    # 校验必须放在算子这一层，不能只靠面板 min —— 旧 .blend 里存着非法组合时，
    # 面板范围保护不了。内置孔没有圆环，约束不同（见下方 cy 处的顶边检查）。
    if r_in < 0.2:
        raise ValueError("内孔半径太小（%.2f mm），至少 0.2mm" % r_in)
    _wall_min = float(C.LANYARD_MIN_WALL_MM)
    if kind == "external" and r_out <= r_in + _wall_min - 1e-9:
        raise ValueError(
            "外环半径必须比内孔半径大至少 %.1fmm（现在内 %.2f / 外 %.2f）。"
            "否则圆环会自己压住自己，切出来的孔是堵的。"
            % (_wall_min, r_in, r_out))

    # 厚度收敛：外置环高度；内置孔始终贯通
    if thickness_mm is None or thickness_mm <= 0 or thickness_mm >= model_th:
        th = model_th
    else:
        th = float(thickness_mm)

    if kind == "internal":
        cy = y_max - (r_out + float(C.LANYARD_EDGE_MARGIN_MM))
        if cy - r_in <= y0 + 0.2:
            raise ValueError("模型太小，放不下内置挂绳孔")
        # v1.48.0（R65）：顶边侧的对称检查。外环半径只是「孔心离顶边多远」的
        # 基准，用户可以把它调得比内孔还小 —— 那样孔顶会冲出模型顶边，得到的
        # 不是一个孔而是把整条边啃掉的缺口（`_is_open` 还会误判成"通了"）。
        if cy + r_in >= y_max - 0.2:
            raise ValueError(
                "内置孔会冲出模型顶边（外环半径 %.2f 比内孔半径 %.2f 小太多）。"
                "请把外环半径调到至少 %.2fmm。"
                % (r_out, r_in, r_in + float(C.LANYARD_EDGE_MARGIN_MM) - 0.2))

        # v1.0.15：底板是**独立对象**（z 从 0 到 0.4），只切主对象的话孔会被
        # 底板堵死。这里把同一根圆柱对所有"深度上要穿过"的对象各切一次 ——
        # 主对象 + 底板子对象，圆柱沿两者的合并 z 范围取，保证一次打穿。
        _targets = [obj]
        try:
            for _ch in obj.children_recursive:
                if _ch.type == "MESH" and _ch.get("cstack_part") == "base":
                    _targets.append(_ch)
        except Exception:  # noqa: BLE001 —— 老对象没有 children_recursive
            pass
        _zs = []
        for _t in _targets:
            _b = _bbox(_t)
            _zs.extend([_b[4], _b[5]])
        # 底板是**单色**板：布尔新面会拿到 material_index 0（第一种颜色），
        # 孔壁上就会露出一圈彩色。切之前记住它的材质索引，切完整块回填。
        _base_mi = {}
        for _t in _targets[1:]:
            if _t.data.polygons:
                _base_mi[_t.name] = int(_t.data.polygons[0].material_index)
        _z_lo, _z_hi = min(_zs), max(_zs)
        snap = _snapshot(obj)
        # 每个对象一根独立圆柱（_remove_temp 会把没人用的网格一起删掉，
        # 共用一份网格会在第二次 spawn 前就失效）。
        _cutters = []
        for _t in _targets:
            _m = _cyl_mesh("CSTACK_LANYARD_CUT", cx, cy,
                           (_z_lo + _z_hi) * 0.5, r_in,
                           (_z_hi - _z_lo) + 2.0, seg)
            _cutters.append((_t, _spawn_temp(_t, _m)))
        try:
            for _t, _c in _cutters:
                _boolean_apply(_t, _c, "DIFFERENCE")
        finally:
            for _t, _c in _cutters:
                _remove_temp(_c)
        for _t in _targets[1:]:
            _mi = _base_mi.get(_t.name)
            if _mi is not None:
                for _p in _t.data.polygons:
                    _p.material_index = _mi
        r_lim = r_in + 0.45

        def _region(c, cx=cx, cy=cy, r_lim=r_lim):
            return ((c.x - cx) ** 2 + (c.y - cy) ** 2) <= r_lim * r_lim

        fixed = _reassign_cut_materials(obj, snap, _region)
        # v1.0.14：布尔切过色膜水平界面会留下零面积四边形，必须清掉
        # （Bambu 对这类面敏感）。材质回填已完成，三角化只继承索引。
        cleaned = _clean_zero_area(obj)
        for _t in _targets[1:]:
            _clean_zero_area(_t)
        return {"kind": "internal", "center": (round(cx, 3), round(cy, 3)),
                "inner_r": r_in, "outer_r": r_out,
                "cut_faces_fixed": fixed,
                "degenerate_cleaned": cleaned,
                "through_objects": len(_targets),
                "open": _is_open(obj, cx, cy, r_in),
                "z_span": (round(_z_lo, 3), round(_z_hi, 3))}

    if kind == "external":
        cy = y_max + r_in   # 内孔与顶边相切：孔完全敞开，外环嵌进模型
        # v1.0.15：从**整件**底面（含底板）起，环才真的压在打印床/基准面上。
        z_bot = _z_all_lo   # 环底与整件底面齐平（不悬空）
        z_top = _z_all_lo + th  # 环高度 = th（0 / 超厚时已收敛为整件全厚）
        me = _ring_mesh("CSTACK_LANYARD_RING", cx, cy, z_bot, z_top,
                        r_in, r_out, seg, mat_index=_base_material_index(obj))
        ring = _spawn_temp(obj, me)
        try:
            _append_object_mesh(obj, ring)
        finally:
            _remove_temp(ring)
        return {"kind": "external", "center": (round(cx, 3), round(cy, 3)),
                "inner_r": r_in, "outer_r": r_out,
                "cut_faces_fixed": 0,
                "open": _is_open(obj, cx, cy, r_in),
                "z_span": (round(z_bot, 3), round(z_top, 3))}

    raise ValueError("未知挂绳孔类型：%s" % kind)
