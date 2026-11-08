// GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
// LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
// PURPOSE: 离线自检 —— 纯函数层（切段 · 标题丢弃 · 行摘除 · 文件头剥离 · 重组）+ 默认策略与显式策略的对照。
// ORIGIN: install/agent-instruction-filter/selftest.mjs（期望值改为中性：默认不漏规则，显式 drop 单独测）
// CUMULATIVE-DELTA: 首版。夹具全部改成中性路径（`docs/AGENTS.md` 等），⛔ 不含任何实例项目名、会话 ID 或历史结论。
//   新增两条首要断言：① 出厂策略下文本**逐字不变**（默认不漏应有规则）；② 显式 drop 只影响命中的那一份。
//   ⚠️ 合成夹具只测逻辑；真实结构由 verify-real-policy.mjs 用部署方提供的文件清单把关。
//
// 用法：node selftest.mjs

import { strict as assert } from 'node:assert'
import {
  filterInstructionText,
  matchRule,
  dropHeadings,
  dropLines,
  stripLeadingComment,
  splitSections,
} from './filter-core.mjs'
import { policy, validatePolicy } from './policy.mjs'

let passed = 0
const failures = []

function check(name, fn) {
  try {
    fn()
    passed += 1
  } catch (error) {
    failures.push(`${name}: ${error.message}`)
  }
}

const OPEN = '<system-reminder>'
const CLOSE = '</system-reminder>'
const KEEP_ALL = { rules: [], defaultRule: { mode: 'keep' } }

const HEADER = ['<!--', 'GENERATED-BY: 某生成者', 'LAST-EDIT: 某生成者', 'PURPOSE: 说明', 'ORIGIN: none', 'CUMULATIVE-DELTA: 首版', '-->'].join('\n')

const GLOBAL_DOC = ['# 全局偏好', '', '- 用中文回复用户。'].join('\n')

const ROOT_DOC = [HEADER, '', '# 项目上下文', '', '> 动手第一件事：读规则文件。', '', '## 索引', '', '| 文档 | 内容 |', '|---|---|', '| `RULES.md` | 规则 |'].join('\n')

const SUB_DOC = [
  HEADER,
  '',
  '# 子系统规则',
  '',
  '## 跑评测（硬前置）',
  '',
  '> 开 GPU 之前必须先通读实验规则。',
  '',
  '**跑评测用远端**：`python tool/run_eval.py …`',
  '',
  '### CWD 与图工具',
  '',
  '进程 CWD 必须是仓库根。',
  '',
  '### 已实测无效的改动（负结果）',
  '',
  '| 改动 | 结果 |',
  '|---|---|',
  '| 加大调用上限 | 0 题受益 |',
].join('\n')

const OTHER_DOC = [HEADER, '', '# 另一条线', '', '## 数值在哪', '', '引用前先回读那个文件。'].join('\n')

const BASELINE = [
  OPEN,
  'The following workspace instructions may be relevant to your work.',
  '',
  'Instructions from: ~/global/AGENTS.md',
  '',
  GLOBAL_DOC,
  '',
  'Instructions from: AGENTS.md',
  '',
  ROOT_DOC,
  '',
  'Instructions from: sub\\AGENTS.md',
  '',
  SUB_DOC,
  '',
  'Instructions from: other\\AGENTS.md',
  '',
  OTHER_DOC,
  CLOSE,
].join('\n')

// ── ⭐ 首要：出厂策略不漏任何应有规则 ────────────────────────────────────
const untouched = filterInstructionText(BASELINE, policy)

check('⭐ 出厂策略下文本逐字不变（默认不漏规则）', () => {
  assert.equal(untouched.changed, false)
  assert.equal(untouched.text, BASELINE)
  assert.equal(untouched.empty, false)
})

check('出厂策略没有 drop 规则、默认 keep', () => {
  assert.deepEqual(policy.rules, [])
  assert.equal(policy.defaultRule.mode, 'keep')
  assert.equal(validatePolicy(policy).ok, true)
})

check('出厂策略下每一份注入都还在', () => {
  for (const marker of [
    'Instructions from: ~/global/AGENTS.md',
    'Instructions from: AGENTS.md',
    'Instructions from: sub\\AGENTS.md',
    'Instructions from: other\\AGENTS.md',
  ]) {
    assert.ok(untouched.text.includes(marker), `应保留：${marker}`)
  }
})

// ── 显式 drop 能力（不是默认）────────────────────────────────────────────
const EXPLICIT_DROP = {
  rules: [
    { match: '**/sub/AGENTS.md', mode: 'drop' },
    { match: '**/AGENTS.md', mode: 'keep' },
  ],
  defaultRule: { mode: 'keep' },
}
const dropped = filterInstructionText(BASELINE, EXPLICIT_DROP).text

check('显式 drop 只影响命中的那一份（含段标记与文件头一起消失）', () => {
  assert.ok(!dropped.includes('Instructions from: sub\\AGENTS.md'))
  assert.ok(!dropped.includes('# 子系统规则'))
  assert.ok(!dropped.includes('跑评测（硬前置）'))
  assert.ok(dropped.includes('Instructions from: AGENTS.md'))
  assert.ok(dropped.includes('Instructions from: other\\AGENTS.md'))
})

check('显式 drop 不是「按段裁」：整份（段标记 + 文件头 + 正文）一起没了', () => {
  const only = filterInstructionText(single('sub\\AGENTS.md', SUB_DOC), EXPLICIT_DROP)
  assert.equal(only.text, '', '单文件消息被整条丢弃')
  assert.equal(only.empty, true)
})

check('具体规则必须排在通配之前才生效', () => {
  const wrongOrder = {
    rules: [
      { match: '**/AGENTS.md', mode: 'keep' },
      { match: '**/sub/AGENTS.md', mode: 'drop' },
    ],
    defaultRule: { mode: 'keep' },
  }
  assert.equal(matchRule(wrongOrder, 'sub\\AGENTS.md').mode, 'keep')
  const rightOrder = {
    rules: [
      { match: '**/sub/AGENTS.md', mode: 'drop' },
      { match: '**/AGENTS.md', mode: 'keep' },
    ],
    defaultRule: { mode: 'keep' },
  }
  assert.equal(matchRule(rightOrder, 'sub\\AGENTS.md').mode, 'drop')
})

// ── 机制层：用合成策略 + 单文件消息单独测（不随出厂策略变化而失效）────────
function single(displayPath, doc) {
  return [OPEN, `Instructions from: ${displayPath}`, '', doc, CLOSE].join('\n')
}

const MECH = {
  rules: [
    { match: '**/global/AGENTS.md', mode: 'keep' },
    {
      match: '**/sub/AGENTS.md',
      mode: 'filter',
      stripLeadingComment: true,
      dropSections: ['CWD 与图工具'],
      dropLines: ['**跑评测用远端**'],
    },
    { match: '**/AGENTS.md', mode: 'keep' },
  ],
  defaultRule: { mode: 'keep' },
}
const mech = filterInstructionText(single('sub\\AGENTS.md', SUB_DOC), MECH).text

check('机制：stripLeadingComment 去掉该文件的文件头，别的段不受影响', () => {
  assert.ok(!mech.includes('GENERATED-BY:'), '该段文件头应被去掉')
  assert.ok(mech.includes('Instructions from: sub\\AGENTS.md'), '段标记还在')
  assert.ok(mech.includes('## 跑评测（硬前置）'), '没被点名的节应保留')
})

check('机制：dropSections 只丢命中标题的子树，同级标题终止丢弃', () => {
  assert.ok(!mech.includes('CWD 与图工具'), '该节应被丢')
  assert.ok(!mech.includes('进程 CWD 必须是仓库根。'), '它的正文应被丢')
  assert.ok(mech.includes('### 已实测无效的改动'), '下一个同级标题应保留')
})

check('机制：dropLines 只摘目标行，同段别的行保留', () => {
  assert.ok(!mech.includes('**跑评测用远端**'), '目标行应被丢')
  assert.ok(mech.includes('开 GPU 之前必须先通读'), '同段别的行应保留')
})

check('机制：只剩 intro、一段都不剩时，整条消息也被判为空', () => {
  const withIntro = [OPEN, 'The following workspace instructions may be relevant to your work.', '', 'Instructions from: X\\AGENTS.md', '', '# X', '', '内容', CLOSE].join('\n')
  const { text, empty } = filterInstructionText(withIntro, { rules: [{ match: '**/AGENTS.md', mode: 'drop' }], defaultRule: { mode: 'keep' } })
  assert.equal(empty, true, '不该留下一句没有内容的 intro')
  assert.equal(text, '')
})

// ── 反向对照 ────────────────────────────────────────────────────────────
check('反向：非 system-reminder 文本原样返回', () => {
  const plain = 'hello, 这不是注入文本'
  const { text, changed, empty } = filterInstructionText(plain, policy)
  assert.equal(text, plain)
  assert.equal(changed, false)
  assert.equal(empty, false)
})

check('反向：dropHeadings 丢到文末不留残段；同级标题能终止', () => {
  const doc = ['## 1 保留节', '', '留着', '', '## 2 丢弃节', '', '丢掉', '', '## 3 尾节', '', '尾'].join('\n')
  const kept = dropHeadings(doc, ['2 丢弃节'])
  assert.ok(kept.includes('留着'))
  assert.ok(!kept.includes('丢掉') && !kept.includes('2 丢弃节'))
  assert.ok(kept.includes('## 3 尾节') && kept.includes('尾'))
})

check('反向：dropLines 不误伤同表相邻行', () => {
  const table = ['| a | 1 |', '| b | 2 |', '| c | 3 |'].join('\n')
  const kept = dropLines(table, ['| b |'])
  assert.ok(!kept.includes('| b |'))
  assert.ok(kept.includes('| a |') && kept.includes('| c |'))
})

check('反向：stripLeadingComment 只吃开头那一块注释', () => {
  assert.equal(stripLeadingComment(`${HEADER}\n\n# 标题`), '\n# 标题')
  assert.equal(stripLeadingComment('# 没有注释头'), '# 没有注释头')
})

check('分段器认出四种段标记', () => {
  const { sections } = splitSections(['Instructions from: a.md', '', 'A', 'Updated instructions from: b.md', '', 'B', 'Instructions removed: c.md'].join('\n'))
  assert.equal(sections.length, 3)
  assert.deepEqual(sections.map((s) => s.path), ['a.md', 'b.md', 'c.md'])
})

// ── 策略校验 ────────────────────────────────────────────────────────────
check('策略校验：合法最小策略通过', () => {
  assert.equal(validatePolicy({ rules: [], defaultRule: { mode: 'keep' } }).ok, true)
  assert.equal(validatePolicy({ rules: [{ match: '**/x.md', mode: 'drop' }] }).ok, true)
})

check('策略校验：坏策略被拒（调用方据此回落 keep）', () => {
  assert.equal(validatePolicy(null).ok, false)
  assert.equal(validatePolicy({ rules: 'x' }).ok, false)
  assert.equal(validatePolicy({ rules: [{ mode: 'drop' }] }).ok, false)
  assert.equal(validatePolicy({ rules: [{ match: 'x', mode: 'nuke' }] }).ok, false)
})

// ── 结果 ────────────────────────────────────────────────────────────────
if (failures.length > 0) {
  console.error(`FAIL: ${failures.length} 条未通过（通过 ${passed}）`)
  for (const failure of failures) console.error('  ✗ ' + failure)
  process.exit(1)
}
console.log(`PASS: ${passed} 条全通过（默认不漏规则 · 显式 drop 单独验证）`)
