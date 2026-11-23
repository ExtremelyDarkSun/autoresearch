# GENERATED-BY: agent implementation, 2026-11-22 +08:00
# LAST-EDIT: 2026-11-22 +08:00
# PURPOSE: kernel/autoresearch/evaluation_runner.py 的单元测试（合成 profile + 临时目录）。
# ORIGIN: none（新文件）
# CUMULATIVE-DELTA: 首版。重点验证「配置不合规必须拒绝」与「--force 会留痕」，
#                   以及执行后确实读回了结果（不是只看退出码）。
"""评测运行器（runner 接线）的测试。

被验证的那一截接线，此前在全仓缺失：`build_command()` 只被 print/dump，
没有任何代码把它的输出交给 `subprocess.run`。副机 L41 把这一点验到了行号级别。
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.autoresearch import evaluation_runner as er  # noqa: E402

PY = sys.executable
RUN_EVAL = ROOT / "tools" / "run_eval.py"
PROFILE_DIR = ROOT / "profiles" / "agent-harness"


def build_profile(
    dest: pathlib.Path,
    *,
    command: list[str] | None = None,
    configured: bool = True,
    limits: dict | None = None,
) -> pathlib.Path:
    """在 dest 造一份最小 profile；把 RULES.md 等既有材料复制过去以通过校验。"""
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("RULES.md", "README.md", "EXTRACTION.md"):
        src = PROFILE_DIR / name
        if src.is_file():
            (dest / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    for sub in ("schemas", "examples", "checks"):
        src = PROFILE_DIR / sub
        if src.is_dir():
            for item in src.rglob("*"):
                if item.is_file():
                    target = dest / sub / item.relative_to(src)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(item.read_bytes())

    default_cmd = [
        PY, "-X", "utf8", "-B", str(RUN_EVAL),
        "--tasks", "{tasks}", "--out", "{output}",
        "--per-task-command", "--allow-failures",
    ]
    lines = [
        'id = "agent-harness"',
        "version = 1",
        "",
        "[evaluation]",
        f"configured = {'true' if configured else 'false'}",
        'result_file = "task_results.jsonl"',
        'trace_dir = "traces"',
        "result_schema_version = 1",
        "command = [" + ", ".join(json.dumps(x) for x in (command if command is not None else default_cmd)) + "]",
        "",
        "[limits]",
    ]
    for key, value in (limits if limits is not None else {
        "per_task_minutes": 10, "max_tool_calls": 20,
        "max_turns": 30, "command_timeout_seconds": 60,
    }).items():
        lines.append(f"{key} = {json.dumps(value)}")
    lines += ["", "[instructions]", 'required = ["RULES.md"]', ""]
    path = dest / "profile.toml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_tasks(path: pathlib.Path, rows) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


class TestRefusesWhenNotConfigured(unittest.TestCase):
    def test_unconfigured_profile_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            profile = build_profile(tmp / "p", configured=False)
            tasks = write_tasks(tmp / "t.jsonl", [{"task_id": "x-1", "command": [PY, "-c", "pass"]}])
            with self.assertRaises(er.EvaluationRunError) as ctx:
                er.run_evaluation(profile, tasks=str(tasks), output=str(tmp / "o"))
            self.assertIn("配置不合规", str(ctx.exception))

    def test_empty_command_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            profile = build_profile(tmp / "p", command=[])
            tasks = write_tasks(tmp / "t.jsonl", [{"task_id": "x-1", "command": [PY, "-c", "pass"]}])
            with self.assertRaises(er.EvaluationRunError):
                er.run_evaluation(profile, tasks=str(tasks), output=str(tmp / "o"))

    def test_missing_limits_are_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            profile = build_profile(tmp / "p", limits={"per_task_minutes": 10})
            tasks = write_tasks(tmp / "t.jsonl", [{"task_id": "x-1", "command": [PY, "-c", "pass"]}])
            with self.assertRaises(er.EvaluationRunError) as ctx:
                er.run_evaluation(profile, tasks=str(tasks), output=str(tmp / "o"))
            self.assertIn("limits", str(ctx.exception))


class TestExecutesAndReadsBack(unittest.TestCase):
    def test_successful_run_returns_results(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            profile = build_profile(tmp / "p")
            tasks = write_tasks(tmp / "t.jsonl", [
                {"task_id": "y-1", "command": [PY, "-c", "pass"]},
                {"task_id": "y-2", "command": [PY, "-c", "import sys;sys.exit(1)"]},
            ])
            report = er.run_evaluation(profile, tasks=str(tasks), output=str(tmp / "o"), timeout=120)
            self.assertTrue(report["executed"])
            self.assertEqual(report["exit"], 0)
            self.assertIsNone(report["results_read_error"])
            self.assertIsNotNone(report["results"])
            self.assertEqual(len(report["results"]["tasks"]), 2)
            statuses = {t2["task_id"]: t2["status"] for t2 in report["results"]["tasks"]}
            self.assertEqual(statuses, {"y-1": "passed", "y-2": "failed"})
            # 通过率口径：passed/(passed+failed)
            self.assertEqual(report["results"]["summary"]["pass_rate"], 0.5)

    def test_missing_results_file_is_reported_not_faked(self):
        """执行器不写结果文件时，必须如实报 results_read_error，不能编造读数。"""
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            # 一个带占位符、但什么都不写的执行器；占位符齐全才不会在 can_run 阶段被拦
            script = (
                "import sys;"
                "print('假装跑完了 tasks=%s out=%s' % (sys.argv[1], sys.argv[2]));"
                "sys.exit(0)"
            )
            profile = build_profile(tmp / "p", command=[PY, "-c", script, "{tasks}", "{output}"])
            tasks = write_tasks(tmp / "t.jsonl", [{"task_id": "z-1", "command": [PY, "-c", "pass"]}])
            report = er.run_evaluation(profile, tasks=str(tasks), output=str(tmp / "o"), timeout=60)
            self.assertTrue(report["executed"])
            self.assertEqual(report["exit"], 0)
            self.assertIsNone(report["results"], "没有结果文件就不该有读数")
            self.assertIsNotNone(report["results_read_error"])
            self.assertIn("找不到结果文件", report["results_read_error"])


class TestSilentDropDetection(unittest.TestCase):
    """L54 CONFIRMED-SILENT-DROP：执行器少写/多写题时，不许判 ok=true。

    背景（副机 L54 原文）：题单 2 行、桩只回 1 行，run_meta 自洽地谎称 tasks_total=1，
    而 research.py eval 仍 exit=0 / ok=true / pass_rate=1.0 —— 被吞掉的那题看不出来。
    根因是 ok 只等于「执行器 exit==0」。
    """

    def _run_with_stub(self, tmp: pathlib.Path, task_ids, emitted_ids, *, sidecar=False):
        """用桩执行器只写回 emitted_ids，模拟静默丢题 / 多写题。

        `sidecar=True` 时同时写 `run_meta.json`（题单快照 + schema 版本），
        也就是**真实 `tools/run_eval.py` 的产出形状**。不加这个开关就测不到
        「read_results 先置 raw、runner 再覆盖成 null」那条叠加路径。
        """
        stub = tmp / "stub.py"
        sidecar_src = (
            "if " + repr(bool(sidecar)) + ":\n"
            "    (out / 'run_meta.json').write_text(json.dumps({'tasklist_task_ids': tasklist_ids,"
            " 'result_schema_version': 1}, ensure_ascii=False), encoding='utf-8')\n"
        )
        stub.write_text(
            "import sys, json, pathlib\n"
            "tasks = pathlib.Path(sys.argv[sys.argv.index('--tasks')+1])\n"
            "out = pathlib.Path(sys.argv[sys.argv.index('--out')+1])\n"
            "out.mkdir(parents=True, exist_ok=True)\n"
            "emit = " + repr(list(emitted_ids)) + "\n"
            "tasklist_ids = [json.loads(x)['task_id'] for x in\n"
            "                tasks.read_text(encoding='utf-8-sig').splitlines() if x.strip()]\n"
            "with (out / 'task_results.jsonl').open('w', encoding='utf-8', newline='\\n') as fh:\n"
            "    for tid in emit:\n"
            "        fh.write(json.dumps({'task_id': tid, 'status': 'passed'}, ensure_ascii=False) + '\\n')\n"
            + sidecar_src +
            "print('pretend done:', len(emit))\n",
            encoding="utf-8",
        )
        profile = build_profile(tmp / "p", command=[
            PY, "-X", "utf8", "-B", str(stub), "--tasks", "{tasks}", "--out", "{output}",
        ])
        tasks = write_tasks(tmp / "t.jsonl", [
            {"task_id": tid, "statement": "x"} for tid in task_ids
        ])
        return er.run_evaluation(profile, tasks=str(tasks), output=str(tmp / "o"), timeout=60)

    def test_missing_task_is_not_ok(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run_with_stub(tmp, ["a-1", "a-2"], ["a-1"])
            # 执行器自己 exit 0，但这不算 ok —— 少了一题
            self.assertEqual(report["exit"], 0)
            self.assertFalse(report["ok"], "少写一题必须判 ok=false")
            self.assertEqual(report["alignment"]["expected_count"], 2)
            self.assertEqual(report["alignment"]["actual_count"], 1)
            self.assertEqual(report["alignment"]["missing_task_ids"], ["a-2"])
            self.assertIn("缺 1 题", report["alignment_error"])
            self.assertIn("a-2", report["alignment_error"])

    def test_extra_task_is_not_ok(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run_with_stub(tmp, ["b-1"], ["b-1", "b-ghost"])
            self.assertFalse(report["ok"], "多写一题也必须判 ok=false")
            self.assertEqual(report["alignment"]["unexpected_task_ids"], ["b-ghost"])
            self.assertIn("多 1 题", report["alignment_error"])

    def test_aligned_run_is_ok(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run_with_stub(tmp, ["c-1", "c-2"], ["c-1", "c-2"])
            self.assertTrue(report["ok"], "题目对齐且 exit 0 才判 ok=true")
            self.assertIsNone(report["alignment_error"])
            self.assertEqual(report["alignment"]["missing_task_ids"], [])
            self.assertEqual(report["alignment"]["unexpected_task_ids"], [])

    # ---- 第二层假绿（副机 L57）：ok=false 之后 summary.pass_rate 仍是 1.0 ----
    #
    # 只把顶层 ok 改成 false 是不够的：任何拿 report["results"]["summary"] 当读数的
    # 下游（图表、对比、CLI 打印）照样看到 100%。对齐结论必须同时注入 summary。

    def test_misaligned_run_nulls_summary_pass_rate(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run_with_stub(tmp, ["d-1", "d-2"], ["d-1"])
            self.assertFalse(report["ok"])
            summary = report["results"]["summary"]
            self.assertIsNone(summary["pass_rate"], "覆盖不全时 summary.pass_rate 必须不可用")
            self.assertEqual(summary["pass_rate_raw"], 1.0, "原值要留档")
            self.assertFalse(summary["aligned"])
            self.assertEqual(summary["alignment"]["missing_task_ids"], ["d-2"])
            self.assertIn("未对齐", summary["pass_rate_basis"])

    def test_aligned_run_keeps_summary_pass_rate(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run_with_stub(tmp, ["g-1", "g-2"], ["g-1", "g-2"])
            summary = report["results"]["summary"]
            self.assertEqual(summary["pass_rate"], 1.0)
            self.assertEqual(summary["pass_rate_raw"], 1.0)
            self.assertTrue(summary["aligned"])
            self.assertNotIn("未对齐", summary["pass_rate_basis"])

    def test_sidecar_present_keeps_original_pass_rate_in_raw(self):
        """有 sidecar 时 `pass_rate_raw` 必须留住**原值**（副机 L61 第 2 步实测）。

        `read_results()` 因为 sidecar 里的题单快照对不上，会先把 `pass_rate` 置 None、
        把原值 1.0 存进 `pass_rate_raw`。runner 若再执行
        `summary["pass_rate_raw"] = summary.get("pass_rate")`，拿到的是 None，
        把一手记录覆盖掉 —— raw 也变成 null，谁都看不出原来是 1.0。

        这条只有在「真实执行器形状（带 sidecar）+ 丢题」时才成立：
        不带 sidecar 的桩反而一切正常，所以早先的用例全都漏了它。
        """
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run_with_stub(tmp, ["s-1", "s-2", "s-3"], ["s-1", "s-2"],
                                         sidecar=True)
            summary = report["results"]["summary"]
            self.assertFalse(report["ok"])
            self.assertEqual(report["verdict"], "misaligned")
            self.assertIsNone(summary["pass_rate"])
            self.assertEqual(summary["pass_rate_raw"], 1.0,
                             "raw 必须是原值 1.0，不能被 None 覆盖")
            self.assertFalse(summary["aligned"])


class TestVerdict(unittest.TestCase):
    """`ok` 是 bool，装不下「为什么不是」。verdict 负责把成因说清。

    尤其要区分这两种 `ok=false`：
      - 覆盖不全（misaligned）—— 读数不可用，属故障
      - 有题没通过（not_all_passed）—— 读数是好的，属结果
    混成一句会让自动循环把「这批题考砸了」当成「评测跑挂了」反复重试。
    """

    def _stub(self, tmp: pathlib.Path, body: str) -> pathlib.Path:
        stub = tmp / "stub.py"
        stub.write_text(body, encoding="utf-8")
        return stub

    def _run(self, tmp: pathlib.Path, stub: pathlib.Path, task_ids, timeout=60, extra=()):
        profile = build_profile(tmp / "p", command=[
            PY, "-X", "utf8", "-B", str(stub), "--tasks", "{tasks}", "--out", "{output}", *extra,
        ])
        tasks = write_tasks(tmp / "t.jsonl", [{"task_id": t, "statement": "x"} for t in task_ids])
        return er.run_evaluation(profile, tasks=str(tasks), output=str(tmp / "o"), timeout=timeout)

    def test_all_passed(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            stub = self._stub(tmp,
                "import sys, json, pathlib\n"
                "tasks = pathlib.Path(sys.argv[sys.argv.index('--tasks')+1])\n"
                "out = pathlib.Path(sys.argv[sys.argv.index('--out')+1])\n"
                "out.mkdir(parents=True, exist_ok=True)\n"
                "rows = [json.loads(x) for x in tasks.read_text(encoding='utf-8-sig').splitlines() if x.strip()]\n"
                "with (out / 'task_results.jsonl').open('w', encoding='utf-8', newline='\\n') as fh:\n"
                "    for r in rows:\n"
                "        fh.write(json.dumps({'task_id': r['task_id'], 'status': 'passed'}) + '\\n')\n")
            report = self._run(tmp, stub, ["v-1", "v-2"])
            self.assertEqual(report["verdict"], "all_passed")
            self.assertTrue(report["ok"])

    def test_not_all_passed_is_a_reading_not_a_failure(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            stub = self._stub(tmp,
                "import sys, json, pathlib\n"
                "tasks = pathlib.Path(sys.argv[sys.argv.index('--tasks')+1])\n"
                "out = pathlib.Path(sys.argv[sys.argv.index('--out')+1])\n"
                "out.mkdir(parents=True, exist_ok=True)\n"
                "rows = [json.loads(x) for x in tasks.read_text(encoding='utf-8-sig').splitlines() if x.strip()]\n"
                "with (out / 'task_results.jsonl').open('w', encoding='utf-8', newline='\\n') as fh:\n"
                "    for i, r in enumerate(rows):\n"
                "        st = 'passed' if i == 0 else 'failed'\n"
                "        fh.write(json.dumps({'task_id': r['task_id'], 'status': st}) + '\\n')\n"
                "sys.exit(1)\n")
            report = self._run(tmp, stub, ["v-1", "v-2"])
            self.assertFalse(report["ok"])
            self.assertEqual(report["verdict"], "not_all_passed")
            self.assertIsNone(report["alignment_error"], "这不是覆盖问题，不该报 alignment_error")
            self.assertEqual(report["results"]["summary"]["pass_rate"], 0.5)

    def test_misaligned(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            stub = self._stub(tmp,
                "import sys, json, pathlib\n"
                "tasks = pathlib.Path(sys.argv[sys.argv.index('--tasks')+1])\n"
                "out = pathlib.Path(sys.argv[sys.argv.index('--out')+1])\n"
                "out.mkdir(parents=True, exist_ok=True)\n"
                "rows = [json.loads(x) for x in tasks.read_text(encoding='utf-8-sig').splitlines() if x.strip()]\n"
                "with (out / 'task_results.jsonl').open('w', encoding='utf-8', newline='\\n') as fh:\n"
                "    fh.write(json.dumps({'task_id': rows[0]['task_id'], 'status': 'passed'}) + '\\n')\n")
            report = self._run(tmp, stub, ["v-1", "v-2"])
            self.assertEqual(report["verdict"], "misaligned")
            self.assertFalse(report["ok"])

    def test_no_results(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            stub = self._stub(tmp, "print('nothing written')\n")
            report = self._run(tmp, stub, ["v-1"])
            self.assertEqual(report["verdict"], "no_results")
            self.assertFalse(report["ok"])

    def test_timed_out(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            stub = self._stub(tmp, "import time\ntime.sleep(30)\n")
            report = self._run(tmp, stub, ["v-1"], timeout=2)
            self.assertEqual(report["verdict"], "timed_out")
            self.assertTrue(report["timed_out"])
            self.assertFalse(report["ok"])


class TestBudget(unittest.TestCase):
    """`[limits].total_budget_minutes` 必须真的生效。

    此前它只是 profile 里一个允许出现的键，**没有任何代码读它** —— 写进 TOML 等于
    没写。同一个毛病 `per_task_minutes` 也犯过（写在配置里、没人执行），
    这类「声明了但没人执行」的键最容易被当成已生效。
    """

    def _stub(self, tmp: pathlib.Path) -> pathlib.Path:
        tmp.mkdir(parents=True, exist_ok=True)
        stub = tmp / "stub.py"
        stub.write_text(
            "import sys, json, pathlib\n"
            "tasks = pathlib.Path(sys.argv[sys.argv.index('--tasks')+1])\n"
            "out = pathlib.Path(sys.argv[sys.argv.index('--out')+1])\n"
            "out.mkdir(parents=True, exist_ok=True)\n"
            "rows = [json.loads(x) for x in tasks.read_text(encoding='utf-8-sig').splitlines() if x.strip()]\n"
            "with (out / 'task_results.jsonl').open('w', encoding='utf-8', newline='\\n') as fh:\n"
            "    for r in rows:\n"
            "        fh.write(json.dumps({'task_id': r['task_id'], 'status': 'passed'}) + '\\n')\n",
            encoding="utf-8",
        )
        return stub

    def _profile(self, tmp: pathlib.Path, **overrides):
        limits = {
            "per_task_minutes": 10, "max_tool_calls": 20,
            "max_turns": 30, "command_timeout_seconds": 60,
        }
        limits.update(overrides)
        stub = self._stub(tmp)
        profile = build_profile(tmp / "p", command=[
            PY, "-X", "utf8", "-B", str(stub), "--tasks", "{tasks}", "--out", "{output}",
        ], limits=limits)
        tasks = write_tasks(tmp / "t.jsonl", [{"task_id": "bud-1", "statement": "x"}])
        return profile, tasks

    def test_declared_budget_sets_default_timeout(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            profile, tasks = self._profile(tmp, total_budget_minutes=1)
            report = er.run_evaluation(profile, tasks=str(tasks), output=str(tmp / "o"))
            self.assertEqual(report["timeout_seconds"], 60.0)
            self.assertEqual(report["budget"]["declared_total_minutes"], 1)
            self.assertFalse(report["budget"]["clamped"])

    def test_explicit_timeout_over_budget_is_clamped(self):
        """给的超时超过声明预算 ⇒ 夹到预算。预算是红线，不是建议。"""
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            profile, tasks = self._profile(tmp, total_budget_minutes=1)
            report = er.run_evaluation(profile, tasks=str(tasks),
                                       output=str(tmp / "o"), timeout=9999)
            self.assertEqual(report["timeout_seconds"], 60.0)
            self.assertTrue(report["budget"]["clamped"], "夹取必须留痕，不能悄悄少跑")
            self.assertIn("超过", report["budget"]["why"])

    def test_explicit_timeout_within_budget_is_kept(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            profile, tasks = self._profile(tmp, total_budget_minutes=10)
            report = er.run_evaluation(profile, tasks=str(tasks),
                                       output=str(tmp / "o"), timeout=30)
            self.assertEqual(report["timeout_seconds"], 30.0)
            self.assertFalse(report["budget"]["clamped"])

    def test_no_budget_no_timeout_uses_default(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            profile, tasks = self._profile(tmp)
            report = er.run_evaluation(profile, tasks=str(tasks), output=str(tmp / "o"))
            self.assertEqual(report["timeout_seconds"], er.DEFAULT_TIMEOUT_SECONDS)
            self.assertIsNone(report["budget"]["declared_total_minutes"])

    def test_bad_timeout_values_are_rejected_before_running(self):
        """nan / inf / 0 / 负数一律在启动进程之前拒绝。

        不守这道门的话，`inf` 会一路走进 subprocess，最后报
        `OverflowError: timestamp out of range` —— 与真实原因毫无关系的报错。
        """
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            profile, tasks = self._profile(tmp)
            for bad in (float("nan"), float("inf"), 0, -1, -1e-9, 1e999):
                with self.subTest(value=repr(bad)):
                    with self.assertRaises(er.EvaluationRunError):
                        er.run_evaluation(profile, tasks=str(tasks),
                                          output=str(tmp / "o"), timeout=bad)
            self.assertFalse((tmp / "o" / "task_results.jsonl").exists(), "拒绝时不该跑出结果")

    def test_bad_declared_budget_is_rejected(self):
        """0 与负数要拒。`inf` 不用测：TOML 里根本写不出 `inf`（tomllib 直接报非法值）。"""
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            for bad in (0, -5):
                with self.subTest(value=repr(bad)):
                    profile, tasks = self._profile(pathlib.Path(t) / f"c{abs(hash(bad))}",
                                                   total_budget_minutes=bad)
                    with self.assertRaises(er.EvaluationRunError):
                        er.run_evaluation(profile, tasks=str(tasks),
                                          output=str(tmp / "o2"), timeout=10)


class TestCliTimeoutBoundary(unittest.TestCase):
    """`research.py eval --timeout` 的边界：必须 exit 2 且不能吐 Traceback。

    副机 L41 在 `tools/run_eval.py` 的 `--timeout nan` 上踩过一次 Traceback；同一个
    毛病在 runner 的 CLI 上也存在过（`inf` 一路走到 subprocess 才炸成
    `OverflowError: timestamp out of range`）。这里守的是**用户看得见的那一面**。
    """

    def _run(self, *extra: str):
        import subprocess
        tmp = pathlib.Path(tempfile.mkdtemp())
        stub = tmp / "stub.py"
        stub.write_text("print('ran')\n", encoding="utf-8")
        profile = build_profile(tmp / "p", command=[
            PY, "-X", "utf8", "-B", str(stub), "--tasks", "{tasks}", "--out", "{output}",
        ])
        tasks = write_tasks(tmp / "t.jsonl", [{"task_id": "cli-1", "statement": "x"}])
        return subprocess.run(
            [PY, "-X", "utf8", "-B", str(ROOT / "research.py"), "eval",
             "--profile", str(profile), "--tasks", str(tasks),
             "--output", str(tmp / "o"), *extra],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120, stdin=subprocess.DEVNULL,
        )

    def test_invalid_timeouts_exit_2_without_traceback(self):
        for bad in ("inf", "nan", "0", "-5", "1e999"):
            with self.subTest(value=bad):
                proc = self._run("--timeout", bad)
                blob = proc.stdout + proc.stderr
                self.assertEqual(proc.returncode, 2, blob)
                self.assertNotIn("Traceback", blob, blob)
                self.assertNotIn("OverflowError", blob, blob)

    def test_omitting_timeout_is_allowed(self):
        """默认不传 `--timeout` 是合法用法（此时按 profile 的 total_budget_minutes）。"""
        proc = self._run()
        self.assertIn(proc.returncode, (0, 1), proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stdout + proc.stderr)


class TestPartialMeasurement(unittest.TestCase):
    """两个通过率口径必须并排出现 —— 否则 1.0 会被读成「全过」。

    守卫 2026-11-23：3 题里 1 题 `infra_error`（根本没跑），
    `summary.pass_rate` 按契约是 `passed/(passed+failed)` = **1.0**。
    契约没错，但它是用户唯一会直接读成「全过」的对外读数。所以再加一个
    `pass_rate_all = passed/n_tasks`，两个数并排摆着，差额自己看得见。
    """

    def _stub(self, tmp: pathlib.Path, body: str) -> pathlib.Path:
        tmp.mkdir(parents=True, exist_ok=True)
        stub = tmp / "stub.py"
        stub.write_text(
            "import sys, json, pathlib\n"
            "tasks = pathlib.Path(sys.argv[sys.argv.index('--tasks')+1])\n"
            "out = pathlib.Path(sys.argv[sys.argv.index('--out')+1])\n"
            "out.mkdir(parents=True, exist_ok=True)\n"
            "rows = [json.loads(x) for x in tasks.read_text(encoding='utf-8-sig').splitlines() if x.strip()]\n"
            + body,
            encoding="utf-8",
        )
        return stub

    def _run(self, tmp: pathlib.Path, body: str, task_ids=("d-1", "d-2")):
        stub = self._stub(tmp, body)
        profile = build_profile(tmp / "p", command=[
            PY, "-X", "utf8", "-B", str(stub), "--tasks", "{tasks}", "--out", "{output}",
        ])
        tasks = write_tasks(tmp / "t.jsonl", [{"task_id": t, "statement": "x"} for t in task_ids])
        return er.run_evaluation(profile, tasks=str(tasks), output=str(tmp / "o"), timeout=60)

    def test_partial_measurement_reports_both_rates(self):
        """部分没测成时必须同时给出两个口径 —— 否则 1.0 会被读成「全过」。

        守卫 2026-11-23：3 题里 1 题 `infra_error`（根本没跑），
        `summary.pass_rate` 按契约是 `passed/(passed+failed)` = **1.0**。
        契约没错，但它是用户唯一会直接读成「全过」的对外读数。所以再加一个
        `pass_rate_all = passed/n_tasks`，两个数并排摆着，差额自己看得见。
        """
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run(tmp,
                "with (out/'task_results.jsonl').open('w',encoding='utf-8',newline='\\n') as fh:\n"
                "    for i, r in enumerate(rows):\n"
                "        st = 'infra_error' if i == 1 else 'passed'\n"
                "        fh.write(json.dumps({'task_id': r['task_id'], 'status': st})+'\\n')\n")
            rates = report["rates"]
            self.assertEqual(rates["pass_rate_scored"], 1.0, "测量口径：只算测出来的题")
            self.assertEqual(rates["pass_rate_all"], 0.5, "全题口径：没测成的算作没通过")
            self.assertIn("1 题没测成", rates["note"])
            self.assertIn("别只看大的那个", rates["note"])

    def test_clean_run_has_equal_rates_and_no_note(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run(tmp,
                "with (out/'task_results.jsonl').open('w',encoding='utf-8',newline='\\n') as fh:\n"
                "    for r in rows:\n"
                "        fh.write(json.dumps({'task_id': r['task_id'], 'status':'passed'})+'\\n')\n")
            rates = report["rates"]
            self.assertEqual(rates["pass_rate_scored"], 1.0)
            self.assertEqual(rates["pass_rate_all"], 1.0)
            self.assertIsNone(rates["note"])

    def test_nothing_scored_gives_no_rates(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run(tmp,
                "with (out/'task_results.jsonl').open('w',encoding='utf-8',newline='\\n') as fh:\n"
                "    for r in rows:\n"
                "        fh.write(json.dumps({'task_id': r['task_id'], 'status':'infra_error'})+'\\n')\n")
            self.assertEqual(report["verdict"], "nothing_scored")
            self.assertIsNone(report["rates"]["pass_rate_scored"])
            self.assertIsNone(report["rates"]["pass_rate_all"])
            self.assertIn("没测成", report["rates"]["note"])


class TestVerdictFromData(unittest.TestCase):
    """`verdict` 必须由**读数**推出来，不能由进程退出码推。

    `tools/gate_probe.py` 的对抗矩阵里抓到的：3 题全 `failed`、执行器仍 `exit 0`，
    verdict 报成 `all_passed`，而 `pass_rate` 明明是 0.0。
    名字里带 passed，却是靠退出码推的 —— 判据看进程脸色，不看数据。
    反向的错也有：全部通过但执行器按自己的约定 `exit 1`，会被误报成 `launch_failed`。
    """

    def _stub(self, tmp: pathlib.Path, body: str) -> pathlib.Path:
        tmp.mkdir(parents=True, exist_ok=True)
        stub = tmp / "stub.py"
        stub.write_text(
            "import sys, json, pathlib\n"
            "tasks = pathlib.Path(sys.argv[sys.argv.index('--tasks')+1])\n"
            "out = pathlib.Path(sys.argv[sys.argv.index('--out')+1])\n"
            "out.mkdir(parents=True, exist_ok=True)\n"
            "rows = [json.loads(x) for x in tasks.read_text(encoding='utf-8-sig').splitlines() if x.strip()]\n"
            + body,
            encoding="utf-8",
        )
        return stub

    def _run(self, tmp: pathlib.Path, body: str, task_ids=("d-1", "d-2")):
        stub = self._stub(tmp, body)
        profile = build_profile(tmp / "p", command=[
            PY, "-X", "utf8", "-B", str(stub), "--tasks", "{tasks}", "--out", "{output}",
        ])
        tasks = write_tasks(tmp / "t.jsonl", [{"task_id": t, "statement": "x"} for t in task_ids])
        return er.run_evaluation(profile, tasks=str(tasks), output=str(tmp / "o"), timeout=60)

    def test_all_failed_but_exit_zero_is_not_all_passed(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run(tmp,
                "with (out/'task_results.jsonl').open('w',encoding='utf-8',newline='\\n') as fh:\n"
                "    for r in rows:\n"
                "        fh.write(json.dumps({'task_id': r['task_id'], 'status':'failed'})+'\\n')\n"
                "sys.exit(0)\n")
            self.assertEqual(report["exit"], 0)
            self.assertEqual(report["verdict"], "not_all_passed",
                             "0/2 通过不能叫 all_passed，哪怕进程 exit 0")
            self.assertEqual(report["results"]["summary"]["pass_rate"], 0.0)

    def test_all_passed_but_exit_nonzero_is_still_all_passed(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run(tmp,
                "with (out/'task_results.jsonl').open('w',encoding='utf-8',newline='\\n') as fh:\n"
                "    for r in rows:\n"
                "        fh.write(json.dumps({'task_id': r['task_id'], 'status':'passed'})+'\\n')\n"
                "sys.exit(1)\n")
            self.assertEqual(report["exit"], 1)
            self.assertEqual(report["verdict"], "all_passed",
                             "数据说全通过，退出码的约定不该改写结论")
            self.assertEqual(report["results"]["summary"]["pass_rate"], 1.0)

    def test_invalid_results_are_not_reported_as_missing(self):
        """结果文件写了但格式不合法 ⇒ `results_invalid`，不是 `no_results`。

        两种情况的下一步动作完全不同：没写去看输出路径/配置，写了读不了去看格式。
        混成一个词等于把诊断信息抹平。
        """
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run(tmp,
                "with (out/'task_results.jsonl').open('w',encoding='utf-8',newline='\\n') as fh:\n"
                "    for r in rows:\n"
                "        fh.write(json.dumps({'task_id': r['task_id'], 'status':'skipped'})+'\\n')\n")
            self.assertFalse(report["ok"])
            self.assertEqual(report["verdict"], "results_invalid")
            self.assertTrue((tmp / "o" / "task_results.jsonl").is_file(),
                            "文件确实存在，只是读不了")

    def test_missing_results_are_reported_as_no_results(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run(tmp, "pass\n")
            self.assertFalse(report["ok"])
            self.assertEqual(report["verdict"], "no_results")
            self.assertFalse((tmp / "o" / "task_results.jsonl").exists())

    def test_all_infra_error_is_nothing_scored(self):
        """一题都没进分母 ⇒ `nothing_scored`，不是 `not_all_passed`。

        守卫 2026-11-23 指出的合法情形：全 infra_error / 全 timed_out 时
        `n_scored=0`、`pass_rate=None`。若仍按「n_passed < n_tasks ⇒ not_all_passed」判，
        等于把「没测成」记成「这次考了 0 分」——一次有效读数。
        数字是 None，但 verdict 与 exit 都在说「这是一次正常的、不通过的评测」。
        """
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run(tmp,
                "with (out/'task_results.jsonl').open('w',encoding='utf-8',newline='\\n') as fh:\n"
                "    for r in rows:\n"
                "        fh.write(json.dumps({'task_id': r['task_id'], 'status':'infra_error',"
                " 'reason':'依赖没装'})+'\\n')\n")
            summary = report["results"]["summary"]
            self.assertEqual(summary["n_scored"], 0)
            self.assertIsNone(summary["pass_rate"])
            self.assertEqual(report["verdict"], "nothing_scored")
            self.assertFalse(report["ok"], "没测成不能算 ok")
            self.assertIn("没测成", report["verdict_reason"])

    def test_partial_measurement_keeps_denominator(self):
        """部分没测成：分母只算测出来的题，但 verdict 要提示并非全部通过。

        2 通过 + 1 infra_error ⇒ `pass_rate=1.0`（分母=2）、`verdict=not_all_passed`。
        这不是假绿：`infra_error` 不进分母是**写在契约里**的口径，
        而 `verdict` 与 `n_unscored` 会同时告诉你「有一题压根没跑」。
        """
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            report = self._run(tmp,
                "with (out/'task_results.jsonl').open('w',encoding='utf-8',newline='\\n') as fh:\n"
                "    for i, r in enumerate(rows):\n"
                "        st = 'infra_error' if i == 1 else 'passed'\n"
                "        fh.write(json.dumps({'task_id': r['task_id'], 'status': st})+'\\n')\n")
            summary = report["results"]["summary"]
            self.assertEqual(summary["pass_rate"], 1.0)
            self.assertEqual(summary["n_scored"], 1)
            self.assertEqual(summary["n_unscored"], 1)
            self.assertEqual(report["verdict"], "not_all_passed")
            self.assertIn("1/2", report["verdict_reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
