# -*- coding: utf-8 -*-
"""系统管理 → 角色权限。

一张"角色 × 功能点"的勾选矩阵。管理员改完点保存，**立即生效**（session.can
每次都现查这份矩阵，不缓存），不需要重启，也不需要重新登入。

两个提示做得比较重（会弹确认）
------------------------------
* 管理员角色如果取消了 `sys.role` 或 `sys.user`，之后**没人能再改权限**，
  也**没人能再建号**。现场只有一个人的时候，这等于把自己锁死。
* 保存前会把"改了哪几项"列出来，避免手滑勾错一条自己没发现。

矩阵存在 data/auth/roles.json，只存角色 → 功能点 → bool，不含任何账号信息。
"""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel,
                             QScrollArea, QVBoxLayout, QWidget)

from .. import auth, theme
from ..audit import audit
from ..widgets import _font
from . import sys_common as C


class RolesTab(QWidget):
    perm_changed = pyqtSignal()

    def __init__(self, parent=None):
        super(RolesTab, self).__init__(parent)
        self._role = "operator"
        self._boxes = {}          # point -> QCheckBox
        self._matrix = auth.roles.matrix()
        self._saved = auth.roles.matrix()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self.cb_role = QComboBox()
        self.cb_role.setFixedHeight(28)
        self.cb_role.setFixedWidth(132)
        for r in auth.ROLES:
            self.cb_role.addItem(auth.ROLE_NAME[r], r)
        self.cb_role.currentIndexChanged.connect(self._on_role)
        self.lb_desc = C.hint("", size=9)
        # 不换行：这一行跟"角色"下拉同一排，换行会顶到下面的白卡上
        self.lb_desc.setWordWrap(False)
        self.lb_desc.setMinimumWidth(250)
        self.lb_dirty = C.hint("", obj="warnTxt", size=9)
        self.btn_reset = C.btn("恢复默认", width=80)
        self.btn_save = C.btn("保存权限", primary=True, width=90)
        self.btn_reset.clicked.connect(self._on_reset)
        self.btn_save.clicked.connect(self._on_save)
        lb_role = QLabel("角色")
        lb_role.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        lb_role.setStyleSheet("color:%s;" % theme.TXT3)
        root.addLayout(C.row(lb_role, self.cb_role, self.lb_desc, None,
                             self.lb_dirty, self.btn_reset, self.btn_save))

        card = C.Card(self, pad=(12, 8, 12, 10))
        self.sa = QScrollArea()
        self.sa.setWidgetResizable(True)
        self.sa.setFrameShape(QFrame.NoFrame)
        self.sa.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self.host = QWidget()
        self.hv = QVBoxLayout(self.host)
        self.hv.setContentsMargins(2, 2, 6, 2)
        self.hv.setSpacing(0)
        self.sa.setWidget(self.host)
        card.body.addWidget(self.sa, 1)
        card.body.addWidget(C.hint(
            "权限变更即时生效，下次点击就用新权限判定；越权操作会被拒绝并写入审计。"
            "「恢复默认」只改当前选中的这个角色。"))
        root.addWidget(card, 1)

        self._build_rows()
        self.cb_role.setCurrentIndex(2)      # 默认落在操作员：最常看的是"它被限了什么"
        self._on_role()

    # ---------------------------------------------------------------- 行
    def _build_rows(self):
        groups = []
        for p in auth.POINTS:
            if not groups or groups[-1][0] != p[1]:
                groups.append((p[1], []))
            groups[-1][1].append(p)

        for gi, (group, points) in enumerate(groups):
            head = QHBoxLayout()
            head.setContentsMargins(0, 8 if gi else 0, 0, 4)
            head.setSpacing(6)
            lb = QLabel(group)
            lb.setFont(_font(theme.F_BODY, 11, bold=True, spacing=0.6))
            lb.setStyleSheet("color:%s;" % theme.TITLE)
            head.addWidget(lb)
            line = QFrame()
            line.setObjectName("hline")
            line.setFixedHeight(1)
            head.addWidget(line, 1)
            self.hv.addLayout(head)

            for key, _mod, desc in points:
                row = QHBoxLayout()
                row.setContentsMargins(2, 3, 2, 3)
                row.setSpacing(8)
                cb = QCheckBox(key)
                cb.setFont(_font(theme.F_MONO, 10, spacing=0.2))
                cb.setFixedWidth(96)
                cb.stateChanged.connect(self._on_check)
                self._boxes[key] = cb
                row.addWidget(cb)
                d = QLabel(desc)
                d.setFont(_font(theme.F_BODY, 10, spacing=0.2))
                d.setStyleSheet("color:%s;" % theme.TXT2)
                row.addWidget(d, 1)
                self.hv.addLayout(row)
        self.hv.addStretch(1)

    # ---------------------------------------------------------------- 状态
    def _on_role(self, *_a):
        self._role = self.cb_role.currentData() or "operator"
        self.lb_desc.setText(auth.ROLE_DESC.get(self._role, ""))
        self.refresh()

    def refresh(self):
        values = self._matrix.get(self._role) or {}
        for key, cb in self._boxes.items():
            cb.blockSignals(True)
            cb.setChecked(bool(values.get(key)))
            cb.blockSignals(False)
        self._sync_dirty()

    def _on_check(self, *_a):
        for key, cb in self._boxes.items():
            self._matrix.setdefault(self._role, {})[key] = cb.isChecked()
        self._sync_dirty()

    def _dirty(self):
        a = self._matrix.get(self._role) or {}
        b = self._saved.get(self._role) or {}
        return [k for k in auth.POINT_KEYS if bool(a.get(k)) != bool(b.get(k))]

    def _sync_dirty(self):
        n = len(self._dirty())
        self.lb_dirty.setText("有 %d 项改动未保存" % n if n else "")
        self.btn_save.setEnabled(bool(n))

    # ---------------------------------------------------------------- 动作
    def _on_reset(self):
        role = self._role
        if not C.ask(self, "恢复默认权限",
                     "把「%s」的权限恢复成出厂默认？\n只影响这一个角色，其他角色不动。"
                     "点【保存权限】后才真正生效。" % auth.ROLE_NAME[role]):
            return
        self._matrix[role] = dict(auth.DEFAULT_MATRIX[role])
        self.refresh()

    def _on_save(self):
        role = self._role
        changes = self._dirty()
        if not changes:
            return
        lines = []
        for k in changes:
            now = bool((self._matrix.get(role) or {}).get(k))
            lines.append("· %s %s（%s）" % ("开启" if now else "关闭",
                                            k, auth.POINT_DESC.get(k, "")))
        text = "「%s」有 %d 项改动：\n%s" % (auth.ROLE_NAME[role], len(changes),
                                            "\n".join(lines[:10]))
        if len(lines) > 10:
            text += "\n…"
        if role == "admin":
            m = self._matrix.get("admin") or {}
            if not m.get("sys.role") or not m.get("sys.user"):
                if not C.ask(self, "管理员将无法再管理权限",
                             text + "\n\n注意：这次改动会让「管理员」失去"
                             "用户管理/权限配置能力，之后没人能改回来。\n"
                             "确定保存？", ok_text="仍然保存", danger=True):
                    return
        if not C.ask(self, "保存权限", text + "\n\n保存后立即生效。", ok_text="保存"):
            return

        for k in auth.POINT_KEYS:
            auth.roles.set_point(role, k, bool((self._matrix.get(role) or {})
                                               .get(k)))
        detail = "、".join("%s=%s" % (k, "开" if (self._matrix.get(role) or {})
                                     .get(k) else "关") for k in changes)
        auth.roles.save(user=auth.session.name,
                        detail="%s：%s" % (auth.ROLE_NAME[role], detail[:400]))
        self._saved = auth.roles.matrix()
        self._matrix = auth.roles.matrix()
        self.refresh()
        self.perm_changed.emit()
        if not auth.session.can("sys.role"):
            C.tell(self, "权限已保存",
                   "当前账号所属角色已没有「权限配置」权限，这个页签将不再显示。",
                   kind="warn")
