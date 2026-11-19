# -*- coding: utf-8 -*-
"""自定义引脚类别注册表（O/T/F/C/V/L 之外的新类别）

设计约束（2026-11-11 用户明确）
--------------------------------
* 内置 6 类 O/T/F/C/V/L **永远存在、永远不可删除**；
* 新增类别 = 「基础大类 + 每排齿数 n + 名称」。识别时**转调基础类的算法**，
  只把 n 换成自己的值；**不复制代码、不修改原类别的任何参数**。
* 因此新增类别在数学上不可能影响原类别：原类别的 n 仍是 `pin_t.NT` /
  `pin_f.NF` / `pin_o.N_PINS` 这些模块常量，本模块只把 n 塞进 `opt["n"]`
  （T / F / O 的检测函数全链路按 n 走，见各自 `detect_*` 的签名）。

落盘：`JC/data/pin_kinds.json`（原子写：先写 .tmp 再 os.replace）。
**只存自定义类别**，内置的不落盘 —— 免得升级时被旧文件里的内置项覆盖。

名称规则：1~3 个字母，自动大写；不能与内置类别或 `pins._KIND_ALIAS` 里的
历史写法重名（SOP / QFP / OTHER 这些虽然也是字母，但已经被 alias 占用了，
放进来会让 `kind_of` 的解析顺序打架）。
"""

from __future__ import print_function

import json
import os
import re
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
PATH = os.path.join(_ROOT, "data", "pin_kinds.json")

# (名称, 基础算法, 每排齿数, 中文说明)
# n 必须与算法模块里的常量一致：pin_o.N_PINS=7 / pin_t.NT=10 / pin_f.NF=32 /
# pin_l.NT=3；C/V 是两端件，每端 1 个焊盘。
BUILTIN = (
    ("T", "T", 10, "双面两排"),
    ("F", "F", 32, "四边四排"),
    ("O", "O", 7, "单排"),
    ("C", "C", 1, "两端件"),
    ("V", "V", 1, "两端件"),
    ("L", "L", 3, "双面两排"),
)

# 可作为「基础大类」的算法（2026-11-11 加 K）。
# 注意与 BUILTIN 的区别：**K 只是算法，不是内置类别** —— 它没有固定的 n，
# 所以不以类别形式出现在列表里，只能通过【新增类别】选「大类 = K」来用。
# 内置 6 类不可删除、n 固定；K 每建一个类别就带入自己的 n。
BASES = ("T", "F", "O", "C", "V", "L", "K")

# 每个基础大类有几排（决定"共多少点"；K 与 T/L 一样是相对的两排）
_ROWS = {"O": 1, "T": 2, "L": 2, "F": 4, "C": 1, "V": 1, "K": 2}

# 新增类别时的默认说明（用户没填 note 时用）
_BASE_NOTE = {
    "O": "单排",
    "T": "双面两排",
    "F": "四边四排",
    "C": "两端件",
    "V": "两端件",
    "L": "双面两排",
    "K": "任意朝向两排",
}

# 与 pins._KIND_ALIAS 的键保持一致（那份表在 core/pins.py，不 import 以免循环）
RESERVED = frozenset([
    "OTHER", "QFP", "SOP", "CUSTOM",
])

_NAME_RE = re.compile(r"^[A-Za-z]{1,3}$")
_N_MIN, _N_MAX = 1, 256

_LOCK = threading.RLock()
_CACHE = None          # dict(name -> spec)，只含自定义


# --------------------------------------------------------------------------- #
# 读写
# --------------------------------------------------------------------------- #
def _norm_item(it):
    """把 JSON 里的一条记录规整成 spec；非法返回 None（坏数据静默丢弃）。"""
    if not isinstance(it, dict):
        return None
    name = str(it.get("name") or "").strip().upper()
    base = str(it.get("base") or "").strip().upper()
    if not _NAME_RE.match(name) or name in _builtin_names() or name in RESERVED:
        return None
    if base not in BASES:
        return None
    try:
        n = int(it.get("n"))
    except Exception:
        return None
    if not (_N_MIN <= n <= _N_MAX):
        return None
    return dict(name=name, base=base, n=n, builtin=False,
                note=str(it.get("note") or ""))


def _builtin_names():
    return tuple(b[0] for b in BUILTIN)


def _load():
    """读盘（首次调用后缓存）。文件不存在 / 损坏都当成"没有自定义类别"。"""
    global _CACHE
    with _LOCK:
        if _CACHE is not None:
            return _CACHE
        cust = {}
        try:
            with open(PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for it in (raw.get("custom") or []):
                s = _norm_item(it)
                if s:
                    cust[s["name"]] = s
        except Exception:
            pass
        _CACHE = cust
        return _CACHE


def _save(cust):
    """原子写：同目录 .tmp → os.replace。失败只影响持久化，不抛给界面。"""
    try:
        d = os.path.dirname(PATH)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1,
                       "custom": [cust[k] for k in sorted(cust)]},
                      f, ensure_ascii=False, indent=2)
        os.replace(tmp, PATH)
        return True
    except Exception:
        return False


def reload():
    """丢掉缓存重新读盘（测试 / 外部改了文件时用）。"""
    global _CACHE
    with _LOCK:
        _CACHE = None
    return _load()


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def specs():
    """全部类别（内置在前、自定义按名称），每项 dict(name/base/n/builtin/note)。"""
    out = [dict(name=b[0], base=b[1], n=b[2], builtin=True, note=b[3])
           for b in BUILTIN]
    cust = _load()
    for k in sorted(cust):
        out.append(dict(cust[k]))
    return out


def spec_of(label):
    """从任意 label 解析出类别 spec；认不出返回 None。

    宽松口径与 `pins.kind_of` 一致：大小写、`Y_3` / `Y-2` / `Y3` 都能认，
    且**只认自定义类别**（内置的交给 `_is_*_label`，避免抢走它们的路由）。
    """
    if not label:
        return None
    s = str(label).strip().upper()
    if not s:
        return None
    head = s.split("_")[0].split("-")[0].strip()
    stem = head.rstrip("0123456789").strip()
    cust = _load()
    for key in (s, head, stem):
        if key in cust:
            return dict(cust[key])
    return None


def get(name):
    """按名称取 spec（内置 + 自定义）；没有返回 None。"""
    s = str(name or "").strip().upper()
    for b in BUILTIN:
        if b[0] == s:
            return dict(name=b[0], base=b[1], n=b[2], builtin=True, note=b[3])
    return dict(_load().get(s) or {}) or None


def names(all_kinds=False):
    """自定义类别名（all_kinds=True 时连内置一起，顺序同 specs()）。"""
    if all_kinds:
        return [s["name"] for s in specs()]
    return sorted(_load())


def count(name):
    """该类别每排几个齿（内置也返回，C/V 返回 1）。"""
    s = get(name)
    return int(s["n"]) if s else 0


def total_expected(name):
    """该类别预期的总点数：T/L/C/V 是两排、F 四排、O 单排。

    只用于界面提示与"点数对不对"的粗判，识别本身不依赖它。
    """
    s = get(name)
    if not s:
        return 0
    per = _ROWS.get(s["base"], 1)
    return int(s["n"]) * per


def describe(name):
    """一行说明，给按钮副标题 / 下拉项用：如 "双面两排 · 10 脚/排 · 共 20 点"。"""
    s = get(name)
    if not s:
        return str(name)
    if s["base"] in ("C", "V"):
        return "%s · 两端各 1 点" % s["note"]
    return "%s · %d 脚/排 · 共 %d 点" % (s["note"], s["n"], total_expected(name))


# --------------------------------------------------------------------------- #
# 增删
# --------------------------------------------------------------------------- #
def validate(name, base, n):
    """只校验不落地，返回 (ok, msg)。"""
    nm = str(name or "").strip().upper()
    if not nm:
        return False, "类别名称不能为空。"
    if not _NAME_RE.match(nm):
        return False, "类别名称只能是 1~3 个字母（A~Z）。"
    if nm in _builtin_names():
        return False, "「%s」是内置类别，不能重复使用。" % nm
    if nm in RESERVED:
        return False, "「%s」是保留写法（历史别名），换一个名称。" % nm
    if nm in _load():
        return False, "类别「%s」已存在。" % nm
    bs = str(base or "").strip().upper()
    if bs not in BASES:
        return False, "基础大类必须是 %s 之一。" % " / ".join(BASES)
    try:
        nv = int(n)
    except Exception:
        return False, "每排引脚数必须是整数。"
    if not (_N_MIN <= nv <= _N_MAX):
        return False, "每排引脚数需在 %d~%d 之间。" % (_N_MIN, _N_MAX)
    if bs in ("C", "V"):
        # 两端件每端只有 1 个焊盘，n 由算法固定为 1 —— 不因为用户填了别的数就拒绝，
        # 免得在"切到 C 又切回 T"时把输入框里的数弄丢。
        return True, ""
    return True, ""


def add(name, base, n, note=""):
    """新增自定义类别。返回 (ok, msg)。"""
    ok, msg = validate(name, base, n)
    if not ok:
        return False, msg
    nm = str(name).strip().upper()
    bs = str(base).strip().upper()
    nv = 1 if bs in ("C", "V") else int(n)
    cust = dict(_load())
    cust[nm] = dict(name=nm, base=bs, n=nv, builtin=False,
                    note=str(note or "").strip() or _BASE_NOTE.get(bs, bs + " 族"))
    _CACHE_swap(cust)
    if not _save(cust):
        return True, "已新增「%s」（写入配置文件失败，重启后会丢失）。" % nm
    return True, "已新增类别「%s」：按 %s 类算法识别，每排 %d 点，共 %d 点。" % (
        nm, bs, nv, nv * _ROWS.get(bs, 1))


def remove(name):
    """删除自定义类别。内置 6 类**永远不可删除**。返回 (ok, msg)。"""
    nm = str(name or "").strip().upper()
    if nm in _builtin_names():
        return False, "「%s」是内置类别，不能删除。" % nm
    cust = dict(_load())
    if nm not in cust:
        return False, "类别「%s」不存在（或已被删除）。" % nm
    del cust[nm]
    _CACHE_swap(cust)
    if not _save(cust):
        return True, "已删除「%s」（写入配置文件失败，重启后会恢复）。" % nm
    return True, ("已删除类别「%s」。已存 JSON 里标签为 %s 的框会回退到通用算法识别。"
                  % (nm, nm))


def _CACHE_swap(cust):
    global _CACHE
    with _LOCK:
        _CACHE = cust
