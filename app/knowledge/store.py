# -*- coding: utf-8 -*-
"""知识库的落地存储。

全部在 `data/kb/` 下，跟着程序目录走，方便整包拷到现场机器：

    data/kb/sources.json    资料清单（哪几份、什么时候导的、有几个片段）
    data/kb/chunks.json     切好的片段（检索就是查它）
    data/kb/files/          原文件副本

为什么要复制副本：用户给的资料常常在微信临时目录、U 盘、桌面，随时会被清掉。
库里只存路径的话，过两天整库就空了。副本不大（几 MB），值得。
"""

import json
import os
import shutil
import traceback
from datetime import datetime

from ..devices.base import DATA_DIR
from . import extract

KB_DIR = os.path.join(DATA_DIR, "kb")
FILES_DIR = os.path.join(KB_DIR, "files")
SOURCES_PATH = os.path.join(KB_DIR, "sources.json")
CHUNKS_PATH = os.path.join(KB_DIR, "chunks.json")
HISTORY_PATH = os.path.join(KB_DIR, "history.json")

VERSION = 1
HIST_MAX = 60


def _ensure():
    for d in (KB_DIR, FILES_DIR):
        if not os.path.isdir(d):
            os.makedirs(d)


def _read(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write(path, obj):
    _ensure()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load():
    """读整库：{"sources": [...], "chunks": [...], "version": 1}"""
    st = _read(SOURCES_PATH, None)
    ck = _read(CHUNKS_PATH, None)
    return {
        "version": VERSION,
        "sources": (st or {}).get("sources", []) if isinstance(st, dict) else [],
        "chunks": (ck or {}).get("chunks", []) if isinstance(ck, dict) else [],
    }


def save_sources(sources):
    _write(SOURCES_PATH, {"version": VERSION, "sources": sources})


def save_chunks(chunks):
    _write(CHUNKS_PATH, {"version": VERSION, "chunks": chunks})


def _next_id(sources):
    n = 1
    used = {s.get("id") for s in sources}
    while ("s%d" % n) in used:
        n += 1
    return "s%d" % n


def _stored_name(name, sources):
    """副本文件名：重名加序号，不覆盖已有的。"""
    base, ext = os.path.splitext(name)
    cand = name
    n = 2
    used = {s.get("stored") for s in sources}
    while cand in used or os.path.exists(os.path.join(FILES_DIR, cand)):
        cand = "%s_%d%s" % (base, n, ext)
        n += 1
    return cand


def add(paths, on_step=None):
    """导入一批资料，返回 (成功数, 报告 [{ok, name, msg, chunks}])"""
    _ensure()
    state = load()
    sources = state["sources"]
    report = []
    changed = False

    for i, src in enumerate(paths, 1):
        name = os.path.basename(src)
        if on_step:
            on_step(i, len(paths), name)
        try:
            k = extract.kind_of(src)
            if not k:
                raise ValueError("不支持的格式（只收 PDF / docx / txt / md）")
            if not os.path.isfile(src):
                raise ValueError("文件不存在")
            base_name = os.path.basename(src)
            # 同名同尺寸 = 同一份，直接替换，不重复入库
            old = None
            for s in sources:
                if s.get("name") == base_name and s.get("size") == os.path.getsize(src):
                    old = s
                    break
            if old:
                sources.remove(old)
                sid = old["id"]
                stored = old["stored"]
            else:
                sid = _next_id(sources)
                stored = _stored_name(base_name, sources)
            dst = os.path.join(FILES_DIR, stored)
            if os.path.abspath(src) != os.path.abspath(dst):
                shutil.copy2(src, dst)
            doc = extract.load(dst)
            chunks = extract.chunk(doc, sid)
            if not chunks:
                raise ValueError(doc.get("note") or "没抽到任何文字")
            sources.append({
                "id": sid,
                "name": base_name,
                "kind": doc["kind"],
                "stored": stored,
                "size": os.path.getsize(dst),
                "pages": doc.get("pages", 0),
                "chunks": len(chunks),
                "added": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "note": doc.get("note", ""),
                "from": src,
            })
            report.append({"ok": True, "name": base_name, "id": sid,
                           "chunks": len(chunks), "note": doc.get("note", "")})
            changed = True
        except Exception as e:
            traceback.print_exc()
            report.append({"ok": False, "name": name, "msg": str(e)})

    if changed:
        rebuild(sources, report)
    return sum(1 for r in report if r["ok"]), report


def rebuild(sources=None, report=None):
    """按当前清单重切全部片段。资料几百份也就一两秒，全量重建最不容易错。"""
    _ensure()
    if sources is None:
        sources = load()["sources"]
    chunks = []
    for s in sources:
        p = os.path.join(FILES_DIR, s.get("stored", ""))
        try:
            doc = extract.load(p)
            got = extract.chunk(doc, s["id"])
            for c in got:                    # 每块都记着"我从哪份资料来"
                c["src_id"] = s["id"]
                c["src"] = s["name"]
            s["chunks"] = len(got)
            chunks.extend(got)
        except Exception:
            traceback.print_exc()
            s["chunks"] = 0
    save_sources(sources)
    save_chunks(chunks)
    return sources, chunks


def remove(src_id):
    state = load()
    sources = [s for s in state["sources"] if s.get("id") != src_id]
    for s in state["sources"]:
        if s.get("id") == src_id:
            p = os.path.join(FILES_DIR, s.get("stored", ""))
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass
    return rebuild(sources)


def stats(state=None):
    state = state or load()
    return {
        "files": len(state["sources"]),
        "chunks": len(state["chunks"]),
        "size": sum(s.get("size", 0) for s in state["sources"]),
    }


# ---------------------------------------------------------------- 搜索记录
# 用户要的"日志"不是切词/df/耗时那一串（那是给开发看的），而是"我问过什么"。
# 单独存一个文件，和资料库解耦：清空记录不会动到资料，重新导资料也不丢记录。

def load_history():
    d = _read(HISTORY_PATH, {})
    items = d.get("items") if isinstance(d, dict) else None
    return [x for x in (items or []) if isinstance(x, dict) and (x.get("q") or "").strip()]


def push_history(q, n=0):
    """记一条问句。

    同一条只留最新那次；**边打边搜的中间态直接顶掉** —— 用户敲"相机是哪个牌子"
    的过程中会先搜到"相机"、"相机是哪"，那几条是过程不是记录，留着只会把面板占满。
    """
    q = (q or "").strip()
    if len(q) < 2:
        return load_history()
    items = load_history()
    if items:
        top = (items[0].get("q") or "").strip()
        if top and (q.startswith(top) or top.startswith(q)):
            items.pop(0)
    items = [x for x in items if (x.get("q") or "").strip() != q]
    items.insert(0, {
        "q": q,
        "n": int(n or 0),
        "t": datetime.now().strftime("%m-%d %H:%M"),
    })
    try:
        _write(HISTORY_PATH, {"version": VERSION, "items": items[:HIST_MAX]})
    except Exception:
        traceback.print_exc()
    return items[:HIST_MAX]


def clear_history():
    try:
        _write(HISTORY_PATH, {"version": VERSION, "items": []})
    except Exception:
        traceback.print_exc()
    return []


def ensure_default(on_step=None):
    """库里空着时，先把随软件附带的资料导进去。

    只认 `docs/` 目录下存在的文件 —— 没有就跳过，不报错。
    """
    state = load()
    if state["sources"]:
        return 0, []
    from ..devices.base import BASE_DIR
    cands = []
    here = os.path.join(BASE_DIR, "docs")
    if os.path.isdir(here):
        for fn in sorted(os.listdir(here)):
            if extract.kind_of(fn):
                cands.append(os.path.join(here, fn))
    if not cands:
        return 0, []
    return add(cands, on_step)
