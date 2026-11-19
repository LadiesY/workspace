# -*- coding: utf-8 -*-
"""
引脚识别核心模块
================
封装项目里已验证的 pin_detect.py（V12）+ build_outputs 的绘制逻辑。

对外提供：
  detect_image(img, boxes)          —— 对一张图 + 若干矩形框做引脚识别
  render_annotated(img, chips)      —— 画绿框 + 红2px点 + 黄编号，返回新图
  run_pins(path, out_json, out_img) —— 单图完整流程（读图→识别→存json/存图）
  load_labelme(path)                —— 读 labelme json → [(label, box), ...]
"""
import os
import sys
import json
import math
import time
import cv2
import numpy as np

from . import grayboost as _GB          # GrayBoost 兜底通道（内部惰性导入）

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(HERE))
_PIN = None

# --------------------------------------------------------------- 决策日志
#  为什么要有它：GrayBoost 是在「主算法失效 / 单芯片特写」时兜底的，触发与否
#  依赖输入而不是代码，出了问题（"怎么还是老算法"）光看界面说不清。这里把每次
#  识别的判定过程落一行 JSON，事后直接翻 `PinTool/gb_debug.log` 即可定位。
#  关闭：设环境变量 PINTOOL_GB_DEBUG=0。日志超过 1MB 自动截断重开。
DEBUG_LOG = os.path.join(HERE, "gb_debug.log")
_DEBUG_ON = os.environ.get("PINTOOL_GB_DEBUG", "1") not in ("0", "off", "no")


def _gb_log(rec):
    if not _DEBUG_ON:
        return
    try:
        if os.path.exists(DEBUG_LOG) and os.path.getsize(DEBUG_LOG) > 1 << 20:
            os.remove(DEBUG_LOG)
        with open(DEBUG_LOG, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


# 模块被导入时记一条：能一眼看出「跑的是哪个进程、GrayBoost 可不可用」。
# 如果用户跑完发现日志里没有 import 行 —— 说明那个 PinTool 进程还是旧代码。
_gb_log(dict(t=time.strftime("%Y-%m-%d %H:%M:%S"), ev="import",
             pid=os.getpid(), exe=sys.executable,
             gb_ok=bool(_GB.available()), gb_err=_GB.error()))


# --------------------------------------------------------------- GrayBoost 策略
#  GrayBoost 是在低分辨率 / 模糊 / 单芯片特写图上重写的一套识别，实测在这 10 张
#  问题图上全部追平或反超 pin_detect。但它套到 OTF 整板的小芯片框上会大幅退化
#  （实测仅 51% 落在 pin_detect 的 ±35% 内），所以**默认只在明确判定为「非整板
#  输入」时才启用**，保证生产整板图零回归。
#
#  为什么不用「分辨率 < X」做判据：实测生产芯片框短边 57~200px、这 10 张问题图
#  101~663px，两者**完全重叠**，任何像素阈值都会把生产图一起命中。真正能干净分
#  开的是「这张图是不是单芯片特写」——生产整板固定 3072x2048，问题图最大也才
#  642x807，两者相差一个数量级。
#
#  两条独立的触发路径（任一成立即用 GrayBoost）：
#    1. box 占满整图（cover >= cover）—— 用户给的单芯片图 + 贴着芯片的框；
#    2. **整图本身就是小图且只标了 1~2 颗芯片（smallimg）** —— 用户给的是单芯片
#       特写，但框只圈住芯片本体、四周还留了大片板面，cover 照样很小。
#       这种情况以前会被漏掉（这就是"明明是问题图却还用老算法"的原因）。
GB_POLICY = dict(
    enabled=True,
    # 'auto' = 按下面条件自动判断；'on' = 全部走 GrayBoost；'off' = 全不用；
    # 'all' = **整板逐颗也试**（UI 的「整板逐颗也试 GrayBoost」复选框）。
    #        注意实测：整板上的小芯片（几十像素）新算法并不稳定（见下方注释），
    #        所以 'all' 只给用户按需开启，默认不参与自动判断。
    mode='auto',
    # 主算法结果「不可用」的判据：点数少于这个值
    min_pins=3,
    # 芯片框面积 / 整图面积 >= 此值 -> 判定为「单芯片特写」输入
    cover=0.35,
    # 小图判据：整图最长边 <= 此值(px) 且标注芯片数 <= max_chips -> 单芯片特写。
    # 生产整板是 3072x2048（最长边 3072），永远命中不了；问题图最大 807。
    # 设 0 = 关闭这条路径。
    img_max=1600,
    max_chips=2,
    # 板载芯片的「四边」复核：pin_detect 的模型里没有"四条边"这个概念（它只有
    # 左右/上下两条列），遇到 QFP 只能硬凑 —— 实测 ST 16C554（64 脚 QFP）它会
    # 报 72。GrayBoost 的 class C 专门解四边，且四边指纹（≥3 条真实点阵 +
    # 近方形暗本体 + 四边点数一致）几乎不可伪装。**这条与图片大小无关**，
    # 因此整板上也能生效；成立就用它的结果替换 pin_detect。
    # 实测安全性：V5.(23) 的 36 颗双列(T)芯片，0 颗被误判成 quad。
    quad=True,
    # 低分辨率阈值（取框短边像素）。默认 0 = 关闭。需要时自行调大，
    # 但请注意它同样会命中生产图（见上）。
    low_res=0,
)



def _load_pin():
    global _PIN
    if _PIN is not None:
        return _PIN
    if PROJ not in sys.path:
        sys.path.insert(0, PROJ)
    import pin_detect as P
    _PIN = P
    return P


SUPPORTED_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")


def load_labelme(path):
    """读 JSON 标签，返回 (shapes, image_filename)。
    shapes: [{'label':str,'box':[x0,y0,x1,y1],'shape_type':str}, ...]

    兼容三种格式，都可直接用于标签导入：
      1. labelme 原生：{"shapes":[{"label","points","shape_type"}], ...}
      2. 本工具 YOLO 模块输出：{"detections":[{"label","conf","box":[x0,y0,x1,y1]}]}
      3. 本工具引脚结果：{"chips":[{"label","box":[[x0,y0],[x1,y1]]}]}
    """
    d = json.load(open(path, encoding="utf-8"))
    shapes = []

    # --- 1. labelme 原生 shapes（points 形式）
    for s in d.get("shapes", []) or []:
        if s.get("shape_type") not in (None, "rectangle"):
            continue
        pts = s.get("points")
        if not pts:
            continue
        pts = np.array(pts, float)
        if pts.ndim != 2 or pts.shape[1] < 2:
            continue
        box = [float(pts[:, 0].min()), float(pts[:, 1].min()),
               float(pts[:, 0].max()), float(pts[:, 1].max())]
        shapes.append(dict(label=str(s.get("label")), box=box,
                           shape_type="rectangle"))

    # --- 2. YOLO 模块输出框 detections[{label,conf,box:[x0,y0,x1,y1]}]
    for b in d.get("detections", []) or []:
        box = b.get("box")
        if not box or len(box) < 4:
            continue
        x0, y0, x1, y1 = [float(v) for v in box[:4]]
        shapes.append(dict(label=str(b.get("label")),
                           box=[min(x0, x1), min(y0, y1),
                                max(x0, x1), max(y0, y1)],
                           shape_type="rectangle",
                           conf=float(b.get("conf", 1.0))))

    # --- 3. 引脚结果 chips[{label,box:[[x0,y0],[x1,y1]]}]
    if not shapes:
        for c in d.get("chips", []) or []:
            box = c.get("box")
            if not box:
                continue
            try:
                if isinstance(box[0], (list, tuple)):
                    (x0, y0), (x1, y1) = box[0], box[1]
                else:
                    x0, y0, x1, y1 = box[:4]
            except Exception:
                continue
            shapes.append(dict(label=str(c.get("label")),
                               box=[min(float(x0), float(x1)), min(float(y0), float(y1)),
                                    max(float(x0), float(x1)), max(float(y0), float(y1))],
                               shape_type="rectangle"))

    return shapes, d.get("imagePath") or d.get("image")


def _gb_decision(img, box, pins, info, opt, n_chips=1):
    """是否对这块芯片改用 GrayBoost -> (bool, reason, whole)。

    reason 取值：force / weak / lowres / closeup / smallimg（'' = 不用）
    whole = True 表示「应该把整幅图交给 GrayBoost」而不是只交给框内的裁剪，
    用于「整图就是单芯片特写、但框只圈了芯片本体」的情况。
    """
    pol = GB_POLICY
    mode = (opt or {}).get("grayboost", pol["mode"])
    if mode == "off" or not pol["enabled"]:
        return False, "", False
    if mode == "on":
        return True, "force", False
    if mode == "all":
        return True, "board", False              # 整板逐颗也试（用户手动开启）
    if info is None or len(pins) < int(pol["min_pins"]):
        return True, "weak", False               # 主算法几乎没找到点
    x0, y0, x1, y1 = [float(v) for v in box[:4]]
    if pol["low_res"] and min(abs(x1 - x0), abs(y1 - y0)) <= float(pol["low_res"]):
        return True, "lowres", False
    if _GB.coverage(box, img.shape) >= float(pol["cover"]):
        return True, "closeup", False            # 框占满整图：单芯片特写
    # 整图很小 + 只标了 1~2 颗 -> 也是单芯片特写（框只圈了本体，四周留了板面）。
    # 这条必须把整幅图交给 GrayBoost：只喂框内裁剪会把芯片切掉一半，反而更差。
    imax = int(pol.get("img_max", 0))
    if imax and max(img.shape[:2]) <= imax and n_chips <= int(pol.get("max_chips", 2)):
        return True, "smallimg", True
    return False, "", False


def _gb_quad(img, box):
    """block-level 四边复核：是四边封装就返回 GrayBoost 结果，否则 None。

    为什么值得单开一条：pin_detect 的模型里只有"两条列"（左/右 或 上/下），
    遇到 QFP 只能硬凑 —— 实测 ST 16C554（64 脚）它报 72。而四边指纹
    （≥3 条真实点阵 + 近方形暗本体 + 四边点数一致）几乎不可伪装：
    V5.(23) 的 36 颗双列(T)芯片，0 颗被误判。开销实测 ~4ms/颗，可以忽略。
    """
    try:
        return _GB.detect_quad(img, box)
    except Exception:
        return None


def _maybe_grayboost(img, box, pins, counts, info, opt, n_chips=1):
    """触发条件成立时用 GrayBoost 的结果替换主算法结果。

    返回 (pins, counts, info, tag)；tag 为 '' / 'GB' / 'GB-MISS'（想救但没救到）。
    GrayBoost 不可用或没找到点时一律**保留主算法结果**，绝不把结果变没。
    """
    mode = (opt or {}).get("grayboost", GB_POLICY["mode"])
    if mode == "off" or not GB_POLICY["enabled"]:
        return pins, counts, info, ""          # 开关关掉 = 完全不碰 GrayBoost
    use, reason, whole = _gb_decision(img, box, pins, info, opt, n_chips)
    r = None
    if use:
        # 先问一句「这块芯片是不是四边都有真实点阵」，是就按 quad 走。**要紧**：
        # 通用 detect() 会外扩 10% 取上下文，在整板上会把邻件焊盘带进来，实测
        # 64 脚 QFP 会从 64 掉成 blob/4。所以 quad 一定要用紧贴框来判。
        r = _gb_quad(img, box)
        if r is not None:
            reason = "quad"
        else:
            try:
                # 'board'（整板逐颗都试）必须紧贴框：labelme 框已贴着元件，
                # 外扩 10% 会把邻件焊盘一起带进来（实测 64 脚 QFP 会掉成 blob/3）。
                r = _GB.detect(img, box, whole=whole,
                               margin=(0.0 if reason == "board" else None))
            except Exception:
                r = None
    elif GB_POLICY.get("quad", True):
        # ---- 板载芯片的四边复核（与图片大小无关，整板上也生效）
        #  pin_detect 的结构里没有"四条边"，两边列的点阵拟合遇到 QFP 只能硬凑。
        #  这里只问一句：这块芯片是不是一个**四边都有真实点阵的近方形封装**？
        #  是 -> 采信 GrayBoost 的 quad 结果；不是 -> 原样返回，什么都不动。
        r = _gb_quad(img, box)
        if r is not None:
            reason = "quad"
    miss = "GB-MISS" if reason in ("weak", "smallimg") else ""
    if r is None:
        return pins, counts, info, miss
    gp, gc, gi = r
    if len(gp) < int(GB_POLICY["min_pins"]):
        return pins, counts, info, miss
    if info is not None:
        gi["gb_main_pins"] = len(pins)
        gi["gb_main_sides"] = info.get("sides")
    gi["gb_reason"] = reason
    return gp, gc, gi, "GB"


def gb_summary(chips):
    """GrayBoost 兜底的概况（给 UI 显示用）。

    三种情况都要说清楚，否则用户看不出到底走没走新算法：
      · 走了并成功 -> 其中 N 个走 GrayBoost 兜底(原因)
      · 想走但新算法也没找到 -> 有 N 个尝试过 GrayBoost 但未取到结果
    """
    gb = [c for c in chips if c.get("method") == "grayboost"]
    miss = [c for c in chips if "GB-" in str(c.get("flags", ""))]
    parts = []
    if gb:
        rs = sorted({str(c.get("gb_reason")) for c in gb if c.get("gb_reason")})
        parts.append("其中 %d 个走 GrayBoost 兜底%s"
                     % (len(gb), ("（" + "/".join(rs) + "）") if rs else ""))
    if miss:
        parts.append("有 %d 个尝试过 GrayBoost 但未取到结果" % len(miss))
    return "，".join(parts)


def _box_xyxy(b):
    """把框统一成 (x0,y0,x1,y1)。兼容 [[x0,y0],[x1,y1]] 与 [x0,y0,x1,y1]。"""
    if not b:
        return None
    try:
        first = b[0]
        if hasattr(first, "__len__") and not isinstance(first, str):
            (ax, ay), (bx, by) = b[0], b[1]
        else:
            ax, ay, bx, by = b[:4]
        ax, ay, bx, by = float(ax), float(ay), float(bx), float(by)
    except Exception:
        return None
    return min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)


def assign_chip_ids(items, row_frac=0.75):
    """给每颗芯片（或每个检测框）编一个稳定、好念的 ID：`<label>_<序号>`。

    例：`T_1  T_2  F_1  O_1`。同一 label 内按**阅读顺序**（先上后下、同行先左
    后右）从 1 开始编号 —— 用户看图说话时（"T_5 第 3 脚"）能一眼对上位置，
    不用在整板上跳来跳去。

    同行判定：用该 label 下框高的中位数 × row_frac 作为行高做分桶，
    这样同一行里框高差几像素也不会被拆成两行。

    就地写入 `items[i]['id']`，返回 items。**只加字段，不动任何识别结果**。
    """
    if not items:
        return items
    groups = {}
    for i, it in enumerate(items):
        lab = str(it.get("label") or "").strip() or "X"
        groups.setdefault(lab, []).append(i)
    for lab, idxs in groups.items():
        rows = []
        for i in idxs:
            bb = _box_xyxy(items[i].get("box"))
            if bb is None:
                continue
            x0, y0, x1, y1 = bb
            rows.append((i, 0.5 * (y0 + y1), 0.5 * (x0 + x1), abs(y1 - y0)))
        if not rows:
            continue
        row_h = max(8.0, float(np.median([t[3] for t in rows])) * float(row_frac))
        rows.sort(key=lambda t: (int(t[1] // row_h), t[2]))
        for k, t in enumerate(rows, 1):
            items[t[0]]["id"] = "%s_%d" % (lab, k)
    return items


def find_pin(chips, chip_id, pin_idx):
    """按「芯片编号 + 引脚编号」查坐标（用户报 T_5 / 第 3 脚 时用）。

    返回 dict(idx,x,y,side,predicted,chip_id,label) 或 None。
    chip_id 支持 'T_5' 精确匹配，也支持只用 'T' 时取该类别第 1 颗（不推荐）。
    """
    cid = str(chip_id or "").strip()
    for c in chips or []:
        got = str(c.get("id") or "")
        if got != cid:
            continue
        for p in c.get("pins", []) or []:
            if int(p.get("idx", -1)) == int(pin_idx):
                d = dict(p)
                d["chip_id"] = got
                d["label"] = c.get("label")
                return d
    return None


def load_pins_json(path):
    """读一份 `*_pins.json`（或任何 {"chips":[...]}），返回 chips 列表。"""
    d = json.load(open(path, encoding="utf-8"))
    return d.get("chips") or []


def _order_f(pins):
    """F 类（四边 QFP）编号：**顶排最右一根 = 1**，从右往左、逆时针绕一圈。

    用户 2026-10-26 定的口径（与常见 QFP 实物丝印的 1 脚标记一致：1 脚在顶边右端，
    然后逆时针数）。四条边各自的读取方向：

        top     x 递减（右 → 左）   1 .. 32
        left    y 递增（上 → 下）   33 .. 64
        bottom  x 递增（左 → 右）   65 .. 96
        right   y 递减（下 → 上）   97 .. 128

    ⚠ 与「顶排左起、顺时针」正好差一个镜像：顺序**反过来**读四条边（top→left→
    bottom→right），每条边的扫描方向也反过来。改这里等于改全部 F 编号，改完必须
    按图核对 1 号落在顶排最右、128 号落在右排最上（与 1 号相邻）。
    """
    key = {"top": lambda p: -float(p.get("x", 0)),      # 右 → 左
           "left": lambda p: float(p.get("y", 0)),      # 上 → 下
           "bottom": lambda p: float(p.get("x", 0)),    # 左 → 右
           "right": lambda p: -float(p.get("y", 0))}    # 下 → 上
    out = []
    for s in ("top", "left", "bottom", "right"):
        g = [p for p in pins if p.get("side") == s]
        g.sort(key=key[s])
        out.extend(g)
    # 兜底：side 缺失/异常的少量点按阅读顺序接在末尾（宁可编号难看，也不能丢点）
    rest = [p for p in pins if p.get("side") not in key]
    rest.sort(key=lambda p: (round(float(p.get("y", 0)) / 6.0),
                             float(p.get("x", 0))))
    out.extend(rest)
    return out


def _order_t(pins):
    """T 类（上下两排）编号：**顶排最右一根 = 1**，从右往左、逆时针绕一圈。

    用户 2026-10-26 定，与 `_order_f` 同一套读法（所有类别统一）：

        top     x 递减（右 → 左）    1 .. 10
        bottom  x 递增（左 → 右）   11 .. 20

    即 1 = 顶排最右、10 = 顶排最左、11 = 底排最左、20 = 底排最右（1 与 20 都在右侧，
    逆时针绕一圈正好闭合成环）。

    ⚠ 与 `order_ccw`（顶排左起、顺时针）正好差一个镜像：只有两排，所以是
    「顶排反读 + 底排反读」的等价结果。改这里等于改全部 T 编号，改完必须按图核对
    1 号落在顶排最右、20 号落在底排最右（与 1 号同一列）。
    """
    key = {"top": lambda p: -float(p.get("x", 0)),      # 右 → 左
           "bottom": lambda p: float(p.get("x", 0))}    # 左 → 右
    out = []
    for s in ("top", "bottom"):
        g = [p for p in pins if p.get("side") == s]
        g.sort(key=key[s])
        out.extend(g)
    # 兜底：side 缺失/异常的少量点按阅读顺序接在末尾（宁可编号难看，也不能丢点）
    rest = [p for p in pins if p.get("side") not in key]
    rest.sort(key=lambda p: (round(float(p.get("y", 0)) / 6.0),
                             float(p.get("x", 0))))
    out.extend(rest)
    return out


def _order_l(pins):
    """L 类（两排各 3 脚）编号：与 F/T 同一套「从靠近右上角那只起、逆时针绕一圈」。

        ── 上下两排时 ──              ── 左右两排时 ──
        top     x 递减（右 → 左） 1..3    right   y 递增（上 → 下） 1..3
        bottom  x 递增（左 → 右） 4..6    left    y 递减（下 → 上） 4..6

    即 1 号永远在「先绕的那一排的右上角那一端」，1 与 6 相邻闭合。
    **两种排布的顺序不一样**，所以不能照抄 `_order_f` 的四边遍历顺序（它固定
    top→left→bottom→right，用在左右排布上会先给 left → 1 号跑到左列，与沙盒
    `pin_l.py::_number` 的结果相反）。这里显式按「有没有 top/bottom」分两支。

    ⚠ 改这里等于改全部 L 编号（沙盒与工具必须一致），改完按图核对：
    上下排 1=上排最右、6=下排最右；左右排 1=右列最上、6=左列最上。
    """
    sides = set(p.get("side") for p in pins)
    if sides & {"top", "bottom"}:
        key = [(("top"), lambda p: -float(p.get("x", 0))),      # 右 → 左
               (("bottom"), lambda p: float(p.get("x", 0)))]    # 左 → 右
    else:
        key = [(("right"), lambda p: float(p.get("y", 0))),     # 上 → 下
               (("left"), lambda p: -float(p.get("y", 0)))]     # 下 → 上
    out = []
    for s, f in key:
        g = [p for p in pins if p.get("side") == s]
        g.sort(key=f)
        out.extend(g)
    # 兜底：side 缺失/异常的少量点按阅读顺序接在末尾（宁可编号难看，也不能丢点）
    used = set(s for s, _f in key)
    rest = [p for p in pins if p.get("side") not in used]
    rest.sort(key=lambda p: (round(float(p.get("y", 0)) / 6.0),
                             float(p.get("x", 0))))
    out.extend(rest)
    return out


def _order_o(pins):
    """O 类（单排 7 脚）编号：与 F/T/L 同一套「从靠近右上角那只起、逆时针绕一圈」。

        top     x 递减（右 → 左）   1 .. 7
        left    y 递减（下 → 上）   1 .. 7
        bottom  x 递增（左 → 右）   1 .. 7
        right   y 递增（上 → 下）   1 .. 7

    只有一条边有点，所以「先绕哪条边」无关紧要；要紧的是**每条边各自的方向**：
    上排 1 在右端、下排 1 在左端、右列 1 在上端、左列 1 在下端 —— 这样单独看上排
    或下排时读法相反，但绕封装一圈仍是同一条（逆时针）路径，与 F/T/L 完全统一。
    用户 2026-10-27 选定：**与 F/T/L 统一**（不采用 C 的「阅读顺序」口径）。

    ⚠ 改这里等于改全部 O 编号，改完按图核对：上排 1=最右、下排 1=最左、
    右列 1=最上、左列 1=最下。
    """
    key = {"top": lambda p: -float(p.get("x", 0)),      # 右 → 左
           "left": lambda p: -float(p.get("y", 0)),     # 下 → 上
           "bottom": lambda p: float(p.get("x", 0)),    # 左 → 右
           "right": lambda p: float(p.get("y", 0))}     # 上 → 下
    out = []
    for s_ in ("top", "left", "bottom", "right"):
        g = [p for p in pins if p.get("side") == s_]
        g.sort(key=key[s_])
        out.extend(g)
    # 兜底：side 缺失/异常的少量点按阅读顺序接在末尾（宁可编号难看，也不能丢点）
    rest = [p for p in pins if p.get("side") not in key]
    rest.sort(key=lambda p: (round(float(p.get("y", 0)) / 6.0),
                             float(p.get("x", 0))))
    out.extend(rest)
    return out


def _sort_algo_pins(label, pins):
    """算法点的排序：C 按阅读顺序、F/T/L/O 按「顶排右起逆时针」、其余走 order_ccw。

    为什么 C 要特殊：order_ccw 对横件是 right → left（逆时针、从左上起），编号会
    变成「1 = 右端、2 = 左端」，与用户看图说话的直觉相反（也与开发期交付的编号相反
    —— 用户一直是按「引脚 2 = 右/下端」反馈位置的）。C 恒 2 点，用阅读顺序最自然：
    竖件 1=上 2=下、横件 1=左 2=右。y 先按 6px 分桶，避免横件两端 y 差几像素时顺序抖。

    为什么 F 要特殊：order_ccw 是「顶排左起、顺时针」，而 F 要的是它的镜像
    （顶排右起、逆时针），见 `_order_f`。

    为什么 T 要特殊：同上（用户要求所有类别编号口径统一），T 只有上下两排，
    见 `_order_t`。

    为什么 L 要特殊：口径与 F/T 相同，但 L 的**排布不固定**（上下 或 左右），
    两种排布的先绕那一排不同，见 `_order_l`。

    为什么 O 要特殊：单排 7 脚，口径也与 F/T/L 统一（用户 2026-10-27 选定），
    但只有一条边有点，四条边各自的方向必须逐条写清，见 `_order_o`。
    """
    k = kind_of(label)
    # K 大类（自定义类别，2026-11-11 接入）：编号口径与 T/L 相同 —— 1 号在
    # 「先绕那一排的右上角」，逆时针绕一圈。K 的排布不固定（上下 或 左右），
    # 所以直接用 `_order_l`（它按 pins 的 side 自动分两支）。
    # ⚠ 只对 base == "K" 生效；其它自定义类别仍走原路径，免得改动既有编号。
    try:
        import pin_kinds
        _sp = pin_kinds.spec_of(label)
        if _sp and str(_sp.get("base")).upper() == "K":
            return _order_l(pins)
    except Exception:
        pass
    if k in ("C", "V"):
        return sorted(pins, key=lambda p: (round(float(p.get("y", 0)) / 6.0),
                                           float(p.get("x", 0))))
    if k == "F":
        return _order_f(pins)
    if k == "T":
        return _order_t(pins)
    if k == "L":
        return _order_l(pins)
    if k == "O":
        # O 类（2026-10-27 接入）单排 7 脚，同样要「逆时针」口径而不是 order_ccw
        return _order_o(pins)
    P = _load_pin()
    try:
        return list(P.order_ccw(pins))
    except Exception:
        return list(pins)


def pack_chip(box, label, chip_id, pins, counts, info, gb_tag=""):
    """把一次识别结果 (pins, counts, info) 打包成 chips 列表里的一项。

    与 detect_image 的输出**完全同构**（chip_id / label / box / pin_count /
    side_counts / flags / method / gb_reason / pins），供单芯片重跑复用，
    保证「重跑替换」后 UI 和写出的 JSON 与整板识别长得一模一样。
    """
    info = info or {}
    ordered = _sort_algo_pins(label, pins)
    pins_json = []
    for idx, p in enumerate(ordered, 1):
        pins_json.append(dict(idx=idx, x=round(float(p["x"]), 1),
                              y=round(float(p["y"]), 1), side=p["side"],
                              predicted=bool(p.get("predicted", False))))
    flags = []
    if gb_tag == "GB":
        flags.append("GB")               # 已改用 GrayBoost 识别
    elif gb_tag == "GB-MISS":
        flags.append("GB-")              # 想救但 GrayBoost 也没找到
    if len(pins_json) < 3 and kind_of(label) not in ("C", "V"):
        # C / V（两端件）恒 2 点，<3 是它们的正常形态，打 LOW 只会误导
        flags.append("LOW")
    if info.get("boosted"):
        flags.append("ENH")
    if info.get("cross"):
        flags.append("CROSS%d" % info["cross"])
    x0, y0, x1, y1 = [float(v) for v in box[:4]]
    return dict(chip_id=chip_id, label=label,
                box=[[round(x0, 1), round(y0, 1)], [round(x1, 1), round(y1, 1)]],
                pin_count=len(pins_json),
                side_counts=counts,
                flags="+".join(flags),
                method=("grayboost" if gb_tag == "GB" else "pin_detect"),
                gb_reason=info.get("gb_reason"),
                pins=pins_json)


# ------------------------------------------------------- 人工补点 / 删点 / 召回率
#  为什么放在这里（而不是 UI 层）：这几件事都要动 `chips` 的数据结构，要求
#  「屏幕看到的、写进 JSON 的、召回率算的」三者永远一致。逻辑集中在一处，
#  UI 只负责把鼠标位置喂进来，就不会出现「补了点但 JSON 没变」这类错位。

def _rect_dist(x, y, box):
    """点到矩形框的欧氏距离（点在框内为 0）。"""
    bb = _box_xyxy(box)
    if bb is None:
        return 1e18
    x0, y0, x1, y1 = bb
    dx = max(x0 - x, 0.0, x - x1)
    dy = max(y0 - y, 0.0, y - y1)
    return float((dx * dx + dy * dy) ** 0.5)


def _nearest_side(x, y, box):
    """点落在框的哪条边（取最近的那条）—— 人工补点也要有 side，
    这样结构统计、order_ccw 重排都不会因为人工点而错乱。"""
    bb = _box_xyxy(box)
    if bb is None:
        return "top"
    x0, y0, x1, y1 = bb
    d = {"left": abs(x - x0), "right": abs(x - x1),
         "top": abs(y - y0), "bottom": abs(y - y1)}
    return min(d, key=d.get)


def chip_at(chips, x, y, tol=8.0):
    """找包含 (x, y) 的芯片下标；没有就找最近的框（容差 tol px）。找不到返回 None。

    优先「包含」且面积最小的框（框可能重叠，取最小的那个最符合直觉）。
    """
    if not chips:
        return None
    inside = []
    for i, ch in enumerate(chips):
        bb = _box_xyxy(ch.get("box"))
        if bb is None:
            continue
        x0, y0, x1, y1 = bb
        if x0 <= x <= x1 and y0 <= y <= y1:
            inside.append((abs(x1 - x0) * abs(y1 - y0), i))
    if inside:
        return min(inside)[1]
    near = [( _rect_dist(x, y, ch.get("box")), i) for i, ch in enumerate(chips)]
    near = [t for t in near if t[0] <= float(tol)]
    return min(near)[1] if near else None


def renumber_chips(chips):
    """重排编号（口径与 pack_chip 完全一致），就地写回。

    · 算法点：`_sort_algo_pins` 后 `idx` = 1..n（F/T 为顶排右起逆时针；其余为
      order_ccw 的顶排左起顺时针）；
    · 人工补点（manual）：按阅读顺序（先上后下、先左后右）重编 `name` = b1/b2/…，
      `idx` 接在算法点之后；并同步刷新 `pin_count` / `side_counts` / `manual_count`，
      保证「点个数 == 各边点数之和」这个不变量在任何编辑之后都成立。
    """
    for ch in chips or []:
        pins = list(ch.get("pins") or [])
        algo = [p for p in pins if not p.get("manual")]
        man = [p for p in pins if p.get("manual")]
        out = _sort_algo_pins(ch.get("label"), algo)
        for i, p in enumerate(out, 1):
            p["idx"] = i
            p.pop("name", None)
        man.sort(key=lambda p: (round(float(p.get("y", 0)) / 4.0),
                                float(p.get("x", 0))))
        for j, p in enumerate(man, 1):
            p["name"] = "b%d" % j
            p["idx"] = len(out) + j
        ch["pins"] = out + man
        ch["pin_count"] = len(ch["pins"])
        cnt = _side_counts(ch["pins"])
        ch["side_counts"] = cnt
        ch["manual_count"] = len(man)
    return chips


def add_manual_pin(chips, x, y, tol=8.0):
    """在 (x, y) 所在的芯片框内补一个点。返回 (chip_idx, pin) 或 (None, 原因)。

    人工补点的字段：`manual=True` + `name='bN'`，颜色在画面上是蓝色，
    与算法点（红）区分；坐标同步写进 JSON 的 chips[].pins。
    """
    if not chips:
        return None, "还没有引脚识别结果，请先点「引脚识别」。"
    ci = chip_at(chips, x, y, tol=tol)
    if ci is None:
        return None, "请点在某个芯片框内（框外不能补点）。"
    ch = chips[ci]
    # 离已有同类点太近就忽略，避免手抖点两下变成一个位置两个点
    for p in ch.get("pins") or []:
        if (float(p.get("x", 0)) - x) ** 2 + (float(p.get("y", 0)) - y) ** 2 < 4.0:
            return None, "这个位置已经有点了。"
    ch.setdefault("pins", []).append(
        dict(idx=0, x=round(float(x), 1), y=round(float(y), 1),
             side=_nearest_side(x, y, ch.get("box")),
             predicted=False, manual=True))
    renumber_chips(chips)
    best, bd = None, 1e18
    for p in ch["pins"]:
        if not p.get("manual"):
            continue
        d = (float(p.get("x", 0)) - x) ** 2 + (float(p.get("y", 0)) - y) ** 2
        if d < bd:
            bd, best = d, p
    return ci, best


def delete_pins(chips, refs):
    """按 [(chip_idx, pin_idx)] 删点（pin_idx 是 chips[chip]['pins'] 的下标）。

    返回实际删除的点数。删完统一重编号，保证编号连续、JSON 里的坐标也被删掉
    （调用方写回 JSON 即可，坐标不会残留在文件里）。
    """
    drop = {}
    for ci, pi in refs or []:
        drop.setdefault(int(ci), set()).add(int(pi))
    n = 0
    for ci, ps in drop.items():
        if not chips or not (0 <= ci < len(chips)):
            continue
        pinlist = chips[ci].get("pins") or []
        keep = [p for k, p in enumerate(pinlist) if k not in ps]
        n += len(pinlist) - len(keep)
        chips[ci]["pins"] = keep
    if n:
        renumber_chips(chips)
    return n


def snapshot_chips(chips):
    """编辑前拍一张快照（撤回用）。点数量级很小，直接深拷贝最省心。"""
    import copy
    return copy.deepcopy(chips if chips is not None else [])


def recall_of(chips):
    """召回率口径：红点 / (红点 + 蓝点) × 100%。

    红点 = 算法识别出的引脚（含智能预测点）；蓝点 = 人工补点。
    返回 (red, blue, pct)。没有任何点返回 (0, 0, 0.0)。
    """
    red = blue = 0
    for ch in chips or []:
        for p in ch.get("pins") or []:
            if p.get("manual"):
                blue += 1
            else:
                red += 1
    tot = red + blue
    return red, blue, (100.0 * red / tot if tot else 0.0)


# ------------------------------------------------------------- 矩形框（芯片框）编辑
#  与「人工补点」同样的理由：删框 / 改框 / 改标签 / 新建框都要动 chips 的
#  数据结构（box / label / id / chip_id），而且必须保证「屏幕、JSON、编号」
#  三者一致，所以统一放在 core，UI 只负责把鼠标坐标喂进来。

KINDS = ("O", "F", "T", "C", "L", "V")  # 支持的封装类别（与 pin_detect 的类别口径一致）
# 各类点数硬先验（由各自 pin_x.py 决定，都是「精确解」，不走候选阶梯/跨片补点）：
#   O = 单排 7 脚（2026-10-27）· F = 四边每排 32 · T = 上下两排各 10
#   C = 两端件 2 · L = 两排各 3 · V = 两端件 2（2026-11-03，算法与 C 共用 pin_c.py）
_KIND_ALIAS = {                  # 用户/上游可能给出的写法 -> 规范类别
    # ⚠ "1" / "OTHER" / "单" 只用于**显示归类**（kind_of）；`pin_detect._is_o_label`
    #   故意不认它们，那几种写法仍走通用算法 —— 否则旧标签会静默变成「恒 7 点」。
    "O": "O", "OTHER": "O", "1": "O", "单": "O", "單": "O",
    "F": "F", "QFP": "F", "4": "F", "四": "F",
    "T": "T", "SOP": "T", "2": "T", "两": "T", "兩": "T",
    "C": "C",                    # 两端件（电阻/电容/LED），恒 2 点，独立算法 pin_c.py
    "L": "L",                    # 两排各 3 脚（轴随图片：上下 或 左右），恒 6 点，pin_l.py
    "V": "V",                    # 两端件（另一类贴片件），恒 2 点，算法与 C 共用 pin_c.py
}


def kind_of(label):
    """把任意 label 规整成 O / F / T（认不出来返回 None）。"""
    s = str(label or "").strip().upper()
    if not s:
        return None
    if s in _KIND_ALIAS:
        return _KIND_ALIAS[s]
    head = s.split("_")[0].split("-")[0].strip()
    if head in _KIND_ALIAS:
        return _KIND_ALIAS[head]
    # 再去掉尾部数字："F1"/"T2"/"O3" —— YOLO 老模型给的就是 "F1"，labelme 里
    # 也有人这么标。以前这里直接返回 None，导致 "F1" 既不算 F 也不算 T，
    # 在 detect_image 里被当成"未知类别"照常走 GrayBoost 接管（2026-10-26 补）。
    stem = head.rstrip("0123456789").strip()
    k = _KIND_ALIAS.get(stem)
    if k:
        return k
    # 自定义类别（2026-11-11）：注册表里的名字原样返回，让 "Y" / "Y_3" / "Y3"
    # 都归到同一个类别名。放在 alias 之后 —— 保证 "SOP"/"QFP" 这些历史写法
    # 仍然优先按 T/F 解析（它们被 pin_kinds 列为保留字，不会成为自定义名）。
    try:
        import pin_kinds
        s2 = pin_kinds.spec_of(stem or head)
        if s2:
            return s2["name"]
    except Exception:
        pass
    return None


def all_kinds():
    """内置 6 类 + 用户自定义类别（界面的类别按钮与下拉用）。

    与 `KINDS` 的区别：`KINDS` 是**固定的算法口径**（pin_detect 里六个
    `_is_*_label` 认得的那 6 个字母），不能被运行期改动；本函数是界面口径，
    只用来列举"可选类别"。自定义类别**不进 KINDS**，免得影响任何按 KINDS
    遍历的算法逻辑。
    """
    extra = []
    try:
        import pin_kinds
        extra = [k for k in pin_kinds.names() if k not in KINDS]
    except Exception:
        pass
    return tuple(KINDS) + tuple(extra)


def kind_text(name):
    """类别的一行说明（下拉/按钮副标题用）："T　双面两排 · 10 脚/排 · 共 20 点"。"""
    try:
        import pin_kinds
        s = pin_kinds.get(name)
        if s:
            return "%s　%s" % (s["name"], pin_kinds.describe(s["name"]))
    except Exception:
        pass
    return str(name)


def precise_kind(label, P=None):
    """label 是否属于「精确解」类别，返回 "C" / "F" / "T" / "L" / "O" / "V" / ""。

    C/V 恒 2 点、F 恒 32×4、T 恒 10×2、L 恒 3×2、O 恒 7 —— 点全由**先验**决定，不是靠
    「数峰 / 跨片补点 / 候选阶梯」凑出来的。所以它们必须独占三条路径：
    不进 GrayBoost、不走 `rerun_chip` 的候选阶梯、不参与跨片补点。

    ⚠ 判据用 `pin_detect._is_*_label`（与算法路由**同一把尺子**），而不是
    `kind_of`：`kind_of` 走 `_KIND_ALIAS`，会把 "SOP" / "2" / "两" 也当成 T，
    而 `pin_detect.detect` 并不会把这些写法路由到 `pin_t`（那 305 颗真值全是
    字面 "T"）。两边判据不一致就会出现「算法还在走通用路径、这里却短路了阶梯」。
    """
    if P is None:
        P = _load_pin()
    # 自定义类别（2026-11-11）：与六个内置类别一样是"精确解"——点数由
    # 「基础类 × 每排 n」先验决定，所以同样要独占路径：不进 GrayBoost、
    # 不走 rerun 的候选阶梯、不参与跨片补点。返回类别名（非空即短路）。
    try:
        import pin_kinds
        s = pin_kinds.spec_of(label)
        if s:
            return s["name"]
    except Exception:
        pass
    for fn, k in (("_is_c_label", "C"), ("_is_v_label", "V"), ("_is_f_label", "F"),
                  ("_is_t_label", "T"), ("_is_l_label", "L"),
                  ("_is_o_label", "O")):
        f = getattr(P, fn, None)
        try:
            if f is not None and f(label):
                return k
        except Exception:
            continue
    return ""


def _norm_xyxy(box):
    """把各种写法的框规整成 (x0, y0, x1, y1) 且 x0<=x1、y0<=y1。

    与 canvas._norm_box 同一套规则：labelme 两角点 [[x,y],[x,y]]、
    平铺 [x0,y0,x1,y1]，也吃 dict/numpy。
    """
    if box is None:
        return None
    try:
        seq = list(box)
    except Exception:
        return None
    if len(seq) == 0:
        return None
    first = seq[0]
    try:
        if isinstance(first, dict):
            x0, y0 = float(first.get("x", 0)), float(first.get("y", 0))
            x1, y1 = float(seq[1].get("x", 0)), float(seq[1].get("y", 0))
        elif hasattr(first, "__len__") and not isinstance(first, str):
            x0, y0 = float(first[0]), float(first[1])
            x1, y1 = float(seq[1][0]), float(seq[1][1])
        elif len(seq) >= 4:
            x0, y0, x1, y1 = (float(seq[0]), float(seq[1]),
                              float(seq[2]), float(seq[3]))
        else:
            return None
    except Exception:
        return None
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def chip_box(chips, idx):
    """取第 idx 个芯片的框，返回 (x0, y0, x1, y1)；越界返回 None。"""
    if not chips or not (0 <= int(idx) < len(chips)):
        return None
    return _norm_xyxy(chips[int(idx)].get("box"))


def set_chip_box(chips, idx, box):
    """改写第 idx 个芯片的框（自动把两点排序成左上/右下）。

    只动 `box`：**框内的引脚坐标一个都不动**（缩小框不会静默丢点，
    用户想删点有专门的删点功能）。返回新的 (x0, y0, x1, y1)，失败返回 None。
    """
    bb = _norm_xyxy(box)
    if bb is None or not chips or not (0 <= int(idx) < len(chips)):
        return None
    x0, y0, x1, y1 = bb
    chips[int(idx)]["box"] = [[round(x0, 1), round(y0, 1)],
                              [round(x1, 1), round(y1, 1)]]
    return x0, y0, x1, y1


def unique_chip_id(chips, kind, skip=None):
    """给 `kind` 类别生成一个不重复的编号 `kind_N`。

    取该类别**第一个没被占用的**编号（所以删掉 T_2 之后再改名，会补回 T_2）。
    这样编号永远是 1..n 连续段，不会因为反复增删而飙到大数。

    `skip` 是要重新命名的那颗自己的下标（统计已用编号时把它排除，
    否则会跟自己撞号）。
    """
    k = kind_of(kind) or str(kind or "").strip().upper() or "X"
    used = set()
    for i, ch in enumerate(chips or []):
        if skip is not None and i == int(skip):
            continue
        cid = str(ch.get("id") or "").strip()
        if not cid:
            continue
        head, _, tail = cid.rpartition("_")
        if head == k and tail.isdigit():
            used.add(int(tail))
    n = 1
    while n in used:
        n += 1
    return "%s_%d" % (k, n)


def set_chip_label(chips, idx, kind):
    """改第 idx 个芯片的类别，并按 `kind_N` 重新命名 —— 编号绝不与现有重复。

    返回新编号（如 'O_3'）；类别不认识 / 下标越界返回 None。
    只改这一颗的 `label` 与 `id`，**不动其它芯片的编号**（避免"改一颗、
    满板编号都变"的意外）。
    """
    k = kind_of(kind)
    if k is None or not chips or not (0 <= int(idx) < len(chips)):
        return None
    ch = chips[int(idx)]
    ch["label"] = k
    ch["id"] = unique_chip_id(chips, k, skip=int(idx))
    return ch["id"]


def _next_chip_id(chips):
    """给新芯片一个不与现有重复的 chip_id（整数）。"""
    m = -1
    for ch in chips or []:
        try:
            m = max(m, int(ch.get("chip_id", -1)))
        except Exception:
            pass
    return m + 1


def make_empty_chip(box, label, chip_id=0):
    """造一个「还没有引脚」的空芯片（结构同 pack_chip 的输出）。

    新建矩形框先用它占位 —— 屏幕上立刻能看到框，引脚交给单芯片重跑去填。
    """
    k = kind_of(label) or "T"
    bb = _norm_xyxy(box)
    if bb is None:
        return None
    return pack_chip(bb, k, int(chip_id), [],
                     {"left": 0, "bottom": 0, "right": 0, "top": 0}, {})


def add_chip(chips, box, label):
    """在结果里新增一个矩形框（空引脚）。返回 (下标, chip) 或 (None, 原因)。

    新框的编号自动取 `kind_N`（N = 该类现有最大编号 + 1），保证不重复。
    """
    if chips is None:
        return None, "还没有识别结果，请先点「引脚识别」再画框。"
    bb = _norm_xyxy(box)
    if bb is None:
        return None, "框太小或坐标无效。"
    x0, y0, x1, y1 = bb
    if (x1 - x0) < 8 or (y1 - y0) < 8:
        return None, "框太小了（宽高都要 ≥ 8 像素），请重新画。"
    ch = make_empty_chip(bb, label, chip_id=_next_chip_id(chips))
    if ch is None:
        return None, "框无效。"
    chips.append(ch)
    ch["id"] = unique_chip_id(chips, ch["label"])
    return len(chips) - 1, ch


def delete_chip(chips, idx):
    """删除第 idx 个矩形框（框内引脚一起消失）。

    返回被删掉的 chip（调用方可以用 pin_count 报"连带删了几个点"）；
    越界返回 None。**不给其它芯片重编号**（保持其余编号稳定）。
    """
    if not chips or not (0 <= int(idx) < len(chips)):
        return None
    return chips.pop(int(idx))


def chip_summary(chips):
    """给信息栏用的一句话：`T_1(F，12点)`。"""
    out = []
    for c in chips or []:
        out.append("%s(%s，%d点)" % (c.get("id") or "?", c.get("label") or "?",
                                    int(c.get("pin_count", 0) or 0)))
    return "、".join(out)


# ------------------------------------------------------- 单芯片重跑「候选阶梯」
#  为什么不再是「跑一次紧贴框、够 min_pins 就换」：
#  整板芯片的 labelme 框只有几十像素（实测 pitch≈5px），而算法里有一批**绝对像
#  素**门槛（quad_probe=26 / clahe_tile=24 / seg_min_w=20 …）是按"芯片占满画面"
#  调的 —— 小图上前置的 quad / dual / dualband 全过不了，掉到最弱的 blob / grid。
#  实测 V5 全量 282 颗：旧口径（≥3 点就换）结构可疑 **40 颗(14.2%)，比主算法的
#  32 颗(11.3%)还差**；其中 92 颗点数比主算法更少，27 颗一个点都没出（右键等于
#  没跑，界面还看不出来）。
#
#  改成阶梯：quad(四边复核) → 紧贴框 x1 → ×2 → ×3。
#    * 外扩候选（margin=0.10）**刻意不做**：整板框已贴着元件，外扩会把邻件焊盘
#      带进来（实测 64 脚 QFP 从 64 掉成 blob/3）。
#    * 只在候选**确实更好**时才接管，判据全部基于「结构不变量」，不需要真值。
#
#  viol = 结构违规度：0 最整齐；99 = 结构上不可能
#         （O 非 1 排 / T 不是一对对边或两排不等 / F 非四边齐）
#  接管条件（任一成立；都不成立则保留 pin_detect 结果，界面会明确提示）：
#    A. 结构严格更好：viol 更小，且点数不腰斩、不暴增；
#    B. 结构相同(且 <=1) 但**多找到引脚** —— 治"漏检一两个脚"；
#    C. 结构相同 但候选**换了一对对边**、点数 0.6~1.0 倍、且点更实在地落在焊盘
#       上 —— 治"主算法把点放在错误的对边上"。
#       实例 V5.(43) T_19：主算法 12 个点落在上下边（真焊盘在左右边），x3 候选
#       10 个点落在左右边且亮度 179 > 主算法 141。
LADDER_STEPS = ((1.0, "x1"), (2.0, "x2"), (3.0, "x3"))
# ⚠ 放大档的开销是**超线性**的：实测 82x62 → ×3 仅 0.5s；140x135 → ×3 约 1.5s；
#   243x186 → ×2 5.3s、×3 28s（一次右键要等半分钟）。所以按"放大后的面积/边长"
#   设上限 —— 大芯片本来像素就够，不需要放大也能跑通前置分支。
LADDER_MAX_SIDE = 640           # 放大后单边超过它就跳过该档
LADDER_MAX_AREA = 200000        # 放大后总像素超过它就跳过该档（≈450x450）
LADDER_BUDGET = 8.0             # 阶梯总时间预算（秒），超了就不再往下试
LADDER_SHRINK = 0.45            # 接管下限：点数不得低于主算法的这个比例
LADDER_GROW = 2.0               # 接管上限（结构更好时）：max(2×主, 主+6)
LADDER_GROW_ADD = 6
LADDER_B_GROW = 1.6             # 「同结构补脚」上限：max(1.6×主, 主+3)
LADDER_B_ADD = 3
LADDER_C_LO = 0.60              # 「同结构换对边」点数比例下限
LADDER_C_Q = 1.12               # 「点更实在」的亮度比例门槛
LADDER_C_Q_ABS = 12.0
LADDER_C_MAXVIOL = 1            # 规则 B 只在"基本整齐"时用
# ---- 规则 D（焊盘周期一致性）：治「主算法把相邻脚合并了」的漏检
#   整板紧贴框的 pitch 只有 5~7px，pin_detect 里一批**绝对像素**参数（clahe_tile /
#   quad_probe / detrend / seg_min_w ...）是按"芯片占满画面"(pitch 15~50px) 调的，
#   于是细间距大件会被整排地合并（V5.(1) T_10 90x61 只出 6 点 / 真值 18）。
#   阶梯里的 x1/x2/x3 本来能补回来，但"不许点数暴增"的闸门（规则 A/B 的上限）
#   把 6→18 也一起挡掉了 —— 这个闸门是为 F_1(64→16) 那类暴缩/暴增设的，
#   对"漏检型"它不是好判据。这里改用**图像自身的焊盘周期**当尺子：
#   主算法每脚跨了 ≈1.6 个周期以上 → 判为漏检 → 取"隐含周期最接近实测"的候选。
#   只在 quad/A/B/C 全都不成立时兜底，所以已定的芯片（V5.(43) T_19~T_26 等）不受影响。
PITCH_BAND = 0.28               # 焊盘带取框宽/高的这个比例
PITCH_FRAC = 0.35               # 峰判定阈值（占动态范围的比例）
PITCH_SAME = 0.35               # 一对边两条带的周期相对差上限
PITCH_CONF = 0.75               # 峰间距一致性下限（低于它不信这个周期）
PITCH_MINPEAK = 3               # 每条带至少这么多个峰
PITCH_RATIO = 1.60              # 主算法隐含周期 / 实测周期 超过它 = 漏检
PITCH_TOL = 0.35                # 候选隐含周期与实测周期的相对容差（取对数误差）
PITCH_GAIN = 0.50               # 候选误差须 < 主算法误差的这个比例，才值得改
# 关掉阶梯（回到旧行为：紧贴框 x1 一次、够 min_pins 就换）：
#   设环境变量 PINTOOL_RERUN_LADDER=0
RERUN_LADDER = os.environ.get("PINTOOL_RERUN_LADDER", "1") not in ("0", "off", "no")

_SIDE_KEYS = ("left", "bottom", "right", "top")


def _side_counts(pins):
    sc = {s: 0 for s in _SIDE_KEYS}
    for p in pins:
        if p.get("side") in sc:
            sc[p["side"]] += 1
    return sc


def _ladder_viol(label, sc):
    """结构违规度：0 最整齐，99 = 结构上不可能。与历次普查口径一致。"""
    if sc is None:
        return 99
    l, b, r, t = (int(sc.get(k, 0) or 0) for k in _SIDE_KEYS)
    lab = str(label or "").strip().upper()
    nz = [v for v in (l, b, r, t) if v]
    if lab == "O":
        return 0 if len(nz) == 1 else 99
    if lab == "T":
        if l and r:
            return abs(l - r) if not (t or b) else 99
        if t and b:
            return abs(t - b) if not (l or r) else 99
        return 99
    if lab == "F":
        if len(nz) == 4:
            return max(nz) - min(nz)
        if len(nz) == 2:                    # 四边封装退化成两排：允许，但记为 6
            a, c = (l, r) if (l and r) else ((t, b) if (t and b) else (0, 0))
            return 6 if c and abs(a - c) <= 1 else 99
        return 99
    if lab in ("C", "V"):
        # 两端件（C / V）：恰好两点、且落在同一对边上（左/右 或 上/下）
        return 0 if (len(nz) == 2 and _ladder_axis(sc)) else 99
    return 0


def _ladder_axis(sc):
    """两排落在哪一对边上：'lr'（左右）/ 'tb'（上下）/ None。"""
    if sc is None:
        return None
    l, b, r, t = (int(sc.get(k, 0) or 0) for k in _SIDE_KEYS)
    lr, tb = (l or 0) + (r or 0), (t or 0) + (b or 0)
    if lr and not tb:
        return "lr"
    if tb and not lr:
        return "tb"
    return None


def _pad_brightness(hsv, pins):
    """点在原图上的"落盘"程度：每个点 3x3 的 V 均值再平均。

    「点是不是压在真焊盘上」用亮度衡量 —— 落在本体 / 板面上的点会明显偏暗。
    """
    if hsv is None or not pins:
        return 0.0
    H, W = hsv.shape[:2]
    vs = []
    for p in pins:
        x = int(round(float(p["x"]))); y = int(round(float(p["y"])))
        x0, x1 = max(0, x - 1), min(W, x + 2)
        y0, y1 = max(0, y - 1), min(H, y + 2)
        if x1 > x0 and y1 > y0:
            vs.append(float(hsv[y0:y1, x0:x1, 2].mean()))
    return float(np.mean(vs)) if vs else 0.0


def _band_profile(band, along_y=True):
    """焊盘带的 1-D 亮度剖面：逐行（或逐列）取最亮的 60% 再平均。

    只取最亮的 60% 是为了把板面/绿色阻焊排除在统计之外。
    """
    if band is None or band.size == 0:
        return None
    v = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)[:, :, 2].astype(np.float32)
    if not along_y:
        v = v.T
    k = max(1, int(v.shape[1] * 0.40))
    return np.sort(v, axis=1)[:, k:].mean(axis=1)


def _profile_pitch(pr):
    """由亮度剖面估焊盘周期。

    返回 (pitch, 置信度, 峰数)：置信度 = 相邻峰间距落在中位数 ±28% 内的占比。
    **峰间距中位数比自相关更稳**：自相关在焊盘带只有十来个周期时容易锁到 2 倍周期。
    """
    if pr is None or len(pr) < 6:
        return None, 0.0, 0
    lo, hi = float(np.percentile(pr, 5)), float(pr.max())
    th = lo + PITCH_FRAC * (hi - lo)
    n = len(pr)
    raw = []
    for i in range(n):
        a = pr[i - 1] if i > 0 else -1e18
        b = pr[i + 1] if i < n - 1 else -1e18
        if pr[i] > th and pr[i] >= a and pr[i] >= b:
            raw.append(i)
    pk = []
    for i in raw:                       # 相邻 <2px 视为同一个峰
        if pk and i - pk[-1] < 2:
            if pr[i] > pr[pk[-1]]:
                pk[-1] = i
        else:
            pk.append(i)
    if len(pk) < PITCH_MINPEAK:
        return None, 0.0, len(pk)
    d = np.diff(pk).astype(np.float64)
    med = float(np.median(d))
    if med <= 1.0:
        return None, 0.0, len(pk)
    conf = float(np.count_nonzero(np.abs(d - med) <= 0.28 * med)) / float(len(d))
    return med, conf, len(pk)


def _row_pitch(img, box, label):
    """估这颗芯片的"引脚间距"(px) —— 纯图像量，不看任何检测结果。

    对「左右」和「上下」两对焊盘带各测一次周期，取更可信且两带一致的那对。
    返回 (pitch, 行长, 置信度, 最小峰数)；测不出返回 None。
    """
    try:
        x0, y0, x1, y1 = [int(round(float(v))) for v in box[:4]]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(img.shape[1], x1), min(img.shape[0], y1)
        if x1 - x0 < 12 or y1 - y0 < 12:
            return None
        W, H = x1 - x0, y1 - y0
        wx = max(6, int(W * PITCH_BAND))
        wy = max(6, int(H * PITCH_BAND))
        pairs = (
            (H, (img[y0:y1, x0:x0 + wx], True), (img[y0:y1, x1 - wx:x1], True)),
            (W, (img[y0:y0 + wy, x0:x1], False), (img[y1 - wy:y1, x0:x1], False)),
        )
        best = None
        for L, b1, b2 in pairs:
            r1 = _profile_pitch(_band_profile(b1[0], b1[1]))
            r2 = _profile_pitch(_band_profile(b2[0], b2[1]))
            if r1[0] is None or r2[0] is None:
                continue
            if abs(r1[0] - r2[0]) > PITCH_SAME * max(r1[0], r2[0]):
                continue                # 两条带周期对不上 → 这对边不是焊盘带
            conf = min(r1[1], r2[1])
            if best is None or conf > best[2]:
                best = ((r1[0] + r2[0]) / 2.0, float(L), conf, min(r1[2], r2[2]))
        if best is None or best[2] < PITCH_CONF or best[3] < PITCH_MINPEAK:
            return None
        return best
    except Exception:
        return None


def _ladder_quad_wins(label, main, c):
    """quad 候选是否会「直接胜出」—— 判据与 _ladder_pick 的 quads 分支逐字相同。

    ⚠ 提速的关键（2026-10-13）：`_gb_quad` 只要 0.01~0.03s，而它一旦合格，
    `_ladder_pick` 一定返回它（quads 分支排在 A/B/C/D 之前）——后面的放大档
    根本用不到。实测 V5.(23) F_1：quad 已给 64 点（正确），旧代码还要白跑
    x2(3.7s)+x3(13.2s)，一次右键等 17.1s。短路后约 0.7s，**候选集都不变**。
    """
    if c is None or c["n"] < int(GB_POLICY["min_pins"]):
        return False
    vc = _ladder_viol(label, c["sc"])
    if vc >= 99 or vc > _ladder_viol(label, main["sc"]):
        return False
    return main["n"] < 3 or c["n"] >= LADDER_SHRINK * main["n"]


def _ladder_pick(label, main, cands, pitchinfo=None):
    """从阶梯候选里挑一个接管对象；挑不出来返回 (None, 原因)。

    main / cands 每项：dict(tag, n, sc, q, pins, ...)
    pitchinfo —— _row_pitch() 的结果 (pitch, 行长, 置信度, 峰数) 或 None，
                 只有规则 A/B/C 全不成立时才用来判"是不是漏检"。
    """
    vm, nm, qm = _ladder_viol(label, main["sc"]), main["n"], main.get("q", 0.0)
    valid = [c for c in cands
             if c["n"] >= int(GB_POLICY["min_pins"])
             and _ladder_viol(label, c["sc"]) < 99]
    if not valid:
        return None, "新算法没给出结构成立的候选"

    # ---- 四边复核是"专家通道"：只要不比自己差就信它
    quads = [c for c in valid if c["tag"] == "quad"
             and _ladder_viol(label, c["sc"]) <= vm
             and (nm < 3 or c["n"] >= LADDER_SHRINK * nm)]
    if quads:
        return max(quads, key=lambda c: c.get("q", 0.0)), "quad 四边复核"

    vmin = min(_ladder_viol(label, c["sc"]) for c in valid)
    same = [c for c in valid if _ladder_viol(label, c["sc"]) == vmin]
    top = max(same, key=lambda c: c["n"])          # 同结构里取点多的一方
    vc, nc, qc = vmin, top["n"], top.get("q", 0.0)
    axm, axc = _ladder_axis(main["sc"]), _ladder_axis(top["sc"])

    # ---- A. 结构严格更好
    if vm >= 3:
        # 主算法结构已明显崩坏：候选只要"基本说得通"且不腰斩就接管
        if vc < vm and vc <= 6 and nc >= LADDER_SHRINK * nm:
            return top, "%s（主算法结构崩坏，%s 结构成立）" % (top["tag"], top["tag"])
        return None, "主算法结构崩坏但候选也没救回来"
    if vc < vm:
        if LADDER_SHRINK * nm <= nc <= max(LADDER_GROW * nm, nm + LADDER_GROW_ADD):
            return top, "%s（结构更整齐 %d→%d）" % (top["tag"], vm, vc)
        return None, "%s 结构更整齐但点数异常（%d→%d）" % (top["tag"], nm, nc)

    # ---- B. 结构相同、多找到引脚
    if vc == vm and vc <= LADDER_C_MAXVIOL:
        if nm < nc <= max(LADDER_B_GROW * nm, nm + LADDER_B_ADD):
            return top, "%s（同结构多找到 %d 个脚）" % (top["tag"], nc - nm)

    # ---- C. 结构相同、换了一对对边且点更实在
    if vc == vm and axc and axm and axc != axm:
        if (LADDER_C_LO * nm <= nc <= nm
                and qc >= max(LADDER_C_Q * qm, qm + LADDER_C_Q_ABS)):
            return top, "%s（换成%s那对边，落盘更实 %.0f→%.0f）" % (
                top["tag"], "左右" if axc == "lr" else "上下", qm, qc)

    # ---- D. 焊盘周期一致性：主算法把相邻脚合并了就推翻它
    #   只在 A/B/C 全不成立时兜底（已定的芯片不受影响）。
    keep_why = "候选都不优于主算法"
    if pitchinfo is None:
        return None, keep_why
    pv, row_len, _conf, _npk = pitchinfo
    lab_u = str(label or "").strip().upper()
    div = 2 if lab_u in ("T", "O") else 4        # T/O 一/两排，F 四排
    if pv <= 1.0 or nm < div:
        return None, keep_why
    if lab_u == "T" and vm > 1:
        return None, keep_why
    r_main = (row_len / (nm / float(div))) / pv  # 主算法隐含周期 / 实测周期
    if 1.0 / PITCH_RATIO < r_main < PITCH_RATIO:
        return None, keep_why                    # 主算法没明显漏检，别动它
    err_main = abs(math.log(r_main))
    best = None
    for c in valid:
        if c["n"] < div or _ladder_viol(label, c["sc"]) > min(vm, 1):
            continue
        r = (row_len / (c["n"] / float(div))) / pv
        e = abs(math.log(r))
        if e > PITCH_TOL:
            continue                             # 候选自己也不合周期
        key = (e, _ladder_viol(label, c["sc"]), -c["n"])
        if best is None or key < best[0]:
            best = (key, c, e)
    if best is None:
        return None, keep_why
    e_best, c_best = best[2], best[1]
    if e_best >= PITCH_GAIN * err_main:
        return None, keep_why                    # 改善不明显，不动
    return c_best, ("%s（焊盘周期纠正漏检：主算法每脚跨 %.1f 个周期，"
                    "%d 点间距与实测 %.1fpx 周期相符）"
                    % (c_best["tag"], r_main, c_best["n"], pv))


def rerun_chip(img, box, label, opt=None):
    """**单颗芯片重跑**：主算法 + GrayBoost 候选阶梯，谁好谁留。

    与整板识别不同，这里不走 GB_POLICY 的条件判断 —— 用户既然手点右键重跑，
    意图就是「这颗用新算法再算一遍」。但"再算一遍"不等于"无条件换成新的"：
    整板小裁图（pitch≈5px）上旧口径会拿更差的结果覆盖主算法（实测 92/282 颗
    点数变少、27 颗一个点都没出），所以流程是：

      1. 主算法 pin_detect 先跑一遍（作为基准 + 兜底）；
      2. 候选阶梯：quad 四边复核 → 紧贴框(margin=0) x1 → ×2 → ×3；
         **提速（2026-10-13）**：quad 一旦合格就直接短路——它在 `_ladder_pick`
         里排第一，胜出后放大档必被忽略，所以不做的候选一个也不影响结果
         （判据见 `_ladder_quad_wins`，与 `_ladder_pick` 的 quads 分支逐字相同）。
         ⚠ 曾试过「按 pitch 只跑一档」，会改坏 11 颗（如 V5.(23) T_33 10→8），
         已完全回退，**不做剪枝**。
      3. 只有候选在**结构不变量**上确实更好（见 _ladder_pick）才接管，
         否则保留主算法 —— detail['reason'] 会说明为什么保留；
      4. 兜底（规则 D）：若 A/B/C 全不成立，但由**图像自身的焊盘周期**判定
         主算法把相邻脚合并了（每脚跨 ≥1.6 个周期），则改用"隐含周期最接近
         实测"的候选 —— 专治细间距大件的整排漏检（V5.(1)/V5.(16) T_10）。

    返回 (chip, detail)：
      chip   —— 与 detect_image 输出同构的 chip dict
      detail —— dict(used, reason, n_main, n_new, cands, pitch, secs)
                used ∈ 'grayboost' / 'pin_detect'
    """
    P = _load_pin()
    t0 = time.time()
    prec = precise_kind(label, P)
    if prec:
        # C 类（两端件）恒 2 点，没有「候选阶梯」可言：quad 四边复核与 ×2/×3 放大档
        # 都是为多脚封装准备的，对 2 点件只会给出错误候选（或白跑几秒）。
        # F 类同理，而且更彻底：它的 32×4 是精确解，阶梯里的 quad / dual 候选
        # 都是"点数未知、靠结构补全"的思路，接管只会把 128 点改坏。直接返回。
        # T 类（2026-10-26 接入）同样：上下两排各 10 点是精确解，而阶梯实测会跑
        # quad + ×2/×3 三档（~1 s）。**这是 T 接回时唯一必须短路的地方** ——
        # 整板路径 GrayBoost 本来就没抢过 T（实测全 method=pin_detect）。
        # L 类（2026-10-26）/ O 类（2026-10-27）同理：3×2 与单排 7 都是精确解，
        # 阶梯的 quad/dual 只为"点数未知、靠结构补全"的多脚封装服务。
        try:
            pins, counts, info = P.detect(img, box, label, opt=opt)
        except Exception as exc:
            pins, counts, info = [], {}, {"failed": True, "reason": str(exc)}
        chip = pack_chip(box, label, 0, pins, counts, info)
        _tag = "%s-class (no ladder)" % prec
        detail = dict(used="pin_detect", reason=_tag,
                      n_main=len(pins), n_new=chip["pin_count"],
                      n_cands=0, cands=[], pitch=None,
                      secs=round(time.time() - t0, 2), exact=prec)
        try:
            _gb_log(dict(t=time.strftime("%Y-%m-%d %H:%M:%S"), ev="rerun",
                         pid=os.getpid(), label=label,
                         img=[int(img.shape[1]), int(img.shape[0])],
                         box=[round(float(v), 1) for v in box[:4]],
                         n_main=len(pins), used="pin_detect", reason=_tag,
                         final_pins=chip["pin_count"], pitch=None, cands=[],
                         secs=detail["secs"]))
        except Exception:
            pass
        return chip, detail
    n_main = 0
    pins, counts, info = [], {}, {}
    try:
        pins, counts, info = P.detect(img, box, label, opt=opt)
        n_main = len(pins)
    except Exception:
        pins, counts, info = [], {}, {"failed": True}

    try:
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    except Exception:
        hsv = None
    main = dict(tag="main", n=n_main, sc=_side_counts(pins),
                q=_pad_brightness(hsv, pins), pins=pins)

    # ---- 候选一：quad 四边复核（最便宜，0.01~0.03s）
    cands = []
    r = _gb_quad(img, box)
    qcand = None
    if r is not None and r[0]:
        qcand = dict(tag="quad", n=len(r[0]), sc=_side_counts(r[0]),
                     q=_pad_brightness(hsv, r[0]), pins=r[0], res=r)
        cands.append(qcand)

    # ---- 焊盘周期（≈0 耗时）：既给规则 D 当尺子，也用来决定该放大到几倍
    pinfo = _row_pitch(img, box, label)

    bw = abs(float(box[2]) - float(box[0]))
    bh = abs(float(box[3]) - float(box[1]))
    t_ladder = time.time()

    def _try_scale(scale, tag):
        """跑一档放大；超预算 / 超尺寸上限就跳过。"""
        if time.time() - t_ladder > LADDER_BUDGET:
            return
        sw, sh = bw * scale, bh * scale
        if max(sw, sh) > LADDER_MAX_SIDE or sw * sh > LADDER_MAX_AREA:
            return                      # 大芯片像素已够，放大只会更慢
        try:
            rr = _GB.detect_scaled(img, box, scale=float(scale), margin=0.0)
        except Exception:
            rr = None
        if rr is None or not rr[0]:
            return
        cands.append(dict(tag=tag, n=len(rr[0]), sc=_side_counts(rr[0]),
                          q=_pad_brightness(hsv, rr[0]), pins=rr[0], res=rr))

    steps = list(LADDER_STEPS if RERUN_LADDER else LADDER_STEPS[:1])
    # quad 已胜出 → 放大档永远用不到，直接不做（省掉最贵的几秒；候选集不变）
    if not _ladder_quad_wins(label, main, qcand):
        for sc, tag in steps:
            _try_scale(sc, tag)

    pick, why = _ladder_pick(label, main, cands, pitchinfo=pinfo)
    dbg = [dict(tag=c["tag"], n=c["n"], viol=_ladder_viol(label, c["sc"]),
                q=round(c.get("q", 0.0), 1), sc=dict(c["sc"])) for c in cands]
    dbg_pitch = None if pinfo is None else dict(
        pitch=round(pinfo[0], 2), row=round(pinfo[1], 1),
        conf=round(pinfo[2], 2), peaks=int(pinfo[3]))

    if pick is not None:
        r = pick["res"]
        pins, counts = r[0], r[1]
        info = dict(r[2] or {})
        info["gb_reason"] = pick["tag"]
        info["gb_cands"] = dbg
        info["gb_pitch"] = dbg_pitch
        used, reason = "grayboost", why
    else:
        info = dict(info or {})
        info["gb_reason"] = "keep-main"
        info["gb_cands"] = dbg
        info["gb_pitch"] = dbg_pitch
        used, reason = "pin_detect", why

    chip = pack_chip(box, label, 0, pins, counts, info,
                     gb_tag=("GB" if used == "grayboost" else ""))
    detail = dict(used=used, reason=reason, n_main=n_main,
                  n_new=chip["pin_count"],
                  n_cands=len(cands), cands=dbg, pitch=dbg_pitch,
                  secs=round(time.time() - t0, 2))
    # 决策日志：用户问「这次重跑为什么是这个结果」时直接翻 gb_debug.log
    try:
        _gb_log(dict(t=time.strftime("%Y-%m-%d %H:%M:%S"), ev="rerun",
                     pid=os.getpid(), label=label,
                     img=[int(img.shape[1]), int(img.shape[0])],
                     box=[round(float(v), 1) for v in box[:4]],
                     n_main=n_main, used=used, reason=reason,
                     final_pins=chip["pin_count"], pitch=dbg_pitch, cands=dbg,
                     secs=detail["secs"]))
    except Exception:
        pass
    return chip, detail


def detect_image(img, shapes, opt=None):
    """对一张 BGR 图 + shapes 做引脚识别。
    opt —— 增强开关 dict（geo_classify/symmetrize/interp_gaps/dense_pitch/
           contrast_boost/cross_chip），为 None 时用 pin_detect 默认（全部开启）。
    返回 chips: [{'chip_id','label','box','pin_count','side_counts','pins':[...]}]"""
    P = _load_pin()
    if not shapes:
        return []
    # pin_detect 的 V/C 与增强缓存用「数组内存地址 + shape + strides」做键。上一张
    # 图（或 GrayBoost 申请/释放的一堆临时数组）释放后，新数组可能复用同一地址
    # 且 shape 相同 -> 命中过期结果。每张图开始前清一次，结果就不再依赖「之前跑
    # 过什么」，同一批里每张图的输出稳定可复现（图内缓存仍然生效，不影响速度）。
    for _c, _o in (("_VC_CACHE", "_VC_ORDER"), ("_ENH_CACHE", "_ENH_ORDER")):
        try:
            getattr(P, _c).clear()
            getattr(P, _o).clear()
        except Exception:
            pass
    boxes = [dict(label=s["label"], box=s["box"]) for s in shapes]
    try:
        # 整板识别：逐芯片识别 + 同款芯片跨片对比补点（问题3，仅限本张图）
        results = P.detect_board(img, boxes, opt=opt)
    except (TypeError, AttributeError):
        results = [P.detect(img, s["box"], s["label"], opt=opt) for s in shapes]
    chips = []
    for i, s in enumerate(shapes):
        box = s["box"]
        pins, counts, info = results[i]
        # ---- GrayBoost 兜底：仅当判定为「非整板输入 / 主算法失效」时替换
        #  C 类不走这条路：GrayBoost 的 quad / dual / dualband 都是为多脚封装写的，
        #  对「只有 2 个引脚的贴片件」没有对应的结构模型，让它接管只会虚增点数。
        #  F 类也不走：它每排 32 点是**已知常量**，由 pin_f 直接解出；GrayBoost
        #  没有 32 先验，而 ``closeup`` 判据（框占满整图 ≥35%）在单颗特写上必然
        #  触发 —— 实测同一批 7 张图它接管了 6 张，给出 8/12/85/90/91 点。
        #  T 类同理：上下两排各 10 点是**已知常量**，由 pin_t 直接解出；quad/dual
        #  没有 10 先验，接管只会把 20 点改成别的数（本轮实测主算法全赢，短路只为
        #  保证「GrayBoost 升级判据 someday 改宽了也不会抢 T」）。
        #  L 类（3×2）/ O 类（单排 7 脚）同理：点数由先验决定，GrayBoost 的
        #  quad/dual 都是"点数未知、靠结构补全"，接管只会把精确解改坏。
        kind = kind_of(s.get("label"))
        prec = precise_kind(s.get("label"), P)
        if prec:
            gb_tag = ""
        else:
            pins, counts, info, gb_tag = _maybe_grayboost(img, box, pins, counts,
                                                          info, opt, len(shapes))
        chip = pack_chip(box, s["label"], i, pins, counts, info, gb_tag=gb_tag)
        chips.append(chip)
        pins_json = chip["pins"]         # 供下面的决策日志用
        # ---- 决策日志（每块芯片一行）
        #  gb_tag='' 时额外记一次「为什么没触发」，这是排查"还是老算法"的关键。
        try:
            why = info.get("gb_reason")
            if not gb_tag:
                if prec:
                    why = "n/a (%s-class)" % prec
                else:
                    _u, _w, _wh = _gb_decision(img, box, pins, info, opt, len(shapes))
                    why = _w or "not-needed"
            _gb_log(dict(t=time.strftime("%Y-%m-%d %H:%M:%S"), ev="detect",
                         pid=os.getpid(), chip=i, n_chips=len(shapes),
                         label=s.get("label"),
                         img=[int(img.shape[1]), int(img.shape[0])],
                         box=[round(float(v), 1) for v in box[:4]],
                         cover=round(_GB.coverage(box, img.shape), 3),
                         main_pins=int(info.get("gb_main_pins", len(pins_json))),
                         gb_tag=gb_tag, gb_reason=why,
                         gb_mode=info.get("gb_mode"),
                         gb_note=str(info.get("note"))[:60],
                         final_pins=len(pins_json),
                         method=chips[-1]["method"]))
        except Exception:
            pass
    # 芯片编号（T_1 / F_2 / O_3…）：与识别结果无关，纯编号，供 UI 显示 + JSON 存档。
    assign_chip_ids(chips)
    return chips


def shapes_from_yolo(boxes):
    """把 YOLO 检测结果（[{'label','conf','box'}]）转成引脚识别的 shapes。

    YOLO 类别 O/T/F 与 pin_detect 的标签别名一致（O→1排 / T→2排 / F→4排），
    直接透传 label 即可，由 pin_detect 内部做映射。
    """
    shapes = []
    for b in boxes:
        x0, y0, x1, y1 = [float(v) for v in b["box"]]
        shapes.append(dict(label=str(b["label"]),
                           box=[min(x0, x1), min(y0, y1),
                                max(x0, x1), max(y0, y1)],
                           shape_type="rectangle",
                           conf=float(b.get("conf", 1.0))))
    return shapes


def detect_from_yolo(img, boxes, opt=None):
    """对 YOLO 检出的每个芯片框做引脚识别。返回 (chips, shapes)。"""
    shapes = shapes_from_yolo(boxes)
    return detect_image(img, shapes, opt=opt), shapes


# 标注默认参数（可被 UI 覆盖）
DOT_SIZE = 2        # 引脚红点半径（px）
NUM_SIZE = 2        # 引脚编号数字大小档位（1..12）
ID_SIZE = 3         # 芯片编号（T_1/F_2…）大小档位（0 = 不画）

# 数字字号档位 -> cv2 的 fontScale 映射（0.45 为历史默认，对应档位 2）
_NUM_SCALE = {1: 0.30, 2: 0.45, 3: 0.60, 4: 0.75, 5: 0.90, 6: 1.05,
              7: 1.20, 8: 1.40, 9: 1.60, 10: 1.80, 11: 2.00, 12: 2.25}


def _num_scale(size):
    """数字字号档位 -> fontScale。"""
    try:
        k = int(size)
    except Exception:
        k = NUM_SIZE
    return _NUM_SCALE.get(k, 0.45)


def _cls_size(size, lo, hi, dflt):
    try:
        k = int(size)
    except Exception:
        k = dflt
    return max(lo, min(hi, k))


def _label_slots(pins, box):
    """每个引脚编号文字摆在哪一侧 —— 同排相邻两点**交错**，避免数字互相压字。

    规则（2026-10-26 用户指定，**所有类别统一**）：
      · 横向排（top / bottom）：排内第 1 个在**点上**、第 2 个在**点下**，交替；
      · 纵向排（left / right）：排内第 1 个在**点右**、第 2 个在**点左**，交替。

    为什么按「排内几何序号」而不是 idx 奇偶：F 的顶排编号是右起（整体倒序），
    按 idx 奇偶会让相位跟着编号方向翻转；而「相邻两点不同侧」只跟点的位置有关。
    所以这里每条边各自按沿边坐标排序取 rank，与编号方向无关。

    返回 [(orient, parity), ...]：
      orient = 'h' 上下摆放 / 'v' 左右摆放；parity = 0/1 表示哪一侧
      （h：0=点上 1=点下；v：0=点右 1=点左）。
    """
    n = len(pins)
    out = [("h", 0)] * n
    groups = {}
    for i, p in enumerate(pins):
        s = str(p.get("side") or "")
        if s not in ("top", "bottom", "left", "right"):
            try:
                s = _nearest_side(float(p.get("x", 0)), float(p.get("y", 0)), box)
            except Exception:
                s = "top"
        groups.setdefault(s, []).append(i)
    for s, ids in groups.items():
        horiz = s in ("top", "bottom")
        key = "x" if horiz else "y"
        ids.sort(key=lambda i: float(pins[i].get(key, 0) or 0))
        for rank, i in enumerate(ids):
            out[i] = ("h" if horiz else "v", rank & 1)
    return out


def render_annotated(img, chips, dot_size=DOT_SIZE, num_size=NUM_SIZE,
                     id_size=0):
    """在图上画：绿框(芯片)、芯片编号、红点(引脚)、黄色数字(编号：F 顶排右起逆时针，
    其余顶排左起顺时针)。

    dot_size —— 引脚红点半径（px），默认 2。
    num_size —— 引脚编号数字大小（档位 1..12），默认 2。
    id_size  —— 芯片编号（如 T_1 / F_2）大小档位（0..12），**0 = 不画**。
                默认 0：交互画布上编号是用矢量浮层画的（缩放不糊），
                只有「保存标注图」时才把它烙进像素里，避免重复出现。

    编号摆位（2026-10-26）：**同排相邻两点交错** —— 横向排一个在点上、邻点在点下；
    纵向排一个在点右、邻点在点左。见 `_label_slots`。放不下时自动翻到另一侧
    （芯片贴图像边缘时不会把数字画到画外）。

    配色（2026-10-13 起）：
      · 算法识别点（含智能预测点）—— **红色**；
      · 人工补点（manual=True）    —— **蓝色**，编号取 `name`（b1 / b2 / …）。
    这样「红点 / 蓝点」与召回率口径（红 / (红+蓝)）在画面上与数字上是一致的一回事。
    """
    ann = img.copy()
    r = _cls_size(dot_size, 1, 40, DOT_SIZE)
    scale = _num_scale(num_size)
    thick = 1 if scale < 0.9 else 2
    idd = _cls_size(id_size, 0, 12, 0)
    isc = _num_scale(idd) if idd else 0.0
    ith = 1 if isc < 0.9 else 2
    for ch in chips:
        bb = _box_xyxy(ch.get("box"))
        if bb is None:
            continue
        x0, y0, x1, y1 = [int(round(v)) for v in bb]
        cv2.rectangle(ann, (x0, y0), (x1, y1), (0, 200, 0), 1)
        if idd and ch.get("id"):
            txt = str(ch["id"])
            (tw, th), _b = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, isc, ith)
            ty = y0 - 4
            if ty - th - 4 < 0:            # 太靠上边：改画到框内侧
                ty = y0 + th + 8
            cv2.rectangle(ann, (x0, ty - th - 4), (x0 + tw + 7, ty + 3),
                          (38, 38, 38), -1)
            cv2.putText(ann, txt, (x0 + 3, ty - 1),
                        cv2.FONT_HERSHEY_SIMPLEX, isc, (0, 230, 255), ith)
        slots = _label_slots(ch["pins"], ch.get("box"))
        H, W = ann.shape[:2]
        for k, p in enumerate(ch["pins"]):
            px, py = int(round(p["x"])), int(round(p["y"]))
            if p.get("manual"):
                col = (255, 96, 0)          # 人工补点：蓝
                txt = str(p.get("name") or "b")
            else:
                col = (0, 0, 255)           # 算法识别点 / 智能预测点：红
                txt = str(p.get("idx"))
            cv2.circle(ann, (px, py), r, col, -1)
            (tw, th), bl = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX,
                                           scale, thick)
            gap = max(2, r + 1)
            orient, par = slots[k]
            if orient == "h":               # 横向排：点上 / 点下 交替
                x = px - tw // 2
                if par == 0:
                    y = py - gap - bl       # 点在下方，基线抬到点之上
                    if y - th < 1:          # 顶上放不下 → 换到点下
                        y = py + gap + th
                else:
                    y = py + gap + th       # 文字顶边落在点下方
                    if y > H - 2:           # 底下放不下 → 换到点上
                        y = py - gap - bl
            else:                           # 纵向排：点右 / 点左 交替
                y = py + th // 2
                if par == 0:
                    x = px + gap
                    if x + tw > W - 2:      # 右边放不下 → 换到点左
                        x = px - gap - tw
                else:
                    x = px - gap - tw
                    if x < 2:               # 左边放不下 → 换到点右
                        x = px + gap
            x = max(1, min(int(x), max(1, W - tw - 2)))
            y = max(th + 1, min(int(y), H - 2))
            cv2.putText(ann, txt, (int(x), int(y)),
                        cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 255, 255), thick)
    return ann


def run_pins(img_path, shape_json_path, out_json=None, out_img=None,
             dot_size=DOT_SIZE, num_size=NUM_SIZE, opt=None):
    """单图完整流程。返回 (chips, annotated_img)。

    ⚠ 读写都走「二进制 + imdecode/imencode」：`cv2.imread/imwrite` 在 Windows 上
    对**中文路径**直接返回 None / 静默失败（用户图名常见「图片1.png」这类）。
    """
    img = cv2.imdecode(np.fromfile(img_path, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise IOError("读不到图片: " + img_path)
    shapes, _ = load_labelme(shape_json_path)
    chips = detect_image(img, shapes, opt=opt)
    ann = render_annotated(img, chips, dot_size=dot_size, num_size=num_size)
    if out_json:
        json.dump({"image": os.path.basename(img_path), "chips": chips},
                  open(out_json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    if out_img:
        ext = os.path.splitext(out_img)[1] or ".png"
        cv2.imencode(ext, ann)[1].tofile(out_img)
    return chips, ann


def list_images(folder):
    """列出文件夹内支持的图片（统一走 core.common）。"""
    from .common import list_images as _li
    return _li(folder)


def find_image_for_json(json_path, img_dirs=None):
    """给一个 labelme json，找同名图片。img_dirs 为空则优先同目录。"""
    stem = os.path.splitext(json_path)[0]
    cands = []
    if img_dirs:
        cands += [os.path.join(d, os.path.basename(stem) + e)
                  for d in img_dirs for e in SUPPORTED_EXT]
    cands += [stem + e for e in SUPPORTED_EXT]
    for c in cands:
        if os.path.exists(c):
            return c
    return None
