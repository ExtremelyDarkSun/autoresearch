# 15 分钟跑通：从 clone 到四条自检全绿

> 本文只讲**怎么装起来并验证**。装完之后能走多远、边界在哪，看 [REPRODUCIBILITY.md](REPRODUCIBILITY.md)。
> 所有命令都在**仓库根目录**执行（脚本靠 `__file__` 反推仓库根，进子目录会失效）。

---

## 0. 你需要什么

| 项 | 要求 | 怎么查 |
|---|---|---|
| Python | **≥ 3.11**（实测 3.12.6 可用） | `python --version` |
| Node.js | **≥ 20**（跑插件测试）；**≥ 24**（读 DSH 压缩会话日志要 zstd） | `node --version` |
| PowerShell | **推荐 7（pwsh）**；5.1 可用但见 §附-坑 2 | `$PSVersionTable.PSVersion` |
| 网络 | 能访问 `github.com`（见 §附-坑 1） | `git ls-remote https://github.com/ExtremelyDarkSun/autoresearch.git` |
| DSH | 可选。只有要用宿主联动/插件时才需要 | — |

**内核与通用工具只用标准库**，所以第 2 步的四条自检**一个依赖都不用装**。

---

## 1. clone

```powershell
git clone https://github.com/ExtremelyDarkSun/autoresearch.git
cd autoresearch
```

**验证**：`git log -1 --format=%H` 应输出 40 位提交号。

> ⚠️ **如果你的网络访问 GitHub 需要代理**，`git` **不会**自动读 Windows 系统代理（它走 libcurl）。
> 必须在同一个 shell 里显式设（见 §附-坑 1）。

### 1.1 先跑一条命令，看这条链在这里通不通

```powershell
python -X utf8 -B tools\smoke.py
```

它用合成数据（`python -c` 当评测命令）把整条链走一遍，七步，一秒左右：

```
[PASS] 1 生成 profile  实际：ok=True argv=12 段
[PASS] 2 全通过         实际：exit 0 / verdict=all_passed / pass_rate=1.0
[PASS] 3 有题未通过       实际：verdict=not_all_passed / alignment_error=None / pass_rate=0.5
[PASS] 4 覆盖不全        实际：verdict=misaligned / ok=False / pass_rate=None / raw=1.0
[PASS] 5 两次读数可比      实际：comparable=True / verdict=flat
[PASS] 6 超时参数边界      实际：全部拦住
[PASS] 7 仓库未被改动      实际：空
合计 7 步：7 PASS / 0 FAIL
```

**为什么先跑它**：单元测试全绿不等于「照文档做得到」。这条链上有五个真实接缝——生成
profile、把 argv 真的交给子进程、覆盖不全时读数变不可用、两次读数可比、参数边界——
单元测试逐点验过，但没有一条命令把它们串起来。`smoke.py` 就是那条命令。

任何一步 FAIL，先修它再看后面的文档：链条末端不对时，前面每一步都可能白做。

---

## 2. 四条自检（这一步不需要装任何东西）

先设两个环境变量，避免中文输出抛 `UnicodeEncodeError`：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
```

然后逐条跑：

```powershell
python -X utf8 -B research.py doctor
python -X utf8 -B research.py demo --output .local/demo
python -X utf8 -B research.py selftest
python -X utf8 -B research.py selftest --plugins
```

**通过的判据**：

| 命令 | 期望 |
|---|---|
| `doctor` | exit 0；JSON 里 `errors: []`；`evaluation.can_run: false`（**这是初始正常态**，不是故障） |
| `demo` | exit 0；`.local/demo/` 下生成 `REPORT.md` / `REPORT.json` / `runtime.autoresearch/` |
| `selftest` | exit 0；输出里出现 `Ran 272 tests ... OK` 和 `Ran 1376 tests ... OK (skipped=11)` |
| `selftest --plugins` | exit 0；末行 `51 passed` |

合计 **1648 个 Python 测试 + 51 个 Node 插件测试**。

> ⚠️ **只看关键字会误判**：`selftest` 的 stderr 里有一行
> `decisions_cli: error: argument --channel-config: not allowed with argument --channel`，
> 那是**参数互斥校验的负向测试**输出，紧接其后的汇总仍是 `OK (skipped=11)`。**不是失败。**

---

## 3. 建工作区

```powershell
python -X utf8 -B research.py init --workspace C:\my-research
```

**验证**：exit 0，输出 JSON 里 `state.empty: true`、`workspace_layout.ok: true`，并生成约 53 个目录。

这一步会建出固定分层结构：

```
<workspace>/
├─ .autoresearch/          运行库（runtime.sqlite3、layout-manifest.json）
├─ AutoDL实验/             实验线（框架/实验分析/实验记录/tool/archive）
├─ Kaggle平台/             平台线（config/tools/实验/文档/爬取过的资料）
├─ data/  install/  submission/  temp/  archive/
└─ AGENTS.md  RULES.md  INSTALL.md  PROJECT.md  ORGANIZATION_MAP.md ...
```

---

## 4. 建专题（含独立代码副本 + 独立 Python 环境）

```powershell
# 4.1 写一个需求文件（内容就是你的研究目标，工具会逐字保留）
'验证一个明确的研究问题。' | Out-File C:\my-req.md -Encoding utf8

# 4.2 建专题
python -X utf8 -B research.py workspace topic `
    --workspace C:\my-research `
    --topic demo-topic `
    --objective "验证一个明确的研究问题" `
    --requirements-file C:\my-req.md `
    --owner-session my-session

# 4.3 接手负责人（世代号；旧负责人继续写会被拒）
python -X utf8 -B research.py topics --db C:\my-research\.autoresearch\runtime.sqlite3 `
    --commit claim --topic demo-topic --session my-session --generation 0

# 4.4 查看
python -X utf8 -B research.py topics --db C:\my-research\.autoresearch\runtime.sqlite3 `
    show --topic demo-topic
```

**验证**：

| 项 | 期望 |
|---|---|
| `provision.json` | `status: provisioned`、`environment_verified: true` |
| 目录 | `AutoDL实验/实验分析/worktrees/demo-topic/`（独立空 Git 仓库）<br>`AutoDL实验/实验记录/environments/demo-topic/Scripts/python.exe` |
| 三份骨架 | `TOPIC.md` / `EVIDENCE_<日期>.md` / `STATUS.md`（都是 `skeleton: true`，**不算已交付报告**） |
| `topics show` | 专题 `status: active`，leader 世代号已绑定 |

> **加 `--no-environment` 可以跳过建 venv**（更快）。注意：加了之后**没有"只补建环境"的单独命令**，要环境就得不带这个参数重建专题。

---

## 5. 跑一次评测（用仓库自带的通用执行器）

仓库**不附带某场比赛的判分器**，但附带了一个**通用执行器** `tools/run_eval.py`：
它负责「按题跑命令、按退出码归类、写合规结果」，**怎么判分由你给的命令决定**。

```powershell
# 先自检（不需要任何真实题单）
python -X utf8 -B tools\run_eval.py --self-test

# 真要跑：每题调用你自己的评测器，用占位符传参
python -X utf8 -B tools\run_eval.py `
    --tasks tasks.jsonl `
    --out runs\run1 `
    --timeout 600 `
    --task-cmd '["python","-X","utf8","-B","my_evaluator.py","--task","{task_id}","--repo","{repo}","--out","{out}"]'

# 或者每题自带命令（题单每行写 "command": ["python","eval.py",...]）
python -X utf8 -B tools\run_eval.py --tasks tasks.jsonl --out runs\run1 --per-task-command
```

**产出**：`runs\run1\task_results.jsonl`（每行一个四态结果）+ `run_meta.json`（limits 与测量配置）。

> ⚠️ **`examples/tasklist.example.jsonl` 里没有 `command` 字段**，它是用来描述题单**形状**的，
> 不是能直接跑的数据。照抄它配 `--per-task-command` 会得到「一题都没跑」。想要一份真能跑的
> 题单长什么样，看 `tools/smoke.py`——它在临时目录里现造一份带 `command` 的，跑完就删。

### 5.1 不想手写 profile？让生成器把它配齐

上面是直接调执行器。要让 `research.py eval` 走 profile 那条链，profile 必须
`configured=true`、`command` 非空、四条 limits 齐、执行器存在——漏一项 `can_run` 就是 false。
`schema` 与 `RULES.md` 的路径还得对。手写容易漏，用生成器：

```powershell
# 生成一份可以直接跑的 profile（并当场自证 can_run=true，不通过就报错退出）
python -X utf8 -B tools\make_profile.py --out profiles\my-run

# 指定解释器 / 调整预算
python -X utf8 -B tools\make_profile.py --out profiles\my-run --python C:\venv\Scripts\python.exe
python -X utf8 -B tools\make_profile.py --out profiles\my-run --limits per_task_minutes=30

# 只看会写什么
python -X utf8 -B tools\make_profile.py --out profiles\my-run --dry-run
```

生成出来的 `command` 末尾带 `--timeout`，值由 `per_task_minutes × 60` 换算——
**预算不是注释，它真的会传给执行器**。改 `per_task_minutes` 后重跑生成器即可同步。

整批总预算走另一条路：profile 里写了 `total_budget_minutes` 时，`research.py eval`
不传 `--timeout` 就用它；**传了但超过它会被夹到声明值**，并在报告的 `budget` 里留痕：

```json
"budget": {"declared_total_minutes": 1, "requested_seconds": 9999,
           "effective_timeout_seconds": 60, "clamped": true,
           "why": "调用方给的 9999 秒超过 profile 声明的总预算 60 秒 ⇒ 夹到声明值"}
```

夹取是刻意的：预算是红线，不是建议。静默超支等于没写这条预算。
另外 `--timeout` 只收**有限正数**——`nan` / `inf` / `0` / 负数一律 exit 2，
不会一路走到子进程才炸成 `OverflowError`。

然后：

```powershell
python -X utf8 -B research.py eval --profile profiles\my-run --tasks tasks.jsonl --output runs\run1
```

`eval` 的输出里除了 `ok`，还有一个 `verdict`，用来区分「为什么不是全绿」：

| `verdict` | 含义 | 该怎么办 |
|---|---|---|
| `all_passed` | 跑完、覆盖齐、**每题都通过** | 正常 |
| `not_all_passed` | 跑完、覆盖齐，但有题没通过（或没测成） | **这是读数**，不是故障；看 `pass_rate` |
| `misaligned` | 结果覆盖与题单对不上（缺题/多题） | 读数不可用，先查执行器为什么吞题 |
| `results_invalid` | 结果文件写了，但读不了（状态值非法、`task_id` 类型错、JSON 坏行） | 查执行器的输出格式 |
| `no_results` | 结果文件根本没有 | 查 `result_file` 配置与执行器输出路径 |
| `nothing_scored` | **一题都没进分母**（全 `infra_error` / 全 `timed_out`） | 这不是 0 分，是没测成；查依赖与环境，别汇报通过率 |
| `timed_out` | 整次评测超时 | 调 `--timeout` |
| `launch_failed` | 评测进程起不来 | 看 `stderr_tail` |

每条报告还带一句 `verdict_reason`（例如「2/2 通过」「2/3 通过」「结果文件根本没有」），
不用回头翻 summary 才知道这句判决是什么意思。

**`verdict` 由读数推出来，不由进程退出码推。** 这两件事在真实执行器上会分叉：

- 3 题全 `failed`、执行器仍 `exit 0` ⇒ `not_all_passed`（早先这里错报 `all_passed`）
- 全部通过、执行器按自己的约定 `exit 1` ⇒ 仍是 `all_passed`

`not_all_passed` 与 `misaligned` 的区别是「这批题考了 30 分」与「成绩单少了几页」；
`results_invalid` 与 `no_results` 的区别是「格式不对」与「压根没写」。
两种混淆都会把人引到错误的下一步。

想自己验这套判定，跑对抗矩阵（九个场景，每个都真的执行一次 `eval`）：

```powershell
python -X utf8 -B tools\gate_probe.py
```

把 `not_all_passed` 当成 `misaligned` 是常见误读：前者说明「这批题考了 30 分」，
后者说明「成绩单少了几页」。前者可以汇报，后者不能。

**判定口径**（`--task-cmd` 的退出码）：

| 情况 | 归入状态 |
|---|---|
| 退出码 0 | `passed` |
| 退出码非 0 | `failed` |
| 超过 `--timeout` | `timed_out`（**不进**通过率分母） |
| 程序找不到 / 启动失败 | `infra_error`（**不进**通过率分母） |

**「跳过」这道题该怎么表达？** 四态是**测量口径**，不只是枚举：`passed`/`failed` 进分母，
`infra_error`/`timed_out` 明确不进。所以不要自己造 `skipped` —— 它不在契约里，写进去会让
整份结果变成 `results_invalid`（响亮但白跑一轮）。按原因归一化：

| 为什么没测 | 怎么写 |
|---|---|
| 环境缺失、依赖装不上 | `infra_error` + `reason`（不进分母） |
| 这题本来就不该在题单里 | 从题单里删掉（题单是输入，不是结果） |
| 模型拒答 / 没作答 | `failed` + `reason`（进分母） |

归一化放在**适配器 / 导入边界**，不要让第五种状态流进规范结果文件。真要长期支持
「跳过」这一态，得同时定义它在 `pass_rate`、`compare`、`alignment` 里的口径并补测试，
而不是只放宽校验器 —— 否则它最容易的用法就是悄悄缩小分母、把通过率抬高。

写完后可以用仓库自己的读数接口验证：

```powershell
python -X utf8 -B research.py results runs\run1\task_results.jsonl
```

**读数会被「题单覆盖」卡住**：结果文件只记「跑过的题」，不记「本该跑哪些题」。
所以执行器会把输入题单的题号快照写进 `run_meta.json` 的 `tasklist_task_ids`；
`read_results()` 读到这个字段后会比对两边：

| 情况 | `summary.pass_rate` | 其他字段 |
|---|---|---|
| 两侧题号完全一致 | 正常数值 | `aligned=true`、`pass_rate_raw` 与之一致 |
| 结果少题 / 多题 | **`null`**（不可用） | `aligned=false`、`alignment.missing_task_ids` 列出缺哪些、原值保留在 `pass_rate_raw` |
| 无 `run_meta.json` 或无该字段 | 正常数值 | 与旧行为**完全一致**，不再多出任何键 |

这条规则的由来：`read_results()` 手上只有结果文件，看不到题单，于是「题单 2 题、
结果只写回 1 题」会被算成 `pass_rate=1.0`——被吞掉的题在读数里彻底消失。这是比报错
更危险的失效，因为没人会去看。

`research.py eval` 还会在结果里同时给出顶层 `ok` 与 `alignment_error`，两者口径一致。

---

## 6. 到这里就到底了（重要）

第 4–5 步做完，你得到的是：**代码能跑、工作区建好、专题骨架就位、独立环境可用、评测能跑**。

再往下需要**四类真实输入**，它们都不在仓库里：

| 缺什么 | 为什么仓库没有 | 看哪 |
|---|---|---|
| 评测依赖 | `requirements-eval.txt` 是**空模板**，要自己填；用 `research.py deps --install` 装 | [REPRODUCIBILITY.md](REPRODUCIBILITY.md) §5 |
| 真实题单 | 属部署方数据，仓库只给形状（`examples/tasklist.example.jsonl` 全是合成示例） | 同上 |
| 数据与模型 | 仓库不提供 | 同上 |
| 凭据 | 文档不含，恢复包明确排除 | [CREDENTIALS.md](CREDENTIALS.md) |

**`doctor` 一直显示 `evaluation.can_run: false` 就是这个原因，不是装错了。**

### 6.1 这四样到底缺哪几样？跑一条命令

上表是分类，不是检查。要把「缺什么」变成可执行的结果，用**人件就绪门**：

```powershell
python -X utf8 -B tools\preflight.py `
    --tasklist D:\work\tasks.jsonl `
    --profile profiles\my-run `
    --data-dir D:\work\data `
    --kaggle $env:USERPROFILE\.kaggle\kaggle.json `
    --ssh-dir $env:USERPROFILE\.ssh `
    --autodl-file D:\work\secrets\autodl-token.txt `
    --authorization D:\work\HUMAN-EVAL-AUTHORIZATION.md
```

四项检查，每项给 ✓/✗ 加一句「下一步」：

| 项 | 查什么 |
|---|---|
| A 题单 | 存在、可解析、非空、**不是**随仓库分发的示例题单 |
| B 执行器 | profile 能读、`can_run=true`、命令里没有 stub/placeholder 字样 |
| C 数据与访问权 | 数据目录非空；Kaggle / AutoDL 文件存在且非空；SSH 有非空 `id_*` 私钥 |
| D 预算授权 | 授权文件里四个标签齐：预算上限 / 授权人 / 有效期 / 允许提交 |

**它只查形状**：不读凭据内容、不发网络请求、不放行任何评测。退出码 4/4 为 0，否则 2。

**0/4 是常态，4/4 也不等于放行。** 4/4 只说「材料齐了」——真开跑仍需人明确表态。
看到 ✗ 不要以为是程序坏了，那是它在如实说「还差人」。

---

## 附：六个必踩的坑

### 坑 1 · git 不读 Windows 系统代理

**症状**：浏览器能上 GitHub、`Invoke-WebRequest` 也 200，但 `git clone` 报 `Connection was reset` 或超时。

**原因**：git 走 libcurl，不继承 Windows 的 WinINET 系统代理设置。

**解法**（只影响当前 shell）：

```powershell
$env:HTTP_PROXY  = 'http://127.0.0.1:10808'   # 改成你自己的代理端口
$env:HTTPS_PROXY = 'http://127.0.0.1:10808'
git -c http.lowSpeedLimit=1 -c http.lowSpeedTime=15 clone <url>
```

### 坑 2 · PowerShell 5.1 会剥离双引号

**症状**：`budget grant --proof-json '{"session_id":"...","seq":16}'` 报
`proof不是合法 JSON：Expecting property name enclosed in double quotes`。

**原因**：Windows 自带 PowerShell 5.1 把传给原生程序的双引号剥掉了，Python 收到的是 `{session_id:...,seq:16}`（无引号）。PowerShell 7 没有这个问题。

**解法**：优先用 `pwsh` 7；或用文件的 `--event-file` 形式传 JSON，不要用 `--proof-json` 内联。

**自检**：

```powershell
python -c "import sys;print(sys.argv[1:])" '{"a":"b"}'
# 5.1 会输出 ['{a:b}']  ← 引号没了
# 7.x 会输出 ['{"a":"b"}']
```

### 坑 3 · .ps1 脚本必须带 UTF-8 BOM

**症状**：脚本里中文变乱码，报「字符串缺少终止符」。

**原因**：PowerShell 5.1 读**无 BOM** 的 UTF-8 文件时按 GBK 解析。

**解法**：存成 **UTF-8 with BOM**。

### 坑 4 · 中文路径会让部分工具解析失败

**症状**：Node 里 `new URL(import.meta.url).pathname` 得到 URL 编码串，`path.dirname` 结果错。

**解法**：路径硬编码，或用 `fileURLToPath()`。

### 坑 5 · 独立 venv 是 `--without-pip` 建的

**症状**：`Scripts` 下没有 `pip.exe`，`site-packages` 是空的，装不了任何包。

**原因**：`workspace.py:101` 用的是 `python -m venv --without-pip`。

**解法**（离线，不需要网络）：

```powershell
& "<venv>\Scripts\python.exe" -m ensurepip
& "<venv>\Scripts\python.exe" -m pip --version
```

### 坑 6 · 授权消息有 400 字符上限

**症状**：把授权 JSON 和几条命令发在同一条消息里，判据直接 `BLOCKED`。

**原因**：定位器只接受 `text_len ≤ 400` 的候选消息。147 字节的授权 JSON 一旦附加任何东西就超限。

**解法**：**授权那一行必须作为唯一正文单独发**，不要附带任何命令或说明。

---

## 一页速查

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONDONTWRITEBYTECODE='1'
python -X utf8 -B research.py doctor
python -X utf8 -B research.py demo --output .local/demo
python -X utf8 -B research.py selftest
python -X utf8 -B research.py selftest --plugins
python -X utf8 -B research.py init --workspace C:\my-research
```
