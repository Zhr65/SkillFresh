#!/usr/bin/env python3
"""
skill-auditor :: baseline.py
Build a baseline pass-rate for a golden set on the CURRENT model.

Replaying a skill requires the agent/skill runtime, so this runner is the integration
point. Two modes:

  1) scaffold  (default): emit baseline-scaffold.json -- an execution checklist of every
     case with status="pending" + input + judge. The agent (or the resident console) runs
     each case, then records the verdict.

  2) ingest: given a recorded results file ({case_id: true/false, ...}), emit
     baseline-results.json in the exact schema regress.py consumes.

Usage:
  python baseline.py --golden golden/github.json --out results/ --model v-current-2026-10-17
  python baseline.py --golden golden/*.json --from recorded.json --out results/ --model v-current-2026-10-17

Note: production baseline uses K>=3 replays per case (K>=5 for LLM-judge scoring cases);
this tool records one verdict per case and trusts the caller to aggregate K runs.
"""
import os
import sys
import json
import glob
import argparse


def load_golden(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", required=True, nargs="+", help="golden set JSON file(s)")
    ap.add_argument("--from", dest="recorded", default=None,
                    help="recorded verdicts JSON: {case_id: true/false} (optional)")
    ap.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--model", default="v-current")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    cases = []
    for g in args.golden:
        for p in sorted(glob.glob(g)):
            data = load_golden(p)
            fp = data.get("fingerprint")
            for c in data.get("cases", []):
                cases.append({
                    "skill": data["skill"],
                    "fingerprint": fp,
                    "id": c["id"],
                    "input": c.get("input", ""),
                    "judge": c.get("judge", ""),
                    "provenance": c.get("provenance", ""),
                })

    recorded = {}
    if args.recorded:
        with open(args.recorded, encoding="utf-8") as f:
            recorded = json.load(f)

    if recorded:
        results = []
        for c in cases:
            if c["id"] in recorded:
                v = recorded[c["id"]]
                results.append({
                    "fingerprint": c["fingerprint"],
                    "skill": c["skill"],
                    "model": args.model,
                    "case_id": c["id"],
                    "passed": bool(v.get("passed") if isinstance(v, dict) else v),
                    "attribution": (v.get("attribution", "") if isinstance(v, dict) else ""),
                })
        out = {"baseline_model": args.model, "results": results}
        path = os.path.join(args.out, "baseline-results.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        passed = sum(1 for r in results if r["passed"])
        print(f"[ok] baseline-results.json -> {len(results)} executed, {passed} passed")
    else:
        scaffold = []
        for c in cases:
            scaffold.append({
                "skill": c["skill"], "case_id": c["id"], "status": "pending",
                "input": c["input"], "judge": c["judge"], "provenance": c["provenance"],
            })
        path = os.path.join(args.out, "baseline-scaffold.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"model": args.model, "cases": scaffold}, f, ensure_ascii=False, indent=2)
        print(f"[ok] baseline-scaffold.json -> {len(scaffold)} cases pending execution")
        print("Run each case through the skill on the current model, then re-run with --from <verdicts.json>")


if __name__ == "__main__":
    main()
