# GENERATED-BY: portable extraction / repository bootstrap (2026-11-09 +08:00)
# LAST-EDIT: portable extraction / repository bootstrap (2026-11-09 +08:00)
# PURPOSE: agent-harness profile 与 agent_harness 适配器的单元测试（全部合成数据 / 临时目录，只用标准库 unittest）
# ORIGIN: none（新文件）
# CUMULATIVE-DELTA: 首版
"""agent-harness profile 的测试。

三条约束：

1. **只用标准库**（unittest / json / tempfile / pathlib / subprocess）。
2. **全部合成数据**：题号是 `demo-00x`，路径是 `/opt/agent/workspace/...`，
   不在测试里引用任何真实题库、gold、模型或历史读数。
3. **现场只读**：唯一读到的既有文件是本仓库交付的
   `profiles/agent-harness/profile.toml` 与 `examples/`；写入一律在 `tempfile` 里。

跑法：

    python -m unittest discover -s tests -v
    python tests/test_agent_profile.py
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.autoresearch import profiles as profiles_mod  # noqa: E402
from kernel.autoresearch.adapters import agent_harness as ah  # noqa: E402

PROFILE_DIR = ROOT / "profiles" / "agent-harness"
CLI = PROFILE_DIR / "checks" / "cli.py"


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def base_profile(**overrides) -> dict:
    """一份最小的合法 profile（合成值，不带任何真实比赛数据）。"""
    profile = {
        "id": "agent-harness",
        "version": 1,
        "evaluation": {
            "command": [],
            "configured": False,
            "result_file": "task_results.jsonl",
            "trace_dir": "traces",
            "result_schema_version": 1,
        },
        "instructions": {"required": ["RULES.md"]},
        "limits": {},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(profile.get(key), dict):
            profile[key] = {**profile[key], **value}
        else:
            profile[key] = value
    return profile


def configured_profile(*, evaluation=None, **limit_overrides) -> dict:
    """一份「配好了、可以跑」的 profile（用于 build_command 的正面用例）。"""
    limits = {
        "per_task_minutes": 10,
        "max_tool_calls": 20,
        "max_turns": 30,
        "command_timeout_seconds": 60,
    }
    limits.update(limit_overrides)
    eval_section = {
        "command": ["<评测入口>", "--tasks", "{tasks}", "--out", "{output}"],
        "configured": True,
        "result_file": "task_results.jsonl",
        "trace_dir": "traces",
        "result_schema_version": 1,
    }
    if evaluation:
        eval_section.update(evaluation)
    return base_profile(evaluation=eval_section, limits=limits)


def write_lines(path: pathlib.Path, rows) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def row(task_id: str, status: str, **extra) -> dict:
    payload = {"task_id": task_id, "status": status}
    payload.update(extra)
    return payload


def make_trace(*steps) -> dict:
    return {"task_id": "demo-001", "steps": list(steps)}


def step(index: int, tool: str, arguments: dict, observation=None) -> dict:
    return {
        "step_id": index,
        "source": "agent",
        "tool_calls": [{"function_name": tool, "arguments": arguments, "tool_call_id": f"call-{index}"}],
        "observation": observation,
    }


def patch_text(path: str, lines) -> str:
    body = "".join(f"+{line}\n" for line in lines)
    return f"--- a/{path}\n+++ b/{path}\n@@ -1,1 +1,{len(lines) + 1} @@\n{body}"


# ---------------------------------------------------------------------------
# load_profile / validate_profile
# ---------------------------------------------------------------------------


class TestLoadProfile(unittest.TestCase):
    def test_load_from_directory(self):
        profile = profiles_mod.load_profile(PROFILE_DIR)
        self.assertEqual(profile["id"], "agent-harness")
        self.assertEqual(profile["version"], 1)
        self.assertEqual(pathlib.Path(profile["_profile_dir"]), PROFILE_DIR)

    def test_load_from_file(self):
        profile = profiles_mod.load_profile(PROFILE_DIR / "profile.toml")
        self.assertEqual(profile["evaluation"]["result_file"], "task_results.jsonl")
        self.assertFalse(profile["evaluation"]["configured"])
        self.assertEqual(profile["evaluation"]["command"], [])

    def test_missing_profile_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(profiles_mod.ProfileError):
                profiles_mod.load_profile(pathlib.Path(tmp) / "nope.toml")

    def test_broken_toml_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = pathlib.Path(tmp) / "profile.toml"
            bad.write_text('id = "agent-harness"\nversion = = 1\n', encoding="utf-8")
            with self.assertRaises(profiles_mod.ProfileError):
                profiles_mod.load_profile(bad)

    def test_shipped_profile_validates_clean(self):
        profile = profiles_mod.load_profile(PROFILE_DIR)
        self.assertEqual(profiles_mod.validate_profile(profile), [])
        self.assertEqual(profiles_mod.required_instructions(profile), ["RULES.md"])
        self.assertTrue((PROFILE_DIR / "RULES.md").is_file())

    def test_shipped_profile_cannot_run_by_default(self):
        profile = profiles_mod.load_profile(PROFILE_DIR)
        state = profiles_mod.evaluation_state(profile)
        self.assertFalse(state["can_run"])
        self.assertFalse(profile["evaluation"]["configured"])
        with self.assertRaises(profiles_mod.ProfileError):
            profiles_mod.build_command(profile, tasks="x.jsonl", output="out")


class TestValidateProfile(unittest.TestCase):
    def test_minimal_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            (base / "RULES.md").write_text("rules\n", encoding="utf-8")
            profile = base_profile()
            profile["_profile_dir"] = str(base)
            self.assertEqual(profiles_mod.validate_profile(profile), [])

    def test_wrong_id_and_version(self):
        errors = profiles_mod.validate_profile(base_profile(id="something-else"))
        self.assertTrue(any("id" in item for item in errors))

        errors = profiles_mod.validate_profile(base_profile(version=2))
        self.assertTrue(any("version" in item for item in errors))

        errors = profiles_mod.validate_profile(base_profile(version=True))
        self.assertTrue(any("version" in item for item in errors))

    def test_command_must_be_list_of_strings(self):
        profile = base_profile(evaluation={"command": "python -m pytest", "configured": False})
        errors = profiles_mod.validate_profile(profile)
        self.assertTrue(any("必须是字符串数组" in item for item in errors))

    def test_unknown_placeholder_rejected(self):
        profile = configured_profile(evaluation={"command": ["run", "--tasks", "{tasklist}", "{tasks}", "{output}"]})
        errors = profiles_mod.validate_profile(profile)
        self.assertTrue(any("{tasklist}" in item for item in errors))

    def test_missing_placeholders_rejected(self):
        profile = configured_profile(evaluation={"command": ["run", "--out", "{output}"]})
        errors = profiles_mod.validate_profile(profile)
        self.assertTrue(any("{tasks}" in item for item in errors))

        profile = configured_profile(evaluation={"command": ["run", "--tasks", "{tasks}"]})
        errors = profiles_mod.validate_profile(profile)
        self.assertTrue(any("{output}" in item for item in errors))

    def test_shell_operator_rejected(self):
        profile = configured_profile(
            evaluation={"command": ["run", "{tasks}", ";", "rm", "-rf", "{output}"]}
        )
        errors = profiles_mod.validate_profile(profile)
        self.assertTrue(any("shell 操作符" in item for item in errors))

    def test_configured_consistency(self):
        errors = profiles_mod.validate_profile(
            base_profile(evaluation={"command": [], "configured": True})
        )
        self.assertTrue(any("configured=true" in item for item in errors))

        errors = profiles_mod.validate_profile(
            base_profile(
                evaluation={
                    "command": ["run", "{tasks}", "{output}"],
                    "configured": False,
                }
            )
        )
        self.assertTrue(any("自相矛盾" in item for item in errors))

    def test_result_file_must_be_relative(self):
        for value in ("/abs/task_results.jsonl", "C:\\abs\\task_results.jsonl", "../escape.jsonl"):
            errors = profiles_mod.validate_profile(
                base_profile(evaluation={"result_file": value})
            )
            self.assertTrue(
                any("result_file" in item for item in errors),
                f"{value} 应被判错，实际 {errors}",
            )

    def test_limits_unknown_key_rejected(self):
        profile = base_profile(limits={"official_global_cap": 1})
        errors = profiles_mod.validate_profile(profile)
        self.assertTrue(any("不是本仓库认可的通用预算名" in item for item in errors))

    def test_limits_values_must_be_positive_int(self):
        for value in (True, "10", 0, -1, 1.5):
            errors = profiles_mod.validate_profile(base_profile(limits={"max_turns": value}))
            self.assertTrue(
                any("max_turns" in item for item in errors),
                f"{value!r} 应被判错，实际 {errors}",
            )

    def test_configured_requires_every_budget(self):
        profile = configured_profile()
        del profile["limits"]["max_tool_calls"]
        errors = profiles_mod.validate_profile(profile)
        self.assertTrue(any("缺少必须显式给出的预算" in item for item in errors))
        self.assertTrue(any("max_tool_calls" in item for item in errors))

    def test_instructions_required_checks(self):
        errors = profiles_mod.validate_profile(base_profile(instructions={"required": []}))
        self.assertTrue(any("不能为空" in item for item in errors))

        errors = profiles_mod.validate_profile(
            base_profile(instructions={"required": ["/abs/RULES.md"]})
        )
        self.assertTrue(any("相对路径" in item for item in errors))

        with tempfile.TemporaryDirectory() as tmp:
            profile = base_profile()
            profile["_profile_dir"] = tmp
            errors = profiles_mod.validate_profile(profile)
            self.assertTrue(any("不存在" in item for item in errors))

    def test_schema_paths_must_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = base_profile(schema={"tasklist": "schemas/missing.json"})
            profile["_profile_dir"] = tmp
            errors = profiles_mod.validate_profile(profile)
            self.assertTrue(any("不存在" in item for item in errors))


# ---------------------------------------------------------------------------
# build_command
# ---------------------------------------------------------------------------


class TestBuildCommand(unittest.TestCase):
    def test_argv_substitution(self):
        profile = configured_profile()
        argv = profiles_mod.build_command(
            profile, tasks="tasks/demo.jsonl", output="runs/run_1"
        )
        self.assertEqual(
            argv, ["<评测入口>", "--tasks", "tasks/demo.jsonl", "--out", "runs/run_1"]
        )

    def test_returns_list_and_never_joins(self):
        profile = configured_profile()
        argv = profiles_mod.build_command(profile, tasks="a b.jsonl", output="out dir")
        self.assertIsInstance(argv, list)
        self.assertIn("a b.jsonl", argv)   # 带空格仍是一个元素，不会被切碎
        self.assertIn("out dir", argv)

    def test_unconfigured_raises_with_reason(self):
        profile = base_profile()
        with self.assertRaises(profiles_mod.ProfileError) as ctx:
            profiles_mod.build_command(profile, tasks="t.jsonl", output="o")
        message = str(ctx.exception)
        self.assertIn("不能发起评测", message)
        self.assertIn("configured", message)

    def test_configured_without_limits_raises(self):
        profile = configured_profile()
        profile["limits"] = {}
        with self.assertRaises(profiles_mod.ProfileError) as ctx:
            profiles_mod.build_command(profile, tasks="t.jsonl", output="o")
        self.assertIn("per_task_minutes", str(ctx.exception))

    def test_empty_paths_raise(self):
        profile = configured_profile()
        for tasks, output in (("", "o"), ("t.jsonl", "  "), (None, "o")):
            with self.assertRaises(profiles_mod.ProfileError):
                profiles_mod.build_command(profile, tasks=tasks, output=output)

    def test_limit_for_has_no_hidden_default(self):
        self.assertIsNone(profiles_mod.limit_for(base_profile(), "max_turns"))
        self.assertEqual(profiles_mod.limit_for(configured_profile(), "max_turns"), 30)
        with self.assertRaises(profiles_mod.ProfileError):
            profiles_mod.limit_for(configured_profile(), "official_cap")


# ---------------------------------------------------------------------------
# read_tasklist
# ---------------------------------------------------------------------------


class TestReadTasklist(unittest.TestCase):
    def test_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lines(
                pathlib.Path(tmp) / "tasks.jsonl",
                [{"task_id": "demo-001"}, {"task_id": "demo-002", "repo": "example/demo-repo"}],
            )
            result = ah.read_tasklist(path)
            self.assertEqual(result["summary"]["n_tasks"], 2)
            self.assertEqual(result["summary"]["task_ids"], ["demo-001", "demo-002"])
            self.assertEqual(result["summary"]["errors"], [])

    def test_duplicate_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lines(
                pathlib.Path(tmp) / "tasks.jsonl",
                [{"task_id": "demo-001"}, {"task_id": "demo-001"}],
            )
            with self.assertRaises(ah.ResultsError) as ctx:
                ah.read_tasklist(path)
            self.assertIn("重复", str(ctx.exception))

    def test_empty_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "tasks.jsonl"
            path.write_text("\n\n", encoding="utf-8")
            with self.assertRaises(ah.ResultsError) as ctx:
                ah.read_tasklist(path)
            self.assertIn("空", str(ctx.exception))

    def test_lenient_mode_collects_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lines(
                pathlib.Path(tmp) / "tasks.jsonl",
                [{"task_id": "demo-001"}, {"task_id": "demo-001"}, {"no_id": 1}],
            )
            result = ah.read_tasklist(path, strict=False)
            self.assertEqual(result["summary"]["n_tasks"], 1)
            self.assertEqual(result["summary"]["duplicates"], ["demo-001"])
            self.assertEqual(len(result["summary"]["errors"]), 2)


# ---------------------------------------------------------------------------
# read_results
# ---------------------------------------------------------------------------


class TestReadResults(unittest.TestCase):
    def test_counts_and_pass_rate_exclude_unscored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lines(
                pathlib.Path(tmp) / "task_results.jsonl",
                [
                    row("demo-001", "passed", duration_seconds=12.0),
                    row("demo-002", "failed", reason="测试仍失败"),
                    row("demo-003", "infra_error", reason="依赖装不上"),
                    row("demo-004", "timed_out"),
                ],
            )
            result = ah.read_results(path)
            summary = result["summary"]
            self.assertEqual(summary["n_tasks"], 4)
            self.assertEqual(summary["n_passed"], 1)
            self.assertEqual(summary["n_failed"], 1)
            self.assertEqual(summary["n_infra_error"], 1)
            self.assertEqual(summary["n_timed_out"], 1)
            self.assertEqual(summary["n_scored"], 2)
            self.assertEqual(summary["n_unscored"], 2)
            self.assertEqual(summary["pass_rate"], 0.5)  # 1 / (1+1)，不是 1/4
            by_id = {task["task_id"]: task for task in result["tasks"]}
            self.assertTrue(by_id["demo-001"]["passed"])
            self.assertFalse(by_id["demo-003"]["passed"])
            self.assertFalse(by_id["demo-004"]["passed"])
            self.assertFalse(by_id["demo-003"]["scored"])
            self.assertEqual(by_id["demo-001"]["duration_seconds"], 12.0)

    def test_all_unscored_gives_none_rate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lines(
                pathlib.Path(tmp) / "task_results.jsonl",
                [row("demo-001", "infra_error"), row("demo-002", "timed_out")],
            )
            summary = ah.read_results(path)["summary"]
            self.assertIsNone(summary["pass_rate"])

    def test_duplicate_task_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lines(
                pathlib.Path(tmp) / "task_results.jsonl",
                [row("demo-001", "passed"), row("demo-001", "failed")],
            )
            with self.assertRaises(ah.ResultsError) as ctx:
                ah.read_results(path)
            self.assertIn("重复", str(ctx.exception))

    def test_invalid_status_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lines(
                pathlib.Path(tmp) / "task_results.jsonl", [row("demo-001", "skipped")]
            )
            with self.assertRaises(ah.ResultsError) as ctx:
                ah.read_results(path)
            self.assertIn("status", str(ctx.exception))

    def test_missing_task_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lines(
                pathlib.Path(tmp) / "task_results.jsonl", [{"status": "passed"}]
            )
            with self.assertRaises(ah.ResultsError) as ctx:
                ah.read_results(path)
            self.assertIn("task_id", str(ctx.exception))

    def test_bad_json_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "task_results.jsonl"
            path.write_text('{"task_id": "demo-001", "status": "passed"}\nnot json\n', encoding="utf-8")
            with self.assertRaises(ah.ResultsError):
                ah.read_results(path)

    def test_lenient_mode_skips_bad_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "task_results.jsonl"
            path.write_text(
                '{"task_id": "demo-001", "status": "passed"}\n'
                '{"task_id": "demo-002", "status": "unknown"}\n'
                '{"task_id": "demo-001", "status": "failed"}\n',
                encoding="utf-8",
            )
            result = ah.read_results(path, strict=False)
            self.assertEqual(result["summary"]["n_tasks"], 1)
            self.assertEqual(len(result["summary"]["errors"]), 2)

    def test_passed_with_reason_is_not_noisy_but_error_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            ok = write_lines(
                base / "ok.jsonl",
                [row("demo-001", "passed", reason="被验证的测试全部通过")],
            )
            self.assertEqual(ah.read_results(ok)["summary"]["notes"], [])

            odd = write_lines(
                base / "odd.jsonl",
                [row("demo-001", "passed", error="harness 内部异常：boom")],
            )
            notes = ah.read_results(odd)["summary"]["notes"]
            self.assertEqual(len(notes), 1)
            self.assertIn("demo-001", notes[0])

    def test_directory_resolution_uses_profile_result_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            write_lines(base / "task_results.jsonl", [row("demo-001", "passed")])
            profile = base_profile()
            summary = ah.read_results(base, profile)["summary"]
            self.assertEqual(summary["n_tasks"], 1)
            self.assertEqual(
                pathlib.Path(summary["source"]).name, "task_results.jsonl"
            )

    def test_measurement_from_profile_and_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            results = write_lines(base / "task_results.jsonl", [row("demo-001", "passed")])
            profile = configured_profile()
            measurement = ah.read_results(results, profile)["summary"]["measurement"]
            self.assertEqual(measurement["profile_id"], "agent-harness")
            self.assertEqual(measurement["limits"]["max_turns"], 30)

            (base / "run_meta.json").write_text(
                json.dumps({"limits": {"max_turns": 7}, "measurement": {"harness": "demo"}}),
                encoding="utf-8",
            )
            measurement = ah.read_results(results, profile)["summary"]["measurement"]
            self.assertEqual(measurement["limits"], {"max_turns": 7})
            self.assertEqual(measurement["harness"], "demo")
            self.assertEqual(measurement["_sidecar"], "run_meta.json")


# ---------------------------------------------------------------------------
# compare_results
# ---------------------------------------------------------------------------


class TestCompareResults(unittest.TestCase):
    def _pair(self, tmp, before_rows, after_rows):
        base = pathlib.Path(tmp)
        before = write_lines(base / "before.jsonl", before_rows)
        after = write_lines(base / "after.jsonl", after_rows)
        return before, after

    def test_flips_and_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            before, after = self._pair(
                tmp,
                [row("demo-001", "failed"), row("demo-002", "passed"), row("demo-003", "failed")],
                [row("demo-001", "passed"), row("demo-002", "failed"), row("demo-003", "failed")],
            )
            diff = ah.compare_results(before, after)
            self.assertTrue(diff["comparable"])
            self.assertEqual(diff["gained"], ["demo-001"])
            self.assertEqual(diff["lost"], ["demo-002"])
            self.assertEqual(diff["unchanged"]["failed"], ["demo-003"])
            self.assertEqual(diff["verdict"], "mixed")
            self.assertEqual(diff["pass_rate"]["delta"], 0.0)

    def test_flat(self):
        with tempfile.TemporaryDirectory() as tmp:
            before, after = self._pair(
                tmp, [row("demo-001", "passed")], [row("demo-001", "passed")]
            )
            diff = ah.compare_results(before, after)
            self.assertEqual(diff["verdict"], "flat")
            self.assertEqual(diff["gained"], [])
            self.assertEqual(diff["lost"], [])

    def test_different_task_sets_are_incomparable(self):
        with tempfile.TemporaryDirectory() as tmp:
            before, after = self._pair(
                tmp,
                [row("demo-001", "failed"), row("demo-002", "failed")],
                [row("demo-001", "passed")],
            )
            diff = ah.compare_results(before, after)
            self.assertFalse(diff["comparable"])
            self.assertEqual(diff["verdict"], "incomparable")
            self.assertEqual(diff["gained"], [])
            self.assertEqual(diff["task_set"]["only_after"], [])
            self.assertEqual(diff["task_set"]["only_before"], ["demo-002"])
            self.assertTrue(diff["reasons"])

    def test_different_measurement_is_incomparable(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            before = write_lines(base / "before" / "task_results.jsonl", [row("demo-001", "failed")])
            after = write_lines(base / "after" / "task_results.jsonl", [row("demo-001", "passed")])
            diff = ah.compare_results(
                ah.read_results(before, configured_profile(max_turns=30)),
                ah.read_results(after, configured_profile(max_turns=99)),
            )
            self.assertFalse(diff["comparable"])
            self.assertIn("测量配置不同", " ".join(diff["reasons"]))
            self.assertEqual(diff["gained"], [])

    def test_allow_different_measurement_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            before = write_lines(base / "b" / "task_results.jsonl", [row("demo-001", "failed")])
            after = write_lines(base / "a" / "task_results.jsonl", [row("demo-001", "passed")])
            diff = ah.compare_results(
                ah.read_results(before, configured_profile(max_turns=30)),
                ah.read_results(after, configured_profile(max_turns=99)),
                require_same_measurement=False,
            )
            self.assertTrue(diff["comparable"])
            self.assertEqual(diff["gained"], ["demo-001"])
            self.assertFalse(diff["measurement"]["same"])

    def test_unscored_is_uncertain_not_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            before, after = self._pair(
                tmp,
                [row("demo-001", "passed"), row("demo-002", "failed")],
                [row("demo-001", "timed_out"), row("demo-002", "passed")],
            )
            diff = ah.compare_results(before, after)
            self.assertEqual(diff["lost"], [])
            self.assertEqual(diff["gained"], ["demo-002"])
            self.assertEqual(len(diff["uncertain"]), 1)
            self.assertEqual(diff["uncertain"][0]["task_id"], "demo-001")
            self.assertTrue(any("不可判定" in note for note in diff["notes"]))

    def test_paired_pass_rate_excludes_one_sided_unscored(self):
        with tempfile.TemporaryDirectory() as tmp:
            before, after = self._pair(
                tmp,
                [row("demo-001", "passed"), row("demo-002", "failed"), row("demo-003", "infra_error")],
                [row("demo-001", "passed"), row("demo-002", "failed"), row("demo-003", "passed")],
            )
            diff = ah.compare_results(before, after)
            rate = diff["pass_rate"]
            # 各自分母：before 1/2、after 2/3（demo-003 只有 after 计分）
            self.assertEqual(rate["before"], 0.5)
            self.assertAlmostEqual(rate["after"], round(2 / 3, 6))
            # 配对分母：只数两侧都计分的 2 题 ⇒ 两边都是 1/2
            self.assertEqual(rate["paired"]["n"], 2)
            self.assertEqual(rate["paired"]["before"], 0.5)
            self.assertEqual(rate["paired"]["after"], 0.5)
            self.assertEqual(rate["paired"]["delta"], 0.0)
            # demo-003 是「不确定」，不算翻正
            self.assertEqual(diff["gained"], [])
            self.assertEqual([item["task_id"] for item in diff["uncertain"]], ["demo-003"])

    def test_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            before = base / "before.jsonl"
            after = base / "after.jsonl"
            before.write_text("", encoding="utf-8")
            after.write_text("", encoding="utf-8")
            diff = ah.compare_results(before, after)
            self.assertEqual(diff["verdict"], "no_data")
            self.assertTrue(diff["comparable"])


# ---------------------------------------------------------------------------
# 轨迹检查
# ---------------------------------------------------------------------------


class TestTraceChecks(unittest.TestCase):
    def _clean_trace(self):
        return make_trace(
            step(1, "read", {"path": "/opt/agent/workspace/example/demo-repo/demo/mathx.py"}),
            step(
                2,
                "shell",
                {"command": "cd /opt/agent/workspace/example/demo-repo && python -m pytest -q"},
            ),
        )

    def test_iter_actions_extracts_paths_from_arguments_and_commands(self):
        actions = ah.iter_actions(self._clean_trace())
        self.assertEqual(len(actions), 2)
        self.assertEqual(
            actions[0]["paths"], ["/opt/agent/workspace/example/demo-repo/demo/mathx.py"]
        )
        self.assertIn("/opt/agent/workspace/example/demo-repo", actions[1]["paths"])
        self.assertEqual(actions[1]["tool"], "shell")

    def test_iter_actions_accepts_name_alias_and_string_arguments(self):
        trace = {
            "steps": [
                {
                    "tool_calls": [
                        {"name": "read", "arguments": json.dumps({"file": "/opt/agent/workspace/a.py"})}
                    ]
                }
            ]
        }
        actions = ah.iter_actions(trace)
        self.assertEqual(actions[0]["tool"], "read")
        self.assertEqual(actions[0]["paths"], ["/opt/agent/workspace/a.py"])

    def test_workspace_clean(self):
        report = ah.check_workspace(
            self._clean_trace(), allowed_roots=["/opt/agent/workspace"]
        )
        self.assertTrue(report["checked"])
        self.assertEqual(report["verdict"], "clean")
        self.assertEqual(report["violations"], [])

    def test_workspace_out_of_root_hit(self):
        trace = make_trace(step(1, "read", {"path": "/wheels/demo-1.0.0/demo/mathx.py"}))
        report = ah.check_workspace(trace, allowed_roots=["/opt/agent/workspace"])
        self.assertEqual(report["verdict"], "hit")
        self.assertEqual(report["violations"][0]["kind"], "out_of_workspace")
        self.assertEqual(report["violations"][0]["path"], "/wheels/demo-1.0.0/demo/mathx.py")

    def test_workspace_forbidden_pattern_hit_even_inside_root(self):
        trace = make_trace(
            step(1, "read", {"path": "/opt/agent/workspace/.answers/demo-001.json"})
        )
        report = ah.check_workspace(
            trace,
            allowed_roots=["/opt/agent/workspace"],
            forbidden_path_patterns=["/opt/agent/workspace/.answers"],
        )
        self.assertEqual(report["verdict"], "hit")
        self.assertEqual(report["violations"][0]["kind"], "forbidden_path")

    def test_workspace_regex_pattern(self):
        trace = make_trace(step(1, "read", {"path": "/opt/agent/workspace/site-packages/x.py"}))
        report = ah.check_workspace(
            trace,
            allowed_roots=["/opt/agent/workspace"],
            forbidden_path_patterns=[r"re:site-packages"],
        )
        self.assertEqual(report["verdict"], "hit")

    def test_workspace_ignore_paths(self):
        trace = make_trace(step(1, "read", {"path": "/tmp/harness/scratch/x.py"}))
        report = ah.check_workspace(
            trace,
            allowed_roots=["/opt/agent/workspace"],
            ignore_paths=["/tmp/harness"],
        )
        self.assertEqual(report["verdict"], "clean")

    def test_workspace_relative_paths_not_judged(self):
        trace = make_trace(step(1, "read", {"path": "demo/mathx.py"}))
        report = ah.check_workspace(trace, allowed_roots=["/opt/agent/workspace"])
        self.assertEqual(report["verdict"], "clean")

    def test_workspace_not_configured_is_not_clean(self):
        report = ah.check_workspace(self._clean_trace())
        self.assertFalse(report["checked"])
        self.assertEqual(report["verdict"], "not_configured")
        self.assertIsNone(report["clean"])

    def test_leak_fragment_in_arguments(self):
        trace = make_trace(step(1, "read", {"path": "/opt/agent/workspace/demo/mathx.py"}))
        report = ah.check_leak(trace, sensitive_fragments=["/opt/agent/workspace/demo/mathx.py"])
        self.assertEqual(report["verdict"], "hit")
        self.assertEqual(report["violations"][0]["kind"], "sensitive_fragment")
        self.assertNotIn("/opt/agent/workspace/demo/mathx.py", report["violations"][0]["fragment"])

    def test_leak_observation_only_when_enabled(self):
        trace = make_trace(
            step(
                1,
                "read",
                {"path": "/opt/agent/workspace/demo/notes.md"},
                observation={"content": "参考补丁片段：DEMO-SECRET-42"},
            )
        )
        off = ah.check_leak(trace, sensitive_fragments=["DEMO-SECRET-42"])
        self.assertEqual(off["verdict"], "clean")

        on = ah.check_leak(
            trace, sensitive_fragments=["DEMO-SECRET-42"], include_observations=True
        )
        self.assertEqual(on["verdict"], "hit")
        self.assertEqual(on["violations"][0]["source"], "observation")

    def test_leak_not_configured(self):
        report = ah.check_leak(self._clean_trace())
        self.assertFalse(report["checked"])
        self.assertEqual(report["verdict"], "not_configured")

    def test_check_trace_merges_profile_config(self):
        profile = base_profile(
            workspace={"allowed_roots": ["/opt/agent/workspace"]},
            leak={"sensitive_fragments": ["DEMO-SECRET-42"], "include_observations": True},
            trace={"tool_names": ["read", "shell"]},
        )
        report = ah.check_trace(self._clean_trace(), profile=profile)
        self.assertTrue(report["checked"])
        self.assertEqual(report["verdict"], "clean")
        self.assertEqual(report["tools"], {"read": 1, "shell": 1})

        dirty = make_trace(
            step(1, "download", {"path": "/wheels/demo/mathx.py"}),
            step(
                2,
                "read",
                {"path": "/opt/agent/workspace/notes.md"},
                observation={"content": "DEMO-SECRET-42"},
            ),
        )
        report = ah.check_trace(dirty, profile=profile)
        kinds = {item["kind"] for item in report["violations"]}
        self.assertEqual(kinds, {"unknown_tool", "out_of_workspace", "sensitive_fragment"})
        self.assertEqual(report["verdict"], "hit")

    def test_check_trace_explicit_args_override_profile(self):
        profile = base_profile(workspace={"allowed_roots": ["/opt/agent/workspace"]})
        trace = make_trace(step(1, "read", {"path": "/opt/other/x.py"}))
        self.assertEqual(ah.check_trace(trace, profile=profile)["verdict"], "hit")
        report = ah.check_trace(trace, profile=profile, allowed_roots=["/opt"])
        self.assertEqual(report["verdict"], "clean")

    def test_check_trace_not_configured(self):
        report = ah.check_trace(self._clean_trace(), profile=base_profile())
        self.assertFalse(report["checked"])
        self.assertEqual(report["verdict"], "not_configured")


# ---------------------------------------------------------------------------
# 补丁相似度
# ---------------------------------------------------------------------------


class TestPatchLeak(unittest.TestCase):
    def test_identical_patches_confirmed(self):
        lines = [f"    value_{index} = {index}" for index in range(40)]
        report = ah.check_patch_leak(patch_text("demo/mathx.py", lines),
                                     patch_text("demo/mathx.py", lines))
        self.assertEqual(report["verdict"], "confirmed")
        self.assertEqual(report["intersection"], 40)

    def test_disjoint_patches_not_formed(self):
        agent = [f"    agent_{index} = {index}" for index in range(40)]
        reference = [f"    reference_{index} = {index}" for index in range(40)]
        report = ah.check_patch_leak(patch_text("demo/mathx.py", agent),
                                     patch_text("demo/mathx.py", reference))
        self.assertEqual(report["verdict"], "not_formed")
        self.assertEqual(report["intersection"], 0)

    def test_short_patches_not_judged(self):
        agent = ["    a = 1", "    b = 2"]
        reference = ["    a = 1", "    b = 2"]
        report = ah.check_patch_leak(patch_text("demo/mathx.py", agent),
                                     patch_text("demo/mathx.py", reference))
        self.assertEqual(report["verdict"], "not_formed")
        self.assertIn("行数低于下限", report["reasons"][0])

    def test_suspected_ratio(self):
        shared = [f"    shared_{index} = {index}" for index in range(32)]
        agent = shared + [f"    agent_extra_{index} = {index}" for index in range(8)]
        reference = shared + [f"    reference_extra_{index} = {index}" for index in range(8)]
        report = ah.check_patch_leak(patch_text("demo/mathx.py", agent),
                                     patch_text("demo/mathx.py", reference))
        self.assertEqual(report["verdict"], "suspected")
        self.assertEqual(report["agent_lines"], 40)
        self.assertGreaterEqual(report["ratio"], report["ratio_threshold"])


# ---------------------------------------------------------------------------
# CLI（子进程跑，验证退出码契约；不联网、不跑评测）
# ---------------------------------------------------------------------------


class TestCli(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-B", "-X", "utf8", str(CLI), *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )

    def test_validate_exit_0(self):
        proc = self._run("validate")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("通过", proc.stdout)

    def test_command_exit_2_when_unconfigured(self):
        proc = self._run("command", "--tasks", "t.jsonl", "--output", "o")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("没有配置评测命令", proc.stdout)

    def test_results_example_exit_0(self):
        proc = self._run("results", str(PROFILE_DIR / "examples" / "results.example.jsonl"))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("通过率", proc.stdout)

    def test_trace_example_needs_rules(self):
        example = str(PROFILE_DIR / "examples" / "trace.example.json")
        proc = self._run("trace", example)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("未配置", proc.stdout)

        proc = self._run("trace", example, "--allowed-root", "/opt/agent/workspace")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        proc = self._run("trace", example, "--allowed-root", "/opt/other")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)

    def test_tasklist_example_exit_0(self):
        proc = self._run("tasklist", str(PROFILE_DIR / "examples" / "tasklist.example.jsonl"))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("demo-001", proc.stdout)

    def test_compare_incomparable_exit_4(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            before = write_lines(base / "before.jsonl", [row("demo-001", "failed"),
                                                         row("demo-002", "passed")])
            after = write_lines(base / "after.jsonl", [row("demo-001", "passed")])
            proc = self._run("compare", str(before), str(after))
            self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
            self.assertIn("不可比", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
