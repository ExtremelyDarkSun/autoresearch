# GENERATED-BY: 工作区布局路径解析模块（新增），2026-11-09
# LAST-EDIT: 2026-11-09 首版：标记选择中文本地目录；无标记严格旧布局；越界与外部 symlink fail-closed
# PURPOSE: 用 `.autoresearch/workspace-layout.json` 标记把专题、代码工作树、环境与框架计划目录
#          解析成实际路径；没有标记的工作区继续用 topics/worktrees/environments/plans，
#          ⛔ 不靠「中文目录在不在」改变旧运行落点。
# ORIGIN: 用户要求（公开项目模板保持本地中文目录：AutoDL实验/实验分析/topics、
#         AutoDL实验/实验分析/worktrees、AutoDL实验/实验记录/environments、
#         AutoDL实验/框架/state/plans；旧工作区保持兼容；状态库仍 .autoresearch）
# CUMULATIVE-DELTA: 首版。标记 schema=1；四目录 + 可选 topic_doc_pattern；所有路径
#          工作区相对、禁绝对/禁 `..`、解析后必须在工作区内（外部 symlink 不跟随）；
#          标记非法一律抛 LayoutError（fail-closed），⛔ 不静默退回旧布局；
#          `plans` 是框架级计划目录，与专题内 `plans/<plan_id>/` 投影前缀是两个概念。

"""工作区布局（目录落点）的单一解析入口。

四条边界（改本模块前先读）
==========================

1. **标记决定布局，目录存在与否不决定。** 只有 ``.autoresearch/workspace-layout.json``
   存在且合法时才用其中的目录；没有标记的工作区**严格**沿用旧布局
   （``topics`` / ``worktrees`` / ``environments`` / ``plans`` 都在工作区根）。
   ⛔ 不要写「中文目录存在就用中文目录」这类探测：那会静默改变旧工作区的运行落点。
2. **一次解析，所有模块共用。** ``workspace`` / ``execution_target`` / ``workflow`` /
   ``lifecycle`` / ``reporting`` 都从这里拿路径；⛔ 不各自拼 ``topics/<topic>``。
3. **路径必须落在工作区内。** 标记里的目录、专题标识拼出的落点都做真实路径检查
   （``Path.resolve()`` 之后再判 ``is_relative_to(root)``）⇒ 绝对路径、``..`` 与指向
   工作区外的 symlink 一律拒（``LayoutError``）。
4. **状态目录不迁移。** 路由数据库仍是 ``.autoresearch/runtime.sqlite3``，
   标记只影响专题/代码/环境/计划四类目录，不迁旧库、不改旧文件字节。

标记格式（schema=1，UTF-8 JSON，路径用 POSIX 斜杠且为工作区相对路径）::

    {
      "schema": 1,
      "layout": "autodl-实验目录",
      "directories": {
        "topics":       "AutoDL实验/实验分析/topics",
        "worktrees":    "AutoDL实验/实验分析/worktrees",
        "environments": "AutoDL实验/实验记录/environments",
        "plans":        "AutoDL实验/框架/state/plans"
      },
      "topic_doc_pattern": "{topics}/{topic}/TOPIC.md"
    }

``directories`` 的四个键都可省略（缺省即旧布局默认值）；``topic_doc_pattern`` 也可省略，
缺省是 ``{topics}/{topic}/TOPIC.md``（``{topics}`` 由 ``directories.topics`` 代入）。

初始化写标记用 :func:`write_layout_marker` / :func:`default_layout_document`，
⛔ 不要在 init 里再抄一份 schema：这里就是正文。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
import re

__all__ = (
    "CHINESE_LAYOUT_DIRECTORIES",
    "CHINESE_LAYOUT_NAME",
    "DIRECTORY_KEYS",
    "LAYOUT_MARKER_REL",
    "LAYOUT_SCHEMA",
    "LEGACY_LAYOUT_DIRECTORIES",
    "LEGACY_TOPIC_DOC_PATTERN",
    "Layout",
    "LayoutError",
    "default_layout_document",
    "read_layout",
    "resolve_layout",
    "write_layout_marker",
)

#: 标记文件的固定相对路径（工作区内）。
LAYOUT_MARKER_REL = ".autoresearch/workspace-layout.json"

#: 标记 schema 版本；不认识的版本 fail-closed。
LAYOUT_SCHEMA = 1

#: 标记里的目录键（顺序固定，便于回执与测试比对）。
DIRECTORY_KEYS = ("topics", "worktrees", "environments", "plans")

#: 旧布局（无标记工作区）的目录：都在工作区根。
LEGACY_LAYOUT_DIRECTORIES = {
    "topics": "topics",
    "worktrees": "worktrees",
    "environments": "environments",
    "plans": "plans",
}

#: 中文本地目录布局（公开项目模板使用；由 init 写进标记）。
CHINESE_LAYOUT_DIRECTORIES = {
    "topics": "AutoDL实验/实验分析/topics",
    "worktrees": "AutoDL实验/实验分析/worktrees",
    "environments": "AutoDL实验/实验记录/environments",
    "plans": "AutoDL实验/框架/state/plans",
}

#: 中文布局的标记标签（只做人读说明；解析不依赖它）。
CHINESE_LAYOUT_NAME = "autodl-实验目录"

#: 标记缺 ``topic_doc_pattern`` 时的默认模式；``{topics}`` 由 ``directories.topics`` 代入。
DEFAULT_TOPIC_DOC_PATTERN = "{topics}/{topic}/TOPIC.md"

#: 旧布局的专题主文档模式（显式保留，便于回执与兼容说明）。
LEGACY_TOPIC_DOC_PATTERN = "topics/{topic}/TOPIC.md"

#: 标记允许的顶层键与目录键（拼错的键不被静默忽略）。
_MARKER_KEYS = frozenset({"schema", "layout", "directories", "topic_doc_pattern"})

#: 专题标识：与 workspace/reporting 同形（ASCII，避免拼出奇怪路径）。
_TOPIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

#: 不允许当目录的落点（状态目录由 .autoresearch 自己管）。
_STATE_DIRNAME = ".autoresearch"

_ABS_WINDOWS = re.compile(r"^[A-Za-z]:[\\/]")


class LayoutError(ValueError):
    """布局标记非法或路径越界。调用方按输入错误处理（不静默回退旧布局）。"""


# ---------------------------------------------------------------------------
# 路径校验小工具
# ---------------------------------------------------------------------------


def _normalize_relative(value, label):
    """把标记里的目录规范化成工作区相对 POSIX 路径；绝对路径 / `..` / 空值一律拒。"""
    if not isinstance(value, str) or not value.strip():
        raise LayoutError(f"{label} 必须是非空字符串（工作区相对路径）")
    raw = value.strip().replace("\\", "/")
    if raw.startswith("//") or _ABS_WINDOWS.match(raw) or raw.startswith("/"):
        raise LayoutError(f"{label} 必须是工作区相对路径，不接受绝对路径：{value!r}")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts:
        raise LayoutError(f"{label} 不能是空路径或工作区根：{value!r}")
    if ".." in parts:
        raise LayoutError(f"{label} 不许含 '..'（可能越出工作区）：{value!r}")
    if parts[0] == _STATE_DIRNAME:
        raise LayoutError(f"{label} 不能用 {_STATE_DIRNAME} 作为落点：{value!r}")
    return "/".join(parts)


def _resolved_inside(root, relative, label):
    """把工作区相对路径落成真实绝对路径；真实位置必须在工作区内（外部 symlink 会被解析出来）。"""
    target = (root / relative).resolve()
    if target == root or not target.is_relative_to(root):
        raise LayoutError(f"{label} 的真实路径越出工作区：{relative}")
    return target


def _require_topic(topic):
    if not isinstance(topic, str) or topic in (".", "..") or not _TOPIC_ID.match(topic):
        raise LayoutError("非法专题标识：只允许字母、数字与 . _ -（且不许含路径分隔符或 ..）")
    return topic


# ---------------------------------------------------------------------------
# 布局对象
# ---------------------------------------------------------------------------


def _substitute(pattern, topics, topic):
    """只做两处显式替换：``{topics}`` / ``{topic}``。

    ⛔ 不用 ``str.format``：调用方显式传入的 pattern 里若有别的花括号会炸；
    这里的契约是「模式里只认这两个占位」。
    """
    return pattern.replace("{topics}", topics).replace("{topic}", topic)


def _check_pattern(pattern, label):
    if not isinstance(pattern, str) or not pattern.strip():
        raise LayoutError(f"{label} 必须是非空字符串")
    text = pattern.strip().replace("\\", "/")
    if "{topic}" not in text:
        raise LayoutError(f"{label} 必须含 {{topic}} 占位：{pattern!r}")
    if text.startswith("/") or text.startswith("//") or _ABS_WINDOWS.match(text):
        raise LayoutError(f"{label} 必须是工作区相对路径，不接受绝对路径：{pattern!r}")
    if ".." in [part for part in text.split("/") if part not in ("", ".")]:
        raise LayoutError(f"{label} 不许含 '..'：{pattern!r}")
    return text


@dataclass(frozen=True)
class Layout:
    """解析后的工作区布局；所有目录都是**绝对**路径且已核过在工作区内。

    属性：
        root            工作区真实根
        marked          是否有合法标记（False = 严格旧布局）
        layout_name     标记里的标签（无标记时 None）
        marker_path     标记文件绝对路径（可能不存在）
        topics / worktrees / environments / plans   四类目录的**相对** POSIX 路径
        topic_doc_pattern   主文档模式（已代入 {topics} 前的原文）
    """

    root: Path
    marked: bool
    layout_name: str | None
    marker_path: Path
    topics: str
    worktrees: str
    environments: str
    plans: str
    topic_doc_pattern: str

    # -- 目录 ---------------------------------------------------------------

    def directory(self, key):
        if key not in DIRECTORY_KEYS:
            raise LayoutError(f"未知目录键：{key!r}")
        relative = getattr(self, key)
        return _resolved_inside(self.root, relative, f"{key} 目录")

    @property
    def topics_dir(self):
        return self.directory("topics")

    @property
    def worktrees_dir(self):
        return self.directory("worktrees")

    @property
    def environments_dir(self):
        return self.directory("environments")

    @property
    def plans_dir(self):
        return self.directory("plans")

    def topic_dir(self, topic):
        """专题目录：``<topics>/<topic>``。"""
        _require_topic(topic)
        return _resolved_inside(self.root, f"{self.topics}/{topic}", f"专题 {topic} 目录")

    def worktree_dir(self, topic):
        """代码工作树：``<worktrees>/<topic>``。"""
        _require_topic(topic)
        return _resolved_inside(self.root, f"{self.worktrees}/{topic}", f"专题 {topic} 代码工作树")

    def environment_dir(self, topic):
        """独立 Python 环境：``<environments>/<topic>``。"""
        _require_topic(topic)
        return _resolved_inside(self.root, f"{self.environments}/{topic}", f"专题 {topic} 环境目录")

    # -- 文件 ---------------------------------------------------------------

    def topic_doc(self, topic):
        """专题主文档的绝对路径（模式由布局或显式自定义决定）。"""
        _require_topic(topic)
        relative = _substitute(self.topic_doc_pattern, self.topics, topic)
        return _resolved_inside(self.root, relative, f"专题 {topic} 主文档")

    def topic_doc_rel(self, topic):
        """专题主文档的工作区相对 POSIX 路径（回执/消息里用）。"""
        return self.relative(self.topic_doc(topic))

    # -- 相对化 -------------------------------------------------------------

    def relative(self, path):
        """把工作区内的真实路径写成 POSIX 相对路径；越界抛 ``LayoutError``。"""
        target = Path(path).resolve()
        if not target.is_relative_to(self.root):
            raise LayoutError(f"路径不在工作区内：{path}")
        return target.relative_to(self.root).as_posix()

    def directories(self):
        return {key: getattr(self, key) for key in DIRECTORY_KEYS}

    def as_public(self):
        """可 JSON 化的只读视图（回报/诊断用；不含私有绝对路径以外的额外事实）。"""
        return {
            "schema": LAYOUT_SCHEMA,
            "marked": self.marked,
            "layout": self.layout_name,
            "marker": LAYOUT_MARKER_REL if self.marked else None,
            "directories": self.directories(),
            "topic_doc_pattern": self.topic_doc_pattern,
        }


# ---------------------------------------------------------------------------
# 读侧：标记 → 布局
# ---------------------------------------------------------------------------


def _read_marker(root):
    """读标记；不存在返回 None。文件坏 / 不是对象 / 未知键 / schema 不符 ⇒ ``LayoutError``。"""
    path = root / LAYOUT_MARKER_REL
    if path.is_symlink():
        # 标记本身指向别处 ⇒ 不跟：读外部文件等于让外部决定落点。
        raise LayoutError(f"布局标记不接受符号链接：{LAYOUT_MARKER_REL}")
    if not path.is_file():
        return None, path
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise LayoutError(f"布局标记不可读：{LAYOUT_MARKER_REL}（{exc}）") from None
    if not raw.strip():
        raise LayoutError(f"布局标记是空文件：{LAYOUT_MARKER_REL}")
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise LayoutError(f"布局标记不是合法 JSON：{LAYOUT_MARKER_REL}（{exc}）") from None
    if not isinstance(document, dict):
        raise LayoutError(f"布局标记必须是 JSON 对象：{LAYOUT_MARKER_REL}")
    unknown = sorted(set(document) - _MARKER_KEYS)
    if unknown:
        raise LayoutError(f"布局标记有未知键：{unknown}（只接受 {sorted(_MARKER_KEYS)}）")
    schema = document.get("schema")
    if type(schema) is not int or schema != LAYOUT_SCHEMA:
        raise LayoutError(f"布局标记 schema 必须是 {LAYOUT_SCHEMA}，实际 {schema!r}")
    return document, path


def _directories_from(document):
    raw = document.get("directories", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise LayoutError("布局标记 directories 必须是对象")
    unknown = sorted(set(raw) - set(DIRECTORY_KEYS))
    if unknown:
        raise LayoutError(f"布局标记 directories 有未知键：{unknown}")
    directories = {}
    for key in DIRECTORY_KEYS:
        directories[key] = _normalize_relative(raw.get(key, LEGACY_LAYOUT_DIRECTORIES[key]),
                                               f"directories.{key}")
    return directories


def read_layout(workspace):
    """解析工作区布局。标记缺失 ⇒ 严格旧布局；标记非法 ⇒ ``LayoutError``（fail-closed）。"""
    if not isinstance(workspace, (str, os.PathLike)):
        raise LayoutError("workspace 必须是路径")
    root = Path(workspace).resolve()
    document, marker_path = _read_marker(root)
    if document is None:
        directories = dict(LEGACY_LAYOUT_DIRECTORIES)
        pattern = DEFAULT_TOPIC_DOC_PATTERN
        name = None
        marked = False
    else:
        directories = _directories_from(document)
        raw_pattern = document.get("topic_doc_pattern")
        pattern = DEFAULT_TOPIC_DOC_PATTERN if raw_pattern is None \
            else _check_pattern(raw_pattern, "topic_doc_pattern")
        name = document.get("layout")
        if name is not None and not isinstance(name, str):
            raise LayoutError(f"布局标记 layout 必须是字符串或省略，实际 {name!r}")
        marked = True
    # 目录与主文档模式先按真实路径核一遍（外部 symlink / 越界在这里就拒，不等到写盘才发现）。
    layout = Layout(root=root, marked=marked, layout_name=name, marker_path=marker_path,
                    topics=directories["topics"], worktrees=directories["worktrees"],
                    environments=directories["environments"], plans=directories["plans"],
                    topic_doc_pattern=pattern)
    for key in DIRECTORY_KEYS:
        _resolved_inside(root, getattr(layout, key), f"{key} 目录")
    return layout


#: `read_layout` 的同义名（调用方两种都读得懂）。
resolve_layout = read_layout


# ---------------------------------------------------------------------------
# 写侧：init 生成标记（不在 init 里抄 schema）
# ---------------------------------------------------------------------------


def default_layout_document(directories=None, *, layout=CHINESE_LAYOUT_NAME, topic_doc_pattern=None):
    """返回可直接 ``json.dump`` 的标记内容（默认中文本地目录布局）。

    ``directories`` 可只给部分键：给了 dict 时，未给的键沿用**旧布局**默认值
    （⛔ 不把「没写」猜成中文目录）；``directories=None`` 才是整套中文默认值。
    返回的文档**总是**带 ``topic_doc_pattern``（缺省 ``{topics}/{topic}/TOPIC.md``），
    这样标记本身能说明主文档落点，init 不必再抄一份模式。
    生成前先做与读取相同的校验，⛔ 不产出会被自己拒掉的文档。
    """
    supplied = {} if directories is None else dict(directories)
    unknown = sorted(set(supplied) - set(DIRECTORY_KEYS))
    if unknown:
        raise LayoutError(f"directories 有未知键：{unknown}")
    merged = {}
    for key in DIRECTORY_KEYS:
        default = CHINESE_LAYOUT_DIRECTORIES[key] if directories is None \
            else LEGACY_LAYOUT_DIRECTORIES[key]
        merged[key] = _normalize_relative(supplied.get(key, default), f"directories.{key}")
    if layout is not None and not isinstance(layout, str):
        raise LayoutError(f"layout 必须是字符串或 None，实际 {layout!r}")
    document = {"schema": LAYOUT_SCHEMA}
    if layout is not None:
        document["layout"] = layout
    document["directories"] = merged
    document["topic_doc_pattern"] = _check_pattern(
        DEFAULT_TOPIC_DOC_PATTERN if topic_doc_pattern is None else topic_doc_pattern,
        "topic_doc_pattern")
    # 自检：这份文档必须能被 read_layout 接受。
    _directories_from(document)
    return document


def write_layout_marker(workspace, directories=None, *, layout=CHINESE_LAYOUT_NAME,
                        topic_doc_pattern=None):
    """为工作区写布局标记（独占创建：已存在就不覆盖）。

    返回 ``{ok, schema, path, idempotent, directories, topic_doc_pattern}``。
    已存在且内容相同 ⇒ ``idempotent=True``；内容不同 ⇒ 抛 ``LayoutError``
    （⛔ 不静默改一个已有工作区的落点）。
    """
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise LayoutError(f"工作区不存在：{root}")
    if not (root / _STATE_DIRNAME).is_dir():
        raise LayoutError(f"请先建立 {_STATE_DIRNAME} 状态目录，再写布局标记")
    if (root / LAYOUT_MARKER_REL).is_symlink():
        raise LayoutError(f"布局标记不接受符号链接：{LAYOUT_MARKER_REL}")
    document = default_layout_document(directories, layout=layout,
                                       topic_doc_pattern=topic_doc_pattern)
    path = root / LAYOUT_MARKER_REL
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    result = {"ok": True, "schema": LAYOUT_SCHEMA, "path": LAYOUT_MARKER_REL,
              "directories": dict(document["directories"]),
              "topic_doc_pattern": document.get("topic_doc_pattern", DEFAULT_TOPIC_DOC_PATTERN)}
    if path.is_file():
        existing = path.read_text(encoding="utf-8-sig")
        same = existing == text
        if not same:
            # 格式化差异（例如手工重排了键序）不算换布局：按语义比一次；只有真的不同才拒。
            try:
                same = json.loads(existing) == document
            except ValueError:
                same = False
        if same:
            return dict(result, idempotent=True)
        raise LayoutError(
            f"布局标记已存在且内容不同：{LAYOUT_MARKER_REL}（⛔ 不覆盖已有工作区的布局；"
            "要换布局请显式迁移，不要静默改落点）")
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    except FileExistsError:  # 并发：另一个进程刚写完，交给「内容相同就幂等」的判定
        return write_layout_marker(workspace, directories, layout=layout,
                                   topic_doc_pattern=topic_doc_pattern)
    return dict(result, idempotent=False)
