# -*- coding: utf-8 -*-
"""测试项目与自动测试目录。

一个项目 = 名字 + 保存地址 + 一套配置（测试程序、坐标转换参数）。

保存地址只放自动测试产生的文件：

    D:\\TestProjects\\                 ← 保存地址，在【新建项目】里选
        N.修前.1\\                     ← 每次自动测试一个：板号.检修状态.序号
            相机照片\\
            引脚json\\
            日志\\
            测试结果\\

项目本身记在 data\\devices.json 的 test.projects 里（一个列表），
【打开项目】下拉的就是这份列表里的名字。

为什么不往保存地址里放 project.json：
保存地址可能是一个公共结果目录、共享盘，或者别人机器的盘符 —— 往里塞
配置文件容易被清掉、被同步走。项目列表属于"这台机器装了哪些项目"，
和硬件参数放一起最省事；地址失效了，项目名还在下拉里，重新指过去就行。
"""

import os
import re
from datetime import datetime

from .devices import store

# 每次自动测试一个会话目录，里面固定这四个子目录（名字由用户指定）
SUB_DIRS = ("相机照片", "引脚json", "日志", "测试结果")

# 会话目录名里四个子目录各自装什么，界面提示用
SUB_DIR_DESC = (
    ("相机照片", "每次测试拍到的板图"),
    ("引脚json", "每张图的引脚坐标"),
    ("日志", "运行过程与异常"),
    ("测试结果", "判定结果与汇总"),
)

# 检修状态：就这两个取值。顺序有意义 —— 第一个是默认值（"修前"）。
REPAIRS = ("修前", "修后")

# 会话目录名 = 板号 . 检修状态 . 序号，三段用点隔开（用户 2026-11-09 定）。
#
# 为什么用点而不是横杠：板号是人工编的编码，很可能本身就带 "-"（`PCB-2026-1109-03`），
# 用横杠连线时"哪一段是分隔符"全靠数，容易认错；点不会出现在这种编码里，一眼三段。
NAME_SEP = "."

# 老版本按时间命名的目录格式（`2026-11-05_10-12`）。现在不再产生这种名字，
# 留着只为认得出以前的目录、别把它们漏在"已有几次"之外。
STAMP_FMT = "%Y-%m-%d_%H-%M"

# 板号最长多少字。Windows 单级目录名上限 255，但整条路径还要加上保存地址，
# 32 个字足够放下"批次 + 日期 + 流水号"这类编码，也不会把路径撑爆。
BOARD_MAX = 32

# Windows 文件名里不能出现的字符（含 0x00-0x1F 控制字符）
_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_board(text):
    """把人工输入的板号收拾成能当目录名的字符串；空 / 全是非法字符 → 空串。

    只做"能落盘"这一件事：非法字符换下划线、连续空白收成一个、去掉首尾空格和点
    （Windows 不允许目录名以空格或点结尾）。别的一个字都不动 —— 板号是用户自己
    定的编码规则，程序不该猜着改。
    """
    s = _BAD_CHARS.sub("_", str(text or ""))
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:BOARD_MAX]


# 会话目录名的拆解式：<板号>.<修前|修后>.<序号>
#
# 板号允许含点（用户也说不准自己的编码里有没有），所以**从右边认**：检修状态只有
# 两个固定词、序号是纯数字，前面剩下的全部算板号。贪婪的 `.+` 配固定词正合适 ——
# 板号 `N.修前` 加状态后是 `N.修前.修前.2`，照样能拆回原样。
_NAME_RE = re.compile(
    r"^(?P<board>.+)\.(?P<repair>%s)\.(?P<num>\d+)$"
    % "|".join(re.escape(r) for r in REPAIRS))


def session_name(board, repair, num):
    """拼会话目录名：`N.修前.5`。board 由调用方保证已经过 safe_board。"""
    return "%s%s%s%s%d" % (board, NAME_SEP, repair, NAME_SEP, int(num))


def parse_session_name(name):
    """把目录名拆回 (板号, 检修状态, 序号)；不是这个格式就返回 None。"""
    m = _NAME_RE.match(str(name or ""))
    if m is None:
        return None
    return m.group("board"), m.group("repair"), int(m.group("num"))


def is_session_name(name):
    """判断一个目录名是不是"会话目录"（时间格式），用来在地址里挑出历次测试。

    同一分钟跑第二次会加 `-2` 后缀（`2026-11-05_10-12-2`），所以原名匹配不上时
    再剥一次后缀试。注意不能先剥再试 —— `10-12` 结尾本身就像 `-12` 后缀。
    """
    try:
        datetime.strptime(name, STAMP_FMT)
        return True
    except (ValueError, TypeError):
        pass
    base = re.sub(r"-\d+$", "", name or "")
    if base == name or not base:
        return False
    try:
        datetime.strptime(base, STAMP_FMT)
        return True
    except (ValueError, TypeError):
        return False


def _num(v):
    """配置里的数字转 float；空串、坏值、None 一律返回 None（＝还没设）。"""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):    # NaN / inf 当没设
        return None
    return f


class Project(object):
    """一个测试项目。name 为空表示"还没起名"，root 为空表示"还没选保存地址"。

    active 是这个项目**上次选中的测试程序名** —— 程序本身是全局的（见 app/program.py），
    项目只记"我上次看的是哪一份"，打开项目时据此自动读回来。

    yolo 是这个项目用的**引脚识别模型文件**（.pt / .onnx 的绝对路径）——
    不同板子训出来的模型不一样，所以它跟着项目走，不是全局设置。
    空串表示还没选。

    x_coarse 是自动测试第三阶段用的**三轴 X 轴粗定位**（mm）：程序把板子送到
    视野附近，X 轴先按它移过去，再交给视觉细定位。受设备管理里 X 轴限位约束，
    由界面保证不越界。None = 还没设。

    step_xyz / step_uvw 是探针两组轴（XYZ / UVW）的**步长**（mm），
    探针下压找点、平面找边时按这个量走小步。None = 还没设。

    board 是**板号**：人工输入的板子编码（如 `PCB-2026-1109-03`），回车即存进
    项目配置。它是本次测试目录名的第一段 —— 没有板号就没有目录名，也就不让开跑
    （2026-11-09 起取消"没填板号就按时间命名"的兜底：对不上号的数据不如不产）。

    repair / task 是自动测试页上另外两项：

    * repair —— **检修状态**，`修前` / `修后`。同一块板修前测一遍、修后测一遍，
      数据靠它区分；它是目录名的第二段，同时也是 pins.json 里的一个键。
    * task   —— **任务号**，就是目录名本身（`N.修前.5`）。它不是人填的：板号和
      检修状态定下来之后，按保存地址里**同板号同状态已有目录的最大序号 + 1**
      自动算出来，界面上只读、不能改。pins.json 里也带一份，方便回溯。
    """

    def __init__(self, name="", root="", created="", program=None, calib=None,
                 active="", yolo="", x_coarse=None, step_xyz=None, step_uvw=None,
                 board="", repair="", task=""):
        self.name = (name or "").strip()
        self.root = os.path.abspath(root) if root else ""
        self.created = created or ""
        # program 是老版本存程序的地方，现在程序都搬进全局库，这里只留个空壳
        self.program = program if isinstance(program, dict) else {}
        self.calib = calib if isinstance(calib, dict) else {}
        self.active = str(active or "")
        self.yolo = os.path.abspath(yolo) if yolo else ""
        self.x_coarse = _num(x_coarse)
        self.step_xyz = _num(step_xyz)
        self.step_uvw = _num(step_uvw)
        self.board = str(board or "").strip()
        # 检修状态只有两个值，别的（老配置、手改文件）一律当"修前"
        self.repair = repair if repair in REPAIRS else REPAIRS[0]
        self.task = str(task or "").strip()

    # ------------------------------------------------------------ 状态
    @property
    def opened(self):
        """起了名就算项目存在，能不能写文件再看 root_ok。"""
        return bool(self.name)

    @property
    def root_ok(self):
        """保存地址还在不在（换台机器、拔了移动硬盘就可能不在）。"""
        return bool(self.root) and os.path.isdir(self.root)

    @property
    def yolo_ok(self):
        """yolo 模型文件还在不在（换了机器、模型被挪走都会不在）。"""
        return bool(self.yolo) and os.path.isfile(self.yolo)

    # ------------------------------------------------------------ 序列化
    def to_dict(self):
        # **task 不落盘**：它是按保存地址里现有目录现算出来的（板号.检修状态.序号），
        # 存下来只会变成一条和真实目录对不上的旧值。真正的那一份在目录名和
        # 引脚json 的 "task" 里。老配置里如果有这个键，from_dict 仍然读得进来。
        return {"name": self.name, "root": self.root,
                "created": self.created,
                "program": self.program, "calib": self.calib,
                "active": self.active, "yolo": self.yolo,
                "x_coarse": self.x_coarse,
                "step_xyz": self.step_xyz, "step_uvw": self.step_uvw,
                "board": self.board, "repair": self.repair}

    @classmethod
    def from_dict(cls, d):
        if not isinstance(d, dict):
            return cls()
        return cls(str(d.get("name") or ""), str(d.get("root") or ""),
                   str(d.get("created") or ""),
                   d.get("program"), d.get("calib"),
                   str(d.get("active") or ""),
                   str(d.get("yolo") or ""),
                   d.get("x_coarse"),
                   d.get("step_xyz"), d.get("step_uvw"),
                   str(d.get("board") or ""),
                   str(d.get("repair") or ""),
                   str(d.get("task") or ""))

    # ------------------------------------------------------------ 会话目录
    def _scan(self, root=None):
        """扫一遍保存地址，返回 (同板号同状态的最大序号, 已存在名字的集合)。

        地址不可用就当"一次都没有"：界面能显示任务号 1，真开跑时 ensure_session
        会因为地址不存在报错，不会拿一个算出来的号去建目录。
        """
        board = safe_board(self.board)
        top, names = 0, set()
        root = root if root is not None else self.root
        if not board or not root or not os.path.isdir(root):
            return top, names
        try:
            listing = os.listdir(root)
        except OSError:
            return top, names
        for n in listing:
            got = parse_session_name(n)
            if got is None:
                continue
            names.add(n)
            if got[0] == board and got[1] == self.repair:
                top = max(top, got[2])
        return top, names

    def task_no(self, root=None):
        """本次该用第几号 = 同板号同状态下已有的最大序号 + 1（一次都没跑过就是 1）。

        取 max+1 而不是补空位（用户 2026-11-09 定）：删掉的旧目录号不再复用，
        免得纸质记录 / 已归档的结果和后来那一次撞号。
        """
        return self._scan(root)[0] + 1

    def next_name(self, root=None):
        """本次会建的目录名，如 `N.修前.5`。没填板号 → 空串（也就跑不了）。"""
        board = safe_board(self.board)
        if not board:
            return ""
        top, names = self._scan(root)
        n = top + 1
        # 正常情况 n 就是空的；万一那个名字已经被占（别人手建的、并发跑了一次），
        # 往后顺延，绝不拿一个已有目录当本次目录 —— 那会把旧数据混进来。
        while session_name(board, self.repair, n) in names:
            n += 1
        return session_name(board, self.repair, n)

    def preview_session(self, root=None, when=None):
        """下次自动测试会建的目录完整路径（只算名字，**不创建**），给界面预览用。

        when 是老接口（那时用时间命名），现在不用了，留着只是为了不改调用方。
        """
        root = root if root is not None else self.root
        name = self.next_name(root)
        if not root or not name:
            return ""
        return os.path.join(root, name)

    def ensure_session(self, when=None):
        """自动测试开跑前调一次：建好本次会话目录和四个子目录。返回路径。

        目录名 = `板号.检修状态.序号`（见 `next_name()`）。开跑这一刻重算一次，
        防的是"界面上显示 5、期间别处先建了 5" —— 谁先落盘谁占号，后来者顺延。
        """
        if not self.root:
            raise RuntimeError("项目还没设保存地址")
        # 地址不存在就**不建**：`os.makedirs` 会一路把缺的层级都造出来，盘符换了 /
        # 临时拔了盘的时候会悄悄建出一串空目录，看着像"跑过了"。
        if not os.path.isdir(self.root):
            raise RuntimeError("保存地址不存在：%s" % self.root)
        if not safe_board(self.board):
            raise RuntimeError("还没填板号：目录按「板号.检修状态.序号」命名，"
                               "先在自动测试页填板号并回车")
        top, names = self._scan()
        n = top + 1
        board = safe_board(self.board)
        while True:
            name = session_name(board, self.repair, n)
            p = os.path.join(self.root, name)
            if name not in names and not os.path.isdir(p):
                break
            n += 1
        for sub in (None,) + SUB_DIRS:
            os.makedirs(os.path.join(p, sub) if sub else p, exist_ok=True)
        return p

    @staticmethod
    def _is_session_dir(path):
        """这个目录是不是一次测试的目录。

        名字对得上 `板号.检修状态.序号` 就是；对不上（老的时间命名、用户自己放的
        备份文件夹）再退一步看**里面有没有那四个子目录里的任意一个** —— 只要是我们
        建的会话目录，至少有「相机照片」。别的杂项文件夹不会带这套子目录，不会被
        误算成一次测试。
        """
        name = os.path.basename(path)
        if parse_session_name(name) or is_session_name(name):
            return True
        return any(os.path.isdir(os.path.join(path, s)) for s in SUB_DIRS)

    def sessions(self, root=None):
        """地址下已有的历次测试目录，按名字倒序（最新的在前）。"""
        root = root if root is not None else self.root
        if not root or not os.path.isdir(root):
            return []
        out = []
        try:
            for n in os.listdir(root):
                p = os.path.join(root, n)
                if os.path.isdir(p) and self._is_session_dir(p):
                    out.append(n)
        except OSError:
            return []
        # 按序号**数值**倒序，不是按字符串：字符串排会把 `N.修前.10` 排在
        # `N.修前.2` 前面（"1" < "2"）。老的时间命名没有序号，退回按名字排。
        def _key(n):
            got = parse_session_name(n)
            return (got[0], got[1], got[2]) if got else (n, "", -1)
        out.sort(key=_key, reverse=True)
        return out

    def session_sub(self, session):
        """某个会话目录下的四个子目录（完整路径）。"""
        return [os.path.join(self.root, session, s) for s in SUB_DIRS]


class Registry(object):
    """已保存项目的清单，落在 data\\devices.json 的 test.projects。

    清单是有序列表而不是字典：下拉里的顺序 = 新建的先后，找起来有手感。
    名字作为唯一标识（下拉要按名字选），重名时按"更新覆盖"处理。
    """

    SECTION = "test"

    def __init__(self, cfg):
        self.cfg = cfg
        sec = cfg.get(self.SECTION)
        if not isinstance(sec, dict):
            sec = cfg[self.SECTION] = {}
        raw = sec.get("projects")
        if not isinstance(raw, list):
            raw = sec["projects"] = []
        self.projects = [Project.from_dict(d) for d in raw
                         if isinstance(d, dict) and (d.get("name") or d.get("root"))]
        self._migrate(sec)

    def _migrate(self, sec):
        """旧版把唯一一个项目直接写在 test.root / test.name 上，搬进列表。"""
        root = str(sec.pop("root", "") or "")
        name = str(sec.pop("name", "") or "")
        created = str(sec.pop("created", "") or "")
        if not (root or name):
            return
        if not name:
            name = os.path.basename(root.rstrip("\\/")) or "未命名项目"
        if self.find(name) is None and not any(
                p.root and p.root == (os.path.abspath(root) if root else "")
                for p in self.projects):
            self.projects.append(Project(name, root, created))

    # ------------------------------------------------------------ 查询
    def names(self):
        return [p.name for p in self.projects]

    def find(self, name):
        for p in self.projects:
            if p.name == name:
                return p
        return None

    def is_empty(self):
        return not self.projects

    # ------------------------------------------------------------ 增删改
    def put(self, proj, old_name=None):
        """写入一个项目。old_name 给了就先按旧名把它摘掉（改名用）。"""
        if old_name and old_name != proj.name:
            self.remove(old_name)
        cur = self.find(proj.name)
        if cur is not None:
            cur.root = proj.root
            cur.created = cur.created or proj.created
            cur.program = proj.program
            cur.calib = proj.calib
            cur.active = proj.active
            cur.yolo = proj.yolo
            cur.x_coarse = proj.x_coarse
            cur.step_xyz = proj.step_xyz
            cur.step_uvw = proj.step_uvw
            cur.board = proj.board
            cur.repair = proj.repair
            cur.task = proj.task
            return cur
        self.projects.append(proj)
        return proj

    def remove(self, name):
        self.projects = [p for p in self.projects if p.name != name]

    # ------------------------------------------------------------ 落盘
    def save(self):
        """写回 data\\devices.json。返回 (ok, 说明)。"""
        self.cfg.setdefault(self.SECTION, {})["projects"] = [
            p.to_dict() for p in self.projects]
        try:
            store.save(self.cfg)
            return True, ""
        except (IOError, OSError) as e:
            return False, str(e)
