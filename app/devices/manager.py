# -*- coding: utf-8 -*-
"""设备管理器：三个驱动 + 配置 + 连接状态，界面只跟它打交道。

连接是阻塞调用（网络握手、枚举相机都要时间，失败时还可能等超时），
一律放到后台线程里跑，结果用信号回主线程 —— 否则点一下「连接」界面就卡住，
连「全部连接」会一个设备一个设备地卡，很难看。
"""

import threading

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from . import store
from .base import ST_CONNECTED, ST_CONNECTING, ST_DISCONNECTED, ST_ERROR
from .camera import CameraDriver
from .motion import MotionDriver
from .probe import ProbeDriver

# 顺序即界面清单顺序
DEVICE_ORDER = ("motion", "probe", "camera")

DEVICE_META = {
    "motion": {
        "name": "三轴控制器",
        "model": "FMC4030  三轴运动控制卡",
        "icon": "controller",
        "desc": "三轴点动 / 定位 / 回零，TCP 通讯",
    },
    "probe": {
        "name": "探针座",
        "model": "GAS 运动控制卡  六轴 XYZ+UVW",
        "icon": "probe",
        "desc": "探针 Z 向点触与 XY/UV 平面定位，网口/串口",
    },
    "camera": {
        "name": "工业相机",
        "model": "Hikvision MVS",
        "icon": "camera",
        "desc": "引脚图像采集，GigE / USB3",
    },
}


class DeviceManager(QObject):
    device_changed = pyqtSignal(str)        # 某台设备状态变了
    logged = pyqtSignal(str, str)           # (文本, 级别 info/ok/warn/err)
    # 急停闸变了（True=已按下并上锁）。整机只有一个闸，自动测试页、运动控制页
    # 两块面板都跟着它切按钮，免得"这边解除了、那边还写着已急停"。
    estop_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super(DeviceManager, self).__init__(parent)
        self.cfg = store.load()
        self.drivers = {
            "motion": MotionDriver(self.cfg["motion"]),
            "probe": ProbeDriver(self.cfg["probe"]),
            "camera": CameraDriver(self.cfg["camera"]),
        }
        self._busy = set()
        # 整机急停标志：自动流程（视觉启动 / 自动运行）每跑一步先看它，
        # 置了就收尾退出，别再发下一条动作。
        self.estop_flag = threading.Event()

    # ---------------------------------------------------------------- 查询
    def driver(self, key):
        return self.drivers.get(key)

    def state(self, key):
        d = self.drivers.get(key)
        return d.state if d else ST_DISCONNECTED

    def is_connected(self, key):
        return self.state(key) == ST_CONNECTED

    def connected_count(self):
        return sum(1 for k in DEVICE_ORDER if self.is_connected(k))

    def is_busy(self, key):
        return key in self._busy

    # ---------------------------------------------------------------- 连接
    def connect(self, key, then_apply=False):
        if key in self._busy:
            return
        d = self.drivers.get(key)
        if d is None:
            return
        self._busy.add(key)
        d.state = ST_CONNECTING
        self.device_changed.emit(key)
        meta = DEVICE_META[key]
        self.logged.emit("正在连接 %s …" % meta["name"], "info")
        # DLL 的 TCP 超时比较长，IP 不通时这段会静默十几秒 —— 给个提示，
        # 免得用户以为程序卡死又去点第二次
        QTimer.singleShot(12000, lambda k=key: self._slow_hint(k))

        def work():
            try:
                ok, msg = d.connect()
            except Exception as e:                       # 驱动里没兜住的异常
                d.state = ST_ERROR
                d.last_error = str(e)
                ok, msg = False, "连接异常：%s" % e
            self._busy.discard(key)
            self.device_changed.emit(key)
            if ok:
                extra = ""
                info = d.info or {}
                if key == "motion":
                    extra = "固件 %s / 库 %s / 序列号 %s" % (
                        info.get("firmware"), info.get("lib"), info.get("serial"))
                elif key == "probe":
                    extra = "DLL 版本 %s" % info.get("dll_version")
                elif key == "camera":
                    extra = info.get("model", "")
                self.logged.emit("%s 已连接。%s" % (meta["name"], extra), "ok")
                if then_apply:
                    self._apply_silent(key)
            else:
                self.logged.emit("%s 连接失败：%s" % (meta["name"], msg), "err")
                detail = (d.info or {}).get("detail")
                if detail:
                    self.logged.emit("　原因：%s" % detail, "warn")

        threading.Thread(target=work, daemon=True).start()

    def _slow_hint(self, key):
        """连接卡住超过 12 秒时提一句，避免用户以为程序死了。"""
        if key in self._busy:
            self.logged.emit(
                "%s 仍在等待响应：DLL 的 TCP 超时比较长，可先检查网线、IP 与端口"
                % DEVICE_META[key]["name"], "warn")

    def disconnect(self, key):
        d = self.drivers.get(key)
        if d is None or key in self._busy:
            return
        meta = DEVICE_META[key]
        try:
            d.disconnect()
            self.logged.emit("%s 已断开" % meta["name"], "info")
        except Exception as e:
            self.logged.emit("%s 断开异常：%s" % (meta["name"], e), "warn")
        self.device_changed.emit(key)

    def connect_all(self):
        lost = [k for k in DEVICE_ORDER if not self.is_connected(k)]
        if not lost:
            self.logged.emit("三台设备都已连接", "info")
            return
        # 顺序连接：三个同时握手容易互相拖慢，日志也会乱
        def work():
            for k in lost:
                while k in self._busy:
                    threading.Event().wait(0.05)
                self._connect_blocking(k)
        threading.Thread(target=work, daemon=True).start()

    def _connect_blocking(self, key):
        d = self.drivers.get(key)
        if d is None or self.is_connected(key):
            return
        self._busy.add(key)
        d.state = ST_CONNECTING
        self.device_changed.emit(key)
        meta = DEVICE_META[key]
        self.logged.emit("正在连接 %s …" % meta["name"], "info")
        try:
            ok, msg = d.connect()
        except Exception as e:
            d.state = ST_ERROR
            ok, msg = False, "连接异常：%s" % e
        self._busy.discard(key)
        self.device_changed.emit(key)
        if ok:
            self.logged.emit("%s 已连接" % meta["name"], "ok")
        else:
            self.logged.emit("%s 连接失败：%s" % (meta["name"], msg), "err")

    def disconnect_all(self):
        for k in DEVICE_ORDER:
            if self.is_connected(k):
                self.disconnect(k)

    # ---------------------------------------------------------------- 急停
    def estop(self):
        """整机急停：三轴 / 探针立即停，相机停采图。

        **直接在当前线程下发，不排队**：排在一个 120 秒的回零后面等，那就不叫
        急停了。返回 [(key, ok, 说明)]，界面照着逐条落进日志。

        未连接的设备也返回一条说明 —— 没发出去的停止指令必须让人知道，
        不能让人以为"点了就停了"。
        """
        self.estop_flag.set()
        out = []
        for key in ("motion", "probe"):
            d = self.drivers.get(key)
            if d is None:
                continue
            if not self.is_connected(key):
                # 闸照样置上：设备这会儿没连，不代表待会儿连上就允许动
                d.estop()
                out.append((key, False, "未连接，停止指令发不出去（请按硬件急停）"))
                continue
            try:
                ok, msg = d.estop()
            except Exception as e:
                ok, msg = False, "停止指令异常：%s" % e
            out.append((key, ok, msg))

        cam = self.drivers.get("camera")
        if cam is not None and self.is_connected("camera"):
            try:
                cam.stop_stream()
                out.append(("camera", True, "已停止采集"))
            except Exception as e:
                out.append(("camera", False, "停止采集异常：%s" % e))
        self.estop_changed.emit(True)
        return out

    def clear_estop(self):
        """解除急停（确认现场安全后才调）。

        只解闸：不动轴、不补动作、不恢复"已回零"。轴停在哪儿就是哪儿。
        """
        self.estop_flag.clear()
        for d in self.drivers.values():
            d.clear_estop()
        self.estop_changed.emit(False)

    def is_estopped(self):
        return self.estop_flag.is_set()

    # ---------------------------------------------------------------- 保存
    def save(self, new_cfg):
        """写盘 + 同步给驱动 + 对已连接的设备下发。

        返回 (是否全部成功, 说明)。配置一定先落盘 —— 参数存下来比下发成功更重要，
        设备这次没连上，下次连上还能用同一份参数。
        """
        try:
            store.save(new_cfg)
        except (IOError, OSError) as e:
            return False, "配置写入失败：%s" % e

        self.cfg = new_cfg
        for k, d in self.drivers.items():
            d.cfg = new_cfg[k]

        results = []
        for k in DEVICE_ORDER:
            if not self.is_connected(k):
                continue
            ok, msg = self._apply_silent(k)
            results.append("%s：%s" % (DEVICE_META[k]["name"], msg))

        if not results:
            return True, "参数已保存（设备未连接，连接后需再点保存下发）"
        return True, "参数已保存。" + "　".join(results)

    def _apply_silent(self, key):
        d = self.drivers.get(key)
        if d is None:
            return False, "无此设备"
        try:
            ok, msg = d.apply()
        except Exception as e:
            ok, msg = False, "下发异常：%s" % e
        self.logged.emit("%s 下发：%s" % (DEVICE_META[key]["name"], msg),
                         "ok" if ok else "warn")
        self.device_changed.emit(key)
        return ok, msg

    # ---------------------------------------------------------------- 关闭
    def shutdown(self):
        for k in DEVICE_ORDER:
            if self.is_connected(k):
                try:
                    self.drivers[k].disconnect()
                except Exception:
                    pass
