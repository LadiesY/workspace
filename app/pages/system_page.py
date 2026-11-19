# -*- coding: utf-8 -*-
"""08 系统管理 —— 五个子功能的壳。

    用户管理 │ 角色权限 │ 审计日志 │ 备份恢复 │ 系统设置

子页签按**当前账号的权限动态生成**：没有 sys.user 就看不到「用户管理」，
连按钮都不出现，不是灰着等人来点。进不来这个模块的（比如操作员）在导航层
就被拦下了（见 main_window.select_module）。

标题条不画子页名：五个子页都属于「08 系统管理」这一个模块，编号也共用，
换成"08 用户管理"会跟导航上的编号打架（设备管理页那边是两页一个模块，
所以那边切换时会换名字，这里不跟）。
"""

from PyQt5.QtCore import QTimer, pyqtSignal
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from .. import auth
from ..widgets import HEAD_H, HEAD_TOP, paint_module_head
from .device_module import SubTab
from .sys_audit import AuditTab
from .sys_common import PAGE_QSS
from .sys_backup import BackupTab
from .sys_roles import RolesTab
from .sys_settings import SettingsTab
from .sys_users import UsersTab

STRIP_H = 40          # 页签条高度（SubTab 自身 30，上下各留 5）

# (key, 显示名, 需要的功能点, 类, 一句话说明)
TABS = (
    ("users", "用户管理", "sys.user", UsersTab),
    ("roles", "角色权限", "sys.role", RolesTab),
    ("audit", "审计日志", "sys.audit", AuditTab),
    ("backup", "备份恢复", "sys.backup", BackupTab),
    ("settings", "系统设置", "sys.setting", SettingsTab),
)


class SystemPage(QWidget):
    perm_changed = pyqtSignal()
    sub_changed = pyqtSignal(object)      # 当前子页名，给顶栏面包屑

    def __init__(self, mod, parent=None):
        super(SystemPage, self).__init__(parent)
        self.mod = mod
        self._tabs = []
        self._pages = []
        # 页面级样式（分组标题、提示文字、分隔线）。规则都带选择器，
        # 挂在这里就够，别在各页里再设无选择器的样式。
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
        # 注意：这里不写 setStyleSheet("background: transparent") —— 无选择器的
        # 声明会向下覆盖子控件样式，按钮会变成白底白字（在登入框上踩过一次）。
        # QStackedWidget 本来就不刷背景，白卡由各页自己画。
        root.addWidget(self.stack, 1)

        self._build()

    # ---------------------------------------------------------------- 构建
    def _granted(self):
        """当前账号能看的页签 key 列表。权限一改这个结果就变。"""
        return [t[0] for t in TABS if auth.session.can(t[2])]

    def is_stale(self):
        """页签和权限对不上了？登入/登出/改权限之后都要问一次。"""
        return self._granted() != [t.key for t in self._tabs]

    def _build(self, keep=None):
        """按权限建页签。权限改了就整体重建（页签可能多一个少一个）。

        keep 是重建前停留的子页 key：改完权限还停在同一页，别把人甩回第一页。
        """
        granted = set(self._granted())
        for key, name, point, cls in TABS:
            if key not in granted:
                continue
            tab = SubTab(key, name, self.strip)
            tab.clicked.connect(self._on_tab)
            tab.set_active(False)
            # 页签是手动 move 的，没进布局；窗口已经显示时新建的子控件默认隐藏，
            # 必须显式 show（第一次构建时靠父窗口 show 带出来，所以看不出问题，
            # 重建时才露馅：内容在、页签没了）
            tab.show()
            page = cls(self)
            # 角色权限页改完要通知主窗口重建页签、重刷导航
            if hasattr(page, "perm_changed"):
                page.perm_changed.connect(self.perm_changed)
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
        super(SystemPage, self).resizeEvent(e)
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
        # 切到某页就把它的数据重读一遍：审计/备份是外部会变的
        page = self._pages[i]
        if hasattr(page, "reload"):
            try:
                page.reload()
            except Exception:
                pass
        self.sub_changed.emit(self.sub_name)

    @property
    def sub_name(self):
        """面包屑上显示子页名。"""
        i = self.stack.currentIndex()
        if 0 <= i < len(self._tabs):
            return self._tabs[i]._text
        return None

    def showEvent(self, e):
        super(SystemPage, self).showEvent(e)
        # 这个页是在"还没登入"的时候建出来的，那时一个页签都没有。
        # 每次显示都对着权限核一遍，登录后/换了角色后自动补齐。
        if self.is_stale():
            self.schedule_refresh()      # 延迟一轮，别在 showEvent 里删自己
            return
        i = self.stack.currentIndex()
        if 0 <= i < len(self._pages) and hasattr(self._pages[i], "reload"):
            self._pages[i].reload()

    # ---------------------------------------------------------------- 权限变更
    def schedule_refresh(self):
        """角色权限改过之后重建页签。延迟一轮，别在信号回调里删掉发信号的页。"""
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
