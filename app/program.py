# -*- coding: utf-8 -*-
"""测试程序：表格数据模型 + 解析规则。

一个测试程序就是一张表（7 列 n 行），一行 = 一条"读哪类矩形框的哪些引脚、测什么"：

    矩形框 | 识别方式 | 点位范围 | 点名范围 | 点名 | 点位 | 测试类别

    矩形框     芯片标签（内置 O/T/C/V/F/L + 类别管理里新增的），
               对应 pins.json 里 chips[].label
    识别方式   全读取 / 不读取
    点位范围   指定点 / 全部 —— 全部引脚都读，还是只读点名的几个
    点名范围   全部 / 指定 —— 该标签下所有芯片都算，还是只算点名的那个
    点名       芯片实例名（如 L_1），只在"点名范围=指定"时填
    点位       引脚序号，只在"点位范围=指定点"时填，支持 1 / 1-8 / 4,22
    测试类别   V（电压）/ f（频率）

两个「点位」列是唯一容易混的地方：第 3 列是**模式**（全部还是指定），
第 6 列才是**引脚号**。

「表里没出现的标签默认不读取」—— 所以 rules_for() 查不到就直接跳过，
不报错（板子上出现新丝印是常态，不该让整份程序跑不起来）。

本模块只做数据和规则，不碰界面；界面在 app/pages/program_editor.py，
将来自动测试模块调 resolve() 拿点位。
"""

import os
import re

# ---------------------------------------------------------------- 列名
C_LABEL = "矩形框"
C_READ = "识别方式"
C_POINT_MODE = "点位范围"
C_NAME_SCOPE = "点名范围"
C_CHIP_NAME = "点名"
C_PIN_SPEC = "点位"
C_CATEGORY = "测试类别"

COLUMNS = (C_LABEL, C_READ, C_POINT_MODE, C_NAME_SCOPE,
           C_CHIP_NAME, C_PIN_SPEC, C_CATEGORY)

# ---------------------------------------------------------------- 取值
READ_ALL = "全读取"
READ_NONE = "不读取"
READ_MODES = (READ_ALL, READ_NONE)

POINT_SPEC = "指定点"
POINT_ALL = "全部"
POINT_MODES = (POINT_SPEC, POINT_ALL)

SCOPE_ALL = "全部"
SCOPE_ONE = "指定"
NAME_SCOPES = (SCOPE_ALL, SCOPE_ONE)

# 测试类别：V = 电压，f = 频率。目前只有这两种，将来加就在这里补。
CATEGORIES = ("V", "f")
CATEGORY_DESC = {"V": "电压", "f": "频率"}

# 新建一行时的默认值（照示例表的常见写法：全读取 + 指定点 + 点名全部）
ROW_DEFAULTS = {
    C_LABEL: "",
    C_READ: READ_ALL,
    C_POINT_MODE: POINT_SPEC,
    C_NAME_SCOPE: SCOPE_ALL,
    C_CHIP_NAME: "",
    C_PIN_SPEC: "",
    C_CATEGORY: "V",
}

# 内置 6 类的标签。真正的候选值走 label_hints()：它在后面接上
# 「视觉定位 → 类别管理」里新增的自定义类别（增删同步，见那个函数）。
LABEL_HINTS = ("O", "T", "C", "V", "F", "L")

# pintool 目录：pin_kinds.py 在这儿。只 import 那个注册表模块（纯 json/os/re，
# 不牵 cv2/numpy），所以这里不会把视觉算法一起拖进来。
_VENDOR_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pintool")


def custom_labels():
    """类别管理里新增的类别名（不含内置）。取不到就当没有 —— 绝不能因为
    读一个提示用的配置文件失败，让测试管理页打不开。"""
    try:
        import sys
        if _VENDOR_DIR not in sys.path:
            sys.path.insert(0, _VENDOR_DIR)
        import pin_kinds
        return list(pin_kinds.names())
    except Exception:
        return []


def label_hints():
    """「矩形框」列的下拉候选 = 内置 6 类 + 类别管理里新增的类别。

    **每次现算**，不缓存：类别的增删要立刻反映到这里（界面上靠
    ProgramEditor.refresh_labels() 把已有行的候选项刷一遍）。删掉的类别
    这里就不再出现，但单元格里已填的文字不会被清 —— 老程序还要能打开、能存。
    """
    out = list(LABEL_HINTS)
    for name in custom_labels():
        if name and name not in out:
            out.append(name)
    return tuple(out)


def label_desc(label):
    """某个标签的算法说明（下拉项的悬停提示用），如
    "Y　任意朝向两排 · 14 脚/排 · 共 28 点"。认不出就返回空串。"""
    try:
        import sys
        if _VENDOR_DIR not in sys.path:
            sys.path.insert(0, _VENDOR_DIR)
        import pin_kinds
        sp = pin_kinds.get(label)
        if not sp:
            return ""
        return "%s　%s" % (sp.get("name"), pin_kinds.describe(sp.get("name")))
    except Exception:
        return ""


def blank_row():
    return dict(ROW_DEFAULTS)


def text(row, key):
    """取一格的文字，顺手去掉首尾空格；None 当空串。"""
    return str(row.get(key) or "").strip()


# ---------------------------------------------------------------- 解析
def parse_pin_spec(spec):
    """把 '1' / '1-8' / '4,22' 拆成引脚号列表，去重升序。

    逗号支持中英文，区间符号支持 - 和 ~。认不出来的片段直接丢掉 —— 校验那步
    会告诉用户写错了，这里只负责"能拆多少拆多少"。
    """
    spec = (spec or "").strip().replace("，", ",")
    if not spec:
        return []
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*[-~]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            out.extend(range(min(a, b), max(a, b) + 1))
        elif part.isdigit():
            out.append(int(part))
    return sorted(set(out))


def is_read(row):
    """这一行要不要读。只有明确写了"不读取"才跳过。"""
    return text(row, C_READ) != READ_NONE


def validate(rows):
    """保存前校验，返回 [(行号, 说明)]，空列表 = 通过。行号从 1 开始。"""
    errs = []
    for n, row in enumerate(rows, 1):
        if not is_read(row):
            continue
        label = text(row, C_LABEL)
        if not label:
            errs.append((n, "矩形框没填"))
        if text(row, C_READ) not in READ_MODES:
            errs.append((n, "识别方式要选「%s」或「%s」" % READ_MODES))
        mode = text(row, C_POINT_MODE)
        if mode not in POINT_MODES:
            errs.append((n, "点位范围要选「%s」或「%s」" % POINT_MODES))
        elif mode == POINT_SPEC and not parse_pin_spec(text(row, C_PIN_SPEC)):
            errs.append((n, "点位范围是「指定点」，点位那列要写引脚号"))
        scope = text(row, C_NAME_SCOPE)
        if scope not in NAME_SCOPES:
            errs.append((n, "点名范围要选「%s」或「%s」" % NAME_SCOPES))
        elif scope == SCOPE_ONE and not text(row, C_CHIP_NAME):
            errs.append((n, "点名范围是「指定」，点名那列要写芯片名（如 L_1）"))
        cat = text(row, C_CATEGORY)
        if not cat:
            errs.append((n, "测试类别没选"))
        elif cat not in CATEGORIES:
            errs.append((n, "测试类别只有 %s" % " / ".join(CATEGORIES)))
    if not any(is_read(r) for r in rows) and rows:
        errs.append((0, "整份程序没有一行是「全读取」的"))
    return errs


def resolve(chips, rows):
    """按表格把点位挑出来。

    chips 是 pins.json 里的 chips 列表（每项含 label / id / pins[].idx,x,y,side）。
    返回 (points, warns, absent)：

        points  每项 {row, chip, label, idx, x, y, side, category}
        warns   [(行号, 说明)] —— 真出错：点名找不到、引脚号不存在
        absent  [标签] —— 程序里配了、这张图上没有的标签

    图里出现、表里没写的标签**不算任何东西**（默认不读取是正常情况）；
    反过来，表里配了图上却没有的标签也不算错 —— 这块板可能根本没上这类件，
    单独列出来只是方便人扫一眼。
    """
    points = []
    warns = []
    absent = []
    for n, row in enumerate(rows, 1):
        if not is_read(row):
            continue
        label = text(row, C_LABEL)
        if not label:
            continue
        target = [c for c in chips if str(c.get("label") or "") == label]
        if not target:
            if label not in absent:
                absent.append(label)
            continue

        if text(row, C_NAME_SCOPE) == SCOPE_ONE:
            name = text(row, C_CHIP_NAME)
            target = [c for c in target if str(c.get("id") or "") == name]
            if not target:
                warns.append((n, "图里没有点名「%s」" % name))
                continue

        mode = text(row, C_POINT_MODE)
        want = None if mode == POINT_ALL else parse_pin_spec(text(row, C_PIN_SPEC))
        for chip in target:
            pins = {int(p.get("idx", 0)): p for p in chip.get("pins") or []}
            idxs = sorted(pins) if want is None else want
            missing = [i for i in idxs if i not in pins]
            if missing:
                warns.append((n, "%s 没有 %s 号引脚"
                              % (chip.get("id"), ",".join(str(i) for i in missing))))
            for i in idxs:
                p = pins.get(i)
                if p is None:
                    continue
                points.append({
                    "row": n,
                    "chip": str(chip.get("id") or ""),
                    "label": label,
                    "idx": i,
                    "x": float(p.get("x", 0.0)),
                    "y": float(p.get("y", 0.0)),
                    "side": str(p.get("side") or ""),
                    "category": text(row, C_CATEGORY),
                })
    return points, warns, absent


def _box_xyxy(box):
    """把框统一成 (x0,y0,x1,y1)。兼容 [[x0,y0],[x1,y1]] 与 [x0,y0,x1,y1]。"""
    if not box:
        return None
    try:
        first = box[0]
        if hasattr(first, "__len__") and not isinstance(first, str):
            (ax, ay), (bx, by) = box[0], box[1]
        else:
            ax, ay, bx, by = box[:4]
        ax, ay, bx, by = float(ax), float(ay), float(bx), float(by)
    except Exception:
        return None
    return min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)


def snake_rank(chips):
    """按**最终图片的几何位置**给每个框算一个 S 型序位（0 起），返回 {框名: 序位}。

    为什么顺序要按几何、不按 json 数组：自动运行是一颗一颗走下去的，顺序直接
    就是空跑行程。json 数组是 YOLO 这一帧的检出先后，同一块板两次都不一样；
    按程序表里的类别先后走则会在类别之间来回大跳。用 S 型扫一遍最省。

    为什么"分带"要用 y 区间重叠、不能用固定行高：同一块板上框高能差十倍
    （实测 F 242px / V 20px / T 128px）。拿中位数×0.75 当行高，中心 y 差
    150px 的三颗明明压在一条水平带上，会被拆成三行，蛇形就退化成来回折返。

    带内按中心 x 排序；第 0 带左→右、第 1 带右→左……反着来，带与带之间不用
    空跑回最左。没有矩形框的芯片排在最后（保持原先后）。
    """
    rows = []
    for i, c in enumerate(chips or []):
        bb = _box_xyxy(c.get("box"))
        if bb is None:
            continue
        rows.append((i, bb[0], bb[1], bb[2], bb[3]))
    if not rows:
        return {}

    rows.sort(key=lambda t: t[2])              # 按上边升序，逐条归带
    bands = []
    cur, hi = [rows[0]], rows[0][4]
    for t in rows[1:]:
        if t[2] <= hi:                         # 与当前带纵向有重叠 -> 同一条带
            cur.append(t)
            hi = max(hi, t[4])
        else:
            bands.append(cur)
            cur, hi = [t], t[4]
    bands.append(cur)

    rank = {}
    n = 0
    for b, band in enumerate(bands):
        band.sort(key=lambda t: (t[1] + t[3]) / 2.0, reverse=(b % 2 == 1))
        for t in band:
            cid = str(chips[t[0]].get("id") or "")
            if cid not in rank:
                rank[cid] = n
                n += 1
    for c in chips or []:                      # 没框的排最后，别让它们插到中间
        cid = str(c.get("id") or "")
        if cid not in rank:
            rank[cid] = n
            n += 1
    return rank


# ---------------------------------------------------------------- 程序库
# 程序是**全局**的：存在 data\devices.json 的 test.programs 里，不挂在某个项目下。
# 在哪个项目里写好的程序，换到别的项目也能在下拉里选到 —— 一块板子的程序不该因为
# 换了个项目就消失。所以【测试程序名称】下拉看到的是全部程序。
#
# 项目自己只记一句"我上次选的是哪一份"（Project.active），打开项目时按它读回来。
#
# 结构：
#   "test": {
#       "projects": [{"name": "主板A测试", "active": "主板A程序", ...}],
#       "programs": [{"name": "主板A程序", "created": "...", "rows": [{...7 列...}]}]
#   }
#
# 老版本把程序存在 project.program["programs"] 里，open_library() 会搬过来。

LIB_KEY = "programs"


def load_programs(lib):
    """程序库列表（原样返回，改它等于改配置）。"""
    return lib if isinstance(lib, list) else []


def program_names(lib):
    return [str(p.get("name") or "") for p in load_programs(lib)]


def find_program(lib, name):
    for p in load_programs(lib):
        if str(p.get("name") or "") == name:
            return p
    return None


def _unique_name(lib, name):
    """重名时加后缀：主板A程序 → 主板A程序 (2)。迁移老数据时用。"""
    base = name
    n = 2
    while find_program(lib, name) is not None:
        name = "%s (%d)" % (base, n)
        n += 1
    return name


def put_program(lib, name, rows, old_name=None, created=""):
    """存入 / 更新一份程序。old_name 给了就先按旧名摘掉（改名用）。"""
    name = (name or "").strip()
    items = load_programs(lib)
    if old_name and old_name != name:
        items[:] = [p for p in items if str(p.get("name") or "") != old_name]
    cur = find_program(lib, name)
    data = [dict(r) for r in rows]
    if cur is not None:
        cur["rows"] = data
        if created and not cur.get("created"):
            cur["created"] = created
        return cur
    item = {"name": name, "created": created or "", "rows": data}
    items.append(item)
    return item


def delete_program(lib, name):
    items = load_programs(lib)
    before = len(items)
    items[:] = [p for p in items if str(p.get("name") or "") != name]
    return before != len(items)


def open_library(cfg, projects=()):
    """拿到全局程序库，顺手把老数据（挂在项目里的程序）搬进来。

    返回 (items, moved)：moved > 0 表示这次真搬了东西，调用方可以顺手落盘。
    搬过的就从项目里摘掉，所以重复调用是幂等的，不会越搬越多。
    """
    sec = cfg.get("test")
    if not isinstance(sec, dict):
        sec = cfg["test"] = {}
    items = sec.get(LIB_KEY)
    if not isinstance(items, list):
        items = sec[LIB_KEY] = []

    moved = 0
    for proj in projects:
        box = getattr(proj, "program", None)
        if not isinstance(box, dict):
            continue
        old = box.get("programs")
        if not isinstance(old, list):
            continue
        first = ""
        for g in old:
            if not isinstance(g, dict):
                continue
            name = str(g.get("name") or "").strip()
            if not name:
                continue
            name = _unique_name(items, name)     # 两个项目里同名就各自留一份
            items.append({"name": name,
                          "created": str(g.get("created") or ""),
                          "rows": [dict(r) for r in (g.get("rows") or [])
                                   if isinstance(r, dict)]})
            first = first or name
            moved += 1
        # 老版本里"某项目有这个程序"就等于"这个项目在用它"，顺手记成上次选的，
        # 升级后一打开项目就能看到自己的程序，不用重新选一遍。
        if first and not getattr(proj, "active", ""):
            proj.active = first
        box.pop("programs", None)
        if not box:
            proj.program = {}
    return items, moved
