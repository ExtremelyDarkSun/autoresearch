# GENERATED-BY: agent implementation, 2026-11-24 +08:00
# LAST-EDIT: 2026-11-24 +08:00
# PURPOSE: 从仓库自己的 git 历史里反解出「带真值的可判定任务」，并跑正负控。
# ORIGIN: none（新文件）。来自一次实验：比赛判分清单在平台侧拿不到，但快照带完整历史，
#         「同一次提交既改源码又改测试」的提交可以反解成 fail-to-pass。
#         实测 fastapi 16 道、rich 27 道可用（详见 docs/UNFIX_TASKS.md）。
# CUMULATIVE-DELTA: 首版。
"""从 git 历史反解可判定任务（unfix tasks）。

## 它解决的问题

有仓库、有测试，但**没有判分清单**（哪几条测试该从红变绿）。这时可以做的是：
把某次修 bug 提交的**源码改动退回去、测试留在新版本**，那条新增的测试就会红。
这个红就是自动得到的 fail-to-pass。

```
第 1 步（红）：源码退到 <commit>^，测试保持 HEAD  → 期望失败，失败用例 = fail-to-pass
第 2 步（绿）：源码恢复 HEAD                      → 期望全过
```

两步都成立，这道题才成立。**任何一步不成立，这道题不许进题集** —— 否则就是拿一把
没校准的尺子量东西。

## 三个子命令

```powershell
python -X utf8 -B tools/unfix.py scan   --repo <dir> [--limit 40]
python -X utf8 -B tools/unfix.py run    --repo <dir> --commit <sha> --tests tests/x.py --out <dir> [--apply-gold]
python -X utf8 -B tools/unfix.py selftest
```

## 判据（`classify`）

判定逻辑被抽成一个纯函数，因为**它曾经连续错三次**：

| 红 | 绿 | 结论 | 为什么 |
|---|---|---|---|
| 1（有用例 id） | 0 | 可用 | 标准情形 |
| 1（无 id） | 0 | 不可用 | 红了但说不出哪条红 |
| **124（挂住）** | 0 | **可用** | 这个 bug 的表现就是死循环；判成不可用会扔掉最像真 bug 的题 |
| 124 | 124 | 不可用 | 两边都跑不完，测不出差别 |
| 2（收集中断） | 0 | 不可用 | 测试根本没跑起来，不是「测出失败」 |
| 0 | 0 | 不可用 | 退回源码仍全过，说明测试没覆盖这处改动 |
| 任意 | ≠0 | 不可用 | 环境本身有问题 |

`124` 是本工具自己的超时码（不是 pytest 的）；pytest 的 `2` 是「被中断/收集错误」。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time

#: 源码目录前缀。不同仓库布局不同（`fastapi/` 平铺、`src/` 型、`rich/` 型）。
DEFAULT_SRC_PREFIXES = ("fastapi/", "httpx/", "requests/", "rich/", "src/")
#: 测试目录前缀。同样是**假设**：有的仓库把测试放在 `tests/`，有的放在根目录或 `test/`。
#: 实测：一个测试放在根目录的仓库，工具报「该提交没有测试改动」—— 而真正的原因是前缀没匹配，
#: 不是提交没改测试。**原因写错了，等于把人引到错的方向。**
DEFAULT_TEST_PREFIXES = ("tests/",)
TIMEOUT_EXIT = 124

#: 判据的**语义版本**。判据的规则一改（例如「红是挂住也算可用」「跳过不算通过」），就要往上跳一位。
#:
#: 为什么要它：离线复核已经存下来的题集时发现 —— 同一份 `candidates.jsonl` 里有 9 条记着
#: 「超时：usable=False」，而**当**前代码同一条会判「红是挂住 ⇒ usable=True」。
#: 结论变了，文件没变，而文件里没有任何东西说明它是哪一版判据产出的。
#: 于是「这道题能不能用」变成了一个**取决于你什么时候跑的问题**。
JUDGE_VERSION = "2026-11-23.4"


def sh(argv, cwd=None, timeout=1800):
    """跑子进程；超时返回 124 而不是抛异常。

    **一个没有超时出口的执行器，会把「一道题挂了」变成「整批没跑」。**
    实测：一道 `tests/test_cells.py` 挂住的题，把整批 40 个候选的扫描打断在第 6 个。
    """
    env = dict(os.environ)
    # 子进程不要在工作树里留 __pycache__：它会出现在 `git status` 里，
    # 也会让「跑一次评测」这件事产生被测对象之外的副作用。
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    try:
        proc = subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, stdin=subprocess.DEVNULL, env=env)
    except subprocess.TimeoutExpired:
        return TIMEOUT_EXIT, "", f"超过 {timeout:g} 秒被中断"
    return proc.returncode, proc.stdout, proc.stderr


def git(repo, *args, timeout=600):
    code, out, err = sh(["git", "-C", str(repo), *args], timeout=timeout)
    return code, out.strip(), err.strip()


def classify(red_exit: int, green_exit: int, red_ids: list[str],
             collection_broken: bool = False,
             red_stats=None, green_stats=None) -> tuple[bool, str]:
    """红/绿两阶段读数 → 「这道题成立吗」。返回 (usable, 理由)。

    纯函数，无副作用 —— 因为这套判据我连续错三次（见模块文档的表），
    必须能用单元测试逐条钉住，而不是靠在真实仓库上跑一遍看结果。
    """
    # **绿侧守卫必须排在所有特例之前。**
    # 实测（audit 一份 40 行的旧题集）：`red=124, green=0` 那条「挂死也算 bug」的特例
    # 直接 return 了，于是「绿相位一条都没跑」这件事根本没被检查 —— 特例绕过了通用守卫。
    if green_exit == 0:
        missing, why = real_pass_missing(green_stats)
        if missing:
            return False, (f"绿不算通过：{why}（passed={(green_stats or {}).get('passed')}、"
                           f"skipped={(green_stats or {}).get('skipped')}）")

    if red_exit == TIMEOUT_EXIT and green_exit == 0:
        return True, "红是挂住、绿是通过 ⇒ 可用（这个 bug 的表现就是挂死）"
    if red_exit == TIMEOUT_EXIT or green_exit == TIMEOUT_EXIT:
        return False, f"超时（red={red_exit} green={green_exit}）：两边都跑不完，测不出差别"
    # 「全跳过」要**先于**「退回源码后仍全过」判掉。
    # 否则一条都没跑也会落到那句「测试没覆盖这处改动」上 —— 结论对，理由错，
    # 而理由会把人引到「去补测试覆盖」这条错路（实测：scan 在一个全 skip 的仓库上就是这么说的）。
    if (red_stats and red_stats.get("total", 0) > 0
            and red_stats.get("passed", 0) == 0
            and red_stats.get("skipped") == red_stats.get("total")):
        return False, (f"这条题的用例全被跳过（{red_stats.get('total')} 条，跑 0 条）："
                       f"测不出东西 —— 跳过不等于通过")
    if red_exit == 0:
        return False, "退回源码后仍全过（测试没覆盖这处改动）"
    if green_exit != 0:
        return False, f"恢复后仍不过（环境问题，pytest exit {green_exit}）"
    if red_exit != 1 and collection_broken:
        return False, f"红是收集错误（pytest exit {red_exit}）：测试根本没跑起来，不是「测出失败」"
    if red_exit != 1:
        return False, f"红不是「有测试失败」（pytest exit {red_exit}）"
    if not red_ids:
        return False, "红是 exit 1 但解析不到用例 id（输出里没有 FAILED 行）"
    # 「跳过」既不是「通过」也不是「失败」：全 skip 的题两边都绿，但一条都没测。
    # 实测（副机 L94 / 桥裁判）：让用例全 skip 的补丁让 pytest exit 0，被判 passed。
    if (red_stats and red_stats.get("total", 0) > 0 and red_stats.get("passed", 0) == 0
            and red_stats.get("skipped") == red_stats.get("total")):
        return False, "红是全跳过：一条都没真跑，这种「红」测不出东西"
    return True, "红→绿成立"


def restore(repo: pathlib.Path) -> None:
    """非破坏性恢复：先取消暂存、再还原工作树。

    不用 `checkout HEAD -- <files>`：该提交若新增了文件，checkout 会按 pathspec 失败，
    **而失败是部分生效的** —— 暂存区留一半改动，下一个候选的「工作树是否干净」守卫就会误判。
    """
    sh(["git", "-C", str(repo), "reset", "-q", "HEAD", "--", "."])
    sh(["git", "-C", str(repo), "checkout", "--", "."])


def collection_broken_from(output) -> bool:
    """这次 pytest 输出里有没有「收集阶段就错」。

    为什么要单独判：`--continue-on-collection-errors` 下 pytest 会以 **exit 1** 退出，
    报告里既有 `ERROR tests/x.py` 也有 `FAILED`。只看退出码会把「测试根本没跑起来」
    当成「测出失败」——一个桶装两件事。

    也不能只看尾部 3 行：收集错误清单可能很长，真错误在中间。
    """
    text = output or ""
    if "during collection" in text:
        return True
    # 只认**收集阶段**的错误行：`ERROR tests/x.py`（路径里没有 `::`）。
    # `ERROR tests/x.py::test_y` 是用例级错误（fixture/setup 抛异常）—— 那是真实的失败信号，
    # 把它算成「没测成」会把「这道题做错了」悄悄从分母里拿掉。
    for line in text.splitlines():
        m = re.match(r"^E?RROR\s+(\S+)", line)
        if m and "::" not in m.group(1):
            return True
    return False


def regression_verdict(rcode: int, new_list) -> tuple[str | None, str | None]:
    """回归检查的结论。返回 (要改成的状态, 原因)；None 表示「不改变题目本身的结论」。

    四种退出码都吃过亏（`exit 4` 路径不存在、`124` 挂住、`5` 一条没收集到、`2` 收集错误）：
    它们都会让 `FAILED` 行为空，于是 `new_list` 为空，闸就悄悄放过去了。
    **回归检查没跑成，等于没查** —— 这时不能再说「题目通过了」。
    """
    if rcode in (0, 1):
        if new_list:
            return "failed", f"题目测试通过，但相对基线新增 {len(new_list)} 条回归失败：" + "; ".join(new_list[:5])
        return None, None
    if rcode == TIMEOUT_EXIT:
        return "infra_error", f"回归检查在预算内没跑完（挂住，exit {rcode}）—— 等于没查"
    return "infra_error", f"回归检查没跑成（pytest exit {rcode}）—— 等于没查，不能算通过"


#: 绿相位「真跑过几条」的两种失败理由。抽成常量，让 scan 与 run_one 用**同一句话**。
SKIP_GUARD_REASON = "跳过不等于通过"
NO_STATS_REASON = "统计不到执行条数（junit 没写出来或解析失败）—— fail-closed，不能按通过处理"


def test_key(x) -> tuple:
    """用例身份键 = (类名, 用例名)，**不含文件路径**。

    pytest 打印的 id 是 `path::Class::test`，而 junit 的 `file` 属性会把类名塞进路径
    （实测 `tests/test_unfix/TestClassify.py`）—— 两边的路径部分根本不同，只能靠「类 + 用例名」对上。
    """
    x = str(x).replace(chr(92), "/")
    if "::" not in x:
        return ("", x)
    head, *rest = x.split("::")
    name = rest[-1]
    cls = rest[-2] if len(rest) >= 2 else ""
    if not cls:
        stem = head.split("/")[-1]
        stem = stem[:-3] if stem.endswith(".py") else stem
        cls = stem if stem[:1].isupper() else ""
    return (cls, name)


def real_pass_missing(stats) -> tuple:
    """绿相位是不是「一条都没真跑过」。返回 (缺失?, 理由)。

    两条都要拦：

    * `passed == 0` —— 全跳过 / 全被 deselect，**跳过不等于通过**；
    * `total == 0` —— 连 junit 都没写出来或解析失败。这时**不能假设它跑过了**
      （fail-closed）：宁可丢一个候选，也不让一道「没证据」的题进题集。

    这条 fail-closed 是副机 L96 提的。我原来写的是「total==0 就不检查」——
    等于**守卫在拿不到数据时静默消失**，而那正是最该警惕的时刻。
    """
    stats = stats or {}
    if stats.get("total", 0) <= 0:
        return True, NO_STATS_REASON
    if stats.get("passed", 0) == 0:
        return True, SKIP_GUARD_REASON
    return False, ""


def pytest_outcomes(xml_path) -> dict:
    """每个用例的结局：`{"tests/test_x.py::test_a": "passed" | "failed" | "skipped" | "error"}`。

    为什么需要逐条：绿相位原来要求**整个测试文件** exit 0。可真实仓库里同一文件常有
    与本次改动无关的既有失败（rich 基线实测就有 7 条），于是好题被误杀 ——
    实测 `4ca56966`：它自己的目标用例能过，同文件的 `test_decode_example` 等 3 条一直是红的。
    """
    import xml.etree.ElementTree as ET

    out = {}
    p = pathlib.Path(xml_path)
    if not p.is_file():
        return out
    try:
        root = ET.parse(p).getroot()
    except ET.ParseError:
        return out
    for case in root.iter("testcase"):
        name = case.get("name") or ""
        # junit 的 `file` 属性才是 pytest 眼里的路径。`classname` 在「测试目录没有 __init__.py」
        # 时**是空的** —— 实测 tests/test_file_proxy.py 的 classname 为空，于是 id 少了 `tests/`
        # 前缀，目标用例全部匹配不上，绿相位一律判「缺失」、好题全被误杀。
        fpath = (case.get("file") or "").replace(chr(92), "/")
        if fpath:
            tid = f"{fpath}::{name}"
        else:
            cls = (case.get("classname") or "").replace(".", "/")
            tid = f"{cls}.py::{name}" if cls else name
        tags = {child.tag for child in case}
        if "failure" in tags:
            out[tid] = "failed"
        elif "error" in tags:
            out[tid] = "error"
        elif "skipped" in tags:
            out[tid] = "skipped"
        else:
            out[tid] = "passed"
    return out


def pytest_stats(xml_path) -> dict:
    """从 `--junitxml` 里数出「真跑过几条」。

    为什么不能只看退出码：实测（副机 L94 + 桥的裁判）一份让用例**全部 skip** 的补丁
    让 pytest 以 **exit 0** 退出，`run_one` 就判 `passed` ——
    「跳过」和「通过」在退出码上长得一样，而它们对判据的意义相反。
    """
    import xml.etree.ElementTree as ET

    stats = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "error": 0}
    p = pathlib.Path(xml_path)
    if not p.is_file():
        return stats
    try:
        root = ET.parse(p).getroot()
    except ET.ParseError:
        return stats
    for case in root.iter("testcase"):
        stats["total"] += 1
        tags = {child.tag for child in case}
        if "failure" in tags:
            stats["failed"] += 1
        elif "error" in tags:
            stats["error"] += 1
        elif "skipped" in tags:
            stats["skipped"] += 1
        else:
            stats["passed"] += 1
    return stats


def pytest_ids(repo: pathlib.Path, py: pathlib.Path, test_paths, timeout=180, tag="scan"):
    if not test_paths:
        return (5, [], "没有测试文件", False,
                {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "error": 0}, {})
    xml_path = pathlib.Path(repo) / f".unfix-{tag}.junit.xml"
    code, out, err = sh([str(py), "-m", "pytest", *test_paths, "-q", "--no-header",
                         "-p", "no:cacheprovider", f"--junitxml={xml_path}"],
                        cwd=repo, timeout=timeout)
    tail = (out or "").strip().splitlines()[-3:]
    ids = sorted(set(re.findall(r"^FAILED (\S+)", out or "", flags=re.M)))
    stats = pytest_stats(xml_path)
    outcomes = pytest_outcomes(xml_path)
    try:
        xml_path.unlink()
    except OSError:
        pass
    # 除了「收集坏了吗」，再带一份统计：绿/红的用例里到底跑了几条
    return code, ids, " ⏎ ".join(tail), collection_broken_from(out), stats, outcomes


def changed_files(repo: pathlib.Path, commit: str, src_prefixes,
                  test_prefixes=DEFAULT_TEST_PREFIXES) -> tuple[list[str], list[str]]:
    code, out, err = git(repo, "show", "--name-only", "--format=", commit)
    if code != 0:
        raise SystemExit(f"读不到提交 {commit}：{err[:160]}")
    files = [ln for ln in out.splitlines() if ln.strip()]
    src = [f for f in files if f.startswith(tuple(src_prefixes))]
    tests = [f for f in files if f.startswith(tuple(test_prefixes))]
    return src, tests


def scan(repo: pathlib.Path, py: pathlib.Path, limit: int, out_dir: pathlib.Path,
         src_prefixes, test_prefixes=DEFAULT_TEST_PREFIXES, budget_seconds: float = 900.0) -> int:
    code, out, err = git(repo, "log", "--name-only", "--format=@@%h|%s", timeout=900)
    if code != 0:
        raise SystemExit("git log 失败：" + err[:160])

    cands, cur = [], None
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("@@"):
            if cur:
                cands.append(cur)
            head, _, subj = line[2:].partition("|")
            cur = {"commit": head, "subject": subj, "files": []}
        elif cur is not None:
            cur["files"].append(line)
    if cur:
        cands.append(cur)

    keep = []
    for c in cands:
        src = [f for f in c["files"] if f.startswith(tuple(src_prefixes))]
        tst = [f for f in c["files"] if f.startswith(tuple(test_prefixes))]
        if src and tst:
            keep.append({"commit": c["commit"], "subject": c["subject"],
                         "src_files": src, "test_files": tst})
    print(f"候选（源码+测试同改）：{len(keep)}；本轮验证前 {min(limit, len(keep))}", flush=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    t_start = time.monotonic()
    stopped = False
    for i, cand in enumerate(keep[:limit], 1):
        # 预算闸：到点就停手，已经验完的照样写出去。
        # 实测：8 道题扫了 553 秒，其中 3 道各卡满 180 秒超时 —— **两个慢题就能把整轮拖死**，
        # 而被杀掉的进程什么都不留（bench 那边已经吃过这个教训）。
        if budget_seconds is not None and (time.monotonic() - t_start) > budget_seconds:
            stopped = True
            print(f"  预算用尽（>{budget_seconds:g}s）：还剩 {len(keep[:limit]) - i + 1} 道没验，"
                  f"已验的照常写出", file=sys.stderr)
            break
        rec = dict(cand)
        rec.update({"judge_version": JUDGE_VERSION,
                    "red": {}, "green": {}, "fail_to_pass": [], "usable": False,
                    "fail_mode": "assert", "why": None})
        code, dirty, _ = git(repo, "status", "--porcelain", "--untracked-files=no")
        if dirty:
            rec["why"] = "工作树不干净（跟踪文件有改动）"
        else:
            try:
                # **整棵 worktree**，不是局部 checkout。
                #
                # 局部 checkout 有两个毛病，都是实测出来的：
                # 1. 只回退源码、测试留 HEAD ⇒ 红相位跑的是 HEAD 的测试，
                #    于是 c2 会**冒领** c3 才写的用例（归因错）；
                # 2. 连测试也退回该提交后，其他模块仍停在 HEAD（可能几千个提交之后），
                #    历史版本自己配不齐 ⇒ 绿相位「恢复后仍不过」，好题被误杀。
                #
                # 所以两个相位各用一棵完整的树：红 = `commit^` + 该 commit 的测试；绿 = `commit` 整棵。
                wt_root = pathlib.Path(out_dir) / "_wt"
                wt_root.mkdir(parents=True, exist_ok=True)
                tag = str(cand["commit"])[:12]
                wt_red = wt_root / f"red-{tag}"
                wt_green = wt_root / f"green-{tag}"
                ok, err = _make_worktree(repo, f"{cand['commit']}^", wt_red)
                if not ok:
                    rec["why"] = f"建红相位 worktree 失败：{err[:120]}"
                else:
                    # 把该提交自己的测试放进红相位（新增的测试文件会因此出现，正是要的）
                    git(wt_red, "checkout", cand["commit"], "--", *cand["test_files"])
                    rc, rids, rtail, rbroken, rstats, _rout = pytest_ids(
                        wt_red, py, cand["test_files"], tag="red")
                    rec["red"] = {"exit": rc, "failed_ids": rids, "tail": rtail, "stats": rstats}
                    rec["red"]["collection_broken"] = rbroken
                    _drop_worktree(repo, wt_red)

                    ok, err = _make_worktree(repo, cand["commit"], wt_green)
                    if not ok:
                        rec["why"] = f"建绿相位 worktree 失败：{err[:120]}"
                    else:
                        gc, gids, gtail, _gbroken, gstats, gout = pytest_ids(
                            wt_green, py, cand["test_files"], tag="green")
                        rec["green"] = {"exit": gc, "failed_ids": gids, "tail": gtail, "stats": gstats}
                        _drop_worktree(repo, wt_green)
                        # **fail_to_pass 的定义**（SWE-bench 口径）：
                        #   红相位失败、绿相位通过的用例。
                        # 同文件里与本题无关的既有失败会**两边都失败**，因此自然被排除，
                        # 不会连坐（实测 rich 4ca56966：同文件 3 条既有失败，旧实现把这道好题扔了）。
                        #
                        # junit 的 `file` 是相对 rootdir 的 basename（实测 file="test_file_proxy.py"），
                        # 拼不回完整 id，所以按「文件名::用例名」比对。歧义会表现为「缺失」→ 拒绝，不会假通过。
                        # 比对键 = (文件名, 类名, 用例名)。
                        # 只按「文件名::用例名」比会漏掉类：junit 的 `name` 不含类名，
                        # 而 pytest 输出的 id 是 `path::Class::test`，两边对不上 ——
                        # 实测 8e01d37：绿相位明明 74 passed，却被判「没有一条变成通过」。
                        # 比对键 = (类名, 用例名)，**不含文件路径**。
                        # pytest 9 的 junit `file` 会把类名塞进路径（实测
                        # `tests/test_unfix/TestClassify.py`），而 `FAILED` 行是
                        # `tests/test_unfix.py::TestClassify::test_x` —— 两者的路径部分根本不同。
                        # 只留「类 + 用例名」，两边才对得上。
                        gout_short = {test_key(k): v for k, v in gout.items()}
                        rout_short = {test_key(k): v for k, v in (_rout or {}).items()}
                        red_ids_norm = [str(t).replace(chr(92), '/') for t in rids]
                        f2p = [t for t in red_ids_norm
                               if gout_short.get(test_key(t)) == 'passed']
                        skipped_targets = [t for t in red_ids_norm
                                           if gout_short.get(test_key(t)) == 'skipped']
                        # 回归：红相位**通过**的用例，绿相位不许变成失败
                        regressions = [k for k, v in rout_short.items()
                                       if v == 'passed' and gout_short.get(test_key(k)) == 'failed']
                        rec["fail_to_pass"] = f2p
                        rec["green"]["targets_ok"] = bool(f2p) and not regressions
                        if f2p and not regressions:
                            gc_for_classify = 0
                        else:
                            gc_for_classify = 1
                            if not f2p:
                                rec["green"]["no_flip"] = dict(list(
                                    (t, gout_short.get(test_key(t), '缺失')) for t in red_ids_norm)[:5])
                            if regressions:
                                rec["green"]["regressions"] = regressions[:5]
                        usable, why = classify(rc, gc_for_classify, red_ids_norm, rbroken,
                                               red_stats=rstats, green_stats=gstats)
                        rec["classify_why"] = why  # audit 用这一句复现，别用被美化过的 why
                        # 绿相位给 classify 的是**合成**过的退出码（只看目标用例翻没翻），
                        # 也必须存下来，否则 audit 用原始 exit 重算会得出另一句话。
                        rec["classify_green_exit"] = gc_for_classify
                        # 把更贴近现象的理由放在前面（原来的「环境问题」会把人引到错的方向）
                        if not usable and rc != TIMEOUT_EXIT:
                            # 「收集错误」要排在所有理由之前：测试根本没跑起来时，
                            # 说「恢复后仍不过（环境问题）」会把人引去查依赖（实测 1710da7 就是这样）。
                            if rbroken:
                                why = (f"红相位是收集错误（pytest exit {rc}）：测试根本没跑起来，"
                                       f"不是「测出失败」")
                            if skipped_targets and not f2p:
                                why = f"绿相位里目标用例被跳过（{len(skipped_targets)} 条）：跳过不等于通过"
                            elif not f2p and red_ids_norm:
                                why = (f"红相位失败的 {len(red_ids_norm)} 条用例在绿相位里没有一条变成通过"
                                       f"（同文件既有失败不算）")
                            elif regressions:
                                why = f"有回归：红相位通过的 {len(regressions)} 条用例在绿相位失败了"
                        rec["usable"] = usable
                        rec["why"] = why
                        if usable and rc == TIMEOUT_EXIT:
                            rec["fail_mode"] = "hang"
                        if usable:
                            rec["fail_to_pass"] = f2p  # 只认「红了又绿」的那些
            finally:
                restore(repo)
        rows.append(rec)
        print(f"[{i}/{min(limit, len(keep))}] {rec['commit']} usable={rec['usable']} "
              f"ftp={len(rec['fail_to_pass'])} {rec['why']} | {rec['subject'][:40]}", flush=True)
        with (out_dir / "candidates.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    tasks = [{"task_id": f"{repo.name}__{r['commit']}", "commit": r["commit"],
              "subject": r["subject"], "src_files": r["src_files"],
              "test_files": r["test_files"], "fail_to_pass": r["fail_to_pass"],
              "fail_mode": r["fail_mode"]} for r in rows if r["usable"]]
    with (out_dir / "tasks_unfix.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for t in tasks:
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")
    print(f"完成：候选 {len(keep)}，验证 {len(rows)}，可用 {len(tasks)} → {out_dir}", flush=True)
    return 0


def new_failures(failed_ids, known_ids) -> list[str]:
    """回归检查：这批失败里，哪些是基线里没有的（= 真的新增了回归）。

    分隔符必须归一化再比：pytest 在 Windows 上打 `tests\\a.py::t`，而人和文档写 `tests/a.py::t`。
    不归一化的话每条已知失败都会被算成新增，闸永远红 —— **一个永远红的闸等于没有闸**。
    """
    norm = lambda s: s.replace("\\", "/")
    known = {norm(k) for k in (known_ids or ())}
    return [i for i in (failed_ids or ()) if norm(i) not in known]


#: 不属于测试目录、但**会改变判据**的文件（按 basename 匹配）。
#: 实测（副机 L89/L90）：只往仓库根加一个 `conftest.py`、里面把用例全 skip，
#: 一道本来是红的题就变成 `passed` —— 而它不在 `tests/` 前缀下，前缀守卫看不见它。
JUDGMENT_PATHS = ("conftest.py", "pytest.ini")

#: 这些文件**只要被动过**就算动了判据。
#:
#: 第一版是「只有出现 `[pytest]` / `[tool:pytest*]` 段头才算」，被副机 L92 的 E1 打穿：
#: `tox.ini` 里本来就有 `[pytest]` 段，补丁在**远离段头的行**追加
#: `addopts = -k "not test_b"` —— diff 里看不到段头，守卫放行，
#: 而 pytest 会读它、把唯一失败的用例 deselect 掉，`failed` 就变 `passed`。
#: 结论：**对配置文件做内容判断不可靠**（diff 里没有上下文）。改成一律算。
JUDGMENT_CONFIG_FILES = ("tox.ini", "setup.cfg", "pyproject.toml", "pytest.ini")


def conftest_imported_modules(repo) -> set:
    """仓库里所有 `conftest.py` 引用的**本地**模块名（顶格 import / from）。

    为什么需要它（副机 L92 的 E2）：根 `conftest.py` 从旁边的 `judge_helper.py` import hook，
    补丁**只改 `judge_helper.py`** —— 补丁里没有 conftest、没有测试前缀、没有配置段头，
    守卫返回 `[]`；而改后的 hook 把全部用例 skip，`status` 就变 `passed`。

    这是有界静态分析：只看 conftest 里的 `import x` / `from x import y` 的**第一段**。
    不追踪多层依赖 —— 所以它不是完备的，只是把最直接的那条堵上。
    """
    if repo is None:
        return set()
    root = pathlib.Path(repo)
    mods = set()
    try:
        conftests = list(root.rglob("conftest.py"))
    except OSError:
        return set()
    for p in conftests:
        parts = set(p.parts)
        if ".git" in parts or "node_modules" in parts or ".venv" in parts:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r"(?m)^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", text):
            mods.add(m.group(1))
    return mods


def _paths_in_patch(patch_text: str) -> list:
    """补丁改到的文件路径。

    路径首选 `diff --git a/<x> b/<y>`；`+++ b/<x>` **只有在上一行是 `--- a/<x>` 头时**
    才算文件头 —— 否则 hunk 里一行内容 `+++ b/pyproject.toml` 会被当成文件头（实测误杀）。
    """
    paths = []
    cur = None
    prev_is_minus = False
    for line in (patch_text or "").splitlines():
        m = re.match(r"^diff --git a/(\S+) b/(\S+)$", line)
        if m:
            cur = m.group(2)
            prev_is_minus = False
            if cur not in paths:
                paths.append(cur)
            continue
        if line.startswith("--- a/") or line.startswith("--- /dev/null"):
            prev_is_minus = True
            continue
        if line.startswith("+++ b/") and prev_is_minus:
            cur = line[6:].strip()
            prev_is_minus = False
            if cur not in paths:
                paths.append(cur)
            continue
        prev_is_minus = False
    return paths


def patch_touches_judgment(patch_text: str, test_prefixes, repo=None) -> list:
    """补丁有没有动到**判据**（测试文件、conftest、pytest 配置、conftest 引用的模块）。

    判据（fail-to-pass）来自测试。被评测的 agent 只要改判据，就能让任何实现变绿。
    四类都算：

    1. 测试前缀下的文件（`tests/...`）；
    2. 任意位置的 `conftest.py` / `pytest.ini`；
    3. `tox.ini` / `setup.cfg` / `pyproject.toml` / `pytest.ini` —— **动就算**（见上面注释的理由）；
    4. `conftest.py` 里 import 的本地模块（有界静态分析，见 `conftest_imported_modules`）。
    """
    prefixes = tuple(test_prefixes or DEFAULT_TEST_PREFIXES)
    imported = conftest_imported_modules(repo)
    hit = []
    for path in _paths_in_patch(patch_text):
        if path in ("/dev/null", ""):
            continue
        base = path.rsplit("/", 1)[-1]
        stem = base[:-3] if base.endswith(".py") else base
        if (
            path.startswith(prefixes)
            or base in JUDGMENT_PATHS
            or base in JUDGMENT_CONFIG_FILES
            or (imported and stem in imported)
        ):
            hit.append(path)
    return sorted(hit)


def patch_touches_tests(patch_text: str, test_prefixes) -> list[str]:
    """向后兼容的别名：语义已扩为「动到判据」，见 `patch_touches_judgment`。"""
    return patch_touches_judgment(patch_text, test_prefixes)


def run_one(repo: pathlib.Path, py: pathlib.Path, commit: str, tests: list[str],
            out_dir: pathlib.Path, apply_gold: bool, patch: str | None,
            timeout: float, src_prefixes, test_prefixes=DEFAULT_TEST_PREFIXES,
            regression_tests=None, known_failures=(), strict_head=False,
            fail_to_pass=None) -> int:
    started = time.monotonic()
    res = {"task_id": commit, "reason": None, "notes": None, "status": "infra_error",
           "duration_seconds": None, "steps": {}}

    def finish(status, reason=None, notes=None):
        res["status"], res["reason"], res["notes"] = status, reason, notes
        res["duration_seconds"] = round(time.monotonic() - started, 2)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{commit}.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(res, ensure_ascii=False))
        return {"passed": 0, "failed": 1, "infra_error": 2}[status]

    if not py.is_file():
        return finish("infra_error", f"找不到解释器：{py}")
    code, dirty, _ = git(repo, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        return finish("infra_error", "工作树不干净（跟踪文件有改动），先清理")

    # **HEAD 闸**：工作树 HEAD 必须就是 `--commit`，否则红相位会混用两个版本。
    # 实测（副机 L107）：`--commit 4e4ab38` 而工作树在 HEAD 时，红相位是
    # 「4e4ab38^ 的源码 + HEAD 的测试」（76 vs 77 条用例）—— 判分对象错了却不报错。
    code, head, _ = git(repo, "rev-parse", "HEAD")
    code2, want, _ = git(repo, "rev-parse", commit)
    head_s, want_s = (head or "").strip(), (want or "").strip()
    mixed = bool(code == 0 and code2 == 0 and head_s and want_s and head_s != want_s)
    if mixed:
        # 记下来。**默认只记不拦**：存量用例与「在 main 上评旧提交」的流程都依赖能跑。
        # 真实实验（bench / 对外读数）应加 --strict-head，把它变成硬失败。
        res["steps"]["head_mismatch"] = {"repo_head": head_s[:12], "commit": want_s[:12]}
        if strict_head:
            return finish(
                "infra_error",
                f"工作树 HEAD（{head_s[:12]}）与 --commit（{want_s[:12]}）不一致："
                f"红相位会混用两个版本的测试，读数不可信。"
                f"先 `git worktree add --detach <目录> {commit}`，再在那个目录里跑")

    src, tests_changed = changed_files(repo, commit, src_prefixes, test_prefixes)
    if not src:
        return finish("infra_error",
                      f"该提交里没有文件匹配源码前缀 {list(src_prefixes)}；"
                      f"若这个仓库布局不同，用 --src-prefixes 指定")
    if not tests_changed:
        return finish("infra_error",
                      f"该提交里没有文件匹配测试前缀 {list(test_prefixes)}，反解不出 fail-to-pass；"
                      f"若测试不在这些目录下，用 --test-prefixes 指定")

    # 跑之前先对齐「你传的测试文件」与「这个提交改的测试文件」。
    #
    # 为什么必须查：传错测试文件时，**退回源码后那些测试照样全过**，于是正控负控都绿，
    # 一道根本不成立的题看起来完全正常 —— 这是题集层面的假绿。
    # 实测踩到：`648ceda2` 改的是 `tests/test_file_proxy.py`，我传了 `tests/test_cells.py`，
    # 负控直接 passed。
    wanted = {pathlib.PurePosixPath(p).as_posix() for p in tests_changed}
    given = {pathlib.PurePosixPath(p).as_posix() for p in tests}
    if not (wanted & given):
        return finish(
            "infra_error",
            f"传给 --tests 的文件与这个提交改的测试文件不相交；"
            f"这个提交改的是：{'、'.join(sorted(wanted))}")

    broken = False
    try:
        code, _, err = git(repo, "checkout", f"{commit}^", "--", *src)
        res["steps"]["revert"] = {"exit": code, "files": src}
        if code != 0:
            return finish("infra_error", f"退回源码失败：{err[:160]}")

        if patch:
            # 先查这个补丁有没有动测试。动了就不评 —— 因为判据来自测试。
            try:
                patch_text = pathlib.Path(patch).read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return finish("infra_error", f"读不到补丁 {patch}：{exc}")
            bad = patch_touches_judgment(patch_text, test_prefixes, repo)
            res["steps"]["patch_check"] = {"touched_tests": bad}
            if bad:
                # 不是「没通过」，是**这份补丁不属于被评测的范围**：它改的是判据本身。
                joined = "、".join(bad)
                prefixes = tuple(test_prefixes or DEFAULT_TEST_PREFIXES)
                only_tests = all(p.startswith(prefixes) for p in bad)
                if only_tests:
                    # 最常见的一种：直接改测试文件。措辞保持具体（下游用例与文档按这句断言）。
                    return finish("infra_error",
                                  f"补丁修改了测试文件：{joined}。"
                                  f"判据来自测试，改测试等于改判据 —— 这份补丁不能评")
                return finish("infra_error",
                              f"补丁改动了判据：{joined}。"
                              f"判据来自测试（conftest / pytest 配置也会改变判据）—— 这份补丁不能评")
            code, _, err = sh(["git", "-C", str(repo), "apply", patch])
            res["steps"]["apply"] = {"source": "指定补丁", "exit": code}
            if code != 0:
                return finish("infra_error", f"打补丁失败：{err[:160]}")
        elif apply_gold:
            # gold = **HEAD 的源码**，不是 `<commit>` 的源码：测试取自 HEAD，
            # 而该提交之后同一文件可能又被改过（实测 16 道里 2 道因此不过）。
            code, _, err = git(repo, "checkout", "HEAD", "--", *src)
            res["steps"]["apply"] = {"source": "gold（HEAD 的源码）", "exit": code}
            if code != 0:
                return finish("infra_error", f"应用 gold 失败：{err[:160]}")
        else:
            res["steps"]["apply"] = {"source": None, "note": "负控：不打补丁"}

        # **红相位**：打补丁之前先量一次「此刻哪些用例是红的」。
        #
        # 不加这一步，「同文件里另有既有失败」的题会把**正确的补丁**判成 failed ——
        # 实测（夹具 samefile）：目标用例 test_target 被修好，同文件 test_always_red 永远红，
        # 旧实现直接 `status=failed`。这会系统性低估模型，而且和 `scan` 的口径分叉。
        out_dir.mkdir(parents=True, exist_ok=True)
        red_xml = pathlib.Path(out_dir) / f"{commit}.red.junit.xml"
        rc, _rout, _rerr = sh([str(py), "-m", "pytest", *tests, "-q", "--no-header",
                               "-p", "no:cacheprovider", f"--junitxml={red_xml}"],
                              cwd=repo, timeout=timeout)
        red_outcomes = pytest_outcomes(red_xml)
        res["steps"]["red"] = {"exit": rc,
                               "failed": sorted(k for k, v in red_outcomes.items()
                                                if v in ("failed", "error")),
                               "stats": pytest_stats(red_xml)}
        try:
            red_xml.unlink()
        except OSError:
            pass

        t0 = time.monotonic()
        xml_path = pathlib.Path(out_dir) / f"{commit}.junit.xml"
        code, out, _ = sh([str(py), "-m", "pytest", *tests, "-q", "--no-header",
                           "-p", "no:cacheprovider", f"--junitxml={xml_path}"],
                          cwd=repo, timeout=timeout)
        stats = pytest_stats(xml_path)
        res["steps"]["pytest"] = {"exit": code, "seconds": round(time.monotonic() - t0, 1),
                                  "tail": " ⏎ ".join((out or "").strip().splitlines()[-3:]),
                                  "stats": stats}
        tail = res["steps"]["pytest"]["tail"]
        # **同一个 exit 1 可以是两件事**：有测试失败，或者收集阶段就出错。
        # `scan` 早就用 `collection_broken_from` 分开过，但 `run_one`（评补丁走的就是这条）
        # 一直只看退出码 —— 于是带收集错误的红被记成 `failed`，甚至全过 + 收集错误被记成 `passed`。
        # 实测（副机 L88，报告 220）：
        #   `2 passed, 1 error` + exit 1  → 报成 failed（应该 infra_error）
        #   全过但 collect 有错          → 报成 passed（应该 infra_error）
        broken = collection_broken_from(out)
        res["steps"]["pytest"]["collection_broken"] = broken

        # 逐条比对：**红的那几条现在是否都过了**，以及**原本绿的有没有被弄红**。
        # 只在整文件非 0 时才用它 —— exit 0 就是最干净的通过，不必再算。
        green_outcomes = pytest_outcomes(xml_path)
        red_failed = res["steps"]["red"]["failed"]
        key = lambda x: test_key(x)  # noqa: E731
        g_by_key = {key(k): v for k, v in green_outcomes.items()}
        now_green = [tid for tid in red_failed if g_by_key.get(key(tid)) == "passed"]
        still_red = [tid for tid in red_failed if g_by_key.get(key(tid)) != "passed"]
        r_by_key = {key(k): v for k, v in red_outcomes.items()}
        regressions = [k for k, v in r_by_key.items()
                       if v == "passed" and g_by_key.get(k) in ("failed", "error")]
        res["steps"]["flip"] = {"red_failed": len(red_failed), "now_passed": len(now_green),
                                "still_red": still_red[:5], "regressions": regressions[:5]}
        targets_all_green = bool(red_failed) and not still_red and not regressions

        # 回归检查：题目测试过了，不代表没把库弄坏。
        #
        # 这是本工具的一个**真实假绿入口**：`--tests` 只跑那道题相关的文件。
        # 一个补丁完全可能「修好这道题、弄坏别处」，而这里照样判 passed。
        # 实测：我自己的四份补丁跑 rich 全量 976 passed，但基线本来就有 7 条既有失败 ——
        # 所以这里**不能要求全绿**，只能比较「相对基线有没有新增」。
        if regression_tests and code == 0:
            t0 = time.monotonic()
            rcode, rout, _ = sh([str(py), "-m", "pytest", *regression_tests, "-q", "--no-header",
                                 "-p", "no:cacheprovider"], cwd=repo, timeout=timeout)
            rids = sorted(set(re.findall(r"^FAILED (\S+)", rout or "", flags=re.M)))
            new_list = new_failures(rids, known_failures)
            res["steps"]["regression"] = {
                "exit": rcode, "seconds": round(time.monotonic() - t0, 1),
                "failed": rids, "new_failures": new_list,
                "known_failures": sorted(known_failures),
                "tail": " ⏎ ".join((rout or "").strip().splitlines()[-3:]),
            }
            verdict, why = regression_verdict(rcode, new_list)
            if verdict:
                # 两种都算「题目不能判通过」：真回归 / 回归检查没跑成
                return finish(verdict, why)
    finally:
        restore(repo)

    if broken:
        # 收集错误时，测试根本没跑全 —— 这个结论无论退出码是 0 还是 1 都不成立。
        return finish("infra_error",
                      f"不是「测试失败」，是收集阶段就出错（pytest exit {code}）：{tail}")
    if code == 0:
        # 「跳过」和「通过」在退出码上都是 0，但对判据的意义相反。
        # 一条都没真跑过却判 passed，等于让任何模型都能靠 skip 拿满分。
        missing, why = real_pass_missing(stats)
        if missing:
            return finish("infra_error",
                          f"绿不算通过：{why}（total={stats.get('total')}、"
                          f"passed={stats.get('passed')}、skipped={stats.get('skipped')}、"
                          f"error={stats.get('error')}）")
        return finish("passed", notes="全部测试通过")
    if code == 1:
        # 整文件还有失败时，**这道题到底考哪几条**才是判据。
        # 这个信息只能来自题目元数据（`candidates.jsonl` 里的 `fail_to_pass`）：
        # 「红相位里所有失败」不等于「这道题的失败」—— 同文件里可能有与本题无关的既有失败
        # （实测夹具 samefile：正确补丁被判 failed）。
        if fail_to_pass:
            want = {test_key(x) for x in fail_to_pass}
            not_green = [x for x in fail_to_pass
                         if g_by_key.get(test_key(x)) != "passed"]
            if not not_green and not regressions:
                return finish("passed",
                              notes=(f"题目要的 {len(want)} 条用例全部通过"
                                     f"（同文件另有既有失败：{tail}）"))
            return finish("failed",
                          f"题目要的用例仍有 {len(not_green)} 条没过："
                          f"{'、'.join(not_green[:3])} —— {tail}")
        if targets_all_green:
            flip = res["steps"]["flip"]
            return finish("passed",
                          notes=(f"目标用例全部翻绿（{flip['now_passed']}/{flip['red_failed']}），"
                                 f"同文件另有既有失败：{tail}"))
        return finish("failed", f"有测试失败：{tail}")
    if code == TIMEOUT_EXIT:
        return finish("failed", f"测试在 {timeout:g} 秒内没跑完（挂住）—— 这个 bug 还在：{tail}")
    return finish("infra_error", f"不是「没通过」，是没测成（pytest exit {code}）：{tail}")


def _make_worktree(repo, ref, path):
    """在 `path` 上物化一个 detached worktree。返回 (ok, 错误文本)。"""
    code, _o, err = git(repo, "worktree", "add", "--detach", str(path), str(ref))
    return code == 0, err


def _drop_worktree(repo, path):
    git(repo, "worktree", "remove", "--force", str(path))


def audit_rows(rows) -> dict:
    """复核一批已存候选行：判据版本 + 理由是否还对得上当前代码。

    为什么需要：判据改过几轮之后，旧文件里的 `why` 可能已经**不是当前代码会给出的理由**。
    实例（离线复核发现）：同一份 `candidates.jsonl` 里 9 条写着「超时：usable=False」，
    而当前代码同一条会判「红是挂住 ⇒ usable=True」。结论变了、文件没变、文件里没有版本信息 ——
    于是「这道题能不能用」取决于你是**什么时候**跑的。

    返回 `{"rows": n, "versions": {...}, "stale": n, "mismatches": [...]}`。
    """
    versions = {}
    mismatches = []
    missing_stats = 0
    for r in rows:
        ver = r.get("judge_version") or "(无版本)"
        versions[ver] = versions.get(ver, 0) + 1
        red = r.get("red") or {}
        green = r.get("green") or {}
        red_ids = red.get("failed_ids") or []
        broken = bool(red.get("collection_broken")) or "during collection" in (red.get("tail") or "")
        # **必须把 stats 也传进去**：不传就等于用一套比生产更弱的规则重算 ——
        # 「两套判据各修各的」正是本文件反复警告的那类分叉。
        # 旧文件没有 stats 时，`real_pass_missing` 会 fail-closed，audit 就会如实报「证据不足」。
        gp = green.get("stats")
        rp = red.get("stats")
        if not gp and not rp:
            missing_stats += 1
        try:
            usable_now, why_now = classify(red.get("exit"), r.get("classify_green_exit", green.get("exit")), red_ids, broken,
                                           red_stats=rp, green_stats=gp)
        except TypeError:
            continue
        stored_why = (r.get("classify_why") or r.get("why") or "").strip()
        if why_now.strip() != stored_why:
            mismatches.append({"commit": r.get("commit"),
                               "stored_usable": r.get("usable"),
                               "stored_why": stored_why,
                               "usable_now": usable_now,
                               "why_now": why_now})
    stale = sum(v for k, v in versions.items() if k != JUDGE_VERSION)
    return {"rows": len(rows), "versions": versions, "stale": stale,
            "missing_stats": missing_stats, "mismatches": mismatches}


def _capture_run(repo, py, commit, tests, out_dir, apply_gold=False, patch=None,
                 timeout=180.0, src_prefixes=DEFAULT_SRC_PREFIXES,
                 test_prefixes=DEFAULT_TEST_PREFIXES, regression_tests=None,
                 known_failures=()):
    """跑一道题并**返回记录**（而不是打印 + 退码）。给 `bench.py` 用。

    刻意复用 `run_one`：这样 bench 与 CLI 走的是同一条路径，不会出现「两套评分逻辑各修各的」
    ——那种分叉在两周后一定会变成两个数字对不上。

    副作用只有两个，都在 out_dir 里：`<commit>.json` 与 pytest 的临时文件。
    """
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run_one(repo, py, commit, tests, pathlib.Path(out_dir),
                apply_gold=apply_gold, patch=patch, timeout=timeout,
                src_prefixes=src_prefixes, test_prefixes=test_prefixes,
                regression_tests=regression_tests, known_failures=known_failures)
    rec_path = pathlib.Path(out_dir) / f"{commit}.json"
    if not rec_path.is_file():
        return {"status": "infra_error", "reason": f"没写出结果文件：{rec_path}"}
    return json.loads(rec_path.read_text(encoding="utf-8"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="unfix.py")
    # `required=False`：`--selftest` 是一个**不带子命令**的入口。
    # 之前写成 `required=True`，于是 `unfix.py --selftest` 永远报「缺少 cmd」——
    # 而文档里我又把它写成标准用法。写 RUNBOOK 时逐条照着敲才发现：
    # **文档比代码严，被文档打回来的是代码。**
    sub = ap.add_subparsers(dest="cmd", required=False)

    s = sub.add_parser("scan", help="扫全历史，产出可用题集")
    s.add_argument("--repo", required=True)
    s.add_argument("--limit", type=int, default=40)
    s.add_argument("--out", default=None)
    s.add_argument("--python", default=None)
    s.add_argument("--src-prefixes", default=None, help="逗号分隔，默认内置几种布局")
    s.add_argument("--test-prefixes", default=None, help="逗号分隔，默认 tests/")
    s.add_argument("--budget-seconds", type=float, default=900.0,
                    help="整轮时间预算；到点就停并写出已有结果（默认 15 分钟）")

    au = sub.add_parser("audit", help="复核已存的 candidates.jsonl（版本 + 理由漂移）")
    au.add_argument("--file", required=True)
    au.add_argument("--show", type=int, default=10)
    r = sub.add_parser("run", help="跑一道题（正控 / 负控 / 评补丁）")
    r.add_argument("--repo", required=True)
    r.add_argument("--commit", required=True)
    r.add_argument("--tests", nargs="+", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--python", default=None)
    r.add_argument("--apply-gold", action="store_true")
    r.add_argument("--fail-to-pass", default=None,
                   help="这道题要考哪些用例（逗号分隔）。给了就按它判，而不是整个文件")
    r.add_argument("--strict-head", action="store_true",
                   help="工作树 HEAD 与 --commit 不一致时直接判 infra_error（真实实验应当打开）")
    r.add_argument("--patch", default=None)
    r.add_argument("--timeout", type=float, default=120.0)
    r.add_argument("--src-prefixes", default=None)
    r.add_argument("--test-prefixes", default=None, help="逗号分隔，默认 tests/")
    r.add_argument("--regression-tests", nargs="*", default=None,
                   help="题目测试通过后再跑这些（查有没有把别处弄坏）；可传目录")
    r.add_argument("--known-failures", nargs="*", default=[],
                   help="基线里本来就失败的用例 id，回归检查时不算新增")

    ap.add_argument("--selftest", action="store_true", help="跑判据的单元测试")

    args = ap.parse_args(argv)
    args.audit_file = getattr(args, "file", None) if args.cmd == "audit" else None
    args.audit_show = getattr(args, "show", 10)

    if getattr(args, "selftest", False):
        # 用路径加载，不依赖 cwd 是仓库根（在别的目录敲 `--selftest` 也该能跑）
        import unittest

        # 先确认这个解释器里有 pytest。没有的话，`scan`/`run` 相关的用例会以「1 例失败」
        # 的形式出现 —— 那读起来像「代码坏了」，其实是「这台机器的 python 没装 pytest」。
        # 实测：副机用全局 python 3.12 跑 `--selftest`，得到 1 例 FAIL；换项目 venv 立刻 29 OK。
        # 这种「环境缺失长得像代码缺陷」的读数，必须自己先说明白，不能让人去猜。
        try:
            import pytest  # noqa: F401
        except ImportError:
            print("这个解释器里没有 pytest：", sys.executable, file=sys.stderr)
            print("自测需要它（多个用例会起真实 pytest 子进程）。", file=sys.stderr)
            print("请用装了 pytest 的解释器，例如：", file=sys.stderr)
            print(r"  <仓库>\.venv\Scripts\python.exe -X utf8 -B tools\unfix.py --selftest",
                  file=sys.stderr)
            print("（或用 --python 指定）。这不是代码缺陷。", file=sys.stderr)
            return 2

        tests_dir = pathlib.Path(__file__).resolve().parents[1] / "tests"
        suite = unittest.TestLoader().discover(str(tests_dir), pattern="test_unfix.py")
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1

    if getattr(args, "audit_file", None):
        import json as _json
        p = pathlib.Path(args.audit_file)
        rows = [_json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        rep = audit_rows(rows)
        print(f"{p.name}：{rep['rows']} 行；版本分布 {rep['versions']}；"
              f"非当前版本 {rep['stale']} 行")
        if rep["stale"]:
            print(f"  注意：当前判据版本是 {JUDGE_VERSION} —— 旧版本产出的文件，"
                  f"结论可能与现在不同，别直接当题集用")
        if rep["missing_stats"]:
            print(f"  其中 {rep['missing_stats']} 行缺执行统计（旧文件没有 junit 数据）："
                  f"当前判据对它们只能 fail-closed —— **要么重扫，要么当它们不存在**")
        print(f"  理由与当前代码不一致：{len(rep['mismatches'])} 行")
        for m in rep["mismatches"][:args.audit_show]:
            print(f"  - {m['commit']}：旧 usable={m['stored_usable']} why={m['stored_why']}")
            print(f"      今 usable={m['usable_now']} why={m['why_now']}")
        return 0 if not rep["mismatches"] and not rep["stale"] else 1

    if not args.cmd:
        ap.print_help()
        return 2

    prefixes = (tuple(args.src_prefixes.split(",")) if getattr(args, "src_prefixes", None)
                else DEFAULT_SRC_PREFIXES)

    if args.cmd == "scan":
        repo = pathlib.Path(args.repo).resolve()
        py = pathlib.Path(args.python) if args.python else (repo / ".venv/Scripts/python.exe")
        out = pathlib.Path(args.out) if args.out else (repo.parent / f"unfix-sweep-{repo.name}")
        s_test_prefixes = (tuple(args.test_prefixes.split(","))
                           if getattr(args, "test_prefixes", None) else DEFAULT_TEST_PREFIXES)
        return scan(repo, py, args.limit, out, prefixes, s_test_prefixes)

    repo = pathlib.Path(args.repo).resolve()
    py = pathlib.Path(args.python) if args.python else (repo / ".venv/Scripts/python.exe")
    test_prefixes = (tuple(args.test_prefixes.split(",")) if getattr(args, "test_prefixes", None)
                     else DEFAULT_TEST_PREFIXES)
    return run_one(repo, py, args.commit, args.tests, pathlib.Path(args.out),
                   args.apply_gold, args.patch, args.timeout, prefixes, test_prefixes,
                   args.regression_tests, set(args.known_failures or ()),
                   strict_head=args.strict_head,
                   fail_to_pass=([x for x in (args.fail_to_pass or '').split(',') if x] or None))


if __name__ == "__main__":
    raise SystemExit(main())
