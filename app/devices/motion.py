# -*- coding: utf-8 -*-
"""三轴控制器（FMC4030）驱动。

底层封装原样取自 D:\\PCB\\KZQ\\fmc4030.py（复制到本目录下的 fmc4030.py），
这里只做「连接 / 断开 / 读状态」这层与界面无关的包装。

两个必须保留的历史坑（来自 KZQ 的实测记录）：
  1. 20240920 那版 DLL 与控制器固件不兼容：Open_Device 返回 0（成功），
     但实际没有建立 TCP 连接，之后每次调用都返回 -5。
     所以「Open 返回 0」不能当连上，必须再读一次版本/状态来验证。
  2. Get_Axis_Current_Pos 在本机固件上恒返回 0，位置一律从
     Get_Machine_Status 的 realPos 取（fmc4030.py 里已经这么做了）。
"""

import os
import time

from . import fmc4030 as core
from . import store
from .base import Driver, LIBS_DIR, ST_DISCONNECTED

DLL_PATH = os.path.join(LIBS_DIR, "FMC4030-Dll.dll")

# 回零方向和探针座不是一个口径：三轴 1=正限位 / 2=负限位（SDK 常量）
HOME_DIR_TEXT = {core.HOME_POS_LIMIT: "正限位", core.HOME_NEG_LIMIT: "负限位"}

_RC = {
    0: "成功",
    -1: "连接失败（查网线 / IP / 端口，必要时重启控制器）",
    -4: "内存分配失败",
    -5: "数据发送失败",
    -6: "数据接收失败",
    -7: "接收数据错误",
    -8: "空指针错误",
}


def rc_text(rc):
    return _RC.get(rc, "未知返回码 %s" % rc)


class MotionDriver(Driver):
    key = "motion"
    kind = "motion"

    AXIS = {"x": core.AXIS_X, "y": core.AXIS_Y, "z": core.AXIS_Z}

    def __init__(self, cfg):
        super(MotionDriver, self).__init__(cfg)
        self._ctrl = None
        self._dev = 0

    # ---------------------------------------------------------------- 连接
    def connect(self):
        cfg = self.cfg
        ip = str(cfg.get("ip", "")).strip() or "192.168.0.30"
        try:
            port = int(cfg.get("port", 8088))
            dev = int(cfg.get("device_id", 0))
        except (TypeError, ValueError):
            return self._fail("端口和设备 ID 必须是整数")

        if not os.path.exists(DLL_PATH):
            return self._fail("找不到 FMC4030-Dll.dll", DLL_PATH)

        try:
            ctrl = core.FMC4030(DLL_PATH)
        except OSError as e:
            # 99% 是位数不符：DLL 是 x64，解释器是 x86（或反过来）
            return self._fail("加载 DLL 失败，多为 Python 位数与 DLL 不一致", str(e))

        rc = ctrl.open(dev, ip, port)
        if rc != 0:
            return self._fail("连接 %s:%d 失败：%s" % (ip, port, rc_text(rc)))

        # 关键：Open 返回 0 不代表真的连上了，读一次版本确认链路通
        try:
            ver = ctrl.get_version(dev)
            fw, lib, sn = int(ver.firmware), int(ver.lib), int(ver.serialnumber)
        except Exception as e:
            ctrl.close(dev)
            return self._fail("连接后读版本失败，链路可能没建立", str(e))

        if fw == 0 and lib == 0 and sn == 0:
            ctrl.close(dev)
            return self._fail(
                "控制器无响应：Open 返回成功但读不到版本信息",
                "常见原因：IP/网线不通；或 DLL 版本与固件不匹配"
                "（20240920 版实测不兼容，需用 20210906 版）")

        # 再读一次状态，确认状态结构体可用（位置显示要用它）
        try:
            ctrl.get_status(dev)
        except Exception as e:
            ctrl.close(dev)
            return self._fail("连接后读状态失败", str(e))

        self._ctrl = ctrl
        self._dev = dev
        return self._ok({
            "firmware": fw,
            "lib": lib,
            "serial": sn,
            "endpoint": "%s:%d / 设备ID %d" % (ip, port, dev),
        })

    def disconnect(self):
        if self._ctrl is not None:
            try:
                self._ctrl.close(self._dev)
            except Exception:
                pass
        self._ctrl = None
        self.state = ST_DISCONNECTED
        self.info = {}
        return True, "已断开"

    # ---------------------------------------------------------------- 参数
    def apply(self):
        """三轴的限位/速度不是「写进控制器」的参数。

        FMC4030 的运动指令里自带速度和加减速，限位是本程序的软件限位
        （下发运动前先比对目标位置，超了就不发指令）。所以这里没有可下发的
        东西 —— 参数保存即生效，不需要连接设备。说清楚比假装下发更靠谱。
        """
        return True, "参数已保存（软件限位在每次运动前校验，无需下发）"

    # ---------------------------------------------------------------- 状态
    def read_state(self):
        if self._ctrl is None:
            return {}
        try:
            st = self._ctrl.get_status(self._dev)
        except Exception:
            return {}
        axes = {}
        for name, ax in self.AXIS.items():
            fl = int(st.axisStatus[ax])
            axes[name] = {
                "pos": round(float(st.realPos[ax]), 3),
                "speed": round(float(st.realSpeed[ax]), 3),
                # 卡上的 HOME_DONE 位不会因为急停而清零，但轴被硬停过就可能丢步。
                # 两者取与：解除急停后一律算「未回零」，逼一次重新回零。
                "homed": bool(fl & core.MACHINE_HOME_DONE) and not self._home_lost,
                "running": bool(fl & (core.MACHINE_RUNNING | core.MACHINE_HOME)),
                "limited": bool(fl & (core.MACHINE_LIMIT_P | core.MACHINE_LIMIT_N)),
                "limit_p": bool(fl & core.MACHINE_LIMIT_P),
                "limit_n": bool(fl & core.MACHINE_LIMIT_N),
                "alarm": False,
                "text": _axis_text(fl),
            }
        auto = bool(st.machineRunStatus & core.MACHINE_AUTO)
        return {"axes": axes, "mode": "自动" if auto else "手动"}

    # ---------------------------------------------------------------- 运动
    def _axis_cfg(self, name):
        return (self.cfg.get("axes") or {}).get(name) or {}

    def limits(self, name):
        """(正限位, 负限位)，None 表示该方向不限制。

        KZQ 原配置用 0 表示"这方向不限制"，这里照旧沿用；六轴那边用的是 null。
        界面要显示允许范围也调这个，别在页面里再算一遍 —— 两套算法迟早对不上。
        """
        c = self._axis_cfg(name)
        p = float(c.get("limit_pos", 0.0) or 0.0)
        n = float(c.get("limit_neg", 0.0) or 0.0)
        return (p if p != 0 else None), (n if n != 0 else None)

    def _check_limit(self, name, target):
        pos, neg = self.limits(name)
        if pos is not None and target > pos + 1e-6:
            return ("%s 轴目标 %.3f mm 超出正限位 %.3f mm，未动作"
                    % (name.upper(), target, pos))
        if neg is not None and target < neg - 1e-6:
            return ("%s 轴目标 %.3f mm 超出负限位 %.3f mm，未动作"
                    % (name.upper(), target, neg))
        return None

    def _prm(self, name):
        c = self._axis_cfg(name)
        sp = float(c.get("speed", 50.0) or 50.0)
        ac = float(c.get("acc", 20.0) or 20.0)
        dc = float(c.get("dec", ac) or ac)
        return sp, ac, dc

    def move_abs(self, name, target_mm):
        """单轴绝对定位。软件限位先比对，超限不发指令。"""
        why = self.estop_block()          # 急停按下后一律不放行（连没连上都不放）
        if why:
            return False, why
        if self._ctrl is None:
            return False, "未连接，无法运动"
        try:
            target = float(target_mm)
        except (TypeError, ValueError):
            return False, "目标位置不是有效数字"
        why = self._check_limit(name, target)
        if why:
            return False, why
        sp, ac, dc = self._prm(name)
        rc = self._ctrl.move_axis(self._dev, self.AXIS[name], target, sp, ac, dc,
                                  core.MOVE_ABSOLUTE)
        if rc != 0:
            return False, "%s 轴运动被拒绝：%s" % (name.upper(), rc_text(rc))
        return True, "%s 轴 → %.3f mm（%.1f mm/s）" % (name.upper(), target, sp)

    def move_rel(self, name, delta_mm):
        """单轴相对点动。控制器原生支持相对模式，不用自己算目标。"""
        why = self.estop_block()
        if why:
            return False, why
        if self._ctrl is None:
            return False, "未连接，无法运动"
        try:
            delta = float(delta_mm)
        except (TypeError, ValueError):
            return False, "步长不是有效数字"
        try:
            cur = float(self._ctrl.get_pos(self._dev, self.AXIS[name]))
        except Exception:
            cur = 0.0
        why = self._check_limit(name, cur + delta)
        if why:
            return False, why
        sp, ac, dc = self._prm(name)
        rc = self._ctrl.move_axis(self._dev, self.AXIS[name], delta, sp, ac, dc,
                                  core.MOVE_RELATIVE)
        if rc != 0:
            return False, "%s 轴点动被拒绝：%s" % (name.upper(), rc_text(rc))
        return True, "%s 轴 %+.3f mm（%.1f mm/s）" % (name.upper(), delta, sp)

    def home(self, name, on_stage=None):
        """单轴回零：下发 → 等卡把 HOME_DONE 置上（阻塞，跑在后台线程里）。

        厂家是分两步的（示例脚本 522「启动回零」→ 524「等待回零完成」），
        这里合成一个阻塞调用。**必须等**：不等就返回 True 的话，「全部回零」
        会把三根轴同时点着，日志还会写「完成」，而实际只是「指令已下发」。

        卡是单向回零：朝 dir 那一侧走，撞到限位开关停，再反向退 fall mm。
        所以「没启动 / 中途停住 / 超时」三种失败都要单独判，否则方向反了会
        白等满一个超时，日志最后只有一句「超时」，看不出是没动还是走到一半。
        """
        why = self.estop_block()
        if why:
            return False, why
        if self._ctrl is None:
            return False, "未连接，无法回零"
        p = self.home_params(name)
        ax = self.AXIS[name]
        rc = self._ctrl.home_axis(self._dev, ax, p["speed"], p["acc"],
                                  p["fall"], p["dir"])
        if rc != 0:
            return False, "%s 轴回零被拒绝：%s" % (name.upper(), rc_text(rc))
        toward = HOME_DIR_TEXT.get(p["dir"], "?")
        if on_stage is not None:
            on_stage("已启动（朝%s，%.1f mm/s）" % (toward, p["speed"]))

        t0 = time.time()
        started = False
        idle = 0.0
        last = t0
        t_stage = t0
        while True:
            now = time.time()
            if self.estop_active:
                self._home_abort(ax)
                return False, "%s 轴回零被急停中止" % name.upper()
            try:
                st = self._ctrl.get_status(self._dev)
                fl = int(st.axisStatus[ax])
            except Exception as e:
                self._home_abort(ax)
                return False, "%s 轴回零中读状态失败：%s" % (name.upper(), e)

            # 刚下发的那一瞬间，卡上可能还留着上一次的 HOME_DONE，别当成本次完成
            if (fl & core.MACHINE_HOME_DONE) and (started or now - t0 > 0.5):
                self._home_lost = False          # 真回到原点了，状态重新可信
                return True, "%s 轴回零完成（朝%s，位置已清零）" % (
                    name.upper(), toward)
            if fl & core.MACHINE_HOME_OVERTIME:
                return False, ("%s 轴回零超时（控制器判定）。检查原点开关有没有"
                               "信号、回零方向是否反了" % name.upper())

            if fl & (core.MACHINE_HOME | core.MACHINE_RUNNING):
                started = True
                idle = 0.0
            elif started:
                idle += now - last
                if idle > 2.0:
                    return False, ("%s 轴回零中途停止，没到原点（查限位、驱动器"
                                   "报警、是否被急停）：%s"
                                   % (name.upper(), _axis_text(fl)))

            if (not started) and now - t0 > 2.0:
                self._home_abort(ax)
                return False, ("%s 轴回零没有启动（2s 内无运行状态）。检查：回零"
                               "方向是否反了、限位开关是否接对、轴是否使能"
                               % name.upper())
            # 回零是阻塞的（这里等了，界面上的位置轮询也就停了），
            # 每 2 秒往日志报一次，别让人以为程序卡死
            if started and on_stage is not None and now - t_stage > 2.0:
                t_stage = now
                on_stage("回零中…（已 %.0fs）" % (now - t0))
            if now - t0 > p["timeout"]:
                self._home_abort(ax)
                return False, "%s 轴回零超过 %.0fs 未完成，已发停止指令" % (
                    name.upper(), p["timeout"])
            last = now
            time.sleep(0.05)

    def _home_abort(self, ax):
        """回零失败/中止时把这一轴停下来，别让它继续顶。"""
        try:
            self._ctrl.stop_axis(self._dev, ax, core.STOP_IMMEDIATE)
        except Exception:
            pass

    def home_params(self, name):
        """该轴的回零参数（逐轴）。缺项回落到厂家默认值，**不跟随点动参数**。

        口径照 FMC4030_Home_Single_Axis：
          speed 回零速度 mm/s、acc 回零加减速 mm/s²、
          fall  回零脱落距离 mm（回零完成后离开限位开关的距离）、
          dir   1=正限位回零 2=负限位回零。

        回零速度/加减速是与点动分开的两套（厂家上位机也是分开的）：点动设得再快，
        回零该多快还是多快。所以缺项时回落到 DEFAULTS，不是回落到 axis.speed。
        """
        c = ((self.cfg.get("home") or {}).get("axes") or {}).get(name) or {}
        d = (((store.DEFAULTS.get("motion") or {}).get("home") or {})
             .get("axes") or {}).get(name) or {}

        try:
            dir_ = int(c.get("dir", d.get("dir", core.HOME_NEG_LIMIT)))
        except (TypeError, ValueError):
            dir_ = core.HOME_NEG_LIMIT
        if dir_ not in (core.HOME_POS_LIMIT, core.HOME_NEG_LIMIT):
            dir_ = core.HOME_NEG_LIMIT

        def num(key, lo):
            try:
                v = float(c.get(key, d.get(key)))
            except (TypeError, ValueError):
                v = float(d.get(key))
            return v if v >= lo else float(d.get(key))

        return {"dir": dir_,
                "speed": num("speed", 0.01),
                "acc": num("acc", 1.0),
                "fall": num("fall", 0.0),
                "timeout": num("timeout", 1.0)}

    def stop(self):
        """急停 / 全部停止。

        这台固件的停止模式到底认哪个，没有文档能拍板（KZQ 实测记录），
        所以「立即停止」和「减速停止」各发一遍，再补一条 Stop_Run，
        哪个生效算哪个。
        """
        if self._ctrl is None:
            return False, "未连接，没有可发的停止指令"
        codes = []
        for name in ("x", "y", "z"):
            ax = self.AXIS[name]
            for mode in (core.STOP_IMMEDIATE, core.STOP_DECEL):
                codes.append(self._ctrl.stop_axis(self._dev, ax, mode))
        try:
            codes.append(self._ctrl.stop_run(self._dev))
        except Exception:
            pass
        if all(c == 0 for c in codes):
            return True, "已下发停止（三轴立停 + 减速 + Stop_Run）"
        return True, "停止指令已下发，部分返回码非 0（%s）" % ",".join(
            str(c) for c in codes)


def _axis_text(flags):
    """把 axisStatus 位翻成人话，状态列直接显示。"""
    if flags & core.MACHINE_HOME_OVERTIME:
        return "回零超时"
    if flags & core.MACHINE_HOME:
        return "回零中"
    if flags & core.MACHINE_RUNNING:
        return "运行中"
    if flags & core.MACHINE_LIMIT_P:
        return "正限位"
    if flags & core.MACHINE_LIMIT_N:
        return "负限位"
    if flags & core.MACHINE_HOME_DONE:
        return "已回零"
    return "就绪"
