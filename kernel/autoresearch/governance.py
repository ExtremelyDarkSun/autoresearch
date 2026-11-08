# GENERATED-BY: portable extraction / repository bootstrap (2026-11-09 +08:00)
# LAST-EDIT: portable extraction / repository bootstrap (2026-11-09 +08:00)
# PURPOSE: 通用研究治理层 —— 事前方案校验与冻结哈希、独立方案审计的版本绑定与次数上限、专项意图的来源与节点监控、
#          经验候选的四处置与修订失效、角色包/委派登记、必读与文档冷件台账。全部是本地 append-only 登记，不联网、不伪造授权。
# ORIGIN: none（新文件）。机制抽取自上游组件 plan_audit.md / intent_monitor.md / research_lessons.md / trajectory_review.md /
#         reading_required.json / CONTRACT.md / CONTEXT_LOADING.md / DOC_MAINTENANCE.md 与 EXPERIMENT_RULES.md 的
#         事前设计、三类对照、结果分支表；只保留与具体赛事无关的通用部分，源头的个人渠道与实例值一律不带。
# CUMULATIVE-DELTA: 首版。默认 fail-closed：审计默认必审、关闭审计必须有 verifier 核过的用户证据、
#         没有 verifier 时一切自述只标 declared（永不冒充 verified）。

"""通用研究治理层。

这一层解决的问题：**研究的每一步都可以自述做了，但自述不能当证据。**
所以每条治理记录都带两样东西 —— 一个可核的绑定值（哈希 / 版本号 / 题集）与一个**来源等级**
（`declared` = 本地自述；`verified` = 由注入的 verifier 用真实宿主证据核过）。

三条硬边界
==========

1. **不联网、不读凭据、不起进程。** 本模块只读写调用方给的 `runtime.Store`。
2. **不伪造授权与读证据。** 没有注入 verifier 时，任何「用户批准了」「宿主证明它读了」
   的记录只能是 `declared`；需要 `verified` 的路径直接抛 `GovernanceError`。
3. **成功校验 ≠ 官方结论。** `validate_plan()` 只判方案形状是否可审、可冻结；
   它不判断假设对不对，也不产出任何通过率。

对外接口
========

    validate_plan(plan) -> list[str]              # 纯函数，不碰 store
    plan_fingerprint(plan) -> str                 # 只哈希「关键字段」
    plan_key_fields(plan) -> dict
    Governance(store, *, verifier=None, policy=None, experiment_id=None)
    Governance.__init__ 会建自己的表（幂等），不改 store 的表与触发器。

`plan` 的关键字段集合 = `KEY_FIELDS`。改这些字段 ⇒ 必须重新审计；
改 `WORDING_FIELDS` 或其它字段 ⇒ 指纹不变 ⇒ **不要求重新审计**（来源：上游
plan_audit.md §6.1 的教训 —— 改一句动机就让上一轮审计作废，反过来压制了修改）。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time

from kernel.autoresearch.runtime.store import StoreError, canonical_json

# ---------------------------------------------------------------------------
# 常量：方案的关键字段与枚举
# ---------------------------------------------------------------------------

#: 参与指纹的字段。改其中任何一个 ⇒ 旧审计不再覆盖。
KEY_FIELDS = (
    "id",
    "goal",
    "hypothesis",
    "change_set",
    "criteria",
    "baseline",
    "case_sets",
    "previous_passes",
    "branches",
    "budget",
    "input_files",
)

#: 只影响叙述的字段。改它们**不**触发重新审计。
WORDING_FIELDS = ("title", "notes", "rationale", "summary", "open_questions", "why")

CASE_SET_KINDS = ("target", "regression", "control")
BRANCH_KINDS = ("support", "contradict", "inconclusive")
DIRECTIONS = ("increase", "decrease")

AUDIT_CATEGORIES = (
    "hypothesis",
    "design",
    "criterion",
    "history",
    "cost",
    "branches",
    "activation",
)

AUDIT_VERDICTS = ("ok", "issue", "unknown")
DISPOSITIONS = ("accepted", "declined", "needs_user")
WAIVER = "waiver"

INTENT_STATES = ("none", "active", "blocked", "closed")
INTENT_TRIGGERS = ("result_analysis", "user_update", "material_deviation")
INTENT_REASONS = ("achieved", "withdrawn", "replaced", "unsatisfiable")
MONITOR_VERDICTS = ("on_track", "drifting", "blocked", "achieved", "unachievable")

LESSON_OPTIONS = ("keep", "keep-edited", "keep-pending", "drop")
LESSON_STATUS = {
    "keep": "confirmed",
    "keep-edited": "confirmed",
    "keep-pending": "confirmed-pending",
    "drop": "dropped",
}

ROLE_NAMES = (
    "researcher",
    "planner",
    "runner",
    "scribe",
    "independent-reviewer",
    "intent-monitor",
    "trajectory-reviewer",
)

#: 人读现状页的八节（顺序即页序）。缺一节 = 没写完。
STATUS_SECTIONS = (
    "范围与目标",
    "你的要求",
    "当前结论",
    "证据",
    "还没做成的事",
    "需要你拍板",
    "证据在哪",
    "自推进状态",
)

DOC_TIERS = ("hot", "cold")

INDEPENDENCE_DECLARED = "declared"
INDEPENDENCE_VERIFIED = "verified"

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
PATH_KEYS = ("path", "cold_path", "index_path")


class GovernanceError(ValueError):
    """治理操作被拒。`errors` 属性带逐条原因（便于 CLI 一次列全）。"""

    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = list(errors or [])


# ---------------------------------------------------------------------------
# 纯函数：校验与指纹
# ---------------------------------------------------------------------------


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _finite(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _non_negative_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _relative_path(value) -> bool:
    if not _text(value):
        return False
    text = value.replace("\\", "/")
    if text.startswith("/") or re.match(r"^[A-Za-z]:/", text):
        return False
    return ".." not in text.split("/")


def _hypothesis_key(plan: dict):
    """`hypothesis` 允许写成一句话，也允许写成 `{id, text}`。

    写成对象时才有 `id` 可用来核「同一轮只改一个假设下的整组改动」。
    """
    hypothesis = plan.get("hypothesis")
    if isinstance(hypothesis, dict):
        return hypothesis.get("id")
    return None


def validate_plan(plan: dict) -> list[str]:
    """返回错误列表（空 = 可以冻结）。**纯函数**：不读文件、不碰 store。

    这一层只判「方案能不能被独立审计」，不判断假设成立与否。
    所有数值必须是有限数；**布尔值不算数值**（`True` 不能当 1 用）。
    """
    errors: list[str] = []

    if not isinstance(plan, dict):
        return ["plan 必须是 dict"]

    for key in KEY_FIELDS:
        if key not in plan:
            errors.append(f"缺少顶层字段 {key}")
    if errors and any(item.endswith("缺少顶层字段 id") for item in errors):
        return errors

    # ---- id / goal ----
    if "id" in plan and not _text(plan.get("id")):
        errors.append("id 必须是非空字符串")
    if "goal" in plan and not _text(plan.get("goal")):
        errors.append("goal 必须是非空字符串（这一轮要回答的问题）")

    # ---- hypothesis ----
    hypothesis = plan.get("hypothesis")
    if "hypothesis" in plan:
        if isinstance(hypothesis, str):
            if not hypothesis.strip():
                errors.append("hypothesis 必须是非空字符串，或 {id, text} 对象")
        elif isinstance(hypothesis, dict):
            if not _text(hypothesis.get("id")):
                errors.append("hypothesis.id 必须是非空字符串")
            if not _text(hypothesis.get("text")):
                errors.append("hypothesis.text 必须是可证伪的一句话")
        else:
            errors.append("hypothesis 必须是字符串或 {id, text} 对象")

    # ---- change_set：一个假设下的整组改动 ----
    change_set = plan.get("change_set")
    if "change_set" in plan:
        if not isinstance(change_set, list):
            errors.append("change_set 必须是数组")
        elif not change_set:
            errors.append("change_set 不能为空：一次改动至少要有一项，且必须写清它属于哪个假设")
        else:
            seen_ids: set[str] = set()
            keys: set = set()
            for index, item in enumerate(change_set):
                label = f"change_set[{index}]"
                if isinstance(item, str):
                    if not item.strip():
                        errors.append(f"{label} 不能是空白字符串")
                    continue
                if not isinstance(item, dict):
                    errors.append(f"{label} 必须是字符串或对象")
                    continue
                if not _text(item.get("description")):
                    errors.append(f"{label}.description 必须非空（改了什么）")
                change_id = item.get("id")
                if change_id is not None:
                    if not _text(change_id):
                        errors.append(f"{label}.id 必须是非空字符串")
                    elif change_id in seen_ids:
                        errors.append(f"{label}.id 与前面的改动重复：{change_id}")
                    else:
                        seen_ids.add(change_id)
                if "hypothesis_id" in item:
                    value = item.get("hypothesis_id")
                    if not _text(value):
                        errors.append(f"{label}.hypothesis_id 必须是非空字符串")
                    else:
                        keys.add(value)
            if len(keys) > 1:
                errors.append(
                    "change_set 里出现多个 hypothesis_id："
                    f"{sorted(keys)}；一次一轮只允许改**一个假设下的整组改动**"
                )
            elif keys:
                declared = _hypothesis_key(plan)
                only = next(iter(keys))
                if declared is None:
                    errors.append(
                        "change_set 写了 hypothesis_id，但 hypothesis 没写成 {id, text} 对象 ⇒ 无法核对"
                    )
                elif only != declared:
                    errors.append(
                        f"change_set 的 hypothesis_id={only!r} 与 hypothesis.id={declared!r} 不一致"
                    )

    # ---- criteria：判据与出处 ----
    criteria = plan.get("criteria")
    if "criteria" in plan:
        if not isinstance(criteria, dict):
            errors.append("criteria 必须是对象")
        else:
            for key in ("metric", "direction", "threshold", "source"):
                if key not in criteria:
                    errors.append(f"缺少 criteria.{key}")
            if "metric" in criteria and not _text(criteria.get("metric")):
                errors.append("criteria.metric 必须非空（量什么）")
            if "direction" in criteria and criteria.get("direction") not in DIRECTIONS:
                errors.append(
                    f"criteria.direction 必须是 {' 或 '.join(DIRECTIONS)}，"
                    f"实际 {criteria.get('direction')!r}"
                )
            if "threshold" in criteria:
                if not _finite(criteria.get("threshold")):
                    errors.append(
                        f"criteria.threshold 必须是有限数值，实际 {criteria.get('threshold')!r}"
                        "（布尔值不算数值）"
                    )
            if "source" in criteria and not _text(criteria.get("source")):
                errors.append(
                    "criteria.source 必须非空：门槛要有出处（哪次读数 / 哪条规则 / 哪份契约）"
                )

    # ---- baseline ----
    baseline = plan.get("baseline")
    if "baseline" in plan:
        if not isinstance(baseline, dict):
            errors.append("baseline 必须是对象")
        else:
            if not _text(baseline.get("label")):
                errors.append("baseline.label 必须非空（这一轮对照的是谁）")
            if not _text(baseline.get("fingerprint")):
                errors.append("baseline.fingerprint 必须非空（对照件的内容指纹）")

    # ---- case_sets：三类对照 ----
    case_sets = plan.get("case_sets")
    if "case_sets" in plan:
        if not isinstance(case_sets, dict):
            errors.append("case_sets 必须是对象")
        else:
            values: dict[str, list[str]] = {}
            for kind in CASE_SET_KINDS:
                if kind not in case_sets:
                    errors.append(f"缺少 case_sets.{kind}")
                    continue
                items = case_sets.get(kind)
                if not isinstance(items, list):
                    errors.append(f"case_sets.{kind} 必须是数组")
                    continue
                cleaned: list[str] = []
                for index, item in enumerate(items):
                    if not _text(item):
                        errors.append(f"case_sets.{kind}[{index}] 必须是非空字符串")
                        continue
                    cleaned.append(item)
                duplicates = sorted({x for x in cleaned if cleaned.count(x) > 1})
                if duplicates:
                    errors.append(
                        f"case_sets.{kind} 里有重复题号 {duplicates}："
                        "同一题在题单里出现两次，后一次会覆盖前一次的落点"
                    )
                values[kind] = cleaned
            if "target" in values and not values["target"]:
                errors.append("case_sets.target 不能为空：这一轮要读的那个量得至少有一道题产生它")
            for left, right in (("target", "regression"), ("target", "control"), ("regression", "control")):
                if left in values and right in values:
                    overlap = sorted(set(values[left]) & set(values[right]))
                    if overlap:
                        errors.append(
                            f"case_sets.{left} 与 case_sets.{right} 重叠 {overlap}："
                            "同一个题号不能同时扮演两种对照角色"
                        )

    # ---- previous_passes：历史已通过项必须留在回归集合里 ----
    previous = plan.get("previous_passes")
    if "previous_passes" in plan:
        if not isinstance(previous, list):
            errors.append("previous_passes 必须是数组")
        else:
            bad = [item for item in previous if not _text(item)]
            if bad:
                errors.append(f"previous_passes 里必须全是非空字符串，问题项：{bad[:5]}")
            cleaned_prev = [item for item in previous if _text(item)]
            duplicates = sorted({x for x in cleaned_prev if cleaned_prev.count(x) > 1})
            if duplicates:
                errors.append(f"previous_passes 里有重复项 {duplicates}")
            regression = None
            if isinstance(case_sets, dict) and isinstance(case_sets.get("regression"), list):
                regression = [x for x in case_sets["regression"] if _text(x)]
            if regression is not None:
                missing = sorted(set(cleaned_prev) - set(regression))
                if missing:
                    errors.append(
                        "previous_passes 必须被 case_sets.regression 覆盖，缺："
                        f"{missing}（历史已通过的题不许从回归集合里掉出去）"
                    )

    # ---- branches：结果分支表 ----
    branches = plan.get("branches")
    if "branches" in plan:
        if not isinstance(branches, dict):
            errors.append("branches 必须是对象")
        else:
            actions: dict[str, str] = {}
            for kind in BRANCH_KINDS:
                if kind not in branches:
                    errors.append(f"缺少 branches.{kind}")
                    continue
                branch = branches.get(kind)
                if not isinstance(branch, dict):
                    errors.append(f"branches.{kind} 必须是对象（至少含 action）")
                    continue
                action = branch.get("action")
                if not _text(action):
                    errors.append(f"branches.{kind}.action 必须非空：这一支出现时下一步做什么")
                else:
                    actions[kind] = action.strip()
            if len(actions) == len(BRANCH_KINDS) and len(set(actions.values())) == 1:
                errors.append(
                    "branches 三支的 action 完全相同："
                    "三支行动一样等于没有分支表（支撑/反驳/判不了要导向不同下一步）"
                )

    # ---- budget ----
    budget = plan.get("budget")
    if "budget" in plan:
        if not isinstance(budget, dict):
            errors.append("budget 必须是对象")
        else:
            if "max_cost_units" not in budget:
                errors.append("缺少 budget.max_cost_units")
            elif not _non_negative_int(budget.get("max_cost_units")):
                errors.append(
                    f"budget.max_cost_units 必须是 ≥0 的整数，实际 {budget.get('max_cost_units')!r}"
                )
            if "max_seconds" not in budget:
                errors.append("缺少 budget.max_seconds")
            elif not _positive_int(budget.get("max_seconds")):
                errors.append(
                    f"budget.max_seconds 必须是 >0 的整数，实际 {budget.get('max_seconds')!r}"
                )
            for key, value in budget.items():
                if key in ("max_cost_units", "max_seconds"):
                    continue
                if _finite(value):
                    continue
                if isinstance(value, bool):
                    errors.append(f"budget.{key} 是布尔值：布尔不算数值，请写清是什么单位")
                elif isinstance(value, (int, float)):
                    errors.append(f"budget.{key} 不是有限数值：{value!r}")

    # ---- input_files：相对路径清单 ----
    input_files = plan.get("input_files")
    if "input_files" in plan:
        if not isinstance(input_files, list):
            errors.append("input_files 必须是数组")
        else:
            for index, item in enumerate(input_files):
                if not _relative_path(item):
                    errors.append(
                        f"input_files[{index}] 必须是仓库内相对路径（不许绝对路径、不许含 '..'），"
                        f"实际 {item!r}"
                    )

    return errors


def _normalize(value):
    """指纹归一化：只做「顺序不影响语义」的那几处排序，不改内容。"""
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def plan_key_fields(plan: dict) -> dict:
    """取出参与指纹的关键字段（缺的记为 None，保持键集合稳定）。"""
    if not isinstance(plan, dict):
        return {}
    fields: dict = {}
    for key in KEY_FIELDS:
        value = plan.get(key)
        if key in ("previous_passes", "input_files") and isinstance(value, list):
            value = sorted(str(item) for item in value)
        if key == "case_sets" and isinstance(value, dict):
            value = {
                kind: sorted(str(x) for x in value.get(kind, []))
                if isinstance(value.get(kind), list)
                else value.get(kind)
                for kind in sorted(set(value) | set(CASE_SET_KINDS))
            }
        fields[key] = _normalize(value)
    return fields


def plan_fingerprint(plan: dict) -> str:
    """关键字段的 sha256。**改叙述字段不会变**，改关键字段一定变。"""
    return hashlib.sha256(canonical_json(plan_key_fields(plan)).encode("utf-8")).hexdigest()


def _digest(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 治理层
# ---------------------------------------------------------------------------

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS gov_policy(\n"
    "  id INTEGER PRIMARY KEY, scope TEXT NOT NULL, target TEXT NOT NULL,\n"
    "  mode TEXT NOT NULL, actor TEXT NOT NULL, evidence_json TEXT NOT NULL,\n"
    "  independence TEXT NOT NULL, created_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS gov_plans(\n"
    "  plan_id TEXT NOT NULL, version INTEGER NOT NULL, fingerprint TEXT NOT NULL,\n"
    "  key_fingerprint TEXT NOT NULL, plan_json TEXT NOT NULL, actor TEXT NOT NULL,\n"
    "  created_at REAL NOT NULL, PRIMARY KEY(plan_id, version))",
    "CREATE TABLE IF NOT EXISTS gov_audits(\n"
    "  prepared_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, plan_version INTEGER NOT NULL,\n"
    "  key_fingerprint TEXT NOT NULL, owner TEXT NOT NULL, note TEXT NOT NULL,\n"
    "  index_no INTEGER NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL,\n"
    "  UNIQUE(plan_id, key_fingerprint, index_no))",
    "CREATE TABLE IF NOT EXISTS gov_audit_reports(\n"
    "  report_id TEXT PRIMARY KEY, prepared_id TEXT NOT NULL,\n"
    "  plan_id TEXT NOT NULL, key_fingerprint TEXT NOT NULL,\n"
    "  report_json TEXT NOT NULL, independence TEXT NOT NULL,\n"
    "  evidence_json TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS gov_audit_responses(\n"
    "  id INTEGER PRIMARY KEY, report_id TEXT NOT NULL, issue_id TEXT NOT NULL,\n"
    "  disposition TEXT NOT NULL, reason TEXT NOT NULL, sources_json TEXT NOT NULL,\n"
    "  actor TEXT NOT NULL, created_at REAL NOT NULL,\n"
    "  UNIQUE(report_id, issue_id))",
    "CREATE TABLE IF NOT EXISTS gov_waivers(\n"
    "  id INTEGER PRIMARY KEY, report_id TEXT NOT NULL, issue_id TEXT NOT NULL,\n"
    "  key_fingerprint TEXT NOT NULL, reason TEXT NOT NULL, evidence_json TEXT NOT NULL,\n"
    "  independence TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL,\n"
    "  UNIQUE(report_id, issue_id))",
    "CREATE TABLE IF NOT EXISTS gov_intents(\n"
    "  intent_id TEXT PRIMARY KEY, state TEXT NOT NULL, quote TEXT NOT NULL,\n"
    "  source_session TEXT NOT NULL, source_seq INTEGER NOT NULL,\n"
    "  actor TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS gov_intent_events(\n"
    "  id INTEGER PRIMARY KEY, intent_id TEXT NOT NULL, kind TEXT NOT NULL,\n"
    "  detail_json TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS gov_user_updates(\n"
    "  id INTEGER PRIMARY KEY, source_session TEXT NOT NULL, seq INTEGER NOT NULL,\n"
    "  quote TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(source_session, seq))",
    "CREATE TABLE IF NOT EXISTS gov_intent_assessments(\n"
    "  id INTEGER PRIMARY KEY, source_session TEXT NOT NULL, covers_seq INTEGER NOT NULL,\n"
    "  decision TEXT NOT NULL, quote TEXT NOT NULL, intent_id TEXT,\n"
    "  answer_seq INTEGER, actor TEXT NOT NULL, created_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS gov_intent_triggers(\n"
    "  trigger_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL, kind TEXT NOT NULL,\n"
    "  detail TEXT NOT NULL, covered_by TEXT, actor TEXT NOT NULL, created_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS gov_intent_reports(\n"
    "  report_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL, covers_json TEXT NOT NULL,\n"
    "  user_seq INTEGER NOT NULL, verdict TEXT NOT NULL, basis TEXT NOT NULL,\n"
    "  independence TEXT NOT NULL, evidence_json TEXT NOT NULL, actor TEXT NOT NULL,\n"
    "  created_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS gov_lessons(\n"
    "  candidate_id TEXT NOT NULL, rev INTEGER NOT NULL, title TEXT NOT NULL,\n"
    "  method TEXT NOT NULL, method_hash TEXT NOT NULL, body_json TEXT NOT NULL,\n"
    "  actor TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(candidate_id, rev))",
    "CREATE TABLE IF NOT EXISTS gov_lesson_confirmations(\n"
    "  id INTEGER PRIMARY KEY, candidate_id TEXT NOT NULL, rev INTEGER NOT NULL,\n"
    "  method_hash TEXT NOT NULL, option TEXT NOT NULL, quote TEXT NOT NULL,\n"
    "  source TEXT NOT NULL, source_session TEXT, source_seq INTEGER,\n"
    "  independence TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS gov_roles(\n"
    "  id INTEGER PRIMARY KEY, role TEXT NOT NULL, path TEXT NOT NULL,\n"
    "  sha256 TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS gov_delegations(\n"
    "  id INTEGER PRIMARY KEY, child_session TEXT NOT NULL, role TEXT NOT NULL,\n"
    "  rolepack_sha TEXT NOT NULL, prompt_sha TEXT NOT NULL, parent_session TEXT NOT NULL,\n"
    "  independence TEXT NOT NULL, evidence_json TEXT NOT NULL, actor TEXT NOT NULL,\n"
    "  created_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS gov_reads(\n"
    "  id INTEGER PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL, role TEXT NOT NULL,\n"
    "  session TEXT NOT NULL, independence TEXT NOT NULL, evidence_json TEXT NOT NULL,\n"
    "  created_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS gov_docs(\n"
    "  id INTEGER PRIMARY KEY, doc_id TEXT NOT NULL, tier TEXT NOT NULL,\n"
    "  path TEXT NOT NULL, bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,\n"
    "  limit_bytes INTEGER, actor TEXT NOT NULL, created_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS gov_doc_rolls(\n"
    "  id INTEGER PRIMARY KEY, doc_id TEXT NOT NULL, hot_sha_before TEXT NOT NULL,\n"
    "  hot_bytes_before INTEGER NOT NULL, cold_path TEXT NOT NULL, cold_sha TEXT NOT NULL,\n"
    "  index_path TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL)",
)

_APPEND_ONLY_TABLES = (
    "gov_plans",
    "gov_audit_reports",
    "gov_audit_responses",
    "gov_waivers",
    "gov_lesson_confirmations",
    "gov_roles",
    "gov_delegations",
    "gov_reads",
    "gov_docs",
    "gov_doc_rolls",
    "gov_user_updates",
    "gov_intent_assessments",
    "gov_intent_events",
)


class Governance:
    """治理登记簿。复用 `runtime.Store` 的事务与事件流。

    参数
    ----
    store : `kernel.autoresearch.runtime.store.Store` 实例（file-backed sqlite）。
    verifier : 可选。任何带 `verify(kind, payload) -> dict|None` 的对象。
        - 返回 dict 且 `verified is True` ⇒ 该条记录标 `verified`；
        - 返回 None / 抛异常 / 没有注入 verifier ⇒ 该条记录只能标 `declared`。
    policy : 可选 dict，覆盖默认政策（见 `DEFAULT_POLICY`）。
    experiment_id : 可选。给了就把事件挂在该实验号下（必须已存在于 store），
        不给则事件不挂实验（`exp_id=NULL`）。

    **没有任何方法会自己造出一条 `verified`。** 独立性、授权、宿主读证据
    三者都只能由注入的 verifier 提供。
    """

    DEFAULT_POLICY = {
        # 审计默认必审（fail-closed）：删配置、什么都不写都还是 required。
        "audit_mode": "required",
        # 每个方案版本最多审几次。扩额需要用户在真实通道里裁决，本层不伪造。
        "max_audits": 2,
        # 要求审计者独立性由 verifier 核过（默认 False ⇒ declared 只作提示）。
        "require_verified_independence": False,
        # 关闭审计是否必须有 verifier 核过的用户证据（默认 True，且不可由 policy 关掉）。
        "policy_off_requires_verified_evidence": True,
        # 关闭审计的用户证据必须晚于该方案最后一次 prepare（防「先准备再偷偷关」）。
        "policy_off_after_prepare": True,
        # 没有专项意图时是否仍拦「未评估的用户原话」。
        "block_unassessed_without_intent": True,
    }

    #: 这几条不允许被 policy 关掉（否则等于关掉整层的安全阀）。
    LOCKED_POLICY = ("policy_off_requires_verified_evidence", "policy_off_after_prepare")

    def __init__(self, store, *, verifier=None, policy=None, experiment_id=None):
        if store is None or not hasattr(store, "transaction"):
            raise GovernanceError("Governance 需要一个 runtime.store.Store 实例")
        self.store = store
        self.verifier = verifier
        self.experiment_id = experiment_id
        merged = dict(self.DEFAULT_POLICY)
        if policy:
            if not isinstance(policy, dict):
                raise GovernanceError("policy 必须是 dict")
            for key, value in policy.items():
                if key in self.LOCKED_POLICY and value is not True:
                    raise GovernanceError(
                        f"policy.{key} 不能被关掉：它是本层的安全阀（关闭审计要真证据）"
                    )
                if key not in self.DEFAULT_POLICY:
                    raise GovernanceError(
                        f"policy.{key} 不是已知政策名；已知：{', '.join(sorted(self.DEFAULT_POLICY))}"
                    )
                merged[key] = value
        self.policy = merged
        with self.store.transaction() as conn:
            for statement in _SCHEMA:
                conn.execute(statement)
            for table in _APPEND_ONLY_TABLES:
                conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
                )
                conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
                )
            conn.execute(
                "CREATE TRIGGER IF NOT EXISTS gov_intents_no_delete BEFORE DELETE ON gov_intents "
                "BEGIN SELECT RAISE(ABORT, 'gov_intents rows are never deleted'); END"
            )

    # -- 内部 ---------------------------------------------------------------

    def _now(self) -> float:
        return time.time()

    def _emit(self, conn, kind: str, actor: str, payload: dict) -> None:
        try:
            self.store.emit(conn, self.experiment_id, kind, actor, payload)
        except StoreError as exc:  # FK 失败等
            raise GovernanceError(
                f"事件落账失败（experiment_id={self.experiment_id!r}）：{exc}"
            ) from exc

    def _require_text(self, value, name: str) -> str:
        if not _text(value):
            raise GovernanceError(f"{name} 必须是非空字符串，实际 {value!r}")
        return value

    def _require_sha(self, value, name: str) -> str:
        if not isinstance(value, str) or not _SHA_RE.match(value):
            raise GovernanceError(f"{name} 必须是 64 位小写十六进制 sha256，实际 {value!r}")
        return value

    def _verify(self, kind: str, evidence) -> tuple[str, dict]:
        """调 verifier。**不捕获就升级**：拿不到 verified 就只能是 declared。"""
        if self.verifier is None or evidence is None:
            return INDEPENDENCE_DECLARED, {}
        try:
            result = self.verifier.verify(kind, evidence)
        except Exception:  # noqa: BLE001 - verifier 坏掉不等于核过
            return INDEPENDENCE_DECLARED, {"error": "verifier raised"}
        if isinstance(result, dict) and result.get("verified") is True:
            return INDEPENDENCE_VERIFIED, dict(result)
        return INDEPENDENCE_DECLARED, {"result": result}

    def _row(self, conn, sql: str, params=()):
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _rows(self, conn, sql: str, params=()):
        return [dict(row) for row in conn.execute(sql, params).fetchall()]

    # ------------------------------------------------------------------
    # 政策
    # ------------------------------------------------------------------

    def set_policy(self, *, scope: str, target: str, mode: str, actor: str, evidence=None) -> dict:
        """写一条审计政策。**升级不设门槛，降级要真证据。**

        `mode="off"` 必须带 verifier 核过的用户证据，且证据时间要晚于该范围
        最后一次 `prepare_audit`（防「先准备、再偷偷关掉」）。
        """
        if scope not in ("global", "plan", "role"):
            raise GovernanceError(f"scope 必须是 global / plan / role，实际 {scope!r}")
        if mode not in ("required", "off"):
            raise GovernanceError(f"mode 必须是 required / off，实际 {mode!r}")
        self._require_text(target, "target")
        self._require_text(actor, "actor")
        independence = INDEPENDENCE_DECLARED
        verified: dict = {}
        if mode == "off":
            independence, verified = self._verify("policy_off", evidence)
            if independence != INDEPENDENCE_VERIFIED:
                raise GovernanceError(
                    "关闭审计需要宿主核过的用户证据（verifier 返回 verified=True）；"
                    "本地自述不能关闭审计。没有注入 verifier 时这条路径永远不可用。"
                )
            created = evidence.get("created_at") if isinstance(evidence, dict) else None
            if self.policy.get("policy_off_after_prepare") and _finite(created):
                with self.store.transaction() as conn:
                    last = self._row(
                        conn,
                        "SELECT MAX(created_at) AS t FROM gov_audits WHERE plan_id=?",
                        (target,),
                    )
                if last and last.get("t") and float(created) < float(last["t"]):
                    raise GovernanceError(
                        "关闭审计的用户证据早于该方案最后一次 prepare_audit ⇒ 拒绝"
                        "（先准备、再偷偷关掉的路径必须堵住）"
                    )
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO gov_policy(scope,target,mode,actor,evidence_json,independence,created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (scope, target, mode, actor, canonical_json(verified or {}), independence, self._now()),
            )
            self._emit(conn, "governance_policy", actor,
                       {"scope": scope, "target": target, "mode": mode,
                        "independence": independence})
        return {"scope": scope, "target": target, "mode": mode, "independence": independence}

    def audit_policy(self, plan_id: str | None = None) -> dict:
        """当前生效政策：plan 级 > global 级 > 默认 required。"""
        with self.store.transaction() as conn:
            rows = self._rows(conn, "SELECT * FROM gov_policy ORDER BY id")
        effective = {"mode": "required", "source": "default(fail-closed)", "independence": None}
        for row in rows:
            if row["scope"] == "global" and row["target"] in ("*", ""):
                effective = {"mode": row["mode"], "source": f"global@{row['id']}",
                             "independence": row["independence"]}
        if plan_id:
            for row in rows:
                if row["scope"] == "plan" and row["target"] == plan_id:
                    effective = {"mode": row["mode"], "source": f"plan@{row['id']}",
                                 "independence": row["independence"]}
        return effective

    # ------------------------------------------------------------------
    # 方案冻结
    # ------------------------------------------------------------------

    def freeze_plan(self, plan: dict, *, actor: str) -> dict:
        """校验并冻结一个方案版本。

        返回 `{plan_id, version, fingerprint, key_fingerprint, reaudit_required}`。

        `reaudit_required=False` 表示**只有叙述字段变了**（关键字段指纹没变），
        上一轮审计仍然覆盖这一版 —— 这是「改一句动机不该让审计作废」那条教训。
        """
        self._require_text(actor, "actor")
        errors = validate_plan(plan)
        if errors:
            raise GovernanceError("方案校验不通过，不能冻结：" + "；".join(errors), errors)
        plan_id = plan["id"]
        key_fp = plan_fingerprint(plan)
        fingerprint = _digest(plan)
        with self.store.transaction() as conn:
            last = self._row(
                conn,
                "SELECT version, key_fingerprint FROM gov_plans WHERE plan_id=?"
                " ORDER BY version DESC LIMIT 1",
                (plan_id,),
            )
            version = (last["version"] + 1) if last else 1
            reaudit = True if last is None else last["key_fingerprint"] != key_fp
            conn.execute(
                "INSERT INTO gov_plans(plan_id,version,fingerprint,key_fingerprint,plan_json,actor,created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (plan_id, version, fingerprint, key_fp, canonical_json(plan), actor, self._now()),
            )
            self._emit(conn, "governance_plan_frozen", actor,
                       {"plan_id": plan_id, "version": version,
                        "key_fingerprint": key_fp, "reaudit_required": reaudit})
        return {
            "plan_id": plan_id,
            "version": version,
            "fingerprint": fingerprint,
            "key_fingerprint": key_fp,
            "reaudit_required": reaudit,
        }

    def latest_plan(self, plan_id: str) -> dict | None:
        with self.store.transaction() as conn:
            row = self._row(
                conn,
                "SELECT * FROM gov_plans WHERE plan_id=? ORDER BY version DESC LIMIT 1",
                (plan_id,),
            )
        if not row:
            return None
        row["plan"] = json.loads(row.pop("plan_json"))
        return row

    # ------------------------------------------------------------------
    # 独立方案审计
    # ------------------------------------------------------------------

    def prepare_audit(self, plan_id: str, *, actor: str, owner: str | None = None,
                      note: str = "") -> dict:
        """为当前方案版本准备一次独立审计。

        硬规则：
        * 政策 `off` ⇒ 拒绝准备（不存在「关了又偷偷审」的半状态）；
        * 同一 `key_fingerprint` 最多 `max_audits` 次（默认 2），超限拒绝；
        * `owner`（负责人）不能等于 `actor`（准备者）；
        * 返回的 `prepared_id` **绑定当前关键字段指纹**，报告必须带同一个。
        """
        self._require_text(actor, "actor")
        plan = self.latest_plan(plan_id)
        if plan is None:
            raise GovernanceError(f"方案 {plan_id!r} 还没有冻结过：先 freeze_plan()")
        policy = self.audit_policy(plan_id)
        if policy["mode"] == "off":
            raise GovernanceError(
                f"审计政策是 off（{policy['source']}）⇒ 不能准备审计；"
                "要恢复必须先写一条 required"
            )
        owner = owner or actor
        self._require_text(owner, "owner")
        # ⚠️ 这里**不**要求 owner != actor：准备审计通常就是负责人自己做的。
        # 独立性要求落在**审计者**身上（record_audit 时核 owner != auditor）。
        key_fp = plan["key_fingerprint"]
        with self.store.transaction() as conn:
            used = self._row(
                conn,
                "SELECT COUNT(*) AS n FROM gov_audits WHERE plan_id=? AND key_fingerprint=?",
                (plan_id, key_fp),
            )["n"]
            limit = int(self.policy["max_audits"])
            if used >= limit:
                raise GovernanceError(
                    f"这个方案版本已经审过 {used} 次，达到上限 {limit}；"
                    "扩额必须由用户在真实通道裁决，本层不提供自我扩额入口"
                )
            index_no = used + 1
            prepared_id = "PREP-" + _digest(
                [plan_id, plan["version"], key_fp, index_no, owner]
            )[:16]
            conn.execute(
                "INSERT INTO gov_audits(prepared_id,plan_id,plan_version,key_fingerprint,owner,note,index_no,actor,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (prepared_id, plan_id, plan["version"], key_fp, owner, note or "",
                 index_no, actor, self._now()),
            )
            self._emit(conn, "governance_audit_prepared", actor,
                       {"prepared_id": prepared_id, "plan_id": plan_id,
                        "version": plan["version"], "key_fingerprint": key_fp,
                        "index_no": index_no, "limit": limit})
        return {
            "prepared_id": prepared_id,
            "plan_id": plan_id,
            "plan_version": plan["version"],
            "key_fingerprint": key_fp,
            "audit_index": index_no,
            "limit": limit,
            "remaining": limit - index_no,
        }

    def record_audit(self, prepared_id: str, report: dict, *, actor: str, evidence=None) -> dict:
        """登记一份独立审计报告。

        报告必须**恰好覆盖七个审项**（`AUDIT_CATEGORIES`），每项给
        `verdict` / `note` / `sources`（非空，每项 `{path, locator}`）。
        一致性强判：`verdict` 是 `issue`/`unknown` ⇒ 必须有同 category 的 issue；
        `unknown` 的那一项 issue 必须 `blocking=true`（判不出来不许默认通过）。
        """
        self._require_text(actor, "actor")
        if not isinstance(report, dict):
            raise GovernanceError("report 必须是 dict")
        errors: list[str] = []
        if report.get("prepared_id") != prepared_id:
            errors.append("report.prepared_id 与参数不一致")
        coverage = report.get("coverage")
        if not isinstance(coverage, dict):
            errors.append("report.coverage 必须是对象")
            coverage = {}
        missing = [key for key in AUDIT_CATEGORIES if key not in coverage]
        extra = [key for key in coverage if key not in AUDIT_CATEGORIES]
        if missing:
            errors.append(f"coverage 缺少审项：{missing}")
        if extra:
            errors.append(f"coverage 有未知审项：{extra}（必须恰好七个）")
        bad_categories: set[str] = set()
        for key, item in coverage.items():
            if key not in AUDIT_CATEGORIES:
                continue
            if not isinstance(item, dict):
                errors.append(f"coverage.{key} 必须是对象")
                continue
            verdict = item.get("verdict")
            if verdict not in AUDIT_VERDICTS:
                errors.append(f"coverage.{key}.verdict 必须是 {'/'.join(AUDIT_VERDICTS)}")
            if not _text(item.get("note")):
                errors.append(f"coverage.{key}.note 必须非空（看到了什么）")
            sources = item.get("sources")
            if not isinstance(sources, list) or not sources:
                errors.append(f"coverage.{key}.sources 必须是非空数组")
            else:
                for index, source in enumerate(sources):
                    if not isinstance(source, dict) or not _text(source.get("path")) \
                            or not _text(source.get("locator")):
                        errors.append(f"coverage.{key}.sources[{index}] 必须是 {{path, locator}}")
            if verdict in ("issue", "unknown"):
                bad_categories.add(key)

        issues = report.get("issues", [])
        if not isinstance(issues, list):
            errors.append("report.issues 必须是数组（没有就给 []）")
            issues = []
        issue_ids: set[str] = set()
        categories_with_issues: set[str] = set()
        for index, issue in enumerate(issues):
            if not isinstance(issue, dict):
                errors.append(f"issues[{index}] 必须是对象")
                continue
            issue_id = issue.get("id")
            if not _text(issue_id):
                errors.append(f"issues[{index}].id 必须非空")
            elif issue_id in issue_ids:
                errors.append(f"issues[{index}].id 重复：{issue_id}")
            else:
                issue_ids.add(issue_id)
            category = issue.get("category")
            if category not in AUDIT_CATEGORIES:
                errors.append(f"issues[{index}].category 必须是七个审项之一")
            else:
                categories_with_issues.add(category)
            if not isinstance(issue.get("blocking"), bool):
                errors.append(f"issues[{index}].blocking 必须是布尔值")
            for key in ("problem", "consequence", "suggestion"):
                if not _text(issue.get(key)):
                    errors.append(f"issues[{index}].{key} 必须非空")
            sources = issue.get("sources")
            if not isinstance(sources, list) or not sources:
                errors.append(f"issues[{index}].sources 必须是非空数组")
        for category in sorted(bad_categories - categories_with_issues):
            errors.append(
                f"coverage.{category}.verdict 是 issue/unknown，但没有同 category 的 issue"
                "（只在 note 里说说不算）"
            )
        by_category: dict[str, list] = {}
        for issue in issues:
            if isinstance(issue, dict) and issue.get("category") in AUDIT_CATEGORIES:
                by_category.setdefault(issue["category"], []).append(issue)
        for category, items in coverage.items():
            if not isinstance(items, dict):
                continue
            if items.get("verdict") == "unknown":
                # 判不出来就不许默认通过：这一项必须有 blocking=true 的 issue。
                if not any(issue.get("blocking") is True for issue in by_category.get(category, [])):
                    errors.append(
                        f"coverage.{category}.verdict=unknown，但这一项没有 blocking=true 的 issue"
                        "（判不出来不许默认通过）"
                    )

        resolves = report.get("resolves", [])
        if not isinstance(resolves, list):
            errors.append("report.resolves 必须是数组")
            resolves = []
        for index, item in enumerate(resolves):
            if not isinstance(item, dict) or not _text(item.get("report_id")) \
                    or not _text(item.get("issue_id")) or not _text(item.get("reason")):
                errors.append(f"resolves[{index}] 必须是 {{report_id, issue_id, reason, sources}}")
            elif not isinstance(item.get("sources"), list) or not item["sources"]:
                errors.append(f"resolves[{index}].sources 必须是非空数组")

        if errors:
            raise GovernanceError("审计报告不合规，拒绝登记：" + "；".join(errors), errors)

        with self.store.transaction() as conn:
            prepared = self._row(
                conn, "SELECT * FROM gov_audits WHERE prepared_id=?", (prepared_id,)
            )
            if prepared is None:
                raise GovernanceError(f"没有这个 prepared_id：{prepared_id!r}")
            if prepared["owner"] == actor:
                raise GovernanceError(
                    "审计者不能是方案负责人（owner）：独立审计必须是另一个会话"
                )
            existing = self._row(
                conn, "SELECT report_id FROM gov_audit_reports WHERE prepared_id=?",
                (prepared_id,),
            )
            if existing:
                raise GovernanceError(
                    f"这次 prepare 已经登记过报告（{existing['report_id']}）；一次准备只收一份报告"
                )
            latest = self._row(
                conn,
                "SELECT version, key_fingerprint FROM gov_plans WHERE plan_id=?"
                " ORDER BY version DESC LIMIT 1",
                (prepared["plan_id"],),
            )
            if latest is None or latest["key_fingerprint"] != prepared["key_fingerprint"]:
                raise GovernanceError(
                    "方案关键字段在这一轮审计期间变过 ⇒ 这份报告不能绑定当前版本；"
                    "请对新版本重新 prepare + 复审"
                )
            independence, verified = self._verify("audit_independence", evidence)
            report_id = "AUD-" + _digest([prepared_id, report])[:12]
            conn.execute(
                "INSERT INTO gov_audit_reports(report_id,prepared_id,plan_id,key_fingerprint,"
                "report_json,independence,evidence_json,actor,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (report_id, prepared_id, prepared["plan_id"], prepared["key_fingerprint"],
                 canonical_json(report), independence, canonical_json(verified), actor, self._now()),
            )
            self._emit(conn, "governance_audit_recorded", actor,
                       {"report_id": report_id, "prepared_id": prepared_id,
                        "plan_id": prepared["plan_id"], "independence": independence,
                        "issues": len(issues)})
        return {
            "report_id": report_id,
            "plan_id": prepared["plan_id"],
            "key_fingerprint": prepared["key_fingerprint"],
            "independence": independence,
            "blocking_open": [issue["id"] for issue in issues
                              if isinstance(issue, dict) and issue.get("blocking") is True],
        }

    def respond_audit(self, report_id: str, dispositions: list, *, actor: str) -> dict:
        """登记主会话对每条审计意见的处置。

        每个 `issue_id` 都要有处置，且 `accepted` / `declined` / `needs_user`
        三选一 + `reason` + `sources` 非空；同一份报告里一条意见只能处置一次。
        """
        self._require_text(actor, "actor")
        if not isinstance(dispositions, list) or not dispositions:
            raise GovernanceError("dispositions 必须是非空数组（每条意见都要有处置）")
        errors: list[str] = []
        cleaned: list[dict] = []
        for index, item in enumerate(dispositions):
            if not isinstance(item, dict):
                errors.append(f"dispositions[{index}] 必须是对象")
                continue
            issue_id = item.get("issue_id")
            if not _text(issue_id):
                errors.append(f"dispositions[{index}].issue_id 必须非空")
            if item.get("disposition") not in DISPOSITIONS:
                errors.append(
                    f"dispositions[{index}].disposition 必须是 {'/'.join(DISPOSITIONS)}"
                )
            if not _text(item.get("reason")):
                errors.append(f"dispositions[{index}].reason 必须非空（不许写「已考虑」）")
            sources = item.get("sources")
            if not isinstance(sources, list) or not sources:
                errors.append(f"dispositions[{index}].sources 必须是非空数组")
            cleaned.append(item)
        if errors:
            raise GovernanceError("处置记录不合规：" + "；".join(errors), errors)

        with self.store.transaction() as conn:
            report = self._row(
                conn, "SELECT * FROM gov_audit_reports WHERE report_id=?", (report_id,)
            )
            if report is None:
                raise GovernanceError(f"没有这份审计报告：{report_id!r}")
            payload = json.loads(report["report_json"])
            known = {issue["id"] for issue in payload.get("issues", [])
                     if isinstance(issue, dict) and _text(issue.get("id"))}
            recorded = []
            for item in cleaned:
                issue_id = item["issue_id"]
                if issue_id not in known:
                    raise GovernanceError(
                        f"issue_id={issue_id!r} 不在这份报告的 issues 里"
                    )
                duplicate = self._row(
                    conn,
                    "SELECT id FROM gov_audit_responses WHERE report_id=? AND issue_id=?",
                    (report_id, issue_id),
                )
                if duplicate:
                    raise GovernanceError(
                        f"报告 {report_id} 的意见 {issue_id} 已经处置过；同一批不许重复处置"
                    )
                conn.execute(
                    "INSERT INTO gov_audit_responses(report_id,issue_id,disposition,reason,"
                    "sources_json,actor,created_at) VALUES(?,?,?,?,?,?,?)",
                    (report_id, issue_id, item["disposition"], item["reason"],
                     canonical_json(item["sources"]), actor, self._now()),
                )
                recorded.append(issue_id)
            self._emit(conn, "governance_audit_responded", actor,
                       {"report_id": report_id, "issues": recorded})
        return {"report_id": report_id, "recorded": recorded}

    def waive(self, report_id: str, issue_id: str, *, reason: str, evidence, actor: str) -> dict:
        """人工豁免一条阻断项。**必须有 verifier 核过的用户证据。**

        豁免绑定当时的 `key_fingerprint`：方案关键字段一变，豁免自动失效。
        """
        self._require_text(actor, "actor")
        self._require_text(reason, "reason")
        independence, verified = self._verify("waiver", evidence)
        if independence != INDEPENDENCE_VERIFIED:
            raise GovernanceError(
                "豁免需要宿主核过的用户证据（verifier verified=True）；"
                "本地自述不能关闭阻断项"
            )
        with self.store.transaction() as conn:
            report = self._row(
                conn, "SELECT * FROM gov_audit_reports WHERE report_id=?", (report_id,)
            )
            if report is None:
                raise GovernanceError(f"没有这份审计报告：{report_id!r}")
            existing = self._row(
                conn, "SELECT id FROM gov_waivers WHERE report_id=? AND issue_id=?",
                (report_id, issue_id),
            )
            if existing:
                raise GovernanceError("这条意见已经豁免过")
            conn.execute(
                "INSERT INTO gov_waivers(report_id,issue_id,key_fingerprint,reason,evidence_json,"
                "independence,actor,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (report_id, issue_id, report["key_fingerprint"], reason,
                 canonical_json(verified), independence, actor, self._now()),
            )
            self._emit(conn, "governance_waiver", actor,
                       {"report_id": report_id, "issue_id": issue_id,
                        "key_fingerprint": report["key_fingerprint"]})
        return {"report_id": report_id, "issue_id": issue_id,
                "independence": independence, "key_fingerprint": report["key_fingerprint"]}

    def audit_state(self, plan_id: str) -> dict:
        """能不能带这一版方案进下一轮。**只读**，不写任何东西。

        返回 `{ok, problems, blocking_open, audits_used, limit, report_id,
        independence, key_fingerprint, policy}`。`ok=False` 就是不放行。
        """
        with self.store.transaction() as conn:
            plan = self._row(
                conn,
                "SELECT * FROM gov_plans WHERE plan_id=? ORDER BY version DESC LIMIT 1",
                (plan_id,),
            )
            if plan is None:
                return {"ok": False, "problems": [f"方案 {plan_id!r} 不存在"],
                        "blocking_open": [], "audits_used": 0,
                        "limit": int(self.policy["max_audits"]), "report_id": None,
                        "independence": None, "key_fingerprint": None,
                        "policy": self.audit_policy(plan_id)}
            key_fp = plan["key_fingerprint"]
            audits = self._rows(
                conn,
                "SELECT * FROM gov_audits WHERE plan_id=? AND key_fingerprint=? ORDER BY index_no",
                (plan_id, key_fp),
            )
            reports = self._rows(
                conn,
                "SELECT * FROM gov_audit_reports WHERE plan_id=? AND key_fingerprint=?"
                " ORDER BY created_at",
                (plan_id, key_fp),
            )
            responses = self._rows(conn, "SELECT * FROM gov_audit_responses")
            waivers = self._rows(
                conn, "SELECT * FROM gov_waivers WHERE key_fingerprint=?", (key_fp,)
            )
        policy = self.audit_policy(plan_id)
        problems: list[str] = []
        blocking_open: list[str] = []
        report_id = reports[-1]["report_id"] if reports else None
        independence = reports[-1]["independence"] if reports else None

        if policy["mode"] == "off":
            problems.append(
                "审计政策是 off，这一版没有审计依据；不代表审过"
                f"（政策来源 {policy['source']}，证据等级 {policy['independence']}）"
            )
        elif not reports:
            problems.append(
                f"当前版本没有审计：key_fingerprint={key_fp[:12]}…；"
                "先 prepare_audit + record_audit"
            )
        else:
            latest = reports[-1]
            payload = json.loads(latest["report_json"])
            resolved: set[tuple[str, str]] = set()
            for item in payload.get("resolves", []) or []:
                if isinstance(item, dict):
                    resolved.add((str(item.get("report_id")), str(item.get("issue_id"))))
            waived = {(row["report_id"], row["issue_id"]) for row in waivers}
            handled = {(row["report_id"], row["issue_id"]): row for row in responses}
            for issue in payload.get("issues", []) or []:
                if not isinstance(issue, dict):
                    continue
                issue_id = str(issue.get("id"))
                key = (latest["report_id"], issue_id)
                response = handled.get(key)
                is_waived = key in waived
                if response and response["disposition"] == "declined":
                    problems.append(f"意见 {issue_id} 被 declined，但没有关闭依据（declined 不等于关掉）")
                if response and response["disposition"] == "needs_user":
                    problems.append(f"意见 {issue_id} 待用户决定（needs_user）")
                # 处置或人工豁免都算「处理过」：豁免本身就是用户对这条意见的决定。
                if not response and not is_waived:
                    problems.append(f"意见 {issue_id} 还没有处置")
                if issue.get("blocking") is True:
                    closed = key in resolved or is_waived
                    if not closed:
                        blocking_open.append(issue_id)
            if blocking_open:
                problems.append(f"阻断项未关：{blocking_open}")
            if self.policy.get("require_verified_independence") \
                    and latest["independence"] != INDEPENDENCE_VERIFIED:
                problems.append(
                    "审计独立性只有 declared（没有 verifier 核过的宿主证据），"
                    "而政策要求 verified"
                )
        return {
            "ok": not problems,
            "problems": problems,
            "blocking_open": blocking_open,
            "audits_used": len(audits),
            "limit": int(self.policy["max_audits"]),
            "report_id": report_id,
            "independence": independence,
            "key_fingerprint": key_fp,
            "policy": policy,
        }

    # ------------------------------------------------------------------
    # 专项意图与节点监控
    # ------------------------------------------------------------------

    def sync_user_update(self, *, source_session: str, seq: int, quote: str) -> dict:
        """登记一条**真实到达过**的用户原话（来自会话记录的高水位同步）。"""
        self._require_text(source_session, "source_session")
        self._require_text(quote, "quote")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq <= 0:
            raise GovernanceError(f"seq 必须是正整数，实际 {seq!r}")
        with self.store.transaction() as conn:
            existing = self._row(
                conn,
                "SELECT id FROM gov_user_updates WHERE source_session=? AND seq=?",
                (source_session, seq),
            )
            if existing:
                return {"source_session": source_session, "seq": seq, "created": False}
            conn.execute(
                "INSERT INTO gov_user_updates(source_session,seq,quote,created_at)"
                " VALUES(?,?,?,?)",
                (source_session, seq, quote, self._now()),
            )
            self._emit(conn, "governance_user_update", "sync",
                       {"source_session": source_session, "seq": seq})
        return {"source_session": source_session, "seq": seq, "created": True}

    def create_intent(self, intent_id: str, *, quote: str, source_session: str, seq: int,
                      actor: str, acceptance: str = "") -> dict:
        """建一条专项意图。**必须指到一句用户原话**（会话号 + seq + 逐字引文）。

        判据（原契约 §2）：有**具体验收对象 + 持续要求**才算专项意图。
        本方法不替你判断语义，只强制「来源可追」；判断错了在这里看不出来。
        """
        self._require_text(intent_id, "intent_id")
        self._require_text(quote, "quote")
        self._require_text(source_session, "source_session")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq <= 0:
            raise GovernanceError("seq 必须是正整数：专项意图必须能指到一条真实用户原话")
        with self.store.transaction() as conn:
            existing = self._row(
                conn, "SELECT state FROM gov_intents WHERE intent_id=?", (intent_id,)
            )
            if existing:
                raise GovernanceError(
                    f"意图 {intent_id!r} 已存在（state={existing['state']}）；"
                    "旧意图不复活，要新开就换 id"
                )
            now = self._now()
            conn.execute(
                "INSERT INTO gov_intents(intent_id,state,quote,source_session,source_seq,"
                "actor,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (intent_id, "active", quote, source_session, seq, actor, now, now),
            )
            conn.execute(
                "INSERT INTO gov_intent_events(intent_id,kind,detail_json,actor,created_at)"
                " VALUES(?,?,?,?,?)",
                (intent_id, "intent_created",
                 canonical_json({"quote": quote, "source_session": source_session,
                                 "seq": seq, "acceptance": acceptance, "actor": actor}),
                 actor, now),
            )
            self._emit(conn, "governance_intent", actor,
                       {"intent_id": intent_id, "state": "active", "seq": seq})
        return {"intent_id": intent_id, "state": "active", "source_seq": seq}

    def assess_update(self, *, source_session: str, covers_seq: int, decision: str,
                      quote: str, actor: str, answer_seq: int | None = None,
                      intent_id: str | None = None) -> dict:
        """登记一次**对用户原话的显式判断**（`intent` 或 `none`）。

        判 `none` 时必须给 `answer_seq` —— 指向用户**回答那句**消息，且必须晚于
        被评估的那句（原契约 §2 的「先问用户」：主会话自己的技术判断不算数）。
        """
        self._require_text(source_session, "source_session")
        self._require_text(quote, "quote")
        self._require_text(actor, "actor")
        if decision not in ("intent", "none"):
            raise GovernanceError("decision 必须是 intent / none")
        if not isinstance(covers_seq, int) or isinstance(covers_seq, bool) or covers_seq <= 0:
            raise GovernanceError("covers_seq 必须是正整数")
        if decision == "none":
            if not isinstance(answer_seq, int) or isinstance(answer_seq, bool):
                raise GovernanceError(
                    "判 none 必须给 answer_seq：要有用户对「这句要不要立为意图」的回答"
                )
            if answer_seq <= covers_seq:
                raise GovernanceError(
                    "answer_seq 必须晚于被评估的那句（cover_seq < answer_seq）"
                )
        if decision == "intent":
            self._require_text(intent_id, "intent_id")
            with self.store.transaction() as conn:
                row = self._row(conn, "SELECT state FROM gov_intents WHERE intent_id=?",
                                (intent_id,))
            if row is None:
                raise GovernanceError(f"意图 {intent_id!r} 不存在：先 create_intent()")
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO gov_intent_assessments(source_session,covers_seq,decision,quote,"
                "intent_id,answer_seq,actor,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (source_session, covers_seq, decision, quote, intent_id, answer_seq,
                 actor, self._now()),
            )
            self._emit(conn, "governance_assess", actor,
                       {"source_session": source_session, "covers_seq": covers_seq,
                        "decision": decision, "intent_id": intent_id})
        return {"source_session": source_session, "covers_seq": covers_seq,
                "decision": decision, "answer_seq": answer_seq}

    def register_trigger(self, intent_id: str, kind: str, detail: str, *, actor: str,
                         trigger_id: str | None = None) -> dict:
        """登记一个监控触发点。

        三类：`result_analysis`（结果分析之后、下一轮方向之前）·
        `user_update`（用户补充/纠正）· `material_deviation`（跑了别的包/读数异常）。
        ⛔ 普通心跳不触发：这里没有 heartbeat 这一类。
        """
        self._require_text(intent_id, "intent_id")
        self._require_text(detail, "detail")
        self._require_text(actor, "actor")
        if kind not in INTENT_TRIGGERS:
            raise GovernanceError(
                f"kind 必须是 {'/'.join(INTENT_TRIGGERS)}；普通心跳不构成触发"
            )
        with self.store.transaction() as conn:
            intent = self._row(conn, "SELECT state FROM gov_intents WHERE intent_id=?",
                               (intent_id,))
            if intent is None:
                raise GovernanceError(f"意图 {intent_id!r} 不存在")
            if intent["state"] == "closed":
                raise GovernanceError("意图已 closed：不再产生新触发")
            trigger_id = trigger_id or "TRG-" + _digest(
                [intent_id, kind, detail, self._now()]
            )[:12]
            existing = self._row(
                conn, "SELECT trigger_id FROM gov_intent_triggers WHERE trigger_id=?",
                (trigger_id,),
            )
            if existing:
                return {"trigger_id": trigger_id, "created": False}
            conn.execute(
                "INSERT INTO gov_intent_triggers(trigger_id,intent_id,kind,detail,covered_by,"
                "actor,created_at) VALUES(?,?,?,?,NULL,?,?)",
                (trigger_id, intent_id, kind, detail, actor, self._now()),
            )
            self._emit(conn, "governance_trigger", actor,
                       {"trigger_id": trigger_id, "intent_id": intent_id, "kind": kind})
        return {"trigger_id": trigger_id, "intent_id": intent_id, "kind": kind,
                "created": True}

    def record_monitor_report(self, intent_id: str, report: dict, *, actor: str,
                              evidence=None) -> dict:
        """登记一份独立监控报告，并**覆盖**它声明的触发点。

        报告必须给：`covers.trigger_ids`（覆盖哪些触发）· `covers.user_seq`
        （读到哪条用户原话）· `verdict`（`on_track`/`drifting`/`blocked`/
        `achieved`/`unachievable`）· `basis`（凭什么这么判）。
        """
        self._require_text(intent_id, "intent_id")
        self._require_text(actor, "actor")
        if not isinstance(report, dict):
            raise GovernanceError("report 必须是 dict")
        covers = report.get("covers")
        if not isinstance(covers, dict):
            raise GovernanceError("report.covers 必须是对象（trigger_ids + user_seq）")
        trigger_ids = covers.get("trigger_ids")
        if not isinstance(trigger_ids, list) or not trigger_ids:
            raise GovernanceError("covers.trigger_ids 必须是非空数组（覆盖了哪些触发）")
        user_seq = covers.get("user_seq")
        if not isinstance(user_seq, int) or isinstance(user_seq, bool) or user_seq < 0:
            raise GovernanceError("covers.user_seq 必须是 ≥0 的整数（读到哪条用户原话）")
        verdict = report.get("verdict")
        if verdict not in MONITOR_VERDICTS:
            raise GovernanceError(f"verdict 必须是 {'/'.join(MONITOR_VERDICTS)}")
        if not _text(report.get("basis")):
            raise GovernanceError("basis 必须非空：要指到原始结果/规则，不是「看起来没问题」")
        independence, verified = self._verify("monitor_independence", evidence)
        with self.store.transaction() as conn:
            intent = self._row(conn, "SELECT * FROM gov_intents WHERE intent_id=?",
                               (intent_id,))
            if intent is None:
                raise GovernanceError(f"意图 {intent_id!r} 不存在")
            open_triggers = [
                row["trigger_id"] for row in self._rows(
                    conn,
                    "SELECT trigger_id FROM gov_intent_triggers WHERE intent_id=?"
                    " AND covered_by IS NULL",
                    (intent_id,),
                )
            ]
            missing = sorted(set(open_triggers) - set(trigger_ids))
            if missing:
                raise GovernanceError(
                    f"这份报告没有覆盖未处理的触发点 {missing}；"
                    "没覆盖到的触发 = 没处理"
                )
            unknown = sorted(set(trigger_ids) - set(open_triggers)
                             - {row["trigger_id"] for row in self._rows(
                                 conn,
                                 "SELECT trigger_id FROM gov_intent_triggers WHERE intent_id=?",
                                 (intent_id,))})
            if unknown:
                raise GovernanceError(f"trigger_ids 里有不存在的触发：{unknown}")
            report_id = "MON-" + _digest([intent_id, report, actor])[:12]
            conn.execute(
                "INSERT INTO gov_intent_reports(report_id,intent_id,covers_json,user_seq,"
                "verdict,basis,independence,evidence_json,actor,created_at,revoked)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,0)",
                (report_id, intent_id, canonical_json(covers), user_seq, verdict,
                 report["basis"], independence, canonical_json(verified), actor, self._now()),
            )
            for trigger_id in trigger_ids:
                conn.execute(
                    "UPDATE gov_intent_triggers SET covered_by=? WHERE trigger_id=?",
                    (report_id, trigger_id),
                )
            self._emit(conn, "governance_monitor_report", actor,
                       {"report_id": report_id, "intent_id": intent_id,
                        "verdict": verdict, "independence": independence})
        return {"report_id": report_id, "intent_id": intent_id,
                "verdict": verdict, "independence": independence,
                "covered": list(trigger_ids)}

    def block_intent(self, intent_id: str, *, reason: str, actor: str) -> dict:
        """暂时受阻 ⇒ `blocked`。**受阻不是失败**：失败继续 active。"""
        self._require_text(reason, "reason")
        return self._set_intent_state(intent_id, "blocked", actor=actor,
                                      detail={"reason": reason}, kind="intent_blocked")

    def unblock_intent(self, intent_id: str, *, actor: str) -> dict:
        return self._set_intent_state(intent_id, "active", actor=actor,
                                      detail={}, kind="intent_unblocked")

    def _set_intent_state(self, intent_id: str, state: str, *, actor: str,
                          detail: dict, kind: str) -> dict:
        self._require_text(intent_id, "intent_id")
        self._require_text(actor, "actor")
        with self.store.transaction() as conn:
            row = self._row(conn, "SELECT * FROM gov_intents WHERE intent_id=?",
                            (intent_id,))
            if row is None:
                raise GovernanceError(f"意图 {intent_id!r} 不存在")
            if row["state"] == "closed":
                raise GovernanceError("意图已 closed：不复活，新开就换 id")
            if row["state"] == state:
                return {"intent_id": intent_id, "state": state, "changed": False}
            conn.execute(
                "UPDATE gov_intents SET state=?,updated_at=? WHERE intent_id=?",
                (state, self._now(), intent_id),
            )
            conn.execute(
                "INSERT INTO gov_intent_events(intent_id,kind,detail_json,actor,created_at)"
                " VALUES(?,?,?,?,?)",
                (intent_id, kind, canonical_json(detail), actor, self._now()),
            )
            self._emit(conn, "governance_intent_state", actor,
                       {"intent_id": intent_id, "state": state})
        return {"intent_id": intent_id, "state": state, "changed": True}

    def close_intent(self, intent_id: str, *, reason: str, evidence, report_id: str,
                     actor: str, replacement_intent_id: str | None = None) -> dict:
        """闭合一条意图。**四条理由之外的闭合一律拒绝。**

        四条：`achieved`（达成）· `withdrawn`（用户撤回）· `replaced`（用户替换）·
        `unsatisfiable`（当前条件下做不到）。

        硬要求（缺一条就拒）：
        * `report_id` 必须是该意图的一份**仍 current** 的独立监控报告（覆盖全部触发、
          读到最新用户原话）；
        * `evidence` 非空，且撤回/替换要带用户原话（`quote`+`session`+`seq`）；
        * `unsatisfiable` 必须由报告 `verdict="unachievable"` 支撑；
        明确不许：单轮失败、主 agent 说「没路了」、超时、监控觉得没得盯。
        """
        self._require_text(intent_id, "intent_id")
        self._require_text(actor, "actor")
        if reason not in INTENT_REASONS:
            raise GovernanceError(
                f"闭合理由必须是 {'/'.join(INTENT_REASONS)}；单轮失败/自述没路/超时都不许当理由"
            )
        if not evidence:
            raise GovernanceError("闭合必须带证据：达成要指到原始结果，撤回/替换要指到用户原话")
        with self.store.transaction() as conn:
            row = self._row(conn, "SELECT * FROM gov_intents WHERE intent_id=?",
                            (intent_id,))
            if row is None:
                raise GovernanceError(f"意图 {intent_id!r} 不存在")
            if row["state"] == "closed":
                raise GovernanceError("意图已 closed")
            report = self._row(conn, "SELECT * FROM gov_intent_reports WHERE report_id=?",
                               (report_id,))
            if report is None or report["intent_id"] != intent_id:
                raise GovernanceError("闭合必须引用这个意图自己的一份监控报告")
            if report["revoked"]:
                raise GovernanceError("引用的监控报告已过期（revoked）⇒ 不能作为闭合依据")
            open_triggers = self._rows(
                conn,
                "SELECT trigger_id FROM gov_intent_triggers WHERE intent_id=?"
                " AND covered_by IS NULL",
                (intent_id,),
            )
            if open_triggers:
                raise GovernanceError(
                    f"还有未处理的触发点 {[r['trigger_id'] for r in open_triggers]}；"
                    "闭合前必须由仍 current 的报告覆盖"
                )
            covers = json.loads(report["covers_json"])
            trigger_ids = covers.get("trigger_ids") or []
            if not trigger_ids:
                raise GovernanceError("报告没有覆盖任何触发点 ⇒ 不能当最后一次独立检查")
            if reason == "unsatisfiable" and report["verdict"] != "unachievable":
                raise GovernanceError(
                    "unsatisfiable 必须由监控报告判 unachievable 支撑；"
                    "主 agent 自述「走不通」不算"
                )
            if reason in ("withdrawn", "replaced"):
                if not isinstance(evidence, dict) or not _text(evidence.get("quote")) \
                        or not _text(evidence.get("session")) or not isinstance(
                            evidence.get("seq"), int):
                    raise GovernanceError(
                        "撤回/替换必须带用户原话（quote + session + seq）；「用户说不用了」不算来源"
                    )
            if reason == "replaced":
                self._require_text(replacement_intent_id, "replacement_intent_id")
                target = self._row(conn, "SELECT state FROM gov_intents WHERE intent_id=?",
                                   (replacement_intent_id,))
                if target is None or target["state"] != "active":
                    raise GovernanceError("替换目标必须是一条现存的 active 意图")
            conn.execute(
                "UPDATE gov_intents SET state=?,updated_at=? WHERE intent_id=?",
                ("closed", self._now(), intent_id),
            )
            conn.execute(
                "INSERT INTO gov_intent_events(intent_id,kind,detail_json,actor,created_at)"
                " VALUES(?,?,?,?,?)",
                (intent_id, "intent_closed",
                 canonical_json({"reason": reason, "evidence": evidence,
                                 "report_id": report_id,
                                 "replacement": replacement_intent_id}), actor, self._now()),
            )
            self._emit(conn, "governance_intent_closed", actor,
                       {"intent_id": intent_id, "reason": reason,
                        "report_id": report_id})
        return {"intent_id": intent_id, "state": "closed", "reason": reason,
                "report_id": report_id}

    def intent_state(self, intent_id: str) -> dict:
        """只读：这条意图现在缺什么才算被盯住了。"""
        with self.store.transaction() as conn:
            row = self._row(conn, "SELECT * FROM gov_intents WHERE intent_id=?",
                            (intent_id,))
            if row is None:
                return {"intent_id": intent_id, "exists": False,
                        "problems": ["意图不存在"]}
            triggers = self._rows(
                conn,
                "SELECT * FROM gov_intent_triggers WHERE intent_id=? AND covered_by IS NULL",
                (intent_id,),
            )
            reports = self._rows(
                conn,
                "SELECT * FROM gov_intent_reports WHERE intent_id=? AND revoked=0"
                " ORDER BY created_at DESC",
                (intent_id,),
            )
            updates = self._rows(
                conn,
                "SELECT * FROM gov_user_updates WHERE source_session=? ORDER BY seq",
                (row["source_session"],),
            )
            assessments = self._rows(
                conn,
                "SELECT * FROM gov_intent_assessments WHERE source_session=?"
                " ORDER BY covers_seq",
                (row["source_session"],),
            )
        latest = reports[0] if reports else None
        problems: list[str] = []
        if triggers:
            problems.append(f"{len(triggers)} 个触发点未被覆盖："
                            f"{[t['trigger_id'] for t in triggers]}")
        if latest is None:
            problems.append("还没有一份仍 current 的监控报告")
        else:
            covers = json.loads(latest["covers_json"])
            assessed_seq = max([a["covers_seq"] for a in assessments], default=0)
            if updates:
                top = max(u["seq"] for u in updates)
                if top > int(covers.get("user_seq", 0)):
                    problems.append(
                        f"有更新的用户原话未被这份报告读到（seq {int(covers.get('user_seq', 0))}"
                        f" < {top}）⇒ 报告过期"
                    )
                if top > assessed_seq and row["state"] in ("none", "closed"):
                    problems.append(
                        f"有已同步未评估的用户原话（评估水位 {assessed_seq} < {top}）"
                    )
        return {
            "intent_id": intent_id,
            "exists": True,
            "state": row["state"],
            "quote": row["quote"],
            "source_session": row["source_session"],
            "source_seq": row["source_seq"],
            "open_triggers": [t["trigger_id"] for t in triggers],
            "latest_report": None if latest is None else {
                "report_id": latest["report_id"],
                "verdict": latest["verdict"],
                "independence": latest["independence"],
                "user_seq": latest["user_seq"],
            },
            "problems": problems,
        }

    def check_start(self, *, intent_id: str | None = None, source_session: str | None = None) -> dict:
        """进入下一轮之前的一次检查。**只读**。

        返回 `{allow, problems, diagnostics}`。`allow=False` ⇒ 不放行。

        边界（写进 docs，别读大）：这一层只能判「登记过的账有没有缺口」。
        语义识别（什么算专项意图、什么算执行偏差）与绕过入口，仍靠主会话。
        """
        problems: list[str] = []
        diagnostics: list[str] = []
        if intent_id is None:
            diagnostics.append("没有专项意图 ⇒ 不派监控（这是正常状态，不是缺口）")
            if self.policy.get("block_unassessed_without_intent") and source_session:
                with self.store.transaction() as conn:
                    updates = self._rows(
                        conn,
                        "SELECT MAX(seq) AS top FROM gov_user_updates WHERE source_session=?",
                        (source_session,),
                    )
                    assessed = self._rows(
                        conn,
                        "SELECT MAX(covers_seq) AS top FROM gov_intent_assessments"
                        " WHERE source_session=?",
                        (source_session,),
                    )
                top = updates[0]["top"] or 0
                done = assessed[0]["top"] or 0
                if top > done:
                    problems.append(
                        f"有已同步未评估的用户原话（评估水位 {done} < {top}）⇒ 先 assess_update"
                    )
            return {"allow": not problems, "problems": problems,
                    "diagnostics": diagnostics}

        with self.store.transaction() as conn:
            row = self._row(conn, "SELECT * FROM gov_intents WHERE intent_id=?",
                            (intent_id,))
        if row is None:
            return {"allow": False, "problems": [f"意图 {intent_id!r} 不存在"],
                    "diagnostics": diagnostics}
        if row["state"] == "closed":
            diagnostics.append("意图已 closed ⇒ 不派监控")
            return {"allow": True, "problems": problems, "diagnostics": diagnostics}
        state = self.intent_state(intent_id)
        problems.extend(state["problems"])
        if row["state"] == "blocked":
            problems.append("意图处于 blocked：阻碍解除前不放行新方向（blocked ≠ closed）")
        return {"allow": not problems, "problems": problems,
                "diagnostics": diagnostics}

    # ------------------------------------------------------------------
    # 经验候选：四种处置 + 修订使旧确认失效
    # ------------------------------------------------------------------

    def add_lesson_candidate(self, candidate: dict, *, actor: str) -> dict:
        """新增一条经验候选（REV=1，status=pending）。

        必填：`candidate_id` · `title` · `method`（**这一行被确认绑定**）·
        `premise` · `applicable_when` · `basis` · `boundaries` · `evidence_level`。
        """
        self._require_text(actor, "actor")
        if not isinstance(candidate, dict):
            raise GovernanceError("candidate 必须是 dict")
        errors = []
        for key in ("candidate_id", "title", "method", "premise", "applicable_when",
                    "basis", "boundaries", "evidence_level"):
            if not _text(candidate.get(key)):
                errors.append(f"candidate.{key} 必须非空")
        if candidate.get("evidence_level") not in ("unverified", "supported"):
            errors.append("candidate.evidence_level 必须是 unverified / supported")
        if errors:
            raise GovernanceError("经验候选不合规：" + "；".join(errors), errors)
        candidate_id = candidate["candidate_id"]
        with self.store.transaction() as conn:
            existing = self._row(
                conn, "SELECT rev FROM gov_lessons WHERE candidate_id=?"
                " ORDER BY rev DESC LIMIT 1", (candidate_id,)
            )
            if existing:
                raise GovernanceError(
                    f"候选 {candidate_id!r} 已存在（REV {existing['rev']}）；"
                    "改条目请用 revise_lesson() 升 REV，不许重写"
                )
            method_hash = hashlib.sha256(candidate["method"].strip().encode("utf-8")).hexdigest()
            conn.execute(
                "INSERT INTO gov_lessons(candidate_id,rev,title,method,method_hash,body_json,"
                "actor,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (candidate_id, 1, candidate["title"], candidate["method"], method_hash,
                 canonical_json(candidate), actor, self._now()),
            )
            self._emit(conn, "governance_lesson_candidate", actor,
                       {"candidate_id": candidate_id, "rev": 1})
        return {"candidate_id": candidate_id, "rev": 1,
                "method_hash": method_hash, "status": "pending"}

    def revise_lesson(self, candidate_id: str, changes: dict, *, actor: str) -> dict:
        """改一条候选：**REV +1**，并把 status 打回 pending。

        ⚠️ 这是本层的核心防线：**旧确认只对它确认过的那一版成立。**
        改了任何正文 ⇒ REV 变 ⇒ 记录里的 `CONFIRMED_REV` 失配 ⇒ 旧确认自动失效。
        """
        self._require_text(actor, "actor")
        if not isinstance(changes, dict) or not changes:
            raise GovernanceError("changes 必须是非空 dict")
        with self.store.transaction() as conn:
            latest = self._row(
                conn, "SELECT * FROM gov_lessons WHERE candidate_id=?"
                " ORDER BY rev DESC LIMIT 1", (candidate_id,)
            )
            if latest is None:
                raise GovernanceError(f"候选 {candidate_id!r} 不存在")
            body = json.loads(latest["body_json"])
            body.update(changes)
            body["candidate_id"] = candidate_id
            for key in ("title", "method", "premise", "applicable_when", "basis",
                        "boundaries", "evidence_level"):
                if not _text(body.get(key)):
                    raise GovernanceError(f"修订后 candidate.{key} 不能为空")
            rev = latest["rev"] + 1
            method_hash = hashlib.sha256(body["method"].strip().encode("utf-8")).hexdigest()
            conn.execute(
                "INSERT INTO gov_lessons(candidate_id,rev,title,method,method_hash,body_json,"
                "actor,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (candidate_id, rev, body["title"], body["method"], method_hash,
                 canonical_json(body), actor, self._now()),
            )
            self._emit(conn, "governance_lesson_revised", actor,
                       {"candidate_id": candidate_id, "rev": rev})
        return {"candidate_id": candidate_id, "rev": rev, "method_hash": method_hash,
                "status": "pending", "invalidates_previous_confirmation": True}

    def confirm_lesson(self, candidate_id: str, *, rev: int, option: str, quote: str,
                       source: str, actor: str, source_session: str | None = None,
                       source_seq: int | None = None, evidence=None) -> dict:
        """登记人的逐条确认。四选一：`keep` / `keep-edited` / `keep-pending` / `drop`。

        两项绑定必须同时对上：`rev` 与 `method_hash`（由当前 REV 的 method 现算）。
        对不上 ⇒ 拒绝（那份确认是对旧版本说的）。
        来源必须写明，且**原话逐字**；没有原话不算确认。
        """
        self._require_text(actor, "actor")
        self._require_text(quote, "quote")
        self._require_text(source, "source")
        if option not in LESSON_OPTIONS:
            raise GovernanceError(f"option 必须是 {'/'.join(LESSON_OPTIONS)}")
        independence, verified = self._verify("lesson_confirmation", evidence)
        with self.store.transaction() as conn:
            row = self._row(
                conn, "SELECT * FROM gov_lessons WHERE candidate_id=? AND rev=?",
                (candidate_id, rev),
            )
            if row is None:
                latest = self._row(
                    conn, "SELECT rev FROM gov_lessons WHERE candidate_id=?"
                    " ORDER BY rev DESC LIMIT 1", (candidate_id,)
                )
                raise GovernanceError(
                    f"没有 REV {rev}（当前最新 REV {latest['rev'] if latest else None}）⇒"
                    "确认必须指向一个真实存在的版本"
                )
            latest = self._row(
                conn, "SELECT rev, method_hash FROM gov_lessons WHERE candidate_id=?"
                " ORDER BY rev DESC LIMIT 1", (candidate_id,)
            )
            if latest["rev"] != rev:
                raise GovernanceError(
                    f"候选已改到 REV {latest['rev']}，你确认的是 REV {rev} ⇒ 旧确认对新版本不成立，"
                    "请对最新 REV 重新确认"
                )
            conn.execute(
                "INSERT INTO gov_lesson_confirmations(candidate_id,rev,method_hash,option,quote,"
                "source,source_session,source_seq,independence,actor,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (candidate_id, rev, row["method_hash"], option, quote, source,
                 source_session, source_seq, independence, actor, self._now()),
            )
            self._emit(conn, "governance_lesson_confirmed", actor,
                       {"candidate_id": candidate_id, "rev": rev, "option": option,
                        "independence": independence})
        return {"candidate_id": candidate_id, "rev": rev, "option": option,
                "status": LESSON_STATUS[option], "independence": independence}

    def lesson_state(self, candidate_id: str) -> dict:
        """只读：当前 REV、生效的确认、以及**已失效**的旧确认有哪些。"""
        with self.store.transaction() as conn:
            latest = self._row(
                conn, "SELECT * FROM gov_lessons WHERE candidate_id=?"
                " ORDER BY rev DESC LIMIT 1", (candidate_id,)
            )
            if latest is None:
                return {"candidate_id": candidate_id, "exists": False,
                        "status": "missing", "problems": ["候选不存在"]}
            confirmations = self._rows(
                conn,
                "SELECT * FROM gov_lesson_confirmations WHERE candidate_id=? ORDER BY id",
                (candidate_id,),
            )
        valid = [c for c in confirmations
                 if c["rev"] == latest["rev"] and c["method_hash"] == latest["method_hash"]]
        stale = [{"rev": c["rev"], "option": c["option"],
                  "why": "REV 或 METHOD 与当前版本不一致"} for c in confirmations
                 if c not in valid]
        status = "pending" if not valid else LESSON_STATUS[valid[-1]["option"]]
        problems = []
        if stale and status == "pending":
            problems.append(f"{len(stale)} 条旧确认已因修订失效 ⇒ 必须重新确认")
        return {
            "candidate_id": candidate_id,
            "exists": True,
            "rev": latest["rev"],
            "method_hash": latest["method_hash"],
            "status": status,
            "confirmation": None if not valid else {
                "rev": valid[-1]["rev"], "option": valid[-1]["option"],
                "independence": valid[-1]["independence"],
                "quote": valid[-1]["quote"], "source": valid[-1]["source"],
            },
            "stale_confirmations": stale,
            "problems": problems,
        }

    def lessons(self, *, status: str | None = None) -> list:
        """列出候选（默认全部）。`status=confirmed` 只给可当依据的条目。"""
        with self.store.transaction() as conn:
            ids = [row["candidate_id"] for row in self._rows(
                conn, "SELECT DISTINCT candidate_id FROM gov_lessons ORDER BY candidate_id")]
        out = []
        for candidate_id in ids:
            state = self.lesson_state(candidate_id)
            if status and state["status"] != status:
                continue
            out.append(state)
        return out

    # ------------------------------------------------------------------
    # 角色包与委派
    # ------------------------------------------------------------------

    def register_rolepack(self, role: str, *, path: str, sha256: str, actor: str) -> dict:
        """登记一份角色提示词的内容哈希。派工时按它核「跑的提示词是哪一版」。"""
        self._require_text(actor, "actor")
        if role not in ROLE_NAMES:
            raise GovernanceError(f"role 必须是 {', '.join(ROLE_NAMES)} 之一")
        self._require_text(path, "path")
        self._require_sha(sha256, "sha256")
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO gov_roles(role,path,sha256,actor,created_at) VALUES(?,?,?,?,?)",
                (role, path, sha256, actor, self._now()),
            )
            self._emit(conn, "governance_rolepack", actor,
                       {"role": role, "sha256": sha256})
        return {"role": role, "sha256": sha256}

    def record_delegation(self, *, child_session: str, role: str, parent_session: str,
                          rolepack_sha: str, prompt_sha: str, actor: str,
                          evidence=None) -> dict:
        """登记一次委派。`rolepack_sha` 必须等于该角色最近一次登记的值。

        独立性只由 verifier 提供；没有 verifier ⇒ `declared`（表示「本地自述派了」）。
        """
        self._require_text(child_session, "child_session")
        self._require_text(parent_session, "parent_session")
        self._require_text(actor, "actor")
        if role not in ROLE_NAMES:
            raise GovernanceError(f"role 必须是 {', '.join(ROLE_NAMES)} 之一")
        self._require_sha(rolepack_sha, "rolepack_sha")
        self._require_sha(prompt_sha, "prompt_sha")
        if child_session == parent_session:
            raise GovernanceError("child_session 不能等于 parent_session")
        independence, verified = self._verify("delegation", evidence)
        with self.store.transaction() as conn:
            latest = self._row(
                conn, "SELECT sha256 FROM gov_roles WHERE role=? ORDER BY id DESC LIMIT 1",
                (role,),
            )
            if latest is None:
                raise GovernanceError(f"角色 {role!r} 还没有登记过角色包：先 register_rolepack()")
            if latest["sha256"] != rolepack_sha:
                raise GovernanceError(
                    f"rolepack_sha 与登记的 {role} 角色包不一致（登记 {latest['sha256'][:12]}…，"
                    f"收到 {rolepack_sha[:12]}…）⇒ 派的可能不是这一版提示词"
                )
            conn.execute(
                "INSERT INTO gov_delegations(child_session,role,rolepack_sha,prompt_sha,"
                "parent_session,independence,evidence_json,actor,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (child_session, role, rolepack_sha, prompt_sha, parent_session,
                 independence, canonical_json(verified), actor, self._now()),
            )
            self._emit(conn, "governance_delegation", actor,
                       {"child_session": child_session, "role": role,
                        "independence": independence})
        return {"child_session": child_session, "role": role,
                "independence": independence}

    # ------------------------------------------------------------------
    # 必读台账（只记 declared / verified，不判断「读过」）
    # ------------------------------------------------------------------

    def record_read(self, *, path: str, sha256: str, role: str, session: str,
                    evidence=None) -> dict:
        """登记一次「读了某份必读件」。

        ⚠️ 默认只能记 `declared`：**本地自述读到 ≠ 宿主证明读过**。
        要 `verified`，必须注入能读宿主 read 事件的 verifier。
        """
        self._require_text(path, "path")
        self._require_text(role, "role")
        self._require_text(session, "session")
        self._require_sha(sha256, "sha256")
        independence, verified = self._verify("read", evidence)
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO gov_reads(path,sha256,role,session,independence,evidence_json,"
                "created_at) VALUES(?,?,?,?,?,?,?)",
                (path, sha256, role, session, independence, canonical_json(verified),
                 self._now()),
            )
            self._emit(conn, "governance_read", session,
                       {"path": path, "role": role, "independence": independence})
        return {"path": path, "role": role, "independence": independence}

    def reading_state(self, *, role: str | None = None) -> dict:
        with self.store.transaction() as conn:
            if role:
                rows = self._rows(conn, "SELECT * FROM gov_reads WHERE role=?", (role,))
            else:
                rows = self._rows(conn, "SELECT * FROM gov_reads")
        return {
            "n_reads": len(rows),
            "by_independence": {
                "declared": sum(1 for row in rows if row["independence"] == INDEPENDENCE_DECLARED),
                "verified": sum(1 for row in rows if row["independence"] == INDEPENDENCE_VERIFIED),
            },
            "note": "declared 只表示自述读过；不是宿主证明",
        }

    # ------------------------------------------------------------------
    # 文档维护：热件上限与冷件滚动
    # ------------------------------------------------------------------

    def register_doc(self, doc_id: str, *, tier: str, path: str, bytes_: int, sha256: str,
                     limit_bytes: int | None = None, actor: str) -> dict:
        """登记一份文档的当前版本。`hot` 必须给上限（到线就滚）。"""
        self._require_text(doc_id, "doc_id")
        self._require_text(path, "path")
        self._require_text(actor, "actor")
        self._require_sha(sha256, "sha256")
        if tier not in DOC_TIERS:
            raise GovernanceError(f"tier 必须是 {'/'.join(DOC_TIERS)}")
        if not isinstance(bytes_, int) or isinstance(bytes_, bool) or bytes_ < 0:
            raise GovernanceError("bytes_ 必须是 ≥0 的整数")
        if tier == "hot" and not _positive_int(limit_bytes):
            raise GovernanceError("hot 文档必须给 limit_bytes（热件没有上限就必然漂）")
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO gov_docs(doc_id,tier,path,bytes,sha256,limit_bytes,actor,created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (doc_id, tier, path, bytes_, sha256, limit_bytes, actor, self._now()),
            )
            self._emit(conn, "governance_doc", actor,
                       {"doc_id": doc_id, "tier": tier, "bytes": bytes_})
        return {"doc_id": doc_id, "tier": tier, "bytes": bytes_, "over_limit":
                tier == "hot" and bytes_ > int(limit_bytes)}

    def roll_cold(self, doc_id: str, *, cold_path: str, cold_sha: str, index_path: str,
                  hot_sha_before: str, hot_bytes_before: int, actor: str) -> dict:
        """把已结案的部分滚进冷件。

        三条硬要求：① 冷件是**搬运**不是删除，必须给冷件路径与哈希；
        ② 必须给**索引**路径（没有索引的冷件等于丢失）；
        ③ `hot_sha_before` 必须等于最近一次登记的 hot 版本哈希 ——
        否则说明算哈希之后又有人写过，滚动的基线不对。
        """
        self._require_text(doc_id, "doc_id")
        self._require_text(cold_path, "cold_path")
        self._require_text(index_path, "index_path")
        self._require_text(actor, "actor")
        self._require_sha(cold_sha, "cold_sha")
        self._require_sha(hot_sha_before, "hot_sha_before")
        if not isinstance(hot_bytes_before, int) or isinstance(hot_bytes_before, bool) \
                or hot_bytes_before < 0:
            raise GovernanceError("hot_bytes_before 必须是 ≥0 的整数")
        with self.store.transaction() as conn:
            latest = self._row(
                conn, "SELECT * FROM gov_docs WHERE doc_id=? ORDER BY id DESC LIMIT 1",
                (doc_id,),
            )
            if latest is None:
                raise GovernanceError(f"文档 {doc_id!r} 没有登记过")
            if latest["tier"] != "hot":
                raise GovernanceError("只有 hot 文档需要滚动")
            if latest["sha256"] != hot_sha_before:
                raise GovernanceError(
                    "hot_sha_before 与最近登记的哈希不一致 ⇒ 算哈希之后文件又变过，"
                    "滚动基线不对（先重读、重新登记，再滚）"
                )
            if cold_path == latest["path"]:
                raise GovernanceError("冷件路径不能等于热件路径（那是覆盖，不是搬运）")
            conn.execute(
                "INSERT INTO gov_doc_rolls(doc_id,hot_sha_before,hot_bytes_before,cold_path,"
                "cold_sha,index_path,actor,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (doc_id, hot_sha_before, hot_bytes_before, cold_path, cold_sha,
                 index_path, actor, self._now()),
            )
            self._emit(conn, "governance_doc_roll", actor,
                       {"doc_id": doc_id, "cold_path": cold_path,
                        "index_path": index_path})
        return {"doc_id": doc_id, "cold_path": cold_path, "index_path": index_path}

    def doc_status(self) -> dict:
        """只读：哪些热件超上限、哪些滚动缺索引、每个文档最后滚到哪。"""
        with self.store.transaction() as conn:
            docs = self._rows(conn, "SELECT * FROM gov_docs ORDER BY id")
            rolls = self._rows(conn, "SELECT * FROM gov_doc_rolls ORDER BY id")
        latest: dict[str, dict] = {}
        for row in docs:
            latest[row["doc_id"]] = row
        per_doc_roll = {}
        for row in rolls:
            per_doc_roll[row["doc_id"]] = row
        over_limit = []
        missing_index = []
        for doc_id, row in latest.items():
            if row["tier"] == "hot" and row["limit_bytes"] is not None \
                    and row["bytes"] > row["limit_bytes"]:
                over_limit.append({"doc_id": doc_id, "path": row["path"],
                                   "bytes": row["bytes"], "limit_bytes": row["limit_bytes"]})
        for doc_id, roll in per_doc_roll.items():
            if not _text(roll["index_path"]):
                missing_index.append(doc_id)
        return {
            "n_docs": len(latest),
            "hot_over_limit": over_limit,
            "cold_missing_index": missing_index,
            "last_rolls": {
                doc_id: {"cold_path": roll["cold_path"], "index_path": roll["index_path"],
                         "hot_sha_before": roll["hot_sha_before"]}
                for doc_id, roll in per_doc_roll.items()
            },
            "problems": (
                [f"热件超上限：{item['doc_id']}" for item in over_limit]
                + [f"冷件缺索引：{doc_id}" for doc_id in missing_index]
            ),
        }


# ---------------------------------------------------------------------------
# 人读现状页的节检查（模板在 kernel/templates/status_page.template.md）
# ---------------------------------------------------------------------------


def validate_status_page(text: str) -> list[str]:
    """检查一份人读现状页是否八节齐全。

    只查节标题在不在、顺序对不对；**不判断内容真假** —— 内容真假要靠证据文件。

    匹配口径：先去掉 `#` 与行首序号，**先按整串相等找**，找不到再退回子串匹配。
    这样「证据」与「证据在哪」不会被互相误配 —— 它们是两节：
    前者是「每条结论对应的关键证据摘要」，后者是「原始证据文件的链接清单」。
    """
    errors: list[str] = []
    if not isinstance(text, str) or not text.strip():
        return ["现状页是空的：八节一个都没有"]
    headings = [line.strip() for line in text.splitlines() if line.startswith("#")]
    normalised = [_normalise_heading(line) for line in headings]
    positions: list[tuple[int, int]] = []
    used: set[int] = set()
    for index, section in enumerate(STATUS_SECTIONS):
        position = None
        for candidate, heading in enumerate(normalised):
            if candidate not in used and heading == section:
                position = candidate
                break
        if position is None:
            for candidate, heading in enumerate(normalised):
                if candidate in used:
                    continue
                # 子串兜底：如果这个标题正好是**另一节**的整名，不算命中。
                # 否则「证据在哪」会被当成「证据」，于是两节互相顶替、各少一节却看不出。
                if heading in STATUS_SECTIONS and heading != section:
                    continue
                if section in heading:
                    position = candidate
                    break
        if position is None:
            errors.append(f"缺少第 {index + 1} 节「{section}」")
        else:
            used.add(position)
            positions.append((index, position))
    for (left_index, left_at), (right_index, right_at) in zip(positions, positions[1:]):
        if right_at < left_at:
            errors.append(
                f"节顺序不对：第 {right_index + 1} 节「{STATUS_SECTIONS[right_index]}」"
                f"出现在第 {left_index + 1} 节「{STATUS_SECTIONS[left_index]}」之前"
            )
    return errors


def _normalise_heading(line: str) -> str:
    """去掉 `#` 与行首序号（`1 ` / `1.` / `第1节`），保留节名。"""
    text = re.sub(r"^#+\s*", "", line).strip()
    text = re.sub(r"^第?\s*\d+\s*[.、,)]?\s*节?\s*", "", text).strip()
    return text
