# 从本地 Kaggle 工作区发布过来的配置

变更说明：首版。这里放的是**配置与文档**，不是数据、不是凭证。

## 来源

| 本仓库文件 | 来源（本地 Kaggle 工作区相对路径） |
|---|---|
| `profiles__README.md` | `profiles/README.md` |
| `INSTALL.md` | `INSTALL.md` |
| `Kaggle平台__AGENTS.md` | `Kaggle平台/AGENTS.md` |
| `…bench-3tasks-summary.md/.json` | `Kaggle平台/实验/loop_2026-11-23_local_swe_probe/agent_attempts/` |

每份文件顶部有一行来源头（含 sha256 前 16 位与字节数），`MANIFEST.json` 是机器读的一份。

## 怎么更新

不要手工 copy —— 手工 copy 时你不会逐个文件检查有没有 `KGAT_` 开头的密钥。

```bash
python tools/publish_from_kaggle.py --src "<Kaggle 工作区根>" --dest docs/from-kaggle
```

这个工具做三件事：只复制白名单里的文件；每个文件加来源头；**扫到疑似凭证就整体放弃**，
一个文件都不写。凭证模式见 `tools/publish_from_kaggle.py` 的 `SECRET_PATTERNS`。

## 明确不发的东西

- `.kaggle_auth/`、`kaggle.json` —— 凭证；
- `data/`、`archive/`、`temp/` —— 数据集与临时产物（体积大、且多数受比赛条款约束）；
- 任何实验输出目录（`state/`、`runs/`）。

## 为什么放在这个仓库

副机复现时要能拿到**同一份配置**。放在这里，一次 `git pull` 就同步了；
散在本地目录里，副机只能靠人转述。