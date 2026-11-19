# -*- coding: utf-8 -*-
"""系统管理页共用的小件：白卡、按钮、表格、确认框。

为什么单独一个文件
------------------
六个子功能的壳是一样的：白卡 + 一行工具栏 + 一张表 + 一句说明。样式各写一遍
迟早走样（本项目以前就出现过两页按钮高差 2px），所以统一从这里取。

**一个必须记住的坑**：给控件 setStyleSheet("background: transparent;") 这种
不带选择器的声明，会向下覆盖子控件已有的 QSS（按钮会只剩边框、白字看不见）。
本文件里所有样式都带选择器，或在顶层页面统一挂一次。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (QAbstractItemView, QDialog, QGraphicsDropShadowEffect,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from .. import auth, theme
from ..widgets import _font, paint_panel

# 页面级 QSS：所有规则都带选择器，挂在系统管理页顶层即可向下生效
PAGE_QSS = """
QLabel#secTitle { color: #0b2e5c; }
QLabel#hintTxt  { color: #8ba1b5; }
QLabel#warnTxt  { color: #b0740f; }
QLabel#okTxt    { color: #1e8f56; }
QLabel#badTxt   { color: #c0392b; }
QLabel#monoTxt  { color: #5b7186; }
QPushButton#danger {
    background: #fdf4f3; border: 1px solid #e0b4ae; border-radius: 3px;
    color: #a83226; padding: 4px 12px;
}
QPushButton#danger:hover { background: #fbe9e7; border-color: #c0392b; }
QPushButton#danger:disabled { color: #c9a9a4; background: #fdf8f7;
                              border-color: #ecd6d2; }
QFrame#hline { background: #e4eef7; }
"""


# ---------------------------------------------------------------- 文本小件
def sec_title(text):
    lb = QLabel(text)
    lb.setObjectName("secTitle")
    lb.setFont(_font(theme.F_BODY, 12, bold=True, spacing=0.5))
    return lb


def hint(text, obj="hintTxt", size=9):
    lb = QLabel(text)
    lb.setObjectName(obj)
    lb.setFont(_font(theme.F_BODY, size, spacing=0.2))
    lb.setWordWrap(True)
    return lb


def btn(text, primary=False, danger=False, width=None, h=26):
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedHeight(h)
    b.setFont(_font(theme.F_BODY, 11, bold=primary, spacing=0.3))
    if danger:
        b.setObjectName("danger")
    elif primary:
        b.setObjectName("primary")
    else:
        b.setObjectName("act")
    if width:
        b.setFixedWidth(width)
    return b


def row(*widgets, spacing=8):
    """把控件横排，末尾留伸缩。widgets 里的 None 当弹簧用。"""
    lay = QHBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        else:
            lay.addWidget(w)
    return lay


def field(label_text, widget):
    """标签在上、控件在下的窄条，给对话框里的表单用。"""
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(3)
    lb = QLabel(label_text)
    lb.setObjectName("hintTxt")
    lb.setFont(_font(theme.F_BODY, 9, spacing=0.2))
    v.addWidget(lb)
    v.addWidget(widget)
    return w


# ---------------------------------------------------------------- 白卡
class Card(QWidget):
    """白卡容器：画 paint_panel 打底，内容用 self.body 往里放。"""

    def __init__(self, parent=None, pad=(14, 12, 14, 12)):
        super(Card, self).__init__(parent)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*pad)
        self.body.setSpacing(10)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        paint_panel(p, self.rect())
        p.end()


# ---------------------------------------------------------------- 表格
def make_table(headers, widths=None, stretch_last=True):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(25)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setAlternatingRowColors(True)
    t.setShowGrid(False)
    t.setWordWrap(False)
    hh = t.horizontalHeader()
    hh.setHighlightSections(False)
    hh.setSectionResizeMode(QHeaderView.Interactive)
    hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    if widths:
        for i, w in enumerate(widths):
            t.setColumnWidth(i, w)
    hh.setStretchLastSection(stretch_last)
    return t


def cell(text, color=None, bold=False, mono=False, align=None):
    it = QTableWidgetItem(str(text))
    f = _font(theme.F_MONO if mono else theme.F_BODY, 10,
              bold=bold, spacing=0.2)
    it.setFont(f)
    if color:
        it.setForeground(QColor(color))
    if align is not None:
        it.setTextAlignment(align | Qt.AlignVCenter)
    return it


def fill_row(table, r, values, colors=None, bolds=None, monos=None):
    """少写重复的 setItem。values 与列数要一致。"""
    for c, v in enumerate(values):
        col = (colors or {}).get(c)
        bold = bool((bolds or {}).get(c))
        mono = bool((monos or {}).get(c))
        table.setItem(r, c, cell(v, col, bold, mono))


def clear_table(table):
    table.setRowCount(0)


def selected_row(table):
    rows = table.selectionModel().selectedRows() if table.selectionModel() else []
    return rows[0].row() if rows else -1


# ---------------------------------------------------------------- 确认框
class CardDialog(QDialog):
    """统一样式的确认/提示框：无边框白卡 + 标题 + 正文 + 可选密码 + 两个按钮。

    密码框不是装饰：恢复这类动作要求"二次确认并记录操作人"，
    光点一下确定证明不了是谁在操作，所以这里要重输一次当前账号的密码。
    """

    def __init__(self, parent, title, text="", ok_text="确定",
                 cancel_text="取消", danger=False, need_pwd=False,
                 pwd_hint="", validator=None, extra=None, width=430):
        super(CardDialog, self).__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self._ok = False
        self._drag = None
        self._validator = validator
        self._w = width

        card = QWidget(self)
        card.setObjectName("cardDlg")
        card.setStyleSheet("""
            QWidget#cardDlg { background: #ffffff; border: 1px solid #c3d7e9;
                              border-radius: 10px; }
            QLabel { background: transparent; }
            QLabel#dlgTitle { color: #0b2e5c; }
            QLabel#dlgText  { color: #4b6478; }
            QLabel#dlgErr   { color: #c0392b; }
            QLineEdit { background: #ffffff; border: 1px solid #cfe0ee;
                        border-radius: 3px; color: #1f3750; padding: 5px 8px; }
            QLineEdit:focus { border-color: #2e74c4; }
            QPushButton#okBtn {
                background: #1b57a0; border: 1px solid #17497f; border-radius: 3px;
                color: #ffffff; font-weight: bold; padding: 5px 18px;
            }
            QPushButton#okBtn:hover { background: #2e74c4; border-color: #2e74c4; }
            QPushButton#okBtn:disabled { background: #eef4fa; color: #a8bacb;
                                         border-color: #d8e5f1; }
            QPushButton#badBtn {
                background: #b33327; border: 1px solid #94261c; border-radius: 3px;
                color: #ffffff; font-weight: bold; padding: 5px 18px;
            }
            QPushButton#badBtn:hover { background: #c93a2f; border-color: #a92a22; }
            QPushButton#cancelBtn {
                background: #f4f9ff; border: 1px solid #c9dcec; border-radius: 3px;
                color: #24405c; padding: 5px 18px;
            }
            QPushButton#cancelBtn:hover { background: #e7f1fb; border-color: #8fb6da; }
        """)
        self._card = card

        lay = QVBoxLayout(card)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(0)

        self.lb_title = QLabel(title)
        self.lb_title.setObjectName("dlgTitle")
        self.lb_title.setFont(_font(theme.F_BODY, 14, bold=True, spacing=0.6))
        lay.addWidget(self.lb_title)
        lay.addSpacing(9)

        self.lb_text = QLabel(text)
        self.lb_text.setObjectName("dlgText")
        self.lb_text.setFont(_font(theme.F_BODY, 10.5, spacing=0.25))
        self.lb_text.setWordWrap(True)
        self.lb_text.setVisible(bool(text))
        lay.addWidget(self.lb_text)

        if extra is not None:
            lay.addSpacing(10)
            lay.addWidget(extra)

        self.ed_pwd = None
        if need_pwd:
            lay.addSpacing(12)
            lb = QLabel(pwd_hint or "请输入当前账号的密码以确认")
            lb.setObjectName("dlgText")
            lb.setFont(_font(theme.F_BODY, 9, spacing=0.2))
            lay.addWidget(lb)
            lay.addSpacing(4)
            self.ed_pwd = QLineEdit()
            self.ed_pwd.setEchoMode(QLineEdit.Password)
            self.ed_pwd.setFixedHeight(30)
            self.ed_pwd.setFont(_font(theme.F_BODY, 11))
            self.ed_pwd.returnPressed.connect(self._on_ok)
            lay.addWidget(self.ed_pwd)

        self.lb_err = QLabel("")
        self.lb_err.setObjectName("dlgErr")
        self.lb_err.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        self.lb_err.setWordWrap(True)
        self.lb_err.setVisible(False)
        lay.addSpacing(6)
        lay.addWidget(self.lb_err)

        lay.addSpacing(16)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        bar.addStretch(1)
        self.btn_cancel = QPushButton(cancel_text)
        self.btn_cancel.setObjectName("cancelBtn")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.setFont(_font(theme.F_BODY, 11))
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok = QPushButton(ok_text)
        self.btn_ok.setObjectName("badBtn" if danger else "okBtn")
        self.btn_ok.setCursor(Qt.PointingHandCursor)
        self.btn_ok.setFont(_font(theme.F_BODY, 11, bold=True))
        self.btn_ok.clicked.connect(self._on_ok)
        if cancel_text:
            bar.addWidget(self.btn_cancel)
        bar.addWidget(self.btn_ok)
        lay.addLayout(bar)

        # 高度按内容算：CardDialog 不参与主布局，写死会裁字
        self.setFixedWidth(width + 24)
        card.setFixedWidth(width)
        card.adjustSize()
        self.setFixedHeight(card.sizeHint().height() + 24)
        card.move(12, 12)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 7)
        shadow.setColor(QColor(11, 46, 92, 65))
        card.setGraphicsEffect(shadow)
        if self.ed_pwd is not None:
            self.ed_pwd.setFocus()

    # ------------------------------------------------------------ 结果
    def password(self):
        return self.ed_pwd.text() if self.ed_pwd is not None else ""

    def _say(self, text):
        self.lb_err.setText(text or "")
        self.lb_err.setVisible(bool(text))

    def _on_ok(self):
        if self._validator is not None:
            err = self._validator(self.password())
            if err:
                self._say(err)
                return
        self._ok = True
        self.accept()

    # ------------------------------------------------------------ 拖动
    def paintEvent(self, _e):
        pass

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.pos().y() < 34:
            self._drag = e.globalPos() - self.frameGeometry().topLeft()
            e.accept()
            return
        super(CardDialog, self).mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPos() - self._drag)
            e.accept()
            return
        super(CardDialog, self).mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag = None
        super(CardDialog, self).mouseReleaseEvent(e)


def check_password(pwd):
    """校验"当前登入者"的密码。返回错误说明，空串表示通过。"""
    u = auth.users.get(auth.session.name) if auth.session.logged_in else None
    if not u:
        return "会话已失效，请重新登入"
    if not auth.check_hash(pwd, u.get("pwd_salt"), u.get("pwd_hash")):
        return "密码不正确"
    return ""


def ask(parent, title, text, ok_text="确定", cancel_text="取消",
        danger=False, extra=None, width=430):
    d = CardDialog(parent, title, text, ok_text=ok_text,
                   cancel_text=cancel_text, danger=danger, extra=extra,
                   width=width)
    return d.exec_() == QDialog.Accepted


def ask_password(parent, title, text, ok_text="确认", danger=True,
                 pwd_hint="请输入当前账号的密码以确认（操作人会记入审计）"):
    """敏感操作的二次确认：必须输对当前账号密码才算数。"""
    d = CardDialog(parent, title, text, ok_text=ok_text, danger=danger,
                   need_pwd=True, pwd_hint=pwd_hint, validator=check_password)
    ok = d.exec_() == QDialog.Accepted
    return ok


def tell(parent, title, text, ok_text="知道了", kind="info"):
    """单按钮提示。kind 只影响按钮配色，danger 用于"操作被拒绝"。"""
    d = CardDialog(parent, title, text, ok_text=ok_text, cancel_text="",
                   danger=(kind == "warn"))
    d.btn_ok.setDefault(True)
    return d.exec_() == QDialog.Accepted


def fmt_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%.1f %s" % (n, unit)) if unit != "B" else ("%d B" % n)
        n /= 1024.0
    return "%.1f GB" % n
