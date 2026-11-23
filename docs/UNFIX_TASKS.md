# 从仓库历史反解可判定任务

> 适用场景：你有仓库、有测试，但**没有判分清单**（不知道哪几条测试该从红变绿）。
> 这个工具从 git 历史里把「修 bug 的那次提交」反解成一道自带真值的题。
> 入口：`tools/unfix.py`；判据测试：`tests/test_unfix.py`。

---

## 1. 它解决什么问题

跑评测需要三样东西：题、能跑的测试、**判据**。前两样仓库里通常有，第三样常常缺。

缺判据时，最容易的做法是「跑一遍仓库自带测试，全绿就算对」——这是假绿：仓库本来全绿，
跑绿了说明不了任何事。**没有一条从红变绿的测试，就没有测量。**

反解的思路：一条修 bug 的提交，同时改了源码和测试。把**源码退回修复前、测试留在修复后**，
那条新增的测试就会红。这个红是自动得到的 fail-to-pass，不需要任何人手工标注。

```
第 1 步（红）：源码退到 <commit>^，测试保持 HEAD  → 期望失败，失败用例 = fail-to-pass
第 2 步（绿）：源码恢复 HEAD                      → 期望全过
```

**两步都成立，这道题才成立。** 任何一步不成立，这道题不许进题集 —— 否则就是拿一把没校准的尺子量东西。

---

## 2. 判据

判定被抽成一个纯函数 `classify(red_exit, green_exit, red_ids, collection_broken)`：

| 红 | 绿 | 结论 | 为什么 |
|---|---|---|---|
| `1`（有用例 id） | `0` | ✅ 可用 | 标准情形 |
| `1`（无 id） | `0` | ❌ | 红了但说不出哪条红 |
| **`124`（挂住）** | `0` | ✅ **可用** | 这个 bug 的表现就是死循环；判成不可用会扔掉最像真 bug 的题 |
| `124` | `124` | ❌ | 两边都跑不完，测不出差别 |
| `2`（收集中断） | `0` | ❌ | 测试根本没跑起来，不是「测出失败」 |
| `0` | `0` | ❌ | 退回源码仍全过，说明测试没覆盖这处改动 |
| 任意 | `≠0` | ❌ | 环境本身有问题 |

`124` 是本工具自己的超时码，**不是 pytest 的码**。pytest 的 `2` 是「被中断 / 收集错误」。

---

## 3. 为什么判据要写成纯函数并配测试

判据分两层测，缺一不可：

| 层次 | 测什么 | 抓得住 | 抓不住 |
|---|---|---|---|
| 纯函数级 | `classify` / `regression_verdict` / `collection_broken_from` / `patch_touches_tests` | 边界值组合 | import 期的名字解析错误 |
| 集成级 | 起临时 git 仓库、走真 CLI（`TestFalseGreens`） | `scan` 的 `NameError` 这类整条路径崩 | 单个函数的边界 |

**两层都是必需的**，有实例：`4ef9340` 把 `TEST_PREFIXES` 改名时漏了 `scan()` 里一行，
纯函数级用例全绿（没有一条调用 `scan()`），是集成级用例才抓到的。

集成级那组（`TestFalseGreens`）由副机编写并附逐条 `git apply -R` 反证；本地用独立变异复核过
（例如只保留 `+++ ` 行头、不再看 `--- `，`test_deleted_test_file_is_detected` 立刻变红）。

做这套实验时，判据**连续错三次**，每次都是把两件不同的事装进同一个桶：

1. **pytest 的 `2` 与 `1` 混在「非零」里** —— 一次埋掉 22 个候选；
2. **`failed` 与「进程被杀」混在 `reason` 字段里** —— 读的人分不清「3 条测试挂了」和「装包失败」；
3. **「超时」混着「两边都跑不完」（不能用）与「红挂住绿通过」（好题）** —— 扔掉 9 道。

三次都发生在判据里。所以现在它们以 `tests/test_unfix.py` 的用例形式存在，而不是靠再读一遍代码。
`python -X utf8 -B tools/unfix.py --selftest` 就跑这组用例。

---

## 4. 用法

```powershell
# 扫全历史，产出题集（每道题都过了红→绿两关）
python -X utf8 -B tools/unfix.py scan --repo <解压后的仓库> --limit 40

# 跑一道题：负控（不打补丁，应 failed）
python -X utf8 -B tools/unfix.py run --repo <dir> --commit <sha> --tests tests/x.py --out <dir>

# 正控（打 HEAD 的源码，应 passed）
python -X utf8 -B tools/unfix.py run --repo <dir> --commit <sha> --tests tests/x.py --out <dir> --apply-gold

# 评一份补丁（这是接入 agent 产出的入口）
python -X utf8 -B tools/unfix.py run --repo <dir> --commit <sha> --tests tests/x.py --out <dir> --patch my.patch
```

`scan` 需要仓库已经能跑测试（依赖装好）。本仓库的 `tools/swe_local_harness.py`（在
`kaggle_competition` 工作区里）负责把比赛快照变成「能跑测试的仓库」。

---

## 5. 实测得到的两个数字

| 仓库 | 候选（源码+测试同改） | 验证 | 可用 | 可用率 |
|---|---|---|---|---|
| fastapi（6016 提交） | 241 | 241 | 16 | 6.6% |
| rich（4449 提交） | 519 | 40 | 27 | 67.5% |

**同一套判据，两个仓库差十倍。** 结论不是一个数字，而是一条纪律：
**在一批代码上成立的规律，别当成通用规律。**

fastapi 低的原因清楚：新功能提交会在测试里用它自己新增的 API，源码退回后测试连 import 都过不去
（收集错误）。rich 的提交更偏「行为修正」，API 不变，所以能反解。

---

## 6. 回归闸：题目过了不等于没把库弄坏

`--tests` 只跑那道题相关的文件。**这正是本工具的一个假绿入口** ——
一个补丁完全可能「修好这道题、弄坏别处」，而默认判定照样给 `passed`。

实测（同一个提交、同一道题）：

| 补丁内容 | 不带回归检查 | 带回归检查 |
|---|---|---|
| 只修 `file_proxy.py` 的 `isatty` | `passed` | `passed`（新增 0 条） |
| 修好同一处 **+ 悄悄改坏 `set_cell_size`** | **`passed`（假绿）** | `failed`：`tests/test_cells.py::test_set_cell_size` |

用法：

```powershell
python -X utf8 -B tools/unfix.py run --repo <r> --commit <sha> --tests tests/x.py --out <o> `
  --patch fix.patch --regression-tests tests --known-failures tests/a.py::t1 tests/b.py::t2
```

两条设计约束，都是被实际情况逼出来的：

1. **不能要求回归全绿**。很多仓库基线本身就有失败（rich 在本机是 `7 failed / 976 passed`，
   与 pygments、终端有关的既有问题）。所以比的是「**相对基线有没有新增**」，
   `--known-failures` 就是基线那批已知失败。
1. **回归闸只在题目测试通过之后才跑**。题目都没修好时不需要查回归（`code == 0` 才进入），
   所以想验证回归闸本身，命令要带 `--apply-gold` 或一个能修好题目的补丁 ——
   否则你看到的是 `failed`（题目没过），看不到 `infra_error`（回归没跑成）。
2. **回归检查没跑成，等于没查**。四种退出码都吃过亏：`4`（路径不存在）、`124`（挂住）、
   `5`（一条没收集到）、`2`（收集错误）。它们都让 `FAILED` 行为空，于是「新增 0 条」、
   闸悄悄放过 —— 实测 `status='passed'` 而 `steps.regression.exit=4`。
   现在这些一律判 `infra_error`，reason 写清「等于没查，不能算通过」。
3. **用例 id 必须归一化路径分隔符再比**。pytest 在 Windows 上打 `tests\a.py::t`，
   人和文档写 `tests/a.py::t`。不归一化时每条已知失败都被算成新增，**闸永远红 ——
   一个永远红的闸等于没有闸**。

---

## 7. 最危险的假绿：补丁改测试

判据（fail-to-pass）来自测试。被评测的 agent 只要**改测试**，任何实现都能变绿。

实测（`648ceda2`）：源码仍是修复前，一份**只把断言改成 `assert True`** 的补丁让 `pytest` 报
`4 passed in 0.05s` —— 如果不拦，评测器会诚实地判它 `passed`。

现在 `--patch` 会先读补丁，检查 `+++ b/<path>` / `--- a/<path>` 行头有没有落到测试前缀下；
落到了就拒绝，**并且状态是 `infra_error` 不是 `failed`**：

```
reason = 补丁修改了测试文件：tests/test_file_proxy.py。
         判据来自测试，改测试等于改判据 —— 这份补丁不能评
```

为什么是 `infra_error` 而不是 `failed`：这不是「没做对」，是**这份提交不属于被评测的范围**。
把它记成 `failed` 会污染通过率的分母。

### 不止是测试文件

实测（副机 L89/L90）：一道源码 `f()==1`、测试要 `2` 的**必红题**，补丁只往仓库根加一个 `conftest.py`：

```python
def pytest_collection_modifyitems(config, items):
    for item in items:
        item.add_marker(pytest.mark.skip(reason="x"))
```

`status` 就变成 `passed` —— **代码一行没改**。它不在 `tests/` 前缀下，前缀守卫对它完全无感。

现在判据守卫认三类（`patch_touches_judgment()`）：

1. 测试前缀下的文件；
2. **任意位置**的 `conftest.py` / `pytest.ini`；
3. `tox.ini` / `setup.cfg` / `pyproject.toml` 里动到 `[pytest]` / `[tool:pytest*]` 段。

### 这个守卫的边界（实测过，别当成万能）

它是一条**白名单**，不是静态分析：

| 入口 | 实测结果 |
|---|---|
| 根目录加 `sitecustomize.py`（让 Python 启动时 monkeypatch 被测模块） | **打不进来**：`site` 在 `-m` 之前执行，cwd 还不在 `sys.path` 上；加与不加，测试都是 `1 failed` |
| 改 `pytest.ini` 的 `addopts` / 加 `[tool.pytest.ini_options]` | 被拦 |
| 改根 `conftest.py`（用例全 skip） | 被拦 |
| **`conftest` 里 `import` 的辅助模块被改**（副机 L92 的 E2） | 已覆盖：`conftest_imported_modules()` 从仓库里每个 `conftest.py` 收集 import 的模块名，补丁动到同名文件就拒绝。有界分析，不追多层依赖 |
| **已有 `[pytest]` 段里加 `addopts`**（副机 L92 的 E1；段头不在 diff 里） | 已覆盖：配置文件**动就算**，不再做内容判断 —— 内容判断实测不可靠 |
| 环境变量（`PYTEST_ADDOPTS`）影响会话 | **没覆盖** —— 补丁改不到环境，但执行器可以 |

PYTEST_ADDOPTS 那一行是**主动写出来的缺口**：因为「守卫没覆盖」和「守卫说没问题」在读的人眼里可能是同一件事。

---

## 8. 已知边界

- **只认「源码+测试同改」的提交**。只改源码或只改测试的提交，反解不出 fail-to-pass。
- **源码目录与测试目录的前缀是假设，不是事实**。默认 `fastapi/ httpx/ requests/ rich/ src/` 与 `tests/`；
  布局不同的仓库要用 `--src-prefixes` / `--test-prefixes` 指定，否则会得到
  「该提交没有源码/测试改动」——**那个原因会把人引到错的方向**，所以现在的报错里会带上生效的前缀。
- **需要仓库有完整历史**。快照若是浅克隆（只有几个提交），候选会很少。
- **gold 的定义是 HEAD 的源码**，不是 `<commit>` 的源码 —— 因为测试取自 HEAD，而该提交之后
  同一文件可能又改过。用 `<commit>` 当 gold 实测会让 2/16 道题正控不过。
- **它评的是「测试能不能从红变绿」**，不评「补丁写得好不好」。一个把测试删掉的补丁也能让它变绿 ——
  所以评外部补丁时，要另外约束「不许改测试文件」。
