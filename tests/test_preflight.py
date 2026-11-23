# GENERATED-BY: agent implementation, 2026-11-23 +08:00
# LAST-EDIT: 2026-11-23 +08:00
# PURPOSE: 人件就绪门（tools/preflight.py）的测试——四个关口各要能绿，也要能红。
# ORIGIN: none（新文件）
# CUMULATIVE-DELTA: 首版。正控（四样都备 → 4/4 exit 0）与逐项反控（拆一样 → 该项 ✗）。
"""人件就绪门的测试。

两个方向都要测：

- **正控**：四样都备齐（全部假夹具，带 TESTONLY）⇒ 4/4、exit 0
- **反控**：逐样拆掉 ⇒ 对应那一项 ✗、总数下降 —— 否则这扇门只会说「缺」，
  没人知道它到底会不会认「有」。
"""
from __future__ import annotations

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


def run(*args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [PY, "-X", "utf8", "-B", str(TOOL), *args],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
        stdin=subprocess.DEVNULL,
    )
    return proc.returncode, proc.stdout + proc.stderr


def make_fixtures(tmp: pathlib.Path) -> dict:
    """造四样**假**夹具。全部带 TESTONLY，绝不能当真人件用。"""
    tasks = tmp / "tasks.jsonl"
    tasks.write_text(
        json.dumps({"task_id": "pf-1", "command": [PY, "-c", "pass"]}, ensure_ascii=False) + "\n"
        + json.dumps({"task_id": "pf-2", "command": [PY, "-c", "pass"]}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # B：用生成器造一份真能跑的 profile（command 指向 tools/run_eval.py，无桩标记）
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
    }


def args_for(f: dict, drop: str | None = None) -> list[str]:
    mapping = {
        "tasklist": ("--tasklist", f["tasklist"]),
        "profile": ("--profile", f["profile"]),
        "data": ("--data-dir", f["data"]),
        "kaggle": ("--kaggle", f["kaggle"]),
        "ssh": ("--ssh-dir", f["ssh"]),
        "autodl": ("--autodl-file", f["autodl"]),
        "auth": ("--authorization", f["auth"]),
    }
    out: list[str] = []
    for key, (flag, value) in mapping.items():
        if key == drop:
            continue
        out += [flag, str(value)]
    return out


class TestPreflight(unittest.TestCase):
    def test_nothing_given_is_zero_of_four(self):
        code, out = run()
        self.assertEqual(code, 2, out)
        self.assertIn("0/4", out)
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
            # 题单两行都带 command。这条断言是补回来的：第一版从 task 顶层找 `command`，
            # 而 `read_tasklist()` 把它放在 `raw` 里，于是明明有 2 条却报「0 条自带 command」。
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
                with self.subTest(drop=drop):
                    code, out = run(*args_for(f, drop=drop))
                    self.assertEqual(code, 2, out)
                    self.assertIn(marker, out)
                    self.assertIn("3/4", out)

    def test_example_tasklist_is_rejected(self):
        """随仓库分发的示例题单要判 ✗ 并说明它不是数据。"""
        with tempfile.TemporaryDirectory() as t:
            f = make_fixtures(pathlib.Path(t))
            code, out = run(*args_for({**f, "tasklist": PROFILE_DIR / "examples" / "tasklist.example.jsonl"}))
            self.assertEqual(code, 2, out)
            self.assertIn("A 题单 ✗", out)
            self.assertIn("示例题单", out)

    def test_stub_profile_is_rejected(self):
        """命令里带 stub 字样要判 ✗ —— 那是假执行器。"""
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            f = make_fixtures(tmp)
            profile = tmp / "stub-profile"
            gen = subprocess.run(
                [PY, "-X", "utf8", "-B", str(ROOT / "tools" / "make_profile.py"),
                 "--out", str(profile), "--python", PY],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
            )
            self.assertEqual(gen.returncode, 0, gen.stdout + gen.stderr)
            toml_path = profile / "profile.toml"
            text = toml_path.read_text(encoding="utf-8")
            stub_py = tmp / "stub_eval.py"
            stub_py.write_text("pass\n", encoding="utf-8")
            replaced = []
            for line in text.splitlines():
                if line.startswith("command = ["):
                    replaced.append('command = [' + ", ".join(
                        json.dumps(x) for x in (PY, "-X", "utf8", "-B", str(stub_py),
                                                "--tasks", "{tasks}", "--out", "{output}")) + "]")
                else:
                    replaced.append(line)
            toml_path.write_text("\n".join(replaced) + "\n", encoding="utf-8")

            code, out = run(*args_for({**f, "profile": profile}))
            self.assertEqual(code, 2, out)
            self.assertIn("B 执行器 ✗", out)
            self.assertIn("桩", out)

    def test_authorization_missing_label_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t)
            f = make_fixtures(tmp)
            bad = tmp / "bad-auth.md"
            bad.write_text("预算上限：0 元\n授权人：TESTONLY\n", encoding="utf-8")
            code, out = run(*args_for({**f, "auth": bad}))
            self.assertEqual(code, 2, out)
            self.assertIn("D 预算授权 ✗", out)
            self.assertIn("有效期", out, "要点名缺哪个标签")

    def test_credentials_content_is_never_printed(self):
        """门只查存在与大小，绝不回显凭据内容。"""
        with tempfile.TemporaryDirectory() as t:
            f = make_fixtures(pathlib.Path(t))
            code, out = run(*args_for(f))
            self.assertEqual(code, 0, out)
            for secret in ("TESTONLY-AUTODL-TOKEN", "TESTONLY PRIVATE KEY", '"key":"TESTONLY"'):
                with self.subTest(secret=secret):
                    self.assertNotIn(secret, out, "凭据内容不该出现在输出里")


if __name__ == "__main__":
    unittest.main(verbosity=2)
