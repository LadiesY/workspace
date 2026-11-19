# -*- coding: utf-8 -*-
"""引脚点 / 矩形框编辑工具条（补点 / 删点 / 撤回 / 召回率 / 画框 / 改框）
=====================================================================
从「引脚识别工具」的 `PinTool\\ui\\pinedit.py` 搬过来，逻辑一行没改；
只换了配色、去掉按钮上的 emoji，并把 pins 模块改成按需导入（见 _PinsProxy）。
另外加了一版横排布局（`horizontal=True`）：画布下方一整条，高 36 px；
默认仍是原工具的竖条（贴在画布左边，宽 112 px）。

为什么做成独立控件、而不是每个面板各写一遍：
两个面板装结果的容器名字不同（`pin_results` / `results`）、写 JSON 的路径规则
也不同，但「改数据 → 重绘 → 写 JSON → 能撤回」这条链路必须**一模一样**，
否则很容易出现"YOLO 里能撤回、标签里撤不动"这种不一致。这里只用几个回调
把面板的差异挡在外面。

面板需要提供：
    get_chips()        -> 当前图的 chips（list）或 None
    apply_chips(chips) -> 写回面板数据并重绘（内部会调 view.set_pins）
    persist()          -> 把当前图结果写进 *_pins.json，返回路径或一句提示
    say(msg)           -> 在信息栏显示一句话
    iter_all()         -> [(显示名, chips), ...]，批量口径的召回率用；可为 None
    draw_chip(idx)     -> 画完新框后跑一次单芯片识别（面板提供，可为 None）

对外方法：
    toolbar()     返回可直接 addWidget 的工具条 QWidget
    set_batch(b)  切换召回率口径（单张 / 批量）
    undo()        撤回上一步（工具条按钮与 Ctrl+Z 都走它）

矩形框编辑（2026-10-13 新增）
----------------------------
    · 矩形框：两点画一个新框 -> 弹窗选 O/F/T -> 自动命名 `O_1`/`F_2`…（不重复）
      -> 立刻写 JSON -> 自动跑单芯片重跑把引脚填上（之后还能再右键重跑）；
    · 右键框：编辑矩形框（拖角点改大小）/ 编辑标签名 / 删除此矩形框（含框内点）；
    · 以上每一步都进撤回栈，Ctrl+Z 可退。
"""
import os

from PyQt5.QtCore import Qt, QObject
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLabel, QFrame, QButtonGroup, QMessageBox,
                             QSizePolicy, QInputDialog)

from .. import theme
from . import engine


class _PinsProxy(object):
    """pins 模块是延迟导入的（首次导入要拉 cv2 / numpy，约 1 秒）。

    这里只在真正调用时再转发，导入本模块本身不碰算法包。
    """

    def __getattr__(self, name):
        return getattr(engine.pins_pkg(), name)


P = _PinsProxy()

_BTN = ("QPushButton{background:#f4f8fd;border:1px solid #d5e4f1;border-radius:3px;"
        "color:#34506b;padding:3px 4px;font-size:11px;}"
        "QPushButton:hover{background:#e9f2fb;border-color:#8fb6da;color:#17497f;}"
        "QPushButton:checked{background:#e6f0fa;border-color:#1b57a0;"
        "color:#17497f;font-weight:bold;}"
        "QPushButton:disabled{color:#879caf;background:#f6f9fc;border-color:#e4eef7;}")

# 外部（自动测试页）要往这条工具栏里塞自己的按钮时，拿这个样式，别另抄一份 ——
# 两个页面的按钮长得不一样，用户一眼就看出来是"后加的"。
BTN_QSS = _BTN


class PinEditKit(QObject):
    """把「补点 / 删点 / 撤回 / 召回率」挂到某个面板的画布上。"""

    MAX_UNDO = 40

    def __init__(self, view, get_chips, apply_chips, persist, say,
                 iter_all=None, draw_chip=None, parent=None, horizontal=False,
                 compact=False, extra=None):
        super().__init__(parent)
        self.view = view
        # extra：调用方自己的按钮（自动测试页的【切换】），排在召回率之后。
        # 视觉定位页不传 → 那条工具栏一个像素都不变。
        self._extra = list(extra or [])
        self._locked = False            # True = 编辑条锁住（画布在放实时画面）
        self._get = get_chips
        self._apply = apply_chips
        self._persist = persist
        self._say = say
        self._iter_all = iter_all
        self._draw_chip = draw_chip       # 画完新框后跑一次单芯片识别
        self._undo = []                 # 快照栈（每个元素是一份深拷贝的 chips）
        self._batch = False
        self.n_single = 0               # 单张口径最近一次的点数（给提示用）
        self._horizontal = bool(horizontal)
        # compact：横排时省掉「引脚编辑」标题和召回率读数，给窄画布腾地方
        # （自动测试页的画布只有 450 宽，完整那条要 503）
        self._compact = bool(compact)
        self._build()
        view.pointAddRequested.connect(self._on_add)
        view.pinsDeleteRequested.connect(self._on_delete)
        view.undoRequested.connect(self.undo)
        view.pinSelectionChanged.connect(self._on_sel)
        view.boxDrawRequested.connect(self._on_box_draw)
        view.boxResizeRequested.connect(self._on_box_resize)
        view.chipDeleteRequested.connect(self._on_chip_delete)
        view.chipLabelRequested.connect(self._on_chip_label)

    # ------------------------------------------------------------- 工具条
    def _make_buttons(self):
        """只建按钮，不管往哪排 —— 竖排 / 横排两套布局共用同一批控件。"""
        self.grp = QButtonGroup(self)
        self.btn_sel = QPushButton("选择")
        self.btn_brush = QPushButton("画笔")
        self.btn_rect = QPushButton("矩形框")
        for b in (self.btn_sel, self.btn_brush, self.btn_rect):
            b.setCheckable(True)
            b.setStyleSheet(_BTN)
            b.setCursor(Qt.PointingHandCursor)
            self.grp.addButton(b)
        self.btn_sel.setToolTip("选择：左键点选引脚点（Ctrl+左键加选）\n"
                               "右键框可：重跑 / 编辑框 / 改标签名 / 删除框")
        self.btn_brush.setToolTip("画笔：左键单击补一个引脚点（蓝色 b1/b2…）")
        self.btn_rect.setToolTip("矩形框：图上点第一点 → 移动鼠标拉出框 → 点第二点完成\n"
                                 "完成后弹出标签选择（O / F / T），自动命名 O_1/F_2…，\n"
                                 "并立即写 JSON + 跑单芯片识别填引脚")
        self.btn_sel.setChecked(True)
        self.btn_sel.clicked.connect(lambda: self.set_tool("select"))
        self.btn_brush.clicked.connect(lambda: self.set_tool("brush"))
        self.btn_rect.clicked.connect(lambda: self.set_tool("rect"))

        self.btn_undo = QPushButton("撤回")
        self.btn_undo.setStyleSheet(_BTN)
        self.btn_undo.setCursor(Qt.PointingHandCursor)
        self.btn_undo.setToolTip("撤回上一步（快捷键 Ctrl+Z）")
        self.btn_undo.clicked.connect(lambda: self.undo())

        self.btn_del = QPushButton("删除选中")
        self.btn_del.setStyleSheet(_BTN)
        self.btn_del.setCursor(Qt.PointingHandCursor)
        self.btn_del.setToolTip("删除选中的引脚点（快捷键 Delete）\n"
                                "Ctrl+左键可一次选多个，再一次性删掉")
        self.btn_del.setEnabled(False)
        self.btn_del.clicked.connect(self._delete_selected)

        self.btn_recall = QPushButton("召回率")
        self.btn_recall.setStyleSheet(_BTN)
        self.btn_recall.setCursor(Qt.PointingHandCursor)
        self.btn_recall.setToolTip("召回率 = 红点 /（红点 + 蓝点）× 100%\n"
                                   "红点 = 算法识别点，蓝点 = 人工补点")
        self.btn_recall.clicked.connect(self.compute_recall)

        self.lab_recall = QLabel("召回率 --")
        self.lab_recall.setStyleSheet(
            "color:%s;font-size:10px;border:none;background:transparent;"
            % theme.OK)

    def _build(self):
        w = QFrame()
        w.setObjectName("pinToolBar")
        w.setStyleSheet("QFrame#pinToolBar{background:#ffffff;"
                        "border:1px solid #dbe6f1;border-radius:4px;}")
        w.setToolTip("左键：补点 / 点选 / 画框　·　Ctrl+左键：加选\n"
                     "右键：点或框的操作　·　Ctrl+Z：撤回　·　Esc：取消")
        self._make_buttons()
        if self._horizontal:
            self._layout_h(w)
        else:
            self._layout_v(w)
        self._w = w

    def _layout_h(self, w):
        """横排：画布下方一整条。高 36 px，按钮左起，召回率读数靠右。

        按钮宽度按文字实测写死（不做自适应，避免「删除选中(3)」变字时整条抖动）；
        整条最小约 503 px。`compact=True` 时省掉标题和召回率读数，最小降到
        约 383 px，给自动测试页那块 450 宽的画布用。
        """
        w.setFixedHeight(36)
        w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(5)

        if not self._compact:
            head = QLabel("引脚编辑")
            head.setFixedWidth(44)
            head.setStyleSheet("color:%s;font-size:11px;border:none;"
                               "background:transparent;" % theme.TXT3)
            lay.addWidget(head)

        for b, wd in ((self.btn_sel, 46), (self.btn_brush, 46), (self.btn_rect, 54)):
            b.setFixedSize(wd, 25)
            lay.addWidget(b)

        lay.addSpacing(1)
        lay.addWidget(self._divider())
        lay.addSpacing(1)

        # 宽度写死，免得「删除选中(3)」变字时整条工具栏跟着抽动
        self.btn_undo.setFixedSize(46, 25)
        lay.addWidget(self.btn_undo)
        self.btn_del.setFixedSize(84, 25)
        lay.addWidget(self.btn_del)
        self.btn_recall.setFixedSize(54, 25)
        lay.addWidget(self.btn_recall)

        self.lab_recall.setWordWrap(False)
        self.lab_recall.setMinimumWidth(0)
        if self._compact:
            # 读数不摆出来（省 ~70 px），召回率结果仍然走状态行 / 日志
            self.lab_recall.setVisible(False)
        else:
            lay.addSpacing(8)
            lay.addWidget(self.lab_recall)
        # 调用方自带的按钮（【切换】）贴在这一排末尾；固定高度 25 跟其它按钮齐平
        for b in self._extra:
            b.setFixedHeight(25)
            lay.addWidget(b)
        lay.addStretch(1)

    def _layout_v(self, w):
        """竖排（原工具的样子）：窄条贴在画布左边。"""
        w.setFixedWidth(112)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(7, 7, 7, 7)
        lay.setSpacing(5)

        head = QLabel("引脚编辑")
        head.setStyleSheet("color:%s;font-size:11px;border:none;"
                           "background:transparent;" % theme.TXT3)
        lay.addWidget(head)

        for b in (self.btn_sel, self.btn_brush, self.btn_rect):
            b.setFixedHeight(24)
            lay.addWidget(b)
        self.btn_undo.setFixedHeight(24)
        lay.addWidget(self.btn_undo)
        self.btn_del.setFixedHeight(24)
        lay.addWidget(self.btn_del)
        self.btn_recall.setFixedHeight(24)
        lay.addWidget(self.btn_recall)

        self.lab_recall.setWordWrap(True)
        lay.addWidget(self.lab_recall)
        for b in self._extra:
            b.setFixedHeight(24)
            lay.addWidget(b)
        lay.addStretch(1)

    def _divider(self):
        f = QFrame()
        f.setFrameShape(QFrame.NoFrame)
        f.setFixedSize(1, 18)
        f.setStyleSheet("background:#dbe6f1;")
        return f

    def toolbar(self):
        return self._w

    # ------------------------------------------------------------- 锁定
    def set_locked(self, on):
        """锁住编辑功能：自动测试页的画布在放**相机实时画面**时用。

        **不能整条 setEnabled(False)**：Qt 的禁用会向下传播，外面塞进来的
        【切换】也在这一条里，整条一禁就再也切不回图片了。所以只按住编辑类
        按钮；每个入口另外判 _locked —— 画布的 Ctrl+Z / Delete 走 keyPressEvent，
        不经过按钮的 enabled，只能在槽里挡。
        """
        on = bool(on)
        if on == self._locked:
            return
        self._locked = on
        edit_btns = (self.btn_sel, self.btn_brush, self.btn_rect,
                     self.btn_undo, self.btn_del, self.btn_recall)
        if on:
            for b in edit_btns:
                b.setEnabled(False)
            self.view.cancel_draw()          # 画了一半的框 / 拉框全部作废
            self.view.end_box_edit()
            self.view.clear_pin_selection()
            self.view.set_tool("select")     # 只看不改；滚轮缩放、双击复位照旧
            return
        # 解锁：模式按钮回可点，撤回 / 删除按"有没有东西可撤 / 可删"恢复
        for b in (self.btn_sel, self.btn_brush, self.btn_rect, self.btn_recall):
            b.setEnabled(True)
        self.btn_undo.setEnabled(bool(self._undo))
        self.btn_del.setEnabled(bool(self.view.pin_selection()))

    # ------------------------------------------------------------- 模式
    def set_tool(self, mode):
        if self._locked:
            return
        mode = mode if mode in ("brush", "rect") else "select"
        self.view.set_tool(mode)
        self.btn_brush.setChecked(mode == "brush")
        self.btn_rect.setChecked(mode == "rect")
        self.btn_sel.setChecked(mode == "select")

    def set_batch(self, batch):
        """召回率口径：True = 批量（跨所有已识别图片），False = 单张。"""
        self._batch = bool(batch)
        self.btn_recall.setToolTip(
            ("召回率 = 红点 /（红点 + 蓝点）× 100%\n当前为**批量**口径："
             "统计所有已识别的图片" if self._batch else
             "召回率 = 红点 /（红点 + 蓝点）× 100%\n当前为**单张**口径："
             "只统计当前这张图"))
        self._update_recall()

    # ------------------------------------------------------------- 撤销栈
    def _push(self, chips):
        self._undo.append(P.snapshot_chips(chips))
        if len(self._undo) > self.MAX_UNDO:
            self._undo.pop(0)
        self.btn_undo.setEnabled(bool(self._undo))

    def undo(self):
        if self._locked:
            return
        if not self._undo:
            self._say("没有可撤回的操作。")
            return
        chips = self._undo.pop()
        self.view.end_box_edit()
        self.view.clear_pin_selection()
        self._refresh(chips)
        self.btn_undo.setEnabled(bool(self._undo))
        self._say("已撤回上一步。" + self._persist_msg())

    # ------------------------------------------------------------- 增 / 删
    def _on_add(self, x, y):
        if self._locked:
            return
        chips = self._get()
        if chips is None:
            self._say("请先点「引脚识别」得到结果，再补点。")
            return
        before = P.snapshot_chips(chips)
        ci, res = P.add_manual_pin(chips, x, y)
        if ci is None:
            self._say(str(res))
            return
        self._push_from(before)
        self.view.clear_pin_selection()
        self._refresh(chips)
        ch = chips[ci]
        self._say("已补点 %s（%s，chip#%d，%d 个点）。%s"
                  % (res.get("name"), ch.get("id") or ch.get("label"),
                     ci + 1, ch.get("pin_count", 0), self._persist_msg()))

    def _on_delete(self, refs):
        if self._locked:
            return
        chips = self._get()
        if chips is None:
            return
        before = P.snapshot_chips(chips)
        n = P.delete_pins(chips, refs)
        if not n:
            self._say("没有可删除的点。")
            return
        self._push_from(before)
        self.view.clear_pin_selection()
        self._refresh(chips)
        self._say("已删除 %d 个点。%s" % (n, self._persist_msg()))

    def _delete_selected(self):
        if self._locked:
            return
        refs = self.view.pin_selection()
        if not refs:
            self._say("请先左键点选引脚点（Ctrl+左键可加选），再删除。")
            return
        self._on_delete(refs)

    def _push_from(self, snap):
        self._undo.append(snap)
        if len(self._undo) > self.MAX_UNDO:
            self._undo.pop(0)
        self.btn_undo.setEnabled(True)

    # ------------------------------------------------------------- 矩形框编辑
    def _ask_kind(self, idx=None):
        """弹窗选类别。返回类别名（内置 O/F/T/C/L/V 或自定义如 Y），取消返回 None。"""
        chips = self._get() or []
        cur = "T"
        if idx is not None and 0 <= idx < len(chips):
            cur = str(chips[idx].get("label") or "T")
        cur = P.kind_of(cur) or "T"
        items = list(P.all_kinds())
        texts = [P.kind_text(k) for k in items]     # "T　双面两排 · 10 脚/排 · 共 20 点"
        d = items.index(cur) if cur in items else 0
        head = "编辑标签名" if idx is not None else "新建矩形框"
        msg = ("这颗芯片的类别：\n"
               "O 单排 / T 双面两排 10 脚 / F 四边 32 脚 / C·V 两端件 / L 双面 3 脚。\n"
               "自己新增的类别（如 Y）也在列表里，名称后的说明就是它的算法与点数。"
               if idx is not None else
               "新矩形框的类别：\n"
               "O 单排 / T 双面两排 10 脚 / F 四边 32 脚 / C·V 两端件 / L 双面 3 脚。\n"
               "自己新增的类别（如 Y）也在列表里，名称后的说明就是它的算法与点数。")
        v, ok = QInputDialog.getItem(self._w, head, msg, texts, d, False)
        if not ok or not v:
            return None
        return items[texts.index(v)] if v in texts else P.kind_of(v)

    def _on_box_draw(self, x0, y0, x1, y1):
        """画完一个新框：选标签 → 建空框 → 写 JSON → 跑单芯片识别填引脚。"""
        if self._locked:
            return
        chips = self._get()
        if chips is None:
            self._say("请先点「引脚识别」得到结果，再画新框。")
            return
        k = self._ask_kind(None)
        if k is None:
            self._say("已取消新建矩形框。")
            return
        before = P.snapshot_chips(chips)
        idx, ch = P.add_chip(chips, [x0, y0, x1, y1], k)
        if idx is None:
            self._say(str(ch))
            return
        self._push_from(before)
        self.view.clear_pin_selection()
        self._refresh(chips)
        self.view.select(idx)
        self._say("已新建矩形框 %s（%s，%.0f×%.0f px）。%s　正在识别框内引脚…"
                  % (ch["id"], ch["label"], abs(x1 - x0), abs(y1 - y0),
                     self._persist_msg()))
        if self._draw_chip is not None:
            self._draw_chip(idx)

    def _on_box_resize(self, idx, box):
        """拖角点改完大小：写回 chips + JSON，并保持编辑状态接着拖。"""
        if self._locked:
            return
        chips = self._get()
        if chips is None or not (0 <= int(idx) < len(chips)):
            return
        before = P.snapshot_chips(chips)
        got = P.set_chip_box(chips, int(idx), box)
        if got is None:
            return
        self._push_from(before)
        self._refresh(chips)
        self.view.select(int(idx))
        self.view.begin_box_edit(int(idx))       # 还能接着拖别的角
        x0, y0, x1, y1 = got
        self._say("已改矩形框 %s 大小：%.0f×%.0f px（左上 %.0f, %.0f）。%s"
                  % (chips[int(idx)].get("id") or ("chip#%d" % (int(idx) + 1)),
                     x1 - x0, y1 - y0, x0, y0, self._persist_msg()))

    def _on_chip_delete(self, idx):
        """删除整个矩形框（含框内的引脚点）。框内点可能不少，先确认一次。"""
        if self._locked:
            return
        chips = self._get()
        if chips is None or not (0 <= int(idx) < len(chips)):
            return
        ch = chips[int(idx)]
        n = int(ch.get("pin_count", 0) or 0)
        name = ch.get("id") or ("chip#%d" % (int(idx) + 1))
        r = QMessageBox.question(
            self._w, "删除矩形框",
            "确定删除矩形框 %s（%s）吗？\n"
            "框内的 %d 个引脚点会一起删掉，并同步写回 JSON。\n\n"
            "（误删可以按 Ctrl+Z 撤回）" % (name, ch.get("label") or "?", n),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        before = P.snapshot_chips(chips)
        gone = P.delete_chip(chips, int(idx))
        if gone is None:
            return
        self._push_from(before)
        self.view.clear_pin_selection()
        self._refresh(chips)
        self._say("已删除矩形框 %s（连带 %d 个引脚点）。%s"
                  % (name, n, self._persist_msg()))

    def _on_chip_label(self, idx):
        """改芯片类别：选 O / F / T 后自动命名 `O_1`/`F_2`…（不与现有编号重复）。"""
        if self._locked:
            return
        chips = self._get()
        if chips is None or not (0 <= int(idx) < len(chips)):
            return
        old = str(chips[int(idx)].get("label") or "?")
        k = self._ask_kind(int(idx))
        if k is None:
            self._say("已取消改标签名。")
            return
        if k == old:
            self._say("类别没变（还是 %s），未改动。" % old)
            return
        before = P.snapshot_chips(chips)
        new_id = P.set_chip_label(chips, int(idx), k)
        if new_id is None:
            return
        self._push_from(before)
        self._refresh(chips)
        self.view.select(int(idx))
        self._say("已把第 %d 个框的标签 %s → %s，重新命名为 %s。%s"
                  % (int(idx) + 1, old, k, new_id, self._persist_msg()))

    # ------------------------------------------------------------- 召回率
    def compute_recall(self):
        if self._locked:
            return
        if self._batch:
            self._recall_batch()
        else:
            self._recall_single()

    def _recall_single(self):
        chips = self._get()
        if chips is None:
            self._say("当前图片还没有引脚识别结果。")
            return
        red, blue, pct = P.recall_of(chips)
        self._update_recall()
        if red + blue == 0:
            self._say("当前图片没有任何点（红点 0、补点 0），召回率无从计算。")
            return
        self._say("单张召回率 = 红点 %d /（红点 %d + 补点 %d）= %.1f%%%s"
                  % (red, red, blue, pct,
                     "" if blue else "　（本图还没有人工补点，所以是 100%）"))

    def _recall_batch(self):
        items = list(self._iter_all() or [])
        if not items:
            self._say("批量模式下还没有任何识别结果，先「引脚识别全部」。")
            return
        red = blue = 0
        per = []
        for name, chips in items:
            r, b, pct = P.recall_of(chips)
            red += r
            blue += b
            per.append((name, r, b, pct))
        tot = red + blue
        pct = (100.0 * red / tot) if tot else 0.0
        self._update_recall()
        lines = ["批量召回率 = 红点 %d /（红点 %d + 补点 %d）= **%.1f%%**"
                 % (red, red, blue, pct),
                 "统计范围：%d 张已识别的图片" % len(items), ""]
        for name, r, b, p in per[:40]:
            lines.append("  %-24s 红 %-5d 蓝 %-4d  %.1f%%"
                         % (os.path.basename(name), r, b, p))
        if len(per) > 40:
            lines.append("  …（其余 %d 张略）" % (len(per) - 40))
        QMessageBox.information(self._w, "批量召回率", "\n".join(lines))
        self._say("批量召回率 = %.1f%%（红 %d / 蓝 %d，共 %d 张图）"
                  % (pct, red, blue, len(items)))

    # ------------------------------------------------------------- 杂项
    def _refresh(self, chips=None):
        data = self._get() if chips is None else chips
        if data is None:
            self._update_recall()
            return
        self._apply(data)
        self._update_recall()

    def _update_recall(self):
        chips = self._get()
        if chips is None:
            self.lab_recall.setText("召回率 --")
            return
        red, blue, pct = P.recall_of(chips)
        if self._horizontal:
            self.lab_recall.setText("召回率 %.1f%%　红 %d · 蓝 %d"
                                    % (pct, red, blue))
        else:
            self.lab_recall.setText("%.1f%%\n红 %d · 蓝 %d" % (pct, red, blue))

    def _persist_msg(self):
        try:
            r = self._persist()
        except Exception as e:                 # 写盘失败不能影响界面操作
            return "（写 JSON 失败：%r）" % e
        if not r:
            return ""
        if isinstance(r, str) and os.path.isabs(r):
            return "已同步 %s" % os.path.basename(r)
        return str(r)

    def _on_sel(self, refs):
        n = len(refs or [])
        # 锁定（实时画面）时不要因为选中变化把「删除选中」又点亮
        self.btn_del.setEnabled(n > 0 and not self._locked)
        self.btn_del.setText("删除选中(%d)" % n if n else "删除选中")

    def reset_state(self):
        """切换图片 / 模式时调用：清空撤回栈与召回率显示（旧图的快照不能跨图撤回）。"""
        self._undo = []
        self.btn_undo.setEnabled(False)
        self.view.cancel_draw()
        self.view.end_box_edit()
        self.view.clear_pin_selection()
        self._update_recall()

    def refresh(self):
        """外部数据变了（换图 / 重跑 / 批量识别）后刷新召回率显示。"""
        self._update_recall()
