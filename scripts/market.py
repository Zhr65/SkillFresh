#!/usr/bin/env python3
"""market.py — 从可现实访问的插件/skill 市场找候选。

为什么单独一个文件：这是唯一会碰网络的部分，和"本地判定"职责不同；
拆开也让 console.py 不再膨胀（它已经 1750+ 行）。

数据源实测（2026-10-19，本机）：
  · DSH 插件目录  https://deepseek1024.com/api/v2/plugins   ✅ 通（13810 条）
  · SkillHub      https://lightmake.site/api/v1/search      ✅ 通（中文可搜）
  · GitHub API                                              ❌ 超时（需加速器）
  · ghfast 等镜像                                            ❌ 不通

**关于 stars 的重要口径**：DSH 目录里的 stars 是**上游源码仓库**的，
不是插件本身的热度（实测：dsh-runtime 101755 stars，自身 installCount 仅 73）。
所以排序**不用 star**，用 installCount + growth7d + 最近提交时间。
输出里也必须显式标注这一点，否则会误导用户。

失败时一律如实报错，不返回空列表假装"没有结果"。
"""
import datetime
import json
import urllib.parse
import urllib.request

UA = {"User-Agent": "skill-auditor/0.5.0"}
DSH_MARKET = "https://deepseek1024.com/api/v2/plugins"
SKILLHUB_SEARCH = "https://lightmake.site/api/v1/search?q=%s&limit=%d"
TIMEOUT = 20


def _fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _text(v):
    if isinstance(v, dict):
        return v.get("zh") or v.get("en") or ""
    return str(v or "")


def _days_since(iso):
    if not iso:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.timezone.utc)
        return max(0, (now - dt).days)
    except Exception:
        return None


def score(entry):
    """复合分：真实安装量 + 近期增长 + 活跃度。**不含 star**。"""
    installs = _int(entry.get("installCount") if "installCount" in entry else entry.get("installs"))
    growth = _int(entry.get("growth7d"))
    days = _days_since(entry.get("pushedAt") or entry.get("updatedAt") or entry.get("updated_at"))
    fresh = 0.0 if days is None else max(0.0, (30 - days) / 30.0)
    return min(installs, 500) / 500.0 * 2.0 + min(growth, 2000) / 2000.0 + fresh * 1.5


def search_dsh(term, limit=8, scan=300):
    """搜 DSH 插件目录。返回 (meta, hits)。"""
    data = _fetch(DSH_MARKET + "?limit=%d" % scan)
    items = data.get("plugins") or []
    meta = {"total": data.get("total"), "generatedAt": data.get("generatedAt"),
            "source": data.get("source"), "scanned": len(items)}
    t = (term or "").lower().strip()
    if not t:
        return meta, []
    hits = [p for p in items
            if t in (p.get("name") or "").lower()
            or t in _text(p.get("description")).lower()
            or t in (p.get("category") or "").lower()]
    return meta, sorted(hits, key=score, reverse=True)[:limit]


def search_skillhub(term, limit=8):
    """搜 WorkBuddy SkillHub。返回列表（可能为空）。"""
    url = SKILLHUB_SEARCH % (urllib.parse.quote(term or ""), limit)
    data = _fetch(url)
    res = data.get("results") if isinstance(data, dict) else data
    return sorted(res or [], key=score, reverse=True)[:limit]


def render_dsh(term, limit=6, out=print):
    try:
        meta, hits = search_dsh(term, limit)
    except Exception as e:
        out("  [err] DSH 市场不可达：%s" % e)
        out("        （本机需要代理才能访问部分外网；失败时如实报，不编造候选）")
        return 0
    stale = "  ⚠️ 非实时快照" if meta.get("source") == "stale" else ""
    out("  DSH 插件市场：目录 %s 条，已扫 %s 条，数据生成于 %s%s"
        % (meta.get("total"), meta.get("scanned"),
           (meta.get("generatedAt") or "")[:19], stale))
    if not hits:
        out("  没有命中（换更短的关键词，或试 SkillHub）")
        return 0
    for p in hits:
        days = _days_since(p.get("pushedAt"))
        out("\n  ▸ %s  [%s]" % (p.get("name"), p.get("category") or "-"))
        out("    %s" % _text(p.get("description"))[:120])
        out("    安装=%s  7日增长=%s  最后提交=%s"
            % (p.get("installCount"), p.get("growth7d"),
               "%s天前" % days if days is not None else "?"))
        out("    ⓘ 上游项目 stars=%s（源码仓库热度，不是本插件安装量）" % p.get("stars"))
        if p.get("install"):
            out("    安装命令：%s" % p["install"])
    return len(hits)


def render_skillhub(term, limit=6, out=print):
    try:
        hits = search_skillhub(term, limit)
    except Exception as e:
        out("  [err] SkillHub 不可达：%s" % e)
        return 0
    if not hits:
        out("  SkillHub 没有命中")
        return 0
    for it in hits:
        dz = it.get("description_zh")
        if isinstance(dz, str) and dz.strip().startswith("{"):
            try:
                dz = json.loads(dz).get("answer", "")
            except Exception:
                pass
        out("\n  ▸ %s  [%s]" % (it.get("slug") or it.get("name"), it.get("category") or "-"))
        out("    %s" % str(dz or it.get("description") or "")[:120])
        out("    downloads=%s  installs=%s  stars=%s  版本=%s"
            % (it.get("downloads"), it.get("installs"), it.get("stars"), it.get("version")))
        if it.get("homepage"):
            out("    页面：%s" % it["homepage"])
    return len(hits)


HOW_TO_READ = [
    "怎么读这些数字（别只看 star）：",
    "  · 安装量   = 真正装过它的人，最硬",
    "  · 7日增长 = 近期热度，涨得快的常是 demo/教程",
    "  · 最后提交 = 还活着吗（半年没动的慎装）",
    "  · stars    = ⚠️ DSH 目录标的是上游源码仓库热度，不是插件热度",
    "  真正该看的：装进来 → scan → 建 golden → 跑基线，用数据比",
]


def main(argv):
    if not argv:
        print("usage: market.py <关键词> [--market dsh|skillhub|both] [--limit N]")
        return 2
    term = argv[0]
    market = argv[argv.index("--market") + 1] if "--market" in argv else "both"
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 6
    if market in ("dsh", "both"):
        print("\n=== DSH 插件市场 · 「%s」 ===" % term)
        render_dsh(term, limit)
    if market in ("skillhub", "both"):
        print("\n=== SkillHub（WorkBuddy skill）· 「%s」 ===" % term)
        render_skillhub(term, limit)
    print()
    for line in HOW_TO_READ:
        print(line)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
