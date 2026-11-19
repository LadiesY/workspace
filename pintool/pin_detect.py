"""PCB pin localization from labelme chip boxes (v12).

User-facing rules
-----------------
  * every pin is INSIDE its annotated rectangle;
  * the dot sits ON the pin (centred on the solder pad) - never in the gap
    between pins, never on the chip body, never on the board in front of the
    pad;
  * packages, leads and pitches vary ~10x, so every stage is scale-adaptive.

Pipeline
--------
1. SOLDER mask: ``V > 95 AND chroma < 95``.  Chroma (= max-min channel) rejects
   the coloured solder mask (green or red board) and keeps grey metal; the V
   floor rejects the black epoxy body and the shadows.
2. BAND (depth of the pin row).  The pins are by construction the FIRST solder
   seen when walking inwards from the box edge; the run-end percentile gives a
   data-driven estimate which is then REFINED BY SCORING: a handful of candidate
   depths is tried and the one that yields the best comb quality wins.  That
   matters on parts whose metal shield is brighter than their own pads (the
   EBYTE module): a band that reaches the body produces an irregular profile and
   therefore loses.
3. PROFILE: integrate the mask over the band -> one pin = one peak, whatever the
   threshold does to the individual blobs.
4. COMB SCORE: ``q = mass * contrast^2 * (0.3+0.7*reg) * (0.4+0.6*cov)``.
   ``mass`` is absolute (sum of peak heights / band) so a faint junk edge cannot
   win by being self-consistent; ``contrast^2`` kills broad bright bands
   (silkscreen, adjacent pads merged at a low threshold).
5. ORIENTATION: label 1 -> strongest single edge; label 2 -> strongest opposite
   pair (L/R or T/B); label 4 -> all four edges.
6. GAP FILL: empty slots of the fitted comb that carry real evidence are
   restored, so a dim pin inside an otherwise regular row is not lost.  A
   candidate must be a genuine local maximum, so a shoulder or a package corner
   is never promoted into a phantom pin.
7. LOCALIZE (v12): the depth of the pin row is found by a MATCHED FILTER over
   the whole box depth and is anchored on PERIODICITY, then each pin is snapped
   onto the row trend.  v11 instead walked in from the box edge and took the
   first bright run, which parks the dot on whatever junk sits between the box
   edge and the pads (silkscreen, solder resist, flux) - the "dot behind the
   pin" complaint, measured at 10-40% of the rows on the affected parts.
"""
import cv2
import numpy as np

ALL_SIDES = ["left", "bottom", "right", "top"]
CCW_SIDES = ("top", "right", "bottom", "left")
THRESHOLDS = [100, 130, 160, 190, 220]

V_LO = 95           # solder: value floor
C_LO = 95           # solder: chroma ceiling
V_GROW = 75         # run growing: softer value floor
BAND_FRACS = (0.09, 0.15, 0.22, 0.31, 0.42)


def chroma_value(img):
    V = img.max(axis=2).astype(np.int16)
    return V, V - img.min(axis=2).astype(np.int16)


# ---- 按图像缓存 V/C ------------------------------------------------------- #
# 一张 3072x2048 的图上跑一遍 chroma_value 要几十毫秒，而一块板上往往有几十个
# 芯片、每个芯片至少要建两次 ``_Ctx``（原始图 + 增强图）。缓存后整板识别快很多。
# 用「数据指针 + shape + strides」做键；只保留最近几张，避免涨内存。
_VC_CACHE = {}
_VC_ORDER = []
_VC_MAX = 6


def _vc_cached(img):
    try:
        key = (img.__array_interface__["data"][0], img.shape, img.strides)
    except Exception:
        return chroma_value(img)
    hit = _VC_CACHE.get(key)
    if hit is not None:
        return hit
    vc = chroma_value(img)
    _VC_CACHE[key] = vc
    _VC_ORDER.append(key)
    while len(_VC_ORDER) > _VC_MAX:
        _VC_CACHE.pop(_VC_ORDER.pop(0), None)
    return vc


def _runs(sig):
    """[(a, b), ...] inclusive runs of True in a 1-D boolean array."""
    idx = np.where(sig)[0]
    if len(idx) == 0:
        return []
    out = []
    s = p = int(idx[0])
    for k in idx[1:]:
        k = int(k)
        if k == p + 1:
            p = k
        else:
            out.append((s, p))
            s = p = k
    out.append((s, p))
    return out


class _Ctx:
    def __init__(self, img, box):
        self.V, self.C = _vc_cached(img)
        H, W = self.V.shape
        x0, y0, x1, y1 = [float(v) for v in box]
        x0, y0 = max(0.0, x0), max(0.0, y0)
        x1, y1 = min(W - 1.0, x1), min(H - 1.0, y1)
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.X0, self.Y0 = int(np.floor(x0)), int(np.floor(y0))
        self.X1, self.Y1 = min(W, int(np.ceil(x1)) + 1), min(H, int(np.ceil(y1)) + 1)
        self.bw, self.bh = x1 - x0, y1 - y0
        self.md = max(self.bw, self.bh)
        self.Wx = max(4, min(W - self.X0, int(round(self.bw)) + 1))
        self.Hy = max(4, min(H - self.Y0, int(round(self.bh)) + 1))
        self._mc, self._pf, self._frs = {}, {}, {}
        self.band = {}                  # side -> chosen band (filled by _orient)

    # ---------------------------------------------------------------- masks -- #
    def mask(self, v, c=C_LO):
        key = (v, c)
        m = self._mc.get(key)
        if m is None:
            sub = self.V[self.Y0:self.Y1, self.X0:self.X1]
            cs = self.C[self.Y0:self.Y1, self.X0:self.X1]
            m = ((sub > v) & (cs < c)).astype(np.uint8)
            m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
            self._mc[key] = m
        return m

    def crop(self, arr):
        """把整图数组裁到 box 区域。

        ``view`` / ``mask`` 都要求「已裁到 box」的输入（``mask`` 内部自己裁），
        从整图直接取 ``arr[:Hy,:Wx]``只会拿到图像左上角，是个隐蔽的坑——实测
        把补点取证全部算成了 0 对比度。"""
        return arr[self.Y0:self.Y1, self.X0:self.X1]

    # ------------------------------------------------------------- geometry -- #
    def _first_run_stats(self, side):
        """(start, end) of the FIRST solder run along the depth, for every
        position along the edge (index 0 = nearest the box edge)."""
        st = self._frs.get(side)
        if st is not None:
            return st
        m = self.mask(V_LO, C_LO)
        Wx, Hy = self.Wx, self.Hy
        lim = int(max(14, min(0.60 * min(self.bw, self.bh), 0.55 * self.md)))
        if side in ("left", "right"):
            lim = int(min(lim, Wx))
            M = m[:Hy, :lim] if side == "left" else m[:Hy, max(0, Wx - lim):Wx][:, ::-1]
        else:
            lim = int(min(lim, Hy))
            M = m[:lim, :Wx].T if side == "top" else m[max(0, Hy - lim):Hy, :Wx][::-1, :].T
        M = M.astype(bool)
        if M.size == 0 or not M.any():
            self._frs[side] = (np.array([], int), np.array([], int), lim)
            return self._frs[side]
        starts = np.where(M.any(axis=1), np.argmax(M, axis=1), -1)
        idx = np.arange(M.shape[1])
        cand = np.where((~M) & (idx[None, :] >= starts[:, None]), idx[None, :], M.shape[1])
        ends = cand.min(axis=1) - 1
        v = starts >= 0
        self._frs[side] = (starts[v], ends[v], lim)
        return self._frs[side]

    def band_candidates(self, side):
        """Depth candidates: fractions of the box plus the measured first-run
        end.  The winner is picked by comb quality in _orient."""
        m0 = min(self.bw, self.bh)
        cap = min(0.60 * m0, max(10.0, self.Wx if side in ("left", "right") else self.Hy) - 1)
        cands = set()
        for f in BAND_FRACS:
            cands.add(float(np.clip(f * m0, 8.0, cap)))
        starts, ends, lim = self._first_run_stats(side)
        if len(ends) >= 4:
            for pct in (75.0, 90.0):
                cands.add(float(np.clip(np.percentile(ends, pct) + 4.0, 8.0, cap)))
        return sorted(cands)

    def profile(self, side, T, band):
        ck = (side, T, int(round(band)))
        p = self._pf.get(ck)
        if p is not None:
            return p
        b = max(3, int(round(band)))
        m = self.mask(T, C_LO)
        Wx, Hy = self.Wx, self.Hy
        if side == "left":
            prof = m[:Hy, :b].sum(axis=1)
        elif side == "right":
            prof = m[:Hy, max(0, Wx - b):Wx].sum(axis=1)
        elif side == "top":
            prof = m[:b, :Wx].sum(axis=0)
        else:
            prof = m[max(0, Hy - b):Hy, :Wx].sum(axis=0)
        prof = np.convolve(prof.astype(float), np.ones(3) / 3.0, mode="same")
        self._pf[ck] = prof
        return prof

    def line(self, side, i, n):
        """(V, C) along depth at index i; element 0 = nearest the box edge."""
        Wx, Hy, X0, Y0 = self.Wx, self.Hy, self.X0, self.Y0
        n = int(max(2, min(n, (Wx if side in ("left", "right") else Hy))))
        if side == "left":
            ys = Y0 + i
            return self.V[ys, X0:X0 + n], self.C[ys, X0:X0 + n]
        if side == "right":
            ys = Y0 + i
            return (self.V[ys, X0 + Wx - n:X0 + Wx][::-1],
                    self.C[ys, X0 + Wx - n:X0 + Wx][::-1])
        if side == "top":
            xs = X0 + i
            return self.V[Y0:Y0 + n, xs], self.C[Y0:Y0 + n, xs]
        xs = X0 + i
        return (self.V[Y0 + Hy - n:Y0 + Hy, xs][::-1],
                self.C[Y0 + Hy - n:Y0 + Hy, xs][::-1])

    def view(self, side, arr, n):
        """``arr`` re-indexed as (along, depth); depth 0 = nearest the box edge.

        This is the same orientation ``line`` uses, extended to the whole row,
        which is what the matched-filter depth search needs."""
        Wx, Hy = self.Wx, self.Hy
        A = arr[:Hy, :Wx]
        n = int(max(2, min(int(n), (Wx if side in ("left", "right") else Hy))))
        if side == "left":
            return np.ascontiguousarray(A[:, :n])
        if side == "right":
            return np.ascontiguousarray(A[:, Wx - n:Wx][:, ::-1])
        if side == "top":
            return np.ascontiguousarray(A[:n, :].T)
        return np.ascontiguousarray(A[Hy - n:, :][::-1, :].T)

    def edge_len(self, side):
        return self.bh if side in ("left", "right") else self.bw


# --------------------------------------------------------------------------- #
#  peak finding and comb scoring
# --------------------------------------------------------------------------- #
def _peaks(prof, smooth=3, saddle_ratio=0.70):
    """Local maxima + topographic prominence, shallow saddles merged.

    A gull-wing lead has a bright toe and a bright heel with a dimmer waist, so
    one physical pin can split into two peaks - exactly why dots used to land in
    the gaps.  Real neighbours are separated by a DEEP saddle."""
    n = len(prof)
    if n < 5:
        return [], []
    p = np.convolve(prof, np.ones(smooth) / float(smooth), mode="same")
    cand = [i for i in range(1, n - 1) if p[i] >= p[i - 1] and p[i] >= p[i + 1]]
    if p[0] >= p[1]:
        cand = [0] + cand
    if p[-1] >= p[-2]:
        cand = cand + [n - 1]
    if not cand:
        return [], []
    cand.sort(key=lambda i: -p[i])
    keep = []
    for i in cand:
        if all(abs(i - j) >= 2 for j in keep):
            keep.append(i)
    keep.sort()
    merged = [keep[0]]
    for i in keep[1:]:
        a = merged[-1]
        if i - a <= 2:
            if p[i] > p[a]:
                merged[-1] = i
            continue
        saddle = float(p[a:i + 1].min())
        if saddle >= saddle_ratio * min(p[a], p[i]):
            if p[i] > p[a]:
                merged[-1] = i
        else:
            merged.append(i)
    prom = []
    for k, i in enumerate(merged):
        left = p[merged[k - 1]:i].min() if k > 0 else (p[:i].min() if i > 0 else p[i])
        right = p[i:merged[k + 1] + 1].min() if k < len(merged) - 1 else p[i:].min()
        prom.append(float(p[i] - max(left, right)))
    return merged, prom


def _peaks_adaptive(prof, pitch_hint=None):
    """Dense-pitch aware peak finder.

    ``_peaks`` merges two maxima whose saddle is shallower than 0.70 of the
    smaller peak.  That is right for gull-wing leads (toe+heel), but on a DENSE
    row (pitch ~= 5 px, e.g. the SSOP-33 in V5) neighbouring pads bloom into each
    other and the real saddle drops below 0.70 -> adjacent pins get MERGED and
    half the row is lost.  The fix: when the row is known to be dense, lower the
    merge threshold so genuine neighbours survive.

    ``pitch_hint`` is the running median of local peak spacing; the saddle bar
    is scaled between SADDLE_WIDE (0.70, wide pitch) and SADDLE_DENSE (0.35,
    dense pitch)."""
    n = len(prof)
    if n < 5:
        return [], []
    p = np.convolve(prof, np.ones(3) / 3.0, mode="same")
    cand = [i for i in range(1, n - 1) if p[i] >= p[i - 1] and p[i] >= p[i + 1]]
    if p[0] >= p[1]:
        cand = [0] + cand
    if p[-1] >= p[-2]:
        cand = cand + [n - 1]
    if not cand:
        return [], []
    cand.sort(key=lambda i: -p[i])
    keep = []
    for i in cand:
        if all(abs(i - j) >= 2 for j in keep):
            keep.append(i)
    keep.sort()
    # 估计典型间距：相邻候选的间距中位数（去掉过小的）
    sp = np.diff(np.array(sorted(keep), float))
    sp = sp[sp >= 2]
    med_sp = float(np.median(sp)) if len(sp) else 10.0
    # 间距越小 -> 允许的鞍部越浅（越不容易合并）
    if med_sp <= 7.0:
        saddle_ratio = 0.35
    elif med_sp >= 14.0:
        saddle_ratio = 0.70
    else:
        saddle_ratio = 0.35 + (med_sp - 7.0) / 7.0 * 0.35
    merged = [keep[0]]
    for i in keep[1:]:
        a = merged[-1]
        if i - a <= 2:
            if p[i] > p[a]:
                merged[-1] = i
            continue
        saddle = float(p[a:i + 1].min())
        if saddle >= saddle_ratio * min(p[a], p[i]):
            if p[i] > p[a]:
                merged[-1] = i
        else:
            merged.append(i)
    prom = []
    for k, i in enumerate(merged):
        left = p[merged[k - 1]:i].min() if k > 0 else (p[:i].min() if i > 0 else p[i])
        right = p[i:merged[k + 1] + 1].min() if k < len(merged) - 1 else p[i:].min()
        prom.append(float(p[i] - max(left, right)))
    return merged, prom


def _evaluate2(ctx, side, T, band):
    """（保留旧名以兼容）直接复用原打分，密排增强改为「选参后补峰」。

    经验教训：把自适应峰值直接塞进打分公式会改变 (band,T) 的相对优劣，
    反而选到把整排当成一个宽峰的参数、峰数更少。所以选参必须完全沿用
    调优过的 _evaluate，密排修复放在 _recover_dense 里做后处理。"""
    return _evaluate(ctx, side, T, band)


def _evaluate(ctx, side, T, band):
    """(quality, teeth, pitch, None, prof) for one (edge, threshold, band)."""
    prof = ctx.profile(side, T, band)
    peaks, prom = _peaks(prof)
    if len(peaks) < 3:
        return 0.0, [], None, None, prof
    pmax = max(prom)
    if pmax <= 0:
        return 0.0, [], None, None, prof
    sel = [k for k in range(len(peaks)) if prom[k] >= 0.18 * pmax]
    if len(sel) < 3:
        return 0.0, [], None, None, prof
    teeth = [peaks[k] for k in sel]
    hgt = np.array([prof[i] for i in teeth], float)
    prm = np.array([prom[k] for k in sel], float)
    contrast = float(np.clip(prm.mean() / max(1e-9, hgt.mean()), 0.0, 1.0))
    gaps = np.diff(np.array(teeth, float))
    gaps = gaps[gaps > 2]
    if len(gaps) < 2:
        return 0.0, [], None, None, prof
    pitch = float(np.median(gaps))
    if pitch <= 3:
        return 0.0, [], None, None, prof
    reg = float(np.mean(np.abs(gaps - pitch) < 0.30 * pitch))
    el = ctx.edge_len(side)
    cov = float(np.clip((teeth[-1] - teeth[0] + pitch) / max(1.0, el), 0.0, 1.0))
    mass = float(hgt.sum()) / max(3.0, band)
    q = mass * (contrast ** 2) * (0.3 + 0.7 * reg) * (0.4 + 0.6 * cov)
    return q, teeth, pitch, None, prof


def _orient(ctx, label):
    """Pick the sides; for each side the best (band, threshold, teeth)."""
    best = {}
    for s in ALL_SIDES:
        bq, bdata = -1.0, (0.0, 130, None, [], None, None)
        cands = ctx.band_candidates(s)
        for band in cands:
            for T in THRESHOLDS:
                q, teeth, p, phi, prof = _evaluate(ctx, s, T, band)
                if q > bq:
                    bq, bdata = q, (q, T, band, teeth, p, prof)
        best[s] = (bq, bdata)
        ctx.band[s] = bdata[2] if bdata[2] is not None else cands[0]
    q = {s: best[s][0] for s in ALL_SIDES}
    if label == "4":
        sides = list(ALL_SIDES)
    elif label == "2":
        sides = (["left", "right"]
                 if q["left"] + q["right"] >= q["top"] + q["bottom"]
                 else ["top", "bottom"])
    else:
        sides = [max(ALL_SIDES, key=lambda s: q[s])]
    return sides, best, q


def _orient2(ctx, label, use_geo_classify=True):
    """Dense-pitch aware orient + LABEL+GEO automatic re-classification.

    Problem 1 (non-rectangular packages): the YOLO label says 2 (two-sided) or
    4 (four-sided), but the actual part may be the other one.  A 2-sided SOP
    that the model stamped F still gets all four edges probed and two of them
    produce junk teeth; a QFP stamped T gets only two edges and loses the other
    two.  Rather than trusting the label blindly we also look at the EVIDENCE:
    how many edges carry a real comb, and how strong it is.

    Rules (evidence first, label as prior):
      * 4 edges carry a credible comb  -> four-sided  (upgrade a stray T)
      * 2 edges carry a credible comb  -> two-sided   (downgrade a stray F)
      * otherwise fall back to the label.
    """
    best = {}
    allc = {}
    for s in ALL_SIDES:
        bq, bdata = -1.0, (0.0, 130, None, [], None, None)
        cands = ctx.band_candidates(s)
        pool = []
        for band in cands:
            for T in THRESHOLDS:
                q, teeth, p, phi, prof = _evaluate2(ctx, s, T, band)
                pool.append((q, T, band, list(teeth), p, prof))
                if q > bq:
                    bq, bdata = q, (q, T, band, teeth, p, prof)
        best[s] = (bq, bdata)
        allc[s] = pool
        ctx.band[s] = bdata[2] if bdata[2] is not None else cands[0]
    q = {s: best[s][0] for s in ALL_SIDES}

    # ---- 几何重新分类 ----------------------------------------------------
    # 设计原则：label 是 YOLO 训练的先验，默认相信它；只有在几何证据
    # 「明显更强」时才纠正，避免把好端端的两侧 SOP 误升成四边。
    qs = sorted(q.values(), reverse=True)
    qmax = qs[0] if qs else 0.0

    def sides_present(s):
        return q[s] >= 0.55 * qmax and len(best[s][1][3]) >= 4

    n_present = sum(1 for s in ALL_SIDES if sides_present(s))
    lr = sides_present("left") or sides_present("right")
    tb = sides_present("top") or sides_present("bottom")
    n_pairs = int(lr) + int(tb)

    src = "label"
    if use_geo_classify and label == "4":
        # 标 4 边但只有一对边有真实梳齿（另一对几乎空白）-> 实为两侧封装
        if n_present <= 2 and n_pairs == 1:
            sides = (["left", "right"] if lr else ["top", "bottom"])
            src = "geo:4->2"
        else:
            sides = list(ALL_SIDES)
    elif use_geo_classify and label == "2":
        # 标 2 边：默认信任。只有当「另一对」两侧都很强、且当前这对
        # 明显更弱时才换成另一对（选错轴），但绝不升级成四边。
        sides = (["left", "right"]
                 if q["left"] + q["right"] >= q["top"] + q["bottom"]
                 else ["top", "bottom"])
        alt = (["top", "bottom"] if sides == ["left", "right"]
               else ["left", "right"])
        cur = q[sides[0]] + q[sides[1]]
        altv = q[alt[0]] + q[alt[1]]
        if altv > 1.5 * max(cur, 1e-9) and altv >= 0.55 * qmax:
            sides = alt
            src = "geo:swap"
    else:
        sides = [max(ALL_SIDES, key=lambda s: q[s])]
    return sides, best, q, src, allc


def _partner_tooth_fill(ctx, sides, best):
    """用同封装「对边」的规则栅格补齿。

    为什么需要：同一封装对边的引脚数、间距必然相同，但两侧证据强度常常不对称——
    一侧的相邻两峰会粘连成一个宽峰（实测 V5.(1)#17 右侧：pitch 13 的 4 个焊盘在
    profile 里被并成 3 个峰，缺口判据根本看不到「异常间距」），或者本侧部分焊盘
    处在阴影里、声势(prominence)低于 0.18*pmax 的门槛而被 `_evaluate` 丢掉
    （实测 V5.(37)#27 左侧）。

    做法：只在「本侧已有齿全部落在对边栅格上（±0.40*pitch）+ 对边更规整且点数
    更多」时启用；每个候选位置（对边的齿位）必须在本侧 profile 上自有局部峰、
    且峰两侧不比自己更亮，才补。这样补出来的是**真齿**（走 `_make_pins` 正常测
    深度），而不是预测点。

    注意：判「同栅格」不能用两侧 pitch 的比值——本侧 pitch 恰恰是被粘连抬高过的
    那个数（V5.(1)#17 右侧 18.5 vs 左侧真实的 11.0），比比值必然被挡住。改成
    「本侧每个齿都能对齐到对边某个齿」就直接绕开了这个自欺。
    """
    for s in list(sides):
        o = OPPOSITE.get(s)
        if o not in sides or o == s:
            continue
        bq_s, (q_s, T_s, band_s, teeth_s, pitch_s, prof_s) = best[s]
        bq_o, (q_o, T_o, band_o, teeth_o, pitch_o, prof_o) = best[o]
        if not teeth_s or not teeth_o or not pitch_o:
            continue
        if len(teeth_s) >= len(teeth_o):
            continue                      # 本侧已不少于对侧，没有可补的
        po = float(pitch_o)
        if po < 3.0:
            continue
        ref = np.array(sorted(float(t) for t in teeth_o), float)
        off = [float(np.min(np.abs(ref - float(t)))) for t in teeth_s]
        if max(off) > 0.40 * po:
            continue                      # 本侧有齿不在对边栅格上 -> 不是同栅格
        go = np.diff(ref)
        go = go[go > 1.5]
        if len(go) < 3:
            continue
        if float(np.mean(np.abs(go - po) < 0.30 * po)) < 0.70:
            continue                      # 对边自己都不规整，不能当标尺
        prof = np.asarray(prof_s, float)
        n = prof.size
        out = sorted(set(int(t) for t in teeth_s if 0 <= int(t) < n))
        vals = [float(prof[t]) for t in out]
        if not vals:
            continue
        base = float(np.median(vals))
        tol = int(max(1, round(0.28 * po)))
        for a in sorted(set(int(round(float(t))) for t in teeth_o)):
            if a <= 0 or a >= n:
                continue
            if min(abs(a - t) for t in out) <= 0.45 * po:
                continue                  # 已有齿，跳过
            lo, hi = max(0, a - tol), min(n, a + tol + 1)
            if hi - lo < 2:
                continue
            seg = prof[lo:hi]
            j = lo + int(np.argmax(seg))
            pk = float(seg.max())
            if pk < 0.55 * base or pk < 2.5:
                continue                  # 峰太弱：本侧这里根本没结构，不硬编
            lv = _peek(prof, a - 0.5 * po, tol)
            rv = _peek(prof, a + 0.5 * po, tol)
            if lv is not None and rv is not None and pk < 0.85 * max(lv, rv):
                continue                  # 旁边更亮 -> 只是宽峰的肩部，不是独立齿
            out.append(j)
            if len(out) >= len(teeth_o):
                break
        new = sorted(set(out))
        if len(new) > len(teeth_s):
            best[s] = (bq_s, (q_s, T_s, band_s, new, pitch_s, prof_s))


def _recover_dense(ctx, side, teeth, pitch, prof, best_all=None, q_now=0.0):
    """Recover pins missed on a DENSE row (problem 2).

    实测根因（V5.(16)#3 左侧）：不是峰值合并算法把峰吃了，而是「按梳齿质量选
    (threshold, band)」在密排时选了过浅的 band（15.4px），窗口只盖到引脚顶部
    的反光、盖不到引脚全长，相邻引脚在 profile 里粘连成 11 个宽峰。同一侧把
    band 加深到 22.7px 就能分出 16 个峰、pitch=5（真实间距），而且质量分
    0.83 只比 1.07 低一点。

    所以正确做法是：在这一侧所有 (T, band) 候选里，找「pitch 明显更小、
    也更规律」且质量分不至于太差的解，用它替换。

    ``best_all`` 为该侧全部 (q, T, band, teeth, pitch, prof)；不给则退回按
    saddle_ratio 重新找峰（兼容旧调用）。"""
    if best_all:
        base = float(pitch or 0)
        if base < 4:
            return teeth, pitch, None, None, False
        # 基准解的「同峰数最优质量」——用于判断密排解是否同样成立。
        # OTF 假阳性实测：密排解质量只有基准解的 0.48~0.54（捡到焊盘外的走线），
        # 而真阳性 V5.(16)#3 的两解质量几乎相当（比值 0.94~0.96）。
        # 故要求密排解质量 >= 基准解的 0.72。
        q_gate = 0.72 * q_now if q_now > 0 else 0.0
        cand = None
        for (q2, T2, band2, te2, p2, pr2) in best_all:
            if not te2 or not p2 or p2 < 2:
                continue
            if q2 < q_gate:
                continue                     # 质量明显不如基准解 → 不是真密排
            # 必须是「密排被合并」的指纹：新 pitch ≈ 原 pitch / k（k>=2）
            k = base / p2
            if k < 1.75 or k > 4.5:
                continue
            kk = int(round(k))
            if abs(k - kk) > 0.30 * kk:
                continue
            # 峰数也必须相应变多（接近 k 倍）
            if len(te2) < len(teeth) * (1 + 0.45 * (kk - 1)):
                continue
            g = np.diff(np.array(sorted(te2), float))
            g = g[g > 1.5]
            if len(g) < 3:
                continue
            reg = float(np.mean(np.abs(g - p2) < 0.35 * p2))
            if reg < 0.60:
                continue
            score = (q2, len(te2), -p2, reg)
            if cand is None or score > cand[0]:
                cand = (score, T2, band2, te2, p2)
        if cand is not None:
            _, T2, band2, te2, p2 = cand
            if len(te2) > len(teeth):
                return sorted(int(t) for t in te2), p2, T2, band2, False
        return teeth, pitch, None, None, False

    # --- 兼容路径：只调 saddle_ratio ---
    if not pitch or pitch < 4 or len(teeth) < 3:
        return teeth, pitch, None, None, False
    for sr in (0.50, 0.40, 0.30, 0.22):
        peaks2, _ = _peaks(prof, saddle_ratio=sr)
        if len(peaks2) <= len(teeth):
            continue
        g2 = np.diff(np.array(sorted(peaks2), float))
        g2 = g2[g2 > 1.5]
        if len(g2) < 4:
            continue
        p2 = float(np.median(g2))
        if p2 < 2:
            continue
        k = int(round(pitch / p2))
        if k < 2 or k > 4:
            continue
        if float(np.mean(np.abs(g2 - p2) < 0.35 * p2)) < 0.70:
            continue
        return sorted(int(p) for p in peaks2), p2, None, None, False
    return teeth, pitch, None, None, False


def _is_peak(prof, pos):
    if pos <= 0 or pos >= len(prof) - 1:
        return 0 <= pos < len(prof)
    return prof[pos] >= prof[pos - 1] and prof[pos] >= prof[pos + 1]


def _fill_slots(teeth, prof, pitch):
    """Restore comb slots that hold real evidence (dim / shadowed pins).

    Non-destructive: existing teeth are never removed.  Candidates must be a
    real local maximum, so the shoulder of a neighbour or the corner of the
    package is never promoted into a phantom pin."""
    if len(teeth) < 2 or not pitch or pitch < 3:
        return list(teeth)
    te = sorted(teeth)
    med = float(np.median([prof[t] for t in te]))
    bar = 0.38 * med
    out = [te[0]]
    for b in te[1:]:
        a = out[-1]
        k = int(round((b - a) / pitch))
        if k >= 2:
            for j in range(1, k):
                pos = int(round(a + j * (b - a) / float(k)))
                if 0 <= pos < len(prof) and prof[pos] >= bar and _is_peak(prof, pos):
                    out.append(pos)
        out.append(b)
    p = int(round(pitch))
    end_bar = 0.70 * med
    for _ in range(2):                      # grow the row at both ends
        nxt = out[-1] + p
        if nxt < len(prof) and prof[nxt] >= end_bar and _is_peak(prof, nxt):
            out.append(nxt)
        prv = out[0] - p
        if prv >= 0 and prof[prv] >= end_bar and _is_peak(prof, prv):
            out.insert(0, prv)
    return sorted(set(out))


def _first_run_depth(lineV, lineC, band, minlen, maxlen):
    """Depth of the pin on this row: centre of the first sufficiently long
    bright run from the box edge, grown outward while the signal stays up.

    ``maxlen`` bounds the growth and is derived from the PITCH, not from the
    band: a gull-wing pad is often deeper than the band that happens to win the
    comb score (e.g. 33 px pad under a 20 px band), and a band-derived cap would
    then park the dot on the pad's brightest speck instead of its centre."""
    seed = (lineV[:band] > V_LO) & (lineC[:band] < C_LO)
    grow = (lineV[:band] > V_GROW) & (lineC[:band] < 115)
    runs = _runs(seed)
    if not runs:
        return None
    pick = None
    for a, b in runs:
        if b - a + 1 >= minlen:
            pick = (a, b)
            break
    if pick is None:
        a, b = max(runs, key=lambda r: r[1] - r[0])
        if b - a + 1 < 2:
            return None
        pick = (a, b)
    a, b = pick
    lo, hi = a, b
    while lo - 1 >= 0 and grow[lo - 1] and (b - lo) < maxlen:
        lo -= 1
    while hi + 1 < band and grow[hi + 1] and (hi - a) < maxlen:
        hi += 1
    return 0.5 * float(lo + hi)


def _row_depth(ctx, side, teeth, pitch, T, band=None):
    """Global depth of the pin row: a matched filter on the known pin positions.

    v11 anchored on the first bright run from the box edge, which fails whenever
    the box is loose: silkscreen, solder resist or flux between the box edge and
    the pads is picked instead and the dot is parked on the board, "behind the
    pin".  Position alone cannot separate the two.

    What separates them is PERIODICITY.  Only a scanline that actually crosses
    the pin row is bright at every pin position and dark halfway between two
    pins; a silkscreen stripe, the body, or the board is uniform.  Score every
    depth by ``on * (1 - off)``.

    The whole box depth is scanned, not a band-derived window (a loose box can
    leave 80 px of slack before the pads), and the FIRST strong peak wins: the
    pins are by construction the first periodic row met walking inwards, so the
    pad row of the opposite side - same teeth positions, same period - is never
    mistaken for this one.
    """
    lim = int(ctx.Wx if side in ("left", "right") else ctx.Hy)
    best = None
    for TT in (T, 130, V_LO):
        if TT is None:
            continue
        m = ctx.mask(int(TT), C_LO).astype(np.float32)
        M = ctx.view(side, m, lim)
        L, N = M.shape
        if L < 4 or N < 3:
            continue
        M = np.apply_along_axis(
            lambda v: np.convolve(v, np.ones(3) / 3.0, mode="same"), 1, M)
        te = sorted(set(int(round(t)) for t in teeth if 0 <= round(t) < L))
        if len(te) < 3:
            continue
        p = max(3.0, float(pitch or 10))
        off = []
        for a, b in zip(te, te[1:]):
            if b - a <= 1.7 * p:
                c = int(round(0.5 * (a + b)))
                if c != a and c != b and 0 <= c < L:
                    off.append(c)
        for e in (te[0] - int(round(0.5 * p)), te[-1] + int(round(0.5 * p))):
            if 0 <= e < L and e not in te:
                off.append(e)
        if not off:
            off = [min(L - 1, max(0, t + max(2, int(round(0.45 * p)))))
                   for t in te]
        A = M[te].mean(axis=0)
        B = M[off].mean(axis=0) if off else np.zeros(N)
        score = np.clip(A - B, 0.0, None) * A
        mx = float(score.max())
        if mx <= 1e-6:
            continue
        if best is None or mx > best[0]:
            best = (mx, TT, score, N)
        if mx >= 0.55:                  # good enough, do not relax further
            break
    if best is None:
        return None
    mx, TT, score, N = best
    # 门槛必须「低」，不能拿全局最强峰来定：真正的引脚排是「从边往里走遇到的
    # 第一排周期亮带」，只要它在近区窗口里没被更强的峰压过就该认它。用
    # 0.45*mx 的高门槛会把「近侧引脚比远侧暗」的芯片直接筛掉——连接器的下排
    # 常年处在阴影里（实测 V5.(5)#0 下排 mask 只有 3px 厚、A=0.58，上排是全亮
    # 的 1.0），于是 row_depth 一路走到对边那一排，两排点重合、真漏检侧永远是
    # 空的（V5.(5)#0 / V5.(9)#4 / V5.(14)#6/#7 的镜像塌陷）。
    thr = max(0.20 * mx, 0.08)
    # 「第一排」不能盲取「第一个够强的深度」：box 边缘附近常有比引脚更亮的丝印/
    # 器件轮廓，它能在深度 1~3px 处凑出一个窄尖峰。实测 V5.(9)#14 顶部：丝印在
    # depth 2 得 0.57、真正的焊盘排在 depth 12 得 1.00，原规则取「第一个
    # >= 0.45*max」就锁死在丝印上，把整排点画到了框边上（RAW 也是这样错的）。
    # 修正：只有当近区 [k, band+0.5*pitch] 里没有比 k 强 1.35 倍的峰时，才认 k。
    # 窗口到 band 为止是安全的——真正的引脚排必然落在 band 内，而对边那排引脚在
    # ~0.9 倍边长处，band <= 0.6*min(bw,bh) 够不着它。
    win = int(round(float(band or 0) + 0.5 * float(pitch or 10)))
    # 近区上限：引脚排必然落在「band + 一点余量」之内。box 画得紧时对边那排引脚
    # 会落在 ~(L-1-band) 处，正好被这个上限挡住——这是「镜像塌陷」的第二道闸门
    # （第一道是上面的低门槛）：实测 V5.(14)#6 下排真引脚被 box 切在框外，扫描
    # 一路走到 depth 91（= 上排的镜像），把整排点画到了上排。
    dcap = int(np.clip(float(band or 0) + 1.5 * float(pitch or 10), 8.0, float(N - 1)))
    sel = np.where(score[:dcap + 1] >= thr)[0]
    hits = sel
    if len(hits) == 0:
        return None
    k = None
    for h in hits:
        h = int(h)
        hi = min(dcap, max(h + 3, win))
        seg = score[h:hi + 1]
        if seg.size == 0 or float(seg.max()) <= 1.35 * float(score[h]):
            k = h
            break
    if k is None:
        k = int(np.argmax(score[:dcap + 1]))
    j = k
    while j + 1 <= dcap and score[j + 1] >= 0.35 * mx:
        j += 1
    mm = k + int(np.argmax(score[k:j + 1]))
    pk = float(score[mm])
    a = b = mm
    while a - 1 >= k and score[a - 1] >= 0.75 * pk:
        a -= 1
    while b + 1 <= j and score[b + 1] >= 0.75 * pk:
        b += 1
    w = score[a:b + 1]
    return float(np.sum(w * np.arange(a, b + 1)) / max(1e-9, float(w.sum())))


def _pin_depth_at(ctx, side, i, n, d, pitch):
    """Local refinement of one pin: centre of the run that holds the row depth.

    Anchored on the row depth (already global-consensus), so a bright blob in
    front of or behind the pad cannot drag the dot away."""
    V1, C1 = ctx.line(side, i, n)
    grow = (V1 > V_GROW) & (C1 < 115)
    m = len(V1)
    k = int(round(d))
    # the row depth is already a global consensus; the local run is only a small
    # per-pin correction, so its budget stays well under one pitch.
    cap = int(max(3, round(0.40 * (pitch or 10))))
    if not (0 <= k < m and grow[k]):
        idx = np.arange(m)
        sel = grow & (np.abs(idx - k) <= cap)
        if not sel.any():
            return None
        k = int(idx[sel][np.argmin(np.abs(idx[sel] - k))])
    lo = hi = k
    # each side gets its OWN budget: a shared budget lets the side that runs
    # out of signal first swallow it and skews the centre off the pad.
    while lo - 1 >= 0 and grow[lo - 1] and (k - lo) < cap:
        lo -= 1
    while hi + 1 < m and grow[hi + 1] and (hi - k) < cap:
        hi += 1
    return 0.5 * float(lo + hi)


def _finalize_depths(teeth, depths, pitch):
    """Snap a pin that fell off its own row onto the row's robust trend.

    All the leads on one side of a package sit at the same depth, so the trend
    is the physical truth; a pin deviating by more than 0.7 pitch has landed on
    junk.  A linear (not constant) trend keeps a rotated box honest."""
    if any(d is None for d in depths) or len(depths) < 3:
        return depths
    T = np.array([float(t) for t in teeth])
    D = np.array([float(d) for d in depths])
    o = np.argsort(T)
    T, D = T[o], D[o]
    k = max(1, len(T) // 3)
    g1, g2, g3 = slice(0, k), slice(k, 2 * k), slice(2 * k, min(len(T), 3 * k))
    b = 0.0
    if g3.stop > g3.start and g1.stop > g1.start:
        x1, y1 = T[g1].mean(), float(np.median(D[g1]))
        x3, y3 = T[g3].mean(), float(np.median(D[g3]))
        if x3 - x1 > 1e-6:
            b = float(np.clip((y3 - y1) / (x3 - x1), -0.08, 0.08))
    base = float(np.median(D - b * T))
    tol = max(3.5, 0.50 * float(pitch or 10))
    tr = base + b * T[o.argsort()]
    return [float(tr[j]) if abs(float(d) - float(tr[j])) > tol else float(d)
            for j, d in enumerate(depths)]


def _make_pins(ctx, side, band, teeth, pitch, T=None):
    band = max(3, int(round(band)))
    minlen = max(3, int(round(0.25 * (pitch or 12))))
    maxlen = max(6, int(round(1.10 * (pitch or 12))))
    lim = ctx.Wx if side in ("left", "right") else ctx.Hy
    n = int(np.clip(max(band + maxlen, int(1.5 * band)), 12, lim))
    Dg = _row_depth(ctx, side, teeth, pitch, T, band=band)
    if Dg is not None:                  # the local run must stay reachable
        n = int(np.clip(max(band + maxlen, Dg + 2 * maxlen), 12, lim))
    depths = []
    for i in teeth:
        if Dg is None:
            V1, C1 = ctx.line(side, i, n)
            depths.append(_first_run_depth(V1, C1, n, minlen, maxlen))
        else:
            depths.append(_pin_depth_at(ctx, side, i, n, Dg, pitch))
    ok = [d for d in depths if d is not None]
    D = float(np.median(ok)) if ok else None
    depths = [d if d is not None else D for d in depths]
    depths = _finalize_depths(teeth, depths, pitch)
    out = []
    for i, d in zip(teeth, depths):
        if d is None:
            continue
        d = float(np.clip(d, 0.0, lim - 1))
        if side == "left":
            x, y = ctx.X0 + d, ctx.Y0 + float(i)
        elif side == "right":
            x, y = ctx.X0 + ctx.Wx - 1 - d, ctx.Y0 + float(i)
        elif side == "top":
            x, y = ctx.X0 + float(i), ctx.Y0 + d
        else:
            x, y = ctx.X0 + float(i), ctx.Y0 + ctx.Hy - 1 - d
        out.append(dict(x=x, y=y, along=float(i), side=side))
    return out


def _dedupe_corners(rows, ctx, sides):
    gaps = []
    for s in sides:
        pos = sorted(t["along"] for t in rows[s])
        gaps += [b - a for a, b in zip(pos, pos[1:]) if b - a > 2]
    pitch = float(np.median(gaps)) if gaps else max(6.0, 0.02 * ctx.md)
    thr = 0.5 * max(4.0, pitch)
    # 真实点优先于预测点：排序时 predicted 排在后面，去重时先占位
    items = sorted(((-{"left": 0, "right": 1, "top": 2, "bottom": 3}[s], s, t)
                    for s in sides for t in rows[s]),
                   key=lambda z: (z[0], z[2].get("predicted", False), z[2]["along"]))
    kept = []
    for _, s, t in items:
        if all((t["x"] - q[0]["x"]) ** 2 + (t["y"] - q[0]["y"]) ** 2 >= thr * thr
               for q in kept):
            kept.append((t, s))
    out = {s: [] for s in sides}
    for t, s in kept:
        out[s].append(t)
    return out


OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}


def _side_axis(side):
    return "y" if side in ("left", "right") else "x"


def _pair_symmetry(rows, s1, s2, ctx):
    """Measure how symmetric two opposite rows are.

    Returns (is_sym, k) where ``k`` = round(n_big / n_small) when the two rows
    look like the same physical row seen twice (same pitch, counts in a 1:k
    ratio), else (False, None).

    Key insight from V5: a pad row is symmetric, so the two opposite edges must
    have the SAME pitch.  When one edge lost pins to a merge, its pitch comes
    out as a small multiple (2x, 3x) of the true one - which is exactly what we
    look for.

    稀疏侧（<=3 点）：其自身的 median pitch 完全不可信（V5.(23) 里只剩 3 点的
    一侧量出 pitch=44/56，而真实是 5）。此时改为「用密侧的 pitch 去核对稀疏侧
    的点是否都落在该网格上」——落在网格上就认定对称。"""
    a, b = rows.get(s1, []), rows.get(s2, [])
    if len(a) < 3 or len(b) < 3:
        return False, None
    pa = sorted(t["along"] for t in a)
    pb = sorted(t["along"] for t in b)
    ga = [v for v in np.diff(np.array(pa, float)) if v > 2]
    gb = [v for v in np.diff(np.array(pb, float)) if v > 2]
    if not ga or not gb:
        return False, None
    ma, mb = float(np.median(ga)), float(np.median(gb))
    if ma <= 0 or mb <= 0:
        return False, None
    n1, n2 = len(a), len(b)
    big, small = max(n1, n2), min(n1, n2)
    k = int(round(big / float(small)))
    if k < 2 or k > 6:
        return False, None
    # 多的那侧应接近少的整数倍
    if abs(big - k * small) > 0.35 * small:
        return False, None
    # 覆盖范围必须重合（同一段轴向区间）
    lo = max(min(pa), min(pb)); hi = min(max(pa), max(pb))
    if hi - lo < 0.5 * min(max(pa) - min(pa), max(pb) - min(pb)):
        return False, None
    # pitch 一致性：容差 20%。若某一侧点数很少（<=3），其 pitch 不可信，
    # 改用「密侧 pitch 网格」核对稀疏侧点位是否对齐（角点不参与核对）。
    dense_p, sparse_pts = (ma, pb) if n1 >= n2 else (mb, pa)
    if small <= 3:
        p = dense_p
        # 去掉落在轴向两端的「角点」（距任一端 < 1.5 pitch），它们可能同时归属
        # 相邻的另一侧，不该用本侧网格去要求它们对齐。
        span_lo = min(min(pa), min(pb)); span_hi = max(max(pa), max(pb))
        core = [v for v in sparse_pts
                if v > span_lo + 1.5 * p and v < span_hi - 1.5 * p]
        if len(core) >= 1:
            base = core[0]
            res = [abs((v - base) / p - round((v - base) / p)) for v in core]
            if max(res) > 0.42:
                return False, None
        return True, k
    if abs(ma - mb) > 0.20 * max(ma, mb):
        return False, None
    return True, k


def _row_depth_from_profile(ctx, side, prof, pitch):
    """该侧「引脚行的深度」：在 profile 里，与引脚位置同相位的那个亮带中心。

    对称补点时目标侧常常只剩两三个点（深度估计不可靠），这里改从
    profile 直接找：用已知 pitch 在 [0, Wx/Hy) 上给每个槽位打分，
    取「槽位亮、槽间暗」最明显的深度。"""
    band_lim = int(ctx.Wx if side in ("left", "right") else ctx.Hy)
    n = band_lim
    if n < 4 or not pitch or pitch < 3:
        return None
    # 用轮廓的掩膜（V_LO）在 depth 方向做投影
    m = ctx.mask(V_LO, C_LO).astype(np.float32)
    M = ctx.view(side, m, n)          # (along, depth)
    L, N = M.shape
    if L < 4 or N < 4:
        return None
    M = np.apply_along_axis(lambda v: np.convolve(v, np.ones(3) / 3.0, mode="same"), 1, M)
    # 每个深度处「相邻槽位」的对比度：取每个深度上 profile 的方差（亮暗交替最强）
    col = M.mean(axis=0)              # 沿 along 平均
    if col.max() <= 1e-6:
        return None
    # 找 col 的最大值深度（引脚行所在），再取该峰中心
    k = int(np.argmax(col))
    a = b = k
    while a - 1 >= 0 and col[a - 1] >= 0.6 * col[k]:
        a -= 1
    while b + 1 < N and col[b + 1] >= 0.6 * col[k]:
        b += 1
    return 0.5 * float(a + b)


def _target_depth(ctx, side, pins):
    """目标侧「引脚行的深度」= 该侧已有引脚的中位深度（垂直于边的方向）。

    实测很可靠：同一侧的点在垂直方向上高度一致（V5 里 3 个点 x 都是 1486）。"""
    if not pins:
        return None
    if side == "left":
        return float(np.median([t["x"] - ctx.X0 for t in pins]))
    if side == "right":
        return float(np.median([ctx.X0 + ctx.Wx - 1 - t["x"] for t in pins]))
    if side == "top":
        return float(np.median([t["y"] - ctx.Y0 for t in pins]))
    return float(np.median([ctx.Y0 + ctx.Hy - 1 - t["y"] for t in pins]))


def _edge_light(ctx, side, dep, win=1):
    """沿边方向的亮度剖面：depth 落在 [dep-win, dep+win] 内时取最大值。

    焊点在图上很亮（solder 反光），所以「某位置上有没有引脚」可以直接量。
    win 不要放大：±2px 的窗口会把亮斑抹平，整行饱和成 255、对比度归零。"""
    lim = int(ctx.Wx if side in ("left", "right") else ctx.Hy)
    N = int(min(lim, np.ceil(float(dep)) + win + 2))
    if N < 4:
        return None
    M = ctx.view(side, ctx.crop(ctx.V).astype(np.float32), N)
    if M.ndim != 2 or M.shape[0] < 4 or M.shape[1] < 2:
        return None
    d0 = int(max(0, round(float(dep)) - win))
    d1 = int(min(M.shape[1], round(float(dep)) + win + 1))
    if d1 <= d0:
        return None
    return M[:, d0:d1].max(axis=1)


def _peek(row, v, tol=1):
    """row 上位置 v 附近（±tol px）的最大亮度。

    tol 不能取 0/1：对边引脚的 along 常有几个像素的系统偏移（透视、板子不
    完全正对），实测 V5.(5)#1 下排比上排整体偏 ~3.5px，硬点采样会正好落在
    暗缝里，把真引脚判成「没有」。"""
    i = int(round(float(v)))
    if i < 0 or i >= row.size:
        return None
    lo = max(0, i - int(tol))
    hi = min(row.size, i + int(tol) + 1)
    return float(row[lo:hi].max())


def _scan_row_depth(ctx, side, slots, pitch):
    """目标侧一个真点都没有时，扫 depth 找「槽位亮 / 槽间暗」对比最强的那个行。

    返回 (dep, row)；row 是该深度的整条亮度剖面（供后续逐个槽位取证）。
    只在靠近该边的前 60% 深度里找——否则会选到「对边那一排引脚」（那里的
    槽位/槽间对比往往更强，实测把下排深度错定到 108px，点直接画到了上排）。"""
    lim = int(ctx.Wx if side in ("left", "right") else ctx.Hy)
    if lim < 4:
        return None, None
    M = ctx.view(side, ctx.crop(ctx.V).astype(np.float32), lim)
    if M.ndim != 2 or M.shape[0] < 4 or M.shape[1] < 4:
        return None, None
    tol = int(max(1, round(0.28 * float(pitch))))
    si = sorted(set(int(round(float(v))) for v in slots
                    if 0 <= int(round(float(v))) < M.shape[0]))
    if len(si) < 2:
        return None, None
    mids = [(si[i] + si[i + 1]) // 2 for i in range(len(si) - 1)]

    def win(idxs):
        cols = []
        for i in idxs:
            a, b = max(0, i - tol), min(M.shape[0], i + tol + 1)
            cols.append(M[a:b, :].max(axis=0))
        return np.array(cols).mean(axis=0)

    contrast = win(si) - win(mids)
    if contrast.size == 0:
        return None, None
    cap = int(min(0.60 * min(ctx.bw, ctx.bh), M.shape[1] - 1))
    if cap < 2:
        cap = M.shape[1] - 1
    k = int(np.argmax(contrast[:cap + 1]))
    if float(contrast[k]) <= 0.0:
        return None, None
    # 峰值中心细化：argmax 只给一个整数深度，差 1~2px 就会让后面的逐槽取证切在
    # 引脚暗边上（实测 V5.(5)#1 下排 argmax 给 5，真峰中心在 3~4）。取 ±2 邻域的
    # 加权质心，返 float。（返回的 row 已不再被调用方使用，保留兼容。）
    lo, hi = max(0, k - 2), min(contrast.size, k + 3)
    seg = np.clip(contrast[lo:hi], 0.0, None)
    dep = float(np.sum(seg * np.arange(lo, hi)) / max(1e-9, float(seg.sum())))
    return dep, M[:, k]


def _snap(row, v, tol):
    """把 v 微调到 row 上最近的亮点中心。

    对边引脚的 along 常有几 px 的系统偏移（透视/板子不正对），补出来的点若
    直接用模板位置会整体偏半个点宽；吸到局部亮度峰上就落回焊盘中心了。"""
    if row is None:
        return float(v)
    i = int(round(float(v)))
    a = max(0, i - int(tol))
    b = min(row.size, i + int(tol) + 1)
    if b - a < 2:
        return float(v)
    return float(a + int(np.argmax(row[a:b])))


def _window(row, v, win):
    """row 上以 v 为中心、半径 win 的局部片段。"""
    i = int(round(float(v)))
    a = max(0, i - int(win))
    b = min(row.size, i + int(win) + 1)
    if b - a < 3:
        return None
    return row[a:b]


def _pin_evidence(ctx, side, row, cand, pitch, real_alongs=None):
    """逐个候选位置「取证」：在该处的**局部**动态范围内要足够亮。

    用局部而不是全行阈值是必须的——板子打光不均匀，实测 V5.(5)#1 同一排引脚
    左半 sv≈130、右半 sv≈62（阴影），而两侧的暗缝都在 ~45。全行阈值会把
    整条暗半侧判成「没有引脚」。改成「候选处相对它周围 ±1.2 齿距的动态范围
    至少爬到 45%」之后，明暗两半都能过。

    这个判据也是能放开数量闸门还不虚增的关键：
    真漏检的点在图上就是亮的焊盘；不是漏检的位置（比如 SOP 引线框本身的分组
    间隙、被阴影盖住的板面）在局部范围内爬不上去，一量便知。"""
    if row is None or row.size < 4 or not cand:
        return []
    tol = int(max(1, round(0.28 * float(pitch))))
    lo, hi = np.percentile(row, (10, 90))
    if float(hi - lo) < 8.0:              # 整行几乎没有起伏，量不准
        return []
    win = int(max(3, round(1.2 * float(pitch))))
    floor = None
    if real_alongs:
        rv = [_peek(row, v, tol) for v in real_alongs]
        rv = [v for v in rv if v is not None]
        if len(rv) >= 2:
            floor = 0.35 * float(np.median(rv))   # 极宽松的下限，只挡离谱的暗点
    out = []
    for v in cand:
        sv = _peek(row, v, tol)
        if sv is None:
            continue
        if floor is not None and sv < floor:
            continue
        # 峰值归属：候选处必须有自己的焊盘。
        # 若 v 附近的最亮点恰好就是已认出的某个真点，说明 v 只是它的偏移影子
        # （两侧 pitch 略有差别时整排都会漂出这种假缺口），不能重复加一个。
        if real_alongs:
            pstar = _snap(row, v, tol)
            if min(abs(float(t) - pstar) for t in real_alongs) <= 0.55 * pitch:
                continue
        w = _window(row, v, win)
        if w is None:
            continue
        wlo, whi = np.percentile(w, (10, 90))
        wsc = float(whi - wlo)
        if wsc < 3.0:                     # 局部太平，没有可辨的亮暗结构
            continue
        if sv - float(np.percentile(w, 20)) < 0.45 * wsc:
            continue
        # 「坐在暗缝上」判据：候选点两侧的**缝**必须不比它更亮。
        # 采样窗口一定要窄（±0.12*pitch）：用 ±0.28*pitch 的宽窗口去量「缝」，窗口
        # 会盖到相邻引脚上，把缝量成引脚亮度（实测 V5.(5)#1 下排 pitch=12、缝在
        # v±6 处，宽窗左右各量到 49/51，于是整排中等亮度的真引脚全被误判成暗缝，
        # 8 个候选丢掉 4 个）。窄窗 + 放宽到 0.45 后，真引脚（sv≈49，缝≈20）稳过。
        tg = int(max(1, round(0.12 * float(pitch))))
        lm = _peek(row, v - 0.5 * pitch, tg)
        rm = _peek(row, v + 0.5 * pitch, tg)
        if lm is not None and rm is not None and sv < 0.45 * (lm + rm):
            continue                      # 左右都比它亮 -> 它坐落在暗缝上
        out.append(float(v))
    return out


def _symmetrize(rows, sides, ctx, pitch_of, best=None):
    """Symmetry-based gap completion for two-sided / four-sided parts.

    Problem 3: one edge of a symmetric part is missed entirely (or half of it),
    because the box clipped it or the pads fell into shadow.  Since the two
    opposite rows are mirror images, the surviving row predicts the missing
    one.

    沿轴位置取自「完整侧」；深度（垂直于边的方向）由目标侧已有真点的中位深度
    决定，整侧为 0 时改用「模板网格扫 depth」找行。最后每个候选位置都要过
    ``_pin_evidence`` 的像素取证——这是能放开数量闸门还不虚增的关键。"""
    for s1 in sides:
        s2 = OPPOSITE[s1]
        if s2 not in sides:
            continue
        a, b = rows.get(s1, []), rows.get(s2, [])
        if len(a) < 4 and len(b) < 4:
            continue
        if len(a) >= len(b):
            tmpl_s, tgt_s, tmpl, tgt = s1, s2, a, b
        else:
            tmpl_s, tgt_s, tmpl, tgt = s2, s1, b, a
        if len(tmpl) < 4:
            continue
        # 目标侧已经和模板侧差不多齐 -> 没有值得补的，直接跳过。
        # 这一条是 OTF 回归逼出来的：两侧各 36 个点时，只要两侧 pitch 差 2%，
        # 沿边累积就漂 0.7 个齿距，会在整排上造出一堆「假缺口」候选；
        # 实测 0006 F 因此从 144 涨到 251。数量闸门 + 下面的峰值归属检查
        # （候选处必须有自己的焊盘、不能压在已有真点上）两道一起才压得住。
        if len(tgt) > 0.95 * len(tmpl):
            continue
        # 旧版这里有两道「数量/对称性」闸门（ratio>0.60 跳过、_pair_symmetry
        # 要求两侧都 >=3 点），结果是用户新数据里最常见的两种漏检全都放行不了：
        #   * 整侧漏检（目标侧 0 点）  -> _pair_symmetry 直接返回 False
        #   * 只漏 1~2 个（ratio 0.75~0.9）-> 被 ratio 闸门挡掉
        # 现在改为「候选位置全部由像素取证」(_pin_evidence)，闸门放开到 0.95；
        # 取证不过的点不会加，所以不会虚增。
        pitch = pitch_of.get(tmpl_s) or pitch_of.get(tgt_s)
        if not pitch or pitch <= 2:
            g = [v for v in np.diff(np.array(sorted(t["along"] for t in tmpl), float)) if v > 2]
            pitch = float(np.median(g)) if g else None
        if not pitch or pitch <= 2:
            continue
        axis = _side_axis(tmpl_s)
        # 用「局部沿边坐标」along 做比较/新增（与 _make_pins 的 along 一致），
        # 不要用绝对的 x/y，否则补点时会当作局部坐标换算成越界的绝对坐标。
        ta = np.array(sorted(t["along"] for t in tmpl), float)
        if len(tgt):
            tg = np.array(sorted(t["along"] for t in tgt), float)
        else:
            tg = np.array([])
        thr = 0.45 * pitch
        added = [float(v) for v in ta
                 if (len(tg) == 0 or np.min(np.abs(tg - v)) > thr)]
        if not added:
            continue
        # 目标侧深度：
        #   * 目标侧还有真点 -> 用它们的中位深度（最可靠）；
        #   * 目标侧整侧为 0 -> 用模板网格去扫 depth，取「槽位亮/槽间暗」最强的那行。
        # 绝不退回「profile 全局最大投影」——四边芯片上会被邻边引脚抢走
        # （实测把右侧深度错定成 124.5px，补出的点落到了框的另一侧）。
        dep = None
        row = None
        if tgt:
            dep = _target_depth(ctx, tgt_s, tgt)
        if dep is None:
            dep, _ = _scan_row_depth(ctx, tgt_s, ta, pitch)
        if dep is None:
            continue
        # 取证用的行要取「depth ±1 的最大亮度」而不是单层：深度估计有 ±1~2px 误差，
        # 单层切片正好切在引脚暗边时会把真点判成没有（V5.(5)#1 下排就是差这 2px）。
        row = _edge_light(ctx, tgt_s, dep, win=1)
        if row is None:
            continue
        added = _pin_evidence(ctx, tgt_s, row, added, pitch,
                              real_alongs=([t["along"] for t in tgt] or None))
        if not added:
            continue
        lim = ctx.Wx if tgt_s in ("left", "right") else ctx.Hy
        tol = int(max(1, round(0.28 * pitch)))
        for v in added:
            # 吸附到局部亮点中心，但不要贴到已有的真实点上
            vs = _snap(row, v, tol)
            if len(tg) and float(np.min(np.abs(tg - vs))) <= 0.35 * pitch:
                vs = float(v)
            d = float(np.clip(dep, 0.0, lim - 1))
            # along 是「局部沿边坐标」，要按边换算成绝对 x/y（与 _make_pins 一致）
            if tgt_s == "left":
                x, y = ctx.X0 + d, ctx.Y0 + vs
            elif tgt_s == "right":
                x, y = ctx.X0 + ctx.Wx - 1 - d, ctx.Y0 + vs
            elif tgt_s == "top":
                x, y = ctx.X0 + vs, ctx.Y0 + d
            else:
                x, y = ctx.X0 + vs, ctx.Y0 + ctx.Hy - 1 - d
            np_ = dict(x=float(x), y=float(y), along=float(vs), side=tgt_s,
                       predicted=True)
            if (ctx.x0 - 2 <= np_["x"] <= ctx.x1 + 2 and
                    ctx.y0 - 2 <= np_["y"] <= ctx.y1 + 2):
                rows.setdefault(tgt_s, []).append(np_)
    return rows


def _interp_gaps(rows, sides, pitch_of, fill_ratio=1.55, opp_pitch=None, ctx=None):
    """Pitch-outlier interpolation for a single edge.

    Problem 4: one pin inside an otherwise regular row is missed, so a gap of
    ~2x the pitch appears.  Where the gap is (close to) an integer multiple of
    the median pitch and the slot is inside the row's span, insert an
    interpolated pin.  Predicted pins carry ``predicted=True``.

    ``opp_pitch`` —— 对边实测 pitch（同款芯片对边引脚数通常相同）。给了它就以
    两边中较小者为基准 pitch：自己是「密排但漏点多」的那一侧时，对边的 5px 才是
    真实 pitch，本侧统计出的 12px 只是「相邻两个真点之间平均跨了 ~2 个位置」。

    保守性（OTF/V5 实测修正）：
      - 真实点 >= 5 个；
      - 若本行间距本就是大小交替（近中位比例 < 0.60），只有在「对边 pitch 明显
        更小、且能整除本行间距」时才继续，否则跳过；
      - k > 3 的大空段视为真空区，不插。
    """
    for s in sides:
        pins = rows.get(s, [])
        if len(pins) < 4:
            continue
        axis = _side_axis(s)
        key = "x" if axis == "x" else "y"
        order = sorted(range(len(pins)), key=lambda i: pins[i][key])
        pins = [pins[i] for i in order]
        vals = np.array([p[key] for p in pins], float)
        gaps = np.diff(vals)
        gaps = gaps[gaps > 1.5]
        if len(gaps) < 3:
            continue
        pitch = float(np.median(gaps))
        if pitch <= 2:
            continue
        # 对边 pitch（本侧）
        op = None
        n_opp = 0
        s_opp = OPPOSITE.get(s)
        if s_opp:
            n_opp = len(rows.get(s_opp, []))
        if opp_pitch:
            op = opp_pitch.get(s) if isinstance(opp_pitch, dict) else opp_pitch
        # 对边 pitch 提示：只有「对边本身点数不少于本侧」时，它的 pitch 才可信
        # （否则是对边漏检得厉害，其 pitch 反而偏大/不可靠，不能用来加密本侧）。
        reliable = n_opp >= len(pins)
        use_opp = False
        if reliable and op and 2 <= op < 0.75 * pitch:
            kk0 = pitch / float(op)
            if abs(kk0 - round(kk0)) <= 0.25 * max(1, round(kk0)):
                pitch = float(op)
                use_opp = True
        near = float(np.mean(np.abs(gaps - pitch) < 0.30 * pitch))
        if (not use_opp and reliable and op
                and abs(float(op) - pitch) <= 0.30 * pitch):
            use_opp = True
        # 次大间距：候选 gap 必须明显大于它，才算「孤立异常」
        srt = np.sort(gaps)
        second = float(srt[-2]) if len(srt) >= 2 else float(srt[-1])
        # ---- 像素取证用的亮度剖面 -------------------------------------------
        # 本侧真实点的中位深度 -> 沿边方向取一条亮度剖面，用来逐个验证候选位置。
        # 有了它就敢放开下面几道「数量闸门」：候选必须真的落在亮点上，否则不插。
        ev_row = None
        if ctx is not None:
            dep = _target_depth(ctx, s, pins)
            if dep is not None:
                ev_row = _edge_light(ctx, s, dep)
        alongs = [float(t.get("along", t[key])) for t in pins]
        add = []
        for i in range(len(pins) - 1):
            d = float(vals[i + 1] - vals[i])
            if d < fill_ratio * pitch:
                continue
            kf = d / pitch
            k = int(round(kf))
            if k < 2:
                continue
            if abs(kf - k) / float(k) > 0.22:
                continue                    # 与整数倍差太多
            # 只在此段的两个端点「都」是真实点时插（避免在行尾外插）
            if pins[i].get("predicted") or pins[i + 1].get("predicted"):
                continue
            cand = [float(vals[i] + j * d / k) for j in range(1, k)]
            # 取证：候选位置在本侧引脚行深度上有没有足够的局部亮度
            good = []
            if ev_row is not None and len(ev_row) > 4:
                good = _pin_evidence(ctx, s, ev_row, cand, pitch, real_alongs=alongs)
            allok = bool(cand) and len(good) >= len(cand)
            # 「本来就规整」的严格路径（老逻辑）：行内间距齐整 + 孤立异常
            strict_ok = bool(use_opp or (near >= 0.60 and d >= 1.25 * second))
            ok = strict_ok or allok
            # 本侧真实点 < 5 时，中位间距只是两三个数的统计量，`near/second`
            # 那套「规整性」判据完全不可信 —— 此时只认真刀真枪的逐点像素取证。
            # 实测 OTF 0319#1（SOT-223 类，顶排 4 个点、中间空 1 个多齿距）：
            # 放宽到 4 个点后，宽松路径在**两个大焊盘之间的绿板**上插了一个假点。
            if len(pins) < 5 and not allok:
                ok = False
            if not ok:
                continue                    # 既不规整、又拿不到像素证据 -> 不插
            if k > 3 and not allok:
                continue                    # 大空段视为真空区，除非逐点取证通过
            for v in cand:
                dep2 = 0.5 * (pins[i][('y' if key == 'x' else 'x')] +
                              pins[i + 1][('y' if key == 'x' else 'x')])
                if key == "x":
                    add.append(dict(x=float(v), y=float(dep2), along=float(v),
                                    side=s, predicted=True))
                else:
                    add.append(dict(x=float(dep2), y=float(v), along=float(v),
                                    side=s, predicted=True))
        if add:
            rows.setdefault(s, []).extend(add)
    return rows


LABEL_ALIAS = {"O": "1", "T": "2", "F": "4",   # current yolo labels (O=one/T=two/F=four)
               "o": "1", "t": "2", "f": "4",
               "1": "1", "2": "2", "4": "4"}   # legacy numeric labels


# --------------------------------------------------------------------------- #
# 问题3：同款引脚芯片跨片对比补点
# --------------------------------------------------------------------------- #
# 思路：同一块 PCB 上（用户明确要求「仅限本块」）常有多片同款芯片，它们的封装
# 尺寸、引脚数、引脚间距完全一致，只是摆放位置/朝向不同。若其中一片检得完整、
# 另一片漏掉一整侧或若干点，就可以拿「完整那片」的引脚栅格去补「残缺那片」。
#
# 关键：同款判定不能只看大小。同款芯片的 (w,h) 必然几乎相同（同一张图、同一
# 比例尺），所以按「尺寸相对差 <= CROSS_TOL」聚类，再要求标注 label 一致、且
# 长边朝向一致（横放/竖放分开处理，避免把 90° 旋转的对应关系搞反）。
# 但「外框近等」并不等于同款——实测 V5.(14) 里 ULN2803A（SOIC-18，9 脚/侧，
# 间距 12px）和 LVC16T245（TSSOP-48，24 脚/侧，间距 ~4px）的 YOLO 框几乎一样大
# （117x112 vs 128x97），只按尺寸聚类会把它们并成一组，于是拿 4px 的模板栅格去
# 给 12px 的片补点，20 秒内就能把 18 个点虚增到 31。所以再加一道**间距一致性**
# 闸门（CROSS_PITCH_TOL）——同封装必然同间距，不同封装即使外框巧合也拦得住。
# 每个同款组选「真实点数最多」的那片当模板（用户描述里正是完整的那片），
# 逐侧把模板的引脚位置「整体平移」到目标片上（平移量由两片各自已有引脚的中心
# 差决定，抵消 YOLO 框松紧差异），最后每个候选点还要过像素取证。

CROSS_TOL = 0.14      # 尺寸相对差上限（同款判定）
CROSS_NEAR = 0.45     # 目标侧已有引脚 0.45*pitch 内视为同一槽位，不重复补
CROSS_EVID = 0.35     # 候选点亮度 >= 该侧真实点中位亮度的 0.35 才认
CROSS_ABS = 45.0      # 且不低于这个绝对亮度下限
PRUNE_REL = 0.55      # 剪枝：本行内亮度 < PRUNE_REL*邻居中位 -> 疑似增强假点
PRUNE_ABS = 110.0     # 且绝对亮度也要够暗。这一条不可省：打光不均时整半排
                      # 都可能只有 60~90（暗），此时「比邻居暗」毫无意义
                      # （整排都暗，彼此的比值接近 1）。只有「在一排 200+ 的亮点
                      # 中间冒出一个 <110 的」才真值得怀疑。
CROSS_MIN = 0.5      # 目标侧点数 <= 模板侧的 0.5 倍才补（只救「整侧塌了」）


def _to_ad(side, x, y, X0, Y0, Wx, Hy):
    """(x,y) -> (along, depth)；depth 0 = 最靠近该侧框边。"""
    if side == "top":    return float(x - X0), float(y - Y0)
    if side == "bottom": return float(x - X0), float(Y0 + Hy - 1 - y)
    if side == "left":   return float(y - Y0), float(x - X0)
    return float(y - Y0), float(X0 + Wx - 1 - x)


def _from_ad(side, a, d, X0, Y0, Wx, Hy):
    """(along, depth) -> (x, y)，``_to_ad`` 的逆。"""
    if side == "top":    return float(X0 + a), float(Y0 + d)
    if side == "bottom": return float(X0 + a), float(Y0 + Hy - 1 - d)
    if side == "left":   return float(X0 + d), float(Y0 + a)
    return float(X0 + Wx - 1 - d), float(Y0 + a)


def _probe_v(V, side, along, depth, X0, Y0, Wx, Hy, r=1):
    """(along, depth) 处 ±r 窗口内的最大亮度；越界返回 None。"""
    x, y = _from_ad(side, along, depth, X0, Y0, Wx, Hy)
    xi, yi = int(round(x)), int(round(y))
    if yi < 0 or yi >= V.shape[0] or xi < 0 or xi >= V.shape[1]:
        return None
    ya, yb = max(0, yi - r), min(V.shape[0], yi + r + 1)
    xa, xb = max(0, xi - r), min(V.shape[1], xi + r + 1)
    return float(V[ya:yb, xa:xb].max())


def _snap_depth(V, side, along, depth, pitch, X0, Y0, Wx, Hy):
    """在 depth 附近 ±0.5*pitch 里找最亮的深度（把点吸附到焊盘上）。"""
    dep = float(depth)
    best = None
    span = max(1, int(round(0.5 * float(pitch))))
    for k in range(-span, span + 1):
        d = dep + k
        if d < 0:
            continue
        val = _probe_v(V, side, along, d, X0, Y0, Wx, Hy)
        if val is None:
            continue
        if best is None or val > best[0]:
            best = (val, d)
    return best[1] if best else dep


def _geom(img, box):
    """box -> (X0, Y0, Wx, Hy)，与 ``_Ctx`` 的口径一致（并夹到图像范围内）。"""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = [float(v) for v in box]
    X0 = int(np.floor(max(0.0, x0))); Y0 = int(np.floor(max(0.0, y0)))
    Wx = max(4, min(W - X0, int(round(x1 - x0)) + 1))
    Hy = max(4, min(H - Y0, int(round(y1 - y0)) + 1))
    return X0, Y0, Wx, Hy


def _same_foil(a, b):
    """两片是否「同款」：标注一致 + 长短边尺寸都接近（同一张图同一比例尺）。"""
    if a["label"] != b["label"]:
        return False
    for (w1, h1), (w2, h2) in ((a["wh"], b["wh"]),):
        m1, n1 = max(w1, h1), min(w1, h1)
        m2, n2 = max(w2, h2), min(w2, h2)
        if abs(m1 - m2) > CROSS_TOL * max(m1, m2):
            return False
        if abs(n1 - n2) > CROSS_TOL * max(n1, n2):
            return False
    # 必须同朝向（横放/竖放分开），否则侧面的对应关系会错
    return (a["wh"][1] >= a["wh"][0]) == (b["wh"][1] >= b["wh"][0])


def _transfer_side(V, s, tp, gp, gt, gg):
    """把模板侧 ``s`` 的引脚栅格平移到目标片，返回新增的 predicted 点。

    ``tp``/``gp`` 为该侧模板/目标的 pin 列表；``gt``/``gg`` 为两片的
    (X0,Y0,Wx,Hy) 几何。
    """
    if len(tp) < 3:
        return []
    tad = [_to_ad(s, p["x"], p["y"], *gt) for p in tp]
    ta = sorted(a for a, _ in tad)
    tdep = [d for _, d in tad]
    dg = np.diff(np.array(ta, float))
    tpit = float(np.median(dg)) if len(dg) else 0.0
    if tpit < 2.0:
        return []
    if len(gp) >= len(tp):
        return []                       # 目标侧已经不比模板少，没什么可补
    # 只救「整侧塌了」的情况：目标侧点数必须少到只剩模板侧的零头。
    # 这条闸门很关键——同一组里各片的计数本来就可能有 ±1~2 的抖动（密排焊盘
    # 边沿被拆成两个峰之类），若允许小幅补齐，就会把模板的过检沿着整组扩散。
    # 实测 V5.(16) 一组：某片 20、某片 49，若只要求「少于模板」就会把 20 硬补
    # 成 49；用户真正要修的「一侧全漏」是 0，0.35 倍这条闸门照样放行。
    if len(gp) > CROSS_MIN * len(tp):
        return []
    # 「本侧本来就稀疏」的判别（见 ``_aliased_side``）：中点也是焊盘才准许补，
    # 否则是本片本来就长这样，套模板细栅格会虚增。目标侧整侧为 0 时无从判别，
    # 走下面的「模板平移」分支（用户主要要修的正是这种整侧塌了的情形）。
    if gp and not _aliased_side(V, s, gp, gg):
        return []
    if gp:
        gad = [_to_ad(s, p["x"], p["y"], *gg) for p in gp]
        ga = sorted(a for a, _ in gad)
        delta = float(np.median(ga)) - float(np.median(ta))
        dep = float(np.median([d for _, d in gad]))
        ref = [_probe_v(V, s, a, d, *gg) for a, d in gad]
        ref = [v for v in ref if v is not None]
        ref = float(np.median(ref)) if ref else None
    else:
        # 目标侧整侧为 0：没有自己的基准，只能信模板（同款同比例尺，深度应接近）
        delta = 0.0
        dep = float(np.median(tdep))
        ref = None
    out = []
    for a in ta:
        v = a + delta
        if gp and min(abs(float(t) - v) for t in ga) <= CROSS_NEAR * tpit:
            continue                    # 该槽位目标片已有（吸附误差以内）
        d = _snap_depth(V, s, v, dep, tpit, *gg)
        val = _probe_v(V, s, v, d, *gg)
        if val is not None:
            bar = CROSS_ABS if ref is None else max(CROSS_ABS, CROSS_EVID * ref)
            if val < bar:
                continue                # 像素取证不过 -> 不硬塞
        x, y = _from_ad(s, v, d, *gg)
        out.append(dict(x=float(x), y=float(y), along=float(v),
                        side=s, predicted=True))
    return out


def _aliased_side(V, s, gp, gt):
    """本侧是否「只检到一半」——判据是相邻两真实点的**中点**也够亮。

    密排芯片（同封装、真间距 5px）在低分辨率下常常只检出 1/3 的点（pitch 被
    高估 3 倍）。那种情况下，两个已检点之间的中点其实也是真焊盘 -> 亮；用模板
    的细栅格补上去是**对的**（V5.(16)#4：10 点/侧补成 24 点/侧）。
    反过来，如果两检点之间的中点是暗板，说明本片本来就是稀疏封装（V5.(14)：
    ULN2803A 只有 9 脚/侧，间距 12px），硬套 TSSOP-48 的 4px 栅格就是虚增。
    这一条是「同封装」判定真正可靠的依据——只看外框尺寸或间距比都做不到。
    """
    if len(gp) < 3:
        return False
    ad = [_to_ad(s, p["x"], p["y"], *gt) for p in gp]
    ad.sort()
    vp = [_probe_v(V, s, a, d, *gt) for a, d in ad]
    vp = [v for v in vp if v is not None]
    if len(vp) < 3:
        return False
    vm = []
    for i in range(len(ad) - 1):
        a = 0.5 * (ad[i][0] + ad[i + 1][0])
        d = 0.5 * (ad[i][1] + ad[i + 1][1])
        v = _probe_v(V, s, a, d, *gt)
        if v is not None:
            vm.append(v)
    if len(vm) < 2:
        return False
    return float(np.median(vm)) >= 0.70 * float(np.median(vp))


def _cross_chip_fill(img, chips, res, o):
    """按「同款芯片」分组，用完整的那片去补其余片；就地写回 ``res``。"""
    V, _C = _vc_cached(img)
    meta = []
    for i, c in enumerate(chips):
        # 注意：必须先看原始 label 再套 ALIAS —— ALIAS 会把 "F" 变成 "4"，
        # 之后就认不出这是 F 了（C 因为不在 ALIAS 里才没踩到这个坑）。
        raw = str(c["label"])
        if (_is_c_label(raw) or _is_v_label(raw) or _is_f_label(raw)
                or _is_t_label(raw) or _is_l_label(raw) or _is_o_label(raw)
                or _custom_spec(raw) is not None):
            continue        # C/V 恒 2 点、F 恒 32×4、T 恒 10×2、L 恒 3×2、O 恒 7，
                            # 自定义类别同理（点数由「基础类 × n」先验决定）：
                            # 都是精确解；跨片栅格补点只会虚增
        lab = LABEL_ALIAS.get(raw, raw)
        X0, Y0, Wx, Hy = _geom(img, c["box"])
        x0, y0, x1, y1 = [float(v) for v in c["box"]]
        meta.append(dict(idx=i, label=lab,
                         wh=(max(1.0, x1 - x0), max(1.0, y1 - y0)),
                         geo=(X0, Y0, Wx, Hy)))
    # ---- 聚类：贪心，跟组内第一个成员比 ----
    groups = []
    for m in meta:
        for g in groups:
            if _same_foil(m, g[0]):
                g.append(m)
                break
        else:
            groups.append([m])
    for g in groups:
        if len(g) < 2:
            continue
        # 模板 = 真实点数最多的那片（并列时取梳齿质量高的）
        def _key(m):
            pins = res[m["idx"]][0]
            nr = sum(1 for p in pins if not p.get("predicted"))
            return (nr, float(res[m["idx"]][2].get("qsum", 0.0)))
        tmpl = max(g, key=_key)
        it = tmpl["idx"]
        for m in g:
            ig = m["idx"]
            if ig == it:
                continue
            pins_t, _ct, info_t = res[it]
            pins_g, _cg, info_g = res[ig]
            sides_g = set(info_g.get("sides", ()))
            add = []
            for s in info_t.get("sides", ()):
                if s not in sides_g:
                    continue            # 目标片这一侧没参与识别，不越权补
                tp = [p for p in pins_t if p["side"] == s]
                gp = [p for p in pins_g if p["side"] == s]
                add += _transfer_side(V, s, tp, gp, tmpl["geo"], m["geo"])
            if not add:
                continue
            # 去重 + 写回
            seen = set()
            for p in add:
                k = (p["side"], int(round(p["x"] / 3.0)), int(round(p["y"] / 3.0)))
                if k in seen:
                    continue
                seen.add(k)
                pins_g.append(p)
            info_g["cross"] = int(info_g.get("cross", 0)) + len(add)
            info_g["predicted"] = int(info_g.get("predicted", 0)) + len(add)
            info_g["group"] = len(g)
            res[ig] = (pins_g, _cg, info_g)


def detect_board(img, chips, min_area=6, opt=None):
    """整板识别：逐芯片识别后，再做「同款芯片跨片对比补点」（问题3）。

    ``chips``: ``[{"label": str, "box": [x0,y0,x1,y1]}, ...]``
    返回 ``[(pins, counts, info), ...]``，与 ``chips`` 一一对应。
    info 追加 ``"group"``（同款组大小）与 ``"cross"``（跨片补的点数）。
    """
    o = dict(DEFAULT_OPT)
    if opt:
        o.update(opt)
    res = [detect(img, c["box"], c["label"], min_area, opt) for c in chips]
    if not o.get("cross_chip", True) or len(chips) < 2:
        return res
    try:
        _cross_chip_fill(img, chips, res, o)
    except Exception:
        pass                            # 跨片补点只是加分项，出错不影响主流程
    return res


DEFAULT_OPT = {
    "geo_classify": True,   # 问题1：按 label + 几何自动判正矩形/两侧
    "symmetrize": True,     # 问题3：对边对称时，用另一侧镜像补齐漏检侧
    "interp_gaps": True,    # 问题4：按间距中位数发现缺位并插值补点
    "dense_pitch": True,    # 问题2：密排引脚自适应峰值合并/声势门槛
    "contrast_boost": True, # 问题4：低分辨率/低对比芯片的对比度增强重试
    "cross_chip": True,     # 问题3：同款芯片跨片对比补点（仅限本块板）
}


def _enhance_patch(img, box, pad=16, clip=2.5, tile=8, sharp=1.6):
    """对 box 附近区域做局部对比度增强（CLAHE + 反锐化），返回同尺寸新图。

    为什么是「局部」：低分辨率芯片的引脚常常又小又暗（实测 V5.(9)#14 是黑色
    SOIC-8，全框 V>95 的像素只占 5%），全局阈值 95 根本够不着它；而 CLAHE 在
    小窗口内拉伸局部对比度，能把「暗引脚 / 暗缝」拉开到阈值之上。只增强 box
    周围一小块而不是整图，既省时间又避免把无关区域一起抬亮改变打分。

    增强后原图像素被替换，因此 ``_Ctx`` 的绝对坐标索引依然成立。
    """
    try:
        key = (img.__array_interface__["data"][0], img.shape, img.strides,
               tuple(int(round(float(v))) for v in box), pad, clip, tile, sharp)
    except Exception:
        key = None
    if key is not None:
        hit = _ENH_CACHE.get(key)
        if hit is not None:
            return hit
    H, W = img.shape[:2]
    x0, y0, x1, y1 = [int(round(float(v))) for v in box]
    X0 = max(0, x0 - pad); Y0 = max(0, y0 - pad)
    X1 = min(W, x1 + pad + 1); Y1 = min(H, y1 + pad + 1)
    if X1 - X0 < 8 or Y1 - Y0 < 8:
        return img
    sub = img[Y0:Y1, X0:X1]
    lab = cv2.cvtColor(sub, cv2.COLOR_BGR2LAB)
    L, A, B = cv2.split(lab)
    cl = cv2.createCLAHE(clipLimit=float(clip), tileGridSize=(int(tile), int(tile)))
    L2 = cl.apply(L)
    out = cv2.cvtColor(cv2.merge([L2, A, B]), cv2.COLOR_LAB2BGR)
    blur = cv2.GaussianBlur(out, (0, 0), 1.2)
    out = cv2.addWeighted(out, float(sharp), blur, 1.0 - float(sharp), 0)
    res = img.copy()
    res[Y0:Y1, X0:X1] = out
    if key is not None:
        _ENH_CACHE[key] = res
        _ENH_ORDER.append(key)
        while len(_ENH_ORDER) > _ENH_MAX:
            _ENH_CACHE.pop(_ENH_ORDER.pop(0), None)
    return res


_ENH_CACHE = {}
_ENH_ORDER = []
_ENH_MAX = 10


def _boost_keeps_sides(a, b):
    """增强解 ``b`` 不得丢掉原解 ``a`` 里「已经有真实引脚的那一侧」。

    这是让「增强重试」安全的关键一条。CLAHE 会把芯片本体的轮廓也抬成周期性亮
    带，于是增强图有时会给出一个**完全不同朝向**的解。实测 V5.(16) 一枚 30x84
    的白色小片：原图在 bottom 找到 3 个真引脚，增强图却改口说引脚在 left/right，
    并给出 5+5=10 个点，全都落在本体两条侧边上。
    反过来，用户真正要修的「一侧整排漏检」是原解只有一侧、增强补出另一侧
    （bottom -> top+bottom），那种解**包含**原有的 side，照样放行。
    """
    pr = {p["side"] for p in a[0] if not p.get("predicted")}
    pe = {p["side"] for p in b[0]}
    return pr.issubset(pe)


def _needs_boost(pins, counts, info):
    """判断本次检出是否「差到值得用增强图重试」。

    只在两种情况下重试，避免在正常图上白白引入增强噪声：
      * 真实（非预测）引脚太少（< 8）——低分辨率小芯片的典型表现；
      * 已选中的某一侧整侧为 0（对侧却有）——漏掉一整排。
    """
    nreal = sum(1 for p in pins if not p.get("predicted"))
    if nreal < 8:
        return True
    sides = info.get("sides", ()) or ()
    for s in sides:
        if counts.get(s, 0) == 0:
            return True
    return False


def _boost_better(a, b):
    """增强解 b 是否明显优于原始解 a。

    判据：b 的真实点数严格更多，且其梳齿总质量不低于 a 的 0.55 倍（增强图整体
    对比度更高、q 天然偏大，所以这条闸门取得很松；它的作用只是挡住「增强把噪声
    抬成假峰、点数虚增但毫无结构」的情况）。"""
    pa, ca, ia = a
    pb, cb, ib = b
    na = sum(1 for p in pa if not p.get("predicted"))
    nb = sum(1 for p in pb if not p.get("predicted"))
    if nb <= na:
        return False
    qa = float(ia.get("qsum", 0.0))
    qb = float(ib.get("qsum", 0.0))
    if qa > 0 and qb < 0.55 * qa:
        return False
    return True


def _prune_weak(img, box, res):
    """用**原图**复核增强重试的结果，剔掉「本行内明显比邻居暗」的假点。

    增强图（CLAHE）会把暗板、暗缝一起抬起来，偶尔在没有焊盘的地方造出假峰
    （实测 V5.(9)#14 那枚 60x84 的六脚小片，顶部第 5 个点落在纯绿板面上）。
    假峰在原图上是暗的，所以拿原图复核最直接。

    为什么用「邻居」而不是「本行中位数」：板面打光常常一半亮一半暗（实测
    V5.(5)#1 下排左半 sv≈130、右半 sv≈62），按中位数会整半排误杀；而按左右
    相邻点比较，暗半排里的点个个都暗、彼此相当，一个都不会掉。**只看真实点**，
    预测点（symmetrize/interp 产出，已各自取证过）原样保留。
    """
    pins, counts, info = res
    if len(pins) < 3:
        return res
    V, _C = _vc_cached(img)
    X0, Y0, Wx, Hy = _geom(img, box)
    by = {}
    for p in pins:
        if p.get("predicted"):
            continue
        by.setdefault(p["side"], []).append(p)
    drop = set()
    for s, ps in by.items():
        if len(ps) < 3:
            continue
        ad = []
        for p in ps:
            a, d = _to_ad(s, p["x"], p["y"], X0, Y0, Wx, Hy)
            ad.append([a, d, _probe_v(V, s, a, d, X0, Y0, Wx, Hy), p])
        ad.sort(key=lambda z: z[0])
        vs = [z[2] for z in ad if z[2] is not None]
        if len(vs) < 3:
            continue
        bad = []
        for i, z in enumerate(ad):
            if z[2] is None:
                continue
            nb = [ad[j][2] for j in (i - 1, i + 1)
                  if 0 <= j < len(ad) and ad[j][2] is not None]
            if not nb:
                continue
            if z[2] < PRUNE_REL * float(np.median(nb)) and z[2] < PRUNE_ABS:
                bad.append(z[3])
        if bad and len(bad) <= 0.40 * len(ad):
            drop.update(id(p) for p in bad)
    if not drop:
        return res
    kept = [p for p in pins if id(p) not in drop]
    n_pred = sum(1 for p in kept if p.get("predicted"))
    counts = {s: sum(1 for p in kept if p["side"] == s) for s in ALL_SIDES}
    return kept, counts, dict(info, predicted=n_pred, pruned=len(drop))


def _is_o_label(label):
    """是否 O 类（**只有一排**引脚，且恰好 7 个）。

    兼容 "O" / "o" / "O7" / "O_7" 等写法。

    ⚠ O 是**默认类别**（`LABEL_ALIAS` 里 O→"1" 就是「一排」的意思），所以判据
    收得很紧：只认字面 O（可带数字后缀），**故意不认** "1" / "OTHER" / "单" 这些
    `kind_of` 里的历史别名 —— 那几种写法原本走通用算法（点数按图像自适应），
    悄悄改成「恒 7 点」会让旧标签静默变样。用户 2026-10-27 确认：O = 单排 7 脚。

    ⚠ 与 C/F/T/L 一样必须在 `detect()` 里**前置**于 `LABEL_ALIAS`：ALIAS 会把
    "O" 映射成 "1"，之后就再也认不出这是 O 了。
    """
    s = str(label or "").strip().upper()
    if not s:
        return False
    head = s.split("_")[0].split("-")[0].strip()
    if head == "O":
        return True
    return head.startswith("O") and head[1:].isdigit()


_O_MOD = None


def _load_o():
    """延迟导入 pin_o（同一张图上没有 O 芯片时完全不加载）。"""
    global _O_MOD
    if _O_MOD is None:
        import pin_o
        _O_MOD = pin_o
    return _O_MOD


def _detect_o(img, box, opt):
    """O 类入口：单排、恒 7 点（算法与验证见 pin_o.py）。

    `pin_o.detect_O` 返回 `(pins, info)` 两元组（沙盒口径，不掺工具侧结构），
    这里补成 `detect()` 约定的 `(pins, counts, info)`：counts 按 side 计数，
    info 补 total/counts 两个键（与 pin_t/pin_l 的输出同构）。

    出错或不可信（对边也有整排证据 → 「不止一排」）时返回空结果 + `info["failed"]`，
    **不**回退通用算法 —— 通用算法只有「两条列」模型，喂单排 7 脚会硬凑出
    8~9 个点（多出来的全是金属体左右侧棱）。宁可 0 点（UI 打 LOW）。

    `opt["n"]` 只给**自定义类别**用（如"选 O 大类、每排 5 脚"），内置 O 不传，
    因此仍走 `detect_O` 的默认 7 —— 原类别行为不变。
    """
    n = (opt or {}).get("n")
    try:
        if n:
            pins, info = _load_o().detect_O(img, box, int(n))
        else:
            pins, info = _load_o().detect_O(img, box)
    except Exception as exc:
        return [], {}, dict(failed=True, reason="O detector error: %s" % (exc,))
    info = dict(info or {})
    counts = dict(top=0, right=0, bottom=0, left=0)
    for q in pins:
        sd = q.get("side")
        counts[sd] = counts.get(sd, 0) + 1
    info["total"] = len(pins)
    info["counts"] = dict(counts)
    return pins, counts, info


def _is_c_label(label):
    """是否 C 类（两端各一个引脚的贴片件）。

    兼容 "C" / "c" / "C_3" 等写法。C 类不走 LABEL_ALIAS 的数字映射
    （O→1排 / T→2排 / F→4排 那套），而是直接路由到独立算法 pin_c.py。
    """
    s = str(label or "").strip().upper()
    return bool(s) and s.split("_")[0].split("-")[0].strip() == "C"


_C_MOD = None


def _load_c():
    """延迟导入 pin_c（与 grayboost 桥接同样的理由：不加载就用不到）。"""
    global _C_MOD
    if _C_MOD is None:
        import pin_c
        _C_MOD = pin_c
    return _C_MOD


def _detect_c(img, box, opt):
    """C 类入口：两端各取一个焊盘中心，恒 2 点（算法与标定见 pin_c.py）。

    出错时返回空结果 + info["failed"]，**不**回退到通用算法 —— 通用算法只有
    「两条列」模型，对只有 2 个引脚的贴片件会胡乱拟合出十几二十个点，
    宁可显示 0 点（UI 会打 LOW 标记）也不要给假结果。
    """
    try:
        return _load_c().detect_C(img, box, opt)
    except Exception as exc:
        return [], {}, dict(failed=True, reason="C detector error: %s" % (exc,))


def _is_v_label(label):
    """是否 V 类（两端各一个引脚的贴片件，与 C **共用同一套算法**）。

    兼容 "V" / "v" / "V_3" 等写法。V 在工具里是**独立类别**（独立编号 V_1、
    独立 JSON label），但识别时直接转调 `pin_c.detect_C` —— 两者都是「两端
    一个焊盘、恒 2 点」的几何，`detect_C` 的轴判据（`axis = 1 if h >= w else 0`）
    已经把竖放（C）与横放（V）都覆盖了，不需要第二套代码。

    与 C 一样**不走 LABEL_ALIAS 的数字映射**，必须在 `detect()` 里前置。

    ⚠ 容忍尾随数字（"V1"/"V2"）：YOLO 老模型把 F 类叫 "F1".."F4"，同一个坑，
    这里照 `_is_f_label` 的口径兜住。不兜的话 `kind_of` 会认成 V 而算法仍走通用
    路径，2 点件会被拟合成十几二十个点 —— 比直接 0 点更难查。
    """
    s = str(label or "").strip().upper()
    if not s:
        return False
    head = s.split("_")[0].split("-")[0].strip()
    if head == "V":
        return True
    return len(head) > 1 and head[0] == "V" and head[1:].isdigit()


def _detect_v(img, box, opt):
    """V 类入口：转调 C 的两端件算法，恒 2 点。

    与 `_detect_c` 的差别只有两处：错误信息写 "V"（不误报成 C 出错），
    以及把 info["label"] 覆盖为 "V"（`pin_c.detect_C` 内部硬编码写的是 "C"，
    只用于诊断日志，没有任何代码读它）。
    """
    try:
        pins, counts, info = _load_c().detect_C(img, box, opt)
    except Exception as exc:
        return [], {}, dict(failed=True, reason="V detector error: %s" % (exc,))
    if isinstance(info, dict):
        info["label"] = "V"
    return pins, counts, info


def _is_f_label(label):
    """是否 F 类（四边 QFP，**每排严格 32 脚**）。

    兼容 "F" / "f" / "F1" / "F_3" 等写法（YOLO 老模型给的是 "F1"）。
    与 C 一样**不走 LABEL_ALIAS 的数字映射**，直接路由到独立算法 pin_f.py。

    ⚠ 2026-10-26 起 F 的语义收窄：只支持每排 32 脚的这一款（用户明确
    「之前的 F 要识别很多类型，现在的 F 只需要识别这一种」）。旧真值库里那些
    17/24/25/28/36 脚/side 的 F 芯片不再支持 —— 所以这里对它们不做兜底回退。
    """
    s = str(label or "").strip().upper()
    if not s:
        return False
    head = s.split("_")[0].split("-")[0].strip()
    return head in ("F", "F1", "F2", "F3", "F4")


_F_MOD = None


def _load_f():
    """延迟导入 pin_f（同一张图上没有 F 芯片时完全不加载）。"""
    global _F_MOD
    if _F_MOD is None:
        import pin_f
        _F_MOD = pin_f
    return _F_MOD


def _detect_f(img, box, opt):
    """F 类入口：四边各 32 点（算法与验证见 pin_f.py）。

    出错或不可信时返回空结果 + info["failed"]，**不**回退通用算法 ——
    通用算法只有「两条列」模型，对四边 QFP 会硬凑出九十来个假点；
    GrayBoost 的 quad/dual 也没有 32 先验，实测同一张图给过 8/12/85/91 点。
    宁可显示 0 点（UI 打 LOW）也不要给"数量看着像、位置全错"的结果。
    """
    try:
        # 注意签名是 detect_F(img, box, n=NF, opt=None)：第三个位置参数是 n，
        # 别顺手传 opt（传 None 会让 n=None -> int(None) 直接抛）。
        return _load_f().detect_F(img, box)
    except Exception as exc:
        return [], {}, dict(failed=True, reason="F detector error: %s" % (exc,))


def _is_t_label(label):
    """是否 T 类（两侧封装，**只上下两排、每排严格 10 个**引脚）。

    兼容 "T" / "t" / "T1" / "T_3" 等写法。与 C/F 一样**不走 LABEL_ALIAS 的数字
    映射**（ALIAS 会把 "T" 变成 "2"，之后就认不出这是 T 了），直接路由到独立
    算法 pin_t.py。

    ⚠ 2026-10-26 起 T 的语义收窄：只支持「上下两排、每排 10 脚」这一款（用户
    明确「T 类型芯片只会是上下引脚排布，每排引脚有且只有 10 个引脚」）。旧库里
    每排 4~28 脚、甚至左右排布的 T 不再支持 —— 所以这里不做兜底回退。

    ⚠ 只认 T 前缀，**故意不认** LABEL_ALIAS 里的历史别名（"SOP" / "2" / "两"）：
    本轮真值库 305 颗 T 全是字面 "T"，把这些别名拉进来只会放大误伤面，还会让
    `PinTool/core/pins.py::precise_kind` 的「精确解」判据与本处路由不一致。
    """
    s = str(label or "").strip().upper()
    if not s:
        return False
    head = s.split("_")[0].split("-")[0].strip()
    return head in ("T", "T1", "T2", "T3", "T4")


_T_MOD = None


def _load_t():
    """延迟导入 pin_t（同一张图上没有 T 芯片时完全不加载）。"""
    global _T_MOD
    if _T_MOD is None:
        import pin_t
        _T_MOD = pin_t
    return _T_MOD


def _detect_t(img, box, opt):
    """T 类入口：上下两排各 10 点（算法与验证见 pin_t.py）。

    出错或不可信时返回空结果 + info["failed"]，**不**回退通用算法 —— 通用算法
    那套「两条列 + geo_classify 自动改判」正是旧版 T 出现「被判成 left/right」
    「多出假排」的来源，而且它的点数不受控。宁可 0 点（UI 打 LOW），也不要
    "数量看着像、位置全错"。
    """
    try:
        # 签名 detect_T(img, box, opt=None)：opt 是增强开关 dict（本模块只读 "n"），
        # 传 None 也安全（n 缺省回落 NT=10）。
        return _load_t().detect_T(img, box, opt)
    except Exception as exc:
        return [], {}, dict(failed=True, reason="T detector error: %s" % (exc,))


def _is_l_label(label):
    """是否 L 类（两侧封装，**两排、每排严格 3 个**引脚）。

    兼容 "L" / "l" / "L1" / "L_3" 等写法。与 C/F/T 一样**不走 LABEL_ALIAS 的数字
    映射**，直接路由到独立算法 pin_l.py。

    ⚠ L 与 T 是**两种不同封装**，不能合并：
      · T = 上下两排、每排 10 脚，排布固定；
      · L = 两排**正交相对**（上下 或 左右，随图片变）、每排 3 脚。
    实测 L 有半数样本是左右排布，喂给 pin_t（上下硬先验 + 10 脚硬先验）只会 0 点。

    ⚠ 只认 L 前缀，**故意不认**任何历史别名：`L` 是 2026-10-26 用户为本类新起的
    标签（更早曾用小写 "t"，已随本次接入一并废弃），没有历史库要兼容。
    """
    s = str(label or "").strip().upper()
    if not s:
        return False
    head = s.split("_")[0].split("-")[0].strip()
    return head in ("L", "L1", "L2", "L3", "L4")


_L_MOD = None


def _load_l():
    """延迟导入 pin_l（同一张图上没有 L 芯片时完全不加载）。"""
    global _L_MOD
    if _L_MOD is None:
        import pin_l
        _L_MOD = pin_l
    return _L_MOD


def _detect_l(img, box, opt):
    """L 类入口：两排各 3 点，恒 6 点（算法与验证见 pin_l.py）。

    出错或不可信时返回空结果 + info["failed"]，**不**回退通用算法 —— 通用算法
    只有「两条列」模型，对「两排各 3 脚」会硬凑出十几个假点；GrayBoost 的
    quad/dual 也没有「3 只等距」先验。宁可 0 点（UI 打 LOW），也不要
    "数量看着像、位置全错"。
    """
    try:
        return _load_l().detect_L(img, box, opt)
    except Exception as exc:
        return [], {}, dict(failed=True, reason="L detector error: %s" % (exc,))


_K_MOD = None


def _load_k():
    """延迟导入 pin_k（同一张图上没有 K 类别时完全不加载）。"""
    global _K_MOD
    if _K_MOD is None:
        import pin_k
        _K_MOD = pin_k
    return _K_MOD


def _detect_k(img, box, opt):
    """K 类入口：任意朝向、任意每排脚数的双排件（算法与标定见 pin_k.py）。

    只在「自定义类别的 base == K」时走到，**六个内置类别一律不经过这里**，
    所以 T / L 的既有行为一个字节都不变。
    """
    try:
        return _load_k().detect_K(img, box, opt)
    except Exception as exc:
        return [], {}, dict(failed=True, reason="K detector error: %s" % (exc,))


# --------------------------------------------------------------------------- #
# 自定义类别（2026-11-11）：基础大类 + 每排齿数 n，转调原算法，不动原常量
# --------------------------------------------------------------------------- #
def _custom_spec(label):
    """label 是否属于用户新增的自定义类别；是则返回 spec，否则 None。

    放在 `detect()` 最前面、**六个 `_is_*_label` 之前**：自定义类别的名字是
    任意的（Y / AA / ZZZ），本身不会命中那六个判定；名字与内置/历史别名冲突
    的情况在 `pin_kinds.validate()` 就挡掉了，所以这里不需要再让路。
    """
    try:
        import pin_kinds
        return pin_kinds.spec_of(label)
    except Exception:
        return None


def _detect_custom(img, box, spec, opt):
    """自定义类别入口：转调基础类算法，**只把每排齿数 n 换成自己的值**。

    这是"新增类别不影响原类别"的全部实现 —— 没有复制任何算法，也没有改任何
    模块常量（pin_t.NT / pin_f.NF / pin_o.N_PINS 依然原样），只是把 n 塞进
    `opt["n"]` 或位置参数。所以原类别的识别结果与加类别之前逐点相同。

    引擎选择（n 与基础类常量一致时一律走原模块，保证"选 L 且 n=3"与原来的 L
    完全等价）：
      T           -> pin_t.detect_T（读 opt["n"]，全链路按 n 走）
      F           -> pin_f.detect_F（n 是位置参数）
      O           -> pin_o.detect_O（n 是位置参数）
      L 且 n == 3 -> pin_l.detect_L（保留三点专属逻辑：双瓣焊盘合并按 3 段做）
      L 且 n != 3 -> pin_t.detect_T（同一族"双排对边"；pin_l 的 `_to3()` 是
                     为 3 点写的，硬套 14 点会把相邻焊盘并错，故换 T 引擎）
      C / V       -> 两端件，n 恒 1，转调 pin_c（与内置 C 同一套）
      K           -> pin_k.detect_K（任意朝向、任意 n 的"双排对边"引擎，
                     2026-11-11 接入。竖放/横放都自己判方向，不依赖标签）
    """
    base = str(spec.get("base") or "").upper()
    name = spec.get("name")
    n = int(spec.get("n") or 0)
    o = dict(opt or {})
    o["n"] = n
    engine = base
    try:
        if base == "K":
            pins, counts, info = _detect_k(img, box, o)
        elif base == "T":
            pins, counts, info = _load_t().detect_T(img, box, opt=o)
        elif base == "F":
            pins, counts, info = _load_f().detect_F(img, box, n=n, opt=o)
        elif base == "O":
            pins, counts, info = _detect_o(img, box, o)
        elif base == "L":
            if n == 3:
                pins, counts, info = _load_l().detect_L(img, box, opt=o)
            else:
                engine = "T"
                pins, counts, info = _load_t().detect_T(img, box, opt=o)
        elif base in ("C", "V"):
            n = 1
            pins, counts, info = _detect_c(img, box, o)
            pins = [dict(p) for p in pins]
            engine = "C"
        else:
            return [], {}, dict(failed=True, kind=name,
                                reason="未知基础大类「%s」" % base)
    except Exception as exc:
        return [], {}, dict(failed=True, kind=name, base=base, n=n,
                            reason="%s detector error: %s" % (name, exc))

    info = dict(info or {})
    info["kind"] = name          # 诊断日志与界面都按这个显示（不是基础类）
    info["base"] = base
    info["n"] = n
    info["engine"] = engine
    info["custom"] = True
    info["total"] = len(pins)
    if isinstance(counts, dict):
        info["counts"] = dict(counts)
    return pins, counts, info


def detect(img, box, label, min_area=6, opt=None):
    """引脚识别。
    opt（可选，dict）逐项开启增强（默认全开，见 DEFAULT_OPT）：
      geo_classify  用四边梳齿证据 + label 自动重判 两侧/四边（问题1）
      symmetrize    对边对称时镜像补齐整侧漏检（问题3）
      interp_gaps   间距中位数的整数倍缺位 -> 插值补点（问题4）
      dense_pitch   密排（pitch 小）时放宽峰值合并与声势门槛（问题2）
      contrast_boost 低对比芯片先按原图跑，结果差则用局部 CLAHE 增强图重试（问题4）

    返回 (pins, counts, info)；info["predicted"] 为补出来的点数。
    """
    # ---- 自定义类别先查注册表（用户新增的 Y / AA …）----
    # 必须在下面六个 _is_*_label 之前：自定义类别走的是"基础算法 + 换 n"，
    # 一旦漏到这里就会被 LABEL_ALIAS 吞掉、落到通用算法上（点数按图像自适应）。
    _spec = _custom_spec(label)
    if _spec is not None:
        return _detect_custom(img, box, _spec, opt)

    # C / V / F / T / L / O 都必须先判，再走 LABEL_ALIAS：ALIAS 会把 F 映射成 "4"、
    # O 映射成 "1"，之后就再也认不出来（旧实现对 F 也是这么绕过去的，这里显式前置）。
    if _is_o_label(label):
        # O 类（单排，2026-10-27 接入）：独立算法，**恒 7 点**。
        # 也**不**参与下面的对边补齿/对称补点/插值补点/跨片补点 —— 那些都是
        # "点数未知、靠结构补全"的思路，对 7 点精确解只会改成 6/8 点。
        return _detect_o(img, box, opt)
    if _is_c_label(label):
        # C 类（电阻/电容/LED 等两端件）：独立算法，恒 2 点。
        # 也**不**参与下面的对边补齿/对称补点/插值补点 —— 那些都是为多脚封装写的。
        return _detect_c(img, box, opt)
    if _is_v_label(label):
        # V 类（2026-11-03 接入）：与 C 共用两端件算法，恒 2 点，只是类别与编号独立。
        # 同样不参与对边补齿/对称补点/插值补点。
        return _detect_v(img, box, opt)
    if _is_f_label(label):
        # F 类（四边 QFP，每排 32）：独立算法，四排各 32 点。
        return _detect_f(img, box, opt)
    if _is_t_label(label):
        # T 类（两侧，上下两排各 10）：独立算法，恒 20 点。
        # 也**不**参与下面的对边补齿/对称补点/插值补点 —— 那些都是"点数未知、
        # 靠结构补全"的思路，对 10×2 的精确解只会改坏。
        return _detect_t(img, box, opt)
    if _is_l_label(label):
        # L 类（两侧，两排各 3）：独立算法，恒 6 点。轴由图像定（上下 或 左右）。
        # 与 T 一样**不**参与对边补齿/对称补点/插值补点 —— 6 点是精确解，
        # 而且它是这三种补点最容易改坏的（3 只焊盘，多补一个就成 7 点）。
        return _detect_l(img, box, opt)
    label = LABEL_ALIAS.get(str(label), str(label))
    o = dict(DEFAULT_OPT)
    if opt:
        o.update(opt)
    raw = _detect_raw(img, box, label, min_area, o)
    if not o.get("contrast_boost", True):
        return raw
    if not _needs_boost(*raw):
        return raw
    img2 = _enhance_patch(img, box)
    if img2 is img:
        return raw
    en = _detect_raw(img2, box, label, min_area, o)
    en = _prune_weak(img, box, en)
    if _boost_better(raw, en) and _boost_keeps_sides(raw, en):
        en[2]["boosted"] = True
        return en
    return raw


def _detect_raw(img, box, label, min_area, o):
    """单次识别（不做增强重试）；``o`` 为已合并的选项 dict。"""
    ctx = _Ctx(img, box)
    if o.get("dense_pitch", True):
        sides, best, q, src, allc = _orient2(ctx, label, use_geo_classify=o.get("geo_classify", True))
    else:
        sides, best, q = _orient(ctx, label)
        src = "label"
        allc = {}
    rows = {}
    # 对边栅格补齿：必须在 _make_pins 之前做，补出来的是「真齿」（会走正常的
    # 全局深度 + 局部归位），而不是对称补点那种「预测点」。
    if o.get("geo_classify", True):
        try:
            _partner_tooth_fill(ctx, sides, best)
        except Exception:
            pass
    for s in sides:
        bq, T, band, teeth, pitch, prof = best[s][1]
        if not teeth:
            rows[s] = []
            continue
        if o.get("dense_pitch", True) and allc.get(s):
            teeth, pitch2, T2, band2, _ = _recover_dense(
                ctx, s, [int(t) for t in teeth], pitch, prof, allc[s], bq)
            if pitch2:
                if T2 is not None:
                    band = band2
                    T = T2
                    prof = ctx.profile(s, T, band)
                best[s] = (bq, (bq, T, band, teeth, pitch2, prof))
        teeth = _fill_slots(list(teeth), prof, pitch)
        rows[s] = _make_pins(ctx, s, band, teeth, pitch, T)
    if len(sides) == 4:
        rows = _dedupe_corners(rows, ctx, sides)

    pitch_of = {s: best[s][1][4] for s in sides}
    n_pred = 0
    if o.get("symmetrize", True) and len(sides) >= 2:
        before = sum(len(rows.get(s, [])) for s in sides)
        rows = _symmetrize(rows, sides, ctx, pitch_of, best=best)
        n_pred += sum(len(rows.get(s, [])) for s in sides) - before
    if o.get("interp_gaps", True):
        # 对边 pitch 提示：同款芯片对边引脚数通常相同，用对边的实测 pitch 校正
        # 「本侧密排但漏点多」时被高估的中位间距。
        opp = {}
        for s in sides:
            o_s = OPPOSITE.get(s)
            if o_s in pitch_of and pitch_of[o_s]:
                opp[s] = float(pitch_of[o_s])
        before = sum(len(rows.get(s, [])) for s in sides)
        rows = _interp_gaps(rows, sides, pitch_of, opp_pitch=opp, ctx=ctx)
        n_pred += sum(len(rows.get(s, [])) for s in sides) - before
    if len(sides) == 4:
        rows = _dedupe_corners(rows, ctx, sides)

    pins = []
    for s in ALL_SIDES:
        for t in rows.get(s, []):
            px = float(np.clip(round(t["x"], 2), ctx.x0, ctx.x1))
            py = float(np.clip(round(t["y"], 2), ctx.y0, ctx.y1))
            d = dict(x=px, y=py, side=s)
            if t.get("predicted"):
                d["predicted"] = True
            pins.append(d)
    counts = {s: len(rows.get(s, [])) for s in ALL_SIDES}
    return pins, counts, {"Vthr": {s: best[s][1][1] for s in sides},
                          "pitch": {s: best[s][1][4] for s in sides},
                          "band": {s: round(best[s][1][2] or 0.0, 1) for s in sides},
                          "sides": tuple(sides),
                          "classify": src,
                          "predicted": n_pred,
                          "qsum": float(sum(q.get(s, 0.0) for s in sides)),
                          "quality": {k: round(v, 1) for k, v in q.items()}}


def order_ccw(pins):
    """Counter-clockwise as viewed normally (screen y down):
    top L->R, right T->B, bottom R->L, left B->T; pin 1 starts top-left."""
    buck = {s: [] for s in ALL_SIDES}
    for p in pins:
        buck[p["side"]].append(p)
    out = []
    for s in CCW_SIDES:
        g = buck[s]
        if not g:
            continue
        if s == "top":
            g.sort(key=lambda p: p["x"])
        elif s == "right":
            g.sort(key=lambda p: p["y"])
        elif s == "bottom":
            g.sort(key=lambda p: -p["x"])
        else:
            g.sort(key=lambda p: -p["y"])
        out.extend(g)
    return out
