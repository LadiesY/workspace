# -*- coding: utf-8 -*-
"""06 数据管理 → 结果查询。

**这一版只做布局**：筛选条、结果表、右侧详情栏都摆好，表格留空。

为什么结果要单独一页而不是直接进波形
------------------------------------
查的时候想的是"哪块板、什么时候、过没过"，看的时候才想"波形长什么样"。
一页塞两件事，表就只剩三列。所以查询页只给结论，选中一行之后去「波形回放」
看细节（接数据时把那行的任务号带过去，本版按钮先不联动）。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from .. import theme
from ..widgets import _font
from . import data_common as D
from . import sys_common as C

HEADERS = ("任务号", "板号", "检修", "状态", "引脚数", "不良", "良率",
           "耗时", "操作人", "完成时间")
WIDTHS = (110, 84, 50, 56, 54, 54, 56, 60, 70, 124)


def _kv(k, v="—"):
    """详情栏的一行：左标签（灰）+ 右值（深色、等宽）。"""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)
    lk = D.label(k)
    lk.setFixedWidth(58)
    h.addWidget(lk)
    lv = D.label(v, obj="", size=10)
    lv.setFont(_font(theme.F_BODY, 10, spacing=0.2))
    lv.setStyleSheet("color:%s;" % theme.TXT)
    h.addWidget(lv, 1)
    return w


class QueryTab(QWidget):
    def __init__(self, parent=None):
        super(QueryTab, self).__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # ---------------- 筛选条
        bar = C.Card(self, pad=(12, 8, 12, 8))
        self.cb_span = D.combo(["近 7 天", "今天", "近 30 天", "全部", "自定义…"], 88)
        self.ed_board = D.edit("如 N、A12", 88)
        self.cb_side = D.combo(["全部", "修前", "修后"], 76)
        self.cb_state = D.combo(["全部", "合格", "不合格", "中断"], 80)
        self.ed_word = D.edit("任务号 / 操作人", 130)

        self.btn_query = C.btn("查询", primary=True, width=58)
        self.btn_reset = C.btn("重置", width=50)
        self.lb_count = D.label("共 0 条")

        # 1100 宽下这一排只剩 ~818px，标签用小字、控件按"刚好够用"给宽度；
        # "共 N 条" 挪到表格标题行 —— 夹在这里会先把按钮挤到重叠。
        bar.body.addLayout(C.row(
            D.label("时间", size=9), self.cb_span,
            D.label("板号", size=9), self.ed_board,
            D.label("检修", size=9), self.cb_side,
            D.label("状态", size=9), self.cb_state,
            D.vline(),
            self.ed_word,
            self.btn_query, self.btn_reset,
            None))
        root.addWidget(bar)

        # ---------------- 主体：左表 + 右详情
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)

        left = C.Card(self, pad=(10, 8, 12, 10))
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        head.addWidget(C.sec_title("测试结果"))
        head.addSpacing(10)
        head.addWidget(self.lb_count)
        head.addStretch(1)
        self.btn_open = C.btn("看波形", width=68)
        self.btn_export = C.btn("导出选中", width=76)
        head.addWidget(self.btn_open)
        head.addWidget(self.btn_export)
        left.body.addLayout(head)

        self.table = C.make_table(HEADERS, WIDTHS)
        left.body.addWidget(self.table, 1)
        left.body.addWidget(D.note(
            "一行一个测试任务，按完成时间倒序；双击行进入波形回放。"
            "良率 = 合格引脚 / 总引脚。"))
        body.addWidget(left, 1)

        # ---------------- 右侧详情
        side = C.Card(self, pad=(12, 10, 12, 12))
        side.setFixedWidth(292)
        side.body.addWidget(C.sec_title("任务详情"))
        side.body.addWidget(D.hline())

        self.lb_tip = D.label("选中左侧一行，这里显示这块板的测试概况")
        side.body.addWidget(self.lb_tip)

        for k in ("任务号", "板号", "检修状态", "开始时间", "结束时间",
                  "操作人", "程序版本"):
            side.body.addWidget(_kv(k))

        side.body.addWidget(D.hline())
        side.body.addWidget(D.label("关键指标", size=10, bold=True))

        grid = QHBoxLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        self.k_pin = D.Kpi("引脚数", "—")
        self.k_bad = D.Kpi("不良", "—", color=theme.TXT3)
        grid.addWidget(self.k_pin, 1)
        grid.addWidget(self.k_bad, 1)
        side.body.addLayout(grid)

        grid2 = QHBoxLayout()
        grid2.setContentsMargins(0, 0, 0, 0)
        grid2.setSpacing(0)
        self.k_yield = D.Kpi("良率", "—")
        self.k_cost = D.Kpi("耗时", "—")
        grid2.addWidget(self.k_yield, 1)
        grid2.addWidget(self.k_cost, 1)
        side.body.addLayout(grid2)

        side.body.addStretch(1)
        side.body.addWidget(D.note("中断的任务也留记录，状态标「中断」"))

        body.addWidget(side)
        root.addLayout(body, 1)
