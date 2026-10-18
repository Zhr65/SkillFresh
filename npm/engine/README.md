# skill-auditor

**Agent 能力折旧表** — 当模型升级时，告诉你哪些 skill 还灵、哪些退化了、哪些该淘汰；当用户缺能力时，按对话需求推荐 skill/插件。

一个零依赖的 WorkBuddy / Claude Code 风格 skill：方法论（SKILL.md）+ 六个 stdlib-only Python 脚本（指纹 / 回归 / 基线 / 常驻控制台 / 安装 / 打包）+ 一个有断言的沙盒演示。

> 仓库名是 **SkillFresh**（skill 保鲜），代码里的项目名仍是 `skill-auditor`，npm 包名 `dsh-skill-auditor`。三者指同一个东西。

## 30 秒试一下

```bash
git clone https://github.com/Zhr65/SkillFresh.git && cd SkillFresh
python demo.py            # 几秒，全程在 demo/ 沙盒里，不碰你的任何数据
python demo.py --clean    # 看完删掉
```

> HTTPS 走不通就用 SSH：`git clone git@github.com:Zhr65/SkillFresh.git`（需已配 GitHub SSH key）

它会在沙盒里造 6 个假 skill，真跑一遍引擎：

```
6 个假 skill → scan 出真实指纹 → 建基线（全过）→ 模型换代 → check 发现基线过期
→ 用新模型重跑 → report 出判定：2 个降级、1 个指纹漂移、3 个稳定
→ 新结果落成新基线 → status 表里出现 1/3、2/3 → recommend 亮出「已降级」档
```

判定是 `regress.py` **真算**的（阈值、漂移检测、众数归因都真跑），只有每个用例的 `passed` 值是构造的
（代表「假设新模型在这些用例上的表现」，标了 `"synthetic": true`）。看完你就知道「有判定之后长什么样」，
不用先憋 30 条 golden set。

## 它解决什么

- **能力退化**：模型每几个月迭代一次，但「我名下的 skill 在新模型上还灵不灵」没人回答。本工具用「版本指纹 + golden set 回归 + 20pp 阈值」把「感觉变笨了」变成可复现的判据。
- **新 skill 好不好用**：消融对照、采纳率、净收益公式（方法论，尚未自动化，工具不产出这些数）。
- **是否适合你**：触发精度、环境对账、风格基准（同上，方法论层）。
- **无匹配时推荐**：对话里出现没有 skill 覆盖的需求时，先查注册表，再按需推荐安装项。

## 多宿主：同一引擎，评 skill 也评插件

核心引擎（指纹 / 基线 / 回归 / 推荐 / 需求匹配）完全共享；每个宿主只是一个适配器，知道「资产在哪、清单长什么样」。

| 宿主 | 评什么 | 默认目录 | 清单文件 |
|---|---|---|---|
| workbuddy | skill | 本 skill 所在的 skills 根 | `SKILL.md` |
| claude-code | skill | `~/.claude/skills` | `SKILL.md` |
| deepseek | 插件 | `~/.dsh/profiles/desktop`（读 package.json 依赖） | `package.json` / `plugin.json` |
| generic | 任意 | `--root` 指定 | `plugin.json` / `manifest.json` / `SKILL.md` / `package.json` |

- 每个宿主独立注册表 `registry.<host>.json`，互不覆盖。
- 基线结果与 golden set 共享（按资产名索引）。
- 加新宿主 = 在 `hosts.json` 加一条（kind + root + 清单文件名），**不用改代码**。

```bash
python scripts/console.py hosts                                    # 看所有宿主 + 探测到的资产数
python scripts/console.py --host workbuddy scan                    # 评 skill
python scripts/console.py --host deepseek --root <插件目录> scan    # 评插件
python scripts/console.py --host deepseek status                   # 插件折旧表
```

> DeepSeek harness 的插件以 npm 依赖形式装在 `~/.dsh/profiles/<profile>/node_modules/`；适配器读该 profile 的 `package.json` dependencies 来认定「已安装」，不会把 `node_modules` 里的传递依赖误当成插件。换 profile 改 `root` 即可。

## 安装（三选一）

**1) 手动复制（最简单）**
```bash
# 用户级（所有项目可用）；clone 下来的目录叫 SkillFresh，要改名为 skill-auditor
cp -r SkillFresh ~/.workbuddy-ai/skills/skill-auditor
# 或项目级（仅当前项目）
cp -r SkillFresh <project>/.workbuddy-ai/skills/skill-auditor
```

**2) git clone**
```bash
git clone https://github.com/Zhr65/SkillFresh.git ~/.workbuddy-ai/skills/skill-auditor
```

**3) 技能市场**（若已发布）
```bash
npx skills@latest add Zhr65/SkillFresh
```

> 目录名必须是 `skill-auditor`，且 `SKILL.md` 在其根目录——这是 skill 的加载约定。

## 快速开始（5 条命令）

```bash
cd ~/.workbuddy-ai/skills/skill-auditor
python scripts/console.py setup --model v-current-2026-10-17   # 一键初始化
python scripts/console.py scan                                  # 扫描 skill 目录，建注册表
python scripts/console.py status                                # 看折旧表
python scripts/console.py recommend                             # 推荐/淘汰面板
python scripts/console.py suggest --need "帮我做一份财务分析报告"  # 无匹配时按需求推荐
```

`setup` 只创建本地数据文件（每个宿主一个模型戳、golden 模板），不覆盖已有数据。

> `hosts.json` 是**本地**配置（含你机器的真实路径，已 gitignore）；首次跑 `discover --write` 会自动生成。想手写就复制 `hosts.example.json`。

## 让别人用上（新用户三步）

别人装上后最缺两样东西：**资产路径**和**基线数据**。三个命令解决：

```bash
python scripts/console.py discover --write          # 自动探测本机 WorkBuddy / Claude Code / DeepSeek / agents 的资产目录，写回 hosts.json
python scripts/console.py --host workbuddy scan     # 扫描，建注册表
python scripts/console.py --host workbuddy bootstrap # 从本地历史生成 golden 草稿
```

或者一条命令串起来：

```bash
python scripts/console.py --host workbuddy onboard
```

`bootstrap` 会读本机会话 transcript（`~/.workbuddy-ai/projects`、`~/.claude/projects`、`~/.dsh/sessions`），把**真实用户请求**按关键词匹配到对应 skill/插件，生成 `golden/<asset>.json` 草稿（标 `"draft": true`）。草稿里的 `expected` / `judge` 是占位符，**需要人工补**，补完再跑 baseline 才有意义。

> 这就是「别人也能用」的关键：工具不再依赖某个人的 golden set，而是从每个用户自己的历史里长出来。

## 模型升级后

```bash
# 1) 记录模型换代（每个宿主一个戳；平台在生产里应自动写）
python scripts/console.py --host workbuddy bump <新模型版本>
python scripts/console.py --host workbuddy check     # 退出码 2 = 需要重跑

# 2) 用新模型重跑 golden set -> new-results.json
python scripts/console.py --host workbuddy report --results new-results.json --promote
#    --promote 是必需的：报告默认只写报告，不碰基线
#    合并按资产进行：这次跑到的资产替换，没跑到的保留并打印

# 3) 重扫，让折旧表和面板看到新判定
python scripts/console.py --host workbuddy scan
python scripts/console.py --host workbuddy recommend
```

> `scan` 只统计**当前模型戳**下的基线。换代后还没重跑的资产会回到 `unverified`——这是有意的：
> 旧模型上的 100% 不能证明新模型上还灵。看到一片未验证，说明该重跑了，不是工具坏了。

## 命令一览

| 命令 | 作用 |
|---|---|
| `hosts` | 列出宿主配置与探测到的资产数 |
| `setup` | 初始化本地数据（版本戳 + golden 模板） |
| `scan` | 扫描 skill 目录，算指纹，合并基线 → `registry.<host>.json` |
| `status` | 折旧表 |
| `check` | 模型版本是否变更 → 是否需重跑 |
| `bump <ver>` | 模拟/记录模型切换事件（按宿主） |
| `recommend` | 推荐/淘汰面板 |
| `report [--promote]` | 生成折旧报告；`--promote` 把结果合并进基线 |
| `probe` | 读宿主日志，找「装得像、跑不起来」的资产 |
| `suggest --need "<需求>"` | 无匹配 skill 时按需求推荐 |
| `seed audit` | seed/ 覆盖了什么、哪些你本机能跑 |
| `baseline --from-seed` | 用社区 seed 建基线（只取你装了的资产） |
| `seed contribute` | 贡献一个 case（自动脱敏） |
| `discover [--write]` | 探测本机各宿主资产目录，可写回 hosts.json |
| `bootstrap [--per-asset N]` | 从本地历史生成 golden 草稿（draft，需补完） |
| `onboard` | discover + scan + bootstrap 一键引导 |

全局 `--out <dir>` 会把**所有**本地数据（registry / baseline / 模型戳 / golden 模板）放到该目录，不再散在两处。

### 陌生用户先看这里

装完看到一片 `unverified` 是正常的（你没有 golden set，也没有历史可挖）。`seed/` 是**随包分发的社区公共用例库**，装上就能跑：

```bash
python scripts/console.py --host workbuddy seed audit      # seed 覆盖什么
python scripts/baseline.py --from-seed --host workbuddy --model <模型>
python scripts/console.py --host workbuddy scan && python scripts/console.py --host workbuddy status
```

`--from-seed` 只取你**真实装了的**资产，装不上的不凑数；你自己的 `golden/` 永远优先于 seed。
判定标准是社区初版，不是权威。

## 安全与隐私

- **零依赖、无网络**：所有脚本只用 Python 标准库，不发送任何数据。
- **不自动删、不自动装**：淘汰与安装都需要用户确认；优先「停用/归档」再删除。
- **个人数据只留在本地**：`registry.<host>.json`、`baseline-results.json`、`recorded-*.json`、`model-version*.json`、填好的 `golden/*.json`、`replay/` 都在 `.gitignore` 里，不会随仓库分发。
- **安装器不搬运数据**：`install.py` 复制到其他宿主时只带代码、文档和 `golden/_template.json`，不复制 registry / 基线 / 个人 golden。
- **golden set 含真实用户 prompt**：不要分享填好的 golden 文件。

## 分发内容 vs 本地生成

**随包分发**：`SKILL.md`、`USAGE.md`、`DISTRIBUTION.md`、`README.md`、`LICENSE`、`_meta.json`、`hosts.example.json`、`demo.py`、`scripts/*.py`（不含测试与 `tools/`）、`seed/*`、`golden/README.md`、`golden/_template.json`

**本地生成（不打包）**：`registry.<host>.json`、`hosts.json`、`model-version*.json`、`baseline-results.json`、`recorded-*.json`、`*-report.*`、`replay/`、填好的 `golden/<skill>.json`

打包：`python scripts/build_dist.py` → 产出 `dist/skill-auditor/` 与 `dist/skill-auditor.zip`（自动排除个人数据）。

## 发布前检查清单

- [ ] `python scripts/test_engine.py` 全过（指纹口径 / 阈值 / 漂移 / 合并 / GBK / seed）
- [ ] `python scripts/console.py --host workbuddy seed audit` 无「index 与 seed/ 不一致」告警
- [ ] `python demo.py` 退出码 0（真实跑完 7 步，有断言）
- [ ] `cd npm && npm test` 全过；`npm run check:engine` 显示一致（engine 没漂）
- [ ] `python scripts/build_dist.py` 后抽查 `dist/` 里没有 registry / golden 实填 / hosts.json
- [ ] 版本号四处一致：`SKILL.md` / `npm/package.json` / `npm/plugin.json` / `_meta.json`
- [ ] 发布前 `node catalog/stamp.mjs` 把市场条目钉到本次提交
- [ ] README 补 1 张 `status` / `recommend` 截图

## 目录结构

```
skill-auditor/
├── SKILL.md              # 方法论 + 触发条件（skill 入口）
├── USAGE.md              # 使用与验证指南
├── README.md             # 本文件
├── DISTRIBUTION.md       # 分发/安装渠道与坑
├── PUBLISH.md            # 发布与市场收录材料
├── LICENSE
├── _meta.json            # SkillHub 元数据
├── demo.py               # 30 秒沙盒演示（有断言）
├── .gitignore            # 排除个人数据
├── scripts/
│   ├── console.py        # 常驻控制台（含 probe / suggest / seed / bootstrap）
│   ├── fingerprint.py    # 版本指纹报告（薄包装）
│   ├── regress.py        # 回归 + 降级/漂移判定
│   ├── baseline.py       # 基线脚手架 + 按资产合并 + --from-seed
│   ├── install.py        # 装到本机各 agent 宿主（不搬个人数据）
│   ├── test_engine.py    # 引擎回归测试（不随包分发）
│   └── build_dist.py     # 干净打包
├── seed/                 # 社区公共 golden（随包分发）
├── npm/                  # DSH 插件（6 个工具 + 入库的 engine/）
├── catalog/              # 市场条目 + 校验/钉 commit 脚本
└── golden/
    ├── README.md         # golden set 说明
    └── _template.json    # 模板（本地复制后填真实用例）
```
