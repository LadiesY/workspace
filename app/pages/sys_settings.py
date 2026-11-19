# -*- coding: utf-8 -*-
"""系统管理 → 系统设置。

分四块：安全策略、备份策略、审计状态（只读）、版本信息。改完点保存，写一条
审计（"修改设置"），并把改动项列出来 —— 密码策略被悄悄放松过，是事后最常
被追问的一件事，留痕比弹窗管用。
"""

import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit,
                             QSpinBox, QVBoxLayout, QWidget)

from .. import auth, theme
from ..audit import audit
from ..devices.base import DATA_DIR
from ..widgets import _font
from . import sys_common as C


def _spin(lo, hi, val, suffix=""):
    sp = QSpinBox()
    sp.setRange(lo, hi)
    sp.setValue(int(val))
    sp.setFixedHeight(26)
    sp.setFixedWidth(78)
    sp.setButtonSymbols(QSpinBox.NoButtons)
    if suffix:
        sp.setSuffix(suffix)
    return sp


class SettingsTab(QWidget):
    def __init__(self, parent=None):
        super(SettingsTab, self).__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        cols = QHBoxLayout()
        cols.setContentsMargins(0, 0, 0, 0)
        cols.setSpacing(8)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)

        # ---------------- 安全策略
        c1 = C.Card(self, pad=(14, 10, 14, 12))
        c1.body.addWidget(C.sec_title("安全策略"))
        c1.body.addWidget(C.hint("改完对下一次登入/注册/重置立即生效。"))
        self.sp_min = _spin(4, 20, 6, " 位")
        c1.body.addLayout(self._row("密码最短位数", self.sp_min))
        self.cb_digit = QCheckBox("密码必须包含数字")
        c1.body.addWidget(self.cb_digit)
        self.cb_letter = QCheckBox("密码必须包含字母")
        c1.body.addWidget(self.cb_letter)
        self.sp_fail = _spin(3, 10, 5, " 次")
        c1.body.addLayout(self._row("连续失败几次锁定", self.sp_fail))
        self.sp_lock = _spin(1, 60, 5, " 分钟")
        c1.body.addLayout(self._row("锁定时长", self.sp_lock))
        c1.body.addStretch(1)

        # ---------------- 备份策略
        c2 = C.Card(self, pad=(14, 10, 14, 12))
        c2.body.addWidget(C.sec_title("备份策略"))
        self.cb_auto = QCheckBox("每日自动备份（工具开着的时候到点执行）")
        c2.body.addWidget(self.cb_auto)
        self.ed_time = QLineEdit("02:00")
        self.ed_time.setFixedHeight(26)
        self.ed_time.setFixedWidth(78)
        self.ed_time.setPlaceholderText("HH:MM")
        c2.body.addLayout(self._row("每天几点备份", self.ed_time))
        self.sp_daily = _spin(1, 30, 7, " 份")
        c2.body.addLayout(self._row("保留最近日备", self.sp_daily))
        self.sp_weekly = _spin(0, 12, 4, " 份")
        c2.body.addLayout(self._row("保留周备", self.sp_weekly))
        c2.body.addWidget(C.hint("备份包在 data\\backup，恢复在「备份恢复」页。"))

        # ---------------- 审计状态（只读）
        # 这几行是数据，不许换行：一换行行数就随宽度变，卡片高度算不准。
        # 拆成两行是因为「首条 + 最近」两个时间戳加在一起太长，窄窗口会撞边。
        c3 = C.Card(self, pad=(14, 10, 14, 12))
        c3.body.addWidget(C.sec_title("审计与数据"))
        self.lb_audit = C.hint("", size=9)
        self.lb_audit.setWordWrap(False)
        c3.body.addWidget(self.lb_audit)
        self.lb_span = C.hint("", size=9)
        self.lb_span.setWordWrap(False)
        c3.body.addWidget(self.lb_span)
        self.lb_path = C.hint("", obj="monoTxt", size=9)
        self.lb_path.setText("数据目录：%s" % DATA_DIR)
        self.lb_path.setWordWrap(False)
        self.lb_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        c3.body.addWidget(self.lb_path)
        c3.body.addWidget(C.hint("审计日志只追加、不可删除，目录与业务数据分开。"))
        c3.body.addStretch(1)

        # ---------------- 版本信息
        c4 = C.Card(self, pad=(14, 10, 14, 12))
        c4.body.addWidget(C.sec_title("版本信息"))
        for k, v in (("软件版本", theme.APP_VER),
                     ("账户体系", "本地账户（离线，无网络）"),
                     ("会话策略", "关闭工具即退出，下次需重新登入")):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            lk = QLabel(k)
            lk.setFont(_font(theme.F_BODY, 9, spacing=0.2))
            lk.setStyleSheet("color:%s;" % theme.TXT3)
            lv = QLabel(v)
            lv.setFont(_font(theme.F_BODY, 9, spacing=0.2))
            lv.setStyleSheet("color:%s;" % theme.TXT)
            row.addWidget(lk)
            row.addStretch(1)
            row.addWidget(lv)
            c4.body.addLayout(row)
        c4.body.addStretch(1)

        # 四张卡都按内容高度收（不再按 3:2 / 1:1 去抢高度）。按比例分的话，
        # 内容少的那张卡片内部会空出一大块，看着像没加载完。剩下的高度留白。
        left.addWidget(c1, 0)
        left.addWidget(c2, 0)
        left.addStretch(1)
        right.addWidget(c3, 0)
        right.addWidget(c4, 0)
        right.addStretch(1)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        root.addLayout(cols, 1)

        self.lb_msg = C.hint("", size=9)
        self.lb_msg.setWordWrap(False)      # 换行会在底栏上方挤成两行，很难看
        self.btn_save = C.btn("保存设置", primary=True, width=96)
        self.btn_save.clicked.connect(self._on_save)
        root.addLayout(C.row(self.lb_msg, None, self.btn_save))

        self._cards = (c1, c2, c3, c4)
        self.reload()

    def _fit_cards(self):
        """把卡片的"下限高度"顶到它自己内容需要的高度。

        卡里的说明文字是 reload() 之后才填进去的，外层布局按建卡时的空标签
        算过一次高度，之后不再跟着长 —— 结果右列两张卡各矮 30 多 px，最后一行
        直接压在底边框上。setMinimumHeight 是这里唯一稳的办法：布局必须服从
        最小高度，而 sizeHint 只是"建议"，被压缩时不吭声。
        """
        for c in self._cards:
            # 两个 sizeHint 取大的：卡里有嵌套 QHBoxLayout 行时 widget.sizeHint()
            # 会比 layout.sizeHint() 小一截（实测差 17px）。再垫 8px —— 9pt 说明
            # 文字的落地高度比布局估的高，不垫最后一行就压在底边框上。
            h = max(c.sizeHint().height(), c.layout().sizeHint().height()) + 8
            if h > c.minimumHeight():
                c.setMinimumHeight(h)

    # ---------------------------------------------------------------- 行
    def _row(self, label, widget):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        lb = QLabel(label)
        lb.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        lb.setStyleSheet("color:%s;" % theme.TXT2)
        row.addWidget(lb)
        row.addStretch(1)
        row.addWidget(widget)
        return row

    # ---------------------------------------------------------------- 数据
    def reload(self):
        sec = auth.settings.get("security") or {}
        bk = auth.settings.get("backup") or {}
        self.sp_min.setValue(int(sec.get("pwd_min", 6)))
        self.cb_digit.setChecked(bool(sec.get("need_digit", True)))
        self.cb_letter.setChecked(bool(sec.get("need_letter", False)))
        self.sp_fail.setValue(int(sec.get("max_fail", 5)))
        self.sp_lock.setValue(int(sec.get("lock_min", 5)))
        self.cb_auto.setChecked(bool(bk.get("auto", True)))
        self.ed_time.setText(str(bk.get("time", "02:00")))
        self.sp_daily.setValue(int(bk.get("keep_daily", 7)))
        self.sp_weekly.setValue(int(bk.get("keep_weekly", 4)))
        st = audit.stats()
        self.lb_audit.setText("审计记录 %d 条 ｜ 文件 %d 个"
                              % (st["count"], st["files"]))
        self.lb_span.setText("首条 %s ｜ 最近 %s"
                             % (st["first"] or "—", st["last"] or "—"))
        # 上面这些标签建的时候是空串，等 reload 填上字，卡片的 sizeHint 才变高。
        # 但外层布局是按「没字时的最小高度」摆的位置，不给它一次重新激活，卡片
        # 就会矮一截、把最后一行压到边框上（实测矮 17~34px）。
        lay = self.layout()
        if lay is not None:
            lay.invalidate()
            lay.activate()
        self._fit_cards()

    # ---------------------------------------------------------------- 保存
    def _on_save(self):
        t = self.ed_time.text().strip()
        try:
            hh, mm = [int(x) for x in t.split(":")]
            if not (0 <= hh < 24 and 0 <= mm < 60):
                raise ValueError
            t = "%02d:%02d" % (hh, mm)
        except (ValueError, TypeError):
            C.tell(self, "时间格式不对", "备份时刻请按 24 小时制填 HH:MM，例如 02:00。",
                   kind="warn")
            return

        new = {
            "security": {
                "pwd_min": self.sp_min.value(),
                "need_digit": self.cb_digit.isChecked(),
                "need_letter": self.cb_letter.isChecked(),
                "max_fail": self.sp_fail.value(),
                "lock_min": self.sp_lock.value(),
            },
            "backup": {
                "auto": self.cb_auto.isChecked(),
                "time": t,
                "keep_daily": self.sp_daily.value(),
                "keep_weekly": self.sp_weekly.value(),
            },
        }
        old = {"security": auth.settings.get("security"),
               "backup": auth.settings.get("backup")}
        diffs = []
        for sec_name, vals in new.items():
            for k, v in vals.items():
                if (old.get(sec_name) or {}).get(k) != v:
                    diffs.append("%s.%s：%s → %s" % (sec_name, k,
                                                     (old.get(sec_name) or {}).get(k),
                                                     v))
        if not diffs:
            self.lb_msg.setText("没有改动")
            return
        if not C.ask(self, "保存设置", "以下 %d 项将被修改：\n%s\n\n保存后立即生效。"
                     % (len(diffs), "\n".join(diffs[:10])), ok_text="保存"):
            return
        auth.settings.save(new, user=auth.session.name,
                           detail="；".join(diffs)[:400])
        self.reload()
        self.lb_msg.setObjectName("okTxt")
        self.lb_msg.style().unpolish(self.lb_msg)
        self.lb_msg.style().polish(self.lb_msg)
        self.lb_msg.setText("已保存 %d 项设置（已记入审计）" % len(diffs))
