/**
 * check-engine.mjs — 验「入库的 engine/ 和上游一致」。
 *
 * 为什么需要它：engine/ 现在要入库，因为 DSH 市场的 **github 安装通道**
 * （catalog 条目里 installSource.kind = "github" + 固定 40 位 commit）拿到的
 * 就是仓库里这一份 —— 没有 prepack 可跑，没人会替你同步。
 * 入库的东西一旦和上游漂了，用户装到的就是旧引擎，而所有测试都是绿的
 * （测试跑的是 sync 之后的现场产物，不是提交里的那份）。
 *
 * 做法：先按上游重建 engine/，再看 git 眼里它跟 HEAD 有没有差。
 *   · 有差 → 说明你改了上游脚本但没提交 engine/，或者手改了 engine/
 *   · 无差 → 提交里的 engine/ 就是上游此刻的样子
 *
 * 用法：npm run check:engine
 * 退出码：0 = 一致，1 = 不一致或缺 git
 */
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const NPM_ROOT = path.resolve(HERE, '..')
const ENGINE = path.join(NPM_ROOT, 'engine')
const NODE = process.execPath

function run(script) {
  execFileSync(NODE, [path.join(HERE, script)], { cwd: NPM_ROOT, stdio: 'inherit' })
}

function git(args) {
  return execFileSync('git', args, { cwd: NPM_ROOT, encoding: 'utf8' })
}

function main() {
  // ① 先重建（顺带跑禁用词扫描；不同步就会在这里 fail）
  run('sync-engine.mjs')

  if (!fs.existsSync(ENGINE)) {
    console.error('[fail] engine/ 不存在，sync 没产出东西')
    process.exit(1)
  }

  // ② git 眼里有没有差
  let status
  try {
    git(['rev-parse', '--git-dir'])
    status = git(['status', '--porcelain', '--', 'engine']).trim()
  } catch {
    console.error('[warn] 不在 git 仓库里（或没有 git），跳过「是否已提交」检查')
    console.error('       只验证了「engine/ 能被同步出来」，没验证「提交里那份是新的」')
    process.exit(0)
  }

  if (!status) {
    console.log('[ok] 入库的 engine/ 与上游一致（git 无差异）')
    return
  }

  console.error('[fail] engine/ 与提交里的版本不一致 —— 别发布，先提交它：')
  for (const line of status.split('\n')) console.error('       ' + line)
  console.error('')
  console.error('   git add npm/engine && git commit -m "chore(npm): 同步引擎"')
  console.error('')
  console.error('   否则 github 安装通道装到的是旧引擎，而 npm 通道是新的 —— 两个通道不同行为。')
  process.exit(1)
}

main()
