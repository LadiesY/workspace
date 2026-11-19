# -*- coding: utf-8 -*-
"""探针座（GAS 运动控制卡）驱动。

底层封装原样取自 D:\\PCB\\TZ\\probe\\gas.py（复制到本目录下的 gas.py）。
厂家手册写的是 MC_* 前缀，但 DLL 里实际导出的是 GA_*，gas.py 已按实测改好。

卡是六轴的：轴 1..6 = X/Y/Z/U/V/W（原工具里分两组，探针1 走 XYZ、探针2 走 UVW）。
六个轴的脉冲当量都是 12800，换算方式一样：

单位换算照 TZ 的 AxisMap：
    位置   mm      <-> 脉冲        pulse = mm * spm
    速度   mm/s    ->  脉冲/ms     v = mm_s * spm / 1000
    加速度 mm/s^2  ->  脉冲/ms^2   a = mm_s2 * spm / 1e6
spm（脉冲当量）默认 12800 脉冲/mm，来自 TZ 的 probe_config.json。
"""

import os

from . import gas
from . import store
from .base import Driver, LIBS_DIR, ST_DISCONNECTED

DLL_PATH = os.path.join(LIBS_DIR, "GAS.dll")

# 卡上轴号，来自 TZ\probe\probe_config.json：
# axis_x=1 / axis_y=2 / axis_z=3 / axis_u=4 / axis_v=5 / axis_w=6
# （厂家 SysParam.xml 里也是 6 个在用轴，轴 1..6 参数完全相同）
AXIS_NO = {"x": 1, "y": 2, "z": 3, "u": 4, "v": 5, "w": 6}

# 探针座是六轴：两组平面轴 XYZ + UVW（原工具里「探针1 (XYZ) / 探针2 (UVW)」）
AXES = ("x", "y", "z", "u", "v", "w")

# 不限制的那一侧用这个值代替。TZ 的 home_full 里用的也是这个量级（±99999*12800）。
_WIDE = 99999 * 12800


class ProbeDriver(Driver):
    key = "probe"
    kind = "probe"

    def __init__(self, cfg):
        super(ProbeDriver, self).__init__(cfg)
        self._card = None

    @property
    def spm(self):
        try:
            v = float(self.cfg.get("spm", 12800.0))
            return v if v > 0 else 12800.0
        except (TypeError, ValueError):
            return 12800.0

    def mm2pulse(self, mm):
        return int(round(float(mm) * self.spm))

    def pulse2mm(self, pulse):
        return float(pulse) / self.spm

    def vel_pulse(self, mm_s):
        """速度 mm/s -> 脉冲/ms（卡要的单位）。"""
        return float(mm_s) * self.spm / 1000.0

    def acc_pulse(self, mm_s2):
        """加速度 mm/s² -> 脉冲/ms²。"""
        return float(mm_s2) * self.spm / 1e6

    # ---------------------------------------------------------------- 轴参数
    def axis_cfg(self, name):
        return (self.cfg.get("axes") or {}).get(name) or {}

    def limits(self, name):
        """(正限位, 负限位)，None 表示该方向不限制（原配置里 null 就是不限）。"""
        ax = self.axis_cfg(name)
        p, n = ax.get("limit_pos"), ax.get("limit_neg")
        return (None if p in (None, "") else float(p),
                None if n in (None, "") else float(n))

    # ---------------------------------------------------------------- 连接
    def connect(self):
        cfg = self.cfg
        addr = str(cfg.get("addr", "")).strip()
        if not addr:
            return self._fail("请先填写设备地址（网口填 PC 端 IP，串口填 COM 口名）")
        try:
            card_no = int(cfg.get("card_no", 1))
        except (TypeError, ValueError):
            return self._fail("卡号必须是整数")
        mode = "serial" if str(cfg.get("mode", "eth")) == "serial" else "eth"

        if not os.path.exists(DLL_PATH):
            return self._fail("找不到 GAS.dll", DLL_PATH)

        try:
            card = gas.GasCard(DLL_PATH)
        except gas.GasError as e:
            return self._fail("加载 GAS.dll 失败", str(e))
        except OSError as e:
            return self._fail("加载 GAS.dll 失败，多为位数不一致", str(e))

        try:
            card.open(mode=mode, addr=addr, card_no=card_no)
        except gas.GasError as e:
            return self._fail("连接 %s 失败：%s" % (addr, e))

        ver = card.dll_version()
        # 连上后逐轴读一次状态：读得到才算真的通
        bad = []
        for name in AXES:
            try:
                card.get_sts(AXIS_NO[name])
            except gas.GasError as e:
                bad.append("%s轴：%s" % (name.upper(), e))
        if len(bad) == len(AXES):
            card.close()
            return self._fail("连接后六轴状态全部读取失败，链路没建立", "；".join(bad))

        self._card = card
        return self._ok({
            "dll_version": ver,
            "endpoint": "%s / 卡号 %d / %s" % (
                addr, card_no, "串口" if mode == "serial" else "网口"),
        })

    def disconnect(self):
        if self._card is not None:
            try:
                self._card.close()
            except Exception:
                pass
        self._card = None
        self.state = ST_DISCONNECTED
        self.info = {}
        return True, "已断开"

    # ---------------------------------------------------------------- 参数
    def push_limit(self, name):
        """把某轴限位换成脉冲下发到卡（GA_SetSoftLimit）。"""
        if self._card is None:
            return False, "未连接"
        pos, neg = self.limits(name)
        if pos is None and neg is None:
            return True, "%s 轴不限位" % name.upper()
        p = _WIDE if pos is None else self.mm2pulse(pos)
        n = -_WIDE if neg is None else self.mm2pulse(neg)
        try:
            self._card.set_soft_limit(AXIS_NO[name], p, n)
        except gas.GasError as e:
            return False, "%s 轴软限位下发失败：%s" % (name.upper(), e)
        return True, "%s 轴 +%d/%d" % (name.upper(), p, n)

    def apply(self):
        """把六轴软限位下发到卡（GA_SetSoftLimit，单位脉冲）。

        这是卡自己的软限位，和本程序的软件限位是两回事：卡会在越界时
        直接拒绝启动，比程序判断更早一步。减速/加速度是运动指令参数，
        不在这里下发。
        """
        if self._card is None:
            return False, "未连接，参数已保存但还没有下发到卡"

        done, fail = [], []
        for name in AXES:
            ok, msg = self.push_limit(name)
            (done if ok else fail).append(msg)
        if fail:
            return False, "软限位下发失败 " + "；".join(fail)
        return True, "软限位已下发（脉冲）：" + "  ".join(done)

    # ---------------------------------------------------------------- 运动
    def _check_limit(self, name, target):
        """软件限位先校验，超了就不发指令。返回 None 表示放行。"""
        pos, neg = self.limits(name)
        if pos is not None and target > pos + 1e-6:
            return ("%s 轴目标 %.3f mm 超出正限位 %.3f mm，未动作"
                    % (name.upper(), target, pos))
        if neg is not None and target < neg - 1e-6:
            return ("%s 轴目标 %.3f mm 超出负限位 %.3f mm，未动作"
                    % (name.upper(), target, neg))
        return None

    def _goto(self, name, target_mm):
        ax = AXIS_NO[name]
        c = self.axis_cfg(name)
        sp = float(c.get("speed", 2.0) or 2.0)
        ac = float(c.get("acc", 500.0) or 500.0)
        dc = float(c.get("dec", ac) or ac)
        # block=False：卡收到指令就返回，界面靠轮询显示位置，不在调用里死等
        self._card.goto_abs(ax, self.mm2pulse(target_mm), self.vel_pulse(sp),
                            self.acc_pulse(ac), self.acc_pulse(dc), block=False)

    def move_abs(self, name, target_mm):
        """绝对定位（mm）。返回 (ok, 人话)。"""
        why = self.estop_block()          # 急停按下后一律不放行（连没连上都不放）
        if why:
            return False, why
        if self._card is None:
            return False, "未连接，无法运动"
        try:
            target = float(target_mm)
        except (TypeError, ValueError):
            return False, "目标位置不是有效数字"
        why = self._check_limit(name, target)
        if why:
            return False, why
        try:
            self._goto(name, target)
        except gas.GasError as e:
            return False, "%s 轴运动被拒绝：%s" % (name.upper(), e)
        sp = float(self.axis_cfg(name).get("speed", 2.0) or 2.0)
        return True, "%s 轴 → %.3f mm（%.2f mm/s）" % (name.upper(), target, sp)

    def move_rel(self, name, delta_mm):
        """相对点动（mm，带符号）。目标 = 卡上读回的当前位置 + 步长。"""
        why = self.estop_block()
        if why:
            return False, why
        if self._card is None:
            return False, "未连接，无法运动"
        try:
            delta = float(delta_mm)
        except (TypeError, ValueError):
            return False, "步长不是有效数字"
        try:
            cur = self.pulse2mm(self._card.get_pos(AXIS_NO[name]))
        except Exception as e:
            return False, "读当前位置失败：%s" % e
        target = cur + delta
        why = self._check_limit(name, target)
        if why:
            return False, why
        try:
            self._goto(name, target)
        except gas.GasError as e:
            return False, "%s 轴点动被拒绝：%s" % (name.upper(), e)
        return True, "%s 轴 %+.3f → %.3f mm" % (name.upper(), delta, target)

    def home(self, name, on_stage=None):
        """单轴回零（阻塞，必须放在后台线程里跑）。

        参数来自 devices.json 的 probe.home：mode=1（只找 HOME 开关）；
        **方向逐轴给**（home.dirs[name]，0=负向 1=正向）—— 原工具里 X 就是负向，
        其余五轴正向，一套方向套六个轴会让 X 朝反方向顶到底。
        回零结束后卡上的位置计数器被清零，软限位要立刻收紧回来 ——
        home_full 为了不被行程挡住会先把它放到 ±99999mm。
        """
        why = self.estop_block()
        if why:
            return False, why
        if self._card is None:
            return False, "未连接，无法回零"
        ax = AXIS_NO[name]
        prm = self._home_pulse(name)
        try:
            # abort 交给卡里那圈轮询：急停按下后它立刻 HomeStop 退出，
            # 不用等满超时（等下去轴可能还在爬）。
            self._card.home_full(ax, prm, on_stage=on_stage,
                                 abort=lambda: self._estop.is_set())
        except gas.GasError as e:
            if self._estop.is_set():
                return False, "%s 轴回零被急停中止" % name.upper()
            return False, "%s 轴回零失败：%s" % (name.upper(), e)
        self._home_lost = False          # 真回到原点了，状态重新可信
        ok, msg = self.push_limit(name)
        tail = "" if ok else "（限位未恢复：%s）" % msg
        return True, "%s 轴回零完成（%s，%.1f mm/s），位置已清零%s" % (
            name.upper(), store.HOME_DIR_TEXT.get(prm["dir"], "?"),
            float(prm["rapid_pulse"]) * 1000.0 / self.spm, tail)

    def home_dir(self, name):
        """该轴的回零方向：0=负向、1=正向。老配置只有一套 dir，回落兼容。"""
        h = self.cfg.get("home") or {}
        dirs = h.get("dirs")
        if isinstance(dirs, dict) and name in dirs:
            try:
                return 1 if int(dirs[name]) else 0
            except (TypeError, ValueError):
                pass
        try:
            return 1 if int(h.get("dir", 1)) else 0
        except (TypeError, ValueError):
            return 1

    def _home_pulse(self, name=None):
        """回零参数 mm 制 -> 卡要的脉冲制。name 给了就取该轴自己的方向。"""
        h = self.cfg.get("home") or {}
        rapid = float(h.get("rapid", 2.0) or 2.0)
        locate = float(h.get("locate", 1.0) or 1.0)
        acc = float(h.get("acc", 500.0) or 500.0)
        return {
            "mode": int(h.get("mode", 1) or 1),
            "dir": self.home_dir(name) if name else 1,
            "offset_pulse": self.mm2pulse(float(h.get("offset", 0.0) or 0.0)),
            "rapid_pulse": self.vel_pulse(rapid),
            "locate_pulse": self.vel_pulse(locate),
            "index_pulse": self.vel_pulse(locate * 0.5),
            "acc_pulse": self.acc_pulse(acc),
            "timeout": float(h.get("timeout", 120.0) or 120.0),
            "index_dis": 0, "back_dis": 0, "delay_ms": 2000,
        }

    def stop(self):
        """六轴平滑停止（GA_Stop 掩码 0xFF）。急停按钮直接调它。"""
        if self._card is None:
            return False, "未连接，没有可发的停止指令"
        try:
            rc = self._card.stop(0xFF, 0)
        except gas.GasError as e:
            return False, "停止指令下发失败：%s" % e
        if rc not in (0, None):
            return False, "停止指令返回 %s（%s）" % (rc, gas.ERR_TEXT.get(rc, "未知"))
        return True, "已下发停止（六轴）"

    # ---------------------------------------------------------------- 状态
    def read_state(self):
        """给界面轮询用：每轴位置 / 速度 / 回零 / 限位 / 报警。"""
        if self._card is None:
            return {}
        axes = {}
        for name in AXES:
            ax = AXIS_NO[name]
            try:
                sts = int(self._card.get_sts(ax))
                pulse = int(self._card.get_pos(ax))
            except Exception:
                continue
            try:
                vel = float(self._card.get_vel(ax))
            except Exception:
                vel = 0.0
            axes[name] = {
                "pos": round(self.pulse2mm(pulse), 3),
                "speed": round(vel * 1000.0 / self.spm, 3),
                "homed": bool(sts & gas.AXIS_STATUS_HOME_SUCESS)
                         and not self._home_lost,
                "running": bool(sts & (gas.AXIS_STATUS_RUNNING |
                                       gas.AXIS_STATUS_HOME_RUNNING)),
                "limited": bool(sts & (gas.AXIS_STATUS_POS_HARD_LIMIT |
                                       gas.AXIS_STATUS_NEG_HARD_LIMIT |
                                       gas.AXIS_STATUS_POS_SOFT_LIMIT |
                                       gas.AXIS_STATUS_NEG_SOFT_LIMIT)),
                "limit_p": bool(sts & (gas.AXIS_STATUS_POS_HARD_LIMIT |
                                       gas.AXIS_STATUS_POS_SOFT_LIMIT)),
                "limit_n": bool(sts & (gas.AXIS_STATUS_NEG_HARD_LIMIT |
                                       gas.AXIS_STATUS_NEG_SOFT_LIMIT)),
                "alarm": bool(sts & (gas.AXIS_STATUS_SV_ALARM |
                                      gas.AXIS_STATUS_ESTOP)),
                "text": gas.status_text(sts),
            }
        return {"axes": axes, "mode": "六轴"}
