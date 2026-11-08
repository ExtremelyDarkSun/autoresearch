#!/usr/bin/env node
// GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
// LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
// PURPOSE: 纯函数层离线自检（不需要宿主、不联网、不花 token）：配置收口 · 读队列 · 新旧判定 · 消息排版 ·
//          项目过滤 · 挑会话 · 挪文件 · 回执。
// ORIGIN: install/autodl-events/selftest-core.mjs（夹具改中性；期望值改为 hold 默认 / 无内置项目路径）
// CUMULATIVE-DELTA: 首版。⛔ 不含任何实例项目路径与机器名；新增三条出厂默认断言：
//   `projects` 空 · `fallback='hold'` · `maxTargets=1`；并断言文案里**没有项目命令**。
//
// 用法：node selftest-core.mjs

import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import {
  DEFAULTS,
  appendLog,
  candidates,
  eventSummary,
  formatMessage,
  isStale,
  listEvents,
  makeUserMessage,
  matchesProject,
  moveEvent,
  pickAgents,
  pickSummary,
  readTargets,
  resolveConfig,
} from './lib/plugin-core.mjs'

let passed = 0
const failures = []

function chk(cond, label) {
  if (cond) {
    passed += 1
    console.log(`  ok  ${label}`)
  } else {
    failures.push(label)
    console.log(`  FAIL ${label}`)
  }
}

const nowIso = new Date().toISOString()
const suite = mkdtempSync(join(tmpdir(), 'autodl-events-core-'))
const queue = join(suite, 'pending')
mkdirSync(queue, { recursive: true })

const sample = {
  id: '20260101T000000-m1-verdict',
  machine: 'm-01',
  triggers: ['verdict'],
  from: { verdict: '可能开不了', idle: 0, status: '已关机' },
  to: { verdict: '可能可开', idle: 2, status: '已关机' },
  text: 'm-01 的开机可用性变成了「可能可开」，现在看起来能开了（时间由后端填入）',
  at: nowIso,
}
writeFileSync(join(queue, 'a.json'), JSON.stringify(sample), 'utf8')
writeFileSync(join(queue, 'b.json'), '{ not json', 'utf8')
writeFileSync(join(queue, 'c.json'), JSON.stringify({ id: 'x' }), 'utf8') // 没有 machine
writeFileSync(join(queue, 'd.txt'), 'ignored', 'utf8')

console.log('① 出厂默认：不猜项目、不广播、没有绑定就 hold')
chk(Array.isArray(DEFAULTS.projects) && DEFAULTS.projects.length === 0, 'DEFAULTS.projects 为空（⛔ 不内置任何项目路径）')
chk(DEFAULTS.fallback === 'hold', "DEFAULTS.fallback === 'hold'")
chk(DEFAULTS.maxTargets === 1, 'DEFAULTS.maxTargets === 1')
chk(DEFAULTS.eventDir === '', 'DEFAULTS.eventDir 为空（必填，插件不猜）')
const bare = resolveConfig({})
chk(bare.fallback === 'hold' && bare.maxTargets === 1, '空配置仍是 hold / 1 个目标')
chk(matchesProject('/any/where', bare.projects) === true, '空 projects = 不限项目')

console.log('② 配置收口')
const cfg = resolveConfig({ eventDir: queue, pollMs: 10, maxAgeMinutes: 0, maxEventsPerMessage: 99, delivery: 'NOPE', fallback: 'nope' })
chk(cfg.pollMs === 1000, 'pollMs 低于 1000 被抬到 1000')
chk(cfg.maxAgeMinutes === 1, 'maxAgeMinutes 低于 1 被抬到 1')
chk(cfg.maxEventsPerMessage === 20, 'maxEventsPerMessage 封顶 20')
chk(cfg.delivery === DEFAULTS.delivery, `非法 delivery 退回默认（${DEFAULTS.delivery}）`)
chk(cfg.fallback === 'hold', "非法 fallback 退回 'hold'（⛔ 不退回 pick）")
chk(cfg.deliveredDir.endsWith('delivered'), 'deliveredDir 默认按 eventDir 推出来')
chk(resolveConfig({ projects: '/srv/proj' }).projects[0] === '/srv/proj', 'projects 允许写成字符串')
chk(resolveConfig({ reviewHint: '自定义复核提示。' }).reviewHint === '自定义复核提示。', 'reviewHint 可由配置覆盖')

console.log('③ 读队列：坏文件与缺字段的跳过，非 json 忽略')
const listed = listEvents(queue)
chk(listed.events.length === 1 && listed.events[0].event.machine === 'm-01', '只读回 1 条合法事件')
chk(listed.bad.length === 2, `坏文件 2 个（实际 ${listed.bad.length}）`)
chk(listEvents(join(suite, 'nope')).events.length === 0, '目录不存在时返回空而不是抛')

console.log('④ 新旧判定')
chk(isStale(sample, Date.now(), 45 * 60_000) === false, '刚写的事件不算旧')
chk(isStale({ at: new Date(Date.now() - 46 * 60_000).toISOString() }, Date.now(), 45 * 60_000) === true, '46 分钟前算旧')
chk(isStale({ at: 'not-a-date' }, Date.now(), 60_000) === true, '时间解析不出来按旧处理')
chk(isStale({}, Date.now(), 60_000) === true, '没有 at 按旧处理')

console.log('⑤ 消息排版：⛔ 不带项目命令')
const one = formatMessage([sample], cfg)
chk(one.startsWith('[机器状态事件] m-01'), '单条消息以「[机器状态事件] 机器名」开头')
chk(one.includes('m-01 的开机可用性变成了'), '带上后端生成的正文')
chk(one.includes('不是用户说的话'), '明说这不是用户说的话')
chk(one.includes('没有试开机'), '明说没试开机')
// 只检查「有没有命令 / 路径」这类会泄露部署细节的形态，⛔ 不在这里写任何具体项目名。
const COMMAND_OR_PATH = /(?:^|\s)(?:python|pythonw|pwsh|powershell|cmd|node|bash)(?:\s|$)|\.py\b|[A-Za-z]:[\\/]|[\\/]/
chk(!COMMAND_OR_PATH.test(one), '⛔ 文案里不含任何命令或路径（部署细节不进事件正文）')
const custom = formatMessage([sample], { ...cfg, reviewHint: '自定义复核提示。' })
chk(custom.trimEnd().endsWith('自定义复核提示。'), 'reviewHint 替换了末尾提示')
const two = formatMessage([sample, { ...sample, machine: 'm-02', text: 'm-02 的正文' }], cfg)
chk(two.includes('2 台机器') && two.includes('m-02'), '两条时标题带台数与机器名')
chk(two.includes('第 2 条'), '多条时分段编号')
chk(eventSummary({}) === '某台机器 的开机可用性变成了「可用」。', '没有 text 时有兜底摘要')

console.log('⑥ 项目过滤（规范化路径前缀，不是子串）')
const PROJECT = '/srv/proj'
chk(matchesProject(PROJECT, [PROJECT]) === true, '完全相等算命中')
chk(matchesProject(`${PROJECT}/sub`, [PROJECT]) === true, '子目录算命中')
chk(matchesProject('C:\\Work\\Proj', ['c:/work/proj']) === true, '大小写与斜杠方向不影响')
chk(matchesProject('/tmp/clone/proj', [PROJECT]) === false, '⛔ 路径里含同名片段但前缀不同的另一份克隆不算命中')
chk(matchesProject('/srv/project-other', [PROJECT]) === false, '前缀不是父目录边界时不算命中')
chk(matchesProject('D:\\other', [PROJECT]) === false, '不命中项目')
chk(matchesProject('', [PROJECT]) === false, '没有 cwd 不算命中')
chk(matchesProject('D:\\other', []) === true, '空 projects = 不限项目')

console.log('⑦ 挑会话：只挑能 steer 且 cwd 命中的')
const pickCfg = { ...cfg, projects: ['/srv/proj'] }
const steerable = { session: { id: 's1', header: { cwd: '/srv/proj' }, seq: 50 }, steer() {} }
const noSteer = { session: { id: 's2', header: { cwd: '/srv/proj' } } }
const otherCwd = { session: { id: 's3', header: { cwd: '/srv/other' } }, steer() {} }
chk(pickAgents([steerable, noSteer, otherCwd], pickCfg, { sessions: ['s1'] }).length === 1, '绑定名单命中时挑到 s1')
chk(pickAgents(null, pickCfg).length === 0, 'roots 为空不抛')

console.log('⑦b 挑靶规则：hold 默认 · 绑定优先 · running 优先 · seq 最大 · maxTargets 封顶')
const big = { session: { id: 'big', header: { cwd: '/srv/proj' }, seq: 900 }, steer() {} }
const small = { session: { id: 'small', header: { cwd: '/srv/proj' }, seq: 3 }, steer() {} }
const busy = { session: { id: 'busy', header: { cwd: '/srv/proj' }, seq: 1 }, status: 'running', steer() {} }
chk(pickAgents([small, big], pickCfg).length === 0, "⛔ 没有绑定名单 + 出厂 hold ⇒ 一条都不投")
chk(pickAgents([small, big], pickCfg, { sessions: ['nope'] }).length === 0,
  '绑定的人不在场 + hold ⇒ 一条都不投')
chk(pickAgents([small, big], { ...pickCfg, fallback: 'pick' })[0].session.id === 'big',
  'fallback=pick 时挑事件条数最多的（big）')
chk(pickAgents([small, big], { ...pickCfg, fallback: 'pick' }, { sessions: ['nope'] }).length === 1,
  '绑定的人不在场 + pick ⇒ 退回启发式（不静默死掉）')
chk(pickAgents([big, busy], { ...pickCfg, fallback: 'pick' })[0].session.id === 'busy', '有 running 时优先挑 running')
chk(pickAgents([small, big], { ...pickCfg, fallback: 'pick', maxTargets: 2 }).length === 2, 'maxTargets=2 时挑 2 个')
chk(pickAgents([small, big], pickCfg, { sessions: ['small'] })[0].session.id === 'small', '绑定名单压过启发式')
chk(pickAgents([small, big], { ...pickCfg, maxTargets: 2 }, { sessions: ['big', 'small'] })
  .map((a) => a.session.id).join(',') === 'big,small', '多绑定：按名单顺序取，不按 roots 顺序')
chk(pickAgents([small, big], pickCfg, { sessions: ['big', 'small'] })[0].session.id === 'big',
  '只取 1 个时取名单里第一个')

console.log('⑦c 读绑定名单文件：判出来的 / 人手写的 / 坏的')
const targetsPath = join(suite, 'targets.json')
writeFileSync(targetsPath, JSON.stringify({ sessions: ['a', ' b '], authoritative: true, source: 'backend' }), 'utf8')
const t1 = readTargets(targetsPath)
chk(t1.sessions.join(',') === 'a,b', '去空白后读回 2 个 id')
chk(t1.authoritative === true && t1.source === 'backend', '认得出这是判出来的名单')
writeFileSync(targetsPath, JSON.stringify({ sessions: ['c'] }), 'utf8')
chk(readTargets(targetsPath).authoritative === false, '人手写的名单 authoritative=false')
chk(readTargets(join(suite, 'nope.json')).sessions.length === 0, '文件不存在返回空')
writeFileSync(targetsPath, '{ bad', 'utf8')
chk(readTargets(targetsPath).sessions.length === 0, '坏 JSON 返回空且不抛')

console.log('⑦d 挑靶摘要：谁在场、投给了谁、有没有退回')
const summary = pickSummary([small, big], pickCfg, { sessions: ['nope'] }, [big])
chk(summary.candidates === 2 && summary.bound === 1 && summary.boundLive === 0, '摘要记下候选/绑定/在场数')
chk(summary.fellBack === true, '投了非绑定会话 ⇒ fellBack=true')
chk(pickSummary([small], pickCfg, null, []).fellBack === false, '什么都没投时不算退回')

console.log('⑧ 消息对象形状')
const message = makeUserMessage('hello')
chk(message.role === 'user' && message.content[0].text === 'hello', 'role=user 且正文原样')
chk(message.source.kind === 'autodl-events', 'source.kind=autodl-events')
chk(Object.isFrozen(message), '消息被冻结')
chk(typeof message.id === 'string' && message.id.length > 10, '带唯一 id')

console.log('⑨ 挪文件与回执')
const delivered = join(suite, 'delivered')
chk(moveEvent(join(queue, 'a.json'), delivered) === true, '挪进 delivered 成功')
chk(readFileSync(join(delivered, 'a.json'), 'utf8').includes('m-01'), '挪过去的文件内容没变')
chk(moveEvent(join(queue, 'missing.json'), delivered) === false, '挪不存在的文件返回 false 而不抛')
const logFile = join(suite, 'logs', 'delivered.jsonl')
chk(appendLog(logFile, { at: nowIso, machines: ['m-01'] }) === true, '回执能写')
chk(appendLog('', {}) === false, 'logFile 为空时不写也不抛')
chk(candidates([steerable, noSteer], pickCfg).length === 1, 'candidates 只留能 steer 的')

rmSync(suite, { recursive: true, force: true })

console.log()
if (failures.length > 0) {
  console.log(`FAIL: ${failures.length} 条不通过（通过 ${passed} 条）`)
  for (const item of failures) console.log(`  - ${item}`)
  process.exit(1)
}
console.log(`PASS: autodl-events 纯函数层自检 ${passed} 条全通过`)
