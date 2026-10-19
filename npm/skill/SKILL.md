---
name: skill-auditor
description: 能力折旧表。当用户问「我装的这些 skill/插件是不是过时了」「换模型后哪些能力退化了」「该删哪些、该补哪些」「这个 skill 到底有没有用」时使用。跨宿主（DSH 插件 / WorkBuddy、Claude Code、Codex 的 skill），基于能力指纹 + golden set 回归，而不是凭感觉。
version: 0.9.0
---

# 能力折旧表（skill-auditor）

模型换代时，装过的 skill / 插件不会报错，只会**悄悄变差**。这个 skill 把「变差」变成可测的数字。

## 三个判断，各自的证据

| 用户的问题 | 怎么判 | 关键前提 |
|---|---|---|
| 能力退化了吗？ | 同一 golden set 在新模型上重跑，通过率降幅 ≥20pp 且方向一致 | 指纹没变。**指纹变了就是「被改过」，不是退化** |
| 新 skill/插件好用吗？ | 消融对照（有它 / 没它）+ 采纳率，算净收益 | 没有对照，只有「感觉不错」 |
| 符合我本人吗？ | 触发精度（该触发时触发、不该触发时不触发）+ 环境对账 + 风格基准 | 用真实历史 prompt，不用想象出来的 |

## 怎么调

插件已注册工具时，直接用工具（这是首选）：

- `skill_audit_status` — 折旧表：指纹 / 基线通过率 / risk / 判定
- `skill_audit_recommend` — 强 / 观察 / 未验证 / 已降级 / 可选精简，五档
- `skill_audit_check` — 当前模型相对上次回归基线是否变了
- `skill_audit_scan` — 装了新东西之后重新发现资产
- `skill_audit_suggest` — 用户的需求没有资产能覆盖时，该补什么

没有工具面（例如只装了 skill 的宿主），走 CLI：

```bash
python <data-dir>/scripts/console.py --host deepseek status
python <data-dir>/scripts/console.py --host deepseek recommend
python <data-dir>/scripts/console.py --host workbuddy status --root ~/.workbuddy-ai/skills
```

`<data-dir>` 默认 `~/.dsh/skill-auditor`（可用 `SKILL_AUDITOR_HOME` 覆盖）。引擎是纯标准库 Python 3.8+，无第三方依赖。

## 用户没有可用的 skill / 插件时

这是本 skill 的**主动机制**，不要等用户问：

1. 先查已装注册表 —— 有覆盖的就不建议新增（避免重复装）；
2. 确实没有 → `skill_audit_suggest --need "<一句话需求>"`；
3. 输出「建议新增 + 搜索词」，**安装由用户确认**，不自动装。

## 硬边界（必须照实说，不许包装成结论）

- **K=1 的基线是自评**，不是统计结论。要下「退化」判决，K≥3。
- golden set 可能含**合成样本**（用户历史不足时）。合成样本能测「结构对不对」，测不出「像不像你」。这类样本在文件里标 `"synthetic": true`。
- `risk` 是「行数 + 工具依赖」的维护面代理，**不代表价值**。高 risk 不等于该删。
- 淘汰、安装都**先确认**；淘汰优先「停用 / 归档」，不静默删除。
- 降级归因只有四类，别扩写：工具调用报错 → 修工具描述；输出结构对内容错 → 重写指令；该触发没触发 → 改触发描述；中间全对终态错 → 才可能是模型能力回归。

## 判定后的动作

| 判定 | 动作 |
|---|---|
| strong | 保留，可置顶推荐 |
| watch | 保留，下次模型变更优先重跑 |
| unverified | 先建 golden set（3–5 条真实 prompt）再判，**暂不淘汰** |
| degraded | 先归因，再决定重写还是停用；不直接删 |
| prune-candidate | 无基线 + 维护面极高 → 建议评估，**不自动删** |
