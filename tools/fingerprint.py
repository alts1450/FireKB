# -*- coding: utf-8 -*-
"""
fingerprint.py —— 产物与脚本指纹（冻结对账用）
==============================================

**为什么需要**：本项目用「哈希 + 时点」作为**口径冻结**的手段（README 的"冻结三件套"、
md5 对账）。临时敲一段 Python 来算哈希很容易敲错口径（取头 12 位还是 16 位、
md5 还是 sha256），而**两个口径混用会让"对得上"变成运气**。

用法：
    python tools\\fingerprint.py                 # 数据产物 + 关键脚本
    python tools\\fingerprint.py --data          # 只看数据产物（冻结对账）
    python tools\\fingerprint.py --code          # 只看代码
    python tools\\fingerprint.py --json out.json # 写一份机器可读清单（用于跨机比对）
    python tools\\fingerprint.py --diff other.json  # 与另一份 json 清单比对（跨机同源核对）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb  # noqa: E402

# 数据产物：口径冻结的对账对象（顺序稳定，便于人工比对两份清单）
DATA_FILES = [
    "20_文本库/pages.jsonl",
    "20_文本库/ocr_results.jsonl",
    "30_知识点/skeleton.json",
    "30_知识点/kp.jsonl",
    "30_知识点/evidence.jsonl",
    "30_知识点/audit.jsonl",
    "30_知识点/state_extract.json",
    "40_复习产物/00_待确认清单.md",
]
# 代码：环节入口 + 共享层 + 引擎层 + 自测
CODE_FILES = [
    "firekb.py",
    "tools/kb.py", "tools/llm.py", "tools/pipeline.py", "tools/status.py",
    "tools/parse_docs.py", "tools/ocr_pages.py", "tools/transcribe.py",
    "tools/build_skeleton.py", "tools/review_skeleton.py",
    "tools/extract_kp.py", "tools/verify_kp.py", "tools/make_review.py",
    "tools/consistency.py", "tools/schema_check.py", "tools/engines_check.py",
    "tools/sync.py", "tools/engines/__init__.py", "tools/engines/base.py",
    "tools/selftest/test_kb.py", "tools/selftest/test_engines.py",
]


def hashes(p: Path) -> dict:
    md5 = hashlib.md5()
    sha = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            md5.update(b)
            sha.update(b)
    st = p.stat()
    return {"bytes": st.st_size,
            "md5": md5.hexdigest(),
            "sha256_16": sha.hexdigest().upper()[:16],
            "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}


def collect(files: list[str]) -> dict:
    out: dict[str, dict] = {}
    for rel in files:
        p = kb.ROOT / rel
        out[rel] = hashes(p) if p.exists() else {"missing": True}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="FireKB 产物/脚本指纹")
    ap.add_argument("--data", action="store_true", help="只看数据产物")
    ap.add_argument("--code", action="store_true", help="只看代码")
    ap.add_argument("--json", help="把清单写到该 json 文件")
    ap.add_argument("--diff", help="与另一份 json 清单比对（跨机同源核对）")
    args = ap.parse_args()

    want_data = args.data or not args.code
    want_code = args.code or not args.data
    manifest = {"root": str(kb.ROOT), "generated_at": kb.now_iso(), "files": {}}

    if args.diff:
        other = json.loads(Path(args.diff).read_text(encoding="utf-8"))
        cur = collect(list(DATA_FILES))
        print("=" * 92)
        print(f"跨机对账：本机 {kb.ROOT}  ←→  清单 {args.diff}（{other.get('root')}）")
        print("=" * 92)
        print(f"  {'文件':<34}{'本机 md5':<16}{'对端 md5':<16}{'字节':>10}  结论")
        print("-" * 92)
        same = diff = miss = 0
        for rel in DATA_FILES:
            a = cur.get(rel, {})
            b = (other.get("files") or {}).get(rel, {})
            if a.get("missing") or b.get("missing"):
                verdict, miss = "缺失（一侧无）", miss + 1
            elif a.get("md5") == b.get("md5"):
                verdict, same = "一致", same + 1
            else:
                verdict, diff = "★不一致", diff + 1
            print(f"  {rel:<34}{str(a.get('md5','-'))[:12]:<16}{str(b.get('md5','-'))[:12]:<16}"
                  f"{a.get('bytes', 0):>10}  {verdict}")
        print("-" * 92)
        print(f"一致 {same} ｜ 不一致 {diff} ｜ 缺失 {miss}")
        return 0 if diff == 0 and miss == 0 else 3

    if want_data:
        manifest["files"].update(collect(DATA_FILES))
    if want_code:
        manifest["files"].update(collect(CODE_FILES))

    print("=" * 92)
    print(f"FireKB 指纹清单   项目根 {kb.ROOT}   生成于 {manifest['generated_at']}")
    print("=" * 92)
    print(f"  {'文件':<34}{'参数':<8}{'md5（前12）':<16}{'sha256（前16）':<18}{'字节':>10}  修改时间")
    print("-" * 92)
    for rel, h in manifest["files"].items():
        if h.get("missing"):
            print(f"  {rel:<34}{'—':<8}{'(缺失)':<16}{'':<18}{'':>10}")
            continue
        kind = "数据" if rel in DATA_FILES else "代码"
        print(f"  {rel:<34}{kind:<8}{h['md5'][:12]:<16}{h['sha256_16']:<18}{h['bytes']:>10}  "
              f"{h['mtime'][:19]}")
    print("-" * 92)
    print(f"共 {len(manifest['files'])} 个文件（数据 {sum(1 for k in manifest['files'] if k in DATA_FILES)}）")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"清单已写出：{out}（在另一台机器上用 --diff 对账）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
