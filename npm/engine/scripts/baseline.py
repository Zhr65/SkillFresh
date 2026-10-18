#!/usr/bin/env python3
"""
skill-auditor :: baseline.py
Build (or extend) a baseline pass-rate for golden sets on the CURRENT model.

Replaying a skill requires the agent/skill runtime, so this runner is the integration
point. Two modes:

  1) scaffold  (default): emit baseline-scaffold.json -- an execution checklist of every
     case with status="pending" + input + judge. The agent (or the resident console) runs
     each case, then records the verdict.

  2) ingest: given a recorded results file ({case_id: true/false, ...}), merge the new
     verdicts into baseline-results.json in the exact schema regress.py consumes.

Merge semantics (changed in 0.5.0):
  Default is MERGE BY ASSET. Assets present in this run are replaced wholesale; assets
  that were not re-run keep their old rows (and are reported). Use --replace for a full
  overwrite. Before this, `--golden golden/github.json` silently deleted every other
  asset's baseline.

Draft guard:
  Cases marked `"draft": true`, or whose expected starts with TODO, are skipped unless
  --allow-draft. `console.py bootstrap` writes drafts with placeholder judging criteria;
  counting those as a real baseline is worse than having none.

Usage:
  python baseline.py --golden golden/github.json --model v-current-2026-10-17
  python baseline.py --golden golden/*.json --from recorded.json --model v-current-2026-10-17
  python baseline.py --golden golden/*.json --from recorded.json --replace
  python baseline.py --golden golden/github.json --from recorded-github.json --k 3   # K≥3
"""
import os
import sys
import json
import glob
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                 # .../skill-auditor (data dir by default)
sys.path.insert(0, HERE)

from console import (  # noqa: E402
    configure_data_dir, write_json, merge_baseline, seed_cases, load_registry, reg_assets,
)


def load_golden(path):
    # utf-8-sig: Windows 记事本 / PowerShell 存出来的 JSON 带 BOM。
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def is_draft(case, golden):
    if golden.get("draft") or case.get("draft"):
        return True
    expected = str(case.get("expected", "")).strip().upper()
    return expected.startswith("TODO") or expected.startswith("<")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", nargs="+", default=None,
                    help="golden set JSON file(s)；--from-seed 时可不填")
    ap.add_argument("--from-seed", action="store_true",
                    help="用随包分发的社区 seed/ 用例建基线（陌生人装上就有判定可跑）")
    ap.add_argument("--only-installed", action="store_true",
                    help="配合 --from-seed：只取本机 registry 里真实存在的资产（默认就是这个行为）")
    ap.add_argument("--all-seed", action="store_true",
                    help="配合 --from-seed：包含你没装的资产（大多数 case 会失败，仅用于探针）")
    ap.add_argument("--from", dest="recorded", default=None,
                    help="recorded verdicts JSON: {case_id: true/false} (optional)")
    ap.add_argument("--out", default=ROOT,
                    help="data directory (default: the skill root; console.py reads it from there)")
    ap.add_argument("--model", default="v-current")
    ap.add_argument("--replace", action="store_true",
                    help="replace the whole baseline instead of merging by asset")
    ap.add_argument("--allow-draft", action="store_true",
                    help="include draft cases (placeholder expected/judge); off by default")
    ap.add_argument("--k", type=int, default=0,
                    help="要求的每个用例判定次数；不足时告警（不阻断）。0 = 不要求")
    ap.add_argument("--host", default=None,
                    help="判断\"你装了哪些资产\"时用哪个宿主的注册表（--from-seed 时有效）")
    args = ap.parse_args()

    configure_data_dir(args.out)
    os.makedirs(args.out, exist_ok=True)

    curated_ids = set()
    if args.recorded:
        with open(args.recorded, encoding="utf-8-sig") as f:
            recorded = json.load(f)
        # recorded 文件里显式标注 curated: true 的 case，视为"已人工补完判定"，
        # 允许覆盖草稿标记。调用方负责这个判定是真的看过，不是占位符。
        for cid, v in recorded.items():
            if cid.startswith("_"):
                continue
            if isinstance(v, dict) and v.get("curated"):
                curated_ids.add(cid)
    else:
        recorded = {}

    if args.from_seed and args.golden:
        print("[err] --from-seed 和 --golden 二选一，别混用")
        return 2
    if not args.from_seed and not args.golden:
        print("[err] 要么给 --golden <文件>，要么给 --from-seed")
        return 2
    if args.only_installed and args.all_seed:
        print("[err] --only-installed 与 --all-seed 互斥")
        return 2

    cases = []
    drafts = 0
    if args.from_seed:
        seeds = seed_cases()
        if not seeds:
            print("[err] 这个包里没有 seed/ 或 seed 里没有可用 case")
            return 1
        installed = set()
        if not args.all_seed:
            reg = load_registry(args.host) if getattr(args, "host", None) else None
            if reg is None:
                # 没指定 host 时用默认宿主的注册表
                import console as _c
                cfg = _c.load_hosts_config()
                reg = load_registry(cfg.get("default_host", "workbuddy"))
            installed = set(reg_assets(reg).keys()) if reg else set()
            if not installed:
                print("[err] 本机还没有注册表（先 scan），无法判断你装了哪些资产；"
                      "确认要包含未安装资产请加 --all-seed")
                return 1
        usable = {s: c for s, c in seeds.items() if args.all_seed or s in installed}
        for skill in sorted(usable):
            for c in usable[skill]:
                cases.append({
                    "skill": skill, "fingerprint": None, "id": c["id"],
                    "input": c.get("input", ""), "judge": c.get("judge", ""),
                    "provenance": c.get("provenance", "seed"), "seed": True,
                })
        skipped = sorted(set(seeds) - set(usable))
        print(f"[seed] {len(cases)} case / {len(usable)} 资产（随包社区种子库）")
        if skipped and not args.all_seed:
            print(f"       跳过你没装的 {len(skipped)} 个资产：{', '.join(skipped)}")
    else:
        for g in args.golden:
            for p in sorted(glob.glob(g)):
                data = load_golden(p)
                fp = data.get("fingerprint")
                for c in data.get("cases", []):
                    if (is_draft(c, data) and not args.allow_draft
                            and c["id"] not in curated_ids):
                        drafts += 1
                        continue
                    cases.append({
                        "skill": data["skill"],
                        "fingerprint": fp,
                        "id": c["id"],
                        "input": c.get("input", ""),
                        "judge": c.get("judge", ""),
                        "provenance": c.get("provenance", ""),
                    })

    if drafts:
        print(f"[skip] {drafts} 个草稿用例（draft=true 或 expected 仍是 TODO）未计入基线；"
              "补完 expected/judge 再跑，或加 --allow-draft 强制包含。")
    if curated_ids:
        print(f"[curated] {len(curated_ids)} 个用例在 recorded 里标了 curated: true —— "
              "视为已人工补完判定，覆盖草稿标记")

    def verdicts_of(v):
        """recorded 里一个 case 的判定值 → run 布尔列表。

        兼容三种写法：
          true                         → [True]           (K=1，旧格式)
          [true, true, false]          → [True,True,False]  (显式 K)
          {"runs": true} / {"runs": [..]} → 同上            (带元信息)
        """
        if isinstance(v, bool):
            return [v]
        if isinstance(v, list) and all(isinstance(x, bool) for x in v):
            return v
        if isinstance(v, dict):
            r = v.get("runs", v.get("passed", True))
            if isinstance(r, bool):
                return [r]
            if isinstance(r, list) and all(isinstance(x, bool) for x in r):
                return r
        return []

    if recorded:
        results = []
        missing = 0
        short_k = 0
        for c in cases:
            if c["id"] not in recorded:
                missing += 1
                continue
            v = recorded[c["id"]]
            runs = verdicts_of(v)
            if not runs:
                print(f"[warn] {c['id']} 的判定值无法解析（要 true / [true,false,...] / {{'runs':..}}）：{v!r}")
                continue
            if args.k and len(runs) < args.k:
                short_k += 1
            attribution = (v.get("attribution", "") if isinstance(v, dict) else "")
            # passed = K 次多数（单次时就是它自己）。K 次平票按失败算 —— 保守口径：
            # 不能让"说不准"变成绿灯。
            passed = (sum(1 for r in runs if r) * 2 > len(runs))
            results.append({
                "fingerprint": c["fingerprint"],
                "skill": c["skill"],
                "model": args.model,
                "case_id": c["id"],
                "passed": bool(passed),
                "k": len(runs),
                "k_passed": sum(1 for r in runs if r),
                "runs": runs,
                "attribution": attribution,
            })
        if missing:
            # 按资产合并时，本资产下没跑的用例会随旧行一起被换掉 —— 必须说清楚，
            # 否则用户以为"只追加"，实际"覆盖面变小了"。
            prev = load_json(BASELINE, {"results": []}) or {"results": []}
            prev_ids = {r.get("case_id") for r in prev.get("results", [])
                        if r.get("skill") in {c["skill"] for c in cases}}
            have = {c["id"] for c in cases}
            dropped = sorted(prev_ids - have)
            if dropped and not args.replace:
                print(f"[warn] {len(dropped)} 个本资产旧用例本次未跑，已随旧行移除：{', '.join(dropped)}"
                      "（只跑部分用例 = 覆盖率下降；要保留请把它们也判定）")
            print(f"[warn] {missing} 个用例没有判定（recorded 文件里缺 id），未写入")
        if short_k and args.k:
            print(f"[warn] {short_k} 个用例的 K < {args.k}（判定次数不足，结论置信度低）")
        if not results:
            print("[err] 没有任何判定可写入；检查 recorded 文件的 case_id")
            return 1
        st = merge_baseline({"baseline_model": args.model, "results": results},
                            replace=args.replace)
        passed = sum(1 for r in results if r["passed"])
        total_runs = sum(r["k"] for r in results)
        print(f"[ok] baseline-results.json -> 本次 {len(results)} 条（{passed} 通过，"
              f"共 {total_runs} 次判定）；库内共 {st['total']} 条")
        print(f"     替换资产：{', '.join(st['replaced'])}")
        if st["kept"] and not args.replace:
            print(f"     保留资产：{', '.join(st['kept'])}（本轮没跑，旧模型判定）")
        print(f"     模型：{', '.join(st['models'])}")
        return 0

    scaffold = []
    for c in cases:
        scaffold.append({
            "skill": c["skill"], "case_id": c["id"], "status": "pending",
            "input": c["input"], "judge": c["judge"], "provenance": c["provenance"],
        })
    path = os.path.join(args.out, "baseline-scaffold.json")
    write_json(path, {"model": args.model, "cases": scaffold})
    print(f"[ok] baseline-scaffold.json -> {len(scaffold)} cases pending execution")
    print("Run each case through the skill on the current model, then re-run with --from <verdicts.json>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
