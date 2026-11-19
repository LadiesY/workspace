# -*- coding: utf-8 -*-
"""自动测试模块（左侧导航第 3 项）。

这一页最终要"按流程编排并自动执行整板测试"。目前做完的是**左栏**
（项目卡 + 画面）+ **右栏流程占位** + **整页底部那条运行日志**。
画布固定 450×300（从「视觉定位」搬过来的同一份控件），下面跟一条精简版
引脚编辑条。

  03 自动测试  按流程编排…            ● 三轴控制器 ● 探针座 ● 工业相机
    ┌ 项目 ──────────────────────┐ ┌ 测试流程 ── 23 行 · 6 框 ────┐
    │ [打开项目 ▾] PCB点位测试1 板号│ │ 框名 点位 像素 像素 空间 空间…│
    │ 检修状态[修前▾] 任务号[N.修前.5]│ │            Y1   Z1   u    v  │
    │ [视觉启动][保存][自动运行][■急停]│ │ T_2   1  1144.9 1217.3  —  — │
    ├ 画面 450×300 ──────────────┤ │ T_2   2  1218.8 1217.8  —  — │
    │        画布（同视觉定位）    │ │ …（14 列一屏看全）           │
    │ [选择 画笔 矩形框 撤回 删除]│ │                              │
    └────────────────────────────┘ └──────────────────────────────┘
    ┌ 运行日志 ─────────────────── 12 条 ─────────────────────────┐
    │ 14:33:29  信息  自动测试已就绪…                              │
    └──────────────────────────────────────────────────────────────┘

急停（整页最重要的一颗按钮）
----------------------------
红实心那颗在项目卡最右边，和三个业务按钮**隔开一段**，就是为了出事时
不会点错。它永远可点 —— 设备没连上也照样能按。

按下做三件事，顺序不能反：

1. **置闸**（`DeviceManager.estop_flag` + 每台驱动的 `_estop`）
2. **下发停止**：三轴 `stop_axis`（立即 + 减速两种模式各发一遍，再补
   Stop_Run）、探针 `GA_Stop(0xFF)` 六轴、相机 `stop_stream`
3. **上锁**：按钮变成「↻ 解除急停」，此后任何运动入口
   （`move_abs` / `move_rel` / `home`）都先过 `estop_block()`，一律拒绝，
   直到有人明确解除

为什么必须上锁：只发一次停止是不够的。自动流程里下一条指令会紧接着把轴
再带走 —— 停完又走，等于没停。探针回零那圈 120 秒的轮询也认这道闸
（`home_full(abort=...)`），按下就 `HomeStop` 退出，不用等超时。

为什么直接在主线程发、不排队：排在一个 120 秒的回零后面等就不叫急停了。
DLL 的停止调用只有几毫秒，宁可这点时间不刷新界面，也要保证指令立刻出去。

没连上的设备会在日志里**明确说"发不出去，请按硬件急停"**，绝不假装停了。

画布尺寸与项目卡的位置
----------------------
画布是**固定 450×300**（`CANVAS_W/CANVAS_H`，图像 1 px = 屏幕 1 px，不跟窗口缩）。
一列里"卡片 133 + 画布面板 358 + 日志 84 + 标题 56"约 651 px，
752 px 的页面放得下，所以项目卡留在左上（用户最早要的位置）。
页面最小尺寸 667×693（窗口约 913×781）。

*2026-11-06 试过 674×450*：那时一列要 898 px 放不下，项目卡被迫挪到右栏。
画布如果再调大（高度超过约 380 px），卡片就得让位 —— 记住这个数。

项目从哪来
----------
【打开项目】列出的是**功能模块 3（测试管理）里保存过的项目**，同一份
`data\\devices.json` → `test.projects`。选一个就把项目对象读进来
（保存地址、上次选的程序、yolo 模型、X 粗定位、探针步长），后面几个阶段
直接用 `self.proj` 里的值，不再回头问用户一遍。

为什么要重读一遍清单：项目是在测试管理页新建/改名的，本页构造时拿到的
`Registry` 是那一刻的快照；用户切过去建个项目再切回来，不重读就看不到。
所以 `showEvent` 和每次点【打开项目】之前都重建一次。

画面区为什么是"复制"而不是嵌一个 VisionPage
--------------------------------------------
视觉定位页（第 5 项）是一整页：左栏一堆参数 + 右栏画布。自动测试只要**画布
本身**（含画布下方的引脚编辑条），参数由流程自己决定，摆一整套过来只会挤掉
流程区。所以这里直接复用两个现成控件：

* `vision.canvas.InteractiveCanvas` —— 画布（缩放 / 平移 / 选点 / 画框）
* `vision.pinedit.PinEditKit`        —— 画布下方那条 [选择|画笔|矩形框|撤回…]

两者都是**同一份代码**，不是抄一遍：视觉定位页改了什么，这边跟着变，不会
出现"两个画布行为不一致"的老毛病。画布尺寸按用户要的 450×300 **固定死**
（不随窗口缩放），图像在里面按比例 fit，3:2 的板图不会变形、也不会被拉长。

引脚编辑条上的补点 / 画框 / 召回率，要等流程真的产出识别结果才有意义；现在
点了会在日志里说"还没有识别结果"，不会假装做了什么。

保存之后这条工具栏最右边多一颗【切换】（2026-11-09 加）：在「识别后的图」和
「相机实时画面」之间来回。保存成功会**自动**切实时（相机没连就留在识别图，
日志说明原因）；实时时整条编辑条锁住，因为那些操作改的是照片坐标，在动着的
画面上改只会把数据搞坏。切换细节见 `_enter_live / _exit_live / _toggle_live`。

设备状态（在页头右侧）
----------------------
三个点跟**设备管理**同步（`DeviceManager.device_changed`），本页不提供
连接/断开按钮 —— 连接是设备管理的职责，两处都能连迟早出现"这边断了
那边还以为连着"的糊涂账（跟运动控制页同一套规矩）。

位置是用户定的（2026-11-09）：摆在页头右边、和「03 自动测试」同一行，
不在项目卡里 —— 项目卡那一行腾出来放**检修状态**和**任务号**。页头是
自绘的（`paintEvent`），所以这条不是布局项，而是页面自己的子控件，
坐标由 `_place_devbar()` 按标题行基线现算，窗口一变就重摆一次。

* 绿点 = 已连接（ST_CONNECTED）
* 红点 = 未连接 / 连接失败
* 琥珀 = 连接中

日志
----
页面最下面那条是**整页共用的运行日志**：一切提示（`_say`）都带时间戳记进日志，
设备连上/断开、打开项目、按钮点了、画布报了未接功能，全走这一条通道。
状态行那一行**已经删掉**（用户要求，界面别堆提示词）：想知道"缺什么"就看
最后一条日志 —— 它信息更全，还能往上翻历史。

高度是**跟着窗口算的**（`_fit_log`：页高的 15%，夹在 84~124 px 之间，约 3~5 行），
不是写死的 —— 早先固定 146 px，窗口一矮就白占两成高度，把画布压成一条。
要更多历史就往上翻（滚动条），要更大画面就换大窗口，两边不互相挤。

右栏的 14 列点位表
------------------
右栏不再是占位，就是**「运行点位」那张表**的样子：14 列、n 行，一行 = 一个要测的
引脚。列头跟用户表一模一样：

    框名 | 点位 | 像素Y1 | 像素Z1 | 空间u | 空间v | 控制器Y2 | 控制器Z2 |
    探针vY3 | 探针vZ3 | 探针fV | 探针fW | 测试 | 结果

xlsx 里那两行合并表头（"像素坐标"跨 Y1/Z1）在 QTableWidget 里只能做一行，
所以把分组名写进列头文字（"像素 Y1"），完整含义挂在表头 tooltip 上，不丢信息。

**表头写成两行、14 列一屏看全**：一行写完"控制器 Y2"这种得占 60 px，十四列
加起来必然横向滚动。改成"控制器 / Y2"两行后每列只占 24~70 px，1280 及以上
的窗口正好铺满，一行都不用滚（1100 这种最小窗口除外 —— 那时右栏只剩不到
300 px，数字真要放不下，宁可滚也不把列压到看不清）。

三处为此做的取舍：

* 行号列（左上角那块空格）**不要了**：一行就是一个引脚，序号没意义，白占 38 px。
* 列宽**现算**：两行表头里最长那行 + 这列当前最长的数据。现在还是「—」的空列
  先窄着，等标定接上、真有数字了自己撑开 —— 不会一上来就把 14 列顶出屏幕。
* 列宽跟着**视口**（`eventFilter`）而不是页面 resize 走：滚动条一出现视口就窄
  14 px，跟着页面算会差一条滚动条的宽度，右边永远挂着一条横向滚动。

**行是算出来的，不是抄表的**：`program.resolve(chips, 程序各行)` 按功能模块 3
存的程序（识别方式 / 点位范围 / 点名范围 / 点位 / 测试类别）去挑 `*_pins.json`
里的引脚，跟上一轮生成 xlsx 用的是同一套规则。所以程序一改、重新识别一次，
这张表跟着变，不会出现"表里 25 行、实际板子 30 个脚"的对不上。

排序按**最终图片的几何位置做 S 型**（2026-11-06 定，见 `program.snake_rank`）：
先用「y 区间重叠」把框分成横带，带内按中心 x 排，第 0 带左→右、第 1 带右→左
反着来。同一框内按引脚号升序 —— 一颗芯片的引脚必须连续测完。
**不是**按 json 数组先后（那是 YOLO 这帧先检出谁，两次跑都不一样），也**不是**
按程序里的类别先后（那会在类别之间来回大跳，实测多走两成行程）。
json 本身不重排：落盘的就是识别 + 人工编辑的原样，顺序是读表那一刻按几何现算的。

目前只有 框名 / 点位 / 像素两列 / 测试 有值：空间坐标、控制器 Y2/Z2、探针
Y3/Z3、V/W 都要等坐标换算和标定（第三阶段），先留「—」占位但不假装有数；
结果列等真跑完才写 PASS / FAIL。

**表是常驻的**：一进这一页就能看到 14 个列头，没数据就是 0 行（框名空着）。
这里没有空态占位、也没有【载入】按钮 —— 用户要的是"打开就看到流程长什么样"，
不是先看一句提示再找按钮。缺什么（没项目 / 没程序 / 没识别结果）只记一行日志。

数据从哪来：项目保存地址下最新的 `*_pins.json`（`<项目>/<会话>/引脚json/`）。

【视觉启动】：一次完整的取图识别（细节在 `app/vision/session.py`）
-----------------------------------------------------------------
    点下去
      → 在项目保存地址下建本次会话目录（**板号.检修状态.序号**）+ 四个子目录
      → 3 秒倒计时（让人把手从台面收回来）
      → 相机抓一帧 → 存到「相机照片」
      → YOLO 识别矩形框（进度条走时间估算）→ 结果画在画布上
      → 引脚识别（再走一遍进度）→ 结果显示在画布上

板号（人工填，回车保存）
------------------------
项目卡第一行右侧那个输入框。填的是这一块板的编码（`PCB-2026-1109-03` 这种），
**回车即存进项目配置**（`data\\devices.json` → `test.projects[].board`），下次
打开项目自动回填 —— 不用每次重填。

它是会话目录名的**第一段**：`板号.检修状态.序号`，比如 `N.修前.5`。
2026-11-09 起取消了"没填板号就按时间命名"的兜底 —— 板号空着连【视觉启动】
都按不下去（日志会说先填板号）。缺一段的名字对不上号，跑出来也没法归档。

任务号（自动算，只读）
----------------------
项目卡第二行那个框里显示的就是**本次目录名**（`N.修前.5`），不是另填的编号。
板号回车、检修状态换值、打开项目这三个时机重算一次：
`同板号 + 同检修状态已有目录的最大序号 + 1`，一次都没跑过就是 1。
修前修后各数各的（`N.修前.1` 和 `N.修后.1` 可以并存），序号不复用
（删掉 3 号，下次还是按最大的往下走）。真开跑时 `ensure_session()` 会再算一次，
界面显示 5、期间别处先建了 5 就顺延到 6，绝不往已有目录里写。

为什么不问"要不要另存一份板号文件"：板号是**项目的属性**，和保存地址、yolo
模型一样跟项目走；放在保存地址里的话，换个地址就丢了。

识别完**不写文件** —— 这时是人工检查时间：画布下方那条编辑工具条的拖动角点 /
右键改标签 / 画笔补点 / 撤回全部可用（就是「视觉定位」页的那一份代码）。

【保存】：人工确认没问题才点
----------------------------
把「引脚 + 矩形框坐标」写成 `引脚json/<图名>_pins.json`，然后**立刻读回来**，
按项目里选的测试程序（识别方式 / 点位范围 / 点名范围 / 点位 / 测试类别）展开成
右栏那 14 列点位表 —— 表里的行就是接下来【自动运行】要走的点。

为什么保存要等人工确认：YOLO 偶尔会多框 / 少框一颗，引脚算法遇上丝印反光也会错。
框错了后面每一条运动指令都是照着错的坐标走的，所以中间必须留一道人的眼睛。

【自动运行】还没接（见页面末尾的 `_todo`）。
"""

import html
import json
import os
import time
import traceback

from PyQt5.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PyQt5.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPixmap
from PyQt5.QtWidgets import (QAbstractItemView, QComboBox, QGroupBox,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
                             QPlainTextEdit, QPushButton, QSizePolicy,
                             QStyle, QStyleOptionHeader, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from .. import icons, program, project, theme
from ..devices import DEVICE_META
from ..devices.base import ST_CONNECTED, ST_CONNECTING
from ..project import Registry
from ..vision import engine as veng
from ..vision.canvas import InteractiveCanvas
from ..vision.pinedit import BTN_QSS, PinEditKit
from ..vision.session import VisionSession
from ..widgets import HEAD_H, HEAD_TOP, Led, Panel, _font, paint_module_head

# 设备顺序，跟设备管理页一致
DEV_KEYS = ("motion", "probe", "camera")

# 画布按用户要的 450×300 固定，不随窗口缩放（图像 1 px = 屏幕 1 px）。
# 左栏 = 画布 + 面板左右内边距 16。
CANVAS_W = 450
CANVAS_H = 300
CANVAS_MIN_H = 180             # 窗口矮到放不下时的兜底高度（正常窗口用不到）
LEFT_W = CANVAS_W + 16

# 画布空态提示
CANVAS_HINT = ("视觉启动后在这里显示画面　·　Ctrl+滚轮缩放　·　"
               "按住滚轮拖动平移　·　双击复位")

# 日志区高度：**跟着窗口高度走**，不再固定 146 px（那会白占两成高度，
# 把画布压成一条）。窗口高就多给两行，窗口矮就收到 3 行左右，剩下的高度
# 全归画布 —— 画布是干活用的，日志只在出问题时才盯着看。
LOG_RATIO = 0.17               # 想分给日志的比例（状态行删了，省下的高度给它）
LOG_H_MIN = 84                 # 最少 3 行（再小读不出上下文）
LOG_H_MAX = 124                # 再多也就 5 行，不跟画布抢
LOG_MAX = 800                  # 日志最多留多少条，超了丢最老的

MENU_QSS = theme.menu_qss()

# 日志级别：词、颜色
LOG_LEVELS = {
    "info": ("信息", theme.TXT3),
    "ok":   ("成功", theme.OK),
    "warn": ("提醒", theme.WARN),
    "err":  ("错误", theme.BAD),
}

LOG_QSS = ("QPlainTextEdit{background:rgba(27,87,160,0.05);border:1px solid #dbe6f1;"
           "border-radius:3px;color:%s;padding:4px 8px;}" % theme.TXT2)

# 【自动运行】是这一页的主操作，给个绿色实心感，别和另外两个抢
RUN_QSS = (
    "QPushButton{background:rgba(30,143,86,0.10);border:1px solid #9ed0b4;"
    "border-radius:3px;color:#1e8f56;font-size:12px;font-weight:600;"
    "padding:0 8px;}"
    "QPushButton:hover{background:rgba(30,143,86,0.16);"
    "border-color:#1e8f56;color:#1e8f56;}"
    "QPushButton:pressed{background:rgba(30,143,86,0.24);}")

# 【保存】原来靠系统默认样式，浅色主题下几乎没有边框，混在四个按钮里看不出是个按钮。
# 给它一套和【视觉启动】同族的描边样式（中性蓝，不抢主操作）；
# 灰掉的时候也保留边框，只是整体变淡 —— 否则禁用态又变回"一行字"。
SAVE_QSS = (
    "QPushButton{background:#f4f9ff;border:1px solid #a8c4de;"
    "border-radius:3px;color:#24405c;font-size:12px;font-weight:600;"
    "padding:0 8px;}"
    "QPushButton:hover{background:#e7f1fb;border-color:#2e74c4;color:#17497f;}"
    "QPushButton:pressed{background:#dbe9f7;}"
    "QPushButton:disabled{background:#f6f9fc;border-color:#c7d8e7;color:#9db0c2;}")

# 急停生效后按钮换成「解除急停」：暗红描边、不再实心 —— 实心红是"有事按我"，
# 描边是"我已经按过了"。局部 styleSheet 会盖掉主题里的 #estop 规则，
# 恢复时置空字符串就还给它。
ESTOP_LOCK_QSS = (
    "QPushButton{background:rgba(192,57,43,0.10);border:1px solid #c0392b;"
    "border-radius:4px;color:#c0392b;font-size:12px;font-weight:bold;"
    "letter-spacing:0.5px;padding:0 4px;}"
    "QPushButton:hover{background:rgba(192,57,43,0.18);border-color:#a92a22;"
    "color:#c0392b;}"
    "QPushButton:pressed{background:rgba(192,57,43,0.28);}")

ESTOP_TIP = ("立即停止三轴控制器、探针座，并停掉相机采图。\n"
             "按下后所有运动被拦住，确认现场安全后再点一次解除")

# ---------------------------------------------------------------- 点位表 14 列
# 顺序、列名跟用户「运行点位.xlsx」完全一致。QTableWidget 只能一行表头，
# 所以 xlsx 里合并的"像素坐标 | 空间坐标 | 控制器 | 探针v | 探针f"分组名
# 直接写进列头（"像素 Y1"），完整意思放 tooltip，不丢信息。
#
# 表头**分两行写**（"控制器\nY2"）：14 列要在一屏里全放下，一行写完
# "控制器 Y2" 这种得占 60 px，十四列加起来就横向滚动了。分两行后每列
# 压在 26~44 px，1380 窗口下正好铺满，一行都不用滚。
# (两行表头, tooltip, 列宽下限)
# 第三个数只是"再挤也不能比这窄"的兜底；实际列宽在 _fit_cols 里按表头词宽
# 和当前数据的最长值现算 —— 现在的空列（空间/控制器/探针）先窄着，等有数
# 据了自己撑开，不会一上来就把 14 列顶出屏幕。
POINT_COLUMNS = (
    ("框名",       "矩形框名（pins.json 的 chips[].id，如 T_2）", 36),
    ("点位",       "引脚序号（chips[].pins[].idx）",              24),
    ("像素\nY1",   "像素坐标 Y1 = 引脚在图上的 x",                40),
    ("像素\nZ1",   "像素坐标 Z1 = 引脚在图上的 y",                40),
    ("空间\nu",    "空间坐标 u（等标定换算）",                    30),
    ("空间\nv",    "空间坐标 v（等标定换算）",                    30),
    ("控制器\nY2", "三轴控制器 Y2（等像素→机台换算）",           34),
    ("控制器\nZ2", "三轴控制器 Z2（等像素→机台换算）",           34),
    ("探针v\nY3",  "探针 f 通道 Y3（等换算）",                    34),
    ("探针v\nZ3",  "探针 f 通道 Z3（等换算）",                    34),
    ("探针f\nV",   "探针 f 通道 V（等换算）",                     30),
    ("探针f\nW",   "探针 f 通道 W（等换算）",                     30),
    ("测试",       "测试类别：V = 电压，f = 频率",                24),
    ("结果",       "测试结果：跑完填 PASS / FAIL",                30),
)

# 表头占两行，得比默认的一行高一些（9 px 字 × 2 行 + 上下留白）
POINT_HEAD_H = 34


def _head_font():
    """表头字体。列宽要靠它算，所以放在外面，别让 WrapHeader 独吞。"""
    return _font(theme.F_BODY, 9, spacing=0.1)


class WrapHeader(QHeaderView):
    """两行表头的 QHeaderView。

    Qt 的表头**天生不换行**：`CE_Header` 画文字时一律单行 + 省略号，
    所以表头里的 "\\n" 会被吞掉。想让它换行只有一条路 —— 自己把文字画上
    去：先让样式把底和分隔线画好（把 `opt.text` 清空），再按两行居中画。
    这样主题的底色 / 边框 / hover 都由 QSS 管，只有排版是这里的。

    **两段绘制必须各自 save/restore**：样式画完 section 之后 QPainter 的
    状态（裁剪区 / 画笔 / 字体）不一定是进来时那份，紧接着 drawText 会被
    整个吃掉 —— 表头看着一个字都没有，而且不报错，极难查。
    """

    def __init__(self, parent=None):
        super(WrapHeader, self).__init__(Qt.Horizontal, parent)
        self.setDefaultAlignment(Qt.AlignCenter)
        self.setSectionsClickable(False)
        self.setHighlightSections(False)
        self.setFixedHeight(POINT_HEAD_H)
        self._f = _head_font()

    def paintSection(self, painter, rect, index):
        # 第一段：底 + 分隔线交给主题样式画（文字清空，否则会被单行省略号截掉）
        painter.save()
        opt = QStyleOptionHeader()
        self.initStyleOption(opt)
        opt.rect = rect
        opt.section = index
        opt.text = ""
        self.style().drawControl(QStyle.CE_Header, opt, painter, self)
        painter.restore()

        # 第二段：两行文字自己画。**必须重新 save**，不能接着上一段画 ——
        # 样式画完 section 之后 QPainter 的状态（裁剪区 / 画笔 / 字体）不一定
        # 还是进来时那份，紧跟着 drawText 会被整个吃掉，表头看着一个字没有。
        text = self.model().headerData(index, Qt.Horizontal, Qt.DisplayRole)
        if not text:
            return
        painter.save()
        painter.setFont(self._f)
        # 和主题里 QHeaderView::section 的文字色一致
        painter.setPen(QColor("#6b8299"))
        painter.drawText(rect.adjusted(1, 2, -1, -2),
                         Qt.AlignHCenter | Qt.AlignVCenter | Qt.TextWordWrap,
                         str(text))
        painter.restore()


# 还没算出来的格子。用「—」而不是留白：留白看不出是"没值"还是"程序漏了"。
POINT_DASH = "—"


class AutoPage(QWidget):
    """自动测试页。目前：项目卡 + 画面（画布/引脚编辑条）+ 运行日志。"""

    def __init__(self, mod, manager, parent=None):
        super(AutoPage, self).__init__(parent)
        self.mod = mod
        self.mgr = manager
        self.reg = Registry(manager.cfg)
        self.proj = None                     # 读进来的项目对象
        self.leds = {}
        self.dev_labels = {}
        self._dev_prev = {}                  # 上一轮设备状态，用来"变了才记日志"
        self._log_n = 0
        self._inited = False
        self._estop_on = False               # 急停是否已按下（按了要上锁）
        self._log_panel = None               # _build_log 里建，resizeEvent 里调高矮
        self._points = []                    # 点位表的行（按几何 S 型排好）
        self._points_src = ("", None)        # 上次用的 pins.json 与时间戳，防重复解析
        self._points_state = ""              # 上次的表状态，没变就不重算、不刷日志
        self._src_path = ""                  # 上次用的 pins.json（视觉启动会直接传）
        self._col_need = [0] * len(POINT_COLUMNS)   # 每列当前最长数据宽，_fit_cols 用
        self._fitting = False                # 列宽重排防重入

        # ---- 视觉启动这条流程（细节在 app/vision/session.py）
        self._engine_obj = None              # PinEngine：第一次拍才建（拉 torch 要几秒）
        self._sess = None                    # VisionSession
        self._img = None                     # 当前照片的 BGR（重绘 / 重跑要用）
        self._boxes = []                     # YOLO 检出的矩形框
        self._chips = None                   # 引脚结果；None = 还没识别（编辑条会被拦）
        self._photo = ""                     # 照片路径
        self._stem = ""                      # 本次会话的图名（json 用它命名）
        self._json_dir = ""                  # <会话>\引脚json
        self._json_path = ""                 # 上次写出的 pins.json
        self._saved = False                  # 是否已经点过【保存】
        self._prog_on = False                # 画布上的进度浮层是否显示着

        # ---- 画布模式：识别图 / 相机实时（2026-11-09 用户要的【切换】）
        # 规则是用户定的：**保存之前不能切**。保存 = 人工确认完毕，那时才允许
        # 看实时画面；保存后自动切过去，点【切换】再回到识别图。
        self._live_ready = False             # 保存过 → 【切换】才可用
        self._live = False                   # 画布现在放的是实时画面（不是照片）
        self._live_img = None                # 最新一帧（QImage，画布刷新用）
        self._live_cam = None                # 本页拿到的相机驱动
        self._live_mine = False              # 流是本页开的 → 离开时只关自己开的
        # 3072×2048 @30fps 逐帧转 QPixmap 是几百 MB/s 的搬运，界面会卡。
        # 最多 90 ms 刷一次，中间只留最后一帧（跟视觉定位页同一套做法）。
        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.timeout.connect(self._flush_live)

        self.setStyleSheet(theme.controls_qss())

        root = QVBoxLayout(self)
        root.setContentsMargins(18, HEAD_H, 18, 18)
        root.setSpacing(9)

        # 先建日志，再建画布：引脚编辑条构造时就会把 _say 挂上，万一它开口
        # 说话，_say 要写的那个控件得已经存在（_log 里还有 hasattr 兜底）。
        logw = self._build_log()

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)
        body.addWidget(self._build_left(), 0)
        body.addWidget(self._build_flow(), 1)

        root.addLayout(body, 1)
        root.addWidget(logw)

        # 设备状态搬到页头右侧（用户指定的位置）。它是页面自己的子控件，
        # 位置在 _place_devbar 里按页头标题行的基线算 —— 页头是自绘的，
        # 没法用布局摆，只能算坐标。
        self._devbar = self._build_devbar()
        self._place_devbar()

        self.mgr.device_changed.connect(self._on_device_changed)
        self._refresh_devices(log=False)
        self._show_project(None)
        self._load_points()                  # 先把"还没选项目"那句话摆上去

    # ================================================================ 界面
    def _build_left(self):
        """左栏：上面项目卡（打开项目 / 设备状态 / 三个按钮），下面画面。

        画布固定 450×300 之后，一列里"卡片 133 + 画布面板 358 + 日志 84"
        只要 651 px，752 px 的页面放得下，卡片就留在左上（用户最早要的
        位置）。画布再高（比如 674×450）卡片就得挪到右栏腾地方。
        """
        left = QWidget()
        left.setFixedWidth(LEFT_W)
        v = QVBoxLayout(left)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(9)
        v.addWidget(self._build_project_box(), 0)
        v.addWidget(self._build_view(), 0)
        v.addStretch(1)
        return left

    def _build_project_box(self):
        """左栏上面那块：项目 + 设备状态 + 三个操作。"""
        gb = QGroupBox("项目")
        gb.setObjectName("gbox")
        # 第三行四颗按钮 104+84+104+92 + 三个 8 px 间隔 = 408，加左右内边距
        # 24 和边框 2 = 434 → 360 只是下限，实际由左栏 466 撑开
        gb.setMinimumWidth(360)
        v = QVBoxLayout(gb)
        # 下边距 10 + 行距 10（原来是 12 / 12）：第二行从 16 px 的文字行换成
        # 22 px 的下拉框 / 输入框，多出的 6 px 在这两处抵掉，卡片总高仍是 136 ——
        # 画布 450×300 一点没变。
        v.setContentsMargins(12, 6, 12, 10)
        v.setSpacing(10)

        # ---- 第一行：打开项目 + 项目名称
        r1 = QHBoxLayout()
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(10)

        self.btn_open = QPushButton("  打开项目")
        self.btn_open.setObjectName("act")
        self.btn_open.setFixedSize(104, 30)
        self.btn_open.setCursor(Qt.PointingHandCursor)
        self.btn_open.setIcon(QIcon(icons.icon(
            "test", theme.TXT2, 12, self.devicePixelRatioF())))
        self.btn_open.setIconSize(QSize(12, 12))
        self.btn_open.setToolTip("下拉选择功能模块 3（测试管理）里保存过的项目")
        self.btn_open.clicked.connect(self._on_open)
        r1.addWidget(self.btn_open)

        self.lb_proj = QLabel("未选择项目")
        self.lb_proj.setFont(_font(theme.F_BODY, 14, bold=True, spacing=0.5))
        self.lb_proj.setStyleSheet(
            "color:%s;background:transparent;" % theme.TXT3)
        self.lb_proj.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.lb_proj.setToolTip("还没选项目：点左边的【打开项目】")
        r1.addWidget(self.lb_proj, 1)

        # ---- 第一行右侧：板号（人工输入，回车保存）
        # 位置就按用户画的那一块：项目名右边的空白处。**不另起一行**：
        # 左栏高度已经卡死 —— 画布固定 450×300，卡片再高 16px 就要把画布压下去，
        # 而"其他区域大小不能变"，所以只能横着放进这一行剩下的一段。
        lb_board = QLabel("板号")
        lb_board.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        lb_board.setStyleSheet("color:%s;background:transparent;" % theme.TXT2)
        lb_board.setToolTip("人工输入板号，按回车保存（记在项目里，下次打开还在）。\n"
                            "本次测试的文件夹就用板号命名。")
        r1.addWidget(lb_board)

        self.ed_board = QLineEdit()
        self.ed_board.setFixedSize(150, 26)
        self.ed_board.setMaxLength(project.BOARD_MAX)
        self.ed_board.setFont(_font(theme.F_BODY, 10))
        self.ed_board.setPlaceholderText("输入后回车")
        self.ed_board.setEnabled(False)
        self.ed_board.setToolTip("先【打开项目】才能填板号 —— 板号是跟着项目存的")
        self.ed_board.returnPressed.connect(self._on_board)
        r1.addWidget(self.ed_board)
        v.addLayout(r1)

        # ---- 第二行：检修状态（下拉）+ 任务号（自动算，只读）
        # 设备状态那三个点已经搬到页头右侧（用户指定的位置），这一行腾出来放
        # 检修状态和任务号 —— 两者都是"这一次测试"的标注，跟板号并列最顺手。
        # 高度账：这一行原来是 16 px 的文字行，现在换成 22 px 的控件（+6）；
        # 卡片各行间距从 12 收到 10（三处共 -6），卡片总高仍是 136 ——
        # 画布 450×300 一寸没动，和"不改变其他区域大小"一致。
        r2 = QHBoxLayout()
        r2.setContentsMargins(2, 0, 0, 0)
        r2.setSpacing(8)

        lb_fix = QLabel("检修状态")
        lb_fix.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        lb_fix.setStyleSheet("color:%s;background:transparent;" % theme.TXT2)
        lb_fix.setToolTip("这次测的是修前还是修后。同一块板返修前后各测一遍，\n"
                          "结果靠这一项区分（会写进 引脚json）。")
        r2.addWidget(lb_fix)

        self.cb_repair = QComboBox()
        self.cb_repair.addItems(project.REPAIRS)
        self.cb_repair.setFixedSize(80, 22)
        self.cb_repair.setFont(_font(theme.F_BODY, 10))
        self.cb_repair.setCursor(Qt.PointingHandCursor)
        self.cb_repair.setEnabled(False)
        self.cb_repair.setToolTip("先【打开项目】才能选 —— 检修状态跟着项目存")
        self.cb_repair.currentIndexChanged.connect(self._on_repair)
        r2.addWidget(self.cb_repair)

        # 这套深色 QSS 里 QComboBox::drop-down 只定了宽度、没定箭头，Qt 画出来
        # 的默认箭头是深色的（在深底上等于看不见）。全局改会影响别的页，所以
        # 只在这个框上叠一个字面 ▾（跟【打开项目 ▾】用的是同一个字符）——
        # 它对鼠标透明，点上去照样展开下拉。
        self._cb_arrow = QLabel("\u25be", self.cb_repair)
        self._cb_arrow.setFont(_font(theme.F_BODY, 8))
        self._cb_arrow.setStyleSheet(
            "color:%s;background:transparent;" % theme.TXT3)
        self._cb_arrow.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._cb_arrow.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._cb_arrow.setGeometry(0, 0, 74, 22)

        lb_task = QLabel("任务号")
        lb_task.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        lb_task.setStyleSheet("color:%s;background:transparent;" % theme.TXT2)
        lb_task.setToolTip("任务号 = 本次测试目录名（板号.检修状态.序号），自动算出来，不能改。")
        r2.addWidget(lb_task)

        # 任务号是**算出来的**（用户 2026-11-09 定）：板号 + 检修状态定下来，
        # 去保存地址里数同板号同状态已有几次，填"最大序号 + 1"。
        # 所以这个框只读 —— 能手改就会出现"界面写 5、目录建 6"的对不上。
        # 只读仍然可以选中 / 复制，排查时有用。
        # 宽度 244：这一行算过账 —— 内边距 2 + 检修状态 41 + 间距 8 + 下拉 80 +
        # 间距 8 + 任务号 31 + 间距 8 + 244 = 422，卡片可用 440 还余 18。
        # 常见板号（`PCB-2026-1109-03.修前.5`）量出来 210 px，244 装得下；
        # 再长的板号会滚动，完整路径在 tooltip 里。
        self.ed_task = QLineEdit()
        self.ed_task.setFixedSize(244, 22)
        self.ed_task.setFont(_font(theme.F_BODY, 10))
        self.ed_task.setReadOnly(True)
        self.ed_task.setPlaceholderText("填板号后自动生成")
        self.ed_task.setEnabled(False)
        self.ed_task.setToolTip("先【打开项目】才能算任务号 —— 任务号按项目保存地址里的目录算")
        r2.addWidget(self.ed_task)
        r2.addStretch(1)
        v.addLayout(r2)

        # ---- 第三行：三个业务操作 + 急停（急停在最右，和它们隔开一段）
        # 宽度账：104+84+104+92 = 384，三段 8 px 间隔 24，共 408；
        # 卡片可用 440（左栏 466 - 左右内边距 24 - 边框 2），塞得下。
        r3 = QHBoxLayout()
        r3.setContentsMargins(0, 0, 0, 0)
        r3.setSpacing(8)

        self.btn_vision = QPushButton("  视觉启动")
        self.btn_vision.setObjectName("primary")
        self.btn_vision.setFixedSize(104, 32)
        self.btn_vision.setCursor(Qt.PointingHandCursor)
        self.btn_vision.setIcon(QIcon(icons.icon(
            "vision", theme.AMBER2, 13, self.devicePixelRatioF())))
        self.btn_vision.setIconSize(QSize(13, 13))
        self.btn_vision.setToolTip("视觉启动：建本次测试目录 → 3 秒后自动拍照 →\n"
                                   "YOLO 识别矩形框 → 引脚识别（进度显示在画布上）")
        self.btn_vision.clicked.connect(self._on_vision)
        r3.addWidget(self.btn_vision)

        self.btn_save = QPushButton("  保存")
        self.btn_save.setFixedSize(84, 32)
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.setStyleSheet(SAVE_QSS)
        self.btn_save.setIcon(QIcon(icons.icon(
            "save", theme.TXT2, 13, self.devicePixelRatioF())))
        self.btn_save.setIconSize(QSize(13, 13))
        self.btn_save.setToolTip("人工检查过矩形框 / 引脚之后点它：\n"
                                 "把引脚 + 矩形框坐标写进「引脚json」，\n"
                                 "再按项目选的程序展开成右栏点位表")
        self.btn_save.clicked.connect(self._on_save)
        r3.addWidget(self.btn_save)

        self.btn_run = QPushButton("  自动运行")
        self.btn_run.setFixedSize(104, 32)
        self.btn_run.setCursor(Qt.PointingHandCursor)
        self.btn_run.setStyleSheet(RUN_QSS)
        self.btn_run.setIcon(QIcon(icons.icon(
            "auto", theme.OK, 13, self.devicePixelRatioF())))
        self.btn_run.setIconSize(QSize(13, 13))
        self.btn_run.setToolTip("按流程整板自动测试（功能待接）")
        self.btn_run.clicked.connect(lambda: self._todo("自动运行"))
        r3.addWidget(self.btn_run)

        # 急停：和上面三个按钮隔开（stretch 吃掉剩下的 32 px），出事时不会点错。
        # 样式来自主题的 QPushButton#estop；永远不 setEnabled(False)。
        r3.addStretch(1)
        self.btn_estop = QPushButton("■ 急停")
        self.btn_estop.setObjectName("estop")
        self.btn_estop.setFixedSize(92, 32)
        self.btn_estop.setCursor(Qt.PointingHandCursor)
        self.btn_estop.setToolTip(ESTOP_TIP)
        self.btn_estop.clicked.connect(self._on_estop)
        r3.addWidget(self.btn_estop)
        v.addLayout(r3)

        v.addStretch(1)
        self._card = gb                    # _fit_canvas 算可用高度要用
        return gb

    # ------------------------------------------------------------------ 页头
    def _build_devbar(self):
        """页头右侧那条设备状态：● 三轴控制器　● 探针座　● 工业相机。

        为什么放在页头而不是项目卡里：用户要把项目卡那一行腾出来给检修状态和
        任务号。页头左边是「03 自动测试 + 一句说明」，右边本来就空着，三个
        状态点摆过去正好，既不挤内容，看的时候也和整页状态在同一水平线上。

        它是**页面自己的子控件**（不是布局项）：页头是 paintEvent 自绘的，
        内容区从 HEAD_H 往下排，页头那块没有布局可用，所以位置由
        `_place_devbar()` 按标题行基线现算，resizeEvent 里跟着重算。
        """
        bar = QWidget(self)
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(16)
        for key in DEV_KEYS:
            box = QHBoxLayout()
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(6)
            led = Led(theme.BAD, 7)
            lb = QLabel(DEVICE_META[key]["name"])
            lb.setFont(_font(theme.F_BODY, 11, spacing=0.3))
            lb.setStyleSheet("color:%s;background:transparent;" % theme.TXT2)
            box.addWidget(led)
            box.addWidget(lb)
            h.addLayout(box)
            self.leds[key] = led
            self.dev_labels[key] = lb
        bar.adjustSize()
        bar.raise_()
        return bar

    def _header_line_y(self):
        """页头那条底线的 y（跟 paint_module_head 里的算法一致）。"""
        fm = QFontMetrics(_font(theme.F_DISP, 34, bold=True, spacing=1.2))
        return HEAD_TOP + fm.height() + 12

    def _title_center_y(self):
        """标题文字「自动测试」那一行的竖直中心。

        页头里编号是 34 pt、标题是 20 pt，两者坐在同一条基线上；
        设备条要和**标题**对齐（不是和编号，也不是和底线），所以算的是
        20 pt 那行的中心：基线 - (ascent - descent) / 2。
        """
        base = HEAD_TOP + QFontMetrics(
            _font(theme.F_DISP, 34, bold=True, spacing=1.2)).ascent()
        fm = QFontMetrics(_font(theme.F_BODY, 20, bold=True, spacing=1.2))
        return base - (fm.ascent() - fm.descent()) / 2.0

    def _devbar_w(self):
        """页头设备条占多宽（含右边距）。绘制标题时要避开这一段。"""
        bar = getattr(self, "_devbar", None)
        if bar is None or not bar.isVisible():
            return 0
        return bar.sizeHint().width() + 14

    def _place_devbar(self):
        """把设备条贴到页头右侧、和标题文字同一行。"""
        bar = getattr(self, "_devbar", None)
        if bar is None:
            return
        bar.adjustSize()
        w, h = bar.sizeHint().width(), bar.sizeHint().height()
        # 竖直方向压在标题那一行的中线上：和左侧「自动测试」看起来是一行
        top = max(HEAD_TOP, int(round(self._title_center_y() - h / 2.0)))
        bar.setGeometry(self.width() - 18 - w, top, w, h)

    def _build_view(self):
        """画面区：把「视觉定位」的画布 + 引脚编辑条搬过来，尺寸固定 450×300。

        用的就是视觉定位页那两个控件，不再另抄一份；画布本身按比例
        fit 图像，所以这一格里放 3:2 的板图不会变形。
        """
        panel = Panel()
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        self.view = InteractiveCanvas(CANVAS_HINT)
        self.view.setFixedSize(CANVAS_W, CANVAS_H)
        self.view.setToolTip("相机画面 / 拍到的板图显示在这里；\n"
                             "缩放平移同「视觉定位」页")
        v.addWidget(self.view, 0)

        # 【切换】：识别图 ↔ 相机实时。保存成功才有意义，所以一开始是灰的。
        # 样式取编辑条那一份（BTN_QSS），跟旁边按钮看起来是同一套；
        # 宽度 46 = 「选择/撤回」的宽度，塞在「召回率」后面刚好铺满 450。
        self.btn_switch = QPushButton("切换")
        self.btn_switch.setFixedSize(46, 25)
        self.btn_switch.setStyleSheet(BTN_QSS)
        self.btn_switch.setCursor(Qt.PointingHandCursor)
        self.btn_switch.setEnabled(False)
        self.btn_switch.setToolTip("先【保存】识别结果：保存之后才能看相机实时画面")
        self.btn_switch.clicked.connect(self._toggle_live)

        # 引脚编辑条：逻辑、按钮、快捷键都跟视觉定位页同一份代码。
        # compact=True 去掉标题和召回率读数 —— 画布只有 450 宽，完整那条要 503。
        # draw_chip：人工新画一个框后，只重跑这一颗把引脚补上（跟视觉定位页一样）。
        # extra：把【切换】交给它排进同一行末尾（视觉定位页不传，样子不变）。
        self.kit = PinEditKit(self.view, self._pin_get, self._pin_apply,
                              self._pin_persist, self._say,
                              iter_all=self._pin_iter, draw_chip=self._draw_chip,
                              parent=self, horizontal=True, compact=True,
                              extra=[self.btn_switch])
        v.addWidget(self.kit.toolbar(), 0)
        return panel

    def _build_flow(self):
        """右栏：14 列点位表（就是「运行点位」那张表，行是按程序算出来的）。

        **表是常驻的**：一进这一页就看得见 14 个列头，没数据就是 0 行 ——
        用户要的是"打开就能看到流程长什么样"，不是先看一句提示再点按钮。
        所以这里没有空态占位、没有【载入】按钮：数据只从项目自己的
        `*_pins.json` 来（视觉启动接上后自动产生），缺什么记进日志。
        """
        panel = Panel()
        v = QVBoxLayout(panel)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(8)

        head = QHBoxLayout()
        head.setContentsMargins(2, 0, 2, 0)
        head.setSpacing(8)
        t = QLabel("测试流程")
        t.setFont(_font(theme.F_BODY, 12, bold=True, spacing=0.4))
        t.setStyleSheet("color:%s;background:transparent;" % theme.TXT)
        head.addWidget(t)

        self.lb_points_n = QLabel("0 行")
        self.lb_points_n.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        self.lb_points_n.setStyleSheet("color:%s;background:transparent;"
                                       % theme.TXT3)
        # 这行字随行数变长（"1234 行 · 89 框"），别让它把页面的最小宽度
        # 一点点顶上去 —— 右栏宽度本来就紧张
        self.lb_points_n.setSizePolicy(QSizePolicy.Ignored,
                                       QSizePolicy.Preferred)
        self.lb_points_n.setMinimumWidth(60)
        head.addWidget(self.lb_points_n)
        head.addStretch(1)
        v.addLayout(head)

        self.tb_points = QTableWidget(0, len(POINT_COLUMNS))
        self.tb_points.setHorizontalHeader(WrapHeader(self.tb_points))
        self.tb_points.setHorizontalHeaderLabels([c[0] for c in POINT_COLUMNS])
        # 行号列白占 38 px（用户圈的黄框就是它，左上角还留一块空格），
        # 这张表一行就是一个引脚，序号没有意义 —— 直接不要。
        self.tb_points.verticalHeader().setVisible(False)
        # 行高仍按 26 走（表头藏了，得自己给行高，不然回落到默认的 30）
        self.tb_points.verticalHeader().setDefaultSectionSize(26)
        self.tb_points.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tb_points.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tb_points.setAlternatingRowColors(True)
        self.tb_points.setWordWrap(False)
        self.tb_points.setCornerButtonEnabled(False)
        # 点位表是跑出来的结果，不是给人改的表：只读
        self.tb_points.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tb_points.setFocusPolicy(Qt.StrongFocus)
        hd = self.tb_points.horizontalHeader()
        hd.setStretchLastSection(False)
        hd.setSectionResizeMode(QHeaderView.Fixed)
        for i, (_name, tip, _wd) in enumerate(POINT_COLUMNS):
            it = self.tb_points.horizontalHeaderItem(i)
            if it is not None:
                it.setToolTip(tip)
        # 14 列按可用宽度等比铺满（见 _fit_cols）；别让列宽反过来把整页的
        # 最小宽度顶上去（窗口缩到 1100 时右栏只剩四百来像素）
        self.tb_points.setMinimumWidth(0)
        self.tb_points.setSizePolicy(QSizePolicy.Expanding,
                                     QSizePolicy.Expanding)
        # 列宽跟着**视口**走：滚动条一出现视口就变窄，页面 resizeEvent 那会儿
        # 视口还是旧宽度，算出来的列宽会差一条滚动条。
        self.tb_points.viewport().installEventFilter(self)
        # 14 列挤在一起，字号跟表头一致（9 px）才放得下；再大就滚了
        self.tb_points.setFont(_font(theme.F_BODY, 9))
        v.addWidget(self.tb_points, 1)
        return panel

    def _build_log(self):
        """页面最下面那条：整页共用的运行日志（带时间戳）。

        不再配【复制】【清空】按钮 —— 复制用选中后 Ctrl+C（QPlainTextEdit
        自带），清空没必要（最多留 LOG_MAX 行，老的自己会掉）。
        """
        panel = Panel()
        panel.setMinimumHeight(LOG_H_MIN)
        self._log_panel = panel            # resizeEvent 里按窗口高度调它
        v = QVBoxLayout(panel)
        v.setContentsMargins(12, 6, 12, 10)
        v.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(2, 0, 2, 0)
        head.setSpacing(8)
        t = QLabel("运行日志")
        t.setFont(_font(theme.F_BODY, 12, bold=True, spacing=0.4))
        t.setStyleSheet("color:%s;background:transparent;" % theme.TXT)
        head.addWidget(t)

        self.lb_log_n = QLabel("0 条")
        self.lb_log_n.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        self.lb_log_n.setStyleSheet("color:%s;background:transparent;"
                                    % theme.TXT3)
        head.addWidget(self.lb_log_n)

        tip = QLabel("连接、项目读取、按钮操作都记在这里")
        tip.setFont(_font(theme.F_BODY, 9, spacing=0.2))
        tip.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        head.addWidget(tip)
        head.addStretch(1)
        v.addLayout(head)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(LOG_MAX)
        self.log.setFont(_font(theme.F_MONO, 11))
        self.log.setStyleSheet(LOG_QSS)
        self.log.setToolTip("只读。要复制某几行就选中后按 Ctrl+C")
        v.addWidget(self.log, 1)
        return panel

    # ================================================================ 日志
    def _log(self, text, level="info"):
        """往日志里追一行：时间 + 级别 + 正文。"""
        if not hasattr(self, "log"):
            return
        word, col = LOG_LEVELS.get(level, LOG_LEVELS["info"])
        body = theme.TXT2 if level in ("info", "ok") else col
        self.log.appendHtml(
            '<span style="color:%s">%s</span>&nbsp;&nbsp;'
            '<span style="color:%s">%s</span>&nbsp;&nbsp;'
            '<span style="color:%s">%s</span>'
            % (theme.TXT3, time.strftime("%H:%M:%S"), col, word, body,
               html.escape(str(text))))
        self._log_n += 1
        self.lb_log_n.setText("%d 条" % self._log_n)

    # ================================================================ 项目
    def _on_open(self):
        """下拉列出测试管理里保存过的项目。"""
        try:
            self.reg = Registry(self.mgr.cfg)      # 可能刚在模块 2 建过项目
            if self.reg.is_empty():
                self._say("还没有保存过的项目：先去「测试管理」新建一个并点【保存项目】。",
                          "warn")
                return
            m = QMenu(self)
            m.setStyleSheet(MENU_QSS)
            for p in self.reg.projects:
                text = p.name if p.root_ok else "%s　（保存地址不存在）" % p.name
                act = m.addAction(text)
                act.setData(p.name)
                act.setToolTip(p.root or "未设置保存地址")
            pos = self.btn_open.mapToGlobal(
                QPoint(0, self.btn_open.height() + 4))
            act = m.exec_(pos)
            if act is not None:
                self._load(act.data())
        except Exception as e:
            traceback.print_exc()
            self._say("打开项目出错：%r" % e, "err")

    def _load(self, name):
        """选中项目：把对象读进来，顺手把"缺什么"说出来。"""
        p = self.reg.find(name)
        if p is None:
            self._say("项目清单里找不到「%s」，可能刚在测试管理里删掉了。" % name, "err")
            return
        if self._sess is not None and self._sess.busy:
            self._sess.cancel()             # 换项目了，旧项目的识别结果不该落到新项目里
        self._reset_result()
        self.proj = p
        self._show_project(p)
        self._say("已打开项目 %s　[%s]" % (p.name, self._brief(p)), "ok")
        self._load_points(force=True)        # 换了项目，点位表跟着重算

    def _show_project(self, p):
        """项目名那一行：有项目就亮起来，没有就灰着提示。板号跟着项目一起换。"""
        if p is None:
            self.lb_proj.setText("未选择项目")
            self.lb_proj.setStyleSheet(
                "color:%s;background:transparent;" % theme.TXT3)
            self.lb_proj.setToolTip("还没选项目：点左边的【打开项目】")
            # 没项目就没有"存在哪儿"这回事，三个输入跟着灰掉并清空
            self.ed_board.clear()
            self.ed_board.setEnabled(False)
            self.ed_board.setToolTip("先【打开项目】才能填板号 —— 板号是跟着项目存的")
            self.cb_repair.blockSignals(True)
            self.cb_repair.setCurrentIndex(0)
            self.cb_repair.blockSignals(False)
            self.cb_repair.setEnabled(False)
            self.cb_repair.setToolTip("先【打开项目】才能选 —— 检修状态跟着项目存")
            self.ed_task.clear()
            self.ed_task.setEnabled(False)
            self.ed_task.setToolTip("先【打开项目】才能算任务号 —— 任务号按项目保存地址里的目录算")
            return
        self.lb_proj.setText(p.name)
        self.lb_proj.setStyleSheet(
            "color:%s;background:transparent;" % theme.TXT)
        self.lb_proj.setToolTip("%s\n保存地址：%s" % (p.name, p.root or "未设置"))
        self.ed_board.setEnabled(True)
        self.ed_board.setText(p.board or "")
        self._board_tip(p)

        # 检修状态 / 任务号：检修状态同板号一样是项目的属性，打开项目就回填；
        # 任务号不存也不用回填 —— 它是**按地址里现有目录现算**的，走 _refresh_task。
        # blockSignals：这里是"把存的值显示出来"，不是用户改的，
        # 不能顺手触发一次保存（换项目时会把旧值写进新项目）。
        self.cb_repair.blockSignals(True)
        i = self.cb_repair.findText(p.repair)
        self.cb_repair.setCurrentIndex(i if i >= 0 else 0)
        self.cb_repair.blockSignals(False)
        self.cb_repair.setEnabled(True)
        self.cb_repair.setToolTip("这次测的是修前还是修后（存进项目，也进目录名）")
        self.ed_task.setEnabled(True)
        self._refresh_task()

    def _board_tip(self, p):
        """板号框的提示语：直接写出"这一次目录会叫什么"，省得人去猜命名规则。"""
        if p is None:
            return
        if project.safe_board(p.board):
            tip = ("板号（已存进项目）：%s\n"
                   "本次测试目录：%s\\" % (p.board, p.next_name()))
            self.ed_board.setToolTip(tip)
        else:
            self.ed_board.setToolTip(
                "还没填板号：目录按「板号.检修状态.序号」命名，\n"
                "填好板号后回车保存 —— 没板号不能开跑。")

    def _refresh_task(self):
        """算任务号：同板号同状态下已有的**最大序号 + 1**，写成完整目录名。

        三个地方会调它 —— 打开项目、板号回车、改检修状态。它只读、不落盘：
        任务号是"这一算的结果"，不是人填的值，存下来只会和真实目录对不上。
        开跑时 `ensure_session()` 会再算一次（界面显示 5、期间别处先建了 5，
        就顺延到 6），所以这里差一个号也不会撞车。
        """
        if self.proj is None:
            self.ed_task.clear()
            return
        name = self.proj.next_name()
        # 号从**名字本身**取，不再另算一次 task_no：名字可能已经跳过一个被占用的
        # 号（有人手建了同名目录），两次各算一遍就会对不上。
        got = project.parse_session_name(name) if name else None
        self.ed_task.setText(name)
        self.ed_task.setCursorPosition(0)   # 板号长时显示开头，别只露出最后一个数字
        if got:
            n = got[2]
            self.ed_task.setToolTip(
                "任务号 = 本次测试目录名（按已有目录自动算，不能改）\n"
                "%s\n同板号同状态已有 %d 次，这次是第 %d 次。"
                % (os.path.join(self.proj.root or "", name), n - 1, n))
            self.proj.task = name           # 内存里同步一份（pins.json 里用它）
        else:
            self.ed_task.setToolTip(
                "还没填板号：目录按「板号.检修状态.序号」命名，\n"
                "填好板号回车后这里自动出现。")

    def _brief(self, p):
        """一句话摘要：读到了什么、缺什么。日志和 tooltip 都用它。"""
        bits = []
        if not p.root_ok:
            bits.append("保存地址不可用")
        if p.active:
            item = program.find_program(program.open_library(self.mgr.cfg)[0],
                                        p.active) or {}
            n = len(item.get("rows") or [])
            bits.append("程序 %s（%d 行）" % (p.active, n))
        else:
            bits.append("还没选测试程序")
        if not p.yolo:
            bits.append("yolo 模型未选")
        elif not p.yolo_ok:
            bits.append("yolo 模型文件不在了")
        else:
            bits.append("yolo %s" % os.path.basename(p.yolo))
        bits.append("X 粗定位 %s" % ("未设" if p.x_coarse is None
                                     else "%g mm" % p.x_coarse))
        return "　".join(bits)

    # ================================================================ 板号
    def _on_board(self):
        """板号输入框**回车** = 保存。

        为什么是回车而不是又加一颗【保存板号】按钮：这一行本来就挤（画布固定
        450 宽，左边栏没有多余宽度），多一颗按钮就要再从项目名那里抢地方；
        而"填完就回车"是输入框的通用习惯，日志会说清楚存没存。
        """
        if self.proj is None:
            self._say("还没打开项目：板号跟着项目存，先【打开项目】。", "warn")
            return
        raw = self.ed_board.text()
        val = project.safe_board(raw)
        if not val:
            # 板号是目录名的第一段，空着就没有目录名 —— 不像以前那样退回时间命名，
            # 所以这里不允许留空：把旧值放回去，说清楚为什么。
            self.ed_board.setText(self.proj.board)
            self._say("板号不能空着：目录按「板号.检修状态.序号」命名，"
                      "先填板号再开跑。", "warn")
            self.ed_board.setFocus()
            return
        if val != raw:
            # 非法字符被换成下划线了 —— 回显出来，让人看得见到底存成了什么
            self.ed_board.setText(val)
        if self.proj.board == val:
            self._board_tip(self.proj)
            self._refresh_task()
            self._say("板号没变：%s" % val, "info")
            return

        self.proj.board = val
        try:
            self.reg.put(self.proj)
            ok, msg = self.reg.save()
        except Exception as e:
            traceback.print_exc()
            self._say("板号没能存进项目配置：%r" % e, "err")
            return
        self._board_tip(self.proj)
        self._refresh_task()
        if not ok:
            self._say("板号存不进 data\\devices.json：%s（这次能用，下次打开就没了）"
                      % msg, "err")
            return
        name = self.proj.next_name()
        got = project.parse_session_name(name) if name else None
        self._say("板号已保存：%s　本次测试目录：%s\\" % (val, name), "ok")
        if got and got[2] > 1:
            n = got[2]
            self._say("　这个板号 + 检修状态「%s」下已经有 %d 次，本次是第 %d 次"
                      "（序号不复用，删掉的旧号也不会补）。"
                      % (self.proj.repair, n - 1, n), "info")
        if not self.proj.root_ok:
            self._say("　注意：保存地址现在打不开，跑之前确认一下。", "warn")

    # ------------------------------------------------ 检修状态 / 任务号
    def _on_repair(self, _i=None):
        """下拉框换值 = 立即保存（没有"确定"按钮，选了就是选了）。

        检修状态是目录名的第二段，所以换完要重算任务号 —— 修前修后各自计数，
        从修前切到修后，任务号会变成修后那条线的下一个号。
        """
        if self.proj is None:
            return
        val = self.cb_repair.currentText() or project.REPAIRS[0]
        if self.proj.repair == val:
            return                          # 显示和存的一样，不重复落盘
        self.proj.repair = val
        try:
            self.reg.put(self.proj)
            ok, msg = self.reg.save()
        except Exception as e:
            traceback.print_exc()
            self._say("检修状态没能存进项目配置：%r" % e, "err")
            self._refresh_task()            # 存不下也要把界面算对，别留着旧号
            return
        self._refresh_task()
        name = self.proj.next_name()
        if ok:
            self._say("检修状态：%s（已存进项目）　本次测试目录：%s\\"
                      % (val, name), "ok")
        else:
            self._say("检修状态存不进 data\\devices.json：%s（这次能用，下次打开就没了）"
                      % msg, "err")

    # ================================================================ 点位表
    def _find_pins(self):
        """项目保存地址里最新的 *_pins.json。

        视觉保存时按 `project.ensure_session()` 的约定落到
        `<项目>/<会话>/引脚json/<图名>_pins.json`，所以往下走三层找；
        同一块板可能识别过好几次，取**最新**那份 —— 表要反映手上这张图。
        """
        root = (self.proj.root if self.proj is not None else "") or ""
        if not root or not os.path.isdir(root):
            return ""
        best, best_mt = "", 0.0
        for base, dirs, files in os.walk(root):
            if os.path.relpath(base, root).count(os.sep) >= 3:
                dirs[:] = []                   # 再往深就不是本次测试的目录了
            for name in files:
                if not name.lower().endswith("_pins.json"):
                    continue
                p = os.path.join(base, name)
                try:
                    mt = os.path.getmtime(p)
                except OSError:
                    continue
                if mt > best_mt:
                    best, best_mt = p, mt
        return best

    def _load_points(self, path=None, force=False):
        """按程序规则展开点位表。path 空 → 自动找项目里最新的 pins.json。

        行是**算出来的**：程序（识别方式 / 点位范围 / 点名范围 / 点位 / 测试类别）
        去筛 pins.json 的引脚，和上一轮生成 xlsx 用的是同一套规则。所以程序一改、
        重新识别一次，这张表就跟着变，不会出现"表里 25 行、板子 30 个脚"。

        缺什么（没项目 / 没程序 / 没识别结果）**不摆提示占位** —— 14 个列头照样
        在，只是 0 行，原因记一行日志。状态没变时不重复解析，也不刷日志。
        """
        if path:
            self._src_path = path               # 显式指定的（视觉启动会这么传）
        if self.proj is None:
            state, why = "no-proj", ""
        elif not self.proj.active:
            state = "no-prog"
            why = "项目「%s」还没选测试程序，点位表先空着（去「测试管理」选一份）。" \
                  % self.proj.name
        else:
            found = self._find_pins()
            if found:
                self._src_path = found          # 项目里有新的就用新的
            elif self._src_path and os.path.isfile(self._src_path):
                found = self._src_path          # 项目里没有，沿用上次那份
            path = found
            state = path or "no-pins"
            why = "" if path else (
                "项目「%s」里还没有 *_pins.json，点位表先空着"
                "（视觉启动取图识别后会生成）。" % self.proj.name)

        if not force and state == self._points_state:
            return                              # 状态没变：不解析、不刷日志
        fresh = self._points_state == ""        # 首次进来别急着说"没项目"
        self._points_state = state

        if self.proj is None or not self.proj.active or not path:
            self._points = []
            self._points_src = ("", None)
            self._fill_points([])
            if why and not fresh:
                self._say(why, "warn")
            return

        try:
            stamp = (os.path.getmtime(path), os.path.getsize(path))
        except OSError:
            stamp = None
        if not force and self._points_src == (path, stamp):
            return                              # 同一份文件没变，不重复解析

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            chips = data.get("chips") or []
        except Exception as e:
            # 文件不在 / 没权限是常事（用户手误点了旧路径），不刷 traceback；
            # 真解析崩了才打，方便排查
            if not isinstance(e, (IOError, OSError)):
                traceback.print_exc()
            self._say("引脚结果读不出来：%s（%s）"
                      % (os.path.basename(path), e), "err")
            self._points = []
            self._fill_points([])
            return

        lib, _moved = program.open_library(self.mgr.cfg)
        item = program.find_program(lib, self.proj.active) or {}
        rows = item.get("rows") or []
        points, warns, absent = program.resolve(chips, rows)

        # 顺序按**最终图片的几何位置做 S 型**（2026-11-06 定）。自动运行是一颗
        # 一颗往下走的，顺序就等于空跑行程：按类别走会在类别之间来回大跳，
        # 按 json 数组走则完全看 YOLO 这一帧先检出谁（同一块板两次都不同）。
        # 规则表只决定"读不读 / 读哪些脚"，不决定先后；框内仍按引脚号升序 ——
        # 一颗芯片的引脚必须连续测完，不能为了蛇形把引脚打散。
        rank = program.snake_rank(chips)
        tail = len(rank)
        points.sort(key=lambda p: (rank.get(p["chip"], tail), p["idx"]))

        self._points = points
        self._points_src = (path, stamp)
        self._fill_points(points)
        self._report_points(points, rows, warns, absent,
                            os.path.basename(path))

    def _report_points(self, points, rows, warns, absent, filename):
        """把"展开了多少行、为什么少了几个"讲清楚，别让人对着表格猜。"""
        if not points:
            self._say("点位表是空的：程序里 %d 条规则和这张图对不上（%s）。"
                      % (len(rows), filename), "warn")
        else:
            # 按框在表里出现的先后列，跟表格的行走顺序一致，好对账
            per = {}
            ids = []
            for p in points:
                if p["chip"] not in per:
                    per[p["chip"]] = 0
                    ids.append(p["chip"])
                per[p["chip"]] += 1
            bits = ["%s %d" % (cid, per[cid]) for cid in ids[:8]]
            if len(ids) > 8:
                bits.append("等 %d 框" % len(ids))
            self._say("点位表已展开：%d 行 / %d 个框（%s），规则「%s」%d 行 ← %s"
                      % (len(points), len(per), "、".join(bits),
                         self.proj.active, len(rows), filename), "ok")
            self._say("　行走顺序按几何 S 型：从上到下分带，带内一左一右交替，"
                      "尽量少空跑。", "info")
        for n, msg in warns:
            self._say("　程序第 %d 行：%s" % (n, msg), "warn")
        if absent:
            self._say("　程序里配了、这张图上没有的框：%s（跳过）"
                      % "、".join(absent), "info")
        skipped = [r for r in rows if not program.is_read(r)]
        if skipped:
            self._say("　程序里有 %d 行是「不读取」，已跳过。" % len(skipped),
                      "info")

    def _fill_points(self, points):
        """填表。没点位就是 0 行 —— 表头一直在，不拿提示盖住它。"""
        n = len(points)
        self.lb_points_n.setText(
            "0 行" if not n else "%d 行 · %d 框"
            % (n, len(set(p["chip"] for p in points))))

        dash = QColor(theme.TXT3)
        body = QColor(theme.TXT2)
        fm = QFontMetrics(self.tb_points.font())
        need = [0] * len(POINT_COLUMNS)      # 这一列最长的那串字有多宽
        self.tb_points.setUpdatesEnabled(False)
        self.tb_points.setRowCount(n)
        for r, p in enumerate(points):
            cells = (
                (p["chip"], False),
                (str(p["idx"]), True),
                ("%.1f" % p["x"], True),
                ("%.1f" % p["y"], True),
            ) + tuple((POINT_DASH, True) for _ in range(8)) + (
                (p["category"], True),
                (POINT_DASH, True),
            )
            for c, (val, num) in enumerate(cells):
                it = QTableWidgetItem(val)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                it.setTextAlignment((Qt.AlignRight if num else Qt.AlignLeft)
                                    | Qt.AlignVCenter)
                if val == POINT_DASH:
                    it.setForeground(dash)
                else:
                    it.setForeground(body)
                    need[c] = max(need[c], fm.horizontalAdvance(val))
                self.tb_points.setItem(r, c, it)
        self._col_need = need
        self.tb_points.setUpdatesEnabled(True)
        self._fit_cols()               # 行的增减会带来/去掉纵向滚动条，宽度要重算

    # ================================================================ 设备
    def _on_device_changed(self, _key):
        self._refresh_devices()

    def _refresh_devices(self, log=True):
        """三个点跟设备管理同步：绿=已连接，红=未连接，琥珀=连接中。"""
        for key in DEV_KEYS:
            st = self.mgr.state(key)
            if st == ST_CONNECTED:
                col, word = theme.OK, "已连接"
            elif st == ST_CONNECTING:
                col, word = theme.WARN, "连接中"
            else:
                col, word = theme.BAD, "未连接"
            self.leds[key].set_color(col)
            self.dev_labels[key].setToolTip(
                "%s：%s（连接在「设备管理」里做）"
                % (DEVICE_META[key]["name"], word))
            # 状态真的变了才记一行，不然每刷一次就刷屏
            if log and self._dev_prev.get(key) != word:
                self._log("%s %s" % (DEVICE_META[key]["name"], word),
                          "ok" if word == "已连接" else
                          ("warn" if word == "连接中" else "info"))
            self._dev_prev[key] = word

    # ================================================================ 视觉启动
    def _ensure_engine(self):
        """识别引擎：第一次用才建。

        为什么不在构造时建：PinEngine 一建就起后台线程，真加载 torch + 模型要几秒、
        几百 MB —— 一进这一页就吃这些资源说不过去。等到真点【视觉启动】再建。
        """
        if self._engine_obj is None:
            self._engine_obj = veng.PinEngine(self)
            self._engine_obj.done.connect(self._on_engine_done)
        return self._engine_obj

    def _ensure_session(self):
        """视觉会话对象（一个页面一个，用来串起拍照→识别→进度）。"""
        if self._sess is None:
            self._sess = VisionSession(self._ensure_engine(), self)
            self._sess.stage.connect(self._on_flow_stage)
            self._sess.progress.connect(self._on_flow_progress)
            self._sess.photo.connect(self._on_flow_photo)
            self._sess.boxes.connect(self._on_flow_boxes)
            self._sess.chips.connect(self._on_flow_chips)
            self._sess.failed.connect(self._on_flow_failed)
            self._sess.finished.connect(self._on_flow_finished)
        return self._sess

    def _on_vision(self):
        """【视觉启动】：建目录 → 3 秒 → 拍照 → YOLO → 引脚识别。"""
        if self._estop_guard("视觉启动"):
            return
        sess = self._sess
        if sess is not None and sess.busy:
            self._say("视觉流程正在跑，等它出结果（急停可以中途打断）。", "warn")
            return
        if self.proj is None:
            self._say("还没打开项目：先【打开项目】—— 保存地址、yolo 模型都跟着项目走。",
                      "warn")
            return
        if not self.proj.root_ok:
            self._say("项目「%s」的保存地址不可用（%s）：去「测试管理」重新指一个。"
                      % (self.proj.name, self.proj.root or "未设置"), "err")
            return
        if not self.proj.yolo:
            self._say("项目没选 yolo 模型，这次用程序自带的 best.pt 识别。", "warn")
        if not self.proj.active:
            self._say("项目还没选测试程序：识别照样能跑，但【保存】之后填不出点位表。",
                      "warn")
        if not project.safe_board(self.proj.board):
            # 2026-11-09 起不再"没板号就按时间命名"：目录名必须凑齐三段，
            # 凑不齐就不产数据 —— 对不上号的图，跑出来也没法归档。
            self._say("还没填板号：本次目录按「板号.检修状态.序号」命名，"
                      "先在上面填板号并回车。", "warn")
            self.ed_board.setFocus()
            return

        self._reset_result()
        self._flow_busy(True)
        if not self._ensure_session().start(self.proj, self.mgr.driver("camera")):
            self._flow_busy(False)          # 建目录就失败了，原因 session 已经说过
            return
        # 目录一建好就把名字接下来：哪怕 YOLO 没检出框、用户手工画框再保存，
        # 也知道该往哪写（不能等到"识别成功"才记）。
        self._stem = self._sess.stem
        self._json_dir = self._sess.json_dir

    def _reset_result(self):
        """开拍前把上一轮的结果清空 —— 别让"新照片 + 旧框"同时出现在画布上。"""
        # 新一轮开始：退出实时画面、把流还回去，【切换】回到"保存前"的禁用态
        # （拍照那几秒画布要显示照片，不能被实时帧顶着刷）
        self._exit_live(restore=False)
        self._close_live_stream()
        self._live_ready = False
        self._refresh_switch()
        self._img = None
        self._boxes = []
        self._chips = None
        self._photo = ""
        self._json_path = ""
        self._saved = False
        self.view.set_image(None)
        self.view.set_boxes([])
        self.view.set_pins([])
        self.kit.reset_state()

    # ---------------------------------------------------------------- 流程回调
    def _on_flow_stage(self, text, level="info"):
        self._say(text, level)

    def _on_flow_progress(self, kind, pct, text):
        """画布左上角的进度浮层：倒计时 / 拍照 / YOLO / 引脚识别都走这里。"""
        if kind == "fail":
            self._prog_on = False
            self.view.hide_progress()
            return
        if not self._prog_on:
            self._prog_on = True
            self.view.show_progress(text, total=100)
        self.view.set_progress(int(pct), text=text, total=100)

    def _on_flow_photo(self, path, img):
        self._photo = path
        self._img = img
        self.view.set_tag_level(4)          # 450 宽看 3072 的图，编号给一点就够
        self.view.set_image(img)
        self.view.set_boxes([])
        self.view.set_pins([])
        self.kit.reset_state()

    def _on_flow_boxes(self, boxes, _sec=0.0):
        self._boxes = list(boxes or [])
        if self._img is None:
            return
        self.view.set_image(self._img)
        self.view.set_boxes([b.get("box") for b in self._boxes],
                            [b.get("id") for b in self._boxes])
        self.view.set_pins([])

    def _on_flow_chips(self, chips, _sec=0.0):
        self._chips = list(chips or [])
        if self._sess is not None:
            self._stem = self._sess.stem
            self._json_dir = self._sess.json_dir
        self._paint()
        self.kit.refresh()
        self._saved = False
        empty = [c for c in self._chips if not int(c.get("pin_count") or 0)]
        if empty:
            self._say("注意：%d 个框一个引脚都没找到（%s），检查一下框的位置。"
                      % (len(empty), "、".join(str(c.get("id")) for c in empty)),
                      "warn")

    def _on_flow_failed(self, _msg):
        """失败时界面收尾。原因那行已经由 stage 说了，这里不重复。"""
        self._prog_on = False
        self.view.hide_progress()
        if self._img is not None and self._chips is None:
            # YOLO 没检出框：给一个空列表，编辑条的【矩形框】才能手工补框
            self._chips = []
            self._paint()

    def _on_flow_finished(self, _ok):
        self._prog_on = False
        self.view.hide_progress()
        self._flow_busy(False)
        # 目录在开跑那一刻就建出来了（不管后面成没成），任务号该往前走一格：
        # 框里永远是"下一次会建哪个目录"，不是"刚才那个"。
        self._refresh_task()

    def _flow_busy(self, on):
        """流程跑着的时候把两个会重入的按钮按住（急停永远可点）。"""
        self.btn_vision.setEnabled(not on)
        self.btn_save.setEnabled(not on)
        self.btn_vision.setText("  识别中…" if on else "  视觉启动")
        # 【切换】跟着一起按住：识别正跑着的时候切实时画面，既没意义、
        # 又会跟拍照抢同一路相机流
        if on:
            self.btn_switch.setEnabled(False)
        else:
            self._refresh_switch()

    # ---------------------------------------------------------------- 保存
    def _on_save(self):
        """【保存】：人工确认过了，把引脚 + 矩形框坐标写进 引脚json，再展开点位表。

        保存成功还会把画布切到**相机实时画面**（相机没连就留在识别图）——
        用户 2026-11-09 要的：确认完就想看一眼相机现在对着哪儿。
        """
        if self._sess is not None and self._sess.busy:
            self._say("识别还没跑完：等结果出来、人工检查过再保存。", "warn")
            return
        if not self._chips:
            self._say("还没有识别结果可保存：先点【视觉启动】。", "warn")
            return
        if not self._json_dir:
            self._say("没有本次测试目录：先点【视觉启动】（目录按「板号.检修状态.序号」建在保存地址下）。",
                      "warn")
            return
        path = self._write_pins()
        if not path:
            return
        n_pin = sum(int(c.get("pin_count") or 0) for c in self._chips)
        self._saved = True
        self._say("已保存 引脚json\\%s（%d 框 / %d 点）"
                  % (os.path.basename(path), len(self._chips), n_pin), "ok")
        # 保存 = 人工确认完毕：从这一刻起才允许切到实时画面（用户定），
        # 并且**自动**切过去。相机没连 / 被占着就留在识别图，日志里会写原因。
        self._live_ready = True
        self._refresh_switch()
        if self._enter_live(auto=True):
            self._say("画布已切到相机实时画面（点【切换】可回到识别图）。", "info")
        if self.proj is None or not self.proj.active:
            self._say("项目还没选测试程序：点位表先空着（去「测试管理」选一份）。", "warn")
            return
        # 表直接吃这份 json，不靠"找最新文件"猜
        self._load_points(path, force=True)

    def _write_pins(self):
        """把当前 chips 写进 `<会话>\\引脚json\\<图名>_pins.json`，返回路径。

        键名跟「引脚识别」工具、视觉定位页写的一致（image / chips），
        多出来的项目、程序、时间三个键只是方便以后追查是哪次跑出来的。
        """
        if not self._chips or not self._json_dir:
            return ""
        stem = self._stem or "capture"
        path = os.path.join(self._json_dir, stem + "_pins.json")
        data = {
            "image": os.path.basename(self._photo or ""),
            "chips": self._chips,
            "project": self.proj.name if self.proj is not None else "",
            "program": self.proj.active if self.proj is not None else "",
            "board": self.proj.board if self.proj is not None else "",
            "repair": self.proj.repair if self.proj is not None else "",
            # 任务号 = 本次目录名，直接取目录（json_dir 是 <会话>\引脚json）。
            # 不用 proj.task：那是界面算的"下一个号"，可能和这次真正建出来的差一号。
            "task": os.path.basename(os.path.dirname(self._json_dir)),
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            os.makedirs(self._json_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except (IOError, OSError) as e:
            self._say("引脚结果写不进去：%s" % e, "err")
            return ""
        self._json_path = path
        return path

    # ---------------------------------------------------------------- 画布
    def _dot_size(self, img):
        """引脚红点在**照片像素**里的半径。

        画布只有 450 宽，3072 的图缩到 15%，默认半径 2 的红点在屏幕上是 0.3 px
        —— 等于看不见。按图片宽度反算，让它在屏幕上有 1.5 px 左右。
        """
        w = img.shape[1] if img is not None else 0
        return int(max(2, round(w / float(CANVAS_W) * 1.6)))

    def _paint(self):
        """按当前 chips 重画画布：红点烙进底图（跟视觉定位页一样），框和编号另走矢量层。"""
        if self._img is None:
            return
        chips = self._chips or []
        disp = self._img
        if chips:
            try:
                disp = veng.render(self._img, chips, dot_size=self._dot_size(self._img),
                                   num_size=2, id_size=0)
            except Exception:                # 标注画不出来不影响看清框
                traceback.print_exc()
                disp = self._img
        self.view.set_image(disp)
        self.view.set_boxes([c.get("box") for c in chips],
                            [c.get("id") for c in chips])
        self.view.set_pins(chips, dot_size=self._dot_size(self._img))

    # ============================================================ 实时画面 / 切换
    # 保存之后画布可以放相机实时画面（用户 2026-11-09 定）：
    #   · 【保存】成功 → 自动切实时（相机没连就留在识别图，日志说明原因）
    #   · 【切换】在两态之间来回：实时 ←→ 识别图（框 / 红点 / 编号都在）
    #   · 实时时编辑条整条锁住 —— 那些操作改的是照片像素坐标，在动着的画面上
    #     改只会把数据搞坏
    # 实时帧不叠识别框：框是这张照片的坐标，画在实时画面上会被误认为在跟踪。
    def _refresh_switch(self):
        """【切换】的可用性 + 提示：只有**保存过**、且没有流程在跑时才可点。"""
        busy = self._sess is not None and self._sess.busy
        ok = bool(self._live_ready and self._img is not None and not busy)
        self.btn_switch.setEnabled(ok)
        self.btn_switch.setText("切换")
        if not self._live_ready:
            tip = "先【保存】识别结果：保存之后才能看相机实时画面"
        elif self._live:
            tip = "现在放的是相机实时画面\n点一下切回识别后的图（框和红色引脚点都在）"
        else:
            tip = ("点一下切到相机实时画面\n实时时画布下方的编辑条会锁住，再点一下切回识别图")
        self.btn_switch.setToolTip(tip)

    def _enable_live_stream(self):
        """把相机流开起来并把帧接到本页。返回 (ok, 失败原因)。"""
        try:
            cam = self.mgr.driver("camera")
        except Exception as e:
            traceback.print_exc()
            return False, "拿不到相机驱动（%r）" % (e,)
        if cam is None:
            return False, "没有相机驱动"
        if not self.mgr.is_connected("camera"):
            return False, "相机没连上（先去【设备管理】连接）"
        if getattr(cam, "state", "") != ST_CONNECTED:
            return False, "相机状态是「%s」，不是已连接" % getattr(cam, "state", "?")
        # 先摘一次再连：反复切换时避免同一个槽被接上两遍（帧会被处理两次）
        try:
            cam.bridge.frame.disconnect(self._on_live_frame)
        except Exception:
            pass
        try:
            cam.bridge.frame.connect(self._on_live_frame)
            cam.bridge.lost.connect(self._on_live_lost)
        except Exception as e:
            traceback.print_exc()
            return False, "接不上相机帧信号（%r）" % (e,)
        if cam.streaming:
            # 流是别的页面开着的：只借，不关（谁开的谁负责停）
            self._live_mine = False
            self._live_cam = cam
            return True, ""
        ok, msg = cam.start_stream()
        if not ok:
            try:
                cam.bridge.frame.disconnect(self._on_live_frame)
            except Exception:
                pass
            return False, "开始取流失败：%s" % msg
        self._live_mine = True
        self._live_cam = cam
        return True, ""

    def _close_live_stream(self):
        """断开帧信号；流是本页开的才停（别人开着的别动）。"""
        cam = self._live_cam
        self._live_cam = None
        if cam is None:
            return
        try:
            cam.bridge.frame.disconnect(self._on_live_frame)
        except Exception:
            pass
        try:
            cam.bridge.lost.disconnect(self._on_live_lost)
        except Exception:
            pass
        if self._live_mine:
            self._live_mine = False
            try:
                cam.stop_stream()
            except Exception:
                traceback.print_exc()

    def _on_live_frame(self, img):
        """相机帧：只留最新一帧，90 ms 内最多刷一次（见 __init__ 的 _live_timer）。"""
        if not self._live or img is None or img.isNull():
            return
        self._live_img = img
        if not self._live_timer.isActive():
            self._live_timer.start(90)

    def _flush_live(self):
        if not self._live or self._live_img is None:
            return
        # 尺寸和照片一致（同一台相机）→ 画布保留当前缩放/平移，切换不跳
        self.view.set_image(QPixmap.fromImage(self._live_img))

    def _on_live_lost(self):
        # 先把流收干净（若还挂着、且是本页开的，就停掉），再退回图片：
        # 掉线信号到了不代表流一定已经停了，留着会白占相机带宽。
        self._close_live_stream()
        if self._live:
            self._exit_live(restore=True)
            self._say("相机掉线了：画布退回识别图。相机接好后点【切换】可以再看实时画面。",
                      "err")

    def _enter_live(self, auto=False):
        """切到相机实时画面。auto=True = 保存后自动来的（失败只记日志，不弹窗）。"""
        if self._live:
            return True
        if not self._live_ready:
            if not auto:
                self._say("先【保存】识别结果，再切实时画面。", "warn")
            return False
        if self._img is None:
            if not auto:
                self._say("画布上还没有照片：先点【视觉启动】。", "warn")
            return False
        ok, why = self._enable_live_stream()
        if not ok:
            self._say("切不了实时画面：%s。画布仍显示识别后的图。" % why, "warn")
            self._refresh_switch()
            return False
        self._live = True
        self._live_img = None               # 等第一帧，别把上一轮的旧帧先画上去
        # 实时画面上不叠框/红点（那是照片坐标），切回图片时由 _paint() 原样恢复
        self.view.set_boxes([])
        self.view.set_pins([])
        self.kit.set_locked(True)           # 用户定的：实时时编辑条禁用
        self._refresh_switch()
        return True

    def _exit_live(self, restore=True):
        """退出实时画面。restore=True → 把识别图（框 + 红点）重画回来。"""
        if not self._live:
            self._refresh_switch()
            return
        self._live = False
        self._live_timer.stop()
        self._live_img = None
        self.kit.set_locked(False)          # 编辑条解锁
        if restore:
            self._paint()                   # 照片 + 框 + 红色引脚点，原样回来
        self._refresh_switch()

    def _toggle_live(self):
        """【切换】：识别图 ←→ 相机实时。"""
        if self._live:
            self._exit_live(restore=True)
            self._close_live_stream()       # 切回图片就不再白占相机带宽
            self._say("画布已切回识别后的图（框和红色引脚点都在）。", "info")
            return
        if self._enter_live():
            self._say("画布已切到相机实时画面；编辑条暂时锁住，再点【切换】回到识别图。",
                      "ok")

    # ================================================================ 画布回调
    # 引脚编辑条要的四个回调。chips 为 None 时编辑条会拦下操作并说明原因
    # （"请先点引脚识别"），所以"还没识别"不需要这里再判一遍。
    def _pin_get(self):
        return self._chips

    def _pin_apply(self, chips):
        """编辑条改完数据回调这里：收下新 chips 并重画。

        **不要在这里调 kit.refresh()** —— 它内部会再调一次 _apply，转成死循环。
        """
        self._chips = chips
        self._paint()

    def _pin_persist(self):
        """每改一步就往 引脚json 里写一遍，不然人工改的东西一关就没了。

        返回绝对路径，编辑条会显示「已同步 xxx_pins.json」。
        """
        if self._chips is None:
            return "（还没有识别结果）"
        self._saved = False                 # 改过了，【保存】需要再点一次
        return self._write_pins() or "（写入失败）"

    def _pin_iter(self):
        """批量召回率口径用：自动测试一次只有当前这一张照片。"""
        if not self._chips:
            return None
        return [(os.path.basename(self._photo or "当前照片"), self._chips)]

    def _draw_chip(self, idx):
        """编辑条画完新框后回调：只重跑这一颗，把框里的引脚补上。"""
        if self._img is None or self._engine_obj is None or not self._chips:
            return
        if not (0 <= int(idx) < len(self._chips)):
            return
        ch = self._chips[int(idx)]
        self._prog_on = True
        self.view.show_progress("识别新框内的引脚…", total=100)
        self._engine_obj.rerun(self._img, ch.get("box"), ch.get("label"),
                              meta={"idx": int(idx)})

    def _on_engine_done(self, kind, payload, info):
        """引擎结果里只有**单芯片重跑**归本页管；detect / pins 由视觉会话处理。"""
        if kind != "rerun":
            return
        meta = (info or {}).get("meta") or {}
        idx = int(meta.get("idx", -1))
        self._prog_on = False
        self.view.hide_progress()
        chip, detail = payload
        if chip is None:
            self._say("重跑这颗失败：%s" % ((detail or {}).get("msg") or "算法没给出原因"),
                      "err")
            return
        cur = list(self._chips or [])
        if not (0 <= idx < len(cur)):
            return
        chip = dict(chip)
        chip["chip_id"] = cur[idx].get("chip_id", idx)
        chip["id"] = cur[idx].get("id")          # 编号跟着位置走，不因重跑而变
        cur[idx] = chip
        self._chips = cur
        veng.pins_pkg().assign_chip_ids(cur)
        self._paint()
        self.kit.refresh()
        self._write_pins()
        self._say("已重跑 %s：%d 个点" % (chip.get("id"),
                                          int(chip.get("pin_count") or 0)), "ok")

    # ================================================================ 急停
    def _on_estop(self):
        """急停按钮：一次按下 = 停 + 上锁；再按一次 = 解除。

        为什么做成两态而不是"按了就停"：只发一次停止挡不住后面的指令 ——
        自动流程会紧接着把轴再带走。所以停下之后必须上锁，等有人明确
        确认现场安全再解除。按钮文字这时是「解除急停」，不会让人摸不清。

        停止指令在**本线程直接下发**，不进队列：排在一个 120 秒的回零后面
        等，就不叫急停了。DLL 的停止调用只有几毫秒，界面这几毫秒不刷新值得。
        """
        if self._estop_on:
            self.mgr.clear_estop()
            self._set_estop_ui(False)
            self._say("急停已解除，可以重新启动动作。", "ok")
            return

        # 先把界面切成"已急停"：即使下面某台设备卡住，用户也看得到按钮生效了
        self._set_estop_ui(True)
        self._say("【急停】已按下：给三轴控制器、探针座下发停止指令。", "err")
        # 正在跑的视觉流程也停：它下一步要么在等相机出图、要么在等识别结果，
        # 相机一停流就等不到了 —— 与其让它挂着 8 秒超时，不如当场收掉。
        if self._sess is not None and self._sess.busy:
            self._sess.cancel()
            self._say("　视觉流程已中止。", "warn")
        try:
            res = self.mgr.estop()
        except Exception as e:
            traceback.print_exc()
            self._say("急停调用异常：%r —— 请立即按硬件急停！" % e, "err")
            return

        for key, ok, msg in res:
            name = DEVICE_META.get(key, {}).get("name", key)
            self._say("　%s：%s" % (name, msg), "ok" if ok else "err")

        if any(ok for _k, ok, _m in res):
            self._say("运动已停止并锁定，确认现场安全后再点【解除急停】。", "warn")
        else:
            self._say("没有任何一台设备执行了停止指令：请立即按硬件急停！", "err")

    def _set_estop_ui(self, on):
        """按钮两态：常态是红实心「■ 急停」，按下后是描边「↻ 解除急停」。"""
        self._estop_on = bool(on)
        if on:
            self.btn_estop.setText("↻ 解除急停")
            self.btn_estop.setStyleSheet(ESTOP_LOCK_QSS)
            self.btn_estop.setToolTip(
                "急停已生效：三轴与探针的运动被拦住。\n"
                "确认现场安全后点这里解除，解除后才能再启动动作")
        else:
            self.btn_estop.setText("■ 急停")
            self.btn_estop.setStyleSheet("")     # 置空 → 回到主题的 #estop 样式
            self.btn_estop.setToolTip(ESTOP_TIP)

    # ================================================================ 其他
    def _estop_guard(self, what):
        """急停锁着时先拦下来，返回 True = 别往下走。"""
        if self.mgr.is_estopped():
            self._say("【%s】急停还锁着：确认现场安全后点【解除急停】再操作。"
                      % what, "warn")
            return True
        return False

    def _todo(self, what):
        """按钮功能还没接 —— 明确说一句，别让人以为是没反应。"""
        if self._estop_guard(what):
            return
        self._say("【%s】的功能还没接上：这一轮只做了布局，接好会用你说的顺序来。"
                  % what, "warn")

    def _say(self, text, level="info"):
        """说一句 —— 只记进日志（状态行那一行已按用户要求删掉）。"""
        self._log(text, level)

    # ================================================================ 生命周期
    def hideEvent(self, e):
        """离开本页就退出实时画面、停掉自己开的流。

        相机带宽要留给真正在用的页面（视觉定位页一开也会取流）；本页只是
        "看一眼相机现在对着哪儿"，没必要在后台一直跑 30 fps。
        下次切回来，画布是识别图，点【切换】再看实时。
        """
        self._exit_live(restore=True)
        self._close_live_stream()
        super(AutoPage, self).hideEvent(e)

    def showEvent(self, e):
        super(AutoPage, self).showEvent(e)
        self._resync()
        self._refresh_devices()
        self._fit_log()
        self._fit_canvas()             # 首帧布局此时才算得出真实高度
        self._fit_cols()               # 首帧视口宽也是这时候才准
        # 再补一次：页面刚被切进来时 stack 可能还没把它撑到最终宽度，
        # 上面那次 _fit_cols 会按窄一点的视口算，回头就多出一条横向滚动条。
        QTimer.singleShot(0, self._fit_cols)
        # 急停可能是别的页面 / 后台流程按下的，切回本页要对齐按钮状态，
        # 不然界面显示「■ 急停」而闸其实是锁着的，点下去只会更糊涂。
        if self.mgr.is_estopped() != self._estop_on:
            self._set_estop_ui(self.mgr.is_estopped())
        if not self._inited:
            self._inited = True
            self._log("自动测试页已就绪：先【打开项目】，再连接设备。", "info")

    def _resync(self):
        """重读项目清单：项目可能在测试管理里改过 / 删过，切回来要看到最新的。"""
        self.reg = Registry(self.mgr.cfg)
        if self.proj is None:
            return
        p = self.reg.find(self.proj.name)
        if p is None:
            name = self.proj.name
            self.proj = None
            self._show_project(None)
            self._say("原来选的项目「%s」已经不在清单里了，重新选一个。"
                      % name, "warn")
        else:
            self.proj = p                        # 换成新对象，参数是刚保存的
            self._show_project(p)
        self._refresh_devices()
        self._load_points()                      # 项目可能改了程序 / 新识别了一张图

    def resizeEvent(self, e):
        super(AutoPage, self).resizeEvent(e)
        self._place_devbar()
        self._fit_log()
        self._fit_canvas()
        self._fit_cols()

    def _fit_cols(self):
        """14 列按当前可用宽度铺满，一屏看全、不留横向滚动。

        每列的最小宽是**现算的**：两行表头里最长那行、以及这一列现在的
        最长数据，取大的再加内边距。所以刚进页（除了框名/点位/像素，其余
        都是「—」）14 列能全铺下；等标定接上、空间/控制器/探针列真有数字
        了，这些列会自己变宽 —— 那时放不下才横向滚动，不是一开始就不给看。
        剩下的富余按最小宽比例分，窄列（点位、测试）不会拉得空荡荡。
        """
        tb = getattr(self, "tb_points", None)
        if tb is None:
            return
        avail = tb.viewport().width()
        if avail < 80:
            return

        fm_h = QFontMetrics(_head_font())
        pad = 8                                 # 单元格左右各 4 px（主题 QSS）
        mins = []
        for c, (name, _tip, floor) in enumerate(POINT_COLUMNS):
            need = max(fm_h.horizontalAdvance(s) for s in name.split("\n"))
            if c < len(self._col_need):
                need = max(need, self._col_need[c])
            mins.append(max(floor, need + pad))

        total = sum(mins)
        floors = [c[2] for c in POINT_COLUMNS]
        floor_total = sum(floors)
        if avail >= total:
            extra = avail - total
            widths = [m + int(extra * m / total) for m in mins]
            widths[-1] += avail - sum(widths)   # 余数给最后一列，别留缝
        elif avail > floor_total:
            # 不够铺满、但也比"下限"宽：在两档之间按比例插值，
            # 宁可把列压窄一点，也别让人为了看最后两列去拖滚动条
            span = total - floor_total
            widths = [f + int((m - f) * (avail - floor_total) / span)
                      for f, m in zip(floors, mins)]
            widths[-1] += avail - sum(widths)
        else:
            widths = list(floors)              # 真放不下了，退回下限并横向滚动
        if self._fitting:
            return
        self._fitting = True
        try:
            for i, w in enumerate(widths):
                if tb.columnWidth(i) != w:
                    tb.setColumnWidth(i, w)
        finally:
            self._fitting = False

    def eventFilter(self, obj, ev):
        """视口尺寸变了就重排列宽（滚动条出现/消失也会走到这里）。"""
        tb = getattr(self, "tb_points", None)
        if tb is not None and obj is tb.viewport() and ev.type() == QEvent.Resize:
            self._fit_cols()
        return super(AutoPage, self).eventFilter(obj, ev)

    def _fit_log(self):
        """日志高度跟着窗口走：窗口高多给两行，矮就收起，省下的全归画布。

        只改**上限**、不动下限：`setFixedHeight` 会把当前高度变成布局的
        最小高度，窗口一矮就再也压不下去（页面最小高度会从 716 顶到 756）。
        所以下限留 `LOG_H_MIN`，上限按页高 15% 算，两边都不动就不会来回抖。
        """
        if self._log_panel is None:
            return
        h = max(LOG_H_MIN, min(LOG_H_MAX, int(self.height() * LOG_RATIO)))
        if self._log_panel.maximumHeight() != h:
            self._log_panel.setMaximumHeight(h)

    def _fit_canvas(self):
        """兜底：窗口太矮时把画布压矮，别让它把引脚编辑条顶出面板。

        正常窗口下算出来 > 300，取 300 —— 也就是用户要的 450×300 固定尺寸。
        只有窗口矮到放不下时才会缩（画布 300 固定 + 上下内边距 16 + 间距 6 +
        工具条 36 = 358，是这一格的最低需求）。不这么做的话，窗口一矮，
        画布和工具条会重叠成一团。

        同样只在要变的时候才 setFixedSize，避免 resize 与布局互相触发。
        """
        if getattr(self, "view", None) is None or not hasattr(self, "_card"):
            return
        gap = 9                            # 左栏里卡片/画面之间、画面/日志之间
        # 日志高度按 _fit_log 的公式现算，不读 _log_panel.height()：
        # 那个值要等布局激活才更新，构造期间是 0，会算出一个过小的画布。
        h_log = max(LOG_H_MIN, min(LOG_H_MAX, int(self.height() * LOG_RATIO)))
        avail = (self.height() - HEAD_H - 18 - gap - self._card.sizeHint().height()
                 - gap - h_log)
        h = max(CANVAS_MIN_H, min(CANVAS_H, avail - 16 - 6 - 36))
        if self.view.height() != h:
            self.view.setFixedSize(CANVAS_W, h)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        # 宽度要让开页头右侧的设备状态条：描述文字只有在 w 内画得下才画，
        # 不让宽的话窄窗口下会钻到「工业相机」底下。
        paint_module_head(p, 18, HEAD_TOP, self.width() - 36 - self._devbar_w(),
                          self.mod["idx"], self.mod["name"], self.mod["desc"])
        p.end()
