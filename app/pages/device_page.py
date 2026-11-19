# -*- coding: utf-8 -*-
"""设备管理页。

左边是设备清单（状态点 + 连接/断开 + 全部连接/全部断开），
右边是选中设备的参数：三轴和探针给「每轴正负限位 / 速度 / 加速度 / 减速度」，
相机给曝光 / 增益 / 帧率 / 分辨率 / 开窗偏移。

参数默认是只读的，点【编辑】才能改，点【保存】才写进 data/devices.json ——
下次启动读的就是保存后的值。

设备参数不是编的，来源见 devices/store.py 顶部注释（KZQ / TZ / XJ 三份原配置）。
"""

import copy
import threading

from PyQt5.QtCore import QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (QColor, QFontMetrics, QLinearGradient, QPainter,
                         QPen, QRadialGradient)
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame,
                             QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QScrollArea,
                             QSizePolicy, QSpinBox, QStackedWidget, QTextEdit,
                             QVBoxLayout, QWidget)

from .. import icons, theme
from ..devices import DEVICE_META, DEVICE_ORDER
from ..devices.base import ST_CONNECTED, ST_CONNECTING
from ..devices.store import AXES, PROBE_AXES
from ..widgets import (HEAD_H as widgets_head_h, HEAD_TOP as widgets_head_top,
                       Panel, _font, _q, paint_module_head)

HEAD_TOP = widgets_head_top
HEAD_H = widgets_head_h

STYLE = theme.controls_qss()


# ---------------------------------------------------------------- 基础块
class Badge(QWidget):
    """状态徽章：小圆角标签，绿=已连接，红=未连接，琥珀=连接中。"""

    def __init__(self, parent=None):
        super(Badge, self).__init__(parent)
        self._text = "未连接"
        self._color = theme.BAD
        self.setFixedHeight(20)
        self.setMinimumWidth(58)

    def set_state(self, state):
        if state == ST_CONNECTED:
            self._text, self._color = "已连接", theme.OK
        elif state == ST_CONNECTING:
            self._text, self._color = "连接中", theme.WARN
        else:
            self._text, self._color = "未连接", theme.BAD
        fm = QFontMetrics(_font(theme.F_BODY, 10, spacing=0.3))
        self.setFixedWidth(fm.horizontalAdvance(self._text) + 22)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self.rect().adjusted(0, 1, -1, -2)
        c = QColor(self._color)
        p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), 120), 1))
        bg = QColor(c)
        bg.setAlpha(34)
        p.setBrush(bg)
        p.drawRoundedRect(r, 3, 3)
        p.setFont(_font(theme.F_BODY, 10, spacing=0.3))
        p.setPen(c)
        p.drawText(r, Qt.AlignCenter, self._text)
        p.end()


class DeviceCard(QWidget):
    """清单里的一项（横排卡片）：状态点 + 图标 + 名称/型号 + 连接/断开。"""

    clicked = pyqtSignal(str)
    connect_clicked = pyqtSignal(str)
    disconnect_clicked = pyqtSignal(str)
    H = 56

    def __init__(self, key, parent=None):
        super(DeviceCard, self).__init__(parent)
        self.key = key
        self.meta = DEVICE_META[key]
        self.setFixedHeight(self.H)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self._hover = False
        self._active = False
        self._sep = True          # 右侧是否画分隔线（最后一张不画）
        self._state = "disconnected"
        self._detail = ""
        self._sub = ""

        self.btn_conn = QPushButton("连接", self)
        self.btn_conn.setObjectName("cardBtn")
        self.btn_conn.setFixedSize(60, 24)
        self.btn_conn.clicked.connect(lambda: self.connect_clicked.emit(self.key))

        self.btn_disc = QPushButton("断开", self)
        self.btn_disc.setObjectName("cardBtn")
        self.btn_disc.setFixedSize(60, 24)
        self.btn_disc.clicked.connect(lambda: self.disconnect_clicked.emit(self.key))

    # ---- 外部调用
    def set_active(self, on):
        if self._active != bool(on):
            self._active = bool(on)
            self.update()

    def set_sep(self, on):
        self._sep = bool(on)
        self.update()

    def set_state(self, state, detail="", sub=""):
        self._state = state
        self._detail = detail or ""
        self._sub = sub or ""
        connected = state == ST_CONNECTED
        self.btn_conn.setEnabled(not connected and state != ST_CONNECTING)
        self.btn_disc.setEnabled(connected)
        self.btn_conn.setText("连接中" if state == ST_CONNECTING else "连接")
        # 固件/库版本这类长信息放不进横排卡片，悬停时能看到
        lines = [self.meta["name"], self.meta["model"]]
        if self._detail:
            lines.append(self._detail)
        if self._sub:
            lines.append(self._sub)
        self.setToolTip("\n".join(lines))
        self.update()

    # ---- 事件
    def enterEvent(self, _e):
        self._hover = True
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def resizeEvent(self, e):
        x = self.width() - 72
        self.btn_conn.move(x, 4)
        self.btn_disc.move(x, 28)
        super(DeviceCard, self).resizeEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.key)
        super(DeviceCard, self).mouseReleaseEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        r = self.rect().adjusted(0, 0, -1, -1)
        w, h = r.width(), r.height()

        if self._active:
            p.fillRect(r, _q(theme.AMBER, 22))
            p.fillRect(0, 0, 2, h, _q(theme.AMBER))
        elif self._hover:
            p.fillRect(r, QColor(27, 87, 160, 12))

        # 卡与卡之间的竖分隔线
        if self._sep:
            p.setPen(QPen(QColor(205, 223, 238, 190), 1))
            p.drawLine(w, 6, w, h - 6)

        # 状态点（绿＝已连接，红＝未连接，琥珀＝连接中）
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
        p.drawEllipse(11, h // 2 - 6, 12, 12)
        p.setBrush(c)
        p.drawEllipse(14, h // 2 - 3, 6, 6)

        # 图标
        icol = theme.AMBER if self._active else theme.TXT2
        pm = icons.icon(self.meta["icon"], icol, 19, self.devicePixelRatioF())
        p.drawPixmap(34, int(h / 2 - 10), pm)

        # 文字区（右侧留出按钮的位置）
        tw = max(40, w - 142)

        p.setFont(_font(theme.F_BODY, 13, spacing=0.3))
        p.setPen(_q("#1f3750" if self._active else theme.TXT))
        fm = QFontMetrics(p.font())
        p.drawText(62, 6, tw, 20, Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(self.meta["name"], Qt.ElideRight, tw))

        # 第二行：连接时显示端点/IP，未连接时显示型号
        p.setFont(_font(theme.F_MONO, 10, spacing=0.2))
        p.setPen(_q(theme.TXT3 if self._state != ST_CONNECTED else theme.TXT2))
        detail = self._detail or self.meta["model"]
        fm2 = QFontMetrics(p.font())
        p.drawText(62, 28, tw, 18, Qt.AlignLeft | Qt.AlignVCenter,
                   fm2.elidedText(detail, Qt.ElideRight, tw))
        p.end()


# ---------------------------------------------------------------- 轴参数表
def _spin(lo, hi, val, dec=3, step=1.0, width=88):
    sb = QDoubleSpinBox()
    sb.setRange(lo, hi)
    sb.setDecimals(dec)
    sb.setSingleStep(step)
    sb.setValue(float(val) if val is not None else 0.0)
    sb.setButtonSymbols(QDoubleSpinBox.NoButtons)
    sb.setFixedWidth(width)
    sb.setFixedHeight(26)
    sb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return sb


class AxisTable(QWidget):
    """每轴一行：正限位 / 负限位 / 速度 / 加速度 / 减速度。

    axes 决定显示哪几轴 —— 三轴控制器传 AXES（X/Y/Z），
    探针座是六轴卡，传 PROBE_AXES（X/Y/Z + U/V/W），到 U 处会插一条分组标题。

    limit_optional=True 时限位改用输入框，可以留空表示「该方向不限制」
    （探针卡原配置 rng_min/rng_max 就是 null 表示不限，硬套数字会改语义）。
    """

    COLS = [("limit_pos", "正限位"), ("limit_neg", "负限位"),
            ("speed", "速度"), ("acc", "加速度"), ("dec", "减速度")]
    COL_W = 108            # 每列宽度（比 100 略宽，数字显示不挤）
    # 单位直接并进表头（小字），省掉一整行 —— 六轴表比三轴表高 3 行，
    # 纵向本来就紧，这一行省下来正好放得下 U/V/W
    UNITS = ["", "mm", "mm", "mm/s", "mm/s²", "mm/s²"]

    def __init__(self, limit_optional=False, axes=None, parent=None):
        super(AxisTable, self).__init__(parent)
        self.limit_optional = limit_optional
        self.axes = tuple(axes) if axes else tuple(AXES)
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(5)

        # 表头（带单位）
        heads = ["轴"] + [c[1] for c in self.COLS]
        for i, txt in enumerate(heads):
            unit = self.UNITS[i]
            lb = QLabel("%s <span style='color:#879caf;font-size:9px;'>%s</span>"
                        % (txt, unit) if unit else txt)
            if unit:
                lb.setTextFormat(Qt.RichText)
            lb.setFont(_font(theme.F_BODY, 10, spacing=0.4))
            lb.setStyleSheet("color:%s;background:transparent;padding-right:9px;"
                             % theme.TXT3)
            # 表头跟着数值一起右对齐，列内看起来才对得上
            lb.setAlignment(Qt.AlignLeft if i == 0 else Qt.AlignRight | Qt.AlignVCenter)
            lb.setFixedWidth(26 if i == 0 else self.COL_W)
            g.addWidget(lb, 0, i)

        self.cells = {}
        row_i = 1
        for name in self.axes:
            # 六轴卡在 U 之前插一条分组标题，X/Y/Z 与 U/V/W 一眼分得开
            if name == "u":
                sep = QLabel("UVW　第二组平面轴")
                sep.setFont(_font(theme.F_MONO, 9, spacing=0.5))
                sep.setStyleSheet("color:#8ba1b5;background:transparent;")
                sep.setContentsMargins(0, 3, 0, 0)
                sep.setFixedHeight(15)
                g.addWidget(sep, row_i, 0, 1, len(self.COLS) + 1,
                            Qt.AlignLeft | Qt.AlignVCenter)
                row_i += 1

            lb = QLabel(name.upper())
            lb.setFont(_font(theme.F_DISP, 12, bold=True, spacing=0.8))
            lb.setStyleSheet("color:%s;background:transparent;" % theme.AMBER_DIM)
            lb.setFixedWidth(26)
            g.addWidget(lb, row_i, 0)

            self.cells[name] = {}
            for col, (field, _label) in enumerate(self.COLS, start=1):
                if self.limit_optional and field in ("limit_pos", "limit_neg"):
                    wd = QLineEdit()
                    wd.setPlaceholderText("不限")
                    wd.setFixedWidth(self.COL_W)
                    wd.setFixedHeight(26)
                    wd.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    wd.setFont(_font(theme.F_MONO, 11, spacing=0.2))
                elif field in ("limit_pos", "limit_neg"):
                    wd = _spin(-99999.0, 99999.0, 0.0, dec=3, step=1.0, width=self.COL_W)
                elif field == "speed":
                    wd = _spin(0.001, 10000.0, 1.0, dec=3, step=0.1, width=self.COL_W)
                else:
                    wd = _spin(0.001, 100000.0, 100.0, dec=2, step=10.0, width=self.COL_W)
                self.cells[name][field] = wd
                g.addWidget(wd, row_i, col)
            row_i += 1

        g.setColumnStretch(len(self.COLS) + 1, 1)

    # ---- 读写
    def set_values(self, axes_cfg):
        for name in self.axes:
            cfg = axes_cfg.get(name, {})
            for field, _l in self.COLS:
                wd = self.cells[name][field]
                v = cfg.get(field)
                if isinstance(wd, QLineEdit):
                    wd.setText("" if v in (None, "") else ("%g" % float(v)))
                else:
                    wd.setValue(float(v) if v not in (None, "") else 0.0)

    def values(self):
        """返回 ({axis: {field: 值}, 错误串)。空限位返回 None。"""
        out, err = {}, None
        for name in self.axes:
            row = {}
            for field, _l in self.COLS:
                wd = self.cells[name][field]
                if isinstance(wd, QLineEdit):
                    txt = wd.text().strip()
                    if not txt:
                        row[field] = None
                        continue
                    try:
                        row[field] = float(txt)
                    except ValueError:
                        err = "%s 轴%s不是有效数字：%s" % (name.upper(), _l, txt)
                        return {}, err
                else:
                    row[field] = float(wd.value())
            out[name] = row
        return out, err

    def set_editable(self, on):
        for name in self.axes:
            for field, _l in self.COLS:
                self.cells[name][field].setEnabled(on)


# ---------------------------------------------------------------- 实时画面
class RatioBox(QWidget):
    """按固定长宽比摆画面控件，多出来的空间留在两侧。

    相机传感器是 3072×2048（3:2）。以前画面框跟着窗口拉伸成一个宽扁的
    2:1 矩形，整幅适配后上下留一片黑，看起来像"画面很小"。
    这里把框本身做成跟传感器同样的比例 —— 画面刚好填满，取景范围一眼可辨。
    ROI 改了尺寸后比例会跟着变（_on_stats 里回传真实宽高）。
    """

    STAT_H = 0                       # 状态条浮在画面上，不占画面高度

    def __init__(self, ratio=1.5, parent=None):
        super(RatioBox, self).__init__(parent)
        self._ratio = float(ratio) if ratio and ratio > 0 else 1.5
        self._child = None
        self.setStyleSheet("background:transparent;")
        self.setMinimumHeight(180)

    def set_ratio(self, ratio):
        try:
            r = float(ratio)
        except (TypeError, ValueError):
            return
        if r <= 0.05 or abs(r - self._ratio) < 1e-4:
            return
        self._ratio = r
        self._relayout()

    def set_child(self, wd):
        self._child = wd
        wd.setParent(self)
        self._relayout()

    def _relayout(self):
        if self._child is None:
            return
        w, h = self.width(), self.height()
        if w <= 2 or h <= 2:
            return
        # 先按高度推宽度；宽度不够时再按宽度反推高度
        cw = int(round((h - self.STAT_H) * self._ratio))
        ch = h
        if cw > w:
            cw = w
            ch = int(round(cw / self._ratio)) + self.STAT_H
        self._child.setGeometry((w - cw) // 2, (h - ch) // 2,
                                max(1, cw), max(1, ch))

    def resizeEvent(self, e):
        super(RatioBox, self).resizeEvent(e)
        self._relayout()


class CameraView(QWidget):
    """相机实时画面：滚轮缩放、按住拖动平移、双击复位。

    做法跟相机工具（XJ）的预览区一致 —— 界面自己画 QImage，不走 SDK 的
    DisplayOneFrame，才能随便改缩放。zoom 是相对「整幅适配窗口」的倍数，
    底部状态条显示的是绝对比例（100% = 1 屏幕像素对 1 传感器像素）。
    """

    ZOOM_MAX_ABS = 16.0                  # 最大放大到 1:1 的 16 倍

    def __init__(self, parent=None):
        super(CameraView, self).__init__(parent)
        # 最小尺寸要小：外面套着 RatioBox，画面框由比例算出来，别被这里卡住
        self.setMinimumSize(120, 90)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.CrossCursor)

        self._img = None
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._drag = None
        self._hint = "相机未连接"
        self._hint2 = ""
        self._live = False
        self._stat = None                # (宽, 高, fps)

    # ------------------------------------------------------------ 外部接口
    def set_image(self, img):
        if img is None or img.isNull():
            return
        self._img = img
        self._hint2 = ""
        self._clamp_pan()
        self.update()

    def clear(self, hint, hint2=""):
        self._img = None
        self._hint = hint or ""
        self._hint2 = hint2 or ""
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._stat = None
        self.update()

    def set_live(self, on):
        if self._live != bool(on):
            self._live = bool(on)
            self.update()

    def set_stat(self, w, h, fps):
        self._stat = (int(w), int(h), float(fps))
        self.update()

    def reset_view(self):
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self.update()

    def abs_scale(self):
        """绝对比例：1.0 = 1 屏幕像素 : 1 传感器像素。"""
        return self._fit_scale() * self._zoom

    # ------------------------------------------------------------ 内部几何
    def _fit_scale(self):
        if self._img is None or self._img.isNull():
            return 1.0
        iw, ih = self._img.width(), self._img.height()
        if iw <= 0 or ih <= 0:
            return 1.0
        # 状态条是浮在画面上的半透明条，不再从画面高度里扣，
        # 整幅画面正好填满按传感器比例做出来的框
        return min(self.width() / float(iw), self.height() / float(ih))

    def _target_rect(self):
        img = self._img
        s = self._fit_scale() * self._zoom
        iw, ih = img.width() * s, img.height() * s
        x = (self.width() - iw) / 2.0 + self._pan.x()
        y = (self.height() - ih) / 2.0 + self._pan.y()
        return QRectF(x, y, iw, ih), s

    def _clamp_pan(self):
        if self._img is None or self._img.isNull():
            self._pan = QPointF(0.0, 0.0)
            return
        s = self._fit_scale() * self._zoom
        mx = max(0.0, (self._img.width() * s - self.width()) / 2.0)
        my = max(0.0, (self._img.height() * s - self.height()) / 2.0)
        self._pan = QPointF(min(max(self._pan.x(), -mx), mx),
                            min(max(self._pan.y(), -my), my))

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, _e):
        p = QPainter(self)
        r = self.rect()
        # 无画面时给一点中心微光，纯黑一大片会显得"坏了"
        g = QRadialGradient(QPointF(r.center()), max(r.width(), r.height()) * 0.72)
        g.setColorAt(0.0, QColor(255, 255, 255))
        g.setColorAt(1.0, QColor(250, 252, 255))
        p.fillRect(r, g)

        has_img = self._img is not None and not self._img.isNull()
        if has_img:
            rect, s = self._target_rect()
            # 缩小时平滑抗锯齿；放大时用最近邻，让每个传感器像素清清楚楚
            p.setRenderHint(QPainter.SmoothPixmapTransform, s < 1.0)
            p.drawImage(rect, self._img)
        else:
            self._paint_hint(p, r)

        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        p.setPen(QPen(QColor(201, 220, 236, 170), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r.adjusted(0, 0, -1, -1))

        self._paint_badge(p, r)
        self._paint_stat(p, r)
        p.end()

    def _paint_hint(self, p, r):
        cy = r.center().y()
        pm = icons.icon("camera", "#8ba1b5", 34, self.devicePixelRatioF())
        p.drawPixmap(int(r.center().x() - pm.width() / 2.0), int(cy - 58), pm)
        p.setFont(_font(theme.F_BODY, 12, spacing=0.5))
        p.setPen(_q("#7189a0"))
        p.drawText(QRect(r.x(), cy - 14, r.width(), 24),
                   Qt.AlignHCenter | Qt.AlignBottom, self._hint)
        if self._hint2:
            p.setFont(_font(theme.F_BODY, 10, spacing=0.2))
            p.setPen(_q("#879caf"))
            p.drawText(QRect(r.x() + 20, cy + 14, r.width() - 40, 46),
                       Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, self._hint2)

    def _paint_badge(self, p, r):
        w, h = 66, 20
        x, y = r.left() + 10, r.top() + 10
        col = QColor(theme.OK if self._live else "#7189a0")
        bg = QColor(col)
        bg.setAlpha(26)
        edge = QColor(col)
        edge.setAlpha(115)
        p.setPen(QPen(edge, 1))
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(x, y, w, h), 3, 3)

        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawEllipse(QPointF(x + 12, y + h / 2.0), 3.0, 3.0)

        p.setFont(_font(theme.F_BODY, 9, spacing=0.6))
        p.setPen(col)
        p.drawText(QRectF(x + 21, y, w - 25, h), Qt.AlignLeft | Qt.AlignVCenter,
                   "取流中" if self._live else "未取流")

    def _paint_stat(self, p, r):
        # 状态条压在画面底部，用向上渐隐的底，画面不被生硬的色块切断
        sh = 22
        sr = QRectF(r.left(), r.bottom() - sh, r.width(), sh)
        g = QLinearGradient(sr.topLeft(), sr.bottomLeft())
        g.setColorAt(0.0, QColor(31, 55, 80, 8))
        g.setColorAt(1.0, QColor(31, 55, 80, 26))
        p.setPen(Qt.NoPen)
        p.fillRect(sr, g)

        if self._stat:
            w, h, fps = self._stat
            left = "%d × %d　·　%.1f fps　·　缩放 %d%%" % (
                w, h, fps, round(self.abs_scale() * 100.0))
        elif self._img is not None:
            left = "　·　缩放 %d%%" % round(self.abs_scale() * 100.0)
        else:
            left = "等待画面"

        p.setFont(_font(theme.F_MONO, 10, spacing=0.3))
        p.setPen(_q(theme.TXT2))
        p.drawText(sr.adjusted(10, 0, -10, 0), Qt.AlignLeft | Qt.AlignVCenter, left)
        if r.width() > 520:
            p.setPen(_q("#96acc0"))
            p.drawText(sr.adjusted(10, 0, -10, 0), Qt.AlignRight | Qt.AlignVCenter,
                       "滚轮缩放　·　拖动平移　·　双击复位")

    def resizeEvent(self, e):
        self._clamp_pan()
        super(CameraView, self).resizeEvent(e)

    # ------------------------------------------------------------ 缩放
    def wheelEvent(self, event):
        if self._img is None or event.angleDelta().y() == 0:
            event.ignore()
            return
        fit = self._fit_scale()
        max_zoom = max(1.0, self.ZOOM_MAX_ABS / fit) if fit > 0 else 1.0
        factor = 1.25 ** (event.angleDelta().y() / 120.0)
        new_zoom = max(1.0, min(max_zoom, self._zoom * factor))
        if abs(new_zoom - self._zoom) < 1e-6:
            event.accept()
            return

        # 光标下的那个点保持不动
        rect_old, s_old = self._target_rect()
        px, py = event.pos().x(), event.pos().y()
        ix = (px - rect_old.x()) / s_old
        iy = (py - rect_old.y()) / s_old

        self._zoom = new_zoom
        if new_zoom <= 1.0001:
            self._pan = QPointF(0.0, 0.0)
        else:
            s_new = fit * self._zoom
            base_x = (self.width() - self._img.width() * s_new) / 2.0
            base_y = (self.height() - self._img.height() * s_new) / 2.0
            self._pan = QPointF(px - ix * s_new - base_x,
                                py - iy * s_new - base_y)
        self._clamp_pan()
        self.update()
        event.accept()

    # ------------------------------------------------------------ 平移
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._zoom > 1.0001:
            self._drag = (event.pos(), QPointF(self._pan))
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super(CameraView, self).mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            p0, pan0 = self._drag
            self._pan = QPointF(pan0.x() + event.pos().x() - p0.x(),
                                pan0.y() + event.pos().y() - p0.y())
            self._clamp_pan()
            self.update()
            event.accept()
            return
        super(CameraView, self).mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            self._drag = None
            self.setCursor(Qt.CrossCursor)
            event.accept()
            return
        super(CameraView, self).mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.reset_view()
        event.accept()


# ---------------------------------------------------------------- 页面
class DevicePage(QWidget):
    """设备管理主页面。"""

    scan_done = pyqtSignal(object, str)

    def __init__(self, mod, manager, parent=None, embedded=False):
        super(DevicePage, self).__init__(parent)
        self.mod = mod
        self.mgr = manager
        # embedded=True：本页被 device_module 装进子页签里，模块标题条由外层统一画，
        # 自己不再重复画一条，也不再预留那 82px 的顶部边距。
        self._embedded = bool(embedded)
        self.setStyleSheet(STYLE)
        self._sel = DEVICE_ORDER[0]
        self._editing = False
        self._dirty = False
        self._lost_handling = False
        self._cam_off_drawn = False
        self._preview_paused = False
        self._log_expanded = True
        self._log_user = False      # 用户手动折叠过就不再自动切换

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 0 if self._embedded else HEAD_H, 18, 18)
        root.setSpacing(12)

        # 设备清单横着排在顶部，剩下整块地方都留给详情（相机实时画面）
        root.addWidget(self._build_strip(), 0)
        root.addWidget(self._build_right(), 1)

        # 信号
        self.mgr.device_changed.connect(self._refresh_states)
        self.mgr.logged.connect(self._log)
        self.scan_done.connect(self._on_scan_done)

        self._select(self._sel)
        self._refresh_states()
        self._log("设备管理已就绪。点【连接】建立通讯；参数默认只读，点【编辑】才能改。", "info")

    # ------------------------------------------------------------ 左：清单
    def _build_strip(self):
        """设备清单横着排一排，占顶部一条固定高度。

        竖着放会占掉左边一整列，把相机实时画面挤成一小块；
        横排之后清单只占顶部一条，下面整块宽度都留给画面。
        """
        panel = Panel()
        lay = QHBoxLayout(panel)
        lay.setContentsMargins(14, 9, 14, 9)
        lay.setSpacing(12)

        # 左：标题 + 连接计数
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(2)
        lb = QLabel("设备清单")
        lb.setFont(_font(theme.F_BODY, 12, bold=True, spacing=0.6))
        lb.setStyleSheet("color:#24506f;background:transparent;")
        self.lb_summary = QLabel("0 / 3 已连接")
        self.lb_summary.setFont(_font(theme.F_MONO, 10, spacing=0.4))
        self.lb_summary.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        left.addWidget(lb)
        left.addWidget(self.lb_summary)
        left.addStretch(1)
        lay.addLayout(left)

        # 中：三张设备卡横排
        self.cards = {}
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        keys = list(DEVICE_ORDER)
        for i, key in enumerate(keys):
            card = DeviceCard(key)
            card.clicked.connect(self._on_card_click)
            card.connect_clicked.connect(self._on_connect)
            card.disconnect_clicked.connect(self._on_disconnect)
            card.set_sep(i < len(keys) - 1)
            # 236 是实测出来的：再宽的话三张卡加上右侧按钮会超出页面宽度，
            # 布局会把面板右边距挤掉，「全部断开」直接贴到面板边上
            card.setMinimumWidth(236)
            self.cards[key] = card
            row.addWidget(card, 1)
        lay.addLayout(row, 1)

        # 右：全部连接 / 全部断开 + 图例
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(5)
        hr = QHBoxLayout()
        hr.setSpacing(8)
        self.btn_all_conn = QPushButton("全部连接")
        self.btn_all_conn.setObjectName("primary")
        self.btn_all_conn.setFixedHeight(28)
        self.btn_all_conn.setFixedWidth(88)
        self.btn_all_conn.clicked.connect(self._on_connect_all)
        self.btn_all_disc = QPushButton("全部断开")
        self.btn_all_disc.setObjectName("act")
        self.btn_all_disc.setFixedHeight(28)
        self.btn_all_disc.setFixedWidth(88)
        self.btn_all_disc.clicked.connect(self._on_disconnect_all)
        hr.addWidget(self.btn_all_conn)
        hr.addWidget(self.btn_all_disc)
        right.addLayout(hr)

        tip = QLabel("绿点＝已连接　红点＝未连接")
        tip.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        tip.setStyleSheet("color:#8ba1b5;background:transparent;")
        tip.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        right.addWidget(tip)
        right.addStretch(1)
        lay.addLayout(right)

        return panel

    # ------------------------------------------------------------ 右：详情
    def _build_right(self):
        panel = Panel()
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(16, 13, 16, 13)
        outer.setSpacing(10)

        # 标题行
        head = QHBoxLayout()
        head.setSpacing(10)
        self.lb_title = QLabel("三轴控制器")
        self.lb_title.setFont(_font(theme.F_BODY, 16, bold=True, spacing=0.8))
        self.lb_title.setStyleSheet("color:#14324f;background:transparent;")
        self.badge = Badge()
        self.lb_model = QLabel("")
        self.lb_model.setFont(_font(theme.F_MONO, 10, spacing=0.3))
        self.lb_model.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)

        self.lb_editing = QLabel("编辑中")
        self.lb_editing.setFont(_font(theme.F_BODY, 10, bold=True, spacing=0.6))
        self.lb_editing.setStyleSheet(
            "color:#17497f;background:rgba(27,87,160,0.12);"
            "border:1px solid #17497f;border-radius:3px;padding:1px 7px;")
        self.lb_editing.setVisible(False)

        self.btn_edit = QPushButton("编辑")
        self.btn_edit.setObjectName("act")
        self.btn_edit.setFixedHeight(28)
        self.btn_edit.setFixedWidth(76)
        self.btn_edit.clicked.connect(self._on_edit)

        self.btn_save = QPushButton("保存")
        self.btn_save.setObjectName("primary")
        self.btn_save.setFixedHeight(28)
        self.btn_save.setFixedWidth(76)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_save.setEnabled(False)

        head.addWidget(self.lb_title)
        head.addWidget(self.badge)
        head.addWidget(self.lb_editing)
        head.addSpacing(4)
        head.addWidget(self.lb_model)
        head.addStretch(1)
        head.addWidget(self.btn_edit)
        head.addWidget(self.btn_save)
        outer.addLayout(head)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background:%s;" % theme.LINE)
        outer.addWidget(line)

        # 内容区分两页：普通设备（通讯 + 轴参数）／相机（左画面 + 右参数）
        self.body_stack = QStackedWidget()
        self.body_stack.setStyleSheet("background:transparent;")
        self.body_stack.addWidget(self._build_params_page())
        self.body_stack.addWidget(self._build_camera_page())
        outer.addWidget(self.body_stack, 1)

        # ---- 日志（可折叠；相机页会自动折叠，把高度全让给实时画面）
        log_head = QHBoxLayout()
        log_head.setSpacing(8)
        lb_log = QLabel("运行日志")
        lb_log.setFont(_font(theme.F_BODY, 10, bold=True, spacing=0.6))
        lb_log.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        self.lb_log_last = QLabel("")
        self.lb_log_last.setFont(_font(theme.F_MONO, 10, spacing=0.1))
        self.lb_log_last.setStyleSheet("color:#8ba1b5;background:transparent;")
        self.btn_log = QPushButton("收起")
        self.btn_log.setObjectName("act")
        self.btn_log.setFixedSize(58, 22)
        self.btn_log.setToolTip("折叠后日志不占画面高度")
        self.btn_log.clicked.connect(self._toggle_log)
        log_head.addWidget(lb_log)
        log_head.addWidget(self.lb_log_last, 1)
        log_head.addWidget(self.btn_log)
        outer.addLayout(log_head)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setFixedHeight(84)
        self.txt_log.setFont(_font(theme.F_MONO, 10, spacing=0.1))
        self.txt_log.document().setMaximumBlockCount(300)
        outer.addWidget(self.txt_log)
        self._log_open = True
        self._log_msg = ""

        return panel

    # ------------------------------------------------------------ 日志折叠
    def _toggle_log(self, force=None):
        """折叠 / 展开运行日志。折叠后只留一行标题，实时画面能多拿 84px。"""
        self._log_user = True
        self._set_log_open((not self._log_open) if force is None else bool(force))

    def _set_log_open(self, open_):
        self._log_open = bool(open_)
        self.txt_log.setVisible(self._log_open)
        self.btn_log.setText("收起" if self._log_open else "展开")
        # 折叠时把最后一条消息显示在标题行，省得完全看不到
        self.lb_log_last.setText("" if self._log_open else self._log_msg)
        self.lb_log_last.setVisible(not self._log_open)

    # ------------------------------------------------------------ 普通设备页
    def _build_params_page(self):
        """三轴 / 探针的参数区：左栏通讯参数 + 说明，右栏轴参数表。

        横排清单之后纵向空间变紧，这里改成左右两栏 —— 横向本来就有富余，
        把「通讯参数 + 说明」放左边、「轴参数表」放右边，Z 行就不会被裁掉了。
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background:transparent;"
                             "QScrollArea>QWidget>QWidget{background:transparent;}")
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        outer = QHBoxLayout(inner)
        outer.setContentsMargins(0, 0, 6, 0)
        outer.setSpacing(18)

        # ================= 左栏：通讯参数 + 参数说明
        left = QWidget()
        left.setStyleSheet("background:transparent;")
        left.setFixedWidth(340)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(10)

        # ---- 连接参数
        self.sec_conn = self._section("通讯参数")
        self.grid_conn = QGridLayout()
        self.grid_conn.setHorizontalSpacing(10)
        self.grid_conn.setVerticalSpacing(7)
        lv.addWidget(self.sec_conn)
        lv.addLayout(self.grid_conn)

        # ---- 参数说明（每台设备一段真实说明，不是占位文案）
        self.lb_notes = QLabel("")
        self.lb_notes.setWordWrap(True)
        self.lb_notes.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        self.lb_notes.setStyleSheet(
            "color:#4a6478;background:rgba(27,87,160,0.03);"
            "border:1px solid #eaf2fa;border-radius:3px;padding:6px 11px;")
        self.lb_notes.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        lv.addWidget(self.lb_notes, 1)
        outer.addWidget(left, 0)

        # ================= 右栏：轴参数
        right = QWidget()
        right.setStyleSheet("background:transparent;")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)

        self.lb_axis = self._section("轴参数")
        self.axis_table = AxisTable(limit_optional=False)
        self.axis_stack = QWidget()
        self.axis_stack.setStyleSheet("background:transparent;")
        asv = QVBoxLayout(self.axis_stack)
        asv.setContentsMargins(0, 0, 0, 0)
        asv.setSpacing(6)
        asv.addWidget(self.axis_table)
        self.lb_axis_hint = QLabel("")
        self.lb_axis_hint.setFont(_font(theme.F_BODY, 10, spacing=0.3))
        self.lb_axis_hint.setStyleSheet("color:#8ba1b5;background:transparent;")
        self.lb_axis_hint.setWordWrap(True)
        rv.addWidget(self.lb_axis)
        rv.addWidget(self.axis_stack)
        rv.addWidget(self.lb_axis_hint)
        rv.addStretch(1)
        outer.addWidget(right, 1)

        scroll.setWidget(inner)
        return scroll

    # ------------------------------------------------------------ 相机页
    def _build_camera_page(self):
        """相机页：左边实时画面，右边通讯/采集参数 —— 调参时画面一直在眼前。"""
        page = QWidget()
        page.setStyleSheet("background:transparent;")
        h = QHBoxLayout(page)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)

        # ---------- 左：实时画面
        view_panel = Panel()
        vp = QVBoxLayout(view_panel)
        vp.setContentsMargins(12, 10, 12, 10)
        vp.setSpacing(8)

        vh = QHBoxLayout()
        vh.setContentsMargins(0, 0, 0, 0)
        vh.setSpacing(8)
        lb_view = self._section("实时画面")
        foot = QLabel("只用于调参显示，不落盘；滚轮缩放，按住左键拖动，双击复位")
        foot.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        foot.setStyleSheet("color:#96acc0;background:transparent;")
        self.btn_reset_view = QPushButton("复位视图")
        self.btn_reset_view.setObjectName("act")
        self.btn_reset_view.setFixedHeight(24)
        self.btn_reset_view.setToolTip("回到整幅适配窗口（也可以双击画面）")
        self.btn_reset_view.clicked.connect(lambda: self.view.reset_view())

        self.btn_stream = QPushButton("停止取流")
        self.btn_stream.setObjectName("act")
        self.btn_stream.setFixedHeight(24)
        self.btn_stream.setFixedWidth(78)
        self.btn_stream.clicked.connect(self._on_toggle_stream)

        vh.addWidget(lb_view)
        vh.addWidget(foot)
        vh.addStretch(1)
        vh.addWidget(self.btn_reset_view)
        vh.addWidget(self.btn_stream)
        vp.addLayout(vh)

        self.view = CameraView()
        # 画面框按传感器长宽比（默认 3:2，与 3072×2048 一致）摆放
        self.view_host = RatioBox(3072.0 / 2048.0)
        self.view_host.set_child(self.view)
        vp.addWidget(self.view_host, 1)
        h.addWidget(view_panel, 1)

        # ---------- 右：参数
        # 画面框按传感器比例摆放后，宽度由高度决定，横向会剩下一些空间；
        # 与其让画面两边留白，不如把右栏放宽，参数说明不用挤成一条
        side = QWidget()
        side.setStyleSheet("background:transparent;")
        side.setFixedWidth(392)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background:transparent;"
                             "QScrollArea>QWidget>QWidget{background:transparent;}")
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(0, 2, 6, 2)
        iv.setSpacing(10)

        # 通讯参数（相机只有序号 + 扫描）
        self.sec_cam_conn = self._section("通讯参数")
        self.grid_cam_conn = QGridLayout()
        self.grid_cam_conn.setHorizontalSpacing(10)
        self.grid_cam_conn.setVerticalSpacing(7)
        iv.addWidget(self.sec_cam_conn)
        iv.addLayout(self.grid_cam_conn)

        # 采集参数
        self.lb_cam = self._section("采集参数")
        self.cam_box = QWidget()
        self.cam_box.setStyleSheet("background:transparent;")
        cg = QGridLayout(self.cam_box)
        cg.setContentsMargins(0, 0, 0, 0)
        cg.setHorizontalSpacing(4)
        cg.setVerticalSpacing(7)

        self.cam_exp = _spin(1.0, 1000000.0, 330000.0, dec=1, step=100.0, width=96)
        self.cam_exp_auto = QCheckBox("自动曝光")
        self.cam_gain = _spin(0.0, 48.0, 0.0, dec=2, step=0.5, width=96)
        self.cam_fps = _spin(0.1, 1000.0, 3.03, dec=2, step=1.0, width=96)
        self.cam_w = QSpinBox()
        self.cam_h = QSpinBox()
        for sb in (self.cam_w, self.cam_h):
            sb.setRange(0, 65535)
            sb.setFixedWidth(96)
            sb.setFixedHeight(26)
            sb.setButtonSymbols(QSpinBox.NoButtons)
            sb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            sb.setSpecialValueText("自动")
        self.cam_ox = QSpinBox()
        self.cam_oy = QSpinBox()
        for sb in (self.cam_ox, self.cam_oy):
            sb.setRange(0, 65535)
            sb.setFixedWidth(96)
            sb.setFixedHeight(26)
            sb.setButtonSymbols(QSpinBox.NoButtons)
            sb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        rows = [
            ("曝光", self.cam_exp, "µs", "增益", self.cam_gain, "dB", None),
            ("帧率", self.cam_fps, "fps", "", None, "", self.cam_exp_auto),
            ("宽", self.cam_w, "px", "高", self.cam_h, "px", None),
            ("偏移 X", self.cam_ox, "px", "偏移 Y", self.cam_oy, "px", None),
        ]
        for i, (n1, w1, u1, n2, w2, u2, extra) in enumerate(rows):
            for base, nm, wd, unit in ((0, n1, w1, u1), (3, n2, w2, u2)):
                lb = QLabel(nm)
                lb.setFont(_font(theme.F_BODY, 11, spacing=0.3))
                lb.setStyleSheet("color:%s;background:transparent;" % theme.TXT2)
                lb.setFixedWidth(54)
                cg.addWidget(lb, i, base)
                if wd is not None:
                    cg.addWidget(wd, i, base + 1)
                if unit:
                    lu = QLabel(unit)
                    lu.setFont(_font(theme.F_MONO, 9, spacing=0.2))
                    lu.setStyleSheet("color:#879caf;background:transparent;")
                    cg.addWidget(lu, i, base + 2)
            if extra is not None:
                cg.addWidget(extra, i, 4, 1, 2)
        cg.setColumnStretch(6, 1)
        iv.addWidget(self.lb_cam)
        iv.addWidget(self.cam_box)

        # 参数说明
        self.lb_notes_cam = QLabel("")
        self.lb_notes_cam.setWordWrap(True)
        self.lb_notes_cam.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        self.lb_notes_cam.setStyleSheet(
            "color:#4a6478;background:rgba(27,87,160,0.03);"
            "border:1px solid #eaf2fa;border-radius:3px;padding:8px 10px;")
        self.lb_notes_cam.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        iv.addWidget(self.lb_notes_cam)

        iv.addStretch(1)
        scroll.setWidget(inner)
        sv.addWidget(scroll, 1)
        h.addWidget(side, 0)

        # 取流信号：取流线程只发信号，画面永远在主线程里画
        cam = self.mgr.driver("camera")
        if cam is not None:
            cam.bridge.frame.connect(self._on_frame)
            cam.bridge.stats.connect(self._on_stats)
            cam.bridge.grab_error.connect(self._on_grab_err)
            cam.bridge.lost.connect(self._on_cam_lost)
        self.view.clear("相机未连接", "点【连接】后这里会显示采集画面")
        return page

    @staticmethod
    def _section(text):
        lb = QLabel(text)
        lb.setFont(_font(theme.F_BODY, 11, bold=True, spacing=0.8))
        lb.setStyleSheet("color:%s;background:transparent;" % theme.AMBER_DIM)
        return lb

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, _e):
        if self._embedded:
            return          # 标题条由外层 device_module 画
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        paint_module_head(p, 18, HEAD_TOP, self.width() - 36,
                          self.mod["idx"], self.mod["name"], self.mod["desc"])
        p.end()

    # ------------------------------------------------------------ 选中
    def _on_card_click(self, key):
        if key == self._sel:
            return
        if self._editing:
            r = QMessageBox.question(
                self, "未保存的修改",
                "%s 的参数还没保存，切换设备会丢弃这些修改。\n继续吗？"
                % DEVICE_META[self._sel]["name"],
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        self._select(key)

    def _select(self, key):
        self._sel = key
        self._editing = False
        self._dirty = False
        meta = DEVICE_META[key]
        for k, card in self.cards.items():
            card.set_active(k == key)

        self.lb_title.setText(meta["name"])
        self.lb_model.setText(meta["model"])
        self.badge.set_state(self.mgr.state(key))
        self.btn_edit.setText("编辑")
        self.btn_edit.setEnabled(True)
        self.btn_save.setEnabled(False)

        is_cam = (key == "camera")
        self.body_stack.setCurrentIndex(1 if is_cam else 0)
        if not is_cam:
            self._preview_paused = False
            self._stop_preview()

        # 看相机时把日志折起来给画面让高度；看别的设备再展开
        # （用户手动折过就不再自动动，免得跟用户较劲）
        if not self._log_user:
            self._set_log_open(not is_cam)

        self._build_conn_form(key)
        self._load_values()
        self._set_editable(False)
        if is_cam:
            self._sync_preview()

    def _build_conn_form(self, key):
        """重建通讯参数区（三台设备的字段不一样；相机在右栏窄格子里重新排）。"""
        grid = self.grid_cam_conn if key == "camera" else self.grid_conn
        while grid.count():
            it = grid.takeAt(0)
            w = it.widget()
            if w is not None:
                # 必须立刻摘下来：deleteLater 只是排队，旧的输入框会留在原格上，
                # 和新控件叠在一起（切设备时表现为「标签和值对不上」）
                w.setParent(None)
                w.deleteLater()

        cfg = self.mgr.cfg
        self.inp = {}
        # 这两个控件只在相机页存在，切走后对象会被销毁，引用要置空
        self.btn_scan = None
        self.lb_scan = None

        if key == "camera":
            self._build_cam_conn_form(grid, cfg["camera"])
            return

        fields = []
        if key == "motion":
            fields = [("ip", "IP 地址", "text", cfg["motion"].get("ip", "")),
                      ("port", "端口", "int", cfg["motion"].get("port", 8088)),
                      ("device_id", "设备 ID", "int", cfg["motion"].get("device_id", 0))]
        elif key == "probe":
            fields = [("mode", "通讯方式", "combo", cfg["probe"].get("mode", "eth")),
                      ("addr", "设备地址", "text", cfg["probe"].get("addr", "")),
                      ("card_no", "卡号", "int", cfg["probe"].get("card_no", 1)),
                      ("spm", "脉冲当量", "float", cfg["probe"].get("spm", 12800.0))]

        for row, (name, label, kind, val) in enumerate(fields):
            lb = QLabel(label)
            lb.setFont(_font(theme.F_BODY, 11, spacing=0.3))
            lb.setStyleSheet("color:%s;background:transparent;" % theme.TXT2)
            lb.setFixedWidth(74)
            if kind == "text":
                wd = QLineEdit(str(val))
                wd.setFixedWidth(200)
                wd.setFixedHeight(26)
            elif kind == "int":
                wd = QSpinBox()
                wd.setRange(0, 65535)
                wd.setValue(int(val))
                wd.setFixedWidth(200)
                wd.setFixedHeight(26)
                wd.setButtonSymbols(QSpinBox.NoButtons)
                wd.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            elif kind == "float":
                wd = _spin(1.0, 1000000.0, val, dec=2, step=100.0, width=200)
            else:
                wd = QComboBox()
                wd.addItems(["网口", "串口"])
                wd.setCurrentIndex(1 if str(val) == "serial" else 0)
                wd.setFixedWidth(200)
                wd.setFixedHeight(26)
            self.inp[name] = wd
            grid.addWidget(lb, row, 0)
            grid.addWidget(wd, row, 1)

        # 探针：脉冲当量说明（放在输入框正下方，别横着甩到右边）
        if key == "probe":
            unit = QLabel("脉冲/mm，用于把 mm 限位换算成卡上的脉冲")
            unit.setFont(_font(theme.F_BODY, 10, spacing=0.2))
            unit.setStyleSheet("color:#8ba1b5;background:transparent;")
            unit.setWordWrap(True)
            grid.addWidget(unit, len(fields), 1, 1, 1)

        grid.setColumnStretch(2, 1)

    def _build_cam_conn_form(self, grid, cfg):
        """相机页右栏的通讯区：序号 + 扫描，竖排（右栏窄，横排会挤）。"""
        lb = QLabel("相机序号")
        lb.setFont(_font(theme.F_BODY, 11, spacing=0.3))
        lb.setStyleSheet("color:%s;background:transparent;" % theme.TXT2)
        lb.setFixedWidth(62)

        wd = QSpinBox()
        wd.setRange(0, 65535)
        wd.setValue(int(cfg.get("device_index", 0)))
        wd.setFixedWidth(104)
        wd.setFixedHeight(26)
        wd.setButtonSymbols(QSpinBox.NoButtons)
        wd.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.inp["device_index"] = wd

        self.btn_scan = QPushButton("扫描相机")
        self.btn_scan.setObjectName("act")
        self.btn_scan.setFixedHeight(26)
        self.btn_scan.setFixedWidth(104)
        self.btn_scan.clicked.connect(self._on_scan)

        self.lb_scan = QLabel("未扫描")
        self.lb_scan.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        self.lb_scan.setStyleSheet("color:#8ba1b5;background:transparent;")

        note = QLabel("序号＝枚举顺序；连接时若没扫描过会自动枚举。"
                      "参数范围以相机实际能力为准，超范围会被相机端拒绝。")
        note.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        note.setStyleSheet("color:#8ba1b5;background:transparent;")
        note.setWordWrap(True)

        # 扫描按钮和状态放同一行，省一行高度给下面的说明
        scan_row = QHBoxLayout()
        scan_row.setContentsMargins(0, 0, 0, 0)
        scan_row.setSpacing(10)
        scan_row.addWidget(self.btn_scan, 0)
        scan_row.addWidget(self.lb_scan, 1)

        grid.addWidget(lb, 0, 0)
        grid.addWidget(wd, 0, 1)
        grid.addLayout(scan_row, 1, 0, 1, 3)
        grid.addWidget(note, 2, 0, 1, 3)
        grid.setColumnStretch(2, 1)

    # ------------------------------------------------------------ 取值
    def _load_values(self):
        cfg = self.mgr.cfg
        if self._sel == "camera":
            c = cfg["camera"]
            self.cam_exp.setValue(float(c.get("exposure", 330000.0)))
            self.cam_exp_auto.setChecked(bool(c.get("auto_exposure")))
            self.cam_gain.setValue(float(c.get("gain", 0.0)))
            self.cam_fps.setValue(float(c.get("fps", 3.03)))
            self.cam_w.setValue(int(c.get("width") or 0))
            self.cam_h.setValue(int(c.get("height") or 0))
            self.cam_ox.setValue(int(c.get("offset_x") or 0))
            self.cam_oy.setValue(int(c.get("offset_y") or 0))
        else:
            # 探针：限位可以留空（原配置里 null 就是不限制），且是六轴（XYZ + UVW）
            need_optional = (self._sel == "probe")
            need_axes = PROBE_AXES if need_optional else AXES
            if self.axis_table.limit_optional != need_optional or \
                    tuple(self.axis_table.axes) != tuple(need_axes):
                new_table = AxisTable(limit_optional=need_optional, axes=need_axes)
                lay = self.axis_stack.layout()
                lay.replaceWidget(self.axis_table, new_table)
                self.axis_table.setParent(None)
                self.axis_table.deleteLater()
                self.axis_table = new_table
            self.axis_table.set_values(cfg[self._sel].get("axes", {}))
            self.axis_table.set_editable(self._editing)

        hints = {
            "motion": "限位是软件限位：每次下发运动前比对目标位置，超了就不发指令。"
                      "填 0 表示该方向不限制。速度与加减速随运动指令一起下发。",
            "probe": "六轴（XYZ + UVW），限位单位 mm（机械坐标），留空表示该方向不限制。"
                     "保存时若已连接，会把限位换算成脉冲下发到卡（GA_SetSoftLimit）。",
        }
        self.lb_axis_hint.setText(hints.get(self._sel, ""))

        notes = {
            "motion": "· 限位是软件限位：运动指令下发前比对目标位置，超限不发指令并提示，不会硬撞。\n"
                      "· 速度与加减速随每条运动指令一起下发，单位 mm/s、mm/s²。\n"
                      "· 通讯为 TCP，默认 192.168.0.30:8088；设备 ID 由控制器端设定。\n"
                      "· 参数保存后立即生效，下次启动仍然是保存的值。",
            "probe": "· 卡是六轴：轴 1~6 依次是 X/Y/Z/U/V/W，两组平面轴独立配置。\n"
                     "· 限位留空＝该方向不限制；连接状态下保存会自动换算成脉冲下发。\n"
                     "· 脉冲当量 12800 脉冲/mm，换丝杠或改细分后需重新标定。",
            "camera": "· 画面是实时取流，只用于调参，不会存盘。\n"
                      "· 宽或高填 0 表示用相机当前分辨率；宽高要一起填或一起留 0。\n"
                      "· 曝光/增益/帧率改完点【保存】会立即下发，画面跟着变。\n"
                      "· 改宽高/偏移时会自动停一下流再续上（相机不允许取流中改 ROI）。",
        }
        if self._sel == "camera":
            self.lb_notes_cam.setText(notes["camera"])
        else:
            self.lb_notes.setText(notes.get(self._sel, ""))

    def _set_editable(self, on):
        self.lb_editing.setVisible(bool(on))
        for wd in getattr(self, "inp", {}).values():
            wd.setEnabled(on)
        self.axis_table.set_editable(on)
        for wd in (self.cam_exp, self.cam_gain, self.cam_fps,
                   self.cam_w, self.cam_h, self.cam_ox, self.cam_oy,
                   self.cam_exp_auto):
            wd.setEnabled(on)
        if getattr(self, "btn_scan", None) is not None:
            self.btn_scan.setEnabled(on)

    # ------------------------------------------------------------ 实时画面
    def _camera(self):
        return self.mgr.driver("camera")

    def _sync_preview(self):
        """该开流就开流，该停就停：选中相机 + 已连接 + 页面可见，三者缺一不可。"""
        cam = self._camera()
        if cam is None:
            return
        connected = self.mgr.is_connected("camera")
        want = (self._sel == "camera" and connected and self.isVisible()
                and not self._preview_paused)

        if not connected:
            self._preview_paused = False
            if cam.streaming:
                cam.stop_stream()
            if not self._cam_off_drawn:
                # 断开了就别把上一帧留在那儿冒充实时画面
                self.view.clear("相机未连接", "点【连接】后这里会显示采集画面")
                self._cam_off_drawn = True
        elif want:
            self._cam_off_drawn = False
            if not cam.streaming:
                self.view.clear("正在取流…", "首帧到来后立即显示")
                ok, msg = cam.start_stream()
                self._log(msg, "ok" if ok else "err")
                if not ok:
                    self.view.clear("取流失败", msg)
        else:
            self._cam_off_drawn = False
            if cam.streaming:
                cam.stop_stream()
                self.view.clear("已停止取流", "点【开始取流】或重新进入本页恢复画面")
        self._sync_stream_btn()

    def _stop_preview(self):
        cam = self._camera()
        if cam is not None and cam.streaming:
            cam.stop_stream()
            self._sync_stream_btn()

    def _sync_stream_btn(self):
        cam = self._camera()
        on = bool(cam is not None and cam.streaming)
        connected = self.mgr.is_connected("camera")
        if getattr(self, "btn_stream", None) is not None:
            self.btn_stream.setText("停止取流" if on else "开始取流")
            self.btn_stream.setEnabled(connected or on)
        if getattr(self, "view", None) is not None:
            self.view.set_live(on)

    def _on_toggle_stream(self):
        cam = self._camera()
        if cam is None:
            return
        if cam.streaming:
            cam.stop_stream()
            self._preview_paused = True
            self.view.clear("已停止取流", "点【开始取流】恢复画面")
            self._log("已停止取流（相机仍连接）", "info")
        else:
            if not self.mgr.is_connected("camera"):
                self._log("相机还没连接，先点清单里的【连接】", "warn")
                return
            self._preview_paused = False
            ok, msg = cam.start_stream()
            self._log(msg, "ok" if ok else "err")
            if ok:
                self.view.clear("正在取流…", "首帧到来后立即显示")
            else:
                self.view.clear("取流失败", msg)
        self._sync_stream_btn()

    def _on_frame(self, img):
        # 画面框的长宽比跟着真实帧走：改了 ROI/分辨率后框也跟着变，
        # 不会出现"整幅装进去但两边空一大块"或者画面被裁掉的情况
        if img is not None and not img.isNull() and img.width() > 0:
            self.view_host.set_ratio(img.width() / float(img.height()))
        self.view.set_image(img)
        if getattr(self, "btn_stream", None) is not None and \
                self.btn_stream.text() != "停止取流":
            self._sync_stream_btn()

    def _on_stats(self, fps, w, h):
        if w > 0 and h > 0:
            self.view_host.set_ratio(w / float(h))
        self.view.set_stat(w, h, fps)

    def _on_grab_err(self, ret):
        self._log("取流错误 0x%08X（偶发可忽略，持续出现请检查网线与带宽）"
                  % (ret & 0xFFFFFFFF), "warn")

    def _on_cam_lost(self):
        if getattr(self, "_lost_handling", False):
            return
        self._lost_handling = True
        try:
            self._log("相机连接已断开（掉线或被拔出），正在释放资源…", "err")
            # 先占住提示位，免得紧接着的状态刷新把它盖成「相机未连接」
            self._cam_off_drawn = True
            self.view.clear("相机已断开", "检查网线 / 供电后重新连接")
            self.mgr.disconnect("camera")
        finally:
            QTimer.singleShot(1200, lambda: setattr(self, "_lost_handling", False))

    def showEvent(self, e):
        super(DevicePage, self).showEvent(e)
        if getattr(self, "view", None) is not None:
            QTimer.singleShot(0, self._sync_preview)

    def hideEvent(self, e):
        self._stop_preview()
        super(DevicePage, self).hideEvent(e)

    # ------------------------------------------------------------ 编辑/保存
    def _on_edit(self):
        if self._editing:
            # 编辑态下这颗按钮是「取消」：丢弃改动，重新按配置回填
            self._editing = False
            self._set_editable(False)
            self.btn_edit.setText("编辑")
            self.btn_save.setEnabled(False)
            self._build_conn_form(self._sel)
            self._load_values()
            self._log("已取消编辑，未保存的修改已丢弃。", "warn")
            return
        self._editing = True
        self._dirty = False
        self._set_editable(True)
        self.btn_edit.setText("取消")
        self.btn_edit.setEnabled(True)
        self.btn_save.setEnabled(True)
        self._log("已进入编辑：改完点【保存】写入 data/devices.json。", "info")

    def _on_save(self):
        cfg = copy.deepcopy(self.mgr.cfg)
        err = None

        if self._sel == "motion":
            cfg["motion"]["ip"] = self.inp["ip"].text().strip() or "192.168.0.30"
            cfg["motion"]["port"] = int(self.inp["port"].value())
            cfg["motion"]["device_id"] = int(self.inp["device_id"].value())
            vals, err = self.axis_table.values()
            if not err:
                for name in AXES:
                    v = vals[name]
                    if v["limit_pos"] != 0 and v["limit_pos"] < v["limit_neg"] and \
                            v["limit_neg"] != 0:
                        err = "%s 轴正限位应大于负限位" % name.upper()
                        break
                    if v["speed"] <= 0:
                        err = "%s 轴速度必须大于 0" % name.upper()
                        break
                    if v["acc"] <= 0 or v["dec"] <= 0:
                        err = "%s 轴加速度/减速度必须大于 0" % name.upper()
                        break
                    cfg["motion"]["axes"][name].update(v)

        elif self._sel == "probe":
            cfg["probe"]["mode"] = "serial" if self.inp["mode"].currentIndex() == 1 else "eth"
            cfg["probe"]["addr"] = self.inp["addr"].text().strip()
            cfg["probe"]["card_no"] = int(self.inp["card_no"].value())
            cfg["probe"]["spm"] = float(self.inp["spm"].value())
            vals, err = self.axis_table.values()
            if not err:
                for name in self.axis_table.axes:
                    v = vals[name]
                    if v["limit_pos"] is not None and v["limit_neg"] is not None \
                            and v["limit_pos"] <= v["limit_neg"]:
                        err = "%s 轴正限位应大于负限位" % name.upper()
                        break
                    if v["speed"] <= 0 or v["acc"] <= 0 or v["dec"] <= 0:
                        err = "%s 轴速度/加速度/减速度必须大于 0" % name.upper()
                        break
                    cfg["probe"]["axes"].setdefault(name, {}).update(v)

        else:
            cfg["camera"]["device_index"] = int(self.inp["device_index"].value())
            cfg["camera"]["exposure"] = float(self.cam_exp.value())
            cfg["camera"]["auto_exposure"] = bool(self.cam_exp_auto.isChecked())
            cfg["camera"]["gain"] = float(self.cam_gain.value())
            cfg["camera"]["fps"] = float(self.cam_fps.value())
            cfg["camera"]["width"] = int(self.cam_w.value())
            cfg["camera"]["height"] = int(self.cam_h.value())
            cfg["camera"]["offset_x"] = int(self.cam_ox.value())
            cfg["camera"]["offset_y"] = int(self.cam_oy.value())
            if (cfg["camera"]["width"] > 0) != (cfg["camera"]["height"] > 0):
                err = "分辨率的宽和高要么都填，要么都留 0（自动）"

        if err:
            self._log("保存失败：%s" % err, "err")
            QMessageBox.warning(self, "参数有误", err)
            return

        ok, msg = self.mgr.save(cfg)
        self._log(msg, "ok" if ok else "err")
        if not ok:
            QMessageBox.warning(self, "保存失败", msg)
            return

        self._editing = False
        self._dirty = False
        self._set_editable(False)
        self.btn_edit.setText("编辑")
        self.btn_save.setEnabled(False)
        self._refresh_states()

    # ------------------------------------------------------------ 连接动作
    def _on_connect(self, key):
        self.mgr.connect(key, then_apply=False)

    def _on_disconnect(self, key):
        self.mgr.disconnect(key)

    def _on_connect_all(self):
        self.mgr.connect_all()

    def _on_disconnect_all(self):
        self.mgr.disconnect_all()

    # ------------------------------------------------------------ 扫描相机
    def _on_scan(self):
        if getattr(self, "btn_scan", None) is not None:
            self.btn_scan.setEnabled(False)
        if getattr(self, "lb_scan", None) is not None:
            self.lb_scan.setText("扫描中…")
        cam = self.mgr.driver("camera")

        def work():
            try:
                devs = cam.enumerate()
                self.scan_done.emit(devs, "")
            except Exception as e:
                self.scan_done.emit([], str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_scan_done(self, devs, err):
        if getattr(self, "btn_scan", None) is not None:
            self.btn_scan.setEnabled(True)
        if err:
            if getattr(self, "lb_scan", None) is not None:
                self.lb_scan.setText("扫描失败")
            self._log("扫描相机失败：%s" % err, "err")
            return
        n = len(devs)
        if getattr(self, "lb_scan", None) is not None:
            self.lb_scan.setText("发现 %d 台" % n if n else "未发现相机")
        if n:
            self._log("发现 %d 台相机：" % n, "ok")
            for d in devs:
                self._log("    [%d] %s" % (d["index"], d["label"]), "info")
            # 序号超出范围时拉回 0，避免保存一个不存在的序号
            if isinstance(self.inp.get("device_index"), QSpinBox):
                self.inp["device_index"].setMaximum(max(0, n - 1))
        else:
            self._log("未发现相机：检查网线/供电；GigE 相机要与本机网卡同网段；"
                      "USB3 相机直插主板口；MVS 客户端是否占用了相机", "warn")

    # ------------------------------------------------------------ 状态刷新
    def _refresh_states(self, *_args):
        for key, card in self.cards.items():
            d = self.mgr.driver(key)
            detail = sub = ""
            if d is not None and self.mgr.is_connected(key):
                info = d.info or {}
                detail = info.get("endpoint") or info.get("model") or ""
                if key == "motion":
                    sub = "固件 %s · 库 %s" % (info.get("firmware"), info.get("lib"))
                elif key == "probe":
                    sub = "DLL 版本 %s" % info.get("dll_version")
                elif key == "camera":
                    # 相机的 endpoint 本身就是型号，第三行再写一遍是重复，留空显示"已连接"
                    sub = ""
            card.set_state(self.mgr.state(key), detail, sub)

        n = self.mgr.connected_count()
        self.lb_summary.setText("%d / %d 已连接" % (n, len(DEVICE_ORDER)))
        busy = any(self.mgr.is_busy(k) for k in DEVICE_ORDER)
        self.btn_all_conn.setEnabled(not busy and n < len(DEVICE_ORDER))
        self.btn_all_disc.setEnabled(n > 0)

        # 详情页徽章
        st = self.mgr.state(self._sel)
        self.badge.set_state(st)
        # 编辑态下设备被拔线/断开，不应该把正在编辑的表单锁掉
        if not self._editing:
            self._set_editable(False)
        # 相机连上/断开后，画面跟着开/关
        if getattr(self, "view", None) is not None:
            self._sync_preview()

    # ------------------------------------------------------------ 日志
    def _log(self, text, level="info"):
        colors = {"info": theme.TXT3, "ok": theme.OK,
                  "warn": theme.WARN, "err": theme.BAD}
        col = colors.get(level, theme.TXT3)
        # 简单转义，避免日志里的 < > 被当标签
        safe = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        self.txt_log.append('<span style="color:%s">%s</span>' % (col, safe))
        self.txt_log.verticalScrollBar().setValue(
            self.txt_log.verticalScrollBar().maximum())
        # 折叠状态下把最后一条显示在标题行
        self._log_msg = text
        if not self._log_open:
            self.lb_log_last.setText(text)
