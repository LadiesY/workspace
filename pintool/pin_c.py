# -*- coding: utf-8 -*-
"""C 类引脚识别（两端件：中间一个元件体，两端各一个金属化焊盘，恒 2 点）。

用于电阻 / 电容 / LED 等贴片两端件。由 `pin_detect.detect(img, box, "C", opt)`
自动路由到本模块（见 `pin_detect._is_c_label`）。

接口与 pin_detect 同口径：

    detect_C(img, box=None, opt=None) -> (pins, counts, info)

    pins   元素 {"x", "y", "side", "score", "pad_width", "region"}
           —— 坐标 = 输入 img 坐标系（box 平移已在内部处理）
    counts {"left":1,"right":1}（横件）或 {"top":1,"bottom":1}（竖件）
    info   诊断字段（axis / method / pad_frac / regions / sep …）

核心判据（2026-10-15 定版，代号 pad-depth）
------------------------------------------
元件两端沿主轴的亮度剖面是「外丝印亮带 → 焊盘面(偏暗) → 锡角/电极亮峰 → 体内暗区」
的多峰结构；**电极/锡角亮峰总比焊盘面更亮**，所以任何「最亮 / 亮区间」判据都会落到
电极上，比标注员要的焊盘面中心偏内 5~10px。

因此：**主轴位置用几何先验（焊盘中心深度 = 主轴长 × 13.5%，从框外沿算），
横轴仍由图像定（该深度 ±15% 主轴长窗口内，亮像素 2%~98% 分位中点）。**

13.5% 由 23 个用户标注真值点标定（两块生产板 11 点 + 特写样本 12 点）。
规则评测台对比（同一批真值）：

    纯剖面（电极峰边界取中）   生产板 mean 28.4 / max 43.5 px   特写 3/12
    几何 0.10 + 剖面混合       生产板 mean 10.8 / max 16.5 px   特写 6/12
    几何 0.135 + 极值吸附      生产板 mean  1.1 / max  2.3 px   特写 10/12
    几何 0.135 纯（无吸附）    生产板 mean  0.86 / max 1.6 px   特写 12/12   ← 采用

即：吸附与剖面修正都没有收益，纯几何最优。后续在 5 块生产板（V3.0 / V5.(1) /
V5.(14) / V5.(19) / V5.(39)，共 74 颗、主轴长 34~100px）上逐颗目检复验通过。
不要再用「焊盘亮区中心」当主轴位置：它在双峰剖面（焊盘 + 电极）上会 ±9px 乱跳。
"""

import warnings

import cv2
import numpy as np

BAND_FRAC = 0.70         # 中位投影时横方向取中央带宽比例
PAD_DEPTH_FRAC = 0.135   # ★ 焊盘中心距外沿的深度（占主轴长度），由 23 个真值点标定
CROSS_WIN_FRAC = 0.15    # 横轴定位窗口半径（占主轴长度），围绕焊盘中心取亮像素


def _to_crop(img, box):
    """box=(x0,y0,x1,y1) 时取裁剪区并返回 (crop, ox, oy)；box 为 None 时整图。"""
    if box is None:
        return img, 0, 0
    x0, y0, x1, y1 = [int(round(float(v))) for v in box[:4]]
    h, w = img.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    return img[y0:y1, x0:x1], x0, y0


def _blue_mask(img):
    """蓝/青色像素（图上人工画的标注框与圆点）。生产图没有，仅用于保护带标注的评估图。"""
    if img.ndim != 3:
        return np.zeros(img.shape[:2], bool)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return (hsv[:, :, 0] >= 85) & (hsv[:, :, 0] <= 135) & (hsv[:, :, 1] >= 60)


def _kill_frame(white, h, w):
    """抹掉截图四边的白色框线（带白框的截图直接喂进来时的保护）。
    判据：某侧最外 4px 带大面积超亮 **且** 紧邻内侧偏暗 —— 真焊盘贴边时内侧同样亮，
    不会被误杀，所以对正常生产图无副作用。"""
    t = float(np.percentile(white, 90))
    sides = ((white[:, :4], white[:, 8:16], (slice(None), slice(0, 8))),
             (white[:, -4:], white[:, -16:-8], (slice(None), slice(-8, None))),
             (white[:4, :], white[8:16, :], (slice(0, 8), slice(None))),
             (white[-4:, :], white[-16:-8, :], (slice(-8, None), slice(None))))
    for band, inner, kill in sides:
        if (band > t).mean() > 0.5 and float(np.median(inner)) < 0.6 * t:
            white[kill] = 0.0


def _smooth(v, k):
    k = max(3, int(k) | 1)
    ker = np.ones(k, np.float32) / k
    return np.convolve(v, ker, mode="same")


def _profile(grayf, valid, axis, h, w):
    """中位数投影：axis=0 沿 x 逐列（横件），axis=1 沿 y 逐行（竖件）。
    横方向只取中央 BAND_FRAC 带宽；蓝色标注像素置 NaN 后取 nanmedian
    （中位数天然抑制零星高光，这也是当初不用「最亮」的原因）。"""
    if axis == 0:
        r0, r1 = int(h * (1 - BAND_FRAC) / 2), int(h * (1 + BAND_FRAC) / 2)
        band, vband = grayf[r0:r1, :], valid[r0:r1, :]
    else:
        c0, c1 = int(w * (1 - BAND_FRAC) / 2), int(w * (1 + BAND_FRAC) / 2)
        band, vband = grayf[:, c0:c1], valid[:, c0:c1]
    g = np.where(vband, band, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)      # 整列被蓝框覆盖 -> All-NaN（无害）
        prof = np.nanmedian(g, axis=0 if axis == 0 else 1)
    prof = np.where(np.isfinite(prof), prof, 0.0).astype(np.float32)
    return _smooth(prof, max(3, int(prof.size * 0.03)))


def _centroid_in_region(whitef, valid, axis, c0, c1):
    """区段内亮像素(≥0.5×区段峰)的 bbox 中心（沿轴取 2%~98% 分位防噪点）。

    不用质心：焊盘亮度常偏向一侧（外缘亮棱 / 丝印），质心会被拉偏；
    bbox 中心 = 焊盘可见范围的几何中心，对渐变边缘稳健。
    """
    if axis == 0:
        sub, vsub = whitef[:, c0:c1 + 1], valid[:, c0:c1 + 1]
    else:
        sub, vsub = whitef[c0:c1 + 1, :], valid[c0:c1 + 1, :]
    peak = float(sub.max())
    if peak <= 0:
        return None
    m = vsub & (sub >= 0.5 * peak)
    ys, xs = np.nonzero(m)
    if ys.size == 0:
        return None
    if axis == 0:
        col = xs + c0
        row = ys
    else:
        col = xs
        row = ys + c0
    x = float(np.clip(np.percentile(col, 98), 0, whitef.shape[1] - 1)
              + np.clip(np.percentile(col, 2), 0, whitef.shape[1] - 1)) / 2.0
    y = float(np.clip(np.percentile(row, 98), 0, whitef.shape[0] - 1)
              + np.clip(np.percentile(row, 2), 0, whitef.shape[0] - 1)) / 2.0
    return x, y, float(sub[m].mean()), int(c1 - c0 + 1)


def detect_C(img, box=None, opt=None):
    """C 类引脚识别：两端各取一个焊盘中心，恒返回 2 点。

    opt 可选项：`pad_frac` 覆盖焊盘深度比例（默认 0.135，一般不要动）。
    """
    crop, ox, oy = _to_crop(img, box)
    if crop.size == 0:
        return [], {}, dict(reason="empty crop")
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    grayf = gray.astype(np.float32)
    valid = ~_blue_mask(crop)
    # 白度图：焊盘是低饱和银白，板面是高饱和绿；V×(1-S/255) 压掉板面颜色
    if crop.ndim == 3:
        sat = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[:, :, 1].astype(np.float32)
        sat = cv2.GaussianBlur(sat, (3, 3), 0)
        whitef = grayf * (255.0 - sat) / 255.0
    else:
        whitef = grayf
    _kill_frame(whitef, *whitef.shape)
    h, w = gray.shape

    # 主轴 = 引脚分布方向：竖件(h>w)焊盘在上下 -> axis=1；横件 -> axis=0
    axis = 1 if h >= w else 0
    n_int = int(h if axis == 1 else w)
    frac = float((opt or {}).get("pad_frac", PAD_DEPTH_FRAC))

    prof = _profile(whitef, valid, axis, h, w)     # 仍算剖面：诊断用，不下发结果

    pins = []
    sides = (("left", "right") if axis == 0 else ("top", "bottom"))
    for from_lo, sd in ((True, sides[0]), (False, sides[1])):
        depth = frac * (n_int - 1)                              # 距外沿的深度（几何先验）
        c_center = depth if from_lo else (n_int - 1 - depth)
        hw = max(3, int(round(CROSS_WIN_FRAC * n_int)))
        c0 = int(np.clip(c_center - hw, 0, n_int - 1))
        c1 = int(np.clip(c_center + hw, 0, n_int - 1))
        r = _centroid_in_region(whitef, valid, axis, c0, c1)     # 只用它的「横轴」坐标
        if r is not None:
            x, y, score, width = r
        else:                                                    # 极端情形：退回框中心线
            x = (w - 1) / 2.0 if axis == 1 else c_center
            y = c_center if axis == 1 else (h - 1) / 2.0
            score, width = 0.0, 0
        if axis == 1:
            y = float(c_center)
        else:
            x = float(c_center)
        pins.append(dict(x=float(x + ox), y=float(y + oy), side=sd,
                         score=float(score), pad_width=int(width),
                         region=[int(c0), int(c1)]))
    counts = {"left": 1, "right": 1} if axis == 0 else {"top": 1, "bottom": 1}
    p1, p2 = pins
    info = dict(label="C", axis=axis, method="pad-depth", pad_frac=frac,
                sides=tuple(sides),
                thr=float(np.percentile(gray, 50)),
                prof_peak=float(prof.max()) if prof.size else 0.0,
                regions=[p1["region"], p2["region"]],
                sep=float(abs(p2["x"] - p1["x"]) if axis == 0 else abs(p2["y"] - p1["y"])))
    return pins, counts, info
