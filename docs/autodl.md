<!--
GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
PURPOSE: AutoDL 后端的机制正文：运行态布局 · 事件/绑定/回执 schema · 判定规则 · worker 用法 · 凭据口径 ·
        云机操作护栏 · 定时任务 · 与 DSH 事件插件的对接 · 边界与缺口
ORIGIN: tool/autodl_availability.py · autodl_events.py · topic_watchdog.py（availability-only 分支）·
        framework/AUTODL_EVENTS.md（重写为通用说明；机器名单、项目路径与事故记录全部剥离）
CUMULATIVE-DELTA: 首版。⛔ 不含任何本机绝对路径、机器名、会话 ID、历史读数；
  运行态统一到 <workspace>/.autoresearch/；所有部署参数显式输入；凭据只按「环境变量名 / 显式文件」读取。
-->

# AutoDL 后端：采集 · 事件 · 队列 · 回执

> 这一层**只读**：查一次机器列表、判一次「从开不了变成能开」，把结果落成队列与绑定名单。
> ⛔ 它不开机、不关机、不跑 GPU 任务；唯一的写动作是**本地文件**。
> ⛔ **读取机器状态不等于开机成功**：报告只说明「看起来能开」，`certain` 恒为 `False`（正在跑的除外）。

---

## 1. 运行态布局（固定，全部相对 `<workspace>`）

```
<workspace>/
└─ .autoresearch/
   ├─ availability.md              # 人可读的最近一次快照（由 worker 写）
   └─ events/
      ├─ pending/<id>.json         # 待投递队列：一条事件一个文件（插件投递后移走）
      ├─ delivered/                # 已投递（插件挪进来）
      ├─ expired/                  # 太旧被丢弃（插件或 worker 挪进来）
      ├─ ledger.jsonl              # append-only 台账，永不消费（复核「发过什么」读这份）
      ├─ state.json                # 上次快照 + 每台机器的上次发事件时间
      ├─ targets.json              # 该投给哪些会话（绑定名单）
      └─ delivered.jsonl           # 投递回执（插件写；也记 apply / waiting）
```

- 运行态**只在工作区里**（`<workspace>/.autoresearch/`），⛔ 不写到宿主目录、不写到系统临时目录。
- `<workspace>` 是**必填输入**：worker 用 `--workspace` 或环境变量 `AUTORESEARCH_WORKSPACE` 拿。
  没有默认值 —— 后端不猜项目根。

---

## 2. 三份文件格式

### 2.1 事件（`pending/<id>.json`）

```json
{
  "kind": "machine_available",
  "machine": "<机器名>",
  "triggers": ["verdict"],
  "from": { "verdict": "可能开不了", "idle": 0, "status": "已关机" },
  "to":   { "verdict": "可能可开", "idle": 2, "status": "已关机", "gpu": 8 },
  "checked_at": "2026-01-01T00:30:00+08:00",
  "at": "2026-01-01T00:31:05+08:00",
  "title": "<机器名> 现在看起来能开了",
  "text": "……（唯一一份正文实现，见 events.build_text）",
  "source": "实例列表接口（只读；没试开机）"
}
```

- `id` = `<时间戳>-<机名slug>-<triggers>`，文件名即 `<id>.json`。
- `at` 缺失或解析不出来时，投递侧按「太旧」处理（宁可丢一条，也不拿旧状态骗人）。
- `text` 是**唯一实现**：投递插件与 `--print` 都直接用这段文字，⛔ 各写一套措辞。
- 正文里不出现任何项目命令；「怎么复核」由投递侧的 `config.reviewHint` 追加。

### 2.2 绑定名单（`targets.json`）

```json
{ "sessions": ["<会话ID>", "…"], "authoritative": true, "source": "sqlite:topics", "at": "…" }
```

- `authoritative: true` 表示名单是**判出来的**（来自负责人绑定），不是手写的。
- 手写 `{ "sessions": ["…"] }` 也认（`authoritative` 缺省为 false）。
- 名单为空 ⇒ 投递侧按 `fallback` 决定：`hold`（默认，一条不发）或 `pick`（退回启发式挑 1 个）。

### 2.3 回执（`delivered.jsonl`）

一行一条 JSON，三种 `kind`：

| `kind` | 什么时候写 | 主要字段 |
|---|---|---|
| `apply` | 插件被宿主组合时写一次 | `pid` · `eventDir` · `pollMs` · `delivery` · `maxTargets` · `fallback` · `projects` |
| `delivered` | 真的投出去之后 | `machines` · `triggers` · `sessions` · `delivery` · `remaining` · `candidates` · `bound` · `picked` · `fellBack` |
| `waiting` | 有事件但没人收 | `pending` · `machines` · `reason`（`no-bound-session` / `bound-session-not-live`）· `fallback` |

回执是**三层验收**的第二、三层证据：`apply` = 宿主已加载，`delivered` = 目标会话真的用了。
⛔ `apply` 不能替代 `delivered`。

---

## 3. 判定规则（写死在 `events.py`，⛔ 下游不许自己发挥）

| 触发 | 条件 | 意思 |
|---|---|---|
| `verdict` | 上次**不是**「可能可开 / 已在用」，这次**是** | 开机可用性翻成可用了 |
| `idle` | 上次空闲卡数 `0`，这次 `≥ 1` | 服务器腾出卡了（无卡档机器只能靠这条被看见） |

六条硬规矩：

1. **只发「变成可用」**，⛔ 不发「变回不可用」。
2. **首次只建基线**：没有上次快照时一条都不发（否则第一次装上会把整张表当事件喷出来）。
3. **整次查询失败不动状态**：`ok=false` 时所有机器都会标「说不准」，拿它当现状就会伪造出一串假「恢复」。
4. **不在本次列表里的机器不判变化**：那种记录说的是「不知道」，不是「开不了」。
5. **冷却期（默认 20 分钟，按机器算）**：状态来回翻的时候不刷屏。
6. **默认看「本次报告里出现的全部机器」**：`watch` 可显式收窄；⛔ 没有内置机器名单。

---

## 4. worker（单次）

```powershell
$python = (Get-Command python).Source   # 用部署机现有的 Python；需要无窗口时用同目录的 pythonw.exe

& $python kernel\autoresearch\backends\autodl\worker.py --help

# 只看不写（不落事件、不动状态、不写绑定与快照）
& $python kernel\autoresearch\backends\autodl\worker.py --workspace <项目根> --dry-run --json

# 跑一次（写队列/状态/绑定/快照）
& $python kernel\autoresearch\backends\autodl\worker.py --workspace <项目根> --once --json `
    --token-env AUTODL_TOKEN --targets-db <.../topics.sqlite> --targets-table topics
```

| 参数 | 说明 |
|---|---|
| `--workspace` | **必填**（或 `AUTORESEARCH_WORKSPACE`）：运行态落点 |
| `--token-env` / `--token-file` | 凭据来源：环境变量**名** / 显式文件路径（见 §5） |
| `--api-url` | 实例列表接口（默认公开地址） |
| `--machines` / `--watch` | 只看哪些机器 / 事件只看哪些机器（空 = 全部） |
| `--no-idle-trigger` | 只按判词触发，不看显卡余量 |
| `--cooldown-minutes` / `--max-age-minutes` | 冷却期 / 过期阈值 |
| `--timeout` / `--deadline` | 单次请求超时 / 整次查询硬上限 |
| `--targets-db` / `--targets-table` | 负责人绑定所在 sqlite（只读）与表名 |
| `--dry-run` / `--json` / `--once` / `--selftest` | 只读模式 / 输出格式 / 单次（默认） / 离线自检 |

退出码：`0` = 查到了 · `2` = 说不清（没给 workspace / 凭据缺 / 查询失败）· `3` = 自检失败。

⚠️ **有硬截止**：`--deadline`（默认 20 秒）是整次查询的上限；就算 DNS 卡住，调用方也会按时拿到结果。
⚠️ **只发一次列表查询**：⛔ 不做「每台再来一次 detail 查询」—— 那只会把 1 次请求变成 N+1 次。
⚠️ **整次失败时仍逐台列机名**（用上次成功的清单，进程内 + 可选缓存文件），⛔ 不返回空表让人以为「没有机器」。

---

## 5. 凭据

- 只从两种来源读：**环境变量名**（`--token-env` / `AUTORESEARCH_*`，默认名 `AUTODL_TOKEN`）或**显式文件**（`--token-file`）。
- 优先级：显式文件 > 显式环境变量名 > 默认环境变量名。
- ⛔ **不内嵌值、不打印、不落盘、不进报告**：报告里只有 `credential_ref`（形如 `env:NAME` / `file:<路径>`）与 `credential_present`。
- 实例记录按**白名单**取字段：密码 / 令牌 / SSH 命令类字段即使接口给了也进不了报告；失败信息统一 `redact()`。
- 测试不读真实凭据：只设临时环境变量或临时文件。

---

## 6. 云机操作适配（默认拒绝，本次未调用）

`kernel/autoresearch/backends/autodl/cloud.py` 里有一个 `power_action()`，但：

1. **不传 `allow_control=True` 就抛 `PermissionError`**，一个字节都不发（这条有测试）；
2. 它只发请求、**不回读状态** ⇒ 返回体里 `status_confirmed` **恒为 `False`**；
3. 「开机成功」的唯一口径是回读实例状态（`availability.collect()` 的 `status ∈ {running, starting}`），
   不是 `power_on` 的返回码（历史上出现过「返回成功但状态没变」）。
4. ⛔ **本次抽取与测试没有真的调用它一次**：测试只断言护栏 + 用假传输层验证请求形态。

---

## 7. 定时任务

```powershell
$ps = 'kernel\hosts\dsh\tools\schtasks.ps1'
# 只看定义（默认动作，不注册）
& $ps -Action preview  -Workspace <项目根> -TaskName <名称>
# 落盘给人复核（不注册）
& $ps -Action generate -Workspace <项目根> -TaskName <名称> -OutFile <文件>
# 真正注册（必须显式确认）
& $ps -Action install  -Workspace <项目根> -TaskName <名称> -ConfirmRegister
# 查询 / 卸载
& $ps -Action status    -TaskName <名称>
& $ps -Action uninstall -TaskName <名称> -ConfirmUnregister
```

- Python 与 worker 入口按参数 / 环境变量 / 相对位置发现（⛔ 不内置本机绝对路径）；优先用 `pythonw.exe`（不弹控制台窗口）。
- 任务频率默认 1 分钟，`-IntervalMinutes` 可改。
- ⚠️ `schtasks /TR` 有长度上限；生成结果里会提示是否该改用 `.cmd` 包装脚本。
- ⚠️ 任务与宿主无关：**宿主关着时事件照旧落盘**，下次宿主起来补投；超过 `maxAgeMinutes` 的进 `expired/`。
  但**没有任何会话在跑时不会有投递**（会写 `waiting` 回执）。

---

## 8. 与 DSH 事件插件的对接

投递侧插件的配置要和这里的布局对齐（生成器会自动填）：

| 插件配置 | 值 |
|---|---|
| `eventDir` | `<workspace>/.autoresearch/events/pending` |
| `deliveredDir` | `<workspace>/.autoresearch/events/delivered` |
| `expiredDir` | `<workspace>/.autoresearch/events/expired` |
| `logFile` | `<workspace>/.autoresearch/events/delivered.jsonl` |
| `targetsFile` | `<workspace>/.autoresearch/events/targets.json` |
| `projects` | 项目根（**规范化路径前缀**，不是子串） |
| `fallback` | `hold`（默认） |
| `maxTargets` | `1` |

⇒ 后端负责「判 + 落」，插件负责「投 + 记」。⛔ 两边都不许各写一套判据或一套事件文案。

---

## 9. 自检与测试

```powershell
# 后端自检（离线；不联网、不读真凭据、不开机）
& $python kernel\autoresearch\backends\autodl\availability.py
& $python kernel\autoresearch\backends\autodl\events.py
& $python kernel\autoresearch\backends\autodl\worker.py --selftest

# 离线测试（标准库 unittest，无第三方依赖；含一个只监听 127.0.0.1 的假 HTTP 服务，不访问互联网）
& $python -m unittest discover -s tests -p "test_autodl.py" -q
```

测试覆盖：凭据来源与不泄露 · 正常/失败/超时/空列表/硬截止 · 判词与「关机 ≠ 不可用」· 机名缓存 ·
事件基线/触发/幂等/冷却/失败不动状态/名单外/watch · 绑定名单（含自定义表列）· 回执与过期 ·
worker 的 dry-run / 写盘 / 发布绑定 / 失败退出码 · 云机操作护栏。

---

## 10. 缺口与不承诺

1. **没有跑过真实接口**：本次所有采集都是假传输层或回环假服务；真实平台的字段增减未验。
2. **没有真的开过关机/重启**：`cloud.py` 只有护栏与请求形态有测试。
3. **没有端到端时延数据**：定时任务周期 + 插件轮询的合计延迟是**推断**，未计时。
4. **绑定来源假设一张 `topics` 表**：内核按「表名/列名可配」实现，但只测过一种 schema。
5. **事件去重只到「投完就挪走」**：宿主在「已投、还没挪走」之间崩掉时可能重发一次。
6. **「能开」永远是推断**：服务器存储水位看不到；报告与事件都不构成开机事实。
7. **Windows 计划任务的真实注册**：脚本已按参数生成命令，但本次**没有注册任何任务**；`/TR` 长度与权限模型要在目标机上验。
