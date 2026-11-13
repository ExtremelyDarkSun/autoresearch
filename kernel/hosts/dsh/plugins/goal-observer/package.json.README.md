<!--
GENERATED-BY: portable synchronization (DSH agent, 2026-11-13 +08:00)
LAST-EDIT: portable synchronization (DSH agent, 2026-11-13 +08:00)
PURPOSE: 承载同目录 package.json 的文件头（JSON 不支持注释），并说明每个字段为什么这么填。
ORIGIN: install/dsh-goal-observer/package.json 的部署版（2026-11-13 读码），按公开仓库的包名/目录重写
CUMULATIVE-DELTA: 首版。去掉部署版的 peerDependencies 与绝对版本锁定（公开仓库不假设本机 DSH 版本）；
  入口由部署版 index.mjs 保持；新增 exports["./core"] 方便离线自检按路径导纯函数；files 只列运行期文件。
-->
# package.json（DSH bundle manifest）的文件头

本目录的 `package.json` 是 DSH bundle manifest；JSON 不支持注释，所以文件头在这里。

| 字段 | 值 | 说明 |
|---|---|---|
| `name` | `@autoresearch/dsh-goal-observer` | 公开仓库的包名；部署配置既可按包名挂，也可按目录绝对路径挂。 |
| `version` | `0.2.0` | 相对部署版 0.1.3 的行为版本：通知参数化、complete 落库顺序、解析逐候选、卸载 disarm。 |
| `description` | 中文一句 | 说明「为带前缀的原生 goal 生成观察指令、只追加、失败继续、绝不暂停、通知默认关闭」。 |
| `type` | `module` | 入口是 ESM（`index.mjs`）。 |
| `main` / `exports["."]` | `./index.mjs` | 宿主 Loader 加载的入口；导出 `name`、`inject`、`apply`。 |
| `exports["./core"]` | `./observer-core.mjs` | 纯函数核心，供离线自检直接导入。 |
| `files` | 运行期文件 | `npm pack` 只装 `index.mjs` · `observer-core.mjs` · `cordis.patch.yml` · 两份 README。 |
| `dsh.bundle.patch` | `./cordis.patch.yml` | 安装器读这个补丁模板；模板出厂 `enabled: false`。 |
| `engines.node` | `>=20.3` | `AbortSignal.any` 从 Node 20.3 才加入；20.0/20.1/20.2 会直接不可用。另用到 `AbortSignal.timeout` / `toReversed`。 |
| `license` | `MIT` | 与公开仓库一致。 |

⛔ 不写死任何本机路径、聊天 ID、话题、会话身份；这些值只能来自部署侧配置或部署侧通知脚本。
