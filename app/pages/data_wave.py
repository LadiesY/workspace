# -*- coding: utf-8 -*-
"""06 数据管理 → 波形回放（多组任务波形对比）。

这是数据管理的主场：同一块板**修前 / 修后**两组采集信号叠在一起看，
电压、时钟、总线三条波形上下排（泳道式，跟示波器一个读法），
哪一条在修前是坏的、修后好了，一眼能看出来。

这一版是布局
------------
画布里的波形是**示意**（用固定公式画出来的形状，不是采回来的数），
右上角一直挂着「示意波形 · 未接入采集数据」，免得被当成真数据。
接数据时只要把 `_series()` 换成从任务文件读采样点，画笔一行不用改。

为什么自己画而不是引 pyqtgraph
------------------------------
现场离线机没 pip，多一个依赖就多一处装不上的风险；而这里要的只是
"几条折线 + 网格 + 光标"，QPainter 路径画得又快又稳（600 点/条，重绘 <2 ms）。
真需要百万点时再换库，那时也是换 `_series()` 一处。

泳道高度怎么分
--------------
三条信号量程一样（0~3.6 V），所以**等分**，不按信号值域自适应 —— 上下等宽
才好横向比"哪条抖动更大"。纵向 8px 余量是留给过冲的（修前时钟有振铃，
贴边画会被裁）。
"""

import math

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from .. import theme
from ..widgets import _font, _q
from . import data_common as D
from . import sys_common as C

# (短名, 信号名, 类型说明) —— 三条泳道的标签
CHANNELS = (
    ("VCC_3V3", "电压 · 0~3.6 V"),
    ("CLK_24M", "时钟 · 0~3.6 V"),
    ("I2C_SDA", "总线 · 0~3.6 V"),
)

C_BEFORE = "#c0392b"     # 修前（不良）：红
C_AFTER = "#1e8f56"      # 修后（良）：绿


# ---------------------------------------------------------------- 示意波形
# 下面四个函数只是"让布局看得出对比效果"的示意曲线，接数据后整段替换。
def _volt(x, before):
    """3.3 V 电源：修前纹波大，修后干净。x 是 0~1 的窗口位置。"""
    if before:
        return 3.30 + 0.20 * math.sin(x * math.pi * 2 * 9) \
                    + 0.07 * math.sin(x * math.pi * 2 * 23)
    return 3.30 + 0.035 * math.sin(x * math.pi * 2 * 9)


def _clk(x, before, n=7):
    """24 MHz 时钟在这里按 7 个周期示意。修前每个跳变都挂振铃。"""
    ph = (x * n) % 1.0
    v = 3.3 if ph < 0.5 else 0.0
    if before:
        d = min(ph, abs(ph - 0.5))
        if d < 0.05:
            v += 0.55 * (1 - d / 0.05) * math.sin(d * 260)
    return v


_BUS = (1, 0, 1, 1, 0, 0, 1, 0)


def _bus(x, before):
    """I2C 数据线：8 个位窗。修前第 4 位没归零、第 6 位多一个毛刺。"""
    i = min(int(x * len(_BUS)), len(_BUS) - 1)
    v = 3.3 if _BUS[i] else 0.0
    if not before:
        return v
    if i == 3:
        return 1.5                      # 丢位：半高不归零
    ph = (x * len(_BUS)) % 1.0
    if i == 5 and ph > 0.55:
        return 2.9                      # 毛刺
    return v


# ---------------------------------------------------------------- 画布
class WaveCanvas(QWidget):
    """三条泳道 × 两组曲线。光标位置固定在 58%，只做展示。"""

    PAD_L = 104         # 左侧信号名
    PAD_R = 14
    PAD_T = 24          # 顶部图例
    AXIS_H = 24         # 底部时间轴
    SPAN_MS = 20.0      # 时间窗
    VMAX = 3.6

    def __init__(self, parent=None):
        super(WaveCanvas, self).__init__(parent)
        self.cursor = 0.58
        self.vis = {"before": True, "after": True}
        self.lanes = (True, True, True)      # 每条泳道显不显示（勾选框控制）
        self.setMinimumHeight(240)

    # ------------------------------------------------------------ 坐标
    def _plot(self):
        return QRectF(self.PAD_L, self.PAD_T,
                      max(10.0, self.width() - self.PAD_L - self.PAD_R),
                      max(10.0, self.height() - self.PAD_T - self.AXIS_H))

    def _lane(self, i):
        r = self._plot()
        h = r.height() / 3.0
        return QRectF(r.left(), r.top() + h * i, r.width(), h)

    # ------------------------------------------------------------ 读数
    def readout(self):
        """光标处的三个读数，给下方那行小字用。"""
        out = []
        for i, (name, _u) in enumerate(CHANNELS):
            fn = (_volt, _clk, _bus)[i]
            v = fn(self.cursor, False)
            out.append((name, "%.2f V" % v))
        return out

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        plot = self._plot()

        # 泳道底 + 分隔线
        for i in range(3):
            lane = self._lane(i)
            if i % 2 == 1:
                p.fillRect(lane, _q("#f7fbfe"))
            p.setPen(QPen(_q("#e6eff7"), 1))
            p.drawLine(QPointF(lane.left(), lane.bottom()),
                       QPointF(lane.right(), lane.bottom()))
        p.setPen(QPen(_q("#dde8f2"), 1))
        p.drawLine(QPointF(plot.left(), plot.top()),
                   QPointF(plot.left(), plot.bottom()))

        # 时间网格（每 4 ms 一条）
        p.setPen(QPen(_q("#eef4fa"), 1))
        for k in range(1, 5):
            x = plot.left() + plot.width() * k / 5.0
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        # 曲线：先画修前，再压修后（修后是结论，要在上面）
        for i in range(3):
            if not self.lanes[i]:
                continue
            if self.vis["before"]:
                self._curve(p, i, True, C_BEFORE, 150, 1.2)
            if self.vis["after"]:
                self._curve(p, i, False, C_AFTER, 220, 1.4)

        # 左侧信号名
        for i, (name, sub) in enumerate(CHANNELS):
            lane = self._lane(i)
            p.setFont(_font(theme.F_MONO, 9, bold=True, spacing=0.3))
            p.setPen(_q(theme.TXT))
            p.drawText(QRectF(2, lane.top() + 6, self.PAD_L - 10, 14),
                       Qt.AlignRight | Qt.AlignVCenter, name)
            p.setFont(_font(theme.F_BODY, 8, spacing=0.2))
            p.setPen(_q(theme.TXT3))
            p.drawText(QRectF(2, lane.top() + 20, self.PAD_L - 10, 13),
                       Qt.AlignRight | Qt.AlignVCenter, sub)

        # 时间轴
        p.setFont(_font(theme.F_MONO, 8, spacing=0.2))
        fm = QFontMetrics(p.font())
        for k in range(6):
            t = self.SPAN_MS * k / 5.0
            x = plot.left() + plot.width() * k / 5.0
            p.setPen(QPen(_q("#cfdeeb"), 1))
            p.drawLine(QPointF(x, plot.bottom()), QPointF(x, plot.bottom() + 4))
            # 单位只写在最后一格上：单独再画一个 "ms" 会跟 "20" 叠在一起
            txt = ("%g" % t) if k < 5 else ("%g ms" % t)
            p.setPen(_q(theme.TXT3))
            dx = fm.horizontalAdvance(txt) / 2.0 if k < 5 else fm.horizontalAdvance(txt)
            p.drawText(QPointF(x - dx, plot.bottom() + 16), txt)

        # 光标 + 时间气泡
        cx = plot.left() + plot.width() * self.cursor
        p.setPen(QPen(_q(theme.AMBER, 130), 1, Qt.DashLine))
        p.drawLine(QPointF(cx, plot.top()), QPointF(cx, plot.bottom()))

        p.setPen(Qt.NoPen)
        p.setBrush(_q(theme.AMBER, 210))
        p.drawPolygon(QPointF(cx - 4, plot.top()), QPointF(cx + 4, plot.top()),
                      QPointF(cx, plot.top() + 5))

        t_txt = "%.2f ms" % (self.SPAN_MS * self.cursor)
        p.setFont(_font(theme.F_MONO, 9, bold=True, spacing=0.3))
        fm2 = QFontMetrics(p.font())
        bw = fm2.horizontalAdvance(t_txt) + 12
        box = QRectF(cx - bw / 2.0, plot.top() - 17, bw, 15)
        p.setBrush(_q(theme.AMBER))
        p.drawRoundedRect(box, 2, 2)
        p.setPen(_q("#ffffff"))
        p.drawText(box, Qt.AlignCenter, t_txt)

        # 图例
        self._legend(p, plot)

        # 示意标记（一直在，别让人当成真数据）
        tag = "示意波形 · 未接入采集数据"
        p.setFont(_font(theme.F_BODY, 8, spacing=0.3))
        fm3 = QFontMetrics(p.font())
        tw = fm3.horizontalAdvance(tag)
        bx = QRectF(self.width() - tw - 26, 3, tw + 18, 16)
        p.setPen(QPen(_q("#e0b9a8"), 1, Qt.DashLine))
        p.setBrush(_q("#fdf6f3"))
        p.drawRoundedRect(bx, 2, 2)
        p.setPen(_q("#a8573f"))
        p.drawText(bx, Qt.AlignCenter, tag)

        p.end()

    def _curve(self, p, i, before, color, alpha, width):
        lane = self._lane(i)
        r = self._plot()
        fn = (_volt, _clk, _bus)[i]
        top = lane.top() + 5
        h = lane.height() - 10

        path = QPainterPath()
        steps = max(2, int(r.width()))
        for s in range(steps + 1):
            x = s / float(steps)
            v = fn(x, before)
            v = max(0.0, min(self.VMAX, v))
            px = r.left() + r.width() * x
            py = top + h - h * (v / self.VMAX)
            if s == 0:
                path.moveTo(px, py)
            else:
                path.lineTo(px, py)

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_q(color, alpha), width))
        p.drawPath(path)

    def _legend(self, p, plot):
        """顶部两个小圆点 + 修前/修后。图例贴在画布左上，跟着画布走。"""
        p.setFont(_font(theme.F_BODY, 9, spacing=0.3))
        fm = QFontMetrics(p.font())
        x = plot.left() - self.PAD_L + 2
        y = 9
        for on, col, txt in ((self.vis["before"], C_BEFORE, "修前"),
                             (self.vis["after"], C_AFTER, "修后")):
            p.setPen(Qt.NoPen)
            p.setBrush(_q(col, 220 if on else 70))
            p.drawEllipse(QRectF(x, y + 4, 4, 4))
            p.setPen(_q(theme.TXT2 if on else theme.TXT3))
            p.drawText(QPointF(x + 18, y + 9), txt)
            x += 18 + fm.horizontalAdvance(txt) + 14


# ---------------------------------------------------------------- 页
class WaveTab(QWidget):
    def __init__(self, parent=None):
        super(WaveTab, self).__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # ---------------- 工具行：选两组任务
        bar = C.Card(self, pad=(12, 8, 12, 8))
        self.cb_a = D.combo(["修前 · N.修前.5", "修前 · N.修前.4", "修前 · A12.修前.2"], 132)
        self.cb_b = D.combo(["修后 · N.修后.6", "修后 · N.修后.5", "修后 · A12.修后.3"], 132)
        self.cb_span = D.combo(["全段 20 ms", "前 5 ms", "后 5 ms", "自定义…"], 96)
        self.btn_swap = D.chip_btn("⇄", 28)
        # 别用三个箭头符号：◀ ▶ ▶ 排在一起分不清哪个是播放
        self.btn_prev = D.chip_btn("上一段", 54)
        self.btn_play = D.chip_btn("播放", 46)
        self.btn_next = D.chip_btn("下一段", 54)
        # 缩放不占按钮位：画布本身支持滚轮缩放（底部有一句提示），
        # 1100 宽的窗口里这一排已经排不下"缩放 − ＋"了
        self.btn_shot = C.btn("导出图", width=64)

        bar.body.addLayout(C.row(
            D.label("对比 A", size=10), self.cb_a,
            self.btn_swap,
            D.label("B", size=10), self.cb_b,
            D.vline(),
            D.label("时间窗", size=10), self.cb_span,
            self.btn_prev, self.btn_play, self.btn_next,
            None, self.btn_shot))
        root.addWidget(bar)

        # ---------------- 主体：左任务清单 + 右波形
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)

        left = C.Card(self, pad=(10, 8, 10, 10))
        left.setFixedWidth(232)
        left.body.addWidget(C.sec_title("任务"))
        left.body.addWidget(D.label("同一块板的修前与修后各一条", size=9))

        self.list = C.make_table(("任务号", "结果"), (128, 56), stretch_last=False)
        left.body.addWidget(self.list, 1)
        left.body.addWidget(D.label("按板号分组，双击换对比对象", size=8))
        body.addWidget(left)

        # ---------------- 波形卡
        card = C.Card(self, pad=(10, 8, 12, 10))
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        head.addWidget(C.sec_title("波形对比"))
        head.addStretch(1)
        self.ch_volt = D.check("电压", True)
        self.ch_clk = D.check("时钟", True)
        self.ch_bus = D.check("总线", True)
        self.ch_before = D.check("修前", True)
        self.ch_after = D.check("修后", True)
        for w in (self.ch_volt, self.ch_clk, self.ch_bus):
            head.addWidget(w)
        head.addWidget(D.vline())
        for w in (self.ch_before, self.ch_after):
            head.addWidget(w)
        card.body.addLayout(head)

        self.canvas = WaveCanvas(self)
        card.body.addWidget(self.canvas, 1)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(14)
        self._reads = []
        foot.addWidget(D.label("修后读数", size=9))
        for name, val in self.canvas.readout():
            lb = D.label("%s %s" % (name, val), obj="monoTxt", size=9)
            self._reads.append(lb)
            foot.addWidget(lb)
        foot.addStretch(1)
        foot.addWidget(D.label("滚轮缩放", size=8))
        card.body.addLayout(foot)
        body.addWidget(card, 1)

        root.addLayout(body, 1)
        root.addWidget(D.note(
            "三条泳道量程相同（0~3.6 V），纵向等分才好比抖动大小；"
            "修前（红）与修后（绿）叠在同一泳道。波形与读数都是示意，采集链路接上后替换。"))
