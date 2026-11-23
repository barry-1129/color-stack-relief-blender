# -*- coding: utf-8 -*-
"""生成进度条（v1.0.16，用户 2026-11-21 第 3 条）。

用户原话：「在执行生成模型时，在右下角添加类似于 3D 轨迹的作业进度条，
来体现执行进度」。

【为什么不用 3D 轨迹那套独立小窗】
`TrailPrint3D` 是**另起一个进程**（progress_win.py 开本地 HTTP 服务 + 浏览器
--app 窗口）来画进度，因为它的生成是同步阻塞的、视口画不出来。那套的代价是
多一个进程、一个浏览器窗口、一个临时 JSON 文件，装插件的人还得有 Edge/Chrome。
本插件只需要一根进度条，用 Blender **自带的状态栏进度**更轻：
``wm.progress_begin/update/end`` 就画在窗口底部状态栏（右下角那条），
配合 ``workspace.status_text_set`` 写当前阶段文字。

【必须强制重绘】
生成算子在主线程里一口气跑完，事件循环不转 → 状态栏不会自己重画。
所以这里按 ~5Hz 调 ``wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)``
把窗口刷一遍（3D 轨迹的视口浮层用的也是这一招）。节流是为了别把生成拖慢。

【无头模式】
``bpy.app.background`` 下没有窗口，``wm.progress_begin`` 会直接抛
``ValueError: 1-2 args execution context is supported``（实测）。所以后台
只往 stdout 打稀疏进度（每 10%），**不碰任何 UI**。探针因此可以随便调。

【绝不能因为进度条把生成搞挂】
所有 UI 调用都包在 try/except 里：画不出来就静默退化成"只有控制台"，
生成本身照常跑。
"""

import time

import bpy

from . import i18n as I

# 两次 UI 重绘之间的最小间隔（秒）。0.2 s ≈ 5 Hz，肉眼看是连续的，
# 又不会让整窗重绘吃掉太多生成时间。
_MIN_REDRAW_DT = 0.20
# 进度至少变化这么多才值得刷一次（避免同一百分比内空转）。
_MIN_STEP = 0.004
# 控制台打点的最小间隔（进度差），10% 一行。
_CONSOLE_STEP = 0.10


class _Reporter:
    def __init__(self):
        self.active = False
        self.title = ""
        self._bar_open = False
        self._frac = -1.0
        self._t_last = 0.0
        self._console_frac = -1.0
        self._t0 = 0.0

    # -- public ----------------------------------------------------------
    def start(self, title="生成模型"):
        self.finish()
        self.active = True
        self.title = title
        self._frac = -1.0
        self._t_last = 0.0
        self._console_frac = -1.0
        self._t0 = time.time()
        if bpy.app.background:
            print("[cstack] %s…" % I.t(title))
            return
        try:
            bpy.ops.wm.progress_begin(0, 1000)
            self._bar_open = True
        except Exception:  # noqa: BLE001 - 没有可用窗口时退化
            self._bar_open = False
        self._paint(0.0, "", force=True)

    def update(self, frac, label=""):
        if not self.active:
            return
        try:
            frac = float(frac)
        except (TypeError, ValueError):
            return
        if frac < 0.0:
            frac = 0.0
        elif frac > 1.0:
            frac = 1.0

        now = time.time()
        # 控制台：无头 / 有头都打，10% 一行，方便回归日志里看阶段
        if frac - self._console_frac >= _CONSOLE_STEP or frac >= 1.0:
            self._console_frac = frac
            print("[cstack] %s %d%%%s"
                  % (I.t(self.title), int(frac * 100),
                     (" · " + str(label)) if label else ""))

        if bpy.app.background:
            return
        if (frac - self._frac) < _MIN_STEP and (now - self._t_last) < _MIN_REDRAW_DT:
            return
        self._paint(frac, label)

    def finish(self):
        was_active = self.active
        self.active = False
        if self._bar_open:
            try:
                bpy.ops.wm.progress_end()
            except Exception:  # noqa: BLE001
                pass
            self._bar_open = False
        if was_active and not bpy.app.background:
            self._clear_status()
            self._redraw()

    # -- internals -------------------------------------------------------
    def _paint(self, frac, label, force=False):
        now = time.time()
        if not force and (now - self._t_last) < _MIN_REDRAW_DT:
            return
        self._frac = frac
        self._t_last = now
        if self._bar_open:
            try:
                bpy.ops.wm.progress_update(int(round(frac * 1000.0)))
            except Exception:  # noqa: BLE001
                self._bar_open = False
        # 状态栏文字：标题 + 百分比 + 当前阶段
        txt = "%s  %d%%" % (I.t(self.title), int(frac * 100))
        if label:
            txt += " · " + str(label)
        try:
            bpy.context.workspace.status_text_set(txt)
        except Exception:  # noqa: BLE001
            pass
        self._redraw()

    @staticmethod
    def _clear_status():
        try:
            bpy.context.workspace.status_text_set(None)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _redraw():
        """强制把窗口刷一遍 —— 否则阻塞中的算子不会重画状态栏。"""
        try:
            wm = bpy.context.window_manager
            win = bpy.context.window or (wm.windows[0] if len(wm.windows) else None)
            if win is None:
                return
            area = None
            for a in win.screen.areas:
                if a.type == "VIEW_3D":
                    area = a
                    break
            if area is None and len(win.screen.areas):
                area = win.screen.areas[0]
            if area is None:
                return
            region = None
            for r in area.regions:
                if r.type == "WINDOW":
                    region = r
                    break
            with bpy.context.temp_override(window=win, area=area, region=region):
                bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
        except Exception:  # noqa: BLE001
            pass


_R = _Reporter()


def start(title="生成模型"):
    """开始一次生成进度（重复调用会先把上一次收干净）。"""
    _R.start(title)


def update(frac, label=""):
    """0~1 的进度 + 当前阶段文字；越界自动钳回，非数字直接忽略。"""
    _R.update(frac, label)


def finish():
    _R.finish()


def active():
    return bool(_R.active)


def stage(base, label, span=0.0):
    """把某一段子任务映射到总进度的 [base, base+span] 区间。

    例：``compute_region(..., progress=stage(0.25, "逐格配色", 0.30))`` ——
    子任务报 0~1，这里换算成总进度的 25%~55%。
    """
    b = float(base)
    s = float(span)

    def _cb(frac):
        try:
            f = float(frac)
        except (TypeError, ValueError):
            return
        if f < 0.0:
            f = 0.0
        elif f > 1.0:
            f = 1.0
        update(b + s * f, label)

    return _cb
