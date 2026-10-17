# skill-auditor

**Agent 能力折旧表** — 当模型升级时，告诉你哪些 skill 还灵、哪些退化了、哪些该淘汰；当用户缺能力时，按对话需求推荐 skill/插件。

一个零依赖的 WorkBuddy / Claude Code 风格 skill：方法论（SKILL.md）+ 三个 stdlib-only Python 脚本（指纹 / 回归 / 常驻控制台）。

## 30 秒试一下

```bash
git clone <本仓库> && cd skill-auditor
python demo.py            # 约 13 秒，全程在 demo/ 沙盒里，不碰你的任何数据
python demo.py --clean    # 看完删掉
```

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
- **新 skill 好不好用**：消融对照、采纳率、净收益公式。
- **是否适合你**：触发精度、环境对账、风格基准。
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
# 用户级（所有项目可用）
cp -r skill-auditor ~/.workbuddy-ai/skills/
# 或项目级（仅当前项目）
cp -r skill-auditor <project>/.workbuddy-ai/skills/
```

**2) git clone**
```bash
git clone <repo-url> ~/.workbuddy-ai/skills/skill-auditor
```

**3) 技能市场**（若已发布）
```bash
npx skills@latest add <owner>/skill-auditor
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

`setup` 只创建本地数据文件（版本戳、golden 模板），不覆盖已有数据。

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
python scripts/console.py bump <新模型版本>     # 平台在真实场景应自动调用这一步
python scripts/console.py check                # 检测变更，给出重跑指引
# 用新模型重跑 golden set -> new-results.json
python scripts/console.py report --results new-results.json --threshold 20
python scripts/console.py scan                 # 新基线写回注册表
python scripts/console.py recommend            # 看是否出现降级淘汰项
```

## 命令一览

| 命令 | 作用 |
|---|---|
| `setup` | 初始化本地数据（版本戳 + golden 模板） |
| `scan` | 扫描 skill 目录，算指纹，合并基线 → `registry.<host>.json` |
| `status` | 折旧表 |
| `check` | 模型版本是否变更 → 是否需重跑 |
| `bump <ver>` | 模拟/记录模型切换事件 |
| `recommend` | 推荐/淘汰面板 |
| `report` | 生成折旧报告 |
| `suggest --need "<需求>"` | 无匹配 skill 时按需求推荐 |
| `discover [--write]` | 探测本机各宿主资产目录，可写回 hosts.json |
| `bootstrap [--per-asset N]` | 从本地历史生成 golden 草稿 |
| `onboard` | discover + scan + bootstrap 一键引导 |

## 安全与隐私

- **零依赖、无网络**：三个脚本只用 Python 标准库，不发送任何数据。
- **不自动删、不自动装**：淘汰与安装都需要用户确认；优先「停用/归档」再删除。
- **个人数据只留在本地**：`registry.<host>.json`、`baseline-results.json`、`recorded-*.json`、填好的 `golden/*.json`、`replay/` 都在 `.gitignore` 里，不会随仓库分发。
- **golden set 含真实用户 prompt**：不要分享填好的 golden 文件。

## 分发内容 vs 本地生成

**随包分发**：`SKILL.md`、`USAGE.md`、`README.md`、`scripts/*.py`、`golden/README.md`、`golden/_template.json`

**本地生成（不打包）**：`registry.<host>.json`、`hosts.json`、`model-version.json`、`baseline-results.json`、`recorded-*.json`、`*-report.*`、`replay/`、填好的 `golden/<skill>.json`

打包：`python scripts/build_dist.py` → 产出 `dist/skill-auditor/` 与 `dist/skill-auditor.zip`（自动排除个人数据）。

## 发布前检查清单

- [ ] 加 `LICENSE`（MIT / Apache-2.0 等，按你的意愿）
- [ ] 确认 `dist/` 里没有个人数据（`build_dist.py` 已排除）
- [ ] 仓库 README 补 1 张 `status` / `recommend` 截图
- [ ] 若要进技能市场，按平台的发布流程提交（`SKILL.md` frontmatter 已就绪）
- [ ] 版本号：改 `SKILL.md` 的 `version`

## 目录结构

```
skill-auditor/
├── SKILL.md              # 方法论 + 触发条件（skill 入口）
├── USAGE.md              # 使用与验证指南
├── README.md             # 本文件
├── .gitignore            # 排除个人数据
├── scripts/
│   ├── console.py        # 常驻控制台（setup/scan/status/check/bump/recommend/report/suggest）
│   ├── fingerprint.py    # 版本指纹
│   ├── regress.py        # 回归 + 降级判定
│   ├── baseline.py       # 基线汇聚
│   └── build_dist.py     # 干净打包
└── golden/
    ├── README.md         # golden set 说明
    └── _template.json    # 模板（本地复制后填真实用例）
```
