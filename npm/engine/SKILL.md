---
name: skill-auditor
description: "Use when evaluating a skill OR plugin's lifecycle across agents: detecting capability degradation after a model update, judging whether a new skill/plugin is effective and fits the user, or auditing whether to keep/rewrite/retire one. Works in WorkBuddy (skills), DeepSeek harness (plugins), and other hosts via adapters. Triggers: '评测这个 skill', '评测这个插件', '模型升级后 skill 还灵不灵', '新 skill 好不好用', '这个 skill 适合我吗', 'deepseek 插件好不好用', '跨 agent 评估能力', '做个 skill 并按评测流程走'."
version: 0.6.0
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash
  - AskUserQuestion
display_name: "skill-auditor"
display_name_zh: "能力折旧与契合度评测"
visibility: "public"
---

# Skill Auditor — Agent 能力折旧与契合度评测

## Overview

**A skill is a depreciating asset.** Model versions ship every few months. A skill that passed last quarter can silently regress — wrong output shape, broken tool calls, missed triggers — and nobody tells the user their agent got dumber.

This skill is the **evaluation methodology**. It answers three questions:

1. **Is a skill degrading?** — capability regression across model versions.
2. **Is a new skill actually good?** — effectiveness vs cost.
3. **Does a skill fit THIS user?** — trigger accuracy + environment + style.

It ships five executable entry points:
- `scripts/console.py` — the **resident console** (data layer): maintains `registry.<host>.json` per host, detects model-version changes (`bump`/`check`), and emits the recommend/eliminate panel. See `USAGE.md` for the full command set.
- `scripts/regress.py` — given golden-set replay results, compute the pass-rate matrix, flag degradation (threshold + direction), and flag `FINGERPRINT-DRIFT` when an asset was edited instead.
- `scripts/baseline.py` — scaffold a replay checklist, then merge recorded verdicts into `baseline-results.json` (by asset; `--replace` for a full overwrite). Draft golden cases are skipped unless `--allow-draft`.
- `scripts/fingerprint.py` — standalone **report** CLI; a thin wrapper over the same hashing code `scan` uses. It writes `fingerprint-report.*` only; it does **not** write the registry.
- `scripts/install.py` — install this skill into every agent host on the machine (`~/.workbuddy-ai/skills`, `~/.claude/skills`, `~/.agents/skills`, `~/.dsh/skills`, `~/.codex/skills`). Verifies after installing; never leaves an empty directory behind; never copies personal data.

**Core principle: No measurement without a baseline. Degradation = significant pass-rate drop under an *unchanged* fingerprint — never "feels dumber".**

## Multi-Host Architecture (skills vs plugins)

One core engine, per-host adapters. The same fingerprint / baseline / regression / recommend / need-suggestion logic runs everywhere; an adapter only knows *where assets live* and *how to read their manifest*.

| host | kind | default root | manifest |
|---|---|---|---|
| workbuddy | skill | skills root that contains this skill | `SKILL.md` |
| claude-code | skill | `~/.claude/skills` | `SKILL.md` |
| deepseek | 插件 | `~/.dsh/profiles/desktop` (reads package.json deps) | `package.json` / `plugin.json` |
| generic | asset | pass `--root` | `plugin.json` / `manifest.json` / `SKILL.md` / `package.json` |

- **Each host keeps its own registry**: `registry.<host>.json` — no cross-host overwrite.
- **Baseline results + golden sets are shared**, keyed by asset name.
- Run with `--host <name> --root <path>`; add custom hosts in `hosts.json`.
- `console.py hosts` shows every profile, its resolved root, and detected asset count.

So in WorkBuddy you evaluate **skills**; in DeepSeek harness you evaluate **plugins** — same commands, different adapter. Adding a new host is a `hosts.json` entry (kind + root + manifest filenames), no code change.

## When to Use

- Before deploying a new skill or plugin ("做个 skill，按评测流程走").
- After a model-version bump, to check whether existing skills regressed.
- When a skill "feels off" — confirm with data, not vibes.
- When deciding whether to keep / rewrite / retire a skill.
- When recommending a skill or plugin to a user (fit + evidence).

**When NOT to use:** one-off tasks with no reusable skill; pure narrative docs.

## Core Concept: The Version Fingerprint

The fingerprint is the **row** of the depreciation matrix.

```
fingerprint = hash(能力面文件内容 + 相对路径)        # 口径 v2-ability-surface
```

**What counts as 能力面**（the files whose change actually changes behaviour）:

- included: `SKILL.md`, `scripts/*`, `lib/*`, `dist/*`, `package.json`, `plugin.json`, `cordis.patch.yml`, templates
- excluded: `README*`, `CHANGELOG*`, `LICENSE*`, images/audio/fonts, `*.map`, lockfiles, `node_modules/`, `test*/`, `docs/`

Why exclude: a README edit does not change capability, but hashing it makes the fingerprint move and produces a **fake** `FINGERPRINT-DRIFT` ("the asset was edited") when nothing functional changed. Plugins make this worse — one installed plugin can carry 16 MB of bundled output plus a 57 KB README.

Rules:
- **Fingerprint unchanged ⇒ the row persists; a pass-rate drop is model regression.**
- **Fingerprint changed ⇒ the change is the skill's own responsibility, not the model's.** Old and new rows coexist, so skill edits are never mis-attributed to model drift.

Run `python scripts/console.py scan` to compute fingerprints and store them in the registry. `fingerprint.py` is a standalone report wrapper; it does not touch the registry.

## Capability Surface (why plugins need a second axis)

The fingerprint says *"did the files change"*. It cannot say *"does it still offer the same thing"*.
`scan` therefore also records a **capability surface** per asset and diffs it against the previous scan:

| Asset kind | What the surface records |
|---|---|
| plugin | `patch` (dsh.bundle.patch), `main`/`module`, `bin`, `exports`, `deps`, `skills`, `mcp`, `hooks`, `insert` (cordis rows), `scripts` |
| skill | `tools` (allowed-tools count), `scripts` |

When it changes, `scan` prints it explicitly:

```
[caps] 1 个资产的能力面有变化（这是「它对外提供的东西变了」，与通过率无关）：
       plug-a                                   +bin
```

**Why this matters for plugins:** a plugin degrades by *not registering its tools / 404-ing its routes / not running its hooks* — not by "answering worse". A golden set of prompts measures the wrong thing for a plugin. The surface diff catches "v1.1.0 dropped an entry point" without needing to boot the host.

**Known boundary (do not paper over it):** the surface is *declared* capability, not *observed* capability. For a plugin whose manifest lies (declares a patch that fails to load), this cannot catch it.

## Runtime Evidence (the third axis, and what it cannot do)

There are two very different things called "a runtime probe". Do not conflate them:

| | 主动探针 (active probe) | 被动证据 (passive evidence) — **implemented** |
|---|---|---|
| what it does | boot the host, ask "which tools did you actually register" | read the log the host already writes |
| cost | DSH `desktop` profile is Electron-exclusive — `dsh --profile desktop --dump-config` is refused; no headless entry → reverse-engineer IPC or drive the UI | one file read, ~10 ms |
| catches | everything, including a plugin whose manifest lies | anything the plugin *logged* |
| status | **not built** | `console.py probe` / `skill_audit_probe` |

`probe` reads `~/.dsh/logs/*.log` (configurable per host via `"logs"` in `hosts.json`), parses
`<iso> [level] [source] message`, attributes lines whose `source` equals an installed asset name, and
flags lines matching failure vocabulary (`fail|error|ENOENT|unavailable|timeout|…`).

Real catch, on this machine:

```
@wxg-prc-cpg/browser-skill-dsh-plugin   2 条故障迹象
  bsk probe failed (spawn bsk ENOENT); browser tools will report install guidance…
```

Files present, manifest valid, `caps=4` — every static check passes. The plugin is loaded and its tools
are registered, but they cannot work: the `bsk` CLI it shells out to is not on PATH. **No amount of
file hashing finds this.**

**Blind spots (state them, never hide them):** a plugin that logs nothing is invisible; log rotation
erases old faults; a plugin nobody has used has no evidence; `info`-level faults are caught by keyword
matching, so both misses and false positives are possible. **Absence of evidence is not evidence of
health** — `probe` prints that sentence itself rather than a green check.

## Implemented vs Methodology-Only (do not blur this line)

The methodology below is broader than the code. Users should know which half they are holding:

| Area | Status |
|---|---|
| Fingerprint over the capability surface, `scan`/`status`, capability diff | **implemented** |
| Golden scaffold + baseline ingest (merge by asset, draft guard) | **implemented** (`baseline.py`) |
| Pass-rate matrix, ≥20pp threshold **+ direction**, `FINGERPRINT-DRIFT` | **implemented** (`regress.py`) |
| Model-change detection (`bump`/`check`), per-host stamps | **implemented — but the event source is manual**; a scheduled `check` stays green until someone bumps |
| Recommend/eliminate panel, need suggestion, history-mined drafts | **implemented** (heuristic; suggestions are not evidence) |
| Runtime evidence from host logs | **implemented** (passive; only what the plugin logged) |
| Ablation, shadow-mode adoption rate, net-value / token telemetry | **methodology only — no code** |
| Trigger precision/recall, environment reconciliation, style baseline | **methodology only — no code** |
| K≥3 replays, LLM-judge cross-validation | **not implemented**; current verdicts are K=1 self-assessment |
| `risk` column | maintenance-surface proxy — **not value** |

If a claim in this file cannot be traced to a row marked "implemented", treat it as advice for the human running the audit, not as something the tool will do for them.



## Judgment 1 — Capability Degradation

Four steps:

1. **Build the golden set from history, not by hand.** Mine past sessions: user adopted without pushback → positive replay; user pushed back / asked rewrite → negative. Replay IS regression. The user's own usage history is the corpus — no invented test set.
2. **Run both models on the same golden set, K≥3** (LLM-judge scoring cases: K≥5, because the judge is noisy). Record pass rate per (fingerprint, model version).
3. **Decision rule:** drop ≥20pp AND directionally consistent → report degradation; below threshold → "volatility watch". This turns "this output is weird" into a reproducible criterion.
4. **Attribute, don't just flag.** Inspect intermediate artifacts:
   - Tool call error / format changed → fix tool description.
   - Output structure right, content wrong → rewrite instructions.
   - Should-have-triggered but didn't → fix trigger description.
   - All intermediates right, only final wrong → model-internal regression; mark skill "single-model-dependent", wait for model fix.

The "7 degraded, 2 rewrite-recommended" line is the attributable subset of step 4's output — that is what makes the report *actionable*.

## Judgment 2 — Is a New Skill Good?

Split into two measurable axes: **effectiveness** (better results) and **economy** (lower cost).

1. **Pre-deploy ablation.** Same task batch, two arms: A without skill, B with skill. Record success rate, human-fix count, token cost.
   `net benefit = success_rate_lift × task_frequency − marginal_cost`
   A skill that lifts success 30% but triggers 3×/year at +50k tokens each is a liability, not an asset.
2. **Post-deploy shadow mode.** New skill runs silent: produces suggestions but doesn't auto-execute; log "what would have happened". Watch **adoption rate** (accepted vs overridden). Adoption rate is the ultimate judge of "good", more honest than any offline metric. Boundary: write-operation skills (delete file, send mail) can only simulate/annotate in shadow, never really run.
3. **Net-value card.** One dashboard per skill: call count, success rate, adoption rate, avg tokens, cumulative net benefit. 90 days net-negative → suggest rewrite or retire. This is the consumer side of the depreciation table.

## Judgment 3 — Does It Fit THIS User?

"Fits you" = triggers accurately + assumptions hold + style matches. All automatable:

1. **Trigger precision** (most affects feel, most skipped). Sample history positives (should trigger) and negatives (look-alike, shouldn't — "fix this bug" vs "explain what a bug is"). Replay → precision/recall. Low precision = annoying (over-triggers); low recall = wasted install. First manual label 10–20, then semi-auto, zero LLM cost.
2. **Environment reconciliation** (installed ≠ runs). Skill metadata declares deps: which tools / MCP / permissions / path habits / language, plus **implicit model-capability assumptions** (long context, code execution, multimodal). Reconcile against the user's real environment; missing → red flag. "Assumes macOS", "outputs Japanese", "needs 200k context" — invisible assumptions are the #1 reason a skill goes unused. This also feeds back into Judgment 1: a failed capability assumption = silent degradation.
3. **Style baseline** (lowest weight). Use the user's historical output + USER.md as reference; LLM-judge compares format, language, verbosity. Only used to break ties between equal-scoring skills.

## Embedded Workflow (for "make a new skill")

When the user asks to create a skill, run the protocol inline **before** delivering:

1. **Environment reconciliation** — does the user's environment satisfy the skill's declared deps + capability assumptions?
2. **Trigger replay** — replay 10–20 sampled cases; report precision/recall.
3. **Ablation** — on a small task batch, measure lift vs cost.
4. **Enter monitoring** — on pass, register fingerprint + golden set; the resident console re-runs on each model bump.

## Need-Based Suggestion (when no skill matches)

This is the "empty shelf" behavior: a user (especially one with zero skills/plugins) asks for something no installed skill covers. Don't just say "I can't" — surface the gap and offer to fill it.

**"Marginally adequate" is more common than "missing".** When the user complains (差点意思 / 不太行 / 凑合 / 改了几次), that is a dissatisfaction signal, not a request for a new asset. `suggest` detects it, maps the need to the *installed* asset via category aliases, and reports that asset's real state. A 100% baseline at K=1 means "it runs" — it does **not** mean the output is good enough. Say that out loud instead of recommending an install.

1. **Detect the need** from the conversation: task type + domain (e.g. "quarterly financial report" → finance/analysis; "remove the AI tone" → humanizer; "build a dashboard" → frontend/chart).
2. **Check the registry first** — `python scripts/console.py suggest --need "<need>"`. It returns either:
   - `✓ 你已有可用的 skill/插件：<name>` → use it, don't install anything; or
   - `💡 建议新增：[install:<slug>] <title>` → no cover, recommend the category.
3. **If uncovered, search the marketplace** for real candidates (skills/plugins/experts) and present a card with evidence: what it does, why it fits this need, source. **Never auto-install** — the user picks. Each recommendation carries **where to search** and **whether it is worth installing** (one-off task → built-in capability; repeated work → install). A request spanning several categories ("design an app UI and ship it as a web page") reports every matched gap, ranked by keyword hits. `suggest --search` / `scripts/market.py` queries the two reachable markets directly (DSH catalog + SkillHub) — **no GitHub proxy needed**.

   **Star numbers lie here.** In the DSH catalog, `stars` belongs to the *upstream repository*, not the plugin (measured: one plugin shows 101,755 stars while having 73 installs). Rank by installs + recent growth + last commit, never by stars, and label the star column so the user is not misled.
4. **On install, register it** (`console.py scan`) so it enters monitoring with a fingerprint + baseline slot.

Boundary: recommend at the moment of need, not as a standing排行榜. One suggestion tied to the current task beats a list of 50 skills. If the need is one-off, prefer built-in capability over installing.

## Artifacts (shared registry)

Three tables, one registry, shared by this skill (methodology) and the resident console (execution / data layer):

- **Depreciation table** — rows = fingerprints, cols = model versions, cells = pass rate; degradation cells flagged with attribution.
- **Net-value table** — per skill: calls, success, adoption, tokens, net benefit.
- **Fit reconciliation** — per skill: precision/recall, env gaps, style delta.

Registry stores: skill path, fingerprint, version, golden-set location, model-version events.

## Tools

- `python scripts/fingerprint.py <assets_dir> [--out <dir>]` → emits `fingerprint-report.json` + a ranked MVP-candidate list (risk heuristic, pending real usage telemetry). Report only; it does not write the registry. Output defaults to the skill root, not the scanned directory.
- `python scripts/regress.py --results <results.json> [--compare <model>] [--out <dir>]` → emits the pass-rate matrix, degradation flags (threshold + no case improved), and `FINGERPRINT-DRIFT` rows. `--compare` is required when the results file holds more than two models.
- `python scripts/baseline.py --golden <files...> --from recorded.json [--replace] [--allow-draft]` → scaffold a replay checklist, or merge recorded verdicts into the baseline **by asset** (other assets are kept and reported). `--from-seed` builds a baseline from the bundled community `seed/`, taking only assets actually installed on this machine. Draft/TODO cases are skipped unless `--allow-draft` or `"curated": true`.
- `python scripts/console.py --host <workbuddy|claude-code|deepseek|agents|generic> {hosts|discover|setup|scan|status|check|bump|stamp|seed|recommend|report|probe|suggest|bootstrap|onboard}` → the host-agnostic resident console. `report --promote` merges a fresh replay into the baseline so the panel sees it; `stamp --infer` tries to infer the model string and **refuses to guess** when it cannot; `seed audit` shows community-seed coverage (and flags index drift), `seed contribute` adds a scrubbed case; `probe` reads host logs; `discover` auto-finds host roots; `bootstrap` drafts golden sets from the user's own history; `onboard` chains discover+scan+bootstrap. `--out` relocates **all** data files. See `USAGE.md`.

Model-change stamps are per host (`model-version.<host>.json`); the legacy single `model-version.json` is still read as a fallback. `scan` only counts baseline rows whose model equals the active stamp — after a model switch, un-rerun assets go back to `unverified` instead of showing a false green.

## Common Mistakes

- Reporting "dumber" without an unchanged fingerprint (can't separate model drift from skill edits).
- Hand-writing the golden set instead of mining history (biased, stale, unscalable).
- Treating the community `seed/` as authority: its judge criteria are a first-pass starting point.
- Judging "good" by a single success-rate score, ignoring cost and fit.
- Auto-deleting or auto-installing — always require user confirmation; recommend disable/archive before delete.

## Next Step

**First run on a fresh machine**: `console.py seed audit` → `console.py baseline --from-seed` → run the scaffold → record verdicts → `scan` → `status`. That gets from "all unverified" to real judgements in minutes without writing a single golden case by hand.
Then for the assets you care about: mine 5+ positives each from your own history into `golden/`, re-baseline with `--k 3`, and re-run on every model bump.
