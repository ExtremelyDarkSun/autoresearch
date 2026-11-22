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
| `selftest` | exit 0；输出里出现 `Ran 272 tests ... OK` 和 `Ran 1218 tests ... OK (skipped=11)` |
| `selftest --plugins` | exit 0；末行 `51 passed` |

合计 **1490 个 Python 测试 + 51 个 Node 插件测试**。

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

**判定口径**（`--task-cmd` 的退出码）：

| 情况 | 归入状态 |
|---|---|
| 退出码 0 | `passed` |
| 退出码非 0 | `failed` |
| 超过 `--timeout` | `timed_out`（**不进**通过率分母） |
| 程序找不到 / 启动失败 | `infra_error`（**不进**通过率分母） |

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
