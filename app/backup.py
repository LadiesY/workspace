# -*- coding: utf-8 -*-
"""全量备份 / 校验 / 恢复。

备份包是普通 zip，命名 ``FULL-YYYYMMDD-HHMM.zip``（用户指定的规范，方便按名字
排序和追溯）。包内除业务数据外还有一个 ``MANIFEST.json``，记下每个文件的
大小和 sha256 —— **校验不靠 zip 自带的 CRC**：CRC 只能发现传输损坏，发现不了
"被人替换成一个同样能解开、但内容不同的包"。恢复前必须过一遍清单比对。

两处刻意的取舍
--------------
* **audit 目录进备份，但不参与恢复。** 备份要完整（出了事要能证明当时发生过
  什么），恢复却不能覆盖审计 —— 否则"恢复一份三天前的备份"就能顺手把这三天的
  审计记录抹掉，"日志不可篡改"就成了空话。
* **恢复前先自动备份一次。** 恢复是覆盖动作，做错了就没有回头路；先在旁边留
  一份当前状态（同样命名 FULL-*，备注写"恢复前自动"），比任何确认框都管用。
"""

import hashlib
import json
import os
import shutil
import zipfile
from datetime import datetime

from .audit import audit
from .devices.base import DATA_DIR

BACKUP_DIR = os.path.join(DATA_DIR, "backup")
MANIFEST = "MANIFEST.json"

# 目录名（相对 data/）不打包：备份自身、缓存
SKIP_DIRS = {"backup", "__pycache__"}
# 文件名不打包：启动日志（每次启动都变，进备份只会让包变大）
SKIP_FILES = {"startup.log"}

# 恢复时**跳过**的路径前缀（相对 data/）：审计独立，恢复不覆盖
RESTORE_SKIP = ("audit/", "audit\\")


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            blob = f.read(1024 * 512)
            if not blob:
                break
            h.update(blob)
    return h.hexdigest()


def _iter_files():
    """遍历 data/ 下要进备份的文件，返回 (绝对路径, 相对 data/ 的 posix 路径)。"""
    for root, dirs, files in os.walk(DATA_DIR):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for fn in sorted(files):
            if fn in SKIP_FILES or fn.endswith(".tmp"):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, DATA_DIR).replace(os.sep, "/")
            yield full, rel


def _ensure_dir():
    if not os.path.isdir(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)


def list_backups():
    """按时间倒序返回备份包列表（只认 FULL-*.zip，别把别的文件混进来）。"""
    _ensure_dir()
    out = []
    for fn in os.listdir(BACKUP_DIR):
        if not (fn.startswith("FULL-") and fn.lower().endswith(".zip")):
            continue
        p = os.path.join(BACKUP_DIR, fn)
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({"name": fn, "path": p, "size": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime)
                    .strftime("%Y-%m-%d %H:%M:%S")})
    out.sort(key=lambda r: r["name"], reverse=True)
    return out


def create(user="", note="", kind="manual"):
    """生成一份 FULL 备份。返回 dict：path / name / files / size / seconds。"""
    _ensure_dir()
    t0 = datetime.now()
    stem = "FULL-%s" % t0.strftime("%Y%m%d-%H%M")
    path = os.path.join(BACKUP_DIR, stem + ".zip")
    if os.path.exists(path):      # 同一分钟内又备了一次，加序号，别覆盖已有的
        i = 2
        while os.path.exists(os.path.join(BACKUP_DIR, "%s-%d.zip" % (stem, i))):
            i += 1
        path = os.path.join(BACKUP_DIR, "%s-%d.zip" % (stem, i))

    manifest = {
        "kind": "FULL",
        "created": t0.strftime("%Y-%m-%d %H:%M:%S"),
        "user": user or "系统",
        "note": note or "",
        "source": "manual" if kind == "manual" else kind,
        "files": {},
    }

    count = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for full, rel in _iter_files():
            try:
                with open(full, "rb") as f:
                    blob = f.read()
            except (IOError, OSError):
                continue          # 单个文件被占用（比如正在写的日志）就跳过
            z.writestr(rel, blob)
            manifest["files"][rel] = {"size": len(blob),
                                      "sha256": hashlib.sha256(blob).hexdigest()}
            count += 1
        z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))

    # 包名带 time 前缀，按名字就能排；返回给界面展示
    return {"path": path, "name": os.path.basename(path), "files": count,
            "size": os.path.getsize(path), "seconds": (datetime.now() - t0)
            .total_seconds(), "manifest": manifest}


def read_manifest(path):
    try:
        with zipfile.ZipFile(path, "r") as z:
            return json.loads(z.read(MANIFEST).decode("utf-8"))
    except (KeyError, IOError, OSError, ValueError):
        return None


def verify(path):
    """逐文件比对清单。返回 (ok, 说明, manifest)。

    先看 zip 结构（CRC），再比清单里的 sha256 —— 两样过才叫"完好"。
    """
    if not os.path.exists(path):
        return False, "备份包不存在：%s" % path, None
    try:
        with zipfile.ZipFile(path, "r") as z:
            bad_crc = z.testzip()
            if bad_crc:
                return False, "包内文件校验失败（CRC 不符）：%s" % bad_crc, None
            names = set(z.namelist())
            if MANIFEST not in names:
                return False, "包内没有清单文件，无法确认内容完整性", None
            try:
                manifest = json.loads(z.read(MANIFEST).decode("utf-8"))
            except ValueError:
                return False, "清单文件损坏（不是合法 JSON）", None
            files = manifest.get("files") or {}
            if not files:
                return False, "清单里没有任何文件记录", None
            missing, diff = [], []
            for rel, info in files.items():
                if rel not in names:
                    missing.append(rel)
                    continue
                blob = z.read(rel)
                if len(blob) != info.get("size") or \
                        hashlib.sha256(blob).hexdigest() != info.get("sha256"):
                    diff.append(rel)
            if missing:
                return False, "包内缺 %d 个文件（例：%s）" % (len(missing),
                                                           missing[0]), manifest
            if diff:
                return False, "%d 个文件内容与清单不符（例：%s）" % (len(diff),
                                                                   diff[0]), manifest
            return True, "校验通过：%d 个文件、逐一比对 sha256 一致" % len(files), manifest
    except (zipfile.BadZipFile, IOError, OSError) as e:
        return False, "包打不开：%s" % e, None


def restore(path, user=""):
    """用备份包覆盖 data/（审计目录除外）。

    返回 (ok, 说明, 恢复文件数)。失败不动任何现有文件 —— 全部先在内存里
    校验，通过了才逐个落盘。
    """
    ok, msg, manifest = verify(path)
    if not ok:
        return False, "恢复中止：%s" % msg, 0

    files = manifest.get("files") or {}
    payload = {}
    try:
        with zipfile.ZipFile(path, "r") as z:
            for rel, info in files.items():
                if any(rel.startswith(p) for p in RESTORE_SKIP):
                    continue
                blob = z.read(rel)
                if hashlib.sha256(blob).hexdigest() != info.get("sha256"):
                    return False, "恢复中止：%s 在读取过程中内容不符" % rel, 0
                payload[rel] = blob
    except (zipfile.BadZipFile, KeyError, IOError, OSError) as e:
        return False, "恢复中止：包读取失败（%s）" % e, 0

    # 恢复前先留一份当前状态。这一步失败就整体放弃 —— 没有退路的覆盖不能做。
    try:
        safety = create(user=user, note="恢复前自动备份", kind="pre-restore")
    except Exception as e:
        return False, "恢复中止：无法生成恢复前备份（%s）" % e, 0

    done = 0
    for rel, blob in payload.items():
        target = os.path.join(DATA_DIR, rel.replace("/", os.sep))
        d = os.path.dirname(target)
        try:
            if d and not os.path.isdir(d):
                os.makedirs(d)
            tmp = target + ".rst"
            with open(tmp, "wb") as f:
                f.write(blob)
            if os.path.exists(target):
                os.remove(target)
            os.rename(tmp, target)
            done += 1
        except (IOError, OSError) as e:
            return False, "恢复中断：写入 %s 失败（%s），已恢复 %d 个文件" % (
                rel, e, done), done

    audit.write("恢复备份", target=os.path.basename(path),
                detail="恢复 %d 个文件；恢复前备份 %s；审计目录未覆盖"
                       % (done, os.path.basename(safety["name"])),
                user=user or None)
    return True, "已恢复 %d 个文件（审计日志未覆盖）。恢复前备份：%s" % (
        done, safety["name"]), done


def prune(keep_daily=None, keep_weekly=None):
    """按保留策略清理旧备份，返回被删掉的文件名列表。

    策略：最近 N 个"有备份的日期"各留最新一份；再在剩下的里面，按 ISO 周
    每周留最新一份，最多 M 周。**只删本目录下的 FULL-*.zip**，别的文件一律不碰。
    """
    from .auth import settings
    bk = settings.get("backup") or {}
    keep_daily = int(bk.get("keep_daily", 7) if keep_daily is None else keep_daily)
    keep_weekly = int(bk.get("keep_weekly", 4) if keep_weekly is None else keep_weekly)

    items = list_backups()                      # 已按名字倒序 = 时间倒序
    keep, days, weeks = set(), [], []
    for it in items:
        name = it["name"]
        # FULL-YYYYMMDD-HHMM[-n].zip
        try:
            date_s = name[5:13]
            day = datetime.strptime(date_s, "%Y%m%d").date()
        except ValueError:
            continue
        if len(days) < keep_daily and day not in days:
            days.append(day)
            keep.add(name)
        elif day in days:
            keep.add(name)          # 同一天的多份都留着，反正是同一批
    for it in items:
        if it["name"] in keep:
            continue
        try:
            day = datetime.strptime(it["name"][5:13], "%Y%m%d").date()
        except ValueError:
            continue
        wk = day.isocalendar()[:2]
        if wk not in weeks and len(weeks) < keep_weekly:
            weeks.append(wk)
            keep.add(it["name"])

    removed = []
    for it in items:
        if it["name"] in keep:
            continue
        try:
            os.remove(it["path"])
            removed.append(it["name"])
        except (IOError, OSError):
            pass
    if removed:
        audit.write("清理旧备份", target="备份目录",
                    detail="按 %d 日 + %d 周策略删除 %d 个：%s"
                           % (keep_daily, keep_weekly, len(removed),
                              "、".join(removed[:5]) + ("…" if len(removed) > 5 else "")))
    return removed


def last_backup():
    items = list_backups()
    return items[0] if items else None


def due_auto(now=None):
    """该不该跑自动备份：开关打开、今天还没跑过、已过设定时刻。"""
    from .auth import settings
    bk = settings.get("backup") or {}
    if not bk.get("auto", True):
        return False, "自动备份已关闭"
    now = now or datetime.now()
    last = str(bk.get("last_auto") or "")
    today = now.strftime("%Y-%m-%d")
    if last == today:
        return False, "今日已备份"
    hhmm = str(bk.get("time") or "02:00")
    try:
        h, m = [int(x) for x in hhmm.split(":")]
    except (ValueError, TypeError):
        h, m = 2, 0
    if (now.hour, now.minute) < (h, m):
        return False, "未到 %s" % hhmm
    return True, ""


def run_auto():
    """自动备份入口（由主窗口定时器调用）。返回结果 dict 或 None。"""
    from .auth import settings
    ok, why = due_auto()
    if not ok:
        return None
    try:
        res = create(user="系统", note="每日自动备份", kind="auto")
    except Exception:
        audit.write("自动备份失败", target="备份目录", result="FAIL",
                    detail="生成备份包时出错")
        return None
    settings.update("backup", {"last_auto": datetime.now().strftime("%Y-%m-%d")})
    audit.write("自动备份", target=res["name"], user="系统", role="-",
                detail="%d 个文件，%.1f MB" % (res["files"],
                                              res["size"] / 1024.0 / 1024.0))
    try:
        prune()
    except Exception:
        pass
    return res
