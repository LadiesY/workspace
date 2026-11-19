# -*- coding: utf-8 -*-
"""海康工业相机驱动（MVS SDK，ctypes 调用）。

枚举 / 打开 / 参数读写的调用顺序照着 D:\\PCB\\XJ\\hicam_tool.py 写的，
运行库（MvCameraControl.dll 及依赖）随程序带在 libs\\mvs 下，
整目录拷到别的机器也能跑，不要求系统装过 MVS。

可调参数取 XJ 界面上那几项：曝光、增益、帧率、分辨率（宽/高）、开窗偏移。

取流（预览）也是照 XJ 的做法：SDK 只负责把帧交出来，转成 QImage 后由界面自己画，
这样界面才能做缩放/平移 —— SDK 自带的 MV_CC_DisplayOneFrame 改不了显示比例。
"""

import os
import threading
import time
from ctypes import (WINFUNCTYPE, byref, c_ubyte, c_uint, c_void_p, cast,
                    memmove, memset, sizeof, POINTER)

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtGui import QImage

from . import mvs_loader
from .base import Driver, ST_DISCONNECTED

# 与 XJ 一致的曝光/增益范围（值本身仍以相机报回的 min/max 为准）
EXP_MIN, EXP_MAX = 1.0, 1000000.0
GAIN_MIN, GAIN_MAX = 0.0, 48.0
FPS_MIN, FPS_MAX = 0.1, 1000.0


def _decode(arr):
    try:
        return bytes(bytearray(arr)).split(b"\x00", 1)[0].decode("utf-8", "ignore")
    except Exception:
        return ""


class FrameBridge(QObject):
    """把取流线程的东西送回界面线程（跨线程信号自动排队）。"""

    frame = pyqtSignal(QImage)                # 已转成 QImage 的一帧
    stats = pyqtSignal(float, int, int)       # fps, 宽, 高
    grab_error = pyqtSignal(int)              # 取流错误码
    lost = pyqtSignal()                       # 相机掉线/被拔出


class GrabWorker(threading.Thread):
    """取流线程：取帧 → 转 QImage → 送界面 → 立刻释放 SDK 缓存。

    显示统一交给界面，这里只管取和转。
    """

    MAX_FPS_EMIT = 30.0                       # 送界面最多 30 帧/秒，避免高帧率拖慢 UI

    def __init__(self, M, cam, bridge, lost_evt):
        threading.Thread.__init__(self)
        self.daemon = True
        self.M = M
        self.cam = cam
        self.bridge = bridge
        self.lost_evt = lost_evt
        self._stop_evt = threading.Event()

    def stop(self):
        self._stop_evt.set()

    def run(self):
        M = self.M
        frame = M.MV_FRAME_OUT()
        t_last = time.time()
        t_emit = 0.0
        n_frames = 0
        err_streak = 0

        while not self._stop_evt.is_set():
            if self.lost_evt.is_set():
                self.bridge.lost.emit()
                break

            memset(byref(frame), 0, sizeof(frame))
            ret = self.cam.MV_CC_GetImageBuffer(frame, 300)
            if ret != 0:
                if ret == M.MV_E_NODATA:      # 没新帧是正常现象
                    continue
                err_streak += 1
                if err_streak == 1 or err_streak % 20 == 0:
                    self.bridge.grab_error.emit(ret)
                if err_streak >= 60:          # 连续约十几秒拿不到帧，按掉线处理
                    self.bridge.lost.emit()
                    break
                time.sleep(0.03)
                continue

            err_streak = 0
            info = frame.stFrameInfo

            now = time.time()
            if now - t_emit >= 1.0 / self.MAX_FPS_EMIT:
                img = self._to_qimage(frame)
                if img is not None:
                    self.bridge.frame.emit(img)
                    t_emit = now

            self.cam.MV_CC_FreeImageBuffer(frame)

            n_frames += 1
            if now - t_last >= 0.5:
                self.bridge.stats.emit(n_frames / (now - t_last),
                                       int(info.nWidth), int(info.nHeight))
                n_frames = 0
                t_last = now

    # ---------------------------------------------------------------- 转图
    def _to_qimage(self, frame):
        M = self.M
        info = frame.stFrameInfo
        w, h = int(info.nWidth), int(info.nHeight)
        if w <= 0 or h <= 0:
            return None

        # Mono8 直接映射成灰度图，省一次转换
        if info.enPixelType == M.PixelType_Gvsp_Mono8:
            img = QImage(w, h, QImage.Format_Grayscale8)
            n = w * h
            if not img.isNull() and img.bytesPerLine() == w:
                memmove((c_ubyte * n).from_address(int(img.bits())),
                        frame.pBufAddr, n)
                return img
            tmp = (c_ubyte * n)()
            memmove(tmp, frame.pBufAddr, n)
            return QImage(bytes(tmp), w, h, w, QImage.Format_Grayscale8).copy()

        # 其它格式（Bayer / Mono10 / Mono12 / YUV…）交给 SDK 转 RGB8
        n_dst = w * h * 3
        img = QImage(w, h, QImage.Format_RGB888)
        if not img.isNull() and img.bytesPerLine() == w * 3:
            dst = (c_ubyte * n_dst).from_address(int(img.bits()))
            need_copy = False
        else:
            dst = (c_ubyte * n_dst)()
            need_copy = True

        st = M.MV_CC_PIXEL_CONVERT_PARAM_EX()
        memset(byref(st), 0, sizeof(st))
        st.nWidth = w
        st.nHeight = h
        st.enSrcPixelType = info.enPixelType
        st.pSrcData = frame.pBufAddr
        st.nSrcDataLen = info.nFrameLen
        st.enDstPixelType = M.PixelType_Gvsp_RGB8_Packed
        st.pDstBuffer = dst
        st.nDstBufferSize = n_dst
        if self.cam.MV_CC_ConvertPixelTypeEx(st) != 0:
            return None
        if need_copy:
            return QImage(bytes(dst), w, h, w * 3, QImage.Format_RGB888).copy()
        return img


class CameraDriver(Driver):
    key = "camera"
    kind = "camera"

    def __init__(self, cfg):
        super(CameraDriver, self).__init__(cfg)
        self._M = None
        self._cam = None
        self.devices = []

        # 预览相关
        self.bridge = FrameBridge()           # 界面连它的信号，取流线程往里发
        self._worker = None
        self._streaming = False
        self._lost_evt = threading.Event()    # SDK 的掉线回调把这里置起来
        self._exc_cb = None                   # 回调必须留引用，否则会被 GC 掉

    # ---------------------------------------------------------------- 枚举
    def enumerate(self):
        """返回 [{"index", "label", "ip"/"sn"}]。失败抛 Exception（带人话原因）。"""
        M = mvs_loader.load()
        self._M = M
        dev_list = M.MV_CC_DEVICE_INFO_LIST()
        layer = (M.MV_GIGE_DEVICE | M.MV_USB_DEVICE | M.MV_GENTL_CAMERALINK_DEVICE
                 | M.MV_GENTL_CXP_DEVICE | M.MV_GENTL_XOF_DEVICE)
        ret = M.MvCamera.MV_CC_EnumDevices(layer, dev_list)
        if ret != 0:
            raise RuntimeError("枚举设备失败（0x%08X）" % (ret & 0xFFFFFFFF))

        out = []
        for i in range(dev_list.nDeviceNum):
            info = cast(dev_list.pDeviceInfo[i], POINTER(M.MV_CC_DEVICE_INFO)).contents
            item = {"index": i, "info": info, "label": ""}
            t = info.nTLayerType
            if t in (M.MV_GIGE_DEVICE, M.MV_GENTL_GIGE_DEVICE):
                gige = info.SpecialInfo.stGigEInfo
                ip = gige.nCurrentIp
                ip_str = "%d.%d.%d.%d" % ((ip >> 24) & 0xFF, (ip >> 16) & 0xFF,
                                          (ip >> 8) & 0xFF, ip & 0xFF)
                model = _decode(gige.chModelName)
                user = _decode(gige.chUserDefinedName)
                item["label"] = ("GigE %s %s (%s)" % (user, model, ip_str)).strip()
                item["ip"] = ip_str
            elif t == M.MV_USB_DEVICE:
                usb = info.SpecialInfo.stUsb3VInfo
                item["label"] = ("USB %s (%s)" % (_decode(usb.chModelName),
                                                  _decode(usb.chSerialNumber))).strip()
                item["sn"] = _decode(usb.chSerialNumber)
            elif t == M.MV_GENTL_CXP_DEVICE:
                item["label"] = "CXP %s" % _decode(info.SpecialInfo.stCXPInfo.chModelName)
            elif t == M.MV_GENTL_CAMERALINK_DEVICE:
                item["label"] = "CML %s" % _decode(info.SpecialInfo.stCMLInfo.chModelName)
            elif t == M.MV_GENTL_XOF_DEVICE:
                item["label"] = "XoF %s" % _decode(info.SpecialInfo.stXoFInfo.chModelName)
            else:
                item["label"] = "未知设备 [%d]" % i
            out.append(item)
        self.devices = out
        return out

    # ---------------------------------------------------------------- 连接
    def connect(self):
        try:
            devs = self.enumerate()
        except mvs_loader.MvsRuntimeNotFound as e:
            return self._fail("找不到海康 MVS 运行库", str(e))
        except Exception as e:
            return self._fail("枚举相机失败", str(e))

        if not devs:
            return self._fail("未发现相机",
                              "检查：网线/供电；GigE 相机要与本机网卡同网段；"
                              "USB3 相机直插主板口不要经 HUB；MVS 客户端是否占用了相机")

        try:
            idx = int(self.cfg.get("device_index", 0))
        except (TypeError, ValueError):
            idx = 0
        if not (0 <= idx < len(devs)):
            return self._fail("配置里选的相机序号 %d 不存在（当前发现 %d 台）"
                              % (idx, len(devs)))

        M = self._M
        dev = devs[idx]
        cam = M.MvCamera()
        ret = cam.MV_CC_CreateHandle(dev["info"])
        if ret != 0:
            return self._fail("创建相机句柄失败（0x%08X）" % (ret & 0xFFFFFFFF))

        ret = cam.MV_CC_OpenDevice(M.MV_ACCESS_Exclusive, 0)
        if ret != 0:
            cam.MV_CC_DestroyHandle()
            return self._fail(
                "打开相机失败（0x%08X）" % (ret & 0xFFFFFFFF),
                "常见原因：被 MVS 客户端或别的程序独占；USB 带宽不足；网卡未配巨帧")

        # GigE 要按链路自动设包大小，不然会丢包花屏
        if dev["info"].nTLayerType in (M.MV_GIGE_DEVICE, M.MV_GENTL_GIGE_DEVICE):
            try:
                pkt = int(cam.MV_CC_GetOptimalPacketSize())
            except Exception:
                pkt = -1
            if pkt > 0:
                cam.MV_CC_SetIntValue("GevSCPSPacketSize", pkt)

        cam.MV_CC_SetImageNodeNum(3)
        cam.MV_CC_SetEnumValue("TriggerMode", M.MV_TRIGGER_MODE_OFF)

        self._cam = cam
        self._lost_evt.clear()
        self._register_exception_cb(cam)
        params = self.read_params()
        return self._ok({
            "model": dev["label"],
            "endpoint": dev["label"],
            "params": params,
        })

    def _register_exception_cb(self, cam):
        """注册掉线回调：拔网线/拔 USB 时 SDK 会调到这里，界面据此断开并提示。"""
        M = self._M
        proto = WINFUNCTYPE(None, c_uint, c_void_p)

        def _cb(exc_type, _user):
            if exc_type == M.MV_EXCEPTION_DEV_DISCONNECT:
                self._lost_evt.set()

        self._exc_cb = proto(_cb)
        try:
            cam.MV_CC_RegisterExceptionCallBack(self._exc_cb, None)
        except Exception:
            pass

    # ---------------------------------------------------------------- 取流
    @property
    def streaming(self):
        return self._streaming

    def start_stream(self):
        """开始取流。返回 (是否成功, 说明)。"""
        if self._cam is None:
            return False, "相机未连接"
        if self._streaming:
            return True, "已在取流"
        ret = self._cam.MV_CC_StartGrabbing()
        if ret != 0:
            return False, "开始取流失败（0x%08X）" % (ret & 0xFFFFFFFF)
        self._lost_evt.clear()
        self._worker = GrabWorker(self._M, self._cam, self.bridge, self._lost_evt)
        self._worker.start()
        self._streaming = True
        return True, "取流已开始"

    def stop_stream(self):
        """停止取流。先让取流线程退出，再调 SDK，顺序反了会卡在 GetImageBuffer。"""
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.stop()
            worker.join(timeout=2.0)
        if self._cam is not None and self._streaming:
            try:
                self._cam.MV_CC_StopGrabbing()
            except Exception:
                pass
        self._streaming = False

    def disconnect(self):
        self.stop_stream()
        if self._cam is not None:
            try:
                self._cam.MV_CC_CloseDevice()
            except Exception:
                pass
            try:
                self._cam.MV_CC_DestroyHandle()
            except Exception:
                pass
        self._cam = None
        self.state = ST_DISCONNECTED
        self.info = {}
        return True, "已断开"

    # ---------------------------------------------------------------- 读参数
    def _q_float(self, key):
        st = self._M.MVCC_FLOATVALUE()
        memset(byref(st), 0, sizeof(st))
        if self._cam.MV_CC_GetFloatValue(key, st) != 0:
            return None
        return st.fCurValue, st.fMin, st.fMax

    def _q_int(self, key):
        st = self._M.MVCC_INTVALUE()
        memset(byref(st), 0, sizeof(st))
        if self._cam.MV_CC_GetIntValue(key, st) != 0:
            return None
        return st.nCurValue, st.nMin, st.nMax, (st.nInc or 1)

    def _align_int(self, key, value):
        """把宽高/偏移对齐到该节点的步进，很多相机要求 8/16 的整数倍。"""
        r = self._q_int(key)
        if not r:
            return int(value)
        _cur, lo, hi, inc = r
        inc = max(int(inc), 1)
        v = int(value)
        v = lo + (v - lo) // inc * inc
        return int(max(lo, min(hi, v)))

    def read_params(self):
        """读回相机当前参数，未连接的项返回 None。"""
        if self._cam is None:
            return {}
        out = {}
        r = self._q_float("ExposureTime")
        if r:
            out["exposure"], out["exposure_min"], out["exposure_max"] = r
        r = self._q_float("Gain")
        if r:
            out["gain"], out["gain_min"], out["gain_max"] = r
        r = self._q_float("ResultingFrameRate") or self._q_float("AcquisitionFrameRate")
        if r:
            out["fps"], out["fps_min"], out["fps_max"] = r
        for key in ("Width", "Height", "OffsetX", "OffsetY"):
            r = self._q_int(key)
            if r:
                out[key.lower()] = r[0]
                out["%s_max" % key.lower()] = r[2]
        return {k: (round(v, 3) if isinstance(v, float) else v)
                for k, v in out.items()}

    # ---------------------------------------------------------------- 下发
    def apply(self):
        if self._cam is None:
            return False, "未连接，参数已保存但还没有下发到相机"

        M = self._M
        cfg = self.cfg
        errs = []

        auto = bool(cfg.get("auto_exposure"))
        try:
            self._cam.MV_CC_SetEnumValueByString("ExposureAuto", "Continuous" if auto else "Off")
        except Exception:
            pass
        if not auto:
            r = self._cam.MV_CC_SetFloatValue("ExposureTime", float(cfg.get("exposure", 330000.0)))
            if r != 0:
                errs.append("曝光 0x%08X" % (r & 0xFFFFFFFF))

        try:
            self._cam.MV_CC_SetEnumValueByString("GainAuto", "Off")
        except Exception:
            pass
        r = self._cam.MV_CC_SetFloatValue("Gain", float(cfg.get("gain", 0.0)))
        if r != 0:
            errs.append("增益 0x%08X" % (r & 0xFFFFFFFF))

        try:
            self._cam.MV_CC_SetBoolValue("AcquisitionFrameRateEnable", True)
        except Exception:
            pass
        r = self._cam.MV_CC_SetFloatValue("AcquisitionFrameRate", float(cfg.get("fps", 3.03)))
        if r != 0:
            errs.append("帧率 0x%08X" % (r & 0xFFFFFFFF))

        w = int(cfg.get("width") or 0)
        h = int(cfg.get("height") or 0)
        ox = int(cfg.get("offset_x") or 0)
        oy = int(cfg.get("offset_y") or 0)
        if w > 0 and h > 0:
            # 取流中改宽高/偏移，相机端基本都会拒绝，这里先停流、下发，再自动续上
            was_streaming = self._streaming
            if was_streaming:
                self.stop_stream()
            try:
                # 顺序很重要：先把偏移归零，才能设大分辨率；设完再挪回去
                self._cam.MV_CC_SetIntValue("OffsetX", 0)
                self._cam.MV_CC_SetIntValue("OffsetY", 0)
                rw = self._cam.MV_CC_SetIntValue("Width", self._align_int("Width", w))
                rh = self._cam.MV_CC_SetIntValue("Height", self._align_int("Height", h))
                if rw != 0:
                    errs.append("宽度 0x%08X" % (rw & 0xFFFFFFFF))
                if rh != 0:
                    errs.append("高度 0x%08X" % (rh & 0xFFFFFFFF))
                if ox > 0:
                    self._cam.MV_CC_SetIntValue("OffsetX", self._align_int("OffsetX", ox))
                if oy > 0:
                    self._cam.MV_CC_SetIntValue("OffsetY", self._align_int("OffsetY", oy))
            finally:
                if was_streaming:
                    self.start_stream()

        self.info["params"] = self.read_params()
        if errs:
            return False, "部分参数下发失败：" + "、".join(errs)
        return True, "相机参数已下发"

    # ---------------------------------------------------------------- 状态
    def read_state(self):
        if self._cam is None:
            return {}
        return self.read_params()
