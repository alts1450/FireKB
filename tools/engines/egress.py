# -*- coding: utf-8 -*-
"""
数据出境声明与显式同意
======================
为什么需要它：
  把重活交给云端 API 会带来一类**软件许可管不到**的风险 —— 教材/规范/课堂录音是第三方版权物，
  录音里还有讲课人的表达甚至学生声音。"技术上能上传"与"有权上传"是两件事。
  项目的正确姿势是：**默认不出境；出境必须显式同意；同意前先把要出去的东西列清楚。**

机制：
  · 每台引擎在 EngineSpec 里自报 egress 级别（none / page_text / page_images / audio / document …）
  · `manifest()` 汇总「将要离开本地机器的数据」；`require_consent()` 在开工前把关
  · 同意落盘 `00_配置/.egress-consent.json`（**仅存本地，绝不入库**；也可用命令行 --allow-egress 临时放行）
  · 全部引擎都是 none 时，本模块是**空操作**——所以对现有本地流程零影响
"""
from __future__ import annotations

import json
import time
from pathlib import Path

# 出境级别 → 人读说明（越靠下越敏感）
EGRESS_LEVELS = {
    "none": "不出本机（全部在本机完成）",
    "page_text": "把页级文本发往第三方服务（不含原始文件）",
    "transcript": "把转写文本发往第三方服务（不含原始音频）",
    "personal": "把**个性化数据**发往第三方服务（复习产物/笔记/错题/作答记录等）",
    "page_images": "把**页面图像**发往第三方服务（含版面与图表信息）",
    "audio": "把**音频**发往第三方服务（含讲课人声音，可能含学生声音）",
    "document": "把**原始素材文件整体**发往第三方服务（教材/规范原文）",
}

# ---------------------------------------------------------------------------
# 绝对禁止出境的级别
# ---------------------------------------------------------------------------
# 「个性化数据绝不出境」——**同意也不能放行**。
#
# 为什么要有这一层，而不是只靠"同意"管理：
#   同意机制回答的是"你有没有**权利**上传"（版权、讲课人权益）——
#   那是一个**可以由人授权**的问题。
#   而个性化数据（课堂录音、转写、复习产物、笔记、作答记录）回答的是另一个问题：
#   它记录的是**这个人自己的学习痕迹**，与"有没有权利"无关 ——
#   一旦出境就无法收回，也不该由一次 --allow-egress 按钮决定。
#   ⇒ 所以它必须是**硬规则**：consent 文件写了也不放行，--allow-egress 也不放行。
#
# 判定归属：
#   audio / transcript / personal   → 个性化（硬禁止）
#   document / page_text / page_images → 第三方出版内容（**可以**显式同意后出境）
NEVER_EGRESS = {"audio", "transcript", "personal"}


def is_never(level: str) -> bool:
    """该出境级别是否属于"绝不出境"（同意也无法放行）。"""
    return level in NEVER_EGRESS


def never_engines(engines: dict) -> list[dict]:
    """返回配置中**绝对禁止出境**的引擎（正常情况下应为空；非空即必须在开工前拦下）。"""
    return [r for r in manifest(engines) if is_never(r["egress"])]


def manifest(engines: dict) -> list[dict]:
    """汇总当前选用引擎的出境情况。engines = {kind: BaseEngine}"""
    rows = []
    for kind, eng in engines.items():
        spec = eng.spec
        rows.append({
            "kind": kind,
            "engine": spec.name,
            "label": spec.label,
            "egress": spec.egress,
            "meaning": EGRESS_LEVELS.get(spec.egress, f"未知级别 {spec.egress}"),
            "needs_network": spec.needs_network,
            "note": spec.note,
        })
    return rows


def any_egress(engines: dict) -> bool:
    return any(e.spec.egress != "none" for e in engines.values())


def consent_file(config_dir) -> Path:
    return Path(config_dir) / ".egress-consent.json"


def load_consent(config_dir) -> dict:
    p = consent_file(config_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def grant(config_dir, engines: dict, note: str = "") -> Path:
    """
    记录同意（人工执行；工具不会自动写）。

    ⚠️ **不会**为 NEVER_EGRESS 的引擎记录同意（个性化数据绝不出境，同意也无效）。
    这里静默跳过是刻意的：允许写反倒会让人以为"已经批过了"。
    """
    p = consent_file(config_dir)
    data = load_consent(config_dir)
    data.setdefault("granted", {})
    for kind, eng in engines.items():
        if eng.spec.egress == "none" or is_never(eng.spec.egress):
            continue
        data["granted"][f"{kind}:{eng.spec.name}"] = {
            "egress": eng.spec.egress, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "note": note,
        }
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def require_consent(engines: dict, config_dir, allow_flag: bool = False) -> None:
    """
    开工前把关：有出境且未获同意 → **fail-loud**（打印清单 + 给出两种放行方式），绝不静默继续。

    两段式：
      ① **个性化数据（audio/transcript/personal）→ 无条件拒绝**，
         不查同意文件、不看 --allow-egress —— 这是硬规则，不是授权问题；
      ② 第三方出版内容（document/page_text/page_images）→ 走原有的同意机制。
    """
    # ---- ① 硬规则：个性化数据绝不出境（先于一切检查）----
    bad = never_engines(engines)
    if bad:
        lines = [
            "",
            "=" * 84,
            "[STOP] 配置里有**个性化数据出境**的引擎 —— 无条件拒绝（这是硬规则）。",
            "=" * 84,
            "涉及：",
        ]
        for r in bad:
            lines.append(f"  · {r['kind']:>6} {r['engine']:<24} 出境级别：{r['egress']}")
            lines.append(f"         含义：{r['meaning']}")
        lines += [
            "",
            "为什么连同意也不行：",
            "  · 同意机制回答的是「你有没有**权利**上传」（版权 / 讲课人权益）——那可以授权；",
            "  · 个性化数据（课堂录音、转写、复习产物、笔记、作答记录）回答的是另一个问题：",
            "    它记录的是**你本人的学习痕迹**，一旦出境无法收回，",
            "    不该由一次 --allow-egress 或一个 consent 文件决定。",
            "  ⇒ 本项目对该类数据一律**不出本机**。",
            "",
            "处置：把 00_配置\\engines.json 里对应环节改回本地引擎（egress=none）。",
            "=" * 84,
            "",
        ]
        raise SystemExit("\n".join(lines))

    rows = manifest(engines)
    risky = [r for r in rows if r["egress"] != "none"]
    if not risky:
        return
    granted = load_consent(config_dir).get("granted", {})
    pending = [r for r in risky if f"{r['kind']}:{r['engine']}" not in granted]
    if not pending or allow_flag:
        return

    lines = [
        "",
        "=" * 84,
        "[STOP] 本次运行会把数据送出本机，且**没有取得同意** —— 拒绝继续。",
        "=" * 84,
        "将要离开本机的数据：",
    ]
    for r in pending:
        lines.append(f"  · {r['kind']:>6} {r['engine']:<24} 出境级别：{r['egress']}")
        lines.append(f"         含义：{r['meaning']}")
        if r.get("note"):
            lines.append(f"         说明：{r['note']}")
    lines += [
        "",
        "=" * 84,
        "【书面禁令】",
        "  禁止直接将**未获得版权的书目**发往云端。违反者**后果自负**。",
        "  —— 本工具**不做技术限制**（上述放行方式照常有效）：是否上传由使用人判断与承担，",
        "     项目只在此**书面**声明禁止，并留下这条记录的痕迹。",
        "=" * 84,
        "",
        "⚠️ 你自己的确认责任（项目替不了你）：教材/规范是第三方版权物，",
        "   课堂录音含讲课人表达、可能含学生声音。请先自行确认你对这些素材**有权上传**。",
        "",
        "两种放行方式：",
        "  1) 本次临时放行：给命令加 --allow-egress",
        "  2) 长期同意：运行 python tools\\engines\\egress.py --grant \"<你自己的确认备注>\"",
        "",
        "若希望完全不出境：把 00_配置\\engines.json 改回本地引擎（egress=none）。",
        "=" * 84,
        "",
    ]
    raise SystemExit("\n".join(lines))


def main() -> int:
    import argparse
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import kb  # 共享层：提供 ROOT/CONFIG_DIR（不重复定义常量）

    ap = argparse.ArgumentParser(description="数据出境声明与同意")
    ap.add_argument("--show", action="store_true", help="列出当前引擎配置与出境情况")
    ap.add_argument("--grant", metavar="备注", help="记录同意（需你确认有权上传）")
    args = ap.parse_args()

    from engines import all_engines
    engs = all_engines()
    rows = manifest(engs)
    print("=" * 84)
    print("引擎配置与数据出境        配置文件：%s" % (Path(kb.CONFIG_DIR) / "engines.json"))
    print("=" * 84)
    for r in rows:
        print("  %-6s %-24s 出境=%-11s %s" % (r["kind"], r["engine"], r["egress"], r["label"] or ""))
        print("         %s" % r["meaning"])
        if r.get("note"):
            print("         说明：%s" % r["note"])
    if args.grant:
        never = never_engines(engs)
        p = grant(kb.CONFIG_DIR, engs, args.grant)
        print("\n已记录同意 → %s" % p)
        print("（该文件为本机私有，已在 .gitignore 中排除）")
        if never:
            print("\n[注意] 以下引擎属于**个性化数据出境**，同意也**不会**生效（硬规则）：")
            for r in never:
                print("   · %s %s（%s）" % (r["kind"], r["engine"], r["egress"]))
    if not any_egress(engs):
        print("\n结论：当前全部引擎都在本机执行，没有数据出境。")
    else:
        never = never_engines(engs)
        if never:
            print("\n[STOP] 当前配置含**个性化数据出境**引擎：%s" % ", ".join(r["engine"] for r in never))
            print("       个性化数据绝不出境 —— 请改回本地引擎。")
        else:
            print("\n结论：出境项均为第三方出版内容，需显式同意后才可运行。")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
