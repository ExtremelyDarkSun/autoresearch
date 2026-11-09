<!--
GENERATED-BY: portable project organization
LAST-EDIT: 2026-11-09
PURPOSE: 从clone到固定布局的确定步骤
ORIGIN: maintained layered workspace organization, neutral extraction
CUMULATIVE-DELTA: 固定项目组织模板；实例事实留空，现行规则直接链接唯一正文。
-->
# 安装与恢复

本目录就是随 Git 提供的固定项目模板。目录、入口、索引与中性规则已确定，不需要让 AI 重新设计。
在框架仓库根运行：

```powershell
python -X utf8 -B research.py init --workspace ../my-research
python -X utf8 -B research.py layout-check --workspace ../my-research
```

目标须不存在。初始化会复制本模板、当前内核契约与所选 profile，生成空状态库与来源清单，不复制真实比赛数据。
工具源码仍从框架仓库运行，工作区通过 `--workspace` 指定。初始化生成的命令会绑定实际框架路径：

```powershell
python -X utf8 -B "@FRAMEWORK@/research.py" workspace --help
```

只想保留旧式单文件指令工作区时显式使用 `init --layout bundle`，它不提供本目录结构。

## 宿主配置

多级 AGENTS 是上下文触发入口，不是自动加载成功的证明。主会话先显式读取根规则，再读所涉目录入口。
DSH 插件仍须另行安装；clone 不改宿主。三插件源码在内核，不能复制旧项目限定目录的配置。
对实际 workspace 设置文档列表、子会话过滤政策和机器事件绑定；核插件确已加载，再测主会话与子会话输入。
预算不足时提高实际宿主预算，不裁切规则。`instructions --bundle` 仍是完整材料诊断，不能把全量合并当作分层加载的证明。
完整部署与插件步骤见框架仓库的 docs/dsh.md；本模板不包含私有profile状态。

## 恢复与更新

复制项目文件及其私有备份后，另行核对代码版本、原始产物、运行资源与预算。旧会话回执不自动获得新会话的可信身份。
初始化清单写入 `.autoresearch/layout-manifest.json`。只更新模板不能覆盖已维护的真实项目；先比对各文件与来源哈希，再人工确认合并。
