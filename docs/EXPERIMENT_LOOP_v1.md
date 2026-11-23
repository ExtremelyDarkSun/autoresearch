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