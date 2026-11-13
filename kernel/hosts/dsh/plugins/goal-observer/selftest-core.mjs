// GENERATED-BY: portable synchronization (DSH agent, 2026-11-13 +08:00)
// LAST-EDIT: portable synchronization (DSH agent, 2026-11-13 +08:00)
// PURPOSE: 观察者纯函数核心的离线自检 —— 配置收口、候选 JSON 解析（含旧实现的「首个候选缺字段就放弃」回归）、
//          历史/输入组装、通知 argv 与理由单行化。不需要宿主、不联网、不花 token。
// ORIGIN: none
// CUMULATIVE-DELTA: 首版。刻意把部署模块的解析 bug 固化成一条回归：第一个候选能解析但缺 instruction 时，
//          必须继续尝试后面的完整候选，而不是立刻报错。

import { strict as assert } from 'node:assert'
import {
  DEFAULT_POLICY,
  buildObserverInput,
  clip,
  formatHistory,
  jsonCandidates,
  normalizeNotification,
  normalizeSettings,
  notificationArgv,
  parseDecision,
  retryFeedback,
  sanitizeReason,
  textOf,
} from './observer-core.mjs'

let passed = 0
const failures = []

function check(name, fn) {
  try {
    fn()
    passed++
  } catch (error) {
    failures.push(`${name}: ${error.message}`)
  }
}

// ── 配置收口 ────────────────────────────────────────────────────────────
check('缺省配置 = 关闭；enabled 必须显式 true', () => {
  assert.equal(normalizeSettings(undefined).enabled, false)
  assert.equal(normalizeSettings({}).enabled, false)
  assert.equal(normalizeSettings({ enabled: 'true' }).enabled, false)
  assert.equal(normalizeSettings({ enabled: false }).enabled, false)
  assert.equal(normalizeSettings({ enabled: true }).enabled, true)
})

check('默认值：前缀/超时/预算/上下文', () => {
  const config = normalizeSettings({ enabled: true })
  assert.equal(config.objectivePrefix, '[观察者]')
  assert.equal(config.timeoutMs, 60000)
  assert.equal(config.maxTokens, 1800)
  assert.equal(config.contextChars, 24000)
  assert.equal(config.provider, undefined)
  assert.equal(config.model, undefined)
  assert.equal(config.policy, '')
})

check('数值越界回落默认，合法值保留', () => {
  const clamped = normalizeSettings({ enabled: true, timeoutMs: 1, maxTokens: 999999, contextChars: 10 })
  assert.equal(clamped.timeoutMs, 60000)
  assert.equal(clamped.maxTokens, 1800)
  assert.equal(clamped.contextChars, 24000)
  const kept = normalizeSettings({ enabled: true, timeoutMs: 240000, maxTokens: 16000, contextChars: 50000 })
  assert.equal(kept.timeoutMs, 240000)
  assert.equal(kept.maxTokens, 16000)
  assert.equal(kept.contextChars, 50000)
})

check('provider/model 只给一个 ⇒ 抛错，不静默用错模型', () => {
  assert.throws(() => normalizeSettings({ enabled: true, provider: 'p' }), /provider 与 model 必须同时/)
  assert.throws(() => normalizeSettings({ enabled: true, model: 'm' }), /provider 与 model 必须同时/)
  const both = normalizeSettings({ enabled: true, provider: 'p', model: 'm' })
  assert.equal(both.provider, 'p')
  assert.equal(both.model, 'm')
})

check('通知默认关闭；即使传了 command 也不生效', () => {
  const notification = normalizeNotification({ command: ['x'] })
  assert.deepEqual(notification, { enabled: false, command: [], cwd: null, throttleMs: 300000 })
  assert.deepEqual(normalizeSettings({ enabled: true }).notification.enabled, false)
})

check('通知显式开启时 command 必须是非空字符串数组', () => {
  assert.throws(() => normalizeNotification({ enabled: true }), /notification\.command/)
  assert.throws(() => normalizeNotification({ enabled: true, command: [] }), /notification\.command/)
  assert.throws(() => normalizeNotification({ enabled: true, command: [''] }), /notification\.command/)
  assert.throws(() => normalizeNotification({ enabled: true, command: [1, 2] }), /notification\.command/)
  const ok = normalizeNotification({ enabled: true, command: ['notifier', '--flag'], cwd: 'C:/tmp', throttleMs: 1000 })
  assert.deepEqual(ok, { enabled: true, command: ['notifier', '--flag'], cwd: 'C:/tmp', throttleMs: 1000 })
})

check('通知 throttleMs 非法值回落默认，0 保留（= 只按 reason 去重）', () => {
  assert.equal(normalizeNotification({ enabled: true, command: ['x'], throttleMs: -1 }).throttleMs, 300000)
  assert.equal(normalizeNotification({ enabled: true, command: ['x'], throttleMs: 0 }).throttleMs, 0)
})

// ── 解析 ────────────────────────────────────────────────────────────────
check('continue：instruction 必填，evidence 可省', () => {
  const decision = parseDecision('{"action":"continue","instruction":"核实 runner 存活"}')
  assert.deepEqual(decision, { action: 'continue', instruction: '核实 runner 存活', evidence: '' })
})

check('complete / pause：evidence 必填（完成需证据）', () => {
  assert.deepEqual(parseDecision('{"action":"complete","evidence":"三轮输出齐全"}'), {
    action: 'complete',
    instruction: '',
    evidence: '三轮输出齐全',
  })
  assert.deepEqual(parseDecision('{"action":"pause","evidence":"需要人付款"}'), {
    action: 'pause',
    instruction: '',
    evidence: '需要人付款',
  })
  assert.throws(() => parseDecision('{"action":"complete"}'), /evidence/)
  assert.throws(() => parseDecision('{"action":"complete","evidence":""}'), /证据/)
  assert.throws(() => parseDecision('{"action":"pause","evidence":123}'), /evidence/)
})

check('continue 缺 instruction / 空 instruction 都拒绝', () => {
  assert.throws(() => parseDecision('{"action":"continue"}'), /instruction/)
  assert.throws(() => parseDecision('{"action":"continue","instruction":"   "}'), /下一步/)
  assert.throws(() => parseDecision('{"action":"continue","instruction":123}'), /instruction/)
})

check('⭐ 回归：首个候选能解析但缺字段时，继续尝试后面的完整候选', () => {
  const raw = '{"action":"continue"}\n{"action":"continue","instruction":"第二步：跑验收"}'
  assert.deepEqual(parseDecision(raw), { action: 'continue', instruction: '第二步：跑验收', evidence: '' })
})

check('代码块围栏 / 前后废话 / 转义字符串都能容忍', () => {
  assert.equal(parseDecision('```json\n{"action":"continue","instruction":"a"}\n```').instruction, 'a')
  assert.equal(parseDecision('前缀 {"action":"continue","instruction":"含 \\"引号\\" 的步骤"} 后缀').instruction, '含 "引号" 的步骤')
  assert.equal(parseDecision('说明\n{"action":"complete","evidence":"证据 {不是对象} 但整体完整"}').action, 'complete')
})

check('候选数不放大：垃圾输出最多看 64 个 { 起点', () => {
  const raw = Array.from({ length: 200 }, () => '{').join('')
  assert.ok(jsonCandidates(raw).length <= 64 + 2)
})

check('空输出 / 非 JSON / 非法 action 都报错', () => {
  assert.throws(() => parseDecision(''), /为空/)
  assert.throws(() => parseDecision('   '), /为空/)
  assert.throws(() => parseDecision('not json at all'), /不是合法 JSON/)
  assert.throws(() => parseDecision('{"action":"stop","evidence":"x"}'), /action 无效/)
  // 数组里的单个对象会被「按 { 配平抠候选」捞出来：这是有意的宽容，不是把数组当对象。
  assert.equal(parseDecision('[{"action":"continue","instruction":"x"}]').instruction, 'x')
})

check('超长 instruction/evidence 截断保留，不抛错', () => {
  const instruction = 'x'.repeat(5000)
  const decision = parseDecision(JSON.stringify({ action: 'continue', instruction }))
  assert.ok(decision.instruction.length < 5000)
  assert.match(decision.instruction, /\[截断/)
})

// ── 历史 / 输入 ────────────────────────────────────────────────────────
check('textOf 只取 text 块；reasoning 不混入', () => {
  assert.equal(textOf([{ type: 'reasoning', text: '想' }, { type: 'text', text: '甲' }, { type: 'text', text: '乙' }]), '甲\n乙')
  assert.equal(textOf(undefined), '')
})

check('formatHistory 从最新往回取，并按 contextChars 截断', () => {
  const events = [
    { seq: 1, type: 'user/message', data: { source: { kind: 'user-input' }, content: [{ type: 'text', text: '最早' }] } },
    { seq: 2, type: 'assistant/message', data: { message: { content: [{ type: 'text', text: '中间' }] } } },
    { seq: 3, type: 'tool/call', data: { name: 'bash', arguments: { command: 'ls' } } },
    { seq: 4, type: 'turn/end', data: {} },
  ]
  const history = formatHistory(events, 10000)
  assert.ok(history.indexOf('"seq":4') < 0)
  assert.ok(history.indexOf('"seq":1') < history.indexOf('"seq":2'))
  assert.match(history, /bash/)
  const clipped = formatHistory(events, 40)
  assert.ok(clipped.length <= 40 + 10, `截断后仍应受控：${clipped.length}`)
})

check('buildObserverInput：goal 带 proposedRound；pending 排除 goal 轮本身', () => {
  const input = JSON.parse(buildObserverInput({
    goal: { id: 'g', revision: 2, objective: '[观察者] x', phase: 'active', roundsStarted: 2 },
    round: 3,
    messages: [
      { source: { kind: 'goal', round: 3 }, content: [{ type: 'text', text: '轮正文' }] },
      { source: { kind: 'user-input' }, content: [{ type: 'text', text: '真人插话' }] },
    ],
    events: [],
    contextChars: 2000,
  }))
  assert.equal(input.goal.proposedRound, 3)
  assert.deepEqual(input.pending, [{ source: { kind: 'user-input' }, text: '真人插话' }])
})

check('retryFeedback 带失败原因与上次输出片段', () => {
  const feedback = retryFeedback('JSON 没写完', '{"action":"cont')
  assert.match(feedback, /JSON 没写完/)
  assert.match(feedback, /上一次输出/)
  assert.match(feedback, /闭合的 }/)
})

// ── 通知参数 ────────────────────────────────────────────────────────────
check('理由单行化：换行/制表→空格、连续空白合并、超长截断', () => {
  assert.equal(sanitizeReason('需要人决定：\r\n付款\t并确认'), '需要人决定： 付款 并确认')
  assert.equal(sanitizeReason('  a   b  '), 'a b')
  const long = sanitizeReason('x'.repeat(600))
  assert.ok(long.length <= 501)
  assert.match(long, /…$/)
})

check('通知 argv = 固定前缀 + 单个 reason；不拼 shell 字符串', () => {
  const argv = notificationArgv(['notifier', '--flag', '值 有空格'], '需要人决定：付款 & 重启')
  assert.equal(argv.length, 4)
  assert.equal(argv[0], 'notifier')
  assert.equal(argv[1], '--flag')
  assert.equal(argv[2], '值 有空格')
  assert.equal(argv[3], '需要人决定：付款 & 重启')
  assert.equal(notificationArgv([], '') [0], '（观察者未给出理由）')
})

check('系统提示词含「不许输出 pause / 不替用户作答」，且不含私有通道字样', () => {
  assert.match(DEFAULT_POLICY, /不许输出 pause/)
  assert.match(DEFAULT_POLICY, /不许替用户作答/)
  assert.doesNotMatch(DEFAULT_POLICY, /聊天 ID|聊天 ID|飞书|feishu|lark/i)
})

check('clip 保留前半段并标注截断字数', () => {
  assert.equal(clip('abcd', 2), 'ab…[截断2字]')
  assert.equal(clip('abcd', 10), 'abcd')
})

// ── 结果 ────────────────────────────────────────────────────────────────
if (failures.length > 0) {
  console.error(`FAIL: ${failures.length} 条未通过（通过 ${passed}）`)
  for (const failure of failures) console.error(`  ✗ ${failure}`)
  process.exit(1)
}
console.log(`PASS: ${passed} 条纯函数自检全通过（配置 · 解析回归 · 历史/输入 · 通知参数）`)
