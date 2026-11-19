# -*- coding: utf-8 -*-
"""系统管理 → 审计日志。

界面是只读的：**没有删除、没有清空、没有编辑**。这不是"还没来得及做"，
是这一块的设计要求 —— 审计一旦能改就不叫审计了（见 app/audit.py 顶部）。

能做的三件事：
* 按关键字 / 结果筛，看"谁在什么时候动了什么"；
* **完整性校验**：从头重算哈希链，指出第一处断点在第几行、哪种断法；
* 导出 CSV（带 BOM，Excel 直接双击不打卷）。导出本身也记一条审计 ——
  审计数据离开这台机器，同样是值得记的事。
"""

import csv
import os
from datetime import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QComboBox, QFileDialog, QLineEdit, QVBoxLayout,
                             QWidget)

from .. import theme
from ..audit import audit
from ..devices.base import DATA_DIR
from . import sys_common as C

HEADERS = ("时间", "用户", "角色", "动作", "对象", "结果", "说明")
WIDTHS = (132, 88, 62, 104, 108, 52)


class AuditTab(QWidget):
    def __init__(self, parent=None):
        super(AuditTab, self).__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self.ed_kw = QLineEdit()
        self.ed_kw.setFixedHeight(26)
        self.ed_kw.setFixedWidth(196)
        self.ed_kw.setPlaceholderText("搜用户名 / 动作 / 对象 / 说明")
        self.ed_kw.returnPressed.connect(self.reload)
        self.cb_res = QComboBox()
        self.cb_res.setFixedHeight(26)
        self.cb_res.setFixedWidth(88)
        for text, val in (("全部结果", ""), ("成功", "OK"), ("失败", "FAIL")):
            self.cb_res.addItem(text, val)
        self.cb_res.currentIndexChanged.connect(self.reload)
        self.btn_find = C.btn("查询", width=58)
        self.btn_find.clicked.connect(self.reload)
        self.btn_check = C.btn("完整性校验", width=92)
        self.btn_check.clicked.connect(self._on_verify)
        self.btn_export = C.btn("导出 CSV", width=80)
        self.btn_export.clicked.connect(self._on_export)
        root.addLayout(C.row(self.ed_kw, self.cb_res, self.btn_find, None,
                             self.btn_check, self.btn_export))

        self.lb_state = C.hint("", size=9)
        self.lb_state.setWordWrap(False)
        self.lb_stat = C.hint("", size=9)
        self.lb_stat.setWordWrap(False)
        root.addLayout(C.row(self.lb_state, None, self.lb_stat))

        card = C.Card(self, pad=(10, 8, 12, 10))
        self.table = C.make_table(HEADERS, WIDTHS)
        self.table.doubleClicked.connect(lambda _i: self._on_detail())
        card.body.addWidget(self.table, 1)
        root.addWidget(card, 1)
        root.addWidget(C.hint(
            "只追加、不可删除：每条记录带前一条的哈希，改一个字或删一行都会被"
            "「完整性校验」发现。审计与业务数据分开存放（data\\audit）。"))

        self.reload()

    # ---------------------------------------------------------------- 数据
    def _entries(self):
        return audit.entries(keyword=self.ed_kw.text().strip(),
                             result=self.cb_res.currentData() or "",
                             limit=600)

    def reload(self):
        recs = self._entries()
        self.table.setRowCount(0)
        for rec in recs:
            r = self.table.rowCount()
            self.table.insertRow(r)
            ok = rec.get("result") == "OK"
            C.fill_row(self.table, r, (
                rec.get("ts", ""), rec.get("user", ""), rec.get("role", ""),
                rec.get("action", ""), rec.get("target", "") or "—",
                "成功" if ok else "失败", rec.get("detail", "")),
                colors={0: theme.TXT3, 1: theme.TXT, 2: theme.TXT3,
                        3: theme.TXT, 4: theme.TXT2,
                        5: theme.OK if ok else theme.BAD, 6: theme.TXT2},
                bolds={3: True}, monos={0: True, 2: True, 5: True})
        st = audit.stats()
        self.lb_stat.setText("显示 %d 条 / 共 %d 条 ｜ 首条 %s ｜ 最近 %s"
                             % (len(recs), st["count"],
                                st["first"] or "—", st["last"] or "—"))

    # ---------------------------------------------------------------- 动作
    def _on_verify(self):
        res = audit.verify()
        if res["ok"]:
            self.lb_state.setObjectName("okTxt")
            text = "完整性校验通过：%d 条记录的哈希链首尾相连，没有断点" % res["total"]
        else:
            self.lb_state.setObjectName("badTxt")
            text = "完整性校验失败：第 %d 条起断链 —— %s" % (res["bad"], res["at"])
        self.lb_state.style().unpolish(self.lb_state)
        self.lb_state.style().polish(self.lb_state)
        self.lb_state.setText(text)
        audit.write("校验审计完整性", target="审计日志",
                    result="OK" if res["ok"] else "FAIL",
                    detail=text)

    def _on_detail(self):
        r = C.selected_row(self.table)
        if r < 0:
            return
        recs = self._entries()
        if r >= len(recs):
            return
        rec = recs[r]
        lines = []
        for k, name in (("seq", "序号"), ("ts", "时间"), ("user", "用户"),
                        ("role", "角色"), ("action", "动作"), ("target", "对象"),
                        ("result", "结果"), ("detail", "说明"),
                        ("prev", "前一条哈希"), ("h", "本条哈希")):
            lines.append("%s：%s" % (name, rec.get(k, "")))
        C.tell(self, "审计记录详情", "\n".join(lines), width=520)

    def _on_export(self):
        recs = self._entries()
        if not recs:
            C.tell(self, "没有可导出的记录", "当前筛选条件下是空的，换个条件再试。")
            return
        default = os.path.join(DATA_DIR,
                               "审计日志_%s.csv" % datetime.now().strftime("%Y%m%d_%H%M"))
        path, _f = QFileDialog.getSaveFileName(self, "导出审计日志", default,
                                               "CSV 文件 (*.csv)")
        if not path:
            return
        try:
            # utf-8-sig：Excel 双击打开中文不乱码（不带 BOM 会变问号）
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(HEADERS + ("序号", "哈希"))
                for rec in reversed(recs):        # 导出按时间正序，便于阅读
                    w.writerow([rec.get("ts", ""), rec.get("user", ""),
                                rec.get("role", ""), rec.get("action", ""),
                                rec.get("target", ""), rec.get("result", ""),
                                rec.get("detail", ""), rec.get("seq", ""),
                                rec.get("h", "")])
        except (IOError, OSError) as e:
            C.tell(self, "导出失败", "写文件出错：%s" % e, kind="warn")
            return
        audit.write("导出审计日志", target=os.path.basename(path),
                    detail="%d 条；筛选：%s" % (len(recs),
                                              self.ed_kw.text().strip() or "无"))
        C.tell(self, "已导出", "导出 %d 条到：\n%s" % (len(recs), path))
