# -*- coding: utf-8 -*-
"""06 数据管理 → 批次统计。

**布局版**：四格指标 + 良率趋势（折线）+ 缺陷分布（横条）+ 批次汇总表。
两张图都是自绘，**曲线是示意**，卡片标题右上角标着「示意」；指标格全部写「—」，
等真数据进来再由 `reload()` 填。

为什么统计要单独的图，而不是只看表
----------------------------------
表能回答"哪一批多少"，回答不了"是不是在变差"。良率掉 0.5% 在表格里是两串
数字，在折线上是一条往下走的线 —— 现场要的是后者。缺陷分布用横条而不是饼：
饼要数角度，横条比长度，六类差别小的时候横条还能看出排序。
"""

import math

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from .. import theme
from ..widgets import _font, _q
from . import data_common as D
from . import sys_common as C

DEFECTS = ("虚焊", "桥连", "开路", "偏移", "污染", "其它")
TREND = (97.2, 96.4, 98.1, 97.0, 98.6, 98.9)      # 示意：6 个批次的良率


# ---------------------------------------------------------------- 良率趋势
class Trend(QWidget):
    PAD_L = 36
    PAD_R = 12
    PAD_T = 14
    PAD_B = 20
    Y0, Y1 = 94.0, 100.0        # 纵轴范围（良率 %）

    def __init__(self, parent=None):
        super(Trend, self).__init__(parent)
        self.setMinimumHeight(150)

    def _plot(self):
        return QRectF(self.PAD_L, self.PAD_T,
                      max(10.0, self.width() - self.PAD_L - self.PAD_R),
                      max(10.0, self.height() - self.PAD_T - self.PAD_B))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        r = self._plot()

        # 横网格 + 纵轴刻度
        p.setFont(_font(theme.F_MONO, 8, spacing=0.2))
        fm = QFontMetrics(p.font())
        for k in range(4):
            v = self.Y0 + (self.Y1 - self.Y0) * k / 3.0
            y = r.bottom() - r.height() * k / 3.0
            p.setPen(QPen(_q("#eef4fa" if k else "#dde8f2"), 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            txt = "%.0f" % v
            p.setPen(_q(theme.TXT3))
            p.drawText(QPointF(r.left() - 6 - fm.horizontalAdvance(txt),
                               y + 3), txt)

        # 折线 + 点
        n = len(TREND)
        pts = []
        for i, v in enumerate(TREND):
            x = r.left() + r.width() * (i + 0.5) / n
            y = r.bottom() - r.height() * (v - self.Y0) / (self.Y1 - self.Y0)
            pts.append(QPointF(x, y))

        path = QPainterPath(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)

        # 线下的淡填充（看着有"面积"，比单根线更像趋势）
        fill = QPainterPath(path)
        fill.lineTo(pts[-1].x(), r.bottom())
        fill.lineTo(pts[0].x(), r.bottom())
        fill.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(_q(theme.AMBER, 16))
        p.drawPath(fill)

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_q(theme.AMBER2), 1.6))
        p.drawPath(path)

        p.setPen(Qt.NoPen)
        for pt in pts:
            p.setBrush(_q("#ffffff"))
            p.drawEllipse(pt, 3.4, 3.4)
            p.setBrush(_q(theme.AMBER2))
            p.drawEllipse(pt, 2.0, 2.0)

        # 横轴：批次序号
        p.setFont(_font(theme.F_BODY, 8, spacing=0.2))
        for i in range(n):
            txt = "B%d" % (i + 1)
            fm2 = QFontMetrics(p.font())
            x = r.left() + r.width() * (i + 0.5) / n
            p.setPen(_q(theme.TXT3))
            p.drawText(QPointF(x - fm2.horizontalAdvance(txt) / 2.0,
                               r.bottom() + 15), txt)

        p.end()


# ---------------------------------------------------------------- 缺陷分布
class Bars(QWidget):
    ROW_H = 24
    PAD_T = 8
    LAB_W = 46
    VAL_W = 34

    def __init__(self, parent=None):
        super(Bars, self).__init__(parent)
        self.setMinimumHeight(150)
        # 示意值（从多到少排好，接数据后按实际计数排序）
        self.values = (9, 6, 5, 4, 2, 1)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        top = self.PAD_T
        bar_x = self.LAB_W + 6
        bar_w = max(20.0, self.width() - bar_x - self.VAL_W - 6)
        vmax = float(max(self.values) or 1)

        p.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        for i, (name, v) in enumerate(zip(DEFECTS, self.values)):
            y = top + i * self.ROW_H
            p.setPen(_q(theme.TXT2))
            p.drawText(QRectF(0, y, self.LAB_W, self.ROW_H - 6),
                       Qt.AlignRight | Qt.AlignVCenter, name)

            track = QRectF(bar_x, y + 4, bar_w, self.ROW_H - 14)
            p.setPen(Qt.NoPen)
            p.setBrush(_q("#eef4fa"))
            p.drawRoundedRect(track, 2, 2)

            w = bar_w * (v / vmax)
            # 头两项用红（主要缺陷），其余用蓝，排序一眼能看出主次
            col = _q("#c0392b", 210) if i < 2 else _q(theme.AMBER_DIM, 190)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(track.left(), track.top(),
                                     max(3.0, w), track.height()), 2, 2)

            p.setFont(_font(theme.F_MONO, 8, spacing=0.2))
            p.setPen(_q(theme.TXT3))
            p.drawText(QRectF(bar_x + bar_w + 4, y, self.VAL_W,
                              self.ROW_H - 6),
                       Qt.AlignLeft | Qt.AlignVCenter, str(v))
            p.setFont(_font(theme.F_BODY, 9, spacing=0.2))

        p.end()


# ---------------------------------------------------------------- 页
class BatchTab(QWidget):
    def __init__(self, parent=None):
        super(BatchTab, self).__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # ---------------- 指标条
        kc = C.Card(self, pad=(14, 8, 14, 10))
        self.k_batch = D.Kpi("批次数", "—", "最近 30 天")
        self.k_boards = D.Kpi("测试板数", "—", "合格 + 不合格")
        self.k_yield = D.Kpi("平均良率", "—", "按引脚加权")
        self.k_bad = D.Kpi("不良引脚", "—", "去重后的点位")
        self.k_cost = D.Kpi("平均单板耗时", "—", "含视觉与运动")
        kc.body.addLayout(D.kpi_row(self.k_batch, self.k_boards, self.k_yield,
                                    self.k_bad, self.k_cost))
        root.addWidget(kc)

        # ---------------- 两张图
        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(8)

        c1 = C.Card(self, pad=(12, 8, 12, 10))
        h1 = QHBoxLayout()
        h1.setContentsMargins(0, 0, 0, 0)
        h1.setSpacing(8)
        h1.addWidget(C.sec_title("良率趋势"))
        h1.addStretch(1)
        h1.addWidget(D.sample_tag())
        c1.body.addLayout(h1)
        c1.body.addWidget(self._sub("按批次，纵轴 94%~100%"))
        self.trend = Trend(self)
        c1.body.addWidget(self.trend, 1)
        mid.addWidget(c1, 1)

        c2 = C.Card(self, pad=(12, 8, 12, 10))
        h2 = QHBoxLayout()
        h2.setContentsMargins(0, 0, 0, 0)
        h2.setSpacing(8)
        h2.addWidget(C.sec_title("缺陷类型分布"))
        h2.addStretch(1)
        h2.addWidget(D.sample_tag())
        c2.body.addLayout(h2)
        c2.body.addWidget(self._sub("按不良引脚归类计数，前两类标红"))
        self.bars = Bars(self)
        c2.body.addWidget(self.bars, 1)
        mid.addWidget(c2, 1)
        root.addLayout(mid, 3)

        # ---------------- 汇总表
        c3 = C.Card(self, pad=(10, 8, 12, 10))
        c3.body.addWidget(C.sec_title("批次汇总"))
        self.table = C.make_table(
            ("批次", "时间", "板数", "合格", "不良", "良率", "平均耗时", "操作人"),
            (150, 140, 62, 62, 62, 70, 84, 90))
        c3.body.addWidget(self.table, 1)
        root.addWidget(c3, 2)

    def _sub(self, text):
        lb = D.label(text, size=8)
        lb.setWordWrap(False)
        return lb
