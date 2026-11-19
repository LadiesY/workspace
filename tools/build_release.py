# -*- coding: utf-8 -*-
"""打包发布：把运行需要的东西整理成一个能直接双击启动的文件夹（可选压 zip）。

用法（在项目根目录下）：

    runtime\\python\\python.exe tools\\build_release.py
    runtime\\python\\python.exe tools\\build_release.py --zip
    runtime\\python\\python.exe tools\\build_release.py --name v0.2 --zip

产出：

    ..\\发布\\电路板测试系统_v0.1\\          ← 整个文件夹拷给别人即可
    ..\\发布\\电路板测试系统_v0.1.zip        ← --zip 时顺带压一份

刻意**不**带进发布包的东西：

  * ``data\\devices.json`` —— 里面有本机的项目和程序清单，对方应该从默认配置起。
    配置文件不存在时 ``store.load()`` 会回默认值，第一次保存参数才生成。
  * ``data\\startup.log`` —— 本机的启动历史，对别人没用。
  * ``data\\kb\\history.json`` —— 本机的搜索历史，属于使用痕迹。
  * ``__pycache__`` / ``*.pyc`` —— 编译缓存，换机器版本对不上反而添乱。
  * ``runtime\\python`` 下的 Doc / Tools / include / libs / tcl / share / Scripts ——
    Python 自带的文档、C 头文件、tkinter 用的 Tcl/Tk、pip 的启动脚本，
    本程序一个都不用，砍掉省 20 多 MB，也让目录看起来干净。

**要**带进发布包的 ``data\\kb\\``（知识库本体）：``sources.json``（资料清单）、
``chunks.json``（切好的片段索引）、``files\\``（资料原件）。这三样缺一样知识库
就是空的，所以不能像以前那样把 ``data\\`` 整个清空。索引和原件版本对得上，
换个路径也能直接查，不需要对方重建。

**不能砍**的（砍了就起不来或功能残缺）：

  runtime\\python\\DLLs        Python 扩展模块（_ssl、_ctypes 等）
  runtime\\python\\Lib         标准库 + site-packages（PyQt5 / cv2 / numpy / torch / ultralytics）
  libs\\                      FMC4030-Dll.dll、GAS.dll、海康 MVS SDK
  MvImport\\                  海康相机 Python 封装（mvs_loader 从这里导入）

两个踩过的坑，写在这里免得下次重犯：

1. **.bat 必须用 GBK 写，且不要 ``chcp 65001``。** UTF-8 中文注释 + chcp 65001
   会让 cmd 在错误的字节位置断行，直接报 ``xxx was unexpected at this time``、
   脚本一行都不执行。GBK 是中文 Windows 控制台的默认代码页，最稳。
2. **安装路径含中文时 Qt 找不到插件。** PyQt5 由 DLL 位置推算插件目录时会把
   非 ASCII 字符转成 ``?``，``QApplication()`` 构造会**卡死**（不报错、不弹窗、
   双击像没反应）。main.py 和启动 bat 都显式设 ``QT_PLUGIN_PATH`` 绕开。
"""

import argparse
import os
import shutil
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)                      # 项目根 D:\PCB\JC
OUT_ROOT = os.path.join(os.path.dirname(SRC), "发布")

# 要带走的文件 / 目录（相对项目根）
FILES = ["main.py", "README.md", "编辑程序示例.xlsx"]
DIRS = ["app", "pintool", "libs", "MvImport", "runtime"]

# runtime\python 下与运行无关的目录，打包时跳过
RUNTIME_SKIP = {"Doc", "Tools", "include", "libs", "tcl", "share", "Scripts"}


def _ignore(dirpath, names):
    """copytree 的 ignore 回调：跳过缓存，以及 runtime\\python 下的无关目录。"""
    skip = set(n for n in names
               if n == "__pycache__" or n.endswith((".pyc", ".pyo")))
    real = os.path.normcase(os.path.abspath(dirpath))
    if real == os.path.normcase(os.path.join(SRC, "runtime", "python")):
        skip |= set(n for n in names if n in RUNTIME_SKIP)
    return skip


LAUNCH_BAT = r"""@echo off
title 电路板测试系统
cd /d "%~dp0"

rem NOTE: this file is GBK encoded. Do NOT add "chcp 65001" here:
rem UTF-8 Chinese comments + chcp 65001 makes cmd split lines in the wrong
rem place and abort with "<text> was unexpected at this time".

set "PYW=%~dp0runtime\python\pythonw.exe"
set "PY=%~dp0runtime\python\python.exe"
set "APP=%~dp0main.py"

rem Qt plugin dir has to be set by hand: PyQt5 works it out from the dll
rem location and loses non-ASCII chars on the way, so a Chinese install path
rem yields "D:/PCB/??/..." and QApplication hangs with no error at all.
rem main.py sets it too; this is the second line of defence.
set "QT_PLUGIN_PATH=%~dp0runtime\python\Lib\site-packages\PyQt5\Qt5\plugins"

if not exist "%APP%" goto NOAPP

rem pythonw first: no black console window, the app shows its own error box
set "RUN=%PYW%"
if not exist "%PYW%" set "RUN=%PY%"
if not exist "%RUN%" goto NOPY

start "" "%RUN%" "%APP%"
exit /b 0

:NOAPP
echo.
echo [错误] 找不到主程序 main.py
echo        期望位置：%APP%
echo        请确认复制的是完整文件夹，不要只拿几个文件。
echo.
pause
exit /b 1

:NOPY
echo.
echo [错误] 找不到内置的 Python 运行时
echo        期望位置：%PYW%
echo        这个文件夹可能没复制完整，请重新复制整个文件夹。
echo.
pause
exit /b 1
"""

DIAG_BAT = r"""@echo off
title 电路板测试系统 · 诊断启动
cd /d "%~dp0"

rem Same as the launcher: GBK file, no chcp, ASCII-only comments.
rem This one keeps the console open so every error stays visible.

echo ============================================================
echo  电路板测试系统 · 诊断启动
echo  正常启动请双击“启动电路板测试系统.bat”，这个窗口是排查用的。
echo ============================================================
echo.

if not exist "%~dp0runtime\python\python.exe" (
    echo [错误] 找不到 runtime\python\python.exe
    echo        这个文件夹没复制完整。
    echo.
    pause
    exit /b 1
)

set "QT_PLUGIN_PATH=%~dp0runtime\python\Lib\site-packages\PyQt5\Qt5\plugins"

echo 解释器：%~dp0runtime\python\python.exe
echo 工作目录：%~dp0
echo 插件目录：%QT_PLUGIN_PATH%
echo.
echo 正在启动，关闭窗口即退出程序……
echo ------------------------------------------------------------

"%~dp0runtime\python\python.exe" "%~dp0main.py"
set "CODE=%ERRORLEVEL%"

echo ------------------------------------------------------------
echo 程序已退出，返回码：%CODE%
if not "%CODE%"=="0" (
    echo 启动日志：%~dp0data\startup.log
    echo 把这个窗口截图发回来，能看出卡在哪一步。
)
echo.
pause
"""

USAGE_TXT = r"""电路板测试系统 %(name)s
打包时间：%(date)s
============================================================

一、怎么启动
------------------------------------------------------------
双击 「启动电路板测试系统.bat」 就行。

* 不需要装 Python、不需要装任何库 —— 文件夹里已经带了运行环境
  （runtime\python，Python 3.9）和所需依赖（PyQt5 / OpenCV / PyTorch 等）。
* **不需要联网**。整个文件夹是自包含的，拷到内网机、离线机都能用。
* 第一次启动可能很慢（见过 1 分钟以上）：文件刚从 U 盘/网络拷过来，
  杀毒软件要逐个扫描 1.2 GB 的文件。第二次起就是 3~8 秒。
  如果等了很久还没窗口，也不要连点，双击一次就够了。
* 第一次拿到建议先双击 「环境自检.bat」，十几秒后会逐项告诉你缺什么；
  全部显示 [ OK ] 再启动最稳妥。
* 双击后有几秒没有反应是正常的：要加载 Qt 和视觉模块，约 3~8 秒。
* 如果双击后什么都没发生，或者弹了错误框：
  1) 先双击 「诊断启动.bat」 —— 它会带着控制台跑，报错直接显示在窗口里；
  2) 还是看不出来的话，把 data\startup.log 发回来。

二、注意事项（很重要）
------------------------------------------------------------
1. 整个文件夹一起拷，不要只拿 main.py。
   程序靠相对路径找 libs\（板卡 DLL）、MvImport\（相机 SDK）、runtime\（Python）。
2. 放在哪里都行，路径里有中文也没关系（启动时程序会自己指定 Qt 插件目录）；
   但不要放在需要管理员权限的目录（C:\Program Files 之类），否则 data\ 写不进去。
3. 杀毒软件可能拦截 pythonw.exe 或 libs 里的板卡 DLL，
   第一次运行请允许；被隔离了会表现为“双击没反应”。
4. 配置文件是 data\devices.json，跟着文件夹走，不写注册表、不写系统目录。
   首次运行会用默认参数（三轴 192.168.0.30:8088、探针 192.168.0.200、
   相机默认曝光），连设备前请在「设备管理」里核对成现场实际参数。
5. 知识库带了两份资料（相机手册、测试程序编写说明）和已经切好的索引，
   打开「知识库」直接用，不用重新导入、也不用联网建索引。
6. 当前状态：左侧 8 个模块 —— 首页（宣传页）、设备管理（含运动控制）、测试管理、
   自动测试、视觉定位、数据管理（只做布局）、知识库、系统管理（登录 / 角色权限 /
   审计日志 / 备份恢复 / 系统设置）。
   数据管理还不接采集数据；自动测试流程还没有执行器，按钮只写日志不动轴。
   已能用的部分见 README.md 的「七、现在有什么」。
7. **账户**：02~08 模块要先登入（首页可以直接看）；关掉程序就自动退出账户。
   第一次用请先在「系统管理 → 用户管理」里建账号，或直接用登入框的「注册」。
   内置三个角色：管理员（全能）/ 工程师（配置类，不含账号与审计）/ 操作员（只看+跑）。
8. **视觉定位 → 类别管理**：内置 6 类不可删；新增类别 = 选基础大类 + 每排引脚数 +
   一个 1~3 字母的名字，识别时转调该大类的算法。新增的类别会自动出现在
   「测试管理 → 测试程序编写」的「矩形框」列候选里。

三、需要什么硬件（没有也能打开软件）
------------------------------------------------------------
* 三轴控制器 FMC4030，网口，默认 192.168.0.30:8088
* 探针座六轴卡 GAS，网口，默认 192.168.0.200
* 海康工业相机（MVS SDK，libs\mvs）
没有硬件时界面能正常打开、参数能改能存，只是连不上、动不了。

四、目录里都有什么
------------------------------------------------------------
启动电路板测试系统.bat   双击启动
环境自检.bat             离线自检：查解释器、依赖、Qt 插件、DLL、知识库
诊断启动.bat             带控制台启动，排查用
main.py                  程序入口
selftest.py              自检脚本本体（由「环境自检.bat」调用）
app\                     界面与业务逻辑
pintool\                 引脚识别算法 + YOLO 权重（weights\best.pt）
libs\                    板卡 DLL、海康 MVS SDK
MvImport\                海康相机 Python 封装
runtime\python\          内置 Python 3.9 + 全部依赖
data\                    配置与日志（首次运行自动生成）
data\kb\                 知识库：资料清单、片段索引、资料原件
编辑程序示例.xlsx        测试程序那张表的填写示例
README.md                开发文档：每个模块做到哪、怎么实现、踩过什么坑

五、给别人之前
------------------------------------------------------------
这个包里没有带本机的项目清单和测试程序（data\devices.json 是空的），
对方打开时是干净的初始状态，项目/程序都要自己新建；知识库资料是带了的。
"""


SELFTEST_PY = r'''# -*- coding: utf-8 -*-
"""离线自检：确认这个文件夹在一台没装 Python、没有网的机器上能不能跑起来。

由「环境自检.bat」调用，只用本目录自带的东西，不联网、不写注册表。
逐项打印 [ OK ] / [FAIL]，最后给一句结论。
"""

import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

QT_PLUGINS = os.path.join(HERE, "runtime", "python", "Lib",
                          "site-packages", "PyQt5", "Qt5", "plugins")

_ok = 0
_bad = []


def _t(t0):
    return "  (%.1fs)" % (time.time() - t0)


def head(text):
    print("")
    print("-- %s %s" % (text, "-" * max(2, 54 - len(text))))


def chk(name, fn, hint=""):
    global _ok
    t0 = time.time()
    try:
        info = fn()
        _ok += 1
        print("  [ OK ] %-20s %s%s" % (name, info or "", _t(t0)))
        return True
    except Exception as e:
        _bad.append((name, "%s: %s" % (type(e).__name__, e)))
        print("  [FAIL] %-20s %s" % (name, e))
        if hint:
            print("         %s" % hint)
        return False


def soft(name, fn):
    """非致命项：现场没接设备时本来就该是这个结果，不算失败。"""
    t0 = time.time()
    try:
        info = fn()
        print("  [ ok ] %-20s %s%s" % (name, info or "", _t(t0)))
    except Exception as e:
        print("  [ -- ] %-20s %s（不影响启动）%s" % (name, e, _t(t0)))


def need_file(rel):
    def f():
        p = os.path.join(HERE, rel.replace("/", os.sep))
        if not os.path.isfile(p):
            raise IOError("缺少 %s" % rel)
        return "%.1f MB" % (os.path.getsize(p) / 1048576.0) if os.path.getsize(p) > 1048576 else "在"
    return f


def need_import(mod):
    def f():
        __import__(mod)
        return getattr(sys.modules[mod], "__version__", "") or "在"
    return f


def main():
    print("=" * 62)
    print("  电路板测试系统 · 环境自检")
    print("  只查这一份文件夹自身，不需要联网，也不需要装 Python")
    print("=" * 62)

    head("解释器")
    print("  可执行   %s" % sys.executable)
    print("  版本     %s" % sys.version.split()[0])
    print("  主目录   %s" % sys.prefix)
    print("  程序目录 %s" % HERE)
    chk("64 位解释器", lambda: (_ for _ in ()).throw(
        RuntimeError("这是 32 位 Python")) if sys.maxsize <= 2 ** 32 else "是",
        "现场机器都是 64 位，32 位解释器加载不了 cv2/torch")
    chk("自带 site-packages", lambda: (
        os.path.join(sys.prefix, "Lib", "site-packages") if os.path.isdir(
            os.path.join(sys.prefix, "Lib", "site-packages"))
        else (_ for _ in ()).throw(RuntimeError("site-packages 不在包里"))))

    head("依赖库")
    for m in ("numpy", "cv2", "PyQt5.QtCore", "PyQt5.QtGui", "PyQt5.QtWidgets"):
        chk(m, need_import(m))
    chk("Pillow", need_import("PIL"))
    chk("torch（慢，几秒）", need_import("torch"),
        "视觉定位的 YOLO 模型要靠它，缺了只能手动画框")
    chk("ultralytics", need_import("ultralytics"))

    head("Qt 与字体")
    os.environ.setdefault("QT_PLUGIN_PATH", QT_PLUGINS)

    def _qt():
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QApplication
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        app = QApplication.instance() or QApplication(sys.argv)
        globals()["_app"] = app          # 留住引用，别让 Qt 对象被回收
        plat = app.platformName()
        if plat != "windows":
            raise RuntimeError("平台插件是 %r，不是 windows" % plat)
        from PyQt5.QtGui import QFontDatabase
        n = len(QFontDatabase().families())
        if n < 10:
            raise RuntimeError("只找到 %d 种字体" % n)
        return "插件 %s，字体 %d 种" % (plat, n)

    ok_qt = chk("创建 QApplication", _qt,
                "插件目录设成了 %s" % QT_PLUGINS)

    head("界面与功能模块")
    if ok_qt:
        def _win():
            from app import theme
            theme.init_fonts()
            globals()["_app"].setStyleSheet(theme.qss())
            from app.main_window import MainWindow
            from app.modules import MODULES
            win = MainWindow()
            globals()["_win"] = win
            names = []
            for i in range(len(MODULES)):
                win.select_module(i)
                names.append(MODULES[i]["name"])
            return "%d 个模块：%s" % (len(MODULES), " ".join(names))

        ok_win = chk("构建主窗口", _win,
                     "data/startup.log 里有详细报错")
        if ok_win:
            def _kbsearch():
                """真的查一次，确认包里的索引能用，不只是文件在。"""
                from app.pages.kb_page import KbPage          # noqa: F401
                from app.knowledge import search, store
                d = store.load()
                idx = search.Index(d["chunks"])
                hits = idx.query("曝光时间", limit=3)
                if not hits:
                    raise RuntimeError("检索「曝光时间」没有命中，索引与资料可能对不上")
                return "查「曝光时间」命中 %d 段" % len(hits)

            soft("知识库检索", _kbsearch)
    else:
        ok_win = False
        print("  [跳过] 主窗口 —— Qt 没起来，后面的都不查了")

    head("板卡与相机")
    for rel in ("libs/FMC4030-Dll.dll", "libs/GAS.dll",
                "libs/mvs/MvCameraControl.dll", "MvImport/MvCameraControl_class.py"):
        chk(rel, need_file(rel), "少了就连不上对应设备（软件仍能打开）")

    def _mvs():
        from app.devices import mvs_loader
        mvs_loader.load()
        return "已加载 MvCameraControl.dll"

    soft("海康相机运行时", _mvs)

    head("知识库")
    def _kb():
        from app.knowledge import store
        d = store.load()
        return "%d 份资料 / %d 个片段" % (len(d["sources"]), len(d["chunks"]))
    chk("资料与索引", _kb, "data/kb 不完整时知识库会是空的，问答查不到东西")

    head("读写权限")
    def _w():
        p = os.path.join(HERE, "data")
        if not os.path.isdir(p):
            os.makedirs(p)
        f = os.path.join(p, ".write_test")
        with open(f, "w", encoding="utf-8") as fp:
            fp.write("ok")
        os.remove(f)
        return "data\\ 可读写"
    chk("data 目录可写", _w,
        "放在 C:\\Program Files 之类需要管理员权限的目录会写不进去，换到 D 盘或桌面")

    # 收尾
    if "_win" in globals():
        try:
            globals()["_win"].close()
        except Exception:
            pass

    print("")
    print("=" * 62)
    if _bad:
        print("  结论：%d 项通过，%d 项失败" % (_ok, len(_bad)))
        for name, err in _bad:
            print("    - %s：%s" % (name, err))
        print("")
        print("  把这些报错截图发回来。启动日志在 data\\startup.log。")
        print("=" * 62)
        return 1
    print("  结论：全部 %d 项通过，双击「启动电路板测试系统.bat」即可使用。" % _ok)
    print("=" * 62)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("")
        print("自检脚本本身出错了：")
        traceback.print_exc()
        sys.exit(2)
'''

SELFTEST_BAT = r"""@echo off
title 电路板测试系统 · 环境自检
cd /d "%~dp0"

rem GBK encoded, no chcp (see the note at the top of build_release.py).

if not exist "%~dp0runtime\python\python.exe" (
    echo.
    echo [错误] 找不到 runtime\python\python.exe
    echo        这个文件夹没复制完整，请重新复制整个文件夹。
    echo.
    pause
    exit /b 1
)

set "QT_PLUGIN_PATH=%~dp0runtime\python\Lib\site-packages\PyQt5\Qt5\plugins"

"%~dp0runtime\python\python.exe" "%~dp0selftest.py"
echo.
pause
"""


def write_bytes(path, data):
    with open(path, "wb") as f:
        f.write(data)


def write_crlf(path, text, enc):
    """统一 CRLF 换行后按指定编码落盘。

    bat 用 GBK：中文 Windows 控制台默认 936，写 UTF-8 会被 cmd 按 936 拆字节，
    长中文注释能把一行拆成两条命令，直接报 "xxx was unexpected at this time"。
    txt 用 utf-8-sig：记事本、写字板、VS Code 都认，不会乱码。
    """
    data = text.replace("\r\n", "\n").replace("\n", "\r\n").encode(enc)
    write_bytes(path, data)


def copy_tree(src, dst):
    shutil.copytree(src, dst, ignore=_ignore)


def build(out_dir, name):
    if os.path.isdir(out_dir):
        # 不直接删：里面有上万个文件，误删一次代价太大，改个名留着，
        # 确认新包能跑再手工删。同盘改名是瞬时的，不复制。
        old = out_dir.rstrip("\\/") + ".old-" + time.strftime("%m%d-%H%M%S")
        os.rename(out_dir, old)
        print("旧目录改名为：%s（确认新包没问题后可删）" % os.path.basename(old))
    os.makedirs(out_dir)

    t0 = time.time()
    for f in FILES:
        src = os.path.join(SRC, f)
        if not os.path.isfile(src):
            print("跳过（不存在）：%s" % f)
            continue
        shutil.copy2(src, os.path.join(out_dir, f))
        print("文件  %s" % f)

    for d in DIRS:
        src = os.path.join(SRC, d)
        if not os.path.isdir(src):
            print("跳过（不存在）：%s\\" % d)
            continue
        print("目录  %s\\ ..." % d)
        copy_tree(src, os.path.join(out_dir, d))

    write_crlf(os.path.join(out_dir, "启动电路板测试系统.bat"), LAUNCH_BAT, "gbk")
    write_crlf(os.path.join(out_dir, "诊断启动.bat"), DIAG_BAT, "gbk")
    write_crlf(os.path.join(out_dir, "环境自检.bat"), SELFTEST_BAT, "gbk")
    write_crlf(os.path.join(out_dir, "selftest.py"), SELFTEST_PY, "utf-8")
    write_crlf(os.path.join(out_dir, "使用说明.txt"),
               USAGE_TXT % {"name": name,
                            "date": time.strftime("%Y-%m-%d %H:%M")},
               "utf-8-sig")

    # data：只带知识库（资料 + 片段索引 + 原件），不带本机配置和搜索历史。
    # 知识库少了 sources.json / chunks.json / files 任何一样都会变成空库，
    # 所以这里不能像以前那样把 data\ 整个清空。
    data_dir = os.path.join(out_dir, "data")
    if os.path.isdir(data_dir):
        shutil.rmtree(data_dir)
    os.makedirs(data_dir)

    kb_src = os.path.join(SRC, "data", "kb")
    if os.path.isdir(kb_src):
        print("目录  data\\kb\\ ...（知识库资料与索引）")
        shutil.copytree(
            kb_src, os.path.join(data_dir, "kb"),
            ignore=lambda d, names: set(
                n for n in names
                if n in ("history.json", "__pycache__")
                or n.endswith((".pyc", ".pyo"))))
    else:
        print("跳过（没有知识库数据）：data\\kb\\")

    write_crlf(os.path.join(data_dir, "说明.txt"),
               "这个目录放配置、日志和知识库：\r\n"
               "  devices.json   设备参数 + 项目 + 程序（首次保存时自动生成，删掉等于恢复出厂）\r\n"
               "  startup.log    启动记录，起不来时看这个\r\n"
               "  settings.json  安全策略 / 备份策略（系统管理 → 系统设置里改）\r\n"
               "  pin_kinds.json 自定义引脚类别（视觉定位 → 类别管理里新增的；只存新增的）\r\n"
               "  auth\\          账户与角色权限：users.json 账号（只存密码派生值）、roles.json 权限矩阵\r\n"
               "                 删掉等于清空所有账号，下次打开要重新注册\r\n"
               "  audit\\         审计日志，按月一个文件，只增不改；恢复备份也不覆盖它\r\n"
               "  kb\\            知识库：sources.json 资料清单、chunks.json 片段索引、files\\ 资料原件\r\n"
               "                 三样缺一样知识库就是空的；要换资料请在软件里增删，别手工删索引\r\n",
               "utf-8-sig")

    size = _du(out_dir)
    print("\n完成：%s" % out_dir)
    print("体积：%.0f MB　用时 %.1f 秒" % (size / 1048576.0, time.time() - t0))
    return out_dir


def _du(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def make_zip(out_dir, zip_path):
    print("\n压缩到：%s" % zip_path)
    if os.path.exists(zip_path):
        os.remove(zip_path)
    base = os.path.dirname(out_dir)
    t0 = time.time()
    done = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                         allowZip64=True, compresslevel=6) as z:
        for root, _dirs, files in os.walk(out_dir):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, base).replace(os.sep, "/")
                z.write(full, rel)
                done += 1
                if done % 500 == 0:
                    print("  %d 个文件 ..." % done, flush=True)
    print("压缩完成：%.0f MB，用时 %.0f 秒"
          % (os.path.getsize(zip_path) / 1048576.0, time.time() - t0))
    return zip_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="v0.3", help="版本后缀，默认 v0.3")
    ap.add_argument("--zip", action="store_true", help="顺便打一个 zip")
    a = ap.parse_args()

    name = "电路板测试系统_%s" % a.name
    out_dir = os.path.join(OUT_ROOT, name)
    if not os.path.isdir(OUT_ROOT):
        os.makedirs(OUT_ROOT)

    build(out_dir, a.name)
    if a.zip:
        make_zip(out_dir, out_dir + ".zip")
    print("\n下一步：把「%s」整个文件夹（或 zip）发给对方，"
          "对方双击里面的「启动电路板测试系统.bat」即可。" % name)


if __name__ == "__main__":
    sys.exit(main())
