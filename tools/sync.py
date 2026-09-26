# -*- coding: utf-8 -*-
"""
多机同步工具 —— 通用文件与数据文件的一致性维护
================================================
同一份项目在多台机器上维护时，代码、配置与文档必须各机一致。本工具把
「哪些文件属于通用文件」写死成清单，用一条命令对比与推送，不必靠记忆。

文件分类：
  general 通用文件（必须各机一致）：tools/*.py、README.md、00_配置/*.md、
          00_配置/courses.json、00_配置/.env.example
  data    数据文件（按需推送）：文本库、知识点库、复习产物
  永不推送：00_配置/.env（含真实密钥，各机各自持有）、90_日志/*、state_*.json、
          目标端独有的文件（例如只在一台机器上产生的 transcript.jsonl）

用法：
  python tools\\sync.py --check            # 只对比，显示差异
  python tools\\sync.py --push             # 推送通用文件（推荐日常使用）
  python tools\\sync.py --push --data      # 通用文件 + 数据文件
  python tools\\sync.py --push --only README.md

目标端位置由环境变量 FIREKB_TARGET 指定。

安全约定：
  * 只推不删：不会删除目标端任何文件。
  * 方向单一：本地 -> 目标端（如需反向，用 --pull）。
  * 推送前会打印将覆盖的文件列表，不额外确认（便于脚本化）。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(os.environ.get("FIREKB_ROOT") or Path(__file__).resolve().parents[1])
TARGET = Path(os.environ.get("FIREKB_TARGET") or r"Z:\\")

# ---- 通用文件清单（新增脚本/文档时请同步维护这里）----
# 清单必须覆盖子目录与项目根，否则新增的文件不会进入对比：
#   ① `tools/**/*.py` 与 `tools/**/*.ps1`、`tools/**/*.bat` —— 只写 `tools/*.py`
#      会漏掉 `tools/engines/` 这类子目录里的全部文件；
#   ② `00_配置/*.json` —— engines.json / courses.json 等配置属于各机必须一致的部分；
#   ③ 项目根下的文件（firekb.py、.gitignore、.gitattributes、LICENSE、
#      THIRD-PARTY-NOTICES.md）—— 它们不在 tools/ 下，容易被清单漏掉；
#      `.gitattributes` 里的逐字节纪律（`* -text`）要求各机检出结果字节一致。
GENERAL_GLOBS = [
    "firekb.py",          # 统一入口（在项目根，不在 tools/ 下）
    ".gitignore",         # 忽略规则（各机都需要）
    ".gitattributes",     # 逐字节纪律（* -text）：各机检出必须字节一致
    "LICENSE",            # 许可证
    "THIRD-PARTY-NOTICES.md",  # 第三方依赖与许可清单（各机都必须有，同 LICENSE 一同维护）
    "CHANGELOG.md",       # 更新日志与兼容性边界（版本声明与 firekb.py 的 __version__ 对应）
    "tools/*.py",
    "tools/**/*.py",
    "tools/**/*.ps1",     # 装机/共享/SSH 配置脚本
    "tools/**/*.bat",
    "README.md",
    "docs/*.md",          # 公开文档：docs/README_public.md 是发布快照 README 的来源，
                          #   漏掉它两机的公开文档会**静默分叉**（实测主力机那份落后一整版，
                          #   因为 --check 根本看不到不在清单里的文件）
    "00_配置/*.md",
    "00_配置/*.json",
    "00_配置/.env.example",
]

# ---- 数据文件清单（内容产物）----
DATA_GLOBS = [
    "20_文本库/pages.jsonl",
    "20_文本库/page_map.json",
    "20_文本库/parse_report.json",
    "20_文本库/ocr_results.jsonl",
    "30_知识点/skeleton.json",
    "30_知识点/kp.jsonl",
    "30_知识点/evidence.jsonl",
    "30_知识点/audit.jsonl",
    "40_复习产物/*",
]

# ---- 永不推送（任何情况下都跳过）----
NEVER = [
    "00_配置/.env",
    "20_文本库/transcript.jsonl",   # 目标端独有（转写结果），反向也不动
    "90_日志/*",
    # 断点续跑状态：**各机各跑各的环节**，状态文件只对产生它的那台机器有意义。
    # 它今天没被推送，其实只是"既不在 GENERAL_GLOBS 也不在 DATA_GLOBS"的**副产品**——
    # 也就是**靠遗漏的安全**：哪天有人往 DATA_GLOBS 里加一条 `30_知识点/*.json`，
    # 状态就会跟着推过去，把一台机器的断点盖到另一台上（症状是"跑过的环节又从头跑"
    # 或反过来被直接跳过，两种都很难查）。显式写进来，安全才是**靠规则**。
    "30_知识点/state_*.json",
]


def md5(path: Path) -> str:
    if not path.exists():
        return ""
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(globs: list[str], only: str | None = None) -> list[Path]:
    out: list[Path] = []
    seen: set[Path] = set()
    for g in globs:
        for p in sorted(ROOT.glob(g)):
            if not p.is_file():
                continue
            if only and only.lower() not in p.name.lower():
                continue
            rel = p.relative_to(ROOT).as_posix()
            if any(Path(rel).match(n) for n in NEVER):
                continue
            if p in seen:
                continue
            seen.add(p)
            out.append(p)
    return out


def peer_only(files: list[Path]) -> list[str]:
    """
    目标端独有文件（本地没有、目标端有）—— 补 `compare()` 的已知盲点。

    为什么必须单独做：`collect()` 只 glob **本地存在**的文件，所以目标端独有的文件
    既不会被检查、也不会被 push/pull 带回来（例如某个脚本只存在于目标端，
    从未进过清单，直到手工发现）。
    做法：用**同一套 glob 在目标端再扫一遍**，减去本地存在的路径；NEVER 清单照旧排除。
    """
    local = {p.relative_to(ROOT).as_posix() for p in files}
    seen: set[str] = set()
    out: list[str] = []
    if not TARGET.exists():
        return out
    for g in GENERAL_GLOBS + DATA_GLOBS:
        try:
            for p in sorted(TARGET.glob(g)):
                if not p.is_file():
                    continue
                rel = p.relative_to(TARGET).as_posix()
                if any(Path(rel).match(n) for n in NEVER):
                    continue
                if rel in local or rel in seen:
                    continue
                seen.add(rel)
                out.append(rel)
        except Exception:
            continue
    return out


def compare(files: list[Path]) -> list[tuple[Path, str]]:
    """返回 [(文件, 状态)]，状态取 same / diff / missing。"""
    rows = []
    for p in files:
        rel = p.relative_to(ROOT)
        tgt = TARGET / rel
        if not tgt.exists():
            rows.append((p, "missing"))
        elif md5(p) == md5(tgt):
            rows.append((p, "same"))
        else:
            rows.append((p, "diff"))
    return rows


def show(rows: list[tuple[Path, str]], title: str) -> None:
    same = sum(1 for _, s in rows if s == "same")
    diff = sum(1 for _, s in rows if s == "diff")
    miss = sum(1 for _, s in rows if s == "missing")
    print(f"\n{title}")
    print("-" * 74)
    print(f"  一致 {same}   不一致 {diff}   目标端缺失 {miss}")
    for p, s in rows:
        if s == "same":
            continue
        rel = p.relative_to(ROOT).as_posix()
        mark = {"diff": "需更新", "missing": "待新增"}[s]
        print(f"    [{mark}] {rel}")


def push(rows: list[tuple[Path, str]], label: str) -> int:
    n = 0
    for p, s in rows:
        if s == "same":
            continue
        rel = p.relative_to(ROOT)
        tgt = TARGET / rel
        tgt.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(p, tgt)
            n += 1
        except Exception as exc:
            print(f"    [失败] {rel} -> {exc}")
    print(f"  {label}：已推送 {n} 个文件")
    return n


def pull(rows: list[tuple[Path, str]], label: str) -> int:
    n = 0
    for p, s in rows:
        if s == "same" or s == "missing":
            continue
        rel = p.relative_to(ROOT)
        src = TARGET / rel
        try:
            shutil.copy2(src, p)
            n += 1
        except Exception as exc:
            print(f"    [失败] {rel} <- {exc}")
    print(f"  {label}：已拉取 {n} 个文件")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="FireKB 双机同步")
    ap.add_argument("--check", action="store_true", help="只对比，不推送")
    ap.add_argument("--push", action="store_true", help="本地 -> 目标端")
    ap.add_argument("--pull", action="store_true", help="目标端 -> 本地（仅通用文件）")
    ap.add_argument("--data", action="store_true", help="同时处理数据文件")
    ap.add_argument("--only", help="只处理文件名包含该字符串的文件")
    args = ap.parse_args()

    print("=" * 74)
    print(f" FireKB 同步    本机 {ROOT}  ->  目标 {TARGET}")
    print("=" * 74)

    if not TARGET.exists():
        print(f"[FAIL] 目标不可访问：{TARGET}")
        print("       请确认目标端已就绪、路径可访问（可用环境变量 FIREKB_TARGET 指定）")
        return 2

    gen = collect(GENERAL_GLOBS, args.only)
    gen_rows = compare(gen)
    show(gen_rows, f"通用文件（{len(gen)} 个）")

    data_rows: list[tuple[Path, str]] = []
    if args.data:
        data = collect(DATA_GLOBS, args.only)
        data_rows = compare(data)
        show(data_rows, f"数据文件（{len(data)} 个）")

    # 目标端独有文件检查：补 compare() 的盲点。
    # ⚠️ 排除集必须包含**本地全部同类文件**（不管是否加 --data）——否则没加 --data 时，
    #    本地明明有的数据文件会被误报成"目标端独有"。
    peers = peer_only(gen + collect(DATA_GLOBS, args.only))

    if args.check or not (args.push or args.pull):
        todo = sum(1 for _, s in gen_rows + data_rows if s != "same")
        print(f"\n共 {todo} 个文件需要同步。执行：python tools\\sync.py --push" +
              (" --data" if args.data else "  （若要一并同步数据，加 --data）"))
        if peers:
            print(f"\n⚠️ 对端独有文件 {len(peers)} 个（**本机没有**，sync 不会处理，请人工决定）：")
            for rel in peers[:20]:
                print(f"    [对端独有] {rel}")
            if len(peers) > 20:
                print(f"    ... 其余 {len(peers) - 20} 个")
            print("    处置：需要的话手工 Copy-Item 回来；不需要则在对端删除。")
        else:
            print("\n对端独有文件：无（已用「并集扫描」核对，不止看本机存在的文件）")
        return 0

    print()
    if args.push:
        push(gen_rows, "通用文件")
        if data_rows:
            push(data_rows, "数据文件")
        print("\n提示：推送后建议在目标端跑一次 tools\\status.py 确认状态一致。")
        if peers:
            print(f"⚠️ 另有 {len(peers)} 个**对端独有**文件未被处理（--push 只推不删、也不会拉回）：")
            for rel in peers[:10]:
                print(f"    [对端独有] {rel}")
            print("    处置：手工 Copy-Item 回来，或在对端删除。")
    elif args.pull:
        pull(gen_rows, "通用文件（反向）")
        print("\n注意：数据文件不参与反向拉取，避免覆盖本机产物。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
