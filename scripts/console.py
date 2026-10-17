#!/usr/bin/env python3
"""
skill-auditor :: console.py — host-agnostic capability auditor

Core engine (fingerprint / baseline / regression / recommend / need-suggestion)
is shared. A per-host ADAPTER knows where assets live and how to read them:

  workbuddy   -> skills  (SKILL.md)
  claude-code -> skills  (SKILL.md)
  deepseek    -> plugins (plugin.json / manifest.json / SKILL.md)
  generic     -> any dir with a manifest

Each host keeps its own registry: registry.<host>.json. Baseline results and
golden sets are shared (keyed by asset name).

Commands
  hosts      list host profiles + resolved roots + detected asset counts
  setup      init local data for a host (model stamp + golden template)
  scan       discover assets for the host -> registry.<host>.json
  status     depreciation table for the host
  check      detect model-version change vs last regression
  bump       record/simulate a model switch
  recommend  strong / watch / unverified / degrade / prune / suggest-new panel
  report     regenerate the depreciation report (via regress.py)
  suggest    need-based recommendation (check registry first, then taxonomy)
"""
import os
import sys
import re
import io
import json
import shutil
import hashlib
import subprocess
import datetime
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
AUDITOR = os.path.dirname(HERE)                       # .../skill-auditor
DEFAULT_OUT = AUDITOR
REGRESS_PY = os.path.join(HERE, "regress.py")
BASELINE = os.path.join(DEFAULT_OUT, "baseline-results.json")
MODEL_STAMP = os.path.join(DEFAULT_OUT, "model-version.json")
GOLDEN_DIR = os.path.join(DEFAULT_OUT, "golden")
HOSTS_CFG = os.path.join(AUDITOR, "hosts.json")

INDEXED_EXT = (".md", ".py", ".js", ".ts", ".json", ".sh", ".dot", ".yaml", ".yml")
SCRIPT_EXT = (".py", ".js", ".ts", ".sh")

RISK_LOW = 100
RISK_HIGH = 120
PRUNE_RISK = 400

# Built-in host profiles (hosts.json overrides / extends these)
HOST_PROFILES = {
    "workbuddy": {"kind": "skill", "label": "skill", "root": None, "manifests": ["SKILL.md"]},
    "claude-code": {"kind": "skill", "label": "skill", "root": "~/.claude/skills", "manifests": ["SKILL.md"]},
    "deepseek": {"kind": "plugin", "label": "插件", "root": "~/.dsh/profiles/desktop",
                 "discover": "deps", "manifests": ["package.json", "plugin.json"]},
    "generic": {"kind": "plugin", "label": "asset", "root": None,
                "manifests": ["plugin.json", "manifest.json", "SKILL.md", "package.json"]},
}

TAXONOMY = [
    {"slug": "humanizer", "title": "humanizer（去 AI 味 / 润色改写）",
     "kw": ["去ai", "ai味", "润色", "改写", "口语化", "人味", "natural", "humanize", "文风", "语气"],
     "query": "humanizer 去AI味 写作"},
    {"slug": "finance", "title": "金融 / 财报 / 估值分析",
     "kw": ["财务", "财报", "股票", "基金", "估值", "投资", "金融", "行情", "板块", "持仓"],
     "query": "finance 财报 估值 分析"},
    {"slug": "docx", "title": "Word 文档创作 / 排版",
     "kw": ["word", "docx", "文档", "报告", "论文", "公文", "合同"],
     "query": "docx 文档 写作 排版"},
    {"slug": "pptx", "title": "PPT 演示文稿",
     "kw": ["ppt", "pptx", "幻灯片", "演示", "汇报"],
     "query": "pptx 演示文稿"},
    {"slug": "sheet", "title": "Excel / 表格处理",
     "kw": ["excel", "表格", "xlsx", "数据表", "透视", "csv"],
     "query": "excel 表格 数据"},
    {"slug": "frontend", "title": "前端 / 网页 / UI",
     "kw": ["前端", "网页", "ui", "react", "html", "css", "组件", "页面"],
     "query": "frontend 网页 UI"},
    {"slug": "browser", "title": "浏览器自动化 / 抓取",
     "kw": ["爬虫", "抓取", "自动化", "浏览器", "网页操作", "截图"],
     "query": "browser 自动化 抓取"},
    {"slug": "debug", "title": "调试 / 排错",
     "kw": ["调试", "报错", "bug", "排查", "崩溃", "异常"],
     "query": "debugging 调试 排错"},
    {"slug": "evals", "title": "评测 / 回归测试",
     "kw": ["评测", "回归", "测试", "eval", "基线", "通过率"],
     "query": "evals 评测 回归"},
    {"slug": "chart", "title": "数据可视化 / 图表",
     "kw": ["图表", "可视化", "chart", "看板", "dashboard", "仪表盘"],
     "query": "chart 可视化 图表"},
]


# ---------------------------------------------------------------- helpers
def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def parse_frontmatter(text):
    """Minimal YAML frontmatter reader: scalars plus '-' list items.

    List items are stored under `<key>__list`. This matters: `allowed-tools` is a
    YAML list in most skills, and dropping those lines silently zeroed the
    tool-dependency term of the risk score for every asset.
    """
    fm = {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return fm
    key = None
    for line in m.group(1).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            if key:
                fm.setdefault(key + "__list", []).append(
                    stripped[2:].strip().strip('"').strip("'"))
            continue
        if ":" in stripped:
            k, v = stripped.split(":", 1)
            key = k.strip()
            fm[key] = v.strip().strip('"').strip("'")
    return fm


def split_tools(s):
    return [t for t in re.split(r"[\s,\-]+", s or "") if t]


def fingerprint_asset(path):
    files = []
    for root, _, fs in os.walk(path):
        for f in fs:
            if f.endswith(INDEXED_EXT):
                files.append(os.path.join(root, f))
    files.sort()
    h = hashlib.sha256()
    for f in files:
        with open(f, "rb") as fh:
            h.update(fh.read())
    for f in files:
        h.update(os.path.relpath(f, path).encode("utf-8"))
    return h.hexdigest()[:16], len(files)


def parse_manifest(manifest_path):
    name = os.path.basename(os.path.dirname(manifest_path))
    meta = {"name": name, "version": "-", "description": "", "tool_deps": 0}
    fn = os.path.basename(manifest_path).lower()
    if fn.endswith(".json"):
        j = load_json(manifest_path, {}) or {}
        meta["name"] = str(j.get("name") or name)
        meta["version"] = str(j.get("version", "-"))
        meta["description"] = str(j.get("description", "") or "")[:120]
        deps = j.get("dependencies") or j.get("tools") or j.get("allowed-tools") or []
        meta["tool_deps"] = len(deps) if isinstance(deps, (list, dict)) else 0
    elif fn.endswith(".md"):
        try:
            txt = open(manifest_path, encoding="utf-8").read()
        except Exception:
            txt = ""
        fm = parse_frontmatter(txt)
        meta["name"] = fm.get("name", name)
        meta["version"] = fm.get("version", "-")
        meta["description"] = (fm.get("description", "") or "")[:120]
        tools = fm.get("allowed-tools__list") or split_tools(fm.get("allowed-tools", ""))
        meta["tool_deps"] = len(tools)
    return meta


def discover_assets(root, manifests, mode="dirs"):
    """Find assets under `root`.

    mode="dirs": each subdir with a manifest (scoped @scope/name dirs descended one level).
    mode="deps": read <root>/package.json dependencies and resolve each to
                 <root>/node_modules/<name> (npm/pnpm layout).
    """
    if not os.path.isdir(root):
        return []
    candidates = []
    if mode == "deps":
        pj = load_json(os.path.join(root, "package.json"), {}) or {}
        for dep in (pj.get("dependencies") or {}).keys():
            p = os.path.join(root, "node_modules", dep)
            if os.path.isdir(p):
                candidates.append((dep, p))
    else:
        for name in sorted(os.listdir(root)):
            p = os.path.join(root, name)
            if not os.path.isdir(p):
                continue
            # Hidden/transient dirs are not assets. The host renames a skill to
            # `_compat-test-<name>` while running compatibility checks; recording that
            # as a separate asset inflates the table and creates a phantom row with
            # no baseline. Skip them, but say so rather than dropping them silently.
            if name.startswith((".", "_")):
                if os.path.isfile(os.path.join(p, "SKILL.md")) or any(
                        os.path.isfile(os.path.join(p, m)) for m in manifests):
                    print(f"[skip] transient-looking dir (not counted as an asset): {name}")
                continue
            if name.startswith("@"):
                for sub in sorted(os.listdir(p)):
                    sp = os.path.join(p, sub)
                    if os.path.isdir(sp):
                        candidates.append((name + "/" + sub, sp))
            else:
                candidates.append((name, p))

    out = []
    for name, p in candidates:
        manifest = None
        for m in manifests:
            mp = os.path.join(p, m)
            if os.path.isfile(mp):
                manifest = mp
                break
        if not manifest:
            continue
        fp, nfiles = fingerprint_asset(p)
        meta = parse_manifest(manifest)
        try:
            lines = open(manifest, encoding="utf-8").read().count("\n") + 1
        except Exception:
            lines = 0
        has_scripts = False
        for _, _, fs in os.walk(p):
            if any(f.endswith(SCRIPT_EXT) for f in fs):
                has_scripts = True
                break
        risk = lines + meta["tool_deps"] * 20 + (60 if has_scripts else 0)
        out.append({
            "id": name,
            "name": meta["name"],
            "kind": "asset",
            "manifest": os.path.basename(manifest),
            "fingerprint": fp,
            "version": meta["version"],
            "description": meta["description"],
            "files": nfiles,
            "lines": lines,
            "tool_deps": meta["tool_deps"],
            "has_scripts": has_scripts,
            "risk": risk,
            "path": os.path.abspath(p),
        })
    return out


# ---------------------------------------------------------------- host config
def load_hosts_config():
    cfg = load_json(HOSTS_CFG, {}) or {}
    merged = {"default_host": cfg.get("default_host", "workbuddy"),
              "hosts": dict(HOST_PROFILES)}
    merged["hosts"].update(cfg.get("hosts", {}))
    return merged


def resolve_host(args):
    cfg = load_hosts_config()
    host = args.host or cfg["default_host"]
    prof = dict(cfg["hosts"].get(host, HOST_PROFILES["generic"]))
    prof.setdefault("kind", "plugin")
    prof.setdefault("label", "asset")
    prof.setdefault("manifests", ["SKILL.md"])
    prof["host"] = host
    return host, prof


def resolve_root(args, prof):
    if args.root:
        return os.path.abspath(os.path.expanduser(args.root))
    if getattr(args, "skills_dir", None):
        return os.path.abspath(os.path.expanduser(args.skills_dir))
    if prof.get("root"):
        return os.path.abspath(os.path.expanduser(prof["root"]))
    # No root configured. Only the host this tool lives in may fall back to its own
    # skills dir; "generic" must be told where to look, otherwise it would silently
    # report this host's assets as if they were the user's.
    if prof.get("host") == "generic":
        return None
    return os.path.dirname(AUDITOR)


def registry_path(host):
    return os.path.join(DEFAULT_OUT, "registry.%s.json" % host)


def load_registry(host):
    p = registry_path(host)
    return load_json(p) if os.path.exists(p) else None


def reg_assets(reg):
    if not reg:
        return {}
    return reg.get("assets") or reg.get("skills") or {}


def get_active_model():
    s = load_json(MODEL_STAMP, None)
    return (s or {}).get("version") or "v-unknown"


# ---------------------------------------------------------------- commands
def cmd_hosts(args):
    cfg = load_hosts_config()
    print("\n宿主适配器（host -> kind -> root）")
    print("-" * 82)
    for host, prof in cfg["hosts"].items():
        ns = argparse.Namespace(root=None, skills_dir=None, host=host)
        r = resolve_root(ns, dict(prof, host=host))
        if r is None:
            print(f"  ? {host:<14} kind={prof.get('kind','?'):<7} discover={prof.get('discover','dirs'):<5} "
                  f"assets=—   root=(not set — pass --root <path>)")
            continue
        exists = os.path.isdir(r)
        n = len(discover_assets(r, prof.get("manifests", ["SKILL.md"]), prof.get("discover", "dirs"))) if exists else 0
        mark = "✓" if exists else "✗"
        print(f"  {mark} {host:<14} kind={prof.get('kind','?'):<7} discover={prof.get('discover','dirs'):<5} assets={n:<3} root={r}")
    print("-" * 82)
    print("  用 --host <name> --root <path> 指定；改 hosts.json 可加自定义宿主。")
    return 0


def cmd_setup(args):
    host, prof = resolve_host(args)
    out = args.out
    os.makedirs(out, exist_ok=True)
    gdir = os.path.join(out, "golden")
    os.makedirs(gdir, exist_ok=True)

    if not os.path.exists(MODEL_STAMP):
        write_json(MODEL_STAMP, {"version": args.model, "bumped_at": now_iso(), "note": "set by setup"})
        print(f"[ok] model-version.json -> {args.model}")
    else:
        print(f"[skip] model-version.json exists ({get_active_model()})")

    if not any(f.endswith(".json") for f in os.listdir(gdir)):
        write_json(os.path.join(gdir, "_template.json"), {
            "skill": "your-asset-name",
            "fingerprint": "0000000000000000",
            "note": "TEMPLATE — copy to <asset>.json and fill with real cases",
            "cases": [{
                "id": "case-01", "polarity": "positive", "type": "task",
                "input": "<real user prompt>",
                "expected": "<what a good result looks like>",
                "judge": "<pass criteria>",
                "provenance": "<source session id>",
            }],
        })
        print("[ok] golden/_template.json created")

    root = resolve_root(args, prof)
    print(f"\n[setup] host        : {host} ({prof['kind']})")
    print(f"[setup] assets root : {root}")
    print(f"[setup] data dir    : {os.path.abspath(out)}")
    print(f"\nNext:  python scripts/console.py --host {host} scan")
    return 0


def cmd_scan(args):
    host, prof = resolve_host(args)
    root = resolve_root(args, prof)
    if root is None:
        print(f"[err] host '{host}' has no root configured — pass --root <path>")
        print("      (refusing to guess: silently scanning another host's directory would")
        print("       report someone else's assets as yours)")
        return 2
    assets = discover_assets(root, prof["manifests"], prof.get("discover", "dirs"))
    if not assets:
        print(f"[warn] no {prof['label']} found under {root}")
        print(f"       manifests looked for: {prof['manifests']}")
        print("       override with --root <path> or edit hosts.json")
        return 1

    baseline = load_json(BASELINE, {"results": []})
    base_by_asset = {}
    for r in baseline.get("results", []):
        s = r["skill"]
        d = base_by_asset.setdefault(s, {"n": 0, "pass": 0})
        d["n"] += 1
        d["pass"] += 1 if r.get("passed") else 0

    golden_assets = set()
    if os.path.isdir(GOLDEN_DIR):
        for f in os.listdir(GOLDEN_DIR):
            if f.endswith(".json") and f != "_template.json":
                g = load_json(os.path.join(GOLDEN_DIR, f), {}) or {}
                golden_assets.add(g.get("skill") or f[:-5])

    prev = load_registry(host) or {}
    prev_assets = reg_assets(prev)
    active_model = get_active_model()

    out_assets = {}
    for a in assets:
        name = a["id"]
        b = base_by_asset.get(name, {"n": 0, "pass": 0})
        n, p = b["n"], b["pass"]
        rate = (p / n) if n else None
        has_golden = name in golden_assets

        if n == 0:
            status = "unverified"
        elif rate == 1.0:
            status = "strong" if a["risk"] < RISK_LOW else "watch"
        elif rate >= 0.8:
            status = "watch"
        else:
            status = "degraded"

        if status == "strong" and a["risk"] < RISK_LOW:
            rec = "feature"
        elif status == "strong":
            rec = "keep"
        elif status == "watch" and n > 0:
            rec = "keep-watch"
        elif status == "unverified" and a["risk"] >= PRUNE_RISK:
            rec = "prune-candidate"
        elif status == "unverified":
            rec = "evaluate"
        elif status == "degraded":
            rec = "rewrite-or-eliminate"
        else:
            rec = "watch"

        a.update({
            "kind": prof["kind"],
            "baseline_model": baseline.get("baseline_model"),
            "baseline_n": n,
            "baseline_pass": p,
            "baseline_rate": rate,
            "has_golden": has_golden,
            "status": status,
            "recommendation": rec,
            "eliminated": prev_assets.get(name, {}).get("eliminated", False),
            "note": prev_assets.get(name, {}).get("note", ""),
        })
        out_assets[name] = a

    registry = {
        "updated_at": now_iso(),
        "host": host,
        "kind": prof["kind"],
        "label": prof["label"],
        "root": os.path.abspath(root),
        "active_model": active_model,
        "last_regression_model": baseline.get("baseline_model", active_model),
        "count": len(out_assets),
        "assets": out_assets,
    }
    write_json(registry_path(host), registry)
    print(f"[ok] registry.{host}.json -> {len(out_assets)} {prof['label']}(s)")
    print(f"     root={root}")
    print(f"     active_model={active_model}  baseline_model={baseline.get('baseline_model')}")
    return 0


def cmd_status(args):
    host, prof = resolve_host(args)
    reg = load_registry(host)
    if not reg:
        print(f"[err] registry.{host}.json missing — run `--host {host} scan` first")
        return 1
    label = prof["label"]
    print(f"\n# 能力折旧表 · {host} ({label}) @ {reg['updated_at']}")
    print(f"# active_model={reg['active_model']}  last_regression={reg['last_regression_model']}  root={reg['root']}")
    # column width follows the longest asset name so scoped names (@scope/pkg) don't break the table
    names = list(reg_assets(reg).keys())
    w = max([28] + [len(n) + 2 for n in names])
    print("-" * (w + 68))
    print(f"{'asset':<{w}}{'fingerprint':<18}{'基线':<10}{'risk':<6}{'golden':<7}status")
    print("-" * (w + 68))
    for name, s in sorted(reg_assets(reg).items()):
        rate = "—" if s.get("baseline_rate") is None else f"{s['baseline_rate']*100:.0f}%"
        base = f"{s.get('baseline_pass',0)}/{s.get('baseline_n',0)} {rate}" if s.get("baseline_n") else "— —"
        tag = " ✓elim" if s.get("eliminated") else ""
        print(f"{name:<{w}}{s.get('fingerprint',''):<18}{base:<10}{s.get('risk',0):<6}{'Y' if s.get('has_golden') else '-':<7}{s.get('status','')}{tag}")
    print("-" * (w + 68))
    print("  注：基线为 K=1 单次判定（多为同模型自评）；risk = 行数+工具依赖的维护面代理，不代表价值。")
    return 0


def cmd_check(args):
    host, _ = resolve_host(args)
    reg = load_registry(host)
    if not reg:
        print(f"[err] registry.{host}.json missing — run `--host {host} scan` first")
        return 1
    active = get_active_model()
    last = reg.get("last_regression_model", active)
    if active != last:
        print(f"⚠  检测到模型版本变更（{host}）：{last}  →  {active}")
        print("    重跑回归以更新折旧表：")
        print("      1) 用新模型重跑 golden set -> new-results.json")
        print(f"      2) python console.py --host {host} report --results new-results.json")
        print(f"      3) python console.py --host {host} scan")
        return 2
    print(f"✓  当前模型 {active} 已是 {host} 的最近回归基线，无需重跑。")
    return 0


def cmd_bump(args):
    ver = args.version or f"v-auto-{now_iso()}"
    write_json(MODEL_STAMP, {"version": ver, "bumped_at": now_iso(),
                             "note": "simulated platform model-switch event"})
    print(f"[ok] 模型版本已更新为 {ver}（模拟模型切换事件）。")
    print("     运行 `check` 查看是否需要重跑回归。")
    return 0


def cmd_recommend(args):
    host, prof = resolve_host(args)
    reg = load_registry(host)
    if not reg:
        print(f"[err] registry.{host}.json missing — run `--host {host} scan` first")
        return 1
    label = prof["label"]
    strong, watch, unverified, prune, degraded = [], [], [], [], []
    for name, s in reg_assets(reg).items():
        if s.get("eliminated"):
            continue
        rec = s.get("recommendation")
        if rec in ("feature", "keep"):
            strong.append((name, s))
        elif rec == "keep-watch":
            watch.append((name, s))
        elif rec == "prune-candidate":
            prune.append((name, s))
        elif rec in ("rewrite-or-eliminate", "degraded"):
            degraded.append((name, s))
        else:
            unverified.append((name, s))

    print(f"\n══════════ 能力管家 · {host} · 推荐/淘汰面板（{label}）══════════\n")

    def emit(rows, tail):
        """按本段最长的 `[action:name]` 标签对齐，避免长短标签把列挤歪。"""
        if not rows:
            print("   （空）")
            return
        w = max(len("[%s:%s]" % (a, n)) for a, n, _s in rows)
        for act, name, s in rows:
            print(f"   {('[%s:%s]' % (act, name)):<{w}}  {name:<26} {tail(s)}")

    print(f"🟢 强 {label}（保留 / 可置顶推荐）")
    emit([("keep", n, s) for n, s in strong],
         lambda s: f"基线 {s.get('baseline_pass')}/{s.get('baseline_n')}  risk={s.get('risk')}")

    print("\n🟡 观察（已建基线但高风险，先保留）")
    emit([("watch", n, s) for n, s in watch],
         lambda s: f"基线 {s.get('baseline_pass')}/{s.get('baseline_n')}  risk={s.get('risk')}")

    print("\n⚪ 未验证（无基线，建议先建 golden set 再判断，暂不淘汰）")
    emit([("eval", n, s) for n, s in unverified], lambda s: f"risk={s.get('risk')}")

    print("\n🔴 已降级 · 淘汰候选（升级重跑后通过率跌破阈值才出现）")
    emit([("rewrite", n, s) for n, s in degraded],
         lambda s: f"基线 {s.get('baseline_pass')}/{s.get('baseline_n')}")

    print("\n🔶 可选精简候选（无基线 + 维护面极高，仅建议评估，绝不自动删除）")
    emit([("prune", n, s) for n, s in prune],
         lambda s: f"risk={s.get('risk')} lines={s.get('lines')} tools={s.get('tool_deps')}")

    print(f"\n💡 建议新增 {label}（填补能力缺口；安装由你确认）")
    gaps = gap_analysis(reg)
    gw = max([len("[install:%s]" % g["slug"]) for g in gaps] or [0])
    for g in gaps:
        print(f"   {('[install:%s]' % g['slug']):<{gw}}  {g['title']} — {g['why']}")

    print("\n操作方式：告诉我『keep:名称 / watch:名称 / eval:名称 / prune:名称 / install:名称』，我按你的选择执行（淘汰与安装均先确认）。")
    return 0


def gap_analysis(reg):
    names = set(reg_assets(reg).keys())
    out = []
    dev_like = [k for k in names if any(t in k for t in ("dev", "code", "debug", "test", "frontend", "backend"))]
    if dev_like and not any(t in names for t in ("test", "eval", "verify")):
        out.append({"slug": "evals", "title": "评测/测试类", "why": "已有较多开发类资产，缺专门回归/评测能力"})
    if "writing-skills" in names and "humanizer" not in names:
        out.append({"slug": "humanizer", "title": "humanizer（去 AI 味）", "why": "写作类资产缺一个去 AI 味的"})
    if not out:
        out.append({"slug": "observability", "title": "可观测性/成本追踪", "why": "净值表需要 token/采纳率遥测"})
    return out


def cmd_report(args):
    cmd = [sys.executable, REGRESS_PY, "--results", args.results,
           "--out", args.out, "--threshold", str(args.threshold)]
    if getattr(args, "compare", None):
        cmd += ["--compare", args.compare]
    rc, outp, errp = run(cmd)
    print(outp)
    if errp:
        print("[stderr]", errp)
    return rc


def match_existing(need, reg):
    assets = reg_assets(reg)
    need_l = (need or "").lower()
    hits, seen = [], set()
    for name, s in assets.items():
        if s.get("eliminated"):
            continue
        if name.lower() in need_l and name not in seen:
            hits.append((name, "名称命中"))
            seen.add(name)
    for t in TAXONOMY:
        if any(k.lower() in need_l for k in t["kw"]):
            for name, s in assets.items():
                if s.get("eliminated") or name in seen:
                    continue
                if t["slug"] in name.lower() or name.lower() in t["slug"]:
                    hits.append((name, f"类别:{t['slug']}"))
                    seen.add(name)
    return hits


def cmd_suggest(args):
    host, prof = resolve_host(args)
    need = args.need
    reg = load_registry(host) or {}
    label = prof["label"]
    print(f"\n需求：{need}\n宿主：{host}（{label}）\n")
    hits = match_existing(need, reg)
    if hits:
        print(f"✓ 你已有可用的 {label}：")
        for n, why in hits:
            print(f"   → {n}  ({why})")
        print("\n直接说『用 <名称> 做…』即可，无需安装。")
        return 0

    need_l = need.lower()
    best = None
    for t in TAXONOMY:
        if any(k.lower() in need_l for k in t["kw"]):
            best = t
            break
    if best:
        print(f"💡 当前 {host} 没有匹配的 {label}。建议新增：")
        print(f"   [install:{best['slug']}]  {best['title']}")
        print(f"   搜索词：{best['query']}")
        print("\n下一步：我可以帮你搜索并安装（需你确认）；也可先看内置能力能否直接做。")
    else:
        print(f"💡 未匹配到现成 {label}。")
        print("   建议：先用内置能力试做；若确属高频任务，我再搜市场/专家推荐。")
    return 0


# ---------------------------------------------------------------- onboarding
TRANSCRIPT_ROOTS = ["~/.workbuddy-ai/projects", "~/.claude/projects", "~/.dsh/sessions"]

HOST_PROBES = {
    "workbuddy": [("~/.workbuddy-ai/skills", "dirs", ["SKILL.md"])],
    "claude-code": [("~/.claude/skills", "dirs", ["SKILL.md"])],
    "agents": [("~/.agents/skills", "dirs", ["SKILL.md"])],
    "deepseek": [("~/.dsh/profiles/*", "deps", ["package.json", "plugin.json"])],
}

SYN = {
    "widget": ["挂件", "小部件"], "balance": ["余额"], "memory": ["记忆"],
    "plugin": ["插件"], "model": ["模型"], "connect": ["接入", "连接"],
    "hindsight": ["事后", "记忆"], "whale": ["鲸鱼"], "skill": ["技能"],
    "report": ["报告"], "search": ["搜索"], "dashboard": ["看板", "仪表盘"],
}


def _expand(tpl):
    t = os.path.expanduser(tpl)
    if "*" in t:
        import glob as _glob
        return sorted(_glob.glob(t))
    return [t]


def sanitize(s):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_") or "asset"


def find_transcripts(extra=None):
    roots = [os.path.expanduser(r) for r in TRANSCRIPT_ROOTS]
    if extra:
        roots.insert(0, os.path.abspath(os.path.expanduser(extra)))
    files = []
    for r in roots:
        if not os.path.isdir(r):
            continue
        for root, _, fs in os.walk(r):
            for f in fs:
                # DSH 存的是 session.v4.jsonl.zstd（zstd 压缩），不是裸 .jsonl。
                # 只认 .jsonl 会让 DSH 用户 bootstrap 静默拿到 0 条样本。
                if f.endswith((".jsonl", ".jsonl.zstd", ".jsonl.zst", ".json.zst")):
                    files.append(os.path.join(root, f))
    return files


def open_transcript(path):
    """打开 transcript 返回可迭代文本流；解不开返回 None（由调用方统计并明确告知）。"""
    low = path.lower()
    if not low.endswith((".zst", ".zstd")):
        try:
            return open(path, encoding="utf-8", errors="replace")
        except Exception:
            return None
    # ① Python 3.14+ 标准库
    try:
        from compression import zstd  # type: ignore
        return io.TextIOWrapper(zstd.open(path, "rb"), encoding="utf-8", errors="replace")
    except Exception:
        pass
    # ② pip install zstandard
    try:
        import zstandard  # type: ignore
        raw = open(path, "rb")
        return io.TextIOWrapper(
            zstandard.ZstdDecompressor().stream_reader(raw), encoding="utf-8", errors="replace")
    except Exception:
        pass
    # ③ zstd CLI
    exe = shutil.which("zstd")
    if exe:
        try:
            p = subprocess.run([exe, "-dc", path], capture_output=True, timeout=120)
            if p.returncode == 0:
                return io.StringIO(p.stdout.decode("utf-8", "replace"))
        except Exception:
            pass
    return None


def _strip_inject(t):
    t = re.sub(r"<system-reminder.*?</system-reminder>", "", t, flags=re.S)
    t = re.sub(r"<user-context>.*?</user-context>", "", t, flags=re.S)
    return t.strip()


COMPACTION_MARKERS = (
    "<cb_summary>", "<conversation_history_summary>", "<previous_user_message>",
    "<previous_assistant_message>", "<previous_tool_call>", "<analysis>", "<summary>",
    "Summary of the conversation", "This session is being continued",
)


def _clean_prompt(t):
    t = re.sub(r"</?user_query>", "", t)
    t = re.sub(r"<message_quote.*?</message_quote>", "", t, flags=re.S)
    t = t.replace("\\n", "\n")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def extract_prompts(files, stats=None):
    out = []
    for path in files:
        fh = open_transcript(path)
        if fh is None:
            if stats is not None:
                stats["undecodable"] = stats.get("undecodable", 0) + 1
            continue
        try:
            with fh:
                for line in fh:
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    role = o.get("role") or (o.get("message") or {}).get("role")
                    typ = o.get("type")
                    if role != "user" and typ != "user":
                        continue
                    c = o.get("content")
                    if c is None:
                        c = (o.get("message") or {}).get("content")
                    texts = []
                    if isinstance(c, str):
                        texts = [c]
                    elif isinstance(c, list):
                        for p in c:
                            if isinstance(p, dict) and p.get("type") in ("input_text", "text"):
                                texts.append(p.get("text", ""))
                    t = _strip_inject("\n".join(texts))
                    # drop compaction summaries / assistant-context artifacts
                    if any(m in t for m in COMPACTION_MARKERS):
                        continue
                    t = _clean_prompt(t)
                    if not t or len(t) < 4:
                        continue
                    if len(t) > 1500:  # a real request is short; long = summary/dump
                        continue
                    out.append(t)
        except Exception:
            continue
    seen, uniq = set(), []
    for t in out:
        k = t.strip()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(k)
    return uniq


def score_asset(prompt, asset):
    p = prompt.lower()
    name = " ".join([asset.get("id", ""), asset.get("name", ""), asset.get("description", "")]).lower()
    toks = set(re.findall(r"[a-z][a-z0-9_-]{2,}", name))
    score = 0
    for t in toks:
        if t in p:
            score += 2
        for syn in SYN.get(t, []):
            if syn in prompt:
                score += 1
    for seg in re.split(r"[-_/@.]+", asset.get("id", "")):
        if len(seg) >= 3 and seg.lower() in p:
            score += 1
    return score


def cmd_discover(args):
    print("\n探测本机宿主（自动发现）")
    print("-" * 80)
    found = {}
    for host, probes in HOST_PROBES.items():
        hit = False
        for root_tpl, mode, manifests in probes:
            for root in _expand(root_tpl):
                if not os.path.isdir(root):
                    continue
                n = len(discover_assets(root, manifests, mode))
                if n:
                    found[host] = (root, mode, manifests, n)
                    print(f"  ✓ {host:<12} {n:<3} assets  mode={mode:<4} root={root}")
                    hit = True
                    break
            if hit:
                break
        if not hit:
            print(f"  ✗ {host:<12} not found")
    print("-" * 80)
    if getattr(args, "write", False) and found:
        cfg = load_json(HOSTS_CFG, {}) or {}
        hosts = cfg.get("hosts", {})
        for host, (root, mode, manifests, n) in found.items():
            h = hosts.get(host, {})
            h.setdefault("kind", "plugin" if mode == "deps" else "skill")
            h.setdefault("label", "插件" if mode == "deps" else "skill")
            h["root"] = root
            h["discover"] = mode
            h["manifests"] = manifests
            hosts[host] = h
        cfg["hosts"] = hosts
        cfg.setdefault("default_host", "workbuddy")
        write_json(HOSTS_CFG, cfg)
        print("[ok] hosts.json 已更新为探测到的路径")
    elif found:
        print("  加 --write 可把探测结果写回 hosts.json")
    return 0


def cmd_bootstrap(args):
    host, prof = resolve_host(args)
    reg = load_registry(host)
    if not reg:
        print(f"[err] 先运行 --host {host} scan")
        return 1
    assets = reg_assets(reg)
    files = find_transcripts(getattr(args, "transcripts", None))
    if not files:
        print("[warn] 没找到会话 transcript（可加 --transcripts <目录>）")
        return 1
    stats = {}
    prompts = extract_prompts(files, stats)
    print(f"\n[bootstrap] host={host}  transcripts={len(files)}  prompts={len(prompts)}")
    if stats.get("undecodable"):
        print(f"[warn] {stats['undecodable']} 个会话文件解不开（多为 zstd 压缩）。"
              "需要 Python 3.14+ / `pip install zstandard` / 系统有 `zstd` 命令三者之一；"
              "或把它们解压成 .jsonl 后用 --transcripts <目录> 指过来。")
    made = 0
    for aid, a in assets.items():
        scored = [(score_asset(p, a), p) for p in prompts]
        scored = [(s, p) for s, p in scored if s > 0]
        scored.sort(key=lambda x: -x[0])
        picked = [p for _, p in scored[:getattr(args, "per_asset", 5)]]
        if not picked:
            continue
        fn = sanitize(aid) + ".json"
        path = os.path.join(GOLDEN_DIR, fn)
        if os.path.exists(path) and not getattr(args, "force", False):
            print(f"  skip {aid} (golden 已存在)")
            continue
        cases = [{
            "id": f"{sanitize(aid)}-draft-{i+1}",
            "polarity": "positive", "type": "task",
            "input": p,
            "expected": "TODO: 填写期望结果",
            "judge": "结果是否完成用户请求且无需返工",
            "provenance": "auto-mined",
        } for i, p in enumerate(picked)]
        write_json(path, {"skill": aid, "fingerprint": a.get("fingerprint"), "draft": True,
                          "note": "auto-drafted by bootstrap from local history; review expected/judge before trusting",
                          "cases": cases})
        made += 1
        print(f"  ✓ {aid}: {len(cases)} draft case(s) -> golden/{fn}")
    print(f"\n[ok] {made} 个 golden 草稿。下一步：人工补 expected/judge，再跑 baseline 建基线。")
    return 0


def cmd_onboard(args):
    print("=== 1/3 探测宿主 ===")
    cmd_discover(argparse.Namespace(write=True))
    print("\n=== 2/3 扫描默认宿主 ===")
    rc = cmd_scan(args)
    print("\n=== 3/3 从历史生成 golden 草稿 ===")
    if rc == 0:
        cmd_bootstrap(args)
    print("\n=== 完成 ===")
    print("下一步：检查 golden/*.json 的 expected/judge，跑 baseline 建基线，再 recommend 看面板。")
    return 0


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def build_parser():
    p = argparse.ArgumentParser(description="skill-auditor · host-agnostic capability auditor")
    p.add_argument("--host", default=None, help="host profile: workbuddy | claude-code | deepseek | generic")
    p.add_argument("--root", default=None, help="assets root (overrides host profile)")
    p.add_argument("--skills-dir", default=None, help="(deprecated alias for --root)")
    p.add_argument("--out", default=DEFAULT_OUT)
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("hosts")
    st = sub.add_parser("setup")
    st.add_argument("--model", default="v-unknown")
    sub.add_parser("scan")
    sub.add_parser("status")
    sub.add_parser("check")
    b = sub.add_parser("bump")
    b.add_argument("version", nargs="?", default=None)
    sub.add_parser("recommend")
    rp = sub.add_parser("report")
    rp.add_argument("--results", default=BASELINE)
    rp.add_argument("--threshold", type=int, default=20)
    rp.add_argument("--compare", default=None,
                    help="model id to compare against the baseline (required when >2 models)")
    sg = sub.add_parser("suggest")
    sg.add_argument("--need", required=True)
    d = sub.add_parser("discover")
    d.add_argument("--write", action="store_true")
    bs = sub.add_parser("bootstrap")
    bs.add_argument("--transcripts", default=None)
    bs.add_argument("--per-asset", type=int, default=5)
    bs.add_argument("--force", action="store_true")
    ob = sub.add_parser("onboard")
    ob.add_argument("--transcripts", default=None)
    ob.add_argument("--per-asset", type=int, default=5)
    ob.add_argument("--force", action="store_true")
    return p


def main():
    args = build_parser().parse_args()
    cmd = args.cmd or "status"
    table = {
        "hosts": cmd_hosts, "setup": cmd_setup, "scan": cmd_scan, "status": cmd_status,
        "check": cmd_check, "bump": cmd_bump, "recommend": cmd_recommend,
        "report": cmd_report, "suggest": cmd_suggest,
        "discover": cmd_discover, "bootstrap": cmd_bootstrap, "onboard": cmd_onboard,
    }
    if cmd not in table:
        print("unknown command")
        return 1
    return table[cmd](args)


if __name__ == "__main__":
    sys.exit(main())
