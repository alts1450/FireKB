# -*- coding: utf-8 -*-
"""
产物契约校验器（schema_check）
==============================
    python tools\\schema_check.py            # 人类可读报告
    python tools\\schema_check.py --json     # 机器可读（便于自动化/流水线）
    python tools\\schema_check.py --counts   # 只打印规模

为什么需要它：
  本项目的产物是**多脚本接力**写出来的（parse→ocr→skeleton→extract→verify→review），
  字段契约只写在各自的代码里。字段一旦悄悄漂移就无人察觉，例如：
    · 某批 evidence 缺 `verify_tier` / `rechecked_at`（另一批有）——导致
      「verify_tier == verify_method」这条不变量在这批数据上**根本无法照搬**；
    · 某批 kp 缺 `verified_at`（另一批有）。
  这类漂移不会报错，只会让人在几天后用错口径。本工具把契约显式化并每次跑批后核对。

判定分三档：
  ✗ FAIL  必填字段缺失 / 类型不符 / 主键重复 / 外键断裂  → 退出码 1
  ! WARN  可选字段整列缺失（往往是口径变了）/ 取值超出枚举
  · INFO  仅统计（行数、字段集、字段出现情况）
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb  # noqa: E402

# ---------------------------------------------------------------- 契约声明
# kind: req=必填, opt=可选, type=类型约束（list/dict/int/bool/str）, enum=取值枚举
CONTRACTS = {
    "kp.jsonl": {
        "path": lambda: kb.KP_FILE,
        "req": ["kp_id", "course", "chunk_id", "name", "definition"],
        "opt": ["aliases", "chapter", "confidence", "course_id", "created_at", "evidence_ids",
                "importance", "node_id", "note", "numbers", "page_label_end",
                "page_label_start", "run_id", "section", "source_file", "status", "type",
                "verified_at"],
        "types": {"aliases": list, "numbers": list, "evidence_ids": list},
        # rejected 由 review_queue --apply 写入（人工复核判定不可用）。
        # 加它之前先确认下游都认：契约要跟着**真实取值**走，也要挡在写入之前。
        "enum": {"status": {"verified", "pending", "rejected"}},
        "key": "kp_id",
    },
    "evidence.jsonl": {
        "path": lambda: kb.EVIDENCE_FILE,
        "req": ["evidence_id", "kp_id", "chunk_id", "quote", "verify_method"],
        "opt": ["chapter", "course", "course_id", "evidence_type", "extracted_at", "node_id",
                "page_label", "quote_verified", "rechecked_at", "run_id", "section",
                "source_file", "verify_tier"],
        "types": {"quote_verified": bool},
        "enum": {"verify_method": {"exact", "partial", "fabricated", "not_found"}},
        "key": "evidence_id",
        "fk": {"kp_id": "kp.jsonl"},
    },
    "audit.jsonl": {
        "path": lambda: kb.AUDIT_FILE,
        # 注意：时间字段叫 `created_at`（不是 `at`）—— 契约要跟着数据走，不能反过来。
        "req": ["audit_id", "priority", "type", "kp_id"],
        "opt": ["course", "course_id", "created_at", "detail", "kp_name", "status",
                "suggestion", "target"],
        "enum": {"priority": {"P0", "P1", "P2"}, "status": {"pending", "done", "ignored"}},
        "key": "audit_id",
    },
    "pages.jsonl": {
        "path": lambda: kb.PAGES_FILE,
        # 注意：`text` 为**可选** —— 封面/版权页/纯图表页本来就没有正文，这类空文本行确实存在，
        # 这是设计如此（也是"state_ocr.done 与 ocr_results 行数不相等"的原因之一）。
        # 把它当必填会产生假失败。
        "req": ["course", "file_id", "page_no", "source_file"],
        "opt": ["char_count", "has_table", "is_scan", "ocr_at", "ocr_confidence", "ocr_engine",
                "ocr_lines", "page_label", "section_title", "source_kind", "source_sha1",
                "text", "text_flat", "text_source"],
        "types": {},
        "key": None,
    },
    "transcript.jsonl": {
        "path": lambda: kb.TEXT_DIR / "transcript.jsonl",
        # 该产物按设计只存在于承担转写的那台机器（不会被同步工具推送）；当前机器缺失属正常
        "machine_local": True,
        "req": ["lecture_id", "course", "source_file", "source_sha1", "start", "end", "text"],
        "opt": ["avg_logprob", "confidence", "duration", "engine", "run_id", "seg_type",
                "speaker", "transcribed_at", "usage", "weight"],
        "enum": {"confidence": {"A", "B", "C"}},
        "key": None,
    },
}

# JSON 结构契约（非 jsonl）
JSON_CONTRACTS = {
    "skeleton.json": {
        "path": lambda: kb.SKELETON_FILE,
        "req_top": ["courses"],
        "note": "courses = {课名: {chapters: [...]}}",
    },
    "state_extract.json": {
        "path": lambda: kb.KP_DIR / "state_extract.json",
        "req_top": ["done", "updated_at"],
        "forbid_top": {"kps": "键容器在磁盘上叫 `done`，`kps` 只是代码内 State 对象的属性名（命名不一致）"},
    },
    "engines.json": {
        "path": lambda: kb.CONFIG_DIR / "engines.json",
        "req_top": ["parser", "ocr", "asr"],
        "note": "引擎选择配置",
    },
    # 这两个配置是**承重的** —— consistency 的条文库口径与 make_review 的产物层排除都读它们。
    # 配置写坏了（少个键、写成数组）不会崩，只会**静默不生效** —— 正是本项目最怕的那种坏法。
    "references.json": {
        "path": lambda: kb.CONFIG_DIR / "references.json",
        "req_top": ["library", "works"],
        "note": "规范条文库：works[].match 是 source_file 的子串，由 kb.resolve_reference 解析",
    },
    "experimental-batches.json": {
        "path": lambda: kb.CONFIG_DIR / "experimental-batches.json",
        "req_top": ["batches"],
        "note": "实验批次登记：按 (course + kp_id_prefix + created_at) 命中，make_review 默认排除",
    },
}

MAX_SHOW = 5


def _rows(p: Path) -> tuple[list[dict], list[str]]:
    bad = []
    rows = []
    for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception as exc:
            bad.append(f"第 {i} 行不是合法 JSON（{type(exc).__name__}）")
    return rows, bad


def check_jsonl(name: str, spec: dict, out: list) -> dict:
    p = spec["path"]()
    res = {"file": name, "path": str(p), "exists": p.exists(), "rows": 0,
           "fail": [], "warn": [], "info": {}}
    if not p.exists():
        if spec.get("machine_local"):
            res["info"]["说明"] = "该产物按设计只在承担转写的那台机器上生成（见 sync.py 的 NEVER 清单），本处缺失属正常"
        else:
            res["warn"].append("文件不存在（该环节可能还没跑）")
        return res

    rows, bad = _rows(p)
    res["rows"] = len(rows)
    res["fail"] += bad[:MAX_SHOW]
    if not rows:
        res["warn"].append("文件为空")
        return res

    # 字段覆盖统计
    seen = Counter()
    for r in rows:
        seen.update(r.keys())
    present = set(seen)
    res["info"]["fields"] = sorted(present)
    missing_cols = [f for f in spec["req"] if f not in present]
    if missing_cols:
        res["fail"].append(f"必填字段整列缺失：{missing_cols}")
    soft = [f for f in spec.get("opt", []) if f not in present]
    if soft:
        res["warn"].append(f"可选字段整列缺失（口径可能变过）：{soft}")

    # 逐行必填 + 类型 + 枚举
    bad_req = Counter()
    bad_type = Counter()
    bad_enum = Counter()
    for r in rows:
        for f in spec["req"]:
            if r.get(f) in (None, ""):
                bad_req[f] += 1
        for f, t in spec.get("types", {}).items():
            v = r.get(f)
            if v is not None and not isinstance(v, t):
                bad_type[f] += 1
        for f, allowed in spec.get("enum", {}).items():
            v = r.get(f)
            if v is not None and v not in allowed:
                bad_enum[f] += 1
    for f, n in bad_req.items():
        res["fail"].append(f"必填字段为空：{f}（{n} 行）")
    for f, n in bad_type.items():
        res["fail"].append(f"类型不符：{f}（{n} 行）")
    for f, n in bad_enum.items():
        res["warn"].append(f"取值超出枚举：{f}（{n} 行）")

    # 主键唯一
    key = spec.get("key")
    if key:
        c = Counter(r.get(key) for r in rows)
        dup = [k for k, n in c.items() if n > 1]
        res["info"]["unique_%s" % key] = len(c)
        if dup:
            res["fail"].append(f"主键重复：{key} 有 {len(dup)} 个重复值，例：{dup[:3]}")
    return res


def cross_check(results: dict, out: list) -> None:
    """外键与跨文件一致性（kp ↔ evidence / chunk_id 形态）"""
    kp_p = CONTRACTS["kp.jsonl"]["path"]()
    ev_p = CONTRACTS["evidence.jsonl"]["path"]()
    if not (kp_p.exists() and ev_p.exists()):
        return
    kp, _ = _rows(kp_p)
    ev, _ = _rows(ev_p)
    kp_ids = {r.get("kp_id") for r in kp}
    broken = [e.get("evidence_id") for e in ev if e.get("kp_id") not in kp_ids]
    out.append({"file": "跨文件：evidence→kp", "path": "(跨文件一致性检查)",
                "fail": ([f"外键断裂 {len(broken)} 条，例：{broken[:3]}"]
                                                     if broken else []),
                "warn": [], "info": {"evidence_rows": len(ev), "kp_rows": len(kp)}, "rows": len(ev)})

    bad_kp_refs = 0
    ev_ids = {e.get("evidence_id") for e in ev}
    for r in kp:
        for eid in (r.get("evidence_ids") or []):
            if eid not in ev_ids:
                bad_kp_refs += 1
    out.append({"file": "跨文件：kp→evidence", "path": "(跨文件一致性检查)",
                "fail": ([f"kp.evidence_ids 指向不存在的证据 {bad_kp_refs} 处"]
                                                      if bad_kp_refs else []),
                "warn": [], "info": {}, "rows": len(kp)})

    legacy = [r.get("chunk_id") for r in kp if r.get("chunk_id") and "::f" not in r["chunk_id"]]
    out.append({"file": "chunk_id 形态", "path": "(字段形态检查)", "fail": [],
                "warn": ([f"{len(legacy)} 条 chunk_id 无文件标签段（旧口径残留），例：{legacy[:2]}"]
                         if legacy else []),
                "info": {"带标签": len(kp) - len(legacy)}, "rows": len(kp)})


def check_json(name: str, spec: dict) -> dict:
    p = spec["path"]()
    res = {"file": name, "path": str(p), "exists": p.exists(), "rows": 0, "fail": [], "warn": [],
           "info": {}}
    if not p.exists():
        res["warn"].append("文件不存在")
        return res
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        res["fail"].append(f"不是合法 JSON：{type(exc).__name__}: {exc}")
        return res
    if not isinstance(data, dict):
        res["fail"].append("顶层不是对象（dict）")
        return res
    missing = [k for k in spec.get("req_top", []) if k not in data]
    if missing:
        res["fail"].append(f"顶层缺键：{missing}")
    for bad, why in (spec.get("forbid_top") or {}).items():
        if bad in data:
            res["fail"].append(f"顶层出现了不该有的键 `{bad}`：{why}")
    res["info"]["top_keys"] = list(data.keys())
    if name == "skeleton.json":
        cs = data.get("courses") or {}
        res["info"]["courses"] = len(cs)
        res["info"]["chapters"] = sum(len(c.get("chapters") or []) for c in cs.values())
        res["info"]["sections"] = sum(len(ch.get("sections") or [])
                                      for c in cs.values() for ch in (c.get("chapters") or []))
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="FireKB 产物契约校验")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--counts", action="store_true", help="只打印规模")
    args = ap.parse_args()

    results = []
    for name, spec in CONTRACTS.items():
        results.append(check_jsonl(name, spec, []))
    cross_check({}, [])
    cross = []
    cross_check({}, cross)
    results += cross
    for name, spec in JSON_CONTRACTS.items():
        results.append(check_json(name, spec))

    fails = [(r["file"], f) for r in results for f in r.get("fail", [])]
    warns = [(r["file"], w) for r in results for w in r.get("warn", [])]

    if args.json:
        print(json.dumps({"results": results, "fails": fails, "warns": warns,
                          "ok": not fails}, ensure_ascii=False, indent=2))
        return 1 if fails else 0

    print("=" * 92)
    print("FireKB 产物契约校验")
    print("=" * 92)
    for r in results:
        mark = "FAIL" if r.get("fail") else ("WARN" if r.get("warn") else "OK")
        print("\n[%s] %s" % (mark, r["file"]))
        if args.counts:
            print("       行数=%s  %s" % (r.get("rows"), r.get("info", {})))
            continue
        print("       路径：%s" % r["path"])
        if r.get("rows"):
            print("       行数：%s" % r["rows"])
        for k, v in (r.get("info") or {}).items():
            if k == "fields":
                continue
            print("       %s：%s" % (k, v))
        for f in r.get("fail", []):
            print("       ✗ %s" % f)
        for w in r.get("warn", []):
            print("       ! %s" % w)

    print("\n" + "=" * 92)
    if fails:
        print("★ 契约校验失败 %d 项：" % len(fails))
        for f, m in fails:
            print("   - [%s] %s" % (f, m))
    else:
        print("契约校验通过（%d 个文件/检查项，%d 条提醒）" % (len(results), len(warns)))
    if warns and not args.counts:
        print("提醒（不阻塞）：")
        for f, m in warns[:12]:
            print("   - [%s] %s" % (f, m))
    print("=" * 92)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
