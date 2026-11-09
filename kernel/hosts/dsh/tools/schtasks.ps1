# GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
# LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
# PURPOSE: Windows 定时任务的定义生成 / 预览 / 安装 / 卸载 / 查询脚本 —— 默认**只生成与预览**，不注册任务。
# ORIGIN: 原项目的计划任务（每分钟跑一次 availability-only）只以“已注册的实例”形式存在；
#          这里把「任务定义」抽成脚本，所有部署参数（任务名 / 工作区 / Python 路径 / 入口）显式输入
# CUMULATIVE-DELTA: 首版。四条护栏：
#   ① `-Action` 默认 `preview`；要真正注册必须显式写 `-Action install -ConfirmRegister`；
#   ② 生成/预览不调用 `schtasks /Create`；`generate` 只把命令写进 `-OutFile`（给人复核）；
#   ③ Python 与入口路径由参数或环境发现，⛔ 不内置本机绝对路径；
#   ④ 用 pythonw（无控制台窗口）跑，避免每分钟弹窗；退出码只反映「脚本本身」，不反映机器状态。
#   2026-11-09 命令里带**实际工作区参数**：`--workspace <解析后的工作区>` 与
#   `--targets-db <工作区>\.autoresearch\runtime.sqlite3`（worker 默认也用它）。定时任务每 tick 还会
#   按布局刷新每个已登记专题的 `.autoresearch\state\process_status\`，⛔ 不写 STATUS.md 研究正文；
#   本脚本仍不注册、不改宿主。
#
# 用法（在仓库根或任意目录）：
#   powershell -NoProfile -File kernel/hosts/dsh/tools/schtasks.ps1 -Action preview -Workspace <项目根>
#   powershell -NoProfile -File ... -Action generate -Workspace <项目根> -TaskName <名称> -OutFile <文件>
#   powershell -NoProfile -File ... -Action install  -Workspace <项目根> -TaskName <名称> -ConfirmRegister
#   powershell -NoProfile -File ... -Action uninstall -TaskName <名称> -ConfirmUnregister
#   powershell -NoProfile -File ... -Action status  -TaskName <名称>
#
# 退出码：0 = 动作完成 · 1 = 参数/环境不对（未执行）· 2 = schtasks 返回失败

[CmdletBinding()]
param(
    [ValidateSet('preview', 'generate', 'install', 'uninstall', 'status')]
    [string]$Action = 'preview',

    [string]$Workspace = $env:AUTORESEARCH_WORKSPACE,

    [string]$TaskName = 'AutoresearchAutoDLWorker',

    [int]$IntervalMinutes = 1,

    [string]$PythonPath = '',

    [string]$WorkerPath = '',

    [string]$OutFile = '',

    [switch]$ConfirmRegister,

    [switch]$ConfirmUnregister
)

$ErrorActionPreference = 'Stop'

function Fail([string]$Message) {
    Write-Host "FAIL: $Message"
    exit 1
}

function Resolve-Python {
    if ($PythonPath -and (Test-Path -LiteralPath $PythonPath)) { return (Resolve-Path -LiteralPath $PythonPath).Path }
    if ($PythonPath) { Fail "指定的 -PythonPath 不存在：$PythonPath" }

    $fromEnv = $env:AUTORESEARCH_PYTHON
    if ($fromEnv -and (Test-Path -LiteralPath $fromEnv)) { return (Resolve-Path -LiteralPath $fromEnv).Path }

    # 优先 pythonw（无控制台窗口）：同目录下若有 pythonw.exe 就用它。
    foreach ($candidate in @('pythonw.exe', 'python.exe')) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($found) {
            $exe = $found.Source
            if ($candidate -eq 'python.exe') {
                $sibling = Join-Path (Split-Path -Parent $exe) 'pythonw.exe'
                if (Test-Path -LiteralPath $sibling) { $exe = $sibling }
            }
            return $exe
        }
    }
    Fail '找不到 Python。请用 -PythonPath 指定 pythonw.exe/python.exe，或设置环境变量 AUTORESEARCH_PYTHON。'
}

function Resolve-Worker {
    if ($WorkerPath) {
        if (-not (Test-Path -LiteralPath $WorkerPath)) { Fail "指定的 -WorkerPath 不存在：$WorkerPath" }
        return (Resolve-Path -LiteralPath $WorkerPath).Path
    }
    $fromEnv = $env:AUTORESEARCH_WORKER
    if ($fromEnv -and (Test-Path -LiteralPath $fromEnv)) { return (Resolve-Path -LiteralPath $fromEnv).Path }

    # 本脚本位于 <repo>/kernel/hosts/dsh/tools/ ⇒ 回溯三级到仓库根
    $repo = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..\..')
    $candidate = Join-Path $repo 'kernel\autoresearch\backends\autodl\worker.py'
    if (Test-Path -LiteralPath $candidate) { return (Resolve-Path -LiteralPath $candidate).Path }
    Fail '找不到 worker.py。请用 -WorkerPath 指定，或设置环境变量 AUTORESEARCH_WORKER。'
}

if (-not $Workspace) { Fail '必须给 -Workspace（或在环境变量 AUTORESEARCH_WORKSPACE 里给），否则不知道运行态落在哪。' }
$workspaceFull = (Resolve-Path -LiteralPath $Workspace -ErrorAction SilentlyContinue)
if (-not $workspaceFull) { Fail "工作区不存在：$Workspace" }
$workspacePath = $workspaceFull.Path

$python = Resolve-Python
$worker = Resolve-Worker

if ($IntervalMinutes -lt 1 -or $IntervalMinutes -gt 1440) { Fail '-IntervalMinutes 必须在 1..1440 之间。' }
if ([string]::IsNullOrWhiteSpace($TaskName)) { Fail '-TaskName 不能为空。' }

$eventsRoot = Join-Path $workspacePath '.autoresearch\events'
$processStatusRoot = Join-Path $workspacePath '.autoresearch\state\process_status'
$runtimeDb = Join-Path $workspacePath '.autoresearch\runtime.sqlite3'
$layoutMarker = Join-Path $workspacePath '.autoresearch\workspace-layout.json'
$layoutNote = if (Test-Path -LiteralPath $layoutMarker) {
    '由 .autoresearch\workspace-layout.json 决定（本地中文目录布局）'
} else {
    '旧布局：topics/<专题>（无布局标记）'
}
$arguments = @(
    "`"$worker`"",
    '--workspace', "`"$workspacePath`"",
    '--targets-db', "`"$runtimeDb`"",
    '--once',
    '--json'
)
$taskCommand = '"' + $python + '" ' + ($arguments -join ' ')
$createLine = 'schtasks.exe /Create /TN "' + $TaskName + '" /SC MINUTE /MO ' + $IntervalMinutes + ' /TR "' + ($taskCommand -replace '"', '\"') + '" /F'
$deleteLine = 'schtasks.exe /Delete /TN "' + $TaskName + '" /F'
$queryLine = 'schtasks.exe /Query /TN "' + $TaskName + '" /V /FO LIST'

$definition = @"
# GENERATED-BY: schtasks.ps1（供人工复核；⛔ 本文件不会自动执行）
# 任务名：$TaskName
# 频率：每 $IntervalMinutes 分钟一次（单次 worker，不驻留）
# 工作区：$workspacePath
# 专题目录：$layoutNote
# 运行态：$eventsRoot
# 流程状态：$processStatusRoot\<专题>.json|.md（每 tick 刷新；⛔ 不写 STATUS.md 研究正文）
# 绑定库：$runtimeDb（读 topics.leader_session_id；读不出显式 unknown，已有 targets.json 不动）
# Python：$python
# 入口：$worker
# 说明：只读采集 + 判事件 + 落队列；⛔ 不开机、不关机、不投递会话消息。

# 手动跑一次（验证用）：
"$python" "$worker" --workspace "$workspacePath" --targets-db "$runtimeDb" --once --json

# 注册（需管理员或当前用户有权限）：
$createLine

# 查询：
$queryLine

# 卸载：
$deleteLine
"@

# schtasks 的 /TR 参数有长度上限（约 261 字符）。超了就把调用写进一个 .cmd 包装脚本，
# 让 /TR 只指那个包装脚本 —— 这是官方推荐做法，也更容易人工复核。
if ($createLine.Length -gt 240) {
    $definition = $definition + [Environment]::NewLine + '# ⚠️ 以上 /TR 太长（' + $createLine.Length + ' 字符），实际注册时建议写一个 .cmd 包装脚本，让 /TR 只指包装脚本。' + [Environment]::NewLine
}

switch ($Action) {
    'preview' {
        Write-Host '== 任务定义（预览，未执行任何注册命令）=='
        Write-Host $definition
        Write-Host ''
        Write-Host '要真正注册：-Action install -ConfirmRegister；要落盘给人复核：-Action generate -OutFile <文件>'
        exit 0
    }
    'generate' {
        if (-not $OutFile) { Fail 'generate 需要 -OutFile（把命令写到哪个文件给人工复核）。' }
        Set-Content -LiteralPath $OutFile -Value $definition -Encoding utf8
        Write-Host "已生成：$OutFile（未注册任何任务）"
        exit 0
    }
    'install' {
        if (-not $ConfirmRegister) {
            Fail '拒绝注册：install 必须显式加 -ConfirmRegister（默认只生成/预览）。'
        }
        Write-Host "即将注册：$TaskName（每 $IntervalMinutes 分钟）"
        Write-Host $createLine
        & schtasks.exe /Create /TN $TaskName /SC MINUTE /MO $IntervalMinutes /TR $taskCommand /F | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Host "FAIL: schtasks /Create 返回 $LASTEXITCODE"
            exit 2
        }
        Write-Host '已注册。要确认「真的跑了」请查运行态回执与应用日志，⛔ 不要只看任务状态。'
        exit 0
    }
    'uninstall' {
        if (-not $ConfirmUnregister) {
            Fail '拒绝删除：uninstall 必须显式加 -ConfirmUnregister。'
        }
        & schtasks.exe /Delete /TN $TaskName /F | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Host "FAIL: schtasks /Delete 返回 $LASTEXITCODE"
            exit 2
        }
        Write-Host "已删除任务：$TaskName"
        exit 0
    }
    'status' {
        & schtasks.exe /Query /TN $TaskName /V /FO LIST | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Host "FAIL: 查不到任务 $TaskName（可能未注册）"
            exit 2
        }
        exit 0
    }
}
