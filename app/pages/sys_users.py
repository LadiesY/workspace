# -*- coding: utf-8 -*-
"""系统管理 → 用户管理。

能做的事只有四件：建号、分配角色、重置密码、禁用/删除。每件都写审计。

几条刻意加的保护
----------------
* **不能动自己**：禁用、删除、改角色都不能指向当前登入的账号。少这一条，
  管理员手一点就把自己锁在门外，而且现场不一定还有第二个人知道密码。
* **不能把最后一个管理员弄没**：禁用/删除/降级前先数一遍"还有几个启用状态的
  管理员"，只剩一个就拒绝。
* **有审计关联的账号不许删**：删掉之后历史记录里那个人就成了"查无此人"，
  追溯断了。这种情况只允许禁用（记录仍在，状态变成"已禁用"）。

删除用户时不碰审计目录里的任何东西 —— 这是审计"独立"的另一半含义。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                             QVBoxLayout, QWidget)

from .. import auth, theme
from ..audit import audit
from ..widgets import _font
from . import sys_common as C

HEADERS = ("用户名", "角色", "状态", "创建时间", "最后登入", "审计记录")
WIDTHS = (150, 90, 80, 150, 150, 80)


class UsersTab(QWidget):
    def __init__(self, parent=None):
        super(UsersTab, self).__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self.btn_new = C.btn("＋ 新建用户", primary=True, width=104)
        self.btn_role = C.btn("修改角色", width=84)
        self.btn_pwd = C.btn("重置密码", width=84)
        self.btn_toggle = C.btn("禁用", width=66)
        self.btn_del = C.btn("删除", danger=True, width=64)
        for b in (self.btn_new, self.btn_role, self.btn_pwd, self.btn_toggle,
                  self.btn_del):
            b.setParent(self)
        self.btn_new.clicked.connect(self._on_new)
        self.btn_role.clicked.connect(self._on_role)
        self.btn_pwd.clicked.connect(self._on_reset)
        self.btn_toggle.clicked.connect(self._on_toggle)
        self.btn_del.clicked.connect(self._on_delete)

        self.lb_count = C.hint("", size=9)
        root.addLayout(C.row(self.btn_new, self.btn_role, self.btn_pwd,
                             self.btn_toggle, self.btn_del, None, self.lb_count))

        card = C.Card(self, pad=(10, 8, 12, 10))
        card.setMinimumHeight(140)
        self.table = C.make_table(HEADERS, WIDTHS)
        self.table.itemSelectionChanged.connect(self._sync)
        self.table.doubleClicked.connect(lambda _i: self._on_role())
        card.body.addWidget(self.table, 1)
        root.addWidget(card, 1)
        root.addWidget(C.hint(
            "规则：用户名唯一；删除仅限没有审计记录的账号，否则请改为禁用"
            "（历史记录要留得住人）；禁用后账号保留、审计不断。"))

        self.reload()

    # ---------------------------------------------------------------- 数据
    def reload(self):
        keep = self.table.currentRow()
        users = auth.users.all()
        users.sort(key=lambda u: (u.get("role") != "admin", u.get("created", "")))
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for u in users:
            r = self.table.rowCount()
            self.table.insertRow(r)
            role = auth.ROLE_NAME.get(u.get("role"), u.get("role", ""))
            dis = bool(u.get("disabled"))
            n = audit.count_user(u["name"])
            C.fill_row(self.table, r, (
                u["name"], role, "已禁用" if dis else "正常",
                u.get("created", "—"), u.get("last_login") or "—", n),
                colors={0: theme.TXT,
                        1: theme.TXT2,
                        2: theme.BAD if dis else theme.OK,
                        3: theme.TXT3, 4: theme.TXT3, 5: theme.TXT3},
                bolds={0: True})
            self.table.item(r, 0).setData(Qt.UserRole, u["name"])
            if u["name"] == auth.session.name:
                self.table.item(r, 0).setText(u["name"] + "（当前）")
        self.table.blockSignals(False)
        self.lb_count.setText("共 %d 个账号" % len(users))
        if 0 <= keep < self.table.rowCount():
            self.table.selectRow(keep)
        self._sync()

    def _current(self):
        r = C.selected_row(self.table)
        if r < 0:
            return None
        name = self.table.item(r, 0).data(Qt.UserRole)
        return auth.users.get(name)

    def _sync(self):
        u = self._current()
        me = u and u["name"] == auth.session.name
        has = u is not None
        self.btn_role.setEnabled(has and not me)
        self.btn_pwd.setEnabled(has)
        self.btn_toggle.setEnabled(has and not me)
        self.btn_del.setEnabled(has and not me)
        if has:
            self.btn_toggle.setText("启用" if u.get("disabled") else "禁用")

    # ---------------------------------------------------------------- 保护
    def _admins_left(self):
        return sum(1 for u in auth.users.all()
                   if u.get("role") == "admin" and not u.get("disabled"))

    def _guard_admin(self, u, action):
        """最后一个启用的管理员不能被禁用/删除/降级。"""
        if u.get("role") != "admin" or u.get("disabled"):
            return True
        if self._admins_left() > 1:
            return True
        C.tell(self, "不能%s" % action,
               "「%s」是当前唯一可用的管理员。\n先建一个管理员账号或把别人提升为"
               "管理员，再来%s —— 否则没人能进系统管理。" % (u["name"], action),
               kind="warn")
        return False

    # ---------------------------------------------------------------- 动作
    def _on_new(self):
        name = QLineEdit()
        name.setFixedHeight(30)
        name.setMaxLength(20)
        name.setPlaceholderText("2~20 字，不含空格与 \\ / : * ?")
        role = QComboBox()
        role.setFixedHeight(30)
        for r in auth.ROLES:
            role.addItem(auth.ROLE_NAME[r], r)
        id6 = QLineEdit()
        id6.setFixedHeight(30)
        id6.setMaxLength(6)
        id6.setPlaceholderText("6 位数字（忘记密码时验证身份）")
        pwd = QLineEdit()
        pwd.setEchoMode(QLineEdit.Password)
        pwd.setFixedHeight(30)
        pwd2 = QLineEdit()
        pwd2.setEchoMode(QLineEdit.Password)
        pwd2.setFixedHeight(30)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        for lb, w in (("用户名", name), ("角色权限", role),
                      ("身份证后 6 位", id6), ("初始密码", pwd),
                      ("确认密码", pwd2)):
            v.addWidget(C.field(lb, w))
        sec = auth.settings.get("security") or {}
        v.addWidget(C.hint("密码至少 %d 位%s" % (
            int(sec.get("pwd_min", 6)),
            "、含数字" if sec.get("need_digit", True) else "")))

        state = {}

        def validate(_p):
            n = name.text().strip()
            if pwd.text() != pwd2.text():
                return "两次输入的密码不一致"
            try:
                state["user"] = auth.users.add(n, role.currentData(),
                                               id6.text().strip(), pwd.text())
            except ValueError as e:
                return str(e)
            return ""

        d = C.CardDialog(self, "新建用户",
                         "建好的账号可以立刻用来登入；初始密码请当面告知本人。",
                         ok_text="创建", extra=form, validator=validate)
        if d.exec_() != d.Accepted:
            return
        u = state["user"]
        audit.write("创建用户", target=u["name"],
                    detail="角色：%s" % auth.ROLE_NAME.get(u["role"], u["role"]))
        self.reload()

    def _on_role(self):
        u = self._current()
        if not u:
            return
        if u["name"] == auth.session.name:
            C.tell(self, "不能修改自己的角色",
                   "改角色等于改自己的权限。请让另一位管理员来调整。", kind="warn")
            return
        if not self._guard_admin(u, "降级"):
            return
        cb = QComboBox()
        cb.setFixedHeight(30)
        for r in auth.ROLES:
            cb.addItem("%s　%s" % (auth.ROLE_NAME[r], auth.ROLE_DESC[r]), r)
        cb.setCurrentIndex(max(0, [r for r in auth.ROLES].index(u["role"])
                               if u["role"] in auth.ROLES else 0))
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        v.addWidget(C.field("新角色", cb))

        def validate(_p):
            new = cb.currentData()
            if new == u["role"]:
                return "角色没有变化"
            if u["role"] == "admin" and new != "admin" and self._admins_left() <= 1:
                return "这是最后一个启用的管理员，不能降级"
            auth.users.set_role(u["name"], new)
            return ""

        d = C.CardDialog(self, "修改角色",
                         "「%s」当前是%s。角色改变后权限立即生效。" %
                         (u["name"], auth.ROLE_NAME.get(u["role"])),
                         ok_text="保存", extra=form, validator=validate)
        if d.exec_() != d.Accepted:
            return
        new = cb.currentData()
        audit.write("修改角色", target=u["name"],
                    detail="%s → %s" % (auth.ROLE_NAME.get(u["role"]),
                                        auth.ROLE_NAME.get(new)))
        self.reload()

    def _on_reset(self):
        u = self._current()
        if not u:
            return
        pwd = QLineEdit()
        pwd.setEchoMode(QLineEdit.Password)
        pwd.setFixedHeight(30)
        pwd2 = QLineEdit()
        pwd2.setEchoMode(QLineEdit.Password)
        pwd2.setFixedHeight(30)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        v.addWidget(C.field("新密码", pwd))
        v.addWidget(C.field("确认密码", pwd2))
        v.addWidget(C.hint("重置后旧密码立即失效；本人也可以在登入页用"
                           "「忘记密码」自助重置。"))

        def validate(_p):
            if pwd.text() != pwd2.text():
                return "两次输入的密码不一致"
            msg = auth.password_error(pwd.text())
            if msg:
                return msg
            try:
                auth.users.set_password(u["name"], pwd.text())
            except ValueError as e:
                return str(e)
            return ""

        d = C.CardDialog(self, "重置密码",
                         "把「%s」的密码重置为新密码。" % u["name"],
                         ok_text="重置", extra=form, validator=validate)
        if d.exec_() != d.Accepted:
            return
        audit.write("重置密码", target=u["name"], detail="管理员重置")
        self.reload()
        C.tell(self, "已重置", "「%s」的密码已更新，请用新密码登入。" % u["name"])

    def _on_toggle(self):
        u = self._current()
        if not u:
            return
        dis = bool(u.get("disabled"))
        if dis:
            ok = C.ask(self, "启用账号", "启用「%s」？启用后可以正常登入。" % u["name"],
                       ok_text="启用")
        else:
            if not self._guard_admin(u, "禁用"):
                return
            ok = C.ask(self, "禁用账号",
                       "禁用「%s」后该账号无法登入，但历史审计与数据都保留。\n"
                       "需要恢复时在同一位置点「启用」。" % u["name"],
                       ok_text="禁用", danger=True)
        if not ok:
            return
        auth.users.set_disabled(u["name"], not dis)
        audit.write("启用账号" if dis else "禁用账号", target=u["name"],
                    detail="由管理员操作")
        self.reload()

    def _on_delete(self):
        u = self._current()
        if not u:
            return
        n = audit.count_user(u["name"])
        if n > 0:
            audit.write("删除用户被拒绝", target=u["name"], result="FAIL",
                        detail="该账号已有 %d 条审计记录，按规则只能禁用" % n)
            C.tell(self, "不能删除",
                   "「%s」在审计日志里有 %d 条记录。删除后追溯会断（记录里那个人"
                   "查无此人），所以按规则只能禁用。\n\n点【禁用】即可达到目的，"
                   "历史记录照旧保留。" % (u["name"], n), kind="warn")
            return
        if not self._guard_admin(u, "删除"):
            return
        if not C.ask(self, "删除账号",
                     "「%s」没有任何审计记录，可以删除。\n删除后无法恢复。" % u["name"],
                     ok_text="删除", danger=True):
            return
        auth.users.remove(u["name"])
        audit.write("删除用户", target=u["name"], detail="无审计关联，按规则删除")
        self.reload()
