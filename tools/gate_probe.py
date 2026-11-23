# GENERATED-BY: agent implementation, 2026-11-23 +08:00
# LAST-EDIT: 2026-11-23 +08:00
# PURPOSE: 对抗矩阵探测评测闸门——单元测试只覆盖「已知的坏形状」，覆盖不到「我们没想到的合法形状」。
# ORIGIN: none（新文件）。诱因：一次探针发现「3 题全 failed、执行器 exit 0」被报成 all_passed，
#         而 pass_rate 明明是 0.0 —— 判据看的是退出码，不是数据。
# CUMULATIVE-DELTA: 首版。九个场景，每个都真的跑一次 `research.py eval`。
"""评测闸门的对抗矩阵探测（研究性实验，不是单元测试）。

问题：我们给 `research.py eval` 装了三道闸——`ok`（覆盖对齐）、`summary.pass_rate`
（题单未对齐就置空）、`verdict`（区分读数与故障）。**它们在「合法但非标准」的执行结果上
会不会误杀？** 单元测试覆盖的是「已知的坏形状」，覆盖不到「我们没想到的合法形状」。

做法：造 N 个执行器场景，每个都真的跑一次 `research.py eval`，把闸门给出的
exit / ok / verdict / pass_rate / aligned / raw 记下来，与「我认为它应该怎么判」对照。
对不上的地方就是缺口——要么闸门太松（假绿），要么太紧（误杀）。

用法：
    python -X utf8 -B gate_probe.py            # 跑矩阵，打印表格
    python -X utf8 -B gate_probe.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
PY = sys.executable
sys.path.insert(0, str(REPO))

MAKE_PROFILE = REPO / "tools" / "make_profile.py"
RESEARCH = REPO / "research.py"

TASKS_3 = [{"task_id": f"g-{i}", "statement": "x"} for i in (1, 2, 3)]


def toml_quote(x: str) -> str:
    return json.dumps(x)


def write(path: pathlib.Path, text: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_tasklist(path: pathlib.Path, rows) -> pathlib.Path:
    return write(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


# ---------------------------------------------------------------------------
# 场景：每个给出 [名字, 期望判定, 桩源码, 题单行]
# 期望判定取值：
#   "block"            —— 闸门应拦住（ok=false 或 verdict 属于故障类）
#   "reading"          —— 这是读数不是故障（not_all_passed 一类）
#   "block_or_reading" —— 两种都可接受，只要不假绿
# ---------------------------------------------------------------------------

def stub(body: str) -> str:
    head = (
        "import sys, json, pathlib\n"
        "tasks = pathlib.Path(sys.argv[sys.argv.index('--tasks')+1])\n"
        "out = pathlib.Path(sys.argv[sys.argv.index('--out')+1])\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "rows = [json.loads(x) for x in tasks.read_text(encoding='utf-8-sig').splitlines() if x.strip()]\n"
    )
    return head + body


SCENARIOS = [
    # ---- 对照组：完全正常 ----
    dict(
        name="A 基线：3 题全通过",
        expect="reading",
        stub=stub(
            "with (out/'task_results.jsonl').open('w',encoding='utf-8',newline='\\n') as fh:\n"
            "    for r in rows:\n"
            "        fh.write(json.dumps({'task_id': r['task_id'], 'status':'passed'})+'\\n')\n"
        ),
        tasks=TASKS_3,
    ),
    # ---- 已知的坏形状（应当拦住）----
    dict(
        name="B 静默丢最后一题",
        expect="block",
        stub=stub(
            "with (out/'task_results.jsonl').open('w',encoding='utf-8',newline='\\n') as fh:\n"
            "    for r in rows[:-1]:\n"
            "        fh.write(json.dumps({'task_id': r['task_id'], 'status':'passed'})+'\\n')\n"
        ),
        tasks=TASKS_3,
    ),
    dict(
        name="C 写回 0 字节结果文件",
        expect="block",
        stub=stub("(out/'task_results.jsonl').write_text('', encoding='utf-8')\n"),
        tasks=TASKS_3,
    ),
    dict(
        name="D 同一题号写两行",
        expect="block",
        stub=stub(
            "with (out/'task_results.jsonl').open('w',encoding='utf-8',newline='\\n') as fh:\n"
            "    for r in rows:\n"
            "        fh.write(json.dumps({'task_id': r['task_id'], 'status':'passed'})+'\\n')\n"
            "        fh.write(json.dumps({'task_id': r['task_id'], 'status':'failed'})+'\\n')\n"
        ),
        tasks=TASKS_3,
    ),
    dict(
        name="E task_id 写成整数",
        expect="block",
        stub=stub(
            "with (out/'task_results.jsonl').open('w',encoding='utf-8',newline='\\n') as fh:\n"
            "    for i, r in enumerate(rows):\n"
            "        fh.write(json.dumps({'task_id': i+1, 'status':'passed'})+'\\n')\n"
        ),
        tasks=TASKS_3,
    ),
    # ---- 「合法但非标准」：这是本轮真正要问的 ----
    dict(
        name="F 把某题标成 skipped（真实评测常见状态）",
        expect="block_or_reading",
        stub=stub(
            "with (out/'task_results.jsonl').open('w',encoding='utf-8',newline='\\n') as fh:\n"
            "    for i, r in enumerate(rows):\n"
            "        st = 'passed' if i < 2 else 'skipped'\n"
            "        fh.write(json.dumps({'task_id': r['task_id'], 'status': st})+'\\n')\n"
        ),
        tasks=TASKS_3,
    ),
    dict(
        name="G 题号有首尾空格，执行器写 trim 后的",
        expect="block_or_reading",
        stub=stub(
            "with (out/'task_results.jsonl').open('w',encoding='utf-8',newline='\\n') as fh:\n"
            "    for r in rows:\n"
            "        fh.write(json.dumps({'task_id': r['task_id'].strip(), 'status':'passed'})+'\\n')\n"
        ),
        tasks=[{"task_id": " g-1 ", "statement": "x"},
               {"task_id": " g-2 ", "statement": "x"},
               {"task_id": " g-3 ", "statement": "x"}],
    ),
    dict(
        name="H 结果里多一行非 JSON 的元信息",
        expect="block",
        stub=stub(
            "with (out/'task_results.jsonl').open('w',encoding='utf-8',newline='\\n') as fh:\n"
            "    fh.write('# run summary: 3 tasks\\n')\n"
            "    for r in rows:\n"
            "        fh.write(json.dumps({'task_id': r['task_id'], 'status':'passed'})+'\\n')\n"
        ),
        tasks=TASKS_3,
    ),
    dict(
        name="I 全部 failed 但 exit 0",
        expect="reading",
        stub=stub(
            "with (out/'task_results.jsonl').open('w',encoding='utf-8',newline='\\n') as fh:\n"
            "    for r in rows:\n"
            "        fh.write(json.dumps({'task_id': r['task_id'], 'status':'failed'})+'\\n')\n"
        ),
        tasks=TASKS_3,
    ),
]


def build_profile(tmp: pathlib.Path, tag: str, stub_path: pathlib.Path) -> pathlib.Path:
    dest = tmp / f"p-{tag}"
    proc = subprocess.run(
        [PY, "-X", "utf8", "-B", str(MAKE_PROFILE), "--out", str(dest)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError("make_profile 失败：" + proc.stdout + proc.stderr)
    toml_path = dest / "profile.toml"
    text = toml_path.read_text(encoding="utf-8")
    argv = [PY, "-X", "utf8", "-B", str(stub_path), "--tasks", "{tasks}", "--out", "{output}"]
    new_line = "command = [" + ", ".join(toml_quote(a) for a in argv) + "]"
    out_lines = []
    replaced = False
    for line in text.splitlines():
        if line.startswith("command = [") and not replaced:
            out_lines.append(new_line)
            replaced = True
        else:
            out_lines.append(line)
    if not replaced:
        raise RuntimeError("profile 里没找到 command 行")
    toml_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return dest


def run_scenario(tmp: pathlib.Path, sc: dict, idx: int) -> dict:
    tag = f"s{idx}"
    stub_path = write(tmp / f"stub-{tag}.py", sc["stub"])
    profile = build_profile(tmp, tag, stub_path)
    tasks = write_tasklist(tmp / f"t-{tag}.jsonl", sc["tasks"])
    out = tmp / f"run-{tag}"
    proc = subprocess.run(
        [PY, "-X", "utf8", "-B", str(RESEARCH), "eval",
         "--profile", str(profile), "--tasks", str(tasks), "--output", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
        stdin=subprocess.DEVNULL,
    )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return dict(name=sc["name"], expect=sc["expect"], parse_error=True,
                    exit=proc.returncode, raw=proc.stdout[:400] + proc.stderr[:400])
    summary = (report.get("results") or {}).get("summary") or {}
    return dict(
        name=sc["name"],
        expect=sc["expect"],
        exit=proc.returncode,
        ok=report.get("ok"),
        verdict=report.get("verdict"),
        pass_rate=summary.get("pass_rate"),
        raw=summary.get("pass_rate_raw"),
        aligned=summary.get("aligned"),
        read_error=report.get("results_read_error"),
        alignment_error=report.get("alignment_error"),
    )


def classify(rec: dict) -> tuple[str, str]:
    """返回 (实际归类, 说明)。归类：block / reading / green-never-blocked。"""
    if rec.get("parse_error"):
        return "parse_error", "CLI 没吐出 JSON"
    verdict = rec.get("verdict")
    if verdict in ("misaligned", "no_results", "results_invalid", "timed_out", "launch_failed"):
        return "block", verdict
    if verdict == "all_passed" and rec.get("pass_rate") == 1.0 and rec.get("aligned") is not False:
        return "reading", "all_passed"
    if verdict == "not_all_passed":
        return "reading", "not_all_passed"
    return "?", str(verdict)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None, help="把结果写成 JSON")
    args = ap.parse_args(argv)

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="gate-probe-"))
    records = []
    try:
        for i, sc in enumerate(SCENARIOS, start=1):
            rec = run_scenario(tmp, sc, i)
            actual, why = classify(rec)
            rec["actual"] = actual
            rec["why"] = why
            expected = rec["expect"]
            if expected == "block_or_reading":
                ok_judgement = actual in ("block", "reading")
            else:
                ok_judgement = actual == expected
            rec["judgement"] = "OK" if ok_judgement else "MISMATCH"
            records.append(rec)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    hdr = f"{'场景':<38}{'期望':<10}{'实际':<10}{'exit':>5}  {'verdict':<16}{'pass_rate':>10}{'raw':>8}  判定"
    print(hdr)
    print("-" * len(hdr))
    for r in records:
        print(f"{r['name'][:36]:<38}{r['expect']:<10}{r['actual']:<10}"
              f"{str(r.get('exit')):>5}  {str(r.get('verdict')):<16}"
              f"{str(r.get('pass_rate')):>10}{str(r.get('raw')):>8}  {r['judgement']}")
        if r["judgement"] != "OK":
            print(f"    说明：{r.get('why')}  read_error={r.get('read_error')}  "
                  f"alignment_error={r.get('alignment_error')}")

    mism = [r for r in records if r["judgement"] != "OK"]
    print("-" * len(hdr))
    print(f"合计 {len(records)} 个场景：{len(records) - len(mism)} 符合预期，{len(mism)} 不符")
    if args.json:
        write(pathlib.Path(args.json), json.dumps(records, ensure_ascii=False, indent=2))
        print(f"明细已写：{args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
