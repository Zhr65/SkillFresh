# 发布与收录：三份可执行材料

三件事：npm 发布、进 DSH 市场、进 WorkBuddy SkillHub。
每份都写成「照着做就行」，并标出哪一步卡在谁身上。

先说结论，因为**我上一轮的判断有一处是错的**：

| 事项 | 我之前说卡在哪 | 实际卡点 | 现在的状态 |
|---|---|---|---|
| `npm publish` | 你的 npm 账号 + 2FA | 就是这个，绕不过 | 命令一条，`npm test` 44 项已过 |
| 进 DSH 市场 | 「得先有 npm 包」 | **一半对**：内置市场只认 npm；但自建目录来源可以走 github 固定 commit，不需要 npm | 条目已写好，已过市场自己的校验器 |
| WorkBuddy SkillHub | 走市场发布流程 | 缺的是「提交入口在哪」这个确认，不是能力 | `_meta.json` + 打包脚本就绪 |

---

## 1. `npm publish`

### 1.1 命令（五分钟）

```bash
cd npm
npm login                      # 浏览器登录；开了 2FA 会要 OTP
npm whoami                     # 确认登的是哪个账号，别发到别人的 scope 下
npm publish --dry-run          # 先看会传哪些文件（这一步就会跑 prepack）
npm publish                    # 若账号是 auth-only 2FA：npm publish --otp=123456
```

期望输出末行：`+ dsh-skill-auditor@0.5.0`

`npm publish` 会触发 `prepack` → `scripts/sync-engine.mjs`，把上游引擎白名单复制进
`engine/`、脱敏本机路径、扫禁用词。命中禁用词会**直接失败**，这是设计，不是 bug。

### 1.2 发完立刻验三件事

```bash
npm view dsh-skill-auditor version          # → 0.5.0
npm view dsh-skill-auditor dist.tarball     # → https://registry.npmjs.org/.../-/dsh-skill-auditor-0.5.0.tgz
npm view dsh-skill-auditor dsh              # → { bundle: { patch: './cordis.patch.yml' } }
```

第三条最关键。市场准入的第 4 条就是查这个字段，查不到就只显示「仅供展示」。

### 1.3 几个会踩的点

| 现象 | 原因 | 处理 |
|---|---|---|
| `E402 Payment required` | 包名被占 / 账号未验证邮箱 | 先去网页确认邮箱已验证；`npm view dsh-skill-auditor` 当前是 404，名字可用（已实测） |
| `EOTP` | 账号开了 auth-only 2FA | 加 `--otp=<6 位>`，或先 `npm profile enable-2fa` 改成 auth-and-writes |
| 想发到 `@zhr65/dsh-skill-auditor` | scoped 包默认私有 | 必须加 `--access public`；但市场准入要求能标准化出唯一包名，非 scoped 更省事 |
| 发布内容里有本机路径 | 脱敏规则漏了 | `npm run sync` 会再扫一遍，看输出里哪一行没被替换 |

### 1.4 版本怎么升（这个有坑）

单一事实源是 `npm/package.json`，但有四处要一起改，否则版本号互相打架：

```
npm/package.json      version
npm/plugin.json       version
_meta.json            version          # SkillHub 读这个
SKILL.md              正文里的版本号
npm/CHANGELOG.md      加一节
```

固定顺序：

```bash
# 1. 改上面四处版本号
npm run check:engine        # 引擎和上游一致？（见 §1.5）
npm test                    # 44 项 + 打包→安装→运行全链路
git add -A && git commit -m "release: 0.5.0"
git push
cd npm && npm publish       # 先提交再发布，别反过来
cd .. && node catalog/stamp.mjs   # 把 catalog 条目的 commit 钉到这次提交
```

### 1.5 `engine/` 入库了，这是本轮的新决定

`npm/engine/` 原本在 `.gitignore` 里，靠 `prepack` 生成。现在改成入库，原因见 §2.1：
DSH 市场的 github 安装通道拿到的就是仓库里那一份，没有 `prepack` 会替你跑。

代价是多了一份重复代码，所以要有个东西拦住漂移：

```bash
npm run check:engine
# 先重建 engine/，再看 git 眼里它跟 HEAD 有没有差
# [ok]   → 提交里的那份就是上游此刻的样子
# [fail] → 你改了上游脚本没提交 engine/，或者手改了 engine/
```

不跑这一步就发布的后果：npm 通道装到新引擎，github 通道装到旧引擎，同一个版本号两种行为。

---

## 2. 进 DSH 市场

### 2.1 先纠正一处：市场有两条安装通道，能力不一样

| 通道 | 谁在用 | 能装什么 | 需要 npm 吗 |
|---|---|---|---|
| **内置 1024Store 目录** | 桌面端「插件市场」的默认源 | 只认 npm 包 | **是，硬门槛** |
| **自建 catalog source** | 用户在市场里手动「添加来源」 | 认 `installSource.kind = "github"` + 固定 commit | 不需要 |

源码依据（不是文档推断）：

- `lib/adapters/dsh-1024store.js:219-228` —— 内置源的适配器只产出 `package: { registry: 'npm', name }`，**从不产出 `installSource`**
- `lib/install/github.js` + `lib/install/service.js:319-321, 448-453` —— 安装服务本身支持 github 固定 commit：取 `raw.githubusercontent.com/<owner>/<repo>/<commit>/<subdir>/package.json` 验身份，再 `pnpm add github:owner/repo#commit&path:/subdir`

所以上一轮我说「PR 得先有 npm 包」——对**内置市场**成立，对**整个市场**不成立。
官方 `CONTRIBUTING.md` 也写了：「A plugin without a published npm package is still
catalogued, listed browse-only」——**能收录，但只能看不能一键装**。

### 2.2 收录材料的真实格式

这个不是「在 PR 描述里写一段话」，而是**提交一个 JSON 文件**。规则抄自
`imsai-sh/awesome-deepseek-harness-plugins` 的 `CONTRIBUTING.md`：

| 规则 | 我们的情况 |
|---|---|
| 一个插件一个 JSON，放 `catalog/plugins/` | 待新建 |
| id 三段式 `owner/repo/子目录`（monorepo 用） | `Zhr65/SkillFresh/npm` |
| 文件名 = 各段小写、非字母数字转 `-`、用 `--` 连 | `zhr65--skillfresh--npm.json` |
| 子目录 id 要求 manifest **正好**在 `<子目录>/package.json` | `npm/package.json` ✓ |
| 只能新增这一个文件，不许碰 README/工作流 | 照做 |
| `dsh.bundle.patch` 指向的文件必须在同一 revision 里 | `npm/cordis.patch.yml` ✓ |
| 通过 static-review 且只新增 1 条 → **自动 squash-merge** | 无需等人工 |
| 仓库要加 `dsh-plugin` topic | 待加 |

条目内容就是 `catalog/awesome-plugin-entry.json`，已按
`catalog/schema/plugin.schema.json` 逐字段验过（含 `description.en/zh` 长度、
`repository` 正则、`category` 正则、`added` 日期格式、文件名推导）。

### 2.3 你要做的三步（约 10 分钟）

**① 加 topic**（GitHub 仓库首页 → 齿轮图标 → Topics → 填 `dsh-plugin` → Save）

**② 提 PR**

```bash
git clone https://github.com/imsai-sh/awesome-deepseek-harness-plugins.git
cd awesome-deepseek-harness-plugins
git checkout -b add-dsh-skill-auditor
cp /path/to/SkillFresh/catalog/awesome-plugin-entry.json \
   catalog/plugins/zhr65--skillfresh--npm.json
git add catalog/plugins/zhr65--skillfresh--npm.json
git commit -m "feat(catalog): add Zhr65/SkillFresh/npm"
git push -u origin add-dsh-skill-auditor
```

**③ 开 PR，标题与正文直接粘下面这段**

标题：

```
feat(catalog): add Zhr65/SkillFresh/npm (dsh-skill-auditor)
```

正文：

```markdown
Adds one new catalog entry for `Zhr65/SkillFresh/npm` (`dsh-skill-auditor`).

**What it does.** Audits installed skills and plugins against the current model.
Each asset gets a capability fingerprint computed only over its capability
surface (code plus declared tools/commands/dependencies, excluding README and
images), then a golden set is replayed to build a pass-rate matrix. A drop is
reported as model degradation only when the fingerprint is unchanged; an
unchanged pass rate with a changed fingerprint is attributed to the asset having
been edited. Registers six tools (`status`, `recommend`, `check`, `scan`,
`probe`, `suggest`). All evaluation runs locally with no network calls.

**Self-check against the submission rules.**

- [x] `npm/package.json` declares `dsh.bundle.patch: "./cordis.patch.yml"`
- [x] the patch file is committed at the same revision
- [x] manifest is exactly at `npm/package.json`, matching the id path
- [x] only `catalog/plugins/zhr65--skillfresh--npm.json` is touched in this PR
- [x] `dsh-plugin` topic added to the repository
- [ ] npm package published — **not yet**; this entry is expected to be listed
      browse-only until it is

**Notes for review.** The package needs Python 3.8+ on the host at runtime; the
engine is stdlib-only with no third-party dependencies. It ships as a DSH plugin
(6 tools) and also as a host-neutral skill for WorkBuddy / Claude Code / Codex.

Repository: https://github.com/Zhr65/SkillFresh
```

**④ 等 bot 评论 `Plugin submission review / static-review` 通过** → 自动 squash-merge
→ `catalog-sync` 推到 D1 → 几分钟后出现在 deepseek1024.com 和两个 README 投影里。

### 2.4 不想等 PR、或不想发 npm：自建目录来源

这条路完全在自己手里，代价是用户要手动加一次来源。

**硬要求**（`catalog-source.schema.json`）：HTTPS、标准 443 端口、无 query/fragment、
路径必须以 `/v1/plugins` 结尾。

- 来源清单：`catalog/catalog-source.example.json`（把 endpoint 换成你的域名）
- 目录页：`catalog/catalog-provider-page.json`（已过市场自己的 `parseCatalogProviderPage`）
- 两者都可以用 `node catalog/validate.mjs` 复验

最小部署（Cloudflare Worker，两个静态响应）：

```js
export default {
  async fetch(req) {
    const url = new URL(req.url)
    if (url.pathname === '/catalog-source.json') {
      return Response.json(SOURCE)                 // catalog-source.example.json 的内容
    }
    if (url.pathname === '/v1/plugins') {
      return Response.json(PAGE)                   // catalog-provider-page.json 的内容
    }
    return new Response('not found', { status: 404 })
  },
}
```

用户侧：市场 → 来源管理 → 添加来源 → 填 `https://<你的域名>/catalog-source.json`。

**代价说清楚**：市场同一时间只启用一个来源（`service.js` 的 `select` 会把其他全部
`enabled: false`）。用户加你的来源，等于把内置 1024Store 顶掉。所以这条路适合
「自己人用 / 内部分发」，不适合当公开分发主渠道。

### 2.5 本机已经验过的（不是推测）

| 验证 | 怎么验的 | 结果 |
|---|---|---|
| 条目能被市场读 | 直接 import 市场自己的 `parseCatalogProviderPage` | 通过（第一版被拒：`description` 里有换行，`plainText` 禁控制字符；已改） |
| github 来源能被市场认 | 直接 import 市场的 `createGitHubPackageVerifier`，走真网络取清单 | `{packageName: dsh-skill-auditor, version: 0.5.0, bundlePatch: ./cordis.patch.yml}` |
| 固定 commit 真能装 | `pnpm add "github:Zhr65/SkillFresh#8f195f0...&path:/npm"` | 19 秒装完，`dsh-skill-auditor 0.4.1`（当时版本） |
| 装完真能跑 | 从安装位置 import 插件、跑 `apply(ctx)` | 注册 6 个工具，`engine/scripts/` 6 个脚本都在 |

第 1 条那个失败很值得记：schema 里 `plainText` 禁止 U+0000–U+001F，**换行也算**。
自己照文档抄规则一定会漏这种，所以 `catalog/validate.mjs` 是去 import 市场那份实现，
而不是复写一遍。

---

## 3. WorkBuddy SkillHub

### 3.1 现状

- `_meta.json` 已就绪：`slug/title/version/author/runtime/emoji/color/tags/category` +
  中英双语 description，`category` 取自市场 API 的真实枚举值 `ai-agent`
- 市场站：`https://lightmake.site`，语义搜索 API 可用
- 缺的是**提交入口的确认**，不是材料

### 3.2 打包

```bash
python scripts/build_dist.py        # → dist/skill-auditor.zip
```

`dist/` 在 `.gitignore` 里（构建产物不入库），zip 内容按白名单打，不含
`registry.*.json` / `baseline-results.json` / 个人 `golden/*.json`。

当前 17 个文件。`demo.py` 也在里面 —— README 和 SkillHub 描述都指向「30 秒试一下」，
不带它，从市场装的人第一步就跑不动（补这个是因为打完包才发现白名单漏了它）。

**本机验过自包含**：解压 zip 到临时目录 → 直接 `python demo.py` → 7 步跑完，
returncode 0，全程只写自己的沙盒目录。

### 3.3 提交（这一步要你登录确认）

我没找到公开的自助提交 API（`/api/v1/search`、`/api/v1/download` 是读接口）。
所以只剩两条：

1. 打开 `https://lightmake.site`，登录后找「上传 / 提交技能」入口，填 `_meta.json`
   里的字段，上传 zip
2. 若站点没有自助入口，去它对应的仓库提 issue 附 zip + `_meta.json`

**提交时别重写描述** —— 直接抄 `_meta.json` 的 `description_zh` / `description_en`，
否则搜索命中率会和别处不一致。

### 3.4 和 DSH 条目的区别（别混）

| | DSH 市场 | WorkBuddy SkillHub |
|---|---|---|
| 受众 | DSH 用户 | WorkBuddy / Claude Code 用户 |
| 形态 | npm 包（含 `cordis.patch.yml`） | skill（`SKILL.md` + `scripts/`） |
| 元数据 | `catalog/plugins/*.json`（别人仓库） | `_meta.json`（自己仓库） |
| 审核 | 静态门禁 + 自动合并 | 站点审核 |
| 版本权威 | npm `latest` | `_meta.json` 的 `version` |

两边版本号必须一致，否则用户会看到「市场说 0.5.0、装完是 0.4.x」。

---

## 4. 三个卡点，逐条说清

### 4.1 「需要你的 npm 账号」—— 真的，绕不过

`npm publish` 是唯一必须你本人做的动作。我不能代你登录，也不该拿你的凭据。
但要做的只有：登录一次 → 跑一条命令。之后每次发版同一条命令。

### 4.2 「得先有 npm 包，PR 才能指过去」—— 一半错

- 对**内置 1024Store 市场**：成立。它只从 npm 装，没包就只显示「仅供展示」
- 对**整个 DSH 市场**：不成立。安装服务支持 `installSource.kind = "github"` + 固定 commit，
  这条路我用真网络 + 真 pnpm 跑通了（§2.5）

所以这一项的「卡点」不是技术，是**渠道选择**：
想进默认市场 → 必须先发 npm；只想让自己人装上 → 自建来源，今天就能用。

### 4.3 「走市场发布流程」—— 缺的是入口确认，不是能力

材料齐了。真正没确定的是 SkillHub 有没有自助提交页面。这个要你登录看一眼，
或者告诉我站点结构，我去把提交表单要的字段补齐。

---

## 5. 每次发新版的固定动作

```bash
# 1. 改版本号（四处，见 §1.4）
# 2. 验引擎没漂
npm run check:engine
# 3. 全链路测试
npm test
# 4. 提交并推送
git add -A && git commit -m "release: x.y.z" && git push
# 5. 发布
cd npm && npm publish
# 6. 钉 catalog 条目的 commit
cd .. && node catalog/stamp.mjs && node catalog/validate.mjs
# 7. 提交 catalog 变更
git add catalog && git commit -m "chore(catalog): pin x.y.z" && git push
# 8. 验线上
npm view dsh-skill-auditor version
```

第 5 步和第 6 步顺序不能反：`stamp` 钉的是**已提交**的代码，钉完再提交 catalog，
pin 才指向一个真实存在、内容不会再变的 revision。

---

## 6. 现在就能自己验的

```bash
node catalog/validate.mjs      # 用市场自己的校验器验条目
npm run check:engine           # 引擎与上游一致
npm test                       # 44 项 + 全链路
python scripts/test_engine.py  # 引擎回归（16 项，含 GBK / 合并 / 漂移）
python demo.py                 # 几秒沙盒演示，不碰真实数据
```

前两条是本轮新增的，都是为了防止「测试全绿但发出去的东西不对」这类问题。
