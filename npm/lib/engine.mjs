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

/** 引擎里会被复制到数据目录的条目（白名单，逐条列举，不递归整个包）。 */
const ENGINE_DIRS = ['scripts']
const ENGINE_FILES = [
  'SKILL.md',
  'USAGE.md',
  'README.md',
  'LICENSE',
  'hosts.example.json',
  'golden/_template.json',
]
/** 引擎代码：每次升级都覆盖。 */
const CODE_DIRS = new Set(['scripts'])
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
    for (const dir of ENGINE_DIRS) {
      const src = path.join(ENGINE_SRC, dir)
      if (!fs.existsSync(src)) continue
      const dst = path.join(DATA_DIR, dir)
      // 先删后拷：`cpSync(force)` 只覆盖同名文件，新版本**删掉**的旧脚本会残留下来，
      // 后续 import 可能拿到旧模块（改名/拆分的版本最容易踩）。只删引擎目录，不碰用户数据。
      if (fs.existsSync(dst)) fs.rmSync(dst, { recursive: true, force: true })
      fs.cpSync(src, dst, { recursive: true })
    }
    for (const rel of ENGINE_FILES) {
      const src = path.join(ENGINE_SRC, rel)
      if (!fs.existsSync(src)) continue
      const dst = path.join(DATA_DIR, rel)
      if (SEED_ONLY.has(rel) && fs.existsSync(dst)) continue
      fs.mkdirSync(path.dirname(dst), { recursive: true })
      fs.copyFileSync(src, dst)
    }
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
    writeJson(stamp, {
      version: 'v-unknown',
      bumped_at: new Date().toISOString(),
      note: 'first install — 跑一次 `bump <当前模型版本>` 让 check 有参照',
    })
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

/** 探测可用的 Python（>=3.8）。结果缓存，失败也缓存，避免每次调用都试 3 个命令。 */
export async function resolvePython() {
  if (pythonCache) {
    if (pythonCache.ok) return pythonCache
    throw new Error(pythonCache.error)
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
  pythonCache = { ok: false, error }
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
  const script = path.join(DATA_DIR, 'scripts', 'console.py')
  const res = await execFileAsync(py.cmd, [...py.pre, script, ...argv], {
    cwd: DATA_DIR,
    timeout: opts.timeout ?? 180000,
    maxBuffer: 16 * 1024 * 1024,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
  })
  return res
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
