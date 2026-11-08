#!/usr/bin/env node
// GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
// LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
// PURPOSE: 只读预览 —— 不装插件、不开宿主，直接看「队列里的事件会变成哪条消息」。
// ORIGIN: install/autodl-events/dryrun.mjs（去实例路径：队列目录由参数/环境变量显式给）
// CUMULATIVE-DELTA: 首版。⛔ 不再用 `__file__/../..` 猜项目根；不给 --event-dir 就按「未执行」退出。
//   ⛔ 只读：不投递、不挪文件、不删文件。
//
// 用法：
//   node dryrun.mjs --event-dir <.../.autoresearch/events/pending> [--targets-file <.../targets.json>] [--review-hint "…"]
//   AUTORESEARCH_EVENT_DIR=<目录> node dryrun.mjs
// 退出码：0 = 预览完成（有没有事件都是 0）· 2 = 未执行（没给队列目录 / 目录读不到）

import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

import { preview } from './lib/index.js'

function parseArgs(argv) {
  const out = {}
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === '--event-dir') out.eventDir = argv[++i]
    else if (arg === '--targets-file') out.targetsFile = argv[++i]
    else if (arg === '--review-hint') out.reviewHint = argv[++i]
    else if (arg === '--help' || arg === '-h') out.help = true
    else out.unknown = arg
  }
  return out
}

const args = parseArgs(process.argv.slice(2))
if (args.help === true) {
  console.log('只读预览事件队列会被排成哪条消息。必须显式给 --event-dir（或环境变量 AUTORESEARCH_EVENT_DIR）。')
  process.exit(0)
}
if (args.unknown !== undefined) {
  console.error(`未执行：不认识的参数 ${args.unknown}`)
  process.exit(2)
}
const eventDir = args.eventDir ?? process.env.AUTORESEARCH_EVENT_DIR ?? ''
if (!eventDir) {
  console.error('未执行：没有给 --event-dir，也没有设置 AUTORESEARCH_EVENT_DIR')
  process.exit(2)
}
const absolute = resolve(eventDir)
if (!existsSync(absolute)) {
  console.error(`未执行：队列目录不存在：${absolute}`)
  process.exit(2)
}

const result = preview(absolute, {
  eventDir: absolute,
  targetsFile: args.targetsFile,
  reviewHint: args.reviewHint,
  maxAgeMinutes: 45,
})

console.log(`队列目录：${absolute}`)
console.log(`文件总数：${result.total} · 还能投递：${result.fresh}`)
if (result.fresh === 0) {
  console.log('现在没有可投递的事件（正常：只有状态真的翻成「可用」时才会有一条）。')
  process.exit(0)
}
console.log('─'.repeat(70))
console.log(result.text)
