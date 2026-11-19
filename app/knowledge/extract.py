# -*- coding: utf-8 -*-
"""资料 → 可检索的片段（chunk）。

切块规则按**文档长什么样**分，不按主题分：

* Word 正文段：相邻段落合并到 400 字上下。一句话中间不断，宁可多带点上下文。
* Word 表格：**一行一条**，并把表头抄进去（`参数：分辨率；MV-CS200-10GM：5472 × 3648`）。
  不抄表头的话搜到「5472」不知道这列是分辨率还是别的东西。
* PDF：按行聚合，每块 2~5 行、不超 260 字。规格书的表格本来一行一个参数，
  聚合后仍能一眼看清，也不至于把整页糊成一块。

每块都带 `src`（哪份资料）、`loc`（第几页 / 哪一节），这是"能核对"的前提 ——
知识库给的任何一句话，都要能点回原文。
"""

import os
import re

from . import docxtext, pdftext

PARA_TARGET = 420          # Word 正文块的目标字数
PARA_GAP = 900             # 单段超长时自己单独成块
PDF_MAX = 260              # PDF 块最大字数
PDF_MIN_LINES = 2

SUFFIX = {".pdf": "pdf", ".docx": "docx", ".txt": "txt", ".md": "md"}

# PDF 的项目符号、小圆点、箭头常是 Symbol / Wingdings 的**私有区字符**（U+F0xx）。
# 换个字体就渲染成方块，正文里留着只会碍眼，也不参与检索 —— 抽出来直接洗掉。
_ICON = re.compile(u"[\ue000-\uf8ff\uf000-\uf0ff]")


def _scrub(t):
    return _ICON.sub("", t or "")


def kind_of(path):
    return SUFFIX.get(os.path.splitext(path)[1].lower(), "")


def load(path):
    """读一份资料 -> {"kind", "title", "blocks", "note"}"""
    k = kind_of(path)
    if k == "pdf":
        got = pdftext.extract(path)
        pages = got["pages"]
        blocks = []
        for i, lines in enumerate(pages, 1):
            for ln, gaps in lines:
                # 一处以上大间隙且不算长 = 表格行，单独成块（别和上下行搅在一起）
                row = gaps >= 1 and len(ln) <= 150
                blocks.append({"kind": "row" if row else "p", "text": ln,
                               "level": 0, "heading": "第 %d 页" % i, "page": i})
        note = ""
        if got["scanned"]:
            note = "没抽到文字，像是扫描件（整页是图），需要先 OCR"
        return {"kind": "pdf", "title": os.path.basename(path),
                "blocks": blocks, "pages": len(pages), "note": note}
    if k == "docx":
        got = docxtext.extract(path)
        return {"kind": "docx", "title": got["title"] or os.path.basename(path),
                "blocks": got["blocks"], "pages": 0, "note": ""}
    if k in ("txt", "md"):
        with open(path, "rb") as f:
            raw = f.read()
        for enc in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                text = ""
        if not text:
            raise ValueError("文本编码认不出来（试过 UTF-8 / GBK）")
        blocks = []
        heading = ""
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                heading = s.lstrip("# ").strip()
                blocks.append({"kind": "h", "text": heading, "level":
                               min(3, len(s) - len(s.lstrip("#"))), "heading": heading})
            else:
                blocks.append({"kind": "p", "text": s, "level": 0, "heading": heading})
        return {"kind": k, "title": os.path.basename(path),
                "blocks": blocks, "pages": 0, "note": ""}
    raise ValueError("只支持 PDF / Word(.docx) / txt / md，别的格式先转一下")


def _flush(buf, out, src_id, heading, kind="p", extra=None):
    if not buf:
        return
    text = "\n".join(buf) if kind == "p" else buf
    out.append({
        "id": "%s-%03d" % (src_id, len(out) + 1),
        "kind": kind,
        "text": text,
        "heading": heading,
        "loc": heading or "开头",
    })
    if extra:
        out[-1].update(extra)


def chunk(doc, src_id):
    """doc -> [chunk]"""
    kind = doc["kind"]
    blocks = doc["blocks"]
    out = []
    for b in blocks:                       # 先统一洗掉字体图标，后面各分支就不用管了
        b["text"] = _scrub(b.get("text"))
        if isinstance(b.get("cells"), list):
            b["cells"] = [_scrub(x) for x in b["cells"]]

    if kind == "pdf":
        buf, heading = [], ""
        for b in blocks:
            t = b["text"]
            heading = b.get("heading") or heading
            if b["kind"] == "row":                      # 表格行：一行一块
                _flush(buf, out, src_id, heading)
                del buf[:]
                out.append({
                    "id": "%s-%03d" % (src_id, len(out) + 1),
                    "kind": "row", "text": t, "heading": heading, "loc": heading,
                })
                continue
            if len(t) > PDF_MAX * 1.6:                  # 超长行（整段）自己成块
                _flush(buf, out, src_id, heading)
                del buf[:]
                out.append({
                    "id": "%s-%03d" % (src_id, len(out) + 1),
                    "kind": "p", "text": t, "heading": heading, "loc": heading,
                })
                continue
            buf.append(t)
            if sum(len(x) for x in buf) >= PDF_MAX and len(buf) >= PDF_MIN_LINES:
                _flush(buf, out, src_id, heading)
                del buf[:]
        if buf:
            _flush(buf, out, src_id, heading)
        return out

    # Word / 文本：段落攒块，表格按行
    buf, heading, tbl_no = [], "", 0

    def flush():
        if buf:
            _flush(buf, out, src_id, heading)
            del buf[:]

    for b in blocks:
        if b["kind"] == "h":
            flush()
            heading = b["text"]
            out.append({
                "id": "%s-%03d" % (src_id, len(out) + 1),
                "kind": "h", "text": b["text"], "heading": b["text"],
                "loc": b["text"], "level": b.get("level", 1),
            })
        elif b["kind"] == "row":
            flush()
            tbl_no += 1
            rows = b.get("rows") or []
            head = b.get("header") or []
            for cells in rows[1:] if len(rows) > 1 else rows:
                text = _row_text(head, cells)
                if not text:
                    continue
                out.append({
                    "id": "%s-%03d" % (src_id, len(out) + 1),
                    "kind": "row", "text": text,
                    "heading": heading or ("表格 %d" % tbl_no),
                    "loc": (heading or "表格 %d" % tbl_no),
                    "header": head, "cells": cells,
                })
        else:
            t = b["text"]
            if buf and sum(len(x) for x in buf) + len(t) > PARA_TARGET:
                flush()
            buf.append(t)
            if len(t) >= PARA_GAP:
                flush()
    flush()
    return out


def _row_text(head, cells):
    """表格行 -> 一句话，表头跟着走。"""
    if not cells:
        return ""
    parts = []
    for i, c in enumerate(cells):
        c = (c or "").strip()
        if not c:
            continue
        h = (head[i] if i < len(head) else "").strip()
        parts.append("%s：%s" % (h, c) if h and h != c else c)
    return "；".join(parts)


def preview(text, n=90):
    s = " ".join((text or "").split())
    return s if len(s) <= n else s[:n] + "…"
