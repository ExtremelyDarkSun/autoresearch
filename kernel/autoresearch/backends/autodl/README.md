<!--
GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
PURPOSE: AutoDL 后端（kernel/autoresearch/backends/autodl/）的模块索引与边界说明
ORIGIN: tool/ 下三个平铺脚本 + topic_watchdog.py 的 availability-only 分支（拆成四个模块）
CUMULATIVE-DELTA: 首版。⛔ 不含任何本机路径、机器名、凭据值；运行态固定在工作区里；所有部署参数显式输入。
-->

# AutoDL 后端

| 模块 | 做什么 | 不做什么 |
|---|---|---|
| [`availability.py`](<availability.py>) | 只读采集 + 判读（`collect` / `classify` / `render_markdown` / `render_line`）；有界耗时；机名缓存 | ⛔ 不开机、不关机、不试开机；⛔ 不做每台一次的 detail 查询 |
| [`events.py`](<events.py>) | 变化判定（`verdict` / `idle`）· 冷却 · 首轮基线 · 持久队列 · 绑定名单 · 投递回执 · 过期清理 | ⛔ 不联网、不判机器 |
| [`worker.py`](<worker.py>) | 单次 worker：采集 → 判事件 → 落队列 → 发绑定 → 写快照；`--help` / `--dry-run` / `--selftest` | ⛔ 不驻留、不排下一轮、不投递会话消息、不调用云机操作 |
| [`cloud.py`](<cloud.py>) | 云机操作适配（开机/关机/重启） | ⛔ 默认 `PermissionError`；不回读状态，`status_confirmed` 恒 `False` |

## 三条不变量

1. **读取状态 ≠ 开机成功**：报告只说「看起来能开」，`certain` 恒 `False`（正在跑的除外）。
2. **凭据只按「环境变量名 / 显式文件」读**：⛔ 不内嵌值、不打印、不落盘、不进报告。
3. **运行态只在 `<workspace>/.autoresearch/`**：布局见 [`docs/autodl.md`](../../../../docs/autodl.md) §1。

## 跑法

```powershell
$python = (Get-Command python).Source   # 用部署机现有的 Python；需要无窗口时用同目录的 pythonw.exe
& $python kernel\autoresearch\backends\autodl\worker.py --help
& $python kernel\autoresearch\backends\autodl\worker.py --workspace <项目根> --dry-run --json
& $python -m unittest discover -s tests -p "test_autodl.py" -q   # 仓库根；标准库，无第三方依赖
```
