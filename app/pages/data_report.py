# -*- coding: utf-8 -*-
"""06 数据管理 → 报表生成。

**布局版**：模板列表、生成参数、预览区三块摆好，[生成报表] 不落盘。

为什么模板要放在左边单独一列
----------------------------
"生成什么"比"填什么参数"更先决定 —— 选了"修前修后对比"，参数区才需要
A/B 两组任务；选了"生产日报"，就只要日期。模板和参数是因果顺序，所以左列
列模板、右边跟着变（接数据时按 `_sync_fields()` 显隐，本版全部平铺）。
"""

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from .. import theme
from ..widgets import _font, _q
from . import data_common as D
from . import sys_common as C

TEMPLATES = (
    ("单板报告", "一块板的判定明细、波形缩略图、不良引脚清单"),
    ("批次汇总", "整批良率、缺陷分布、异常板清单"),
    ("修前修后对比", "同一块板两次测试的指标与波形对照"),
    ("生产日报 / 周报", "按班次汇总：数量、良率、节拍、责任工序"),
)


class PreviewBox(QWidget):
    """预览占位框：自绘虚线 + 两行居中说明。

    为什么不用 QSS 的 border
    ------------------------
    给 QWidget 设无选择器的 background/border，样式会**向下传播给子控件**，
    孩子也各画一圈；而且 QWidget 的默认 paintEvent 不画边框，实际画出来多大
    全看它高兴（这里就出过一次：框只包住两行字，没撑满卡片）。自己画 rect，
    尺寸就是控件尺寸，一眼能对上。
    """

    def __init__(self, title, sub, parent=None):
        super(PreviewBox, self).__init__(parent)
        self.title = title
        self.sub = sub
        self.setMinimumHeight(120)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(_q("#c3d7e9"), 1, Qt.DashLine))
        p.setBrush(_q("#fbfdff"))
        p.drawRoundedRect(r, 4, 4)

        cy = r.center().y()
        p.setFont(_font(theme.F_BODY, 10, spacing=0.3))
        fm = QFontMetrics(p.font())
        p.setPen(_q("#7e95a9"))
        p.drawText(QRectF(r.left(), cy - fm.height() - 2, r.width(), fm.height()),
                   Qt.AlignCenter, self.title)

        p.setFont(_font(theme.F_BODY, 9, spacing=0.3))
        fm2 = QFontMetrics(p.font())
        p.setPen(_q(theme.TXT3))
        p.drawText(QRectF(r.left(), cy + 4, r.width(), fm2.height()),
                   Qt.AlignCenter, self.sub)
        p.end()


class ReportTab(QWidget):
    def __init__(self, parent=None):
        super(ReportTab, self).__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)

        # ---------------- 左：模板
        left = C.Card(self, pad=(10, 8, 10, 10))
        left.setFixedWidth(268)
        left.body.addWidget(C.sec_title("报表模板"))
        left.body.addWidget(D.label("选一个模板，右边跟着换参数", size=9))

        self.list = C.make_table(("模板",), (236,), stretch_last=False)
        self.list.verticalHeader().setDefaultSectionSize(32)
        for name, _d in TEMPLATES:
            r = self.list.rowCount()
            self.list.insertRow(r)
            C.fill_row(self.list, r, (name,),
                       colors={0: theme.TXT}, bolds={0: True},
                       monos={0: False})
            self.list.item(r, 0).setToolTip(_d)
        self.list.selectRow(0)
        left.body.addWidget(self.list, 1)
        left.body.addWidget(D.note("占位符由后台填，不在这里排版"))
        body.addWidget(left)

        # ---------------- 右：参数 + 预览
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)

        pc = C.Card(self, pad=(12, 8, 12, 10))
        self.pc = pc
        pc.body.addWidget(C.sec_title("生成参数"))

        r1 = QHBoxLayout()
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(14)
        self.cb_span = D.combo(["近 7 天", "今天", "近 30 天", "自定义…"], 130)
        self.cb_batch = D.combo(["全部批次", "B-2026-1111-A", "B-2026-1110-B"], 170)
        r1.addWidget(C.field("时间范围", self.cb_span), 1)
        r1.addWidget(C.field("批次 / 板号", self.cb_batch), 1)
        r1.addWidget(C.field("输出格式", D.combo(["PDF", "Word", "HTML"], 100)), 1)
        pc.body.addLayout(r1)

        inc = QHBoxLayout()
        inc.setContentsMargins(0, 0, 0, 0)
        inc.setSpacing(16)
        inc.addWidget(D.label("包含", size=10))
        self.c_shot = D.check("波形截图", True)
        self.c_detail = D.check("判定明细", True)
        self.c_chart = D.check("统计图表", True)
        self.c_sign = D.check("签名 / 审核栏", False)
        for w in (self.c_shot, self.c_detail, self.c_chart, self.c_sign):
            inc.addWidget(w)
        inc.addStretch(1)
        pc.body.addLayout(inc)

        out = QHBoxLayout()
        out.setContentsMargins(0, 0, 0, 0)
        out.setSpacing(8)
        self.ed_dir = D.edit("data\\report", 330)
        self.btn_browse = C.btn("浏览…", width=64)
        out.addWidget(C.field("输出目录", self.ed_dir), 1)
        out.addWidget(self.btn_browse, 0, Qt.AlignBottom)
        pc.body.addLayout(out)

        act = QHBoxLayout()
        act.setContentsMargins(0, 0, 0, 0)
        act.setSpacing(8)
        self.btn_make = C.btn("生成报表", primary=True, width=90)
        self.btn_preview = C.btn("预览", width=64)
        self.lb_state = D.label("还没有生成", size=9)
        act.addWidget(self.btn_make)
        act.addWidget(self.btn_preview)
        act.addSpacing(6)
        act.addWidget(self.lb_state)
        act.addStretch(1)
        pc.body.addLayout(act)
        right.addWidget(pc)

        # 预览区
        pv = C.Card(self, pad=(12, 8, 12, 10))
        self.pv = pv
        pv.body.addWidget(C.sec_title("预览"))
        self.holder = PreviewBox("生成后在这里翻页预览",
                                 "版式由模板决定，这一栏只是预览，不改内容", self)
        pv.body.addWidget(self.holder, 1)
        right.addWidget(pv, 1)

        body.addLayout(right, 1)
        root.addLayout(body, 1)

    def showEvent(self, e):
        """参数卡按内容顶住最小高度。

        外层是「参数卡（不伸展）+ 预览卡（伸展）」，空间紧的时候 Qt 会先压
        参数卡 —— 压到比 sizeHint 还矮，最后那行按钮就骑在卡片边框上。
        setMinimumHeight 是一根硬底，压不过去。
        """
        super(ReportTab, self).showEvent(e)
        # sizeHint 会随列宽变（字段标签一换行就矮一截），所以再给一个常量兜底：
        # 216 是 10pt 字体下这张卡"标题 + 三行字段 + 按钮行"的最小高度。
        h = max(self.pc.sizeHint().height(), 216)
        if h > self.pc.minimumHeight():
            self.pc.setMinimumHeight(h)
