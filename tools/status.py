# -*- coding: utf-8 -*-
"""
项目状态总览 —— 一眼看清 FireKB 现在走到哪一步
================================================
用法：python tools\\status.py

对应流水线：M1 归档 -> M2 解析 -> M3 转写 -> M4 结构化 -> M5 校验 -> M6 复习产物
不含任何写操作，纯只读检查。
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 路径常量统一向共享层 kb 取（不要在本地再写一遍 FIREKB_ROOT 解析）。
# **本地 load_jsonl / load_courses 不保留**，一律用 kb 的 —— 本地"同名不同源"的实现
# 只要容错口径与共享层有半点差别，表现就是"某些文件读得出来、另一些读不出来"，
# 而且**不报错**。所以要合并这类实现，必须以容错行为逐项一致为前提，至少覆盖：
# 不存在 / 空文件 / 只有空行 / 无尾换行 / 空行夹杂 / 坏行在首中尾 / 全坏行 /
# 首尾空格 / 顶层数组 / 顶层数字 / UTF-8 BOM / 中文值 / CRLF。
sys.path.insert(0, str(Path(__file__).resolve().parent))

import kb  # noqa: E402  共享层（路径常量与 IO 的唯一来源）

ROOT = kb.ROOT
ARCHIVE = kb.ARCHIVE
TEXT_DIR = kb.TEXT_DIR
KP_DIR = kb.KP_DIR
REVIEW_DIR = kb.REVIEW_DIR

# 兼容原有调用点：直接转发到共享层（不要再在本地写第二份实现）
load_jsonl = kb.load_jsonl
load_courses = kb.load_courses

DOC_EXTS = {".pdf", ".pptx", ".epub"}
AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".opus", ".amr", ".flac", ".wma", ".ogg", ".mp4"}


def bar(done: int, total: int, width: int = 28) -> str:
    if total <= 0:
        return "[" + "-" * width + "]  n/a"
    filled = int(width * min(done, total) / total)
    pct = 100.0 * min(done, total) / total
    return f"[{'=' * filled}{'.' * (width - filled)}] {pct:5.1f}%  ({done}/{total})"


def main() -> int:
    print("=" * 74)
    print(f" FireKB 项目状态总览    {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f" 数据根目录: {ROOT}")
    print("=" * 74)

    courses = load_courses()
    names = [c.get("name", "?") for c in courses]
    print(f"\n课程配置: {len(courses)} 门")
    if courses:
        print("  " + " / ".join(names))

    # ---------- M1 归档 ----------
    print("\n" + "-" * 74)
    print(" M1 归档层（原始内容，只增不改）")
    print("-" * 74)
    archive_stats: dict[str, dict] = {}
    for sub in ("课件", "规范教材", "录音"):
        d = ARCHIVE / sub
        docs, audios, size = 0, 0, 0
        per_course: Counter = Counter()
        if d.exists():
            for p in d.rglob("*"):
                if not p.is_file():
                    continue
                sz = p.stat().st_size
                size += sz
                rel = p.relative_to(d).parts
                course = rel[-2] if len(rel) >= 2 else "未分类"
                if p.suffix.lower() in DOC_EXTS:
                    docs += 1
                    per_course[course] += 1
                elif p.suffix.lower() in AUDIO_EXTS:
                    audios += 1
                    per_course[course] += 1
        archive_stats[sub] = {"docs": docs, "audios": audios, "size": size, "per_course": per_course}
        print(f"  {sub:<8} 文档 {docs:>3}  音频 {audios:>3}  合计 {size / 1024 / 1024:>8.1f} MB")
        for c, n in per_course.most_common():
            print(f"           - {c}: {n}")

    missing = [n for n in names if n not in archive_stats["规范教材"]["per_course"]
               and n not in archive_stats["课件"]["per_course"]]
    if missing:
        print(f"  [!] 尚无任何素材的课程（{len(missing)}）：{' / '.join(missing)}")

    # ---------- M2 解析 ----------
    print("\n" + "-" * 74)
    print(" M2 解析层（课件/教材 -> 页级文本）")
    print("-" * 74)
    pages = load_jsonl(TEXT_DIR / "pages.jsonl")
    if not pages:
        print("  尚未解析。执行：python tools\\parse_docs.py")
    else:
        by_course: dict[str, dict] = defaultdict(lambda: {"files": set(), "pages": 0, "scan": 0, "chars": 0})
        for r in pages:
            c = r.get("course", "未分类")
            by_course[c]["files"].add(r.get("file_id"))
            by_course[c]["pages"] += 1
            by_course[c]["chars"] += r.get("char_count", 0)
            if r.get("is_scan"):
                by_course[c]["scan"] += 1
        print(f"  总页记录 {len(pages)}  文件 {len({r.get('file_id') for r in pages})}  "
              f"文本 {sum(r.get('char_count', 0) for r in pages):,} 字  "
              f"扫描页 {sum(1 for r in pages if r.get('is_scan'))}")
        print(f"  {'课程':<26}{'文件':>5}{'页数':>7}{'扫描页':>7}{'字数':>10}")
        for c in sorted(by_course, key=lambda x: -by_course[x]["pages"]):
            v = by_course[c]
            print(f"  {c:<26}{len(v['files']):>5}{v['pages']:>7}{v['scan']:>7}{v['chars']:>10,}")

    # ---------- M3 转写 ----------
    print("\n" + "-" * 74)
    print(" M3 转写层（课堂录音 -> 带时间戳文本）")
    print("-" * 74)
    tr = load_jsonl(TEXT_DIR / "transcript.jsonl")
    audio_total = archive_stats["录音"]["audios"]
    if not tr:
        print(f"  尚无转写结果。已归档录音 {audio_total} 个。")
        if audio_total:
            print("  在有 GPU 的机器上执行：python tools\\transcribe.py")
    else:
        dur = sum(r.get("duration", 0) for r in tr)
        conf = Counter(r.get("confidence", "?") for r in tr)
        print(f"  转写段 {len(tr)}  覆盖时长 {dur / 3600:.2f} 小时  "
              f"置信度 A={conf.get('A', 0)} B={conf.get('B', 0)} C={conf.get('C', 0)}")
        by_lecture = Counter(r.get("lecture_id") for r in tr)
        print(f"  已完成课时 {len(by_lecture)} 个")

    # ---------- M4 结构化 ----------
    print("\n" + "-" * 74)
    print(" M4 结构化层（知识点 + 证据引用）")
    print("-" * 74)
    kps = load_jsonl(KP_DIR / "kp.jsonl")
    evs = load_jsonl(KP_DIR / "evidence.jsonl")
    skel = KP_DIR / "skeleton.json"
    if skel.exists():
        try:
            s = json.loads(skel.read_text(encoding="utf-8"))
            scourses = s.get("courses") or {}
            n_ch = sum(len(c.get("chapters") or []) for c in scourses.values())
            n_sec = sum(sum(len(ch.get("sections") or []) for ch in (c.get("chapters") or []))
                        for c in scourses.values())
            n_matter = sum(1 for c in scourses.values()
                           for ch in (c.get("chapters") or []) if ch.get("kind") == "matter")
            print(f"  知识骨架: {n_ch} 章 / {n_sec} 节，覆盖 {len(scourses)} 门课"
                  f"（非正文材料 {n_matter} 章已标记排除）")
            for cname, c in sorted(scourses.items()):
                chs = c.get("chapters") or []
                if chs:
                    print(f"           - {cname}: {len(chs)} 章 / "
                          f"{sum(len(x.get('sections') or []) for x in chs)} 节"
                          f"（{c.get('method', '?')}）")
        except Exception as exc:
            print(f"  知识骨架: 文件存在但无法读取（{type(exc).__name__}）")
    else:
        print("  知识骨架: 未构建（python tools\\build_skeleton.py）")
    if not kps:
        print("  知识点: 尚未抽取（python tools\\extract_kp.py）")
    else:
        status = Counter(k.get("status", "?") for k in kps)
        conf = Counter(k.get("confidence", "?") for k in kps)
        print(f"  知识点 {len(kps)} 个   证据引用 {len(evs)} 条")
        print(f"  状态分布: " + "  ".join(f"{k}={v}" for k, v in status.most_common()))
        print(f"  置信度分布: " + "  ".join(f"{k}={v}" for k, v in conf.most_common()))

    # ---------- M5 校验 ----------
    print("\n" + "-" * 74)
    print(" M5 校验层（防幻觉闸门）")
    print("-" * 74)
    audit = load_jsonl(KP_DIR / "audit.jsonl")
    pending = [a for a in audit if a.get("status") == "pending"]
    # 判定依据必须是 **kp.jsonl 里知识点的核验状态**，而不是 audit.jsonl 是否为空：
    #   `audit.jsonl` 是「人工复核队列」——**只有出问题的知识点才入队**。
    #   于是"全部通过"（最理想的情况）反而让 audit 为空，被显示成「尚未校验」，
    #   还建议用户去重跑校验 —— **把成功报成没做**，是最容易让人白忙的一种错。
    verified_n = len([k for k in kps if k.get("status") == "verified" or k.get("verified_at")])
    if not kps:
        print("  尚未校验（python tools\\verify_kp.py）")
    elif verified_n == 0:
        print(f"  尚未校验（{len(kps)} 条知识点都没有核验标记）")
    elif not audit:
        print(f"  校验通过：{verified_n}/{len(kps)} 条已核验，"
              f"**0 条待人工复核**（这是最理想的状态）")
    else:
        kinds = Counter(a.get("type", "?") for a in audit)
        prio = Counter(a.get("priority", "?") for a in pending)
        print(f"  已核验 {verified_n}/{len(kps)} 条；复核队列 {len(audit)} 条，待处理 {len(pending)} 条")
        print(f"  类型分布: " + "  ".join(f"{k}={v}" for k, v in kinds.most_common()))
        print(f"  待处理优先级: " + "  ".join(f"{k}={v}" for k, v in sorted(prio.items())))
        p0 = prio.get("P0", 0)
        print(f"  P0 待办 {p0} 条 " + ("(健康：应长期为 0)" if p0 == 0 else "(需要尽快处理)"))

    # ---------- M6 复习产物 ----------
    print("\n" + "-" * 74)
    print(" M6 复习产物层")
    print("-" * 74)
    cards = load_jsonl(REVIEW_DIR / "cards.jsonl")
    files = sorted(p for p in REVIEW_DIR.glob("*") if p.is_file()) if REVIEW_DIR.exists() else []
    if not files:
        print("  尚无产物（python tools\\make_review.py）")
    else:
        for p in files:
            print(f"  {p.name:<34}{p.stat().st_size / 1024:>9.1f} KB")
        if cards:
            kinds = Counter(c.get("card_type", "?") for c in cards)
            print(f"  记忆卡 {len(cards)} 张: " + "  ".join(f"{k}={v}" for k, v in kinds.most_common()))

    # ---------- 总进度 ----------
    print("\n" + "=" * 74)
    print(" 总进度")
    print("=" * 74)
    total_sessions = sum(c.get("weekly_sessions", 2) for c in courses) * 17 if courses else 0
    print(f"  M1 归档      {bar(archive_stats['课件']['docs'] + archive_stats['规范教材']['docs'], max(total_sessions, 1))}")
    print(f"  M2 解析      {bar(len({r.get('file_id') for r in pages}), max(archive_stats['课件']['docs'] + archive_stats['规范教材']['docs'], 1))}")
    print(f"  M3 转写      {bar(len({r.get('lecture_id') for r in tr}), max(total_sessions, 1))}")
    print(f"  M4 结构化    {bar(len(kps), max(len(pages) // 20, 1))}")
    print(f"  M5 校验      {bar(verified_n, max(len(kps), 1))}")
    print(f"  M6 复习产物  {bar(len(files), 4)}")
    print("\n  下一步建议：")
    steps = []
    if not pages:
        steps.append("python tools\\parse_docs.py          # 先解析已有教材")
    if pages and not skel.exists():
        steps.append("python tools\\build_skeleton.py       # 从教材构建知识骨架")
    if skel.exists() and not kps:
        steps.append("python tools\\extract_kp.py           # 抽取知识点（消耗 API）")
    if kps and not audit:
        steps.append("python tools\\verify_kp.py            # 校验知识点与原文一致性")
    if audit and not files:
        steps.append("python tools\\make_review.py          # 生成背诵卡与对照表")
    if not steps:
        steps.append("全部模块已跑过；可放入课堂课件与录音，或重跑增量更新。")
    for s in steps:
        print(f"    {s}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
