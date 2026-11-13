# GENERATED-BY: 完整功能迁移（持续推进 A1–A7 / 远端进度 H1–H9 / 入向超期 D2 / 迭代节奏 §0.6）
# LAST-EDIT: 2026-11-13（goal 与补足器并用：真实事件通知不被goal覆盖状态抑制）
# PURPOSE: 通用进度探针与受管推进器 —— 把「外部有没有新东西」变成可声明、可定位、fail-closed 的观测，
#          并在该叫醒负责人时叫一次、退出、留 needs_rearm 与结构化判词；不内置任何比赛、机器或结论。
# ORIGIN: 上游单文件推进器（963 行）与通用进度探针（478 行）的机制；契约见 kernel/contracts/context_budget.md §4；
#         行为预期 A1–A7 / D2 / H1–H9 见 kernel/contracts/framework_expectations.md。
# CUMULATIVE-DELTA: 2026-11-13 外部变化先通知负责人，goal继续主导执行，不因coverage未知漏事件。 2026-11-09 默认关闭监控时接受无意图声明，不伪造none状态； 首版。与上游的刻意差异：① 命令探针不用管道采集，stdout/stderr 直接落到私有工作区的
#          文件句柄（不截断），回传路径 + sha256，原文同时进事件账；② 采集失败 fail-closed，连续三次形成
#          progress_unavailable 事件，单次失败也不许当成「没有变化」；③ goal 为主来源，外部推进器只在
#          goal 覆盖状态**确切**为空位时补足，未知/部分覆盖一律不可判、不许假绿；④ 入向超期由一个可注入
#          的 overdue 回调给出，优先级高于静默窗口与 goal 抑制；⑤ 一次唤醒即退出并写 needs_rearm，
#          重挂必须由 owner 显式执行，任何退出都写结构化判词与预期退出码（退出码本身不可信）；
#          ⑥ sqlite 只追加事件账 + sha 链 + `event:<seq>` 定位；⑦ 单值租约 + fence 防止同 owner 双作业。
#  2026-11-09 追加（本文件）：⑧ `outcome('advance')` 在落账前核当前真实 overdue（D2）：超期先回复用户、
#          来源不可判一律拒，安全动作（collect/stop/package/rollback/halt/checkpoint）仍不阻；
#          ⑨ 新增只读 `preflight` / `next_round`：复用 `Workflow.check` 同判据 + 只读 overdue + assurance
#          解释清单，全过才给可执行 `next_command`，否则逐项 missing/blocked/unknown，⛔ 不生成新计划；
#          ⑩ 新增只读 `schedule`：按显式研究阶段（mechanism → composition → large → paired）做跨专题排序，
#          用 Runtime 现有资源租约判同机器/GPU 冲突，只给命令、不实发任务；阶段未知诚实保留 unknown。

"""通用进度探针与受管推进器（不含任何比赛、机器或研究结论）。

这个模块补的是「等待」那一格
=============================

宿主 goal 负责持续推进（H1）。外部推进器同时监听「外部到底有没有新东西」，
并在该叫醒负责人时叫一次（A1–A4）。它**不认识**题目、分数、通过数、机器名或 SSH 主机 ——
这些全部来自调用方写的 spec；内核只认识两件通用的事：**采集**与**相邻两次观测的差**
（H5）。

边界（先写清楚，免得被读成授权）
================================

1. **不调用模型、不联网**，只跑 spec 里声明的命令（可能含 ssh），只写自己的状态根。
2. **不派工、不写研究页、不发消息**；唤醒只是「把话说了」，剩下的由负责人做。
3. **不替宿主叫醒任何人。** 本模块的 `watch` 是一个阻塞进程；**它自己不能唤醒宿主会话**。
   只有宿主把 `watch` 当成受管后台作业、并在该作业结束时投递通知，负责人才会醒。
   `watch` 的输出与落盘记录里一律写 `host_woken: false` / `requires_host_notification: true`，
   ⛔ 不许把它读成「已经唤醒宿主」。由此推论：**一次唤醒 = 一次作业退出**（A6），
   下一次必须由 owner 显式 `rearm` 再挂（A3：不设固定寿命，不静默自退）。
4. **CLI 不自动起替代服务。** `watch` 只在被人（或人的受管作业）显式启动时运行；
   它不会 fork 守护进程、不会自续、不会重启自己。用户授权的是「跑这个循环」这一次。

spec（声明式操作；内核不写死任何地址与路径）
==========================================

```json
{
  "version": 1,
  "name": "demo",
  "owner": "main",
  "state": {"root": ".autoresearch/progress", "db": ".autoresearch/progress.sqlite3"},
  "poll_sec": 20,
  "quiet": {"window_sec": 600, "max_window_sec": 7200},
  "round": {"kind": "direct_instruction", "basis": "用户在本次会话里直接授权执行"},
  "overdue": {"max_age_sec": 304},
  "goal": {"state": "unknown", "coverage": "unknown"},
  "collectors": [
    {"key": "rows", "kind": "cmd", "argv": ["python", "-c", "print('{\\"n\\": 7}')"], "pick": "n",
     "timeout_sec": 120},
    {"key": "remote_rows", "kind": "cmd", "pick": "n",
     "transport": {"type": "ssh", "host": "<主机>", "user": "<用户>", "port": 22,
                   "ssh_argv": ["ssh", "-p", "{port}", "-o", "BatchMode=yes", "{user}@{host}"],
                   "fetch_argv": ["scp", "-P", "{port}", "{user}@{host}:{source}", "{destination}"]},
     "argv": ["python3", "-c", "print('{\\"n\\": 7}')"]},
    {"key": "out_dir", "kind": "dir", "path": "/data/out", "aspect": "file_count",
     "transport": {"type": "ssh", "host": "<主机>", "user": "<用户>", "port": 22,
                   "ssh_argv": ["ssh", "-p", "{port}", "{user}@{host}"],
                   "fetch_argv": ["scp", "-r", "-P", "{port}", "{user}@{host}:{source}", "{destination}"]}},
    {"key": "flag", "kind": "file", "path": "state/flag.txt", "aspect": "line_count"}
  ],
  "events": [
    {"key": "rows", "op": "increased_by", "value": 1},
    {"key": "out_dir", "op": "changed"},
    {"key": "flag", "op": "appeared"}
  ]
}
```

键是闭集：不认识的键直接拒（⛔ 避免写成「看起来生效」）。`round.kind` 必填：
`direct_instruction`（用户在会话里直接授权执行的轮次）或 `planned`（要派工/规划的轮次）。
**直令轮的识别只来自这份配置**，⛔ 不去读旧的阶段产物、也不要求旧 stage 跑过（H9）。

采集协议（H5 / H6 / A7）
=======================

* 命**只**用 `argv` 列表 + `shell=False` 起进程。**采集不用管道**：stdout / stderr 直接写进
  状态根下的真实文件句柄（`captures/<run>/<key>/stdout.bin` 等），**不截断**；
  回传里给完整相对路径、字节数与 sha256。原文同时进 `observation` / `collect_failure`
  事件的 `captures` 字段，作为原始证据存档（大输出请读文件，事件里也有全文）。
* **失败 fail-closed**：超时 · 文件/目录不存在（`exists` 之外）· 非零退出 · 输出不可解析 ·
  传输失败，全部算采集失败。失败轮**不做**「相邻两次比较」，⛔ 不许退化成「没有变化」。
* 连续三次采集失败形成一条 `progress_unavailable` 事件并唤醒；单次失败也立刻唤醒
  （带原始失败原因与捕获路径），只是不升级成 unavailable。成功一轮清零。

事件账（sqlite，只追加，可定位）
=============================

`<state.db>` 里 `progress_events` 每行一条：`seq`（从 1 连续）、`ts` / `at`、`kind`、
`payload_json`、`prev_sha`、`sha256`（本行内容指纹）。表级触发器拒 UPDATE / DELETE；
读取时逐行复核序号、前缀链与指纹，不符即拒（可判的损坏，不是「通过」）。
每个事件对外给 `locator = "event:<seq>"`，载荷里带捕获路径、前后观测指纹与 spec 指纹，
所以「这次唤醒凭什么发生」能一路查到原始 stdout/stderr 文件。

判定顺序（优先级从高到低；D2 / A5 / H1 / H2 / H6 / H8）
=====================================================

1. **用户超期未回复**（可注入 `overdue` 回调）⇒ 唤醒，且**不看**静默窗口、**不被** goal 抑制。
   回调抛错 ⇒ 不可判，⛔ 不许假装「没有超期」。
2. **采集失败** ⇒ 唤醒（带原始失败原因；第三次起带 `progress_unavailable`）。
3. **有变化事件** ⇒ 唤醒并交付原始变化，不重复起跑。通知不依赖 goal 覆盖判定。
4. **goal 状态未知 / 覆盖状态不是「确切的满 / 确切的空」** ⇒ 不可判（`undecidable`），
   ⛔ 不返回假绿；status 的 `ok=false` 并列出阻塞项。
5. **goal 是 active 且确切全覆盖** ⇒ 静默（外部推进器**不重复推进**，H2）。
6. **无可走变化且静默窗口到点** ⇒ 唤醒（A4：把原始状态与「为什么现在叫」一起给出来）。
7. 其余 ⇒ 继续等待。

静默窗口会退避：连续两次「确实无事可做」（`outcome nothing`）后窗口翻倍，按 `max_window_sec`
封顶；一次 `advance` 清零（H8）。

一次唤醒的三个出口（H7）
=======================

`outcome` 只接受三种，别的一律拒：

* `advance`：做了什么（`action` 必填）；
* `nothing`：确实无事可做 + **下次判定条件**（`next_condition` 必填）；
* `problem`：登记一个新问题（`problem` 必填，返回 `issue_id`）。

记录完 `outcome` 之后仍要 `rearm`：唤醒过的作业已经结束，重挂是负责人的显式动作（A6）。
反过来也卡住：**一次唤醒没有出口就不许重挂**，**一次唤醒只能有一个出口**（H7 变成机械检查，
不再只靠纪律）。

租约与围栏（单一有效推进来源；H2）
================================

`progress_lease` 是按 `name` 的单值租约：`owner` 是人类可读归属，`holder` 是本次进程实例
（`pid` + 随机串）。**同一时刻只有一个 holder**；第二个进程——**哪怕 owner 相同**——
拿不到租约（`LeaseBusy`），只会追加一条 `lease_busy` 事件，⛔ 不改租约、不改别人的记录。
接管只在租约过期或显式释放后发生，且 `fence` 单调 +1；旧 fence 的写入被拒（`FenceLost`），
所以「同 owner 双作业」不会同时推进。

对外接口
========

    load_spec(path) -> dict
    Progress.load(root, spec_path, **kw) -> Progress
    Progress(root, spec, *, spec_path=None, db_path=None, capture_root=None, owner=None,
             goal=None, overdue=None, clock=time.time, holder=None, ttl_sec=90.0)
    p.acquire(ttl_sec=None) / p.renew() / p.release() / p.lease()
    p.probe()                      # 一轮采集 + 比较 + 落账
    p.decide(probe, goal=..., overdue=...)   # 纯判定（不写账）
    p.tick(goal=..., overdue=...)  # 采集 + 判定 + 落账（单轮）
    p.watch(poll=None, max_rounds=0, goal=..., overdue=...)  # 阻塞；一次唤醒即退出
    p.rearm(note=None, job_id=None)          # owner 显式重挂
    p.outcome(kind, action=..., next_condition=..., problem=..., sources=..., note=...)
    p.status() / p.events(limit=None) / p.last_decision()
    p.record_watch_exit(result, actual_exit) # CLI 退出前补 actual_exit（A7）
    WATCH_CONTRACT / EXIT_CODES / SPEC_VERSION

退出码（⛔ 后台作业可能把它们压成 1，真值一律看落盘记录，A7）：

    0  成功：probe 无事件 / tick 无需唤醒 / watch 已唤醒 / status 健康
    3  probe 有事件 / tick 已唤醒 / status 需要动作（重挂·采集失败·超期）
    4  采集失败（fail-closed；tick/watch 也用它表示「因采集失败而唤醒」）
    5  watch 到达调用方给的轮数上限（**没有唤醒**；已写 needs_rearm，必须重挂）
    6  拿不到租约（未启动）或运行中丢失租约
    7  还有未重挂的唤醒（拒绝再挂第二个作业）
    9  不可判（goal 状态未知 / 覆盖状态不确切 / status 不绿）
    2  spec 或参数被拒
    1  运行异常

对应行为预期：A1（有效授权与预算内继续）· A2（没有有效推进来源时显示故障与恢复动作）·
A3（不因固定寿命静默退出；等待中持续监视）· A4（静默窗口到点以原始状态与原因唤醒）·
A5 / D2（用户待回复优先，不被静默窗口抑制，不许假绿）· A6（一次唤醒即结束 + 显式重挂）·
A7（退出码不足以判原因，结构化落盘保留判词与预期退出码）· H1（goal 主导）·
H2（外部推进器只补 goal 不覆盖的时段，避免重复推进）· H3（新进展覆盖远端产物，不看本地 mtime）·
H4（载荷说明变了什么并附原始事件）· H5（命令/文件/目录采集器 + 声明式操作，语义在配置）·
H6（采集失败 fail-closed，连续三次成事件并唤醒）· H7（三出口）· H8（两次无事翻倍且有上限）·
H9（直令轮由配置识别）。

出口前的 D2 复核（2026-11-09 追加）
==================================

`outcome('advance', ...)` 是「持续推进下一轮」的出口。落账前它必须核**当前真实**入向超期：

* 超期来源不可判（没注入、回调抛错、返回空/畸形）⇒ **拒**（`ProgressError`），
  ⛔ 不把「不知道」记成「没有超期」；
* 有超期未回复入向 ⇒ **拒**，判词写明「先回复用户」、条数、最久 age 与上限；
* 确切没有超期 ⇒ 放行，并把这份 overdue 证据写进 outcome 载荷（`overdue_gate`）。

「保安全 stop/collect 不阻」用**显式** `safe_action=` 表达，取值与 `Monitoring.SAFE_ACTIONS` 同义
（collect / stop / package / rollback / halt / checkpoint）。声明后跳过超期闸、在载荷里记
`safe_action`，并且**不能**同时声明下一轮（`next_plan`/`topic`/`owner`/`intents`/`argv`）；
`next_round` 遇到这种出口只返回 `safety_only`，⛔ 不给新实验命令。它不是任意布尔旁路。
`nothing` / `problem` 两条出口本来就不受此闸影响 —— 安全收尾优先走这两条。

下一轮只读预检（§0.6 / K1）
==========================

`Progress.preflight(...)` 回答一个问题：**「现在能不能按这份已冻结的计划开下一轮」**。
它只读，判据与统一入口同一套（`Workflow(readonly=True).check`）：

    plan_frozen       计划已冻结且与当前版本一致
    plan_sources      本轮 prepare 记录在、来源哈希未过期、before 快照在
    argv              实际 argv 与事前声明逐项相等
    predecessor       同专题没有未结束 / 未复盘的轮次（J5、J6）
    execution_target  真实独立代码落点与环境
    audit             当前审计通过（不是 declared）
    monitoring        Monitoring.check_start 放行（唯一意图权威）
    admission         Admission 放行（共享预算预留 / 作用域 / 大规模决策）
    overdue           当前没有超期未回复入向（只读源；不可判 ⇒ 不给命令）
    assurance         只读汇总（可注入或自动发现；required+applicable 的 blocked/unknown 会拦）

这些**全过**才给 `next_command`（绝对路径、可从任意 cwd 执行的 `workflow_cli launch`）。
任一不过 ⇒ 逐项 `missing` / `blocked` / `unknown`，且 `next_command=None`。
⛔ 它从不生成计划、从不写决策、从不伪造授权：`next_plan`、`topic`、`owner`、`intents`、`argv`
都必须显式给。历史 outcome 没有显式下一动作时只是 `unknown`，不会被读成「advance」。

`Progress.next_round(...)` 在 preflight 之外再加**跨轮**条件（A6）：一次唤醒没有出口 ⇒ 先记出口；
最近出口是 `nothing` ⇒ `waiting`（带下次条件，不给命令）；`problem` ⇒ `problem_open`；
`advance` 但没写显式下一动作 ⇒ `legacy_next_unknown`（诚实 unknown）；显式下一动作与本轮调用
不一致 ⇒ `next_mismatch`；`user_stop=True` ⇒ `stopped`。一次调用只推进一轮，⛔ 不自动续循环。

跨专题阶段排序（§0.6 四阶段）
============================

`Progress.schedule(entries=[...])` 只做**排序与命令生成**，不实发任务、不开机器、不写盘。
阶段只来自显式 `stage` 或计划研究元数据（`research.stage` / `stage` / `execution.stage`），
⛔ 不按 exp 编号猜；顺序固定 mechanism → composition → large → paired，同阶段再按专题
`priority` 与 topic 名稳定排序，并逐项写出次序理由。每个候选用**同一套** preflight 判据，
再用 Runtime 现有资源租约（只读 `resources` / `scheduler_resource_quarantine`）判冲突：

* 正在跑的大题单占用同机器 / 同 GPU 键 ⇒ 该小实验**不推荐**（blocked）；
* 零 GPU（显式 `gpu_units == 0`）或资源键不相交、且预算/审批已过 ⇒ `parallel_safe` 可并行；
* 阶段、资源、预算、审批任一未知 ⇒ `unknown`，⛔ 不推荐；
* 同专题还有未结束 / 未复盘的轮次 ⇒ blocked（不改正在跑的冻结包、题单与参数）；
* 并行实验各自属于自己那一轮：调度结果只给命令，⛔ 不把并行读数混进本轮。

`schedule` 的跨轮条件比 `next_round` 宽：候选本身是调用方显式给出的下一轮，所以只要求
**没有未出口的唤醒**；它不会从上一轮完成状态推断任何计划。
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量（全是通用值；主机、路径、比赛字段一律不进内核）
# ---------------------------------------------------------------------------

SPEC_VERSION = 1
LLM_FREE = True

STATE_ROOT_DEFAULT = ".autoresearch/progress"
DB_DEFAULT = ".autoresearch/progress.sqlite3"
LEASE_NAME = "progress"

GENESIS = "sha256:" + "0" * 64

OPS = frozenset({
    "changed", "increased_by", "decreased_by", "appeared", "disappeared", "crossed", "mismatch",
})
FILE_ASPECTS = frozenset({"exists", "size", "mtime", "line_count", "sha256"})
DIR_ASPECTS = frozenset({"exists", "file_count", "newest_mtime", "total_size"})
FORMATS = frozenset({"json", "regex"})
TRANSPORT_TYPES = frozenset({"local", "ssh"})

GOAL_STATES = ("active", "blocked", "complete", "unknown")
GOAL_COVERAGE = ("full", "partial", "none", "unknown")
ROUND_KINDS = ("direct_instruction", "planned")
OUTCOME_KINDS = ("advance", "nothing", "problem")
FAILURE_KINDS = ("timeout", "missing", "nonzero_exit", "unparsable", "transport_error", "spec_error")

#: 安全动作（L10 / D2）：取包 · 停跑 · 打包 · 回滚 · 停机 · checkpoint。
#: 与 `monitoring.SAFE_ACTIONS` 同一词汇；本模块不 import 监控，避免内核层反向依赖。
SAFE_ACTIONS = ("collect", "stop", "package", "rollback", "halt", "checkpoint")

#: 跨专题四阶段（§0.6）：少量机制验证 → 准备组合 → 审批后大题单 → 冻结组合与对照逐题配对。
#: ⛔ 只来自显式声明或计划研究元数据；不按 exp 编号这一层猜阶段。
STAGES = ("mechanism", "composition", "large", "paired")
STAGE_ORDER = STAGES
STAGE_NOTES = {
    "mechanism": "少量任务验证机制是否实际生效",
    "composition": "准备组合：不改冻结包，只准备更大的题单与对照",
    "large": "审批后的大题单：需要共享预算预留与大规模决策",
    "paired": "冻结组合与对照逐题配对：同装置、同条件逐题对齐",
}

#: 预检每一项的状态。`pass` 之外都要在回传里逐项给出原因。
CHECK_STATES = ("pass", "missing", "blocked", "unknown")
#: 候选实验的结论三态（schedule）。
ENTRY_STATES = ("runnable", "blocked", "unknown")
#: 预检的固定项目（顺序即输出顺序）。
PREFLIGHT_CHECKS = (
    "declarations", "plan_frozen", "topic", "plan_sources", "argv", "predecessor",
    "execution_target", "audit", "monitoring", "admission", "plan_metadata", "overdue",
    "assurance", "remote", "command",
)
#: `schedule` 的候选键（闭集：不认识的键直接拒，避免「看起来生效」）。
SCHEDULE_ENTRY_KEYS = frozenset({
    "topic", "next_plan", "owner", "intents", "argv", "stage", "resources", "note",
})
#: 资源声明里认识的键（list 或 dict 两种形状）。
RESOURCE_KEYS = frozenset({"keys", "resources", "gpu_units", "gpu", "machine", "exclusive", "source"})

#: 事件种类（只追加）。`note` 是调用方登记自定义事实用的通用格。
EVENT_KINDS = (
    "observation", "change", "collect_failure", "progress_unavailable", "decision", "wake",
    "watch_end", "watch_exit", "rearm", "outcome", "note",
    "lease_acquired", "lease_renew", "lease_released", "lease_busy", "lease_stolen", "fence_lost",
)

#: 退出码表：模块与 CLI 共用；⛔ 后台作业可能把非零压成 1，真值看落盘记录（A7）。
EXIT_CODES = {
    "ok": 0,
    "health_action": 3,
    "collect_failed": 4,
    "cap_reached": 5,
    "lease": 6,
    "pending_rearm": 7,
    "undecidable": 9,
    "spec": 2,
    "error": 1,
}

#: 宿主契约：父 agent 照这份把 `watch` 挂成受管后台作业。
WATCH_CONTRACT = {
    "version": 1,
    "command": (
        "python -X utf8 -B kernel/autoresearch/progress_cli.py --root <工作区> --spec <spec.json> "
        "watch --owner <负责人标识>"
    ),
    "managed_by": "host-background-job",
    "blocking": True,
    "goal_state": (
        "必须给 --goal-json <文件> 或 --adapter <文件>（goal_state()/overdue()）；"
        "不给 ⇒ goal 状态未知 ⇒ 退出 9（不可判），⛔ 不返回假绿"),
    "wake_once": "一次唤醒（或不可判）即退出；不设固定寿命，不会到点静默退出",
    "host_woken": False,
    "requires_host_notification": True,
    "self_restart": False,
    "on_exit": [
        "宿主在受管作业结束时投递通知 —— 负责人才会醒；watch 自己叫不醒任何人",
        "读 <state.root>/runs/<job_id>.json 的 verdict / reason / intended_exit / actual_exit",
        "按三出口记录 outcome（advance / nothing + next_condition / problem）",
        "显式执行 rearm，再用同一条 watch 命令挂回（A6）",
    ],
    "rearm_command": (
        "python -X utf8 -B kernel/autoresearch/progress_cli.py --root <工作区> --spec <spec.json> "
        "rearm --owner <负责人标识> --note '<本轮做了什么>'"
    ),
    "next_round": (
        "要开下一轮：先 outcome 里写显式 next（next_plan/topic/owner/intents/argv），"
        "再用 `progress_cli.py preflight`（或 next-round）拿只读预检与可执行 launch 命令。"
        "⛔ 不凭上一轮完成自动生成新计划或伪造授权；预检不过就没有命令。"),
    "note": "CLI 不会自己起替代服务；watch 的启动是用户的显式授权。",
}

_SAFE_NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

SPEC_KEYS = frozenset({
    "version", "name", "owner", "state", "poll_sec", "quiet", "round", "goal", "overdue",
    "collectors", "events", "note",
})
COLLECTOR_KEYS = frozenset({
    "key", "kind", "argv", "path", "aspect", "pick", "format", "pattern", "timeout_sec",
    "transport", "remote_argv", "note",
})
EVENT_KEYS = frozenset({"key", "op", "value", "direction", "expected", "note"})
TRANSPORT_KEYS = frozenset({
    "type", "host", "user", "port", "ssh_argv", "fetch_argv", "connect_timeout_sec",
})
SSH_PLACEHOLDERS = frozenset({"host", "port", "user"})
FETCH_PLACEHOLDERS = frozenset({"host", "port", "user", "source", "destination"})


class ProgressError(ValueError):
    """可判的拒绝：证据读得到，但不满足契约。"""


class SpecError(ProgressError):
    """spec 不合法。"""


class CollectError(ProgressError):
    """采集失败（fail-closed）。`kind` ∈ FAILURE_KINDS，`capture` 是原始捕获（可定位）。"""

    def __init__(self, kind, detail, *, capture=None, key=None):
        if kind not in FAILURE_KINDS:
            kind = "spec_error"
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail
        self.capture = capture
        self.key = key


class LeaseBusy(ProgressError):
    """同一时刻已有在岗推进器（租约未过期且 holder 不同）。"""

    def __init__(self, message, *, holder=None, owner=None, fence=None, expires_in=None):
        super().__init__(message)
        self.holder = holder
        self.owner = owner
        self.fence = fence
        self.expires_in = expires_in


class FenceLost(ProgressError):
    """围栏失效：租约已被别人接管 / 已过期 / 已被释放。"""


class PendingRearm(ProgressError):
    """还有未重挂的唤醒：拒绝在同一 owner 上再挂第二个作业。"""


class OutcomeBlocked(ProgressError):
    """出口被 D2 拒绝：当前有超期入向，或超期来源不可判（⛔ 不许记 advance 假绿）。"""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value) -> str:
    if isinstance(value, bytes):
        data = value
    elif isinstance(value, str):
        data = value.encode("utf-8")
    else:
        data = _canonical(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat(timespec="seconds")


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _need_name(value, label) -> str:
    if not isinstance(value, str) or not _SAFE_NAME_RE.match(value.strip()):
        raise SpecError(f"{label} 不合法（字母或数字开头，1-128 位，只含字母数字与 . _ -）：{value!r}")
    return value.strip()


def _rel_or_abs(root: Path, path: Path) -> str:
    """落盘记录里的路径：在工作区内就给相对路径（可定位、可搬），否则给绝对路径。"""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return str(path)


def _resolve(root: Path, value, default: str) -> Path:
    raw = value if _text(value) else default
    candidate = Path(os.path.expanduser(raw))
    return candidate if candidate.is_absolute() else (root / candidate)


def _diff_fields(before, after, prefix: str = "", depth: int = 0) -> list:
    """前后两次观测的字段级差：给「变了什么」一个可读、可核的形状。"""
    if depth > 6:
        return [{"field": prefix or "<value>", "before": before, "after": after}]
    if isinstance(before, dict) and isinstance(after, dict):
        out = []
        for key in sorted(set(before) | set(after)):
            out.extend(_diff_fields(before.get(key), after.get(key),
                                    f"{prefix}.{key}" if prefix else str(key), depth + 1))
        return out
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        out = []
        for index, (left, right) in enumerate(zip(before, after)):
            out.extend(_diff_fields(left, right, f"{prefix}.{index}" if prefix else str(index),
                                    depth + 1))
        return out
    if before != after or not prefix:
        return [{"field": prefix or "<value>", "before": before, "after": after}]
    return []


# ---------------------------------------------------------------------------
# 只读投影助手：计划元数据 / 研究阶段 / 资源声明 / Runtime 租约 / 起跑闸问题归因
# ---------------------------------------------------------------------------

_UNSET = object()

#: 起跑闸问题文本 → 预检项目。按顺序匹配（先匹配者生效），只用于**归因**；
#: 通过与否由 `Workflow.check` 的结构化 `checks` 决定，⛔ 不靠猜文本判过。
_PROBLEM_RULES = (
    ("plan_frozen", ("计划校验：", "计划缺 id", "计划还没有冻结过", "计划与已冻结版本不一致",
                     "计划文件不存在", "计划不是合法 JSON", "计划必须是 JSON 对象")),
    ("topic", ("缺专题主文档", "专题未登记", "的状态是")),
    ("plan_sources", ("这一轮还没有 prepare 记录", "准备记录", "计划的已冻结版本已不在治理库",
                      "计划在 prepare 之后", "事前材料过期", "来源", "旧复盘来源", "专题主文档",
                      "专题目标在 prepare 之后", "该会话的用户原话在 prepare 之后",
                      "缺 before 主文档快照", "before 快照", "before 收据")),
    ("argv", ("实际 argv", "owner_session 与 prepare")),
    ("predecessor", ("同专题有未结束运行", "已结束但未复盘", "已有实际启动记录", "已被否定",
                     "retry", "不能重跑同一轮")),
    ("execution_target", ("执行目标：",)),
    ("audit", ("审计：",)),
    ("monitoring", ("监控：", "必须显式声明监控入口", "intent 声明冲突")),
    ("admission", ("执行准入：",)),
    ("declarations", ("owner_session 必填", "必须给出实际 argv", "实际 argv 必须",
                      "实际 argv 的每一项")),
)


def _problem_bucket(problem) -> str:
    text = str(problem)
    for name, prefixes in _PROBLEM_RULES:
        if any(text.startswith(prefix) or prefix in text for prefix in prefixes):
            return name
    return "other"


def _gate_problems_by_check(problems) -> dict:
    buckets = {}
    for problem in problems or []:
        buckets.setdefault(_problem_bucket(problem), []).append(str(problem))
    return buckets


def _plan_argument(workspace, next_plan):
    """把 `next_plan` 归成 (给起跑闸的形态, 给 CLI 的工作区内相对路径)。

    * dict ⇒ 直接交给 `Workflow.check`（内存计划），但**没有路径** ⇒ 不给可执行命令；
    * 相对路径 ⇒ 原样；绝对路径 ⇒ 必须在工作区内，转成相对路径（⛔ 不接受越界路径）。
    """
    if isinstance(next_plan, dict):
        return json.loads(json.dumps(next_plan)), None
    if not _text(next_plan):
        raise SpecError("next_plan 必须是工作区内相对路径或计划对象")
    raw = str(next_plan).strip().replace("\\", "/")
    path = Path(raw)
    if path.is_absolute():
        try:
            raw = path.resolve().relative_to(Path(workspace).resolve()).as_posix()
        except (ValueError, OSError) as exc:
            raise SpecError(
                f"next_plan 越出工作区：{next_plan}（CLI 只接受工作区内相对路径）") from exc
    return raw, raw


def _load_plan_metadata(workspace, next_plan):
    """只读读计划内容，给阶段 / 资源元数据用。返回 `(对象|None, 来源, sha256|None)`。

    ⚠️ 它只服务于**排序元数据**；真正的起跑闸会自己重读同一路径，
    ⛔ 不因为这里读到过就认为计划没变。
    """
    if isinstance(next_plan, dict):
        try:
            return json.loads(json.dumps(next_plan)), "memory", None
        except (TypeError, ValueError):
            return None, "memory", None
    if not _text(next_plan):
        return None, "missing", None
    root = Path(workspace).resolve()
    raw = str(next_plan).strip().replace("\\", "/")
    path = Path(raw)
    if path.is_absolute():
        try:
            path = path.resolve()
            raw = path.relative_to(root).as_posix()
        except (ValueError, OSError):
            return None, "outside", None
    else:
        path = (root / raw).resolve()
        if not path.is_relative_to(root):
            return None, "outside", None
    if not path.is_file():
        return None, "missing", None
    data = path.read_bytes()
    digest = _digest(data)
    try:
        payload = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "unreadable", digest
    if not isinstance(payload, dict):
        return None, "unreadable", digest
    return payload, "file", digest


def _intent_declaration(intents, *, root=None) -> dict:
    """归一显式意图声明：`{"intent_id": "..."}` 与 `{"none": True}` 二选一，⛔ 省略不默认放行。"""
    if root is not None and (intents is None or intents == {"intent_id": None, "none": False}):
        from .monitoring import enabled
        if not enabled(root):
            return {"intent_id": None, "none": False}
    if not isinstance(intents, dict):
        raise SpecError("intents 必须是对象：{\"intent_id\": ...} 或 {\"none\": true}")
    extra = set(intents) - {"intent_id", "none"}
    if extra:
        raise SpecError(f"intents 有不认识的键：{sorted(extra)}")
    has_id = _text(intents.get("intent_id"))
    none = intents.get("none")
    if none not in (None, True):
        raise SpecError("intents.none 只能是 true 或缺省（⛔ 不接受 false 这种「看起来声明了」的写法）")
    if has_id and none is True:
        raise SpecError("intents 声明冲突：intent_id 与 none 只能给一个")
    if not has_id and none is not True:
        raise SpecError("必须显式声明意图入口：intent_id 或 none=true（省略不默认放行）")
    return {"intent_id": intents.get("intent_id").strip() if has_id else None,
            "none": none is True}


def _argv_list(argv, label="argv") -> list:
    if isinstance(argv, str) or not isinstance(argv, (list, tuple)) or not argv:
        raise SpecError(f"{label} 必须是显式非空字符串数组（⛔ 不接受字符串命令行）")
    if any(not isinstance(item, str) or not item for item in argv):
        raise SpecError(f"{label} 的每一项都必须是非空字符串")
    return [str(item) for item in argv]


def _plan_stage(plan) -> dict:
    """只读计划里的研究阶段；缺失 / 冲突一律 unknown（⛔ 不按编号猜）。"""
    if not isinstance(plan, dict):
        return {"known": False, "stage": None, "source": "none",
                "why": "读不到计划对象，无法判定研究阶段"}
    candidates = []
    research = plan.get("research")
    if isinstance(research, dict) and research.get("stage") is not None:
        candidates.append(("plan.research.stage", research.get("stage")))
    if plan.get("stage") is not None:
        candidates.append(("plan.stage", plan.get("stage")))
    execution = plan.get("execution")
    if isinstance(execution, dict) and execution.get("stage") is not None:
        candidates.append(("plan.execution.stage", execution.get("stage")))
    if not candidates:
        return {"known": False, "stage": None, "source": "none",
                "why": ("计划没有显式研究阶段（research.stage / stage / execution.stage 都没有）："
                        "⛔ 不按 exp 编号这一层猜阶段")}
    values = {str(value).strip() for _, value in candidates}
    if len(values) > 1:
        return {"known": False, "stage": None, "source": "conflict",
                "why": f"计划里的阶段声明不一致：{[(name, value) for name, value in candidates]}"}
    value = next(iter(values))
    if value not in STAGES:
        return {"known": False, "stage": None, "source": candidates[0][0],
                "why": f"阶段值非法：{value!r}；只能是 {list(STAGES)}"}
    return {"known": True, "stage": value, "source": candidates[0][0], "why": None}


def _stage_of(plan, explicit=None) -> dict:
    """研究阶段只来自显式声明或计划研究元数据；两者矛盾 ⇒ unknown（⛔ 不覆盖计划）。"""
    plan_stage = _plan_stage(plan)
    if explicit is None:
        return plan_stage
    value = str(explicit).strip()
    if value not in STAGES:
        return {"known": False, "stage": None, "source": "explicit",
                "why": f"显式 stage 非法：{explicit!r}；只能是 {list(STAGES)}"}
    if not plan_stage.get("known") and plan_stage.get("source") not in ("none",):
        return {"known": False, "stage": None, "source": "conflict",
                "why": (f"计划的研究阶段声明不可用（{plan_stage.get('why')}）："
                        "⛔ 不用调用方 stage 覆盖")}
    if plan_stage.get("known") and plan_stage.get("stage") != value:
        return {"known": False, "stage": None, "source": "conflict",
                "why": (f"显式 stage={value!r} 与计划研究阶段 {plan_stage.get('stage')!r} 不一致："
                        "⛔ 不用调用方参数覆盖冻结计划")}
    return {"known": True, "stage": value, "source": "explicit", "why": None,
            "declared_source": plan_stage.get("source")}


def _normalize_resources(raw, label="resources") -> dict:
    """归一资源声明：`{known, complete, keys, gpu_units, machine, exclusive, why}`。

    冲突判据只用 **Runtime 的正式 `resource_key` 字符串**（精确相等）：
    ⛔ 不把 `machine` 标签猜成某个键，也不从 `gpu_units` 反推键名 —— 猜出来的「无冲突」就是假绿。
    要判同机器 / 同 GPU，就把那些键显式写进 `keys`。
    """
    out = {"known": False, "complete": False, "keys": [], "gpu_units": None,
           "machine": None, "exclusive": None, "why": None}
    if raw is None:
        out["why"] = f"{label} 未声明"
        return out
    if isinstance(raw, str) or not isinstance(raw, (list, dict)):
        out["why"] = f"{label} 必须是字符串列表或对象"
        return out
    declared_keys = False
    keys = None
    if isinstance(raw, list):
        declared_keys = True
        keys = raw
    else:
        extra = set(raw) - RESOURCE_KEYS
        if extra:
            out["why"] = f"{label} 有不认识的键：{sorted(extra)}"
            return out
        declared_keys = "keys" in raw or "resources" in raw
        keys = raw.get("keys", raw.get("resources"))
        gpu = raw.get("gpu_units", raw.get("gpu"))
        machine = raw.get("machine")
        exclusive = raw.get("exclusive")
        if gpu is not None:
            if isinstance(gpu, bool) or not isinstance(gpu, int) or gpu < 0:
                out["why"] = f"{label}.gpu_units 必须是非负整数"
                return out
            out["gpu_units"] = int(gpu)
        if machine is not None:
            if not _text(machine):
                out["why"] = f"{label}.machine 必须是非空字符串"
                return out
            out["machine"] = machine.strip()
        if exclusive is not None:
            if not isinstance(exclusive, bool):
                out["why"] = f"{label}.exclusive 必须是布尔"
                return out
            out["exclusive"] = exclusive
    if keys is None:
        keys = []
    if isinstance(keys, str) or not isinstance(keys, list) \
            or any(not isinstance(item, str) or not item.strip() for item in keys):
        out["why"] = f"{label} 的资源键必须是字符串列表"
        return out
    out["keys"] = sorted({item.strip() for item in keys})
    if not out["keys"] and not declared_keys and out["gpu_units"] is None:
        out["why"] = (f"{label} 只给了 machine / exclusive 这类标签，没有给出 Runtime resource_key："
                      "⛔ 不把标签猜成「没有冲突」；请把机器 / GPU 的资源键写进 keys")
        return out
    if out["gpu_units"] is not None and out["gpu_units"] > 0 and not any(
            key == "gpu" or key.startswith("gpu:") or key.startswith("gpu/")
            for key in out["keys"]):
        out["why"] = (f"{label}.gpu_units={out['gpu_units']} 但没有对应的 gpu 资源键："
                      "⛔ 不能判 GPU 冲突")
        return out
    if out["machine"] is not None and not out["keys"]:
        out["why"] = (f"{label}.machine={out['machine']!r} 没有对应的 resource_key："
                      "Runtime 按 resource_key 判占用，⛔ 不把 machine 别名猜成无冲突")
        return out
    out["known"] = True
    out["complete"] = bool(out["keys"]) or declared_keys or out["gpu_units"] == 0
    return out


def _plan_resources(plan):
    """只读计划里的资源声明；没有声明返回 None（多处声明不一致 ⇒ unknown）。"""
    if not isinstance(plan, dict):
        return {"known": False, "complete": False, "keys": [], "gpu_units": None,
                "machine": None, "exclusive": None, "source": "none",
                "why": "读不到计划对象，无法判定资源占用"}
    candidates = []
    execution = plan.get("execution")
    if isinstance(execution, dict) and execution.get("resources") is not None:
        candidates.append(("plan.execution.resources", execution.get("resources")))
    if isinstance(execution, dict) and execution.get("resource_keys") is not None:
        candidates.append(("plan.execution.resource_keys", execution.get("resource_keys")))
    remote_block = execution.get("remote") if isinstance(execution, dict) else None
    if isinstance(remote_block, dict) and remote_block.get("resources") is not None:
        candidates.append(("plan.execution.remote.resources",
                           remote_block.get("resources")))
    if plan.get("resources") is not None:
        candidates.append(("plan.resources", plan.get("resources")))
    if not candidates:
        return None
    normalized = [(_normalize_resources(raw, name), name) for name, raw in candidates]
    broken = next((item for item, _ in normalized if item["why"]), None)
    if broken is not None:
        source = next(name for item, name in normalized if item["why"])
        return dict(broken, source=source)
    signatures = {(tuple(item["keys"]), item["gpu_units"], item["machine"], item["exclusive"])
                  for item, _ in normalized}
    if len(signatures) > 1:
        return {"known": False, "complete": False, "keys": [], "gpu_units": None,
                "machine": None, "exclusive": None, "source": "conflict",
                "why": f"计划里多处资源声明不一致：{[name for name, _ in candidates]}"}
    return dict(normalized[0][0], source=normalized[0][1])


def _resource_signature(resource) -> tuple:
    return (tuple(resource.get("keys") or []), resource.get("gpu_units"),
            resource.get("machine"), resource.get("exclusive"))


def _resources_of(plan, explicit=None) -> dict:
    """从显式参数或计划元数据读资源声明。

    ⚠️ 显式 `resources` **不能**覆盖冻结计划里的相反声明：两边都有且不一致 ⇒ unknown（⛔ 不绿）。
    """
    plan_resource = _plan_resources(plan)
    if explicit is not None:
        result = _normalize_resources(explicit, "resources")
        result["source"] = "explicit"
        if plan_resource is not None and not plan_resource.get("known"):
            return {"known": False, "complete": False, "keys": [], "gpu_units": None,
                    "machine": None, "exclusive": None, "source": "conflict",
                    "why": (f"计划里的资源声明不可用（{plan_resource.get('why')}）："
                            "⛔ 不用调用方 resources 覆盖计划")}
        if plan_resource is not None and plan_resource.get("known"):
            if _resource_signature(result) != _resource_signature(plan_resource):
                return {"known": False, "complete": False, "keys": [], "gpu_units": None,
                        "machine": None, "exclusive": None, "source": "conflict",
                        "why": ("显式 resources 与冻结计划里的资源声明不一致："
                                "⛔ 不用调用方参数覆盖计划；要么改计划重新冻结，要么按计划声明传")}
        return result
    if plan_resource is not None:
        return plan_resource
    return {"known": False, "complete": False, "keys": [], "gpu_units": None,
            "machine": None, "exclusive": None, "source": "none",
            "why": ("计划没有显式资源声明（execution.resources / execution.resource_keys / "
                    "resources）：⛔ 不能判同机器/GPU 冲突，也不能推荐可跑")}


def _readonly_store(db_path):
    """只读状态库句柄；⛔ 不建库、不建目录、不迁移。拿不到就返回 `(None, why)`。"""
    from .runtime.store import ReadonlyStore
    try:
        store = ReadonlyStore(db_path)
    except (TypeError, ValueError) as exc:
        return None, f"状态库句柄不可用：{type(exc).__name__}: {exc}"
    if not store.is_file():
        return None, f"状态库不存在：{store.db_path}（只读检查不建库）"
    return store, None


def _readonly_projection(db_path, plan_ids, now) -> dict:
    """只读读 Runtime 资源租约 + 冻结计划内容。

    缺库 / 缺表 / WAL 边界 / 不可读 ⇒ `checked=False`（不可判）；⛔ 不把缺表读成「没有租约」。
    """
    out = {"checked": False, "why": None, "leases": [], "plans": {}, "missing_plans": [],
           "as_of": float(now)}
    store, why = _readonly_store(db_path)
    if store is None:
        out["why"] = why
        return out
    try:
        conn = store.connect()
    except Exception as exc:  # noqa: BLE001 - WAL 边界 / 不是 SQLite 都按不可判处理
        out["why"] = f"只读打开失败（WAL 边界 / 不是 SQLite）：{type(exc).__name__}: {exc}"
        return out
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "resources" not in tables:
            out["why"] = ("缺 resources 表（Runtime 未初始化）：资源占用不可判；"
                          "⛔ 不把缺表读成「没有租约」")
            return out
        rows = [dict(row) for row in conn.execute(
            "SELECT resource_key,exp_id,task_id,generation,token,lease_until FROM resources"
            " ORDER BY resource_key")]
        quarantine = {}
        if "scheduler_resource_quarantine" in tables:
            quarantine = {row["resource_key"]: row["reason"] for row in conn.execute(
                "SELECT resource_key,reason FROM scheduler_resource_quarantine")}
        plans = {}
        wanted = sorted({str(item) for item in (plan_ids or []) if _text(item)})
        if "gov_plans" in tables:
            for plan_id in wanted:
                row = conn.execute(
                    "SELECT plan_json FROM gov_plans WHERE plan_id=?"
                    " ORDER BY version DESC LIMIT 1", (plan_id,)).fetchone()
                if row is None:
                    continue
                try:
                    plans[plan_id] = json.loads(row["plan_json"])
                except (TypeError, ValueError):
                    plans[plan_id] = None
        out["missing_plans"] = [plan_id for plan_id in wanted if plan_id not in plans]
    except Exception as exc:  # noqa: BLE001 - 旧 schema / 坏行都按不可判
        out["why"] = f"只读读取失败（不可判）：{type(exc).__name__}: {exc}"
        return out
    finally:
        conn.close()
    leases = []
    for row in rows:
        key = str(row["resource_key"])
        try:
            expired = float(row["lease_until"]) <= float(now)
        except (TypeError, ValueError):
            expired = True
        reason = quarantine.get(key)
        leases.append({
            "resource_key": key, "exp_id": row["exp_id"], "task_id": row["task_id"],
            "generation": row["generation"], "lease_until": row["lease_until"],
            "expired": expired, "quarantine_reason": reason,
            "state": "unknown" if (expired or reason is not None) else "leased",
            "occupied": True,
        })
    out.update({"checked": True, "leases": leases, "plans": plans})
    return out


def _open_rounds(workspace, db_path, projection) -> dict:
    """只读列出「还没收口」的轮次（未结束 或 结束但未复盘）及其计划元数据。"""
    from .workflow import Workflow
    try:
        status = Workflow(str(workspace), db=db_path, readonly=True).status()
    except Exception as exc:  # noqa: BLE001 - 缺库 / 旧 schema / WAL 都按不可判
        return {"checked": False, "why": f"{type(exc).__name__}: {exc}", "open": [], "closed": []}
    if status.get("problems"):
        return {"checked": False, "why": "；".join(str(item) for item in status["problems"]),
                "open": [], "closed": []}
    plans = (projection or {}).get("plans") or {}
    open_runs, closed = [], []
    for run in status.get("runs") or []:
        item = dict(run)
        item["plan"] = plans.get(run.get("plan_id"))
        if run.get("finished") and run.get("reviewed"):
            closed.append(item)
        else:
            open_runs.append(item)
    return {"checked": True, "why": None, "open": open_runs, "closed": closed}


def _topic_priorities(db_path):
    """只读专题优先级（越小越先）；读不到返回 `(空, why)`，⛔ 不猜默认值当已知。"""
    from .runtime.topics import readonly_board
    try:
        board = readonly_board(db_path)
    except Exception as exc:  # noqa: BLE001
        return {}, f"专题板不可读：{type(exc).__name__}: {exc}"
    out = {}
    for topic in board.get("topics") or []:
        if isinstance(topic, dict) and _text(topic.get("id")):
            out[str(topic["id"])] = topic.get("priority")
    return out, None


def _assurance_summary(result) -> dict:
    """归一 `assurance.checklist` 的只读汇总。

    判据（父会话口径）：`required` 且 `applicable` 的项，**blocked 与 unknown 都拒**；
    非 required / 不适用的 blocked/unknown 只作诊断（非阻断）；`ok=false` 一律按阻断处理。
    """
    if not isinstance(result, dict):
        return {"state": "unknown", "blocking": False, "items": [],
                "why": f"assurance 返回值不是对象：{type(result).__name__}"}
    items = [item for item in (result.get("items") or []) if isinstance(item, dict)]
    required_blocked, required_unknown, diagnostics = [], [], []
    for item in items:
        status = item.get("status")
        required = item.get("required") is True
        applicable = item.get("applicable") is not False
        entry = {"name": item.get("name"), "status": status, "required": required,
                 "applicable": applicable,
                 "problems": [str(problem) for problem in (item.get("problems") or [])]}
        if required and applicable and status == "blocked":
            required_blocked.append(entry)
        elif required and applicable and status == "unknown":
            required_unknown.append(entry)
        elif status in ("blocked", "unknown"):
            diagnostics.append(entry)
    required_missing = [str(item) for item in (result.get("required_missing") or [])]
    blocking = bool(required_blocked or required_unknown or required_missing
                    or result.get("ok") is False)
    why = None
    if required_blocked:
        why = ("assurance 有 required+applicable 的阻断项：" +
               "、".join(str(item["name"]) for item in required_blocked))
    elif required_unknown:
        why = ("assurance 的 required+applicable 项不可判（⛔ 不自动放行）：" +
               "、".join(str(item["name"]) for item in required_unknown))
    elif required_missing:
        why = "assurance 报告必需检查缺失：" + "、".join(required_missing)
    elif result.get("ok") is False:
        why = "assurance 报 ok=false（未给出可归因的 required 项）"
    return {
        "state": "blocked" if blocking else "pass",
        "blocking": blocking,
        "why": why,
        "items": items,
        "counts": result.get("counts"),
        "required_blocked": required_blocked,
        "required_unknown": required_unknown,
        "required_missing": required_missing,
        "diagnostics": diagnostics,
        "source": "assurance.checklist",
    }


def _call_with_supported(function, kwargs):
    """按被调方签名挑它能接的关键字（夹具常只收一部分）；⛔ 不吞真正的执行错误。"""
    try:
        parameters = inspect.signature(function).parameters
    except (TypeError, ValueError):
        return function(**kwargs)
    if any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values()):
        return function(**kwargs)
    accepted = {name for name, item in parameters.items()
                if item.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                 inspect.Parameter.KEYWORD_ONLY)}
    return function(**{key: value for key, value in kwargs.items() if key in accepted})


def _same_plan(left, right) -> bool:
    """两份计划声明是不是同一份：对象按规范 JSON，路径按工作区相对写法。"""
    if isinstance(left, dict) and isinstance(right, dict):
        try:
            return _canonical(left) == _canonical(right)
        except (TypeError, ValueError):
            return False
    if _text(left) and _text(right):
        return str(left).strip().replace("\\", "/") == str(right).strip().replace("\\", "/")
    return False


def _source_trust(source, explicit=None) -> str:
    """D2 源的可信度：`trusted`（可信只读复核）/ `untrusted`（静态快照、任意 adapter）/ `none`。

    ⛔ 静态 JSON 或调用方自报的 `count=0` 不能作为「没有超期」的依据；只有真实只读来源
    （`progress_host.read_overdue` 读 workspace 库的已认证入向 / 回执）才算 trusted。
    """
    if explicit in ("trusted", "untrusted", "none"):
        return explicit
    if source is None:
        return "none"
    if callable(source):
        return "trusted"
    return "untrusted"


# ---------------------------------------------------------------------------
# 远端分支：复用 RemoteWorkflow.check（或 progress_remote 包装），不复制判据
# ---------------------------------------------------------------------------


def _raw_resource_keys(raw):
    """按原顺序取资源键（远端 helper 要求与计划声明逐项相等，⛔ 不排序、不改写）。"""
    if raw is None:
        return None
    if isinstance(raw, list):
        keys = [item.strip() for item in raw if isinstance(item, str) and item.strip()]
        return keys if len(keys) == len(raw) else None
    if isinstance(raw, dict):
        keys = raw.get("keys", raw.get("resources"))
        if keys is None:
            return [] if raw.get("gpu_units", raw.get("gpu")) == 0 else None
        if isinstance(keys, list):
            cleaned = [item.strip() for item in keys if isinstance(item, str) and item.strip()]
            return cleaned if len(cleaned) == len(keys) else None
    return None


def _plan_remote_resources(plan):
    """计划 `execution.remote.resources` 的原顺序资源键；没有声明返回 None。"""
    if not isinstance(plan, dict):
        return None
    execution = plan.get("execution") if isinstance(plan.get("execution"), dict) else None
    remote = execution.get("remote") if isinstance(execution, dict) else None
    if not isinstance(remote, dict):
        return None
    return _raw_resource_keys(remote.get("resources"))


def _remote_problem_summary(problems) -> tuple:
    """远端 check 的问题文本 → (state, reason)。

    * 缺部署源（未配置 / 缺文件）⇒ unknown（部署缺失，不是永久 unsupported）；
    * 部署明确 disabled / 配置非法 / 远端块缺字段 ⇒ blocked；
    * 不可判类文本 ⇒ unknown。
    """
    items = [str(item) for item in (problems or []) if str(item).strip()]
    if not items:
        return "unknown", "远端预检未给出问题说明，但判不放行"
    blocked_markers = ("enabled 不是 true", "disabled", "禁用", "已废弃", "配置有未知键",
                       "必须是", "必填", "不支持")
    if any(any(marker in item for marker in blocked_markers) for item in items):
        return "blocked", "；".join(items)
    unknown_markers = ("未配置受管远端部署", "部署未就绪", "缺少工作区内", "worker",
                       "不可判", "未核", "未验", "不存在")
    if any(any(marker in item for marker in unknown_markers) for item in items):
        return "unknown", "；".join(items)
    return "blocked", "；".join(items)


def _normalize_remote_result(remote) -> dict:
    """把 `progress_remote.remote_preflight` / 兜底 RemoteWorkflow.check 的结果压成统一形状。"""
    if not isinstance(remote, dict):
        return {"state": "unknown", "ok": False, "why": "远端预检返回值不是对象", "items": [],
                "command": None, "blocked": [], "unknown": [], "missing": [],
                "checks": {}, "raw": remote}
    problems = []
    for key in ("missing", "blocked", "unknown"):
        problems.extend(str(item) for item in (remote.get(key) or []))
    problems.extend(str(item) for item in (remote.get("problems") or []))
    state = "unknown"
    if remote.get("ok") is True:
        state = "pass"
    elif remote.get("status") == "blocked":
        state = "blocked"
    elif remote.get("status") == "unknown":
        state = "unknown"
    elif problems:
        state, _ = _remote_problem_summary(problems)
    command = remote.get("launch_command")
    if state == "pass" and (not isinstance(command, list) or not command
                            or any(not isinstance(item, str) or not item for item in command)):
        return {"state": "unknown", "ok": False,
                "why": "远端预检判过但没有给出可执行 launch_command（或形状不合法）",
                "items": [], "command": None,
                "blocked": list(remote.get("blocked") or []),
                "unknown": list(remote.get("unknown") or []),
                "missing": list(remote.get("missing") or []),
                "checks": remote.get("checks") or {}, "raw": remote}
    if state != "pass":
        command = None
    why = None
    if state != "pass":
        _, why = _remote_problem_summary(problems)
        if remote.get("why"):
            why = str(remote.get("why")) + ("；" + why if why else "")
    return {"state": state, "ok": state == "pass", "why": why, "items": problems,
            "command": command,
            "blocked": list(remote.get("blocked") or []),
            "unknown": list(remote.get("unknown") or []),
            "missing": list(remote.get("missing") or []),
            "checks": remote.get("checks") or {}, "raw": remote}


def _remote_check_fallback(*, workspace, db_path, plan, topic, owner, intents, argv,
                           resources, monitor_check, authorization_check, verifier,
                           clock, allow_fixture_gates=False) -> dict:
    """兜底（`progress_remote` 未落地时）：直接复用 `RemoteWorkflow.check` 的只读闸。

    ⛔ 不复制远端启动 / 停止证明：那部分只在 `RemoteWorkflow.submit/query/cancel` 主链里；
    本函数只读、不联网、不写盘，判过才给 `remote_cli.py start` 的显式命令。
    """
    from .remote_workflow import RemoteWorkflow, read_deployment
    deployment, deployment_problems = read_deployment(workspace)
    try:
        flow = RemoteWorkflow(
            str(workspace), db=str(db_path), deployment=deployment, readonly=True,
            verifier=verifier, monitor_check=monitor_check,
            require_monitor_check=True,
            authorization_check=authorization_check,
            require_authorization_check=True, clock=clock,
            allow_fixture_gates=bool(allow_fixture_gates))
        raw = flow.check(
            topic=topic, plan=plan, owner_session=owner, argv=argv,
            resources=tuple(resources or ()), intent_id=intents["intent_id"],
            intent_none=bool(intents["none"]))
    except Exception as exc:  # noqa: BLE001 - 缺库 / 部署读不出 / 旧 schema 都按不可判
        return {"ok": False, "status": "unknown", "launch_command": None,
                "problems": [f"远端预检不可判：{type(exc).__name__}: {exc}"],
                "blocked": [], "unknown": [f"remote:{type(exc).__name__}"],
                "missing": [], "checks": {},
                "deployment_problems": list(deployment_problems or [])}
    result = dict(raw)
    result["launch_command"] = None
    result["status"] = "runnable" if raw.get("ok") else (
        "blocked" if raw.get("gate_ok") else "unknown")
    if raw.get("ok") and _text(raw.get("plan_id")):
        cli = Path(__file__).with_name("remote_cli.py").resolve()
        command = [sys.executable, "-X", "utf8", "-B", str(cli), "start",
                   "--workspace", str(Path(workspace).resolve())]
        if db_path:
            command += ["--db", str(Path(db_path).resolve())]
        command += ["--topic", topic, "--plan", _text(plan) and str(plan) or "",
                    "--owner-session", owner]
        if intents["none"]:
            command.append("--intent-none")
        elif intents["intent_id"]:
            command += ["--intent-id", intents["intent_id"]]
        for key in sorted({str(item) for item in (resources or []) if _text(item)}):
            command += ["--resource", key]
        command += ["--", *argv]
        if not _text(plan):
            result["launch_command"] = None
            result["problems"] = list(result.get("problems") or []) + [
                "next_plan 是内存对象，没有工作区内相对路径：远端 CLI 需要显式路径"]
        else:
            result["launch_command"] = command
    result.setdefault("problems", [])
    result["checks"] = result.get("checks") or {}
    return result


# ---------------------------------------------------------------------------
# spec：闭集校验 + 归一
# ---------------------------------------------------------------------------


def _validate_placeholders(template, allowed, label) -> list:
    if not isinstance(template, list) or not template:
        raise SpecError(f"{label} 必须是非空 argv 列表")
    out = []
    for item in template:
        if not isinstance(item, str) or not item:
            raise SpecError(f"{label} 的每一项必须是非空字符串：{item!r}")
        used = set(_PLACEHOLDER_RE.findall(item))
        extra = used - set(allowed)
        if extra:
            raise SpecError(f"{label} 用了不认识的占位符 {sorted(extra)}；只允许 {sorted(allowed)}")
        out.append(item)
    return out


def _validate_transport(spec, key) -> dict:
    raw = spec.get("transport")
    if raw is None:
        return {"type": "local"}
    if not isinstance(raw, dict):
        raise SpecError(f"collector.transport 必须是对象：{key}")
    extra = set(raw) - TRANSPORT_KEYS
    if extra:
        raise SpecError(f"collector.transport 有不认识的键：{sorted(extra)}（{key}）")
    kind = raw.get("type")
    if kind not in TRANSPORT_TYPES:
        raise SpecError(f"collector.transport.type 只能是 {sorted(TRANSPORT_TYPES)}：{key}={kind}")
    if kind == "local":
        extra = set(raw) - {"type"}
        if extra:
            raise SpecError(f"local transport 不接受这些键：{sorted(extra)}（{key}）")
        return {"type": "local"}
    missing = [name for name in ("host", "user", "port") if not raw.get(name)]
    if missing:
        raise SpecError(f"ssh transport 缺字段 {missing}（{key}）；地址与账号只能来自 spec")
    if not _number(raw.get("port")):
        raise SpecError(f"ssh transport.port 必须是数字：{key}")
    ssh_argv = _validate_placeholders(raw.get("ssh_argv"), SSH_PLACEHOLDERS,
                                      f"transport.ssh_argv（{key}）")
    fetch_argv = None
    if raw.get("fetch_argv") is not None:
        fetch_argv = _validate_placeholders(raw.get("fetch_argv"), FETCH_PLACEHOLDERS,
                                            f"transport.fetch_argv（{key}）")
    timeout = raw.get("connect_timeout_sec", 30)
    if not _number(timeout) or timeout <= 0:
        raise SpecError(f"transport.connect_timeout_sec 必须是正数：{key}")
    return {
        "type": "ssh",
        "host": str(raw["host"]).strip(),
        "user": str(raw["user"]).strip(),
        "port": int(raw["port"]),
        "ssh_argv": ssh_argv,
        "fetch_argv": fetch_argv,
        "connect_timeout_sec": float(timeout),
    }


def _validate_collector(raw, spec) -> dict:
    if not isinstance(raw, dict):
        raise SpecError("collectors 的每一项必须是对象")
    key = raw.get("key")
    key = _need_name(key, "collector.key")
    extra = set(raw) - COLLECTOR_KEYS
    if extra:
        raise SpecError(f"collector 有不认识的键：{sorted(extra)}（{key}）")
    kind = raw.get("kind")
    if kind not in ("cmd", "file", "dir"):
        raise SpecError(f"collector.kind 只能是 cmd/file/dir：{key}={kind}")
    timeout = raw.get("timeout_sec", 120)
    if not _number(timeout) or timeout <= 0:
        raise SpecError(f"collector.timeout_sec 必须是正数：{key}")
    item = {
        "key": key,
        "kind": kind,
        "timeout_sec": float(timeout),
        "transport": _validate_transport(raw, key),
        "note": raw.get("note"),
    }
    if kind == "cmd":
        argv = raw.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
            raise SpecError(f"collector.argv 必须是非空字符串列表：{key}")
        item["argv"] = list(argv)
        fmt = raw.get("format", "json")
        if fmt not in FORMATS:
            raise SpecError(f"collector.format 只能是 json/regex：{key}={fmt}")
        item["format"] = fmt
        if fmt == "regex":
            if not _text(raw.get("pattern")):
                raise SpecError(f"regex 采集器缺 pattern：{key}")
            try:
                re.compile(raw["pattern"])
            except re.error as exc:
                raise SpecError(f"pattern 不是合法正则：{key}（{exc}）") from exc
        item["pattern"] = raw.get("pattern")
        pick = raw.get("pick")
        if pick is not None and not _text(pick):
            raise SpecError(f"collector.pick 必须是非空字符串：{key}")
        item["pick"] = pick
    else:
        path = raw.get("path")
        if not _text(path):
            raise SpecError(f"collector.path 必须是非空字符串：{key}")
        allowed = FILE_ASPECTS if kind == "file" else DIR_ASPECTS
        aspect = raw.get("aspect", "exists")
        if aspect not in allowed:
            raise SpecError(f"{kind} 采集器的 aspect 只能是 {sorted(allowed)}：{key}={aspect}")
        item["path"] = path.strip()
        item["aspect"] = aspect
        remote_argv = raw.get("remote_argv")
        if item["transport"]["type"] == "local" and remote_argv is not None:
            raise SpecError(f"本地采集器不接受 remote_argv（会被忽略却看起来生效）：{key}")
        if item["transport"]["type"] == "local" and (
                raw.get("format") not in (None, "json") or raw.get("pattern") is not None
                or raw.get("pick") is not None):
            raise SpecError(f"本地 file/dir 采集器不接受 format/pattern/pick（只用于远端命令输出）：{key}")
        if remote_argv is not None:
            if not isinstance(remote_argv, list) or not remote_argv or \
                    not all(isinstance(x, str) and x for x in remote_argv):
                raise SpecError(f"collector.remote_argv 必须是非空字符串列表：{key}")
            item["remote_argv"] = list(remote_argv)
            # 远端命令的输出形状与 cmd 采集器一致：允许 json/regex + pick
            fmt = raw.get("format", "json")
            if fmt not in FORMATS:
                raise SpecError(f"collector.format 只能是 json/regex：{key}={fmt}")
            item["format"] = fmt
            if fmt == "regex":
                if not _text(raw.get("pattern")):
                    raise SpecError(f"regex 采集器缺 pattern：{key}")
                try:
                    re.compile(raw["pattern"])
                except re.error as exc:
                    raise SpecError(f"pattern 不是合法正则：{key}（{exc}）") from exc
            item["pattern"] = raw.get("pattern")
            pick = raw.get("pick")
            if pick is not None and not _text(pick):
                raise SpecError(f"collector.pick 必须是非空字符串：{key}")
            item["pick"] = pick
        else:
            item["remote_argv"] = None
            item["format"] = "json"
            item["pattern"] = None
            item["pick"] = None
        if item["transport"]["type"] == "ssh" and not item["remote_argv"] \
                and not item["transport"].get("fetch_argv"):
            raise SpecError(
                f"远端路径采集器必须声明 remote_argv 或 transport.fetch_argv 之一：{key}"
                "（内核不猜远端怎么取数据）")
    return item


def validate_spec(raw) -> dict:
    """闭集校验并归一。⛔ 不认识的键、缺 round.kind、非 argv 命令都拒绝。"""
    if not isinstance(raw, dict):
        raise SpecError("spec 顶层必须是对象")
    extra = set(raw) - SPEC_KEYS
    if extra:
        raise SpecError(f"spec 有不认识的键：{sorted(extra)}")
    if int(raw.get("version", 0)) != SPEC_VERSION:
        raise SpecError(f"spec.version 必须是 {SPEC_VERSION}，实际 {raw.get('version')!r}")
    name = raw.get("name")
    if not _text(name):
        raise SpecError("spec.name 必须是非空字符串")
    owner = raw.get("owner")
    if owner is not None:
        owner = _need_name(owner, "spec.owner")

    state_raw = raw.get("state") or {}
    if not isinstance(state_raw, dict):
        raise SpecError("spec.state 必须是对象")
    extra = set(state_raw) - {"root", "db"}
    if extra:
        raise SpecError(f"spec.state 有不认识的键：{sorted(extra)}")
    state = {
        "root": state_raw.get("root") or STATE_ROOT_DEFAULT,
        "db": state_raw.get("db") or DB_DEFAULT,
    }
    for label, value in state.items():
        if not _text(value):
            raise SpecError(f"spec.state.{label} 必须是非空字符串")

    poll = raw.get("poll_sec", 20)
    if not _number(poll) or poll <= 0:
        raise SpecError("spec.poll_sec 必须是正数")

    quiet_raw = raw.get("quiet") or {}
    if not isinstance(quiet_raw, dict):
        raise SpecError("spec.quiet 必须是对象")
    extra = set(quiet_raw) - {"window_sec", "max_window_sec"}
    if extra:
        raise SpecError(f"spec.quiet 有不认识的键：{sorted(extra)}")
    window = quiet_raw.get("window_sec", 600)
    max_window = quiet_raw.get("max_window_sec", max(7200, float(window) if _number(window) else 7200))
    if not _number(window) or window < 0:
        raise SpecError("spec.quiet.window_sec 必须是非负数")
    if not _number(max_window) or max_window < window:
        raise SpecError("spec.quiet.max_window_sec 必须 ≥ window_sec")

    round_raw = raw.get("round")
    if not isinstance(round_raw, dict):
        raise SpecError("spec.round 必须是对象且带 kind（直令轮由配置识别，缺失即拒）")
    extra = set(round_raw) - {"kind", "basis", "note"}
    if extra:
        raise SpecError(f"spec.round 有不认识的键：{sorted(extra)}")
    round_kind = round_raw.get("kind")
    if round_kind not in ROUND_KINDS:
        raise SpecError(f"spec.round.kind 只能是 {sorted(ROUND_KINDS)}：{round_kind!r}")

    goal_raw = raw.get("goal")
    goal = None
    if goal_raw is not None:
        if not isinstance(goal_raw, dict):
            raise SpecError("spec.goal 必须是对象")
        extra = set(goal_raw) - {"state", "coverage", "basis"}
        if extra:
            raise SpecError(f"spec.goal 有不认识的键：{sorted(extra)}")
        state_value = goal_raw.get("state", "unknown")
        coverage = goal_raw.get("coverage", "unknown")
        if state_value not in GOAL_STATES:
            raise SpecError(f"spec.goal.state 只能是 {sorted(GOAL_STATES)}：{state_value!r}")
        if coverage not in GOAL_COVERAGE:
            raise SpecError(f"spec.goal.coverage 只能是 {sorted(GOAL_COVERAGE)}：{coverage!r}")
        goal = {"state": state_value, "coverage": coverage, "basis": goal_raw.get("basis")}

    overdue_raw = raw.get("overdue") or {}
    if not isinstance(overdue_raw, dict):
        raise SpecError("spec.overdue 必须是对象")
    extra = set(overdue_raw) - {"max_age_sec"}
    if extra:
        raise SpecError(f"spec.overdue 有不认识的键：{sorted(extra)}")
    max_age = overdue_raw.get("max_age_sec", 304)
    if not _number(max_age) or max_age <= 0:
        raise SpecError("spec.overdue.max_age_sec 必须是正数")

    collectors_raw = raw.get("collectors")
    if not isinstance(collectors_raw, list) or not collectors_raw:
        raise SpecError("spec.collectors 必须是非空数组")
    collectors = [_validate_collector(item, raw) for item in collectors_raw]
    keys = [item["key"] for item in collectors]
    if len(keys) != len(set(keys)):
        raise SpecError("collector.key 重复")

    events_raw = raw.get("events", [])
    if not isinstance(events_raw, list):
        raise SpecError("spec.events 必须是数组")
    events = []
    for item in events_raw:
        if not isinstance(item, dict):
            raise SpecError("events 的每一项必须是对象")
        extra = set(item) - EVENT_KEYS
        if extra:
            raise SpecError(f"event 有不认识的键：{sorted(extra)}")
        if item.get("key") not in keys:
            raise SpecError(f"event.key 没有对应采集器：{item.get('key')!r}")
        op = item.get("op")
        if op not in OPS:
            raise SpecError(f"event.op 只能是 {sorted(OPS)}：{op!r}")
        if op in ("increased_by", "decreased_by", "crossed") and not _number(item.get("value")):
            raise SpecError(f"{op} 需要数值 value：{item.get('key')}")
        direction = item.get("direction", "above")
        if op == "crossed" and direction not in ("above", "below"):
            raise SpecError("crossed.direction 只能是 above/below")
        if op == "mismatch" and "expected" not in item:
            raise SpecError(f"mismatch 需要 expected：{item.get('key')}")
        events.append({
            "key": item["key"], "op": op, "value": item.get("value"),
            "direction": direction, "expected": item.get("expected"), "note": item.get("note"),
        })

    return {
        "version": SPEC_VERSION,
        "name": name.strip(),
        "owner": owner,
        "state": state,
        "poll_sec": float(poll),
        "quiet": {"window_sec": float(window), "max_window_sec": float(max_window)},
        "round": {
            "kind": round_kind,
            "basis": round_raw.get("basis") or "spec.round.kind",
            "note": round_raw.get("note"),
        },
        "goal": goal,
        "overdue": {"max_age_sec": float(max_age)},
        "collectors": collectors,
        "events": events,
        "note": raw.get("note"),
    }


def load_spec(path) -> dict:
    """读 spec（容忍 BOM；PowerShell 写出的 JSON 常带 BOM）。"""
    target = Path(path)
    try:
        doc = json.loads(target.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise SpecError(f"spec 不存在：{target}") from exc
    except json.JSONDecodeError as exc:
        raise SpecError(f"spec 不是合法 JSON：{exc}") from exc
    return validate_spec(doc)


def spec_fingerprint(spec) -> str:
    """spec 指纹：spec 一改，前后观测就不可比（旧的比较自动降级成基准轮）。"""
    return _digest(spec)


# ---------------------------------------------------------------------------
# 进程与原始捕获（**不用管道**：stdout/stderr 直接写文件句柄）
# ---------------------------------------------------------------------------


class Capture:
    """一次进程执行的原始捕获：路径 + 字节数 + sha256 + 原文。

    ⚠️ 原文**不截断**：文件是主证据，`text` 是同内容的解码副本（便于事件账自带原文）。
    """

    def __init__(self, root: Path, capture_dir: Path, key: str):
        self.root = root
        self.dir = capture_dir / key
        self.stdout_path = self.dir / "stdout.bin"
        self.stderr_path = self.dir / "stderr.bin"
        self.meta_path = self.dir / "meta.json"

    def run(self, argv, *, timeout, cwd=None, transport="local", env_note=None) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        started = time.time()
        timed_out = False
        exit_code = None
        spawn_error = None
        try:
            with self.stdout_path.open("wb") as out, self.stderr_path.open("wb") as err:
                proc = subprocess.run(
                    list(argv), shell=False,
                    cwd=str(cwd) if cwd else None,
                    stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                    timeout=float(timeout),
                )
                exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = None
        except OSError as exc:
            spawn_error = f"{type(exc).__name__}: {exc}"
        duration = time.time() - started

        stdout_bytes = self.stdout_path.read_bytes() if self.stdout_path.is_file() else b""
        stderr_bytes = self.stderr_path.read_bytes() if self.stderr_path.is_file() else b""
        meta = {
            "key": self.dir.name,
            "argv": [str(item) for item in argv],
            "transport": transport,
            "cwd": str(cwd) if cwd else None,
            "timeout_sec": float(timeout),
            "timed_out": timed_out,
            "exit_code": exit_code,
            "spawn_error": spawn_error,
            "duration_sec": round(duration, 6),
            "started_at": _iso(started),
            "finished_at": _iso(time.time()),
            "env_note": env_note,
            "stdout_bytes": len(stdout_bytes),
            "stderr_bytes": len(stderr_bytes),
            "stdout_sha256": _digest(stdout_bytes),
            "stderr_sha256": _digest(stderr_bytes),
        }
        self.meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8", newline="\n")
        return {
            "dir": self.dir,
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
            "meta_path": self.meta_path,
            "stdout_bytes": stdout_bytes,
            "stderr_bytes": stderr_bytes,
            "stdout_text": stdout_bytes.decode("utf-8", "replace"),
            "stderr_text": stderr_bytes.decode("utf-8", "replace"),
            "stdout_sha256": meta["stdout_sha256"],
            "stderr_sha256": meta["stderr_sha256"],
            "exit_code": exit_code,
            "timed_out": timed_out,
            "spawn_error": spawn_error,
            "duration_sec": meta["duration_sec"],
            "argv": meta["argv"],
            "transport": transport,
        }

    def record(self, raw: dict, root: Path) -> dict:
        """给事件账用的可定位记录：路径 + 字节数 + sha256 + 原文（不截断）。"""
        return {
            "argv": raw["argv"],
            "transport": raw["transport"],
            "exit_code": raw["exit_code"],
            "timed_out": raw["timed_out"],
            "spawn_error": raw["spawn_error"],
            "duration_sec": raw["duration_sec"],
            "stdout": {
                "path": _rel_or_abs(root, raw["stdout_path"]),
                "bytes": len(raw["stdout_bytes"]),
                "sha256": raw["stdout_sha256"],
                "text": raw["stdout_text"],
            },
            "stderr": {
                "path": _rel_or_abs(root, raw["stderr_path"]),
                "bytes": len(raw["stderr_bytes"]),
                "sha256": raw["stderr_sha256"],
                "text": raw["stderr_text"],
            },
            "meta": _rel_or_abs(root, raw["meta_path"]),
        }


def _expand(template, values, label) -> list:
    """只替换 `{已知名}` 形态的占位符，其余花括号原样保留（模板里可能有 JSON / 代码片段）。"""
    def replace(match):
        name = match.group(1)
        if name not in values:
            raise CollectError("spec_error",
                               f"{label} 缺占位符取值：{name}（只允许 spec 声明的字段）")
        return str(values[name])

    return [_PLACEHOLDER_RE.sub(replace, item) for item in template]


# ---------------------------------------------------------------------------
# 传输适配器：本地 / ssh 命令 + 文件传输
# ---------------------------------------------------------------------------


class LocalTransport:
    type = "local"

    def __init__(self):
        self.connect_timeout_sec = None

    def run_argv(self, argv) -> list:
        return list(argv)

    def fetch_argv(self, source, destination) -> list:
        raise CollectError("transport_error", "local transport 没有文件传输：请改用 spec 里的本地路径")


class SshTransport:
    """ssh 命令与文件传输：**argv 模板与全部地址都来自 spec**，内核不写死任何主机或路径。"""

    type = "ssh"

    def __init__(self, config):
        self.host = config["host"]
        self.user = config["user"]
        self.port = config["port"]
        self.ssh_argv = config["ssh_argv"]
        self._fetch_argv = config.get("fetch_argv")
        self.connect_timeout_sec = config.get("connect_timeout_sec", 30)

    def _values(self, **extra):
        values = {"host": self.host, "user": self.user, "port": self.port}
        values.update(extra)
        return values

    def run_argv(self, argv) -> list:
        """远端命令：ssh argv（已替换地址）+ 一条作为单参数的远端命令。

        ⚠️ 本地仍然 `shell=False`；远端那一侧由登录 shell 解释，这是 ssh 的语义，
        所以 spec 里的远端 argv 必须是**自己就能安全执行的命令**，别把本地变量拼进去。
        """
        remote = shlex.join([str(item) for item in argv])
        return _expand(self.ssh_argv, self._values(), "transport.ssh_argv") + [remote]

    def fetch_argv(self, source, destination) -> list:
        if not self._fetch_argv:
            raise CollectError("spec_error", "ssh transport 没有 fetch_argv：无法取回远端文件/目录")
        return _expand(self._fetch_argv, self._values(source=str(source), destination=str(destination)),
                       "transport.fetch_argv")


def _transport_for(collector) -> LocalTransport | SshTransport:
    config = collector["transport"]
    if config["type"] == "ssh":
        return SshTransport(config)
    return LocalTransport()


# ---------------------------------------------------------------------------
# 采集器
# ---------------------------------------------------------------------------


def _parse_output(collector, text, capture) -> object:
    if collector["format"] == "regex":
        match = re.search(collector["pattern"], text)
        if not match:
            raise CollectError("unparsable", f"regex 没命中：{collector['key']}",
                               capture=capture, key=collector["key"])
        return match.group(1) if match.groups() else match.group(0)
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CollectError("unparsable", f"命令输出不是 JSON：{collector['key']}（{exc}）",
                           capture=capture, key=collector["key"]) from exc
    pick = collector.get("pick")
    if pick:
        current = doc
        for part in pick.split("."):
            if part == "":
                continue
            try:
                if isinstance(current, list):
                    current = current[int(part)]
                elif isinstance(current, dict):
                    current = current[part]
                else:
                    raise KeyError(part)
            except (KeyError, IndexError, ValueError) as exc:
                raise CollectError("unparsable", f"pick 路径不存在：{pick}（{collector['key']}）",
                                   capture=capture, key=collector["key"]) from exc
        return current
    return doc


def _need_path(path: Path, label: str) -> None:
    if not path.is_file():
        raise CollectError("missing", f"文件不存在：{label}")


def _aspect_value(collector, path: Path) -> object:
    kind, aspect = collector["kind"], collector["aspect"]
    if kind == "file":
        if aspect == "exists":
            return path.is_file()
        _need_path(path, str(path))
        if aspect == "size":
            return path.stat().st_size
        if aspect == "mtime":
            return round(path.stat().st_mtime, 3)
        if aspect == "line_count":
            with path.open("rb") as handle:
                return sum(1 for _ in handle)
        if aspect == "sha256":
            return _digest(path.read_bytes())
    else:
        if aspect == "exists":
            return path.is_dir()
        if not path.is_dir():
            raise CollectError("missing", f"目录不存在：{path}")
        if aspect == "file_count":
            return sum(1 for item in path.iterdir() if item.is_file())
        if aspect == "total_size":
            total = 0
            for item in path.rglob("*"):
                try:
                    if item.is_file():
                        total += item.stat().st_size
                except OSError:
                    continue
            return total
        newest = None
        for item in path.rglob("*"):
            try:
                stamp = item.stat().st_mtime
            except OSError:
                continue
            if newest is None or stamp > newest:
                newest = stamp
        return None if newest is None else round(newest, 3)
    raise CollectError("spec_error", f"未知 aspect：{kind}.{aspect}")


class _Round:
    """一轮采集的上下文（捕获目录、传输、时钟）。"""

    def __init__(self, root: Path, capture_root: Path, run_id: str, clock):
        self.root = root
        self.capture_root = capture_root
        self.run_id = run_id
        self.clock = clock
        self.captures = {}

    def capture_for(self, key: str) -> Capture:
        return Capture(self.root, self.capture_root / self.run_id, key)

    def run_cmd(self, collector, argv, transport, *, label=None) -> dict:
        capture = self.capture_for(collector["key"] if label is None else f"{collector['key']}__{label}")
        raw = capture.run(argv, timeout=collector["timeout_sec"],
                          cwd=self.root, transport=transport.type)
        record = capture.record(raw, self.root)
        self.captures[capture.dir.name] = record
        return {"raw": raw, "record": record}

    def fetch(self, collector, transport) -> tuple:
        if not isinstance(transport, SshTransport):
            raise CollectError("spec_error", "只有 ssh transport 需要取回文件")
        target_dir = self.capture_root / self.run_id / collector["key"] / "fetched"
        target_dir.mkdir(parents=True, exist_ok=True)
        remote = collector["path"]
        if collector["kind"] == "file":
            destination = target_dir / os.path.basename(remote.rstrip("/"))
        else:
            destination = target_dir
        argv = transport.fetch_argv(remote, destination)
        result = self.run_cmd(collector, argv, transport, label="fetch")
        raw = result["raw"]
        if raw["timed_out"]:
            raise CollectError("timeout", f"传输超时 {collector['timeout_sec']}s：{remote}",
                               capture=result["record"], key=collector["key"])
        if raw["exit_code"] != 0:
            raise CollectError("transport_error",
                               f"传输失败 rc={raw['exit_code']}：{remote}",
                               capture=result["record"], key=collector["key"])
        if not destination.exists():
            raise CollectError("missing", f"传输完成但目标不存在：{destination}",
                               capture=result["record"], key=collector["key"])
        return destination, result["record"]


def _collect_one(collector, ctx: _Round) -> tuple:
    """返回 (value, capture_record|None)。失败抛 CollectError（带原始捕获）。"""
    transport = _transport_for(collector)
    remote = transport.type == "ssh"
    if collector["kind"] == "cmd":
        result = ctx.run_cmd(collector, transport.run_argv(collector["argv"]), transport)
        raw = result["raw"]
        if raw["spawn_error"]:
            raise CollectError("transport_error",
                               f"进程起不来：{raw['spawn_error']}",
                               capture=result["record"], key=collector["key"])
        if raw["timed_out"]:
            raise CollectError("timeout", f"命令超时 {collector['timeout_sec']}s",
                               capture=result["record"], key=collector["key"])
        if raw["exit_code"] != 0:
            raise CollectError("nonzero_exit", f"命令非零退出 rc={raw['exit_code']}",
                               capture=result["record"], key=collector["key"])
        return _parse_output(collector, raw["stdout_text"], result["record"]), result["record"]

    if remote and collector.get("remote_argv"):
        result = ctx.run_cmd(collector, transport.run_argv(collector["remote_argv"]), transport)
        raw = result["raw"]
        if raw["spawn_error"]:
            raise CollectError("transport_error", f"进程起不来：{raw['spawn_error']}",
                               capture=result["record"], key=collector["key"])
        if raw["timed_out"]:
            raise CollectError("timeout", f"远端采集超时 {collector['timeout_sec']}s",
                               capture=result["record"], key=collector["key"])
        if raw["exit_code"] != 0:
            raise CollectError("nonzero_exit", f"远端命令非零退出 rc={raw['exit_code']}",
                               capture=result["record"], key=collector["key"])
        # 远端命令的输出形状与 cmd 采集器一致（JSON + pick 或 regex）
        probe_like = dict(collector, format=collector.get("format", "json"),
                          pattern=collector.get("pattern"), pick=collector.get("pick"))
        return _parse_output(probe_like, raw["stdout_text"], result["record"]), result["record"]

    if remote:
        local_path, record = ctx.fetch(collector, transport)
    else:
        local_path = Path(os.path.expanduser(collector["path"]))
        record = None
    return _aspect_value(collector, local_path), record


def collect_round(spec, *, root, capture_root, run_id, clock=time.time) -> dict:
    """跑完一轮采集。返回 `{ok, values, hashes, captures, failures}`；任一失败即整轮失败。"""
    ctx = _Round(Path(root), Path(capture_root), run_id, clock)
    values, hashes, captures, failures = {}, {}, {}, []
    for collector in spec["collectors"]:
        key = collector["key"]
        try:
            value, record = _collect_one(collector, ctx)
        except CollectError as exc:
            failures.append({
                "key": key,
                "kind": exc.kind,
                "detail": exc.detail,
                "capture": exc.capture,
            })
            continue
        values[key] = value
        hashes[key] = _digest(value)
        captures[key] = record
    return {
        "ok": not failures,
        "values": values,
        "hashes": hashes,
        "captures": captures,
        "failures": failures,
        "run_id": run_id,
        "collected_at": clock(),
        "spec_sha256": spec_fingerprint(spec),
    }


# ---------------------------------------------------------------------------
# 相邻两次观测的差（通用算子，不含任何领域语义）
# ---------------------------------------------------------------------------


def _numeric(value):
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


def eval_events(spec, previous, current) -> list:
    """纯函数：上一份观测 vs 这一份观测 ⇒ 变化列表。`previous is None` ⇒ 基准轮，零事件。"""
    if previous is None:
        return []
    out = []
    for rule in spec.get("events", []):
        key, op = rule["key"], rule["op"]
        before, after = previous.get(key), current.get(key)
        hit = False
        if op == "changed":
            hit = before != after
        elif op == "increased_by":
            left, right = _numeric(before), _numeric(after)
            hit = left is not None and right is not None and (right - left) >= rule["value"]
        elif op == "decreased_by":
            left, right = _numeric(before), _numeric(after)
            hit = left is not None and right is not None and (left - right) >= rule["value"]
        elif op == "appeared":
            hit = (before in (None, False, 0, "")) and (after not in (None, False, 0, ""))
        elif op == "disappeared":
            hit = (before not in (None, False, 0, "")) and (after in (None, False, 0, ""))
        elif op == "crossed":
            left, right = _numeric(before), _numeric(after)
            if left is not None and right is not None:
                if rule.get("direction", "above") == "above":
                    hit = left <= rule["value"] < right
                else:
                    hit = left >= rule["value"] > right
        elif op == "mismatch":
            hit = after != rule.get("expected")
        if hit:
            out.append({
                "key": key,
                "op": op,
                "before": before,
                "after": after,
                "before_sha256": _digest(before),
                "after_sha256": _digest(after),
                "expected": rule.get("expected"),
                "value": rule.get("value"),
                "direction": rule.get("direction", "above") if op == "crossed" else None,
                "what_changed": _diff_fields(before, after),
            })
    return out


# ---------------------------------------------------------------------------
# 事件账（sqlite 只追加 + sha 链）
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS progress_events(
  seq INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  at TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  prev_sha TEXT NOT NULL,
  sha256 TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS progress_events_no_update
BEFORE UPDATE ON progress_events
BEGIN SELECT RAISE(ABORT, 'progress_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS progress_events_no_delete
BEFORE DELETE ON progress_events
BEGIN SELECT RAISE(ABORT, 'progress_events is append-only'); END;
CREATE TABLE IF NOT EXISTS progress_lease(
  name TEXT PRIMARY KEY,
  owner TEXT NOT NULL,
  holder TEXT NOT NULL,
  fence INTEGER NOT NULL,
  acquired_ts REAL NOT NULL,
  renewed_ts REAL NOT NULL,
  expires_ts REAL NOT NULL,
  held INTEGER NOT NULL
);
"""


def _row_sha(seq, kind, payload_json, at, prev) -> str:
    return _digest("\n".join([str(seq), kind, payload_json, at, prev]))


def _verify_rows(rows) -> list:
    """逐行复核：seq 连续、前缀链一致、本行指纹一致、载荷规范。不符即拒（不是「通过」）。"""
    previous = GENESIS
    out = []
    for index, row in enumerate(rows, 1):
        if row["seq"] != index:
            raise ProgressError(f"事件账序号不连续（第 {index} 行 seq={row['seq']!r}）：疑似被删或插入")
        if row["prev_sha"] != previous:
            raise ProgressError(f"事件账前缀链不符（第 {index} 行）：疑似被改写或插入")
        expected = _row_sha(index, row["kind"], row["payload_json"], row["at"], previous)
        if expected != row["sha256"]:
            raise ProgressError(f"事件账内容指纹不符（第 {index} 行）：疑似被改写")
        try:
            payload = json.loads(row["payload_json"])
        except ValueError as exc:
            raise ProgressError(f"事件账第 {index} 行载荷不是合法 JSON：{exc}") from exc
        if _canonical(payload) != row["payload_json"]:
            raise ProgressError(f"事件账第 {index} 行载荷不是规范 JSON：疑似被改写")
        previous = row["sha256"]
        out.append({
            "seq": index,
            "locator": f"event:{index}",
            "ts": row["ts"],
            "at": row["at"],
            "kind": row["kind"],
            "payload": payload,
            "sha256": row["sha256"],
            "prev_sha": row["prev_sha"],
        })
    return out


# ---------------------------------------------------------------------------
# 主体
# ---------------------------------------------------------------------------


class Progress:
    """一个工作区根 + 一份 spec + 一份事件账 + 一个可注入的 goal / overdue 来源。"""

    def __init__(self, root, spec=None, *, spec_path=None, db_path=None, capture_root=None,
                 owner=None, goal=None, overdue=None, clock=time.time, holder=None,
                 ttl_sec=90.0):
        self.root = Path(root).resolve()
        if spec is None:
            if spec_path is None:
                raise SpecError("必须给 spec 或 spec_path")
            spec = load_spec(spec_path)
        self.spec = validate_spec(spec)  # 归一后再校验一次是幂等的；裸 dict 也必须过闭集检查
        self.spec_path = Path(spec_path).resolve() if spec_path else None
        state = self.spec["state"]
        self.db_path = Path(_resolve(self.root, db_path or state["db"], DB_DEFAULT)).resolve()
        self.capture_root = Path(_resolve(self.root, capture_root or state["root"],
                                          STATE_ROOT_DEFAULT)).resolve() / "captures"
        self.run_root = self.capture_root.parent / "runs"
        self.owner = _need_name(owner or self.spec.get("owner") or "owner", "owner")
        self.holder = holder or f"pid{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.clock = clock
        self.ttl_sec = float(ttl_sec)
        self._goal = goal
        self._overdue = overdue
        #: D2 源可信度：静态快照 / 任意 adapter 只作诊断，⛔ 不能凭它记 advance 或给命令。
        self._overdue_trust = _source_trust(overdue)
        self._fence = None

    # -- 构造 ---------------------------------------------------------------

    @classmethod
    def load(cls, root, spec_path, **kwargs):
        return cls(root, load_spec(spec_path), spec_path=spec_path, **kwargs)

    # -- 数据库 -------------------------------------------------------------

    def _connect(self, *, readonly=False):
        if readonly:
            if not self.db_path.is_file():
                return None
            conn = sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            return conn
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.executescript(_SCHEMA)
        return conn

    def _read(self, conn=None):
        own = conn is None
        conn = conn or self._connect(readonly=True)
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT seq,ts,at,kind,payload_json,prev_sha,sha256 FROM progress_events ORDER BY seq"
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return []
            raise ProgressError(f"事件库不可读：{exc}") from exc
        finally:
            if own:
                conn.close()
        return _verify_rows(rows)

    def _append_conn(self, conn, kind, payload, *, at=None, enforce_fence=True) -> dict:
        if kind not in EVENT_KINDS:
            raise ProgressError(f"未登记的事件种类：{kind}")
        if enforce_fence:
            self._assert_fence(conn)
        # ⚠️ 追加只核**最后一行**（前缀链 + 本行指纹），整链复核留给读路径：
        #    长跑时每轮都全量复核会把 O(行数) 变成每轮成本。读路径 `events()` 仍逐行复核。
        row = conn.execute(
            "SELECT seq,at,kind,payload_json,prev_sha,sha256 FROM progress_events"
            " ORDER BY seq DESC LIMIT 1").fetchone()
        if row is None:
            seq, previous = 1, GENESIS
        else:
            expected = _row_sha(row["seq"], row["kind"], row["payload_json"], row["at"],
                                row["prev_sha"])
            if expected != row["sha256"]:
                raise ProgressError(f"事件账末行指纹不符（seq={row['seq']}）：疑似被改写")
            seq, previous = int(row["seq"]) + 1, row["sha256"]
        ts = float(self.clock() if at is None else at)
        at_text = _iso(ts)
        payload = dict(payload)
        payload.setdefault("epoch", ts)
        payload.setdefault("owner", self.owner)
        payload.setdefault("holder", self.holder)
        payload_json = _canonical(payload)
        sha = _row_sha(seq, kind, payload_json, at_text, previous)
        conn.execute(
            "INSERT INTO progress_events(seq,ts,at,kind,payload_json,prev_sha,sha256)"
            " VALUES(?,?,?,?,?,?,?)",
            (seq, ts, at_text, kind, payload_json, previous, sha),
        )
        return {"seq": seq, "locator": f"event:{seq}", "kind": kind, "payload": payload,
                "ts": ts, "at": at_text, "sha256": sha, "prev_sha": previous}

    def append(self, kind, payload, *, enforce_fence=True) -> dict:
        """追加一条事件（登记自定义事实用）。写路径带 fence 校验：失锁者写不进去。"""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            event = self._append_conn(conn, kind, payload, enforce_fence=enforce_fence)
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return event

    def events(self, limit=None) -> list:
        events = self._read()
        return events[-limit:] if limit else events

    # -- 租约与围栏 ---------------------------------------------------------

    def _lease_row(self, conn):
        return conn.execute(
            "SELECT name,owner,holder,fence,acquired_ts,renewed_ts,expires_ts,held"
            " FROM progress_lease WHERE name=?", (LEASE_NAME,)).fetchone()

    def _assert_fence(self, conn) -> None:
        if self._fence is None:
            return
        row = self._lease_row(conn)
        now = float(self.clock())
        if row is None or not row["held"] or row["holder"] != self.holder \
                or int(row["fence"]) != int(self._fence) or float(row["expires_ts"]) <= now:
            raise FenceLost(
                "围栏失效：租约已过期、已释放或被别的实例接管（本实例 fence="
                f"{self._fence}，盘上={None if row is None else int(row['fence'])}）")

    def acquire(self, *, ttl_sec=None, note=None) -> dict:
        """取租约（同一 holder 重复取即续期，不换 fence）。拿不到抛 `LeaseBusy`。

        ⚠️ 第二个进程**哪怕 owner 相同**也拿不到：`holder` 是进程实例标识。
        """
        ttl = float(ttl_sec or self.ttl_sec)
        now = float(self.clock())
        conn = self._connect()
        busy = None
        result = None
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = self._lease_row(conn)
            if row is not None and row["held"] and float(row["expires_ts"]) > now \
                    and row["holder"] != self.holder:
                busy = {
                    "holder": row["holder"], "owner": row["owner"],
                    "fence": int(row["fence"]),
                    "expires_in": round(float(row["expires_ts"]) - now, 3),
                }
                self._append_conn(conn, "lease_busy", {
                    "requested_by": self.holder, "requested_owner": self.owner,
                    "held_by": busy["holder"], "held_owner": busy["owner"],
                    "fence": busy["fence"], "expires_in_sec": busy["expires_in"],
                    "reason": "同一时刻只允许一个有效推进来源；被拒者不改租约、不改别人的记录",
                }, at=now, enforce_fence=False)
            elif row is not None and row["held"] and row["holder"] == self.holder \
                    and float(row["expires_ts"]) > now:
                fence = int(row["fence"])
                conn.execute("UPDATE progress_lease SET renewed_ts=?, expires_ts=?, owner=?"
                             " WHERE name=?", (now, now + ttl, self.owner, LEASE_NAME))
                self._fence = fence
                self._append_conn(conn, "lease_renew", {
                    "fence": fence, "expires_in_sec": ttl, "note": note,
                }, at=now, enforce_fence=False)
                result = {"owner": self.owner, "holder": self.holder, "fence": fence,
                          "expires_in_sec": ttl, "renewed": True}
            else:
                previous_holder = row["holder"] if row is not None else None
                previous_fence = int(row["fence"]) if row is not None else 0
                fence = previous_fence + 1
                if row is None:
                    conn.execute(
                        "INSERT INTO progress_lease(name,owner,holder,fence,acquired_ts,renewed_ts,"
                        "expires_ts,held) VALUES(?,?,?,?,?,?,?,1)",
                        (LEASE_NAME, self.owner, self.holder, fence, now, now, now + ttl))
                else:
                    conn.execute(
                        "UPDATE progress_lease SET owner=?,holder=?,fence=?,acquired_ts=?,"
                        "renewed_ts=?,expires_ts=?,held=1 WHERE name=?",
                        (self.owner, self.holder, fence, now, now, now + ttl, LEASE_NAME))
                self._fence = fence
                if previous_holder and previous_holder != self.holder:
                    self._append_conn(conn, "lease_stolen", {
                        "fence": fence, "previous_holder": previous_holder,
                        "previous_fence": previous_fence,
                        "reason": "上一个租约已过期或已释放；围栏单调 +1，旧 fence 的写入会被拒",
                    }, at=now, enforce_fence=False)
                self._append_conn(conn, "lease_acquired", {
                    "fence": fence, "ttl_sec": ttl, "note": note,
                    "expires_in_sec": ttl,
                }, at=now, enforce_fence=False)
                result = {"owner": self.owner, "holder": self.holder, "fence": fence,
                          "expires_in_sec": ttl, "renewed": False}
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        if busy is not None:
            raise LeaseBusy(
                f"拿不到租约：{busy['holder']}（owner={busy['owner']}）在岗，"
                f"{busy['expires_in']}s 后过期；本进程未启动，不改租约、不杀别人",
                holder=busy["holder"], owner=busy["owner"], fence=busy["fence"],
                expires_in=busy["expires_in"])
        return result

    def renew(self, *, ttl_sec=None) -> dict:
        ttl = float(ttl_sec or self.ttl_sec)
        now = float(self.clock())
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_fence(conn)
            conn.execute("UPDATE progress_lease SET renewed_ts=?, expires_ts=? WHERE name=?",
                         (now, now + ttl, LEASE_NAME))
            event = self._append_conn(conn, "lease_renew", {
                "fence": self._fence, "expires_in_sec": ttl}, at=now)
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return {"owner": self.owner, "holder": self.holder, "fence": self._fence,
                "expires_in_sec": ttl, "locator": event["locator"]}

    def release(self) -> dict:
        conn = self._connect()
        fence = self._fence
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = self._lease_row(conn)
            if row is not None and row["holder"] == self.holder and row["held"]:
                conn.execute("UPDATE progress_lease SET held=0, expires_ts=? WHERE name=?",
                             (float(self.clock()), LEASE_NAME))
                self._append_conn(conn, "lease_released", {
                    "fence": int(row["fence"])}, enforce_fence=False)
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
            self._fence = None
        return {"owner": self.owner, "holder": self.holder, "fence": fence, "released": True}

    def lease(self) -> dict:
        """只读：当前租约与「我是不是那个在岗者」。"""
        conn = self._connect(readonly=True)
        if conn is None:
            return {"exists": False, "live": False, "mine": False}
        try:
            try:
                row = self._lease_row(conn)
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc).lower():
                    return {"exists": False, "live": False, "mine": False}
                raise ProgressError(f"租约表不可读：{exc}") from exc
        finally:
            conn.close()
        if row is None:
            return {"exists": False, "live": False, "mine": False}
        now = float(self.clock())
        live = bool(row["held"]) and float(row["expires_ts"]) > now
        return {
            "exists": True,
            "owner": row["owner"],
            "holder": row["holder"],
            "fence": int(row["fence"]),
            "live": live,
            "mine": bool(self._fence is not None and row["holder"] == self.holder
                         and int(row["fence"]) == int(self._fence)),
            "expires_in_sec": round(float(row["expires_ts"]) - now, 3),
            "held": bool(row["held"]),
        }

    # -- 事件派生状态（唯一真值 = 事件账）-----------------------------------

    def _last_observation(self, events):
        for event in reversed(events):
            if event["kind"] == "observation":
                return event
        return None

    def _failure_chain(self, events) -> list:
        """最近一段连续采集失败：只看「采集结果」事件，别被判定/唤醒记录打断。"""
        chain = []
        for event in reversed(events):
            if event["kind"] == "collect_failure":
                chain.append(event)
            elif event["kind"] == "observation":
                break
        return list(reversed(chain))

    def _failure_streak(self, events) -> int:
        return len(self._failure_chain(events))

    def _unavailable_since_success(self, events) -> dict | None:
        for event in reversed(events):
            if event["kind"] == "progress_unavailable":
                return event
            if event["kind"] == "observation":
                return None
        return None

    def _needs_rearm(self, events) -> dict:
        for event in reversed(events):
            if event["kind"] == "rearm":
                return {"value": False, "since": event["at"], "generation": event["payload"].get("generation")}
            if event["kind"] == "wake":
                return {"value": True, "since": event["at"],
                        "wake_id": event["payload"].get("wake_id"),
                        "job_id": event["payload"].get("job_id"),
                        "verdict": event["payload"].get("verdict"),
                        "reason": event["payload"].get("reason"),
                        "source": "wake", "seq": event["seq"],
                        "locator": event["locator"]}
            if event["kind"] == "watch_end":
                return {"value": True, "since": event["at"],
                        "wake_id": event["payload"].get("wake_id"),
                        "job_id": event["payload"].get("job_id"),
                        "verdict": event["payload"].get("verdict"),
                        "reason": event["payload"].get("reason"),
                        "source": "watch_end", "seq": event["seq"],
                        "locator": event["locator"]}
        return {"value": False, "since": None, "generation": 0}

    @staticmethod
    def _outcome_since(events, seq) -> list:
        return [event for event in events
                if event["kind"] == "outcome" and seq is not None and event["seq"] > seq]

    def _idle_streak(self, events) -> int:
        streak = 0
        for event in reversed(events):
            if event["kind"] != "outcome":
                continue
            kind = event["payload"].get("kind")
            if kind == "advance":
                break
            if kind == "problem":
                break
            if kind == "nothing":
                streak += 1
        return streak

    def _quiet_window(self, events) -> dict:
        streak = self._idle_streak(events)
        base = self.spec["quiet"]["window_sec"]
        cap = self.spec["quiet"]["max_window_sec"]
        factor = 2 ** (streak // 2)
        effective = min(base * factor, cap)
        return {"base_sec": base, "max_sec": cap, "effective_sec": effective, "idle_streak": streak}

    def _pending_changes(self, events, *, exclude_locator=None) -> list:
        """上一次唤醒/出口之后**还没被处理**的变化事件（probe 与 tick 分开跑时不丢改变）。"""
        pending = []
        for event in reversed(events):
            if exclude_locator and event["locator"] == exclude_locator:
                continue
            if event["kind"] in ("wake", "outcome"):
                break
            if event["kind"] == "change":
                pending.append(event)
        return list(reversed(pending))

    def _last_activity_ts(self, events) -> float | None:
        """静默判据的起点：最后一次真实动静（变化 / 唤醒 / 出口 / 基准观测）。"""
        for event in reversed(events):
            if event["kind"] in ("change", "wake", "outcome", "watch_end"):
                return float(event["ts"])
            if event["kind"] == "observation" and event["payload"].get("baseline"):
                return float(event["ts"])
        return None

    # -- goal / overdue 注入 -------------------------------------------------

    @staticmethod
    def _call(provider):
        return provider() if callable(provider) else provider

    def _goal_state(self, goal=None) -> dict:
        raw = self._call(goal if goal is not None else self._goal)
        if raw is None:
            raw = self.spec.get("goal")
        if raw is None:
            return {"state": "unknown", "coverage": "unknown", "known": False,
                    "why": "未注入 goal 状态（既没有 goal 回调，也没有 spec.goal）"}
        if not isinstance(raw, dict):
            return {"state": "unknown", "coverage": "unknown", "known": False,
                    "why": f"goal 来源不是对象：{type(raw).__name__}"}
        state = raw.get("state", "unknown")
        coverage = raw.get("coverage", "unknown")
        if state not in GOAL_STATES:
            return {"state": "unknown", "coverage": "unknown", "known": False,
                    "why": f"goal.state 非法：{state!r}"}
        if coverage not in GOAL_COVERAGE:
            return {"state": "unknown", "coverage": "unknown", "known": False,
                    "why": f"goal.coverage 非法：{coverage!r}"}
        exact = coverage in ("full", "none")
        known = state in ("active", "blocked", "complete") and exact
        return {
            "state": state,
            "coverage": coverage,
            "known": known,
            "why": raw.get("why") or raw.get("basis"),
            "source": "provider" if (goal is not None or self._goal is not None) else "spec",
        }

    def _overdue_state(self, overdue=None) -> dict:
        provider = overdue if overdue is not None else self._overdue
        max_age = self.spec["overdue"]["max_age_sec"]
        if provider is None:
            return {"checked": False, "count": 0, "items": [], "max_age_sec": max_age,
                    "why": "未注入 overdue 回调（无法核对入向超期；status 会标 unverified）"}
        try:
            raw = self._call(provider)
        except Exception as exc:  # noqa: BLE001 - 注入层可以抛任何错误，一律按不可判处理
            return {"checked": False, "count": 0, "items": [], "max_age_sec": max_age,
                    "error": f"{type(exc).__name__}: {exc}",
                    "why": "overdue 回调失败 ⇒ 不能断言「没有超期」，不许假绿"}
        if raw is None:
            return {"checked": False, "count": 0, "items": [], "max_age_sec": max_age,
                    "why": "overdue 回调返回空（未核对）"}
        if not isinstance(raw, dict):
            return {"checked": False, "count": 0, "items": [], "max_age_sec": max_age,
                    "error": f"overdue 来源不是对象：{type(raw).__name__}"}
        # ⚠️ 真实只读源（progress_host.read_overdue）会用 `checked=False` / `error` 表示
        # 「读不动 ⇒ 不可判」。这里必须原样尊重它，⛔ 不能把缺库 / 坏表读成 `checked=True`。
        if raw.get("checked") is False or raw.get("error"):
            return {"checked": False, "count": 0, "items": [], "max_age_sec": max_age,
                    "overdue": [],
                    "error": (raw.get("error") or raw.get("why")
                              or "overdue 来源报告 checked=False（不可判）")}
        limit = raw.get("max_age_sec", max_age)
        if not _number(limit):
            return {"checked": False, "count": 0, "items": [], "max_age_sec": max_age,
                    "error": "overdue.max_age_sec 不是数字"}
        items = []
        for item in raw.get("items") or []:
            if not isinstance(item, dict):
                continue
            age = item.get("age_sec")
            if not _number(age):
                continue
            items.append({"id": item.get("id"), "age_sec": float(age),
                          "summary": item.get("summary"), "seq": item.get("seq")})
        if not items and _number(raw.get("oldest_age_sec")):
            items = [{"id": raw.get("id"), "age_sec": float(raw["oldest_age_sec"]),
                      "summary": raw.get("summary"), "seq": raw.get("seq")}]
        overdue_items = [item for item in items if item["age_sec"] > float(limit)]
        return {
            "checked": True,
            "max_age_sec": float(limit),
            "items": items,
            "overdue": overdue_items,
            "count": len(overdue_items),
            "why": raw.get("why"),
        }

    # -- 采集 ---------------------------------------------------------------

    def _new_run_id(self) -> str:
        return f"{int(self.clock() * 1000)}-{uuid.uuid4().hex[:8]}"

    def probe(self) -> dict:
        """一轮采集 + 相邻比较 + 落账。⛔ 失败轮不做比较、不退化成「没有变化」。"""
        events_before = self._read()
        run_id = self._new_run_id()
        collection = collect_round(self.spec, root=self.root, capture_root=self.capture_root,
                                   run_id=run_id, clock=self.clock)
        last = self._last_observation(events_before)
        same_spec = bool(last and last["payload"].get("spec_sha256") == collection["spec_sha256"])
        previous = last["payload"].get("values") if (last and same_spec) else None

        if not collection["ok"]:
            chain = self._failure_chain(events_before)
            streak = len(chain) + 1
            first_failure = chain[0]["locator"] if chain else None
            failure_event = self.append("collect_failure", {
                "run_id": run_id,
                "failure_streak": streak,
                "failures": collection["failures"],
                "captures": collection["captures"],
                "spec_sha256": collection["spec_sha256"],
                "values_seen": collection["values"],
                "note": "fail-closed：本轮不做相邻比较，不许当成「没有变化」",
            })
            unavailable = self._unavailable_since_success(events_before)
            escalated = None
            if streak >= 3 and unavailable is None:
                escalated = self.append("progress_unavailable", {
                    "run_id": run_id,
                    "failure_streak": streak,
                    "failures": collection["failures"],
                    "captures": collection["captures"],
                    "first_failure": first_failure or failure_event["locator"],
                    "recovery": [
                        "先修采集链路：读 capture 里的原始 stdout/stderr/exit_code",
                        "确认命令、路径、超时与远端地址仍然是 spec 声明的那些",
                        "修好后重跑 probe；成功一轮会清零连续失败计数",
                    ],
                })
            return {
                "ok": False,
                "run_id": run_id,
                "values": collection["values"],
                "hashes": collection["hashes"],
                "captures": collection["captures"],
                "changes": [],
                "failures": collection["failures"],
                "failure_streak": streak,
                "unavailable": bool(escalated or unavailable),
                "failure_locator": failure_event["locator"],
                "unavailable_locator": (escalated or unavailable)["locator"]
                if (escalated or unavailable) else None,
                "baseline": previous is None,
                "spec_sha256": collection["spec_sha256"],
            }

        # 采集成功：清空失败链（事件账里由本行 observation 断开）
        changes = eval_events(self.spec, previous, collection["values"]) if previous is not None else []
        observation = self.append("observation", {
            "run_id": run_id,
            "baseline": previous is None,
            "spec_changed": bool(last and not same_spec),
            "values": collection["values"],
            "hashes": collection["hashes"],
            "captures": collection["captures"],
            "spec_sha256": collection["spec_sha256"],
        })
        change_event = None
        if changes:
            change_event = self.append("change", {
                "run_id": run_id,
                "observation": observation["locator"],
                "spec_sha256": collection["spec_sha256"],
                "changes": changes,
                "raw_captures": collection["captures"],
            })
        return {
            "ok": True,
            "run_id": run_id,
            "values": collection["values"],
            "hashes": collection["hashes"],
            "captures": collection["captures"],
            "changes": changes,
            "failures": [],
            "failure_streak": 0,
            "unavailable": False,
            "baseline": previous is None,
            "observation_locator": observation["locator"],
            "change_locator": change_event["locator"] if change_event else None,
            "spec_sha256": collection["spec_sha256"],
        }

    # -- 判定（纯函数式的实例方法：不写账）----------------------------------

    def decide(self, probe, *, goal=None, overdue=None) -> dict:
        events = self._read()
        pending_changes = self._pending_changes(events, exclude_locator=probe.get("change_locator"))
        goal_state = self._goal_state(goal)
        overdue_state = self._overdue_state(overdue)
        quiet = self._quiet_window(events)
        last_activity = self._last_activity_ts(events)
        now = float(self.clock())
        age = None if last_activity is None else max(0.0, now - last_activity)
        needs = self._needs_rearm(events)
        round_info = {
            "kind": self.spec["round"]["kind"],
            "basis": self.spec["round"]["basis"],
            "planning_required": self.spec["round"]["kind"] == "planned",
        }
        progress_summary = {
            "ok": bool(probe.get("ok")),
            "run_id": probe.get("run_id"),
            "baseline": bool(probe.get("baseline")),
            "change_count": len(probe.get("changes") or []),
            "change_locator": probe.get("change_locator"),
            "observation_locator": probe.get("observation_locator"),
            "failure_streak": int(probe.get("failure_streak") or 0),
            "unavailable": bool(probe.get("unavailable")),
            "failure_locator": probe.get("failure_locator"),
            "unavailable_locator": probe.get("unavailable_locator"),
            "hashes": probe.get("hashes") or {},
        }
        causes = []
        verdict = "sleep"
        reason = ""
        overdue_items = overdue_state.get("overdue") or []

        if overdue_state.get("error"):
            verdict = "undecidable"
            reason = "核不了入向超期（overdue 回调失败）⇒ 不能断言「没有待回复」，不许假绿"
            causes.append({"kind": "overdue_unknown", "detail": overdue_state.get("error")})
        elif overdue_items:
            verdict = "wake"
            oldest = max(item["age_sec"] for item in overdue_items)
            reason = (f"⚠️ 有 {len(overdue_items)} 条超期未回复入向（最久 {int(oldest)}s"
                      f" > {int(overdue_state['max_age_sec'])}s）⇒ 先回复用户；"
                      "该原因不看静默窗口，也不被 goal 抑制")
            causes.append({"kind": "overdue_user_message", "count": len(overdue_items),
                           "oldest_age_sec": oldest, "items": overdue_items[:20]})
        elif not probe.get("ok"):
            verdict = "wake"
            kinds = ", ".join(sorted({item["kind"] for item in probe.get("failures") or []}))
            if probe.get("unavailable"):
                reason = (f"采集连续 {probe.get('failure_streak')} 次失败（{kinds}）⇒ "
                          "采集链路不可用，按 fail-closed 唤醒；⛔ 这不等于「没有变化」")
                causes.append({"kind": "progress_unavailable",
                               "failure_streak": probe.get("failure_streak"),
                               "locator": probe.get("unavailable_locator"),
                               "failures": probe.get("failures")})
            else:
                reason = (f"采集失败（{kinds}）⇒ fail-closed 唤醒；⛔ 不许把失败读成「没有变化」"
                          f"（连续 {probe.get('failure_streak')} 次）")
                causes.append({"kind": "collect_failed",
                               "failure_streak": probe.get("failure_streak"),
                               "locator": probe.get("failure_locator"),
                               "failures": probe.get("failures")})
        elif probe.get("changes") or pending_changes:
            verdict = "wake"
            changes = list(probe.get("changes") or [])
            locator = probe.get("change_locator")
            for event in pending_changes:
                changes.extend(event["payload"].get("changes") or [])
                locator = event["locator"]
            summary = ", ".join(f"{item['key']}:{item['op']}" for item in changes[:8])
            reason = (f"外部进度有变化（{len(changes)} 条：{summary}）⇒ 唤醒负责人看变化；"
                      "载荷带原始字段、前后值与前后 hash")
            causes.append({"kind": "progress_change", "changes": changes, "locator": locator})
        elif not goal_state["known"]:
            verdict = "undecidable"
            if goal_state["coverage"] == "partial":
                reason = ("goal 覆盖状态是 partial（不确切）⇒ 无从判断该不该由外部推进器补位；"
                          "不许假绿，交负责人确认")
                causes.append({"kind": "goal_coverage_partial", "goal": goal_state})
            else:
                reason = ("goal 状态不可知（state/coverage 未知）⇒ 不知道有没有效推进来源，"
                          "不许假绿；先接 goal 状态再谈推进")
                causes.append({"kind": "goal_state_unknown", "goal": goal_state})
        elif goal_state["state"] == "active" and goal_state["coverage"] == "full":
            verdict = "sleep"
            reason = ("goal 主导且确切全覆盖 ⇒ 外部推进器不重复推进（H2）；"
                      "当前没有待交付变化事件，由 goal 那一侧推进")
            causes.append({"kind": "goal_covers", "goal": goal_state})
        elif age is None or age >= quiet["effective_sec"]:
            verdict = "wake"
            reason = (f"可推进却没人动它：距上一次真实动静 "
                      + ("自始（还没有基准观测）" if age is None else f"{int(age)}s")
                      + f"（阈值 {int(quiet['effective_sec'])}s，连续无事 {quiet['idle_streak']} 次）"
                      "⇒ 唤醒负责人")
            causes.append({"kind": "quiet_window", "age_sec": age,
                           "window_sec": quiet["effective_sec"]})
        else:
            verdict = "sleep"
            reason = (f"距上一次真实动静 {int(age)}s < 静默窗口 {int(quiet['effective_sec'])}s "
                      "⇒ 继续等，不打扰正在干活的人")
            causes.append({"kind": "quiet_not_elapsed", "age_sec": age,
                           "window_sec": quiet["effective_sec"]})

        supplement_allowed = bool(goal_state["known"] and goal_state["coverage"] == "none")
        return {
            "at": _iso(now),
            "epoch": now,
            "verdict": verdict,
            "reason": reason,
            "causes": causes,
            "goal": goal_state,
            "supplement": {
                "allowed": supplement_allowed,
                "why": ("goal 覆盖状态确切为空位 ⇒ 外部推进器可以补足"
                        if supplement_allowed else
                        "goal 覆盖状态不是确切空位 ⇒ 外部推进器不得推进"
                        if goal_state["known"] else
                        "goal 状态不可知 ⇒ 无可判的补位依据"),
            },
            "overdue": overdue_state,
            "progress": progress_summary,
            "quiet": dict(quiet, age_sec=age),
            "round": round_info,
            "planning_required": round_info["planning_required"],
            "needs_rearm": needs,
            "recovery": self._recovery(causes),
        }

    def _recovery(self, causes) -> list:
        kinds = {item["kind"] for item in causes}
        steps = []
        if "overdue_user_message" in kinds or "overdue_unknown" in kinds:
            steps.append("先回复用户（D2）：超期入向优先于任何推进动作")
        if "progress_unavailable" in kinds or "collect_failed" in kinds:
            steps.append("读 capture 里的原始 stdout/stderr/exit_code（事件载荷给路径与 sha256）")
            steps.append("修采集链路后重跑 probe；成功一轮会清零连续失败计数")
        if "goal_state_unknown" in kinds or "goal_coverage_partial" in kinds:
            steps.append("接入 goal 状态（progress_cli 的 --goal-json / --adapter），别假定它不覆盖")
        if "progress_change" in kinds:
            steps.append("读 change 事件的 changes 与 raw_captures，据变化决定推进或记录无事")
        if "quiet_window" in kinds:
            steps.append("照下一步动作推进；做完记录 outcome（advance / nothing / problem）")
        if "goal_covers" in kinds:
            steps.append("继续监听外部事件；当前无新事件，由 goal 那一侧推进")
        if not steps:
            steps.append("继续等待；若确认无事可做，记录 outcome nothing 并写下下次判定条件")
        steps.append("任何唤醒都会被记成 needs_rearm：处理完必须显式 rearm 再挂回（A6）")
        return steps

    # -- 单轮 ---------------------------------------------------------------

    def tick(self, *, goal=None, overdue=None, ttl_sec=None, hold_lease=False) -> dict:
        """单轮：采集 + 判定 + 落账。自己取租约（`hold_lease=False` 时本轮结束即释放）。"""
        needs = self._needs_rearm(self._read())
        if needs["value"]:
            return {"verdict": "pending_rearm", "reason":
                    f"上一个唤醒还没重挂（{needs.get('wake_id')} / {needs.get('reason')}）⇒ "
                    "先记录 outcome 再 rearm，⛔ 不允许在同一 owner 上再挂一个推进器",
                    "needs_rearm": needs, "intended_exit": EXIT_CODES["pending_rearm"]}
        acquired = False
        if self._fence is None:
            try:
                self.acquire(ttl_sec=ttl_sec)
                acquired = True
            except LeaseBusy as exc:
                return {"verdict": "lease_busy", "reason": str(exc),
                        "lease": {"holder": exc.holder, "owner": exc.owner, "fence": exc.fence,
                                  "expires_in_sec": exc.expires_in},
                        "intended_exit": EXIT_CODES["lease"]}
        try:
            result = self._round(goal=goal, overdue=overdue)
        finally:
            if acquired and not hold_lease:
                self.release()
        result["intended_exit"] = self._wake_exit_code(result)
        return result

    def _round(self, *, goal=None, overdue=None) -> dict:
        probe = self.probe()
        result = self.decide(probe, goal=goal, overdue=overdue)
        decision = self.append("decision", {
            "verdict": result["verdict"],
            "reason": result["reason"],
            "causes": [{k: v for k, v in cause.items()
                        if k in ("kind", "count", "oldest_age_sec", "failure_streak", "locator",
                                 "age_sec", "window_sec")}
                       for cause in result["causes"]],
            "goal": result["goal"],
            "supplement": result["supplement"],
            "overdue": {k: v for k, v in result["overdue"].items() if k != "items"},
            "quiet": result["quiet"],
            "round": result["round"],
            "progress": {k: v for k, v in result["progress"].items() if k != "hashes"},
            "need_human": result["verdict"] in ("wake", "undecidable"),
        })
        result["decision_locator"] = decision["locator"]
        if result["verdict"] in ("wake", "undecidable"):
            wake_id = f"W-{int(result['epoch'])}-{uuid.uuid4().hex[:6]}"
            intended = self._wake_exit_code(result)
            wake = self.append("wake", {
                "wake_id": wake_id,
                "verdict": result["verdict"],
                "reason": result["reason"],
                "causes": result["causes"],
                "decision": decision["locator"],
                "intended_exit": intended,
                "host_woken": False,
                "requires_host_notification": True,
                "needs_rearm": True,
                "recovery": result["recovery"],
                "round": result["round"],
                "goal": result["goal"],
                "quiet": result["quiet"],
                "progress": {k: v for k, v in result["progress"].items() if k != "hashes"},
            })
            result["wake_id"] = wake_id
            result["wake_locator"] = wake["locator"]
            result["host_woken"] = False
            result["requires_host_notification"] = True
            result["needs_rearm"] = {"value": True, "wake_id": wake_id,
                                     "reason": result["reason"], "since": wake["at"],
                                     "locator": wake["locator"]}
        return result

    @staticmethod
    def _wake_exit_code(result) -> int:
        if result.get("verdict") == "wake":
            return EXIT_CODES["collect_failed"] if not result["progress"]["ok"] else EXIT_CODES["ok"]
        if result.get("verdict") == "undecidable":
            return EXIT_CODES["undecidable"]
        if result.get("verdict") == "pending_rearm":
            return EXIT_CODES["pending_rearm"]
        if result.get("verdict") in ("lease_busy", "lease_lost"):
            return EXIT_CODES["lease"]
        if result.get("verdict") == "exhausted":
            return EXIT_CODES["cap_reached"]
        return EXIT_CODES["error"] if result.get("verdict") == "error" else EXIT_CODES["ok"]

    # -- 阻塞监视（受管后台作业本体）----------------------------------------

    def watch(self, *, poll=None, max_rounds=0, goal=None, overdue=None, ttl_sec=None) -> dict:
        """阻塞轮询：一次唤醒（或不可判）就退出，并写 needs_rearm。

        ⚠️ **它叫不醒宿主**：只有宿主把本进程当受管后台作业、并在其结束时通知负责人，
        负责人才会醒。输出与落盘记录恒写 `host_woken: false` / `self_restart: false`。
        ⚠️ **无固定寿命**：不设「到点自退」。`max_rounds` 是调用方显式给的上限（试跑/自检用），
        到达时写 `verdict=exhausted` + needs_rearm，⛔ 不假装成功。
        """
        needs = self._needs_rearm(self._read())
        if needs["value"]:
            return {"verdict": "pending_rearm", "reason":
                    f"上一个唤醒还没重挂（{needs.get('wake_id')}）⇒ 先记录 outcome 再 rearm",
                    "needs_rearm": needs, "host_woken": False,
                    "intended_exit": EXIT_CODES["pending_rearm"]}
        try:
            lease = self.acquire(ttl_sec=ttl_sec)
        except LeaseBusy as exc:
            return {"verdict": "lease_busy", "reason": str(exc),
                    "lease": {"holder": exc.holder, "owner": exc.owner, "fence": exc.fence,
                              "expires_in_sec": exc.expires_in},
                    "host_woken": False, "intended_exit": EXIT_CODES["lease"]}
        job_id = f"J-{int(self.clock() * 1000)}-{uuid.uuid4().hex[:6]}"
        interval = float(poll if poll is not None else self.spec["poll_sec"])
        started = float(self.clock())
        rounds = 0
        record = {
            "job_id": job_id,
            "owner": self.owner,
            "holder": self.holder,
            "spec_path": str(self.spec_path) if self.spec_path else None,
            "spec_sha256": spec_fingerprint(self.spec),
            "db": str(self.db_path),
            "capture_root": str(self.capture_root),
            "pid": os.getpid(),
            "managed_by": "host-background-job",
            "self_restart": False,
            "host_woken": False,
            "requires_host_notification": True,
            "poll_sec": interval,
            "lease": lease,
            "started_at": _iso(started),
            "rounds": 0,
            "verdict": None,
            "reason": None,
            "intended_exit": None,
            "actual_exit": None,
            "needs_rearm": None,
        }
        self._write_run_record(job_id, record)
        result = {"job_id": job_id, "rounds": 0, "host_woken": False,
                  "requires_host_notification": True, "self_restart": False}
        try:
            while True:
                rounds += 1
                try:
                    self.renew(ttl_sec=ttl_sec)
                    round_result = self._round(goal=goal, overdue=overdue)
                except FenceLost as exc:
                    result = {"job_id": job_id, "rounds": rounds, "verdict": "lease_lost",
                              "reason": str(exc), "host_woken": False,
                              "requires_host_notification": True,
                              "self_restart": False,
                              "needs_rearm": {"value": True, "reason": str(exc),
                                              "verdict": "lease_lost"},
                              "intended_exit": EXIT_CODES["lease"]}
                    self.append("watch_end", {
                        "job_id": job_id, "verdict": "lease_lost", "reason": str(exc),
                        "rounds": rounds, "intended_exit": EXIT_CODES["lease"],
                        "host_woken": False,
                    }, enforce_fence=False)
                    self._finish_run_record(job_id, result, record, started)
                    return result
                result = round_result
                result["rounds"] = rounds
                record.update({
                    "rounds": rounds,
                    "verdict": result["verdict"],
                    "reason": result["reason"],
                    "intended_exit": self._wake_exit_code(result),
                    "needs_rearm": result.get("needs_rearm"),
                    "updated_at": _iso(float(self.clock())),
                    "last_decision": result.get("decision_locator"),
                    "wake_id": result.get("wake_id"),
                })
                self._write_run_record(job_id, record)
                if result["verdict"] in ("wake", "undecidable"):
                    return self._finish_watch(result, record, job_id, started)
                if max_rounds and rounds >= int(max_rounds):
                    capped = {
                        "job_id": job_id,
                        "rounds": rounds,
                        "verdict": "exhausted",
                        "reason": (f"调用方给的轮数上限 {max_rounds} 到了：**没有唤醒**；"
                                   "已写 needs_rearm，必须显式重挂（⛔ 这不是成功，也不是静默退出）"),
                        "host_woken": False,
                        "requires_host_notification": True,
                        "intended_exit": EXIT_CODES["cap_reached"],
                        "round": result.get("round"),
                        "decision_locator": result.get("decision_locator"),
                    }
                    self.append("watch_end", {
                        "job_id": job_id, "verdict": "exhausted", "rounds": rounds,
                        "reason": capped["reason"],
                        "intended_exit": EXIT_CODES["cap_reached"],
                        "host_woken": False,
                        "decision": result.get("decision_locator"),
                    })
                    return self._finish_watch(capped, record, job_id, started)
                time.sleep(interval)
        except BaseException as exc:  # noqa: BLE001 - 任何中断都留判词，不静默消失
            failure = {
                "job_id": job_id, "rounds": rounds, "verdict": "error",
                "reason": f"{type(exc).__name__}: {exc}",
                "host_woken": False, "requires_host_notification": True,
                "self_restart": False,
                "needs_rearm": {"value": True, "reason": f"{type(exc).__name__}: {exc}",
                                "verdict": "error"},
                "intended_exit": EXIT_CODES["error"],
            }
            try:
                self.append("watch_end", {
                    "job_id": job_id, "verdict": "error", "rounds": rounds,
                    "reason": failure["reason"], "intended_exit": EXIT_CODES["error"],
                    "host_woken": False,
                }, enforce_fence=False)
            except Exception:  # noqa: BLE001 - 落账失败不改退出语义
                pass
            self._finish_run_record(job_id, failure, record, started)
            raise
        finally:
            try:
                self.release()
            except Exception:  # noqa: BLE001 - 释放失败不改退出语义
                pass

    def _finish_watch(self, result, record, job_id, started) -> dict:
        result = dict(result)
        result.setdefault("job_id", job_id)
        result["host_woken"] = False
        result["requires_host_notification"] = True
        result["self_restart"] = False
        result["intended_exit"] = self._wake_exit_code(result)
        result["needs_rearm"] = {
            "value": True,
            "wake_id": result.get("wake_id"),
            "reason": result.get("reason"),
            "verdict": result.get("verdict"),
        }
        result["next"] = ("由 owner 显式重挂：先 outcome（advance / nothing / problem），"
                          "再 rearm，然后重跑同一条 watch 命令；本进程不会自续")
        result["host_notice"] = (
            "watch 只是把话说了：它自己叫不醒宿主会话。宿主把本进程当受管后台作业、"
            "并在其结束时投递通知，负责人才会醒；⛔ 不把本记录读成「宿主已被唤醒」。")
        record.update({
            "verdict": result.get("verdict"),
            "reason": result.get("reason"),
            "intended_exit": result["intended_exit"],
            "updated_at": _iso(float(self.clock())),
            "finished_at": _iso(float(self.clock())),
            "needs_rearm": {"value": True, "wake_id": result.get("wake_id"),
                            "reason": result.get("reason")},
            "next": result["next"],
        })
        self._write_run_record(job_id, record)
        return result

    def record_watch_exit(self, result, actual_exit) -> dict:
        """CLI 退出前补 `actual_exit`：退出码可能被后台作业压成 1，真值必须落盘（A7）。"""
        job_id = result.get("job_id")
        intended = result.get("intended_exit")
        event = self.append("watch_exit", {
            "job_id": job_id,
            "verdict": result.get("verdict"),
            "intended_exit": intended,
            "actual_exit": int(actual_exit),
            "reason": result.get("reason"),
            "host_woken": False,
            "note": "退出码在后台作业语境下可能被归一成 1 ⇒ 判词与预期退出码以本记录为准",
        }, enforce_fence=False)
        if job_id:
            record = self._read_run_record(job_id) or {}
            record.update({
                "actual_exit": int(actual_exit),
                "intended_exit": intended,
                "verdict": result.get("verdict"),
                "reason": result.get("reason"),
                "exit_locator": event["locator"],
                "finished_at": _iso(float(self.clock())),
            })
            self._write_run_record(job_id, record)
        return {"locator": event["locator"], "intended_exit": intended,
                "actual_exit": int(actual_exit)}

    def _write_run_record(self, job_id, record) -> Path:
        self.run_root.mkdir(parents=True, exist_ok=True)
        path = self.run_root / f"{job_id}.json"
        temporary = path.with_name(path.name + ".tmp")
        payload = dict(record)
        payload.setdefault("job_id", job_id)
        payload["updated_at"] = _iso(float(self.clock()))
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
                             encoding="utf-8", newline="\n")
        os.replace(temporary, path)
        return path

    def _read_run_record(self, job_id) -> dict | None:
        path = self.run_root / f"{job_id}.json"
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def run_record(self, job_id) -> dict | None:
        return self._read_run_record(job_id)

    def _finish_run_record(self, job_id, result, record, started) -> None:
        record.update({
            "verdict": result.get("verdict"),
            "reason": result.get("reason"),
            "intended_exit": result.get("intended_exit"),
            "rounds": result.get("rounds"),
            "updated_at": _iso(float(self.clock())),
            "finished_at": _iso(float(self.clock())),
            "next": result.get("next") or "读 needs_rearm 与 outcome；处理完显式 rearm 再挂回",
        })
        self._write_run_record(job_id, record)

    # -- 重挂与出口 ---------------------------------------------------------

    def rearm(self, *, note=None, job_id=None, ttl_sec=None) -> dict:
        """owner 显式重挂：清 needs_rearm、代次 +1，并给出下一条 watch 命令。"""
        events = self._read()
        needs = self._needs_rearm(events)
        if not needs["value"]:
            raise ProgressError("没有待重挂的作业（needs_rearm 已经是 false）；"
                                "rearm 只用于处理一次已发生的唤醒/收工")
        if needs.get("source") == "wake" and not self._outcome_since(events, needs.get("seq")):
            raise ProgressError(
                "这次唤醒还没有记录出口（H7）：先 outcome advance / nothing(+next_condition) / "
                "problem，再 rearm；⛔ 不许跳过出口直接重挂")
        generation = 1 + sum(1 for event in events if event["kind"] == "rearm")
        event = self.append("rearm", {
            "generation": generation,
            "previous_wake_id": needs.get("wake_id"),
            "previous_job_id": needs.get("job_id"),
            "job_id": job_id,
            "note": note,
            "needs_rearm": False,
        }, enforce_fence=False)
        command = self.watch_command()
        return {
            "ok": True,
            "generation": generation,
            "needs_rearm": {"value": False, "since": event["at"]},
            "locator": event["locator"],
            "next_command": command,
            "note": ("重挂是显式动作：接下来用 next_command 重新挂成受管后台作业；"
                     "⛔ 本工具不会自己起服务，也不会自续"),
        }

    def outcome(self, kind, *, action=None, next_condition=None, problem=None, note=None,
                sources=None, safe_action=None, next_plan=None, topic=None, owner=None,
                intents=None, argv=None) -> dict:
        """一次唤醒的出口：advance / nothing(+next_condition) / problem(+issue_id)。

        `advance` 是「持续推进下一轮」，落账前必须核**当前真实**入向超期（D2）：
        超期 ⇒ 拒并指明先回复用户；来源不可判 ⇒ 拒（⛔ 不把未知记成没有超期）。
        安全收尾（stop/collect/...）用显式 `safe_action=` 声明（L10），它跳过超期闸，
        但**不能**同时声明下一轮；`next_round` 遇到这种出口只返回 `safety_only`。
        """
        if kind not in OUTCOME_KINDS:
            raise ProgressError(f"outcome.kind 只能是 {sorted(OUTCOME_KINDS)}：{kind!r}")
        if safe_action is not None and kind != "advance":
            raise ProgressError("safe_action 只用于 advance 记录安全收尾；nothing / problem 本来就不受阻")
        next_given = any(value is not None for value in (next_plan, topic, owner, intents, argv))
        if next_given and kind != "advance":
            raise ProgressError("只有 advance 能声明下一轮（next_plan/topic/owner/intents/argv）")
        events = self._read()
        needs = self._needs_rearm(events)
        if not needs["value"]:
            raise ProgressError("没有待处置的唤醒：先有一次 wake（或 watch_end）再记录 outcome")
        if self._outcome_since(events, needs.get("seq")):
            raise ProgressError(
                "这次唤醒已经记录过出口：一次唤醒只能有一个出口（advance / nothing / problem）")
        payload = {"kind": kind, "wake_id": needs.get("wake_id"),
                   "job_id": needs.get("job_id"), "note": note}
        if kind == "advance":
            if not _text(action):
                raise ProgressError("advance 必须写清做了什么（action 非空）")
            payload["action"] = action
            if safe_action is not None:
                value = str(safe_action).strip().lower()
                if value not in SAFE_ACTIONS:
                    raise ProgressError(
                        f"safe_action 只能是 {list(SAFE_ACTIONS)}：{safe_action!r}"
                        "（它不是任意旁路；安全收尾也可以改用 nothing / problem 出口）")
                if next_given:
                    raise ProgressError(
                        "advance 声明 safe_action 时不能再声明下一轮：安全收尾不是下一轮，"
                        "⛔ 不许拿它当进入下一轮的凭据")
                payload["safe_action"] = value
                payload["overdue_gate"] = {
                    "skipped": True, "why": "safe_action（L10）：安全收尾不受入向超期闸阻断"}
            else:
                if next_given:
                    payload["next"] = self._next_declaration(
                        next_plan=next_plan, topic=topic, owner=owner, intents=intents, argv=argv)
                payload["overdue_gate"] = self._require_no_overdue()
        elif kind == "nothing":
            if not _text(next_condition):
                raise ProgressError("nothing 必须写下一次判定条件（next_condition 非空）——"
                                    "「无事」不是结论，条件才是")
            payload["next_condition"] = next_condition
        else:
            if not _text(problem):
                raise ProgressError("problem 必须写清是什么问题（problem 非空）")
            payload["problem"] = problem
            # 问题号绑定这次唤醒（一次唤醒只能有一个出口 ⇒ 号唯一，且不用猜下一个 seq）
            payload["issue_id"] = f"ISSUE-{needs.get('seq')}"
        if sources is not None:
            if not isinstance(sources, list) or not all(isinstance(item, str) and item for item in sources):
                raise ProgressError("sources 必须是字符串列表（先写路径或事件定位）")
            payload["sources"] = list(sources)
        event = self.append("outcome", payload, enforce_fence=False)
        return {
            "ok": True,
            "outcome": payload,
            "locator": event["locator"],
            "issue_id": payload.get("issue_id"),
            "safety_only": bool(payload.get("safe_action")),
            "needs_rearm": needs,
            "next": "记录 outcome 不等于重挂：仍需显式 rearm（A6）",
        }

    def _require_no_overdue(self) -> dict:
        """D2：advance 之前核当前真实入向超期。不可判 ⇒ 拒；超期 ⇒ 拒并指明先回复用户。"""
        if self._overdue_trust != "trusted":
            raise OutcomeBlocked(
                "D2 源不可信（trust=" + str(self._overdue_trust) + "）：⛔ 不能凭静态快照 / "
                "任意 adapter 的 count=0 记 advance；生产入口固定接 workspace 库的只读 "
                "read_overdue（已认证入向与回执），或改用 nothing / problem，或用 safe_action "
                "记录安全收尾")
        state = self._overdue_state()
        base = {
            "checked": bool(state.get("checked")),
            "max_age_sec": state.get("max_age_sec"),
            "count": int(state.get("count") or 0),
        }
        if not state.get("checked"):
            reason = state.get("error") or state.get("why") or "未注入可核对的 overdue 源"
            raise OutcomeBlocked(
                "核不了当前入向超期（" + str(reason) + "）：⛔ 不能把未知记成 advance；"
                "先接入只读 overdue 源（progress_cli 的 --overdue-json / --adapter）并确认"
                "没有待回复消息，或改用 nothing / problem 出口，或用 safe_action 记录安全收尾")
        items = state.get("overdue") or []
        if items:
            oldest = max(float(item.get("age_sec") or 0) for item in items)
            ids = [item.get("id") for item in items[:10] if item.get("id")]
            raise OutcomeBlocked(
                f"有 {len(items)} 条超期未回复入向（最久 {int(oldest)}s > "
                f"{int(float(state.get('max_age_sec') or 0))}s）：先回复用户（D2），"
                f"⛔ 不记 advance 假绿；ids={ids}")
        base["oldest_age_sec"] = None
        base["items"] = []
        base["trust"] = self._overdue_trust
        base["source"] = "provider" if self._overdue is not None else "none"
        return base

    def _next_declaration(self, *, next_plan, topic, owner, intents, argv) -> dict:
        """把「下一轮」声明归一：五件必须同时给，⛔ 不生成计划、不生成授权。"""
        if next_plan is None:
            raise ProgressError(
                "声明下一轮必须同时给 next_plan / topic / owner / intents / argv（五件，缺一不可）")
        topic_value = _need_name(topic, "topic")
        owner_value = _need_name(owner, "owner")
        intent_value = _intent_declaration(intents, root=self.root)
        argv_value = _argv_list(argv)
        if isinstance(next_plan, dict):
            plan_value = json.loads(json.dumps(next_plan))
        elif _text(next_plan):
            plan_value = str(next_plan).strip().replace("\\", "/")
        else:
            raise ProgressError("next_plan 必须是工作区内相对路径或计划对象")
        return {"plan": plan_value, "topic": topic_value, "owner": owner_value,
                "intents": intent_value, "argv": argv_value}

    # -- 下一轮：跨轮状态（只读） -------------------------------------------

    def _round_transition(self, outcome, *, topic, owner, intents, argv, plan) -> dict:
        """最近一次出口能不能作为「开下一轮」的凭据。⛔ 不从「完成了」推断下一轮。"""
        payload = outcome.get("payload") or {}
        kind = payload.get("kind")
        common = {"outcome_locator": outcome.get("locator"), "outcome_kind": kind,
                  "wake_id": payload.get("wake_id")}
        if kind == "advance" and payload.get("safe_action"):
            return dict(common, ok=False, state="safety_only",
                        safe_action=payload.get("safe_action"),
                        reason=("这次出口是安全收尾（safe_action=" + str(payload.get("safe_action"))
                                + "）：不进入下一轮，⛔ 不给新实验命令"))
        if kind == "nothing":
            return dict(common, ok=False, state="waiting",
                        next_condition=payload.get("next_condition"),
                        reason=("本轮出口是 nothing（下次判定条件："
                                + str(payload.get("next_condition")) + "）：没有要开的下一轮"))
        if kind == "problem":
            return dict(common, ok=False, state="problem_open",
                        problem=payload.get("problem"), issue_id=payload.get("issue_id"),
                        reason="本轮出口是 problem（未闭合问题）：先处置问题，再谈下一轮")
        if kind != "advance":
            return dict(common, ok=False, state="unknown",
                        reason=f"不认识的历史出口：{kind!r}")
        declaration = payload.get("next")
        if not isinstance(declaration, dict) or not declaration:
            return dict(common, ok=False, state="legacy_next_unknown",
                        reason=("这次 advance 没有写下显式下一动作（next_plan/topic/owner/intents/argv）："
                                "⛔ 不把「上一轮完成了」读成「该开下一轮」，也不能凭它绿 advance"))
        problems = []
        if topic is None or declaration.get("topic") != topic:
            problems.append(f"已记录的下一动作指向专题 {declaration.get('topic')!r}，与本次 {topic!r} 不一致")
        if owner is None or declaration.get("owner") != owner:
            problems.append(f"已记录的下一动作负责人是 {declaration.get('owner')!r}，与本次 {owner!r} 不一致")
        if not _same_plan(declaration.get("plan"), plan):
            problems.append("已记录的下一动作计划与本次 next_plan 不一致")
        if intents is None or declaration.get("intents") != intents:
            problems.append("已记录的下一动作意图声明与本次 intents 不一致")
        if argv is None or list(declaration.get("argv") or []) != list(argv):
            problems.append("已记录的下一动作 argv 与本次 argv 不一致")
        if problems:
            return dict(common, ok=False, state="next_mismatch", problems=problems,
                        reason="已记录的下一动作与本次调用不一致：⛔ 不换计划、不换 argv、不换负责人")
        return dict(common, ok=True, state="advance_declared", next=declaration,
                    reason="已记录的显式下一动作与本次调用一致：可以作为开下一轮的凭据")

    def _round_state(self, *, topic=None, owner=None, intents=None, argv=None, plan=None) -> dict:
        """只读：本次「下一轮」在跨轮协议上处于什么状态（A6 / H7）。"""
        events = self._read()
        needs = self._needs_rearm(events)
        if needs["value"]:
            wake = needs.get("seq")
            outcome = next((event for event in reversed(events)
                            if event["kind"] == "outcome" and wake is not None
                            and event["seq"] > wake), None)
            base = {"needs_rearm": True, "wake_locator": needs.get("locator"),
                    "wake_id": needs.get("wake_id")}
            if outcome is None:
                return dict(base, ok=False, state="outcome_missing",
                            reason=("这次唤醒还没有记录出口（advance / nothing / problem）："
                                    "先 outcome，再谈下一轮（A6 / H7）"))
            return dict(base, **self._round_transition(
                outcome, topic=topic, owner=owner, intents=intents, argv=argv, plan=plan))
        outcome = next((event for event in reversed(events) if event["kind"] == "outcome"), None)
        if outcome is None:
            return {"needs_rearm": False, "ok": True, "state": "first_round",
                    "reason": "还没有任何唤醒与出口：这是首轮（不依赖上一轮完成）"}
        return dict({"needs_rearm": False}, **self._round_transition(
            outcome, topic=topic, owner=owner, intents=intents, argv=argv, plan=plan))

    # -- 下一轮：只读预检 ---------------------------------------------------

    def _gate_wiring(self, workspace, db_path, *, monitor_check=_UNSET,
                     authorization_check=_UNSET, verifier=_UNSET):
        """解析（或注入）三个固定接线：监控 / 执行准入 / 审计核验。⛔ 没有跳过开关。"""
        notes = []
        monitor = monitor_check
        if monitor is _UNSET:
            try:
                from .workflow_cli import host_monitor
                monitor, why = host_monitor(workspace)
                if why:
                    notes.append(why)
            except Exception as exc:  # noqa: BLE001 - 接线失败也要 fail-closed
                monitor = None
                notes.append(f"监控生产接线不可用：{type(exc).__name__}: {exc}")
        authorization = authorization_check
        if authorization is _UNSET:
            try:
                from .runtime.store import ReadonlyStore
                from .workflow_cli import host_authorization
                authorization, why = host_authorization(
                    workspace, ReadonlyStore(db_path), readonly=True)
                if why:
                    notes.append(why)
            except Exception as exc:  # noqa: BLE001
                authorization = None
                notes.append(f"执行准入生产接线不可用：{type(exc).__name__}: {exc}")
        checker = verifier
        if checker is _UNSET:
            try:
                from .workflow_cli import _host_verifier
                checker = _host_verifier()
            except Exception:  # noqa: BLE001 - 没有核验器只会让审计停在 declared
                checker = None
        return monitor, authorization, checker, notes

    def _readonly_workflow(self, workspace, db_path, *, monitor_check=_UNSET,
                           authorization_check=_UNSET, verifier=_UNSET,
                           execution_target=None, require_provision=True):
        """只读起跑闸：`Workflow(readonly=True)` + 生产接线（或调用方注入的夹具）。"""
        from .execution_target import ExecutionTarget
        from .workflow import Workflow
        monitor, authorization, checker, notes = self._gate_wiring(
            workspace, db_path, monitor_check=monitor_check,
            authorization_check=authorization_check, verifier=verifier)
        target = execution_target or ExecutionTarget(workspace, require_provision=require_provision)
        flow = Workflow(str(workspace), db=db_path, readonly=True,
                        verifier=checker, monitor_check=monitor,
                        authorization_check=authorization,
                        execution_target=target)
        return flow, notes

    def _preflight_assurance(self, workspace, *, topic, owner, plan_obj, db_path, now,
                             assurance, record, host_source=_UNSET, services=None):
        """合并 `assurance.checklist` 的只读汇总：解释清单，不是硬闸的替代。"""
        if assurance is None:
            record("assurance", "unknown",
                   "调用方没有提供 assurance 汇总：不知道哪些 required 检查失效 ⇒ "
                   "⛔ 不以 optional 聚合器解释放行", {"wired": False, "skipped": True},
                   blocking=True)
            return None
        function = assurance if callable(assurance) else None
        if function is None and assurance is _UNSET:
            try:
                # 用 importlib 显式按模块名取，避免 `from . import assurance` 命中包属性
                # 导致的「先 import 真模块、后 patch sys.modules 不生效」的顺序问题。
                function = getattr(importlib.import_module("kernel.autoresearch.assurance"),
                                   "checklist", None)
            except Exception:  # noqa: BLE001 - 模块不存在 ⇒ 未接线（不许当放行）
                function = None
        if not callable(function):
            record("assurance", "unknown",
                   "assurance.checklist 未接线（模块不存在或未注入）：不知道哪些 required 检查失效 ⇒ "
                   "⛔ 不给执行命令", {"wired": False}, blocking=True)
            return None
        host = None
        if host_source is not _UNSET:
            host = host_source
        else:
            try:
                from .host_evidence import DSHHost
                host = DSHHost()
            except Exception:  # noqa: BLE001 - 拿不到宿主就按 host=None 交给 assurance（它会判 unknown）
                host = None
        try:
            payload = {
                "workspace": str(workspace), "topic": topic,
                "exp_id": (plan_obj or {}).get("id") if isinstance(plan_obj, dict) else None,
                "owner_session": owner, "db": str(db_path),
                "host": host, "now": now, "required": True,
            }
            if services is not None:
                payload["services"] = services
            raw = _call_with_supported(function, payload)
            summary = _assurance_summary(raw)
        except Exception as exc:  # noqa: BLE001 - 汇总失败不冒充通过：不知道失效了哪些 required 检查
            summary = {"state": "unknown", "blocking": True, "items": [],
                       "why": f"assurance 调用失败（{type(exc).__name__}: {exc}）："
                              "不知道失效了哪些 required 检查 ⇒ ⛔ 不给执行命令"}
        record("assurance", summary["state"], summary.get("why") or "assurance 汇总已合并（解释清单）",
               summary, blocking=bool(summary.get("blocking")))
        return summary

    def _launch_command(self, workspace, db_path, topic, plan_rel, owner, intents, argv) -> list:
        """可执行的下一条 launch 命令：绝对路径，可从任意 cwd 跑（⛔ 不靠相对 kernel/）。"""
        cli = Path(__file__).with_name("workflow_cli.py").resolve()
        parts = [sys.executable, "-X", "utf8", "-B", str(cli),
                 "launch", "--workspace", str(Path(workspace).resolve())]
        if db_path:
            parts += ["--db", str(Path(db_path).resolve())]
        parts += ["--topic", topic, "--plan", plan_rel, "--owner-session", owner]
        if intents["none"]:
            parts.append("--intent-none")
        elif intents["intent_id"]:
            parts += ["--intent-id", intents["intent_id"]]
        parts += ["--", *argv]
        return parts

    def _rearm_argv(self) -> list:
        cli = Path(__file__).with_name("progress_cli.py").resolve()
        parts = [sys.executable, "-X", "utf8", "-B", str(cli), "--root", str(self.root)]
        if self.spec_path:
            parts += ["--spec", str(self.spec_path)]
        parts += ["rearm", "--owner", self.owner]
        return parts

    def preflight(self, *, next_plan, topic=None, owner=None, intents=None, argv=None,
                  workspace=None, db=None, stage=None, resources=None, overdue=_UNSET,
                  assurance=_UNSET, require_provision=True, monitor_check=_UNSET,
                  authorization_check=_UNSET, verifier=_UNSET, execution_target=None,
                  remote_preflight=_UNSET, allow_fixture_gates=False,
                  assurance_host=_UNSET, assurance_services=None, clock=None) -> dict:
        """只读预检：现在能不能按这份**已冻结**计划开下一轮。

        硬判据复用 `Workflow(readonly=True).check`（计划 / 准备来源 / argv / J5 / 审计 /
        监控 / 准入 / 真实源码），再加只读 overdue 与 assurance 解释清单。全过才给
        `next_command`；⛔ 不生成计划、不写盘、不伪造授权。
        """
        now = float(clock() if callable(clock) else (clock if clock is not None else self.clock()))
        root = Path(workspace or self.root).resolve()
        db_path = Path(db).resolve() if db else (root / ".autoresearch" / "runtime.sqlite3")
        checks, order = {}, []
        missing, blocked, unknown = [], [], []

        def record(name, state, reason=None, evidence=None, blocking=True):
            if state not in CHECK_STATES:
                raise ProgressError(f"预检状态非法：{state!r}")
            if name not in checks:
                order.append(name)
            checks[name] = {"state": state, "reason": reason or "",
                            "evidence": evidence or {}, "blocking": bool(blocking)}
            # 覆盖同一项时，把它从旧的状态桶里撤掉（例：远端分支改判 execution_target）。
            for bucket in (missing, blocked, unknown):
                if name in bucket:
                    bucket.remove(name)
            bucket = {"pass": None, "missing": missing, "blocked": blocked,
                      "unknown": unknown}[state]
            if bucket is not None and name not in bucket:
                bucket.append(name)

        declarations = []
        topic_value = owner_value = intent_value = argv_value = None
        try:
            topic_value = _need_name(topic, "topic")
        except ProgressError as exc:
            declarations.append(str(exc))
        try:
            owner_value = _need_name(owner, "owner")
        except ProgressError as exc:
            declarations.append(str(exc))
        try:
            intent_value = _intent_declaration(intents, root=root)
        except ProgressError as exc:
            declarations.append(str(exc))
        try:
            argv_value = _argv_list(argv)
        except ProgressError as exc:
            declarations.append(str(exc))
        check_plan = cli_plan = None
        try:
            check_plan, cli_plan = _plan_argument(root, next_plan)
        except ProgressError as exc:
            declarations.append(str(exc))

        gate = None
        gate_error = None
        gate_notes = []
        if declarations:
            record("declarations", "missing", "；".join(declarations))
            for name in ("plan_frozen", "topic", "plan_sources", "argv", "predecessor",
                         "execution_target", "audit", "monitoring", "admission"):
                record(name, "unknown", "前置声明不全，未调用起跑闸")
        else:
            record("declarations", "pass", "topic / owner / intents / argv / next_plan 都是显式给的")
            try:
                flow, gate_notes = self._readonly_workflow(
                    root, db_path, monitor_check=monitor_check,
                    authorization_check=authorization_check, verifier=verifier,
                    execution_target=execution_target, require_provision=require_provision)
                gate = flow.check(topic=topic_value, plan=check_plan,
                                  owner_session=owner_value, argv=argv_value,
                                  intent_id=intent_value["intent_id"],
                                  intent_none=intent_value["none"])
            except Exception as exc:  # noqa: BLE001 - 缺库 / 旧 schema / WAL / 计划读不出
                gate_error = exc

        if gate_error is not None:
            message = f"{type(gate_error).__name__}: {gate_error}"
            if any(marker in str(gate_error) for marker in
                   ("计划文件不存在", "计划不是合法 JSON", "计划必须是 JSON 对象")):
                state = "missing" if "计划文件不存在" in str(gate_error) else "blocked"
                record("plan_frozen", state, message)
            for name in ("plan_frozen", "topic", "plan_sources", "argv", "predecessor",
                         "execution_target", "audit", "monitoring", "admission"):
                if name not in checks:
                    record(name, "unknown",
                           f"起跑闸不可判（缺库 / 缺表 / 旧 schema / WAL 边界）：{message}")
        elif gate is not None:
            problems_by = _gate_problems_by_check(gate.get("problems"))
            gate_checks = gate.get("checks") or {}

            plan_info = gate_checks.get("plan")
            if plan_info and not problems_by.get("plan_frozen"):
                record("plan_frozen", "pass", "计划与已冻结版本逐字节一致",
                       {"version": plan_info.get("version"),
                        "key_fingerprint": plan_info.get("key_fingerprint")})
            elif problems_by.get("plan_frozen"):
                record("plan_frozen", "blocked", "；".join(problems_by["plan_frozen"]))
            else:
                record("plan_frozen", "missing", "计划还没有冻结过：先 prepare（会冻结并记录哈希）")

            topic_info = gate_checks.get("topic")
            if problems_by.get("topic"):
                record("topic", "blocked", "；".join(problems_by["topic"]), topic_info)
            elif topic_info:
                record("topic", "pass", "专题已登记且状态为 active", topic_info)
            else:
                record("topic", "unknown", "起跑闸没有给出专题状态")

            preparation = gate_checks.get("preparation")
            if problems_by.get("plan_sources"):
                record("plan_sources", "blocked", "；".join(problems_by["plan_sources"]),
                       {"prepared_id": (preparation or {}).get("prepared_id")})
            elif preparation:
                record("plan_sources", "pass", "准备记录在，来源哈希未过期，before 快照在",
                       {"prepared_id": preparation.get("prepared_id"),
                        "source_fingerprint": preparation.get("source_fingerprint")})
            else:
                record("plan_sources", "missing", "这一轮还没有 prepare 记录：先 prepare")

            if problems_by.get("argv"):
                record("argv", "blocked", "；".join(problems_by["argv"]))
            elif preparation:
                record("argv", "pass", "实际 argv 与事前声明逐项相等", {"argv": argv_value})
            else:
                record("argv", "missing", "没有准备记录可比对 argv")

            if problems_by.get("predecessor"):
                record("predecessor", "blocked", "；".join(problems_by["predecessor"]))
            else:
                record("predecessor", "pass", "同专题没有未结束 / 未复盘的轮次，也没有重跑同一计划")

            target_info = gate_checks.get("execution_target")
            if problems_by.get("execution_target") or (
                    isinstance(target_info, dict) and target_info.get("ok") is not True):
                details = problems_by.get("execution_target") or [str(target_info)]
                record("execution_target", "blocked", "；".join(details),
                       {key: (target_info or {}).get(key) for key in
                        ("cwd", "python", "provision", "isolation_verified")})
            elif isinstance(target_info, dict):
                record("execution_target", "pass", "真实独立代码落点与环境已核",
                       {key: target_info.get(key) for key in
                        ("cwd", "python", "provision", "isolation_verified")})
            else:
                record("execution_target", "unknown", "起跑闸没有给出执行落点")

            audit_info = gate_checks.get("audit") or {}
            if audit_info.get("ok") is True:
                record("audit", "pass", "当前审计通过（不是 declared）",
                       {"decision": audit_info.get("decision"),
                        "key_fingerprint": audit_info.get("key_fingerprint")})
            elif problems_by.get("audit") or audit_info:
                record("audit", "blocked",
                       "；".join(problems_by.get("audit")
                                or [f"审计状态未通过（decision={audit_info.get('decision')!r}）"]),
                       {"decision": audit_info.get("decision")})
            else:
                record("audit", "unknown", "起跑闸没有给出审计状态")

            monitor_info = gate_checks.get("monitor") or {}
            if monitor_info.get("ok") is True:
                record("monitoring", "pass", "Monitoring.check_start 放行（唯一意图权威）",
                       {"state": monitor_info.get("state"),
                        "registered": monitor_info.get("registered"),
                        "intent_id": monitor_info.get("intent_id")})
            elif monitor_info:
                details = (problems_by.get("monitoring") or monitor_info.get("problems")
                           or ["监控检查未放行"])
                record("monitoring", "blocked", "；".join(str(item) for item in details),
                       {"unwired": monitor_info.get("unwired"),
                        "fixture": monitor_info.get("fixture")})
            else:
                record("monitoring", "unknown", "起跑闸没有给出监控状态")

            admission_info = gate_checks.get("authorization") or {}
            if admission_info.get("ok") is True:
                record("admission", "pass", "Admission 放行（共享预算预留 / 作用域 / 决策）",
                       {"decision": admission_info.get("decision"),
                        "scope": admission_info.get("scope")})
            elif admission_info:
                details = (problems_by.get("admission") or admission_info.get("problems")
                           or ["执行准入未放行"])
                record("admission", "blocked", "；".join(str(item) for item in details),
                       {"unwired": admission_info.get("unwired"),
                        "fixture": admission_info.get("fixture")})
            else:
                record("admission", "unknown", "起跑闸没有给出执行准入状态")

        # ---- 计划元数据（阶段 / 资源）：只用于排序解释，现状一律写清来源 ----
        plan_obj, plan_source, plan_sha = _load_plan_metadata(root, next_plan)
        execution_spec = plan_obj.get("execution") if isinstance(plan_obj, dict) else None
        execution_spec = execution_spec if isinstance(execution_spec, dict) else {}
        execution_scope = str(execution_spec.get("scope") or "local").strip()
        stage_info = _stage_of(plan_obj, stage)
        resource_info = _resources_of(plan_obj, resources)
        remote_command = None
        if execution_scope != "local":
            explicit_keys = _raw_resource_keys(resources) if resources is not None else None
            declared_keys = _plan_remote_resources(plan_obj)
            resources_for_remote = list(explicit_keys if explicit_keys is not None
                                        else (declared_keys or []))
            remote = None
            remote_why = None
            if resources is not None and explicit_keys is None:
                remote_why = "显式 resources 形状不合法（需要字符串数组或 {keys/resources}）"
            elif explicit_keys is not None and declared_keys is not None \
                    and list(explicit_keys) != list(declared_keys):
                remote_why = ("显式 resources 与冻结计划 execution.remote.resources 逐项不一致："
                              f"显式 {explicit_keys} vs 计划 {declared_keys}（⛔ 不覆盖计划）")
            elif not resource_info.get("known") and declared_keys is None:
                remote_why = ("远端执行需要显式正式 resource_key"
                              "（冻结计划 execution.remote.resources 或显式 resources）："
                              + str(resource_info.get("why") or "资源未声明"))
            elif remote_preflight is None:
                remote_why = "远端预检被调用方显式禁用（remote_preflight=None）：⛔ 不给命令"
            else:
                function = remote_preflight if callable(remote_preflight) else None
                if function is None and remote_preflight is _UNSET:
                    try:
                        from . import progress_remote
                        function = getattr(progress_remote, "remote_preflight", None)
                    except Exception as exc:  # noqa: BLE001 - 模块缺失不能当作已检查
                        remote_why = f'远端预检模块不可用（{type(exc).__name__}）'
                        function = None
                # 只有调用方显式注入时才把三个回调交给远端 helper；生产传 None，
                # 让 helper 用 remote_cli._space(readonly=True) 的固定接线。
                wiring = {
                    "monitor_check": None if monitor_check is _UNSET else monitor_check,
                    "authorization_check": (None if authorization_check is _UNSET
                                            else authorization_check),
                    "verifier": None if verifier is _UNSET else verifier,
                }
                if callable(function):
                    payload = {
                        "workspace": str(root), "db": str(db_path),
                        "plan": next_plan, "topic": topic_value,
                        "owner_session": owner_value, "argv": argv_value,
                        "resources": resources_for_remote, "intents": intent_value,
                        "now": now, "clock": clock,
                        "allow_fixture_gates": bool(allow_fixture_gates),
                        **wiring,
                    }
                    try:
                        remote = _call_with_supported(function, payload)
                        # 冻结计划的 execution.remote.resources 是权威：文件投影落后时按它重跑一次
                        # （只读；⛔ 不用调用方参数覆盖计划）。
                        if explicit_keys is None and isinstance(remote, dict):
                            plan_meta = remote.get("plan_meta")
                            plan_resources = (plan_meta or {}).get("plan_resources") \
                                if isinstance(plan_meta, dict) else None
                            if isinstance(plan_resources, list):
                                frozen_keys = [str(item) for item in plan_resources]
                                if frozen_keys != resources_for_remote:
                                    resources_for_remote = frozen_keys
                                    payload["resources"] = frozen_keys
                                    remote = _call_with_supported(function, payload)
                    except Exception as exc:  # noqa: BLE001 - 远端预检坏掉不许放行
                        remote_why = f"远端预检调用失败：{type(exc).__name__}: {exc}"
                else:
                    remote_why = remote_why or '远端预检没有可调用的 remote_preflight 接口'
            if remote is None:
                record("execution_target", "unknown", remote_why,
                       {"scope": execution_scope})
                record("remote", "unknown", remote_why,
                       {"scope": execution_scope, "resources": resources_for_remote},
                       blocking=True)
            else:
                normalized = _normalize_remote_result(remote)
                for key, bucket in (("missing", missing), ("blocked", blocked),
                                    ("unknown", unknown)):
                    for item in normalized.get(key) or []:
                        label = f"remote.{item}"
                        if label not in bucket:
                            bucket.append(label)
                evidence = {
                    "scope": execution_scope,
                    "resources": resources_for_remote,
                    "status": normalized["state"],
                    "checks": normalized["checks"],
                    "blocked": normalized["blocked"],
                    "unknown": normalized["unknown"],
                    "missing": normalized["missing"],
                    "problems": normalized["items"],
                    "wiring": remote.get("wiring") if isinstance(remote, dict) else None,
                }
                if normalized["ok"]:
                    record("execution_target", "pass",
                           "远端执行落点由远端预检验证（本地 launch 不参与）",
                           {"scope": execution_scope})
                    record("remote", "pass", "远端预检通过（统一闸 + 远端绑定）", evidence)
                    remote_command = normalized["command"]
                else:
                    state = "unknown" if normalized["state"] == "unknown" else "blocked"
                    record("execution_target", state, normalized["why"],
                           {"scope": execution_scope})
                    record("remote", state, normalized["why"], evidence, blocking=True)
        # 调用方显式阶段 / 资源与冻结计划矛盾或非法 ⇒ 阻断（⛔ 不用调用方参数覆盖计划换绿）。
        metadata_problems = []
        if stage is not None and not stage_info.get("known"):
            metadata_problems.append("stage：" + str(stage_info.get("why")))
        if resources is not None and not resource_info.get("known"):
            metadata_problems.append("resources：" + str(resource_info.get("why")))
        if metadata_problems:
            record("plan_metadata", "blocked", "；".join(metadata_problems))
        else:
            record("plan_metadata", "pass",
                   "调用方显式阶段 / 资源与计划声明一致（或没有覆盖计划）")

        # ---- 只读 overdue：缺源 / 不可信 ⇒ 不给命令（D2） ----
        if overdue is _UNSET:
            overdue_state = self._overdue_state()
            source_trust = self._overdue_trust
            overdue_source = "provider" if self._overdue is not None else "none"
        else:
            overdue_state = self._overdue_state(overdue)
            source_trust = _source_trust(overdue)
            overdue_source = "snapshot"
        if source_trust != "trusted":
            record("overdue", "unknown",
                   "D2 源不可信（trust=" + str(source_trust) + "）：静态快照 / 任意 adapter "
                   "的 count=0 ⛔ 不能当「没有超期」；生产固定接 workspace 库的只读 read_overdue",
                   {"source": overdue_source, "trust": source_trust})
        elif overdue_state.get("error") or not overdue_state.get("checked"):
            reason = overdue_state.get("error") or overdue_state.get("why") or "没有可核对的源"
            record("overdue", "unknown",
                   "入向超期不可判（" + str(reason) + "）：⛔ 不能断言没有超期，不给执行命令",
                   {"source": overdue_source, "trust": source_trust})
        elif int(overdue_state.get("count") or 0) > 0:
            items = overdue_state.get("overdue") or []
            oldest = max(float(item.get("age_sec") or 0) for item in items)
            ids = [item.get("id") for item in items[:10] if item.get("id")]
            record("overdue", "blocked",
                   f"有 {len(items)} 条超期未回复入向（最久 {int(oldest)}s）：先回复用户（D2）"
                   f"；ids={ids}", {"source": overdue_source, "trust": source_trust})
        else:
            record("overdue", "pass", "当前没有超期未回复入向",
                   {"max_age_sec": overdue_state.get("max_age_sec"),
                    "source": overdue_source, "trust": source_trust,
                    "checked": overdue_state.get("checked"),
                    "count": overdue_state.get("count")})

        # ---- assurance：解释清单（required+applicable 的 blocked/unknown 会拦） ----
        self._preflight_assurance(root, topic=topic_value, owner=owner_value,
                                  plan_obj=plan_obj, db_path=db_path, now=now,
                                  assurance=assurance, record=record,
                                  host_source=assurance_host,
                                  services=assurance_services)

        # ---- 命令：只有全过且计划有工作区内路径才给 ----
        blocking_failures = [name for name in order
                             if checks[name]["blocking"] and checks[name]["state"] != "pass"]
        command = None
        command_text = None
        if blocking_failures:
            states = {checks[name]["state"] for name in blocking_failures}
            record("command", "blocked" if states & {"missing", "blocked"} else "unknown",
                   "预检未全过 ⇒ 不给执行命令：" + "、".join(blocking_failures))
        elif execution_scope != "local":
            if remote_command is None:
                record("command", "unknown",
                       "远端预检未给出可执行远端 start 命令 ⇒ 不给命令（⛔ 不冒用本地 launch）")
            else:
                command = list(remote_command)
                command_text = " ".join(shlex.quote(part) for part in command)
                record("command", "pass", "已给出可执行的远端 start 命令",
                       {"scope": execution_scope, "argv": command})
        elif cli_plan is None:
            record("command", "unknown",
                   "next_plan 是内存对象，没有工作区内相对路径：CLI 命令需要显式路径 ⇒ 不给命令")
        else:
            command = self._launch_command(root, db_path, topic_value, cli_plan,
                                           owner_value, intent_value, argv_value)
            command_text = " ".join(shlex.quote(part) for part in command)
            record("command", "pass", "已给出可执行的下一轮 launch 命令",
                   {"path": cli_plan, "argv": command})

        blocking_failures = [name for name in order
                             if checks[name]["blocking"] and checks[name]["state"] != "pass"]
        blocking_states = {checks[name]["state"] for name in blocking_failures}
        if not blocking_failures:
            status = "runnable"
        elif blocking_states & {"missing", "blocked"}:
            status = "blocked"
        else:
            status = "unknown"
        ok = status == "runnable" and command is not None
        if not ok:
            status = "blocked" if (command is None and status == "runnable") else status
        ordered_checks = {name: checks[name] for name in PREFLIGHT_CHECKS if name in checks}
        ordered_checks.update({name: checks[name] for name in order if name not in ordered_checks})
        return {
            "ok": ok,
            "ready": ok,
            "verdict": "ready" if ok else status,
            "status": status,
            "checks": ordered_checks,
            "missing": missing,
            "blocked": blocked,
            "unknown": unknown,
            "blocking_failures": blocking_failures,
            "next_command": command,
            "next_command_text": command_text,
            "next_plan": cli_plan if cli_plan is not None else next_plan,
            "plan": {
                "path": cli_plan,
                "source": plan_source,
                "sha256": plan_sha,
                "id": plan_obj.get("id") if isinstance(plan_obj, dict) else None,
                "stage": stage_info,
                "resources": resource_info,
                "execution_scope": execution_scope,
            },
            "execution_scope": execution_scope,
            "topic": topic_value,
            "owner": owner_value,
            "intents": intent_value,
            "argv": argv_value,
            "workspace": str(root),
            "db": str(db_path),
            "read_only": True,
            "writes": [],
            "as_of": _iso(now),
            "notes": list(gate_notes) + [
                "预检只读：不调用 probe / tick / watch / rearm / outcome，不写事件账、不建库",
                "硬闸复用 Workflow(check)：计划冻结 / 准备来源 / argv / J5 / 审计 / 监控 / 准入 / 真实源码",
                "overdue 只来自只读源；不可判一律不给命令（⛔ 不把未知记成没有超期）",
                "next_command 用绝对路径，可从任意 cwd 执行；⛔ 它不由本工具代跑",
                "阶段与资源只来自显式声明或计划元数据；⛔ 不按 exp 编号猜",
                "远端 scope、真实宿主唤醒与「负责人在一轮结束后自动覆盖」都未验：本命令只给本地预检与命令，"
                "不代表宿主会叫醒负责人、也不代表远端可停",
            ],
        }

    def next_round(self, *, next_plan, topic=None, owner=None, intents=None, argv=None,
                   user_stop=False, workspace=None, db=None, stage=None, resources=None,
                   overdue=_UNSET, assurance=_UNSET, require_provision=True,
                   monitor_check=_UNSET, authorization_check=_UNSET, verifier=_UNSET,
                   execution_target=None, remote_preflight=_UNSET,
                   allow_fixture_gates=False, assurance_host=_UNSET,
                   assurance_services=None, clock=None) -> dict:
        """跨轮下一轮：preflight + 出口/下一动作/停止的跨轮条件；一次只推进一轮。"""
        try:
            normalized_intents = _intent_declaration(intents, root=workspace or self.root)
        except ProgressError:
            normalized_intents = None
        normalized_argv = list(argv) if isinstance(argv, (list, tuple)) else None
        normalized_topic = topic.strip() if _text(topic) else None
        normalized_owner = owner.strip() if _text(owner) else None
        pre = self.preflight(
            next_plan=next_plan, topic=topic, owner=owner, intents=intents, argv=argv,
            workspace=workspace, db=db, stage=stage, resources=resources, overdue=overdue,
            assurance=assurance, require_provision=require_provision,
            monitor_check=monitor_check, authorization_check=authorization_check,
            verifier=verifier, execution_target=execution_target, clock=clock,
            allow_fixture_gates=allow_fixture_gates,
            assurance_host=assurance_host, assurance_services=assurance_services)
        round_state = self._round_state(topic=normalized_topic, owner=normalized_owner,
                                        intents=normalized_intents, argv=normalized_argv,
                                        plan=next_plan)
        if user_stop:
            round_state = {"ok": False, "state": "user_stopped", "needs_rearm": round_state.get("needs_rearm"),
                           "reason": "调用方声明用户已停止：不推进、不重挂、不给命令（用户停止优先）"}
            verdict, ok = "stopped", False
        elif round_state.get("state") == "safety_only":
            verdict, ok = "safety_only", False
        elif pre["ok"] and round_state.get("ok"):
            verdict, ok = "ready", True
        elif round_state.get("state") == "waiting":
            verdict, ok = "waiting", False
        else:
            verdict, ok = "blocked", False
        command = pre["next_command"] if ok else None
        rearm = self._rearm_argv() if ok and round_state.get("needs_rearm") else None
        steps = []
        if ok:
            if rearm:
                steps.append("先显式 rearm（一次唤醒只能有一个出口，A6）："
                             + " ".join(shlex.quote(part) for part in rearm))
            steps.append("再执行 next_command（真实起跑入口；⛔ 本工具不代跑、不自动续循环）")
        elif verdict == "safety_only":
            steps.append("安全收尾已记录：不开下一轮；需要继续时重新记录 outcome"
                         "（带显式 next 的 advance，或 nothing / problem）")
        elif verdict == "stopped":
            steps.append("用户已停止：不推进；恢复要新的用户明确指示")
        elif verdict == "waiting":
            steps.append("本轮出口是 nothing：等它的下次判定条件；⛔ 不要为了「有产出」硬开下一轮")
        elif round_state.get("state") == "outcome_missing":
            steps.append("先记录本轮出口（advance / nothing / problem），再谈下一轮")
        elif round_state.get("state") == "problem_open":
            steps.append("先处置未闭合问题（见 issue_id），再谈下一轮")
        elif round_state.get("state") == "legacy_next_unknown":
            steps.append("这次 advance 没有显式下一动作：重新记录一次带 next 的 advance，"
                         "或显式给出下一轮五件（next_plan/topic/owner/intents/argv）")
        elif round_state.get("state") == "next_mismatch":
            steps.append("已记录的下一动作与本次调用不一致：⛔ 不换计划 / argv / 负责人")
        else:
            steps.append("先修预检里的缺失 / 阻断 / 不可判项，再来要命令")
        return {
            "ok": ok,
            "verdict": verdict,
            "preflight": pre,
            "round": round_state,
            "next_command": command,
            "next_command_text": (" ".join(shlex.quote(part) for part in command)
                                  if command else None),
            "rearm_command": rearm,
            "rearm_command_text": (" ".join(shlex.quote(part) for part in rearm)
                                   if rearm else None),
            "steps": steps,
            "read_only": True,
            "writes": [],
            "workspace": pre["workspace"],
            "as_of": pre["as_of"],
            "notes": [
                "一次调用只推进一轮：不常驻循环、不自动 rearm、不自动起进程",
                "历史 outcome 没有显式 next ⇒ legacy_next_unknown，⛔ 不读成「该开下一轮」",
                "安全收尾（safe_action）只返回 safety_only：⛔ 不给新实验命令",
                "真实宿主唤醒（watch 结束后的通知）与自动覆盖未验：本命令只做只读判定",
            ],
        }

    def schedule(self, *, entries, workspace=None, db=None, user_stop=False, stage_order=None,
                 overdue=_UNSET, assurance=_UNSET, require_provision=True,
                 monitor_check=_UNSET, authorization_check=_UNSET, verifier=_UNSET,
                 execution_target=None, remote_preflight=_UNSET,
                 allow_fixture_gates=False, assurance_host=_UNSET,
                 assurance_services=None, clock=None) -> dict:
        """只读跨专题阶段排序：给有序候选与逐项命令，⛔ 不实发任务、不开机、不写盘。"""
        now = float(clock() if callable(clock) else (clock if clock is not None else self.clock()))
        root = Path(workspace or self.root).resolve()
        db_path = Path(db).resolve() if db else (root / ".autoresearch" / "runtime.sqlite3")
        order = list(stage_order or STAGE_ORDER)
        if len(order) != len(STAGES) or set(order) != set(STAGES):
            raise SpecError(f"stage_order 必须是 {list(STAGES)} 的排列：{order!r}")
        rank = {stage: index for index, stage in enumerate(order)}
        if not isinstance(entries, list) or not entries:
            raise SpecError("entries 必须是非空数组（每个候选一个显式下一轮）")
        normalized, seen = [], set()
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise SpecError(f"entries[{index}] 必须是对象")
            extra = set(entry) - SCHEDULE_ENTRY_KEYS
            if extra:
                raise SpecError(f"entries[{index}] 有不认识的键：{sorted(extra)}")
            topic = _need_name(entry.get("topic"), f"entries[{index}].topic")
            if topic in seen:
                raise SpecError(f"entries 里的 topic 重复：{topic}")
            seen.add(topic)
            normalized.append(dict(entry, topic=topic))

        # ---- 跨轮条件：用户停止优先；未出口的唤醒必须先收口 ----
        events = self._read()
        needs = self._needs_rearm(events)
        last_outcome = next((event for event in reversed(events) if event["kind"] == "outcome"), None)
        round_info = {
            "needs_rearm": needs,
            "outcome_kind": ((last_outcome or {}).get("payload") or {}).get("kind"),
            "outcome_locator": (last_outcome or {}).get("locator"),
        }
        gate_problems = []
        safety_only = False
        if needs["value"]:
            closed = any(event["kind"] == "outcome" and needs.get("seq") is not None
                         and event["seq"] > needs["seq"] for event in events)
            if not closed:
                gate_problems.append("上一次唤醒还没有记录出口：先 outcome 再排下一轮（A6 / H7）")
        if last_outcome is not None:
            payload = last_outcome.get("payload") or {}
            if payload.get("kind") == "advance" and payload.get("safe_action"):
                safety_only = True
                gate_problems.append("最近出口是安全收尾（safe_action="
                                     + str(payload.get("safe_action")) + "）：不排新实验")
            elif payload.get("kind") == "advance" and not isinstance(payload.get("next"), dict):
                round_info["legacy_next_unknown"] = True
                round_info["note"] = ("历史 advance 没有显式 next：本排序不据此推断任何计划；"
                                       "候选计划必须由调用方显式给出")
        round_info["problems"] = list(gate_problems)
        stopped = bool(user_stop)
        if stopped:
            verdict = "stopped"
        elif safety_only:
            verdict = "safety_only"
        elif gate_problems:
            verdict = "blocked"
        else:
            verdict = "ready"

        # ---- 只读运行时投影：在跑 / 未复盘轮次 + Runtime 资源租约 + 专题优先级 ----
        leases_only = _readonly_projection(db_path, [], now)
        running = _open_rounds(root, db_path, leases_only)
        plan_ids = [run.get("plan_id") for run in running.get("open") or [] if run.get("plan_id")]
        projection = _readonly_projection(db_path, plan_ids, now) if plan_ids else leases_only
        for run in running.get("open") or []:
            run["plan"] = (projection.get("plans") or {}).get(run.get("plan_id"))
        priorities, priority_why = _topic_priorities(db_path)
        leases = projection.get("leases") or []
        occupied = {item["resource_key"] for item in leases}
        large_runs, large_keys, large_unknown = [], set(), False
        for run in running.get("open") or []:
            stage_info = _stage_of(run.get("plan"))
            resource_info = _resources_of(run.get("plan"))
            run["stage_info"] = stage_info
            run["resources"] = resource_info
            if stage_info.get("known") and stage_info.get("stage") == "large":
                large_runs.append(run)
                if resource_info.get("known"):
                    large_keys |= set(resource_info.get("keys") or [])
                else:
                    large_unknown = True

        items = []
        if not (stopped or safety_only):
            for entry in normalized:
                pre = self.preflight(
                    next_plan=entry.get("next_plan"), topic=entry["topic"],
                    owner=entry.get("owner"), intents=entry.get("intents"),
                    argv=entry.get("argv"), workspace=root, db=str(db_path),
                    stage=entry.get("stage"), resources=entry.get("resources"),
                    overdue=overdue, assurance=assurance,
                    require_provision=require_provision, monitor_check=monitor_check,
                    authorization_check=authorization_check, verifier=verifier,
                    execution_target=execution_target, clock=clock,
                    allow_fixture_gates=allow_fixture_gates,
                    assurance_host=assurance_host,
                    assurance_services=assurance_services)
                stage_info = pre["plan"]["stage"]
                resource_info = pre["plan"]["resources"]
                state = "runnable"
                problems = list(gate_problems)
                if problems:
                    state = "blocked"
                if not pre["ok"]:
                    state = pre["status"] if pre["status"] in ("blocked", "unknown") else "blocked"
                    details = [f"{name}：{pre['checks'].get(name, {}).get('reason') or '未通过'}"
                               for name in pre["blocking_failures"]]
                    problems.append("预检未全过：" + "；".join(details))
                if not stage_info.get("known"):
                    state = "unknown" if state == "runnable" else state
                    problems.append("研究阶段未知：" + str(stage_info.get("why")))
                if not resource_info.get("known"):
                    state = "unknown" if state == "runnable" else state
                    problems.append("资源占用未知：" + str(resource_info.get("why")))
                for run in running.get("open") or []:
                    if run.get("topic") == entry["topic"]:
                        state = "blocked"
                        problems.append(
                            f"同专题还有未收口轮次 {run.get('run_id')}"
                            f"（finished={run.get('finished')}, reviewed={run.get('reviewed')}）："
                            "不能改正在跑的冻结包 / 题单 / 参数")
                zero_gpu = (resource_info.get("gpu_units") == 0) or (
                    resource_info.get("complete") and not resource_info.get("keys"))
                conflict = sorted(set(resource_info.get("keys") or []) & occupied)
                if conflict:
                    state = "blocked"
                    problems.append("与正在占用的资源冲突（同机器 / 同 GPU）：" + "、".join(conflict))
                unknown_lease_keys = sorted({item["resource_key"] for item in leases
                                             if item.get("state") == "unknown"})
                hit_unknown = sorted(set(resource_info.get("keys") or []) & set(unknown_lease_keys))
                if hit_unknown:
                    state = "unknown" if state == "runnable" else state
                    problems.append("资源租约状态不可判（过期 / 隔离）：" + "、".join(hit_unknown))
                if not running.get("checked"):
                    state = "unknown" if state == "runnable" else state
                    problems.append("在跑轮次不可判（" + str(running.get("why")) + "）：⛔ 不能判冲突")
                if not projection.get("checked"):
                    state = "unknown" if state == "runnable" else state
                    problems.append("资源租约不可判（" + str(projection.get("why"))
                                    + "）：⛔ 不能判冲突")
                if large_runs and resource_info.get("known"):
                    share = set(resource_info.get("keys") or []) & large_keys
                    if zero_gpu:
                        pass
                    elif share:
                        state = "blocked"
                        problems.append("大题单在跑且占用同一资源键：" + "、".join(sorted(share)))
                    elif large_unknown:
                        state = "unknown" if state == "runnable" else state
                        problems.append("大题单在跑且其资源占用不可判：⛔ 不能判并行安全")
                why = []
                if stage_info.get("known"):
                    why.append(f"阶段 {stage_info['stage']}（第 {rank[stage_info['stage']] + 1}/4）："
                               + STAGE_NOTES.get(stage_info["stage"], ""))
                why.append("同阶段次序：专题 priority=" + str(priorities.get(entry["topic"]))
                           + "，topic=" + entry["topic"])
                parallel_safe = False
                if state == "runnable" and large_runs:
                    if zero_gpu:
                        parallel_safe = True
                        why.append("零 GPU，且预算 / 审批 / 审计 / 监控全过：可与大题单并行")
                    elif set(resource_info.get("keys") or []).isdisjoint(large_keys):
                        parallel_safe = True
                        why.append("资源键不与正在跑的大题单相交：可并行")
                preflight_summary = {
                    "ok": pre["ok"], "status": pre["status"],
                    "blocking_failures": list(pre["blocking_failures"]),
                    "checks": {name: item["state"] for name, item in pre["checks"].items()},
                }
                items.append({
                    "topic": entry["topic"], "state": state,
                    "stage": stage_info, "resources": resource_info,
                    "plan": pre["plan"], "argv": pre["argv"], "owner": pre["owner"],
                    "intents": pre["intents"], "note": entry.get("note"),
                    "execution_scope": pre.get("execution_scope"),
                    "parallel_safe": parallel_safe, "zero_gpu": zero_gpu,
                    "next_command": pre["next_command"] if state == "runnable" else None,
                    "next_command_text": (pre["next_command_text"] if state == "runnable" else None),
                    "why": why, "problems": problems, "preflight": preflight_summary,
                })

        runnable = [item for item in items if item["state"] == "runnable"]
        runnable.sort(key=lambda item: (
            rank.get((item["stage"] or {}).get("stage"), len(STAGES)),
            priorities.get(item["topic"]) if isinstance(priorities.get(item["topic"]), int) else 2,
            item["topic"],
        ))
        # 候选之间也比资源：同 resource_key 的候选只能「依次执行」，⛔ 不同时列为可并行。
        for item in runnable:
            item["contention_with"] = []
            for other in runnable:
                if other is item:
                    continue
                share = sorted(set(item["resources"].get("keys") or [])
                               & set(other["resources"].get("keys") or []))
                if share:
                    item["contention_with"].append({"topic": other["topic"], "keys": share})
            if item["contention_with"]:
                item["parallel_safe"] = False
                item["why"].append(
                    "与同批候选 "
                    + "、".join(entry["topic"] for entry in item["contention_with"])
                    + " 争用同一 resource key：必须按本次次序依次执行，⛔ 不同时列为可并行")
        reasons = []
        for position, item in enumerate(runnable, 1):
            reasons.append(f"#{position} {item['topic']}："
                           + "；".join(item["why"]))
        blocked_items = [item for item in items if item["state"] == "blocked"]
        unknown_items = [item for item in items if item["state"] == "unknown"]
        notes = [
            "只排序并给显式命令：不实发任务、不开机、不写盘",
            "不改正在跑的冻结包 / 题单 / 参数：未结束或未复盘的轮次会阻断同专题新轮",
            "并行实验各自属于自己那一轮：结果不混入本轮",
            "阶段只来自显式声明或计划研究元数据；⛔ 不按 exp 编号猜阶段",
            "资源冲突按 Runtime 现有 resources 租约判定；缺表 / 过期 / 隔离一律不可判",
            "同 resource key 的候选只按本次次序依次执行，⛔ 不同时列为可并行",
            "远端 scope 未验：本轮只给本地命令；远端候选诚实记 unknown，不生成会必被本地入口拒绝的命令",
        ]
        if priority_why:
            notes.append(priority_why)
        if stopped:
            reasons = ["用户已停止：不排序、不给命令（用户停止优先）"]
        elif safety_only:
            reasons = ["最近出口是安全收尾（safe_action）：不排新实验、不给命令"]
        elif gate_problems:
            reasons = ["跨轮条件未过，候选全部不推荐：" + "；".join(gate_problems)]
        return {
            "ok": bool(runnable) and verdict == "ready",
            "verdict": verdict,
            "read_only": True,
            "writes": [],
            "as_of": _iso(now),
            "workspace": str(root),
            "db": str(db_path),
            "stage_order": order,
            "stage_notes": dict(STAGE_NOTES),
            "round": round_info,
            "running": [
                {"topic": run.get("topic"), "run_id": run.get("run_id"),
                 "plan_id": run.get("plan_id"), "finished": run.get("finished"),
                 "reviewed": run.get("reviewed"),
                 "stage": run.get("stage_info"), "resources": run.get("resources")}
                for run in running.get("open") or []
            ],
            "running_checked": bool(running.get("checked")),
            "leases": leases,
            "leases_checked": bool(projection.get("checked")),
            "recommended": runnable,
            "parallel_safe": [item for item in runnable if item["parallel_safe"]],
            "blocked": blocked_items,
            "unknown": unknown_items,
            "next_commands": [item["next_command"] for item in runnable
                              if item["next_command"]],
            "reasons": reasons,
            "notes": notes,
        }

    # -- 只读状态 -----------------------------------------------------------

    def status(self) -> dict:
        """只读：租约、最近观测、静默窗口、goal / overdue、needs_rearm 与阻塞项。"""
        events = self._read()
        lease = self.lease()
        goal_state = self._goal_state()
        overdue_state = self._overdue_state()
        needs = self._needs_rearm(events)
        quiet = self._quiet_window(events)
        last_activity = self._last_activity_ts(events)
        now = float(self.clock())
        observation = self._last_observation(events)
        failure_streak = self._failure_streak(events)
        last_decision = next((event for event in reversed(events)
                              if event["kind"] == "decision"), None)
        last_wake = next((event for event in reversed(events) if event["kind"] == "wake"), None)
        blockers = []
        if not observation:
            blockers.append("no_observation")
        if failure_streak:
            blockers.append("collect_failed")
        if goal_state["state"] == "unknown" or not goal_state["known"]:
            blockers.append("goal_coverage_partial" if goal_state["coverage"] == "partial"
                            else "goal_state_unknown")
        if needs["value"]:
            blockers.append("needs_rearm")
        if overdue_state.get("error"):
            blockers.append("overdue_unknown")
        elif overdue_state.get("overdue"):
            blockers.append("overdue_user_message")
        unverified = []
        if not overdue_state.get("checked"):
            unverified.append("overdue")
        ok = not blockers
        return {
            "ok": ok,
            "green": ok,
            "blockers": blockers,
            "unverified": unverified,
            "owner": self.owner,
            "holder": self.holder,
            "spec": {"name": self.spec["name"], "sha256": spec_fingerprint(self.spec),
                     "path": str(self.spec_path) if self.spec_path else None,
                     "round": self.spec["round"]},
            "db": str(self.db_path),
            "capture_root": str(self.capture_root),
            "events": len(events),
            "last_event": events[-1]["locator"] if events else None,
            "lease": lease,
            "needs_rearm": needs,
            "goal": goal_state,
            "supplement": {
                "allowed": bool(goal_state["known"] and goal_state["coverage"] == "none"),
                "why": ("goal 覆盖状态确切为空位 ⇒ 可补足" if goal_state["known"]
                        and goal_state["coverage"] == "none" else
                        "goal 覆盖状态不是确切空位 ⇒ 外部推进器不得推进"),
            },
            "overdue": overdue_state,
            "quiet": dict(quiet, age_sec=None if last_activity is None
                          else max(0.0, now - last_activity)),
            "progress": {
                "observation": {k: v for k, v in observation["payload"].items()
                                if k in ("run_id", "baseline", "values", "hashes", "spec_sha256")}
                if observation else None,
                "observation_locator": observation["locator"] if observation else None,
                "failure_streak": failure_streak,
                "unavailable": self._unavailable_since_success(events)["payload"]
                if self._unavailable_since_success(events) else None,
            },
            "last_decision": {k: v for k, v in last_decision["payload"].items()
                              if k in ("verdict", "reason", "need_human")} | {
                                  "locator": last_decision["locator"]}
            if last_decision else None,
            "last_wake": {k: v for k, v in last_wake["payload"].items()
                          if k in ("wake_id", "verdict", "reason", "intended_exit", "host_woken")} | {
                              "locator": last_wake["locator"]}
            if last_wake else None,
            "round": {
                "kind": self.spec["round"]["kind"],
                "basis": self.spec["round"]["basis"],
                "planning_required": self.spec["round"]["kind"] == "planned",
            },
            "watch_contract": WATCH_CONTRACT,
            "recovery": self._recovery(
                [{"kind": blocker} for blocker in blockers
                 if blocker not in ("no_observation", "needs_rearm")]),
        }

    def last_decision(self) -> dict | None:
        event = next((item for item in reversed(self._read()) if item["kind"] == "decision"), None)
        return event["payload"] | {"locator": event["locator"]} if event else None

    # -- 命令模板 -----------------------------------------------------------

    def watch_command(self) -> str:
        cli = Path(__file__).with_name("progress_cli.py")
        parts = [sys.executable, "-X", "utf8", "-B", str(cli),
                 "--root", str(self.root)]
        if self.spec_path:
            parts += ["--spec", str(self.spec_path)]
        parts += ["watch", "--owner", self.owner]
        return " ".join(shlex.quote(part) for part in parts)

    def rearm_command(self) -> str:
        cli = Path(__file__).with_name("progress_cli.py")
        parts = [sys.executable, "-X", "utf8", "-B", str(cli),
                 "--root", str(self.root)]
        if self.spec_path:
            parts += ["--spec", str(self.spec_path)]
        parts += ["rearm", "--owner", self.owner, "--note", "<本轮做了什么>"]
        return " ".join(shlex.quote(part) for part in parts)


__all__ = [
    "Progress", "ProgressError", "SpecError", "CollectError", "LeaseBusy", "FenceLost",
    "PendingRearm", "OutcomeBlocked", "load_spec", "validate_spec", "spec_fingerprint",
    "collect_round",
    "eval_events", "WATCH_CONTRACT", "EXIT_CODES", "SPEC_VERSION", "EVENT_KINDS",
    "GOAL_STATES", "GOAL_COVERAGE", "ROUND_KINDS", "OUTCOME_KINDS", "FAILURE_KINDS",
    "SAFE_ACTIONS", "STAGES", "STAGE_ORDER", "STAGE_NOTES", "CHECK_STATES", "ENTRY_STATES",
    "PREFLIGHT_CHECKS", "SCHEDULE_ENTRY_KEYS", "RESOURCE_KEYS",
    "LocalTransport", "SshTransport", "Capture",
]
