# dsh-skill-auditor

**能力折旧表** — 给装了一堆 skill / 插件的 agent 做体检。

模型换代时，你装的东西不会报错，只会悄悄变差。这个插件把「变差」变成可测的数字：
每个资产一个**能力指纹**，每个指纹一列**通过率**，指纹没变而通过率掉了，才叫退化。

- 宿主：DeepSeek Harness（插件）· WorkBuddy / Claude Code / Codex（skill，见 [`skill/SKILL.md`](./skill/SKILL.md)）
- 依赖：Node ≥ 18 装插件；运行时需要 **Python 3.8+**（纯标准库，无 pip 依赖）
- 网络：**引擎不联网**。所有判定在本机算，注册表、基线、golden set 都留在本机。

---

## 安装

```bash
# DSH 插件（会被市场/CLI 安装到 profile 的 bundle 列表）
dsh plugin --profile <name> add dsh-skill-auditor

# 本地开发
dsh plugin --profile web add link:<本目录绝对路径>
```

装完重启会话，工具面会多出 5 个 `skill_audit_*` 工具。桌面端装完没反应就完全退出（含托盘）再打开。

**找不到 Python？** 用环境变量指定解释器：

```bash
SKILL_AUDITOR_PYTHON=/usr/bin/python3      # macOS / Linux
set SKILL_AUDITOR_PYTHON=C:\Python312\python.exe   # Windows
```

---

## 更新

**先说一个硬事实：DSH 市场没有「更新」按钮。** 市场安装器对已在 profile 里的包会直接拒绝
（`assertNotInstalled()` → `conflict: This plugin is already managed by the active profile.`），
所以「已安装」视图只有卸载。升级只能走下面三条之一：

| 路径 | 怎么做 | 代价 |
|---|---|---|
| 卸载 → 重装 | 市场 → 已安装 → 卸载 → 回发现页点安装 | 两步，中间插件不在 |
| CLI（web profile） | `dsh plugin --profile web add dsh-skill-auditor` | 桌面 profile 被 Electron 独占，CLI 按设计拒绝 |
| 手动 pnpm | `cd ~/.dsh/profiles/desktop && pnpm add dsh-skill-auditor@latest` | 绕过 Desktop 的 checkpoint，可能被 reconcile 覆盖 |

**怎么知道有新版本**：插件不会主动提醒（引擎不联网）。自己查：

```bash
npm view dsh-skill-auditor version        # 远端最新
cat ~/.dsh/skill-auditor/.engine.json     # 本机已装
```

**更新后你的数据会怎样**：包的 `version` 变了，下次调用工具时自动把 `scripts/` 整体替换成新版
（先删后拷，避免旧版本删掉的脚本残留），**注册表、基线、golden set、`hosts.json` 一条不动**。
这条路径有测试覆盖（`test/smoke.mjs` 第 5 段）。

---

## 第一次用：从「全是未验证」到「有判定」

刚装上时所有资产都是 `unverified` —— 没有 golden set 就没有对照，这是诚实的起点，不是故障。

**① 看清单**（注册表缺失时会自动 scan）

```
skill_audit_status  host=deepseek
```

**② 建 golden set**，两条路：

- **自动起草**（从本机会话里挖真实 prompt）：

  ```bash
  cd ~/.dsh/skill-auditor
  python scripts/console.py --host deepseek bootstrap
  ```

  产出的每条 case 都标 `"draft": true`，`expected` 是 `TODO` —— **必须人工补**，否则测的只是「有没有回话」。

- **手写**：`~/.dsh/skill-auditor/golden/<资产名>.json`，照 `golden/_template.json` 抄，3–5 条就够。
  关键字段是 `input`（真实 prompt）和 `judge`（怎么算通过）。

> ⚠️ DSH 的会话文件是 `session.v4.jsonl.zstd`（zstd 压缩）。`bootstrap` 能识别但**不一定能解压**：
> 需要 Python 3.14+、`pip install zstandard`、或系统有 `zstd` 命令，三者之一。
> 都没有时它会明确报「N 个会话文件解不开」，不会假装 0 条。

**③ 记模型戳 + 建基线**

```bash
python scripts/console.py --host deepseek bump <当前模型版本>   # 让 check 有参照
python scripts/baseline.py --golden golden/<资产名>.json --out results/ --model <当前模型版本>
# 生成 scaffold → 用当前模型跑一遍 → 回填 true/false → --from recorded.json 再跑一次
```

之后每次模型换代，`skill_audit_check` 会告诉你「基线是旧模型的」，再重跑一轮才有降级判定。

---

## 六个工具

| 工具 | 干什么 | 会写文件吗 |
|---|---|---|
| `skill_audit_status` | 折旧表：指纹 / 基线通过率 / risk / caps / golden / 判定 | 否（注册表缺失时会先自动 scan） |
| `skill_audit_recommend` | 强 / 观察 / 未验证 / 已降级 / 可选精简 五档 + 建议新增 | 否 |
| `skill_audit_check` | 当前模型相对上次回归基线变了吗 | 否 |
| `skill_audit_scan` | 重新发现资产，写 `registry.<host>.json`，并 diff 能力面 | 是（只写注册表） |
| `skill_audit_probe` | **运行时证据**：读宿主日志，找出「装得像、跑不起来」的插件 | 否 |
| `skill_audit_suggest` | 需求没有资产覆盖时，该补什么 + 搜索词 | 否 |

默认审计对象是 DSH 的**插件**（`--host deepseek`）。审 skill 传 `host: "workbuddy"` / `"claude-code"` / `"agents"`；审任意目录传 `host: "generic"` + `root: "<路径>"`。

### 为什么插件要单独一个 probe

插件退化不是「回答变差」，而是**工具没注册 / 路由 404 / 依赖 CLI 缺失**。这类故障里，文件齐全、`package.json` 正确、能力面（caps）也正常 —— 静态扫描永远看不见。

`probe` 读宿主自己打的日志（`~/.dsh/logs/*.log`），按包名归因，抓的是**实际发生的**而不是**声明的**：

```
⚠ 1 个资产有运行时故障迹象（文件齐全、manifest 正确，但跑起来报错）：
  @wxg-prc-cpg/browser-skill-dsh-plugin
    2 条故障迹象  最近 2026-10-17T13:51:58Z  [info]
      bsk probe failed (spawn bsk ENOENT); browser tools will report install guidance…
```

**盲区（明说）**：插件不写日志就看不见；日志轮转后旧故障消失；从没被用过的插件没有证据；`info` 级故障靠关键词识别，可能漏也可能误报。**没有证据 ≠ 没问题。**

真正要「宿主实际注册了哪些工具」得启动宿主 —— DSH 的 `desktop` profile 被 Electron 独占（`dsh --profile desktop --dump-config` 直接拒绝），没有 headless 入口，所以没做。

---

## 数据落在哪

```
$DSH_HOME/skill-auditor/            # 默认 ~/.dsh/skill-auditor
├── scripts/                        # 引擎（首次运行从包里复制出来）
├── golden/                         # 每个资产的 golden set（你的，不会被升级覆盖）
├── hosts.json                      # 宿主表，可自己加
├── registry.<host>.json            # 每个宿主一份资产注册表
├── baseline-results.json           # 判定结果
└── model-version.json              # 当前模型戳
```

升级插件**只覆盖 `scripts/` 和文档**，不碰你的注册表、基线、golden set。要换位置：`SKILL_AUDITOR_HOME=/path/to/dir`。

---

## 判定口径（照实说，不包装）

| 词 | 意思 |
|---|---|
| **指纹** | `hash(正文 + 触发条件 + 工具依赖 + 输出模板 + 运行时参数)`。指纹变了 → 是「被改过」，不是模型退化 |
| **退化** | 同一 golden set、K≥3、通过率降 ≥20pp 且方向一致。K=1 只能算「疑似」 |
| **risk** | 行数 + 工具依赖的维护面代理。**不代表价值**，高 risk 不等于该删 |
| **unverified** | 没有 golden set。**暂不淘汰**，先建 3–5 条真实 prompt |

淘汰和安装都只给建议，**不自动执行**。淘汰优先「停用 / 归档」，不静默删除。

---

## 发布者备忘

```bash
npm run sync      # 把上游引擎同步进 engine/（含脱敏 + 禁用词扫描，prepack 自动跑）
npm run lint      # 三个 .js/.mjs 的语法检查
npm run dry       # npm pack --dry-run，看最终包内容
```

进 DSH 市场的一键安装要同时满足：唯一合法 npm 名、非 Desktop 自有 bundle、
`registry.npmjs.org/<pkg>/latest` 同名且**精确稳定版本**（不能 prerelease）、
manifest 声明安全的相对路径 `dsh.bundle.patch`。发布前先 `npm view dsh-skill-auditor version` 确认名字没被占。

## License

MIT
