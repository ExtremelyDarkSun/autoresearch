# 一轮真实实验的最小闭环

> 变更说明：首版。这份文档描述「让模型做题 → 拿补丁 → 判分」这条链路怎么跑，
> 以及**在让任何模型做题之前**必须先做的那件事。

## 为什么要先自检判分器

整条链路里最容易被跳过的一环是**判分器本身对不对**。如果判分器会把红判成绿，
那么后面所有读数都只是把错误放大。

所以顺序固定为：

```
造夹具 → 先用三份已知答案把判分器钉住 → 再让模型做题
```

## 造夹具

```bash
python tools/make_loop_fixture.py --dest D:\loop-task
```

产出两个提交 + 三份「已知答案」：

| 提交 | 内容 |
|---|---|
| `c1` | `mod.py` 里 `double(x) = abs(x) * 2` —— 对负数错，`test_double_negative` 红 |
| `c2` | 改成 `x * 2`，并加一条回归用例 `test_double_small` |

| 已知答案 | 期望判分 |
|---|---|
| `--apply-gold` | `passed` |
| `good.patch`（只改 `mod.py`） | `passed` |
| `cheat.patch`（只把断言改成 `assert True`） | `infra_error`，理由「补丁修改了测试文件」 |

三条都符合预期，才说明判分器可用。

## 题面

```
仓库里 tests/test_double.py 有一个用例失败。请只修改 mod.py 让它通过，不要动测试文件。
```

题面刻意短：要测的是**模型能不能从失败测试里读出意图**，不是能不能读长文档。

## 判分

```bash
python -X utf8 -B tools/unfix.py run --repo <dest> --commit <c2> \
    --tests tests/test_double.py --out <dest>\score \
    --patch <候选补丁> --python <能跑 pytest 的解释器> --src-prefixes mod.py
```

## 已知边界

- 夹具只有一道题、一次尝试。它能证明**链路通**，不能证明**通过率**。
- 判分器现在会拒绝改测试 / 改 conftest / 改配置 / 全跳过 / 收集中断的补丁；
  这些都是实测出来的绕过路径，写在 `docs/UNFIX_TASKS.md`。

## 题目必须剥离未来（2026-11-24 补）

第一版闭环用的是**真仓库**：让模型在 `commit^` 上做题，判分对着 `commit`。

结果副机交回的补丁**逐字等于我自己某次提交** —— 连中文注释和实验编号都一样。
原因不复杂：**题就出在那个仓库里，答案写在 git 历史里**，`git log -S` 一翻就有。

所以出题改成两步，缺一不可：

```bash
# 1) 从真仓库造「看不到答案」的副本
python tools/make_task_bundle.py --repo <repo> --commit <sha> --dest <题目目录>

# 2) 判分仍在真仓库上做（模型看不到）
python -X utf8 -B tools/unfix.py run --repo <repo> --commit <sha> --patch <候选补丁> ...
```

`make_task_bundle.py` 的产出是 **单提交**仓库：`commit^` 的整棵树 + `commit` 自己的测试。
三道校验不通过就**拒绝出题**：

1. `git log --all --oneline` 只能 1 行；
2. `git status` 干净；
3. 副本里不含 `commit` 的源码改动（自然满足，但由「单提交」保证）。

### 这条和前面所有「防绕过」的关系

前十几轮防的是「补丁改判据」；这一条防的是**「模型直接看答案」**——
它不需要改任何东西，只要 `git log`。**判据再严也拦不住。**

## 复评一份已有补丁：`--only-commit`

`bench` 原来只能从 `scan` 的第一名往后取 `--limit` 道，**没法按指定 commit 评**。
想拿一份已存在的补丁复评，只能靠 `--limit` 碰运气碰上那个 commit。

现在：

```bash
python -X utf8 -B tools\bench.py --repo <repo> --python <py> \
    --src-prefixes "rich/" --only-commit 4ca56966 \
    --out <dir> --patch-dir <dir>
```

实测（rich 仓库）：

| | 耗时 | 扫了几道 |
|---|---|---|
| 旧：`--limit 9` 再靠运气 | 十几分钟 | 9 |
| 新：`--only-commit 4ca56966` | **4 秒** | 1 |

关键是**在验证之前过滤**，不是验证完再过滤 —— `scan` 也支持 `only_commits` 参数，
`bench` 把 `--only-commit` 直接传下去。

### 顺带一个坑

被测仓库的工作树若被上一次**中断**的 scan 留在改动状态（哪怕只是暂存区），
之后所有 run 都会以「工作树不干净」失败。实测踩到：`M rich/cells.py` 停在暂存区，
`--only-commit` 直接报不可用。清理后才 4 秒通过。

**中断运行的残留会伪装成「这道题坏了」。**

## 一条命令跑完一轮：`tools/study_round.py`

前面这些零件（造题、造副本、收补丁、判分、汇总）原来是分开跑的。分开跑的代价不是慢，
而是**每轮都要手工串五条命令，而且参数记在脑子里** —— 一旦某一步用了上次留下的目录，
读数就混进旧产物。这几周里发生过不止一次。

```bash
python tools/study_round.py --repo D:\rich --python C:\py\python.exe \
    --src-prefixes rich/ --limit 3 --out rounds/r1 \
    --solver-cmd "dsh --profile headless --prompt {prompt_file}"
```

五个阶段，产物都在 `--out` 下：

| 阶段 | 产物 |
|---|---|
| 1 造题 | `scan/candidates.jsonl` |
| 2 造副本 | `tasks/taskNN/`（单提交，**目录名不含 sha**） |
| 3 收补丁 | `patches/<commit>.patch`（或 `--patch-dir` 导入现成的） |
| 4 判分 | `bench/summary.json`、`bench/task_results.jsonl` |
| 5 汇总 | `round.json` |

### 三种输入，三种读数（都实测）

| 输入 | `patch_scored` | `pass_rate_scored` |
|---|---|---|
| 不给补丁 | 0 | **`None`**（没评，不是 0 分） |
| 已知 gold 补丁 | 2 | `1.0` |
| 假 solver（只加一行注释） | 2 | `0.0` |

`solver-cmd` 里的 `{task_dir}`、`{prompt_file}` 会被替换；题面模板只给
「哪个用例失败」和「不要改测试」，不给提交信息、不给 diff。

## 第一次多题闭卷轮次（n=2，读数 0.5）

题集来自 rich（已修好回归检查的扫描，10 道里可用 4 道）。我给每道题造了单提交副本，
只看失败的用例，自己写补丁：

| 题 | 目标用例 | 我的做法 | 判分 |
|---|---|---|---|
| `648ceda2` | `test_isatty` | 给 `FileProxy` 加 `isatty()` 委托 | **passed** |
| `d4f8620c` | `test_print_empty_with_end` | 把 `objects = (NewLine(),)` 换成 `Text(end)` | **failed** |

```
可用 2；负控红 2；正控绿 2；量具成立=True
评过 2，通过 1；pass_rate_scored=0.5 / pass_rate_all=0.5
```

**n=2 的 0.5 没有统计意义**，它的价值是：整条链（造题→副本→做题→补丁→判分）
第一次在**多于一道题**上跑通，且两题的结果不同 —— 说明判据有区分度，不是恒绿也不是恒红。

### 这一轮我自己踩到的坑

`git diff > x.patch`：PowerShell 的 `>` 默认写 **UTF-16LE**，`git apply` 只回
`No valid patches in input`。那句话把「编码不对」说成了「补丁是空的」，
于是这道题被记成 `infra_error` —— **分母悄悄少了一个**。

现在 `run` 会先看前两个字节：`ff fe` / `fe ff` 直接报「补丁是 UTF-16 编码……多半是
PowerShell 的 `>` 写的」。

## 单题预算：挂死一道题不该拖垮整轮

一轮评测里最贵的不是慢题，是**挂死的题**：三个相位各等满 `--timeout`（默认 180 秒）= 9 分钟，
而读完数还分不清「题挂死」和「补丁挂死」。

现在：

| 参数 | 含义 |
|---|---|
| `--timeout` | **单个相位**的上限（pytest 一次） |
| `--task-budget` | **单道题**的墙钟上限（默认 240 秒），后续相位的超时取剩余预算 |
| 负控挂住 | 直接记 `skipped=neg_hang`，**不再跑正控与判分** |

实测（rich 三题，其中 `8ff0fb1f` 的负控挂死）：

```
[2/3] (3/3) 8ff0fb1f 负控 / 正控
        负控挂住：跳过正控与判分
```

整轮 311 秒结束，而不是 3 × 180 秒干等。

### 顺带：把「不干净」说清楚

`工作树不干净（跟踪文件有改动）` → 现在会列出**是哪些文件**：

```
工作树不干净（跟踪文件有改动）：rich/cells.py。
先 `git restore --staged --worktree .` 或 `git checkout -- .` 再跑
```

原因很具体：上一轮被打断的 bench 把 `rich/cells.py` 留在暂存区，之后**每一道题**
都被判「工作树不干净」—— 看起来像三道题一起坏了。**中断残留会伪装成「题坏了」。**

## 一道题的异常不许炸整轮

题单里写一个不存在的 commit，`changed_files` 会抛 `SystemExit`。改动前：**整轮直接退出**，
已经跑完的读数全丢 —— 这是最贵的一种失败（跑了半小时，什么都没留下）。

现在单题被 `try` 包住：异常记成 `skipped=error` + 异常原文，继续下一道。

实测（题单 2 道，第 1 道 commit 不存在）：

```
[2/3] (1/2) deadbeef 负控 / 正控
        单题异常，记 infra_error 继续：SystemExit: 读不到提交 deadbeef：…
[2/3] (2/2) <真 sha> 负控 / 正控
[3/3] 汇总  →  exit 0，两道题都在记录里
```

### 顺带：汇总里把「负控挂住」单独计数

`summary["skipped_neg_hang"]` + `tasks_attempted`。否则读者看到「跑了 3 道、可用 2 道」
会去猜第 3 道怎么了 —— **报告里不该有需要读者猜的缺口。**

## 题单必须自带 `repo` 与前缀

`scan` 产出的 `tasks_unfix.jsonl` 原来只有 `commit / test_files / fail_to_pass` 等字段。
把它直接喂给下游会连撞两堵墙：

1. `bench --tasks` 不知道在哪个仓库评（题单里没 `repo`）；
2. 用自定义前缀（例如 `--src-prefixes mod.py`）扫出来的题，复评时前缀退回默认值，
   **全部判 `infra_error`** ——「该提交里没有文件匹配源码前缀」。

也就是说「scan 产题单 → 复评」是**半条链**，必须手改文件才能通。

现在题单每行都带 `repo`、`src_prefixes`、`test_prefixes`；`bench` 用题单里的值，
命令行参数只作兜底。实测（夹具仓库，命令里不给 `--repo` 也不给前缀）：

```
[3/3] 汇总
  可用题 1；负控红 1；正控绿 1；量具成立=True
```

### 同一类错误的第 N 次

本周已经四次「产物缺了消费者要的东西」：

| 缺什么 | 现象 |
|---|---|
| `bench` 不透传 `fail_to_pass` | CLI 判 passed、benchmark 判 failed |
| `bench` 不把 `only_commits` 传给 `scan` | 复评要多花十几分钟 |
| `scan` 不传 `budget_seconds` | `--budget-seconds 0` 仍然跑满 |
| 题单不带 `repo` / 前缀 | 复评全判 `infra_error` |

四次都不是算法错，都是**接口没接上**。

## 退出码 1 但不是工具的错

上一轮我把「bench 作业退出码 1、产物却齐全」记成没查清的异常。查清了：

PowerShell 里写 `python ... 2>&1 | Select-Object -Last 20`，
会把 bench 写到 **stderr** 的诊断（「负控挂住」「预算用尽」那些）变成 **NativeCommandError 记录**，
于是**脚本/作业的退出码变成 1，而 bench 本身是 exit 0**。

同一份题单、同样参数，改成 `*> 文件` 抓输出：

```
$LASTEXITCODE = 0
```

所以：**这不是 bench 的 bug，是抓输出的方式造成的误判。**

两件事值得记：

1. 诊断写 stderr 是对的（下游能分流），但**它会让粗心的管道读到假失败**。
   现在 `bench.py` 的模块说明里写了这条约定与正确抓法。
2. 我上一轮的处理其实是对的 —— 没把它记成「完成」，而是记成「存疑」。
   如果当时顺手写成「已跑通、exit 0」，这条就永远查不出来。

## `round.json` 与 `bench/summary.json` 会互相打架

实测（副机 L117 现场）：先跑一次 `study_round` 写了 `round.json`，之后又**单独**跑了一次
`bench` 写进同一个 `bench/` 目录。结果：

| 文件 | `patch_scored` | `instrument_ok` |
|---|---|---|
| `round.json`（旧）| 0 | false |
| `bench/summary.json`（新）| 1 | true |

**两个文件都没有写「我是什么时候、基于哪份汇总写的」。** 读的人只能猜谁新。

现在 `round.json` 里带两样东西：

```json
"generated_at": "2026-11-24T09:00:56+08:00",
"bench_summary_sha256": "1f3c…"     // summary.json 的前 16 位
```

重跑 `bench` 之后，把 `summary.json` 的哈希和 `round.json` 里那个比一下就知道谁过期了。

> 同一个实验留下两个读数文件、并且都不标新旧，比只留一个错读数更坏 ——
> 因为读的人会挑那个好看的。