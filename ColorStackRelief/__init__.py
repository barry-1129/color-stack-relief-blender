# -*- coding: utf-8 -*-
"""叠色浮雕工坊 (ColorStack Relief) —— Blender 插件入口。

导入图片 → 自动分析颜色 → 按 CMYW 叠色原理建模 → 导出 3MF / OBJ。

作者：barry
许可证：GPL-3.0-or-later
"""

bl_info = {
    "name": "叠色浮雕工坊 ColorStack Relief",
    "author": "barry / 尼罗河的泥螺",
    "version": (1, 0, 17),
    "blender": (4, 2, 0),
    "location": "3D 视图 > 侧栏(N) > 叠色浮雕",
    "description": "导入图片，按品红/黄/天蓝/白叠色原理自动建模，导出可打印 3MF 与 OBJ",
    "category": "Mesh",
    "doc_url": "",
    "tracker_url": "",
}

import bpy as _bpy

from . import constants as _C          # noqa: E402
from . import i18n as _i18n            # noqa: E402
from . import props as _props          # noqa: E402
from . import operators as _operators  # noqa: E402
from . import panels as _panels        # noqa: E402

__version__ = _C.ADDON_VERSION_STR

# 汇总所有需要注册的类（无头体检脚本会遍历它逐个 register_class）
classes = tuple(_props.classes) + tuple(_operators.classes) + tuple(_panels.classes)

# 会话级版本标记（v1.4.3 新增）。
#
# 【为什么必须有它】Blender 在**同一次会话**里"移除 → 重装"扩展时，旧版代码
# 已经 import 进 sys.modules，重装**不会**重新加载模块 —— 界面看起来装好了，
# 实际运行的仍是旧版（2026-09-13 实机事故：装完 v1.4.2 仍弹旧版 v1.4.0 的
# "float division by zero"）。这个标记挂在 bpy.app.driver_namespace 上，
# 它的生命周期 = 整个 Blender 会话，跨越扩展的移除/重装，正好用来侦测残留。
_MARKER = "CSTACK_LOADED_VERSION"
_STALE = "CSTACK_STALE_FROM"


def _check_stale_runtime():
    """记录本次会话实际加载的版本；发现旧版残留就警告并留下面板标记。"""
    try:
        ns = _bpy.app.driver_namespace
    except Exception:  # noqa: BLE001
        return
    prev = ns.get(_MARKER)
    ns[_MARKER] = _C.ADDON_VERSION_STR
    if prev and prev != _C.ADDON_VERSION_STR:
        ns[_STALE] = prev
        print()
        print("=" * 62)
        print("[叠色浮雕工坊] ⚠ 检测到旧版本残留！")
        print("  本次会话之前运行的是 v%s，重装扩展不会替换" % prev)
        print("  已加载进内存的旧代码 —— 现在面板/按钮跑的仍是旧逻辑。")
        print("  请**完全退出并重启 Blender**，新版本才会真正生效。")
        print("=" * 62)
        print()


def stale_runtime_from():
    """面板用：本会话是否存在旧版残留，返回旧版本号或 None。"""
    try:
        return _bpy.app.driver_namespace.get(_STALE)
    except Exception:  # noqa: BLE001
        return None


def _sync_language(*_args):
    """载入 .blend 后把 i18n 全局同步成文件里保存的语言选择。

    【v1.16.1】只改 I.LANG 不够 —— 注册期文案（面板 bl_label、属性 name/
    description）是按 register 时的默认语言（中文）构建的，必须再排一次
    延迟重注册，重启 Blender 后英文设置才能真正完整生效（含走 BLT 崩溃护栏）。
    """
    try:
        ctx = _bpy.context
        if ctx.scene is not None and hasattr(ctx.scene, "cstack"):
            _i18n.set_lang(ctx.scene.cstack.language)
            _i18n.request_retranslate()
    except Exception:  # noqa: BLE001
        pass


def register():
    _check_stale_runtime()
    _props.register()
    _operators.register()
    _panels.register()
    if _sync_language not in _bpy.app.handlers.load_post:
        _bpy.app.handlers.load_post.append(_sync_language)


def unregister():
    if _sync_language in _bpy.app.handlers.load_post:
        _bpy.app.handlers.load_post.remove(_sync_language)
    _panels.unregister()
    _operators.unregister()
    _props.unregister()
