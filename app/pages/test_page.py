# -*- coding: utf-8 -*-
"""测试管理页 —— 项目 + 两个工作区（测试程序编写 / 坐标转化）。

界面分工（2026-11-05 第三轮定，按用户描述改）：

    ┌ [新建项目] [打开项目 ▾]                    ● 状态 ┐
    │ （没点新建、没打开项目时，下面就是空白）           │
    ├────────────────────────────────────────────────────┤
    │ 项目名称 [____] 保存地址 [__________] [浏览]       │ ← 点【新建项目】才出现
    │ 测试程序名称 [只读时灰 ▾]  [💾保存项目][✎编辑项目][🗑删除项目]
    │ yolo 模型 [只读时灰 ____][选择模型]  三轴 X 粗定位 [__] mm -35~35
    │ 探针 XYZ 步长 [__ ▾]   探针 UVW 步长 [__ ▾]
    │ 下次自动测试会建：D:\\...\\2026-11-05_10-40         │
    ├ 左栏 208 ──────┬ 右栏：选中那一栏的内容 ─────────┤
    │ ▣ 测试程序编写  │                                  │
    │ ◎ 坐标转化      │                                  │
    └────────────────┴──────────────────────────────────┘
    一句状态提示

三条规则（用户明确要求）：

1. **只有点【保存项目】才落盘** —— 新建项目时改了名字、选了地址都只是草稿，
   点了保存才进项目清单，【打开项目】下拉里才看得到它。
2. **打开的项目默认只读** —— 点【编辑项目】才能改名字、改保存地址、换 yolo 模型、
   改测试程序，改完再点【保存项目】；没点编辑时输入框、【浏览】【选择模型】和表格
   都是灰的。
3. **删除要二次确认** —— 弹窗写明"删除后不可复原"，确认后只从项目清单里移除，
   保存地址里的照片/日志/结果一个都不动。

【打开项目】不是选文件夹，而是下拉本机保存过的项目名字；保存地址只是"自动测试
产生的文件存哪"，不往里写任何项目文件 —— 项目清单记在 data\\devices.json，
见 app/project.py。

项目信息条上那个【测试程序名称】下拉是"这台机器上有哪些程序"的入口：选中哪个，
右栏表格就自动读进哪一份（见 ProgramEditor.open）。程序是**全局**的，存在
data\\devices.json 的 test.programs 里 —— 在哪个项目里写好的程序，别的项目也能选到；
项目自己只记"上次选的是哪一份"（Project.active），重开项目时按它读回来。

这个下拉跟表格一样受只读约束：打开项目后它是灰的，点【编辑项目】才解锁 ——
"选哪份程序"算改这个项目，光看的话打开项目时已经把上次那份读进来了。

再下一行的【yolo 模型】是引脚识别用的模型文件（.pt / .onnx）。不同板子训出来的
模型不一样，所以它跟着项目走（Project.yolo），不是全局设置；同样只在编辑态可选，
选中后点【保存项目】才落盘。文件被挪走时路径框标琥珀色，tooltip 写明原因。

最后两行是自动测试要用的参数，也只在编辑态可改：

* **三轴 X 粗定位（mm）** —— 第三阶段开跑时 X 轴先移到这里，把板子送到视野附近。
  允许范围**从设备管理的 X 轴限位来**（`manager.cfg["motion"]["axes"]["x"]`，
  0 表示不限），旁边直接标出来；填超了当场退回原值，保存时再拦一次 ——
  存进去一个越界值，等自动测试时驱动层拒动，查起来更费劲。
* **探针 XYZ / UVW 步长（mm）** —— 探针走小步的量。给常用候选也允许手填，
  必须是大于 0 的数字。
"""

import os
import traceback
from datetime import datetime

from PyQt5.QtCore import QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFontMetrics, QIcon, QPainter
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                            QLabel, QPushButton, QLineEdit, QComboBox,
                            QFileDialog, QFrame, QStackedWidget, QMenu,
                            QMessageBox)

from .. import icons, program, theme
from ..project import Project, Registry, SUB_DIRS
from ..widgets import HEAD_H, Panel, _font, paint_module_head
from .program_editor import ProgramEditor

# 打开项目下拉的样式：QMenu 默认是系统浅色，和深色界面格格不入
MENU_QSS = theme.menu_qss()


class _EntryItem(QFrame):
    """左栏的功能入口：图标 + 标题 + 一句说明，选中时左边亮一道琥珀色。"""

    clicked = pyqtSignal()

    def __init__(self, icon_name, title, sub, parent=None):
        super(_EntryItem, self).__init__(parent)
        self.setObjectName("entryItem")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(50)
        self._icon = icon_name

        h = QHBoxLayout(self)
        h.setContentsMargins(12, 5, 8, 5)
        h.setSpacing(9)

        self.lb_icon = QLabel()
        self.lb_icon.setFixedSize(16, 16)
        self.lb_icon.setAlignment(Qt.AlignCenter)
        self.lb_icon.setStyleSheet("background:transparent;")
        h.addWidget(self.lb_icon, 0, Qt.AlignVCenter)

        v = QVBoxLayout()
        v.setSpacing(1)
        v.addStretch(1)

        self.lb_title = QLabel(title)
        self.lb_title.setObjectName("entryTitle")
        self.lb_title.setFont(_font(theme.F_BODY, 11, bold=True, spacing=0.3))

        self.lb_sub = QLabel(sub)
        self.lb_sub.setObjectName("entrySub")
        self.lb_sub.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        self.lb_sub.setVisible(bool(sub))     # 没写副标题就整行不占位

        v.addWidget(self.lb_title)
        v.addWidget(self.lb_sub)
        v.addStretch(1)
        h.addLayout(v, 1)

        self.set_active(False)

    def _paint_icon(self, color):
        self.lb_icon.setPixmap(
            icons.icon(self._icon, color, 15, self.devicePixelRatioF()))

    def set_active(self, on):
        self._paint_icon(theme.AMBER if on else theme.TXT3)
        if on:
            self.setStyleSheet(
                "QFrame#entryItem{background:rgba(27,87,160,0.07);"
                "border:1px solid #17497f;border-left:3px solid %s;"
                "border-radius:4px;}"
                "QLabel#entryTitle{background:transparent;color:%s;}"
                "QLabel#entrySub{background:transparent;color:%s;}"
                % (theme.AMBER, theme.AMBER2, theme.AMBER_DIM))
        else:
            self.setStyleSheet(
                "QFrame#entryItem{background:rgba(27,87,160,0.02);"
                "border:1px solid #dbe6f1;border-left:3px solid #e4eef7;"
                "border-radius:4px;}"
                "QFrame#entryItem:hover{background:rgba(27,87,160,0.05);"
                "border-color:#d5e4f1;}"
                "QLabel#entryTitle{background:transparent;color:%s;}"
                "QLabel#entrySub{background:transparent;color:%s;}"
                % (theme.TXT2, theme.TXT3))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super(_EntryItem, self).mousePressEvent(e)


class TestPage(QWidget):
    """测试管理页：一个项目 = 名字 + 保存地址 + 一套配置。"""

    # (key, 标题, 副标题)。key 同时也是图标名（见 icons.py）
    STAGES = (
        ("program", "测试程序编写", ""),
        ("calib", "坐标转化", "像素坐标 ↔ 机械坐标"),
    )

    # 探针步长候选（mm）：0.001 用来找细边，5 用来快速挪位。
    # 跟手动控制页的快捷步长是同一套量级，别处改这里也跟着改。
    STEPS = ("0.001", "0.002", "0.005", "0.01", "0.02", "0.05",
             "0.1", "0.2", "0.5", "1", "2", "5")

    def __init__(self, mod, manager, parent=None):
        super(TestPage, self).__init__(parent)
        self.mod = mod
        self.mgr = manager
        self.reg = Registry(manager.cfg)
        # 程序是全局的（test.programs），老版本挂在项目里的那份当场搬过来
        _, moved = program.open_library(manager.cfg, self.reg.projects)
        if moved:
            self.reg.save()
        self.proj = None                # 当前正在编辑的项目（草稿也算）
        self._orig = None               # 它在清单里的名字，None = 还没保存过
        self.editable = False           # 只有新建 / 点了【编辑项目】才是 True
        self._snap = ("", "")           # 进入编辑那刻的名字/地址，用来判断有没有改动
        self._prog_loading = False       # 程序下拉在程序化改选中时挡信号
        self.cards = []
        self._inited = False

        self.setStyleSheet(theme.controls_qss())

        root = QVBoxLayout(self)
        root.setContentsMargins(18, HEAD_H, 18, 18)
        root.setSpacing(9)

        panel = Panel()
        v = QVBoxLayout(panel)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(10)
        v.addWidget(self._build_topbar())

        self.body = QStackedWidget()
        self.body.addWidget(QWidget())          # 0：空白（还没新建/打开项目）
        self.body.addWidget(self._build_work())  # 1：项目工作区
        v.addWidget(self.body, 1)
        v.addWidget(self._build_info())

        root.addWidget(panel, 1)

        # 预览路径里带着"现在几点"，页面一直开着会过时，定期刷一下
        self._clock = QTimer(self)
        self._clock.setInterval(20000)
        self._clock.timeout.connect(self._refresh_caption)

        self.body.setCurrentIndex(0)
        self._set_editable(False)
        self._refresh()

    # ================================================================ 顶栏
    def _build_topbar(self):
        w = QWidget()
        w.setObjectName("projBar")
        w.setFixedHeight(46)
        w.setStyleSheet(
            "#projBar{background:rgba(255,255,255,0.018);"
            "border:1px solid #dbe6f1;border-radius:4px;}")
        h = QHBoxLayout(w)
        h.setContentsMargins(12, 0, 14, 0)
        h.setSpacing(10)

        self.btn_new = QPushButton("新建项目")
        self.btn_new.setObjectName("primary")
        self.btn_new.setFixedSize(100, 30)
        self.btn_new.setCursor(Qt.PointingHandCursor)
        self.btn_new.setToolTip("建立一个新项目：起名字、选保存地址")
        self.btn_new.clicked.connect(self._on_new)
        h.addWidget(self.btn_new)

        self.btn_open = QPushButton("打开项目  ▾")
        self.btn_open.setObjectName("act")
        self.btn_open.setFixedSize(112, 30)
        self.btn_open.setCursor(Qt.PointingHandCursor)
        self.btn_open.setToolTip("下拉选择本机保存过的项目")
        self.btn_open.clicked.connect(self._on_open)
        h.addWidget(self.btn_open)

        h.addStretch(1)

        self.lb_state = QLabel("● 未打开项目")
        self.lb_state.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        self.lb_state.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        h.addWidget(self.lb_state)
        return w

    def _build_info(self):
        self.info = QLabel("点【新建项目】开始，或从【打开项目】里选一个保存过的项目。")
        self.info.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        self.info.setWordWrap(True)
        self.info.setMinimumHeight(18)
        self.info.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        return self.info

    # ================================================================ 工作区
    def _build_work(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        v.addWidget(self._build_setup())

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        row.addWidget(self._build_side(), 0)
        row.addWidget(self._build_workspace(), 1)
        v.addLayout(row, 1)
        return w

    def _build_setup(self):
        """项目名 + 保存地址 + 测试程序下拉 + 项目操作（保存 / 编辑 / 删除）。

        2026-11-05 第五轮：项目操作从"左栏底部"挪到这一条的最右 —— 它们管的是
        整个项目，紧挨着项目名称/保存地址更顺；顺手把【测试程序名称】下拉放在
        同一行，选中哪份程序，右栏表格就自动读哪份。
        """
        w = QWidget()
        w.setObjectName("setupBar")
        w.setStyleSheet(
            "#setupBar{background:rgba(255,255,255,0.018);"
            "border:1px solid #dbe6f1;border-radius:4px;}")
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 9, 12, 7)
        v.setSpacing(5)

        r = QHBoxLayout()
        r.setSpacing(8)
        r.addWidget(self._tag("项目名称", 52))

        self.ed_name = QLineEdit()
        self.ed_name.setFixedHeight(28)
        self.ed_name.setFixedWidth(190)
        self.ed_name.setFont(_font(theme.F_BODY, 11, spacing=0.2))
        self.ed_name.setPlaceholderText("项目名（默认取文件夹名）")
        r.addWidget(self.ed_name)

        r.addSpacing(16)
        r.addWidget(self._tag("保存地址", 52))

        self.ed_root = QLineEdit()
        self.ed_root.setReadOnly(True)
        self.ed_root.setPlaceholderText("自动测试的文件存这里（点【浏览】选）")
        self.ed_root.setFont(_font(theme.F_MONO, 10, spacing=0.1))
        self.ed_root.setFixedHeight(28)
        # 不再撑满整条：典型路径 20 来个字符，380 够看；再长鼠标停上去看全路径
        self.ed_root.setMaximumWidth(380)
        self.ed_root.setStyleSheet("color:%s;" % theme.TXT2)
        self.ed_root.editingFinished.connect(self._refresh_caption)  # 手输也要刷新预览
        r.addWidget(self.ed_root, 1)

        self.btn_browse = QPushButton("浏览")
        self.btn_browse.setObjectName("act")
        self.btn_browse.setFixedSize(56, 28)
        self.btn_browse.setCursor(Qt.PointingHandCursor)
        self.btn_browse.setToolTip("选一个文件夹存放自动测试的结果")
        self.btn_browse.clicked.connect(self._on_browse)
        r.addWidget(self.btn_browse)

        v.addLayout(r)

        # 第二行：左边【测试程序名称】下拉，右边项目级的三个操作。
        # 单看"程序属于项目"，把下拉塞进第一行最省地方，但 1100 宽的窗口下
        # 保存地址会被挤成几个字 —— 宁可多一行，也别让地址看不见。
        p = QHBoxLayout()
        p.setContentsMargins(0, 0, 0, 0)
        p.setSpacing(8)
        p.addWidget(self._tag("测试程序名称", 66))

        self.cb_prog = QComboBox()
        self.cb_prog.setFixedSize(220, 28)
        self.cb_prog.setFont(_font(theme.F_BODY, 11, spacing=0.2))
        self.cb_prog.setCursor(Qt.PointingHandCursor)
        self.cb_prog.setToolTip("下拉选择保存过的测试程序，选中后表格自动读取它")
        self.cb_prog.currentIndexChanged.connect(self._on_pick_program)
        p.addWidget(self.cb_prog)
        p.addStretch(1)

        self.btn_save = QPushButton("  保存项目")
        self.btn_save.setObjectName("primary")
        self.btn_save.setFixedSize(98, 28)
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.setIcon(QIcon(icons.icon(
            "save", theme.AMBER2, 13, self.devicePixelRatioF())))
        self.btn_save.setIconSize(QSize(13, 13))
        self.btn_save.clicked.connect(self._on_save)
        p.addWidget(self.btn_save)

        self.btn_edit = QPushButton("  编辑项目")
        self.btn_edit.setFixedSize(90, 28)
        self.btn_edit.setCursor(Qt.PointingHandCursor)
        self.btn_edit.setIcon(QIcon(icons.icon(
            "edit", theme.TXT2, 12, self.devicePixelRatioF())))
        self.btn_edit.setIconSize(QSize(12, 12))
        self.btn_edit.setStyleSheet(
            "QPushButton{background:#f4f9ff;border:1px solid #a8c4de;"
            "border-radius:3px;color:#17497f;font-size:11px;padding:0 5px;}"
            "QPushButton:hover{background:#e7f1fb;border-color:#2e74c4;}"
            "QPushButton:disabled{color:#8ba1b5;background:#f4f9ff;"
            "border-color:#c9dbeb;}")
        self.btn_edit.clicked.connect(self._on_edit)
        p.addWidget(self.btn_edit)

        self.btn_del = QPushButton("  删除项目")
        self.btn_del.setFixedSize(94, 28)
        self.btn_del.setCursor(Qt.PointingHandCursor)
        self.btn_del.setIcon(QIcon(icons.icon(
            "trash", theme.BAD, 12, self.devicePixelRatioF())))
        self.btn_del.setIconSize(QSize(12, 12))
        self.btn_del.setStyleSheet(
            "QPushButton{background:#fdf3f2;border:1px solid #d59a95;"
            "border-radius:3px;color:#c0392b;font-size:11px;padding:0 5px;}"
            "QPushButton:hover{background:#fdf3f2;border-color:#c0392b;"
            "color:#c0392b;}"
            "QPushButton:disabled{color:#e0b4b0;background:#fdf3f2;"
            "border-color:#fdf3f2;}")
        self.btn_del.clicked.connect(self._on_delete)
        p.addWidget(self.btn_del)
        v.addLayout(p)

        # 第三行：yolo 模型 —— 引脚识别用哪个模型文件。
        # 模型跟着项目走（不同板子训出来的模型不一样），所以放在项目条上，
        # 和保存地址一样只在编辑状态可选。
        y = QHBoxLayout()
        y.setContentsMargins(0, 0, 0, 0)
        y.setSpacing(8)
        y.addWidget(self._tag("yolo 模型", 66))

        self.ed_yolo = QLineEdit()
        self.ed_yolo.setReadOnly(True)
        self.ed_yolo.setPlaceholderText("点【选择模型】选引脚识别用的 .pt / .onnx 模型")
        self.ed_yolo.setFont(_font(theme.F_MONO, 10, spacing=0.1))
        self.ed_yolo.setFixedHeight(28)
        self.ed_yolo.setMinimumWidth(180)
        self.ed_yolo.setMaximumWidth(460)      # 别把整行撑满，右边还要放粗定位
        self.ed_yolo.setStyleSheet("color:%s;" % theme.TXT2)
        y.addWidget(self.ed_yolo, 1)

        self.btn_yolo = QPushButton("选择模型")
        self.btn_yolo.setObjectName("act")
        self.btn_yolo.setFixedSize(72, 28)
        self.btn_yolo.setCursor(Qt.PointingHandCursor)
        self.btn_yolo.setToolTip("选这个项目要用的 yolo 模型文件")
        self.btn_yolo.clicked.connect(self._on_pick_yolo)
        y.addWidget(self.btn_yolo)
        y.addStretch(1)

        # 同一行右半：三轴 X 轴粗定位 —— 第三阶段自动测试开跑时，X 轴先按这个值
        # 移过去把板子送到视野附近，再交给视觉细定位。范围由设备管理里 X 轴限位决定。
        y.addWidget(self._tag("三轴 X 粗定位", 96))
        self.ed_xc = QLineEdit()
        self.ed_xc.setReadOnly(True)
        self.ed_xc.setFixedSize(84, 28)
        self.ed_xc.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.ed_xc.setFont(_font(theme.F_MONO, 11, spacing=0.2))
        self.ed_xc.setPlaceholderText("未设")
        self.ed_xc.setToolTip("第三阶段自动测试时 X 轴先移到这里")
        self.ed_xc.editingFinished.connect(self._on_xc_edited)
        y.addWidget(self.ed_xc)

        lb_mm = QLabel("mm")
        lb_mm.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        lb_mm.setFixedWidth(22)
        lb_mm.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        y.addWidget(lb_mm)

        self.lb_xc_range = QLabel("")
        self.lb_xc_range.setFont(_font(theme.F_MONO, 9, spacing=0.1))
        self.lb_xc_range.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        y.addWidget(self.lb_xc_range)
        v.addLayout(y)

        # 第四行：探针两组轴的步长（探针下压找点 / 平面找边按这个量走小步）
        s = QHBoxLayout()
        s.setContentsMargins(0, 0, 0, 0)
        s.setSpacing(8)
        s.addWidget(self._tag("探针 XYZ 步长", 96))

        self.cb_sxyz = self._step_combo()
        s.addWidget(self.cb_sxyz)

        s.addSpacing(22)
        s.addWidget(self._tag("探针 UVW 步长", 96))

        self.cb_suvw = self._step_combo()
        s.addWidget(self.cb_suvw)
        s.addStretch(1)
        v.addLayout(s)

        # 第三行：地址下面那行小字 —— 下次自动测试会建哪个目录、这个地址里已有几次记录
        cap = QHBoxLayout()
        cap.setContentsMargins(0, 0, 0, 0)
        cap.setSpacing(10)
        self.lb_next = QLabel("")
        self.lb_next.setFont(_font(theme.F_MONO, 9, spacing=0.1))
        self.lb_next.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lb_next.setStyleSheet(
            "color:%s;background:transparent;" % theme.AMBER_DIM)
        cap.addWidget(self.lb_next, 1)

        self.lb_count = QLabel("")
        self.lb_count.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        self.lb_count.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        cap.addWidget(self.lb_count, 0)
        v.addLayout(cap)
        return w

    def _build_side(self):
        """左栏：两个工作区入口。

        项目级的操作（保存 / 编辑 / 删除）已经搬到上面的项目条里 —— 它们管的是
        整个项目，不该跟"选哪个工作区"混在一列。
        """
        w = QWidget()
        w.setFixedWidth(208)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        head = QLabel("工作区")
        head.setFont(_font(theme.F_BODY, 9, spacing=1.6))
        head.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        v.addWidget(head)

        for i, (key, title, sub) in enumerate(self.STAGES):
            it = _EntryItem(key, title, sub)
            it.clicked.connect(lambda i=i: self._select_stage(i))
            self.cards.append(it)
            v.addWidget(it)

        v.addStretch(1)
        return w

    def _build_workspace(self):
        self.stack = QStackedWidget()
        self.editor = ProgramEditor(self)
        self.stack.addWidget(self.editor)
        self.stack.addWidget(self._stage_page("坐标转化"))
        return self.stack

    def _stage_page(self, title):
        """一栏的内容区。现在只有标题，正文空着，下一轮往里填。"""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(7)

        head = QHBoxLayout()
        head.setContentsMargins(2, 0, 2, 0)
        head.setSpacing(8)
        t = QLabel(title)
        t.setFont(_font(theme.F_BODY, 13, bold=True, spacing=0.6))
        t.setStyleSheet("color:%s;background:transparent;" % theme.TXT)
        head.addWidget(t)
        head.addStretch(1)
        st = QLabel("● 待编写")
        st.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        st.setStyleSheet("color:%s;background:transparent;" % theme.WARN)
        head.addWidget(st)
        v.addLayout(head)

        panel = Panel()
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(0, 0, 0, 0)
        tip = QLabel("· 内容待填写 ·")
        tip.setFont(_font(theme.F_MONO, 10, spacing=1.4))
        tip.setAlignment(Qt.AlignCenter)
        tip.setStyleSheet("color:#8ba1b5;background:transparent;")
        pv.addWidget(tip)
        v.addWidget(panel, 1)
        return w

    def _tag(self, text, w=52):
        """小号灰标签。宽度按文字实测取，w 只当"最小宽度"用。

        别用 lb.font() 去量 —— 中文标签的实际字号来自全局样式表
        （QWidget{font-size:12px}），QSS 的字号不会写回 widget.font()，
        量出来只有 10px、比实际渲染窄一圈，"测试程序名称"就会被切成"测试程序名"。
        统一用 theme.FONT_PX 建字体来量，才是屏幕上那个宽度。
        """
        lb = QLabel(text)
        f = _font(theme.F_BODY, theme.FONT_PX, spacing=0.2)
        lb.setFont(f)
        need = QFontMetrics(f).width(text) + 8
        lb.setFixedWidth(max(int(w or 0), need))
        lb.setStyleSheet("color:%s;background:transparent;font-size:%dpx;"
                         % (theme.TXT3, theme.FONT_PX))
        return lb

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        paint_module_head(p, 18, 16, self.width() - 36,
                          self.mod["idx"], self.mod["name"], self.mod["desc"])
        p.end()

    # ================================================================ 项目
    def _on_new(self):
        try:
            if not self._confirm_discard():
                return
            self._sync_cfg()
            # 草稿：名字先空着，选了地址按文件夹名补上；点【保存项目】才进清单
            self.proj = Project("", "", datetime.now().strftime("%Y-%m-%d %H:%M"))
            self._orig = None
            self.ed_name.clear()
            self.ed_root.clear()
            self.ed_root.setToolTip("")
            self.ed_yolo.clear()
            self.ed_yolo.setToolTip("")
            self._fill_params(self.proj)      # 新项目：粗定位/步长都空着
            self.body.setCurrentIndex(1)
            self._select_stage(0)
            self.editor.bind(self.proj)       # 新项目：不预选任何程序
            self._refresh_programs()
            self._set_editable(True)          # 新建时直接可编辑
            self._refresh()
            self.ed_name.setFocus()
            self._say("新建项目：起个名字、选保存地址，然后点【保存项目】。", "info")
        except Exception as e:                  # 槽函数里漏出去的异常会直接杀进程
            traceback.print_exc()
            self._say("新建项目出错：%r" % e, "err")

    def _build_open_menu(self):
        """打开项目下拉：一行一个保存过的项目名，地址不对的标出来。"""
        m = QMenu(self)
        m.setStyleSheet(MENU_QSS)
        for p in self.reg.projects:
            text = p.name if p.root_ok else "%s　（保存地址不存在）" % p.name
            act = m.addAction(text)
            act.setData(p.name)
            act.setToolTip(p.root or "未设置保存地址")
        return m

    def _on_open(self):
        """下拉列出保存过的项目。选文件夹的那套旧逻辑已经去掉。"""
        try:
            if self.reg.is_empty():
                self._say("还没有保存过的项目，先点【新建项目】。", "warn")
                return
            m = self._build_open_menu()
            pos = self.btn_open.mapToGlobal(
                QPoint(0, self.btn_open.height() + 4))
            act = m.exec_(pos)
            if act is not None:
                self._open(act.data())
        except Exception as e:
            traceback.print_exc()
            self._say("打开项目出错：%r" % e, "err")

    def _open(self, name):
        self._sync_cfg()
        p = self.reg.find(name)
        if p is None:
            self._say("项目列表里找不到 %s" % name, "err")
            return
        if not self._confirm_discard():
            return
        self.proj = p
        self._orig = p.name
        self.ed_name.setText(p.name)
        self.ed_root.setText(p.root)
        self.ed_root.setToolTip(p.root)
        self.ed_root.setCursorPosition(0)
        self.ed_yolo.setText(p.yolo)
        self.ed_yolo.setToolTip(p.yolo)
        self.ed_yolo.setCursorPosition(0)
        self._fill_params(p)
        self.body.setCurrentIndex(1)
        self._select_stage(0)
        self.editor.bind(p)                # 换项目：表格先清空
        self._refresh_programs()           # 下拉列出全部程序
        self._set_editable(False)          # 打开的项目默认只读

        # 上次在这个项目里选的是哪份程序，打开就把它读回来 ——
        # 不然用户明明选过了，重开项目又是一张空表。
        note = ""
        if p.active:
            if program.find_program(self.lib, p.active) is not None:
                if self.editor.open(p.active):
                    note = "　程序：%s" % p.active
            else:
                p.active = ""              # 那份程序被删了，别再惦记
                self.reg.save()
        self._refresh()
        if p.root_ok:
            self._say("已打开项目：%s%s　（只读；用上面的【测试程序名称】选程序看，"
                      "要改就点【编辑项目】）" % (p.name, note), "ok")
        else:
            self._say("已打开项目：%s%s　—— 但保存地址不在（%s），"
                      "点【编辑项目】重新选一个再跑自动测试。"
                      % (p.name, note, p.root or "未设置"), "warn")

    def _on_browse(self):
        try:
            if not self.editable:
                self._say("只读：先点【编辑项目】才能改保存地址。", "warn")
                return
            start = self.ed_root.text().strip() or os.path.expanduser("~")
            d = QFileDialog.getExistingDirectory(
                self, "选择保存地址（自动测试拍的照片、日志、结果都存这里）", start)
            if not d:
                return
            self.ed_root.setText(d)
            self.ed_root.setToolTip(d)
            self.ed_root.setCursorPosition(0)
            if not self.ed_name.text().strip():
                self.ed_name.setText(os.path.basename(d.rstrip("\\/")) or "新项目")
            self._refresh_caption()
            self._refresh()
            self._say("保存地址改了，别忘了点【保存项目】。", "info")
        except Exception as e:
            traceback.print_exc()
            self._say("选保存地址出错：%r" % e, "err")

    def _step_combo(self):
        """探针步长下拉：给常用值，也允许手填。"""
        cb = QComboBox()
        cb.setFixedSize(112, 28)
        cb.setEditable(True)
        cb.setInsertPolicy(QComboBox.NoInsert)
        cb.setFont(_font(theme.F_MONO, 10, spacing=0.2))
        cb.setCursor(Qt.PointingHandCursor)
        cb.addItems(list(self.STEPS))
        cb.setCurrentIndex(-1)
        if cb.lineEdit() is not None:
            cb.lineEdit().setFont(_font(theme.F_MONO, 10, spacing=0.2))
            cb.lineEdit().setPlaceholderText("未设（mm）")
        cb.currentTextChanged.connect(lambda _t: self._refresh())
        return cb

    # ------------------------------------------------------------ 项目条：X 粗定位
    def _x_limits(self):
        """三轴控制器 X 轴的软件限位 (正, 负)，None = 该方向不限。

        口径直接照搬 MotionDriver.limits：device 配置里 0 表示"不限制"。
        在页面里重算一遍是为了能随时跟着设备管理页的新值走 —— 别在两处写死。
        """
        cfg = getattr(self.mgr, "cfg", None) or {}
        axes = ((cfg.get("motion") or {}).get("axes") or {})
        c = axes.get("x") or {}
        try:
            p = float(c.get("limit_pos") or 0)
            n = float(c.get("limit_neg") or 0)
        except (TypeError, ValueError):
            return None, None
        return (p if p else None), (n if n else None)

    def _refresh_xc_range(self):
        """把允许范围写在下拉右边，省得填完才知道越界。"""
        if not hasattr(self, "lb_xc_range"):
            return
        pos, neg = self._x_limits()
        if pos is None and neg is None:
            self.lb_xc_range.setText("不限")
            self.lb_xc_range.setToolTip("设备管理里 X 轴没设限位")
            return
        lo = "%g" % neg if neg is not None else "-∞"
        hi = "%g" % pos if pos is not None else "+∞"
        self.lb_xc_range.setText("%s ~ %s" % (lo, hi))
        self.lb_xc_range.setToolTip("范围来自设备管理 → 三轴控制器 → X 轴限位（mm）")

    def _xc_value(self):
        """输入框里的值：合法 float 或 None（空 / 不是数字）。"""
        t = self.ed_xc.text().strip()
        if not t:
            return None
        try:
            return float(t)
        except ValueError:
            return float("nan")     # 用 NaN 表示"填了但不是数字"，好和"空"区分

    def _restore_xc(self):
        v = self.proj.x_coarse if self.proj is not None else None
        self.ed_xc.setText("" if v is None else ("%g" % v))

    def _xc_error(self, v):
        """越界返回一句人话，没问题返回 None。"""
        if v is None or v != v:         # 空 / 不是数字
            return None
        pos, neg = self._x_limits()
        if pos is not None and v > pos + 1e-9:
            return "X 粗定位 %.3f mm 超过正限位 %.3f mm（设备管理里 X 轴）" % (v, pos)
        if neg is not None and v < neg - 1e-9:
            return "X 粗定位 %.3f mm 超过负限位 %.3f mm（设备管理里 X 轴）" % (v, neg)
        return None

    def _on_xc_edited(self):
        """填完 X 粗定位：校验数字和限位，不合格就退回上一次的值。"""
        try:
            if not self.editable:
                self._restore_xc()
                return
            v = self._xc_value()
            if v is not None and v != v:
                self._say("X 粗定位要填数字（mm），已退回原来的值。", "warn")
                self._restore_xc()
                self._refresh()
                return
            why = self._xc_error(v)
            if why:
                self._say(why + "，已退回原来的值。", "warn")
                self._restore_xc()
                self._refresh()
                self.ed_xc.setFocus()
                self.ed_xc.selectAll()
                return
            if v is not None:
                self.ed_xc.setText("%g" % round(v, 3))
            self._refresh()
        except Exception as e:
            traceback.print_exc()
            self._say("改 X 粗定位出错：%r" % e, "err")

    # ------------------------------------------------------------ 存 / 改 / 删
    def _on_pick_yolo(self):
        """选 yolo 模型文件。跟保存地址一样，只在编辑状态可选。"""
        try:
            if not self.editable:
                self._say("只读：先点【编辑项目】才能换 yolo 模型。", "warn")
                return
            start = self.ed_yolo.text().strip()
            if start and not os.path.isfile(start):
                start = os.path.dirname(start)
            if not start or not os.path.isdir(start):
                start = self.ed_root.text().strip() or os.path.expanduser("~")
            f, _ = QFileDialog.getOpenFileName(
                self, "选择 yolo 模型（引脚识别用）", start,
                "模型文件 (*.pt *.onnx *.engine);;所有文件 (*.*)")
            if not f:
                return
            f = os.path.normpath(f.replace("/", "\\"))
            self.ed_yolo.setText(f)
            self.ed_yolo.setToolTip(f)
            self.ed_yolo.setCursorPosition(0)
            self._refresh()
            self._say("yolo 模型选了：%s　别忘了点【保存项目】。" % os.path.basename(f),
                      "info")
        except Exception as e:
            traceback.print_exc()
            self._say("选 yolo 模型出错：%r" % e, "err")

    def _fill_params(self, p):
        """把项目里的测试参数写回控件（X 粗定位 + 探针两组步长）。"""
        self.ed_xc.setText("" if p.x_coarse is None else "%g" % p.x_coarse)
        self._set_step(self.cb_sxyz, p.step_xyz)
        self._set_step(self.cb_suvw, p.step_uvw)

    @staticmethod
    def _set_step(cb, v):
        cb.setCurrentIndex(-1)
        cb.setEditText("" if v is None else "%g" % v)

    def _read_params(self):
        """读三项测试参数 → (x_coarse, step_xyz, step_uvw)，None = 没设。

        有问题抛 ValueError，调用方负责提示并中止保存 —— 参数不许悄悄存个错的。
        """
        xc = self._xc_value()
        if xc is not None and xc != xc:
            raise ValueError("X 粗定位要填数字（mm）")
        why = self._xc_error(xc)
        if why:
            raise ValueError(why)
        return (xc,
                self._step_value(self.cb_sxyz, "探针 XYZ 步长"),
                self._step_value(self.cb_suvw, "探针 UVW 步长"))

    @staticmethod
    def _step_value(cb, label):
        t = cb.currentText().strip()
        if not t:
            return None
        try:
            v = float(t)
        except ValueError:
            raise ValueError("%s 要填数字（mm），现在是「%s」" % (label, t))
        if v <= 0:
            raise ValueError("%s 要大于 0，现在是 %g" % (label, v))
        return v

    def _on_save(self):
        """【保存项目】：把界面上这份名字 + 地址写进清单。只有这里会落盘。"""
        try:
            self._sync_cfg()
            if not self.editable:
                self._say("只读：先点【编辑项目】才能改。", "warn")
                return
            name = self.ed_name.text().strip()
            root = self.ed_root.text().strip()
            if not name or not root:
                self._say("还差内容：项目名称和保存地址都要填。", "warn")
                (self.ed_name if not name else self.btn_browse).setFocus()
                return

            other = self.reg.find(name)
            if other is not None and name != self._orig:
                self._say("已经有同名项目「%s」，换个名字。"
                          "（想用它就在【打开项目】里选）" % name, "warn")
                self.ed_name.setFocus()
                self.ed_name.selectAll()
                return

            # 地址不存在也照样存下来（换台机器、盘没插都可能），界面上标"地址失效"。
            # 这里刻意不 makedirs —— 保存地址是结果目录，不该由配置动作去建目录。
            verb = "已新建" if self._orig is None else (
                "已改名" if name != self._orig else "已保存")

            # 测试参数先过一遍：X 粗定位越限、步长不是正数，一律不让存 ——
            # 存进去一个越界值，第三阶段会自动测试时 X 轴一动就被驱动层拒掉，更难查。
            try:
                xc, s_xyz, s_uvw = self._read_params()
            except ValueError as e:
                self._say("还不能保存：%s" % e, "warn")
                if self._xc_error(self._xc_value()):
                    self.ed_xc.setFocus()
                    self.ed_xc.selectAll()
                return

            flushed = self.editor.flush()      # 表格里改了一半的程序也顺手存上
            proj = Project(name, root, self.proj.created if self.proj else "",
                           yolo=self.ed_yolo.text().strip(),
                           x_coarse=xc, step_xyz=s_xyz, step_uvw=s_uvw)
            if self.proj is not None:
                proj.program = self.proj.program
                proj.calib = self.proj.calib
                proj.active = self.proj.active  # 记住这个项目上次选的程序
                proj.board = self.proj.board    # 板号是在自动测试页填的，别在这儿弄丢
                proj.repair = self.proj.repair  # 检修状态同理，也进目录名
                # 任务号不用带：它是按保存地址里的目录现算的，不存进项目
            cur = self.reg.put(proj, old_name=self._orig)
            ok, msg = self.reg.save()
            if not ok:
                self._say("项目清单没能写进配置：%s" % msg, "err")
                return

            self.proj = cur
            self._orig = cur.name
            self.ed_name.setText(cur.name)
            self.editor.set_project(cur)
            self._refresh_programs()
            self._set_editable(False)          # 存完回到只读
            self._refresh()
            text = "%s：%s　（保存地址 %s）" % (verb, cur.name, cur.root)
            if flushed == "unnamed":
                self._say(text + "　程序还没起名字，表格内容没跟着存进项目", "warn")
            elif not cur.root_ok:
                self._say(text + "　地址暂时打不开，跑自动测试前确认一下", "warn")
            elif cur.yolo and not cur.yolo_ok:
                self._say(text + "　yolo 模型文件找不到了，跑自动测试前重选一个", "warn")
            else:
                self._say(text, "ok")
        except Exception as e:
            traceback.print_exc()
            self._say("保存项目出错：%r" % e, "err")

    def _on_edit(self):
        try:
            if not self._orig:
                self._say("这个项目还没保存过，直接填好点【保存项目】就行。", "warn")
                return
            self._set_editable(True)
            self._refresh()
            self.ed_name.setFocus()
            self.ed_name.selectAll()
            self._say("编辑中：改完点【保存项目】才生效。", "info")
        except Exception as e:
            traceback.print_exc()
            self._say("进入编辑出错：%r" % e, "err")

    def _on_delete(self):
        try:
            self._sync_cfg()
            if not self._orig:
                self._say("这个项目还没保存过，没有可删的记录。", "warn")
                return
            name = self._orig
            if not self._ask_delete(name):
                return
            self.reg.remove(name)
            ok, msg = self.reg.save()
            if not ok:
                self._say("删掉了但配置没写进去：%s　重启后它还会回来。" % msg, "err")
                return

            # 回到刚进页面的空白态
            self.proj = None
            self._orig = None
            self.ed_name.clear()
            self.ed_root.clear()
            self.ed_root.setToolTip("")
            self.ed_yolo.clear()
            self.ed_yolo.setToolTip("")
            self._fill_params(Project())      # 参数也一起清空
            self.body.setCurrentIndex(0)
            self.editor.bind(None)
            self._refresh_programs()
            self._set_editable(False)
            self._refresh()
            self._say("已删除项目：%s　（保存地址里的文件、写好的测试程序都没动）"
                      % name, "ok")
        except Exception as e:
            traceback.print_exc()
            self._say("删除项目出错：%r" % e, "err")

    def _ask_delete(self, name):
        """删除前的确认框：说清楚删什么、不可复原、什么不会删。"""
        return self._confirm(
            "删除项目", "是否删除项目「%s」？" % name,
            "删除后不可复原：项目名称、保存地址和它记住的配置会一起消失，"
            "【打开项目】里也不会再出现。\n\n"
            "保存地址里的照片、引脚 json、日志、测试结果不会被删除；"
            "测试程序是所有项目共用的，也不会跟着项目删掉。",
            yes_text="删除", no_text="取消", danger=True)

    def _confirm(self, title, text, info="", yes_text="确定", no_text="取消",
                 danger=False):
        """自己拼一个确认框，别用 QMessageBox.question()。

        两个原因：按钮要显示中文（Qt 默认是英文 Yes/No）；按钮样式必须**逐个**
        设在按钮上 —— 只设在对话框上时，会被父页面继承来的 controls_qss 顶掉，
        按钮会退化成浅色系统样式，在深色界面里白得刺眼。
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
        box.setWindowTitle(title)
        box.setText(text)
        if info:
            box.setInformativeText(info)
        box.setStyleSheet(
            "QMessageBox{background:%s;}"
            "QLabel{color:%s;background:transparent;}"
            % (theme.PANEL_TOP, theme.TXT))

        btn_yes = box.addButton(
            yes_text, QMessageBox.DestructiveRole if danger else QMessageBox.AcceptRole)
        btn_yes.setStyleSheet(
            "QPushButton{background:%s;border:1px solid %s;border-radius:3px;"
            "color:%s;min-width:76px;min-height:26px;}"
            "QPushButton:hover{border-color:%s;}"
            % (("#fdf3f2", "#d59a95", "#e0908a", "#c0392b") if danger
               else ("#f4f9ff", "#c9dcec", "#cfe0ec", "#8fb6da")))
        btn_no = box.addButton(no_text, QMessageBox.RejectRole)
        btn_no.setStyleSheet(
            "QPushButton{background:#f4f9ff;border:1px solid #c9dcec;"
            "border-radius:3px;color:#cfe0ec;min-width:76px;min-height:26px;}"
            "QPushButton:hover{background:#e7f1fb;border-color:#8fb6da;}")
        for b in (btn_yes, btn_no):
            b.setCursor(Qt.PointingHandCursor)
        box.setDefaultButton(btn_no)          # 回车默认是取消，防手快
        box.exec_()
        return box.clickedButton() is btn_yes

    def _fields(self):
        """项目条上能改的字段，按顺序成元组 —— 存快照、判改动都用它。"""
        return (self.ed_name.text().strip(), self.ed_root.text().strip(),
                self.ed_yolo.text().strip(), self.ed_xc.text().strip(),
                self.cb_sxyz.currentText().strip(),
                self.cb_suvw.currentText().strip())

    def _dirty(self):
        """编辑模式下改过名字、地址、模型或程序表格，还没保存。"""
        if self.editor.is_dirty():
            return True
        if not self.editable or self.proj is None:
            return False
        return self._fields() != self._snap

    def _persist_programs(self):
        """程序库落盘。

        程序现在是全局的（`test.programs`），不再依赖项目有没有保存过 ——
        项目还没存也不影响程序写进配置。返回一句缀在提示后面的话（没有就是空串）。
        """
        self._sync_cfg()               # 先对准当前配置对象，再写
        ok, msg = self.reg.save()
        return "" if ok else "　（配置没写进去：%s，重启会丢）" % msg

    def _sync_cfg(self):
        """配置对象被换掉时跟着换（见 DeviceManager.save）。

        设备管理页保存参数走的是 `deepcopy(manager.cfg)` → `manager.save(cfg)`，
        整份配置会换成新对象。项目清单和程序库都挂在这份配置上，不跟着换就会往
        旧字典里写 —— 下一次落盘会把设备刚改的参数一起抹掉。

        这里**只换指向**，不重建清单：内存里的项目对象才是最新的（可能刚改过），
        程序库每次通过 self.lib 现取，也不会抱着一份旧列表。
        """
        if self.reg.cfg is self.mgr.cfg:
            return False
        self.reg.cfg = self.mgr.cfg
        return True

    def _confirm_discard(self):
        """离开当前编辑状态前问一句，别让改了一半的东西悄悄没了。"""
        if not self._dirty():
            return True
        if self.editor.is_dirty():
            return self._confirm(
                "还有没保存的修改",
                "测试程序改过但没点【保存程序】，继续就会丢掉这些改动。",
                "要留的话先点【取消】，再点【保存程序】。",
                yes_text="放弃修改", no_text="继续编辑", danger=True)
        return self._confirm(
            "还有没保存的修改",
            "当前的修改还没点【保存项目】，继续就会丢掉。",
            "要保存的话先点【取消】，再点【保存项目】。",
            yes_text="放弃修改", no_text="继续编辑", danger=True)

    def _set_editable(self, on):
        """只读 / 可编辑：输入框、浏览、保存、编辑四处一起变。"""
        self.editable = bool(on)
        self._snap = self._fields() if self.editable else ("", "", "", "", "", "")
        self.ed_name.setReadOnly(not self.editable)
        self.ed_name.setStyleSheet(
            "color:%s;" % (theme.TXT if self.editable else theme.TXT2))
        self.ed_root.setReadOnly(not self.editable)   # 编辑时可以直接粘贴路径
        self.ed_root.setStyleSheet(
            "color:%s;" % (theme.TXT if self.editable else theme.TXT2))
        self.ed_yolo.setReadOnly(not self.editable)   # 同上，模型路径也能粘
        # X 粗定位和两组步长：跟保存地址一个待遇，只读时全灰
        self.ed_xc.setReadOnly(not self.editable)
        self.ed_xc.setStyleSheet(
            "color:%s;" % (theme.TXT if self.editable else theme.TXT2))
        self.cb_sxyz.setEnabled(self.editable)
        self.cb_suvw.setEnabled(self.editable)
        tip_step = ("探针 XYZ 组轴走小步时的步长" if self.editable
                    else "只读：点【编辑项目】才能改")
        self.cb_sxyz.setToolTip(tip_step)
        self.cb_suvw.setToolTip(tip_step)
        self.btn_browse.setEnabled(self.editable)
        self.btn_yolo.setEnabled(self.editable)
        self.btn_yolo.setToolTip(
            "选这个项目要用的 yolo 模型文件" if self.editable
            else "只读：先点【编辑项目】才能换模型")
        self.btn_edit.setText("  编辑中" if self.editable else "  编辑项目")
        self.btn_save.setToolTip(
            "把项目名称和保存地址存进项目列表，之后【打开项目】里才有它"
            if self.editable else "只读：先点【编辑项目】才能改")
        self.btn_save.setEnabled(self.editable)
        self.editor.set_editable(self.editable)
        self._refresh_buttons()
        self._refresh_programs()      # 下拉的可用状态跟着只读状态变
        self._refresh_yolo()          # 模型那行的颜色/提示也跟着变

    def _refresh_buttons(self):
        """三个项目操作各自什么时候能用。

        * 保存项目：只在编辑状态可用（只有它会把改动落盘）；
        * 编辑项目：项目已保存、且当前不在编辑状态才可用；
        * 删除项目：项目已保存就能删（会弹确认框）。
        """
        saved = bool(self._orig)
        self.btn_edit.setEnabled(saved and not self.editable)
        self.btn_edit.setToolTip(
            "正在编辑：改完点【保存项目】" if self.editable else
            ("解锁项目名称、保存地址和测试程序表格" if saved
             else "先把项目点【保存项目】存下来"))
        self.btn_del.setEnabled(saved)
        self.btn_del.setToolTip(
            "从项目列表里删掉这个项目（保存地址里的文件不动）" if saved
            else "还没保存过的项目，没有可删的记录")

    # ------------------------------------------------------------ 测试程序下拉
    def _refresh_programs(self):
        """把**全局**程序库灌进【测试程序名称】下拉。

        程序不挂在项目下：在哪份项目里存下的程序，这里都能选到（用户明确要求）。
        只在"程序库变了"或"换了项目"时调（别在每次敲键时调，下拉会闪）。
        选中项跟着 ProgramEditor.pname 走，保存 / 改名 / 删除之后调一次就同步。
        """
        if not hasattr(self, "cb_prog"):
            return
        ed = getattr(self, "editor", None)
        cur = (ed.pname or "") if ed is not None else ""
        self._prog_loading = True
        try:
            self.cb_prog.clear()
            names = program.program_names(self.lib) if self.proj is not None else []
            if not names:
                self.cb_prog.addItem("（还没有保存的程序）", "")
            else:
                self.cb_prog.addItem("（未选择程序）", "")
                for name in names:
                    item = program.find_program(self.lib, name) or {}
                    rows = item.get("rows") or []
                    self.cb_prog.addItem(name, name)
                    self.cb_prog.setItemData(
                        self.cb_prog.count() - 1,
                        "测试程序：%s\n%d 行规则\n创建于 %s"
                        % (name, len(rows), item.get("created") or "—"),
                        Qt.ToolTipRole)
            i = self.cb_prog.findData(cur) if cur else 0
            self.cb_prog.setCurrentIndex(i if i >= 0 else 0)
        finally:
            self._prog_loading = False
        # 程序下拉跟着项目的只读状态走：打开项目只是"看"，选哪份程序等于改这个项目，
        # 所以要点【编辑项目】才解锁（用户明确要求）。禁用比"点了弹提示"更直观。
        can_pick = self.editable and bool(names)
        self.cb_prog.setEnabled(can_pick)
        if not names:
            tip = ("还没有保存过的程序：点右栏【新建程序】编写、再点【保存程序】"
                   if self.proj is not None else "先新建或打开一个项目")
        elif not self.editable:
            tip = "只读：点【编辑项目】后才能切换测试程序"
        else:
            tip = "下拉选择保存过的测试程序（所有项目共用），选中后表格自动读取它"
        self.cb_prog.setToolTip(tip)

    def _sync_prog_combo(self):
        """把下拉的选中项拉回 ProgramEditor 当前的程序（取消操作时用）。"""
        self._prog_loading = True
        try:
            name = self.editor.pname or ""
            i = self.cb_prog.findData(name) if name else 0
            self.cb_prog.setCurrentIndex(i if i >= 0 else 0)
        finally:
            self._prog_loading = False

    @property
    def lib(self):
        """全局程序库（配置里的 test.programs）。

        每次都从 self.mgr.cfg 现取 —— 设备管理页保存参数时整份配置会被换掉，
        抓着一份旧列表写进去，下一次落盘就丢了。
        """
        items, _ = program.open_library(self.mgr.cfg)
        return items

    def _remember_program(self, name):
        """记下"这个项目上次看的是哪份程序"，下次打开项目按它读回来。

        程序本身是全局的，项目里只存一个名字（Project.active）。选中即写盘 ——
        只读状态下点不了【保存项目】，不立刻记下来就等于白选。
        """
        if self.proj is None:
            return
        name = (name or "").strip()
        if getattr(self.proj, "active", "") == name:
            return
        self.proj.active = name
        if self._orig:                      # 没保存过的草稿项目，等【保存项目】一起写
            self._sync_cfg()
            ok, msg = self.reg.save()
            if not ok:
                self._say("选中的程序没写进配置：%s" % msg, "warn")

    def _forget_program(self, name):
        """程序被删了：把各项目里指向它的"上次选的程序"清掉。"""
        n = 0
        for p in self.reg.projects:
            if getattr(p, "active", "") == name:
                p.active = ""
                n += 1
        if self.proj is not None and getattr(self.proj, "active", "") == name:
            self.proj.active = ""
            n += 1
        return n

    def _on_pick_program(self, _i):
        """在下拉里选了程序：让右栏表格把它读进来。

        选程序 = 改"这个项目看哪份程序"，所以只在【编辑项目】状态下允许；
        只读时下拉是禁用的，这里再兜一次（防止键盘/程序化改动绕过）。
        """
        if self._prog_loading or self.proj is None:
            return
        if not self.editable:
            self._sync_prog_combo()
            self._say("只读：点【编辑项目】后才能切换测试程序。", "warn")
            return
        try:
            name = self.cb_prog.currentData() or ""
            if name == (self.editor.pname or ""):
                return
            if not name:
                # 选回"未选择"：等于把表格收起，同样要保护没保存的改动
                if self.editor.is_dirty() and not self._confirm(
                        "还有没保存的修改",
                        "当前程序改过但没点【保存程序】，收起会丢掉这些改动。",
                        "要留的话先点【取消】，再点【保存程序】。",
                        yes_text="放弃修改", no_text="继续编辑", danger=True):
                    self._sync_prog_combo()
                    return
                self.editor.bind(self.proj)
                self._remember_program("")          # 下次打开项目就是空的
                self._select_stage(0)
                self._say("已收起当前程序，表格清空了。", "info")
                return
            if not self.editor.open(name):
                self._sync_prog_combo()     # 取消 / 找不到：下拉退回去
                return
            self._remember_program(name)    # 记住，重开项目时自动读回来
            self._select_stage(0)           # 选了程序就切到"测试程序编写"那栏
        except Exception as e:
            traceback.print_exc()
            self._say("选测试程序出错：%r" % e, "err")

    # ------------------------------------------------------------ 刷新
    def _refresh(self):
        p = self.proj
        saved = bool(self._orig)
        if p is None:
            self.lb_state.setText("● 未打开项目")
            self.lb_state.setToolTip("")
            self.lb_state.setStyleSheet(
                "color:%s;background:transparent;" % theme.TXT3)
        elif not saved:
            self.lb_state.setText("● 新建中　未保存")
            self.lb_state.setToolTip("点【保存项目】才会出现在【打开项目】里")
            self.lb_state.setStyleSheet(
                "color:%s;background:transparent;" % theme.WARN)
        elif self.editable:
            self.lb_state.setText("● 编辑中　%s" % p.name)
            self.lb_state.setToolTip("改完点【保存项目】")
            self.lb_state.setStyleSheet(
                "color:%s;background:transparent;" % theme.WARN)
        elif not p.root_ok:
            self.lb_state.setText("● 地址失效　%s" % p.name)
            self.lb_state.setToolTip(p.root)
            self.lb_state.setStyleSheet(
                "color:%s;background:transparent;" % theme.WARN)
        else:
            self.lb_state.setText("● 已打开　%s" % p.name)
            self.lb_state.setToolTip("%s\n只读：点【编辑项目】才能改" % p.root)
            self.lb_state.setStyleSheet(
                "color:%s;background:transparent;" % theme.OK)
        self._refresh_buttons()
        self._refresh_caption()
        self._refresh_yolo()
        self._refresh_xc_range()      # 限位可能刚在设备管理页改过

    def _refresh_yolo(self):
        """yolo 模型那行的颜色和提示：空着灰、找得到正常色、找不到标红。"""
        if not hasattr(self, "ed_yolo"):
            return
        p = self.ed_yolo.text().strip()
        if not p:
            self.ed_yolo.setStyleSheet(
                "color:%s;border-color:#d5e4f1;" % theme.TXT3)
            self.ed_yolo.setToolTip(
                "还没选 yolo 模型" if self.editable
                else "还没选 yolo 模型（点【编辑项目】后可选）")
            return
        if os.path.isfile(p):
            self.ed_yolo.setStyleSheet(
                "color:%s;border-color:#d5e4f1;"
                % (theme.TXT if self.editable else theme.TXT2))
            try:
                mb = os.path.getsize(p) / 1024.0 / 1024.0
                size = "%.1f MB" % mb
            except OSError:
                size = "大小读不到"
            self.ed_yolo.setToolTip("%s\n%s" % (p, size))
        else:
            self.ed_yolo.setStyleSheet(
                "color:%s;border-color:#d59a95;" % theme.WARN)
            self.ed_yolo.setToolTip("%s\n这个文件不在了，点【选择模型】重选" % p)

    def _refresh_caption(self):
        """地址下面那行小字：下次自动测试会建哪个目录、这个地址里已有几次记录。"""
        root = self.ed_root.text().strip()
        if not root:
            self.lb_next.setText("四个子文件夹：%s" % " / ".join(SUB_DIRS))
            self.lb_next.setStyleSheet(
                "color:%s;background:transparent;" % theme.AMBER_DIM)
            self.lb_next.setToolTip("每次自动测试会建一个文件夹（名字是"
                                    "「板号.检修状态.序号」，在自动测试页填板号后自动算），"
                                    "里面固定这四个子目录")
            self.lb_count.setText("")
            return
        if not os.path.isdir(root):
            self.lb_next.setText("保存地址不存在，重新选一个文件夹")
            self.lb_next.setStyleSheet(
                "color:%s;background:transparent;" % theme.WARN)
            self.lb_next.setToolTip(root)
            self.lb_count.setText("")
            return
        self.lb_next.setStyleSheet(
            "color:%s;background:transparent;" % theme.AMBER_DIM)
        # 目录名 = 板号.检修状态.序号。板号和检修状态都是在自动测试页填的，
        # 这里预览"下次会建哪个目录"要一起带上，否则明明填了板号、选了修后，
        # 界面上还显示一条对不上的名字。
        p = Project("", root,
                    board=(self.proj.board if self.proj else ""),
                    repair=(self.proj.repair if self.proj else ""))
        full = p.preview_session()
        n = len(p.sessions())
        self.lb_count.setText("已有 %d 次记录" % n if n else "")
        if not full:
            # 没填板号 → 三段凑不齐，也就没有"下次的目录名"可预览
            self.lb_next.setText("填板号后自动生成目录名（板号.检修状态.序号）")
            self.lb_next.setToolTip(
                "板号在自动测试页的项目卡里填（回车保存）；没板号不能开跑。")
            self.lb_count.setToolTip("保存地址：%s" % root)
            return
        _folder, name = os.path.split(full)
        self.lb_next.setText("下次自动测试：%s\\" % name)
        self.lb_next.setToolTip(full)
        self.lb_count.setToolTip("保存地址：%s" % root)

    def _select_stage(self, i):
        for k, c in enumerate(self.cards):
            c.set_active(k == i)
        self.stack.setCurrentIndex(i)

    def _say(self, text, level="info"):
        colors = {"info": theme.TXT2, "ok": theme.OK,
                  "warn": theme.WARN, "err": theme.BAD}
        self.info.setText(text)
        self.info.setStyleSheet("color:%s;background:transparent;"
                                % colors.get(level, theme.TXT2))

    # ================================================================ 生命周期
    def showEvent(self, e):
        super(TestPage, self).showEvent(e)
        if self._sync_cfg():
            self._refresh_programs()
        # 「矩形框」列的下拉候选跟着「视觉定位 → 类别管理」走：
        # 每次显示都刷一遍，从视觉页新增/删掉类别回来立刻生效
        self.editor.refresh_labels()
        if not self._inited:
            self._inited = True
            self._select_stage(0)
        self._refresh_caption()
        self._refresh_xc_range()      # 限位可能刚在设备管理页改过
        self._clock.start()

    def hideEvent(self, e):
        self._clock.stop()
        super(TestPage, self).hideEvent(e)
