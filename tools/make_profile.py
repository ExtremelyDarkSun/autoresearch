# GENERATED-BY: agent implementation, 2026-11-22 +08:00
# LAST-EDIT: 2026-11-22 +08:00
# PURPOSE: 把「手写一份能跑的 profile」收敛成一条命令，否则实验框架永远差最后一步。
# ORIGIN: none（新文件）。仓库交付的 profiles/agent-harness/profile.toml 是**形状说明**：
#         command=[] 且 configured=false，这是刻意为之（照抄就发起真评测更糟），
#         但它也让「想跑一次」的人必须自己写 command 数组、四条 limits、RULES.md 与
#         schema 路径 —— 任一处写错就 can_run=false，而报错只说缺什么，不说怎么补。
# CUMULATIVE-DELTA: 首版。生成后当场用 evaluation_state() 自证 can_run=true，
#                   不通过就报错退出，绝不产出一份「看起来配好了」的目录。
"""生成一份「可直接跑评测」的 profile 目录。

## 它解决什么

仓库里有两块现成的东西：

- `profiles/agent-harness/profile.toml` —— 字段形状说明，`command=[]`、`configured=false`
- `tools/run_eval.py` —— 通用评测执行器

但「把它们接起来」这一步是空的。`evaluation_state()` 的 `can_run` 要同时满足
配置齐、命令非空、四条预算齐、执行器存在 —— 手写时漏一项就 False，
而 False 只告诉你缺哪项，不告诉你该填什么值。

本脚本一次生成齐，并当场自证。

## 用法

```powershell
# 最省事：用当前解释器，默认预算
python -X utf8 -B tools\\make_profile.py --out profiles\\my-run

# 指定解释器（例如某个独立 venv 的 python）
python -X utf8 -B tools\\make_profile.py --out profiles\\my-run --python C:\\venv\\Scripts\\python.exe

# 覆盖预算
python -X utf8 -B tools\\make_profile.py --out profiles\\my-run --limits per_task_minutes=30 --limits max_turns=60

# 只看会写什么，不落盘
python -X utf8 -B tools\\make_profile.py --out profiles\\my-run --dry-run
```

## 生成的 command 是什么意思

    [<python>, "-X", "utf8", "-B", <repo>/tools/run_eval.py,
     "--tasks", "{tasks}", "--out", "{output}", "--per-task-command"]

`--per-task-command` 表示**每题自带命令**：题单每行写自己的 `command` 数组。
不做 shell 拼接，题号里有空格也不会被切碎。

想改成统一命令模板（`--task-cmd`）就手工替换这一段——但那要用 JSON 数组字符串，
容易写错，所以默认给每题自带命令这种更笨也更稳的形状。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from kernel.autoresearch.adapters.agent_harness import ResultsError  # noqa: E402
from kernel.autoresearch.profiles import (  # noqa: E402
    REQUIRED_LIMIT_KEYS,
    ProfileError,
    build_command,
    evaluation_state,
    load_profile,
    validate_profile,
)

SOURCE_PROFILE = REPO / "profiles" / "agent-harness"
RUN_EVAL = REPO / "tools" / "run_eval.py"

#: 默认预算：够跑小批量合成题，真实题库通常要调大。
DEFAULT_LIMITS = {
    "per_task_minutes": 10,
    "max_tool_calls": 20,
    "max_turns": 30,
    "command_timeout_seconds": 600,
}

#: 需要从源 profile 目录复制过去的材料（schema 校验会查它们是否存在）。
COPY_FILES = ("RULES.md", "README.md", "EXTRACTION.md")
COPY_DIRS = ("schemas", "examples", "checks")


def parse_limits(pairs) -> dict:
    """把 `key=value` 解析成 limits 字典；键必须是内核认的，值必须是正数。"""
    limits = dict(DEFAULT_LIMITS)
    for raw in pairs or ():
        if "=" not in raw:
            raise SystemExit(f"--limits 需要 key=value 形状，收到 {raw!r}")
        key, _, value = raw.partition("=")
        key = key.strip()
        if key not in REQUIRED_LIMIT_KEYS and key != "total_budget_minutes":
            raise SystemExit(
                f"未知的预算键 {key!r}；只允许 {', '.join(REQUIRED_LIMIT_KEYS)} "
                "与 total_budget_minutes"
            )
        try:
            parsed = int(value.strip())
        except ValueError:
            raise SystemExit(f"--limits {key} 的值必须是整数，收到 {value!r}")
        if parsed <= 0:
            raise SystemExit(f"--limits {key} 必须大于 0，收到 {parsed}")
        limits[key] = parsed
    return limits


def render_profile(python: str, limits: dict) -> str:
    # `per_task_minutes` 必须变成执行器真正认识的参数，否则它只是一行注释 ——
    # 「配置里写了上限、实际没人执行」是最容易被当成已生效的一类假绿。
    # 执行器的 `--timeout` 是单题墙钟秒数，这里按分钟换算过去。
    per_task_seconds = int(limits["per_task_minutes"]) * 60
    command = [
        python,
        "-X", "utf8", "-B", str(RUN_EVAL),
        "--tasks", "{tasks}",
        "--out", "{output}",
        "--per-task-command",
        "--timeout", str(per_task_seconds),
    ]
    lines = [
        "# 由 tools/make_profile.py 生成。要改预算就重跑生成器，或直接改下面的数值。",
        "#",
        "# 这份文件与 profiles/agent-harness/profile.toml 的区别只有一处：",
        "# 这里 command 非空、configured=true、四条 limits 齐 —— 所以 can_run 为 true，",
        "# 可以真的发起评测。原来那份是形状说明，刻意不自带命令。",
        "",
        'id = "agent-harness"',
        "version = 1",
        "",
        "[evaluation]",
        "# 注意 command 末尾的 --timeout：它由 [limits].per_task_minutes 换算而来",
        f"# （{limits['per_task_minutes']} 分钟 = {per_task_seconds} 秒）。",
        "# 改 per_task_minutes 后请重跑生成器，否则这两个数会各说各话。",
        "command = [" + ", ".join(json.dumps(x, ensure_ascii=False) for x in command) + "]",
        "configured = true",
        'result_file = "task_results.jsonl"',
        'trace_dir = "traces"',
        "result_schema_version = 1",
        "",
        "[instructions]",
        'required = ["RULES.md"]',
        "",
        "[limits]",
    ]
    for key in REQUIRED_LIMIT_KEYS:
        lines.append(f"{key} = {limits[key]}")
    if "total_budget_minutes" in limits:
        lines.append(f"total_budget_minutes = {limits['total_budget_minutes']}")
    lines += [
        "",
        "[trace]",
        "tool_names = []",
        "read_tools = []",
        "edit_tools = []",
        "command_tools = []",
        "",
        "[workspace]",
        "allowed_roots = []",
        "forbidden_path_patterns = []",
        "ignore_paths = []",
        "",
        "[leak]",
        "sensitive_fragments = []",
        "include_observations = false",
        "",
        "[schema]",
        'tasklist = "schemas/tasklist.schema.json"',
        'results = "schemas/results.schema.json"',
        'trace = "schemas/trace.schema.json"',
        'tasklist_example = "examples/tasklist.example.jsonl"',
        'results_example = "examples/results.example.jsonl"',
        'trace_example = "examples/trace.example.json"',
        "",
    ]
    return "\n".join(lines)


def materialize(dest: Path) -> list[str]:
    """把源 profile 的材料复制到目标目录，返回复制了什么。"""
    copied: list[str] = []
    for name in COPY_FILES:
        src = SOURCE_PROFILE / name
        if src.is_file():
            shutil.copy2(src, dest / name)
            copied.append(name)
    for name in COPY_DIRS:
        src = SOURCE_PROFILE / name
        if src.is_dir():
            shutil.copytree(src, dest / name, dirs_exist_ok=True)
            copied.append(name + "/")
    return copied


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="make_profile.py",
        description="生成一份 can_run=true 的 profile 目录（可直接 research.py eval）",
    )
    parser.add_argument("--out", required=True, help="目标目录（不存在会创建）")
    parser.add_argument("--python", default=sys.executable,
                        help="评测命令的解释器路径，默认当前解释器")
    parser.add_argument("--limits", action="append", default=[],
                        help="覆盖预算，形如 per_task_minutes=30；可重复")
    parser.add_argument("--force", action="store_true", help="目标目录已有 profile.toml 时覆盖")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要写入的 profile.toml")
    args = parser.parse_args(argv)

    dest = Path(args.out).expanduser()
    profile_path = dest / "profile.toml"

    if profile_path.exists() and not args.force and not args.dry_run:
        print(f"目标已存在：{profile_path}\n要覆盖请加 --force", file=sys.stderr)
        return 2

    if not Path(args.python).exists() and not shutil.which(args.python):
        print(f"--python 指向的解释器不存在：{args.python}", file=sys.stderr)
        return 2

    if not RUN_EVAL.is_file():
        print(f"找不到执行器：{RUN_EVAL}（仓库结构不对？）", file=sys.stderr)
        return 2

    limits = parse_limits(args.limits)
    text = render_profile(args.python, limits)

    if args.dry_run:
        print(text)
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    copied = materialize(dest)
    profile_path.write_text(text, encoding="utf-8")

    # 当场自证：不通过就删掉刚写的 profile.toml，别留一份「看起来配好了」的目录。
    try:
        profile = load_profile(profile_path)
        errors = validate_profile(profile)
        if errors:
            raise ProfileError("；".join(errors))
        state = evaluation_state(profile)
        if not state.get("can_run"):
            raise ProfileError("；".join(state.get("blocking") or ["can_run=false，原因未给出"]))
        argv = build_command(profile, tasks="<tasks.jsonl>", output="<out>")
    except (ProfileError, ResultsError) as exc:
        profile_path.unlink(missing_ok=True)
        print(f"生成失败，已删除 profile.toml：{exc}", file=sys.stderr)
        return 3

    print(json.dumps({
        "ok": True,
        "profile": str(profile_path),
        "copied": copied,
        "limits": limits,
        "python": args.python,
        "argv_preview": argv,
        "next": (
            "python -X utf8 -B research.py eval "
            f"--profile {profile_path} --tasks <题单.jsonl> --output <输出目录>"
        ),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
