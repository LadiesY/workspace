# -*- coding: utf-8 -*-
"""设备驱动的公共部分：状态常量、异常、基类。

三个驱动（三轴 / 探针 / 相机）都实现同一组方法，界面只跟这组方法打交道，
不关心底下是 FMC4030 的 DLL、GAS 的卡还是海康的 MVS。
"""

import os
import threading

# 设备状态。界面上的绿点 = CONNECTED，红点 = 其余一切（包括连接中、出错）。
ST_DISCONNECTED = "disconnected"
ST_CONNECTING = "connecting"
ST_CONNECTED = "connected"
ST_ERROR = "error"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIBS_DIR = os.path.join(BASE_DIR, "libs")
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "devices.json")


class DeviceError(RuntimeError):
    """连接/下发失败。message 直接给人看，不要只写错误码。"""

    def __init__(self, message, detail=""):
        self.detail = detail
        RuntimeError.__init__(self, message)


class Driver(object):
    """驱动基类。

    子类必须实现 connect / disconnect / apply / read_state；
    apply 的语义是「把界面上的参数下发到设备」，设备没连接时返回 (False, "未连接")，
    不要抛异常 —— 参数能不能下发不该阻止用户保存配置。
    """

    key = ""
    kind = ""

    def __init__(self, cfg):
        self.cfg = cfg
        self.state = ST_DISCONNECTED
        self.last_error = ""
        self.info = {}          # 连接后读回的真实信息，比如固件版本、序列号
        # 急停闸：按下之后所有运动入口先看它。只发一次停止是不够的 ——
        # 流程里的下一条指令会紧接着把轴再带走，那就不叫停。
        self._estop = threading.Event()
        # 急停过之后「回零状态不再可信」：轴是被硬停下来的，步进可能丢步，
        # 卡上的 HOME_SUCESS 位却不会因此清零。这个标志把两者分开，
        # 由 read_state 合进 homed 字段，逼操作员重新回零。
        self._home_lost = False

    # ---- 子类实现 ----
    def connect(self):
        raise NotImplementedError

    def disconnect(self):
        raise NotImplementedError

    def apply(self):
        return False, "该设备没有可下发的参数"

    def read_state(self):
        return {}

    # ---- 公共 ----
    @property
    def connected(self):
        return self.state == ST_CONNECTED

    # ---- 急停 ----
    # 运动类设备（三轴、探针）都走这三步：置闸 → 发停止 → 运动入口被闸拦住。
    # 闸不自动复位：急停之后必须有人明确确认（解除急停），才允许再动。
    @property
    def estop_active(self):
        return self._estop.is_set()

    def estop(self):
        """急停：先置闸，再立即下发停止指令。返回 (ok, 人话)。

        闸先置、停止后发，顺序不能反：反过来的话这中间插进来的一条运动
        指令会漏过去。
        """
        self._estop.set()
        self.invalidate_home()      # 被硬停过的轴，原点不再可信
        try:
            return self.stop()
        except Exception as e:
            return False, "停止指令异常：%s" % e

    def invalidate_home(self):
        """把「已回零」作废：之后必须重新回零才能做绝对定位。"""
        self._home_lost = True

    def clear_estop(self):
        """解除急停闸（只在确认现场安全后由界面调用）。

        只解闸，不恢复回零状态 —— 轴停在半路，剩下的距离不会自己走完，
        原点也不会自己回来。重新动之前该回零就重新回零。
        """
        self._estop.clear()

    def estop_block(self):
        """运动入口的统一拦截：返回 None 表示放行，否则是给用户看的原因。"""
        if self._estop.is_set():
            return "急停已按下，运动被拦住：确认现场安全后先解除急停"
        return None

    def stop(self):
        """停止指令。子类覆盖；没有可停的东西的设备用这个默认实现。"""
        return False, "该设备没有可发的停止指令"

    def _ok(self, info=None):
        self.state = ST_CONNECTED
        self.last_error = ""
        if info:
            self.info.update(info)
        return True, "已连接"

    def _fail(self, message, detail=""):
        self.state = ST_ERROR
        self.last_error = message
        self.info = {}
        if detail:
            self.info["detail"] = detail
        return False, message
