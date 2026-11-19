# -*- coding: utf-8 -*-
"""GrayBoost 备用引脚识别 —— 低质量 / 单芯片特写图的兜底通道
=============================================================

算法本体在实验目录 `gray_pin/gray_boost.py`（class A 栅格 / class B 本体定位 /
class C 四边 QFP，自动路由）。本模块只做三件事：

  1. 惰性导入 `gray_boost`，任何异常都吞掉并返回「不可用」，绝不影响主流程；
  2. 把「整图 + 芯片框」转成 GrayBoost 需要的「裁剪图」，再把结果坐标平移回整图；
  3. 输出与 `pin_detect.detect()` **完全同构**的 (pins, counts, info)，让上层
     （core/pins.py、UI）无感替换。

为什么要单独一个通道
--------------------
在 OTF 整板图上，生产算法 pin_detect 已经很好；但在**小分辨率 / 模糊 / 单芯片
特写**这类输入上它会成片地过检（例：8 脚的片子报 15~34 个点）。GrayBoost 是
针对这类图重写的，实测在这 10 张问题图上全部追平或反超。

但反过来，把 GrayBoost 硬套到 OTF 的小芯片框上会**大幅退化**（实测仅 51% 落在
pin_detect 的 ±35% 内，8→16、112→3 都出现过）。所以本通道**默认只在明确判定为
「非整板输入」时才启用**，判据见 core/pins.py 的 GB_POLICY。
"""
import os
import sys

import numpy as np
import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(HERE))          # D:\PCB4\shujuji3
GB_DIR = os.path.join(PROJ, "gray_pin")

SIDES = ("left", "bottom", "right", "top")

# 裁剪时在芯片框外扩的比例：给 GrayBoost 留一点板面上下文（本体定位需要）
MARGIN = 0.10

_GB = None
_ERR = None


def _load():
    """导入 gray_boost；失败缓存异常并返回 None（主流程照常走 pin_detect）。"""
    global _GB, _ERR
    if _GB is not None:
        return _GB
    if _ERR is not None:
        return None
    try:
        if GB_DIR not in sys.path:
            sys.path.insert(0, GB_DIR)
        import gray_boost as G            # noqa: WPS433 (lazy by design)
        _GB = G
    except Exception as e:                # pragma: no cover - env dependent
        _ERR = e
        return None
    return _GB


def available():
    """GrayBoost 是否可用（供 UI 灰显开关用）。"""
    return _load() is not None


def error():
    return repr(_ERR) if _ERR is not None else None


def crop_box(img, box, margin=MARGIN):
    """按芯片框裁剪（外扩 margin），返回 (crop, x0, y0)。"""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = [float(v) for v in box[:4]]
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    mw, mh = margin * (x1 - x0), margin * (y1 - y0)
    a = int(max(0, np.floor(x0 - mw)))
    b = int(min(W, np.ceil(x1 + mw)))
    c = int(max(0, np.floor(y0 - mh)))
    d = int(min(H, np.ceil(y1 + mh)))
    if b - a < 8 or d - c < 8:
        return None, 0, 0
    return img[c:d, a:b], a, c


def coverage(box, shape):
    """芯片框面积 / 整图面积 —— 判「这是单芯片特写还是整板上的一个小芯片」。"""
    H, W = shape[:2]
    x0, y0, x1, y1 = [float(v) for v in box[:4]]
    bw, bh = abs(x1 - x0), abs(y1 - y0)
    return (bw * bh) / float(max(1, W * H))


def detect_quad(img, box, cfg=None, min_edges=4):
    """只试 class C（四边 QFP），用于**板载芯片的逐颗复核**。

    与 detect() 的三点区别（都是实测逼出来的）：

      1. **严格按框裁剪（margin=0）**。板载芯片的 labelme 框已经贴着元件，外扩
         10% 会把邻件的焊盘带进来 —— 实测 64 脚 QFP：tight 得 64，margin=0.10
         直接掉成 blob/3。
      2. **只跑 quad_detect**，不跑 grid/blob。快，而且只认「四条边」这一种指纹。
      3. `min_edges=4` 要求**四条边全部**测到真实点阵（quad_ok 只要求 ≥3）。
         板载复核是"要不要推翻 pin_detect"的决策，条件卡的越死越好。

    返回 (pins, counts, info)（整图坐标系）或 None。
    """
    G = _load()
    if G is None:
        return None
    try:
        crop, ox, oy = crop_box(img, box, margin=0.0)
    except Exception:
        return None
    if crop is None or min(crop.shape[:2]) < 24:
        return None
    try:
        q = G.quad_detect(crop, cfg)
        if not G.quad_ok(q, cfg):
            return None
    except Exception:
        return None
    sides = q.get("sides") or {}
    if len(sides) < int(min_edges):
        return None

    pins = []
    for name in SIDES:
        for p in sides.get(name, {}).get("pins", []):
            pins.append(dict(x=float(p["x"]) + ox, y=float(p["y"]) + oy,
                             side=name))
    if len(pins) < 4:
        return None
    counts = {s: 0 for s in SIDES}
    for p in pins:
        counts[p["side"]] += 1
    ne = {k: len(v["pins"]) for k, v in sides.items()}
    info = dict(mode="quad", pitch=q.get("pitch"), note=q.get("note"),
                gb=True, gb_mode="quad", n_edges=len(sides), n_per_edge=ne,
                cross=0, boosted=False)
    return pins, counts, info


def detect(img, box, cfg=None, whole=False, margin=None):
    """对芯片框跑 GrayBoost。

    返回 (pins, counts, info)（与 pin_detect.detect 同构，坐标为整图坐标系），
    或 None（不可用 / 没找到）。

    whole=True —— 整幅图就是单芯片特写、但框只圈住芯片本体时用：不裁剪，
    直接把整幅图交给 GrayBoost（只喂框内裁剪会把芯片切掉一半，反而更差）。
    margin —— 裁剪外扩比例；None 用默认 MARGIN（0.10）。整板芯片要传 0.0：
    labelme 框已贴着元件，外扩会把邻件焊盘带进来。
    """
    G = _load()
    if G is None:
        return None
    if whole:
        crop, ox, oy = img, 0, 0
    else:
        crop, ox, oy = crop_box(img, box,
                                margin=MARGIN if margin is None else float(margin))
    if crop is None or min(crop.shape[:2]) < 24:
        return None
    try:
        pts, mode, meta = G.detect_auto(crop, cfg)
    except Exception:
        return None
    if not pts:
        return None

    pins = []
    for p in pts:
        side = p.get("side")
        if side not in SIDES:
            continue
        d = dict(x=float(p["x"]) + ox, y=float(p["y"]) + oy, side=side)
        if p.get("pred"):
            d["predicted"] = True
        pins.append(d)
    if not pins:
        return None

    counts = {s: 0 for s in SIDES}
    for p in pins:
        counts[p["side"]] += 1
    info = dict(mode=mode, pitch=meta.get("pitch"), note=meta.get("note"),
                gb=True, gb_mode=mode, cross=0, boosted=False)
    return pins, counts, info


def detect_scaled(img, box, scale=1.0, margin=0.0, cfg=None):
    """裁剪（可选外扩）→ 按 scale 放大 → 跑 detect_auto → 坐标缩回整图。

    为什么需要：板载芯片的 labelme 框只有几十像素（pitch≈5px），而算法里有一批
    **绝对像素**参数（quad_probe=26 / clahe_tile=24 / quad_detrend=121 …）是按
    "芯片占满画面"调的，在这么小的图上前置分支的闸门过不了，直接掉到 blob/grid。
    把裁图放大 2~3 倍能把 pitch 抬进甜点区（实测 V5.(43) T_21~T_26 五路全 0 →
    ×2 后 8 点 4+4 全对）。

    与 detect() 的区别只是"多一次缩放"，其余（坐标平移、返回结构）完全一致，
    方便上层把它当成阶梯里的一档候选。返回 (pins, counts, info) 或 None。
    """
    G = _load()
    if G is None:
        return None
    try:
        crop, ox, oy = crop_box(img, box, margin=margin)
    except Exception:
        return None
    if crop is None or min(crop.shape[:2]) < 24:
        return None
    scale = float(scale or 1.0)
    im = crop
    if scale != 1.0:
        it = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
        im = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=it)
        if min(im.shape[:2]) < 24:
            return None
    try:
        pts, mode, meta = G.detect_auto(im, cfg)
    except Exception:
        return None
    if not pts:
        return None

    pins = []
    for p in pts:
        side = p.get("side")
        if side not in SIDES:
            continue
        d = dict(x=float(p["x"]) / scale + ox,
                 y=float(p["y"]) / scale + oy, side=side)
        if p.get("pred"):
            d["predicted"] = True
        pins.append(d)
    if not pins:
        return None

    counts = {s: 0 for s in SIDES}
    for p in pins:
        counts[p["side"]] += 1
    info = dict(mode=mode, pitch=meta.get("pitch"), note=meta.get("note"),
                gb=True, gb_mode=mode, scale=scale, margin=margin,
                cross=0, boosted=False)
    return pins, counts, info
