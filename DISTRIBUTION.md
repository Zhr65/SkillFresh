# 分发与安装（给别人的 agent 用）

一个纯 Python 引擎（stdlib，零依赖），四种形态，按宿主取用。
所有命令都在本机验证过；没验证的会标注。

---

## 0. 四种形态，谁能用

| 形态 | 长什么样 | 谁认 |
|---|---|---|
| **skill** | `SKILL.md` + `scripts/` | WorkBuddy、Claude Code、Codex、DSH（`~/.dsh/skills`）、`~/.agents/skills` |
| **npm 插件** | `package.json` 含 `dsh.bundle.patch` + `cordis.patch.yml` | DSH 插件市场（可一键安装） |
| **裸 CLI** | `python scripts/console.py ...` | 任何能给 bash 的 agent |
| **zip** | `dist/skill-auditor.zip` | 人传人 |

核心只有一个，形态是外壳。**能跑不等于能被搜到** —— 见 §5。

---

## 1. 一条命令装到本机所有 agent

```bash
python scripts/install.py                    # 装到所有已存在的宿主目录
python scripts/install.py --targets workbuddy,dsh
python scripts/install.py --dry-run          # 只看会做什么
python scripts/install.py --copy             # 强制复制（默认优先符号链接）
python scripts/install.py --create           # 宿主目录不存在时也创建
python scripts/install.py --uninstall        # 撤销（只删指向本 skill 的）
```

会自动识别的宿主：

| 名称 | 目录 | 说明 |
|---|---|---|
| `workbuddy-ai` | `~/.workbuddy-ai/skills` | **WorkBuddy Agent 实际加载的目录**（本机实证） |
| `workbuddy` | `~/.workbuddy/skills` | 桌面端技能市场默认写入 |
| `claude` | `~/.claude/skills` | Claude Code |
| `agents` | `~/.agents/skills` | 跨 agent 通用（`npx skills` 也装这里） |
| `dsh` | `~/.dsh/skills` | DeepSeek Harness |
| `codex` | `~/.codex/skills` | OpenAI Codex CLI（装了才有这个目录） |

**本机实测结果**：Windows 上目录符号链接需要开发者模式/管理员，`install.py` 会自动退化为复制，并明确告诉你「改动需重跑 install」。不会留下空目录——装完会读一次 `SKILL.md` 验证。

---

## 2. WorkBuddy

### 两个目录的坑

| 目录 | 谁在用 |
|---|---|
| `~/.workbuddy-ai/skills/` | 本会话 Agent **实际加载**（22 个 skill 全在已加载列表） |
| `~/.workbuddy/skills/` | 桌面端技能市场写入，里面的 skill **未**出现在已加载列表 |

装给 Agent 用 → `~/.workbuddy-ai/skills/`。

### 市场（SkillHub）

`https://lightmake.site`，语义搜索 API 可用：

```bash
curl -s "https://lightmake.site/api/v1/search?q=skill+audit&limit=5"
```

安装 = 下载 zip 解到 skills 目录：

```bash
curl -L -o /tmp/skill.zip "https://lightmake.site/api/v1/download?slug=<slug>"
unzip -o /tmp/skill.zip -d ~/.workbuddy-ai/skills/<slug>
```

> 同类参考：市场里的 `skill-audit-modeio`（1186 downloads）做的是**安装前静态安全审计**，不是能力折旧。两者不冲突。

---

## 3. DeepSeek Harness

DSH **有内置插件市场**（`dsh-community-market`），路径：**设置 → 插件市场**（本机 `dsh-market v1.1.66`）。
界面支持搜索、分类（AGI 架构探索 / UI 增强 / 用量与计费 / 主题与外观…）、已安装、已屏蔽、来源切换，条目右侧直接「安装」。

### 三种用法，按需要选

| 用法 | 怎么做 | 适合 |
|---|---|---|
| **A. 当 skill 用**（最快） | `python scripts/install.py --targets dsh`，然后让 Agent 跑 `console.py --host deepseek status` | 只想用起来 |
| **B. 从市场装**（一键） | 设置 → 插件市场 → 搜到 → 安装 | 已发布到 npm 并被目录收录 |
| **C. CLI 装** | `dsh plugin --profile web add <npm\|github:owner/repo\|link:path>` | Web 端 / 本地开发 |

> ⚠️ 桌面端读的是 `desktop` profile，`dsh plugin` 命令行**按设计拒绝**碰它
> （`profile "desktop" is managed exclusively by the Electron application`）。
> 桌面端只能用内置 `plugin_manager` 或市场 UI；CLI 的 `--profile web` 只对 `dsh web` 有效。

### 市场的自动安装条件（官方文档原文口径）

一个目录条目要能「一键安装」，必须同时满足：

1. 条目能标准化出**且只能标准化出一个**合法 npm package name；
2. 该 package 不是 `dsh-plugin-desktop` / `dsh-plugin-desktop-beta` / `dsh-community-market`；
3. `https://registry.npmjs.org/<package>/latest` 返回**同名 + 精确稳定版本**（不能是 prerelease）；
4. npm manifest 声明一个**安全的相对路径** `dsh.bundle.patch`。

不满足时条目仍可浏览，只显示「仅供展示」的手动命令。
**版本权威是 npm `latest`**，目录里写的版本不会成为安装目标。

### 目录来源（catalog source）

市场本身不带插件数据，它读「目录来源」。当前可用的两类：

- `dsh-community-market`（Beta，内置）—— 数据来自 `awesome-deepseek-harness-plugins`（1024Store），走 `/api/v2/plugins`
- `dsh-market` —— 社区热门插件市场，数据来自 `awesome-dsh-plugin`

同一时间只启用一个。任何人都可以按 `catalog-source` / `catalog-provider-page` schema 发布自己的来源。

---

## 4. 让别人的 agent 用上：三种分发强度

| 强度 | 做法 | 别人要做什么 |
|---|---|---|
| 低 | 发 `dist/skill-auditor.zip` | 解压到对应 skills 目录 |
| 中 | 发 git 仓库地址 | `git clone` 到 skills 目录 / `npx skills add owner/repo` |
| 高 | 发布 npm + 进 DSH 目录来源 | 市场里点「安装」 |

---

## 5. 进 DSH 市场：npm 包骨架（已就绪，在 `npm/`）

```
npm/
├── package.json          # name=dsh-skill-auditor, version=0.4.1（稳定版）
│                         # "dsh": { "bundle": { "patch": "./cordis.patch.yml" } }
├── cordis.patch.yml      # - insert: [{ id: skill-auditor, name: dsh-skill-auditor }]
├── plugin.json           # skills: ./skill（宿主能把它当 skill 加载）
├── lib/index.js          # 插件入口：注册 5 个 skill_audit_* 工具
├── lib/engine.mjs        # 引擎定位 / Python 探测 / 用户数据目录
├── skill/SKILL.md        # 插件内自带 skill（hindsight 同款做法）
├── engine/               # 生成的：上游引擎副本（prepack 时同步）
├── scripts/sync-engine.mjs  # 白名单复制 + 路径脱敏 + 禁用词扫描
└── test/{smoke,tarball}.mjs # 冒烟 + 打包→安装→运行 全链路
```

**四个市场准入条件，逐条对上：**

| 条件 | 状态 |
|---|---|
| 唯一合法 npm 包名 | `dsh-skill-auditor` — `npm view` 返回 404，未被占用 ✓ |
| 非 Desktop 自有 bundle | 不是 `dsh-plugin-desktop*` / `dsh-community-market` ✓ |
| `registry.npmjs.org/<pkg>/latest` 同名 + 精确稳定版本 | 待发布；`version` 已是 `0.4.1`，无 prerelease 后缀 ✓ |
| manifest 声明安全相对路径 `dsh.bundle.patch` | `./cordis.patch.yml` ✓ |

**本机已验证（不是推测）：**

- `npm pack` → 21 个文件 / 44 kB，不含 registry、基线、个人 golden set、构建脚本、测试
- `npm install <tgz>` 到干净目录 → 从安装位置 import → 注册 5 个工具 → 真跑引擎出折旧表（3 个真实插件）
- `prepack` 自动同步引擎，且**路径脱敏**（本机 Python 路径 → `python`）+ 禁用词扫描（命中即发布失败）

**发布三步：**

```bash
cd npm
npm run test          # 冒烟 + 全链路（当前 36 项全过）
npm publish           # 需要 npm 账号；首次发布前确认 2FA
```

```bash
# 然后给目录来源提 PR（二选一）
#   · awesome-deepseek-harness-plugins（dsh-community-market 的数据源）
#   · awesome-dsh-plugin（dsh-market 的数据源）
# 条目只需要：name=dsh-skill-auditor + 一句话描述 + 仓库/主页链接
```

```bash
# 最后在市场里验：搜索 → 详情（安装 preview 显示 0.4.1）→ 安装 → 完全退出重开
```

> 桌面端装完若没出现，完全退出（含托盘）再打开。
> 引擎需要本机有 Python 3.8+；没有时工具会直接说清「装 Python 或设 SKILL_AUDITOR_PYTHON」，不会静默失败。

### 发布之后怎么更新（已核到源码）

**市场没有「更新」按钮。** `dsh-community-market/lib/install/service.js` 里：

```js
async function assertNotInstalled(profile, packageName) {
  const profileManifest = await readManifest(join(profile.dir, 'package.json'));
  if (profileReferencesPlugin(profileManifest, packageName)) {
    throw new MarketInstallError('conflict', 'This plugin is already managed by the active profile.');
  }
}
```

已装的包再点安装 → 直接 `conflict`。官方文档也写明「已安装」视图「可移除依赖；核心 bundle 只读」，
没有升级操作。所以升级只能三选一：

| 路径 | 怎么做 | 代价 |
|---|---|---|
| 卸载 → 重装 | 市场 → 已安装 → 卸载 → 回发现页点安装 | 两步，中间插件不在 |
| CLI（web profile） | `dsh plugin --profile web add dsh-skill-auditor` | 桌面 profile 被 Electron 独占，CLI 按设计拒绝 |
| 手动 pnpm | `cd ~/.dsh/profiles/desktop && pnpm add dsh-skill-auditor@latest` | 绕过 Desktop 的 checkpoint，可能被 reconcile 覆盖 |

**版本权威是 npm `latest`**，所以只要 `npm publish` 了新版本，上面任一路径装到的就是新版。

**数据兼容（这是我们自己的责任，市场不管）**：

- 包的 `version` 变了 → 下次调用工具时 `ensureEngine()` 把 `scripts/` **先删后拷**整体替换
- `registry.*.json` / `baseline-results.json` / `model-version.json` / `golden/*.json` / `hosts.json` **不动**
- `.engine.json` 记录已装引擎版本；`test/smoke.mjs` 第 5 段专测这条路径（版本变化 → 代码替换 → 用户数据不变）

**怎么让人知道有新版本**：引擎不联网，插件不会主动提醒。用户自查：

```bash
npm view dsh-skill-auditor version        # 远端
cat ~/.dsh/skill-auditor/.engine.json     # 本机
```

> 想加「有新版本」提示，就得让引擎联网 —— 与「判定全在本机算」的承诺冲突，所以没做。

---

## 6. 发布清单

- [x] LICENSE（MIT）
- [x] 干净分发包（14 文件，零个人数据）
- [x] 安装说明（本文档）
- [x] 跨宿主安装器（`scripts/install.py`）
- [x] npm 包骨架（`npm/`，pack → install → run 全链路已验证）
- [x] 插件冒烟测试（44 项断言，`npm run test`）
- [x] SkillHub `_meta.json`（照本机市场安装的真实样例补：`slug/title/version/author/runtime/emoji/color/tags/description_zh/description_en/category`，`category` 取自市场 API 实际枚举 `ai-agent`）
- [ ] `npm publish`（要你的 npm 账号）
- [ ] 目录来源 PR（awesome-deepseek-harness-plugins / awesome-dsh-plugin）
- [ ] WorkBuddy SkillHub 提交（`_meta.json` 已就绪，走市场发布流程）
- [x] git 仓库（已 `git init -b main` + 2 次提交，30 个文件，零个人数据；**还差 push 到远端**）
- [x] 30 秒演示（`demo.py`：沙盒 7 步真跑引擎，约 13 秒；全新 clone 实测通过）

### 还差一步：push 到远端

仓库已经能在本地 clone，但别人要 clone 得先有个远端地址：

```bash
cd ~/.workbuddy-ai/skills/skill-auditor
git remote add origin git@github.com:<你的账号>/skill-auditor.git
git push -u origin main
```

推上去之后，别人就两条命令：

```bash
git clone <你的仓库地址> && cd skill-auditor
python demo.py          # 约 13 秒，沙盒，不碰他的数据
```

**全新 clone 实测**（不是推测）：`demo.py` 7 步全过、`npm test` 44 项全过。
这个验证抓到过一个真 bug：`npm/engine/` 在 gitignore 里，`prepack` 只在 pack/publish 时跑，
所以新 clone 里 `npm test` 会连锁失败 30 条 —— 已补 `pretest` 自动同步引擎。
