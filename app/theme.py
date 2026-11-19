# -*- coding: utf-8 -*-
"""配色、字体、尺寸常量。

数值全部取自原型 D:\\PCB\\电路板测试系统原型\\css\\app.css 的 :root 变量，
改这里等于全局换皮肤，各控件都从本模块取色，不要写死颜色。
"""

import os

# 勾选态的对勾图片（QSS 里用，路径要是正斜杠）
_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
CHECK_ICON = os.path.join(_ASSETS, "check.png").replace("\\", "/")

# ---------- 色板 ----------
# 2026-11-10 换成浅色版：浅蓝 + 白为主，深蓝作强调。原深色工业风那套值留在
# 注释里做对照，以后要改回去不用翻历史。
#
# 三条硬规则（换色时别破）：
# 1) AMBER / AMBER2 / AMBER_DIM 名字没改，但现在是**深蓝三档**，不是黄。
#    三档要拉开明度，否则按钮、描边、编号会糊成一片。全局搜索 AMBER 的地方
#    都当"强调色"在用，改值即可，不用逐个改语义。
# 2) 背景一律往浅里走、文字一律往深里走。以前是"深底浅字"，现在反过来，
#    任何新增的深色 hex 都要先想清楚是不是本来想在白底上画线。
# 3) 状态色（OK / WARN / BAD / CYAN）在白底上要压深一档才够对比度，
#    原来那套是给黑底调亮的，直接拿过来会发飘。
BG          = "#eef4fa"        # 最底层背景          (原 #05080b)
BG_WORK     = "#eef4fa"        # 工作区底色          (原 #060a0e)
SIDEBAR_TOP = "#dce8f5"        # 侧栏渐变上          (原 #0b1217)
SIDEBAR_BOT = "#ccdeee"        # 侧栏渐变下          (原 #060a0d)
TOPBAR_TOP  = "#ffffff"        # 顶栏渐变上          (原 #0c1319)
TOPBAR_BOT  = "#f6fafd"        # 顶栏渐变下          (原 #090e13)
PANEL_TOP   = "#ffffff"        # 面板渐变上          (原 #0e161d)
PANEL_BOT   = "#fafcff"        # 面板渐变下          (原 #0a1116)
RAISE       = "#f1f7fc"        # 抬一层的底（表头/输入框）  (原 #14212b)
LINE        = "#dde8f2"        # 细分割线            (原 #1a2733)
LINE2       = "#c2d6e8"        # 边框                (原 #283b4a)

# 页头那条横向蓝渐变：左上偏蓝、中间近白、右边回到淡蓝。
# 工作区不再画蓝白格子，全靠这条给页头一点层次，不然整片死白。
HEAD_GA     = "#cfe1f4"
HEAD_GB     = "#eff6fc"
HEAD_GC     = "#dfeaf8"

TXT         = "#1f3750"        # 正文                (原 #d3dee8)
TXT2        = "#5b7186"        # 次级                (原 #94a6b5)
TXT3        = "#8ba1b5"        # 弱化                (原 #5f7282)
TXT_NAV_IDX = "#96acc0"        # 导航编号            (原 #43525f)
TITLE       = "#0b2e5c"        # 页头大标题（深藏蓝），比正文再深一档

AMBER       = "#1b57a0"        # 主强调色（深蓝）    (原 #ffb23f 琥珀)
AMBER2      = "#2e74c4"        # 亮一档：hover / 高亮 (原 #ffcb76)
AMBER_DIM   = "#6d94be"        # 暗一档：编号、描边   (原 #e8c88d)
CYAN        = "#0f8b84"        # 次要强调（已接入）  (原 #3ad6c6)
OK          = "#1e8f56"
WARN        = "#b0740f"
BAD         = "#c0392b"

# ---------- 应用信息 ----------
APP_NAME = "电路板测试系统"
APP_SUB  = "PCB AUTOMATED TEST SYSTEM"
APP_VER  = "v0.3.0"

# 账户区：顶栏右上角那块。不在 theme 里写死用户名/角色了 ——
# 2026-11-11 接了账户体系，实际显示什么由 app/auth.py 的 session 决定。
ACCOUNT_NAME = "未登入"          # 仅作未登入时的兜底文案
ACCOUNT_ROLE = "本地账户"

# ---------- 尺寸（px，单位与原型 CSS 一致） ----------
SIDEBAR_W  = 246
TOPBAR_H   = 58
STATUSBAR_H = 30
NAV_ITEM_H = 38
RADIUS     = 3

# ---------- 字体 ----------
# 原型用的是 HarmonyOS Sans SC / DIN Next LT Pro / Cascadia Mono，
# 前两个本机没有，用最接近的替代：微软雅黑 UI（正文）、Bahnschrift（DIN 风格显示体）。
# _pick() 会在运行时确认字体存在，缺失时逐级回退，避免换台机器字全变宋体。
_FBODY = ["Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans SC", "Segoe UI"]
_FDISP = ["Bahnschrift", "DIN Alternate", "Segoe UI Semibold", "Segoe UI"]
_FMONO = ["Cascadia Mono", "Consolas", "Courier New"]

F_BODY = _FBODY[0]
F_DISP = _FDISP[0]
F_MONO = _FMONO[0]

# 全局正文像素字号（QSS 里 QWidget{font-size}）。注意：QSS 的字号**不会**写回
# widget.font()，所以用 QFontMetrics(label.font()) 量中文会比实际渲染窄一截、
# 按它设固定宽度就会把字裁掉（2026-11-05 踩过）。要量宽度就用这个常量建字体。
FONT_PX = 12
FONT_PX_SMALL = 10          # 备注、状态条那种小字，也是直接写在 QSS 里的


def _pick(candidates, available):
    for name in candidates:
        if name in available:
            return name
    return candidates[-1]


def init_fonts():
    """启动时调一次，按本机实际字体表确定三个字体族。"""
    global F_BODY, F_DISP, F_MONO
    from PyQt5.QtGui import QFontDatabase
    fams = set(QFontDatabase().families())
    F_BODY = _pick(_FBODY, fams)
    F_DISP = _pick(_FDISP, fams)
    F_MONO = _pick(_FMONO, fams)


# ---------- QSS ----------
def qss():
    """全局样式表。只放通用控件，自定义绘制的控件不吃 QSS。"""
    return """
QWidget {{
    color: {TXT};
    font-family: "{F_BODY}";
    font-size: {FONT_PX}px;
}}
QToolTip {{
    background: #ffffff;
    color: #000000;
    border: 1px solid {LINE2};
    padding: 4px 7px;
}}
QScrollBar:vertical {{
    background: #e6eef7; width: 8px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #b9cbdc; border-radius: 4px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: #93aec5; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QScrollBar:horizontal {{ background: #e6eef7; height: 8px; }}
QScrollBar::handle:horizontal {{
    background: #b9cbdc; border-radius: 4px; min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: #93aec5; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}
""".format(TXT=TXT, F_BODY=F_BODY, PANEL_TOP=PANEL_TOP, LINE2=LINE2,
           FONT_PX=FONT_PX)

# ---------- 通用控件样式 ----------
def menu_qss():
    """QMenu（下拉菜单）的样式：默认是系统浅色，在深色界面里很刺眼。

    现在是浅色界面，反而要防它套用系统深色主题 —— 这里把底色、文字、选中态
    全写死，跟随主题走。
    """
    return """
QMenu {
    background: #ffffff; border: 1px solid #c2d6e8;
    color: #1f3750; padding: 4px;
}
QMenu::item { padding: 6px 22px 6px 14px; border-radius: 3px; }
QMenu::item:selected { background: #e6f0fa; color: #17497f; }
QMenu::separator { height: 1px; background: #dde8f2; margin: 4px 8px; }
"""


def controls_qss():
    """输入框 / 按钮 / 文本域的通用样式。

    设备管理页和运动控制页都是同一套控件，样式放在这里共用一份 ——
    两处各写一遍迟早会走样（以前就出现过按钮高度差 2px 的问题）。
    """
    return """
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {
    background: #ffffff;
    border: 1px solid #cfe0ee;
    border-radius: 3px;
    color: #1f3750;
    padding: 3px 6px;
    selection-background-color: #bcd8f2;
    selection-color: #12324f;
}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {
    border-color: #2e74c4;
    background: #fdfeff;
}
QLineEdit:hover, QDoubleSpinBox:hover, QSpinBox:hover, QComboBox:hover {
    border-color: #a8c4de;
}
QLineEdit:disabled, QDoubleSpinBox:disabled, QSpinBox:disabled, QComboBox:disabled {
    color: #7d95a9;
    background: #f4f8fc;
    border-color: #dde8f2;
}
QComboBox::drop-down { border: none; width: 16px; }
/* 下拉弹出列表（QComboBox 的 view 是独立顶层窗口，样式必须写到 view 上）。
   除了这里，任何祖先控件上的**无选择器** background（如 "background: transparent"）
   都会传染到它的 viewport，把白底刷成透明 → 弹出来是黑底灰字。 */
QComboBox QAbstractItemView {
    background-color: #ffffff;
    border: 1px solid #c2d6e8;
    color: #1f3750;
    outline: 0;                      /* 去掉选中行外面那圈橙色虚线焦点框 */
    selection-background-color: #e6f0fa;
    selection-color: #17497f;
    padding: 2px;
}
QComboBox QAbstractItemView::item {
    min-height: 22px;
    padding: 3px 8px;
}
QComboBox QAbstractItemView::item:hover {
    background: #f2f8ff;
    color: #17497f;
}
QComboBox QAbstractItemView::item:selected {
    background: #e6f0fa;
    color: #17497f;
}
QPushButton#act {
    background: #f4f9ff; border: 1px solid #c9dcec;
    border-radius: 3px; color: #24405c; padding: 4px 12px;
}QPushButton#act:hover { background: #e7f1fb; border-color: #8fb6da; }
QPushButton#act:pressed { background: #dbe9f7; }
QPushButton#act:disabled { color: #7d95a9; background: #f6f9fc; border-color: #c9dbeb; }
QPushButton#primary {
    background: rgba(27,87,160,0.09); border: 1px solid #8fb1d2;
    border-radius: 3px; color: #17497f; padding: 4px 14px; font-weight: bold;
}
QPushButton#primary:hover { background: rgba(27,87,160,0.16); border-color: #2e74c4; }
QPushButton#primary:pressed { background: rgba(27,87,160,0.24); }
QPushButton#primary:disabled { color: #86a0b6; background: #f5f9fd; border-color: #c2d8e9; }
QPushButton#cardBtn {
    background: #f7fbff; border: 1px solid #d2e2f0;
    border-radius: 3px; color: #4b6a86; font-size: 11px;
}
QPushButton#cardBtn:hover { background: #eaf3fc; border-color: #8fb6da; color: #1b4a78; }
QPushButton#cardBtn:disabled { color: #7d95a9; background: #f8fbfe; border-color: #e4eef7; }
QTextEdit {
    background: #f8fbfe; border: 1px solid #dbe6f1;
    border-radius: 3px; color: #4a6478;
}
/* 表格：测试程序编写那 7 列 */
QTableWidget {
    background: #ffffff;
    alternate-background-color: #f7fbfe;
    gridline-color: #e4eef7;
    border: 1px solid #dbe6f1;
    border-radius: 3px;
    selection-background-color: rgba(27,87,160,0.10);
    selection-color: #12406f;
    outline: none;
}
QTableWidget::item { padding: 1px 4px; }
QTableWidget::item:selected {
    background: rgba(27,87,160,0.10); color: #12406f;
}
QHeaderView::section {
    background: #f1f7fc;
    color: #6b8299;
    border: none;
    border-right: 1px solid #e4eef7;
    border-bottom: 1px solid #dbe6f1;
    padding: 5px 6px;
    font-size: 11px;
}
QHeaderView::section:last { border-right: none; }
QTableCornerButton::section { background: #f1f7fc; border: none; }
QCheckBox { color: #34506b; spacing: 6px; }
QCheckBox::indicator {
    width: 13px; height: 13px; border-radius: 2px;
    border: 1px solid #b7cde1; background: #ffffff;
}
/* 选中：深蓝底 + 白色对勾。只有底色没有勾时，一排方块分不清是"选中"还是
   "被填充"，权限矩阵那种一张 23 行的表最容易看错。 */
QCheckBox::indicator:checked {
    background: #1b57a0; border-color: #1b57a0;
    image: url("@CHECK@");
}
/* 点动的 +/− 小方块 */
QPushButton#jogdot {
    background: #f4f9ff; border: 1px solid #c9dcec;
    border-radius: 3px; color: #24405c; font-size: 15px; font-weight: bold;
    padding: 0;
}
QPushButton#jogdot:hover { background: #e7f1fb; border-color: #8fb6da; color: #0f2f4d; }
QPushButton#jogdot:pressed { background: #d8e8f7; }
QPushButton#jogdot:disabled { color: #7d95a9; background: #f6f9fc; border-color: #e0eaf3; }
/* 顶栏账户区的【登入】【注册】小按钮：要比正文轻，但不能像一行说明文字 */
QPushButton#topLink {
    background: rgba(27,87,160,0.06); border: 1px solid #b7cfe4;
    border-radius: 3px; color: #17497f; padding: 2px 10px;
}
QPushButton#topLink:hover { background: rgba(27,87,160,0.14); border-color: #8fb6da; }
QPushButton#topLink:pressed { background: rgba(27,87,160,0.22); }
/* 步长快捷键 */
QPushButton#chip {
    background: #f4f8fd; border: 1px solid #d5e4f1;
    border-radius: 3px; color: #4b6a86; font-size: 11px; padding: 0;
}
QPushButton#chip:hover { background: #e9f2fb; border-color: #8fb6da; color: #1b4a78; }
QPushButton#chip:disabled { color: #7d95a9; border-color: #e4eef7; }
/* 急停：不管连没连上都得能按，所以样式和禁用态都单独给。
   浅底上红要压深一档，否则粉粉的没有"能拍下去"的分量。 */
QPushButton#estop {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #c93a2f, stop:1 #9b211a);
    border: 1px solid #a92a22; border-radius: 4px;
    color: #fff4f2; font-size: 13px; font-weight: bold; letter-spacing: 1px;
}
QPushButton#estop:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #dd4a3e, stop:1 #ad251d);
    border-color: #8e1f16;
}
QPushButton#estop:pressed { background: #8e1f16; border-color: #7a1a12; }
/* 分组框：视觉定位页的「YOLO 模型导入」、测试管理页的「项目」 */
QGroupBox {
    border: 1px solid #dbe6f1;
    border-radius: 4px;
    margin-top: 14px;          /* 要盖得住 10px 标题的整行高，否则标题被切头 */
    padding-top: 0px;
    background: rgba(27,87,160,0.015);
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 5px;
    color: #7189a0;
}
""".replace("@CHECK@", CHECK_ICON)



# ---------- tooltip 兜底 ----------
# 为什么需要这一层：QSS 里 "QToolTip { background:#ffffff; }" 会被**祖先控件上的
# 无选择器样式表**盖掉。像 self.lb.setStyleSheet("color:#5b7186;background:transparent;")
# 这种写法（没有 QLabel{ } 包起来）按 Qt 的规则会连同子控件一起生效 —— tooltip
# 也被算在内，于是透明底 → 屏幕上就是黑底、深蓝字，看不清。
# 这类写法全工程有几十处，逐个改属性风险大，所以在 tooltip 显示时给它自己挂一条
# 样式表（自己身上的规则优先级最高，祖先盖不掉）。
TIP_QSS = ("background:#ffffff;color:#000000;"
           "border:1px solid %s;padding:4px 7px;" % LINE2)

# 守卫实例必须留一个引用：只 installEventFilter 不存对象的话，Python 这边一回收，
# 过滤器就跟着没了，tip 又变回黑底（排查过一次，现象是"改了没用"）。
_TIP_GUARD = None


def _make_tip_filter(app):
    from PyQt5.QtCore import QEvent, QObject, QTimer

    class _TipFilter(QObject):
        def eventFilter(self, obj, ev):
            try:
                et = ev.type()
                if et == QEvent.ToolTip:
                    # tooltip 事件先到：下一轮事件循环再扫一遍（tip 那时已建好）
                    QTimer.singleShot(0, lambda: sweep_tips(app))
                    return False
                # Show / Polish / StyleChange 都要管：Qt 每次复用同一个 QTipLabel，
                # 显示前会把它的 styleSheet 重置成 "/* */"（它自己那套占位写法），
                # 只在 Show 上补一刀会被这一步吃掉 —— 所以这几个点都补，幂等。
                if et in (QEvent.Show, QEvent.Polish, QEvent.StyleChange):
                    if obj.metaObject().className() == "QTipLabel":
                        if obj.styleSheet() != TIP_QSS:
                            obj.setStyleSheet(TIP_QSS)
            except Exception:
                pass
            return False

    return _TipFilter(app)          # parent=app，进程活多久它活多久


def _install(app):
    """建过滤器并**真的装上**（只 new 不 install 是不会有事件的）。"""
    f = _make_tip_filter(app)
    app.installEventFilter(f)
    return f


def sweep_tips(app=None):
    """把当前可见的 tooltip 刷成 TIP_QSS（幂等，可以在任何时机调）。"""
    try:
        from PyQt5.QtWidgets import QApplication
        app = app or QApplication.instance()
        for w in app.allWidgets():
            if w.isVisible() and w.metaObject().className() == "QTipLabel":
                if w.styleSheet() != TIP_QSS:
                    w.setStyleSheet(TIP_QSS)
    except Exception:
        pass


def install_tip_guard(app):
    """启动时调一次。失败也不能影响启动 —— tooltip 只是不好看，不是不能用。"""
    global _TIP_GUARD
    if _TIP_GUARD is not None:
        return _TIP_GUARD
    try:
        _TIP_GUARD = _install(app)
        return _TIP_GUARD
    except Exception:
        return None
