// GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
// LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
// PURPOSE: 转接层 —— 稳定入口 `lib/index.js` 指到 `lib/plugin.js`（实现只有一份，⛔ 这里不写逻辑）
// ORIGIN: install/autodl-events/lib/index.js
// CUMULATIVE-DELTA: 首版。保留「换文件名可绕过 ESM 模块缓存」这一能力：实现改在 plugin.js，入口 URL 不变。
//   ⚠️ 换成新文件名后，profile 里的挂载路径要同步改（或保持指向本转接层）。

export { name, inject, apply, preview } from './plugin.js'
