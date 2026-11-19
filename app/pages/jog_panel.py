# -*- coding: utf-8 -*-
"""手动控制页：点动 / 定位 / 回零 / 急停。

为什么单独一个文件：这块和「参数配置」是两套逻辑，塞进 device_page.py
那个文件就要奔两千行去了。这里只管运动，参数读写还是走原来的那一套。

界面（从上到下）：

    工具条   点动步长 + 常用步长快捷键 + 一句说明
    轴表     每轴一行：轴 / 当前位置 / 当前速度 / 目标 / − + 移动 回零 / 状态
    状态行   回零情况、限位、报警
    动作条   全部回零 · 回零设置 · 全部停止 ······ 急停（永远是红的，永远能按）

几条刻意的设计：

* 速度/加减速不在这里另开一套输入框，直接用「参数配置」里每轴的值。
  两处各存一份迟早会不一样，到时候按哪个走谁也说不清。
* 位置显示是**轮询**来的（200ms），不是运动指令的回执 —— 卡上真正在走
  多少，看的就是这个数。运动中数字变青色，撞击/报警变红色。
* 急停是**两态**：按一次 = 停 + 上锁（按钮变成「解除急停」），再按一次才解除。
  只发一次停止挡不住后面的指令，而且解除之后**不会续跑**：轴停在哪儿就是哪儿，
  要动重新下指令。被硬停过的轴会标成「未回零」，逼你重新回零再绝对定位 ——
  步进被硬停可能丢步，位置数字看着对、实际已经偏了。
* 探针没回零不给绝对定位（原工具也是这么拦的）：没有原点，绝对坐标就是
  个数字，照着走可能直接撞上去。点动不受这个限制，撤出来永远允许。
"""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QScrollArea, QVBoxLayout,
                             QWidget)

import traceback

from .. import theme
from ..devices import store
from ..devices.manager import DEVICE_META
from ..devices.runner import MotionRunner
from ..devices.store import AXES, PROBE_AXES
from ..widgets import _font
from .home_cfg import HomeCfgDialog, MotionHomeDialog

# 常用步长快捷键（mm）。0.01 用来找边，10 用来快速挪位
STEP_CHIPS = ("0.01", "0.05", "0.1", "1", "10")

# 急停上锁后的按钮样式（实心红是「有事按我」，描边红是「已经按了」）。
# 带类型选择器：不带会把 QComboBox 的弹出列表也染上（踩过）。
ESTOP_LOCK_QSS = (
    "QPushButton{background:rgba(192,57,43,0.10);border:1px solid #c0392b;"
    "border-radius:4px;color:#c0392b;font-size:12px;font-weight:bold;"
    "letter-spacing:0.5px;padding:0 4px;}"
    "QPushButton:hover{background:rgba(192,57,43,0.18);border-color:#a92a22;}"
    "QPushButton:pressed{background:rgba(192,57,43,0.28);}")


class JogPanel(QWidget):
    """一台设备的手动控制页。"""

    logged = pyqtSignal(str, str)       # (文本, 级别)
    step_changed = pyqtSignal(float)    # 步长改了（由页面负责落盘）

    def __init__(self, key, mgr, parent=None):
        super(JogPanel, self).__init__(parent)
        self.key = key
        self.mgr = mgr
        self.driver = mgr.driver(key)
        self.axes = tuple(PROBE_AXES if key == "probe" else AXES)
        self.rows = {}
        self.state = {}
        self._connected = False
        self._colors = {}
        self._estop_on = bool(self.mgr.is_estopped())   # 整机的闸，不是本面板的

        self.runner = MotionRunner(key, self.driver, period=0.2, parent=self)
        self.runner.tick.connect(self._on_tick)
        self.runner.finished.connect(self._on_done)
        self.runner.stage.connect(lambda t: self.logged.emit(t, "info"))

        self.setStyleSheet(theme.controls_qss())
        self._build()
        self._refresh_ranges()          # 目标框一建好就显示这条轴的允许范围
        self._refresh_home_tips()
        self._set_estop_ui(self._estop_on)      # 另一块面板按过急停，这里也要锁着
        self.mgr.estop_changed.connect(self._set_estop_ui)
        self.runner.start()

    # ---------------------------------------------------------------- 配置
    def _dev_cfg(self):
        return self.mgr.cfg.get(self.key) or {}

    def _axis_cfg(self, name):
        return (self._dev_cfg().get("axes") or {}).get(name) or {}

    def _spm(self):
        try:
            v = float(self._dev_cfg().get("spm", 12800.0))
            return v if v > 0 else 12800.0
        except (TypeError, ValueError):
            return 12800.0

    # ---------------------------------------------------------------- 建界面
    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        v.addLayout(self._build_bar())

        # 状态行放在表上方当横幅：运动中/限位/未回零一眼就看到，不用往下找
        self.lb_status = QLabel("")
        self.lb_status.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        self.lb_status.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        self.lb_status.setWordWrap(True)
        v.addWidget(self.lb_status)

        table = QWidget()
        table.setStyleSheet("background:transparent;")
        self.grid = QGridLayout(table)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(0)
        self._build_table()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background:transparent;"
                             "QScrollArea>QWidget>QWidget{background:transparent;}")
        # 视口默认会自己铺一层底色（比面板亮一档），关掉才是真正的透明
        scroll.viewport().setAutoFillBackground(False)
        scroll.setWidget(table)
        v.addWidget(scroll, 1)

        v.addLayout(self._build_actions())
        self._set_actions_enabled(False)

    def _build_bar(self):
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)

        lb = QLabel("点动步长")
        lb.setFont(_font(theme.F_BODY, 11, spacing=0.3))
        lb.setStyleSheet("color:%s;background:transparent;" % theme.TXT2)
        bar.addWidget(lb)

        self.inp_step = QLineEdit()
        self.inp_step.setFixedSize(84, 26)
        self.inp_step.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.inp_step.setFont(_font(theme.F_MONO, 12, spacing=0.3))
        self.inp_step.setText("%g" % float(self._dev_cfg().get("jog_step", 1.0) or 1.0))
        self.inp_step.editingFinished.connect(self._on_step_edited)
        bar.addWidget(self.inp_step)

        lb_unit = QLabel("mm")
        lb_unit.setFont(_font(theme.F_MONO, 10, spacing=0.2))
        lb_unit.setStyleSheet("color:#879caf;background:transparent;")
        bar.addWidget(lb_unit)

        for txt in STEP_CHIPS:
            b = QPushButton(txt)
            b.setObjectName("chip")
            b.setFixedSize(42, 24)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, t=txt: self._set_step(float(t)))
            bar.addWidget(b)

        bar.addStretch(1)
        hint = QLabel("速度与加减速取「参数配置」里各轴的值")
        hint.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        hint.setStyleSheet("color:#8ba1b5;background:transparent;")
        bar.addWidget(hint)
        return bar

    def _build_table(self):
        # (标题, 起始列, 跨列, 对齐)
        heads = [("轴", 0, 1, Qt.AlignLeft),
                 ("当前位置 (mm)", 1, 1, Qt.AlignLeft),
                 ("当前速度 (mm/s)", 2, 1, Qt.AlignLeft),
                 ("目标位置 (mm)", 3, 1, Qt.AlignLeft),
                 ("操作", 4, 4, Qt.AlignHCenter),
                 ("状态", 8, 1, Qt.AlignRight)]
        for text, col, span, align in heads:
            lb = QLabel(text)
            lb.setFont(_font(theme.F_BODY, 10, spacing=0.4))
            lb.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
            lb.setAlignment(align | Qt.AlignVCenter)
            self.grid.addWidget(lb, 0, col, 1, span)
        self.grid.setRowMinimumHeight(0, 26)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background:%s;" % theme.LINE)
        self.grid.addWidget(line, 1, 0, 1, 9)
        self.grid.setRowMinimumHeight(1, 6)

        for i, name in enumerate(self.axes):
            self._build_row(2 + i, name)

        # 轴行和末尾的弹性行各分一半多余高度：三轴时行高会被撑到 ~50px，
        # 正好和六轴排满时差不多，两个页面看起来是一套东西；剩下的空白吃掉，
        # 轴少的时候也不至于一大片死空。
        for i in range(len(self.axes)):
            self.grid.setRowStretch(2 + i, 1)
        self.grid.setRowStretch(2 + len(self.axes), 1)

        # 数字列给不同权重，整张表铺满宽度：三轴也不会挤在左边一小块
        self.grid.setColumnStretch(1, 3)
        self.grid.setColumnStretch(2, 2)
        self.grid.setColumnStretch(3, 2)
        self.grid.setColumnStretch(8, 1)

    def _build_row(self, r, name):
        self.grid.setRowMinimumHeight(r, 42)

        # 隔行底纹：只是让六行数字好对位，不喧宾夺主
        if (r % 2) == 1:
            bg = QWidget()
            bg.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            bg.setAttribute(Qt.WA_StyledBackground, True)
            bg.setStyleSheet("background:rgba(27,87,160,0.05);border-radius:3px;")
            self.grid.addWidget(bg, r, 0, 1, 9)

        tag = QLabel(name.upper())
        tag.setFixedSize(26, 22)
        tag.setAlignment(Qt.AlignCenter)
        tag.setFont(_font(theme.F_DISP, 12, bold=True, spacing=0.6))
        tag.setStyleSheet(
            "color:%s;background:rgba(27,87,160,0.10);"
            "border:1px solid #fdf0c9;border-radius:3px;" % theme.AMBER_DIM)
        self.grid.addWidget(tag, r, 0, Qt.AlignLeft | Qt.AlignVCenter)

        pos = QLabel("—")
        pos.setMinimumWidth(120)
        pos.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        pos.setFont(_font(theme.F_MONO, 16, spacing=0.4))
        pos.setStyleSheet("color:#6b8299;background:transparent;")
        self.grid.addWidget(pos, r, 1)

        spd = QLabel("—")
        spd.setMinimumWidth(84)
        spd.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        spd.setFont(_font(theme.F_MONO, 11, spacing=0.2))
        spd.setStyleSheet("color:#8ba1b5;background:transparent;")
        self.grid.addWidget(spd, r, 2)

        target = QLineEdit()
        target.setFixedSize(96, 26)
        target.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        target.setFont(_font(theme.F_MONO, 12, spacing=0.3))
        target.setPlaceholderText("绝对坐标")
        target.setToolTip("填绝对坐标，点【移动】走过去（不会有人替你猜方向）")
        target.returnPressed.connect(lambda n=name: self._on_move(n))
        # 改一下输入就把"超限"红框撤掉，不然红着框人会以为还没改对
        target.textChanged.connect(lambda _t, n=name: self._mark_target(n, False))
        self.grid.addWidget(target, r, 3, Qt.AlignRight | Qt.AlignVCenter)

        btn_m = QPushButton("−")
        btn_m.setObjectName("jogdot")
        btn_m.setFixedSize(30, 26)
        btn_m.setToolTip("往负方向走一个步长")
        btn_m.setCursor(Qt.PointingHandCursor)
        btn_m.clicked.connect(lambda _c=False, n=name: self._on_jog(n, -1))
        self.grid.addWidget(btn_m, r, 4, Qt.AlignHCenter | Qt.AlignVCenter)

        btn_p = QPushButton("+")
        btn_p.setObjectName("jogdot")
        btn_p.setFixedSize(30, 26)
        btn_p.setToolTip("往正方向走一个步长")
        btn_p.setCursor(Qt.PointingHandCursor)
        btn_p.clicked.connect(lambda _c=False, n=name: self._on_jog(n, +1))
        self.grid.addWidget(btn_p, r, 5, Qt.AlignHCenter | Qt.AlignVCenter)

        btn_move = QPushButton("移动")
        btn_move.setObjectName("act")
        btn_move.setFixedSize(52, 26)
        btn_move.setToolTip("按目标位置做绝对定位")
        btn_move.setCursor(Qt.PointingHandCursor)
        btn_move.clicked.connect(lambda _c=False, n=name: self._on_move(n))
        self.grid.addWidget(btn_move, r, 6, Qt.AlignHCenter | Qt.AlignVCenter)

        btn_home = QPushButton("回零")
        btn_home.setObjectName("act")
        btn_home.setFixedSize(52, 26)
        btn_home.setToolTip("朝负限位方向找原点，完成后位置清零")
        btn_home.setCursor(Qt.PointingHandCursor)
        btn_home.clicked.connect(lambda _c=False, n=name: self._on_home(n))
        self.grid.addWidget(btn_home, r, 7, Qt.AlignHCenter | Qt.AlignVCenter)

        status = QLabel("未连接")
        status.setMinimumWidth(88)
        status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        status.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        status.setStyleSheet("color:#8ba1b5;background:transparent;")
        self.grid.addWidget(status, r, 8)

        self.rows[name] = {"tag": tag, "pos": pos, "speed": spd, "target": target,
                           "btn_m": btn_m, "btn_p": btn_p, "btn_move": btn_move,
                           "btn_home": btn_home, "status": status}

    def _build_actions(self):
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 2, 0, 0)
        bar.setSpacing(8)

        self.btn_home_all = QPushButton("全部回零")
        self.btn_home_all.setObjectName("act")
        self.btn_home_all.setFixedSize(88, 30)
        self.btn_home_all.clicked.connect(self._on_home_all)

        self.btn_stop_all = QPushButton("全部停止")
        self.btn_stop_all.setObjectName("act")
        self.btn_stop_all.setFixedSize(88, 30)
        self.btn_stop_all.setToolTip("平滑停止所有轴（不影响急停）")
        self.btn_stop_all.clicked.connect(self._on_stop_all)

        # 回零设置两块都有，但口径不同，各自一套对话框：
        #   探针座：六轴方向不一样（原工具 X 负向、其余正向），另外还有回零方式
        #   三轴：SDK 那四项（方向 / 速度 / 加减速 / 脱落距离），逐轴各一份
        self.btn_home_cfg = QPushButton("回零设置")
        self.btn_home_cfg.setObjectName("act")
        self.btn_home_cfg.setFixedSize(88, 30)
        self.btn_home_cfg.setCursor(Qt.PointingHandCursor)
        if self.key == "probe":
            self.btn_home_cfg.setToolTip("每根轴朝哪边回零、快移/定位速度、超时")
        else:
            self.btn_home_cfg.setToolTip(
                "每根轴朝哪侧限位回零、回零速度、加减速、脱落距离、超时")
        self.btn_home_cfg.clicked.connect(self._on_home_cfg)

        self.btn_estop = QPushButton("■  急 停")
        self.btn_estop.setObjectName("estop")
        self.btn_estop.setFixedSize(112, 34)
        self.btn_estop.setToolTip("立即停止所有轴并上锁；再按一次解除。\n"
                                  "解除后轴停在原地，不会自己续走")
        self.btn_estop.setCursor(Qt.PointingHandCursor)
        self.btn_estop.clicked.connect(self._on_estop)
        # 急停永远可点：设备掉线、状态未知的时候更需要它

        bar.addWidget(self.btn_home_all)
        bar.addWidget(self.btn_home_cfg)
        bar.addWidget(self.btn_stop_all)
        bar.addStretch(1)
        bar.addWidget(self.btn_estop)
        return bar

    # ---------------------------------------------------------------- 外部接口
    def set_connected(self, on):
        self._connected = bool(on)
        self._set_actions_enabled(self._connected and not self._estop_on)
        if not on:
            self.state = {}
            for name in self.axes:
                self._paint(name, None)
            self.lb_status.setText("设备未连接：切到【设备管理】页签连上之后这里才能动。")
            self.lb_status.setStyleSheet(
                "color:%s;background:transparent;" % theme.TXT3)
        elif not self.state:
            # 刚连上、第一帧状态还没回来：别让上面那句「未连接」挂着
            self.lb_status.setText("已连接，正在读取轴状态…")
            self.lb_status.setStyleSheet(
                "color:%s;background:transparent;" % theme.TXT3)

    def set_active(self, on):
        """页面/标签页在不在前台 —— 不在就别轮询，别占着卡。"""
        self.runner.set_active(on)

    def shutdown(self):
        self.runner.shutdown()

    # ---------------------------------------------------------------- 状态刷新
    def _on_tick(self, state):
        axes = (state or {}).get("axes") or {}
        self.state = state or {}
        for name in self.axes:
            self._paint(name, axes.get(name))
        self._refresh_summary(axes)
        self._refresh_ranges()      # 设备管理里改了限位，这里两百毫秒内跟上

    def _paint(self, name, st):
        row = self.rows[name]
        if not st:
            self._text(row["pos"], "—", "#6b8299" if self._connected else "#879caf")
            self._text(row["speed"], "—", "#8ba1b5")
            self._text(row["status"], "无响应" if self._connected else "未连接", "#8ba1b5")
            return

        running = bool(st.get("running"))
        alarm = bool(st.get("alarm")) or bool(st.get("limited"))
        self._text(row["pos"], "%.3f" % float(st.get("pos", 0.0)),
                   theme.BAD if alarm else (theme.CYAN if running else "#24506f"))
        self._text(row["speed"], "%.3f" % float(st.get("speed", 0.0)),
                   theme.CYAN if running else "#8ba1b5")

        if alarm:
            txt, col = ("限位" if st.get("limited") else "报警"), theme.BAD
        elif running:
            txt, col = st.get("text") or "运行中", theme.CYAN
        elif st.get("homed"):
            txt, col = "已回零", theme.OK
        else:
            txt, col = "未回零", theme.WARN
        self._text(row["status"], txt, col)

    def _text(self, lb, txt, color):
        """只在真的变了才改：200ms 一次，六行都重刷样式会白烧 CPU。"""
        if self._colors.get(id(lb)) == (txt, color):
            return
        self._colors[id(lb)] = (txt, color)
        if lb.text() != txt:
            lb.setText(txt)
        lb.setStyleSheet("color:%s;background:transparent;" % color)

    def _refresh_summary(self, axes):
        if self._estop_on:
            # 急停横幅压过一切：200ms 一次的轮询不该把「已上锁」这行字刷掉
            self.lb_status.setText("急停已按下并上锁：所有运动被拦住，"
                                   "确认现场安全后点【解除急停】。")
            self.lb_status.setStyleSheet(
                "color:%s;background:transparent;" % theme.BAD)
            return
        if not self._connected:
            self.lb_status.setText("设备未连接：切到【设备管理】页签连上之后这里才能动。")
            self.lb_status.setStyleSheet(
                "color:%s;background:transparent;" % theme.TXT3)
            return
        if not axes:
            self.lb_status.setText("已连接但读不到轴状态：检查通讯链路。")
            self.lb_status.setStyleSheet(
                "color:%s;background:transparent;" % theme.BAD)
            return

        bits, color = [], theme.TXT3
        not_home = [n.upper() for n in self.axes if not axes.get(n, {}).get("homed")]
        running = [n.upper() for n in self.axes if axes.get(n, {}).get("running")]
        limited = [n.upper() for n in self.axes if axes.get(n, {}).get("limited")]
        alarm = [n.upper() for n in self.axes if axes.get(n, {}).get("alarm")]

        if running:
            bits.append("运动中：%s" % " / ".join(running))
            color = theme.CYAN
        if limited:
            bits.append("限位：%s（反方向点动才能退出）" % " / ".join(limited))
            color = theme.BAD
        if alarm:
            bits.append("报警：%s（先查驱动/急停回路）" % " / ".join(alarm))
            color = theme.BAD
        if not_home:
            bits.append("未回零：%s" % " / ".join(not_home))
            if not self._require_home():
                bits[-1] += "（坐标仅供参考）"
            else:
                bits[-1] += "（只能点动，绝对定位会被拦下）"
            if color == theme.TXT3:
                color = theme.WARN
        if not bits:
            text = "全部已回零，可以正常定位。"
        else:
            text = "　·　".join(bits)
        self.lb_status.setText(text)
        self.lb_status.setStyleSheet("color:%s;background:transparent;" % color)

    # ---------------------------------------------------------------- 步长
    def _step(self):
        try:
            v = abs(float(self.inp_step.text().strip()))
            return v if v > 0 else 1.0
        except (TypeError, ValueError):
            return 1.0

    def _set_step(self, val):
        self.inp_step.setText("%g" % float(val))
        self._on_step_edited()

    def _on_step_edited(self):
        try:
            v = abs(float(self.inp_step.text().strip()))
            if v <= 0:
                raise ValueError
        except (TypeError, ValueError):
            v = 1.0
        self.inp_step.setText("%g" % v)
        # 记进配置（内存里立刻生效，落盘由页面那边防抖处理）
        self._dev_cfg()["jog_step"] = v
        self.step_changed.emit(v)

    # ---------------------------------------------------------------- 限位
    def _range(self, name):
        """该轴允许的绝对位置 (lo, hi)，None = 这个方向不限。

        直接问驱动要：三轴用 0 表示不限、六轴用 null，各自语义由驱动解释，
        页面不重复实现一套 —— 两处各算一遍迟早会对不上。
        """
        try:
            hi, lo = self.driver.limits(name)
        except Exception:
            return None, None
        return lo, hi

    def _range_text(self, name):
        lo, hi = self._range(name)
        if lo is None and hi is None:
            return "不限位"
        if lo is None:
            return "≤ %g" % hi
        if hi is None:
            return "≥ %g" % lo
        return "%g ~ %g" % (lo, hi)

    def _refresh_ranges(self):
        """把允许范围写进目标框的提示里（设备管理里改了限位，这里跟着变）。"""
        for name in self.axes:
            wd = self.rows[name]["target"]
            txt = self._range_text(name)
            tip = ("填绝对坐标，点【移动】走过去（本轴允许 %s mm，超了不会下发指令）"
                   % txt if txt != "不限位"
                   else "填绝对坐标，点【移动】走过去（本轴没设限位）")
            if wd.toolTip() != tip:
                wd.setToolTip(tip)
            # 空着的时候直接看到范围，省得先去查设备管理
            ph = "绝对坐标" if txt == "不限位" else txt
            if wd.placeholderText() != ph:
                wd.setPlaceholderText(ph)

    def _limit_why(self, name, target):
        """目标超出设备管理里设的限位就返回一句人话，放行返回 None。"""
        lo, hi = self._range(name)
        if hi is not None and target > hi + 1e-6:
            return ("%s 轴目标 %.3f mm 超出正限位 %.3f mm，未下发指令"
                    "（限位在【设备管理】里改）" % (name.upper(), target, hi))
        if lo is not None and target < lo - 1e-6:
            return ("%s 轴目标 %.3f mm 超出负限位 %.3f mm，未下发指令"
                    "（限位在【设备管理】里改）" % (name.upper(), target, lo))
        return None

    def _mark_target(self, name, bad):
        """超限时把目标框标红，改了输入就恢复（没变就不碰样式表，省得每次敲键重算）。"""
        wd = self.rows[name]["target"]
        qss = ("border:1px solid %s;background:#fdf3f2;" % theme.BAD) if bad else ""
        if wd.styleSheet() != qss:
            wd.setStyleSheet(qss)

    # ---------------------------------------------------------------- 动作
    def _require_conn(self):
        if not self.mgr.is_connected(self.key):
            self.logged.emit("未连接，操作被忽略。请先切到【设备管理】页签连接。", "warn")
            return False
        return True

    def _require_home(self):
        if self.key != "probe":
            return False
        return bool(self._dev_cfg().get("require_home", True))

    def _homed(self, name):
        return bool((self.state.get("axes") or {}).get(name, {}).get("homed"))

    def _on_jog(self, name, sign):
        if not self._require_conn():
            return
        delta = sign * self._step()
        desc = "%s 轴点动 %+.3f mm" % (name.upper(), delta)
        self.runner.submit(desc, lambda d, n=name, x=delta: d.move_rel(n, x))

    def _on_move(self, name):
        if not self._require_conn():
            return
        txt = self.rows[name]["target"].text().strip()
        if not txt:
            self.logged.emit("%s 轴还没填目标位置。" % name.upper(), "warn")
            self.rows[name]["target"].setFocus()
            return
        try:
            tgt = float(txt)
        except ValueError:
            self.logged.emit("%s 轴的目标不是有效数字：%s" % (name.upper(), txt), "err")
            return
        if self._require_home() and not self._homed(name):
            self.logged.emit(
                "%s 轴还没回零，绝对坐标不可信，已拦下。可以先点动，或点【回零】。"
                % name.upper(), "warn")
            return
        # 先在界面上拦一道：目标框立刻标红、日志说清超了多少。
        # 驱动层还会再比一次（那道才是真正的防线），这里只是让人马上看见。
        why = self._limit_why(name, tgt)
        if why:
            self._mark_target(name, True)
            self.logged.emit(why, "warn")
            self.rows[name]["target"].setFocus()
            return
        self._mark_target(name, False)
        self.runner.submit("%s 轴移动到 %.3f mm" % (name.upper(), tgt),
                           lambda d, n=name, t=tgt: d.move_abs(n, t))

    def _home_worker(self, name):
        return lambda d: d.home(
            name, on_stage=lambda stage, _pos=None, n=name:
            self.runner.stage.emit("%s 轴回零：%s" % (n.upper(), stage)))

    def _on_home(self, name):
        if not self._require_conn():
            return
        self.runner.submit("%s 轴回零" % name.upper(), self._home_worker(name))

    def _on_home_all(self):
        if not self._require_conn():
            return

        def work(d):
            done, fails = [], []
            for n in self.axes:
                if d.estop_active:
                    fails.append("急停已按下，%s" % " / ".join(
                        x.upper() for x in self.axes[len(done) + len(fails):]))
                    break
                ok, msg = d.home(
                    n, on_stage=lambda stage, _pos=None, nm=n:
                    self.runner.stage.emit("%s 轴回零：%s" % (nm.upper(), stage)))
                (done if ok else fails).append(msg)
            if fails:
                return False, "回零 %d/%d 成功；失败：%s" % (
                    len(done), len(self.axes), "；".join(fails))
            return True, "%d 轴回零完成：%s" % (len(done), "  ".join(done))

        self.runner.submit("全部回零", work)

    def _on_stop_all(self):
        if not self._require_conn():
            return
        self.runner.submit("全部停止", lambda d: d.stop())

    # ---------------------------------------------------------------- 回零设置
    def _on_home_cfg(self):
        """打开回零设置。探针座和三轴各一套对话框，口径不同（方向编码不一样）。"""
        cfg = self._dev_cfg()
        if self.key == "probe":
            dlg = HomeCfgDialog(cfg.get("home") or {}, self)
        else:
            dlg = MotionHomeDialog(cfg.get("home") or {}, self)
        if dlg.exec_() != QDialog.Accepted or not dlg.home:
            return
        # 原地改：驱动拿着的是同一个 dict，改完下一次回零就生效，不用重连
        home = cfg.setdefault("home", {})
        home.update(dlg.home)
        try:
            store.save(self.mgr.cfg)
        except (IOError, OSError) as e:
            self.logged.emit("回零设置没能写入配置：%s" % e, "err")
            return
        self._refresh_home_tips()
        if self.key == "probe":
            self.logged.emit("回零设置已保存：%s（%s，快移 %.2f mm/s）"
                             % (dlg.summary(),
                                store.HOME_MODE_TEXT.get(dlg.home["mode"], "?"),
                                dlg.home["rapid"]), "ok")
        else:
            self.logged.emit("回零设置已保存：%s" % dlg.summary(), "ok")
        self.logged.emit("下一步：先只回一根轴试方向，别直接点【全部回零】。", "info")

    def _refresh_home_tips(self):
        """把每根轴的「回零」按钮提示改成它自己的方向。"""
        for name, row in self.rows.items():
            if self.key == "probe":
                d = store.HOME_DIR_TEXT.get(self.driver.home_dir(name), "?")
                row["btn_home"].setToolTip(
                    "%s 轴回零：朝%s找 HOME 开关，完成后位置清零\n"
                    "方向在【回零设置】里改" % (name.upper(), d))
            else:
                p = self.driver.home_params(name)
                d = store.HOME_LIMIT_TEXT.get(p["dir"], "?")
                row["btn_home"].setToolTip(
                    "%s 轴回零：朝%s走，%.0f mm/s，到位后退 %.1f mm 离开开关\n"
                    "方向/速度/脱落距离在【回零设置】里改"
                    % (name.upper(), d, p["speed"], p["fall"]))

    # ---------------------------------------------------------------- 急停
    def _on_estop(self):
        """急停 / 解除急停 —— 同一个按钮两态。

        按一次：整机停 + 上锁，按钮变「解除急停」。只发一次停止是不够的，
        队列里排着的、流程里下一条指令会紧接着把轴再带走。
        再按一次：只解闸。**不续跑、不补动作**，轴停在哪儿就是哪儿；
        被硬停过的轴已经标成「未回零」，绝对定位会被拦到重新回零为止。
        """
        if self._estop_on:
            self.mgr.clear_estop()
            self._set_estop_ui(False)
            self.logged.emit("急停已解除。轴停在原地，不会自己续走；"
                             "被硬停过的轴已标为「未回零」，重新回零后再走绝对定位。"
                             "（探针座会拦住绝对定位；三轴只是状态显示，请自己确认。）", "ok")
            return

        # 先清队列：排队里的运动指令要是在停止之后才执行，等于没停
        n = self.runner.drain()
        extra = "（清掉了 %d 条还没执行的指令）" % n if n else ""
        try:
            res = self.mgr.estop()
        except Exception as e:
            traceback.print_exc()
            self.logged.emit("急停调用异常：%r —— 请立即按硬件急停！" % e, "err")
            return
        for key, ok, msg in res:
            if key == self.key:
                self.logged.emit("【急停】%s%s" % (msg, extra), "warn" if ok else "err")
                break
        else:
            self.logged.emit("【急停】本机没有可停的指令%s" % extra, "warn")
        # 另一台的停止结果也报一句：急停是整机的，别让人以为只停了自己这台
        for key, ok, msg in res:
            if key != self.key:
                self.logged.emit("　%s：%s" % (
                    DEVICE_META.get(key, {}).get("name", key), msg),
                    "ok" if ok else "err")
        if not any(ok for _k, ok, _m in res):
            self.logged.emit("没有任何设备执行了停止指令：请立即按硬件急停！", "err")

    def _set_estop_ui(self, on):
        """锁态/常态切换。整机只有一个闸，两块面板都跟着它走。"""
        on = bool(on)
        self._estop_on = on
        if on:
            self.btn_estop.setText("↻ 解除急停")
            self.btn_estop.setStyleSheet(ESTOP_LOCK_QSS)
            self.btn_estop.setToolTip("急停已生效并上锁：所有运动被拦住。\n"
                                      "确认现场安全后点这里解除；解除后轴停在原地，"
                                      "不会自己续走")
        else:
            self.btn_estop.setText("■  急 停")
            self.btn_estop.setStyleSheet("")
            self.btn_estop.setToolTip("立即停止所有轴并上锁；再按一次解除。\n"
                                      "解除后轴停在原地，不会自己续走")
        self.btn_estop.setEnabled(True)
        self._set_actions_enabled(self._connected and not on)
        if self.lb_status is not None and on:
            self.lb_status.setText("急停已按下并上锁：所有运动被拦住，"
                                   "确认现场安全后点【解除急停】。")
            self.lb_status.setStyleSheet(
                "color:%s;background:transparent;" % theme.BAD)

    # ---------------------------------------------------------------- 执行回执
    def _on_done(self, desc, ok, msg):
        self.logged.emit("%s → %s" % (desc, msg or ("完成" if ok else "失败")),
                         "ok" if ok else "err")

    # ---------------------------------------------------------------- 使能
    def _set_actions_enabled(self, on):
        for row in self.rows.values():
            for k in ("btn_m", "btn_p", "btn_move", "btn_home", "target"):
                row[k].setEnabled(on)
        self.btn_home_all.setEnabled(on)
        self.btn_stop_all.setEnabled(on)
        self.inp_step.setEnabled(on)
        self.btn_estop.setEnabled(True)
