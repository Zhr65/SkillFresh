/**
 * sync-engine.mjs — 把上游 skill-auditor 的**引擎**同步进 npm 包的 engine/。
 *
 * 为什么不直接 `cp -r`：
 *   ① 上游目录里有用户数据（registry.*.json / baseline-results.json /
 *      个人 golden set / 会话 provenance），绝不能进公开 npm 包；
 *   ② 文档里有本机绝对路径（Python 解释器路径），要脱敏成 `python`；
 *   ③ 需要一个可核对的清单（每个文件的 sha256），发布后能验证包内容。
 *
 * 白名单之外的任何东西都不会被复制。复制后再扫一遍禁用词，命中就 fail ——
 * 宁可发布失败，也不要漏一份个人数据出去。
 *
 * 产物 engine/ **入库**（不在 .gitignore 里）：DSH 市场的 github 安装通道
 * （installSource.kind = "github" + 固定 commit）拿到的就是仓库里这一份，
 * 没有 prepack 可跑。所以入库这份必须和上游同步 —— 用 npm run check:engine 验。
 *
 * 用法：npm run sync   （prepack / pretest 时自动跑）
 */
import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const NPM_ROOT = path.resolve(HERE, '..')
const SRC = path.resolve(NPM_ROOT, '..')
const DEST = path.join(NPM_ROOT, 'engine')

/** 逐条白名单。目录按 *.ext 过滤，文件原样复制。 */
const DIRS = [{ rel: 'scripts', ext: ['.py'] }]
const FILES = [
  'SKILL.md',
  'USAGE.md',
  'README.md',
  'LICENSE',
  'hosts.example.json',
  'golden/_template.json',
]

/** 复制后必须**不存在**的词。命中 = 有个人数据漏出来了 = 直接失败。 */
const FORBIDDEN = [
  /zhr15/i,
  /c-Users-[A-Za-z0-9]/i, // 会话 transcript 目录名
  /\.workbuddy-ai[\\/]binaries/i, // 本机受管解释器路径
  /sk-[A-Za-z0-9]{16,}/, // 疑似 API key
]

function sha256(buf) {
  return crypto.createHash('sha256').update(buf).digest('hex')
}

/** 本机绝对路径 → 中性占位符。顺序敏感：先处理 python.exe，再兜底整段 home。 */
function sanitize(text) {
  return text
    .replace(/[A-Za-z]:[\\/]Users[\\/][^\\/\s"'`]+[\\/][^\s"'`]*?python(?:\.exe)?/gi, 'python')
    .replace(/[A-Za-z]:[\\/]Users[\\/][^\\/\s"'`]+[\\/][^\s"'`]*/gi, '<home>')
    .replace(/\/Users\/[^/\s"'`]+[^\s"'`]*/g, '<home>')
    .replace(/\/home\/[^/\s"'`]+[^\s"'`]*/g, '<home>')
}

function readVersion() {
  const pkg = JSON.parse(fs.readFileSync(path.join(NPM_ROOT, 'package.json'), 'utf8'))
  return pkg.version
}

function main() {
  if (!fs.existsSync(path.join(SRC, 'scripts', 'console.py'))) {
    console.error(`[fail] 上游引擎不存在：${SRC}/scripts/console.py`)
    console.error('       本脚本假定 npm/ 与 skill-auditor 同源（npm/ 是它的子目录）。')
    process.exit(1)
  }

  fs.rmSync(DEST, { recursive: true, force: true })
  fs.mkdirSync(DEST, { recursive: true })

  const written = []
  const problems = []

  for (const { rel, ext } of DIRS) {
    const srcDir = path.join(SRC, rel)
    if (!fs.existsSync(srcDir)) {
      problems.push(`缺少目录 ${rel}`)
      continue
    }
    for (const entry of fs.readdirSync(srcDir)) {
      if (entry.startsWith('__pycache__') || entry.startsWith('.')) continue
      if (!ext.some((e) => entry.endsWith(e))) continue
      const src = path.join(srcDir, entry)
      const buf = fs.readFileSync(src)
      const text = sanitize(buf.toString('utf8'))
      const dst = path.join(DEST, rel, entry)
      fs.mkdirSync(path.dirname(dst), { recursive: true })
      fs.writeFileSync(dst, text, 'utf8')
      written.push({ path: `${rel}/${entry}`, bytes: Buffer.byteLength(text), sha256: sha256(text) })
    }
  }

  for (const rel of FILES) {
    const src = path.join(SRC, rel)
    if (!fs.existsSync(src)) {
      problems.push(`缺少文件 ${rel}`)
      continue
    }
    const text = sanitize(fs.readFileSync(src, 'utf8'))
    const dst = path.join(DEST, rel)
    fs.mkdirSync(path.dirname(dst), { recursive: true })
    fs.writeFileSync(dst, text, 'utf8')
    written.push({ path: rel, bytes: Buffer.byteLength(text), sha256: sha256(text) })
  }

  // 禁用词扫描：扫的是**已经写进 DEST 的最终内容**，不是源文件
  for (const f of written) {
    const text = fs.readFileSync(path.join(DEST, f.path), 'utf8')
    for (const re of FORBIDDEN) {
      const m = re.exec(text)
      if (m) problems.push(`${f.path} 命中禁用词 ${re} → "${m[0]}"`)
    }
  }

  // 用户数据绝不能出现
  const strays = fs.existsSync(DEST)
    ? fs.readdirSync(DEST).filter((f) => /^registry\.|^baseline-results|^model-version|^recorded-baseline|^hosts\.json$/.test(f))
    : []
  for (const s of strays) problems.push(`用户数据泄漏进包：${s}`)

  // 清单必须**可复现**：engine/ 现在入库（github 安装通道要靠它），
  // 带时间戳的话每次 sync 都会产生 git diff，check-engine 就永远报「不同步」。
  // 溯源靠 version + 每文件 sha256，够了。
  const manifest = {
    name: 'dsh-skill-auditor',
    version: readVersion(),
    source: 'skill-auditor (upstream engine)',
    fileCount: written.length,
    files: written.sort((a, b) => a.path.localeCompare(b.path)),
  }
  fs.writeFileSync(path.join(DEST, 'engine-manifest.json'), JSON.stringify(manifest, null, 2) + '\n', 'utf8')

  if (problems.length) {
    console.error('[fail] 引擎同步被拒绝：')
    for (const p of problems) console.error('  - ' + p)
    process.exit(1)
  }

  console.log(`[ok] engine/ ← ${written.length} 个文件，版本 ${manifest.version}`)
  for (const f of manifest.files) console.log(`     ${String(f.bytes).padStart(7)}  ${f.path}`)
}

main()
