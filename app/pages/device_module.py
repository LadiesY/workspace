# -*- coding: utf-8 -*-
"""设备管理模块页（把「设备管理」和「运动控制」合成一个模块）。

左侧导航现在只有一项「设备管理」，进来后顶部标题条右侧有两个子页签：

    [设备管理]   连设备、调参数、看实时画面
    [运动控制]   点动 / 定位 / 回零 / 急停

两个子页的内容就是原来的 DevicePage 和 MotionPage，**逻辑一行没改**，
只是用 embedded=True 让它们不再各自画模块标题条（否则会画两条），
标题条和子页签统一由本页绘制。

为什么不做成两个导航项：这两块是同一件事的两半 —— 参数和连接在左、动作在右，
分两个导航项来回切最容易出现「这边断了那边还以为连着」。合并后状态点就在同一屏。
"""

from PyQt5.QtCore import QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from .. import theme
from ..widgets import HEAD_H, HEAD_TOP, _font, _q, paint_module_head
from .device_page import DevicePage
from .motion_page import MotionPage

MOTION_DESC = "三轴 / 六轴点动、定位、回零与急停"


class SubTab(QWidget):
    """子页签：圆角胶囊，选中的用强调蓝（与左侧导航选中态同一套语言）。

    2026-11-10：**没选中也要有框**。之前未选中态边框是 #e4eef7（白底上几乎看不见），
    只有当前页那个像按钮、另一个像一行说明文字，用户反馈"不明显"。现在三态都画框，
    只靠底色和文字色区分选中。
    """

    clicked = pyqtSignal(str)
    H = 30

    def __init__(self, key, text, parent=None):
        super(SubTab, self).__init__(parent)
        self.key = key
        self._text = text
        self._active = False
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        fm = QFontMetrics(_font(theme.F_BODY, 12, bold=True, spacing=0.8))
        self.setFixedSize(fm.horizontalAdvance(text) + 44, self.H)

    def set_active(self, on):
        if self._active != bool(on):
            self._active = bool(on)
            self.update()

    def enterEvent(self, _e):
        self._hover = True
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit(self.key)
        super(SubTab, self).mouseReleaseEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        if self._active:
            p.setPen(QPen(_q(theme.AMBER, 170), 1))
            p.setBrush(_q(theme.AMBER, 26))
        elif self._hover:
            p.setPen(QPen(_q("#8fb6da"), 1))
            p.setBrush(QColor(27, 87, 160, 14))
        else:
            p.setPen(QPen(_q("#b7cfe4"), 1))      # 未选中：框要看得见
            p.setBrush(_q("#f7fbff"))
        p.drawRoundedRect(r, 3, 3)

        p.setFont(_font(theme.F_BODY, 12, bold=True, spacing=0.8))
        if self._active:
            col = "#17497f"
        elif self._hover:
            col = theme.TXT
        else:
            col = "#4b6a86"
        p.setPen(_q(col))
        p.drawText(r, Qt.AlignCenter, self._text)
        p.end()


class DeviceModulePage(QWidget):
    """设备管理（含运动控制子页）。左侧导航「02 设备管理」对应的就是这一页。"""

    sub_changed = pyqtSignal(object)    # 当前子页名；设备管理子页发 None

    def __init__(self, mod, manager, parent=None):
        super(DeviceModulePage, self).__init__(parent)
        self.mod = mod
        self._i = 0

        # 子页共用模块自己的编号（2026-11-10 起插了「01 首页」，设备管理变 02）
        mot_mod = dict(mod)
        mot_mod.update({"name": "运动控制", "desc": MOTION_DESC})

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶部留出标题条的高度，把两个页签浮在标题条上；这里不填背景，
        # 让父窗口画出来的标题条透上来。
        # 2026-11-10：页签从"贴右边"改成**水平居中**（用户要求试试居中）。
        # 居中不用布局的 stretch 做 —— 窗口窄到 1100 时正中间正好压在标题右侧那行
        # 说明文字上，会叠字。改成自己算 x：先按居中算，再被"标题块右边界 + 26"
        # 和"右内边距"两头夹住，宽屏仍居中，窄屏自动让开。
        self.head = QWidget(self)
        self.head.setFixedHeight(HEAD_H)

        self.tabs = []
        for key, text in (("device", "设备管理"), ("motion", "运动控制")):
            tab = SubTab(key, text, self.head)
            tab.clicked.connect(self._on_tab)
            self.tabs.append(tab)
        root.addWidget(self.head)

        self.stack = QStackedWidget(self)
        # 带选择器：无选择器的 background 会传到子孙（含下拉列表的 viewport）
        self.stack.setStyleSheet("QStackedWidget { background: transparent; }")
        self.pages = [DevicePage(mod, manager, embedded=True),
                      MotionPage(mot_mod, manager, embedded=True)]
        for page in self.pages:
            self.stack.addWidget(page)
        root.addWidget(self.stack, 1)

        self.tabs[0].set_active(True)
        self._place_tabs()

    # ---------------------------------------------------------------- 页签位置
    def _head_text(self):
        """标题条上的（标题, 说明）—— 跟着子页走，和 paintEvent 取同一份。"""
        page = self.pages[self._i]
        name = self.mod["name"] if self._i == 0 else page.mod["name"]
        desc = self.mod["desc"] if self._i == 0 else page.mod["desc"]
        return name, desc

    def _place_tabs(self):
        """两个页签水平居中，但两头都夹住：别压标题块，也别顶出右边界。

        标题块宽度按 paint_module_head 的算法量（编号 + 13 + 标题 + 13 + 说明），
        所以子页切换、说明文字变长变短都能跟着让位。
        """
        if not self.tabs:
            return
        w = self.head.width() or self.width()
        gap = 6
        total = sum(t.width() for t in self.tabs) + gap * (len(self.tabs) - 1)

        name, desc = self._head_text()
        num_w = QFontMetrics(_font(theme.F_DISP, 34, bold=True,
                                   spacing=1.2)).horizontalAdvance(self.mod["idx"])
        title_w = QFontMetrics(_font(theme.F_BODY, 20, bold=True,
                                     spacing=1.2)).horizontalAdvance(name)
        desc_w = QFontMetrics(_font(theme.F_BODY, 11,
                                    spacing=0.4)).horizontalAdvance(desc)
        title_right = 18 + num_w + 13 + title_w + 13 + desc_w

        x = (w - total) // 2
        x = max(x, title_right + 26)
        x = min(x, w - 18 - total)
        for t in self.tabs:
            t.move(x, HEAD_TOP)
            x += t.width() + gap

    def resizeEvent(self, e):
        super(DeviceModulePage, self).resizeEvent(e)
        self._place_tabs()

    # ---------------------------------------------------------------- 子页切换
    def _on_tab(self, key):
        self._select(0 if key == "device" else 1)

    def _select(self, i):
        if i == self._i:
            return
        self._i = i
        self.stack.setCurrentIndex(i)
        for k, tab in enumerate(self.tabs):
            tab.set_active(k == i)
        self._place_tabs()          # 标题/说明变了，让位量要重算
        self.update()
        self.sub_changed.emit(self.sub_name)

    @property
    def sub_name(self):
        """当前子页名：设备管理子页返回 None（面包屑保持「已接入」原样）。"""
        return None if self._i == 0 else self.pages[1].mod["name"]

    # ---------------------------------------------------------------- 绘制
    def paintEvent(self, _e):
        """标题跟着子页走：设备管理 → 02 设备管理，运动控制 → 02 运动控制。

        编号固定用模块自己的编号 —— 两个子页属于同一个模块，编号跟着模块走，
        不另外给子页造一个号出来（会和别的模块撞号）。
        """
        page = self.pages[self._i]
        name = self.mod["name"] if self._i == 0 else page.mod["name"]
        desc = self.mod["desc"] if self._i == 0 else page.mod["desc"]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        paint_module_head(p, 18, HEAD_TOP, self.width() - 36,
                          self.mod["idx"], name, desc)
        p.end()
