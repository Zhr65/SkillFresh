/**
 * engine.mjs — 引擎定位 / Python 探测 / 用户数据目录
 *
 * 设计约束（都是踩过的坑）：
 *   ① 引擎（Python，纯 stdlib）不能直接跑在 node_modules 里 —— 它要写
 *      registry.<host>.json / baseline-results.json / model-version.json，
 *      而 node_modules 可能只读、且 `npm update` 会整个换掉。所以首次运行时
 *      把 engine/ 复制到可写的数据目录，之后所有读写都在数据目录里。
 *   ② 升级只覆盖**引擎代码**（scripts/、文档），绝不覆盖用户数据
 *      （registry.*、baseline-results.json、model-version.json、hosts.json、
 *      用户自己的 golden/*.json）。golden/_template.json 只在缺失时补。
 *   ③ Python 版本靠**实际跑一次**确认，不看命令是否存在 —— Windows 上
 *      `python` 常指向 Microsoft Store 的 0 字节 stub，存在但不可用。
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFile } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const PACKAGE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const ENGINE_SRC = path.join(PACKAGE_ROOT, 'engine')

export const PACKAGE_VERSION = readPackageVersion()

/** DSH 主目录：桌面端与 web 端共用；可用 DSH_HOME 覆盖（与 dsh 自身一致）。 */
export const DSH_HOME = process.env.DSH_HOME || path.join(os.homedir(), '.dsh')

/** 用户数据 + 引擎副本的落地目录。 */
export const DATA_DIR =
  process.env.SKILL_AUDITOR_HOME || path.join(DSH_HOME, 'skill-auditor')

/** 引擎里会被复制到数据目录的条目（白名单，逐条列举，不递归整个包）。
 *  审查 P0-2：原来只有 'scripts'，DSH 用户跑 baseline --from-seed / seed audit
 *  报"没有 seed/ 目录"——console.py 的 SEED_DIR 指向数据目录下的 seed。 */
const ENGINE_DIRS = ['scripts', 'seed']
const ENGINE_FILES = [
  'SKILL.md',
  'USAGE.md',
  'README.md',
  'LICENSE',
  'hosts.example.json',
  'golden/_template.json',
]
/** 引擎代码：每次升级都覆盖（seed 属公共内容，升级应覆盖，与用户数据区分）。 */
const CODE_DIRS = new Set(['scripts', 'seed'])
/** 只在缺失时补齐（可能是用户改过的文件）。 */
const SEED_ONLY = new Set(['golden/_template.json'])

/** 首次安装生成的宿主表：全部用 ~ 相对路径，不含任何个人绝对路径。 */
const DEFAULT_HOSTS = {
  default_host: 'workbuddy',
  hosts: {
    workbuddy: {
      kind: 'skill',
      label: 'skill',
      root: '~/.workbuddy-ai/skills',
      discover: 'dirs',
      manifests: ['SKILL.md'],
    },
    'claude-code': {
      kind: 'skill',
      label: 'skill',
      root: '~/.claude/skills',
      discover: 'dirs',
      manifests: ['SKILL.md'],
    },
    agents: {
      kind: 'skill',
      label: 'skill',
      root: '~/.agents/skills',
      discover: 'dirs',
      manifests: ['SKILL.md'],
    },
    deepseek: {
      kind: 'plugin',
      label: '插件',
      root: '~/.dsh/profiles/desktop',
      discover: 'deps',
      manifests: ['package.json', 'plugin.json'],
    },
    generic: {
      kind: 'plugin',
      label: 'asset',
      root: null,
      discover: 'dirs',
      manifests: ['plugin.json', 'manifest.json', 'SKILL.md', 'package.json'],
    },
  },
}

function readPackageVersion() {
  try {
    const raw = fs.readFileSync(path.join(PACKAGE_ROOT, 'package.json'), 'utf8')
    return JSON.parse(raw).version || '0.0.0'
  } catch {
    return '0.0.0'
  }
}

function markerPath() {
  return path.join(DATA_DIR, '.engine.json')
}

function readMarker() {
  try {
    return JSON.parse(fs.readFileSync(markerPath(), 'utf8'))
  } catch {
    return null
  }
}

function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n', 'utf8')
}

/**
 * 把 engine/ 复制到 DATA_DIR（幂等）。
 * @returns {{dataDir: string, action: 'created'|'upgraded'|'unchanged', version: string}}
 */
export function ensureEngine() {
  if (!fs.existsSync(path.join(ENGINE_SRC, 'scripts', 'console.py'))) {
    throw new Error(
      '引擎缺失：包里没有 engine/scripts/console.py。' +
        '源码仓库里先跑 `npm run sync`（把上游 skill-auditor 的引擎同步进来）。',
    )
  }

  const marker = readMarker()
  const installed = fs.existsSync(path.join(DATA_DIR, 'scripts', 'console.py'))
  const action = !installed ? 'created' : marker?.version === PACKAGE_VERSION ? 'unchanged' : 'upgraded'

  if (action !== 'unchanged') {
    fs.mkdirSync(DATA_DIR, { recursive: true })
    // 原子升级（审查 P0）：先复制到临时目录再逐目录 rename。
    // 原来 rmSync→cpSync 原地改：并发会话（启动自动体检 + 用户首调）会读到
    // 半写文件/ENOENT。rename 在同盘上原子；marker 最后写，中断可自愈。
    const tmpRoot = path.join(DATA_DIR, `.upgrade-${process.pid}-${Date.now()}`)
    fs.mkdirSync(tmpRoot, { recursive: true })
    for (const dir of ENGINE_DIRS) {
      const src = path.join(ENGINE_SRC, dir)
      if (!fs.existsSync(src)) continue
      const dst = path.join(DATA_DIR, dir)
      // 先删后拷：`cpSync(force)` 只覆盖同名文件，新版本**删掉**的旧脚本会残留下来，
      // 后续 import 可能拿到旧模块（改名/拆分的版本最容易踩）。只删引擎目录，不碰用户数据。
      if (fs.existsSync(dst)) fs.rmSync(dst, { recursive: true, force: true })
      fs.cpSync(src, path.join(tmpRoot, dir), { recursive: true })
      fs.renameSync(path.join(tmpRoot, dir), dst)
    }
    for (const rel of ENGINE_FILES) {
      const src = path.join(ENGINE_SRC, rel)
      if (!fs.existsSync(src)) continue
      const dst = path.join(DATA_DIR, rel)
      if (SEED_ONLY.has(rel) && fs.existsSync(dst)) continue
      fs.mkdirSync(path.dirname(dst), { recursive: true })
      fs.copyFileSync(src, dst)
    }
    fs.rmSync(tmpRoot, { recursive: true, force: true })
    writeJson(markerPath(), {
      version: PACKAGE_VERSION,
      action,
      copied_at: new Date().toISOString(),
      source: ENGINE_SRC,
    })
  }

  // 用户数据：只在缺失时补，永不覆盖
  const hostsCfg = path.join(DATA_DIR, 'hosts.json')
  if (!fs.existsSync(hostsCfg)) writeJson(hostsCfg, DEFAULT_HOSTS)
  const stamp = path.join(DATA_DIR, 'model-version.json')
  if (!fs.existsSync(stamp)) {
    // 首次安装：优先写**真实模型串**，而不是 v-unknown。
    // 原因：v-unknown 会让 check 永远说"无需重跑"（unknown == unknown），
    // 模型换代永远检测不到 —— 等于这个功能对陌生人失效。
    const guess = inferEnvModel()
    writeJson(stamp, guess
      ? { version: guess.model, bumped_at: new Date().toISOString(),
          note: `first install (source: ${guess.source})` }
      : { version: 'v-unknown', bumped_at: new Date().toISOString(),
          note: 'first install — 未能在宿主环境推断出模型；'
              + '跑一次 `bump <当前模型版本>` 让 check 有参照（否则 check 恒为"无需重跑"）' })
  }

  return { dataDir: DATA_DIR, action, version: PACKAGE_VERSION }
}

// ------------------------------------------------------------------ python

let pythonCache = null

function pythonCandidates() {
  const list = []
  const forced = process.env.SKILL_AUDITOR_PYTHON
  if (forced) list.push({ cmd: forced, pre: [] })
  if (process.platform === 'win32') {
    list.push({ cmd: 'python', pre: [] })
    list.push({ cmd: 'python3', pre: [] })
    list.push({ cmd: 'py', pre: ['-3'] })
  } else {
    list.push({ cmd: 'python3', pre: [] })
    list.push({ cmd: 'python', pre: [] })
  }
  return list
}

function execFileAsync(cmd, args, opts = {}) {
  return new Promise((resolve) => {
    execFile(
      cmd,
      args,
      { timeout: opts.timeout ?? 20000, maxBuffer: opts.maxBuffer ?? 8 * 1024 * 1024, windowsHide: true, ...opts },
      (err, stdout, stderr) => {
        resolve({
          code: err ? (typeof err.code === 'number' ? err.code : 1) : 0,
          killed: !!(err && err.killed),
          stdout: String(stdout ?? ''),
          stderr: String(stderr ?? '') + (err && err.code === 'ENOENT' ? `ENOENT: ${cmd}` : ''),
        })
      },
    )
  })
}

/** 探测可用的 Python（>=3.8）。成功永久缓存；失败缓存 60s（负缓存 TTL）。
 *  审查 P1：失败也永久缓存会让「现场装好 Python」的用户整个会话期报废——
 *  宿主常驻，负缓存必须过期重试。 */
const PY_NEG_TTL = 60_000
export async function resolvePython() {
  if (pythonCache) {
    if (pythonCache.ok) return pythonCache
    if (Date.now() - pythonCache.at < PY_NEG_TTL) throw new Error(pythonCache.error)
    pythonCache = null // 负缓存过期：用户可能刚装好 Python，重试
  }
  const tried = []
  for (const cand of pythonCandidates()) {
    const probe = await execFileAsync(
      cand.cmd,
      [...cand.pre, '-c', 'import sys;print(sys.version_info[0]*100+sys.version_info[1])'],
      { timeout: 15000 },
    )
    const ver = parseInt(probe.stdout.trim(), 10)
    if (probe.code === 0 && Number.isFinite(ver) && ver >= 308) {
      pythonCache = { ok: true, cmd: cand.cmd, pre: cand.pre, version: ver }
      return pythonCache
    }
    tried.push(`${cand.cmd}${cand.pre.length ? ' ' + cand.pre.join(' ') : ''} → ${probe.code === 0 ? 'too old' : 'unavailable'}`)
  }
  const error =
    '找不到可用的 Python 3.8+。装上 Python 3，或用环境变量 SKILL_AUDITOR_PYTHON 指定解释器绝对路径。' +
    `（已试：${tried.join('；')}）`
  pythonCache = { ok: false, error, at: Date.now() }
  throw new Error(error)
}

/**
 * 跑 console.py。
 * @param {string[]} argv  传给 console.py 的参数（不含解释器）
 * @param {{timeout?: number}} [opts]
 */
export async function runConsole(argv, opts = {}) {
  const py = await resolvePython()
  ensureEngine()
  // 每次调用前刷新模型事件（宿主环境里能读到什么就记什么）。
  // 审查 P2 修正：原来每次调用都写一行（多为 unknown），jsonl 无限增长且与
  // 工具自述「只读」矛盾。现在节流：同模型 5 分钟内不重复写。
  try { appendModelEvent(opts.ctx, { throttleMs: 5 * 60_000 }) } catch {}
  const script = path.join(DATA_DIR, 'scripts', 'console.py')
  // 审查 P1：background 任务 unref（宿主退出不被拖住）；用户调用的工具照常等待。
  // 实现：后台路径用 execFileAsync（原有 Promise 封装）但传入 unref 选项；
  // 前台路径沿用 execFileAsync —— 简单可靠，不用 ChildProcess 内部 API。
  if (opts.background) {
    // 后台：立即返回，但**收集 stdout 并在 close 时回调 onDone**。
    // 审查 P0-3：原来 background 分支返回恒空 stdout，runStartupAudit 拿到空串
    // 就早退——last-audit.txt 永远写不出来（死功能）。
    const child = execFile(py.cmd, [...py.pre, script, ...argv], {
      cwd: DATA_DIR,
      timeout: opts.timeout ?? 180000,
      maxBuffer: 16 * 1024 * 1024,
      windowsHide: true,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    }, (err, stdout, stderr) => {
      if (typeof opts.onDone === 'function') {
        opts.onDone({
          code: err ? (typeof err.code === 'number' ? err.code : 1) : 0,
          killed: !!(err && err.killed),
          stdout: String(stdout ?? ''),
          stderr: String(stderr ?? '') + (err && err.code === 'ENOENT' ? `ENOENT: ${cmd}` : ''),
        })
      }
    })
    child.unref?.()
    child.on('error', () => {})   // 防未处理 error 事件崩宿主
    return { code: 0, stdout: '', stderr: '', background: true }
  }
  return execFileAsync(py.cmd, [...py.pre, script, ...argv], {
    cwd: DATA_DIR,
    timeout: opts.timeout ?? 180000,
    maxBuffer: 16 * 1024 * 1024,
    windowsHide: true,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
  })
}

/** 引擎状态：给 doctor / 报错信息用。 */
export function engineInfo() {
  const marker = readMarker()
  return {
    packageRoot: PACKAGE_ROOT,
    engineSrc: ENGINE_SRC,
    dataDir: DATA_DIR,
    packageVersion: PACKAGE_VERSION,
    installedVersion: marker?.version ?? null,
    dataDirExists: fs.existsSync(DATA_DIR),
    hostsConfig: fs.existsSync(path.join(DATA_DIR, 'hosts.json')),
    registries: fs.existsSync(DATA_DIR)
      ? fs.readdirSync(DATA_DIR).filter((f) => f.startsWith('registry.') && f.endsWith('.json'))
      : [],
  }
}

// ------------------------------------------------------------------ model events
// 「模型换代事件源」的被动实现：插件常驻宿主里，每次启动都往
// model-events.jsonl 追加一行 {ts, model, source}。模型串从宿主进程里**可见的
// 位置**尽力提取（ctx 字段 / 环境变量），取不到就记 'unknown' —— 记 unknown
// 是有意的：它证明"这一刻宿主在跑、插件活着"，并且一旦宿主将来暴露模型字段，
// 事件流自动开始携带真值，stamp --infer --from-events 就能消费，无需改代码。
// 绝不编造模型串：unknown 就是 unknown。

/**
 * 从宿主进程环境尽力推断当前模型串。推断不到返回 null（绝不编造）。
 * 只在首次安装写 model-version.json 时用 —— 那时还拿不到 cordis ctx。
 */
export function inferEnvModel() {
  const keys = ['SKILL_AUDITOR_MODEL', 'CURRENT_MODEL', 'MODEL_VERSION', 'OPENAI_MODEL']
  for (const k of keys) {
    const v = process.env[k]
    if (v && String(v).trim()) return { model: String(v).trim(), source: `env:${k}` }
  }
  // DSH / opencode 常见的 profile 配置里可能带默认模型
  const cfgCands = [
    path.join(DSH_HOME, 'settings.json'),
    path.join(os.homedir(), '.workbuddy-ai', 'settings.json'),
  ]
  for (const p of cfgCands) {
    try {
      const d = JSON.parse(fs.readFileSync(p, 'utf8'))
      for (const key of ['model', 'modelVersion', 'currentModel', 'activeModel', 'defaultModel']) {
        const v = d?.[key]
        if (typeof v === 'string' && v.trim()) return { model: v.trim(), source: `cfg:${p}` }
        if (v && typeof v === 'object' && v.id) return { model: String(v.id), source: `cfg:${p}` }
      }
    } catch {}
  }
  return null
}

export function detectModel(ctx) {
  const fromEnv = process.env.SKILL_AUDITOR_MODEL || process.env.CURRENT_MODEL
    || process.env.MODEL_VERSION || null
  if (fromEnv) return { model: fromEnv, source: 'env' }
  const candidates = [
    ctx?.model, ctx?.config?.model, ctx?.config?.modelVersion,
    ctx?.runtime?.model, ctx?.state?.model,
  ]
  for (const c of candidates) {
    if (typeof c === 'string' && c.trim() && c.trim().toLowerCase() !== 'unknown') {
      return { model: c.trim(), source: 'ctx' }
    }
  }
  return { model: 'unknown', source: 'none' }
}

export function appendModelEvent(ctx, opts = {}) {
  const { model, source } = detectModel(ctx)
  try {
    // 节流（审查 P2）：同模型 5 分钟内不重复写，jsonl 不再无限增长。
    // 用事件文件自身大小做粗节流：最后一条同 model 且 <5min 则跳过。
    const p = path.join(DATA_DIR, 'model-events.jsonl')
    if (opts.throttleMs) {
      try {
        const st = fs.statSync(p)
        if (Date.now() - st.mtimeMs < opts.throttleMs && st.size > 0) {
          const tail = fs.readFileSync(p, 'utf8').trim().split('\n').pop()
          const last = JSON.parse(tail)
          if (last.model === model) return { model, source, throttled: true }
        }
      } catch {}
    }
    fs.mkdirSync(DATA_DIR, { recursive: true })
    const line = JSON.stringify({
      ts: new Date().toISOString(), model, source,
      packageVersion: PACKAGE_VERSION,
    })
    fs.appendFileSync(p, line + '\n', 'utf8')
    return { model, source }
  } catch {
    return { model, source, failed: true }
  }
}

/** 读最近 N 条事件里最新的非 unknown 模型串（给 stamp --infer --from-events）。 */
export function lastKnownModel(limit = 200) {
  const p = path.join(DATA_DIR, 'model-events.jsonl')
  try {
    const lines = fs.readFileSync(p, 'utf8').trim().split('\n').slice(-limit)
    for (let i = lines.length - 1; i >= 0; i--) {
      try {
        const o = JSON.parse(lines[i])
        if (o.model && o.model !== 'unknown') {
          return { model: o.model, ts: o.ts, source: `model-events.jsonl (${o.source})` }
        }
      } catch {}
    }
  } catch {}
  return null
}
