<!--
GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
PURPOSE: 承载同目录 `package.json` 的文件头（JSON 不支持注释），并说明「本包为什么没有 cordis.patch.yml」
ORIGIN: none（本文件；体例与另两套插件一致）
CUMULATIVE-DELTA: 首版。
-->

# package.json（插件包 manifest）的文件头

| 字段 | 值 | 判据 |
|---|---|---|
| `name` | `@autoresearch/dsh-autodl-events` | 独立命名空间，避免与出货包重名 |
| `type` | `module` | 按 ESM 加载 |
| `main` / `exports["."]` | `./lib/plugin.js` | 插件入口；另导出 `./package.json` 便于安装器读元数据 |
| `files` | 见文件 | 发布白名单（`lib/index.js`、`lib/core.mjs` 是转接层，必须一起带） |
| `license` | `MIT` | 通用开源许可 |

**为什么没有 `cordis.patch.yml`**：这套插件在部署时按**生成的加载配置**挂载（见 `kernel/hosts/dsh/tools/gen-profile-patch.mjs`），
不需要随包带一份补丁模板。⛔ 不要把本目录当 bundle 用；`dsh.bundle.patch` 不在这里。

安装、配置、验收与回滚见同目录 `README.md` 与仓库 `docs/dsh.md`。
