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