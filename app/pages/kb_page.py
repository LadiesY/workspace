# -*- coding: utf-8 -*-
"""06 知识库：把资料变成"能查到、能核对"的一句话。

界面是一张**问答卡**（左：命中的原文片段；右：对话流 + 输入框），不是"搜索框 + 结果页"。
用户问的是一句话，界面就该像在跟他对话。

设计上的取舍（都是有原因的，改之前先看这里）：

* **先给一句结论，再给原文**。用户问的是问题，等的是一句话；只甩原文，
  他得自己在那段里找答案，这是"不够智能"最伤的地方。结论句是从命中的块里
  **原样摘**出来的（`rank_sentences`），不改写、不生成，下面永远跟着原文和出处。
* **不给答案就明说**。查不到时宁可空着并说清"是库里没这个词，还是资料真没写"，
  也不让模型编一个。这条比"看起来智能"重要得多；查不到也走同一套对话流，
  别切成一片空白页，那会让人以为卡住了。
* **"搜索记录"和"检索过程"是两件事**。现场要的是"我问过什么"（`history.json`，
  底部胶囊 + 首页清单 + 下拉），切词 / df / 耗时是给开发看的，收在回答下面的
  「看检索过程」里，不占版面。
* **索引在内存里重建**。资料是几百份以内，全量重建是毫秒级；
  换成持久化倒排反而要处理"资料更新了索引没更新"的各种脏状态。
* **页头右侧那行统计是自绘的**，和自动测试页的设备状态灯同一套做法 ——
  页头是 paintEvent 画的，没有布局可用。

排版上有几个坑踩过，别再踩：

* `QTextBrowser` 的文档会给块级元素套默认底色，深色主题下是一条条横带，
  看着像文字垫了灰纸 —— `setDefaultStyleSheet` 里统一压成透明；
  要底色的地方（气泡）用 `<td bgcolor>` 铺，别指望 `border-radius`（QTextDocument 不支持）。
* `ResultCard.fit_to()` 会被 `resizeEvent` 反复调用，每次都 `setFixedHeight`
  会让 layout 抖到所有卡片叠在一起，所以按宽度做了缓存。
* 胶囊按钮的宽度用 `QFontMetrics` 量，**QSS 里就别再写 font-size**：
  渲染字号和测量字号不一致时，末尾的字会被裁掉（"为什么读取不到T_"少了 `_2`）。
* 截图验证只能用真实窗口 + `primaryScreen().grabWindow()`：
  offscreen 模式下字体数为 0，`win.grab()` 又抓不到滚动区内容。
"""

import html as _html
import os
import re
import traceback
from datetime import datetime

from PyQt5.QtCore import QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QScrollArea,
                             QSizePolicy, QSplitter, QTextBrowser, QVBoxLayout,
                             QWidget)

from .. import theme
from ..knowledge import store
from ..knowledge.search import Index
from ..widgets import HEAD_H, HEAD_TOP, _font, _q, paint_module_head
from ..vision.pinedit import BTN_QSS

CARD_GAP = 10
LEFT_W = 366
HL_BG = "#fdf0c9"
HL_FG = "#7a5a10"

# 三档灰：弱（辅助）/ 中（次级说明）/ 正文。原来只有两档，层级压不出来。
TXTM = "#5b7186"
TXTS = "#8ba1b5"
PANEL_BG = "#ffffff"
PANEL_LN = "#dbe6f1"

# 问答面板（照 RAG 智能问答那张图做的）
HEAD_BAR_H = 44          # 面板标题条：▍RAG 智能问答 …… 会话号
CHIP_H = 26              # 示例 / 历史问题的胶囊
INPUT_H = 40             # 底部输入框
FOOT_TOP = 11            # 输入区上边距（ chips 行之上 ）
FOOT_GAP = 10            # chips 行与输入行之间的间距
FOOT_BOT = 12            # 输入区下边距（ 输入框到底板边框 ）
# 输入区整条高度只能由上面几项算出来，不要再手写一个数字。
# 之前写死 74，而内容实际要 11+26+10+40+12=99，QVBoxLayout 从下面开始挤，
# 最后一行（输入框 + 发送按钮）被挤出容器，屏幕上就只剩上半截。
FOOT_H = FOOT_TOP + CHIP_H + FOOT_GAP + INPUT_H + FOOT_BOT

CHIP_QSS = (
    "QPushButton{background:#f4f8fd;border:1px solid #c2d6e8;border-radius:4px;"
    "color:#6b8299;padding:0 11px;text-align:center;}"
    "QPushButton:hover{background:#eaf3fc;border-color:#8fb6da;color:#24506f;}"
    "QPushButton:pressed{background:#eaf3fc;}")
CHIP2_QSS = (
    "QPushButton{background:transparent;border:1px solid #d5e4f1;border-radius:4px;"
    "color:#8ba1b5;padding:0 10px;text-align:center;}"
    "QPushButton:hover{background:#f4f8fd;border-color:#93aec5;color:#6b8299;}"
    "QPushButton:disabled{color:#879caf;border-color:#e4eef7;}")
SEND_QSS = (
    "QPushButton{background:%s;border:none;border-radius:5px;color:#ffffff;"
    "font-size:13px;font-weight:600;letter-spacing:1px;}"
    "QPushButton:hover{background:#2e74c4;}"
    "QPushButton:pressed{background:#17497f;}"
    "QPushButton:disabled{background:#e2ebf3;color:#a8bacb;}" % theme.AMBER)
INPUT_QSS = (
    "QLineEdit{background:#ffffff;border:1px solid #cfe0ee;border-radius:5px;"
    "padding:0 12px;color:#1b4a78;selection-background-color:#bcd8f2;"
    "font-size:13px;}"
    "QLineEdit:focus{border-color:#8fb6da;background:#f2f7fb;}"
    "QLineEdit:hover{border-color:#a8c4de;}")

_KIND_CN = {"h": "标题", "p": "正文", "row": "表格行"}

# 切句用。答案面板靠它从整块里挑出"真正回答这一问"的那一句。
# 前瞻那个 `(?=\d+\.\s)` 是切 FAQ 用的：`…选的是哪份" 2. 改完程序要…`
# 中间没有标点，不在这里断开的话，答案会把上一题的尾巴当开头。
_SENT_SPLIT = re.compile(u"[。；;！!？?\n]+|(?=\\s*\\d+\\s*\\.\\s)")
# 这些词出现在句子里，说明这句是"讲原因/给处理"的，问「为什么」时优先
_WHY_HINT = (u"因为", u"原因", u"含义", u"不算", u"跳过", u"记进", u"没有",
             u"漏", u"警告", u"要处理", u"要不要处理", u"这么设计", u"不是错")
# 像小标题的句子（"2. 改完程序要重新做视觉启动吗"）只是在复述问题，不该当答案
_HEADING_LIKE = re.compile(u"^\\s*(?:\\d+\\s*[.、]|[一二三四五六七八九十]+、)")


def _esc(s):
    return _html.escape(s or "", quote=False)


def _split_sentences(text):
    """切句。

    资料里两种排版混着来：Word 段落有「。」有换行，而表格行 / 提炼过的短句是
    **空格连成一长条**（`对程序里的每一行： 识别方式 = 不读取 → 跳过 …`）。
    只按标点切的话，后者整块算一句，"结论"就变成把整段抄一遍。
    """
    raw = [p.strip(" \t·") for p in _SENT_SPLIT.split(text or "")]
    raw = [p for p in raw if p]
    # 一整条、没有句末标点的，有两种情况：术语串（`A → B  C → D`）要按空格切，
    # 普通长句（`支持自动或手动调节增益、曝光时间、白平衡、LUT、`）切了就碎。
    if len(raw) <= 1 and (u"→" in (text or "") or (text or "").count(" ") >= 5):
        raw = [p for p in re.split(r"\s+", (text or "").strip()) if p]
    # 碎片拼回 22 字上下的句子（"图上没有这个" + "label" + "→" + "记进…"）
    out, buf = [], ""
    for p in raw:
        if _HEADING_LIKE.match(p):
            # 小标题单独成句，别和上一句的尾巴粘在一起
            if buf:
                out.append(buf)
            out.append(p)
            buf = ""
            continue
        if not buf:
            buf = p
            continue
        # 上一片停在「：」「=」「→」上，话明显没说完，不管多长都接住下一片。
        if buf.rstrip().endswith((u"=", u"：", u":", u"→")):
            buf = (buf + " " + p).strip()
            continue
        if len(buf) < 22:
            # 只拼"明显被切开的一句话"：上一片话没说完（以冒号/逗号/顿号/= 结尾），
            # 或者这一片/上一片本身就很碎（<8 字）。
            # 反例：「含义：程序点名了，图上没这颗」和「要不要处理：要：改程序」
            # 各自成义，硬拼会读成一句怪话 —— 宁可分成两句给。
            half = buf[-1] in u"：:，,、="
            frag = len(p) <= 8 or len(buf) <= 8
            if half or frag:
                # 碎片里混着 label / id / T_2 这种英文、还有单独的 →：
                # 它们只是被空格隔开的词，补句号会切出「图上没有这个。label」这种怪句，
                # 用空格接回去就行。只有纯中文的碎句（"不用"）才补句号。
                ascii_frag = all(ord(ch) < 128 for ch in p) or p == u"→"
                ascii_tail = ord(buf[-1]) < 128
                joiner = u"。" if (not ascii_frag and not ascii_tail
                                   and len(buf) <= 6
                                   and buf[-1] not in u"。；，、：:") else " "
                buf = (buf + joiner + p).strip()
            else:
                out.append(buf)
                buf = p
        else:
            out.append(buf)
            buf = p
    if buf:
        out.append(buf)
    res = []
    for s in out:
        s = s.strip().strip(u"→").strip()
        if s:
            res.append(s)
    return res


def rank_sentences(text, terms, why=False, question=""):
    """切成句子打分，返回 [(分数, 句子)]，高的在前。

    评分 = 命中词数 + （问为什么时）原因提示词加分 - 复述问题 - 过长惩罚。
    最后一条是为了 FAQ 那种排版：「2. 改完程序要重新做视觉启动吗」命中的词最多，
    但它只是把问题抄了一遍，真正的答案在下一句。
    """
    qf = re.sub(u"[\\s？?。，,、！!；;：:]+", "", question or "")
    out = []
    for p in _split_sentences(text):
        if len(p) < 6:
            continue
        low = p.lower()
        n = sum(1 for t in terms if len(t) >= 2 and t.lower() in low)
        if not n:
            continue
        s = float(n)
        if why:
            s += 1.4 * sum(1 for h in _WHY_HINT if h in p)
            if re.match(u"^(为什么|原因|因为|为啥)", p):
                s += 1.8
        if _HEADING_LIKE.match(p) and len(p) < 46:
            s -= 2.2
        if qf and len(qf) >= 6:
            pf = re.sub(u"[\\s？?。，,、！!；;：:]+", "",
                        re.sub(u"^\\s*\\d+\\s*[.、]", "", p))
            if pf and (pf in qf or qf in pf) and len(pf) >= len(qf) * 0.6:
                s -= 6.0                     # 这句就是问题本身
        s -= max(0, len(p) - 90) * 0.012      # 长句压一点，别把整段搬上来
        out.append((s, p[:170]))
    out.sort(key=lambda x: -x[0])
    return out


def best_sentence(text, terms, why=False, question=""):
    got = rank_sentences(text, terms, why, question)
    return got[0][1] if got else ""



def _gap(h=6):
    w = QWidget()
    w.setFixedHeight(h)
    w.setStyleSheet("background:transparent;")
    return w


def _mark(text, terms):
    """原文 -> 带高亮的内联 HTML。

    terms 是检索真正命中的词（中文二元组），相邻的会重叠成一片 —— 合并区间
    再高亮，否则「曝光时间」会变成 3 个互相嵌套的 span，看起来像乱码。
    """
    text = text or ""
    if not terms:
        return _esc(text).replace("\n", "<br>")
    low = text.lower()
    spans = []
    for t in terms:
        t = (t or "").lower()
        if len(t) < 2 and not t.isalnum():
            continue
        i = low.find(t)
        while i >= 0:
            spans.append((i, i + len(t)))
            i = low.find(t, i + len(t))
    if not spans:
        return _esc(text).replace("\n", "<br>")
    spans.sort()
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out, last = [], 0
    for a, b in merged:
        out.append(_esc(text[last:a]).replace("\n", "<br>"))
        out.append('<span style="background-color:%s;color:%s;">%s</span>'
                   % (HL_BG, HL_FG, _esc(text[a:b]).replace("\n", "<br>")))
        last = b
    out.append(_esc(text[last:]).replace("\n", "<br>"))
    return "".join(out)


# ---------------------------------------------------------------- 小零件
class _Glass(QWidget):
    """放大镜图标。不写字符是因为 ⌕ / 🔍 在部分字体里是空框，画一个最稳。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(15, 15)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(_q(theme.TXT3), 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRect(1, 1, 9, 9))
        p.drawLine(9, 9, 13, 13)
        p.end()


class OutlineRow(QWidget):
    """左栏的章节行：点一下看这一节的原文。"""

    clicked = pyqtSignal(str)

    def __init__(self, chunk, level, parent=None):
        super().__init__(parent)
        self._id = chunk.get("id")
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setFixedHeight(28 if level <= 1 else 27)
        self._level = level
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10 if level <= 1 else 26, 0, 10, 0)
        self.lb = QLabel((chunk.get("text") or "").strip())
        # 二级目录原来 11px，中文笔画糊成一片；12px 和结果卡片正文对齐。
        self.lb.setFont(_font(theme.F_BODY, 12, bold=(level <= 1)))
        self.lb.setStyleSheet("background:transparent;color:%s;"
                              % (theme.TXT2 if level <= 1 else theme.TXT3))
        lay.addWidget(self.lb, 1)

    def enterEvent(self, _e):
        self._hover = True
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self._id or "")
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e):
        if not self._hover:
            return
        p = QPainter(self)
        p.fillRect(self.rect(), _q("#f7fbfe"))
        p.setPen(Qt.NoPen)
        p.setBrush(_q(theme.AMBER, 200))
        p.drawRect(0, 4, 2, self.height() - 8)
        p.end()


# ---------------------------------------------------------------- 结果卡片
class ResultCard(QWidget):
    """一条命中：序号 + 出处 + 摘要（关键词高亮）。"""

    clicked = pyqtSignal(int)

    def __init__(self, idx, res, parent=None):
        super().__init__(parent)
        self._idx = idx
        self._res = res
        self._hover = False
        self._sel = False
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        c = res.chunk
        lay = QVBoxLayout(self)
        lay.setContentsMargins(13, 10, 13, 12)
        lay.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(9)
        # 序号是视觉锚点：一排卡片扫下来，先看到的是 01 02 03，不是一堆同色的字
        self.lb_no = QLabel("%02d" % (idx + 1))
        self.lb_no.setFont(_font(theme.F_MONO, 12, bold=True, spacing=0.5))
        self.lb_no.setStyleSheet("color:#96acc0;background:transparent;")
        self.lb_no.setFixedWidth(20)
        top.addWidget(self.lb_no)
        self.lb_loc = QLabel((c.get("loc") or "").strip() or "未标注")
        self.lb_loc.setFont(_font(theme.F_BODY, 11, spacing=0.3))
        self.lb_loc.setStyleSheet("color:%s;background:transparent;" % TXTM)
        top.addWidget(self.lb_loc)
        top.addStretch(1)
        self.lb_kind = QLabel(_KIND_CN.get(c.get("kind"), "正文"))
        self.lb_kind.setFont(_font(theme.F_BODY, 9, spacing=0.6))
        self.lb_kind.setStyleSheet("color:%s;background:transparent;" % TXTS)
        top.addWidget(self.lb_kind)
        lay.addLayout(top)

        body = QLabel()
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        body.setFont(_font(theme.F_BODY, 12))
        body.setStyleSheet("color:%s;background:transparent;" % theme.TXT)
        self._plain = self._brief(c.get("text") or "", res.terms)
        body.setText(_mark(self._plain, res.terms))
        lay.addWidget(body)

        self.fit_to(LEFT_W)

    def _brief(self, text, terms, n=110):
        """摘要：优先从命中词附近截，而不是永远从第一句开始。"""
        s = " ".join(text.split())
        if len(s) <= n:
            return s
        low, pos = s.lower(), -1
        for t in terms:
            pos = low.find((t or "").lower())
            if pos >= 0:
                break
        start = max(0, pos - 26) if pos > 26 else 0
        chunk = s[start:start + n]
        return ("…" if start else "") + chunk + "…"

    def fit_to(self, w):
        """按列表实际宽度算高度 —— 换行后到底几行，交给 QFontMetrics 量。

        宽度没变就直接返回。这个方法会被 resizeEvent 反复调用；每次都 setFixedHeight
        的话，layout 每次都要重排，重排又改尺寸，最后所有卡片会叠在同一个位置上。
        """
        w = max(160, int(w) - 4)
        if getattr(self, "_fw", -1) == w:
            return
        self._fw = w
        fm = QFontMetrics(_font(theme.F_BODY, 12))
        r = fm.boundingRect(QRect(0, 0, max(90, w - 26), 4000),
                            Qt.TextWordWrap, self._plain)
        # 54 = 序号行（12px mono 带行距）+ 上下 22px 内边距；再留 8px 呼吸
        self.setFixedHeight(54 + max(18, r.height() + 2))

    def set_sel(self, on):
        if self._sel != bool(on):
            self._sel = bool(on)
            self.lb_no.setStyleSheet(
                "color:%s;background:transparent;"
                % (theme.AMBER if self._sel else "#96acc0"))
            self.lb_loc.setStyleSheet(
                "color:%s;background:transparent;"
                % (theme.TXT if self._sel else TXTM))
            self.update()

    def enterEvent(self, _e):
        self._hover = True
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self._idx)
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self.rect().adjusted(0, 0, -1, -1)
        if self._sel:
            p.fillRect(r, _q("#f1f7fc"))
            p.setPen(Qt.NoPen)
            p.setBrush(_q(theme.AMBER))
            p.drawRect(r.left(), r.top() + 1, 2, r.height() - 2)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(_q(theme.LINE2, 170), 1))
            p.drawRect(r)
        elif self._hover:
            p.fillRect(r, _q("#f7fbfe"))
            p.setPen(QPen(_q(theme.LINE2, 120), 1))
            p.drawRect(r)
        else:
            p.setPen(QPen(_q(theme.LINE, 130), 1))
            p.drawRect(r)
        p.end()


# ---------------------------------------------------------------- 页头统计
class _Stat(QWidget):
    """页头右侧的统计，自绘（页头没有布局可用）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text1 = ""
        self.text2 = ""
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_text(self, a, b):
        if (a, b) != (self.text1, self.text2):
            self.text1, self.text2 = a, b
            self.update()

    def sizeHint(self):
        f1 = _font(theme.F_MONO, 15, bold=True, spacing=0.6)
        fm1 = QFontMetrics(f1)
        w = max(fm1.horizontalAdvance(self.text1),
                QFontMetrics(_font(theme.F_BODY, 10)).horizontalAdvance(self.text2))
        return QSize(w + 26, 46)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        r = self.rect()
        p.setPen(QPen(_q(theme.LINE2, 90), 1))
        p.drawLine(0, 10, 0, r.height() - 10)
        p.setFont(_font(theme.F_MONO, 15, bold=True, spacing=0.6))
        p.setPen(_q(theme.AMBER_DIM))
        p.drawText(r.adjusted(14, 0, 0, -12), Qt.AlignVCenter | Qt.AlignLeft, self.text1)
        p.setFont(_font(theme.F_BODY, 10))
        p.setPen(_q(theme.TXT3))
        p.drawText(r.adjusted(14, 22, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, self.text2)
        p.end()


# ---------------------------------------------------------------- 页面
class KbPage(QWidget):
    def __init__(self, mod, parent=None):
        super().__init__(parent)
        self.mod = mod
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)
        self.state = {"sources": [], "chunks": []}
        self.idx = Index([])
        self.results = []
        self.hist = []                   # 搜索记录（问过的问题，不在 data 里另存）
        self._sel = -1
        self._mode = "blank"             # blank / result / sources / empty
        self._busy = False
        self._trace = {}
        self._trace_open = False
        self._menu = None

        self._build()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(280)
        self._timer.timeout.connect(self.refresh)

        # 会话号：按当天日期流水，和 RAG 面板上那个一致
        self.lb_session.setText("会话 KB-%s" % datetime.now().strftime("%Y%m%d-%H%M"))
        self.reload(auto_default=True)

    # ------------------------------------------------------------ 构建
    def _build(self):
        """整页分两块：左边是"证据"（命中的片段），右边是一张完整的问答卡。

        右边这张卡按 RAG 智能问答的形态做：标题条 + 对话流 + 示例胶囊 + 输入行，
        而不是"搜索框 + 结果页"。用户问的是一句话，界面就该像在跟他对话。
        """
        root = QVBoxLayout(self)
        root.setContentsMargins(18, HEAD_H, 18, 12)
        root.setSpacing(8)

        # ---------------- 左：命中的片段
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(7)

        self.lb_hint = QLabel("")
        self.lb_hint.setFont(_font(theme.F_BODY, 10, spacing=0.5))
        self.lb_hint.setStyleSheet("color:%s;background:transparent;" % TXTS)
        ll.addWidget(self.lb_hint)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:vertical{background:transparent;width:8px;margin:2px 0;}"
            "QScrollBar::handle:vertical{background:#c9dcec;border-radius:4px;}"
            "QScrollBar::handle:vertical:hover{background:#93aec5;}"
            "QScrollBar::add-line,QScrollBar::sub-line{height:0;}")
        self.holder = QWidget()
        self.holder.setStyleSheet("background:transparent;")
        self.vlist = QVBoxLayout(self.holder)
        self.vlist.setContentsMargins(0, 0, 2, 0)
        self.vlist.setSpacing(CARD_GAP)
        self.vlist.addStretch(1)
        self.scroll.setWidget(self.holder)
        ll.addWidget(self.scroll, 1)

        # ---------------- 右：问答面板
        self.panel = QWidget()
        self.panel.setObjectName("kbPanel")
        self.panel.setAttribute(Qt.WA_StyledBackground, True)
        self.panel.setStyleSheet(
            "QWidget#kbPanel{background:%s;border:1px solid %s;border-radius:8px;}"
            % (PANEL_BG, PANEL_LN))
        pl = QVBoxLayout(self.panel)
        pl.setContentsMargins(1, 1, 1, 1)
        pl.setSpacing(0)

        pl.addWidget(self._headbar())

        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(False)
        self.detail.setOpenLinks(False)
        self.detail.anchorClicked.connect(self._on_anchor)
        self.detail.setFrameShape(QFrame.NoFrame)
        self.detail.document().setDocumentMargin(0)
        self.detail.setStyleSheet(
            "QTextBrowser{background:transparent;border:none;padding:0;color:%s;}"
            "QScrollBar:vertical{background:transparent;width:8px;margin:2px 0;}"
            "QScrollBar::handle:vertical{background:#c9dcec;border-radius:4px;}"
            "QScrollBar::handle:vertical:hover{background:#93aec5;}"
            "QScrollBar::add-line,QScrollBar::sub-line{height:0;}" % theme.TXT)
        # QTextDocument 会给块级元素套一层默认底色，深色主题下就是一条条横带，
        # 看着像文字后面垫了块灰纸。统一按透明处理，需要底色的地方自己内联写。
        self.detail.document().setDefaultStyleSheet(
            "div,p,span,td,th,table,tr,li,ul,ol{background-color:transparent;}")
        pl.addWidget(self.detail, 1)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background:%s;border:none;" % PANEL_LN)
        pl.addWidget(line)

        pl.addWidget(self._foot())

        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(10)
        self.split.setStyleSheet(
            "QSplitter::handle{background:transparent;}"
            "QSplitter::handle:hover{background:#c9dcec;}")
        self.split.addWidget(left)
        self.split.addWidget(self.panel)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([LEFT_W, 900])
        root.addWidget(self.split, 1)

        # ---- 页头统计（自绘层，跟随 resize 摆位）
        self.stat = _Stat(self)
        self.stat.show()

    # ------------------------------------------------------------ 面板零件
    def _headbar(self):
        bar = QWidget()
        bar.setFixedHeight(HEAD_BAR_H)
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setStyleSheet("background:transparent;border:none;")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(15, 0, 10, 0)
        bl.setSpacing(9)

        tick = QFrame()
        tick.setFixedSize(2, 15)
        tick.setStyleSheet("background:%s;border:none;" % theme.AMBER)
        bl.addWidget(tick)

        lb = QLabel("RAG 智能问答")
        lb.setFont(_font(theme.F_BODY, 13, bold=True, spacing=0.8))
        lb.setStyleSheet("color:#12324f;background:transparent;")
        bl.addWidget(lb)
        bl.addStretch(1)

        self.btn_add = QPushButton("＋ 添加资料")
        self.btn_add.setFixedHeight(24)
        self.btn_add.setFixedWidth(88)
        self.btn_add.setFont(_font(theme.F_BODY, 11))
        self.btn_add.setStyleSheet(CHIP2_QSS)
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_add.setToolTip(
            "把 PDF / Word / 文本导进知识库（会复制一份到 data\\kb\\files）")
        self.btn_add.clicked.connect(self.add_files)
        bl.addWidget(self.btn_add)

        self.lb_session = QLabel("")
        self.lb_session.setFont(_font(theme.F_MONO, 10, spacing=0.5))
        self.lb_session.setStyleSheet("color:%s;background:transparent;" % TXTS)
        self.lb_session.setToolTip("这次打开的会话编号，按日期流水排")
        bl.addWidget(self.lb_session)
        return bar

    def _foot(self):
        """示例胶囊 + 输入行。搜索记录也放在这一行：问过的问题一眼能看见。"""
        w = QWidget()
        w.setFixedHeight(FOOT_H)
        w.setAttribute(Qt.WA_StyledBackground, True)
        w.setStyleSheet("background:transparent;border:none;")
        fl = QVBoxLayout(w)
        fl.setContentsMargins(15, FOOT_TOP, 13, FOOT_BOT)
        fl.setSpacing(FOOT_GAP)

        row = QWidget()
        row.setStyleSheet("background:transparent;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(7)

        self.lb_try = QLabel("试试")
        self.lb_try.setFont(_font(theme.F_BODY, 10, spacing=0.8))
        self.lb_try.setStyleSheet("color:%s;background:transparent;" % TXTS)
        rl.addWidget(self.lb_try)

        self.chip_box = QWidget()
        self.chip_box.setStyleSheet("background:transparent;")
        # 不抢横向剩余空间，否则后面的 addStretch 失效，「搜索记录 / 答案可溯源」
        # 会紧贴着最后一个胶囊，而不是贴着面板右边。
        self.chip_box.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self.chip_lay = QHBoxLayout(self.chip_box)
        self.chip_lay.setContentsMargins(0, 0, 0, 0)
        self.chip_lay.setSpacing(7)
        rl.addWidget(self.chip_box)
        rl.addStretch(1)

        self.btn_hist = QPushButton("搜索记录")
        self.btn_hist.setFixedHeight(CHIP_H - 4)
        self.btn_hist.setFont(_font(theme.F_BODY, 10))
        self.btn_hist.setStyleSheet(CHIP2_QSS)
        self.btn_hist.setCursor(Qt.PointingHandCursor)
        self.btn_hist.setToolTip("看全部搜索记录（存在 data\\kb\\history.json）")
        self.btn_hist.clicked.connect(self._show_history)
        rl.addWidget(self.btn_hist)

        self.badge = QLabel("答案可溯源")
        self.badge.setFont(_font(theme.F_BODY, 10, spacing=0.4))
        self.badge.setStyleSheet(
            "color:#8ab4d6;background:#f4f9ff;border:1px solid #9dbfdd;"
            "border-radius:9px;padding:2px 9px;")
        self.badge.setToolTip("每条回答都标着出自哪份资料、第几页 / 哪一节，可以照着核对")
        rl.addWidget(self.badge)
        fl.addWidget(row)

        ir = QHBoxLayout()
        ir.setSpacing(9)
        self.ed = QLineEdit()
        self.ed.setFixedHeight(INPUT_H)
        self.ed.setFont(_font(theme.F_BODY, 13))
        self.ed.setStyleSheet(INPUT_QSS)
        self.ed.setPlaceholderText("输入问题，回车发送")
        self.ed.returnPressed.connect(self._on_send)
        self.ed.textChanged.connect(lambda _t: self._timer.start())
        ir.addWidget(self.ed, 1)

        self.btn_send = QPushButton("发送")
        self.btn_send.setFixedSize(72, INPUT_H)
        self.btn_send.setFont(_font(theme.F_BODY, 13, bold=True, spacing=1.2))
        self.btn_send.setStyleSheet(SEND_QSS)
        self.btn_send.setCursor(Qt.PointingHandCursor)
        self.btn_send.clicked.connect(self._on_send)
        ir.addWidget(self.btn_send)
        fl.addLayout(ir)
        return w

    def _on_send(self):
        self._timer.stop()                     # 回车发就别等那 280ms 防抖了
        self.refresh()

    # ------------------------------------------------------------ 搜索记录
    def _chip(self, text, weak=False):
        f = _font(theme.F_BODY, 12)
        fm = QFontMetrics(f)
        # 太长的问句在胶囊里显示成"为什么读取不到…"，完整的那句放 tooltip。
        # 不裁的话三四个历史问题就能把底部这一行撑爆（发送按钮被挤走）。
        label = fm.elidedText(text, Qt.ElideRight, 168)
        b = QPushButton(label)
        b.setFixedHeight(CHIP_H)
        b.setFont(f)
        b.setStyleSheet(CHIP2_QSS if weak else CHIP_QSS)
        b.setCursor(Qt.PointingHandCursor)
        # 宽度按实际字号量，两边各留 11px 内边距。QSS 里不要再写 font-size，
        # 否则渲染字号和这里的测量字号不一致，末尾的字会被裁掉。
        b.setFixedWidth(max(46, min(210, fm.width(label) + 26)))
        b.setToolTip(text)
        b.clicked.connect(lambda _c=0, s=text: self._ask(s))
        return b

    def _ask(self, q):
        self.ed.setText(q)
        self._timer.stop()
        self.refresh()
        self.ed.setFocus()

    def _fill_chips(self):
        """搜过 → 直接摆出最近问过的，这就是"搜索记录"；没搜过 → 摆几个能问的。"""
        while self.chip_lay.count():
            it = self.chip_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        self.hist = store.load_history()
        if self.hist:
            # 先摆"点了真有结果"的那几条，0 条的排后面 —— 一屏就三个位置，
            # 别让一个空查询把位置占了。
            hit = [x.get("q") for x in self.hist if x.get("n")]
            miss = [x.get("q") for x in self.hist if not x.get("n")]
            items = (hit + miss)[:3]
            head = "最近"
        else:
            items = self._samples()[:3]
            head = "试试"
        self.lb_try.setText(head if items else "")
        for s in items:
            self.chip_lay.addWidget(self._chip(s))
        # 按钮常显，没记录时置灰 —— 隐藏掉的话用户根本不知道有"搜索记录"这回事。
        self.btn_hist.setEnabled(bool(self.hist))
        self.btn_hist.setToolTip(
            "看全部搜索记录（存在 data\\kb\\history.json）" if self.hist
            else "还没有搜索记录")

    def _show_history(self):
        from PyQt5.QtCore import QPoint
        from PyQt5.QtWidgets import QMenu
        self.hist = store.load_history()
        if not self.hist:
            return
        m = QMenu(self)
        m.setStyleSheet(
            "QMenu{background:#f7fbff;border:1px solid #c2d6e8;padding:5px 0;}"
            "QMenu::item{padding:6px 22px 6px 16px;color:#4b6a86;font-size:12px;}"
            "QMenu::item:selected{background:#e7f1fb;color:#12324f;}"
            "QMenu::separator{height:1px;background:#d5e4f1;margin:5px 0;}")
        for x in self.hist[:26]:
            a = m.addAction("%s　　%s" % (x.get("q") or "", x.get("t") or ""))
            a.setData(x.get("q") or "")
        m.addSeparator()
        a = m.addAction("清空搜索记录")
        a.setData("__clear__")
        m.triggered.connect(self._on_hist_pick)
        self._menu = m                       # 留住引用，否则弹出来就被回收
        pos = self.btn_hist.mapToGlobal(QPoint(0, 0))
        m.exec_(QPoint(pos.x() - 120, pos.y() - m.sizeHint().height() - 6))

    def _on_hist_pick(self, action):
        q = action.data()
        if q == "__clear__":
            store.clear_history()
            self.hist = []
            self._fill_chips()
            return
        if q:
            self._ask(q)

    def _trace_one_line(self):
        """一行诊断，跟在回答底下。要细节点「看检索过程」。

        只留用户能懂的两件事：命中多少段、花了多久。"编号直通""几个改写词"
        是给调试看的，放在详情页里，别堆在这一行。
        """
        t = self._trace or {}
        v = t.get("verdict")
        if not v:
            return ""
        if v == "ok":
            s = "%d 段命中" % t.get("kept", 0)
            if t.get("ms"):
                s += "　·　%.0f ms" % t["ms"]
            return s
        if v == "no_terms":
            return "库里没有这些词"
        if v == "below_threshold":
            return "都不到门槛"
        if v == "no_match":
            return "一个词都没命中"
        return v

    # ------------------------------------------------------------ 数据
    def reload(self, auto_default=False):
        try:
            self.state = store.load()
            if auto_default and not self.state["sources"]:
                store.ensure_default()
                self.state = store.load()
            self.idx = Index(self.state["chunks"])
        except Exception:
            traceback.print_exc()
            self.state = {"sources": [], "chunks": []}
            self.idx = Index([])
        self._sync_stat()
        if self.ed.text().strip():
            self.refresh()
        else:
            self._show_blank()

    def _sync_stat(self):
        st = self.state
        n_file, n_chunk = len(st["sources"]), len(st["chunks"])
        size = sum(s.get("size", 0) for s in st["sources"]) / 1048576.0
        self.stat.set_text("%d 份资料 · %d 段" % (n_file, n_chunk),
                           "共 %.1f MB" % size if size else "还没导入资料")
        self.stat.adjustSize()
        self._place_stat()

    def _place_stat(self):
        s = self.stat.sizeHint()
        self.stat.resize(s)
        base = HEAD_TOP + QFontMetrics(_font(theme.F_DISP, 34, bold=True)).ascent()
        h = QFontMetrics(_font(theme.F_DISP, 34, bold=True)).height()
        top = int(round(base - h / 2.0 - s.height() / 2.0 + 4))
        self.stat.move(self.width() - 18 - s.width(), max(HEAD_TOP - 2, top))

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_sized", False):
            self._sized = True
            w = max(640, self.width())
            left = max(300, min(LEFT_W, int(w * 0.34)))
            self.split.setSizes([left, max(300, w - left - 12)])
            QTimer.singleShot(0, self._fit_cards)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_stat()
        wpx = self.scroll.viewport().width()
        for i in range(self.vlist.count() - 1):
            w = self.vlist.itemAt(i).widget()
            if isinstance(w, ResultCard):
                w.fit_to(wpx)

    # ------------------------------------------------------------ 检索
    def refresh(self):
        q = self.ed.text().strip()
        self._trace = {}
        self._trace_open = False
        if not q:
            self.lb_hint.setText("")
            self._show_blank()
            return
        if not self.idx.chunks:
            self._empty("库还是空的", "点右上角【添加资料】把 PDF / Word 导进来。")
            return
        self.results = self.idx.query(q, limit=40, trace=self._trace)
        # 记一条搜索记录（边打边搜的中间态会被 push_history 吃掉，只留最后那次）
        store.push_history(q, len(self.results))
        self.hist = store.load_history()
        self._fill_chips()
        self._sel = 0 if self.results else -1
        self._fill_list()
        if self.results:
            self.lb_hint.setText("命中的片段　·　%d 条" % len(self.results))
            self.lb_hint.setStyleSheet("color:%s;background:transparent;" % TXTS)
            self._show_result(0)
        else:
            self.lb_hint.setText("命中的片段　·　0 条")
            self.lb_hint.setStyleSheet("color:%s;background:transparent;" % TXTS)
            self._empty("资料里没有这一条",
                        self._miss_tip())

    def _miss_tip(self):
        """查不到时，按 trace 说清楚"到底为什么没有" —— 三种情况差别很大。"""
        t = self._trace or {}
        if t.get("verdict") == "no_terms":
            # 单字不进这个列表：切词会剩下"样""到"这种碎片，列出来只会让人困惑
            miss = [x for x in (t.get("dropped") or []) if len(x) >= 2][:5]
            s = "、".join(_esc(x) for x in miss) or "（没有可用的词）"
            return ("这一问里的词，资料里一个都没出现过：%s。<br><br>"
                    "两种可能：一是资料确实没写这件事（那就是真没有，不猜）；"
                    "二是换了叫法 —— 换成手册上的说法再试一次。" % s)
        if t.get("verdict") == "below_threshold":
            return ("有 %d 块沾到了问句里的词，但没有一块沾得够多，所以宁可空着。<br><br>"
                    "把问句缩短一点（只留关键的名词或编号）通常就有结果了。"
                    % t.get("cand", 0))
        return ("换个说法试试：型号写全（MV-CS200-10GM）、参数名用书上的叫法"
                "（曝光时间 / 靶面尺寸）、编号直接贴（T_2）。<br><br>"
                "这里不会猜 —— 查不到就是查不到。")

    def _trace_html(self):
        """检索详情：每个词在库里有几块、被哪一层捞上来、为什么这么多。"""
        t = self._trace or {}
        if not t:
            return ""

        def row(k, v):
            # 不用表格：QTextDocument 的 table 会在单元格空处留一条灰底。
            # 标签都是两个字，全角空格分开就对齐了。
            # background 显式写死：QTextDocument 会给块级元素套默认底色，
            # 不写的话第一行会出来一条横带（像文字被压住）。
            return ('<p style="font-family:\'Microsoft YaHei UI\';font-size:12px;'
                    'margin:0 0 9px 0;color:#4b6a86;background-color:#ffffff;">'
                    '<span style="color:%s;">%s</span>　　%s</p>'
                    % (TXTS, _esc(k), v))

        term_bit = "　".join(
            "%s<span style=\"color:%s;\">(%d)</span>"
            % (_esc(x), TXTS, len(self.idx.postings.get(x, ())))
            for x in (t.get("terms") or [])) or "—"
        rows = [
            row(u"问句", _esc(t.get("q"))),
            row(u"词项", term_bit + '<span style="color:%s;">　· 括号里是库里有几块含它</span>' % TXTS),
        ]
        if t.get("dropped"):
            rows.append(row(u"丢弃", "%s<span style=\"color:%s;\">　· 库里没有这个词，不计入门槛</span>"
                            % ("　".join(_esc(x) for x in t["dropped"]), TXTS)))
        if t.get("ext"):
            rows.append(row(u"改写", "%s<span style=\"color:%s;\">　· %d 个，只负责召回，不参与门槛</span>"
                            % ("　".join(_esc(x) for x in t["ext"]), TXTS, len(t["ext"]))))
        it = {"why": u"「为什么」类 → 加权 日志 / 常见问题 / 缺席 等排查段",
              "how": u"「怎么做」类 → 加权 步骤 / 流程 / 顺序 等操作段",
              "what": u"「是什么」类", "where": u"「在哪」类"}.get(t.get("intent"), u"未识别出问法")
        rows.append(row(u"问法", _esc(it)))
        if t.get("entity"):
            rows.append(row(u"门槛", u"命中 1 个词即放行<span style=\"color:%s;\">"
                            u"　· 因为问句里有编号/型号，这是确定性信号</span>" % TXTS))
        else:
            rows.append(row(u"门槛", u"要求命中 %d 个词" % t.get("need", 0)))
        rows.append(row(u"候选", u"%d 块 → 留下 %d 块 → 取前 %d"
                        % (t.get("cand", 0), t.get("kept", 0), len(self.results))))
        if t.get("top"):
            rows.append(row(u"前三", "　·　".join(
                _esc(x) for x in self._top_names(t["top"]))))
        rows.append(row(u"耗时", u"%.2f ms" % t.get("ms", 0)))

        return (
            '<div style="margin-left:26px;margin-right:26px;margin-top:20px;">'
            '<a href="t:" style="font-family:\'Microsoft YaHei UI\';font-size:11px;'
            'color:%s;text-decoration:none;letter-spacing:0.6px;">← 回到回答</a>'
            '<div style="font-family:\'Microsoft YaHei UI\';font-size:11px;'
            'color:%s;letter-spacing:1.2px;margin-top:20px;">检索过程</div>'
            '<div style="font-family:\'Microsoft YaHei UI\';font-size:18px;'
            'color:#0b2e5c;margin-top:8px;">这一条是怎么查出来的</div>'
            '<div style="font-family:\'Microsoft YaHei UI\';font-size:12px;'
            'color:%s;margin-top:8px;line-height:180%%;">'
            '答不上来的时候先看这里：是库里没这个词，还是被门槛砍了，还是资料真没写 —— '
            '三种情况的处理方式完全不同。</div>'
            '<div style="height:1px;background:%s;margin:16px 0 10px 0;"></div>'
            '%s'
            '</div>' % (TXTM, TXTS, TXTS, PANEL_LN, "".join(rows)))

    def _top_names(self, ids):
        """trace 里存的是块 id（s1-073），用户看不懂 —— 换成节名 / 位置。"""
        m = {c.get("id"): (c.get("heading") or c.get("loc") or c.get("id"))
             for c in (self.state.get("chunks") or [])}
        return [m.get(i) or i for i in ids]

    def _toggle_trace(self):
        """点「看检索过程」→ 整个对话区换成诊断页；点「回到回答」再换回来。"""
        if not self._trace:
            return
        if self._trace_open:
            self._trace_open = False
            if self.results:
                self._show_result(max(0, self._sel))
            else:
                self._empty("资料里没有这一条", self._miss_tip())
            return
        self._trace_open = True
        self.detail.setHtml(self._trace_html())
        self.detail.verticalScrollBar().setValue(0)

    def _fill_list(self):
        while self.vlist.count() > 1:
            it = self.vlist.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for i, r in enumerate(self.results):
            card = ResultCard(i, r, self.holder)
            card.clicked.connect(self._show_result)
            self.vlist.insertWidget(self.vlist.count() - 1, card)
        QTimer.singleShot(0, self._fit_cards)

    def _fit_cards(self):
        wpx = self.scroll.viewport().width()
        for i in range(self.vlist.count() - 1):
            w = self.vlist.itemAt(i).widget()
            if isinstance(w, ResultCard):
                w.fit_to(wpx)
        # 改完高度立刻重排一次。不主动 activate 的话要等下一轮事件循环，
        # 那一瞬间卡片是叠在一起的（截图里会看到文字糊成一团）。
        self.vlist.activate()

    def _pick(self, i):
        for j in range(self.vlist.count() - 1):
            w = self.vlist.itemAt(j).widget()
            if isinstance(w, ResultCard):
                w.set_sel(j == i)

    def _cover_ids(self):
        """封面 / 目录块的 id。这类块词项全中但什么也没讲，不能拿来当结论。"""
        idx = getattr(self, "idx", None)
        if idx is None:
            return set()
        return {c.get("id") for c, v in zip(idx.chunks, idx.cover) if v}

    def _section_text(self, chunk):
        """标题块本身只有一行字，要拿它后面那一节的正文来挑句子。"""
        chunks = self.state.get("chunks") or []
        pos = -1
        for i, c in enumerate(chunks):
            if c.get("id") == chunk.get("id"):
                pos = i
                break
        if pos < 0:
            return chunk.get("text") or ""
        base = int(chunk.get("level") or 1)
        buf = [chunk.get("text") or ""]
        for c in chunks[pos + 1:pos + 10]:
            if c.get("kind") == "h" and int(c.get("level") or 1) <= base:
                break
            buf.append(c.get("text") or "")
        return "\n".join(buf)

    def _answer_html(self):
        """把前几块里最该当结论的句子挑出来。

        用户问的是一句话，不是"撞到了哪几段"。原来直接甩原文，他得自己拼答案 ——
        这是"不够智能"最伤的地方，比排序不准更伤。

        做法是**跨块比句子**，不是"每块各出一句"：同一块里的高分句往往比
        下一块的第一句更答得上问题。每块最多贡献 2 句，保证不押在一条上。
        """
        t = self._trace or {}
        why = t.get("intent") == "why"
        q = (self.ed.text() or "").strip()
        pool = []
        cover = self._cover_ids()
        for r in self.results[:5]:
            c = r.chunk
            if c.get("id") in cover:
                continue                       # 封面 / 目录不当答案来源
            body = self._section_text(c) if c.get("kind") == "h" else (c.get("text") or "")
            # 挑句时把改写词也算进来（"读取不到"要认得"缺席/图里没有"），
            # 但高亮只用原词，见 Result.terms 的说明。
            for sc, s in rank_sentences(body, list(r.terms) + list(r.extra), why, q)[:2]:
                # 混一点块分进去：排得越前的块，抽出来的句子越可信
                pool.append((sc + max(0.0, r.score) * 0.10, s, c))
        if not pool:
            return ""
        pool.sort(key=lambda x: -x[0])
        if not pool or pool[0][0] < 0.8:
            return ""                          # 挑不出一句像样的，就别硬凑，直接给原文
        # FAQ 段落的第一行往往就是那句问题本身（"2. 改完程序要重新做视觉启动吗"），
        # 命中词最多但什么也没回答 —— 直接从答案里剔掉，不是降权就够的。
        qf = re.sub(u"[\\s？?。，,、！!；;：:]+", "", q)
        picked, seen = [], []
        top = pool[0][0]
        for sc, s, c in pool:
            if sc < top * 0.55:                # 明显是下一档的，不要了（跨行断句的碎片）
                break
            if qf and len(qf) >= 6:
                sf = re.sub(u"[\\s？?。，,、！!；;：:]+", "",
                            re.sub(u"^\\s*\\d+\\s*[.、]", "", s))
                if sf and (sf in qf or qf in sf) and len(sf) >= len(qf) * 0.6:
                    continue
            if any(s in x or x in s for x in seen):
                continue
            seen.append(s)
            picked.append((s, c))
            if len(picked) >= 3:
                break
        if not picked:
            return ""

        rows = "".join(
            '<div style="font-family:\'Microsoft YaHei UI\';font-size:15px;'
            'color:#24506f;line-height:192%%;margin-bottom:12px;">%s</div>' % _esc(s)
            for s, _c in picked)
        locs, seen2 = [], set()
        for _s, c in picked:
            k = (c.get("loc") or "").strip()
            if k and k not in seen2:
                seen2.add(k)
                locs.append(_esc(k))
        from urllib.parse import quote
        first = self.results[0].chunk
        who = (self.ed.text() or "").strip()
        if len(who) > 56:
            who = who[:56] + "…"
        trace = self._trace_one_line()

        # 对话流：先"你问"，再"RAG 助手"气泡，最后一行诊断 + 出处。
        # 气泡用 2px 竖条 + 深底模拟，QTextDocument 不支持圆角，别指望 border-radius。
        return (
            '<div style="margin:24px 26px 0 26px;font-family:\'Microsoft YaHei UI\';'
            'background-color:%s;">'
            '<div style="font-size:10px;color:%s;letter-spacing:1.4px;">你问</div>'
            '<div style="font-size:14px;color:#7f96ab;margin-top:7px;'
            'line-height:165%%;background-color:%s;">%s</div>'
            '<div style="font-size:10px;color:%s;letter-spacing:1.4px;'
            'margin-top:20px;background-color:%s;">RAG 助手</div>'
            '<table border="0" width="100%%" cellspacing="0" cellpadding="0" '
            'style="margin-top:9px;"><tr>'
            '<td width="2" bgcolor="%s"></td>'
            '<td bgcolor="#f7fbff" style="padding:16px 18px 14px 16px;">'
            '%s'
            '<div style="font-size:11px;color:%s;margin-top:4px;'
            'background-color:#f7fbff;">依据　%s</div>'
            '</td></tr></table>'
            '<div style="font-size:11px;color:%s;margin-top:9px;line-height:180%%;'
            'background-color:%s;">%s　·　'
            '<a href="t:" style="color:#8ab4d6;text-decoration:none;">看检索过程</a>'
            '　·　'
            '<a href="c:%s" style="color:#8ab4d6;text-decoration:none;">打开这一节</a>'
            '</div></div>'
            % (PANEL_BG, TXTS, PANEL_BG, _esc(who),
               TXTS, PANEL_BG,
               theme.AMBER, rows, TXTS, "　·　".join(locs) or "—",
               TXTS, PANEL_BG, trace or "检索完成", quote(first.get("id") or "")))

    def _show_result(self, i):
        if not (0 <= i < len(self.results)):
            return
        if getattr(self, "_trace_open", False):
            self._trace_open = False
        self._sel = i
        self._mode = "result"
        self._pick(i)
        r = self.results[i]
        c = r.chunk
        src = c.get("src") or ""
        loc = (c.get("loc") or "").strip() or "未标注"
        kind = _KIND_CN.get(c.get("kind"), "正文")
        n = len(self.results)

        answer = self._answer_html() if i == 0 else ""
        back = "" if i == 0 else (
            '<a href="r:0" style="font-size:11px;color:%s;text-decoration:none;'
            'letter-spacing:0.6px;">← 回到回答</a>' % TXTM)

        head = (
            '%s'
            '<div style="margin:22px 26px 0 26px;font-family:\'Microsoft YaHei UI\';'
            'background-color:%s;">'
            '%s'
            '<div style="font-size:10px;color:%s;letter-spacing:1.2px;margin-top:%s;'
            'background-color:%s;">原文　·　第 %d / %d 条</div>'
            '<div style="font-size:15px;color:#12324f;margin-top:8px;'
            'line-height:150%%;background-color:%s;">%s</div>'
            '<div style="font-size:11px;color:%s;margin-top:6px;'
            'background-color:%s;">%s　·　%s　·　%s</div>'
            '<div style="height:1px;background:%s;margin:14px 0 16px 0;"></div>'
            '</div>'
        ) % (answer, PANEL_BG,
             back,
             TXTS, "6px" if not answer else "0", PANEL_BG, i + 1, n,
             PANEL_BG, _esc(os.path.splitext(src)[0] or loc),
             TXTS, PANEL_BG, _esc(loc), kind, _esc(self._ext_of(src)),
             PANEL_LN)

        body = "" if c.get("kind") == "h" else (
            '<div style="margin:0 26px;font-family:\'Microsoft YaHei UI\';'
            'font-size:13px;line-height:198%%;color:#2b4a66;background-color:%s;">%s</div>'
            % (PANEL_BG, _mark(c.get("text") or "", r.terms)))

        ctx = self._context(c)

        foot = (
            '<div style="margin:0 26px;padding-bottom:22px;font-family:\'Microsoft YaHei UI\';">'
            '<div style="height:1px;background:%s;margin:20px 0 12px 0;"></div>'
            '<div style="font-size:11px;color:%s;background-color:%s;">'
            '命中的词：%s%s</div>'
            '<div style="font-size:11px;color:%s;margin-top:7px;'
            'background-color:%s;">出处：%s　→　%s</div>'
            '</div>'
        ) % (PANEL_LN, TXTS, PANEL_BG,
             "、".join(_esc(t) for t in r.terms[:12]) or "—",
             ("　+ 改写：" + "、".join(_esc(t) for t in r.extra[:8]))
             if r.extra else "",
             TXTS, PANEL_BG, _esc(src), _esc(loc))

        self.detail.setHtml(head + body + ctx + foot)
        self.detail.verticalScrollBar().setValue(0)

    def _context(self, chunk):
        """命中标题就把这一节正文摆出来；命中一行表格就把同页前后几行带上。

        只给"命中那一行"的话，用户还得自己去翻手册 —— 那就白做了。
        """
        chunks = self.state.get("chunks") or []
        pos = -1
        for i, c in enumerate(chunks):
            if c.get("id") == chunk.get("id"):
                pos = i
                break
        if pos < 0:
            return ""

        if chunk.get("kind") == "h":                 # 标题 -> 整节
            base = int(chunk.get("level") or 1)
            body = []
            for c in chunks[pos + 1:pos + 14]:
                if c.get("kind") == "h" and int(c.get("level") or 1) <= base:
                    break
                body.append(c)
                if len(body) >= 10:
                    break
            if not body:
                return ""
            rows = "".join(
                '<div style="margin-bottom:11px;color:#4b6a86;">%s</div>'
                % _esc(c.get("text")).replace("\n", "<br>") for c in body)
            return (
                '<div style="margin-top:20px;font-family:\'Microsoft YaHei UI\';'
                'font-size:11px;color:#8ba1b5;letter-spacing:0.5px;">本节内容</div>'
                '<div style="font-family:\'Microsoft YaHei UI\';font-size:12px;'
                'line-height:185%%;margin-top:10px;">%s</div>' % rows)

        if chunk.get("kind") != "row" or len(chunk.get("text") or "") > 56:
            return ""
        near = [c for c in chunks[max(0, pos - 3):pos] + chunks[pos + 1:pos + 4]
                if c.get("src_id") == chunk.get("src_id")
                and c.get("heading") == chunk.get("heading")]
        if not near:
            return ""
        rows = "".join(
            '<div style="margin-bottom:5px;">%s</div>'
            % _esc(c.get("text")).replace("\n", "<br>") for c in near[-6:])
        return (
            '<div style="margin-top:22px;font-family:\'Microsoft YaHei UI\';'
            'font-size:11px;color:#8ba1b5;letter-spacing:0.5px;">同页附近</div>'
            '<div style="font-family:\'Microsoft YaHei UI\';font-size:11px;'
            'color:#a4b6c6;line-height:180%%;margin-top:8px;">%s</div>' % rows)

    def _models(self):
        """从库里捞出像型号的串（`MV-CS200-10GM`），当搜索提示用。"""
        out, seen = [], set()
        for c in self.state.get("chunks") or []:
            for m in re.findall(r"\b[A-Z]{2,5}-[A-Z0-9]{2,6}(?:-[A-Z0-9]{1,6})?\b",
                                c.get("text") or ""):
                if len(m) >= 6 and m not in seen:
                    seen.add(m)
                    out.append(m)
            if len(out) >= 3:
                break
        return out[:3]

    def _ext_of(self, name):
        k = os.path.splitext(name or "")[1].lower().lstrip(".")
        return {"pdf": "PDF 手册", "docx": "Word 文档", "md": "Markdown",
                "txt": "文本"}.get(k, k.upper() or "资料")

    # ------------------------------------------------------------ 空态
    def _clear_list(self):
        while self.vlist.count() > 1:
            it = self.vlist.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _fill_outline(self):
        """左栏放「章节速览」：空着的大片黑比什么都难看，而且新用户不知道能问什么。"""
        self._clear_list()
        chunks = self.state.get("chunks") or []
        if not chunks:
            return
        srcs = {s["id"]: s for s in self.state.get("sources", [])}
        order, seen = [], set()
        for i, c in enumerate(chunks):
            if c.get("kind") == "h":
                key = (c.get("src_id"), (c.get("text") or "").strip())
                if key in seen:
                    continue
                seen.add(key)
                order.append((i, c, int(c.get("level") or 1)))
            elif c.get("kind") == "p" and (c.get("heading") or "").startswith("第"):
                key = (c.get("src_id"), c.get("heading"))
                if key in seen:
                    continue
                seen.add(key)
                order.append((i, {"id": c.get("id"), "text": c.get("heading"),
                                  "src_id": c.get("src_id")}, 1))
        cur_src = None
        count = 0
        for i, c, lv in order:
            if c.get("src_id") != cur_src:
                cur_src = c.get("src_id")
                s = srcs.get(cur_src)
                if s is not None and count:
                    self.vlist.insertWidget(self.vlist.count() - 1, _gap())
                if s is not None:
                    cap = QLabel("%s · %d 段" % (os.path.splitext(s.get("name", ""))[0][:22],
                                                 s.get("chunks", 0)))
                    cap.setFont(_font(theme.F_BODY, 9, spacing=0.8))
                    cap.setStyleSheet("color:#8ba1b5;background:transparent;"
                                      "padding:2px 10px 0 10px;")
                    self.vlist.insertWidget(self.vlist.count() - 1, cap)
            row = OutlineRow({"id": c.get("id"), "text": c.get("text")}, lv, self.holder)
            row.clicked.connect(self._show_section)
            self.vlist.insertWidget(self.vlist.count() - 1, row)
            count += 1
            if count >= 46:
                break

    def _show_blank(self):
        self._mode = "sources"
        self.lb_hint.setText("资料目录　·　点一节看原文")
        self.lb_hint.setStyleSheet("color:%s;background:transparent;" % TXTS)
        self._fill_chips()
        self._fill_outline()
        self.detail.setHtml(self._sources_html())
        self.detail.verticalScrollBar().setValue(0)

    def _show_section(self, chunk_id):
        """点左栏的章节 → 右栏显示这一节的原文（到下一个标题为止）。"""
        chunks = self.state.get("chunks") or []
        pos = -1
        for i, c in enumerate(chunks):
            if c.get("id") == chunk_id:
                pos = i
                break
        if pos < 0:
            return
        head = chunks[pos]
        base = int(head.get("level") or 1)
        body = []
        for c in chunks[pos + 1:pos + 12]:
            if c.get("kind") == "h" and int(c.get("level") or 1) <= base:
                break
            body.append(c)
            if len(body) >= 8:
                break
        self._mode = "section"
        self._pick(-1)
        src = head.get("src") or ""
        inner = "".join(
            '<div style="margin-bottom:12px;color:#2b4a66;">%s</div>'
            % _esc(c.get("text")).replace("\n", "<br>") for c in body) or \
            '<div style="color:#8ba1b5;">这一节下面没有正文。</div>'
        self.detail.setHtml(
            '<div style="margin:18px 28px 0 28px;font-family:\'Microsoft YaHei UI\';">'
            '<div style="font-size:10px;color:#8ba1b5;letter-spacing:1.4px;">这一节</div>'
            '<div style="font-size:19px;color:#0b2e5c;margin-top:9px;line-height:145%%;">%s</div>'
            '<div style="font-size:11px;color:#8ba1b5;margin-top:7px;">%s</div>'
            '<div style="height:1px;background:#dbe6f1;margin:15px 0 18px 0;"></div>'
            '<div style="font-size:13px;line-height:196%%;">%s</div></div>'
            % (_esc(head.get("text")), _esc(src), inner))
        self.detail.verticalScrollBar().setValue(0)

    def _empty(self, title, tip):
        """查不到 / 库为空。

        查不到时也走对话流：先摆"你问"，再用助手气泡说没有 ——
        直接切成一片空白页，用户会以为卡住了。
        """
        self._mode = "empty"
        self._clear_list()
        who = (self.ed.text() or "").strip()
        if not who or not (self.idx.chunks):
            self.detail.setHtml(
                '<div style="margin:64px 44px 0 44px;font-family:\'Microsoft YaHei UI\';">'
                '<div style="font-size:18px;color:#4b6a86;">%s</div>'
                '<div style="font-size:13px;color:#8ba1b5;margin-top:16px;'
                'line-height:200%%;">%s</div></div>' % (_esc(title), tip))
            return
        # 左栏整片黑会让人以为坏了，给一行说清"这儿本该有什么"
        lb = QLabel("左栏是命中的原文片段，这一问一段都没命中。")
        lb.setFont(_font(theme.F_BODY, 11))
        lb.setWordWrap(True)
        lb.setStyleSheet("color:%s;background:transparent;padding:9px 10px;"
                         "border:1px dashed #d5e4f1;border-radius:6px;" % TXTS)
        self.vlist.insertWidget(self.vlist.count() - 1, lb)
        if len(who) > 56:
            who = who[:56] + "…"
        self.detail.setHtml(
            '<div style="margin:24px 26px 0 26px;font-family:\'Microsoft YaHei UI\';'
            'background-color:%s;">'
            '<div style="font-size:10px;color:%s;letter-spacing:1.4px;">你问</div>'
            '<div style="font-size:14px;color:#7f96ab;margin-top:7px;'
            'line-height:165%%;">%s</div>'
            '<div style="font-size:10px;color:%s;letter-spacing:1.4px;'
            'margin-top:20px;">RAG 助手</div>'
            '<table border="0" width="100%%" cellspacing="0" cellpadding="0" '
            'style="margin-top:9px;"><tr>'
            '<td width="2" bgcolor="%s"></td>'
            '<td bgcolor="#f7fbff" style="padding:16px 18px 15px 16px;">'
            '<div style="font-size:16px;color:#6b8194;background-color:#f7fbff;">%s</div>'
            '<div style="font-size:13px;color:#7f96ab;line-height:192%%;'
            'margin-top:11px;background-color:#f7fbff;">%s</div>'
            '</td></tr></table>'
            '</div>'
            % (PANEL_BG, TXTS, _esc(who), TXTS, theme.WARN, _esc(title), tip))

    def _on_anchor(self, url):
        """右栏里的链接：示例问题 / 搜索记录 = 直接问；t: = 检索过程；节 = 看这一节。"""
        s = url.toString()
        if s.startswith("q:"):
            from urllib.parse import unquote
            self._ask(unquote(s[2:]))
        elif s.startswith("r:"):
            try:
                self._show_result(int(s[2:] or 0))
            except ValueError:
                pass
        elif s == "t:":
            self._toggle_trace()
        elif s == "h:clear":
            store.clear_history()
            self.hist = []
            self._fill_chips()
            self._show_blank()
        elif s.startswith("c:"):
            from urllib.parse import unquote
            self._show_section(unquote(s[2:]))

    # 常用问法。写死是有意的：从表格里自动抽"参数名"抽不准（"型号""参数""应用行业"
    # 都长得像），而示例点下去必须真有结果 —— 所以先拿这张表试，查得到才显示。
    _CANDS = ("曝光时间", "分辨率", "最大帧率", "靶面尺寸", "工作温度", "像素格式",
              "数据接口", "防护等级", "七列", "点位表", "S 型排序", "筛点")

    def _samples(self):
        out = []
        for q in self._CANDS:                  # 只留"点了真有结果"的
            if len(self.idx) and self.idx.query(q, 1):
                out.append(q)
            if len(out) >= 4:
                break
        if len(out) >= 4:
            return out
        for c in self.state.get("chunks") or []:   # 兜底：用章节名（去掉序号）
            if c.get("kind") != "h":
                continue
            t = re.sub(u"^[〇一二三四五六七八九十\\d.]+\\s*[、.]?\\s*", "",
                       (c.get("text") or "").strip())
            if 5 <= len(t) <= 20 and t not in out:
                out.append(t)
            if len(out) >= 5:
                break
        return out[:5]

    def _sources_html(self):
        """开场白。像 RAG 助手在等你提问，而不是一张"资料管理表"。"""
        st = self.state
        n_file, n_chunk = len(st["sources"]), len(st["chunks"])
        rows = []
        for s in st["sources"]:
            nm = s.get("name") or ""
            if len(nm) > 36:
                nm = nm[:34] + "…"
            rows.append(
                '<tr>'
                '<td width="46%%" bgcolor="%s" style="padding:8px 10px;color:#2b4a66;">%s</td>'
                '<td width="15%%" bgcolor="%s" style="padding:8px 10px;color:#5b7186;">%s</td>'
                '<td width="11%%" bgcolor="%s" style="padding:8px 10px;color:#5b7186;">%d</td>'
                '<td width="28%%" bgcolor="%s" style="padding:8px 10px;color:#8ba1b5;">%s</td>'
                '</tr>' % (PANEL_BG, _esc(nm),
                           PANEL_BG, self._ext_of(s.get("name")),
                           PANEL_BG, s.get("chunks", 0),
                           PANEL_BG, _esc(s.get("added", ""))))
        # border="0" + 单元格铺底色：QTextDocument 的表格不认 border-collapse，
        # 不铺的话整张表会带一条灰底，跟页面底色差一截。
        table = (
            '<table border="0" width="100%%" cellspacing="0" cellpadding="0" '
            'style="font-family:\'Microsoft YaHei UI\';font-size:12px;margin-top:9px;">'
            '<tr bgcolor="%s">'
            '<td width="46%%" style="padding:7px 10px;color:#8ba1b5;">资料</td>'
            '<td width="15%%" style="padding:7px 10px;color:#8ba1b5;">类型</td>'
            '<td width="11%%" style="padding:7px 10px;color:#8ba1b5;">片段</td>'
            '<td width="28%%" style="padding:7px 10px;color:#8ba1b5;">导入时间</td>'
            '</tr>%s</table>' % ("#f1f7fc", "".join(rows))) if rows else (
            '<div style="font-size:12px;color:#8ba1b5;margin-top:9px;">还没有资料。'
            '点右上角【＋ 添加资料】导入 PDF 或 Word。</div>')

        from urllib.parse import quote
        models = self._models()

        greet = (
            '知识库已加载 <span style="color:#14324f;">%d 份资料、%d 个片段</span>。'
            '可以直接问设备操作、故障排查、参数查询，回答会附上原文出处'
            '（哪份文件、第几页 / 哪一节）。'
            % (n_file, n_chunk)) if n_file else (
            '知识库还是空的。点右上角【＋ 添加资料】把 PDF / Word 导进来，'
            '导完就能问。')

        # 「最近问过」= 搜索记录，直接摆在开场白下面。这是用户要的"日志"。
        hist = self.hist or store.load_history()
        if hist:
            items = "".join(
                '<div style="margin-bottom:14px;background-color:%s;">'
                '<a href="q:%s" style="color:#a8c8de;text-decoration:none;'
                'font-size:12px;">%s</a>'
                '<div style="font-size:10px;color:%s;margin-top:3px;'
                'background-color:%s;">%s%s</div></div>'
                % (PANEL_BG, quote(x.get("q") or ""), _esc(x.get("q")),
                   TXTS, PANEL_BG, x.get("t") or "",
                   ("　·　%d 条" % x["n"]) if x.get("n") else "　·　没找到")
                for x in hist[:8])
            body = (
                '<div style="margin-top:26px;background-color:%s;">'
                '<div style="font-size:13px;color:#1b4a78;background-color:%s;">'
                '最近问过</div>'
                '<div style="font-size:11px;color:%s;margin-top:5px;'
                'background-color:%s;">存在 data\\kb\\history.json　·　'
                '<a href="h:clear" style="color:#8ab4d6;text-decoration:none;">'
                '清空记录</a></div>'
                '<div style="margin-top:11px;">%s</div></div>'
                % (PANEL_BG, PANEL_BG, TXTS, PANEL_BG, items))
        else:
            body = (
                '<div style="margin-top:26px;font-size:12px;color:%s;'
                'line-height:200%%;background-color:%s;">'
                '还没有搜索记录。在下面输入框里问一句，最近的 %d 条会留在这里，'
                '点一下就能重新查。</div>'
                % (TXTS, PANEL_BG, 60))

        return (
            '<div style="margin:24px 26px 0 26px;font-family:\'Microsoft YaHei UI\';'
            'background-color:%s;">'
            '<div style="font-size:10px;color:%s;letter-spacing:1.4px;'
            'background-color:%s;">RAG 助手</div>'
            '<table border="0" width="100%%" cellspacing="0" cellpadding="0" '
            'style="margin-top:9px;"><tr>'
            '<td width="2" bgcolor="%s"></td>'
            '<td bgcolor="#f7fbff" style="padding:16px 18px 15px 16px;">'
            '<div style="font-size:13px;color:#4b6a86;line-height:188%%;'
            'background-color:#f7fbff;">%s</div>'
            '</td></tr></table>'
            '<div style="font-size:13px;color:#1b4a78;margin-top:26px;'
            'background-color:%s;">知识库里的资料</div>'
            '%s'
            '%s'
            '<div style="font-size:11px;color:%s;margin-top:26px;line-height:200%%;'
            'padding-bottom:24px;background-color:%s;">%s'
            '库存在 data\\kb\\，原件副本在 data\\kb\\files\\（原文件删掉也能查）。</div>'
            '</div>'
            % (PANEL_BG, TXTS, PANEL_BG,
               theme.AMBER, greet,
               PANEL_BG, table, body,
               TXTS, PANEL_BG,
               ('也可以直接搜型号：%s。<br>' % _esc("、".join(models))) if models else ""))

    # ------------------------------------------------------------ 导入
    def add_files(self):
        paths, _f = QFileDialog.getOpenFileNames(
            self, "选资料（可多选）", "",
            "资料 (*.pdf *.docx *.txt *.md);;所有文件 (*)")
        if not paths:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.btn_add.setEnabled(False)
        self.btn_add.setText("导入中")
        QApplication.processEvents()
        try:
            ok, report = store.add(paths)
        except Exception as e:
            traceback.print_exc()
            report, ok = [{"ok": False, "name": "导入", "msg": str(e)}], 0
        finally:
            QApplication.restoreOverrideCursor()
            self.btn_add.setEnabled(True)
            self.btn_add.setText("添加资料")
        self.reload()
        bad = [r for r in report if not r.get("ok")]
        if ok:
            self._say_import(ok, report, bad)
            if self.ed.text().strip():
                self.refresh()
            else:
                self._show_blank()
        else:
            msg = bad[0].get("msg") if bad else "没导进去"
            self._empty("这些资料没能导入", _esc(msg))

    def _say_import(self, ok, report, bad):
        chunks = sum(r.get("chunks", 0) for r in report if r.get("ok"))
        tip = ("已导入 %d 份、%d 个片段。" % (ok, chunks))
        if bad:
            tip += "<br><br>" + "<br>".join(
                "%s：%s" % (_esc(r["name"]), _esc(r.get("msg", ""))) for r in bad)
        if self.ed.text().strip():
            return
        self.detail.setHtml(
            '<div style="padding:26px 28px;font-family:\'Microsoft YaHei UI\';">'
            '<div style="font-size:15px;color:#1e8f56;">%s</div>'
            '<div style="font-size:12px;color:#5b7186;margin-top:12px;'
            'line-height:190%%;">%s</div></div>' % ("导入完成", tip))

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        w = self.width() - 36 - self.stat.width() - 8
        paint_module_head(p, 18, HEAD_TOP, max(200, w), self.mod["idx"],
                          self.mod["name"], self.mod["desc"])
        p.end()

    def keyPressEvent(self, e):
        # 方向键在结果里上下走，不用离开输入框
        if e.key() in (Qt.Key_Down, Qt.Key_Up) and self.results:
            step = 1 if e.key() == Qt.Key_Down else -1
            self._show_result(max(0, min(len(self.results) - 1, self._sel + step)))
            self.scroll.ensureWidgetVisible(
                self.vlist.itemAt(max(0, self._sel)).widget(), 0, 40)
            return
        super().keyPressEvent(e)
