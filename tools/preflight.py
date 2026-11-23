# GENERATED-BY: agent implementation, 2026-11-23 +08:00
# LAST-EDIT: 2026-11-23 +08:00
# PURPOSE: 真实跑一轮之前必须由「人」提供的四样东西，用一条命令查清缺哪几项。
# ORIGIN: none（新文件）。设计取自副机已验证的「人件就绪门」（四路正控 4/4、撤销反控 0/4 都跑过），
#         但把路径全部参数化，不带任何一场比赛的固定值 —— 副机那份绑死在它自己的目录上。
# CUMULATIVE-DELTA: 首版。四项检查，缺项给「下一步」而不是只给 ✗。
"""人件就绪门：真实跑一轮之前，先看四样东西齐不齐。

## 它解决什么问题

「跑不动真实实验」这句话太粗。真实原因永远是下面四件之一，而它们都只有**人**能提供：

| 项 | 是什么 | 为什么机器给不了 |
|---|---|---|
| A 题单 | 哪些题、每题怎么判分 | 仓库刻意不附带比赛数据（防私有字段混进通用配置） |
| B 执行器 | 真正跑评测的那个 argv | 命令形状由部署方定；仓库只给通用执行器与生成器 |
| C 数据与访问权 | 数据目录、Kaggle/SSH/AutoDL 凭据 | 凭据要人去拿；登录要过人证 |
| D 一次真评测的预算授权 | 上限、授权人、有效期、是否允许提交 | 花钱与放行只能由人表态 |

这个门只查**形状**：文件在不在、能不能解析、字段齐不齐。它**不读凭据内容**、
不发网络请求、不放行任何评测。

## 最重要的一句话

**0/4 是常态，4/4 也不等于放行。** 四项齐只说明「材料齐了」，
真正的放行还得人明确说一句「可以跑」。
反过来，看到 ✗ 不要以为是程序坏了 —— 那是这个工具在如实说「还差人」。

## 用法

```powershell
python -X utf8 -B tools\\preflight.py `
    --tasklist D:\\work\\tasks.jsonl `
    --profile profiles\\my-run `
    --data-dir D:\\work\\data `
    --kaggle $env:USERPROFILE\\.kaggle\\kaggle.json `
    --ssh-dir $env:USERPROFILE\\.ssh `
    --autodl-file D:\\work\\secrets\\autodl-token.txt `
    --authorization D:\\work\\HUMAN-EVAL-AUTHORIZATION.md
```

不指定某一项 ⇒ 该项判 ✗ 并说明「未指定」。退出码：4/4 为 0，否则 2。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from kernel.autoresearch.adapters.agent_harness import (  # noqa: E402
    ResultsError,
    read_tasklist,
)
from kernel.autoresearch.profiles import (  # noqa: E402
    ProfileError,
    evaluation_state,
    load_profile,
)

#: 授权文件里必须出现的四个标签（只看标签，不看值）。
AUTH_LABELS = ("预算上限", "授权人", "有效期", "允许提交")

#: 随仓库分发的示例题单 —— 它是形状说明，不是真数据。指向它要判 ✗ 并说清原因。
EXAMPLE_TASKLISTS = ("tasklist.example.jsonl",)

#: 已知的桩/占位执行器：出现这些说明配的还是假命令。
STUB_MARKERS = ("stub", "fake", "placeholder", "dummy", "example")


class Check:
    def __init__(self, key: str, title: str):
        self.key = key
        self.title = title
        self.ok = False
        self.detail = ""
        self.next_step = ""

    def pass_(self, detail: str) -> "Check":
        self.ok = True
        self.detail = detail
        return self

    def fail(self, detail: str, next_step: str = "") -> "Check":
        self.ok = False
        self.detail = detail
        self.next_step = next_step
        return self


def check_tasklist(path: str | None) -> Check:
    c = Check("A", "题单")
    if not path:
        return c.fail("未指定 --tasklist", "给出真实题单的路径（每行一条 JSON，至少含 task_id）")
    p = pathlib.Path(path)
    if not p.is_file():
        return c.fail(f"找不到题单文件：{p}", "确认路径；题单是输入，不是结果")
    if p.name.lower() in EXAMPLE_TASKLISTS:
        return c.fail(
            f"这是随仓库分发的示例题单（{p.name}），不是真数据",
            "示例只描述形状、不带 command 字段，照它跑会一题都跑不起来",
        )
    try:
        got = read_tasklist(str(p))
    except ResultsError as exc:
        return c.fail(f"读不了：{exc}", "按报错行号修题单")
    tasks = got.get("tasks") or []
    if not tasks:
        return c.fail("题单是空的", "空题单通常意味着命令行漏了参数")

    # `read_tasklist()` 返回的每条是 {task_id, line, raw}：原始行在 `raw` 里。
    # 别在顶层找 `command` —— 我第一次就写成 `t.get("command")`，于是明明每题都带命令，
    # 却报「0 条自带 command」。这种「读数看着像结论、其实是取错字段」的错，
    # 正是这个仓库一路在打的东西，只不过这次在我自己新写的 50 行里。
    def _has_command(task: dict) -> bool:
        raw = task.get("raw") if isinstance(task.get("raw"), dict) else {}
        cmd = raw.get("command")
        return isinstance(cmd, list) and bool(cmd)

    with_cmd = sum(1 for t in tasks if _has_command(t))
    return c.pass_(f"strict 可读，n_tasks={len(tasks)}，{with_cmd} 条自带 command")


def check_profile(path: str | None) -> Check:
    c = Check("B", "执行器")
    if not path:
        return c.fail("未指定 --profile", "用 tools/make_profile.py 生成一份，或给出已有 profile 目录")
    try:
        profile = load_profile(path)
    except ProfileError as exc:
        return c.fail(f"profile 读不了：{exc}", "先跑 python -X utf8 -B research.py profile 看校验错误")
    state = evaluation_state(profile)
    command = (profile.get("evaluation") or {}).get("command") or []
    if not state.get("can_run"):
        blocking = "；".join(state.get("blocking") or ["未知原因"])
        return c.fail(f"can_run=false：{blocking}", "补齐 command / configured / limits")
    joined = " ".join(str(x) for x in command).lower()
    hit = [m for m in STUB_MARKERS if m in joined]
    if hit:
        return c.fail(
            f"命令里出现桩/占位标记 {'/'.join(hit)}，这不是真实执行器：{joined[:120]}",
            "把 command 换成真正跑评测的那个入口",
        )
    return c.pass_(f"可解析，can_run=true，command {len(command)} 元素")


def check_data(args) -> Check:
    c = Check("C", "数据与访问权")
    problems: list[str] = []
    ok_bits: list[str] = []

    if not args.data_dir:
        problems.append("未指定 --data-dir")
    else:
        d = pathlib.Path(args.data_dir)
        if not d.is_dir():
            problems.append(f"数据目录不存在：{d}")
        elif not any(d.iterdir()):
            problems.append(f"数据目录是空的：{d}")
        else:
            ok_bits.append("数据目录存在且非空")

    for label, value in (("Kaggle", args.kaggle), ("AutoDL", args.autodl_file)):
        if not value:
            problems.append(f"未指定 --{label.lower() if label == 'Kaggle' else 'autodl-file'}")
        else:
            f = pathlib.Path(value)
            # 只查存在与大小，**绝不读内容、绝不打印值**。
            if not f.is_file() or f.stat().st_size == 0:
                problems.append(f"{label} 文件不存在或为空：{f}")
            else:
                ok_bits.append(f"{label} 文件存在（{f.stat().st_size} B，未读取）")

    if not args.ssh_dir:
        problems.append("未指定 --ssh-dir")
    else:
        d = pathlib.Path(args.ssh_dir)
        keys = [p for p in d.glob("id_*") if p.is_file() and not p.name.endswith(".pub")] if d.is_dir() else []
        if not keys:
            problems.append(f"SSH 目录没有非空 id_* 私钥：{d}")
        else:
            ok_bits.append(f"SSH 私钥 {len(keys)} 个")

    if problems:
        return c.fail("；".join(problems), "按列出的缺项逐条备料；凭据只查存在性，不读内容")
    return c.pass_("；".join(ok_bits))


def check_authorization(path: str | None) -> Check:
    c = Check("D", "预算授权")
    if not path:
        return c.fail("未指定 --authorization", "一次真评测需要人写明上限/授权人/有效期/是否允许提交")
    p = pathlib.Path(path)
    if not p.is_file():
        return c.fail(f"授权文件不存在：{p}", "没有授权文件时不允许开跑")
    try:
        text = p.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        return c.fail(f"读不了授权文件：{exc}")
    missing = [label for label in AUTH_LABELS if label not in text]
    if missing:
        return c.fail(
            f"授权文件缺标签：{'、'.join(missing)}",
            f"四个标签都要有：{' / '.join(AUTH_LABELS)}",
        )
    return c.pass_(f"四标签齐（{' / '.join(AUTH_LABELS)}）；未回显任何值")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="preflight.py", description="人件就绪门：真实评测前的四样东西")
    ap.add_argument("--tasklist", default=None, help="真实题单路径（JSONL）")
    ap.add_argument("--profile", default=None, help="profile 目录或 profile.toml")
    ap.add_argument("--data-dir", default=None, help="数据目录（存在且非空）")
    ap.add_argument("--kaggle", default=None, help="kaggle.json 路径（只查存在与大小）")
    ap.add_argument("--ssh-dir", default=None, help="SSH 目录（需含非空 id_* 私钥）")
    ap.add_argument("--autodl-file", default=None, help="AutoDL token 文件（只查存在与大小）")
    ap.add_argument("--authorization", default=None, help="预算授权文件")
    ap.add_argument("--json", default=None, help="把结果写成 JSON")
    args = ap.parse_args(argv)

    checks = [
        check_tasklist(args.tasklist),
        check_profile(args.profile),
        check_data(args),
        check_authorization(args.authorization),
    ]

    print(f"人件就绪门 · 仓库 {REPO}")
    for c in checks:
        mark = "✓" if c.ok else "✗"
        print(f"{c.key} {c.title} {mark}")
        print(f"    {c.detail}")
        if not c.ok and c.next_step:
            print(f"    下一步：{c.next_step}")

    done = sum(1 for c in checks if c.ok)
    total = len(checks)
    verdict = 0 if done == total else 2
    print(
        f"结论：四项中 {done}/{total} 齐；exit {verdict}。"
        "齐只表示人件形状齐，不是放行评测 —— 放行仍需人明确表态。"
    )

    if args.json:
        payload = {
            "checks": [
                {"key": c.key, "title": c.title, "ok": c.ok,
                 "detail": c.detail, "next_step": c.next_step}
                for c in checks
            ],
            "done": done, "total": total, "exit": verdict,
        }
        pathlib.Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写：{args.json}")
    return verdict


if __name__ == "__main__":
    raise SystemExit(main())
