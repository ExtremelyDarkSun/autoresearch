<!--
GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
PURPOSE: 承载 `policy.template.json` 的文件头（JSON 不支持注释），说明每个字段填什么
ORIGIN: none（本文件）
CUMULATIVE-DELTA: 首版。
-->

# policy.template.json 的文件头

判定政策是**部署方给的**，插件只提供中性默认。四组字段：

| 字段 | 填什么 | 判据 |
|---|---|---|
| `scope.include` | 哪些方面算「可能与文档有关」，一条一个方面 | 为空 = 没限定范围（`verify-real-docs.mjs` 会要求它非空） |
| `scope.exclude` | 哪些话题一律判 covered，不打扰用户 | 为空 = 没排除清单（同上） |
| `criteria` | 三类判据的措辞（covered / partial / missing） | 判定器照这三句答 |
| `systemIntro` | 提示词首段的角色说明（字符串数组） | 可留默认 |
| `anchor` | 放在提示词**最后一段**的范围锚点 | 为空 = 没锚点（同上） |

用法：复制到部署侧填写，用插件 `config.policy` 传进去；⛔ 不要改插件里的默认值来适配某个项目。

```powershell
node kernel\hosts\dsh\plugins\dsh-doc-gap\verify-real-docs.mjs --config <含 docs 与 policy 的配置.json>
```
