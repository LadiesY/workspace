# -*- coding: utf-8 -*-
"""设备参数的读写。

全部落在 data/devices.json，跟着程序目录走 —— 整个文件夹拷到别的机器，
配置也一起过去，不写系统目录、不写注册表。

数值来源（不是编的）：
  * 三轴 IP/端口/速度/加减速/限位 —— KZQ\\config.json（192.168.0.30:8088，
    speed 50、acc/dec 20，限位 X±35 / Y±180 / Z±200 由 limit 35/180/200 拆成正负）
  * 探针 地址/卡号/脉冲当量 —— TZ\\probe\\probe_config.json（192.168.0.200，卡号 1，
    spm 12800 脉冲/mm，v_xy 2.0 mm/s，a_xy 500 mm/s²）
  * 相机 曝光/增益/帧率 —— XJ\\config.json（330000 µs、0 dB、3.03 fps）
"""

import copy
import json
import os

from .base import CONFIG_PATH, DATA_DIR

# 三轴控制器 FMC4030 是 X/Y/Z 三轴
AXES = ("x", "y", "z")

# 探针座的 GAS 卡是六轴：轴号 1..6 依次是 X/Y/Z/U/V/W
# （TZ\probe\probe_config.json 里 axis_u=4 / axis_v=5 / axis_w=6）
PROBE_AXES = ("x", "y", "z", "u", "v", "w")

# 回零方向的口径（卡的 GA_HomeSetPrmSingle 就是这么认的）。
# 界面、日志、配置三处都从这里取字，别各写各的。
HOME_DIR_TEXT = {0: "负向", 1: "正向"}
HOME_MODE_TEXT = {1: "只找 HOME 开关", 2: "HOME + 编码器 Z 相", 3: "只找编码器 Z 相"}

# 三轴（FMC4030）的回零方向和探针座不是一个口径，别混用：
#   三轴：1 = 正限位回零、2 = 负限位回零（SDK 常量，FMC4030-Dll.h）
#   探针：0 = 负向、1 = 正向（GAS 卡口径）
HOME_LIMIT_TEXT = {1: "正限位", 2: "负限位"}

DEFAULTS = {
    "motion": {
        "ip": "192.168.0.30",
        "port": 8088,
        "device_id": 0,
        "jog_step": 1.0,            # 点动步长 mm，手动控制页那个「步长」
        "axes": {
            # 限位单位 mm，是「软件限位」：每次下发运动前比对目标位置，超了就拒绝
            "x": {"limit_pos": 35.0, "limit_neg": -35.0,
                  "speed": 50.0, "acc": 20.0, "dec": 20.0},
            "y": {"limit_pos": 180.0, "limit_neg": -180.0,
                  "speed": 50.0, "acc": 20.0, "dec": 20.0},
            "z": {"limit_pos": 200.0, "limit_neg": -200.0,
                  "speed": 50.0, "acc": 20.0, "dec": 20.0},
        },
        # 三轴回零参数（逐轴，对齐厂家上位机那四项）。卡是**单向**回零：
        # 朝 dir 指定的那一侧走，撞到该侧限位开关就停，再反向退 fall mm 脱离开关，
        # 这一点记为原点。dir 填反不会报任何错，现场表现是「电机一直响、轴顶在
        # 机械尽头」，一直到超时才报失败。
        #   方向：keep 2（负限位）—— 这是本机原工具一直在用的方向。厂家上位机
        #         界面的默认值是 1（正限位），但那是它的出厂默认、不是本机的机械
        #         事实，照抄会把能用的配置改成不能用的。
        #   其余四项按厂家默认：回零速度 50、回零加减速 100、脱落距离 5。
        #   脱落距离不能给 0：轴会停在开关被压住的位置，限位灯一直亮，
        #   状态列就会显示「负限位」而不是「已回零」。
        "home": {
            "axes": {
                "x": {"dir": 2, "speed": 50.0, "acc": 100.0,
                      "fall": 5.0, "timeout": 30.0},
                "y": {"dir": 2, "speed": 50.0, "acc": 100.0,
                      "fall": 5.0, "timeout": 30.0},
                "z": {"dir": 2, "speed": 50.0, "acc": 100.0,
                      "fall": 5.0, "timeout": 30.0},
            },
        },
    },
    "probe": {
        "mode": "eth",              # eth=网口 / serial=串口
        "addr": "192.168.0.200",    # 网口填 PC 端 IP，串口填 COM 口名
        "card_no": 1,
        "spm": 12800.0,             # 脉冲当量 pulse/mm，mm 与脉冲换算用
        "jog_step": 1.0,            # 点动步长 mm
        "require_home": True,       # 没回零就不让绝对定位（原工具也是这么拦的）
        # 回零参数：来自 TZ\probe\probe_config.json 的 home_cfg。
        # **方向是逐轴的，六轴不一样**：原工具里 X 是 0（负向），其余五轴是 1（正向）。
        # 之前这里只有一套 dir=1 给六轴共用，X 就成了朝反方向回零 —— 会一路顶到
        # 机械尽头堵转，直到超时。所以 dirs 必须按轴给。
        # 卡的口径：dir 0=负向、1=正向（写反了就是朝反方向一直走）。
        "home": {
            "mode": 1,              # 1=只找 HOME 开关 2=HOME+Z相 3=只找 Z 相
            "rapid": 2.0,           # mm/s  找开关时的快移速度
            "locate": 1.0,          # mm/s  找到后二次定位速度
            "acc": 500.0,           # mm/s²
            "offset": 0.0,          # mm    原点偏移
            "timeout": 120.0,       # s     单轴回零超时
            "dirs": {"x": 0, "y": 1, "z": 1, "u": 1, "v": 1, "w": 1},
        },
        "axes": {
            # 限位单位 mm（机械坐标），None 表示该方向不限制
            "x": {"limit_pos": None, "limit_neg": None,
                  "speed": 2.0, "acc": 500.0, "dec": 500.0},
            "y": {"limit_pos": None, "limit_neg": None,
                  "speed": 2.0, "acc": 500.0, "dec": 500.0},
            "z": {"limit_pos": None, "limit_neg": None,
                  "speed": 1.0, "acc": 500.0, "dec": 500.0},
            # U/V/W 是第二组平面轴：厂家 SysParam.xml 轴 4/5/6 的 SpdWork=2、
            # AccWork=DeceWork=500，和 TZ 的 v_xy / a_xy 一致；软限位原配置是 null
            "u": {"limit_pos": None, "limit_neg": None,
                  "speed": 2.0, "acc": 500.0, "dec": 500.0},
            "v": {"limit_pos": None, "limit_neg": None,
                  "speed": 2.0, "acc": 500.0, "dec": 500.0},
            "w": {"limit_pos": None, "limit_neg": None,
                  "speed": 2.0, "acc": 500.0, "dec": 500.0},
        },
    },
    "camera": {
        "device_index": 0,
        "exposure": 330000.0,       # µs
        "auto_exposure": False,
        "gain": 0.0,                # dB
        "fps": 3.03,
        "width": 0,                 # 0 = 用相机当前值，连接后自动读回
        "height": 0,
        "offset_x": 0,
        "offset_y": 0,
    },
    # 视觉定位页的设置。算法参数默认值取自「引脚识别工具」的默认口径：
    # YOLO conf 0.25 / iou 0.45（PinTool\\core\\yolo_detect.py 的 run 默认值）
    "vision": {
        "model": "",                # 空 = 用 pintool\weights\best.pt（包里自带的）
        "conf": 0.25,               # YOLO 检出芯片的置信度门槛
        "shot_dir": "",             # 相机拍照存哪，空 = data\captures
        "dot_size": 2,              # 引脚点半径（画在图上的像素）
        "num_size": 2,              # 引脚编号字号
        "id_size": 3,               # 芯片编号字号
        "enhance": True,            # 智能补全：识别完把缺的角点补齐
        "grayboost": True,          # 低分辨率/虚焦时走 gray_pin 兜底
        "save_json": True,          # 存识别结果 JSON
        "save_img": False,          # 存带框标注图（默认不存，一张 8MB）
        "save_pin_json": True,      # 存引脚结果 JSON
        "save_pin_img": False,      # 存引脚标注图
    },
    # 测试管理页。保存地址只放自动测试产生的文件；项目清单（名字 + 保存地址 +
    # 测试程序 + 标定参数）也记在这里，【打开项目】下拉读的就是这份列表。
    # 具体结构见 app/project.py 的 Registry。
    "test": {
        "projects": [],
    },
}

# 界面上每行显示什么、单位是什么。放在这里，界面和校验共用一份，避免两处写不一样。
AXIS_FIELDS = [
    ("limit_pos", "正限位", "mm"),
    ("limit_neg", "负限位", "mm"),
    ("speed", "速度", "mm/s"),
    ("acc", "加速度", "mm/s²"),
    ("dec", "减速度", "mm/s²"),
]


def _merge(base, incoming):
    """按默认值的结构递归合并，老配置缺字段时自动补上，不丢新字段。"""
    out = copy.deepcopy(base)
    if not isinstance(incoming, dict):
        return out
    for k, v in incoming.items():
        if k not in out:
            out[k] = v                      # 未知字段保留，可能是以后加的
        elif isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load():
    """读配置。文件不存在、坏了，一律回默认值，绝不因为配置问题让程序起不来。"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (IOError, OSError, ValueError):
        return copy.deepcopy(DEFAULTS)
    cfg = _merge(DEFAULTS, raw)
    # 老的 probe.home.dir（一套方向给六个轴）已经没有意义，留着只会误导下一个
    # 读配置的人。dirs 合并时已经补好了，这里直接丢掉，下次保存就干净了。
    home = (cfg.get("probe") or {}).get("home")
    if isinstance(home, dict):
        home.pop("dir", None)
    return cfg


def save(cfg):
    """写配置。先写临时文件再替换，写一半断电不会把原配置毁掉。"""
    if not os.path.isdir(DATA_DIR):
        os.makedirs(DATA_DIR)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    if os.path.exists(CONFIG_PATH):
        os.remove(CONFIG_PATH)
    os.rename(tmp, CONFIG_PATH)
