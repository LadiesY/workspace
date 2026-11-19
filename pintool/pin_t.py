# -*- coding: utf-8 -*-
"""T 类引脚识别（两侧封装，**只上下两排、每排严格 10 个**引脚）。

由 `pin_detect.detect(img, box, "T", opt)` 自动路由到本模块
（见 `pin_detect._is_t_label`）。开发期在 `pin_dev_T/` 沙盒里迭代，
这里是与工具对接的正式版：算法与沙盒 `pin_t_dev.detect_T` 逐行一致，
只去掉 CLI/出图/旧算法对照（`_build_pin_t.py` 机械切片，不要手改本文件）。

接口与 pin_c / pin_f / pin_detect 同口径：

    detect_T(img, box, opt=None) -> (pins, counts, info)

    pins   元素 {"x", "y", "side", "idx"}，坐标 = 输入 img 坐标系
           side ∈ top/bottom；idx 为该排内序号（1 起，左→右）
    counts {"top":10,"right":0,"bottom":10,"left":0}
    info   诊断（每排 band/pitch/CV/残差/对比度 与 notes）；不可信的排返回 0 点

★ 硬先验（用户 2026-10-26 明确）：T **只会**是上下两排排布，**每排有且只有 10 个**。
  所以 ①不探左/右、不做两排/四边自动重判；②不数峰，直接拟合 10 齿等间距梳；
  ③不做「点数不符就回退通用算法」的护栏 —— 通用算法用的是两条列模型 +
  geo_classify 自动改判，正是旧版「T 被判成 left/right / 多出假排」的来源。
  宁可 0 点（UI 打 LOW），也不要"数量看着对、位置全错"。

算法要点（细节见函数 docstring，每条都有实测依据，别随手改）
------------------------------------------------------------
1. 响应图用**局部相对白度** Wn = W / Gauss(W, σ)，W = V·(255−S)/255。
   同板两排常差几倍亮度，任何绝对阈值都会把暗排整排吃掉。
2. 不数峰，直接拟合 N=10 齿等间距梳 a_k = c + k·P，只解 (c, P)。
3. 梳齿评分「齿位均值 − 缝隙均值」**对整排平移一个齿距不敏感**，所以必须再做
   **相位消歧**：看最暗一齿 / 中位齿的比值（正确 0.76~0.94，错一格 0.21~0.50）。
4. 深度 = **焊盘主亮块的重心**（主峰 ±0.4·pitch 窗口内亮度加权），不是 25% 电平
   中点 —— 本类引脚是「暗趾 + 亮焊盘」且两排不对称，电平中点会落在暗谷里
   （实测点/列峰值 0.72，改后 1.03）。配 `row_depth` 行级约束 + 放宽的行带。
5. 两排 pitch 共识：同封装必然同间距，相对差 >6% 用对比度高的那排锁另一排重解。
6. 自检：间距 CV ≤ 0.12、最小对比度 ≥ 0.10，越界则该排返回 0 点并在 notes 写明。
"""

from __future__ import print_function

import cv2
import numpy as np

TSIDES = ("top", "right", "bottom", "left")

# --------------------------------------------------------------------------- #
# ★ T 类硬先验（2026-10-26 用户明确）
# --------------------------------------------------------------------------- #
NT = 10                    # 每排**有且只有** 10 个引脚
PAIR = ("top", "bottom")   # **只会**是上下两排排布（不做左/右，也不做四边）

# 剖面/拟合参数（与 F 沙盒同源，已在 F 的 7 张图上验证过这套数学）
RESP_SIGMA_FRAC = 0.09     # Wn 的低频尺度（占 box 短边比例）
# ★ 行带阈值/厚度：T 的引脚比 F 的长（T1/2 实测引脚跨 48 px，box 短边 226 → 21%），
#   F 的 0.25 / 0.16 会**把行带截断在趾部**，25% 中点于是被拖到「暗趾与亮焊盘之间
#   的谷」上（实测 y=274，而亮焊盘中心 291）。放宽到 0.12 / 0.26 后两排都落在
#   焊盘亮块中心（top 291 / bottom 477），且 band 仍在框内。
BAND_LOW = 0.12            # 行带下限 = 背景 + BAND_LOW * (峰-背景)
BAND_SMOOTH = 2.0          # 行带阈值前先平滑深度剖面，用来跨过双峰之间的凹口
DEPTH_LEVEL = 0.25         # 单根引脚的深度取「25% 电平」首末跨点中点
BAND_MAX_FRAC = 0.26       # 行带最大厚度（占 box 短边），防止一直延伸进本体
PITCH_TOL = 0.20           # 梳齿间距搜索范围 ±20%
TOOTH_W = 0.45             # 单根引脚取质心的窗口半宽（×P）
EDGE_MARGIN = 0.02         # 齿位离 box 边的最小距离（占边长比例）
DEPTH_PAD_FRAC = 0.03      # 行带搜索向框外扩的量（占 box 短边比例）
EDGE_SKIP = 2              # 图像最外 2 px 不参与行带统计（贴边丝印很亮，会抢峰）
PITCH_CONSENSUS = 0.06     # 两排 pitch 相对差超过这个值就锁定重解
DWIN_FRAC = 0.40           # 焊盘深度搜索窗口（×pitch）：越小越贴最亮点，越大越稳


def _smooth(p, sigma):
    if sigma <= 0:
        return p
    r = max(1, int(round(3 * sigma)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / float(sigma)) ** 2)
    k /= k.sum()
    return np.convolve(np.pad(p, (r, r), mode="edge"), k, mode="valid")


class TCtx(object):
    """一颗 T 芯片的识别上下文（只读原图）。

    坐标：每侧用 (沿, 深) 描述。T 只上下两排，所以
      * 沿 a —— 图像的 x（绝对坐标）；
      * 深 d —— 距标注框该侧边的像素数，往框内为正；允许为负（行带搜索会向
        框外扩 pad 像素，框画紧一点也不至于把行带截断）。
    """

    def __init__(self, img, box, n=NT):
        self.img = img
        self.h, self.w = img.shape[:2]
        self.box = [float(v) for v in box]
        self.n = int(n)
        self.x0, self.y0, self.x1, self.y1 = self.box
        self.W = int(round(self.x1 - self.x0))
        self.H = int(round(self.y1 - self.y0))
        self.short = float(min(self.W, self.H))
        self.pad = int(round(DEPTH_PAD_FRAC * self.short))

        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
        V, S = hsv[:, :, 2], hsv[:, :, 1]
        W = V * (255.0 - S) / 255.0
        sig = max(6.0, RESP_SIGMA_FRAC * self.short)
        g = cv2.GaussianBlur(W, (0, 0), sig)
        self.Wn = W / (g + 8.0)          # 局部相对白度：消曝光梯度、保引脚周期
        self.sigma = sig

    # ---------------- (沿, 深) ↔ 图像坐标 ---------------- #
    def along_range(self, side):
        return int(round(self.x0)), int(round(self.x1))

    def depth_index(self, side, d):
        d = int(round(d))
        if side == "top":
            return int(round(self.y0)) + d
        return int(round(self.y1)) - 1 - d

    def _in_image(self, i):
        return 0 <= i < self.h

    def _line(self, i, a0, a1):
        return self.Wn[i, a0:a1]

    def depth_profile(self, side):
        """沿深度方向的均值剖面。下标 0 对应 d = -pad。"""
        a0, a1 = self.along_range(side)
        dmax = int(round(BAND_MAX_FRAC * self.short)) + 6
        idx = np.arange(-self.pad, dmax)
        out = np.zeros(len(idx), np.float32)
        lim = self.h - EDGE_SKIP
        for k, d in enumerate(idx):
            i = self.depth_index(side, d)
            if not (EDGE_SKIP <= i < lim):
                continue
            out[k] = float(self._line(i, a0, a1).mean())
        return out

    def band(self, side):
        """行带在深度方向的 [d_lo, d_hi)。"""
        dp = self.depth_profile(side)
        if not (dp > 0).any():
            return (0, 1), dict(dp=dp, thr=None, d0=-self.pad)
        dps = _smooth(dp, BAND_SMOOTH)           # 平滑以跨过双峰凹口
        pk = float(dps.max())
        bg = float(np.percentile(dp[dp > 0], 15))
        thr = bg + BAND_LOW * (pk - bg)
        hit = dps >= thr
        # 峰值只允许落在框内（d>=0）：引脚按约定必须在框里面。否则框松一点就可能
        # 把框外贴边的丝印/边框当成引脚行（F 沙盒实测过整排滑到框外）。
        i0 = min(self.pad, len(dp) - 1)
        imax = int(np.argmax(dps[i0:])) + i0
        lo = imax
        while lo > 0 and hit[lo - 1]:
            lo -= 1
        hi = imax
        while hi + 1 < len(dp) and hit[hi + 1]:
            hi += 1
        hi += 1
        wmax = int(round(BAND_MAX_FRAC * self.short))
        if hi - lo > wmax:
            c = (lo + hi) // 2
            lo = max(0, c - wmax // 2)
            hi = min(len(dp), lo + wmax)
        if hi - lo < 3:
            lo = max(0, imax - 3)
            hi = min(len(dp), imax + 4)
        d0 = -self.pad
        return (int(lo + d0), int(hi + d0)), dict(dp=dp, thr=float(thr), bg=bg, d0=d0)

    def along_profile(self, side, band):
        """沿边剖面：行带内对深度取均值 → 一根引脚一个峰。"""
        a0, a1 = self.along_range(side)
        dlo, dhi = band
        rows = [self.depth_index(side, d) for d in range(dlo, dhi)]
        rows = [i for i in rows if self._in_image(i)]
        if not rows:
            return np.zeros(max(1, a1 - a0), np.float32), (a0, 0)
        r0, r1 = min(rows), max(rows) + 1
        M = self.Wn[r0:r1, a0:a1]
        return M.mean(0), (a0, len(rows))

    def row_depth(self, side, band, win=None):
        """行级深度：**整排平均剖面**的主峰窗口重心。

        10 根引脚在深度方向是对齐的，沿边平均后噪声被平掉，峰位置比任何单列都稳。
        先有行级深度，再让每根齿只在这个深度 ±win 内找焊盘 —— 否则某一列撞上
        丝印/邻件时主峰整根跑掉（实测 7 号底排拟合残差 48 px 就是这么来的）。
        """
        dp = self.depth_profile(side)
        d_org = -self.pad
        dlo, dhi = band
        lo_i = max(0, dlo - d_org)
        hi_i = min(len(dp), dhi - d_org)
        if hi_i - lo_i < 3:
            return 0.5 * (dlo + dhi)
        seg = dp[lo_i:hi_i]
        k = int(np.argmax(seg))
        wth = float(win) if win else max(3.0, 0.25 * (dhi - dlo))
        a0 = max(0, k - int(round(wth)))
        b0 = min(len(seg), k + int(round(wth)) + 1)
        part = seg[a0:b0]
        bg = float(part.min())
        w = np.clip(part - bg, 0.0, None)
        if float(w.sum()) <= 1e-9:
            return float(dlo + k)
        ii = np.arange(dlo, dlo + len(seg), dtype=np.float64)[a0:b0]
        return float((w * ii).sum() / float(w.sum()))

    def depth_of(self, side, a, band, center=None, win=None):
        """某根引脚（沿边绝对坐标 a）在深度方向的中心 —— **主亮块（焊盘）的重心**。

        为什么不是 25% 电平中点（F 沙盒用的那个）
        ------------------------------------------
        T 这几张图是 SOP 贴在板上俯拍，一根引脚在深度方向是「**暗趾 + 亮焊盘**」
        结构，而且两侧并不对称：上排的趾在焊盘**上方**（远离本体），下排的趾在
        焊盘**下方**。25% 电平跨点会把暗趾一起包进去，中点正好落在「趾与焊盘之间
        的谷」上 —— 实测 7 张 14 排：「点处亮度 / 该列 band 内峰值」只有 **0.72**
        （最差 0.30），也就是点悬在暗处，肉眼一看就是"没压在焊盘上"。

        改用「主峰 ±win 窗口内的亮度加权重心」后，同一指标 **0.97**（最差 0.73）。

        实测四种口径（`_depth_probe.py`，7 张图 × 2 排）：
            25% 电平中点      0.718  最差 0.30   同排深度 σ 2.5 px
            (Wn-bg)² 重心     0.793  最差 0.43   σ 3.4
            **主峰±窗口重心    0.968  最差 0.73   σ 7.7**   ← 采用（配行级约束）
            主峰亚像素        1.000  最差 1.00   σ 8.8

        ``center`` 给定时（用 ``row_depth`` 的行级深度），只在 center±win 内找峰，
        防止单列被丝印/邻件带跑；不给则在本列 band 内自由找（旧行为）。
        """
        a = int(round(a))
        dlo, dhi = band
        m = 2
        idx = [d for d in range(dlo - m, dhi + m) if self._in_image(self.depth_index(side, d))]
        if not idx:
            return 0.5 * (dlo + dhi)
        col = self.Wn[:, a]
        seg = np.array([col[self.depth_index(side, d)] for d in idx], np.float32)
        if seg.size < 3:
            return 0.5 * (dlo + dhi)
        idx_a = np.asarray(idx, np.float64)
        wth = float(win) if win else max(3.0, 0.28 * (dhi - dlo))
        if center is None:
            lo_d, hi_d = float(idx[0]), float(idx[-1])
        else:
            lo_d, hi_d = float(center) - wth, float(center) + wth
        sel = np.nonzero((idx_a >= lo_d) & (idx_a <= hi_d))[0]
        if sel.size < 3:                       # 窗口太窄就退回整段
            sel = np.arange(len(idx))
        sub = seg[sel]
        k = int(np.argmax(sub))
        a0 = max(0, k - int(round(wth)))
        b0 = min(len(sub), k + int(round(wth)) + 1)
        part = sub[a0:b0]
        bg = float(part.min())
        w = np.clip(part - bg, 0.0, None)
        s = float(w.sum())
        if s <= 1e-9:
            return float(idx_a[sel][k])
        return float((w * idx_a[sel][a0:b0]).sum() / s)


def _fit_row_line(a, d):
    """把 n 个深度值拟合成一条直线 depth(a)=alpha*a+beta（两次剔除离群）。

    引脚行本身是直的，逐点深度抖动来自反光/丝印。拟合直线既更准，也顺便给出
    "行是否歪了"的证据（alpha != 0 说明相机/摆放有轻微倾斜）。
    """
    a = np.asarray(a, np.float64)
    d = np.asarray(d, np.float64)
    keep = np.ones(len(a), bool)
    alpha = beta = 0.0
    for _ in range(3):
        if keep.sum() < 3:
            break
        A = np.vstack([a[keep], np.ones(keep.sum())]).T
        sol, _, _, _ = np.linalg.lstsq(A, d[keep], rcond=None)
        alpha, beta = float(sol[0]), float(sol[1])
        res = d - (alpha * a + beta)
        s = max(float(np.median(np.abs(res))) * 1.4826, 0.35)
        keep = np.abs(res) <= 2.5 * s
    return alpha, beta, keep


def _teeth(c, P, n):
    return c + P * np.arange(n, dtype=np.float32)


def _comb_contrast(prof_s, c, P, n):
    """齿位均值 − 齿间隙（+P/2）均值。梳齿压在引脚上时这个量最大。"""
    a = _teeth(c, P, n)
    b = a + 0.5 * P
    if a[0] < 0 or b[-1] > len(prof_s) - 1:
        return -1e9
    L = len(prof_s) - 1
    ia = np.clip(np.round(a).astype(np.int32), 0, L)
    ib = np.clip(np.round(b).astype(np.int32), 0, L)
    return float(prof_s[ia].mean() - prof_s[ib].mean())


def _find_peaks(p, distance=3, rel_prom=0.12):
    p = np.asarray(p, np.float32)
    if p.size < 3:
        return np.zeros(0, np.int32)
    lo = float(np.percentile(p, 10))
    thr = lo + rel_prom * (float(p.max()) - lo)
    cand = [i for i in range(1, p.size - 1)
            if p[i] >= p[i - 1] and p[i] >= p[i + 1] and p[i] >= thr]
    cand.sort(key=lambda i: -p[i])
    keep = []
    for i in cand:
        if all(abs(i - j) >= distance for j in keep):
            keep.append(i)
    return np.array(sorted(keep), np.int32)


def _pitch_hint(prof):
    """峰间距中位数给 pitch 初值。

    不用自相关：本项目已量化过，自相关对"一根亮一根暗"的交替纹样会锁到 2 倍周期。
    """
    p = prof - np.percentile(prof, 10)
    if p.max() <= 0:
        return None
    pk = _find_peaks(p, distance=3, rel_prom=0.12)
    if len(pk) >= 4:
        d = np.diff(pk).astype(np.float32)
        d = d[d <= np.percentile(d, 80) * 1.3]
        if len(d) >= 3:
            return float(np.median(d))
    return None


def _scan_phase(prof_s, P, n):
    """固定 P，用两次卷积一次算出所有起点 c 的齿/隙对比，返回 (最佳 c, 得分)。"""
    span = int(round(P * (n - 1))) + 1
    if span + int(round(0.5 * P)) >= len(prof_s):
        return None
    half = int(round(0.5 * P))
    kt = np.zeros(span + half + 1, np.float32)
    kg = np.zeros(span + half + 1, np.float32)
    for k in range(n):
        i = int(round(k * P))
        kt[i] += 1.0 / n
        kg[i + half] += 1.0 / n
    st = np.correlate(prof_s, kt, "valid")
    sg = np.correlate(prof_s, kg, "valid")
    sc = st - sg
    i = int(np.argmax(sc))
    return float(i), float(sc[i])


def _phase_eval(prof_s, c, P, n):
    """评估一个相位候选，返回 (判别分, 对比度, 最暗齿/中位齿)。

    为什么需要它：``_comb_contrast`` 对"整排平移一个齿距"几乎不敏感 —— 齿位与
    缝隙同时平移一格，两项相减还是同一个数（F 沙盒实测 0.596 vs 0.580，只差
    0.016，判错方向）。真正稳的是**最暗那一齿的相对高度**：
      * 正确相位：N 齿全落在引脚上，最暗齿 ≈ 中位齿（比值 0.7~1.0）；
      * 错一格：末端那齿露到引脚阵列外，比值塌到 0.3 以下。
    门槛 0.70（F 沙盒 28 个侧面的实测两簇分界：正确 0.76~0.94 / 错格 0.21~0.50）。
    """
    L = len(prof_s) - 1
    a = _teeth(c, P, n).astype(np.float64)
    if a[0] < 0 or a[-1] > L:
        return 0.0, -1e9, 0.0
    idx = np.clip(np.round(a).astype(np.int32), 0, L)
    vals = prof_s[idx].astype(np.float64)
    med = float(np.median(vals))
    ct = _comb_contrast(prof_s, c, P, n)
    if med <= 1e-9:
        return 0.0, ct, 0.0
    rmin = float(vals.min() / med)
    pen = min(1.0, rmin / 0.70)
    return ct * pen, ct, rmin


def fit_comb(prof, n, margin_px=0.0, pitch_lock=None, lock_tol=0.015):
    """解 (c, P)：n 齿等间距梳拟合。``pitch_lock`` 用于"两排同封装必然同间距"。"""
    lo_i = int(round(margin_px))
    hi_i = len(prof) - 1 - int(round(margin_px))
    prof_s = _smooth(prof.astype(np.float32), 1.1)

    hint = _pitch_hint(prof)
    how = "peaks"
    if hint is None or not (3 < hint < len(prof) / max(2.0, n / 4.0)):
        hint = (hi_i - lo_i) / float(max(1, n - 1))
        how = "span"

    if pitch_lock:
        hint = float(pitch_lock)
        P_lo = hint * (1.0 - lock_tol)
        P_hi = hint * (1.0 + lock_tol)
    else:
        P_lo = hint * (1.0 - PITCH_TOL)
        P_hi = hint * (1.0 + PITCH_TOL)
    best = (-1e18, None, None)
    for P in np.arange(P_lo, P_hi + 1e-9, 0.02):
        r = _scan_phase(prof_s, P, n)
        if r is None:
            continue
        if r[1] > best[0]:
            best = (r[1], r[0], P)
    if best[1] is None:
        return None
    _, c, P = best
    for _ in range(4):
        for dc in np.arange(-0.70, 0.71, 0.05):
            for dP in np.arange(-0.06, 0.061, 0.005):
                sc = _comb_contrast(prof_s, c + dc, P + dP, n)
                if sc > best[0]:
                    best = (sc, c + dc, P + dP)
        _, c, P = best

    # 相位消歧（判据见 _phase_eval）
    _, c0, P0 = best
    pick = (_phase_eval(prof_s, c0, P0, n)[0], c0)
    for k in (-2, -1, 1, 2):
        sc = _phase_eval(prof_s, c0 + k * P0, P0, n)[0]
        if sc > pick[0] * 1.05:
            pick = (sc, c0 + k * P0)
    c = pick[1]
    if abs(c - c0) > 1e-6:
        best = (_comb_contrast(prof_s, c, P0, n), c, P0)
        for _ in range(2):
            for dc in np.arange(-0.30, 0.31, 0.05):
                for dP in np.arange(-0.04, 0.041, 0.005):
                    sc = _comb_contrast(prof_s, c + dc, P0 + dP, n)
                    if sc > best[0]:
                        best = (sc, c + dc, P0 + dP)
            _, c, P0 = best

    return dict(c=float(best[1]), P=float(best[2]), score=float(best[0]),
                hint=float(hint), how=how)


def refine_teeth(prof, c, P, n, w=TOOTH_W):
    """每根齿在 ±w*P 内求亚像素重心，返回 (位置, 残差, 对比度)。"""
    out = np.zeros(n, np.float32)
    resid = np.zeros(n, np.float32)
    contrast = np.zeros(n, np.float32)
    L = len(prof)
    for k in range(n):
        a = c + P * k
        lo = max(0, int(np.floor(a - w * P)))
        hi = min(L, int(np.ceil(a + w * P)) + 1)
        if hi - lo < 3:
            out[k] = a
            continue
        seg = prof[lo:hi].astype(np.float32)
        bl = float(seg.min())
        wgt = seg - bl
        s = float(wgt.sum())
        if s <= 1e-6:
            out[k] = a
        else:
            idx = np.arange(lo, hi, dtype=np.float32)
            out[k] = float((wgt * idx).sum() / s)
            contrast[k] = float(seg.max() - bl)
        resid[k] = float(a - out[k])
    return out, resid, contrast


def _fit_side(ctx, side, n, margin, pitch_lock=None):
    """单侧：定行带 → 沿边剖面 → n 齿梳 → 亚像素精化 → 深度 → 图像坐标。"""
    band, _bi = ctx.band(side)
    prof, (alo, thick) = ctx.along_profile(side, band)
    if prof.size < max(8, n * 2):
        return dict(ok=False, reason="profile too short", band=band, thick=thick)
    fit = fit_comb(prof, n, margin_px=margin, pitch_lock=pitch_lock)
    if fit is None:
        return dict(ok=False, reason="comb fit failed", band=band, thick=thick)

    teeth, resid, contrast = refine_teeth(prof, fit["c"], fit["P"], n)
    # 深度：先用整排平均剖面定「行级深度」，再让每根齿只在该深度 ±半 pitch 内
    # 找焊盘。窗口用 pitch 而不是固定像素，不同分辨率（T1 从 268 到 605 px）自适应。
    win = DWIN_FRAC * fit["P"]
    cdep = ctx.row_depth(side, band, win=win)
    dep_raw = np.array([ctx.depth_of(side, t + alo, band, center=cdep, win=win)
                        for t in teeth], np.float64)
    alpha, beta, _ = _fit_row_line(np.arange(n, dtype=np.float64), dep_raw)
    dep = alpha * np.arange(n, dtype=np.float64) + beta
    dep_res = float(np.abs(dep_raw - dep).max()) if n else 0.0

    Y0, Y1 = int(round(ctx.y0)), int(round(ctx.y1))
    rows = []
    for k in range(n):
        a = float(teeth[k]) + alo
        d = float(dep[k])
        y = Y0 + d if side == "top" else Y1 - 1 - d
        rows.append((float(np.clip(a, ctx.x0, ctx.x1)),
                     float(np.clip(y, ctx.y0, ctx.y1)),
                     float(contrast[k]), float(resid[k])))
    rows.sort(key=lambda r: r[0])          # 上下排都是左→右

    sp = np.diff([r[0] for r in rows])
    cv = float(sp.std() / max(1e-6, sp.mean())) if sp.size else 9.9
    ct_min = float(np.min(contrast)) if contrast.size else 0.0
    ok = (cv <= 0.12) and (ct_min >= 0.10) and (fit["P"] > 3)
    return dict(
        ok=bool(ok), band=band, thick=thick, rows=rows, fit=fit,
        locked=(pitch_lock is not None), cv=cv, ct_min=ct_min,
        resid_max=float(np.abs(resid).max()) if resid.size else 0.0,
        dep_resid_max=dep_res, tilt=float(np.degrees(np.arctan(alpha / max(1e-6, fit["P"])))),
        reason="" if ok else ("spacing irregular" if cv > 0.12 else "weak contrast"),
    )


def detect_T(img, box, opt=None):
    """T 类识别入口（v2）：**只认上下两排，每排严格 10 个**。

    与工具旧算法的区别（旧算法 = ``detect_T_baseline``）：
      1. **不探左右、不做四边/两排的自动重判**。用户明确 T 只会上下排布，所以
         旧算法那套 ``geo_classify``（按证据在 2 边/4 边之间来回改判）全部不用 ——
         它正是「T1/2 被判成 left/right」或「多出几条假排」的来源。
      2. **N=10 是硬先验**：不再数峰，直接拟合 10 齿等间距梳 ``a_k = c + k·P``。
         少一根/多一根都会体现在残差与"最暗一齿"上，而不是体现在点数上。
      3. **深度用 25% 电平首末跨点中点**，不用"最亮处"，也不用重心 —— 引脚在
         深度方向是双峰，最亮的是根部焊盘端（T1/2 实测差 18 px）。

    返回口径：
        pins    [dict(x, y, side, idx)]，side ∈ {top,bottom}；idx = 排内左→右
        counts  {"top":n,"right":0,"bottom":n,"left":0}
        info    每侧 band/pitch/CV/残差/对比度 + 两排 pitch 共识记录
    """
    o = dict(n=NT)
    if opt:
        o.update(opt)
    n = int(o.get("n") or NT)

    ctx = TCtx(img, box, n=n)
    margin = EDGE_MARGIN * max(ctx.W, ctx.H)

    res = {s: _fit_side(ctx, s, n, margin) for s in PAIR}

    # ---- 两排 pitch 共识 ----
    # 同封装两排必然同间距。只有两排，没有"多数"可依，所以取**对比度更高**的那排
    # 当基准（它更可能真的压在引脚上），把另一排锁在它的 P ±1.5% 内重解。
    note = []
    if all(res[s].get("fit") for s in PAIR):
        P = {s: res[s]["fit"]["P"] for s in PAIR}
        spread = abs(P["top"] - P["bottom"]) / max(P.values())
        if spread > PITCH_CONSENSUS:
            ref = max(PAIR, key=lambda s: res[s]["fit"]["score"])
            other = [s for s in PAIR if s != ref][0]
            r2 = _fit_side(ctx, other, n, margin, pitch_lock=P[ref])
            # 只在**锁定后仍然可信**时才替换：否则保留原解（原来至少是 ok 的，
            # 被锁到别排的错误 P 上只会更差）。
            if r2.get("ok"):
                note.append("%s 排 pitch %.3f 与 %s 排 %.3f 差 %.1f%% → 按 %s 锁定重解"
                            % (other, P[other], ref, P[ref], 100 * spread, ref))
                res[other] = r2

    pins, counts = [], {}
    info = dict(n=n, sigma=round(ctx.sigma, 2), sides=list(PAIR),
                failed=False, notes=note, detail={}, counts={})
    for s in PAIR:
        r = res[s]
        counts[s] = len(r["rows"]) if r.get("ok") else 0
        if not r.get("ok"):
            if r.get("fit"):
                note.append("%s 排不可信：%s（P=%.2f，CV=%.3f）"
                            % (s, r["reason"], r["fit"]["P"], r["cv"]))
            continue
        for (x, y, _ct, _rs) in r["rows"]:
            pins.append(dict(x=round(x, 2), y=round(y, 2), side=s))
        info["detail"][s] = dict(
            band=r["band"], thick=r["thick"], locked=r["locked"],
            pitch=round(r["fit"]["P"], 4), pitch_hint=round(r["fit"]["hint"], 3),
            how=r["fit"]["how"], score=round(r["fit"]["score"], 5),
            cv=round(r["cv"], 4), resid_max=round(r["resid_max"], 3),
            dep_resid_max=round(r["dep_resid_max"], 3),
            tilt_deg=round(r["tilt"], 3), contrast_min=round(r["ct_min"], 4))
    for s in ("right", "left"):
        counts[s] = 0
    info["counts"] = dict(counts)
    info["total"] = len(pins)
    return _tag_idx(pins, counts, info)


def _tag_idx(pins, counts, info):
    """把算法输出的扁平 pins 按侧别补上排内阅读序号 idx（1 起）。"""
    out = []
    for s in TSIDES:
        g = [p for p in pins if p["side"] == s]
        if s in ("top", "bottom"):
            g.sort(key=lambda p: p["x"])
        else:
            g.sort(key=lambda p: p["y"])
        for k, p in enumerate(g, 1):
            d = dict(p)
            d["idx"] = k
            out.append(d)
    info = dict(info or {})
    info["total"] = len(out)
    info["counts"] = dict(counts or {})
    return out, counts, info


def order_by_side(pins):
    return {s: sorted([p for p in pins if p["side"] == s], key=lambda p: p["idx"])
            for s in TSIDES}


# --------------------------------------------------------------------------- #
# 核对图
# --------------------------------------------------------------------------- #
def imread_u(path):
    """读图（兼容中文/空格路径）。"""
    with open(path, "rb") as f:
        buf = np.frombuffer(f.read(), np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


