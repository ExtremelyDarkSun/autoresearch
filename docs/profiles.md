<!--
GENERATED-BY: portable extraction / repository bootstrap (2026-11-09 +08:00)
LAST-EDIT: portable extraction / repository bootstrap (2026-11-09 +08:00)
PURPOSE: profile 层与 agent-harness 适配器的接口契约 —— 给上层集成方的 CLI 用，也给后来的维护者改之前读
ORIGIN: none（新文件）
CUMULATIVE-DELTA: 首版
-->

# profile 与 agent-harness 适配器接口

读者有两类：**上层集成方的 CLI**（`research.py` → `kernel.autoresearch.cli`，接入方），
以及**以后要改这两个模块的人**。

一句话：`kernel/autoresearch/profiles.py` 管「配置合不合法、命令怎么拼」，
`kernel/autoresearch/adapters/agent_harness.py` 管「读数怎么算、两次读数能不能比」。
两者都**只碰本地文件**：import 时不联网、不读项目、不起子进程。

---

## 1 职责边界

| 谁 | 负责 | 不负责 |
|---|---|---|
| `kernel/autoresearch/profiles.py` | 读 TOML、校验结构、拼评测 argv、报告「现在能不能跑」 | 起进程、判读数、读写运行态 |
| `kernel/autoresearch/adapters/agent_harness.py` | 读题单 / 结果 / 轨迹，做规范化、对比、越界与泄漏检查 | 发起评测、决定题单内容、判断科学结论 |
| `profiles/agent-harness/**` | 配置本体、必读规则、schema、合成示例、离线 CLI | 任何真实题库 / gold / 模型 / 分数 |
| 上层集成方的 `research.py` + CLI | 调用本层、起子进程跑评测、合并读取义务、写运行态 | 重写读数口径 |

⚠️ 旧 agent 产物协议的兼容适配器（`AgentRunSpec` / `AgentExecution`）由上层集成方写在
`kernel/autoresearch/adapters/execution_contract.py`，**不在本层**。本层只认中性新协议。

---

## 2 配置接口（`kernel/autoresearch/profiles.py`）

```python
from kernel.autoresearch.profiles import (
    ProfileError,
    load_profile, validate_profile, build_command,
    evaluation_state, required_instructions, resolve_instruction, limit_for,
)
```

### 2.1 `load_profile(path) -> dict`

`path` 可以是 `profile.toml`，也可以是包含它的目录。

* 返回文件里的全部键，另加两个注入键：`_profile_path`、`_profile_dir`（绝对路径字符串）。
* **不校验**（校验是 `validate_profile` 的事）。
* 失败抛 `ProfileError`：文件不存在 / 不是合法 TOML / 解释器没有 `tomllib`（< Python 3.11）。

### 2.2 `validate_profile(profile) -> list[str]`

返回错误列表，空列表 = 通过。**不抛异常**，一次报全。

查什么：

| 区块 | 判据 |
|---|---|
| 顶层 | `id == "agent-harness"`；`version == 1`（整数，布尔不算） |
| `[evaluation]` | `command` 是字符串数组；元素非空；不含 `;` `&&` `\|\|` `\|` `>` `>>` `<` `2>&1` `&` 这类 shell 操作符；占位符只能是 `{tasks}` / `{output}` 且**两者都必须出现**；`configured` 与 `command` 是否一致（空命令必须 `false`，非空必须 `true`）；`result_file` / `trace_dir` 必须是相对路径且不含 `..`；`result_schema_version` ≥ 1 |
| `[instructions]` | `required` 非空、每项是相对路径、**文件存在**（profile 目录已知时） |
| `[limits]` | 键只能是五个通用预算名；值是正整数；`configured=true` 时四个必需预算必须全给 |
| `[schema]`（可选） | 每项是相对路径且文件存在 |
| `[trace]` / `[workspace]` / `[leak]`（可选） | 列出的键必须是字符串数组，`include_observations` 必须是布尔 |

不判错的东西：未知的**顶层**键（允许扩展）、`[trace]` / `[workspace]` / `[leak]` 里的未知键。

### 2.3 `build_command(profile, *, tasks: str, output: str) -> list[str]`

* 先跑 `validate_profile` + 一致性检查；不通过就抛 `ProfileError`，消息里带全部原因。
* `[evaluation].configured` 不是 `true`、或 `command` 为空 ⇒ 抛 `ProfileError`，
  消息写明「不能发起评测」以及缺哪几项配置。
* `tasks` / `output` 必须是非空字符串（含空格原样保留为**一个** argv 元素）。
* 只做 `str.replace("{tasks}", ...)` 与 `str.replace("{output}", ...)`，返回新的
  `list[str]`。**不拼字符串、不经过 shell、不调 `subprocess`。**
* `[limits]` 缺必需预算也走同一条拒绝路径（真评测不许靠隐藏默认值兜底）。

**调用方的用法**：`subprocess.run(argv, ...)`，⚠️ 不要 `shell=True`。

### 2.4 辅助

| 函数 | 用途 |
|---|---|
| `evaluation_state(profile) -> dict` | `configured` / `command` / `limits` / `can_run` / `blocking`（人话原因）/ `errors` |
| `required_instructions(profile) -> list[str]` | `[instructions].required` **原样**（相对 profile 目录） |
| `resolve_instruction(profile, name) -> Path \| None` | 解析成绝对路径（不判断存在性） |
| `limit_for(profile, key) -> int \| None` | 取预算；未配置返回 `None`；键不在白名单抛 `ProfileError` |

---

## 3 读数接口（`kernel/autoresearch/adapters/agent_harness.py`）

```python
from kernel.autoresearch.adapters import agent_harness as ah

result = ah.read_results(path, profile=None, strict=True, read_sidecar=True)
diff   = ah.compare_results(before, after, profile=None,
                            require_same_tasks=True, require_same_measurement=True)
tasks  = ah.read_tasklist(path, strict=True)
report = ah.check_trace(trace, profile=None, **覆盖)
```

### 3.1 `read_results(path, profile=None, *, strict=True, read_sidecar=True) -> dict`

返回：

```python
{
  "tasks": [
    {"task_id": "demo-001", "status": "passed", "passed": True, "scored": True,
     "duration_seconds": 42.5, "reason": "...", "line": 1, "raw": {...}},
  ],
  "summary": {
    "n_tasks", "n_passed", "n_failed", "n_infra_error", "n_timed_out",
    "n_scored", "n_unscored",
    "pass_rate",                 # passed / (passed + failed)；分母为 0 时 None
    "pass_rate_basis",           # 口径说明（字符串）
    "task_ids", "errors", "notes", "source", "measurement",
  },
}
```

* `status` 只允许 `passed` / `failed` / `infra_error` / `timed_out`。
  **`infra_error` 与 `timed_out` 永不计为通过，也不进 `pass_rate` 的分母。**
* `strict=True`（默认）遇到重复 `task_id`、缺 `task_id`、未定义状态、非 JSON 行都抛
  `ResultsError` 并给行号；`strict=False` 收集到 `summary["errors"]` 并跳过。
* `path` 是目录时按 `profile` 的 `result_file` 取（没有 profile 时取 `task_results.jsonl`）。
* `measurement` 是**测量配置指纹**：profile 的 `_profile_id` / `version` /
  `result_schema_version` / `limits`，再加结果文件旁 sidecar
  （`<结果名>.meta.json` 或 `run_meta.json`）里的 `limits` / `measurement`。

### 3.2 `compare_results(before, after, *, profile=None, require_same_tasks=True, require_same_measurement=True) -> dict`

* 两侧都可以是 `read_results()` 的返回值或文件路径。
* **先查可比性**：题集不同 / 测量配置不同 ⇒ `comparable=False`，
  `gained` 与 `lost` 为空，`verdict="incomparable"`，原因进 `reasons`。
* 翻正 / 翻负只统计两侧都 `scored` 的题；任一侧 `infra_error` / `timed_out`
  落进 `uncertain`（既不算翻正也不算翻负）。
* **两个通过率都要看**：`pass_rate.before/after/delta` 是各自分母（每侧自己的
  passed + failed），`pass_rate.paired` 是**两侧同一分母**（只数两侧都计分的题）。
  某题只有一侧计分时，「各自分母」的差里会混进「这题这次算不算计分」，
  配对口径把这个混入项拿掉 ⇒ **报改善/退步以配对口径为主**。
* 主要键：`verdict`（`improved` / `regressed` / `mixed` / `flat` / `no_data` /
  `incomparable`）· `verdict_zh` · `gained` · `lost` · `uncertain` ·
  `unchanged{passed,failed}` · `pass_rate{before,after,delta,paired}` · `task_set` ·
  `measurement` · `reasons` · `notes` · `sources`。

### 3.3 `read_tasklist(path, *, strict=True) -> dict`

题单 JSONL，一行一题，至少 `{"task_id": "..."}`。

* 重复 `task_id` ⇒ `ResultsError`（轨迹 / 补丁按题号命名，重复会让后一次覆盖前一次）。
* 空题单（0 行）⇒ `ResultsError`（通常是命令行漏参数，随后静默跑成默认全量）。
* 返回 `{"tasks": [{"task_id", "line", "raw"}], "summary": {"n_tasks", "task_ids", "duplicates", "errors", "source"}}`。

### 3.4 轨迹检查

| 函数 | 判什么 | 关键返回 |
|---|---|---|
| `load_trace(path)` | 读轨迹（`{"steps": [...]}` 或步骤数组） | dict |
| `iter_actions(trace)` | 展开成「一次工具调用 = 一个动作」 | `step` / `tool` / `arguments` / `text` / `paths` / `commands` / `observation` |
| `check_workspace(trace, *, allowed_roots, forbidden_path_patterns, ignore_paths)` | 绝对路径跑出根目录、或命中禁区 | `checked` / `verdict` / `violations` |
| `check_leak(trace, *, sensitive_fragments, include_observations, ignore_paths, reveal)` | 敏感片段（默认只查 agent 发出的动作） | `checked` / `verdict` / `violations` |
| `check_trace(trace, *, profile=None, ...)` | 上面两件 + 工具名白名单 | `tools` / `violations` / `verdict` / `checked` |
| `check_patch_leak(agent_patch, reference_patch, *, min_lines=30, ratio=0.5)` | agent 的改动是不是参考补丁本身 | `verdict`（`confirmed` / `suspected` / `not_formed`） |

**未配置 ≠ 干净**：规则全空时 `checked=False`、`verdict="not_configured"`、`clean=None`。
调用方不许把这种情况打印成「通过」。

配置优先级：**显式参数 > profile 的 `[trace]` / `[workspace]` / `[leak]` > 空**。

---

## 4 我在约定之外新增的字段（需要上层集成方知道）

上层集成方给的约定是：`id` / `version` / `[evaluation].command|configured|result_file` /
`[instructions].required` / `[limits]`。以下是我加的，都有默认值，不配也能用：

| 新增 | 位置 | 为什么加 |
|---|---|---|
| `trace_dir` | `[evaluation]` | 轨迹目录也要有个约定名，否则读轨迹得靠调用方猜 |
| `result_schema_version` | `[evaluation]` | 进测量配置指纹：结果格式变了，两次读数就不该直接比 |
| `[trace]`（`tool_names` / `read_tools` / `edit_tools` / `command_tools`） | profile | 轨迹检查要「配置工具名」，不能写死某一场的工具名 |
| `[workspace]`（`allowed_roots` / `forbidden_path_patterns` / `ignore_paths`） | profile | 越界判据要「配置路径」，不能写死本场的沙箱布局 |
| `[leak]`（`sensitive_fragments` / `include_observations`） | profile | 泄漏判据要「配置敏感片段」 |
| `[schema]`（6 个键） | profile | 让 profile 自己指向 schema 与示例，`validate` 顺手查存在性 |
| `_profile_path` / `_profile_dir` | `load_profile` 返回值 | 让下游能把相对路径解析成绝对路径（读取义务、schema 校验） |

`[limits]` 的第五个键 `total_budget_minutes`（整批总预算）也在我这边定义；
前四个是约定里的「时间 / 工具 / 轮次 / 单命令超时」。

---

## 5 异常与退出码

| 异常 | 何时 | 建议调用方动作 |
|---|---|---|
| `profiles.ProfileError` | 配置读不到 / 不合法 / 命令未配置 | 打印消息（已含原因），不要再起子进程 |
| `adapters.agent_harness.ResultsError` | 题单或结果非法、文件读不到、轨迹不是 JSON | 打印消息（含行号），不要拿半份数据出结论 |

离线 CLI（`profiles/agent-harness/checks/cli.py`）的退出码：

| 码 | 含义 |
|---|---|
| 0 | 通过（配置合法 / 数据可读 / 可比 / 轨迹干净） |
| 1 | 输入或参数错误 |
| 2 | 未配置（没有评测命令，或检查规则全空 ⇒ **什么都没查**） |
| 3 | 检查命中（越界 / 敏感片段 / 补丁与参考一致） |
| 4 | 两次读数不可比 |

---

## 6 接入清单（上层集成方三步）

1. **配置**：`profile = load_profile("profiles/agent-harness")` →
   `errors = validate_profile(profile)`；非空就打印并停（不要「先跑起来再说」）。
2. **读取义务**：`required_instructions(profile)` 拿相对路径，
   `resolve_instruction(profile, name)` 拿绝对路径，合并进你的必读清单。
3. **评测**：`evaluation_state(profile)["can_run"]` 为真才 `build_command(profile, tasks=..., output=...)`，
   然后 `subprocess.run(argv, shell=False)`；未配置就照 `blocking` 打印，直接返回「未配置」。

⛔ 三条不要做：

* 不要 `shell=True`，也不要把 argv 再 join 成字符串。
* 不要给 `[limits]` 补默认值 —— 那会让「没配预算的评测」看起来能跑。
* 不要用 `read_results` 的 `pass_rate` 当官方分数；它是**本地读数**，分母是「真正计分的题」。

---

## 7 已知边界

* 检查是字面量匹配 + 路径前缀比较：不解析软链接，不做语义等价，不做编码归一。
* JSON Schema 文件是契约文档；本仓库**不内置** JSON Schema 校验器（不引第三方依赖）。
* 只支持 Python ≥ 3.11（`tomllib`）。低版本会抛 `ProfileError` 说明原因，不会静默降级。
* 没有复刻官方评测环境，见 `profiles/agent-harness/EXTRACTION.md` §4。
