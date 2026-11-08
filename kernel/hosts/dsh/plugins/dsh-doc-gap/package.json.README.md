<!--
GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
PURPOSE: 承载同目录 `package.json` 的文件头（JSON 不支持注释）
ORIGIN: none（本文件；包信息说明与 agent-instruction-filter 同体例，避免重复一份长文）
CUMULATIVE-DELTA: 首版。
-->

# package.json（bundle manifest）的文件头

字段含义与安装边界和同层 `agent-instruction-filter/package.json.README.md` 一致：

- `name` = `@autoresearch/dsh-doc-gap`（独立命名空间，避免与出货包重名）
- `type` = `module`，`exports["."]` = `./index.js`（Host 插件入口）
- `dsh.bundle.patch` = `./cordis.patch.yml`（**这一行使本目录成为 bundle**）
- `private: true` 只表示「不误发到公共 npm」，⛔ 不影响它进本地 git 仓库

安装与三层验收见仓库 `docs/dsh.md`。
