# -*- coding: utf-8 -*-
"""运动指令的后台执行器。

为什么要单独一个线程：

1. DLL 调用是阻塞的。探针回零最长会等 120 秒（home_full 里轮询到成功为止），
   放主线程跑就是界面假死 —— 急停按钮点不动，那这个按钮就没意义了。
2. **同一张卡不能并发调用**。KZQ 的实测记录里，状态轮询线程和点动线程各调
   各的 DLL 时，get_pos 会偶发读回 0；位置一错，限位判断就是废的。
   所以这里只有一根线程：轮询和指令排队串行跑，同一时刻只有一个调用在飞。

急停是唯一故意绕开队列的东西：它直接在调用方线程里发停止指令。
排在一个 120 秒的回零后面等，那就不叫急停了。
"""

import queue
import threading

from PyQt5.QtCore import QObject, pyqtSignal


class MotionRunner(QObject):
    """一个设备一根线程：轮询状态 + 串行执行排队来的指令。"""

    tick = pyqtSignal(dict)                 # 一次状态快照（driver.read_state()）
    finished = pyqtSignal(str, bool, str)   # (指令说明, 是否成功, 说明)
    stage = pyqtSignal(str)                 # 长流程的中间进度（回零分几个阶段）

    def __init__(self, key, driver, period=0.2, parent=None):
        super(MotionRunner, self).__init__(parent)
        self.key = key
        self.driver = driver
        self._period = float(period)
        self._q = queue.Queue()
        self._active = threading.Event()
        self._stop = threading.Event()
        self._thread = None

    # ---------------------------------------------------------------- 生命周期
    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop,
                                        name="motion-%s" % self.key)
        self._thread.daemon = True
        self._thread.start()

    def shutdown(self):
        self._stop.set()
        self._active.set()                  # 唤醒挂在 get(timeout) 上的循环

    def set_active(self, on):
        """只有这个页面真的在前台时才轮询 —— 切走就别再占着卡。"""
        if on:
            self._active.set()
        else:
            self._active.clear()

    # ---------------------------------------------------------------- 指令
    def submit(self, desc, fn):
        """fn(driver) -> (ok, msg)，在后台线程里执行。"""
        self._q.put((desc, fn))

    def estop(self):
        """跳过队列直接发停止。返回 (ok, msg)。"""
        try:
            return self.driver.stop()
        except Exception as e:
            return False, "急停指令异常：%s" % e

    def drain(self):
        """丢掉还没跑的指令（急停之后用，免得刚停完又自己走起来）。"""
        n = 0
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
            n += 1
        return n

    # ---------------------------------------------------------------- 线程
    def _loop(self):
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=self._period)
            except queue.Empty:
                item = None
            if self._stop.is_set():
                break

            if item is not None:
                desc, fn = item
                ok, msg = True, ""
                try:
                    ok, msg = fn(self.driver)
                except Exception as e:                  # 驱动没兜住的异常
                    ok, msg = False, "执行异常：%s" % e
                self.finished.emit(desc, bool(ok), str(msg or ""))
                continue

            if not self._active.is_set():
                continue
            if not self.driver.connected:
                continue
            try:
                state = self.driver.read_state()
            except Exception:
                state = {}
            self.tick.emit(state)
