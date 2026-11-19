# -*- coding: utf-8 -*-
"""F 类引脚识别（四边 QFP，每排 **严格 32 个**引脚）。

由 `pin_detect.detect(img, box, "F", opt)` 自动路由到本模块
（见 `pin_detect._is_f_label`）。开发期在 `pin_dev_F/` 沙盒里迭代，
这里是与工具对接的正式版（算法与沙盒逐行一致，只去掉 CLI/出图）。

接口与 pin_c / pin_detect 同口径：

    detect_F(img, box, n=NF, opt=None) -> (pins, counts, info)

    pins   元素 {"x", "y", "side", "idx"}，坐标 = 输入 img 坐标系
           side ∈ top/right/bottom/left；idx 为该排内序号（1 起，阅读顺序）
    counts {"top":32,"right":32,"bottom":32,"left":32}
    info   诊断（每侧 band/pitch/CV/对比度/残差 与 notes）；不可信的侧返回 0 点

★ 硬先验 N=32：**只适用于每排 32 脚的这一款 F 封装**（用户 2026-10-26 明确：
  旧库里 17/24/25/28/36 脚的 F 一律不再支持）。因此**不做**「真实脚数不符就回退
  通用算法」的护栏 —— 通用算法用的是两排模型，对四边 QFP 会给出假点，
  宁可 0 点（UI 打 LOW），也不要"数量看着对、位置全错"。

算法要点（细节见函数 docstring，每条都有实测依据，别随手改）
------------------------------------------------------------
1. 响应图用**局部相对白度** Wn = W / Gauss(W, σ)，W = V·(255−S)/255。
   同板底排常比顶排暗 3 倍，任何绝对亮度阈值都会把整排判成背景。
2. 不数峰，直接拟合 **N 齿等间距梳** a_k = c + k·P，只解 (c, P)。数量天然恒等于 N。
3. 梳齿评分用「齿位均值 − 缝隙均值」。该评分**对整排平移一个齿距不敏感**
   （齿与缝同时平移），所以必须再做**相位消歧**：看最暗一齿相对中位齿高的
   比值 rmin（正确相位 0.76~0.94，错一格 0.21~0.50，见 `_phase_eval`）。
4. 深度坐标 = 单列 Wn 上「25% 电平首末跨点」的中点（引脚深度是双峰，重心会偏）。
5. 交叉约束：四排同封装必然同间距，偏离中位 pitch >6% 的一侧用中位 pitch 重解。
6. 自检：最小对比度 ≥ 0.10、中位对比度 ≥ 0.20、间距 CV（低分辨率下放宽到 0.40
   但要求对比度 ≥ 0.18）；越界则该侧返回 0 点并在 notes 写明。

2026-10-27 针对性修补（都是"某一侧 0 点"的实测死法，见各函数 docstring）
------------------------------------------------------------------
A. `FCtx.bands()` 返回**多个候选行带**：底排压在阴影里、本体下棱反光更亮时
   argmax 会锁到本体棱上（12/13/10 三张图的底排全是这个死法）。主带自检不过
   就退到次带重试；主带合格时行为与旧版完全一致。
B. pitch 搜索改用**几何先验** ARRAY_FRAC(0.67)×跨度兜底：pitch 3.2 px 时峰间距
   被合并规则带偏到 3.0，旧代码 `3 < hint` 直接丢掉、退回跨度口径 5.16，
   搜索区间里没有真值 → 11 号图四侧全 0。
C. 梳齿评分与 rmin 取样改成**亚像素线性插值**：整数取整会让目标变成阶梯函数，
   13 号图 F.3 上排因此把真周期 3.98 解成 4.029（+1.2%，32 齿累计漂 1.5 px）。
D. `_fix_overshoot()`：相位整体偏几格时末端 2~4 齿落到阵列外（11 号图 F.3 右排
   末 3 齿对比度 0.06/0.03/0.02），按"掉队齿最少"平移修正。
E. 沿边抖动 > 0.10P 时用 32 点稳健直线替换逐齿重心（低分辨率下噪声 ±0.5 px）。
F. **深度取样补 `+alo`**（2026-10-27 晚，最要紧的一处）：`refine_teeth` 给的 teeth 是
   相对 `alo`（along_range 起点）的口径，`depth_of` 要的是绝对图像坐标。之前漏加，
   深度取自"图像第 k 列"而不是"第 alo+k 列"，偏移量正好等于框 x0/y0 —— 越靠右/下的
   芯片偏得越多，逐脚深度被 `_fit_row_line` 固化成一条 8 px 斜坡，看起来就是
   **"引脚排是正的、标注是斜的"**。96 侧统计：倾斜（p90）0.0197→0.0111、最大 0.0820→0.0364。
   同时 `depth_of` 里对 a 做了夹边界（齿会被振荡推出框 -> 否则 IndexError）。
"""

from __future__ import print_function

import cv2
import numpy as np

import argparse
import json
import os
import sys
import time

import cv2
import numpy as np

# --------------------------------------------------------------------------- #
# 先验与常量
# --------------------------------------------------------------------------- #
NF = 32                    # ★ F 类每排引脚数（硬先验，本款 32）
SIDES = ("top", "right", "bottom", "left")

RESP_SIGMA_FRAC = 0.09     # Wn 的低频尺度（占 box 短边比例）
BAND_LOW = 0.25            # 行带下限 = 背景 + BAND_LOW * (峰-背景)
BAND_SMOOTH = 2.0          # 行带阈值前先平滑深度剖面（px），用来跨过双峰之间的凹口
DEPTH_LEVEL = 0.25         # 单根引脚的深度范围取「25% 电平」的首末跨点
BAND_MAX_FRAC = 0.16       # 行带最大厚度（占 box 短边），防止一直延伸到本体里
BAND_CAND_MAX = 3          # 深度方向最多试几个候选行带（主带不合格时才往下试）
BAND_MIN_THICK = 3         # 候选行带最小厚度（px）
ARRAY_FRAC = 0.67          # ★ 引脚阵列占边长比例（32 脚 QFP 实测 0.65~0.70，13 图 ×4 侧）
PITCH_TOL = 0.20           # 梳齿间距搜索范围 ±20%
TOOTH_W = 0.45             # 单根引脚取质心的窗口半宽（×P）
EDGE_MARGIN = 0.02         # 齿位离 box 边的最小距离（占边长比例）
DEPTH_PAD_FRAC = 0.03      # 行带搜索向框外扩的量（占 box 短边比例）
EDGE_SKIP = 2              # 图像最外 2 px 不参与行带统计（贴边丝印很亮，会抢峰）


def _smooth(p, sigma):
    if sigma <= 0:
        return p
    r = max(1, int(round(3 * sigma)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / float(sigma)) ** 2)
    k /= k.sum()
    return np.convolve(np.pad(p, (r, r), mode="edge"), k, mode="valid")


def _response(img):
    """局部相对白度图 Wn（float32，与原图同尺寸）。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    V, S = hsv[:, :, 2], hsv[:, :, 1]
    W = V * (255.0 - S) / 255.0
    return W, W.shape


class FCtx(object):
    """一个 F 芯片的识别上下文（只读原图）。

    坐标约定：每侧用 (沿, 深) 两个量描述。
      * 深 d  —— 距**标注框该侧边**的像素数，往框内为正。允许为负：行带搜索会
        向框外扩 ``pad`` 像素，这样框画紧一点也不会把行带截断（实测框上移 3 px
        时底排深度中点会漂 5.5 px，就是截断造成的）。
      * 沿 a  —— 沿该侧方向的绝对图像坐标（上/下排是 x，左/右排是 y）。
    """

    def __init__(self, img, box, n=NF):
        self.img = img
        self.h, self.w = img.shape[:2]
        self.box = [float(v) for v in box]
        self.n = int(n)
        self.x0, self.y0, self.x1, self.y1 = self.box
        self.W = int(round(self.x1 - self.x0))
        self.H = int(round(self.y1 - self.y0))
        self.short = float(min(self.W, self.H))
        self.long = float(max(self.W, self.H))
        self.pad = int(round(DEPTH_PAD_FRAC * self.short))

        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
        V, S = hsv[:, :, 2], hsv[:, :, 1]
        W = V * (255.0 - S) / 255.0
        sig = max(6.0, RESP_SIGMA_FRAC * self.short)
        g = cv2.GaussianBlur(W, (0, 0), sig)
        self.Wn = W / (g + 8.0)
        self.sigma = sig
        ys, ye = int(round(self.y0)), int(round(self.y1))
        xs, xe = int(round(self.x0)), int(round(self.x1))
        # 夹到图像内：框被拉到图片外时切片为空 -> np.median 返回 nan（只影响 info 的
        # base，不影响点位，但会给上层和测试脚本刷 RuntimeWarning）。
        ys, ye = max(0, min(ys, self.h)), max(0, min(ye, self.h))
        xs, xe = max(0, min(xs, self.w)), max(0, min(xe, self.w))
        seg = self.Wn[ys:ye, xs:xe]
        self.base = float(np.median(seg)) if seg.size else 1.0

    # ---------------- (沿, 深) 与图像坐标的换算 ---------------- #
    def along_range(self, side):
        """沿边方向的绝对下标范围 [a0, a1)（框内，已夹到图像内）。

        必须夹：标注框偶尔会顶出图像边界（图 1 的框放大 6% 后 x0=-5.7），
        越界后切片返回空数组 → 深度剖面整条 nan → 该侧"profile too short"
        直接 0 点。夹住之后框出界只是少几个像素，不会整排丢。
        """
        if side in ("top", "bottom"):
            return max(0, int(round(self.x0))), min(self.w, int(round(self.x1)))
        return max(0, int(round(self.y0))), min(self.h, int(round(self.y1)))

    def depth_index(self, side, d):
        """深度 d 对应的绝对行号（上/下排）或列号（左/右排）。"""
        d = int(round(d))
        if side == "top":
            return int(round(self.y0)) + d
        if side == "bottom":
            return int(round(self.y1)) - 1 - d
        if side == "left":
            return int(round(self.x0)) + d
        return int(round(self.x1)) - 1 - d

    def _in_image(self, side, i):
        return 0 <= i < (self.h if side in ("top", "bottom") else self.w)

    def depth_profile(self, side):
        """沿深度方向的均值剖面。下标 0 对应 d = -pad，所以 d = idx - pad。"""
        a0, a1 = self.along_range(side)
        dmax = int(round(BAND_MAX_FRAC * self.short)) + 6
        idx = np.arange(-self.pad, dmax)
        out = np.zeros(len(idx), np.float32)
        lim = (self.h if side in ("top", "bottom") else self.w) - EDGE_SKIP
        for k, d in enumerate(idx):
            i = self.depth_index(side, d)
            if not (EDGE_SKIP <= i < lim):      # 图像最外几像素丢弃（贴边丝印/边框）
                continue
            if side in ("top", "bottom"):
                line = self.Wn[i, a0:a1]
            else:
                line = self.Wn[a0:a1, i]
            out[k] = float(line.mean())
        return out

    def bands(self, side):
        """候选行带列表 ``[(band, binfo), ...]``，**第一项与旧 band() 完全一致**。

        为什么要有多个候选
        ------------------
        底排引脚普遍压在阴影里，而芯片本体的**下棱有一道镜面反光**，在 Wn 上比
        引脚本身还亮。于是 ``argmax`` 锁到本体棱上（实测 12/13/10 三张图的底排
        全是这个死法：band 跑到了 d≈40，那里是本体棱，沿边剖面一整条全平，
        自检判"间距不规则" → 该侧 0 点）。11 号图底排更糟：本体内部的下沿也是
        一片亮，argmax 直接进了本体。

        主带自检不过时不能就此给 0 点 —— 引脚行本身还在剖面上，只是不是最高峰。
        所以这里把**所有**高于同一门限的连续段都列成候选，让 ``_fit_side`` 逐个
        试梳齿拟合：真引脚行能做出一致间距 + 高对比度的梳，本体棱做不出来。
        主带合格时直接返回主带，既有好侧一个像素都不动。
        """
        dp = self.depth_profile(side)
        d0 = -self.pad
        if not (dp > 0).any():
            return [((0, 1), dict(dp=dp, thr=None, d0=d0, peak=None, primary=True))]
        dps = _smooth(dp, BAND_SMOOTH)          # 平滑以跨过双峰凹口
        pk = float(dps.max())
        bg = float(np.percentile(dp[dp > 0], 15))
        thr = bg + BAND_LOW * (pk - bg)
        hit = dps >= thr
        # 峰值只允许落在框内（d>=0）：引脚按约定必须在标注框里面。
        # 否则框松一点就可能把框外贴边的丝印/边框当成引脚行（实测 box+5 时
        # 底排整排滑到框外 d=-13..-2，位置全错但数量仍是 32）。
        i0 = min(self.pad, len(dp) - 1)
        wmax = int(round(BAND_MAX_FRAC * self.short))

        def grow(i):
            lo = i
            while lo > 0 and hit[lo - 1]:
                lo -= 1
            hi = i
            while hi + 1 < len(dp) and hit[hi + 1]:
                hi += 1
            return lo, hi + 1

        def clip(lo, hi, imax):
            if hi - lo > wmax:
                c = (lo + hi) // 2
                lo = max(0, c - wmax // 2)
                hi = min(len(dp), lo + wmax)
            if hi - lo < BAND_MIN_THICK:
                lo = max(0, imax - 3)
                hi = min(len(dp), imax + 4)
            return int(lo + d0), int(hi + d0)

        imax = int(np.argmax(dps[i0:])) + i0
        lo, hi = grow(imax)
        prim = clip(lo, hi, imax)
        out = [(prim, dict(dp=dp, thr=float(thr), bg=bg, d0=d0,
                           peak=float(dps[imax]), primary=True))]

        segs, i = [], 0
        while i < len(dp):
            if not hit[i]:
                i += 1
                continue
            a, b = grow(i)
            j = int(np.argmax(dps[a:b])) + a
            if j >= i0:                          # 次带的峰也必须在框内
                c = clip(a, b, j)
                if c != prim and c[1] - c[0] >= BAND_MIN_THICK:
                    segs.append((float(dps[j]), c))
            i = b
        segs.sort(key=lambda t: -t[0])
        seen = set([prim])
        for pkv, c in segs:
            if c in seen:
                continue
            seen.add(c)
            out.append((c, dict(dp=dp, thr=float(thr), bg=bg, d0=d0,
                                peak=pkv, primary=False)))
            if len(out) >= BAND_CAND_MAX:
                break
        return out

    def band(self, side):
        """行带在深度方向的范围 [d_lo, d_hi)（整数像素，可为负）。"""
        return self.bands(side)[0]

    def along_profile(self, side, band):
        """沿边剖面：行带内对深度取均值 → 一维信号（一根引脚一个峰）。"""
        a0, a1 = self.along_range(side)
        dlo, dhi = band
        rows = [self.depth_index(side, d) for d in range(dlo, dhi)]
        rows = [i for i in rows if self._in_image(side, i)]
        if not rows:
            return np.zeros(max(1, a1 - a0), np.float32), (a0, 0)
        r0, r1 = min(rows), max(rows) + 1
        if side in ("top", "bottom"):
            M = self.Wn[r0:r1, a0:a1]          # (深度, 沿) -> 沿方向求均值
            return M.mean(0), (a0, len(rows))
        M = self.Wn[a0:a1, r0:r1]              # (沿, 深度) -> 沿方向求均值
        return M.mean(1), (a0, len(rows))

    def depth_of(self, side, a, band):
        """某根引脚（沿边**绝对**图像坐标 a）在深度方向的中心 —— 25% 电平首末跨点的中点。

        为什么不是重心、也不是半高宽：引脚在深度方向是双峰（脚趾亮 → 中段偏暗
        → 根部亮，实测 Wn 1.61 / 0.93 / 1.91）。重心被两峰亮度比拖来拖去；
        半高宽只抱住其中一个峰。25% 电平的首末跨点把凹口自然桥接，得到的是
        引脚本体的几何中心。

        ⚠ a 必须是**绝对**坐标。`refine_teeth` 返回的 teeth 是**相对** `alo`
        （= `along_range` 的 a0）的口径，调用方要先 `+ alo`。2026-10-27 之前
        `_fit_band` 漏了这一步（见该处注释），导致深度取自图像左上角起算的
        另一列 —— 偏移量正好等于框 x0/y0，越靠右/下的芯片越偏，逐脚深度被
        拟合成一条 8 px 的斜坡，看起来"引脚排是斜的"。
        """
        lim = self.h if side in ("top", "bottom") else self.w
        a = min(max(0, int(round(a))), lim - 1)    # 越界会 IndexError（齿可能被振荡推出框）
        dlo, dhi = band
        m = 2
        idx = list(range(dlo - m, dhi + m))
        idx = [d for d in idx if self._in_image(side, self.depth_index(side, d))]
        if not idx:
            return 0.5 * (dlo + dhi)
        if side in ("top", "bottom"):
            col = self.Wn[:, a]
        else:
            col = self.Wn[a, :]
        seg = np.array([col[self.depth_index(side, d)] for d in idx], np.float32)
        if seg.size < 2:
            return 0.5 * (dlo + dhi)
        bg = float(seg.min())
        pk = float(seg.max())
        if pk - bg < 1e-3:
            return 0.5 * (dlo + dhi)
        thr = bg + DEPTH_LEVEL * (pk - bg)
        above = np.nonzero(seg >= thr)[0]
        if above.size == 0:
            return float(idx[int(np.argmax(seg))])
        k = 0.5 * (above[0] + above[-1])
        # 在跨点之间做线性插值，得到亚像素深度
        lo_i = int(np.floor(k))
        fr = k - lo_i
        if 0 <= lo_i and lo_i + 1 < len(idx):
            return float(idx[lo_i] + fr * (idx[lo_i + 1] - idx[lo_i]))
        return float(idx[min(lo_i, len(idx) - 1)])


def _fit_row_line(a, d):
    """把 32 个深度值拟合成一条直线 depth(a)=alpha*a+beta（两次剔除离群）。

    引脚行本身是直的，逐点深度的小抖动来自反光/丝印。拟合直线既更准，
    也顺便给出「行是否歪了」的证据（alpha != 0 说明相机/摆放有轻微倾斜）。
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
        s = float(np.median(np.abs(res))) * 1.4826
        s = max(s, 0.35)
        keep = np.abs(res) <= 2.5 * s
    return alpha, beta, keep


# --------------------------------------------------------------------------- #
# 梳齿拟合
# --------------------------------------------------------------------------- #
def _teeth(c, P, n):
    return c + P * np.arange(n, dtype=np.float32)


def _at(prof_s, v):
    """在剖面 v 处线性插值（亚像素取样）。

    为什么不能用 ``np.round`` 取整数下标
    ----------------------------------
    齿位被四舍五入到整像素后，目标函数变成**阶梯函数**：P 在 ±0.5 px 内变化时
    得分只在"取整跳变"的台阶上才变。于是 argmax 会停在"取整运气好"的 P 上，
    而不是真周期。实测 13 号图 F.3 上排：真周期 3.98（谐波拟合 3.975 / 分段
    3.965~3.995 全一致），旧取整目标给 4.029（偏 +1.2%），32 齿从第 1 根到最后
    一根累计漂 1.5 px ≈ 0.4 个齿距 —— 端点明显偏。
    改成线性插值后目标连续可微，同一个例子给出 3.998。
    （np.interp 每次调用有 ~15 µs 开销，爬山要算几千次，所以手写插值。）
    """
    L = len(prof_s) - 1
    v = min(max(v, 0.0), float(L))
    i = int(v)
    if i >= L:
        return float(prof_s[L])
    f = v - i
    return float(prof_s[i] * (1.0 - f) + prof_s[i + 1] * f)


def _comb_score(prof_s, c, P, n):
    """N 齿梳的得分：齿位处平滑剖面的均值（离开有效区间直接判死）。"""
    a = _teeth(c, P, n)
    if a[0] < 0 or a[-1] > len(prof_s) - 1:
        return -1e9
    L = len(prof_s) - 1
    i = np.clip(np.floor(a).astype(np.int32), 0, L - 1)
    f = a - i
    return float((prof_s[i] * (1.0 - f) + prof_s[i + 1] * f).mean())


def _comb_contrast(prof_s, c, P, n):
    """齿位均值 − 齿间隙（+P/2）均值（齿位亚像素取样，见 ``_at``）。

    这是真正该最大化的量：梳齿压在引脚上时齿高、间隙低，对比最大。
    只用「齿位均值」会奖励「把 32 齿挤进一条本来就亮的窄带」。
    """
    a = _teeth(c, P, n).astype(np.float64)
    b = a + 0.5 * P
    if a[0] < 0 or b[-1] > len(prof_s) - 1:
        return -1e9
    L = len(prof_s) - 1
    ia = np.clip(np.floor(a).astype(np.int32), 0, L - 1)
    fa = a - ia
    ib = np.clip(np.floor(b).astype(np.int32), 0, L - 1)
    fb = b - ib
    va = prof_s[ia] * (1.0 - fa) + prof_s[ia + 1] * fa
    vb = prof_s[ib] * (1.0 - fb) + prof_s[ib + 1] * fb
    return float(va.mean() - vb.mean())



def _find_peaks(p, distance=3, rel_prom=0.12):
    """极简峰检测（替代 scipy.signal.find_peaks，省掉一个外部依赖）。

    先按「局部极大 + 高于相对门限」筛候选，再按高度贪心、保证最小间隔。
    只用来量相邻峰间距的中位数，不需要 scipy 那套 prominence 的完整语义。
    """
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

    为什么不用自相关：本项目已量化过，自相关对"一根亮一根暗"的交替纹样会锁到
    2 倍周期；峰间距中位数没有这个毛病。峰太少时退回 span/(N-1)。
    """
    p = prof - np.percentile(prof, 10)
    if p.max() <= 0:
        return None
    pk = _find_peaks(p, distance=3, rel_prom=0.12)
    if len(pk) >= 4:
        d = np.diff(pk).astype(np.float32)
        d = d[d <= np.percentile(d, 80) * 1.3]        # 丢掉跨排的坏间隔
        if len(d) >= 3:
            return float(np.median(d))
    return None


def _scan_phase(prof_s, P, n):
    """固定 P，用一次卷积算出**所有**起点 c 的齿/隙对比，返回 (最佳 c, 得分)。

    np.correlate(prof_s, ker) 的第 c 项就是 Σ_j prof_s[c+j]*ker[j]，
    把 ker 在 k*P 处置 1/(n) 就得到「齿位均值」；再减一个偏置 P/2 的核
    得到「间隙均值」。两个核都是稀疏的，整段扫描只花一次 C 级卷积。
    """
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
    """评估一个候选相位，返回 (判别分, 对比度, 最暗齿/中位齿)。

    为什么需要它
    ------------
    ``_comb_contrast`` 对"整排平移一个齿距"几乎不敏感：齿位与缝隙同时平移一格，
    两项相减还是同一个数（5 号图左排实测 0.596 vs 0.580，只差 0.016，方向判错）。
    所以对比度只能定到"相位模 P 任意"，必须另找判据来区分整格。

    试过两个不行的判据：
      * "齿是不是局部极大" —— 整格平移后齿正好落在**隔壁那根引脚**的峰上，
        照样是局部极大，区分不出来；而且齿位偏 1~2 px 就会被判成不是极大，噪声大。
      * "Σ 缺齿量" —— 32 齿里只有 1 齿露在阵列外，占总和 2%，淹在噪声里。

    真正稳的是**最暗那一齿的相对高度**：
      * 正确相位：32 齿全落在引脚上，最暗齿 ≈ 中位齿；
      * 错一格：末端那一齿露到引脚阵列外，相对高度直接塌掉 —— 一个齿就够判。

    实测 7 张图 ×4 侧的分布（D:/207/shujuji/F，见 out/batch7c）：
        正确相位 rmin = 0.76 ~ 0.94
        ±1 格   rmin = 0.21 ~ 0.50        ← 两簇完全不重叠
    门槛取 0.70（两簇中点是 0.63，取 0.70 更保守），线性压分：
        pen = min(1, rmin / 0.70)
    正确相位 pen 恒为 1，不干扰对比度排序；错格相位被压到 0.3~0.7。
    """
    L = len(prof_s) - 1
    a = _teeth(c, P, n).astype(np.float64)
    if a[0] < 0 or a[-1] > L:
        return 0.0, -1e9, 0.0
    ia = np.clip(np.floor(a).astype(np.int32), 0, L - 1)
    fa = a - ia
    vals = (prof_s[ia] * (1.0 - fa) + prof_s[ia + 1] * fa).astype(np.float64)
    med = float(np.median(vals))
    ct = _comb_contrast(prof_s, c, P, n)
    if med <= 1e-9:
        return 0.0, ct, 0.0
    rmin = float(vals.min() / med)
    pen = min(1.0, rmin / 0.70)          # 最暗齿塌下去才压分
    return ct * pen, ct, rmin


def fit_comb(prof, n, margin_px=0.0, pitch_lock=None, lock_tol=0.015):
    """解 (c, P)：n 齿等间距梳拟合。返回 dict(c, P, score, hint, how)。

    ``pitch_lock`` 给定时只在其 ±``lock_tol`` 内搜 P —— 用于"四排同封装必然同
    间距"的交叉约束：某一侧被框外杂物带偏时，用其余侧的中位间距把它拉回来。
    """
    lo_i = int(round(margin_px))
    hi_i = len(prof) - 1 - int(round(margin_px))
    prof_s = _smooth(prof.astype(np.float32), 1.1)

    span = (hi_i - lo_i) / float(max(1, n - 1))     # 阵列铺满框时的 pitch（上限口径）
    geom = ARRAY_FRAC * span                        # 几何先验：32 脚阵列只占边长 ~2/3
    hint = _pitch_hint(prof)
    how = "peaks"
    # 峰间距在低分辨率下不可靠：pitch≈3.2 px 时相邻峰会被 distance=3 的合并规则
    # 并掉，中位数落到 3.0（11 号图实测）——旧的 `3 < hint` 直接把它扔掉，退回
    # span（5.16），搜索区间 [4.1,6.2] 里根本没有真值 3.25，四侧全 0 点。
    # 现在：下界放到 2.0，并用几何先验做合理性检查，出格就改用几何先验。
    if hint is None or not (2.0 <= hint <= 1.6 * span) \
            or not (0.80 * geom <= hint <= 1.35 * geom):
        if hint is not None and 2.0 <= hint <= 1.6 * span:
            how = "peaks-off"                       # 峰给了值但离几何先验太远
        else:
            how = "geom"
        hint = geom

    if pitch_lock:
        hint = float(pitch_lock)
        P_lo = hint * (1.0 - lock_tol)
        P_hi = hint * (1.0 + lock_tol)
    else:
        # 区间取「峰间距 ±20%」与「几何先验 ±20%」的并集：峰间距可能被相邻峰
        # 合并带偏 10~20%，几何先验也可能因框画松而偏，取并集两者都兜住。
        P_lo = min(hint * (1.0 - PITCH_TOL), geom * (1.0 - PITCH_TOL))
        P_hi = max(hint * (1.0 + PITCH_TOL), geom * (1.0 + PITCH_TOL))
        P_lo = max(P_lo, 2.0)
        P_hi = min(P_hi, span * 1.05)
    best = (-1e18, None, None)
    for P in np.arange(P_lo, P_hi + 1e-9, 0.02):
        r = _scan_phase(prof_s, P, n)
        if r is None:
            continue
        if r[1] > best[0]:
            best = (r[1], r[0], P)
    if best[1] is None:
        return None
    # 局部精化：P 与 c 都做细步长爬山
    _, c, P = best
    for _ in range(4):
        for dc in np.arange(-0.70, 0.71, 0.05):
            for dP in np.arange(-0.06, 0.061, 0.005):
                sc = _comb_contrast(prof_s, c + dc, P + dP, n)
                if sc > best[0]:
                    best = (sc, c + dc, P + dP)
        _, c, P = best

    # 相位消歧：对比度只能定到"相位模 P"，整排平移 k 个齿距得分几乎一样。
    # 在 ±2 格内挑"最暗一齿也不塌"的那个（判据见 _phase_eval）。
    _, c0, P0 = best
    base = _phase_eval(prof_s, c0, P0, n)
    pick = (base[0], c0)
    for k in (-2, -1, 1, 2):
        ck = c0 + k * P0
        sc = _phase_eval(prof_s, ck, P0, n)[0]
        if sc > pick[0] * 1.05:                  # 明显更整齐才改判
            pick = (sc, ck)
    c = pick[1]
    if abs(c - c0) > 1e-6:
        best = (_comb_contrast(prof_s, c, P0, n), c, P0)
        for _ in range(2):                       # 换相位后重精化 P
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
        lo = int(np.floor(a - w * P))
        hi = int(np.ceil(a + w * P)) + 1
        lo = max(0, lo)
        hi = min(L, hi)
        if hi - lo < 3:
            out[k] = a
            continue
        seg = prof[lo:hi].astype(np.float32)
        bl = float(seg.min())
        wgt = seg - bl
        s = float(wgt.sum())
        if s <= 1e-6:
            out[k] = a
            contrast[k] = 0.0
        else:
            idx = np.arange(lo, hi, dtype=np.float32)
            out[k] = float((wgt * idx).sum() / s)
            contrast[k] = float(seg.max() - bl)
        resid[k] = float(a - out[k])
    return out, resid, contrast


def _fix_overshoot(prof, teeth, contrast, resid, P, n, K=5):
    """端点外溢修复：整排梳平移 ±1..K 个齿距，取「掉队齿最少」的相位。

    为什么需要
    ----------
    pitch 小到 3.3 px（引脚宽 1.6 px）时，``_comb_contrast`` 的齿/隙差值只剩
    0.04 —— 相位目标几乎是平的，相位可能整体偏好几格。偏了的直接症状是
    **末端 2~4 齿落进引脚阵列外面**：实测 11 号图 F.3 右排末 3 齿的逐齿对比度
    0.06 / 0.03 / 0.02，其余 0.3~1.2；F.4 左排同样末 3 齿 0.06 / 0.04 / 0.04。
    这两侧因此被判"间距不规则"，整排 0 点。

    判据为什么不用 ``_phase_eval`` 的 rmin
    --------------------------------------
    整排一起偏到缝隙上时 32 齿都很"平"，rmin 反而降不下去；而齿/隙差在这种
    极窄 duty 下可能为负（实测偏相位 ct=-0.013、rmin=0.70）——两项都不可靠。
    改用**掉队齿计数**：逐齿对比度 < 0.40×中位对比度 记为掉队。掉队越少越好，
    平手取中位对比度大的。只平移、不改 P，所以已经对齐的排（掉队 0）原样返回。
    """
    med = float(np.median(contrast)) if contrast.size else 0.0
    if med <= 0:
        return teeth, contrast, resid
    base = int(np.sum(contrast < 0.40 * med))
    if base == 0:
        return teeth, contrast, resid
    best = (base, -med, 0)
    for k in list(range(-K, 0)) + list(range(1, K + 1)):
        c0 = float(teeth[0]) + k * P
        if c0 < 0 or c0 + P * (n - 1) > len(prof) - 1:
            continue
        t2, _, c2 = refine_teeth(prof, c0, P, n)
        m2 = float(np.median(c2))
        if m2 <= 0:
            continue
        b2 = int(np.sum(c2 < 0.40 * m2))
        if (b2, -m2) < (best[0], best[1]):
            best = (b2, -m2, k)
    k = best[2]
    if k == 0:
        return teeth, contrast, resid
    c0 = float(teeth[0]) + k * P
    t2, _, c2 = refine_teeth(prof, c0, P, n)
    r2 = (c0 + P * np.arange(n, dtype=np.float32)) - t2
    return t2, c2, r2


# --------------------------------------------------------------------------- #
# 主识别
# --------------------------------------------------------------------------- #
def _fit_side(ctx, side, n, margin, pitch_lock=None):
    """单侧：定行带 → 沿边剖面 → N 齿梳 → 亚像素精化 → 深度 → 图像坐标。

    行带按 ``ctx.bands()`` 的候选顺序逐个试，**第一个自检通过的接管**。
    主带（旧的 argmax 带）合格时行为与旧版完全一致 —— 既有好侧不受影响。
    """
    tried = []
    first_fail = None
    for band, binfo in ctx.bands(side):
        r = _fit_band(ctx, side, n, margin, band, binfo, pitch_lock)
        tried.append(dict(band=list(band), ok=bool(r.get("ok")),
                          reason=r.get("reason", ""),
                          P=(round(r["fit"]["P"], 3) if r.get("fit") else None),
                          cv=round(r.get("cv", 9.9), 4),
                          ct=round(r.get("ct_min", 0.0), 4)))
        if r.get("ok"):
            r["tried"] = tried
            return r
        if first_fail is None:
            first_fail = r
    first_fail["tried"] = tried
    return first_fail


def _fit_band(ctx, side, n, margin, band, binfo, pitch_lock=None):
    prof, (alo, thick) = ctx.along_profile(side, band)
    if prof.size < max(8, n * 2):
        return dict(ok=False, reason="profile too short", band=band, thick=thick)
    fit = fit_comb(prof, n, margin_px=margin, pitch_lock=pitch_lock)
    if fit is None:
        return dict(ok=False, reason="comb fit failed", band=band, thick=thick)

    teeth, resid, contrast = refine_teeth(prof, fit["c"], fit["P"], n)
    teeth, contrast, resid = _fix_overshoot(prof, teeth, contrast, resid, fit["P"], n)
    # 沿边方向抖动平滑：pitch 只有 3~4 px 时，逐齿亚像素重心的噪声有 ±0.5~1 px
    # （= 0.15~0.3 个齿距），而引脚阵列本身是等间距的 —— 这部分是噪声，不是几何。
    # 抖动超过 0.10P 就改用 32 点的稳健直线（剔除离群后最小二乘），
    # 噪声按 √32 收敛到 ~0.1 px，顺便把 (c,P) 换成这条直线的截距/斜率。
    # 抖动小的侧（好图 CV≤0.05）原样不动。
    kk = np.arange(n, dtype=np.float64)
    smoothed = False
    cv_raw = 9.9
    t_raw = teeth.copy()
    if n >= 8:
        dr = (teeth - fit["c"] - fit["P"] * kk).astype(np.float64)
        jt = 1.4826 * float(np.median(np.abs(dr - np.median(dr))))
        sp0 = np.diff(teeth)
        if sp0.size and sp0.mean() > 1e-6:
            cv_raw = float(sp0.std() / sp0.mean())
        if jt > 0.10 * fit["P"]:
            al, be, _ = _fit_row_line(kk, teeth)
            fit = dict(fit)
            fit["P"] = float(al)
            fit["c"] = float(be)
            teeth = (al * kk + be).astype(np.float32)
            resid = (t_raw - teeth).astype(np.float32)
            smoothed = True
    fit = dict(fit)
    fit.setdefault("c", float(teeth[0]))
    # ⚠ teeth 是**相对** alo 的沿边坐标（prof 从 along_range 的 a0 起算），
    # 而 depth_of 取的是 Wn 的绝对列/行 —— 必须 +alo，否则读的是"图像第 x0+k 列"，
    # 也就是隔壁那颗芯片的引脚。96 个侧统计：漏加 alo 时逐脚深度跨度中位 9.2 px
    # （最大 27.5），补上后 2.5 px（最大 8.0）；下面 _fit_row_line 还会把这段
    # 乱跳固化成一条斜坡（"引脚排是正的、点列是斜的"就是这么来的）。
    dep_raw = np.array([ctx.depth_of(side, t + alo, band) for t in teeth], np.float64)
    alpha, beta, _ = _fit_row_line(np.arange(n, dtype=np.float64), dep_raw)
    dep = alpha * np.arange(n, dtype=np.float64) + beta
    dep_res = float(np.abs(dep_raw - dep).max()) if n else 0.0

    # 沿边 / 深度 → 图像坐标（像素下标口径，与 depth_index 一致）
    X0, Y0 = int(round(ctx.x0)), int(round(ctx.y0))
    X1, Y1 = int(round(ctx.x1)), int(round(ctx.y1))
    rows = []
    for k in range(n):
        a = float(teeth[k]) + alo
        d = float(dep[k])
        if side == "top":
            x, y = a, Y0 + d
        elif side == "bottom":
            x, y = a, Y1 - 1 - d
        elif side == "left":
            x, y = X0 + d, a
        else:
            x, y = X1 - 1 - d, a
        rows.append((float(np.clip(x, ctx.x0, ctx.x1)),
                     float(np.clip(y, ctx.y0, ctx.y1)),
                     float(contrast[k]), float(resid[k])))

    if side in ("top", "bottom"):
        rows.sort(key=lambda r: r[0])          # 上/下排：左→右
    else:
        rows.sort(key=lambda r: r[1])          # 左/右排：上→下

    key = 0 if side in ("top", "bottom") else 1
    sp = np.diff([r[key] for r in rows])
    cv = float(sp.std() / max(1e-6, sp.mean())) if sp.size else 9.9
    # jit = 相邻间距的稳健抖动（px）。CV 就是 jit/P，**同一个 jit 在小 pitch 图上
    # 会被放大**：pitch 4 px 时 ±0.5 px 的亚像素抖动就是 CV 0.12，明明 32 齿
    # 全压在引脚上也会被判"间距不规则"（13 号图右排 CV 0.168、上排 0.124，
    # 对比度分别是 0.35 / 0.51 —— 高度可信却过不了门）。所以门限是
    # 「CV 合格」**或**「对比度足够高（每根齿确实压在亮脊上）且抖动在可解释范围」。
    # 真正的垃圾带两种口径都过不了：ct_min 只有 0.01~0.05。
    jit = float(np.median(np.abs(sp - np.median(sp)))) * 1.4826 if sp.size else 9.9
    ct_min = float(np.min(contrast)) if contrast.size else 0.0
    ct_med = float(np.median(contrast)) if contrast.size else 0.0
    ok = (ct_min >= 0.10) and (ct_med >= 0.20) and (fit["P"] > 2.0) and \
         (cv_raw <= 0.12 or (ct_min >= 0.18 and cv_raw <= 0.40))
    return dict(
        ok=bool(ok), band=band, thick=thick, rows=rows, fit=fit,
        locked=(pitch_lock is not None),
        cv=cv, cv_raw=cv_raw, jit=jit, ct_min=ct_min, ct_med=ct_med, smooth=smoothed,
        resid_max=float(np.abs(resid).max()) if resid.size else 0.0,
        dep_resid_max=dep_res,
        tilt=float(np.degrees(np.arctan(alpha / max(1e-6, fit["P"])))),
        reason="" if ok else ("spacing irregular" if cv_raw > 0.12 else "weak contrast"),
    )


def detect_F(img, box, n=NF, opt=None):
    """F 类识别：四排，每排 n 个。

    返回 (pins, counts, info)。pins 每项 dict(x, y, side, idx)，
    idx 是该排内的序号（1 起，阅读顺序：上/下 左→右，左/右 上→下）。

    两遍：
      ① 四侧各自独立解 (c, P)；
      ② 四排同封装必然同间距 —— 取四侧 P 的中位数，偏离 >6% 的那侧用中位
         P 重解（±1.5% 内搜）。这一步专门救"某一侧被框外杂物带偏"：实测框
         放宽 5 px 时底排会滑到框外、P 变成 7.56（真值 9.30）却仍报 32 个点。
    """
    o = dict(n=n)
    if opt:
        o.update(opt)
    n = int(o["n"])
    ctx = FCtx(img, box, n=n)
    margin = EDGE_MARGIN * max(ctx.W, ctx.H)

    res = {}
    for side in SIDES:
        res[side] = _fit_side(ctx, side, n, margin)

    good = [r["fit"]["P"] for r in res.values() if r.get("ok") and r.get("fit")]
    P_med = float(np.median(good)) if len(good) >= 2 else None
    if P_med:
        for side in SIDES:
            r = res[side]
            if r.get("fit") and abs(r["fit"]["P"] - P_med) / P_med > 0.06:
                r2 = _fit_side(ctx, side, n, margin, pitch_lock=P_med)
                if r2.get("fit"):
                    res[side] = r2

    pins = []
    counts = {}
    info = dict(n=n, sigma=round(ctx.sigma, 2), base=round(ctx.base, 4),
                pitch_consensus=(round(P_med, 4) if P_med else None),
                sides={}, failed=False, notes=[])
    for side in SIDES:
        r = res[side]
        if not r.get("ok"):
            counts[side] = 0
            info["sides"][side] = dict(band=r.get("band"), ok=False,
                                       reason=r.get("reason", "failed"),
                                       bands_tried=r.get("tried"),
                                       pitch=(round(r["fit"]["P"], 4) if r.get("fit") else None))
            if r.get("fit"):
                info["notes"].append("%s 侧不可信：%s（P=%.2f，CV=%.3f，对比度 %.2f）"
                                     % (side, r["reason"], r["fit"]["P"], r["cv"], r["ct_min"]))
            continue
        for k, (x, y, ct, rs) in enumerate(r["rows"], 1):
            pins.append(dict(x=round(x, 2), y=round(y, 2), side=side, idx=k))
        counts[side] = len(r["rows"])
        info["sides"][side] = dict(
            band=r["band"], thick=r["thick"], ok=True, locked=r["locked"],
            band_alt=(len(r.get("tried") or []) > 1), bands_tried=r.get("tried"),
            pitch=round(r["fit"]["P"], 4), pitch_hint=round(r["fit"]["hint"], 3),
            how=r["fit"]["how"], score=round(r["fit"]["score"], 5),
            cv=round(r["cv"], 4), cv_raw=round(r["cv_raw"], 4),
            smoothed=bool(r.get("smooth")), resid_max=round(r["resid_max"], 3),
            depth_tilt_deg=round(r["tilt"], 3),
            depth_resid_max=round(r["dep_resid_max"], 3),
            contrast_min=round(r["ct_min"], 4),
        )

    info["total"] = len(pins)
    return pins, counts, info


def order_by_side(pins):
    out = {}
    for s in SIDES:
        out[s] = sorted([p for p in pins if p["side"] == s], key=lambda p: p["idx"])
    return out

def imread_u(path):
    """读图（兼容中文路径）。"""
    with open(path, "rb") as f:
        buf = np.frombuffer(f.read(), np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)
