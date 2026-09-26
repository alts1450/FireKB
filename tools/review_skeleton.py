# -*- coding: utf-8 -*-
"""
M4a-2 · 知识骨架 AI 复核
========================
解决 build_skeleton.py「页眉章节名法」的层级误判：二级/三级内容被当成一级章节。

典型症状：
  * 消防给水工程 19 个"章"里混入：
      一、建筑按使用性质分类 / 四、 按消防水压分类 / (一)减压孔板 / 减压阀设置要求：
  * 火灾探测与报警系统 3 个"章"分别是：二、基础知识 / 三、实践应用 / 四、复习思考
  * enclosure fire dynamics 37 个"章"里混入：书名、Contents、Preface、Authors、DOI

根因：页眉章节名法只能看到「这一页页眉印了什么」，无法判断它在书的层级体系里是第几级。
      当教材没有「第X章」形式的章号时（或页眉印的是节名），页眉名会被直接当成章。

三层设计（先规则、后 AI、全程留证据）：
  1. 规则预判 —— 形态明确的直接判定，不花 API：
       第X章 / Chapter N            -> chapter
       一、 / （一） / 1.1 / 1.1.1   -> section
       目录/前言/索引/附录/公告/国标 -> matter
       DOI/网址/纯数字/续表/乱码     -> noise
  2. AI 复核 —— 只把规则判不准的候选交给模型，并附带上下文
       （邻居章标题、页码范围、页数、该页页首原文、同书 chapter 数量）
  3. 机器核验 —— AI 必须给出「原文连续片段」作为证据，用 kb.verify_quote 逐字核验；
       核验不通过的判定一律降级为 pending（待确认），**不写回骨架**。
       这是本项目的防幻觉铁律：AI 的判断可以不采纳，但不能没有出处。

用法：
  python tools\\review_skeleton.py --check          # 只看规则预判统计（不调 API）
  python tools\\review_skeleton.py --dry-run        # 看将发给 AI 的内容
  python tools\\review_skeleton.py --course 防排烟工程
  python tools\\review_skeleton.py --limit 1        # 只复核前 1 门课（试跑）
  python tools\\review_skeleton.py                  # 全量复核（写复核报告）
  python tools\\review_skeleton.py --apply          # 应用复核结果到 skeleton.json（自动备份）

产物：
  30_知识点\\skeleton_review.json   复核报告（每条候选的判定 + 证据 + 核验档位）
  90_日志\\skeleton_backup_<时间>.json   --apply 前的骨架备份
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(os.environ.get("FIREKB_ROOT") or Path(__file__).resolve().parents[1])
TOOLS = ROOT / "tools"
KP_DIR = ROOT / "30_知识点"
TEXT_DIR = ROOT / "20_文本库"
LOG_DIR = ROOT / "90_日志"
SKELETON_FILE = KP_DIR / "skeleton.json"
PAGES_FILE = TEXT_DIR / "pages.jsonl"
REVIEW_FILE = KP_DIR / "skeleton_review.json"

sys.path.insert(0, str(TOOLS))
import kb  # type: ignore  # noqa: E402
# 附录的归类**必须只有一处实现**。
# build_skeleton 的 MATTER_RE 与本文件的 RE_MATTER 若各写一遍「附录」，
# 会变成"同一判据两套实现"，改一处漏一处。
# 因此判定统一走 build_skeleton.classify_kind。
import build_skeleton  # type: ignore  # noqa: E402

# ---------------------------------------------------------------------------
# 规则预判用的模式
# ---------------------------------------------------------------------------
CN = "零一二三四五六七八九十百"

RE_CH = re.compile(rf"^\s*第\s*[{CN}0-9]{{1,4}}\s*[章篇部]\s*[\s　:：\.、]*(.*)$")
RE_CH_EN = re.compile(r"^\s*Chapter\s+[0-9]{1,2}\b", re.I)

# 二级：中文顿号编号 / 括号编号 / 多级阿拉伯数字
RE_SEC_CN = re.compile(rf"^\s*[{CN}0-9]{{1,3}}\s*[、\.]\s*\S")
RE_SEC_PAREN = re.compile(rf"^\s*[（(]\s*[{CN}0-9]{{1,3}}\s*[）)]\s*\S")
RE_SEC_MULTI = re.compile(r"^\s*[0-9]{1,2}(?:\.[0-9]{1,2}){1,2}\s+\S")

# 非正文材料（在 build_skeleton 的 MATTER_RE 基础上扩展，覆盖常见识别噪声）
# ⚠️ 这里**不含 appendix / 附录** —— 附录是数值表密集区，不是非正文材料；
# 整章跳过它等于把最该背的数值丢掉（如防排烟工程 p314-319 的《通风管道计算表》）。
# 附录由 build_skeleton.APPENDIX_RE 判为 kind=appendix，走正常抽取；
# 判据只在 build_skeleton 一处实现。
RE_MATTER = re.compile(
    r"^\s*(contents?|table\s*of\s*contents|index|list\s*of\s*(symbols|figures|tables|abbrev\w*)|"
    r"notation|preface|foreword|acknowledg\w*|about\s*the\s*authors?|authors?|"
    r"bibliograph\w*|references?|glossary|abstract|"
    r"符号表|目\s*录|前\s*言|序\s*言|致\s*谢|参考文献|索\s*引|作者简介|摘\s*要|"
    r"中华人民共和国国家标准|中华人民共和国.*公告|国家标准|出版社|"
    r"当代杰出青年科学文库|文库|丛书)\s*[\.。:：]?\s*$", re.I)

# 识别噪声：DOI、网址、纯数字/罗马数字、表格残片、过长过短
RE_NOISE = re.compile(
    r"(doi\s*[:：]|https?://|www\.|^\s*[0-9\.\-—\s]{1,12}\s*$|"
    r"^\s*[ivxlcdm]{1,6}\s*$|续表|^\s*表\s*[0-9]|^\s*图\s*[0-9]|"
    r"^\s*第\s*[0-9]+\s*页|^\s*[0-9]{1,4}\s*$)", re.I)


def rule_verdict(title: str, page_span: int | None = None) -> tuple[str, str] | None:
    """
    规则预判。返回 (verdict, reason)；判不准返回 None（交给 AI）。

    verdict ∈ chapter / section / matter / noise
    """
    t = (title or "").strip()
    if not t:
        return "noise", "标题为空"

    # 1) 明确章号 —— 最可信，直接放行
    if RE_CH.match(t) or RE_CH_EN.match(t):
        return "chapter", "带明确章号（第X章 / Chapter N）"

    # 2) 附录 / 附表 —— **先于 matter 判**，且判成 chapter（保留抽取）。
    #    附录是数值表最密集的地方，规则能判死的就别退给 AI 或当作非正文。
    if build_skeleton.classify_kind(t) == "appendix":
        return "chapter", "附录/附表（数值表密集区，保留抽取）"

    # 3) 非正文材料
    if RE_MATTER.match(t):
        return "matter", "前言/目录/索引等非正文材料"

    # 4) 识别噪声
    if RE_NOISE.search(t):
        return "noise", "DOI/网址/页码/表格残片等识别噪声"
    if len(t) < 2:
        return "noise", "标题过短"
    # 中文标题却几乎不含中文、也不像英文词（OCR 乱码）
    if re.search(r"[\u4e00-\u9fff]", t) is None and len(re.findall(r"[A-Za-z]{2,}", t)) == 0:
        return "noise", "无法构成标题（OCR 乱码）"

    # 5) 明确的二级编号
    if RE_SEC_CN.match(t) or RE_SEC_PAREN.match(t) or RE_SEC_MULTI.match(t):
        return "section", "带二级/三级编号（一、/（一）/1.1 形式）"

    return None  # 判不准，交给 AI


# ---------------------------------------------------------------------------
# 候选与上下文
# ---------------------------------------------------------------------------
def pages_index(course_pages: list[dict]) -> dict:
    """
    按 (source_file, page_no) 建索引。

    必须带 source_file：一个课程目录下可能有多本书，各自的 page_no 都从 1 开始
    （如「通用规范」目录下多本规范合订）。只用 page_no
    当键会让后写入的书覆盖前面的书，从而取到错误的页、错误的原文证据——
    复核结论也就失去依据。
    """
    return {(p.get("source_file"), p.get("page_no")): p
            for p in course_pages if p.get("page_no") is not None}


def head_text(pg: dict | None, max_chars: int = 150) -> str:
    """取某页正文开头若干字符，作为给 AI 的原文证据来源。"""
    if not pg:
        return ""
    txt = (pg.get("text") or "").strip()
    txt = re.sub(r"\s+", " ", txt)
    return txt[:max_chars]


def build_candidates(course: str, info: dict, by_page: dict[int, dict]) -> list[dict]:
    """把骨架里的章转换成一批评审候选，附上判断所需的上下文。"""
    chapters = info.get("chapters", [])
    n_with_no = sum(1 for c in chapters
                    if RE_CH.match(c.get("title", "") or "") or RE_CH_EN.match(c.get("title", "") or ""))
    out: list[dict] = []
    for i, c in enumerate(chapters):
        prev_title = chapters[i - 1].get("title") if i > 0 else None
        next_title = chapters[i + 1].get("title") if i + 1 < len(chapters) else None
        ps, pe = c.get("page_start"), c.get("page_end")
        span = None
        if isinstance(ps, int) and isinstance(pe, int):
            span = max(0, pe - ps + 1)
        rv = rule_verdict(c.get("title", ""), span)
        out.append({
            "idx": i,
            "node_id": c.get("node_id"),
            # 关键：一个"课程"目录下可能有多本书。章号与页眉体系因书而异，
            # 必须按书分组复核，否则上下文串台会导致层级误判。
            "book": c.get("source_file") or "(未知来源)",
            "title": c.get("title"),
            "page_start": ps,
            "page_end": pe,
            "page_span": span,
            "sections_found": len(c.get("sections") or []),
            "method": c.get("from"),
            "prev_title": prev_title,
            "next_title": next_title,
            "page_head": head_text(by_page.get((c.get("source_file"), ps))
                                   if isinstance(ps, int) else None),
            "rule_verdict": rv[0] if rv else None,
            "rule_reason": rv[1] if rv else None,
        })
    return out, n_with_no


SYSTEM_PROMPT = """你是消防工程教材的章节结构审校员。

任务：判断候选标题在**这本书的层级体系**里属于哪一级，而不是判断它"像不像标题"。

四个类别：
- chapter：真正的一级章（全书主要知识板块，通常并列、覆盖全书）
- section：二级/三级内容（隶属于某个 chapter，即使它看起来像一个完整标题）
- matter ：非正文材料（书名、丛书名、目录、前言、序、致谢、作者、符号表、索引、参考文献、附录、国标公告等）
- noise ：识别噪声（DOI、网址、纯页码、表格残片如"续表3"、OCR 乱码）

判断要点：
1. 带明确章号（第X章 / Chapter N）的，是 chapter。
2. 形式为「一、」「(一)」「1.1」「1.1.1」的，是 section —— 它们是被编号的从属层级。
3. 判断"从属"的关键：若某个标题明显落在前一个 chapter 的**主题范围**之内，
   它就是 section。例如"减压阀设置要求""(一)减压孔板"都从属于给水系统的那个章。
4. 同一本书的 chapter 应当数量有限、彼此并列、能覆盖全书；若某个"章"夹在两个
   chapter 之间且主题被前一个包住，优先判 section。
5. 只有在标题确实无法归入任何已知类别时才判 noise。

输出格式（严格 JSON，不要多余文字）：
{"items": [{"idx": <候选编号>, "verdict": "chapter|section|matter|noise",
            "reason": "一句话中文理由",
            "evidence": "从该候选页码范围原文里逐字摘录的连续片段，10~60 字"}]}

关于 evidence（硬性要求）：
- 必须逐字来自我给出的 page_head 原文，不得改写、不得翻译、不得拼接。
- 若确实无法从原文取到片段，evidence 填空字符串 ""，我会据此降低该判定的可信度。
- 不要为了凑证据而编造。"""


def short_book(source_file: str) -> str:
    """把 source_file 缩成易读的书名，便于日志与报告阅读。"""
    s = (source_file or "").split("/")[-1]
    s = re.sub(r"\.(pdf|epub|pptx|docx)$", "", s, flags=re.I)
    s = re.sub(r"\s*\(z-library[^)]*\)", "", s, flags=re.I)
    return s.strip() or (source_file or "(未知来源)")


def build_prompt(course: str, book: str, info: dict, cands: list[dict],
                 targets: list[dict]) -> str:
    lines: list[str] = []
    lines.append(f"课程/目录：{course}")
    lines.append(f"正在复核的书：{short_book(book)}")
    lines.append(f"识别方式：{info.get('method')}（header=按页眉章节名识别）")
    lines.append("重要：以下候选全部来自**同一本书**，请只在这本书自己的层级体系内判断，"
                 "不要与其他书比较。")
    lines.append(f"本书候选总数：{len(cands)}")
    n_no = sum(1 for c in cands
               if RE_CH.match(c["title"] or "") or RE_CH_EN.match(c["title"] or ""))
    lines.append(f"本书带明确章号（第X章/Chapter N）的候选数：{n_no}")
    lines.append("")
    lines.append("全部候选（供你理解本书的整体结构，编号 idx 与下表一致）：")
    for c in cands:
        mark = "✓章号" if (RE_CH.match(c["title"] or "") or RE_CH_EN.match(c["title"] or "")) else "  ——"
        rng = f"p{c['page_start']}-{c['page_end']}" if c["page_start"] else "页码未定位"
        lines.append(f"  [{c['idx']:>2}] {mark} {c['title']}  ({rng}, {c['page_span']}页)")
    lines.append("")
    lines.append("=" * 66)
    lines.append("需要你判定的候选项（规则层无法确定，请逐个给出结论）：")
    lines.append("=" * 66)
    for c in targets:
        lines.append("")
        lines.append(f"idx={c['idx']}")
        lines.append(f"  标题      : {c['title']}")
        lines.append(f"  页码范围  : p{c['page_start']}-{c['page_end']}（{c['page_span']} 页）")
        lines.append(f"  前一个候选: {c['prev_title']}")
        lines.append(f"  后一个候选: {c['next_title']}")
        lines.append(f"  已归入节数: {c['sections_found']}")
        lines.append(f"  该页首原文: {c['page_head'] or '(无文本)'}")
    lines.append("")
    lines.append(f"请对以上 {len(targets)} 个候选项逐个输出判定，返回 JSON。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 复核主流程
# ---------------------------------------------------------------------------
def review_course(course: str, info: dict, by_page: dict, args) -> dict:
    cands, n_with_no = build_candidates(course, info, by_page)
    info["_n_with_no"] = n_with_no

    # 规则层直接定论的，以及需要 AI 的
    decided: list[dict] = []
    targets: list[dict] = []
    for c in cands:
        if c["rule_verdict"]:
            decided.append({**c, "verdict": c["rule_verdict"], "reason": c["rule_reason"],
                            "evidence": "", "decided_by": "rule", "verify_tier": "-",
                            "accepted": True})
        else:
            targets.append(c)

    # 按书分组：同一课程目录下可能有多本书，
    # 各书的章号与页眉体系互不相干，混在一起复核必然串台。
    books: dict[str, list[dict]] = {}
    for c in cands:
        books.setdefault(c["book"], []).append(c)
    book_targets: dict[str, list[dict]] = {}
    for c in targets:
        book_targets.setdefault(c["book"], []).append(c)

    print(f"\n▍{course}")
    print(f"   候选 {len(cands)} 个（来自 {len(books)} 本书）："
          f"规则判定 {len(decided)} 个，需 AI 复核 {len(targets)} 个")
    if decided:
        from collections import Counter
        c0 = Counter(d["verdict"] for d in decided)
        print(f"   规则层结论： " + "  ".join(f"{k}={v}" for k, v in c0.items()))
    for bk in books:
        n_a = len(book_targets.get(bk, []))
        print(f"     · {short_book(bk)[:52]:<52} 候选{len(books[bk]):>3}  "
              f"规则{len(books[bk]) - n_a:>3}  AI{n_a:>3}")

    ai_items: list[dict] = []
    if targets and not args.check:
        for book, bt in book_targets.items():
            if args.dry_run:
                p = build_prompt(course, book, info, books[book], bt)
                print(f"\n   [dry-run] 《{short_book(book)}》将发送给模型的内容：")
                print("   " + "\n   ".join(p.splitlines()[:30]))
                print(f"   ...（共 {len(p)} 字符）")
                continue
            prompt = build_prompt(course, book, info, books[book], bt)
            print(f"\n   复核《{short_book(book)}》{len(bt)} 个候选（prompt {len(prompt)} 字符）...")
            try:
                res = kb.ask_json(prompt, system=SYSTEM_PROMPT, max_tokens=8000,
                                  temperature=0.1, verbose=args.verbose)
                items = res.get("items") or []
                ai_items.extend(items)
                print(f"     模型返回 {len(items)} 条判定")
            except Exception as exc:
                print(f"     [FAIL] AI 复核失败：{exc}")

    by_idx = {c["idx"]: c for c in targets}
    ai_rows: list[dict] = []
    for it in ai_items:
        try:
            idx = int(it.get("idx"))
        except Exception:
            continue
        c = by_idx.get(idx)
        if not c:
            continue
        verdict = str(it.get("verdict") or "").strip().lower()
        if verdict not in ("chapter", "section", "matter", "noise"):
            verdict = "pending"
        reason = str(it.get("reason") or "").strip()[:200]
        evidence = str(it.get("evidence") or "").strip()

        # ---- 机器核验：证据必须逐字来自该候选页码范围 ----
        body = collect_body(by_page, c.get("book"), c["page_start"], c["page_end"])
        tier, matched, note = ("not_found", "", "无证据")
        if evidence and body:
            tier, matched, note = kb.verify_quote(evidence, body)
        ok = bool(evidence) and kb.quote_passes(tier)

        # 证据不过 -> 不采纳，降级为 pending（防幻觉铁律）
        if verdict != "chapter" and not ok:
            final, accepted = "pending", False
            reason = f"[证据未通过核验，待人工确认] {reason}"
        else:
            # chapter 判定不需要证据（保持现状即无风险）
            final, accepted = verdict, True

        ai_rows.append({**c, "verdict": final, "reason": reason, "evidence": evidence,
                        "decided_by": "ai", "verify_tier": tier, "accepted": accepted,
                        "verify_note": note if evidence else ""})

    # 没拿到 AI 结果的候选 -> pending
    got = {r["idx"] for r in ai_rows}
    for c in targets:
        if c["idx"] not in got:
            ai_rows.append({**c, "verdict": "pending", "reason": "未获得模型判定",
                            "evidence": "", "decided_by": "none", "verify_tier": "-",
                            "accepted": False})

    rows = sorted(decided + ai_rows, key=lambda r: r["idx"])
    from collections import Counter
    summary = dict(Counter(r["verdict"] for r in rows))
    return {"course": course, "method": info.get("method"), "candidates": rows,
            "summary": summary, "chapter_count_before": len(info.get("chapters") or [])}


def collect_body(by_page: dict, src, ps, pe, max_chars: int = 12000) -> str:
    """把某候选（指定书 + 页码范围）拼成一段正文，用于引用核验。"""
    if not isinstance(ps, int):
        return ""
    if not isinstance(pe, int):
        pe = ps
    parts: list[str] = []
    total = 0
    for n in range(ps, pe + 1):
        pg = by_page.get((src, n))
        if not pg:
            continue
        t = (pg.get("text") or "").strip()
        if not t:
            continue
        parts.append(t)
        total += len(t)
        if total >= max_chars:
            break
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 应用复核结果
# ---------------------------------------------------------------------------
def apply_review(skeleton: dict, reviews: dict[str, dict]) -> tuple[dict, dict]:
    """
    把复核结论写回骨架结构。

      chapter -> 保留
      section -> 降级为其前方最近的真章下的节
      matter  -> kind=matter（现有机制：抽取知识点时跳过）
      noise   -> 移出骨架，记入报告
      pending -> 原样保留（但标记 review_status，便于后续人工处理）
    """
    stats = {"kept": 0, "demoted": 0, "matter": 0, "removed": 0, "pending": 0, "reassigned_sections": 0}

    for course, info in skeleton.get("courses", {}).items():
        rev = reviews.get(course)
        if not rev:
            continue
        verdict_by_id = {}
        for r in rev["candidates"]:
            nid = r.get("node_id")
            if nid:
                verdict_by_id[nid] = r

        new_chapters: list[dict] = []
        cur: dict | None = None

        for ch in info.get("chapters", []):
            rec = verdict_by_id.get(ch.get("node_id"))
            verdict = (rec or {}).get("verdict", "chapter")

            if verdict == "noise":
                stats["removed"] += 1
                continue

            if verdict == "section":
                # 降级：并入前方最近的真章
                if cur is None:
                    if new_chapters:
                        cur = new_chapters[-1]
                    else:
                        # 前面还没有真章：保留为章，避免内容无处安放
                        ch["review_status"] = "section_without_parent"
                        new_chapters.append(ch)
                        stats["kept"] += 1
                        continue
                sec = {
                    "no": (rec or {}).get("title"),
                    "title": ch.get("title"),
                    "page_start": ch.get("page_start"),
                    "page_label": ch.get("page_label_start"),
                    "page_end": ch.get("page_end"),
                    "from": "skeleton_review",
                    "review_reason": (rec or {}).get("reason"),
                }
                cur.setdefault("sections", []).append(sec)
                stats["demoted"] += 1
                stats["reassigned_sections"] += 1
                continue

            if verdict == "matter":
                # 守卫：**附录/附表不允许被标成 matter**。
                # AI 看到"附表"两个字容易归到"非正文材料"，但它恰恰是数值最密集的地方 ——
                # 误判成 matter 会让整章数值区被静默跳过（如「续附表」整章）。
                # 所以这里用规则硬挡：规则能判死的，不要交给模型 judge。
                if build_skeleton_classify(ch.get("title", "")) == "appendix":
                    ch["kind"] = "appendix"
                    ch["review_status"] = "appendix"
                    ch["review_note"] = ("AI 判为 matter，但标题命中附录/附表模式 —— "
                                         "按附录归类规则改判 appendix（保留抽取）")
                    new_chapters.append(ch)
                    cur = ch
                    stats["matter"] += 1
                    stats.setdefault("appendix_rescued", 0)
                    stats["appendix_rescued"] += 1
                    continue
                ch["kind"] = "matter"
                ch["review_status"] = "matter"
                new_chapters.append(ch)
                cur = ch
                stats["matter"] += 1
                continue

            if verdict == "pending":
                ch["review_status"] = "pending"
                stats["pending"] += 1
            else:
                stats["kept"] += 1
            new_chapters.append(ch)
            cur = ch

        # 章号重排 + 区间/节区间重算
        info["chapters"] = renumber(new_chapters, course)

    return skeleton, stats


def renumber(chapters: list[dict], course: str) -> list[dict]:
    """复核后重排序号、重算区间与 node_id（降级会把章变少）。"""
    chapters.sort(key=lambda c: (c.get("page_start") or 10 ** 9))
    course_key = re.sub(r"\s+", "", course)[:12]

    for i, c in enumerate(chapters, start=1):
        c["num"] = i
        c["node_id"] = f"{course_key}::ch{i:02d}"

    for i, c in enumerate(chapters):
        nxt = chapters[i + 1] if i + 1 < len(chapters) else None
        if c.get("page_start") and nxt and nxt.get("page_start"):
            c["page_end"] = max(c["page_start"], nxt["page_start"] - 1)

    for c in chapters:
        secs = sorted(c.get("sections") or [], key=lambda s: s.get("page_start") or 0)
        for i, s in enumerate(secs):
            nxt = secs[i + 1] if i + 1 < len(secs) else None
            if s.get("page_start") and nxt and nxt.get("page_start"):
                s["page_end"] = max(s["page_start"], nxt["page_start"] - 1)
            else:
                s["page_end"] = c.get("page_end")
        c["sections"] = secs
        c["section_count"] = len(secs)
        probe = re.sub(r"\s+", "", c.get("title", "")).lower()
        if c.get("kind") != "matter":
            c["kind"] = "body"
        for s in secs:
            safe = re.sub(r"[^\w\u4e00-\u9fff]+", "", str(s.get("no") or s["title"]))[:10]
            s["node_id"] = f"{c['node_id']}::{safe}"
    return chapters


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="知识骨架 AI 复核（修正层级误判）")
    ap.add_argument("--check", action="store_true", help="只做规则预判统计，不调用 API")
    ap.add_argument("--dry-run", action="store_true", help="只展示将发给模型的内容")
    ap.add_argument("--course", help="只复核某门课")
    ap.add_argument("--limit", type=int, help="最多复核前 N 门课（试跑用）")
    ap.add_argument("--apply", action="store_true", help="把复核结果应用到 skeleton.json（自动备份）")
    ap.add_argument("--force-apply", action="store_true",
                    help="即使检出页码范围倒置也强行写回（默认拒绝）")
    ap.add_argument("--show", action="store_true", help="只在屏幕上显示，不写复核报告")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not SKELETON_FILE.exists():
        print(f"[FAIL] 骨架不存在：{SKELETON_FILE}，请先运行 build_skeleton.py")
        return 2
    if not PAGES_FILE.exists():
        print(f"[FAIL] 文本库不存在：{PAGES_FILE}，请先运行 parse_docs.py")
        return 2

    skeleton = kb.load_json(SKELETON_FILE, {}) or {}
    groups = kb.group_pages_by_course()

    print("=" * 74)
    print("M4a-2 知识骨架 AI 复核")
    print("=" * 74)
    print(f" 骨架    : {SKELETON_FILE}")
    print(f" 生成于  : {skeleton.get('generated_at')}")
    print(f" 复核策略: 规则预判 -> AI 复核 -> 逐字引用核验（核验不过一律不采纳）")
    if args.check:
        print(" 模式    : --check（只做规则预判，不调用 API）")
    elif args.dry_run:
        print(" 模式    : --dry-run（不调用 API）")

    reviews: dict[str, dict] = {}
    todo = list(sorted(skeleton.get("courses", {}).items()))
    if args.course:
        todo = [(k, v) for k, v in todo if args.course in k]
    if args.limit:
        todo = todo[: args.limit]

    if not todo:
        print("\n[!] 没有匹配的课程")
        return 1

    for course, info in todo:
        pages = groups.get(course) or []
        by_page = pages_index(pages)
        if not by_page:
            print(f"\n▍{course}\n   [!] 无可用页文本，跳过")
            continue
        reviews[course] = review_course(course, info, by_page, args)

    # ---- 汇总 ----
    print("\n" + "=" * 74)
    print(" 复核汇总")
    print("=" * 74)
    total: dict[str, int] = {}
    for course, rev in reviews.items():
        s = rev["summary"]
        for k, v in s.items():
            total[k] = total.get(k, 0) + v
        print(f" {course:<24} 候选 {len(rev['candidates']):>3}  "
              f"chapter={s.get('chapter', 0):>3}  section={s.get('section', 0):>3}  "
              f"matter={s.get('matter', 0):>2}  noise={s.get('noise', 0):>2}  pending={s.get('pending', 0):>2}")
    print(" " + "-" * 70)
    print(f" 合计： " + "  ".join(f"{k}={v}" for k, v in sorted(total.items())))

    cost = kb.cost_summary()
    if cost["calls"]:
        print(f"\n API 调用 {cost['calls']} 次，"
              f"输入 {cost['prompt_tokens']} tokens，输出 {cost['completion_tokens']} tokens"
              f"（约 {kb.estimate_cost_yuan():.3f} 元，空闲时段估算）")
        if cost["failed"]:
            print(f" 失败调用 {cost['failed']} 次")

    # ---- 写报告 ----
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_skeleton_generated_at": skeleton.get("generated_at"),
        "strategy": "rule -> ai -> verbatim-citation-verify",
        "summary": total,
        "cost": cost,
        "courses": reviews,
    }

    if not args.show and not args.dry_run and not args.check:
        # --check / --dry-run 只是预演，不能覆盖已有的真实复核报告
        kb.write_json(REVIEW_FILE, report)
        print(f"\n复核报告已写入：{REVIEW_FILE}")

    # ---- 应用 ----
    if args.apply:
        if args.check or args.dry_run:
            print("\n[!] --apply 需要真实复核结果，请去掉 --check / --dry-run 后重跑")
            return 1
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = LOG_DIR / f"skeleton_backup_{stamp}.json"
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        backup.write_text(SKELETON_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"\n已备份骨架：{backup}")

        new_sk, stats = apply_review(skeleton, reviews)
        new_sk["reviewed_at"] = datetime.now().isoformat(timespec="seconds")
        new_sk["review_source"] = "review_skeleton.py"

        # ---- 写回前范围断言 ----
        # 为什么必须加：本脚本把 AI 复核结果直接写回骨架，而降级/合并章会重算
        # page_end，可能留下 **page_end < page_start** 的倒置范围（形如 `ch10::21 p38-20`）。
        # 倒置范围会让该节切不出任何块（静默丢内容），下游只能靠"章级兜底"勉强接住。
        bad = []
        for cname, cinfo in (new_sk.get("courses") or {}).items():
            for ch in (cinfo.get("chapters") or []):
                cs, ce = ch.get("page_start"), ch.get("page_end")
                if cs and ce and int(ce) < int(cs):
                    bad.append(f"{cname} / {ch.get('node_id')} 章范围倒置 p{cs}-{ce}")
                for s in (ch.get("sections") or []):
                    ss, se = s.get("page_start"), s.get("page_end")
                    if ss and se and int(se) < int(ss):
                        bad.append(f"{cname} / {s.get('node_id')} 节范围倒置 p{ss}-{se}")
        if bad:
            print("\n" + "=" * 78)
            print(f"[STOP] 复核结果里有 {len(bad)} 处**页码范围倒置**（page_end < page_start），"
                  f"拒绝写回：")
            print("=" * 78)
            for b in bad[:15]:
                print("   ✗ " + b)
            if len(bad) > 15:
                print(f"   ... 其余 {len(bad) - 15} 处")
            print("\n   倒置范围会让该章/节切不出任何块（静默丢内容），必须先修复核规则。")
            print("   骨架**未被改动**（备份已存在：%s）" % backup.name)
            print("   确认要强行写入（不推荐）：加 --force-apply")
            print("=" * 78)
            if not getattr(args, "force_apply", False):
                return 3
            print("   [!] --force-apply 已指定，继续写入（请自行承担后果）")

        kb.write_json(SKELETON_FILE, new_sk)
        print("复核结果已应用到骨架：")
        print(f"   保留 chapter {stats['kept']}   降级为节 {stats['demoted']}   "
              f"标记 matter {stats['matter']}   移除噪声 {stats['removed']}   "
              f"待确认 {stats['pending']}")
        print(f"骨架已更新：{SKELETON_FILE}")
        print("\n下一步：python tools\\build_skeleton.py --show  核对结果，"
              "确认无误后可 python tools\\extract_kp.py")
    else:
        print("\n提示：加 --apply 才会把复核结果写回骨架（写前会自动备份）。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
