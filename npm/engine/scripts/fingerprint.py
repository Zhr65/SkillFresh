#!/usr/bin/env python3
"""
skill-auditor :: fingerprint.py
Standalone CLI that emits fingerprint-report.json / .md for a skills directory.

This is a THIN WRAPPER on purpose. Hashing and manifest parsing live in
console.py (`fingerprint_asset` / `parse_manifest`); this file only formats a
report. Keeping one implementation matters: two copies of the hashing logic
would drift, and a drifting fingerprint silently breaks attribution
(a changed hash looks like "the asset was edited" when it wasn't).

Fingerprint = hash of every indexed file's content + its relative path.
Per the methodology: an UNCHANGED fingerprint is what lets us attribute a
pass-rate drop to the model instead of to a skill edit.

Usage:
  python fingerprint.py <skills_dir> [--out <dir>]
"""
import os
import sys
import json
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from console import fingerprint_asset, parse_manifest, SCRIPT_EXT  # noqa: E402


def has_scripts(path):
    for _, _, fs in os.walk(path):
        for f in fs:
            if f.endswith(SCRIPT_EXT):
                return True
    return False


def main():
    if len(sys.argv) < 2:
        print("usage: fingerprint.py <skills_dir> [--out <dir>]")
        sys.exit(1)

    skills_dir = sys.argv[1]
    # 默认写到本 skill 的根目录，而不是被扫描的 skills 目录：
    # 以前默认把 fingerprint-report.* 丢进用户的宿主目录，等于污染它。
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else root

    if not os.path.isdir(skills_dir):
        print(f"skills_dir not found: {skills_dir}")
        sys.exit(1)

    rows = []
    for name in sorted(os.listdir(skills_dir)):
        p = os.path.join(skills_dir, name)
        manifest = os.path.join(p, "SKILL.md")
        if not os.path.isfile(manifest):
            continue
        meta = parse_manifest(manifest)
        fp, nfiles, unreadable = fingerprint_asset(p)
        with open(manifest, encoding="utf-8") as fh:
            lines = fh.read().count("\n") + 1
        scripts = has_scripts(p)
        rows.append({
            "skill": name,
            "fingerprint": fp,
            "version": meta.get("version", "-"),
            "files": nfiles, "unreadable": unreadable,
            "lines": lines,
            "tool_deps": meta.get("tool_deps", 0),
            "has_scripts": scripts,
            "description": (meta.get("description", "") or "")[:90],
            # maintenance-surface proxy, NOT value; replace once usage telemetry exists
            "risk": lines + meta.get("tool_deps", 0) * 20 + (60 if scripts else 0),
        })

    ranked = sorted(rows, key=lambda x: -x["risk"])
    report = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "skills_dir": os.path.abspath(skills_dir),
        "count": len(rows),
        "note": "risk is a heuristic (complexity + dependency breadth + bundled code). "
                "Replace with real usage frequency once telemetry exists.",
        "skills": rows,
    }

    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "fingerprint-report.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    md_path = os.path.join(out_dir, "fingerprint-report.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("# 能力折旧表骨架 (fingerprint draft)\n\n")
        fh.write(f"- 生成时间: {report['generated']}\n")
        fh.write(f"- 扫描目录: `{report['skills_dir']}`\n")
        fh.write(f"- skill 数: {report['count']}\n\n")
        fh.write("> 指纹 = hash(文件内容 + 相对路径)，与 `console.py scan` 同一实现。\n")
        fh.write("> 指纹不变 ⇒ 通过率下降归因为模型退化；指纹变了 ⇒ 归因为 skill 自身改动。\n")
        fh.write("> `risk` 是维护面代理（复杂度+依赖面+捆绑代码），**不代表价值**，待用量遥测接入后替换。\n\n")
        fh.write("## 折旧表骨架（行 = 指纹，列 = 模型版本，待填通过率）\n\n")
        fh.write("| skill | fingerprint | version | 基线通过率 | 新模型通过率 | 判定 |\n")
        fh.write("|---|---|---|---|---|---|\n")
        for r in rows:
            fh.write(f"| {r['skill']} | `{r['fingerprint']}` | {r['version']} | — | — | 待回归 |\n")
        fh.write("\n## MVP 候选（按风险分降序，挑 3–5 个挖 golden set）\n\n")
        fh.write("| # | skill | risk | lines | tool_deps | scripts |\n")
        fh.write("|---|---|---|---|---|---|\n")
        for i, r in enumerate(ranked[:10], 1):
            fh.write(f"| {i} | {r['skill']} | {r['risk']} | {r['lines']} | {r['tool_deps']} | "
                     f"{'yes' if r['has_scripts'] else 'no'} |\n")

    print(f"[ok] scanned {len(rows)} skills -> {json_path}")
    print(f"[ok] markdown      -> {md_path}")
    print("\nTop MVP candidates by risk:")
    for i, r in enumerate(ranked[:10], 1):
        print(f"  {i:2d}. {r['skill']:<28} risk={r['risk']:<5} lines={r['lines']:<5} "
              f"tools={r['tool_deps']} scripts={'Y' if r['has_scripts'] else 'N'}")


if __name__ == "__main__":
    main()
