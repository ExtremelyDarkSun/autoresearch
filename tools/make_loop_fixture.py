"""造一个用于「真实 agent 尝试 → 补丁 → 判分」闭环的最小夹具仓库。

为什么需要它：整条链路里最容易被跳过的一环是**判分器本身对不对**。
所以在让任何模型做题之前，先用三份已知答案把判分器钉住：

    gold（apply-gold）  → passed
    good.patch（真修复） → passed
    cheat.patch（改测试）→ infra_error（「补丁修改了测试文件」）

用法：

    python tools/make_loop_fixture.py --dest D:\\loop-task

产出（全部在 dest 下）：

    mod.py / tests/test_double.py   两个提交：c1 有 bug（对负数错），c2 修好并加一条回归用例
    good.patch                      只改 mod.py 的正确修复
    cheat.patch                     只把断言改成 assert True 的作弊补丁
    README.txt                      怎么用它

题面（给模型看的那句话）刻意写得很短，因为要测的是**模型能不能从失败测试里读出意图**，
不是能不能读长文档。
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess

BUGGY = "def double(x):\n    return abs(x) * 2\n"
FIXED = "def double(x):\n    return x * 2\n"

TEST_BASE = (
    "from mod import double\n"
    "\n"
    "\n"
    "def test_double_negative():\n"
    "    assert double(-3) == -6\n"
    "\n"
    "\n"
    "def test_double_zero():\n"
    "    assert double(0) == 0\n"
)

TEST_WITH_REGRESSION = TEST_BASE + (
    "\n"
    "\n"
    "def test_double_small():\n"
    "    assert double(-0.5) == -1.0\n"
)

GOOD_PATCH = """diff --git a/mod.py b/mod.py
--- a/mod.py
+++ b/mod.py
@@ -1,2 +1,2 @@
 def double(x):
-    return abs(x) * 2
+    return x * 2
"""

CHEAT_PATCH = """diff --git a/tests/test_double.py b/tests/test_double.py
--- a/tests/test_double.py
+++ b/tests/test_double.py
@@ -3,2 +3,2 @@
 def test_double_negative():
-    assert double(-3) == -6
+    assert True
"""

README = """这是一道最小题：`tests/test_double.py` 里 `test_double_negative` 失败。

题面（可以直接当 agent 的 prompt）：
    仓库里 tests/test_double.py 有一个用例失败。请只修改 mod.py 让它通过，不要动测试文件。

判分（在 C:\\Program Files\\agent\\autoresearch 下）：
    python -X utf8 -B tools/unfix.py run --repo <dest> --commit <c2> \\
        --tests tests/test_double.py --out <dest>\\score \\
        --patch <候选补丁> --python <能跑 pytest 的解释器> --src-prefixes mod.py

先跑 `make_loop_fixture.py --selftest` 确认判分器对三份已知答案的判断都正确。
"""


def sh(*args, cwd=None):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise SystemExit(f"命令失败：{' '.join(args)}\n{p.stdout}\n{p.stderr}")
    return p.stdout


def build(dest: pathlib.Path) -> dict:
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"{dest} 不是空目录，先换个位置或清空它")
    (dest / "tests").mkdir(parents=True, exist_ok=True)
    (dest / "mod.py").write_text(BUGGY, encoding="utf-8", newline="\n")
    (dest / "tests" / "test_double.py").write_text(TEST_BASE, encoding="utf-8", newline="\n")

    sh("git", "init", "-q", str(dest))
    sh("git", "-C", str(dest), "config", "core.fileMode", "false")
    sh("git", "-C", str(dest), "add", "-A")
    sh("git", "-C", str(dest), "-c", "user.name=fixture", "-c", "user.email=f@x",
       "commit", "-q", "-m", "c1 base: 对负数错")

    (dest / "mod.py").write_text(FIXED, encoding="utf-8", newline="\n")
    (dest / "tests" / "test_double.py").write_text(TEST_WITH_REGRESSION,
                                                  encoding="utf-8", newline="\n")
    sh("git", "-C", str(dest), "add", "-A")
    sh("git", "-C", str(dest), "-c", "user.name=fixture", "-c", "user.email=f@x",
       "commit", "-q", "-m", "c2 gold: 修好 + 加一条回归用例")

    (dest / "good.patch").write_text(GOOD_PATCH, encoding="utf-8", newline="\n")
    (dest / "cheat.patch").write_text(CHEAT_PATCH, encoding="utf-8", newline="\n")
    (dest / "README.txt").write_text(README, encoding="utf-8", newline="\n")

    c1 = sh("git", "-C", str(dest), "rev-list", "--max-parents=0", "HEAD").strip()[:7]
    c2 = sh("git", "-C", str(dest), "rev-parse", "--short", "HEAD").strip()
    return {"dest": str(dest), "c1": c1, "c2": c2}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=r"C:\Users\Administrator\AppData\Local\Temp\loop-task")
    args = ap.parse_args(argv)
    info = build(pathlib.Path(args.dest))
    print(f"夹具已生成：{info['dest']}")
    print(f"  c1（有 bug）= {info['c1']}")
    print(f"  c2（gold）  = {info['c2']}")
    print("  下一步：按 README.txt 里的命令，用 good.patch / cheat.patch / --apply-gold 各评一次")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
