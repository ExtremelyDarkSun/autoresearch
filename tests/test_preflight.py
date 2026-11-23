# GENERATED-BY: agent implementation, 2026-11-23 +08:00
# LAST-EDIT: 2026-11-23 +08:00
# PURPOSE: 人件就绪门的测试。三态（✓/✗/?）与四道口子的正控反控都在这里钉住。
# ORIGIN: none（新文件）
# CUMULATIVE-DELTA: 二版。副机 L69 指出三处假绿（改名即绕过、允许提交=是 也过、名字像桩就误伤），
#                   以及两实现标签词表冲突（预算上限 vs 花费上限）。本版逐条对应到测试。
"""人件就绪门的测试。

要点：**门不能只会说「缺」**。所以每一条既有正控（材料齐 ⇒ ✓）也有反控（材料不齐 ⇒ ✗），
另有一类是 `?`（配置完整但没证明是真的）—— 三态各自都要被测到。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "preflight.py"
PY = sys.executable
PROFILE_DIR = ROOT / "profiles" / "agent-harness"
RUN_EVAL = ROOT / "tools" / "run_eval.py"


def run(*args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [PY, "-X", "utf8", "-B", str(TOOL), *args],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
        stdin=subprocess.DEVNULL,
    )
    return proc.returncode, proc.stdout + proc.stderr


def make_provenance(tmp: pathlib.Path, sha: str | None = None) -> pathlib.Path:
    """给执行器登记一份 provenance；不传 sha 就用 run_eval.py 的真实哈希。"""
    real = sha or hashlib.sha256(RUN_EVAL.read_bytes()).hexdigest()
    path = tmp / "provenance.json"
    path.write_text(json.dumps({
        "argv_template": ["python", "tools/run_eval.py", "--tasks", "{tasks}", "--out", "{output}"],
        "entry": {"kind": "script", "path": "tools/run_eval.py", "sha256": real},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def make_fixtures(tmp: pathlib.Path, *, provenance: bool = True) -> dict:
    """造四样**假**夹具。全部带 TESTONLY，绝不能当真人件用。"""
    tasks = tmp / "tasks.jsonl"
    tasks.write_text(
        json.dumps({"task_id": "pf-1", "command": [PY, "-c", "pass"]}, ensure_ascii=False) + "\n"
        + json.dumps({"task_id": "pf-2", "command": [PY, "-c", "pass"]}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    profile = tmp / "profile"
    gen = subprocess.run(
        [PY, "-X", "utf8", "-B", str(ROOT / "tools" / "make_profile.py"), "--out", str(profile)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    if gen.returncode != 0:
        raise RuntimeError("make_profile 失败：" + gen.stdout + gen.stderr)

    data = tmp / "data"
    data.mkdir()
    (data / "TESTONLY-sample.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    kaggle = tmp / "kaggle.json"
    kaggle.write_text('{"username":"TESTONLY","key":"TESTONLY"}', encoding="utf-8")

    ssh = tmp / "ssh"
    ssh.mkdir()
    (ssh / "id_testonly").write_text("TESTONLY PRIVATE KEY\n", encoding="utf-8")
    (ssh / "id_testonly.pub").write_text("TESTONLY PUBLIC KEY\n", encoding="utf-8")

    autodl = tmp / "autodl-token.txt"
    autodl.write_text("TESTONLY-AUTODL-TOKEN\n", encoding="utf-8")

    auth = tmp / "AUTHORIZATION.md"
    auth.write_text(
        "# TESTONLY 授权（假夹具）\n"
        "预算上限：0 元\n授权人：TESTONLY\n有效期：TESTONLY\n允许提交：否\n",
        encoding="utf-8",
    )

    return {
        "tasklist": tasks, "profile": profile, "data": data,
        "kaggle": kaggle, "ssh": ssh, "autodl": autodl, "auth": auth,
        "provenance": make_provenance(tmp) if provenance else None,
    }


def args_for(f: dict, drop: str | None = None, **override) -> list[str]:
    merged = {**f, **override}
    mapping = {
        "tasklist": "--tasklist", "profile": "--profile", "data": "--data-dir",
        "kaggle": "--kaggle", "ssh": "--ssh-dir", "autodl": "--autodl-file",
        "auth": "--authorization", "provenance": "--executor-provenance",
    }
    out: list[str] = []
    for key, flag in mapping.items():
        if key == drop or merged.get(key) is None:
            continue
        out += [flag, str(merged[key])]
    return out


class TestPreflight(unittest.TestCase):
    def test_nothing_given_is_zero_of_four(self):
        code, out = run()
        self.assertEqual(code, 2, out)
        self.assertIn("0/4 齐", out)
        for key in ("A 题单 ✗", "B 执行器 ✗", "C 数据与访问权 ✗", "D 预算授权 ✗"):
            with self.subTest(key=key):
                self.assertIn(key, out)

    def test_all_fixtures_pass(self):
        with tempfile.TemporaryDirectory() as t:
            f = make_fixtures(pathlib.Path(t))
            code, out = run(*args_for(f))
            self.assertEqual(code, 0, out)
            self.assertIn("4/4 齐", out)
            self.assertNotIn("✗", out)
            self.assertIn("2 条自带 command", out, out)

    def test_dropping_each_item_turns_it_red(self):
        with tempfile.TemporaryDirectory() as t:
            f = make_fixtures(pathlib.Path(t))
            expectations = {
                "tasklist": "A 题单 ✗",
                "profile": "B 执行器 ✗",
                "data": "C 数据与访问权 ✗",
                "auth": "D 预算授权 ✗",
            }
            for drop, marker in expectations.items():
                # 用生成器产出的 profile 造夹具成本高，这里只重跑断言，夹具复用
                with self.subTest(drop=drop):
                    code, out = run(*args_for(f, drop=drop))
                    self.assertEqual(code, 2, out)
                    self.assertIn(marker, out)
                    self.assertIn("3/4 齐", out)

    def test_example_tasklist_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            f = make_fixtures(pathlib.Path(t))
            code, out = run(*args_for(
                f, tasklist=PROFILE_DIR / "examples" / "tasklist.example.jsonl"))
            self.assertEqual(code, 2, out)
            self.assertIn("A 题单 ✗", out)
            self.assertIn("逐字节相同", out)

    def test_renamed_copy_of_example_is_still_rejected(self):
        """副机 L69 抓到的假绿：只看文件名，改个名就过了。现在按内容判。"""
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            f = make_fixtures(tmp)
            copied = tmp / "tasks-copied-example.jsonl"
            copied.write_bytes((PROFILE_DIR / "examples" / "tasklist.example.jsonl").read_bytes())
            code, out = run(*args_for(f, tasklist=copied))
            self.assertEqual(code, 2, out)
            self.assertIn("A 题单 ✗", out)
            self.assertIn("逐字节相同", out)

    def test_tasklist_without_any_command_is_unknown_not_ok(self):
        """题单合法但每题都不带 command：不是「题单不合格」，是「模式不匹配」，判 ?。"""
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            f = make_fixtures(tmp)
            bare = tmp / "bare.jsonl"
            bare.write_text('{"task_id":"pf-1","statement":"x"}\n', encoding="utf-8")
            code, out = run(*args_for(f, tasklist=bare))
            self.assertEqual(code, 2, out)
            self.assertIn("A 题单 ?", out)
            self.assertIn("未证实", out)

    def test_stub_looking_name_is_unknown_not_hard_fail(self):
        """名字像桩只降级为 ?，不判 ✗ —— `my_stubby_runner.py` 是误伤。"""
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            f = make_fixtures(tmp)
            stub_py = tmp / "stub_eval.py"
            stub_py.write_text("pass\n", encoding="utf-8")
            profile = tmp / "stub-profile"
            gen = subprocess.run(
                [PY, "-X", "utf8", "-B", str(ROOT / "tools" / "make_profile.py"),
                 "--out", str(profile), "--python", PY],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
            )
            self.assertEqual(gen.returncode, 0, gen.stdout + gen.stderr)
            toml_path = profile / "profile.toml"
            lines = []
            for line in toml_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("command = ["):
                    lines.append('command = [' + ", ".join(
                        json.dumps(x) for x in (PY, "-X", "utf8", "-B", str(stub_py),
                                                "--tasks", "{tasks}", "--out", "{output}")) + "]")
                else:
                    lines.append(line)
            toml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            code, out = run(*args_for(f, profile=profile))
            self.assertEqual(code, 2, out)
            self.assertIn("B 执行器 ?", out)
            self.assertIn("名字不是判据", out)

    def test_provenance_mismatch_is_hard_fail(self):
        """配置完整 + 登记哈希不符 ⇒ ✗。这是唯一能识破「真解释器跑假脚本」的判据。"""
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            f = make_fixtures(tmp)
            wrong = make_provenance(tmp, sha="0" * 64)
            code, out = run(*args_for(f, provenance=wrong))
            self.assertEqual(code, 2, out)
            self.assertIn("B 执行器 ✗", out)
            self.assertIn("provenance 校验失败", out)

    def test_authorization_synonym_spelling_is_accepted(self):
        """`花费上限` 与 `预算上限` 同义 —— 副机两套实现曾因此给出相反结论。"""
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            f = make_fixtures(tmp)
            alt = tmp / "auth-spend.md"
            alt.write_text(
                "花费上限：0 元\n授权人：TESTONLY\n有效期：TESTONLY\n允许提交：否\n",
                encoding="utf-8",
            )
            code, out = run(*args_for(f, auth=alt))
            self.assertEqual(code, 0, out)
            self.assertIn("D 预算授权 ✓", out)

    def test_authorization_missing_label_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            f = make_fixtures(tmp)
            bad = tmp / "bad-auth.md"
            bad.write_text("预算上限：0 元\n授权人：TESTONLY\n", encoding="utf-8")
            code, out = run(*args_for(f, auth=bad))
            self.assertEqual(code, 2, out)
            self.assertIn("D 预算授权 ✗", out)
            self.assertIn("有效期", out, "要点名缺哪个标签")

    def test_allow_submit_yes_is_vetoed(self):
        """`允许提交=是` 必须一票否决 —— 只看标签存在会放过它（副机 L69 实测）。"""
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            f = make_fixtures(tmp)
            yes = tmp / "auth-yes.md"
            yes.write_text(
                "预算上限：0 元\n授权人：TESTONLY\n有效期：TESTONLY\n允许提交：是\n",
                encoding="utf-8",
            )
            code, out = run(*args_for(f, auth=yes))
            self.assertEqual(code, 2, out)
            self.assertIn("D 预算授权 ✗", out)
            self.assertIn("不许提交 Kaggle", out)

    def test_credentials_content_is_never_printed(self):
        with tempfile.TemporaryDirectory() as t:
            f = make_fixtures(pathlib.Path(t))
            code, out = run(*args_for(f))
            self.assertEqual(code, 0, out)
            for secret in ("TESTONLY-AUTODL-TOKEN", "TESTONLY PRIVATE KEY", '"key":"TESTONLY"'):
                with self.subTest(secret=secret):
                    self.assertNotIn(secret, out, "凭据内容不该出现在输出里")


if __name__ == "__main__":
    unittest.main(verbosity=2)
