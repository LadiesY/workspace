# -*- coding: utf-8 -*-
"""电路板测试系统 —— 程序入口。

双击「启动电路板测试系统.bat」→ 用包内 Python 跑这个文件。
启动时先往 data/startup.log 写一行环境信息，万一 Qt 起不来，事后能查到
是哪个路径、哪个解释器出的问题。

**路径含中文的坑（发版前踩过）**：PyQt5 这套构建里，Qt 的插件目录是从
Qt5Core.dll 所在位置推出来的，而且中间过了一次 ANSI 转换 —— 只要安装路径里有
汉字，`QLibraryInfo.location(PluginsPath)` 就返回 `D:/PCB/??/????/...`（中文变问号），
平台插件找不到，`QApplication()` 一构造就**卡住**：不报错、不退出、不弹窗，双击后
表现为「什么都没发生」。绕法是自己指定插件目录（Qt 用 UTF-8 读这个环境变量，
中文路径没问题，实测可用），见 _ensure_qt_plugins()。
"""

import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

LOG_PATH = os.path.join(HERE, "data", "startup.log")


def _qt_plugin_dirs():
    """可能的 Qt 插件目录：优先用包内的 PyQt5，其次用当前解释器装的那份。"""
    dirs = [os.path.join(HERE, "runtime", "python", "Lib", "site-packages",
                         "PyQt5", "Qt5", "plugins")]
    try:
        import PyQt5
        dirs.append(os.path.join(os.path.dirname(PyQt5.__file__),
                                 "Qt5", "plugins"))
    except Exception:
        pass
    return [d for d in dirs if os.path.isdir(d)]


def _ensure_qt_plugins():
    """把插件目录写进 QT_PLUGIN_PATH，绕开中文路径下的问号路径。

    必须在创建 QApplication 之前调用（导入 PyQt5 本身没关系）。
    已经有人设过的话，把自己的目录补在最前面，不覆盖原有设置。
    """
    dirs = _qt_plugin_dirs()
    if not dirs:
        return []
    old = os.environ.get("QT_PLUGIN_PATH", "")
    have = [p for p in old.split(os.pathsep) if p]
    add = [d for d in dirs if os.path.normcase(d) not in
           set(os.path.normcase(p) for p in have)]
    if add:
        os.environ["QT_PLUGIN_PATH"] = os.pathsep.join(add + have)
    return dirs


def _log(text):
    try:
        d = os.path.dirname(LOG_PATH)
        if not os.path.isdir(d):
            os.makedirs(d)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
    except Exception:
        pass


def _fatal(title, detail):
    _log("FATAL " + title + "\n" + detail)
    try:
        from PyQt5.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        QMessageBox.critical(None, title, detail)
    except Exception:
        try:
            sys.stderr.write(detail + "\n")
        except Exception:
            pass


def main():
    plugins = _ensure_qt_plugins()      # 必须在 QApplication 之前
    _log("start | exe=%s | python=%s | cwd=%s | qt_plugins=%s"
         % (sys.executable, sys.version.split()[0], os.getcwd(),
            plugins[0] if plugins else "（未找到）"))

    try:
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QApplication

        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

        app = QApplication(sys.argv)
        app.setApplicationName("电路板测试系统")
        app.setApplicationDisplayName("电路板测试系统")

        from app import theme
        theme.init_fonts()
        app.setStyleSheet(theme.qss())
        theme.install_tip_guard(app)        # tooltip 一律白底黑字（见 theme.TipGuard）

        from app.main_window import MainWindow
        win = MainWindow()
        win.show()

        _log("window shown | font=%s / %s / %s"
             % (theme.F_BODY, theme.F_DISP, theme.F_MONO))
        return app.exec_()

    except Exception:
        _fatal("电路板测试系统 · 启动失败", traceback.format_exc())
        return 3


if __name__ == "__main__":
    sys.exit(main())
