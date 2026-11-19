# -*- coding: utf-8 -*-
"""L 类（两排 × 每排 3 脚）算法 —— 工具版（2026-10-26 从 pin_dev_L/pin_l.py 切片）。

与 T 类（每排 10 脚）的关系
---------------------------
物理上是同一族「两侧引脚封装」，但用户明确 L 是**另一种封装**：两排、每排恰好 3 个。
所以这里不复用 `pin_t.detect_T`（它把 10 当硬先验、把上下当硬先验），另写一套。

与沙盒版的差别：只去掉出图（`render`）与命令行（`__main__`），**算法逐行一致**。
入口 `detect_L(img, box, opt=None)` 返回 (pins, counts, info)。"""
from __future__ import annotations

import os
import cv2
import numpy as np

NT = 3                                     # 每排引脚数（硬先验）
PAIRS = (("top", "bottom"), ("left", "right"))

# 侧边剖面：深度带宽度候选（占该方向长度的比例）。多尺度是必要的 ——
# 带太宽会把本体亮边（4/6/8 的上沿是整条亮金边）算进去，一行 255 就把焊盘吞了；
# 带太窄在左右排布件上又够不到焊盘（2 号左列焊盘离框边 0.19W）。
BANDS = (0.07, 0.09, 0.11, 0.13, 0.16, 0.19, 0.22, 0.26, 0.30, 0.34)
THRS = (0.50, 0.42, 0.58, 0.35, 0.65, 0.28)   # 半高阈值的多个候选

PITCH_N = (2.40, 3.85)      # span/pitch 允许范围（实测 2.82~3.35；框再缩放 ±10% 后 2.55~3.7）
MAX_RESID = 0.18            # 三齿等距残差 |p1-p2|/p 上限
MERGE_FRAC = 0.0            # 占位：合并靠「最小间隙优先」，见 _to3()
NOISE_MIN = 6.0             # 对比度低于此值直接判无阵列


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _gray(img):
    if img is None:
        raise ValueError("img is None")
    if img.ndim == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return img.astype(np.float32)


def _rect(box):
    x0, y0, x1, y1 = [float(v) for v in box]
    return (int(np.floor(min(x0, x1))), int(np.floor(min(y0, y1))),
            int(np.ceil(max(x0, x1))), int(np.ceil(max(y0, y1))))


def _runs(mask):
    """布尔数组 → 连续 True 段 [[a,b], ...]（含端点）。"""
    out = []
    i, n = 0, mask.size
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            out.append([i, j])
            i = j + 1
        else:
            i += 1
    return out


def _to3(runs):
    """把段数合并到 3 段：每次合并**间隙最小**的一对（双瓣焊盘就是这样并回一体的）。

    不用「间隙 < k·span 就合并」的固定阈值 —— 实测同一颗芯片上「同一个焊盘的双瓣」
    间隙只有 0.02~0.06 span，而「相邻两焊盘」的间隙可小到 0.10 span，固定阈值
    必然二选一选错一边。最小间隙优先自动区分：双瓣排在任何两个焊盘之前。
    """
    rs = [list(x) for x in runs]
    while len(rs) > 3:
        gaps = [rs[i + 1][0] - rs[i][1] for i in range(len(rs) - 1)]
        i = int(np.argmin(gaps))
        rs[i][1] = rs[i + 1][1]
        del rs[i + 1]
    return rs


def _cross_mid(prof, a, b, lvl):
    """段 [a,b] 在电平 lvl 上的**左右跨点中点**（亚像素），比亮度重心稳。

    为什么不取亮度重心：焊盘两端亮斑的亮度并不相等（4 号上排右斑明显更亮更大），
    重心会被拉向亮的那一端，实测偏 8~18 px（0.1~0.2 pitch）。用户看到的"焊盘中心"
    是那块金属的中线，所以用电平跨点中点 —— 与 `pin_f` 的 25% 电平口径一致。
    """
    seg = prof[a:b + 1].astype(np.float64)
    if seg.size < 2:
        return (a + b) / 2.0
    m = seg >= lvl
    if not m.any():
        return (a + b) / 2.0
    i0 = int(np.argmax(m))
    i1 = int(len(m) - 1 - np.argmax(m[::-1]))
    # 左侧：i0-1 → i0 之间的插值
    xa = float(a + i0)
    if i0 > 0 and seg[i0] > seg[i0 - 1]:
        t = (lvl - seg[i0 - 1]) / max(1e-6, seg[i0] - seg[i0 - 1])
        xa = a + i0 - 1 + min(max(t, 0.0), 1.0)
    xb = float(a + i1)
    if i1 < seg.size - 1 and seg[i1] > seg[i1 + 1]:
        t = (seg[i1] - lvl) / max(1e-6, seg[i1] - seg[i1 + 1])
        xb = a + i1 + min(max(t, 0.0), 1.0)
    return 0.5 * (xa + xb)


def _edge_prof(g, r, side, band):
    """沿某条边取一维剖面（该带内 **max** 投影）。返回 (prof, origin, span)。

    max 投影是为了弱排 —— 弱排焊盘只比基板亮一点点，均值会把它抹平。
      代价：亮排会饱和（255 平台），但这只影响"峰形"，不影响"位置"，
            位置由后面的段重心 + 2D 重心两步定，不依赖峰的尖锐程度。
    """
    x0, y0, x1, y1 = r
    W, H = x1 - x0, y1 - y0
    if side in ("top", "bottom"):
        h = max(2, int(round(band * H)))
        a, b = (y0, min(y1, y0 + h)) if side == "top" else (max(y0, y1 - h), y1)
        return np.asarray(g[a:b, x0:x1].max(0), np.float32), x0, W
    w = max(2, int(round(band * W)))
    a, b = (x0, min(x1, x0 + w)) if side == "left" else (max(x0, x1 - w), x1)
    return np.asarray(g[y0:y1, a:b].max(1), np.float32), y0, H


def _contrast(prof, cs, p):
    """阵列对比度：焊盘窗均值 − 间隙窗均值。cs 为剖面内坐标。"""
    n = prof.size
    tw = max(1.0, 0.30 * p)
    pad, gap = [], []
    for c in cs:
        a, b = int(round(c - tw)), int(round(c + tw))
        a, b = max(0, a), min(n - 1, b)
        pad.append(float(prof[a:b + 1].mean()))
    for c in cs[:-1]:
        a, b = int(round(c + 0.40 * p)), int(round(c + 0.60 * p))
        a, b = max(0, a), min(n - 1, b)
        gap.append(float(prof[a:b + 1].mean()))
    if not gap:
        return 0.0
    return float(np.mean(pad) - np.mean(gap))


# --------------------------------------------------------------------------- #
# 第一步：单侧「3 焊盘阵列」候选
# --------------------------------------------------------------------------- #
def cand_side(g, r, side):
    """在某条边上找「3 个等距焊盘」。多尺度 × 多阈值，返回最佳候选（可能 None）。"""
    x0, y0, x1, y1 = r
    best = None
    for bf in BANDS:
        prof, org, span = _edge_prof(g, r, side, bf)
        if prof.size < 12:
            continue
        pr = np.convolve(prof, np.ones(3) / 3.0, "same")
        base = float(np.percentile(pr, 10))
        top = float(np.percentile(pr, 97))
        if top - base < NOISE_MIN:
            continue
        pmin, pmax = span / PITCH_N[1], span / PITCH_N[0]
        for tf in THRS:
            lvl = base + tf * (top - base)
            m = pr >= lvl
            rr = _runs(m)
            rr = [q for q in rr if q[1] - q[0] >= max(1, int(0.015 * span))]
            if len(rr) < 3:
                continue
            rr = _to3(rr)
            cs = [_cross_mid(pr, a, b, lvl) for a, b in rr]
            p1, p2 = cs[1] - cs[0], cs[2] - cs[1]
            p = 0.5 * (p1 + p2)
            if not (pmin <= p <= pmax):
                continue
            res = abs(p1 - p2) / max(1e-6, p)
            if res > MAX_RESID:
                continue
            con = _contrast(pr, cs, p)
            if con < NOISE_MIN:
                continue
            # 段宽要彼此接近（3 个同为焊盘，不该有的一大一小）
            ws = [b - a + 1.0 for a, b in rr]
            wsc = 1.0 - min(ws) / max(ws)
            # 等距残差权重给得比较重（1.8）：同封装的 3 只焊盘**必然严格等距**，这是
            # 结构性硬约束；而对比度会被"间隙里恰好有亮杂物"污染（图片1 实测间隙里
            # 有两个 170~220 的亮斑，低阈值会把它们并进焊盘、把中点往里拉 6~9 px）。
            # 用等距把这类被污染的候选压下去，实测能把图片1 从 p=53 修回 p=57.5。
            sc = con * (1.0 - 1.8 * min(res, 0.5)) * (1.0 - 0.30 * wsc)
            cand = dict(sc=sc, con=con, res=res, wsc=wsc, p=p, lvl=lvl,
                        cs=[c + org for c in cs], cs_loc=cs, org=org,
                        span=span, band=bf, thr=tf, side=side,
                        runs=[[a + org, b + org] for a, b in rr])
            if best is None or sc > best["sc"]:
                best = cand
    return best


# --------------------------------------------------------------------------- #
# 第二步：深度（沿垂直于排的方向）扫描
# --------------------------------------------------------------------------- #
def _row_R(g, r, horiz, cs, p, d):
    """在深度 d 处量「焊盘列 − 间隙列」对比度。horiz=True 表示排是水平排（深度=y）。"""
    x0, y0, x1, y1 = r
    tw = max(1.0, 0.25 * p)
    gw = max(1.0, 0.10 * p)
    if horiz:
        if d < y0 or d >= y1:
            return -1e9
        line = g[d, :]
    else:
        if d < x0 or d >= x1:
            return -1e9
        line = g[:, d]
    pad, gap = [], []
    for c in cs:
        a, b = int(round(c - tw)), int(round(c + tw))
        pad.append(float(line[a:b + 1].mean()))
    for c in cs[:-1]:
        a, b = int(round(c + 0.42 * p)), int(round(c + 0.58 * p))
        gap.append(float(line[a:b + 1].mean()))
    if not pad or not gap:
        return -1e9
    return float(np.mean(pad) - np.mean(gap))


def depth_scan(g, r, horiz, cs, p, lo, hi):
    """在 [lo, hi] 内扫深度，返回 (best_d, 曲线)。曲线先用 3 点平滑再取峰。"""
    lo, hi = int(round(lo)), int(round(hi))
    lo = max(lo, r[1] if horiz else r[0])
    hi = min(hi, (r[3] if horiz else r[2]) - 1)
    if hi <= lo:
        return lo, np.zeros(0)
    R = np.array([_row_R(g, r, horiz, cs, p, d) for d in range(lo, hi + 1)])
    Rs = np.convolve(R, np.ones(3) / 3.0, "same") if R.size >= 3 else R
    Rs[:1] = R[:1]
    Rs[-1:] = R[-1:]
    return int(lo + int(np.argmax(Rs))), R


def _row_extent(R, lo, frac=0.45):
    """深度曲线的「峰-半高-连续段」→ (a, b, mid) 亚像素。"""
    if R.size < 3:
        return lo, lo, float(lo)
    k = int(np.argmax(R))
    peak = float(R[k])
    base = float(np.median(R))
    if peak - base < 1e-6:
        return lo + k, lo + k, float(lo + k)
    lvl = base + frac * (peak - base)
    i = k
    while i > 0 and R[i - 1] > lvl:
        i -= 1
    j = k
    while j < R.size - 1 and R[j + 1] > lvl:
        j += 1
    a, b = float(lo + i), float(lo + j)
    # 亚像素：两侧跨点插值
    if i > 0:
        t = (lvl - R[i - 1]) / max(1e-6, R[i] - R[i - 1])
        a = lo + i - 1 + min(max(t, 0.0), 1.0)
    if j < R.size - 1:
        t = (R[j] - lvl) / max(1e-6, R[j] - R[j + 1])
        b = lo + j + min(max(t, 0.0), 1.0)
    return a, b, 0.5 * (a + b)


def _refine_depth(g, r, cx, cy, p, horiz, bh):
    """只沿**深度**方向精修（沿排坐标不动）：返回精修后的深度。

    沿排坐标由电平跨点中点定（对称、不带亮度偏置），这里再让亮度重心去修深度，
    是因为深度方向上焊盘的可见亮区就是焊盘本身（外侧是基板、内侧是本体），
    重心的含义明确；而沿排方向两侧都是同一个焊盘的亮斑，重心才有偏置问题。
    """
    x0, y0, x1, y1 = r
    row_h = max(2.0, 0.30 * p)      # 沿排方向半窗
    dep_h = max(2.0, bh)            # 深度方向半窗
    dep = float(cy if horiz else cx)
    for _ in range(2):
        if horiz:      # 排是水平排 → 深度 = y
            a = max(x0, int(round(cx - row_h))); b = min(x1, int(round(cx + row_h)) + 1)
            c = max(y0, int(round(dep - dep_h))); d = min(y1, int(round(dep + dep_h)) + 1)
        else:          # 排是竖直排 → 深度 = x
            a = max(x0, int(round(dep - dep_h))); b = min(x1, int(round(dep + dep_h)) + 1)
            c = max(y0, int(round(cy - row_h))); d = min(y1, int(round(cy + row_h)) + 1)
        if b - a < 2 or d - c < 2:
            return dep
        win = g[c:d, a:b]
        base = float(np.percentile(win, 20))
        w = np.clip(win - base, 0, None)
        if horiz:
            prof = w.sum(1); pos = np.arange(c, d, dtype=np.float64)
        else:
            prof = w.sum(0); pos = np.arange(a, b, dtype=np.float64)
        s = prof.sum()
        if s <= 1e-6:
            return dep
        nd = float((pos * prof).sum() / s)
        dep = min(max(nd, dep - 0.5 * dep_h), dep + 0.5 * dep_h)
        dep_h *= 0.7
        row_h *= 0.85
    return dep


def _line_pred(xs, ys):
    """过 (xs, ys) 的最小二乘直线，返回与 xs 等长的预测值（行内共线：封装是刚体）。"""
    xs = np.asarray(xs, np.float64)
    ys = np.asarray(ys, np.float64)
    if xs.size < 2:
        return ys
    k = np.polyfit(xs - xs.mean(), ys, 1)
    return np.polyval(k, xs - xs.mean())


def _best_shift(g, r, horiz, cs, p, d, span=0.28):
    """弱排沿排方向的**整体偏移**：在 ±span·p 内找让「焊盘列 minus 间隙列」最大的位移。

    弱排整排压在阴影里，逐点定位会被噪声带跑（4 号实测第一只被拉偏 14 px）；
    但整排是同一块封装、同一栅格，只会因为轻微旋转/透视整体偏一点，所以只估**一个**
    平移量，自由度从"每排一个点"降到"每排一个数"，噪声影响小一个量级。
    """
    x0, y0, x1, y1 = r
    dd = int(round(d))
    if horiz:
        dd = min(max(dd, y0 + 1), y1 - 2)
        line = g[dd - 1:dd + 2, :].max(0)
    else:
        dd = min(max(dd, x0 + 1), x1 - 2)
        line = g[:, dd - 1:dd + 2].max(1)
    n = line.size
    tw = max(1.0, 0.25 * p)

    def con(dx):
        pad, gap = [], []
        for c in cs:
            a, b = int(round(c + dx - tw)), int(round(c + dx + tw))
            if a < 0 or b >= n:
                return -1e9
            pad.append(float(line[a:b + 1].mean()))
        for c in cs[:-1]:
            a, b = int(round(c + dx + 0.40 * p)), int(round(c + dx + 0.60 * p))
            if a < 0 or b >= n:
                return -1e9
            gap.append(float(line[a:b + 1].mean()))
        return float(np.mean(pad) - np.mean(gap)) if gap else -1e9

    grid = np.arange(-span * p, span * p + 1e-9, 0.5)
    vals = np.array([con(float(v)) for v in grid])
    if vals.size == 0 or np.all(vals < -1e8):
        return 0.0
    k = int(np.argmax(vals))
    if 0 < k < vals.size - 1:      # 抛物线亚像素
        y0v, y1v, y2v = vals[k - 1], vals[k], vals[k + 1]
        den = y0v - 2 * y1v + y2v
        if abs(den) > 1e-9:
            return float(grid[k] + 0.5 * (y0v - y2v) / den * 0.5)
    return float(grid[k])


# --------------------------------------------------------------------------- #
# 第三步：每脚 2D 精修
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def detect_L(img, box, opt=None):
    """L 类识别入口：两排 × 每排 3 脚。

    返回 (pins, counts, info)
        pins   [dict(x, y, side, idx)]，坐标 = 输入 img 坐标系
               side ∈ {top,bottom} 或 {left,right}
        counts {"top":n,"right":n,"bottom":n,"left":n}
        info   band/pitch/对比度/轴判定/深度/告警
    """
    o = dict(band_h=0.085, depth_frac=0.45)
    if opt:
        o.update(opt)
    g = _gray(img)
    r = _rect(box)
    x0, y0, x1, y1 = r
    W, H = x1 - x0, y1 - y0
    info = dict(flags=[], cands={}, axis=None, pitch=None, depth={})

    cands = {}
    for s in ("top", "bottom", "left", "right"):
        c = cand_side(g, r, s)
        cands[s] = c
        if c:
            info["cands"][s] = dict(sc=round(c["sc"], 1), con=round(c["con"], 1),
                                    p=round(c["p"], 2), band=c["band"], thr=c["thr"],
                                    cs=[round(v, 1) for v in c["cs"]])
    info["cands_raw"] = cands

    # ---- 判轴：**四条边都算一次"两排"假设，按两排的证据之和投票** --------------
    # 为什么不能"谁的单边分最高就选谁"：框稍微缩放一点，垂直于真轴的那条边上也可能
    # 凑出"3 段等距"（实测框 ±5~10% 就会翻轴）。但**假轴凑不出对面那排** ——
    # 判据取 r1+r2（本侧排对比度 + 对面半区最强排对比度），假轴的 r2 必然很低。
    # 这也正是 L 类的硬不变量：两排必须正交相对，不可能只有一边有排。
    vertical = {"top", "bottom"}
    hyps = []
    for s in ("top", "bottom", "left", "right"):
        c = cands[s]
        if not c:
            continue
        opp = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}[s]
        horiz = s in vertical
        if horiz:
            low, high = (y0, y0 + H // 2), (y0 + H // 2, y1)
        else:
            low, high = (x0, x0 + W // 2), (x0 + W // 2, x1)
        rs, ro = (low, high) if s in ("top", "left") else (high, low)
        cs = c["cs"]
        d1, R1 = depth_scan(g, r, horiz, cs, c["p"], rs[0], rs[1])
        d2, R2 = depth_scan(g, r, horiz, cs, c["p"], ro[0], ro[1])
        if R1.size == 0 or R2.size == 0:
            continue
        r1 = float(R1.max())
        r2 = float(R2.max())
        if r1 < NOISE_MIN:
            continue
        # 两排都要"像一排"：弱排可以暗，但不能没有（否则就是只有单边有排的假阵列）
        if r2 < max(2.0, 0.10 * r1):
            info["flags"].append("假设 %s: 对面那半无排 r2=%.1f（否决）" % (s, r2))
            continue
        # 两排 pitch 应当一致（弱排的候选 pitch 不可靠，只罚不否决）
        pen = 1.0
        if cands[opp]:
            dp = abs(cands[opp]["p"] - c["p"]) / c["p"]
            pen *= (1.0 - 0.35 * min(1.0, dp / 0.25))
        sc = (r1 + r2) * pen
        hyp = dict(side=s, opp=opp, horiz=horiz, cand=c, d1=d1, d2=d2,
                   R1=R1, R2=R2, r1=r1, r2=r2, rs=rs, ro=ro, sc=sc)
        info.setdefault("hyp", {})[s] = dict(sc=round(sc, 1), r1=round(r1, 1),
                                             r2=round(r2, 1), p=round(c["p"], 2))
        hyps.append(hyp)

    # 两级决策：① 用 r1+r2 选**轴**（哪一对边）；② 用单边候选分选**主排**（哪边当基准）。
    # 为什么必须分两级：同一对边的两种写法 r1+r2 几乎相等（就是同两排），谁大谁小纯看噪声；
    # 而主排决定 pitch 与沿排坐标 —— 实测图片1 让暗排当主排会取到 p=66.5（真值≈56）。
    chosen = None
    if hyps:
        top_h = max(hyps, key=lambda h: h["sc"])
        pair = {top_h["side"], top_h["opp"]}
        same = [h for h in hyps if {h["side"], h["opp"]} == pair]
        chosen = max(same, key=lambda h: h["cand"]["sc"])

    if chosen is None:
        info["flags"].append("未找到合法的两排阵列")
        counts = dict(top=0, right=0, bottom=0, left=0)
        return [], counts, info

    s, opp, horiz = chosen["side"], chosen["opp"], chosen["horiz"]
    c = chosen["cand"]
    p = c["p"]
    info["axis"] = (s, opp)
    info["pitch"] = round(float(p), 2)
    info["depth"] = {s: chosen["d1"], opp: chosen["d2"]}
    info["con"] = {s: round(chosen["r1"], 1), opp: round(chosen["r2"], 1)}

    bh = max(2.0, o["band_h"] * (H if horiz else W))
    cs = list(c["cs"])
    dim_frac = o.get("dim_frac", 0.45)

    pins = []
    for side, R, rng in ((s, chosen["R1"], chosen["rs"]),
                         (opp, chosen["R2"], chosen["ro"])):
        Rmax = float(R.max()) if R.size else 0.0
        dim = Rmax < dim_frac * chosen["r1"]        # 这一排是不是"阴影里的那排"
        _, _, dmid = _row_extent(R, rng[0], 0.45)
        dbase = chosen["d1"] if side == s else chosen["d2"]
        if dim:
            # 弱排：沿排位置沿用强排（同封装同栅格），先估一个整体偏移，深度取 R 半高段中点
            shift = _best_shift(g, r, horiz, cs, p, dmid) if o.get("weak_shift", True) else 0.0
            cc = [v + shift for v in cs]
            dep = [dmid] * len(cc)
            info.setdefault("weak", {})[side] = dict(dim=True, R=Rmax, d=dmid,
                                                     shift=round(shift, 2))
        else:
            cc = list(cs)
            dep = []
            for v in cc:
                cx, cy = (v, dbase) if horiz else (dbase, v)
                nd = _refine_depth(g, r, cx, cy, p, horiz, bh)
                dep.append(nd)
            dep = [float(v) for v in _line_pred(cc, dep)]     # 行内共线
            info.setdefault("weak", {})[side] = dict(dim=False, R=Rmax,
                                                     d=round(float(np.mean(dep)), 2))
        for v, dv in zip(cc, dep):
            cx, cy = (v, dv) if horiz else (dv, v)
            pins.append(dict(x=round(cx, 2), y=round(cy, 2), side=side))

    pins = _number(s, opp, horiz, pins)
    counts = dict(top=0, right=0, bottom=0, left=0)
    for q in pins:
        counts[q["side"]] = counts.get(q["side"], 0) + 1
    info["n"] = len(pins)
    if len(pins) != 2 * NT:
        info["flags"].append("点数 %d != %d" % (len(pins), 2 * NT))
    return pins, counts, info


def _number(side_a, side_b, horiz, pins):
    """编号：与 F/T 同一套「从靠近右上角那只起、逆时针绕一圈」。

    水平排（上下）：1..3 = 上排 x 递减（右→左），4..6 = 下排 x 递增（左→右）。
    竖直排（左右）：1..3 = 右列 y 递增（上→下），4..6 = 左列 y 递减（下→上）。
    """
    # 先绕的那一排固定：水平排从上排起、竖直排从右列起（与 F/T 都是逆时针开头一致）
    A_side, B_side = ("top", "bottom") if horiz else ("right", "left")
    A = [q for q in pins if q["side"] == A_side]
    B = [q for q in pins if q["side"] == B_side]
    if horiz:
        A.sort(key=lambda q: -q["x"])      # 上排：右 → 左
        B.sort(key=lambda q: q["x"])       # 下排：左 → 右
    else:
        A.sort(key=lambda q: q["y"])       # 右列：上 → 下
        B.sort(key=lambda q: -q["y"])      # 左列：下 → 上
    out = []
    for i, q in enumerate(A + B):
        q = dict(q)
        q["idx"] = i + 1
        out.append(q)
    return out


def imread_u(path):
    """读带中文/空格的路径（cv2.imread 在 Windows 上对非 ASCII 路径会失败）。"""
    with open(path, "rb") as f:
        buf = np.frombuffer(f.read(), np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)
