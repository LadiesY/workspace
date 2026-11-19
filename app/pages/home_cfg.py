# -*- coding: utf-8 -*-
"""探针座 · 回零设置对话框。

只管一件事：把「哪根轴朝哪边回零」交到操作员手里。

为什么值得单独做：卡上的回零是**单向**的 —— 只朝 dir 给的方向走，找到 HOME
开关就停；走不到就一直顶，直到超时。方向填反不会有任何报错，现场表现是
「电机一直嗡嗡响、轴顶在尽头」，然后 120 秒过去报一句回零失败。

而六个轴的方向本来就不一样：原工具（TZ\\probe\\probe_config.json）里 X 是负向、
Y/Z/U/V/W 是正向。用一套 dir 套六个轴，X 必然朝反方向跑。所以这里按轴给。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QFrame,
                             QGridLayout, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

from .. import theme
from ..devices import fmc4030 as core
from ..devices import store
from ..widgets import _font

DLG_QSS = """
QDialog { background: #eef4fa; }
QLabel#kTitle { color: #0b2e5c; }
QLabel#kSub   { color: #5b7186; }
QLabel#kHint  { color: #8ba1b5; }
QLabel#kBold  { color: #17497f; }
QFrame#kLine  { background: #e0ebf5; }
QWidget#kCard { background: #ffffff; border: 1px solid #c3d7e9; border-radius: 8px; }
"""

# 数字框右侧要给上下箭头留位：全局 padding 只有 6px，
# 像「100 mm/s²」这种带单位的会长到箭头底下
SPIN_QSS = "QDoubleSpinBox{padding-right:18px;}"


def _hint(text, size=9):
    lb = QLabel(text)
    lb.setObjectName("kHint")
    lb.setFont(_font(theme.F_BODY, size, spacing=0.2))
    return lb


class HomeCfgDialog(QDialog):
    """回零参数：方式 / 速度 / 超时 + 六轴各自的方向。"""

    def __init__(self, home, parent=None):
        super(HomeCfgDialog, self).__init__(parent)
        self.setWindowTitle("探针座 · 回零设置")
        self.setModal(True)
        self.setFixedWidth(486)
        self.setStyleSheet(DLG_QSS + theme.controls_qss() + SPIN_QSS)

        h = dict(home or {})
        dirs = dict(h.get("dirs") or {})
        self._h0 = h                   # 原始参数：acc / offset 原样带回去
        self.home = None               # 点了保存才填：一份新的 home dict

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 14)
        v.setSpacing(9)

        t = QLabel("回零设置")
        t.setObjectName("kTitle")
        t.setFont(_font(theme.F_BODY, 14, bold=True, spacing=0.6))
        v.addWidget(t)
        s = QLabel("回零是单向的：只朝选定方向走，找到 HOME 开关就停。\n"
                   "方向填反不会报错，只会一路顶到尽头——电机嗡嗡响、超时后失败。")
        s.setObjectName("kSub")
        s.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        s.setWordWrap(True)
        v.addWidget(s)
        v.addSpacing(2)

        # ------------------------------------------------ 通用参数
        card = QWidget()
        card.setObjectName("kCard")
        # 顶住最小高度：外层垂直空间不够时 Qt 会压缩它，把里面的输入框裁掉半截
        card.setMinimumHeight(112)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 11, 14, 12)
        cl.setSpacing(8)

        r0 = QHBoxLayout()
        r0.setSpacing(10)
        lb_mode = _hint("回零方式")
        lb_mode.setFixedWidth(52)
        r0.addWidget(lb_mode)
        self.cb_mode = QComboBox()
        self.cb_mode.setFixedHeight(28)
        for code in (1, 2, 3):
            self.cb_mode.addItem("%s（%d）" % (store.HOME_MODE_TEXT[code], code), code)
        idx = self.cb_mode.findData(int(h.get("mode", 1) or 1))
        self.cb_mode.setCurrentIndex(idx if idx >= 0 else 0)
        r0.addWidget(self.cb_mode, 1)
        cl.addLayout(r0)

        r1 = QHBoxLayout()
        r1.setSpacing(10)
        r1.addWidget(self._num_box("快移", h.get("rapid", 2.0), 0.05, 20.0, 0.1,
                                   " mm/s", "找开关时用的速度"))
        r1.addWidget(self._num_box("定位", h.get("locate", 1.0), 0.05, 20.0, 0.1,
                                   " mm/s", "找到开关后二次定位的速度"))
        r1.addWidget(self._num_box("超时", h.get("timeout", 120.0), 5.0, 600.0, 5.0,
                                   " s", "单轴回零最多等多久"))
        cl.addLayout(r1)
        v.addWidget(card)

        # ------------------------------------------------ 逐轴方向
        lb_dir = QLabel("各轴回零方向")
        lb_dir.setObjectName("kBold")
        lb_dir.setFont(_font(theme.F_BODY, 10, bold=True, spacing=0.3))
        v.addWidget(lb_dir)
        v.addWidget(_hint("卡的口径：0 = 负向、1 = 正向。只对探针座生效，改完下次回零就用新值。"))

        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(7)
        self.cb_dir = {}
        for i, name in enumerate(store.PROBE_AXES):
            r, c = divmod(i, 3)
            cell = QHBoxLayout()
            cell.setSpacing(6)
            tag = QLabel(name.upper())
            tag.setObjectName("kBold")
            tag.setFont(_font(theme.F_MONO, 11, bold=True, spacing=0.4))
            tag.setFixedWidth(14)
            cell.addWidget(tag)
            cb = QComboBox()
            cb.setFixedHeight(28)
            cb.addItem("负向", 0)
            cb.addItem("正向", 1)
            try:
                cur = int(dirs.get(name, h.get("dir", 1)))
            except (TypeError, ValueError):
                cur = 1
            cb.setCurrentIndex(1 if cur else 0)
            cb.setToolTip("%s 轴朝哪边找原点" % name.upper())
            cell.addWidget(cb, 1)
            self.cb_dir[name] = cb
            box = QWidget()
            box.setLayout(cell)
            grid.addWidget(box, r, c)
        v.addLayout(grid)

        line = QFrame()
        line.setObjectName("kLine")
        line.setFixedHeight(1)
        v.addWidget(line)

        note = QLabel("建议：改完先只回一根轴试方向，别直接点【全部回零】—— "
                      "六根轴同时跑，反的那根会把时间全耗在顶死上。")
        note.setObjectName("kHint")
        note.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        note.setWordWrap(True)
        v.addWidget(note)

        v.addStretch(1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        b_rst = QPushButton("恢复默认")
        b_rst.setObjectName("act")
        b_rst.setFixedSize(88, 30)
        b_rst.setCursor(Qt.PointingHandCursor)
        b_rst.setToolTip("X 负向、其余五轴正向（原工具的配置）")
        b_rst.clicked.connect(self._reset)
        foot.addWidget(b_rst)
        foot.addStretch(1)
        b_cancel = QPushButton("取消")
        b_cancel.setObjectName("act")
        b_cancel.setFixedSize(80, 30)
        b_cancel.setCursor(Qt.PointingHandCursor)
        b_cancel.clicked.connect(self.reject)
        foot.addWidget(b_cancel)
        b_ok = QPushButton("保存")
        b_ok.setObjectName("primary")
        b_ok.setFixedSize(88, 30)
        b_ok.setCursor(Qt.PointingHandCursor)
        b_ok.setDefault(True)
        b_ok.clicked.connect(self._save)
        foot.addWidget(b_ok)
        v.addLayout(foot)

        # 高度交给布局自己算：写死的话换字体/换 DPI 就会把底部按钮裁掉半截
        self.adjustSize()

    # ---------------------------------------------------------------- 构件
    def _num_box(self, label, val, lo, hi, step, suffix, tip):
        box = QVBoxLayout()
        box.setSpacing(2)
        lb = _hint(label)
        box.addWidget(lb)
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setSingleStep(step)
        try:
            sp.setValue(float(val))
        except (TypeError, ValueError):
            pass
        sp.setSuffix(suffix)
        sp.setDecimals(0 if step >= 1.0 else 2)
        sp.setFixedHeight(28)
        sp.setMinimumWidth(108)
        sp.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        sp.setToolTip(tip)
        box.addWidget(sp)
        if label == "快移":
            self.sp_rapid = sp
        elif label == "定位":
            self.sp_locate = sp
        else:
            self.sp_timeout = sp
        w = QWidget()
        w.setLayout(box)
        return w

    # ---------------------------------------------------------------- 动作
    def _reset(self):
        """恢复成原工具那套：X 负向、其余正向。"""
        d = dict(store.DEFAULTS["probe"]["home"])
        self.cb_mode.setCurrentIndex(self.cb_mode.findData(int(d["mode"])))
        self.sp_rapid.setValue(float(d["rapid"]))
        self.sp_locate.setValue(float(d["locate"]))
        self.sp_timeout.setValue(float(d["timeout"]))
        for name, cb in self.cb_dir.items():
            cb.setCurrentIndex(1 if int(d["dirs"].get(name, 1)) else 0)

    def _save(self):
        dirs = dict((n, 1 if cb.currentIndex() else 0)
                    for n, cb in self.cb_dir.items())
        self.home = {
            "mode": int(self.cb_mode.currentData() or 1),
            "rapid": round(float(self.sp_rapid.value()), 2),
            "locate": round(float(self.sp_locate.value()), 2),
            "acc": float(self._h0.get("acc", 500.0) or 500.0),
            "offset": float(self._h0.get("offset", 0.0) or 0.0),
            "timeout": float(self.sp_timeout.value()),
            "dirs": dirs,
        }
        self.accept()

    def summary(self):
        """一句话：X 负向 / Y 正向 / ……，写日志用。"""
        if not self.home:
            return ""
        return " ".join("%s %s" % (n.upper(), store.HOME_DIR_TEXT[d])
                        for n, d in self.home["dirs"].items())


class MotionHomeDialog(QDialog):
    """三轴控制器 · 回零设置（逐轴：方向 / 速度 / 加减速 / 脱落距离 / 超时）。

    和探针座那份不是同一套口径，所以单独一个类，别硬塞进 HomeCfgDialog：

    * 方向：三轴是 SDK 常量 **1=正限位、2=负限位**；探针是 0=负向、1=正向。
    * 三轴多了「脱落距离」——回零完成后反向退多少 mm 把开关松开。厂家上位机
      默认 5；给 0 的话轴停在开关被压住的位置，限位灯一直亮，状态列会显示
      「负限位」而不是「已回零」。
    * 超时：这里是**我们自己**等待的上限（等不到就发停止）；卡上另有一个
      「回零超时时间」（设备参数），卡先到期会置 HOME_OVERTIME，状态列显示
      「回零超时」。两边都不用改卡参数，谁先到算谁的。
    """

    def __init__(self, home, parent=None):
        super(MotionHomeDialog, self).__init__(parent)
        self.setWindowTitle("三轴控制器 · 回零设置")
        self.setModal(True)
        self.setFixedWidth(662)
        self.setStyleSheet(DLG_QSS + theme.controls_qss() + SPIN_QSS)

        h = dict(home or {})
        axes0 = dict(h.get("axes") or {})
        self._h0 = h
        self._a0 = axes0
        self.home = None

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 14)
        v.setSpacing(9)

        t = QLabel("回零设置")
        t.setObjectName("kTitle")
        t.setFont(_font(theme.F_BODY, 14, bold=True, spacing=0.6))
        v.addWidget(t)

        s = QLabel("控制器是单向回零：朝选定方向走，撞到该侧限位开关就停，"
                   "再反向退「脱落距离」把开关松开，这一点记为原点。\n"
                   "方向反了不会报错——只会一路顶到机械尽头，直到超时才失败。")
        s.setObjectName("kSub")
        s.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        s.setWordWrap(True)
        v.addWidget(s)
        v.addSpacing(2)

        card = QWidget()
        card.setObjectName("kCard")
        card.setMinimumHeight(150)
        gl = QGridLayout(card)
        gl.setContentsMargins(14, 10, 14, 12)
        gl.setHorizontalSpacing(10)
        gl.setVerticalSpacing(7)

        heads = ("轴", "回零方向", "回零速度", "回零加减速", "脱落距离", "超时")
        for c, text in enumerate(heads):
            lb = _hint(text, 8)
            gl.addWidget(lb, 0, c)

        self.cb_dir = {}
        self.sp_speed = {}
        self.sp_acc = {}
        self.sp_fall = {}
        self.sp_timeout = {}

        for r, name in enumerate(store.AXES, start=1):
            a = dict(axes0.get(name) or {})
            tag = QLabel(name.upper())
            tag.setObjectName("kBold")
            tag.setFont(_font(theme.F_MONO, 11, bold=True, spacing=0.4))
            tag.setFixedWidth(16)
            gl.addWidget(tag, r, 0)

            cb = QComboBox()
            cb.setFixedHeight(28)
            for code in (core.HOME_NEG_LIMIT, core.HOME_POS_LIMIT):
                cb.addItem("%s（%d）" % (store.HOME_LIMIT_TEXT[code], code), code)
            try:
                cur = int(a.get("dir", store.DEFAULTS["motion"]["home"]["axes"][name]["dir"]))
            except (TypeError, ValueError):
                cur = core.HOME_NEG_LIMIT
            idx = cb.findData(cur)
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            cb.setToolTip("%s 轴朝哪一侧限位开关找原点。\n"
                          "装反了就是一路顶到机械尽头，没有任何报错。"
                          % name.upper())
            gl.addWidget(cb, r, 1)
            self.cb_dir[name] = cb

            dflt = store.DEFAULTS["motion"]["home"]["axes"][name]
            self.sp_speed[name] = self._box(gl, r, 2, a.get("speed", dflt["speed"]),
                                            1.0, 300.0, 5.0, 0, " mm/s",
                                            "回零时的移动速度，与点动速度无关")
            self.sp_acc[name] = self._box(gl, r, 3, a.get("acc", dflt["acc"]),
                                          10.0, 5000.0, 10.0, 0, " mm/s²",
                                          "回零加减速。厂家示例用 100，给太小启停很肉")
            self.sp_fall[name] = self._box(gl, r, 4, a.get("fall", dflt["fall"]),
                                           0.0, 50.0, 0.5, 1, " mm",
                                           "回零完成后反向退多少 mm 离开限位开关。\n"
                                           "厂家默认 5；给 0 会停在开关上，限位灯一直亮")
            self.sp_timeout[name] = self._box(gl, r, 5, a.get("timeout", dflt["timeout"]),
                                              5.0, 600.0, 5.0, 0, " s",
                                              "本软件最多等多久；卡上另有「回零超时时间」")
        v.addWidget(card)

        line = QFrame()
        line.setObjectName("kLine")
        line.setFixedHeight(1)
        v.addWidget(line)

        note = QLabel("建议：改完先只回一根轴试方向，别直接点【全部回零】。"
                      "三轴回零参数与点动速度是分开的，改这里不影响手动点动。")
        note.setObjectName("kHint")
        note.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        note.setWordWrap(True)
        v.addWidget(note)

        v.addStretch(1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        b_rst = QPushButton("恢复默认")
        b_rst.setObjectName("act")
        b_rst.setFixedSize(88, 30)
        b_rst.setCursor(Qt.PointingHandCursor)
        b_rst.setToolTip("回到本机原来那套：负限位、50 mm/s、加减速 100、脱落 5 mm")
        b_rst.clicked.connect(self._reset)
        foot.addWidget(b_rst)
        foot.addStretch(1)
        b_cancel = QPushButton("取消")
        b_cancel.setObjectName("act")
        b_cancel.setFixedSize(80, 30)
        b_cancel.setCursor(Qt.PointingHandCursor)
        b_cancel.clicked.connect(self.reject)
        foot.addWidget(b_cancel)
        b_ok = QPushButton("保存")
        b_ok.setObjectName("primary")
        b_ok.setFixedSize(88, 30)
        b_ok.setCursor(Qt.PointingHandCursor)
        b_ok.setDefault(True)
        b_ok.clicked.connect(self._save)
        foot.addWidget(b_ok)
        v.addLayout(foot)

        self.adjustSize()

    def _box(self, grid, row, col, val, lo, hi, step, dec, suffix, tip):
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setSingleStep(step)
        sp.setDecimals(dec)
        try:
            sp.setValue(float(val))
        except (TypeError, ValueError):
            pass
        sp.setSuffix(suffix)
        sp.setFixedHeight(28)
        sp.setMinimumWidth(108)
        sp.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        sp.setToolTip(tip)
        grid.addWidget(sp, row, col)
        return sp

    def _reset(self):
        d = store.DEFAULTS["motion"]["home"]["axes"]
        for name in store.AXES:
            a = d[name]
            idx = self.cb_dir[name].findData(int(a["dir"]))
            self.cb_dir[name].setCurrentIndex(idx if idx >= 0 else 0)
            self.sp_speed[name].setValue(float(a["speed"]))
            self.sp_acc[name].setValue(float(a["acc"]))
            self.sp_fall[name].setValue(float(a["fall"]))
            self.sp_timeout[name].setValue(float(a["timeout"]))

    def _save(self):
        axes = {}
        for name in store.AXES:
            axes[name] = {
                "dir": int(self.cb_dir[name].currentData() or core.HOME_NEG_LIMIT),
                "speed": round(float(self.sp_speed[name].value()), 2),
                "acc": round(float(self.sp_acc[name].value()), 1),
                "fall": round(float(self.sp_fall[name].value()), 2),
                "timeout": round(float(self.sp_timeout[name].value()), 1),
            }
        self.home = dict(self._h0)
        self.home["axes"] = axes
        self.accept()

    def summary(self):
        """一句话：X 负限位 50mm/s 脱落5 / Y …，写日志用。"""
        if not self.home:
            return ""
        out = []
        for name, a in self.home["axes"].items():
            out.append("%s %s %.0fmm/s 脱落%.1f" % (
                name.upper(), store.HOME_LIMIT_TEXT.get(a["dir"], "?"),
                a["speed"], a["fall"]))
        return "；".join(out)
