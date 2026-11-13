// GENERATED-BY: portable synchronization (DSH agent, 2026-11-13 +08:00)
// LAST-EDIT: portable synchronization (DSH agent, 2026-11-13 +08:00)
// PURPOSE: 可重建的打包入口 —— 用本机 Node 自带的 npm 做本地 `npm pack`（不联网），并生成逐文件 sha256 清单；
//          需要复核时用 --verify 重算，避免把 tgz 字节当成唯一真相。
// ORIGIN: none（部署版只放了固定 tgz 与机器特定验收器；这里改成从源码重建）
// CUMULATIVE-DELTA: 首版。三个约束：① 只读 package.json 的 files 列表，不把 node_modules/测试打进去；
//          ② 不写任何时间戳（同一份源码得到同一份 manifest.json）；③ 没有 npm 时只写清单并以 2 退出，不假装成功。

import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = dirname(fileURLToPath(import.meta.url))
const packageJson = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
const args = process.argv.slice(2)
const manifestOnly = args.includes('--manifest-only')
const verify = args.includes('--verify')
const dist = resolve(root, 'dist')

function posix(path) {
  return path.split(sep).join('/')
}

function walk(entry, out) {
  const absolute = resolve(root, entry)
  if (!existsSync(absolute)) return
  const info = statSync(absolute)
  if (info.isDirectory()) {
    for (const child of readdirSync(absolute).sort()) walk(join(entry, child), out)
    return
  }
  if (info.isFile()) out.add(posix(relative(root, absolute)))
}

function packagedFiles() {
  const out = new Set(['package.json'])
  for (const entry of packageJson.files ?? []) walk(entry, out)
  return [...out].sort()
}

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex')
}

function buildManifest() {
  const files = packagedFiles().map((path) => {
    const bytes = readFileSync(join(root, path))
    return { path, bytes: bytes.length, sha256: sha256(bytes) }
  })
  // integrity = 对 files 数组的规范 JSON 再做一次 sha256；改动任何一个文件都会变。
  const integrity = sha256(Buffer.from(JSON.stringify(files), 'utf8'))
  return { name: packageJson.name, version: packageJson.version, files, integrity }
}

function writeManifest(manifest) {
  mkdirSync(dist, { recursive: true })
  const path = join(dist, 'manifest.json')
  writeFileSync(path, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')
  return path
}

function verifyManifest() {
  const path = join(dist, 'manifest.json')
  if (!existsSync(path)) {
    console.error(`未执行：找不到 ${path}，先跑一次 node pack.mjs`)
    process.exit(2)
  }
  const expected = JSON.parse(readFileSync(path, 'utf8'))
  const actual = buildManifest()
  const problems = []
  if (expected.name !== actual.name) problems.push(`name 不一致：${expected.name} != ${actual.name}`)
  if (expected.version !== actual.version) problems.push(`version 不一致：${expected.version} != ${actual.version}`)
  const key = (file) => file.path
  const expectedMap = new Map((expected.files ?? []).map((file) => [key(file), file]))
  const actualMap = new Map(actual.files.map((file) => [key(file), file]))
  for (const [file, value] of actualMap) {
    const previous = expectedMap.get(file)
    if (!previous) problems.push(`新增文件：${file}`)
    else if (previous.sha256 !== value.sha256 || previous.bytes !== value.bytes) problems.push(`内容变了：${file}`)
  }
  for (const file of expectedMap.keys()) {
    if (!actualMap.has(file)) problems.push(`文件没了：${file}`)
  }
  if (expected.integrity !== actual.integrity) problems.push('integrity 不一致')
  if (problems.length > 0) {
    console.error(`FAIL: 清单校验不通过（${problems.length} 条）`)
    for (const problem of problems) console.error(`  ✗ ${problem}`)
    process.exit(1)
  }
  console.log(`PASS: ${actual.files.length} 个文件与 dist/manifest.json 一致（integrity=${actual.integrity.slice(0, 16)}…）`)
}

if (verify) {
  verifyManifest()
  process.exit(0)
}

const manifest = buildManifest()
const manifestPath = writeManifest(manifest)
console.log(`已写清单：${manifestPath}（${manifest.files.length} 个文件 · integrity=${manifest.integrity}）`)

if (manifestOnly) process.exit(0)

// 优先用本机 Node 旁边的 npm-cli.js：不走 shell，路径里有空格也不会被拆开。
const npmCli = join(dirname(process.execPath), 'node_modules', 'npm', 'bin', 'npm-cli.js')
if (!existsSync(npmCli)) {
  console.error('未执行 npm pack：找不到本机 npm-cli.js。清单已生成；请用部署机的 npm 执行：')
  console.error(`  cd "${root}" && npm pack --pack-destination dist`)
  process.exit(2)
}
mkdirSync(dist, { recursive: true })
const packed = spawnSync(process.execPath, [npmCli, 'pack', '--json', '--pack-destination', dist], {
  cwd: root,
  encoding: 'utf8',
})
if (packed.status !== 0) {
  console.error(`未执行 npm pack：exit=${packed.status}`)
  if (packed.stderr) console.error(packed.stderr.trim())
  process.exit(2)
}
let filename
try {
  const parsed = JSON.parse(packed.stdout)
  filename = parsed?.[0]?.filename
} catch {
  filename = undefined
}
if (!filename) {
  console.error('npm pack 成功但没解析出 tgz 文件名；请检查 npm 输出。')
  process.exit(2)
}
const tgz = join(dist, filename)
const tgzBytes = readFileSync(tgz)
console.log(`已生成：${tgz}`)
console.log(`tgz sha256：${sha256(tgzBytes)}（${tgzBytes.length} 字节）`)
console.log('⚠️ tgz 字节可能随 npm/压缩实现变化；完整性以 dist/manifest.json 的逐文件 sha256 与 integrity 为准。')
