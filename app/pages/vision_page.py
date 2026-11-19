# -*- coding: utf-8 -*-
"""视觉定位模块（左侧导航第 5 项）。

页面按「引脚识别工具」（PinTool）的 YOLO 模型导入面板摆出来，但改成**左右两栏**：
左边是全部参数（模型 / 相机 / 识别 / 外观 / 保存），右边整块留给交互画布。
原工具是上下排的，画布只有两百多像素高，3072×2048 的图缩到一两百像素，
左右两边全是黑边；改成左右分栏后画布有 800×700 上下，图能按原比例铺开。

    ┌ 左栏 464px ──────────────┐ ┌ 右栏 ─────────────────────────┐
    │ ┌ YOLO 模型导入 ───────┐ │ │ ┌ 引脚编辑 ┬───────────────┐ │
    │ │ 模型：[路径][选择]   │ │ │ │ 选择     │                │ │
    │ │ 相机：[打开][拍单张] │ │ │ │ 画笔     │   交互画布      │ │
    │ │       [拍多张][关闭] │ │ │ │ 矩形框   │  图片/取景画面  │ │
    │ │ ● 取景中 3072×2048   │ │ │ │ 撤回     │  芯片框+引脚点  │ │
    │ │ 拍照目录：[路径][..] │ │ │ │ 删除选中 │                │ │
    │ └──────────────────────┘ │ │ │ 召回率   │                │ │
    │ 图片模式：[单张▾][导入]   │ │ │          │                │ │
    │ [运行][运行全部][◀][▶]    │ │ └──────────┴───────────────┘ │
    │ [引脚识别][引脚识别全部]  │ └───────────────────────────────┘
    │ 标注外观：点/数字/编号…   │
    │ ┌ 操作提示 ────────────┐ │
    │ │ 画布怎么操作、怎么拍照│ │
    │ └──────────────────────┘ │
    │ 保存：[保存][全部保存]    │
    └──────────────────────────┘
    一句状态提示（跨全宽）

相机怎么接进来
--------------
原工具只有「导入图片」一条路。这里在模型导入框里加了一行相机按键。
**相机不直接拿去识别，一定要先拍成图片**——理由见下。

* **打开相机** —— 相机没连就先连（连不上会提示去设备管理看），连上后开始取流，
  交互画布变成实时画面（取景）；
* **拍单张** —— 取景时才亮。按一下抓当前帧、存到「拍照目录」、画布换成这张照片，
  **然后自动退出相机**。接下来就是普通图片，可以直接【运行】【引脚识别】【保存】；
* **拍多张** —— 同理，每按一次拍一张，拍到的依次进图片列表（可以【◀ 上一张】
  【下一张 ▶】翻看），相机继续取流；点【关闭相机】才退出。
* **关闭相机** —— 停流。本次拍过图就停在最后一张上接着用，一张没拍就退回原来的图。

为什么必须先拍照：一帧 3072×2048 的识别要几秒，边取流边算的话，结果画在 A 帧上、
屏幕上已经是 B 帧，框会「飘」；而且照片落盘之后才是一个正经的图片文件，
`<图名>_pins.json` 才有地方写回，补点/改框才能持久化。

不做什么
--------
* **不接几何畸变校正**（原工具的 jbjz\\correct.py 没复制进来），屏上的坐标就是
  相机像素坐标。
* 不设「连接/断开」按钮 —— 连接归设备管理，这里只是替它发起一次连接。

两条来路
--------
* `pintool\\`       —— 引脚识别算法（从 PinTool_portable_20261103 原样复制）
* `vision\\canvas.py` / `vision\\pinedit.py` —— 交互画布与引脚编辑工具条
  （同一来源，逻辑没改，只换了配色）
"""

import json
import os
import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPainter, QPixmap
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                             QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .. import theme
from ..devices import store
from ..devices.base import ST_CONNECTING
from ..vision import (CAPTURE_DIR, PinEngine, model_path, pins_pkg,
                      qimage_to_bgr, read_image, render, write_image)
from ..vision.canvas import InteractiveCanvas
from ..vision.pinedit import PinEditKit
from ..widgets import HEAD_H, Panel, _font, paint_module_head

CAMERA_KEY = "<camera>"
IMG_EXT = "图片 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)"
HINT = ("导入图片、或点【打开相机】后显示画面：Ctrl+滚轮缩放　·　"
        "按住滚轮拖动平移　·　左键点选框　·　右键重跑/编辑　·　双击复位")
COLORS = [(0, 200, 0), (255, 128, 0), (0, 128, 255), (200, 0, 200),
          (255, 0, 0), (0, 220, 220)]


def _now():
    return time.strftime("%Y%m%d_%H%M%S")


class VisionPage(QWidget):
    """视觉定位页。"""

    def __init__(self, mod, manager, parent=None):
        super(VisionPage, self).__init__(parent)
        self.mod = mod
        self.mgr = manager
        self.cam = manager.driver("camera")

        # ---- 数据（key = 图片路径，或 CAMERA_KEY）
        self.paths = []
        self.idx = 0
        self.detections = {}        # key -> [ {'label','conf','box','id'} ]
        self.pin_results = {}       # key -> [ chip ]
        self._cur_img = None        # 当前画面的 BGR（识别就用它）
        self._pending = None        # 'detect' / 'pins' / 'rerun' / 'batch'
        self._batch = None          # 批量任务的状态
        self._cam_on = False        # 相机在取流（不管画面显示的是实时还是照片）
        self._live = False          # 画布显示的是实时画面（取景中）；False = 显示照片
        self._cam_paths = []        # 本次相机会话拍到的文件（按顺序）
        self._cam_qimage = None     # 最新一帧（拍多张时画布不刷，但缓存要更新）
        self._cam_fps = 0.0
        self._cam_size = (0, 0)
        self._want_cam = False      # 点了【打开相机】，正等连接完成
        self._model_tried = False

        # 相机帧合并：一台 GigE 相机 30 fps、每帧 3072×2048，逐帧转 QPixmap
        # 是 500 MB/s 的搬运，界面会卡。这里最多 90 ms 刷一次，中间只留最后一帧。
        self._frame_timer = QTimer(self)
        self._frame_timer.setSingleShot(True)
        self._frame_timer.timeout.connect(self._flush_frame)

        # ---- 引擎
        self.engine = PinEngine(self)
        self.engine.stage.connect(lambda t: self._say(t, "info"))
        self.engine.model_ready.connect(self._on_model)
        self.engine.done.connect(self._on_done)
        self.engine.failed.connect(self._on_failed)

        self.setStyleSheet(theme.controls_qss())

        root = QVBoxLayout(self)
        root.setContentsMargins(18, HEAD_H, 18, 18)
        root.setSpacing(0)
        root.addWidget(self._build_panel(), 1)

        self.mgr.device_changed.connect(self._on_device_changed)
        if self.cam is not None:
            self.cam.bridge.frame.connect(self._on_frame)
            self.cam.bridge.stats.connect(self._on_stats)
            self.cam.bridge.lost.connect(self._on_lost)
            self.cam.bridge.grab_error.connect(self._on_grab_error)

        self._refresh_cam_label()
        self._refresh_actions()

    # ================================================================ 界面
    def _build_panel(self):
        panel = Panel()
        v = QVBoxLayout(panel)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(9)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        body.addWidget(self._build_left(), 0)
        body.addLayout(self._build_view_row(), 1)
        v.addLayout(body, 1)
        v.addWidget(self._build_info())
        return panel

    def _build_left(self):
        """左栏：所有参数。固定 464px —— 再多画布就窄了，再少两列控件排不下。"""
        left = QWidget()
        left.setFixedWidth(464)
        v = QVBoxLayout(left)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(9)
        v.addWidget(self._build_model_box())
        v.addLayout(self._build_img_row())
        v.addLayout(self._build_run_row())
        v.addLayout(self._build_pin_row())
        v.addLayout(self._build_pinopt_row())
        v.addLayout(self._build_style_row())
        v.addLayout(self._build_style_row2())
        v.addStretch(1)
        v.addLayout(self._build_save_row())
        v.addLayout(self._build_save_row2())
        return left

    def _build_model_box(self):
        gb = QGroupBox("YOLO 模型导入")
        gb.setObjectName("gbox")
        v = QVBoxLayout(gb)
        v.setContentsMargins(12, 6, 12, 10)
        v.setSpacing(8)

        # ---- 模型行
        r1 = QHBoxLayout()
        r1.setSpacing(8)
        r1.addWidget(self._lab("模型："))
        self.ed_model = QLineEdit()
        self.ed_model.setReadOnly(True)
        self.ed_model.setPlaceholderText("未选择模型（.pt）")
        self.ed_model.setFont(_font(theme.F_MONO, 10, spacing=0.1))
        self.ed_model.setFixedHeight(28)
        r1.addWidget(self.ed_model, 1)
        self.btn_model = QPushButton("选择模型")
        self.btn_model.setObjectName("act")
        self.btn_model.setFixedSize(88, 28)
        self.btn_model.setCursor(Qt.PointingHandCursor)
        self.btn_model.clicked.connect(self._on_pick_model)
        r1.addWidget(self.btn_model)
        self.lb_model_state = QLabel("未加载")
        self.lb_model_state.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        self.lb_model_state.setFixedWidth(132)
        self.lb_model_state.setStyleSheet(
            "color:%s;background:transparent;" % theme.TXT3)
        r1.addWidget(self.lb_model_state)
        v.addLayout(r1)

        # ---- 相机行：打开 / 拍单张 / 拍多张 / 关闭
        r2 = QHBoxLayout()
        r2.setSpacing(8)
        r2.addWidget(self._lab("相机："))

        self.btn_cam = QPushButton("打开相机")
        self.btn_cam.setObjectName("primary")
        self.btn_cam.setFixedSize(84, 28)
        self.btn_cam.setCursor(Qt.PointingHandCursor)
        self.btn_cam.setToolTip("开始取流，画布变成实时画面（相机没连会先连一次）。\n"
                                "拍完照之后再点它，可以回到实时画面继续取。")
        self.btn_cam.clicked.connect(self._on_open_camera)
        r2.addWidget(self.btn_cam)

        self.btn_shot1 = QPushButton("拍单张")
        self.btn_shot1.setObjectName("act")
        self.btn_shot1.setFixedSize(72, 28)
        self.btn_shot1.setCursor(Qt.PointingHandCursor)
        self.btn_shot1.setToolTip("抓当前这一帧、存到拍照目录，画布换成这张照片，\n"
                                  "然后自动退出相机 —— 接着就能【运行】【引脚识别】")
        self.btn_shot1.clicked.connect(self._on_shot_one)
        r2.addWidget(self.btn_shot1)

        self.btn_shot_many = QPushButton("拍多张")
        self.btn_shot_many.setObjectName("act")
        self.btn_shot_many.setFixedSize(72, 28)
        self.btn_shot_many.setCursor(Qt.PointingHandCursor)
        self.btn_shot_many.setToolTip("每按一次拍一张，依次进图片列表（可翻看）；\n"
                                      "相机继续取流，点【关闭相机】才退出")
        self.btn_shot_many.clicked.connect(self._on_shot_many)
        r2.addWidget(self.btn_shot_many)

        self.btn_cam_off = QPushButton("关闭相机")
        self.btn_cam_off.setObjectName("act")
        self.btn_cam_off.setFixedSize(84, 28)
        self.btn_cam_off.setCursor(Qt.PointingHandCursor)
        self.btn_cam_off.setToolTip("停止取流。本次拍过图就停在最后一张继续用，\n"
                                    "一张没拍就退回原来的图片。")
        self.btn_cam_off.clicked.connect(self._on_close_camera)
        r2.addWidget(self.btn_cam_off)
        v.addLayout(r2)

        # ---- 相机状态：单独一行，把 分辨率 / 帧率 / 已拍张数 写全
        self.lb_cam = QLabel("")
        self.lb_cam.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        self.lb_cam.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        self.lb_cam.setWordWrap(True)
        self.lb_cam.setMinimumHeight(16)
        v.addWidget(self.lb_cam)

        # ---- 拍照落盘目录
        r3 = QHBoxLayout()
        r3.setSpacing(8)
        r3.addWidget(self._lab("拍照目录"))
        self.ed_shot_dir = QLineEdit()
        self.ed_shot_dir.setReadOnly(True)
        self.ed_shot_dir.setFont(_font(theme.F_MONO, 10, spacing=0.1))
        self.ed_shot_dir.setFixedHeight(28)
        self.ed_shot_dir.setToolTip("相机拍下来的照片存这里；"
                                    "之后保存 JSON / 标注图也默认从这里开始选")
        r3.addWidget(self.ed_shot_dir, 1)
        self.btn_shot_dir = QPushButton("选择")
        self.btn_shot_dir.setObjectName("act")
        self.btn_shot_dir.setFixedSize(52, 28)
        self.btn_shot_dir.setCursor(Qt.PointingHandCursor)
        self.btn_shot_dir.setToolTip("换一个拍照保存文件夹")
        self.btn_shot_dir.clicked.connect(self._on_pick_shot_dir)
        r3.addWidget(self.btn_shot_dir)
        v.addLayout(r3)
        return gb

    def _build_img_row(self):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._lab("图片模式："))
        self.cb_mode = QComboBox()
        self.cb_mode.addItems(["单张", "批量"])
        self.cb_mode.setFixedSize(92, 28)
        self.cb_mode.currentIndexChanged.connect(self._on_mode)
        row.addWidget(self.cb_mode)

        self.btn_import = QPushButton("导入图片")
        self.btn_import.setObjectName("act")
        self.btn_import.setFixedSize(88, 28)
        self.btn_import.setCursor(Qt.PointingHandCursor)
        self.btn_import.clicked.connect(self._on_import)
        row.addWidget(self.btn_import)
        row.addStretch(1)
        return row

    def _build_run_row(self):
        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_run = QPushButton("运行")
        self.btn_run.setObjectName("primary")
        self.btn_run.setFixedSize(88, 28)
        self.btn_run.setCursor(Qt.PointingHandCursor)
        self.btn_run.setToolTip("跑 YOLO，检出芯片框（原工具的「运行」）")
        self.btn_run.clicked.connect(self._on_run)
        row.addWidget(self.btn_run)

        self.btn_run_all = QPushButton("运行全部")
        self.btn_run_all.setObjectName("act")
        self.btn_run_all.setFixedSize(88, 28)
        self.btn_run_all.setCursor(Qt.PointingHandCursor)
        self.btn_run_all.setToolTip("对当前图片列表里的每一张都跑一遍")
        self.btn_run_all.clicked.connect(self._on_run_all)
        row.addWidget(self.btn_run_all)

        row.addStretch(1)
        self.btn_prev = QPushButton("◀ 上一张")
        self.btn_prev.setObjectName("act")
        self.btn_prev.setFixedSize(88, 28)
        self.btn_prev.setCursor(Qt.PointingHandCursor)
        self.btn_prev.clicked.connect(lambda: self._switch(-1))
        self.btn_next = QPushButton("下一张 ▶")
        self.btn_next.setObjectName("act")
        self.btn_next.setFixedSize(88, 28)
        self.btn_next.setCursor(Qt.PointingHandCursor)
        self.btn_next.clicked.connect(lambda: self._switch(1))
        row.addWidget(self.btn_prev)
        row.addWidget(self.btn_next)
        return row

    def _build_pin_row(self):
        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_pin = QPushButton("引脚识别")
        self.btn_pin.setObjectName("primary")
        self.btn_pin.setFixedSize(88, 28)
        self.btn_pin.setCursor(Qt.PointingHandCursor)
        self.btn_pin.setToolTip("在芯片框内跑引脚识别（红点 + 黄编号）")
        self.btn_pin.clicked.connect(self._on_pin)
        row.addWidget(self.btn_pin)

        self.btn_pin_all = QPushButton("引脚识别全部")
        self.btn_pin_all.setObjectName("act")
        self.btn_pin_all.setFixedSize(108, 28)
        self.btn_pin_all.setCursor(Qt.PointingHandCursor)
        self.btn_pin_all.clicked.connect(self._on_pin_all)
        row.addWidget(self.btn_pin_all)

        row.addStretch(1)
        row.addWidget(self._lab("置信度"))
        self.sp_conf = QDoubleSpinBox()
        self.sp_conf.setRange(0.05, 0.95)
        self.sp_conf.setSingleStep(0.05)
        self.sp_conf.setDecimals(2)
        self.sp_conf.setFixedSize(72, 28)
        self.sp_conf.setToolTip("YOLO 检出芯片的门槛，默认 0.25。\n"
                                "调低会多检、也更容易把焊盘/丝印当成芯片")
        self.sp_conf.valueChanged.connect(lambda _v: self._save_later())
        row.addWidget(self.sp_conf)
        return row

    def _build_pinopt_row(self):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._lab("导出结果：", color=theme.TXT3))
        self.chk_pin_json = QCheckBox("存引脚 JSON")
        self.chk_pin_json.setChecked(True)
        self.chk_pin_img = QCheckBox("存引脚标注图")
        for ck in (self.chk_pin_json, self.chk_pin_img):
            ck.setFont(_font(theme.F_BODY, 10, spacing=0.1))
            ck.toggled.connect(lambda _on: self._save_later())
        row.addWidget(self.chk_pin_json)
        row.addWidget(self.chk_pin_img)
        row.addStretch(1)
        return row

    def _build_style_row(self):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._lab("标注外观：", bold=True, color=theme.TXT3))

        row.addWidget(self._lab("引脚点大小"))
        self.sp_dot = QSpinBox()
        self.sp_dot.setRange(1, 40)
        self.sp_dot.setSuffix(" px")
        self.sp_dot.setFixedSize(78, 28)
        self.sp_dot.setToolTip("引脚标注红点半径（1~40 px），默认 2")
        self.sp_dot.valueChanged.connect(self._on_style_changed)
        row.addWidget(self.sp_dot)

        row.addWidget(self._lab("数字大小"))
        self.sp_num = QSpinBox()
        self.sp_num.setRange(1, 12)
        self.sp_num.setSuffix(" 档")
        self.sp_num.setFixedSize(78, 28)
        self.sp_num.setToolTip("引脚编号数字大小档位（1~12），默认 2")
        self.sp_num.valueChanged.connect(self._on_style_changed)
        row.addWidget(self.sp_num)
        row.addStretch(1)
        return row

    def _build_style_row2(self):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._lab("芯片编号"))
        self.sp_id = QSpinBox()
        self.sp_id.setRange(0, 12)
        self.sp_id.setSuffix(" 档")
        self.sp_id.setFixedSize(78, 28)
        self.sp_id.setToolTip("芯片框左上角的编号（T_1 / F_2 / O_3…），0 = 不显示。\n"
                              "编号是矢量浮层，缩放不糊；存带框图时才烙进像素。")
        self.sp_id.valueChanged.connect(self._on_style_changed)
        row.addWidget(self.sp_id)

        row.addSpacing(6)
        self.chk_enh = QCheckBox("智能补全")
        self.chk_enh.setChecked(True)
        self.chk_enh.setToolTip("分类/对称/插值/增强/同款跨片补全，默认开。\n"
                                "关掉就只用 pin_detect 原始结果。")
        self.chk_enh.toggled.connect(lambda _on: self._save_later())
        row.addWidget(self.chk_enh)

        self.chk_gb = QCheckBox("GrayBoost 兜底")
        self.chk_gb.setChecked(True)
        self.chk_gb.setToolTip("低分辨率 / 单芯片特写这类图，自动换 GrayBoost 重识别。\n"
                               "整板图（3072×2048）不受影响。")
        self.chk_gb.toggled.connect(lambda _on: self._save_later())
        row.addWidget(self.chk_gb)

        row.addStretch(1)

        self.btn_kinds = QPushButton("类别管理")
        self.btn_kinds.setObjectName("act")
        self.btn_kinds.setFixedSize(88, 28)
        self.btn_kinds.setCursor(Qt.PointingHandCursor)
        self.btn_kinds.setToolTip("列举引脚算法类别（O/T/F/C/V/L）与新增的类别。\n"
                                  "新增类别 = 选基础大类 + 每排引脚数，识别时转调该大类\n"
                                  "的算法，原类别的参数一个都不改。")
        self.btn_kinds.clicked.connect(self._on_kinds)
        row.addWidget(self.btn_kinds)
        return row

    def _on_kinds(self):
        """打开类别管理。关闭后只刷新提示与按钮可用态，不动已识别的结果 ——
        类别只影响"下一次识别"，已存 JSON 里的点不会因为改类别而消失。"""
        from .kind_mgr import open_kind_manager
        open_kind_manager(self)
        try:
            from .kind_mgr import pin_kinds
            n_all = len(pin_kinds.names(all_kinds=True))
            n_cus = len(pin_kinds.names())
        except Exception:
            n_all, n_cus = 6, 0
        self._say("类别设置已更新：共 %d 个类别（自定义 %d 个）。"
                  "改类别只影响之后的识别，已保存的点不变。" % (n_all, n_cus))

    def _build_view_row(self):
        self.view = InteractiveCanvas(HINT)
        self.view.setMinimumHeight(320)
        self.view.setMinimumWidth(300)
        self.view.rerunRequested.connect(self._on_rerun_chip)
        self.view.boxSelected.connect(self._on_box_selected)

        # 工具条横着放在画布下面：画布就能吃满整栏宽度（原来竖条占掉 112 px）
        self.kit = PinEditKit(self.view, self._pin_get, self._pin_apply,
                             self._pin_persist, self._say, iter_all=self._pin_iter,
                             draw_chip=self._on_rerun_chip, parent=self,
                             horizontal=True)

        box = QVBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        box.addWidget(self.view, 1)
        box.addWidget(self.kit.toolbar())
        return box

    def _build_save_row(self):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._lab("保存内容：", color=theme.TXT3))
        self.chk_json = QCheckBox("保存 JSON")
        self.chk_json.setChecked(True)
        self.chk_img = QCheckBox("保存带框图")
        for ck in (self.chk_json, self.chk_img):
            ck.setFont(_font(theme.F_BODY, 10, spacing=0.1))
            ck.toggled.connect(lambda _on: self._save_later())
        row.addWidget(self.chk_json)
        row.addWidget(self.chk_img)
        row.addStretch(1)
        return row

    def _build_save_row2(self):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        self.btn_save = QPushButton("保存")
        self.btn_save.setObjectName("primary")
        self.btn_save.setFixedSize(118, 30)
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.setToolTip("把当前这张的检测 / 引脚结果写到指定文件夹")
        self.btn_save.clicked.connect(self._on_save)
        row.addWidget(self.btn_save)

        self.btn_save_all = QPushButton("全部保存")
        self.btn_save_all.setObjectName("act")
        self.btn_save_all.setFixedSize(118, 30)
        self.btn_save_all.setCursor(Qt.PointingHandCursor)
        self.btn_save_all.setToolTip("列表里所有识别过的图一起写出去")
        self.btn_save_all.clicked.connect(self._on_save_all)
        row.addWidget(self.btn_save_all)
        return row

    def _build_info(self):
        self.info = QLabel("请先导入 YOLO 模型，再导入图片；也可以直接点【打开相机】。")
        self.info.setFont(_font(theme.F_BODY, 10, spacing=0.2))
        self.info.setWordWrap(True)
        self.info.setMinimumHeight(18)
        self.info.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        return self.info

    def _lab(self, text, bold=False, color=None):
        lb = QLabel(text)
        lb.setFont(_font(theme.F_BODY, 11, bold=bold, spacing=0.3))
        lb.setStyleSheet("color:%s;background:transparent;"
                         % (color or theme.TXT2))
        return lb

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        paint_module_head(p, 18, 16, self.width() - 36,
                          self.mod["idx"], self.mod["name"], self.mod["desc"])
        p.end()

    # ================================================================ 配置
    def _cfg(self):
        cfg = self.mgr.cfg.get("vision")
        if cfg is None:
            cfg = self.mgr.cfg.setdefault("vision", {})
        return cfg

    def _apply_cfg(self):
        cfg = self._cfg()
        self.sp_conf.setValue(float(cfg.get("conf", 0.25) or 0.25))
        self.sp_dot.setValue(int(cfg.get("dot_size", 2) or 2))
        self.sp_num.setValue(int(cfg.get("num_size", 2) or 2))
        self.sp_id.setValue(int(cfg.get("id_size", 3) or 3))
        self.chk_enh.setChecked(bool(cfg.get("enhance", True)))
        self.chk_gb.setChecked(bool(cfg.get("grayboost", True)))
        self.chk_json.setChecked(bool(cfg.get("save_json", True)))
        self.chk_img.setChecked(bool(cfg.get("save_img", False)))
        self.chk_pin_json.setChecked(bool(cfg.get("save_pin_json", True)))
        self.chk_pin_img.setChecked(bool(cfg.get("save_pin_img", False)))
        self._show_model_name()
        d = self._shot_dir()
        self.ed_shot_dir.setText(d)
        self.ed_shot_dir.setCursorPosition(0)
        self.ed_shot_dir.setToolTip(d)

    def _show_model_name(self):
        p = model_path(self._cfg().get("model"))
        self.ed_model.setText(p)
        self.ed_model.setCursorPosition(0)     # 不设的话光标在末尾，长路径会把开头滚掉
        self.ed_model.setToolTip(p)

    def _save_later(self):
        if getattr(self, "_save_timer", None) is None:
            self._save_timer = QTimer(self)
            self._save_timer.setSingleShot(True)
            self._save_timer.timeout.connect(self._flush_cfg)
        self._save_timer.start(600)

    def _flush_cfg(self):
        # 还没走过一次 _apply_cfg 就存盘，会把「界面默认值」当成用户设置写进配置文件
        # （以前真出现过 dot_size 被写成 1）。没初始化完就先不动盘上的东西。
        if not getattr(self, "_inited", False):
            return
        cfg = self._cfg()
        cfg.update({"conf": float(self.sp_conf.value()),
                    "dot_size": int(self.sp_dot.value()),
                    "num_size": int(self.sp_num.value()),
                    "id_size": int(self.sp_id.value()),
                    "enhance": self.chk_enh.isChecked(),
                    "grayboost": self.chk_gb.isChecked(),
                    "save_json": self.chk_json.isChecked(),
                    "save_img": self.chk_img.isChecked(),
                    "save_pin_json": self.chk_pin_json.isChecked(),
                    "save_pin_img": self.chk_pin_img.isChecked()})
        try:
            store.save(self.mgr.cfg)
        except (IOError, OSError) as e:
            self._say("设置没能写入配置文件：%s" % e, "warn")

    # ================================================================ 生命周期
    def showEvent(self, e):
        super(VisionPage, self).showEvent(e)
        if not getattr(self, "_inited", False):
            self._inited = True
            self._apply_cfg()
            self._say("视觉定位已就绪：导入图片，或点【打开相机】。", "info")
        QTimer.singleShot(0, self._sync_camera)
        QTimer.singleShot(0, self._ensure_model)

    def hideEvent(self, e):
        # 离开本页先把流停掉：设备管理那边可能马上要用同一台相机
        if self.cam is not None and getattr(self.cam, "streaming", False):
            self.cam.stop_stream()
        super(VisionPage, self).hideEvent(e)

    def _ensure_model(self):
        if self._model_tried or self.engine.loaded:
            return
        self._model_tried = True
        self._show_model_name()
        self.engine.load_model(self._cfg().get("model"))

    def _sync_camera(self):
        """回到本页：相机还开着但流被掐了（比如去过设备管理页），把取流接上。"""
        if not self._cam_on or self.cam is None:
            return
        if not self.mgr.is_connected("camera"):
            return
        if not getattr(self.cam, "streaming", False):
            ok, _msg = self.cam.start_stream()
            if not ok:
                self._say("恢复取流失败：%s" % _msg, "err")
        self._refresh_cam_label()
        self._refresh_actions()

    # ================================================================ 相机
    def _on_open_camera(self):
        if self.cam is None:
            self._say("没有相机设备（设备管理里没有这一项）。", "err")
            return
        if not self.mgr.is_connected("camera"):
            if self._want_cam:
                self._say("相机还在连接中，连上会自动开始取流。", "warn")
                return
            self._want_cam = True
            self._say("相机未连接，正在连接…（连上会自动开始取流）", "info")
            self.mgr.connect("camera", then_apply=True)
            self._refresh_cam_label()
            return

        if self._cam_on and not self._live:
            # 拍完照还接着取流：回到取景，不用重新开流
            self._live = True
            self.view.clear_image()
            self.view.set_placeholder("正在恢复取景…")
            self._say("已回到实时画面，可以接着拍。", "info")
            self._refresh_cam_label()
            self._refresh_actions()
            return

        ok, msg = self.cam.start_stream()
        if not ok:
            self._say("开始取流失败：%s　（到【设备管理】里检查相机）" % msg, "err")
            return
        self._cam_on = True
        self._live = True
        self._cam_paths = []
        self.view.clear_image()
        self.view.set_placeholder("相机已打开，正在等第一帧…")
        self.kit.reset_state()
        self._say("相机已打开：画面出来后点【拍单张】或【拍多张】。", "ok")
        self._refresh_cam_label()
        self._refresh_actions()

    # ---------------------------------------------------------------- 拍照
    def _shot_dir(self):
        d = str(self._cfg().get("shot_dir") or "").strip()
        return d or CAPTURE_DIR

    def _on_pick_shot_dir(self):
        start = self._shot_dir()
        if not os.path.isdir(start):
            start = CAPTURE_DIR
        d = QFileDialog.getExistingDirectory(self, "选择拍照保存文件夹", start)
        if not d:
            return
        self._cfg()["shot_dir"] = d
        self._save_later()
        self.ed_shot_dir.setText(d)
        self.ed_shot_dir.setCursorPosition(0)
        self.ed_shot_dir.setToolTip(d)
        self._say("拍照目录已设为：%s" % d, "ok")

    def _shot_name(self):
        t = time.time()
        return "shot_%s_%03d.png" % (time.strftime("%Y%m%d_%H%M%S",
                                                   time.localtime(t)),
                                     int((t % 1) * 1000))

    def _take_shot(self):
        """抓当前帧 → 落盘 → 当成一张普通图片加到列表里显示。失败返回 ""。"""
        if self._pending is not None:
            self._say("上一步还没跑完，等它出结果再拍。", "warn")
            return ""
        if not self._cam_on:
            self._say("先点【打开相机】，有画面了再拍。", "warn")
            return ""
        if self._cam_qimage is None:
            self._say("还没收到画面，等一两秒再拍。", "warn")
            return ""
        img = qimage_to_bgr(self._cam_qimage)
        if img is None:
            self._say("这一帧读不出像素，稍后再拍。", "err")
            return ""
        d = self._shot_dir()
        if not os.path.isdir(d):
            try:
                os.makedirs(d)
            except OSError as e:
                self._say("拍照目录建不出来：%s" % e, "err")
                return ""
        path = os.path.join(d, self._shot_name())
        ok, msg = write_image(path, img)
        if not ok:
            self._say("照片存不下来：%s　（换一个拍照目录试试）" % msg, "err")
            return ""

        # 拍到的就是一张普通图片：进列表、选中、直接显示（不必再从磁盘读一遍）
        self.paths.append(path)
        self.idx = len(self.paths) - 1
        self._cam_paths.append(path)
        self._cur_img = img
        self.kit.reset_state()
        self.view.set_image(img)
        self.view.set_tag_level(self.sp_id.value())
        self.view.set_boxes([])
        self.view.set_pins([])
        self.kit.refresh()
        return path

    def _stop_stream(self):
        if self.cam is not None:
            self.cam.stop_stream()
        self._cam_on = False
        self._live = False
        self._cam_qimage = None

    def _on_shot_one(self):
        path = self._take_shot()
        if not path:
            return
        self._stop_stream()                    # 拍完自动退出相机
        self._refresh_cam_label()
        self._refresh_actions()
        self._say("已拍照并退出相机：%s　—　点【运行】识别芯片框，再【引脚识别】。"
                  % os.path.basename(path), "ok")

    def _on_shot_many(self):
        path = self._take_shot()
        if not path:
            return
        self._live = False                     # 画布停在刚拍的这张，取流继续在后台缓存新帧
        self._refresh_cam_label()
        self._refresh_actions()
        self._say("已拍第 %d 张：%s　—　再点【拍多张】接着拍，点【关闭相机】退出。"
                  % (len(self._cam_paths), os.path.basename(path)), "ok")

    def _on_close_camera(self):
        if not self._cam_on and not self._cam_paths:
            self._say("相机没在取流。", "warn")
            return
        self._stop_stream()
        n = len(self._cam_paths)
        self._cam_paths = []
        self.kit.reset_state()
        if n:
            # 拍过图就停在最后一张上接着用（它们已经是磁盘上的图片了）
            self._say("已关闭相机：本次拍了 %d 张，都在 %s　—　可以直接识别 / 保存。"
                      % (n, self._shot_dir()), "ok")
        else:
            self._restore_view()
            self._say("已关闭相机，画布回到原样。", "info")
        self._refresh_cam_label()
        self._refresh_actions()

    def _restore_view(self):
        """画布回到「没打开相机」的样子：有图就显示图，没图就显示提示。"""
        if self.paths and 0 <= self.idx < len(self.paths):
            self._show_current()
        else:
            self._cur_img = None
            self.view.clear_image()
            self.view.set_placeholder(HINT)
        self.kit.refresh()

    def _on_frame(self, img):
        if not self._cam_on:
            return
        if img is None or img.isNull():
            return
        first = self._cam_qimage is None
        self._cam_qimage = img
        # 取景时才刷画布；拍多张的过程中画布停在刚拍的照片上，
        # 但 _cam_qimage 仍要继续更新 —— 否则下一次拍到的还是老一帧
        if self._live and not self._frame_timer.isActive():
            self._frame_timer.start(90)
        if first:
            # 第一帧到了：拍照按钮这一刻才该亮起来
            QTimer.singleShot(0, self._refresh_actions)
            QTimer.singleShot(0, self._refresh_cam_label)

    def _flush_frame(self):
        if not self._cam_on or not self._live or self._cam_qimage is None:
            return
        self.view.set_image(QPixmap.fromImage(self._cam_qimage))

    def _on_stats(self, fps, w, h):
        self._cam_fps = float(fps)
        self._cam_size = (int(w), int(h))
        self._refresh_cam_label()

    def _on_lost(self):
        was = self._cam_on
        self._cam_on = False
        self._live = False
        self._cam_qimage = None
        if was and self._cam_paths:
            # 拍到的东西已经是磁盘上的图片，留着接着用，别因为掉线把画面清掉
            self._say("相机掉线了（取流已停止）；已拍的 %d 张还在。"
                      % len(self._cam_paths), "err")
        elif was:
            self._cur_img = None
            if self.paths:
                self._restore_view()       # 手上还有图片就退回图片，别留个错误画面
            else:
                self.view.clear_image()
                self.view.set_placeholder(
                    "相机已断开　·　检查网线/供电，然后到【设备管理】里重连")
            self._say("相机掉线了（取流已停止）。", "err")
        self._refresh_cam_label()
        self._refresh_actions()

    def _on_grab_error(self, code):
        self._say("取流错误：0x%08X" % (code & 0xFFFFFFFF), "warn")

    def _on_device_changed(self, key):
        if key != "camera":
            return
        connected = self.mgr.is_connected("camera")
        self._refresh_cam_label()
        if self._want_cam:
            if connected:
                self._want_cam = False
                self._on_open_camera()         # 连上了，接着开流
            elif self.mgr.state("camera") != ST_CONNECTING:
                self._want_cam = False
                self._say("相机没连上：到【设备管理】里看一下（被占用 / 网段 / 供电）。", "err")
        if not connected and self._cam_on:
            self._on_lost()
        self._refresh_actions()

    def _refresh_cam_label(self):
        conn = self.mgr.is_connected("camera")
        connecting = self.mgr.state("camera") == ST_CONNECTING
        size = self._cam_size

        if self._cam_on and self._live:
            text = "● 取景中"
            if size[0]:
                text += "　%d × %d　%.1f fps" % (size[0], size[1], self._cam_fps)
            col = theme.OK
        elif self._cam_on:
            text, col = "● 已拍 %d 张　（相机仍在取流）" % len(self._cam_paths), theme.AMBER
        elif conn:
            info = (self.cam.info or {}) if self.cam is not None else {}
            text, col = "● 已连接　%s" % (info.get("model") or "工业相机"), theme.OK
        elif connecting or self._want_cam:
            text, col = "● 正在连接…", theme.WARN
        else:
            text, col = "● 未连接　（到【设备管理】里连接）", theme.BAD
        self.lb_cam.setText(text)
        self.lb_cam.setToolTip(text)
        self.lb_cam.setStyleSheet("color:%s;background:transparent;" % col)

        if self._cam_on and self._live:
            self.btn_cam.setText("取景中…")
        elif self._cam_on:
            self.btn_cam.setText("继续取景")
        else:
            self.btn_cam.setText("打开相机")

    # ================================================================ 图片
    def _on_mode(self, _idx):
        batch = self.cb_mode.currentIndex() == 1
        self.btn_import.setText("导入文件夹" if batch else "导入图片")
        self._refresh_actions()

    def _on_import(self):
        if self._cam_on:
            self._say("相机模式下不能导入图片，先点【关闭相机】。", "warn")
            return
        batch = self.cb_mode.currentIndex() == 1
        start = CAPTURE_DIR if os.path.isdir(CAPTURE_DIR) else ""
        if not start and self.paths:
            start = os.path.dirname(self.paths[0])

        if batch:
            d = QFileDialog.getExistingDirectory(self, "选择图片文件夹", start)
            if not d:
                return
            paths = list(pins_pkg().list_images(d) or [])
            if not paths:
                self._say("这个文件夹里没有支持的图片。", "warn")
                return
        else:
            path, _f = QFileDialog.getOpenFileName(self, "打开图片", start, IMG_EXT)
            if not path:
                return
            paths = [path]

        self.paths = paths
        self.idx = 0
        self.kit.reset_state()
        self._show_current()
        self._say("已导入 %d 张图片。" % len(paths), "ok")

    def _switch(self, d):
        if len(self.paths) < 2 or self._cam_on:
            return
        self.idx = (self.idx + d) % len(self.paths)
        self.kit.reset_state()
        self._show_current()

    def _show_current(self):
        if not self.paths:
            return
        p = self.paths[self.idx]
        img = read_image(p)
        if img is None:
            self._say("读不到图片：%s" % p, "err")
            return
        self._cur_img = img
        key = p
        if key in self.pin_results:
            self._paint(img, self.pin_results[key])
        elif key in self.detections:
            self._paint_boxes(img, self.detections[key])
        else:
            self.view.set_image(img)
            self.view.set_boxes([])
            self.view.set_pins([])
        self.kit.refresh()

        marks = []
        if key in self.detections:
            marks.append("已检测")
        if key in self.pin_results:
            marks.append("已识别引脚")
        tail = ("（%s）" % "、".join(marks)) if marks else "（未检测）"
        self._say("[%d/%d] %s%s" % (self.idx + 1, len(self.paths),
                                    os.path.basename(p), tail), "info")
        self._refresh_actions()

    # ================================================================ 运行
    def _key(self):
        # 只有「取景中」才是没有落盘位置的实时画面；拍下来的都是正经文件路径
        if self._cam_on and self._live:
            return CAMERA_KEY
        if 0 <= self.idx < len(self.paths):
            return self.paths[self.idx]
        return None

    def _style_kw(self):
        return dict(dot_size=int(self.sp_dot.value()),
                    num_size=int(self.sp_num.value()),
                    id_size=int(self.sp_id.value()))

    def _opt_kw(self):
        """增强开关 / GrayBoost 开关 → pin_detect 的 opt（全默认时给 None）。"""
        opt = None
        if not self.chk_enh.isChecked():
            opt = dict(geo_classify=False, symmetrize=False, interp_gaps=False,
                       dense_pitch=False, contrast_boost=False, cross_chip=False)
        if not self.chk_gb.isChecked():
            opt = dict(opt or {})
            opt["grayboost"] = "off"
        return opt

    def _guard(self):
        """能不能开始一次识别：返回 True 才往下走。"""
        if self._pending is not None:
            self._say("上一步还没跑完，等它出结果。", "warn")
            return False
        if self._cam_on and self._live:
            self._say("正在取景：先点【拍单张】或【拍多张】把画面拍成照片，再识别。", "warn")
            return False
        if self._cur_img is None:
            self._say("没有画面：先【导入图片】，或【打开相机】后拍一张。", "warn")
            return False
        return True

    def _set_pending(self, kind, text="正在处理…"):
        self._pending = kind
        if kind == "detect":
            self.btn_run.setText("检测中…")
        elif kind == "pins":
            self.btn_pin.setText("识别中…")
        self.view.show_progress(text)
        self._refresh_actions()

    def _clear_pending(self):
        self._pending = None
        self.view.hide_progress()
        self._refresh_actions()

    def _on_run(self):
        if not self._guard():
            return
        key = self._key()
        self._pending = "detect"
        self._set_pending("detect")
        self._say("正在检测芯片框…", "info")
        self._save_later()
        self.engine.detect(self._cur_img, conf=float(self.sp_conf.value()),
                           iou=0.45, meta={"path": key})

    def _on_pin(self):
        if not self._guard():
            return
        key = self._key()
        if key not in self.detections:
            # 没跑过 YOLO 就先跑一遍，跑完自动接着识别引脚（原工具的做法）
            self._pending = "detect"
            self._set_pending("detect")
            self._say("还没有芯片框，先跑一次检测…", "info")
            self.engine.detect(self._cur_img, conf=float(self.sp_conf.value()),
                               iou=0.45, meta={"path": key, "chain": "pins"})
            return
        self._submit_pins(key)

    def _submit_pins(self, key):
        self._pending = "pins"
        self._set_pending("pins")
        self._say("正在识别引脚…", "info")
        self.engine.pins(self._cur_img, self.detections.get(key) or [],
                         opt=self._opt_kw(), meta={"path": key})

    # ================================================================ 批量
    def _on_run_all(self):
        if len(self.paths) < 2:
            self._say("批量需要先导入一个文件夹。", "warn")
            return
        self._start_batch("detect")

    def _on_pin_all(self):
        if len(self.paths) < 2:
            self._say("批量需要先导入一个文件夹。", "warn")
            return
        self._start_batch("pins")

    def _start_batch(self, kind):
        if self._pending is not None:
            self._say("上一步还没跑完，等它出结果。", "warn")
            return
        self._pending = "batch"
        self._batch = {"kind": kind, "i": 0, "ok": 0, "n": len(self.paths),
                       "img": None}
        self.kit.reset_state()
        self._refresh_actions()
        self.view.show_progress("批量处理…", total=self._batch["n"])
        self._batch_step()

    def _batch_step(self):
        b = self._batch
        if b is None:
            return
        while b["i"] < b["n"]:
            p = self.paths[b["i"]]
            img = read_image(p)
            b["i"] += 1
            if img is None:
                continue
            b["img"] = img
            self.view.set_progress(b["i"] - 1,
                                   text="批量 %d/%d：%s" % (b["i"], b["n"],
                                                           os.path.basename(p)),
                                   total=b["n"])
            meta = {"path": p, "batch": True}
            if b["kind"] == "pins" and p in self.detections:
                self.engine.pins(img, self.detections[p], opt=self._opt_kw(), meta=meta)
            else:
                self.engine.detect(img, conf=float(self.sp_conf.value()),
                                   iou=0.45, meta=meta)
            return
        self._batch_done()

    def _batch_done(self):
        b = self._batch or {"kind": "detect", "ok": 0, "n": 0}
        self._batch = None
        self._pending = None
        self.view.hide_progress()
        what = "检测" if b["kind"] == "detect" else "引脚识别"
        self._say("批量%s完成：成功 %d / %d 张。" % (what, b["ok"], b["n"]), "ok")
        self._show_current()
        self._refresh_actions()

    # ================================================================ 结果
    def _on_done(self, kind, payload, info):
        meta = (info or {}).get("meta") or {}
        sec = float((info or {}).get("sec") or 0.0)
        if kind == "detect":
            self._on_detect_done(payload, meta, sec)
        elif kind == "pins":
            self._on_pins_done(payload, meta, sec)
        else:
            self._on_rerun_done(payload, meta, sec)

    def _on_detect_done(self, boxes, meta, sec):
        key = meta.get("path")
        boxes = boxes or []
        if meta.get("batch"):
            self.detections[key] = boxes
            self.pin_results.pop(key, None)
            if self._batch is None:
                return
            if self._batch["kind"] == "pins":
                # 批量引脚识别：这张图还没检测过，先补检测，随后接着认引脚
                self.engine.pins(self._batch["img"], boxes, opt=self._opt_kw(),
                                 meta={"path": key, "batch": True})
            else:
                self._batch["ok"] += 1
                self._batch_step()
            return

        self.detections[key] = boxes
        self.pin_results.pop(key, None)
        self._pending = None
        self.view.hide_progress()
        if key == self._key() and self._cur_img is not None:
            self.kit.reset_state()
            self._paint_boxes(self._cur_img, boxes)
        if meta.get("chain") == "pins":
            self._submit_pins(key)
            return
        self._clear_pending()
        if not boxes:
            self._say("没检出芯片（%.1fs）：图里没有模型认识的封装，或置信度太高。"
                      "可调低【置信度】再试。" % sec, "warn")
        else:
            self._say("检出芯片框 %d 个（%.1fs）。%s"
                      % (len(boxes), sec, _cls_summary(boxes)), "ok")

    def _on_pins_done(self, chips, meta, sec):
        key = meta.get("path")
        chips = chips or []
        if meta.get("batch"):
            self.pin_results[key] = chips
            if self._batch is not None:
                self._batch["ok"] += 1
                self._batch_step()
            return

        self.pin_results[key] = chips
        self._pending = None
        self.view.hide_progress()
        if key == self._key() and self._cur_img is not None:
            self.kit.reset_state()
            self._paint(self._cur_img, chips)
        self.kit.refresh()
        self._clear_pending()
        n_pin = sum(int(c.get("pin_count", 0) or 0) for c in chips)
        if not chips:
            self._say("没有可识别的芯片框：先点【运行】。", "warn")
        else:
            empty = [c for c in chips if not int(c.get("pin_count", 0) or 0)]
            extra = ("；其中 %d 颗一个脚都没找到" % len(empty)) if empty else ""
            self._say("芯片 %d 个、引脚合计 %d 个（%.1fs）。%s%s"
                      % (len(chips), n_pin, sec, _pin_summary(chips), extra),
                      "warn" if empty else "ok")

    def _on_failed(self, msg, meta):
        meta = meta or {}
        self._pending = None
        self._batch = None
        self.view.hide_progress()
        self.btn_run.setText("运行")
        self.btn_pin.setText("引脚识别")
        self._say("识别失败：%s" % msg, "err")
        self._refresh_actions()

    def _on_model(self, ok, msg):
        if ok:
            self.lb_model_state.setText("已加载")
            self.lb_model_state.setStyleSheet(
                "color:%s;background:transparent;" % theme.OK)
            self.lb_model_state.setToolTip(msg)
        else:
            self.lb_model_state.setText("加载失败")
            self.lb_model_state.setStyleSheet(
                "color:%s;background:transparent;" % theme.BAD)
            self.lb_model_state.setToolTip(msg)
            self._say("模型加载失败：%s" % msg, "err")
        self._show_model_name()

    # ================================================================ 单芯片重跑
    def _on_box_selected(self, idx):
        key = self._key()
        if not key:
            return
        if key in self.pin_results:
            cs = self.pin_results[key]
            if 0 <= idx < len(cs):
                c = cs[idx]
                tag = c.get("id") or ("chip#%d" % (idx + 1))
                self._say("已选中 %s（%s，chip#%d）：%d 个点%s　—　右键框可「单芯片重跑」，"
                          "右键点可删除；画布下方工具条可补点 / 撤回 / 算召回率。"
                          % (tag, c.get("label"), idx + 1,
                             int(c.get("pin_count", 0) or 0),
                             ("　" + c["flags"]) if c.get("flags") else ""), "info")
        elif key in self.detections:
            ds = self.detections[key]
            if 0 <= idx < len(ds):
                self._say("已选中 %s（检测框 #%d，%s）"
                          % (ds[idx].get("id") or "", idx + 1,
                             ds[idx].get("label")), "info")

    def _on_rerun_chip(self, idx):
        key = self._key()
        if not key or self._cur_img is None:
            return
        if key not in self.pin_results:
            self._say("请先点【引脚识别】得到结果，再对单颗芯片重跑。", "warn")
            return
        if self._pending is not None:
            self._say("上一步还在跑，等它出结果。", "warn")
            return
        chips = self.pin_results[key]
        if not (0 <= idx < len(chips)):
            return
        ch = chips[idx]
        x0, y0, x1, y1 = _box4(ch.get("box"))
        self.view.select(idx)
        self.view.show_progress("重跑 chip#%d（新算法）…" % (idx + 1), total=100)
        self._pending = "rerun"
        self._refresh_actions()
        self.engine.rerun(self._cur_img, [x0, y0, x1, y1], ch.get("label"),
                          opt=self._opt_kw(), meta={"path": key, "idx": idx})

    def _on_rerun_done(self, payload, meta, sec):
        chip, detail = payload
        detail = detail or {}
        key = meta.get("path")
        idx = int(meta.get("idx", -1))
        self._pending = None
        self.view.hide_progress()
        self._refresh_actions()
        if chip is None:
            self._say("重跑失败：%s" % detail.get("msg"), "err")
            return
        cur = list(self.pin_results.get(key) or [])
        if not (0 <= idx < len(cur)):
            return
        chip = dict(chip)
        chip["chip_id"] = cur[idx].get("chip_id", idx)
        chip["id"] = cur[idx].get("id")        # 编号跟着位置走，不因重跑而变
        cur[idx] = chip
        pins_pkg().assign_chip_ids(cur)
        self.pin_results[key] = cur
        if key == self._key() and self._cur_img is not None:
            self.view.clear_pin_selection()
            self._paint(self._cur_img, cur, sel=idx)
        self.kit.refresh()
        saved = self._persist_pins(key)

        used = detail.get("used")
        if used == "grayboost":
            msg = ("已重跑 chip#%d：改用新算法（%s）→ %d 个点（主算法原本 %d 个）"
                   % (idx + 1, detail.get("reason"), chip.get("pin_count", 0),
                      detail.get("n_main")))
        elif used == "pin_detect" and detail.get("exact"):
            # 精确解类别（T/F/C/V/L/O + 自定义类如 K）：点数由「类别 × 每排 n」
            # 先验决定，本来就不走候选阶梯。这里不能报"新算法未接管"——没接管
            # 是设计如此，不是候选全军覆没。
            msg = ("已重跑 chip#%d：按 %s 类精确解识别，%d 个点（不走候选阶梯）"
                   % (idx + 1, detail.get("exact"), chip.get("pin_count", 0)))
        elif used == "pin_detect":
            msg = ("已重跑 chip#%d：新算法未接管（%s），保留主算法 %d 个点"
                   % (idx + 1, detail.get("reason") or "无有效候选",
                      chip.get("pin_count", 0)))
        else:
            msg = "已重跑 chip#%d：%d 个点" % (idx + 1, chip.get("pin_count", 0))
        if saved:
            # 写回的是绝对路径，界面上只报文件名，别把整条路径怼到状态栏
            msg += ("　JSON 已更新：%s" % os.path.basename(saved)
                    if os.path.isabs(saved) else "　" + saved)
        self._say(msg, "ok")

    # ================================================================ 绘制
    def _paint(self, img, chips, sel=None):
        """画引脚结果：红点/编号烙在底图上（和原工具一致），框/编号另用矢量层。"""
        kw = self._style_kw()
        disp = render(img, chips, dot_size=kw["dot_size"],
                      num_size=kw["num_size"], id_size=0)
        self.view.set_image(disp)
        self.view.set_tag_level(self.sp_id.value())
        self.view.set_boxes([c.get("box") for c in chips],
                            [c.get("id") for c in chips])
        self.view.set_pins(chips, dot_size=self.sp_dot.value())
        if sel is not None and 0 <= sel < len(chips):
            self.view.select(sel)

    def _paint_boxes(self, img, boxes):
        self.view.set_image(img)
        self.view.set_tag_level(self.sp_id.value())
        self.view.set_boxes([b.get("box") for b in boxes],
                            [b.get("id") for b in boxes])
        self.view.set_pins([])

    def _on_style_changed(self, *_):
        key = self._key()
        if key and self._cur_img is not None:
            if key in self.pin_results:
                sel = self.view.selected()
                self._paint(self._cur_img, self.pin_results[key],
                            sel=(sel if sel >= 0 else None))
            elif key in self.detections:
                self._paint_boxes(self._cur_img, self.detections[key])
            else:
                self.view.set_tag_level(self.sp_id.value())
        self._save_later()

    # ================================================================ 引脚编辑回调
    def _pin_get(self):
        key = self._key()
        return self.pin_results.get(key) if key else None

    def _pin_apply(self, chips):
        key = self._key()
        if not key:
            return
        self.pin_results[key] = chips
        if self._cur_img is not None:
            sel = self.view.selected()
            self._paint(self._cur_img, chips, sel=(sel if sel >= 0 else None))

    def _pin_persist(self):
        key = self._key()
        return self._persist_pins(key)

    def _persist_pins(self, key):
        """把当前结果写回 *_pins.json（相机画面没有落盘位置，只在内存里）。"""
        if not key:
            return ""
        if key == CAMERA_KEY:
            return "（相机画面：改的是内存里的结果，点【保存】再落盘）"
        chips = self.pin_results.get(key)
        if chips is None:
            return ""
        path = os.path.join(os.path.dirname(key),
                            os.path.splitext(os.path.basename(key))[0] + "_pins.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"image": os.path.basename(key), "chips": chips},
                          f, ensure_ascii=False, indent=2)
        except (IOError, OSError) as e:
            return "（写 JSON 失败：%s）" % e
        return path

    def _pin_iter(self):
        """批量召回率的口径：所有已识别过引脚的图。"""
        return [(p, self.pin_results[p]) for p in self.paths
                if p in self.pin_results]

    # ================================================================ 模型
    def _on_pick_model(self):
        start = os.path.dirname(model_path(self._cfg().get("model")))
        path, _f = QFileDialog.getOpenFileName(self, "选择 YOLO 模型", start,
                                               "模型 (*.pt *.onnx)")
        if not path:
            return
        self._cfg()["model"] = path
        self._save_later()
        self._model_tried = True
        self._show_model_name()
        self.lb_model_state.setText("加载中…")
        self.lb_model_state.setStyleSheet("color:%s;background:transparent;" % theme.TXT3)
        self.engine.load_model(path)
        self._say("正在切换模型：%s" % os.path.basename(path), "info")

    # ================================================================ 保存
    def _stem(self, key):
        if key == CAMERA_KEY:
            return "capture_" + _now()
        return os.path.splitext(os.path.basename(key))[0]

    def _save_detect(self, key, outdir, img):
        boxes = self.detections.get(key) or []
        stem = self._stem(key)
        saved = []
        if self.chk_json.isChecked():
            jp = os.path.join(outdir, stem + ".json")
            with open(jp, "w", encoding="utf-8") as f:
                json.dump({"image": os.path.basename(key), "detections": boxes},
                          f, ensure_ascii=False, indent=2)
            saved.append(jp)
        if self.chk_img.isChecked() and img is not None:
            ip = os.path.join(outdir, stem + "_yolo.png")
            ok, _m = write_image(ip, _draw_boxes(img, boxes))
            if ok:
                saved.append(ip)
        return saved

    def _save_pins(self, key, outdir, img):
        chips = self.pin_results.get(key) or []
        stem = self._stem(key)
        saved = []
        if self.chk_pin_json.isChecked():
            jp = os.path.join(outdir, stem + "_pins.json")
            with open(jp, "w", encoding="utf-8") as f:
                json.dump({"image": os.path.basename(key), "chips": chips},
                          f, ensure_ascii=False, indent=2)
            saved.append(jp)
        if self.chk_pin_img.isChecked() and img is not None:
            ip = os.path.join(outdir, stem + "_pins.png")
            kw = self._style_kw()
            ok, _m = write_image(ip, render(img, chips, dot_size=kw["dot_size"],
                                            num_size=kw["num_size"],
                                            id_size=max(1, kw["id_size"])))
            if ok:
                saved.append(ip)
        return saved

    def _ask_dir(self, key):
        start = self._shot_dir()
        if key and key != CAMERA_KEY:
            start = os.path.dirname(key)
        if not os.path.isdir(start):
            start = self._shot_dir()
        d = QFileDialog.getExistingDirectory(self, "选择保存文件夹", start)
        return d or None

    def _on_save(self):
        key = self._key()
        if not key or (key not in self.detections and key not in self.pin_results):
            self._say("当前画面还没有检测 / 识别结果。", "warn")
            return
        if not (self.chk_json.isChecked() or self.chk_img.isChecked()
                or self.chk_pin_json.isChecked() or self.chk_pin_img.isChecked()):
            self._say("四个保存选项都没勾：勾一个再保存。", "warn")
            return
        d = self._ask_dir(key)
        if not d:
            return
        saved = []
        try:
            if key in self.detections:
                saved += self._save_detect(key, d, self._cur_img)
            if key in self.pin_results:
                saved += self._save_pins(key, d, self._cur_img)
        except (IOError, OSError) as e:
            self._say("保存失败：%s" % e, "err")
            return
        if not saved:
            self._say("没有写出文件：检查勾选项。", "warn")
        else:
            self._say("已保存 %d 个文件到 %s" % (len(saved), d), "ok")

    def _on_save_all(self):
        todo = [p for p in self.paths
                if p in self.detections or p in self.pin_results]
        if not todo:
            self._say("还没有任何检测 / 识别结果。", "warn")
            return
        d = self._ask_dir(todo[0])
        if not d:
            return
        n = 0
        for p in todo:
            img = read_image(p)
            try:
                if p in self.detections:
                    n += len(self._save_detect(p, d, img))
                if p in self.pin_results:
                    n += len(self._save_pins(p, d, img))
            except (IOError, OSError) as e:
                self._say("保存 %s 失败：%s" % (os.path.basename(p), e), "err")
        self._say("已保存 %d 张图的结果到 %s" % (len(todo), d), "ok")

    # ================================================================ 杂项
    def _refresh_actions(self):
        busy = self._pending is not None
        cam_on = self._cam_on
        live = self._live
        key = self._key()
        has_img = self._cur_img is not None
        # 取景中的画面是流动的，识别出来的框对不上当前帧 —— 必须先拍下来
        can_run = bool(has_img and not live and not busy)

        self.btn_run.setEnabled(can_run)
        self.btn_pin.setEnabled(can_run)
        if not busy:
            self.btn_run.setText("运行")
            self.btn_pin.setText("引脚识别")

        # 相机：取景中「打开相机」不能重复按；拍过照之后它变「继续取景」
        self.btn_cam.setEnabled((not cam_on) or (not live))
        can_shot = cam_on and self._cam_qimage is not None and not busy
        self.btn_shot1.setEnabled(can_shot)
        self.btn_shot_many.setEnabled(can_shot)
        self.btn_cam_off.setEnabled(cam_on or bool(self._cam_paths))

        self.cb_mode.setEnabled(not cam_on)
        self.btn_import.setEnabled(not cam_on and not busy)
        nav = len(self.paths) > 1 and not cam_on
        self.btn_prev.setEnabled(nav)
        self.btn_next.setEnabled(nav)
        batch = len(self.paths) > 1 and not cam_on
        self.btn_run_all.setEnabled(batch and not busy)
        self.btn_pin_all.setEnabled(batch and not busy)

        has_res = bool(key and (self.detections.get(key)
                                or self.pin_results.get(key)))
        self.btn_save.setEnabled(has_res and not busy)
        self.btn_save_all.setEnabled(
            not busy and any(self.detections.get(p) or self.pin_results.get(p)
                             for p in self.paths))

    def _say(self, text, level="info"):
        colors = {"info": theme.TXT2, "ok": theme.OK,
                  "warn": theme.WARN, "err": theme.BAD}
        self.info.setText(text)
        self.info.setStyleSheet("color:%s;background:transparent;"
                                % colors.get(level, theme.TXT2))

    def shutdown(self):
        try:
            self.engine.shutdown()
        except Exception:
            pass


# ---------------------------------------------------------------- 小工具
def _cls_summary(boxes):
    if not boxes:
        return ""
    c = {}
    for b in boxes:
        c[b["label"]] = c.get(b["label"], 0) + 1
    return "类别分布：" + "，".join("%s×%d" % (k, v) for k, v in sorted(c.items()))


def _pin_summary(chips):
    if not chips:
        return ""
    c = {}
    for ch in chips:
        c[ch["label"]] = c.get(ch["label"], 0) + 1
    return "芯片类别：" + "，".join("%s×%d" % (k, v) for k, v in sorted(c.items()))


def _box4(box):
    """各种写法的框 → (x0, y0, x1, y1) 四个 float。"""
    try:
        if box and hasattr(box[0], "__len__"):
            (x0, y0), (x1, y1) = box[0], box[1]
        else:
            x0, y0, x1, y1 = box
        return float(x0), float(y0), float(x1), float(y1)
    except (TypeError, ValueError, IndexError):
        return 0.0, 0.0, 0.0, 0.0


def _draw_boxes(img, boxes):
    """把检测框画到图上（只在「保存带框图」时用）。"""
    import cv2

    ann = img.copy()
    for b in boxes:
        x0, y0, x1, y1 = [int(round(v)) for v in _box4(b.get("box"))]
        lab = str(b.get("label"))
        col = COLORS[sum(ord(ch) for ch in lab) % len(COLORS)]
        cv2.rectangle(ann, (x0, y0), (x1, y1), col, 2)
        txt = "%s %.2f" % (b.get("id") or lab, float(b.get("conf", 1.0)))
        (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        cv2.rectangle(ann, (x0, max(0, y0 - th - 6)), (x0 + tw + 4, y0), col, -1)
        cv2.putText(ann, txt, (x0 + 2, max(12, y0 - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    return ann
