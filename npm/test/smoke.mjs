/**
 * smoke.mjs — 插件冒烟测试（不进 npm 包，只在本仓库跑）
 *
 * 做的是「装到 DSH 里会发生什么」的最小复现：
 *   ① 假 ctx（inject + tools.register）→ apply() → 断言 5 个工具注册上
 *   ② 真跑引擎：临时 DSH_HOME 下 ensureEngine → 调 Python → 出折旧表
 *   ③ 断言输出里没有路径泄漏、没有 traceback
 *
 * 用法：node test/smoke.mjs
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

// engine/ 是生成物（gitignore），新 clone 里没有。给一句人话，而不是 30 条连锁失败。
const ENGINE_PY = new URL('../engine/scripts/console.py', import.meta.url)
if (!fs.existsSync(ENGINE_PY)) {
  console.error('[fail] 缺 npm/engine/ —— 先跑 `npm run sync`（`npm test` 会自动跑它）')
  process.exit(1)
}

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'skill-auditor-smoke-'))
process.env.DSH_HOME = TMP
delete process.env.SKILL_AUDITOR_HOME

const failures = []
function check(cond, label, detail) {
  if (cond) {
    console.log(`  ✓ ${label}`)
  } else {
    console.log(`  ✗ ${label}${detail ? `\n      ${String(detail).slice(0, 400)}` : ''}`)
    failures.push(label)
  }
}

const mod = await import('../lib/index.js')

// ---------------------------------------------------------------- ① 注册面
console.log('\n[1] 注册面')
const registered = []
const ctx = {
  logger: { warn: (m) => console.log('  [warn] ' + m), info: () => {} },
  inject: (deps, cb) => cb(ctx),
  tools: { register: (t) => registered.push(t) },
}
mod.apply(ctx)

const names = registered.map((t) => t.name)
check(names.length === 9, `注册 9 个工具（实际 ${names.length}）`, names.join(', '))
for (const want of [
  'skill_audit_status',
  'skill_audit_recommend',
  'skill_audit_check',
  'skill_audit_scan',
  'skill_audit_suggest',
  'skill_audit_probe',
  'skill_audit_run',
  'skill_audit_can',
  'skill_audit_find',
]) {
  check(names.includes(want), `含 ${want}`)
}
const suggest = registered.find((t) => t.name === 'skill_audit_suggest')
check(
  suggest && suggest.parameters.required?.includes('need'),
  'suggest 把 need 标为必填',
  JSON.stringify(suggest?.parameters),
)
for (const t of registered) {
  check(t.parameters.type === 'object', `${t.name} parameters 是 object schema`)
  check(typeof t.output?.render === 'function', `${t.name} 有 output.render`)
}

// ---------------------------------------------------------------- ② 真跑引擎
console.log('\n[2] 引擎（临时 DSH_HOME）')
const { engineInfo } = await import('../lib/engine.mjs')

const status = registered.find((t) => t.name === 'skill_audit_status')
const out = await status.execute({ host: 'deepseek' }, {})
check(typeof out === 'string' && out.length > 0, 'status 返回非空字符串')
check(/能力折旧表|插件/.test(out), 'status 输出是折旧表', out)
check(!/Traceback|ENOENT|exit [1-9]/.test(out), 'status 无 traceback / 无 ENOENT / 非零退出', out)
check(!/skill-auditor-smoke-.*\\\\scripts/.test(out) === true, '输出里不含内部脚本路径')

const dataDir = engineInfo().dataDir
check(fs.existsSync(path.join(dataDir, 'scripts', 'console.py')), '引擎已复制到数据目录')
check(fs.existsSync(path.join(dataDir, 'hosts.json')), 'hosts.json 已生成')
check(fs.existsSync(path.join(dataDir, 'model-version.json')), 'model-version.json 已生成')
check(
  !fs.existsSync(path.join(dataDir, 'registry.workbuddy.json')),
  '没有把上游的 registry 带过来',
)

const reg = path.join(dataDir, 'registry.deepseek.json')
check(fs.existsSync(reg), '自动 scan 写出了 registry.deepseek.json')
if (fs.existsSync(reg)) {
  const parsed = JSON.parse(fs.readFileSync(reg, 'utf8'))
  const n = Object.keys(parsed.assets || parsed.skills || {}).length
  check(n > 0, `注册表里有 ${n} 个真实插件`)
}

// ---------------------------------------------------------------- ③ 其余工具
console.log('\n[3] 其余工具')
const rec = await registered.find((t) => t.name === 'skill_audit_recommend').execute({ host: 'deepseek' }, {})
check(/强|观察|未验证/.test(rec), 'recommend 输出五档面板', rec.slice(0, 300))
check(!/Traceback|ENOENT/.test(rec), 'recommend 无报错')

const chk = await registered.find((t) => t.name === 'skill_audit_check').execute({ host: 'deepseek' }, {})
check(/模型|基线|registry/.test(chk), 'check 有实质输出', chk.slice(0, 300))

const sug = await registered.find((t) => t.name === 'skill_audit_suggest').execute({ need: '把 pdf 批量转成 word' }, {})
check(/建议|匹配|需求/.test(sug), 'suggest 有实质输出', sug.slice(0, 300))
check(!/Traceback/.test(sug), 'suggest 无 traceback')

const empty = await registered.find((t) => t.name === 'skill_audit_suggest').execute({ need: '   ' }, {})
check(/缺少 need/.test(empty), 'need 为空时给出明确提示', empty)

const scan = await registered.find((t) => t.name === 'skill_audit_scan').execute({ host: 'deepseek' }, {})
check(/ok|registry/.test(scan), 'scan 输出可读', scan.slice(0, 200))

// probe 在临时 DSH_HOME 里没有日志可读 —— 关键是它要说清「没有证据 ≠ 没问题」，而不是崩
const probe = await registered.find((t) => t.name === 'skill_audit_probe').execute({ host: 'deepseek' }, {})
check(/运行时证据|没有配置可读日志/.test(probe), 'probe 有实质输出', probe.slice(0, 300))
check(!/Traceback|ENOENT: python/.test(probe), 'probe 无 traceback', probe.slice(0, 300))
check(/没有证据|没有配置可读日志|故障迹象/.test(probe), 'probe 明确区分「没证据」与「没问题」', probe.slice(0, 300))

// ---------------------------------------------------------------- ④ 幂等
console.log('\n[4] 幂等 / 不覆盖用户数据')
const before = fs.readFileSync(path.join(dataDir, 'hosts.json'), 'utf8')
const custom = JSON.parse(before)
custom.hosts.myhost = { kind: 'skill', label: 'skill', root: '~/my-skills', discover: 'dirs', manifests: ['SKILL.md'] }
fs.writeFileSync(path.join(dataDir, 'hosts.json'), JSON.stringify(custom, null, 2))

const { ensureEngine } = await import('../lib/engine.mjs')
const again = ensureEngine()
check(again.action === 'unchanged', '重复 ensureEngine 是 unchanged', JSON.stringify(again))
const after = JSON.parse(fs.readFileSync(path.join(dataDir, 'hosts.json'), 'utf8'))
check(!!after.hosts.myhost, '用户改过的 hosts.json 没被覆盖')

// ---------------------------------------------------------------- ⑤ 升级
// 这是「发布后能不能更新」的核心：包版本变了 → 引擎代码整体替换，用户数据一条不动。
console.log('\n[5] 升级（模拟 0.0.1-old → 当前版本）')
const userRegistry = path.join(dataDir, 'registry.mine.json')
fs.writeFileSync(userRegistry, JSON.stringify({ host: 'mine', assets: { a: { fingerprint: 'deadbeef' } } }))
const userGolden = path.join(dataDir, 'golden', 'mine.json')
fs.writeFileSync(userGolden, JSON.stringify({ skill: 'mine', cases: [{ id: 'm-01' }] }))
const consolePy = path.join(dataDir, 'scripts', 'console.py')
fs.appendFileSync(consolePy, '\n# POLLUTED-BY-OLD-VERSION\n')
const stale = path.join(dataDir, 'scripts', 'removed-in-newer-version.py')
fs.writeFileSync(stale, '# 旧版本有、新版本删掉的脚本\n')
const markerFile = path.join(dataDir, '.engine.json')
fs.writeFileSync(markerFile, JSON.stringify({ version: '0.0.1-old', action: 'created' }))

const upgraded = ensureEngine()
check(upgraded.action === 'upgraded', '版本变化被识别为 upgraded', JSON.stringify(upgraded))
check(!fs.readFileSync(consolePy, 'utf8').includes('POLLUTED-BY-OLD-VERSION'), '引擎代码被新版覆盖')
check(!fs.existsSync(stale), '新版本删掉的旧脚本被清掉（先删后拷）')
check(fs.existsSync(userRegistry), '用户注册表还在')
check(
  JSON.parse(fs.readFileSync(userRegistry, 'utf8')).assets.a.fingerprint === 'deadbeef',
  '用户注册表内容没变',
)
check(fs.existsSync(userGolden), '用户 golden set 还在')
check(
  JSON.parse(fs.readFileSync(markerFile, 'utf8')).version === upgraded.version,
  '版本标记已更新到当前包版本',
)
check(ensureEngine().action === 'unchanged', '升级后再次调用是 unchanged')

// ---------------------------------------------------------------- 结果
fs.rmSync(TMP, { recursive: true, force: true })

console.log('')
if (failures.length) {
  console.log(`[FAIL] ${failures.length} 项未通过：\n  - ${failures.join('\n  - ')}`)
  process.exit(1)
}
console.log('[PASS] 全部通过')
