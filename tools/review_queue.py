# -*- coding: utf-8 -*-
"""
review_queue.py —— 人工复核工作表
=====================================

把 M5 审核队列（`audit.jsonl` 的 P0/P1）转成**人能当场裁决**的工作表：
每个待复核条目，**左边是知识点引用的原文片段（quote），右边是它声称的出处分块正文**，
再附上审计判词。判完把结论填回 TSV，用 `--apply` 一次性写回。

## 为什么要做这个（而不是让人打开 PDF 一条条找）

本项目的硬约束是"任何结论都能落到「文件 + 页码」"，代价就是**每条可疑引用都要人回去核原文**。
把这类判断交给本地小模型并不可靠（分类一致率 35%、幻觉标记精度 40%）——
所以这一步**必须由人做**，能优化的只有**人的效率**：

  · 手工做法：打开 PDF → 翻到第 N 页 → 在几千字里找那句话 → 再回来看 quote 是否一致；
  · 本工具：把 quote 和它声称的出处正文**并排放在一起**，人只需要"看有没有"。

这直接决定了整批 P0 是"半小时能扫完"还是"根本没人愿意动手"。

## 用法

    python tools\\review_queue.py --export                  # 导出 P0 工作表（默认）
    python tools\\review_queue.py --export --priority P1     # 导出 P1
    python tools\\review_queue.py --export --course 防排烟工程
    python tools\\review_queue.py --apply 40_复习产物\\06_复核裁决.tsv

产出（写入 40_复习产物/，与 00_待确认清单.md 配套）：
    06_复核工作表.md    —— 同屏对照，人读
    06_复核裁决.tsv     —— 待填的空裁决表，人填完后 --apply

## 裁决取值（--apply 认这三个）

    ok      引用/数字属实 → 该 evidence 判为 exact（人工确认），kp 保留
    bad     确属编造/错误 → 该 kp 标记 rejected（从复习产物中剔除）
    skip    拿不准，暂不裁决（保持 pending）

## 安全约定

  · `--export` **只读**，不碰任何数据；
  · `--apply` 写盘前**自动备份** evidence/kp/audit 三件（改行/删行前必须先备份，
    这是不可省略的一步），并要求显式 `--yes`；
  · 裁决记录写进 audit 行的 `status`（done/ignored）+ 一行 `human_review` 说明，
    不静默丢弃任何一条 —— 谁在什么时候判的，留在数据里。
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
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

import kb          # noqa: E402
import verify_kp   # noqa: E402

SHEET_MD = kb.REVIEW_DIR / "06_复核工作表.md"
SHEET_TSV = kb.REVIEW_DIR / "06_复核裁决.tsv"

VERDICTS = {"ok", "bad", "skip"}
# 并排展示时，两侧各截这么长（够看清"有没有"，又不至于把工作表撑成几百页）
QUOTE_SHOW = 300
BODY_WINDOW = 900


def _body_for(ev: dict, by_key: dict, by_range: dict) -> str:
    """取该条 evidence 声称出处的**分块正文**（与 M5 判定用的是同一套索引）。"""
    full = kb.parse_chunk_key_full(ev.get("chunk_id") or "")
    if not full:
        return ""
    course, ps, pe, ftag = full
    if ftag:
        hit = by_key.get((course, ftag, ps, pe))
        if hit:
            return hit[1]
    cands = by_range.get((course, ps, pe)) or []
    if cands:
        return cands[0][1]
    return verify_kp._fallback_body(course, ps, pe)


def _locate(body: str, quote: str) -> tuple[str, bool]:
    """
    在正文里定位引用，返回 (展示用正文窗口, 是否逐字命中)。

    为什么按**归一化后**的位置回来截窗口：原实现直接 `body.find(quote)`，
    英文书里有换行/合字就找不到，窗口会退化成"从头截 900 字"——
    人看到的是开头，而引用在中段，于是**看起来像证据不存在**（假阴性会误导裁决）。
    改为：先在归一化串里找位置，再按长度比例映射回原文下标。
    """
    if not body:
        return "", False
    nq = kb.normalize_for_match(quote)
    if nq:
        nb = kb.normalize_for_match(body)
        idx = nb.find(nq)
        if idx >= 0:
            ratio = idx / max(len(nb), 1)
            start = max(0, int(len(body) * ratio) - BODY_WINDOW // 3)
            return body[start:start + BODY_WINDOW], True
    return body[:BODY_WINDOW], False


def collect_rows(priority: str, course: str | None) -> tuple[list[dict], dict]:
    audits = kb.load_jsonl(kb.AUDIT_FILE)
    kps = {k.get("kp_id"): k for k in kb.load_jsonl(kb.KP_FILE)}
    evs = kb.load_jsonl(kb.EVIDENCE_FILE)
    ev_by_id = {e.get("evidence_id"): e for e in evs}
    ev_by_kp: dict[str, list[dict]] = defaultdict(list)
    for e in evs:
        ev_by_kp[e.get("kp_id")].append(e)

    by_key, by_range = verify_kp.build_chunk_body_index()

    rows = []
    exp_ids = kb.experimental_kp_ids()
    for a in audits:
        if a.get("priority") != priority:
            continue
        if a.get("status") not in (None, "pending"):
            continue                       # 已裁决过的不再进队列
        if course and course not in (a.get("course") or ""):
            continue
        kp = kps.get(a.get("kp_id")) or {}
        # 审计的 target 对 quote 类是 evidence_id；对数字/定义类就是 kp_id
        ev = ev_by_id.get(a.get("target") or "")
        if ev is None:
            cands = ev_by_kp.get(a.get("kp_id")) or []
            bad = [e for e in cands if e.get("verify_method") in ("fabricated", "not_found", "partial")]
            ev = bad[0] if bad else (cands[0] if cands else None)
        body, hit = ("", False)
        if ev:
            body, hit = _locate(_body_for(ev, by_key, by_range), ev.get("quote") or "")
        rows.append({
            "audit_id": a.get("audit_id"),
            "type": a.get("type"),
            "priority": a.get("priority"),
            "course": a.get("course"),
            "kp_id": a.get("kp_id"),
            "kp_name": a.get("kp_name"),
            "detail": a.get("detail"),
            "suggestion": a.get("suggestion"),
            "target": a.get("target"),
            "definition": kp.get("definition") or "",
            "status": kp.get("status"),
            "page": kp.get("page_label_start"),
            "page_end": kp.get("page_label_end"),
            "source_file": (kp.get("source_file") or ev and ev.get("source_file")) or "",
            "quote": (ev.get("quote") if ev else "") or "",
            "verify_method": (ev.get("verify_method") if ev else "") or "",
            "verify_note": (ev.get("verify_note") if ev else "") or "",
            "body": body,
            "literal_hit": hit,
            "experimental": a.get("kp_id") in exp_ids,
        })
    meta = {"by_key": by_key, "by_range": by_range,
            "total_audit": len(audits), "pending_audit": sum(
                1 for a in audits if a.get("status") in (None, "pending"))}
    return rows, meta


def export(rows: list[dict], meta: dict, priority: str, course: str | None) -> int:
    kb.REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    by_type = Counter(r["type"] for r in rows)
    by_course = Counter(r["course"] for r in rows)

    L = []
    L.append(f"# 复核工作表 · {priority}（这些内容**在裁决前不要背**）")
    L.append("")
    L.append(f"生成时间：{ts}　范围：{'全部课程' if not course else course}")
    L.append("")
    L.append(f"待裁决 **{len(rows)}** 条（audit 共 {meta['total_audit']} 条，"
             f"其中未裁决 {meta['pending_audit']} 条）。")
    L.append("")
    L.append("> **怎么用**：每条下面「引用」与「声称出处的原文」是并排给出的 ——")
    L.append("> 你只需要判断**这段话在不在右边**。判完把结论填进同目录的 "
             "`06_复核裁决.tsv`，再跑")
    L.append("> `python tools\\review_queue.py --apply 40_复习产物\\06_复核裁决.tsv`。")
    L.append(">")
    L.append("> 裁决取值：`ok` 属实 ／ `bad` 确属编造或错误 ／ `skip` 拿不准（保持待复核）。")
    L.append("")
    L.append("| 问题类型 | 条数 |")
    L.append("|---|---:|")
    for t, n in by_type.most_common():
        L.append(f"| {t} | {n} |")
    L.append("")
    L.append("| 课程 | 条数 |")
    L.append("|---|---:|")
    for c, n in by_course.most_common():
        L.append(f"| {c} | {n} |")
    L.append("")
    L.append("---")
    L.append("")

    for i, r in enumerate(rows, 1):
        mark = "★实验批次 " if r["experimental"] else ""
        L.append(f"## {i}. {mark}{r['kp_name']}")
        L.append("")
        L.append(f"- **审计**：`{r['audit_id']}`　类型 `{r['type']}`　优先级 `{r['priority']}`")
        L.append(f"- **课程**：{r['course']}　**知识点**：`{r['kp_id']}`　**状态**：{r['status']}")
        L.append(f"- **出处**：{str(r['source_file']).split('/')[-1]}　"
                 f"**页码**：{r['page']}–{r['page_end']}")
        L.append(f"- **判词**：{r['detail']}")
        L.append(f"- **建议**：{r['suggestion']}")
        L.append("")
        if r["definition"]:
            L.append(f"**知识点定义**：{r['definition'][:300]}")
            L.append("")
        L.append(f"### 引用（`{r['verify_method']}`"
                 f"{'，机器已能逐字命中' if r['literal_hit'] else '，机器判定未逐字命中'}）")
        L.append("")
        L.append("```text")
        L.append((r["quote"] or "（该知识点没有引用片段）")[:QUOTE_SHOW])
        L.append("```")
        L.append("")
        if r["verify_note"]:
            L.append(f"> 机器说明：{r['verify_note'][:200]}")
            L.append("")
        L.append("### 声称出处的原文（同屏对照）")
        L.append("")
        L.append("```text")
        L.append((r["body"] or "（取不到分块正文 —— 该条需人工打开原文件核对）")[:BODY_WINDOW])
        L.append("```")
        L.append("")
        L.append(f"**裁决**：`[ ] ok`　`[ ] bad`　`[ ] skip`　"
                 f"（audit_id `{r['audit_id']}`）")
        L.append("")
        L.append("---")
        L.append("")

    SHEET_MD.write_text("\n".join(L), encoding="utf-8")

    with SHEET_TSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["audit_id", "kp_id", "course", "kp_name", "type", "裁决(ok/bad/skip)", "备注"])
        for r in rows:
            w.writerow([r["audit_id"], r["kp_id"], r["course"], r["kp_name"], r["type"], "", ""])

    print(f"工作表：{SHEET_MD}（{len(rows)} 条）")
    print(f"裁决表：{SHEET_TSV}（填完后 --apply）")
    print(f"问题类型：{dict(by_type)}")
    return 0


def apply(tsv: Path, yes: bool) -> int:
    if not tsv.exists():
        print(f"[FAIL] 找不到裁决表：{tsv}")
        return 2
    rows = []
    with tsv.open("r", encoding="utf-8", newline="") as f:
        rd = csv.DictReader(f, delimiter="\t")
        for r in rd:
            v = (r.get("裁决(ok/bad/skip)") or "").strip().lower()
            if v:
                rows.append((r, v))
    if not rows:
        print("[FAIL] 裁决表里一条都没填（裁决列全空）。填 ok/bad/skip 后再跑。")
        return 2
    bad_v = sorted({v for _, v in rows if v not in VERDICTS})
    if bad_v:
        print(f"[FAIL] 非法裁决值 {bad_v}（只认 {sorted(VERDICTS)}）")
        return 2
    if not yes:
        print(f"[STOP] 将按 {len(rows)} 条裁决写回 evidence / kp / audit。")
        print("       确认请加 --yes（写盘前会自动备份三件数据文件）。")
        return 0

    # ---- 备份（改行/删行前必须先备份，且不能靠猜）----
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bk = kb.LOG_DIR / f"_备份_复核回写_{stamp}"
    bk.mkdir(parents=True, exist_ok=True)
    for src in (kb.EVIDENCE_FILE, kb.KP_FILE, kb.AUDIT_FILE):
        if src.exists():
            shutil.copy2(src, bk / src.name)
    print(f"已备份：{bk}")

    kps = {k.get("kp_id"): k for k in kb.load_jsonl(kb.KP_FILE)}
    evs = kb.load_jsonl(kb.EVIDENCE_FILE)
    audits = kb.load_jsonl(kb.AUDIT_FILE)
    ev_by_id = {e.get("evidence_id"): e for e in evs}
    aud_by_id = {a.get("audit_id"): a for a in audits}
    ev_by_kp: dict[str, list[dict]] = defaultdict(list)
    for e in evs:
        ev_by_kp[e.get("kp_id")].append(e)

    now = datetime.now().isoformat(timespec="seconds")
    stat = Counter()
    for r, v in rows:
        aid, kp_id = r.get("audit_id"), r.get("kp_id")
        a = aud_by_id.get(aid)
        kp = kps.get(kp_id)
        if a is None or kp is None:
            stat["未匹配"] += 1
            continue
        a["status"] = "done" if v in ("ok", "bad") else "pending"
        a["human_review"] = {"verdict": v, "at": now, "by": "人工复核工作表"}
        # ok → 该 kp 的全部 evidence 视为人工确认（写入口径，不覆盖机器判词）
        if v == "ok":
            for e in ev_by_kp.get(kp_id, []):
                e["human_verified"] = True
                e["human_verified_at"] = now
            kp["status"] = "verified"
            stat["ok"] += 1
        elif v == "bad":
            kp["status"] = "rejected"
            kp["rejected_at"] = now
            kp["reject_reason"] = f"人工复核判定不可用（{aid}）"
            stat["bad"] += 1
        else:
            stat["skip"] += 1

    kb.rewrite_jsonl(kb.EVIDENCE_FILE, evs)
    kb.rewrite_jsonl(kb.KP_FILE, list(kps.values()))
    kb.rewrite_jsonl(kb.AUDIT_FILE, audits)
    print(f"已写回：{dict(stat)}")
    print("下一步：python -m firekb review   （重跑 M6，把 rejected 从产物里剔除）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="人工复核工作表")
    ap.add_argument("--export", action="store_true", help="导出工作表（只读）")
    ap.add_argument("--apply", metavar="TSV", help="按裁决表回写")
    ap.add_argument("--priority", default="P0", choices=["P0", "P1"], help="默认 P0")
    ap.add_argument("--course", help="只导出某门课")
    ap.add_argument("--yes", action="store_true", help="确认写回")
    args = ap.parse_args()

    if args.apply:
        return apply(Path(args.apply), args.yes)

    rows, meta = collect_rows(args.priority, args.course)
    if not rows:
        print(f"[i] {args.priority} 队列里没有待裁决条目"
              f"（{'课程 ' + args.course if args.course else '全部课程'}）。")
        return 0
    return export(rows, meta, args.priority, args.course)


if __name__ == "__main__":
    sys.exit(main())
