# -*- coding: utf-8 -*-
"""mvs_loader.py —— 定位并加载海康 MVS 运行时 (MvCameraControl.dll)。

Python 3.8+ 的 ctypes 默认不再搜索 PATH，而 MvImport 里的封装用的是
WinDLL(dllname, winmode=0)（老式搜索顺序），所以只要在 import 之前把
DLL 所在目录塞进 PATH 即可生效。本模块按以下顺序查找：

  1. 环境变量 MVS_RUNTIME_DIR
  2. 配置里手工指定的目录（自己填的运行库位置）
  3. 本工程自带的 libs\\mvs（默认，整目录拷走就能用）
  4. 各盘符下的 MVS 标准安装路径（C:\\Program Files (x86)\\MVS\\Runtime\\Win64 等）

找到后返回已 import 的 MvCameraControl_class 模块。
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MVIMPORT_DIR = os.path.join(BASE_DIR, "MvImport")
MVS_LOCAL_DIR = os.path.join(BASE_DIR, "libs", "mvs")
DLL_NAME = "MvCameraControl.dll"


class MvsRuntimeNotFound(RuntimeError):
    """找不到 MvCameraControl.dll 时抛出。"""


def _standard_dirs():
    """MVS 客户端/开发包的标准运行库目录。"""
    dirs = []
    for drive in ("C", "D", "E", "F"):
        dirs += [
            r"%s:\Program Files (x86)\MVS\Runtime\Win64" % drive,
            r"%s:\Program Files\MVS\Runtime\Win64" % drive,
            r"%s:\Program Files (x86)\Common Files\MVS\Runtime\Win64" % drive,
            r"%s:\Program Files\Common Files\MVS\Runtime\Win64" % drive,
            r"%s:\MVS\Runtime\Win64" % drive,
            r"%s:\MVS\Development\Samples\Python\MvImport" % drive,
        ]
    return dirs


def find_runtime_dir(extra_dir=None):
    """返回第一个包含 MvCameraControl.dll 的目录；找不到返回 None。"""
    candidates = []
    env = os.environ.get("MVS_RUNTIME_DIR")
    if env:
        candidates.append(env)
    if extra_dir:
        candidates.append(extra_dir)
    # 本工程自带的运行库副本优先：整目录拷走就能用，不依赖系统装没装 MVS
    candidates.append(MVS_LOCAL_DIR)
    candidates.append(os.path.join(BASE_DIR, "runtime"))
    candidates.append(os.path.join(BASE_DIR, "runtime", "Win64"))
    candidates += _standard_dirs()

    for d in candidates:
        if d and os.path.isfile(os.path.join(d, DLL_NAME)):
            return os.path.abspath(d)

    return None


_loaded_module = None
_loaded_dir = None


def load(extra_dir=None):
    """加载并返回 MvCameraControl_class 模块（进程内只加载一次）。"""
    global _loaded_module, _loaded_dir
    if _loaded_module is not None:
        return _loaded_module

    dll_dir = find_runtime_dir(extra_dir)
    if dll_dir is None:
        raise MvsRuntimeNotFound(
            "未找到 MvCameraControl.dll。\n\n"
            "请任选一种方式解决：\n"
            "1) 安装 MVS 客户端：D:\\PCB\\海康相机软件\\海康相机软件\\MVS_STD_4.3.2_240705.exe\n"
            "2) 安装 MVS SDK：D:\\PCB\\海康相机软件\\海康相机软件\\MVS_SDK_V4_4_0_4_..._STD.exe\n"
            "3) 把运行库目录（含 MvCameraControl.dll 及其依赖 DLL）复制到\n"
            "   %s\\runtime\\ 下\n"
            "4) 在 config.json 里写 \"runtime_dir\": \"<运行库目录>\"\n"
            "5) 设置环境变量 MVS_RUNTIME_DIR=<运行库目录>"
            % BASE_DIR
        )

    # 老式 DLL 搜索顺序会看 PATH；add_dll_directory 兼顾新式加载
    os.environ["PATH"] = dll_dir + os.pathsep + os.environ.get("PATH", "")
    try:
        os.add_dll_directory(dll_dir)
    except (AttributeError, OSError):
        pass

    if MVIMPORT_DIR not in sys.path:
        sys.path.insert(0, MVIMPORT_DIR)

    import MvCameraControl_class as mvs  # noqa: E402

    _loaded_module = mvs
    _loaded_dir = dll_dir
    return mvs


def version_text():
    """返回 (dll 目录, SDK 版本字符串)。"""
    if _loaded_module is None:
        return _loaded_dir or "", ""
    try:
        v = _loaded_module.MvCamera.MV_CC_GetSDKVersion()
        return _loaded_dir or "", "%d.%d.%d.%d" % (
            (v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
    except Exception:
        return _loaded_dir or "", ""
