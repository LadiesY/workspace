# -*- coding: utf-8 -*-
"""设备层：三个驱动的封装 + 配置 + 管理器。

界面只 import 这个包，不直接碰 ctypes / DLL。
"""

from .base import (ST_CONNECTED, ST_CONNECTING, ST_DISCONNECTED, ST_ERROR,
                   DeviceError)
from .manager import DEVICE_META, DEVICE_ORDER, DeviceManager

__all__ = [
    "DeviceManager", "DEVICE_ORDER", "DEVICE_META",
    "ST_CONNECTED", "ST_CONNECTING", "ST_DISCONNECTED", "ST_ERROR",
    "DeviceError",
]
