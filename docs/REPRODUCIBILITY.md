# 复现性边界：这个项目能自动做到哪一层

> 本文回答三个问题：**能自动复现到哪？边界为什么在那？边界之后需要什么？**
> 结论来自 2026-11-10 ~ 11-22 在 Windows 11 上的一次完整实测（105 轮执行记录），不是推演。

---

## 一句话

**代码与工作区骨架能全自动重建（到独立 Python 环境为止）；再往前一步需要四类真实输入，它们不在仓库里，且没有任何获取入口。**

---

## 14 层判定

| 层 | 内容 | 判定 | 判据（可复算） |
|---|---|---|---|
| **L0** | clone | ✅ 通过 | `HEAD` 与公开提交逐字相等 |
| **L1** | 零依赖自检 | ✅ 通过 | `selftest` exit 0：`Ran 272 tests OK` + `Ran 1218 tests OK (skipped=11)`；`--plugins` 末行 `51 passed`；**未装任何依赖** |
| **L2** | `init` 工作区 | ✅ 通过 | exit 0；约 53 目录 / 125 文件；`state.empty=true`、`workspace_layout.ok=true` |
| **L3** | 建专题 + 负责人 | ✅ 通过 | `provision.json`: `status=provisioned`、`isolation=empty-independent-git`；`owner_session` 已绑定 |
| **L4** | 独立 venv | ✅ 通过 | `pyvenv.cfg`: `version`、`include-system-site-packages=false`、`command` 含 `--without-pip`；`environment_verified=true` |
| **L5** | 评测依赖 | ❌ 缺失 | 仓库只有 `requirements-feishu.txt`（飞书加密可选件）；**无评测依赖清单、无安装入口** |
| **L6** | 题单与评测命令 | ❌ 缺失 | `profile.toml`: `command=[]`、`configured=false` |
| **L7** | 数据与模型 | ❌ 缺失 | 仓库无来源 / 许可 / 校验和 / 落点；`profile.toml` 无 data/model 字段 |
| **L8** | 5 域凭据 | ❌ 缺失 | 文档 0 个凭据名；源码只有引用名（见 [CREDENTIALS.md](CREDENTIALS.md)） |
| **L9** | 真实评测与提交 | ⬜ 未尝试 | 依赖 L5–L8 |
| **L10** | 主链入口闸门 | ✅ **通过（闸门有效）** | `workflow check` **exit 9**，problems 非空，**拒绝且零子进程**；`budget grant` **exit 2**「需要顶层会话中带客户端rpcId的真实用户消息」 |
| **L11** | a2a 授权门 | ✅ **通过（结构性拒代发）** | a2a 会话 `parentSession=null`、`delegationDepth=0`、`isSeeded=false`、`source.rpcId=null` |
| **L12** | 独立审计 | ✅ **通过** | `independence=verified`、`audits_used=2`、`remaining=0`、`checks.audit.ok=true` |
| **L13** | 真人授权 grant | ⬜ 未证（**设计内人闸**） | 要求真人顶层 GUI 会话里 `type=user/message` 且 `rpcId` 非空的消息 |
| **L14+** | 真实实验 / 提交 | ⬜ 未开始 | 依赖全部上述 |

**汇总：通过 8 层、缺失 4 层、未尝试 2 层。**

---

## 自动边界：L4

**L0 → L4 全自动**，一条命令链走完，不需要任何凭据、不需要任何人工判断：

```powershell
python -X utf8 -B research.py init --workspace <ws>
python -X utf8 -B research.py workspace topic --workspace <ws> --topic <t> \
    --objective "..." --requirements-file <f> --owner-session <s>
python -X utf8 -B research.py topics --db <ws>/.autoresearch/runtime.sqlite3 \
    --commit claim --topic <t> --session <s> --generation 0
```

**L5 开始断在四个字上：没有输入。**

不是"配置写错了"，是仓库里**物理上没有**这些东西：

| 缺的东西 | 仓库里的实际情况 |
|---|---|
| 依赖清单 | 只有 `requirements-feishu.txt`（README 明说是飞书加密的可选件） |
| 评测命令 | `profile.toml` 的 `command=[]` |
| 数据 / 模型 | 无来源、无许可、无校验和、无落点 |
| 凭据 | 文档 0 个名字；恢复包明确排除 |

---

## 三道闸门（L10–L12）为什么算"通过"而不是"缺失"

这是最容易被误读的一点。

**它们不是阻塞我们的障碍，而是被我们验证过「确实有效」的安全机制**：

| 闸门 | 实测行为 | 意义 |
|---|---|---|
| **L10 主链入口闸** | `workflow check` exit 9，**零子进程**，拒绝执行 | 该拦的地方真拦住了，不是警告一下继续跑 |
| **L11 a2a 授权门** | a2a 会话 `rpcId=null`，结构上不可能充当真人授权 | agent **无法伪造**用户授权 |
| **L12 独立审计** | `independence=verified`、`audits_used=2`，不通过不放行 | 审计不是走过场 |

**所以 L13 的"未证"不是缺陷，是设计的终点收益**：它宁可停下来等人，也不让 agent 自己给自己放行。

---

## 常见误读

| 误读 | 实际情况 |
|---|---|
| 「`doctor` 显示 `can_run=false`，是不是装错了？」 | **不是**。初始态就是 false，因为 `command=[]`、没配预算 |
| 「`ready_for_evaluation=false`，装上依赖就会变 true 吧？」 | **不会**。`workspace.py:109/129` 是建专题时的硬编码，全仓无第二写入点 |
| 「README 说支持 Kaggle，配个 `kaggle.json` 就行」 | **不行**。`README.md:53` 提到的 Kaggle 客户端源码 0 处命中，是缺实现 |
| 「venv 建好了就能装包」 | **不能**。它是 `--without-pip` 建的，得先 `ensurepip` |
| 「L13 卡住说明项目不完整」 | **恰恰相反**。L13 是设计内的人闸，证明授权链没被绕过 |

---

## 三个真实的代码缺陷（不是"缺输入"）

| # | 缺陷 | 位置 | 后果 |
|---|---|---|---|
| 1 | 两个「就绪标志」是 `provision` 常量，无第二写入点 | `workspace.py:109` / `:129` | 依赖装齐了字段也不翻转，它显示的是静态文本 |
| 2 | Kaggle 客户端缺失 | `README.md:53`（源码 0 处） | 配了凭据也无代码读取 |
| 3 | venv 用 `--without-pip` 且无安装入口 | `workspace.py:101` | 连 pip 都要自己 `ensurepip`，文档没写 |

这三条是**代码层可修的**（见 `docs/UPGRADE_SYNC.md` 的演进约定）。

---

## 想把边界往前推，需要做什么

| 目标 | 必须补的东西 |
|---|---|
| 到 L5 | 评测依赖清单 + 一个安装入口（装到专题 venv，并更新 `dependencies_installed`） |
| 到 L6 | `profile.toml` 的 `[evaluation].command` + 真实题单 |
| 到 L7 | 数据集/模型的来源、许可、校验和、落点 |
| 到 L8 | 按 [CREDENTIALS.md](CREDENTIALS.md) 配 5 类凭据 |
| 到 L13 | 一条真人顶层 GUI 会话消息（**必须人做，agent 不得代发**） |
| 到 L14 | 上述全部 + 明确解除「不花钱 / 不提交」红线 |

**注意顺序**：L5–L8 是"人能提供"的，L13 是"必须人亲自做"的，两者性质不同。
