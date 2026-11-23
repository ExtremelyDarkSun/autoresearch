# GENERATED-BY: agent implementation, 2026-11-23 +08:00
# LAST-EDIT: 2026-11-23 +08:00
# PURPOSE: 可复现性矩阵——把「副机复现主机」从一句话变成一张可比对的表。
# ORIGIN: none（新文件）。诱因：前几轮「复现」靠人工挑不变量（demo 12 条、矩阵 9 行），
#         每次都要重新商量「比什么、别比什么」。这个工具把它固化成：跑固定命令集 →
#         规范化输出 → 出哈希 → 两台机器直接比哈希。
# CUMULATIVE-DELTA: 首版。六条命令，含「应当一致」与「允许不同（附原因）」两类期望。
"""可复现性矩阵：把「副机复现主机」变成可比对的哈希。

## 它解决什么问题

「副机复现主机」此前靠人工挑不变量：demo 比 12 个字段、对抗矩阵比 9 行 verdict。
每次都有人问「这个能不能比」——而摇摆的判据本身就是假绿与假失败的双重来源。

这个工具把判据固化：

1. 跑一条固定的命令集（全部离线、不联网、不花钱）
2. 把输出按规则**规范化**（抹掉绝对路径、临时目录、时间戳、耗时、随机 id）
3. 对规范化后的文本取哈希
4. 两台机器各跑一次，`--compare` 直接给出「哪些一致、哪些不同、不同的原因是什么」

## 两种期望

- `same`：规范化后必须逐字一致。不一致 = 真的要查的缺口。
- `may-differ`：允许不同，**并写明为什么**（例如 `doctor` 会打印 Python 版本、
  `smoke` 会打印耗时）。这一类不同不算缺口，但也不许悄悄扩大解释——
  原因写在 `WHY` 里，改了就得改这一行。

## 用法

```powershell
# 本机跑一遍，写出 JSON
python -X utf8 -B tools\\repro_matrix.py --json matrix-host.json

# 拿到另一台机器的 JSON 后比对
python -X utf8 -B tools\\repro_matrix.py --compare matrix-peer.json

# 同一台机器连跑两次，确认矩阵自身稳定（跨机比对的前提）
python -X utf8 -B tools\\repro_matrix.py --self-check
```
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
PY = sys.executable
RESEARCH = REPO / "research.py"


# ---------------------------------------------------------------------------
# 规范化：把「每台机器必然不同、但不影响结论」的部分抹掉
# ---------------------------------------------------------------------------

REPLACEMENTS = [
    # ---- 路径（顺序要紧，先引号内、后裸路径）----
    #
    # 副机 L66 实测抓到的坑：原规则 `[A-Za-z]:\\[^\s"'\]);
    # ,;]*` 在**空格处停下**。它的解释器装在
    # `C:\Program Files\Python312\python.exe`，于是只吃掉 `C:\Program`，
    # 剩下的 ` Files\Python312\python.exe` 又被 UNC 规则吃成 `<UNC>`，
    # 结果同一个 profile 在两台机器上规范化成不同文本 ——
    #
    #     "<PATH> Files<UNC>"        （副机，路径带空格）
    #     "<PATH>"                   （主机，路径无空格）
    #
    # **一个用来消除环境噪声的工具，自己制造了噪声。** 而且它只在「安装路径含空格」
    # 的机器上发作，主机永远复现不出来。
    #
    # 修法：先整体吃掉引号内的路径（JSON/TOML 字符串里可能是 `\\` 转义），
    # 再处理裸路径（裸路径没法可靠判断结尾，只能到空白/分隔符为止）；UNC 规则同理，
    # 且必须在引号规则之后 —— 否则 `\\` 转义会被误当成 UNC 前缀。
    (re.compile(r'"[A-Za-z]:\\\\?[^"]*"'), '"<PATH>"'),
    (re.compile(r'"[\\\\/]{2}[^"]*"'), '"<UNC>"'),
    # 裸路径**不在这里处理** —— 交给 `_mask_bare_paths()` 逐字符扫。
    # 原因见那个函数的注释：一条正则同时要「吃掉多段各自含空格的路径」和
    # 「不吃掉路径后面的散文」，副机 L70 把两种失败都实测出来了。
    # ---- 工作树状态 ----
    # smoke 第 7 步的措辞取决于「开始跑之前仓库脏不脏」，那是测试前置条件，
    # 不是机器属性。跨机比对本不该被它带偏，所以两种措辞收敛成同一个标记。
    (re.compile(r"与开始时一致（本来就脏 \d+ 项）"), "<TREE-STATE>"),
    (re.compile(r"无新增改动"), "<TREE-STATE>"),
    # ISO 时间戳与日期
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"), "<TS>"),
    (re.compile(r"\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}(?:\.\d+)?"), "<TS>"),
    # 耗时：`in 118.962s`、`耗时 134s`、`6.75s`
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:s|sec|seconds?)\b"), "<DUR>"),
    (re.compile(r"in\s+\d+(?:\.\d+)?s\b"), "in <DUR>"),
    # 32/64 位十六进制 id（attempt id、sha256、blob）
    (re.compile(r"\b[0-9a-f]{32,64}\b"), "<ID>"),
    # 浮点秒数（预算时间戳那类）
    (re.compile(r"\b\d{10}\.\d+\b"), "<EPOCH>"),
    # 空白收敛，避免缩进差异放大
    (re.compile(r"[ \t]+"), " "),
]


def _eat_bare_path(text: str, start: int) -> int:
    """从 `start`（指向 `X:\\` 的开头）往后吃一条裸路径，返回结束下标。

    为什么不用正则：路径里的空格数量是不定的。
    `C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe` 有两处
    各自含空格，而 `C:\\Python313\\python.exe 后面还有散文` 里的空格后面是散文。
    用一条正则同时处理这两件事，要么吃掉散文，要么在 `(x86)` 处断掉 ——
    副机 L70 把这两种失败都实测出来了。

    判据只有一条，逐字符看：**遇到空白时，往同一行的后面找反斜杠；
    找得到就说明这段空白还在路径里，找不到就说明路径到词为止。**
    """
    stop_chars = set('"\'\n\r|<>*')
    sep_chars = set(",;")
    i = start
    while i < len(text):
        ch = text[i]
        if ch in stop_chars or ch in sep_chars:
            break
        if ch == ")":
            # 右括号：只有它后面紧跟路径成分时才属于路径（`(x86)\`），
            # 否则它多半是散文里的收尾括号。
            if i + 1 >= len(text) or text[i + 1] in " \t\n\r" or text[i + 1] in sep_chars:
                break
            i += 1
            continue
        if ch in " \t":
            j = i
            while j < len(text) and text[j] in " \t":
                j += 1
            # 到行尾/引号/分隔符之前还有反斜杠吗？
            k = j
            has_backslash = False
            while k < len(text) and text[k] not in '"\'\n\r':
                if text[k] in sep_chars:
                    break
                if text[k] == "\\":
                    has_backslash = True
                    break
                k += 1
            if not has_backslash:
                break
            i = j
            continue
        i += 1
    return i


def _mask_bare_paths(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        m = _BARE_DRIVE.search(text, i)
        m_unc = _BARE_UNC.search(text, i)
        cands = [x for x in (m, m_unc) if x]
        if not cands:
            out.append(text[i:])
            break
        first = min(cands, key=lambda x: x.start())
        out.append(text[i:first.start()])
        if first.re is _BARE_UNC:
            out.append("<UNC>")
            i = _eat_bare_path(text, first.end())
        else:
            end = _eat_bare_path(text, first.end())
            out.append("<PATH>")
            i = end
    return "".join(out)


_BARE_DRIVE = re.compile(r"[A-Za-z]:\\")
_BARE_UNC = re.compile(r"\\\\[A-Za-z0-9._-]+\\")


def normalize(text: str) -> str:
    for pattern, repl in REPLACEMENTS:
        text = pattern.sub(repl, text)
    # 裸路径单独处理：引号内的已经被上面吃掉，剩下的是无引号形态。
    text = _mask_bare_paths(text)
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 命令集
# ---------------------------------------------------------------------------

def build_cases(tmp: pathlib.Path):
    """返回 [(名字, 期望, 为什么, argv)]。全部离线、只读、不花钱。"""
    demo_out = tmp / "demo"
    made = tmp / "profile"
    return [
        ("profile", "same",
         "只回显 profile 内容与 argv；抹掉路径后应当逐字一致",
         [PY, "-X", "utf8", "-B", str(RESEARCH), "profile",
          "--path", str(REPO / "profiles" / "agent-harness")]),
        ("doctor", "may-differ",
         "会打印 Python 版本与宿主事实（宿主名、插件清单等）；两台机器本来不同",
         [PY, "-X", "utf8", "-B", str(RESEARCH), "doctor"]),
        ("make_profile", "same",
         "生成的 profile 文本与 argv 预览；路径规范化后应一致",
         [PY, "-X", "utf8", "-B", str(REPO / "tools" / "make_profile.py"),
          "--out", str(made), "--dry-run"]),
        ("smoke", "same",
         "七步端到端；耗时与「工作树本来就脏」的措辞都已抹掉，判定与 PASS/FAIL 必须一致",
         [PY, "-X", "utf8", "-B", str(REPO / "tools" / "smoke.py")]),
        ("gate_probe", "same",
         "九个对抗场景的判定表；不应含机器相关项，必须逐字一致",
         [PY, "-X", "utf8", "-B", str(REPO / "tools" / "gate_probe.py")]),
        ("demo", "same",
         "离线研究生命周期；只保留不变量（state/submit_calls/budget/attempt），"
         "抹掉 attempt id 与时间戳后应一致",
         [PY, "-X", "utf8", "-B", str(RESEARCH), "demo", "--output", str(demo_out)]),
    ]


def run_case(name: str, argv, *, keep_normalized: bool = False) -> dict:
    env_note = None
    try:
        proc = subprocess.run(
            argv, cwd=str(REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=900,
            stdin=subprocess.DEVNULL,
        )
        raw = (proc.stdout or "") + (proc.stderr or "")
        code = proc.returncode
    except subprocess.TimeoutExpired:
        raw, code, env_note = "<timeout>", -1, "900 秒超时"
    except OSError as exc:
        raw, code, env_note = f"<oserror>{exc}", -1, str(exc)

    norm = normalize(raw)
    rec = {
        "name": name,
        "exit": code,
        "lines": len(norm.splitlines()) if norm else 0,
        "norm_sha": digest(norm),
        "raw_sha": digest(raw),
        "note": env_note,
    }
    if keep_normalized:
        rec["normalized"] = norm
    return rec


def collect(*, keep_normalized: bool = False) -> dict:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="repro-matrix-"))
    out = {"python": sys.version.split()[0], "platform": sys.platform, "cases": []}
    for name, expect, why, argv in build_cases(tmp):
        rec = run_case(name, argv, keep_normalized=keep_normalized)
        rec.update({"expect": expect, "why": why})
        out["cases"].append(rec)
    return out


def print_table(matrix: dict) -> None:
    hdr = f"{'命令':<14}{'exit':>5}{'行数':>6}  {'规范化 hash':<18}{'期望':<12}"
    print(hdr)
    print("-" * len(hdr))
    for c in matrix["cases"]:
        print(f"{c['name']:<14}{c['exit']:>5}{c['lines']:>6}  {c['norm_sha']:<18}{c['expect']:<12}")
    print(f"Python = {matrix['python']} / platform = {matrix['platform']}")


def compare(mine: dict, other: dict) -> int:
    by_name = {c["name"]: c for c in other.get("cases", [])}
    hdr = f"{'命令':<14}{'判定':<10}{'本机 hash':<18}{'对方 hash':<18}{'期望':<12}"
    print(hdr)
    print("-" * len(hdr))
    problems = 0
    for c in mine["cases"]:
        o = by_name.get(c["name"])
        if o is None:
            print(f"{c['name']:<14}{'缺项':<10}{c['norm_sha']:<18}{'-':<18}{c['expect']:<12}")
            problems += 1
            continue
        same = c["norm_sha"] == o["norm_sha"]
        if same:
            verdict = "一致"
        elif c["expect"] == "may-differ":
            verdict = "允许不同"
        else:
            verdict = "不一致"
            problems += 1
        print(f"{c['name']:<14}{verdict:<10}{c['norm_sha']:<18}{o['norm_sha']:<18}{c['expect']:<12}")
        if not same:
            print(f"    原因说明：{c['why']}")
            first_diff(mine, c["name"], other, o)
    print("-" * len(hdr))
    print(f"Python：本机 {mine['python']} / 对方 {other.get('python')}")
    print(f"需要查的不一致：{problems} 项（may-differ 里 hash 不同不算）")
    return 0 if problems == 0 else 1


def first_diff(mine: dict, name: str, other: dict, other_case: dict) -> None:
    a = next((c["normalized"] for c in mine["cases"] if c["name"] == name), "")
    b = other_case.get("normalized", "")
    if not b:
        print("    （对方没带规范化原文，只能比 hash）")
        return
    la, lb = a.splitlines(), b.splitlines()
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else "<无>"
        y = lb[i] if i < len(lb) else "<无>"
        if x != y:
            print(f"    首个差异在第 {i + 1} 行：")
            print(f"      本机：{x[:160]}")
            print(f"      对方：{y[:160]}")
            return
    print("    （规范化文本相同，但 hash 不同 —— 检查是否含被抹掉的字符差异）")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="repro_matrix.py", description="可复现性矩阵")
    ap.add_argument("--json", default=None, help="把本机矩阵写成 JSON")
    ap.add_argument("--compare", default=None, help="与另一台机器的 JSON 比对")
    ap.add_argument("--self-check", action="store_true",
                    help="连跑两次，确认矩阵在同一台机器上稳定（跨机比对的前提）")
    ap.add_argument("--keep-normalized", action="store_true",
                    help="JSON 里保留规范化全文（默认不保留；要定位差异时再加）")
    args = ap.parse_args(argv)

    if args.self_check:
        first = collect()
        second = collect()
        a = {c["name"]: c["norm_sha"] for c in first["cases"]}
        b = {c["name"]: c["norm_sha"] for c in second["cases"]}
        print_table(first)
        print()
        unstable = [n for n in a if a[n] != b.get(n)]
        print(f"自检：{len(a)} 条命令，{len(a) - len(unstable)} 条两次一致，{len(unstable)} 条不稳定")
        for n in unstable:
            print(f"  ! {n}：{a[n]} → {b[n]}  —— 这条的规范化规则还不够狠，跨机比对会被它带偏")
        return 0 if not unstable else 1

    matrix = collect(keep_normalized=args.keep_normalized)

    if args.compare:
        other = json.loads(pathlib.Path(args.compare).read_text(encoding="utf-8-sig"))
        return compare(matrix, other)

    print_table(matrix)
    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写：{args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
