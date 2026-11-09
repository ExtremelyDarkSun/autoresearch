# GENERATED-BY: portable extraction / repository bootstrap (2026-11-09 +08:00)
# LAST-EDIT: audit-host binding, 2026-11-09（宿主证据绑定：expected 由服务层构造、created_at 取自宿主、K11/K12 与 check_start）
# PURPOSE: 通用研究治理层 —— 事前方案校验与冻结哈希、独立方案审计的版本绑定与次数上限、专项意图的来源与节点监控、
#          经验候选的四处置与修订失效、角色包/委派登记、必读与文档冷件台账。全部是本地 append-only 登记，不联网、不伪造授权。
# ORIGIN: none（新文件）。机制抽取自上游组件 plan_audit.md / plan_audit.py / plan_audit_host.py / intent_monitor.md /
#         research_lessons.md / trajectory_review.md / reading_required.json / CONTRACT.md / CONTEXT_LOADING.md /
#         DOC_MAINTENANCE.md 与 EXPERIMENT_RULES.md 的事前设计、三类对照、结果分支表；只保留与具体赛事无关的通用部分，
#         源头的个人渠道与实例值一律不带。
# CUMULATIVE-DELTA: 2026-11-09 宿主证据绑定改造：① 敏感动作（关闭审计 / 豁免 / 扩额）的 expected 命令对象由本层按动作参数
#         构造，调用方只能给 proof（{session_id, seq}），不接受 caller 自带的 expected / created_at / report；
#         ② 时序判定用宿主返回的 created_at，缺时间一律拒；③ audit_independence 的 payload 绑定 owner / actor /
#         prepared_id / 报告全文（record_audit_file 用六键短声明），审计会话与 actor 对不上直接拒；④ 新增可信
#         extend（每次 +1，每次都要新的可信用户命令，不能自扩；不设固定次数上限）；⑤ 新增 K11 历史启动识别（只认真实 workflow 启动记录，未来新轮不能豁免，
#         做不到可信历史就显式未实现）；⑥ 修 check_start 的 closed 未评估缺口与未登记来源放行缺口；
#         ⑦ `authorization` 进 KEY_FIELDS：scope / series_id / reservation_id / decision_id 不能当措辞改动免重审。
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

宿主证据的绑定口径（2026-11-09）
================================

四个敏感动作（`policy_off` · `waiver` · `audit_extend` · `audit_independence`）的用户/子会话
证据由**本层**构造完整 payload 交给注入的 verifier（生产是 `host_evidence.DSHHost`）：

* 调用方只能给 **proof**：`{"session_id": ..., "seq": ...}`；给别的键（尤其是 `expected` /
  `created_at` / `report`）直接拒 —— 否则「拿无关动作的真实命令关审计」就能走通。
* 用户命令的 `expected` 由本层按动作参数现算（见 `policy_command()` / `waive_command()` /
  `extend_command()`），verifier 必须核「真实用户消息里的命令 == expected」。
* 时序判定只用**宿主返回的 created_at**（真实消息时间）。缺时间 ⇒ 不可判 ⇒ 拒。
* `audit_independence` 的 payload 绑定 owner（prepare 记录的负责人）、actor（报告署名的审计会话）、
  `prepared_id` 与报告全文（`record_audit_file` 用六键短声明 + 报告文件 SHA256）。审计会话号
  出现时必须与 actor 一致，否则直接拒。

`legacy_exempt` 只认**真实过去的启动记录**（同库 `wf_runs`，且启动早于本层第一次看到该方案），
只说明「不追溯新审计要求」，`audited` 恒为 False；新轮次或做不到可信历史时 fail-closed
并保留显式 `unimplemented` 标记，不伪造豁免。

`plan` 的关键字段集合 = `KEY_FIELDS`。改这些字段 ⇒ 必须重新审计；
改 `WORDING_FIELDS` 或其它字段 ⇒ 指纹不变 ⇒ **不要求重新审计**（来源：上游
plan_audit.md §6.1 的教训 —— 改一句动机就让上一轮审计作废，反过来压制了修改）。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time

from kernel.autoresearch.runtime.store import StoreError, canonical_json

# ---------------------------------------------------------------------------
# 常量：方案的关键字段与枚举
# ---------------------------------------------------------------------------

#: 参与指纹的字段。改其中任何一个 ⇒ 旧审计不再覆盖。
#: ⚠️ `authorization`（scope / series_id / reservation_id / decision_id）也在这里：
#: 它决定这一轮算小样本还是大规模 / 线上提交，属于**实质授权**，⛔ 不能当措辞改动免重审。
#: 但它**不强制每份计划都写**（没有预算准入的轮次可以不带）；见 `REQUIRED_KEY_FIELDS`。
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
    "authorization",
)

#: 方案**必须**写出的顶层字段。`authorization` 只在写了的时候参与指纹，不强制存在。
REQUIRED_KEY_FIELDS = tuple(key for key in KEY_FIELDS if key != "authorization")

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
LEGACY_STATUS_SECTIONS = (
    "范围与目标", "你的要求", "当前结论", "证据", "还没做成的事",
    "需要你拍板", "证据在哪", "自推进状态",
)
STATUS_SECTIONS = (
    "你的要求", "一句话现状", "最近实验", "已定 / 已否 / 待定",
    "本轮改了什么", "这轮结论依赖的未验证前提", "证据在哪", "下一步研究",
)

DOC_TIERS = ("hot", "cold")

INDEPENDENCE_DECLARED = "declared"
INDEPENDENCE_VERIFIED = "verified"

#: 用户在真实消息里必须原样发的那条命令的 action 名（宿主逐键核相等）。
POLICY_COMMAND = "plan_audit_policy"
WAIVE_COMMAND = "plan_audit_waive"
EXTEND_COMMAND = "plan_audit_extend"

#: 调用方能给的宿主证据只有这两个键。**不许带 expected / created_at / report**：
#: expected 由本层构造，created_at 只能由宿主返回。
PROOF_KEYS = ("session_id", "seq")

#: 扩额**不设固定次数上限**（源契约 K12 只要求「再准备须可信用户 extend，不能自扩」）。
#: 每次 extend 只 +1，且每次都要一条**新的**真实用户命令（命令含 used/limit/key_fingerprint 防重放）；
#: ⛔ 没有任何参数或 policy 能让本层自己加额度。

#: 宿主消息时间允许比本机时间快多少秒（时钟偏移容忍）。超过就判「未来时间不可信」。
USER_TIME_FUTURE_TOLERANCE = 5.0

#: 历史豁免只说明「不追溯」，绝不等于审过。
LEGACY_LABEL = "历史豁免：规则生效前已启动，不追溯（未审计，不等于审过）"
LEGACY_UNIMPLEMENTED = (
    "unimplemented: 做不到可信历史启动记录 ⇒ legacy_exempt 不可用（显式未实现；不追溯不等于可伪造豁免）"
)

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
PATH_KEYS = ("path", "cold_path", "index_path")


def _bare_session(value) -> str:
    """去掉可选的 `session-` 前缀，用于比较「是不是同一个会话」。"""
    text = value.strip()
    return text[len("session-"):] if text.startswith("session-") else text


def _same_identity(left, right) -> bool:
    if not (_text(left) and _text(right)):
        return False
    return _bare_session(left) == _bare_session(right)


def _hard_path(path: str) -> bool:
    """实质路径判定：`audit_support.hard_path` 之外，本层另把 `authorization` 整枝当硬。

    `authorization.scope / series_id / reservation_id / decision_id` 决定这一轮算小样本、
    大规模还是线上提交 —— ⛔ 不能当措辞或「定位修正」用小修绕过重审。
    （`audit_support.hard_path` 的正式过滤由父 agent 补；这里是治理层的第二道。）
    """
    from .audit_support import hard_path
    if hard_path(path):
        return True
    return str(path).split('.')[0] == 'authorization'


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

    for key in REQUIRED_KEY_FIELDS:
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
            exploratory = criteria.get("mode") == "exploratory"
            if criteria.get("mode", "confirmatory") not in ("exploratory", "confirmatory"):
                errors.append("criteria.mode 必须是 exploratory 或 confirmatory")
            required = ("metric", "observation", "interpretation_boundary", "conditions", "stop_condition", "source") if exploratory else ("metric", "direction", "threshold", "source")
            for key in required:
                if key not in criteria:
                    errors.append(f"缺少 criteria.{key}")
                elif exploratory and not _text(criteria.get(key)):
                    errors.append(f"criteria.{key} 必须非空")
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

    # ---- authorization：预算准入声明（写了才进指纹，也是最容易被当成「措辞」偷改的一处）----
    if "authorization" in plan:
        authorization = plan.get("authorization")
        if not isinstance(authorization, dict):
            errors.append("authorization 必须是对象（scope / series_id / reservation_id / decision_id）")
        else:
            for key in ("series_id", "reservation_id"):
                if key in authorization and not _text(authorization.get(key)):
                    errors.append(f"authorization.{key} 写了就必须是非空字符串")
            if "scope" in authorization \
                    and authorization.get("scope") not in ("small", "large", "submission"):
                errors.append("authorization.scope 必须是 small / large / submission")

    return errors


def _normalize(value):
    """指纹归一化：只做「顺序不影响语义」的那几处排序，不改内容。"""
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def plan_key_fields(plan: dict) -> dict:
    """取出参与指纹的关键字段（缺的记为 None，保持键集合稳定）。

    `authorization` 只在计划里写了的时候才进指纹：没有预算准入的轮次不带它，
    旧方案的指纹因此不变；一旦写了，改 scope / series_id / reservation_id /
    decision_id 都会让旧审计失效（不是措辞改动）。
    """
    if not isinstance(plan, dict):
        return {}
    fields: dict = {}
    for key in KEY_FIELDS:
        if key == "authorization":
            continue
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
    for key in ("execution_argv", "implementation", "authorization"):
        if key in plan:
            fields[key] = _normalize(plan[key])
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
    "CREATE TABLE IF NOT EXISTS gov_audit_corrections(\n"
    " id INTEGER PRIMARY KEY, report_id TEXT NOT NULL, old_fingerprint TEXT NOT NULL,\n"
    " new_fingerprint TEXT NOT NULL, detail_json TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL)",
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
    "CREATE TABLE IF NOT EXISTS gov_audit_extensions(\n"
    "  id INTEGER PRIMARY KEY, plan_id TEXT NOT NULL, key_fingerprint TEXT NOT NULL,\n"
    "  used_before INTEGER NOT NULL, limit_before INTEGER NOT NULL,\n"
    "  user_created_at REAL NOT NULL, independence TEXT NOT NULL,\n"
    "  evidence_json TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL,\n"
    "  created_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS gov_legacy_exemptions(\n"
    "  id INTEGER PRIMARY KEY, plan_id TEXT NOT NULL, key_fingerprint TEXT NOT NULL,\n"
    "  run_id TEXT NOT NULL, started_at REAL NOT NULL, basis TEXT NOT NULL,\n"
    "  evidence_json TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL,\n"
    "  created_at REAL NOT NULL, UNIQUE(plan_id, run_id))",
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
    "gov_audit_corrections",
    "gov_plans",
    "gov_audit_reports",
    "gov_audit_responses",
    "gov_waivers",
    "gov_audit_extensions",
    "gov_legacy_exemptions",
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
    clock : 可选 `() -> float`。本层自己的时间来源（默认 `time.time`），用于登记时间戳与
        「宿主消息时间不得在未来」判定。⛔ 用户证据的时间永远取宿主返回的 `created_at`。

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

    def __init__(self, store, *, verifier=None, policy=None, experiment_id=None,
                 clock=None):
        if store is None or not hasattr(store, "transaction"):
            raise GovernanceError("Governance 需要一个 runtime.store.Store 实例")
        self.store = store
        self.verifier = verifier
        self.experiment_id = experiment_id
        #: 本层自己的时间来源。只用于「宿主时间不得在未来」这类判定与登记时间戳；
        #: **用户证据的时间永远取宿主返回的 created_at**，不取调用方自报、也不取这里。
        self.clock = clock or time.time
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
        value = self.clock()
        if not _finite(value):
            raise GovernanceError("clock() 必须返回有限数值时间戳")
        return float(value)

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

    # -- 宿主证据：proof 由调用方给，expected 由本层构造 ---------------------

    def _proof(self, evidence, kind: str) -> dict:
        """只从调用方证据里取 `{session_id, seq}`。

        **不接受调用方自带 `expected` / `created_at` / `report`**：expected 必须由本层
        按动作参数构造，时间必须由宿主返回。给别的键一律拒 —— 否则「拿另一条真实用户
        命令（或另一份动作的 expected）去关这个动作」的路径就成立了。
        """
        if evidence is None:
            raise GovernanceError(
                f"{kind} 需要宿主证据 proof={{session_id, seq}}：没有证据就不能核真实用户/子会话"
            )
        if not isinstance(evidence, dict):
            raise GovernanceError(f"{kind} 的宿主证据必须是一个对象：{{session_id, seq}}")
        if set(evidence) == {"proof"}:
            inner = evidence["proof"]
        elif set(evidence) == set(PROOF_KEYS):
            inner = evidence
        else:
            extra = sorted(set(evidence) - set(PROOF_KEYS) - {"proof"})
            raise GovernanceError(
                f"{kind} 的宿主证据只能含 {list(PROOF_KEYS)}（或 {{'proof': {{…}}}}）；"
                f"⛔ 不接受调用方自带 expected/created_at/report 等字段"
                f"（多出：{extra or '嵌套 proof 之外的键'}）"
            )
        if not isinstance(inner, dict) or set(inner) != set(PROOF_KEYS):
            raise GovernanceError(f"{kind} 的 proof 必须恰好是 {list(PROOF_KEYS)}")
        session = inner["session_id"]
        seq = inner["seq"]
        if not _text(session) or any(ch.isspace() for ch in session) or len(session) > 200:
            raise GovernanceError("proof.session_id 必须是非空、无空白的会话标识")
        if not _non_negative_int(seq):
            raise GovernanceError("proof.seq 必须是 ≥0 的整数（宿主消息序号）")
        return {"session_id": session.strip(), "seq": seq}

    def _verify_user_command(self, kind: str, expected: dict, evidence) -> dict:
        """核「真实用户消息里恰好有 expected 这条命令」。

        `expected` 由调用方动作参数**在本层**构造，调用方只能给 proof。
        硬要求（缺一条都抛错，不降级成 declared）：
        * 宿主结果必须回传它核过的 `command`，且与 expected **逐键相等**（⛔ 不许省略）；
        * `created_at` 必须是可判定的宿主时间，且不得比本机时间快超过
          `USER_TIME_FUTURE_TOLERANCE` 秒（未来时间不可信）；
        * 回传的 `session_id` 去掉可选的 `session-` 前缀后必须等于 proof 的会话号；
        * 回传的 `seq` 必须精确等于 proof 的 seq。
        """
        proof = self._proof(evidence, kind)
        if self.verifier is None:
            raise GovernanceError(
                f"{kind} 需要注入宿主核验器（生产用 host_evidence.DSHost）："
                "没有核验器就不能核真实用户证据；本层不会把自述当授权"
            )
        payload = {"proof": proof, "expected": expected}
        try:
            result = self.verifier.verify(kind, payload)
        except Exception as exc:  # noqa: BLE001 - 核验器坏掉不等于核过
            raise GovernanceError(
                f"{kind} 的宿主核验失败：{type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(result, dict) or result.get("verified") is not True:
            raise GovernanceError(f"{kind} 需要宿主返回 verified=True；拿不到就不放行")
        command = result.get("command")
        if command is None:
            raise GovernanceError(
                f"{kind} 的宿主结果没有回传核过的 command：无法证明核的是这条命令 ⇒ 拒绝"
            )
        if command != expected:
            raise GovernanceError(
                f"{kind} 的宿主核验命令与本次动作构造的 expected 不一致："
                "另一条（别的动作 / 别的目标）的用户命令不能关这个动作"
            )
        created = result.get("created_at")
        if not _finite(created) or float(created) <= 0:
            raise GovernanceError(
                f"{kind} 的宿主结果没有可判定的 created_at：时序不可判 ⇒ 拒绝"
                "（时间只能来自宿主消息记录，不接受调用方自报）"
            )
        now = self._now()
        if float(created) > now + USER_TIME_FUTURE_TOLERANCE:
            raise GovernanceError(
                f"{kind} 的宿主消息时间在未来（{float(created):.0f} > 本机 {now:.0f} + "
                f"{USER_TIME_FUTURE_TOLERANCE:.0f}s）：不可信 ⇒ 拒绝"
            )
        session = result.get("session_id")
        if not _text(session):
            raise GovernanceError(
                f"{kind} 的宿主结果缺 session_id：无法确认核的是哪个会话的消息 ⇒ 拒绝"
            )
        if not _same_identity(session, proof["session_id"]):
            raise GovernanceError(
                f"{kind} 的宿主核过的会话与 proof 不一致：{session!r} != "
                f"{proof['session_id']!r} ⇒ 拒绝"
            )
        seq = result.get("seq")
        if not _non_negative_int(seq):
            raise GovernanceError(f"{kind} 的宿主结果缺合法的 seq：无法确认核的是哪条消息 ⇒ 拒绝")
        if seq != proof["seq"]:
            raise GovernanceError(
                f"{kind} 的宿主核过的 seq 与 proof 不一致：{seq!r} != {proof['seq']!r} ⇒ 拒绝"
            )
        if result.get("kind") not in (None, kind):
            raise GovernanceError(f"{kind} 的宿主结果 kind={result.get('kind')!r} 对应不上")
        return {
            "expected": expected,
            "proof": proof,
            "created_at": float(created),
            "session_id": session,
            "seq": seq,
            "rpc_id": result.get("rpc_id"),
            "command_sha256": result.get("command_sha256"),
            "source": result.get("source"),
        }

    def _verify_independence(self, prepared: dict, actor: str, document: dict,
                             evidence) -> tuple[str, dict]:
        """核审计者独立性：payload 绑定 owner / actor / prepared_id / 报告全文。

        没证据或核验器没核过 ⇒ `declared`（不冒充 verified）；核验成功但会话号/owner/
        材料标识与记录对不上 ⇒ 直接抛错（这是被篡改的绑定，不是「没核过」）。
        """
        if evidence is None:
            return INDEPENDENCE_DECLARED, {}
        if self.verifier is None:
            return INDEPENDENCE_DECLARED, {"reason": "no verifier injected"}
        try:
            proof = self._proof(evidence, "audit_independence")
        except GovernanceError as exc:
            return INDEPENDENCE_DECLARED, {"error": str(exc)}
        payload = {
            "proof": proof,
            "owner_session": prepared["owner"],
            "prepared_id": prepared["prepared_id"],
            "report": document,
        }
        try:
            result = self.verifier.verify("audit_independence", payload)
        except Exception as exc:  # noqa: BLE001 - 核验器坏掉不等于核过
            return INDEPENDENCE_DECLARED, {"error": f"{type(exc).__name__}: {exc}"}
        if not isinstance(result, dict) or result.get("verified") is not True:
            return INDEPENDENCE_DECLARED, {"result": result if isinstance(result, dict) else None}
        session = result.get("session_id")
        if not _text(session):
            raise GovernanceError(
                "宿主说核过了，但没有给出审计会话号：无法把独立性与报告署名人绑定，拒绝登记"
            )
        if not _same_identity(session, actor):
            raise GovernanceError(
                "宿主核过的审计会话与报告署名 actor 不一致 ⇒ 独立性不可信，拒绝登记"
            )
        if _same_identity(session, prepared["owner"]):
            raise GovernanceError("宿主核过的审计会话就是方案负责人自己 ⇒ 不独立，拒绝登记")
        owner = result.get("owner_session")
        if not _text(owner):
            raise GovernanceError("宿主结果缺 owner_session：无法绑定负责人，拒绝登记")
        if not _same_identity(owner, prepared["owner"]):
            raise GovernanceError("宿主核过的 owner 与 prepare 记录的负责人不一致，拒绝登记")
        echoed = result.get("prepared_id")
        if not _text(echoed):
            raise GovernanceError("宿主结果缺 prepared_id：无法绑定被审材料，拒绝登记")
        if echoed != prepared["prepared_id"]:
            raise GovernanceError("宿主核过的被审材料标识与这次 prepare 不一致，拒绝登记")
        verified = {
            "proof": proof,
            "owner_session": prepared["owner"],
            "prepared_id": prepared["prepared_id"],
            "document_sha256": _digest(document),
            "session_id": session,
            "seq": result.get("seq", proof["seq"]),
            "created_at": result.get("created_at"),
            "tool_call_id": result.get("tool_call_id"),
            "source": result.get("source"),
        }
        return INDEPENDENCE_VERIFIED, verified

    def _proof_reused(self, conn, table: str, proof: dict) -> bool:
        """同一条宿主消息（session_id, seq）不能用两次来关不同动作。

        旧库缺这张表 ⇒ 视为「没消费过」（只读兼容；真正写库的实例会先建表）。
        """
        if not self._table_exists(conn, table):
            return False
        for row in self._rows(conn, f"SELECT evidence_json FROM {table}"):
            try:
                payload = json.loads(row["evidence_json"] or "{}")
            except ValueError:
                continue
            stored = payload.get("proof") if isinstance(payload, dict) else None
            if isinstance(stored, dict) and stored.get("session_id") == proof["session_id"] \
                    and stored.get("seq") == proof["seq"]:
                return True
        return False

    @staticmethod
    def _user_created(evidence_json: str):
        """从已登记的宿主结果里取 created_at（宿主时间）。取不到返回 None。"""
        try:
            payload = json.loads(evidence_json or "{}")
        except ValueError:
            return None
        value = payload.get("created_at") if isinstance(payload, dict) else None
        return float(value) if _finite(value) else None

    def _row(self, conn, sql: str, params=()):
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _rows(self, conn, sql: str, params=()):
        return [dict(row) for row in conn.execute(sql, params).fetchall()]

    @staticmethod
    def _table_exists(conn, name: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None

    def _count(self, conn, table: str, plan_id: str) -> int:
        """数一个方案在某张登记表里的行数。**旧库缺表视为 0**（只读兼容）。"""
        if not self._table_exists(conn, table):
            return 0
        row = self._row(conn, f"SELECT COUNT(*) AS n FROM {table} WHERE plan_id=?", (plan_id,))
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------------
    # 政策
    # ------------------------------------------------------------------

    def policy_command(self, *, scope: str, target: str, mode: str) -> dict:
        """用户在真实消息里必须原样发出的关闭命令（由本层构造，调用方不能挑）。"""
        return {"action": POLICY_COMMAND, "scope": scope, "target": target, "mode": mode}

    def set_policy(self, *, scope: str, target: str, mode: str, actor: str, evidence=None) -> dict:
        """写一条审计政策。**升级不设门槛，降级要真用户消息。**

        `mode="off"` 的三条硬要求：
        * 必须由注入的宿主核验器核过一条**真实用户消息**，且消息里的命令与
          `policy_command(scope, target, mode)` 逐键相等（调用方只能给 proof）；
        * 时序判定用宿主返回的 `created_at`，且必须**严格晚于**该方案最后一次
          `prepare_audit`（防「先准备、再偷偷关掉」）；缺时间不可判 ⇒ 拒；
        * 同一条用户消息不能重复用来关闭（`(session_id, seq)` 只能用一次）。
        """
        if scope not in ("global", "plan", "role"):
            raise GovernanceError(f"scope 必须是 global / plan / role，实际 {scope!r}")
        if mode not in ("required", "off"):
            raise GovernanceError(f"mode 必须是 required / off，实际 {mode!r}")
        self._require_text(target, "target")
        self._require_text(actor, "actor")
        expected = self.policy_command(scope=scope, target=target, mode=mode)
        independence = INDEPENDENCE_DECLARED
        verified: dict = {}
        if mode == "off":
            verified = self._verify_user_command("policy_off", expected, evidence)
            independence = INDEPENDENCE_VERIFIED
            created = verified["created_at"]
            with self.store.transaction() as conn:
                if scope == "plan":
                    last = self._row(
                        conn,
                        "SELECT MAX(created_at) AS t FROM gov_audits WHERE plan_id=?",
                        (target,),
                    )
                    if last and last.get("t") is not None and created <= float(last["t"]):
                        raise GovernanceError(
                            "关闭审计的用户消息早于该方案最后一次 prepare_audit ⇒ 拒绝"
                            "（先准备、再偷偷关掉的路径必须堵住）"
                        )
                if self._proof_reused(conn, "gov_policy", verified["proof"]):
                    raise GovernanceError("这条用户消息已经用于关闭过政策：同一条决定不能重复消费")
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO gov_policy(scope,target,mode,actor,evidence_json,independence,created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (scope, target, mode, actor, canonical_json(verified or {}), independence, self._now()),
            )
            self._emit(conn, "governance_policy", actor,
                       {"scope": scope, "target": target, "mode": mode,
                        "independence": independence})
        return {"scope": scope, "target": target, "mode": mode,
                "independence": independence, "expected": expected}

    def audit_policy(self, plan_id: str | None = None) -> dict:
        """当前生效政策：plan 级 > global 级 > 默认 required。

        关闭记录只有在**晚于该方案最后一次 prepare** 时才对它生效（宿主返回的
        created_at 判；取不到时间的关闭记录一律不生效 ⇒ 回到 required）。这样即使
        历史库里有一条写在 prepare 之前的 off，也不会被拿来免审。
        """
        with self.store.transaction() as conn:
            rows = self._rows(conn, "SELECT * FROM gov_policy ORDER BY id")
            last_prep = None
            if plan_id:
                last = self._row(
                    conn,
                    "SELECT MAX(created_at) AS t FROM gov_audits WHERE plan_id=?",
                    (plan_id,),
                )
                last_prep = last.get("t") if last else None
        effective = {"mode": "required", "source": "default(fail-closed)",
                     "independence": None, "plan_checked": bool(plan_id),
                     "off_ignored": []}
        for row in rows:
            applies = (row["scope"] == "global" and row["target"] in ("*", "")) \
                or (plan_id is not None and row["scope"] == "plan" and row["target"] == plan_id)
            if not applies:
                continue
            if row["mode"] == "off":
                created = self._user_created(row["evidence_json"])
                if created is None:
                    effective["off_ignored"].append(
                        {"policy_id": row["id"], "reason": "没有宿主 created_at，关闭记录不生效"})
                    continue
                if last_prep is not None and created <= float(last_prep):
                    effective["off_ignored"].append(
                        {"policy_id": row["id"],
                         "reason": "关闭早于该方案最后一次 prepare，不生效"})
                    continue
            effective = {"mode": row["mode"], "source": f"{row['scope']}@{row['id']}",
                         "independence": row["independence"],
                         "plan_checked": bool(plan_id),
                         "off_ignored": effective["off_ignored"]}
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
        * 每个方案（跨版本累计）默认最多 `max_audits` 次（默认 2 = 首审 + 一次增量复审），
          再要就用可信 `extend_audit()` 每次 +1；**每次扩额都要新的真实用户命令，不能自我扩额**；
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
            used = self._count(conn, "gov_audits", plan_id)
            extensions = self._count(conn, "gov_audit_extensions", plan_id)
            limit = int(self.policy["max_audits"]) + int(extensions)
            if used >= limit:
                raise GovernanceError(
                    f"这个方案累计已经审过 {used} 次，达到上限 {limit}"
                    f"（首审 + 增量复审 {int(self.policy['max_audits'])} 次，"
                    f"已扩额 {extensions} 次）；"
                    "扩额必须由可信 extend_audit() 用**新的**真实用户命令逐次加，"
                    "本层不提供自我扩额入口"
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

    def record_audit(self, prepared_id: str, report: dict, *, actor: str, evidence=None,
                     _host_document: dict | None = None) -> dict:
        """登记一份独立审计报告。

        报告必须**恰好覆盖七个审项**（`AUDIT_CATEGORIES`），每项给
        `verdict` / `note` / `sources`（非空，每项 `{path, locator}`）。
        一致性强判：`verdict` 是 `issue`/`unknown` ⇒ 必须有同 category 的 issue；
        `unknown` 的那一项 issue 必须 `blocking=true`（判不出来不许默认通过）。

        `evidence` 只能给 `{session_id, seq}`（哪条子会话消息）。独立性核验的 payload
        由本层构造：owner=prepare 记录的负责人、actor=本函数署名、prepared_id、报告全文
        （`record_audit_file` 走六键短声明）。宿主没核过 ⇒ `declared`；核过但会话号与
        actor 对不上 ⇒ 直接拒。
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
        decision = report.get("decision")
        modern = decision is not None
        if modern:
            focus = report.get("focus")
            focus_keys = ("question_goal", "design_interpretation", "history_constraints")
            if not isinstance(focus, dict) or set(focus) != set(focus_keys) or not all(_text(focus.get(k)) for k in focus_keys):
                errors.append("focus 必须恰好给三项非空审查重点")
            for issue in issues:
                if isinstance(issue, dict) and issue.get("blocking") and not _text(issue.get("why_blocking")):
                    errors.append("重大阻断必须写 why_blocking")
            if decision not in ("proceed", "minor_fix", "blocked"):
                errors.append("decision 必须是 proceed/minor_fix/blocked")
            if not _text(report.get("decision_reason")):
                errors.append("decision_reason 必须非空")
            blocking = any(x.get("blocking") is True for x in issues if isinstance(x, dict))
            if decision in ("proceed", "minor_fix") and blocking:
                errors.append("proceed/minor_fix 不允许 blocking issue")
            if decision == "blocked" and not blocking:
                errors.append("blocked 必须有 blocking issue")
            if decision == "minor_fix" and not issues:
                errors.append("minor_fix 必须有 issue")
        for category, items in coverage.items():
            if not isinstance(items, dict):
                continue
            if items.get("verdict") == "unknown":
                related = by_category.get(category, [])
                if not modern and not any(x.get("blocking") is True for x in related):
                    errors.append(f"coverage.{category}.verdict=unknown，但这一项没有 blocking=true 的 issue")
                if modern:
                    for issue in related:
                        reason = "why_blocking" if issue.get("blocking") else "why_not_blocking"
                        if not _text(issue.get(reason)):
                            errors.append(f"unknown issue 必须写 {reason}")
        grants = report.get("minor_fix_paths", [])
        if not isinstance(grants, list):
            errors.append("minor_fix_paths 必须是数组")
            grants = []
        for grant in grants:
            if not isinstance(grant, dict) or not _text(grant.get("path")) or not _text(grant.get("why")) or grant.get("issue_id") not in issue_ids:
                errors.append("minor_fix_paths 必须按 issue 点名 {path, issue_id, why}")
            elif _hard_path(grant["path"]) or any(x.get("id") == grant["issue_id"] and x.get("blocking") for x in issues):
                errors.append("硬实质路径或重大问题不能授权为小修：" + grant["path"])

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

        # 第一段只读：先把「谁在审、绑哪一版、要关哪些旧问题」核清，再去做宿主核验
        #（宿主包里有文件 IO / 子进程，不能握着写事务）。
        with self.store.transaction() as conn:
            prepared = self._row(
                conn, "SELECT * FROM gov_audits WHERE prepared_id=?", (prepared_id,)
            )
            if prepared is None:
                raise GovernanceError(f"没有这个 prepared_id：{prepared_id!r}")
            if _same_identity(prepared["owner"], actor):
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
            for resolved in resolves:
                previous = self._row(conn, "SELECT * FROM gov_audit_reports WHERE report_id=? AND plan_id=?", (resolved["report_id"], prepared["plan_id"]))
                if not previous or not any(x.get("id") == resolved["issue_id"] for x in json.loads(previous["report_json"]).get("issues", [])):
                    raise GovernanceError("resolves 必须指向同计划的既有问题")

        # 第二段：宿主核验独立性。payload 由本层构造（owner / actor / prepared_id / 报告全文）。
        document = report if _host_document is None else _host_document
        independence, verified = self._verify_independence(prepared, actor, document, evidence)

        # 第三段重新开写事务：期间若冒出第二份报告或版本变了，仍然拒。
        with self.store.transaction() as conn:
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

    def correct_audit(self, report_id: str, corrections: list, *, actor: str) -> dict:
        """自查只覆盖指定非阻断意见及可授权叶子，不增加审计次数。"""
        from .audit_support import changes
        self._require_text(actor, "actor")
        if not isinstance(corrections, list) or not corrections:
            raise GovernanceError("corrections 必须是非空数组")
        with self.store.transaction() as conn:
            row = self._row(conn, "SELECT * FROM gov_audit_reports WHERE report_id=?", (report_id,))
            if not row:
                raise GovernanceError("报告不存在")
            report = json.loads(row["report_json"])
            if report.get("decision") not in ("proceed", "minor_fix"):
                raise GovernanceError("只有新三态非阻断报告可使用 correct")
            prep = self._row(conn, "SELECT * FROM gov_audits WHERE prepared_id=?", (row["prepared_id"],))
            if actor != prep["owner"]:
                raise GovernanceError("小修必须由负责人登记")
            before = self._row(conn, "SELECT * FROM gov_plans WHERE plan_id=? AND version=?", (row["plan_id"], prep["plan_version"]))
            after = self._row(conn, "SELECT * FROM gov_plans WHERE plan_id=? ORDER BY version DESC LIMIT 1", (row["plan_id"],))
            changed = changes(json.loads(before["plan_json"]), json.loads(after["plan_json"]))
            issues = {x["id"]: x for x in report.get("issues", [])}
            grants = {x["path"]: x for x in report.get("minor_fix_paths", [])}
            covered = set()
            for item in corrections:
                if not isinstance(item, dict) or not all(_text(item.get(k)) for k in ("what", "self_check")) or not item.get("sources"):
                    raise GovernanceError("小修必须写 what/self_check/sources")
                issue_id = item.get("issue_id")
                if issue_id and (issue_id not in issues or issues[issue_id].get("blocking")):
                    raise GovernanceError("correct 不能关闭阻断或未知意见")
                for path in item.get("paths", []):
                    if _hard_path(path):
                        raise GovernanceError("硬实质变更必须复审：" + path)
                    if path.endswith(".locator"):
                        pass  # 只改定位叶子，文件 path 另有变更时仍需独立授权。
                    elif path not in grants or grants[path]["issue_id"] != issue_id:
                        raise GovernanceError("此小修路径未按问题获得授权：" + path)
                    covered.add(path)
            key_changes = [p for p in changed if p.split('.')[0] in KEY_FIELDS or p.split('.')[0] in ('execution_argv', 'implementation')]
            if any(p not in covered for p in key_changes):
                raise GovernanceError("关键字段变化未被小修覆盖：" + str(key_changes))
            conn.execute("INSERT INTO gov_audit_corrections(report_id,old_fingerprint,new_fingerprint,detail_json,actor,created_at) VALUES(?,?,?,?,?,?)",
                         (report_id, row["key_fingerprint"], after["key_fingerprint"], canonical_json(corrections), actor, self._now()))
            self._emit(conn, "governance_audit_corrected", actor, {"report_id": report_id, "changed_paths": changed})
        return {"report_id": report_id, "key_fingerprint": after["key_fingerprint"], "reaudit_required": False}

    def record_audit_file(self, prepared_id: str, path: str, declaration: dict, *,
                          actor: str, evidence=None) -> dict:
        """从报告文件登记审计：核「短声明（六键）↔ 报告文件字节 SHA256」后登记。

        * 六键短声明 = `prepared_id` / `decision` / `decision_reason` / `report_file` /
          `report_sha256` / `issues[{id,blocking}]`；多一键或少一键都拒；
        * `declaration.report_sha256` 必须等于报告文件的**原始字节** SHA256（换文件即拒）；
        * 宿主核验的文档是这条短声明（审计者真实回传的就是它）；payload 里仍带
          owner / actor / prepared_id，`record_audit` 负责核会话与署名人一致。
        """
        from .audit_support import load_bound_report
        if not isinstance(declaration, dict):
            raise GovernanceError("短声明必须是对象（六键：prepared_id/decision/decision_reason/"
                                  "report_file/report_sha256/issues）")
        try:
            report = load_bound_report(path, declaration)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise GovernanceError(f"报告文件与短声明不一致，拒绝登记：{exc}") from exc
        return self.record_audit(prepared_id, report, actor=actor, evidence=evidence,
                                 _host_document=declaration)

    def waive_command(self, report_id: str, issue_id: str) -> dict:
        """豁免动作的用户命令（由本层按报告与当前关键指纹现算）。"""
        self._require_text(report_id, "report_id")
        self._require_text(issue_id, "issue_id")
        with self.store.transaction() as conn:
            report = self._row(conn, "SELECT * FROM gov_audit_reports WHERE report_id=?",
                               (report_id,))
            if report is None:
                raise GovernanceError(f"没有这份审计报告：{report_id!r}")
            issues = {x.get("id") for x in json.loads(report["report_json"]).get("issues", [])
                      if isinstance(x, dict)}
            if issue_id not in issues:
                raise GovernanceError(f"issue_id={issue_id!r} 不在这份报告的 issues 里")
            plan = self._row(
                conn, "SELECT version, key_fingerprint FROM gov_plans WHERE plan_id=?"
                      " ORDER BY version DESC LIMIT 1", (report["plan_id"],))
            if plan is None or plan["key_fingerprint"] != report["key_fingerprint"]:
                raise GovernanceError(
                    "方案关键字段已经变过 ⇒ 这份报告的阻断与豁免都不覆盖当前版本；先复审"
                )
        return {"action": WAIVE_COMMAND, "plan_id": report["plan_id"],
                "report_id": report_id, "issue_id": issue_id,
                "key_fingerprint": report["key_fingerprint"]}

    def waive(self, report_id: str, issue_id: str, *, reason: str, evidence, actor: str) -> dict:
        """人工豁免一条审计意见。**必须有宿主核过的真实用户命令。**

        三条硬要求：① 命令由本层按 `{action, plan_id, report_id, issue_id,
        key_fingerprint}` 构造，调用方只能给 proof；② 用户消息时间（宿主 created_at）
        必须不早于这份报告 —— 人不可能在报告存在之前就看过它；③ 命令绑当前关键指纹，
        方案实质一变，豁免自动失效。
        """
        self._require_text(actor, "actor")
        self._require_text(reason, "reason")
        expected = self.waive_command(report_id, issue_id)
        verified = self._verify_user_command("waiver", expected, evidence)
        with self.store.transaction() as conn:
            report = self._row(
                conn, "SELECT * FROM gov_audit_reports WHERE report_id=?", (report_id,)
            )
            if report is None:
                raise GovernanceError(f"没有这份审计报告：{report_id!r}")
            if verified["created_at"] < float(report["created_at"]):
                raise GovernanceError(
                    "豁免的用户消息早于这份审计报告 ⇒ 那时这份报告还不存在；不能用来关它"
                )
            existing = self._row(
                conn, "SELECT id FROM gov_waivers WHERE report_id=? AND issue_id=?",
                (report_id, issue_id),
            )
            if existing:
                raise GovernanceError("这条意见已经豁免过")
            if self._proof_reused(conn, "gov_waivers", verified["proof"]):
                raise GovernanceError("这条用户消息已经用于豁免过：同一条决定不能重复消费")
            conn.execute(
                "INSERT INTO gov_waivers(report_id,issue_id,key_fingerprint,reason,evidence_json,"
                "independence,actor,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (report_id, issue_id, report["key_fingerprint"], reason,
                 canonical_json(verified), INDEPENDENCE_VERIFIED, actor, self._now()),
            )
            self._emit(conn, "governance_waiver", actor,
                       {"report_id": report_id, "issue_id": issue_id,
                        "key_fingerprint": report["key_fingerprint"]})
        return {"report_id": report_id, "issue_id": issue_id,
                "independence": INDEPENDENCE_VERIFIED,
                "key_fingerprint": report["key_fingerprint"],
                "expected": expected}

    # ------------------------------------------------------------------
    # 审计额度：可信 extend（每次 +1，不设自我扩额的入口）
    # ------------------------------------------------------------------

    def audit_budget(self, plan_id: str) -> dict:
        """只读：这个方案（跨版本累计）用了多少、当前上限多少、是否可扩。

        `limit = max_audits + extensions`；扩额次数本身**没有固定上限**（K12 只要求
        每次都要可信用户 extend），所以这里不报 `max_extensions`。
        """
        self._require_text(plan_id, "plan_id")
        with self.store.transaction() as conn:
            used = self._count(conn, "gov_audits", plan_id)
            extensions = self._count(conn, "gov_audit_extensions", plan_id)
        limit = int(self.policy["max_audits"]) + int(extensions)
        return {"plan_id": plan_id, "used": int(used), "limit": int(limit),
                "extensions": int(extensions),
                "remaining": max(int(limit) - int(used), 0),
                "exhausted": int(used) >= int(limit),
                "can_extend": int(used) >= int(limit)}

    def extend_command(self, plan_id: str) -> dict:
        """扩额动作的用户命令（由本层按当前额度现算，含 used/limit 防重放）。"""
        plan = self.latest_plan(plan_id)
        if plan is None:
            raise GovernanceError(f"方案 {plan_id!r} 还没有冻结过")
        budget = self.audit_budget(plan_id)
        if budget["used"] < budget["limit"]:
            raise GovernanceError(
                f"审计额度还没用满（{budget['used']}/{budget['limit']}）：不能预先加额度"
            )
        return {"action": EXTEND_COMMAND, "plan_id": plan_id,
                "used": budget["used"], "limit": budget["limit"],
                "key_fingerprint": plan["key_fingerprint"]}

    def extend_audit(self, plan_id: str, *, evidence, actor: str, reason: str = "") -> dict:
        """可信用户扩额：只在额度用满后 +1。**每次都要一条新的真实用户命令。**

        用户命令由 `extend_command()` 现算（含 `used` / `limit` / 当前关键指纹）：
        旧指令在额度变化后对不上 ⇒ 不能拿一次批准反复扩。缺宿主时间、命令不等、
        核验器缺失、同一条消息重复消费一律拒；**没有任何参数能绕过用户命令加额度**。
        扩额次数本身不设固定上限：用户每次明确授权一条新命令就能再加一次。
        """
        self._require_text(actor, "actor")
        expected = self.extend_command(plan_id)
        verified = self._verify_user_command("audit_extend", expected, evidence)
        with self.store.transaction() as conn:
            used = self._count(conn, "gov_audits", plan_id)
            extensions = self._count(conn, "gov_audit_extensions", plan_id)
            limit = int(self.policy["max_audits"]) + int(extensions)
            if int(used) != expected["used"] or int(limit) != expected["limit"]:
                raise GovernanceError(
                    "额度在核验期间变了 ⇒ 这条用户命令不能用于当前状态，请重新确认"
                )
            last = self._row(conn, "SELECT MAX(created_at) AS t FROM gov_audits WHERE plan_id=?",
                             (plan_id,))
            if last and last.get("t") is not None \
                    and verified["created_at"] < float(last["t"]):
                raise GovernanceError(
                    "扩额的用户消息早于最后一轮审计 ⇒ 那时人还没看到审计结果；不能用来扩额"
                )
            if self._proof_reused(conn, "gov_audit_extensions", verified["proof"]):
                raise GovernanceError("这条用户消息已经用于扩额过：同一条决定不能重复消费")
            plan = self._row(
                conn, "SELECT key_fingerprint FROM gov_plans WHERE plan_id=?"
                      " ORDER BY version DESC LIMIT 1", (plan_id,))
            conn.execute(
                "INSERT INTO gov_audit_extensions(plan_id,key_fingerprint,used_before,"
                "limit_before,user_created_at,independence,evidence_json,reason,actor,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (plan_id, (plan or {}).get("key_fingerprint", ""), int(used), int(limit),
                 verified["created_at"], INDEPENDENCE_VERIFIED, canonical_json(verified),
                 reason or "", actor, self._now()),
            )
            self._emit(conn, "governance_audit_extended", actor,
                       {"plan_id": plan_id, "extensions": int(extensions) + 1,
                        "limit": int(limit) + 1, "used": int(used)})
        budget = self.audit_budget(plan_id)
        return {"plan_id": plan_id, "expected": expected,
                "independence": INDEPENDENCE_VERIFIED, **budget}

    # ------------------------------------------------------------------
    # K11 历史轮次：只认真实过去的启动记录；不追溯 ≠ 审过
    # ------------------------------------------------------------------

    def _workflow_runs(self, plan_id: str) -> list[dict]:
        """读同一个库里 workflow 的真实启动记录（K11 的历史启动来源）。

        表不存在（没接过执行主链）⇒ 返回空 ⇒ 调用方走「显式未实现」，不猜。
        """
        with self.store.transaction() as conn:
            if not self._table_exists(conn, "wf_runs"):
                return []
            try:
                rows = self._rows(
                    conn, "SELECT * FROM wf_runs WHERE plan_id=? ORDER BY started_at",
                    (plan_id,))
            except sqlite3.OperationalError:
                return []
        return rows

    def _first_governance_time(self, conn, plan_id: str):
        """本层第一次看到这个方案的时间（冻结或第一次准备审计）。"""
        plan = conn.execute(
            "SELECT MIN(created_at) AS t FROM gov_plans WHERE plan_id=?", (plan_id,)
        ).fetchone()
        audit = conn.execute(
            "SELECT MIN(created_at) AS t FROM gov_audits WHERE plan_id=?", (plan_id,)
        ).fetchone()
        values = [row["t"] for row in (plan, audit) if row is not None and row["t"] is not None]
        return min(values) if values else None

    def legacy_state(self, plan_id: str) -> dict:
        """只读：这个方案现在算不算**真实历史轮次**。

        `legacy_exempt=True` 只表示「规则生效前已起跑，不追溯新审计要求」；
        `audited` 恒为 False，`label` 逐字写「未审计，不等于审过」。
        """
        plan = self.latest_plan(plan_id)
        base = {"plan_id": plan_id, "legacy_exempt": False, "audited": False,
                "available": False, "runs": [], "exempted_runs": [],
                "reason": None, "label": "未审计", "unimplemented": False,
                "key_fingerprint": None}
        if plan is None:
            base["reason"] = "方案不存在"
            return base
        base["key_fingerprint"] = plan["key_fingerprint"]
        runs = self._workflow_runs(plan_id)
        base["runs"] = [{"run_id": run.get("run_id"), "started_at": run.get("started_at")}
                        for run in runs]
        if not runs:
            base["reason"] = LEGACY_UNIMPLEMENTED
            base["unimplemented"] = True
            return base
        valid = [run for run in runs if _finite(run.get("started_at"))]
        if not valid:
            base["reason"] = LEGACY_UNIMPLEMENTED + "（启动记录没有可判定的 started_at）"
            base["unimplemented"] = True
            return base
        with self.store.transaction() as conn:
            first = self._first_governance_time(conn, plan_id)
            if self._table_exists(conn, "gov_legacy_exemptions"):
                exemptions = [row for row in self._rows(
                    conn, "SELECT * FROM gov_legacy_exemptions WHERE plan_id=?", (plan_id,))
                    if row["key_fingerprint"] == plan["key_fingerprint"]]
            else:
                exemptions = []
        gated = [run for run in valid
                 if first is not None and float(run["started_at"]) >= float(first)]
        if gated:
            base["gated_runs"] = [{"run_id": run["run_id"], "started_at": run["started_at"]}
                                  for run in gated]
            base["reason"] = (
                "有启动记录晚于本层第一次看到该方案 ⇒ 那是受治理的新轮次，不能豁免；新轮走当前审计"
            )
            return base
        covered = {row["run_id"] for row in exemptions}
        base["available"] = True
        uncovered = [run["run_id"] for run in valid if run["run_id"] not in covered]
        if uncovered:
            base["uncovered_runs"] = uncovered
            base["reason"] = (f"真实历史启动记录还没有登记豁免：{uncovered} ⇒ 先 legacy_exempt"
                              "（只豁免历史要求，不算审过）")
            return base
        base.update(legacy_exempt=True, audited=False, label=LEGACY_LABEL,
                    exempted_runs=sorted(covered), reason=None)
        return base

    def legacy_exempt(self, plan_id: str, *, actor: str, reason: str, evidence=None) -> dict:
        """登记「历史轮次不追溯新审计要求」。**只认真实过去的启动记录。**

        硬要求（缺一条就 fail-closed，且错误里带 `unimplemented` 标记而不是造豁免）：
        * 同库 `wf_runs` 里必须有这个方案的真实启动记录（workflow 起跑时落的那条）；
        * 每条启动时间必须可判、在过去、且**早于本层第一次看到该方案的时间** ——
          受治理之后才起的轮次（含未来新轮）一律不能豁免；
        * 豁免绑当前关键字段指纹：方案实质一变，豁免不再覆盖。

        `evidence`（宿主可信来源）可选；给了就必须是 `legacy_start` 且宿主核过时间。
        生产 `DSHost` 目前不提供这种 kind ⇒ 这条路径会 fail-closed，不伪造。
        """
        self._require_text(actor, "actor")
        self._require_text(reason, "reason")
        plan = self.latest_plan(plan_id)
        if plan is None:
            raise GovernanceError(
                "方案还没有冻结过：历史豁免要绑当前关键字段指纹，先 freeze_plan()",
                ["legacy_plan_not_frozen"])
        runs = self._workflow_runs(plan_id)
        if not runs:
            raise GovernanceError(LEGACY_UNIMPLEMENTED,
                                  ["legacy_untrusted_history", "unimplemented"])
        valid = [run for run in runs if _finite(run.get("started_at"))]
        if not valid:
            raise GovernanceError(
                LEGACY_UNIMPLEMENTED + "（启动记录没有可判定的 started_at）",
                ["legacy_untrusted_history", "unimplemented"])
        now = self._now()
        with self.store.transaction() as conn:
            first = self._first_governance_time(conn, plan_id)
            if self._table_exists(conn, "gov_legacy_exemptions"):
                existing = {row["run_id"] for row in self._rows(
                    conn, "SELECT * FROM gov_legacy_exemptions WHERE plan_id=?", (plan_id,))
                    if row["key_fingerprint"] == plan["key_fingerprint"]}
            else:
                existing = set()
        pending = []
        for run in valid:
            started = float(run["started_at"])
            if started > now:
                raise GovernanceError(
                    "启动记录的时间在未来 ⇒ 不可信，不能当历史轮次",
                    ["legacy_future_start"])
            if first is not None and started >= float(first):
                raise GovernanceError(
                    "这条启动记录晚于本层第一次看到该方案 ⇒ 是受治理的轮次（或新轮），不能豁免；"
                    "新轮必须走当前审计",
                    ["legacy_new_round"])
            if run["run_id"] not in existing:
                pending.append(run)
        if not pending:
            return self.legacy_state(plan_id)
        basis = "workflow_start_record"
        verified: dict = {}
        if evidence is not None:
            proof = self._proof(evidence, "legacy_start")
            with self.store.transaction() as conn:
                reused = self._proof_reused(conn, "gov_legacy_exemptions", proof)
            if reused:
                raise GovernanceError("这条用户/宿主证据已经用于历史豁免", ["legacy_proof_reused"])
            if len(pending) != 1:
                raise GovernanceError(
                    "一条可信来源证据只能绑一条启动记录；有多条历史 run 时逐条给证据",
                    ["legacy_evidence_ambiguous"])
            if self.verifier is None:
                raise GovernanceError(
                    "给了宿主证据但没有核验器：不能把历史启动记成可信来源",
                    ["legacy_untrusted_history", "unimplemented"])
            target = pending[0]
            payload = {"proof": proof, "plan_id": plan_id, "run_id": target["run_id"],
                       "started_at": float(target["started_at"])}
            try:
                result = self.verifier.verify("legacy_start", payload)
            except Exception as exc:  # noqa: BLE001 - 不支持就是不可用，不猜
                raise GovernanceError(
                    f"宿主不能核历史启动（legacy_start）：{type(exc).__name__}: {exc}；"
                    "这条路径保留为显式未实现，不伪造豁免",
                    ["legacy_untrusted_history", "unimplemented"]) from exc
            if not isinstance(result, dict) or result.get("verified") is not True:
                raise GovernanceError("宿主没有核过这条历史启动证据",
                                      ["legacy_untrusted_history"])
            if not _finite(result.get("created_at")):
                raise GovernanceError("宿主结果缺 created_at：历史启动时间不可判，拒绝",
                                      ["legacy_untrusted_history"])
            if result.get("run_id") not in (None, target["run_id"]):
                raise GovernanceError("宿主核过的 run_id 与本地启动记录不一致",
                                      ["legacy_binding_mismatch"])
            if result.get("started_at") is not None \
                    and float(result["started_at"]) != float(target["started_at"]):
                raise GovernanceError("宿主核过的启动时间与本地记录不一致",
                                      ["legacy_binding_mismatch"])
            verified = {"proof": proof, "created_at": float(result["created_at"]),
                        "run_id": target["run_id"],
                        "started_at": float(target["started_at"]),
                        "session_id": result.get("session_id"), "source": result.get("source")}
            basis = "host_evidence"
        with self.store.transaction() as conn:
            for run in pending:
                try:
                    conn.execute(
                        "INSERT INTO gov_legacy_exemptions(plan_id,key_fingerprint,"
                        "run_id,started_at,basis,evidence_json,reason,actor,created_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?)",
                        (plan_id, plan["key_fingerprint"], run["run_id"],
                         float(run["started_at"]), basis, canonical_json(verified),
                         reason, actor, self._now()),
                    )
                except sqlite3.IntegrityError as exc:
                    raise GovernanceError(
                        "这条历史启动记录已经按另一版关键指纹登记过豁免：当前版本是新设计，"
                        "必须走当前审计（不能拿旧轮次的启动记录豁免新设计）",
                        ["legacy_binding_mismatch"]) from exc
            self._emit(conn, "governance_legacy_exempt", actor,
                       {"plan_id": plan_id, "runs": [run["run_id"] for run in pending],
                        "audited": False, "basis": basis})
        state = self.legacy_state(plan_id)
        state["basis"] = basis
        return state

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

    def audit_state(self, plan_id: str) -> dict:
        """三态投影。所有历史阻断跨版本存活；历史豁免不称 audited。只读。"""
        from .audit_state import audit_state
        return audit_state(self, plan_id)

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

        返回 `{allow, problems, diagnostics, source_registered}`。`allow=False` ⇒ 不放行。

        这一层只判「登记过的账有没有缺口」，但**不给假绿**：

        * `closed` 的意图**不**直接放行：closed 只说明不派新监控，仍拦「已同步未评估」
          的用户原话（修 L1 的 closed 分支缺口）；
        * 没有专项意图是正常状态（不派监控），但**来源会话必须已知**：没给来源、或该会话
          从没登记过用户原话水位 ⇒ 不可判 ⇒ 不放行（修 L1「未登记会话放行」缺口）；
        * 给了 `source_session` 但它不是该意图的来源 ⇒ 不可判 ⇒ 不放行。

        语义识别（什么算专项意图、什么算执行偏差）仍靠主会话；完整监控主链在
        `monitoring.Monitoring.check_start`，本函数不冒充它。
        """
        problems: list[str] = []
        diagnostics: list[str] = []
        if intent_id is None:
            diagnostics.append("没有专项意图 ⇒ 不派监控（这是正常状态，不是缺口）")
            if not _text(source_session):
                problems.append(
                    "没有来源会话：无法核未评估的用户原话与监控遗漏（不可判 ⇒ 不放行）；"
                    "请给 --source-session"
                )
                return {"allow": False, "problems": problems,
                        "diagnostics": diagnostics, "source_registered": False}
            with self.store.transaction() as conn:
                updates = self._row(
                    conn,
                    "SELECT MAX(seq) AS top FROM gov_user_updates WHERE source_session=?",
                    (source_session,),
                )
                assessed = self._row(
                    conn,
                    "SELECT MAX(covers_seq) AS top FROM gov_intent_assessments"
                    " WHERE source_session=?",
                    (source_session,),
                )
                registered = self._row(
                    conn,
                    "SELECT (SELECT COUNT(*) FROM gov_user_updates WHERE source_session=?)"
                    " + (SELECT COUNT(*) FROM gov_intent_assessments WHERE source_session=?)"
                    " + (SELECT COUNT(*) FROM gov_intents WHERE source_session=?) AS n",
                    (source_session, source_session, source_session),
                )["n"]
            if not registered:
                problems.append(
                    f"来源会话 {source_session!r} 未登记（没有用户原话水位）⇒ 不可判，拒绝放行；"
                    "先 sync_user_update 登记真实原话"
                )
                return {"allow": False, "problems": problems,
                        "diagnostics": diagnostics, "source_registered": False}
            top = int(updates["top"] or 0)
            done = int(assessed["top"] or 0)
            if self.policy.get("block_unassessed_without_intent") and top > done:
                problems.append(
                    f"有已同步未评估的用户原话（评估水位 {done} < {top}）⇒ 先 assess_update"
                )
            return {"allow": not problems, "problems": problems,
                    "diagnostics": diagnostics, "source_registered": True}

        with self.store.transaction() as conn:
            row = self._row(conn, "SELECT * FROM gov_intents WHERE intent_id=?",
                            (intent_id,))
        if row is None:
            return {"allow": False, "problems": [f"意图 {intent_id!r} 不存在"],
                    "diagnostics": diagnostics, "source_registered": False}
        if _text(source_session) and not _same_identity(source_session, row["source_session"]):
            problems.append(
                f"给的来源会话与意图登记的不一致：{source_session!r} != "
                f"{row['source_session']!r} ⇒ 不可判，拒绝放行"
            )
        if row["state"] == "closed":
            diagnostics.append("意图已 closed ⇒ 不派监控；但 closed ≠ 用户原话都评估过")
        state = self.intent_state(intent_id)
        problems.extend(state["problems"])
        if row["state"] == "blocked":
            problems.append("意图处于 blocked：阻碍解除前不放行新方向（blocked ≠ closed）")
        return {"allow": not problems, "problems": problems,
                "diagnostics": diagnostics, "source_registered": True}

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


def _validate_status_structure(text: str, sections) -> list[str]:
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
    for index, section in enumerate(sections):
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
                if heading in sections and heading != section:
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
                f"节顺序不对：第 {right_index + 1} 节「{sections[right_index]}」"
                f"出现在第 {left_index + 1} 节「{sections[left_index]}」之前"
            )
    return errors


def _normalise_heading(line: str) -> str:
    """去掉 `#` 与行首序号（`1 ` / `1.` / `第1节`），保留节名。"""
    text = re.sub(r"^#+\s*", "", line).strip()
    text = re.sub(r"^第?\s*\d+\s*[.、,)]?\s*节?\s*", "", text).strip()
    return text


def validate_status_page(text: str, *, legacy: bool = False) -> list[str]:
    """研究八节及四段结构。旧页显式 legacy 只读兼容。"""
    sections = LEGACY_STATUS_SECTIONS if legacy else STATUS_SECTIONS
    errors = _validate_status_structure(text, sections)
    if errors or legacy:
        return errors
    matches = list(re.finditer(r"(?m)^##(?!#)\s+.*$", text))
    if len(matches) != 8:
        return ["研究页必须有八个二级标题"]
    bodies = [text[m.end():matches[i+1].start() if i+1 < len(matches) else len(text)].strip() for i,m in enumerate(matches)]
    for i, body in enumerate(bodies):
        if not body:
            errors.append(f"第 {i+1} 节没有内容")
    recent = bodies[2]
    if '暂无实验，未起跑' not in recent:
        blocks = re.split(r'(?m)^###\s+', recent)[1:]
        if not blocks:
            errors.append("第 3 节实验须用三级标题分块")
        if len(blocks) > 3:
            errors.append("第 3 节最多保留最近 3 轮")
        for block in blocks:
            positions = [block.find(label) for label in ('假设', '判据', '设置', '结果')]
            if any(x < 0 for x in positions) or positions != sorted(positions):
                errors.append("每个实验块须按假设→判据→设置→结果组织")
            if '来源' not in block:
                errors.append("实验块缺来源")
    return errors
