# -*- coding: utf-8 -*-
"""
M5 · 校验与审核队列（防幻觉闸门）
====================================
对 M4 抽出的知识点做四类机器可执行的核验，产出分级审核队列 audit.jsonl。
未通过 P0 核验的知识点不会进入 M6 的背诵材料。

四类核验（前两类零 LLM 成本，后两类可选）：
  1. 引用核验   —— 每条 evidence 的 quote 是否真的存在于原文（M4 已初判，此处复核并给出上下文）
  2. 数字核验   —— 知识点里出现的所有数字是否能在原文中找到（消防工程最怕数字被改写）
  3. 定义质量   —— 定义过短/过于笼统/疑似编造（规则判定）
  4. 重复冲突   —— 同一课程内名称高度相似的知识点 -> 合并建议

审核优先级（人工只做确认/驳回，不从零写）：
  P0 必须处理：引用找不到原文、数字找不到原文、定义缺失
  P1 抽审    ：定义偏短、名称重复、别名缺失
  P2 仅记录  ：格式类问题

用法：
  python tools\\verify_kp.py                 # 核验并生成队列
  python tools\\verify_kp.py --course "防排烟工程"
  python tools\\verify_kp.py --show          # 只看统计，不写文件
  python tools\\verify_kp.py --recheck       # 用新规则就地重判存量 evidence
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(os.environ.get("FIREKB_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "tools"))

import kb  # noqa: E402

NUM_RE = re.compile(r"\d+(?:\.\d+)?")
NAME_SIM_THRESHOLD = 0.82
MIN_DEF_LEN = 20


def label_to_page_no(label: str) -> int | None:
    m = re.search(r"\d+", label or "")
    return int(m.group()) if m else None


def build_page_index() -> dict[tuple[str, int], dict]:
    idx = {}
    for p in kb.load_jsonl(kb.PAGES_FILE):
        key = (p.get("source_file"), p.get("page_no"))
        idx[key] = p
    return idx


def page_numbers_from_labels(*labels: str) -> list[int]:
    nums = []
    for lb in labels:
        n = label_to_page_no(lb)
        if n:
            nums.append(n)
    return nums


def source_text_of(kp: dict, evs: list[dict], index: dict) -> str:
    """取该知识点来源页面的原文（用于数字核验）。"""
    sf = kp.get("source_file")
    pages = page_numbers_from_labels(kp.get("page_label_start"), kp.get("page_label_end"))
    # 同时把证据里出现的页码也纳入，避免页码范围不全导致误报
    for e in evs:
        n = label_to_page_no(e.get("page_label"))
        if n:
            pages.append(n)
    if not pages:
        return ""
    lo, hi = min(pages), max(pages)
    parts = []
    for pn in range(lo, hi + 1):
        rec = index.get((sf, pn))
        if rec:
            parts.append(rec.get("text") or "")
    return "\n".join(parts)


def norm(s: str) -> str:
    """名称 / 数字比对的归一化 —— 统一走 kb 的共享实现。

    各处若各写一份（例如只做「小写 + 去空白」），同一份文本会得到不同结论 ——
    典型的"两套实现漂移"。归一化必须只有一处实现。
    """
    return kb.normalize_for_match(s)


def tier_of(e: dict) -> str:
    """取证据档位：优先 verify_tier，兼容只有 verify_method / quote_verified 的存量行。

    档位语义（与 kb.verify_quote 一致）：
      exact      逐字命中 —— **唯一算通过**
      partial    头尾命中、中段有落差 —— 进 P1 人工复核队列，**不计入通过率**
      fabricated 仅前缀命中 / 数字未逐字命中 / 空引用 —— **P0 拦截**
      not_found  连前缀都找不到 —— **与 fabricated 同待遇（P0 拦截）**
    """
    t = e.get("verify_tier") or e.get("verify_method")
    if t in (kb.VERIFY_EXACT, kb.VERIFY_PARTIAL, kb.VERIFY_FABRICATED, kb.VERIFY_NOT_FOUND):
        return t
    return kb.VERIFY_EXACT if e.get("quote_verified") else kb.VERIFY_FABRICATED


def number_appears(num_token: str, text: str) -> bool:
    """
    数字核验。容忍单位写法差异与 OCR 的空白差异，
    但不容忍数值本身变化（350 -> 35 或 250 都算不合格）。
    """
    m = NUM_RE.search(num_token or "")
    if not m:
        return True
    value = m.group()
    t = norm(text)
    if value in t:
        return True
    # 处理千分位与全角
    alt = value.replace(".", "")
    if alt and alt in t.replace(",", ""):
        return True
    # 形如 0.5 也可能原文写作 .5
    if value.startswith("0.") and value[1:] in t:
        return True
    return False


# ---------------------------------------------------------------------------
# 存量 evidence 回填（--recheck）
# ---------------------------------------------------------------------------
def build_chunk_body_index() -> tuple[dict, dict]:
    """
    用**当前**代码重建块正文，返回 (by_key, by_range)：
      by_key   —— {(course, file_tag, page_start, page_end): (chunk_id, body)}
      by_range —— {(course, page_start, page_end): [(chunk_id, body), ...]}

    为什么按「课程 + 页范围」索引而不是按 chunk_id：chunk_id 的格式会变
    （末段是否带位置序号），而存量 evidence 里存的是写入当时的旧键；
    必须用**页范围**这个不变量去对齐，否则新旧键对不上，
    重判会全部掉到 not_found（一种不报错、只改数字的静默错误）。

    键加了**文件标签**后，(course, pA-B) 不再唯一（同一门课可挂多本书）——
    只用页范围索引会让不同书之间**互相覆盖**。
    因此改为两套索引：带标签时精确命中；不带标签的旧键在同范围候选里逐一判定取最高档。
    """
    import extract_kp  # 同目录；函数级导入，避免模块级循环依赖
    skel = kb.load_json(kb.SKELETON_FILE, {}) or {}
    by_key: dict = {}
    by_range: dict = {}
    for course, pages in kb.group_pages_by_course().items():
        usable = [p for p in pages if (p.get("text") or "").strip()]
        if not usable:
            continue
        for c in extract_kp.chunks_from_skeleton(course, usable, skel):
            tag = kb.make_file_tag(c.get("file_id"))
            by_key[(course, tag, c["page_start"], c["page_end"])] = (c["chunk_id"], c["text"])
            by_range.setdefault((course, c["page_start"], c["page_end"]), []).append(
                (c["chunk_id"], c["text"]))
    return by_key, by_range


_TIER_RANK = {"exact": 3, "partial": 2, "fabricated": 1, "not_found": 0}


def _judge_best(quote: str, cands: list[tuple[str, str]]) -> tuple | None:
    """
    在多个候选块正文中取**判定最高**的一档。

    用于：evidence 存的是不带文件标签的旧键，而同 (course, 页范围) 下有多本书的块。
    逐个判、取最高档，避免「只拿第一个候选判 → 假 not_found」这类静默降级。
    返回 (rank, tier, matched, note, chunk_id, body)；候选为空返回 None。
    """
    best = None
    for cid, body in cands:
        tier, matched, note = kb.verify_quote(quote, body)
        rank = _TIER_RANK.get(tier, 0)
        if best is None or rank > best[0]:
            best = (rank, tier, matched, note, cid, body)
    return best


def _fallback_body(course: str, ps: int, pe: int) -> str:
    """块索引未命中时的兜底：按页码区间直接拼原始页文本（更宽，但不会误判成 not_found）。"""
    parts = []
    for p in kb.load_jsonl(kb.PAGES_FILE):
        if p.get("course") != course:
            continue
        pn = p.get("page_no")
        if pn is None or not (ps <= pn <= pe):
            continue
        if (p.get("text") or "").strip():
            parts.append(f"【{p.get('page_label')}】\n{(p.get('text') or '').strip()}")
    return "\n\n".join(parts)


def recheck_evidence(course: str | None = None) -> dict:
    """
    用**当前**归一化 + 三档规则就地重新判定存量 evidence，
    并把旧的 chunk_id（含位置序号）一并改写为新主键。

    只判定，不写盘（写盘由 main() 决定，--show 时完全不写）。
    """
    evs = kb.load_jsonl(kb.EVIDENCE_FILE)
    rep = {"total": len(evs), "judged": 0, "changed": 0, "upgraded": 0,
           "downgraded": 0, "unmatched": 0, "ambiguous": 0,
           "before": Counter(), "after": Counter(),
           "rows": evs}
    if not evs:
        return rep
    by_key, by_range = build_chunk_body_index()
    run_stamp = datetime.now().isoformat(timespec="seconds")
    for e in evs:
        if course and course not in (e.get("course") or ""):
            continue
        rep["judged"] += 1
        # 键带文件标签 → 精确命中；不带标签的旧键 → 同范围候选逐一判定
        full = kb.parse_chunk_key_full(e.get("chunk_id") or "")
        body = ""
        if full:
            course_n, ps, pe, ftag = full
            cands: list = []
            if ftag:
                hit = by_key.get((course_n, ftag, ps, pe))
                if hit:
                    cands = [hit]
                else:
                    # 标签对不上（换了源文件 / 旧标签）→ 退到同页范围候选，不制造假阴性
                    cands = list(by_range.get((course_n, ps, pe)) or [])
                    if cands:
                        rep["ambiguous"] += 1
            else:
                cands = list(by_range.get((course_n, ps, pe)) or [])
            if len(cands) == 1:
                e["chunk_id"], body = cands[0]
            elif len(cands) > 1:
                rep["ambiguous"] += 1
                pick = _judge_best(e.get("quote") or "", cands)
                if pick:
                    _, _, _, _, cid_pick, body = pick
                    e["chunk_id"] = cid_pick
            else:
                rep["unmatched"] += 1
                body = _fallback_body(course_n, ps, pe)
        else:
            rep["unmatched"] += 1
        old = e.get("verify_method") or ("exact" if e.get("quote_verified") else "not_found")
        tier, matched, note = kb.verify_quote(e.get("quote") or "", body)
        rep["before"][old] += 1
        rep["after"][tier] += 1
        if old != tier:
            rep["changed"] += 1
            if old != kb.VERIFY_EXACT and tier == kb.VERIFY_EXACT:
                rep["upgraded"] += 1
            elif old == kb.VERIFY_EXACT and tier != kb.VERIFY_EXACT:
                rep["downgraded"] += 1
        e["quote_verified"] = kb.quote_passes(tier)
        e["verify_method"] = tier
        if e["quote_verified"]:
            # 通过的行不带这两个字段（与抽取路径同一 schema：字段存在性不取决于记录来自哪条路径）
            e.pop("verify_matched", None)
            e.pop("verify_note", None)
        else:
            e["verify_matched"] = matched
            e["verify_note"] = note
        e["rechecked_at"] = run_stamp

    # ---- 置信度重算 ----
    # 按 kp 聚合其证据档位重算 confidence：全部 exact → A；部分 exact → B；无 exact / 0 条 → C。
    # 规则取自 kb.confidence_from_tiers（与抽取时同一套），避免出现
    # 「evidence 已全 exact、kp.confidence 还写着 C」这类陈旧字段。
    ev_tiers: dict[str, list[str]] = defaultdict(list)
    for e in evs:
        ev_tiers[e.get("kp_id")].append(tier_of(e))
    kps = kb.load_jsonl(kb.KP_FILE)
    conf_before: Counter = Counter()
    conf_after: Counter = Counter()
    for kp in kps:
        conf_before[kp.get("confidence")] += 1
        if course is None or course in (kp.get("course") or ""):
            kp["confidence"] = kb.confidence_from_tiers(ev_tiers.get(kp["kp_id"], []))
        conf_after[kp.get("confidence")] += 1
    rep["kps"] = kps
    rep["conf_before"] = conf_before
    rep["conf_after"] = conf_after
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description="M5 知识点校验与审核队列")
    ap.add_argument("--course", help="只处理某门课")
    ap.add_argument("--show", action="store_true", help="只显示统计，不写文件")
    ap.add_argument("--recheck", action="store_true",
                    help="用当前归一化/三档规则就地重判存量 evidence，再生成审核队列")
    ap.add_argument("--force-recheck", action="store_true",
                    help="即使 evidence 大量无法在新块索引对位（疑似旧口径）也强行回填（不推荐）")
    args = ap.parse_args()

    rep: dict = {}
    if args.recheck:
        rep = recheck_evidence(args.course)
        if not rep["total"]:
            print("[recheck] evidence.jsonl 为空，跳过")
        else:
            def _fmt(c: Counter) -> str:
                return "  ".join(f"{k}={v}" for k, v in c.most_common()) or "-"
            print("=" * 74)
            print(f"[recheck] 重新判定 evidence {rep['judged']} 条"
                  f"（档位变化 {rep['changed']}，升级→exact {rep['upgraded']}，降级 {rep['downgraded']}）")
            print(f"[recheck] 判定前：{_fmt(rep['before'])}")
            print(f"[recheck] 判定后：{_fmt(rep['after'])}")
            # 同标签对照：按「判定前」出现过的档位逐一打印 前→后 数量，
            # 用来确认没有整档异常消失（旧档位归零、exact 掉档都一眼可见）
            arrow = "   ".join(f"{k} {v}→{rep['after'].get(k, 0)}"
                               for k, v in rep["before"].most_common())
            print(f"[recheck] 同标签对照：{arrow}")
            _tb = sum(1 for e in rep["rows"] if e.get("quote_verified"))
            print(f"[recheck] quote_verified：True {_tb}/{rep['total']}")
            print(f"[recheck] confidence：{_fmt(rep['conf_before'])}  ->  {_fmt(rep['conf_after'])}")
            if rep["unmatched"]:
                print(f"[recheck][WARN] {rep['unmatched']} 条 evidence 的正文靠页区间兜底取得"
                      f"（块索引未命中，骨架可能已变，请核对该条判定）")
            if rep.get("ambiguous"):
                print(f"[recheck][INFO] {rep['ambiguous']} 条 evidence 在「同课程同页范围」下有"
                      f"多个候选块（多本书页码重叠），已逐候选判定并取最高档")

            if args.show:
                print("[recheck] --show 模式：audit/kp 不写盘（evidence 仍会回填）")
            else:
                print("[recheck] audit / kp 状态将照常写盘")

            # ---- 写回前守卫 ----
            # 为什么必须加：`--recheck` 会**就地改写 evidence**。当 evidence 来自**旧口径**
            # （块边界不同、chunk_id 带不带文件标签不同）时，绝大多数引用会在新块索引里对不上，
            # 判定随之掉档 —— 而它会**安静地写回**：
            # exact 会被大面积误降级为 not_found，质量数字当场变坏且无人察觉。
            _total = max(rep.get("total") or 0, 1)
            _bad = rep.get("unmatched", 0) + rep.get("ambiguous", 0)
            _ratio = _bad / _total
            if _ratio > 0.10 and not getattr(args, "force_recheck", False):
                print("\n" + "=" * 78)
                print(f"[STOP] 拒绝回填：{_bad}/{_total}（{_ratio:.0%}）条 evidence 无法在新块索引里"
                      f"精确对位（unmatched={rep['unmatched']} ambiguous={rep['ambiguous']}）")
                print("=" * 78)
                print("  含义：这批 evidence 很可能来自**旧口径**（块边界或主键格式已变），")
                print("        强行回填会把 exact 大面积误降级为 not_found（降级多、升级少）。")
                print("  正确做法：归档旧数据 → 全量重抽（详见 00_配置\\下一对话交接_20260921.md §5.5）")
                print("  确实要强行回填（不推荐）：加 --force-recheck")
                print("=" * 78)
                print("  ⚠️ evidence 文件**未被改动**。")
                return 3
            kb.rewrite_jsonl(kb.EVIDENCE_FILE, rep["rows"])
            print(f"[recheck] 已就地回填：{kb.EVIDENCE_FILE}")

    kps = kb.load_jsonl(kb.KP_FILE)
    evs = kb.load_jsonl(kb.EVIDENCE_FILE)
    if args.recheck and rep.get("kps"):
        # 用重算过 confidence 的 kp 列表（写盘路径与 --recheck 一致；--show 时下面会早退不写）
        kps = rep["kps"]
    if not kps:
        print(f"[!] 知识点库为空：{kb.KP_FILE}")
        print("    请先运行：python tools\\extract_kp.py")
        return 1

    if args.course:
        kps = [k for k in kps if args.course in (k.get("course") or "")]
        keep = {k["kp_id"] for k in kps}
        evs = [e for e in evs if e.get("kp_id") in keep]

    ev_by_kp: dict[str, list[dict]] = defaultdict(list)
    for e in evs:
        ev_by_kp[e.get("kp_id")].append(e)

    index = build_page_index()

    print("=" * 74)
    print("M5 知识点校验")
    print("=" * 74)
    print(f"待校验知识点 {len(kps)}，证据引用 {len(evs)}\n")

    audits: list[dict] = []
    stat = Counter()
    aid = 0

    def add_audit(a_type: str, priority: str, kp: dict, detail: str, suggestion: str = "",
                  target: str = ""):
        nonlocal aid
        aid += 1
        audits.append({
            "audit_id": f"AUD-{aid:05d}",
            "type": a_type,
            "priority": priority,
            "course": kp.get("course"),
            "course_id": kp.get("course_id"),
            "kp_id": kp.get("kp_id"),
            "kp_name": kp.get("name"),
            "target": target or kp.get("kp_id"),
            "detail": detail,
            "suggestion": suggestion,
            "status": "pending",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })

    p0_kp: set[str] = set()

    for kp in kps:
        kp_id = kp["kp_id"]
        kp_evs = ev_by_kp.get(kp_id, [])
        src = source_text_of(kp, kp_evs, index)

        # --- 1. 引用核验 ---
        if not kp_evs:
            add_audit("no_evidence", "P0", kp, "该知识点没有任何证据引用，无法溯源")
            p0_kp.add(kp_id)
            stat["P0"] += 1
        else:
            # 档位分流：**只有 exact 算通过**；
            # partial → P1 人工复核队列（**不计入通过率**）；
            # fabricated / not_found → P0 拦截（不进背诵材料，两者同待遇）。
            partial = [e for e in kp_evs if tier_of(e) == kb.VERIFY_PARTIAL]
            bad = [e for e in kp_evs
                   if tier_of(e) in (kb.VERIFY_FABRICATED, kb.VERIFY_NOT_FOUND)]
            if bad:
                add_audit("quote_unverified", "P0", kp,
                          f"{len(bad)}/{len(kp_evs)} 条引用未在原文中找到"
                          f"（{tier_of(bad[0])}）",
                          "人工核对原页；若确属编造，驳回该知识点",
                          target=bad[0].get("evidence_id", ""))
                p0_kp.add(kp_id)
                stat["P0"] += 1
                if not bad[0].get("quote"):
                    add_audit("empty_quote", "P0", kp, "引用内容为空")
            if partial:
                add_audit("quote_partial", "P1", kp,
                          f"{len(partial)}/{len(kp_evs)} 条引用头尾均命中、中段有落差，需人工复核",
                          "对照原页确认中段是否被改写；本档不计入通过率",
                          target=partial[0].get("evidence_id", ""))
                stat["P1"] += 1

        # --- 2. 数字核验 ---
        nums = kp.get("numbers") or []
        if nums and src:
            missing = [n for n in nums if not number_appears(str(n), src)]
            if missing:
                add_audit("number_unverified", "P0", kp,
                          f"以下数值未在来源页原文中找到：{'、'.join(missing[:6])}",
                          "对照原页确认；数字错误必须修正后才可用于背诵")
                p0_kp.add(kp_id)
                stat["P0"] += 1
        elif nums and not src:
            add_audit("number_uncheckable", "P1", kp,
                      f"有 {len(nums)} 个数值但无法定位来源页原文，未能核验")

        # --- 3. 定义质量 ---
        d = (kp.get("definition") or "").strip()
        if len(d) < MIN_DEF_LEN:
            add_audit("definition_too_short", "P0", kp,
                      f"定义仅 {len(d)} 字，不足以脱离原文理解",
                      "补写定义或驳回")
            p0_kp.add(kp_id)
            stat["P0"] += 1
        elif re.search(r"(见(原文|上文|前文)|如上所述|同上|如图所示)", d):
            add_audit("definition_not_self_contained", "P1", kp,
                      "定义含指代性表述，脱离原文无法理解")

        if not (kp.get("aliases") or []):
            stat["no_alias"] += 1  # 仅统计，不生成审核条目（避免队列膨胀）

    # --- 4. 重复 / 冲突检测（同课程内名称相似）---
    by_course: dict[str, list[dict]] = defaultdict(list)
    for kp in kps:
        by_course[kp.get("course") or "未分类"].append(kp)

    dup_pairs = 0
    for course, group in by_course.items():
        names = [(k, norm(k.get("name"))) for k in group]
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, na = names[i]
                b, nb = names[j]
                if not na or not nb:
                    continue
                if na == nb or na in nb or nb in na:
                    ratio = 1.0
                else:
                    ratio = SequenceMatcher(None, na, nb).ratio()
                if ratio >= NAME_SIM_THRESHOLD:
                    dup_pairs += 1
                    add_audit("duplicate_name", "P1", a,
                              f"与 {b.get('kp_id')}「{b.get('name')}」高度相似（{ratio:.2f}）",
                              "确认是否同一知识点：是则合并，否则改名区分",
                              target=b.get("kp_id", ""))
                    stat["P1"] += 1

    # --- 汇总 ---
    counts = Counter(a["priority"] for a in audits)
    types = Counter(a["type"] for a in audits)

    print(f"{'课程':<28}{'知识点':>7}{'证据':>7}{'P0':>6}{'P1':>6}")
    print("-" * 74)
    for course in sorted(by_course):
        group = by_course[course]
        ids = {k["kp_id"] for k in group}
        n_ev = sum(len(ev_by_kp.get(i, [])) for i in ids)
        c = Counter()
        for a in audits:
            if a["course"] == course:
                c[a["priority"]] += 1
        print(f"{course:<28}{len(group):>7}{n_ev:>7}{c.get('P0', 0):>6}{c.get('P1', 0):>6}")
    print("-" * 74)
    print(f"合计 P0={counts.get('P0', 0)}  P1={counts.get('P1', 0)}  P2={counts.get('P2', 0)}")
    if types:
        print("问题类型：" + "  ".join(f"{t}={n}" for t, n in types.most_common()))

    passed = len(kps) - len(p0_kp)
    print(f"\n通过核验 {passed}/{len(kps)} 个知识点"
          f"（{100 * passed / max(len(kps), 1):.1f}%）")
    if counts.get("P0", 0) == 0:
        print("P0 = 0：全部知识点可直接进入背诵材料（健康状态）")
    else:
        print(f"P0 = {counts['P0']}：这些未通过核验，不会进入背诵材料，请优先处理")

    if args.show:
        print("\n（--show 模式，未写文件）")
        return 0

    # 写审核队列 + 回写知识点状态
    kb.rewrite_jsonl(kb.AUDIT_FILE, audits)
    for kp in kps:
        if kp["kp_id"] in p0_kp:
            kp["status"] = "pending"
        else:
            kp["status"] = "verified"
        kp["verified_at"] = datetime.now().isoformat(timespec="seconds")

    # 保留未参与校验的知识点原状态
    all_kps = kb.load_jsonl(kb.KP_FILE)
    if args.course:
        updated = {k["kp_id"]: k for k in kps}
        for k in all_kps:
            if k["kp_id"] in updated:
                k["status"] = updated[k["kp_id"]]["status"]
                k["verified_at"] = updated[k["kp_id"]]["verified_at"]
                if args.recheck:
                    # 置信度重算也要落盘（否则 --course + --recheck 时 A/C 分布不会更新）
                    k["confidence"] = updated[k["kp_id"]].get("confidence", k.get("confidence"))
    else:
        all_kps = kps
    kb.rewrite_jsonl(kb.KP_FILE, all_kps)

    print(f"\n审核队列：{kb.AUDIT_FILE}（{len(audits)} 条）")
    print(f"知识点状态已更新：verified / pending")
    print("\n下一步：python tools\\make_review.py  生成背诵卡与数字对照表")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
