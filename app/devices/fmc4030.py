# -*- coding: UTF-8 -*-
"""
FMC4030 三轴运动控制器 —— Python 控制封装（v2，按 SDK 头文件 FMC4030-Dll.h 对齐）
================================================================================
依据：SDK Demo/FMC4030-C++-demo/FMC4030-Dll-Test/FMC4030-Dll.h（权威声明）
      + 实际 DLL 导出表（31 个导出函数）交叉核对。

通讯：以太网/TCP。默认 IP 192.168.0.30，端口 8088。
位数：DLL 必须与 Python 位数一致（本机 64 位 Python -> X64 的 FMC4030-Dll.dll）。
依赖：VC++2012 运行库（vcredist_x86.exe）。

返回值：0 成功；-1 连接失败；-5 发送失败；-6 接收失败；-7 数据错误；-8 空指针。
"""

import os
from ctypes import (
    windll, c_int, c_int32, c_uint, c_uint32, c_uint16, c_float,
    c_char_p, c_ubyte, POINTER, Structure, pointer, byref,
)


# ============================ 轴 / 模式常量 ============================
AXIS_X = 0
AXIS_Y = 1
AXIS_Z = 2

MOVE_RELATIVE = 1        # 相对运动
MOVE_ABSOLUTE = 2        # 绝对运动

HOME_POS_LIMIT = 1       # 正限位回零
HOME_NEG_LIMIT = 2       # 负限位回零

STOP_DECEL = 1           # 减速停止
STOP_IMMEDIATE = 2       # 立即停止

ARC_CW = 1               # 顺时针圆弧
ARC_CCW = 2              # 逆时针圆弧

AXIS_XY = 0x03           # 两轴插补轴选
AXIS_XZ = 0x05
AXIS_YZ = 0x06

# 下载文件类型（FMC4030_Download_File 的 fileType）
FILE_TYPE_FIRMWARE = 1   # 控制卡升级文件（.bin）
FILE_TYPE_SCRIPT = 2     # 控制卡自动运行脚本文件（.fmc / .elo）

# 设备运行模式（machineRunStatus）
MACHINE_MANUAL = 0x0001  # 手动模式
MACHINE_AUTO = 0x0002    # 脚本自动运行模式

# 各轴状态位（axisStatus）
MACHINE_POWER_ON = 0x0000
MACHINE_RUNNING = 0x0001
MACHINE_PAUSE = 0x0002
MACHINE_RESUME = 0x0004
MACHINE_STOP = 0x0008
MACHINE_LIMIT_N = 0x0010
MACHINE_LIMIT_P = 0x0020
MACHINE_HOME_DONE = 0x0040
MACHINE_HOME = 0x0080
MACHINE_AUTO_RUN = 0x0100
MACHINE_LIMIT_N_NONE = 0x0200
MACHINE_LIMIT_P_NONE = 0x0400
MACHINE_HOME_NONE = 0x0800
MACHINE_HOME_OVERTIME = 0x1000


# ============================ 结构体 ============================
class MachineStatus(Structure):
    """machine_status —— Get_Machine_Status 的输出"""
    _fields_ = [
        ("realPos", c_float * 3),          # 三轴实时位置 mm
        ("realSpeed", c_float * 3),        # 三轴实时速度 mm/s
        ("inputStatus", c_uint32),         # 输入口状态
        ("outputStatus", c_uint32),        # 输出口状态
        ("limitNStatus", c_uint32),        # 负限位状态
        ("limitPStatus", c_uint32),        # 正限位状态
        ("machineRunStatus", c_uint32),    # 设备运行状态（手动/自动）
        ("axisStatus", c_uint32 * 3),      # 各轴状态位
        ("homeStatus", c_uint32),          # 回零状态（未用，用 axisStatus）
        ("file", c_ubyte * 600),           # char file[20][30] 控制器内脚本名
    ]


class MachineDevicePara(Structure):
    """machine_device_para —— Get/Set_Device_Para"""
    _fields_ = [
        ("id", c_uint32),
        ("bound232", c_uint32),
        ("bound485", c_uint32),
        ("ip", c_ubyte * 15),
        ("port", c_int),
        ("div", c_int * 3),                # 细分
        ("lead", c_int * 3),               # 导程
        ("softLimitMax", c_int * 3),       # 软限位上限
        ("softLimitMin", c_int * 3),       # 软限位下限
        ("homeTime", c_int * 3),           # 回零超时
    ]


class MachineVersion(Structure):
    """machine_version —— Get_Version_Info"""
    _fields_ = [
        ("firmware", c_uint32),
        ("lib", c_uint32),
        ("serialnumber", c_uint32),
    ]


# ============================ 封装类 ============================
class FMC4030:
    def __init__(self, dll_path=None):
        if dll_path is None:
            dll_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "FMC4030-Dll.dll")
        if not os.path.exists(dll_path):
            raise FileNotFoundError("找不到 DLL: %s" % dll_path)
        self._lib = windll.LoadLibrary(dll_path)
        self._dll_path = dll_path
        self.missing = []          # 本次 DLL 未导出的函数名
        self._bind_all()

    # ---- 只绑定实际存在的导出，缺一个不会拖垮全部 ----
    def _bind(self, name, argtypes):
        fn = getattr(self._lib, name, None)
        if fn is None:
            self.missing.append(name)
            return
        fn.argtypes = argtypes
        fn.restype = c_int

    def _bind_all(self):
        i, f, fp, cp, vp, ip = (
            c_int, c_float, POINTER(c_float), c_char_p, POINTER(c_ubyte),
            POINTER(c_int),
        )
        self._bind("FMC4030_Open_Device", [i, c_char_p, i])
        self._bind("FMC4030_Close_Device", [i])
        # 单轴
        self._bind("FMC4030_Jog_Single_Axis", [i, i, f, f, f, f, i])
        self._bind("FMC4030_Check_Axis_Is_Stop", [i, i])
        self._bind("FMC4030_Home_Single_Axis", [i, i, f, f, f, i])
        self._bind("FMC4030_Stop_Single_Axis", [i, i, i])
        self._bind("FMC4030_Get_Axis_Current_Pos", [i, i, fp])
        self._bind("FMC4030_Get_Axis_Current_Speed", [i, i, fp])
        # IO
        self._bind("FMC4030_Set_Output", [i, i, i])
        self._bind("FMC4030_Get_Input", [i, i, ip])
        # 485
        self._bind("FMC4030_Write_Data_To_485", [i, c_char_p, i])
        self._bind("FMC4030_Read_Data_From_485", [i, c_char_p, ip])
        # Modbus
        self._bind("FMC4030_MB01_Operation", [i, i, c_uint16, c_char_p, ip])
        self._bind("FMC4030_MB03_Operation", [i, i, c_uint16, i, c_char_p, ip])
        self._bind("FMC4030_MB05_Operation", [i, i, c_uint16, c_uint16, c_char_p, ip])
        self._bind("FMC4030_MB06_Operation", [i, i, c_uint16, c_uint16, c_char_p, ip])
        self._bind("FMC4030_MB16_Operation", [i, i, c_uint16, i, POINTER(c_uint16), c_char_p, ip])
        # 插补
        self._bind("FMC4030_Line_2Axis", [i, c_uint, f, f, f, f, f])
        self._bind("FMC4030_Line_3Axis", [i, c_uint, f, f, f, f, f, f])
        self._bind("FMC4030_Arc_2Axis", [i, c_uint, f, f, f, f, f, f, f, f, i])
        self._bind("FMC4030_Pause_Run", [i, c_uint])
        self._bind("FMC4030_Resume_Run", [i, c_uint])
        self._bind("FMC4030_Stop_Run", [i])
        # 状态 / 参数 / 版本
        self._bind("FMC4030_Get_Machine_Status", [i, POINTER(MachineStatus)])
        self._bind("FMC4030_Get_Device_Para", [i, POINTER(MachineDevicePara)])
        self._bind("FMC4030_Set_Device_Para", [i, POINTER(MachineDevicePara)])
        self._bind("FMC4030_Get_Version_Info", [i, POINTER(MachineVersion)])
        # 文件 / 自动运行
        self._bind("FMC4030_Download_File", [i, c_char_p, i])
        self._bind("FMC4030_Delete_Script_File", [i, c_char_p])
        self._bind("FMC4030_Start_Auto_Run", [i, c_char_p])
        self._bind("FMC4030_Stop_Auto_Run", [i])
        # 头文件声明了但本 DLL 未导出（保留占位，不绑定）
        self._bind("FMC4030_Set_FSC_Speed", [i, i, f])

    # ------------------------- 连接 -------------------------
    def open(self, dev_id, ip="192.168.0.30", port=8088):
        return self._lib.FMC4030_Open_Device(dev_id, ip.encode("utf-8"), port)

    def close(self, dev_id):
        return self._lib.FMC4030_Close_Device(dev_id)

    # ------------------------- 单轴 -------------------------
    def move_axis(self, dev_id, axis, pos, speed, acc, dec, mode=MOVE_ABSOLUTE):
        return self._lib.FMC4030_Jog_Single_Axis(
            dev_id, axis, c_float(pos), c_float(speed), c_float(acc), c_float(dec), mode)

    def home_axis(self, dev_id, axis, speed, accdec, fall_step=0.0, direction=HOME_NEG_LIMIT):
        return self._lib.FMC4030_Home_Single_Axis(
            dev_id, axis, c_float(speed), c_float(accdec), c_float(fall_step), direction)

    def stop_axis(self, dev_id, axis, mode=STOP_DECEL):
        return self._lib.FMC4030_Stop_Single_Axis(dev_id, axis, mode)

    def is_stopped(self, dev_id, axis):
        return self._lib.FMC4030_Check_Axis_Is_Stop(dev_id, axis) == 1

    def get_pos(self, dev_id, axis):
        """读单轴位置（mm）。

        ！不要用 FMC4030_Get_Axis_Current_Pos：本机控制器（固件 2015 + 库 20210906）
        实测该接口**恒返回 0**，而 Get_Machine_Status 里的 realPos 是准的。
        限位判断依赖这个值，读错会直接放行越界动作，所以统一从状态结构体取。
        """
        return float(self.get_status(dev_id).realPos[axis])

    def get_speed(self, dev_id, axis):
        """读单轴速度（mm/s）。同上，Get_Axis_Current_Speed 不可靠，取 realSpeed。"""
        return float(self.get_status(dev_id).realSpeed[axis])

    # ------------------------- IO -------------------------
    def set_output(self, dev_id, io, on):
        """on=True 导通(写1)，False 断开(写0)。底层开漏：0高 1低，此处已翻转。"""
        return self._lib.FMC4030_Set_Output(dev_id, io, 0 if on else 1)

    def get_input(self, dev_id, io):
        v = c_int(0)
        self._lib.FMC4030_Get_Input(dev_id, io, pointer(v))
        return v.value

    # ------------------------- 485 / Modbus -------------------------
    def write_485(self, dev_id, data: bytes):
        return self._lib.FMC4030_Write_Data_To_485(dev_id, data, len(data))

    def read_485(self, dev_id, maxlen=256):
        buf = b"\x00" * maxlen
        n = c_int(0)
        self._lib.FMC4030_Read_Data_From_485(dev_id, buf, pointer(n))
        return buf[:n.value]

    # ------------------------- 插补 -------------------------
    def line_2axis(self, dev_id, axis_mask, end_x, end_y, speed, acc, dec):
        return self._lib.FMC4030_Line_2Axis(
            dev_id, axis_mask, c_float(end_x), c_float(end_y),
            c_float(speed), c_float(acc), c_float(dec))

    def line_3axis(self, dev_id, end_x, end_y, end_z, speed, acc, dec):
        return self._lib.FMC4030_Line_3Axis(
            dev_id, 0, c_float(end_x), c_float(end_y), c_float(end_z),
            c_float(speed), c_float(acc), c_float(dec))

    def arc_2axis(self, dev_id, axis_mask, end_x, end_y, center_x, center_y,
                  radius, speed, acc, dec, direction=ARC_CW):
        return self._lib.FMC4030_Arc_2Axis(
            dev_id, axis_mask, c_float(end_x), c_float(end_y),
            c_float(center_x), c_float(center_y), c_float(radius),
            c_float(speed), c_float(acc), c_float(dec), direction)

    def pause(self, dev_id, axis=0):
        return self._lib.FMC4030_Pause_Run(dev_id, axis)

    def resume(self, dev_id, axis=0):
        return self._lib.FMC4030_Resume_Run(dev_id, axis)

    def stop_run(self, dev_id):
        return self._lib.FMC4030_Stop_Run(dev_id)

    # ------------------------- 状态 / 参数 / 版本 -------------------------
    def get_status(self, dev_id):
        ms = MachineStatus()
        self._lib.FMC4030_Get_Machine_Status(dev_id, pointer(ms))
        return ms

    def get_device_para(self, dev_id):
        p = MachineDevicePara()
        self._lib.FMC4030_Get_Device_Para(dev_id, pointer(p))
        return p

    def set_device_para(self, dev_id, para: MachineDevicePara):
        """危险：改错会导致设备异常/损坏，非必要别动。"""
        return self._lib.FMC4030_Set_Device_Para(dev_id, pointer(para))

    def get_version(self, dev_id):
        v = MachineVersion()
        self._lib.FMC4030_Get_Version_Info(dev_id, pointer(v))
        return v

    # ------------------------- 文件 / 自动运行（.fmc 脚本） -------------------------
    def download_script(self, dev_id, file_path):
        """把本地脚本(.fmc/.elo)上传到控制器。fileType=2。"""
        return self._lib.FMC4030_Download_File(
            dev_id, file_path.encode("utf-8"), FILE_TYPE_SCRIPT)

    def download_firmware(self, dev_id, file_path):
        """上传固件(.bin)。fileType=1。慎用。"""
        return self._lib.FMC4030_Download_File(
            dev_id, file_path.encode("utf-8"), FILE_TYPE_FIRMWARE)

    def start_auto_run(self, dev_id, file_name):
        """启动控制器内已存在的脚本文件名（不是本地路径）。"""
        return self._lib.FMC4030_Start_Auto_Run(dev_id, file_name.encode("utf-8"))

    def stop_auto_run(self, dev_id):
        return self._lib.FMC4030_Stop_Auto_Run(dev_id)

    def delete_script(self, dev_id, file_name):
        return self._lib.FMC4030_Delete_Script_File(dev_id, file_name.encode("utf-8"))

    def list_scripts(self, dev_id):
        """从状态结构体里解析控制器内脚本文件名列表。"""
        ms = self.get_status(dev_id)
        raw = bytes(ms.file)
        names = []
        for k in range(20):
            chunk = raw[k * 30:(k + 1) * 30]
            name = chunk.split(b"\x00", 1)[0].decode("utf-8", "ignore").strip()
            if name:
                names.append(name)
        return names


# ============================ 自检（不连硬件） ============================
def selftest(dll_path=None):
    c = FMC4030(dll_path)
    print("DLL:", c._dll_path)
    print("未导出的函数:", c.missing if c.missing else "无（全部绑定成功）")
    return c


if __name__ == "__main__":
    selftest()
