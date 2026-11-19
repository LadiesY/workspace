# -*- coding: utf-8 -*-
"""
GrayBoost -- low-resolution / blurry chip pin detection (standalone experiment)

Fully decoupled from pin_detect.py and PinTool/. No existing file is touched.

Pipeline
    gray -> upsample -> flat-field (illumination equalize) -> CLAHE
    -> USM sharpen / top-hat -> multi-threshold voting binarize -> morphology
    -> pad-column segmentation on a high-percentile bright mask
    -> periodicity scoring picks the two real pad columns (this is what keeps
       the green-PCB glare and the body silkscreen from being mistaken for pads)
    -> row-projection peak pick -> grid fit -> fill missing slots -> number

Usage
    python gray_boost.py [image] [--out dir] [--scale 3] [--pin1 br]
"""
import os
import sys
import json
import argparse

import numpy as np
import cv2

SRC_DEFAULT = (r'C:\Users\20309\.workbuddy\clipboard-images'
               r'\clipboard-2026-10-09T08-47-28-230Z-0cfa737d.jpg')
OUT_DEFAULT = r'D:\PCB4\shujuji3\gray_pin\out'

CFG = {
    'scale': 3.0,          # upsample factor
    'bg_frac': 0.045,      # background sigma = frac * min(H,W)
    'clahe_clip': 3.2,
    'clahe_tile': 24,
    'usm': 0.9,
    'top_frac': 0.062,     # top-hat kernel = frac * min(H,W)
    'block_frac': 0.045,   # adaptive threshold block
    'adap_C': -7.0,
    'vote_need': 2,        # how many of the 3 masks must agree
    'open_k': 3,
    'close_max': 9,
    'col_pct': 90.0,       # percentile used to build the column mask
    'seg_frac': 0.30,      # column segment threshold
    'seg_min_w': 20,
    'seg_merge': 45,
    'seg_min_w2': 10,      # fine salience: single pad column, not a whole field
    'seg_merge2': 12,
    'band_frac': 0.35,     # fraction of the band width used as the "topK" pool
    'peak_ratio': 0.32,    # row-projection peak threshold
    'peak_pos': 0.0,       # detrended peak must reach this * full positive height
    'peak_rel': 0.0,       # a peak must reach this * frame p99.5 brightness
    'min_gap_frac': 0.22,  # the two pad columns must be at least this far apart
    'cv_gate': 0.12,       # row-gap regularity gate (real pad columns are <0.10)
    'band_rel': 0.0,       # a pad column must be this bright vs the frame's p99.5
    'sub_ac': 0.45,        # accept lag T/k only if ac[T/k] >= this * ac[T] ...
    'sub_ac_abs': 0.30,    # ... and never below this absolute autocorrelation
    'evid_rel': 0.72,      # a peak must reach this fraction of its band's median peak
    'fill_rel': 0.55,      # fill a slot when its local peak >= rel * median real
    # 端点杂点：贴在图片边上的亮块（板边反光 / 邻件引脚 / 丝印 / 本体投影）**亮度
    # 可能和真引脚一样高**，`_evid_prune` 的「变暗才丢」拦不住。它的指纹是
    # 「**紧贴图边** 且到相邻真引脚的**间隔明显短于 pitch**」——若它是真引脚，它
    # 就该在 1 个 pitch 处。注意只治「过短」：末端真引脚若因透视/畸变**间隔偏大**
    # （s3 实测 1.34×pitch）不能误剪。贴边量按**图幅比例**定（实测三例分别占
    # 6/477、19/945、12/277，都在 10% 内；用 ×pitch 会随 pitch 漂移）。
    'end_lat_short': 0.80,   # 端点间隔 < 其余间隔中位的这个比例 -> 判杂点
    'end_lat_border': 0.10,  # 且必须落在「距图两端 这个比例*图幅 内」（贴边）
    'pin1_corner': 'br',

    # ---- class B: body localisation + edge-hugging pad band
    'body_sat': 0.62,      # body = saturated below this * frame p99 saturation
    'body_val': 0.62,      #        and darker than this * frame p99.8 value
    'body_row_frac': 0.30,  # a body row is >= this share of "not green board"
    'body_min_h': 14,      # reject a body shorter than this many rows
    'body_min_w': 12,      # reject a body narrower than this many columns
    'body_sat_k': 1.45,    # trim end rows greener than this * body core sat
    'body_sat_b': 12.0,
    'body_min_frac': 0.04,  # body blob must cover this share of the frame
    'strip_frac': 0.30,    # band depth = this * body thickness
    'blob_level': 0.35,    # pad = band median + this * (p99 - median)
    'blob_min_span': 12.0,  # reject a band with less dynamic range than this
    'blob_min_area': 0.0012,  # smallest pad, share of (band width * depth)
    'blob_min_wfrac': 0.025,  # narrowest pad, share of the body length
    'blob_max_wfrac': 0.12,  # widest pad, share of the frame width
    'blob_gap_top': 0.16,  # top pad must come within this * depth of the body
    'blob_gap_bot': 0.55,  # bottom pads sit further out (lead frame)
    'blob_sat': 0.35,      # a lead is achromatic: bright *and* saturation <= this
    'blob_chroma': 0.40,   # ungated x-window more chromatic than this -> shard
                           # (real leads measured <= 0.21; the board-edge shard
                           #  that survives the sat gate measures 0.42)
    'blob_merge': 6,       # merge two blobs closer than this along x
    'blob_protrude': 0.10,  # a bottom lead must stick out this * depth past the edge
    'blob_border_h': 1.7,   # border blob taller than this * median pad = board edge
    'blob_min_total': 3,
    'gap_ratio_gate': 2.4,  # class B only when the longest hole / median gap >=

    # ---- class C: quad (pads on all four edges of a square package)
    'quad_sat': 0.45,      # body: saturation <= this * frame p95 saturation
    'quad_min_frac': 0.06,  # smallest body blob, share of the frame
    'quad_ar': 0.72,       # min(w,h)/max(w,h) of the body -- must look square
    'quad_pin_rel': 0.45,  # "this is a pin" level, between body and frame p99
    'quad_prof': 0.08,     # pin-occupancy that marks a row as being in a band
    'quad_band': 0.048,    # a real pin band is at least this * min(H,W) thick
    'quad_gap': 0.03,      # quiet stretch that marks the package edge
    'quad_probe': 26,      # depth of the first, pitch-measuring band
    # 信号检测（找峰、算点数/规则性）用的带深 = 这个系数 * 实测 pitch。
    # 保持小：它决定「哪里是引脚」，实测这一档最稳。
    'quad_depth': 1.15,    # detection band depth = this * measured pitch
    'quad_depth_min': 6,
    'quad_depth_max': 60,
    # **点位窗口**：点的法线位置 = 这条带上「亮带中段」的中点。引脚沿法线长约
    # 3 个 pitch（实测 V5 的 64 脚 QFP：pitch 4.87px、引脚亮带 13~15px）。窗口
    # 必须**盖住整根引脚**：2.2*pitch 时带在引脚最亮的一段（外侧焊盘）之前就截断
    # 了，中点被拉回内侧 1/3，点看着「贴在引脚根部」。3.0*pitch 覆盖整根后中点
    # 回到引脚正中（实测「点处/该针最亮」0.53 -> 0.86）。
    # 注意：只改点位，不改检测，点数/触发判据完全不变。
    'quad_pin_depth': 3.00,  # locator band depth = this * pitch
    'quad_detrend': 121,   # box width that removes the band's slow trend
    'quad_jump': 0.30,     # a pad gap may differ from the median by this much
    'quad_min_pins': 4,    # an edge with fewer pads than this is not an edge
    # 四条边必须基本一致。真 QFP 四边点数在物理上完全相等，但实测有 ±1 的
    # 数点噪声（角上多/少一颗；例：ST 16C554 实测 17/15/16/16，真值四边各 16）。
    # tol=1 会把这种"总数其实完全正确"的 QFP 直接拒掉 -> 掉回 class B 只报 3~4 颗。
    # 放宽到 2 不影响安全性：quad_detect 已经要求**至少 3 条边有真实点阵**
    # （双列 SOP 只有 2 条），外加 quad_body 的近方形暗本体门槛。
    'quad_tol': 2,         # the four edges must agree to within this many pads
    'quad_cv': 0.20,       # and each edge's pad spacing must be this regular

    # ---- class C'': 双列封装，但**本体不成方形 / 太暗太糊**，quad_body 收不到
    #  （实测这颗模糊 SOIC-10：暗色团块只占 3.4% < quad_min_frac，且 ar=0.32
    #   < quad_ar，quad_body 直接返回 None，于是 quad/dual 两路都没了，掉到
    #   class A 报 11 点：上排 5 个对 + 本体中间 6 个假点、下排 5 个全漏）。
    #  这时不再依赖本体：直接从**引脚亮带**（金属=亮且低饱和）的外缘定出两条
    #  对边，再交给 quad_side 去测点阵 —— 用的是同一条成熟链路。
    'dual_lvl': 80.0,      # 金属阈值的灰度分位（blur 小图上 80 分位最稳）
    'dual_sat': 0.50,      # 金属：饱和 <= 这个系数 * 全图 p99 饱和
    'dual_run': 0.25,      # 一条「亮带」要求该行/列至少这个比例是金属
    'dual_min_len': 0.04,  # 亮带最短长度 = 这个系数 * 该轴长度
    'dual_border': 0.25,   # 亮带必须从这个距离内开始（贴着图边）
    'dual_body': 0.25,     # 两条亮带之间至少要空出这个比例（中间是本体的暗区）
    # 同一条边上的点必须**共线**：它们的法向偏移相对中位不能超过这个 * pitch。
    # 这是「亮带兜底」唯一的自检 —— 实测它正好把 class A 本来就对的双列图挡在
    # 门外（s3：左 1.38 / 右 0.93 * pitch，明显有杂点跳出），而真正需要兜底的
    # 模糊图通过（test4：上排 0.00 / 下排 0.38）。
    'dual_straight': 0.60,
}


# ----------------------------------------------------------------- helpers
def odd(v, lo=3):
    v = int(round(v))
    v = max(lo, v)
    return v if v % 2 == 1 else v + 1


def ek(n):
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (odd(n), odd(n)))


def save(path, arr):
    ext = os.path.splitext(path)[1] or '.png'
    ok, buf = cv2.imencode(ext, arr)
    if ok:
        buf.tofile(path)
    return ok


def to_bgr(im):
    return cv2.cvtColor(im, cv2.COLOR_GRAY2BGR) if im.ndim == 2 else im


def otsu_thr(gray):
    t, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return float(t)


# ----------------------------------------------------------------- stage 1
def preprocess(img, cfg):
    """gray -> upsample -> flat-field -> CLAHE -> USM -> top-hat"""
    g0 = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h0, w0 = g0.shape
    s = float(cfg['scale'])
    g = cv2.resize(g0, (int(round(w0 * s)), int(round(h0 * s))),
                   interpolation=cv2.INTER_CUBIC)
    mn = min(g.shape)

    # (a) flat field: subtract a very wide gaussian (lighting / background)
    sig = max(6.0, cfg['bg_frac'] * mn)
    bg = cv2.GaussianBlur(g.astype(np.float32), (0, 0), sig)
    flat = np.clip(g.astype(np.float32) - bg + 128.0, 0, 255).astype(np.uint8)

    # (b) CLAHE on the flat-fielded image
    t = int(cfg['clahe_tile'])
    clahe = cv2.createCLAHE(clipLimit=float(cfg['clahe_clip']),
                            tileGridSize=(t, t))
    ce = clahe.apply(flat)

    # (c) unsharp mask
    a = float(cfg['usm'])
    if a > 0:
        bl = cv2.GaussianBlur(ce, (0, 0), 1.2)
        sh = cv2.addWeighted(ce, 1.0 + a, bl, -a, 0)
    else:
        sh = ce

    # (d) white top-hat: keeps structures smaller than the kernel
    th = cv2.morphologyEx(sh, cv2.MORPH_TOPHAT, ek(odd(cfg['top_frac'] * mn, 9)))
    return g0, g, flat, ce, sh, th


# ----------------------------------------------------------------- stage 2
def binarize(ce, th, cfg):
    mn = min(ce.shape)
    _, b_otsu = cv2.threshold(ce, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    b_adap = cv2.adaptiveThreshold(ce, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY,
                                   odd(cfg['block_frac'] * mn, 15),
                                   float(cfg['adap_C']))
    _, b_top = cv2.threshold(th, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    votes = ((b_otsu > 0).astype(np.uint8) + (b_adap > 0).astype(np.uint8)
             + (b_top > 0).astype(np.uint8))
    fuse = (votes >= int(cfg['vote_need'])).astype(np.uint8) * 255
    return b_otsu, b_adap, b_top, fuse


def clean_mask(m, cfg):
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, ek(int(cfg['open_k'])))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, ek(int(cfg['close_max'])))
    return m


# ----------------------------------------------------------------- stage 3
def peaks_1d(v, thr_ratio=0.32, min_dist=None, pos_frac=0.0):
    v = np.asarray(v, float)
    n = v.size
    if n < 3:
        return []
    lo, hi = float(v.min()), float(v.max())
    if hi - lo < 1e-6:
        return []
    thr = lo + float(thr_ratio) * (hi - lo)
    # detrended signals are centred on 0: a real pin is a strong *positive*
    # excursion, while bare board / body texture only wobbles near the floor.
    # A plain lo+ratio*(hi-lo) threshold lets those wobbles through whenever
    # the negative side of the range is wide, so also demand a fraction of the
    # full positive height.
    if pos_frac > 0.0:
        thr = max(thr, hi * float(pos_frac))
    cand = [i for i in range(1, n - 1)
            if v[i] >= v[i - 1] and v[i] >= v[i + 1] and v[i] >= thr]
    if not cand:
        return []
    if not min_dist:
        d = np.diff(cand)
        d = d[d > 0]
        min_dist = max(2, int(np.median(d) * 0.55)) if d.size else 2
    keep = []
    for i in sorted(cand, key=lambda i: -v[i]):
        if all(abs(i - j) >= min_dist for j in keep):
            keep.append(i)
    return sorted(keep)


def autocorr(x):
    """normalised autocorrelation of a 1-D signal; index = lag"""
    x = np.asarray(x, float)
    n = x.size
    if n < 24:
        return None
    x = x - x.mean()
    ac = np.correlate(x, x, 'full')[n - 1:]
    if ac[0] <= 1e-9:
        return None
    return ac / ac[0]


def dominant_period(x, lo_frac=0.05, hi_frac=0.45):
    """autocorrelation period of a 1-D signal -> (lag, normalised peak)"""
    ac = autocorr(x)
    if ac is None:
        return None
    n = ac.size
    lo = max(4, int(lo_frac * n))
    hi = max(lo + 2, min(n - 2, int(hi_frac * n)))
    if hi <= lo:
        return None
    k = lo + int(np.argmax(ac[lo:hi]))
    return float(k), float(ac[k])


def peak_lattice(sm, T, cfg, ac=None):
    """peaks of a detrended column signal, robust to a doubled autocorr lag.

    `dominant_period` frequently locks onto the 2nd (or 3rd) harmonic: a dense
    pin row whose odd/even pins differ slightly in brightness gives a stronger
    autocorrelation peak at 2x the pitch.  Feeding that lag into `peaks_1d` as
    the min-distance then suppresses every other real pin, so the column comes
    out with half the pins and twice the pitch -- and because two *wrong* bands
    can still agree with each other, the pair scorer happily picks them.

    Try the lag and its sub-harmonics, but only accept a sub-harmonic whose own
    autocorrelation is still comparable to the dominant one.  A true half-pitch
    (all 24 pins present, alternating brightness) has ac[T/2] >= ac[T]; a lag
    that is merely a numeric divisor of T does not.  Without that gate a fine
    lattice can always beat the real one on raw peak count and the detector
    starts reporting 3x-5x too many pins.

    Returns (peaks, pitch, lag) or None.
    """
    T = int(round(T))
    ref = float(ac[T]) if (ac is not None and 0 <= T < ac.size) else 0.0
    floor = max(float(cfg['sub_ac_abs']),
                float(cfg['sub_ac']) * ref)
    best = None
    for div in (1, 2, 3, 4):
        lag = int(round(T / div))
        if lag < 4:
            break
        if div > 1:
            if ac is None or lag >= ac.size or float(ac[lag]) < floor:
                continue
        md = max(3, int(0.55 * lag))
        pk = peaks_1d(sm, float(cfg['peak_ratio']), md,
                      float(cfg.get('peak_pos') or 0.0))
        if len(pk) < 3:
            continue
        gp = np.diff(pk).astype(float)
        gp = gp[gp > 0]
        if gp.size < 2:
            continue
        cv = float(np.std(gp) / np.mean(gp))
        q = len(pk) * max(0.0, 1.0 - min(cv, 1.0))
        if best is None or q > best[0] + 1e-9:
            best = (q, pk, float(np.mean(gp)), lag)
    if best is None:
        return None
    return best[1], best[2], best[3]


def band_signal(g, x0, x1, frac=0.35):
    """per-row brightness of the brightest `frac` of the band -> 1-D signal"""
    sub = g[:, x0:x1 + 1].astype(np.float32)
    k = max(1, int(frac * sub.shape[1]))
    srt = np.sort(sub, axis=1)[:, -k:]
    return srt.mean(1)


def column_segments(S, frac=0.30, min_w=20, merge=45):
    """high-percentile bright mask -> list of (x0, x1) vertical bands"""
    h, w = S.shape
    col = (S > 0).astype(np.float32).sum(0)
    if float(col.max()) <= 0:
        return []
    col = cv2.blur(col.reshape(1, -1), (31, 1)).ravel()
    thr = float(frac) * float(col.max())
    on = col >= thr
    segs = []
    i = 0
    while i < w:
        if on[i]:
            j = i
            while j + 1 < w and on[j + 1]:
                j += 1
            segs.append([i, j])
            i = j + 1
        else:
            i += 1
    out = []
    for s in segs:
        if out and s[0] - out[-1][1] <= merge:
            out[-1][1] = s[1]
        else:
            out.append(list(s))
    return [(a, b) for a, b in out if b - a + 1 >= min_w]


def band_xcenter(g, x0, x1, y0, y1):
    """brightness weighted x-centre of one pad inside the band"""
    y0 = max(0, int(y0))
    y1 = min(g.shape[0] - 1, int(y1))
    sub = g[y0:y1 + 1, x0:x1 + 1].astype(np.float32)
    if sub.size == 0:
        return 0.5 * (x0 + x1)
    base = float(np.percentile(sub, 50))
    wgt = np.clip(sub - base, 0, None).sum(0)
    if wgt.sum() <= 0:
        return 0.5 * (x0 + x1)
    xs = np.arange(x0, x1 + 1, dtype=np.float32)
    return float((wgt * xs).sum() / wgt.sum())


def column_candidate(g, x0, x1, cfg):
    """score one column band as a pad column by its periodicity"""
    hi = band_signal(g, x0, x1, float(cfg['band_frac']))
    base = cv2.blur(hi.reshape(-1, 1), (1, 121)).ravel()
    sm = cv2.blur((hi - base).reshape(-1, 1), (1, 9)).ravel()
    per = dominant_period(sm)
    if per is None:
        return None
    T, acp = per
    lat = peak_lattice(sm, T, cfg, autocorr(sm))
    if lat is None:
        return None
    pks, lat_pitch, T = lat
    if len(pks) < 3:
        return None
    # A peak must sit on a genuinely *bright* row, not on the mid-bright board
    # or on body texture that the detrending turned into a bump.  Reference is
    # the frame's bright content (cached on cfg by detect()); when that leaves
    # too few peaks (a dim chip in a frame with one blown highlight) keep them.
    ref = float(cfg.get('_ref') or 0.0)
    pr = float(cfg.get('peak_rel') or 0.0)
    if ref > 0.0 and pr > 0.0:
        kept = [p for p in pks if hi[int(p)] >= pr * ref]
        if len(kept) >= 3:
            pks = kept
    gaps = np.diff(pks).astype(float)
    gaps = gaps[gaps > 0]
    cv_ = float(np.std(gaps) / np.mean(gaps)) if gaps.size > 1 else 9.9
    p90 = float(np.percentile(hi, 90))
    p10 = float(np.percentile(hi, 10))
    contrast = (p90 - p10) / max(1.0, p90)
    score = (acp * max(0.0, 1.0 - min(cv_, 1.0)) * contrast
             * min(len(pks), 10) / 10.0)
    return dict(x0=x0, x1=x1, peaks=[int(p) for p in pks],
                pitch=float(np.mean(gaps)) if gaps.size else 0.0,
                score=float(score), contrast=float(contrast), cv=cv_,
                acp=acp, T=T, signal=hi, detrend=sm, bright=float(p90))


def peak_match(pa, pb):
    """median nearest-neighbour distance between two peak sets"""
    if not pa or not pb:
        return 9e9
    return float(np.median([min(abs(y - z) for z in pb) for y in pa]))


def estimate_pitch(vals):
    v = np.sort(np.asarray(vals, float))
    if v.size < 2:
        return None
    d = np.diff(v)
    d = d[d > 1e-6]
    if d.size == 0:
        return None
    p = float(np.percentile(d, 20))
    for _ in range(10):
        k = np.maximum(1.0, np.round(d / p))
        cand = d / k
        cand = cand[(cand > 0.45 * p) & (cand < 1.9 * p)]
        if cand.size == 0:
            break
        np_ = float(np.median(cand))
        if abs(np_ - p) < 0.02 * p:
            p = np_
            break
        p = np_
    return p


def fit_grid(vals, pitch, tol_ratio=0.34):
    v = np.asarray(sorted(vals), float)
    if v.size == 0 or not pitch:
        return None
    o = float(v[0])
    for _ in range(6):
        k = np.round((v - o) / pitch)
        ok = np.abs(v - (o + k * pitch)) <= tol_ratio * pitch
        if ok.sum() < 2:
            break
        b, a = np.polyfit(k[ok], v[ok], 1)
        if b <= 1e-6:
            break
        o, pitch = float(a), float(b)
    k = np.round((v - o) / pitch)
    return float(o), float(pitch), k.astype(int), np.abs(v - o - k * pitch)


def snap(ce, x, y, r):
    """brightness-weighted centroid inside +-r"""
    h, w = ce.shape
    x0, x1 = max(0, int(x - r)), min(w, int(x + r) + 1)
    y0, y1 = max(0, int(y - r)), min(h, int(y + r) + 1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return float(x), float(y)
    win = ce[y0:y1, x0:x1].astype(np.float32)
    wgt = np.clip(win - float(np.percentile(win, 40)), 0, None)
    if wgt.sum() <= 0:
        return float(x), float(y)
    ys, xs = np.mgrid[y0:y1, x0:x1]
    return float((xs * wgt).sum() / wgt.sum()), float((ys * wgt).sum() / wgt.sum())


def _evid_prune(pads, bands, ce, pitch, cfg, min_keep=4):
    """Drop pads whose local brightness evidence is far below the band median.

    A chain of real pads is uniformly bright; an image-border / band-edge
    artefact lands at the end of the chain and is clearly dimmer.  Predicted
    pads that sit on a slot which was just rejected go away with it, so the
    fill step cannot resurrect a bad peak.
    """
    h, w = ce.shape
    for name, c in bands:
        recs = pads.get(name) or []
        ridx = [i for i, q in enumerate(recs) if not q.get('pred')]
        if len(ridx) < min_keep:
            continue
        xx0, xx1 = max(0, c['x0']), min(w, c['x1'] + 1)
        vals = []
        for i in ridx:
            q = recs[i]
            yy0 = max(0, int(q['cy'] - 0.35 * pitch))
            yy1 = min(h, int(q['cy'] + 0.35 * pitch) + 1)
            vals.append(float(ce[yy0:yy1, xx0:xx1].max()) if yy1 > yy0 else 0.0)
        ref = float(np.median(vals))
        if ref <= 0:
            continue
        thr = float(cfg['evid_rel']) * ref
        edge = 0.45 * pitch
        ymax = ce.shape[0] - 1
        bad = []
        for k, i in enumerate(ridx):
            v = vals[k]
            y = recs[i]['cy']
            if v < thr or ((y < edge or y > ymax - edge) and v < ref):
                bad.append(i)          # dim, or stuck on the top/bottom border
        if not bad or len(ridx) - len(bad) < min_keep:
            continue
        bset = set(bad)
        btol = 0.40 * pitch
        bady = [recs[i]['cy'] for i in bad]
        pads[name] = [q for i, q in enumerate(recs)
                      if i not in bset
                      and not (q.get('pred')
                               and any(abs(q['cy'] - y) <= btol for y in bady))]


# ----------------------------------------------------------------- stage 4
def _lattice_end_prune(pads, bands, ce, cfg, min_keep=3):
    """Drop a stray END pad that hugs the frame border and sits closer than pitch.

    `_evid_prune` removes an end pad only when it is *dimmer* than the chain.
    A board-edge stripe or a neighbour's lead can be just as bright, so it
    survives that gate.  Its fingerprint is twofold: it hugs the frame border
    *along the band axis*, and it lands well inside one pitch of the first real
    pad (a real pin would sit ~1 pitch away).  Only the two ends are eligible
    (an interior pad is fenced in from both sides) and only the "too short"
    direction is treated -- a genuine end pin stretched *longer* than pitch by
    perspective must be kept.
    """
    short = float(cfg['end_lat_short'])
    frac = float(cfg['end_lat_border'])
    H = float(ce.shape[0])
    lo, hi = frac * H, (1.0 - frac) * H
    for name, _c in bands:
        recs = list(pads.get(name) or [])
        while len(recs) > min_keep:
            cy = np.asarray([q['cy'] for q in recs], float)
            gaps = np.diff(cy)
            if len(gaps) < 1:
                break
            cand = []
            if cy[0] < lo:
                rest = float(np.median(gaps[1:])) if len(gaps) > 1 else float(gaps[0])
                if rest > 1e-6 and float(gaps[0]) < short * rest:
                    cand.append((1.0 - float(gaps[0]) / rest, 0))
            if cy[-1] > hi:
                rest = float(np.median(gaps[:-1])) if len(gaps) > 1 else float(gaps[-1])
                if rest > 1e-6 and float(gaps[-1]) < short * rest:
                    cand.append((1.0 - float(gaps[-1]) / rest, -1))
            if not cand:
                break
            _dev, which = max(cand)
            drop = recs[0] if which == 0 else recs[-1]
            keep = recs[1:] if which == 0 else recs[:-1]
            btol = 0.40 * float(np.median(gaps))
            recs = [q for q in keep
                    if not (q.get('pred')
                            and abs(q['cy'] - drop['cy']) <= btol)]
        pads[name] = recs


def detect(img, cfg=None):
    cfg = dict(CFG, **(cfg or {}))
    g0, g, flat, ce, sh, th = preprocess(img, cfg)
    cfg['_ref'] = float(np.percentile(g, 99.5))
    b_otsu, b_adap, b_top, fuse = binarize(ce, th, cfg)
    mask = clean_mask(fuse, cfg)
    h, w = ce.shape

    steps = [('1 input', g0), ('2 gray', g), ('3 flat-field', flat),
             ('4 CLAHE', ce), ('5 sharpen', sh), ('6 top-hat', th),
             ('7 otsu', b_otsu), ('8 adaptive', b_adap),
             ('9 vote-fuse', fuse), ('10 cleaned', mask)]
    empty = dict(pads={}, steps=steps, ce=ce, g=g, body=None, pitch=None,
                 mask=mask, cands=[])

    # ---- (1) column candidates, scored by periodicity.
    # Two saliences are pooled: widely merged segments (a whole pad field) and
    # tightly split ones (a single pad column).  A frame that also contains the
    # neighbouring board copper only separates at the fine salience, while a
    # clean close-up is happy with either -- so let the pair score decide.
    S = (ce >= np.percentile(ce, float(cfg['col_pct']))).astype(np.uint8) * 255
    segs = column_segments(S, float(cfg['seg_frac']), int(cfg['seg_min_w']),
                           int(cfg['seg_merge']))
    segs = sorted(set(segs) | set(column_segments(
        S, float(cfg['seg_frac']), int(cfg['seg_min_w2']),
        int(cfg['seg_merge2']))))
    cands = [c for c in (column_candidate(g, a, b, cfg) for a, b in segs) if c]
    if len(cands) < 2:
        empty['note'] = 'less than two pad columns'
        return empty

    # A pad column is one of the *brightest* things in the frame. A shadowed
    # row / bare green board still yields periodicity-shaped noise peaks, so
    # gate on absolute brightness w.r.t. the frame's p99.5 -- but only when the
    # gate still leaves a pair to choose from (never lose the last two).
    ref = float(np.percentile(g, 99.5))
    lit = [c for c in cands if c['bright'] >= float(cfg['band_rel']) * ref]
    if len(lit) >= 2:
        cands = lit

    # a real pad column has very regular row gaps; the body silkscreen and the
    # green-PCB glare do not. Use the gate when it still leaves a pair to choose
    # from, otherwise fall back to every candidate.
    strict = [c for c in cands if c['cv'] <= float(cfg['cv_gate'])]
    pool = strict if len(strict) >= 2 else cands

    # ---- (2) pick the best symmetric pair (matching rows, far apart)
    best = None
    for i in range(len(pool)):
        for j in range(i + 1, len(pool)):
            a, b = pool[i], pool[j]
            if b['x0'] - a['x1'] < float(cfg['min_gap_frac']) * w:
                continue
            pp = 0.5 * (a['pitch'] + b['pitch']) or 1.0
            dm = peak_match(a['peaks'], b['peaks'])
            if dm > 0.45 * pp:
                continue
            na, nb = len(a['peaks']), len(b['peaks'])
            wa = a['x1'] - a['x0'] + 1
            wb = b['x1'] - b['x0'] + 1
            # a real pad column is a WIDE band (that is the pad length) and both
            # sides of one package carry the same number of pins.  A skinny band
            # is usually the pin *thickness* seen in the wrong orientation, or a
            # silkscreen / glare stripe.
            wfac = float(np.clip(min(wa, wb) / (0.8 * pp), 0.35, 1.0))
            sc = (a['score'] + b['score'] + 1.2 / (1.0 + dm / pp)
                  + 2.0 * (2.0 - a['cv'] - b['cv'])
                  + 0.35 * min(na, nb) - 0.5 * abs(na - nb))
            sc *= wfac
            if best is None or sc > best[0]:
                best = (sc, a, b, dm)
    if best is None:
        cands.sort(key=lambda c: -c['score'])
        empty['note'] = 'no symmetric pad-column pair'
        empty['cands'] = cands
        return empty
    pair_score, L, R, dm = best

    pitch = 0.5 * (L['pitch'] + R['pitch'])
    rr = max(4.0, 0.26 * pitch)

    pads = {}
    for name, c in (('left', L), ('right', R)):
        bw = c['x1'] - c['x0'] + 1
        xs = [band_xcenter(g, c['x0'], c['x1'], p - 5, p + 5) for p in c['peaks']]
        xmed = float(np.median(xs))          # pads in one column share an x
        recs = []
        for p in c['peaks']:
            sx, sy = snap(ce, xmed, p, rr)
            recs.append(dict(cx=sx, cy=sy, w=bw, h=10, pred=False))
        pads[name] = sorted(recs, key=lambda q: q['cy'])

    # -- drop the "peak" that is really the band / body edge: an image-border
    #    artefact sits at the end of the chain and is much dimmer than the real
    #    pads of the same band, so gate each peak by local brightness evidence.
    _evid_prune(pads, (('left', L), ('right', R)), ce, pitch, cfg)
    for name in ('left', 'right'):
        pads[name].sort(key=lambda q: q['cy'])

    # ...and a *bright* border blob survives the brightness gate entirely; catch
    # it by the pitch it breaks instead.  Do it BEFORE the fill step, otherwise
    # the stray end pad corrupts the lattice and the fill injects fake pads on
    # the other band (test7: 5+1 real -> fill added 2 more).
    _lattice_end_prune(pads, (('left', L), ('right', R)), ce, cfg)
    for name in ('left', 'right'):
        pads[name].sort(key=lambda q: q['cy'])

    yall = [q['cy'] for q in pads['left'] + pads['right']] or [0.0]
    body = dict(x0=float(L['x1']), x1=float(R['x0']),
                y0=float(min(yall)), y1=float(max(yall)))

    # ---- (3) the sparser side borrows the denser side's grid
    if pads['left'] and pads['right']:
        base = 'right' if len(pads['right']) >= len(pads['left']) else 'left'
        tgt = 'left' if base == 'right' else 'right'
        fit = fit_grid([q['cy'] for q in pads[base]], pitch)
        if fit is not None:
            o, p, kk, _ = fit
            real = [q['cy'] for q in pads[tgt]]
            px = float(np.median([q['cx'] for q in pads[tgt]]))
            bw = float(pads[tgt][0]['w'])
            refs = []
            for y in real:
                yy0, yy1 = max(0, int(y - 0.5 * p)), min(h, int(y + 0.5 * p))
                xx0, xx1 = max(0, int(px - 0.5 * bw)), min(w, int(px + 0.5 * bw))
                if xx1 > xx0 and yy1 > yy0:
                    refs.append(float(ce[yy0:yy1, xx0:xx1].max()))
            ref = float(np.median(refs)) if refs else 0.0
            tol = 0.30 * p
            for k in range(int(kk.min()), int(kk.max()) + 1):
                y = o + k * p
                if any(abs(y - ry) <= tol for ry in real):
                    continue
                yy0, yy1 = max(0, int(y - 0.5 * p)), min(h, int(y + 0.5 * p))
                xx0, xx1 = max(0, int(px - 0.5 * bw)), min(w, int(px + 0.5 * bw))
                if xx1 - xx0 < 2 or yy1 - yy0 < 2:
                    continue
                if ref > 0 and float(ce[yy0:yy1, xx0:xx1].max()) < float(cfg['fill_rel']) * ref:
                    continue
                sx, sy = snap(ce, px, y, 0.28 * p)
                pads[tgt].append(dict(cx=sx, cy=sy, w=bw, h=10, pred=True))
        pads[tgt].sort(key=lambda q: q['cy'])

    # the fill step can re-introduce the very artefact we just rejected, so
    # re-run the evidence gate and take the slots it created down with it.
    _evid_prune(pads, (('left', L), ('right', R)), ce, pitch, cfg)
    for name in ('left', 'right'):
        pads[name].sort(key=lambda q: q['cy'])

    # ...and a *bright* border blob survives the brightness gate entirely; catch
    # it by the pitch it breaks instead (idempotent -- the pre-fill call usually
    # already cleaned the chain, this only mops up what the fill added).
    _lattice_end_prune(pads, (('left', L), ('right', R)), ce, cfg)
    for name in ('left', 'right'):
        pads[name].sort(key=lambda q: q['cy'])

    # ---- (4) overlay of the chosen bands, for the report
    vis = cv2.cvtColor(ce, cv2.COLOR_GRAY2BGR)
    for c in (L, R):
        cv2.line(vis, (c['x0'], 0), (c['x0'], h - 1), (0, 0, 255), 3)
        cv2.line(vis, (c['x1'], 0), (c['x1'], h - 1), (0, 255, 255), 3)
    for name, c, col in (('L', L, (255, 0, 0)), ('R', R, (0, 255, 0))):
        for p in c['peaks']:
            cv2.circle(vis, (int(0.5 * (c['x0'] + c['x1'])), int(p)), 6, col, -1)
    steps.append(('11 bands', vis))

    return dict(pads=pads, steps=steps, ce=ce, g=g, body=body, pitch=pitch,
                mask=mask, cands=cands, L=L, R=R, match=dm,
                pair_score=pair_score)


# ----------------------------------------------------------------- stage 5
#  The whole pipeline above works on "pad columns stacked along rows".
#  A chip photographed with its pins on the *top / bottom* edge is the same
#  picture rotated 90 deg, so we just rotate the input, reuse everything, and
#  map the answers back. `orient` is 'h' (pads left/right) or 'v' (pads top/bottom).
def _pt_orig(x, y, orient, H0):
    """work frame -> original frame (H0 = upscaled height of the ORIGINAL img)"""
    if orient == 'h':
        return float(x), float(y)
    return float(y), float(H0 - 1 - x)


def _rect_orig(x0, y0, x1, y1, orient, H0):
    ax, ay = _pt_orig(x0, y0, orient, H0)
    bx, by = _pt_orig(x1, y1, orient, H0)
    return min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)


def work_pins(res, orient, H0):
    """flatten res['pads'] into original-frame points (still unordered)"""
    side_of = {'left': 'left', 'right': 'right'}
    if orient == 'v':
        side_of = {'left': 'bottom', 'right': 'top'}
    pts = []
    for k in ('left', 'right'):
        for q in res['pads'].get(k, []):
            ox, oy = _pt_orig(q['cx'], q['cy'], orient, H0)
            pts.append(dict(x=ox, y=oy, key=float(q['cy']),
                            side=side_of[k], pred=bool(q.get('pred'))))
    return pts


def order_pins(pts, orient, corner):
    """CCW ring numbering, starting from the corner named in cfg['pin1_corner']"""
    c = (corner or 'br').lower()
    row_first = c[0] if c[:1] in ('t', 'b') else 'b'
    col_first = c[1] if len(c) > 1 and c[1] in ('l', 'r') else 'r'
    if orient == 'h':
        seq = ([('right', True), ('left', False)] if row_first == 'b'
               else [('left', True), ('right', False)])
    else:
        first = 'top' if row_first == 't' else 'bottom'
        second = 'bottom' if first == 'top' else 'top'
        asc = (col_first == 'l')
        seq = [(first, not asc), (second, asc)]
    out = []
    for sd, rev in seq:
        out += sorted([p for p in pts if p['side'] == sd],
                      key=lambda q: q['key'], reverse=rev)
    return out


def result_score(res, cfg):
    """how much we trust a detection run (drives orientation / scale choice).
    The pair-selection score already folds in row-gap regularity (cv), band
    width (pad length), pin count and left/right symmetry; on top of that a
    lattice whose row-gap irregularity is above the gate is heavily discounted
    (that is what kills the rotated view of an already-fine image)."""
    if not res.get('L') or not res.get('R') or not res.get('pads'):
        return 0.0
    s = float(res.get('pair_score', 0.0))
    cvl, cvr = float(res['L']['cv']), float(res['R']['cv'])
    if cvl > float(cfg['cv_gate']) or cvr > float(cfg['cv_gate']):
        s *= 0.34
    return s


def detect_best(img, cfg, orients=('h', 'v'), scale_mul=(1.0, 1.7)):
    """try both orientations on a small scale ladder; return the best frame"""
    best = None
    for orient in orients:
        work = (img if orient == 'h' else cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE))
        for m in scale_mul:
            c2 = dict(cfg, scale=float(cfg['scale']) * float(m))
            try:
                res = detect(work, c2)
            except Exception:                 # noqa: one bad branch must not kill the run
                continue
            q = result_score(res, c2)
            if q > 0.0 and (best is None or q > best[0]):
                best = (q, orient, float(c2['scale']), res, c2)
    return best


def annotate(big, pins_pts, body, cfg):
    """draw numbered pins on the original-orientation, upscaled image"""
    out = big.copy()
    h, w = out.shape[:2]
    mn = min(h, w)
    rad = max(3, int(round(0.010 * mn)))
    fs = max(0.5, 0.0016 * mn)
    th = max(1, int(round(0.0028 * mn)))

    if body is not None:
        b = np.asarray(body, np.float32)
        col_b = (80, 200, 80)
        if b.ndim == 1 and b.size == 4:
            cv2.rectangle(out, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])),
                          col_b, max(1, th // 2))
        elif b.size >= 8:
            cv2.polylines(out, [b.reshape(-1, 1, 2).astype(np.int32)], True,
                          col_b, max(1, th // 2))

    pins = []
    for i, q in enumerate(pins_pts, 1):
        x, y = int(round(q['x'])), int(round(q['y']))
        col = (255, 128, 0) if q['pred'] else (0, 0, 255)
        cv2.circle(out, (x, y), rad, col, -1, cv2.LINE_AA)
        cv2.circle(out, (x, y), rad + 2, (255, 255, 255), max(1, th // 2),
                   cv2.LINE_AA)
        tx, ty = x + rad + 3, y + int(fs * 12)
        cv2.putText(out, str(i), (tx, ty), cv2.FONT_HERSHEY_SIMPLEX,
                    fs, (0, 0, 0), th + 2, cv2.LINE_AA)
        cv2.putText(out, str(i), (tx, ty), cv2.FONT_HERSHEY_SIMPLEX,
                    fs, (0, 255, 255), th, cv2.LINE_AA)
        pins.append(dict(idx=i, side=q['side'], x=round(float(q['x']), 1),
                         y=round(float(q['y']), 1), predicted=bool(q['pred'])))
    return out, pins


def tile(imgs, cols=5, cell=400, cap=24):
    tiles = []
    for title, im in imgs:
        im = to_bgr(im)
        h, w = im.shape[:2]
        s = cell / float(w)
        nh = max(1, int(round(h * s)))
        im = cv2.resize(im, (cell, nh),
                        interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
        cv2.rectangle(im, (0, 0), (cell - 1, cap - 1), (20, 20, 20), -1)
        cv2.putText(im, title, (6, cap - 7), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (0, 255, 255), 1, cv2.LINE_AA)
        tiles.append(im)
    rows = int(np.ceil(len(tiles) / float(cols)))
    hh = max(t.shape[0] for t in tiles)
    sheet = np.zeros((rows * (hh + 4), cols * (cell + 4), 3), np.uint8)
    for i, t in enumerate(tiles):
        r, c = i // cols, i % cols
        y, x = r * (hh + 4), c * (cell + 4)
        sheet[y:y + t.shape[0], x:x + t.shape[1]] = t
    return sheet


# --------------------------------------------------- class B: body localisation
#  Class A (the pipeline above) assumes the pins form a *regular lattice* that
#  spans the whole edge.  A long dark package breaks that assumption: only the
#  two ends carry reflective pads and the middle is bare board / shadow, so a
#  periodic fit happily extrapolates a row of pins into empty space.
#
#  Class B works the other way round:
#    1. find the dark package body (low saturation + dark, i.e. NOT green board),
#    2. trace its top and bottom edge column by column -- that also absorbs any
#       slight camera tilt,
#    3. threshold a narrow band hugging that edge and report the actual pad
#       blobs found inside it.  Nothing is ever extrapolated.
def body_mask(img, cfg):
    """-> (mask uint8, ry0, ry1) the dark package body, or None"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    S = hsv[:, :, 1].astype(np.float32)
    V = hsv[:, :, 2].astype(np.float32)
    H, W = S.shape
    if min(H, W) < 24:
        return None
    s_gate = float(cfg['body_sat']) * float(np.percentile(S, 99))
    v_gate = float(cfg['body_val']) * float(np.percentile(V, 99.8))
    raw = (S <= s_gate) & (V <= v_gate)
    rowfrac = raw.sum(1).astype(np.float32) / float(W)

    # longest run of rows that are mostly "not green board"
    on = rowfrac >= float(cfg['body_row_frac'])
    runs, y = [], 0
    while y < H:
        if on[y]:
            j = y
            while j + 1 < H and on[j + 1]:
                j += 1
            runs.append((y, j))
            y = j + 1
        else:
            y += 1
    if not runs:
        return None
    ry0, ry1 = max(runs, key=lambda r: r[1] - r[0])
    if ry1 - ry0 + 1 < int(cfg['body_min_h']):
        return None

    # trim the ends: the board above and the shadow below are *greener* than
    # the moulded body, so gate on the row-median saturation measured against
    # the body's own core.
    L = ry1 - ry0 + 1
    rs = cv2.blur(np.median(S, axis=1).astype(np.float32).reshape(-1, 1),
                  (1, 5)).ravel()
    core = rs[ry0 + L // 4: ry1 - L // 4 + 1]
    if core.size == 0:
        return None
    s_core = float(np.median(core))
    gate = max(float(cfg['body_sat_k']) * s_core,
               s_core + float(cfg['body_sat_b']))
    while ry1 - ry0 > int(cfg['body_min_h']) and rs[ry0] > gate:
        ry0 += 1
    while ry1 - ry0 > int(cfg['body_min_h']) and rs[ry1] > gate:
        ry1 -= 1

    m = np.zeros((H, W), np.uint8)
    m[ry0:ry1 + 1] = raw[ry0:ry1 + 1].astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, ek(odd(0.06 * min(H, W), 5)))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, ek(3))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    if n < 2:
        return None
    i = 1 + int(np.argmax(st[1:, 4]))
    if float(st[i, 4]) < float(cfg['body_min_frac']) * H * W:
        return None
    return (lab == i).astype(np.uint8), ry0, ry1


def body_edges(mask, cfg):
    """per-column top / bottom edge of the body (y), interpolated + smoothed."""
    H, W = mask.shape
    top = np.full(W, -1, np.float32)
    bot = np.full(W, -1, np.float32)
    for x in range(W):
        ys = np.where(mask[:, x] > 0)[0]
        if ys.size:
            top[x] = float(ys[0])
            bot[x] = float(ys[-1])
    ok = (top >= 0) & (bot > top)
    if int(ok.sum()) < int(cfg['body_min_w']):
        return None, None, ok
    idx = np.where(ok)[0]
    topf = np.interp(np.arange(W), idx, top[idx]).astype(np.float32)
    botf = np.interp(np.arange(W), idx, bot[idx]).astype(np.float32)
    k = odd(max(5, 0.10 * W))
    topf = cv2.blur(topf.reshape(1, -1), (k, 1)).ravel()
    botf = cv2.blur(botf.reshape(1, -1), (k, 1)).ravel()
    return topf, botf, ok


def _strip_blobs(V, Sat, edge, edge_ok, bh, outward, cfg):
    """2-D pad blobs inside the band hugging the traced body edge.
    outward = +1: band lies above the edge (its last row is the edge itself);
    outward = -1: band lies below the edge (its row 2 is the edge)."""
    H, W = V.shape
    rows = int(bh) + 3
    strip = np.zeros((rows, W), np.float32)
    sstrip = np.zeros((rows, W), np.float32)
    valid = np.zeros(W, bool)
    for x in range(W):
        if not edge_ok[x]:
            continue
        e = int(round(float(edge[x])))
        y0 = e - bh if outward > 0 else e - 2
        for i in range(rows):
            y = y0 + i
            if 0 <= y < H:
                strip[i, x] = V[y, x]
                sstrip[i, x] = Sat[y, x]
                valid[x] = True
    if int(valid.sum()) < 8 or rows < 5:
        return []
    vals = strip[:, valid]
    med = float(np.median(vals))
    hi = float(np.percentile(vals, 99.0))
    if hi - med < float(cfg['blob_min_span']):
        return []
    thr = med + float(cfg['blob_level']) * (hi - med)
    sat_hi = float(cfg['blob_sat']) * 255.0
    # Keep the brightness-only mask around: it is what a chromatic object looks
    # like *before* the saturation gate shreds it, and it is how we tell an
    # achromatic remnant of such an object from a real (also achromatic) lead.
    mg = (strip >= thr).astype(np.uint8)
    mg[:, ~valid] = 0
    mg = cv2.morphologyEx(mg, cv2.MORPH_CLOSE, ek(3))
    # A lead is metallic: bright *and* achromatic.  A bright patch of bare board
    # (or any coloured part) is just as bright in V, so the brightness gate
    # alone lets it in -- and then it eats the neighbouring lead through the
    # merge step.  Keep only the low-saturation core.
    m = ((mg > 0) & (sstrip <= sat_hi)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, ek(3))
    n, lab, st, cen = cv2.connectedComponentsWithStats(m, 8)
    edge_row = bh if outward > 0 else 2
    gap = float(cfg['blob_gap_top' if outward > 0 else 'blob_gap_bot']) * bh
    a_min = max(8.0, float(cfg['blob_min_area']) * float(valid.sum()) * bh)
    w_min = max(4.0, float(cfg['blob_min_wfrac']) * float(valid.sum()))
    w_max = float(cfg['blob_max_wfrac']) * W
    chrom_gate = float(cfg['blob_chroma'])

    keep = []
    hinfo = {}          # component id -> (height, touches the x frame border)
    for i in range(1, n):
        a = float(st[i, 4])
        x0, y0 = int(st[i, 0]), int(st[i, 1])
        w, h = int(st[i, 2]), int(st[i, 3])
        if a < a_min or w < w_min or w > w_max:
            continue
        # The saturation gate can leave behind the achromatic core of a
        # coloured object (a specular streak on a yellow part, the pale middle
        # of a bright board patch).  That remnant is bright, flat and lead-like.
        # Look at what the same x-window holds in the ungated mask: if it is
        # mostly chromatic, the blob is a shard, not a pad.
        win = mg[:, x0:x0 + w] > 0
        if win.any():
            if float((sstrip[:, x0:x0 + w][win] > sat_hi).mean()) > chrom_gate:
                continue
        touch = (x0 <= 0) or (x0 + w >= W)
        if outward > 0:
            if (y0 + h - 1) < edge_row - gap:      # does not reach the package
                continue
        else:
            # A lead hangs *below* the body edge.  A bright mark on the package
            # that merely starts at the edge (0..2 px in, which happens when the
            # traced edge is a couple of px lower than the real one) is not a
            # pin -- but neither is it something to be rejected outright: the
            # old `y0 < 3` gate threw away real bottom-left leads whose bar
            # begins right at the body edge.  Require a real protrusion instead.
            if (y0 + h - 1) < edge_row + max(2, int(float(cfg['blob_protrude']) * bh)):
                continue
            if y0 > edge_row + gap:                # much too far below
                continue
        # a flat sliver pinned to the frame border is the board edge, not a pad
        if touch and h < 0.55 * bh:
            continue
        keep.append([x0, x0 + w - 1, i, a])
        hinfo[i] = (h, touch)

    # A big *bright* object hugging the frame border (board edge, via, a
    # neighbouring part) passes both the brightness and the sliver gate: it is
    # neither dark nor flat.  Leads, on the other hand, are uniform, so that
    # thing is far taller than the band's typical pad.  Drop it before the
    # merge step, otherwise it glues itself onto the outermost lead and drags
    # that pad's centroid off the package.
    inner = [hh for _i, (hh, tc) in hinfo.items() if not tc]
    if len(inner) >= 2:
        hmed = float(np.median(inner))
        drop = set(i for i, (hh, tc) in hinfo.items()
                   if tc and hh > float(cfg['blob_border_h']) * hmed)
        if drop:
            keep = [r for r in keep if r[2] not in drop]

    if not keep:
        return []

    keep.sort()
    merged = []
    for r in keep:
        if merged and r[0] - merged[-1][1] <= int(cfg['blob_merge']):
            merged[-1][1] = max(merged[-1][1], r[1])
            merged[-1].append(r[2])
        else:
            merged.append([r[0], r[1], r[2]])

    out = []
    for t in merged:
        x0, x1 = t[0], t[1]
        labs = t[2:]
        sel = np.isin(lab[:, x0:x1 + 1], labs) & (m[:, x0:x1 + 1] > 0)
        ys, xs = np.nonzero(sel)
        if ys.size == 0:
            continue
        wgt = np.clip(strip[:, x0:x1 + 1][sel] - thr, 1e-3, None)
        cx = float(x0) + float((xs * wgt).sum() / wgt.sum())
        rowc = float((ys * wgt).sum() / wgt.sum())
        xi = int(np.clip(round(cx), 0, W - 1))
        cy = (float(edge[xi]) - bh + rowc if outward > 0
              else float(edge[xi]) - 2 + rowc)
        out.append(dict(cx=cx, cy=cy, w=float(x1 - x0 + 1),
                        h=float(ys.max() - ys.min() + 1),
                        strength=float(strip[:, x0:x1 + 1][sel].max())))
    return sorted(out, key=lambda r: r['cx'])


def blob_detect(img, cfg=None):
    """Class B detector: pads on the package's two long edges."""
    cfg = dict(CFG, **(cfg or {}))
    empty = dict(pads={}, steps=[], mask=None, note=None, edges=None,
                 pitch=None, box=None)
    bm = body_mask(img, cfg)
    if bm is None:
        empty['note'] = 'no dark body found'
        return empty
    mask, ry0, ry1 = bm
    top, bot, okc = body_edges(mask, cfg)
    if top is None:
        empty['mask'] = mask
        empty['note'] = 'body too narrow'
        return empty
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    V = hsv[:, :, 2].astype(np.float32)
    Sat = hsv[:, :, 1].astype(np.float32)
    lh = float(np.median(bot[okc] - top[okc]))
    if lh < float(cfg['body_min_h']):
        empty['mask'] = mask
        empty['note'] = 'body too thin'
        return empty
    bh = float(np.clip(cfg['strip_frac'] * lh, 6.0, 0.60 * lh))
    pads = {'top': _strip_blobs(V, Sat, top, okc, int(round(bh)), +1, cfg),
            'bottom': _strip_blobs(V, Sat, bot, okc, int(round(bh)), -1, cfg)}

    xs = sorted(q['cx'] for v in pads.values() for q in v)
    pitch = None
    if len(xs) >= 3:
        g = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
        g = [t for t in g if t > 1.0]
        pitch = float(np.median(g)) if g else None

    vis = img.copy()
    ov = vis.copy()
    ov[mask > 0] = (60, 120, 40)
    vis = cv2.addWeighted(vis, 0.55, ov, 0.45, 0)
    cv2.polylines(vis, [np.stack([np.arange(W), top], 1).astype(np.int32)],
                  False, (0, 255, 255), 1)
    cv2.polylines(vis, [np.stack([np.arange(W), bot], 1).astype(np.int32)],
                  False, (0, 255, 255), 1)
    steps = [('1 body', vis)]
    pr = img.copy()
    for k, col in (('top', (0, 0, 255)), ('bottom', (255, 0, 0))):
        for q in pads.get(k, []):
            cv2.circle(pr, (int(round(q['cx'])), int(round(q['cy']))), 4, col, -1)
    steps.append(('2 pads', pr))
    cnts, _ = cv2.findContours((mask * 255).astype(np.uint8),
                               cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    box = None
    if cnts:
        box = cv2.boxPoints(cv2.minAreaRect(
            max(cnts, key=cv2.contourArea))).astype(np.float32)
    return dict(pads=pads, steps=steps, mask=mask, note=None, pitch=pitch,
                box=box, edges=dict(top=top, bottom=bot),
                body=dict(x0=float(np.percentile(np.where(okc)[0], 5)),
                          x1=float(np.percentile(np.where(okc)[0], 95)),
                          y0=float(np.percentile(top[okc], 5)),
                          y1=float(np.percentile(bot[okc], 95))))


def blob_pins(res):
    """class-B pads -> original-frame points (unordered)"""
    pts = []
    for k in ('top', 'bottom'):
        for q in res['pads'].get(k, []):
            pts.append(dict(x=float(q['cx']), y=float(q['cy']),
                            key=float(q['cx']), side=k, pred=False))
    return pts


def blob_score(res, cfg):
    n = sum(len(v) for v in res['pads'].values())
    if n < int(cfg['blob_min_total']):
        return 0.0
    st = [q['strength'] for v in res['pads'].values() for q in v]
    return float(n) * float(np.mean(st)) if st else 0.0


def blob_gap_ratio(res, cfg):
    """longest hole between neighbouring pad groups / median gap, per edge.
    A regular pin row ~1.0-1.6; a body whose pads only sit at the two ends
    (class B) is far larger."""
    ratios, n_tot = [], 0
    for k, v in res['pads'].items():
        n_tot += len(v)
        if len(v) < 3:
            continue
        xs = sorted(q['cx'] for q in v)
        gaps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
        gaps = [g for g in gaps if g > 1.0]
        if len(gaps) < 2:
            continue
        med = float(np.median(gaps))
        if med <= 0:
            continue
        ratios.append(float(max(gaps)) / med)
    if not ratios:
        return 0.0, n_tot
    return float(max(ratios)), n_tot


# ------------------------------------------------------- class C: quad (QFP)
#  A QFP / LQFP carries pins on all FOUR edges, so neither class A (two opposed
#  pad columns) nor class B (two long edges) can describe it -- class A happily
#  reports a 2-column answer for a 4-sided part and misses half the pins.
#
#  Class C:
#    1. find the package body -- the one large region that is BOTH dark and
#       desaturated.  The green solder mask is dark too, but it is strongly
#       saturated, and *that* is what separates the two (a plain gray threshold
#       cannot: on this part the body is 48 gray and the board 71-124);
#    2. take its minimum-area rectangle, order the four corners;
#    3. walk each edge outward and sample a thin band hugging it, using
#       cv2.remap -- so a tilted package is "unrolled" along the band and no
#       image rotation is needed;
#    4. count the pad peaks on each edge and keep the four that agree.  A QFP is
#       symmetric, so "three of four edges carry the same number of pads" is a
#       very strong signature -- no dual-row part can fake it.
#  Like class B nothing is ever extrapolated: only pads actually visible in the
#  band are reported.
QUAD_ORDER = ('left', 'bottom', 'right', 'top')


def _body_gates(g, S, cfg):
    """(gray ceiling, saturation ceiling) that isolate the moulded body.

    The body is not merely dark -- the solder mask is dark too, and on the QFP
    sample above the body is 48 gray while the board is 71-124, so no fixed cut
    works.  What does work is that the body is *uniform*: it owns the dominant
    mode of the gray histogram, the board owns the next one, and the valley
    between the two is the body/board cut.  A whole-frame Otsu instead lands in
    the middle of the pin population (113 here) and swallows half the board.
    Saturation is the second, independent cue -- the mask is green (S 84-176)
    where the moulding is not (S 28-36)."""
    h = np.bincount(g.ravel(), minlength=256).astype(np.float32)
    hs = cv2.GaussianBlur(h.reshape(1, -1), (0, 0), 2.0).ravel()
    m1 = int(np.argmax(hs))
    pk = [i for i in range(2, 254)
          if hs[i] >= hs[i - 1] and hs[i] > hs[i + 1] and i > m1 + 6]
    g_gate = float(m1 + int(np.argmin(hs[m1:pk[0] + 1]))) if pk else m1 * 1.4
    s_gate = float(cfg['quad_sat']) * float(np.percentile(S, 95))
    return g_gate, s_gate


def _body_edge(prof, thr, min_band, min_gap):
    """first package edge measured inwards from the start of `prof`.

    Scan in from the frame border: cross the bare board, cross the pin band,
    stop at the first sustained quiet stretch -- that stretch is the package.
    Returns (edge_index, pin_band_thickness).  A band thinner than `min_band`
    means we only clipped a bright speck (or the pins on that edge are too dim
    to see at all), and the edge is reported as unknown rather than guessed."""
    on = prof > thr
    n = on.size
    if not on.any():
        return None, 0
    i = int(np.argmax(on))
    j = i
    while j + 1 < n and on[j + 1]:
        j += 1
    band = j - i + 1
    if band < min_band:
        return None, 0
    k = j + 1
    while k < n:
        if not on[k]:
            e = k
            while e + 1 < n and not on[e + 1]:
                e += 1
            if e - k + 1 >= min_gap:
                return k, band
            k = e + 1
        else:
            k += 1
    return None, 0


def quad_body(img, cfg):
    """the dark, uniform, near-square package body -> dict or None"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    S = hsv[:, :, 1].astype(np.float32)
    H, W = S.shape
    if min(H, W) < 48:
        return None
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g_gate, s_gate = _body_gates(g, S, cfg)

    # (1) sanity: there has to be a large, dark, *desaturated* blob, and it has
    #     to be roughly square.  This is what keeps the router from reaching for
    #     class C on a dual-row part or on a frame full of loose components.
    raw = ((g <= g_gate) & (S <= s_gate)).astype(np.uint8)
    raw = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, ek(odd(0.05 * min(H, W), 5)))
    raw = cv2.morphologyEx(raw, cv2.MORPH_OPEN, ek(3))
    n, lab, st, _ = cv2.connectedComponentsWithStats(raw, 8)
    if n < 2:
        return None
    i = 1 + int(np.argmax(st[1:, 4]))
    if float(st[i, 4]) < float(cfg['quad_min_frac']) * H * W:
        return None
    m = (lab == i).astype(np.uint8)
    cnt = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    if not cnt:
        return None
    (bcx, bcy), (bw, bh), b_ang = cv2.minAreaRect(max(cnt, key=cv2.contourArea))
    if bw < 12 or bh < 12:
        return None
    if min(bw, bh) / max(bw, bh) < float(cfg['quad_ar']):
        return None

    # (2) the real edges come from the pin brightness, not from that blob: the
    #     blob bleeds into the pin band (pins are desaturated too), so its box
    #     is ~30 px too generous on every side.  Profile the pin mask instead
    #     and read each edge off the first quiet stretch.
    lvl = g_gate + float(cfg['quad_pin_rel']) * (float(np.percentile(g, 99))
                                                 - g_gate)
    pin = (g > lvl).astype(np.float32)
    k = odd(max(3, int(0.012 * min(H, W))))
    rs = cv2.blur(pin.mean(1).reshape(-1, 1), (1, k)).ravel()
    cs = cv2.blur(pin.mean(0).reshape(1, -1), (k, 1)).ravel()
    thr = float(cfg['quad_prof'])
    mb = max(4, int(round(float(cfg['quad_band']) * min(H, W))))
    mg = max(4, int(round(float(cfg['quad_gap']) * min(H, W))))
    y0, _ = _body_edge(rs, thr, mb, mg)
    yb, _ = _body_edge(rs[::-1], thr, mb, mg)
    x0, _ = _body_edge(cs, thr, mb, mg)
    xb, _ = _body_edge(cs[::-1], thr, mb, mg)
    y1 = None if yb is None else H - 1 - yb
    x1 = None if xb is None else W - 1 - xb

    if x0 is None and x1 is None:
        x0, x1 = float(st[i, 0]), float(st[i, 0] + st[i, 2])
    if y0 is None:
        y0 = float(st[i, 1])
    y0 = float(y0)
    # a QFP moulding is square, so one trustworthy axis sizes the other.  That
    # is not a fudge: it is exactly what repairs the common failure where the
    # pins on one edge are too dim for the profile (this sample's whole bottom
    # edge reads 27-46 px of brightness where the top reads 118-185).
    if x0 is not None and x1 is not None:
        w = float(x1 - x0)
        if y1 is None:
            y1 = y0 + w
    elif y1 is not None:
        w = float(y1 - y0)
        x0, x1 = float(st[i, 0]), float(st[i, 0] + st[i, 2])
    else:
        return None
    if x0 is None:
        x0 = float(st[i, 0])
    if x1 is None:
        x1 = float(st[i, 0] + st[i, 2])
    if y1 is None:
        y1 = y0 + (x1 - x0)
    w, h = float(x1 - x0), float(y1 - y0)
    if min(w, h) < 12:
        return None

    cx, cy = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
    box = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float32)
    return dict(cx=cx, cy=cy, w=w, h=h, ang=0.0,
                square=float(min(w, h) / max(w, h)),
                area=float(st[i, 4]), mask=m, box=box,
                mask_box=(float(st[i, 0]), float(st[i, 1]),
                          float(st[i, 0] + st[i, 2]), float(st[i, 1] + st[i, 3])),
                min_area=(float(bcx), float(bcy), float(bw), float(bh)))


def _edge_strip(g, p0, p1, depth):
    """band of (depth x L) sampled just outside the p0 -> p1 edge.

    tl -> tr -> br -> bl runs clockwise in image coordinates, so the outward
    normal of an edge is (dy, -dx).  Sampling with remap means a tilted package
    needs no image rotation at all."""
    p0 = np.asarray(p0, np.float32)
    p1 = np.asarray(p1, np.float32)
    v = p1 - p0
    L = int(round(float(np.hypot(v[0], v[1])))) + 1
    depth = max(3, int(depth))
    if L < 16:
        return None, None, None
    nrm = max(1e-6, float(np.hypot(v[0], v[1])))
    nx, ny = v[1] / nrm, -v[0] / nrm
    t = np.linspace(0.0, 1.0, L, dtype=np.float32)
    bx = p0[0] + t * v[0]
    by = p0[1] + t * v[1]
    dd = np.arange(depth, dtype=np.float32)
    mx = bx[None, :] + dd[:, None] * nx
    my = by[None, :] + dd[:, None] * ny
    strip = cv2.remap(g, mx, my, cv2.INTER_LINEAR,
                      borderMode=cv2.BORDER_REPLICATE)
    return strip, mx, my


def _trim_lattice(pks, L, cfg):
    """keep only the longest run of pads whose spacing matches the band median.

    A band is sampled along the *whole* edge, so it also covers the two package
    corners -- and there the neighbouring edge's outermost pads poke into it and
    show up as one or two extra peaks sitting 0.04-0.2 band-lengths from the end
    (this sample: one at t=0.005 on the top edge, two at t=0.02/0.06 on the
    left).  They are not on the pad lattice, so drop them."""
    if len(pks) < 4:
        return pks
    t = np.asarray(pks, float) / max(1.0, float(L) - 1.0)
    gp = np.diff(t)
    med = float(np.median(gp))
    if med <= 1e-6:
        return pks
    ok = np.abs(gp - med) <= float(cfg['quad_jump']) * med
    bi = bj = -1
    i = 0
    while i < ok.size:
        if ok[i]:
            j = i
            while j + 1 < ok.size and ok[j + 1]:
                j += 1
            if bi < 0 or (j - i) > (bj - bi):
                bi, bj = i, j
            i = j + 1
        else:
            i += 1
    if bi < 0:
        return pks
    return list(pks[bi:bj + 2])


def _strip_signal(strip, cfg):
    """(bright profile, detrended profile) along a band"""
    k = max(1, int(float(cfg['band_frac']) * strip.shape[0]))
    hi = np.sort(strip, axis=0)[-k:].mean(0)
    base = cv2.blur(hi.reshape(-1, 1), (1, int(cfg['quad_detrend']))).ravel()
    sm = cv2.blur((hi - base).reshape(-1, 1), (1, 9)).ravel()
    return hi, sm


def quad_side(g, p0, p1, cfg, name, depth=None):
    """pads sitting just outside one edge -> dict or None"""
    if depth is None:
        co, _, _ = _edge_strip(g, p0, p1, int(cfg['quad_probe']))
        if co is None:
            return None
        _, sm0 = _strip_signal(co, cfg)
        per = dominant_period(sm0)
        if per is None:
            return None
        depth = int(round(np.clip(float(cfg['quad_depth']) * float(per[0]),
                                  float(cfg['quad_depth_min']),
                                  float(cfg['quad_depth_max']))))
    co, mx, my = _edge_strip(g, p0, p1, depth)
    if co is None:
        return None
    hi, sm = _strip_signal(co, cfg)
    per = dominant_period(sm)
    if per is None:
        return None
    lat = peak_lattice(sm, float(per[0]), cfg, autocorr(sm))
    if lat is None:
        return None
    pks = _trim_lattice(lat[0], co.shape[1], cfg)
    if len(pks) < 3:
        return None
    gaps = np.diff(np.asarray(pks, float))
    gaps = gaps[gaps > 0]
    cv_ = float(np.std(gaps) / np.mean(gaps)) if gaps.size > 1 else 9.9
    L = co.shape[1]
    # ---- 点的法线位置：单独用「覆盖整根引脚」的窗口重采样后取亮度质心。
    #   检测（峰位/点数/规则性）仍走上面的 depth，所以点数与触发判据完全不变，
    #   只把点从「引脚内半段」挪回引脚中心（见 CFG['quad_pin_depth'] 的说明）。
    pin_pitch = float(np.mean(gaps)) if gaps.size else float(per[0])
    probe = int(np.clip(round(float(cfg['quad_pin_depth']) * pin_pitch),
                        depth, int(cfg['quad_depth_max'])))
    co2, mx2, my2 = _edge_strip(g, p0, p1, probe)
    if co2 is None:
        co2, mx2, my2 = co, mx, my
    sub2 = co2 - float(np.median(co2))

    # ---- 点的切向位置：把峰位吸附到**真实亮带中心**
    #  为什么必须单独做：检测用的 sm = blur(detrend(hi), 9) —— 那个 9 是**固定像素**
    #  的平滑窗。单芯片特写里 pitch 几十像素，9px 窗 ≪ pitch，无影响；但整板上
    #  pitch 只有 ~5px，9px 窗跨了 ~1.9 个周期，峰位被整体推后 ~0.4*pitch ≈ 2px
    #  —— 正好落进引脚间隔（用户实测「点标在引脚间隔上」就是这个）。
    #  这里用**未平滑**的亮度剖面把每个峰拉回最近的真实亮带中心：峰数不变、
    #  点数不变、触发判据完全不变，只改点的位置。
    hi2, _ = _strip_signal(co2, cfg)
    hb = hi2 - cv2.blur(hi2.reshape(-1, 1),
                        (1, int(cfg['quad_detrend']))).ravel()
    half = max(1, int(round(0.5 * pin_pitch)))
    snapped = []
    for p in pks:
        a = max(0, int(p) - half)
        b = min(int(hb.size) - 1, int(p) + half)
        j = a + int(np.argmax(hb[a:b + 1])) if b >= a else int(p)
        if snapped and j <= snapped[-1]:      # 保持严格递增，避免两点吸到同一颗
            j = snapped[-1] + 1
        snapped.append(int(np.clip(j, 0, co2.shape[1] - 1)))

    pins = []
    for k, p in enumerate(pks):
        # 点的法线位置：取「亮带」的中段中点，而不是亮度加权质心。
        # 加权质心会偏向引脚外端（焊盘外缘最亮），实测偏心 2~3px；中点更稳。
        #
        # 注意：必须取**一段连续**亮带的中点，不能取 first..last。
        # QFP 引脚沿法线常有两个亮瓣：①引脚主体 ②靠近本体的根部高光；
        # 两者之间有一段暗腰。first..last 的中点正好落在暗腰上
        # （实测 ST 16C554 左列：点处灰度 100，而同一根引脚的亮带是 205）。
        # 所以在「阈值以上」的各段里挑**面积最大的一段**，再取它的中点。
        q = snapped[k]
        col = np.clip(sub2[:, q], 0, None)
        di = None
        if col.size and col.max() > 0:
            msk = col >= 0.35 * float(col.max())
            runs = []
            i, n_col = 0, int(msk.size)
            while i < n_col:
                if msk[i]:
                    j = i
                    while j + 1 < n_col and msk[j + 1]:
                        j += 1
                    runs.append((i, j))
                    i = j + 1
                else:
                    i += 1
            if runs:
                a, b = max(runs, key=lambda r: float(col[r[0]:r[1] + 1].sum()))
                di = int(round(0.5 * (a + b)))
        if di is None:
            di = co2.shape[0] // 2
        di = int(np.clip(di, 0, co2.shape[0] - 1))
        pins.append(dict(x=float(mx2[di, q]), y=float(my2[di, q]),
                         t=float(p) / max(1, L - 1),
                         val=float(hi[int(p)]), pred=False))
    return dict(side=name, pins=pins, depth=int(depth),
                pitch=float(np.mean(gaps)) if gaps.size else 0.0,
                cv=cv_, band=L, val=float(np.percentile(hi, 95)))


def quad_pins(res):
    """flatten the four edges into one counter-clockwise pin list.

    Pin 1 of a QFP sits at the top-left corner and numbering runs counter-
    clockwise, i.e. down the left edge, left-to-right along the bottom, up the
    right edge, right-to-left along the top -- which is exactly 't descending'
    on every edge of the clockwise tl->tr->br->bl chain."""
    out = []
    for name in QUAD_ORDER:
        v = (res.get('sides') or {}).get(name)
        if not v:
            continue
        for q in sorted(v['pins'], key=lambda q: -q['t']):
            out.append(dict(x=q['x'], y=q['y'], pred=False, side=name))
    return out


def quad_detect(img, cfg=None):
    """Class C detector: pads on all four edges of a square package."""
    cfg = dict(CFG, **(cfg or {}))
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    cfg['_ref'] = float(np.percentile(g, 99.5))
    steps = [('1 input', g)]
    empty = dict(pads={}, steps=steps, note=None, box=None, pitch=None,
                 sides={}, cands=[], body=None)
    b = quad_body(img, cfg)
    if b is None:
        empty['note'] = 'no square dark body'
        return empty

    vis = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
    cv2.polylines(vis, [b['box'].reshape(-1, 1, 2).astype(np.int32)], True,
                  (0, 255, 0), 2)
    steps.append(('2 body', vis))

    tl, tr, br, bl = b['box']
    edges = (('top', tl, tr), ('right', tr, br),
             ('bottom', br, bl), ('left', bl, tl))
    depth, sides = None, {}
    for name, p0, p1 in edges:
        r = quad_side(g, p0, p1, cfg, name, depth)
        if r is None:
            continue
        if depth is None:
            depth = r['depth']
        sides[name] = r
    empty['body'] = b
    empty['sides'] = sides
    if len(sides) < 3:
        empty['note'] = 'only %d of 4 edges show a pad lattice' % len(sides)
        return empty

    cnt = [len(v['pins']) for v in sides.values()]
    med = int(round(float(np.median(cnt))))
    # a QFP carries the same count on every edge: re-read an outvoted edge with
    # the shared band depth before giving up on it
    for name, p0, p1 in edges:
        v = sides.get(name)
        if v is None or abs(len(v['pins']) - med) <= 1:
            continue
        r = quad_side(g, p0, p1, cfg, name, depth)
        if r is not None:
            sides[name] = r

    vis = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
    cv2.polylines(vis, [b['box'].reshape(-1, 1, 2).astype(np.int32)], True,
                  (0, 255, 0), 2)
    for v in sides.values():
        for q in v['pins']:
            cv2.circle(vis, (int(round(q['x'])), int(round(q['y']))), 5,
                       (0, 0, 255), 2)
    steps.append(('3 pads', vis))

    pitches = [v['pitch'] for v in sides.values() if v['pitch']]
    return dict(pads={k: v['pins'] for k, v in sides.items()}, steps=steps,
                note=None, box=b['box'], body=b,
                pitch=float(np.median(pitches)) if pitches else 0.0,
                sides=sides, cands=[])


def quad_ok(res, cfg=None):
    """is this a genuine four-edge package rather than a lucky dual-row fit?"""
    cfg = dict(CFG, **(cfg or {}))
    s = res.get('sides') or {}
    if len(s) < 3:
        return False
    cnt = [len(v['pins']) for v in s.values()]
    if min(cnt) < int(cfg['quad_min_pins']):
        return False
    if max(cnt) - min(cnt) > int(cfg['quad_tol']):
        return False
    if max(v['cv'] for v in s.values()) > float(cfg['quad_cv']):
        return False
    return True


# ------------------------------------------------------------------ dual-row
#  「两条对边」也要接受（SOP / SOIC / 双列直插 等）
#  ---------------------------------------------------------------
#  为什么必须补这一条（实测，别看轻）：
#  quad_detect 本质是「沿本体的四条边各取一条引脚带，在带上找规则点阵」。
#  对**双列**封装，它照样能测出 left / right 两条边，而且比 class A 的
#  「两列周期栅格 + 本体定位」准得多 —— 因为它是贴着封装本体边缘采样的，
#  周期与起始相位都由那一条带自己决定，不会横跨整幅图去凑栅格。
#
#  实测这颗模糊 SOP-8（486x368，用户手画的 8 个蓝框为真值）：
#      quad_detect   -> left 4 / right 4 = 8 点，**8/8 全落在框内**
#      class A grid  -> 24 点（沿两条水平行铺满整幅图），只有 12/24 在框内
#  以前 `quad_detect` 里那句 `if len(sides) < 3: return empty` 把这两条边的
#  结果直接扔了，路由就掉到 class A —— 把一颗本来 8/9 正确的芯片变成 24 点。
#  所以这里把「恰好两条对边、点数相等、间距规则」当成合法结果接住。
#
#  卡得很死（宁可不接）：必须**恰好两条**且**互为对边**，必须有暗色本体，
#  两边点数必须相等且不小于 quad_min_pins，cv 必须过 quad_cv。
#  不满足就照旧往下走 class B / class A，行为与改动前完全一致。
def _dual_sides(res, cfg):
    """恰好两条互为对边的引脚带 -> (name_a, name_b) 或 None。"""
    s = res.get('sides') or {}
    if len(s) != 2:
        return None
    for a, b in (('left', 'right'), ('top', 'bottom')):
        if set(s) == {a, b}:
            na = len(s[a].get('pins') or [])
            nb = len(s[b].get('pins') or [])
            if na < int(cfg['quad_min_pins']) or nb < int(cfg['quad_min_pins']):
                return None
            if na != nb:
                return None
            if max(float(s[a].get('cv', 9.9)),
                   float(s[b].get('cv', 9.9))) > float(cfg['quad_cv']):
                return None
            return (a, b)
    return None


def dual_ok(res, cfg=None):
    """是「双列封装」而不是噪声吗（恰好两条对边、点数相等、间距规则）。"""
    cfg = dict(CFG, **(cfg or {}))
    if res.get('body') is None:          # 没有暗色本体 -> 不是芯片，不接
        return False
    return _dual_sides(res, cfg) is not None


# ------------------------------------------------- dual-row without a body
#  为什么还要这一条（实测，别删）：
#  class C 的双列路径（上面 dual_ok）**依赖 quad_body**。可 quad_body 要求
#  「一团足够大、且近方形的暗色本体」。模糊的小图 / 低对比图上这条会直接
#  收不到本体，于是 quad 与 dual 两路一起失效，路由掉到 class A：
#      实测这颗模糊 SOIC-10（208x406，上下各 5 脚）：
#          quad_body  -> None（暗团块占 3.4% < quad_min_frac 0.06，ar 0.32 < 0.72）
#          class A    -> 11 点（上排 5 个对 + 本体中间 6 个假点，下排 5 个全漏）
#      而把两条边交给 quad_side 后：top 5 / bottom 5，**10/10 全落在用户框内**。
#  所以这里不再找本体，改为**直接定位两条引脚亮带**：芯片的引脚是金属（亮且
#  低饱和），双列封装在「垂直于引脚」的那条轴上一定表现为
#  「贴边一条亮带 -> 中间一长段暗（本体）-> 对边一条亮带」。
#  两条亮带的内缘就是两条对边的位置，剩下的完全交给 quad_side —— 与 class C
#  走的是同一条点阵检测链路，只是边的来源不同。
def _profile_runs(prof, thr, min_len):
    """一维剖面里 >= thr 的连续段（长度 >= min_len）-> [(起, 止), ...]"""
    on = prof >= thr
    n = on.size
    out = []
    i = 0
    while i < n:
        if on[i]:
            j = i
            while j + 1 < n and on[j + 1]:
                j += 1
            if j - i + 1 >= min_len:
                out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def _band_pair(prof, L, cfg):
    """垂直于引脚那条轴上的两条亮带 -> (贴近起点的带, 贴近终点的带) 或 None。

    要求：两条带都贴着图的两端、中间留出足够长的暗区（本体），否则不算
    「双列封装打满整幅图」—— 这条正是防止它在整板图上乱开火。"""
    runs = _profile_runs(prof, float(cfg['dual_run']),
                         max(3, int(round(float(cfg['dual_min_len']) * L))))
    if len(runs) < 2:
        return None
    a, b = runs[0], runs[-1]
    if a[0] > float(cfg['dual_border']) * L:
        return None
    if b[1] < (1.0 - float(cfg['dual_border'])) * L - 1:
        return None
    if (b[0] - a[1]) < float(cfg['dual_body']) * L:
        return None
    return a, b


def _side_straight(pins, p0, p1, pitch, cfg):
    """一条边上的点是否共线（法向偏移相对中位的最大偏离 <= dual_straight*pitch）。"""
    if not pins:
        return False
    v = p1 - p0
    L = float(np.hypot(v[0], v[1]))
    if L < 1e-6:
        return False
    nx, ny = v[1] / L, -v[0] / L
    off = np.array([(q['x'] - p0[0]) * nx + (q['y'] - p0[1]) * ny
                    for q in pins], np.float64)
    return bool(np.max(np.abs(off - np.median(off)))
                <= float(cfg['dual_straight']) * float(pitch))


def _grid_split(best, cfg):
    """class A 的两列点数是否**互相矛盾**（不等）—— 不等 = 它在瞎铺栅格。

    best 是 detect_best 的返回值（None 表示 class A 压根没找到栅格）。"""
    if best is None:
        return True
    res = best[3]
    L, R = res.get('L'), res.get('R')
    nl = len(L['peaks']) if L and L.get('peaks') is not None else 0
    nr = len(R['peaks']) if R and R.get('peaks') is not None else 0
    return nl != nr


def dual_band_detect(img, cfg=None):
    """Class C'': 无本体、直接从引脚亮带定两条对边。"""
    cfg = dict(CFG, **(cfg or {}))
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    cfg['_ref'] = float(np.percentile(g, 99.5))
    H, W = g.shape
    empty = dict(pads={}, steps=[], note=None, box=None, pitch=None,
                 sides={}, cands=[], body=None)
    if min(H, W) < 32:
        empty['note'] = 'too small'
        return empty

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    S = hsv[:, :, 1].astype(np.float32)
    s_cap = float(cfg['dual_sat']) * float(np.percentile(S, 99.0))
    lvl = float(np.percentile(g, float(cfg['dual_lvl'])))
    M = ((g >= lvl) & (S <= s_cap)).astype(np.float32)
    if M.sum() < 0.02 * H * W:
        empty['note'] = 'no metal'
        return empty

    # 两条候选轴：行带（引脚朝上/下）与列带（引脚朝左/右）。
    # 端点顺序按 quad_detect 的顺时针约定给，法线才会朝**外**（指向引脚带）。
    cands = []
    rp = _band_pair(M.mean(1), H, cfg)
    if rp is not None:
        (t0, t1), (b0, b1) = rp
        cands.append((('top', np.array([0.0, t1 + 1.0]),
                       np.array([W - 1.0, t1 + 1.0])),
                      ('bottom', np.array([W - 1.0, b0 - 1.0]),
                       np.array([0.0, b0 - 1.0]))))
    cp = _band_pair(M.mean(0), W, cfg)
    if cp is not None:
        (l0, l1), (r0, r1) = cp
        cands.append((('left', np.array([l1 + 1.0, H - 1.0]),
                       np.array([l1 + 1.0, 0.0])),
                      ('right', np.array([r0 - 1.0, 0.0]),
                       np.array([r0 - 1.0, H - 1.0]))))

    for (na, pa0, pa1), (nb, pb0, pb1) in cands:
        depth, sides = None, {}
        for nm, p0, p1 in ((na, pa0, pa1), (nb, pb0, pb1)):
            r = quad_side(g, p0, p1, cfg, nm, depth)
            if r is None:
                continue
            if depth is None:
                depth = r['depth']
            sides[nm] = r
        if len(sides) != 2:
            continue
        # 同一条边上的点必须共线（否则是杂点，见 CFG['dual_straight']）
        if not all(_side_straight(v['pins'], p0, p1, v['pitch'], cfg)
                   for (nm, p0, p1), v in (((na, pa0, pa1), sides.get(na)),
                                           ((nb, pb0, pb1), sides.get(nb)))):
            continue
        xs = [q['x'] for v in sides.values() for q in v['pins']]
        ys = [q['y'] for v in sides.values() for q in v['pins']]
        box = np.array([[min(xs), min(ys)], [max(xs), min(ys)],
                        [max(xs), max(ys)], [min(xs), max(ys)]], np.float32)
        pitches = [v['pitch'] for v in sides.values() if v['pitch']]
        res = dict(pads={k: v['pins'] for k, v in sides.items()},
                   steps=[], note='bands only, no body', box=box,
                   body=dict(box=box), sides=sides, cands=[],
                   pitch=float(np.median(pitches)) if pitches else 0.0)
        if _dual_sides(res, cfg) is not None:      # 恰好两条对边、点数相等、规则
            return res
    empty['note'] = 'no dual band pair'
    return empty


# --------------------------------------------------------- library entry
#  PinTool (and anything else) should not have to re-implement the routing that
#  main() performs, nor worry about the three classes using different frames
#  (class A reports coordinates in the *upscaled* frame, B and C in the input
#  frame).  detect_auto() is the one call that hides all of that.
def detect_auto(img, cfg=None):
    """Route one image through the best of class A / B / C.

    Returns (pins, mode, meta):
        pins  -- unordered list of dict(x, y, side, pred) in the *input image*
                 frame; side is one of left / bottom / right / top.
        mode  -- 'quad' | 'blob' | 'grid' | 'none'
        meta  -- dict(mode, pitch, note, box, n_per_edge / orient / scale ...)

    The decision is byte-for-byte the one main() makes: class C first (a
    four-edge signature cannot be faked by a dual-row part), then class B, then
    class A -- the same order, the same gates.
    """
    cfg = dict(CFG, **(cfg or {}))
    H, W = img.shape[:2]

    # ---- class C: four edges carry the pads (QFP / LQFP)
    q = quad_detect(img, cfg)
    if quad_ok(q, cfg):
        pts = []
        for name in QUAD_ORDER:
            for p in (q.get('sides') or {}).get(name, {}).get('pins', []):
                pts.append(dict(x=float(p['x']), y=float(p['y']),
                                side=name, pred=False))
        ne = {k: len(v['pins']) for k, v in q['sides'].items()}
        return pts, 'quad', dict(mode='quad', pitch=q.get('pitch'),
                                 note=None, box=q.get('box'),
                                 n_per_edge=ne)

    # ---- class C': 两条对边（SOP / SOIC 这类双列封装）
    #  quad_detect 对双列封装同样给出很准的 left/right 两条边，只是以前被
    #  「必须 ≥3 条边」的门槛扔掉了（见 dual_ok 上方注释）。这里接住它，
    #  并且**必须排在 class A 之前** —— class A 在模糊小图上会把整幅图铺成
    #  一片假栅格（实测 8 脚的片子报 24 点）。
    if q.get('body') is not None and _dual_sides(q, cfg):
        pts = []
        for name in QUAD_ORDER:
            for p in (q.get('sides') or {}).get(name, {}).get('pins', []):
                pts.append(dict(x=float(p['x']), y=float(p['y']),
                                side=name, pred=False))
        ne = {k: len(v['pins']) for k, v in q['sides'].items()}
        return pts, 'dual', dict(mode='dual', pitch=q.get('pitch'),
                                 note='two opposite edges only',
                                 box=q.get('box') or q['body'].get('box'),
                                 n_per_edge=ne)

    # ---- class A / B, exactly main()'s decision
    best = detect_best(img, cfg, orients=('h', 'v'))
    grid_score = 0.0 if best is None else float(best[0])
    grid_irregular = (best is None) or (grid_score <= 0.0)
    if best is not None:
        L, R = best[3].get('L'), best[3].get('R')
        if L and R and (float(L['cv']) > float(cfg['cv_gate']) or
                        float(R['cv']) > float(cfg['cv_gate'])):
            grid_irregular = True

    # ---- class C'': 双列但本体不成方形 / 太糊，quad_body 收不到时的兜底。
    #  闸门卡得很死：**只有 class A 自己两列点数互相矛盾（或压根没结果）时**才让
    #  dualband 接管 —— 「两列不等」就是 class A 在这张图上瞎铺栅格的强信号。
    #  实测（模糊 SOIC-10）：class A 报 L=4 / R=7 两列不等，11 个点里只有 5 个真在
    #  引脚上；而正常的双列图两列必然相等（test2 24=24、test3 27=27）。
    #  没有这道闸门，dualband 会在 OTF 生产小芯片上大面积抢答（49/128 颗）。
    q2 = dual_band_detect(img, cfg)
    if _dual_sides(q2, cfg) is not None and _grid_split(best, cfg):
        pts = []
        for name in QUAD_ORDER:
            for p in (q2.get('sides') or {}).get(name, {}).get('pins', []):
                pts.append(dict(x=float(p['x']), y=float(p['y']),
                                side=name, pred=False))
        ne = {k: len(v['pins']) for k, v in q2['sides'].items()}
        return pts, 'dualband', dict(mode='dualband', pitch=q2.get('pitch'),
                                     note=q2.get('note'),
                                     box=q2.get('box'), n_per_edge=ne)

    bres = blob_detect(img, cfg)
    gap_ratio, n_blob = blob_gap_ratio(bres, cfg)
    # ---- class B 的「两排必须等长」自检（2026-10-12）
    #  class B 只在**本体的两条长边**上找焊盘，所以它给出的永远是「上排 + 下排」。
    #  而双列封装两条物理排的脚数**必须相等**（跟亮度/模糊无关）。两排都测到点却
    #  数目不等，就是一条自相矛盾的结果 —— 它不该去覆盖别的分支。
    #  实测（V5 整板紧贴框 282 颗，13 块板）：
    #    · 走 blob 的 37 颗里，有 15 颗两排不等（3 点的报 2/1、4 点的报 1/3…）；
    #    · 这 15 颗里有 11 颗主算法 pin_detect 的结构反而是成立的
    #      （典型 V5.(23) T_9：主算法 8（4/4 全对）→ blob 3（2/1）被采用）。
    #  注意**不**把「只有一排有脚」也否掉：那是单排（O）封装的正常形态
    #  （实测 V5.(43) O_2 就是上排 5 / 下排 0），否掉反而会把好结果扔掉。
    _bp = bres.get('pads') or {}
    n_top = len(_bp.get('top') or [])
    n_bot = len(_bp.get('bottom') or [])
    blob_bad_rows = (n_top > 0 and n_bot > 0 and n_top != n_bot)

    use_blob = (bres.get('box') is not None
                and n_blob >= int(cfg['blob_min_total'])
                and not blob_bad_rows
                and (grid_irregular or gap_ratio >= float(cfg['gap_ratio_gate'])))
    if use_blob:
        pts = [dict(x=float(p['x']), y=float(p['y']), side=p['side'],
                    pred=bool(p['pred'])) for p in blob_pins(bres)]
        return pts, 'blob', dict(mode='blob', pitch=bres.get('pitch'),
                                 note=bres.get('note'), box=bres.get('box'))

    # ---- 唯一可信的候选被上面否掉时，不要顺手把 class A 的假栅格发出去。
    #  没有这条，blob 一被否就会掉进 class A；而 class A 在小裁图上会铺出
    #  36 / 111 这种荒唐栅格（实测 V5.(23) T_30 = 36、V5.(53) F_5 = 111，
    #  主算法分别是 18、5）。**宁可不给**：调用方（PinTool 的 rerun_chip）
    #  见新算法没出点就会保留 pin_detect 的结果 —— 回到「这颗没重跑」，
    #  天然不会比主算法差。class A 若确实整齐（两列 cv 都 ≤ cv_gate），照常放行。
    if best is None or (blob_bad_rows and grid_irregular):
        return [], 'none', dict(mode='none',
                                note=('blob self-contradictory' if blob_bad_rows
                                      else 'no pad lattice'),
                                pitch=None, box=None)

    score, orient, s, res, _ = best
    H0 = H * s                                  # upscaled height of the input
    raw = work_pins(res, orient, H0)            # -> upscaled input frame
    pts = [dict(x=float(p['x']) / s, y=float(p['y']) / s,   # -> input frame
                side=p['side'], pred=bool(p['pred'])) for p in raw]
    return pts, 'grid', dict(mode='grid', orient=orient, scale=float(s),
                             score=float(score), pitch=res.get('pitch'),
                             note=res.get('note'), box=None)


# ----------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('image', nargs='?', default=SRC_DEFAULT)
    ap.add_argument('--out', default=OUT_DEFAULT)
    ap.add_argument('--scale', type=float, default=CFG['scale'])
    ap.add_argument('--pin1', default=CFG['pin1_corner'],
                    help='corner of pin 1: tl / tr / bl / br')
    ap.add_argument('--rot', default='auto', choices=['auto', 'h', 'v'],
                    help="auto = try both pin orientations; h = pads L/R; v = pads T/B")
    ap.add_argument('--peak-pos', type=float, default=CFG['peak_pos'],
                    help='strict mode: drop detrended peaks below this fraction '
                         'of the full positive height (kills mid-bright board)')
    ap.add_argument('--mode', default='auto',
                    choices=['auto', 'grid', 'blob', 'quad'],
                    help="auto = pick class A (regular lattice), class B "
                         "(dark body, pads only at the ends) or class C "
                         "(square package, pads on all four edges); "
                         "grid / blob / quad force one class")
    ap.add_argument('--body-sat', type=float, default=CFG['body_sat'],
                    help='class B: body saturation gate (x frame p99)')
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    img = cv2.imread(a.image, cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit('cannot read ' + a.image)

    cfg = dict(CFG, scale=a.scale, pin1_corner=a.pin1, peak_pos=a.peak_pos,
               body_sat=a.body_sat)
    best = None
    if a.rot == 'auto':
        best = detect_best(img, cfg, orients=('h', 'v'))
    else:
        best = detect_best(img, cfg, orients=(a.rot,), scale_mul=(1.0,))
    grid_score = 0.0 if best is None else float(best[0])

    # ---- class decision
    mode = a.mode
    qres = None
    if mode in ('auto', 'quad'):
        qres = quad_detect(img, cfg)
        if mode == 'quad' and not quad_ok(qres, cfg):
            print('quad mode: %s' % (qres.get('note') or
                                     'not a four-edge package'))
            return 1
    if mode == 'auto' and quad_ok(qres, cfg):
        # a QFP is the *only* shape whose four edges all carry a pad lattice.
        # It also happens to be the one shape class A gets wrong -- its left
        # and right edges alone form a perfectly plausible dual-row lattice,
        # so grid mode reports 2*N and silently drops the other two sides.
        # Validate the four-edge signature first and take it when it holds.
        mode = 'quad'
    bres = None
    gap_ratio, n_blob = 0.0, 0
    if mode in ('auto', 'blob'):
        bres = blob_detect(img, cfg)
        gap_ratio, n_blob = blob_gap_ratio(bres, cfg)
    if mode == 'auto':
        grid_irregular = (best is None) or (grid_score <= 0.0)
        if best is not None:
            L, R = best[3].get('L'), best[3].get('R')
            if L and R and (float(L['cv']) > float(cfg['cv_gate']) or
                            float(R['cv']) > float(cfg['cv_gate'])):
                grid_irregular = True
        if bres is None:
            pass
        use_blob = (bres is not None and bres.get('box') is not None
                    and n_blob >= int(cfg['blob_min_total'])
                    and (grid_irregular or gap_ratio >=
                         float(cfg['gap_ratio_gate'])))
        mode = 'blob' if use_blob else 'grid'
        if mode == 'grid' and best is None:
            print('no pad lattice found (tried orientation %s)' % a.rot)
            return 1

    if mode == 'quad':
        s = 1.0
        big = img.copy()
        pts = quad_pins(qres)
        bpts = qres['box'].copy()
        marked, pins = annotate(big, pts, bpts, cfg)
        half = cv2.resize(marked, (int(marked.shape[1] * 0.5),
                                   int(marked.shape[0] * 0.5)),
                          interpolation=cv2.INTER_AREA)
        steps = list(qres['steps'])
        steps.append(('4 marked', half))
        save(os.path.join(a.out, 'steps.png'), tile(steps, cols=4, cell=440))
        save(os.path.join(a.out, 'marked.png'), marked)
        cnt = {k: len(v['pins']) for k, v in qres['sides'].items()}
        info = dict(
            image=os.path.basename(a.image),
            image_size=[img.shape[1], img.shape[0]],
            mode='quad', orient='quad', scale=s,
            score=round(float(qres.get('pitch') or 0.0), 3),
            grid_score=round(grid_score, 3),
            body_box=[[round(float(x), 1), round(float(y), 1)]
                      for x, y in bpts],
            pitch_orig=None if not qres.get('pitch') else round(qres['pitch'], 2),
            n_edges=len(cnt), n_per_edge=cnt,
            n_left=cnt.get('left', 0), n_right=cnt.get('right', 0),
            n_top=cnt.get('top', 0), n_bottom=cnt.get('bottom', 0),
            note=qres.get('note'), pins=pins,
        )
        with open(os.path.join(a.out, 'pins.json'), 'w', encoding='utf-8') as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
        print('mode=quad  edges=%d  per-edge %s  pitch(orig)=%s  grid_score=%.1f'
              % (len(cnt), cnt, info['pitch_orig'], grid_score))
        print('total pins =', len(pins), '  ->', os.path.join(a.out, 'marked.png'))
        return 0

    if mode == 'blob':
        if bres is None:
            bres = blob_detect(img, cfg)
        if bres.get('box') is None:
            print('blob mode: %s' % (bres.get('note') or 'no body'))
            return 1
        s = 1.0
        big = img.copy()
        pts_raw = blob_pins(bres)
        oriented = 'v'
        steps = list(bres['steps'])
        bpts = bres['box'].copy() * s
        pts = order_pins(pts_raw, oriented, cfg['pin1_corner'])
        marked, pins = annotate(big, pts, bpts, cfg)
        half = cv2.resize(marked, (int(marked.shape[1] * 0.5),
                                   int(marked.shape[0] * 0.5)),
                          interpolation=cv2.INTER_AREA)
        steps.append(('3 marked', half))
        save(os.path.join(a.out, 'steps.png'), tile(steps, cols=4, cell=440))
        save(os.path.join(a.out, 'marked.png'), marked)
        nT = len(bres['pads'].get('top', []))
        nB = len(bres['pads'].get('bottom', []))
        info = dict(image=os.path.basename(a.image),
                    image_size=[img.shape[1], img.shape[0]],
                    mode='blob', orient=oriented, scale=s,
                    score=round(blob_score(bres, cfg), 3),
                    grid_score=round(grid_score, 3),
                    gap_ratio=round(gap_ratio, 2),
                    body_box=[[round(float(x), 1), round(float(y), 1)]
                              for x, y in bpts],
                    pitch_orig=None if not bres.get('pitch') else round(bres['pitch'], 2),
                    n_top=nT, n_bottom=nB, n_pred=0,
                    note=bres.get('note'), pins=pins)
        with open(os.path.join(a.out, 'pins.json'), 'w', encoding='utf-8') as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
        print('mode=blob  pads=%d (top %d / bottom %d)  gap_ratio=%.2f  '
              'grid_score=%.1f' % (len(pins), nT, nB, gap_ratio, grid_score))
        print('total pins =', len(pins), '  ->', os.path.join(a.out, 'marked.png'))
        return 0

    score, orient, s, res, cfg2 = best
    big = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    H0 = big.shape[0]

    pts_raw = work_pins(res, orient, H0)
    pts = order_pins(pts_raw, orient, cfg['pin1_corner'])

    body = res.get('body')
    if body:
        body = _rect_orig(body['x0'], body['y0'], body['x1'], body['y1'],
                          orient, H0)
    marked, pins = annotate(big, pts, body, cfg)
    half = cv2.resize(marked, (int(marked.shape[1] * 0.5),
                               int(marked.shape[0] * 0.5)),
                      interpolation=cv2.INTER_AREA)

    steps = list(res['steps'])
    steps.append(('12 marked', half))
    save(os.path.join(a.out, 'steps.png'), tile(steps, cols=6, cell=400))
    save(os.path.join(a.out, 'marked.png'), marked)

    nL = len(res['pads'].get('left', []))
    nR = len(res['pads'].get('right', []))
    info = dict(
        image=os.path.basename(a.image),
        image_size=[img.shape[1], img.shape[0]],
        mode='grid',
        orient=orient, scale=s, score=round(float(score), 3),
        gap_ratio=round(gap_ratio, 2),
        pitch_upscaled=None if not res.get('pitch') else round(res['pitch'], 2),
        pitch_orig=None if not res.get('pitch') else round(res['pitch'] / s, 2),
        band_left=None if not res.get('L') else [res['L']['x0'] // int(s), res['L']['x1'] // int(s)],
        band_right=None if not res.get('R') else [res['R']['x0'] // int(s), res['R']['x1'] // int(s)],
        n_left=nL, n_right=nR,
        n_pred=sum(1 for q in pts_raw if q['pred']),
        note=res.get('note'),
        pins=pins,
    )
    with open(os.path.join(a.out, 'pins.json'), 'w', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False, indent=1)

    print('mode=grid orient=%s scale=%.1f score=%.2f  L=%d R=%d filled=%d  '
          'pitch(orig)=%s' % (orient, s, score, nL, nR, info['n_pred'],
                              info['pitch_orig']))
    print('bands   = L%s R%s  match=%.1f'
          % (info['band_left'], info['band_right'], res.get('match', -1)))
    print('total pins =', len(pins), '  ->', os.path.join(a.out, 'marked.png'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
