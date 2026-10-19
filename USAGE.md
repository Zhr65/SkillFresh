# skill-auditor 常驻评测台 · 使用与验证指南

元 skill（SKILL.md）= 对话里的方法论；`scripts/console.py` = 常驻数据层 + 执行层。
每个宿主有自己的模型版本戳 `model-version.<host>.json`（旧版单一 `model-version.json` 仍兼容读取），
`console.py check` 对比注册表里的上次回归版本，版本变了就提示重跑。

## 主动体检（`audit`）——装上就有结论，不用自己跑用例

**这是最该先用的命令。** 它把三面证据合起来一次性给结论：

| 面 | 数据来源 | 需要用户跑用例吗 |
|---|---|---|
| **版本** | npm registry 的 `latest` vs 已装版本 | ❌ 不需要（硬数据） |
| **运行时** | 宿主日志里的报错（ENOENT / 401 / API 缺失） | ❌ 不需要 |
| **基线** | 已有的判定通过率 | 只有这面需要 |

```bash
python scripts/console.py --host deepseek audit
```

输出分四档，每档一句话建议：

```
🔴 建议先修（跑不起来 / 通过率低）（3）
  • @vectorize-io/hindsight-coding-agents   已装 0.8.1 | 基线 0/3 K=1
  • @wxg-prc-cpg/browser-skill-dsh-plugin   已装 0.3.3 | 基线 0/3 | 4 条运行时故障
      ↳ bsk probe failed (spawn bsk ENOENT)
⬆️ 建议升级（市场有新版）（1）
  • dshmarket                               已装 1.66.5 | 最新 1.66.42 ← 落后
✅ 正常（1）
  • dsh-whale-widget                        已装 0.4.2 | 基线 3/3
⚪ 未测（没有基线）（1）
  • ...
```

**DSH 插件会在宿主启动 8 秒后自动跑一次 audit**，结论写到
`~/.dsh/skill-auditor/last-audit.txt`，日志里打一行摘要 —— **不阻塞启动、不自动卸载、不自动升级**。

> 版本落后 ≠ 该淘汰：可能是上游有 breaking change 还没适配。
> 没基线 ≠ 该删：只是没测过。



```bash
PY=python                                    # 换成你自己的解释器（需 3.8+）
C="scripts/console.py"                       # 在仓库根目录执行

"$PY" "$C" --host deepseek audit        # ★ 主动体检：一条命令出全部结论（推荐）
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

## 常用命令一览

| 命令 | 作用 |
|---|---|
| `audit` | **主动体检**：版本对比 + 运行时故障 + 基线，一次给结论 |
| `hosts` | 宿主配置与资产数 |
| `scan` | 扫描资产，建/更新 registry |
| `status` | 折旧表（含 K 列） |
| `recommend` | 强/观察/未验证/跑不起来/已降级/精简 六档面板 |
| `check` / `bump` / `stamp` | 模型换代检测（`stamp --infer` 推断不到会拒绝） |
| `report --promote` | 出折旧报告并把结果并进基线 |
| `probe` | 只读日志，找「装得像、跑不起来」 |
| `suggest [--search]` | 缺能力推荐 / 不满侦测 / 市场搜候选 |
| `seed audit` / `seed contribute` | 社区用例覆盖 / 贡献 |
| `baseline --from-seed` | 用社区用例建基线（只取已装资产） |


> 想先看效果不建数据：`python demo.py`（沙盒，几秒，`--clean` 清掉）。demo 每一步都检查退出码，失败会停。

## 多宿主（skill / 插件）

同一引擎，按 `--host` 选适配器：

```bash
"$PY" "$C" hosts                                  # 列出宿主 + 探测资产数
"$PY" "$C" --host workbuddy scan                  # WorkBuddy skill
"$PY" "$C" --host claude-code scan                # Claude Code skill
"$PY" "$C" --host deepseek --root <插件目录> scan  # DeepSeek harness 插件
"$PY" "$C" --host deepseek status                 # 插件折旧表

# 注意：DSH 的「已安装插件」以 profile 的 package.json 里
# `dsh.profile.bundles` 为准，**不是** `dependencies`。两者会不一致，
# scan 会同时报出「bundled（Electron 主包自带）」「装了但未启用」「声明了但找不到文件」。
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

## 「勉强够用」侦测（比「没有」更常见的真实状态）

用户装了东西但效果不满意，是最容易被漏掉的信号——只说"你缺什么类目"永远看不到"你有但不好用"。
`suggest` 会先认不满信号，再查已装资产的真实状态：

```
suggest --need "我设计 app 的 UI 总是差点意思"
  💡 检测到「不满意」信号（strong）：差点意思
  · frontend-dev（类目:ui-design）
      → 基线 5/5（100%），状态 watch
        ⚠️ 100% 但是 K=1 自评 ——「能跑通」不等于「效果让你满意」
          你的「差点意思」很可能是质量问题，不是能不能用的问题
```

识别的不满是**关键词级**的（`差点意思`/`不太行`/`很一般`/`凑合`/`改了几次`…），
宁可少报也不误报——判语义要模型，这一步只负责"把已在抱怨这件事标记出来"。

**关键结论**：基线 100% 但 K=1 时，说明"能用"；用户说的"差点意思"是**质量差距**，
这类差距基线测不出来，只能靠换更好的资产或补人工判据。

## 去市场找真实候选（`--search`，不需要 GitHub 加速器）

```bash
python scripts/console.py suggest --need "帮我设计一个app的界面" --search
```

会真的去两个市场拉候选（实测都能直连，**不需要加速器**）：

| 市场 | 地址 | 能给什么 |
|---|---|---|
| DSH 插件目录 | `deepseek1024.com/api/v2/plugins` | 13810 条插件，含 安装量 / 7日增长 / 最后提交 |
| SkillHub（WorkBuddy） | `lightmake.site/api/v1/search` | 支持中文搜索，含 downloads / installs / 版本 |

也可以单独用：`python scripts/market.py "ui design" --market both`

### ⚠️ 关于 star：这里最容易骗人

**DSH 目录里的 stars 是上游源码仓库的热度，不是插件本身的热度。**
实测：`dsh-runtime` 显示 **101,755 stars**，但它自身 `installCount` 只有 **73**——
那个 10 万 star 是 `nexu-io/open-design` 这个仓库的。

所以：
- 排序**不用 star**，用 安装量 + 7日增长 + 最近提交时间
- 输出里每条的 star 都标注为「上游项目 stars（源码仓库热度，不是本插件安装量）」
- 真实效果：按安装量排序能排出 `ui-ux-pro-max`（**5,959 安装** / 10.7 万下载），
  而单看 star 会把它（290 stars）埋掉

### 拿到候选后怎么判断值不值得装

三条硬检查（比 star 可靠）：
1. 有 demo 或截图能看效果吗
2. 最近半年还有提交吗
3. LICENSE 允许你的用法吗

然后：`装 → scan → 建 golden → 跑基线`，用数据比较，而不是靠热度排名。

实测示例：
- `suggest --need "帮我做一份季度财务分析报告"` → 未装 → `[install:finance]` + 去哪搜 + 值不值得装
- `suggest --need "帮我设计一个app的界面"` → 未装 → `[install:ui-design]`（命中词 设计/界面/app）
- `suggest --need "帮我做一个公司官网"` → **已装 frontend-dev** → 直接说"用它做"，不重复推荐
- `suggest --need "帮我打开浏览器自动登录网站"` → **已装 browser-use** → 已有可用
- `suggest --need "帮我把这段文字去一下AI味"` → **已装 humanizer**

跨类目需求报多个缺口，按命中词数量排序：
- `suggest --need "设计一个 app 界面并做成网页上线"` → `ui-design` + `frontend`

**TAXONOMY 只覆盖 11 个常见类目，不是万能表**。没命中时它明说"未匹配到现成类别"
并建议先用内置能力试做——而不是硬凑一个推荐。

## 新用户三步 + seed（陌生人装上不再看空表）

刚装上时你名下没有 golden set、也没有自己的会话历史可挖，`status` 会一片 unverified。
`seed/` 是**随包分发的社区公共用例库**，装上就能跑：

```bash
# ① 看 seed 覆盖了什么、其中哪些你本机能跑
python scripts/console.py --host workbuddy seed audit

# ② 用 seed 在当前模型上建基线（只取你真实装了的资产）
python scripts/baseline.py --from-seed --host workbuddy --model <当前模型版本>
#    生成执行清单 → 逐条判定 → 写 recorded.json → 再用 --from 收编

# ③ 之后照常
python scripts/console.py --host workbuddy scan
python scripts/console.py --host workbuddy status        # 不再是空表
```

`--from-seed` **只取你本机真实存在的资产**（装不上的不会凑数，并明确列出跳过哪些）。
愿意包含未安装资产做探针才加 `--all-seed`。

**你的 `golden/` 永远优先于 seed**：同名资产以你的为准，seed 只补你没有的。

seed 判定标准是社区初版，**不是权威**；真要当硬基线请自己复核后再 `--k 3`。

## 贡献一个 case 给别人用

```bash
python scripts/console.py seed contribute \
    --skill <资产名> --case-id <唯一id> \
    --input "<真实 prompt>" --expected "<期望结果>" --judge "<怎么算通过>"
```

- 会做**机械脱敏**（用户名 / 绝对路径 / 会话目录 / 微信账号），并标记 `scrubbed: true`
- 脱敏只兜已知模式，**判断不了业务描述能不能公开** —— 提 PR 前自己再看一遍
- 判定标准写法：**可以不看模型输出就复现同一结论**。反面「回答得不错」，正面「给出两个项目的定位且各附可核实来源」

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
- **golden set 覆盖**：seed 随包带 35 case / 10 资产（社区公共，见 `seed/README.md`）；
  你自己的 `golden/` 才计入强判定。seed 是起点，不是终点。
- **seed 判定标准**：单人初版、未经交叉评审；错判的 case 比 missing 的更伤，所以宁少而准。
- **插件运行时**：`probe` 只能看见日志里记录过的故障；没有主动探针（DSH desktop profile 被 Electron 独占）。
