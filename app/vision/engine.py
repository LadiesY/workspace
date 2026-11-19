# -*- coding: utf-8 -*-
"""引脚识别引擎：把 pintool 下的算法包起来，界面只跟这里打交道。

`pintool\\` 是从「引脚识别工具」（PinTool_portable_20261103）原样复制过来的
算法目录，**一行没改**（和 libs\\ 里的 DLL、MvImport\\ 一样，只是搬了个位置）：

    pintool\\pin_detect.py / pin_c.py / pin_f.py / pin_t.py / pin_l.py / pin_o.py
    pintool\\gray_pin\\gray_boost.py                 低分辨率兜底通道
    pintool\\core\\{pins,yolo_detect,grayboost}.py    封装层（原工具的 core）
    pintool\\weights\\best.pt                        YOLO 芯片检测模型（T / F / O）

**几何畸变校正没有复制**（原工具的 jbjz\\correct.py 与 core\\distortion.py）——
这一版不接几何校正，图上什么样的坐标就出什么样的坐标。

三个动作，和原工具的界面一一对应
--------------------------------
* ``detect``  只跑 YOLO —— 界面上是「运行」，出的是芯片框；
* ``pins``    对已有的芯片框跑引脚识别 —— 界面上是「引脚识别」；
* ``rerun``   单颗芯片重跑（右键菜单那项），换新算法再算一遍这颗。

为什么要跑在后台线程
--------------------
* 加载 YOLO（拉 torch）约 3 秒，一帧 3072×2048 的推理约 3~9 秒；放主线程界面直接假死。
* 一条队列 + 一根线程**串行**执行：模型加载和三种识别不会撞在一起（torch 推理不
  保证线程安全，而且同时跑两个只会互相拖慢）。

结果全用信号回主线程：``done(kind, payload, meta)`` / ``failed(msg, meta)``。
meta 是调用方自己塞进去的标记（哪张图、第几颗芯片、是不是批量），引擎原样带回，
界面靠它把结果对回原来的位置 —— 引擎不持有界面状态，也就不需要什么「当前图」。
"""

import os
import queue
import sys
import threading
import time

import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtGui import QImage

from ..devices.base import BASE_DIR

# cv2 / numpy 在这里（主线程）先导一次再交给后台线程用。
# 踩过的坑：让引擎线程第一次 import cv2 时，正好主线程在跑 Qt 的事件派发，
# Windows 上会偶发 0x8001010d（RPC_E_CANTCALLOUT_ININPUTSYNCCALL）直接崩进程 ——
# 表现是「点识别，窗口没了」。提前导好就不会有这个竞争。
import cv2                                  # noqa: E402,F401  isort:skip

VENDOR_DIR = os.path.join(BASE_DIR, "pintool")
DEFAULT_MODEL = os.path.join(VENDOR_DIR, "weights", "best.pt")
CAPTURE_DIR = os.path.join(BASE_DIR, "data", "captures")

_PKG = None            # 已导入的 core.pins
_YOLO = None           # 已导入的 core.yolo_detect


def _pkg():
    """第一次用到时才把算法包装进来（导入 cv2/numpy 要时间，能省就省）。"""
    global _PKG, _YOLO
    if _PKG is not None:
        return _PKG
    if VENDOR_DIR not in sys.path:
        sys.path.insert(0, VENDOR_DIR)
    # 原工具每次识别会往 core\\gb_debug.log 写决策日志，那是它自己排查用的；
    # 这里默认关掉，别在程序目录里堆日志。想看得把环境变量设成 1。
    os.environ.setdefault("PINTOOL_GB_DEBUG", "0")
    from core import pins as _pins
    from core import yolo_detect as _yolo
    _PKG, _YOLO = _pins, _yolo
    return _PKG


def pins_pkg():
    """给界面层（画布的引脚编辑工具条）用的 pins 模块。"""
    return _pkg()


def model_path(cfg_path=""):
    """配置里填了就用自己的，没填（或文件不在了）就用包里带的那个。"""
    p = str(cfg_path or "").strip()
    if p and os.path.isfile(p):
        return p
    return DEFAULT_MODEL


def qimage_to_bgr(img):
    """QImage → cv2 BGR。相机可能是 Mono8（灰度），也可能是 RGB888。"""
    import cv2

    if img is None or img.isNull():
        return None
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    if w <= 0 or h <= 0 or bpl <= 0:
        return None

    if img.format() == QImage.Format_Grayscale8:
        buf = img.constBits().asstring(bpl * h)
        arr = np.frombuffer(buf, np.uint8).reshape(h, bpl)[:, :w]
        return np.ascontiguousarray(cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR))

    rgb = img.convertToFormat(QImage.Format_RGB888)
    bpl = rgb.bytesPerLine()
    buf = rgb.constBits().asstring(bpl * h)
    arr = np.frombuffer(buf, np.uint8).reshape(h, bpl)[:, :w * 3]
    arr = arr.reshape(h, w, 3)
    return np.ascontiguousarray(cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))


def bgr_to_qimage(bgr):
    """cv2 BGR → QImage（画布显示、标注图预览时用）。"""
    if bgr is None:
        return None
    rgb = bgr[:, :, ::-1].copy()
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, w * 3, QImage.Format_RGB888).copy()


def read_image(path):
    """读图。走 imdecode：cv2.imread 在 Windows 上遇到中文路径直接返回 None。"""
    import cv2
    if not path or not os.path.isfile(path):
        return None
    try:
        data = np.fromfile(path, np.uint8)
    except (IOError, OSError):
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def write_image(path, bgr):
    """写图，同样走 imencode（中文路径）。返回 (ok, 说明)。"""
    import cv2
    ext = os.path.splitext(path)[1] or ".png"
    ok, buf = cv2.imencode(ext, bgr)
    if not ok:
        return False, "编码失败：" + ext
    try:
        buf.tofile(path)
    except (IOError, OSError) as e:
        return False, str(e)
    return True, path


def render(img, chips, dot_size=2, num_size=2, id_size=4):
    """画标注图（绿框 + 红点 + 黄编号）。只在「保存带框图」时用 ——
    屏幕上的叠加是矢量画的，缩放不糊，也不该把编号烙进像素里两次。"""
    return _pkg().render_annotated(img, chips, dot_size=dot_size,
                                   num_size=num_size, id_size=id_size)


class PinEngine(QObject):
    """识别引擎。所有方法都只是把活派给后台线程，立刻返回。

    kind 只有三个值："detect" / "pins" / "rerun"，界面按它分派结果。
    """

    stage = pyqtSignal(str)                  # 阶段提示（改状态文字用）
    model_ready = pyqtSignal(bool, str)      # (成功?, 说明)
    done = pyqtSignal(str, object, object)   # (kind, 结果, meta)
    failed = pyqtSignal(str, object)         # (说明, meta)

    def __init__(self, parent=None):
        super(PinEngine, self).__init__(parent)
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._busy = False
        self._model = None               # YoloRunner
        self._model_info = ""            # 已加载模型的说明（类别、文件名）
        self._model_path = ""

        self._thread = threading.Thread(target=self._loop, name="PinEngine")
        self._thread.daemon = True
        self._thread.start()

    # ---------------------------------------------------------------- 外部
    @property
    def loaded(self):
        return self._model is not None

    @property
    def busy(self):
        return self._busy or not self._q.empty()

    @property
    def model_info(self):
        return self._model_info

    def load_model(self, path):
        self._q.put(("model", model_path(path)))

    def detect(self, bgr, conf=0.25, iou=0.45, meta=None):
        """只跑 YOLO，出芯片框。bgr 是 cv2 的 BGR ndarray。"""
        self._q.put(("detect", (bgr, float(conf), float(iou), meta)))

    def pins(self, bgr, boxes, opt=None, meta=None):
        """对给定芯片框做引脚识别。boxes 与 detect 的输出同构。"""
        self._q.put(("pins", (bgr, boxes or [], opt, meta)))

    def rerun(self, bgr, box, label, opt=None, meta=None):
        """单颗芯片重跑（右键菜单）。"""
        self._q.put(("rerun", (bgr, box, label, opt, meta)))

    def cancel_pending(self):
        """丢掉还没开始跑的活（比如连点两下）。正在跑的那一条跑完为止。"""
        n = 0
        while True:
            try:
                self._q.get_nowait()
                n += 1
            except queue.Empty:
                break
        return n

    def shutdown(self):
        self._stop.set()
        try:
            self._q.put(("__stop__", None))
        except Exception:
            pass

    # ---------------------------------------------------------------- 线程
    def _loop(self):
        while not self._stop.is_set():
            try:
                kind, payload = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            if kind == "__stop__":
                break
            self._busy = True
            try:
                if kind == "model":
                    self._do_model(payload)
                elif kind == "detect":
                    self._do_detect(payload)
                elif kind == "pins":
                    self._do_pins(payload)
                else:
                    self._do_rerun(payload)
            except Exception as e:                       # 算法里没兜住的异常
                meta = payload[3] if kind in ("detect", "pins") else (
                    payload[4] if kind == "rerun" else None)
                self.failed.emit(_human(e), meta)
            finally:
                self._busy = False

    def _do_model(self, path):
        if self._model is not None and path == self._model_path:
            self.model_ready.emit(True, self._model_info)
            return
        self.stage.emit("正在加载识别模型…")
        _pkg()
        runner = _YOLO.YoloRunner()
        names = runner.load(path)
        kinds = "/".join(sorted(str(v) for v in (names or {}).values())) or "未知"
        self._model = runner
        self._model_path = path
        self._model_info = "%s · 类别 %s" % (os.path.basename(path), kinds)
        self.stage.emit("模型已就绪（%s）" % self._model_info)
        self.model_ready.emit(True, self._model_info)

    def _need_model(self):
        """没手动选过模型就用包里带的那个，不让用户卡在「没模型」。"""
        if self._model is None:
            self._do_model(DEFAULT_MODEL)

    def _do_detect(self, payload):
        img, conf, iou, meta = payload
        if img is None:
            raise ValueError("这一帧读不出像素（图像为空或格式不认识）")
        self._need_model()
        name = _meta_name(meta)
        self.stage.emit("正在检测 %s（%d × %d）…" % (name, img.shape[1], img.shape[0]))
        t0 = time.time()
        boxes = self._model.run(img, conf=conf, iou=iou) or []
        if boxes:
            _pkg().assign_chip_ids(boxes)     # 检测框也编号（T_1 / F_2…）
        self.done.emit("detect", boxes, {"meta": meta, "sec": time.time() - t0})

    def _do_pins(self, payload):
        img, boxes, opt, meta = payload
        if img is None:
            raise ValueError("这一帧读不出像素（图像为空或格式不认识）")
        self.stage.emit("正在识别 %s 的引脚…" % _meta_name(meta))
        t0 = time.time()
        chips = _pkg().detect_from_yolo(img, boxes, opt=opt)[0] if boxes else []
        self.done.emit("pins", chips, {"meta": meta, "sec": time.time() - t0})

    def _do_rerun(self, payload):
        img, box, label, opt, meta = payload
        if img is None:
            raise ValueError("这一帧读不出像素（图像为空或格式不认识）")
        self.stage.emit("正在重跑芯片…")
        t0 = time.time()
        chip, detail = _pkg().rerun_chip(img, box, label, opt=opt)
        self.done.emit("rerun", (chip, detail), {"meta": meta, "sec": time.time() - t0})


def _meta_name(meta):
    """meta 里的路径换成给人看的名字（日志/状态文字用）。"""
    if isinstance(meta, dict):
        p = meta.get("path") or ""
        if p:
            return os.path.basename(p)
    return "当前画面"


def _human(e):
    """把异常翻成人话：算法层的报错常常只有类型名，光看那个没法排查。"""
    msg = str(e).strip()
    if isinstance(e, ImportError):
        return "算法依赖缺失：%s" % msg
    if isinstance(e, MemoryError):
        return "内存不够：这一帧太大了，先缩小分辨率或 ROI 再识别"
    if isinstance(e, FileNotFoundError):
        return "找不到文件：%s" % msg
    return msg or e.__class__.__name__
