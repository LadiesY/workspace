# -*- coding: utf-8 -*-
"""Word（.docx）抽取（零依赖）。

.docx 就是个 zip：word/document.xml 里按顺序排着段落（w:p）和表格（w:tbl），
用标准库 zipfile + ElementTree 就能读，不需要 python-docx。

标题靠 `<w:outlineLvl w:val="n">` 认 —— Word 的"标题 1/2/3"样式一定带这个，
比猜字号、猜加粗稳。目录页那种带页码的段落按"含 PAGEREF/HYPERLINK 域"直接跳过，
否则会把目录当成正文重复一遍。
"""

import re
import zipfile
import xml.etree.ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _text(pnode):
    """段落纯文本：w:t 取字，w:tab 变空格，w:br 变换行。"""
    out = []
    for node in pnode.iter():
        tag = node.tag
        if tag == W + "t":
            out.append(node.text or "")
        elif tag == W + "tab":
            out.append(" ")
        elif tag == W + "br":
            out.append("\n")
    return "".join(out)


def _is_toc(pnode):
    """目录项：带 PAGEREF / TOC / HYPERLINK 域，正文里不会出现。"""
    for node in pnode.iter():
        if node.tag == W + "instrText":
            s = (node.text or "")
            if "PAGEREF" in s or "TOC " in s or "HYPERLINK" in s:
                return True
    return False


def _level(pnode):
    ppr = pnode.find(W + "pPr")
    if ppr is None:
        return 0
    lv = ppr.find(W + "outlineLvl")
    if lv is None:
        return 0
    try:
        return int(lv.get(W + "val")) + 1
    except (TypeError, ValueError):
        return 0


def _cell_text(tc):
    parts = []
    for pnode in tc.findall(W + "p"):
        t = _text(pnode).strip()
        if t:
            parts.append(t)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _table_rows(tbl):
    rows = []
    for tr in tbl.findall(W + "tr"):
        cells = [_cell_text(tc) for tc in tr.findall(W + "tc")]
        cells = [c for c in cells if c != ""] or cells
        if any(c for c in cells):
            rows.append(cells)
    return rows


def extract(path):
    """返回 {"blocks": [ {kind, text, level, rows, heading} ], "title": str}

    kind: "h"（标题）/ "p"（正文段）/ "row"（表格行）
    """
    z = zipfile.ZipFile(path)
    m = re.search(r"<dc:title>(.*?)</dc:title>", _safe(z, "docProps/core.xml"), re.S)
    title = (m.group(1).strip() if m else "")

    root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(W + "body")
    blocks = []
    heading = ""
    for node in (body if body is not None else []):
        if node.tag == W + "p":
            if _is_toc(node):
                continue
            txt = _text(node).strip()
            if not txt:
                continue
            lv = _level(node)
            if lv:
                heading = txt
                blocks.append({"kind": "h", "text": txt, "level": lv,
                               "heading": txt})
            else:
                blocks.append({"kind": "p", "text": txt, "level": 0,
                               "heading": heading})
        elif node.tag == W + "tbl":
            rows = _table_rows(node)
            if not rows:
                continue
            head = rows[0]
            data = rows[1:] if len(rows) > 1 else []
            blocks.append({"kind": "row", "text": "", "level": 0,
                           "heading": heading, "rows": [head] + data,
                           "header": head, "table": True})
    return {"blocks": blocks, "title": title or path}


def _safe(z, name):
    try:
        return z.read(name).decode("utf-8", "replace")
    except Exception:
        return ""


def readable(path):
    """能不能读（给导入时提前判错用）。"""
    try:
        z = zipfile.ZipFile(path)
        return "word/document.xml" in z.namelist()
    except Exception:
        return False
