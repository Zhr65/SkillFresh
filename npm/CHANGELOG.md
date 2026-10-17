# Changelog

版本号跟 npm `latest` 对齐：`npm view dsh-skill-auditor version` 是权威，市场目录里写的版本不作数。

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
