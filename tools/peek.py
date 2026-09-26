# -*- coding: utf-8 -*-
"""
页面文本查看器 —— 调试解析/OCR 结果用
========================================
用途：检查某门课某些页面的实际文本形态，用于诊断章节识别、OCR 质量等问题。

用法：
  python tools\\peek.py --list                       # 列出各课程可用页数
  python tools\\peek.py 防排烟工程                    # 看前 3 页
  python tools\\peek.py 防排烟工程 --pages 5,20,40    # 看指定页
  python tools\\peek.py 防排烟工程 --pages 1-10 --head 12
  python tools\\peek.py 防排烟工程 --scan             # 只看扫描页/OCR 页
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(os.environ.get("FIREKB_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "tools"))

import kb  # noqa: E402


def parse_pages(spec: str | None) -> list[int] | None:
    if not spec:
        return None
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                out.extend(range(int(a), int(b) + 1))
            except ValueError:
                continue
        elif part.isdigit():
            out.append(int(part))
    return out or None


def main() -> int:
    ap = argparse.ArgumentParser(description="查看页面文本（调试用）")
    ap.add_argument("course", nargs="?", help="课程名（模糊匹配）")
    ap.add_argument("--list", action="store_true", help="列出各课程页数概况")
    ap.add_argument("--pages", help="页码，如 5,20,40 或 1-10")
    ap.add_argument("--head", type=int, default=16, help="每页显示前几行（默认 16）")
    ap.add_argument("--width", type=int, default=92, help="每行截断宽度")
    ap.add_argument("--scan", action="store_true", help="只看 OCR 出来的页")
    args = ap.parse_args()

    rows = kb.load_jsonl(kb.PAGES_FILE)
    if not rows:
        print(f"[!] 文本库为空：{kb.PAGES_FILE}")
        return 1

    by_course: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_course[r.get("course") or "未分类"].append(r)
    for c in by_course:
        by_course[c].sort(key=lambda r: r.get("page_no", 0))

    if args.list or not args.course:
        print("=" * 78)
        print(f"{'课程':<30}{'页数':>6}{'OCR页':>7}{'空页':>6}{'字数':>10}")
        print("-" * 78)
        for c in sorted(by_course):
            g = by_course[c]
            ocr = sum(1 for r in g if r.get("text_source") == "ocr")
            empty = sum(1 for r in g if not (r.get("text") or "").strip())
            chars = sum(r.get("char_count", 0) for r in g)
            print(f"{c:<30}{len(g):>6}{ocr:>7}{empty:>6}{chars:>10,}")
        print("=" * 78)
        if not args.course:
            return 0

    # 匹配课程
    target = None
    for c in by_course:
        if args.course in c or c in args.course:
            target = c
            break
    if not target:
        print(f"[!] 未找到匹配课程：{args.course}")
        print("    可选：" + " / ".join(sorted(by_course)))
        return 1

    group = by_course[target]
    pages = parse_pages(args.pages)
    if pages:
        sel = [r for r in group if r.get("page_no") in pages]
    elif args.scan:
        sel = [r for r in group if r.get("text_source") == "ocr"][:3]
    else:
        sel = group[:3]

    print("=" * 78)
    print(f"课程：{target}　共 {len(group)} 页　显示 {len(sel)} 页")
    print("=" * 78)
    for r in sel:
        t = r.get("text") or ""
        flag = f" [OCR 置信{r.get('ocr_confidence')}]" if r.get("text_source") == "ocr" else ""
        print(f"\n──── {r.get('page_label')}  chars={len(t)}{flag} ────")
        if not t.strip():
            print("   (空)")
            continue
        for line in t.split("\n")[: args.head]:
            print("   |" + line[: args.width])
    return 0


if __name__ == "__main__":
    sys.exit(main())
