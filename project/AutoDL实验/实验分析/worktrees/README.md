<!--
GENERATED-BY: portable organization, 2026-11-09 +08:00
LAST-EDIT: 2026-11-09 +08:00
PURPOSE: 专题代码副本的落点、填写字段与验收边界。
ORIGIN: existing project placeholder README
CUMULATIVE-DELTA: 恢复中文目录并提供可填写格式，清空实例不清空章节和字段。
-->
# 专题代码副本

代码由workspace topic建立。每个专题有独立Git工作树或独立Git仓库。此表没有实例，模板行不能作为已建立或已验证的证明。

| 专题 | 代码相对路径 | 来源仓库引用 | 固定commit | 分支 | 创建方式 | 隔离检查与证据 |
|---|---|---|---|---|---|---|
| `<专题>`（模板） | `<本目录/专题>` | `<仓库>` | `<完整commit>` | `<分支>` | `git-worktree/empty-independent-git` | `<命令、退出码、证据路径或未验证>` |

已有代码不由init覆盖。原始源仓库保留。新电脑上重建worktree须核commit、未提交改动和分支，不能只复制.git指针就声称工作树有效。
