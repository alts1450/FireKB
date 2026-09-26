# -*- coding: utf-8 -*-
"""
本地全文检索（M7 的字面检索层，零成本、不调用任何模型）
========================================================
在 20_文本库\\pages.jsonl 上做关键词检索，返回带出处的命中片段。

用法：
  python tools\\search_pages.py --stats
  python tools\\search_pages.py 防火分区
  python tools\\search_pages.py 耐火极限 柱 --course 建筑防火
  python tools\\search_pages.py 疏散 --limit 5 --context 50

说明：
  * 多个关键词用空格分隔，默认「全部命中」才返回（AND 语义）。
  * 加 --any 改为「任一命中」（OR 语义）。
  * 命中词会用【】标出，便于人工核验——这是本项目所有输出的统一呈现规范。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(os.environ.get("FIREKB_ROOT") or Path(__file__).resolve().parents[1])
PAGES_FILE = ROOT / "20_文本库" / "pages.jsonl"
PAGEMAP_FILE = ROOT / "20_文本库" / "page_map.json"


def load_pages() -> list[dict]:
    if not PAGES_FILE.exists():
        print(f"[!] 文本库不存在：{PAGES_FILE}")
        print("    请先运行：python tools\\parse_docs.py")
        return []
    rows: list[dict] = []
    with PAGES_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def show_stats(rows: list[dict]) -> None:
    if not rows:
        return
    by_course: dict[str, set[str]] = defaultdict(set)
    pages_by_course: Counter = Counter()
    scan_by_course: Counter = Counter()
    for r in rows:
        by_course[r["course"]].add(r["file_id"])
        pages_by_course[r["course"]] += 1
        if r.get("is_scan"):
            scan_by_course[r["course"]] += 1

    print("=" * 70)
    print("文本库概况")
    print("=" * 70)
    print(f"总页记录 : {len(rows)}")
    print(f"总文件数 : {len({r['file_id'] for r in rows})}")
    print(f"扫描/空页: {sum(1 for r in rows if r.get('is_scan'))}  （需 OCR 或人工补录）")
    print(f"文本总量 : {sum(r.get('char_count', 0) for r in rows):,} 字")
    print()
    print(f"{'课程/分类':<22}{'文件':>6}{'页数':>8}{'扫描页':>8}")
    print("-" * 70)
    for course in sorted(by_course, key=lambda c: -pages_by_course[c]):
        print(f"{course:<22}{len(by_course[course]):>6}{pages_by_course[course]:>8}{scan_by_course[course]:>8}")

    if PAGEMAP_FILE.exists():
        pm = json.loads(PAGEMAP_FILE.read_text(encoding="utf-8"))
        files = pm.get("files", [])
        if files:
            print()
            print("页码映射（前 5 个文件）：")
            for f in files[:5]:
                print(f"  {f['source_file']}")
                print(f"    原文件共 {f['pages']} 页  ->  合并后全局第 {f['merged_start']}~{f['merged_end']} 页")
    print("=" * 70)


def make_snippet(text: str, terms: list[str], width: int) -> str:
    """截取包含首个命中词的上下文，并把所有命中词用【】标出。"""
    flat = re.sub(r"\s+", " ", text or "").strip()
    if not flat:
        return "(空)"
    pos = -1
    for t in terms:
        i = flat.lower().find(t.lower())
        if i != -1 and (pos == -1 or i < pos):
            pos = i
    if pos == -1:
        return flat[: width * 2]
    start = max(0, pos - width // 2)
    end = min(len(flat), pos + width + width // 2)
    seg = flat[start:end]
    for t in sorted(terms, key=len, reverse=True):
        seg = re.sub(f"({re.escape(t)})", r"【\1】", seg, flags=re.I)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(flat) else ""
    return f"{prefix}{seg}{suffix}"


def search(rows: list[dict], terms: list[str], course: str | None,
           limit: int, context: int, match_any: bool) -> int:
    hits: list[dict] = []
    for r in rows:
        if course and course not in r.get("course", ""):
            continue
        hay = (r.get("text_flat") or "").lower()
        if not hay:
            continue
        found = [t for t in terms if t.lower() in hay]
        ok = bool(found) if match_any else len(found) == len(terms)
        if ok:
            hits.append({"row": r, "found": found})

    print("=" * 70)
    print(f"检索：{' OR '.join(terms) if match_any else ' AND '.join(terms)}"
          + (f"   课程过滤：{course}" if course else ""))
    print(f"命中 {len(hits)} 页" + (f"，显示前 {limit} 条" if len(hits) > limit else ""))
    print("=" * 70)
    if not hits:
        print("没有命中。建议：")
        print("  1) 换更短的关键词（如「防火分区」拆成「分区」）")
        print("  2) 加 --any 放宽为任一命中")
        print("  3) 确认课件是否已放进 10_原始归档 并跑过 parse_docs.py")
        return 1

    by_file: dict[str, list[dict]] = defaultdict(list)
    for h in hits:
        by_file[h["row"]["source_file"]].append(h)

    shown = 0
    for src in sorted(by_file):
        if shown >= limit:
            break
        print(f"\n▍{src}")
        for h in sorted(by_file[src], key=lambda x: x["row"]["page_no"]):
            if shown >= limit:
                break
            r = h["row"]
            flag = " [扫描页/空页]" if r.get("is_scan") else ""
            print(f"  {r['page_label']}{flag}  (命中: {'、'.join(h['found'])})")
            print(f"    {make_snippet(r.get('text', ''), terms, context)}")
            shown += 1
    print("\n" + "=" * 70)
    print("提示：以上每个命中都带有「文件 + 页码」，这就是本项目的引用规范——")
    print("      任何结论都必须能落到这一级，否则视为不可核验。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="文本库检索 / 概况")
    ap.add_argument("terms", nargs="*", help="关键词，多个用空格分隔")
    ap.add_argument("--stats", action="store_true", help="显示文本库概况")
    ap.add_argument("--course", help="按课程/分类过滤")
    ap.add_argument("--limit", type=int, default=10, help="最多显示条数（默认 10）")
    ap.add_argument("--context", type=int, default=70, help="片段上下文长度（默认 70）")
    ap.add_argument("--any", action="store_true", help="任一关键词命中即可（默认全部命中）")
    args = ap.parse_args()

    rows = load_pages()
    if not rows:
        return 1
    if args.stats or not args.terms:
        show_stats(rows)
        return 0
    return search(rows, args.terms, args.course, args.limit, args.context, args.any)


if __name__ == "__main__":
    sys.exit(main())
