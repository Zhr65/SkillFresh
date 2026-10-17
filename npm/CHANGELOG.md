# Changelog

版本号跟 npm `latest` 对齐：`npm view dsh-skill-auditor version` 是权威，市场目录里写的版本不作数。

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
