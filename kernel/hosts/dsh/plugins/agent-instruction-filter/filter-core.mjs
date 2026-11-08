// GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
// LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
// PURPOSE: 纯函数核心 —— 把宿主注入的指令基线条目按策略裁剪。不依赖宿主、不碰网络、可离线自检。
// ORIGIN: install/agent-instruction-filter/filter-core.mjs（机制抽取；实例路径与历史注释已剥离）
// CUMULATIVE-DELTA: 首版。保留四种段标记（基线 / 追加 / 更新 / 移除）· 整段丢弃 · 按标题丢弃子树 · 按行摘除 ·
//   文件头注释剥离 · CRLF 归一。⛔ 默认策略是 keep（见 policy.mjs），本文件不内置任何裁剪。
//
// 格式依据（宿主侧契约，升级宿主后要复核）：注入消息是纯文本块，以 `<system-reminder>` 开闭，
// 段标记为 `Instructions from: <显示路径>` / `Additional instructions from:` /
// `Updated instructions from:` / `Instructions removed:`。段标记行之后是 `\n\n` + 文件正文。

const SYSTEM_REMINDER_OPEN = '<system-reminder>'
const SYSTEM_REMINDER_CLOSE = '</system-reminder>'

/** 四种段标记。基线用第一种，目录级刷新用后三种。 */
const SECTION_MARKERS = [
  'Instructions from: ',
  'Additional instructions from: ',
  'Updated instructions from: ',
  'Instructions removed: ',
]

/** 路径一律按 `/` 比较：Windows 上的显示路径是 `dir\AGENTS.md` 这种形态。 */
export function normalizePath(value) {
  return String(value ?? '').replace(/\\/g, '/').replace(/^\.\//, '')
}

/**
 * 把 glob 风格的 match 编译成正则。
 * 支持 `**`（跨目录）· `*`（单层）· 精确与后缀匹配。
 */
function compileMatch(pattern) {
  const p = normalizePath(pattern)
  if (!p.includes('*')) {
    return (path) => path === p || path.endsWith('/' + p)
  }
  const body = p
    .replace(/[.+^${}()|[\]\\]/g, '\\$&')
    .replace(/\*\*\//g, '\u0000SLASH\u0000')
    .replace(/\*\*/g, '\u0000ANY\u0000')
    .replace(/\*/g, '[^/]*')
    .replace(/\u0000SLASH\u0000/g, '(?:.*/)?')
    .replace(/\u0000ANY\u0000/g, '.*')
  const re = new RegExp('^' + body + '$')
  return (path) => re.test(path) || path.endsWith('/' + p.replace(/^\*\*\//, ''))
}

/** 按顺序取第一条命中的规则；都不命中就用 defaultRule。⛔ 没有策略时返回 keep。 */
export function matchRule(policy, displayPath) {
  const path = normalizePath(displayPath)
  for (const rule of policy?.rules ?? []) {
    if (rule === null || typeof rule !== 'object' || typeof rule.match !== 'string') continue
    const test = compileMatch(rule.match)
    if (test(path)) return rule
  }
  return policy?.defaultRule ?? { mode: 'keep' }
}

/** 把注入正文切成「前言 + 若干段」。段之外的文本进前言。 */
export function splitSections(body) {
  const preamble = []
  const sections = []
  let current = null
  for (const line of body.split('\n')) {
    const marker = SECTION_MARKERS.find((m) => line.startsWith(m))
    if (marker !== undefined) {
      if (current !== null) sections.push(current)
      current = { marker, path: line.slice(marker.length).trim(), lines: [] }
    } else if (current !== null) {
      current.lines.push(line)
    } else {
      preamble.push(line)
    }
  }
  if (current !== null) sections.push(current)
  return { preamble, sections }
}

/**
 * 丢掉命中的标题及其整段正文，直到出现同级或更高级的标题。
 * ⚠️ 已知限制：不看代码围栏，围栏里长得像标题的行也会参与判断。
 */
export function dropHeadings(text, needles) {
  const hits = (needles ?? []).filter((n) => typeof n === 'string' && n.length > 0)
  if (hits.length === 0) return text
  const out = []
  let skipLevel = null
  for (const line of text.split('\n')) {
    const heading = /^(#{1,6})\s+(.*)$/.exec(line)
    if (heading !== null) {
      const level = heading[1].length
      if (skipLevel !== null) {
        // 同级或更高级标题终止丢弃；更深的子标题本身也要丢，⛔ 不能漏出去。
        if (level <= skipLevel) skipLevel = null
        else continue
      }
      if (skipLevel === null && hits.some((n) => heading[2].includes(n))) {
        skipLevel = level
        continue
      }
    } else if (skipLevel !== null) {
      continue
    }
    out.push(line)
  }
  return out.join('\n')
}

/**
 * 丢掉文件开头的 HTML 注释块（本仓库用注释块承载文件头五要素）。
 * 判据：它记录「这份文件怎么来的」，对执行任务是零判断增量。
 */
export function stripLeadingComment(text) {
  return text.replace(/^\s*<!--[\s\S]*?-->[ \t]*\r?\n?/, '')
}

/** 丢掉任何「含有给定子串」的整行。用来摘掉表格里的单行，而不动同表其他行。 */
export function dropLines(text, needles) {
  const hits = (needles ?? []).filter((n) => typeof n === 'string' && n.length > 0)
  if (hits.length === 0) return text
  return text
    .split('\n')
    .filter((line) => !hits.some((n) => line.includes(n)))
    .join('\n')
}

/** 对一段套用规则。返回 null 表示整段丢弃。 */
export function applyRuleToSection(section, rule) {
  if (rule === undefined || rule === null) return section
  if (rule.mode === 'drop') return null
  let lines = section.lines
  if (rule.stripLeadingComment === true) {
    const stripped = stripLeadingComment(lines.join('\n'))
    if (stripped.trim().length === 0) return null
    lines = stripped.split('\n')
  }
  if (Array.isArray(rule.dropSections) && rule.dropSections.length > 0) {
    const kept = dropHeadings(lines.join('\n'), rule.dropSections)
    if (kept.trim().length === 0) return null
    lines = kept.split('\n')
  }
  if (Array.isArray(rule.dropLines) && rule.dropLines.length > 0) {
    const kept = dropLines(lines.join('\n'), rule.dropLines)
    if (kept.trim().length === 0) return null
    lines = kept.split('\n')
  }
  if (lines === section.lines) return section
  return { ...section, lines }
}

/**
 * 裁剪一条注入消息的正文。
 * @returns {{ text: string, changed: boolean, empty: boolean }}
 *   empty=true 表示裁完什么都不剩，调用方应整条丢弃该消息。
 */
export function filterInstructionText(text, policy) {
  // ⚠️ 必须先把 CRLF 归一成 LF：JS 的 `.` 不匹配 `\r`，于是 `/^(#{1,6})\s+(.*)$/` 在
  //    「行尾带 \r」的真实文件上**一条标题都匹配不到**。代价 = 被改写过的注入消息统一成 LF；
  //    这是本插件唯一的非策略性改动（策略为 keep 时文本仍逐字返回，不改行尾）。
  const raw = String(text ?? '').replace(/\r\n/g, '\n')
  if (!raw.startsWith(SYSTEM_REMINDER_OPEN) || !raw.includes(SYSTEM_REMINDER_CLOSE)) {
    return { text: raw, changed: raw !== String(text ?? ''), empty: false }
  }
  const closeIndex = raw.lastIndexOf(SYSTEM_REMINDER_CLOSE)
  const body = raw
    .slice(SYSTEM_REMINDER_OPEN.length, closeIndex)
    .replace(/^\n/, '')
    .replace(/\n$/, '')

  const { preamble, sections } = splitSections(body)
  const kept = []
  for (const section of sections) {
    const rule = matchRule(policy, section.path)
    const result = applyRuleToSection(section, rule)
    if (result !== null) kept.push(result)
  }

  // ⚠️ 关键判据：**只要这份消息带着指令段，而一段都没剩下，就整条丢掉。**
  // 否则文本里会剩下一句没有内容的 intro，它既没有信息、又会让上游以为「注入了东西」。
  // 分段器认不出段（sections 为空）时不套这条，以免误伤「只有一行 marker 文本」那种形态。
  if (sections.length > 0 && kept.length === 0) return { text: '', changed: true, empty: true }

  const blocks = []
  const head = preamble.join('\n').trim()
  if (head.length > 0) blocks.push(head)
  for (const section of kept) {
    // 段的形态是 `marker + path + "\n\n" + content`；切开后 `lines[0]` 就是那个空行，
    // 所以这里只补一个 "\n"，由 `lines.join("\n")` 自己给出第二个。
    blocks.push((section.marker + section.path + '\n' + section.lines.join('\n')).trimEnd())
  }
  if (blocks.length === 0) return { text: '', changed: true, empty: true }

  const next = [SYSTEM_REMINDER_OPEN, blocks.join('\n\n'), SYSTEM_REMINDER_CLOSE].join('\n')
  return { text: next, changed: next !== raw, empty: false }
}
