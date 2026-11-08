<!--
GENERATED-BY: portable extraction / repository bootstrap (2026-11-09 +08:00)
LAST-EDIT: portable extraction / repository bootstrap (2026-11-09 +08:00)
PURPOSE: 源文件 → 目标文件的抽取映射，逐条说明抽了什么、哪些已实现、哪些必须由部署方提供数据、哪些刻意没做
ORIGIN: none（新文件）
CUMULATIVE-DELTA: 首版
-->

# 抽取映射：源项目 → 本仓库的 agent-harness 部分

**结论先说：这里抽取的是「被优化的对象是一个 agent harness」那一层接口与判据。
没有复刻任何官方评测环境。** 本仓库不含题库、参考补丁、隐藏测试、官方评分脚本、
指定模型权重或硬件布局 —— 所以它不能、也不声称能跑出一份「官方分数」。

分类依据：上游静态审计报告的「采用的三类边界」表 ——
agent 比赛配置 = **因为优化对象是 agent harness 才需要的**题单 / 逐题结果 / 轨迹 /
工具 / 工作区 / 泄漏 / 预算 schema、适配与分析方法；同时剥离当前比赛官方规则、
指定模型、真实题号、已测参数与结论。

---

## 1 逐文件映射

| 源（上游组件，按相对文件名） | 目标 | 抽了什么 | 状态 |
|---|---|---|---|
| `run_eval.py`（约 3.7k 行） | `kernel/autoresearch/profiles.py`（`build_command` / `evaluation_state` / `limit_for`）+ `profiles/agent-harness/profile.toml` 的 `[evaluation]`、`[limits]` | 评测入口的**参数面**：题单入口、结果落点、四条正交预算（每题分钟 / 工具次数 / 轮次 / 单命令超时）的名字与语义；`configured` 这类「没配就别跑」的开关 | **已实现**（argv 模板 + 预算名 + 默认不跑） |
| 同上 | `kernel/autoresearch/adapters/agent_harness.py`（`read_results`） | 逐题结果行的**字段面**：一题一行、`resolved` → 四态 `status`、耗时与原因字段的取法 | **已实现**（中性 schema，见 `schemas/results.schema.json`） |
| 同上 | 同文件（`iter_actions`） | 轨迹取数口径：`steps[].tool_calls[].function_name/arguments` + `steps[].observation.content` | **已实现**（同时兼容 `name`/`args` 别名） |
| 同上 | —— | vLLM 起服务、GPU 选卡与共享内存、沙箱降 uid、吞吐冒烟、许可证、环境变量前导、JUnit XML 取证钩子 | **刻意未做**：这些是「本场测量装置」，不是 harness 接口（见 §4） |
| `trace_judge.py` | `agent_harness.py`（`iter_actions` / `check_trace`） | 「按 trace 现算过程量」的取数口径：只取 `status=ok` 的观测、工具名优先于参数推断 | **已实现**（取数层） |
| 同上 | —— | J2（gold hunk 读取覆盖率）/ J3'（跑没跑仓库自带测试）/ J6（搜索锚点） | **未做**：三个判据都要 gold 补丁才能算 ⇒ 属部署数据（见 §3） |
| `gates/gate_tasklist.py` | `agent_harness.py`（`read_tasklist`） | **D 层**：题单里不许有重复题号（落点按题号命名 ⇒ 重复行互相覆盖）；外加「空题单」判错 | **已实现** |
| 同上 | —— | A 层（argv 里有没有 `--task-list`）/ B 层（日志里的总题数）/ C 层（实际题号集合） | **未做**：A/B/C 依赖本场 `run_eval` 的日志格式与 `sweep_results.jsonl` 文件名 ⇒ 属部署适配 |
| `gates/gate_leak.py` | `agent_harness.py`（`check_leak` / `check_patch_leak`） | ① **只判 agent 自己发出的动作**，不把工具返回里的路径当成动作；② 新增行**集合**比对 + **行数下限**（行数太少时占比没有意义）；③ 敏感片段由配置注入 | **已实现**（简化版） |
| 同上 | —— | 几十处语料的连片命中长度、豁免台账（`known_false_positives.jsonl`）、按观测内容判「读到没读到」、原生产物归因 | **未做**：那些是 285 KB 里的产能，靠的是本场语料与历史假阳性台账 |
| `gates/gate_out_of_workspace.py` | `agent_harness.py`（`check_workspace`） | 越界判据：`/wheels`、`site-packages`、`/usr/lib/python*`、conda 路径、沙箱外目录 —— 抽象成 `allowed_roots` + `forbidden_path_patterns` | **已实现**（规则可配，路径不写死） |
| 同上 | —— | 从 `results/logs/*.log` 逐题扫描 + ANSI 剥离 | **未做**：本仓库只读结构化 trace JSON，不解析日志文本 |
| `contracts/runtime.md` 等运行契约 | —— | —— | **不在本次范围**：由上层集成方抽取 |
| `runtime/execution.py` | —— | —— | **不在本次范围**：上层集成方负责（旧 agent 产物协议的可选兼容适配器 `execution_contract.py`） |

---

## 2 为什么不是整份复制 `run_eval.py`

整份文件里绝大部分与本 profile 无关：模型服务拉起、显存与共享内存判据、沙箱后端、
降权与权限修补、依赖探针、吞吐冒烟、JUnit 取证钩子、环境变量前导、许可证告警。
整份搬会同时搬进**本场模型名与机器布局**，而这两样正是要剥离的第三类内容。

已抽的三块（参数面、结果字段面、轨迹取数）加起来不到 400 行，且都不依赖
某个具体 harness 的库。判据：**换一个 harness，本仓库这三块不用改，只改 profile。**

---

## 3 确需部署方提供的数据（本仓库一律不带）

| 需要什么 | 谁提供 | 为什么不进仓库 |
|---|---|---|
| 真实题单（题号 + 题面 + 基线版本） | 部署方 | 属第三类内容；且题号会漂 |
| 参考补丁 / gold | 部署方 | 属答案材料，进仓库等于把评分答案公开 |
| 隐藏测试 / 官方评分脚本 | 部署方 | 同上 |
| 官方 harness 库或其 wheel | 部署方 | 版本与许可都属部署环境 |
| 指定模型与服务地址 | 部署方 | 属第三类内容 |
| 四条预算的**实际数值** | 部署方 | 本仓库不预填、也不设隐藏默认值 |
| 工具名清单（`[trace].tool_names`） | 部署方 | 不同 harness 的工具名不同 |
| 敏感片段（`[leak].sensitive_fragments`） | 部署方 | 片段本身就是答案材料 |
| 工作区根（`[workspace].allowed_roots`） | 部署方 | 取决于沙箱布局 |

示例数据（`examples/*`）**全部是合成的**：题号 `demo-00x`，仓库 `example/demo-repo`，
工作区 `/opt/agent/workspace`，敏感片段 `DEMO-SECRET-42`。它们只说明形状。

---

## 4 明确没有做的事（不许宣称）

* ⛔ **没有复刻官方评测环境**：没有官方题库、没有隐藏测试、没有官方评分脚本、
  没有指定模型权重、没有官方硬件布局。
* ⛔ **没有跑过任何真实评测**：本仓库的默认配置是 `configured=false` + 空命令，
  `build_command()` 会直接报错；测试与 CLI 只跑合成数据。
* ⛔ **没有把历史读数或历史结论带过来**：本仓库没有任何分数、通过率或「某个改动有效」的结论。
* ⛔ **没有官方规则**：`RULES.md` 写的是**读数口径**（什么算通过、什么算无效），
  不是哪一场比赛的规则。外部规则请部署方自己引用出处。
* ⛔ **检查不是证明**：`check_leak` / `check_workspace` 是字面量匹配与前缀比较，
  改名、换行、编码绕过的泄漏搜不出来。它们是**读数标注工具**。

---

## 5 怎么验证抽取是否真的可用

```bash
# 配置合法（退出码 0）
python profiles/agent-harness/checks/cli.py validate

# 单元测试（全部合成数据 / 临时目录；条数以实际输出为准，不写死在这里）
python -m unittest discover -s tests -p 'test_agent_profile.py' -v

# 中性 schema 与示例自洽
python profiles/agent-harness/checks/cli.py tasklist profiles/agent-harness/examples/tasklist.example.jsonl
python profiles/agent-harness/checks/cli.py results  profiles/agent-harness/examples/results.example.jsonl
python profiles/agent-harness/checks/cli.py trace    profiles/agent-harness/examples/trace.example.json \
    --allowed-root /opt/agent/workspace

# 默认不跑评测（退出码 2，打印挡住真评测的原因）
python profiles/agent-harness/checks/cli.py command --tasks t.jsonl --output o
```
