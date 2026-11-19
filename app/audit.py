# -*- coding: utf-8 -*-
"""审计日志：只追加、可自证、与业务数据分开存。

为什么不用普通文本日志
--------------------
条例和现场检查要回答的是"这条记录有没有被人改过"。普通日志改一行看不出来，
所以这里每条记录都带 **前一条的哈希 + 本条内容的哈希**（哈希链）：

    h(n) = sha256( seq | ts | user | role | action | target | result | detail | h(n-1) )

改了中间任何一条的任何一个字，从那条往后整条链都对不上，`verify()` 会指出
第一处断点。删掉一条同样断链。这不是防篡改的上限（拿到文件的系统管理员
可以整体重算），但足以让"悄悄改一条"留下证据，也比纯文本强得多。

三条硬约束
----------
1. **只追加**：本模块没有任何删除/改写接口，文件以 ``"a"`` 打开。别为了"清一下
   日志"加一个 truncate —— 那等于把审计体系作废。
2. **单独目录**：落 ``data/audit/``，不和 ``data/auth/``、``data/kb/`` 混。恢复备份时
   整个 audit 目录**不参与覆盖**（见 backup.py），否则"恢复一份旧备份"就能顺手
   把最近的审计抹掉。
3. **按月分文件**：``audit-YYYYMM.jsonl``，一条一行 JSON。用 JSONL 是为了能
   流式读、崩了也只坏最后一行，不影响前面的。
"""

import hashlib
import json
import os
import threading
from datetime import datetime

from .devices.base import DATA_DIR

AUDIT_DIR = os.path.join(DATA_DIR, "audit")

# 链的起点。第一条记录的 prev 用这个常量，校验时也拿它当初始值。
GENESIS = "0" * 64


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canon(rec):
    """把参与哈希的字段按固定顺序拼起来。

    只拼固定字段、不用 json.dumps(dict)：字典键序在不同 Python 版本/不同构造
    路径下都可能变，一变哈希就全错。这里是"协议"，改了要整库重算，别动。
    """
    return "|".join((
        str(rec.get("seq", "")),
        str(rec.get("ts", "")),
        str(rec.get("user", "")),
        str(rec.get("role", "")),
        str(rec.get("action", "")),
        str(rec.get("target", "")),
        str(rec.get("result", "")),
        str(rec.get("detail", "")),
        str(rec.get("prev", "")),
    ))


class AuditLog(object):
    def __init__(self, dirpath=AUDIT_DIR):
        self.dir = dirpath
        self._lock = threading.Lock()
        self._seq = None            # 惰性初始化：第一次写的时候从文件尾读回来
        self._prev = None
        # 当前操作人由 auth 注入（避免 audit 反过来 import auth 形成环）
        self.actor_provider = None

    # ---------------------------------------------------------------- 基础
    def _ensure_dir(self):
        if not os.path.isdir(self.dir):
            os.makedirs(self.dir)

    def _files(self):
        """按月文件名升序 —— 文件名就是时间序，不用读内容再排。"""
        try:
            names = sorted(n for n in os.listdir(self.dir)
                           if n.startswith("audit-") and n.endswith(".jsonl"))
        except (IOError, OSError):
            return []
        return [os.path.join(self.dir, n) for n in names]

    def _path(self, when=None):
        when = when or datetime.now()
        return os.path.join(self.dir, "audit-%s.jsonl" % when.strftime("%Y%m"))

    def _tail(self):
        """最后一条的 (seq, h)。文件不存在或读不出来就从链首开始。"""
        files = self._files()
        if not files:
            return 0, GENESIS
        last = files[-1]
        try:
            with open(last, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                step = min(size, 65536)
                f.seek(size - step)
                blob = f.read()
        except (IOError, OSError):
            return 0, GENESIS
        for line in reversed(blob.decode("utf-8", "replace").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue          # 崩在半行上的那条跳过，往前找
            return int(rec.get("seq", 0)), str(rec.get("h", GENESIS))
        return 0, GENESIS

    def _init_chain(self):
        if self._seq is None:
            self._seq, self._prev = self._tail()

    # ---------------------------------------------------------------- 写
    def write(self, action, target="", result="OK", detail="",
              user=None, role=None):
        """追加一条。user/role 不给就用当前会话的操作人（未登入显示"未登入"）。"""
        if user is None or role is None:
            name, rname = "未登入", "-"
            if self.actor_provider is not None:
                try:
                    name, rname = self.actor_provider()
                except Exception:
                    pass
            user = name if user is None else user
            role = rname if role is None else role

        with self._lock:
            self._ensure_dir()
            self._init_chain()
            rec = {
                "seq": self._seq + 1,
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "user": str(user or ""),
                "role": str(role or "-"),
                "action": str(action or ""),
                "target": str(target or ""),
                "result": str(result or "OK"),
                "detail": str(detail or ""),
            }
            rec["prev"] = self._prev
            rec["h"] = _sha(_canon(rec))
            try:
                with open(self._path(), "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())     # 断电也别丢这一条，审计不能靠缓存
            except (IOError, OSError):
                return None                 # 写不进去也不能把业务流程打断
            self._seq = rec["seq"]
            self._prev = rec["h"]
            return rec

    # ---------------------------------------------------------------- 读
    def entries(self, keyword="", result=None, user=None, action=None,
                limit=800):
        """倒序返回最近的记录（新的在前）。过滤都在内存里做 —— 量级不大。"""
        out = []
        kw = (keyword or "").strip().lower()
        for path in reversed(self._files()):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except (IOError, OSError):
                continue
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if result and rec.get("result") != result:
                    continue
                if user and rec.get("user") != user:
                    continue
                if action and rec.get("action") != action:
                    continue
                if kw:
                    blob = " ".join(str(rec.get(k, "")) for k in
                                    ("user", "role", "action", "target",
                                     "detail", "result")).lower()
                    if kw not in blob:
                        continue
                out.append(rec)
                if len(out) >= max(1, int(limit)):
                    return out
        return out

    def actions(self):
        """出现过的动作名，给筛选下拉用。"""
        seen = []
        for rec in self.entries(limit=5000):
            a = rec.get("action") or ""
            if a and a not in seen:
                seen.append(a)
        return seen

    def users(self):
        seen = []
        for rec in self.entries(limit=5000):
            n = rec.get("user") or ""
            if n and n not in seen:
                seen.append(n)
        return seen

    def count_user(self, name):
        """某用户名下有多少条记录 —— 删用户前要判断（有审计关联的只能禁用）。"""
        if not name:
            return 0
        return sum(1 for rec in self.entries(limit=100000)
                   if rec.get("user") == name)

    def stats(self):
        recs = self.entries(limit=1000000)
        if not recs:
            return {"count": 0, "first": "", "last": "", "files": 0}
        return {"count": len(recs), "first": recs[-1].get("ts", ""),
                "last": recs[0].get("ts", ""), "files": len(self._files())}

    # ---------------------------------------------------------------- 校验
    def verify(self):
        """从头重算整条链。

        返回 {"ok": bool, "total": int, "bad": int, "at": str}
        bad 是"第一处断点"的序号（1 起），ok=False 时 at 给出文件名和行号。
        注意：解析失败的行也算断点 —— 半行意味着这条记录不完整。
        """
        prev = GENESIS
        total = 0
        for path in self._files():
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except (IOError, OSError) as e:
                return {"ok": False, "total": total, "bad": total + 1,
                        "at": "%s 打不开：%s" % (os.path.basename(path), e)}
            for i, line in enumerate(lines, 1):
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    rec = json.loads(line)
                except ValueError:
                    return {"ok": False, "total": total, "bad": total,
                            "at": "%s 第 %d 行：不是合法 JSON（记录被截断？）"
                                  % (os.path.basename(path), i)}
                if rec.get("prev") != prev:
                    return {"ok": False, "total": total, "bad": total,
                            "at": "%s 第 %d 行：前一条哈希对不上（中间被删或被改）"
                                  % (os.path.basename(path), i)}
                if _sha(_canon(rec)) != rec.get("h"):
                    return {"ok": False, "total": total, "bad": total,
                            "at": "%s 第 %d 行：本条内容与哈希不符（被改过）"
                                  % (os.path.basename(path), i)}
                prev = rec.get("h")
        return {"ok": True, "total": total, "bad": 0, "at": ""}


# 全程序共用一个实例。谁要写审计就 import 它，不要各自 new。
audit = AuditLog()
