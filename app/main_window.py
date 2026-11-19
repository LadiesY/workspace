# -*- coding: utf-8 -*-
"""主窗口：左侧导航 + 顶部栏（时钟 / 账户）+ 工作区 + 底部状态栏。

这一版是空壳：没有任何设备连接、没有业务逻辑、没有示例数据。
模块切换只做界面切换。
"""

import sys
import time

from PyQt5.QtCore import (QEvent, QObject, QPoint, QPointF, QDateTime, QRect,
                          QSize, Qt, QTimer, pyqtSignal)
from PyQt5.QtGui import (QColor, QFontMetrics, QIcon, QLinearGradient, QPainter,
                         QPen, QRadialGradient)
from PyQt5.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                             QMenu, QPushButton, QStackedWidget, QVBoxLayout,
                             QWidget)

from . import auth, backup as bk, icons, theme
from .audit import audit
from .devices import DeviceManager
from .modules import MODULES
from .pages import (AutoPage, DataPage, DeviceModulePage, HomePage, KbPage,
                    SystemPage, TestPage, VisionPage)
from .pages import sys_common as C
from .pages.auth_dialog import AuthDialog
from .widgets import GridCanvas, Led, NavItem, PlaceholderPage, _font, _q

STYLE_STATUS = (
    "background: rgba(27,87,160,0.035);"
    "border-top: 1px solid %s;" % theme.LINE
)


def _style_titlebar(hwnd):
    """把 Windows 标题栏刷成界面的浅色，让整个窗口看起来是一体的。

    Win11（build 22000+）支持 DWMWA_CAPTION_COLOR / TEXT_COLOR / BORDER_COLOR，
    可以精确指定颜色。**先关掉深色模式**（属性 20/19 置 0），否则系统会把
    标题文字反白，白底白字就看不见了。
    """
    try:
        import ctypes
        from ctypes import wintypes
        dwm = ctypes.windll.dwmapi

        def _set(attr, value):
            v = ctypes.c_int(value)
            rc = dwm.DwmSetWindowAttribute(
                wintypes.HWND(hwnd), ctypes.c_int(attr),
                ctypes.byref(v), ctypes.sizeof(v))
            return rc == 0

        # 浅色模式：界面上是白底深字，标题栏也得跟着，不然窗口顶上一道黑边
        for attr in (20, 19):
            if _set(attr, 0):
                break

        def _ref(hex_color):
            # DWM 要的是 COLORREF：0x00BBGGRR
            r = int(hex_color[1:3], 16)
            g = int(hex_color[3:5], 16)
            b = int(hex_color[5:7], 16)
            return (b << 16) | (g << 8) | r

        _set(35, _ref(theme.SIDEBAR_TOP))   # DWMWA_CAPTION_COLOR
        _set(36, _ref(theme.TXT))           # DWMWA_TEXT_COLOR
        _set(34, _ref(theme.LINE2))         # DWMWA_BORDER_COLOR
        return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------- 侧栏
class BrandBlock(QWidget):
    """顶部品牌区：芯片标志 + 软件名 + 英文副题 + 渐隐分隔线。"""

    H = 80

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.H)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        w = self.width()

        pm = icons.logo(40, self.devicePixelRatioF())
        p.drawPixmap(16, 20, pm)

        p.setFont(_font(theme.F_BODY, 17, bold=True, spacing=0.9))
        p.setPen(_q(theme.TXT))
        p.drawText(QRect(68, 15, w - 78, 22), Qt.AlignLeft | Qt.AlignVCenter,
                   theme.APP_NAME)

        p.setFont(_font(theme.F_MONO, 9, spacing=1.3))
        p.setPen(_q(theme.TXT3))
        p.drawText(QRect(68, 38, w - 78, 14), Qt.AlignLeft | Qt.AlignVCenter,
                   "PCB TEST SYSTEM")

        # 底部渐隐线（原型 .brand::after）
        g = QLinearGradient(16, 0, 16 + (w - 32) * 0.7, 0)
        g.setColorAt(0.0, _q(theme.AMBER, 115))
        g.setColorAt(1.0, _q(theme.AMBER, 0))
        p.setPen(QPen(g, 1))
        p.drawLine(16, self.H - 1, w - 16, self.H - 1)
        p.end()


class SideStatus(QWidget):
    """侧栏底部三行信息。显示的都是真实值，不编数据。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(STYLE_STATUS)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(15, 13, 15, 13)
        lay.setSpacing(7)

        rows = [
            ("软件版本", theme.APP_VER),
            ("运行环境", "Python %d.%d.%d" % sys.version_info[:3]),
            ("设备地址", "未连接"),
        ]
        self._vals = {}
        for k, v in rows:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            lk = QLabel(k)
            lk.setFont(_font(theme.F_BODY, 10, spacing=0.2))
            lk.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
            lv = QLabel(v)
            lv.setFont(_font(theme.F_MONO, 10, spacing=0.2))
            lv.setStyleSheet("color:%s;background:transparent;" % theme.TXT2)
            lv.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(lk)
            row.addStretch(1)
            row.addWidget(lv)
            lay.addLayout(row)
            self._vals[k] = lv

    def set_device_addr(self, addr):
        """接入设备管理后由连接流程调用；None 或空串一律显示未连接。"""
        self._vals["设备地址"].setText(addr or "未连接")


class SideBar(QWidget):
    """左侧导航栏，246px，渐变底 + 右侧渐隐高光线。"""

    moduleSelected = None  # type: ignore

    def __init__(self, on_select, parent=None):
        super().__init__(parent)
        self.setFixedWidth(theme.SIDEBAR_W)
        self._items = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        lay.addWidget(BrandBlock())

        cap = QLabel("功能模块 / MODULES")
        cap.setFont(_font(theme.F_BODY, 9, spacing=2.0))
        cap.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        cap.setContentsMargins(16, 18, 16, 9)
        lay.addWidget(cap)

        wrap = QVBoxLayout()
        wrap.setContentsMargins(9, 0, 9, 0)
        wrap.setSpacing(2)
        for i, mod in enumerate(MODULES):
            it = NavItem(mod["idx"], mod["name"], mod["icon"])
            it.clicked.connect(lambda idx=i: on_select(idx))
            self._items.append(it)
            wrap.addWidget(it)
        lay.addLayout(wrap)

        lay.addStretch(1)
        self.status = SideStatus()
        lay.addWidget(self.status)

        self._items[0].set_active(True)

    def set_active(self, index):
        for i, it in enumerate(self._items):
            it.set_active(i == index)

    def set_locked(self, logged_in):
        """未登入时，除首页外的模块都挂一把小锁（视觉提示）。"""
        for i, it in enumerate(self._items):
            it.set_locked((not logged_in) and MODULES[i]["key"] != "home")

    def paintEvent(self, _e):
        p = QPainter(self)
        r = self.rect()
        g = QLinearGradient(0, 0, 0, r.height())
        g.setColorAt(0.0, _q(theme.SIDEBAR_TOP))
        g.setColorAt(1.0, _q(theme.SIDEBAR_BOT))
        p.fillRect(r, g)

        # 右边框 + 沿边渐隐的高光（原型 .sidebar::after）。
        # 浅色版不要青色：浅蓝底上一条青绿会像没洗干净，统一走主强调蓝。
        p.setPen(QPen(_q(theme.LINE), 1))
        p.drawLine(r.right(), 0, r.right(), r.height())
        hg = QLinearGradient(0, 0, 0, r.height())
        hg.setColorAt(0.00, _q(theme.AMBER, 0))
        hg.setColorAt(0.34, _q(theme.AMBER, 64))
        hg.setColorAt(0.66, _q(theme.AMBER, 40))
        hg.setColorAt(1.00, _q(theme.AMBER, 0))
        p.setPen(QPen(hg, 1))
        p.drawLine(r.right(), 0, r.right(), r.height())
        p.end()


# ---------------------------------------------------------------- 顶部
class Avatar(QWidget):
    """账户头像：30px 圆，径向渐变 + 边框 + 外圈淡蓝环。"""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._text = text
        self.setFixedSize(34, 34)

    def set_text(self, text):
        text = text or "?"
        if text != self._text:
            self._text = text
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        cx, cy, r = 17.0, 17.0, 15.0

        p.setPen(QPen(_q(theme.AMBER, 40), 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r + 1.5, r + 1.5)

        g = QRadialGradient(QPointF(cx - 4, cy - 5), r * 1.9)
        g.setColorAt(0.0, _q("#eaf2fb"))
        g.setColorAt(1.0, _q("#cadff2"))
        p.setPen(QPen(_q(theme.LINE2), 1))
        p.setBrush(g)
        p.drawEllipse(QPointF(cx, cy), r, r)

        p.setFont(_font(theme.F_BODY, 13))
        p.setPen(_q(theme.TXT))
        p.drawText(QRect(0, 0, 34, 34), Qt.AlignCenter, self._text)
        p.end()


class Clock(QWidget):
    """时 / 分 / 秒 + 日期。每秒取一次系统时间，系统改时间也能跟上。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)

        self.lb_time = QLabel("--:--:--")
        self.lb_time.setFont(_font(theme.F_DISP, 15, bold=True, spacing=1.6))
        self.lb_time.setStyleSheet("color:%s;background:transparent;" % theme.TXT)
        self.lb_time.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.lb_date = QLabel("----------")
        self.lb_date.setFont(_font(theme.F_MONO, 9, spacing=1.0))
        self.lb_date.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        self.lb_date.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        lay.addWidget(self.lb_time)
        lay.addWidget(self.lb_date)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._tick()
    def _tick(self):
        now = QDateTime.currentDateTime()
        self.lb_time.setText(now.toString("HH:mm:ss"))
        self.lb_date.setText("%s %s" % (now.toString("yyyy-MM-dd"),
                                        self._weekday(now.date().dayOfWeek())))

    @staticmethod
    def _weekday(n):
        return ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[n - 1]


class AccountWidget(QWidget):
    """顶栏右侧的账户区。

    未登入：问号头像 + 「未登入 / 本地账户」+【登入】【注册】两个按钮。
    已登入：首字头像 + 用户名 + 角色名，点一下弹菜单（修改密码 / 退出登入）。

    这一块是**唯一的登入入口**：工具打开就是首页，没登入也能看宣传页，
    但 02~08 八个功能模块点了会被拦下并提示（见 MainWindow.select_module）。
    """

    login_clicked = pyqtSignal()
    register_clicked = pyqtSignal()
    logout_clicked = pyqtSignal()
    password_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super(AccountWidget, self).__init__(parent)
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(9)

        self.avatar = Avatar("?")
        lay.addWidget(self.avatar)

        meta = QVBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(1)
        self.lb_name = QLabel("未登入")
        self.lb_name.setFont(_font(theme.F_BODY, 12, bold=True, spacing=0.2))
        self.lb_name.setStyleSheet("color:%s;background:transparent;" % theme.TXT2)
        self.lb_role = QLabel("本地账户")
        self.lb_role.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        self.lb_role.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        meta.addWidget(self.lb_name)
        meta.addWidget(self.lb_role)
        lay.addLayout(meta)

        self.btn_in = QPushButton("登入")
        self.btn_reg = QPushButton("注册")
        for b in (self.btn_in, self.btn_reg):
            b.setObjectName("topLink")
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(24)
            b.setFont(_font(theme.F_BODY, 11, spacing=0.4))
        self.btn_in.clicked.connect(lambda: self.login_clicked.emit())
        self.btn_reg.clicked.connect(lambda: self.register_clicked.emit())
        lay.addSpacing(2)
        lay.addWidget(self.btn_in)
        lay.addWidget(self.btn_reg)

    # ---------------------------------------------------------------- 状态
    def set_logged_out(self):
        self.avatar.set_text("?")
        self.lb_name.setText("未登入")
        self.lb_name.setStyleSheet("color:%s;background:transparent;" % theme.TXT2)
        self.lb_role.setText("本地账户")
        self.btn_in.setVisible(True)
        self.btn_reg.setVisible(True)
        self.setToolTip("未登入：功能模块 02~08 需要登入后才能使用")
        self.setCursor(Qt.ArrowCursor)

    def set_user(self, name, role_name):
        self.avatar.set_text((name or "?")[:1])
        self.lb_name.setText(name)
        self.lb_name.setStyleSheet("color:%s;background:transparent;" % theme.TXT)
        self.lb_role.setText(role_name)
        self.btn_in.setVisible(False)
        self.btn_reg.setVisible(False)
        self.setToolTip("点这里：修改密码 / 退出登入")
        self.setCursor(Qt.PointingHandCursor)

    # ---------------------------------------------------------------- 菜单
    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and auth.session.logged_in \
                and self.rect().contains(e.pos()):
            self._menu(e.pos())
            e.accept()
            return
        super(AccountWidget, self).mouseReleaseEvent(e)

    def _menu(self, pos):
        m = QMenu(self)
        m.setStyleSheet(theme.menu_qss())
        a_pwd = m.addAction("修改密码")
        m.addSeparator()
        a_out = m.addAction("退出登入")
        act = m.exec_(self.mapToGlobal(pos))
        if act is a_pwd:
            self.password_clicked.emit()
        elif act is a_out:
            self.logout_clicked.emit()


class TopBar(QWidget):
    """58px 顶栏：面包屑（左）+ 时钟与账户（右）。

    按需求不放设备连接状态；中间留白，不摆任何假数据。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(theme.TOPBAR_H)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 18, 0)
        lay.setSpacing(0)

        crumbs = QHBoxLayout()
        crumbs.setContentsMargins(0, 0, 0, 0)
        crumbs.setSpacing(8)
        self._crumb_root = self._crumb("工作站", theme.TXT3)
        self._crumb_sep1 = self._crumb("/", "#b4c8da")
        self._crumb_mod = self._crumb(MODULES[0]["name"], theme.TXT2)
        self._crumb_sep2 = self._crumb("/", "#b4c8da")
        self._crumb_sub = self._crumb("待接入", theme.TXT3)
        for w in (self._crumb_root, self._crumb_sep1, self._crumb_mod,
                  self._crumb_sep2, self._crumb_sub):
            crumbs.addWidget(w)
        lay.addLayout(crumbs)
        lay.addStretch(1)

        lay.addWidget(Clock())

        div = QWidget()
        div.setFixedSize(1, 22)
        div.setStyleSheet("background:%s;" % theme.LINE2)
        lay.addSpacing(14)
        lay.addWidget(div)
        lay.addSpacing(14)

        self.account = AccountWidget()
        lay.addWidget(self.account)

    @staticmethod
    def _crumb(text, color):
        lb = QLabel(text)
        lb.setFont(_font(theme.F_BODY, 12, spacing=0.4))
        lb.setStyleSheet("color:%s;background:transparent;" % color)
        return lb

    def set_module(self, mod, sub=None):
        """sub 给模块内的子页签用（设备管理 / 运动控制），没有就显示接入状态。"""
        self._mod = mod
        self._crumb_mod.setText(mod["name"])
        if not mod.get("ready"):
            self._crumb_sub.setText("待接入")
            self._crumb_sub.setStyleSheet(
                "color:%s;background:transparent;" % theme.TXT3)
            return
        self._crumb_sub.setText(sub or "已接入")
        self._crumb_sub.setStyleSheet(
            "color:%s;background:transparent;" % theme.CYAN)

    def paintEvent(self, _e):
        p = QPainter(self)
        r = self.rect()
        g = QLinearGradient(0, 0, 0, r.height())
        g.setColorAt(0.0, _q(theme.TOPBAR_TOP))
        g.setColorAt(1.0, _q(theme.TOPBAR_BOT))
        p.fillRect(r, g)
        p.setPen(QPen(_q(theme.LINE), 1))
        p.drawLine(0, r.bottom(), r.width(), r.bottom())
        p.end()


# ---------------------------------------------------------------- 底部
class StatusBar(QWidget):
    """30px 底栏：运行状态 + 版本。不放坐标/力值这类还没有数据来源的字段。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(theme.STATUSBAR_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(0)

        self.led = Led(theme.CYAN, 6)
        self.msg = QLabel("系统就绪")
        self.msg.setFont(_font(theme.F_BODY, 10, spacing=0.4))
        self.msg.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)

        lay.addWidget(self.led)
        lay.addSpacing(6)
        lay.addWidget(self.msg)
        lay.addStretch(1)

        # 登入状态：一眼看清"现在是谁在用"，旁边就是版本号
        self.lb_user = QLabel("未登入")
        self.lb_user.setFont(_font(theme.F_MONO, 9, spacing=0.4))
        self.lb_user.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        lay.addWidget(self.lb_user)
        lay.addSpacing(16)

        for text in (theme.APP_VER,):
            lb = QLabel(text)
            lb.setFont(_font(theme.F_MONO, 10, spacing=0.5))
            lb.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
            lay.addWidget(lb)
            lay.addSpacing(14)

    def set_user(self, name="", role=""):
        if not name:
            self.lb_user.setText("未登入")
            self.lb_user.setStyleSheet(
                "color:%s;background:transparent;" % theme.TXT3)
        else:
            self.lb_user.setText("%s · %s" % (name, role))
            self.lb_user.setStyleSheet(
                "color:%s;background:transparent;" % theme.TXT2)

    def set_devices(self, n):
        """底栏一句话状态：连上几台设备。n 由 DeviceManager.connected_count() 给。"""
        if n <= 0:
            text, col = "系统就绪 · 设备未连接", theme.TXT3
        else:
            text, col = "系统就绪 · 已连接 %d 台设备" % n, theme.TXT2
        if self.msg.text() != text:
            self.msg.setText(text)
            self.msg.setStyleSheet("color:%s;background:transparent;" % col)
            self.led.set_color(theme.OK if n > 0 else theme.TXT3)

    def paintEvent(self, _e):
        p = QPainter(self)
        r = self.rect()
        g = QLinearGradient(0, 0, 0, r.height())
        g.setColorAt(0.0, _q("#ffffff"))
        g.setColorAt(1.0, _q("#f3f8fd"))
        p.fillRect(r, g)
        p.setPen(QPen(_q(theme.LINE), 1))
        p.drawLine(0, 0, r.width(), 0)
        p.end()


# ---------------------------------------------------------------- 工作区
class Workspace(GridCanvas):
    """网格背景 + 堆叠页面。页面自身不填背景，透出网格。

    已接入功能的模块用真实页面，其余仍是占位页 —— 一眼能看出哪块做了。
    """

    def __init__(self, manager=None, parent=None):
        super().__init__(parent)
        self.stack = QStackedWidget(self)
        self.stack.setAttribute(Qt.WA_TranslucentBackground, True)
        # 选择器必须写全（QStackedWidget {...}）。无选择器的 background/color
        # 会**向下传染给所有子孙控件**，连 QComboBox 弹出列表的 viewport 都会被
        # 刷成透明，弹出来就是黑底 + 灰字（实测过：下拉框背景 #000000）。
        self.stack.setStyleSheet("QStackedWidget { background: transparent; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.stack)
        for mod in MODULES:
            if mod["key"] == "home":
                # 首页是宣传页（系统全景 + 效益图表），不接设备，单独一类
                self.home_page = HomePage(mod)
                self.stack.addWidget(self.home_page)
            elif mod["key"] == "device" and manager is not None:
                # 设备管理 + 运动控制合成一页，两个子页签
                self.device_page = DeviceModulePage(mod, manager)
                self.stack.addWidget(self.device_page)
            elif mod["key"] == "auto" and manager is not None:
                self.stack.addWidget(AutoPage(mod, manager))
            elif mod["key"] == "test" and manager is not None:
                self.stack.addWidget(TestPage(mod, manager))
            elif mod["key"] == "vision" and manager is not None:
                self.stack.addWidget(VisionPage(mod, manager))
            elif mod["key"] == "data":
                # 数据管理：五个子页（查询 / 波形 / 报表 / 导出 / 统计），
                # 这一版只做布局，不接采集数据
                self.data_page = DataPage(mod)
                self.stack.addWidget(self.data_page)
            elif mod["key"] == "kb":
                self.stack.addWidget(KbPage(mod))
            elif mod["key"] == "system":
                self.system_page = SystemPage(mod)
                self.stack.addWidget(self.system_page)
            else:
                self.stack.addWidget(PlaceholderPage(mod))


# ---------------------------------------------------------------- 主窗口
class PermGuard(QObject):
    """挂在控件上的点击拦截器。

    为什么不用 setEnabled(False)：禁用只能说明"现在不能点"，说不清为什么，
    也留不下痕迹。条例要求"越权操作被拒绝并写审计"，所以这里点下去先判权限，
    不够就拦下来、给原因、记一条失败记录。

    拦截装在**控件自身**上，不是整页：整页禁用会把正常的查看也一起挡掉。
    运动控制是例外（整块都是动作，没有"只看"的部分），用 deep=True 连子孙控件
    一起装 —— 点在按钮内文字上时，事件目标是子控件，只装父控件是拦不住的。
    """

    def __init__(self, window):
        super(PermGuard, self).__init__(window)
        self.win = window
        self._pairs = 0

    def add(self, widget, point, label, deep=False):
        if widget is None:
            return
        self._install(widget, point, label)
        if deep:
            for child in widget.findChildren(QWidget):
                self._install(child, point, label)

    def _install(self, w, point, label):
        w.setProperty("_perm_point", point)
        w.setProperty("_perm_label", label)
        w.installEventFilter(self)
        self._pairs += 1

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.MouseButtonPress:
            point = obj.property("_perm_point")
            if point and not auth.session.can(point):
                self._deny(obj.property("_perm_label") or point, point)
                return True          # 吞掉，别让按钮真的动起来
        return False

    def _deny(self, label, point):
        audit.write("越权操作被拒绝", target=label, result="FAIL",
                    detail="角色 %s 缺少功能点 %s" % (auth.session.role_name, point))
        C.tell(self.win, "权限不足",
               "当前角色（%s）无权%s。\n\n如需使用，请联系管理员在"
               "「系统管理 → 角色权限」里授权。" % (auth.session.role_name, label),
               kind="warn")


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(theme.APP_NAME)
        self.setWindowIcon(QIcon(icons.logo(64, 1.0)))
        self.setMinimumSize(QSize(1100, 700))

        self._current = 0
        self._titlebar_done = False

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.side = SideBar(self.select_module)
        self.topbar = TopBar()
        self.manager = DeviceManager(self)
        self.workspace = Workspace(self.manager)
        self.statusbar = StatusBar()

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self.topbar)
        right.addWidget(self.workspace, 1)
        right.addWidget(self.statusbar)

        root.addWidget(self.side)
        root.addLayout(right, 1)

        self._fit_screen()
        self.select_module(0)

        # 侧栏底部的「设备地址」跟控制器配置保持一致
        self.manager.device_changed.connect(self._refresh_side_addr)
        self._refresh_side_addr()
        # 底栏那一句跟着连接台数走，别一直挂着「功能未接入」
        self.manager.device_changed.connect(self._refresh_status)
        self._refresh_status()
        # 设备管理页里切子页签，顶栏面包屑跟着变
        dp = getattr(self.workspace, "device_page", None)
        if dp is not None:
            dp.sub_changed.connect(self._on_sub_changed)

        # ---------------- 账户
        acc = self.topbar.account
        acc.login_clicked.connect(lambda: self._open_auth("login"))
        acc.register_clicked.connect(lambda: self._open_auth("register"))
        acc.logout_clicked.connect(self._logout)
        acc.password_clicked.connect(self._change_password)
        auth.session.subscribe(self._on_session_change)
        self._on_session_change()

        # ---------------- 权限守卫（按钮级）
        self.guard = PermGuard(self)
        self._install_guards()
        sp = getattr(self.workspace, "system_page", None)
        if sp is not None:
            sp.sub_changed.connect(lambda name: self._on_sub_changed(name, "system"))
            sp.perm_changed.connect(self._on_perm_changed)

        # 数据管理五个子页，面包屑也要跟着走
        dpg = getattr(self.workspace, "data_page", None)
        if dpg is not None:
            dpg.sub_changed.connect(lambda name: self._on_sub_changed(name, "data"))

        # ---------------- 每日自动备份
        # 60 秒扫一次：开工时工具就开着，到点自己备；错过了（比如 3 点才开机）
        # 下一次扫描会补一次，不会等到明天。
        self._backup_timer = QTimer(self)
        self._backup_timer.setInterval(60 * 1000)
        self._backup_timer.timeout.connect(self._auto_backup)
        self._backup_timer.start()
        QTimer.singleShot(4000, self._auto_backup)

    # ---------------------------------------------------------------- 权限
    def _page(self, key):
        for i, m in enumerate(MODULES):
            if m["key"] == key:
                return self.workspace.stack.widget(i)
        return None

    def _install_guards(self):
        """按页面把"需要权限才能点的控件"登记进来。

        只登记动作类按钮；输入框、表格这些留给页面自己的只读逻辑管，
        免得用户只是点一下输入框就弹"权限不足"。
        """
        dp = getattr(self.workspace, "device_page", None)
        if dp is not None and len(getattr(dp, "pages", [])) >= 2:
            dev, mot = dp.pages[0], dp.pages[1]
            for name in ("btn_conn", "btn_disc", "btn_all_conn", "btn_all_disc"):
                self.guard.add(getattr(dev, name, None), "dev.connect", "连接/断开设备")
            self.guard.add(mot, "dev.motion", "使用运动控制", deep=True)

        test = self._page("test")
        if test is not None:
            for name, label in (("btn_new", "新建项目"), ("btn_save", "保存项目"),
                                ("btn_edit", "编辑项目"), ("btn_del", "删除项目"),
                                ("btn_yolo", "更换识别模型")):
                self.guard.add(getattr(test, name, None), "test.edit", label)

        auto = self._page("auto")
        if auto is not None:
            self.guard.add(getattr(auto, "btn_run", None), "auto.run", "启动自动运行")
            self.guard.add(getattr(auto, "btn_save", None), "auto.edit", "修改流程并保存")
            self.guard.add(getattr(auto, "btn_vision", None), "vis.edit", "调用视觉识别")

        vis = self._page("vision")
        if vis is not None:
            for name in ("btn_model", "btn_import", "btn_run", "btn_run_all",
                         "btn_pin", "btn_pin_all", "btn_save", "btn_save_all"):
                self.guard.add(getattr(vis, name, None), "vis.edit", "调整视觉参数/运行识别")
            # 类别管理单独一个功能点：它会改变"哪些标签走哪套算法识别"，
            # 影响面比调参数大，所以不并进 vis.edit（工程师默认有、操作员没有）
            self.guard.add(getattr(vis, "btn_kinds", None), "vis.kinds",
                           "新增/删除引脚类别")

        kb = self._page("kb")
        if kb is not None:
            self.guard.add(getattr(kb, "btn_add", None), "kb.edit", "导入/删除资料")

    def _on_perm_changed(self):
        """角色权限改过之后：页签重建、导航与账户区重刷。"""
        sp = getattr(self.workspace, "system_page", None)
        if sp is not None:
            sp.schedule_refresh()
        self._on_session_change()

    # ---------------------------------------------------------------- 账户
    def _open_auth(self, mode="login"):
        d = AuthDialog(mode, self)
        return d.exec_() == QDialog.Accepted

    def _logout(self):
        if not auth.session.logged_in:
            return
        auth.session.logout("用户主动退出登入")

    def _change_password(self):
        if not auth.session.logged_in:
            return
        from PyQt5.QtWidgets import QLineEdit
        old = QLineEdit()
        old.setEchoMode(QLineEdit.Password)
        old.setFixedHeight(30)
        new = QLineEdit()
        new.setEchoMode(QLineEdit.Password)
        new.setFixedHeight(30)
        new2 = QLineEdit()
        new2.setEchoMode(QLineEdit.Password)
        new2.setFixedHeight(30)
        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        for text, w in (("原密码", old), ("新密码", new), ("确认新密码", new2)):
            v.addWidget(C.field(text, w))

        def validate(_pwd):
            u = auth.users.get(auth.session.name)
            if u is None:
                return "账号不存在，请重新登入"
            if not auth.check_hash(old.text(), u.get("pwd_salt"),
                                   u.get("pwd_hash")):
                return "原密码不对"
            msg = auth.password_error(new.text())
            if msg:
                return msg
            if new.text() != new2.text():
                return "两次输入的新密码不一致"
            auth.users.set_password(auth.session.name, new.text())
            return ""

        d = C.CardDialog(self, "修改密码",
                         "修改的是「%s」自己的密码，改完立即生效（本次会话不受影响）。"
                         % auth.session.name,
                         ok_text="修改", extra=form, validator=validate)
        if d.exec_() != QDialog.Accepted:
            return
        audit.write("修改密码", target=auth.session.name, detail="本人修改")
        C.tell(self, "已修改", "密码已更新，下次登入请用新密码。")

    def _prompt_login(self, module_name):
        ok = C.ask(self, "请先登入",
                   "「%s」需要登入后才能使用。\n\n"
                   "首页可以随意查看；02~08 功能模块都要先登入。"
                   "关掉工具会自动退出账户，下次打开需要重新登入。" % module_name,
                   ok_text="去登入", cancel_text="稍后")
        if not ok:
            return False
        return self._open_auth("login") and auth.session.logged_in

    def _on_session_change(self):
        """登入/登出/权限变更后统一刷：顶栏账户、导航小锁、底栏、当前页。"""
        acc = self.topbar.account
        if auth.session.logged_in:
            acc.set_user(auth.session.name, auth.session.role_name)
            self.statusbar.set_user(auth.session.name, auth.session.role_name)
        else:
            acc.set_logged_out()
            self.statusbar.set_user()
        self.side.set_locked(auth.session.logged_in)

        # 系统管理页是"未登入"时建的，一个页签都没有；登入/登出后要重建
        sp = getattr(self.workspace, "system_page", None)
        if sp is not None and sp.is_stale():
            sp.schedule_refresh()

        # 数据管理页同理：页签按 data.view / data.export 生成
        dpg = getattr(self.workspace, "data_page", None)
        if dpg is not None and dpg.is_stale():
            dpg.schedule_refresh()

        if not auth.session.logged_in:
            mod = MODULES[self._current]
            if auth.MODULE_POINTS.get(mod["key"]) is not None:
                # 停在需要登入的模块上（比如用户点了"退出登入"），退回首页
                self._current = 0
                self.side.set_active(0)
                self.workspace.stack.setCurrentIndex(0)
                self.topbar.set_module(MODULES[0], None)

    # ---------------------------------------------------------------- 自动备份
    def _auto_backup(self):
        try:
            res = bk.run_auto()
        except Exception:
            res = None
        if not res:
            return
        sp = getattr(self.workspace, "system_page", None)
        if sp is not None:
            for page in getattr(sp, "_pages", []):
                if hasattr(page, "reload"):
                    try:
                        page.reload()
                    except Exception:
                        pass

    def _refresh_status(self, *_args):
        self.statusbar.set_devices(self.manager.connected_count())

    def _refresh_side_addr(self, *_args):
        ip = str(self.manager.cfg.get("motion", {}).get("ip") or "").strip()
        self.side.status.set_device_addr(ip or None)

    def closeEvent(self, e):
        """关窗口时把设备连接断干净，别让控制器/相机句柄留在进程外。

        顺手把账户登出（写一条"退出程序"的审计）—— 用户要求"关闭工具会自动
        退出账户，每次打开都要登入"。会话本来只在内存，这里只是补上留痕。
        """
        try:
            auth.session.logout("退出程序")
        except Exception:
            pass
        try:
            self.manager.shutdown()
        except Exception:
            pass
        super().closeEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        if not self._titlebar_done:
            self._titlebar_done = True
            _style_titlebar(int(self.winId()))

    # ---------- 布局 ----------
    def _fit_screen(self):
        """按可用屏幕定初始尺寸，避免高 DPI 下窗口比屏幕大。

        这台机器物理 2880x1800、缩放 200%，Qt 的逻辑坐标是 1440x900，
        窗口按逻辑像素给尺寸即可，无需自己乘缩放系数。
        """
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry()
        w = min(1380, int(avail.width() * 0.96))
        h = min(840, int(avail.height() * 0.94))
        self.resize(w, h)
        self.move(avail.x() + (avail.width() - w) // 2,
                  avail.y() + (avail.height() - h) // 2)

    # ---------- 交互 ----------
    def select_module(self, index):
        """切换模块。**这里是登入与权限的唯一闸口**。

        未登入点 02~08：提示"请先登入"，给出直接去登入的按钮，但不进去；
        已登入但角色没这个功能点：提示权限不足并写审计，同样不进去。
        """
        mod = MODULES[index]
        point = auth.MODULE_POINTS.get(mod["key"])
        if point is not None:
            if not auth.session.logged_in:
                audit.write("未登入访问被拒绝", target=mod["name"], result="FAIL",
                            detail="模块「%s」需要登入" % mod["name"])
                if self._prompt_login(mod["name"]):
                    return self.select_module(index)   # 登入完直接进刚才点的那个
                return
            if not auth.session.can(point):
                audit.write("越权访问被拒绝", target=mod["name"], result="FAIL",
                            detail="角色 %s 缺少功能点 %s"
                                   % (auth.session.role_name, point))
                C.tell(self, "权限不足",
                       "当前角色（%s）没有「%s」的访问权限。\n\n如需使用，请联系"
                       "管理员在「系统管理 → 角色权限」里授权。"
                       % (auth.session.role_name, mod["name"]), kind="warn")
                return

        self._current = index
        self.side.set_active(index)
        self.workspace.stack.setCurrentIndex(index)
        page = self.workspace.stack.currentWidget()
        # 模块内部有子页签的（设备管理 / 系统管理），面包屑跟着子页走
        self.topbar.set_module(mod, getattr(page, "sub_name", None))

    def _on_sub_changed(self, name, key="device"):
        """子页签切换，顶栏面包屑同步。

        只在「该模块正显示着」时才改面包屑。登入/登出会让系统管理页整页重建，
        重建过程中 _select() 会把 sub_changed 重新发一遍 —— 不判当前模块的话，
        人明明停在首页，面包屑却被写成「系统管理 / 用户管理」。
        """
        idx = -1
        for i, m in enumerate(MODULES):
            if m["key"] == key:
                idx = i
                break
        if idx < 0 or idx != self._current:
            return
        self.topbar.set_module(MODULES[idx], name)

    def paintEvent(self, _e):
        # 兜底填底，防止缩放/重绘时闪白
        p = QPainter(self)
        p.fillRect(self.rect(), _q(theme.BG))
        p.end()
