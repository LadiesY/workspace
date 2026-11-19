# -*- coding: utf-8 -*-
"""系统管理 → 备份恢复。

列表里每一行都是一个 FULL-*.zip。能做的动作：立即备份、校验、恢复、删除。

为什么"校验"要单独做一个按钮
---------------------------
包里带 MANIFEST.json，记着每个文件的大小和 sha256。恢复前必过这一关——
一个能正常解开、内容却被换过的包，光看"能打开"是发现不了的。这里的校验
就是拿清单逐个文件比对，任一处不符会指名道姓说哪个文件。

恢复的边界
----------
* 需要 `sys.restore` 权限（默认只有管理员）+ 重输当前账号密码；
* 恢复前会**自动先备一份**当前状态（备注写"恢复前自动"），做错了能退回去；
* **审计目录不参与恢复** —— 否则"恢复到三天前"就等于把这三天的记录抹了；
* 恢复完当前账号可能已经对不上（备份里的账号体系更旧），所以会**自动登出**，
  要求重新登入。这不是偷懒，是避免拿着旧会话操作新数据。
"""

import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QVBoxLayout, QWidget

from .. import auth, theme
from .. import backup as B
from ..audit import audit
from . import sys_common as C

HEADERS = ("备份文件", "创建时间", "大小", "内容", "备注")
WIDTHS = (196, 136, 78, 96)


class BackupTab(QWidget):
    def __init__(self, parent=None):
        super(BackupTab, self).__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self.btn_new = C.btn("立即备份", primary=True, width=88)
        self.btn_check = C.btn("校验选中", width=86)
        self.btn_restore = C.btn("恢复选中", width=86, danger=True)
        self.btn_del = C.btn("删除选中", width=86)
        self.btn_new.clicked.connect(self._on_new)
        self.btn_check.clicked.connect(self._on_check)
        self.btn_restore.clicked.connect(self._on_restore)
        self.btn_del.clicked.connect(self._on_delete)
        self.lb_auto = C.hint("", size=9)
        self.lb_auto.setWordWrap(False)      # 换行会把表头顶下去
        root.addLayout(C.row(self.btn_new, self.btn_check, self.btn_restore,
                             self.btn_del, None, self.lb_auto))

        card = C.Card(self, pad=(10, 8, 12, 10))
        self.table = C.make_table(HEADERS, WIDTHS)
        self.table.itemSelectionChanged.connect(self._sync)
        card.body.addWidget(self.table, 1)
        self.lb_state = C.hint("", size=9)
        card.body.addWidget(self.lb_state)
        root.addWidget(card, 1)
        # 说明放卡片外面：塞在卡片里会把卡片撑高，底边被描边切一刀很难看
        root.addWidget(C.hint(
            "命名 FULL-YYYYMMDD-HHMM（用户规范，按名字就能排时间）。"
            "备份含业务数据与审计；恢复不覆盖 data\\audit，且自动先备一份当前状态。"))

        if not auth.session.can("sys.restore"):
            self.btn_restore.setEnabled(False)
            self.btn_restore.setToolTip("恢复需要管理员权限（sys.restore）")

        self.reload()

    # ---------------------------------------------------------------- 数据
    def reload(self):
        keep = self.table.currentRow()
        items = B.list_backups()
        self.table.setRowCount(0)
        for it in items:
            man = B.read_manifest(it["path"]) or {}
            files = man.get("files") or {}
            note = man.get("note") or ""
            if man.get("user") and man.get("user") not in note:
                note = ("%s · %s" % (man.get("user"), note)).strip(" ·")
            r = self.table.rowCount()
            self.table.insertRow(r)
            C.fill_row(self.table, r, (
                it["name"], man.get("created") or it["mtime"],
                C.fmt_size(it["size"]), "%d 个文件" % len(files), note or "—"),
                colors={0: theme.TXT, 1: theme.TXT2, 2: theme.TXT2,
                        3: theme.TXT2, 4: theme.TXT3},
                bolds={0: True}, monos={0: True, 1: True})
            self.table.item(r, 0).setData(Qt.UserRole, it["path"])
        if 0 <= keep < self.table.rowCount():
            self.table.selectRow(keep)
        self._sync_auto(items)

    def _sync_auto(self, items):
        s = auth.settings.get("backup") or {}
        on = s.get("auto", True)
        # 只留「策略」三件事。上次备份时间、包个数都在表里第一行/行数里，
        # 再说一遍会把 1100px 窗口的这行顶出右边界（实测溢出 4px）。
        txt = "自动备份 %s ｜ 每日 %s ｜ 保留 %d日+%d周" % (
            "开" if on else "关", s.get("time", "02:00"),
            int(s.get("keep_daily", 7)), int(s.get("keep_weekly", 4)))
        self.lb_auto.setText(txt)
        self.lb_auto.setObjectName("okTxt" if on else "warnTxt")
        self.lb_auto.style().unpolish(self.lb_auto)
        self.lb_auto.style().polish(self.lb_auto)

    def _current_path(self):
        r = C.selected_row(self.table)
        if r < 0:
            return None
        return self.table.item(r, 0).data(Qt.UserRole)

    def _sync(self):
        has = self._current_path() is not None
        self.btn_check.setEnabled(has)
        self.btn_del.setEnabled(has)
        self.btn_restore.setEnabled(has and auth.session.can("sys.restore"))

    # ---------------------------------------------------------------- 动作
    def _on_new(self):
        if not C.ask(self, "立即备份",
                     "把 data\\ 下的业务数据与审计打成一个 FULL 备份包？\n"
                     "大会话目录（相机照片）不在 data 里，备份很快。"):
            return
        self.btn_new.setEnabled(False)
        try:
            res = B.create(user=auth.session.name, note="手动备份")
            audit.write("生成备份", target=res["name"],
                        detail="%d 个文件，%s，用时 %.1f s" % (
                            res["files"], C.fmt_size(res["size"]),
                            res["seconds"]))
            removed = B.prune()
        except Exception as e:
            self.btn_new.setEnabled(True)
            C.tell(self, "备份失败", "生成备份包时出错：%s" % e, kind="warn")
            return
        self.btn_new.setEnabled(True)
        self.reload()
        msg = "已生成 %s\n%d 个文件，%s" % (res["name"], res["files"],
                                          C.fmt_size(res["size"]))
        if removed:
            msg += "\n按保留策略清理了 %d 个旧备份。" % len(removed)
        C.tell(self, "备份完成", msg)

    def _on_check(self):
        path = self._current_path()
        if not path:
            return
        ok, msg, man = B.verify(path)
        audit.write("校验备份", target=os.path.basename(path),
                    result="OK" if ok else "FAIL", detail=msg)
        self.lb_state.setText(("✔ " if ok else "✘ ") + msg)
        self.lb_state.setObjectName("okTxt" if ok else "badTxt")
        self.lb_state.style().unpolish(self.lb_state)
        self.lb_state.style().polish(self.lb_state)
        C.tell(self, "校验通过" if ok else "校验失败", msg,
               kind="info" if ok else "warn")

    def _on_restore(self):
        path = self._current_path()
        if not path:
            return
        name = os.path.basename(path)
        ok, msg, man = B.verify(path)
        if not ok:
            audit.write("恢复备份被拒绝", target=name, result="FAIL",
                        detail="校验未通过：" + msg)
            C.tell(self, "不能恢复", "这个包没通过校验：\n%s" % msg, kind="warn")
            return
        if not C.ask_password(
                self, "恢复备份",
                "将用「%s」覆盖当前 data\\ 下的业务数据。\n"
                "· 恢复前会自动备份当前状态；\n"
                "· 审计日志不会被覆盖；\n"
                "· 恢复后需要重新登入。" % name,
                ok_text="恢复", danger=True):
            return
        ok, msg, n = B.restore(path, user=auth.session.name)
        if not ok:
            C.tell(self, "恢复失败", msg, kind="warn")
            self.reload()
            return
        # 恢复了账号/设置文件，内存里的副本必须重新读，否则界面还拿旧的判断权限
        auth.users = auth.UserStore(auth.USERS_PATH)
        auth.roles = auth.RoleStore(auth.ROLES_PATH)
        auth.settings.reload()
        self.reload()
        auth.session.logout("恢复备份后重新登入")
        C.tell(self, "恢复完成",
               "%s\n\n账号与设置已重新载入，请用密码重新登入。" % msg)

    def _on_delete(self):
        path = self._current_path()
        if not path:
            return
        name = os.path.basename(path)
        if not C.ask(self, "删除备份",
                     "删除「%s」？\n只删这一个备份包，不影响当前数据。" % name,
                     ok_text="删除", danger=True):
            return
        try:
            os.remove(path)
        except (IOError, OSError) as e:
            C.tell(self, "删除失败", "%s" % e, kind="warn")
            return
        audit.write("删除备份", target=name, detail="手动删除备份包")
        self.reload()
