#!/usr/bin/env node
/**
 * GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
 * LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
 * PURPOSE: 接线层离线自检：用假 ctx 驱动 `apply()`，不需要宿主、不联网。
 * ORIGIN: install/autodl-events/selftest-plugin.mjs（夹具改中性；出厂值改为 hold / 1 目标）
 * CUMULATIVE-DELTA: 首版。覆盖：挂上即投递（显式 pick 或显式绑定）· 出厂 hold 不投 · 无会话留队列 ·
 *   太旧进 expired · 坏文件被删 · 禁用/缺目录不投 · cwd 前缀隔离 · 只叫醒 1 个 · 每批条数上限 ·
 *   绑定优先 · inject 模式 · 释放后定时器停（反向对照）。
 *
 * 用法：node selftest-plugin.mjs
 */

import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { apply } from './lib/index.js'

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

const PROJECT_CWD = '/srv/proj'

function fakeAgent(id, cwd = PROJECT_CWD) {
  const got = { steered: [], injected: [] }
  return {
    session: { id, header: { cwd } },
    steer(message) { got.steered.push(message) },
    inject(message) { got.injected.push(message) },
    got,
  }
}

function fakeCtx(roots) {
  const disposers = []
  const logs = []
  const ctx = {
    logger: {
      info: (m) => logs.push(m),
      warn: (m) => logs.push(m),
      debug: () => {},
    },
    get: (name) => (name === 'agents' ? { roots: () => roots } : undefined),
    effect: (fn) => {
      const disposer = fn()
      if (typeof disposer === 'function') disposers.push(disposer)
      return disposer
    },
  }
  return { ctx, logs, dispose: () => disposers.forEach((d) => d()) }
}

function suite() {
  const dir = mkdtempSync(join(tmpdir(), 'autodl-events-plugin-'))
  const queue = join(dir, 'pending')
  mkdirSync(queue, { recursive: true })
  return {
    dir,
    queue,
    config: {
      eventDir: queue,
      deliveredDir: join(dir, 'delivered'),
      expiredDir: join(dir, 'expired'),
      logFile: join(dir, 'delivered.jsonl'),
      projects: [PROJECT_CWD],
      pollMs: 1000,
      maxAgeMinutes: 45,
      // 出厂是 hold；这些用例要测投递，所以显式给绑定或显式 pick。
      fallback: 'pick',
    },
    cleanup: () => rmSync(dir, { recursive: true, force: true }),
  }
}

function writeEvent(queue, name, overrides = {}) {
  const event = {
    id: name.replace(/\.json$/, ''),
    machine: 'm-01',
    triggers: ['verdict'],
    at: new Date().toISOString(),
    text: 'm-01 的开机可用性变成了「可能可开」，现在看起来能开了（测试）',
    ...overrides,
  }
  writeFileSync(join(queue, name), JSON.stringify(event), 'utf8')
  return event
}

function readLog(file) {
  return existsSync(file)
    ? readFileSync(file, 'utf8').trim().split('\n').filter(Boolean).map((line) => JSON.parse(line))
    : []
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function main() {
  console.log('① 挂上就投递：一条事件 → steer 一条消息 → 文件挪进 delivered')
  {
    const s = suite()
    writeEvent(s.queue, 'a.json')
    const agent = fakeAgent('s1')
    const { ctx, dispose } = fakeCtx([agent])
    apply(ctx, s.config)
    chk(agent.got.steered.length === 1, `steer 被调用 1 次（实际 ${agent.got.steered.length}）`)
    const text = agent.got.steered[0]?.content?.[0]?.text ?? ''
    chk(text.includes('m-01'), '消息里点名了机器')
    chk(text.includes('机器状态事件'), '消息带固定前缀')
    // 只检查「命令 / 路径」这类部署细节有没有漏进文案，⛔ 不写具体项目名。
    chk(!/(?:^|\s)(?:python|pythonw|pwsh|powershell|cmd|node|bash)(?:\s|$)|\.py\b|[A-Za-z]:[\\/]|[\\/]/.test(text),
      '⛔ 文案里不含命令或路径')
    chk(agent.got.steered[0]?.source?.kind === 'autodl-events', 'source.kind=autodl-events')
    chk(existsSync(join(s.config.deliveredDir, 'a.json')), '文件挪进 delivered/')
    chk(!existsSync(join(s.queue, 'a.json')), '原文件已离开队列')
    const log = readLog(s.config.logFile)
    const deliveredLines = log.filter((row) => row.kind === 'delivered')
    chk(deliveredLines.length === 1 && deliveredLines[0].machines[0] === 'm-01',
      `回执写了 1 行 delivered 且带机器名（实际 ${deliveredLines.length} 行）`)
    chk(log.some((row) => row.kind === 'apply'), 'apply 也留了一行（重启后能确认插件挂上）')
    dispose()
    s.cleanup()
  }

  console.log('② ⭐ 出厂默认（hold + 无绑定）：不投递，事件留在队列，回执写 waiting')
  {
    const s = suite()
    writeEvent(s.queue, 'b0.json')
    const agent = fakeAgent('s1')
    const { ctx, dispose } = fakeCtx([agent])
    apply(ctx, { ...s.config, fallback: undefined })
    chk(agent.got.steered.length === 0, '没有绑定 ⇒ 一条都不投')
    chk(existsSync(join(s.queue, 'b0.json')), '事件留在队列里等绑定出现')
    const log = readLog(s.config.logFile)
    const waiting = log.find((row) => row.kind === 'waiting')
    chk(waiting && waiting.reason === 'no-bound-session', 'waiting 行写清是「没有绑定会话」')
    chk(waiting && waiting.fallback === 'hold', 'waiting 行记下 fallback=hold')
    dispose()
    s.cleanup()
  }

  console.log('③ 没有会话：不投递、挪走，事件留在队列')
  {
    const s = suite()
    writeEvent(s.queue, 'b.json')
    const { ctx, dispose } = fakeCtx([])
    apply(ctx, s.config)
    chk(existsSync(join(s.queue, 'b.json')), '事件仍在队列里等人')
    chk(!existsSync(join(s.config.deliveredDir, 'b.json')), '没有进 delivered')
    dispose()
    s.cleanup()
  }

  console.log('④ 太旧的事件：进 expired，⛔ 不叫醒会话')
  {
    const s = suite()
    writeEvent(s.queue, 'c.json', { at: new Date(Date.now() - 47 * 60_000).toISOString() })
    const agent = fakeAgent('s1')
    const { ctx, dispose } = fakeCtx([agent])
    apply(ctx, s.config)
    chk(agent.got.steered.length === 0, 'steer 一次都没调')
    chk(existsSync(join(s.config.expiredDir, 'c.json')), '文件挪进 expired/')
    dispose()
    s.cleanup()
  }

  console.log('⑤ 坏 JSON：删掉、不抛、不影响同批的好事件')
  {
    const s = suite()
    writeFileSync(join(s.queue, 'bad.json'), '{ nope', 'utf8')
    writeEvent(s.queue, 'good.json')
    const agent = fakeAgent('s1')
    const { ctx, dispose } = fakeCtx([agent])
    apply(ctx, s.config)
    chk(!existsSync(join(s.queue, 'bad.json')), '坏文件被删掉')
    chk(agent.got.steered.length === 1, '同批的好事件照样投递')
    dispose()
    s.cleanup()
  }

  console.log('⑥ 配置关掉 / 缺目录：彻底不动（反向对照）')
  {
    const s = suite()
    writeEvent(s.queue, 'd.json')
    const agent = fakeAgent('s1')
    const off = fakeCtx([agent])
    apply(off.ctx, { ...s.config, enabled: false })
    chk(agent.got.steered.length === 0 && existsSync(join(s.queue, 'd.json')), 'enabled:false 时一条不投、文件不动')
    off.dispose()

    const noDir = fakeCtx([agent])
    apply(noDir.ctx, { ...s.config, eventDir: '' })
    chk(agent.got.steered.length === 0, 'eventDir 空时一条不投')
    noDir.dispose()

    const on = fakeCtx([agent])
    apply(on.ctx, s.config)
    chk(agent.got.steered.length === 1, '同一条配置打开后确实会投（证明前面的 0 不是假绿）')
    on.dispose()
    s.cleanup()
  }

  console.log('⑦ cwd 不在项目里：不叫醒它')
  {
    const s = suite()
    writeEvent(s.queue, 'e.json')
    const outsider = fakeAgent('s9', '/srv/other-project')
    const { ctx, dispose } = fakeCtx([outsider])
    apply(ctx, s.config)
    chk(outsider.got.steered.length === 0, '别的项目的会话不被叫醒')
    chk(existsSync(join(s.queue, 'e.json')), '事件留在队列里')
    dispose()
    s.cleanup()
  }

  console.log('⑦b 同名克隆（前缀不同）：不叫醒它')
  {
    const s = suite()
    writeEvent(s.queue, 'e2.json')
    const clone = fakeAgent('s10', '/tmp/clone/proj')
    const { ctx, dispose } = fakeCtx([clone])
    apply(ctx, s.config)
    chk(clone.got.steered.length === 0, '⛔ 路径里含同名片段但前缀不同 ⇒ 不算命中')
    dispose()
    s.cleanup()
  }

  console.log('⑧ 两个命中会话：默认只叫醒 1 个（maxTargets=1）')
  {
    const s = suite()
    writeEvent(s.queue, 'f.json')
    const a1 = fakeAgent('s1')
    const a2 = fakeAgent('s2')
    const { ctx, dispose } = fakeCtx([a1, a2])
    apply(ctx, s.config)
    const got = a1.got.steered.length + a2.got.steered.length
    chk(got === 1, `默认只叫醒 1 个（实际 ${got}）`)
    dispose()
    s.cleanup()
  }

  console.log('⑧b maxTargets=2 时两个都收到，且消息 id 不同')
  {
    const s = suite()
    writeEvent(s.queue, 'f2.json')
    const a1 = fakeAgent('s1')
    const a2 = fakeAgent('s2')
    const { ctx, dispose } = fakeCtx([a1, a2])
    apply(ctx, { ...s.config, maxTargets: 2 })
    chk(a1.got.steered.length === 1 && a2.got.steered.length === 1, '两个会话各收到 1 条')
    chk(a1.got.steered[0].id !== a2.got.steered[0].id, '两条消息 id 不同（不是同一个对象）')
    dispose()
    s.cleanup()
  }

  console.log('⑧c 绑定名单：只投绑定那个；绑定的不在场时按 fallback 决定')
  {
    const s = suite()
    const targetsFile = join(s.dir, 'targets.json')
    writeFileSync(targetsFile, JSON.stringify({ sessions: ['s2'], authoritative: true, source: 'backend' }), 'utf8')
    writeEvent(s.queue, 'f3.json')
    const a1 = fakeAgent('s1')
    const a2 = fakeAgent('s2')
    const { ctx, dispose } = fakeCtx([a1, a2])
    apply(ctx, { ...s.config, targetsFile })
    chk(a1.got.steered.length === 0 && a2.got.steered.length === 1, '只投绑定名单里的 s2')
    dispose()
    s.cleanup()

    // 出厂 hold：绑定的不在场 ⇒ 一条都不投，事件留队列
    const s2 = suite()
    writeFileSync(join(s2.dir, 'targets.json'), JSON.stringify({ sessions: ['s9'], authoritative: true }), 'utf8')
    writeEvent(s2.queue, 'f6.json')
    const c1 = fakeAgent('s1')
    const { ctx: ctx2, dispose: dispose2 } = fakeCtx([c1])
    apply(ctx2, { ...s2.config, fallback: 'hold', targetsFile: join(s2.dir, 'targets.json') })
    chk(c1.got.steered.length === 0, 'fallback=hold 时一条都不投')
    chk(existsSync(join(s2.queue, 'f6.json')), '事件留在队列里等绑定的那个会话回来')
    const waiting = readLog(s2.config.logFile).find((row) => row.kind === 'waiting')
    chk(waiting && waiting.reason === 'bound-session-not-live', 'waiting 行写清是「绑定的会话不在场」')
    dispose2()
    s2.cleanup()

    // 显式 pick：绑定的不在场 ⇒ 退回启发式，回执里记 fellBack
    const s3 = suite()
    writeFileSync(join(s3.dir, 'targets.json'), JSON.stringify({ sessions: ['s9'], authoritative: true }), 'utf8')
    writeEvent(s3.queue, 'f4.json')
    const b1 = fakeAgent('s1')
    const { ctx: ctx3, dispose: dispose3 } = fakeCtx([b1])
    apply(ctx3, { ...s3.config, fallback: 'pick', targetsFile: join(s3.dir, 'targets.json') })
    chk(b1.got.steered.length === 1, '绑定的不在场 + 显式 pick ⇒ 退回启发式，投给 s1')
    const delivered = readLog(s3.config.logFile).find((row) => row.kind === 'delivered')
    chk(delivered && delivered.fellBack === true && delivered.bound === 1, '回执记下了「绑了 1 个但全不在场、已退回」')
    dispose3()
    s3.cleanup()
  }

  console.log('⑧d 优先正在跑的会话（status=running）')
  {
    const s = suite()
    writeEvent(s.queue, 'f5.json')
    const idle = fakeAgent('s-idle')
    const busy = fakeAgent('s-busy')
    busy.status = 'running'
    const { ctx, dispose } = fakeCtx([idle, busy])
    apply(ctx, s.config)
    chk(busy.got.steered.length === 1 && idle.got.steered.length === 0, '只有 running 的那个被叫醒')
    dispose()
    s.cleanup()
  }

  console.log('⑨ 每批条数上限：maxEventsPerMessage=1 时只送第一条，其余留队列')
  {
    const s = suite()
    writeEvent(s.queue, 'g1.json', { machine: 'm-01' })
    writeEvent(s.queue, 'g2.json', { machine: 'm-02' })
    const agent = fakeAgent('s1')
    const { ctx, dispose } = fakeCtx([agent])
    apply(ctx, { ...s.config, maxEventsPerMessage: 1 })
    chk(agent.got.steered.length === 1, '只送 1 条消息')
    const text = agent.got.steered[0].content[0].text
    chk(text.includes('m-01') && !text.includes('m-02'), '这一条只讲 m-01')
    chk(existsSync(join(s.queue, 'g2.json')), '第二条还在队列里')
    dispose()
    s.cleanup()
  }

  console.log('⑩ inject 模式：调 inject，不调 steer')
  {
    const s = suite()
    writeEvent(s.queue, 'h.json')
    const agent = fakeAgent('s1')
    const { ctx, dispose } = fakeCtx([agent])
    apply(ctx, { ...s.config, delivery: 'inject' })
    chk(agent.got.injected.length === 1 && agent.got.steered.length === 0, 'inject 收到 1 条、steer 0 条')
    dispose()
    s.cleanup()
  }

  console.log('⑪ 反向对照：不释放就一直轮询；释放后不再投')
  {
    const s = suite()
    const agent = fakeAgent('s1')
    const live = fakeCtx([agent])
    apply(live.ctx, s.config)
    writeEvent(s.queue, 'i1.json')
    await sleep(1300)
    chk(agent.got.steered.length === 1, `定时器在岗时 1.3 秒内会自己投（实际 ${agent.got.steered.length}）`)

    live.dispose()
    writeEvent(s.queue, 'i2.json', { machine: 'm-02' })
    await sleep(1300)
    chk(agent.got.steered.length === 1, '释放后再放事件，不会再有新投递')
    chk(existsSync(join(s.queue, 'i2.json')) || existsSync(join(s.config.deliveredDir, 'i2.json')),
      '释放后事件既没被投也没丢（留在队列）')
    s.cleanup()
  }

  console.log()
  if (failures.length > 0) {
    console.log(`FAIL: ${failures.length} 条不通过（通过 ${passed} 条）`)
    for (const item of failures) console.log(`  - ${item}`)
    process.exit(1)
  }
  console.log(`PASS: autodl-events 接线层自检 ${passed} 条全通过`)
}

main().catch((error) => {
  console.error('自检崩了：', error)
  process.exit(1)
})
