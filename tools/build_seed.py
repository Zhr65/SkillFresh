#!/usr/bin/env python3
"""生成通用 skill 的纯净 seed case（不依赖图片/本地路径/账号）。

原则：
  · 不引用图片、不给本机绝对路径、不带账号信息；
  · expected 必须**可不看输出就复现**（说了什么/做了什么/产出在哪）；
  · judge 尽量客观（可核验），避免文风类主观判断；
  · 宁少而准，不多而松。

用法：python tools/build_seed.py（在 skill-auditor 根目录执行）
"""
import glob
import io
import json
import os
import re
import sys

CASES = {
    "github": [
        {
            "id": "gh-10",
            "input": "用 gh 列出 ithtelab/workbuddy-manager 这个仓库最近 5 个 release 的版本号",
            "expected": "给出该仓库最近 5 个 release 的版本号列表（或明确说明查不到/无 release）；不编造版本号",
            "judge": "版本号可逐一回查；查不到时明确说查不到而不是虚构",
        },
        {
            "id": "gh-11",
            "input": "帮我在 GitHub 上找一个用 Python 写的 CLI 剪贴板工具，要求 star 数别太低，给我三个候选",
            "expected": "给出 3 个真实存在的候选（owner/repo），各自说明为什么符合条件；不编造仓库",
            "judge": "三个仓库都真实存在；没有候选时说明没找到而非凑数",
        },
    ],
    "writing-skills": [
        {
            "id": "ws-10",
            "input": "帮我写一个新的 skill，作用是「把会议记录整理成待办清单」",
            "expected": "先追问/澄清必要信息（输入格式、输出格式、是否分类），再按最小结构产出 SKILL.md 草稿；不直接堆一长份文档",
            "judge": "是否澄清关键歧义；SKILL.md 是否含 frontmatter（name/description）+ 触发条件 + 工作步骤",
        },
        {
            "id": "ws-11",
            "input": "我这个 skill 的 description 写什么好？它是用来「查 GitHub 上仓库信息」的",
            "expected": "给出具体可用的 description 文案，且包含触发词（如「查 GitHub」「仓库信息」），说明为什么这样写",
            "judge": "是否给出可直接粘贴的文本；是否说明触发词设计理由；不是泛泛说「写清楚点」",
        },
    ],
    "humanizer": [
        {
            "id": "hu-10",
            "input": "这段话帮我去掉 AI 味：「综上所述，本方案不仅奠定了坚实的基石，更为未来的发展注入了强劲的动力，具有里程碑式的重大意义。」",
            "expected": "去掉空泛吹捧（基石/里程碑/强劲动力），替换为具体内容；若无具体事实可写，就指出'原文没有可保留的实质信息'",
            "judge": "是否清除空洞修辞；是否不编造事实；是否保留原意",
        },
        {
            "id": "hu-11",
            "input": "把这段改自然点：「In order to achieve this goal, it is important to note that the platform has the ability to scale.」",
            "expected": "压缩冗词（In order to / it is important to note / has the ability to），给出更短更直接的说法",
            "judge": "是否明显变短；是否语义不变；不是同义替换式的敷衍",
        },
    ],
    "frontend-dev": [
        {
            "id": "fe-10",
            "input": "我有一个按钮，hover 时背景色不变，帮我写个最小复现（只要 HTML+CSS，三行以内）",
            "expected": "给出可运行的最小 HTML+CSS 片段，且 hover 规则确实会触发背景色变化",
            "judge": "代码可直接保存为 .html 打开验证；hover 变化是否真的生效（不是写了 :hover 但选择器错）",
        },
        {
            "id": "fe-11",
            "input": "flex 布局里怎么让一个元素左右分开、垂直居中？写个最小例子",
            "expected": "给出最小 HTML+CSS，使用 justify-content: space-between（或等价）+ align-items: center",
            "judge": "是否实现两件事（左右分开 + 垂直居中）；代码是否最小可运行",
        },
    ],
    "brainstorming": [
        {
            "id": "br-10",
            "input": "我想做个给程序员用的周报生成器，帮我想想可以做成什么样",
            "expected": "给出多个差异化方向（每个说清目标用户、输入输出、与常见方案的区别），并指出哪些假设需要用户确认",
            "judge": "方向数量≥3 且彼此不同；是否区分'已验证'与'猜测'；没有把猜测说成事实",
        },
    ],
    "systematic-debugging": [
        {
            "id": "sd-10",
            "input": "我的 Python 脚本本地跑没问题，服务器上报 ModuleNotFoundError，怎么系统地查？",
            "expected": "给出可执行的排查顺序（依赖清单对比/Python 与 venv 版本/路径/大小写等），而不是直接猜一个原因",
            "judge": "是否可复现地逐步排错；是否指出如何确认而非只给可能性列表",
        },
        {
            "id": "sd-11",
            "input": "服务偶发地一天卡死一次，重启就好，日志里看不到报错。这种情况怎么查？",
            "expected": "提出针对'无日志线索'的排查手段（如采样/转储/资源监控/加埋点复现），明确说明这是可观测性缺失问题",
            "judge": "是否指出'没日志'本身是问题；是否给出最小改进方案（先加什么埋点）",
        },
    ],
    "test-driven-development": [
        {
            "id": "td-10",
            "input": "我要写个函数判断字符串是不是回文，教我按 TDD 走一遍",
            "expected": "先写失败测试，再写最小实现让它通过，再谈边界（大小写/空格/空串）；顺序不能反",
            "judge": "是否先测试后实现；是否提到最小实现；是否覆盖边界",
        },
    ],
    "verification-before-completion": [
        {
            "id": "vb-10",
            "input": "我改完了，你看下有没有问题",
            "expected": "不直接下结论；要求/执行验证（跑测试、给证据、贴输出），再报告'通过/未通过'；区分'确认的问题'与'未验证的猜测'",
            "judge": "是否给出可核验证据；是否把未经证实的说法标为猜测；没有把'应该没问题'当结论",
        },
    ],
    "executing-plans": [
        {
            "id": "ep-10",
            "input": "这个计划第 3 步和数据库schema不符，我该怎么处理？",
            "expected": "停下来并说明阻碍（不硬做）；给出可选处理（改计划/改 schema/跳过），让用户决定；不静默偏离计划",
            "judge": "是否先报告不符点；是否给出选项而非自作主张；没有假装按计划完成",
        },
    ],
    "dispatching-parallel-agents": [
        {
            "id": "dp-10",
            "input": "我有三个互不依赖的查询任务，能并行吗？",
            "expected": "判断可并行（无共享状态/无顺序依赖），说明并行边界；若任务间有依赖则指出并给出理由",
            "judge": "是否正确识别独立性；若结论是'不可并行'，理由是否成立",
        },
    ],
}

PRIVATE = re.compile(r"zhr15|xwechat|[A-Za-z]:\\+[Uu]sers|@image", re.I)


def main():
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dry = "--dry-run" in sys.argv
    idx_path = os.path.join("seed", "index.json")
    index = json.load(io.open(idx_path, encoding="utf-8")) if os.path.exists(idx_path) else {"assets": {}}

    added = 0
    for skill, cases in CASES.items():
        for c in cases:
            blob = json.dumps(c, ensure_ascii=False)
            if PRIVATE.search(blob):
                print("[skip] %s/%s 含隐私片段" % (skill, c["id"]))
                continue
        target = os.path.join("seed", "%s.json" % skill)
        doc = json.load(io.open(target, encoding="utf-8")) if os.path.exists(target) else {
            "skill": skill, "seed": True,
            "note": "community seed — judge criteria are a starting point, not authority",
            "cases": [],
        }
        have = {c["id"] for c in doc["cases"]}
        for c in cases:
            if c["id"] in have:
                continue
            doc["cases"].append(dict(c))
            added += 1
        if not dry:
            with io.open(target, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=2)
        index["assets"][skill] = {"cases": len(doc["cases"]), "file": os.path.basename(target)}
        print("%-26s %3d case(s)" % (skill, len(doc["cases"])))

    index["schema_version"] = 1
    index["cases"] = sum(a["cases"] for a in index["assets"].values())
    index["asset_count"] = len(index["assets"])
    if not dry:
        with io.open(idx_path, "w", encoding="utf-8") as fh:
            json.dump(index, fh, ensure_ascii=False, indent=2)
    print("\nadded=%d assets=%d cases=%d%s"
          % (added, len(index["assets"]), index["cases"], " (dry-run)" if dry else ""))


if __name__ == "__main__":
    main()
