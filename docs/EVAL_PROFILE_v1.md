# 把 bench 接进 `research.py eval`

变更说明：首版。记一次实测：这条链路怎么接、内核会怎么拒绝、拒绝信息各是什么意思。

## 为什么需要这份文档

`research.py eval` 不是「跑个命令然后读输出」，它按 profile 执行并**校验结果契约**。
接错任何一环，它都**不会假装成功**，而是给出一个具体的 verdict：

| verdict | 含义 |
|---|---|
| `no_results` | 结果文件根本没写 |
| `results_invalid` | 文件存在但状态不在 `passed/failed/infra_error/timed_out` 里 |
| `nothing_scored` | 全是 `infra_error` —— **不是 0 分，是没测成** |
| `all_passed` / `some_failed` | 正常读数 |

## 三处必改（`profiles/agent-harness/profile.toml`）

仓库里这份 profile 的 `configured = false` 是**故意**的，有用例钉着
（`test_shipped_profile_cannot_run_by_default`）。要用就填这三处：

```toml
[evaluation]
command = ["python", "-X", "utf8", "-B", "tools/bench.py",
           "--tasks", "{tasks}", "--out", "{output}",
           "--patch-dir", "<放 <commit>.patch 的目录>",
           "--python", "<能跑 pytest 的解释器>"]
configured = true

[limits]
per_task_minutes = 5
max_tool_calls = 40
max_turns = 20
command_timeout_seconds = 300
total_budget_minutes = 60
```

只允许 `{tasks}` 与 `{output}` 两个占位符，**不经过 shell**。

## 题单格式（`--tasks`）

```json
{"task_id":"648ceda2","repo":"D:\\rich","commit":"648ceda2",
 "test_files":["tests/test_file_proxy.py"],
 "fail_to_pass":["tests/test_file_proxy.py::test_isatty"]}
```

缺 `commit`/`test_files`/`fail_to_pass` 任何一项，`bench` 直接拒绝执行（不猜）。

## 实测（2 道题，rich 仓库）

```
python research.py profile --path <临时副本>          → errors = []
python research.py eval --profile <临时副本> \
    --tasks tasks.jsonl --output out4 --timeout 600
  → exit=0  verdict=all_passed  pass_rate_scored=1.0  pass_rate_all=1.0
```

同一份配置**不给补丁**时：

```
verdict=nothing_scored
「没有一道题进入通过率分母（全部 infra_error / timed_out）：这不是 0 分，是没测成」
```

## 一个必须守住的映射

`bench` 写 `task_results.jsonl` 时，**非 `passed`/`failed` 一律映射成 `infra_error`**，
原始状态写进 `reason`（`没评：no_patch`）。

原因：内核只认四种状态；而「没有补丁」若被写成 `failed`，
通过率会看起来像**模型不行**，其实是**没评**。