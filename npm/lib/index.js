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
import { ensureEngine, engineInfo, resolvePython, runConsole, appendModelEvent } from './engine.mjs'

const PLUGIN_NAME = 'skill-auditor'

/** 本宿主的默认审计对象是「插件」，不是 skill —— 在 DSH 里插件才是安装单元。 */
function defaultHost() {
  return process.env.SKILL_AUDITOR_HOST || 'deepseek'
}

function buildArgv(cmd, args, extra = [], positionalAfterCmd = false) {
  const host = (args && args.host) || defaultHost()
  const root = args && args.root
  const argv = ['--host', String(host)]
  if (root) argv.push('--root', String(root))
  argv.push(cmd)
  // 位置参数（task/query）以 '-' 开头时会被 argparse 当选项（审查 P2），
  // 在它们前插 '--' 终止选项解析。
  if (positionalAfterCmd && extra.length) argv.push('--')
  argv.push(...extra)
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
  const positional = !!opts.positional
  let res = await runConsole(buildArgv(cmd, args, extra, positional), opts)
  let note = ''
  if (res.code !== 0 && isMissingRegistry(`${res.stdout}${res.stderr}`)) {
    const host = (args && args.host) || defaultHost()
    const scan = await runConsole(buildArgv('scan', args), { timeout: opts.timeout })
    if (scan.code !== 0) return formatFailure(scan, `自动扫描 ${host} 资产`)
    note = `（首次使用：已自动扫描 ${host} 资产并写入 registry.${host}.json）\n\n`
    res = await runConsole(buildArgv(cmd, args, extra, positional), opts)
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
    'skill_audit_find',
    '【用户忘了插件名时调】按功能找已装的 skill/插件并返回「该调哪个工具」。用户说「我记得有个能查余额的」「那个记忆插件叫啥」「有没有能操作网页的」——提到某个功能但没说名字——就用功能描述调它。返回：资产名 + 它注册的工具名（如 hindsight_capture_initiative）+ 怎么调用。拿到工具名后**直接调用该工具**，不要只把名字念给用户。纯 UI 插件会标明「无工具，让用户在界面里用」。只读。',
    {
      query: { type: 'string', description: '功能描述，如「查余额」「记忆」「打开网页」「画图表」' },
      host: HOST_PARAM,
      root: ROOT_PARAM,
    },
    ['query'],
    async (args) => {
      const q = String((args && args.query) || '').trim()
      if (!q) return '缺少 query 参数：把用户提到的功能用一句话描述传进来。'
      return runWithAutoScan('find', args, [q], { timeout: 120000, positional: true })
    },
  ),
  tool(
    'skill_audit_can',
    '【动手前先调这个】判断「这件事我能不能做」：用户提出一个具体任务（做 PPT / 查 GitHub / 处理表格 / 自动化浏览器 / 去 AI 味 / 设计界面…）时，先用任务描述调它，确认有没有已装资产能覆盖、以及它们现在是否可用。返回三种结论并给出该怎么做：① 能做 → 直接干活，不要向用户汇报「我查过了」；② 有但坏了 → 先告诉用户是什么问题，别假装能做；③ 缺能力 → 一句话推荐（含安装量/值不值得装）或直接用内置能力做初版，**不要直接说做不了**。只读、不装不删。',
    {
      task: { type: 'string', description: '任务的自然语言描述，如「把这份财报做成 PPT」「去 GitHub 查项目」' },
      market: { type: 'string', description: '填 "1" 表示本地没有时去市场搜候选（需网络，慢一点）' },
      host: HOST_PARAM,
      root: ROOT_PARAM,
    },
    ['task'],
    async (args) => {
      const task = String((args && args.task) || '').trim()
      if (!task) return '缺少 task 参数：把用户的任务用一句话描述传进来。'
      const extra = [task]
      if (String(args && args.market) === '1') extra.push('--market')
      return runWithAutoScan('can', args, extra, { timeout: 120000, positional: true })
    },
  ),
  tool(
    'skill_audit_status',
    '【诊断用】列出每个已装资产的能力指纹、基线通过率、K、risk、是否有 golden set，以及状态（strong/watch/unverified/degraded/blocked）。用户问「我装的东西哪些在退化/哪些没测过」时用。日常任务开始前应该用 skill_audit_can，不是这个。',
    { host: HOST_PARAM, root: ROOT_PARAM },
    [],
    async (args) => runWithAutoScan('status', args, [], { timeout: 180000 }),
  ),
  tool(
    'skill_audit_recommend',
    '【诊断用】推荐/淘汰面板：强/观察/未验证/跑不起来·环境问题/已降级/可选精简 六档 + 建议新增。用户明确问「哪个该删/该留」时用。淘汰只是建议，绝不自动执行。',
    { host: HOST_PARAM, root: ROOT_PARAM },
    [],
    async (args) => runWithAutoScan('recommend', args, [], { timeout: 180000 }),
  ),
  tool(
    'skill_audit_check',
    '【模型换代后调】检查当前模型相对上次回归基线是否变了。变了会给出重跑步骤；模型戳没设置时返回「没法判断」而不是「无需重跑」。',
    { host: HOST_PARAM, root: ROOT_PARAM },
    [],
    async (args) => runWithAutoScan('check', args, [], { timeout: 60000 }),
  ),
  tool(
    'skill_audit_scan',
    '【装了/删了东西之后调】重新发现资产并写 registry.<host>.json。记录能力指纹；指纹变化标注为「已改动」而非「模型退化」。',
    { host: HOST_PARAM, root: ROOT_PARAM },
    [],
    async (args) => {
      const res = await runConsole(buildArgv('scan', args), { timeout: 180000 })
      if (res.code !== 0) return formatFailure(res, '扫描资产')
      return clean(res.stdout)
    },
  ),
  tool(
    'skill_audit_probe',
    '【排查「装了但不工作」时调】读宿主日志，找出「文件齐全、manifest 正确、但跑起来报错」的插件（依赖 CLI 缺失、路由起不来等）。只读。盲区：插件不写日志就看不见。',
    { host: HOST_PARAM },
    [],
    async (args) => {
      const argv = buildArgv('probe', args)
      const res = await runConsole(argv, { timeout: 60000 })
      // probe 默认退出码 0；只有 --strict 才非零。非零时也要把正文给出去。
      // probe 正常退出码是 0（审查 P2：原条件要求 code!==0 永假，auto-scan 是死代码）。
      // 改为只看文本：registry 缺失就自动 scan 一次再跑。
      if (/registry\.[\w.-]+\.json missing/.test(`${res.stdout}${res.stderr}`)) {
        return runWithAutoScan('probe', args, [], { timeout: 60000 })
      }
      return clean(`${res.stdout}${res.stderr}`) || formatFailure(res, '运行时证据采集')
    },
  ),
  tool(
    'skill_audit_run',
    '主动体检：一次给出全部结论——哪些该修（跑不起来/通过率低）、哪些该升级（市场有新版）、哪些正常、哪些没测过。对比已装版本与 npm registry 最新版，并读取运行时故障。只读，不卸载不升级。用户说「帮我看看插件有没有问题/该删哪个/要不要更新」时首选这个。',
    { host: HOST_PARAM },
    [],
    async (args) => {
      const res = await runConsole(buildArgv('audit', args), { timeout: 180000 })
      if (res.code !== 0) return formatFailure(res, '主动体检')
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
  // 模型事件源（被动）：宿主每次启动都留痕。模型串现在多半是 unknown
  // （DSH 不把模型暴露给插件 ctx），但事件流一旦出现真值，stamp --infer
  // --from-events 就能自动消费 —— 这是「手动 bump」走向自动的过渡设计。
  try {
    const ev = appendModelEvent(ctx)
    ctx.logger?.info?.(`${PLUGIN_NAME}: model event -> ${ev.model} (${ev.source})`)
  } catch {}
  // 装上就开始体检：后台异步跑一次 audit，结论写进 DATA_DIR/last-audit.txt。
  // **不阻塞启动、不自动卸载、不自动升级** —— 只报告，动作由用户决定。
  runStartupAudit(ctx)
}

/** 启动体检：不 await、失败只记日志。
 *  审查 P1：timer 与子进程都 unref —— 宿主自然退出不被拖住（原来挂到 audit
 *  结束最长 ~2 分钟）；宿主强制退出时 Python 子进程随之释放（不再孤儿化）。 */
function runStartupAudit(ctx) {
  const timer = setTimeout(async () => {
    try {
      ensureEngine()
      const res = await runConsole(['--host', defaultHost(), 'audit'],
                                   { timeout: 120000, background: true })
      const text = String(res.stdout || '')
      if (!text.trim()) return
      try {
        const fs = await import('node:fs')
        const path = await import('node:path')
        const { DATA_DIR } = await import('./engine.mjs')
        fs.writeFileSync(path.join(DATA_DIR, 'last-audit.txt'), text, 'utf8')
      } catch {}
      const needsAction = /🔴|⬆️/.test(text)
      // 「查不了」不能说成「没问题」。下面三种情况都是"没数据"，必须单独说。
      const blind = []
      if (/版本面没查到任何数据/.test(text)) blind.push('版本不可查')
      if (/没有可读日志/.test(text)) blind.push('无日志')
      if (/全都没有基线/.test(text)) blind.push('无基线')
      const summary = (text.match(/🔴[^\n]*|⬆️[^\n]*|✅[^\n]*/g) || []).join(' / ')
      const label = needsAction
        ? '有待处理项'
        : (blind.length ? `结论不完整（${blind.join('/')}）` : '未发现异常')
      ctx.logger?.[needsAction ? 'warn' : (blind.length ? 'info' : 'info')]?.(
        `${PLUGIN_NAME}: 启动体检 ${label} — ${summary || '详见 last-audit.txt'}`,
      )
    } catch (err) {
      ctx.logger?.info?.(`${PLUGIN_NAME}: 启动体检跳过（${err && err.message}）`)
    }
  }, 8000)
  timer.unref?.()
}

export default { name: PLUGIN_NAME, inject, apply }
