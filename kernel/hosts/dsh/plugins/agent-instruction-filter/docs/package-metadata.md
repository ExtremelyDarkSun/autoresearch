<!--
GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
PURPOSE: `package.json` 的字段说明与安装边界（bundle manifest 是什么、由谁改 profile）
ORIGIN: install/agent-instruction-filter/package.json.README.md（重写为通用说明，去实例路径与判据）
CUMULATIVE-DELTA: 首版。包名改独立命名空间；说明 `dsh.bundle.patch` 决定本目录是不是 bundle；
  明确「安装器改 profile、人不手改」这一条边界。
-->

# package.json（bundle manifest）

本文件解释同目录 `package.json` 的每个字段，以及「谁能改 profile」。

| 字段 | 值 | 判据 |
|---|---|---|
| `name` | `@autoresearch/dsh-agent-instruction-filter` | 必须唯一；用独立命名空间，避免与宿主出货包重名 |
| `version` | `1.0.0` | 与内核发布节奏一致 |
| `private` | `true` | 只是**不允许误发到公共 npm**，⛔ 不影响它进本地 git 仓库 |
| `type` | `module` | 插件按 ESM 加载 |
| `exports["."]` | `./index.js` | Host 插件入口 |
| `dsh.bundle.patch` | `./cordis.patch.yml` | **这一行使本目录成为 bundle**：安装器的 `install_bundle` 靠它找到补丁 |

## 安装边界

- profile 的 `package.json` / `cordis.patch.yml` **由安装器的 `install_bundle` 改**，⛔ 不要手改（手改容易整份替换出货行）。
- 本目录的 `cordis.patch.yml` 是**出厂 config 模板**；实际启用的值以部署方复核后的那份为准。
- 验收要分三层记录：**文件已改** / **宿主已加载** / **目标会话实际使用**。⛔ 三者不能互相替代，详见 `docs/dsh.md`。
