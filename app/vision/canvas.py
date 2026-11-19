# -*- coding: utf-8 -*-
"""
交互式图像画布
==============
把「只能看、单击弹大图」的图片区换成可以直接操作的画布：

  · Ctrl + 滚轮        —— 以鼠标位置为中心缩放（普通滚轮仍归面板滚动）
  · 按住滚轮(中键)拖动 —— 上下左右平移
  · 左键单击矩形框     —— 选中该框（高亮描边）
  · 右键               —— 菜单「单芯片重跑（用新算法）」
  · 双击               —— 复位为「适应窗口」

每个框的左上角还会画一个**芯片编号**（T_1 / F_2 / O_3…）——它是矢量浮层，
用 ItemIgnoresTransformations 画的，**缩放时始终是固定屏幕大小**，不会糊、
也不会糊成巨字。编号由 pins.assign_chip_ids() 生成并写进 JSON 的 chips[].id。

对外信号：
    boxSelected(int)     选中框的下标；-1 = 取消选中
    rerunRequested(int)  请求对第 i 个框重跑
    pointAddRequested(x, y)   画笔单击补点（场景坐标）
    pinsDeleteRequested(list) 删除引脚点 [(chip_idx, pin_idx), ...]
    undoRequested()           撤回上一步
    pinSelectionChanged(list) 引脚选中集变化
    boxDrawRequested(x0,y0,x1,y1)  新画的框（两点确定，场景坐标）
    boxResizeRequested(int, list)  框被拖角点改大小 -> (下标, [x0,y0,x1,y1])
    chipDeleteRequested(int)       请求删除整个矩形框（含框内引脚）
    chipLabelRequested(int)        请求改这颗芯片的类别（O / F / T）

引脚点编辑（2026-10-13 新增）
---------------------------
画布多了一层 `_PinMark`（平时透明，选中时套青色圈），用来做引脚点的命中测试：
  · 工具模式：`set_tool('select')` 点选/框选；`set_tool('brush')` 左键单击补点；
    `set_tool('rect')` 两点画一个新矩形框；
  · 点选引脚：左键单击 / Ctrl+左键加选（批量删点用）；选中集见 pin_selection()；
  · 右键：在点上 → 删除该点 / 删除选中点；在框上 → 重跑 / 编辑框 / 改标签 / 删框；
  · 快捷键：Ctrl+Z 撤回、Delete 删除选中点、Esc 清空选中 / 取消画框。

矩形框编辑（2026-10-13 新增）
----------------------------
  · **改大小**：右键框 →「编辑矩形框」→ 框的四角出现把手，拖任意一个角即可
    改大小（拖动时实时预览，松手才写回数据 + JSON）；Esc 退出。
  · **改标签**：右键框 →「编辑标签名」→ 选 O / F / T，自动命名成 `O_1`/`F_2`…
    （编号不与现有重复）。
  · **删框**：右键框 →「删除此矩形框」，框和框内所有引脚点一起删。
  · **画新框**：选工具「□ 矩形框」→ 图上点第一点 → 移动鼠标拉出框 → 点第二点
    完成（两点确定一个框）。

进度浮层（重跑时显示）：
    show_progress(text, total=0) / set_progress(done, text) / hide_progress()
    total=0 表示不确定进度（忙碌条）。

本文件从「引脚识别工具」的 `PinTool\\ui\\canvas.py` 搬过来，交互逻辑一行没改；
只做了两件事：把配色换成测试系统的主题色（`app\\theme.py`），
以及把 `ndarray_to_qpixmap` 换成本包自己的实现（原工具那一份依赖它的
`ui\\widgets.py`，本项目没有那套控件）。
"""
from PyQt5.QtCore import Qt, QRectF, pyqtSignal
from PyQt5.QtGui import (QPainter, QPen, QColor, QPixmap, QFont,
                         QFontMetricsF, QBrush)
from PyQt5.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
                             QGraphicsRectItem, QGraphicsItem, QMenu, QAction,
                             QLabel, QProgressBar, QVBoxLayout, QWidget,
                             QFrame, QSizePolicy)

from .. import theme
from .engine import bgr_to_qimage


def ndarray_to_qpixmap(bgr):
    """cv2 BGR ndarray -> QPixmap（无则返回 None）。"""
    if bgr is None:
        return None
    q = bgr_to_qimage(bgr)
    return None if q is None else QPixmap.fromImage(q)

# 描边配色（全部取自 app\\theme.py，和左栏、按钮是同一套色）
C_NORMAL = theme.OK        # 普通框：绿
C_SELECT = theme.AMBER     # 选中框：琥珀
C_HOVER = theme.CYAN       # 悬停框：青
C_TAG_BG = "#0b1217"       # 编号标签底色
C_TAG_FG = theme.AMBER2    # 编号文字：浅琥珀
C_PIN_SEL = theme.CYAN     # 选中引脚点的高亮圈：青
C_HANDLE = theme.AMBER     # 矩形框角点把手：琥珀
C_HANDLE_EDGE = "#081016"
C_RUBBER = theme.AMBER     # 新框的橡皮筋预览
C_EDIT = "#ff6ab8"         # 正在编辑的框：洋红（与"选中"区分开）
C_BG = "#0c1720"           # 画布底色：深蓝黑（浅色界面里纯黑太"漏"，压一点蓝）

_STYLE_SCROLL = (
    "QScrollBar:vertical{background:#0a1218;width:9px;margin:0;}"
    "QScrollBar:horizontal{background:#0a1218;height:9px;margin:0;}"
    "QScrollBar::handle:vertical{background:#24384a;border-radius:4px;min-height:24px;}"
    "QScrollBar::handle:horizontal{background:#24384a;border-radius:4px;min-width:24px;}"
    "QScrollBar::handle:hover{background:#33566d;}"
    "QScrollBar::add-line,QScrollBar::sub-line{height:0;width:0;}"
    "QScrollBar::add-page,QScrollBar::sub-page{background:transparent;}")


def _norm_box(b):
    """把各种写法的框统一成 (x0, y0, x1, y1)。

    项目里框有两种写法，必须都吃得下：
      · 引脚结果 chips[i]['box']      -> [[x0, y0], [x1, y1]]（labelme 的两个角点）
      · YOLO 检测 detections[i]['box'] -> [x0, y0, x1, y1]（已经是 4 个数）
    """
    if b is None:
        return None
    seq = list(b)
    first = seq[0] if seq else None
    nested = isinstance(first, dict) or (
        hasattr(first, "__len__") and not isinstance(first, str))
    if nested and len(seq) >= 2:
        p0, p1 = seq[0], seq[1]
        if isinstance(p0, dict):                       # {'x':..,'y':..}
            x0, y0 = float(p0.get("x", 0)), float(p0.get("y", 0))
            x1, y1 = float(p1.get("x", 0)), float(p1.get("y", 0))
        else:
            x0, y0, x1, y1 = (float(p0[0]), float(p0[1]),
                              float(p1[0]), float(p1[1]))
    elif len(seq) >= 4:
        x0, y0, x1, y1 = (float(seq[0]), float(seq[1]),
                          float(seq[2]), float(seq[3]))
    else:
        return None
    return x0, y0, x1, y1


def _pen(color, width=1.2):
    p = QPen(QColor(color))
    p.setCosmetic(True)          # 与缩放无关，始终是屏幕上的固定宽度
    p.setWidthF(width)
    return p


class _BoxItem(QGraphicsRectItem):
    """一个芯片矩形框；index 是它在结果列表里的下标。"""

    def __init__(self, index, rect):
        super().__init__(rect)
        self.index = index
        self.setBrush(QColor(0, 0, 0, 0))
        self.setAcceptHoverEvents(True)


class _ChipTagItem(QGraphicsItem):
    """框左上角的芯片编号标签（如 `T_1`）。

    用 `ItemIgnoresTransformations`：**屏幕像素固定大小**，缩放图时既不会糊
    也不会变成巨字 —— 这正是「一眼分辨谁是谁」需要的。位置仍跟随框。
    """

    PAD_X, PAD_Y = 4, 2

    def __init__(self, text, level):
        super().__init__()
        self._text = str(text)
        self._level = int(level)
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        self.setZValue(100)                      # 盖在所有框之上
        # 用**应用默认字体**（只设为粗体）：写死字体名在有些机器上会解析不到
        # （如 'Microsoft YaHei' 在中文系统上登记为 '微软雅黑'），缺字就很糟。
        self._f = QFont()
        self._f.setBold(True)
        self._f.setPixelSize(self._px())

    def _px(self):
        """档位 -> 屏幕像素字号（1..12 档 -> 8..19px）。"""
        return max(8, min(30, 7 + int(self._level)))

    def set_level(self, level):
        self.prepareGeometryChange()
        self._level = int(level)
        self._f.setPixelSize(self._px())
        self.update()

    def boundingRect(self):
        fm = QFontMetricsF(self._f)
        w = fm.horizontalAdvance(self._text) + 2 * self.PAD_X
        h = fm.height() + 2 * self.PAD_Y
        return QRectF(0.0, 0.0, float(w), float(h))

    def paint(self, p, opt, widget=None):
        r = self.boundingRect()
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(C_TAG_BG)))
        p.drawRoundedRect(r, 3.0, 3.0)
        p.setPen(QPen(QColor(C_TAG_FG)))
        p.setFont(self._f)
        p.drawText(r.adjusted(self.PAD_X, 0, -self.PAD_X, 0),
                   Qt.AlignVCenter | Qt.AlignLeft, self._text)


class _PinMark(QGraphicsItem):
    """一个引脚点的「可点选 / 可高亮」层。

    为什么不在这一层画点：算法点已经由底图（`render_annotated`）画成红点、
    人工补点画成蓝点。这里重复画一遍会出现"双层点"对不齐。所以本层平时**透明**，
    只在被选中时套一个青色高亮圈 —— 视觉与改动前一致，多的是可点选的手感。

    坐标用**图像坐标系**（普通 item）：高亮圈随缩放一起放大，正好圈住底图上
    那个大点；命中判定在画布里按屏幕像素折算容差（见 `_pin_at`），
    所以缩到很小时也点得中。
    """

    def __init__(self, chip_idx, pin_idx, manual, r):
        super().__init__()
        self.chip_idx = int(chip_idx)
        self.pin_idx = int(pin_idx)
        self.manual = bool(manual)
        self._r = float(r)
        self._sel = False
        self.setZValue(70)                  # 在框(10+i)之上、芯片编号(100)之下
        self.setAcceptedMouseButtons(Qt.NoButton)

    def boundingRect(self):
        k = self._r + 3.0
        return QRectF(-k, -k, 2 * k, 2 * k)

    def set_radius(self, r):
        self.prepareGeometryChange()
        self._r = float(r)
        self.update()

    def set_selected(self, on):
        on = bool(on)
        if on == self._sel:
            return
        self._sel = on
        self.update()

    def paint(self, p, opt, widget=None):
        if not self._sel:
            return
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(Qt.NoBrush)
        pen = QPen(QColor(C_PIN_SEL))
        pen.setWidthF(max(1.0, self._r * 0.45))
        p.setPen(pen)
        rr = self._r + max(2.0, self._r * 0.7)
        p.drawEllipse(QRectF(-rr, -rr, 2 * rr, 2 * rr))


class _HandleItem(QGraphicsItem):
    """矩形框四角的「把手」——拖它就能改框的大小。

    用 `ItemIgnoresTransformations`：把手在屏幕上是**固定大小**的小黄方块，
    放大到芯片级也不会变成巨大的方块挡住引脚。它自身的 `scenePos()` 仍是
    场景坐标（就是它锚定的那个角），命中判定按屏幕像素折算容差。
    """

    SIZE = 5.0                     # 半边长（屏幕像素）

    def __init__(self, corner, on):
        super().__init__()
        self.corner = int(corner)  # 0=左上 1=右上 2=右下 3=左下
        self._on = bool(on)
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        self.setZValue(120)        # 盖在框、引脚点之上（芯片编号 100 之下）
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setAcceptHoverEvents(False)

    def boundingRect(self):
        k = self.SIZE
        return QRectF(-k, -k, 2 * k, 2 * k)

    def paint(self, p, opt, widget=None):
        if not self._on:
            return
        k = self.SIZE
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setBrush(QBrush(QColor(C_HANDLE)))
        p.setPen(QPen(QColor(C_HANDLE_EDGE), 1.0))
        p.drawRect(QRectF(-k, -k, 2 * k, 2 * k))


class _RubberItem(QGraphicsRectItem):
    """画新框时的橡皮筋预览（虚线，不参与鼠标事件）。"""

    def __init__(self):
        super().__init__()
        pen = QPen(QColor(C_RUBBER))
        pen.setCosmetic(True)
        pen.setWidthF(1.4)
        pen.setStyle(Qt.DashLine)
        self.setPen(pen)
        self.setBrush(QColor(255, 178, 63, 30))
        self.setZValue(130)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.show()                 # addItem 后要可见，否则预览是"隐形"的


class _Overlay(QWidget):
    """进度浮层：不接收鼠标事件，浮在画布左上角。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(
            "background:rgba(9,15,20,235);"
            "border:1px solid #24384a; border-radius:5px;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        self.lab = QLabel("处理中…")
        self.lab.setStyleSheet(
            "background:transparent; border:none; color:#d3dee8; font-size:12px;")
        lay.addWidget(self.lab)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.setStyleSheet(
            "QProgressBar{background:#16232d;border:none;border-radius:3px;}"
            "QProgressBar::chunk{background:#2e74c4;border-radius:3px;}")
        lay.addWidget(self.bar)
        self.hide()

    def set_state(self, done, total, text):
        if text:
            self.lab.setText(text)
        if total and total > 0:
            self.bar.setRange(0, int(total))
            self.bar.setValue(int(done))
        else:
            self.bar.setRange(0, 0)          # 忙碌条
        self.adjustSize()


class InteractiveCanvas(QGraphicsView):
    boxSelected = pyqtSignal(int)
    rerunRequested = pyqtSignal(int)
    # ---- 引脚点编辑（2026-10-13 新增）
    pointAddRequested = pyqtSignal(float, float)   # 画笔单击：场景坐标 (x, y) 补点
    pinsDeleteRequested = pyqtSignal(list)         # [(chip_idx, pin_idx), ...] 删除
    undoRequested = pyqtSignal()                   # 撤回上一步
    pinSelectionChanged = pyqtSignal(list)         # 选中的引脚 [(chip_idx, pin_idx)]
    # ---- 矩形框编辑（2026-10-13 新增）
    boxDrawRequested = pyqtSignal(float, float, float, float)  # 新画的框两点
    boxResizeRequested = pyqtSignal(int, list)     # (chip_idx, [x0,y0,x1,y1])
    chipDeleteRequested = pyqtSignal(int)          # 删除整个矩形框（含框内引脚）
    chipLabelRequested = pyqtSignal(int)           # 改芯片类别（O / F / T）

    def __init__(self, placeholder="（暂无图片）", parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setFrameShape(QFrame.NoFrame)
        self.setBackgroundBrush(QColor(C_BG))
        self.setStyleSheet("QGraphicsView{border:1px solid #cfe0ee;}"
                           + _STYLE_SCROLL)
        self.setMouseTracking(True)
        self.setMinimumSize(220, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)

        self._bg = None            # QGraphicsPixmapItem
        self._rects = []           # [_BoxItem]
        self._tags = []            # [_ChipTagItem]
        self._box_pairs = []       # [(rect_item, label_text)] 供切换档位时重建
        self._label_of = {}        # 框下标 -> 编号文本
        self._tag_level = 0        # 0 = 不显示编号
        self._sel = -1
        self._pan = None
        self._auto_fit = True
        self._size = None
        self._placeholder = placeholder

        # 引脚点层 / 工具模式
        self._pin_items = []       # [_PinMark]
        self._pin_sel = set()      # {(chip_idx, pin_idx)}
        self._pin_r = 2.0          # 底图上点的半径（图像 px），用于画高亮圈
        self._tool = "select"      # 'select' | 'brush' | 'rect'

        # 矩形框绘制 / 编辑
        self._draw_start = None    # 画新框时第一个点的场景坐标
        self._rubber = None        # _RubberItem
        self._edit_idx = None      # 正在拖角点编辑的框下标
        self._handles = []         # [_HandleItem]（4 个角）
        self._drag_corner = None   # 正在拖的角
        self._drag_base = None     # 拖拽起始时的 (x0, y0, x1, y1)

        # 空图提示
        self._hint = QLabel(placeholder, self)
        self._hint.setAlignment(Qt.AlignCenter)
        # 提示文案有一整行，窗口窄的时候得让它折行，否则右半边被切掉
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(
            "background:transparent; border:none; color:%s;" % theme.TXT3)
        self._hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self._overlay = _Overlay(self)
        self._overlay.hide()

    # ------------------------------------------------------------- 外部接口
    def set_image(self, bgr_or_pixmap):
        """设置底图。尺寸没变时**保留**当前缩放/平移（改外观、重跑后重绘不跳）。"""
        if bgr_or_pixmap is None:
            self.clear_image()
            return
        if isinstance(bgr_or_pixmap, QPixmap):
            pm = bgr_or_pixmap
        else:
            pm = ndarray_to_qpixmap(bgr_or_pixmap)
        if pm is None or pm.isNull():
            self.clear_image()
            return
        if self._bg is None:
            self._bg = QGraphicsPixmapItem(pm)
            self._bg.setZValue(-10)
            self._bg.setTransformationMode(Qt.SmoothTransformation)
            self._scene.addItem(self._bg)
        else:
            self._bg.setPixmap(pm)
        self._scene.setSceneRect(QRectF(pm.rect()))
        self._hint.hide()
        size = (pm.width(), pm.height())
        if size != self._size:
            self._size = size
            self.fit()

    def set_boxes(self, boxes, labels=None):
        """重建矩形框（+ 可选的芯片编号标签）。

        boxes  每项既可以是 [[x0,y0],[x1,y1]]（引脚结果），
               也可以是 [x0,y0,x1,y1]（YOLO 检测）—— 由 _norm_box 统一。
        labels 与 boxes 一一对应的编号文本（如 ['T_1','T_2',...]）。
               传 None / 空 表示这一批只画框不画编号。
        """
        for it in self._rects:
            self._scene.removeItem(it)
        for t in self._tags:
            self._scene.removeItem(t)
        self.end_box_edit()          # 框重建了，角点把手必须跟着作废重建
        self._rects = []
        self._tags = []
        self._box_pairs = []
        self._label_of = {}
        self._sel = -1
        bx = list(boxes or [])
        lb = list(labels or [])
        for i, b in enumerate(bx):
            nb = _norm_box(b)
            if nb is None:
                continue
            x0, y0, x1, y1 = nb
            r = QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            it = _BoxItem(i, r)
            it.setPen(_pen(C_NORMAL))
            it.setZValue(10 + i)
            self._scene.addItem(it)
            self._rects.append(it)
            txt = str(lb[i]) if (i < len(lb) and lb[i]) else ""
            self._box_pairs.append((it, txt))
            if txt:
                self._label_of[i] = txt
        self._rebuild_tags()
        self.boxSelected.emit(-1)

    def set_tag_level(self, level):
        """设置芯片编号的显示档位（0 = 不显示）。改动立即生效。"""
        self._tag_level = int(level)
        self._rebuild_tags()

    def _rebuild_tags(self):
        for t in self._tags:
            self._scene.removeItem(t)
        self._tags = []
        if self._tag_level <= 0:
            return
        for it, txt in self._box_pairs:
            if not txt:
                continue
            tag = _ChipTagItem(txt, self._tag_level)
            r = it.rect()
            tag.setPos(r.left(), r.top())
            self._scene.addItem(tag)
            self._tags.append(tag)

    def clear_image(self):
        if self._bg is not None:
            self._scene.removeItem(self._bg)
            self._bg = None
        self.cancel_draw()
        self.end_box_edit()
        self.set_pins([])
        self._pin_sel = set()
        self.set_boxes([])
        self._size = None
        self._scene.setSceneRect(QRectF(0, 0, 1, 1))
        self.resetTransform()
        self._hint.setText(self._placeholder)
        self._hint.show()
        self._hint.raise_()

    def set_placeholder(self, text):
        self._placeholder = text
        if self._bg is None:
            self._hint.setText(text)

    def has_image(self):
        return self._bg is not None

    def selected(self):
        return self._sel

    def select(self, idx):
        if self._edit_idx is not None and idx != self._edit_idx:
            self.end_box_edit()
        self._sel = idx
        for it in self._rects:
            if it.index == self._edit_idx:            # 正在编辑的框保持洋红
                it.setPen(_pen(C_EDIT, 2.2))
                continue
            on = it.index == idx
            it.setPen(_pen(C_SELECT if on else C_NORMAL, 2.2 if on else 1.2))
        self.boxSelected.emit(idx)

    # ------------------------------------------------- 引脚点层 / 工具模式
    def set_tool(self, mode):
        """切换工具：'select'（点选/框选）| 'brush'（画笔补点）| 'rect'（画矩形框）。"""
        m = str(mode)
        self._tool = m if m in ("brush", "rect") else "select"
        if self._tool != "rect":
            self.cancel_draw()
        self._apply_cursor()

    def tool(self):
        return self._tool

    def _apply_cursor(self):
        if self._tool in ("brush", "rect"):
            self.viewport().setCursor(Qt.CrossCursor)
        else:
            self.viewport().setCursor(Qt.ArrowCursor)

    # ------------------------------------------------- 矩形框：画新框
    def cancel_draw(self):
        """取消进行中的「画新框」。"""
        self._draw_start = None
        if self._rubber is not None:
            self._scene.removeItem(self._rubber)
            self._rubber = None

    def drawing(self):
        return self._draw_start is not None

    def _finish_draw(self, sp):
        """第二个点落定：收橡皮筋，发信号让上层去建框。"""
        p0 = self._draw_start
        self.cancel_draw()
        if p0 is None:
            return
        x0, y0 = min(p0.x(), sp.x()), min(p0.y(), sp.y())
        x1, y1 = max(p0.x(), sp.x()), max(p0.y(), sp.y())
        self.boxDrawRequested.emit(float(x0), float(y0), float(x1), float(y1))

    # ------------------------------------------------- 矩形框：拖角点改大小
    def begin_box_edit(self, idx):
        """进入「编辑矩形框」状态：在框的四角画把手。"""
        try:
            idx = int(idx)
        except Exception:
            return
        if not (0 <= idx < len(self._rects)):
            return
        self.end_box_edit()
        self._edit_idx = idx
        self.cancel_draw()
        it = self._rects[idx]
        it.setPen(_pen(C_EDIT, 2.2))
        for k in range(4):
            h = _HandleItem(k, True)
            self._scene.addItem(h)
            self._handles.append(h)
        self._sync_handles()
        self.viewport().setCursor(Qt.SizeAllCursor)

    def end_box_edit(self):
        """退出编辑状态：移除把手，恢复原来的描边。"""
        for h in self._handles:
            self._scene.removeItem(h)
        self._handles = []
        self._drag_corner = None
        self._drag_base = None
        idx, self._edit_idx = self._edit_idx, None
        if idx is not None and 0 <= idx < len(self._rects):
            on = (idx == self._sel)
            self._rects[idx].setPen(_pen(C_SELECT if on else C_NORMAL,
                                         2.2 if on else 1.2))
        self._apply_cursor()

    def editing_box(self):
        return self._edit_idx

    def _corner_pts(self, rect):
        return [(rect.left(), rect.top()), (rect.right(), rect.top()),
                (rect.right(), rect.bottom()), (rect.left(), rect.bottom())]

    def _sync_handles(self):
        """把把手挪到当前框的四角（拖动过程中实时调用）。"""
        if self._edit_idx is None or not (0 <= self._edit_idx < len(self._rects)):
            return
        rect = self._rects[self._edit_idx].rect()
        for h, (x, y) in zip(self._handles, self._corner_pts(rect)):
            h.setPos(x, y)

    def _handle_at(self, pos):
        """找鼠标下的角点把手（容差按屏幕像素折算）。"""
        if self._edit_idx is None or not self._handles:
            return None
        sp = self.mapToScene(pos)
        z = max(1e-6, self.zoom())
        tol = max(_HandleItem.SIZE + 2.0, 10.0 / z)
        for h in self._handles:
            c = h.scenePos()
            if ((c.x() - sp.x()) ** 2 + (c.y() - sp.y()) ** 2) ** 0.5 <= tol:
                return h
        return None

    def _drag_to(self, sp):
        """按当前鼠标位置更新被拖的那个角（保持 x0<=x1、y0<=y1，最小 8px）。"""
        if self._edit_idx is None or self._drag_corner is None:
            return
        x0, y0, x1, y1 = self._drag_base
        c = self._drag_corner
        if c == 0:
            x0, y0 = sp.x(), sp.y()
        elif c == 1:
            x1, y0 = sp.x(), sp.y()
        elif c == 2:
            x1, y1 = sp.x(), sp.y()
        else:
            x0, y1 = sp.x(), sp.y()
        if x1 - x0 < 8.0:
            if c in (0, 3):
                x0 = x1 - 8.0
            else:
                x1 = x0 + 8.0
        if y1 - y0 < 8.0:
            if c in (0, 1):
                y0 = y1 - 8.0
            else:
                y1 = y0 + 8.0
        it = self._rects[self._edit_idx]
        it.setRect(QRectF(x0, y0, x1 - x0, y1 - y0))
        self._sync_handles()
        return x0, y0, x1, y1

    def current_box(self, idx=None):
        """取某个框当前的 (x0, y0, x1, y1)（拖动中取的是**实时**矩形）。"""
        i = self._edit_idx if idx is None else int(idx)
        if i is None or not (0 <= i < len(self._rects)):
            return None
        r = self._rects[i].rect()
        return float(r.left()), float(r.top()), float(r.right()), float(r.bottom())

    def set_pins(self, chips, dot_size=2.0):
        """重建引脚点层。`chips` 与 detect_image 输出同构。

        每条引脚建成一个 `_PinMark`：平时透明，被选中时套青色圈。
        传入的 `dot_size` 用于把高亮圈画得正好裹住底图上的那个点。
        """
        for it in self._pin_items:
            self._scene.removeItem(it)
        self._pin_items = []
        self._pin_r = max(1.0, float(dot_size))
        for ci, ch in enumerate(chips or []):
            for pi, p in enumerate(ch.get("pins") or []):
                try:
                    x, y = float(p["x"]), float(p["y"])
                except Exception:
                    continue
                mk = _PinMark(ci, pi, p.get("manual"), self._pin_r)
                mk.setPos(x, y)
                mk.set_selected((ci, pi) in self._pin_sel)
                self._scene.addItem(mk)
                self._pin_items.append(mk)
        self._pin_sel &= {(it.chip_idx, it.pin_idx) for it in self._pin_items}
        self.pinSelectionChanged.emit(sorted(self._pin_sel))

    def pin_selection(self):
        """当前选中的引脚，[(chip_idx, pin_idx), ...]（pin_idx 是 pins 列表下标）。"""
        return sorted(self._pin_sel)

    def clear_pin_selection(self):
        if self._pin_sel:
            self._pin_sel = set()
            self._sync_pin_sel()

    def _sync_pin_sel(self):
        for it in self._pin_items:
            it.set_selected((it.chip_idx, it.pin_idx) in self._pin_sel)
        self.pinSelectionChanged.emit(sorted(self._pin_sel))

    def _pin_at(self, pos):
        """找鼠标下最近的引脚点。

        容差按**屏幕像素**折算（≥7px）：缩得很小时点只有 1 个像素，
        靠图像坐标的固定容差根本点不中，所以这里除上当前缩放。
        """
        if not self._pin_items:
            return None
        sp = self.mapToScene(pos)
        z = max(1e-6, self.zoom())
        tol = max(self._pin_r + 2.0, 7.0 / z)
        best, bd = None, None
        for it in self._pin_items:
            c = it.scenePos()
            d = ((c.x() - sp.x()) ** 2 + (c.y() - sp.y()) ** 2) ** 0.5
            if d <= tol and (bd is None or d < bd):
                bd, best = d, it
        return best

    def fit(self):
        if self._bg is None:
            return
        self.resetTransform()
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
        self._auto_fit = True

    def zoom(self):
        return float(self.transform().m11())

    # -------- 进度浮层
    def show_progress(self, text="处理中…", total=0):
        self._overlay.set_state(0, total, text)
        self._overlay.show()
        self._overlay.raise_()
        self._place_overlay()

    def set_progress(self, done, text=None, total=None):
        t = self._overlay.bar.maximum() if total is None else total
        if t < 0:
            t = 0
        self._overlay.set_state(done, t, text)
        self._place_overlay()

    def hide_progress(self):
        self._overlay.hide()

    def _place_overlay(self):
        self._overlay.adjustSize()
        self._overlay.move(12, 12)

    # ------------------------------------------------------------- 绘制/布局
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._hint.setGeometry(self.rect().adjusted(24, 0, -24, 0))
        self._place_overlay()
        if self._auto_fit and self._bg is not None:
            self.fit()

    # ------------------------------------------------------------- 鼠标交互
    def _box_at(self, pos):
        for it in self.items(pos):
            if isinstance(it, _BoxItem):
                return it
        return None

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            d = e.angleDelta().y()
            if d == 0:
                e.accept()
                return
            f = 1.15 if d > 0 else (1.0 / 1.15)
            z = self.zoom() * f
            if z < 0.05 or z > 60.0:
                e.accept()
                return
            self._auto_fit = False
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
            self.scale(f, f)
            e.accept()
            return
        # 普通滚轮：画布放大到超出可视区时滚动画布，否则交给外层（面板）滚动
        if self.verticalScrollBar().maximum() > 0:
            super().wheelEvent(e)
        else:
            e.ignore()

    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self._pan = e.pos()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        if e.button() == Qt.LeftButton:
            sp = self.mapToScene(e.pos())
            if self._tool == "brush":
                self.pointAddRequested.emit(float(sp.x()), float(sp.y()))
                e.accept()
                return
            if self._tool == "rect":
                if self._draw_start is None:          # 第一点：起点
                    self._draw_start = sp
                    self._rubber = _RubberItem()
                    self._rubber.setRect(QRectF(sp, sp))
                    self._scene.addItem(self._rubber)
                else:                                 # 第二点：收框
                    self._finish_draw(sp)
                e.accept()
                return
            h = self._handle_at(e.pos())              # 正在编辑框 → 先看角点
            if h is not None:
                self._drag_corner = h.corner
                self._drag_base = self.current_box()
                self.viewport().setCursor(Qt.ClosedHandCursor)
                e.accept()
                return
            if self._edit_idx is not None:
                self.end_box_edit()                   # 点别处 = 结束编辑
            mk = self._pin_at(e.pos())
            if mk is not None:
                key = (mk.chip_idx, mk.pin_idx)
                if e.modifiers() & Qt.ControlModifier:
                    # Ctrl + 左键：加选 / 减选（批量选点用）
                    if key in self._pin_sel:
                        self._pin_sel.discard(key)
                    else:
                        self._pin_sel.add(key)
                else:
                    self._pin_sel = {key}
                self._sync_pin_sel()
                e.accept()
                return
            if self._pin_sel and not (e.modifiers() & Qt.ControlModifier):
                self.clear_pin_selection()
            it = self._box_at(e.pos())
            self.select(it.index if it is not None else -1)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            d = e.pos() - self._pan
            self._pan = e.pos()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - d.x())
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - d.y())
            e.accept()
            return
        sp = self.mapToScene(e.pos())
        if self._drag_corner is not None:              # 拖角点改大小：实时预览
            self._drag_to(sp)
            e.accept()
            return
        if self._draw_start is not None and self._rubber is not None:
            p0 = self._draw_start                            # 橡皮筋跟鼠标
            self._rubber.setRect(QRectF(p0, sp).normalized())
            e.accept()
            return
        if self._edit_idx is not None:                 # 悬停角点时换光标
            h = self._handle_at(e.pos())
            self.viewport().setCursor(
                Qt.SizeFDiagCursor if h is not None else Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton and self._pan is not None:
            self._pan = None
            self._apply_cursor()          # 画笔/画框模式下不能恢复成箭头
            e.accept()
            return
        if e.button() == Qt.LeftButton and self._drag_corner is not None:
            idx = self._edit_idx
            self._drag_corner = None
            self._drag_base = None
            box = self.current_box(idx)
            self.viewport().setCursor(Qt.SizeAllCursor)
            if idx is not None and box is not None:
                self.boxResizeRequested.emit(int(idx), [float(v) for v in box])
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self._tool == "rect" or self._draw_start is not None:
            e.accept()               # 画框时别把双击当成"适应窗口"，会跳缩放
            return
        self.fit()
        e.accept()

    def contextMenuEvent(self, e):
        mk = self._pin_at(e.pos())
        it = self._box_at(e.pos())
        m = QMenu(self)
        m.setStyleSheet(
            "QMenu{background:#232a33;color:#dfe3e8;border:1px solid #3d4653;"
            "padding:4px;}"
            "QMenu::item{padding:6px 22px;border-radius:4px;}"
            "QMenu::item:selected{background:#2b6fd6;}"
            "QMenu::item:disabled{color:#6b7480;}")
        if mk is not None:
            key = (mk.chip_idx, mk.pin_idx)
            if key not in self._pin_sel:          # 右键的那个点先纳入选中
                self._pin_sel = {key}
                self._sync_pin_sel()
            a_del = QAction("删除该点", self)
            a_del.triggered.connect(
                lambda _c=False, k=key: self.pinsDeleteRequested.emit([k]))
            m.addAction(a_del)
            if len(self._pin_sel) > 1:
                a_del2 = QAction("删除选中的 %d 个点" % len(self._pin_sel), self)
                a_del2.triggered.connect(
                    lambda _c=False: self.pinsDeleteRequested.emit(
                        sorted(self._pin_sel)))
                m.addAction(a_del2)
            info = QAction("已选中 %d 个点（Ctrl+左键可加选）" % len(self._pin_sel),
                           self)
            info.setEnabled(False)
            m.addAction(info)
            m.addSeparator()
        elif it is not None:
            self.select(it.index)
            tag = self._label_of.get(it.index)
            act = QAction("单芯片重跑（用新算法）", self)
            act.triggered.connect(
                lambda _c=False, i=it.index: self.rerunRequested.emit(i))
            m.addAction(act)
            a_edit = QAction("编辑矩形框（拖角点改大小）", self)
            a_edit.triggered.connect(
                lambda _c=False, i=it.index: self.begin_box_edit(i))
            m.addAction(a_edit)
            a_lab = QAction("编辑标签名（O / F / T）", self)
            a_lab.triggered.connect(
                lambda _c=False, i=it.index: self.chipLabelRequested.emit(i))
            m.addAction(a_lab)
            a_delbox = QAction("删除此矩形框（连同框内点）", self)
            a_delbox.triggered.connect(
                lambda _c=False, i=it.index: self.chipDeleteRequested.emit(i))
            m.addAction(a_delbox)
            if self._pin_sel:
                a_del2 = QAction("删除选中的 %d 个点" % len(self._pin_sel), self)
                a_del2.triggered.connect(
                    lambda _c=False: self.pinsDeleteRequested.emit(
                        sorted(self._pin_sel)))
                m.addAction(a_del2)
            m.addSeparator()
            info = QAction("已选中 %s" % (tag if tag else "第 %d 个框" % (it.index + 1)),
                           self)
            info.setEnabled(False)
            m.addAction(info)
            m.addSeparator()
        else:
            if self._edit_idx is not None:
                info = QAction("正在编辑 %s —— 拖四角改大小，Esc 结束"
                               % (self._label_of.get(self._edit_idx) or
                                  "第 %d 个框" % (self._edit_idx + 1)), self)
                info.setEnabled(False)
                m.addAction(info)
                m.addSeparator()
            tip = QAction("左键点选框 / 点选引脚点，右键操作", self)
            tip.setEnabled(False)
            m.addAction(tip)
            m.addSeparator()
        a_undo = QAction("撤回上一步（Ctrl+Z）", self)
        a_undo.triggered.connect(lambda _c=False: self.undoRequested.emit())
        m.addAction(a_undo)
        m.addSeparator()
        a_fit = QAction("适应窗口", self)
        a_fit.triggered.connect(self.fit)
        m.addAction(a_fit)
        m.exec_(e.globalPos())

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            if self._draw_start is not None:
                self.cancel_draw()          # 画到一半 -> 取消画框
                e.accept()
                return
            if self._edit_idx is not None:
                self.end_box_edit()
                e.accept()
                return
            self.clear_pin_selection()
            self.select(-1)
            e.accept()
            return
        if e.key() == Qt.Key_Z and (e.modifiers() & Qt.ControlModifier):
            self.undoRequested.emit()
            e.accept()
            return
        if e.key() == Qt.Key_Delete:
            if self._pin_sel:
                self.pinsDeleteRequested.emit(sorted(self._pin_sel))
            e.accept()
            return
        if e.key() == Qt.Key_0 and (e.modifiers() & Qt.ControlModifier):
            self.fit()
            e.accept()
            return
        super().keyPressEvent(e)
