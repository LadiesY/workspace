# -*- coding: utf-8 -*-
"""06 数据管理 → 数据导出。

**布局版**：范围、内容、格式、目录、进度、历史表都摆好，按钮不落盘。

导出与「备份」不是一回事：备份是整目录快照（给灾难恢复用，人不读），
导出是给质检/客户/Excel 看的表（要能被别人打开）。所以这里不谈"恢复"，
只谈"这份数据拿去干什么" —— 于是才有"导出内容"这一栏可勾选。
"""

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from .. import theme
from ..widgets import _q
from . import data_common as D
from . import sys_common as C


class Bar(QWidget):
    """细进度条。QProgressBar 在浅色样式下会带系统边框和居中文字，太吵，
    这里只要一根 6px 的槽 —— 进度本来就有旁边的文字在说。"""

    def __init__(self, parent=None):
        super(Bar, self).__init__(parent)
        self.setFixedHeight(6)
        self.pct = 0

    def set_pct(self, v):
        self.pct = max(0, min(100, int(v)))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0, 1, 0, -1)
        p.setPen(Qt.NoPen)
        p.setBrush(_q("#d5e4f1"))
        p.drawRoundedRect(r, 3, 3)
        if self.pct > 0:
            w = max(6.0, r.width() * self.pct / 100.0)
            p.setBrush(_q(theme.AMBER2))
            p.drawRoundedRect(QRectF(r.left(), r.top(), w, r.height()), 3, 3)
        p.end()


class ExportTab(QWidget):
    def __init__(self, parent=None):
        super(ExportTab, self).__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)

        # ---------------- 范围
        c1 = C.Card(self, pad=(12, 8, 12, 10))
        c1.body.addWidget(C.sec_title("导出范围"))
        self.cb_scope = D.combo([
            ("当前查询结果（0 条）", "query"),
            ("选中任务（未选）", "sel"),
            ("整个批次（—）", "batch"),
            ("全部历史", "all")], 230)
        c1.body.addWidget(C.field("范围", self.cb_scope))
        c1.body.addWidget(D.hline())

        c1.body.addWidget(D.label("导出内容", size=10, bold=True))
        g1 = QHBoxLayout()
        g1.setContentsMargins(0, 0, 0, 0)
        g1.setSpacing(14)
        self.c_wave = D.check("波形原始数据", True)
        self.c_judge = D.check("测试结论", True)
        self.c_pin = D.check("引脚坐标", True)
        g1.addWidget(self.c_wave)
        g1.addWidget(self.c_judge)
        g1.addWidget(self.c_pin)
        g1.addStretch(1)
        c1.body.addLayout(g1)

        g2 = QHBoxLayout()
        g2.setContentsMargins(0, 0, 0, 0)
        g2.setSpacing(14)
        self.c_img = D.check("现场图片", False)
        self.c_log = D.check("运行日志", False)
        g2.addWidget(self.c_img)
        g2.addWidget(self.c_log)
        g2.addStretch(1)
        c1.body.addLayout(g2)
        c1.body.addStretch(1)
        c1.body.addWidget(D.note("勾得越多文件越大：波形单板约 300 KB"))
        top.addWidget(c1, 1)

        # ---------------- 输出
        c2 = C.Card(self, pad=(12, 8, 12, 10))
        c2.body.addWidget(C.sec_title("输出"))
        r1 = QHBoxLayout()
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(14)
        r1.addWidget(C.field("格式", D.combo(["CSV", "Excel (.xlsx)", "JSON"], 120)), 1)
        r1.addWidget(C.field("文件名规则", D.edit(
            "{板号}_{检修}_{时间}", 190)), 1)
        c2.body.addLayout(r1)

        r2 = QHBoxLayout()
        r2.setContentsMargins(0, 0, 0, 0)
        r2.setSpacing(8)
        self.ed_dir = D.edit("data\\export", 300)
        self.btn_browse = C.btn("浏览…", width=64)
        r2.addWidget(C.field("输出目录", self.ed_dir), 1)
        r2.addWidget(self.btn_browse, 0, Qt.AlignBottom)
        c2.body.addLayout(r2)

        self.bar = Bar(self)
        c2.body.addWidget(self.bar)

        r3 = QHBoxLayout()
        r3.setContentsMargins(0, 0, 0, 0)
        r3.setSpacing(8)
        self.btn_go = C.btn("开始导出", primary=True, width=92)
        self.btn_open = C.btn("打开目录", width=80)
        self.lb_state = D.label("还没有可导出的数据", size=9)
        r3.addWidget(self.btn_go)
        r3.addWidget(self.btn_open)
        r3.addSpacing(4)
        r3.addWidget(self.lb_state)
        r3.addStretch(1)
        c2.body.addLayout(r3)
        top.addWidget(c2, 1)

        root.addLayout(top)

        # ---------------- 最近导出
        c3 = C.Card(self, pad=(10, 8, 12, 10))
        c3.body.addWidget(C.sec_title("最近导出"))
        self.table = C.make_table(
            ("文件名", "时间", "范围", "格式", "大小", "记录数", "状态"),
            (196, 116, 118, 62, 58, 60, 64))
        c3.body.addWidget(self.table, 1)
        c3.body.addWidget(D.note(
            "每次导出记一条：谁导的、导了多少条、文件多大；失败的那条留着看原因"))
        root.addWidget(c3, 1)
