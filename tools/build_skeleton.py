# -*- coding: utf-8 -*-
"""
M4a · 知识骨架构建
===================
从教材 / 课件中识别章节结构，生成课程的知识骨架（章 -> 节），
为后续「知识点归位」（extract_kp.py）和「自上而下复习」提供导航系统。

为什么骨架必须先行：
  没有骨架，跨课/跨章节的知识点命名会漂移，几十节课后无法收拾。
  消防工程有现成的教材体系，骨架不需要 AI 发明，只需要「识别 + 归位」。

识别策略（先精准后兜底）：
  1. 目录页解析：一页中出现 ≥4 个「第X章 标题 ... 页码」模式 -> 判为目录页，
     直接得到完整章列表（最准）。
  2. 正文标题识别：逐页扫描「第X章 / Chapter N / 第X节 / 1.2」等模式。
  3. 页码归属：章标题所在页为起始页，下一章起始页 - 1 为结束页。

用法：
  python tools\\build_skeleton.py             # 构建全部课程
  python tools\\build_skeleton.py --course 防排烟工程
  python tools\\build_skeleton.py --show      # 只看结果，不重写
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 路径常量统一向共享层 kb 取，不在各脚本里各写一遍 FIREKB_ROOT 解析。
# 用 `__file__` 定位 tools/ 而不是用 ROOT —— 这样"先有鸡还是先有蛋"就不存在了（导入 kb 后才拿 ROOT）。
# ⚠️ 本文件按**字节级还原**验收（`.pyc` 头的 mtime 字段参与比对，如 mtime 1789882321 ⇒ SHA `8219A8EA…`）：
#    改完**不要跑 `py_compile`**（会重写 .pyc 头），语法校验用 `compile(src, path, "exec")`。
sys.path.insert(0, str(Path(__file__).resolve().parent))

import kb  # noqa: E402  共享层（路径常量唯一来源）

ROOT = kb.ROOT
TEXT_DIR = kb.TEXT_DIR
KP_DIR = kb.KP_DIR
PAGES_FILE = kb.PAGES_FILE
SKELETON_FILE = kb.SKELETON_FILE

CN = "零一二三四五六七八九十百"
RE_CH = [
    re.compile(rf"^第\s*([{CN}0-9]{{1,4}})\s*章\s*[\s　:：\.、]*(.{{1,40}})$"),
    re.compile(r"^Chapter\s+([0-9]{1,2})\b[\s:：\.]*(.{0,40})$", re.I),
]
RE_SEC = [
    re.compile(rf"^第\s*([{CN}0-9]{{1,4}})\s*节\s*[\s　:：\.、]*(.{{1,40}})$"),
    # 支持 1.2 与 1.2.3 两种编号形式（英文教材常见 "2.2.1  General Description..."）
    re.compile(r"^([0-9]{1,2}(?:\.[0-9]{1,2}){1,2})\s+([\s　]*\S.{1,45})$"),
]
RE_TOC_LINE = re.compile(
    rf"^第\s*([{CN}0-9]{{1,4}})\s*章\s*[\s　:：\.、]*(.{{1,40}}?)[\s\.·．…\-]{{2,}}\s*([0-9]{{1,4}})\s*$"
)
RE_TOC_EN = re.compile(r"^Chapter\s+([0-9]{1,2})\b[\s:：\.]*(.{1,40}?)[\s\.·．…\-]{2,}\s*([0-9]{1,4})\s*$", re.I)

TITLE_MAX_LEN = 46
NOISE_CHARS = set("，。；：！？,;:!?（）()《》\"'’“”")

# 前言 / 后置材料：这类章节不是正文内容，抽取知识点只会产出"符号表说明"之类的废料
# （符号表章节若不标记，会抽出"面积类符号""比热与热力学量符号"这类没有独立知识点的条目）。
MATTER_RE = re.compile(
    r"^\s*(index|contents|tableofcontents|listof(symbols|figures|tables)|notation|"
    r"preface|foreword|acknowledg\w*|abouttheauthor|authors?|bibliography|references|"
    r"符号表|目录|前言|序言|致谢|参考文献|索引|作者简介)\s*$", re.I)

# 附录 / 附表：**不是**非正文材料，而是数值表最密集的高价值区。
#
# 为什么必须从 MATTER_RE 里摘出来：把 `附录` 与「索引/符号表/参考文献」并列，
# 会让它被**整章跳过**。而附录恰恰常是数值表：例如防排烟工程 p314–319 的
# 《附录A 钢板圆形通风管道计算表》（速度 / 动压 / 风管断面直径），
# 正是本项目 `03_数字对照表.md` 存在的意义所在 —— 跳过它等于把最该背的数值丢了。
# 消防给水工程 ch12 附录（10 页）同理。
#
# 为什么单独立一个 kind 而不是直接并回 body：附录有它的特殊性（多为纯表格、
# 段落结构弱），留一个可检索的标记，将来要单独调抽取提示词时用得上；
# 而 extract_kp 只跳过 `matter`，所以 `appendix` 会被正常抽取。
APPENDIX_RE = re.compile(r"^\s*(附\s*录|附\s*表|续附\s*表|appendix)", re.I)


def classify_kind(title: str) -> str:
    """章的分类：matter（跳过）/ appendix（保留，且要显式报告）/ body。"""
    probe = re.sub(r"\s+", "", title or "").lower()
    if MATTER_RE.match(probe):
        return "matter"
    if APPENDIX_RE.match(probe):
        return "appendix"
    return "body"


def clean_title(s: str) -> str:
    """
    清掉标题中的页码与点线，并修补 OCR 造成的粘连。

    页眉常见形态：
      中文：「第 2 章 火灾烟气的流动与控制 23」「24 防排烟工程」
            OCR 还会抹掉空格或把空格识成下划线：「第2 章_火灾烟气的流动与控制27」
      英文：「Chapter 3 Heat Transfer」（配页码行）
    """
    if not s:
        return ""
    s = s.replace("_", " ").replace("\u3000", " ")
    s = re.sub(r"[\s　\.·．…\-]{1,}[0-9]{1,4}\s*$", "", s)   # 尾部页码（有分隔符）
    # 前导页码：既可能是「24 防排烟工程」（有空格），也可能是 OCR 粘连的
    # 「82防排烟工程」。只在本就紧跟中文字符时才剥离，避免误伤「3D打印」这类标题。
    s = re.sub(r"^[\s　]*[0-9]{1,4}[\s　]*(?=[\u4e00-\u9fff])", "", s)
    # 「第X章……27」：OCR 抹掉空格后页码紧贴标题，需单独剥离
    if re.match(r"^第\s*[0-9零一二三四五六七八九十百]{1,4}\s*[章节]", s):
        s = re.sub(r"[0-9]{1,4}\s*$", "", s).rstrip()
    s = re.sub(r"\s+", " ", s).strip(" .·．…-—\t　")
    return s


def chapter_key(s: str) -> str:
    """
    章的分组键：优先用章号（最稳），无章号时回退到归一化标题。

    为什么必须这样做：同一章在不同页的页眉会出现
      「第2章火灾烟气的流动与控制」「第 2 章 火灾烟气的流动与控制」
      「第2 章_火灾烟气的流动与控制27」「第2章火灾烟气的流动与控制53」
    若按原文字符串比较，同一章会被拆成十几个（每页页眉带的页码不同即算作不同标题）。
    """
    k = re.sub(r"\s+", "", s or "").replace("_", "")
    m = re.match(rf"^第([{CN}0-9]{{1,4}})[章节]", k)
    if m:
        num = cn_to_int(m.group(1))
        if num is not None:
            return f"#ch{num}"
    m = re.match(r"^Chapter([0-9]{1,2})", k, re.I)
    if m:
        return f"#ch{m.group(1)}"
    return re.sub(r"[0-9]{1,6}$", "", k)


def plausible_title(s: str) -> bool:
    """过滤 OCR 噪声与正文误判。"""
    if not s or len(s) > TITLE_MAX_LEN:
        return False
    if sum(1 for c in s if c in NOISE_CHARS) > 2:
        return False
    # 至少一个中文字或 3 个拉丁字母
    if not (re.search(r"[\u4e00-\u9fff]", s) or re.search(r"[A-Za-z]{3,}", s)):
        return False
    return True


def parse_toc(pages: list[dict]) -> list[tuple[int, str]]:
    """从目录页解析完整章列表。返回 [(章序号, 标题), ...]（按出现顺序）。"""
    best: list[tuple[int, str]] = []
    for pg in pages[:60]:  # 目录通常在前 60 页内
        text = pg.get("text") or ""
        if not text:
            continue
        hits: list[tuple[int, str]] = []
        for line in text.split("\n"):
            line = line.strip()
            if len(line) > 70:
                continue
            for pat in (RE_TOC_LINE, RE_TOC_EN):
                m = pat.match(line)
                if m:
                    num_raw, title, _pno = m.group(1), m.group(2), m.group(3)
                    num = cn_to_int(num_raw)
                    title = clean_title(title)
                    if num and plausible_title(title):
                        hits.append((num, title))
                    break
        if len(hits) >= 4 and len(hits) > len(best):
            best = hits
    # 去重保序
    seen = set()
    out = []
    for num, title in best:
        if num in seen:
            continue
        seen.add(num)
        out.append((num, title))
    return sorted(out, key=lambda x: x[0])


def cn_to_int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    d = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
         "六": 6, "七": 7, "八": 8, "九": 9}
    if s == "十":
        return 10
    if "十" in s:
        a, _, b = s.partition("十")
        tens = d.get(a, 1) if a else 1
        ones = d.get(b, 0) if b else 0
        return tens * 10 + ones
    if len(s) == 1:
        return d.get(s)
    return None


def scan_headings(pages: list[dict]) -> list[dict]:
    """从正文逐页识别章 / 节标题。"""
    found: list[dict] = []
    for pg in pages:
        text = pg.get("text") or ""
        if not text:
            continue
        lines = [ln.strip() for ln in text.split("\n")[:15]]
        for ln in lines:
            if not ln or len(ln) > TITLE_MAX_LEN + 12:
                continue
            hit = None
            for pat in RE_CH:
                m = pat.match(ln)
                if m:
                    num = cn_to_int(m.group(1))
                    title = clean_title(m.group(2))
                    if num and plausible_title(title):
                        hit = {"level": 1, "num": num, "title": title}
                    break
            if not hit:
                for pat in RE_SEC:
                    m = pat.match(ln)
                    if m:
                        sec_no = m.group(1)
                        title = clean_title(m.group(2))
                        if plausible_title(title):
                            try:
                                sec_int = int(float(sec_no)) if "." in sec_no else cn_to_int(sec_no)
                            except Exception:
                                sec_int = None
                            hit = {"level": 2, "num": sec_int, "no": sec_no, "title": title}
                        break
            if hit:
                found.append({
                    **hit,
                    "page_no": pg.get("page_no"),
                    "page_label": pg.get("page_label"),
                    "source_file": pg.get("source_file"),
                })
                break  # 每页最多认一个标题
    return found


def page_header(pg: dict) -> tuple[str | None, str | None]:
    """取一页最前面的两行，用于页眉分析。"""
    lines = [ln.strip() for ln in (pg.get("text") or "").split("\n") if ln.strip()]
    a = lines[0][:90] if len(lines) >= 1 else None
    b = lines[1][:90] if len(lines) >= 2 else None
    return a, b


_PAGE_NUM_RE = re.compile(r"^(?:[ivxlcdm]{1,6}|\d{1,4}|第?\s*\d{1,4}\s*页?)$", re.I)


def chapters_from_header(pages: list[dict], min_pages: int = 2) -> list[dict]:
    """
    通过「页眉中的章节名变化」识别章。

    适用形态：
      英文教材：第 1 行 = 页码，第 2 行 = 章名或书名
      中文教材：第 1 行常为 "第X章 章名"，或章名 + 页码

    关键难点 —— 双边页眉：
      印刷书常在左右页交替印「书名」与「章名」（如偶数页印章名、奇数页印书名）。
      若不排除书名，页眉名会逐页来回跳变，真正的章名会被判成「每页都在变」，
      导致每章只统计到 1 页而被 min_pages 过滤掉 —— 全部正文章会因此丢失。
      解决办法：统计页眉名频次，把「在书前部出现过 且 高频出现」的名字视为书名排除。
    """
    seq: list[tuple[str, int, str | None]] = []
    for pg in pages:
        h1, h2 = page_header(pg)
        name = None
        if h1 and _PAGE_NUM_RE.match(h1):
            name = h2
        elif h1 and not _PAGE_NUM_RE.match(h1):
            if not re.search(r"[。；，,;]$", h1):
                name = clean_title(h1)
        if name:
            name = clean_title(name)
            if not plausible_title(name) or len(name) < 3:
                name = None
        # 关键：必须连 source_file 一起记录。多本书同处一个课程目录时，
        # page_no 会各自从 1 开始（如「通用规范」目录下多本规范合订），
        # 只记 page_no 会让后面的书覆盖前面的书，章的来源与页码双双错位。
        seq.append((pg.get("source_file") or "", pg.get("page_no") or 0, name))

    counter = Counter(n for _, _, n in seq if n)
    front_names = {n for _, _, n in seq[:8] if n}
    total = max(len(seq), 1)
    exclude = {n for n in front_names if counter[n] >= max(3, total * 0.10)}

    # 补充规则：出现频率最高的、且不属于「第X章/Chapter N」形式的页眉名，判为书名排除。
    # 中文教材奇数页页眉常印书名（如「防排烟工程」），不排除会让真正的章名被冲散。
    if counter:
        top_name, top_n = counter.most_common(1)[0]
        if (top_n >= max(5, total * 0.15)
                and not re.match(r"^(第\s*[0-9零一二三四五六七八九十百]{1,4}\s*[章节]|Chapter\s+[0-9]{1,2})",
                                 top_name, re.I)):
            exclude.add(top_name)

    chapters: list[dict] = []
    cur: dict | None = None
    for src, pno, name in seq:
        if not name or name in exclude:
            continue
        # 按章号归一后的 key 比较（见 chapter_key 的说明）；
        # 再并上 source_file，避免不同书的同名章（如两本都有 Introduction）被错误合并
        key = f"{src}|{chapter_key(name)}"
        if cur is None or key != cur["_key"]:
            cur = {"title": name, "_key": key, "page_start": pno,
                   "source_file": src, "count": 1}
            chapters.append(cur)
        else:
            cur["count"] += 1

    # 按 key 再聚合一次：中间夹入的噪声页（如页码粘连书名「82防排烟工程」）会打断
    # 上面的连续性判断，把同一章拆成多段。这里按 key 归并，彻底消除该问题。
    merged: dict[str, dict] = {}
    order: list[str] = []
    for c in chapters:
        k = c["_key"]
        g = merged.get(k)
        if g is None:
            merged[k] = dict(c)
            order.append(k)
        else:
            g["pages"] = g.get("pages", [g["page_start"]]) + [c["page_start"]]
            g["count"] += c["count"]
            # 标题取更完整的一个（通常带「第X章」前缀且更长）
            if (re.match(r"^第", c["title"]) and len(c["title"]) > len(g["title"])):
                g["title"] = c["title"]

    out = [merged[k] for k in order if merged[k]["count"] >= min_pages]
    # 不按 page_no 排序：多本书各自从 1 编号，按页码排会跨书错乱；
    # order 本身已是识别顺序（= 按 source_file, page_no 排序后的阅读顺序）。
    for c in out:
        c.pop("_key", None)
        c.pop("pages", None)
    return out


def build_course(course: str, pages: list[dict]) -> dict:
    usable = [p for p in pages if (p.get("text") or "").strip()]
    if not usable:
        return {"course": course, "chapters": [], "note": "无可用文本（可能待 OCR）",
                "pages": len(pages), "usable_pages": 0}

    toc = parse_toc(usable)
    headings = scan_headings(usable)
    header_ch = chapters_from_header(usable)
    ch_headings = [h for h in headings if h["level"] == 1]
    sec_headings = [h for h in headings if h["level"] == 2]
    # 用 (source_file, page_no) 做键：多本书同处一个课程目录时 page_no 会各自从 1 开始，
    # 只用 page_no 当键会让后写入的书覆盖前面的书（章的来源与页码定位随之错位）。
    by_page = {(p.get("source_file"), p.get("page_no")): p for p in usable}

    chapters: list[dict] = []
    method = "none"
    if toc:
        method = "toc"
        # 用目录页的章列表为准，页码从正文标题里补
        by_num: dict[int, dict] = {}
        for h in ch_headings:
            by_num.setdefault(h["num"], h)
        for num, title in toc:
            h = by_num.get(num)
            chapters.append({
                "num": num,
                "title": title,
                "page_start": h["page_no"] if h else None,
                "page_label_start": h["page_label"] if h else None,
                "source_file": h["source_file"] if h else None,
                "sections": [],
                "from": "toc",
            })
        # 目录里没列出但正文里有的章，补进来
        known = {c["num"] for c in chapters}
        for h in ch_headings:
            if h["num"] not in known:
                chapters.append({
                    "num": h["num"], "title": h["title"],
                    "page_start": h["page_no"], "page_label_start": h["page_label"],
                    "source_file": h["source_file"], "sections": [], "from": "body",
                })
    elif header_ch:
        # 页眉章节名变化法：对没有「第X章」标题的教材（中英文都常见）最有效
        method = "header"
        for i, h in enumerate(header_ch, start=1):
            # 章来源直接取识别时记录的 source_file，不再靠 page_no 反查（见 by_page 说明）
            src = h.get("source_file")
            pg = by_page.get((src, h["page_start"])) or {}
            chapters.append({
                "num": i,
                "title": h["title"],
                "page_start": h["page_start"],
                "page_label_start": pg.get("page_label"),
                "source_file": src,
                "sections": [],
                "from": "header",
            })
    else:
        # 正文标题法：同一章的页眉会在该章每一页重复出现，必须按章号去重，
        # 否则同一本书里每一页都会自成一个"章"，章数虚高到上百个。
        seen_nums: set = set()
        dedup: list[dict] = []
        for h in ch_headings:
            if h["num"] in seen_nums:
                continue
            seen_nums.add(h["num"])
            dedup.append(h)
        method = "body" if dedup else "none"
        chapters = [{
            "num": h["num"], "title": h["title"],
            "page_start": h["page_no"], "page_label_start": h["page_label"],
            "source_file": h["source_file"], "sections": [], "from": "body",
        } for h in dedup]

    chapters.sort(key=lambda c: c["num"])

    # 章结束页 = 下一章起始页 - 1（仅当两章同属一本书；跨书页码相减毫无意义）
    book_last: dict = {}
    for p in usable:
        sf = p.get("source_file")
        if sf:
            book_last[sf] = max(book_last.get(sf, 0), p.get("page_no", 0))

    for i, c in enumerate(chapters):
        nxt = chapters[i + 1] if i + 1 < len(chapters) else None
        same_book = bool(nxt) and nxt.get("source_file") == c.get("source_file")
        if c.get("page_start") and same_book and nxt.get("page_start"):
            c["page_end"] = max(c["page_start"], nxt["page_start"] - 1)
        elif c.get("page_start"):
            # 本书最后一章：取本书的最大页码，取全集最大值会在多书时越界
            c["page_end"] = book_last.get(c.get("source_file"), c["page_start"])

    # 节归入所属章：按页码落在哪一章区间
    for h in sec_headings:
        pno = h.get("page_no")
        if pno is None:
            continue
        owner = None
        for c in chapters:
            # 必须同书：不同书的页码区间会重叠（各自从 1 编号），只比页码会认错归属
            if (c.get("source_file") == h.get("source_file")
                    and c.get("page_start") and c.get("page_end")
                    and c["page_start"] <= pno <= c["page_end"]):
                owner = c
                break
        if owner is None and chapters:
            owner = chapters[0] if pno < (chapters[0].get("page_start") or 10**9) else chapters[-1]
        if owner is not None:
            owner["sections"].append({
                "no": h.get("no"),
                "title": h["title"],
                "page_start": h["page_no"],
                "page_label": h.get("page_label"),
            })

    # 节区间
    for c in chapters:
        secs = sorted(c["sections"], key=lambda s: s.get("page_start") or 0)
        for i, s in enumerate(secs):
            nxt = secs[i + 1] if i + 1 < len(secs) else None
            if s.get("page_start") and nxt and nxt.get("page_start"):
                s["page_end"] = max(s["page_start"], nxt["page_start"] - 1)
            else:
                s["page_end"] = c.get("page_end")
        c["sections"] = secs
        c["section_count"] = len(secs)

    # 生成稳定 node_id
    course_key = re.sub(r"\s+", "", course)[:12]
    for c in chapters:
        c["node_id"] = f"{course_key}::ch{c['num']:02d}"
        # 标记前言/后置材料：抽取知识点时默认跳过（可用 --include-matter 强制包含）；
        # 附录/附表不算 matter，单独标 appendix（会被正常抽取，判据见 classify_kind）。
        c["kind"] = classify_kind(c.get("title", ""))
        for s in c["sections"]:
            safe = re.sub(r"[^\w\u4e00-\u9fff]+", "", str(s.get("no") or s["title"]))[:10]
            s["node_id"] = f"{c['node_id']}::{safe}"

    return {
        "course": course,
        "chapters": chapters,
        "pages": len(pages),
        "usable_pages": len(usable),
        "toc_found": bool(toc),
        "method": method,
        "chapter_count": len(chapters),
        "section_count": sum(len(c["sections"]) for c in chapters),
    }


def reclassify_appendix(dry_run: bool = False) -> int:
    """
    迁移：把**已存在骨架**里被判成 matter 的附录/附表章改判为 appendix。

    为什么做成工具而不是手工改 JSON：
      ① 这套骨架是 `build_skeleton` + `review_skeleton`（AI）两层产物；
      ② 一旦全量重建骨架，附录还会再次落回 matter —— 那时需要**同一个判据再跑一次**。
      做成工具才能"改一次、留证据、可重复"。

    ⚠️ 为什么只做 matter → appendix 的**单向**改判，不做别的：
      `classify_kind` 对「当代杰出青年科学文库」这类坏标题会返回 `body`，
      但那是 AI 复核**故意**判成 matter 的（它确实是丛书名/版权页噪声）。
      若按 classify_kind 全量重写 kind，就会把 AI 的判断覆盖掉 —— 骨架复核的成果
      会在重跑时丢掉。所以这里**只修附录/附表这一种确定的情况**。
    """
    if not SKELETON_FILE.exists():
        print(f"[FAIL] 骨架不存在：{SKELETON_FILE}")
        return 2
    sk = kb.load_json(SKELETON_FILE, {}) or {}
    flipped = []
    for course, info in (sk.get("courses") or {}).items():
        for ch in (info.get("chapters") or []):
            if ch.get("kind") != "matter":
                continue
            if classify_kind(ch.get("title", "")) == "appendix":
                flipped.append((course, ch.get("num"), ch.get("title"),
                                ch.get("page_start"), ch.get("page_end")))
                if not dry_run:
                    ch["kind"] = "appendix"
                    ch["kind_reason"] = ("附录/附表是数值表密集区，"
                                         "改判保留抽取（build_skeleton --reclassify-appendix）")
    print("=" * 74)
    print("附录归类迁移：附录类章 matter → appendix")
    print("=" * 74)
    if not flipped:
        print("  无需改判（没有任何 matter 章的标题命中附录/附表模式）")
        return 0
    for c, num, title, ps, pe in flipped:
        print(f"  ch{num:<4} {c:<22} {str(title)[:30]:<30} p{ps}-{pe}")
    print(f"\n  共 {len(flipped)} 章")
    if dry_run:
        print("\n（--dry-run：未写入）")
        return 0
    # 备份：改数据前先留底，出错可直接回滚
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = kb.LOG_DIR / f"skeleton_backup_{stamp}.json"
    kb.LOG_DIR.mkdir(parents=True, exist_ok=True)
    bak.write_text(SKELETON_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"\n  已备份：{bak}")
    kb.write_json(SKELETON_FILE, sk)
    print(f"  已写入：{SKELETON_FILE}")
    print("\n下一步：python -m firekb extract --check   # 看增量块数")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="构建知识骨架（教材章节结构）")
    ap.add_argument("--course", help="只处理某门课")
    ap.add_argument("--show", action="store_true", help="只显示结果，不重写 skeleton.json")
    ap.add_argument("--reclassify-appendix", action="store_true",
                    help="把已有骨架里 matter 的附录/附表章改判为 appendix（不重建骨架）")
    ap.add_argument("--dry-run", action="store_true", help="配合 --reclassify-appendix：只显示不改")
    args = ap.parse_args()

    if args.reclassify_appendix:
        return reclassify_appendix(dry_run=args.dry_run)


    if not PAGES_FILE.exists():
        print(f"[FAIL] 文本库不存在：{PAGES_FILE}，请先运行 parse_docs.py")
        return 2

    sys.path.insert(0, str(ROOT / "tools"))
    from kb import group_pages_by_course, load_courses, match_course  # type: ignore

    groups = group_pages_by_course()
    courses_cfg = load_courses()

    print("=" * 72)
    print("M4a 知识骨架构建")
    print("=" * 72)

    result: dict = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "教材/课件章节结构识别",
        "courses": {},
    }

    for course, pages in sorted(groups.items()):
        if args.course and args.course not in course:
            continue
        cfg = match_course(course) or {}
        info = build_course(course, pages)
        info["course_id"] = cfg.get("id", "")
        result["courses"][course] = info

        note = ""
        if not info["chapters"]:
            note = f"  [!] {info.get('note', '未识别到章节')}"
        print(f"\n▍{course}  ({(cfg or {}).get('id', '-')})")
        method_label = {"toc": "目录页解析", "header": "页眉章节名", "body": "正文标题识别",
                        "none": "未识别"}.get(info.get("method", "none"), "?")
        print(f"   页数 {info['pages']}（有文本 {info['usable_pages']}）  "
              f"章 {info.get('chapter_count', 0)}  节 {info.get('section_count', 0)}  "
              f"方式：{method_label}{note}")
        for c in info["chapters"][:12]:
            rng = (f"p{c['page_start']}-{c['page_end']}"
                   if c.get("page_start") else "页码未定位")
            print(f"     {c['node_id']:<20} {c['title'][:34]:<34} {rng:<14} 节 {len(c['sections'])}")
        if len(info["chapters"]) > 12:
            print(f"     ... 其余 {len(info['chapters']) - 12} 章")

    # 未被识别的课程（无文本，通常等 OCR）
    missing = [n for n in (c.get("name") for c in courses_cfg)
               if n and not any(n == k or n in k for k in result["courses"])]
    if missing:
        print("\n[!] 以下课程尚无可用文本，骨架暂缺（等 OCR 或放入课件后重跑）：")
        for m in missing:
            print(f"    - {m}")

    if not args.show:
        KP_DIR.mkdir(parents=True, exist_ok=True)
        import json
        SKELETON_FILE.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n骨架已写入：{SKELETON_FILE}")
    else:
        print("\n（--show 模式，未写入文件）")

    print("\n下一步：python tools\\extract_kp.py --course <课程名> 抽取知识点")
    return 0


if __name__ == "__main__":
    sys.exit(main())
