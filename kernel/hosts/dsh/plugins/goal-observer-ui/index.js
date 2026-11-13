// GENERATED-BY: portable synchronization (DSH agent, 2026-11-13 +08:00)
// LAST-EDIT: portable synchronization (DSH agent, 2026-11-13 +08:00)
// PURPOSE: 让宿主发现 goal-observer-ui 这个客户端包；宿主侧没有运行逻辑，也不改 goal/消息/运行状态。
// ORIGIN: install/dsh-goal-observer-ui/index.js（2026-11-13 读码提取）
// CUMULATIVE-DELTA: 首版。包名改为 @autoresearch/dsh-goal-observer-ui；入口保持 ESM 的空 apply()。

export const name = 'goal-observer-ui'

// 真正的开关是 Loader 的 disabled（见 README §4.1）：本文件不读 config，也没有能力关掉浏览器 bundle。
export function apply() {}
