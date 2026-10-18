#!/usr/bin/env python3
"""把 golden/ 里可公开的 case 迁移到 seed/，顺手脱敏 provenance。

规则（保守）：
  · 只迁非草稿、非本机独有的资产；
  · provenance 里的用户名/会话目录名 → 中性标签（如 "session:2026-10-12"）；
  · input 里出现本机绝对路径/xwechat 的 case → 不迁（宁可少，不可泄）。

用法：python tools/seed_sync.py [--dry-run]   （在 skill-auditor 根目录执行）
"""
import glob
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_ONLY = {
    "dsh-whale-widget", "skill-auditor", "dsh-workbuddy-connect", "your-skill-name",
}


def scrub(text):
    if not isinstance(text, str):
        return text, False
    out = text
    # 会话目录名 c-Users-zhr15-WorkBuddy AI-2026-10-12-11-25-38 → session-2026-10-12
    out = re.sub(r"c-Users-[A-Za-z0-9]+-.*?(\d{4}-\d{2}-\d{2})", r"session:\1", out)
    out = re.sub(r"[A-Za-z]:\\\\?[Uu]sers\\\\?[^\\\\/\\s\"']+", "<home>", out)
    out = re.sub(r"[A-Za-z]:/[Uu]sers/[^/\\s\"']+", "<home>", out)
    out = out.replace("zhr15", "<user>")
    out = re.sub(r"xwechat_files/[^/\\s\"']+", "xwechat_files/<account>", out)
    out = re.sub(r"WorkBuddy AI/\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}", "<workspace>", out)
    return out, out != text


def public_ok(case):
    txt = json.dumps(case, ensure_ascii=False)
    if "xwechat" in txt:
        return False
    if re.search(r"[A-Za-z]:\\\\?[Uu]sers\\\\", txt):
        return False
    return True


def main():
    dry = "--dry-run" in sys.argv
    os.chdir(ROOT)
    os.makedirs("seed", exist_ok=True)
    index_path = os.path.join("seed", "index.json")
    index = {"schema_version": 1, "cases": 0, "assets": {}, "note": "community seed; see seed/README.md"}
    index = json.load(io.open(index_path, encoding="utf-8")) if os.path.exists(index_path) else index

    migrated, skipped = 0, 0
    for f in sorted(glob.glob("golden/*.json")):
        d = json.load(io.open(f, encoding="utf-8-sig"))
        skill = d.get("skill")
        if d.get("draft") or skill in LOCAL_ONLY or skill == "your-skill-name":
            continue
        keep = []
        for c in d.get("cases", []):
            if not public_ok(c):
                skipped += 1
                continue
            c2 = dict(c)
            changed = False
            for key in ("input", "expected", "judge", "provenance"):
                v, ch = scrub(c2.get(key, ""))
                c2[key] = v
                changed = changed or ch
            if changed:
                c2["scrubbed"] = True
            keep.append(c2)
            migrated += 1
        if not keep:
            continue
        out = {
            "skill": skill,
            "seed": True,
            "note": "community seed — judge criteria are a starting point, not authority",
            "cases": keep,
        }
        target = os.path.join("seed", os.path.basename(f))
        if not dry:
            with io.open(target, "w", encoding="utf-8") as fh:
                json.dump(out, fh, ensure_ascii=False, indent=2)
        index["assets"][skill] = {"cases": len(keep), "file": os.path.basename(f)}
        print("%-22s -> seed/%-22s %d cases" % (skill, os.path.basename(f), len(keep)))

    index["cases"] = sum(a["cases"] for a in index["assets"].values())
    index["asset_count"] = len(index["assets"])
    if not dry:
        with io.open(index_path, "w", encoding="utf-8") as fh:
            json.dump(index, fh, ensure_ascii=False, indent=2)
    print()
    print("migrated=%d skipped(private)=%d assets=%d%s"
          % (migrated, skipped, len(index["assets"]), " (dry-run)" if dry else ""))


if __name__ == "__main__":
    main()
