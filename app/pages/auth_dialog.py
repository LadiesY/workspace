# -*- coding: utf-8 -*-
"""登入 / 注册 / 忘记密码 —— 一个窗口三页。

为什么不拆成三个对话框
----------------------
三个动作共用"用户名、角色、身份证后 6 位"这几样东西，注册完紧接着要登入、
重置完也要登入。拆开就要来回关窗开窗、传用户名，体验上像三个不同程序。
这里做成一个无边框卡片，页内切换，成功后**自动带着用户名跳到登入页**，
用户只需要再输一次密码。

无边框是为了跟主窗口的浅色主题连成一片：系统对话框的标题栏是系统灰，
和这套白卡蓝线放一起很扎眼。拖动、关闭都自己处理（见 mousePressEvent）。

安全相关的几处约定
------------------
* 密码框一律 ``EchoMode.Password``；不清空输入，出错时原因写在输入框下面，
  不弹二次对话框（弹窗会把刚输的内容和焦点一起打断）。
* 登入失败的原因照实给（"未注册" / "密码不对" / "已禁用" / "已锁定"），
  这是**本机工具**，藏信息只会让人反复试。锁定与失败次数在 auth.py 里。
* 注册成功、重置成功、登入失败都写审计（见 auth.py / audit.py）。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QComboBox, QDialog, QGraphicsDropShadowEffect,
                             QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QStackedWidget, QVBoxLayout, QWidget)

from .. import auth, theme
from ..audit import audit
from ..widgets import _font

W = 486
H = 528

QSS = """
QWidget#authCard {
    background: #ffffff;
    border: 1px solid #c3d7e9;
    border-radius: 12px;
}
QLabel { background: transparent; }
QLabel#authTitle   { color: #0b2e5c; }
QLabel#authSub     { color: #8ba1b5; }
QLabel#fieldLabel  { color: #5b7186; }
QLabel#authErr     { color: #c0392b; }
QLabel#authOk      { color: #1e8f56; }
QLabel#authHint    { color: #8ba1b5; }
QLineEdit, QComboBox {
    background: #ffffff; border: 1px solid #cfe0ee; border-radius: 3px;
    color: #1f3750; padding: 5px 8px; selection-background-color: #bcd8f2;
}
QLineEdit:focus, QComboBox:focus { border-color: #2e74c4; background: #fdfeff; }
QLineEdit[bad="true"] { border-color: #d98b83; background: #fffafa; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background: #ffffff; border: 1px solid #c2d6e8; color: #1f3750;
    selection-background-color: #e6f0fa; selection-color: #17497f;
}
QPushButton#authPrimary {
    background: #1b57a0; border: 1px solid #17497f; border-radius: 3px;
    color: #ffffff; font-weight: bold; padding: 8px 0;
}
QPushButton#authPrimary:hover  { background: #2e74c4; border-color: #2e74c4; }
QPushButton#authPrimary:pressed{ background: #17497f; }
QPushButton#authPrimary:disabled { background: #eef4fa; color: #a8bacb;
                                   border-color: #d8e5f1; }
QPushButton#linkBtn {
    background: transparent; border: none; color: #1b57a0; padding: 0;
    text-align: left;
}
QPushButton#linkBtn:hover { color: #2e74c4; }
QPushButton#authClose {
    background: transparent; border: none; color: #8ba1b5; font-size: 15px;
}
QPushButton#authClose:hover { color: #c0392b; }
"""


def _label(text, obj, px=10, bold=False, spacing=0.3):
    lb = QLabel(text)
    lb.setObjectName(obj)
    lb.setFont(_font(theme.F_BODY, px, bold=bold, spacing=spacing))
    return lb


def _field_label(text):
    lb = _label(text, "fieldLabel", 10)
    lb.setFixedHeight(15)
    return lb


class _Pane(QWidget):
    """三页的公共部分：标题 + 副标题 + 错误/成功提示 + 主按钮。"""

    def __init__(self, title, sub, parent=None):
        super(_Pane, self).__init__(parent)
        # 注意：这里**不能**写 setStyleSheet("background: transparent")。
        # 在 Qt 里给一个控件设无选择器的样式，会向下作用到所有子控件，
        # 把按钮的 QSS 底色整条覆盖掉 —— 表现是按钮只剩边框、文字白到看不见
        # （实测踩过）。普通 QWidget 本来就不刷背景，不需要这一句。
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.lb_title = _label(title, "authTitle", 19, bold=True, spacing=1.0)
        self.lb_sub = _label(sub, "authSub", 10, spacing=0.3)
        lay.addWidget(self.lb_title)
        lay.addSpacing(5)
        lay.addWidget(self.lb_sub)
        lay.addSpacing(18)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(9)
        lay.addLayout(self.body)

        self.lb_msg = _label("", "authErr", 10, spacing=0.2)
        self.lb_msg.setWordWrap(True)
        self.lb_msg.setMinimumHeight(0)
        self.lb_msg.setVisible(False)
        lay.addSpacing(6)
        lay.addWidget(self.lb_msg)

        lay.addSpacing(8)
        self.btn_ok = QPushButton("")
        self.btn_ok.setObjectName("authPrimary")
        self.btn_ok.setFixedHeight(36)
        self.btn_ok.setFont(_font(theme.F_BODY, 12, bold=True, spacing=1.0))
        self.btn_ok.setCursor(Qt.PointingHandCursor)
        lay.addWidget(self.btn_ok)

        self.links = QHBoxLayout()
        self.links.setContentsMargins(0, 0, 0, 0)
        self.links.setSpacing(6)
        lay.addSpacing(12)
        lay.addLayout(self.links)
        lay.addStretch(1)

    def say(self, text, ok=False):
        """页内提示。ok=True 走绿色（注册/重置成功），否则红色。"""
        self.lb_msg.setObjectName("authOk" if ok else "authErr")
        # 换了 objectName 必须重新 polish，不然 QSS 里按 #id 写的颜色不会重算
        self.lb_msg.style().unpolish(self.lb_msg)
        self.lb_msg.style().polish(self.lb_msg)
        self.lb_msg.setText(text or "")
        self.lb_msg.setVisible(bool(text))

    def add_field(self, label, widget):
        self.body.addWidget(_field_label(label))
        self.body.addWidget(widget)
        return widget

    def add_link(self, text, slot):
        b = QPushButton(text)
        b.setObjectName("linkBtn")
        b.setCursor(Qt.PointingHandCursor)
        b.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        b.clicked.connect(slot)
        self.links.addWidget(b)
        return b


class AuthDialog(QDialog):
    """mode="login" | "register"；成功后 self.user 是被登入的账号。"""

    def __init__(self, mode="login", parent=None):
        super(AuthDialog, self).__init__(parent)
        self.user = None
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setStyleSheet(QSS)
        self.resize(W + 24, H + 24)     # 外圈留 12px 给投影，不然阴影全被裁掉
        self._drag = None

        card = QWidget(self)
        card.setObjectName("authCard")
        card.setGeometry(12, 12, W, H)
        self._card = card
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(26)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(11, 46, 92, 60))
        card.setGraphicsEffect(shadow)

        root = QVBoxLayout(card)
        root.setContentsMargins(34, 20, 34, 24)
        root.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.lb_brand = _label(theme.APP_NAME + " · 本地账户", "authSub", 9,
                               spacing=1.4)
        head.addWidget(self.lb_brand)
        head.addStretch(1)
        btn_x = QPushButton("✕")
        btn_x.setObjectName("authClose")
        btn_x.setFixedSize(22, 22)
        btn_x.setCursor(Qt.PointingHandCursor)
        btn_x.clicked.connect(self.reject)
        btn_x.setToolTip("关闭")
        head.addWidget(btn_x)
        root.addLayout(head)
        root.addSpacing(14)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self._build_login()
        self._build_register()
        self._build_forgot()

        self.goto(mode)
        self._fill_users()

    # ---------------------------------------------------------------- 构件
    def _build_login(self):
        p = _Pane("登入", "选择本机已注册的账号，输入密码进入工具", self)
        p.btn_ok.setText("登　入")
        p.btn_ok.clicked.connect(self.do_login)

        self.cb_user = QComboBox()
        self.cb_user.setFixedHeight(32)
        self.cb_user.setFont(_font(theme.F_BODY, 12))
        self.cb_user.currentTextChanged.connect(self._sync_role_label)
        p.add_field("用户名", self.cb_user)

        self.lb_role = QLineEdit()
        self.lb_role.setReadOnly(True)
        self.lb_role.setFixedHeight(32)
        self.lb_role.setFont(_font(theme.F_BODY, 12))
        self.lb_role.setFocusPolicy(Qt.NoFocus)   # 只读栏不进 Tab 顺序
        p.add_field("角色权限（自动带出）", self.lb_role)

        self.ed_pwd = QLineEdit()
        self.ed_pwd.setEchoMode(QLineEdit.Password)
        self.ed_pwd.setFixedHeight(32)
        self.ed_pwd.setFont(_font(theme.F_BODY, 12))
        self.ed_pwd.returnPressed.connect(self.do_login)
        p.add_field("登入密码", self.ed_pwd)

        p.links.addStretch(1)
        p.add_link("忘记密码？", lambda: self.goto("forgot"))
        sep = _label("|", "authHint", 10)
        p.links.addWidget(sep)
        p.add_link("注册新账号", lambda: self.goto("register"))
        p.links.addStretch(1)

        self.page_login = p
        self.stack.addWidget(p)

    def _build_register(self):
        p = _Pane("注册账号", "账号只存在本机；关掉工具即退出，下次需重新登入", self)
        p.btn_ok.setText("注　册")
        p.btn_ok.clicked.connect(self.do_register)

        self.ed_new_name = QLineEdit()
        self.ed_new_name.setFixedHeight(32)
        self.ed_new_name.setFont(_font(theme.F_BODY, 12))
        self.ed_new_name.setMaxLength(20)
        self.ed_new_name.setPlaceholderText("2~20 字，不能用空格和 \\ / : * ?")
        p.add_field("用户名", self.ed_new_name)

        self.cb_role = QComboBox()
        self.cb_role.setFixedHeight(32)
        self.cb_role.setFont(_font(theme.F_BODY, 12))
        for r in auth.ROLES:
            # 下拉里只放角色名，逐条说明放到下面的提示行 —— 名字带一长串说明
            # 会把下拉撑得比输入框宽，白卡上很别扭
            self.cb_role.addItem(auth.ROLE_NAME[r], r)
        self.cb_role.currentIndexChanged.connect(self._sync_role_hint)
        p.add_field("角色权限", self.cb_role)

        self.lb_role_hint = _label("", "authHint", 9, spacing=0.2)
        self.lb_role_hint.setWordWrap(True)
        p.body.addWidget(self.lb_role_hint)
        p.body.addSpacing(2)

        self.ed_id6 = QLineEdit()
        self.ed_id6.setFixedHeight(32)
        self.ed_id6.setFont(_font(theme.F_BODY, 12))
        self.ed_id6.setMaxLength(6)
        self.ed_id6.setPlaceholderText("6 位数字，忘记密码时用来验证身份")
        p.add_field("身份证后 6 位", self.ed_id6)

        self.ed_new_pwd = QLineEdit()
        self.ed_new_pwd.setEchoMode(QLineEdit.Password)
        self.ed_new_pwd.setFixedHeight(32)
        self.ed_new_pwd.setFont(_font(theme.F_BODY, 12))
        p.add_field("登入密码", self.ed_new_pwd)

        self.ed_new_pwd2 = QLineEdit()
        self.ed_new_pwd2.setEchoMode(QLineEdit.Password)
        self.ed_new_pwd2.setFixedHeight(32)
        self.ed_new_pwd2.setFont(_font(theme.F_BODY, 12))
        self.ed_new_pwd2.returnPressed.connect(self.do_register)
        p.add_field("确认密码", self.ed_new_pwd2)

        self.lb_rule = _label("", "authHint", 9, spacing=0.2)
        p.body.addSpacing(2)
        p.body.addWidget(self.lb_rule)

        p.links.addStretch(1)
        p.add_link("已有账号，去登入", lambda: self.goto("login"))
        p.links.addStretch(1)

        self.page_reg = p
        self.stack.addWidget(p)
        self._sync_role_hint()

    def _build_forgot(self):
        p = _Pane("重置密码", "用注册时填的身份证后 6 位验证身份，然后设置新密码", self)
        p.btn_ok.setText("重置密码")
        p.btn_ok.clicked.connect(self.do_reset)

        self.cb_user2 = QComboBox()
        self.cb_user2.setFixedHeight(32)
        self.cb_user2.setFont(_font(theme.F_BODY, 12))
        p.add_field("用户名", self.cb_user2)

        self.ed_id6b = QLineEdit()
        self.ed_id6b.setFixedHeight(32)
        self.ed_id6b.setFont(_font(theme.F_BODY, 12))
        self.ed_id6b.setMaxLength(6)
        self.ed_id6b.setPlaceholderText("注册时填的 6 位数字")
        p.add_field("身份证后 6 位", self.ed_id6b)

        self.ed_pwd_b = QLineEdit()
        self.ed_pwd_b.setEchoMode(QLineEdit.Password)
        self.ed_pwd_b.setFixedHeight(32)
        self.ed_pwd_b.setFont(_font(theme.F_BODY, 12))
        p.add_field("新密码", self.ed_pwd_b)

        self.ed_pwd_b2 = QLineEdit()
        self.ed_pwd_b2.setEchoMode(QLineEdit.Password)
        self.ed_pwd_b2.setFixedHeight(32)
        self.ed_pwd_b2.setFont(_font(theme.F_BODY, 12))
        self.ed_pwd_b2.returnPressed.connect(self.do_reset)
        p.add_field("确认新密码", self.ed_pwd_b2)

        p.links.addStretch(1)
        p.add_link("返回登入", lambda: self.goto("login"))
        p.links.addStretch(1)

        self.page_forgot = p
        self.stack.addWidget(p)

    # ---------------------------------------------------------------- 状态
    def _fill_users(self):
        names = auth.users.names()
        keep = self.cb_user.currentText()
        for cb in (self.cb_user, self.cb_user2):
            cb.blockSignals(True)
            cb.clear()
            if names:
                cb.addItems(names)
            else:
                cb.addItem("（本机还没有账号）")
            cb.blockSignals(False)
        if keep in names:
            self.cb_user.setCurrentText(keep)
        self._sync_role_label()

    def _sync_role_label(self, *_a):
        u = auth.users.get(self.cb_user.currentText())
        if not u:
            self.lb_role.setText("—")
        else:
            txt = auth.ROLE_NAME.get(u.get("role"), u.get("role"))
            if u.get("disabled"):
                txt += "（已禁用）"
            self.lb_role.setText(txt)

    def _sync_role_hint(self, *_a):
        role = self.cb_role.currentData() or "operator"
        self.lb_role_hint.setText(auth.ROLE_DESC.get(role, ""))
        sec = auth.settings.get("security") or {}
        bits = ["密码至少 %d 位" % int(sec.get("pwd_min", 6))]
        if sec.get("need_digit", True):
            bits.append("含数字")
        if sec.get("need_letter", False):
            bits.append("含字母")
        self.lb_rule.setText("｜".join(bits) + "（策略可在系统管理里改）")

    def goto(self, mode):
        self._mode = mode
        page = {"login": 0, "register": 1, "forgot": 2}.get(mode, 0)
        self.stack.setCurrentIndex(page)
        # 三页字段数不一样，窗口按页收放 —— 固定成最高那页的话，登入页下面会空
        # 一大块白，看着像没加载完。高度是量出来的：各页 sizeHint + 80（品牌行
        # 与上下留白），少 10px 就会把标签挤到输入框上（实测踩过）。
        h = {"login": 432, "register": 616, "forgot": 500}.get(mode, 588)
        self.resize(W + 24, h + 24)
        self._card.setGeometry(12, 12, W, h)
        if mode == "login":
            self._fill_users()
            self.ed_pwd.clear()
            self.ed_pwd.setFocus()
        elif mode == "register":
            for w in (self.ed_new_name, self.ed_id6, self.ed_new_pwd,
                      self.ed_new_pwd2):
                w.clear()
            self.page_reg.say("")
            self.ed_new_name.setFocus()
        else:
            self._fill_users()
            for w in (self.ed_id6b, self.ed_pwd_b, self.ed_pwd_b2):
                w.clear()
            self.page_forgot.say("")
            self.ed_id6b.setFocus()

    def _mark(self, *edits, bad=True):
        for e in edits:
            e.setProperty("bad", "true" if bad else "false")
            e.style().unpolish(e)
            e.style().polish(e)

    # ---------------------------------------------------------------- 动作
    def do_login(self):
        name = self.cb_user.currentText()
        if not auth.users.get(name):
            self.page_login.say("本机还没有账号，请先注册")
            return False
        ok, msg, user = auth.users.check_login(name, self.ed_pwd.text())
        if not ok:
            audit.write("登入失败", target=name, result="FAIL", detail=msg,
                        user=name, role="-")
            self.page_login.say(msg)
            self._mark(self.ed_pwd)
            self.ed_pwd.selectAll()
            self.ed_pwd.setFocus()
            return False
        self._mark(self.ed_pwd, bad=False)
        auth.session.login(user)
        audit.write("登入成功", target=name,
                    detail="角色：%s；来源：本机界面" % auth.session.role_name,
                    user=name, role=auth.session.role_name)
        self.user = user
        self.accept()
        return True

    def do_register(self):
        name = self.ed_new_name.text().strip()
        role = self.cb_role.currentData() or "operator"
        id6 = self.ed_id6.text().strip()
        pwd = self.ed_new_pwd.text()
        pwd2 = self.ed_new_pwd2.text()
        self._mark(self.ed_new_name, self.ed_id6, self.ed_new_pwd,
                   self.ed_new_pwd2, bad=False)
        if not name:
            self.page_reg.say("请填用户名")
            self._mark(self.ed_new_name)
            self.ed_new_name.setFocus()
            return False
        if not (id6.isdigit() and len(id6) == 6):
            self.page_reg.say("身份证后 6 位要填 6 个数字")
            self._mark(self.ed_id6)
            self.ed_id6.setFocus()
            return False
        msg = auth.password_error(pwd)
        if msg:
            self.page_reg.say(msg)
            self._mark(self.ed_new_pwd)
            self.ed_new_pwd.setFocus()
            return False
        if pwd != pwd2:
            self.page_reg.say("两次输入的密码不一致")
            self._mark(self.ed_new_pwd2)
            self.ed_new_pwd2.setFocus()
            return False
        try:
            auth.users.add(name, role, id6, pwd)
        except ValueError as e:
            self.page_reg.say(str(e))
            self._mark(self.ed_new_name)
            return False
        audit.write("注册账号", target=name,
                    detail="角色：%s" % auth.ROLE_NAME.get(role, role),
                    user=name, role=auth.ROLE_NAME.get(role, role))
        # 注册完直接带去登入页，只让再输一次密码
        self.goto("login")
        if self.cb_user.findText(name) >= 0:
            self.cb_user.setCurrentText(name)
        self._sync_role_label()
        self.page_login.say("注册成功。请输入刚才设置的密码登入。", ok=True)
        self.ed_pwd.setFocus()
        return True

    def do_reset(self):
        name = self.cb_user2.currentText()
        id6 = self.ed_id6b.text().strip()
        pwd = self.ed_pwd_b.text()
        pwd2 = self.ed_pwd_b2.text()
        self._mark(self.ed_id6b, self.ed_pwd_b, self.ed_pwd_b2, bad=False)
        if not auth.users.get(name):
            self.page_forgot.say("请选择一个已注册的用户名")
            return False
        if pwd != pwd2:
            self.page_forgot.say("两次输入的新密码不一致")
            self._mark(self.ed_pwd_b2)
            return False
        ok, msg = auth.users.reset_by_id(name, id6, pwd)
        if not ok:
            audit.write("密码重置失败", target=name, result="FAIL", detail=msg,
                        user=name, role="-")
            self.page_forgot.say(msg)
            self._mark(self.ed_id6b if "身份证" in msg else self.ed_pwd_b)
            return False
        audit.write("重置密码", target=name, detail="凭身份证后 6 位自助重置",
                    user=name, role="-")
        self.goto("login")
        if self.cb_user.findText(name) >= 0:
            self.cb_user.setCurrentText(name)
        self._sync_role_label()
        self.page_login.say("密码已重置。请用新密码登入。", ok=True)
        self.ed_pwd.setFocus()
        return True

    # ---------------------------------------------------------------- 外框
    def paintEvent(self, _e):
        """整窗透明，白色圆角卡片由 authCard 那个子控件画（QSS 里那条）。

        这里**不能**再刷一层白：刷了就把投影盖住了，卡片看着像贴死在屏幕边上。
        """
        pass

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.pos().y() < 72:
            self._drag = e.globalPos() - self.frameGeometry().topLeft()
            e.accept()
            return
        super(AuthDialog, self).mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPos() - self._drag)
            e.accept()
            return
        super(AuthDialog, self).mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag = None
        super(AuthDialog, self).mouseReleaseEvent(e)
