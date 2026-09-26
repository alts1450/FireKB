# -*- coding: utf-8 -*-
"""
dedupe_sources.py —— 按声明清理重复来源
=========================================

读取 `00_配置/references.json` 的 `duplicates` 声明，把"同一部文本的重复格式"从
`pages.jsonl` 里剔除，只保留声明为 `keep` 的那一份。

## 为什么做成工具、而不是手工删那两行

1. **删行是危险动作**。"删之前先备份"如果只写在文档里，就只靠人记得；
   工具化以后，备份、干跑、拒跑条件都是代码写死的，忘不掉。
2. **每份数据副本都要各跑一次**。把数据整体推给对方有覆盖风险（对方的进度被冲掉），
   所以正确做法不是"删完推数据"，而是**同一个工具在各份数据上各跑一次** ——
   输入同源（pages.jsonl 的 md5 一致），输出自然一致。
3. 声明在 `references.json` 里已有（谁重复、留哪份、为什么），工具只负责执行它 ——
   "声明"与"执行"分离，事后复核时一眼能看出这是有据可依的清理，不是随手删的。

## 用法

    python tools\\dedupe_sources.py --check     # 只报告将删除什么（默认，只读）
    python tools\\dedupe_sources.py --apply --yes   # 执行（自动备份）

## 拒跑条件（fail-loud，不做"尽力而为"）

  · 找不到声明的重复项、或声明里的 work 匹配不到任何页 → 拒跑（说明声明与数据脱节）；
  · **要删的行被任何 kp/evidence 引用** → 拒跑（会制造孤儿引用，比重复更糟）；
  · `keep` 那一份在这个库里不存在或 0 页 → 拒跑（不能把唯一一份删掉）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(os.environ.get("FIREKB_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "tools"))

import kb  # noqa: E402


def plan() -> tuple[list[dict], list[str]]:
    """算出将要删除的页行；返回 (计划, 拒跑原因)。"""
    dups = kb.reference_duplicates()
    pages = kb.load_jsonl(kb.PAGES_FILE)
    kp = kb.load_jsonl(kb.KP_FILE)
    ev = kb.load_jsonl(kb.EVIDENCE_FILE)

    block: list[str] = []
    out: list[dict] = []
    if not dups:
        block.append("references.json 的 duplicates 为空 —— 没有需要清理的重复来源")

    for d in dups:
        wid, keep, drop = d.get("work"), (d.get("keep") or "").lower(), (d.get("drop") or "").lower()
        work = next((w for w in kb.reference_works() if w.get("id") == wid), None)
        if work is None:
            block.append(f"duplicates 声明的 work={wid} 在 works 里找不到")
            continue

        def _rows(kind: str) -> list[dict]:
            return [p for p in pages
                    if (kb.resolve_reference(p.get("source_file")) or {}).get("id") == wid
                    and str(p.get("source_kind") or "").lower() == kind]

        keep_rows, drop_rows = _rows(keep), _rows(drop)
        if not keep_rows:
            block.append(f"{wid}：声明的 keep={keep} 在这个库里 0 页 —— 拒绝删除唯一一份")
            continue
        if not drop_rows:
            out.append({"work": wid, "status": "无需清理", "drop": drop, "rows": []})
            continue

        drop_files = {p.get("file_id") for p in drop_rows}
        drop_srcs = {p.get("source_file") for p in drop_rows}
        # 关键守卫：要删的来源不能被任何 kp/evidence 引用（否则会制造孤儿引用，比重复更糟）
        # ⚠️ 比对用 `source_file` 而不是从 chunk_id 反解 file_tag：
        #    kp/evidence 本来就带 source_file 字段，直接比是**最不易出错**的判据；
        #    反解标签要处理"带标签/不带标签/消歧后缀"三种形态，判错的方向恰好是"以为没人引用"。
        ref_kp = sorted({r.get("kp_id") for r in kp if r.get("source_file") in drop_srcs})
        ref_ev = sorted({e.get("evidence_id") for e in ev if e.get("source_file") in drop_srcs})
        if ref_kp or ref_ev:
            block.append(f"{wid}：要删的 {drop} 版仍被 {len(ref_kp)} 个知识点 / "
                         f"{len(ref_ev)} 条证据引用 —— 拒绝删除（会制造孤儿引用）")
            continue

        out.append({
            "work": wid, "status": "将删除", "drop": drop, "keep": keep,
            "rows": drop_rows,
            "pages": len(drop_rows),
            "chars": sum(len(p.get("text") or "") for p in drop_rows),
            "files": sorted({str(p.get("source_file")).split("/")[-1] for p in drop_rows}),
        })
    return out, block


def main() -> int:
    ap = argparse.ArgumentParser(description="按 references.json 声明清理重复来源")
    ap.add_argument("--check", action="store_true", help="只报告（默认行为，只读）")
    ap.add_argument("--apply", action="store_true", help="执行删除")
    ap.add_argument("--yes", action="store_true", help="配合 --apply 确认写盘")
    args = ap.parse_args()

    plans, block = plan()
    print("=" * 78)
    print("重复来源清理（声明见 00_配置/references.json 的 duplicates）")
    print("=" * 78)
    print(f"pages.jsonl 现有 {len(kb.load_jsonl(kb.PAGES_FILE))} 行")
    print()
    for p in plans:
        if p["status"] == "无需清理":
            print(f"  [{p['work']}] 无需清理（{p['drop']} 版一页都没有）")
            continue
        print(f"  [{p['work']}] 将删除 {p['drop']} 版：{p['pages']} 行 / {p['chars']} 字符")
        for f in p["files"]:
            print(f"       - {f[:76]}")
        print(f"     保留 {p['keep']} 版（{p['keep']} 版存在且在库中）")
    print()
    if block:
        print("[STOP] 拒绝执行：")
        for b in block:
            print(f"   · {b}")
        return 3

    targets = [p for p in plans if p["status"] == "将删除"]
    if not targets:
        print("没有需要删除的行。")
        return 0
    if not args.apply:
        print("（--check 模式，未写盘。执行：--apply --yes）")
        return 0
    if not args.yes:
        print("[STOP] 将写盘。确认请加 --yes（写盘前自动备份）。")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bk = kb.LOG_DIR / f"_备份_去重_{stamp}"
    bk.mkdir(parents=True, exist_ok=True)
    shutil.copy2(kb.PAGES_FILE, bk / kb.PAGES_FILE.name)
    print(f"已备份：{bk}")

    drop_keys = set()
    for p in targets:
        for r in p["rows"]:
            drop_keys.add((r.get("source_file"), r.get("page_no")))
    pages = kb.load_jsonl(kb.PAGES_FILE)
    kept = [p for p in pages if (p.get("source_file"), p.get("page_no")) not in drop_keys]
    removed = len(pages) - len(kept)
    kb.rewrite_jsonl(kb.PAGES_FILE, kept)
    print(f"已写回：{kb.PAGES_FILE}   删除 {removed} 行，剩余 {len(kept)} 行")
    print("\n下一步：")
    print("  1) 在**每一份数据副本**上跑同一个工具（各跑一次，不要整体推数据）：")
    print("     python tools\\dedupe_sources.py --apply --yes")
    print("  2) python tools\\fingerprint.py --diff ...  核对各副本 pages.jsonl 是否仍同源")
    return 0


if __name__ == "__main__":
    sys.exit(main())
