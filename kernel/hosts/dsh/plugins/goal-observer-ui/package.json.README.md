<!--
GENERATED-BY: portable synchronization (DSH agent, 2026-11-13 +08:00)
LAST-EDIT: portable synchronization (DSH agent, 2026-11-13 +08:00)
PURPOSE: 承载同目录 package.json 的文件头（JSON 不支持注释），并说明客户端包的发现方式。
ORIGIN: install/dsh-goal-observer-ui/package.json（2026-11-13 读码），包名改为公开仓库的 @autoresearch/*
CUMULATIVE-DELTA: 首版。入口 index.js 保持 ESM；新增 main 字段便于按文件路径挂；files 只列运行期文件。
-->
# package.json（DSH bundle manifest）的文件头

本目录的 `package.json` 是 DSH bundle manifest；JSON 不支持注释，所以文件头在这里。

| 字段 | 值 | 说明 |
|---|---|---|
| `name` | `@autoresearch/dsh-goal-observer-ui` | 公开仓库包名；部署配置可按包名或目录绝对路径挂。 |
| `version` | `0.2.0` | 与 host 插件同版本；客户端只做显示，不随 host 决策格式做破坏性变更。 |
| `description` | 中文一句 | 说明「按 goalId/revision/round 拼显示副本、不改原件」。 |
| `type` / `main` | `module` / `./index.js` | 宿主侧入口（空 `apply()`），只负责让宿主发现这个包。 |
| `exports["./client"]` | `./client.js` | 浏览器端 `window.__ModuleLoader__` 加载的显示插件。 |
| `files` | 运行期文件 | `npm pack` 只装 `index.js` · `client.js` · `cordis.patch.yml` · 两份 README。 |
| `dsh.bundle.patch` | `./cordis.patch.yml` | 只插入一行 `goal-observer-ui`；出厂 `disabled: true`（真正的 UI 开关，见 README §4.1）。 |
| `dsh.client` | `platform: web` · `immediately: true` · inject 列表 | 客户端包依赖的出货 UI 包；缺任何一个都不加载。 |

⛔ 本包不发起网络请求、不写会话、不调用模型；显示内容全部来自已有事件。
