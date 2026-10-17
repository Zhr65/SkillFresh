# Changelog

版本号跟 npm `latest` 对齐：`npm view dsh-skill-auditor version` 是权威，市场目录里写的版本不作数。

## 0.5.0 — 2026-10-18

### 修正：三处会让结论说反话的状态错误（都有回归测试）

- **升级闭环闭合**：`report` 默认只写报告、不改基线，文档却让人 `report → scan`，结果面板永远不变。
  新增 `report --promote`：把本次 results 按资产合并进 `baseline-results.json`，再提示 `scan`。
- **基线导入不再整体覆盖**：`baseline.py` 默认按资产合并（本次跑到的替换、没跑到的保留并打印），
  `--replace` 才整体覆盖。以前只补一个 skill 会把其他资产的基线静默清空。
- **旧模型基线不再冒充绿灯**：`scan` 只统计当前模型戳下的判定；换代后未重跑的资产回到 `unverified`，
  并打印「N 条基线属于旧模型，未计入」。

### 修正：判定口径

- `regress.py` 的「方向一致」现在真的检查方向：降幅 ≥ 阈值且**没有任何用例变好**才算 DEGRADATION；
  混有变好的用例降为 `volatility watch`。报告新增 `worse_cases/better_cases`。
- 基线合并后 `baseline-results.json` 新增 `models` 列表与 `merged_at`。

### 修正：Windows 中文控制台

- `✓ ✗ ⚠ 🟢…` 在 GBK（cp936）控制台下会 `UnicodeEncodeError`，`hosts`/`recommend` 直接崩。
  现在按 stdout 实际编码选择符号（UTF-8 保留原符号，GBK 降级为 `[ok]`/`●`/`*`），中文照常输出。
- `--out` 现在对**所有**数据生效（registry / baseline / 模型戳 / golden）；以前只搬一半。
- JSON 读取统一 `utf-8-sig`（Windows 记事本/PowerShell 的 BOM 不再炸）。
- `run()` 子进程固定 UTF-8 解码，父进程设了 `PYTHONIOENCODING` 时不再乱码。

### 修正：解析与建议

- frontmatter 支持 `description: |` / `>` 块标量；以前 frontend-dev 的描述会变成字面量 `|`。
- `gap_analysis` 改子串匹配：已有 `test-driven-development`/`verification-before-completion` 时
  不再假报「缺评测类」。
- `suggest` 名称匹配增加名称片段（`frontend-dev` 可被「做个前端页面」命中）。

### 修正：模型戳与数据路径

- `model-version.<host>.json`；`bump`/`check`/`setup` 都按宿主读写，旧 `model-version.json` 仅作 fallback。
- `baseline.py` 默认 `--out` 改为 skill 根目录（以前写进 `scripts/`，`scan` 看不见）。
- 新增 `--allow-draft`；draft 用例默认跳过。

### 工程质量

- 新增 `scripts/test_engine.py`（16 项，stdlib `unittest`）：指纹口径、块标量、基线合并、阈值/方向/漂移、
  模型过滤、GBK 输出。不随包分发。
- `demo.py` 每步检查退出码（以前报告以外的步骤失败也会继续）；符号按编码降级。
- `install.py`：复制时排除个人数据；旧版本副本报 `stale` 而不是「已安装」；`--copy` 也做安装后校验。
- `fingerprint.py` 默认输出改到 skill 根目录（不再往被扫描的宿主目录里写报告）。
- 文档修正：入口数量、probe 缺失、golden 指纹过期、自检数字、安装命令、目录树、发布清单。

### 已知未做（仍是边界）

- 模型切换事件源仍手动；K=1 自评；插件仍无主动运行时探针；消融 / 采纳率 / 净收益仍只有方法论。

## 0.4.1 的未发布内容（随 0.5.0 首发）

### 新增：github 安装通道

- `engine/` **入库**（原在 .gitignore，靠 prepack 生成）。原因：DSH 市场的 github 安装通道
  （目录条目 `installSource.kind = "github"` + 固定 40 位 commit）拿到的就是仓库里这一份，
  没有 prepack 会替你跑 —— 忽略它，那条通道装出来的包没有引擎，一调用就失败
- 新增 `npm run check:engine`：重建 `engine/` 后与 git HEAD 比对，拦「改了上游脚本没提交 engine/」。
  不拦的后果：npm 通道装新引擎、github 通道装旧引擎，同一个版本号两种行为
- `engine-manifest.json` 去掉 `built_at` 时间戳：清单必须可复现，否则每次 sync 都产生 diff，
  check 就永远报「不同步」。溯源靠 version + 逐文件 sha256
- `package.json` 补 `repository` / `homepage` / `bugs`（npm 页面与 github 通道都用得上）

### 实测（发布材料，见根目录 PUBLISH.md）

- DSH 目录条目通过市场自己的 `parseCatalogProviderPage`（第一版被拒：description 里有换行，
  `plainText` 禁 U+0000–U+001F，换行也算 —— 所以校验用市场那份实现，不复写规则）
- github 来源通过市场自己的 `createGitHubPackageVerifier`（真网络取固定 commit 的 package.json）
- `pnpm add "github:Zhr65/SkillFresh#<sha>&path:/npm"` 装出 0.4.1，6 工具注册，引擎随包落地

## 0.4.1 — 2026-10-17

### 修正：指纹口径 v2（`v2-ability-surface`）

**这是一次会改变已有指纹的升级**：旧 registry 里的指纹全部失效，需要重跑一次 `scan` 和一轮回归。

- 指纹现在**只覆盖能力面**：`SKILL.md` / `scripts/` / `lib/` / `dist/` / `package.json` / `plugin.json` / `cordis.patch.yml` / 模板
- 排除 `README*`、`CHANGELOG*`、`LICENSE*`、图片/音频/字体、`*.map`、锁文件、`node_modules/`、`test*/`、`docs/`
- 原因：改 README 不改能力，却会让指纹动、产生**假的** `FINGERPRINT-DRIFT`。实测某插件 1437 KB 里有 57 KB 是 README，
  另一个插件 67 个文件 / **16 MB** 全是打包产物 + 文档 —— 噪声盖住了信号
- 验证：改 README → 指纹不变；加图片 → 不变；改 `lib/index.js` → 变

### 新增：能力面（capability surface）

- `scan` 现在额外记录每个资产**对外提供什么**：`patch` / `main` / `bin` / `exports` / `deps` / `skills` / `mcp` / `hooks` / `insert` / `scripts`
- 与上次扫描对比，变化会显式打印：`[caps] 1 个资产的能力面有变化 … +bin`
- `status` 新增 `caps` 列（能力面条目数）
- 为什么插件需要这个：插件退化的形态是「工具没注册 / 路由 404 / 钩子没跑」，不是「回答变差」。
  用 prompt golden set 测插件测的是错的东西；能力面 diff 不用启动宿主就能抓到「新版本少了个入口」

### 新增：`skill_audit_probe`（运行时证据，第 6 个工具）

- 读宿主自己打的日志（`~/.dsh/logs/*.log`），按包名归因，找出「文件齐全、manifest 正确、但跑起来报错」的插件
- 只读、不启动宿主、不占端口，desktop / web 都能用
- 实测抓到真例：`@wxg-prc-cpg/browser-skill-dsh-plugin` 的 `bsk` CLI 缺失 →
  日志里 `bsk probe failed (spawn bsk ENOENT)`，而文件/manifest/能力面全部正常
- 同时修了 `hosts.json` 的合并语义：以前整个条目替换内置默认，导致老配置文件丢掉新增字段
  （表现为「明明配了日志却说没有」）；现在逐字段合并

### 边界（没做，别当成有）

- **没有主动探针**：能力面是 manifest **声明**的，日志是**实际发生**的，但都不是「宿主实际注册了哪些工具」。
  DSH 的 `desktop` profile 被 Electron 独占（`dsh --profile desktop --dump-config` 直接拒绝），
  没有 headless 入口，要做得逆向 IPC 或驱动 UI —— 贵且脆，没做
- `probe` 的盲区：插件不写日志就看不见；日志轮转后旧故障消失；从没被用过的插件没有证据；
  `info` 级故障靠关键词识别，可能漏或误报。**没有证据 ≠ 没问题**

## 0.4.0 — 2026-10-17

首个可发布版本。

### 新增

- **DSH 插件形态**：`package.json` 声明 `dsh.bundle.patch` → `cordis.patch.yml`，注册 5 个工具
  （`status` / `recommend` / `check` / `scan` / `suggest`）
- **引擎随包分发**：首次调用把 `engine/` 复制到 `$DSH_HOME/skill-auditor/`，之后所有读写都在那里，
  不碰 `node_modules`（只读 + `npm update` 会整个换掉）
- **自带 skill**（`skill/SKILL.md`）：同一份包在只认 skill 的宿主里也能用
- **跨宿主审计**：`host` 参数支持 `deepseek` / `workbuddy` / `claude-code` / `agents` / `generic`
- **首次使用自动 scan**：注册表缺失时自动发现资产并写入 `registry.<host>.json`
- **`bootstrap` 支持 zstd 压缩的会话**：DSH 存的是 `session.v4.jsonl.zstd`，现在能被识别；
  解不开时明确报数量并给出三种解法，不再静默返回 0 条

### 修正

- 引擎升级改为**先删后拷 `scripts/`**：旧版本删掉/改名的脚本不再残留（否则可能 import 到旧模块）
- 没有 Python 时给出可执行的指引（装 Python 3 或设 `SKILL_AUDITOR_PYTHON`），不再静默失败

### 数据兼容

- `registry.*.json`、`baseline-results.json`、`model-version.json`、`golden/*.json`、`hosts.json`
  在升级中**保持不变**；`.engine.json` 只记录已装引擎版本，用于判断要不要替换代码
- 升级行为有测试覆盖：`test/smoke.mjs` 第 5 段（版本变化 → 代码替换 → 用户数据不变）
