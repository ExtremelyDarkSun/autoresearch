# GENERATED-BY: portable extraction / repository bootstrap (2026-11-09 +08:00)
# LAST-EDIT: portable extraction / repository bootstrap (2026-11-09 +08:00)
# PURPOSE: 读、校验并消费「目标 agent harness」比赛配置（profile.toml）—— 本模块只碰本地文件，不联网、不启动评测
# ORIGIN: none（新文件）。参数面抽取自上游组件 run_eval.py 的 DEFAULTS 与 build_submission_limits 调用点、
#         以及上游组件 gates/gate_tasklist.py 的题单判据；只保留「被优化对象是 agent harness」的通用部分
# CUMULATIVE-DELTA: 首版。评测命令默认空（configured=false）⇒ 从 import 到调用 build_command 都不会发起真评测。

"""目标 agent harness 比赛配置（profile）的加载、校验与命令构造。

三条边界（改本模块前先读）：

1. **只读本地配置。** 模块级导入不读任何项目、不访问网络、不启动进程。
2. **默认不许跑。** `[evaluation].command` 为空且 `configured=false` 时，`build_command()`
   直接报错。想发起真评测必须由人显式改配置。
3. **不许走 shell。** `build_command()` 只返回 argv 列表（`list[str]`），不拼字符串、
   不调 `shell=True`。命令模板里只认两个占位符：`{tasks}` 与 `{output}`。

对外接口（上层集成方的 CLI 只需这四个）：

    load_profile(path) -> dict
    validate_profile(profile) -> list[str]
    build_command(profile, *, tasks, output) -> list[str]
    evaluation_state(profile) -> dict
"""

from __future__ import annotations

import pathlib
import re

try:  # Python 3.11+ 才有 tomllib；本仓库不加第三方依赖
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 只在 <3.11 的解释器上走到
    tomllib = None  # type: ignore[assignment]


class ProfileError(ValueError):
    """profile 不可用（读不到、解析失败、校验不通过、命令未配置）。"""


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

PROFILE_ID = "agent-harness"
PROFILE_VERSION = 1
PROFILE_FILENAME = "profile.toml"

PLACEHOLDER_TASKS = "{tasks}"
PLACEHOLDER_OUTPUT = "{output}"
ALLOWED_PLACEHOLDERS = (PLACEHOLDER_TASKS, PLACEHOLDER_OUTPUT)

#: `[limits]` 只认这些**通用预算名**。写别的键一律判错 ——
#: 这是为了防止把某一场比赛的私有预算字段名（或官方固定值）带进通用配置。
ALLOWED_LIMIT_KEYS = (
    "per_task_minutes",
    "max_tool_calls",
    "max_turns",
    "command_timeout_seconds",
    "total_budget_minutes",
)

#: `[evaluation].configured = true` 时必须显式给全的预算键。
#: 理由：真评测一旦发起就必须带齐时间 / 工具 / 轮次 / 单命令超时语义，
#: 不许靠本模块或 harness 里的隐藏默认值兜底。
REQUIRED_LIMIT_KEYS = (
    "per_task_minutes",
    "max_tool_calls",
    "max_turns",
    "command_timeout_seconds",
)

#: 出现在命令模板里就说明作者以为这里会走 shell ⇒ 直接判错。
SHELL_OPERATOR_ELEMENTS = (";", "&&", "||", "|", ">", ">>", "<", "2>&1", "&")

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]*)\}")

_TRACE_LIST_KEYS = (
    "tool_names",
    "read_tools",
    "edit_tools",
    "command_tools",
)
_WORKSPACE_LIST_KEYS = (
    "allowed_roots",
    "forbidden_path_patterns",
    "ignore_paths",
)
_LEAK_LIST_KEYS = ("sensitive_fragments",)


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------


def load_profile(path) -> dict:
    """读一个 profile 并返回 dict。

    `path` 可以是 `profile.toml` 本身，也可以是**包含** `profile.toml` 的目录
    （例如 `profiles/agent-harness`）。

    返回的 dict 比文件里多两个元数据键（下划线开头，不参与 TOML 校验）：

    * `_profile_path` —— profile 文件的绝对路径
    * `_profile_dir`  —— profile 文件所在目录的绝对路径

    本函数**不做校验**：校验是 `validate_profile()` 的事，两件事分开才能让
    CLI 先打印全部错误再决定要不要跑。
    """
    p = pathlib.Path(path)
    if p.is_dir():
        p = p / PROFILE_FILENAME
    if not p.is_file():
        raise ProfileError(f"找不到 profile 文件：{p}")
    if tomllib is None:  # pragma: no cover
        raise ProfileError(
            "读 TOML 需要 Python ≥ 3.11 的 tomllib；本仓库不引入第三方依赖。"
            f"当前解释器不支持。profile：{p}"
        )
    try:
        raw = p.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ProfileError(f"读不到 profile 文件：{p}：{exc}") from exc
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"profile 不是合法 TOML：{p}：{exc}") from exc
    if not isinstance(data, dict):  # pragma: no cover - tomllib 必返回 dict
        raise ProfileError(f"profile 顶层必须是表（table）：{p}")
    resolved = p.resolve()
    data["_profile_path"] = str(resolved)
    data["_profile_dir"] = str(resolved.parent)
    return data


def profile_dir(profile) -> pathlib.Path | None:
    """profile 所在目录（未知则 None）。"""
    if isinstance(profile, dict) and profile.get("_profile_dir"):
        return pathlib.Path(str(profile["_profile_dir"]))
    return None


def required_instructions(profile) -> list[str]:
    """`[instructions].required` 的原样列表（相对 profile 目录）。

    读取义务由上层集成方合并；本模块只给出相对路径，不改写、不展开。
    """
    if not isinstance(profile, dict):
        return []
    section = profile.get("instructions")
    if not isinstance(section, dict):
        return []
    required = section.get("required")
    if not isinstance(required, list):
        return []
    return [str(x) for x in required]


def resolve_instruction(profile, name: str) -> pathlib.Path | None:
    """把 `[instructions].required` 里的一项解析成绝对路径。

    profile 目录未知时返回 None；文件不存在时**仍返回路径**（存在性由
    `validate_profile()` 报，调用方自己决定要不要读）。
    """
    base = profile_dir(profile)
    if base is None:
        return None
    return base / name


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def validate_profile(profile) -> list[str]:
    """返回错误列表（空列表 = 通过）。

    约定：

    * **不抛异常**，把所有问题一次报全，方便 CLI 一次列清。
    * 只查结构与一致性，不查「能不能跑」；「能不能跑」见 `evaluation_state()`。
    * 未知的**顶层**键不判错（允许扩展）；`[limits]` 里的未知键**判错**。
    """
    errors: list[str] = []

    if not isinstance(profile, dict):
        return ["profile 必须是 dict（用 load_profile() 读出来）"]

    # ---- 顶层 id / version ----
    if "id" not in profile:
        errors.append(f"缺少顶层 id（必须是 '{PROFILE_ID}'）")
    elif profile["id"] != PROFILE_ID:
        errors.append(f"顶层 id 必须是 '{PROFILE_ID}'，实际是 {profile['id']!r}")

    if "version" not in profile:
        errors.append(f"缺少顶层 version（必须是 {PROFILE_VERSION}）")
    elif isinstance(profile["version"], bool) or not isinstance(profile["version"], int):
        errors.append(f"顶层 version 必须是整数 {PROFILE_VERSION}，实际是 {profile['version']!r}")
    elif profile["version"] != PROFILE_VERSION:
        errors.append(f"顶层 version 必须是 {PROFILE_VERSION}，实际是 {profile['version']!r}")

    # ---- [evaluation] ----
    evaluation = profile.get("evaluation")
    if evaluation is None:
        errors.append("缺少 [evaluation] 表")
        evaluation = {}
    elif not isinstance(evaluation, dict):
        errors.append("[evaluation] 必须是表")
        evaluation = {}

    command = evaluation.get("command")
    configured = evaluation.get("configured")

    if command is None:
        errors.append("缺少 [evaluation].command（未配置请写空数组 []）")
        command = []
    elif not isinstance(command, list):
        errors.append("[evaluation].command 必须是字符串数组（argv 模板），不是字符串")
        command = []
    else:
        for index, element in enumerate(command):
            if not isinstance(element, str):
                errors.append(f"[evaluation].command[{index}] 必须是字符串，实际是 {type(element).__name__}")
                continue
            if not element.strip():
                errors.append(f"[evaluation].command[{index}] 不能是空白字符串")
            if element in SHELL_OPERATOR_ELEMENTS:
                errors.append(
                    f"[evaluation].command[{index}] 是 shell 操作符 {element!r}："
                    "本命令模板只产出 argv 数组、不经过 shell，写操作符说明配置方式错了"
                )
            for match in _PLACEHOLDER_RE.finditer(element):
                token = match.group(0)
                if token not in ALLOWED_PLACEHOLDERS:
                    errors.append(
                        f"[evaluation].command[{index}] 有不允许的占位符 {token}；"
                        f"只允许 {PLACEHOLDER_TASKS} 与 {PLACEHOLDER_OUTPUT}"
                    )
        if command:
            joined = " ".join(str(x) for x in command if isinstance(x, str))
            if PLACEHOLDER_TASKS not in joined:
                errors.append(
                    f"[evaluation].command 已配置但没有 {PLACEHOLDER_TASKS} 占位符 ⇒ "
                    "题单路径无处传入，评测会跑成默认全量"
                )
            if PLACEHOLDER_OUTPUT not in joined:
                errors.append(
                    f"[evaluation].command 已配置但没有 {PLACEHOLDER_OUTPUT} 占位符 ⇒ "
                    "结果落点无处指定，读结果时找不到文件"
                )

    if configured is None:
        errors.append("缺少 [evaluation].configured（未配置评测请写 false）")
    elif not isinstance(configured, bool):
        errors.append(f"[evaluation].configured 必须是布尔值，实际是 {configured!r}")
    elif configured is True and not command:
        errors.append("configured=true 但 [evaluation].command 为空 ⇒ 无法发起评测")
    elif configured is False and command:
        errors.append(
            "configured=false 但 [evaluation].command 非空 ⇒ 配置自相矛盾；"
            "要跑就显式置 configured=true"
        )

    result_file = evaluation.get("result_file")
    if result_file is None:
        errors.append("缺少 [evaluation].result_file（结果 JSONL 的文件名）")
    elif not isinstance(result_file, str) or not result_file.strip():
        errors.append("[evaluation].result_file 必须是非空字符串")
    else:
        errors.extend(_relative_path_errors("[evaluation].result_file", result_file))

    trace_dir = evaluation.get("trace_dir")
    if trace_dir is not None:
        if not isinstance(trace_dir, str) or not trace_dir.strip():
            errors.append("[evaluation].trace_dir 必须是非空字符串")
        else:
            errors.extend(_relative_path_errors("[evaluation].trace_dir", trace_dir))

    version_key = evaluation.get("result_schema_version")
    if version_key is not None and (
        isinstance(version_key, bool) or not isinstance(version_key, int) or version_key < 1
    ):
        errors.append("[evaluation].result_schema_version 必须是 ≥1 的整数")

    # ---- [instructions] ----
    instructions = profile.get("instructions")
    if instructions is None:
        errors.append("缺少 [instructions] 表")
    elif not isinstance(instructions, dict):
        errors.append("[instructions] 必须是表")
    else:
        required = instructions.get("required")
        if required is None:
            errors.append("缺少 [instructions].required（必读文件的相对路径数组）")
        elif not isinstance(required, list):
            errors.append("[instructions].required 必须是字符串数组")
        elif not required:
            errors.append("[instructions].required 不能为空：至少要有一份必读规则")
        else:
            base = profile_dir(profile)
            for index, item in enumerate(required):
                if not isinstance(item, str) or not item.strip():
                    errors.append(f"[instructions].required[{index}] 必须是非空字符串")
                    continue
                path_errors = _relative_path_errors(f"[instructions].required[{index}]", item)
                errors.extend(path_errors)
                if path_errors or base is None:
                    continue
                if not (base / item).is_file():
                    errors.append(
                        f"[instructions].required[{index}] 指向的文件不存在：{item}"
                        f"（相对 profile 目录 {base}）"
                    )

    # ---- [limits] ----
    limits = profile.get("limits")
    if limits is None:
        errors.append("缺少 [limits] 表（未配置就留空表，不要写官方固定值）")
    elif not isinstance(limits, dict):
        errors.append("[limits] 必须是表")
    else:
        for key, value in limits.items():
            if key not in ALLOWED_LIMIT_KEYS:
                errors.append(
                    f"[limits].{key} 不是本仓库认可的通用预算名；"
                    f"只允许 {', '.join(ALLOWED_LIMIT_KEYS)}"
                )
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                errors.append(f"[limits].{key} 必须是整数，实际是 {value!r}")
            elif value <= 0:
                errors.append(f"[limits].{key} 必须是正整数，实际是 {value!r}")
        if isinstance(configured, bool) and configured is True:
            missing = [k for k in REQUIRED_LIMIT_KEYS if k not in limits]
            if missing:
                errors.append(
                    "[evaluation].configured=true 但 [limits] 缺少必须显式给出的预算："
                    + ", ".join(missing)
                    + "（真评测不允许靠隐藏默认值兜底）"
                )

    # ---- [schema]（可选）----
    schema = profile.get("schema")
    if schema is not None:
        if not isinstance(schema, dict):
            errors.append("[schema] 必须是表")
        else:
            base = profile_dir(profile)
            for key, value in schema.items():
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"[schema].{key} 必须是相对路径字符串")
                    continue
                path_errors = _relative_path_errors(f"[schema].{key}", value)
                errors.extend(path_errors)
                if path_errors or base is None:
                    continue
                if not (base / value).is_file():
                    errors.append(f"[schema].{key} 指向的文件不存在：{value}")

    # ---- [trace] / [workspace] / [leak]（均可选）----
    errors.extend(_list_section_errors(profile, "trace", _TRACE_LIST_KEYS, ("include_observations",)))
    errors.extend(_list_section_errors(profile, "workspace", _WORKSPACE_LIST_KEYS, ()))
    errors.extend(_list_section_errors(profile, "leak", _LEAK_LIST_KEYS, ("include_observations",)))

    return errors


def _relative_path_errors(label: str, value: str) -> list[str]:
    """相对路径约束：不许绝对路径、不许 `..`、不许空段。"""
    errors: list[str] = []
    if pathlib.PurePosixPath(value).is_absolute() or pathlib.PureWindowsPath(value).is_absolute():
        errors.append(f"{label} 必须是相对路径，实际是绝对路径 {value!r}")
        return errors
    parts = re.split(r"[\\/]+", value)
    if any(part == ".." for part in parts):
        errors.append(f"{label} 不许含 '..'：{value!r}")
    return errors


def _list_section_errors(profile: dict, section: str, list_keys, bool_keys) -> list[str]:
    """校验 `[trace]` / `[workspace]` / `[leak]` 这类「字符串数组 + 布尔」表。"""
    errors: list[str] = []
    table = profile.get(section)
    if table is None:
        return errors
    if not isinstance(table, dict):
        return [f"[{section}] 必须是表"]
    for key in list_keys:
        if key not in table:
            continue
        value = table[key]
        if not isinstance(value, list):
            errors.append(f"[{section}].{key} 必须是字符串数组")
            continue
        for index, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                errors.append(f"[{section}].{key}[{index}] 必须是非空字符串")
    for key in bool_keys:
        if key in table and not isinstance(table[key], bool):
            errors.append(f"[{section}].{key} 必须是布尔值，实际是 {table[key]!r}")
    return errors


# ---------------------------------------------------------------------------
# 状态与命令
# ---------------------------------------------------------------------------


def evaluation_state(profile) -> dict:
    """这份 profile 现在能不能发起真评测。**不抛异常**。

    返回键：

    | 键 | 含义 |
    |---|---|
    | `configured` | `[evaluation].configured` 原值 |
    | `command` | 命令模板（原样） |
    | `can_run` | 校验通过 且 configured=true 且必须有预算齐 |
    | `blocking` | 现在挡住真评测的原因（人话，逐条） |
    """
    errors = validate_profile(profile)
    blocking: list[str] = list(errors)

    evaluation = profile.get("evaluation") if isinstance(profile, dict) else None
    evaluation = evaluation if isinstance(evaluation, dict) else {}
    configured = evaluation.get("configured")
    command = evaluation.get("command") if isinstance(evaluation.get("command"), list) else []

    limits = profile.get("limits") if isinstance(profile, dict) else None
    limits = limits if isinstance(limits, dict) else {}

    if configured is not True:
        blocking.append("[evaluation].configured 不是 true ⇒ 按配置不发起真评测")
    if not command:
        blocking.append("[evaluation].command 为空 ⇒ 没有可执行的评测命令")
    missing = [k for k in REQUIRED_LIMIT_KEYS if k not in limits]
    if missing:
        blocking.append("[limits] 未配置必要预算：" + ", ".join(missing))

    return {
        "configured": configured,
        "command": list(command),
        "limits": dict(limits),
        "can_run": not errors and configured is True and bool(command) and not missing,
        "blocking": blocking,
        "errors": errors,
    }


def build_command(profile, *, tasks: str, output: str) -> list[str]:
    """把命令模板实例化成 argv 数组。

    只用 `str.replace()` 替换 `{tasks}` 与 `{output}`，**不拼 shell 字符串**。
    未配置命令时抛 `ProfileError` 并说明怎么配 —— 这是「默认不跑真评测」那道闸。

    参数
    ----
    tasks : 题单文件路径（字符串，原样进 argv）
    output : 结果输出目录（字符串，原样进 argv）
    """
    state = evaluation_state(profile)
    if not state["can_run"]:
        reason = "；".join(state["blocking"]) or "未知原因"
        profile_id = profile.get("id") if isinstance(profile, dict) else None
        raise ProfileError(
            f"profile {profile_id!r} 不能发起评测：{reason}。"
            "要跑真评测：在 [evaluation] 里写 command 并把 configured 置 true，"
            "同时在 [limits] 里给出 " + ", ".join(REQUIRED_LIMIT_KEYS) + "。"
        )

    for name, value in (("tasks", tasks), ("output", output)):
        if not isinstance(value, str) or not value.strip():
            raise ProfileError(f"build_command() 的 {name} 必须是非空字符串，实际是 {value!r}")
        if "\x00" in value:
            raise ProfileError(f"build_command() 的 {name} 含 NUL 字节")

    argv: list[str] = []
    for element in state["command"]:
        argv.append(element.replace(PLACEHOLDER_TASKS, tasks).replace(PLACEHOLDER_OUTPUT, output))
    return argv


def limit_for(profile, key: str):
    """取一个预算值；未配置返回 None（**不返回默认值**，避免隐藏默认值）。"""
    if key not in ALLOWED_LIMIT_KEYS:
        raise ProfileError(
            f"未知预算名 {key!r}；只允许 {', '.join(ALLOWED_LIMIT_KEYS)}"
        )
    limits = profile.get("limits") if isinstance(profile, dict) else None
    if not isinstance(limits, dict):
        return None
    return limits.get(key)
