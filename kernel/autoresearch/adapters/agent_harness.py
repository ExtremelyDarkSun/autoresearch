# GENERATED-BY: portable extraction / repository bootstrap (2026-11-09 +08:00)
# LAST-EDIT: portable extraction / repository bootstrap (2026-11-09 +08:00)
# PURPOSE: 目标 agent harness 评测结果的读取 / 规范化 / 对比，以及轨迹级的工作区越界与泄漏检查（全部离线、只读输入）
# ORIGIN: none（新文件）。抽取自上游组件 run_eval.py（逐题结果行的字段面与预算字段映射）、
#         trace_judge.py（按 trace 的 tool_calls / observation 取数）、
#         gates/gate_out_of_workspace.py（只判 agent 自己发出的动作）、
#         gates/gate_leak.py（新增行集合对比 + 行数下限）。以上只取「被优化对象是 agent harness」的通用部分。
# CUMULATIVE-DELTA: 首版。状态口径固定为 passed/failed/infra_error/timed_out；infra_error 与 timed_out 永不计入通过。

"""目标 agent harness 的评测结果与轨迹检查（离线）。

本模块只读输入、只写 stdout（由 CLI 决定），不联网、不启动评测、不读凭据。

设计口径（改之前先读）
======================

1. **状态只有四种**：`passed` · `failed` · `infra_error` · `timed_out`。
   环境错误与超时**不是通过、也不是失败**：它们不进 `pass_rate` 的分母。
   ⇒ 想拿 `pass_rate` 说「这个 harness 多强」时，分母是「真正测出来的题」。
2. **非法数据不吞**：重复 `task_id`、缺 `task_id`、未定义状态、非 JSON 行都在
   `strict=True`（默认）下抛 `ResultsError`，把行号一并给出。
3. **对比先查可比性**：题集不同或测量配置不同 ⇒ 判 `不可比`，不产出翻正翻负。
4. **未检查 ≠ 通过**：所有检查函数都带 `checked` 字段；规则为空时
   `verdict = "not_configured"`，不许当成 `clean`。
5. **不写死任何一场比赛的数据**：工具名、路径规则、敏感片段全部由调用方（或
   profile 的 `[trace]` / `[workspace]` / `[leak]`）传入。
"""

from __future__ import annotations

import json
import pathlib
import re

# ---------------------------------------------------------------------------
# 状态与常量
# ---------------------------------------------------------------------------

STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_INFRA_ERROR = "infra_error"
STATUS_TIMED_OUT = "timed_out"

#: 合法状态（顺序即文档顺序）。
VALID_STATUSES = (STATUS_PASSED, STATUS_FAILED, STATUS_INFRA_ERROR, STATUS_TIMED_OUT)

#: 这两种状态表示「这次测量没测出 harness 的能力」，不能算失败、更不能算通过。
UNSCORED_STATUSES = (STATUS_INFRA_ERROR, STATUS_TIMED_OUT)

DEFAULT_RESULT_FILE = "task_results.jsonl"
DEFAULT_TASKLIST_FILE = "tasklist.jsonl"
SIDECAR_NAMES = ("{stem}.meta.json", "run_meta.json")

#: 轨迹里可能承载「文件路径」的参数键（兼容多种 harness 命名）。
PATH_ARG_KEYS = (
    "path",
    "paths",
    "filepath",
    "file_path",
    "file",
    "files",
    "filename",
    "target",
    "target_file",
    "directory",
    "dir",
    "cwd",
)

#: 轨迹里可能承载「命令」的参数键。
COMMAND_ARG_KEYS = ("command", "cmd", "shell", "script", "bash", "sh")

_VERDICT_CLEAN = "clean"
_VERDICT_HIT = "hit"
_VERDICT_NOT_CONFIGURED = "not_configured"

_TOKEN_SPLIT = re.compile(r"[\s'\";|&<>(){}\[\],]+")
_QUOTE_CHARS = "\"'`()[]{}<>,;:"
_DRIVE_RE = re.compile(r"^[A-Za-z]:/")


class ResultsError(ValueError):
    """结果文件不合规（读不到、不是 JSONL、字段非法、题号重复）。"""


# ---------------------------------------------------------------------------
# 路径小工具（跨平台、只做字符串规范化）
# ---------------------------------------------------------------------------


def _norm_path(value: str) -> str:
    """把路径规范化成「正斜杠、无多余分隔符、无尾斜杠」的字符串。

    只做字符串层面的事：**不解析软链接、不访问磁盘**。
    """
    text = str(value).strip().strip(_QUOTE_CHARS)
    text = text.replace("\\", "/")
    text = re.sub(r"/{2,}", "/", text)
    if len(text) > 1:
        text = text.rstrip("/")
    return text


def _is_absolute(path: str) -> bool:
    return path.startswith("/") or bool(_DRIVE_RE.match(path))


def _under_any_root(path: str, roots) -> bool:
    for root in roots:
        base = _norm_path(root)
        if not base:
            continue
        if base == "/":
            if path.startswith("/"):
                return True
            continue
        if path == base or path.startswith(base + "/"):
            return True
    return False


def _matches_pattern(path: str, pattern: str) -> bool:
    """前缀匹配；`re:` 前缀表示正则（只做 search，不要求整串匹配）。"""
    if pattern.startswith("re:"):
        try:
            return re.search(pattern[3:], path) is not None
        except re.error:
            return False
    base = _norm_path(pattern)
    return bool(base) and (path == base or path.startswith(base + "/"))


def _ignored(path: str, ignore_paths) -> bool:
    return any(_matches_pattern(path, item) for item in ignore_paths)


def _paths_in_text(text: str) -> list[str]:
    """从一段命令文本里挑出「像路径」的 token（保持出现顺序、去重）。

    只收含 `/` 或 `\\` 的 token；`--flag=/x/y` 取等号右边的部分。
    """
    found: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN_SPLIT.split(text or ""):
        token = raw.strip(_QUOTE_CHARS)
        if not token:
            continue
        if "=" in token:
            token = token.split("=", 1)[1]
        token = token.strip(_QUOTE_CHARS)
        if not token:
            continue
        if "/" not in token and "\\" not in token:
            continue
        norm = _norm_path(token)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        found.append(norm)
    return found


# ---------------------------------------------------------------------------
# 读题单
# ---------------------------------------------------------------------------


def read_tasklist(path, *, strict=True) -> dict:
    """读一份中性题单 JSONL，并拦住两类会静默毁掉读数的写法。

    每行至少需要 `{"task_id": "..."}`。

    拦什么
    ------
    * **空题单**：0 行 ⇒ 非法。命令行漏了参数时最常见的后果是「悄悄跑成默认
      全量」，而结果看起来正常。
    * **重复题号**：同一题号写两行 ⇒ 非法。轨迹 / 补丁 / 测试输出通常按题号
      命名，后一次会覆盖前一次，于是「同一份题单里取两次读数」其实只拿到一份。

    返回 `{"tasks": [...], "summary": {...}}`；`strict=False` 时把问题记进
    `summary["errors"]` 并跳过坏行（重复题号只保留第一次出现的那行）。
    """
    p = pathlib.Path(path)
    if p.is_dir():
        p = p / DEFAULT_TASKLIST_FILE
    if not p.is_file():
        raise ResultsError(f"找不到题单文件：{p}")
    try:
        text = p.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise ResultsError(f"读不到题单文件：{p}：{exc}") from exc

    tasks: list[dict] = []
    errors: list[dict] = []
    duplicates: list[str] = []
    seen: dict[str, int] = {}

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            if strict:
                raise ResultsError(f"{p} 第 {lineno} 行不是合法 JSON：{exc}") from exc
            errors.append({"line": lineno, "kind": "invalid_json", "detail": str(exc)})
            continue
        if not isinstance(row, dict):
            if strict:
                raise ResultsError(f"{p} 第 {lineno} 行不是 JSON 对象")
            errors.append({"line": lineno, "kind": "not_object", "detail": type(row).__name__})
            continue
        task_id = row.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            if strict:
                raise ResultsError(f"{p} 第 {lineno} 行缺少非空字符串 task_id")
            errors.append({"line": lineno, "kind": "missing_task_id", "detail": repr(task_id)})
            continue
        task_id = task_id.strip()
        if task_id in seen:
            duplicates.append(task_id)
            if strict:
                raise ResultsError(
                    f"{p} 第 {lineno} 行的 task_id {task_id!r} 与第 {seen[task_id]} 行重复；"
                    "轨迹与补丁按题号命名，重复题号会让后一次覆盖前一次"
                )
            errors.append(
                {
                    "line": lineno,
                    "kind": "duplicate_task_id",
                    "detail": f"task_id={task_id!r} 已在第 {seen[task_id]} 行出现",
                }
            )
            continue
        seen[task_id] = lineno
        tasks.append({"task_id": task_id, "line": lineno, "raw": row})

    if not tasks and strict:
        raise ResultsError(
            f"题单是空的（0 行有效题号）：{p}；"
            "空题单通常意味着命令行漏了参数，随后会静默跑成默认全量"
        )

    return {
        "tasks": tasks,
        "summary": {
            "n_tasks": len(tasks),
            "task_ids": [task["task_id"] for task in tasks],
            "duplicates": duplicates,
            "errors": errors,
            "source": str(p),
        },
    }


# ---------------------------------------------------------------------------
# 读结果
# ---------------------------------------------------------------------------


def _resolve_results_file(path, profile) -> pathlib.Path:
    p = pathlib.Path(path)
    if p.is_dir():
        name = DEFAULT_RESULT_FILE
        if isinstance(profile, dict):
            evaluation = profile.get("evaluation")
            if isinstance(evaluation, dict) and isinstance(evaluation.get("result_file"), str):
                name = evaluation["result_file"]
        return p / name
    return p


def _profile_measurement(profile, results_path: pathlib.Path, read_sidecar: bool) -> dict:
    """测量配置指纹。

    两个来源，后者覆盖前者：

    1. profile 的 `[evaluation].result_schema_version` 与 `[limits]`；
    2. 结果文件旁的 sidecar（`<结果名>.meta.json` 或 `run_meta.json`）里的
       `limits` 与 `measurement` 两个可选键。

    sidecar 的用法：跑一次评测就把它当时的真实预算写下去，事后对比才有依据；
    没有 sidecar 时只能拿 profile 现值指纹，两边都不同就会被判「不可比」。
    """
    measurement: dict = {
        "profile_id": None,
        "profile_version": None,
        "result_schema_version": None,
        "limits": {},
    }
    if isinstance(profile, dict):
        measurement["profile_id"] = profile.get("id")
        measurement["profile_version"] = profile.get("version")
        evaluation = profile.get("evaluation")
        if isinstance(evaluation, dict):
            measurement["result_schema_version"] = evaluation.get("result_schema_version")
        limits = profile.get("limits")
        if isinstance(limits, dict):
            measurement["limits"] = dict(limits)

    if not read_sidecar:
        return measurement

    for template in SIDECAR_NAMES:
        candidate = results_path.parent / template.format(stem=results_path.stem)
        if not candidate.is_file():
            continue
        try:
            blob = json.loads(candidate.read_text(encoding="utf-8-sig", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(blob, dict):
            continue
        limits = blob.get("limits")
        if isinstance(limits, dict):
            measurement["limits"] = dict(limits)
        extra = blob.get("measurement")
        if isinstance(extra, dict):
            measurement.update(extra)
        measurement["_sidecar"] = candidate.name
        break
    return measurement


def read_results(path, profile=None, *, strict=True, read_sidecar=True) -> dict:
    """读一份中性结果 JSONL，规范化为 `{"tasks": [...], "summary": {...}}`。

    每行最少需要两个字段：

    ```json
    {"task_id": "demo-001", "status": "passed"}
    ```

    `status` 只能是 `passed` / `failed` / `infra_error` / `timed_out`。
    其余字段原样保留在 `task["raw"]` 里；`duration_seconds` 与 `reason`
    额外抽到顶层方便对比（可选字段，缺了就是 `None`）。

    参数
    ----
    path : 结果文件，或包含它的目录（目录时按 profile 的 `result_file` 取，
           没有 profile 时取 `task_results.jsonl`）。
    profile : 可选。只用来 (a) 从目录里定位结果文件，(b) 生成测量配置指纹。
    strict : `True`（默认）遇到非法行直接抛 `ResultsError`；`False` 则把问题
           记进 `summary["errors"]` 并跳过该行。

    返回
    ----
    `{"tasks": [...], "summary": {...}}`

    `summary["pass_rate"]` 的分母是 `passed + failed`，**不含** infra_error 与
    timed_out。取不到分母时为 `None`（不是 0）。
    """
    results_path = _resolve_results_file(path, profile)
    if not results_path.is_file():
        raise ResultsError(f"找不到结果文件：{results_path}")

    try:
        text = results_path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise ResultsError(f"读不到结果文件：{results_path}：{exc}") from exc

    tasks: list[dict] = []
    errors: list[dict] = []
    notes: list[str] = []
    seen: dict[str, int] = {}

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            problem = {"line": lineno, "kind": "invalid_json", "detail": str(exc)}
            if strict:
                raise ResultsError(
                    f"{results_path} 第 {lineno} 行不是合法 JSON：{exc}"
                ) from exc
            errors.append(problem)
            continue
        if not isinstance(row, dict):
            problem = {"line": lineno, "kind": "not_object", "detail": type(row).__name__}
            if strict:
                raise ResultsError(f"{results_path} 第 {lineno} 行不是 JSON 对象")
            errors.append(problem)
            continue

        task_id = row.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            problem = {"line": lineno, "kind": "missing_task_id", "detail": repr(task_id)}
            if strict:
                raise ResultsError(
                    f"{results_path} 第 {lineno} 行缺少非空字符串 task_id（实际 {task_id!r}）"
                )
            errors.append(problem)
            continue
        task_id = task_id.strip()

        if task_id in seen:
            problem = {
                "line": lineno,
                "kind": "duplicate_task_id",
                "detail": f"task_id={task_id!r} 已在第 {seen[task_id]} 行出现",
            }
            if strict:
                raise ResultsError(
                    f"{results_path} 第 {lineno} 行的 task_id {task_id!r} 与第 "
                    f"{seen[task_id]} 行重复；同一份结果里一个题号只能有一行"
                )
            errors.append(problem)
            continue
        seen[task_id] = lineno

        status = row.get("status")
        if status not in VALID_STATUSES:
            problem = {
                "line": lineno,
                "kind": "invalid_status",
                "detail": f"task_id={task_id!r} status={status!r}；只允许 {', '.join(VALID_STATUSES)}",
            }
            if strict:
                raise ResultsError(
                    f"{results_path} 第 {lineno} 行（task_id={task_id!r}）的 status={status!r} "
                    f"不在允许集合内：{', '.join(VALID_STATUSES)}"
                )
            errors.append(problem)
            continue

        duration = None
        for key in ("duration_seconds", "seconds", "wall_seconds", "elapsed_seconds"):
            value = row.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            duration = float(value)
            break

        reason = None
        for key in ("reason", "error", "message", "detail"):
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                reason = value.strip()
                break

        if status == STATUS_PASSED:
            # 只在**明确的错误字段**非空时才提示：`reason` 在 passed 行上常常是
            # 「为什么通过」，不是错误。别把正常行刷成一堆噪声。
            error_field = row.get("error")
            if isinstance(error_field, str) and error_field.strip():
                notes.append(
                    f"task_id={task_id!r} 声称 passed 但 error 字段非空，"
                    "按状态计通过；原始文本保留在 raw 里供复核"
                )

        tasks.append(
            {
                "task_id": task_id,
                "status": status,
                "passed": status == STATUS_PASSED,
                "scored": status in (STATUS_PASSED, STATUS_FAILED),
                "duration_seconds": duration,
                "reason": reason,
                "line": lineno,
                "raw": row,
            }
        )

    counts = {status: 0 for status in VALID_STATUSES}
    for task in tasks:
        counts[task["status"]] += 1
    n_scored = counts[STATUS_PASSED] + counts[STATUS_FAILED]
    pass_rate = (counts[STATUS_PASSED] / n_scored) if n_scored else None

    summary = {
        "n_tasks": len(tasks),
        "n_passed": counts[STATUS_PASSED],
        "n_failed": counts[STATUS_FAILED],
        "n_infra_error": counts[STATUS_INFRA_ERROR],
        "n_timed_out": counts[STATUS_TIMED_OUT],
        "n_scored": n_scored,
        "n_unscored": counts[STATUS_INFRA_ERROR] + counts[STATUS_TIMED_OUT],
        "pass_rate": None if pass_rate is None else round(pass_rate, 6),
        "pass_rate_basis": "passed / (passed + failed)；infra_error 与 timed_out 不进分母",
        "task_ids": sorted(task["task_id"] for task in tasks),
        "errors": errors,
        "notes": notes,
        "source": str(results_path),
        "measurement": _profile_measurement(profile, results_path, read_sidecar),
    }
    return {"tasks": tasks, "summary": summary}


# ---------------------------------------------------------------------------
# 对比
# ---------------------------------------------------------------------------

_COMPARABLE_VERDICTS = {
    "improved": "改善",
    "regressed": "退步",
    "mixed": "混合（有翻正也有翻负）",
    "flat": "持平",
    "no_data": "无数据",
    "incomparable": "不可比",
}


def _ensure_results(value, profile=None) -> dict:
    if isinstance(value, dict) and "tasks" in value and "summary" in value:
        return value
    if isinstance(value, (str, pathlib.Path)):
        return read_results(value, profile)
    raise ResultsError(
        "compare_results() 的每一侧必须是 read_results() 的返回值或结果文件路径，"
        f"实际是 {type(value).__name__}"
    )


def compare_results(before, after, *, profile=None, require_same_tasks=True,
                    require_same_measurement=True) -> dict:
    """对比两次读数，产出翻正 / 翻负 / 不确定。

    可比性两条**默认硬查**：

    * 题集必须相同（`require_same_tasks=True`）；
    * 测量配置指纹必须相同（`require_same_measurement=True`；指纹见
      `read_results()` 的 `measurement`）。

    任一条不满足 ⇒ `comparable=False`，`gained` / `lost` 为空，`verdict="incomparable"`，
    原因写进 `reasons`。**不产出「看起来像结论」的翻正翻负。**

    翻正翻负只统计两侧都 `scored` 的题；任一侧是 `infra_error` / `timed_out`
    的题落进 `uncertain`，并给出原因。
    """
    left = _ensure_results(before, profile)
    right = _ensure_results(after, profile)

    left_summary = left["summary"]
    right_summary = right["summary"]
    left_ids = set(left_summary["task_ids"])
    right_ids = set(right_summary["task_ids"])
    only_left = sorted(left_ids - right_ids)
    only_right = sorted(right_ids - left_ids)
    task_set_same = not only_left and not only_right

    left_measure = left_summary.get("measurement") or {}
    right_measure = right_summary.get("measurement") or {}
    measurement_same = left_measure == right_measure

    reasons: list[str] = []
    if require_same_tasks and not task_set_same:
        reasons.append(
            "题集不同："
            f"只在 before 有 {len(only_left)} 题{_preview(only_left)}；"
            f"只在 after 有 {len(only_right)} 题{_preview(only_right)}"
        )
    if require_same_measurement and not measurement_same:
        reasons.append("测量配置不同：" + _measurement_diff(left_measure, right_measure))

    comparable = not reasons

    left_by_id = {task["task_id"]: task for task in left["tasks"]}
    right_by_id = {task["task_id"]: task for task in right["tasks"]}

    gained: list[str] = []
    lost: list[str] = []
    kept_pass: list[str] = []
    kept_fail: list[str] = []
    uncertain: list[dict] = []
    paired: list[str] = []

    if comparable:
        for task_id in sorted(left_ids & right_ids):
            a = left_by_id[task_id]
            b = right_by_id[task_id]
            if a["status"] in UNSCORED_STATUSES or b["status"] in UNSCORED_STATUSES:
                uncertain.append(
                    {
                        "task_id": task_id,
                        "before": a["status"],
                        "after": b["status"],
                        "why": "至少一侧是环境错误/超时 ⇒ 不判定翻正翻负",
                    }
                )
                continue
            paired.append(task_id)
            if a["status"] == STATUS_FAILED and b["status"] == STATUS_PASSED:
                gained.append(task_id)
            elif a["status"] == STATUS_PASSED and b["status"] == STATUS_FAILED:
                lost.append(task_id)
            elif a["status"] == STATUS_PASSED and b["status"] == STATUS_PASSED:
                kept_pass.append(task_id)
            else:
                kept_fail.append(task_id)

    # 配对通过率：**两侧同一分母**（只数两侧都计分的题）。
    # 为什么需要它：某一侧被环境错误/超时剔除的题，在「各自分母」下会一侧进、
    # 一侧不进 ⇒ 通过率差里混进了「这题这次算不算计分」。配对口径把这个混入项拿掉。
    paired_pass_before = sum(1 for t in paired if left_by_id[t]["status"] == STATUS_PASSED)
    paired_pass_after = sum(1 for t in paired if right_by_id[t]["status"] == STATUS_PASSED)
    n_paired = len(paired)
    paired_before = (paired_pass_before / n_paired) if n_paired else None
    paired_after = (paired_pass_after / n_paired) if n_paired else None
    paired_delta = (
        round(paired_after - paired_before, 6)
        if isinstance(paired_before, float) and isinstance(paired_after, float)
        else None
    )

    notes: list[str] = []
    if uncertain:
        notes.append(
            f"{len(uncertain)} 题不可判定（环境错误/超时）；它们既不算翻正也不算翻负，"
            "也不进配对通过率"
        )

    left_rate = left_summary.get("pass_rate")
    right_rate = right_summary.get("pass_rate")
    delta = None
    if isinstance(left_rate, (int, float)) and isinstance(right_rate, (int, float)):
        delta = round(float(right_rate) - float(left_rate), 6)

    if not comparable:
        verdict = "incomparable"
    elif not left_ids or not right_ids:
        verdict = "no_data"
    elif gained and lost:
        verdict = "mixed"
    elif gained:
        verdict = "improved"
    elif lost:
        verdict = "regressed"
    else:
        verdict = "flat"

    return {
        "comparable": comparable,
        "verdict": verdict,
        "verdict_zh": _COMPARABLE_VERDICTS[verdict],
        "reasons": reasons,
        "notes": notes,
        "task_set": {
            "same": task_set_same,
            "n_before": len(left_ids),
            "n_after": len(right_ids),
            "only_before": only_left,
            "only_after": only_right,
        },
        "measurement": {
            "same": measurement_same,
            "before": left_measure,
            "after": right_measure,
        },
        "pass_rate": {
            "before": left_rate,
            "after": right_rate,
            "delta": delta,
            "basis": left_summary.get("pass_rate_basis"),
            "paired": {
                "n": n_paired,
                "before": None if paired_before is None else round(paired_before, 6),
                "after": None if paired_after is None else round(paired_after, 6),
                "delta": paired_delta,
                "basis": "只数两侧都计分的题；环境错误/超时两侧都不进分母",
            },
        },
        "n": {
            "before": {
                "passed": left_summary["n_passed"],
                "failed": left_summary["n_failed"],
                "infra_error": left_summary["n_infra_error"],
                "timed_out": left_summary["n_timed_out"],
            },
            "after": {
                "passed": right_summary["n_passed"],
                "failed": right_summary["n_failed"],
                "infra_error": right_summary["n_infra_error"],
                "timed_out": right_summary["n_timed_out"],
            },
        },
        "gained": gained,
        "lost": lost,
        "uncertain": uncertain,
        "unchanged": {"passed": kept_pass, "failed": kept_fail},
        "sources": {
            "before": left_summary.get("source"),
            "after": right_summary.get("source"),
        },
    }


def _preview(items, limit: int = 5) -> str:
    if not items:
        return ""
    shown = ", ".join(str(x) for x in items[:limit])
    return f"（{shown}{' …' if len(items) > limit else ''}）"


def _measurement_diff(left: dict, right: dict) -> str:
    keys = sorted(set(left) | set(right))
    parts = []
    for key in keys:
        a = left.get(key)
        b = right.get(key)
        if a != b:
            parts.append(f"{key}: {a!r} → {b!r}")
    return "；".join(parts) if parts else "两侧指纹不一致"


# ---------------------------------------------------------------------------
# 轨迹：取动作
# ---------------------------------------------------------------------------


def load_trace(path) -> dict:
    """读一份轨迹 JSON。接受 `{"steps": [...]}` 或直接一个步骤数组。"""
    p = pathlib.Path(path)
    if not p.is_file():
        raise ResultsError(f"找不到轨迹文件：{p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResultsError(f"轨迹不是合法 JSON：{p}：{exc}") from exc
    if isinstance(data, list):
        return {"steps": data}
    if not isinstance(data, dict):
        raise ResultsError(f"轨迹顶层必须是对象或数组：{p}")
    if not isinstance(data.get("steps", []), list):
        raise ResultsError(f"轨迹的 steps 必须是数组：{p}")
    return data


def _observation_text(observation) -> str:
    """工具返回内容的文本形态（`content` 可能是 JSON 字符串）。"""
    if observation is None:
        return ""
    if isinstance(observation, str):
        return observation
    if isinstance(observation, dict):
        for key in ("content", "text", "output", "result"):
            value = observation.get(key)
            if isinstance(value, str):
                return value
        try:
            return json.dumps(observation, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):  # pragma: no cover
            return str(observation)
    return str(observation)


def iter_actions(trace: dict, *, path_keys=PATH_ARG_KEYS, command_keys=COMMAND_ARG_KEYS) -> list[dict]:
    """把轨迹展开成「一次工具调用 = 一个动作」的列表。

    每个动作含：`step`（步序号）、`tool`（工具名，兼容 `function_name` / `name`）、
    `arguments`（原样，字符串会尝试解析成对象）、`text`（参数的整体文本，
    用于敏感片段匹配）、`paths`（提取到的路径）、`commands`（提取到的命令）、
    `observation`（该步工具返回内容）。

    **只取 agent 自己发出的调用**，不把工具返回里的路径当成 agent 的动作
    （否则 pytest 回显的 `/usr/lib/python…` 会被误判成越界检索）。
    """
    steps = trace.get("steps") if isinstance(trace, dict) else trace
    if not isinstance(steps, list):
        return []

    actions: list[dict] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        calls = step.get("tool_calls")
        if not isinstance(calls, list) or not calls:
            continue
        observation = _observation_text(step.get("observation"))
        for call in calls:
            if not isinstance(call, dict):
                continue
            tool = call.get("function_name") or call.get("name") or call.get("tool")
            tool = str(tool).strip() if tool is not None else None
            raw_args = call.get("arguments", call.get("args"))
            if isinstance(raw_args, str):
                try:
                    parsed = json.loads(raw_args)
                except json.JSONDecodeError:
                    parsed = raw_args
            else:
                parsed = raw_args
            if isinstance(parsed, str):
                text = parsed
                arg_map = {}
            elif isinstance(parsed, dict):
                arg_map = parsed
                try:
                    text = json.dumps(parsed, ensure_ascii=False, sort_keys=True)
                except (TypeError, ValueError):  # pragma: no cover
                    text = str(parsed)
            else:
                arg_map = {}
                text = "" if parsed is None else str(parsed)

            paths: list[str] = []
            for key in path_keys:
                value = arg_map.get(key)
                if isinstance(value, str):
                    paths.append(_norm_path(value))
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            paths.append(_norm_path(item))

            commands: list[str] = []
            for key in command_keys:
                value = arg_map.get(key)
                if isinstance(value, str) and value.strip():
                    commands.append(value)
            if not commands and isinstance(parsed, str) and parsed.strip():
                commands.append(parsed)

            for command in commands:
                paths.extend(_paths_in_text(command))

            deduped: list[str] = []
            for item in paths:
                if item and item not in deduped:
                    deduped.append(item)

            actions.append(
                {
                    "step": index,
                    "tool": tool,
                    "arguments": parsed,
                    "text": text,
                    "paths": deduped,
                    "commands": commands,
                    "observation": observation,
                }
            )
    return actions


# ---------------------------------------------------------------------------
# 轨迹：工作区越界
# ---------------------------------------------------------------------------


def check_workspace(trace, *, allowed_roots=(), forbidden_path_patterns=(), ignore_paths=(),
                    actions=None) -> dict:
    """判「agent 有没有跑到工作区外面」。

    两条规则，任一命中即违规：

    * **越界**：绝对路径不在任何 `allowed_roots` 之下；
    * **禁区**：路径命中 `forbidden_path_patterns`（前缀，或 `re:` 开头的正则）。

    相对路径不判（没法确定展开位置）；`~` 开头的路径也不判（同上）。
    规则全空 ⇒ `checked=False`、`verdict="not_configured"`，**不当通过**。
    """
    action_list = actions if actions is not None else iter_actions(trace)
    roots = [str(x) for x in allowed_roots or ()]
    forbidden = [str(x) for x in forbidden_path_patterns or ()]
    ignores = [str(x) for x in ignore_paths or ()]
    checked = bool(roots or forbidden)

    violations: list[dict] = []
    for action in action_list:
        for path in action["paths"]:
            if _ignored(path, ignores):
                continue
            hit = next((p for p in forbidden if _matches_pattern(path, p)), None)
            if hit is not None:
                violations.append(
                    {"kind": "forbidden_path", "tool": action["tool"], "step": action["step"],
                     "path": path, "pattern": hit}
                )
                continue
            if not _is_absolute(path):
                continue
            if roots and not _under_any_root(path, roots):
                violations.append(
                    {"kind": "out_of_workspace", "tool": action["tool"], "step": action["step"],
                     "path": path, "allowed_roots": roots}
                )

    counts: dict[str, int] = {}
    for item in violations:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1

    return {
        "checked": checked,
        "verdict": (not checked and _VERDICT_NOT_CONFIGURED)
        or (_VERDICT_HIT if violations else _VERDICT_CLEAN),
        "clean": (None if not checked else not violations),
        "n_actions": len(action_list),
        "allowed_roots": roots,
        "forbidden_path_patterns": forbidden,
        "violations": violations,
        "counts": counts,
    }


# ---------------------------------------------------------------------------
# 轨迹：泄漏
# ---------------------------------------------------------------------------


def _mask_fragment(fragment: str, reveal: bool) -> str:
    if reveal:
        return fragment
    head = fragment[:4]
    return f"{head}…（{len(fragment)} 字，已遮蔽；加 reveal=True 看全量）"


def check_leak(trace, *, sensitive_fragments=(), include_observations=False, ignore_paths=(),
               actions=None, reveal=False) -> dict:
    """判「agent 有没有碰到不该看的材料」。

    判据是**配置进来的敏感片段**（字面量子串）：可以是参考补丁里的独有行、
    隐藏测试的名字、答案文件的路径、内部代号……由调用方决定，本函数不认识
    任何具体比赛的内容。

    默认只在**工具调用参数**里搜；`include_observations=True` 时连工具返回
    内容一起搜（用来判「读到了」而不只是「试图读」）。

    ⚠️ 只搜字面片段 ⇒ 改名/换行/编码绕过的内容搜不出来。这是有意的边界：
    本函数是**读数标注工具**，不是完整性证明。

    规则为空 ⇒ `checked=False`、`verdict="not_configured"`。
    """
    action_list = actions if actions is not None else iter_actions(trace)
    fragments = [str(x) for x in sensitive_fragments or ()]
    ignores = [str(x) for x in ignore_paths or ()]
    checked = bool(fragments)

    violations: list[dict] = []
    for action in action_list:
        haystacks = [("arguments", action["text"])]
        if include_observations and action.get("observation"):
            haystacks.append(("observation", action["observation"]))
        for source, haystack in haystacks:
            if not haystack:
                continue
            # 只有**所有路径**都在忽略名单里时才跳过这次动作；
            # 只要有一个路径不在名单里，就照常搜（宁可多报，不要漏报）。
            if action["paths"] and all(_ignored(path, ignores) for path in action["paths"]):
                continue
            for index, fragment in enumerate(fragments):
                if not fragment:
                    continue
                count = haystack.count(fragment)
                if not count:
                    continue
                violations.append(
                    {
                        "kind": "sensitive_fragment",
                        "source": source,
                        "tool": action["tool"],
                        "step": action["step"],
                        "fragment_index": index,
                        "fragment": _mask_fragment(fragment, reveal),
                        "occurrences": count,
                    }
                )

    return {
        "checked": checked,
        "verdict": (not checked and _VERDICT_NOT_CONFIGURED)
        or (_VERDICT_HIT if violations else _VERDICT_CLEAN),
        "clean": (None if not checked else not violations),
        "n_actions": len(action_list),
        "n_fragments": len(fragments),
        "include_observations": bool(include_observations),
        "violations": violations,
    }


# ---------------------------------------------------------------------------
# 轨迹：合成一次检查（配置来自 profile，可被显式参数覆盖）
# ---------------------------------------------------------------------------


def _section(profile, name: str) -> dict:
    if not isinstance(profile, dict):
        return {}
    section = profile.get(name)
    return section if isinstance(section, dict) else {}


def _pick(explicit, profile, section: str, key: str, default=()):
    """显式参数 > profile 配置 > 默认值。显式参数只认「非空」。"""
    if explicit is not None and explicit != () and explicit != []:
        return explicit
    value = _section(profile, section).get(key)
    if value is None:
        return default
    return value


def check_trace(trace, *, profile=None, tool_names=None, allowed_roots=None,
                forbidden_path_patterns=None, sensitive_fragments=None,
                ignore_paths=None, include_observations=None, reveal=False) -> dict:
    """一次跑完：工具名白名单 + 工作区越界 + 敏感片段。

    配置优先级：**显式参数 > profile 的 `[trace]` / `[workspace]` / `[leak]` > 空**。
    全部为空时 `verdict="not_configured"`（没有任何规则被检查过）。

    工具名白名单的意义：轨迹里出现白名单外的工具名，说明 harness 的实际工具面
    与你以为的不同（或轨迹来自另一份配置）⇒ 这条读数要打问号。
    """
    actions = iter_actions(trace)

    names = _pick(tool_names, profile, "trace", "tool_names", ())
    roots = _pick(allowed_roots, profile, "workspace", "allowed_roots", ())
    forbidden = _pick(forbidden_path_patterns, profile, "workspace", "forbidden_path_patterns", ())
    ignores = _pick(ignore_paths, profile, "workspace", "ignore_paths", ())
    fragments = _pick(sensitive_fragments, profile, "leak", "sensitive_fragments", ())
    include_obs = _pick(include_observations, profile, "leak", "include_observations", False)

    tool_usage: dict[str, int] = {}
    unknown: list[dict] = []
    for action in actions:
        name = action["tool"]
        key = name if name is not None else "(无工具名)"
        tool_usage[key] = tool_usage.get(key, 0) + 1

    if names:
        for action in actions:
            if action["tool"] not in names:
                unknown.append({"tool": action["tool"], "step": action["step"]})

    workspace = check_workspace(
        trace,
        allowed_roots=roots,
        forbidden_path_patterns=forbidden,
        ignore_paths=ignores,
        actions=actions,
    )
    leak = check_leak(
        trace,
        sensitive_fragments=fragments,
        include_observations=bool(include_obs),
        ignore_paths=ignores,
        actions=actions,
        reveal=reveal,
    )

    violations = list(workspace["violations"]) + list(leak["violations"])
    for item in unknown:
        violations.append({"kind": "unknown_tool", **item})

    checked = bool(names) or workspace["checked"] or leak["checked"]
    if not checked:
        verdict = _VERDICT_NOT_CONFIGURED
    elif violations:
        verdict = _VERDICT_HIT
    else:
        verdict = _VERDICT_CLEAN

    return {
        "task_id": trace.get("task_id") if isinstance(trace, dict) else None,
        "n_steps": len(trace.get("steps") or []) if isinstance(trace, dict) else None,
        "n_actions": len(actions),
        "tools": tool_usage,
        "checked": checked,
        "verdict": verdict,
        "clean": None if not checked else not violations,
        "violations": violations,
        "workspace": workspace,
        "leak": leak,
        "tool_name_check": {
            "configured": bool(names),
            "tool_names": [str(x) for x in names or ()],
            "unknown": unknown,
        },
    }


# ---------------------------------------------------------------------------
# 补丁相似度（判「agent 的改动是不是参考补丁本身」）
# ---------------------------------------------------------------------------


def added_lines(patch_text: str) -> tuple[set[str], set[str]]:
    """取补丁的新增行集合与文件集合。

    口径：**保留缩进**（缩进在源码里有语义）、**丢掉空白行**；文件取自 `+++ b/` 头。
    """
    lines: set[str] = set()
    files: set[str] = set()
    for raw in (patch_text or "").splitlines():
        line = raw.rstrip("\r")
        if line.startswith("+++ "):
            name = line[4:].strip()
            if name.startswith("b/"):
                name = name[2:]
            if name and name != "/dev/null":
                files.add(name)
            continue
        if line.startswith("+") and not line.startswith("+++"):
            body = line[1:]
            if body.strip():
                lines.add(body)
    return lines, files


def check_patch_leak(agent_patch: str, reference_patch: str, *, min_lines: int = 30,
                     ratio: float = 0.5) -> dict:
    """把 agent 的补丁与参考补丁做新增行集合比对。

    判据
    ----
    | 情形 | verdict |
    |---|---|
    | 任一侧新增行 < `min_lines` | `not_formed`（行数太少，占比没有意义） |
    | 两侧零独有且都过下限 | `confirmed`（补丁就是参考补丁） |
    | 交集占 agent 新增行 ≥ `ratio` | `suspected` |
    | 其余 | `not_formed` |

    ⚠️ `min_lines` / `ratio` 是**本工具的启发式阈值，不是任何官方规定**；
    默认值可覆盖，引用结论时必须写明这两个数。
    """
    agent_lines, agent_files = added_lines(agent_patch)
    ref_lines, ref_files = added_lines(reference_patch)
    inter = agent_lines & ref_lines
    only_agent = agent_lines - ref_lines
    only_ref = ref_lines - agent_lines
    n_agent, n_ref, n_inter = len(agent_lines), len(ref_lines), len(inter)
    actual_ratio = (n_inter / n_agent) if n_agent else 0.0

    reasons: list[str] = []
    if not agent_lines:
        verdict = "not_formed"
        reasons.append("agent 补丁没有新增行，无从比对")
    elif n_agent < min_lines or n_ref < min_lines:
        verdict = "not_formed"
        reasons.append(
            f"行数低于下限 {min_lines}（agent {n_agent} · 参考 {n_ref}）⇒ 占比没有意义，不判"
        )
    elif not only_agent and not only_ref:
        verdict = "confirmed"
        reasons.append("两侧零独有且都过行数下限 ⇒ 补丁与参考补丁一致")
    elif actual_ratio >= ratio:
        verdict = "suspected"
        reasons.append(
            f"交集占 agent 新增行 {actual_ratio:.0%} ≥ {ratio:.0%}，但两侧仍有独有行"
            f"（agent {len(only_agent)} · 参考 {len(only_ref)}）"
        )
    else:
        verdict = "not_formed"
        reasons.append(f"交集占 agent 新增行 {actual_ratio:.0%}，不构成抄参考补丁")

    return {
        "verdict": verdict,
        "reasons": reasons,
        "agent_lines": n_agent,
        "reference_lines": n_ref,
        "intersection": n_inter,
        "agent_only": len(only_agent),
        "reference_only": len(only_ref),
        "ratio": round(actual_ratio, 4),
        "min_lines": min_lines,
        "ratio_threshold": ratio,
        "files_agent": sorted(agent_files),
        "files_reference": sorted(ref_files),
        "files_same": len(agent_files & ref_files),
        "convention": "新增行去重 · 保留缩进 · 丢空白行；文件取自 +++ b/ 头",
        "sample_agent_only": sorted(only_agent)[:5],
        "sample_reference_only": sorted(only_ref)[:5],
    }
