#!/usr/bin/env python3
"""
skill-auditor :: build_dist.py
Build a clean, shareable package that contains ONLY the skill code + docs,
never the user's personal data (registry, baselines, recorded verdicts, filled
golden sets, reports).

Usage:
  python scripts/build_dist.py
Output:
  dist/skill-auditor/          (folder to copy into a skills dir)
  dist/skill-auditor.zip       (same, zipped)
"""
import os
import shutil
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
AUDITOR = os.path.dirname(HERE)
DIST_ROOT = os.path.join(AUDITOR, "dist")
OUT = os.path.join(DIST_ROOT, "skill-auditor")

# demo.py 必须带上：README 的「30 秒试一下」和 SkillHub 描述都指向它，
# 从市场 zip 装的人要能跑。它只写自己的沙盒目录，不碰用户数据。
SHIP_FILES = ["SKILL.md", "USAGE.md", "DISTRIBUTION.md", "README.md", "LICENSE",
              ".gitignore", "hosts.example.json", "_meta.json", "demo.py"]
SHIP_SCRIPTS = ["console.py", "fingerprint.py", "regress.py", "baseline.py", "install.py", "build_dist.py"]
GOLDEN_SHIP = ["README.md", "_template.json"]
# seed/ 是社区公共 golden，陌生人装上就有判定可跑；contrib/ 是未评审的贡献片段，不入包
SEED_SHIP = ["README.md", "index.json"]
SEED_SKIP = {"contrib", "_sync_template.py"}


def main():
    if os.path.isdir(DIST_ROOT):
        shutil.rmtree(DIST_ROOT)
    os.makedirs(OUT)

    for f in SHIP_FILES:
        src = os.path.join(AUDITOR, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(OUT, f))

    sdir = os.path.join(OUT, "scripts")
    os.makedirs(sdir)
    for f in SHIP_SCRIPTS:
        src = os.path.join(AUDITOR, "scripts", f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(sdir, f))

    gdir = os.path.join(OUT, "golden")
    os.makedirs(gdir)
    for f in GOLDEN_SHIP:
        src = os.path.join(AUDITOR, "golden", f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(gdir, f))

    # seed/：只发 README + index + 各资产 JSON，不发 contrib/ 与同步脚本
    seeded = os.path.join(OUT, "seed")
    os.makedirs(seeded)
    for f in SEED_SHIP:
        src = os.path.join(AUDITOR, "seed", f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(seeded, f))
    src_seed = os.path.join(AUDITOR, "seed")
    if os.path.isdir(src_seed):
        for entry in sorted(os.listdir(src_seed)):
            if entry in SEED_SKIP:
                continue
            p = os.path.join(src_seed, entry)
            if os.path.isfile(p) and entry.endswith(".json"):
                shutil.copy2(p, os.path.join(seeded, entry))

    zpath = os.path.join(DIST_ROOT, "skill-auditor.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(OUT):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, DIST_ROOT))

    files = []
    for root, _, fs in os.walk(OUT):
        for f in fs:
            files.append(os.path.relpath(os.path.join(root, f), DIST_ROOT))
    print(f"[ok] dist folder -> {OUT} ({len(files)} files)")
    print(f"[ok] zip         -> {zpath}")
    for f in sorted(files):
        print("   ", f)


if __name__ == "__main__":
    main()
