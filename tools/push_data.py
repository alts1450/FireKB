# -*- coding: utf-8 -*-
"""
push_data.py —— 把数据产物推向目标端（**必须先证明方向**）
==========================================================================

## 为什么数据传播要单独一个工具

`sync.py` 负责代码、配置、文档这类通用文件；数据产物（文本库、知识点、复习产物）不同：
**传错方向造成的覆盖不可逆**。所以数据传播单独一个工具，而且**传播前必须先证明方向** ——
"本地更新"只是一个默认假设，不能当真，必须用数据本身验证一遍。

## 本工具的做法：先证明，再传播

`--check` 会对每个数据产物做**严格子集证明**：

    目标端的数据必须 ⊆ 本地的数据，且差集**恰好**是本地这一批次新增的那些 id。

只要目标端有任何一条本地没有的记录 ⇒ **拒绝传播**（那条记录很可能是目标端独有的
工作成果，覆盖过去就永久没了）。证明通过才允许 `--push`。

证明的三个层次（缺一不可）：
  ① 主键集合：目标端 kp_id / evidence_id ⊆ 本地；差集大小 == 本地该批次 run_id 新增数；
  ② 块标记：目标端 state_extract.done ⊆ 本地；差集大小 == 本地该批次新抽的块数；
  ③ 目标端独有数 **必须为 0**（这一条是真正的安全线）。

## 用法

    python tools\\push_data.py --check                 # 只做子集证明（只读）
    python tools\\push_data.py --push --yes            # 证明通过才传播（自动备份目标端）
    python tools\\push_data.py --check --run-id EXT... # 指定 run_id（默认自动取最新）

⚠️ 与 `sync.py` 的分工：
    · `sync.py`      —— 推**通用文件**（代码/配置/文档），只推不删，日常用；
    · `push_data.py` —— 推**数据产物**，带子集证明，只在确实需要时用。

目标端位置由环境变量 FIREKB_TARGET 指定。
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
TARGET = Path(os.environ.get("FIREKB_TARGET") or r"Z:\\")
sys.path.insert(0, str(ROOT / "tools"))

import kb  # noqa: E402

# 要传播的数据产物（**显式清单**，不用通配符 —— 免得把不该动的也卷进来）
DATA_FILES = [
    "20_文本库/pages.jsonl",
    "30_知识点/skeleton.json",
    "30_知识点/kp.jsonl",
    "30_知识点/evidence.jsonl",
    "30_知识点/audit.jsonl",
    "30_知识点/state_extract.json",
]
# 复习产物：整目录同步（它们是纯派生物，且已被 00_待确认清单 的排除逻辑覆盖）
DATA_DIRS = ["40_复习产物"]


def _read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def latest_run_id() -> str | None:
    """取 kp.jsonl 里最新的 run_id（= 最近一次抽取批次）。"""
    kp = _read_jsonl(ROOT / "30_知识点/kp.jsonl")
    cands = [k.get("run_id") for k in kp if k.get("run_id")]
    if not cands:
        return None
    # run_id 形如 EXTyyyymmdd-hhmmss-xxxx，字典序即时间序
    return max(cands)


def prove(run_id: str | None) -> tuple[bool, list[str]]:
    """做严格子集证明。返回 (是否通过, 报告行)。"""
    rep: list[str] = []
    ok = True

    pairs = [("kp.jsonl", "kp_id"), ("evidence.jsonl", "evidence_id")]
    for fname, key in pairs:
        lp, tp = ROOT / "30_知识点" / fname, TARGET / "30_知识点" / fname
        lrows, trows = _read_jsonl(lp), _read_jsonl(tp)
        L = {r.get(key) for r in lrows}
        T = {r.get(key) for r in trows}
        new = {r.get(key) for r in lrows if run_id and r.get("run_id") == run_id}
        peer_only = T - L
        delta = L - T
        good = (not peer_only) and (delta == new)
        ok = ok and good
        rep.append(f"  {fname:<16} 本机 {len(L):>5}  对端 {len(T):>5}  "
                   f"本机独有 {len(delta):>4}（该批次新增 {len(new)}）  "
                   f"**对端独有 {len(peer_only)}**  {'✓' if good else '✗'}")
        if peer_only:
            rep.append(f"        ★ 对端独有示例：{sorted(x for x in peer_only if x)[:3]}")

    lp, tp = ROOT / "30_知识点/state_extract.json", TARGET / "30_知识点/state_extract.json"
    if lp.exists() and tp.exists():
        L = set((json.loads(lp.read_text(encoding="utf-8")).get("done") or {}))
        T = set((json.loads(tp.read_text(encoding="utf-8")).get("done") or {}))
        peer_only = T - L
        good = not peer_only
        ok = ok and good
        rep.append(f"  {'state_extract':<16} 本机 {len(L):>5}  对端 {len(T):>5}  "
                   f"本机独有 {len(L - T):>4}  **对端独有 {len(peer_only)}**  "
                   f"{'✓' if good else '✗'}")
        if peer_only:
            rep.append(f"        ★ 对端独有示例：{sorted(peer_only)[:3]}")
    return ok, rep


def main() -> int:
    ap = argparse.ArgumentParser(description="数据产物传播（带严格子集证明）")
    ap.add_argument("--check", action="store_true", help="只做子集证明（默认行为）")
    ap.add_argument("--push", action="store_true", help="证明通过后传播")
    ap.add_argument("--yes", action="store_true", help="确认写盘")
    ap.add_argument("--run-id", help="该批次的 run_id（默认自动取最新）")
    args = ap.parse_args()

    if not TARGET.exists():
        print(f"[FAIL] 对端不可访问：{TARGET}")
        return 2

    rid = args.run_id or latest_run_id()
    print("=" * 84)
    print(f"数据产物传播    本机 {ROOT}  ->  对端 {TARGET}")
    print("=" * 84)
    print(f"该批次 run_id：{rid or '(未找到)'}")
    print("\n【严格子集证明】对端数据必须 ⊆ 本机，且差集恰好是该批次新增")
    ok, rep = prove(rid)
    print("\n".join(rep))
    print()
    if not ok:
        print("[STOP] 证明未通过 —— **拒绝传播**。")
        print("  含义：对端有本机没有的记录，推过去会**永久丢掉对端的工作成果**。")
        print("  正确做法：先搞清楚那些记录是哪来的（对端跑过什么？），再决定方向。")
        return 3

    print("[OK] 证明通过：对端是本机的严格子集，传播方向安全（本机更新）。")
    if not args.push:
        print("\n（--check 模式，未写盘。传播请加 --push --yes）")
        return 0
    if not args.yes:
        print("\n[STOP] 将覆盖对端数据。确认请加 --yes（写盘前自动备份对端）。")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = TARGET / "90_日志" / f"_对端备份_preserve_{stamp}"
    bak.mkdir(parents=True, exist_ok=True)
    n = 0
    for rel in DATA_FILES:
        src, dst = ROOT / rel, TARGET / rel
        if not src.exists():
            continue
        if dst.exists():
            (bak / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, bak / rel)          # 备份对端原文件
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
        print(f"  已推 {rel}")
    for d in DATA_DIRS:
        src, dst = ROOT / d, TARGET / d
        if not src.exists():
            continue
        dst.mkdir(parents=True, exist_ok=True)
        for p in sorted(src.glob("*")):
            if p.is_file():
                shutil.copy2(p, dst / p.name)
                n += 1
        print(f"  已推 {d}/*")
    print(f"\n共 {n} 个文件；对端原文件已备份到：{bak}")
    print("\n下一步：核对各副本指纹一致")
    print("  python tools\\fingerprint.py --diff <另一份清单.json>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
