# -*- coding: utf-8 -*-
"""引脚类别管理（列举 / 新增 / 删除自定义类别）

用户 2026-11-11 的规格
----------------------
* 列举引脚算法类别，每类一个按钮：T / F / O / C / V / L；
* **不点【编辑类别】就不能选择类别**（只读态点击只给一句提示，不选中）；
* 编辑态里有【新增类别】【删除类别】；
* 内置 6 类**永远不可删除**；新增的可以删；
* 新增 = 选基础大类 + 输入名称（只能字母、不得重名）+ 每排引脚数。
  例：Y + T 大类 + 每排 14 → 标签 Y 的框按 T 算法识别、n=14，原 T 仍是 10。

实现上"新增类别"只是往 `data/pin_kinds.json` 写一行，识别时由
`pin_detect.detect()` 查表后转调基础算法并把 n 塞进 `opt["n"]`；没有复制任何
算法代码，也没有改任何模块常量，所以**原类别的识别结果逐点不变**。
"""

import os
import sys

from PyQt5.QtCore import QRect, QRegExp, Qt
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPen, QRegExpValidator
from PyQt5.QtWidgets import (QComboBox, QDialog, QGridLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QSpinBox,
                             QVBoxLayout, QWidget)

from .. import theme
from ..widgets import _font, paint_panel
from ..vision import engine as _engine

if _engine.VENDOR_DIR not in sys.path:
    sys.path.insert(0, _engine.VENDOR_DIR)
import pin_kinds                                    # noqa: E402

# 内置类别的中文一句话（按钮副标题用；比 pin_kinds 的 note 更适合界面）
_TAG = {
    "O": "单排 7 脚",
    "T": "双面两排 10 脚",
    "F": "四边四排 32 脚",
    "C": "两端件 2 点",
    "L": "双面两排 3 脚",
    "V": "两端件 2 点",
    "K": "任意朝向 任意脚数",
}

DLG_QSS = """
QDialog { background: #eef4fa; }
QLabel#kTitle { color: #0b2e5c; }
QLabel#kSub   { color: #5b7186; }
QLabel#kHint  { color: #8ba1b5; }
QLabel#kBad   { color: #c0392b; }
QLabel#kOk    { color: #1e8f56; }
QPushButton#kDel {
    background: #fdf4f3; border: 1px solid #e0b4ae; border-radius: 3px;
    color: #a83226; padding: 4px 12px;
}
QPushButton#kDel:hover { background: #fbe9e7; border-color: #c0392b; }
QPushButton#kDel:disabled { color: #c9a9a4; background: #fdf8f7;
                            border-color: #ecd6d2; }
QFrame#kLine { background: #e0ebf5; }
QWidget#kCard { background: #ffffff; border: 1px solid #c3d7e9; border-radius: 8px; }
"""


# --------------------------------------------------------------------------- #
# 类别按钮（自绘：上排类别名、下排算法与点数）
# --------------------------------------------------------------------------- #
class KindButton(QPushButton):
    """一个类别一个按钮。自绘而不用 QSS `:checked` —— 全局按钮样式里的
    border/bg 会被 :checked 规则打架，两态深浅不好控。"""

    def __init__(self, spec, parent=None, w=210, h=62):
        super(KindButton, self).__init__(parent)
        self.spec = dict(spec)
        self.setCheckable(True)
        self.setFlat(True)
        self.setFixedSize(w, h)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_Hover, True)
        self.setToolTip(self._tip())

    def _tip(self):
        s = self.spec
        n = int(s["n"])
        tot = pin_kinds.total_expected(s["name"])
        if s["base"] in ("C", "V"):
            return ("%s：两端件，与 %s 同类算法，恒 2 点。%s"
                    % (s["name"], s["base"],
                       "内置类别，不可删除。" if s.get("builtin") else "自定义类别，可删除。"))
        return ("%s：按 %s 类算法识别，每排 %d 个、共 %d 点。\n%s"
                % (s["name"], s["base"], n, tot,
                   "内置类别，不可删除。" if s.get("builtin") else "自定义类别，可删除。"))

    def enterEvent(self, e):
        self.update()
        return super(KindButton, self).enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        return super(KindButton, self).leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self.rect().adjusted(0, 0, -1, -1)
        if self.isChecked():
            bg, bd = "#e6f1fb", theme.AMBER2
        elif self.underMouse():
            bg, bd = "#ffffff", "#9fc2e0"
        else:
            bg, bd = "#ffffff", "#c3d7e9"
        p.setPen(QPen(QColor(bd), 1.0))
        p.setBrush(QColor(bg))
        p.drawRoundedRect(r, 7, 7)

        name = self.spec["name"]
        p.setPen(QColor("#0b2e5c"))
        p.setFont(_font(theme.F_BODY, 13, bold=True, spacing=0.6))
        p.drawText(QRect(r.left() + 12, r.top() + 9, r.width() - 60, 20),
                   Qt.AlignLeft | Qt.AlignVCenter, name)

        # 右上角：自定义类别标一枚小胶囊，内置的不标（一眼看得出哪些能删）
        if not self.spec.get("builtin"):
            tag = "新增"
            p.setFont(_font(theme.F_BODY, 8, spacing=0.2))
            tw = QFontMetrics(p.font()).width(tag) + 10
            tr = QRect(r.right() - tw - 8, r.top() + 9, tw, 15)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#fdf1e2"))
            p.drawRoundedRect(tr, 7, 7)
            p.setPen(QColor("#a4670f"))
            p.drawText(tr, Qt.AlignCenter, tag)

        p.setPen(QColor(theme.TXT2))
        p.setFont(_font(theme.F_BODY, 9, spacing=0.1))
        txt = _TAG.get(name) or (
            "%s · %d 脚/排 · 共 %d 点" % (self.spec["base"], self.spec["n"],
                                          pin_kinds.total_expected(name)))
        fm = QFontMetrics(p.font())
        txt = fm.elidedText(txt, Qt.ElideRight, r.width() - 24)
        p.drawText(QRect(r.left() + 12, r.top() + 33, r.width() - 24, 16),
                   Qt.AlignLeft | Qt.AlignVCenter, txt)
        p.end()


# --------------------------------------------------------------------------- #
# 新增类别
# --------------------------------------------------------------------------- #
class NewKindDialog(QDialog):
    """新增自定义类别：选大类 → 输入名称 → 输入每排引脚数。"""

    def __init__(self, parent=None):
        super(NewKindDialog, self).__init__(parent)
        self.setWindowTitle("新增引脚类别")
        self.setModal(True)
        self.setFixedSize(470, 286)
        self.setStyleSheet(DLG_QSS + theme.controls_qss())
        self.created = None

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 14)
        v.setSpacing(10)

        t = QLabel("新增引脚类别")
        t.setObjectName("kTitle")
        t.setFont(_font(theme.F_BODY, 14, bold=True, spacing=0.6))
        v.addWidget(t)
        s = QLabel("新类别只是「基础大类 + 每排引脚数」，识别时转调该大类的算法，\n"
                   "不会改动原类别的任何参数。")
        s.setObjectName("kSub")
        s.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        v.addWidget(s)
        v.addSpacing(4)

        # ---- 基础大类
        lb1 = QLabel("基础大类（决定用哪套算法）")
        lb1.setObjectName("kHint")
        lb1.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        v.addWidget(lb1)
        self.cb_base = QComboBox()
        self.cb_base.setFixedHeight(30)
        for nm in ("T", "F", "O", "C", "V", "L", "K"):
            self.cb_base.addItem("%s　%s" % (nm, _TAG[nm]), nm)
        self.cb_base.currentIndexChanged.connect(self._sync)
        v.addWidget(self.cb_base)

        # ---- 名称 + 每排数
        row = QHBoxLayout()
        row.setSpacing(12)

        box1 = QVBoxLayout()
        box1.setSpacing(3)
        lb2 = QLabel("新类别名称（只能是字母）")
        lb2.setObjectName("kHint")
        lb2.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        box1.addWidget(lb2)
        self.ed_name = QLineEdit()
        self.ed_name.setFixedHeight(30)
        self.ed_name.setMaxLength(3)
        self.ed_name.setPlaceholderText("如 Y")
        self.ed_name.setValidator(QRegExpValidator(QRegExp("[A-Za-z]{1,3}"), self))
        self.ed_name.textChanged.connect(self._sync)
        box1.addWidget(self.ed_name)
        row.addLayout(box1, 2)

        box2 = QVBoxLayout()
        box2.setSpacing(3)
        lb3 = QLabel("单排引脚数")
        lb3.setObjectName("kHint")
        lb3.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        box2.addWidget(lb3)
        self.sp_n = QSpinBox()
        self.sp_n.setRange(1, 256)
        self.sp_n.setValue(14)
        self.sp_n.setFixedHeight(30)
        self.sp_n.setMinimumWidth(96)
        self.sp_n.valueChanged.connect(self._sync)
        box2.addWidget(self.sp_n)
        row.addLayout(box2, 1)
        v.addLayout(row)

        self.lb_msg = QLabel("")
        self.lb_msg.setObjectName("kHint")
        self.lb_msg.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        self.lb_msg.setWordWrap(True)
        self.lb_msg.setMinimumHeight(34)
        v.addWidget(self.lb_msg)

        v.addStretch(1)
        foot = QHBoxLayout()
        foot.addStretch(1)
        b_cancel = QPushButton("取消")
        b_cancel.setObjectName("act")
        b_cancel.setFixedSize(88, 30)
        b_cancel.setCursor(Qt.PointingHandCursor)
        b_cancel.clicked.connect(self.reject)
        foot.addWidget(b_cancel)
        self.b_ok = QPushButton("新增")
        self.b_ok.setObjectName("primary")
        self.b_ok.setFixedSize(88, 30)
        self.b_ok.setCursor(Qt.PointingHandCursor)
        self.b_ok.clicked.connect(self._on_ok)
        foot.addWidget(self.b_ok)
        v.addLayout(foot)

        self._sync()

    def _sync(self, *_a):
        # 名称统一大写（与 pin_kinds 的规范化一致，避免界面显示小写、存盘变大写）
        txt = self.ed_name.text()
        up = txt.upper()
        if up != txt:
            pos = self.ed_name.cursorPosition()
            self.ed_name.setText(up)
            self.ed_name.setCursorPosition(pos)

        base = self.cb_base.currentData()
        two_end = base in ("C", "V")
        # 两端件只禁用输入框、不改写里面的数 —— 切回 T 时用户填的 14 还在
        self.sp_n.setEnabled(not two_end)

        name = self.ed_name.text().strip().upper() or "?"
        per = {"O": 1, "T": 2, "L": 2, "F": 4, "C": 1, "V": 1, "K": 2}.get(base, 1)
        total = (1 if two_end else int(self.sp_n.value())) * per
        self.lb_msg.setObjectName("kHint")
        self.lb_msg.setStyleSheet("color:#8ba1b5;")
        if two_end:
            self.lb_msg.setText("C / V 是两端件：每端固定 1 个焊盘，每排只能填 1，共 2 点。")
        elif base == "K":
            self.lb_msg.setText("将按 K 类算法识别：自己判朝向（上下或左右两排），"
                                "每排 %d 个 = 共 %d 点。" % (self.sp_n.value(), total))
        else:
            self.lb_msg.setText("将按 %s 类算法识别：每排 %d 个 × %d 排 = 共 %d 点。"
                                % (base, self.sp_n.value(), per, total))
        self.b_ok.setEnabled(True)

    def _on_ok(self):
        nm = self.ed_name.text().strip().upper()
        base = self.cb_base.currentData()
        ok, msg = pin_kinds.validate(nm, base, self.sp_n.value())
        if not ok:
            self.lb_msg.setStyleSheet("color:#c0392b;")
            self.lb_msg.setText(msg)
            return
        ok, msg = pin_kinds.add(nm, base, self.sp_n.value())
        if not ok:
            self.lb_msg.setStyleSheet("color:#c0392b;")
            self.lb_msg.setText(msg)
            return
        self.created = nm
        self.accept()


# --------------------------------------------------------------------------- #
# 类别管理
# --------------------------------------------------------------------------- #
class KindDialog(QDialog):
    """类别列表 + 编辑态增删。

    只读态：点按钮只提示"请先点【编辑类别】"，不选中 —— 这就是用户说的
    "不点编辑类别就无法选择类别"。
    """

    COLS = 3

    def __init__(self, parent=None):
        super(KindDialog, self).__init__(parent)
        self.setWindowTitle("引脚类别管理")
        self.setModal(True)
        self.setFixedSize(724, 452)
        self.setStyleSheet(DLG_QSS + theme.controls_qss())
        self.editing = False
        self.cur = None
        self._btns = {}

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 14)
        v.setSpacing(9)

        # ---- 标题行
        head = QHBoxLayout()
        head.setSpacing(12)
        box = QVBoxLayout()
        box.setSpacing(3)
        t = QLabel("引脚类别管理")
        t.setObjectName("kTitle")
        t.setFont(_font(theme.F_BODY, 14, bold=True, spacing=0.6))
        box.addWidget(t)
        s = QLabel("内置 O / T / F / C / V / L 永远存在、不可删除；\n"
                   "新增类别只换「基础大类 + 每排引脚数」，原类别参数一个都不动。")
        s.setObjectName("kSub")
        s.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        s.setWordWrap(True)
        box.addWidget(s)
        head.addLayout(box, 1)

        self.b_edit = QPushButton("编辑类别")
        self.b_edit.setObjectName("act")
        self.b_edit.setCheckable(True)
        self.b_edit.setFixedSize(96, 30)
        self.b_edit.setCursor(Qt.PointingHandCursor)
        self.b_edit.setToolTip("点一下进入编辑态：可以选择类别、新增/删除。\n"
                              "只读态下点类别按钮不会选中。")
        self.b_edit.toggled.connect(self._on_edit_toggled)
        head.addWidget(self.b_edit, 0, Qt.AlignTop)
        v.addLayout(head)

        line = QWidget()
        line.setObjectName("kLine")
        line.setFixedHeight(1)
        line.setStyleSheet("QWidget#kLine { background:#e0ebf5; }")
        v.addWidget(line)

        # ---- 类别按钮网格
        self.grid_box = QWidget()
        self.grid = QGridLayout(self.grid_box)
        self.grid.setContentsMargins(0, 2, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(10)
        v.addWidget(self.grid_box)

        # ---- 详情卡
        self.card = QWidget()
        self.card.setObjectName("kCard")
        self.card.setFixedHeight(96)
        cv = QHBoxLayout(self.card)
        cv.setContentsMargins(16, 12, 16, 12)
        cv.setSpacing(14)
        info = QVBoxLayout()
        info.setSpacing(6)
        self.lb_sel = QLabel("未选择类别")
        self.lb_sel.setObjectName("kTitle")
        self.lb_sel.setFont(_font(theme.F_BODY, 12, bold=True, spacing=0.5))
        info.addWidget(self.lb_sel)
        self.lb_desc = QLabel("点【编辑类别】后可选择类别。")
        self.lb_desc.setObjectName("kSub")
        self.lb_desc.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        self.lb_desc.setWordWrap(True)
        info.addWidget(self.lb_desc)
        info.addStretch(1)
        cv.addLayout(info, 1)

        self.b_del = QPushButton("删除类别")
        self.b_del.setObjectName("kDel")
        self.b_del.setFixedSize(92, 30)
        self.b_del.setCursor(Qt.PointingHandCursor)
        self.b_del.setEnabled(False)
        self.b_del.clicked.connect(self._on_del)
        cv.addWidget(self.b_del, 0, Qt.AlignVCenter)
        v.addWidget(self.card)

        v.addStretch(1)

        # ---- 底栏
        foot = QHBoxLayout()
        foot.setSpacing(10)
        self.b_add = QPushButton("新增类别")
        self.b_add.setObjectName("primary")
        self.b_add.setFixedSize(96, 30)
        self.b_add.setCursor(Qt.PointingHandCursor)
        self.b_add.setEnabled(False)
        self.b_add.setToolTip("先点右上角【编辑类别】")
        self.b_add.clicked.connect(self._on_add)
        foot.addWidget(self.b_add)
        self.lb_foot = QLabel("")
        self.lb_foot.setObjectName("kHint")
        self.lb_foot.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        foot.addWidget(self.lb_foot, 1)
        b_close = QPushButton("关闭")
        b_close.setObjectName("act")
        b_close.setFixedSize(88, 30)
        b_close.setCursor(Qt.PointingHandCursor)
        b_close.clicked.connect(self.accept)
        foot.addWidget(b_close)
        v.addLayout(foot)

        self._rebuild()

    # ------------------------------------------------------------ 构建 / 刷新
    def _rebuild(self):
        """重建按钮网格（类别数量少，直接重建比增量改简单且不会残留）。"""
        while self.grid.count():
            it = self.grid.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._btns = {}
        specs = pin_kinds.specs()
        # 末行不足一整行时整体居中左移（1 个别孤零零挂在最左边）
        rem = len(specs) % self.COLS
        off = (self.COLS - rem) // 2 if rem else 0
        for i, sp in enumerate(specs):
            b = KindButton(sp, self.grid_box)
            b.clicked.connect(lambda _c=False, nm=sp["name"]: self._on_pick(nm))
            r, c = i // self.COLS, i % self.COLS
            if r == len(specs) // self.COLS:
                c += off
            self.grid.addWidget(b, r, c, Qt.AlignCenter)
            self._btns[sp["name"]] = b
        if self.cur in self._btns:
            self._btns[self.cur].setChecked(True)
        else:
            self.cur = None
            self._show_detail(None)

    def _on_edit_toggled(self, on):
        self.editing = bool(on)
        self.b_edit.setText("完成编辑" if on else "编辑类别")
        self.b_edit.setObjectName("primary" if on else "act")
        # objectName 变了要重新套一遍样式，否则还是旧配色
        self.b_edit.style().unpolish(self.b_edit)
        self.b_edit.style().polish(self.b_edit)
        self.b_add.setEnabled(on)
        self.b_add.setToolTip("" if on else "先点右上角【编辑类别】")
        self.lb_foot.setText("编辑中：点类别可选中，自定义类别可删除。" if on else "")
        if not on:
            for b in self._btns.values():
                b.setChecked(False)
            self.cur = None
            self._show_detail(None)
        else:
            self._show_detail(self.cur)

    def _on_pick(self, name):
        if not self.editing:
            # 只读态：checkable 的按钮自己会把 checked 翻过来，必须按回去 ——
            # 否则"没选中"看起来像"选中了"，用户会以为类别已经切过去了。
            self._btns[name].setChecked(False)
            self.lb_foot.setText("只读模式：请先点右上角【编辑类别】，再选择类别。")
            return
        self.cur = name
        for k, b in self._btns.items():
            b.setChecked(k == name)
        self._show_detail(name)

    def _show_detail(self, name):
        if not name:
            self.lb_sel.setText("未选择类别" if self.editing else "未选择类别")
            self.lb_desc.setText("点【编辑类别】后可选择类别。"
                                 if not self.editing else "点上面任一类别查看详情，"
                                                          "自定义类别可删除。")
            self.b_del.setEnabled(False)
            self.b_del.setToolTip("")
            return
        sp = pin_kinds.get(name) or {}
        builtin = bool(sp.get("builtin"))
        self.lb_sel.setText("类别 %s　%s" % (name, "内置" if builtin else "自定义"))
        if builtin:
            self.lb_desc.setText(
                "按 %s 类算法识别，%s。\n"
                "内置类别不可删除、引脚数固定；要不同脚数请用【新增类别】另建名称。"
                % (sp.get("base"), pin_kinds.describe(name)))
            self.b_del.setEnabled(False)
            self.b_del.setToolTip("内置类别不可删除")
        else:
            self.lb_desc.setText(
                "按 %s 类算法识别，%s。\n"
                "删除后，标签为 %s 的已存框会回退到通用算法，点数不保证准确。"
                % (sp.get("base"), pin_kinds.describe(name), name))
            self.b_del.setEnabled(True)
            self.b_del.setToolTip("删除这个自定义类别")

    # ------------------------------------------------------------ 增 / 删
    def _on_add(self):
        d = NewKindDialog(self)
        if d.exec_() != QDialog.Accepted or not d.created:
            return
        nm = d.created
        self.cur = nm
        self._rebuild()
        self.lb_foot.setText("已新增类别「%s」：标签为 %s 的框会按 %s 类算法、"
                             "每排 %d 点识别。"
                             "「测试管理 → 测试程序编写」的「矩形框」列已同步（回到那一页即生效）。"
                             % (nm, nm, pin_kinds.get(nm)["base"],
                                pin_kinds.get(nm)["n"]))

    def _on_del(self):
        if not self.cur:
            return
        sp = pin_kinds.get(self.cur) or {}
        if sp.get("builtin"):
            return
        from .sys_common import ask
        ok = ask(self, "删除类别",
                 "确定删除自定义类别「%s」？\n\n删除后，已存 JSON 里标签为 %s 的框"
                 "会回退到通用算法，不再按 %s 类 + n=%d 识别。"
                 % (self.cur, self.cur, sp.get("base"), sp.get("n")),
                 ok_text="删除", danger=True)
        if not ok:
            return
        removed = self.cur
        ok, msg = pin_kinds.remove(removed)
        self.cur = None
        self._rebuild()
        self.lb_foot.setText(msg + "「测试程序编写」里已填的 %s 不会被清掉，"
                                   "只是那一列的下拉候选里不再有它。" % removed)


def open_kind_manager(parent):
    """在视觉页上打开类别管理；返回对话框实例（测试用）。"""
    d = KindDialog(parent)
    d.exec_()
    return d
