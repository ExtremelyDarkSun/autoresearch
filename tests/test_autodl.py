# GENERATED-BY: portable extraction (agent, 2026-11-09 02:10 +08:00)
# LAST-EDIT: portable extraction (agent, 2026-11-09 02:10 +08:00)
# PURPOSE: AutoDL 后端（采集 / 事件 / 绑定 / worker / 云机操作护栏）的离线测试 —— **只用标准库 unittest**。
# ORIGIN: tool/autodl_availability.py 与 autodl_events.py 的自检（_selftest）机制；测试改成外部文件、夹具全部中性
# CUMULATIVE-DELTA: 首版用 pytest 风格；**本版改成纯 unittest**（⛔ 不依赖 pytest，也不靠 skip 缺依赖）：
#   46 个用例逐条保留断言，覆盖假传输层 / 只监听回环的假 HTTP / 事件全生命周期 / worker / 云机操作护栏。
#   ⛔ 不读真凭据文件、不打印凭据、不调度任何云机操作（只测护栏）、不访问互联网。
#
# 跑法（仓库根，任意有 Python 3.11+ 的机器）：python -m unittest discover -s tests -p "test_autodl.py" -q
"""离线测试：`kernel/autoresearch/backends/autodl/`（标准库 unittest，无第三方依赖）。

原则：
- ⛔ 不访问互联网（唯一「网络」是一个只绑定 127.0.0.1 的假服务，且用完即关）；
- ⛔ 不读真实凭据文件（用临时环境变量或临时文件）；
- ⛔ 不调用任何云机操作（只断言护栏会在未授权时拒绝）。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kernel.autoresearch.backends.autodl import availability, cloud, events, worker  # noqa: E402
from kernel.autoresearch.workspace_layout import resolve_layout, write_layout_marker  # noqa: E402

TZ = timezone(timedelta(hours=8))
TOKEN_ENV = "AUTORESEARCH_TEST_TOKEN"


# ── 夹具 ──────────────────────────────────────────────────
@contextlib.contextmanager
def env_var(name: str, value: str):
    """临时设置环境变量（⛔ 不碰真实凭据；用完恢复原样）。"""
    previous = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


@contextlib.contextmanager
def without_env(name: str):
    previous = os.environ.get(name)
    os.environ.pop(name, None)
    try:
        yield
    finally:
        if previous is not None:
            os.environ[name] = previous


def sample_document() -> dict:
    return {"code": "Success", "data": {"list": [
        {"machine_alias": "m-01", "region_name": "region-a", "status": "running",
         "status_at": "2026-01-01T00:00:00+08:00", "start_mode": "gpu",
         "snapshot_gpu_alias_name": "GPU-X", "gpu_all_num": 8, "gpu_idle_num": 3,
         "payg_price": 3280, "root_fs_used_rate": 0.839, "uuid": "u-1",
         "root_password": "LEAK-CANARY-PASSWORD", "jupyter_token": "LEAK-CANARY-JUPYTER",
         "ssh_command": "ssh -p 1 root@example.invalid"},
        {"machine_alias": "m-02", "region_name": "region-a", "status": "shutdown",
         "start_mode": "gpu", "gpu_all_num": 8, "gpu_idle_num": 0, "uuid": "u-2"},
        {"machine_alias": "m-03", "region_name": "region-a", "status": "shutdown",
         "start_mode": "gpu", "gpu_all_num": 8, "gpu_idle_num": 2, "uuid": "u-3"},
        {"machine_alias": "m-04", "region_name": "region-b", "status": "shutdown",
         "start_mode": "non_gpu", "gpu_all_num": 9, "gpu_idle_num": 9, "uuid": "u-4"},
        {"machine_alias": "m-05", "region_name": "region-b", "status": "shutdown",
         "start_mode": "non_gpu", "uuid": "u-5"},
        {"machine_alias": "m-06", "region_name": "region-a", "status": "stopping",
         "start_mode": "gpu", "gpu_all_num": 8, "gpu_idle_num": 2, "uuid": "u-6"},
    ]}}


def fake_transport(document=None, *, status: int = 200, raises: Exception | None = None,
                   delay: float = 0.0, calls: list | None = None):
    """造一个假传输层：`(url, body, headers, timeout) -> (status, body_bytes)`。"""
    payload = json.dumps(document if document is not None else sample_document()).encode("utf-8")

    def transport(url, body, headers, timeout):
        if calls is not None:
            calls.append({"url": url, "body": body, "headers": dict(headers), "timeout": timeout})
        if delay:
            import time as _time
            _time.sleep(delay)
        if raises is not None:
            raise raises
        return status, payload

    return transport


def report_of(rows: "list[dict]", *, ok: bool = True,
              checked_at: str = "2026-01-01T00:30:00+08:00") -> dict:
    return {"ok": ok, "checked_at": checked_at, "why_not": None if ok else "接口拒绝",
            "machines": rows}


def row_of(machine: str, verdict: str, idle, *, in_list: bool = True,
           status: str = "已关机") -> dict:
    return {"machine": machine, "boot_availability": verdict, "gpu_idle_num": idle,
            "gpu_all_num": 8, "status": status, "status_raw": "shutdown", "in_list": in_list}


BASE_ROWS = [row_of("m-01", "可能开不了", 0), row_of("m-02", "已在用", 0, status="运行中"),
             row_of("m-03", "说不准", 0), row_of("m-04", "说不准", 0)]


class TempDirMixin:
    """给每个用例一个临时目录，结束时自动删。"""

    def tmpdir(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)


class AvailabilityStateMixin:
    """每个用例都从「没有上次成功清单、没有缓存、没有测试凭据」开始。"""

    def setUp(self):
        super().setUp()
        availability._LAST_GOOD.clear()                       # noqa: SLF001
        availability._CACHE.update(at=None, report=None)      # noqa: SLF001
        os.environ.pop(TOKEN_ENV, None)

    def tearDown(self):
        availability._LAST_GOOD.clear()                       # noqa: SLF001
        availability._CACHE.update(at=None, report=None)      # noqa: SLF001
        os.environ.pop(TOKEN_ENV, None)
        super().tearDown()


# ── availability：凭据 ────────────────────────────────────
class CredentialTests(unittest.TestCase):
    def test_credential_from_env_name(self):
        with env_var(TOKEN_ENV, "token-value-1"):
            token, ref, why = availability.resolve_credential(env_var=TOKEN_ENV)
        self.assertEqual(token, "token-value-1")
        self.assertEqual(ref, f"env:{TOKEN_ENV}")
        self.assertIsNone(why)

    def test_credential_missing_env_reports_name_not_value(self):
        token, ref, why = availability.resolve_credential(env_var="AUTORESEARCH_NO_SUCH_VAR")
        self.assertIsNone(token)
        self.assertEqual(ref, "env:AUTORESEARCH_NO_SUCH_VAR")
        self.assertIn("AUTORESEARCH_NO_SUCH_VAR", why)
        self.assertIn("没有值", why)

    def test_credential_from_explicit_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "credential"
            path.write_text("  file-token-value  \n", encoding="utf-8")
            token, ref, why = availability.resolve_credential(token_file=path)
        self.assertEqual(token, "file-token-value")
        self.assertTrue(ref.startswith("file:"))
        self.assertIsNone(why)

    def test_credential_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            token, _ref, why = availability.resolve_credential(token_file=Path(tmp) / "nope")
        self.assertIsNone(token)
        self.assertIn("不存在", why)

    def test_credential_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "credential"
            path.write_text("   \n", encoding="utf-8")
            token, _ref, why = availability.resolve_credential(token_file=path)
        self.assertIsNone(token)
        self.assertIn("空的", why)


# ── availability：采集与判读 ──────────────────────────────
class AvailabilityTests(TempDirMixin, AvailabilityStateMixin, unittest.TestCase):
    def test_collect_normalizes_and_never_leaks_credentials(self):
        with env_var(TOKEN_ENV, "LEAK-CANARY-TOKEN"):
            report = availability.collect(token_env=TOKEN_ENV, transport=fake_transport(),
                                          now=datetime(2026, 1, 1, 0, 30, tzinfo=TZ))
        self.assertTrue(report["ok"])
        self.assertTrue(report["read_only"])
        rows = {row["machine"]: row for row in report["machines"]}
        self.assertEqual(rows["m-01"]["status"], "运行中")
        self.assertEqual(rows["m-01"]["boot_availability"], "已在用")
        self.assertTrue(rows["m-01"]["certain"])
        self.assertEqual(rows["m-02"]["boot_availability"], "可能开不了")
        self.assertEqual(rows["m-03"]["boot_availability"], "可能可开")
        self.assertEqual(rows["m-04"]["boot_availability"], "说不准")      # 无卡档
        self.assertFalse(rows["m-05"]["gpu_headroom_known"])              # 字段缺失
        self.assertEqual(rows["m-05"]["boot_availability"], "说不准")
        self.assertEqual(rows["m-06"]["boot_availability"], "说不准")      # 过渡态
        dumped = availability.to_json(report)
        for canary in ("LEAK-CANARY-TOKEN", "LEAK-CANARY-PASSWORD", "LEAK-CANARY-JUPYTER",
                       "root_password", "jupyter_token", "ssh_command", "root@"):
            self.assertNotIn(canary, dumped)
        self.assertTrue(report["source"]["credential_present"])
        self.assertNotIn("LEAK-CANARY-TOKEN", report["source"]["credential_ref"])

    def test_shutdown_is_not_unavailable(self):
        report = availability.collect(transport=fake_transport(), now=datetime(2026, 1, 1, tzinfo=TZ))
        for row in report["machines"]:
            self.assertNotEqual(row["boot_availability"], "不可用")
        for row in report["machines"]:
            if row["boot_availability"] in ("可能可开", "可能开不了"):
                self.assertIn("试开机", row["boot_reason"])

    def test_collect_machine_filter_keeps_named_rows_and_marks_unknown(self):
        report = availability.collect(transport=fake_transport(), machines=["m-01", "m-99"],
                                      now=datetime(2026, 1, 1, tzinfo=TZ))
        rows = {row["machine"]: row for row in report["machines"]}
        self.assertEqual(rows["m-01"]["boot_availability"], "已在用")
        self.assertIn("不在本次查询拿到的机器列表里", rows["m-99"]["boot_reason"])

    def test_query_failure_keeps_named_rows_and_all_unknown(self):
        transport = fake_transport({"code": "AuthorizeFailed", "msg": "登录超时"})
        report = availability.collect(transport=transport, machines=["m-01"],
                                      now=datetime(2026, 1, 1, tzinfo=TZ))
        self.assertFalse(report["ok"])
        self.assertIn("凭据", report["why_not"])
        self.assertEqual(report["machines"][0]["boot_availability"], "说不准")
        self.assertIn("说不清", availability.render_line(report))

    def test_timeout_is_reported_as_timeout(self):
        report = availability.collect(transport=fake_transport(raises=TimeoutError("timed out")),
                                      now=datetime(2026, 1, 1, tzinfo=TZ))
        self.assertFalse(report["ok"])
        self.assertIn("超时", report["why_not"])

    def test_empty_list_is_zero_rows_not_error(self):
        report = availability.collect(
            transport=fake_transport({"code": "Success", "data": {"list": []}}),
            now=datetime(2026, 1, 1, tzinfo=TZ))
        self.assertTrue(report["ok"])
        self.assertEqual(report["machines"], [])

    def test_deadline_bounds_wall_clock(self):
        import time as _time
        started = _time.monotonic()
        report = availability.collect(transport=fake_transport(delay=1.2), deadline_s=0.2,
                                      now=datetime(2026, 1, 1, tzinfo=TZ))
        spent = _time.monotonic() - started
        self.assertFalse(report["ok"])
        self.assertIn("上限", report["why_not"])
        self.assertLess(spent, 0.8)

    def test_failed_query_still_lists_previous_machine_names(self):
        availability.collect(transport=fake_transport(), now=datetime(2026, 1, 1, tzinfo=TZ))
        lost = availability.collect(transport=fake_transport(raises=TimeoutError("timed out")),
                                    now=datetime(2026, 1, 1, tzinfo=TZ))
        self.assertEqual(len(lost["machines"]), 6)
        self.assertTrue(all(row["boot_availability"] == "说不准" for row in lost["machines"]))
        self.assertEqual(lost["source"]["machine_names_from"], "memory")
        self.assertIn("没查成", availability.render_markdown(lost))

    def test_name_cache_survives_process_restart(self):
        tmp = self.tmpdir()
        cache = tmp / "names.json"
        availability.collect(transport=fake_transport(), remember=True, cache_file=cache,
                             now=datetime(2026, 1, 1, tzinfo=TZ))
        self.assertTrue(cache.is_file())
        self.assertEqual(json.loads(cache.read_text(encoding="utf-8"))["machines"][0], "m-01")
        availability._LAST_GOOD.clear()                       # noqa: SLF001 —— 模拟换进程
        cold = availability.collect(transport=fake_transport(raises=TimeoutError("timed out")),
                                    remember=False, cache_file=cache,
                                    now=datetime(2026, 1, 1, tzinfo=TZ))
        self.assertEqual(len(cold["machines"]), 6)
        self.assertEqual(cold["source"]["machine_names_from"], "cache")

    def test_render_markdown_and_line(self):
        report = availability.collect(transport=fake_transport(),
                                      now=datetime(2026, 1, 1, 0, 30, tzinfo=TZ))
        markdown = availability.render_markdown(report)
        self.assertIn("| 机器 |", markdown)
        self.assertIn("开机可用性", markdown)
        self.assertIn("没有开机", markdown)
        self.assertIn("读取不等于开机", markdown)
        self.assertTrue(availability.render_line(report).startswith("机器可用性"))
        self.assertNotIn("|", availability.render_markdown(report, table=False))
        self.assertTrue(availability.render_markdown(report, heading="").startswith("**"))
        bad_h2 = [line for line in markdown.splitlines()
                  if line.startswith("## ") and not line.startswith("### ")]
        self.assertEqual(bad_h2, [])

    def test_report_criteria_declares_read_only_semantics(self):
        report = availability.collect(transport=fake_transport(), now=datetime(2026, 1, 1, tzinfo=TZ))
        for key in ("read_only", "shutdown_is_not_unavailable", "unknown_when_fields_missing",
                    "never_probe_by_booting", "reading_is_not_booting"):
            self.assertIn(key, report["criteria"])

    def test_availability_selftest_passes(self):
        self.assertEqual(availability.selftest(), 0)


# ── 真实 HTTP 路径：只监听回环，不访问互联网 ─────────────
class _StubHandler(BaseHTTPRequestHandler):
    document: dict = {}
    seen: "list[dict]" = []

    def do_POST(self):  # noqa: N802 —— http.server 的接口名
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length)
        type(self).seen.append({"path": self.path, "body": body,
                                "headers": dict(self.headers)})
        payload = json.dumps(type(self).document).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # noqa: D401 —— 静音
        return


class LoopbackHttpTests(AvailabilityStateMixin, unittest.TestCase):
    def test_loopback_http_end_to_end(self):
        _StubHandler.document = sample_document()
        _StubHandler.seen = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with env_var(TOKEN_ENV, "token-value"):
            url = f"http://127.0.0.1:{server.server_address[1]}/api/v1/instance"
            report = availability.collect(transport=None, api_url=url, timeout=5.0,
                                          token_env=TOKEN_ENV, now=datetime(2026, 1, 1, tzinfo=TZ))
        self.assertTrue(report["ok"], report.get("why_not"))
        self.assertEqual(len(report["machines"]), 6)
        self.assertTrue(_StubHandler.seen, "假服务没有收到请求")
        sent = _StubHandler.seen[0]
        self.assertEqual(json.loads(sent["body"].decode("utf-8"))["page_size"], 100)
        # 只发一次请求：⛔ 不做每台一次的 detail 查询
        self.assertEqual(len(_StubHandler.seen), 1)


# ── events：判定、冷却、基线、队列、绑定、回执 ───────────
class EventsTests(TempDirMixin, unittest.TestCase):
    def test_first_run_is_baseline_only(self):
        root = self.tmpdir()
        result = events.record(root, report_of(BASE_ROWS))
        self.assertTrue(result["baseline"])
        self.assertEqual(result["emitted"], [])
        self.assertTrue(events.state_path(root).is_file())
        self.assertEqual(events.read_pending(root), [])

    def test_verdict_trigger_emits_one_event(self):
        root = self.tmpdir()
        events.record(root, report_of(BASE_ROWS))
        result = events.record(
            root, report_of([row_of("m-01", "可能可开", 0)] + BASE_ROWS[1:]),
            now=datetime(2026, 1, 1, 1, 0, tzinfo=TZ))
        self.assertEqual(len(result["emitted"]), 1)
        event = result["emitted"][0]
        self.assertEqual(event["machine"], "m-01")
        self.assertEqual(event["triggers"], ["verdict"])
        self.assertEqual(event["kind"], events.EVENT_KIND)
        self.assertIn("m-01", event["text"])
        self.assertIn("可能可开", event["text"])
        self.assertIn("没有试开机", event["text"])
        self.assertIn("没动机器", event["text"])
        pending = events.read_pending(root)
        self.assertEqual(len(pending), 1)
        self.assertTrue(pending[0]["_file"].endswith(".json"))
        ledger_lines = events.ledger_path(root).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(ledger_lines), 1)

    def test_idle_trigger_when_verdict_unchanged(self):
        root = self.tmpdir()
        events.record(root, report_of(BASE_ROWS))
        result = events.record(
            root, report_of(BASE_ROWS[:2] + [row_of("m-03", "说不准", 4)] + BASE_ROWS[3:]),
            now=datetime(2026, 1, 1, 1, 0, tzinfo=TZ))
        self.assertEqual([event["triggers"] for event in result["emitted"]], [["idle"]])
        self.assertIn("能不能真开起来", result["emitted"][0]["text"])

    def test_idle_trigger_can_be_disabled(self):
        root = self.tmpdir()
        events.record(root, report_of(BASE_ROWS))
        result = events.record(
            root, report_of(BASE_ROWS[:2] + [row_of("m-03", "说不准", 4)] + BASE_ROWS[3:]),
            idle_trigger=False, now=datetime(2026, 1, 1, 1, 0, tzinfo=TZ))
        self.assertEqual(result["emitted"], [])

    def test_event_is_idempotent_when_already_available(self):
        root = self.tmpdir()
        events.record(root, report_of(BASE_ROWS))
        events.record(root, report_of([row_of("m-01", "可能可开", 1)] + BASE_ROWS[1:]),
                      now=datetime(2026, 1, 1, 1, 0, tzinfo=TZ))
        again = events.record(root, report_of([row_of("m-01", "可能可开", 3)] + BASE_ROWS[1:]),
                              now=datetime(2026, 1, 1, 1, 10, tzinfo=TZ))
        self.assertEqual(again["emitted"], [])

    def test_cooldown_suppresses_repeat_then_lets_it_through(self):
        root = self.tmpdir()
        start = datetime(2026, 1, 1, 1, 0, tzinfo=TZ)
        events.record(root, report_of(BASE_ROWS), now=start)
        first = events.record(root, report_of([row_of("m-01", "可能可开", 1)] + BASE_ROWS[1:]),
                              now=start + timedelta(minutes=1))
        self.assertEqual(len(first["emitted"]), 1)
        events.record(root, report_of(BASE_ROWS), now=start + timedelta(minutes=2))
        cooled = events.record(root, report_of([row_of("m-01", "可能可开", 1)] + BASE_ROWS[1:]),
                               now=start + timedelta(minutes=5))
        self.assertEqual(cooled["emitted"], [])
        events.record(root, report_of(BASE_ROWS), now=start + timedelta(minutes=30))
        warmed = events.record(root, report_of([row_of("m-01", "可能可开", 1)] + BASE_ROWS[1:]),
                               now=start + timedelta(minutes=45))
        self.assertEqual(len(warmed["emitted"]), 1)

    def test_failed_query_does_not_touch_state_or_emit(self):
        root = self.tmpdir()
        events.record(root, report_of(BASE_ROWS))
        before = events.state_path(root).read_text(encoding="utf-8")
        failed = events.record(root, {"ok": False, "why_not": "超时", "machines": []},
                               now=datetime(2026, 1, 1, 1, 0, tzinfo=TZ))
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["emitted"], [])
        self.assertEqual(events.state_path(root).read_text(encoding="utf-8"), before)

    def test_machine_not_in_list_is_not_a_change(self):
        root = self.tmpdir()
        events.record(root, report_of(BASE_ROWS))
        result = events.record(root, report_of([row_of("m-01", "可能可开", 3, in_list=False)]
                                               + BASE_ROWS[1:]),
                               now=datetime(2026, 1, 1, 1, 0, tzinfo=TZ))
        self.assertEqual(result["emitted"], [])

    def test_watch_list_limits_events(self):
        root = self.tmpdir()
        events.record(root, report_of(BASE_ROWS + [row_of("m-99", "可能开不了", 0)]))
        result = events.record(
            root,
            report_of([row_of("m-01", "可能可开", 1)] + BASE_ROWS[1:]
                      + [row_of("m-99", "可能可开", 1)]),
            watch=["m-01"], now=datetime(2026, 1, 1, 1, 0, tzinfo=TZ))
        self.assertEqual([event["machine"] for event in result["emitted"]], ["m-01"])

    def test_watch_all_covers_unlisted_machines(self):
        root = self.tmpdir()
        events.record(root, report_of(BASE_ROWS + [row_of("m-99", "可能可开", 0)]))
        result = events.record(root, report_of(BASE_ROWS + [row_of("m-99", "可能可开", 3)]),
                               watch="all", now=datetime(2026, 1, 1, 1, 0, tzinfo=TZ))
        self.assertEqual([event["machine"] for event in result["emitted"]], ["m-99"])

    def test_broken_state_and_bad_event_files_are_tolerated(self):
        root = self.tmpdir()
        events.state_path(root).parent.mkdir(parents=True, exist_ok=True)
        events.state_path(root).write_text("{not json", encoding="utf-8")
        result = events.record(root, report_of(BASE_ROWS))
        self.assertTrue(result["baseline"])
        events.pending_dir(root).mkdir(parents=True, exist_ok=True)
        (events.pending_dir(root) / "bad.json").write_text("{", encoding="utf-8")
        self.assertEqual(events.read_pending(root), [])

    def test_expire_stale_moves_old_events(self):
        root = self.tmpdir()
        events.record(root, report_of(BASE_ROWS), now=datetime(2026, 1, 1, 1, 0, tzinfo=TZ))
        events.record(root, report_of([row_of("m-01", "可能可开", 1)] + BASE_ROWS[1:]),
                      now=datetime(2026, 1, 1, 1, 5, tzinfo=TZ))
        moved = events.expire_stale(root, max_age_minutes=45,
                                    now=datetime(2026, 1, 1, 3, 0, tzinfo=TZ))
        self.assertEqual(len(moved), 1)
        self.assertEqual(list(events.pending_dir(root).glob("*.json")), [])
        self.assertTrue((events.expired_dir(root) / moved[0]).is_file())

    def test_runtime_layout_is_workspace_scoped(self):
        root = self.tmpdir()
        self.assertEqual(events.events_root(root), root / ".autoresearch" / "events")
        self.assertEqual(events.pending_dir(root), root / ".autoresearch" / "events" / "pending")
        self.assertEqual(events.delivered_dir(root), root / ".autoresearch" / "events" / "delivered")
        self.assertEqual(events.expired_dir(root), root / ".autoresearch" / "events" / "expired")
        self.assertEqual(events.targets_path(root),
                         root / ".autoresearch" / "events" / "targets.json")
        self.assertEqual(events.receipt_path(root),
                         root / ".autoresearch" / "events" / "delivered.jsonl")
        with self.assertRaises(ValueError):
            events.workspace_root("")

    def test_bound_sessions_reads_ro_and_sorts(self):
        tmp = self.tmpdir()
        db = tmp / "topics.sqlite"
        connection = sqlite3.connect(str(db))
        try:
            connection.execute("CREATE TABLE topics (id TEXT PRIMARY KEY, status TEXT, "
                               "leader_session_id TEXT, leader_since TEXT)")
            connection.executemany("INSERT INTO topics VALUES (?,?,?,?)", [
                ("t-old", "active", "session-b", "2026-01-01T01:00:00+08:00"),
                ("t-new", "active", "session-a", "2026-01-01T02:00:00+08:00"),
                ("t-dup", "active", "session-a", "2026-01-01T01:30:00+08:00"),
                ("t-none", "active", None, None),
                ("t-arch", "archived", "session-z", "2026-01-01T03:00:00+08:00"),
            ])
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(events.bound_sessions(db), ["session-a", "session-b"])
        self.assertEqual(events.bound_sessions(tmp / "missing.sqlite"), [])
        broken = tmp / "broken.sqlite"
        broken.write_text("not a db", encoding="utf-8")
        self.assertEqual(events.bound_sessions(broken), [])
        # 自定义表/列名：内核不假设调用方的 schema
        other = tmp / "other.db"
        connection = sqlite3.connect(str(other))
        try:
            connection.execute("CREATE TABLE leads (sid TEXT, at TEXT, state TEXT)")
            connection.execute("INSERT INTO leads VALUES ('s-1', '2026-01-01T00:00:00+08:00', 'open')")
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(
            events.bound_sessions(other, table="leads", session_column="sid",
                                  since_column="at", status_column="state",
                                  active_value="open"),
            ["s-1"])

    def test_write_and_read_targets(self):
        root = self.tmpdir()
        doc = events.write_targets(root, ["s-1", " s-2 ", ""], source="test")
        on_disk = json.loads(events.targets_path(root).read_text(encoding="utf-8"))
        self.assertEqual(doc["sessions"], ["s-1", "s-2"])
        self.assertTrue(on_disk["authoritative"])
        self.assertEqual(on_disk["source"], "test")
        self.assertEqual(events.read_targets(root)["sessions"], ["s-1", "s-2"])
        events.write_targets(root, [])
        self.assertEqual(events.read_targets(root)["sessions"], [])
        events.targets_path(root).write_text("{bad", encoding="utf-8")
        self.assertEqual(events.read_targets(root),
                         {"sessions": [], "authoritative": False, "source": None})

    def test_receipts_round_trip(self):
        root = self.tmpdir()
        self.assertTrue(events.append_receipt(root, {"kind": "apply", "pid": 1}))
        self.assertTrue(events.append_receipt(root, {"kind": "delivered", "machines": ["m-01"]}))
        receipts = events.read_receipts(root)
        self.assertEqual(len(receipts), 2)
        self.assertEqual(events.read_receipts(root, kind="delivered")[0]["machines"], ["m-01"])
        with events.receipt_path(root).open("a", encoding="utf-8") as handle:
            handle.write("not json\n")
        self.assertEqual(len(events.read_receipts(root)), 2)

    def test_events_selftest_passes(self):
        self.assertEqual(events.selftest(), 0)


# ── worker：单次、显式参数、不写越界状态 ─────────────────
class WorkerTests(TempDirMixin, AvailabilityStateMixin, unittest.TestCase):
    def worker_args(self, root: Path, **overrides):
        namespace = worker.build_parser().parse_args(
            ["--workspace", str(root), "--token-env", TOKEN_ENV])
        for key, value in overrides.items():
            setattr(namespace, key, value)
        return namespace

    def test_worker_dry_run_writes_nothing(self):
        root = self.tmpdir()
        with env_var(TOKEN_ENV, "token-value"):
            summary = worker.run_once(self.worker_args(root, dry_run=True),
                                      transport=fake_transport(),
                                      now=datetime(2026, 1, 1, tzinfo=TZ))
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["machines"], 6)
        self.assertFalse((root / ".autoresearch").exists())

    def test_worker_once_writes_queue_state_and_snapshot(self):
        root = self.tmpdir()
        with env_var(TOKEN_ENV, "token-value"):
            first = worker.run_once(self.worker_args(root), transport=fake_transport(),
                                    now=datetime(2026, 1, 1, 1, 0, tzinfo=TZ))
            self.assertTrue(first["ok"])
            self.assertTrue(first["baseline"])
            self.assertTrue(events.state_path(root).is_file())
            snapshot = root / ".autoresearch" / "availability.md"
            self.assertTrue(snapshot.is_file())
            text = snapshot.read_text(encoding="utf-8")
            self.assertIn("读取不等于开机", text)
            self.assertFalse(text.splitlines()[0].startswith("## "))   # 不抢二级标题

            flipped = {"code": "Success", "data": {"list": [
                {"machine_alias": f"m-0{i}", "status": "shutdown", "start_mode": "gpu",
                 "gpu_all_num": 8, "gpu_idle_num": 2, "uuid": f"u-{i}"}
                for i in range(1, 7)
            ]}}
            second = worker.run_once(self.worker_args(root),
                                     transport=fake_transport(flipped),
                                     now=datetime(2026, 1, 1, 1, 10, tzinfo=TZ))
        self.assertTrue(second["ok"])
        self.assertIn("m-02", second["emitted"])
        self.assertGreaterEqual(len(events.read_pending(root)), 1)

    def test_worker_publishes_targets_when_db_given(self):
        root = self.tmpdir()
        db = root / "topics.sqlite"
        connection = sqlite3.connect(str(db))
        try:
            connection.execute("CREATE TABLE topics (id TEXT PRIMARY KEY, status TEXT, "
                               "leader_session_id TEXT, leader_since TEXT)")
            connection.execute("INSERT INTO topics VALUES ('t1', 'active', 'session-a', "
                               "'2026-01-01T00:00:00+08:00')")
            connection.commit()
        finally:
            connection.close()
        with env_var(TOKEN_ENV, "token-value"):
            summary = worker.run_once(
                self.worker_args(root, targets_db=str(db), dry_run=False),
                transport=fake_transport(), now=datetime(2026, 1, 1, tzinfo=TZ))
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["targets"]["sessions"], ["session-a"])
        self.assertTrue(summary["targets"]["authoritative"])
        self.assertEqual(events.read_targets(root)["sessions"], ["session-a"])

    def test_worker_without_workspace_is_unknown(self):
        namespace = worker.build_parser().parse_args([])
        namespace.workspace = ""
        summary = worker.run_once(namespace, transport=fake_transport())
        self.assertFalse(summary["ok"])
        self.assertIn("workspace", summary["why"])

    def test_worker_failed_query_exits_unknown(self):
        root = self.tmpdir()
        with env_var(TOKEN_ENV, "token-value"):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = worker.main(["--workspace", str(root), "--json"],
                                   transport=fake_transport(raises=TimeoutError("timed out")),
                                   now=datetime(2026, 1, 1, tzinfo=TZ))
        self.assertEqual(code, 2)
        payload = json.loads(buffer.getvalue())
        self.assertFalse(payload["ok"])
        self.assertIn("超时", payload["why"])

    def test_worker_help_and_selftest(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with self.assertRaises(SystemExit) as raised:
                worker.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("--workspace", buffer.getvalue())
        self.assertEqual(worker.main(["--selftest"]), 0)


# ── worker：可用性 tick 的 process_status 投影与 targets-db 接线 ───────
CHINESE_TOPICS = "AutoDL实验/实验分析/topics"


class TickProjectionTests(TempDirMixin, AvailabilityStateMixin, unittest.TestCase):
    """每次 availability tick 都要：刷新已登记专题的 process_status、按绑定库写 targets。

    判据（⛔ 不看「命令退出 0」）：
    1. 两 tick 幂等：同一输入不重复发事件、targets 名单不变、研究页 `STATUS.md` 字节不变；
    2. 专题目录按 workspace-layout 解析（中文本地目录），流程状态只落 `process_status/`；
    3. 缺登记库 / 缺 `topics` 表 ⇒ 显式 unknown，⛔ 不写权威空名单、不假绿；
    4. 新会话绑定后 targets 立刻更新；没有绑定 ⇒ 权威空名单 + hold（事件不投递）。
    """

    def make_workspace(self, rows=(("T1", "active", None),), *, table=True):
        """临时工作区：中文布局标记 + runtime.sqlite3（可选缺表）+ 专题目录与研究页。"""
        root = self.tmpdir()
        (root / ".autoresearch").mkdir()
        write_layout_marker(root)
        layout = resolve_layout(root)
        db_path = root / ".autoresearch" / "runtime.sqlite3"
        connection = sqlite3.connect(str(db_path))
        try:
            if table:
                connection.execute("CREATE TABLE topics (id TEXT PRIMARY KEY, status TEXT, "
                                   "leader_session_id TEXT, leader_since TEXT)")
                connection.executemany(
                    "INSERT INTO topics VALUES (?,?,?,?)",
                    [(topic, status, leader, None) for topic, status, leader in rows])
                connection.commit()
        finally:
            connection.close()
        for topic, status, leader in rows:
            directory = layout.topic_dir(topic)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "STATUS.md").write_text(
                f"# {topic} 研究页\n\n## 1 你的要求\n只验证机制，不动机器。\n", encoding="utf-8")
        return root, layout, db_path

    def worker_args(self, root: Path, **overrides):
        namespace = worker.build_parser().parse_args(
            ["--workspace", str(root), "--token-env", TOKEN_ENV])
        for key, value in overrides.items():
            setattr(namespace, key, value)
        return namespace

    def tick(self, root: Path, when, transport=None, **overrides):
        with env_var(TOKEN_ENV, "token-value"):
            return worker.run_once(self.worker_args(root, **overrides),
                                   transport=transport or fake_transport(),
                                   now=datetime(2026, 1, 1, when[0], when[1], tzinfo=TZ))

    def all_shutdown(self):
        return {"code": "Success", "data": {"list": [
            {"machine_alias": f"m-0{i}", "status": "shutdown", "start_mode": "gpu",
             "gpu_all_num": 8, "gpu_idle_num": 2, "uuid": f"u-{i}"} for i in range(1, 7)]}}

    def set_leader(self, db_path: Path, topic: str, session: str, since: str):
        connection = sqlite3.connect(str(db_path))
        try:
            connection.execute("UPDATE topics SET leader_session_id=?, leader_since=? WHERE id=?",
                               (session, since, topic))
            connection.commit()
        finally:
            connection.close()

    def process_status_json(self, root: Path, topic: str = "T1"):
        path = root / ".autoresearch" / "state" / "process_status" / f"{topic}.json"
        return json.loads(path.read_text(encoding="utf-8")), path

    def test_two_ticks_are_idempotent_and_keep_research_page_bytes(self):
        root, layout, _db = self.make_workspace()
        page = layout.topic_dir("T1") / "STATUS.md"
        before = page.read_bytes()

        first = self.tick(root, (1, 0))
        second = self.tick(root, (1, 1))
        self.assertTrue(first["ok"] and second["ok"])
        self.assertTrue(first["baseline"])
        self.assertEqual(second["emitted"], [], "同一输入的第二 tick 不应重复发事件")
        self.assertEqual(len(events.read_pending(root)), 0)
        self.assertEqual(page.read_bytes(), before, "⛔ tick 不许改研究页任何字节")

        self.assertEqual(first["process_status"]["status"], "written")
        self.assertEqual(second["process_status"]["status"], "written")
        self.assertEqual(second["emitted"], [])
        projection, path = self.process_status_json(root)
        self.assertEqual(projection["verdict"], "状态待核")       # 没有 checkpoint ⇒ 不假绿
        self.assertEqual(projection["page"]["path"],
                         f"{CHINESE_TOPICS}/T1/STATUS.md", "流程投影要按布局指到中文专题页")
        self.assertTrue((root / ".autoresearch" / "state" / "process_status"
                         / "T1.md").is_file())
        # 第二次 tick 只是刷新心跳（projected_at 变）；判定与交付状态必须一致。
        self.assertEqual(projection["documentation"]["state"],
                         first["process_status"]["topics"]["T1"]["delivery"])
        self.assertEqual(first["targets_status"], "empty")
        self.assertTrue(first["events_hold"])
        self.assertEqual(first["targets"]["sessions"], [])
        self.assertTrue(first["targets"]["authoritative"])

    def test_flip_emits_once_then_holds_and_never_touches_status(self):
        root, layout, _db = self.make_workspace()
        page = layout.topic_dir("T1") / "STATUS.md"
        before = page.read_bytes()
        self.tick(root, (1, 0))
        flipped = self.tick(root, (1, 5), transport=fake_transport(self.all_shutdown()))
        self.assertTrue(flipped["emitted"], "从关机到有余卡应判出事件")
        pending = len(events.read_pending(root))
        repeated = self.tick(root, (1, 10), transport=fake_transport(self.all_shutdown()))
        self.assertEqual(repeated["emitted"], [], "同一状态重复 tick 不许重复发事件")
        self.assertEqual(len(events.read_pending(root)), pending)
        self.assertEqual(page.read_bytes(), before)
        self.assertEqual(repeated["process_status"]["status"], "written")

    def test_missing_topic_db_is_explicit_unknown_and_keeps_targets(self):
        root = self.tmpdir()
        (root / ".autoresearch").mkdir()
        write_layout_marker(root)
        stale = root / ".autoresearch" / "events" / "targets.json"
        stale.parent.mkdir(parents=True)
        stale.write_text(json.dumps({"sessions": ["session-old"], "authoritative": True}),
                         encoding="utf-8")
        summary = self.tick(root, (1, 0))
        self.assertTrue(summary["ok"], "可用性查询成功，不能被登记库缺失改成失败")
        self.assertEqual(summary["topics"]["status"], "unknown")
        self.assertIn("登记库不存在", summary["topics"]["why"])
        self.assertEqual(summary["targets_status"], "unknown")
        self.assertEqual(summary["targets"]["sessions"], ["session-old"], "unknown 不许清空旧名单")
        self.assertEqual(summary["process_status"]["status"], "unknown")
        self.assertIn("登记库不存在", summary["process_status"]["why"])
        self.assertFalse((root / ".autoresearch" / "state" / "process_status").exists(),
                         "没有登记表就不许写任何专题流程状态")
        self.assertEqual(json.loads(stale.read_text(encoding="utf-8"))["sessions"],
                         ["session-old"], "⛔ 不拿空名单覆盖已有 targets.json")

    def test_missing_topics_table_is_explicit_unknown(self):
        root, _layout, _db = self.make_workspace(table=False)
        summary = self.tick(root, (1, 0))
        self.assertEqual(summary["topics"]["status"], "unknown")
        self.assertIn("schema 缺 topics 表", summary["topics"]["why"])
        self.assertEqual(summary["targets_status"], "unknown")
        self.assertFalse(events.targets_path(root).exists())
        self.assertEqual(summary["process_status"]["status"], "unknown")

    def test_new_binding_updates_targets_and_unbound_holds(self):
        root, _layout, db_path = self.make_workspace()
        unbound = self.tick(root, (1, 0))
        self.assertTrue(unbound["events_hold"])
        self.assertEqual(unbound["targets"]["sessions"], [])
        self.assertTrue(unbound["targets"]["authoritative"])
        self.assertEqual(events.read_receipts(root), [], "无绑定时不许有投递回执")
        self.assertFalse(events.receipt_path(root).exists())
        self.set_leader(db_path, "T1", "session-new", "2026-01-01T01:04:00+08:00")
        bound = self.tick(root, (1, 5))
        self.assertEqual(bound["targets_status"], "published")
        self.assertFalse(bound["events_hold"])
        self.assertEqual(bound["targets"]["sessions"], ["session-new"])
        self.assertEqual(events.read_targets(root)["sessions"], ["session-new"])

    def test_registered_topic_without_directory_is_not_fake_green(self):
        root, _layout, _db = self.make_workspace(
            rows=(("T1", "active", None), ("T2", "active", None)))
        # 删掉 T2 的专题目录：登记表有、落点没有 ⇒ 该专题必须显式 unknown。
        shutil.rmtree(root / CHINESE_TOPICS / "T2")
        summary = self.tick(root, (1, 0))
        self.assertEqual(summary["process_status"]["count"], 2)
        self.assertEqual(summary["process_status"]["status"], "partial")
        self.assertFalse(summary["process_status"]["topics"]["T2"]["ok"])
        self.assertTrue(summary["process_status"]["topics"]["T2"]["unknown"])
        self.assertTrue(summary["process_status"]["topics"]["T1"]["ok"])
        self.assertFalse((root / ".autoresearch" / "state" / "process_status" / "T2.json").exists())


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell"),
                     "schtasks.ps1 是 Windows PowerShell 脚本")
class SchtasksDefinitionTests(TempDirMixin, unittest.TestCase):
    """计划任务定义只生成不注册，且命令里带实际工作区与 targets-db。"""

    SCRIPT = REPO_ROOT / "kernel" / "hosts" / "dsh" / "tools" / "schtasks.ps1"

    def generate(self, workspace: Path, out: Path) -> str:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.SCRIPT),
             "-Action", "generate", "-Workspace", str(workspace), "-OutFile", str(out),
             "-PythonPath", sys.executable],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stdout)
        return out.read_text(encoding="utf-8-sig")

    def test_generated_definition_carries_actual_workspace_and_targets_db(self):
        workspace = self.tmpdir()
        (workspace / ".autoresearch").mkdir()
        write_layout_marker(workspace)
        out = workspace / "task-definition.txt"
        text = self.generate(workspace, out)
        self.assertIn(str(workspace.resolve()), text)
        self.assertIn("--workspace", text)
        self.assertIn("--targets-db", text)
        self.assertIn(str((workspace / ".autoresearch" / "runtime.sqlite3").resolve()), text)
        self.assertIn("process_status", text)
        self.assertIn("不写 STATUS.md", text)
        self.assertIn("workspace-layout.json", text)
        # 生成文件里只有命令文本；本测试没有执行 install（默认动作是 preview）。
        self.assertIn("schtasks.exe /Create", text)
        self.assertNotIn("已注册", text)


# ── cloud：默认拒绝；离线只验请求形态 ────────────────────
class CloudTests(unittest.TestCase):
    def test_cloud_refuses_without_explicit_allow(self):
        with self.assertRaises(PermissionError):
            cloud.power_action("u-1", "power_on", api_url="http://127.0.0.1:1/x")

    def test_cloud_rejects_unknown_action_even_when_allowed(self):
        with self.assertRaises(ValueError):
            cloud.power_action("u-1", "destroy", api_url="http://127.0.0.1:1/x",
                               allow_control=True)

    def test_cloud_composes_request_with_fake_transport(self):
        calls = []
        with env_var(TOKEN_ENV, "token-value"):
            result = cloud.power_action("u-1", "power_on", api_url="http://127.0.0.1:1/x",
                                        allow_control=True, token_env=TOKEN_ENV,
                                        transport=fake_transport({"code": "Success"}, calls=calls))
        self.assertTrue(result["ok"])
        self.assertFalse(result["status_confirmed"], "⛔ 请求成功不等于机器已经开起来")
        self.assertTrue(calls)
        self.assertEqual(json.loads(calls[0]["body"].decode("utf-8"))["action"], "power_on")
        self.assertNotIn("token-value", json.dumps(result))

    def test_cloud_reports_api_rejection(self):
        transport = fake_transport({"code": "WrongStartMode", "msg": "无卡实例最多 2 台"})
        with env_var(TOKEN_ENV, "token-value"):
            result = cloud.power_action("u-1", "power_on", api_url="http://127.0.0.1:1/x",
                                        allow_control=True, token_env=TOKEN_ENV,
                                        transport=transport)
        self.assertFalse(result["ok"])
        self.assertIn("WrongStartMode", result["why"])
        self.assertFalse(result["status_confirmed"])

    def test_cloud_without_credentials_does_not_send(self):
        calls = []
        with without_env(TOKEN_ENV):
            result = cloud.power_action("u-1", "power_on", api_url="http://127.0.0.1:1/x",
                                        allow_control=True, token_env="AUTORESEARCH_NO_SUCH_VAR",
                                        transport=fake_transport(calls=calls))
        self.assertFalse(result["ok"])
        self.assertEqual(calls, [], "没有凭据就不该发请求")


if __name__ == "__main__":
    unittest.main(verbosity=2)
