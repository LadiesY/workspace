# -*- coding: utf-8 -*-
"""自动测试的一次「视觉会话」：建目录 → 3 秒计时 → 拍照 → YOLO → 引脚识别。

【视觉启动】按下去之后，所有跟"图"有关的事都在这里，界面只管把信号接到日志和画布上：

    1. 建目录      <项目保存地址>/<板号.检修状态.序号>/ + 四个子目录
                   （相机照片 / 引脚json / 日志 / 测试结果，见 app/project.py）
    2. 倒计时      3 秒，让人把手从台面上收回来，别拍进半边手
    3. 拍照        相机取流抓一帧；没接相机时退到离线样图（虚拟测试用）
                   → <会话>/相机照片/capture_20261106_174205.png
    4. YOLO        用项目选的模型识别矩形框（.pt），进度条走时间估算
    5. 引脚识别    对上面那些框逐个找引脚（pintool 的 pin_detect）

跑完之后**不写引脚 json** —— 那是人工检查过、点【保存】时才写的事（用户定的流程：
识别完先让人看框对不对，可以补点 / 改框 / 改标签名，确认没问题才落盘）。

为什么进度是"估算"的
--------------------
两张图都是**一次推理**：YOLO 一帧 3~9 秒，引脚识别一次吃完所有框（`cross_chip`
默认开着，按整板邻框信息算，拆成逐个芯片跑会改结果）。算法层没有中间回调，
所以进度条按**已经过去的时间**往上爬，封顶 95%，真出结果时直接跳 100%。
爬的时间常数取实测值（YOLO ~1.6%/120ms ≈ 7 秒），慢一点比"卡在 99% 不动"好。

拍照为什么不用阻塞等待
----------------------
相机出帧在取流线程，主线程只能等信号。这里用 100 ms 的 QTimer 轮询"有没有新帧"，
超时 8 秒就报失败 —— 阻塞写在主线程会让整个窗口假死，而这 8 秒里用户可能正想按急停。
"""

import glob
import os
import time
from datetime import datetime

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from ..devices.base import BASE_DIR, ST_CONNECTED
from ..project import SUB_DIRS
from . import engine as veng

# 子目录名统一从 project.SUB_DIRS 取，别在两处各写一遍（改一处不会两边不一致）
SUB_PHOTO, SUB_PINS, SUB_LOG, SUB_RESULT = SUB_DIRS

COUNTDOWN = 3                 # 点【视觉启动】后等几秒再拍
GRAB_TIMEOUT = 8.0            # 取流开始后等第一帧的上限（秒）
POLL_MS = 100                 # 等帧轮询间隔
RAMP_MS = 120                 # 进度条节拍
DETECT_RAMP = 1.6             # 每拍 +1.6% → 约 7 秒爬到 95%
PINS_RAMP = 2.4               # 引脚识别通常更快，爬得急一点
RAMP_CAP = 95.0               # 估算进度最高到 95，剩下 5 留给"真出结果"

# 离线样图：没接相机时拿它顶一下（虚拟测试 / 演示）。环境变量优先，
# 方便临时指一张图，不用往程序目录里塞文件。
SAMPLE_DIR = os.path.join(BASE_DIR, "data", "samples")
SAMPLE_ENV = "PCB_SAMPLE_IMAGE"
SAMPLE_EXT = (".png", ".jpg", ".jpeg", ".bmp")


def sample_image():
    """离线样图的路径；没有就返回空串。"""
    p = str(os.environ.get(SAMPLE_ENV) or "").strip()
    if p and os.path.isfile(p):
        return p
    if not os.path.isdir(SAMPLE_DIR):
        return ""
    files = []
    for ext in SAMPLE_EXT:
        files += glob.glob(os.path.join(SAMPLE_DIR, "*" + ext))
    return sorted(files)[-1] if files else ""


class VisionSession(QObject):
    """一次视觉会话。**一个项目可以跑很多次**，每次 `start()` 都开一个新目录。

    信号（界面只连这些，不碰内部状态）：

        stage(文字, 级别)          每一步的说明，直接进运行日志
        progress(步骤, 百分比, 文字) 步骤 ∈ countdown / grab / detect / pins / fail
        photo(路径, BGR)           拍完的照片（画布显示它）
        boxes(检测框, 秒)          YOLO 结果
        chips(引脚结果, 秒)        引脚识别结果 —— 这时可以人工检查了
        failed(说明)              没跑完；照片可能已经存下来了，别当什么都没发生
        finished(是否跑完)          无论成败都会发，界面据此恢复按钮
    """

    stage = pyqtSignal(str, str)
    progress = pyqtSignal(str, int, str)
    photo = pyqtSignal(str, object)
    boxes = pyqtSignal(object, float)
    chips = pyqtSignal(object, float)
    failed = pyqtSignal(str)
    finished = pyqtSignal(bool)

    def __init__(self, engine, parent=None):
        super(VisionSession, self).__init__(parent)
        self._engine = engine

        # 本次会话的产物（界面保存时要用）
        self.session_dir = ""
        self.stem = ""
        self.photo_path = ""
        self.json_dir = ""
        self.img = None                  # 照片的 BGR（编辑时重绘用）
        # 注意：不能叫 self.boxes / self.chips —— 那是下面两个 pyqtSignal 的名字，
        # 一赋值就把信号屏蔽成普通 list，界面 connect 时会报
        # "'list' object has no attribute 'connect'"（踩过一次）。
        self.detected = []               # YOLO 的矩形框
        self.found = []                  # 引脚识别结果

        self._busy = False
        self._step = ""
        self._token = 0                  # 每次 start 加一，用来丢掉上一轮迟到的结果
        self._proj = None
        self._cam = None
        self._conf = 0.25
        self._iou = 0.45
        self._left = 0
        self._pct = 0.0
        self._t0 = 0.0
        self._frame = None
        self._started_stream = False
        self._deadline = 0.0

        self._cd = QTimer(self)
        self._cd.setInterval(1000)
        self._cd.timeout.connect(self._tick_countdown)

        self._ramp = QTimer(self)
        self._ramp.setInterval(RAMP_MS)
        self._ramp.timeout.connect(self._tick_ramp)

        self._wait = QTimer(self)
        self._wait.setInterval(POLL_MS)
        self._wait.timeout.connect(self._poll_frame)

        engine.done.connect(self._on_done)
        engine.failed.connect(self._on_failed)
        engine.stage.connect(self._on_engine_stage)

    # ================================================================ 状态
    @property
    def busy(self):
        return self._busy

    @property
    def step(self):
        return self._step

    # ================================================================ 开始
    def start(self, proj, camera=None, conf=0.25, iou=0.45, when=None):
        """开跑。返回 False 表示没跑起来（原因已经用 failed 说过了）。

        建目录这一步**先做**：地址不可写要在倒计时之前就报出来，
        别让人对着 3 秒的倒计时等，最后才说存不了。
        """
        if self._busy:
            return False
        try:
            session = proj.ensure_session(when)
        except Exception as e:
            self.failed.emit("建不了本次测试目录：%s" % e)
            return False

        now = datetime.now()
        self._busy = True
        self._step = "countdown"
        self._token += 1
        self._proj = proj
        self._cam = camera
        self._conf = float(conf)
        self._iou = float(iou)
        self.session_dir = session
        self.stem = "capture_" + now.strftime("%Y%m%d_%H%M%S")
        self.photo_path = os.path.join(session, SUB_PHOTO, self.stem + ".png")
        self.json_dir = os.path.join(session, SUB_PINS)
        self.img = None
        self.detected = []
        self.found = []

        self._log_line("=== 视觉启动 %s ===" % now.strftime("%Y-%m-%d %H:%M:%S"))
        self._log_line("会话目录：%s" % session)
        for sub in SUB_DIRS:
            self._log_line("  子目录：%s" % sub)
        self.stage.emit("本次测试目录已建好（板号.检修状态.序号）：%s"
                        % os.path.basename(session), "ok")
        self.stage.emit("　四个子目录：%s" % "、".join(SUB_DIRS), "info")

        self._left = COUNTDOWN
        self.stage.emit("%d 秒后自动拍照（%s）" % (COUNTDOWN, self.stem), "info")
        self.progress.emit("countdown", 0, "%d 秒后自动拍照…" % COUNTDOWN)
        self._cd.start()
        return True

    def cancel(self):
        """半路取消（换项目、急停、关页）。迟到的识别结果会被 token 丢掉。"""
        if not self._busy:
            return
        self._token += 1
        self._stop_timers()
        self._release_camera()
        self._busy = False
        self._step = ""
        self.stage.emit("视觉流程已取消。", "warn")
        self._log_line("已取消")
        self.finished.emit(False)

    # ================================================================ 倒计时
    def _tick_countdown(self):
        self._left -= 1
        if self._left > 0:
            pct = int(100.0 * (COUNTDOWN - self._left) / COUNTDOWN)
            self.progress.emit("countdown", pct, "%d 秒后自动拍照…" % self._left)
            return
        self._cd.stop()
        self._begin_grab()

    # ================================================================ 拍照
    def _begin_grab(self):
        self._step = "grab"
        self.progress.emit("grab", 5, "正在拍照…")
        cam = self._cam
        if cam is not None and getattr(cam, "state", "") == ST_CONNECTED:
            self._log_line("相机已连接：取流抓一帧")
            if self._begin_camera(cam):
                return
            self._fail("相机取流失败：这 8 秒里没能拿到一帧。检查相机是否被别的程序占着，"
                       "或网线/供电是否掉了。")
            return
        self._from_sample()

    def _begin_camera(self, cam):
        """开始等相机出帧。返回 False = 连取流都起不来。"""
        self._frame = None
        try:
            cam.bridge.frame.connect(self._on_frame)
        except Exception:
            return False
        if not cam.streaming:
            ok, msg = cam.start_stream()
            if not ok:
                self._unhook_frame(cam)
                self._log_line("取流失败：%s" % msg)
                return False
            self._started_stream = True
        self._deadline = time.time() + GRAB_TIMEOUT
        self.progress.emit("grab", 20, "等待相机出图…")
        self._wait.start()
        return True

    def _on_frame(self, img):
        # 只留第一帧：后面还在出图也无所谓，抓拍就是抓"开始那一刻"的那张
        if self._frame is None:
            self._frame = img

    def _poll_frame(self):
        if self._frame is not None:
            self._wait.stop()
            frame, self._frame = self._frame, None
            self._release_camera()
            img = veng.qimage_to_bgr(frame)
            if img is None:
                self._fail("相机给了帧，但转不成 BGR（像素格式不认识）。")
                return
            self._log_line("已抓到一帧 %d×%d" % (img.shape[1], img.shape[0]))
            self._save_photo(img)
            return
        if time.time() > self._deadline:
            self._wait.stop()
            self._release_camera()
            self._fail("等相机出图超时（%.0f 秒）：帧一直没来。" % GRAB_TIMEOUT)

    def _release_camera(self):
        """退出取流并断开帧信号。只停自己开的流，别人开着的别动。"""
        cam = self._cam
        if cam is None:
            return
        self._unhook_frame(cam)
        if self._started_stream:
            self._started_stream = False
            try:
                cam.stop_stream()
            except Exception:
                pass

    def _unhook_frame(self, cam):
        try:
            cam.bridge.frame.disconnect(self._on_frame)
        except Exception:
            pass

    def _from_sample(self):
        """相机没连：用离线样图顶（只为了把流程跑通，日志里会写明）。"""
        p = sample_image()
        img = veng.read_image(p) if p else None
        if img is None:
            self._fail("相机未连接，也没有离线样图（放一张到 data/samples/，"
                       "或设环境变量 %s）。接上相机再点【视觉启动】。" % SAMPLE_ENV)
            return
        self.stage.emit("相机未连接：本次用离线样图 %s（虚拟测试）"
                        % os.path.basename(p), "warn")
        self._log_line("相机未连接，改用离线样图 %s" % p)
        self._save_photo(img)

    def _save_photo(self, img):
        ok, msg = veng.write_image(self.photo_path, img)
        if not ok:
            self._fail("照片存不下来：%s" % msg)
            return
        self.img = img
        self.progress.emit("grab", 100, "照片已保存")
        self.photo.emit(self.photo_path, img)
        name = os.path.basename(self.photo_path)
        self.stage.emit("第 1 步完成：照片已存到 相机照片\\%s（%d×%d）"
                        % (name, img.shape[1], img.shape[0]), "ok")
        self._log_line("照片：%s" % self.photo_path)
        self._begin_detect()

    # ================================================================ YOLO
    def _begin_detect(self):
        self._step = "detect"
        self._pct = 0.0
        self._t0 = time.time()
        model = getattr(self._proj, "yolo", "") or ""
        self.stage.emit("第 2 步：YOLO 识别矩形框（模型 %s）…"
                        % (os.path.basename(model) if model else "程序自带 best.pt"),
                        "info")
        self.progress.emit("detect", 0, "YOLO 识别中…")
        try:
            self._engine.load_model(model)      # 队列串行：加载完才轮到下面的 detect
            self._engine.detect(self.img, conf=self._conf, iou=self._iou,
                                meta={"flow": self._token,
                                      "path": self.photo_path})
        except Exception as e:
            self._fail("YOLO 没能开始：%s" % e)
            return
        self._ramp.start()

    def _begin_pins(self):
        self._step = "pins"
        self._pct = 0.0
        self._t0 = time.time()
        self.stage.emit("第 3 步：引脚识别（%d 个矩形框）…" % len(self.detected), "info")
        self.progress.emit("pins", 0, "引脚识别中…")
        try:
            self._engine.pins(self.img, self.detected, opt=None,
                              meta={"flow": self._token, "path": self.photo_path})
        except Exception as e:
            self._fail("引脚识别没能开始：%s" % e)
            return
        self._ramp.start()

    # ================================================================ 进度
    def _tick_ramp(self):
        step = DETECT_RAMP if self._step == "detect" else PINS_RAMP
        self._pct = min(RAMP_CAP, self._pct + step)
        sec = time.time() - self._t0
        word = "YOLO 识别中" if self._step == "detect" else "引脚识别中"
        self.progress.emit(self._step, int(self._pct), "%s…　%.1fs" % (word, sec))

    # ================================================================ 引擎回调
    def _on_engine_stage(self, text):
        """引擎的阶段话（加载模型 / 正在检测…）也进日志，排查时有用。"""
        if self._busy:
            self.stage.emit(text, "info")

    def _on_done(self, kind, payload, info):
        meta = (info or {}).get("meta") or {}
        if kind not in ("detect", "pins"):
            return                          # 单芯片重跑归页面管，这里不插手
        if meta.get("flow") != self._token:
            return                          # 上一轮迟到的结果
        sec = float((info or {}).get("sec") or 0.0)
        if kind == "detect":
            self._ramp.stop()
            self.detected = list(payload or [])
            if not self.detected:
                self._fail("YOLO 一个矩形框都没检出（%.1fs）：这块板可能不在这个模型的认识"
                           "范围里。换项目选的模型，或用编辑栏的【矩形框】手工补框。" % sec)
                return
            self.progress.emit("detect", 100, "YOLO：%d 个矩形框" % len(self.detected))
            self.boxes.emit(self.detected, sec)
            self.stage.emit("第 2 步完成：检出 %d 个矩形框（%.1fs）：%s"
                            % (len(self.detected), sec, _labels(self.detected)), "ok")
            self._begin_pins()
            return

        self._ramp.stop()
        self.found = list(payload or [])
        n_pin = sum(int(c.get("pin_count") or 0) for c in self.found)
        self.progress.emit("pins", 100, "引脚识别完成")
        self.chips.emit(self.found, sec)
        self.stage.emit("第 3 步完成：%d 个框 / %d 个引脚（%.1fs）"
                        % (len(self.found), n_pin, sec), "ok")
        self._log_line("引脚识别：%d 框 / %d 点" % (len(self.found), n_pin))
        self._step = ""
        self._busy = False
        self.stage.emit("识别完成，请人工检查矩形框（可拖角点 / 改标签 / 补点），"
                        "确认后点【保存】。", "warn")
        self.finished.emit(True)

    def _on_failed(self, msg, meta):
        if not self._busy:
            return
        m = meta if isinstance(meta, dict) else {}
        if m and m.get("flow") not in (None, self._token):
            return
        self._fail("识别失败：%s" % msg)

    # ================================================================ 收尾
    def _fail(self, msg):
        self._stop_timers()
        self._release_camera()
        self._busy = False
        self._step = ""
        self.progress.emit("fail", 100, "未完成")
        self.stage.emit(msg, "err")
        self._log_line("失败：%s" % msg)
        self.failed.emit(msg)
        self.finished.emit(False)

    def _stop_timers(self):
        for t in (self._cd, self._ramp, self._wait):
            try:
                t.stop()
            except Exception:
                pass

    def _log_line(self, text):
        """往 <会话>/日志/视觉流程.log 追一行。

        写不动就算了 —— 日志写失败不该让整条流程停下来。
        """
        if not self.session_dir:
            return
        p = os.path.join(self.session_dir, SUB_LOG, "视觉流程.log")
        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write("%s  %s\n" % (time.strftime("%H:%M:%S"), text))
        except (IOError, OSError):
            pass


def _labels(boxes):
    """检测框的类别统计，如「T 8、F 2、O 1」。"""
    per = {}
    order = []
    for b in boxes:
        k = str(b.get("label") or "?")
        if k not in per:
            per[k] = 0
            order.append(k)
        per[k] += 1
    return "、".join("%s %d" % (k, per[k]) for k in order)
