/**
 * validate.mjs — 用**市场自己的校验器**验 catalog 条目，不是我自己编的规则。
 *
 * 为什么这么绕：DSH 市场读目录来源时会跑
 *   dsh-community-market/lib/contracts/validate.js  → parseCatalogProviderPage
 * 它用的是包内 ajv + 生成好的 schema，还有 schema 管不到的语义检查
 * （id 不重复、repository 能规范化、installSource 能对上 repository）。
 * 自己照文档抄一遍规则必然和实现有偏差 —— 所以直接 import 那份实现。
 *
 * 校验器找不到时**明确说找不到**并退化为结构检查，不假装验过了。
 *
 * 用法：
 *   node catalog/validate.mjs
 *   DSH_MARKET_DIR="/path/to/dsh-community-market" node catalog/validate.mjs
 */
import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PAGE = path.join(HERE, 'catalog-provider-page.json')

/** 按「最可能是真的那份」排序的候选路径。 */
function candidates() {
  const list = []
  if (process.env.DSH_MARKET_DIR) list.push(process.env.DSH_MARKET_DIR)

  const home = os.homedir()
  for (const profile of ['desktop', 'web']) {
    list.push(path.join(home, '.dsh', 'profiles', profile, 'node_modules', 'dsh-community-market'))
  }
  // 桌面端内置（Electron 自带 resources/app/node_modules）
  for (const base of [
    process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'Programs'),
    process.env.ProgramFiles,
    'D:/AI',
  ].filter(Boolean)) {
    // 只在浅层扫，别把整块盘翻一遍
    try {
      for (const d of fs.readdirSync(base)) {
        list.push(path.join(base, d, 'resources', 'app', 'node_modules', 'dsh-community-market'))
      }
    } catch {}
  }
  return list
}

async function loadValidator() {
  for (const dir of candidates()) {
    const entry = path.join(dir, 'lib', 'contracts', 'validate.js')
    if (!fs.existsSync(entry)) continue
    try {
      const mod = await import(pathToFileURL(entry).href)
      if (typeof mod.parseCatalogProviderPage === 'function') {
        return { dir, parse: mod.parseCatalogProviderPage }
      }
    } catch (e) {
      console.error(`[warn] 找到 ${entry} 但 import 失败：${e.message}`)
    }
  }
  return null
}

function structuralCheck(page) {
  const problems = []
  if (page.schemaVersion !== '1.0.0') problems.push('schemaVersion 必须是 "1.0.0"')
  if (!Array.isArray(page.items)) problems.push('items 必须是数组')
  if (!page.page || typeof page.page !== 'object') problems.push('page 必须是对象')
  for (const [i, it] of (page.items ?? []).entries()) {
    for (const k of ['id', 'name', 'displayName', 'summary']) {
      if (typeof it[k] !== 'string' || !it[k]) problems.push(`items[${i}].${k} 缺失`)
    }
    if (!it.repository && !it.package) problems.push(`items[${i}] 必须有 repository 或 package`)
    if (it.installSource) {
      if (it.installSource.kind !== 'github') problems.push(`items[${i}].installSource.kind 必须是 github`)
      if (!/^[0-9a-f]{40}$/.test(it.installSource.commit ?? '')) {
        problems.push(`items[${i}].installSource.commit 必须是 40 位小写十六进制`)
      }
      if (!it.repository) problems.push(`items[${i}] 有 installSource 却没 repository —— 市场会直接跳过这一条`)
    }
  }
  return problems
}

function splitRepo(url) {
  const parts = new URL(url).pathname.split('/').filter(Boolean)
  return { owner: parts[0], repo: (parts[1] ?? '').replace(/\.git$/, '') }
}

function reportPin(page) {
  const item = page.items[0]
  const commit = item.installSource?.commit
  const { owner, repo } = splitRepo(item.repository.url)
  const sub = item.repository.subdirectory
  const manifestUrl = `https://raw.githubusercontent.com/${owner}/${repo}/${commit}/${sub ? sub + '/' : ''}package.json`
  console.log('')
  console.log('固定 commit 对应的清单地址（市场校验时会去取这个 URL）：')
  console.log('  ' + manifestUrl)
  console.log('')
  console.log('安装目标（pnpm）：')
  console.log(`  github:${owner}/${repo}#${commit}${sub ? `&path:/${sub}` : ''}`)
}

async function main() {
  const page = JSON.parse(fs.readFileSync(PAGE, 'utf8'))
  const found = await loadValidator()

  if (!found) {
    console.error('[warn] 没找到 dsh-community-market 的校验器 —— 下面只是**结构检查**，不等于通过市场校验')
    console.error('       想真验：DSH_MARKET_DIR=<市场包目录> node catalog/validate.mjs')
    const problems = structuralCheck(page)
    if (problems.length) {
      for (const p of problems) console.error('  - ' + p)
      process.exit(1)
    }
    console.log('[ok] 结构检查通过（未经市场校验器验证）')
    reportPin(page)
    return
  }

  console.log(`[i] 用市场自己的校验器：${found.dir}`)
  try {
    found.parse(page)
    console.log('[ok] 通过 parseCatalogProviderPage —— 这就是市场读目录来源时跑的那段')
    reportPin(page)
  } catch (e) {
    console.error('[fail] 市场校验器拒绝了这个条目：')
    console.error('  ' + (e.message ?? String(e)))
    if (Array.isArray(e.issues)) for (const i of e.issues) console.error('  - ' + JSON.stringify(i))
    process.exit(1)
  }
}

main()
