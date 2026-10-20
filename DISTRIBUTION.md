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

> 两条通道的差别（源码核实，`PUBLISH.md §2.1` 有细节）：
> 1024Store 适配器只产出 `package: {registry: 'npm'}`，从不产出 `installSource` ——
> 所以**内置市场**一键安装只认 npm；但安装服务本身支持
> `installSource.kind: "github"` + 固定 40 位 commit（`lib/install/github.js`），
> 自建目录来源走这条路**不需要 npm**。
> 本条目两种形态都已备好（`catalog/`），且都用市场自己的校验器验过。

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
├── package.json          # name=dsh-skill-auditor, version=以 package.json 为准（稳定版）
│                         # "dsh": { "bundle": { "patch": "./cordis.patch.yml" } }
├── cordis.patch.yml      # - insert: [{ id: skill-auditor, name: dsh-skill-auditor }]
├── plugin.json           # skills: ./skill（宿主能把它当 skill 加载）
├── lib/index.js          # 插件入口：注册 6 个 skill_audit_* 工具
├── lib/engine.mjs        # 引擎定位 / Python 探测 / 用户数据目录
├── skill/SKILL.md        # 插件内自带 skill（hindsight 同款做法）
├── engine/               # 入库的上游引擎副本（check:engine 防漂移）
├── scripts/sync-engine.mjs   # 白名单复制 + 路径脱敏 + 禁用词扫描
├── scripts/check-engine.mjs  # 重建 engine/ → 与 HEAD 比对，拦「两通道不同行为」
└── test/{smoke,tarball}.mjs # 冒烟 + 打包→安装→运行 全链路
```

> `engine/` **入库**，不在 .gitignore：DSH 市场的 github 安装通道拿到的就是
> 仓库里这一份，没有 prepack 会替你跑。忽略它 → 那条通道装出来的包没有引擎。
> 改上游脚本后必须 `npm run sync` 并提交，`npm run check:engine` 会拦。

**四个市场准入条件，逐条对上：**

| 条件 | 状态 |
|---|---|
| 唯一合法 npm 包名 | `dsh-skill-auditor` — `npm view` 返回 404，未被占用 ✓ |
| 非 Desktop 自有 bundle | 不是 `dsh-plugin-desktop*` / `dsh-community-market` ✓ |
| `registry.npmjs.org/<pkg>/latest` 同名 + 精确稳定版本 | 待发布；`version` 以 package.json 为准，无 prerelease 后缀 ✓ |
| manifest 声明安全相对路径 `dsh.bundle.patch` | `./cordis.patch.yml` ✓ |

**本机已验证（不是推测）：**

- `npm pack` → **40 个文件 / ~122 kB**（2026-10-20 `npm pack --dry-run` 实测；engine/ 镜像进包后比早期版本翻倍——数字以后以 `npm pack --dry-run` 实际输出为准，别抄文档），不含 registry、基线、个人 golden set、构建脚本、测试
- `npm install <tgz>` 到干净目录 → 从安装位置 import → 注册 **9 个工具** → 真跑引擎出折旧表（3 个真实插件）
- `prepack` 自动同步引擎，且**路径脱敏**（本机 Python 路径 → `python`）+ 禁用词扫描（命中即发布失败）

**发布三步（详细版含 2FA、版本四处同步、固定动作清单 → 见 `PUBLISH.md`）：**

```bash
cd npm
npm run check:engine   # 引擎与上游一致（github 通道与 npm 通道不发生分歧）
npm run test           # 冒烟 + 全链路（全部断言）
npm publish            # 需要 npm 账号；2FA 用 --otp
```

```bash
# 然后给目录来源提 PR：
#   · awesome-deepseek-harness-plugins（1024Store 内置市场的数据源）
#     条目是 catalog/plugins/ 下的一个 JSON 文件，内容已备好：
#     catalog/awesome-plugin-entry.json（文件名 zhr65--dsh-skill-auditor--npm.json）
#   · 或自建 catalog source（不需要 npm，用 github 固定 commit）
# 两条路的取舍与已验证据见 PUBLISH.md §2
```

```bash
# 最后在市场里验：搜索 → 详情（安装 preview 显示当前版本）→ 安装 → 完全退出重开
```

> 桌面端装完若没出现，完全退出（含托盘）再打开。
> 引擎需要本机有 Python 3.8+；没有时工具会直接说清「装 Python 或设 SKILL_AUDITOR_PYTHON」，不会静默失败。

### 发布之后怎么更新（已核到源码，**2026-10-18 修正**）

> **修正一个重要错误结论。** 本节此前写「市场没有更新按钮」——那是把
> `dsh-community-market`（0.1.0-dev.0，只是个**目录来源适配器**）当成了市场本身。
> 桌面应用里真正的市场 UI 是 **`dshmarket`**（app 内置 1.66.5 / web profile 1.66.20），
> 它**有完整的更新功能**。用户在市场里看到的「更新」按钮是真的。

`dshmarket/lib/install.js` 核到的事实：

- 更新走 pnpm（`add` / `remove` / `install` / `update` 四个动词，`update` 是独立路由）
- **失败自动回滚**：更新失败会重装上一版，并明确区分「pnpm 没启动（什么都没动）」
  和「文件被运行中的宿主占用（Windows file-locked）」——后者会提示重启宿主再试
- **假成功防护**：装完逐包验证 dsh manifest 与入口产物，缺了当场移除，防止把下次启动搞挂
- **支持 github 钉 commit 的目标**：`github:owner/repo#<sha>&path:/子目录`（含集合仓库
  自动拆子包、中国区镜像已解析 commit 的兼容）
- 版本权威仍是 npm registry（`name@<version>` / `latest`），git 来源保持 git 指向

**对我们意味着**：`npm publish` 新版本 → 用户在市场「已安装」里点**更新** → 拿到新版。
不需要卸载重装。引擎侧的数据兼容承诺不变：

- 包的 `version` 变了 → 下次调用工具时 `ensureEngine()` 把 `scripts/` **先删后拷**整体替换
- `registry.*.json` / `baseline-results.json` / `model-version*.json` / `golden/*.json` / `hosts.json` **不动**
- `.engine.json` 记录已装引擎版本；`test/smoke.mjs` 第 5 段专测这条路径

**备选路径**（更新按钮之外，仍然成立）：

| 路径 | 怎么做 | 适用 |
|---|---|---|
| 市场更新按钮 | 已安装 → 更新 | **首选**，桌面/web 都有 |
| CLI（web profile） | `dsh plugin --profile web update dsh-skill-auditor` | `dsh web` 场景 |
| 手动 pnpm | `cd ~/.dsh/profiles/desktop && pnpm add dsh-skill-auditor@latest` | 排障用，绕过市场 |

**怎么让人知道有新版本**：市场更新页会比对 npm，但引擎本身不联网、不主动提醒。用户自查：

```bash
npm view dsh-skill-auditor version        # 远端
cat ~/.dsh/skill-auditor/.engine.json     # 本机
```

> 想加「有新版本」提示，就得让引擎联网 —— 与「判定全在本机算」的承诺冲突，所以没做。

---

## 6. 发布清单

- [x] LICENSE（MIT）
- [x] 干净分发包（30+ 文件，零个人数据；以 build_dist.py 实际输出为准）
- [x] 安装说明（本文档）
- [x] 跨宿主安装器（`scripts/install.py`）
- [x] npm 包骨架（`npm/`，pack → install → run 全链路已验证）
- [x] 插件冒烟测试（全部断言，`npm run test`）
- [x] SkillHub `_meta.json`（照本机市场安装的真实样例补：`slug/title/version/author/runtime/emoji/color/tags/description_zh/description_en/category`，`category` 取自市场 API 实际枚举 `ai-agent`）
- [ ] `npm publish`（要你的 npm 账号；命令与注意事项 → `PUBLISH.md §1`）
- [ ] 目录来源 PR（条目已按对方 schema 写好并逐字段验过 → `catalog/awesome-plugin-entry.json`；步骤与 PR 文案 → `PUBLISH.md §2.3`）
- [ ] WorkBuddy SkillHub 提交（`_meta.json` 已就绪，缺的是站点提交入口确认 → `PUBLISH.md §3`）
- [x] DSH 目录条目（两种通道形态都备好，均通过市场自己的校验器：`catalog/`）
- [x] github 安装通道实测（pnpm 从固定 commit 装 `path:/npm` → 6 工具注册 → 引擎随包落地）
- [x] `engine/` 入库 + `check:engine` 防漂移（npm 通道与 github 通道不发生版本分歧）
- [x] git 仓库（7 次提交，零个人数据；**已推送到 https://github.com/Zhr65/dsh-skill-auditor**）
- [x] 30 秒演示（`demo.py`：沙盒 7 步真跑引擎，几秒；**远端 clone 实测通过**）

### 仓库已上线

```
https://github.com/Zhr65/dsh-skill-auditor          # 仓库名 dsh-skill-auditor，项目名 skill-auditor
git@github.com:Zhr65/dsh-skill-auditor.git          # SSH 远端
```

别人两条命令：

```bash
git clone https://github.com/Zhr65/dsh-skill-auditor.git && cd dsh-skill-auditor
python demo.py          # 几秒，沙盒，不碰他的数据
```

**从远端 clone 实测**（不是推测）：`demo.py` 7 步全过、`npm test` 全部断言通过。

这个验证抓到过两个真问题，都已修：
1. `npm/engine/` 在 gitignore 里，`prepack` 只在 pack/publish 时跑 → 新 clone 里 `npm test` 连锁失败 30 条 → 补 `pretest` 自动同步
2. 本机 git 的 HTTPS 走不通（`schannel: CRYPT_E_NO_REVOCATION_CHECK`，吊销检查拿不到）→ 改用 SSH 远端推送成功。
   这是**本机环境**问题（代理 + schannel），不是仓库问题；别人用 HTTPS clone 一般不受影响。
