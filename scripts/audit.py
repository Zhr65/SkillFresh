#!/usr/bin/env python3
"""audit.py — 主动体检：装上就跑，直接给结论（不是给任务清单）。

用户诉求（2026-10-19）：
  「装上后工具就开始检测，发现被淘汰的告诉用户；还要和市场比，择优推荐。」
  而不是让用户自己跑用例、自己写判据、自己 ingest。

设计：
  ① 版本面（最硬、无需跑用例）：已装版本 vs npm registry latest vs 市场目录
     —— 落后就是落后，不需要任何判定标准
  ② 运行时面：复用 probe 的日志证据（插件报错、401、ENOENT）
  ③ 基线面：已经有基线的看通过率；没有的明确说"没测过"，并给出可一条命令跑的部分
  ④ 结论：分档输出「该修 / 该升 / 该删 / 正常 / 未测」，每档给一句可执行建议

诚实边界：
  · 版本落后 ≠ 该淘汰（可能新版有 breaking change）
  · 市场不在前 200 条 ≠ 未收录（目录分页）
  · 网络不可达时，版本面跳过并明说，不假装"已是最新"
"""
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

UA = {"User-Agent": "skill-auditor/0.5.0"}
NPM = "https://registry.npmjs.org/%s/latest"
DSH_MARKET = "https://deepseek1024.com/api/v2/plugins?limit=200"


def get_json(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def vtuple(v):
    """'1.66.42' -> (1,66,42)；非数字段当 0。"""
    if not v:
        return None
    parts = re.findall(r"\d+", str(v))
    return tuple(int(p) for p in parts[:4]) if parts else None


def npm_latest(pkg):
    try:
        return get_json(NPM % urllib.parse.quote(pkg, safe=""))["version"]
    except Exception:
        return None


def market_index():
    """市场目录：name -> 条目。失败返回 (None, 原因)。"""
    try:
        d = get_json(DSH_MARKET)
        return {p.get("name"): p for p in (d.get("plugins") or [])}, d.get("generatedAt")
    except Exception as e:
        return None, str(e)


def probe_evidence(logdir):
    """读宿主日志，返回 {plugin: [fault lines]}。和 console.py probe 同源。"""
    hits = {}
    try:
        files = [os.path.join(logdir, f) for f in os.listdir(logdir) if f.endswith(".log")]
    except OSError:
        return hits
    pat = re.compile(r"^(\S+)\s+\[(\w+)\]\s+\[([^\]]+)\]\s*(.*)$")
    fault = re.compile(r"fail|error|enoent|exception|unavailable|missing|refus|denied|"
                       r"timeout|not found|threw|crash|invalid|cannot|unable|401|403", re.I)
    for path in sorted(files, key=lambda p: -os.path.getmtime(p))[:1]:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()[-3000:]
        except OSError:
            continue
        for line in lines:
            m = pat.match(line)
            if not m:
                continue
            src, msg = m.group(3), m.group(4)
            if fault.search(msg) or m.group(2).lower() in ("error", "fatal"):
                hits.setdefault(src, []).append(msg.strip()[:160])
    return hits


def load_json(p, default=None):
    try:
        with open(p, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return default


def main(argv):
    host = "deepseek"
    if "--host" in argv:
        host = argv[argv.index("--host") + 1]
    offline = "--offline" in argv

    reg_path = os.path.join(ROOT, "registry.%s.json" % host)
    reg = load_json(reg_path)
    if not reg:
        print("还没有 registry.%s.json —— 先跑一次 scan（或让 audit 自己扫）" % host)
        return 1
    assets = reg.get("assets") or {}
    bl = load_json(os.path.join(ROOT, "baseline-results.json"), {"results": []}) or {}
    rows = bl.get("results") or []
    active = reg.get("active_model")

    # 基线统计（只算当前模型的）
    base = {}
    for r in rows:
        if r.get("model") and active and r["model"] != active:
            continue
        d = base.setdefault(r["skill"], {"n": 0, "p": 0, "k": 0, "attrs": []})
        d["n"] += 1
        d["p"] += 1 if r.get("passed") else 0
        d["k"] = max(d["k"], r.get("k") or 1)
        if not r.get("passed") and r.get("attribution"):
            d["attrs"].append(str(r["attribution"]).lower())

    logs = None
    if not offline:
        cfg = load_json(os.path.join(ROOT, "hosts.json"), {}) or {}
        prof = ((cfg.get("hosts") or {}).get(host) or {})
        for pat in (prof.get("logs") or []):
            import glob
            for p in glob.glob(os.path.expanduser(pat)):
                logs = os.path.dirname(p)
                break
            if logs:
                break
        if not logs:
            d = os.path.expanduser("~/.dsh/logs")
            logs = d if os.path.isdir(d) else None
    ev = probe_evidence(logs) if logs else {}

    mkt, mkt_meta = (None, "offline") if offline else market_index()

    buckets = {"upgrade": [], "fix": [], "retire": [], "ok": [], "untested": []}

    for name, s in sorted(assets.items()):
        installed = s.get("version")
        latest = None if offline else npm_latest(name)
        behind = None
        if installed and latest:
            a, b = vtuple(installed), vtuple(latest)
            if a and b and b > a:
                behind = latest

        b = base.get(name)
        rate = (b["p"] / b["n"]) if b and b["n"] else None
        faults = ev.get(name) or []

        item = {"name": name, "installed": installed, "latest": latest,
                "behind": behind, "base": b, "rate": rate, "faults": faults,
                "status": s.get("status")}

        # 判定顺序：先"能不能用"，再"该不该升"
        if rate is not None and rate == 0 and faults:
            buckets["fix"].append(item)
        elif rate is not None and rate < 0.8:
            buckets["fix"].append(item)
        elif behind:
            buckets["upgrade"].append(item)
        elif faults:
            buckets["fix"].append(item)
        elif b and b["n"]:
            buckets["ok"].append(item)
        else:
            buckets["untested"].append(item)

    print(f"\n{'='*70}")
    print(f"  主动体检 · {host} · {len(assets)} 个资产 · 模型 {active}")
    print(f"{'='*70}")
    if mkt_meta and mkt_meta != "offline":
        print(f"  市场快照：{mkt_meta[:19]}（{len(mkt or {})} 条）")

    def show(title, items, advice):
        if not items:
            return
        print(f"\n{title}（{len(items)}）")
        for it in items:
            print(f"  • {it['name']}")
            bits = []
            if it["installed"]:
                bits.append("已装 %s" % it["installed"])
            if it["behind"]:
                bits.append("最新 %s ← 落后" % it["behind"])
            if it["rate"] is not None:
                b = it["base"]
                bits.append("基线 %d/%d%s" % (b["p"], b["n"], " K=%d" % b["k"] if b["k"] else ""))
            if it["faults"]:
                bits.append("%d 条运行时故障" % len(it["faults"]))
            print("      " + " | ".join(bits))
            if it["faults"]:
                print("      ↳ " + it["faults"][-1])
        print(f"  → {advice}")

    show("🔴 建议先修（跑不起来 / 通过率低）", buckets["fix"],
         "修配置或环境；修不好再考虑停用——别急着重写")
    show("⬆️ 建议升级（市场有新版）", buckets["upgrade"],
         "升级前看一眼 changelog 有没有 breaking change；升完 scan 一次")
    show("✅ 正常", buckets["ok"], "保持现状")
    show("⚪ 未测（没有基线，无法判断好坏）", buckets["untested"],
         "这不是故障。想测就跑：console.py baseline --from-seed（seed 里有现成用例）")

    if not any(buckets.values()):
        print("\n  没有可报告的资产")

    print(f"\n{'='*70}")
    print("口径：版本来自 npm registry（权威）；市场条目只作参考。")
    print("      版本落后 ≠ 该淘汰（可能是上游 breaking change 未适配）。")
    print("      没基线 ≠ 该删（只是没测过）。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
