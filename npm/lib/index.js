/**
 * dsh-skill-auditor — DSH (DeepSeek Harness) 插件入口。
 *
 * 干的事：把「能力折旧表」引擎挂到 host plane 的 tools 注册面，
 * 让每个会话都能查自己的 skill / 插件有没有退化、该留该删。
 *
 * 只用两个宿主 API：ctx.inject(['tools'], cb) 和 tools.register({...})。
 * 不碰 webServer / credentials / connection —— 不注入前端、不读凭据、不开路由。
 * 引擎是本地 Python 子进程，无网络调用（bootstrap 例外，见 SKILL.md）。
 */
import { ensureEngine, engineInfo, resolvePython, runConsole } from './engine.mjs'

const PLUGIN_NAME = 'skill-auditor'

/** 本宿主的默认审计对象是「插件」，不是 skill —— 在 DSH 里插件才是安装单元。 */
function defaultHost() {
  return process.env.SKILL_AUDITOR_HOST || 'deepseek'
}

function buildArgv(cmd, args, extra = []) {
  const host = (args && args.host) || defaultHost()
  const root = args && args.root
  const argv = ['--host', String(host)]
  if (root) argv.push('--root', String(root))
  argv.push(cmd, ...extra)
  return argv
}

function clean(text) {
  return String(text ?? '')
    .replace(/\r\n/g, '\n')
    .replace(/^\n+/, '')
    .replace(/\s+$/, '')
}

function isMissingRegistry(out) {
  return /registry\.[\w.-]+\.json missing/.test(out)
}

function formatFailure(res, what) {
  const body = clean(`${res.stdout}\n${res.stderr}`) || '(无输出)'
  const hints = []
  if (res.killed) hints.push('超时被杀；资产很多时可先只查一个宿主，或用 --host 缩小范围。')
  if (/ENOENT/.test(res.stderr)) hints.push('找不到 Python 解释器，见下方提示。')
  if (res.code !== 0 && !hints.length) hints.push('这是引擎的非零退出；先看输出里的 [err] 行。')
  return `${what} 失败（exit ${res.code}）\n\n${body}${hints.length ? '\n\n可能原因：\n- ' + hints.join('\n- ') : ''}`
}

/**
 * 跑一条命令；注册表缺失时自动补一次 scan（这是最常见的首次使用状态）。
 * 自动 scan 只写 registry.<host>.json，不动任何资产，也不删东西。
 */
async function runWithAutoScan(cmd, args, extra, opts = {}) {
  let res = await runConsole(buildArgv(cmd, args, extra), opts)
  let note = ''
  if (res.code !== 0 && isMissingRegistry(`${res.stdout}${res.stderr}`)) {
    const host = (args && args.host) || defaultHost()
    const scan = await runConsole(buildArgv('scan', args), { timeout: opts.timeout })
    if (scan.code !== 0) return formatFailure(scan, `自动扫描 ${host} 资产`)
    note = `（首次使用：已自动扫描 ${host} 资产并写入 registry.${host}.json）\n\n`
    res = await runConsole(buildArgv(cmd, args, extra), opts)
  }
  if (res.code !== 0) return formatFailure(res, `执行 ${cmd}`)
  return note + clean(res.stdout)
}

function tool(name, description, properties, required, handler) {
  return {
    name,
    description,
    parameters: {
      type: 'object',
      properties,
      ...(required.length ? { required } : {}),
    },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: String(value ?? '') }],
    },
    execute: handler,
  }
}

const HOST_PARAM = {
  type: 'string',
  description:
    '宿主 profile：deepseek（默认，审计已安装插件）| workbuddy | claude-code | agents | generic。' +
    'generic 需要同时给 root。',
}
const ROOT_PARAM = {
  type: 'string',
  description: '资产根目录（覆盖宿主 profile 里的默认路径）。仅在要审计非标准目录时使用。',
}

const TOOLS = [
  tool(
    'skill_audit_status',
    '能力折旧表：列出该宿主下每个已装 skill/插件的能力指纹、基线通过率、维护面 risk、是否有 golden set，以及判定（strong / watch / unverified / degraded）。只读，不改任何文件。想知道「我装的东西哪些在退化」时先调这个。',
    { host: HOST_PARAM, root: ROOT_PARAM },
    [],
    async (args) => runWithAutoScan('status', args, [], { timeout: 180000 }),
  ),
  tool(
    'skill_audit_recommend',
    '推荐/淘汰面板：把资产分成「强（保留/推荐）」「观察」「未验证」「已降级·淘汰候选」「可选精简」五档，并给出可执行的建议新增项。淘汰与安装都只是**建议**，绝不自动执行。',
    { host: HOST_PARAM, root: ROOT_PARAM },
    [],
    async (args) => runWithAutoScan('recommend', args, [], { timeout: 180000 }),
  ),
  tool(
    'skill_audit_check',
    '检查当前模型版本是否已变更（相对上次回归基线）。变更时会给出「用新模型重跑 golden set → report → scan」的三步操作，并说明为什么不能直接判定为退化。只读。',
    { host: HOST_PARAM, root: ROOT_PARAM },
    [],
    async (args) => runWithAutoScan('check', args, [], { timeout: 60000 }),
  ),
  tool(
    'skill_audit_scan',
    '重新发现该宿主下的资产并写入 registry.<host>.json（新增/删除了 skill 或插件之后跑）。会记录每个资产的能力指纹，指纹变化会被标注为「已改动」而不是「模型退化」。',
    { host: HOST_PARAM, root: ROOT_PARAM },
    [],
    async (args) => {
      const res = await runConsole(buildArgv('scan', args), { timeout: 180000 })
      if (res.code !== 0) return formatFailure(res, '扫描资产')
      return clean(res.stdout)
    },
  ),
  tool(
    'skill_audit_suggest',
    '需求匹配：当用户当前的活儿没有任何已装 skill/插件能覆盖时，用一句话描述需求，返回该不该新增、新增什么、以及搜索词。先查已装注册表（避免重复装），查不到才落到分类表。',
    {
      need: { type: 'string', description: '一句话描述需求，例如「把 pdf 批量转成 word」' },
      host: HOST_PARAM,
      root: ROOT_PARAM,
    },
    ['need'],
    async (args) => {
      const need = String((args && args.need) || '').trim()
      if (!need) return '缺少 need 参数：用一句话描述用户的需求。'
      const res = await runConsole(buildArgv('suggest', args, ['--need', need]), { timeout: 60000 })
      if (res.code !== 0) return formatFailure(res, '需求匹配')
      return clean(res.stdout)
    },
  ),
]

/** 每个工具都先确保引擎就位；失败时把数据目录一起报出来，便于排查。 */
function wrap(t) {
  return {
    ...t,
    async execute(args, exec) {
      try {
        ensureEngine()
        return await t.execute(args, exec)
      } catch (err) {
        let info = {}
        try {
          info = engineInfo()
        } catch {}
        try {
          await resolvePython()
        } catch (pyErr) {
          return `${PLUGIN_NAME}: ${pyErr.message}`
        }
        return (
          `${PLUGIN_NAME} 执行失败：${err && err.message ? err.message : String(err)}\n` +
          `数据目录：${info.dataDir || '(未初始化)'}\n` +
          `包版本：${info.packageVersion || '?'}  已装引擎：${info.installedVersion || '(无)'}`
        )
      }
    },
  }
}

export const name = PLUGIN_NAME
/** 与 @vectorize-io/hindsight-coding-agents/dsh 同一形态：字符串数组 = 必需服务列表。 */
export const inject = ['tools']

export function apply(ctx) {
  const register = (toolCtx) => {
    for (const t of TOOLS) {
      try {
        toolCtx.tools.register(wrap(t))
      } catch (err) {
        ctx.logger?.warn?.(`${PLUGIN_NAME}: 注册工具 ${t.name} 失败 — ${err && err.message}`)
      }
    }
  }
  // 局部 inject：apply 立刻执行（不拖到所有服务就绪），工具就绪后再注册。
  ctx.inject(['tools'], register)
}

export default { name: PLUGIN_NAME, inject, apply }
