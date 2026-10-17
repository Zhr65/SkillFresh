/**
 * stamp.mjs — 把 catalog 条目里的 installSource.commit 钉到当前 HEAD，
 * 顺手把 latestVersion 对齐 npm/package.json。
 *
 * 为什么必须钉 commit：市场校验 github 来源时，取的是
 *   raw.githubusercontent.com/<owner>/<repo>/<commit>/<subdir>/package.json
 * 用分支名（main）会被拒 —— schema 只接受 40 位 commit，
 * 因为分支会动，装出来的东西不可复现。
 *
 * 顺序很重要：
 *   ① 先提交代码（引擎、lib、package.json 的版本号都在里面）
 *   ② 再跑 node catalog/stamp.mjs       ← 钉到刚提交的那个 commit
 *   ③ 再提交 catalog/ 本身
 * 第 ③ 步产生的 commit 不包含在 pin 里，也无所谓 —— 用户要的是代码。
 *
 * 用法：npm run release:pin
 */
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '..')
const PAGE = path.join(HERE, 'catalog-provider-page.json')
const PKG = path.join(ROOT, 'npm', 'package.json')

function git(args) {
  return execFileSync('git', args, { cwd: ROOT, encoding: 'utf8' }).trim()
}

const sha = git(['rev-parse', 'HEAD'])
if (!/^[0-9a-f]{40}$/.test(sha)) {
  console.error(`[fail] 拿不到合法的 HEAD：${sha}`)
  process.exit(1)
}

const dirty = git(['status', '--porcelain', '--', 'npm', 'scripts'])
if (dirty) {
  console.error('[fail] npm/ 或 scripts/ 还有没提交的改动 —— 先提交，再钉 commit：')
  for (const line of dirty.split('\n')) console.error('       ' + line)
  process.exit(1)
}

const version = JSON.parse(fs.readFileSync(PKG, 'utf8')).version
const page = JSON.parse(fs.readFileSync(PAGE, 'utf8'))
const item = page.items[0]

const before = item.installSource?.commit
item.installSource = { ...(item.installSource ?? { kind: 'github' }), kind: 'github', commit: sha }
item.latestVersion = version

fs.writeFileSync(PAGE, JSON.stringify(page, null, 2) + '\n', 'utf8')

const { owner, repo } = (() => {
  const p = new URL(item.repository.url).pathname.split('/').filter(Boolean)
  return { owner: p[0], repo: p[1] }
})()

console.log(`[ok] installSource.commit  ${before} → ${sha}`)
console.log(`[ok] latestVersion         ${version}`)
console.log('')
console.log('下一步：')
console.log('  node catalog/validate.mjs      # 用市场自己的校验器验一遍')
console.log('  git add catalog && git commit  # 提交条目本身')
console.log('')
console.log('提醒：npm 发布后要回头删掉 installSource ——')
console.log('      市场只要看到 installSource 就走 github 固定 commit，')
console.log('      那样 npm 发了新版用户也拿不到（版本权威被 pin 死了）。')
