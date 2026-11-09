<!--
GENERATED-BY: portable extraction / repository bootstrap (2026-11-09 +08:00)
LAST-EDIT: portable extraction / repository bootstrap (2026-11-09 +08:00)
PURPOSE: agent-harness profile 的使用说明 —— 每个配置键管什么、怎么离线自查、接到评测前要满足什么
ORIGIN: none（新文件）
CUMULATIVE-DELTA: 首版。纠正执行入口说明，明确research.py profile只生成命令。增加首次启动prompt链接。
-->

# agent-harness profile

一句话：**被优化的对象是一个 agent harness 时，评测接口该长什么样。**
默认不带评测命令，因此装好之后**不会自己跑起来**，也不会去碰任何外部题库。

`RULES.md` 是必读（读数口径在那里）。本文件是用法说明书。

---

## 1 目录里有什么

| 路径 | 内容 |
|---|---|
| `profile.toml` | 配置本体：评测入口、预算名、轨迹 / 工作区 / 泄漏规则 |
| `RULES.md` | **必读**：四态口径、通过率分母、题单与测量配置纪律 |
| `schemas/tasklist.schema.json` | 题单形状（JSONL，一行一题） |
| `schemas/results.schema.json` | 结果形状（JSONL，一行一题） |
| `schemas/trace.schema.json` | 轨迹形状（JSON，一题一文件） |
| `examples/*` | **全部合成**的示例：题单 / 结果 / 轨迹 |
| `checks/cli.py` | 离线检查入口（校验配置、预览 argv、读结果、对比、查轨迹、比补丁） |
| `EXTRACTION.md` | 从上游抽取了什么、哪些还没做 |

⚠️ 三个 `*.schema.json` 与 `examples/*` **不带文件头**：JSON 不支持注释。
它们的来源与用途写在本文件与 `EXTRACTION.md` 里。

---

## 2 默认状态：不会跑

```toml
[evaluation]
command = []
configured = false
```

后果（都是设计出来的）：

* `kernel.autoresearch.profiles.build_command()` 直接报错，并说明缺什么。
* `checks/cli.py command` 退出码 `2`（未配置），打印挡住真评测的原因。
* `validate` 仍然**通过**：没配评测命令不是配置错误，是默认状态。

想让 harness 真的跑起来，要同时做三件事：

1. `[evaluation].command` 写成 argv 数组（只允许 `{tasks}` 与 `{output}`）；
2. `[evaluation].configured = true`；
3. `[limits]` 里给出 `per_task_minutes` · `max_tool_calls` · `max_turns` ·
   `command_timeout_seconds`。

三件缺一件，`build_command()` 都不放行。

---

## 3 配置键

### 3.1 顶层

| 键 | 值 | 管什么 |
|---|---|---|
| `id` | `"agent-harness"` | 固定值，内核按它认这个 profile |
| `version` | `1` | 固定值，格式版本 |
| `name` / `description` | 字符串 | 人读说明 |

### 3.2 `[evaluation]`

| 键 | 默认 | 管什么 |
|---|---|---|
| `command` | `[]` | 评测 argv 模板；**不经过 shell**；只认 `{tasks}` / `{output}` |
| `configured` | `false` | 与 `command` 必须一致：空命令必须配 `false`，非空命令必须配 `true` |
| `result_file` | `"task_results.jsonl"` | 结果文件名，相对 `--output` 目录 |
| `trace_dir` | `"traces"` | 轨迹目录名，相对 `--output` 目录 |
| `result_schema_version` | `1` | 会进测量配置指纹；改结果格式就加这个数 |

### 3.3 `[instructions]`

`required = ["RULES.md"]` —— 相对本 profile 目录的必读文件。
内核只给出路径（`required_instructions()` / `resolve_instruction()`），
**读取义务由上层集成方合并**，本模块不判断「读到没有」。

### 3.4 `[limits]`

只认五个通用预算名：`per_task_minutes` · `max_tool_calls` · `max_turns` ·
`command_timeout_seconds` · `total_budget_minutes`。

* 写别的键 ⇒ `validate` 报错（防止把某场比赛的私有字段名带进来）。
* 不预填任何数值，也没有隐藏默认值：`limit_for()` 未配置返回 `None`。
* 四条语义互不替代，见 `RULES.md` §4。

### 3.5 `[trace]`

| 键 | 管什么 |
|---|---|
| `tool_names` | 允许出现的工具名；出现白名单外的工具 ⇒ 判 `unknown_tool`。空 = 不校验 |
| `read_tools` / `edit_tools` / `command_tools` | 工具归类，留给分析用；空 = 不归类 |

### 3.6 `[workspace]`

| 键 | 管什么 |
|---|---|
| `allowed_roots` | 允许 agent 活动的根目录（绝对路径）。绝对路径不在其下 ⇒ 越界 |
| `forbidden_path_patterns` | 命中即越界：字面前缀，或 `re:` 开头的正则 |
| `ignore_paths` | 明确忽略的路径前缀（例如 harness 自己的临时目录） |

相对路径与 `~` 开头的路径**不判**（没法确定展开位置）。

### 3.7 `[leak]`

| 键 | 管什么 |
|---|---|
| `sensitive_fragments` | 敏感片段（字面子串）：参考补丁独有行、隐藏测试名、答案目录路径…… |
| `include_observations` | `false` 只查 agent 发出的动作；`true` 连工具返回一起查 |

### 3.8 `[schema]`

指向本目录里的 schema 与示例，`validate` 会查它们是否存在。

---

## 4 离线自查（不联网、不跑评测）

在仓库根执行：

```bash
# ① 配置本身合不合法
python profiles/agent-harness/checks/cli.py validate

# ② 评测 argv 长什么样（只打印，不执行）
python profiles/agent-harness/checks/cli.py command --tasks <题单> --output <目录>

# ③ 题单：重复题号 / 空题单都会被判错
python profiles/agent-harness/checks/cli.py tasklist profiles/agent-harness/examples/tasklist.example.jsonl

# ④ 结果：只给规范化 summary（四态计数 + 通过率 + 测量配置指纹）
python profiles/agent-harness/checks/cli.py results <task_results.jsonl> \
    --profile profiles/agent-harness

# ⑤ 对比：先查题集与测量配置，再给翻正 / 翻负 / 不确定
#    「通过率(配对)」= 两侧同一分母，报改善/退步以它为主
python profiles/agent-harness/checks/cli.py compare <before.jsonl> <after.jsonl> \
    --profile profiles/agent-harness

# ⑥ 轨迹：越界与泄漏（规则来自 profile，也可用 --allowed-root / --fragment 临时覆盖）
python profiles/agent-harness/checks/cli.py trace <trace.json> --profile profiles/agent-harness \
    --allowed-root /opt/agent/workspace

# ⑦ 补丁相似度：agent 的改动是不是参考补丁本身
python profiles/agent-harness/checks/cli.py patch <agent.patch> <reference.patch>
```

退出码统一：`0` 通过 · `1` 输入/参数错误 · `2` 未配置 · `3` 命中 · `4` 不可比。

示例数据是合成的，可以直接拿来试：

```bash
python profiles/agent-harness/checks/cli.py results profiles/agent-harness/examples/results.example.jsonl
python profiles/agent-harness/checks/cli.py trace profiles/agent-harness/examples/trace.example.json \
    --allowed-root /opt/agent/workspace
```

---

## 5 接到评测 / 接到上层集成方

内核接口（`kernel/autoresearch/profiles.py`）：

```python
from kernel.autoresearch.profiles import (
    load_profile, validate_profile, build_command, evaluation_state,
    required_instructions, resolve_instruction, limit_for,
)

profile = load_profile("profiles/agent-harness")        # dict，含 _profile_dir
errors = validate_profile(profile)                      # list[str]，空 = 通过
state = evaluation_state(profile)                       # can_run / blocking
argv = build_command(profile, tasks=..., output=...)     # list[str]，未配置就抛 ProfileError
```

结果与轨迹接口（`kernel/autoresearch/adapters/agent_harness.py`）：

```python
from kernel.autoresearch.adapters import agent_harness as ah

result = ah.read_results("run_1/task_results.jsonl", profile)   # {"tasks": [...], "summary": {...}}
diff = ah.compare_results("run_1/task_results.jsonl", "run_2/task_results.jsonl", profile=profile)
report = ah.check_trace(trace_dict, profile=profile)            # 越界 + 泄漏 + 工具名
```

本 CLI **不执行**评测命令。`research.py profile` 也只调用 `build_command()` 生成 argv，不启动子进程。部署方需要提供真实执行入口，或通过 [首次启动 prompt](../../docs/FIRST_RUN_PROMPT.md) 让执行 AI 补齐并测试接线。本 profile 只管 argv 生成与结果分析。

---

## 6 已知边界（不要当成能力）

* `examples/*` 是合成数据，**不是**任何题库的样本。
* 检查是**字面量**匹配：改名、换行、编码绕过的泄漏搜不出来。它是读数标注工具，
  不是完整性证明。
* 工作区判定是**字符串前缀**比较：不解析软链接、不访问磁盘。
* JSON Schema 文件是给人和其他工具看的契约；本仓库**不内置** JSON Schema 校验器
  （不引第三方依赖），测试只断言形状与必填字段。
* 没有复刻任何官方评测环境：没有官方题库、没有隐藏测试、没有官方评分脚本、
  没有指定模型权重。见 `EXTRACTION.md`。
