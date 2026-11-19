# -*- coding: utf-8 -*-
"""检索：四层叠着来，少一层现场就会答非所问。

* **词项倒排**：中文按二元组（"传感器" → 传感 / 感器）、英文数字按整词。
  型号 `MV-CS200-10GM`、参数名 `曝光时间` 靠它精确命中。
* **整句子串**：把查询原样在片段里找一遍，命中就大幅加分。
* **口语改写**：现场问「读取不到 T_2」，手册里写的是「图里没有这个 label →
  记进"缺席"清单」。字面一个字都不重合，二元组永远碰不上。`_SYN` 把这层
  翻译补上 —— 扩展词只负责**召回**，不参与门槛判定。
* **问句意图**：问「为什么」要的是排查段，不是「假设 T_1 有 12 脚」那种举例。
  `_INTENT` 按问法给对应段落加权。

三条硬规矩，改之前先想清楚：

1. **编号是强信号**。`T_2` 命中一个就该出结果，不能因为同句里别的词没出现
   就判空 —— 现场九成问句都是"某个编号怎么了"。
2. **`df=0` 的词不进分母**。查询端切完停用词会残留「到」这类单字，索引端
   二元组根本不产它（"读取不到"只产出 读取/取不/不到）。让它在分母里，就是
   白扣一分，长问句必然被判死。
3. **该空就空**。扩展词只加分、原词才定生死。原词一个没沾上又没编号，
   宁可不给，也不硬凑一条错的让现场照着操作。

`query(..., trace=d)` 会把每一步都填进 d：切出哪些词、哪些被扔掉、每个词
在库里有多少块、门槛要求几条、最终为什么是空。答不上来时先看这个。
"""

import math
import re
import time

CJK = re.compile(u"[\u4e00-\u9fff]+")
WORD = re.compile(r"[a-z0-9][a-z0-9._\-/]*")

# 问句里没有信息量的词。不去掉的话，"怎么""什么" 会把所有段落都命中。
STOP = set(u"""的 了 是 在 和 与 及 或 就 都 也 还 又 而 但 如果 怎么 怎样 如何 什么
哪些 哪个 多少 为什么 可以 能否 是否 需要 一个 这个 那个 我们 你们 他们 它 我 你
请问 一下 关于 对于 以及 还有 时候 应该 要 能 会 有 没 不 太 很 更 最""".split())

# 长的排前面，保证「为什么」先于「为」被切掉
_STOP_RE = re.compile("|".join(sorted([re.escape(w) for w in STOP], key=len, reverse=True)))

# 点位编号：T_2 / T2 / O1 / L-3。这类词出现在问句里通常就是全部信息。
_ID = re.compile(r"^[a-z]{1,3}[\-_]?\d{1,3}$")

# ---------------------------------------------------------------- 口语 -> 手册用语
# 只收**真的会撞上**的，不收通用近义词。左边是现场说法（空格分隔的子串），
# 右边是手册里的说法（会被切成词项）。加一条之前先问：手册里真是这么写的吗？
_SYN = (
    (u"读取不到 读不到 读不出 没读到 读不进来 找不到 抓不到 检不到 认不出 "
     u"没认出来 没识别 识别不出 识别不到 漏了 漏掉 不见了 没有反应",
     u"缺席 图里没有 未识别 漏检 警告 点名 不算错 label"),
    (u"对不上 不匹配 不一样 不对应 串了",
     u"点名 警告 缺席 对不上"),
    (u"不生效 改了没用 不起作用 没起作用",
     u"重算 重新 生效 缓存"),
    (u"报错 出错 失败 异常 卡住",
     u"警告 错误 中止 排查"),
    (u"顺序 排序 先测哪个 走位",
     u"S 型 几何 行走 分带 交替"),
    (u"生成 算出来 怎么算 多少行 几行",
     u"展开 点位表 行数 生成"),
    (u"怎么配 怎么写 规则 怎么写规则",
     u"规则 填 写法 必填"),
)

# ---------------------------------------------------------------- 问句意图
# (名字, 触发问法的子串, 该加权的词)
_INTENT = (
    ("why", u"为什么 为何 怎么会 咋回事 怎么回事 原因",
     u"原因 常见问题 日志 警告 缺席 报错 错误 排查 含义 不算错 为什么"),
    ("how", u"怎么 如何 怎样 步骤 流程 顺序",
     u"步骤 流程 顺序 固定五步 先 再 然后 做法 填"),
    ("what", u"是什么 什么是 什么叫 定义 含义 区别",
     u""),
    ("where", u"在哪 哪里 哪一节 哪一页 哪里改",
     u""),
)

W_EXT = 0.42          # 扩展词（同义改写）的权重：只用来召回，压不过原词
W_INTENT = 0.40       # 意图关键词每命中一个，加 0.40 * idf


def _uniq(seq):
    seen, out = set(), []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _alias(w):
    """T_2 -> t2。索引端和查询端都走这里，换个写法才认得出是同一颗。"""
    if _ID.match(w):
        a = re.sub(r"[\-_]", "", w)
        if a != w:
            return a
    return ""


def is_entity(t):
    """强信号词：点位编号、型号、带数字的长串。

    这类东西命中了就不该被"命中词数不够"判死 —— 它是确定性的，不是概率的。
    """
    if not t:
        return False
    if _ID.match(t):
        return True
    if len(t) >= 5 and "-" in t and re.search(r"\d", t):
        return True                                   # mv-cs200-10gm
    if len(t) >= 4 and re.search(r"\d", t):
        return True                                   # cs200 / 5472
    return False


def tokenize(text, cut_stop=False):
    """中文二元组 + 英文整词。

    cut_stop=True 用于**查询**：先把「什么 / 怎么 / 是 / 的」这类词从串里切掉，
    再对剩下的片段做二元组。不切的话「任务号是什么」会拆出「号是」「是什」——
    跨词边界造出来的二元组能把任何含「是什么」的标题顶上来，噪声很大。
    索引端保留全量二元组（召回更全），查询端只取干净的（精度更高）。

    编号会额外产出一个去分隔符的别名（`t_2` → `t2`），两端一致才换写法也认。
    """
    s = (text or "").lower()
    if cut_stop:
        s = _STOP_RE.sub("\u0001", s)
    out = []
    for m in CJK.finditer(s):
        word = m.group(0)
        if len(word) == 1:
            out.append(word)
            continue
        for i in range(len(word) - 1):
            bg = word[i:i + 2]
            if bg not in STOP:
                out.append(bg)
    for m in WORD.finditer(s):
        w = m.group(0).strip(".-/")
        if len(w) >= 1 and w not in STOP:
            out.append(w)
            a = _alias(w)
            if a:
                out.append(a)
    return _uniq(out)


def expand(q):
    """问题里的口语 -> 手册里的说法。返回扩展词（未过滤 df）。"""
    low = (q or "").lower()
    out = []
    for trig, ext in _SYN:
        if any(t in low for t in trig.split()):
            out.extend(tokenize(ext))
    return _uniq(out)


def intent(q):
    """问法 -> (意图名, 该加权的词)。认不出就是 normal。"""
    low = (q or "").lower()
    for name, trig, words in _INTENT:
        if any(t in low for t in trig.split()):
            return name, [w for w in tokenize(words) if w]
    return "normal", []


class Result(object):
    """一条命中。

    `terms` 是**原词**（问句里的词，块里真出现的）：只拿它高亮、显示"命中的词"。
    `extra` 是同义改写出来的词：只参与加分和挑句，不上高亮 ——
    不然「读取不到」会拉进 缺席/没有/标签/警告 一串，整段被标成一片黄。
    """

    __slots__ = ("chunk", "score", "terms", "extra", "why")

    def __init__(self, chunk, score, terms, why=None, extra=None):
        self.chunk = chunk
        self.score = score
        self.terms = terms
        self.extra = extra or []
        self.why = why or {}


class Index(object):
    """片段索引。几百到几千块，建索引是毫秒级，每次导入后重建即可。"""

    def __init__(self, chunks):
        self.chunks = list(chunks or [])
        self.postings = {}
        self.doclen = []
        self.head_tok = []           # 每块的节名切成词，用来判断"这块整块就在讲这件事"
        self.cover = []              # 封面 / 目录块：没有节名，或者正文就是"目录"
        self.avgdl = 1.0
        self._build()

    def _build(self):
        total = 0
        for i, c in enumerate(self.chunks):
            text = (c.get("text") or "") + " " + (c.get("heading") or "")
            toks = tokenize(text)
            self.doclen.append(max(1, len(toks)))
            total += self.doclen[-1]
            self.head_tok.append(set(tokenize(c.get("heading") or "")))
            h = (c.get("heading") or "").strip()
            t = (c.get("text") or "").strip()
            self.cover.append((not h) or t == "目录" or t.endswith("目录"))
            for t2 in set(toks):
                self.postings.setdefault(t2, {})[i] = toks.count(t2)
        self.avgdl = (float(total) / len(self.chunks)) if self.chunks else 1.0

    def __len__(self):
        return len(self.chunks)

    def _idf(self, term):
        df = len(self.postings.get(term, ()))
        n = max(1, len(self.chunks))
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    def query(self, text, limit=24, src_ids=None, kind=None, trace=None):
        """trace 传一个 dict 进来，会被填成这次检索的完整过程（答不上来的证据）。"""
        t0 = time.time()
        q = (text or "").strip()
        tr = trace if trace is not None else {}
        tr.clear()

        if not self.chunks:
            tr.update(verdict="no_db", q=q, terms=[], dropped=[], ext=[],
                      intent="normal", need=0, cand=0, kept=0, ms=0.0)
            return []
        if not q:
            tr.update(verdict="no_q", q=q, terms=[], dropped=[], ext=[],
                      intent="normal", need=0, cand=0, kept=0, ms=0.0)
            return []

        raw = tokenize(q, cut_stop=True)
        terms = [t for t in raw if t in self.postings]
        # df=0 的词（"到"这种停用词切完残留的单字）不进分母，否则长问句必死
        dropped = [t for t in raw if t not in self.postings]
        ent = [t for t in terms if is_entity(t)]
        ext = [t for t in expand(q) if t in self.postings and t not in terms]
        it_name, it_words = intent(q)
        it_words = [w for w in it_words if w in self.postings]

        if not terms and not ext:
            tr.update(verdict="no_terms", q=q, terms=[], dropped=dropped, ext=[],
                      intent=it_name, need=0, cand=0, kept=0,
                      ms=(time.time() - t0) * 1000.0)
            return []

        k1, b = 1.2, 0.6
        scores, hit_o, hit_e, why = {}, {}, {}, {}

        def add(term, weight, is_orig):
            for i, tf in self.postings.get(term, {}).items():
                if src_ids and self.chunks[i].get("src_id") not in src_ids:
                    continue
                dl = self.doclen[i]
                denom = tf + k1 * (1 - b + b * dl / self.avgdl)
                if not denom:
                    continue
                s = weight * self._idf(term) * (tf * (k1 + 1)) / denom
                scores[i] = scores.get(i, 0.0) + s
                (hit_o if is_orig else hit_e).setdefault(i, []).append(term)

        for t in terms:
            add(t, 1.0, True)
        for t in ext:
            add(t, W_EXT, False)

        if not scores:
            tr.update(verdict="no_match", q=q, terms=terms, dropped=dropped,
                      ext=ext, intent=it_name, need=0, cand=0, kept=0,
                      ms=(time.time() - t0) * 1000.0)
            return []

        # 问法加权：问"为什么"就往"日志 / 常见问题 / 缺席"这些段上压
        boost_hit = {}
        if it_words:
            for i in list(scores):
                body = ((self.chunks[i].get("text") or "") + " "
                        + (self.chunks[i].get("heading") or "")).lower()
                got = [w for w in it_words if w in body][:3]
                if got:
                    scores[i] += W_INTENT * sum(self._idf(w) for w in got)
                    boost_hit[i] = got
                    why[i] = why.get(i, 0) + 1

        # 封面 / 目录块降权：它把一堆节名攒在一段里，词项最容易全中，
        # 但它什么也没讲。不压的话"点位表怎么生成"第一条永远是封面页。
        for i in list(scores):
            if self.cover[i]:
                scores[i] *= 0.72

        # 节名命中：整块都在讲这件事（"2.7 点位表 14 列" 比文档目录块更该排前面）。
        # 只看原词 —— 扩展词是猜的，不能拿它去抬标题。
        for i in list(scores):
            got = [t for t in terms if t in self.head_tok[i]]
            if got:
                scores[i] += 0.55 * sum(self._idf(t) for t in got[:3])

        # 整句 / 标题原样命中：把"就是这段"的顶上来
        flat = re.sub(r"\s+", "", q).lower()
        for i in list(scores):
            c = self.chunks[i]
            body = re.sub(r"\s+", "", ((c.get("text") or "") + (c.get("heading") or "")).lower())
            if len(flat) >= 2 and flat in body:
                scores[i] += 5.0 + min(4.0, len(flat) * 0.12)
            if len(flat) >= 3 and flat in re.sub(r"\s+", "", (c.get("heading") or "").lower()):
                scores[i] += 2.5

        # 门槛：编号命中了就放行（need=1），不再要求"命中词数占多数"
        if ent:
            need = 1
        elif len(terms) <= 2:
            need = max(1, len(terms))
        else:
            need = max(2, int(len(terms) * 0.6))

        def ok(i):
            n_o, n_e = len(set(hit_o.get(i, []))), len(set(hit_e.get(i, [])))
            if n_o >= need:
                return True
            # 原词沾了一点、扩展词也成片命中 = 改写命中了，算数
            if n_o >= 1 and n_e >= 2:
                return True
            if n_e >= 3:
                return True
            return False

        keep = [i for i in scores if ok(i)]
        if not keep:
            # 兜底：有编号就再放宽一次；没有就明说查不到，不硬凑
            if ent:
                keep = [i for i in scores if hit_o.get(i)]
            if not keep:
                best = max([len(set(v)) for v in hit_o.values()] or [0])
                tr.update(verdict="below_threshold", q=q, terms=terms,
                          dropped=dropped, ext=ext, intent=it_name,
                          need=need, cand=len(scores), kept=0, best_orig=best,
                          entity=bool(ent), ms=(time.time() - t0) * 1000.0)
                return []

        keep.sort(key=lambda i: -scores[i])
        out = []
        for i in keep[:limit]:
            c = self.chunks[i]
            if kind and c.get("kind") != kind:
                continue
            o = _uniq(list(hit_o.get(i, [])))
            e = [t for t in _uniq(list(hit_e.get(i, []))) if t not in o]
            out.append(Result(c, scores[i],
                              sorted(o, key=lambda t: -self._idf(t)),
                              {"orig": len(o), "ext": len(e),
                               "intent": boost_hit.get(i, [])},
                              extra=sorted(e, key=lambda t: -self._idf(t))))

        tr.update(verdict="ok", q=q, terms=terms, dropped=dropped, ext=ext,
                  intent=it_name, need=need, cand=len(scores), kept=len(keep),
                  entity=bool(ent), top=[r.chunk.get("id") for r in out[:3]],
                  ms=(time.time() - t0) * 1000.0)
        return out

    def suggest(self, n=8):
        """首页给几个"能问什么"的例子 —— 空页面最需要的是这个。"""
        out = []
        for c in self.chunks:
            if c.get("kind") == "h":
                out.append(c.get("text") or "")
            if len(out) >= n:
                break
        return [s for s in out if s]
