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
import re
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
AUTH_LABELS = {
    "预算上限": ("预算上限", "花费上限"),
    "授权人": ("授权人", "批准人"),
    "有效期": ("有效期", "有效至"),
    "允许提交": ("允许提交", "准入提交"),
}

#: 随仓库分发的示例题单 —— 它是形状说明，不是真数据。指向它要判 ✗ 并说清原因。
EXAMPLE_TASKLISTS = ("tasklist.example.jsonl",)

#: 已知的桩/占位执行器：出现这些说明配的还是假命令。
STUB_MARKERS = ("stub", "fake", "placeholder", "dummy", "example")


class Check:
    """三态：`ok` / `bad` / `unknown`。

    为什么不是布尔值：副机 L69 指出得对 ——「没发现 stub 字样」推不出「执行器是真的」。
    一个门只会说 ✓/✗，就会把「没证据」压成「通过」。`unknown` 是诚实的第三态：
    配置完整、但没证明它是真实执行器。
    """

    def __init__(self, key: str, title: str):
        self.key = key
        self.title = title
        self.state = "bad"          # ok / bad / unknown
        self.detail = ""
        self.next_step = ""

    @property
    def ok(self) -> bool:
        return self.state == "ok"

    def pass_(self, detail: str) -> "Check":
        self.state = "ok"
        self.detail = detail
        return self

    def fail(self, detail: str, next_step: str = "") -> "Check":
        self.state = "bad"
        self.detail = detail
        self.next_step = next_step
        return self

    def unknown(self, detail: str, next_step: str = "") -> "Check":
        self.state = "unknown"
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

    # 判「是不是随仓库分发的示例」要看**内容**，不能看文件名。
    # 副机 L69 实测：把 `tasklist.example.jsonl` 原样复制成 `tasks-copied-example.jsonl`，
    # 改名即绕过 —— 只认名字的判据挡不住任何想绕过的人。
    example = REPO / "profiles" / "agent-harness" / "examples" / "tasklist.example.jsonl"
    if example.is_file() and _same_file_content(p, example):
        return c.fail(
            f"这份题单与随仓库分发的示例逐字节相同（{p.name}），不是真数据",
            "示例只描述形状；照它跑等于拿合成数据当研究结果",
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
    detail = f"strict 可读，n_tasks={len(tasks)}，{with_cmd} 条自带 command"
    if with_cmd == 0:
        # 题单本身合法，但每题都不带命令 ⇒ 用 `--per-task-command` 跑会一题都跑不起来。
        # 这不是「题单不合格」，是「题单 + 执行器模式」不匹配，所以是 unknown 不是 bad。
        return c.unknown(
            detail + "（每题都不带 command：只有执行器用统一 `--task-cmd` 模板时才跑得起来）",
            "要么每题加 command，要么确认执行器用的是统一命令模板",
        )
    return c.pass_(detail)


def _same_file_content(a: pathlib.Path, b: pathlib.Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def check_profile(path: str | None, provenance: str | None = None) -> Check:
    """三态判执行器。

    副机 L69 把这件事拆得很清楚，采纳它的结论：

    * **配置完整性** —— `evaluation_state()` 已经做了（command 非空、`configured`、
      limits 齐、`command[0]` 非占位符/存在/非目录/后缀合格）。它证明的是「没配错」，
      **不是「执行器是真的」**。
    * **身份（provenance）** —— 真实执行器应当有可核对的登记：入口的内容哈希。
      「假脚本」和「真脚本」在文件存在性上完全一样，只有哈希能区分。
    * **行为负控** —— 拿一个故意不能跑的输入真跑一次。这会真的执行执行器，
      可能联网/计费，所以**默认不做**，留给人明确批准。

    所以：`can_run=false` ⇒ ✗；配好了但没 provenance ⇒ **`?`（未证实）**，不是 ✓。
    """
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

    # 能否被驱动：`{tasks}` 与 `{output}` 必须是独立元素，否则内核替换不了。
    missing_ph = [ph for ph in ("{tasks}", "{output}") if ph not in command]
    if missing_ph:
        return c.fail(
            f"command 里缺占位符 {'、'.join(missing_ph)}，无法被驱动",
            "command 必须含独立的 {tasks} 与 {output} 元素",
        )

    # 名字里像桩 ⇒ 降级为 unknown，**不判 ✗**：`my_stubby_runner.py` 是误伤，
    # `runner.py --dry-run` 是合法旗标。名字不是判据。
    joined = " ".join(str(x) for x in command).lower()
    hit = [m for m in STUB_MARKERS if m in joined]
    if hit:
        return c.unknown(
            f"command 里出现像桩的词 {'/'.join(hit)}：{joined[:120]}",
            "名字不是判据；请提供执行器 provenance，或人工确认它不是桩",
        )

    if not provenance:
        return c.unknown(
            f"可解析，can_run=true，command {len(command)} 元素；"
            "但「配置完整」不等于「这是真实执行器」—— 缺 provenance 登记",
            "给 --executor-provenance 一份清单（含入口 sha256）；"
            "或用拒绝性负控（空题单应非零退出）来证明它会做评测",
        )

    ok, why = _verify_provenance(command, provenance)
    if ok:
        # ⚠️ 说清这一项到底证明了什么。副机 L72 用 `noop.py`（两行、什么都不做）
        # 加一份**自己写的** provenance 拿到 4/4，证明了这条边界：
        #
        #   provenance 只能证明「这个入口与登记的内容一致」——即**没被偷换**；
        #   它**不能**证明「这个入口真的会做评测」。
        #
        # 因为登记本身也是人写的。我把「唯一能识破真解释器跑假脚本的判据」写进
        # commit message，那句话是错的。真正能证伪「假执行器」的只有行为负控：
        # 拿一个必然失败的输入跑一次，要求它以非零退出且不产出合规结果。
        return c.pass_(
            f"can_run=true，command {len(command)} 元素；provenance 与登记一致（{why}，未被替换）"
            "；注意：这只证明「没被偷换」，不证明「它真的会评测」"
        )
    return c.fail(f"provenance 校验失败：{why}", "执行器被替换过？核对登记里的 sha256 与当前入口")


def _resolve_entry(command: list) -> tuple[str, pathlib.Path | None, str]:
    """把 command 归成 `(kind, path, 说明)`：kind ∈ script / inline / binary。"""
    first = str(command[0])
    # `python -c "..."`：入口是 -c 后面的整段 payload，没有文件可哈希。
    for i, element in enumerate(command):
        if element == "-c" and i + 1 < len(command):
            return "inline", None, str(command[i + 1])
    # 否则取第一个「看起来是文件」的元素（跳过解释器与 -X/-B 这类旗标）。
    for element in command[1:]:
        s = str(element)
        if s.startswith("-") or s.startswith("{"):
            continue
        p = pathlib.Path(s)
        if p.is_file():
            return "script", p, s
    return "binary", pathlib.Path(first) if pathlib.Path(first).is_file() else None, first


def _verify_provenance(command: list, provenance_path: str) -> tuple[bool, str]:
    import hashlib

    try:
        blob = json.loads(pathlib.Path(provenance_path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"读不了 provenance：{exc}"
    if not isinstance(blob, dict):
        return False, "provenance 不是 JSON 对象"
    entry = blob.get("entry") if isinstance(blob.get("entry"), dict) else {}
    want = entry.get("sha256")
    if not isinstance(want, str) or len(want) != 64:
        return False, "provenance.entry.sha256 缺失或不是 64 位十六进制"

    kind, path, detail = _resolve_entry(command)
    if kind == "inline":
        actual = hashlib.sha256(detail.encode("utf-8")).hexdigest()
        return (actual == want, f"inline payload 哈希 {actual[:12]}…")
    if path is None:
        return False, "找不到可哈希的入口文件（command[0] 也不是文件）"
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        return False, f"读不了入口文件：{exc}"
    return (actual == want, f"{path.name} 哈希 {actual[:12]}…")


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
        # 副机 L72 实测的假绿：原来这里只查 `is_file()`，于是**0 字节私钥**被判 ✓，
        # 而旁边的失败文案写的正是「没有**非空** id_* 私钥」—— 文案承诺「非空」，
        # 实现只查「存在」。0 字节私钥不可能用于认证，这是标准的假绿形状。
        # 顺带把「没有私钥」与「私钥是空的」分成两句话：操作者的下一步不一样。
        nonempty = []
        for p in keys:
            try:
                if p.stat().st_size > 0:
                    nonempty.append(p)
            except OSError:
                pass
        if not keys:
            problems.append(f"SSH 目录里没有 id_* 私钥：{d}")
        elif not nonempty:
            problems.append(f"SSH 私钥是空的（0 字节，无法用于认证）：{d}")
        else:
            ok_bits.append(f"SSH 私钥 {len(nonempty)} 个（非空）")

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

    # 标签用**别名集合**：副机 L69 实测，两套实现一个要「预算上限」、一个要「花费上限」，
    # 于是同一份授权文件在两边给出相反结论 —— 「同一批事实、两个实现、结论相反」
    # 比任何单边判据都糟。同义词都收，但每个标签必须**后面跟着值**（见下）。
    missing = [label for label, aliases in AUTH_LABELS.items()
               if not any(a in text for a in aliases)]
    if missing:
        return c.fail(
            f"授权文件缺标签：{'、'.join(missing)}",
            "每项都要有："
            + " / ".join(f"{label}（{'或'.join(aliases)}）" for label, aliases in AUTH_LABELS.items()),
        )

    # 只查「标签在不在」会放过 `允许提交=是` —— 而「不许提交 Kaggle」是本项目的铁律。
    # 所以这一项的值必须解析出来，`是` 直接一票否决。
    submit = _label_value(text, AUTH_LABELS["允许提交"])
    if submit is None:
        return c.fail("「允许提交」没有值（只有标签）", "写成「允许提交：否」这种带值的形式")
    if submit.strip() not in ("否", "no", "false", "0", "不允许"):
        return c.fail(
            f"「允许提交」的值是 {submit.strip()!r}，本项目不许提交 Kaggle",
            "把「允许提交」改成「否」；要提交必须人来改这条铁律，不是改授权文件",
        )

    return c.pass_(
        "四标签齐且「允许提交=否」；其余值不回显（避免把金额/姓名打到日志里）"
    )


def _label_value(text: str, aliases) -> str | None:
    """取某个标签后面的值：支持 `标签：值` / `标签: 值` / `标签=值`。"""
    for alias in aliases:
        m = re.search(re.escape(alias) + r"\s*[:：=]\s*(.+)$", text, flags=re.M)
        if m:
            return m.group(1).strip()
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="preflight.py", description="人件就绪门：真实评测前的四样东西")
    ap.add_argument("--tasklist", default=None, help="真实题单路径（JSONL）")
    ap.add_argument("--profile", default=None, help="profile 目录或 profile.toml")
    ap.add_argument("--data-dir", default=None, help="数据目录（存在且非空）")
    ap.add_argument("--kaggle", default=None, help="kaggle.json 路径（只查存在与大小）")
    ap.add_argument("--ssh-dir", default=None, help="SSH 目录（需含非空 id_* 私钥）")
    ap.add_argument("--autodl-file", default=None, help="AutoDL token 文件（只查存在与大小）")
    ap.add_argument("--authorization", default=None, help="预算授权文件")
    ap.add_argument("--executor-provenance", default=None,
                    help="执行器 provenance 清单（含 entry.sha256）；不给则 B 项只能给「未证实」")
    ap.add_argument("--json", default=None, help="把结果写成 JSON")
    args = ap.parse_args(argv)

    checks = [
        check_tasklist(args.tasklist),
        check_profile(args.profile, args.executor_provenance),
        check_data(args),
        check_authorization(args.authorization),
    ]

    print(f"人件就绪门 · 仓库 {REPO}")
    for c in checks:
        mark = {"ok": "✓", "bad": "✗"}.get(c.state, "?")
        print(f"{c.key} {c.title} {mark}")
        print(f"    {c.detail}")
        if not c.ok and c.next_step:
            print(f"    下一步：{c.next_step}")

    done = sum(1 for c in checks if c.state == "ok")
    unknown = sum(1 for c in checks if c.state == "unknown")
    bad = sum(1 for c in checks if c.state == "bad")
    total = len(checks)
    verdict = 0 if done == total else 2
    print(
        f"结论：{done}/{total} 齐，{unknown} 项未证实，{bad} 项缺；exit {verdict}。"
        "齐只表示人件形状齐，不是放行评测 —— 放行仍需人明确表态。"
    )

    if args.json:
        payload = {
            "checks": [
                {"key": c.key, "title": c.title, "state": c.state, "ok": c.ok,
                 "detail": c.detail, "next_step": c.next_step}
                for c in checks
            ],
            "done": done, "unknown": unknown, "bad": bad,
            "total": total, "exit": verdict,
        }
        pathlib.Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写：{args.json}")
    return verdict


if __name__ == "__main__":
    raise SystemExit(main())
