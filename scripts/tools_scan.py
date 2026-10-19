#!/usr/bin/env python3
"""tools_scan.py — 从插件源码里扫出「它注册了哪些工具」。

为什么需要：能力路由要回答"用户说这件事，该调哪个工具"。
光知道插件名不够 —— Agent 还得知道工具名才能调。

扫法（多特征联合，宁可漏也别报错的名字）：
  ① DSH 插件标准的 tools.register({ name: "xxx" })
  ② 内联的 tool-names.json（browser-skill 这种：var session = "bsk_session"）
  ③ toolSpecs() 返回的字面量（hindsight 这种）
  ④ 全文字面量 xxx_yyy 且与包名相关的前缀（bsk_ / hindsight_）

**关键：只保留"像工具名"的标识符** —— 必须含下划线、长度合理、不是常见单词。
实测踩坑：单纯抓 name: 'xxx' 会抓到 jsdelivr / money1 / accept 这类噪音。
"""
import json
import os
import re

# 明显不是工具名的词（只用于兜底正则，不用于结构化匹配）
NOISE = {
    "accept", "true", "false", "null", "string", "number", "boolean", "object",
    "array", "default", "button", "input", "text", "title", "label", "value",
    "name", "type", "id", "url", "path", "file", "json", "html", "css",
    "error", "warn", "info", "debug", "trace",
}
# 像"状态/事件/内部标记"而非可调用工具的片段 —— 前缀兜底时剔除
NON_TOOL_PARTS = {
    "busy", "not_found", "injection", "refresh", "failed", "error", "ok",
    "started", "stopped", "done", "pending", "timeout", "denied", "skipped",
    "clicked", "changed", "loaded", "ready", "closed", "opened",
}

IDENT = re.compile(r"^[a-z][a-z0-9_]{4,60}$")


def _read(p, limit=4 * 1024 * 1024):
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def _js_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in
                       ("node_modules", ".git", "assets", "dist-images")]
        for f in filenames:
            if f.endswith((".js", ".mjs", ".cjs")):
                yield os.path.join(dirpath, f)


def scan_tools(plugin_dir):
    """返回 (tools, reasons)。tools 是排好序的工具名列表。

    reasons 说明"靠哪条规则找到的"，便于排查误报。
    """
    if not os.path.isdir(plugin_dir):
        return [], []
    tools, reasons = set(), []

    # 包名前缀（用于④的相关性判断）：@scope/name-xxx -> name
    base = os.path.basename(plugin_dir.rstrip("/\\"))
    pkg = os.path.join(plugin_dir, "package.json")
    try:
        with open(pkg, encoding="utf-8-sig") as fh:
            base = (json.load(fh).get("name") or base).split("/")[-1]
    except Exception:
        pass
    stem = re.split(r"[-_]", base.lower())[0]

    for path in _js_files(plugin_dir):
        text = _read(path)
        if not text:
            continue

        # ① tools.register({ name: "xxx" }) —— 最可靠的信号
        # 注意 {0,400} 太窄：hindsight 的 register 块里夹着 execute()，name 在很后面。
        # 改成"从 tools.register 起点往后 2000 字符内找第一个 name 字面量"。
        for m in re.finditer(r"tools(?:\?)?\.register\s*\(", text):
            seg = text[m.start():m.start() + 2000]
            nm_ = re.search(r"name\s*:\s*['\"]([a-z][a-z0-9_]{4,60})['\"]", seg)
            if nm_:
                n = nm_.group(1)
                if IDENT.match(n) and n not in NOISE:
                    tools.add(n)
        if "tools.register" in text:
            reasons.append("tools.register@%s" % os.path.basename(path))

        # ③ toolSpecs 附近的 name 字面量
        i = text.find("toolSpecs")
        if i > 0:
            seg = text[i:i + 4000]
            for m in re.finditer(r"name\s*:\s*['\"]([a-z][a-z0-9_]{4,60})['\"]", seg):
                n = m.group(1)
                if IDENT.match(n) and n not in NOISE:
                    tools.add(n)
            reasons.append("toolSpecs@%s" % os.path.basename(path))

        # ② 内联 tool-names.json：var x = "prefix_yyy"
        if "tool-names" in text:
            for m in re.finditer(r"var\s+\w+\s*=\s*['\"]([a-z][a-z0-9_]{4,60})['\"]", text):
                n = m.group(1)
                if IDENT.match(n) and n not in NOISE:
                    tools.add(n)
            reasons.append("tool-names.json@%s" % os.path.basename(path))

        # ⑤ 关键兜底：**带命名空间前缀的字面量**。
        # 插件常用 `<name>_<action>` 命名（bsk_page / hindsight_reflect），
        # 而且 name 字段常是变量（name: spec.name），只能靠字面量反推。
        # 判据：出现 ≥2 次，且前缀与包名首词相近。
        prefixed = {}
        for m in re.finditer(r"['\"]([a-z][a-z0-9]{1,18}_[a-z0-9_]{2,40})['\"]", text):
            n = m.group(1)
            if n in NOISE or not IDENT.match(n):
                continue
            pfx = n.split("_")[0]
            prefixed.setdefault(pfx, set()).add(n)
        for pfx, names in prefixed.items():
            if len(names) < 2:
                continue
            # 前缀必须与包名**首词**高度相关。
            # 之前用包含关系（pfx[:2] in stem）太宽：zod 的 invalid_* / 内部 inject_*
            # 都被当成工具。改成：前缀是包名首词的前缀，或包名首词是它的前缀。
            if not (stem.startswith(pfx) or pfx.startswith(stem[:max(3, len(pfx) - 2)])):
                continue
            # 剔除状态/事件类标识（session_busy、xxx_injection 不是可调用工具）
            clean = {n for n in names
                     if not (set(n.split("_")[1:]) & NON_TOOL_PARTS)}
            tools.update(clean)
            if clean:
                reasons.append("prefix:%s@%s" % (pfx, os.path.basename(path)))

        # ④ 兜底：与包名相关的下划线标识符（至少出现 2 次，避免偶发字符串）
        # 注意：这里的 "not tools" 必须看**本文件**是否还没贡献过工具名，
        # 不能看全局 tools —— 否则前一个文件命中后，后续文件的结构化匹配就被跳过
        # （实测：hindsight 的 tools.register 因前面文件先塞了字面量而丢失）。
        if True:
            cands = {}
            for m in re.finditer(r"['\"]([a-z][a-z0-9]{1,20}_[a-z0-9_]{2,40})['\"]", text):
                n = m.group(1)
                if n in NOISE or not IDENT.match(n):
                    continue
                cands[n] = cands.get(n, 0) + 1
            for n, c in cands.items():
                if c >= 2 and (stem[:3] in n or n.split("_")[0] == stem):
                    tools.add(n)
            if cands:
                reasons.append("literal@%s" % os.path.basename(path))

    return sorted(tools), reasons


def describe(tool_name):
    """把工具名翻译成人话（给 find 的输出用）。"""
    parts = [p for p in tool_name.split("_") if p]
    return " / ".join(parts)
