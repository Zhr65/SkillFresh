# MVP 基线现状（golden sets + baseline）

从 26 个本地历史会话 transcript（10/12–10/16）真实挖出的用户任务，已为 3 个 MVP skill 建好 golden set。
挖掘方法见 `../../../.workbuddy-ai/memory/mine_history.py`（剥离 `<system-reminder>` 等注入块后取真实 query，去重后 181 条用户消息，按 skill 关键词归桶）。

## 各 skill 状态

| skill | 指纹 | golden 案例数 | 基线（当前模型） | 下一步 |
|---|---|---|---|---|
| github | `3fde719f3750f320` | 5（gh-01..05） | **3/3 已执行，全通过**（gh-01/02/03；gh-04/05 待补） | 补 gh-04/05；等模型升级重跑 |
| writing-skills | `1edac5aa8fc04e37` | 5（ws-01..05） | **5/5 已执行，全通过**（ws-01/03 真实 Web 回放；其余当前模型真实作答判定） | 等模型升级重跑 |
| frontend-dev | `e5c463cb7583d791` | 5（fe-01..05） | **5/5 已执行，全通过**（fe-01..03 真实作答；fe-04/fe-05 为能力推理探针，项目产物不在本会话，待加载 dsh-whale-widget 后重标定） | 等模型升级重跑 |
| humanizer | `433e426e2c2615c5` | 5（hu-01..05，**合成**） | **5/5 已执行，全通过**（历史 0 条真实样本，按 SKILL.md 的 5 类 AI 写作特征构造；回放时坚持"不臆造事实"，缺数据只标注缺失） | 有真实用户文本后替换为真实样本 |
| dsh-whale-widget（**插件**） | `1666fc13dc65ccd5` | 3（wh-01..03） | **3/3 已执行，全通过**（源码级对账回放，答案锚定到两个工程的文件行号；记录见 `../replay/dsh-whale-widget-replay.md`） | 等模型升级重跑；可按需扩到 5 条 |

> 合并基线现状：`baseline-results.json` 含 **21 条已执行 verdict（21/21 通过，100% 基线）**，均由 `recorded-baseline.json` 经 baseline.py ingest 生成。折旧报告当前显示 n/a（仅基线模型、无对比模型）；模型升级后用同 golden set 重跑即出 drop 与降级判定。
> humanizer 在这 26 个 transcript 里没有真实任务样本，暂用合成样本；待真实历史出现后由 mine_history.py 补足。
> `dsh-whale-widget` 是第一个**插件** golden set，也是第一条插件真基线（`--host deepseek status` 已从 unverified 变 strong）。

## 基线诚实性备注（影响跨模型可比性）
- **fe-04 / fe-05** 依赖 `dsh-whale-widget` 项目的具体产物（`widget.src.html` / `server.js`），本会话未加载该工程，故这两条是「能力推理探针」而非真实文件改动。判定为通过，但归因中明确标注；建议加载项目后重标定，使基线更硬。
- **ws-02** 判为通过，但归因指出：当前 authoring 实际倾向「先写文档」，与期望的 RED-GREEN-REFACTOR 不一致——这是 writing-skills 自身待补的 TDD 闸门，后续可作为独立回归项跟进。
- **wh-01..03 是 K=1 自评**：回放虽锚定真实源码（`dsh-whale-widget` v0.4.2 与 `wb-points-plugin` v0.3.1 都在本机），但判定由同一模型做出，未做 LLM-judge 交叉验证。要当硬基线用，需人工复核或提到 K≥3。

## 插件 golden set 与 skill 的区别（wh-* 的经验）
- skill 的 case 是「让 skill 干活」；插件的 case 是「让 agent 拿这个插件当参照物回答问题」。
- 因此插件的 `expected` 必须写成「答案是否锚定到真实资产（文件/行号/机制）」——否则会退化成文风评分，测不出插件价值。

## 判定规则（已固化在 regress.py）
- 通过率下降 ≥ 20pp 且方向一致 → DEGRADATION（带归因）
- 低于阈值 → volatility watch
- 生产基线每案例 K≥3（评分类 K≥5）；本 MVP 已执行案例为 K=1，重跑时补足。

## 复现命令
```bash
# 1) 生成执行脚手架（待执行案例清单）
python scripts/baseline.py --golden golden/*.json --out . --model v-current-YYYY-MM-DD
# 2) 跑完每个案例后，把判定写进 recorded.json，再汇聚
python scripts/baseline.py --golden golden/github.json --from recorded-github.json --out . --model v-current-2026-10-17
# 3) 模型升级后，用新模型重跑同一 golden set，得到 new-results.json，再算折旧
python scripts/regress.py --results new-results.json --out . --threshold 20
```
