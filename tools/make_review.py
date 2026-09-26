# -*- coding: utf-8 -*-
"""
M6 · 复习产物生成
==================
把通过校验的知识点转成可直接用的复习材料，组织方式对应「先枝干、再脉络」的复习路径。

产出（写入 40_复习产物/）：
  00_待确认清单.md   未通过校验的内容（明确告诉你「这些不要背」）
  01_枝干速览.md     一页纸：课程 -> 章 -> 节 -> 知识点名称 + 重要度（先看这个）
  02_背诵卡.md       问答卡，带出处，可直接背
  03_数字对照表.md   把所有数值类知识点聚成一张表（消防工程最高效的复习产物）
  04_脉络展开.md     按章节展开完整定义 + 原文摘录（再看这个）
  05_anki导入.tsv    Anki 可直接导入的制表符格式
  cards.jsonl        结构化卡片（供后续程序使用）

用法：
  python tools\\make_review.py                 # 生成全部产物
  python tools\\make_review.py --course "防排烟工程"
  python tools\\make_review.py --level 4       # 只输出重要度 >= 4 的（考前突击）
  python tools\\make_review.py --include-pending  # 把未校验的也列出来（默认排除）
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(os.environ.get("FIREKB_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "tools"))

import kb  # noqa: E402

OUT = kb.REVIEW_DIR


def stars(n: int) -> str:
    n = max(1, min(5, int(n or 3)))
    return "★" * n + "☆" * (5 - n)


def short_src(kp: dict) -> str:
    sf = (kp.get("source_file") or "").split("/")[-1]
    sf = sf.split("(")[0].strip()[:34]
    a = kp.get("page_label_start") or ""
    b = kp.get("page_label_end") or ""
    rng = f"{a}" if a == b or not b else f"{a}–{b}"
    return f"{sf} {rng}".strip()


def page_num(kp: dict) -> int:
    """取「第 N 页」里的数字，用于稳定排序；解析不出给极大值（排到最后）。"""
    m = re.search(r"(\d+)", str(kp.get("page_label_start") or ""))
    return int(m.group(1)) if m else 10 ** 9


def cross_book_dedup(kps: list[dict], ev_by_kp: dict) -> tuple[list[dict], int]:
    """
    跨书精确同名去重 —— **只作用于复习产物，不动 kp.jsonl**。

    背景：「多书全抽」（一门课下的教材与课件全部入课）会让同一概念在多本书里各出一条
    同名知识点。数据层保留各书条目（可溯源最强、便于日后核对），产物层合成一条，
    避免背诵卡里同一概念重复出现多次。

    判据（**不得擅自放宽**）：
      * 只合并**同一门课内、名称去空白后完全相同**的条目；
        **不做模糊相似合并** —— 「轰燃」与「轰燃的判定」是不同知识点，不能合。
      * 保留哪条：证据里 quote_verified 条数多者 → 定义长着 → 页码早者 → kp_id 小者
        （四段判据全是确定值，结果可复现）。
      * 被合并条目的出处以 `alt_sources` 附注保留（书 / 页 / kp_id / 章 / 其数值），
        可溯源不丢；**不把它们的数值并进主条目**（避免误合时污染数值表）。
    返回 (去重后的列表, 被合并掉的条目数)。
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for k in kps:
        groups[((k.get("course") or ""), (k.get("name") or "").strip().casefold())].append(k)

    kept: list[dict] = []
    merged = 0
    for members in groups.values():
        if len(members) == 1:
            members[0].setdefault("alt_sources", [])
            kept.append(members[0])
            continue

        def rank(k: dict):
            n_ok = sum(1 for e in ev_by_kp.get(k.get("kp_id"), []) if e.get("quote_verified"))
            return (-n_ok, -len(k.get("definition") or ""), page_num(k), str(k.get("kp_id") or ""))

        ordered = sorted(members, key=rank)
        win = ordered[0]
        win["alt_sources"] = [{
            "kp_id": m.get("kp_id"),
            "chapter": m.get("chapter"),
            "source_file": m.get("source_file"),
            "page_label_start": m.get("page_label_start"),
            "page_label_end": m.get("page_label_end"),
            "numbers": m.get("numbers") or [],
        } for m in ordered[1:]]
        kept.append(win)
        merged += len(ordered) - 1
    return kept, merged


def alt_note(kp: dict) -> str:
    """把 alt_sources 渲染成一行「同义出处」附注；无附注返回空串。"""
    alts = kp.get("alt_sources") or []
    if not alts:
        return ""
    parts = []
    for a in alts:
        seg = f"{short_src(a)}　·　`{a.get('kp_id')}`"
        if a.get("numbers"):
            seg += f"（该条含数值：{'、'.join(str(x) for x in a['numbers'][:4])}）"
        parts.append(seg)
    return "**同义出处：** " + "；".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="M6 复习产物生成")
    ap.add_argument("--course", help="只输出某门课")
    ap.add_argument("--level", type=int, default=0, help="只输出重要度 >= N 的知识点（考前突击用）")
    ap.add_argument("--include-pending", action="store_true",
                    help="把未通过校验的知识点也列入正文（默认排除，只进待确认清单）")
    ap.add_argument("--include-experimental", action="store_true",
                    help="把已登记的实验批次知识点也列入正文（默认排除；见 00_配置/experimental-batches.json）")
    args = ap.parse_args()

    kps = kb.load_jsonl(kb.KP_FILE)
    evs = kb.load_jsonl(kb.EVIDENCE_FILE)
    audits = kb.load_jsonl(kb.AUDIT_FILE)
    skel = kb.load_json(kb.SKELETON_FILE, {}) or {}

    if not kps:
        print(f"[!] 知识点库为空：{kb.KP_FILE}")
        print("    请先运行 extract_kp.py 与 verify_kp.py")
        return 1

    # ---- 实验批次：**产物层排除，kp.jsonl 本体不动** ----
    # 为什么必须在产物层拦：这些 kp 的 status 是 verified，不拦就会静默进背诵卡，
    # 用户会把"试跑留下的 15 条样本"当成该课的完整考点背下来。
    exp_rows = [k for k in kps if kb.is_experimental_kp(k)]
    exp_ids = {k["kp_id"] for k in exp_rows if k.get("kp_id")}
    exp_batches = sorted({(kb.is_experimental_kp(k) or {}).get("id") for k in exp_rows} - {None})

    if args.course:
        kps = [k for k in kps if args.course in (k.get("course") or "")]
        ids = {k["kp_id"] for k in kps}
        evs = [e for e in evs if e.get("kp_id") in ids]
        audits = [a for a in audits if a.get("kp_id") in ids]

    if args.level:
        kps = [k for k in kps if int(k.get("importance") or 3) >= args.level]
        ids = {k["kp_id"] for k in kps}
        evs = [e for e in evs if e.get("kp_id") in ids]

    ev_by_kp: dict[str, list[dict]] = defaultdict(list)
    for e in evs:
        ev_by_kp[e.get("kp_id")].append(e)

    pending_ids = {k["kp_id"] for k in kps if k.get("status") == "pending"}
    # 人工复核判定不可用的（review_queue --apply 写 status=rejected）——
    # 必须排除，否则"人已经判过是错的"内容还会照样进背诵卡（那这个裁决就白做了）。
    rejected_ids = {k["kp_id"] for k in kps if k.get("status") == "rejected"}
    pending_audits = [a for a in audits if a.get("priority") == "P0"]

    usable = [k for k in kps if args.include_pending or k["kp_id"] not in pending_ids]
    excluded = [k for k in kps if k["kp_id"] in pending_ids and not args.include_pending]

    rej_excluded = [k for k in usable if k.get("kp_id") in rejected_ids]
    if rej_excluded:
        usable = [k for k in usable if k.get("kp_id") not in rejected_ids]
        print(f"[i] 已排除人工驳回（rejected）知识点 {len(rej_excluded)} 条")

    # 实验批次同样在产物层排除（默认排除；--include-experimental 才纳入）
    exp_excluded = []
    if exp_ids and not args.include_experimental:
        exp_excluded = [k for k in usable if k.get("kp_id") in exp_ids]
        usable = [k for k in usable if k.get("kp_id") not in exp_ids]
        print(f"[i] 已排除实验批次知识点 {len(exp_excluded)} 条"
              f"（批次 {', '.join(exp_batches)}）—— 它们不是正式产出，见 00_配置/experimental-batches.json")
        print(f"    要一并输出：加 --include-experimental")

    # ---- 跨书精确同名去重：只作用于产物层，kp.jsonl 本体不动 ----
    _before = len(usable)
    usable, merged_n = cross_book_dedup(usable, ev_by_kp)

    by_course: dict[str, list[dict]] = defaultdict(list)
    for k in usable:
        by_course[k.get("course") or "未分类"].append(k)

    OUT.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    scope = f"课程：{args.course}" if args.course else "全部课程"
    if args.level:
        scope += f"；仅重要度 ≥ {args.level}"

    written: list[tuple[str, int]] = []

    # ---------------------------------------------------------------- 00 待确认
    lines = [
        "# 待确认清单（这些内容**不要背**）", "",
        f"生成时间：{ts}　范围：{scope}", "",
        "> 本清单中的知识点未通过机器校验（引用在原文中找不到、数字对不上、定义缺失等）。",
        "> 在人工核对前，**不得用于背诵**。处理方式：打开对应文件的原页核对，",
        "> 确认无误后在知识点库中把 status 改为 verified，或驳回删除。", "",
    ]
    if pending_audits:
        lines.append(f"共 {len(pending_audits)} 条，按课程分组：")
        lines.append("")
        for course in sorted({a.get("course", "") for a in pending_audits}):
            lines.append(f"## {course}")
            lines.append("")
            lines.append("| 知识点 | 问题类型 | 详情 | 建议 |")
            lines.append("|---|---|---|---|")
            for a in pending_audits:
                if a.get("course") != course:
                    continue
                lines.append(f"| {a.get('kp_name')} | {a.get('type')} | "
                             f"{a.get('detail')} | {a.get('suggestion') or '-'} |")
            lines.append("")
    else:
        lines.append("当前没有 P0 问题。全部知识点均已通过机器校验。")
        lines.append("")
    if excluded:
        lines.append(f"（另有 {len(excluded)} 个知识点因未通过校验已在下方各部分中排除，"
                     f"如需强制包含请加 --include-pending）")
    if exp_excluded:
        # 实验批次必须在这里**显式露面**：产物是用户真正会看的东西，
        # 只在控制台打印一句，用户回头看书时是不知道的 —— 这才是要防的"静默"。
        lines.append(f"（另有 {len(exp_excluded)} 个知识点属于**实验批次**"
                     f"（{'、'.join(exp_batches)}），未列入下方各部分 —— 它们是探测性抽取的样本，"
                     f"不是该课的完整考点。登记见 00_配置/experimental-batches.json；"
                     f"如需强制包含请加 --include-experimental）")
        lines.append("")
        lines.append(f"<details><summary>展开这 {len(exp_excluded)} 条实验批次知识点（仅供核对，不要背）</summary>")
        lines.append("")
        for k in exp_excluded:
            lines.append(f"- `{k.get('kp_id')}` **{k.get('name')}** — {k.get('course')}"
                         f"（p{k.get('page_label_start')}）")
        lines.append("")
        lines.append("</details>")
        lines.append("")
    if rej_excluded:
        # 人工已判定不可用 —— 这是**最强的排除理由**，必须留痕，否则以后有人会问
        # "这条怎么不见了"（然后就有人把它加回来）。
        lines.append(f"（另有 {len(rej_excluded)} 个知识点已被**人工复核判定不可用**（status=rejected），"
                     f"已从下方各部分剔除。裁决记录见 audit.jsonl 的 human_review 字段）")
        lines.append("")
        lines.append(f"<details><summary>展开这 {len(rej_excluded)} 条被驳回的知识点</summary>")
        lines.append("")
        for k in rej_excluded:
            lines.append(f"- `{k.get('kp_id')}` **{k.get('name')}** — {k.get('course')}"
                         f"｜{k.get('reject_reason') or ''}")
        lines.append("")
        lines.append("</details>")
        lines.append("")
    (OUT / "00_待确认清单.md").write_text("\n".join(lines), encoding="utf-8")
    written.append(("00_待确认清单.md", len(pending_audits)))

    # ---------------------------------------------------------------- 01 枝干速览
    lines = [
        "# 枝干速览（第一遍看这个）", "",
        f"生成时间：{ts}　范围：{scope}", "",
        f"覆盖 {len(by_course)} 门课、{len(usable)} 个知识点。"
        "建议第一遍只看本页，建立全局结构；第二遍再看 04_脉络展开.md。", "",
    ]
    for course in sorted(by_course):
        group = by_course[course]
        cid = group[0].get("course_id") or ""
        lines.append(f"# {course}　`{cid}`　共 {len(group)} 个知识点")
        lines.append("")
        ch_groups: dict[str, list[dict]] = defaultdict(list)
        for k in group:
            ch_groups[k.get("chapter") or "（未分章）"].append(k)
        for ch in ch_groups:
            sub = sorted(ch_groups[ch], key=lambda k: -int(k.get("importance") or 3))
            lines.append(f"## {ch}")
            lines.append("")
            for k in sub:
                num = ""
                if k.get("numbers"):
                    num = f"　`{'、'.join(str(n) for n in k['numbers'][:3])}`"
                lines.append(f"- {stars(int(k.get('importance') or 3))} **{k.get('name')}**{num}")
            lines.append("")
    (OUT / "01_枝干速览.md").write_text("\n".join(lines), encoding="utf-8")
    written.append(("01_枝干速览.md", len(usable)))

    # ---------------------------------------------------------------- 02 背诵卡
    lines = ["# 背诵卡", "", f"生成时间：{ts}　范围：{scope}", "",
             "用法：遮住「答」，只看「问」；答不出时看「出处」回原文那一页。", ""]
    cards: list[dict] = []
    n = 0
    for course in sorted(by_course):
        group = sorted(by_course[course], key=lambda k: (-int(k.get("importance") or 3),
                                                         k.get("chapter") or ""))
        lines.append(f"## {course}")
        lines.append("")
        for k in group:
            n += 1
            evs_k = ev_by_kp.get(k["kp_id"], [])
            quote = ""
            for e in evs_k:
                if e.get("quote_verified") and e.get("quote"):
                    quote = e["quote"]
                    break
            lines.append(f"### 卡片 {n}　{stars(int(k.get('importance') or 3))}")
            lines.append("")
            lines.append(f"**问：** {k.get('name')}是什么？")
            lines.append("")
            lines.append(f"**答：** {k.get('definition')}")
            lines.append("")
            if k.get("numbers"):
                lines.append(f"**关键数值：** {'、'.join(str(x) for x in k['numbers'])}")
                lines.append("")
            if quote:
                lines.append(f"**原文：** {quote}")
                lines.append("")
            lines.append(f"**出处：** {short_src(k)}　·　`{k.get('kp_id')}`")
            lines.append("")
            _alt = alt_note(k)
            if _alt:
                lines.append(_alt)
                lines.append("")
            if k.get("note"):
                lines.append(f"**提示：** {k.get('note')}")
                lines.append("")
            cards.append({
                "card_id": f"C{n:04d}",
                "kp_id": k.get("kp_id"),
                "course": course,
                "chapter": k.get("chapter"),
                "question": f"{k.get('name')}是什么？",
                "answer": k.get("definition"),
                "numbers": k.get("numbers") or [],
                "quote": quote,
                "source": short_src(k),
                "alt_sources": k.get("alt_sources") or [],
                "importance": int(k.get("importance") or 3),
                "card_type": "data" if k.get("numbers") else
                             ("method" if k.get("type") in ("方法", "原理") else "concept"),
            })
    (OUT / "02_背诵卡.md").write_text("\n".join(lines), encoding="utf-8")
    written.append(("02_背诵卡.md", len(cards)))
    kb.rewrite_jsonl(OUT / "cards.jsonl", cards)

    # ---------------------------------------------------------------- 03 数字对照表
    num_kps = [k for k in usable if k.get("numbers")]
    lines = ["# 数字对照表", "", f"生成时间：{ts}　范围：{scope}", "",
             "> 消防考试里数字错一个就是丢分。本表把散落在各章节的数值集中到一处对比记忆。",
             "> **所有数值均已通过原文核验**（未通过的已排除到待确认清单）。", ""]
    by_course_num: dict[str, list[dict]] = defaultdict(list)
    for k in num_kps:
        by_course_num[k.get("course") or "未分类"].append(k)
    if not num_kps:
        lines.append("当前没有含数值的知识点。")
        lines.append("")
    for course in sorted(by_course_num):
        lines.append(f"## {course}")
        lines.append("")
        lines.append("| 知识点 | 数值 | 出处 | 重要度 |")
        lines.append("|---|---|---|---|")
        for k in sorted(by_course_num[course], key=lambda x: -int(x.get("importance") or 3)):
            vals = "　".join(f"`{v}`" for v in k["numbers"])
            lines.append(f"| {k.get('name')} | {vals} | {short_src(k)} | "
                         f"{stars(int(k.get('importance') or 3))} |")
        lines.append("")
    (OUT / "03_数字对照表.md").write_text("\n".join(lines), encoding="utf-8")
    written.append(("03_数字对照表.md", len(num_kps)))

    # ---------------------------------------------------------------- 04 脉络展开
    lines = ["# 脉络展开（第二遍看这个）", "",
             f"生成时间：{ts}　范围：{scope}", "",
             "按课程 → 章 → 知识点展开，每条含完整定义与原文摘录。", ""]
    for course in sorted(by_course):
        group = by_course[course]
        lines.append(f"# {course}")
        lines.append("")
        ch_groups: dict[str, list[dict]] = defaultdict(list)
        for k in group:
            ch_groups[k.get("chapter") or "（未分章）"].append(k)
        for ch in ch_groups:
            lines.append(f"## {ch}")
            lines.append("")
            for k in sorted(ch_groups[ch], key=lambda x: -int(x.get("importance") or 3)):
                lines.append(f"### {k.get('name')}　{stars(int(k.get('importance') or 3))}")
                lines.append("")
                lines.append(f"- **类型**：{k.get('type')}　**出处**：{short_src(k)}")
                _alt = alt_note(k)
                if _alt:
                    lines.append(f"- {_alt}")
                if k.get("aliases"):
                    lines.append(f"- **别名**：{'、'.join(k['aliases'])}")
                lines.append("")
                lines.append(f"{k.get('definition')}")
                lines.append("")
                if k.get("numbers"):
                    lines.append(f"- **数值**：{'、'.join(str(x) for x in k['numbers'])}")
                if k.get("note"):
                    lines.append(f"- **提示**：{k.get('note')}")
                lines.append("")
                for e in ev_by_kp.get(k["kp_id"], [])[:2]:
                    if not e.get("quote"):
                        continue
                    mark = "" if e.get("quote_verified") else "（未核验）"
                    lines.append(f"> {e['quote']}{mark}　—　{e.get('page_label')}")
                    lines.append("")
    (OUT / "04_脉络展开.md").write_text("\n".join(lines), encoding="utf-8")
    written.append(("04_脉络展开.md", len(usable)))

    # ---------------------------------------------------------------- 05 Anki
    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t", lineterminator="\n",
                   quoting=csv.QUOTE_MINIMAL)
    for c in cards:
        tags = " ".join(x for x in [
            (c["course"] or "").replace(" ", "_"),
            (c.get("chapter") or "").replace(" ", "_"),
            f"imp{c['importance']}",
        ] if x)
        w.writerow([c["question"], c["answer"], tags, c["source"]])
    (OUT / "05_anki导入.tsv").write_text(buf.getvalue(), encoding="utf-8-sig")
    written.append(("05_anki导入.tsv", len(cards)))

    # ---------------------------------------------------------------- 汇总
    print("=" * 74)
    print("M6 复习产物生成完成")
    print("=" * 74)
    print(f"范围：{scope}")
    print(f"纳入知识点 {len(usable)} 个"
          + (f"（跨书同名合并掉 {merged_n} 条，合并前 {_before} 条）" if merged_n else "")
          + (f"（另有 {len(excluded)} 个因未通过校验被排除）" if excluded else ""))
    print()
    for name, cnt in written:
        p = OUT / name
        print(f"  {name:<24}{cnt:>6} 条{cnt and ' ' or ''}"
              f"{p.stat().st_size / 1024:>9.1f} KB")
    print()
    print(f"输出目录：{OUT}")
    print()
    print("建议的使用顺序：")
    print("  1. 先看 00_待确认清单.md —— 明确哪些不能背")
    print("  2. 再看 01_枝干速览.md —— 一遍建立全局结构（约 10 分钟）")
    print("  3. 用 03_数字对照表.md 集中攻数值（消防最易丢分处）")
    print("  4. 最后用 04_脉络展开.md 逐条深入，答不出的回 02_背诵卡.md")
    print("  5. 需要刷题式记忆时，把 05_anki导入.tsv 导入 Anki")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
