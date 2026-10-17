#!/usr/bin/env python3
"""
skill-auditor :: regress.py
Compute the pass-rate matrix (fingerprint x model version) from golden-set replay
results, then flag capability degradation with attribution.

Input results JSON schema:
{
  "baseline_model": "v2.4",
  "results": [
    {"fingerprint": "a1b2c3d4e5f6g7h8", "skill": "humanizer",
     "model": "v2.4", "case_id": "c1", "passed": true},
    {"fingerprint": "a1b2c3d4e5f6g7h8", "skill": "humanizer",
     "model": "v2.5", "case_id": "c1", "passed": false,
     "attribution": "output structure changed -> rewrite instructions"}
  ]
}

Decision rule: drop >= 20pp AND directionally consistent (no case improved — every
case that moved, moved down) -> "degradation". A drop that crosses the threshold while
some cases improved is "volatility watch" (churn, not a clean regression). A fingerprint
that has no baseline row in the compare run is reported as "FINGERPRINT-DRIFT" (the
asset was edited — not the model).

Usage:
  python regress.py --results <results.json> [--out <dir>] [--threshold 20] [--compare <model>]
"""
import os
import sys
import json
import argparse
from collections import defaultdict


def build_matrix(results):
    """pass[(fp, model)] = (passed, total, {attributions}); cases[(fp,model)][case_id]=bool"""
    agg = defaultdict(lambda: {"passed": 0, "total": 0, "attrs": defaultdict(int)})
    cases = defaultdict(dict)
    skills = {}
    for r in results:
        key = (r["fingerprint"], r["model"])
        agg[key]["total"] += 1
        if r.get("passed"):
            agg[key]["passed"] += 1
        else:
            attr = r.get("attribution", "unattributed")
            agg[key]["attrs"][attr] += 1
        if r.get("case_id"):
            cases[key][r["case_id"]] = bool(r.get("passed"))
        skills[r["fingerprint"]] = r.get("skill", r["fingerprint"])
    return agg, cases, skills


def rate(d):
    return (d["passed"] / d["total"] * 100) if d["total"] else None


def direction(base_cases, new_cases):
    """(worse, better) over case ids present in both runs."""
    worse = better = 0
    for cid, was in base_cases.items():
        if cid not in new_cases:
            continue
        now = new_cases[cid]
        if was and not now:
            worse += 1
        elif now and not was:
            better += 1
    return worse, better


def pick_compare_model(models, baseline, explicit):
    """Pick the model to compare against the baseline.

    Never guess by sort order: a third model, or version strings that don't sort
    chronologically ('v2.9' vs 'v2.10'), makes the lexicographically-last pick
    silently wrong — which inverts the verdict.
    """
    if explicit:
        if explicit not in models:
            print(f"[err] --compare {explicit} is not present in the results file. "
                  f"found: {', '.join(models) or '(none)'}")
            return "__ERR__"
        return explicit
    others = [m for m in models if m != baseline]
    if not others:
        return None
    if len(others) == 1:
        return others[0]
    print(f"[err] results hold {len(others)} models besides the baseline: {', '.join(others)}")
    print("      pass --compare <model> explicitly; picking one by sort order could invert the verdict.")
    return "__ERR__"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True, help="results JSON file")
    ap.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)),
                    help="output directory")
    ap.add_argument("--threshold", type=float, default=20.0,
                    help="degradation drop threshold in percentage points")
    ap.add_argument("--compare", default=None,
                    help="model id to compare against the baseline (required when the results "
                         "file holds more than two models — guessing would invert the verdict)")
    args = ap.parse_args()

    with open(args.results, encoding="utf-8-sig") as fh:
        data = json.load(fh)

    baseline = data.get("baseline_model")
    results = data["results"]
    agg, cases, skills = build_matrix(results)

    models = sorted({r["model"] for r in results})
    compare_model = pick_compare_model(models, baseline, args.compare)
    if compare_model == "__ERR__":
        sys.exit(1)

    fp_models = defaultdict(set)
    for r in results:
        fp_models[r["fingerprint"]].add(r["model"])

    rows = []
    for fp in sorted(skills):
        skill = skills[fp]
        seen = fp_models[fp]
        has_base = bool(baseline) and baseline in seen
        has_new = bool(compare_model) and compare_model in seen

        base_d = agg.get((fp, baseline)) if has_base else None
        base_rate = rate(base_d) if base_d else None
        new_d = agg.get((fp, compare_model)) if has_new else None
        new_rate = rate(new_d) if new_d else None

        drop = None
        worse = better = 0
        verdict = "n/a"
        note = ""
        if base_rate is not None and new_rate is not None:
            drop = base_rate - new_rate
            worse, better = direction(cases.get((fp, baseline), {}),
                                      cases.get((fp, compare_model), {}))
            if drop >= args.threshold:
                if worse > 0 and better == 0:
                    verdict = "DEGRADATION"
                else:
                    verdict = "volatility watch"
                    note = (f"drop {drop:.1f}pp ≥ threshold but direction is mixed "
                            f"(worse {worse} / better {better}) — some cases improved, so this is churn")
            elif drop <= -args.threshold:
                if better > 0 and worse == 0:
                    verdict = "IMPROVED"
                else:
                    verdict = "volatility watch"
                    note = (f"rise {abs(drop):.1f}pp but direction is mixed "
                            f"(worse {worse} / better {better})")
            else:
                verdict = "volatility watch"
        elif has_new and not has_base:
            # The asset was edited (or is new) since the baseline: this fingerprint has
            # no reference row. That is NOT model degradation — the skill itself changed.
            verdict = "FINGERPRINT-DRIFT"
            note = "new fingerprint has no baseline — re-baseline it; the change is attributable to the skill, not the model"
        elif has_base and compare_model and not has_new:
            verdict = "FINGERPRINT-DRIFT"
            note = "baseline fingerprint absent from the compare run — asset was edited or removed"
        elif has_base and not compare_model:
            verdict = "baseline-only"
            note = "no compare model yet"

        # pick dominant attribution if degraded
        attr = ""
        if new_d and new_d["attrs"]:
            attr = sorted(new_d["attrs"].items(), key=lambda x: -x[1])[0][0]

        rows.append({
            "skill": skill,
            "fingerprint": fp,
            "baseline_rate": round(base_rate, 1) if base_rate is not None else None,
            "new_rate": round(new_rate, 1) if new_rate is not None else None,
            "drop_pp": round(drop, 1) if drop is not None else None,
            "worse_cases": worse,
            "better_cases": better,
            "verdict": verdict,
            "attribution": attr,
            "note": note,
        })

    out = {
        "baseline_model": baseline,
        "compare_model": compare_model,
        "threshold_pp": args.threshold,
        "attribution_source": "caller-supplied free text (regress.py aggregates, it does not derive)",
        "rows": rows,
    }
    os.makedirs(args.out, exist_ok=True)
    json_path = os.path.join(args.out, "depreciation-report.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    md_path = os.path.join(args.out, "depreciation-report.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(f"# 能力折旧报告\n\n")
        cmp_txt = f"`{compare_model}`" if compare_model else "（尚无对比模型 —— 只有基线，等模型升级重跑后才会出现降级判定）"
        fh.write(f"- 基线模型: `{baseline}`  →  对比模型: {cmp_txt}\n")
        fh.write(f"- 降级阈值: 通过率下降 ≥ {args.threshold:g}pp，且**没有任何用例变好**\n\n")
        fh.write("| skill | fingerprint | 基线 | 新模型 | 降幅(pp) | 差/好 | 判定 | 归因 | 说明 |\n")
        fh.write("|---|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            def d(v, suffix=""):
                return f"{v}{suffix}" if v is not None else "—"
            fh.write(f"| {r['skill']} | `{r['fingerprint']}` | {d(r['baseline_rate'], '%')} | "
                      f"{d(r['new_rate'], '%')} | {d(r['drop_pp'])} | {r['worse_cases']}/{r['better_cases']} | "
                      f"**{r['verdict']}** | {r['attribution']} | {r['note']} |\n")
        fh.write("\n> 判定口径：降幅 ≥ 阈值**且方向一致**（所有移动的用例都变差、没有一个变好）才算降级；"
                 "跨过阈值但混有变好的用例，只算波动观察（churn 不等于回归）。`FINGERPRINT-DRIFT` "
                 "表示该指纹在对比轮里没有基线行（资产被改过），责任在 skill 自身，不是模型退化。\n")
        fh.write("> 归因是**调用方写入的自由文本**，本工具只做众数聚合，不负责推导归因。\n")
        fh.write("> 基线每案例 K=1 时为单次判定，K≥3 才有统计意义（评分类用例建议 K≥5）。\n")

    print(f"[ok] -> {json_path}")
    print(f"[ok] -> {md_path}")
    print("\nVerdicts:")
    for r in rows:
        extra = f"  ({r['note']})" if r["note"] else ""
        print(f"  {r['skill']:<26} {r['verdict']:<18} drop={r['drop_pp']} "
              f"worse/better={r['worse_cases']}/{r['better_cases']} attr={r['attribution']}{extra}")


if __name__ == "__main__":
    main()
