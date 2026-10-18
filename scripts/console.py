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
import glob
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


def configure_data_dir(out=None):
    """--out 必须对**所有**数据生效。

    以前它只改 report / golden 模板的位置，baseline / model-version / registry
    仍写死在 AUDITOR，等于「看起来支持、实际半支持」：用户把 --out 指到别处，
    数据还是散在两处。现在所有路径都从这里派生。
    """
    global DEFAULT_OUT, BASELINE, MODEL_STAMP, GOLDEN_DIR
    DEFAULT_OUT = os.path.abspath(os.path.expanduser(out or AUDITOR))
    BASELINE = os.path.join(DEFAULT_OUT, "baseline-results.json")
    MODEL_STAMP = os.path.join(DEFAULT_OUT, "model-version.json")
    GOLDEN_DIR = os.path.join(DEFAULT_OUT, "golden")
    return DEFAULT_OUT


def model_stamp_path(host=None):
    """模型戳按宿主分开：WorkBuddy 与 DSH 的模型换代不是同一时刻发生的，
    共用一个全局戳会互相污染（一边 bump，另一边也报「需重跑」）。"""
    return os.path.join(DEFAULT_OUT, "model-version.%s.json" % host) if host else MODEL_STAMP


# ---------------------------------------------------------------- 控制台安全输出
# Windows 中文控制台是 GBK（cp936）：✓、✗、⚠、emoji 都不在码表里，print 会直接
# UnicodeEncodeError，把一条正常命令变成 traceback。不能强行把 stdout 改成 UTF-8
# ——那会让老控制台显示乱码。这里按「当前 stdout 能编码什么」选符号，中文原样输出。
_OUT_ENC = (getattr(sys.stdout, "encoding", None) or "ascii")


def _can_encode(text):
    try:
        text.encode(_OUT_ENC)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def sym(primary, *fallbacks):
    for s in (primary,) + fallbacks:
        if _can_encode(s):
            return s
    return ""


OK_MARK = sym("✓", "[ok]")
FAIL_MARK = sym("✗", "[x]")
WARN_MARK = sym("⚠", "!")
GREEN = sym("🟢", "●", "+")
YELLOW = sym("🟡", "◐", "o")
WHITE = sym("⚪", "○", "o")
RED = sym("🔴", "✗", "x")
ORANGE = sym("🔶", "△", "!")
BULB = sym("💡", "*")


def use_safe_stdio():
    """兜底：即使遇到没预料的字符，也让它变成 '?' 而不是崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass

# ---------------------------------------------------------------- 指纹口径
# 指纹只覆盖**能力面**。文档/图片/锁文件不参与：
#   · 改 README 不该触发「资产被改过」——它不影响能力，却会让指纹变，产生假的 FINGERPRINT-DRIFT
#   · 插件包里常有 16MB 打包产物 + 一堆给不同 harness 的入口，文档混进去等于让噪声盖住信号
# 口径有版本号，写进 registry；换口径时老指纹失效，需要重跑一轮回归。
FP_ALGO = "v2-ability-surface"
FP_EXCLUDE_DIRS = {
    "node_modules", ".git", "__pycache__", "test", "tests", "__tests__",
    "spec", "specs", "examples", "docs", ".github", "coverage", ".venv",
}
FP_EXCLUDE_NAME_RE = re.compile(
    r"^(readme|changelog|changes|license|licence|contributing|security|"
    r"authors|notice|code_of_conduct|history)([.\-_].*)?$", re.I)
FP_EXCLUDE_SUFFIX = (".map", ".lock", "-lock.json", ".tgz", ".zip", ".log")
FP_EXCLUDE_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tiff",
    ".mp3", ".wav", ".ogg", ".m4a", ".mp4", ".webm", ".mov",
    ".ttf", ".woff", ".woff2", ".otf", ".eot",
}


def _fp_skip(rel_parts, filename):
    """这个文件要不要排除出指纹？"""
    for part in rel_parts[:-1]:
        if part.lower() in FP_EXCLUDE_DIRS:
            return True
    low = filename.lower()
    if FP_EXCLUDE_NAME_RE.match(low):
        return True
    if low.endswith(FP_EXCLUDE_SUFFIX):
        return True
    return os.path.splitext(low)[1] in FP_EXCLUDE_EXT


def fingerprint_asset(path):
    files = []
    for root, dirs, fs in os.walk(path):
        rel_root = os.path.relpath(root, path)
        rel_parts = [] if rel_root == "." else rel_root.split(os.sep)
        # 剪枝：不进入被排除的目录（否则 node_modules 里几万个文件全走一遍）
        dirs[:] = [d for d in dirs if d.lower() not in FP_EXCLUDE_DIRS]
        for f in fs:
            if not f.endswith(INDEXED_EXT):
                continue
            if _fp_skip(rel_parts + [f], f):
                continue
            files.append(os.path.join(root, f))
    files.sort()
    h = hashlib.sha256()
    for f in files:
        with open(f, "rb") as fh:
            h.update(fh.read())
    for f in files:
        h.update(os.path.relpath(f, path).encode("utf-8"))
    return h.hexdigest()[:16], len(files)

RISK_LOW = 100
RISK_HIGH = 120
PRUNE_RISK = 400

# Built-in host profiles (hosts.json overrides / extends these)
HOST_PROFILES = {
    "workbuddy": {"kind": "skill", "label": "skill", "root": None, "manifests": ["SKILL.md"]},
    "claude-code": {"kind": "skill", "label": "skill", "root": "~/.claude/skills", "manifests": ["SKILL.md"]},
    "deepseek": {"kind": "plugin", "label": "插件", "root": "~/.dsh/profiles/desktop",
                 "discover": "deps", "manifests": ["package.json", "plugin.json"],
                 "logs": ["~/.dsh/logs/*.log"]},
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
    # utf-8-sig：Windows 记事本 / PowerShell 存出来的 JSON 带 BOM，
    # 用 "utf-8" 读会 JSONDecodeError（用户手写的 recorded/golden 最容易踩）。
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def parse_frontmatter(text):
    """Minimal YAML frontmatter reader: scalars, lists, and block scalars.

    Handles the three shapes real skills use:
      - `key: value`
      - `key:` followed by `  - item` (stored under `<key>__list`)
      - `key: |` / `key: >` followed by indented text (folded into one string)

    The block-scalar case is not cosmetic: `frontend-dev` writes
    `description: |`, and a parser that drops it stores the literal "|" as the
    description — which then poisons keyword matching in `bootstrap`.
    """
    fm = {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return fm
    lines = m.group(1).splitlines()
    key = None
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if stripped.startswith("- "):
            if key:
                fm.setdefault(key + "__list", []).append(
                    stripped[2:].strip().strip('"').strip("'"))
            i += 1
            continue
        if ":" in stripped and not line[:1].isspace():
            k, v = stripped.split(":", 1)
            key = k.strip()
            v = v.strip()
            if v in ("|", ">", "|-", ">-", "|+", ">+"):
                block = []
                i += 1
                while i < len(lines) and (not lines[i].strip() or lines[i][:1].isspace()):
                    block.append(lines[i].strip())
                    i += 1
                fm[key] = " ".join(x for x in block if x).strip()
                continue
            fm[key] = v.strip('"').strip("'")
            i += 1
            continue
        i += 1
    return fm


def split_tools(s):
    return [t for t in re.split(r"[\s,\-]+", s or "") if t]


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


def discover_assets(root, manifests, mode="dirs", quiet=False):
    """Find assets under `root`.

    mode="dirs": each subdir with a manifest (scoped @scope/name dirs descended one level).
    mode="deps": read <root>/package.json dependencies and resolve each to
                 <root>/node_modules/<name> (npm/pnpm layout).

    quiet=True suppresses the `[skip]` chatter (used by `hosts`, which only wants counts).
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
                if not quiet and (os.path.isfile(os.path.join(p, "SKILL.md")) or any(
                        os.path.isfile(os.path.join(p, m)) for m in manifests)):
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
            "capabilities": capability_surface(p, manifest),
            "path": os.path.abspath(p),
        })
    return out


def capability_surface(asset_path, manifest_path):
    """资产**对外提供什么** —— 与「文件内容变了吗」分开的第二个维度。

    指纹回答「内容变了吗」，能力面回答「它对外提供的东西变了吗」。
    插件退化最常见的形态不是「回答变差」，而是「工具没注册 / 路由 404 / 钩子没跑」，
    所以对插件只测 prompt→输出 是不够的，得先能看见能力面。
    """
    caps = {}
    mname = os.path.basename(manifest_path).lower()
    if mname.endswith(".json"):
        j = load_json(manifest_path, {}) or {}
        dsh = j.get("dsh") if isinstance(j.get("dsh"), dict) else {}
        bundle = dsh.get("bundle") if isinstance(dsh.get("bundle"), dict) else {}
        if bundle.get("patch"):
            caps["patch"] = str(bundle["patch"])
        for key in ("main", "module", "bin"):
            v = j.get(key)
            if v:
                caps[key] = len(v) if isinstance(v, (dict, list)) else str(v)[:40]
        if isinstance(j.get("exports"), (dict, str)):
            caps["exports"] = len(j["exports"]) if isinstance(j["exports"], dict) else 1
        if isinstance(j.get("dependencies"), dict):
            caps["deps"] = len(j["dependencies"])
        if j.get("skills"):
            caps["skills"] = str(j["skills"])[:40]
        if isinstance(j.get("mcpServers"), dict):
            caps["mcp"] = len(j["mcpServers"])
        if j.get("hooks"):
            caps["hooks"] = str(j["hooks"])[:40]
        # cordis patch：标准库没有 YAML 解析器，只数 insert 条数（够判断能力面增减）
        for cand in ("cordis.patch.yml", "cordis.patch.yaml"):
            p = os.path.join(asset_path, cand)
            if os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8") as fh:
                        caps["insert"] = len(re.findall(r"^\s*-\s*id:", fh.read(), re.M))
                except Exception:
                    pass
                break
    else:
        try:
            with open(manifest_path, encoding="utf-8") as fh:
                txt = fh.read()
        except Exception:
            txt = ""
        fm = parse_frontmatter(txt)
        tools = fm.get("allowed-tools__list") or split_tools(fm.get("allowed-tools", ""))
        if tools:
            caps["tools"] = len(tools)
    n_scripts = 0
    for root, dirs, fs in os.walk(asset_path):
        dirs[:] = [d for d in dirs if d.lower() not in FP_EXCLUDE_DIRS]
        n_scripts += sum(1 for f in fs if f.endswith(SCRIPT_EXT))
    if n_scripts:
        caps["scripts"] = n_scripts
    return caps


# ---------------------------------------------------------------- host config
def load_hosts_config():
    cfg = load_json(HOSTS_CFG, {}) or {}
    merged = {"default_host": cfg.get("default_host", "workbuddy"),
              "hosts": dict(HOST_PROFILES)}
    # 逐字段合并：hosts.json 里的条目覆盖同名字段，但继承内置默认（logs/discover 等）。
    # 整个条目替换会让老 hosts.json 丢掉后来新增的字段 —— 表现为「明明配了却说没有」。
    for h, p in (cfg.get("hosts") or {}).items():
        base = dict(HOST_PROFILES.get(h, {}))
        base.update(p or {})
        merged["hosts"][h] = base
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


def get_active_model(host=None):
    """当前模型戳。优先读 model-version.<host>.json，fallback 到旧版全局戳。"""
    p = model_stamp_path(host)
    s = load_json(p, None)
    if s is None and host:
        s = load_json(MODEL_STAMP, None)   # 0.4.x 的单一全局戳：兼容读取
    return (s or {}).get("version") or "v-unknown"


def merge_baseline(new_doc, replace=False):
    """把一次回归结果并入基线，返回统计。

    默认按**资产**合并：本次跑到的资产整块替换，没跑到的资产原样保留。
    以前是整体覆盖，只重跑一个 skill 就会把其他资产的基线静默清空。
    """
    old = load_json(BASELINE, {"results": []}) or {"results": []}
    new_rows = [r for r in (new_doc.get("results") or []) if r.get("skill")]
    new_skills = {r["skill"] for r in new_rows}
    if replace or not old.get("results"):
        merged_rows = list(new_rows)
        kept_skills = set()
    else:
        kept = [r for r in old["results"] if r.get("skill") not in new_skills]
        kept_skills = {r.get("skill") for r in kept if r.get("skill")}
        merged_rows = kept + new_rows
    models = sorted({r.get("model") for r in merged_rows if r.get("model")})
    doc = {
        "baseline_model": (new_doc.get("baseline_model") or old.get("baseline_model") or ""),
        "models": models,
        "merged_at": now_iso(),
        "results": merged_rows,
    }
    write_json(BASELINE, doc)
    return {"replaced": sorted(new_skills), "kept": sorted(kept_skills),
            "total": len(merged_rows), "models": models}


def report_is_newer(host):
    """report 写了判定、但 registry 还没吸收 —— 面板与报告会不一致。"""
    rp = os.path.join(DEFAULT_OUT, "depreciation-report.json")
    reg = registry_path(host)
    if not (os.path.exists(rp) and os.path.exists(reg)):
        return False
    return os.path.getmtime(rp) > os.path.getmtime(reg) + 1


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
        n = len(discover_assets(r, prof.get("manifests", ["SKILL.md"]), prof.get("discover", "dirs"),
                               quiet=True)) if exists else 0
        mark = OK_MARK if exists else FAIL_MARK
        print(f"  {mark} {host:<14} kind={prof.get('kind','?'):<7} discover={prof.get('discover','dirs'):<5} assets={n:<3} root={r}")
    print("-" * 82)
    print("  用 --host <name> --root <path> 指定；改 hosts.json 可加自定义宿主。")
    return 0


def cmd_setup(args):
    host, prof = resolve_host(args)
    out = DEFAULT_OUT
    os.makedirs(out, exist_ok=True)
    gdir = GOLDEN_DIR
    os.makedirs(gdir, exist_ok=True)

    stamp = model_stamp_path(host)
    legacy_stamp = os.path.exists(MODEL_STAMP) and load_json(MODEL_STAMP, {}) or {}
    if not os.path.exists(stamp) and not legacy_stamp.get("version"):
        write_json(stamp, {"version": args.model, "bumped_at": now_iso(), "note": "set by setup"})
        print(f"[ok] {os.path.basename(stamp)} -> {args.model}")
    elif os.path.exists(stamp):
        print(f"[skip] {os.path.basename(stamp)} exists ({get_active_model(host)})")
    else:
        print(f"[skip] legacy model-version.json exists ({legacy_stamp.get('version')}) — kept as fallback")

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


def infer_model_stamp():
    """尽力从本机真实数据源推断当前模型串。返回 (version, source, confidence)。

    原则：**推断不到就返回 None，绝不猜**。一个编造的模型戳比没有戳更糟——
    它会让 check 永远说"无需重跑"（假绿灯）。

    已核实的本机情况：DSH settings.json / models.json 无模型字段；
    DSH sessions 的 jsonl 没有 model 字段；桌面日志（desktop-next.log）也不打模型串。
    所以这台机器上默认推断不到 —— 这是事实，会明确说出来，而不是退化成猜。
    """
    candidates = [
        # (来源, 取值, 置信度) —— 按可信度排序
        ("env:SKILL_AUDITOR_MODEL", os.environ.get("SKILL_AUDITOR_MODEL"), "high"),
        ("env:CURRENT_MODEL", os.environ.get("CURRENT_MODEL"), "high"),
        ("env:MODEL_VERSION", os.environ.get("MODEL_VERSION"), "high"),
        ("env:OPENAI_MODEL", os.environ.get("OPENAI_MODEL"), "medium"),
    ]
    for src, val, conf in candidates:
        if val and val.strip():
            return val.strip(), src + "（宿主进程注入的环境变量）", conf

    # settings.json 里的显式 model 字段（不猜键名，只认明确的 model/modelVersion）
    for cfg in ("~/.dsh/settings.json", "~/.workbuddy-ai/settings.json"):
        p = os.path.expanduser(cfg)
        data = load_json(p, None) if os.path.exists(p) else None
        if not isinstance(data, dict):
            continue
        for key in ("model", "modelVersion", "currentModel", "activeModel"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip(), f"{cfg}:{key}", "medium"
            if isinstance(v, dict) and v.get("id"):
                return str(v["id"]).strip(), f"{cfg}:{key}.id", "medium"

    # DSH 桌面日志里显式打了模型串的行（本机当前没有，但宿主以后可能打）
    prof = HOST_PROFILES.get("deepseek", {})
    for pat in (prof.get("logs") or []):
        for path in sorted(glob.glob(os.path.expanduser(pat)),
                           key=lambda x: -os.path.getmtime(x))[:1]:
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    tail = fh.readlines()[-500:]
            except OSError:
                continue
            for line in tail:
                m = re.search(r"(?:model|modelVersion|current_model)\D{0,6}"
                              r"([A-Za-z0-9][A-Za-z0-9._/-]{4,40})", line)
                if m and not line.startswith("[skip]"):
                    return m.group(1), f"log:{os.path.basename(path)}", "low"
    return None, None, None


def cmd_stamp(args):
    """stamp [--infer]：写模型戳。--infer 时先尝试从本机推断，推断不到就拒绝。"""
    host, _ = resolve_host(args)
    if getattr(args, "infer", False):
        ver, source, conf = infer_model_stamp()
        if not ver:
            print(f"{WARN_MARK} 推断不到当前模型版本 —— 没有写戳（猜一个假戳会让 check 永远说「无需重跑」）")
            print("   已检查并在本机为空的来源：")
            print("     · 环境变量 SKILL_AUDITOR_MODEL / CURRENT_MODEL / MODEL_VERSION")
            print("     · ~/.dsh/settings.json、~/.workbuddy-ai/settings.json 的 model 字段")
            print("     · ~/.dsh/logs/*.log 里的模型串（desktop-next.log 目前不打）")
            print("   两种正确做法：")
            print("     a) 平台侧注入：宿主启动时写 SKILL_AUDITOR_MODEL 环境变量，再跑 stamp --infer")
            print("     b) 手动：python console.py --host %s stamp <版本号>" % host)
            return 2
        print(f"[infer] 从 {source} 推断出模型串（置信度 {conf}）：{ver}")
    else:
        ver = args.version
        if not ver:
            print("[err] stamp 需要一个版本号：stamp <版本号>，或用 stamp --infer 尝试推断")
            return 2
    write_json(model_stamp_path(host), {"version": ver, "bumped_at": now_iso(),
                                        "note": "stamp"})
    print(f"[ok] {host} 的模型戳 -> {ver}（{os.path.basename(model_stamp_path(host))}）")
    print(f"     下一步：python console.py --host {host} check")
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
    active_model = get_active_model(host)
    base_by_asset = {}
    skipped = 0
    for r in baseline.get("results", []):
        # 只统计当前模型戳下的判定。模型换代后还没重跑的资产会回到 unverified，
        # 这是有意的：旧模型上的 100% 不能证明新模型上还灵（否则就是假绿灯）。
        if active_model and r.get("model") and r["model"] != active_model:
            skipped += 1
            continue
        s = r["skill"]
        d = base_by_asset.setdefault(s, {"n": 0, "pass": 0, "k_total": 0, "min_k": None})
        d["n"] += 1
        d["pass"] += 1 if r.get("passed") else 0
        k = r.get("k")
        if k:
            d["k_total"] += k
            d["min_k"] = k if d["min_k"] is None else min(d["min_k"], k)

    golden_assets = set()
    if os.path.isdir(GOLDEN_DIR):
        for f in os.listdir(GOLDEN_DIR):
            if f.endswith(".json") and f != "_template.json":
                g = load_json(os.path.join(GOLDEN_DIR, f), {}) or {}
                golden_assets.add(g.get("skill") or f[:-5])

    prev = load_registry(host) or {}
    prev_assets = reg_assets(prev)

    out_assets = {}
    for a in assets:
        name = a["id"]
        b = base_by_asset.get(name, {"n": 0, "pass": 0, "k_total": 0, "min_k": None})
        n, p = b["n"], b["pass"]
        rate = (p / n) if n else None
        has_golden = name in golden_assets
        min_k = b.get("min_k")

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
            "baseline_min_k": min_k,
            "baseline_runs": b.get("k_total") or 0,
            "has_golden": has_golden,
            "status": status,
            "recommendation": rec,
            "eliminated": prev_assets.get(name, {}).get("eliminated", False),
            "note": prev_assets.get(name, {}).get("note", ""),
        })
        # 能力面 diff：插件升级后最该看的是「对外提供的东西少了什么」，不是通过率
        prev_caps = (prev_assets.get(name) or {}).get("capabilities") or {}
        cur_caps = a.get("capabilities") or {}
        if prev_caps and isinstance(prev_caps, dict):
            added = sorted(set(cur_caps) - set(prev_caps))
            removed = sorted(set(prev_caps) - set(cur_caps))
            changed = sorted(k for k in set(cur_caps) & set(prev_caps)
                             if cur_caps[k] != prev_caps[k])
            if added or removed or changed:
                a["caps_diff"] = {"added": added, "removed": removed, "changed": changed}
        out_assets[name] = a

    registry = {
        "updated_at": now_iso(),
        "host": host,
        "kind": prof["kind"],
        "label": prof["label"],
        "root": os.path.abspath(root),
        "fp_algo": FP_ALGO,
        "active_model": active_model,
        "last_regression_model": baseline.get("baseline_model") or active_model,
        "count": len(out_assets),
        "assets": out_assets,
    }
    write_json(registry_path(host), registry)
    print(f"[ok] registry.{host}.json -> {len(out_assets)} {prof['label']}(s)")
    print(f"     root={root}")
    print(f"     active_model={active_model}  baseline_model={baseline.get('baseline_model')}")
    if skipped:
        print(f"     {WARN_MARK} {skipped} 条基线属于旧模型，未计入（模型换代后先重跑 golden set 再 scan）")
    drifted = [(k, v["caps_diff"]) for k, v in out_assets.items() if v.get("caps_diff")]
    if drifted:
        print(f"[caps] {len(drifted)} 个资产的能力面有变化（这是「它对外提供的东西变了」，与通过率无关）：")
        for k, d in drifted:
            bits = []
            if d["added"]:
                bits.append("+" + ",".join(d["added"]))
            if d["removed"]:
                bits.append("-" + ",".join(d["removed"]))
            if d["changed"]:
                bits.append("~" + ",".join(d["changed"]))
            print(f"       {k:<40} {' '.join(bits)}")
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
    print("-" * (w + 74))
    print(f"{'asset':<{w}}{'fingerprint':<18}{'基线':<10}{'K':<6}{'risk':<6}{'caps':<6}{'golden':<7}status")
    print("-" * (w + 80))
    for name, s in sorted(reg_assets(reg).items()):
        rate = "—" if s.get("baseline_rate") is None else f"{s['baseline_rate']*100:.0f}%"
        base = f"{s.get('baseline_pass',0)}/{s.get('baseline_n',0)} {rate}" if s.get("baseline_n") else "— —"
        mk = s.get("baseline_min_k")
        ktxt = (f"{mk}" + (f"×{s.get('baseline_runs')}" if s.get("baseline_runs") else "")) if mk else "—"
        tag = f" {OK_MARK}elim" if s.get("eliminated") else ""
        caps = s.get("capabilities") or {}
        caps_n = len(caps) if isinstance(caps, dict) else 0
        print(f"{name:<{w}}{s.get('fingerprint',''):<18}{base:<10}{ktxt:<6}{s.get('risk',0):<6}"
              f"{caps_n:<6}{'Y' if s.get('has_golden') else '-':<7}{s.get('status','')}{tag}")
    print("-" * (w + 80))
    # K 口径要随实际数据变化，不能写死"K=1"
    min_k = min([s.get("baseline_min_k") for s in reg_assets(reg).values()
                 if s.get("baseline_min_k")] or [1])
    knote = f"K={min_k}" if min_k <= 1 else f"最小 K={min_k}（K≥3 才有统计意义，K=1 只是单次判定）"
    print(f"  注：基线{knote}，多为同模型自评；risk = 行数+工具依赖的维护面代理，不代表价值。")
    print(f"  caps = 能力面条目数（入口/patch/工具/脚本…）；指纹口径 {reg.get('fp_algo', 'v1')}，"
          "只算能力面，不含 README/图片/锁文件。")
    print("  注：基线只统计 active_model 下的判定；换代后未重跑的资产会回到 unverified。")
    if report_is_newer(host):
        print(f"  {WARN_MARK} 折旧报告比注册表新：report 的判定还没进面板，"
              "用 report --promote 合并基线后再 scan。")
    return 0


def cmd_check(args):
    host, _ = resolve_host(args)
    reg = load_registry(host)
    if not reg:
        print(f"[err] registry.{host}.json missing — run `--host {host} scan` first")
        return 1
    active = get_active_model(host)
    last = reg.get("last_regression_model", active)
    if active != last:
        print(f"{WARN_MARK}  检测到模型版本变更（{host}）：{last}  →  {active}")
        print("    重跑回归以更新折旧表：")
        print("      1) 用新模型重跑 golden set -> new-results.json")
        print(f"      2) python console.py --host {host} report --results new-results.json --promote")
        print(f"      3) python console.py --host {host} scan")
        return 2
    print(f"{OK_MARK}  当前模型 {active} 已是 {host} 的最近回归基线，无需重跑。")
    return 0


def cmd_bump(args):
    host, _ = resolve_host(args)
    ver = args.version or f"v-auto-{now_iso()}"
    stamp = model_stamp_path(host)
    write_json(stamp, {"version": ver, "bumped_at": now_iso(),
                       "note": "simulated platform model-switch event"})
    print(f"[ok] 模型版本已更新为 {ver}（{host}，模拟模型切换事件）。")
    print(f"     写入 {os.path.basename(stamp)}；运行 `check` 查看是否需要重跑回归。")
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

    print(f"{GREEN} 强 {label}（保留 / 可置顶推荐）")
    emit([("keep", n, s) for n, s in strong],
         lambda s: f"基线 {s.get('baseline_pass')}/{s.get('baseline_n')}  risk={s.get('risk')}")

    print(f"\n{YELLOW} 观察（已建基线但高风险，先保留）")
    emit([("watch", n, s) for n, s in watch],
         lambda s: f"基线 {s.get('baseline_pass')}/{s.get('baseline_n')}  risk={s.get('risk')}")

    print(f"\n{WHITE} 未验证（无基线，建议先建 golden set 再判断，暂不淘汰）")
    emit([("eval", n, s) for n, s in unverified], lambda s: f"risk={s.get('risk')}")

    print(f"\n{RED} 已降级 · 淘汰候选（升级重跑后通过率跌破阈值才出现）")
    emit([("rewrite", n, s) for n, s in degraded],
         lambda s: f"基线 {s.get('baseline_pass')}/{s.get('baseline_n')}")

    print(f"\n{ORANGE} 可选精简候选（无基线 + 维护面极高，仅建议评估，绝不自动删除）")
    emit([("prune", n, s) for n, s in prune],
         lambda s: f"risk={s.get('risk')} lines={s.get('lines')} tools={s.get('tool_deps')}")

    print(f"\n{BULB} 建议新增 {label}（填补能力缺口；安装由你确认）")
    gaps = gap_analysis(reg)
    gw = max([len("[install:%s]" % g["slug"]) for g in gaps] or [0])
    for g in gaps:
        print(f"   {('[install:%s]' % g['slug']):<{gw}}  {g['title']} — {g['why']}")

    print("\n操作方式：告诉我『keep:名称 / watch:名称 / eval:名称 / prune:名称 / install:名称』，我按你的选择执行（淘汰与安装均先确认）。")
    if report_is_newer(host):
        print(f"{WARN_MARK} 折旧报告比注册表新：report 的判定还没进面板，"
              "用 report --promote 合并基线后再 scan。")
    return 0


# ---------------------------------------------------------------- runtime evidence
# 「运行时探针」有主动/被动两种，成本差两个数量级，别混为一谈：
#   主动探针 = 我启动宿主、问它「你到底注册了哪些工具」。DSH 的 desktop profile 被 Electron
#              独占（`dsh --profile desktop --dump-config` 直接拒绝），没有 headless 入口 →
#              要逆向 IPC 或驱动 UI。贵、脆，且和用户正在用的实例抢资源。
#   被动证据 = 读宿主**自己打的日志**，找出「文件齐全、manifest 正确、但跑起来报错」的插件。
#              零启动、零端口、跨 desktop/web 都行；代价是有盲区（见 PROBE_BLIND_SPOTS）。
# 这里实现的是后者。日志格式：`<iso> [level] [source] message`，source 为插件包名时可归因。
PROBE_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\S+)\s+\[(\w+)\]\s+\[([^\]]+)\]\s*(.*)$")
PROBE_FAULT_RE = re.compile(
    r"fail|error|enoent|exception|unavailable|missing|refus|denied|timeout|"
    r"not found|threw|crash|invalid|cannot|unable", re.I)
PROBE_MAX_BYTES = 4 * 1024 * 1024   # 单个日志只读尾部，日志可以很大
PROBE_BLIND_SPOTS = (
    "插件不写日志 → 看不见；日志轮转 → 旧故障消失；"
    "从没被用过的插件没有证据；日志里的 info 级故障靠关键词识别，可能漏或误报"
)


def discover_logs(prof):
    out = []
    for pat in prof.get("logs") or []:
        for p in sorted(glob.glob(os.path.expanduser(pat)), key=lambda x: -os.path.getmtime(x)):
            out.append(p)
    return out


def read_tail(path, limit=PROBE_MAX_BYTES):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > limit:
                fh.seek(size - limit)
                fh.readline()  # 丢掉被截断的半行
            return fh.read().decode("utf-8", "replace").splitlines()
    except Exception:
        return []


def cmd_probe(args):
    host, prof = resolve_host(args)
    reg = load_registry(host)
    if not reg:
        print(f"[err] registry.{host}.json missing — run `--host {host} scan` first")
        return 1
    logs = discover_logs(prof)
    if not logs:
        print(f"[warn] 宿主 {host} 没有配置可读日志（hosts.json 里的 \"logs\"），无法采集运行时证据。")
        print("       这不是「没问题」，是「没有证据」。主动探针需要启动宿主，本工具不做。")
        return 1

    names = set(reg_assets(reg).keys())
    hits = {}       # asset -> [{ts, level, msg, fault, log}]
    total_lines = 0
    for path in logs:
        for line in read_tail(path):
            total_lines += 1
            m = PROBE_LINE_RE.match(line)
            if not m:
                continue
            ts, level, source, msg = m.group(1), m.group(2), m.group(3), m.group(4)
            if source not in names:
                continue
            fault = bool(PROBE_FAULT_RE.search(msg)) or level.lower() in ("error", "fatal")
            hits.setdefault(source, []).append({
                "ts": ts, "level": level, "msg": msg.strip(),
                "fault": fault, "log": os.path.basename(path),
            })

    print(f"\n══════════ 运行时证据 · {host} · {prof['label']} ══════════")
    print(f"日志: {', '.join(os.path.dirname(p) for p in logs[:1])}  "
          f"（{len(logs)} 个文件，{total_lines} 行）")
    print("只读采集，不启动宿主。")
    bad = []
    for name in sorted(names):
        rows = hits.get(name) or []
        faults = [r for r in rows if r["fault"]]
        if faults:
            bad.append((name, faults))
    if not bad:
        print(f"\n{OK_MARK} 日志里没有可归因到 {len(names)} 个资产的故障迹象。")
        print(f"  （不代表没问题 —— {PROBE_BLIND_SPOTS}）")
        return 0

    print(f"\n{WARN_MARK} {len(bad)} 个资产有运行时故障迹象（文件齐全、manifest 正确，但跑起来报错）：")
    for name, faults in bad:
        last = faults[-1]
        print(f"\n  {name}")
        print(f"    {len(faults)} 条故障迹象  最近 {last['ts']}  [{last['level']}]")
        print(f"      {last['msg'][:160]}")
        print(f"      → 静态扫描（文件/manifest/能力面）看不见这类问题；这是宿主自己打出来的")
    print(f"\n盲区：{PROBE_BLIND_SPOTS}")
    print("要「实际注册了哪些工具」，得启动宿主 —— desktop profile 被 Electron 独占，本工具不做。")
    return 1 if getattr(args, "strict", False) else 0


def _covered(names, words):
    """资产名里是否已有这类能力（子串匹配）。

    以前这里是 `t in names` 的精确集合判断：资产叫 `test-driven-development`，
    就永远匹配不到 "test"，于是面板一直建议再装一个评测类 skill —— 假缺口。
    """
    low = [n.lower() for n in names]
    return any(any(w in n for w in words) for n in low)


def gap_analysis(reg):
    names = set(reg_assets(reg).keys())
    out = []
    dev_like = _covered(names, ("dev", "code", "debug", "test", "frontend", "backend", "plan"))
    if dev_like and not _covered(names, ("eval", "test", "verify", "debug", "regress")):
        out.append({"slug": "evals", "title": "评测/测试类", "why": "已有开发类资产，但没有专门做回归/评测的"})
    if _covered(names, ("writing", "doc", "blog")) and not _covered(names, ("humanizer",)):
        out.append({"slug": "humanizer", "title": "humanizer（去 AI 味）", "why": "写作类资产缺一个去 AI 味的"})
    if not out:
        out.append({"slug": "observability", "title": "可观测性/成本追踪",
                    "why": "净值表需要 token/采纳率遥测（当前没有数据源，属方法论待落地项）"})
    return out


def cmd_report(args):
    results = args.results or BASELINE
    cmd = [sys.executable, REGRESS_PY, "--results", results,
           "--out", DEFAULT_OUT, "--threshold", str(args.threshold)]
    if getattr(args, "compare", None):
        cmd += ["--compare", args.compare]
    rc, outp, errp = run(cmd)
    print(outp)
    if errp:
        print("[stderr]", errp)
    if rc != 0:
        return rc
    promote = getattr(args, "promote", False)
    if promote:
        data = load_json(results, {}) or {}
        st = merge_baseline(data, replace=getattr(args, "replace", False))
        print(f"\n[ok] 基线已合并：替换 {len(st['replaced'])} 个资产，保留 {len(st['kept'])} 个，"
              f"共 {st['total']} 条（模型：{', '.join(st['models']) or '?'}）")
        if st["kept"]:
            print(f"     保留未重跑的资产：{', '.join(st['kept'])}（仍是旧模型的判定）")
        print(f"     下一步：python scripts/console.py --host {args.host or 'workbuddy'} scan")
    else:
        print("\n提示：报告不会改基线。要让折旧表/面板看到这次判定，请加 --promote 后重跑，再 scan。")
    return rc


def match_existing(need, reg):
    """需求 → 已装资产。名称全串 + 名称片段（去噪）两级匹配。

    以前只认「完整资产名出现在需求里」，用户说「帮我润色一下」永远匹配不到
    humanizer；片段匹配让「去 AI 味 / 财报 / 图表」这类自然说法也能命中。
    """
    assets = reg_assets(reg)
    need_l = (need or "").lower()
    noise = {"skill", "plugin", "tool", "tools", "agent", "agents", "dsh", "the", "and", "with"}
    hits, seen = [], set()
    for name, s in assets.items():
        if s.get("eliminated"):
            continue
        segs = [p for p in re.split(r"[-_/@.]+", name.lower())
                if len(p) >= 4 and p not in noise]
        if name.lower() in need_l or any(p in need_l for p in segs):
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
        print(f"{OK_MARK} 你已有可用的 {label}：")
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
        print(f"{BULB} 当前 {host} 没有匹配的 {label}。建议新增：")
        print(f"   [install:{best['slug']}]  {best['title']}")
        print(f"   搜索词：{best['query']}")
        print("\n下一步：我可以帮你搜索并安装（需你确认）；也可先看内置能力能否直接做。")
    else:
        print(f"{BULB} 未匹配到现成 {label}。")
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
                n = len(discover_assets(root, manifests, mode, quiet=True))
                if n:
                    found[host] = (root, mode, manifests, n)
                    print(f"  {OK_MARK} {host:<12} {n:<3} assets  mode={mode:<4} root={root}")
                    hit = True
                    break
            if hit:
                break
        if not hit:
            print(f"  {FAIL_MARK} {host:<12} not found")
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
        print(f"  {OK_MARK} {aid}: {len(cases)} draft case(s) -> golden/{fn}")
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
    # 子进程统一说 UTF-8：否则父进程按 GBK 解码、子进程按环境变量输出时，
    # 中文报告会变成乱码或直接 UnicodeDecodeError。
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
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
    st = sub.add_parser("stamp")
    st.add_argument("version", nargs="?", default=None,
                    help="模型版本号；缺省时必须配合 --infer")
    st.add_argument("--infer", action="store_true",
                    help="从环境变量 / settings / 宿主日志推断模型串；推断不到则拒绝写戳")
    sub.add_parser("recommend")
    pb = sub.add_parser("probe")
    pb.add_argument("--strict", action="store_true",
                    help="有运行时故障迹象时以退出码 1 结束（给 CI/脚本用）")
    rp = sub.add_parser("report")
    rp.add_argument("--results", default=None,
                    help="results JSON (default: <data dir>/baseline-results.json)")
    rp.add_argument("--threshold", type=int, default=20)
    rp.add_argument("--compare", default=None,
                    help="model id to compare against the baseline (required when >2 models)")
    rp.add_argument("--promote", action="store_true",
                    help="把本次结果合并进基线（否则报告只是报告，面板看不到）")
    rp.add_argument("--replace", action="store_true",
                    help="与 --promote 连用：整体替换基线，而不是按资产合并")
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
    use_safe_stdio()
    # --out 必须在此之前落地：所有数据路径（registry/baseline/model/golden）都从它派生。
    configure_data_dir(getattr(args, "out", None))
    cmd = args.cmd or "status"
    table = {
        "hosts": cmd_hosts, "setup": cmd_setup, "scan": cmd_scan, "status": cmd_status,
        "check": cmd_check, "bump": cmd_bump, "stamp": cmd_stamp, "recommend": cmd_recommend,
        "probe": cmd_probe,
        "report": cmd_report, "suggest": cmd_suggest,
        "discover": cmd_discover, "bootstrap": cmd_bootstrap, "onboard": cmd_onboard,
    }
    if cmd not in table:
        print("unknown command")
        return 1
    return table[cmd](args)


if __name__ == "__main__":
    sys.exit(main())
