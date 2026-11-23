# GENERATED-BY: agent implementation, 2026-11-22 +08:00
# LAST-EDIT: 2026-11-22 +08:00
# PURPOSE: 通用评测执行器。填补「仓库不附带 run_eval.py 本体」的缺口（docs/REPRODUCIBILITY.md 的 L6.5）。
# ORIGIN: none（新文件）。接口面参照 profiles/agent-harness/EXTRACTION.md 记录的 argv 契约
#         （--tasks / --out）与 schemas/results.schema.json 的四态结果格式；
#         **没有**复制上游 run_eval.py 的任何代码（上游本体从未存在于本仓库任何历史）。
# CUMULATIVE-DELTA: 首版。零第三方依赖、只用标准库、不联网、不拼 shell。
"""通用评测执行器：读题单 → 逐题执行 → 写四态结果。

## 它解决什么问题

本仓库只从上游 `run_eval.py` 抽了**参数面**（argv 模板 + 预算名 + 默认不跑），
**没有真正去跑评测的程序**。所以哪怕凭据、数据、题单全齐，最后一步也没东西可执行。

本文件就是那个缺失的执行体，但刻意做成**通用**的：

- 它不知道某场比赛怎么判分，只负责「按题跑命令、按退出码归类、写合规结果」；
- 「怎么判分」由 `--task-cmd` 给出的命令决定，于是同一个执行器能跑任何 harness；
- 结果严格按 `profiles/agent-harness/schemas/results.schema.json` 写，
  `read_results()` 可以直接读。

## 用法

```powershell
# 每题跑同一个评测器，用占位符传参（推荐）
python tools/run_eval.py --tasks tasks.jsonl --out runs/r1 ^
    --task-cmd '["python","-X","utf8","-B","my_evaluator.py","--task","{task_id}","--repo","{repo}","--out","{out}"]'

# 每题从题单自带的 command 字段读（题单里每行写 "command": [...]）
python tools/run_eval.py --tasks tasks.jsonl --out runs/r1 --per-task-command

# 只校验结构、不执行
python tools/run_eval.py --tasks tasks.jsonl --out runs/r1 --dry-run

# 自检：不需要任何真实题单，用合成题自证契约
python tools/run_eval.py --self-test
```

## 占位符

`{task_id}` `{repo}` `{base_commit}` `{workspace}` `{out}` `{out_root}`

其中 `{workspace}` 是给这道题准备的独立目录（默认 `<out>/workspaces/<task_id>`），
`{out}` 是这道题自己的输出目录（`<out>/tasks/<task_id>`）。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 全部题跑完（**不代表全通过**；通过率看结果文件） |
| 2 | 参数或环境错误（没跑任何题） |
| 3 | 题单结构非法（缺 `task_id`、重复、不是 JSONL） |

## 边界（刻意不做的）

- 不联网、不装包、不 clone 仓库（`{repo}` 只是字符串，交给你的命令去用）
- 不拼 shell：`--task-cmd` 必须是 JSON 数组，逐元素替换后直接 `subprocess.run(list)`
- 不伪造读数：`--dry-run` 产出的计划文件里 `executed=false`，不给任何 passed
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

STATUSES = ("passed", "failed", "infra_error", "timed_out")
PLACEHOLDERS = ("task_id", "repo", "base_commit", "workspace", "out", "out_root")

#: 本执行器写出的结果行格式版本。改了结果行的字段语义就要 +1，并同步
#: `profiles/agent-harness/schemas/results.schema.json` 与 profile 的
#: `[evaluation].result_schema_version`。它会落进 run_meta.json，供读取端核对。
RESULT_SCHEMA_VERSION = 1

#: 单题超时的合法上限（24 小时）。超过它基本一定是误填。
MAX_TIMEOUT_SECONDS = 86400.0


def positive_finite(value: str) -> float:
    """argparse 的 type=：只接受 (0, MAX_TIMEOUT_SECONDS] 内的有限数。

    为什么要卡死：`--timeout nan` / `inf` / `0` / `-1` 会让 `subprocess.run(timeout=...)`
    在内部线程 join 时抛非预期异常（L41 实测 `--timeout nan` 直接 Traceback、exit 1），
    把「参数填错」伪装成「执行器崩了」。这里在解析阶段就挡掉。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"--timeout 必须是数字，收到 {value!r}")
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError(f"--timeout 必须是有限数，收到 {value!r}")
    if number <= 0:
        raise argparse.ArgumentTypeError(f"--timeout 必须大于 0，收到 {value!r}")
    if number > MAX_TIMEOUT_SECONDS:
        raise argparse.ArgumentTypeError(
            f"--timeout 不得超过 {MAX_TIMEOUT_SECONDS:g} 秒（24 小时），收到 {value!r}")
    return number


def safe_task_id(task_id: str) -> bool:
    """题号会被拼进目录名，必须拒绝路径穿越与 Windows 保留名。"""
    if not task_id or not task_id.strip():
        return False
    bad = set('<>:"/\\|?*')
    if any(ch in bad for ch in task_id):
        return False
    if task_id in (".", "..") or task_id.startswith("../") or task_id.startswith("..\\"):
        return False
    stem = task_id.split(".")[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL"} or stem.startswith(("COM", "LPT")):
        return False
    if task_id.endswith((" ", ".")):
        return False
    return True


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 题单
# ---------------------------------------------------------------------------

def read_tasklist(path: Path) -> tuple[list[dict], list[str]]:
    """读题单，返回 (tasks, problems)。problems 非空即判非法。"""
    problems: list[str] = []
    tasks: list[dict] = []

    if not path.is_file():
        return [], [f"题单不存在：{path}"]

    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        return [], [f"读不到题单：{path}：{exc}"]

    seen: dict[str, int] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"第 {lineno} 行不是合法 JSON：{exc}")
            continue
        if not isinstance(row, dict):
            problems.append(f"第 {lineno} 行不是对象")
            continue
        task_id = row.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            problems.append(f"第 {lineno} 行缺 task_id 或为空")
            continue
        task_id = task_id.strip()
        if not safe_task_id(task_id):
            problems.append(
                f"第 {lineno} 行 task_id 不能当目录名（含路径分隔符、保留名或以空格/点结尾）：{task_id!r}")
            continue
        if task_id in seen:
            problems.append(f"第 {lineno} 行 task_id 重复：{task_id}（首次出现在第 {seen[task_id]} 行）")
            continue
        seen[task_id] = lineno
        row["task_id"] = task_id
        tasks.append(row)

    if not tasks and not problems:
        problems.append("题单里没有任何有效行")
    return tasks, problems


# ---------------------------------------------------------------------------
# 命令解析
# ---------------------------------------------------------------------------

def parse_cmd_template(raw: str) -> list[str]:
    """把 --task-cmd 解析成 argv 列表。只接受 JSON 数组，避免引号被 shell 吃掉。"""
    raw = (raw or "").strip()
    if not raw:
        raise SystemExit("--task-cmd 不能为空")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"--task-cmd 必须是 JSON 数组字符串，例如 '[\"python\",\"eval.py\",\"{{task_id}}\"]'；"
            f"解析失败：{exc}"
        ) from exc
    if not isinstance(value, list) or not value or not all(isinstance(x, str) for x in value):
        raise SystemExit("--task-cmd 必须是非空字符串数组")
    return value


def render(argv: list[str], mapping: dict[str, str]) -> list[str]:
    """逐元素替换占位符。不拼 shell，不重新分词。"""
    out: list[str] = []
    for piece in argv:
        text = piece
        for key in PLACEHOLDERS:
            text = text.replace("{" + key + "}", mapping.get(key, ""))
        out.append(text)
    return out


#: 只能从题单行里取值的占位符。缺了它们就是**缺字段**，不能静默换成空串。
FROM_TASKLIST = ("repo", "base_commit")

#: 模板里「像占位符」的记号：`{名字}`，名字只由小写字母和下划线组成。
#: 刻意不用 `\{[^}]*\}` —— `python -c 'print({"a": 1})'` 这种正经花括号不能被误判。
PLACEHOLDER_TOKEN = re.compile(r"\{([a-z_]+)\}")


def used_placeholders(argv: list[str]) -> list[str]:
    found: list[str] = []
    for piece in argv:
        for name in PLACEHOLDER_TOKEN.findall(piece):
            if name in PLACEHOLDERS and name not in found:
                found.append(name)
    return found


def unknown_placeholders(argv: list[str]) -> list[str]:
    """模板里出现了不认识的 `{名字}` —— 十有八九是把 `{task_id}` 拼错了。

    这类错误会**原样传给执行器**（`{task_idd}` 变成一个普通参数），
    执行器可能照常退出 0，于是「一条谁都没执行的命令」被记成通过。
    """
    found: list[str] = []
    for piece in argv:
        for name in PLACEHOLDER_TOKEN.findall(piece):
            token = "{" + name + "}"
            if name not in PLACEHOLDERS and token not in found:
                found.append(token)
    return found


def missing_tasklist_fields(argv: list[str], task: dict) -> list[str]:
    """模板用到了、但题单这行没给的字段（空白值等同于没给）。"""
    missing: list[str] = []
    for key in used_placeholders(argv):
        if key not in FROM_TASKLIST:
            continue
        value = task.get(key)
        if not isinstance(value, str) or not value.strip():
            missing.append(key)
    return missing


def validate_render_inputs(raw: list[str], task: dict | None = None) -> str | None:
    """渲染前的唯一一处判断：这条命令**能不能**拿去跑。

    返回 `None` 表示可以渲染；否则返回一句能直接写进 `reason` 的原因。

    为什么要有这个函数：`--task-cmd` 模板模式与 `--per-task-command` 模式是**两条路**，
    而「空串当缺省」这个毛病两条路都有。L72 我只堵了模板那条，副机 L73 立刻用
    per-task 模式复现出同样三条：缺 `base_commit` → `--commit ''`、`{task_idd}` 原样传下去，
    全部 `ran=True / status=passed / exit=0`。

    **一个判据只修一条分支，等于没修** —— 所以现在两条路都先过这里，不允许「拼上空串就执行」。
    """
    unknown = unknown_placeholders(raw)
    if unknown:
        available = "、".join("{" + k + "}" for k in PLACEHOLDERS)
        return (
            f"命令里有不认识的占位符 {'、'.join(unknown)}；"
            f"可用的有：{available}"
        )
    if task is not None:
        missing = missing_tasklist_fields(raw, task)
        if missing:
            return (
                f"题单这一行缺字段 {'、'.join(missing)}，而命令用到了它们；"
                "不能拿空串去执行（会静默测到别的东西）"
            )
    return None


# ---------------------------------------------------------------------------
# 单题执行
# ---------------------------------------------------------------------------

def _reason_from_stdout(stdout: str | None, limit: int = 200) -> str | None:
    """从执行器的 stdout 里取它自己写的失败原因。

    约定：整段 stdout 能被 `json.loads` 解析，且是对象、带非空字符串 `reason`。
    不满足就返回 None —— **不猜、不解析半行 JSON**，因为把半截 JSON 当原因读，
    比「退出码 1」更误导人。
    """
    text = (stdout or "").strip()
    if not text or not text.startswith("{"):
        return None
    try:
        payload = json.loads(text.splitlines()[-1]) if "\n" in text else json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return None
    reason = reason.strip().replace("\n", " ⏎ ")
    return reason[:limit]


def run_one(task: dict, argv: list[str], *, cwd: Path | None, timeout: float) -> dict:
    """跑一道题，返回符合 schema 的结果行。任何异常都归入四态，不向外抛。"""
    started = time.monotonic()
    base = {
        "task_id": task["task_id"],
        "reason": None,
        "duration_seconds": None,
        "notes": None,
    }
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        base["status"] = "timed_out"
        base["reason"] = f"超过单题预算 {timeout:g} 秒被中断"
        base["duration_seconds"] = round(time.monotonic() - started, 3)
        return base
    except OSError as exc:
        base["status"] = "infra_error"
        base["reason"] = f"{type(exc).__name__}: {exc}"
        base["duration_seconds"] = round(time.monotonic() - started, 3)
        return base

    elapsed = round(time.monotonic() - started, 3)
    base["duration_seconds"] = elapsed
    base["status"] = "passed" if proc.returncode == 0 else "failed"
    if proc.returncode != 0:
        base["reason"] = f"退出码 {proc.returncode}"
        # 执行器如果按约定在 stdout 打了一行 JSON（含 `reason`），就把它提上来。
        #
        # 为什么要做：`退出码 1` 这个词里装了两件事 —— 「3 条测试挂了」和「进程被杀 / 装包失败」
        # 都是非零退出。四态分得清（0/1/其他），但 `reason` 字段对读的人没有信息量；
        # 详细内容只在 `notes` 的 400 字尾串里，而且被截断。
        # 这里把执行器**自己给出的**原因提到 reason，尾串照旧留在 notes —— 一个字段一个职责。
        own = _reason_from_stdout(proc.stdout)
        if own:
            base["reason"] = f"退出码 {proc.returncode}：{own}"
    tail_out = (proc.stdout or "").strip()[-400:]
    tail_err = (proc.stderr or "").strip()[-400:]
    bits = []
    if tail_out:
        bits.append("stdout尾: " + tail_out.replace("\n", " ⏎ "))
    if tail_err:
        bits.append("stderr尾: " + tail_err.replace("\n", " ⏎ "))
    if bits:
        base["notes"] = " ｜ ".join(bits)
    return base


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_tasks(
    tasks: list[dict],
    *,
    out_root: Path,
    argv_template: list[str] | None,
    per_task_command: bool,
    timeout: float,
    dry_run: bool,
) -> dict:
    out_root.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    plan: list[dict] = []

    for task in tasks:
        task_id = task["task_id"]
        workspace = out_root / "workspaces" / task_id
        task_out = out_root / "tasks" / task_id
        mapping = {
            "task_id": task_id,
            "repo": str(task.get("repo") or ""),
            "base_commit": str(task.get("base_commit") or ""),
            "workspace": str(workspace),
            "out": str(task_out),
            "out_root": str(out_root),
        }

        if per_task_command:
            raw_cmd = task.get("command")
            if not isinstance(raw_cmd, list) or not raw_cmd or not all(isinstance(x, str) for x in raw_cmd):
                results.append({
                    "task_id": task_id,
                    "status": "infra_error",
                    "reason": "题单这一行没有合法的 command 数组（要求字符串数组）",
                    "duration_seconds": None,
                    "notes": None,
                })
                continue
            # per-task 模式也是「渲染」：同样会吃到缺字段与拼错的占位符，
            # 所以同样要先过 validate_render_inputs —— 不做的话，这条分支就是
            # 「模板分支已修、另一条路上还在拼空串」（副机 L73 实测复现）。
            problem = validate_render_inputs(raw_cmd, task)
            if problem:
                results.append({
                    "task_id": task_id,
                    "status": "infra_error",
                    "reason": problem,
                    "duration_seconds": None,
                    "notes": None,
                })
                continue
            argv = render(raw_cmd, mapping)
        else:
            # 模板用到了 `{repo}` / `{base_commit}`，而题单这行没给 ⇒ 不跑。
            #
            # 早先的实现会把缺的字段换成空串，命令照常执行、执行器照常退出 0，
            # 于是「在默认分支上跑了一遍」被记成「在指定 commit 上跑过」。
            # 这是比崩溃更难发现的一种错：**读数有值，但它测的是另一份代码。**
            problem = validate_render_inputs(argv_template or [], task)
            if problem:
                results.append({
                    "task_id": task_id,
                    "status": "infra_error",
                    "reason": problem,
                    "duration_seconds": None,
                    "notes": None,
                })
                continue
            argv = render(argv_template or [], mapping)

        entry = {"task_id": task_id, "argv": argv, "workspace": str(workspace), "out": str(task_out)}
        plan.append(entry)

        if dry_run:
            continue

        try:
            workspace.mkdir(parents=True, exist_ok=True)
            task_out.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            results.append({
                "task_id": task_id,
                "status": "infra_error",
                "reason": f"准备目录失败：{type(exc).__name__}: {exc}",
                "duration_seconds": None,
                "notes": None,
            })
            continue

        results.append(run_one(task, argv, cwd=task_out, timeout=timeout))

    # dry-run 刻意**不写** task_results.jsonl：写一个 0 字节的结果文件会让
    # 「只看文件是否存在」的上层误以为跑过了（L41 实测踩到）。计划另存 run_plan.json。
    if not dry_run:
        write_jsonl(out_root / "task_results.jsonl", results)

    meta = {
        "generated_at": now_iso(),
        "executed": not dry_run,
        "tasks_total": len(tasks),
        "tasks_run": len(results),
        # 输入题单的题号（排序去重）—— 写给下游做对齐判定。
        #
        # 为什么必须落在 run_meta 里：`read_results()` 只拿到结果文件，看不到题单；
        # 于是「题单 2 题、结果 1 题」在**直读路径**上会被算成 pass_rate=1.0
        # （副机 L58 实测：直接对 task_results.jsonl 调 read_results 仍得 1.0）。
        # 把题单快照写进 sidecar，读取端才有依据判「这份结果覆盖不全」。
        "tasklist_task_ids": sorted({str(t["task_id"]) for t in tasks}),
        # 这份结果文件是按第几版 schema 写的。读取端拿它和 profile 的
        # `result_schema_version` 对一下：不一致就说明「配置写着 v2、文件其实是 v1」，
        # 那种情况下的两次读数不该被当成同格式直接比较。
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "limits": {"per_task_timeout_seconds": timeout},
        "measurement": {
            "executor": "tools/run_eval.py",
            "mode": "per-task-command" if per_task_command else "single-command-template",
            "cwd_policy": "each task runs with cwd=<out>/tasks/<task_id>",
        },
        "status_counts": {s: sum(1 for r in results if r.get("status") == s) for s in STATUSES},
    }
    (out_root / "run_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if dry_run:
        (out_root / "run_plan.json").write_text(
            json.dumps({"executed": False, "plan": plan}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return meta


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

def self_test() -> int:
    """用合成题单自证契约：三个用例分别覆盖 passed / failed / timed_out。"""
    with tempfile.TemporaryDirectory(prefix="run-eval-selftest-") as tmp:
        base = Path(tmp)
        tasks_path = base / "tasks.jsonl"
        rows = [
            {"task_id": "syn-001", "repo": "example/demo", "base_commit": "0",
             "command": [sys.executable, "-c", "print('ok')"]},
            {"task_id": "syn-002", "repo": "example/demo", "base_commit": "0",
             "command": [sys.executable, "-c", "import sys; sys.exit(3)"]},
            {"task_id": "syn-003", "repo": "example/demo", "base_commit": "0",
             "command": [sys.executable, "-c", "import time; time.sleep(5)"]},
        ]
        write_jsonl(tasks_path, rows)

        tasks, problems = read_tasklist(tasks_path)
        if problems:
            print("自检失败：题单就读不过：" + "; ".join(problems))
            return 2

        out_root = base / "run"
        meta = run_tasks(
            tasks,
            out_root=out_root,
            argv_template=None,
            per_task_command=True,
            timeout=2.0,
            dry_run=False,
        )

        results_path = out_root / "task_results.jsonl"
        produced = [json.loads(x) for x in results_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        by_id = {r["task_id"]: r["status"] for r in produced}

        expected = {"syn-001": "passed", "syn-002": "failed", "syn-003": "timed_out"}
        ok = by_id == expected
        print("自检结果：")
        for task_id, want in expected.items():
            got = by_id.get(task_id, "(缺失)")
            mark = "OK" if got == want else "不符"
            print(f"  {task_id}: 期望 {want:10} 实际 {got:10} {mark}")
        print(f"  结果文件：{results_path}（{len(produced)} 行）")
        print(f"  run_meta.json：{out_root / 'run_meta.json'}")
        print(f"  计数：{meta['status_counts']}")
        if ok:
            print("SELFTEST=PASS")
            return 0
        print("SELFTEST=FAIL")
        return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="通用评测执行器：读题单 → 逐题执行 → 写四态结果（不联网、不拼 shell）"
    )
    parser.add_argument("--tasks", help="题单 JSONL（每行一个对象，至少含 task_id）")
    parser.add_argument("--out", help="输出目录；结果写 <out>/task_results.jsonl")
    parser.add_argument("--task-cmd", help='每题执行的命令模板，JSON 数组字符串，如 \'["python","eval.py","{task_id}"]\'')
    parser.add_argument("--per-task-command", action="store_true",
                        help="改为从题单每行的 command 字段读命令（要求字符串数组）")
    parser.add_argument("--timeout", type=positive_finite, default=600.0,
                        help="单题超时秒数，默认 600；必须是 (0, 86400] 内的有限数")
    parser.add_argument("--dry-run", action="store_true", help="只校验并生成计划，不执行任何命令")
    parser.add_argument("--allow-failures", action="store_true",
                        help="即使有非 passed 也返回 exit 0（默认：有非 passed 就 exit 1，避免上层把全题失败当成评测成功）")
    parser.add_argument("--self-test", action="store_true", help="用合成题单自检契约，不需要真实题单")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    if not args.tasks or not args.out:
        parser.error("除 --self-test 外，--tasks 与 --out 都必填")

    if not args.per_task_command and not args.task_cmd:
        parser.error("必须给 --task-cmd（统一命令模板）或 --per-task-command（每题自带命令）之一")

    # 先校验一切，再创建目录 —— 否则「参数非法但已经建了 out/run_meta.json」
    # 会让调用方以为跑过（L42 反例正是这条：--timeout inf + 不存在的 --out）。
    tasks, problems = read_tasklist(Path(args.tasks))
    if problems:
        print("题单非法，未执行任何题：", file=sys.stderr)
        for item in problems:
            print("  - " + item, file=sys.stderr)
        return 3

    argv_template = None
    if not args.per_task_command:
        argv_template = parse_cmd_template(args.task_cmd or "")
        # 拼错的占位符要在**开跑之前**拦住：`{task_idd}` 会被原样当参数传下去，
        # 执行器照常 exit 0，于是「一条谁都没执行的命令」被记成通过。
        unknown = unknown_placeholders(argv_template)
        if unknown:
            print(
                "命令模板里有不认识的占位符：" + "、".join(unknown)
                + "；可用的有：" + "、".join("{" + k + "}" for k in PLACEHOLDERS),
                file=sys.stderr,
            )
            return 2

    out_root = Path(args.out).resolve()
    try:
        out_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"无法创建输出目录 {out_root}：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    meta = run_tasks(
        tasks,
        out_root=out_root,
        argv_template=argv_template,
        per_task_command=bool(args.per_task_command),
        timeout=float(args.timeout),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(meta, ensure_ascii=False, indent=2))

    if args.dry_run:
        return 0

    not_passed = sum(meta["status_counts"].get(s, 0) for s in ("failed", "timed_out", "infra_error"))
    if not_passed:
        print(
            f"\n[注意] {not_passed} 题非 passed（failed/timed_out/infra_error 合计）；"
            f"status_counts={meta['status_counts']}。",
            file=sys.stderr,
        )
        if not args.allow_failures:
            print(
                "按默认策略返回 exit 1（有非 passed）；"
                "若你的上层只关心「评测本身跑完了」，加 --allow-failures 改回 exit 0。",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
