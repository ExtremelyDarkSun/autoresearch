# GENERATED-BY: 统一只读检查视图（E2/E4）迁移，2026-11-09
# LAST-EDIT: 2026-11-09 共16项；预算绑定Admission.check_budget实例与同一时点，租约区分启动阶段
# PURPOSE: 把「这一轮绿不绿」变成一张只读清单：每项带 name/status/exp_id/source/evidence/required，缺数据一律 unknown。
# ORIGIN: framework_expectations E2（证据检查关联实验）· E4（当前必需检查清单）· E7（待答问题）· E5（下一步交接投影）
# CUMULATIVE-DELTA: 首版。⛔ 不实例化可写服务、不建库/目录/迁移 schema、不造任何 verified 证明；
#   所有来源都可以用 services={...} 注入（生产默认按只读构造去读真实台账）。
#   本轮补（E4 复核）：新增 `workflow.audit` / `budget.reservation` / `remote.lease` 三个**分项**——
#   未启动的轮次核当前独立审计、当前可用预算预留、远端资源租约证据（各自 blocked|unknown，不计绿）；
#   已启动/已完成的轮次只展示当次历史绑定（`wf_runs.gates_json.audit`、`settled|released`、
#   无持有/隔离资源），⛔ 不强求「新的起跑可行」；`local` 范围明确不适用远端租约。
#   再修（remote 阶段语义）：未启动的远端轮次**还没有** claim 租约（取租约在 `remote start`
#   的写事务里），所以不再要求「本实验已有 task 租约」，改为只读核
#   `plan.execution.remote.resources` 声明的**正式资源键**现状（复用 `progress_remote._lease_view`：
#   free→ok / held→blocked / quarantine→unknown / 缺键或缺 resources·quarantine 表→unknown），
#   ⛔ 不读本地执行的 resources 猜远端 key，⛔ 不把缺表读成空闲。

"""统一只读检查视图（E2/E4）。

一句话：**给每个必需检查一行，写清它读的是哪份真实台账、关联哪个实验、有没有在绿。**

它做什么
========

`checklist(workspace, *, topic=None, exp_id=None, owner_session=None, db=None, services=None,
host=None, now=None, required=True)` 把现有六个来源的**只读投影**收成一张清单：

| item | 来源 | 回答什么 |
|---|---|---|
| `workflow.preparation` | `Workflow.readonly.status` | 事前材料过期 / 下一轮被什么拦住 |
| `workflow.review` | 同上 | **缺复盘**：已结束但没复盘的运行 |
| `workflow.audit` | `wf_runs.gates_json`（历史）/ 只读治理 `audit_state`（未启动） | **独立审计**：已启动看当次绑定，未启动核当前审计 |
| `monitoring.intent` | `Monitoring.status` | **待监控**：意图状态、待审触发、未处置意见、过期报告 |
| `monitoring.launch` | `Monitoring.check_start(action='launch')` | 起跑闸的判词 |
| `decisions.inbound` | `DecisionService.readonly.reply_status` | **超期入向**（304 s 窗口） |
| `decisions.approval` | `DecisionService.readonly.status` | 关联实验**真实计划范围**所需批准 / 隔离 / 未送达 |
| `budget.reservation` | `budget_accounts` / `budget_reservations`（只读） | **预算预留**：未启动核当前可用；已启动核历史结算/释放 |
| `remote.lease` | `RemoteWorkflow(readonly=True).status`（已启动）/ `progress_remote.lease_view`（未启动） | **远端资源租约**：`local` 不适用；未启动只核**声明键**现状（free=ok / held=blocked / quarantine=unknown / 缺键缺表=unknown） |
| `issues.registry` | `Issues.readonly.status` | 未结问题（closed 一律不挡新轮） |
| `issues.exp_link` | 同上 | **证据/问题关联实验**；旧无 exp 记录 → unknown（未入台账） |
| `questions.open` | `Questions.readonly.open_questions` | 待答 / 超期 / 只有 declared（默认诊断，`blocking` 才拦） |
| `reporting.page` | `reporting.check` | 人读页正文与回执 |
| `reporting.round` | `reporting.check_round` | 整轮终态 + 负责人判断 + 页面回执 |
| `review.continuation` | `review_continuation.project` | 下一步判据 / 证据 / 费用范围 / 负责人 / 是否需批准（**只是声明**） |

⚠️ **审计 / 预算 / 租约三项只对「未启动的轮次」核当前条件**；已启动或已完成的轮次只展示
当时的真实绑定（`wf_runs.gates_json` 的 `audit` / 预算预留的 `settled|released` /
没有持有或隔离的资源租约）。⛔ 不对已完成的轮次强求「现在还能起跑」，也⛔ 不把缺证据读成绿。

⚠️ **远端租约还分阶段**：未启动的轮次本来就没有 claim 过租约（取租约发生在 `remote start`
的写事务里），所以它核的是**计划声明的正式资源键现在能不能拿**（free / held / quarantined），
⛔ 不要求本实验已有 `task` 租约 —— 否则真实可执行的远端计划会被永远挡死。

每项的形状固定：

    {name, status:'ok'|'blocked'|'unknown', exp_id, exp_link, source, evidence, required,
     applicable, problems[], notes[]}

* `status` 是**这个检查自己的发现**：`ok` 有证据、`blocked` 有反向证据、`unknown` 读不到或判不了；
* `required` 是**这一次判词是否依赖它**；`applicable` 是这条检查在当前上下文**成不成立**
  （例如 `small` 范围不需要专项批准 ⇒ 不适用，且明写「不适用 ≠ 已批准」）；
* `ok = 没有任何 required 的项目是 blocked 或 unknown`。⛔ `unknown` 永不计绿。
* `exp_link`：`linked` / `unlinked` / `unknown` / `not_applicable`。未关联的检查在
  `unlinked_items` 里点名 —— 这就是 E2 的「未关联时明确说未入台账」。

它不是什么
==========

* ⛔ **不只是个装饰**：没给的宿主证据就是 `unknown`（例如没注入 `host` ⇒ `monitoring.*`
  一律 unknown），⛔ 不会因为「没报错」就变绿。
* ⛔ **不是执行者**：不 `prepare` / `launch` / `review` / `ack` / `send` / `tick`，一个字节都不写。
* ⛔ **不造证明**：本模块从不写 `verified`；需要宿主证明的项，没有注入就是 unknown。
* ⛔ **不是审批**：`review.continuation` 里 `needs_approval=true` 只是**声明**，
  `declared_only=true`；真实批准只看 `decisions.approval`（要已批准决策才算绿）。
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3

from kernel.autoresearch.decisions import DecisionError, DecisionService
from kernel.autoresearch.issues import Issues
from kernel.autoresearch.monitoring import Monitoring, MonitoringError
from kernel.autoresearch import reporting as reporting_module
from kernel.autoresearch.questions import Questions, QuestionError
from kernel.autoresearch import review_continuation
from kernel.autoresearch.runtime.store import ReadonlyStore, StoreError
from kernel.autoresearch.workflow import Workflow, WorkflowError

STATUSES = ('ok', 'blocked', 'unknown')
#: 需要专项批准的执行范围；`none`/`small` 由授权与预算覆盖，不需要每轮批准。
APPROVAL_SCOPES = ('large', 'submission')
#: 需要真实远端资源租约的执行范围（`local` 不需要，⛔ 不造假的远端资源）。
REMOTE_SCOPES = ('remote', 'ssh')
#: 只读判「资源键现在能不能拿」需要的表（缺任一张 ⇒ 不可判，⛔ 不读成 free）。
LEASE_TABLES = ('resources', 'scheduler_resource_quarantine')
#: 判为「已批准」的决策状态与裁决。
APPROVED_STATE, APPROVED_RESOLUTION = 'resolved', 'user_approved'


class AssuranceError(ValueError):
    """输入错误（工作区不存在、参数互斥等）。检查项本身的问题一律进 items，不抛。"""


def _iso(stamp):
    if stamp is None:
        return None
    return datetime.fromtimestamp(float(stamp), timezone.utc).astimezone().isoformat()


def _token(value, label, *, limit=128):
    if not isinstance(value, str) or not value.strip():
        raise AssuranceError(f'{label} 必须是非空字符串')
    text = value.strip()
    if any(char.isspace() for char in text):
        raise AssuranceError(f'{label} 不能含空白')
    if len(text) > limit:
        raise AssuranceError(f'{label} 过长（>{limit} 字符）')
    return text


def _item(name, status, *, exp_id=None, exp_link='not_applicable', source, evidence=None,
          required=True, applicable=True, problems=None, notes=None):
    if status not in STATUSES:
        raise AssuranceError(f'内部错误：item 状态非法 {status!r}')
    return {'name': name, 'status': status, 'exp_id': exp_id, 'exp_link': exp_link,
            'source': source, 'evidence': dict(evidence or {}), 'required': bool(required),
            'applicable': bool(applicable), 'problems': list(problems or []),
            'notes': list(notes or [])}


def _link_of(exp_id, *, kind='linked', note=None):
    """这个检查跟实验的关系：有 exp 就 linked，没有就明说 unlinked。"""
    if exp_id is None:
        return 'unlinked'
    return kind


# ---------------------------------------------------------------------------
# 各来源的检查项
# ---------------------------------------------------------------------------


def _workflow_items(workflow, *, topic, exp_id):
    """`workflow.preparation` 与 `workflow.review`（缺复盘单列一项）。"""
    scope = bool(topic or exp_id)
    link = _link_of(exp_id)
    link_note = ('按 `plan_id` 过滤（框架既有约定：`Workflow._authorization_gate` 把 '
                 '`plan_id` 当实验号传给准入）') if exp_id else None
    try:
        status = workflow.status(topic=topic, plan_id=exp_id)
    except (WorkflowError, StoreError, sqlite3.Error) as exc:
        problem = f'执行主链状态读不到（{type(exc).__name__}: {exc}）⇒ 不可判'
        return ([_item('workflow.preparation', 'unknown', exp_id=exp_id, exp_link=link,
                       source='workflow', evidence={'kind': 'readonly_status'}, required=scope,
                       problems=[problem]),
                 _item('workflow.review', 'unknown', exp_id=exp_id, exp_link=link,
                       source='workflow', evidence={'kind': 'readonly_status'}, required=scope,
                       problems=[problem])], None)
    if not status.get('ok'):
        problem = '；'.join(status.get('problems') or ['执行主链状态不可判'])
        notes = [link_note] if link_note else []
        return ([_item('workflow.preparation', 'unknown', exp_id=exp_id, exp_link=link,
                       source='workflow', evidence={'db': status.get('db')}, required=scope,
                       problems=[problem], notes=notes),
                 _item('workflow.review', 'unknown', exp_id=exp_id, exp_link=link,
                       source='workflow', evidence={'db': status.get('db')}, required=scope,
                       problems=[problem], notes=notes)], None)
    preparations = status.get('preparations') or []
    runs = status.get('runs') or []
    pending = status.get('pending_reports') or []
    expired = [{'plan_id': item['plan_id'], 'prepared_id': item['prepared_id'],
                'problems': item['expiry_problems']} for item in preparations if item['expired']]
    next_round = status.get('next_round') or {}
    prep_problems = []
    if expired:
        prep_problems.append(f'{len(expired)} 份事前材料已过期 ⇒ 重新 prepare')
    prep_problems.extend(next_round.get('problems') or [])
    prep_evidence = {'preparations': len(preparations), 'expired': expired,
                     'runs': len(runs), 'pending_reports': pending,
                     'next_round_allowed': next_round.get('allowed')}
    notes = [link_note] if link_note else []
    applicable = bool(preparations or runs or topic or exp_id)
    prep_items = _item('workflow.preparation',
                       'blocked' if prep_problems else 'ok', exp_id=exp_id, exp_link=link,
                       source='workflow', evidence=prep_evidence, required=scope,
                       applicable=applicable, problems=prep_problems, notes=notes)
    if not runs:
        review = _item('workflow.review', 'ok', exp_id=exp_id, exp_link=link, source='workflow',
                       evidence={'runs': 0, 'pending_reports': []}, required=scope,
                       applicable=False, notes=(['还没有结束的运行为止，没有复盘可缺']
                                                + ([link_note] if link_note else [])))
    else:
        problems = [f'运行 {run_id} 已结束但还没有复盘（缺复盘 ⇒ 下一轮不许开）'
                    for run_id in pending]
        documentation = status.get('documentation_failures') or []
        notes = ([link_note] if link_note else []) + (
            [f'{len(documentation)} 个运行的文档层失败只作诊断，不覆盖退出码也不阻停']
            if documentation else [])
        review = _item('workflow.review', 'blocked' if problems else 'ok', exp_id=exp_id,
                       exp_link=link, source='workflow',
                       evidence={'runs': len(runs), 'pending_reports': pending,
                                 'documentation_failures': documentation},
                       required=scope, problems=problems, notes=notes)
    return [prep_items, review], status


def _monitoring_items(monitoring, *, topic, exp_id, owner_session):
    """`monitoring.intent`（待监控）与 `monitoring.launch`（起跑闸判词）。"""
    link = _link_of(exp_id)
    scope = bool(exp_id)
    base_notes = ['缺宿主适配器（host）或负责人会话时，意图与监控一律不可判 —— ⛔ 不当通过']
    if owner_session is None:
        problem = ('缺负责人会话（owner_session）：意图与过程监控按会话记账，读不到 ⇒ 不可判'
                   '（⛔ 不是「没有监控要求」）')
        return [_item('monitoring.intent', 'unknown', exp_id=exp_id, exp_link=link,
                      source='monitoring', evidence={'owner_session': None}, required=scope,
                      problems=[problem], notes=base_notes),
                _item('monitoring.launch', 'unknown', exp_id=exp_id, exp_link=link,
                      source='monitoring', evidence={'owner_session': None}, required=scope,
                      problems=[problem], notes=base_notes)]
    try:
        state = monitoring.status(owner_session, exp=exp_id or '')
    except MonitoringError as exc:
        problem = f'意图监控不可判（{type(exc).__name__}: {exc}）'
        return [_item('monitoring.intent', 'unknown', exp_id=exp_id, exp_link=link,
                      source='monitoring', evidence={'owner_session': owner_session},
                      required=scope, problems=[problem], notes=base_notes),
                _item('monitoring.launch', 'unknown', exp_id=exp_id, exp_link=link,
                      source='monitoring', evidence={'owner_session': owner_session},
                      required=scope, problems=[problem], notes=base_notes)]
    undecidable = bool(state.get('undecidable'))
    problems = list(state.get('problems') or [])
    pending = state.get('pending') or []
    unassessed = state.get('unassessed_user_updates') or []
    stale = state.get('report_stale') or []
    issues = state.get('issues') or {}
    unresponded = issues.get('unresponded') or []
    blocking = issues.get('blocking_open') or []
    for label, values in (('待审触发', pending), ('未评估的用户原话', unassessed),
                          ('过期报告', stale), ('未处置意见', unresponded),
                          ('未关闭的阻断意见', blocking)):
        if values:
            problems.append(f'{label}：{len(values)} 项')
    intent_status = 'unknown' if undecidable else ('blocked' if problems else 'ok')
    intent = _item('monitoring.intent', intent_status, exp_id=exp_id, exp_link=link,
                   source='monitoring',
                   evidence={'owner_session': owner_session, 'state': state.get('state'),
                             'registered': state.get('registered'),
                             'intent_id': state.get('intent_id'),
                             'sync_watermark': state.get('sync_watermark'),
                             'assessed_watermark': state.get('assessed_watermark'),
                             'pending': pending, 'unassessed_user_updates': unassessed,
                             'report_id': state.get('report_id'), 'report_stale': stale,
                             'unresponded': unresponded, 'blocking_open': blocking,
                             'host_configured': state.get('host_configured')},
                   required=scope, problems=problems, notes=base_notes)
    if not exp_id:
        launch = _item('monitoring.launch', 'unknown', exp_id=None, exp_link='unlinked',
                       source='monitoring', evidence={'action': 'launch'},
                       required=False, applicable=False,
                       notes=['没有实验号 ⇒ 不判起跑闸（⛔ 不是放行）'])
        return [intent, launch]
    try:
        gate = monitoring.check_start(owner_session, exp_id, action='launch')
    except MonitoringError as exc:
        return [intent, _item('monitoring.launch', 'unknown', exp_id=exp_id, exp_link=link,
                              source='monitoring', evidence={'action': 'launch'}, required=True,
                              problems=[f'起跑闸不可判（{type(exc).__name__}: {exc}）'],
                              notes=base_notes)]
    gate_problems = list(gate.get('problems') or [])
    if gate.get('undecidable') and not gate_problems:
        gate_problems = ['起跑闸不可判（缺宿主证据）']
    launch_status = 'unknown' if gate.get('undecidable') else (
        'blocked' if not gate.get('allow') else 'ok')
    launch = _item('monitoring.launch', launch_status, exp_id=exp_id, exp_link=link,
                   source='monitoring',
                   evidence={'action': 'launch', 'allow': gate.get('allow'),
                             'state': gate.get('state'), 'exempt': gate.get('exempt'),
                             'problems': gate_problems, 'note': gate.get('note')},
                   required=True, problems=gate_problems, notes=base_notes)
    return [intent, launch]


def _frozen_plan(db, exp_id):
    """从只读库里读这个实验**当前冻结的计划**（`gov_plans` 最新一版）。`(plan, why)`。

    ⛔ 不构造可写服务、不跑治理构造、不迁移 schema；读不到就说读不到。
    """
    if not db.is_file():
        return None, f'状态库不存在：{db}（只读模式不建库）'
    try:
        conn = ReadonlyStore(db).connect()
    except StoreError as exc:
        return None, f'状态库不可读（{exc}）'
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if 'gov_plans' not in tables:
            return None, '状态库没有 gov_plans 表（只读模式不迁移）'
        row = conn.execute('SELECT plan_json FROM gov_plans WHERE plan_id=? '
                           'ORDER BY version DESC LIMIT 1', (exp_id,)).fetchone()
    except sqlite3.Error as exc:
        return None, f'计划读不到（{type(exc).__name__}: {exc}）'
    finally:
        conn.close()
    if row is None:
        return None, f'没有 plan_id={exp_id} 的冻结计划'
    try:
        plan = json.loads(row[0])
    except (TypeError, ValueError):
        return None, 'plan_json 不是合法 JSON ⇒ 不可判'
    if not isinstance(plan, dict):
        return None, 'plan_json 不是对象 ⇒ 不可判'
    return plan, None


def _plan_scope(db, exp_id):
    """从冻结计划里读真实执行范围（只读）。读不到 ⇒ `(None, why)`，⛔ 不猜。"""
    plan, why = _frozen_plan(db, exp_id)
    if plan is None:
        return None, why
    declared = plan.get('authorization')
    scope = None
    if isinstance(declared, dict):
        scope = declared.get('scope')
    if scope is None:
        scope = plan.get('scope')
    scope = str(scope).strip().lower() if isinstance(scope, str) else None
    return scope, None


def _reads(db):
    """一次性只读读连接（`ReadonlyStore`：干净 WAL 库上不创建 sidecar）。"""
    return ReadonlyStore(db).connect()


def _audit_item(db, *, exp_id, status):
    """`workflow.audit`：**已启动**展示当次历史审计绑定；**未启动**才核当前独立审计。

    ⛔ 不对已启动/已完成的轮次强求「新的起跑可行」；也⛔ 不把缺审计读成绿。
    """
    link = _link_of(exp_id)
    if exp_id is None:
        return _item('workflow.audit', 'unknown', exp_id=None, exp_link='unlinked',
                     source='workflow', required=False, applicable=False,
                     notes=['没有实验号 ⇒ 不判审计状态（⛔ 不是「已审过」）'])
    runs = [] if status is None else (status.get('runs') or [])
    if runs:
        latest = runs[-1]
        try:
            conn = _reads(db)
            try:
                row = conn.execute('SELECT gates_json FROM wf_runs WHERE run_id=?',
                                   (latest['run_id'],)).fetchone()
            finally:
                conn.close()
        except (StoreError, sqlite3.Error) as exc:
            return _item('workflow.audit', 'unknown', exp_id=exp_id, exp_link=link,
                         source='workflow', evidence={'run_id': latest['run_id'],
                                                      'historical': True},
                         required=True,
                         problems=[f'运行记录里的审计闸读不到（{type(exc).__name__}）⇒ 不可判'])
        gates = {}
        if row is not None:
            try:
                gates = json.loads(row['gates_json'] or '{}')
            except (TypeError, ValueError):
                gates = {}
        if not isinstance(gates, dict):
            gates = {}
        # `wf_runs.gates_json` 是**扁平**的：`audit` 来自 `_gate`，`audit_recheck` 来自
        # 启动事务内的那次只读复核（两者都是当时真实写下的事实）。
        audit = gates.get('audit')
        recheck_audit = gates.get('audit_recheck')
        evidence = {'run_id': latest['run_id'], 'historical': True,
                    'audit': audit if isinstance(audit, dict) else None,
                    'audit_recheck': (recheck_audit if isinstance(recheck_audit, dict)
                                      else None),
                    'gates_keys': sorted(gates)}
        if not isinstance(audit, dict):
            return _item('workflow.audit', 'unknown', exp_id=exp_id, exp_link=link,
                         source='workflow', evidence=evidence, required=True,
                         problems=['这次运行没有记录审计闸的绑定：不能证明起跑前有独立审计'
                                   '（⛔ 历史缺记录不补造）'])
        problems = []
        if audit.get('ok') is not True:
            problems.append('这次运行的审计闸不是 ok：起跑前没有合格的独立审计')
        if isinstance(recheck_audit, dict) and recheck_audit.get('ok') is not True:
            problems.append('启动事务内的审计复核不是 ok：' +
                            '；'.join(recheck_audit.get('problems') or ['不可判']))
        return _item('workflow.audit', 'blocked' if problems else 'ok', exp_id=exp_id,
                     exp_link=link, source='workflow', evidence=evidence, required=True,
                     problems=problems,
                     notes=['已启动的轮次只展示**历史**审计绑定；'
                            '⛔ 不要求它现在仍能起跑'])
    # 未启动：核当前独立审计（只读治理投影，不是新写一套 gate）
    try:
        from kernel.autoresearch.workflow import _readonly_governance
        gov = _readonly_governance(db)
        state = gov.audit_state(exp_id)
    except Exception as exc:  # noqa: BLE001 - 审计读不到只能 unknown
        return _item('workflow.audit', 'unknown', exp_id=exp_id, exp_link=link,
                     source='workflow', evidence={'historical': False}, required=True,
                     problems=[f'当前审计状态不可判（{type(exc).__name__}）⇒ 不得起跑'])
    evidence = {'historical': False, 'decision': state.get('decision'),
                'audit_decision': state.get('audit_decision'),
                'independence': state.get('independence'),
                'audits_used': state.get('audits_used'), 'limit': state.get('limit'),
                'key_fingerprint': state.get('key_fingerprint')}
    if state.get('ok') is True:
        return _item('workflow.audit', 'ok', exp_id=exp_id, exp_link=link,
                     source='workflow', evidence=evidence, required=True,
                     notes=['未启动轮次：当前有合格的独立审计（这是起跑前提之一）'])
    problems = list(state.get('problems') or ['当前没有合格的独立审计'])
    return _item('workflow.audit', 'blocked', exp_id=exp_id, exp_link=link,
                 source='workflow', evidence=evidence, required=True,
                 problems=problems,
                 notes=['未启动轮次缺当前独立审计 ⇒ 不得起跑（⛔ 历史豁免不算审过）'])


def _preparation_owner(db, plan_id):
    """当前准备记录的 owner（只读）。⛔ 不编造会话证据；读不到就是 None。"""
    if not db.is_file():
        return None, '状态库不存在'
    try:
        conn = _reads(db)
        try:
            row = conn.execute('SELECT owner_session FROM wf_preparations WHERE plan_id=? '
                               'ORDER BY created_at DESC, rowid DESC LIMIT 1',
                               (plan_id,)).fetchone()
        finally:
            conn.close()
    except (StoreError, sqlite3.Error) as exc:
        return None, f'准备记录读不到（{type(exc).__name__}）'
    if row is None or not str(row[0] or '').strip():
        return None, '没有准备记录或它没有 owner_session'
    return str(row[0]).strip(), None


def _budget_gate_resolver(gate, *, root, db):
    """拿到**真实**预算判据：`admission.Admission.check_budget`。

    ⛔ 本模块不定义第二套「live 预留」判据；`services['budget_gate']` 只给测试/宿主注入，
    生产不传就用 `Admission.check_budget`（只读）。返回 `(callable, why_unavailable)`。
    """
    if gate is not None:
        function = gate if callable(gate) else getattr(gate, 'check_budget', None)
        if not callable(function):
            return None, '注入的 budget_gate 没有可调用的 check_budget'
        return function, None
    try:
        from kernel.autoresearch.admission import Admission
    except Exception as exc:  # noqa: BLE001 - 导不进来只能 unknown
        return None, f'admission 不可导入（{type(exc).__name__}）'
    function = getattr(Admission(root, store=ReadonlyStore(db)), 'check_budget', None)
    if not callable(function):
        return None, ('admission.Admission 还没有 check_budget（真实预算判据未接线）'
                      '⇒ 本层不用本地 rows 自定判据')
    return function, None


def _budget_item(db, *, root, exp_id, started, frozen, owner_session=None, now=None,
                 gate=None):
    """`budget.reservation`：未启动用**真实预算判据**；已启动只看历史结算（诊断，不阻收尾）。

    ⚠️ 未启动的判定**只有一份**：`Admission.check_budget(plan, owner_session, exp_id)`。
    本层只负责把它的判词翻译成 item 状态，⛔ 不自己写 live / units / expiry 判据。
    `wf_preparations.owner_session` 只作为 owner 的**来源**（非空即可），⛔ 不当会话证明。
    """
    link = _link_of(exp_id)
    if exp_id is None:
        return _item('budget.reservation', 'unknown', exp_id=None, exp_link='unlinked',
                     source='budget', required=False, applicable=False,
                     notes=['没有实验号 ⇒ 不判预算（⛔ 不是「不用预算」）'])
    if frozen is None:
        return _item('budget.reservation', 'unknown', exp_id=exp_id, exp_link=link,
                     source='budget', evidence={'frozen_plan': False}, required=False,
                     applicable=False,
                     notes=['还没有冻结计划 ⇒ 没有可核的预算需求'
                            '（⛔ 不是「已经预留」）'])
    declared = frozen.get('authorization') if isinstance(frozen.get('authorization'), dict) else {}
    series_ref = declared.get('series_id')
    reservation_ref = declared.get('reservation_id')
    try:
        conn = _reads(db)
        try:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            missing = [name for name in ('budget_accounts', 'budget_reservations')
                       if name not in tables]
            if missing:
                return _item('budget.reservation', 'unknown', exp_id=exp_id, exp_link=link,
                             source='budget', evidence={'missing_tables': missing},
                             required=not started,
                             problems=['状态库缺预算表：' + '、'.join(missing)
                                       + '（只读模式不建表）⇒ 不可判'])
            rows = [dict(row) for row in conn.execute(
                'SELECT * FROM budget_reservations WHERE exp_id=? ORDER BY created_at',
                (exp_id,))]
            accounts = {}
            for row in rows:
                account = conn.execute('SELECT * FROM budget_accounts WHERE series_id=?',
                                       (row['series_id'],)).fetchone()
                if account is not None:
                    accounts[row['series_id']] = dict(account)
        finally:
            conn.close()
    except (StoreError, sqlite3.Error) as exc:
        return _item('budget.reservation', 'unknown', exp_id=exp_id, exp_link=link,
                     source='budget', required=not started,
                     problems=[f'预算台账读不到（{type(exc).__name__}）⇒ 不可判'])
    evidence = {'series_id': series_ref, 'reservation_id': reservation_ref,
                'declared_scope': declared.get('scope'),
                'reservations': [{'id': row['id'], 'series_id': row['series_id'],
                                  'scope': row['scope'], 'units': row['units'],
                                  'actual_units': row['actual_units'],
                                  'status': row['status']} for row in rows],
                'accounts': {key: {'active': value.get('active'),
                                   'expires_at': value.get('expires_at'),
                                   'total_units': value.get('total_units'),
                                   'reserved_units': value.get('reserved_units'),
                                   'spent_units': value.get('spent_units')}
                             for key, value in accounts.items()},
                'started': bool(started)}
    if started:
        # 已启动/已完成：**诊断**性质，不阻安全收尾（真实费用未知不等于可以假结算）。
        notes = ['预算单位是部署方定义的；本层只读台账，不签发、不结算',
                 '已启动轮次的预算是**历史诊断**：unknown 只说明「不能假结算」，'
                 '⛔ 不阻断安全收尾']
        if not rows:
            return _item('budget.reservation', 'unknown', exp_id=exp_id, exp_link=link,
                         source='budget', evidence=evidence, required=False,
                         problems=['这一轮没有任何预算预留记录：不能证明它走过共享额度'
                                   '（⛔ 缺记录不补造、不当作已结算）'], notes=notes)
        statuses = {row['status'] for row in rows}
        if statuses <= {'settled', 'released'}:
            return _item('budget.reservation', 'ok', exp_id=exp_id, exp_link=link,
                         source='budget', evidence=evidence, required=False,
                         notes=notes + ['历史完成：预留已 settlement / release（只读台账为证）'])
        return _item('budget.reservation', 'unknown', exp_id=exp_id, exp_link=link,
                     source='budget', evidence=evidence, required=False,
                     problems=['有过启动的轮次里预算预留仍是 '
                               + '、'.join(sorted(statuses - {'settled', 'released'}))
                               + '：没有可信计量就不能算结算完成'], notes=notes)
    # 未启动：判词只来自真实预算判据
    function, why = _budget_gate_resolver(gate, root=root, db=db)
    source_owner, owner_why = _preparation_owner(db, exp_id)
    owner = (owner_session or source_owner or '')
    evidence.update({'gate': 'admission.Admission.check_budget',
                     'owner_session': owner or None,
                     'owner_source': ('caller' if owner_session else
                                      ('wf_preparations' if source_owner else None)),
                     'owner_reason': owner_why})
    if function is None:
        return _item('budget.reservation', 'unknown', exp_id=exp_id, exp_link=link,
                     source='budget', evidence=evidence, required=True,
                     problems=[str(why) + ' ⇒ 不可判（⛔ 不用本地 rows 自定 live 判据）'])
    if not owner:
        return _item('budget.reservation', 'unknown', exp_id=exp_id, exp_link=link,
                     source='budget', evidence=evidence, required=True,
                     problems=['没有可用的 owner_session（调用方没给，准备记录里也没有）'
                               '⇒ 真实预算判据无法核 ⇒ 不可判（⛔ 不编造会话证据）'])
    try:
        checked = function(frozen, owner, exp_id, now=now)
    except Exception as exc:  # noqa: BLE001 - 判据坏掉不等于核过
        return _item('budget.reservation', 'unknown', exp_id=exp_id, exp_link=link,
                     source='budget', evidence=evidence, required=True,
                     problems=[f'预算判据不可判（{type(exc).__name__}）⇒ 不得起跑'])
    if not isinstance(checked, dict):
        return _item('budget.reservation', 'unknown', exp_id=exp_id, exp_link=link,
                     source='budget', evidence=dict(evidence, gate_result=checked),
                     required=True,
                     problems=['预算判据返回值不可判（需要对象）⇒ 不得起跑'])
    verdict = checked.get('ok')
    if verdict is None:
        verdict = checked.get('allow')
    evidence['gate_result'] = {key: checked.get(key) for key in
                               ('ok', 'allow', 'scope', 'series_id', 'reservation_id',
                                'units', 'expires_at', 'problems', 'undecidable')}
    problems = [str(item) for item in (checked.get('problems') or [])]
    notes = ['预算单位是部署方定义的；本层只读台账，不签发、不结算',
             '判词来自 `Admission.check_budget`（真实预算判据），本层不另定 live 判据']
    if verdict is True:
        return _item('budget.reservation', 'ok', exp_id=exp_id, exp_link=link,
                     source='budget', evidence=evidence, required=True, notes=notes)
    if verdict is False:
        return _item('budget.reservation', 'blocked', exp_id=exp_id, exp_link=link,
                     source='budget', evidence=evidence, required=True,
                     problems=problems or ['真实预算判据判不放行（未给出原因）'], notes=notes)
    return _item('budget.reservation', 'unknown', exp_id=exp_id, exp_link=link,
                 source='budget', evidence=evidence, required=True,
                 problems=problems or ['预算判据返回值不可判（需要 ok|allow 布尔）'], notes=notes)


def _execution_scope(frozen, status):
    """这一轮的执行范围：优先冻结计划，其次最近一次运行记录里的 `execution_scope`。"""
    if isinstance(frozen, dict):
        execution = frozen.get('execution')
        if isinstance(execution, dict) and isinstance(execution.get('scope'), str):
            return execution['scope'].strip().lower(), 'plan.execution.scope'
        if isinstance(frozen.get('execution_scope'), str):
            return frozen['execution_scope'].strip().lower(), 'plan.execution_scope'
    if isinstance(frozen, dict) and isinstance(frozen.get('execution'), str):
        return frozen['execution'].strip().lower(), 'plan.execution'
    return None, '计划没有声明执行范围'


def _declared_resource_keys(frozen):
    """正式资源键只认 `plan.execution.remote.resources`（与 `Remote.check` 同源）。

    ⛔ 不读 `execution.resources` / 本地执行的资源声明去「猜」远端 key。
    """
    execution = frozen.get('execution') if isinstance(frozen, dict) else None
    if not isinstance(execution, dict):
        return []
    block = execution.get('remote')
    if not isinstance(block, dict):
        return []
    resources = block.get('resources')
    if not isinstance(resources, list):
        return []
    return [item.strip() for item in resources if isinstance(item, str) and item.strip()]


def _lease_view(db, resources):
    """资源租约现状（只读）：复用 `progress_remote._lease_view` 的**同一份**判据。

    父会话可能把它改名成公开 `lease_view`；两种名字都认，⛔ 不在这里另写一份状态机。
    只读预检**不 claim** 租约：取租约发生在 `remote start` 的写路径里。
    """
    from kernel.autoresearch import progress_remote
    function = getattr(progress_remote, 'lease_view', None) \
        or getattr(progress_remote, '_lease_view', None)
    if not callable(function):
        raise AssuranceError('progress_remote 没有提供 lease_view / _lease_view')
    return function(db, resources)


def _scheduler_tables(db):
    """读租约现状需要的表；缺任何一张 ⇒ 不可判（⛔ 不把「表都没有」读成 free）。"""
    try:
        conn = _reads(db)
        try:
            present = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
    except (StoreError, sqlite3.Error) as exc:
        return None, f'状态库不可读（{type(exc).__name__}）'
    missing = [name for name in LEASE_TABLES if name not in present]
    return (not missing), (('缺表：' + '、'.join(missing)) if missing else None)


def _lease_not_started(db, *, exp_id, frozen, link, scope, scope_source):
    """未启动的远端轮次：只核**声明**的资源键现状，⛔ 不要求本实验已经 claim 租约。

    取租约发生在 `remote start` 的同一个事务里；因此这里**只**判「声明的资源键现在是否
    可核为空闲」，判词与 `progress_remote.remote_preflight` 的 lease 段一致：
    `free → ok`、`held → blocked`、`quarantined → unknown`、`unconfigured / 其他 → unknown`。
    """
    keys = _declared_resource_keys(frozen)
    evidence = {'execution_scope': scope, 'scope_source': scope_source,
                'stage': 'not_started', 'declared_resources': keys}
    if not keys:
        return _item('remote.lease', 'unknown', exp_id=exp_id, exp_link=link, source='remote',
                     evidence=evidence, required=True,
                     problems=['计划没有在 execution.remote.resources 里声明正式资源键'
                               '⇒ 没有可核的资源租约，不得提交'
                               '（⛔ 不从本地执行声明猜远端 key）'])
    tables_ok, table_why = _scheduler_tables(db)
    if tables_ok is None:
        return _item('remote.lease', 'unknown', exp_id=exp_id, exp_link=link, source='remote',
                     evidence=evidence, required=True,
                     problems=[f'租约现状不可判：{table_why}'])
    if not tables_ok:
        return _item('remote.lease', 'unknown', exp_id=exp_id, exp_link=link, source='remote',
                     evidence=dict(evidence, missing_tables=table_why), required=True,
                     problems=[f'租约现状不可判：{table_why}（只读模式不建表）'])
    try:
        view = _lease_view(db, keys)
    except Exception as exc:  # noqa: BLE001 - 租约读不到只能 unknown
        return _item('remote.lease', 'unknown', exp_id=exp_id, exp_link=link, source='remote',
                     evidence=evidence, required=True,
                     problems=[f'租约现状不可判（{type(exc).__name__}）⇒ 不得提交'])
    evidence['lease_view'] = view
    note = ('只读预检不 claim 租约：取租约发生在 remote start；'
            'free = 本地台账里这些键没有被 task 持有、也没在 quarantine')
    status = view.get('status')
    details = view.get('details') or {}
    if status == 'free':
        return _item('remote.lease', 'ok', exp_id=exp_id, exp_link=link, source='remote',
                     evidence=evidence, required=True,
                     notes=['未启动：声明的正式资源键在本地只读台账里空闲', note])
    if status == 'held':
        holder = '、'.join(str(item.get('resource_key')) for item in details.get('held') or [])
        return _item('remote.lease', 'blocked', exp_id=exp_id, exp_link=link, source='remote',
                     evidence=evidence, required=True,
                     problems=['资源租约现在被其他任务持有：' + (holder or '、'.join(keys)), note])
    if status == 'quarantined':
        blocked_keys = '、'.join(str(item.get('resource_key'))
                                for item in details.get('quarantined') or [])
        return _item('remote.lease', 'unknown', exp_id=exp_id, exp_link=link, source='remote',
                     evidence=evidence, required=True,
                     problems=['资源在 quarantine（上一个付费任务结束未经核空闲）：'
                               + (blocked_keys or '、'.join(keys)),
                               'unknown 时资源租约必须保留；明确终态后才可用 remote release'])
    if status == 'unconfigured':
        return _item('remote.lease', 'unknown', exp_id=exp_id, exp_link=link, source='remote',
                     evidence=evidence, required=True,
                     problems=['没有可核的资源键：没有资源租约不得提交'])
    return _item('remote.lease', 'unknown', exp_id=exp_id, exp_link=link, source='remote',
                 evidence=evidence, required=True,
                 problems=[str(view.get('note') or '租约现状不可判')])


def _lease_item(root, db, *, exp_id, status, frozen):
    """`remote.lease`：远端执行要有可核的资源租约；`local` 范围明确不适用。

    只用 `RemoteWorkflow(readonly=True).status()` 这一个现成只读投影，⛔ 不另写 gate、
    ⛔ 不构造可写服务、⛔ 不联网（`query=False`）。
    """
    link = _link_of(exp_id)
    if exp_id is None:
        return _item('remote.lease', 'unknown', exp_id=None, exp_link='unlinked',
                     source='remote', required=False, applicable=False,
                     notes=['没有实验号 ⇒ 不判资源租约（⛔ 不是「没有租约风险」）'])
    scope, scope_source = _execution_scope(frozen, status)
    runs = [] if status is None else (status.get('runs') or [])
    gates_scopes = []
    if runs:
        try:
            conn = _reads(db)
            try:
                row = conn.execute('SELECT gates_json FROM wf_runs WHERE run_id=?',
                                   (runs[-1]['run_id'],)).fetchone()
            finally:
                conn.close()
            gates = json.loads((row['gates_json'] if row is not None else '{}') or '{}')
            target = gates.get('execution_target') if isinstance(gates, dict) else None
            if isinstance(target, dict) and isinstance(target.get('execution_scope'), str):
                gates_scopes.append(target['execution_scope'].strip().lower())
            execution = gates.get('execution') if isinstance(gates, dict) else None
            if isinstance(execution, dict) and isinstance(execution.get('scope'), str):
                gates_scopes.append(execution['scope'].strip().lower())
        except (StoreError, sqlite3.Error, TypeError, ValueError):
            gates_scopes = []
    if scope is None and gates_scopes:
        scope, scope_source = gates_scopes[-1], 'wf_runs.gates_json.target.execution_scope'
    if scope is None:
        return _item('remote.lease', 'unknown', exp_id=exp_id, exp_link=link,
                     source='remote', evidence={'execution_scope': None,
                                                'scope_source': scope_source},
                     required=False, applicable=False,
                     notes=['计划没有声明执行范围 ⇒ 不判是否需要远端资源租约'
                            '（⛔ 不是「local」）'])
    if scope not in REMOTE_SCOPES:
        return _item('remote.lease', 'ok', exp_id=exp_id, exp_link=link, source='remote',
                     evidence={'execution_scope': scope, 'scope_source': scope_source},
                     required=False, applicable=False,
                     notes=[f'执行范围是 {scope}（本地）⇒ 不需要远端资源租约'
                            '（⛔ 不是「远端资源已可用」）'])
    if not runs:
        # 未启动：还没有 claim 租约（取租约在 remote start 的写事务里），所以只核声明键的现状。
        return _lease_not_started(db, exp_id=exp_id, frozen=frozen, link=link, scope=scope,
                                  scope_source=scope_source)
    try:
        from kernel.autoresearch.remote_workflow import RemoteWorkflow
        projection = RemoteWorkflow(root, db=db, readonly=True).status(plan_id=exp_id)
    except Exception as exc:  # noqa: BLE001 - 远端投影读不到只能 unknown
        return _item('remote.lease', 'unknown', exp_id=exp_id, exp_link=link, source='remote',
                     evidence={'execution_scope': scope}, required=True,
                     problems=[f'远端投影不可判（{type(exc).__name__}）⇒ 不得起跑'])
    remote = projection.get('remote') or []
    evidence = {'execution_scope': scope, 'scope_source': scope_source,
                'remote_rows': [{'run_id': item.get('run_id'),
                                 'finished': item.get('finished'),
                                 'task': item.get('task'),
                                 'resources_held': item.get('resources_held'),
                                 'resources_quarantined': item.get('resources_quarantined'),
                                 'reservation': item.get('reservation'),
                                 'terminal_verified': item.get('terminal_verified')}
                                for item in remote],
                'query': False,
                'note': projection.get('resources_note')}
    if not remote:
        return _item('remote.lease', 'unknown', exp_id=exp_id, exp_link=link, source='remote',
                     evidence=evidence, required=True,
                     problems=['这一轮已经有运行记录，但没有本地资源租约绑定'
                               '⇒ 租约不可判（⛔ 不假设远端空闲）'],
                     notes=['unknown 时资源租约必须保留；明确终态后才可用 remote release'])
    quarantined = sorted({key for item in remote
                          for key in (item.get('resources_quarantined') or [])})
    held = sorted({key for item in remote for key in (item.get('resources_held') or [])})
    unfinished = [item for item in remote if not item.get('finished')]
    problems = []
    if quarantined:
        problems.append('远端资源被隔离（需人工核空闲）：' + '、'.join(quarantined))
    if unfinished:
        if any(item.get('task') is None for item in unfinished):
            problems.append('未结束的远端运行没有本地 task 租约记录 ⇒ 租约不可判')
        else:
            return _item('remote.lease', 'ok', exp_id=exp_id, exp_link=link, source='remote',
                         evidence=evidence, required=True,
                         notes=['远端运行进行中：本层只读，不核也不改资源租约；'
                                '终态前不得 release'])
    if held:
        problems.append('远端资源租约仍被持有：⛔ 不能声称历史完成（等明确终态 / 人工核空闲）')
    if problems:
        return _item('remote.lease', 'unknown', exp_id=exp_id, exp_link=link, source='remote',
                     evidence=evidence, required=True, problems=problems,
                     notes=['unknown 时资源租约必须保留；明确终态后才可用 remote release'])
    return _item('remote.lease', 'ok', exp_id=exp_id, exp_link=link, source='remote',
                 evidence=evidence, required=True,
                 notes=['历史完成：没有持有或隔离的远端资源租约（只读投影为证）'])


def _decisions_items(service, *, db, exp_id, now, frozen_plan=None):
    """`decisions.inbound`（超期入向）与 `decisions.approval`（所需批准 / 隔离 / 未送达）。"""
    link = _link_of(exp_id)
    scope_flag = bool(exp_id)
    inbound_notes = ['超期按 304 s 文字回复窗口判；卡片点击不是文字回复']
    try:
        reply = service.reply_status(exp_id, now=now)
    except DecisionError as exc:
        problem = f'入向回复窗口读不到（{type(exc).__name__}: {exc}）⇒ 不可判'
        inbound = _item('decisions.inbound', 'unknown', exp_id=exp_id, exp_link=link,
                        source='decisions', evidence={'kind': 'reply_status'},
                        required=scope_flag, problems=[problem], notes=inbound_notes)
        approval = _item('decisions.approval', 'unknown', exp_id=exp_id, exp_link=link,
                         source='decisions', evidence={'kind': 'readonly_status'},
                         required=scope_flag, problems=[problem])
        return [inbound, approval]
    if reply.get('undecidable'):
        problem = '；'.join(reply.get('problems') or ['决策台账不可判'])
        inbound = _item('decisions.inbound', 'unknown', exp_id=exp_id, exp_link=link,
                        source='decisions', evidence={'undecidable': True}, required=scope_flag,
                        problems=[problem], notes=inbound_notes)
        # 审批项不在这里提前退出：计划范围若是 small/none，它本来就该是「不适用」。
    else:
        overdue = reply.get('overdue') or []
        problems = [f'超期未回复入向：{item["event_id"]}（{item.get("why") or "超过回复窗口"}）'
                    for item in overdue]
        inbound = _item('decisions.inbound', 'blocked' if overdue else 'ok', exp_id=exp_id,
                        exp_link=link, source='decisions',
                        evidence={'text_events': reply.get('text_events'),
                                  'card_events': reply.get('card_events'),
                                  'replied': reply.get('replied'),
                                  'pending': reply.get('pending'),
                                  'grace_sec': reply.get('grace_sec'),
                                  'overdue': [item['event_id'] for item in overdue],
                                  'unanswerable': [item['event_id']
                                                   for item in (reply.get('unanswerable') or [])]},
                        required=scope_flag, problems=problems, notes=inbound_notes)

    # 计划范围：先看这个实验**真实计划**声明的范围；再看该实验已有决策自报的范围。
    if isinstance(frozen_plan, dict):
        declared = frozen_plan.get('authorization')
        raw = declared.get('scope') if isinstance(declared, dict) else None
        if raw is None:
            raw = frozen_plan.get('scope')
        declared_scope = str(raw).strip().lower() if isinstance(raw, str) else None
        why = None
    else:
        declared_scope, why = _plan_scope(db, exp_id) if exp_id else (None, '没有实验号')
    try:
        status = service.status(exp_id=exp_id)
    except DecisionError as exc:
        status = {'ok': False, 'undecidable': True,
                  'problems': [f'决策台账读不到（{type(exc).__name__}: {exc}）']}
    undecidable = bool(status.get('undecidable')) or 'decisions' not in status
    decisions = [] if undecidable else (status.get('decisions') or [])
    decision_scopes = sorted({str(item.get('scope')) for item in decisions
                              if item.get('scope')})
    scope = declared_scope or (decision_scopes[0] if len(decision_scopes) == 1 else None)
    approved = [item for item in decisions
                if item.get('state') == APPROVED_STATE
                and item.get('resolution') == APPROVED_RESOLUTION]
    quarantined = [item['decision_id'] for item in decisions
                   if item.get('state') == 'quarantined']
    undelivered = [item['decision_id'] for item in decisions
                   if item.get('state') in ('prepared', 'send_failed')]
    broken = [{'decision_id': item['decision_id'], 'problems': item.get('problems') or []}
              for item in decisions if item.get('problems')]
    evidence = {'plan_scope': scope, 'plan_scope_reason': why,
                'decision_scopes': decision_scopes,
                'decisions': len(decisions), 'undecidable': undecidable,
                'approved': [{'decision_id': item['decision_id'], 'scope': item.get('scope'),
                              'resolution': item.get('resolution')} for item in approved],
                'quarantined': quarantined, 'undelivered': undelivered,
                'broken_chains': broken}
    notes = ['`small`/`none` 范围不需要每轮专项批准；审批链的成立仍由决策服务自己判']
    if scope is None:
        # 既没有真实计划范围、也没有单一决策范围 ⇒ 不知道要不要批，⛔ 不当成「不需要批」。
        return [inbound, _item('decisions.approval', 'unknown', exp_id=exp_id, exp_link=link,
                               source='decisions', evidence=evidence, required=False,
                               applicable=False,
                               problems=['没有读到该实验的真实计划范围（' + str(why) + '）'
                                         '⇒ 本实例不判「需不需要批准」，⛔ 也不当成已批准'],
                               notes=notes)]
    if scope not in APPROVAL_SCOPES:
        # 范围不需要专项批准 ⇒ 这一项**不适用**（⛔ 不是「已批准」）。
        problems = []
        if not undecidable:
            if quarantined:
                problems.append('交付结果不确定被隔离：' + '、'.join(quarantined)
                                + '（⛔ 不自动重发）')
            if undelivered:
                problems.append('决策还没送达：' + '、'.join(undelivered))
            if broken:
                problems.append('审批链不完整：'
                                + '、'.join(item['decision_id'] for item in broken))
        if undecidable:
            notes.append('决策台账不可判：范围不需要专项批准，所以不判审批；'
                         '隔离与送达也没核（⛔ 不能当成「没有异常」）')
        approval = _item('decisions.approval', 'blocked' if problems else 'ok', exp_id=exp_id,
                         exp_link=link, source='decisions', evidence=evidence,
                         required=scope_flag and bool(problems),
                         applicable=bool(problems), problems=problems, notes=notes)
        if not problems:
            approval['notes'].append(
                f'不适用：范围 {scope or "未知"} 不在 {"/".join(APPROVAL_SCOPES)} 之列 ⇒ '
                '不需要专项批准（这只是不适用，⛔ 不是「已批准」）')
        return [inbound, approval]
    problems = []
    if undecidable:
        # 需要批准但台账不可判 ⇒ 不绿（⛔ 缺 schema 不当作「没有待批」）。
        problems.extend(status.get('problems') or ['决策台账不可判，无法核本章节的批准'])
        return [inbound, _item('decisions.approval', 'unknown', exp_id=exp_id, exp_link=link,
                               source='decisions', evidence=evidence,
                               required=scope_flag, problems=problems, notes=notes)]
    if not approved:
        problems.append(f'范围 {scope} 的下一步还没有本实验的已批准决策 ⇒ 不得起跑/提交')
    if quarantined:
        problems.append('交付结果不确定被隔离：' + '、'.join(quarantined))
    if undelivered:
        problems.append('决策还没送达：' + '、'.join(undelivered))
    approval = _item('decisions.approval', 'blocked' if problems else 'ok', exp_id=exp_id,
                     exp_link=link, source='decisions', evidence=evidence,
                     required=scope_flag, problems=problems, notes=notes)
    return [inbound, approval]


def _issues_items(issues, *, exp_id):
    """`issues.registry`（未结问题）与 `issues.exp_link`（证据/问题关联实验）。"""
    try:
        ledger_events = issues.events()
    except ValueError as exc:
        problem = f'问题台账不可读（{exc}）⇒ 不可判'
        return [_item('issues.registry', 'unknown', exp_id=exp_id, exp_link=_link_of(exp_id),
                      source='issues', evidence={'kind': 'readonly_events'}, required=bool(exp_id),
                      problems=[problem]),
                _item('issues.exp_link', 'unknown', exp_id=exp_id, exp_link=_link_of(exp_id),
                      source='issues', evidence={'kind': 'readonly_events'}, required=False,
                      problems=[problem])]
    ledger_missing = not ledger_events
    scoped = issues.status(exp_id=exp_id) if exp_id else issues.status()
    all_items = issues.status()
    unresolved = [item for item in scoped if item.get('state') in ('open', 'fixed')]
    closed = [item for item in scoped if item.get('state') == 'closed']
    # 「未入台账」：没有任何有效实验关联的记录。⛔ 不把它们算进本实验的判词，但要点名。
    not_counted = [{'issue_id': item['issue_id'], 'state': item.get('state'),
                    'exp_link': item.get('exp_link'), 'exp_id': item.get('exp_id')}
                   for item in all_items if not item.get('effective_exps')]
    problems = [f'{item["issue_id"]}（{item.get("state")}，负责人 {item.get("owner")}）：'
                f'{item.get("summary")}' for item in unresolved]
    evidence = {'issues': len(all_items), 'in_scope': len(scoped),
                'unresolved': [item['issue_id'] for item in unresolved],
                'closed': [item['issue_id'] for item in closed],
                'not_counted': not_counted, 'ledger_missing': ledger_missing}
    notes = ['已结案（closed）的问题不挡新轮；只有 open / fixed 算未结']
    if ledger_missing:
        registry = _item('issues.registry', 'unknown', exp_id=exp_id, exp_link=_link_of(exp_id),
                         source='issues', evidence=evidence, required=bool(exp_id),
                         problems=['问题台账还没有任何记录：无法区分「没有已知问题」与'
                                   '「检查从没登记过」⇒ 不可判（⛔ 不当作绿）'],
                         notes=notes)
    else:
        registry = _item('issues.registry', 'blocked' if problems else 'ok', exp_id=exp_id,
                         exp_link=_link_of(exp_id), source='issues', evidence=evidence,
                         required=bool(exp_id), problems=problems, notes=notes)
    link_notes = ['旧记录的 payload 里没有 exp_id 键 ⇒ 投影成 unknown；⛔ 不猜归属、⛔ 不改写历史。'
                  '要把它算进某个实验，用 `issues_cli link --issue … --exp … --text … --source …`。']
    if ledger_missing:
        link_notes.append('问题台账还没有任何记录：这条只在有记录时才说明「未入台账」')
    if not_counted:
        link_item = _item('issues.exp_link', 'unknown', exp_id=exp_id,
                          exp_link=_link_of(exp_id), source='issues', evidence=evidence,
                          required=False,
                          problems=[f'{len(not_counted)} 条问题记录没有关联实验 ⇒ 未入本台账判词'],
                          notes=link_notes)
    else:
        link_item = _item('issues.exp_link', 'ok', exp_id=exp_id, exp_link=_link_of(exp_id),
                          source='issues', evidence=evidence, required=False, notes=link_notes)
    return [registry, link_item]


def _questions_items(questions, *, exp_id):
    """`questions.open`：待答 / 超期 / 只有 declared（⛔ declared 不算已回答）。

    默认**诊断**（`required=False`）：问了普通问题不代表实验要停等。只有 `blocking=True`
    且关联本实验的问题才挡住「依赖它的下一步」。⛔ 也不能把问题超期当成 D2（那是我们欠用户的入向）。
    """
    link = _link_of(exp_id)
    try:
        state = questions.open_questions(exp_id)
    except QuestionError as exc:
        return _item('questions.open', 'unknown', exp_id=exp_id, exp_link=link,
                     source='questions', evidence={'kind': 'open_questions'},
                     required=False,
                     problems=[f'提问台账读不到（{type(exc).__name__}: {exc}）⇒ 不可判'])
    if state.get('undecidable'):
        return _item('questions.open', 'unknown', exp_id=exp_id, exp_link=link,
                     source='questions', evidence={'undecidable': True},
                     required=False, problems=list(state.get('problems') or []))
    opened = state.get('open') or []
    overdue = state.get('overdue') or []
    blocking_overdue = state.get('blocking_overdue') or []
    blocking_open = state.get('blocking_open') or []
    declared = state.get('declared_only') or []
    unlinked = state.get('unlinked') or []
    blocking = blocking_open
    problems = list(state.get('problems') or [])
    problems.extend(f'{item["question_id"]} 被声明为 blocking，答复前依赖它的动作不得开始'
                    for item in blocking_open if item not in blocking_overdue)
    notes = list(state.get('diagnostics') or [])
    if opened:
        notes.append('待答问题：' + '、'.join(item['question_id'] for item in opened))
    if declared:
        notes.append('只有手工文字（declared，⛔ 不算用户已回答）：'
                     + '、'.join(item['question_id'] for item in declared))
    if unlinked:
        notes.append('没有实验关联的旧提问（unknown/unlinked，未计入本实验）：'
                     + '、'.join(item['question_id'] for item in unlinked))
    return _item('questions.open', 'blocked' if blocking else 'ok', exp_id=exp_id, exp_link=link,
                 source='questions',
                 evidence={'open': [item['question_id'] for item in opened],
                           'overdue': [item['question_id'] for item in overdue],
                           'blocking_open': [item['question_id'] for item in blocking_open],
                           'blocking_overdue': [item['question_id']
                                                for item in blocking_overdue],
                           'declared_only': [item['question_id'] for item in declared],
                           'unlinked': [item['question_id'] for item in unlinked],
                           'with_deadline': [item['question_id'] for item in opened
                                             if item.get('deadline')],
                           'no_deadline': [item['question_id'] for item in opened
                                           if not item.get('deadline')]},
                 required=bool(exp_id) and bool(blocking), problems=problems, notes=notes)


def _reporting_items(reporting, root, *, topic, exp_id):
    """`reporting.page`（页面交付）与 `reporting.round`（整轮终态 + 负责人判断）。"""
    link = _link_of(exp_id)
    if topic is None:
        page = _item('reporting.page', 'ok', exp_id=exp_id, exp_link=link, source='reporting',
                     evidence={'topic': None}, required=False, applicable=False,
                     notes=['没有专题号 ⇒ 不判人读页（⛔ 不是「页面已验收」）'])
        round_item = _item('reporting.round', 'ok', exp_id=exp_id, exp_link=link,
                           source='reporting', evidence={'topic': None}, required=False,
                           applicable=False,
                           notes=['没有专题号 ⇒ 不判整轮交付（⛔ 不是「整轮已交付」）'])
        return [page, round_item]
    try:
        check = reporting.check(Path(root), topic, required=False)
    except (reporting_module.ReportingError, OSError, ValueError) as exc:
        page = _item('reporting.page', 'unknown', exp_id=exp_id, exp_link=link,
                     source='reporting', evidence={'topic': topic}, required=True,
                     problems=[f'人读页检查读不到（{type(exc).__name__}: {exc}）⇒ 不可判'])
        round_item = _item('reporting.round', 'unknown', exp_id=exp_id, exp_link=link,
                           source='reporting', evidence={'topic': topic},
                           required=bool(exp_id),
                           problems=[f'整轮交付检查读不到（{type(exc).__name__}: {exc}）'])
        return [page, round_item]
    evidence = {'topic': topic, 'state': check.get('state'),
                'legacy_exempt': check.get('legacy_exempt'),
                'page': check.get('page'), 'task_state': check.get('task_state'),
                'source_fingerprint': check.get('source_fingerprint'),
                'receipt': check.get('receipt')}
    problems = list(check.get('problems') or [])
    warnings = list(check.get('warnings') or [])
    if check.get('state') == '未开始' or (
            not (check.get('page') or {}).get('exists')
            and check.get('task_state') is None and not check.get('receipt')):
        page = _item('reporting.page', 'ok', exp_id=exp_id, exp_link=link,
                     source='reporting', evidence=evidence, required=False, applicable=False,
                     notes=[f'人读页还没有开始（state={check.get("state")}）⇒ 不判'
                            '（⛔ 不是「已验收」）'] + warnings)
    elif check.get('legacy_exempt'):
        page = _item('reporting.page', 'unknown', exp_id=exp_id, exp_link=link,
                     source='reporting', evidence=evidence, required=False,
                     problems=['历史页未接入来源回执：正文未验证（旧页只读兼容）'], notes=warnings)
    else:
        page = _item('reporting.page', 'blocked' if problems else 'ok', exp_id=exp_id,
                     exp_link=link, source='reporting', evidence=evidence, required=True,
                     problems=problems, notes=warnings)
    if not exp_id:
        round_item = _item('reporting.round', 'ok', exp_id=None, exp_link='unlinked',
                           source='reporting', evidence={'topic': topic}, required=False,
                           applicable=False,
                           notes=['没有实验号 ⇒ 不判整轮交付（⛔ 不是「整轮已交付」）'])
        return [page, round_item]
    try:
        result = reporting.check_round(Path(root), topic, exp_id)
    except (reporting_module.ReportingError, OSError, ValueError) as exc:
        return [page, _item('reporting.round', 'unknown', exp_id=exp_id, exp_link=link,
                            source='reporting', evidence={'topic': topic}, required=True,
                            problems=[f'整轮交付检查读不到'
                                      f'（{type(exc).__name__}: {exc}）⇒ 不可判'])]
    round_problems = list(result.get('problems') or [])
    round_notes = list(result.get('notes') or [])
    if result.get('round_terminal') is None and any('没有运行事件' in problem
                                                    for problem in round_problems):
        return [page, _item('reporting.round', 'ok', exp_id=exp_id, exp_link=link,
                            source='reporting',
                            evidence={'topic': topic, 'exp': exp_id,
                                      'round_terminal': None},
                            required=False, applicable=False,
                            notes=['这个实验还没有运行事件 ⇒ 没有整轮交付可判'
                                   '（⛔ 不是「整轮已交付」）'] + round_notes)]
    return [page, _item('reporting.round', 'blocked' if round_problems else 'ok', exp_id=exp_id,
                        exp_link=link, source='reporting',
                        evidence={'topic': topic, 'exp': exp_id,
                                  'round_terminal': result.get('round_terminal')},
                        required=True, problems=round_problems, notes=round_notes)]


def _latest_review(db, *, topic, exp_id):
    """最近一条复盘记录（只读）。⛔ 不改 schema、不补造。"""
    if not db.is_file():
        return None, f'状态库不存在：{db}（只读模式不建库）'
    try:
        conn = ReadonlyStore(db).connect()
    except StoreError as exc:
        return None, f'状态库不可读（{exc}）'
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if 'wf_reviews' not in tables:
            return None, '状态库没有 wf_reviews 表（只读模式不迁移）'
        where, args = [], []
        if exp_id:
            where.append('plan_id=?')
            args.append(exp_id)
        if topic:
            where.append('topic=?')
            args.append(topic)
        clause = (' WHERE ' + ' AND '.join(where)) if where else ''
        row = conn.execute('SELECT * FROM wf_reviews' + clause
                           + ' ORDER BY created_at DESC, run_id DESC LIMIT 1',
                           tuple(args)).fetchone()
    except sqlite3.Error as exc:
        return None, f'复盘记录读不到（{type(exc).__name__}: {exc}）'
    finally:
        conn.close()
    if row is None:
        return None, '还没有复盘记录'
    record = {'run_id': row['run_id'], 'plan_id': row['plan_id'], 'topic': row['topic'],
              'verdict': row['verdict'], 'branch': row['branch'],
              'next_action': row['next_action'], 'created_at': row['created_at']}
    try:
        record['record'] = json.loads(row['record_json'])
    except (TypeError, ValueError):
        record['record'] = None
    return record, None


def _continuation_item(db, *, topic, exp_id):
    """`review.continuation`：E5 的下一步交接投影。⛔ 声明需要审批 ≠ 已批准。"""
    link = _link_of(exp_id)
    record, why = _latest_review(db, topic=topic, exp_id=exp_id)
    if record is None:
        return _item('review.continuation', 'unknown', exp_id=exp_id, exp_link=link,
                     source='workflow', evidence={'reviews': 0, 'reason': why},
                     required=False, applicable=False,
                     notes=['没有可读的复盘记录 ⇒ 没有下一步交接信息（⛔ 不是「无需下一步」）',
                            '缺复盘本身由 workflow.review 判'])
    if record.get('record') is None:
        return _item('review.continuation', 'unknown', exp_id=exp_id, exp_link=link,
                     source='workflow',
                     evidence={'run_id': record['run_id'], 'reason': 'record_json 不是合法 JSON'},
                     required=False, problems=['复盘记录损坏 ⇒ 下一步交接不可判'])
    try:
        value = review_continuation.project(record['record'])
    except (ValueError, TypeError) as exc:
        return _item('review.continuation', 'unknown', exp_id=exp_id, exp_link=link,
                     source='workflow', evidence={'run_id': record['run_id']},
                     required=False,
                     problems=[f'下一步交接投影失败（{type(exc).__name__}: {exc}）'])
    evidence = {'run_id': record['run_id'], 'verdict': record.get('verdict'),
                'branch': record.get('branch'), 'next_action': record.get('next_action'),
                'schema_version': value.get('schema_version'), 'status': value.get('status'),
                'legacy': value.get('legacy'), 'approval_status': value.get('approval_status'),
                'criterion': value.get('criterion'), 'next_owner': value.get('next_owner'),
                'cost_scope': value.get('cost_scope'), 'needs_approval':
                    value.get('needs_approval'), 'approval_reason': value.get('approval_reason'),
                'decision_id': value.get('decision_id'),
                'criterion_evidence': value.get('criterion_evidence')}
    notes = ['`needs_approval`/`approval_status` 只是复盘里的**声明**（declared_only）⇒ '
             '⛔ 不能当审批证据；真实批准只看 decisions.approval']
    if value.get('status') != 'recorded':
        return _item('review.continuation', 'unknown', exp_id=exp_id, exp_link=link,
                     source='workflow', evidence=evidence, required=False,
                     problems=list(value.get('problems') or
                                   ['复盘没有记录下一步交接信息（旧记录只读兼容）']),
                     notes=notes)
    problems = []
    if value.get('needs_approval') and value.get('cost_scope', {}).get('scope') in APPROVAL_SCOPES:
        notes.append('下一步声明需要批准，且范围在 '
                     + '/'.join(APPROVAL_SCOPES) + '：等待批准本身是合法下一步，'
                     '但起跑/提交前必须另行拿到真实批准')
    return _item('review.continuation', 'blocked' if problems else 'ok', exp_id=exp_id,
                 exp_link=link, source='workflow', evidence=evidence, required=False,
                 problems=problems, notes=notes)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def checklist(workspace, *, topic=None, exp_id=None, owner_session=None, db=None,
              services=None, host=None, now=None, required=True):
    """一次只读的统一检查清单。返回见模块文档的固定形状。

    * `services`：`{'workflow','monitoring','decisions','issues','questions','reporting'}`；
      给了就用注入对象，没给的按**只读生产构造**去读真实台账；
    * `host`：意图监控的宿主适配器。⛔ 不给 ⇒ `monitoring.*` 一律 unknown（不造宿主证据）；
    * `now`：判超期用的时间戳（默认当前时间），便于复现；
    * `required=True`：本实例用于判绿；`False` 只列 blocked 判词（unknown 不判绿）。
    """
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise AssuranceError(f'工作区不存在：{root}')
    topic_ref = None if topic is None else _token(topic, '专题号', limit=200)
    exp_ref = None if exp_id is None else _token(exp_id, '实验号')
    owner_ref = None if owner_session is None else _token(owner_session, '负责人会话')
    database = Path(db).resolve() if db is not None else (root / '.autoresearch' / 'runtime.sqlite3')
    moment = float(now) if now is not None else datetime.now(timezone.utc).timestamp()
    if not math.isfinite(moment) or moment <= 0:
        raise AssuranceError('检查时点 now 必须是有限正时间')
    injected = dict(services or {})
    unknown_keys = sorted(set(injected) - {'workflow', 'monitoring', 'decisions', 'issues',
                                           'questions', 'reporting', 'budget_gate'})
    if unknown_keys:
        raise AssuranceError('services 里有无法识别的键：' + '、'.join(unknown_keys))
    sources = {}

    def note_source(name, value):
        sources[name] = value

    def build(name, factory):
        """构造一个来源对象；构造失败只让该项 unknown，⛔ 不让整张清单崩掉。"""
        if name in injected:
            note_source(name, 'injected')
            return injected[name]
        try:
            value = factory()
        except Exception as exc:  # noqa: BLE001 - 构造失败要变成 unknown，不是异常
            note_source(name, f'construct_failed: {type(exc).__name__}: {exc}')
            return None
        note_source(name, 'readonly')
        return value

    items = []
    clock = (lambda: moment)

    # 0) 判绿范围：没有范围就没有「这一轮」可判 —— 明写 unknown，⛔ 不拿空清单冒充绿
    if topic_ref is None and exp_ref is None:
        items.append(_item('checklist.scope', 'unknown', exp_id=None, exp_link='not_applicable',
                           source='assurance',
                           evidence={'topic': None, 'exp_id': None},
                           required=bool(required),
                           problems=['没有给 topic 也没有给 exp_id：本清单没有可判绿的轮次范围'
                                     '（⛔ 空清单一律不算绿）'],
                           notes=['要给 --topic 或 --exp 才有「这一轮」的判词；'
                                  '只在排查时用 --informational']))
    else:
        items.append(_item('checklist.scope', 'ok', exp_id=exp_ref,
                           exp_link=_link_of(exp_ref), source='assurance',
                           evidence={'topic': topic_ref, 'exp_id': exp_ref,
                                     'db': str(database), 'db_exists': database.is_file()},
                           required=bool(required)))

    # 1) 执行主链（审计分项紧跟着它）
    flow_status = None
    workflow = build('workflow', lambda: Workflow(root, db=database, readonly=True))
    if workflow is None:
        problem = f'执行主链只读构造失败：{sources["workflow"]}'
        items.extend([
            _item('workflow.preparation', 'unknown', exp_id=exp_ref, exp_link=_link_of(exp_ref),
                  source='workflow', required=bool(topic_ref or exp_ref), problems=[problem]),
            _item('workflow.review', 'unknown', exp_id=exp_ref, exp_link=_link_of(exp_ref),
                  source='workflow', required=bool(topic_ref or exp_ref), problems=[problem])])
    else:
        built_items, flow_status = _workflow_items(workflow, topic=topic_ref, exp_id=exp_ref)
        items.extend(built_items)
    frozen_plan, frozen_why = (None, '没有实验号')
    if exp_ref is not None:
        frozen_plan, frozen_why = _frozen_plan(database, exp_ref)
    items.append(_audit_item(database, exp_id=exp_ref, status=flow_status))

    # 2) 意图与过程监控
    monitoring = build('monitoring', lambda: Monitoring(root, host=host))
    items.extend(_monitoring_items(monitoring, topic=topic_ref, exp_id=exp_ref,
                                   owner_session=owner_ref) if monitoring is not None else [
        _item('monitoring.intent', 'unknown', exp_id=exp_ref, exp_link=_link_of(exp_ref),
              source='monitoring', required=bool(exp_ref),
              problems=[f'意图监控只读构造失败：{sources["monitoring"]}']),
        _item('monitoring.launch', 'unknown', exp_id=exp_ref, exp_link=_link_of(exp_ref),
              source='monitoring', required=bool(exp_ref),
              problems=[f'意图监控只读构造失败：{sources["monitoring"]}'])])

    # 3) 审批与入向
    decisions = build('decisions',
                      lambda: DecisionService.readonly(database, clock=clock))
    if decisions is None:
        problem = f'决策只读服务构造失败：{sources["decisions"]}'
        items.extend([
            _item('decisions.inbound', 'unknown', exp_id=exp_ref, exp_link=_link_of(exp_ref),
                  source='decisions', required=bool(exp_ref), problems=[problem]),
            _item('decisions.approval', 'unknown', exp_id=exp_ref, exp_link=_link_of(exp_ref),
                  source='decisions', required=bool(exp_ref), problems=[problem])])
    else:
        items.extend(_decisions_items(decisions, db=database, exp_id=exp_ref, now=moment,
                                     frozen_plan=frozen_plan))

    # 3b) 预算预留（未启动调真实 `Admission.check_budget`；已启动只作历史诊断）
    started = bool(flow_status and (flow_status.get('runs') or []))
    items.append(_budget_item(database, root=root, exp_id=exp_ref, started=started,
                              frozen=frozen_plan, owner_session=owner_ref, now=moment,
                              gate=injected.get('budget_gate')))

    # 3c) 远端资源租约（`local` 范围明确不适用，⛔ 不造假的远端资源）
    items.append(_lease_item(root, database, exp_id=exp_ref, status=flow_status,
                            frozen=frozen_plan))

    # 4) 问题台账
    issues = build('issues', lambda: Issues(root, readonly=True))
    if issues is None:
        problem = f'问题台账只读构造失败：{sources["issues"]}'
        items.extend([
            _item('issues.registry', 'unknown', exp_id=exp_ref, exp_link=_link_of(exp_ref),
                  source='issues', required=bool(exp_ref), problems=[problem]),
            _item('issues.exp_link', 'unknown', exp_id=exp_ref, exp_link=_link_of(exp_ref),
                  source='issues', required=False, problems=[problem])])
    else:
        items.extend(_issues_items(issues, exp_id=exp_ref))

    # 5) 待答问题
    questions = build('questions', lambda: Questions(root, db=database, readonly=True, clock=clock))
    if questions is None:
        items.append(_item('questions.open', 'unknown', exp_id=exp_ref,
                           exp_link=_link_of(exp_ref), source='questions',
                           required=bool(exp_ref),
                           problems=[f'提问台账只读构造失败：{sources["questions"]}']))
    else:
        items.append(_questions_items(questions, exp_id=exp_ref))

    # 6) 人读页与整轮交付
    reporting = build('reporting', lambda: reporting_module)
    items.extend(_reporting_items(reporting, root, topic=topic_ref, exp_id=exp_ref))

    # 7) 下一步交接（E5）
    items.append(_continuation_item(database, topic=topic_ref, exp_id=exp_ref))

    # 读不到的检查不许写成「已关联本实验」：unknown 的实验关联一律投影成 unknown（⛔ 不猜）。
    for item in items:
        if item['status'] == 'unknown' and item['exp_id'] is not None:
            item['exp_link'] = 'unknown'
            if '关联未经证实' not in item['notes']:
                item['notes'].append('关联未经证实：这一项读不到真实台账，⛔ 不能算作已关联本实验')

    blocked = [item['name'] for item in items if item['status'] == 'blocked']
    unknown = [item['name'] for item in items if item['status'] == 'unknown']
    blocking = [item for item in items if item['status'] == 'blocked'
                and (item['required'] or not required)]
    undecided = [item for item in items if item['status'] == 'unknown' and item['required']]
    counts = {status: sum(1 for item in items if item['status'] == status) for status in STATUSES}
    if blocking:
        exit_code, ok = 3, False
    elif required and undecided:
        exit_code, ok = 2, False
    else:
        exit_code, ok = 0, True
    unlinked_items = [item['name'] for item in items if item['exp_link'] != 'linked']
    return {
        'ok': ok,
        'workspace': str(root),
        'topic': topic_ref,
        'exp_id': exp_ref,
        'owner_session': owner_ref,
        'db': str(database),
        'db_exists': database.is_file(),
        'checked_at': _iso(moment),
        'required_mode': bool(required),
        'sources': sources,
        'items': items,
        'counts': counts,
        'blocked': blocked,
        'unknown': unknown,
        'required_missing': [item['name'] for item in undecided],
        'unlinked_items': unlinked_items,
        'exit_code': exit_code,
        'note': ('unknown 永不计绿：读不到 / 缺 schema / 缺宿主证据都写 unknown；'
                 '不适用（applicable=false）与「已通过」是两件事。'
                 '本清单只读，不写库、不建目录、不签发任何授权。'),
    }


def render_human(result):
    """给人和聊天看的一行一项摘要（⛔ 不是给机器解析的接口）。"""
    lines = [f"检查范围：workspace={result['workspace']} topic={result.get('topic')} "
             f"exp={result.get('exp_id')} ok={result['ok']} exit={result['exit_code']}"]
    for item in result['items']:
        flag = {'ok': '[ok]', 'blocked': '[BLOCKED]', 'unknown': '[unknown]'}[item['status']]
        extra = ''
        if not item['applicable']:
            extra = '（不适用）'
        if item['problems']:
            extra += '：' + '；'.join(item['problems'])
        lines.append(f"{flag} {item['name']}{extra}")
    return '\n'.join(lines)


__all__ = ['AssuranceError', 'checklist', 'render_human']
