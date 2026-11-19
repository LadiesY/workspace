# -*- coding: utf-8 -*-
"""功能模块清单。

顺序即左侧导航顺序，由用户指定。idx 只用于界面编号，不参与逻辑。
key 是给以后接功能用的稳定标识，改名字不要改 key。

「运动控制」不在这里：它已经并进「设备管理」，作为该模块页内的第二个子页签
（见 pages/device_module.py）。这里只放左侧导航真正要出现的一项一格。
"""

MODULES = [
    {
        "key": "home",
        "idx": "01",
        "ready": True,
        "name": "首页",
        "icon": "home",
        "desc": "自动测试全景与效益总览",
    },
    {
        "key": "device",
        "idx": "02",
        "ready": True,
        "name": "设备管理",
        "icon": "device",
        "desc": "控制器、相机、探针座等外设的连接与状态",
    },
    {
        "key": "test",
        "idx": "03",
        "ready": True,
        "name": "测试管理",
        "icon": "test",
        "desc": "测试项目管理、测试程序与像素坐标换算",
    },
    {
        "key": "auto",
        "idx": "04",
        "ready": True,
        "name": "自动测试",
        "icon": "auto",
        "desc": "按流程编排并自动执行整板测试",
    },
    {
        "key": "vision",
        "idx": "05",
        "ready": True,
        "name": "视觉定位",
        "icon": "vision",
        "desc": "相机取图、引脚识别与像素坐标提取",
    },
    {
        "key": "data",
        "idx": "06",
        "ready": True,
        "name": "数据管理",
        "icon": "data",
        "desc": "采集信号的波形对比、结果查询、统计与导出",
    },
    {
        "key": "kb",
        "idx": "07",
        "ready": True,
        "name": "知识库",
        "icon": "kb",
        "desc": "工艺参数、故障案例与操作手册",
    },
    {
        "key": "system",
        "idx": "08",
        "ready": True,
        "name": "系统管理",
        "icon": "system",
        "desc": "账户、角色权限、审计、备份与系统设置",
    },
]


def by_index(i):
    if 0 <= i < len(MODULES):
        return MODULES[i]
    return MODULES[0]
