# -*- coding: utf-8 -*-
"""
state 主键迁移（显式脚本，幂等）
================================
把 `30_知识点/state_extract.json` 里的**旧格式键**

    {course}::{chapter}::{section}::p{start}-{end}::{i:04d}

迁移为**新格式主键**

    {course}::p{start}-{end}

并补算内容指纹 `fp`（页范围 + 字符数 + 首 32 字哈希，见 `kb.chunk_fingerprint`）。

为什么必须迁移
--------------
主键末段的位置序号 `{i:04d}` 会随骨架重建**整体漂移**：只要骨架动过一次，
全部 state 键立刻失效、断点续跑形同虚设，对不上块的键会被当成"没做过"而重抽 ——
知识点条目因此成倍增长（重抽一遍就翻一倍）。

⚠️ `state_*.json` 属于单机进度，不会被同步工具推送，
所以**每一份独立的数据副本都必须各自迁移一次**。两道网 + 一道闸：
  ① **本脚本**（显式、可预演、带自检）—— 对每份数据副本各跑一次、各自备份；
  ② `extract_kp.py` 启动时的自动迁移兜底 —— 只在有人忘记时兜住；
  ③ `extract_kp.py` 的**陈旧主键守卫** —— 自动迁移仍无法解决时直接拒跑（返回码 3）。

用法：
  python tools\\migrate_state.py              # 迁移（自动备份旧文件）
  python tools\\migrate_state.py --check      # 只报告将要做什么，不写
  $env:FIREKB_ROOT="D:\\其它副本"; python tools\\migrate_state.py   # 迁移另一份数据副本
"""

from __future__ import annotations

import argparse
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

import extract_kp  # noqa: E402
import kb  # noqa: E402


def build_index() -> tuple[dict, dict]:
    """重建全部块，返回 (chunk_by_key, page_index)。"""
    skel = kb.load_json(kb.SKELETON_FILE, {}) or {}
    chunk_by_key: dict = {}
    page_index: dict = {}
    for course, pages in kb.group_pages_by_course().items():
        usable = [p for p in pages if (p.get("text") or "").strip()]
        if not usable:
            continue
        for c in extract_kp.chunks_from_skeleton(course, usable, skel):
            chunk_by_key[c["chunk_id"]] = c
            page_index[(c["course"], c["page_start"], c["page_end"])] = c
    return chunk_by_key, page_index


def main() -> int:
    ap = argparse.ArgumentParser(description="FireKB state 主键迁移")
    ap.add_argument("--state", default="extract", help="state 名（默认 extract -> state_extract.json）")
    ap.add_argument("--check", action="store_true", help="只报告，不写文件")
    args = ap.parse_args()

    state = kb.State(args.state)
    print("=" * 74)
    print(f" FireKB state 主键迁移    ROOT={ROOT}")
    print("=" * 74)
    print(f"state 文件：{state.path}")
    if not state.path.exists():
        print("[FAIL] state 文件不存在，无需迁移")
        return 2
    print(f"迁移前键数：{state.count()}")

    legacy = [k for k in state.done if kb.is_legacy_chunk_key(k)]
    print(f"其中旧格式键（含位置序号）：{len(legacy)}")
    for k in legacy[:5]:
        print(f"  - {k}")
    if len(legacy) > 5:
        print(f"  ... 其余 {len(legacy) - 5} 个")

    chunk_by_key, page_index = build_index()
    print(f"已用当前代码重建块：{len(chunk_by_key)} 个（用于补算指纹）")

    if args.check:
        # 只算不写：在副本上跑一遍迁移，报告结果
        probe = kb.State(args.state)
        rep = kb.migrate_state_keys(probe, chunk_by_key=chunk_by_key, page_index=page_index)
        print("\n[--check] 将会：")
        print(f"  迁移旧格式键 {rep['migrated']} 个，补算指纹 {rep['fp_filled']} 个，"
              f"合并重复 {rep['dropped_dup']} 个，无法解析 {len(rep['unmatched'])} 个")
        print("\n  新旧键 1:1 映射表（旧键 -> 新键 | fp | at / chars / kps 原样保留）：")
        for old, new, fp, at, chars, kps, tag in rep["rows"]:
            print(f"  [{tag}] {old}")
            print(f"        -> {new}")
            print(f"           fp={fp}   at={at}   chars={chars}   kps={kps}")
        missing_fp = [k for k, r in probe.done.items() if r.get("fp") is None]
        if missing_fp:
            print(f"  [WARN] 仍有 {len(missing_fp)} 个键没补到 fp（对应块已不存在？）："
                  f"{missing_fp[:3]}")
        print("\n[--check] 未写盘。")
        return 0

    # 备份（90_日志 永不参与同步，适合放备份）
    backup_dir = ROOT / "90_日志" / "_state_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = backup_dir / f"{state.path.stem}.{ts}.json"
    shutil.copy2(state.path, backup)
    print(f"已备份：{backup}")

    rep = kb.migrate_state_keys(state, chunk_by_key=chunk_by_key, page_index=page_index)
    state.save()

    print("\n新旧键 1:1 映射表（旧键 -> 新键 | fp | at / chars / kps 原样保留）：")
    for old, new, fp, at, chars, kps, tag in rep["rows"]:
        print(f"  [{tag}] {old}")
        print(f"        -> {new}")
        print(f"           fp={fp}   at={at}   chars={chars}   kps={kps}")

    print("\n迁移结果：")
    print(f"  旧格式键迁移 {rep['migrated']} 个（{rep['total']} -> {len(state.done)}）")
    print(f"  补算指纹 {rep['fp_filled']} 个；合并重复键 {rep['dropped_dup']} 个")
    if rep["unmatched"]:
        print(f"  [WARN] {len(rep['unmatched'])} 个键无法解析、原样保留：{rep['unmatched'][:3]}")
    print(f"  写回：{state.path}")

    # 迁移后自检：键应为新格式且唯一
    still_legacy = [k for k in state.done if kb.is_legacy_chunk_key(k)]
    no_fp = [k for k, r in state.done.items() if r.get("fp") is None]
    dup = len(state.done) - len(set(state.done))
    print("\n自检：")
    print(f"  残留旧格式键 {len(still_legacy)}（应为 0）"
          f"{'  <-- [FAIL]' if still_legacy else '  [OK]'}")
    print(f"  缺指纹键 {len(no_fp)}（应为 0）"
          f"{'  <-- [WARN]' if no_fp else '  [OK]'}")
    print(f"  重复键 {dup}（应为 0）{'  <-- [FAIL]' if dup else '  [OK]'}")
    for k, rec in state.done.items():
        print(f"  {k}   fp={rec.get('fp')}  chars={rec.get('chars')}  kps={rec.get('kps')}")
    print("=" * 74)
    return 1 if (still_legacy or dup) else 0


if __name__ == "__main__":
    sys.exit(main())
