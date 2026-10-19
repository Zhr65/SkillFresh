#!/usr/bin/env python3
"""生成"插件类型判据模板"+ 三个已装 DSH 插件的可用 golden。

为什么需要这个：
  bootstrap 起草的插件 golden，input 多半是关键词误匹配（比如给浏览器插件
  配了"我想让不同 agent 都能用"这种设计讨论），expected 还是 TODO 占位。
  光套判据没用 —— input 本身就该重写。

插件判据模板（按类型）：
  插件的价值 = 它的工具**真的能调通并返回可用结果**，而不是"agent 能谈它"。
  所以判据围绕三件事：
    1) 是否真的调用了工具（而非凭印象回答）
    2) 返回值是否可用（非空、结构正确、含关键字段）
    3) 失败时是否给出可操作的错误（而不是编造成功）

用法：python tools/build_plugin_golden.py
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 判据模板：按插件能力类型给通用但可判的标准（template: true 会标在文件里）
TEMPLATES = {
    "browser-automation": {
        "judge": "是否真的通过 bsk_* 工具执行了浏览器操作（打开/读取/点击任一步），"
                 "且回报的结果来自工具返回而非凭空描述；工具不可用时是否明确说明失败原因",
        "expected": "调用浏览器工具完成操作并返回可核对的结果（页面标题/URL/文本片段）；"
                    "若 bsk 守护进程未就绪，明确报出'daemon 未启动'而不是假装成功",
    },
    "memory": {
        "judge": "（1）是否检索到了与此前会话相关的记忆内容；（2）引用记忆时是否说明来源"
                 "（会话/时间/主题）；（3）没有相关记忆时是否明确说没有，而不是编造",
        "expected": "给出与本次问题相关的历史记忆摘要，并标注来源；无匹配记忆时明确回答'没有相关记录'",
    },
    "model-connector": {
        "judge": "是否说明了可用的模型来源及如何切换（不需要用户手工配 key 即为达标）；"
                 "无法连接时是否给出具体失败点（端点/凭证/网络）而非笼统报错",
        "expected": "说明当前可用的模型列表与选择方式；若连接失败，指出失败环节并可复现",
    },
}

# 三个已装插件：重写 input（真实任务，不是关键词误匹配）+ 套对应类型模板
PLUGINS = {
    "@wxg-prc-cpg/browser-skill-dsh-plugin": {
        "kind": "browser-automation",
        "cases": [
            {"id": "bsk-01",
             "input": "用浏览器工具打开 https://example.com 并告诉我页面标题",
             "type": "tool-call"},
            {"id": "bsk-02",
             "input": "打开 https://example.com 读一下页面上的第一段正文",
             "type": "tool-call"},
            {"id": "bsk-03",
             "input": "用浏览器工具访问 http://127.0.0.1:1 这个不存在的地址，把报错告诉我",
             "type": "error-path"},
        ],
    },
    "@vectorize-io/hindsight-coding-agents": {
        "kind": "memory",
        "cases": [
            {"id": "hs-01",
             "input": "我们之前是不是讨论过这个项目的发布流程？帮我回忆一下结论",
             "type": "memory-recall"},
            {"id": "hs-02",
             "input": "记得我上次说的那个 skill 评测工具吗，它现在叫什么名字",
             "type": "memory-recall"},
            {"id": "hs-03",
             "input": "回忆一下我们有没有聊过完全不存在的话题：火星养猪场审批流程",
             "type": "memory-negative"},
        ],
    },
    "dsh-workbuddy-connect": {
        "kind": "model-connector",
        "cases": [
            {"id": "wbc-01",
             "input": "现在有哪些可用模型？它们分别来自哪里？",
             "type": "capability"},
            {"id": "wbc-02",
             "input": "我想换一个模型继续这个任务，该怎么做？",
             "type": "capability"},
            {"id": "wbc-03",
             "input": "如果 WorkBuddy 桌面端没运行，这个插件还能连上模型吗？连不上会怎样？",
             "type": "error-path"},
        ],
    },
}


def main():
    os.chdir(ROOT)
    dry = "--dry-run" in sys.argv
    # 1) 写模板文件（给贡献者/其他插件复用）
    os.makedirs(os.path.join("seed", "templates"), exist_ok=True)
    for kind, t in TEMPLATES.items():
        p = os.path.join("seed", "templates", "plugin-%s.json" % kind)
        doc = {
            "template": True,
            "kind": kind,
            "note": "插件判据模板：input 仍要按具体插件重写；expected/judge 可作为该类插件的起点",
            "expected": t["expected"],
            "judge": t["judge"],
        }
        if not dry:
            with io.open(p, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=2)
        print("template -> %s" % p)

    # 2) 重写三个插件的 golden（真实 input + 类型判据）
    for skill, spec in PLUGINS.items():
        kind = spec["kind"]
        tpl = TEMPLATES[kind]
        cases = []
        for c in spec["cases"]:
            cases.append({
                "id": c["id"],
                "polarity": "positive" if c["type"] != "error-path" else "negative",
                "type": c["type"],
                "input": c["input"],
                "expected": tpl["expected"],
                "judge": tpl["judge"],
                "provenance": "plugin-template:%s" % kind,
                "template": True,
            })
        safe = skill.replace("@", "").replace("/", "_")
        p = os.path.join("golden", "%s.json" % safe)
        doc = json.load(io.open(p, encoding="utf-8")) if os.path.exists(p) else {}
        doc.update({
            "skill": skill,
            "draft": False,          # 不再是草稿：input/criteria 都是可判的
            "template": True,        # 但判据来自类型模板，不如手写精确
            "note": ("判据来自插件类型模板（%s），不是针对该插件手写 —— "
                     "作为起点可用，要当硬基线请自行复核" % kind),
            "cases": cases,
        })
        if not dry:
            with io.open(p, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=2)
        print("%-42s %d case(s) [%s]" % (skill, len(cases), kind))


if __name__ == "__main__":
    main()
