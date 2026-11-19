# -*- coding: utf-8 -*-
"""O 类（单排引脚）专用算法。

语义（与 T/F/C/L 同一套思路：精确解 + 硬先验）
--------------------------------------------------
* O = **只有一排**引脚，另一侧绝对没有（对边若也有整排证据 → 判 failed，不给点）。
* 一排齿数**恒为 7**（``N_PINS``，用户 2026-10-27 确认；不是自动数出来的）。
* 一排齿**严格等间距**，阵列铺满该方向的大部分（span/box ≈ 0.63~0.67）。

为什么通用算法（pin_detect 的「两条列」模型）在 O 上不行
----------------------------------------------------------
实测 13 颗：4 颗判错边（把金属体亮面当引脚排），6 颗点数多 1~2。
多出来的点全是**金属体的左右侧棱**——它们是竖直亮线，在「沿排剖面」上也是峰，
但 ① 显著度只有真焊盘的 1/4~1/3；② 位置几乎正好落在栅格的一个齿距外。
所以本模块不数峰，直接**拟合 7 齿等间距梳** `a_k = c + k·P`，假峰自然落不进栅格。

两部分几何
----------
沿排 a（齿位）：7 齿梳拟合，pitch 由「阵列铺满」先验限定；相位消歧靠齿高齐整度惩罚。
深度 z（齿深）：逐齿算「齿列亮度 − 半齿距间隙亮度」的匹配滤波曲线 C[k,d]，
  取离峰 ≤2P 且 ≥0.40·峰的**所有**瓣并起来（该曲线常见焊盘/银脚两瓣）的加权重心，
  再对整排做稳健直线拟合（同一排焊盘物理共线）。不使用「50% 电平段中点」：
  焊盘在深度上是多子段，逐齿各自挑最长段会跳到不同子段（同排离散 8~9px）。
"""
from __future__ import annotations

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

KIND = "O"
# ★ 硬先验：O 类只有一排，且恰好 7 脚（用户 2026-10-27 确认）
N_PINS = 7

# ---- 结构先验（13 颗样本标定）
DEPTH_FRAC = 0.55          # 从该边向内最多看短边的 55% 找焊盘带（0.34 时松框会把焊盘挤出窗口）
# ★ 不是「取最长段」而是「所有段都当候选」：低阈值下焊盘带与金属体会连成一片，
#   真焊盘带往往只在 0.6~0.75 档才单独成为一段。取最长段 = 系统性选错带。
BAND_LEVELS = (0.30, 0.40, 0.50, 0.60, 0.70)
BAND_THICK_P = (0.22, 2.40)   # 焊盘带厚度/齿距 的允许区间（真值 0.7~1.6，连体带 4.7~5.0）
SPAN_FRAC = (0.52, 0.82)   # 一排阵列应占据的宽度比例（×(n-1) 换算 pitch）
ROW_FIT = True             # 整排 z 共线拟合（抹掉个别齿的跳变）
# 深度落点口径：'pin' = 整根引脚中心（焊盘瓣 + 银脚瓣 并集重心）
#               'pad' = 只取靠框边那一瓣（焊盘亮面）
Z_ANCHOR = "pin"
SINGLE_MARGIN = 0.55       # 对边证据 / 本边证据 超过它 → 判「不止一排」


def _whitish(img):
    """相对白度 V·(255-S)/255：金属焊盘高、绿油/丝印低，对曝光不敏感。"""
    if cv2 is None:
        raise RuntimeError("cv2 required")
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    return (hsv[:, :, 2] / 255.0) * (1.0 - hsv[:, :, 1] / 255.0)


def _smooth(v, k):
    k = int(k)
    if k <= 1:
        return np.asarray(v, np.float32)
    ker = np.ones(k, np.float32) / k
    return np.convolve(np.asarray(v, np.float32), ker, mode="same")


def _rectify(W, side, depth):
    """某条边转成 (depth, along)：第 0 行 = 该边外沿，列方向 = 沿排正方向。"""
    d = int(max(4, min(depth, min(W.shape))))
    if side == "top":
        return W[:d, :]
    if side == "bottom":
        return W[-d:, :][::-1, :]
    if side == "left":
        return W[:, :d].T
    return W[:, -d:].T[::-1, :]


def _ac_energy(P2):
    """沿排方向的高频能量：焊盘带每列都在亮-暗-亮里跳；金属面/背景行几乎不变。"""
    r = P2.mean(1, keepdims=True)
    a = P2 - _smooth(P2.mean(1), 3)[:, None]
    lo = np.apply_along_axis(lambda v: _smooth(v, max(3, len(v) // 8)), 1, a)
    return (a - lo).std(1) + 0.0 * r.mean()


def _energy_runs(E, th, min_rows=4):
    """E(d) ≥ th·max 的**所有**连续段（不是只取最长的那个）。"""
    if E.max() <= 1e-6:
        return []
    on = E >= th * E.max()
    out = []
    i, n = 0, len(on)
    while i < n:
        if on[i]:
            j = i
            while j + 1 < n and on[j + 1]:
                j += 1
            if (j - i + 1) >= min_rows:
                out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def _comb_scan(p, n, cs, P, hw_frac=0.20):
    """相位向量化：对一组 c 一次算完梳分（用积分和图，避免逐齿切片）。"""
    L = len(p)
    S = np.concatenate([[0.0], np.cumsum(p.astype(np.float64))])
    cs = np.asarray(cs, np.float64)
    hw = max(1.0, hw_frac * P)
    k = cs[:, None] + (np.arange(n) * P)[None, :]
    i0 = np.clip(np.round(k - hw), 0, L - 1).astype(np.int32)
    i1 = np.clip(np.round(k + hw), 0, L - 1).astype(np.int32)
    teeth = (S[i1 + 1] - S[i0]) / (i1 - i0 + 1.0)
    kg = cs[:, None] + (np.arange(n - 1) * P + 0.5 * P)[None, :]
    g0 = np.clip(np.round(kg - 0.16 * P), 0, L - 1).astype(np.int32)
    g1 = np.clip(np.round(kg + 0.16 * P), 0, L - 1).astype(np.int32)
    gaps = (S[g1 + 1] - S[g0]) / (g1 - g0 + 1.0)
    s = teeth.mean(1) - gaps.mean(1)
    pen = teeth.min(1) / np.maximum(np.median(teeth, axis=1), 1e-6)
    s = s * np.minimum(1.0, pen / 0.55)
    # 越界相位置 -inf
    bad = (cs < -0.2 * P) | (cs + (n - 1) * P > L - 1 + 0.2 * P)
    s = np.where(bad, -9e9, s)
    return s


def _comb(p, n, P_lo, P_hi):
    """n 齿等间距梳：粗扫（P×相位全向量化）+ 局部细化。返回 (score, c, P)。"""
    L = len(p)
    if P_hi <= P_lo:
        return (-9e9, None, None)
    best = (-9e9, None, None)

    def scan(Pstep, Pc, halfP, cs_of):
        nonlocal best
        for P in np.arange(Pc - halfP, Pc + halfP + 1e-9, Pstep):
            if not (P_lo <= P <= P_hi):
                continue
            cs = cs_of(P)
            if len(cs) == 0:
                continue
            sc = _comb_scan(p, n, cs, P)
            i = int(np.argmax(sc))
            if sc[i] > best[0]:
                best = (float(sc[i]), float(cs[i]), float(P))

    # 粗扫：pitch 范围由「阵列铺满」先验限定，相位按可行区间全扫
    Pc = 0.5 * (P_lo + P_hi)
    scan(0.35, Pc, 0.5 * (P_hi - P_lo) + 0.35,
         lambda P: np.arange(-0.2 * P, L - (n - 1) * P + 0.2 * P, 0.5))
    if best[1] is None:
        return best
    # 细化两轮
    for Pstep, cstep, halfP, halfc in ((0.10, 0.6, 0.5, 1.2), (0.02, 0.1, 0.12, 0.25)):
        scan(Pstep, best[2], halfP,
             lambda P, c=best[1], h=halfc, st=cstep: np.arange(c - h, c + h + 1e-9, st))
    return best


def _side_probe(W, side, bw, bh, n=N_PINS):
    """某条边的最佳 (band, profile, comb)。

    ⚠ 四条踩过的坑（都是「静默出错」，不报错）：
    ① 只取「最长的能量段」→ 低阈值下焊盘带与金属体连成一条 → 深度中心落到两者中间；
    ② 用对比度选带 → 偏向焊盘最亮的几行 → 深度中心偏到焊盘上缘（4#1 踩过）；
    ③ 深度窗口只取短边 34% → 框一松焊盘就被挤出窗口，剩下的假带顶上来；
    ④ 厚度不设限 → [0,44] 这种把焊盘+金属体并起来的带也能拿高分。
    做法：5 档阈值 × 每档所有段 → 厚度必须落在 BAND_THICK_P×P → 取梳分最高。
    提速：先用「能量最强的那条带」跑一次全量梳拟合定出 (c,P)，其余候选带只在这个
    种子附近做局部评分（实测各候选带的 P 相差 <0.3%，种子足够准），最后再精修一次。
    """
    short = min(bw, bh)
    depth = max(8, int(round(short * DEPTH_FRAC)))
    P2 = _rectify(W, side, depth)
    if P2.shape[1] < 12 or P2.shape[0] < 6:
        return None
    E = _ac_energy(P2)
    L = P2.shape[1]
    P_lo = max(4.0, SPAN_FRAC[0] * L / float(n))
    P_hi = SPAN_FRAC[1] * L / float(n - 1)

    runs, seen = [], set()
    for lvl in BAND_LEVELS:
        for band in _energy_runs(E, lvl):
            if band in seen:
                continue
            seen.add(band)
            runs.append(band)
    if not runs:
        return None

    def prof(band):
        p0 = _smooth(P2[band[0]:band[1] + 1, :].mean(0), 3)
        lo, hi = np.percentile(p0, 10), np.percentile(p0, 99)
        if hi - lo < 0.04:
            return None
        return np.clip((p0 - lo) / (hi - lo), 0, 1.4), p0, float(hi - lo)

    # --- 种子：能量最强的那条带，跑一次全量梳拟合
    seed = max(runs, key=lambda b: float(E[b[0]:b[1] + 1].mean()) * (1.0 + 0.02 * (b[1] - b[0])))
    pr = prof(seed)
    if pr is None:
        return None
    sc0, c0, P0 = _comb(pr[0], n, P_lo, P_hi)
    if c0 is None:
        return None

    best = None
    for band in runs:
        pr = prof(band)
        if pr is None:
            continue
        q, p0, strength = pr
        thick = band[1] - band[0] + 1
        # 局部评分：相位 ±0.7P 全扫，pitch 只微调 ±1.5%
        cs = np.arange(c0 - 0.7 * P0, c0 + 0.7 * P0 + 1e-9, 0.35)
        bc, bP, bs = c0, P0, -9e9
        for Ps in (P0 * f for f in (0.985, 1.0, 1.015)):
            if not (P_lo <= Ps <= P_hi):
                continue
            scv = _comb_scan(q, n, cs, Ps)
            i = int(np.argmax(scv))
            if scv[i] > bs:
                bs, bc, bP = float(scv[i]), float(cs[i]), float(Ps)
        tp = thick / max(bP, 1e-6)
        if not (BAND_THICK_P[0] <= tp <= BAND_THICK_P[1]):
            continue
        # 轻微偏好「厚度≈1.1P」的带（焊盘近似方形），只做同分时的仲裁
        sc2 = bs * (1.0 - 0.10 * abs(np.log(max(tp, 0.05) / 1.10)))
        Eb = E[band[0]:band[1] + 1]
        core = band
        if Eb.max() > 1e-6:
            pk = float(Eb.max())
            im = int(np.argmax(Eb))
            a = im
            while a > 0 and Eb[a - 1] >= 0.6 * pk:
                a -= 1
            b = im
            while b < len(Eb) - 1 and Eb[b + 1] >= 0.6 * pk:
                b += 1
            core = (band[0] + a, band[0] + b)
        r = dict(side=side, band=band, core=core, p=q, raw=p0, depth=depth, P2=P2,
                 strength=strength, score=bs, score2=sc2, c=bc, P=bP, thick=thick)
        if best is None or sc2 > best["score2"]:
            best = r
    if best is None:
        return None
    # --- 赢家再做一次精修（限 P_lo~P_hi）
    sc, c, P = _comb(best["p"], n, P_lo, P_hi)
    if c is not None and sc >= best["score"] - 1e-9:
        best.update(c=c, P=P, score=sc)
    return best


def _tooth_contrast(P2, xs, P):
    """逐齿的「焊盘−间隙」深度曲线 C[k, d]：齿列亮度 − 半齿距处亮度。

    为什么不用「50% 电平段」：焊盘在深度方向是「亮面 + 暗影」多子段，逐齿各自挑
    最长段会跳到不同子段上（实测同排 z 离散 8~9px）。这条曲线是**匹配滤波**：
    只有「这里有焊盘、旁边是缝」的深度才为正，峰位就是焊盘中心，且不依赖阈值。
    """
    n_d, L = P2.shape
    n = len(xs)
    hw = max(1, int(round(0.20 * P)))
    C = np.zeros((n, n_d), np.float32)
    for k, a in enumerate(xs):
        i0, i1 = int(round(a - hw)), int(round(a + hw))
        if i1 < 0 or i0 > L - 1:
            continue
        i0, i1 = max(0, i0), min(L - 1, i1)
        teeth = P2[:, i0:i1 + 1].mean(1)
        # 间隙取「离得近且没越界」的那一侧（末齿只有左侧）
        g = np.zeros(n_d, np.float32)
        for sgn in (1.0, -1.0):
            b = a + sgn * 0.5 * P
            j0, j1 = int(round(b - hw)), int(round(b + hw))
            if j1 < 0 or j0 > L - 1:
                continue
            j0, j1 = max(0, j0), min(L - 1, j1)
            g = P2[:, j0:j1 + 1].mean(1)
            break
        C[k] = teeth - g
    return C


def _fit_row_z(zs):
    """整排深度稳健直线拟合：z_k ≈ a + b·k。

    同一排焊盘在物理上共线（透视/微旋转只产生线性项），所以用拟合值替换原始值，
    把个别齿的跳变抹掉。返回 (zf, resid_max, n_outlier)。
    """
    zs = np.asarray(zs, float)
    ok = np.isfinite(zs)
    if ok.sum() < 3:
        return zs, 0.0, 0
    idx = np.arange(len(zs), dtype=float)
    b, a = np.polyfit(idx[ok], zs[ok], 1)   # ⚠ polyfit 返回 [斜率, 截距]
    for _ in range(2):
        res = np.abs(zs - (a + b * idx))
        keep = ok & (res <= max(1.5, 2.5 * float(np.median(res[ok]))))
        if keep.sum() < 3 or keep.all():
            break
        b, a = np.polyfit(idx[keep], zs[keep], 1)
    zf = a + b * idx
    res = np.abs(zs - zf)
    nout = int((ok & (res > max(2.0, 0.35 * float(np.median(res[ok] + 1e-6)) + 2.0))).sum())
    return zf, float(np.nanmax(res[ok])) if ok.any() else 0.0, nout


def detect_O(img, box, n=N_PINS):
    """单排引脚识别。返回 (pins, info)。

    pins: [{"x": float, "y": float, "side": "top"|"bottom"|"left"|"right"}]
          沿轴升序（横排左→右 / 竖排上→下），编号由调用方按同一口径给。
    info: {side, n, pitch, depth, resid, band, cands, failed}
    """
    x0, y0 = float(min(box[0], box[2])), float(min(box[1], box[3]))
    x1, y1 = float(max(box[0], box[2])), float(max(box[1], box[3]))
    X0 = max(0, int(np.floor(x0))); X1 = min(img.shape[1], int(np.ceil(x1)))
    Y0 = max(0, int(np.floor(y0))); Y1 = min(img.shape[0], int(np.ceil(y1)))
    if X1 - X0 < 12 or Y1 - Y0 < 12:
        return [], dict(failed="box too small", side=None, n=0, pitch=0.0,
                        depth=0.0, resid=[], cands={})
    W = _whitish(img[Y0:Y1, X0:X1])
    bw, bh = X1 - X0, Y1 - Y0

    probes = {}
    for s in ("top", "bottom", "left", "right"):
        r = _side_probe(W, s, bw, bh, n)
        if r is None:
            continue
        probes[s] = r

    info = dict(side=None, n=0, pitch=0.0, depth=0.0, resid=[], band=None,
                cands={k: dict(score=round(v["score"], 3), P=round(v["P"], 2),
                               band=[int(v["band"][0]), int(v["band"][1])])
                       for k, v in probes.items()},
                failed="")
    if not probes:
        info["failed"] = "no single-row evidence"
        return [], info

    def pick(a, b):
        pool = [probes[s] for s in (a, b) if s in probes]
        return max(pool, key=lambda r: r["score"]) if pool else None

    best_h, best_v = pick("top", "bottom"), pick("left", "right")
    pool = [r for r in (best_h, best_v) if r is not None]
    best = max(pool, key=lambda r: r["score"])
    side = best["side"]
    opp = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}[side]
    # 单排约束：对边证据若与本边同量级 → 这不是 O，宁可报空
    if opp in probes and probes[opp]["score"] >= SINGLE_MARGIN * best["score"]:
        info["failed"] = "two rows (%s+%s)" % (side, opp)
        return [], info

    band, P2, p = best["band"], best["P2"], best["raw"]
    c, P = best["c"], best["P"]
    # 齿位精修：在梳齿附近找剖面的局部峰（亚像素），再整体重拟合 c,P
    yy, xx = [], []
    for k in range(n):
        xk = c + k * P
        i0, i1 = int(round(xk - 0.28 * P)), int(round(xk + 0.28 * P))
        i0, i1 = max(1, i0), min(len(p) - 2, i1)
        if i1 <= i0:
            continue
        j = i0 + int(np.argmax(p[i0:i1 + 1]))
        den = p[j - 1] - 2 * p[j] + p[j + 1]
        dx = 0.0 if abs(den) < 1e-9 else 0.5 * (p[j - 1] - p[j + 1]) / den
        yy.append(j + float(np.clip(dx, -0.5, 0.5)))
        xx.append(k)
    if len(xx) >= 3:
        A = np.vstack([np.ones(len(xx)), np.array(xx, float)]).T
        sol, *_ = np.linalg.lstsq(A, np.array(yy, float), rcond=None)
        c, P = float(sol[0]), float(sol[1])
    xs = np.array([c + k * P for k in range(n)], float)
    resid = [float(abs(xs[k] - yy[i])) for i, k in enumerate(xx)] if yy else []

    # ---- 深度：逐齿「焊盘−间隙」对比曲线，取「引脚整体」的加权重心
    # ⚠ 该曲线常见**两瓣**（焊盘一瓣、引脚银脚一瓣），两瓣幅度接近，取 argmax 会
    #   随分辨率来回跳（实测同一颗原图 z=32.6 / 0.7 倍 z=21.9）。所以把离峰 ≤2P
    #   且 ≥0.40·峰的所有段并起来，量整根引脚的中心 —— 这才是标注该落的位置。
    C = _tooth_contrast(P2, xs, P)
    Cp = np.maximum(C, 0.0)
    Cg = Cp.mean(0)
    if Cg.max() <= 1e-9:
        info["failed"] = "no pad stripe"
        return [], info
    pk = float(Cg.max())
    im = int(np.argmax(Cg))
    runs = [r for r in _energy_runs(Cg, 0.40, min_rows=2)
            if r[0] <= im + 2.0 * P and r[1] >= im - 2.0 * P]
    if not runs:
        runs = [(im, im)]
    elif Z_ANCHOR == "pad":
        # 焊盘在引脚的外侧（靠框边），取起点最小的一瓣
        runs = [min(runs, key=lambda r: r[0])]
    lo_i = min(r[0] for r in runs)
    hi_i = max(r[1] for r in runs)
    core = (lo_i, hi_i)
    w = Cp[:, lo_i:hi_i + 1]
    dd = np.arange(lo_i, hi_i + 1, dtype=np.float64)
    den = w.sum(1).astype(np.float64)
    zs = np.where(den > 1e-9, (w * dd).sum(1) / np.maximum(den, 1e-9), np.nan)
    ok = np.isfinite(zs)
    if ok.sum() < 1:
        info["failed"] = "no pad stripe"
        return [], info
    zres, nout = 0.0, 0
    if ok.sum() >= 3 and ROW_FIT:
        zf, zres, nout = _fit_row_z(zs)
        zs = zf
    elif ok.sum() < len(zs):
        zs[~ok] = np.nanmedian(zs)

    pins = []
    for k in range(n):
        a = float(np.clip(xs[k], 0, P2.shape[1] - 1))
        z = float(np.clip(zs[k], 0, P2.shape[0] - 1))
        if side == "top":
            px, py = X0 + a, Y0 + z
        elif side == "bottom":
            px, py = X0 + a, Y1 - 1 - z
        elif side == "left":
            px, py = X0 + z, Y0 + a
        else:
            px, py = X1 - 1 - z, Y0 + a
        pins.append(dict(x=float(np.clip(px, x0, x1)), y=float(np.clip(py, y0, y1)),
                         side=side))
    if side in ("top", "bottom"):
        pins.sort(key=lambda d: d["x"])
    else:
        pins.sort(key=lambda d: d["y"])

    info.update(side=side, n=len(pins), pitch=float(P),
                depth=float(np.nanmedian(zs)), band=[int(band[0]), int(band[1])],
                core=[int(core[0]), int(core[1])], zres=round(zres, 2), nout=nout,
                resid=[round(r, 2) for r in resid], score=round(float(best["score"]), 3))
    return pins, info


def number_pins(pins):
    """单排编号：沿轴升序 1..n（横排左→右、竖排上→下）。就地返回。"""
    if not pins:
        return pins
    s = pins[0].get("side")
    if s in ("top", "bottom"):
        pins.sort(key=lambda d: d["x"])
    else:
        pins.sort(key=lambda d: d["y"])
    for i, d in enumerate(pins, 1):
        d["name"] = str(i)
    return pins
