# -*- coding: utf-8 -*-
"""测试程序编写：7 列表格 + 程序的新建 / 保存 / 删除。

    ┌ 测试程序编写                              ● 已保存　主板 A 程序 ┐
    │ [新建程序] │ 程序名称[______] [保存程序] [删除程序]             │
    ├───────────────────────────────────────────────┬────────┤
    │ 矩形框│识别方式│点位范围│点名范围│点名│点位│测试类别 │ 新增行 │
    │   O   │ 全读取 │ 指定点 │  全部  │    │ 1  │   V    │ 删减行 │
    ├───────────────────────────────────────────────┴────────┤
    │ 备注：一行 = 一条读取规则 …                                │
    └─────────────────────────────────────────────────────────┘

**选程序**不在这一条上：打开哪份程序由页面顶部的【测试程序名称】下拉负责
（程序属于项目，跟项目名挨着才顺），选中后调本类的 open() 把表格读进来。

几条交互规则（跟项目条那套保持一致）：

* **只有点【保存程序】才进清单** —— 改完表格、改完名字都是草稿，状态标「未保存」，
  顶部【测试程序名称】下拉里看不到；
* **表格跟着项目的只读状态** —— 打开项目 / 打开程序后默认只读，表格、下拉、
  增删行全灰，只能看；点项目条上的【编辑项目】才解锁；
* 列与列之间是联动的，跟 Excel 那份示例一样：
  `识别方式=不读取` → 后面几列清空并禁用；`点位范围=全部` → 点位清空；
  `点名范围=全部` → 点名清空。省得写出"不读还要填引脚号"这种自相矛盾的行。

列的语义见 app/program.py 顶部注释。
"""

import traceback
from datetime import datetime

from PyQt5.QtCore import QEvent, QSize, Qt
from PyQt5.QtGui import QBrush, QColor, QIcon
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                            QPushButton, QLineEdit, QComboBox, QFrame,
                            QTableWidget, QTableWidgetItem,
                            QHeaderView, QAbstractItemView)

from .. import icons, program, theme
from ..widgets import Panel, _font

# 单元格里的小下拉：比通用控件更紧凑，边框压暗，像"可选的格子"而不是一排按钮
CELL_QSS = """
QComboBox {
    background: #f2f7fb; border: 1px solid #dbe6f1; border-radius: 2px;
    color: #1f3750; padding: 1px 4px; font-size: 11px;
}
QComboBox:hover { border-color: #8fb6da; }
QComboBox:focus { border-color: #8fb6da; }
QComboBox:disabled {
    color: #8ba1b5; background: #f6fafd; border-color: #eef5fb;
}
QComboBox::drop-down { border: none; width: 14px; }
QComboBox QAbstractItemView {
    background: #ffffff; border: 1px solid #c2d6e8; color: #1f3750;
    selection-background-color: #e6f0fa; outline: none;
}
"""

# 列宽：7 列加起来约 700，1440 窗口下右栏还有富余
COL_W = (86, 96, 96, 96, 118, 108, 96)


class _Row(object):
    """一行：数据 + 这一行的控件。

    控件按 key 存着，切只读/联动时不用去表里按坐标找。
    """

    __slots__ = ("spec", "combos", "items")

    def __init__(self, spec):
        self.spec = spec
        self.combos = {}
        self.items = {}


class ProgramEditor(QWidget):
    """测试程序编写工作区。挂在 TestPage 上，proj / 只读状态都由页面给。"""

    def __init__(self, page, parent=None):
        super(ProgramEditor, self).__init__(parent)
        self.page = page                        # TestPage，读 proj、说话、弹确认框
        self.proj = None
        self.pname = None                       # 当前程序名；None = 还没保存过
        self.rows = []                          # [_Row]，下标 = 表格行号
        self._loading = False                   # 程序化写控件时挡掉信号
        self._dirty = False
        self._fresh = False                     # 点了【新建程序】但还什么都没填
        self.editable = False

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(7)

        v.addWidget(self._build_head())
        v.addWidget(self._build_bar())
        v.addWidget(self._build_body(), 1)
        v.addWidget(self._build_note())

    # ================================================================ 搭建
    def _build_head(self):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 0, 2, 0)
        h.setSpacing(8)

        t = QLabel("测试程序编写")
        t.setFont(_font(theme.F_BODY, 13, bold=True, spacing=0.6))
        t.setStyleSheet("color:%s;background:transparent;" % theme.TXT)
        h.addWidget(t)
        h.addStretch(1)

        self.lb_pstate = QLabel("● 无项目")
        self.lb_pstate.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        self.lb_pstate.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        h.addWidget(self.lb_pstate)
        return w

    def _build_bar(self):
        w = QWidget()
        w.setObjectName("progBar")
        w.setFixedHeight(44)
        w.setStyleSheet(
            "#progBar{background:rgba(27,87,160,0.03);"
            "border:1px solid #dbe6f1;border-radius:4px;}")
        h = QHBoxLayout(w)
        h.setContentsMargins(10, 0, 12, 0)
        h.setSpacing(8)

        self.btn_new = QPushButton("新建程序")
        self.btn_new.setObjectName("primary")
        self.btn_new.setFixedSize(84, 28)
        self.btn_new.setCursor(Qt.PointingHandCursor)
        self.btn_new.setToolTip("清空表格，起个名字后点【保存程序】")
        self.btn_new.clicked.connect(self._on_new)
        h.addWidget(self.btn_new)

        # 打开程序的下拉搬到上面那条项目信息里（【测试程序名称】）：
        # 程序现在是全局的（所有项目共用一套），选哪份程序跟"当前是哪个项目"无关，
        # 放在项目条的同一行更好找；两处各放一个一模一样的下拉只会让人犹豫。
        sep = QFrame()
        sep.setFixedSize(1, 20)
        sep.setStyleSheet("background:rgba(27,87,160,0.10);border:none;")
        h.addWidget(sep)

        lb = QLabel("程序名称")
        lb.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        lb.setFixedWidth(48)
        lb.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        h.addWidget(lb)

        self.ed_name = QLineEdit()
        self.ed_name.setFixedHeight(28)
        self.ed_name.setMinimumWidth(96)
        self.ed_name.setFont(_font(theme.F_BODY, 11, spacing=0.2))
        self.ed_name.setPlaceholderText("给这份程序起个名字")
        self.ed_name.textChanged.connect(self._on_name_typed)
        h.addWidget(self.ed_name, 1)

        self.btn_save = QPushButton("  保存程序")
        self.btn_save.setObjectName("primary")
        self.btn_save.setFixedSize(92, 28)
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.setIcon(QIcon(icons.icon(
            "save", theme.AMBER2, 13, self.devicePixelRatioF())))
        self.btn_save.setIconSize(QSize(13, 13))
        self.btn_save.setToolTip("存进本项目；之后顶部【测试程序名称】里才会出现")
        self.btn_save.clicked.connect(self._on_save)
        h.addWidget(self.btn_save)

        self.btn_del = QPushButton("  删除程序")
        self.btn_del.setFixedSize(92, 28)
        self.btn_del.setCursor(Qt.PointingHandCursor)
        self.btn_del.setIcon(QIcon(icons.icon(
            "trash", theme.BAD, 13, self.devicePixelRatioF())))
        self.btn_del.setIconSize(QSize(13, 13))
        self.btn_del.setStyleSheet(
            "QPushButton{background:#fdf3f2;border:1px solid #d59a95;"
            "border-radius:3px;color:#c0392b;font-size:11px;padding:0 6px;}"
            "QPushButton:hover{background:#fdf3f2;border-color:#c0392b;"
            "color:#c0392b;}"
            "QPushButton:disabled{color:#e0b4b0;background:#fdf3f2;"
            "border-color:#fdf3f2;}")
        self.btn_del.clicked.connect(self._on_delete)
        h.addWidget(self.btn_del)
        return w

    def _build_body(self):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)

        self.tb = QTableWidget(0, len(program.COLUMNS))
        self.tb.setHorizontalHeaderLabels(list(program.COLUMNS))
        self.tb.verticalHeader().setDefaultSectionSize(30)
        self.tb.verticalHeader().setFixedWidth(34)
        self.tb.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.tb.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tb.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tb.setAlternatingRowColors(True)
        self.tb.setShowGrid(True)
        self.tb.setWordWrap(False)
        self.tb.setCornerButtonEnabled(False)
        self.tb.setFocusPolicy(Qt.StrongFocus)
        self.tb.setEditTriggers(QAbstractItemView.DoubleClicked
                                | QAbstractItemView.SelectedClicked
                                | QAbstractItemView.EditKeyPressed)
        hd = self.tb.horizontalHeader()
        hd.setHighlightSections(False)
        hd.setStretchLastSection(True)
        hd.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for i, wd in enumerate(COL_W):
            self.tb.setColumnWidth(i, wd)
        self.tb.cellChanged.connect(self._on_cell_changed)
        h.addWidget(self.tb, 1)

        # 空表时给一句引导，不然一片空网格不知道从哪下手
        self.lb_empty = QLabel(
            "还没有规则\n点右边【新增行】加一条，或点顶部【测试程序名称】选一份已保存的",
            self.tb.viewport())
        self.lb_empty.setAlignment(Qt.AlignCenter)
        self.lb_empty.setFont(_font(theme.F_BODY, 10, spacing=0.6))
        self.lb_empty.setStyleSheet(
            "color:#8ba1b5;background:transparent;")
        self.lb_empty.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.lb_empty.hide()
        self.tb.viewport().installEventFilter(self)

        # 右侧增删行：竖排两个小按钮，和表格同高不抢地方
        col = QWidget()
        col.setFixedWidth(78)
        cv = QVBoxLayout(col)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(6)

        self.btn_add = QPushButton("  新增行")
        self.btn_add.setFixedHeight(28)
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_add.setToolTip("在表格末尾加一行")
        self.btn_add.setIcon(QIcon(icons.icon(
            "plus", theme.OK, 13, self.devicePixelRatioF())))
        self.btn_add.setIconSize(QSize(13, 13))
        self.btn_add.setStyleSheet(_row_btn_qss(theme.OK))
        self.btn_add.clicked.connect(self._on_add_row)
        cv.addWidget(self.btn_add)

        self.btn_sub = QPushButton("  删减行")
        self.btn_sub.setFixedHeight(28)
        self.btn_sub.setCursor(Qt.PointingHandCursor)
        self.btn_sub.setToolTip("删掉当前选中的那一行")
        self.btn_sub.setIcon(QIcon(icons.icon(
            "minus", theme.BAD, 13, self.devicePixelRatioF())))
        self.btn_sub.setIconSize(QSize(13, 13))
        self.btn_sub.setStyleSheet(_row_btn_qss(theme.BAD))
        self.btn_sub.clicked.connect(self._on_sub_row)
        cv.addWidget(self.btn_sub)
        cv.addStretch(1)
        h.addWidget(col, 0)
        return w

    def _build_note(self):
        self.lb_note = QLabel()
        self.lb_note.setWordWrap(True)
        self.lb_note.setFont(_font(theme.F_BODY, 9, spacing=0.3))
        self.lb_note.setStyleSheet(
            "color:%s;background:rgba(27,87,160,0.03);"
            "border-left:2px solid #b8c9d8;padding:5px 8px;"
            "border-radius:2px;" % theme.TXT3)
        self._refresh_note()
        return self.lb_note

    def _refresh_note(self):
        """备注里的标签清单跟着类别管理走（新增的类别会自动列进来）。"""
        hints = program.label_hints()
        new = [h for h in hints if h not in program.LABEL_HINTS]
        extra = ""
        if new:
            extra = ("其中 <b style='color:#17497f'>%s</b> 是「视觉定位 → 类别管理」里"
                     "新增的类别，增删会同步到这一列。" % " / ".join(new))
        self.lb_note.setText(
            "<b style='color:#17497f'>备注</b>　一行 = 一条读取规则。"
            "「矩形框」填芯片标签（%s），与「视觉定位 → 类别管理」的类别一致；"
            "「识别方式」选<b style='color:#5b7186'>不读取</b>就整类跳过，后面几列会自动清空；"
            "「点位范围」选<b style='color:#5b7186'>指定点</b>后，在「点位」列写引脚号"
            "（如 1、1-8、4,22，逗号分隔）；"
            "「点名范围」选<b style='color:#5b7186'>指定</b>后，在「点名」列写具体芯片（如 L_1）。"
            "表里没出现的标签默认不读取；测试类别目前只有 V（电压）和 f（频率）。%s"
            % (" / ".join(hints), extra))

    # ================================================================ 绑定项目
    def bind(self, proj):
        """换项目时调：清空表格，切到这个项目（可能还没有程序）。"""
        self.proj = proj
        self.pname = None
        self._dirty = False
        self._fresh = False
        self._loading = True
        self.ed_name.clear()
        self._clear_rows()
        self._loading = False
        self._refresh_state()
        self.page._refresh_programs()

    def set_project(self, proj):
        """只换项目引用，不动表格 —— 保存项目后对象会换成清单里那一个。"""
        self.proj = proj
        self._refresh_state()

    @property
    def lib(self):
        """全局程序库（配置里的 test.programs）。

        程序不挂在项目下：哪个项目里写好的程序，别的项目也能选到。
        """
        return self.page.lib

    def set_editable(self, on):
        self.editable = bool(on)
        for r in self.rows:
            self._apply_state(r)
        self.btn_new.setEnabled(self.editable)
        self.btn_add.setEnabled(self.editable)
        self.btn_sub.setEnabled(self.editable)
        self.btn_save.setEnabled(self.editable)
        self.btn_del.setEnabled(self.editable and bool(self.pname))
        self.ed_name.setReadOnly(not self.editable)
        self._refresh_state()

    def _fill_labels(self, cb):
        """把「矩形框」的候选灌进下拉：内置 6 类 + 类别管理里新增的类别。

        每项挂一句说明（悬停能看到"哪套算法 · 每排几脚 · 共几点"），
        这样填 Y 这种自定义类别时，不用回视觉页确认它到底几脚。
        """
        names = list(program.label_hints())
        cb.clear()
        for name in names:
            cb.addItem(name)
            tip = program.label_desc(name)
            if tip:
                cb.setItemData(cb.count() - 1, tip, Qt.ToolTipRole)
        cb.setCurrentIndex(-1)
        cb.setToolTip("芯片标签：%s\n与「视觉定位 → 类别管理」的类别同步。"
                      % " / ".join(names))

    def refresh_labels(self):
        """把「矩形框」列的候选刷成当前的类别清单（视觉定位 → 类别管理改了类别之后）。

        三条约束：
          * **已填的文字原样保留** —— 删掉一个类别不该把老程序里的格子清空，
            那份程序还得能打开、能存；
          * 只动候选项，不碰 spec，`_dirty` 不变（这不是一次编辑）；
          * 每次页面显示时都调一次（TestPage.showEvent），所以从视觉页回来
            立刻就是新的候选，不依赖谁记得发信号。
        """
        old = self._loading
        self._loading = True
        try:
            for r in self.rows:
                cb = r.combos.get(program.C_LABEL)
                if cb is None:
                    continue
                val = r.spec.get(program.C_LABEL) or cb.currentText()
                self._fill_labels(cb)
                cb.setEditText(val)
        finally:
            self._loading = old
        self._refresh_note()

    def is_dirty(self):
        return bool(self.editable and self._dirty)

    # ================================================================ 行
    def _clear_rows(self):
        self.rows = []
        self.tb.setRowCount(0)

    def _insert_row(self, at, data=None):
        spec = program.blank_row()
        if data:
            spec.update({k: str(data.get(k) or "") for k in program.COLUMNS})
        r = _Row(spec)
        self.rows.insert(at, r)

        self._loading = True
        try:
            self.tb.insertRow(at)

            r.combos[program.C_LABEL] = self._combo(
                msg="矩形框", editable=True)
            self._fill_labels(r.combos[program.C_LABEL])
            r.combos[program.C_READ] = self._combo(
                values=program.READ_MODES)
            r.combos[program.C_POINT_MODE] = self._combo(
                values=program.POINT_MODES)
            r.combos[program.C_NAME_SCOPE] = self._combo(
                values=program.NAME_SCOPES)
            r.combos[program.C_CATEGORY] = self._combo(
                values=program.CATEGORIES)

            for key, cb in r.combos.items():
                # 用 currentTextChanged 而不是 currentIndexChanged：矩形框那个下拉是可输入的，
                # 手打/程序设值时 currentIndex 可能一直是 -1，只有这条信号能拿到文字
                cb.currentTextChanged.connect(
                    lambda _t, rr=r, k=key, c=cb: self._on_combo(rr, k, c))
                col = program.COLUMNS.index(key)
                self.tb.setCellWidget(at, col, _cell(cb))

            for key in (program.C_CHIP_NAME, program.C_PIN_SPEC):
                it = QTableWidgetItem("")
                it.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                self.tb.setItem(at, program.COLUMNS.index(key), it)
                r.items[key] = it
        finally:
            self._loading = False

        self._sync_widgets(r)
        self._renumber()
        return r

    def _remove_row(self, at):
        r = self.rows.pop(at)
        self._loading = True
        try:
            self.tb.removeRow(at)
        finally:
            self._loading = False
        r.combos.clear()
        r.items.clear()
        self._renumber()

    def _renumber(self):
        for i in range(self.tb.rowCount()):
            it = QTableWidgetItem(str(i + 1))
            it.setTextAlignment(Qt.AlignCenter)
            it.setForeground(QBrush(QColor("#8ba1b5")))
            it.setFlags(Qt.ItemIsEnabled)
            self.tb.setVerticalHeaderItem(i, it)

    def _combo(self, values=(), editable=False, msg=""):
        cb = QComboBox()
        cb.setFont(_font(theme.F_BODY, 10, spacing=0.1))
        cb.setFixedHeight(24)
        cb.setStyleSheet(CELL_QSS)
        cb.setCursor(Qt.ArrowCursor)
        if editable:
            cb.setEditable(True)
            cb.setInsertPolicy(QComboBox.NoInsert)
            if cb.lineEdit() is not None:
                cb.lineEdit().setFont(_font(theme.F_BODY, 10, spacing=0.1))
                cb.lineEdit().setPlaceholderText(msg)
        if values:
            cb.addItems(list(values))
        cb.setCurrentIndex(-1)
        return cb

    def _row_of(self, r):
        for i, x in enumerate(self.rows):
            if x is r:
                return i
        return -1

    # ================================================================ 联动
    def _apply_state(self, r):
        """按这一行的取值决定哪些格子能用（只在可编辑时）。"""
        e = self.editable
        read = r.spec.get(program.C_READ) != program.READ_NONE
        one = r.spec.get(program.C_NAME_SCOPE) == program.SCOPE_ONE
        spec = r.spec.get(program.C_POINT_MODE) == program.POINT_SPEC

        r.combos[program.C_READ].setEnabled(e)
        r.combos[program.C_LABEL].setEnabled(e and read)
        r.combos[program.C_POINT_MODE].setEnabled(e and read)
        r.combos[program.C_NAME_SCOPE].setEnabled(e and read)
        r.combos[program.C_CATEGORY].setEnabled(e and read)
        self._item_editable(r, program.C_CHIP_NAME, e and read and one)
        self._item_editable(r, program.C_PIN_SPEC, e and read and spec)

    def _item_editable(self, r, key, on):
        it = r.items.get(key)
        if it is None:
            return
        f = it.flags()
        if on:
            f |= Qt.ItemIsEditable
        else:
            f &= ~Qt.ItemIsEditable
        it.setFlags(f)
        it.setForeground(QBrush(QColor(theme.TXT if on else "#8ba1b5")))

    def _sync_widgets(self, r):
        """把 spec 的值写回控件（程序化写，挡信号）。"""
        old = self._loading
        self._loading = True
        try:
            for key, cb in r.combos.items():
                val = r.spec.get(key) or ""
                if cb.isEditable():
                    cb.setCurrentIndex(-1)
                    cb.setEditText(val)
                else:
                    cb.setCurrentIndex(cb.findText(val))
            for key, it in r.items.items():
                it.setText(r.spec.get(key) or "")
        finally:
            self._loading = old
        self._apply_state(r)

    def _on_combo(self, r, key, cb):
        if self._loading:
            return
        try:
            val = cb.currentText().strip()
            if r.spec.get(key) == val:
                return
            r.spec[key] = val
            if key == program.C_READ and val == program.READ_NONE:
                for k in (program.C_POINT_MODE, program.C_NAME_SCOPE,
                          program.C_CHIP_NAME, program.C_PIN_SPEC,
                          program.C_CATEGORY):
                    r.spec[k] = ""
                self._sync_widgets(r)
            elif key == program.C_POINT_MODE and val == program.POINT_ALL:
                r.spec[program.C_PIN_SPEC] = ""
                self._sync_widgets(r)
            elif key == program.C_NAME_SCOPE and val == program.SCOPE_ALL:
                r.spec[program.C_CHIP_NAME] = ""
                self._sync_widgets(r)
            else:
                self._apply_state(r)
            self._touch()
        except Exception as e:
            traceback.print_exc()
            self._say("改表格出错：%r" % e, "err")

    def _on_cell_changed(self, row, col):
        if self._loading or row < 0 or row >= len(self.rows):
            return
        try:
            key = program.COLUMNS[col]
            it = self.tb.item(row, col)
            if it is None:
                return
            r = self.rows[row]
            val = it.text().strip()
            if r.spec.get(key) == val:
                return
            r.spec[key] = val
            self._touch()
        except Exception as e:
            traceback.print_exc()
            self._say("改表格出错：%r" % e, "err")

    def _on_name_typed(self, _t):
        if self._loading:
            return
        self._refresh_state()

    def _touch(self):
        self._dirty = True
        self._refresh_state()

    # ================================================================ 增删行
    def _on_add_row(self):
        try:
            if not self.editable:
                self._say("只读：先点【编辑项目】才能改程序。", "warn")
                return
            # 一律加在末尾：编写程序是"一条条往下加"，插在选中行后面反而容易加错位置
            at = len(self.rows)
            self._insert_row(at)
            self.tb.setCurrentCell(at, 0)
            self.tb.scrollTo(self.tb.model().index(at, 0))
            self._touch()
            self._say("加了第 %d 行。" % (at + 1), "info")
        except Exception as e:
            traceback.print_exc()
            self._say("新增行出错：%r" % e, "err")

    def _on_sub_row(self):
        try:
            if not self.editable:
                self._say("只读：先点【编辑项目】才能改程序。", "warn")
                return
            at = self.tb.currentRow()
            if at < 0:
                self._say("先在表格里点一行，再点【删减行】。", "warn")
                return
            self._remove_row(at)
            if self.rows:
                self.tb.setCurrentCell(min(at, len(self.rows) - 1), 0)
            self._touch()
            self._say("删掉了第 %d 行。" % (at + 1), "info")
        except Exception as e:
            traceback.print_exc()
            self._say("删减行出错：%r" % e, "err")

    # ================================================================ 程序
    def _on_new(self):
        try:
            if not self.editable:
                self._say("只读：先点【编辑项目】才能新建程序。", "warn")
                return
            if self.is_dirty() and not self.page._confirm(
                    "还有没保存的修改",
                    "当前程序改过但没点【保存程序】，新建会丢掉这些改动。",
                    "要留的话先点【取消】，再点【保存程序】。",
                    yes_text="放弃修改", no_text="继续编辑", danger=True):
                return
            self.pname = None
            self._loading = True
            self.ed_name.clear()
            self._loading = False
            self._clear_rows()
            self._dirty = False
            self._fresh = True
            self._refresh_state()
            self.page._remember_program("")     # 新程序还没定名，先别记旧的
            self.page._refresh_programs()
            self.ed_name.setFocus()
            self._say("新建程序：起个名字，点【新增行】加规则，最后点【保存程序】。", "info")
        except Exception as e:
            traceback.print_exc()
            self._say("新建程序出错：%r" % e, "err")

    def open(self, name):
        """读进一份保存过的程序。返回 True = 读进来了，False = 取消 / 找不到。

        页面顶部那个【测试程序名称】下拉就是调它 —— 读进来只是"看"，
        能不能改不在这里判，看项目有没有点【编辑项目】。
        """
        item = program.find_program(self.lib, name)
        if item is None:
            self._say("程序清单里找不到「%s」。" % name, "err")
            return False
        if self.is_dirty() and not self.page._confirm(
                "还有没保存的修改",
                "当前程序改过但没点【保存程序】，打开别的会丢掉这些改动。",
                "要留的话先点【取消】，再点【保存程序】。",
                yes_text="放弃修改", no_text="继续编辑", danger=True):
            return False
        self.pname = name
        self._loading = True
        self.ed_name.setText(name)
        self._clear_rows()
        self._loading = False
        rows = item.get("rows") or []
        for i, data in enumerate(rows):
            self._insert_row(i, data)
        self._dirty = False
        self._fresh = False
        self._refresh_state()
        if rows:
            self.tb.setCurrentCell(0, 0)
        self.page._refresh_programs()
        self._say("已打开程序：%s（%d 行）%s"
                  % (name, len(rows),
                     "" if self.editable else "　只读，要改先点【编辑项目】"),
                  "ok")
        return True

    def _on_save(self):
        """【保存程序】：只有这里会把表格写进程序库（所有项目共用）。"""
        try:
            if not self.editable:
                self._say("只读：先点【编辑项目】才能保存程序。", "warn")
                return False
            if self.proj is None:
                self._say("先新建或打开一个项目，再写程序。", "warn")
                return False
            name = self.ed_name.text().strip()
            if not name:
                self._say("先给程序起个名字，否则顶部【测试程序名称】里没法选。", "warn")
                self.ed_name.setFocus()
                return False
            other = program.find_program(self.lib, name)
            if other is not None and name != self.pname:
                self._say("已经有同名程序「%s」了（程序在所有项目里共用），换个名字。" % name,
                          "warn")
                self.ed_name.setFocus()
                self.ed_name.selectAll()
                return False
            if not self.rows:
                self._say("程序还是空的：先点【新增行】加一条规则再保存。", "warn")
                return False

            created = datetime.now().strftime("%Y-%m-%d %H:%M")
            program.put_program(self.lib, name, self._rows_data(),
                                old_name=self.pname, created=created)
            self.pname = name
            self._dirty = False
            self._fresh = False
            note = self.page._persist_programs()
            self.page._remember_program(name)   # 这个项目下次打开就读它
            self._refresh_state()
            self.page._refresh_programs()      # 顶部下拉立刻能看到这份程序

            errs = program.validate(self._rows_data())
            if errs:
                first = errs[0]
                more = "" if len(errs) == 1 else "　等 %d 处" % len(errs)
                self._say("已保存：%s（%d 行）。但第 %d 行 %s%s，跑自动测试前先补上。%s"
                          % (name, len(self.rows), first[0], first[1], more, note),
                          "warn")
            else:
                self._say("已保存程序：%s（%d 行），顶部【测试程序名称】里可以选到它了。%s"
                          % (name, len(self.rows), note), "ok")
            return True
        except Exception as e:
            traceback.print_exc()
            self._say("保存程序出错：%r" % e, "err")
            return False

    def _on_delete(self):
        try:
            if not self.pname:
                self._say("还没有保存过的程序可删。", "warn")
                return
            name = self.pname
            if not self.page._confirm(
                    "删除程序", "是否删除程序「%s」？" % name,
                    "删除后不可复原：这份程序的所有行都会消失，"
                    "顶部【测试程序名称】下拉里也不会再出现。\n\n"
                    "项目本身、保存地址里的测试结果都不受影响。",
                    yes_text="删除", no_text="取消", danger=True):
                return
            program.delete_program(self.lib, name)
            self.page._forget_program(name)      # 各项目别再记着这份程序
            note = self.page._persist_programs()
            self.pname = None
            self._dirty = False
            self._fresh = False
            self._loading = True
            self.ed_name.clear()
            self._loading = False
            self._clear_rows()
            self._refresh_state()
            self.page._refresh_programs()
            self._say("已删除程序：%s%s" % (name, note), "ok")
        except Exception as e:
            traceback.print_exc()
            self._say("删除程序出错：%r" % e, "err")

    # ================================================================ 对外
    def flush(self):
        """把表格当前内容写进程序库，供【保存项目】顺手落盘（不校验、不弹提示）。

        返回 "none"（没改动）/ "saved"（存了）/ "unnamed"（有内容但没起名字）。
        """
        if not self.editable or self.proj is None or not self._dirty:
            return "none"
        name = self.ed_name.text().strip()
        if not name:
            return "unnamed"
        program.put_program(self.lib, name, self._rows_data(),
                            old_name=self.pname,
                            created=datetime.now().strftime("%Y-%m-%d %H:%M"))
        self.pname = name
        self.proj.active = name        # 顺手记成"这个项目选的是它"，跟项目一起存
        self._fresh = False
        self._dirty = False
        return "saved"

    def _rows_data(self):
        return [dict(r.spec) for r in self.rows]

    def read_count(self):
        return sum(1 for r in self.rows if program.is_read(r.spec))

    # ================================================================ 状态
    def eventFilter(self, obj, e):
        # 表格铺满整块区域，空行提示要跟着视口改大小
        if obj is self.tb.viewport() and e.type() == QEvent.Resize:
            self._place_empty()
        return super(ProgramEditor, self).eventFilter(obj, e)

    def _place_empty(self):
        self.lb_empty.setGeometry(self.tb.viewport().rect())
        self.lb_empty.setVisible(not self.rows and self.proj is not None)
        if self.editable:
            self.lb_empty.setText(
                "还没有规则\n点右边【新增行】加一条，或点顶部【测试程序名称】选一份已保存的")
        else:
            self.lb_empty.setText(
                "没有要显示的程序\n点顶部【测试程序名称】选一份，或点【编辑项目】新建")
        self.lb_empty.raise_()

    def _refresh_state(self):
        n, m = len(self.rows), self.read_count()
        self.btn_del.setEnabled(self.editable and bool(self.pname))
        name = self.ed_name.text().strip()
        if self.proj is None:
            text, color, tip = "● 无项目", theme.TXT3, ""
        elif not self.editable:
            text = "● 只读　%s" % (self.pname or "未选程序")
            color, tip = theme.TXT3, "共 %d 行；点【编辑项目】才能改" % n
        elif self.pname and not self._dirty:
            text, color = "● 已保存　%s" % self.pname, theme.OK
            tip = "共 %d 行，其中 %d 行要读取" % (n, m)
        elif self._dirty or self._fresh or name or n:
            text, color = "● 未保存　%s" % (name or "未命名"), theme.WARN
            tip = "共 %d 行；点【保存程序】才会进顶部【测试程序名称】的下拉" % n
        else:
            text, color, tip = "● 还没有程序", theme.TXT3, "点【新建程序】开始"
        self.lb_pstate.setText(text)
        self.lb_pstate.setToolTip(tip)
        self.lb_pstate.setStyleSheet(
            "color:%s;background:transparent;" % color)
        self._place_empty()

    def _say(self, text, level="info"):
        self.page._say(text, level)

    def resizeEvent(self, e):
        # 窄窗口下状态标签会挤掉名称框，宁可先藏起来
        self.lb_pstate.setVisible(self.width() >= 700)
        super(ProgramEditor, self).resizeEvent(e)


def _cell(w):
    """把控件塞进单元格，四周留 2px，看起来像格子里的控件而不是按钮。"""
    box = QWidget()
    h = QHBoxLayout(box)
    h.setContentsMargins(2, 2, 2, 2)
    h.setSpacing(0)
    h.addWidget(w)
    box.setStyleSheet("background:transparent;")
    return box


def _row_btn_qss(color):
    return ("QPushButton{background:#f7fbff;border:1px solid #d2e2f0;"
            "border-radius:3px;color:#4b6a86;font-size:11px;padding:0 4px;}"
            "QPushButton:hover{background:#e7f1fb;border-color:#8fb6da;"
            "color:#1b4a78;}"
            "QPushButton:disabled{color:#879caf;background:#f2f7fb;"
            "border-color:#e0eaf3;}"
            "QPushButton:pressed{border-color:%s;}" % color)
