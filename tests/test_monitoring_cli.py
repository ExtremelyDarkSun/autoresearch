# GENERATED-BY: 意图与过程监控 CLI 自然语言闭合路径测试
# LAST-EDIT: 2026-11-09（首版：close 的 --user-quote / --user-quote-file 经公开 CLI 打通；
#            真 DSHHost + 合成日志正例；无报告 / 无绑定 / 不匹配原话拒绝且零状态变动；旧 proof 路径仍可用）
# PURPOSE: 证明用户不需要写 Python 调用库：命令行即可用真实用户原话闭合 withdrawn，且独立报告约束不被跳过。
# ORIGIN: kernel/autoresearch/monitoring_cli.py 的 close 参数与派发；契约 kernel/contracts/intent_monitor.md §4.1；
#         服务层 Monitor.close(quote=…) 的绑定判据（tests/test_monitoring.py 已覆盖同一批反例）
# CUMULATIVE-DELTA: 首版。全部合成数据：会话 owner-alpha / monitor-bravo、实验 E1；不连真实宿主、不调用模型。
"""意图与过程监控 CLI 测试（只测 close 的两条路径与帮助文本）。

为什么用**真 `DSHHost` + 合成日志**：只用夹具自报「verified=True」证明不了原话真的来自宿主日志。
这里把合成会话写成 DSHHost 能读的格式（`root/sessions/<project>/<sid>/session.v1.jsonl.zstd`
+ 注入 decoder），由 CLI 用 `--adapter` 构造真 DSHHost 去读、去核原话与 spawn 独立性。

跑法：

    python -X utf8 -B -m unittest discover -s tests -p 'test_monitoring_cli.py' -v
    python -X utf8 -B tests/test_monitoring_cli.py
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.autoresearch import monitoring_cli  # noqa: E402
from kernel.autoresearch.host_evidence import DSHHost  # noqa: E402
from kernel.autoresearch.monitoring import Monitor, MonitoringError  # noqa: E402

OWNER = "owner-alpha"
CHILD = "monitor-bravo"
EXP = "E1"
USER_TEXT = "请守住已经通过的回归题，别把翻正的又弄丢"
WITHDRAW_TEXT = "这个方向先停掉，回归题不用再盯了"
SHORTER_QUOTE = "这个方向先停掉"

ADAPTER_SOURCE = '''import json
from pathlib import Path

from kernel.autoresearch.host_evidence import DSHHost


def build_host(log_root=None):
    root = Path(log_root).resolve()

    def decode(path):
        rows = json.loads((root / "rows.json").read_text(encoding="utf-8"))
        return rows[str(path)]

    return DSHHost(root=root, decoder=decode)
'''


# ---------------------------------------------------------------------------
# 合成宿主日志（真 DSHHost 来读）
# ---------------------------------------------------------------------------


class SyntheticLogs:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.sessions = self.root / "sessions" / "project"
        self.rows = {}
        self.dump()

    def write(self, sid, rows):
        path = self.sessions / sid / "session.v1.jsonl.zstd"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic-session-log")
        self.rows[str(path)] = rows
        self.dump()

    def dump(self):
        (self.root / "rows.json").write_text(
            json.dumps(self.rows, ensure_ascii=False), encoding="utf-8")

    def host(self):
        def decode(path):
            rows = json.loads((self.root / "rows.json").read_text(encoding="utf-8"))
            return rows[str(path)]

        return DSHHost(root=self.root, decoder=decode)

    def adapter(self):
        path = self.root / "host_adapter.py"
        path.write_text(ADAPTER_SOURCE, encoding="utf-8")
        return path


def user_row(seq, text, *, time, rpc):
    return {"seq": seq, "type": "user/message", "time": time,
            "data": {"source": {"kind": "user", "rpcId": rpc},
                     "message": {"role": "user",
                                 "content": [{"type": "text", "text": text}]}}}


def assistant_row(seq, text, *, time):
    return {"seq": seq, "type": "assistant/message", "time": time,
            "data": {"message": {"role": "assistant",
                                 "content": [{"type": "text", "text": text}]}}}


def header(sid, *, parent=None, depth=0, created=1000, seeded=False, origin=None):
    row = {"type": "session", "id": sid, "delegationDepth": depth,
           "isSeeded": seeded, "createdAt": created}
    if parent is not None:
        row["parentSession"] = parent
    if origin is not None:
        row["origin"] = origin
    return row


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


class CliCase(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        (self.root / "results.json").write_text("t1 通过\nt2 通过\nr1 通过\n", encoding="utf-8")
        (self.root / "plan.md").write_text("# 计划\n第 1 步：守住回归\n第 2 步：小样本验证\n",
                                           encoding="utf-8")
        self.logs = SyntheticLogs(self.root)
        # 本文件测「显式启用后」的 CLI 行为：默认关闭时写命令会拒写（另有单独用例覆盖）。
        policy = self.root / ".autoresearch" / "intent_monitor_policy.json"
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text(json.dumps({"enabled": True}), encoding="utf-8")
        self.mon = Monitor(self.root, host=self.logs.host())
        self.adapter = self.logs.adapter()

    # -- 运行 CLI ----------------------------------------------------------

    def _args(self, command, *extra):
        return ["--root", str(self.root), "--adapter", str(self.adapter),
                "--log-root", str(self.root), "--session", OWNER, command, *extra]

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        code = None
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.dict(os.environ, {"DSH_SESSION_ID": ""}):
            try:
                code = monitoring_cli.main(argv)
            except SystemExit as exc:  # argparse 的互斥 / 必填错误
                code = exc.code
        text = out.getvalue()
        payload = None
        if text.strip().startswith(("{", "[")):
            try:
                payload = json.loads(text)
            except ValueError:
                payload = None
        return code, payload, text, err.getvalue()

    def _close(self, *extra):
        return self._run(self._args("close", *extra))

    # -- 状态零写核对 ------------------------------------------------------

    def _tree(self):
        base = self.root / ".autoresearch"
        out = {}
        if base.exists():
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    out[path.relative_to(self.root).as_posix()] = hashlib.sha256(
                        path.read_bytes()).hexdigest()
        return out

    # -- 场景 --------------------------------------------------------------

    def _owner_rows(self):
        return [header(OWNER),
                user_row(0, USER_TEXT, time=1100, rpc="rpc-1"),
                assistant_row(1, "收到，继续守回归。", time=1200)]

    def _start(self, owner_rows):
        self.logs.write(OWNER, owner_rows)
        self.mon.register(OWNER, state="active", intent="守住已经通过的回归题",
                          sources=[{"session": OWNER, "seq": 0}],
                          reason="用户原话要求盯住回归", intent_id="INT-1")

    def _report(self, prep, quote, *, binding=True, reason="withdrawn", replacement=None):
        closure = {"target": "closed",
                   "basis": "用户最新原话撤回了这个方向，没有新原话要求继续",
                   "sources": [{"session": OWNER, "seq": 2},
                               {"path": "plan.md", "locator": "L1"}]}
        if binding:
            closure["user_command"] = {
                "expected": {"action": "close_intent", "intent_id": "INT-1",
                             "reason": reason, "replacement_intent_id": replacement,
                             "target": "closed"},
                "user_quote": quote}
        return {
            "prepared_id": prep["prepared_id"], "monitor_session": CHILD,
            "owner_session": OWNER, "exp": EXP, "verdict": "on_track",
            "covers": json.loads(json.dumps(prep["covers"])),
            "direction_sha256": prep["direction_sha256"],
            "intent": {"stated": "守住已经通过的回归题", "verdict": "consistent",
                       "note": "用户原话与最近几轮动作一致",
                       "quote": {"seq": 0, "text": USER_TEXT},
                       "sources": [{"session": OWNER, "seq": 0}]},
            "trajectory": {"summary": "先确认基线，再跑小样本验证", "verdict": "on_track",
                           "note": "每一步都能对上意图，没有换抓手",
                           "steps": [{"seq": 0, "what": "从用户原话确认意图与判据"}]},
            "findings": [{"text": "样本太小，读数不能外推到全量",
                          "sources": [{"path": "results.json", "locator": "L1"}]}],
            "issues": [],
            "next_direction": {"text": "先停这个方向，等负责人登记新意图",
                               "direction_sha256": prep["direction_sha256"],
                               "sources": [{"path": "plan.md", "locator": "L1"}]},
            "closure": closure,
            "evidence_reads": [{"kind": "raw_session", "session": OWNER, "seq": 0},
                               {"kind": "snapshot", "path": prep["snapshot"]}],
            "resolves": [],
        }

    def _prepare(self, owner_rows, *, user_text=WITHDRAW_TEXT, rpc="rpc-2"):
        owner_rows.append(user_row(2, user_text, time=1300, rpc=rpc))
        self.logs.write(OWNER, owner_rows)
        self.mon.event(OWNER, "result_analysis", exp=EXP, stage="pending",
                       note="结果回收待分析",
                       sources=[{"path": "results.json", "locator": "L1"}])
        return self.mon.prepare(OWNER, exp=EXP,
                                sources=[{"path": "results.json", "locator": "L1"}],
                                direction=[{"path": "plan.md", "locator": "L1"}])

    def _record_report(self, prep, owner_rows, report):
        body = json.dumps(report, ensure_ascii=False, sort_keys=True)
        self.logs.write(CHILD, [
            header(CHILD, parent=OWNER, depth=1, created=1400, origin="subagent"),
            {"seq": 0, "type": "subagent/descriptor", "time": 1400,
             "data": {"provider": "spawn", "mode": "one-shot"}},
            assistant_row(1, body, time=1500),
        ])
        owner_rows.extend([
            {"seq": 3, "type": "tool/call", "time": 1350,
             "data": {"name": "subagent", "callId": "call-1",
                      "arguments": {"prompt": "请独立审计 " + prep["prepared_id"]}}},
            {"seq": 4, "type": "tool/result", "time": 1360,
             "data": {"message": {"toolCallId": "call-1",
                                  "content": [{"type": "text", "text": "started " + CHILD}]}}},
            {"seq": 5, "type": "subagent/catalog", "time": 1370,
             "data": {"childId": CHILD, "mode": "one-shot"}},
        ])
        self.logs.write(OWNER, owner_rows)
        return self.mon.record(OWNER, report_bytes=body.encode("utf-8"),
                               proof={"session_id": CHILD, "seq": 1})

    def _scenario(self, *, binding=True, reason="withdrawn", replacement=None):
        owner_rows = self._owner_rows()
        self._start(owner_rows)
        prep = self._prepare(owner_rows)
        quote = {"session": OWNER, "seq": 2, "text": WITHDRAW_TEXT}
        recorded = self._record_report(
            prep, owner_rows,
            self._report(prep, quote, binding=binding, reason=reason, replacement=replacement))
        return {"prep": prep, "recorded": recorded, "owner_rows": owner_rows}


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


class CloseNaturalQuoteCliTests(CliCase):
    def test_close_help_documents_both_paths(self):
        code, _, text, _ = self._run(["close", "--help"])
        self.assertEqual(code, 0)
        for token in ("--user-quote", "--user-quote-file", "--proof", "--proof-seq"):
            self.assertIn(token, text)
        self.assertIn("独立报告", text)
        self.assertIn("互斥", text)

    def test_close_withdrawn_through_cli_with_real_host(self):
        scenario = self._scenario()
        # CLI 用的是真 DSHHost（不是只返回 verified=True 的夹具）
        host = monitoring_cli._load_host(str(self.adapter), str(self.root), self.root)
        self.assertIsInstance(host, DSHHost)
        self.assertTrue(host.session(OWNER)["session_id"].endswith(OWNER))
        quote = {"session": OWNER, "seq": 2, "text": WITHDRAW_TEXT}
        code, payload, text, _ = self._close(
            "--reason", "withdrawn", "--to", "closed",
            "--report-id", scenario["recorded"]["report_id"],
            "--user-quote", json.dumps(quote, ensure_ascii=False))
        self.assertEqual(code, 0, text)
        self.assertEqual(payload["state"], "closed")
        self.assertEqual(payload["command"], {"action": "close_intent", "intent_id": "INT-1",
                                              "reason": "withdrawn", "replacement_intent_id": None,
                                              "target": "closed"})
        self.assertIsNone(payload["proof_seq"])
        self.assertEqual(payload["evidence"]["session"], OWNER)
        self.assertEqual(payload["evidence"]["text"], WITHDRAW_TEXT)
        self.assertEqual(payload["binding"]["report_id"], scenario["recorded"]["report_id"])
        self.assertEqual(payload["binding"]["monitor_session"], CHILD)
        # 状态真的落账：用 CLI 自己读回来（同一 adapter、同一账本）
        code, status, text, _ = self._run(self._args("status"))
        self.assertEqual(code, 0, text)
        self.assertEqual(status["state"], "closed")
        self.assertEqual(status["reports"][0]["report_id"], scenario["recorded"]["report_id"])

    def test_close_without_report_is_rejected_and_writes_nothing(self):
        owner_rows = self._owner_rows()
        self._start(owner_rows)
        self._prepare(owner_rows)  # 只准备材料，没有独立报告
        before = self._tree()
        self.assertTrue(before)  # 账本 / 材料包确实存在，零写比较才有意义
        code, payload, _, _ = self._close(
            "--reason", "withdrawn", "--to", "closed",
            "--report-id", "MON-0000000000000000",
            "--user-quote", json.dumps({"session": OWNER, "seq": 2, "text": WITHDRAW_TEXT},
                                       ensure_ascii=False))
        self.assertEqual(code, 2)
        self.assertIn("找不到该监控报告", "；".join(payload["problems"]))
        self.assertEqual(self._tree(), before)
        code, status, _, _ = self._run(self._args("status"))
        self.assertEqual(status["state"], "active")
        self.assertEqual(status["reports"], [])

    def test_close_quote_without_report_binding_is_rejected(self):
        scenario = self._scenario(binding=False)
        before = self._tree()
        code, payload, _, _ = self._close(
            "--reason", "withdrawn", "--to", "closed",
            "--report-id", scenario["recorded"]["report_id"],
            "--user-quote", json.dumps({"seq": 2, "text": WITHDRAW_TEXT}, ensure_ascii=False))
        self.assertEqual(code, 2)
        self.assertIn("closure.user_command", "；".join(payload["problems"]))
        self.assertEqual(self._tree(), before)
        code, status, _, _ = self._run(self._args("status"))
        self.assertEqual(status["state"], "active")

    def test_close_mismatched_quote_is_rejected_and_writes_nothing(self):
        scenario = self._scenario()
        wrong = self.root / "wrong_quote.json"
        wrong.write_text(json.dumps({"session": OWNER, "seq": 2, "text": SHORTER_QUOTE},
                                    ensure_ascii=False), encoding="utf-8")
        right = self.root / "right_quote.json"
        right.write_text(json.dumps({"session": OWNER, "seq": 2, "text": WITHDRAW_TEXT},
                                    ensure_ascii=False), encoding="utf-8")
        before = self._tree()
        self.assertTrue(before)  # 账本 / 材料包确实存在，零写比较才有意义
        code, payload, _, _ = self._close(
            "--reason", "withdrawn", "--to", "closed",
            "--report-id", scenario["recorded"]["report_id"],
            "--user-quote-file", str(wrong))
        self.assertEqual(code, 2)
        self.assertIn("同一条", "；".join(payload["problems"]))
        self.assertEqual(self._tree(), before)
        code, status, _, _ = self._run(self._args("status"))
        self.assertEqual(status["state"], "active")
        # 同一场景改成正确原话（@文件 形式）就能闭合：拒绝没有把状态弄坏
        code, payload, text, _ = self._close(
            "--reason", "withdrawn", "--to", "closed",
            "--report-id", scenario["recorded"]["report_id"],
            "--user-quote", "@" + str(right))
        self.assertEqual(code, 0, text)
        self.assertEqual(payload["state"], "closed")

    def test_close_user_quote_and_proof_are_mutually_exclusive(self):
        scenario = self._scenario()
        quote = json.dumps({"seq": 2, "text": WITHDRAW_TEXT}, ensure_ascii=False)
        for extra in (("--proof-seq", "2"), ("--proof", str(self.root / "proof.json"))):
            with self.subTest(extra=extra):
                code, _, _, err = self._close(
                    "--reason", "withdrawn", "--to", "closed",
                    "--report-id", scenario["recorded"]["report_id"],
                    "--user-quote", quote, *extra)
                self.assertEqual(code, 2)
                self.assertIn("not allowed with", err)
        # 与 --quote-text 混用：CLI 层直接拒
        code, payload, _, _ = self._close(
            "--reason", "withdrawn", "--to", "closed",
            "--report-id", scenario["recorded"]["report_id"],
            "--user-quote", quote, "--quote-text", WITHDRAW_TEXT)
        self.assertEqual(code, 2)
        self.assertIn("--quote-text", "；".join(payload["problems"]))
        # achieved 不接受原话
        code, payload, _, _ = self._close(
            "--reason", "achieved", "--to", "closed",
            "--report-id", scenario["recorded"]["report_id"], "--user-quote", quote)
        self.assertEqual(code, 2)
        self.assertIn("verdict", "；".join(payload["problems"]))
        # 两条路都不给：拒绝且点明两条路
        code, payload, _, _ = self._close(
            "--reason", "withdrawn", "--to", "closed",
            "--report-id", scenario["recorded"]["report_id"])
        self.assertEqual(code, 2)
        text = "；".join(payload["problems"])
        self.assertIn("--user-quote", text)
        self.assertIn("--proof", text)
        # 以上失败都在写账之前：状态仍是 active
        code, status, _, _ = self._run(self._args("status"))
        self.assertEqual(status["state"], "active")

    def test_close_replaced_through_cli_binds_new_intent_id(self):
        scenario = self._scenario(reason="replaced", replacement="INT-2")
        quote = json.dumps({"session": OWNER, "seq": 2, "text": WITHDRAW_TEXT}, ensure_ascii=False)
        before = self._tree()
        # 替换 id 与报告绑定不一致：逐键比对拒绝，零状态变动
        code, payload, _, _ = self._close(
            "--reason", "replaced", "--to", "closed",
            "--report-id", scenario["recorded"]["report_id"],
            "--replacement-intent", "INT-3", "--user-quote", quote)
        self.assertEqual(code, 2)
        self.assertIn("逐键", "；".join(payload["problems"]))
        self.assertEqual(self._tree(), before)
        # 与绑定一致：CLI 完成替换闭合
        code, payload, text, _ = self._close(
            "--reason", "replaced", "--to", "closed",
            "--report-id", scenario["recorded"]["report_id"],
            "--replacement-intent", "INT-2", "--user-quote", quote)
        self.assertEqual(code, 0, text)
        self.assertEqual(payload["state"], "closed")
        self.assertEqual(payload["command"]["reason"], "replaced")
        self.assertEqual(payload["command"]["replacement_intent_id"], "INT-2")
        self.assertEqual(payload["binding"]["expected"]["replacement_intent_id"], "INT-2")

    def test_close_json_command_path_still_works_through_cli(self):
        command = {"action": "close_intent", "intent_id": "INT-1", "reason": "withdrawn",
                   "replacement_intent_id": None, "target": "closed"}
        owner_rows = self._owner_rows()
        self._start(owner_rows)
        prep = self._prepare(owner_rows, user_text=json.dumps(command, ensure_ascii=False))
        recorded = self._record_report(prep, owner_rows,
                                       self._report(prep, None, binding=False))
        code, payload, text, _ = self._close(
            "--reason", "withdrawn", "--to", "closed",
            "--report-id", recorded["report_id"], "--proof-seq", "2")
        self.assertEqual(code, 0, text)
        self.assertEqual(payload["state"], "closed")
        self.assertEqual(payload["command"], command)
        self.assertEqual(payload["proof_seq"], 2)
        self.assertIsNone(payload["binding"])

    def test_close_quote_rejects_bad_input_shapes(self):
        scenario = self._scenario()
        report_id = scenario["recorded"]["report_id"]
        cases = [
            (["--user-quote", "不是 JSON"], "--user-quote"),
            (["--user-quote", json.dumps(["seq", 2])], "JSON 对象"),
            (["--user-quote", "@" + str(self.root / "missing.json")], "不存在"),
            (["--user-quote-file", str(self.root / "missing.json")], "不存在"),
        ]
        for extra, expected in cases:
            with self.subTest(extra=extra):
                code, payload, _, _ = self._close(
                    "--reason", "withdrawn", "--to", "closed",
                    "--report-id", report_id, *extra)
                self.assertEqual(code, 2)
                self.assertIn(expected, "；".join(payload["problems"]))
        self.assertEqual(self._run(self._args("status"))[1]["state"], "active")


class SwitchCliTests(unittest.TestCase):
    """CLI 层开关：`enabled` 只读；默认关闭时 `check` 放行且不写盘、写命令拒写；启用后按原判据。"""

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.dict(os.environ, {"DSH_SESSION_ID": ""}):
            try:
                code = monitoring_cli.main(argv)
            except SystemExit as exc:
                code = exc.code
        text = out.getvalue()
        payload = json.loads(text) if text.strip().startswith("{") else None
        return code, payload, text, err.getvalue()

    def _tree(self):
        base = self.root / ".autoresearch"
        if not base.is_dir():
            return {}
        return {path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(base.rglob("*")) if path.is_file()}

    def test_enabled_command_is_read_only_and_defaults_off(self):
        before = self._tree()
        code, payload, text, _ = self._run(["--root", str(self.root), "enabled"])
        self.assertEqual(code, 0, text)
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["state"], "disabled")
        self.assertEqual(self._tree(), before, "enabled 命令必须零写")
        policy = self.root / ".autoresearch" / "intent_monitor_policy.json"
        policy.parent.mkdir(parents=True)
        policy.write_text('{"enabled": true}', encoding="utf-8")
        code, payload, text, _ = self._run(["--root", str(self.root), "enabled"])
        self.assertEqual(code, 0, text)
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["state"], "enabled")

    def test_off_check_passes_without_adapter_and_writes_nothing(self):
        before = self._tree()
        code, payload, text, _ = self._run(
            ["--root", str(self.root), "--session", OWNER, "check", "--exp", EXP])
        self.assertEqual(code, 0, text)
        self.assertTrue(payload["allow"])
        self.assertEqual(payload["state"], "disabled")
        self.assertFalse(payload["undecidable"])
        self.assertEqual(self._tree(), before, "默认关闭时 check 不许建目录 / 写账")

    def test_off_write_commands_are_refused(self):
        code, payload, _, _ = self._run(
            ["--root", str(self.root), "--session", OWNER, "register",
             "--state", "none", "--reason", "关闭时不该登记"])
        self.assertEqual(code, 2)
        self.assertIn("拒写", "；".join(payload["problems"]))
        self.assertFalse((self.root / ".autoresearch").exists())


if __name__ == "__main__":
    unittest.main()
