/**
 * GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
 * LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
 * PURPOSE: dsh-doc-gap 的纯逻辑自测：不联网、不加载宿主、不花 token。
 * ORIGIN: install/dsh-doc-gap/selftest.mjs（夹具改中性；补「默认不启用 / 政策外置 / 超限诊断 / 前缀隔离」）
 * CUMULATIVE-DELTA: 首版。⛔ 不含任何实例项目名、文档绝对路径与历史读数。
 *
 * 用法：node selftest.mjs
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {
  DEFAULT_POLICY,
  applyTruncationGate,
  buildHint,
  buildJudgePrompt,
  buildJudgeSystem,
  docDiagnostics,
  extractJson,
  flattenJevAnswer,
  gateVerdict,
  loadDocs,
  matchesProject,
  normalizePath,
  normalizeVerdict,
  resolveConfig,
} from './docgap-core.mjs'

let passed = 0
const test = (label, fn) => {
  try {
    fn()
    passed += 1
    console.log(`  ok   ${label}`)
  } catch (error) {
    console.log(`  FAIL ${label}\n       ${error.message}`)
    process.exitCode = 1
  }
}

const DOCS = [
  { label: '设计说明', path: '/tmp/fixture/DESIGN.md', text: '# DESIGN' },
  { label: '格式约定', path: '/tmp/fixture/FORMAT.md', text: '# FORMAT' },
]

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'docgap-core-'))

console.log('resolveConfig')
test('出厂默认：不启用（模型调用只在显式启用后）', () => {
  const cfg = resolveConfig(undefined)
  assert.equal(cfg.enabled, false)
  assert.equal(cfg.threshold, 0.8)
  assert.equal(cfg.notify, 'hint')
  assert.equal(cfg.onlyRoot, true)
  assert.deepEqual(cfg.projects, [])
  assert.deepEqual(cfg.docs, [])
  assert.equal(cfg.judge.backend, 'session')
  assert.equal(cfg.judge.timeoutMs, 9000)
})
test('显式 enabled: true 才打开', () => {
  assert.equal(resolveConfig({ enabled: true }).enabled, true)
  assert.equal(resolveConfig({ enabled: 'yes' }).enabled, false, '只认严格 true')
})
test('深合并保留未覆盖的 judge 字段', () => {
  const cfg = resolveConfig({ judge: { backend: 'jev' }, threshold: 0.9 })
  assert.equal(cfg.judge.backend, 'jev')
  assert.equal(cfg.judge.timeoutMs, 9000)
  assert.equal(cfg.threshold, 0.9)
})
test('字符串文档归一成 {path,label}', () => {
  const cfg = resolveConfig({ docs: ['/tmp/fixture/DESIGN.md'] })
  assert.deepEqual(cfg.docs, [{ path: '/tmp/fixture/DESIGN.md', label: 'DESIGN.md' }])
})
test('越界阈值被夹住', () => {
  assert.equal(resolveConfig({ threshold: 5 }).threshold, 1)
  assert.equal(resolveConfig({ threshold: -2 }).threshold, 0)
  assert.equal(resolveConfig({ threshold: 'x' }).threshold, 0.8)
})
test('政策缺项回落到中性默认（不借用实例政策）', () => {
  const cfg = resolveConfig({ policy: { scope: { include: ['只有这一条'] } } })
  assert.deepEqual(cfg.policy.scope.include, ['只有这一条'])
  assert.deepEqual(cfg.policy.scope.exclude, DEFAULT_POLICY.scope.exclude)
  assert.equal(cfg.policy.criteria.covered, DEFAULT_POLICY.criteria.covered)
  assert.equal(cfg.policy.anchor, DEFAULT_POLICY.anchor)
})

console.log('项目范围：规范化路径前缀（不是子串）')
test('空白名单 = 不限项目', () => {
  assert.equal(matchesProject([], '/any/where'), true)
  assert.equal(matchesProject(undefined, undefined), true)
})
test('相等算命中', () => {
  assert.equal(matchesProject(['/srv/proj'], '/srv/proj'), true)
})
test('子目录算命中', () => {
  assert.equal(matchesProject(['/srv/proj'], '/srv/proj/sub/dir'), true)
})
test('大小写与斜杠方向不敏感', () => {
  assert.equal(matchesProject(['C:\\Work\\Proj'], 'c:/work/PROJ/Sub'), true)
})
test('⛔ 路径里恰好含同名片段但前缀不同的另一份克隆不算命中', () => {
  assert.equal(matchesProject(['/srv/proj'], '/tmp/clone/proj'), false)
  assert.equal(matchesProject(['C:\\a\\proj'], 'C:\\Temp\\proj'), false)
})
test('前缀不是父目录边界时不算命中', () => {
  assert.equal(matchesProject(['/srv/proj'], '/srv/project-other'), false)
})
test('尾部斜杠与 `.` 段被归一', () => {
  assert.equal(matchesProject(['/srv/proj/'], '/srv/proj'), true)
  assert.equal(matchesProject(['/srv/proj'], '/srv/./proj/./sub'), true)
  assert.equal(matchesProject(['/srv/proj'], '/srv/proj/../proj/sub'), true)
})
test('UNC 路径按同一规则比较', () => {
  assert.equal(matchesProject(['\\\\server\\share\\proj'], '\\\\SERVER\\Share\\Proj\\sub'), true)
  assert.equal(matchesProject(['\\\\server\\share\\proj'], '\\\\server\\share\\proj-other'), false)
})
test('真实目录边界：父目录命中、同前缀的兄弟目录不命中', () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'docgap-paths-'))
  const proj = path.join(base, 'proj')
  const sibling = path.join(base, 'proj-other')
  const child = path.join(proj, 'sub')
  fs.mkdirSync(child, { recursive: true })
  fs.mkdirSync(sibling, { recursive: true })
  assert.equal(matchesProject([proj], child), true, '子目录应命中')
  assert.equal(matchesProject([proj], sibling), false, '同前缀的兄弟目录不该命中')
  assert.equal(matchesProject([path.join(proj, '..', 'proj')], child), true, '含 .. 的等价路径应命中')
  fs.rmSync(base, { recursive: true, force: true })
})
test('拿不到 cwd 时返回 false（宁可不触发）', () => {
  assert.equal(matchesProject(['/srv/proj'], undefined), false)
  assert.equal(matchesProject(['/srv/proj'], ''), false)
})
test('配置里的 projects 被归一', () => {
  assert.deepEqual(resolveConfig({ projects: [' /srv/proj ', ''] }).projects, ['/srv/proj'])
  assert.deepEqual(resolveConfig({ projects: '/srv/proj' }).projects, ['/srv/proj'])
  assert.equal(normalizePath('C:\\A\\B\\'), 'c:/a/b')
})

console.log('extractJson')
test('裸 JSON', () => {
  assert.deepEqual(extractJson('{"a":1}'), { a: 1 })
})
test('代码块包裹 + 前后噪音', () => {
  assert.deepEqual(extractJson('好的：\n```json\n{"a":{"b":2}}\n```\n以上'), { a: { b: 2 } })
})
test('字符串里的花括号不干扰', () => {
  assert.deepEqual(extractJson('{"why":"写了 {x} 和 }"}, 后'), { why: '写了 {x} 和 }' })
})
test('没有 JSON 返回 null', () => {
  assert.equal(extractJson('我不确定'), null)
  assert.equal(extractJson(undefined), null)
})

console.log('normalizeVerdict')
test('正常 missing', () => {
  const v = normalizeVerdict({ point: '实验动机', coverage: 'missing', p_missing: 0.92, why: '文档无动机段' })
  assert.equal(v.coverage, 'missing')
  assert.equal(v.probability, 0.92)
  assert.equal(v.point, '实验动机')
  assert.equal(v.target, undefined, '落点不该由判定器给')
})
test('中文覆盖值也被接受', () => {
  assert.equal(normalizeVerdict({ coverage: '未覆盖', p_missing: 0.9 }).coverage, 'missing')
  assert.equal(normalizeVerdict({ coverage: '已覆盖', p_missing: 0.1 }).coverage, 'covered')
})
test('判定器就算多给了 target 也被忽略', () => {
  const v = normalizeVerdict({ coverage: 'partial', p_missing: 0.85, target: 'FORMAT.md' })
  assert.equal(v.coverage, 'partial')
  assert.equal(v.target, undefined)
})
test('非法输入返回 null', () => {
  assert.equal(normalizeVerdict(null), null)
  assert.equal(normalizeVerdict({ coverage: 'missing' }), null)
  assert.equal(normalizeVerdict({ p_missing: 0.9 }), null)
})

console.log('gateVerdict')
const verdict = (over) => normalizeVerdict({ coverage: 'missing', p_missing: 0.9, ...over })
test('命中', () => {
  assert.equal(gateVerdict(verdict({}), 0.8).hit, true)
})
test('covered 不提示', () => {
  assert.equal(gateVerdict(verdict({ coverage: 'covered', p_missing: 0.95 }), 0.8).reason, 'covered')
})
test('低于阈值不提示', () => {
  assert.equal(gateVerdict(verdict({ p_missing: 0.79 }), 0.8).reason, 'below-threshold')
})
test('partial 也能触发（只看概率）', () => {
  assert.equal(gateVerdict(verdict({ coverage: 'partial', p_missing: 0.88 }), 0.8).hit, true)
})
test('阈值边界含等号', () => {
  assert.equal(gateVerdict(verdict({ p_missing: 0.8 }), 0.8).hit, true)
})

console.log('提示词：范围、判据与锚点都由政策生成')
test('默认政策生成的系统提示含 include 与 exclude，且不含实例专用词', () => {
  const system = buildJudgeSystem(resolveConfig({}).policy)
  for (const item of DEFAULT_POLICY.scope.include) assert.ok(system.includes(item))
  for (const item of DEFAULT_POLICY.scope.exclude) assert.ok(system.includes(item))
  assert.ok(system.includes('拿不准时判 covered'), '范围之后必须给出保守回落')
  // 默认政策只该含中文与格式关键字；出现别的英文词就说明混进了部署侧的专用叫法。
  const allowedWords = new Set(['JSON', 'covered', 'partial', 'missing', 'p_missing', 'why', 'point', 'coverage'])
  const leaked = [...new Set(system.match(/[A-Za-z_]+/g) ?? [])].filter((word) => !allowedWords.has(word))
  assert.deepEqual(leaked, [], `默认政策不该含部署侧专用词：${leaked.join(', ')}`)
})
test('自定义政策替换了默认范围', () => {
  const cfg = resolveConfig({
    policy: {
      scope: { include: ['只判 X'], exclude: ['不判 Y'] },
      criteria: { covered: 'C1', partial: 'P1', missing: 'M1' },
      anchor: '自定义锚点：只判 X。',
    },
  })
  const system = buildJudgeSystem(cfg.policy)
  assert.ok(system.includes('只判 X'))
  assert.ok(system.includes('不判 Y'))
  assert.ok(!system.includes('本项目的目标、规则'))
  assert.match(system, /C1 = covered/)
  const prompt = buildJudgePrompt({ question: 'q', answer: 'a', docs: DOCS, policy: cfg.policy })
  assert.ok(prompt.trimEnd().endsWith('}'), 'prompt 应以 JSON 模板收尾')
  assert.ok(prompt.includes('自定义锚点：只判 X。'), '锚点必须在输出段')
  assert.ok(prompt.indexOf('自定义锚点：只判 X。') > prompt.indexOf('## 输出'), '锚点必须在「## 输出」之后')
})
test('prompt 带上了问题、答复、两份文档，且不含 target 字段', () => {
  const text = buildJudgePrompt({ question: '这个实验的动机是什么？', answer: '因为……', docs: DOCS, policy: resolveConfig({}).policy })
  assert.match(text, /这个实验的动机是什么？/)
  assert.match(text, /因为……/)
  assert.match(text, /设计说明（\/tmp\/fixture\/DESIGN\.md）/)
  assert.match(text, /"p_missing"/)
  assert.doesNotMatch(text, /"target"/, '落点不该出现在判定器的输出契约里')
})
test('hint 模式只提示不提问，并把落点交回会话', () => {
  const text = buildHint(verdict({}), 'hint', ['设计说明'])
  assert.match(text, /置信度 90%/)
  assert.match(text, /落点由你判断/)
  assert.match(text, /我对照的是：设计说明/)
  assert.doesNotMatch(text, /ask_user_question/)
})
test('ask 模式让 agent 去问，且不指定落点文件', () => {
  const text = buildHint(verdict({}), 'ask', ['设计说明', '格式约定'])
  assert.match(text, /ask_user_question/)
  assert.match(text, /「补（Recommended）」/)
  assert.match(text, /先不补/)
  assert.match(text, /不要重试/)
  assert.match(text, /哪个文件哪一节/)
  assert.doesNotMatch(text, /写进「设计说明」/, '不该由插件点名文件')
})
test('partial 用不同措辞', () => {
  assert.match(buildHint(verdict({ coverage: 'partial' }), 'hint'), /写得不够清楚/)
})

console.log('文档加载与超限诊断')
test('真实文件读得到，缺失文件留 error 且不抛', () => {
  const file = path.join(tmp, 'DESIGN.md')
  fs.writeFileSync(file, '# 设计要求\n- 动机：待填\n', 'utf8')
  const cfg = resolveConfig({ docs: [{ label: '设计要求', path: file }, { label: '不存在', path: path.join(tmp, 'nope.md') }] })
  const docs = loadDocs(cfg)
  assert.equal(docs.length, 2)
  assert.match(docs[0].text, /动机：待填/)
  assert.equal(docs[0].truncated, false)
  assert.equal(docs[0].overCap, false)
  assert.equal(docs[1].text, '')
  assert.ok(docs[1].error)
})
test('maxChars 截断生效，且标出 truncated / totalChars / limit / overCap', () => {
  const file = path.join(tmp, 'big.md')
  fs.writeFileSync(file, 'x'.repeat(500), 'utf8')
  const docs = loadDocs(resolveConfig({ docs: [{ label: 'big', path: file, maxChars: 100 }] }))
  assert.ok(docs[0].text.length < 200)
  assert.match(docs[0].text, /已截断/)
  assert.equal(docs[0].truncated, true)
  assert.equal(docs[0].overCap, true)
  assert.equal(docs[0].totalChars, 500)
  assert.equal(docs[0].limit, 100)
})
test('⭐ 文档超上限必须被诊断出来（message 点名原文长度与上限）', () => {
  const file = path.join(tmp, 'over.md')
  fs.writeFileSync(file, 'y'.repeat(900), 'utf8')
  const cfg = resolveConfig({ docs: [{ label: '超限文档', path: file, maxChars: 100 }] })
  const diag = docDiagnostics(loadDocs(cfg), cfg)
  assert.equal(diag.ok, false)
  assert.equal(diag.overCap.length, 1)
  assert.match(diag.message, /文档超过上限/)
  assert.match(diag.message, /900/)
  assert.match(diag.message, /100/)
})
test('刚好等于上限不算超限', () => {
  const file = path.join(tmp, 'exact.md')
  fs.writeFileSync(file, 'z'.repeat(100), 'utf8')
  const cfg = resolveConfig({ docs: [{ label: 'exact', path: file, maxChars: 100 }] })
  const diag = docDiagnostics(loadDocs(cfg), cfg)
  assert.equal(diag.ok, true)
  assert.equal(diag.overCap.length, 0)
})
test('缺文件也算诊断不通过，但不抛', () => {
  const cfg = resolveConfig({ docs: [{ label: 'nope', path: path.join(tmp, 'nope.md') }] })
  const diag = docDiagnostics(loadDocs(cfg), cfg)
  assert.equal(diag.ok, false)
  assert.equal(diag.missing.length, 1)
  assert.match(diag.message, /读不到/)
})
test('截断的文档在 prompt 里标注原文长度', () => {
  const file = path.join(tmp, 'big2.md')
  fs.writeFileSync(file, 'w'.repeat(900), 'utf8')
  const cfg = resolveConfig({ docs: [{ label: 'big', path: file }], maxDocChars: 100 })
  const docs = loadDocs(cfg)
  const prompt = buildJudgePrompt({ question: 'q', answer: 'a', docs, policy: cfg.policy })
  assert.match(prompt, /已截断：只给了前 \d+ 字符，原文 900 字符/)
})

console.log('截断硬闸（机械降级，⛔ 不靠提示词自觉）')
const truncatedDoc = { label: '超限', path: '/tmp/fixture/big.md', text: 'x'.repeat(90), truncated: true, totalChars: 900, limit: 100, overCap: true }
const intactDoc = { label: '正常', path: '/tmp/fixture/rules.md', text: '规则', truncated: false, totalChars: 2, limit: 100, overCap: false }
test('没有截断文档时判定原样返回', () => {
  const out = applyTruncationGate(verdict({}), [intactDoc])
  assert.equal(out.gated, false)
  assert.equal(out.verdict.coverage, 'missing')
  assert.equal(out.verdict.probability, 0.9)
})
test('⭐ missing + 有文档被截断 ⇒ 降级 partial 且 p 封顶 0.6', () => {
  const out = applyTruncationGate(verdict({ p_missing: 0.95 }), [truncatedDoc])
  assert.equal(out.gated, true)
  assert.equal(out.verdict.coverage, 'partial')
  assert.equal(out.verdict.probability, 0.6)
  assert.equal(out.verdict.truncationGated, true)
  assert.match(out.why, /被截断/)
})
test('⭐ 默认阈值 0.8 下，截断材料的 missing 永远不触发', () => {
  const out = applyTruncationGate(verdict({ p_missing: 0.99 }), [truncatedDoc])
  assert.equal(gateVerdict(out.verdict, 0.8).hit, false)
})
test('阈值放宽到 0.5 时会提示，但只能是 partial 且 p=0.6', () => {
  const out = applyTruncationGate(verdict({ p_missing: 0.99 }), [truncatedDoc])
  const gate = gateVerdict(out.verdict, 0.5)
  assert.equal(gate.hit, true)
  assert.equal(out.verdict.coverage, 'partial')
  assert.equal(out.verdict.probability, 0.6)
})
test('partial 被截断时只压概率，不改覆盖档', () => {
  const out = applyTruncationGate(verdict({ coverage: 'partial', p_missing: 0.9 }), [truncatedDoc])
  assert.equal(out.verdict.coverage, 'partial')
  assert.equal(out.verdict.probability, 0.6)
})
test('判定为 null 时硬闸不造判定', () => {
  const out = applyTruncationGate(null, [truncatedDoc])
  assert.equal(out.verdict, null)
  assert.equal(out.gated, false)
})

console.log('flattenJevAnswer')
test('从 probabilities 里取 missing 概率，忽略多余的 target 答案', () => {
  const flat = flattenJevAnswer({
    answers: [
      { name: 'coverage', choice: 'missing', probabilities: { covered: 0.05, partial: 0.03, missing: 0.92 } },
      { name: 'target', choice: '设计说明', confidence: 0.88 },
    ],
  })
  assert.equal(flat.coverage, 'missing')
  assert.equal(flat.p_missing, 0.92)
  assert.equal(flat.target, undefined)
})
test('只有 confidence 时按 choice 折算', () => {
  const flat = flattenJevAnswer({ answers: [{ name: 'coverage', choice: 'covered', confidence: 0.9 }] })
  assert.ok(Math.abs(flat.p_missing - 0.1) < 1e-9)
})
test('缺 coverage 返回 null', () => {
  assert.equal(flattenJevAnswer({ answers: [] }), null)
})

fs.rmSync(tmp, { recursive: true, force: true })

console.log(`\n${passed} passed`)
