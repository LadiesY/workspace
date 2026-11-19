# -*- coding: utf-8 -*-
"""
GAS 运动控制卡 ctypes 封装
==========================

事实依据（已实测，不是猜的）：
  * GAS.dll 为 x64，导出 548 个 GA_* 符号，GA_GetDllVersion() 返回 20260105
  * 厂商手册里写的 MC_* 前缀在本 DLL 里基本不存在（只有 MC_Robot* 两个）
    => 调用一律用 GA_*，不要照手册抄 MC_*
  * 位置单位：脉冲(pulse)；速度单位：脉冲/毫秒(Pluse/ms)；加速度：脉冲/ms^2
  * 返回值：0 成功 / 1 执行失败 / 2 版本不支持 / 7 参数错
            -1 通讯失败 / -6 打开失败 / -7 无响应 / -8 串口打开失败

本模块只做“把 C 函数安全地变成 Python 方法”，不含任何工艺逻辑。
工艺逻辑在 probe_core.py，界面在 app.py。
"""

from __future__ import print_function

import ctypes
import os
import sys
import threading
import time

# ---------------------------------------------------------------- 常量

GA_COM_SUCCESS = 0
GA_COM_ERR_EXEC_FAIL = 1
GA_COM_ERR_LICENSE_WRONG = 2
GA_COM_ERR_DATA_WRONG = 7
GA_COM_ERR_SEND = -1
GA_COM_ERR_CARD_OPEN_FAIL = -6
GA_COM_ERR_TIME_OUT = -7
GA_COM_ERR_COM_OPEN_FAIL = -8

ERR_TEXT = {
    0: "成功",
    1: "执行失败(检查命令执行条件)",
    2: "版本不支持该API",
    7: "参数错误",
    -1: "通讯失败(检查接线)",
    -6: "打开控制器失败(IP/串口名/重复Open)",
    -7: "运动控制器无响应(是否已连接/已打开)",
    -8: "打开串口失败",
}

# 轴状态位
AXIS_STATUS_ESTOP = 0x00000001
AXIS_STATUS_SV_ALARM = 0x00000002
AXIS_STATUS_POS_SOFT_LIMIT = 0x00000004
AXIS_STATUS_NEG_SOFT_LIMIT = 0x00000008
AXIS_STATUS_FOLLOW_ERR = 0x00000010
AXIS_STATUS_POS_HARD_LIMIT = 0x00000020
AXIS_STATUS_NEG_HARD_LIMIT = 0x00000040
AXIS_STATUS_IO_SMS_STOP = 0x00000080
AXIS_STATUS_IO_EMG_STOP = 0x00000100
AXIS_STATUS_ENABLE = 0x00000200
AXIS_STATUS_RUNNING = 0x00000400
AXIS_STATUS_ARRIVE = 0x00000800
AXIS_STATUS_HOME_RUNNING = 0x00001000
AXIS_STATUS_HOME_SUCESS = 0x00002000
AXIS_STATUS_HOME_SWITCH = 0x00004000
AXIS_STATUS_INDEX = 0x00008000
AXIS_STATUS_HOME_FAIL = 0x00400000

STATUS_BITS = [
    (AXIS_STATUS_ESTOP, "急停"),
    (AXIS_STATUS_SV_ALARM, "驱动报警"),
    (AXIS_STATUS_POS_SOFT_LIMIT, "正软限位"),
    (AXIS_STATUS_NEG_SOFT_LIMIT, "负软限位"),
    (AXIS_STATUS_FOLLOW_ERR, "跟随误差越限"),
    (AXIS_STATUS_POS_HARD_LIMIT, "正硬限位"),
    (AXIS_STATUS_NEG_HARD_LIMIT, "负硬限位"),
    (AXIS_STATUS_IO_SMS_STOP, "IO平滑停止"),
    (AXIS_STATUS_IO_EMG_STOP, "IO急停"),
    (AXIS_STATUS_ENABLE, "使能"),
    (AXIS_STATUS_RUNNING, "运行中"),
    (AXIS_STATUS_ARRIVE, "到位"),
    (AXIS_STATUS_HOME_RUNNING, "回零中"),
    (AXIS_STATUS_HOME_SUCESS, "回零成功"),
    (AXIS_STATUS_HOME_FAIL, "回零失败"),
]

# 输入 IO 类型（GA_GetDiRaw 的 nDiType）
MC_LIMIT_POSITIVE = 0
MC_LIMIT_NEGATIVE = 1
MC_ALARM = 2
MC_HOME = 3
MC_GPI = 4
MC_ARRIVE = 5
MC_MPG = 7

# 输出 IO 类型
MC_ENABLE = 10
MC_CLEAR = 11
MC_GPO = 12

# 硬件捕获模式
CAPTURE_HOME = 1
CAPTURE_INDEX = 2
CAPTURE_PROBE1 = 3
CAPTURE_PROBE2 = 4
CAPTURE_PROBE1_STOP = 13      # 探针捕获且自动停止 —— 点触首选
CAPTURE_PROBE2_STOP = 14

# 回零方式
HOME_MODE_HOME = 1            # 只找 HOME 开关（最常用）
HOME_MODE_HOME_INDEX = 2      # HOME + 编码器 Z 相
HOME_MODE_INDEX = 3           # 只找 Z 相


class GasError(RuntimeError):
    """
    DLL 返回非 0 时抛出，带上函数名与错误码。
    code=None 表示不是 DLL 返回的错误（超时、未到位、中止等自己判断出来的），
    这种不要套用 DLL 的错误码文案 —— 否则会误导人去查接线。
    """

    def __init__(self, func, code, extra=""):
        self.func = func
        self.code = code
        if code is None:
            msg = "GA_%s: %s" % (func, extra or "失败")
        else:
            msg = "GA_%s 返回 %d: %s" % (func, code, ERR_TEXT.get(code, "未知错误"))
            if extra:
                msg += " | " + extra
        RuntimeError.__init__(self, msg)


def status_text(sts):
    on = [name for bit, name in STATUS_BITS if sts & bit]
    return "/".join(on) if on else "空闲"


def _limit_dir_block(sts, dirn):
    """
    这一趟运动会不会被限位挡住。dirn>0 往正走，<0 往负走，0 原地不动。

    返回 None = 放行，否则返回一句能直接贴到日志里的人话。

    **只拦「朝着已触发限位去」的方向**。顶在负限位上还禁止往正方向走，
    等于把人锁死在限位上出不来 —— 撤出动作必须永远允许。
    """
    if dirn > 0:
        if sts & AXIS_STATUS_POS_HARD_LIMIT:
            return "正硬限位正触发，不能继续往正方向走（可以先往负方向撤）"
        if sts & AXIS_STATUS_POS_SOFT_LIMIT:
            return "已到正软限位上界，卡会拒绝继续往正方向走"
    elif dirn < 0:
        if sts & AXIS_STATUS_NEG_HARD_LIMIT:
            return "负硬限位正触发，不能继续往负方向走（可以先往正方向撤）"
        if sts & AXIS_STATUS_NEG_SOFT_LIMIT:
            return "已到负软限位下界，卡会拒绝继续往负方向走"
    return None


# ---------------------------------------------------------------- 真实卡

def default_dll():
    here = os.path.dirname(os.path.abspath(__file__))
    cand = [
        os.path.join(here, "GAS.dll"),
        os.path.join(here, "..", "Python简单例程", "Windows", "GAS.dll"),
        os.path.join(here, "..", "Python简单例程", "Windows", "GAS.dll"),
        r"D:\PCB\TZ\Python简单例程\Windows\GAS.dll",
    ]
    for c in cand:
        if os.path.exists(c):
            return os.path.abspath(c)
    return ""


# 函数原型表: name -> (restype, [argtypes...], 默认参数个数说明)
# 严格按 gas_linux_n.h 的声明写；写错参数类型 = 崩溃或静默错误
_PROTO = {
    # 打开/关闭
    "GA_Open": ("int", ["short", "char*"]),
    "GA_OpenByIP": ("int", ["char*", "char*", "uint", "ushort"]),
    "GA_Close": ("int", []),
    "GA_Reset": ("int", []),
    "GA_SetCardNo": ("int", ["short"]),
    "GA_GetCardNo": ("int", ["short*"]),
    "GA_GetDllVersion": ("int", []),
    "GA_GetCardMessage": ("int", ["char*"]),
    "GA_GetID": ("int", ["uint*"]),

    # 轴使能
    "GA_AxisOn": ("int", ["short"]),
    "GA_AxisOff": ("int", ["short"]),

    # 状态
    "GA_GetSts": ("int", ["short", "int*", "short", "uint*"]),
    "GA_ClrSts": ("int", ["short", "short"]),
    "GA_GetPrfPos": ("int", ["short", "double*", "short", "uint*"]),
    "GA_GetPrfVel": ("int", ["short", "double*", "short", "uint*"]),
    "GA_GetAxisEncPos": ("int", ["short", "double*", "short", "uint*"]),
    "GA_GetAxisError": ("int", ["short", "double*", "short", "uint*"]),

    # 点位
    "GA_PrfTrap": ("int", ["short"]),
    "GA_SetTrapPrmSingle": ("int", ["short", "double", "double", "double", "short"]),
    "GA_SetPos": ("int", ["short", "int"]),
    "GA_SetVel": ("int", ["short", "double"]),
    "GA_Update": ("int", ["int"]),
    # 注意 llPos 是 long long，不是 int —— 手册 5.4 里那个效率最高的合并函数
    "GA_SetTrapPosAndUpdate": ("int", ["short", "longlong", "double", "double",
                                       "double", "double", "short", "short"]),

    # JOG
    "GA_PrfJog": ("int", ["short"]),
    "GA_SetJogPrmSingle": ("int", ["short", "double", "double", "double"]),

    # 停止 / 安全
    "GA_Stop": ("int", ["int", "int"]),
    "GA_SetSoftLimit": ("int", ["short", "int", "int"]),
    "GA_GetSoftLimit": ("int", ["short", "int*", "int*"]),
    "GA_LmtsOn": ("int", ["short", "short"]),
    "GA_LmtsOff": ("int", ["short", "short"]),
    "GA_SetLmtSnsSingle": ("int", ["short", "short", "short"]),
    "GA_GetLmtSnsSingle": ("int", ["short", "short*", "short*"]),
    "GA_LmtSns": ("int", ["ushort"]),
    "GA_CrdHlimEnable": ("int", ["short", "short"]),
    "GA_SetAxisBand": ("int", ["short", "int", "int"]),
    "GA_SetStopDec": ("int", ["short", "double", "double"]),
    "GA_SetPosErr": ("int", ["short", "int"]),
    # 硬限位信号映射到通用 DI —— 厂家就是靠这两个把限位开关接到 DI11/DI12…
    # 不调它，GA_LmtsOn 开了也没用：轴根本不知道该看哪个输入
    "GA_SetHardLimP": ("int", ["short", "short", "short", "short"]),
    "GA_SetHardLimN": ("int", ["short", "short", "short", "short"]),
    # 软限位按规划位置还是编码器位置判。开环步进编码器恒 0，开了会误判
    "GA_SoftLimEnc": ("int", ["short", "short"]),
    # 原点信号电平
    "GA_HomeSns": ("int", ["ushort"]),
    "GA_GetHomeSns": ("int", ["short*"]),

    # 位置
    "GA_ZeroPos": ("int", ["short", "short"]),
    "GA_SetPrfPos": ("int", ["short", "int"]),
    "GA_SetBacklash": ("int", ["short", "int", "double", "int"]),

    # IO
    "GA_GetDiRaw": ("int", ["short", "int*"]),
    "GA_GetDi": ("int", ["short", "int*"]),
    "GA_GetDo": ("int", ["short", "int*"]),
    "GA_SetDoBit": ("int", ["short", "short", "short"]),
    "GA_SetDoBitReverse": ("int", ["short", "short", "short", "short"]),
    "GA_SetExtDoBit": ("int", ["short", "short", "ushort"]),
    "GA_GetExtDiBit": ("int", ["short", "short", "ushort*"]),
    "GA_GetExtDiValue": ("int", ["short", "uint*", "short"]),

    # 回零
    "GA_HomeSetPrmSingle": ("int", ["short", "short", "short", "int", "double",
                                    "double", "double", "double", "int", "int", "short"]),
    "GA_HomeStart": ("int", ["int"]),
    "GA_HomeStop": ("int", ["int"]),
    "GA_HomeGetSts": ("int", ["short", "ushort*", "int*", "int*", "int*"]),
    "GA_HomeGetFailReason": ("int", ["short", "ushort*"]),
    "GA_HomeSetFinishFlag": ("int", ["short", "ushort"]),
    "GA_HomeGetPrmSingle": ("int", ["short", "short*", "short*", "int*",
                                    "double*", "double*", "double*", "double*"]),

    # 硬件捕获（探针）
    "GA_SetCaptureMode": ("int", ["short", "short"]),
    "GA_GetCaptureMode": ("int", ["short", "short*", "short"]),
    "GA_GetCaptureStatus": ("int", ["short", "short*", "int*", "short", "uint*"]),
    "GA_ClearCaptureStatus": ("int", ["short"]),
    "GA_SetCaptureSense": ("int", ["short", "short", "short"]),
    "GA_SetCaptureSource": ("int", ["short", "short"]),
}

HOME_STS_TEXT = {0: "尚未回零", 1: "回零中", 2: "回零成功"}

_CTYPE = {
    "int": ctypes.c_int,
    "short": ctypes.c_short,
    "uint": ctypes.c_uint,
    "ushort": ctypes.c_ushort,
    "longlong": ctypes.c_longlong,
    "double": ctypes.c_double,
    "char*": ctypes.c_char_p,
    "int*": ctypes.POINTER(ctypes.c_int),
    "short*": ctypes.POINTER(ctypes.c_short),
    "uint*": ctypes.POINTER(ctypes.c_uint),
    "ushort*": ctypes.POINTER(ctypes.c_ushort),
    "double*": ctypes.POINTER(ctypes.c_double),
}


class _LimitLogic(object):
    """
    限位标志的处理逻辑，真卡和模拟卡共用一份，避免两边行为不一致。

    只依赖子类提供 `get_sts` / `get_pos` / `call_raw`（模拟卡自带同名方法）。
    """

    LIMIT_HARD = (AXIS_STATUS_POS_HARD_LIMIT | AXIS_STATUS_NEG_HARD_LIMIT)
    LIMIT_SOFT = (AXIS_STATUS_POS_SOFT_LIMIT | AXIS_STATUS_NEG_SOFT_LIMIT)

    def settle_limits(self, axes):
        """
        把「锁存的残留限位标志」和「开关真的还压着」区分开。

        为什么必须有这一步：限位触发标志是**锁存**的。手册 `MC_ClrSts` 的说明就是
        「清除驱动器报警标志、跟随误差越限标志、**限位触发标志**」。
        也就是说：只要历史上触发过一次（回零撞到头、手动顶了一下、甚至一次误触发），
        这个位就会一直为 1，直到有人显式清它。

        结果就是我们自己的预检看到「已在限位」，把之后**每一次**运动都拦下来。
        表现出来就是用户看到的那样：位置已经归零、机械明明在原点，
        日志却一直说「已到限位，运动被拦下」。

        做法：清一次再读。
          - 开关真的压着 -> 标志立刻重新置位，说明是真信号，必须仍然拦
          - 只是残留锁存 -> 标志消失，可以正常运动

        返回 {axis: {"pos": bool, "neg": bool, "cleared": bool, "trust": bool}}
        trust=False 表示正负同时触发（物理上不可能），这个轴的限位信号不可信。
        """
        out = {}
        for ax in axes:
            ax = int(ax)
            sts0 = self.get_sts(ax)
            if not (sts0 & self.LIMIT_HARD):
                out[ax] = {"pos": False, "neg": False, "cleared": False, "trust": True}
                continue
            self.clr_sts(ax)
            # 等一下再读：卡是周期扫描输入的，清完立刻读有可能还没重新扫到
            # 「开关仍压着」这个事实，于是把真信号误判成残留标志。
            time.sleep(0.012)
            sts1 = self.get_sts(ax)
            pos = bool(sts1 & AXIS_STATUS_POS_HARD_LIMIT)
            neg = bool(sts1 & AXIS_STATUS_NEG_HARD_LIMIT)
            out[ax] = {
                "pos": pos,
                "neg": neg,
                # 清之前有、清之后没了 -> 是残留标志，不是真信号
                "cleared": not pos and not neg,
                "trust": not (pos and neg),
            }
        return out

    def prep_move(self, axes):
        """
        每次下发运动前的统一准备：清掉**锁存的残留限位标志**。

        有真报警或急停时不碰状态字：那种情况下清标志只会掩盖问题。
        """
        try:
            for ax in axes:
                s = self.get_sts(int(ax))
                if s & (AXIS_STATUS_ESTOP | AXIS_STATUS_SV_ALARM):
                    return {}
        except Exception:
            return {}
        try:
            return self.settle_limits(axes)
        except Exception:
            return {}

    def limit_block(self, axis, target_pulse):
        """
        目标位置这一趟会不会被限位挡住。返回 None 表示能走，否则返回原因文字。

        关键是**看方向**：负限位触发时，往正方向走（背离限位）是撤出动作，
        必须允许，否则一旦顶到限位就永远退不出来 —— 这比限位本身更麻烦。
        """
        axis = int(axis)
        sts = self.get_sts(axis)
        pos_hard = bool(sts & AXIS_STATUS_POS_HARD_LIMIT)
        neg_hard = bool(sts & AXIS_STATUS_NEG_HARD_LIMIT)
        pos_soft = bool(sts & AXIS_STATUS_POS_SOFT_LIMIT)
        neg_soft = bool(sts & AXIS_STATUS_NEG_SOFT_LIMIT)
        if pos_hard and neg_hard:
            # 物理上不可能同时压住两端 -> 电平设反 / IO 号错 / 输入悬空
            return None
        try:
            cur = self.get_pos(axis)
        except Exception:
            return None
        dirn = 0
        if target_pulse > cur + 1:
            dirn = 1
        elif target_pulse < cur - 1:
            dirn = -1
        if dirn > 0 and pos_hard:
            return "正硬限位正触发，不能继续往正方向走"
        if dirn < 0 and neg_hard:
            return "负硬限位正触发，不能继续往负方向走"
        if dirn > 0 and pos_soft:
            return "已到正软限位上界，卡会拒绝继续往正方向走"
        if dirn < 0 and neg_soft:
            return "已到负软限位下界，卡会拒绝继续往负方向走"
        return None

    def wait_all(self, targets, timeout=30.0, abort=None, band=2.0):
        """等一批轴全部到位。targets 是 [(axis, pos_pulse), ...]"""
        todo = list(targets)
        t0 = time.time()
        bad = (AXIS_STATUS_ESTOP | AXIS_STATUS_SV_ALARM)
        # 每轴这一趟的走向，用来判断限位该不该拦（背离方向必须放行）
        dirs = {}
        for ax, tgt in targets:
            try:
                here = self.get_pos(int(ax))
            except Exception:
                here = float(tgt)
            dirs[int(ax)] = (1 if tgt > here + 1 else (-1 if tgt < here - 1 else 0))
        while todo:
            if abort is not None and abort():
                self.stop(0xFF)
                raise GasError("WaitAll", None, "已中止")
            still = []
            for ax, tgt in todo:
                sts = self.get_sts(ax)
                if sts & bad:
                    self.stop(0xFF)
                    raise GasError("WaitAll", None,
                                   "轴%d 异常: %s" % (ax, status_text(sts)))
                blocked = _limit_dir_block(sts, dirs.get(ax, 0))
                if blocked:
                    self.stop(0xFF)
                    raise GasError("WaitAll", None,
                                   "轴%d %s（%s）" % (ax, blocked, status_text(sts)))
                if (sts & AXIS_STATUS_RUNNING) or abs(self.get_pos(ax) - tgt) > band:
                    still.append((ax, tgt))
            todo = still
            if not todo:
                return True
            if time.time() - t0 > timeout:
                self.stop(0xFF)
                raise GasError("WaitAll", None, "多轴等位超时 %.1fs" % timeout)
            time.sleep(0.004)
        return True

    def wait_arrive(self, axis, target=None, timeout=30.0, abort=None,
                    band=2.0, quiet_polls=30, on_warn=None):
        """
        等一次运动结束。

        判据按可靠性排序：
          1) 规划器停止(RUNNING=0) **且** 规划位置与目标差 <= band  -> 到位
          2) 规划器停止且位置连续 quiet_polls 次不再变化           -> 认为已结束
          3) 超时 -> 抛错，并报出「当前 / 目标 / 差值」

        **故意不用 ARRIVE 位做判据**：该位要求「规划位置与**实际位置**误差在误差带内
        并保持设定时间」。开环步进（没接编码器）时实际位置恒为 0，误差永远超限，
        ARRIVE 永远不置位 —— 拿它当判据就会每次等到超时。这个问题在真机上踩过。

        **限位只拦「朝向它」的方向**：顶在负限位上还禁止往正方向走，就等于把人锁死
        在限位上出不来。所以这里先用起点和目标算出这一趟的方向，只在「朝着已触发的
        限位去」时才中止；背离限位的撤出动作一律放行。
        """
        axis = int(axis)
        mask = 1 << (axis - 1)
        t0 = time.time()
        bad = (AXIS_STATUS_ESTOP | AXIS_STATUS_SV_ALARM)
        last_pos = None
        quiet = 0
        target = None if target is None else float(target)
        if target is None:
            dirn = 0
        else:
            try:
                here = self.get_pos(axis)
            except Exception:
                here = target
            dirn = 1 if target > here + 1 else (-1 if target < here - 1 else 0)
        both_warned = False

        while True:
            if abort is not None and abort():
                self.stop(mask)
                raise GasError("WaitArrive", None, "轴%d 已中止" % axis)

            sts = self.get_sts(axis)
            if sts & bad:
                self.stop(mask)
                raise GasError("WaitArrive", None,
                               "轴%d 异常: %s" % (axis, status_text(sts)))

            # 正负硬限位同时为 1 在物理上不可能（机械不可能同时压住两端），
            # 说明电平设反 / IO 号填错 / 输入悬空。这种时候限位信号不可信，
            # 拿它当判据只会把轴锁死，所以只警告一次、不中止。
            if (sts & AXIS_STATUS_POS_HARD_LIMIT) and (sts & AXIS_STATUS_NEG_HARD_LIMIT):
                if not both_warned:
                    both_warned = True
                    if on_warn is not None:
                        try:
                            on_warn("轴%d 正负硬限位**同时**触发，物理上不可能 —— "
                                    "限位信号不可信（多半是「低/高电平触发」设反，"
                                    "或 IO 号填错）。本次运动按放行处理，"
                                    "请去「限位 / IO 诊断」核对。" % axis)
                        except Exception:
                            pass
            else:
                blocked = _limit_dir_block(sts, dirn)
                if blocked:
                    self.stop(mask)
                    raise GasError("WaitArrive", None,
                                   "轴%d %s（%s）" % (axis, blocked, status_text(sts)))

            pos = self.get_pos(axis)
            running = bool(sts & AXIS_STATUS_RUNNING)

            if running:
                quiet = 0
            else:
                if target is not None and abs(pos - target) <= band:
                    return True
                if last_pos is not None and abs(pos - last_pos) < 0.5:
                    quiet += 1
                else:
                    quiet = 0
                if quiet >= quiet_polls:
                    if target is not None:
                        self.stop(mask)
                        hint = ""
                        # 卡自己也会因为限位拒绝启动：这时规划器一次都没跑过，
                        # 位置没变，只说「差多少脉冲」会让人以为丢步。
                        lim_all = (AXIS_STATUS_POS_HARD_LIMIT |
                                   AXIS_STATUS_NEG_HARD_LIMIT |
                                   AXIS_STATUS_POS_SOFT_LIMIT |
                                   AXIS_STATUS_NEG_SOFT_LIMIT)
                        if sts & lim_all:
                            hint = "；同时状态里有 %s —— 卡很可能压根没让启动" % status_text(
                                sts & lim_all)
                        raise GasError("WaitArrive", None,
                                       "轴%d 停住了但没到目标：当前 %.0f，目标 %.0f，差 %.0f 脉冲%s"
                                       % (axis, pos, target, target - pos, hint))
                    return True
            last_pos = pos

            if time.time() - t0 > timeout:
                self.stop(mask)
                raise GasError("WaitArrive", None,
                               "轴%d 等待到位超时 %.1fs（当前 %.0f%s）"
                               % (axis, timeout, pos,
                                  "" if target is None else "，目标 %.0f" % target))
            time.sleep(0.004)


class GasCard(_LimitLogic):
    """真实运动控制卡。所有 GA_ 调用都带返回值检查。"""

    is_mock = False

    def __init__(self, dll_path=None, verbose=False):
        self.dll_path = dll_path or default_dll()
        if not self.dll_path or not os.path.exists(self.dll_path):
            raise GasError("LoadLibrary", -6,
                           "找不到 GAS.dll，请把 GAS.dll 放到脚本同目录，或改配置 dll_path")
        self.lib = ctypes.WinDLL(self.dll_path)
        self.verbose = verbose
        self.opened = False
        self.card_no = 1
        self._bind()

    def _bind(self):
        for name, (rt, args) in _PROTO.items():
            try:
                fn = getattr(self.lib, name)
            except AttributeError:
                continue          # 该型号没有这个函数，跳过
            fn.restype = _CTYPE[rt]
            fn.argtypes = [(_CTYPE[a] if not a.endswith("*") else _CTYPE[a]) for a in args]
        for name in _PROTO:
            if not hasattr(self, name.replace("GA_", "ga_").lower()):
                pass

    # -------------------------------------------------- 基础调用

    def call(self, name, *args):
        fn = getattr(self.lib, name, None)
        if fn is None:
            raise GasError(name[3:], 2, "该型号 DLL 无此函数")
        res = fn(*args)
        if res != 0:
            raise GasError(name[3:], res)
        return res

    def call_raw(self, name, *args):
        """不抛异常，返回原始码。用于轮询类调用，避免刷日志。"""
        fn = getattr(self.lib, name, None)
        if fn is None:
            return -2
        return fn(*args)

    def dll_version(self):
        return self.call_raw("GA_GetDllVersion")

    # -------------------------------------------------- 连接

    def open(self, mode="eth", addr="192.168.0.200", card_no=1):
        """mode: 'eth' 网口 / 'serial' 串口。addr: PC 端 IP 或 COM 口名。"""
        self.card_no = int(card_no)
        self.call("GA_SetCardNo", ctypes.c_short(self.card_no))
        if mode == "serial":
            self.call("GA_Open", ctypes.c_short(1), addr.encode("ascii"))
        else:
            self.call("GA_Open", ctypes.c_short(0), addr.encode("ascii"))
        self.opened = True
        try:
            self.call("GA_Reset")
        except GasError:
            pass
        return True

    def open_by_ip(self, pc_ip, card_ip, retry=0):
        self.call("GA_OpenByIP", pc_ip.encode("ascii"), card_ip.encode("ascii"), 0, int(retry))
        self.opened = True
        return True

    def close(self):
        try:
            if self.opened:
                self.call_raw("GA_Close")
        finally:
            self.opened = False

    # -------------------------------------------------- 轴

    def axis_on(self, axis):
        self.call("GA_AxisOn", ctypes.c_short(axis))

    def axis_off(self, axis):
        self.call("GA_AxisOff", ctypes.c_short(axis))

    def get_sts(self, axis):
        v = ctypes.c_int(0)
        self.call("GA_GetSts", ctypes.c_short(axis), ctypes.byref(v),
                  ctypes.c_short(1), None)
        return v.value

    def clr_sts(self, axis):
        self.call("GA_ClrSts", ctypes.c_short(axis), ctypes.c_short(1))

    def get_pos(self, axis):
        """规划位置（脉冲）。"""
        v = ctypes.c_double(0.0)
        self.call("GA_GetPrfPos", ctypes.c_short(axis), ctypes.byref(v),
                  ctypes.c_short(1), None)
        return v.value

    def get_enc_pos(self, axis):
        """编码器实际位置（脉冲）。注意：步进开环时编码器可能没接。"""
        v = ctypes.c_double(0.0)
        self.call("GA_GetAxisEncPos", ctypes.c_short(axis), ctypes.byref(v),
                  ctypes.c_short(1), None)
        return v.value

    def get_vel(self, axis):
        v = ctypes.c_double(0.0)
        self.call("GA_GetPrfVel", ctypes.c_short(axis), ctypes.byref(v),
                  ctypes.c_short(1), None)
        return v.value

    def get_error(self, axis):
        """跟随误差 = 规划位置 - 实际位置（脉冲）。开环没编码器时这个值没有意义。"""
        v = ctypes.c_double(0.0)
        self.call("GA_GetAxisError", ctypes.c_short(axis), ctypes.byref(v),
                  ctypes.c_short(1), None)
        return v.value

    def zero_pos(self, axis, count=1):
        self.call("GA_ZeroPos", ctypes.c_short(axis), ctypes.c_short(count))

    def set_soft_limit(self, axis, positive_pulse, negative_pulse):
        self.call("GA_SetSoftLimit", ctypes.c_short(axis),
                  ctypes.c_int(int(positive_pulse)), ctypes.c_int(int(negative_pulse)))

    def get_soft_limit(self, axis):
        pos, neg = ctypes.c_int(0), ctypes.c_int(0)
        self.call("GA_GetSoftLimit", ctypes.c_short(axis),
                  ctypes.byref(pos), ctypes.byref(neg))
        return pos.value, neg.value

    def lmts_on(self, axis, limit_type=-1):
        """让该轴的正/负硬限位信号生效。-1=正负都开，0=只开正，1=只开负。"""
        return self.call_raw("GA_LmtsOn", ctypes.c_short(axis), ctypes.c_short(limit_type))

    def lmts_off(self, axis, limit_type=-1):
        return self.call_raw("GA_LmtsOff", ctypes.c_short(axis), ctypes.c_short(limit_type))

    def set_lmt_sns_single(self, axis, pos_low=True, neg_low=True):
        """
        设置该轴正/负限位的触发电平。

        **手册原文（API使用说明 §MC_LmtSns）**：
          运动控制器默认的限位开关是**常闭**开关，即各轴处于正常工作状态时，
          其限位信号输入为**低电平**，当限位信号为**高电平**时限位触发。
          如果使用的传感器是**常开**，则需要调用本函数，将限位的逻辑电平反一下。

        参数 nPosSns / nNegSns：**1 = 低电平触发，0 = 高电平触发**。

        所以：
          - 常闭开关（手册默认、绝大多数机床）  -> pos_low=False（高电平触发）
          - 常开传感器（N/O 接近开关等）        -> pos_low=True （低电平触发）

        这个参数设反的后果非常典型：开关根本没碰，卡却认为**正负两个限位同时触发**，
        于是任何方向的运动都被拒绝 —— 机械一动不动。看到「正负同时触发」先怀疑这里。
        """
        return self.call_raw("GA_SetLmtSnsSingle", ctypes.c_short(axis),
                             ctypes.c_short(1 if pos_low else 0),
                             ctypes.c_short(1 if neg_low else 0))

    def get_limit_inputs(self):
        """直接读正/负限位输入原值（位图，按轴）。用于诊断接线。"""
        out = {}
        for name, t in (("pos", MC_LIMIT_POSITIVE), ("neg", MC_LIMIT_NEGATIVE)):
            v = ctypes.c_int(0)
            r = self.call_raw("GA_GetDiRaw", ctypes.c_short(t), ctypes.byref(v))
            out[name] = (v.value if r == 0 else None)
        return out

    def set_hard_lim(self, axis, pos_io, neg_io, ntype=1, card_index=0):
        """
        把「正/负硬限位」绑到具体的通用输入口上。

        ntype=1 表示通用输入（厂家日志里 nType 就是 1）。
        io 传 -1 表示该方向没有限位开关，跳过。

        为什么要这一步：GA_LmtsOn 只是「允许限位信号起作用」，
        但轴并不知道去哪个 DI 上取这个信号。不先 SetHardLimP/N，
        限位开关装了也读不到，轴会一路撞下去 —— 这正是之前能"走 100mm"的原因之一。
        """
        ok = True
        if pos_io is not None and int(pos_io) >= 0:
            r = self.call_raw("GA_SetHardLimP", ctypes.c_short(axis),
                              ctypes.c_short(ntype), ctypes.c_short(card_index),
                              ctypes.c_short(int(pos_io)))
            ok = ok and (r == 0)
        if neg_io is not None and int(neg_io) >= 0:
            r = self.call_raw("GA_SetHardLimN", ctypes.c_short(axis),
                              ctypes.c_short(ntype), ctypes.c_short(card_index),
                              ctypes.c_short(int(neg_io)))
            ok = ok and (r == 0)
        return ok

    def get_lmts_on(self, axis):
        """查询该轴正/负硬限位是否已启用，返回 (pos_on, neg_on)。"""
        p, n = ctypes.c_short(0), ctypes.c_short(0)
        r = self.call_raw("GA_GetLmtsOnOff", ctypes.c_short(axis),
                          ctypes.byref(p), ctypes.byref(n))
        if r != 0:
            return None, None
        return bool(p.value), bool(n.value)

    def set_soft_lim_enc(self, axis, on):
        """软限位改成按编码器位置判（on=1）。开环没编码器时不要开。"""
        return self.call_raw("GA_SoftLimEnc", ctypes.c_short(axis),
                             ctypes.c_short(1 if on else 0))

    def read_axes(self, axes):
        """
        一次读回多轴的完整现场：规划位置、编码器位置、状态位、各限位。

        界面每 250ms 刷一次，用它比逐轴调 5 个函数快，也保证同一帧的数据
        是同一个时间点的（分别读会出现「位置已到、限位还没到」的错位）。

        返回 {axis: {...}}，任一轴读失败就跳过该轴，不抛异常 ——
        刷新界面不能因为一根轴通讯超时就整个卡住。
        """
        out = {}
        for a in axes:
            try:
                sts = self.get_sts(a)
                prf = self.get_pos(a)
                enc = self.get_enc_pos(a)
            except Exception:
                continue
            out[a] = {
                "sts": sts,
                "prf": prf,
                "enc": enc,
                "err": prf - enc,
                "enabled": bool(sts & AXIS_STATUS_ENABLE),
                "running": bool(sts & AXIS_STATUS_RUNNING),
                "arrive": bool(sts & AXIS_STATUS_ARRIVE),
                "homed": bool(sts & AXIS_STATUS_HOME_SUCESS),
                "pos_hard": bool(sts & AXIS_STATUS_POS_HARD_LIMIT),
                "neg_hard": bool(sts & AXIS_STATUS_NEG_HARD_LIMIT),
                "pos_soft": bool(sts & AXIS_STATUS_POS_SOFT_LIMIT),
                "neg_soft": bool(sts & AXIS_STATUS_NEG_SOFT_LIMIT),
                "alarm": bool(sts & AXIS_STATUS_SV_ALARM),
                "estop": bool(sts & AXIS_STATUS_ESTOP),
                "follow_err": bool(sts & AXIS_STATUS_FOLLOW_ERR),
            }
        return out

    def limit_hit(self, axis):
        """该轴任一硬限位是否已触发。行程探测就是靠它判断撞到头的。"""
        try:
            sts = self.get_sts(axis)
        except Exception:
            return 0
        if sts & AXIS_STATUS_POS_HARD_LIMIT:
            return +1
        if sts & AXIS_STATUS_NEG_HARD_LIMIT:
            return -1
        return 0

    def set_axis_band(self, axis, band_pulse, hold_ms):
        self.call("GA_SetAxisBand", ctypes.c_short(axis),
                  ctypes.c_int(int(band_pulse)), ctypes.c_int(int(hold_ms)))

    def set_stop_dec(self, axis, smooth, abrupt):
        self.call("GA_SetStopDec", ctypes.c_short(axis),
                  ctypes.c_double(smooth), ctypes.c_double(abrupt))

    def stop(self, mask=0xFF, option=0):
        """mask 按位表示轴号（bit0=轴1）。option 0=平滑停止。"""
        return self.call_raw("GA_Stop", ctypes.c_int(int(mask)), ctypes.c_int(int(option)))

    # -------------------------------------------------- 点位运动

    def setup_trap(self, axis, acc, dec, vel_start=0.0, smooth_ms=0):
        self.call("GA_PrfTrap", ctypes.c_short(axis))
        self.call("GA_SetTrapPrmSingle", ctypes.c_short(axis),
                  ctypes.c_double(float(acc)), ctypes.c_double(float(dec)),
                  ctypes.c_double(float(vel_start)), ctypes.c_short(int(smooth_ms)))

    def goto_abs(self, axis, pos_pulse, vel, acc, dec=None, smooth_ms=0, block=True,
                 timeout=30.0, abort=None, on_warn=None):
        """绝对点位运动（单轴）。vel/acc 单位：脉冲/ms、脉冲/ms^2。"""
        dec = acc if dec is None else dec
        self.prep_move([axis])
        blocked = self.limit_block(axis, pos_pulse)
        if blocked:
            raise GasError("LimitBlock", None, "轴%d %s" % (int(axis), blocked))
        self.setup_trap(axis, acc, dec, 0.0, smooth_ms)
        self.call("GA_SetPos", ctypes.c_short(axis), ctypes.c_int(int(round(pos_pulse))))
        self.call("GA_SetVel", ctypes.c_short(axis), ctypes.c_double(float(vel)))
        self.call("GA_Update", ctypes.c_int(1 << (int(axis) - 1)))
        if block:
            self.wait_arrive(axis, target=pos_pulse, timeout=timeout, abort=abort,
                             on_warn=on_warn)

    def move_to(self, axis, pos_pulse, vel, acc, dec=None, smooth_ms=0):
        """不等待的绝对运动。"""
        self.goto_abs(axis, pos_pulse, vel, acc, dec, smooth_ms, block=False)

    def prep_move(self, axes):
        """
        每次下发运动前的统一准备：清掉**锁存的残留限位标志**。

        限位触发标志是锁存的（见 `settle_limits`）。只要历史上触发过一次，
        标志就一直挂着，之后每次运动都会被我们自己的预检拦下 ——
        表现就是「位置已经归零、机械就在原点，却一直说在限位、动不了」。

        有真报警或急停时不碰状态字：那种情况下清标志只会掩盖问题。
        """
        try:
            for ax in axes:
                s = self.get_sts(int(ax))
                if s & (AXIS_STATUS_ESTOP | AXIS_STATUS_SV_ALARM):
                    return {}
        except Exception:
            return {}
        try:
            return self.settle_limits(axes)
        except Exception:
            return {}

    def goto_multi(self, moves, block=True, timeout=30.0, abort=None, band=2.0,
                   on_warn=None):
        """
        多轴同时启动。moves 是 [{axis,pos,vel,acc,dec?,smooth_ms?}, ...]

        注意这是「同时启动」不是插补：每轴按自己的速度走自己的距离，
        先到的先停。要插补轨迹得用坐标系(MC_LnXY)，那套是另一回事。
        """
        if not moves:
            return True
        self.prep_move([int(m["axis"]) for m in moves])
        blocked = []
        for m in moves:
            why = self.limit_block(int(m["axis"]), float(m["pos"]))
            if why:
                blocked.append("轴%d %s" % (int(m["axis"]), why))
        if blocked:
            raise GasError("LimitBlock", None, "；".join(blocked))
        mask = 0
        for m in moves:
            ax = int(m["axis"])
            acc = float(m["acc"])
            self.setup_trap(ax, acc, float(m.get("dec", acc)), 0.0,
                            int(m.get("smooth_ms", 0)))
            self.call("GA_SetPos", ctypes.c_short(ax),
                      ctypes.c_int(int(round(float(m["pos"])))))
            self.call("GA_SetVel", ctypes.c_short(ax), ctypes.c_double(float(m["vel"])))
            mask |= 1 << (ax - 1)
        self.call("GA_Update", ctypes.c_int(mask))
        if block:
            return self.wait_all([(int(m["axis"]), float(m["pos"])) for m in moves],
                                 timeout=timeout, abort=abort, band=band)
        return True

    # -------------------------------------------------- JOG

    def jog(self, axis, vel, acc=0.5, dec=None):
        """速度模式启动。vel 带符号，单位脉冲/ms。"""
        self.call("GA_PrfJog", ctypes.c_short(axis))
        self.call("GA_SetJogPrmSingle", ctypes.c_short(axis),
                  ctypes.c_double(float(acc)),
                  ctypes.c_double(float(dec if dec is not None else acc)),
                  ctypes.c_double(0.0))
        self.call("GA_SetVel", ctypes.c_short(axis), ctypes.c_double(float(vel)))

    def zero_jog_vel(self, axis):
        self.call_raw("GA_SetVel", ctypes.c_short(axis), ctypes.c_double(0.0))

    # -------------------------------------------------- IO

    def get_di_raw(self, di_type=MC_GPI):
        v = ctypes.c_int(0)
        self.call("GA_GetDiRaw", ctypes.c_short(di_type), ctypes.byref(v))
        return v.value & 0xFFFFFFFF

    def ext_di_bit(self, card_index, bit):
        v = ctypes.c_ushort(0)
        self.call("GA_GetExtDiBit", ctypes.c_short(card_index), ctypes.c_short(bit),
                  ctypes.byref(v))
        return v.value

    def set_ext_do_bit(self, card_index, bit, value):
        self.call("GA_SetExtDoBit", ctypes.c_short(card_index), ctypes.c_short(bit),
                  ctypes.c_ushort(int(value)))

    def set_do_bit(self, do_type, num, value):
        self.call("GA_SetDoBit", ctypes.c_short(do_type), ctypes.c_short(num),
                  ctypes.c_short(int(value)))

    def pulse_do(self, do_type, num, value, ms):
        self.call("GA_SetDoBitReverse", ctypes.c_short(do_type), ctypes.c_short(num),
                  ctypes.c_short(int(value)), ctypes.c_short(int(ms)))

    # -------------------------------------------------- 回零

    def home_setup(self, axis, mode=HOME_MODE_HOME, direction=0, offset_pulse=0,
                   rapid_vel=5.0, locate_vel=1.0, index_vel=0.5, acc=0.5):
        self.call("GA_HomeSetPrmSingle",
                  ctypes.c_short(axis), ctypes.c_short(mode), ctypes.c_short(direction),
                  ctypes.c_int(int(offset_pulse)),
                  ctypes.c_double(rapid_vel), ctypes.c_double(locate_vel),
                  ctypes.c_double(index_vel), ctypes.c_double(acc),
                  ctypes.c_int(0), ctypes.c_int(0), ctypes.c_short(0))

    def home_start(self, axis):
        self.call("GA_HomeStart", ctypes.c_int(int(axis)))

    def home_stop(self, axis):
        """
        结束回零 / 解除回零状态。
        手册 5.14 明确写了：如果回零没有正常完成，必须调用它，
        否则轴不能运动 —— 也就是轴被锁死，点它不动。
        """
        return self.call_raw("GA_HomeStop", ctypes.c_int(int(axis)))

    def home_get_sts(self, axis):
        """
        回零状态：0 尚未回零 / 1 回零中 / 2 回零成功。
        同时返回 (定位位置, Z相捕获绝对位置, Z相捕获到传感器的距离)。
        比读轴状态位可靠 —— 状态位里 HOME_RUNNING/HOME_SUCESS 的时序不一定覆盖全过程。
        """
        st = ctypes.c_ushort(0)
        pos = ctypes.c_int(0)
        zpos = ctypes.c_int(0)
        zdist = ctypes.c_int(0)
        r = self.call_raw("GA_HomeGetSts", ctypes.c_short(axis), ctypes.byref(st),
                          ctypes.byref(pos), ctypes.byref(zpos), ctypes.byref(zdist))
        if r != 0:
            return None, 0, 0, 0
        return st.value, pos.value, zpos.value, zdist.value

    def home_fail_reason(self, axis):
        v = ctypes.c_ushort(0)
        r = self.call_raw("GA_HomeGetFailReason", ctypes.c_short(axis), ctypes.byref(v))
        return v.value if r == 0 else None

    def home_get_prm(self, axis):
        m = ctypes.c_short(0)
        d = ctypes.c_short(0)
        off = ctypes.c_int(0)
        v1 = ctypes.c_double(0)
        v2 = ctypes.c_double(0)
        v3 = ctypes.c_double(0)
        a = ctypes.c_double(0)
        r = self.call_raw("GA_HomeGetPrmSingle", ctypes.c_short(axis),
                          ctypes.byref(m), ctypes.byref(d), ctypes.byref(off),
                          ctypes.byref(v1), ctypes.byref(v2), ctypes.byref(v3),
                          ctypes.byref(a))
        if r != 0:
            return None
        return {"mode": m.value, "dir": d.value, "offset": off.value,
                "rapid": v1.value, "locate": v2.value, "index": v3.value, "acc": a.value}

    def home_set_finish_flag(self, axis, value=1):
        """强行把「回零完成标志」置上。没有原点开关的轴（或想跳过回零）时用。"""
        return self.call_raw("GA_HomeSetFinishFlag", ctypes.c_short(axis),
                             ctypes.c_ushort(int(value)))

    def release_homing(self, axis):
        """解除回零锁：HomeStop + 清状态位。"""
        self.call_raw("GA_HomeStop", ctypes.c_int(int(axis)))
        self.call_raw("GA_ClrSts", ctypes.c_short(axis), ctypes.c_short(1))

    def home_wait(self, axis, timeout=120.0, abort=None, on_stage=None):
        """
        等回零结束。

        判据用 GA_HomeGetSts 的 0/1/2 状态机，轴状态位只作补充
        （HOME_SUCESS 的时序不一定覆盖全过程）。

        **任何失败路径都会调 HomeStop**：手册写明回零没正常完成时轴会被锁住
        不能运动，不解除的话后面点它是不动的。
        """
        t0 = time.time()
        started = False
        stopped_at = None
        last_stage = None
        while True:
            if abort is not None and abort():
                self.home_stop(axis)
                raise GasError("HomeWait", None, "轴%d 回零被中止" % axis)

            code, pos, zpos, zdist = self.home_get_sts(axis)
            sts = self.get_sts(axis)

            if code == 2 or (sts & AXIS_STATUS_HOME_SUCESS):
                return True

            running = (code == 1) or bool(sts & AXIS_STATUS_HOME_RUNNING) or \
                      bool(sts & AXIS_STATUS_RUNNING)
            if running:
                started = True
                stopped_at = None
            if on_stage is not None and code != last_stage:
                last_stage = code
                on_stage(HOME_STS_TEXT.get(code, "未知"), pos)

            if sts & AXIS_STATUS_HOME_FAIL:
                reason = self.home_fail_reason(axis)
                self.home_stop(axis)
                raise GasError("HomeWait", None,
                               "轴%d 回零失败（失败码 %s）" % (axis, reason))

            # 起了 2 秒还完全没动静 -> 多半是模式/方向/使能有问题，别傻等
            if (not started) and time.time() - t0 > 2.0:
                reason = self.home_fail_reason(axis)
                self.home_stop(axis)
                raise GasError("HomeWait", None,
                               "轴%d 回零没有启动（2s 内无状态变化，失败码 %s）。"
                               "检查：回零方向是否反了、原点开关是否接对、轴是否使能"
                               % (axis, reason))

            # 动过又停了、却始终没报成功 -> 是被限位/报警/急停打断的，
            # 立刻判失败。没有这条就只能干等到超时（实测白等满 120 秒，
            # 而且最后状态还显示"尚未回零"，看日志的人会以为它一直在走）。
            if started and not running and code == 0:
                if stopped_at is None:
                    stopped_at = time.time()
                elif time.time() - stopped_at > 2.0:
                    reason = self.home_fail_reason(axis)
                    self.home_stop(axis)
                    raise GasError("HomeWait", None,
                                   "轴%d 回零中途停止（走了 %.1fs 没到原点，失败码 %s）。"
                                   "多数是撞了限位/机械尽头，或原点开关没信号 —— "
                                   "先确认方向对不对"
                                   % (axis, time.time() - t0, reason))
            else:
                stopped_at = None

            if time.time() - t0 > timeout:
                self.home_stop(axis)
                raise GasError("HomeWait", None,
                               "轴%d 回零超时 %.0fs（最后状态：%s）"
                               % (axis, timeout, HOME_STS_TEXT.get(code, "未知")))
            time.sleep(0.02)

    def home_full(self, axis, cfg, wide_pulse=99999 * 12800, log=None,
                  abort=None, on_stage=None):
        """
        厂家式完整回零（照抄 RunTimeLog 里厂家的调用顺序）：

            1. 先把软限位放到「足够大」，免得回零行程被自己设的范围挡住
               （厂家就是这么干的：MC_SetSoftLimit ±1279987200）
            2. GA_HomeSetPrmSingle 下参数
            3. GA_HomeStart 启动
            4. 轮询 HomeGetSts 直到 2（成功）
            5. GA_ZeroPos(axis,1) 把计数器清零 —— 这一步才是"原点"
            6. 由调用方把软限位收紧回真实行程

        cfg 是 dict：mode / dir / rapid / locate / index / acc / offset（mm 制，本函数内换算）。

        返回 True。失败抛 GasError，且一定会 HomeStop 解锁。
        """
        mode = int(cfg.get("mode", 1))
        direction = int(cfg.get("dir", 0))
        offset_pulse = int(cfg.get("offset_pulse", 0))
        rapid = float(cfg.get("rapid_pulse", 20.0))
        locate = float(cfg.get("locate_pulse", 5.0))
        indexv = float(cfg.get("index_pulse", 2.0))
        acc = float(cfg.get("acc_pulse", 0.5))
        index_dis = int(cfg.get("index_dis", 0))
        back_dis = int(cfg.get("back_dis", 0))
        delay_ms = int(cfg.get("delay_ms", 0))

        # 1. 放开软限位
        try:
            self.set_soft_limit(axis, int(wide_pulse), -int(wide_pulse))
        except Exception:
            pass
        # 2. 参数
        self.call("GA_HomeSetPrmSingle",
                  ctypes.c_short(axis), ctypes.c_short(mode), ctypes.c_short(direction),
                  ctypes.c_int(offset_pulse),
                  ctypes.c_double(rapid), ctypes.c_double(locate),
                  ctypes.c_double(indexv), ctypes.c_double(acc),
                  ctypes.c_int(index_dis), ctypes.c_int(back_dis), ctypes.c_short(delay_ms))
        # 3. 启动
        self.home_start(axis)
        # 4. 等
        self.home_wait(axis, timeout=float(cfg.get("timeout", 120.0)),
                       abort=abort, on_stage=on_stage)
        # 5. 清零 —— 厂家 GA_ZeroPos:nAxisNum=1,nCount = 1
        self.zero_pos(axis, 1)
        if log:
            log("轴%d 回零完成，位置计数器已清零（原点建立）" % axis, "ok")
        return True

    # -------------------------------------------------- 硬件捕获（探针）

    def capture_arm(self, axis, mode=CAPTURE_PROBE1_STOP, sense=None):
        """装载探针捕获。mode=13 表示捕获并自动停止 —— 点触用这个最稳。"""
        if sense is not None:
            self.call_raw("GA_SetCaptureSense", ctypes.c_short(axis),
                          ctypes.c_short(mode), ctypes.c_short(int(sense)))
        self.call_raw("GA_ClearCaptureStatus", ctypes.c_short(axis))
        self.call("GA_SetCaptureMode", ctypes.c_short(axis), ctypes.c_short(int(mode)))

    def capture_query(self, axis):
        """返回 (是否已捕获, 捕获位置脉冲)。pStatus 非 0 视为已触发。"""
        st = ctypes.c_short(0)
        val = ctypes.c_int(0)
        r = self.call_raw("GA_GetCaptureStatus", ctypes.c_short(axis),
                          ctypes.byref(st), ctypes.byref(val),
                          ctypes.c_short(1), None)
        if r != 0:
            return False, 0
        return bool(st.value), val.value

    def capture_clear(self, axis):
        self.call_raw("GA_ClearCaptureStatus", ctypes.c_short(axis))


# ---------------------------------------------------------------- 模拟卡

class MockCard(_LimitLogic):
    """
    无硬件时把整条流程跑通。行为：位置按速度推进，Z 下降到“假想板面”即触发接触。
    这不是玩具——上机前先用它验证路径规划、坐标换算、CSV 解析是否正确。
    """

    is_mock = True

    def __init__(self, surface_pulse_map=None, verbose=False):
        self.dll_path = "<mock>"
        self.opened = False
        self.card_no = 1
        self.verbose = verbose
        self.pos = dict((i, 0.0) for i in range(1, 9))
        self.sts = {a: AXIS_STATUS_ENABLE | AXIS_STATUS_ARRIVE for a in self.pos}
        self._home = set()
        self.surface = surface_pulse_map or {}   # axis -> 触发接触的位置
        self.capture = {}
        self.downtime = 0.03                     # 每个动作的假延时

    # -- 兼容接口
    def dll_version(self):
        return 99999999

    def open(self, mode="eth", addr="", card_no=1):
        self.opened = True
        return True

    def open_by_ip(self, pc_ip, card_ip, retry=0):
        self.opened = True
        return True

    def close(self):
        self.opened = False

    def axis_on(self, axis):
        self.sts[axis] |= AXIS_STATUS_ENABLE

    def axis_off(self, axis):
        self.sts[axis] &= ~AXIS_STATUS_ENABLE

    def get_sts(self, axis):
        return self.sts.get(axis, 0)

    def clr_sts(self, axis):
        """
        清状态字。真卡的 GA_ClrSts 手册说它清「驱动器报警 / 跟随误差越限 / **限位触发标志**」，
        模拟卡要跟它一致，否则测不出「清了之后还压着就会立刻重新置位」这个关键行为。
        """
        self.sts[axis] = self.sts.get(axis, 0) & ~(
            AXIS_STATUS_SV_ALARM | AXIS_STATUS_FOLLOW_ERR |
            AXIS_STATUS_POS_HARD_LIMIT | AXIS_STATUS_NEG_HARD_LIMIT)
        for side, bit in (("pos", AXIS_STATUS_POS_HARD_LIMIT),
                          ("neg", AXIS_STATUS_NEG_HARD_LIMIT)):
            if (axis, side) in getattr(self, "limit_pressed", set()):
                self.sts[axis] |= bit

    def set_limit(self, axis, side, on):
        """
        自检用：模拟「限位开关被压住 / 松开」。
        side 取 "pos"/"neg"。压住时状态位置 1，松开时清掉。
        """
        self.limit_pressed = getattr(self, "limit_pressed", set())
        bit = (AXIS_STATUS_POS_HARD_LIMIT if side == "pos"
               else AXIS_STATUS_NEG_HARD_LIMIT)
        if on:
            self.limit_pressed.add((int(axis), side))
            self.sts[axis] = self.sts.get(axis, 0) | bit
        else:
            self.limit_pressed.discard((int(axis), side))
            self.sts[axis] = self.sts.get(axis, 0) & ~bit

    def latch_limit(self, axis, side):
        """自检用：只置标志、模拟开关已经松开了 —— 也就是「残留锁存」那种情况。"""
        bit = (AXIS_STATUS_POS_HARD_LIMIT if side == "pos"
               else AXIS_STATUS_NEG_HARD_LIMIT)
        self.sts[axis] = self.sts.get(axis, 0) | bit

    def get_pos(self, axis):
        return self.pos.get(axis, 0.0)

    def get_enc_pos(self, axis):
        return self.pos.get(axis, 0.0)

    def get_vel(self, axis):
        return 0.0

    def get_error(self, axis):
        return 0.0

    def zero_pos(self, axis, count=1):
        self.pos[axis] = 0.0

    def set_soft_limit(self, axis, p, n):
        self.soft_lim = getattr(self, "soft_lim", {})
        self.soft_lim[axis] = (p, n)

    def get_soft_limit(self, axis):
        return getattr(self, "soft_lim", {}).get(axis, (10 ** 9, -10 ** 9))

    def lmts_on(self, axis, limit_type=-1):
        """
        记录正/负限位分别的启用状态。
        limit_type: -1=正负都开，0=只开正，1=只开负（和真卡一致）。
        以前模拟卡只记一个整数，分不清「只开了正限位」和「只开了负限位」，
        测不出 push_limits 里分两次调用的逻辑对不对。
        """
        self.lmts = getattr(self, "lmts", {})
        pos, neg = self.lmts.get(axis, (False, False))
        lt = int(limit_type)
        if lt in (-1, 0):
            pos = True
        if lt in (-1, 1):
            neg = True
        self.lmts[axis] = (pos, neg)
        return 0

    def lmts_off(self, axis, limit_type=-1):
        self.lmts = getattr(self, "lmts", {})
        pos, neg = self.lmts.get(axis, (False, False))
        lt = int(limit_type)
        if lt in (-1, 0):
            pos = False
        if lt in (-1, 1):
            neg = False
        self.lmts[axis] = (pos, neg)
        return 0

    def set_lmt_sns_single(self, axis, pos_low=True, neg_low=True):
        self.lmt_sns = getattr(self, "lmt_sns", {})
        self.lmt_sns[axis] = (bool(pos_low), bool(neg_low))
        return 0

    def get_limit_inputs(self):
        return {"pos": 0, "neg": 0}

    def set_hard_lim(self, axis, pos_io, neg_io, ntype=1, card_index=0):
        self.hard_lim = getattr(self, "hard_lim", {})
        self.hard_lim[axis] = (pos_io, neg_io)
        return True

    def get_lmts_on(self, axis):
        return getattr(self, "lmts", {}).get(axis, (False, False))

    def set_soft_lim_enc(self, axis, on):
        return 0

    def read_axes(self, axes):
        out = {}
        for a in axes:
            sts = self.get_sts(a)
            prf = self.get_pos(a)
            out[a] = {
                "sts": sts, "prf": prf, "enc": self.get_enc_pos(a),
                "err": prf - self.get_enc_pos(a),
                "enabled": bool(sts & AXIS_STATUS_ENABLE),
                "running": bool(sts & AXIS_STATUS_RUNNING),
                "arrive": bool(sts & AXIS_STATUS_ARRIVE),
                "homed": bool(sts & AXIS_STATUS_HOME_SUCESS),
                "pos_hard": bool(sts & AXIS_STATUS_POS_HARD_LIMIT),
                "neg_hard": bool(sts & AXIS_STATUS_NEG_HARD_LIMIT),
                "pos_soft": bool(sts & AXIS_STATUS_POS_SOFT_LIMIT),
                "neg_soft": bool(sts & AXIS_STATUS_NEG_SOFT_LIMIT),
                "alarm": bool(sts & AXIS_STATUS_SV_ALARM),
                "estop": bool(sts & AXIS_STATUS_ESTOP),
                "follow_err": bool(sts & AXIS_STATUS_FOLLOW_ERR),
            }
        return out

    def limit_hit(self, axis):
        sts = self.get_sts(axis)
        if sts & AXIS_STATUS_POS_HARD_LIMIT:
            return +1
        if sts & AXIS_STATUS_NEG_HARD_LIMIT:
            return -1
        return 0

    def set_axis_band(self, axis, band, ms):
        pass

    def set_stop_dec(self, axis, s, a):
        pass

    def stop(self, mask=0xFF, option=0):
        for a in list(self.pos):
            if int(mask) & (1 << (a - 1)):
                # 保留限位位：真卡 Stop 不会清限位标志，模拟卡也不能清
                keep = self.sts.get(a, 0) & (self.LIMIT_HARD | self.LIMIT_SOFT)
                self.sts[a] = AXIS_STATUS_ENABLE | AXIS_STATUS_ARRIVE | keep
        return 0

    def setup_trap(self, axis, acc, dec, vel_start=0.0, smooth_ms=0):
        pass

    def _sim_move(self, axis, target, vel, abort=None):
        """按速度推进位置，途中检查模拟接触面。接触时置 capture 并停止。"""
        step = 0.02
        cur = self.pos.get(axis, 0.0)
        target = float(target)
        for _ in range(400000):
            if abort is not None and abort():
                self._finish(axis, "abort")
                return
            if not (self.sts.get(axis, 0) & AXIS_STATUS_RUNNING):
                return                       # 被 stop() 打断
            # 模拟用：把速度放大 20 倍，否则 Windows 上 sleep(1ms) 实际约 15ms，
            # 走 300mm 要 7 秒多，测试和演示都难受。接触点仍然精确落在模拟面上。
            v = max(abs(vel) * 1000.0 * step * 20.0, 1500.0)
            nxt = cur + (v if target > cur else -v)
            if (target > cur and nxt > target) or (target < cur and nxt < target):
                nxt = target
            surf = self.surface.get(axis)
            if surf is not None:
                d0 = cur - surf
                d1 = nxt - surf
                # 判据：这一小步「跨过」了接触面（d0、d1 异号），或者「正好落在」
                # 接触面上（d1==0 而 d0!=0）。
                # 不能写成 d0*d1<=0 —— 起点正好在接触面上（d0==0）时，之后每一次
                # **往后退**都会满足它，于是又被判成撞了一次：退不下去，回退和抬起全失效。
                if d0 * d1 < 0 or (d1 == 0 and d0 != 0):
                    self.pos[axis] = surf
                    self.capture[axis] = (True, int(surf))
                    self._finish(axis, "contact")
                    return
            cur = nxt
            self.pos[axis] = cur
            if abs(target - cur) <= 1e-9:
                self._finish(axis, "done")
                return
            time.sleep(0.001)
        self._finish(axis, "done")

    def _finish(self, axis, why):
        # 保留限位位：真卡运动结束不会把限位标志擦掉（它是锁存的），
        # 模拟卡不能擦，否则「残留锁存」这类故障在自检里永远测不出来。
        keep = self.sts.get(axis, 0) & (self.LIMIT_HARD | self.LIMIT_SOFT)
        self.sts[axis] = AXIS_STATUS_ENABLE | AXIS_STATUS_ARRIVE | keep

    def goto_abs(self, axis, pos_pulse, vel, acc, dec=None, smooth_ms=0, block=True,
                 timeout=30.0, abort=None, on_warn=None):
        self.prep_move([axis])
        blocked = self.limit_block(axis, pos_pulse)
        if blocked:
            raise GasError("LimitBlock", None, "轴%d %s" % (int(axis), blocked))
        self.move_to(axis, pos_pulse, vel, acc, dec, smooth_ms)
        if block:
            self.wait_arrive(axis, target=pos_pulse, timeout=timeout, abort=abort,
                             on_warn=on_warn)
        return True

    def move_to(self, axis, pos_pulse, vel, acc, dec=None, smooth_ms=0):
        """异步：起一个线程推进，立刻返回。"""
        keep = self.sts.get(axis, 0) & (self.LIMIT_HARD | self.LIMIT_SOFT)
        self.sts[axis] = AXIS_STATUS_ENABLE | AXIS_STATUS_RUNNING | keep
        th = threading.Thread(target=self._sim_move, args=(axis, pos_pulse, vel, None))
        th.daemon = True
        th.start()

    def goto_multi(self, moves, block=True, timeout=30.0, abort=None, band=2.0,
                   on_warn=None):
        self.prep_move([int(m["axis"]) for m in moves])
        blocked = []
        for m in moves:
            why = self.limit_block(int(m["axis"]), float(m["pos"]))
            if why:
                blocked.append("轴%d %s" % (int(m["axis"]), why))
        if blocked:
            raise GasError("LimitBlock", None, "；".join(blocked))
        for m in moves:                      # 先全部标记为运行，再一起推进
            ax = int(m["axis"])
            keep = self.sts.get(ax, 0) & (self.LIMIT_HARD | self.LIMIT_SOFT)
            self.sts[ax] = AXIS_STATUS_ENABLE | AXIS_STATUS_RUNNING | keep
        for m in moves:
            ax = int(m["axis"])
            th = threading.Thread(target=self._sim_move,
                                  args=(ax, float(m["pos"]), float(m["vel"]), None))
            th.daemon = True
            th.start()
        if block:
            return self.wait_all([(int(m["axis"]), float(m["pos"])) for m in moves],
                                 timeout=timeout, abort=abort, band=band)
        return True

    def jog(self, axis, vel, acc=0.5, dec=None):
        self.sts[axis] |= AXIS_STATUS_RUNNING

    def zero_jog_vel(self, axis):
        self.sts[axis] = AXIS_STATUS_ENABLE | AXIS_STATUS_ARRIVE

    def get_di_raw(self, di_type=MC_GPI):
        # 有 surface 且已到面，就报 bit0 有效
        for a, (cap, _v) in self.capture.items():
            if cap:
                return 0x01
        return 0x00

    def ext_di_bit(self, card_index, bit):
        return 1 if self.get_di_raw() & (1 << bit) else 0

    def set_ext_do_bit(self, card_index, bit, value):
        pass

    def set_do_bit(self, do_type, num, value):
        pass

    def pulse_do(self, do_type, num, value, ms):
        pass

    def home_setup(self, axis, mode=1, direction=0, offset_pulse=0,
                   rapid_vel=5.0, locate_vel=1.0, index_vel=0.5, acc=0.5):
        pass

    def home_start(self, axis):
        self.sts[axis] = AXIS_STATUS_ENABLE | AXIS_STATUS_HOME_RUNNING
        self.home_code = getattr(self, "home_code", {})
        self.home_code[axis] = 1

    def home_stop(self, axis):
        pass

    def home_full(self, axis, cfg, wide_pulse=99999 * 12800, log=None,
                  abort=None, on_stage=None):
        """模拟卡也走同一条流程，保证上层代码在无硬件时被真正跑到。"""
        self.set_soft_limit(axis, int(wide_pulse), -int(wide_pulse))
        self.home_start(axis)
        self.home_wait(axis, timeout=float(cfg.get("timeout", 120.0)),
                       abort=abort, on_stage=on_stage)
        self.zero_pos(axis, 1)
        return True

    def home_wait(self, axis, timeout=120.0, abort=None, on_stage=None):
        self.pos[axis] = 0.0
        self.sts[axis] = AXIS_STATUS_ENABLE | AXIS_STATUS_ARRIVE | AXIS_STATUS_HOME_SUCESS
        self.home_code = getattr(self, "home_code", {})
        self.home_code[axis] = 2
        return True

    def home_get_sts(self, axis):
        self.home_code = getattr(self, "home_code", {})
        return self.home_code.get(axis, 0), int(self.pos.get(axis, 0)), 0, 0

    def home_fail_reason(self, axis):
        return 0

    def home_get_prm(self, axis):
        return {"mode": 1, "dir": 0, "offset": 0, "rapid": 10.0,
                "locate": 1.0, "index": 0.5, "acc": 0.5}

    def home_set_finish_flag(self, axis, value=1):
        self.home_code = getattr(self, "home_code", {})
        if value:
            self.home_code[axis] = 2
        return 0

    def release_homing(self, axis):
        self.home_code = getattr(self, "home_code", {})
        self.home_code[axis] = 0
        return 0

    def capture_arm(self, axis, mode=CAPTURE_PROBE1_STOP, sense=None):
        self.capture[axis] = (False, 0)

    def capture_query(self, axis):
        return self.capture.get(axis, (False, 0))

    def capture_clear(self, axis):
        self.capture[axis] = (False, 0)


def make_card(dll_path=None, mock=False):
    if mock:
        return MockCard()
    return GasCard(dll_path)
