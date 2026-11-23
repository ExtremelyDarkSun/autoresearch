<!-- 来源：Kaggle 工作区 `Kaggle平台/实验/loop_2026-11-23_local_swe_probe/agent_attempts/bench-3tasks-summary.md`
     sha256=9bb8379bbe04a121…  956 字节
     由 tools/publish_from_kaggle.py 发布；凭证扫描已通过 -->

# 一轮小型评测：读数与口径

- 仓库：`C:\Users\Administrator\AppData\Local\Temp\swel\mut-rich_4079`
- 可用题：**3**（候选里过了红→绿两关的）
- 负控（不打补丁应红）：**3/3**
- 正控（打 gold 应绿）：**3/3**
- 量具成立：**True**（两条对照都成立且至少有一道题）

## 补丁评分

| 口径 | 值 | 分母是什么 |
|---|---|---|
| pass_rate_scored | 1.0 | 真评过的题（3）|
| pass_rate_all | 1.0 | 全部可用题（3）|

没评成：**0** 道（`no_patch` 或 `infra_error`）。

> 两个口径都写出来，是因为它们回答不同问题。只报好看的那个等于替读者做决定。

## 逐题

| 提交 | 主题 | 负控 | 正控 | 补丁 |
|---|---|---|---|---|
| `648ceda2` | proxy isatty | failed | passed | passed |
| `4ca56966` | preserve newlines | failed | passed | passed |
| `d4f8620c` | empty with end | failed | passed | passed |
