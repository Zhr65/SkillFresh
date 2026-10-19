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

# GBK 防护（审查 P1）：audit.py 之前直接 print emoji，GBK 终端必崩
# （npm 插件路径因强制 PYTHONIOENCODING=utf-8 不受影响，纯 CLI 冷启动必崩）。
try:
    from console import use_safe_stdio, sym
    use_safe_stdio()
    SYM_OK, SYM_UP, SYM_WARN, SYM_TEST = sym("ok"), sym("up"), sym("warn"), sym("test")
except Exception:  # 兜底：console.py 缺失也不崩
    SYM_OK, SYM_UP, SYM_WARN, SYM_TEST = "[ok]", "[up]", "[!]", "[t]"

UA = {"User-Agent": "skill-auditor/0.5.0"}
NPM = "https://registry.npmjs.org/%s/latest"
DSH_MARKET = "https://deepseek1024.com/api/v2/plugins?limit=200"


def _int(v):
    """审查 P1：int('1,234') / int('12.5') 会崩——畸形数据一律降级为 0。"""
    try:
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return 0


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


def npm_latest_batch(pkgs, per_call_timeout=6):
    """批量查 npm latest，带**总预算**（审查 P1）：
    原来串行 N×15s，网络半通时必然超过外层 120s 被杀，结果全丢。
    现在共享一个 deadline：预算耗尽，剩余插件标 None（输出会明说版本面不完整），
    且首个成功/首个失败即推断后续——同 registry 的可达性是一致的。"""
    deadline = __import__("time").time() + per_call_timeout * min(len(pkgs), 6)
    out = {}
    for p in pkgs:
        if __import__("time").time() > deadline:
            for rest in pkgs:
                if rest not in out:
                    out[rest] = None
            break
        out[p] = npm_latest(p)
    return out


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


def find_alternatives(name, mkt, installed_version, limit=3):
    """找市场里可能替代这个插件的东西 —— 用户视角：「有没有更好的」。

    替代判定（保守，宁可少报 —— 报错的替代比不报更糟）：
      ① 名字/描述**共享有意义的词**（长度≥5，排除 memory/plugin/dsh 这类通用词）
      ② 或者同 category 且安装量不低于它
    必须**安装量 ≥ 它的 1/3**，否则不算"更好的"。
    """
    if not mkt or not isinstance(mkt, dict):
        return []
    me = mkt.get(name)
    cat = (me or {}).get("category")
    base_install = _int((me or {}).get("installCount"))
    # 太通用的词会造成误报（实测：browser 插件的替代里混进了 memory 插件）。
    # 注意：browser / memory / model 这些是**功能词**，不能排除 ——
    # 排除了就找不到真正的同类（它们的替代项正是靠这些词命中的）。
    GENERIC = {"dsh", "plugin", "plugins", "skill", "skills", "agent", "agents",
               "tool", "tools", "harness", "deepseek", "coding", "connect",
               "extension", "widget", "core", "base"}
    toks = {t for t in re.split(r"[-_/@.]+", name.lower())
            if len(t) >= 5 and t not in GENERIC}
    out = []
    for n, p in mkt.items():
        if n == name:
            continue
        desc = str((p.get("description") or {}).get("zh")
                   or (p.get("description") or ""))
        shared = any(t in n.lower() or t in desc.lower() for t in toks)
        same_cat = bool(cat) and p.get("category") == cat
        if not (shared or same_cat):
            continue
        inst = _int(p.get("installCount"))
        # 安装量太低的不算替代（"更好的"至少得有人用）
        if inst < max(10, base_install // 3):
            continue
        out.append({"name": n, "installs": inst,
                    "growth": p.get("growth7d"), "pushed": (p.get("pushedAt") or "")[:10],
                    "cat": p.get("category"), "desc": desc[:70],
                    "why": "功能相近" if shared else "同类"})
    out.sort(key=lambda x: -(x["installs"] or 0))
    return out[:limit]


def load_json(p, default=None):
    try:
        with open(p, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return default


def live_version(profile_root, name):
    """实时读已装插件的版本 —— 不信 registry 快照。

    先 profile/node_modules，再回落到 Electron 主包（bundled 插件）。
    """
    if not profile_root:
        return None
    cands = [os.path.join(profile_root, "node_modules", name, "package.json")]
    app = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "DSH Desktop",
                       "resources", "app", "node_modules", name, "package.json")
    cands.append(app)
    for p in cands:
        d = load_json(p)
        if isinstance(d, dict) and d.get("version"):
            return d["version"]
    return None


def main(argv):
    host = "deepseek"
    if "--host" in argv:
        host = argv[argv.index("--host") + 1]
    offline = "--offline" in argv
    # 透传给内部 scan：否则 generic 这种没有默认根的宿主会扫不动
    root_arg = []
    if "--root" in argv:
        root_arg = ["--root", argv[argv.index("--root") + 1]]
    out_arg = []
    if "--out" in argv:
        out_arg = ["--out", argv[argv.index("--out") + 1]]
    # 数据目录：--out 必须生效（和 console.py 同一口径）。
    # 以前 audit 只读 ROOT，导致 --out 指的目录读不到注册表。
    DATA = os.path.abspath(os.path.expanduser(
        argv[argv.index("--out") + 1])) if out_arg else ROOT

    reg_path = os.path.join(DATA, "registry.%s.json" % host)
    reg = load_json(reg_path)
    if not reg:
        # 首次运行（陌生人下载后的第一眼）——不能让人先去敲 scan。
        # 自己扫一次；扫失败才报错，并把原因说清。
        if "--no-scan" in argv:
            print("还没有 registry.%s.json（--no-scan：不自动扫描）" % host)
            return 1
        print("首次运行：还没有注册表，先自动扫描一次…")
        try:
            import subprocess
            rs = subprocess.run(
                [sys.executable, os.path.join(HERE, "console.py"),
                 "--host", host, *root_arg, *out_arg, "scan"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=180)
            if rs.returncode != 0:
                print("[err] 自动扫描失败（exit %s）：" % rs.returncode)
                print((rs.stdout or "")[-800:])
                print((rs.stderr or "")[-400:])
                return 1
        except Exception as e:
            print("[err] 自动扫描失败：%s" % e)
            return 1
        reg = load_json(reg_path)
        if not reg:
            print("[err] 扫描后仍找不到 registry.%s.json" % host)
            return 1
        print("扫描完成：%d 个资产\n" % len(reg.get("assets") or {}))
    assets = reg.get("assets") or {}
    # 关键修正（2026-10-19）：registry 是 scan 时的快照，可能很旧。
    # 实测事故：registry 停在 10-17，里面记 dsh-workbuddy-connect 0.7.4，
    # 而 profile 实际已 0.7.8 —— 体检拿旧数据当现状，误报「落后，建议升级」。
    # 所以这里实时读 node_modules 里的 package.json，覆盖 registry 的 version。
    stale_versions = []
    for name, s in assets.items():
        live = live_version(reg.get("root"), name)
        if live and s.get("version") and live != s.get("version"):
            stale_versions.append((name, s["version"], live))
            s["version"] = live
        elif live and not s.get("version"):
            s["version"] = live
    bl = load_json(os.path.join(DATA, "baseline-results.json"), {"results": []}) or {}
    rows = bl.get("results") or []
    active = reg.get("active_model")

    # registry 过旧就重扫一次：它只是 scan 时的快照，而「装了哪些插件」会变
    # （实测事故：快照停在 10-17，漏掉 dshmarket、版本也过期）。
    # 只在超过 1 小时没更新时重扫，避免每次都跑。
    import datetime as _dt
    need_rescan = False
    upd = reg.get("updated_at") or ""
    try:
        age_h = (_dt.datetime.now() - _dt.datetime.fromisoformat(upd)).total_seconds() / 3600.0
        need_rescan = age_h > 1.0
    except Exception:
        need_rescan = True
    if need_rescan and "--no-scan" not in argv:
        print(f"注册表快照较旧（{upd or '未知'}），先重扫一次…")
        try:
            import subprocess
            rs = subprocess.run(
                [sys.executable, os.path.join(HERE, "console.py"),
                 "--host", host, *root_arg, *out_arg, "scan"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=180)
            if rs.returncode == 0:
                reg = load_json(reg_path) or reg
                assets = reg.get("assets") or {}
                active = reg.get("active_model")
                print("重扫完成：%d 个资产\n" % len(assets))
        except Exception as e:
            print(f"[warn] 重扫失败，继续用旧快照：{e}\n")

    # 基线统计（只算当前模型的）
    base = {}
    model_unknown = active in ("v-unknown", "", None)
    other_models = {}      # 资产 -> {模型: {n,p}}，保留其他模型的历史
    for r in rows:
        if not model_unknown and r.get("model") and r["model"] != active:
            od = other_models.setdefault(r["skill"], {}).setdefault(
                r["model"], {"n": 0, "p": 0})
            od["n"] += 1
            od["p"] += 1 if r.get("passed") else 0
            continue
        d = base.setdefault(r["skill"], {"n": 0, "p": 0, "k": 0, "attrs": [], "model": r.get("model")})
        d["n"] += 1
        d["p"] += 1 if r.get("passed") else 0
        d["k"] = max(d["k"], r.get("k") or 1)
        if not r.get("passed") and r.get("attribution"):
            d["attrs"].append(str(r.get("attribution")).lower())

    # 模型变更检测（用户视角）：切了模型，以前测过的都不算数了，
    # 但用户未必知道 —— 主动说出来，并给出「怎么补测」。
    prev_models = sorted({m for d in other_models.values() for m in d})
    model_changed = bool(prev_models) and not model_unknown

    logs = None
    if not offline:
        cfg = load_json(os.path.join(DATA, "hosts.json"), {}) or {}
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

    # 批量查版本（带总预算），不再串行 N×15s
    latest_map = {}
    if not offline:
        latest_map = npm_latest_batch(list(assets.keys()))

    for name, s in sorted(assets.items()):
        installed = s.get("version")
        latest = latest_map.get(name)
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
                "status": s.get("status"),
                "others": other_models.get(name) or {}}

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

    # 用户视角的模型变更提示：切了模型，以前测过的都不算数了。
    if model_changed:
        print(f"\n{SYM_WARN} 检测到模型变更：之前测的是 {'、'.join(prev_models)}，"
              f"现在是 {active}")
        need = [n for n in assets if n not in base or not base[n]["n"]]
        print(f"   这 {len(need)} 个资产在新模型上**还没测过** —— 旧模型的通过率不能代表它")
        print("   在新模型上的表现。想补测（一条命令生成执行清单）：")
        print("     console.py baseline --from-seed")
        print("   ⓘ 旧模型上的判定已保留，切换回来还能看到（不会被清零）")

    def show(title, items, advice, suggest_alts=False):
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
            # 用户视角：「市场里有没有更好的」——只在真有问题时才问，避免噪声
            if suggest_alts and mkt:
                alts = find_alternatives(it["name"], mkt, it["installed"])
                if alts:
                    print("      ↳ 市场里同类的还有：")
                    for a in alts:
                        print("        · %-34s 安装 %-5s 增长 %-5s 更新 %s" % (
                            a["name"][:34], a["installs"], a["growth"], a["pushed"]))
                        if a["desc"]:
                            print("          %s" % a["desc"])
        print(f"  → {advice}")

    show(f"{SYM_WARN} 建议先修（跑不起来 / 通过率低）", buckets["fix"],
         "修配置或环境；修不好再考虑停用——别急着重写", suggest_alts=True)
    show(f"{SYM_UP} 建议升级（市场有新版）", buckets["upgrade"],
         "升级前看一眼 changelog 有没有 breaking change；升完 scan 一次", suggest_alts=True)
    show(f"{SYM_OK} 正常", buckets["ok"], "保持现状")
    show(f"{SYM_TEST} 未测（没有基线，无法判断好坏）", buckets["untested"],
         "这不是故障。想测就跑：console.py baseline --from-seed（seed 里有现成用例）")

    if not any(buckets.values()):
        print("\n  没有可报告的资产")

    # 覆盖度：把"查不了"和"查了没问题"分开说。
    # 这是最容易出错的地方——没数据时打"无异常"就是假绿灯。
    checked_version = sum(1 for it in
                          (buckets["upgrade"] + buckets["ok"] + buckets["fix"]
                           + buckets["untested"]) if it["latest"])
    checked_runtime = bool(logs)
    tested = len(buckets["ok"]) + len(buckets["fix"])
    print(f"\n{'='*70}")
    s1 = SYM_WARN
    print(f"体检覆盖度：版本 {checked_version}/{len(assets)}"
          f"  · 运行时 {'已读日志' if checked_runtime else '无日志可读'}"
          f"  · 有基线 {tested}/{len(assets)}")
    if not checked_version:
        print("  {s1} 版本面没查到任何数据（网络不可达？）—— 这不等于'已是最新'")
    if not checked_runtime:
        print("  {s1} 没有可读日志 —— 这不等于'运行正常'")
    if tested == 0:
        print("  {s1} 全都没有基线 —— 这不等于'都没问题'，只是没测过")
    print("口径：版本来自 npm registry（权威）；市场条目只作参考。")
    print("      版本落后 ≠ 该淘汰（可能是上游 breaking change 未适配）。")
    print("      没基线 ≠ 该删（只是没测过）。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
