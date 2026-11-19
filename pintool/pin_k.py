# -*- coding: utf-8 -*-
"""K 类：**任意朝向、任意每排脚数的双排件**（2026-11-11 接入）。

为什么另立一类，而不是改 T / L
------------------------------
T 的 `PAIR = ("top", "bottom")` 是写死的：只探上下两条边，`along_range()` 恒返回
x 范围，整个坐标系里没有"左右"这个概念（2026-10-26 为了压住"T 被误判成
left/right、多出假排"才这么定的）。所以竖放的件、哪怕标签打的是 T，也必然 0 点。
L 虽然 `PAIRS` 两对边都试，但 `_to3()` 是"把焊盘合并到 3 段"的三点专属逻辑，
硬套任意 n 会把相邻焊盘并错，于是 `_detect_custom` 里 n≠3 时又转回 T —— 又回到
只探上下。

T 和 L 都是实测达标的存量算法，不动它们；K 把"探哪两条边"和"每排几个齿"
两件事都换掉：

  1. **四条边全评，选相对的一对**：上下、左右各解一次，取「两排都可信 + 综合
     质量最高」的那一对。方向由证据决定，不由标签或图像长宽比决定。
  2. **行带枚举**：按每个局部峰各生成一条候选带（各自相对阈值），每条带都做
     一次梳拟合，按「平均齿对比度 ÷ 间距不齐度」评分。T 只取全局最亮那一条，
     一旦本体边缘的横贯亮带比引脚亮，那一排就永远丢了。
  3. **pitch 不靠"阵列铺满"先验**：n 只决定齿数，间距由剖面峰 + 梳拟合搜出来，
     所以 n=3 和 n=14 走同一条路。

深度口径（标定过的，别随手改）
------------------------------
每根引脚在深度方向常是**两段**：外侧窄的引脚条 + 内侧更宽的焊盘块，有时中间还
夹一条暗腰。三种"看起来都对"的口径实测各差 20~30px：

  1. 「沿边平均亮度最大的深度」→ 两段亮度只差 1.07 与 1.10，谁赢靠运气，
     上排落外侧 y=65、下排落内侧 y=416，**同一个封装两排两把尺子**；
  2. 「齿对比度最大的深度」→ 偏好外端（悬空段边缘锐利、齿隙深），每点贴最外；
  3. 「行带内部的中点」→ 行带是给梳拟合用的窄带，只覆盖引脚上半段，深度被夹住。

正解是**深度范围与行带解耦**：用低门限（峰值的 10%）沿齿对比度向两侧扩出整根
引脚，再在这段按沿边平均亮度求重心（实测 y=92.3 / 425.1，与视觉中点吻合）。
横贯亮带的齿对比度≈0 甚至为负，扩到这里自然停住。

逐齿**不再**精化：一旦按亮度逐齿找重心，窗口内的外段亮块会把点拽回去（实测又
退到 y≈69）。引脚行本身是直的、长度一致，整排统一用行级值。

数学部分（梳拟合、亚像素精化、平滑）直接复用 `pin_t` 的纯函数，不复制不改写；
`pin_t.py` 一个字节都不改，本模块只 import 它。
"""

from __future__ import print_function

import cv2
import numpy as np

import pin_t as _T

# --------------------------------------------------------------------------- #
# 参数
# --------------------------------------------------------------------------- #
SIDES = ("top", "right", "bottom", "left")
OPP = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}
PAIRS = (("top", "bottom"), ("left", "right"))   # 只输出相对的一对

BAND_LEVELS = (0.30, 0.45, 0.60, 0.75)   # 每条候选带自己的相对阈值
BAND_MAX_FRAC = 0.45                     # 行带最大厚度（占短边）
BAND_MAX_D = 0.32                        # 行带最远搜索深度（占短边）
TOOTH_W = 0.45
DWIN = 0.40
CV_MAX = 0.15                            # 间距不齐度上限（齿距标准差/均值）
CT_MIN = 0.06                            # 单齿最低对比度（相对白度）

# 评分 q = 平均齿对比度 ÷ 间距不齐度。实测：真引脚 43~68，噪声带 < 10，
# 两簇之间空得很开，所以可以拿 25 当"这条边已经找到引脚了"的门槛，
# 达到就不再试同一条边的其它候选带、也不再试另一对边（纯提速，不改结果）。
Q_STRONG = 25.0
BAND_IOU = 0.45                          # 候选带去重：交并比超过这个算同一条


def _whitish(img):
    """相对白度：亮且不饱和。金属焊盘在 HSV 里是高 V 低 S。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    return hsv[:, :, 2] * (255.0 - hsv[:, :, 1]) / 255.0


class KCtx(object):
    """一颗 K 芯片的识别上下文（只读原图）。

    每侧用 (沿, 深) 描述：`view(side)` 把框内区域重排成「行 = 深度（从该边往内）、
    列 = 沿边方向（阅读顺序）」的矩阵 M。四种朝向共用同一套下游代码，所以在 M 上
    解出来的 (沿, 深) 最后按 side 换回图像坐标即可。
    """

    def __init__(self, img, box):
        self.img = img
        self.h, self.w = img.shape[:2]
        self.x0, self.y0, self.x1, self.y1 = [float(v) for v in box]
        self.W = int(round(self.x1 - self.x0))
        self.H = int(round(self.y1 - self.y0))
        self.short = float(min(self.W, self.H))
        W = _whitish(img)
        sig = max(6.0, 0.09 * self.short)
        # 除以大尺度局部均值 → 抵消光照不均与相机增益；+8 防除零
        self.Wn = W / (cv2.GaussianBlur(W, (0, 0), sig) + 8.0)

    def view(self, side):
        x0 = max(0, int(round(self.x0)))
        y0 = max(0, int(round(self.y0)))
        x1 = min(self.w, int(round(self.x1)))
        y1 = min(self.h, int(round(self.y1)))
        A = self.Wn[y0:y1, x0:x1]
        if side == "top":
            return A
        if side == "bottom":
            return A[::-1, :]
        if side == "left":
            return A.T
        return A[:, ::-1].T


# --------------------------------------------------------------------------- #
# 单侧求解
# --------------------------------------------------------------------------- #
def _band_cands(M, wmax, dcap):
    """按**每个局部峰**各生成一条候选行带（各自相对阈值）。

    T 的做法是取平滑深度剖面的全局最大峰再向两侧扩 —— 只要本体边缘有一条横贯
    的亮带比引脚还亮，行带就锁在亮带上，那一排必然 0 点（实测 T1 的底排）。
    这里每个峰都生成候选，交给下游用齿对比度去裁决谁才是引脚。
    """
    dp = M.mean(1).astype(np.float64)
    if dp.size < 8:
        return []
    dps = _T._smooth(dp.astype(np.float32), 2.0).astype(np.float64)
    pk = float(dps.max())
    bg = float(np.percentile(dps, 15))
    if pk <= bg + 1e-6:
        return []
    out = []
    for i in range(2, min(len(dps) - 2, dcap)):
        if not (dps[i] >= dps[i - 1] and dps[i] >= dps[i + 1]):
            continue
        prom = (dps[i] - bg) / (pk - bg)
        if prom < 0.18:                      # 太弱的峰不生成带（噪声）
            continue
        for lvl in BAND_LEVELS:
            thr = bg + lvl * (dps[i] - bg)
            lo = i
            while lo > 0 and dps[lo - 1] >= thr:
                lo -= 1
            hi = i
            while hi + 1 < len(dps) and dps[hi + 1] >= thr:
                hi += 1
            hi += 1
            if hi - lo < 3:
                continue
            if hi - lo > wmax:               # 太厚：以峰为中心截断
                c = (lo + hi) // 2
                lo = max(0, c - wmax // 2)
                hi = min(len(dps), lo + wmax)
            out.append((int(lo), int(hi)))
    # 去重：两条带交并比够大就算同一条（同一峰不同阈值常常只差 2~5 像素，
    # 重复拟合只是白花时间 —— fit_comb 一次约 50ms，是这里的全部开销）
    uniq = []
    for b in out:
        if all(_iou(b, u) < BAND_IOU for u in uniq):
            uniq.append(b)
    return uniq


def _iou(a, b):
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return float(inter) / max(1.0, union)


def _best_on_side(M, n, margin, wmax, dcap):
    """一条边：所有候选带里挑梳拟合最好的一条。返回 dict 或 None。

    评分 `q = 平均齿对比度 / (1e-6 + 间距不齐度)`：对比度高说明压在金属上，
    不齐度低说明真的是一排等间距引脚。硬门槛先卡两道（CV / 单齿对比度），
    过不了的带直接扔掉 —— 宁可 0 点，也不给"数量对、位置错"的点。

    一旦某条带的 q 达到 Q_STRONG（真引脚实测 43~68，噪声带 < 10），就不再看
    同一条边的其它候选带 —— 只为提速，不改判定。
    """
    best = None
    for (lo, hi) in _band_cands(M, wmax, dcap):
        prof = M[lo:hi].mean(0).astype(np.float32)
        if prof.size < max(10, 2 * n):
            continue
        fit = _T.fit_comb(prof, n, margin_px=margin)
        if fit is None or fit["P"] <= 3:
            continue
        teeth, resid, ct = _T.refine_teeth(prof, fit["c"], fit["P"], n)
        sp = np.diff(teeth)
        cv = float(sp.std() / max(1e-6, sp.mean())) if sp.size else 9.9
        ct_min = float(np.min(ct)) if ct.size else 0.0
        ct_mean = float(np.mean(ct)) if ct.size else 0.0
        if cv > CV_MAX or ct_min < CT_MIN:
            continue
        q = ct_mean / (1e-6 + cv)
        if best is None or q > best["q"]:
            best = dict(side=None, band=(lo, hi), fit=fit, teeth=teeth,
                        cv=cv, ct_min=ct_min, ct_mean=ct_mean, q=q,
                        prof=prof, resid=resid)
        if q >= Q_STRONG:
            break
    return best


def _row_center(M, sol, n):
    """行级深度 = **整根引脚的中点**（齿对比度达标区间的亮度重心）。

    三种错法与正解见模块文档。要点：深度范围和行带**解耦** —— 行带只覆盖引脚
    上半段，拿它当中点最多只能给到 y=75；这里沿整条深度扫齿对比度，用峰值 10%
    的低门限向两侧扩出"整根引脚"，再按沿边平均亮度求重心。
    """
    lo, hi = sol["band"]
    P = sol["fit"]["P"]
    L = M.shape[1]
    H = M.shape[0]
    ia = np.clip(np.round(sol["teeth"]).astype(int), 0, L - 1)
    ib = np.clip(np.round(sol["teeth"] + 0.5 * P).astype(int), 0, L - 1)
    d_lo, d_hi = 1, H - 1
    cts = np.empty(d_hi - d_lo, np.float64)
    for i, d in enumerate(range(d_lo, d_hi)):
        pr = M[d - 1:d + 2].mean(0)
        cts[i] = float(pr[ia].mean() - pr[ib].mean())
    cts = _T._smooth(cts.astype(np.float32), 1.5).astype(np.float64)

    # 峰必须落在行带里，避免挑到完全另一处的偶发响应（例如对面那一排）
    b_lo = max(0, int(lo) - d_lo)
    b_hi = min(len(cts), int(hi) - d_lo)
    if b_hi - b_lo < 2:
        b_lo, b_hi = 0, len(cts)
    k = b_lo + int(np.argmax(cts[b_lo:b_hi]))
    pk = float(cts[k])
    if pk <= 1e-6:                           # 整条带都没有齿：退回带中点
        return 0.5 * (float(lo) + float(hi))

    thr = max(0.10 * pk, 0.03)
    a0 = k
    while a0 > 0 and cts[a0 - 1] >= thr:
        a0 -= 1
    b0 = k
    while b0 + 1 < len(cts) and cts[b0 + 1] >= thr:
        b0 += 1

    seg = M[d_lo + a0:d_lo + b0 + 1].mean(1).astype(np.float64)
    if seg.size < 2:
        return float(d_lo + k)
    seg = seg - seg.min()
    s = float(seg.sum())
    if s <= 1e-9:
        return float(d_lo + 0.5 * (a0 + b0))
    ii = np.arange(d_lo + a0, d_lo + b0 + 1, dtype=np.float64)
    return float((seg * ii).sum() / s)


def _depths(M, sol, n):
    """每根齿的深度：整排统一用行级中点（理由见模块文档"逐齿不再精化"）。"""
    cdep = _row_center(M, sol, n)
    return np.full(n, cdep, np.float64)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def detect_K(img, box, opt=None):
    """识别一颗 K 类芯片。

    `opt["n"]` = 每排引脚数（必填，缺了就返回 0 点，不猜）。
    返回 `(pins, counts, info)`，与 T / F / L 的返回形状一致：
      pins = [{"x", "y", "side", "idx"}, ...]，按阅读顺序编号
      counts = {"top": n, "right": 0, "bottom": n, "left": 0}
      info = {"n", "pair", "detail", "counts", "total", "notes"}；失败时
             `info["failed"]` 有值。

    朝向由证据决定：四条边各解一次，取「两排都可信且 q 较低的那排更高」的对边。
    只有一排可信时**不给点** —— K 是双排件，单排宁可 0 点（与 T/L 同一原则）。
    """
    n = int((opt or {}).get("n") or 0)
    if n < 1:
        return [], {s: 0 for s in SIDES}, dict(failed="n missing")
    ctx = KCtx(img, box)
    margin = 0.02 * max(ctx.W, ctx.H)
    wmax = max(6, int(round(BAND_MAX_FRAC * ctx.short)))
    dcap = max(10, int(round(BAND_MAX_D * ctx.short)))

    per = {}
    pair, pair_q = None, -1.0
    # 按「一对一对」求值：上下先试，两边都够好就不再看左右 —— 板上绝大多数
    # 芯片只朝一个方向，多试一对纯属白花时间（fit_comb 约 50ms/次）。
    for a, b in PAIRS:
        for s in (a, b):
            M = ctx.view(s)
            sol = _best_on_side(M, n, margin, wmax, dcap)
            if sol is not None:
                sol["side"] = s
                sol["M"] = M
            per[s] = sol
        if per[a] is not None and per[b] is not None:
            q = min(per[a]["q"], per[b]["q"])    # 一对里木桶效应：取差的那排
            if q > pair_q:
                pair, pair_q = (a, b), q
            if q >= Q_STRONG:
                break

    if pair is None:
        one = [s for s in SIDES if per.get(s) is not None]
        return [], {s: 0 for s in SIDES}, dict(
            failed="single row only" if len(one) == 1 else "no usable row pair",
            sides={s: round((per.get(s) or {}).get("q") or 0.0, 2) for s in SIDES})

    notes = []
    # 两排同封装必然同间距：差 > 6% 就把弱的锁到强的重解（与 pin_t 同一策略）
    A, B = per[pair[0]], per[pair[1]]
    Pa, Pb = A["fit"]["P"], B["fit"]["P"]
    if abs(Pa - Pb) / max(Pa, Pb) > 0.06:
        strong, weak = (A, B) if A["q"] >= B["q"] else (B, A)
        s2 = weak["side"]
        prof = weak["prof"]
        fit = _T.fit_comb(prof, n, margin_px=margin, pitch_lock=strong["fit"]["P"])
        if fit is not None:
            teeth, resid, ct = _T.refine_teeth(prof, fit["c"], fit["P"], n)
            sp = np.diff(teeth)
            cv = float(sp.std() / max(1e-6, sp.mean())) if sp.size else 9.9
            if cv <= CV_MAX and float(np.min(ct)) >= CT_MIN:
                notes.append("%s 排按 %s 排锁定 P=%.2f 重解" % (s2, strong["side"], fit["P"]))
                weak.update(fit=fit, teeth=teeth, cv=cv, ct_min=float(np.min(ct)),
                            ct_mean=float(np.mean(ct)))

    pins, counts, detail = [], {}, {}
    for s in pair:
        sol = per[s]
        dep = _depths(sol["M"], sol, n)
        rows = []
        for j in range(n):
            a = float(sol["teeth"][j])
            d = float(dep[j])
            if s == "top":
                x, y = ctx.x0 + a, ctx.y0 + d
            elif s == "bottom":
                x, y = ctx.x0 + a, ctx.y1 - 1 - d
            elif s == "left":
                x, y = ctx.x0 + d, ctx.y0 + a
            else:
                x, y = ctx.x1 - 1 - d, ctx.y0 + a
            rows.append((float(np.clip(x, ctx.x0, ctx.x1)),
                         float(np.clip(y, ctx.y0, ctx.y1))))
        # 竖排按 y 排序、横排按 x 排序 —— 编号与"从上到下 / 从左到右"一致
        rows.sort(key=lambda r: (r[1], r[0]) if s in ("left", "right") else (r[0],))
        for j, (x, y) in enumerate(rows, 1):
            pins.append(dict(x=round(x, 2), y=round(y, 2), side=s, idx=j))
        counts[s] = len(rows)
        detail[s] = dict(band=sol["band"], P=round(sol["fit"]["P"], 2),
                         cv=round(sol["cv"], 4), ct=round(sol["ct_mean"], 3),
                         q=round(sol["q"], 2))
    for s in SIDES:
        counts.setdefault(s, 0)
    info = dict(n=n, pair=list(pair), notes=notes, detail=detail,
                counts=counts, total=len(pins))
    return pins, counts, info
