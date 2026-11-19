# -*- coding: utf-8 -*-
"""01 首页：自动化测试效益总览（宣传页）。

这一页不是干活的地方，是**给人看的一页**：上来先看见系统在做什么，再看
"换成自动化之后到底省了什么"。所以版面由三块拼成：

    上排左   系统全景    循环播放的宣传片（cv2 解码）+ HUD 框、章节、进度
    上排右   效益数字    6× / 产能 / 漏检 / 追溯 / 上手，数字滚动登场
    下排     三张图      单板耗时构成 · 连续 8 小时良率 · 六维能力

几条取舍（改之前先看）：

* **宣传片用 cv2 解码，不用 QMediaPlayer**。离线机缺 Qt 多媒体后端就是黑屏，
  而且不给提示；cv2 是现成依赖。理由与线程模型写在 ``app/media/reel.py`` 顶部。
* **画面边缘做白化羽化，不套黑色显示器边框**。源片底色本来就接近白，羽化之后
  3D 线稿像是"浮"在白卡上；套黑框会跟浅色主题打架，还把卡片压得很重。
* **数字按同一套口径自洽**：60 min → 10 min 才是 6×；8 h 班次 8 板 → 48 板，
  48 × 10 min = 480 min 正好一个班。改数字时这几处要一起改，否则一算就露。
* **示例值必须写明**。页脚一行小字"按单板 60 min、8 h 班次测算"——宣传页最怕
  把估算值当实测值，现场一核对就穿，反而砸掉整套东西的可信度。
* **动画只在可见时跑**。切走就停表、暂停解码。进场动画结束后三张图不进静帧，
  而是各留一个**低频循环**：效益表一排一排点名（5.2 s 一圈）、良率曲线 0→8 h
  反复重画（5.2 s 一圈）、六维图慢慢自转（75 s 一圈）。刷新率按卡片分开给
  （关键帧多的给 20 fps，曲线和雷达 20 fps 也够），别让一张基本静态的图占满 CPU
  —— 工业机上还跑着 cv2 解码和别的进程。
* 卡片一律 ``paint_panel`` 打底、``_card_head`` 起头，跟其余页面同一套语言；
  别在这里新造卡片样式。

排版上的注意点：卡片高度是布局给的（窗口 1100×700 也得出得来），所以所有
纵向位置都从 ``r`` 现算、从下往上留白，不要写死 y。
"""

import math
import os
import time

from PyQt5.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PyQt5.QtGui import (QColor, QFontMetrics, QLinearGradient, QPainter,
                         QPainterPath, QPen, QPixmap)
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget

from .. import theme
from ..media import ReelDecoder
from ..widgets import HEAD_H, _font, _q, paint_module_head, paint_panel

# ---------------------------------------------------------------- 常量
ASSET = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "assets", "home_reel.mp4")

PAD_L = 18
PAD_T = 16
PAD_B = 18
GAP = 14

ROW1 = 0.62                     # 上下两行的比例

INK   = theme.TITLE
BLUE  = theme.AMBER
BLUE2 = theme.AMBER2
BLUE3 = theme.AMBER_DIM
WARM  = "#b0740f"               # "人工"一律用暖色，跟自动化的蓝对开
MUTE  = theme.TXT3
SUB   = theme.TXT2
GRID  = "#dde8f3"
FAINT = "#a9bccd"

# 宣传片章节：(起始秒, 名字)。名字取自片子里的分镜小标题；
# 时间点是用**帧间差分**找出来的转场峰值（3.2 / 5.1 / 7.6 / 9.5 / 11.6 / 17.0），
# 再逐点抽帧核对标题 —— 手写估计会差好几秒，换片子必须重新跑一遍。
# 现在这条是 19.3 s 的精剪版（原 68.5 s），七段，平均不到 3 秒，所以章节名要短，
# 长了会跟时间码抢位置。
CHAPTERS = (
    (0.0,  "平台开场"),
    (3.2,  "整机结构"),
    (5.1,  "运动系统"),
    (7.6,  "视觉引导"),
    (9.5,  "电性测试"),
    (11.6, "作业流程"),
    (17.0, "性能指标"),
)


# ---------------------------------------------------------------- 小工具
def _ease(x):
    """进场动画的缓动：先快后慢，收得干脆。"""
    x = max(0.0, min(1.0, x))
    return 1.0 - (1.0 - x) ** 3


def _chapter(t):
    """按播放秒数取当前章节名。"""
    name = CHAPTERS[0][1]
    for t0, n in CHAPTERS:
        if t >= t0:
            name = n
    return name


def _smooth(pts):
    """把折线点列变成顺滑曲线（中点 + 水平控制柄），数据点一个不改。"""
    path = QPainterPath(pts[0])
    for i in range(1, len(pts)):
        p0, p1 = pts[i - 1], pts[i]
        mx = (p0.x() + p1.x()) / 2.0
        path.cubicTo(QPointF(mx, p0.y()), QPointF(mx, p1.y()), p1)
    return path


def _curve_y(pts, x):
    """在横坐标 x 处取 ``_smooth`` 那条曲线的 y —— 让扫描头正好落在线上。

    直接用数据点线性插值会跟画出来的曲线差一两个像素，扫到头的时候看着像
    标点浮在线的外面。这里是同样的三次贝塞尔（控制柄在 x 中点），对 t 二分
    反解，18 次就足够准（误差 < 0.05 px）。
    """
    if x <= pts[0].x():
        return pts[0].y()
    if x >= pts[-1].x():
        return pts[-1].y()
    i = 0
    while i < len(pts) - 2 and x > pts[i + 1].x():
        i += 1
    x0, y0 = pts[i].x(), pts[i].y()
    x1, y1 = pts[i + 1].x(), pts[i + 1].y()
    xm = (x0 + x1) / 2.0
    lo, hi = 0.0, 1.0
    for _ in range(18):
        t = (lo + hi) / 2.0
        u = 1.0 - t
        c0, c1, c2, c3 = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
        if c0 * x0 + (c1 + c2) * xm + c3 * x1 < x:
            lo = t
        else:
            hi = t
    t = (lo + hi) / 2.0
    u = 1.0 - t
    return y0 + (y1 - y0) * (3 * u * t * t + t * t * t)


def _mix(c1, c2, k):
    """两色线性插值。轮播时数据色从蓝渐变到红，别硬切。"""
    a, b = QColor(c1), QColor(c2)
    k = max(0.0, min(1.0, k))
    return QColor(int(a.red() + (b.red() - a.red()) * k),
                  int(a.green() + (b.green() - a.green()) * k),
                  int(a.blue() + (b.blue() - a.blue()) * k))


_FCACHE = {}


def _cfont(family, px, bold=False, spacing=0.0, strike=False):
    """字号缓存的字体：轮播每帧都要按权重算字号，不缓存就每帧造一堆 QFont。"""
    key = (family, int(round(px)), bold, round(spacing, 1), strike)
    f = _FCACHE.get(key)
    if f is None:
        f = _font(family, key[1], bold, spacing)
        f.setStrikeOut(strike)
        _FCACHE[key] = f
    return f


def _card_head(p, r, title, tag=""):
    """卡片小标题：一道竖条 + 标题 + 右上角的等宽小字。返回内容起始 y。"""
    p.setPen(Qt.NoPen)
    p.setBrush(_q(BLUE))
    p.drawRect(QRectF(r.left(), r.top() + 4, 3, 12))

    p.setFont(_font(theme.F_BODY, 13, bold=True, spacing=0.7))
    p.setPen(_q(INK))
    p.drawText(QPointF(r.left() + 10, r.top() + 15), title)

    if tag:
        f = _font(theme.F_MONO, 9, spacing=1.4)
        p.setFont(f)
        p.setPen(_q(FAINT))
        p.drawText(QPointF(r.right() - QFontMetrics(f).horizontalAdvance(tag),
                           r.top() + 14), tag)
    return r.top() + 30


class _Card(QWidget):
    """卡片基类：白底 + 细边 + 角标，内容交给 paint_body。"""

    def __init__(self, title, tag="", delay=0.0, period=0.0, parent=None):
        super(_Card, self).__init__(parent)
        self._title = title
        self._tag = tag
        self._delay = delay
        self._period = period        # 进场结束后的刷新间隔，0 = 不再刷新
        self._t = 0.0
        self._p = 0.0
        self._due = 0.0
        self._done = False
        # 卡片自己铺满整块白底，所以声明"不透明"：否则 Qt 每帧会先把**父窗口**
        # 的工作区渐变重画一遍（1114×742）再画卡片 —— 宣传片 25 fps 下这一项
        # 就能吃掉小半个核，实测从 100% 掉到 40% 靠的就是这一行。
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    # ---------------------------------------------------------------- 时钟
    def tick(self, t):
        self._t = t
        self._p = _ease((t - self._delay) / 1.0)
        if not self._done:
            self.update()
            if t >= self._delay + 1.0:
                self._done = True
        elif self._period > 0 and t >= self._due:
            self._due = t + self._period
            self.update()

    # ---------------------------------------------------------------- 绘制
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        # 先把整块铺白：paint_panel 只画到 w-1，最后一列会露底
        p.fillRect(self.rect(), _q(theme.PANEL_TOP))
        r = self.rect().adjusted(0, 0, -1, -1)
        paint_panel(p, r)
        body = QRect(r.left() + 14, r.top() + 11,
                     r.width() - 28, r.height() - 23)
        if body.width() > 40 and body.height() > 40:
            self.paint_body(p, body)
        p.end()

    def paint_body(self, p, r):
        pass


# ================================================================ 系统全景
class VideoCard(_Card):
    """宣传片：循环播放，套一层 HUD（章节、时间码、进度、扫描带）。"""

    def __init__(self, parent=None):
        super(VideoCard, self).__init__("系统全景", "", 0.0, 0.0, parent)
        self._dec = None
        self._pm = None
        self._dur = 19.3
        self._pos = 0.0                  # 真实播放位置（秒），由解码线程上报
        self._err = ""

    # ---------------------------------------------------------------- 解码器
    def start_reel(self):
        if self._dec is not None or not os.path.isfile(ASSET):
            if not os.path.isfile(ASSET):
                self._err = "找不到宣传片：app/assets/home_reel.mp4"
                self.update()
            return
        self._dec = ReelDecoder(ASSET, 20, parent=self)
        self._dec.frame.connect(self._on_frame)
        self._dec.pos.connect(self._on_pos)
        self._dec.loaded.connect(self._on_loaded)
        self._dec.looped.connect(self._on_looped)
        self._dec.failed.connect(self._on_failed)
        self._dec.start()

    def set_paused(self, on):
        self._paused = bool(on)
        if self._dec is not None:
            self._dec.set_paused(on)

    def shutdown(self):
        if self._dec is not None:
            self._dec.stop()
            self._dec = None

    def _on_frame(self, img):
        # 解码线程已经缩到"画面矩形 × 屏幕倍率"，这里只做一次格式转换 + 打上 dpr，
        # 绘制时按 1:1 贴，不再重采样（重采样每帧要 1~3 ms，25 fps 下就是 5% 的核）
        self._pm = QPixmap.fromImage(img)
        self._pm.setDevicePixelRatio(self.devicePixelRatioF())
        self.update()
        if self._dec is not None:
            self._dec.ack()          # 回执：下一帧可以送来了

    def _on_pos(self, sec):
        self._pos = sec

    def _on_loaded(self, dur):
        if dur > 1.0:
            self._dur = dur

    def _on_looped(self):
        self._pos = 0.0

    def _on_failed(self, msg):
        self._err = msg
        self.update()

    # ---------------------------------------------------------------- 绘制
    def paint_body(self, p, r):
        # 播放位置用解码器上报的真实秒数：解码有快有慢，用墙钟算的话
        # 章节标签会和画面对不上（实测差三四秒）
        el = self._pos % max(self._dur, 1.0)
        tag = "%s · %02d:%02d / %02d:%02d" % (
            _chapter(el), el // 60, el % 60, self._dur // 60, self._dur % 60)
        y = _card_head(p, r, self._title, tag)
        box = QRect(r.left(), y + 2, r.width(), r.bottom() - y - 6)

        vw = min(box.width(), int(box.height() * 16 / 9))
        vh = int(vw * 9 / 16)
        vr = QRect(box.left() + (box.width() - vw) // 2,
                   box.top() + (box.height() - vh) // 2, vw, vh)
        if vr.width() < 60 or vr.height() < 40:
            return

        # ---- 画面
        dpr = self.devicePixelRatioF()
        if self._dec is not None:
            self._dec.set_target(vr.width() * dpr, vr.height() * dpr)
        if self._pm is not None and not self._pm.isNull():
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawPixmap(QPointF(vr.left(), vr.top()), self._pm)
            self._feather(p, vr)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(_q("#f4f9fd"))
            p.drawRect(vr)
            p.setFont(_font(theme.F_BODY, 11))
            p.setPen(_q(FAINT))
            p.drawText(vr, Qt.AlignCenter,
                       self._err or "宣传片加载中…")

        # ---- 四角框（HUD）
        p.setPen(QPen(_q(BLUE, 110), 1))
        p.setBrush(Qt.NoBrush)
        c, m = 14, 7
        x0, y0, x1, y1 = vr.left() + m, vr.top() + m, vr.right() - m, vr.bottom() - m
        p.drawLine(QPointF(x0, y0), QPointF(x0 + c, y0))
        p.drawLine(QPointF(x0, y0), QPointF(x0, y0 + c))
        p.drawLine(QPointF(x1, y0), QPointF(x1 - c, y0))
        p.drawLine(QPointF(x1, y0), QPointF(x1, y0 + c))
        p.drawLine(QPointF(x0, y1), QPointF(x0 + c, y1))
        p.drawLine(QPointF(x0, y1), QPointF(x0, y1 - c))
        p.drawLine(QPointF(x1, y1), QPointF(x1 - c, y1))
        p.drawLine(QPointF(x1, y1), QPointF(x1, y1 - c))

        # ---- 底部进度：贴着卡片下沿，不用再挤画面
        py = r.bottom() - 2
        tr = QRectF(r.left(), py, r.width(), 2)
        p.setPen(Qt.NoPen)
        p.setBrush(_q("#e4eef7"))
        p.drawRect(tr)
        p.setBrush(_q(BLUE))
        p.drawRect(QRectF(tr.left(), py, tr.width() * (el / self._dur), 2))
        for t0, _n in CHAPTERS[1:]:
            tx = tr.left() + tr.width() * (t0 / self._dur)
            p.setBrush(_q("#ffffff"))
            p.drawRect(QRectF(tx, py - 2, 1, 5))

    # ---------------------------------------------------------------- 羽化
    def _feather(self, p, vr):
        """把画面四条边化进白卡：不加边框，让它像浮在卡上。"""
        w = 20
        p.setPen(Qt.NoPen)
        for rect in (QRect(vr.left(), vr.top(), vr.width(), w),
                     QRect(vr.left(), vr.bottom() - w, vr.width(), w)):
            g = QLinearGradient(0, rect.top(), 0, rect.bottom())
            if rect.top() == vr.top():
                g.setColorAt(0.0, _q("#ffffff"))
                g.setColorAt(1.0, _q("#ffffff", 0))
            else:
                g.setColorAt(0.0, _q("#ffffff", 0))
                g.setColorAt(1.0, _q("#ffffff"))
            p.setBrush(g)
            p.drawRect(rect)
        for rect in (QRect(vr.left(), vr.top(), w, vr.height()),
                     QRect(vr.right() - w, vr.top(), w, vr.height())):
            g = QLinearGradient(rect.left(), 0, rect.right(), 0)
            if rect.left() == vr.left():
                g.setColorAt(0.0, _q("#ffffff"))
                g.setColorAt(1.0, _q("#ffffff", 0))
            else:
                g.setColorAt(0.0, _q("#ffffff", 0))
                g.setColorAt(1.0, _q("#ffffff"))
            p.setBrush(g)
            p.drawRect(rect)


# ================================================================ 效益数字
KPI_ROWS = (
    ("单板耗时", "60 min", "10 min"),
    ("单班产能", "8 板", "48 板"),
    ("漏检率", "3.2 %", "0.08 %"),
    ("追溯字段", "0 项", "128 项/板"),
    ("新人上手", "3 个月", "3 天"),
)

RED  = theme.BAD                # "轮到的那排"用红：一眼能分出哪一行在说事
BAND = "#fdecea"                # 轮播底：淡红，不抢正片

ROW_STEP = 1.05                 # 一排占一轮（秒），五行一整圈 5.25 s
ROW_FADE = 0.42                 # 一轮里最后 42% 用来交给下一排（交叉淡化，不硬切）


class KpiCard(_Card):
    """右上：先给一个大数字，再列五条"人工 → 自动"，五行轮着被点名。

    轮播用**交叉淡化**而不是滚动条：滚动到最后一排再回第一排会瞬移一下，
    看着像掉帧。这里两排同时在途、权重加起来恒等于 1，转回第一排是连贯的。
    """

    def __init__(self, parent=None):
        super(KpiCard, self).__init__("自动化带来的改变", "MANUAL → AUTO", 0.15, 0.05, parent)

    def paint_body(self, p, r):
        y = _card_head(p, r, self._title, self._tag)
        # 窗口 1100×700 时这张卡只有 286 高，大数字 + 五行硬排不下 —— 全按可用高度缩
        big_h = 84 if r.height() >= 320 else 64
        self._big(p, r, y, big_h)
        self._rows(p, r, y + big_h)

    # ---------------------------------------------------------------- 轮播权重
    def _weights(self):
        """每行当前的强调权重 0~1。同一时刻最多两行非零，加起来是 1。"""
        n = len(KPI_ROWS)
        u = self._t - 1.15                    # 等大数字滚完再开始轮
        if u <= 0.0:
            return [1.0] + [0.0] * (n - 1)
        step = u / ROW_STEP
        i = int(step) % n
        fr = step - int(step)
        t0 = 1.0 - ROW_FADE
        k = _ease((fr - t0) / ROW_FADE) if fr > t0 else 0.0
        w = [0.0] * n
        w[i] += 1.0 - k
        w[(i + 1) % n] += k
        return w

    # ---------------------------------------------------------------- 大数字
    def _big(self, p, r, y, big_h):
        val = 1.0 + 5.0 * self._p
        size = 46 if big_h >= 84 else 36
        f = _font(theme.F_DISP, size, bold=True, spacing=0.5)
        p.setFont(f)
        txt = "%.1f" % val
        tw = QFontMetrics(f).horizontalAdvance(txt)
        base = y + big_h - (30 if big_h >= 84 else 24)

        p.setPen(_q(BLUE, 26))
        p.drawText(QPointF(r.left() + 2, base + 2), txt)
        p.setPen(_q(BLUE))
        p.drawText(QPointF(r.left(), base), txt)

        p.setFont(_font(theme.F_DISP, 22 if big_h >= 84 else 18, bold=True))
        p.setPen(_q(BLUE))
        p.drawText(QPointF(r.left() + tw + 4, base), "×")

        tx = r.left() + tw + 34
        p.setFont(_font(theme.F_BODY, 12, bold=True, spacing=0.4))
        p.setPen(_q(INK))
        p.drawText(QPointF(tx, base - 22), "等效效率")
        p.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        p.setPen(_q(MUTE))
        p.drawText(QPointF(tx, base - 4), "单板 60 min → 10 min")
        p.drawText(QPointF(tx, base + 11), "同一台机、同一个人力投入")
        return p

    # ---------------------------------------------------------------- 五行
    def _rows(self, p, q, y):
        n = len(KPI_ROWS)
        avail = q.bottom() - y - 22          # 留 22 给脚注
        # 行高按剩余空间均分，别写死 42：写死了大窗口下面会空一大块、
        # 小窗口又会顶出去（1100×700 实测两样都犯）
        h = max(24.0, min(64.0, avail / n))
        w = self._weights()

        # 先铺分隔线，再叠轮播底：底是半透明的，线透出来反而有层次
        for i in range(1, n):
            ry = y + i * h
            p.setPen(QPen(_q("#e8f1f9"), 1))
            p.drawLine(QPointF(q.left(), ry), QPointF(q.right(), ry))

        for i, (lb, old, new) in enumerate(KPI_ROWS):
            ry = y + i * h
            # 视觉权重压一下曲线：交接时旧行退得快、新行上得晚，
            # 否则中途会同时看到两条完整的高亮，显脏
            k = w[i] ** 1.7

            if k > 0.015:                   # 轮到了：整排铺淡红 + 左侧竖条
                band = QRectF(q.left() - 7, ry + 0.5, q.width() + 14, h - 1.0)
                p.setPen(Qt.NoPen)
                p.setBrush(_q(BAND, int(238 * k)))
                p.drawRoundedRect(band, 3, 3)
                p.setBrush(_q(RED, int(230 * k)))
                p.drawRect(QRectF(band.left() + 1.0, band.top() + 3.0,
                                  2.6, max(4.0, band.height() - 6.0)))

            # 字号：轮到时标签 +2px、数据 +3px，别缩回去太快（k 已经缓动过）
            p.setFont(_cfont(theme.F_BODY, 12 + 2 * k, spacing=0.2))
            p.setPen(_mix(SUB, INK, k))
            p.drawText(QRectF(q.left(), ry, 90, h),
                       Qt.AlignLeft | Qt.AlignVCenter, lb)

            # 旧值：轮到时再压暗一档，把注意力推给右边的红字
            f_old = _cfont(theme.F_DISP, 12 - 1 * k, strike=True)
            p.setFont(f_old)
            p.setPen(_q("#a9bccd", int(255 - 80 * k)))
            ow = QFontMetrics(f_old).horizontalAdvance(old)
            p.drawText(QRectF(q.right() - 168 - ow, ry, ow + 2, h),
                       Qt.AlignRight | Qt.AlignVCenter, old)

            p.setFont(_font(theme.F_BODY, 10))
            p.setPen(_mix("#c0d0de", RED, k * 0.55))
            p.drawText(QRectF(q.right() - 164, ry, 16, h),
                       Qt.AlignCenter, "→")

            f_new = _cfont(theme.F_DISP, 13 + 3 * k, bold=True)
            p.setFont(f_new)
            p.setPen(_mix(BLUE, RED, k))
            nw = QFontMetrics(f_new).horizontalAdvance(new)
            p.drawText(QRectF(q.right() - nw, ry, nw + 2, h),
                       Qt.AlignRight | Qt.AlignVCenter, new)

        fy = y + n * h + 12
        if fy < q.bottom() - 6:
            p.setFont(_font(theme.F_BODY, 9, spacing=0.2))
            p.setPen(_q(FAINT))
            p.drawText(QRectF(q.left(), fy - 9, q.width(), 12),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       "＊示例值：按单板 60 min、8 h 班次测算，实际随板型浮动")


# ================================================================ 耗时构成
# (全名, 简称, 人工 min, 自动 min, 颜色)
SEGS = (
    ("定位对位", "定位", "15", "1.5", "#9dc0e2"),
    ("走位测量", "走位", "36", "7.5", "#4f86bd"),
    ("判定记录", "判定", "9", "1.0", "#1b57a0"),
)
TOTAL_MAN = 60.0
TOTAL_AUTO = 10.0


class TimeCard(_Card):
    """单板 60 min 拆成三段，跟自动的 10 min 摆在同一把尺子上。"""

    def __init__(self, parent=None):
        super(TimeCard, self).__init__("单板耗时构成", "MIN / BOARD", 0.3, 0.12, parent)

    def paint_body(self, p, r):
        y = _card_head(p, r, self._title, self._tag)

        lab_w = 32
        tot_w = 56
        x0 = r.left() + lab_w
        bar_w = max(40, r.width() - lab_w - tot_w)
        # 矮卡片（1100×700 只有 166 高）就压条高、缩间距，别把结论框挤出去
        roomy = (r.bottom() - y) >= 180
        bh = 30 if roomy else 22
        gap = 14 if roomy else 9

        y1 = y + 6
        y2 = y1 + bh + gap
        self._bar(p, x0, y1, bar_w, bh, "人工", TOTAL_MAN, r)
        self._bar(p, x0, y2, bar_w, bh, "自动", TOTAL_AUTO, r)

        # 自动完成的那条竖线：人工还在第二段时，机器已经出结果了
        mx = x0 + bar_w * (TOTAL_AUTO / TOTAL_MAN)
        pen = QPen(_q(WARM, 140), 1, Qt.DashLine)
        pen.setDashPattern([3, 3])
        p.setPen(pen)
        p.drawLine(QPointF(mx, y1 - 4), QPointF(mx, y2 + bh + 4))

        # 图例：用简称，卡片窄的时候三个并排也放得下
        ly = y2 + bh + (20 if roomy else 17)
        f = _font(theme.F_BODY, 9.5, spacing=0.2)
        p.setFont(f)
        cx = r.left()
        for i, (_full, short, man, auto, col) in enumerate(SEGS):
            p.setPen(Qt.NoPen)
            p.setBrush(_q(col))
            p.drawRoundedRect(QRectF(cx, ly - 7, 6, 6), 1, 1)
            p.setPen(_q(SUB))
            txt = "%s %s→%s" % (short, man, auto)
            p.drawText(QPointF(cx + 10, ly), txt)
            cx += QFontMetrics(f).horizontalAdvance(txt) + 18

        # 底部结论：同样产出下，两个班的账
        box = QRectF(r.left(), r.bottom() - 46, r.width(), 46)
        if box.top() > ly + 10:
            p.setPen(Qt.NoPen)
            p.setBrush(_q("#f2f8fd"))
            p.drawRoundedRect(box, 4, 4)
            p.setPen(QPen(_q("#dbe9f6"), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(box, 4, 4)

            p.setFont(_font(theme.F_BODY, 9.5, spacing=0.2))
            p.setPen(_q(MUTE))
            p.drawText(QPointF(box.left() + 12, box.top() + 16), "同样 48 块板")

            p.setFont(_font(theme.F_BODY, 11))
            p.setPen(_q(WARM))
            p.drawText(QPointF(box.left() + 12, box.top() + 35), "人工 48 h（6 个班）")

            p.setFont(_font(theme.F_BODY, 10))
            p.setPen(_q("#b9c9d7"))
            p.drawText(QPointF(box.left() + 128, box.top() + 35), "→")

            p.setFont(_font(theme.F_BODY, 11, bold=True))
            p.setPen(_q(BLUE))
            p.drawText(QPointF(box.left() + 140, box.top() + 35), "自动 8 h（1 个班）")

    def _bar(self, p, x, y, w, h, label, total, r):
        p.setFont(_font(theme.F_BODY, 10.5, bold=True, spacing=0.3))
        p.setPen(_q(WARM if label == "人工" else BLUE))
        p.drawText(QRectF(x - 34, y, 32, h), Qt.AlignRight | Qt.AlignVCenter, label)

        # 底槽
        p.setPen(Qt.NoPen)
        p.setBrush(_q("#f0f6fb"))
        p.drawRoundedRect(QRectF(x, y, w, h), 3, 3)

        # 分段：一律按 60 min 那把尺子换算，自动那条自然只剩 1/6
        factor = total / 60.0
        cx = x
        drawn = 0.0
        for _f, _s, man, _a, col in SEGS:
            seg = float(man) * factor * self._p
            seg = min(seg, total * self._p - drawn)
            if seg <= 0.01:
                continue
            sw = w * seg / 60.0
            p.setBrush(_q(col))
            p.drawRect(QRectF(cx, y, max(0.8, sw - 0.8), h))
            drawn += seg
            cx += sw

        p.setFont(_font(theme.F_DISP, 11.5, bold=True))
        p.setPen(_q(WARM if label == "人工" else BLUE))
        p.drawText(QRectF(x + w + 8, y, 48, h), Qt.AlignLeft | Qt.AlignVCenter,
                   "%d min" % int(round(total)))


# ================================================================ 良率曲线
HOURS = (0, 1, 2, 3, 4, 5, 6, 7, 8)
Y_MAN = (98.4, 98.0, 97.5, 96.8, 95.9, 94.9, 93.7, 92.6, 91.8)
Y_AUTO = (99.4, 99.4, 99.3, 99.4, 99.3, 99.4, 99.3, 99.4, 99.3)


SWEEP = 3.6     # 0 → 8 h 画完要多久（秒）
REST = 1.6      # 画完停一下，让两条尾巴读得清，再从头跑


class YieldCard(_Card):
    """连续 8 小时：人工的曲线会往下掉，自动的几乎是一条平线。

    这条曲线**每隔几秒从头画一遍**，不是画完就冻在那儿：扫描头走到哪、
    "现在几点"就在哪，右侧的终值要等跑到 8 h 才落定。静态图看不出"越干越差"，
    动起来才看得见人工那条一直在掉。
    """

    def __init__(self, parent=None):
        super(YieldCard, self).__init__("连续作业 8 小时", "YIELD %", 0.45, 0.05, parent)

    def _sweep(self):
        """0~1 的扫描进度：跑到 1 就停 REST 秒，再回到 0 重跑。"""
        u = self._t - 0.85
        if u <= 0.0:
            return 0.0
        return min(1.0, (u % (SWEEP + REST)) / SWEEP)

    def paint_body(self, p, r):
        y = _card_head(p, r, self._title, self._tag)

        lab_w, end_w, foot_h = 28, 46, 40
        plot = QRectF(r.left() + lab_w, y + 8,
                      r.width() - lab_w - end_w,
                      r.bottom() - y - 8 - foot_h)
        if plot.width() < 60 or plot.height() < 40:
            return

        lo, hi = 88.0, 100.0

        def px(h):
            return plot.left() + plot.width() * h / 8.0

        def py(v):
            return plot.bottom() - plot.height() * (v - lo) / (hi - lo)

        # 网格 + 刻度
        p.setFont(_font(theme.F_MONO, 8.5))
        for v in (90, 95, 100):
            gy = py(v)
            pen = QPen(_q(GRID), 1, Qt.DashLine)
            pen.setDashPattern([2, 4])
            p.setPen(pen)
            p.drawLine(QPointF(plot.left(), gy), QPointF(plot.right(), gy))
            p.setPen(_q(FAINT))
            p.drawText(QRectF(plot.left() - lab_w + 2, gy - 7, lab_w - 6, 14),
                       Qt.AlignRight | Qt.AlignVCenter, "%d" % v)

        s = self._sweep()
        hnow = 8.0 * s

        # x 轴刻度：扫描头走到哪一格，那一格就亮起来（比另加一个数字读数干净）
        f_h = _font(theme.F_BODY, 8.5)
        f_hot = _font(theme.F_BODY, 8.5, bold=True)
        for h in (0, 2, 4, 6, 8):
            hot = abs(h - hnow) < 0.3
            p.setFont(f_hot if hot else f_h)
            p.setPen(_q(BLUE if hot else FAINT))
            p.drawText(QRectF(px(h) - 14, plot.bottom() + 4, 28, 14),
                       Qt.AlignCenter, "%dh" % h)

        # 时间游标：竖虚线 + 轴上的小三角，先画，让曲线压在它上面
        if s > 0.004 and s < 0.999:
            hx = px(hnow)
            pen = QPen(_q(BLUE, 90), 1, Qt.DashLine)
            pen.setDashPattern([2, 4])
            p.setPen(pen)
            p.drawLine(QPointF(hx, plot.top()), QPointF(hx, plot.bottom()))
            p.setPen(Qt.NoPen)
            p.setBrush(_q(BLUE, 150))
            tri = QPainterPath()
            tri.moveTo(QPointF(hx - 3.5, plot.bottom() + 1))
            tri.lineTo(QPointF(hx + 3.5, plot.bottom() + 1))
            tri.lineTo(QPointF(hx, plot.bottom() - 3.5))
            tri.closeSubpath()
            p.drawPath(tri)

        # 曲线：按扫描进度裁一段画出来
        curves = [([QPointF(px(h), py(v)) for h, v in zip(HOURS, ys)], col, ys[-1])
                  for ys, col in ((Y_MAN, WARM), (Y_AUTO, BLUE))]
        p.save()
        p.setClipRect(QRectF(plot.left() - 1, plot.top() - 9,
                             plot.width() * s + 2, plot.height() + 20))
        for pts, col, _last in curves:
            path = _smooth(pts)
            area = QPainterPath(path)
            area.lineTo(QPointF(pts[-1].x(), plot.bottom()))
            area.lineTo(QPointF(pts[0].x(), plot.bottom()))
            area.closeSubpath()

            g = QLinearGradient(0, plot.top(), 0, plot.bottom())
            g.setColorAt(0.0, _q(col, 34))
            g.setColorAt(1.0, _q(col, 3))
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawPath(area)

            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(_q(col, 215), 1.7))
            p.drawPath(path)
        p.restore()

        # 班次进度读数：放图区左下角。两条曲线都在 91% 以上，压不到它
        if 0.004 < s < 0.999:
            p.setFont(_font(theme.F_MONO, 8.5))
            p.setPen(_q(MUTE))
            p.drawText(QRectF(plot.left() + 1, plot.bottom() - 15, 90, 12),
                       Qt.AlignLeft | Qt.AlignVCenter, "班次 %0.1f / 8.0 h" % hnow)

        # 扫描头：贴着曲线跑，走到哪算到哪
        if 0.004 < s < 0.999:
            for pts, col, _last in curves:
                hx = px(hnow)
                hy = _curve_y(pts, hx)
                p.setPen(Qt.NoPen)
                p.setBrush(_q(col, 42))
                p.drawEllipse(QPointF(hx, hy), 7.5, 7.5)
                p.setBrush(_q(col))
                p.drawEllipse(QPointF(hx, hy), 2.8, 2.8)

        # 末端：呼吸点 + 右侧数值（放轴外，避免压线）。跑到 8 h 才落定
        elif s >= 0.999:
            k = 0.5 + 0.5 * math.sin(self._t * 2.4)
            for pts, col, last in curves:
                ex, ey = pts[-1].x(), pts[-1].y()
                p.setPen(Qt.NoPen)
                p.setBrush(_q(col, int(40 + 50 * k)))
                p.drawEllipse(QPointF(ex, ey), 7.0, 7.0)
                p.setBrush(_q(col))
                p.drawEllipse(QPointF(ex, ey), 2.6, 2.6)

                p.setFont(_font(theme.F_DISP, 9.5, bold=True))
                p.setPen(_q(col))
                p.drawText(QRectF(plot.right() + 7, ey - 8, end_w - 4, 16),
                           Qt.AlignLeft | Qt.AlignVCenter, "%.1f%%" % last)

        # 图例（贴着底边，单独一行，别跟 x 轴刻度挤）
        f = _font(theme.F_BODY, 9.5, spacing=0.2)
        p.setFont(f)
        lx = plot.left()
        ly = r.bottom() - 5
        for col, name in ((WARM, "人工"), (BLUE, "自动化")):
            p.setPen(QPen(_q(col), 1.6))
            p.drawLine(QPointF(lx, ly - 4), QPointF(lx + 12, ly - 4))
            p.setPen(_q(SUB))
            p.drawText(QPointF(lx + 16, ly), name)
            lx += 16 + QFontMetrics(f).horizontalAdvance(name) + 16


# ================================================================ 六维能力
RADAR = (
    ("效率", 32, 94),
    ("一致性", 48, 95),
    ("精度", 58, 92),
    ("可追溯", 20, 96),
    ("人力节省", 12, 88),
    ("安全", 55, 90),
)

# 转多快：4.8°/s 一圈约 75 秒。慢了看不出在动，快了晕。
# 20 fps 下每帧 0.24°，肉眼看是连续的，不会有台阶。
RADAR_ROT = math.radians(4.8)

# 轴名离外圈留多少，以及单个轴名的竖直半高
LAB_PAD = 14
LAB_HH = 8


class RadarCard(_Card):
    """六个维度两两对比。数值 0~100，越大越好。

    整张图**绕中心慢慢自转**：蛛网、辐条、两个多边形一起转，轴名跟着位子走
    但永远保持水平（转的是角度，不是文字）。所以每一帧都要按当前角度重算
    文字框的对齐方式 —— 转到左边就右对齐、转到右边就左对齐，否则会压在图上。
    """

    def __init__(self, parent=None):
        super(RadarCard, self).__init__("六维能力", "0 - 100", 0.6, 0.05, parent)

    def paint_body(self, p, r):
        y = _card_head(p, r, self._title, self._tag)

        n = len(RADAR)
        area_h = r.bottom() - y
        f_axis = _font(theme.F_BODY, 9.5, spacing=0.3)
        lab_w = max(QFontMetrics(f_axis).horizontalAdvance(nm) for nm, _m, _a in RADAR)
        lg_w = QFontMetrics(_font(theme.F_BODY, 10)).horizontalAdvance("自动化") + 24

        # 转起来之后轴名会绕一整圈，所以左右都得留出 lab_w：外圈能张多大，
        # 由"去掉图例宽度后的一半"和"高度的一半"两头夹定，不能只看高度。
        room = r.width() - lg_w - 8
        cx = r.left() + max(46.0, room / 2.0)
        cy = y + area_h / 2.0
        rad = max(26.0, min(room / 2.0 - LAB_PAD - lab_w,
                            area_h / 2.0 - LAB_PAD - LAB_HH))

        rot = RADAR_ROT * max(0.0, self._t - 0.7)

        def ang(i):
            return -math.pi / 2 + i * 2 * math.pi / n + rot

        def pt(i, v):
            a = ang(i)
            rr = rad * v / 100.0
            return QPointF(cx + rr * math.cos(a), cy + rr * math.sin(a))

        # 蛛网
        p.setBrush(Qt.NoBrush)
        for k in (0.25, 0.5, 0.75, 1.0):
            poly = QPainterPath()
            for i in range(n):
                q = pt(i, 100 * k)
                if i == 0:
                    poly.moveTo(q)
                else:
                    poly.lineTo(q)
            poly.closeSubpath()
            p.setPen(QPen(_q(GRID if k < 1.0 else "#cbdced"), 1))
            p.drawPath(poly)

        p.setPen(QPen(_q(GRID), 1))
        for i in range(n):
            p.drawLine(QPointF(cx, cy), pt(i, 100))

        # 两个多边形（从中心长出来）
        p.save()
        p.setClipRect(QRectF(cx - rad - 4, cy - rad - 4, rad * 2 + 8, rad * 2 + 8))
        for idx, (col, fill_a) in enumerate(((WARM, 24), (BLUE, 30))):
            poly = QPainterPath()
            for i, (_n, man, auto) in enumerate(RADAR):
                v = man if idx == 0 else auto
                q = pt(i, v * self._p)
                if i == 0:
                    poly.moveTo(q)
                else:
                    poly.lineTo(q)
            poly.closeSubpath()
            p.setBrush(_q(col, fill_a))
            p.setPen(QPen(_q(col, 210), 1.5))
            p.drawPath(poly)
            if idx == 1 and self._p > 0.99:
                p.setBrush(_q(col))
                p.setPen(Qt.NoPen)
                for i, (_n, _m, auto) in enumerate(RADAR):
                    p.drawEllipse(pt(i, auto), 2.2, 2.2)
        p.restore()

        # 轴名：跟着角度绕圈，但字始终水平；对齐方式按当前方向现算
        p.setFont(f_axis)
        for i, (name, _m, _a) in enumerate(RADAR):
            a = ang(i)
            co, si = math.cos(a), math.sin(a)
            ax = cx + (rad + LAB_PAD) * co
            ay = cy + (rad + LAB_PAD) * si
            if co > 0.25:
                box, al = QRectF(ax, ay - LAB_HH, lab_w + 4, LAB_HH * 2), Qt.AlignLeft
            elif co < -0.25:
                box, al = QRectF(ax - lab_w - 4, ay - LAB_HH, lab_w + 4, LAB_HH * 2), \
                    Qt.AlignRight
            else:
                box, al = QRectF(ax - (lab_w + 4) / 2.0, ay - LAB_HH, lab_w + 4,
                                 LAB_HH * 2), Qt.AlignHCenter
            p.setPen(_q(SUB))
            p.drawText(box, al | Qt.AlignVCenter, name)

        # 图例（右侧竖排，转起来之后雷达会绕到它，得留够边）
        lx = r.right() - lg_w + 4
        ly = cy - 12
        f = _font(theme.F_BODY, 10)
        p.setFont(f)
        for col, name in ((BLUE, "自动化"), (WARM, "人工")):
            p.setPen(Qt.NoPen)
            p.setBrush(_q(col))
            p.drawRoundedRect(QRectF(lx, ly - 5, 8, 8), 2, 2)
            p.setPen(_q(SUB))
            p.drawText(QPointF(lx + 13, ly + 2), name)
            ly += 20


# ================================================================ 页面
class HomePage(QWidget):
    """01 首页。布局两行：上排 系统全景 + 效益数字，下排 三张图。"""

    def __init__(self, mod, parent=None):
        super(HomePage, self).__init__(parent)
        self.mod = mod
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(PAD_L, HEAD_H, PAD_L, PAD_B)
        root.setSpacing(GAP)

        row1 = QHBoxLayout()
        row1.setSpacing(GAP)
        self.video = VideoCard()
        self.kpi = KpiCard()
        row1.addWidget(self.video, 58)
        row1.addWidget(self.kpi, 42)

        row2 = QHBoxLayout()
        row2.setSpacing(GAP)
        self.time_c = TimeCard()
        self.yield_c = YieldCard()
        self.radar_c = RadarCard()
        row2.addWidget(self.time_c, 33)
        row2.addWidget(self.yield_c, 34)
        row2.addWidget(self.radar_c, 33)

        root.addLayout(row1, int(ROW1 * 100))
        root.addLayout(row2, int((1 - ROW1) * 100))

        self._cards = (self.kpi, self.time_c, self.yield_c, self.radar_c)
        self._t0 = time.time()
        self._timer = QTimer(self)
        self._timer.setInterval(40)          # 25 fps，够顺，也不至于占满一个核
        self._timer.timeout.connect(self._tick)

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    # ---------------------------------------------------------------- 生命周期
    def showEvent(self, e):
        super(HomePage, self).showEvent(e)
        self._timer.start()
        self.video.start_reel()
        self.video.set_paused(False)

    def hideEvent(self, e):
        super(HomePage, self).hideEvent(e)
        self._timer.stop()
        self.video.set_paused(True)

    def shutdown(self):
        self._timer.stop()
        self.video.shutdown()

    # ---------------------------------------------------------------- 时钟
    def _tick(self):
        t = time.time() - self._t0
        for c in self._cards:
            c.tick(t)
        self.video._t = t

    # ---------------------------------------------------------------- 绘制
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        paint_module_head(p, PAD_L, PAD_T, self.width() - PAD_L * 2,
                          self.mod["idx"], self.mod["name"], self.mod["desc"])
        p.end()
