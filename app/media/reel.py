# -*- coding: utf-8 -*-
"""宣传片解码：QThread 里用 OpenCV 循环读帧，转成 QImage 抛给界面。

为什么不用 QMediaPlayer / QVideoWidget：

* 现场机是**离线**的，Qt 的 multimedia 后端（ffmpeg / directshow 插件）不一定装全，
  缺一个插件就是黑屏，而且不给任何提示 —— 宣传页黑着比没有更糟；
* ``cv2`` 本来就是本项目的硬依赖（视觉定位在用），这条解码路是现成的；
* 自己解帧能精确控制"缩到多大再抛"：源是 1600×900，直接丢给界面缩放要多花一次
  全尺寸采样。

几条必须守住的约束：

* **缩放在解码线程里做完**，而且直接缩到界面要的像素尺寸（``set_target``）。
  界面拿到就 1:1 贴上去，不再重采样 —— 一张 1280×720 的图 Qt 每帧缩一次，
  25 fps 下足够吃掉小半个核。
* **等界面画完再解下一帧**（``_ack``）。不做背压的话，界面偶尔卡一下，
  事件队列里就堆一串过期帧，内存和 CPU 一起涨，画面还越来越滞后。
* **片源帧率高于输出帧率时要丢帧**。以前是"读一帧抛一帧、按输出帧率 sleep"，
  片源 30 fps、输出 20 fps 就等于**慢放到 0.66 倍速**（19 s 的片子放 29 s）。
  现在按 ``src_fps / out_fps`` 的比例每次多 grab 掉零头，播放速度和墙上时钟一致。
* **按目标帧率 sleep**，不是能解多快抛多快。
* ``stop()`` 之后必须 ``wait()``：窗口关掉线程还在读文件，退出时会挂住。
* 解码失败只发 ``failed`` 信号，**不抛异常**——宣传片读不出来不该把整个窗口带走。
"""

import threading
import time

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QImage


class ReelDecoder(QThread):
    """循环播放一个 mp4，解码后按帧抛 ``QImage``。"""

    frame = pyqtSignal(QImage)      # 一帧画面（已缩放到界面要的尺寸）
    pos = pyqtSignal(float)         # 当前播放位置（秒），跟着帧一起报
    loaded = pyqtSignal(float)      # 打开成功，参数是总时长（秒）
    looped = pyqtSignal()           # 播到结尾，回到第一帧
    failed = pyqtSignal(str)        # 打不开 / 解码炸了

    def __init__(self, path, fps=20, size=(1280, 720), parent=None):
        super(ReelDecoder, self).__init__(parent)
        self._path = path
        self._fps = float(fps)
        self._size = size
        self._alive = True
        self._paused = False
        self._ack = threading.Event()
        self._ack.set()

    # ---------------------------------------------------------------- 控制
    def set_target(self, w, h):
        """界面尺寸变了就调这里，不用重启解码器。"""
        w, h = max(64, int(w)), max(36, int(h))
        if (w, h) != self._size:
            self._size = (w, h)

    def stop(self):
        """让 run() 退出并等它真的结束。窗口关闭时必须调。"""
        self._alive = False
        self._ack.set()
        self.wait(3000)

    def set_paused(self, on):
        """页面切走时暂停，别在后台白烧 CPU。"""
        self._paused = bool(on)
        if not on:
            self._ack.set()

    def ack(self):
        """界面画完一帧后回执，解码线程才解下一帧（背压）。"""
        self._ack.set()

    # ---------------------------------------------------------------- 主循环
    def run(self):
        cap = None
        try:
            import cv2

            cap = cv2.VideoCapture(self._path)
            if not cap.isOpened():
                self.failed.emit("视频打不开：%s" % self._path)
                return

            src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            if total > 0:
                self.loaded.emit(float(total) / max(src_fps, 1.0))

            out_fps = max(1.0, min(self._fps, src_fps))
            step = 1.0 / out_fps
            # 播放进度用**墙上时钟**做基准：每抛一帧就补一次，落后多少帧 grab 掉多少。
            # 光靠"按输出帧率 sleep"是不够的 —— 界面画一帧要时间，实际输出只有
            # 12~15 fps，片源却有 30 fps，于是整段片子被拖成 0.7 倍速。
            t0_play = time.time()
            idx = 0                     # 已经消费掉的源帧数（读到的 + 跳过的）

            while self._alive:
                if self._paused:
                    self.msleep(100)
                    t0_play = time.time() - idx / max(src_fps, 1.0)
                    continue
                # 上一帧还没画完就等着，别把队列堆起来
                if not self._ack.wait(0.15):
                    continue

                t0 = time.time()
                ok, bgr = cap.read()
                if not ok:
                    # 到结尾了：回到第一帧接着放，宣传片是循环展示用的
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    idx = 0
                    t0_play = time.time()
                    self.looped.emit()
                    continue
                idx += 1

                w, h = self._size
                if bgr.shape[1] != w or bgr.shape[0] != h:
                    bgr = cv2.resize(bgr, (w, h), interpolation=cv2.INTER_AREA)
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                # copy() 是必须的：rgb 的缓冲下一帧就被覆盖，不拷贝会花屏
                img = QImage(rgb.data, w, h, rgb.strides[0],
                             QImage.Format_RGB888).copy()
                self._ack.clear()
                self.frame.emit(img)
                # 位置按帧号算，别用墙钟：解码有快有慢、暂停、循环都会把墙钟带偏，
                # 章节标签就会跟画面对不上（实测差三四秒）
                self.pos.emit(idx / max(src_fps, 1.0))

                # 落后墙上时钟多少帧就丢多少（grab 只挪指针不解码，比 read 便宜）
                want = (time.time() - t0_play) * src_fps
                n = min(int(want - idx), 45)          # 卡顿后最多一次追 1.5 s
                for _ in range(max(0, n)):
                    if not cap.grab():
                        break
                    idx += 1

                left = step - (time.time() - t0)
                if left > 0:
                    self.msleep(int(left * 1000))
        except Exception as exc:                     # 驱动 / 编解码 / 显存都可能炸
            self.failed.emit("%s: %s" % (type(exc).__name__, exc))
        finally:
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass
