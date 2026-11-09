# GENERATED-BY: research lifecycle projection migration, 2026-11-09
# LAST-EDIT: 2026-11-09，投影复盘v2的下一步判据、费用、负责人及审批需要；保留旧事件字节
# PURPOSE: 研究执行（workflow）与人读交付（reporting）之间的接线层：
#          只读 wf_preparations/wf_runs/wf_finishes/wf_reviews + gov_plans，投影出
#          topics/<topic>/plans/<plan>/{plan.json,AI_WORK.md,receipts/*.json}，
#          再把 run/round 终态与复盘结论写进 reporting（deterministic id + 真实收据来源），
#          并提供 before/after 主文档快照（原始字节 + sha，只生成一次）。
# ORIGIN: 上游 ai_work.py 的「AI 工作材料是投影」与报告交付纪律；只保留机制，无比赛/机器/读数。
# CUMULATIVE-DELTA: 2026-11-09 复盘v2交接投影到AI_WORK与leader_conclusion；旧输入事件不重写。
#   首版不启进程、不核宿主、不判科学；缺 before 不补造；hook 失败只记 diagnostic。
#   2026-11-09 只读零副作用：`_read` 不再拼裸 `?mode=ro`，委托 `ReadonlyStore`（WAL 库按 sidecar 实况选
#   mode=ro / immutable=1，读不创建 -shm/-wal）；缺库仍是原来的 LifecycleError，WAL 歧义态 StoreError 交上层。
#   2026-11-09 专题落点由 `.autoresearch/workspace-layout.json` 决定（无标记仍是 topics/<topic>）；
#   专题内投影前缀仍是 `plans/`（相对专题目录），与布局里的框架级 directories.plans 不是同一个目录。

"""研究执行 ↔ 人读交付的接线层。

这一层解决的问题：**workflow 的 append-only 记录是真的，reporting 的页面规则也是真的，
但两边没有接线** —— 于是「跑完了」只能靠人手工再写一遍事件与结论。

本模块只做三件事：

1. **投影**（`sync`）：只读权威表 `wf_preparations/wf_runs/wf_finishes/wf_reviews` 与
   `gov_plans`，在专题目录生成
   `plans/<plan_id>/plan.json`（冻结计划）、`plans/<plan_id>/AI_WORK.md`（严格机器投影）、
   `plans/<plan_id>/receipts/{run,finish,review}-<run_id>.json`（只追加收据）。
2. **事件与结论**（仍是 `sync`）：把每个已结束 run 的 run / round 终态写进
   `reporting.record_event`，把真的有 `wf_reviews` 记录的复盘写进
   `reporting.record_input(leader_conclusion)`。事件 id 用 `run_id` 派生，
   `source` 指向刚生成的收据 JSON + locator（⛔ 不靠口头完成）。
3. **主文档快照**（`before` / `after`）：以**原始字节**保存 `TOPIC.md` 并绑 sha；
   `before` 必须在启动前、`after` 必须在 review 之后；只生成一次，内容不符就拒；
   ⛔ 绝不用当前 TOPIC 补造已启动历史的 before。

三条硬边界
==========

1. **只读权威表、只写投影与收据。** `sync` 不改 `wf_*` / `gov_plans`；DB 用只读 URI + `query_only`。
2. **不核 scribe、不判科学。** Lifecycle 只能产出 `declared` 回执所需的供料与待更新状态；
   真实 `verified` 只能由 `reporting` 的宿主验证器产生。缺真实 scribe 时，`reporting.check`
   仍然是「待更新」，⛔ 不能算 done。
3. **失败分级。** 投影/收据/快照的不一致一律抛出（fail closed，不覆盖）；
   而 `post()`（event_sink 适配）**永不抛**：hook 失败只追加一条 diagnostic，
   不覆盖调用方退出码，也不阻断安全停。

与 reporting 的接口（稳定）

    Lifecycle(workspace, db=<同一 runtime.sqlite3>)
    .sync(topic, plan_id=None) -> dict
    .before(preparation) -> dict          # preparation: dict（workflow.prepare 的返回）或 prepared_id
    .after(review) -> dict                # review: dict（workflow.review 的返回）或 run_id
    .post(kind, payload) -> dict          # event_sink 适配：result_analysis 触发 after+sync
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from kernel.autoresearch import reporting  # noqa: E402  (复用页面/事件/来源指纹的规则)
from kernel.autoresearch.runtime.store import ReadonlyStore  # noqa: E402
from kernel.autoresearch.review_continuation import render as render_continuation  # noqa: E402
from kernel.autoresearch.workspace_layout import resolve_layout  # noqa: E402

SCHEMA = 1

#: 权威表（只读）。
WF_TABLES = ("wf_preparations", "wf_runs", "wf_finishes", "wf_reviews", "wf_events", "gov_plans")

#: `wf_finishes.reason` → `reporting` 的事件种类。⛔ 不新增种类，也不把未知原因猜成成功。
TERMINAL_KIND = {
    "completed": "completed",
    "failed": "failed",
    "launch_error": "failed",
    "timeout": "failed",
    "interrupted": "stopped",
    "stopped_by_user": "stopped",
    "superseded": "superseded",
}

#: 允许写进 `sources.json` 的生成物前缀（**专题内**计划投影目录，相对专题目录）。
#: ⚠️ 与布局标记里的 `directories.plans`（框架级 `AutoDL实验/框架/state/plans`）不是同一个目录；
#: 保持 `plans/` 前缀是为了旧工作区的 sources.json 条目逐字节兼容。
PROJECTION_PREFIX = "plans/"

#: ⛔ 这些是人读输出，不能进来源清单（否则 FEED/RECEIPT 会自证循环）。
FORBIDDEN_SOURCES = ("STATUS.md", "FEED.md", "RECEIPT.json", "SOURCES.json")


class LifecycleError(ValueError):
    """接线层拒绝：投影不一致、快照缺失/不符、权威记录不完整。"""

    def __init__(self, message, problems=None):
        super().__init__(message)
        self.problems = list(problems or [])


# ---------------------------------------------------------------------------
# 小工具：时间、哈希、读写
# ---------------------------------------------------------------------------


def _iso(timestamp):
    """固定 UTC 字符串（与 workflow 的 `_iso` 同形），让投影字节可重算。"""
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(timestamp)))
    except (TypeError, ValueError, OSError):
        return None


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _pretty(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _rel(root, path):
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()


def _ensure_topic(topic):
    if not isinstance(topic, str) or not topic.strip():
        raise LifecycleError("topic 必填")
    reporting.require_topic(topic)
    return topic


def _write_once(path, data, *, label):
    """写一次：不存在就写；存在且逐字节相同 ⇒ 幂等；不同 ⇒ 拒绝覆盖。"""
    path = Path(path)
    if path.exists():
        existing = path.read_bytes()
        if existing == data:
            return "identical"
        raise LifecycleError(
            f"{label} 已存在且内容不同：{path.name}（同名源内容不符，拒绝覆盖；"
            "投影只能由权威表重算，⛔ 不许手改）")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:      # 独占创建：并发也不覆盖
            stream.write(data)
    except FileExistsError:
        return _write_once(path, data, label=label)
    return "written"


def _write_projection(path, data, *, label):
    """投影文件可重算：内容相同就不动；不同则原子替换（它是由权威表派生的，不是台账）。"""
    path = Path(path)
    if path.exists() and path.read_bytes() == data:
        return "identical"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    temp.write_bytes(data)
    os.replace(temp, path)
    return "written"


def _append_jsonl(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(_canonical(row) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _load_json(path, default):
    path = Path(path)
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise LifecycleError(f"{path.name} 不是合法 JSON：{exc}") from None


# ---------------------------------------------------------------------------
# 只读权威表
# ---------------------------------------------------------------------------


class Lifecycle:
    """一次接线：workspace 与其 runtime.sqlite3（与 workflow / governance 同一个库）。"""

    def __init__(self, workspace, *, db=None, layout=None):
        if not isinstance(workspace, (str, Path)):
            raise LifecycleError("workspace 必须是路径")
        self.root = Path(workspace).resolve()
        if not self.root.is_dir():
            raise LifecycleError(f"工作区不存在：{self.root}")
        self.db = Path(db).resolve() if db is not None else (
            self.root / ".autoresearch" / "runtime.sqlite3")
        self.log_path = self.root / ".autoresearch" / "lifecycle" / "hooks.jsonl"
        #: 专题落点只由 `.autoresearch/workspace-layout.json` 决定（无标记 = topics/<topic>）。
        try:
            self.layout = layout if layout is not None else resolve_layout(self.root)
        except ValueError as exc:
            raise LifecycleError(f"工作区布局不可用：{exc}") from None

    # -- 路径 ---------------------------------------------------------------

    def topic_dir(self, topic):
        _ensure_topic(topic)
        try:
            path = self.layout.topic_dir(topic)
        except ValueError as exc:
            raise LifecycleError(f"专题 {topic} 的落点不可用：{exc}") from None
        if not path.is_dir():
            raise LifecycleError(
                f"专题目录不存在：{self.layout.topics}/{topic}"
                "（布局由 .autoresearch/workspace-layout.json 决定；无标记工作区是 topics/<topic>）")
        return path

    def plan_dir(self, topic, plan_id):
        return self.topic_dir(topic) / "plans" / str(plan_id)

    def plan_projection(self, topic, plan_id):
        return self.plan_dir(topic, plan_id) / "plan.json"

    def ai_work(self, topic, plan_id):
        return self.plan_dir(topic, plan_id) / "AI_WORK.md"

    def receipts_dir(self, topic, plan_id):
        return self.plan_dir(topic, plan_id) / "receipts"

    def receipt(self, topic, plan_id, kind, run_id):
        if kind not in ("run", "finish", "review"):
            raise LifecycleError(f"未知收据类型：{kind}")
        return self.receipts_dir(topic, plan_id) / f"{kind}-{run_id}.json"

    def snapshots_dir(self, topic, plan_id):
        # A source change may require another preparation before the first start.
        # Preserve each preparation instead of overwriting its immutable before receipt.
        with self._read() as conn:
            rows = self._rows(conn, 'SELECT prepared_id FROM wf_preparations WHERE topic=? AND plan_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (topic, plan_id))
        if not rows:
            raise LifecycleError('没有可绑定的准备版本，不能创建快照')
        return self.topic_dir(topic) / 'snapshots' / str(plan_id) / rows[0]['prepared_id']

    def snapshot(self, topic, plan_id, stage):
        if stage not in ("before", "after"):
            raise LifecycleError("快照阶段只能是 before / after")
        return self.snapshots_dir(topic, plan_id) / f"{stage}.md"

    def snapshot_receipt(self, topic, plan_id, stage):
        return self.snapshots_dir(topic, plan_id) / f"{stage}.json"

    # -- 只读连接 -----------------------------------------------------------

    @contextmanager
    def _read(self):
        if not self.db.is_file():
            raise LifecycleError(
                f"runtime 库不存在：{_rel(self.root, self.db) if self.db.is_relative_to(self.root) else self.db}"
                "（先 workflow prepare；本模块不建库）")
        # 连接方式委托 runtime.store.ReadonlyStore：WAL 库按 sidecar 实况选
        # mode=ro / immutable=1，读不创建 -shm / -wal；WAL 歧义态 StoreError 交给上层。
        connection = ReadonlyStore(self.db).connect()
        try:
            connection.execute("BEGIN")
            yield connection
        finally:
            connection.rollback()
            connection.close()

    @staticmethod
    def _rows(connection, sql, args=()):
        try:
            return [dict(row) for row in connection.execute(sql, args)]
        except sqlite3.Error as exc:
            raise LifecycleError(f"权威表不可读：{exc}") from None

    # -- 投影 ---------------------------------------------------------------

    def _plan_rows(self, topic, plan_id=None):
        with self._read() as conn:
            if plan_id is not None:
                plans = [plan_id]
            else:
                rows = self._rows(
                    conn,
                    "SELECT plan_id, MIN(created_at) AS first_at FROM wf_preparations WHERE topic=?"
                    " GROUP BY plan_id UNION "
                    "SELECT plan_id, MIN(started_at) AS first_at FROM wf_runs WHERE topic=?"
                    " GROUP BY plan_id", (topic, topic))
                ordered = {}
                for row in rows:
                    stamp = row.get("first_at") or 0.0
                    ordered[row["plan_id"]] = min(stamp, ordered.get(row["plan_id"], stamp))
                plans = [item for item, _ in sorted(ordered.items(), key=lambda pair: (pair[1], pair[0]))]
            if not plans:
                raise LifecycleError(f"专题 {topic} 还没有任何 prepare/run 记录：先 workflow prepare")
            data = {}
            for item in plans:
                prepared = self._rows(
                    conn,
                    "SELECT * FROM wf_preparations WHERE topic=? AND plan_id=? ORDER BY created_at",
                    (topic, item))
                runs = self._rows(
                    conn,
                    "SELECT * FROM wf_runs WHERE topic=? AND plan_id=? ORDER BY started_at",
                    (topic, item))
                finishes = {}
                reviews = {}
                for run in runs:
                    row = self._rows(conn, "SELECT * FROM wf_finishes WHERE run_id=?", (run["run_id"],))
                    if row:
                        finishes[run["run_id"]] = row[0]
                    row = self._rows(conn, "SELECT * FROM wf_reviews WHERE run_id=?", (run["run_id"],))
                    if row:
                        reviews[run["run_id"]] = row[0]
                frozen = self._rows(
                    conn,
                    "SELECT * FROM gov_plans WHERE plan_id=? ORDER BY version DESC LIMIT 1", (item,))
                data[item] = {"plan_id": item, "preparations": prepared, "runs": runs,
                              "finishes": finishes, "reviews": reviews,
                              "frozen": frozen[0] if frozen else None}
            return plans, data

    def _write_plan_projection(self, topic, plan_id, frozen):
        if frozen is None:
            raise LifecycleError(f"计划 {plan_id} 还没有冻结版本（gov_plans 为空）：先 workflow prepare")
        try:
            plan = json.loads(frozen["plan_json"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LifecycleError(f"gov_plans.plan_json 读不出：{exc}") from None
        data = _pretty(plan).encode('utf-8')
        version = frozen['version']
        immutable = self.plan_dir(topic, plan_id) / f'plan-v{version}.json'
        _write_once(immutable, data, label=f'计划版本 {plan_id} v{version}')
        target = self.plan_projection(topic, plan_id)
        state = _write_projection(target, data, label=f'计划投影 {plan_id}')
        return target, state

    def _receipt_payloads(self, topic, plan_id, record):
        """收据内容只来自权威行（不含当前时间），保证重跑逐字节一致。"""
        payloads = []
        for run in record["runs"]:
            run_id = run["run_id"]
            run_body = {
                "schema": SCHEMA, "kind": "run", "run_id": run_id, "plan_id": plan_id,
                "topic": topic, "prepared_id": run["prepared_id"],
                "plan_version": run["plan_version"], "owner_session": run["owner_session"],
                "started_at": _iso(run["started_at"]), "argv": json.loads(run["argv_json"]),
                "cwd": run["cwd"], "source_fingerprint": run["source_fingerprint"],
                "plan_sha256": run["plan_sha256"], "gates": json.loads(run["gates_json"]),
            }
            payloads.append(("run", run_id, run_body))
            finish = record["finishes"].get(run_id)
            if finish is not None:
                payloads.append(("finish", run_id, {
                    "schema": SCHEMA, "kind": "finish", "run_id": run_id, "plan_id": plan_id,
                    "topic": topic, "reason": finish["reason"], "exitcode": finish["exitcode"],
                    "signal": finish["signal"], "finished_at": _iso(finish["finished_at"]),
                    "documentation": {"status": finish["documentation_status"],
                                      "detail": finish["documentation_detail"]},
                }))
            review = record["reviews"].get(run_id)
            if review is not None:
                payloads.append(("review", run_id, {
                    "schema": SCHEMA, "kind": "review", "run_id": run_id, "plan_id": plan_id,
                    "topic": topic, "verdict": review["verdict"], "branch": review["branch"],
                    "next_action": review["next_action"], "record_sha256": review["record_sha256"],
                    "evidence": json.loads(review["evidence_json"]),
                    "record": json.loads(review["record_json"]),
                    "created_at": _iso(review["created_at"]),
                }))
        return payloads

    def _write_receipts(self, topic, plan_id, payloads):
        written, paths = [], []
        for kind, run_id, body in payloads:
            target = self.receipt(topic, plan_id, kind, run_id)
            state = _write_once(target, (_canonical(body) + "\n").encode("utf-8"),
                                label=f"{kind} 收据 {run_id}")
            paths.append(target)
            if state == "written":
                written.append(_rel(self.root, target))
        return written, paths

    def _ai_work_text(self, topic, plan_id, record, diagnostics):
        """严格机器投影：每个数字/判词都能回指 wf_* 行；⛔ 不含手工台账字段。"""
        lines = [
            f"# AI 工作材料 · {topic} · {plan_id}",
            "",
            "> 由 `lifecycle.sync()` 从权威表投影生成，⛔ **不是手工台账**：改这里没有用，改 `wf_*` 记录。",
            "> 生成源：`wf_preparations` / `wf_runs` / `wf_finishes` / `wf_reviews` / `gov_plans`"
            "（同一 `runtime.sqlite3`，只读）。",
            "",
            "## 1 事前材料（冻结与准备）",
            "",
        ]
        frozen = record["frozen"] or {}
        if frozen:
            lines += [f"- 冻结版本：v{frozen.get('version')} · key_fingerprint `{frozen.get('key_fingerprint')}`",
                      f"- plan_sha256：`{frozen.get('plan_sha256') or '<未记录>'}`"]
        else:
            lines.append("- 冻结版本：⛔ 缺（gov_plans 没有该 plan）")
        if not record["preparations"]:
            lines.append("- 准备记录：⛔ 缺（没有 wf_preparations 行）")
        for prep in record["preparations"]:
            lines += [
                "",
                f"### prepared_id `{prep['prepared_id']}`",
                f"- 时间：{_iso(prep['created_at'])} · 负责人：`{prep['owner_session']}` · actor：`{prep['actor']}`",
                f"- 来源指纹：`{prep['source_fingerprint']}`",
                f"- 声明 argv：`{prep['argv_json']}`",
                f"- 实现声明：`{prep['implementation_json']}`",
            ]
            try:
                snapshot = json.loads(prep["snapshot_json"])
            except (TypeError, ValueError):
                lines.append("- 快照：⛔ 读不出")
                continue
            sources = snapshot.get("sources") or {}
            lines.append(f"- 来源文件（{len(sources)} 个）：")
            for rel in sorted(sources):
                lines.append(f"  - `{rel}` `{sources[rel]}`")
        lines += ["", "## 2 运行、结束与复盘", ""]
        if not record["runs"]:
            lines.append("- 还没有运行记录。")
        for run in record["runs"]:
            run_id = run["run_id"]
            lines += [
                f"### run `{run_id}`",
                f"- 启动：{_iso(run['started_at'])} · prepared_id `{run['prepared_id']}`",
                f"- argv：`{run['argv_json']}` · cwd：`{run['cwd']}`",
                f"- 来源指纹：`{run['source_fingerprint']}` · plan_sha256：`{run['plan_sha256']}`",
            ]
            finish = record["finishes"].get(run_id)
            if finish is None:
                lines.append("- 结束：⛔ 未记录（运行结束不等于报告交付）")
            else:
                lines += [f"- 结束原因：`{finish['reason']}` · exitcode：`{finish['exitcode']}`"
                          f" · signal：`{finish['signal']}`",
                          f"- 文档状态：`{finish['documentation_status']}` — {finish['documentation_detail']}"]
            review = record["reviews"].get(run_id)
            if review is None:
                lines.append("- 复盘：⛔ 未复盘（下一轮会被拦）")
            else:
                lines += [f"- 复盘：verdict `{review['verdict']}` · branch `{review['branch']}`",
                          f"- 下一步：{review['next_action']}",
                          f"- 复盘记录 sha256：`{review['record_sha256']}`",
                          f"- 复盘证据：`{review['evidence_json']}`"]
                lines.extend('- ' + part for part in render_continuation(json.loads(review['record_json'])))
            lines.append(f"- 收据：`{_rel(self.root, self.receipt(topic, plan_id, 'run', run_id))}`"
                         + (f" · `{_rel(self.root, self.receipt(topic, plan_id, 'finish', run_id))}`"
                            if finish is not None else "")
                         + (f" · `{_rel(self.root, self.receipt(topic, plan_id, 'review', run_id))}`"
                            if review is not None else ""))
        lines += ["", "## 3 诊断（缺什么）", ""]
        if not diagnostics:
            lines.append("- 无。")
        for item in diagnostics:
            lines.append(f"- [{item['level']}] {item['code']}：{item['detail']}")
        return "\n".join(lines) + "\n"

    def _update_sources_manifest(self, topic, generated):
        """把投影收据/AI_WORK/plan 加进 `topics/<topic>/sources.json` 固定来源表。

        ⛔ 不把 STATUS.md / FEED.md / RECEIPT.json 这类**人读输出**加进去（会造成自证循环）。
        """
        path = self.topic_dir(topic) / "sources.json"
        existing = _load_json(path, [])
        if not isinstance(existing, list) or not all(isinstance(item, str) for item in existing):
            raise LifecycleError(
                f"{self.layout.topics}/{topic}/sources.json 必须是 JSON 字符串路径列表")
        for item in existing + list(generated):
            name = Path(item).name
            if name in FORBIDDEN_SOURCES or "/.autoresearch/" in item or item.startswith(".autoresearch/"):
                raise LifecycleError(
                    f"来源清单不能包含人读输出或状态目录：{item}（FEED/RECEIPT/STATUS 自证循环）")
        declared = sorted({item for item in existing if item.strip()} | {item for item in generated})
        if declared == sorted({item for item in existing if item.strip()}):
            return None
        path.write_text(_pretty(declared), encoding="utf-8", newline="\n")
        return path

    # -- 对外：sync ---------------------------------------------------------

    def sync(self, topic, *, plan_id=None):
        """把权威记录投影成人读/AI 材料与 reporting 事件（幂等；只读权威表）。"""
        topic = _ensure_topic(topic)
        plans, data = self._plan_rows(topic, plan_id)
        written, diagnostics, summaries = [], [], []
        generated = []
        for item in plans:
            record = data[item]
            plan_diagnostics = []
            plan_path, plan_state = self._write_plan_projection(topic, item, record["frozen"])
            generated.append(_rel(self.root, plan_path))
            if plan_state == "written":
                written.append(_rel(self.root, plan_path))
            payloads = self._receipt_payloads(topic, item, record)
            receipt_written, receipt_paths = self._write_receipts(topic, item, payloads)
            written.extend(receipt_written)
            generated.extend(_rel(self.root, path) for path in receipt_paths)
            # 诊断：缺 ending / 缺 review / 缺 before 快照（历史不补造）
            for run in record["runs"]:
                run_id = run["run_id"]
                if run_id not in record["finishes"]:
                    plan_diagnostics.append({"level": "info", "code": "run_unfinished",
                                             "detail": f"{run_id} 还没有结束记录（wf_finishes 缺行）"})
                    continue
                if run_id not in record["reviews"]:
                    plan_diagnostics.append({"level": "info", "code": "review_missing",
                                             "detail": f"{run_id} 已结束但未复盘：下一轮会被 workflow 拦"})
                before_receipt = self.snapshot_receipt(topic, item, "before")
                if not before_receipt.is_file() or not self.snapshot(topic, item, 'before').is_file():
                    plan_diagnostics.append({
                        "level": "blocker", "code": "before_missing",
                        "detail": f"{run_id} 缺 before 主文档快照：历史运行不接受事后补造，"
                                  "只能在新轮次启动前调用 before()"})
                elif run_id in record["reviews"] and not self.snapshot_receipt(topic, item, "after").is_file():
                    plan_diagnostics.append({"level": "info", "code": "after_missing",
                                             "detail": f"{run_id} 已复盘但 after 快照未生成："
                                                       "在 review 成功后调用 after()（本模块不自动补）"})
            diagnostics.extend(plan_diagnostics)
            ai_text = self._ai_work_text(topic, item, record, plan_diagnostics)
            ai_path = self.ai_work(topic, item)
            ai_state = _write_projection(ai_path, ai_text.encode("utf-8"),
                                         label=f"AI_WORK {item}")
            generated.append(_rel(self.root, ai_path))
            if ai_state == "written":
                written.append(_rel(self.root, ai_path))
            summary = {"plan_id": item, "plan": _rel(self.root, plan_path),
                       "ai_work": _rel(self.root, ai_path), "receipts": [], "reporting": {}}
            summary["receipts"] = [_rel(self.root, path) for path in receipt_paths]
            summary["reporting"] = self._project_reporting(topic, item, record)
            summaries.append(summary)
        manifest = self._update_sources_manifest(topic, sorted(set(generated)))
        if manifest is not None:
            written.append(_rel(self.root, manifest))
        return {"ok": True, "schema": SCHEMA, "topic": topic, "plans": summaries,
                "diagnostics": diagnostics, "written": sorted(set(written)),
                "sources_manifest": _rel(self.root, self.topic_dir(topic) / "sources.json")}

    def _project_reporting(self, topic, plan_id, record):
        """把终结 run 与真实 review 投影成 reporting 事件/结论（id 由 run_id / record_sha 派生）。"""
        out = {"events": [], "inputs": []}
        for run in record["runs"]:
            run_id = run["run_id"]
            start_id = f'wf-start-{run_id}'
            start_source = {'path': _rel(self.root, self.receipt(topic, plan_id, 'run', run_id)),
                            'locator': f'{run_id}:start'}
            started = reporting.record_event(self.root, topic, plan_id, 'running', start_id,
                start_source, scope='run', reason=f'实际启动 {run_id}；结果尚未产生')
            out['events'].append({'scope': 'run', 'event_id': start_id,
                                  'idempotent': bool(started.get('idempotent'))})
            finish = record["finishes"].get(run_id)
            if finish is None:
                continue
            kind = TERMINAL_KIND.get(finish["reason"])
            if kind is None:
                raise LifecycleError(f"{run_id} 的结束原因 {finish['reason']} 没有对应事件种类")
            finish_rel = _rel(self.root, self.receipt(topic, plan_id, "finish", run_id))
            reason_text = (f"run {run_id} 结束：{finish['reason']}（exitcode={finish['exitcode']}；"
                           f"documentation={finish['documentation_status']}）")
            source = {"path": finish_rel, "locator": f"{run_id}:finish"}
            for scope, event_id in (("run", f"wf-run-{run_id}"), ("round", f"wf-round-{run_id}")):
                result = reporting.record_event(
                    self.root, topic, plan_id, kind, event_id, source, scope=scope,
                    reason=reason_text, exit_code=finish["exitcode"])
                out["events"].append({"scope": scope, "event_id": event_id,
                                      "idempotent": bool(result.get("idempotent"))})
            review = record["reviews"].get(run_id)
            if review is None:
                continue
            review_rel = _rel(self.root, self.receipt(topic, plan_id, "review", run_id))
            try:
                body = json.loads(review["record_json"])
            except (TypeError, ValueError):
                raise LifecycleError(f"{run_id} 的 review.record_json 读不出") from None
            text = "；".join(part for part in (
                f"verdict={review['verdict']}",
                f"branch={review['branch']}",
                f"next_action={review['next_action']}",
                f"result_summary={body.get('result_summary')}",
            ) if part)
            if body.get('schema_version') == 2:
                text += '；' + '；'.join(render_continuation(body))
            result = reporting.record_input(
                self.root, topic, "leader_conclusion", text,
                {"path": review_rel, "locator": f"{run_id}:review"},
                exp=plan_id, input_id=f"wf-review-{run_id}-{review['record_sha256'][:16]}")
            out["inputs"].append({"run_id": run_id, "input_id": result["input"]["input_id"],
                                  "idempotent": bool(result.get("idempotent"))})
        return out

    # -- 对外：before / after ----------------------------------------------

    def _resolve_preparation(self, preparation):
        if isinstance(preparation, dict):
            prepared_id = preparation.get("prepared_id")
            topic = preparation.get("topic")
            plan_id = preparation.get("plan_id")
        else:
            prepared_id, topic, plan_id = str(preparation or ""), None, None
        if not prepared_id:
            raise LifecycleError("before 需要 preparation（dict）或 prepared_id")
        with self._read() as conn:
            rows = self._rows(conn, "SELECT * FROM wf_preparations WHERE prepared_id=?", (prepared_id,))
            if not rows:
                raise LifecycleError(f"没有这个准备记录：{prepared_id}")
            row = rows[0]
            if topic is not None and topic != row["topic"]:
                raise LifecycleError(f"preparation.topic={topic!r} 与记录 {row['topic']!r} 不一致")
            if plan_id is not None and plan_id != row["plan_id"]:
                raise LifecycleError(f"preparation.plan_id={plan_id!r} 与记录 {row['plan_id']!r} 不一致")
            runs = self._rows(conn, "SELECT run_id FROM wf_runs WHERE prepared_id=?", (prepared_id,))
        return row, runs

    def before(self, preparation):
        """启动前的主文档快照（原始字节 + sha）。⛔ 已启动的历史不能补造。"""
        row, runs = self._resolve_preparation(preparation)
        if runs:
            raise LifecycleError(
                f"{row['plan_id']} 已经启动过（run {runs[0]['run_id']}）："
                "before 只能在启动前生成，⛔ 不接受事后补造历史快照")
        topic, plan_id = row["topic"], row["plan_id"]
        document = self.topic_dir(topic) / "TOPIC.md"
        if not document.is_file():
            raise LifecycleError(f"缺专题主文档：{self.layout.topics}/{topic}/TOPIC.md")
        raw = document.read_bytes()
        digest = _digest(raw)
        receipt = {
            "schema": SCHEMA, "stage": "before", "topic": topic, "plan_id": plan_id,
            "prepared_id": row["prepared_id"], "plan_version": row["plan_version"],
            "plan_sha256": row["plan_sha256"], "source_fingerprint": row["source_fingerprint"],
            "source": _rel(self.root, document), "source_sha256": digest,
            "sha256": digest, "bytes": len(raw), "at": _iso(row["created_at"]),
        }
        target = self.snapshot(topic, plan_id, "before")
        state = _write_once(target, raw, label=f"before 快照 {plan_id}")
        receipt_path = self.snapshot_receipt(topic, plan_id, "before")
        _write_once(receipt_path, _pretty(receipt).encode("utf-8"), label=f"before 收据 {plan_id}")
        return {"ok": True, "stage": "before", "state": state, "path": _rel(self.root, target),
                "receipt": _rel(self.root, receipt_path), **receipt}

    def after(self, review):
        """review 成功后的主文档快照。⛔ 缺 before 时拒绝（不许用当前 TOPIC 补造）。"""
        if isinstance(review, dict):
            run_id = review.get("run_id")
        else:
            run_id = str(review or "")
        if not run_id:
            raise LifecycleError("after 需要 review（dict）或 run_id")
        with self._read() as conn:
            runs = self._rows(conn, "SELECT * FROM wf_runs WHERE run_id=?", (run_id,))
            if not runs:
                raise LifecycleError(f"没有这个运行记录：{run_id}")
            run = runs[0]
            finishes = self._rows(conn, "SELECT * FROM wf_finishes WHERE run_id=?", (run_id,))
            reviews = self._rows(conn, "SELECT * FROM wf_reviews WHERE run_id=?", (run_id,))
        if not finishes:
            raise LifecycleError(f"{run_id} 还没有结束记录：先 finish（运行结束不等于报告交付）")
        if not reviews:
            raise LifecycleError(f"{run_id} 还没有复盘记录：after 只能在 review 成功之后生成")
        topic, plan_id = run["topic"], run["plan_id"]
        before_receipt = _load_json(self.snapshot_receipt(topic, plan_id, "before"), None)
        if not isinstance(before_receipt, dict) or not self.snapshot(topic, plan_id, "before").is_file():
            raise LifecycleError(
                f"缺 before 主文档快照：{plan_id} 不能在 review 后用当前 TOPIC 补造 before。"
                "新轮次请在启动前调用 before()；历史轮次只能记录缺口")
        document = self.topic_dir(topic) / "TOPIC.md"
        if not document.is_file():
            raise LifecycleError(f"缺专题主文档：{self.layout.topics}/{topic}/TOPIC.md")
        raw = document.read_bytes()
        digest = _digest(raw)
        if digest == before_receipt.get("sha256"):
            raise LifecycleError("主文档与 before 完全相同：判决没有实际落到主文档（review 不应通过）")
        review_row = reviews[0]
        receipt = {
            "schema": SCHEMA, "stage": "after", "topic": topic, "plan_id": plan_id,
            "run_id": run_id, "prepared_id": run["prepared_id"],
            "before_sha256": before_receipt.get("sha256"), "review_record_sha256": review_row["record_sha256"],
            "source": _rel(self.root, document), "source_sha256": digest,
            "sha256": digest, "bytes": len(raw), "at": _iso(review_row["created_at"]),
        }
        target = self.snapshot(topic, plan_id, "after")
        state = _write_once(target, raw, label=f"after 快照 {plan_id}")
        receipt_path = self.snapshot_receipt(topic, plan_id, "after")
        _write_once(receipt_path, _pretty(receipt).encode("utf-8"), label=f"after 收据 {plan_id}")
        return {"ok": True, "stage": "after", "state": state, "path": _rel(self.root, target),
                "receipt": _rel(self.root, receipt_path), **receipt}

    # -- 对外：hook 适配 ----------------------------------------------------

    def post(self, kind, payload):
        """`event_sink` 适配：review 落库后自动 after + sync；**永不抛**。

        ⛔ 失败只追加 diagnostic：不覆盖调用方退出码、⛔ 不阻断安全停、⛔ 不回滚已落的复盘。
        """
        payload = payload if isinstance(payload, dict) else {}
        try:
            if kind == "evidence":
                return {"ok": True, "skipped": "证据随 result_analysis 的 review 收据送达"}
            if kind != "result_analysis":
                return self._hook_diagnostic(kind, payload, f"未支持的 hook kind：{kind}")
            run_id = payload.get("run_id")
            topic = payload.get("topic") or ""
            plan_id = payload.get("plan_id")
            result = {"ok": True, "kind": kind, "run_id": run_id}
            try:
                result["after"] = self.after(run_id)
            except (LifecycleError, OSError, ValueError) as exc:
                result["after_error"] = f"{type(exc).__name__}: {exc}"
            result["sync"] = self.sync(topic, plan_id=plan_id)
            if "after_error" in result:
                result["ok"] = False
                return self._hook_diagnostic(kind, payload, result["after_error"], extra=result)
            return result
        except (LifecycleError, OSError, ValueError, sqlite3.Error) as exc:  # noqa: BLE001
            return self._hook_diagnostic(kind, payload, f"{type(exc).__name__}: {exc}")

    def _hook_diagnostic(self, kind, payload, error, *, extra=None):
        """hook 失败只落 diagnostic（append-only），⛔ 不抛、⛔ 不改退出码。"""
        row = {"schema": SCHEMA, "at": _iso(time.time()), "kind": str(kind),
               "error": str(error), "run_id": payload.get("run_id"),
               "topic": payload.get("topic"), "plan_id": payload.get("plan_id"),
               "note": "hook 失败只记诊断：不覆盖退出码，不阻断安全停"}
        try:
            _append_jsonl(self.log_path, row)
        except OSError:
            pass
        out = {"ok": False, "kind": kind, "diagnostic": row}
        if extra:
            out.update(extra)
        return out


__all__ = ["Lifecycle", "LifecycleError", "TERMINAL_KIND", "FORBIDDEN_SOURCES"]
