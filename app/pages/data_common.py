# -*- coding: utf-8 -*-
"""数据管理页共用的小件：下拉、输入、复选、指标块、空态提示。

为什么单独一个文件
------------------
五个子页（结果查询 / 波形回放 / 报表生成 / 数据导出 / 批次统计）的工具栏
长得一样：一串窄下拉 + 窄输入框 + 一两个按钮。各写一遍迟早走样 —— 本项目
在系统管理那边就出现过"同一排按钮高差 2px"。统一从这里取。

白卡、按钮、表格、说明文字直接用 `sys_common` 那一套（`Card` / `btn` /
`make_table` / `hint`），不再复制一份：两个模块的白卡必须长得一模一样，
复制出去改一次就分家了。本文件只补数据管理特有的小件。

**本页只做布局，没有任何取数逻辑。** 表格留空、指标写「—」，等真数据接进来
再往这些壳里填 —— 到时候只改各 Tab 的 `reload()`，布局一行不用动。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout,
                             QLabel, QLineEdit, QSizePolicy, QVBoxLayout,
                             QWidget)

from .. import theme
from ..widgets import _font
from . import sys_common as C

# 空态统一说法：所有"还没有数据"的地方用同一句，别一处一个说法
EMPTY_TEXT = "还没有数据 —— 采集与测试结果接进来之后，这里按时间倒序列出"


# ---------------------------------------------------------------- 小控件
def combo(items, width=110, current=0):
    """窄下拉。items 可以是 str 列表或 (显示, 数据) 列表。"""
    cb = QComboBox()
    cb.setFixedHeight(26)
    cb.setFixedWidth(width)
    cb.setFont(_font(theme.F_BODY, 10, spacing=0.2))
    for it in items:
        if isinstance(it, (tuple, list)):
            cb.addItem(str(it[0]), it[1])
        else:
            cb.addItem(str(it), it)
    if 0 <= current < cb.count():
        cb.setCurrentIndex(current)
    return cb


def edit(placeholder="", width=150):
    e = QLineEdit()
    e.setFixedHeight(26)
    e.setFixedWidth(width)
    e.setFont(_font(theme.F_BODY, 10, spacing=0.2))
    e.setPlaceholderText(placeholder)
    return e


def check(text, on=True):
    cb = QCheckBox(text)
    cb.setFont(_font(theme.F_BODY, 10, spacing=0.2))
    cb.setChecked(on)
    return cb


def chip_btn(text, width=26, h=26):
    """工具行里的小方按钮（播放、缩放、翻页）。样式见 theme 的 QPushButton#chip。"""
    from PyQt5.QtWidgets import QPushButton
    b = QPushButton(text)
    b.setObjectName("chip")
    b.setFixedSize(width, h)
    b.setFont(_font(theme.F_BODY, 10, spacing=0.2))
    b.setCursor(Qt.PointingHandCursor)
    return b


def label(text, obj="hintTxt", size=9, bold=False):
    """小字标签。obj 用 sys_common.PAGE_QSS 里的那几种（hintTxt/monoTxt/…）。"""
    lb = QLabel(text)
    lb.setObjectName(obj)
    lb.setFont(_font(theme.F_BODY, size, bold=bold, spacing=0.2))
    return lb


def note(text, size=8):
    """卡片内的**单行**说明。

    必须关掉自动换行：换行的 QLabel，sizeHint 是按"理想宽度"估的，卡片窄一点
    就要多一行的高度，而外层布局还按老高度摆位置，最后一行就被压在卡片边框上
    （系统设置页踩过同款，症状是"文字被描边切一刀"）。关掉换行后 sizeHint 才是
    真实宽度，宁可整句短一点，也不让它折行。
    """
    lb = C.hint(text)
    lb.setWordWrap(False)
    lb.setFont(_font(theme.F_BODY, size, spacing=0.2))
    return lb


def vline():
    """工具栏里的一根竖分隔线，比加 margin 干净。"""
    w = QWidget()
    w.setFixedWidth(1)
    w.setFixedHeight(18)
    w.setStyleSheet("background:#e0ebf5;")
    return w


def hline():
    """卡片内的一条细分隔线（样式来自 sys_common.PAGE_QSS 的 QFrame#hline）。"""
    f = QFrame()
    f.setObjectName("hline")
    f.setFixedHeight(1)
    return f


# ---------------------------------------------------------------- 指标块
class Kpi(QWidget):
    """一格指标：小标题在上、数值在中、副注在下。

    数值用等宽字体 —— 这一排四格里数字宽度不一致会左右跳，看着像没对齐。
    """

    def __init__(self, title, value="—", note="", color=None, parent=None):
        super(Kpi, self).__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)

        self.lb_title = label(title)
        v.addWidget(self.lb_title)

        self.lb_value = QLabel(value)
        self.lb_value.setFont(_font(theme.F_MONO, 20, bold=True, spacing=0.6))
        self.lb_value.setStyleSheet("color:%s;" % (color or theme.TITLE))
        v.addWidget(self.lb_value)

        self.lb_note = label(note)
        if not note:
            self.lb_note.hide()
        v.addWidget(self.lb_note)

    def set_value(self, value, color=None):
        self.lb_value.setText(str(value))
        if color:
            self.lb_value.setStyleSheet("color:%s;" % color)


def kpi_row(*items):
    """一排指标，等宽分。items 是 Kpi 对象或 None（None 当前不占位）。"""
    lay = QHBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    for i, w in enumerate(items):
        if w is None:
            continue
        lay.addWidget(w, 1)
        if i < len(items) - 1:
            # 分隔线用 QLabel 而不是裸 QWidget：QWidget 的默认 paintEvent 不画
            # 样式表里的 background，画出来只有一截断线；QLabel 会老实填满。
            sep = QLabel()
            sep.setFixedWidth(1)
            sep.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
            sep.setStyleSheet("background:#e8f0f8;")
            lay.addWidget(sep)
    return lay


# ---------------------------------------------------------------- 空态
def empty_hint(text=EMPTY_TEXT):
    """表格/画布下方那句统一的空态说明。"""
    lb = C.hint(text)
    lb.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    return lb


def sample_tag(text="示意"):
    """卡片标题行右侧的小角标：这页里的图形不是真数据。

    画在图里会压住曲线，放在标题行既显眼又不挡东西。
    """
    lb = QLabel(text)
    lb.setFont(_font(theme.F_BODY, 8, spacing=0.3))
    lb.setFixedHeight(16)
    lb.setStyleSheet("color:#8a6a2f; background:#fdfaf2;"
                     "border:1px dashed #e0d3b8; border-radius:2px;"
                     "padding:0 5px;")
    return lb
