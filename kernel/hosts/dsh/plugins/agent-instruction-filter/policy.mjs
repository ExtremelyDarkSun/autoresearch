// GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
// LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
// PURPOSE: 指令过滤策略的**出厂默认值**（中性空模板）+ 策略 schema 说明。改判据只改本文件或外置 policyPath。
// ORIGIN: install/agent-instruction-filter/policy.mjs（机制抽取；实例的四份项目规则 drop、旧策略史已剥离）
// CUMULATIVE-DELTA: 首版即中性默认：`rules: []` + `defaultRule.mode: 'keep'` ⇒ **一份都不裁**。
//   `drop` / `filter` 两种能力完整保留，但只能由**显式策略文件**启用，⛔ 不作为出厂默认。
//
// 规则语义（与实现无关的契约，改实现时不许改这几条）：
//   match               路径 glob（`**` 跨目录 · `*` 单层）。`\` 与 `/` 等价；无通配符时按「精确或路径后缀」匹配。
//   mode                'keep' 原样 · 'drop' 整段（整份文件）丢弃 · 'filter' 按下面三项裁
//   stripLeadingComment 去掉文件开头的 HTML 注释块（本仓库 §文件头惯例写在注释里）
//   dropSections        标题子串（**不含 `#`**）：命中的标题连同正文丢到下一个同级或更高级标题之前
//   dropLines           行子串：丢掉含该子串的整行（用来摘表格里的单行）
// 命中顺序：rules 自上而下，第一条命中生效（所以**具体必须排在通配之前**）。都不命中 ⇒ defaultRule。
//
// ⚠️ 安全默认（两条互相独立的护栏）：
//   ① 插件 config.mode 出厂为 'off'（见 cordis.patch.yml 与 index.js）⇒ 连监听器都不注册；
//   ② 本文件 rules 为空 ⇒ 就算插件被打开，也只是「逐字不动」。
//   读策略失败时索引回落到本文件（全 keep），⛔ 绝不回落到任何旧 drop 策略。

/** 允许的规则模式。任何别的值在加载时被判为不合法策略。 */
export const RULE_MODES = ['keep', 'drop', 'filter']

/** 出厂策略：中性、空、只保留。 */
export const policy = {
  version: 2,

  defaultRule: { mode: 'keep' },

  /** 出厂为空。要裁剪时请**外置**一份策略文件并用 config.policyPath 指过去。 */
  rules: [],
}

/**
 * 策略对象的最小结构校验。⛔ 只判结构，不判业务合理性。
 * @returns {{ok: true} | {ok: false, why: string}}
 */
export function validatePolicy(candidate) {
  if (candidate === null || typeof candidate !== 'object' || Array.isArray(candidate)) {
    return { ok: false, why: '策略不是一个对象' }
  }
  if (!Array.isArray(candidate.rules)) return { ok: false, why: 'rules 不是数组' }
  for (const [index, rule] of candidate.rules.entries()) {
    if (rule === null || typeof rule !== 'object') {
      return { ok: false, why: `rules[${index}] 不是对象` }
    }
    if (typeof rule.match !== 'string' || rule.match.trim().length === 0) {
      return { ok: false, why: `rules[${index}].match 缺失或为空` }
    }
    if (rule.mode !== undefined && !RULE_MODES.includes(rule.mode)) {
      return { ok: false, why: `rules[${index}].mode 非法：${String(rule.mode)}` }
    }
  }
  const fallback = candidate.defaultRule
  if (fallback !== undefined) {
    if (fallback === null || typeof fallback !== 'object') {
      return { ok: false, why: 'defaultRule 不是对象' }
    }
    if (fallback.mode !== undefined && !RULE_MODES.includes(fallback.mode)) {
      return { ok: false, why: `defaultRule.mode 非法：${String(fallback.mode)}` }
    }
  }
  return { ok: true }
}
