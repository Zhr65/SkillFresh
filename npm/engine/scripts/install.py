#!/usr/bin/env python3
"""
skill-auditor :: install.py
Install this skill into every agent host found on this machine.

One core, many hosts: the engine is plain Python (stdlib only), so each host only
needs the folder to appear in the directory it scans. This script finds those
directories and links (or copies) the skill into them.

Usage:
  python install.py                       # link into every host dir that already exists
  python install.py --targets workbuddy,dsh
  python install.py --copy                # copy instead of link (no live updates)
  python install.py --create              # create missing host dirs
  python install.py --dry-run
  python install.py --uninstall           # remove links that point at this folder

Link vs copy:
  link  -> edits here apply everywhere immediately (best while iterating)
  copy  -> each host gets an independent snapshot (best for a stable release)

Personal data (registry.*, baseline, model-version, hosts.json, filled golden sets,
replay logs) is NEVER copied: a copy is a code snapshot, not a data migration.
Old snapshots are reported as stale instead of being silently treated as "installed".

On Windows, directory symlinks need Developer Mode or admin; the script falls back
to a copy, telling you which it used.
"""
import os
import re
import sys
import shutil
import argparse

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../skill-auditor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from console import OK_MARK, FAIL_MARK  # noqa: E402


def read_field(key, path=HERE):
    """Read a frontmatter scalar (name/version) from SKILL.md."""
    try:
        with open(os.path.join(path, "SKILL.md"), encoding="utf-8-sig") as fh:
            m = re.search(r"^%s:\s*(\S+)" % re.escape(key), fh.read(), re.M)
        return m.group(1).strip('"\'') if m else ""
    except Exception:
        return ""


# 目录名不一定等于 skill 名：`git clone ... SkillFresh` 下来的目录叫 SkillFresh。
# 以 SKILL.md 的 name 为准，安装后的目录名才是各宿主能识别的 skill-auditor。
NAME = read_field("name") or os.path.basename(HERE)


def read_version(path=HERE):
    return read_field("version", path)

HOSTS = [
    ("workbuddy-ai", "~/.workbuddy-ai/skills", "WorkBuddy Agent（本会话实际加载的目录）"),
    ("workbuddy",    "~/.workbuddy/skills",    "WorkBuddy 桌面端技能市场目录"),
    ("claude",       "~/.claude/skills",       "Claude Code"),
    ("agents",       "~/.agents/skills",       "跨 agent 通用目录（skills CLI 也装这里）"),
    ("dsh",          "~/.dsh/skills",          "DeepSeek Harness"),
    ("codex",        "~/.codex/skills",        "OpenAI Codex CLI"),
]

# 复制时排除的东西：构建产物 + **个人数据**。golden/_template.json 会被单独补回。
IGNORE = shutil.ignore_patterns(
    "dist", "_archive", "__pycache__", ".git", "*.pyc",
    "registry*.json", "baseline-results.json", "baseline-scaffold.json",
    "model-version*.json", "recorded-*.json", "hosts.json",
    "depreciation-report.*", "fingerprint-report.*", "replay", "demo",
    "golden/*.json",
)


def expand(p):
    return os.path.abspath(os.path.expanduser(p))


def ensure_template(dst):
    src = os.path.join(HERE, "golden", "_template.json")
    target = os.path.join(dst, "golden", "_template.json")
    if os.path.isfile(src) and not os.path.exists(target):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(src, target)


def link_dir(src, dst, copy_only=False):
    """Install src at dst and VERIFY it. Returns 'symlink' or 'copy'.

    A failed link attempt must never leave an empty directory behind: that looks
    installed to every host but contains nothing. Verify by reading SKILL.md
    through the new path, and fall back to a real copy if linking didn't work.
    """
    if not copy_only:
        try:
            os.symlink(src, dst, target_is_directory=True)
            if os.path.isfile(os.path.join(dst, "SKILL.md")):
                return "symlink"
            # created, but unusable — undo before falling back
            if os.path.islink(dst):
                os.unlink(dst)
            else:
                shutil.rmtree(dst, ignore_errors=True)
        except (OSError, NotImplementedError, AttributeError):
            if os.path.isdir(dst) and not os.path.islink(dst) and not os.listdir(dst):
                os.rmdir(dst)          # leftover shell from a failed attempt

    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=IGNORE)
    ensure_template(dst)
    if not os.path.isfile(os.path.join(dst, "SKILL.md")):
        raise RuntimeError(f"installed to {dst} but SKILL.md is not readable there")
    return "copy"


def is_ours(dst):
    """True if dst already points at / contains this skill."""
    if os.path.islink(dst):
        return os.path.realpath(dst) == os.path.realpath(HERE)
    return os.path.isfile(os.path.join(dst, "SKILL.md"))


def install_one(name, path, desc, args):
    root = expand(path)
    dst = os.path.join(root, NAME)

    # 源头保护：`--force` 到源目录本身时，下面的 rmtree 会把源删掉（HERE 就在里面），
    # 安装器把自己删了。这个路径必须先拦。
    if os.path.realpath(dst) == os.path.realpath(HERE):
        return name, "ok", "这是源目录本身，无需安装"

    if not os.path.isdir(root):
        if not args.create:
            return name, "skip", f"目录不存在：{path}（加 --create 可创建）"
        if args.dry_run:
            return name, "dry", f"将创建 {path} 并安装"
        os.makedirs(root, exist_ok=True)

    if os.path.exists(dst) or os.path.islink(dst):
        # An empty dir here is a leftover from a failed install, not a real skill.
        if os.path.isdir(dst) and not os.path.islink(dst) and not os.listdir(dst):
            if not args.dry_run:
                os.rmdir(dst)
        elif is_ours(dst) and not args.force:
            src_v, dst_v = read_version(HERE), read_version(dst)
            if src_v and dst_v and src_v != dst_v:
                return name, "stale", f"已安装 {dst_v}，源是 {src_v}；加 --force 升级"
            return name, "ok", "已安装，跳过"
        elif not args.force:
            return name, "skip", f"目标已存在且不是本 skill：{dst}（加 --force 覆盖）"
        elif args.dry_run:
            return name, "dry", "将覆盖已有目标"
        else:
            if os.path.islink(dst):
                os.unlink(dst)
            else:
                shutil.rmtree(dst)

    if args.dry_run:
        return name, "dry", f"将安装到 {dst}"

    try:
        method = link_dir(HERE, dst, copy_only=args.copy)
    except Exception as e:                                    # noqa: BLE001
        return name, "err", f"安装失败：{e}"

    if method == "symlink":
        note = f"符号链接 → {dst}（改源目录即时生效）"
    else:
        note = f"复制 → {dst}（code snapshot；个人数据未复制，改动需重跑 install）"
    return name, "ok", note


def uninstall_one(name, path, args):
    root = expand(path)
    dst = os.path.join(root, NAME)
    if os.path.realpath(dst) == os.path.realpath(HERE):
        return name, "skip", "这是源目录本身，拒绝卸载"
    if not (os.path.exists(dst) or os.path.islink(dst)):
        return name, "skip", "未安装"
    if not is_ours(dst):
        return name, "skip", "不是本 skill 的安装，未删除"
    if args.dry_run:
        return name, "dry", f"将删除 {dst}"
    if os.path.islink(dst):
        os.unlink(dst)
    else:
        shutil.rmtree(dst)
    return name, "ok", f"已移除 {dst}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", default="all",
                    help="comma list of host names, or 'all' (default)")
    ap.add_argument("--copy", action="store_true", help="copy instead of link")
    ap.add_argument("--create", action="store_true", help="create missing host dirs")
    ap.add_argument("--force", action="store_true", help="overwrite an existing install")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()

    wanted = None if args.targets == "all" else {t.strip() for t in args.targets.split(",") if t.strip()}
    known = {h[0] for h in HOSTS}
    if wanted and not wanted <= known:
        print(f"[err] unknown target(s): {', '.join(sorted(wanted - known))}")
        print(f"      known: {', '.join(sorted(known))}")
        return 2

    print(f"skill      : {NAME} {read_version(HERE)}")
    print(f"source     : {HERE}")
    print(f"mode       : {'uninstall' if args.uninstall else ('copy' if args.copy else 'link')}"
          f"{' (dry-run)' if args.dry_run else ''}\n")

    rows = []
    for name, path, desc in HOSTS:
        if wanted and name not in wanted:
            continue
        if args.uninstall:
            rows.append(uninstall_one(name, path, args))
        else:
            rows.append(install_one(name, path, desc, args))

    w = max(len(r[0]) for r in rows) + 2
    for name, status, msg in rows:
        mark = {"ok": OK_MARK, "skip": "-", "dry": ".", "stale": "!", "err": FAIL_MARK}.get(status, "?")
        print(f"  {mark} {name:<{w}}{msg}")

    ok = sum(1 for r in rows if r[1] == "ok")
    stale = [r[0] for r in rows if r[1] == "stale"]
    print(f"\n{ok}/{len(rows)} 完成。验证：")
    print(f"  python {os.path.join(HERE, 'scripts', 'console.py')} hosts")
    if stale:
        print(f"  需要升级：{', '.join(stale)} —— 加 --force 重跑即可覆盖代码（个人数据不受影响）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
