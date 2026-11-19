# -*- coding: utf-8 -*-
"""账户、角色、权限与会话。

单机工具，不走网络：账号、权限矩阵、系统设置全落在 ``data/`` 下，跟着程序
目录走。密码只存 PBKDF2 派生值（每个账号独立盐），注册时填的身份证后 6 位
也**只存派生值** —— 它只是"忘记密码"时的身份凭据，存明文等于把身份证后 6 位
抄在硬盘上。

会话只在内存里
--------------
关掉工具就失效，下次打开必须重新登入（用户明确要求）。所以这里没有"记住我"、
没有 token 落盘、也没有自动续期 —— ``session`` 对象的生命周期就是进程的
生命周期，MainWindow.closeEvent 里再显式登出一次写审计。

权限分两层
----------
* **模块级**：进不进得去某个页面（``MODULE_POINTS``）。操作员没有系统管理的
  ``sys.view``，导航点了会被拦下并写审计。
* **功能点级**：进去之后能不能做某个动作（连接设备、编辑项目、生成备份…）。
  默认矩阵见 ``DEFAULT_MATRIX``，管理员可在「系统管理 → 角色权限」里改，
  改完立即生效（每次都现查矩阵，不缓存会话里的权限副本）。
"""

import copy
import hashlib
import hmac
import json
import os
import re
import threading
from datetime import datetime, timedelta

from .audit import audit
from .devices.base import BASE_DIR, DATA_DIR

AUTH_DIR = os.path.join(DATA_DIR, "auth")
USERS_PATH = os.path.join(AUTH_DIR, "users.json")
ROLES_PATH = os.path.join(AUTH_DIR, "roles.json")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

# PBKDF2 迭代次数。12 万次在本机约 60 ms，登入一次完全无感；再往上加收益很小
# （这是本机文件，不是联网服务，攻击面是"拿到硬盘的人"，迭代次数只防离线暴破）。
PBKDF2_ROUNDS = 120000

ROLES = ("admin", "engineer", "operator")
ROLE_NAME = {"admin": "管理员", "engineer": "工程师", "operator": "操作员"}
ROLE_DESC = {
    "admin": "系统治理：账号、权限、审计、备份与恢复",
    "engineer": "现场配置：设备、程序、视觉与备份生成",
    "operator": "受限查看：查看与执行，不参与任何配置",
}

# ---------------------------------------------------------------- 功能点
# (键, 所属模块, 说明)。顺序即权限矩阵里的显示顺序，也是模块分组顺序。
POINTS = (
    ("home.view", "首页", "查看宣传页与总览"),
    ("dev.view", "设备管理", "查看设备与参数"),
    ("dev.connect", "设备管理", "连接 / 断开设备"),
    ("dev.motion", "设备管理", "运动控制：点动、定位、回零、急停"),
    ("test.view", "测试管理", "查看项目与测试程序"),
    ("test.edit", "测试管理", "新建 / 编辑 / 删除项目与程序"),
    ("auto.view", "自动测试", "查看流程与运行状态"),
    ("auto.run", "自动测试", "启动自动运行"),
    ("auto.edit", "自动测试", "修改流程、保存参数"),
    ("vis.view", "视觉定位", "查看画面与识别结果"),
    ("vis.edit", "视觉定位", "导入模型、调整参数、运行识别"),
    ("vis.kinds", "视觉定位", "类别管理：新增 / 删除引脚类别"),
    ("data.view", "数据管理", "查看测试数据"),
    ("data.export", "数据管理", "导出数据"),
    ("kb.view", "知识库", "查看资料与问答"),
    ("kb.edit", "知识库", "导入 / 删除资料"),
    ("sys.view", "系统管理", "进入系统管理"),
    ("sys.user", "系统管理", "用户管理：创建、禁用、重置密码、删除"),
    ("sys.role", "系统管理", "角色权限矩阵配置"),
    ("sys.audit", "系统管理", "审计日志查看与完整性校验"),
    ("sys.backup", "系统管理", "生成备份、校验备份"),
    ("sys.restore", "系统管理", "从备份恢复"),
    ("sys.setting", "系统管理", "系统设置与策略"),
)

POINT_KEYS = tuple(p[0] for p in POINTS)
POINT_DESC = {p[0]: p[2] for p in POINTS}
POINT_GROUP = {p[0]: p[1] for p in POINTS}

# 左侧导航的 8 个模块分别要哪个功能点。None = 不需要登入（首页）
MODULE_POINTS = {
    "home": None,
    "device": "dev.view",
    "test": "test.view",
    "auto": "auto.view",
    "vision": "vis.view",
    "data": "data.view",
    "kb": "kb.view",
    "system": "sys.view",
}

# 只读功能点：操作员的授权就从这一堆里挑。**必须排掉 sys.view** ——
# 它名字也以 .view 结尾，但"能进系统管理"本身就不是只读权限。
_VIEW = [p for p in POINT_KEYS if p.endswith(".view") and not p.startswith("sys.")]
_ALL = list(POINT_KEYS)

DEFAULT_MATRIX = {
    # 管理员：全开。少给一项就等于给自己挖坑（比如没有 sys.role 就改不回权限）。
    "admin": {k: True for k in _ALL},
    # 工程师：干活的全开，治理类全关。备份只能生成不能恢复 —— 恢复会覆盖
    # 别人的配置，属于治理动作。
    "engineer": {k: (k not in ("sys.user", "sys.role", "sys.audit",
                               "sys.restore")) for k in _ALL},
    # 操作员：只看 + 能跑自动测试。"不参与配置"落到矩阵上就是所有 edit/connect/
    # motion/sys 全是 False，唯一的非 view 权限是 auto.run。
    "operator": {k: (k in _VIEW or k == "auto.run") for k in _ALL},
}

DEFAULT_SETTINGS = {
    "security": {
        "pwd_min": 6,           # 密码最短位数
        "need_digit": True,     # 必须含数字
        "need_letter": False,   # 必须含字母（默认关：现场常用纯数字口令）
        "confirm_sensitive": True,   # 敏感操作二次确认要输密码
        "max_fail": 5,          # 连续失败几次锁定
        "lock_min": 5,          # 锁多少分钟
    },
    "backup": {
        "auto": True,
        "time": "02:00",
        "keep_daily": 7,
        "keep_weekly": 4,
        "last_auto": "",
    },
}


def _now():
    return datetime.now()


def _stamp(dt=None):
    return (dt or _now()).strftime("%Y-%m-%d %H:%M:%S")


def _ensure(path_dir):
    if not os.path.isdir(path_dir):
        os.makedirs(path_dir)


def _write_json(path, data):
    """原子写：先写 .tmp 再替换。半路断电不会留下半个 JSON。"""
    _ensure(os.path.dirname(path))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    if os.path.exists(path):
        os.remove(path)
    os.rename(tmp, path)


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return copy.deepcopy(default)


# ---------------------------------------------------------------- 口令
def make_hash(secret, salt=None):
    """返回 (salt_hex, hash_hex)。同一口令每次的盐都不同。"""
    salt = salt or os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", str(secret).encode("utf-8"),
                             bytes.fromhex(salt), PBKDF2_ROUNDS)
    return salt, dk.hex()


def check_hash(secret, salt, expected):
    """常数时间比对，别用 == （时间差能猜出前缀）。"""
    if not salt or not expected:
        return False
    try:
        _, got = make_hash(secret, salt)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, str(expected))


# ---------------------------------------------------------------- 系统设置
class Settings(object):
    """data/settings.json。读的时候和默认值合并，新增配置项不会因为老文件缺键而崩。"""

    def __init__(self, path=SETTINGS_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.d = self._load()

    def _load(self):
        raw = _read_json(self.path, {})
        out = copy.deepcopy(DEFAULT_SETTINGS)
        if isinstance(raw, dict):
            for sec, vals in raw.items():
                if isinstance(vals, dict) and isinstance(out.get(sec), dict):
                    out[sec].update(vals)
                else:
                    out[sec] = vals
        return out

    def reload(self):
        with self._lock:
            self.d = self._load()
        return self.d

    def get(self, section, key=None, default=None):
        sec = self.d.get(section) or {}
        if key is None:
            return copy.deepcopy(sec)
        return sec.get(key, default)

    def update(self, section, patch):
        with self._lock:
            sec = self.d.setdefault(section, {})
            sec.update(patch)
            _write_json(self.path, self.d)
        return copy.deepcopy(self.d.get(section) or {})

    # 保存设置是治理动作，统一从这里走，方便一次写一条审计
    def save(self, data, user="", detail=""):
        merged = copy.deepcopy(self.d)
        for sec, vals in (data or {}).items():
            if isinstance(vals, dict):
                merged.setdefault(sec, {}).update(vals)
            else:
                merged[sec] = vals
        _write_json(self.path, merged)
        with self._lock:
            self.d = merged
        audit.write("修改设置", target="系统设置", detail=detail or "更新系统设置",
                    user=user or None)
        return copy.deepcopy(merged)


settings = Settings()


def password_error(pwd):
    """按当前策略校验密码。返回空串表示通过，否则是给人看的原因。"""
    pwd = str(pwd or "")
    sec = settings.get("security") or {}
    n = int(sec.get("pwd_min", 6) or 6)
    if len(pwd) < n:
        return "密码至少 %d 位" % n
    if sec.get("need_digit", True) and not re.search(r"\d", pwd):
        return "密码里要有数字"
    if sec.get("need_letter", False) and not re.search(r"[A-Za-z]", pwd):
        return "密码里要有字母"
    return ""


# ---------------------------------------------------------------- 角色矩阵
class RoleStore(object):
    """角色 → {功能点: 是否授权}。存在 data/auth/roles.json。"""

    def __init__(self, path=ROLES_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._m = self._load()

    def _load(self):
        raw = _read_json(self.path, {})
        out = copy.deepcopy(DEFAULT_MATRIX)
        if isinstance(raw, dict):
            for role in ROLES:
                got = raw.get(role)
                if isinstance(got, dict):
                    # 逐点合并：老文件里没有的新功能点回落到默认值，不会变成"未授权"
                    for k in POINT_KEYS:
                        if k in got:
                            out[role][k] = bool(got[k])
        return out

    def matrix(self):
        with self._lock:
            return copy.deepcopy(self._m)

    def role(self, role):
        with self._lock:
            return dict(self._m.get(role) or {})

    def can(self, role, point):
        with self._lock:
            return bool((self._m.get(role) or {}).get(point, False))

    def set_point(self, role, point, value):
        with self._lock:
            if role not in ROLES or point not in POINT_KEYS:
                return False
            self._m.setdefault(role, {})[point] = bool(value)
            return True

    def save(self, user="", detail=""):
        with self._lock:
            _write_json(self.path, self._m)
            snap = copy.deepcopy(self._m)
        audit.write("修改角色权限", target="权限矩阵",
                    detail=detail or "更新角色权限", user=user or None)
        return snap


roles = RoleStore()


# ---------------------------------------------------------------- 用户
class UserStore(object):
    """data/auth/users.json。

    记录字段：name / role / pwd_salt / pwd_hash / id6_salt / id6_hash /
    created / last_login / disabled / fail / lock_until
    """

    def __init__(self, path=USERS_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._users = self._load()

    def _load(self):
        raw = _read_json(self.path, {"version": 1, "users": []})
        users = raw.get("users") if isinstance(raw, dict) else []
        out = []
        for u in users or []:
            if isinstance(u, dict) and u.get("name"):
                out.append(u)
        return out

    def _save(self):
        _write_json(self.path, {"version": 1, "users": self._users})

    # ------------------------------------------------------------ 查询
    def all(self):
        with self._lock:
            return [dict(u) for u in self._users]

    def names(self):
        with self._lock:
            return [u["name"] for u in self._users]

    def get(self, name):
        key = str(name or "").strip().casefold()
        with self._lock:
            for u in self._users:
                if u["name"].casefold() == key:
                    return dict(u)
        return None

    def role_of(self, name):
        u = self.get(name)
        return (u or {}).get("role", "")

    def count(self):
        with self._lock:
            return len(self._users)

    # ------------------------------------------------------------ 写
    def add(self, name, role, id6, pwd):
        """注册。校验失败抛 ValueError，消息直接给用户看。"""
        name = str(name or "").strip()
        if not name:
            raise ValueError("请填用户名")
        if len(name) > 20:
            raise ValueError("用户名最多 20 个字")
        if re.search(r"[\\/:*?\"<>|\s]", name):
            raise ValueError("用户名不能含空格或 \\ / : * ? \" < > |")
        if role not in ROLES:
            raise ValueError("请选择角色权限")
        if not re.match(r"^\d{6}$", str(id6 or "")):
            raise ValueError("身份证后 6 位要是 6 个数字")
        msg = password_error(pwd)
        if msg:
            raise ValueError(msg)
        with self._lock:
            for u in self._users:
                if u["name"].casefold() == name.casefold():
                    raise ValueError("用户名「%s」已存在" % name)
            salt, h = make_hash(pwd)
            s6, h6 = make_hash(id6)
            rec = {
                "name": name,
                "role": role,
                "pwd_salt": salt,
                "pwd_hash": h,
                "id6_salt": s6,
                "id6_hash": h6,
                "created": _stamp(),
                "last_login": "",
                "disabled": False,
                "fail": 0,
                "lock_until": "",
            }
            self._users.append(rec)
            self._save()
            return dict(rec)

    def _mutate(self, name, fn):
        key = str(name or "").strip().casefold()
        with self._lock:
            for u in self._users:
                if u["name"].casefold() == key:
                    fn(u)
                    self._save()
                    return dict(u)
        return None

    def set_password(self, name, pwd):
        msg = password_error(pwd)
        if msg:
            raise ValueError(msg)
        salt, h = make_hash(pwd)
        return self._mutate(name, lambda u: u.update(
            {"pwd_salt": salt, "pwd_hash": h, "fail": 0, "lock_until": ""}))

    def set_role(self, name, role):
        if role not in ROLES:
            raise ValueError("角色不合法")
        return self._mutate(name, lambda u: u.update({"role": role}))

    def set_disabled(self, name, on):
        return self._mutate(name, lambda u: u.update({"disabled": bool(on)}))

    def remove(self, name):
        key = str(name or "").strip().casefold()
        with self._lock:
            for i, u in enumerate(self._users):
                if u["name"].casefold() == key:
                    del self._users[i]
                    self._save()
                    return True
        return False

    def set_last_login(self, name):
        return self._mutate(name, lambda u: u.update(
            {"last_login": _stamp(), "fail": 0, "lock_until": ""}))

    def _bump_fail(self, name):
        sec = settings.get("security") or {}
        limit = int(sec.get("max_fail", 5) or 5)
        lock_min = int(sec.get("lock_min", 5) or 5)

        def fn(u):
            u["fail"] = int(u.get("fail", 0)) + 1
            if u["fail"] >= limit:
                u["lock_until"] = (_now() + timedelta(minutes=lock_min)
                                   ).strftime("%Y-%m-%d %H:%M:%S")
                u["fail"] = 0
        self._mutate(name, fn)

    # ------------------------------------------------------------ 登录
    def check_login(self, name, pwd):
        """返回 (ok, 消息, 用户dict)。消息在失败时是原因，成功时为空串。"""
        u = self.get(name)
        if not u:
            return False, "该用户名未注册", None
        if u.get("disabled"):
            return False, "账号已被禁用，请联系管理员", None
        until = str(u.get("lock_until") or "")
        if until:
            try:
                if _now() < datetime.strptime(until, "%Y-%m-%d %H:%M:%S"):
                    return False, "连续输错已锁定，请 %s 后再试" % until, None
            except ValueError:
                pass
        if not check_hash(pwd, u.get("pwd_salt"), u.get("pwd_hash")):
            self._bump_fail(u["name"])
            left = ""
            sec = settings.get("security") or {}
            nu = self.get(u["name"]) or {}
            if nu.get("lock_until"):
                left = "（已锁定至 %s）" % nu["lock_until"]
            else:
                left = "（再错 %d 次锁定）" % max(
                    1, int(sec.get("max_fail", 5)) - int(nu.get("fail", 0)))
            return False, "密码不对" + left, None
        return True, "", self.set_last_login(u["name"])

    def check_id6(self, name, id6):
        u = self.get(name)
        if not u:
            return False, "该用户名未注册"
        if not re.match(r"^\d{6}$", str(id6 or "")):
            return False, "身份证后 6 位要是 6 个数字"
        if not check_hash(id6, u.get("id6_salt"), u.get("id6_hash")):
            return False, "身份证后 6 位不对"
        return True, ""

    def reset_by_id(self, name, id6, new_pwd):
        ok, msg = self.check_id6(name, id6)
        if not ok:
            return False, msg
        msg = password_error(new_pwd)
        if msg:
            return False, msg
        self.set_password(name, new_pwd)
        return True, ""


users = UserStore()


# ---------------------------------------------------------------- 会话
class Session(object):
    """当前登入状态。只在内存，进程结束即消失。"""

    def __init__(self):
        self._user = None
        self._subs = []

    # -------------------------------------------------------- 订阅
    def subscribe(self, fn):
        """界面注册回调：登入/登出/权限变更后重刷。回调无参数。"""
        if fn not in self._subs:
            self._subs.append(fn)

    def _notify(self):
        for fn in list(self._subs):
            try:
                fn()
            except Exception:
                pass

    # -------------------------------------------------------- 状态
    @property
    def user(self):
        return dict(self._user) if self._user else None

    @property
    def logged_in(self):
        return self._user is not None

    @property
    def name(self):
        return (self._user or {}).get("name", "")

    @property
    def role(self):
        return (self._user or {}).get("role", "")

    @property
    def role_name(self):
        return ROLE_NAME.get(self.role, "未登入")

    def login(self, user):
        """user 必须来自 UserStore。登入成功写审计。"""
        self._user = dict(user or {})
        audit.write("登入", target=self.name,
                    detail="角色：%s" % self.role_name,
                    user=self.name, role=self.role_name)
        self._notify()

    def logout(self, reason="用户退出", quiet=False):
        if not self._user:
            return
        name, rname = self.name, self.role_name
        self._user = None
        if not quiet:
            audit.write("登出", target=name, detail=reason,
                        user=name, role=rname)
        self._notify()

    # -------------------------------------------------------- 权限
    def can(self, point):
        """功能点是否授权。**每次现查矩阵**，权限改完立刻生效。"""
        if not self._user:
            return False
        if not point:
            return True
        return roles.can(self.role, point)

    def can_module(self, key):
        point = MODULE_POINTS.get(key)
        return True if point is None else self.can(point)

    def can_any_sys(self):
        return any(self.can(k) for k in POINT_KEYS if k.startswith("sys."))

    def points(self):
        return [k for k in POINT_KEYS if self.can(k)]


session = Session()


def actor():
    """给 audit 用的"当前操作人"回调。"""
    if session.logged_in:
        return session.name, session.role_name
    return "未登入", "-"


audit.actor_provider = actor


def ensure_dirs():
    """启动时把目录建好。空目录也要在，不然备份脚本扫不到。"""
    for d in (AUTH_DIR, os.path.dirname(SETTINGS_PATH)):
        _ensure(d)


ensure_dirs()
