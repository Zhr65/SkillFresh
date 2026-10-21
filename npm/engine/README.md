# dsh-skill-auditor

**你的 DSH 装了 20 个插件，但你真的用上了吗？**

这个工具回答三个问题——**都在对话里，不用你敲命令**：

---

## 1. 「那个查余额的叫啥来着？」

你装了插件，但忘了名字。

> 你：帮我查下余额
>
> 它：（查已装插件）你装的是 `dsh-whale-widget`，直接说「用鲸鱼挂件看余额」就行

不用记插件名，也不用知道它暴露了什么工具。**说功能就行**。

---

## 2. 「这些插件还好用吗？」

插件会坏、会过时，但通常没人告诉你。

装好后**自动体检**（宿主启动 8 秒后跑一次，不阻塞你），结论写在一个文件里：

```
🔴 建议先修（1）
  browser-skill   装是装上了，但依赖的 bsk 命令行找不到

⬆️ 建议升级（1）
  dshmarket       1.66.5 → 1.66.42（落后 37 个版本）

✅ 正常（3）
```

它区分三种事：**跑不起来**（修环境）、**版本落后**（升级）、**能力退化**（模型换代后重跑用例测出来的）。

第三件是这东西的核心——**换模型后，同一批用例重跑，通过率掉超过 20pp 才算退化**。不是「感觉变笨了」，是有数据。

---

## 3. 「我这插件是不是只用了个皮毛？」

装了插件，但只用了最显眼那个按钮。

```
能力利用盘点 · 14 个工具
已用 1 / 14    未动用 13

💡 还在睡觉的 13 个：
  · hindsight_capture_initiative  —— 能把重要讨论存成可检索的记忆
  · bsk_tabs                      —— 多标签页操作，批量对比页面用得上
  · bsk_session                   —— 管理多个浏览器会话
```

每个没用上的工具都告诉你**它是干什么的、什么场景该用**。数据来源是插件自己的源码——工具名和描述都是扫出来的，不靠插件作者自觉。

---

## 装

**方式一：当 DSH 插件用**（推荐，能力全开）

```bash
dsh plugin --profile web add dsh-skill-auditor
```

装完重启 DSH，8 秒后自动体检。

**方式二：当普通 CLI 用**

```bash
npm i -g dsh-skill-auditor
dsh-skill-auditor            # 裸跑 = 直接出体检结论
```

---

## 为什么值得信

这个工具自己就反对「凭感觉下结论」：

- **不猜**：判不出来就说「判不出来」。模型版本推不到时它拒绝写戳，而不是写个假的。
- **不伪造**：插件没有可调用工具时，它说「这是纯 UI 插件，让用户在界面里用」，不会假装能调。
- **数据说话**：能力退化要「指纹没变 + 通过率掉 ≥20pp + 方向一致」三条同时成立。只掉一点或者有升有降，都只标「观察」，不叫退化。
- **零依赖、纯本地**：只用 Python 标准库。判定、体检、盘点全在你机器上跑，除了可选的插件市场查询，不联网。
- **不自动改任何东西**：不自动卸载、不自动升级、不改你的配置。只报告。

---

## 它怎么做到「说功能就能用」

三步，全在本地：

1. **扫**——读插件源码，提取它注册了哪些工具、每个工具干什么
2. **路由**——把用户的话（「帮我记住这个」）映射到功能域（记忆），再映射到具体插件和工具名
3. **交付**——把工具名交给模型，模型直接调用

支持 28 个计算机方向的功能域（数据库、部署、测试、爬虫、鉴权、监控、CI…），中文说法和英文说法都认。

---

## 支持的宿主

| 宿主 | 评什么 | 目录 |
|---|---|---|
| **DSH** | 插件 | `~/.dsh/profiles/<profile>` |
| WorkBuddy | skill | `~/.workbuddy-ai/skills` |
| Claude Code | skill | `~/.claude/skills` |
| 通用 | 任意 | `--root` 指定 |

同一套引擎，每个宿主只是一个适配器（知道资产在哪、清单长什么样）。

---

## 30 秒看效果（不碰你的数据）

```bash
git clone https://github.com/Zhr65/dsh-skill-auditor.git && cd dsh-skill-auditor
python demo.py
```

它在 `demo/` 沙盒里造 6 个假 skill，真跑一遍完整流程：扫描 → 建基线 → 模拟模型换代 → 重跑 → 判定谁退化了。全程不读也不写你的任何真实数据。

---

## 命令附录

<details>
<summary>日常就三条（点开看）</summary>

```bash
dsh-skill-auditor              # 体检（裸跑就是这个）
dsh-skill-auditor insights     # 能力利用盘点
dsh-skill-auditor find "记忆"  # 按功能找插件
```

</details>

<details>
<summary>全部命令（开发者向）</summary>

```bash
python scripts/console.py can "做个PPT"      # 这件事能不能做
python scripts/console.py find "查余额"       # 按功能找资产
python scripts/console.py insights           # 能力利用盘点
python scripts/console.py audit              # 体检（版本+运行时+基线）
python scripts/console.py status             # 折旧表
python scripts/console.py recommend          # 推荐/淘汰面板（六档）
python scripts/console.py check              # 模型换代了吗
python scripts/console.py probe              # 读日志找运行时故障
python scripts/console.py scan               # 重新扫描资产
python scripts/console.py suggest --need "X" # 缺能力时推荐
```

所有命令加 `--host <宿主>` 指定评哪个宿主，`--out <dir>` 换数据目录。

**退出码可编程**：`0` 正常 / `2` 有问题或需要重跑 / `3` 缺能力。

</details>

<details>
<summary>建模基线（想让判定更准才需要）</summary>

装完就能用，基线是可选的加强项。想跑：

```bash
python scripts/baseline.py --from-seed --host deepseek --model <当前模型>
python scripts/console.py --host deepseek scan
```

`seed/` 是随包分发的公共用例库（35 条，覆盖 10 个常见资产），`--from-seed` 只取你真实装了的那些，不凑数。

模型升级后重跑一遍，就能看到「哪些资产在新模型上退化了」。

</details>

<details>
<summary>数据都在哪</summary>

全部本地，个人数据不进仓库、不进包：

| 文件 | 内容 |
|---|---|
| `registry.<host>.json` | 资产清单 + 指纹 + 工具名 |
| `baseline-results.json` | 判定记录 |
| `model-version.json` | 当前模型戳 |
| `last-audit.txt` | 最近一次体检报告 |

安装器复制到其他宿主时**只带代码和文档**，不搬注册表、基线、个人用例。

</details>

<details>
<summary>项目结构</summary>

```
scripts/      引擎（8 个脚本，纯标准库）
npm/          DSH 插件 + npm 包（9 个工具）
seed/         公共用例库（随包分发）
golden/       你自己的用例（不上传）
catalog/      市场收录条目
demo.py       沙盒演示
```

</details>

---

## 开发

```bash
python scripts/test_engine.py    # 95 项引擎测试
python demo.py                   # 端到端演示
cd npm && npm test               # 插件冒烟 + 打包安装全链路
```

MIT License。

---

<sub>这个 README 的原则：**先讲你能得到什么，再讲怎么敲**。看不明白哪段，是写的人的问题，不是你的问题。</sub>
