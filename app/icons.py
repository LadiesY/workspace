# -*- coding: utf-8 -*-
"""图标：直接把原型 index.html 里的 SVG 图形拿过来渲染。

原型靠 CSS 的 stroke:currentColor 上色，Qt 这边没有 currentColor，
所以在外面包一层 svg 把 stroke 写成具体颜色，再交给 QSvgRenderer 画成位图。
同一个图标同一种颜色的位图只生成一次（_cache）。
"""

from PyQt5.QtCore import QByteArray, Qt
from PyQt5.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer

# 24x24 视图，line 风格，与原型一致
_SHAPES = {
    # 首页：房子
    "home": '<path d="M3.4 10.6L12 3.6l8.6 7"/><path d="M5.7 9.2v10.6h12.6V9.2"/>'
            '<path d="M9.9 19.8v-5.9h4.2v5.9"/>',
    # 设备管理：两台机箱叠放
    "device": '<rect x="3.5" y="4" width="17" height="6" rx="1.4"/>'
              '<rect x="3.5" y="14" width="17" height="6" rx="1.4"/>'
              '<circle cx="7" cy="7" r="1"/><circle cx="7" cy="17" r="1"/>'
              '<path d="M16.5 7h1.5M16.5 17h1.5"/>',
    # 测试管理：夹板 + 表格线
    "test": '<rect x="5" y="4" width="14" height="17" rx="1.5"/>'
            '<path d="M9 4.5h6v3H9z"/><path d="M8.5 11h7M8.5 15h4.5"/>',
    # 自动测试：流程列表 + 执行箭头
    "auto": '<path d="M4 6h9M4 12h6.5M4 18h4.5"/>'
            '<path d="M15 10.2l5.6 4.1-5.6 4.1z"/>',
    # 运动控制：十字坐标 + 箭头
    "motion": '<path d="M12 3v18M3 12h18"/>'
              '<path d="M12 3l-2.2 3M12 3l2.2 3M12 21l-2.2-3M12 21l2.2-3M3 12l3-2.2M3 12l3 2.2'
              'M21 12l-3-2.2M21 12l-3 2.2"/>',
    # 视觉定位：眼睛
    "vision": '<path d="M2.5 12s3.8-6 9.5-6 9.5 6 9.5 6-3.8 6-9.5 6S2.5 12 2.5 12z"/>'
              '<circle cx="12" cy="12" r="2.6"/>',
    # 信号采集：波形
    "signal": '<path d="M2.5 13h3.2l2.4-7 3.4 13 3-9.5 2.3 5.5h4.7"/>',
    # 数据管理：柱状图
    "data": '<path d="M4 20h16"/><path d="M6.5 20V11M10.8 20V6.5M15.2 20V13M19.5 20V8.5"/>',
    # 知识库：翻开的书
    "kb": '<path d="M4 5.5c2.6-1.4 5.4-1.4 8 0 2.6-1.4 5.4-1.4 8 0v13c-2.6-1.4-5.4-1.4-8 0'
          '-2.6-1.4-5.4-1.4-8 0z"/><path d="M12 5.5v13"/>',
    # 系统管理：齿轮
    "system": '<circle cx="12" cy="12" r="3.2"/>'
              '<path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4'
              'M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M18.5 5.5l-1.7 1.7M7.2 16.8L5.5 18.5"/>',
    # ---- 设备管理页用 ----
    # 三轴控制器：机箱 + 三个轴位指示
    "controller": '<rect x="3.5" y="4.5" width="17" height="15" rx="1.6"/>'
                  '<circle cx="8.3" cy="9.6" r="1.5"/><circle cx="12" cy="9.6" r="1.5"/>'
                  '<circle cx="15.7" cy="9.6" r="1.5"/><path d="M7 16h10"/>',
    # 探针座：针杆 + 针尖
    "probe": '<path d="M12 3v8.4"/><rect x="9.2" y="11.4" width="5.6" height="4" rx="1"/>'
             '<path d="M12 15.4v4.2"/><circle cx="12" cy="21" r="0.9"/>',
    # 工业相机：机身 + 镜头
    "camera": '<rect x="2.8" y="7.4" width="13" height="9.2" rx="1.6"/>'
              '<path d="M15.8 11.4l4.4-2.4v6.2l-4.4-2.4z"/><circle cx="8" cy="12" r="2.1"/>',
    # ---- 测试管理页用 ----
    # 测试程序编写：编号步骤列表
    "program": '<circle cx="6" cy="6" r="1.9"/><circle cx="6" cy="12" r="1.9"/>'
               '<circle cx="6" cy="18" r="1.9"/>'
               '<path d="M10.6 6h9.4M10.6 12h7M10.6 18h4.6"/>',
    # 坐标转化：十字准星 + 目标点
    "calib": '<path d="M12 3v3.4M12 17.6V21M3 12h3.4M17.6 12H21"/>'
             '<circle cx="12" cy="12" r="4.1"/><circle cx="12" cy="12" r="0.9"/>',
    # 保存：软盘
    "save": '<path d="M4.2 5.7A1.5 1.5 0 015.7 4.2h9.1L19.8 9v9.3a1.5 1.5 0 01-1.5 1.5H5.7'
            'a1.5 1.5 0 01-1.5-1.5z"/><path d="M8.2 4.2v4.6h6.4V4.2"/>'
            '<rect x="7.6" y="13" width="8.8" height="6.8" rx="0.7"/>',
    # 编辑：铅笔
    "edit": '<path d="M4.2 19.8l.9-3.8L15.4 5.7a2 2 0 012.9 0l.2.2a2 2 0 010 2.9L8.1 19.1z"/>'
            '<path d="M14 7l3 3"/>',
    # 删除：垃圾桶
    "trash": '<path d="M4.6 7h14.8"/><path d="M9.6 7V5.3h4.8V7"/>'
             '<path d="M6.6 7l.7 12a1.6 1.6 0 001.6 1.5h6.2a1.6 1.6 0 001.6-1.5l.7-12"/>'
             '<path d="M10.3 10.6v6M13.7 10.6v6"/>',
    # 新增行：加号
    "plus": '<path d="M12 5.6v12.8M5.6 12h12.8"/>',
    # 删减行：减号
    "minus": '<path d="M5.6 12h12.8"/>',
}

_TMPL = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
         'stroke="{color}" stroke-width="1.5" stroke-linecap="round" '
         'stroke-linejoin="round">{body}</svg>')

# 品牌标：主控板外形 + 引脚，颜色固定为琥珀色
LOGO = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 44 44">'
        '<rect x="12.5" y="12.5" width="19" height="19" rx="3" fill="none" '
        'stroke="#1b57a0" stroke-width="1.5" opacity="0.9"/>'
        '<path d="M17 12.5V6M22 12.5V6M27 12.5V6M17 31.5V38M22 31.5V38M27 31.5V38'
        'M12.5 17H6M12.5 22H6M12.5 27H6M31.5 17H38M31.5 22H38M31.5 27H38" '
        'stroke="#1b57a0" stroke-width="1.1" opacity="0.5"/>'
        '<rect x="17.5" y="17.5" width="9" height="9" rx="1.5" fill="rgba(27,87,160,0.14)" '
        'stroke="#1b57a0" stroke-width="0.9"/>'
        '<circle cx="22" cy="22" r="2.1" fill="#1b57a0"/></svg>')

_cache = {}


def _render(svg, size, dpr=1.0):
    """把 svg 字符串渲染成 QPixmap，size 是逻辑尺寸（px）。"""
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    px = max(1, int(round(size * dpr)))
    img = QImage(px, px, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    renderer.render(p)
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name, color, size=15, dpr=1.0):
    """取一个线条图标。name 见 _SHAPES，color 为 #rrggbb。"""
    key = (name, color, size, round(dpr, 2))
    pm = _cache.get(key)
    if pm is None:
        body = _SHAPES.get(name)
        if body is None:
            raise KeyError("unknown icon: %s" % name)
        pm = _render(_TMPL.format(color=color, body=body), size, dpr)
        _cache[key] = pm
    return pm


def logo(size=40, dpr=1.0):
    key = ("__logo__", size, round(dpr, 2))
    pm = _cache.get(key)
    if pm is None:
        pm = _render(LOGO, size, dpr)
        _cache[key] = pm
    return pm
