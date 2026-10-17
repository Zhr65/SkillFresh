# MVP 基线现状（golden sets + baseline）

从本地历史会话 transcript 真实挖出的用户任务，已为 4 个 skill + 1 个插件建好 golden set。
挖掘方法：`console.py bootstrap`（剥离 `<system-reminder>` 等注入块后取真实 query，按关键词归桶）。
早期那次是用 `mine_history.py` 离线挖的；该脚本**不随包分发**，现在等价入口是 `bootstrap`。

## 各资产状态

| 资产 | 指纹 | golden 案例数 | 基线（当前模型） | 下一步 |
|---|---|---|---|---|
| github | `3fde719f3750f320` | 5（gh-01..05） | **3/3 已执行，全通过**（gh-01/02/03；gh-04/05 待补） | 补 gh-04/05；等模型升级重跑 |
| writing-skills | `470305487b384f99` | 5（ws-01..05） | **5/5 已执行，全通过**（ws-01/03 真实 Web 回放；其余当前模型真实作答判定） | 等模型升级重跑 |
| frontend-dev | `e5c463cb7583d791` | 5（fe-01..05） | **5/5 已执行，全通过**（fe-01..03 真实作答；fe-04/fe-05 为能力推理探针，项目产物不在本会话，待加载 dsh-whale-widget 后重标定） | 等模型升级重跑 |
| humanizer | `9b31c9d79f3b5ca6` | 5（hu-01..05，**合成**） | **5/5 已执行，全通过**（历史 0 条真实样本，按 SKILL.md 的 5 类 AI 写作特征构造；回放时坚持"不臆造事实"，缺数据只标注缺失） | 有真实用户文本后替换为真实样本 |
| dsh-whale-widget（**插件**） | `a190dff7f6907146` | 3（wh-01..03） | **3/3 已执行，全通过**（源码级对账回放，答案锚定到两个工程的文件行号；记录见 `../replay/dsh-whale-widget-replay.md`） | 等模型升级重跑；可按需扩到 5 条 |

> 合并基线现状：`baseline-results.json` 含 **21 条已执行 verdict（21/21 通过）**。
> 其中 18 条属于 WorkBuddy skill、3 条属于 DSH 插件；折旧报告当前显示 n/a（只有基线模型、无对比模型）。
> `golden/dsh-workbuddy-connect.json` 与 `golden/vectorize-io_hindsight-coding-agents.json` 仍是
> `draft: true` 的 bootstrap 草稿：**不计入基线**（`baseline.py` 默认跳过 draft，除非 `--allow-draft`）。

## 基线诚实性备注（影响跨模型可比性）

- **fe-04 / fe-05** 依赖 `dsh-whale-widget` 项目的具体产物（`widget.src.html` / `server.js`），
  本会话未加载该工程，故这两条是「能力推理探针」而非真实文件改动。判定为通过，但归因中明确标注；
  建议加载项目后重标定，使基线更硬。
- **ws-02** 判为通过，但归因指出：当前 authoring 实际倾向「先写文档」，与期望的 RED-GREEN-REFACTOR 不一致
  ——这是 writing-skills 自身待补的 TDD 闸门，后续可作为独立回归项跟进。
- **wh-01..03 是 K=1 自评**：回放虽锚定真实源码（`dsh-whale-widget` v0.4.2 与 `wb-points-plugin` v0.3.1
  都在本机），但判定由同一模型做出，未做 LLM-judge 交叉验证。要当硬基线用，需人工复核或提到 K≥3。

## 插件 golden set 与 skill 的区别（wh-* 的经验）

- skill 的 case 是「让 skill 干活」；插件的 case 是「让 agent 拿这个插件当参照物回答问题」。
- 因此插件的 `expected` 必须写成「答案是否锚定到真实资产（文件/行号/机制）」——否则会退化成文风评分，
  测不出插件价值。

## 判定规则（已固化在 regress.py）

- 通过率下降 ≥ 20pp **且没有任何用例变好** → DEGRADATION（带归因）
- 降幅跨过阈值但混有变好的用例 → volatility watch（churn，不算回归）
- 指纹在对比轮里没有基线行 → FINGERPRINT-DRIFT（资产被改过，别怪模型）
- 生产基线每案例 K≥3（评分类 K≥5）；本 MVP 已执行案例为 K=1，重跑时补足。

## 复现命令

```bash
# 1) 生成执行脚手架（待执行案例清单）；draft 用例默认跳过
python scripts/baseline.py --golden "golden/*.json" --model <当前模型>

# 2) 每个案例跑完后，把判定写进 recorded.json，再按资产合并进基线
python scripts/baseline.py --golden "golden/*.json" --from recorded.json --model <当前模型>
#    只补一个资产也可以（其余资产的基线会保留，并打印出来）
python scripts/baseline.py --golden golden/github.json --from recorded-github.json

# 3) 模型升级后，用新模型重跑同一 golden set，得到 new-results.json，再出报告并把结果落成基线
python scripts/console.py --host workbuddy report --results new-results.json --promote
python scripts/console.py --host workbuddy scan
```
