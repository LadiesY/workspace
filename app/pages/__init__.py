# -*- coding: utf-8 -*-
"""功能页面。占位页在 widgets.py，这里放已接入真实功能的页面。"""

from .auto_page import AutoPage
from .data_module import DataPage
from .device_module import DeviceModulePage
from .device_page import DevicePage
from .home_page import HomePage
from .kb_page import KbPage
from .motion_page import MotionPage
from .system_page import SystemPage
from .test_page import TestPage
from .vision_page import VisionPage

__all__ = ["AutoPage", "DataPage", "DeviceModulePage", "DevicePage",
           "HomePage", "KbPage", "MotionPage", "SystemPage", "TestPage",
           "VisionPage"]
