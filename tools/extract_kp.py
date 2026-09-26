# -*- coding: utf-8 -*-
"""
M4b · 知识点抽取（核心模块）
==============================
按知识骨架切块，从教材/课件/转写原文中抽取可独立成立的知识点，
并为每个知识点绑定**逐字引用的证据**（文件 + 页码 + 原文摘录）。

防幻觉的核心设计 —— 引用可机器校验：
  抽取时强制要求 quote 为「原文连续片段，逐字摘录，不得改写」；
  落盘前用归一化字符串匹配检查该摘录是否真的存在于原文中。
  校验不通过的引用会被标记 quote_verified=false，在 M5 阶段优先送人工复核。
  这一步把「AI 说它引用了原文」变成「原文里确实有这句话」，是可自动执行的事实核查。

用法：
  python tools\\extract_kp.py --check                    # 自检与规模估算
  python tools\\extract_kp.py --course "防排烟工程" --limit 3   # 小规模试跑
  python tools\\extract_kp.py                            # 处理全部待抽取块
  python tools\\extract_kp.py --force                    # 忽略进度全部重跑

约束：
  * 断点续跑：每个块处理完立即追加落盘，中断只损失当前块。
  * 默认用 DeepSeek Flash（配合 disable_thinking，速度快、成本低）。
  * 知识点名称与定义用中文输出；quote 保持原文语言（英文书保留英文原句）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(os.environ.get("FIREKB_ROOT") or Path(__file__).resolve().parents[1])
TEXT_DIR = ROOT / "20_文本库"
KP_DIR = ROOT / "30_知识点"
LOG_DIR = ROOT / "90_日志"

sys.path.insert(0, str(ROOT / "tools"))

import kb  # noqa: E402

# 单块字符上限。**默认值取自 kb（单一来源）**，不要在这里写字面量：
# 硬编码一个字面量就会与 kb.CHUNK_MAX_CHARS 形成两个名字相近、含义却不同的数
# （11000 与 12000），复算者极易用错参数；常量定义见 kb.py。
MAX_CHARS = int(os.environ.get("FIREKB_CHUNK_CHARS") or kb.CHUNK_EXTRACT_MAX_CHARS)

SYSTEM_PROMPT = """你是消防工程专业课的助教，负责把教材原文整理成可复习的知识点。

铁律（违反即为无效输出）：
1. 只从给定原文中抽取，绝不引入原文没有的内容。
2. 每条证据的 quote 必须是原文中的**连续片段、逐字摘录**，不得改写、不得拼接、不得翻译。
   若原文是英文，quote 就保持英文原样。
3. 数字（温度、时间、距离、浓度、尺寸等）必须原样来自原文，不得换算、不得推算；
   原文没给数字就写空数组。
4. 无法在原文中找到依据的结论，宁可不写。

输出必须是合法 JSON，不要任何解释文字。"""

USER_TEMPLATE = """课程：{course}
章节位置：{chapter}{section}
原文（【】内为该段所在页码，引用时请使用同样的页码标记）：

{text}

请抽取本章节的知识点，按下面的 JSON 结构输出（不要输出 JSON 以外的内容）：

{{
  "knowledge_points": [
    {{
      "name": "知识点名称：中文名词短语，4-20 字，不含句号",
      "definition": "一句话定义：不看原文也能读懂，40-120 字",
      "type": "概念|原理|方法|数据|易错点 之一",
      "aliases": ["常见别名或英文名，可为空数组"],
      "importance": 1,
      "evidence": [
        {{
          "page_label": "第20页",
          "quote": "原文连续片段，20-80 字，逐字摘录",
          "evidence_type": "定义|讲解|例题|数据|图表"
        }}
      ],
      "numbers": ["原文中的关键数值+单位，如 350℃、0.5m/s"],
      "note": "易错提示或适用范围；原文没有就写空字符串"
    }}
  ]
}}

要求：
- 数量控制在 5-15 条，宁缺毋滥；重复或过于琐碎的不要抽。
- 每个知识点至少 1 条 evidence；page_label 必须是上面原文里出现过的页码标记。
- importance 为 1-5 的整数（5 表示最该背、最该考）。
"""


# ---------------------------------------------------------------------------
# 引用核验 —— 实现放在 kb.py 共享，这里只保留薄封装，避免两套实现漂移
# ---------------------------------------------------------------------------
def normalize_for_match(s: str) -> str:
    """归一化用于引用校验（实现见 kb.normalize_for_match）。"""
    return kb.normalize_for_match(s)


def verify_quote(quote: str, body: str) -> tuple[str, str, str]:
    """
    校验引用是否真的存在于原文中（实现见 kb.verify_quote）。
    返回 (tier, matched_text, note)；tier 为 exact / partial / fabricated / not_found，
    **只有 exact 算通过**（partial 进人工复核队列、不计通过率）。
    """
    return kb.verify_quote(quote, body)


def split_body_into_paged_text(pages: list[dict]) -> str:
    return "\n\n".join(f"【{p.get('page_label')}】\n{(p.get('text') or '').strip()}"
                       for p in pages if (p.get("text") or "").strip())


def page_runs(pages: list[dict], max_chars: int) -> list[list[dict]]:
    """
    先把页序列按**页码连续性**切成若干连续段，再在每段内按长度切窗。

    为什么需要它：页码归属唯一化之后，一个块拥有的页可能被别的块挖出若干空洞
    （例如章前导语 10-12 + 节 13-16 + 章尾 17-20）。若直接对整个序列切窗，
    窗口的 `page_start..page_end` 会跨过不属于它的页，让「页范围 → 归属」失真。
    先切连续段可保证**每个窗口的页范围恰好等于它实际拥有的页**。
    """
    out: list[list[dict]] = []
    run: list[dict] = []
    for p in pages:
        pn = p.get("page_no")
        if run and pn != (run[-1].get("page_no") or 0) + 1:
            out.extend(page_windows(run, max_chars))
            run = []
        run.append(p)
    if run:
        out.extend(page_windows(run, max_chars))
    return out


def chunks_from_skeleton(course: str, pages: list[dict], skel: dict,
                         max_chars: int = MAX_CHARS,
                         include_matter: bool = False) -> list[dict]:
    """
    按骨架的章 / 节切块；超出长度上限时再按页切分。
    没有骨架的课程退化为「按页顺序聚合」（kb.build_chunks），**同样按文件分组**。

    两条切块口径，都直接决定块的主键是否唯一：
      ① **按 source_file 分组建块**。若以页号 `{page_no: p}` 为键而不带文件，
         同一门课挂多本教材（每本书的页码都从 1 重编）时会互相覆盖：全库 3,137 个
         页行只会剩下 1,995 个唯一页码，**1,142 行（36%）被静默丢弃**，而且同一章内
         会混着 2-3 本书的页。因此每本书独立解析页码，主键带文件标签
         （`{course}::f{TAG}::pA-B`），课件与教材都能入课。
      ② **页码归属唯一化**。章与章之间、节与节之间、节与所属章之间做「先声明者得」
         的归属划分，并把节的页范围**夹回所属章**内：若直接取节自己声明的范围，
         节会越界到下一章。⇒ 每个 (文件, 页码) 恰属一个块，从根上消除
         「两块页范围相同 ⇒ 文本逐字节相同 ⇒ 主键碰撞」。
         并以 `kb.assign_chunk_ids` 的 fail-loud 唯一性断言兜底。
    """
    info = (skel.get("courses") or {}).get(course) or {}
    chapters = info.get("chapters") or []
    chunks: list[dict] = []

    # ① 按文件分组：{file_id: {page_no: page}}（file_id 缺失时回退用 source_file 分组）
    by_file: dict = {}
    for p in pages:
        by_file.setdefault(p.get("file_id") or p.get("source_file"), {})[p.get("page_no")] = p

    def add(chapter_title, section_title, node_id, seg_pages):
        if not seg_pages:
            return
        text = split_body_into_paged_text(seg_pages)
        if len(text) < 200:
            return
        chunks.append({
            "course": course,
            "chapter": chapter_title,
            "section": section_title,
            "node_id": node_id,
            "file_id": seg_pages[0].get("file_id"),
            "page_start": seg_pages[0].get("page_no"),
            "page_end": seg_pages[-1].get("page_no"),
            "page_label_start": seg_pages[0].get("page_label"),
            "page_label_end": seg_pages[-1].get("page_label"),
            "source_file": seg_pages[0].get("source_file"),
            "chars": len(text),
            "text": text,
            "pages": seg_pages,
        })

    if chapters:
        # ②-a 章级归属：同一 (文件, 页码) 只归**第一个**声明它的章
        # （骨架里相邻两章的页范围可能重叠，都声称拥有同一页）
        claimed: dict = {}
        for ci, ch in enumerate(chapters):
            if not include_matter and ch.get("kind") == "matter":
                continue
            cs, ce = ch.get("page_start"), ch.get("page_end")
            if not cs or not ce:
                continue
            for fid, fmap in by_file.items():
                owner = claimed.setdefault(fid, {})
                for pn in range(int(cs), int(ce) + 1):
                    if pn in fmap and pn not in owner:
                        owner[pn] = ci

        for ci, ch in enumerate(chapters):
            # 跳过前言/符号表/索引等非正文材料（骨架已标记 kind=matter）
            if not include_matter and ch.get("kind") == "matter":
                continue
            cs, ce = ch.get("page_start"), ch.get("page_end")
            if not cs or not ce:
                continue
            cs, ce = int(cs), int(ce)
            secs = ch.get("sections") or []
            for fid, fmap in by_file.items():
                owner = claimed.get(fid, {})
                ch_pages = [fmap[pn] for pn in range(cs, ce + 1)
                            if pn in fmap and owner.get(pn) == ci]
                ch_pages = [p for p in ch_pages if (p.get("text") or "").strip()]
                if not ch_pages:
                    continue
                if len(split_body_into_paged_text(ch_pages)) <= max_chars:
                    add(ch["title"], None, ch.get("node_id"), ch_pages)
                    continue
                if secs:
                    # ②-b 节级归属：把节范围**夹回所属章**，节之间「先声明者得」
                    sec_owner: dict = {}
                    for si, s in enumerate(secs):
                        ss = int(s.get("page_start") or cs)
                        se = int(s.get("page_end") or ce)
                        ss, se = max(ss, cs), min(se, ce)   # 夹回所属章：少了这一步，节会越界到下一章
                        if se < ss:
                            continue                        # 范围倒置（如 p10-8）直接跳过
                        for pn in range(ss, se + 1):
                            if pn in fmap and pn not in sec_owner:
                                sec_owner[pn] = si
                    in_sec: set = set()
                    for si, s in enumerate(secs):
                        sp = [fmap[pn] for pn in sorted(sec_owner)
                              if sec_owner[pn] == si and pn in fmap]
                        sp = [p for p in sp if (p.get("text") or "").strip()]
                        if not sp:
                            continue
                        for p in sp:
                            in_sec.add(p.get("page_no"))
                        if len(split_body_into_paged_text(sp)) <= max_chars:
                            add(ch["title"], s.get("title"), s.get("node_id"), sp)
                        else:
                            for part in page_runs(sp, max_chars):
                                add(ch["title"], s.get("title"), s.get("node_id"), part)
                    # 章内不属于任何节的部分（章首导语 / 节间空隙），按连续段切窗
                    rest = [p for p in ch_pages if p.get("page_no") not in in_sec]
                    for part in page_runs(rest, max_chars):
                        add(ch["title"], None, ch.get("node_id"), part)
                else:
                    for part in page_runs(ch_pages, max_chars):
                        add(ch["title"], None, ch.get("node_id"), part)
    else:
        # 无骨架的课程：仍**按文件分别**聚合，避免多本书页码互相覆盖
        for fid, fmap in by_file.items():
            fpages = [fmap[pn] for pn in sorted(fmap)]
            for c in kb.build_chunks(fpages, max_chars=max_chars):
                seg = [fmap[pn] for pn in range(c["page_start"], c["page_end"] + 1) if pn in fmap]
                add(c.get("chapter"), c.get("section"), None, seg)

    # 主键为内容/位置派生（`{course}::f{TAG}::p{start}-{end}`，不含位置序号 `{i:04d}`），
    # 返回前断言全局唯一（碰撞用内容派生后缀消歧，消歧后仍碰撞即 fail loudly）。
    return kb.assign_chunk_ids(chunks)


def page_windows(pages: list[dict], max_chars: int) -> list[list[dict]]:
    """把页序列切成不超过 max_chars 的窗口。"""
    out: list[list[dict]] = []
    cur: list[dict] = []
    size = 0
    for p in pages:
        ln = len(p.get("text") or "") + 20
        if cur and size + ln > max_chars:
            out.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += ln
    if cur:
        out.append(cur)
    return out


def stale_state_keys(state, chunks: list[dict]) -> tuple[list[str], list[str]]:
    """
    陈旧主键守卫的探测器。

    返回 (stale, orphans)：
      stale   —— state 里的**旧格式主键**，按页范围能对上当前的块：不迁移就会把这些
                 已处理过的块重抽一遍（→ 重复知识点），必须拒跑。
      orphans —— 对不上任何块的键（骨架变更 / 课程消失），不构成重复风险，只告警。

    判据以「当前 chunk_id 集合」为基准：不要用 `count('::')` 那类结构判据，
    否则新格式的消歧后缀 `course::p30-31::h1a2b` 会被误判成旧键。

    主键加上文件标签段后，**不带标签的中格式键**（`course::pA-B`）仍要按
    「同课程 + 同页范围」判为 stale：否则一次口径升级就会静默变成「全量重抽」，
    而重抽是可观测的破坏性动作，必须由人显式裁定（`--force` 或先归档 state），
    不能由守卫放行。
    """
    ids = {c["chunk_id"] for c in chunks}
    # 标签无关的 (course, pA-B) 索引，用于识别中格式旧键
    ranges = {(c["course"], c["page_start"], c["page_end"]) for c in chunks}
    stale: list[str] = []
    orphans: list[str] = []
    for k in state.done:
        if k in ids:
            continue
        parsed = kb.parse_chunk_key_full(k)
        if parsed:
            course, ps, pe, ftag = parsed
            if kb.make_chunk_key(course, ps, pe, ftag) in ids:
                stale.append(k)
                continue
            if ftag is None and (course, ps, pe) in ranges:
                stale.append(k)
                continue
        orphans.append(k)
    return stale, orphans


def migrate_state(chunks: list[dict]) -> int:
    """
    一次性迁移：把 `state_extract.json` 的旧位置序号主键改成内容派生主键，
    并补算内容指纹。零 API。

    迁移逻辑本体在 `kb.migrate_state_keys()`（与主流程共用同一套键/指纹原语，避免两套漂移），
    这里只负责：备份 → 打印「新旧键 1:1 映射表 + fp」→ 失败即拒写。

    纪律：**只改「键」与「fp」，`at` / `chars` / `kps` 三个原有值原样保留**（逐一对照打印）；
    只要有键无法解析或对应块已不存在（unresolved）就打印 [FAIL] 并 return 1，**不写回**。
    """
    path = kb.KP_DIR / "state_extract.json"
    print("=" * 72)
    print(f"state 主键迁移          {path}")
    print("=" * 72)
    if not path.exists():
        print(f"[FAIL] 文件不存在，无需迁移：{path}")
        return 2

    state = kb.State("extract")
    print(f"迁移前键数：{len(state.done)}    本次可识别的块：{len(chunks)}")

    # 备份必须在任何写操作之前
    backup_dir = kb.LOG_DIR / "_备份"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = backup_dir / f"state_extract.{stamp}.json"
    shutil.copy2(path, backup)
    print(f"已备份：{backup}")

    by_id = {c["chunk_id"]: c for c in chunks}
    # 键带文件标签后 (course, pA-B) 不再唯一（多本书可能共用同一页范围），
    # 索引因此带上文件标签；同时保留无标签的索引形状供 kb.migrate_state_keys 兜底。
    page_index = {(c["course"], kb.make_file_tag(c.get("file_id")), c["page_start"], c["page_end"]): c
                  for c in chunks}
    rep = kb.migrate_state_keys(state, chunk_by_key=by_id, page_index=page_index)

    print("\n新旧键 1:1 映射表（旧键 -> 新键 | fp | at / chars / kps 原样保留）：")
    for old, new, fp, at, chars, kps, tag in rep["rows"]:
        print(f"  [{tag}] {old}")
        print(f"        -> {new}")
        print(f"           fp={fp}   at={at}   chars={chars}   kps={kps}")

    unresolved = rep["unmatched"]
    if unresolved:
        print(f"\n[FAIL] {len(unresolved)} 个键无法解析或对应块已不存在，**未写回**：")
        for k in unresolved[:20]:
            print(f"   - {k}")
        print("（state 文件保持原样；请先确认骨架/页范围是否已变）")
        return 1

    state.save()
    print(f"\n已写回：{path}")
    print(f"迁移完成：{rep['total']} -> {len(state.done)} 个键（迁移 {rep['migrated']} 个，"
          f"补算指纹 {rep['fp_filled']} 个，合并重复 {rep['dropped_dup']} 个）")
    print("at / chars / kps 三个原有字段未改动（见上表逐一对照）")
    return 0


def append_with_retry(path, rows, head: str = "", tries: int = 5) -> None:
    """
    逐文件重试落盘（瞬时 OSError 不应中断长时间的全量抽取）。

    ⚠️ 必须**按单个文件**重试，绝不把两个 append 包在同一个重试块里 ——
    否则「kp 已写成功、evidence 失败」时会连 kp 一起重写，直接产出重复知识点。

    残余风险：若 kp 写成功而 evidence 连试 tries 次仍失败，该块不会被 mark，
    重跑会重新抽取并产生**同名不同 kp_id** 的重复条目；由 M5 的重复检测
    （同课程同名 / 同页范围）兜底发现。
    """
    for k in range(1, tries + 1):
        try:
            kb.append_jsonl(path, rows)
            return
        except OSError as exc:
            if k == tries:
                raise
            print(f"{head}  [WARN] 落盘失败 {type(exc).__name__}: {str(exc)[:80]}，"
                  f"{3 * k}s 后重试 {k}/{tries}")
            time.sleep(3 * k)


def next_kp_index(existing: list[dict], course_id: str) -> int:
    mx = 0
    for k in existing:
        if k.get("course_id") != course_id:
            continue
        m = re.search(r"(\d+)$", str(k.get("kp_id", "")))
        if m:
            mx = max(mx, int(m.group(1)))
    return mx + 1


def extract_chunk(chunk: dict, course_id: str, start_index: int, verbose: bool = False,
                  run_id: str | None = None):
    """抽一个块。`run_id` 会写进该块产生的每条 kp/evidence —— 供批次追溯与安全清理。"""
    prompt = USER_TEMPLATE.format(
        course=chunk["course"],
        chapter=chunk.get("chapter") or "（未分章）",
        section=(" / " + chunk["section"]) if chunk.get("section") else "",
        text=chunk["text"],
    )
    # max_tokens 8000 → 16000：大块（原文约 11,000 字）最容易触发截断，症状是
    #   `LLM 调用失败（3 次）：无法解析为 JSON。原始返回前 300 字：'{\n  "knowledge_points": [\n    {...'`
    # —— 返回体**开头是合法 JSON、整体不完整** ⇒ 输出被 max_tokens 截断，不是网络或格式问题。
    # llm.py 的 DEFAULT_MAX_TOKENS 同样是 8000，而**思考型模型的 reasoning 也占用同一份预算**，
    # 知识点最多的块最容易被吃掉预算。抬高上限不产生额外费用（按实际用量计费），
    # 只有真正写满时才多花。模块级设计意图是配合 disable_thinking 使用（见文件开头文档字符串）；
    # 该开关当前**未启用**，以保持全库抽取口径一致。
    data = kb.ask_json(prompt, system=SYSTEM_PROMPT, max_tokens=16000,
                       temperature=0.2, verbose=verbose)
    items = data.get("knowledge_points") or data.get("knowledgePoints") or []
    if not isinstance(items, list):
        raise ValueError("返回结构中缺少 knowledge_points 数组")

    body = chunk["text"]
    kps: list[dict] = []
    evs: list[dict] = []
    idx = start_index
    for it in items:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip().strip("。.")
        definition = str(it.get("definition") or "").strip()
        if not name or len(definition) < 10:
            continue
        kp_id = f"{course_id}-KP-{idx:04d}"
        idx += 1

        ev_list = it.get("evidence") or []
        if not isinstance(ev_list, list):
            ev_list = []
        verified_flags = []
        for j, ev in enumerate(ev_list[:4]):
            if not isinstance(ev, dict):
                continue
            quote = str(ev.get("quote") or "").strip()
            tier, matched, note = verify_quote(quote, body)   # 三档判定
            ok = kb.quote_passes(tier)
            verified_flags.append(ok)
            rec = {
                "evidence_id": f"{kp_id}-E{j + 1}",
                "kp_id": kp_id,
                "course": chunk["course"],
                "course_id": course_id,
                "chapter": chunk.get("chapter"),
                "section": chunk.get("section"),
                "node_id": chunk.get("node_id"),
                "source_file": chunk.get("source_file"),
                "page_label": str(ev.get("page_label") or chunk.get("page_label_start") or ""),
                "quote": quote,
                "quote_verified": ok,
                "verify_method": tier,      # 档位短标签：exact / partial / fabricated / not_found
                "evidence_type": str(ev.get("evidence_type") or "讲解"),
                "chunk_id": chunk["chunk_id"],
                "extracted_at": datetime.now().isoformat(timespec="seconds"),
                # 批次标识：append 型产物跨多轮运行后必然批次混杂，没有它就无法安全清理
                # 旧批次 —— 若把逐段生成的 transcribed_at 误当批次 id，清理时会连带删掉
                # 正常行。只写 run_id，模型/端点指纹集中在 state.meta（9 千行不必各背一份）。
                "run_id": run_id,
            }
            if not ok:
                # 非 exact 才写这两个字段 —— schema 必须与产出路径无关：
                # verify_kp --recheck 判为通过时会 pop 掉它们；若抽取路径对所有行都写，
                # 「字段是否存在」就会取决于记录来自哪条路径，出现同值双字段 / 路径相关 schema。
                rec["verify_matched"] = matched   # 命中片段，便于人工比对原页
                rec["verify_note"] = note         # 长说明，人工复核直接看这一行
            evs.append(rec)

        # 置信度：evidence 全部可核验 -> A；部分 -> B；全部不可核验 -> C
        # 规则集中在 kb.confidence_from_tiers，与 verify_kp --recheck 回填时同一套，
        # 避免「evidence 已全 exact、confidence 还写 C」这类陈旧字段。
        conf = kb.confidence_from_tiers(verified_flags)

        nums = it.get("numbers") or []
        if not isinstance(nums, list):
            nums = []

        kps.append({
            "kp_id": kp_id,
            "course": chunk["course"],
            "course_id": course_id,
            "name": name,
            "aliases": [str(a) for a in (it.get("aliases") or []) if str(a).strip()][:5],
            "definition": definition,
            "type": str(it.get("type") or "概念"),
            "importance": int(it.get("importance") or 3) if str(it.get("importance") or "3").isdigit() else 3,
            "numbers": [str(n) for n in nums][:10],
            "note": str(it.get("note") or "").strip(),
            "node_id": chunk.get("node_id"),
            "chapter": chunk.get("chapter"),
            "section": chunk.get("section"),
            "source_file": chunk.get("source_file"),
            "page_label_start": chunk.get("page_label_start"),
            "page_label_end": chunk.get("page_label_end"),
            "evidence_ids": [],
            "status": "draft",
            "confidence": conf,
            "chunk_id": chunk["chunk_id"],
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "run_id": run_id,          # 批次标识，语义同 evidence.run_id
        })
        kps[-1]["evidence_ids"] = [e["evidence_id"] for e in evs if e["kp_id"] == kp_id]

    return kps, evs, idx


# ---------------------------------------------------------------------------
def check(args) -> int:
    print("=" * 72)
    print("M4b 知识点抽取 · 自检")
    print("=" * 72)
    if not kb.PAGES_FILE.exists():
        print(f"[FAIL] 文本库不存在：{kb.PAGES_FILE}；请先运行 parse_docs.py")
        return 2
    try:
        llm = kb._llm_module()
        cfg = llm.load_env()
        if not llm.has_valid_key(cfg):
            print(f"[FAIL] API Key 未配置或仍是占位符。请编辑 {kb.CONFIG_DIR / '.env'}")
            return 3
        print(f"[ OK ] API Key 已配置    模型：{cfg.get('DEEPSEEK_MODEL') or '默认'}")
    except Exception as exc:
        print(f"[FAIL] 无法读取 LLM 配置：{type(exc).__name__}: {exc}")
        return 3

    groups = kb.group_pages_by_course()
    skel = kb.load_json(kb.SKELETON_FILE, {}) or {}
    total_chunks = 0
    print(f"\n{'课程':<28}{'块数':>6}{'字符':>12}{'骨架':>8}")
    print("-" * 72)
    for course, pages in sorted(groups.items()):
        usable = [p for p in pages if (p.get("text") or "").strip()]
        if not usable:
            print(f"{course:<28}{'-':>6}{'-':>12}{'无文本':>8}")
            continue
        ch = chunks_from_skeleton(course, usable, skel,
                                  include_matter=args.include_matter)
        total_chunks += len(ch)
        chars = sum(c["chars"] for c in ch)
        has = "有" if (skel.get("courses") or {}).get(course, {}).get("chapters") else "无"
        print(f"{course:<28}{len(ch):>6}{chars:>12,}{has:>8}")
    print("-" * 72)
    print(f"合计 {total_chunks} 个块")
    est_in = total_chunks * MAX_CHARS / 1.4
    print(f"预计输入约 {est_in / 1_000_000:.2f}M tokens，"
          f"成本量级约 {est_in / 1_000_000 * 1.5:.1f} 元（Flash 空闲时段）")
    print(f"预计耗时约 {total_chunks * 0.7:.0f} 分钟（按每块 40 秒粗估）")
    print("\n建议：先 --limit 3 试跑，检查知识点质量与引用核验率，再全量运行。")
    print("=" * 72)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="M4b 知识点抽取")
    ap.add_argument("--check", action="store_true", help="自检与规模估算")
    ap.add_argument("--course", help="只处理某门课（模糊匹配）")
    ap.add_argument("--limit", type=int, help="最多处理 N 个块（试跑用）")
    ap.add_argument("--force", action="store_true", help="忽略进度，重新抽取")
    ap.add_argument("--chunk-chars", type=int, default=MAX_CHARS, help="单块字符上限")
    ap.add_argument("--dry-run", action="store_true", help="只列出将处理的块")
    ap.add_argument("--migrate-state", action="store_true",
                    help="把 state_extract.json 的旧位置序号主键迁移为内容派生主键并补指纹（一次性，零 API）")
    ap.add_argument("--verbose", action="store_true", help="打印每次调用详情")
    ap.add_argument("--include-matter", action="store_true",
                    help="包含前言/符号表/索引等非正文材料（默认跳过）")
    args = ap.parse_args()

    if args.check:
        return check(args)

    groups = kb.group_pages_by_course()
    skel = kb.load_json(kb.SKELETON_FILE, {}) or {}
    courses_cfg = {c.get("name"): c for c in kb.load_courses()}

    all_chunks: list[dict] = []
    for course, pages in sorted(groups.items()):
        if args.course and args.course not in course:
            continue
        usable = [p for p in pages if (p.get("text") or "").strip()]
        if not usable:
            continue
        all_chunks.extend(chunks_from_skeleton(course, usable, skel, args.chunk_chars,
                                               include_matter=args.include_matter))

    if not all_chunks:
        print("没有可抽取的内容。请先运行 parse_docs.py / ocr_pages.py，并确认有文本。")
        return 1

    # 全局唯一断言（跨课程兜一道；课程内的断言在 chunks_from_skeleton 里）
    _dup = [k for k, n in Counter(c["chunk_id"] for c in all_chunks).items() if n > 1]
    if _dup:
        raise RuntimeError(f"chunk_id 主键全局不唯一（{len(_dup)} 个）：{_dup[:5]}")

    if args.migrate_state:
        return migrate_state(all_chunks)

    state = kb.State("extract")

    # 陈旧主键守卫：**不做静默自动迁移**（隐藏的写入比显式拒跑危险）。
    # --migrate-state / --check / --force 三种模式跳过本守卫。
    if not (args.migrate_state or args.check or args.force):
        stale, orphans = stale_state_keys(state, all_chunks)
        if stale:
            print("=" * 72)
            print(f"[STOP] 检测到 {len(stale)} 个未迁移的旧格式主键 —— "
                  f"继续跑会重复抽取这些块（重复知识点）")
            for k in stale[:10]:
                print(f"   - {k}")
            print("       请先执行：python tools\\extract_kp.py --migrate-state")
            print("=" * 72)
            return 3
        if orphans:
            print(f"[WARN] state 中有 {len(orphans)} 个孤立键"
                  f"（对应块已不存在，不影响本次运行）：{orphans[:3]}")

    done_flags = [state.is_done(c["chunk_id"], kb.chunk_fingerprint(c)) for c in all_chunks]
    todo_all = [c for c, f in zip(all_chunks, done_flags) if args.force or not f]
    todo = todo_all[: args.limit] if args.limit else todo_all

    print("=" * 72)
    # 进度口径：完成数按 (总块数 - 剩余待处理) 计算，而不是按 --limit 截断后的处理数 ——
    # 否则 --limit 3 会把另外 231 个尚未处理的块也当成跑过的，打出「跑完了 231 个」的假象。
    print(f"M4b 知识点抽取   总块数 {len(all_chunks)}   剩余待处理 {len(todo_all)}"
          f"   本次处理 {len(todo)}（已完成 {len(all_chunks) - len(todo_all)}）")
    if args.force:
        print("（--force：忽略进度，本次将重抽全部块；注意 kb.append_jsonl 无去重，会产生重复条目）")
    print("=" * 72)

    if args.dry_run:
        for c in todo[:40]:
            print(f"  {c['course']:<24} {str(c.get('chapter'))[:34]:<36} "
                  f"p{c['page_start']}-{c['page_end']}  {c['chars']}字")
        if len(todo) > 40:
            print(f"  ... 其余 {len(todo) - 40} 块")
        return 0
    if not todo:
        print("全部已完成。若要重跑用 --force。")
        return 0

    existing = kb.load_jsonl(kb.KP_FILE)
    counters: dict[str, int] = {}
    kp_all = 0
    ev_all = 0
    bad_quotes = 0
    tier_count: Counter = Counter()
    failed: list[str] = []
    t0 = time.time()

    # 批次标识 + 复算指纹：写进该批次的每条 kp/evidence，同时把「用哪个模型/端点、
    # 哪套本地引擎」记进 state.meta —— 云端模型会静默升级下线，缺指纹就无法复算历史结论。
    run_id = kb.new_run_id("EXT")
    state.meta["last_run_id"] = run_id
    state.meta["last_started_at"] = datetime.now().isoformat(timespec="seconds")
    state.meta["last_run_scope"] = {
        "chunks_total": len(all_chunks), "chunks_planned": len(todo),
        "course": args.course or "", "chunk_chars": args.chunk_chars,
        "force": bool(args.force),
    }
    state.meta["llm"] = kb.llm_fingerprint()
    state.meta["engines"] = kb.engine_fingerprint()
    state.save()
    print(f"批次标识 {run_id}  模型 {state.meta['llm'].get('model')}  "
          f"端点 {state.meta['llm'].get('base_url') or '默认'}")

    for i, chunk in enumerate(todo, start=1):
        course = chunk["course"]
        cfg = courses_cfg.get(course) or kb.match_course(course) or {}
        course_id = cfg.get("id") or re.sub(r"\W+", "", course)[:10].upper()
        if course_id not in counters:
            counters[course_id] = next_kp_index(existing, course_id)

        head = f"[{i}/{len(todo)}] {course[:18]:<18} {str(chunk.get('chapter'))[:26]:<26}"
        try:
            kps, evs, new_idx = extract_chunk(chunk, course_id, counters[course_id],
                                              verbose=args.verbose, run_id=run_id)
        except Exception as exc:
            print(f"{head}  [FAIL] {type(exc).__name__}: {str(exc)[:90]}")
            failed.append(chunk["chunk_id"])
            continue

        counters[course_id] = new_idx
        if not kps:
            state.mark(chunk["chunk_id"], fingerprint=kb.chunk_fingerprint(chunk),
                       chars=chunk["chars"], kps=0, run_id=run_id)
            print(f"{head}  无知识点（跳过）")
            continue

        # 立即落盘：抽取很贵，中断不允许丢结果（逐文件重试，见 append_with_retry）
        append_with_retry(kb.KP_FILE, kps, head=head)
        append_with_retry(kb.EVIDENCE_FILE, evs, head=head)
        existing.extend(kps)
        state.mark(chunk["chunk_id"], fingerprint=kb.chunk_fingerprint(chunk),
                   chars=chunk["chars"], kps=len(kps), run_id=run_id)

        kp_all += len(kps)
        ev_all += len(evs)
        for e in evs:
            tier_count[e.get("verify_method") or "unknown"] += 1
        nbad = sum(1 for e in evs if not e["quote_verified"])
        bad_quotes += nbad
        conf = {}
        for k in kps:
            conf[k["confidence"]] = conf.get(k["confidence"], 0) + 1
        flag = ""
        if nbad:
            npart = sum(1 for e in evs if e.get("verify_method") == kb.VERIFY_PARTIAL)
            flag = f"  [!] {nbad} 条非 exact（partial {npart} → 人工复核队列）"
        print(f"{head}  +{len(kps):>2} 知识点  +{len(evs)} 证据  "
              f"A{conf.get('A', 0)}/B{conf.get('B', 0)}/C{conf.get('C', 0)}{flag}")

    elapsed = time.time() - t0
    cost = kb.cost_summary()
    print("\n" + "=" * 72)
    print(f"本次新增知识点 {kp_all}，证据引用 {ev_all}")
    print(f"引用未通过核验 {bad_quotes} 条（这些会在 M5 阶段优先送人工复核）")
    if tier_count:
        # exact 占比显著偏低即为质量信号（全量输入是 OCR 文本，含错字与断行）
        order = (kb.VERIFY_EXACT, kb.VERIFY_PARTIAL, kb.VERIFY_FABRICATED, kb.VERIFY_NOT_FOUND)
        parts = [f"{t}={tier_count.get(t, 0)}" for t in order if tier_count.get(t)]
        extra = {k: v for k, v in tier_count.items() if k not in order}
        parts += [f"{k}={v}" for k, v in extra.items()]
        print(f"引用核验档位：{'  '.join(parts)}（**只有 exact 算通过**）")
    print(f"本次批次 run_id：{run_id}（已写入本批次每条 kp / evidence）")
    print(f"耗时 {elapsed / 60:.1f} 分钟，API 调用 {cost['calls']} 次，失败 {cost['failed']} 次")
    print(f"tokens: 输入 {cost['prompt_tokens']:,}  输出 {cost['completion_tokens']:,}  "
          f"约 {kb.estimate_cost_yuan():.2f} 元（空闲时段估算）")
    print(f"知识点库：{kb.KP_FILE}")
    print(f"证据引用：{kb.EVIDENCE_FILE}")
    if failed:
        print(f"\n失败块 {len(failed)} 个（重跑本脚本会自动重试）：")
        for f in failed[:10]:
            print(f"  - {f}")
    print("\n下一步：python tools\\verify_kp.py  对知识点做一致性校验并生成审核队列")
    print("=" * 72)
    # 有失败块就返回非 0（失败块会被下一次运行自动重试，但"退出码 0"会让脚本与 CI
    # 以为这次全成了 —— 尤其是**没有 API Key 时整批失败**，那时最需要立刻被察觉）。
    if failed or cost["failed"]:
        print(f"\n[FAIL] 本次有 {len(failed)} 个块失败、{cost['failed']} 次 API 调用失败；"
              f"重跑本脚本会自动重试这些块。")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
