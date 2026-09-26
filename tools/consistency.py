# -*- coding: utf-8 -*-
"""
consistency.py —— 配置一致性检查
===================================

把「同一门课在不同环节叫什么、有哪些东西」摊成一张台账，一次看清**口径裂缝**。

为什么需要：口径不一致既不报错也不崩溃，**不检查就永远不会有人发现**，但它会让"课本"
给出过期或漏课的结论。台账要抓的就是这类静默裂缝，例如：
  · `courses.json` 有 7 门课，骨架只有 6 门，`通用规范` 有骨架却不在课表里；
  · `消防规划学` 在课表里但零素材、零骨架、零知识点；
  · 骨架的章页码范围只覆盖了 `火灾探测与报警系统` 的 3.1% 页（其余页没有章节归属）；
  · 复习产物是整库产物，快照时间可能早于最新知识点（"产物比数据旧"= 用户看到的是旧结论）。

判据分级：
  FAIL —— 数据自相矛盾（同一批数据里出现了互斥的事实），必须处理，退出码 3；
  WARN —— 口径裂缝/待决策/产物陈旧，需要人看一眼，不阻塞，退出码不变；
  INFO —— 现状陈述（数字本身就是结论）。
本脚本**只读**：不写任何产物、不修任何数据。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb  # noqa: E402
import build_skeleton  # noqa: E402  # 附录 / 非正文材料的唯一判据实现

# 素材根目录下这些顶层目录**不是课程**（避免把"录音"当成课来对账）
NON_COURSE_DIRS = {"录音", "课件", "生成图片", "temp", "tmp"}
# 素材真正按课存放的两级：10_原始归档/规范教材/<课名> 与 10_原始归档/课件/<课名>
MATERIAL_ROOTS = ("规范教材", "课件")
# 章页码范围覆盖率低于该值时告警（经验值：低于它就是"教材有页但骨架没归属"）
COVERAGE_WARN = 0.90


def _dirs(p: Path) -> list[str]:
    if not p.exists():
        return []
    return sorted(d.name for d in p.iterdir() if d.is_dir() and not d.name.startswith((".", "_")))


def material_dirs() -> list[str]:
    """磁盘上的素材课程目录（规范教材/ + 课件/ 去重，顺序稳定）。"""
    names: list[str] = []
    for root in MATERIAL_ROOTS:
        for d in _dirs(kb.ARCHIVE / root):
            if d not in names:
                names.append(d)
    return names


def material_from_pages(pages: list[dict]) -> dict[str, set[str]]:
    """
    **已入库文本的素材出处**（比磁盘更权威：它就是 M1 当时读到的东西）。
    取 source_file 的前两级目录作为素材目录名。
    """
    out: dict[str, set[str]] = {}
    for p in pages:
        rel = str(p.get("source_file") or "")
        parts = [x for x in rel.split("/") if x]
        top = "/".join(parts[:2]) if len(parts) >= 2 else (parts[0] if parts else "(未知)")
        out.setdefault(p.get("course") or "未分类", set()).add(top)
    return out


def _mtime(p: Path) -> str:
    if not p.exists():
        return "-"
    return datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")


def _norm_set(names) -> set[str]:
    """归一化课程名集合（走 kb 的同一套匹配，避免本脚本自造判定）。"""
    out = set()
    for n in names:
        cfg = kb.match_course(n)
        out.add((cfg or {}).get("name") or n)
    return out


def collect() -> dict:
    courses_cfg = kb.load_courses()
    cfg_names = [c.get("name") for c in courses_cfg if c.get("name")]

    pages = kb.load_jsonl(kb.PAGES_FILE)
    pages_by_course: dict[str, list[dict]] = {}
    for p in pages:
        pages_by_course.setdefault(p.get("course") or "未分类", []).append(p)

    skel = kb.load_json(kb.SKELETON_FILE, {}) or {}
    skel_courses = skel.get("courses") or {}

    kp = kb.load_jsonl(kb.KP_FILE)
    ev = kb.load_jsonl(kb.EVIDENCE_FILE)
    kp_by_course: dict[str, int] = {}
    for r in kp:
        kp_by_course[r.get("course") or "未分类"] = kp_by_course.get(r.get("course") or "未分类", 0) + 1
    ev_by_course: dict[str, int] = {}
    for r in ev:
        ev_by_course[r.get("course") or "未分类"] = ev_by_course.get(r.get("course") or "未分类", 0) + 1

    return {
        "cfg_names": cfg_names,
        "material_dirs": material_dirs(),
        "material_pages": material_from_pages(pages),
        "audio_dirs": _dirs(kb.ARCHIVE / "录音"),
        "pages_by_course": pages_by_course,
        "skel_courses": skel_courses,
        "kp_by_course": kp_by_course,
        "ev_by_course": ev_by_course,
        "kp_rows": len(kp), "ev_rows": len(ev), "page_rows": len(pages),
    }


def coverage(course: str, pages: list[dict], skel_course: dict) -> dict:
    """
    页码范围覆盖度（只看有正文的页）。**两个粒度同时给出，不能互相替代**：

      节覆盖 —— 每节的 [page_start, page_end] 并集（最细：回答"每页有没有节归属"）
      章覆盖 —— 每章的 [page_start, page_end] 并集（较粗：回答"每页有没有章归属"）

    为什么要两个：章覆盖只说明"这一页落在某个章里"，回答不了"每页是否真的归到了某一节"，
    两数可以差出三成（一门课章覆盖 94.2% 而节覆盖只有 63.4%）。只报一个数，
    读者会把粗口径当细口径用；所以这里把两个都摆出来，谁也别替代谁。
    """
    with_text = {p.get("page_no") for p in pages if (p.get("text") or "").strip()}
    sec_cov: set = set()
    ch_cov: set = set()

    def _span(a, b) -> set:
        if not (isinstance(a, int) and isinstance(b, int)):
            return set()
        if b < a:                              # 倒挂区间：review_skeleton 已加断言拦它，这里只容错显示
            a, b = b, a
        return {n for n in with_text if a <= n <= b}

    for ch in (skel_course.get("chapters") or []):
        secs = ch.get("sections") or []
        sec_span: set = set()
        for sec in secs:
            sec_span |= _span(sec.get("page_start"), sec.get("page_end"))
        own = _span(ch.get("page_start"), ch.get("page_end"))
        # 章覆盖：**以章自己声明的 [page_start, page_end] 为准**（没有就退回其节的并集）——
        # 一次统计里只用一个算法，避免与"节的并集"混着算、得出无法复现的数。
        ch_cov |= own or sec_span
        # 节覆盖：以节的并集为准（整章没有节时才退回章范围）
        sec_cov |= sec_span or own

    tot = len(with_text)

    def _t(s: set) -> tuple[int, int, float]:
        return len(s), tot, (len(s) / tot if tot else 0.0)

    return {"节": _t(sec_cov), "章": _t(ch_cov), "有文本页": tot}


def reference_ledger() -> dict:
    """
    规范条文库台账。

    为什么单列：若把若干部规范当成"一门课"，它的 course_id 只能靠猜，而每本书的页码各自
    从 1 重编，会被塞进同一个页空间。把素材目录整体识别为**规范条文库**（规范的合集）后，
    本函数把"每本规范各有多少页 / 知识点 / 证据"按**作品**摊开 ——
    这才是「拆成独立条目」在数据上的意义：引用落点是「哪本规范 + 第几页」，
    而不是「第 65 页」这种没有主语、指向不明的范围。

    ⚠️ 本函数**只读**：pages/kp 里仍写着原课名，作品归属是从 source_file 反查出来的。
    """
    lib = kb.reference_library()
    dirs = kb.reference_course_dirs()
    pages = kb.load_jsonl(kb.PAGES_FILE)
    kp = kb.load_jsonl(kb.KP_FILE)
    ev = kb.load_jsonl(kb.EVIDENCE_FILE)

    in_lib = [p for p in pages
              if p.get("course") in dirs or kb.is_reference_source(p.get("source_file"))]
    by_work: dict[str, dict] = {}
    for w in kb.reference_works():
        by_work[w["id"]] = {"work": w, "pages": 0, "live_pages": 0, "chars": 0, "sources": set(),
                            "page_nos": set(), "kp": 0, "ev": 0}
    unclaimed: dict[str, dict] = {}

    for p in in_lib:
        w = kb.resolve_reference(p.get("source_file"))
        src = str(p.get("source_file") or "")
        live = 1 if (p.get("text") or "").strip() else 0
        if w and w.get("id") in by_work:
            slot = by_work[w["id"]]
            slot["pages"] += 1
            slot["live_pages"] += live
            slot["chars"] += len(p.get("text") or "")
            slot["sources"].add(src)
            slot["page_nos"].add(p.get("page_no"))
        else:
            slot = unclaimed.setdefault(src, {"pages": 0, "live_pages": 0, "chars": 0, "kp": 0, "ev": 0})
            slot["pages"] += 1
            slot["live_pages"] += live
            slot["chars"] += len(p.get("text") or "")

    for r in kp:
        sf = r.get("source_file")
        if not kb.is_reference_source(sf):
            continue
        w = kb.resolve_reference(sf)
        if w and w.get("id") in by_work:
            by_work[w["id"]]["kp"] += 1
        else:
            unclaimed.setdefault(str(sf or ""), {"pages": 0, "live_pages": 0, "chars": 0,
                                                 "kp": 0, "ev": 0})["kp"] += 1

    ev_by_kp = {}
    for r in ev:
        ev_by_kp[r.get("kp_id")] = ev_by_kp.get(r.get("kp_id"), 0) + 1
    for r in kp:
        sf = r.get("source_file")
        if not kb.is_reference_source(sf):
            continue
        w = kb.resolve_reference(sf)
        n = ev_by_kp.get(r.get("kp_id"), 0)
        if w and w.get("id") in by_work:
            by_work[w["id"]]["ev"] += n

    return {
        "library": lib,
        "dirs": dirs,
        "works": by_work,
        "unclaimed": unclaimed,
        "page_rows": len(in_lib),
        "live_pages": sum(s["live_pages"] for s in by_work.values()),
        "source_count": len({s for slot in by_work.values() for s in slot["sources"]}),
        "duplicates": kb.reference_duplicates(),
    }


def main() -> int:
    d = collect()
    ref = reference_ledger()                  # 规范条文库口径
    ref_set = _norm_set(ref["dirs"])          # 已归入条文库的"课名"（如 通用规范）
    fails: list[str] = []
    warns: list[str] = []
    infos: list[str] = []

    cfg_set = _norm_set(d["cfg_names"])
    skel_set = _norm_set(d["skel_courses"].keys())
    pages_set = _norm_set(d["pages_by_course"].keys())
    kp_set = _norm_set(d["kp_by_course"].keys())
    mat_set = _norm_set(d["material_dirs"])
    ingested = _norm_set(d["material_pages"].keys())
    all_courses = sorted(cfg_set | skel_set | pages_set | kp_set | mat_set,
                         key=lambda s: (s not in cfg_set, s))

    print("=" * 108)
    print("FireKB 配置一致性台账（课表 / 素材 / 文本 / 骨架 / 知识点 / 证据 / 规范条文库 七个口径）")
    print("=" * 108)
    hdr = (f"{'课程':<26}{'课表':^6}{'磁盘素材':^10}{'已入库素材':^11}"
           f"{'文本页':^8}{'骨架':^6}{'知识点':^8}{'证据':^7}{'节覆盖':>9}{'章覆盖':>9}")
    print(hdr)
    print("-" * 108)
    for c in all_courses:
        pages_c = d["pages_by_course"].get(c)
        if pages_c is None:                    # 归一化后的键可能不同形，兜一层
            pages_c = next((v for k, v in d["pages_by_course"].items()
                            if kb.match_course(k) and (kb.match_course(k) or {}).get("name") == c), [])
        skel_c = d["skel_courses"].get(c) or next(
            (v for k, v in d["skel_courses"].items()
             if (kb.match_course(k) or {}).get("name") == c or k == c), None)
        cov = coverage(c, pages_c or [], skel_c or {}) if skel_c else None
        n_pages = len([p for p in (pages_c or []) if (p.get("text") or "").strip()])
        kpn = d["kp_by_course"].get(c, 0)
        evn = d["ev_by_course"].get(c, 0)
        # 课表列：已归入规范条文库的名字显示 n/a 而不是 ✗ ——
        # ✗ 会被读成"漏了一门课"，而规范条文库**本来就不在课表里**。
        # 用 ASCII 的 "n/a" 而不是中文：`^6` 按**字符数**补白，中文是双宽字符，
        # 会把这个 6 格宽的列撑成 9 格、整张表错位（这是显示问题，不是数据问题）。
        in_table = "n/a" if c in ref_set else ("✓" if c in cfg_set else "✗")
        print(f"{c:<26}{in_table:^6}"
              f"{('✓' if c in mat_set else '✗'):^10}{('✓' if c in ingested else '✗'):^11}{n_pages:^8}"
              f"{('✓' if c in skel_set else '✗'):^6}{kpn:^8}{evn:^7}"
              f"{(f'{cov[chr(33410)][2]*100:.1f}%' if cov else '—'):>9}"
              f"{(f'{cov[chr(31456)][2]*100:.1f}%' if cov else '—'):>9}")
        # ---- 逐门课的判据 ----
        if c in skel_set and c not in cfg_set:
            if c in ref_set:
                # 它不是"身份不明的课"，而是规范条文库的素材目录 —— 见下方条文库台账
                infos.append(f"{c}：不在课表，但已归入**规范条文库**口径 —— "
                             f"逐本规范见下方台账，不再按课对账")
            else:
                warns.append(f"[口径] {c}：有骨架但**不在课表 courses.json** —— 它的 course_id 只能靠猜测，"
                             f"请决定是补进课表还是并入其它课")
        if c in cfg_set and c not in pages_set:
            warns.append(f"[缺步] {c}：课表里有，但**没有任何文本页**（M1/M2 未产出）→ 该课目前无内容可抽")
        if c in kp_set and c not in skel_set:
            fails.append(f"[矛盾] {c}：有知识点却**没有骨架**（知识点缺章节归属，复习产物无法归位）")
        if c in pages_set and c not in skel_set and n_pages:
            warns.append(f"[缺步] {c}：有 {n_pages} 页文本但没有骨架 → M4a 未跑，M4b 抽取会走退化路径")
        if cov:
            same = cov["节"][:2] == cov["章"][:2]
            for gran in ("节", "章"):
                got, tot, pct = cov[gran]
                if tot and pct < COVERAGE_WARN and not (same and gran == "章"):
                    tail = "（节/章同值）" if same else ""
                    warns.append(f"[覆盖] {c}：{gran}页码范围只覆盖 {got}/{tot} 页 = {pct*100:.1f}%"
                                 f"（其余页无{gran}归属，抽取时会被塞进退化块）{tail}")
        if skel_c and c not in kp_set:
            warns.append(f"[缺步] {c}：有骨架但**零知识点** → M4b 未跑")

    # ---- 规范条文库口径 ----
    #  为什么单列一张表：若把多本规范当成"一门课"，几本书的页码会被塞进同一个页空间
    #  —— 而**每本书的页码都从 1 重编**。拆成逐本条目后，"第 65 页"这种落点才有唯一含义
    #  （哪本规范的第 65 页）。
    if ref["library"] or ref["works"]:
        lib = ref["library"]
        print("\n" + "-" * 108)
        print(f"【规范条文库】{(lib.get('name') or '')}"
              f"（目录：{(lib.get('course_dir') or '-')}；"
              f"口径：{lib.get('role') or 'reference'} —— 不是课程，不进课时统计）")
        print(f"{'规范ID':<14}{'编号':<24}{'名称':<28}{'来源':>5}{'页':>6}{'字符':>9}{'知识点':>7}{'证据':>6}{'页码范围':>10}")
        print("-" * 108)
        for wid, slot in ref["works"].items():
            w = slot["work"]
            # ⚠️ 口径必须写清楚：`页` 这里取**有正文的页**，与上方课程表同口径
            #    （课程表数的也是有正文页；这里若改成数全部页行，就会与课程表差出
            #     一行，看起来像数据矛盾 —— 一个数字两种口径最容易误导读者）。
            n_src = len(slot["sources"])
            if n_src <= 1:
                nos = slot["page_nos"]
                rng = f"1..{max(nos)}" if nos else "—"
            else:
                rng = "多来源"
            print(f"{wid:<14}{str(w.get('code') or ''):<24}{str(w.get('name') or ''):<28}"
                  f"{n_src:>5}{slot['live_pages']:>6}{slot['chars']:>9}{slot['kp']:>7}{slot['ev']:>6}{rng:>10}")
        print("-" * 108)
        print(f"条文库合计：{ref['page_rows']} 页行 / {ref['live_pages']} 页有正文"
              f"（{len(ref['works'])} 本规范，{ref['source_count']} 个来源文件）")

        for wid, slot in ref["works"].items():
            w = slot["work"]
            if slot["pages"] == 0:
                warns.append(f"[条文库] {wid} {w.get('name')}：已在 references.json 声明，"
                             f"但 pages.jsonl 里**一页都匹配不到** → match 串写错了，或该规范尚未解析入库")
        for src, slot in ref["unclaimed"].items():
            warns.append(f"[条文库] 未声明的作品：{src.split('/')[-1][:60]} "
                         f"（{slot['pages']} 页 / {slot['kp']} 知识点）"
                         f" → 该来源既不属于任何一门课、也不在 references.json 的 works 里，"
                         f"引用落点无从定位；请补进 references.json")
        for dup in ref["duplicates"]:
            infos.append(f"[条文库] 重复来源已登记（**尚未清理**）：{dup.get('work')} 的 "
                         f"{dup.get('drop')} 版与 {dup.get('keep')} 版是同一部文本的两种格式 ⇒ "
                         f"以 {dup.get('keep')} 版为准；清理前须先备份（删行不可逆）")

    # ---- 实验批次登记 ----
    #  为什么要在台账里报：实验批次的知识点 status 也是 verified，
    #  不报出来就会被当成"该课已经抽好了"——它们会静默流进 02_背诵卡.md。
    batches = kb.load_experimental_batches()
    if batches:
        kp_rows = kb.load_jsonl(kb.KP_FILE)
        hit: dict[str, list[dict]] = {}
        for r in kp_rows:
            b = kb.is_experimental_kp(r)
            if b:
                hit.setdefault(b.get("id"), []).append(r)
        for b in batches:
            bid = b.get("id")
            rows = hit.get(bid, [])
            exp = b.get("expected_kp")
            tag = "实验批次" if rows else "★实验批次（一条都没匹配到）"
            infos.append(f"[{tag}] {bid} {b.get('title')}：命中 {len(rows)} 条 kp"
                         f"（登记 expected_kp={exp}）→ 在复习产物中**默认排除**"
                         f"（见 00_配置/experimental-batches.json），不是该课的完整考点")
            if rows and exp is not None and len(rows) != exp:
                warns.append(f"[实验批次] {bid}：登记 expected_kp={exp}，实际命中 {len(rows)} 条 —— "
                             f"批次已漂移（可能是重跑过/手工改过），请核对登记是否该更新")
            if not rows:
                warns.append(f"[实验批次] {bid}：登记了却**一条都匹配不到**（course/prefix/created_at "
                             f"对不上）→ 要么数据变了、要么登记过期；不影响数据，但标注已失效")

    # ---- 被 kind=matter 跳过的页 ----
    #  为什么必须报：`extract_kp` 默认跳过 kind=matter 的章，而这个跳过**完全静默** ——
    #  数值最密集的附表就这样消失，没有任何地方会提示。
    #
    #  ⚠️ 判据只用**一处**：`build_skeleton.classify_kind`。
    #     不要另造"数字密度 ≥ 12% 就算数值表"这类判据：`Author Index` 数字密度 36.4%、
    #     `Subject Index` 23.4% —— 索引本来就是"名字+页码"，数字密得很，但它确实是非正文材料。
    #     密度分辨不了"页码数字"与"数值表数字"；同一判据有两套实现必然漂移，所以不自己造一套。
    skel_all = kb.load_json(kb.SKELETON_FILE, {}) or {}
    pages_all = kb.load_jsonl(kb.PAGES_FILE)
    page_lookup: dict[tuple[str, int], dict] = {}
    for p in pages_all:
        page_lookup[(p.get("course") or "", p.get("page_no"))] = p
    skipped_total = 0
    appendix_pages = 0
    appendix_items: list[str] = []
    for cname, cinfo in (skel_all.get("courses") or {}).items():
        for ch in (cinfo.get("chapters") or []):
            if ch.get("kind") != "matter":
                continue
            cs, ce = ch.get("page_start"), ch.get("page_end")
            if not (isinstance(cs, int) and isinstance(ce, int)):
                continue
            hit = [page_lookup[(cname, n)] for n in range(cs, ce + 1)
                   if (cname, n) in page_lookup]
            skipped_total += len(hit)
            if build_skeleton.classify_kind(ch.get("title", "")) == "appendix":
                appendix_pages += len(hit)
                digits = sum(sum(1 for c in (p.get("text") or "") if c.isdigit()) for p in hit)
                chars = sum(len(p.get("text") or "") for p in hit) or 1
                appendix_items.append(
                    f"{cname} ch{ch.get('num')}「{ch.get('title')}」{len(hit)} 页"
                    f"（数字占比 {digits/chars:.1%}）")
    if skipped_total:
        infos.append(f"被 kind=matter 跳过的页共 {skipped_total} 页（抽取时默认跳过，"
                     f"可用 extract --include-matter 强制包含）")
    for s in appendix_items:
        warns.append(f"[附录] **附录/附表被当成非正文材料整章跳过**：{s} —— "
                     f"附录常是数值表最密集的地方（数字占比可达 50% 以上），"
                     f"跳过它等于丢掉最该背的数值。请把该章判为 kind=appendix 后正常抽取；"
                     f"**重跑骨架后这 {appendix_pages} 页即恢复**"
                     f"（重跑会改块边界，需先确认再动抽取，不要直接 --force）")

    # ---- 页码双轨 ----
    #  page_no 是 PDF 物理页，书上印的是印刷页码，两者差一个 offset（每门课不同）。
    #  没有 offset 的课，引用只能写"PDF pN"——人按书上的页码去找会找错页。
    offs = (kb.load_page_offsets().get("courses") or {})
    if offs:
        ok_c, miss_c, mixed_c = [], [], []
        for cname, o in sorted(offs.items()):
            if o.get("mixed"):
                mixed_c.append(cname)
            elif isinstance(o.get("offset"), int):
                ok_c.append(f"{cname}({o['offset']:+d})")
            else:
                miss_c.append(cname)
        if ok_c:
            infos.append(f"[页码双轨] 课程级 offset 已确定（印刷页 = 物理页 + offset）：{'、'.join(ok_c)}")
        if mixed_c:
            warns.append(f"[页码双轨] 这些课**不能给课程级 offset**（目录下多本书、各自偏移不同）："
                         f"{'、'.join(mixed_c)} —— 引用必须带 file_id 走文件级 offset，"
                         f"否则会指错页（见 00_配置/page-offsets.json）")
        if miss_c:
            warns.append(f"[页码双轨] 这些课**没有 offset**，引用只能写「PDF pN」：{'、'.join(miss_c)} "
                         f"—— 建议跑 python -m firekb offsets --write，测不准的手工登记")

    # ---- 素材目录 vs 课表 ----
    for m in d["material_dirs"]:
        if _norm_set([m]).isdisjoint(cfg_set | skel_set):
            warns.append(f"[口径] 素材目录 10_原始归档/{MATERIAL_ROOTS[0]}/{m} 在课表与骨架里都找不到对应课程名")
    for c in sorted(pages_set):
        if c not in mat_set:
            infos.append(f"{c}：本机 10_原始归档 下无素材目录（素材可能不在当前环境），"
                         f"但已入库文本，不影响使用")

    # ---- 每门课的素材出处（已入库口径） ----
    for course, srcs in sorted(d["material_pages"].items()):
        infos.append(f"{course} 文本来自：{', '.join(sorted(srcs))}")

    # ---- 录音 ----
    if d["audio_dirs"]:
        infos.append(f"录音目录（权重 3 · 仅供参考，不作引用来源）：{', '.join(d['audio_dirs'])}")

    # ---- 复习产物新鲜度 ----
    prod_files = sorted([p for p in kb.REVIEW_DIR.glob("*") if p.is_file()],
                        key=lambda p: p.stat().st_mtime, reverse=True) if kb.REVIEW_DIR.exists() else []
    if not prod_files:
        warns.append("[产物] 40_复习产物 为空 → M6 未跑")
    else:
        newest = prod_files[0]
        for name, src in (("kp.jsonl", kb.KP_FILE), ("audit.jsonl", kb.AUDIT_FILE)):
            if src.exists() and newest.stat().st_mtime < src.stat().st_mtime:
                warns.append(f"[陈旧] 复习产物最新一件 {newest.name}({_mtime(newest)}) 早于 "
                             f"{name}({_mtime(src)}) → 用户看到的是**旧结论**，请重跑 M6")
        infos.append(f"复习产物 {len(prod_files)} 件，最新 {newest.name} @ {_mtime(newest)}")

    # ---- 引擎可用性 ----
    try:
        from engines import all_engines, preflight_report
        eng = all_engines()
        for kind, name, missing in preflight_report(eng):
            if not missing:
                infos.append(f"引擎 {kind}：{name} 可用")
            else:
                warns.append(f"[引擎] {kind}={name} 缺少依赖：{missing}（本机可能不跑这一步）")
    except Exception as exc:
        warns.append(f"[引擎] 无法加载引擎层：{type(exc).__name__}: {exc}")

    # ---- 汇总 ----
    print("-" * 108)
    print(f"文本行 {d['page_rows']} ｜ 知识点 {d['kp_rows']} ｜ 证据 {d['ev_rows']} ｜ "
          f"课表 {len(d['cfg_names'])} 门 ｜ 骨架 {len(d['skel_courses'])} 门")
    print(f"关键文件时间：pages {_mtime(kb.PAGES_FILE)} ｜ skeleton {_mtime(kb.SKELETON_FILE)} ｜ "
          f"kp {_mtime(kb.KP_FILE)} ｜ audit {_mtime(kb.AUDIT_FILE)}")

    def _dump(title: str, items: list[str]) -> None:
        print(f"\n{title}（{len(items)} 条）")
        if not items:
            print("   （无）")
            return
        for i, s in enumerate(items, 1):
            print(f"  {i:>2}. {s}")

    _dump("FAIL · 数据自相矛盾（必须处理）", fails)
    _dump("WARN · 口径裂缝 / 待决策 / 可疑陈旧", warns)
    _dump("INFO · 现状陈述", infos)

    print("\n" + "=" * 108)
    if fails:
        print(f"结论：存在 {len(fails)} 项矛盾，退出码 3")
        return 3
    print(f"结论：无矛盾（{len(warns)} 条待决策项，见上）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
