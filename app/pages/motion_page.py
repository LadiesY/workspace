# -*- coding: utf-8 -*-
"""运动控制页（「设备管理」模块里的第二个子页签）。

原来它是左侧导航的第 4 项，现在并进「设备管理」，页头和子页签由
pages/device_module.py 统一画，本页只负责内容。

这里只干一件事：让轴动起来。设备管理子页负责「连上、调参数」，
这一页负责「点动、定位、回零、急停」，两边不重复。

**连接不在这里做**：连上/断开是设备管理的职责，这里只用状态点告诉你能不能操作
（绿点＝已连接、红点＝未连接、黄点＝连接中）。想连设备就切到左边的「设备管理」
页签，两处都能连迟早会出现「这边断了那边还以为连着」的糊涂账。

页面结构：

    [运动控制]  模块标题（由 device_module 画）
    ┌ 面板 ────────────────────────────────────────────────┐
    │ 三轴控制器 ● │ 探针座 ○     未连接 · 切到【设备管理】页签连接 │
    │ ──────────────────────────────────────────────────── │
    │ 点动步长 + 快捷键                                     │
    │ 状态横幅（运动中 / 限位 / 未回零）                       │
    │ 轴表：轴 当前位置 当前速度 目标 − + 移动 回零 状态        │
    │ 全部回零  全部停止                     ■ 急 停          │
    │ ──────────────────────────────────────────────────── │
    │ 运行日志                                              │
    └──────────────────────────────────────────────────────┘

两台设备各自有一份独立的 JogPanel 和一根轮询线程，切到哪台就只轮询哪台 ——
没选中的那台连 DLL 都不碰。
"""

from PyQt5.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                             QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

from .. import theme
from ..devices import DEVICE_META, store
from ..devices.base import ST_CONNECTED, ST_CONNECTING, ST_ERROR
from ..widgets import HEAD_H, HEAD_TOP, Panel, _font, _q, paint_module_head
from .jog_panel import JogPanel

# 这一页能操作的设备（相机没有运动轴）
MOTION_KEYS = ("motion", "probe")


class DeviceTab(QWidget):
    """顶部设备切换块：状态点 + 名称 + 一行连接信息。"""

    clicked = pyqtSignal(str)

    def __init__(self, key, parent=None):
        super(DeviceTab, self).__init__(parent)
        self.key = key
        self.meta = DEVICE_META[key]
        self._active = False
        self._hover = False
        self._state = ""
        self._detail = ""
        self.setFixedSize(212, 46)
        self.setCursor(Qt.PointingHandCursor)

    def set_active(self, on):
        if self._active != bool(on):
            self._active = bool(on)
            self.update()

    def set_state(self, state, detail=""):
        if (state, detail) != (self._state, self._detail):
            self._state, self._detail = state, detail
            self.update()
        # 这一页不能连设备，状态点就是唯一线索 —— 鼠标停上去得说清是什么状态
        tip = {ST_CONNECTED: "已连接",
               ST_CONNECTING: "连接中…",
               ST_ERROR: "连接失败：切到【设备管理】页签重连"}.get(
                   state, "未连接：切到【设备管理】页签连接")
        if detail:
            tip += "　" + detail
        self.setToolTip(tip)

    def enterEvent(self, _e):
        self._hover = True
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.key)
        super(DeviceTab, self).mouseReleaseEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        if self._active:
            p.setPen(QPen(_q("#1b57a0"), 1))
            p.setBrush(_q(theme.AMBER, 26))
        elif self._hover:
            p.setPen(QPen(_q("#c9dcec"), 1))
            p.setBrush(QColor(27, 87, 160, 12))
        else:
            p.setPen(QPen(_q("#e4eef7"), 1))
            p.setBrush(_q("#f2f7fb"))
        p.drawRoundedRect(r, 3, 3)

        # 状态点
        if self._state == ST_CONNECTED:
            col = theme.OK
        elif self._state == ST_CONNECTING:
            col = theme.WARN
        else:
            col = theme.BAD
        c = QColor(col)
        p.setPen(Qt.NoPen)
        glow = QColor(c)
        glow.setAlpha(55)
        p.setBrush(glow)
        p.drawEllipse(13, 19, 10, 10)
        p.setBrush(c)
        p.drawEllipse(15, 21, 6, 6)

        # 名称
        p.setFont(_font(theme.F_BODY, 13, bold=True, spacing=0.3))
        p.setPen(_q("#17497f" if self._active else theme.TXT))
        p.drawText(30, 6, 174, 20, Qt.AlignLeft | Qt.AlignVCenter,
                   self.meta["name"])

        # 第二行：连接信息 / 型号
        sub = self._detail or self.meta["model"]
        p.setFont(_font(theme.F_MONO, 9, spacing=0.1))
        p.setPen(_q("#8ba1b5"))
        fm = QFontMetrics(p.font())
        p.drawText(30, 25, 174, 16, Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(sub, Qt.ElideRight, 174))
        p.end()


class MotionPage(QWidget):
    """运动控制页。"""

    def __init__(self, mod, manager, parent=None, embedded=False):
        super(MotionPage, self).__init__(parent)
        self.mod = mod
        self.mgr = manager
        # embedded=True：被 device_module 装进「设备管理」的子页签里，
        # 标题条由外层画，顶部不再留 82px。
        self._embedded = bool(embedded)
        self._sel = MOTION_KEYS[0]

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 0 if self._embedded else HEAD_H, 18, 18)
        root.setSpacing(12)
        root.addWidget(self._build_panel(), 1)

        self.mgr.device_changed.connect(self._refresh)
        self.mgr.logged.connect(self._log)

        self._select(self._sel)
        self._refresh()
        self._log("运动控制已就绪：设备在左边【设备管理】页签里连接，连上后这里即可点动 / 定位 / 回零。",
                  "info")

    # ---------------------------------------------------------------- 界面
    def _build_panel(self):
        panel = Panel()
        v = QVBoxLayout(panel)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(10)

        # ---- 头：设备切换 + 连接状态
        head = QHBoxLayout()
        head.setSpacing(8)
        self.tabs = {}
        for key in MOTION_KEYS:
            tab = DeviceTab(key)
            tab.clicked.connect(self._on_tab_click)
            self.tabs[key] = tab
            head.addWidget(tab)

        head.addStretch(1)

        # 连接/断开归设备管理页，这里只报状态、指个路
        self.lb_link = QLabel("")
        self.lb_link.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        self.lb_link.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        head.addWidget(self.lb_link)
        v.addLayout(head)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background:%s;" % theme.LINE)
        v.addWidget(line)

        # ---- 中：每台设备一份手动控制
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background:transparent;")
        self.panels = {}
        for key in MOTION_KEYS:
            jp = JogPanel(key, self.mgr)
            jp.logged.connect(self._log)
            jp.step_changed.connect(self._on_step_changed)
            self.panels[key] = jp
            self.stack.addWidget(jp)
        v.addWidget(self.stack, 1)

        # ---- 底：运行日志
        log_head = QHBoxLayout()
        log_head.setSpacing(8)
        lb = QLabel("运行日志")
        lb.setFont(_font(theme.F_BODY, 10, bold=True, spacing=0.6))
        lb.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        btn_clear = QPushButton("清空")
        btn_clear.setObjectName("act")
        btn_clear.setFixedSize(52, 22)
        btn_clear.clicked.connect(lambda: self.txt_log.clear())
        log_head.addWidget(lb)
        log_head.addStretch(1)
        log_head.addWidget(btn_clear)
        v.addLayout(log_head)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setFixedHeight(84)
        self.txt_log.setFont(_font(theme.F_MONO, 10, spacing=0.1))
        self.txt_log.document().setMaximumBlockCount(400)
        v.addWidget(self.txt_log)
        return panel

    def paintEvent(self, _e):
        if self._embedded:
            return          # 标题条由外层 device_module 画
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        paint_module_head(p, 18, HEAD_TOP, self.width() - 36,
                          self.mod["idx"], self.mod["name"], self.mod["desc"])
        p.end()

    # ---------------------------------------------------------------- 设备切换
    def _on_tab_click(self, key):
        if key != self._sel:
            self._select(key)

    def _select(self, key):
        self._sel = key
        self.stack.setCurrentWidget(self.panels[key])
        for k, panel in self.panels.items():
            panel.set_active(k == key and self.isVisible())
        self._refresh()

    def _refresh(self, *_args):
        for key, tab in self.tabs.items():
            d = self.mgr.driver(key)
            info = (d.info or {}) if d is not None else {}
            detail = ""
            if self.mgr.is_connected(key):
                detail = info.get("endpoint") or info.get("model") or ""
            tab.set_state(self.mgr.state(key), detail)
            tab.set_active(key == self._sel)

        for key, panel in self.panels.items():
            panel.set_connected(self.mgr.is_connected(key))

        self._refresh_link()

    def _refresh_link(self):
        """右上角那行只说明「现在能不能动」，连接动作留给设备管理页。"""
        state = self.mgr.state(self._sel)
        if state == ST_CONNECTED:
            text, col = "已连接，可以直接操作", theme.OK
        elif state == ST_CONNECTING:
            text, col = "正在连接…", theme.WARN
        elif self.mgr.is_busy(self._sel):
            text, col = "正在连接…", theme.WARN
        else:
            text, col = "未连接 · 切到【设备管理】页签连接", theme.TXT3
        if self.lb_link.text() != text:
            self.lb_link.setText(text)
            self.lb_link.setStyleSheet(
                "color:%s;background:transparent;" % col)

    # ---------------------------------------------------------------- 生命周期
    def showEvent(self, e):
        super(MotionPage, self).showEvent(e)
        QTimer.singleShot(0, self._wake)

    def hideEvent(self, e):
        for panel in getattr(self, "panels", {}).values():
            panel.set_active(False)
        super(MotionPage, self).hideEvent(e)

    def _wake(self):
        for key, panel in self.panels.items():
            panel.set_active(key == self._sel and self.isVisible())

    # ---------------------------------------------------------------- 日志
    def _log(self, text, level="info"):
        colors = {"info": theme.TXT3, "ok": theme.OK,
                  "warn": theme.WARN, "err": theme.BAD}
        safe = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        self.txt_log.append('<span style="color:%s">%s</span>'
                            % (colors.get(level, theme.TXT3), safe))
        self.txt_log.verticalScrollBar().setValue(
            self.txt_log.verticalScrollBar().maximum())

    # ---------------------------------------------------------------- 步长落盘
    def _on_step_changed(self, _val):
        if getattr(self, "_step_timer", None) is None:
            self._step_timer = QTimer(self)
            self._step_timer.setSingleShot(True)
            self._step_timer.timeout.connect(self._flush_step)
        self._step_timer.start(600)

    def _flush_step(self):
        try:
            store.save(self.mgr.cfg)
        except (IOError, OSError) as e:
            self._log("点动步长没能写入配置：%s" % e, "warn")
