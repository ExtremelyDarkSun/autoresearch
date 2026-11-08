<!--
GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
PURPOSE: 承载 `config/deployment.example.json` 的文件头（JSON 不支持注释），并说明生成器怎么用它
ORIGIN: none（本文件）
CUMULATIVE-DELTA: 首版。
-->

# deployment.example.json 的文件头

本目录的 JSON 是**部署参数示例**（不是运行态配置），字段含义：

| 字段 | 说明 |
|---|---|
| `workspace` | 项目根绝对路径。运行态落在 `<workspace>/.autoresearch/`，事件目录与绑定文件都由它推出来 |
| `plugins.<id>.enabled` | 是否启用。**示例文件里三套全是关闭**；`enabled: false` / `mode: off` / `fallback: hold` 是出厂默认，要启用哪套就改那一套自己的键（见 `docs/dsh.md` §1 与 §3.1） |
| `plugins.doc-gap.docs` | 判据文档清单（`label` / `path` / 可选 `maxChars`）。`maxDocChars` 必须大于每份字符数 |
| `plugins.doc-gap.policy` | 判定范围 / 排除清单 / 三类判据 / 结尾锚点 —— **必须由部署方给**，插件不内置项目专用范围 |
| `plugins.autodl-events.projects` | 规范化路径前缀（项目根），不是子串 |

用法（只生成、不写 profile）：

```powershell
node kernel\hosts\dsh\tools\gen-profile-patch.mjs --deployment kernel\hosts\dsh\config\deployment.example.json --out .\out\patch.generated.yml
```
