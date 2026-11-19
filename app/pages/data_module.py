# -*- coding: utf-8 -*-
"""06 数据管理 —— 五个子功能的壳。

    结果查询 │ 波形回放 │ 报表生成 │ 数据导出 │ 批次统计

这一版**只做布局**：五个子页的骨架、工具栏、表格、画布都摆好了，但没有取数
逻辑 —— 表格留空、指标写「—」、波形与统计图是示意形状（页面上都有标注）。
接数据时只改各 Tab 的 `reload()`，布局一行不用动。

为什么和「系统管理」一样用"页头下方一条独立页签条"
--------------------------------------------------
五个页签横排要 ~520px，塞进页头右侧（标题块要 ~420px）在 1100 宽的窗口上
一定会叠字。系统管理那边已经验证过：单起一条 40px 的页签条最稳，页头只
放编号和标题。

子页按权限生成
--------------
`data.view` 看查询/波形/统计，`data.export` 才能看报表与导出。没给的不生成
（不是生成后隐藏）—— 跟系统管理保持同一套做法，权限矩阵里改完即时生效。
"""

from PyQt5.QtCore import QTimer, pyqtSignal
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from .. import auth
from ..widgets import HEAD_H, HEAD_TOP, paint_module_head
from .data_batch import BatchTab
from .data_export import ExportTab
from .data_query import QueryTab
from .data_report import ReportTab
from .data_wave import WaveTab
from .device_module import SubTab
from .sys_common import PAGE_QSS

STRIP_H = 40          # 页签条高度（SubTab 自身 30，上下各留 5）

# (key, 显示名, 需要的功能点, 类)
TABS = (
    ("query", "结果查询", "data.view", QueryTab),
    ("wave", "波形回放", "data.view", WaveTab),
    ("report", "报表生成", "data.export", ReportTab),
    ("export", "数据导出", "data.export", ExportTab),
    ("batch", "批次统计", "data.view", BatchTab),
)


class DataPage(QWidget):
    sub_changed = pyqtSignal(object)      # 当前子页名，给顶栏面包屑

    def __init__(self, mod, parent=None):
        super(DataPage, self).__init__(parent)
        self.mod = mod
        self._tabs = []
        self._pages = []
        self.setStyleSheet(PAGE_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 0, 18, 18)
        root.setSpacing(0)

        self.head = QWidget(self)
        self.head.setFixedHeight(HEAD_H)
        root.addWidget(self.head)

        self.strip = QWidget(self)
        self.strip.setFixedHeight(STRIP_H)
        root.addWidget(self.strip)

        self.stack = QStackedWidget(self)
        # 不写无选择器的 setStyleSheet（会向下覆盖子控件样式），透出父窗口背景
        root.addWidget(self.stack, 1)

        self._build()

    # ---------------------------------------------------------------- 构建
    def _granted(self):
        return [t[0] for t in TABS if auth.session.can(t[2])]

    def is_stale(self):
        return self._granted() != [t.key for t in self._tabs]

    def _build(self, keep=None):
        granted = set(self._granted())
        for key, name, point, cls in TABS:
            if key not in granted:
                continue
            tab = SubTab(key, name, self.strip)
            tab.clicked.connect(self._on_tab)
            tab.set_active(False)
            # 手动 move 的页签不在布局里，窗口已显示时新建默认隐藏 —— 必须显式 show
            tab.show()
            page = cls(self)
            self._tabs.append(tab)
            self._pages.append(page)
            self.stack.addWidget(page)
        self._place_tabs()
        if self._pages:
            keys = [t.key for t in self._tabs]
            self._select(keys.index(keep) if keep in keys else 0)

    def _place_tabs(self):
        x = 0
        for tab in self._tabs:
            tab.move(x, 5)
            x += tab.width() + 6

    def resizeEvent(self, e):
        super(DataPage, self).resizeEvent(e)
        self._place_tabs()

    # ---------------------------------------------------------------- 切换
    def _on_tab(self, key):
        for i, tab in enumerate(self._tabs):
            if tab.key == key:
                self._select(i)
                return

    def _select(self, i):
        if not (0 <= i < len(self._pages)):
            return
        self.stack.setCurrentIndex(i)
        for k, tab in enumerate(self._tabs):
            tab.set_active(k == i)
        page = self._pages[i]
        if hasattr(page, "reload"):
            try:
                page.reload()
            except Exception:
                pass
        self.sub_changed.emit(self.sub_name)

    @property
    def sub_name(self):
        i = self.stack.currentIndex()
        if 0 <= i < len(self._tabs):
            return self._tabs[i]._text
        return None

    def showEvent(self, e):
        super(DataPage, self).showEvent(e)
        # 未登入时建过一次（0 个页签），登入后要对着权限补齐
        if self.is_stale():
            self.schedule_refresh()
            return
        i = self.stack.currentIndex()
        if 0 <= i < len(self._pages) and hasattr(self._pages[i], "reload"):
            self._pages[i].reload()

    # ---------------------------------------------------------------- 权限变更
    def schedule_refresh(self):
        QTimer.singleShot(0, self._refresh_soft)

    def _refresh_soft(self):
        keep = None
        i = self.stack.currentIndex()
        if 0 <= i < len(self._tabs):
            keep = self._tabs[i].key
        for page in self._pages:
            try:
                page.setParent(None)
                page.deleteLater()
            except Exception:
                pass
        for tab in self._tabs:
            tab.setParent(None)
            tab.deleteLater()
        self._pages, self._tabs = [], []
        self._build(keep=keep)
        self.update()

    # ---------------------------------------------------------------- 绘制
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        paint_module_head(p, 18, HEAD_TOP, self.width() - 36,
                          self.mod["idx"], self.mod["name"], self.mod["desc"])
        p.end()
