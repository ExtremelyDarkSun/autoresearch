# 凭据清单：要什么、从哪拿、放哪里

> **为什么需要这份文档**
> 本仓库**不含任何凭据**，也不含获取方式。`project/INSTALL.md:64` 明确写着私有恢复包「默认排除凭据」，
> 连 `--include` 也只限「非凭据材料」。所以一台新机器必须先按本文把凭据配好，才可能跑真实实验。
>
> **本文的证据基础**：凭据名与字段位置全部来自源码（逐条标了 `文件:行号`）；
> 获取路径是各平台的通用入口，平台改版可能变化，**以实际页面为准**。

---

## 0. 总表

| # | 能力域 | 凭据（确切名字） | 落点 | 不配的后果 |
|---|---|---|---|---|
| 1 | 飞书消息通道 | `FEISHU_APP_ID`<br>`FEISHU_APP_SECRET`<br>`FEISHU_VERIFICATION_TOKEN`<br>`FEISHU_ENCRYPT_KEY` | 环境变量（配置里只写 `env:XXX` 引用） | `channels doctor` 报未配置；`send_*` 抛 `NotSent` |
| 2 | AutoDL 云机 | `AUTODL_TOKEN` | 环境变量 | 查不到实例列表，云机相关功能全不可用 |
| 3 | 远端 SSH | `key_file`<br>`known_hosts`（另有 `host`/`user`/`port`） | `SSHDeployment` 字段 | `remote` 系列默认 `enabled=False`，不会连任何机器 |
| 4 | Kaggle 平台 | ⚠️ **源码未实现** | — | 见 §4，这是缺实现不是缺凭据 |
| 5 | 数据集 / 模型 / 提交包 | 无固定名，部署方自定 | 你自定的路径 | 真实评测无法开始 |

**这五类里，前三个配了就能通「外部通道/云机/远端」三条线；第 4 类配了也没用（代码里没有客户端）；第 5 类必须你提供真实数据。**

---

## 1. 飞书（真实消息通道 / 审批回执）

### 1.1 确切的四个字段

源码 `kernel/autoresearch/channels/__init__.py:17-20`：

```python
'app_id_ref': 'env:FEISHU_APP_ID',            # ⛔ 只写引用，不写值
'app_secret_ref': 'env:FEISHU_APP_SECRET',    # ⛔ 只写引用，不写值
'verification_token_ref': 'env:FEISHU_VERIFICATION_TOKEN',
'encrypt_key_ref': 'env:FEISHU_ENCRYPT_KEY',
```

**注意这个设计**：配置里写的是 `env:FEISHU_APP_ID` 这种**引用**，不是值。真实值放在环境变量里。所以：

```powershell
$env:FEISHU_APP_ID = 'cli_xxxxxxxxxxxx'
$env:FEISHU_APP_SECRET = 'xxxxxxxxxxxxxxxxxxxx'
$env:FEISHU_VERIFICATION_TOKEN = 'xxxxxxxxxxxx'
$env:FEISHU_ENCRYPT_KEY = 'xxxxxxxxxxxxxxxxxxxx'
```

### 1.2 从哪拿

在飞书开放平台创建一个「企业自建应用」，然后取四样东西：

| 字段 | 在飞书后台的位置 |
|---|---|
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 应用详情 → **凭证与基础信息** |
| `FEISHU_VERIFICATION_TOKEN` | 应用详情 → **事件订阅** → 订阅配置 |
| `FEISHU_ENCRYPT_KEY` | 同上位置（若开启加密推送才有） |

还需要在应用里配：**事件订阅回调地址**、**开通消息发送权限**、**把机器人加进目标群**。

### 1.3 还要配的项（不只是四个凭据）

源码 `channels/__init__.py:21-25` 还有四个非凭据但必填的字段：

```python
'allowed_recipients': ['oc_xxx'],        # 收件人允许列表，空列表 = 不发
'allowed_sender_open_ids': ['ou_xxx'],   # 谁能让人拍板
'chat_bindings': {'oc_xxx': 'exp-01'},   # 显式 chat → 实验绑定
'send_enabled': False,                   # 默认不发，部署时显式打开
```

- `oc_xxx` = 群/会话的 chat_id
- `ou_xxx` = 用户 open_id

### 1.4 怎么验

```powershell
python -X utf8 -B research.py channels --config "<上面那段 JSON>" doctor
```

期望：`ok=True`。若 `send_enabled=False`，发送会被明确拦下（`NotSent`），这是设计行为，不是故障。

---

## 2. AutoDL（云机查询 / 变化事件）

### 2.1 确切的名字

源码 `kernel/autoresearch/backends/autodl/availability.py:80-81`：

```python
#: 默认凭据环境变量**名**（不是值）。
DEFAULT_TOKEN_ENV = "AUTODL_TOKEN"
```

API 地址（`availability.py:79`）：

```python
DEFAULT_API_URL = "https://www.autodl.com/api/v1/instance"
```

### 2.2 从哪拿

AutoDL 控制台 → 账户/个人设置 → **API 令牌 / 访问令牌**（不同版本叫法可能不同），复制那串 token。

### 2.3 怎么放

```powershell
$env:AUTODL_TOKEN = '<你的 token>'
```

### 2.4 怎么验

用内核自带的查询函数（只读，不会开机、不会花钱）：

```powershell
python -X utf8 -B -c @"
from kernel.autoresearch.backends.autodl.availability import query_instances
r = query_instances()
print(r)
"@
```

期望：能返回实例列表 JSON。若报「无 token」，检查环境变量名是否恰好是 `AUTODL_TOKEN`。

> ⚠️ **这条查询是只读的**。开机、创建实例、提交任务都属于**花钱动作**，内核默认不做，需要显式授权。

---

## 3. 远端 SSH（受管远端执行）

### 3.1 确切的字段

源码 `kernel/autoresearch/backends/ssh.py:92-103`：

```python
class SSHDeployment:
    """一台 SSH 机器的部署参数。**默认 enabled=False，不会连任何机器。**"""
    enabled: bool = False
    host: str | None = None
    user: str | None = None
    port: int = 22
    key_file: str | None = None
    known_hosts: str | None = None
    remote_dir: str = "/tmp/autoresearch-ssh"
    python: str = "python3"
```

### 3.2 从哪拿

**这不是"申请"来的，是你自己的机器密钥**：

```powershell
# 没有密钥就生成一个（不要设空口令）
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\id_ed25519 -N '""'

# 把公钥放到远端
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh user@host "cat >> ~/.ssh/authorized_keys"
```

`known_hosts` 用首次连接时自动生成的那个文件即可（`~/.ssh/known_hosts`）。

### 3.3 怎么验

```powershell
python -X utf8 -B research.py remote --help
```

`remote` 系列默认 `enabled=False`，需要显式部署参数才会连机器。

---

## 4. Kaggle：这是「缺实现」，不是「缺凭据」

**必须说清楚这一点，否则会浪费时间。**

- `README.md:53` 提到「通用 Kaggle 客户端」，但**源码里 0 处命中**（审计实测）
- 所以即使你配好 `~/.kaggle/kaggle.json`，**也没有代码会去读它**
- 真实 Kaggle 线要么等这个客户端被实现，要么你在自己的适配层里接

如果你只是想拿 Kaggle 数据：`kaggle.json` 的标准拿法是 **kaggle.com → 头像 → Settings → API → Create New API Token**，会下载一个 `kaggle.json`，放到 `~/.kaggle/kaggle.json`。**但这与本仓库的代码无关**。

---

## 5. 数据集 / 模型 / 提交包

仓库不提供，也没有约定路径。你需要自己定，并且在两处对齐：

1. `profile.toml` 的 `[evaluation]` 段（评测命令与数据在哪）
2. 你的评测器实际去哪里读

`project/INSTALL.md:53` 的原文是：「凭据、数据集、模型与提交包单独配置或备份。不能把这些内容上传公开GitHub。」

---

## 6. 安全规则（三条硬线）

| 规则 | 依据 |
|---|---|
| ⛔ **凭据绝不进 git** | `README.md:26`、`docs/NEW_MACHINE_RECOVERY.md:30,78`、`project/INSTALL.md:53` 四处都写明 |
| ⛔ **私有恢复包不含凭据** | `project/INSTALL.md:64`：默认排除，且 `--include` 也只限「非凭据材料」 |
| ⛔ **配置里只写引用不写值** | `channels/__init__.py:17-20` 逐条标注「只写引用，不写值」 |

**所以跨机器迁移时，凭据永远需要人重新配一次。** 这是设计选择，不是疏漏。

---

## 7. 一页检查表

```powershell
# 逐条跑，全 True 才算配齐（Kaggle 那条恒为 False，属已知缺实现）
foreach ($n in 'FEISHU_APP_ID','FEISHU_APP_SECRET','FEISHU_VERIFICATION_TOKEN','FEISHU_ENCRYPT_KEY','AUTODL_TOKEN') {
    '{0,-32} {1}' -f $n, [bool](Get-Item "env:$n" -ErrorAction SilentlyContinue).Value
}
'SSH key_file                     ' + (Test-Path "$env:USERPROFILE\.ssh\id_ed25519")
'known_hosts                      ' + (Test-Path "$env:USERPROFILE\.ssh\known_hosts")
'kaggle.json（本仓库未使用）      ' + (Test-Path "$env:USERPROFILE\.kaggle\kaggle.json")
```
