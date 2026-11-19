# -*- coding: utf-8 -*-
"""自绘控件。

界面里所有"看起来像原型"的部分都是 QPainter 画的，没用 QSS 拼——
原型那套渐变、左侧色条、描边数字，用 QSS 复刻要走一堆属性选择器和
unpolish/polish，不如直接画来得准，也省得以后调颜色调不动。

坐标一律按原型的 CSS 像素取，Qt 的 200% 缩放会自动放大，视觉尺寸与原型一致。
"""

from PyQt5.QtCore import QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import (QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
                         QPainterPath, QPen, QRadialGradient)
from PyQt5.QtWidgets import QWidget

from . import icons, theme


def _q(hex_color, alpha=None):
    c = QColor(hex_color)
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def _font(family, px, bold=False, spacing=0.0):
    f = QFont(family)
    f.setPixelSize(px)
    f.setBold(bold)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return f


class Led(QWidget):
    """小圆点指示灯：实心 + 外圈光晕，原型里的 .led。"""

    def __init__(self, color=None, diameter=6, parent=None):
        super().__init__(parent)
        self._color = QColor(color or theme.TXT3)
        self._d = diameter
        self.setFixedSize(diameter + 6, diameter + 6)

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        r = self._d / 2.0
        glow = QColor(self._color)
        glow.setAlpha(60)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), r + 2.0, r + 2.0)
        p.setBrush(self._color)
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.end()


class NavItem(QWidget):
    """左侧导航的一项：编号 + 图标 + 名称。

    选中态：左侧 2px 深蓝色条 + 蓝色渐隐底 + 文字加深，
    与原型 .nav-item.active 一致（浅色版把琥珀换成了深蓝）。
    """

    clicked = pyqtSignal()

    def __init__(self, idx, name, icon_name, parent=None):
        super().__init__(parent)
        self._idx = idx
        self._name = name
        self._icon = icon_name
        self._hover = False
        self._active = False
        self._locked = False       # 未登入时 02~08 挂一把小锁
        self.setFixedHeight(theme.NAV_ITEM_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)

    def set_active(self, on):
        if self._active != bool(on):
            self._active = bool(on)
            self.update()

    def set_locked(self, on):
        """未登入时在右侧画一把小锁。只是提示，真正的拦截在 select_module。"""
        if self._locked != bool(on):
            self._locked = bool(on)
            self.update()

    def enterEvent(self, _e):
        self._hover = True
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        r = self.rect()
        w, h = r.width(), r.height()

        if self._active:
            g = QLinearGradient(0, 0, w * 0.78, 0)
            g.setColorAt(0.00, _q(theme.AMBER, 40))
            g.setColorAt(0.62, _q(theme.AMBER, 12))
            g.setColorAt(1.00, _q(theme.AMBER, 0))
            p.fillRect(r, g)
            p.fillRect(QRect(0, 0, 2, h), _q(theme.AMBER))
            p.setPen(QPen(_q(theme.AMBER, 26), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r.adjusted(0, 0, -1, -1))
        elif self._hover:
            p.fillRect(r, QColor(27, 87, 160, 10))

        # 编号
        p.setFont(_font(theme.F_MONO, 10, spacing=0.6))
        p.setPen(_q(theme.AMBER if self._active else theme.TXT_NAV_IDX))
        p.drawText(QRect(10, 0, 20, h), Qt.AlignLeft | Qt.AlignVCenter, self._idx)

        # 图标
        col = theme.AMBER if self._active else (
            theme.TXT if self._hover else theme.TXT2)
        pm = icons.icon(self._icon, col, 15, self.devicePixelRatioF())
        p.drawPixmap(39, (h - 15) // 2, pm)

        # 名称
        p.setFont(_font(theme.F_BODY, 13, spacing=0.4))
        if self._active:
            p.setPen(_q(theme.AMBER))
        else:
            p.setPen(_q(theme.TXT if self._hover else theme.TXT2))
        p.drawText(QRect(65, 0, w - 70, h), Qt.AlignLeft | Qt.AlignVCenter, self._name)

        if self._locked:
            # 小锁：上面一道半圆弧 + 下面一个圆角方块，14px 见方
            col = _q(theme.TXT_NAV_IDX, 170)
            lx, ly = w - 20, h / 2.0 - 6
            p.setPen(QPen(col, 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(lx + 1, ly - 1, 7, 8), 0, 180 * 16)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(lx, ly + 4, 9, 7), 1.5, 1.5)
        p.end()


def paint_panel(p, r):
    """画一块标准面板：白底 + 细边 + 顶部蓝色高光 + 四角角标。

    占位页和设备管理页共用，保证两处的「面板」长得一模一样。
    浅色版的"高光"不是发光，是卡片顶上那一道强调蓝 —— 白卡在浅蓝底上
    本来就看不出边界，全靠这道线和四角撑住质感。
    """
    g = QLinearGradient(0, r.top(), 0, r.bottom())
    g.setColorAt(0.0, _q(theme.PANEL_TOP))
    g.setColorAt(1.0, _q(theme.PANEL_BOT))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawRect(r)

    p.setPen(QPen(_q("#c3d7e9"), 1))
    p.setBrush(Qt.NoBrush)
    p.drawRect(r.adjusted(0, 0, -1, -1))

    # 顶部高光线：蓝色渐隐，原型 .panel::before
    hg = QLinearGradient(r.left(), 0, r.left() + r.width() * 0.55, 0)
    hg.setColorAt(0.0, _q(theme.AMBER, 150))
    hg.setColorAt(0.47, _q(theme.AMBER, 40))
    hg.setColorAt(1.0, _q(theme.AMBER, 0))
    p.setPen(QPen(hg, 1))
    p.drawLine(r.left() + 1, r.top(), int(r.left() + r.width() * 0.55), r.top())

    # 四角角标
    p.setPen(QPen(_q(theme.AMBER, 95), 1))
    c, m = 9, 5
    p.drawLine(r.left() + m, r.top() + m, r.left() + m + c, r.top() + m)
    p.drawLine(r.left() + m, r.top() + m, r.left() + m, r.top() + m + c)
    p.drawLine(r.right() - m, r.top() + m, r.right() - m - c, r.top() + m)
    p.drawLine(r.right() - m, r.top() + m, r.right() - m, r.top() + m + c)
    p.drawLine(r.left() + m, r.bottom() - m, r.left() + m + c, r.bottom() - m)
    p.drawLine(r.left() + m, r.bottom() - m, r.left() + m, r.bottom() - m - c)
    p.drawLine(r.right() - m, r.bottom() - m, r.right() - m - c, r.bottom() - m)
    p.drawLine(r.right() - m, r.bottom() - m, r.right() - m, r.bottom() - m - c)


def paint_module_head(p, x, y, w, idx, name, desc):
    """模块标题条：描边大号编号 + 标题 + 描述 + 底线。返回底线 y。"""
    num_f = _font(theme.F_DISP, 34, bold=True, spacing=1.2)
    fm_num = QFontMetrics(num_f)
    num_w = fm_num.horizontalAdvance(idx)

    title_f = _font(theme.F_BODY, 20, bold=True, spacing=1.2)
    fm_title = QFontMetrics(title_f)
    title_w = fm_title.horizontalAdvance(name)

    desc_f = _font(theme.F_BODY, 11, spacing=0.4)

    gap = 13
    x_num = x
    x_title = x_num + num_w + gap
    x_desc = x_title + title_w + gap
    base = y + fm_num.ascent()

    # 编号：描边字，内部极淡填充。浅色底上描边要更实、填充要更淡，
    # 否则"描边数字"会糊成一坨灰蓝。
    path = QPainterPath()
    path.addText(QPointF(x_num, base), num_f, idx)
    pen = QPen(_q(theme.AMBER, 180))
    pen.setWidthF(1.0)
    p.setPen(pen)
    p.setBrush(_q(theme.AMBER, 18))
    p.drawPath(path)

    p.setFont(title_f)
    p.setPen(_q(theme.TITLE))
    p.drawText(QPointF(x_title, base), name)

    if x_desc + fm_title.horizontalAdvance(desc) < x + w:
        p.setFont(desc_f)
        p.setPen(_q(theme.TXT3))
        p.drawText(QPointF(x_desc, base), desc)

    line_y = y + fm_num.height() + 12
    g = QLinearGradient(x, 0, x + w, 0)
    g.setColorAt(0.0, QColor(157, 184, 208, 170))
    g.setColorAt(1.0, QColor(157, 184, 208, 0))
    p.setPen(QPen(g, 1))
    p.drawLine(x, line_y, x + w, line_y)
    return line_y


class GridCanvas(QWidget):
    """工作区背景：纯净浅蓝底 + 页头一条横向蓝渐变。

    2026-11-10 浅色改版把原来的「34px 蓝白格子 + 青色径向光晕」整片去掉了 ——
    浅色下格子线会变成一层灰网格，屏幕看着脏，也压住了卡片。
    现在只有页头那 132px 有颜色（左淡蓝 → 中近白 → 右淡蓝），往下淡出到底色，
    靠这条渐变给页头一点层次，剩下的地方干干净净。
    """

    HEAD_BAND = 132

    def paintEvent(self, _e):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, _q(theme.BG_WORK))

        band = QRect(0, 0, r.width(), min(self.HEAD_BAND, r.height()))
        g = QLinearGradient(0, 0, band.width(), 0)
        g.setColorAt(0.0, _q(theme.HEAD_GA))
        g.setColorAt(0.5, _q(theme.HEAD_GB))
        g.setColorAt(1.0, _q(theme.HEAD_GC))
        p.fillRect(band, g)

        # 下边缘淡出到底色，不然横向渐变会留一条硬边，像贴了张纸
        base = QColor(theme.BG_WORK)
        fade_h = 54
        top = max(0, band.bottom() - fade_h + 1)
        fade = QLinearGradient(0, top, 0, band.bottom() + 1)
        c0 = QColor(base)
        c0.setAlpha(0)
        fade.setColorAt(0.0, c0)
        fade.setColorAt(1.0, base)
        p.fillRect(QRect(0, top, r.width(), band.bottom() - top + 1), fade)
        p.end()


class PlaceholderPage(QWidget):
    """模块占位页：模块标题条 + 一块空面板 + 居中提示。

    故意不放任何示例数据。原型里那些项目名、测点坐标、波形都是编的，
    照搬进来会让人分不清哪块真做了、哪块还是空的。
    """

    def __init__(self, mod, parent=None):
        super().__init__(parent)
        self.mod = mod
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)

    # ---------- 绘制 ----------
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)

        pad_l, pad_t, pad_r, pad_b = 18, 16, 18, 18
        inner_w = self.width() - pad_l - pad_r

        head_bottom = self._draw_head(p, pad_l, pad_t, inner_w)

        top = head_bottom + 14
        panel = QRect(pad_l, top, inner_w,
                      max(80, self.height() - top - pad_b))
        self._draw_panel(p, panel)
        self._draw_empty(p, panel)
        p.end()

    def _draw_head(self, p, x, y, w):
        return paint_module_head(p, x, y, w, self.mod["idx"],
                                 self.mod["name"], self.mod["desc"])

    def _draw_panel(self, p, r):
        paint_panel(p, r)

    def _draw_empty(self, p, r):
        cx, cy = r.center().x(), r.center().y()
        w = r.width()

        p.setFont(_font(theme.F_MONO, 9, spacing=2.2))
        p.setPen(_q("#b6c8d8"))
        p.drawText(QRect(r.left(), cy - 80, w, 12), Qt.AlignCenter,
                   "PLACEHOLDER  %s" % self.mod["idx"])

        pm = icons.icon(self.mod["icon"], "#96acc0", 48, self.devicePixelRatioF())
        p.drawPixmap(int(cx - 24), int(cy - 54), pm)

        p.setFont(_font(theme.F_BODY, 14, spacing=0.8))
        p.setPen(_q(theme.TXT2))
        p.drawText(QRect(r.left(), cy - 6, w, 22), Qt.AlignCenter,
                   "该模块尚未接入功能")

        p.setFont(_font(theme.F_BODY, 12, spacing=0.4))
        p.setPen(_q(theme.TXT3))
        p.drawText(QRect(r.left(), cy + 20, w, 18), Qt.AlignCenter,
                   "界面骨架已就位，功能将逐个填入")


# ---------------------------------------------------------------- 页面公共块
HEAD_TOP = 16
HEAD_H = 82                      # 模块标题条占位高度，内容从这里往下排


class Panel(QWidget):
    """带标准外观的面板，内容用布局往里放。设备页和运动页都用它。"""

    def __init__(self, parent=None):
        super(Panel, self).__init__(parent)
        # 这里**不设** setStyleSheet("background: transparent")：
        # 没有选择器的 background 会向下传给所有子孙，连 QComboBox 弹出列表的
        # viewport 都被刷成透明 → 下拉弹出来是黑底灰字，看不清。
        # 面板本身是 paintEvent 自绘的，QWidget 默认也不填背景，本来就不需要它。

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        paint_panel(p, self.rect())
        p.end()
