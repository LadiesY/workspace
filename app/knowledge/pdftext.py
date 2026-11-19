# -*- coding: utf-8 -*-
"""PDF 文字层抽取（零依赖）。

为什么自己写：软件随包带的是精简版 Python，没有 pip、没有 pypdf/pymupdf，
而知识库必须能在**客户现场的离线机器**上导入资料。几百行标准库代码换掉一个
第三方包，比让运维去装包靠谱。

能力边界（够用就好，不假装全能）：

* 只认**有文字层**的 PDF（Word 导出、手册、规格书都属于这类）；
* 扫描件（整页是图、没有文字）抽不出字 —— 上层会提示"这份像是扫描件"；
* 不还原排版精细位置，只保证**逐行可读、顺序正确**，够检索用；
* 表格按行还原（每行一条），不做单元格合并。

实现要点：解析对象 → 页面树 → 内容流里的 BT/ET 文本块 → 用 ToUnicode CMap
把字形码翻成 Unicode → 按 y 坐标聚行、按 x 排序拼字。
"""

import re
import zlib

# ---------------------------------------------------------------- 对象层


def _obj_span(raw, num):
    """返回对象体在文件里的 (start, end)；找不到返回 None。"""
    m = re.search(rb"(?<![0-9])%d\s+(\d+)\s+obj" % num, raw)
    if not m:
        return None
    s = m.end()
    i = s
    depth = 0
    end = None
    while i < len(raw):
        c = raw[i:i + 1]
        two = raw[i:i + 2]
        if two == b"<<":
            depth += 1
            i += 2
            continue
        if two == b">>":
            depth -= 1
            i += 2
            if depth == 0:
                end = i
                break
            continue
        if c == b"(":                      # 跳过字符串里的括号
            j = i + 1
            lvl = 1
            while j < len(raw) and lvl:
                if raw[j:j + 1] == b"\\":
                    j += 2
                    continue
                if raw[j:j + 1] == b"(":
                    lvl += 1
                elif raw[j:j + 1] == b")":
                    lvl -= 1
                j += 1
            i = j
            continue
        i += 1
    if end is None:
        e = raw.find(b"endobj", s)
        return (s, e if e > 0 else len(raw))
    return (s, end)


def _obj(raw, num):
    """返回 (字典头 bytes, 流数据 bytes)。没有流时数据为空。"""
    sp = _obj_span(raw, num)
    if not sp:
        return None, b""
    s, e = sp
    head = raw[s:e]
    k = raw.find(b"stream", e)
    if k < 0:
        return head, b""
    k2 = raw.find(b"endobj", e)
    if 0 < k2 < k:                          # stream 在 endobj 之后，说明本对象没有流
        return head, b""
    p = k + 6
    if raw[p:p + 2] == b"\r\n":
        p += 2
    elif raw[p:p + 1] in (b"\n", b"\r"):
        p += 1
    m = re.search(rb"/Length\s+(\d+)(?:\s+(\d+)\s+R)?", head)
    data = None
    if m:
        if m.group(2) is None:
            data = raw[p:p + int(m.group(1))]
        else:
            lsp = _obj_span(raw, int(m.group(1)))
            if lsp:
                v = re.search(rb"\d+", raw[lsp[0]:lsp[1]])
                if v:
                    data = raw[p:p + int(v.group(0))]
    if data is None:
        k3 = raw.find(b"endstream", p)
        data = raw[p:k3 if k3 > 0 else len(raw)].rstrip(b"\r\n")
    return head, data


def _inflate(head, data):
    if b"FlateDecode" not in (head or b""):
        return data
    try:
        return zlib.decompress(data)
    except Exception:
        for skip in (0, 1, 2):
            try:
                return zlib.decompressobj().decompress(data[skip:])
            except Exception:
                pass
        return b""


def _ref(head, key):
    m = re.search(rb"/" + key + rb"\s+(\d+)\s+\d+\s+R", head or b"")
    return int(m.group(1)) if m else None


def _val(head, key):
    m = re.search(rb"/" + key + rb"\s*(\[[^\]]*\]|[^/>\[\]()]+|/[^\s/\[\]<>]+)",
                  head or b"")
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------- 字体


def _uni(hexbytes):
    try:
        b = bytes.fromhex(hexbytes.decode("ascii"))
    except Exception:
        return ""
    if len(b) % 2:
        b = b"\x00" + b
    try:
        return b.decode("utf-16-be", "replace")
    except Exception:
        return ""


def _parse_cmap(data):
    """ToUnicode CMap -> {字形码: 字符串}"""
    cmap = {}
    if not data:
        return cmap
    for blk in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            cmap[int(src, 16)] = _uni(dst)
    for blk in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for m in re.finditer(
                rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)",
                blk):
            a, b = int(m.group(1), 16), int(m.group(2), 16)
            dst = m.group(3)
            if dst.startswith(b"["):
                for i, it in enumerate(re.findall(rb"<([0-9A-Fa-f]+)>", dst)):
                    cmap[a + i] = _uni(it)
            else:
                base = int(dst[1:-1], 16)
                for i in range(min(b - a + 1, 65536)):
                    try:
                        cmap[a + i] = chr(base + i)
                    except ValueError:
                        pass
    return cmap


_CP1252 = {0x91: u"\u2018", 0x92: u"\u2019", 0x93: u"\u201c", 0x94: u"\u201d",
           0x95: u"\u2022", 0x96: u"\u2013", 0x97: u"\u2014", 0x85: u"\u2026"}


def _decode(kind, data, cmap):
    b = data
    if cmap:
        wide = any(k > 0xFF for k in cmap)
        out = []
        if wide:
            i = 0
            while i + 1 < len(b):
                out.append(cmap.get((b[i] << 8) | b[i + 1], ""))
                i += 2
            if i < len(b):
                out.append(cmap.get(b[i], ""))
        else:
            for c in b:
                out.append(cmap.get(c, ""))
        return "".join(out)
    if kind == "s":
        try:
            return b.decode("cp1252")
        except Exception:
            return "".join(_CP1252.get(c, chr(c)) for c in b)


# ---------------------------------------------------------------- 内容流

_TOKEN = re.compile(rb"""
    (?P<num>[+-]?(?:\d+\.\d*|\.\d+|\d+))
  | (?P<name>/[^\s/\[\]<>(){}%]*)
  | (?P<str>\((?:\\.|[^\\()]|\((?:\\.|[^\\()])*\))*\))
  | (?P<hex><[0-9A-Fa-f\s]*>)
  | (?P<arr>[\[\]])
  | (?P<op>[A-Za-z'"*][A-Za-z0-9'"*]*)
""", re.X | re.S)


def _unescape(s):
    body = s[1:-1]
    out = bytearray()
    i = 0
    mp = {b"n": 10, b"r": 13, b"t": 9, b"b": 8, b"f": 12,
          b"(": 40, b")": 41, b"\\": 92}
    while i < len(body):
        c = body[i:i + 1]
        if c == b"\\":
            n = body[i + 1:i + 2]
            if n in mp:
                out.append(mp[n])
                i += 2
            elif n.isdigit():
                m = re.match(rb"[0-7]{1,3}", body[i + 1:i + 4])
                out.append(int(m.group(0), 8) & 0xFF)
                i += 1 + len(m.group(0))
            elif n in (b"\n", b"\r"):
                i += 2
            else:
                out.append(ord(n) if n else 92)
                i += 2
        else:
            out += c
            i += 1
    return bytes(out)


def _is_cjk(ch):
    if not ch:
        return False
    o = ord(ch)
    return 0x2E80 <= o <= 0x9FFF or 0xFF00 <= o <= 0xFFEF


def _width(s, size):
    return sum(1.0 if ord(c) > 0x2E80 else 0.52 for c in s) * size


def _tidy(s):
    s = re.sub(r"[ \t]{2,}", " ", s)
    # 中文之间被拆出来的空格是解析产物，原文没有
    s = re.sub(u"(?<=[\u4e00-\u9fff])[ ]+(?=[\u4e00-\u9fff\uff0c\u3002\u3001"
               u"\uff1b\uff1a\uff1f\uff01\uff08\uff09\u300a\u300b])", "", s)
    s = re.sub(u"(?<=[\uff0c\u3002\u3001\uff1b\uff1a\uff1f\uff01\uff09\u300b])"
               u"[ ]+(?=[\u4e00-\u9fff])", "", s)
    # 型号里的连字符：`MV -CS200 -10GM` / `MV -CS200-10GM` 都是同一个型号
    s = re.sub(r"(?<=[A-Za-z0-9])[ ]*-(?=[A-Za-z0-9])", "-", s)
    s = re.sub(r"(?<=[A-Za-z0-9])-[ ]+(?=[A-Za-z0-9])", "-", s)
    return s.strip()


def _page_lines(content, fonts):
    """解析一页内容流 -> [(行文本, 列间隙数)]，已按阅读顺序排好。

    列间隙数 = 这一行里出现几处「明显大于字间距的空白」。规格书的表格行
    `参数 | 值 | 值` 会数出 1~2 处；普通叙述段落基本是 0。上层靠这个把
    表格行单独切块，检索「分辨率」才能直接落到那一行，而不是糊在整页里。
    """
    items = []          # (x, y, size, text)
    tm = [1, 0, 0, 1, 0, 0]
    tlm = [1, 0, 0, 1, 0, 0]
    size, leading, font = 10.0, 12.0, None
    stack, ops = [], []
    pos, n = 0, len(content)

    def emit(txt):
        if txt:
            items.append((tm[4], tm[5], size, txt))

    while pos < n:
        m = _TOKEN.search(content, pos)
        if not m:
            break
        pos = m.end()
        g = m.lastgroup
        if g == "num":
            ops.append(float(m.group("num")))
        elif g == "arr":
            ops.append(m.group("arr"))
        elif g == "name":
            ops.append(m.group("name")[1:].decode("latin-1"))
        elif g == "str":
            ops.append(("s", _unescape(m.group("str"))))
        elif g == "hex":
            h = re.sub(rb"\s", b"", m.group("hex")[1:-1])
            if len(h) % 2:
                h += b"0"
            try:
                ops.append(("h", bytes.fromhex(h.decode("ascii"))))
            except Exception:
                ops.append(("h", b""))
        else:
            op = m.group("op")
            if op == b"BI":                       # 内嵌图，整段跳过
                k = content.find(b"EI", pos)
                pos = k + 2 if k > 0 else n
                ops = []
                continue
            if op == b"q":
                stack.append((tm[:], font, size))
            elif op == b"Q":
                if stack:
                    tm, font, size = stack.pop()
            elif op == b"BT":
                tm = [1, 0, 0, 1, 0, 0]
                tlm = [1, 0, 0, 1, 0, 0]
            elif op == b"Tf":
                if len(ops) >= 2:
                    if isinstance(ops[-2], str):
                        font = ops[-2]
                    if isinstance(ops[-1], float):
                        size = ops[-1]
            elif op == b"TL":
                if ops and isinstance(ops[-1], float):
                    leading = ops[-1]
            elif op in (b"Td", b"TD"):
                if len(ops) >= 2 and isinstance(ops[-2], float) and isinstance(ops[-1], float):
                    tx, ty = ops[-2], ops[-1]
                    if op == b"TD":
                        leading = -ty
                    tlm = [tlm[0], tlm[1], tlm[2], tlm[3],
                           tlm[4] + tx * tlm[0] + ty * tlm[2],
                           tlm[5] + tx * tlm[1] + ty * tlm[3]]
                    tm = tlm[:]
            elif op == b"Tm":
                if len(ops) >= 6 and all(isinstance(v, float) for v in ops[-6:]):
                    tlm = ops[-6:]
                    tm = tlm[:]
            elif op == b"T*":
                tlm = [tlm[0], tlm[1], tlm[2], tlm[3],
                       tlm[4] - leading * tlm[2], tlm[5] - leading * tlm[3]]
                tm = tlm[:]
            elif op in (b"Tj", b"'", b'"'):
                if op != b"Tj":
                    tlm = [tlm[0], tlm[1], tlm[2], tlm[3],
                           tlm[4] - leading * tlm[2], tlm[5] - leading * tlm[3]]
                    tm = tlm[:]
                if ops and isinstance(ops[-1], tuple):
                    emit(_decode(ops[-1][0], ops[-1][1], fonts.get(font)))
            elif op == b"TJ":
                buf = ""
                for v in ops:
                    if isinstance(v, tuple):
                        buf += _decode(v[0], v[1], fonts.get(font))
                emit(buf)
            ops = []

    if not items:
        return []
    items.sort(key=lambda t: (-t[1], t[0]))
    lines, cur = [], []
    last_y, last_sz = None, 10.0
    for x, y, sz, t in items:
        if last_y is None or abs(y - last_y) <= max(3.0, last_sz * 0.55):
            cur.append((x, sz, t))
        else:
            lines.append(cur)
            cur = [(x, sz, t)]
        last_y, last_sz = y, sz
    if cur:
        lines.append(cur)

    out = []
    for ln in lines:
        ln.sort(key=lambda p: p[0])
        buf, prev_end, prev_sz, gaps = "", None, 10.0, 0
        for x, sz, t in ln:
            if prev_end is not None:
                gap = x - prev_end
                if gap > max(1.6, prev_sz * 0.22) and buf and not buf.endswith(" "):
                    if not (_is_cjk(buf[-1:]) and _is_cjk(t[:1])):
                        buf += " "
                # 大间隙 = 分栏：这一行多半是表格行（参数名 | 值 | 值）
                if gap > prev_sz * 1.15:
                    gaps += 1
            buf += t
            prev_end = x + _width(t, sz)
            prev_sz = sz
        s = _tidy(buf)
        if s:
            out.append((s, gaps))
    return out


def _page_numbers(raw):
    """按页面树顺序取页对象号。"""
    pages = []
    m = re.search(rb"/Type\s*/Catalog", raw)
    root = None
    if m:
        blk = raw[max(0, m.start() - 240):m.start() + 240]
        r = re.search(rb"/Pages\s+(\d+)\s+0\s+R", blk)
        if r:
            root = int(r.group(1))

    def walk(num, depth=0):
        if num is None or depth > 30:
            return
        head, _ = _obj(raw, num)
        if head is None:
            return
        if re.search(rb"/Type\s*/Pages\b", head):
            kids = _val(head, b"Kids")
            for k in (re.findall(rb"(\d+)\s+0\s+R", kids) if kids else []):
                walk(int(k), depth + 1)
        elif re.search(rb"/Type\s*/Page\b(?!s)", head):
            pages.append(num)

    if root:
        walk(root)
    if not pages:                                  # 没有目录树就按对象顺序兜底
        for num in range(1, min(len(raw) // 20, 4000)):
            head, _ = _obj(raw, num)
            if head and re.search(rb"/Type\s*/Page\b(?!s)", head):
                pages.append(num)
    return pages


def _page_fonts(raw, head):
    """页面资源里的字体 -> {名字: cmap}"""
    fonts = {}
    fm = re.search(rb"/Font\s*<<(.*?)>>", head, re.S)
    if not fm:
        return fonts
    for name, ref in re.findall(rb"/([A-Za-z0-9]+)\s+(\d+)\s+0\s+R", fm.group(1)):
        fh, _ = _obj(raw, int(ref))
        cmap = {}
        tu = _ref(fh, b"ToUnicode")
        if tu:
            th, td = _obj(raw, tu)
            cmap = _parse_cmap(_inflate(th, td))
        fonts[name.decode()] = cmap or None
    return fonts


def _page_stream(raw, head):
    c = _ref(head, b"Contents")
    if not c:
        return b""
    ch, cd = _obj(raw, c)
    data = _inflate(ch, cd)
    if data:
        return data
    r = re.search(rb"/Contents\s*\[(.*?)\]", head, re.S)     # Contents 是数组
    if not r:
        return b""
    parts = []
    for ref in re.findall(rb"(\d+)\s+0\s+R", r.group(1)):
        h2, d2 = _obj(raw, int(ref))
        parts.append(_inflate(h2, d2))
    return b"\n".join(parts)


def extract(path):
    """返回 {"pages": [ [行, ...], ... ], "scanned": bool}"""
    raw = open(path, "rb").read()
    if not raw.startswith(b"%PDF"):
        raise ValueError("不是 PDF 文件")
    nums = _page_numbers(raw)
    pages = []
    for pnum in nums:
        head, _ = _obj(raw, pnum)
        if head is None:
            continue
        try:
            lines = _page_lines(_page_stream(raw, head), _page_fonts(raw, head))
        except Exception:
            lines = []
        pages.append(lines)
    text_len = sum(sum(len(t) for t, _g in p) for p in pages)
    return {"pages": pages,
            "scanned": text_len < 40 * max(1, len(pages))}
