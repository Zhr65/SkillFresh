# skill-auditor 常驻评测台 · 使用与验证指南

元 skill（SKILL.md）= 对话里的方法论；本目录的 `scripts/console.py` = 常驻数据层 + 执行层。
折旧表不再靠手动触发：`model-version.json` 是「模型版本戳」，`console.py check` 对比注册表里的
上次回归版本，版本变了就提示重跑。

## 日常用法（4 条命令）

```bash
PY=python                                    # 换成你自己的解释器（需 3.8+）
C="scripts/console.py"                       # 在仓库根目录执行

"$PY" "$C" --host workbuddy setup --model <当前模型版本>   # 首次安装：初始化版本戳 + golden 模板
"$PY" "$C" --host workbuddy scan        # 扫描 skill 目录 -> 更新 registry.<host>.json（指纹+基线+风险）
"$PY" "$C" --host workbuddy status      # 看折旧表：skill | 指纹 | 基线通过率 | risk | golden | 状态
"$PY" "$C" --host workbuddy recommend   # 推荐/淘汰面板（强/观察/未验证/降级淘汰/精简候选/建议新增）
"$PY" "$C" --host workbuddy check       # 检测模型版本是否变更；变了 -> 提示重跑回归三步
"$PY" "$C" --host deepseek probe       # 运行时证据：读宿主日志，找「装得像、跑不起来」的插件
"$PY" "$C" --host workbuddy suggest --need "<对话里的需求>"   # 无匹配 skill 时按需求推荐安装项
"$PY" "$C" discover --write                  # 自动探测本机宿主并写回 hosts.json
"$PY" "$C" --host <h> bootstrap              # 从本地历史生成 golden 草稿
"$PY" "$C" --host <h> onboard                # discover + scan + bootstrap 一键
```

> 想先看效果不建数据：`python demo.py`（沙盒，约 13 秒，`--clean` 清掉）。

注意：`scan` 内部会 spawn 子进程调 fingerprint.py，个别沙箱环境需放开执行；
`status / check / recommend / bump / report` 无子进程，直接跑。

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
"$PY" "$C" discover --write                      # 探测本机 WorkBuddy / Claude Code / DeepSeek / agents，写回 hosts.json
"$PY" "$C" --host workbuddy scan                 # 建注册表
"$PY" "$C" --host workbuddy bootstrap --per-asset 5   # 从本地历史生成 golden 草稿
# 或一键：
"$PY" "$C" --host workbuddy onboard
```

`bootstrap` 读本机会话 transcript（`~/.workbuddy-ai/projects`、`~/.claude/projects`、`~/.dsh/sessions`），把真实用户请求匹配到对应资产，生成 `golden/<asset>.json` 草稿（`"draft": true`）。**草稿的 expected/judge 是占位符，必须人工补完再跑 baseline**，否则等于没基线。

## 模型升级后怎么跑（真正的闭环）

```bash
"$PY" "$C" bump <新模型版本>     # 平台在真实场景会自动调这一步（模拟模型切换事件）
"$PY" "$C" check                # 确认检测到变更，得到重跑指引
# 1) 用新模型重跑 golden set（K>=3），判定写进 new-results.json（格式同 recorded-baseline.json）
# 2) 汇聚 + 出折旧报告：
"$PY" "$C" report --results new-results.json --threshold 20
"$PY" "$C" scan                 # 把新基线写回 registry，折旧表更新
"$PY" "$C" recommend            # 看「🔴 已降级 · 淘汰候选」是否出现
```

判定规则（regress.py 固化）：同一 golden set 通过率下降 ≥20pp 且方向一致 → DEGRADATION（带归因）。

## 推荐安装 / 淘汰的交互方式

面板每行带动作 id（`[keep:名称]` `[prune:名称]` `[install:slug]` 等）。
对 Agent 说「keep:github」「prune:humanizer」「install:agent-evals」即可；**淘汰与安装都先确认再执行，
绝不静默操作**。淘汰优先级：先「停用/归档」保留恢复入口，确认无用才删除。

## 无匹配时按需求推荐（suggest）

当对话里出现你名下没有任何 skill 覆盖的需求（新用户零 skill 时尤其常见），不要直接说做不了：

1. `console.py suggest --need "<需求>"`；
2. 有覆盖 → 输出「✓ 你已有可用 skill：<名称>」，直接用，不装新东西；
3. 无覆盖 → 输出「[install:<类别>] 建议新增 + 搜索词」，再由 Agent 去技能市场搜真实候选、给证据
   （做什么 / 为何契合 / 来源），**用户确认后才安装**；
4. 装完 `scan` 一次，让它进入指纹 + 基线监测。

实测示例：
- `suggest --need "帮我做一份季度财务分析报告"` → `[install:finance] 金融/财报/估值分析 skill`
- `suggest --need "帮我把这段文字去一下AI味"` → `✓ 你已有可用 skill：humanizer`
- `suggest --need "帮我规划一次线下婚礼流程"` → 未匹配到现成类别，建议先用内置能力试做

## 怎么验证它真的在工作（一次性自检清单）

1. `status` 应显示 21 个 skill，其中 github/frontend-dev/writing-skills 带 `Y`（有 golden），
   基线 3/3、5/5、5/5 全通过 —— 对应 `baseline-results.json` 的 13 条真实 verdict。
2. `bump v-test` 后 `check` 应输出 `⚠ 检测到模型版本变更`（退出码 2）；再 `bump v-current-2026-10-17`
   恢复后 `check` 应输出 `✓ 无需重跑`。
3. `recommend` 应出现：🟢 github、🟡 frontend-dev/writing-skills、⚪ 16 个未验证、
   🔴 降级候选为空（诚实：还没跑过升级回归，没人真的"落后"）、🔶 humanizer/subagent-driven-development
   （无基线+维护面极高，仅供评估）。
4. `report` 重跑应输出三个 skill 各 n/a drop（只有基线、无对比模型，符合预期）。

## 生产化缺口（如实记录）

- **模型切换事件源**：现在是 `bump` 手动模拟；生产应由平台在真实切模型时自动调 `bump`（或写
  `model-version.json`），再加一个定时任务跑 `check` 即为全自动。
- **risk 启发式**：行数+工具依赖+捆绑代码的代理分，待接入真实用量遥测（调用次数/采纳率/token）
  后替换为净收益公式。
- **K 次重跑**：当前每案例 K=1；生产基线 K≥3（LLM-judge 评分类 K≥5）。
- **golden set 覆盖**：21 个 skill 中 3 个有 golden set；`prune-candidate` 里那两个
  （humanizer / subagent-driven-development）下一步优先补样本，别急着淘汰。
