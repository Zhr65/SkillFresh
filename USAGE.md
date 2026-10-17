# skill-auditor 常驻评测台 · 使用与验证指南

元 skill（SKILL.md）= 对话里的方法论；`scripts/console.py` = 常驻数据层 + 执行层。
每个宿主有自己的模型版本戳 `model-version.<host>.json`（旧版单一 `model-version.json` 仍兼容读取），
`console.py check` 对比注册表里的上次回归版本，版本变了就提示重跑。

## 常用命令

```bash
PY=python                                    # 换成你自己的解释器（需 3.8+）
C="scripts/console.py"                       # 在仓库根目录执行

"$PY" "$C" --host workbuddy setup --model <当前模型版本>   # 首次安装：每个宿主一个版本戳 + golden 模板
"$PY" "$C" --host workbuddy scan        # 扫描 -> registry.<host>.json（指纹 + 能力面 + 基线 + 维护面）
"$PY" "$C" --host workbuddy status      # 折旧表：asset | 指纹 | 基线 | risk | caps | golden | 状态
"$PY" "$C" --host workbuddy recommend   # 推荐/淘汰面板（强/观察/未验证/降级/精简/建议新增）
"$PY" "$C" --host workbuddy check       # 模型戳 vs 上次回归；变了 -> 提示重跑三步（退出码 2）
"$PY" "$C" --host deepseek probe        # 运行时证据：读宿主日志，找「装得像、跑不起来」的插件
"$PY" "$C" --host workbuddy suggest --need "<对话里的需求>"   # 无匹配时按需求推荐
"$PY" "$C" discover --write             # 自动探测本机宿主并写回 hosts.json
"$PY" "$C" --host <h> bootstrap         # 从本地历史生成 golden 草稿（draft）
"$PY" "$C" --host <h> onboard           # discover + scan + bootstrap 一键
```

全局 `--out <dir>` 会把 registry / baseline / 模型戳 / golden 模板一起放到该目录——不再「只搬一半」。

> 想先看效果不建数据：`python demo.py`（沙盒，几秒，`--clean` 清掉）。demo 每一步都检查退出码，失败会停。

## 多宿主（skill / 插件）

同一引擎，按 `--host` 选适配器：

```bash
"$PY" "$C" hosts                                  # 列出宿主 + 探测资产数
"$PY" "$C" --host workbuddy scan                  # WorkBuddy skill
"$PY" "$C" --host claude-code scan                # Claude Code skill
"$PY" "$C" --host deepseek --root <插件目录> scan  # DeepSeek harness 插件
"$PY" "$C" --host deepseek status                 # 插件折旧表
```

每个宿主写各自的 `registry.<host>.json`，互不覆盖；baseline / golden 共享。
加新宿主：在 `hosts.json` 加一条 `{kind, label, root, manifests}` 即可，无需改代码。

## 给别人用：新用户三步

别人装上后，缺的是「路径」和「基线数据」，三个命令解决：

```bash
"$PY" "$C" discover --write                      # 探测 WorkBuddy / Claude Code / DeepSeek / agents，写回 hosts.json
"$PY" "$C" --host workbuddy scan                 # 建注册表
"$PY" "$C" --host workbuddy bootstrap --per-asset 5   # 从本地历史生成 golden 草稿
# 或一键：
"$PY" "$C" --host workbuddy onboard
```

`bootstrap` 读本机会话 transcript（`~/.workbuddy-ai/projects`、`~/.claude/projects`、`~/.dsh/sessions`），
把真实用户请求匹配到对应资产，生成 `golden/<asset>.json` 草稿（`"draft": true`）。
**草稿的 expected/judge 是占位符，必须人工补完再跑 baseline**；`baseline.py` 默认会跳过 draft 用例
（除非加 `--allow-draft`），避免把占位符当成真基线。

## 模型升级后怎么跑（真正的闭环）

```bash
"$PY" "$C" --host workbuddy bump <新模型版本>   # 平台在真实场景应自动调这一步（按宿主写戳）
"$PY" "$C" --host workbuddy check               # 确认检测到变更，得到重跑指引
# 1) 用新模型重跑 golden set（K>=3），判定写进 new-results.json
# 2) 出报告，并把结果合并进基线（关键：不加 --promote 就只是报告）
"$PY" "$C" --host workbuddy report --results new-results.json --promote
# 3) 重扫，让折旧表/面板看到新判定
"$PY" "$C" --host workbuddy scan
"$PY" "$C" --host workbuddy recommend           # 看「已降级 · 淘汰候选」是否出现
```

`--promote` 的合并是**按资产**的：本次跑到的资产替换，没跑到的保留并打印出来。
所以只补一个 skill 的基线是安全操作，不会再把其他资产清空。

判定规则（regress.py 固化）：同一 golden set 通过率下降 ≥20pp，**且没有任何用例变好** → DEGRADATION（带归因）。
跨过阈值但混有变好的用例，只算 `volatility watch`（churn 不等于回归）。

`scan` 只统计**当前模型戳**下的判定。换代后没重跑的资产会回到 `unverified`，并打印
「N 条基线属于旧模型，未计入」——这是有意的，避免旧模型的 100% 冒充新模型绿灯。

## 推荐安装 / 淘汰的交互方式

面板每行带动作 id（`[keep:名称]` `[prune:名称]` `[install:slug]` 等）。
对 Agent 说「keep:github」「prune:humanizer」「install:agent-evals」即可；**淘汰与安装都先确认再执行，
绝不静默操作**。淘汰优先级：先「停用/归档」保留恢复入口，确认无用才删除。

## 无匹配时按需求推荐（suggest）

当对话里出现你名下没有任何 skill 覆盖的需求（新用户零 skill 时尤其常见），不要直接说做不了：

1. `console.py suggest --need "<需求>"`；
2. 有覆盖 → 输出「你已有可用 skill：<名称>」，直接用，不装新东西；
3. 无覆盖 → 输出「[install:<类别>] 建议新增 + 搜索词」，再由 Agent 去技能市场搜真实候选、给证据
   （做什么 / 为何契合 / 来源），**用户确认后才安装**；
4. 装完 `scan` 一次，让它进入指纹 + 基线监测。

名称匹配既认完整资产名，也认名称片段（`frontend-dev` 能被「做个前端页面」命中），比早期只认全串实用。

实测示例：
- `suggest --need "帮我做一份季度财务分析报告"` → `[install:finance]`
- `suggest --need "帮我把这段文字去一下AI味"` → 已有 humanizer
- `suggest --need "帮我规划一次线下婚礼流程"` → 未匹配到现成类别，建议先用内置能力试做

## 怎么验证它真的在工作（自检清单）

数字随本机资产变化，下面以当前机器为例（22 个 WorkBuddy skill + 4 个 DSH 插件）：

1. `status` 应显示 22 个 skill；github / frontend-dev / humanizer / writing-skills 带 `Y`（有 golden），
   基线分别是 3/3、5/5、5/5、5/5 —— 对应 `baseline-results.json` 里的 18 条 WorkBuddy verdict
   （全库 21 条，含插件的 3 条）。其余 18 个为 unverified。
2. `bump v-test` 后 `check` 应输出检测到变更（退出码 2）；`bump` 回 `v-current-2026-10-17` 后
   `check` 应输出「无需重跑」。`bump` 只写当前宿主的戳，不会污染另一个宿主。
3. `recommend` 应出现：强 1 个（github）、观察 3 个、未验证 18 个、降级候选为空（还没跑过升级回归）、
   精简候选 1 个（subagent-driven-development，无基线 + 维护面高）。
4. 升级后没重跑时 `status` 会全部回到 unverified，并提示「N 条基线属于旧模型，未计入」——这是诚实行为。

## 生产化缺口（如实记录）

- **模型切换事件源**：现在仍是 `bump` 手动模拟（已按宿主分开）。生产应由平台在真实切模型时自动写戳，
  再加一个定时任务跑 `check` 才算全自动；否则周度 check 只会一直报「无变更」。
- **risk 启发式**：行数 + 工具依赖 + 捆绑代码的代理分，不代表价值；待接入真实用量遥测
  （调用次数 / 采纳率 / token）后替换为净收益公式。
- **K 次重跑**：当前每案例 K=1、由同一模型自评；生产基线 K≥3（LLM-judge 评分类 K≥5）。
- **golden set 覆盖**：22 个 skill 里 4 个有 golden，4 个插件里 1 个有真基线；
  两个插件 golden（hindsight / workbuddy-connect）仍是 draft，不计入基线。
- **插件运行时**：`probe` 只能看见日志里记录过的故障；没有主动探针（DSH desktop profile 被 Electron 独占）。
