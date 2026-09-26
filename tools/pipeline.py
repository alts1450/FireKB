# -*- coding: utf-8 -*-
"""
FireKB 流水线编排 —— 一条命令跑完全流程
==========================================
按依赖顺序依次执行各模块，每步都有前置条件检查与结果汇总。

流水线顺序：
  1. parse_docs     解析课件/教材（PDF/PPTX/EPUB -> 页级文本）
  2. ocr_pages      扫描页 OCR（耗时最长，可用 --skip-ocr 跳过）
  3. build_skeleton 构建知识骨架（章节结构）
  4. extract_kp     抽取知识点 + 证据引用（消耗 API）
  5. verify_kp      校验与审核队列
  6. make_review    生成复习产物

用法：
  python tools\\pipeline.py                  # 跑完整流程
  python tools\\pipeline.py --skip-ocr       # 跳过 OCR（扫描页少时）
  python tools\\pipeline.py --only ocr       # 只跑某一步
  python tools\\pipeline.py --from skeleton  # 从某一步开始往后跑
  python tools\\pipeline.py --dry-run        # 只显示将执行的步骤

注意：OCR 在后台跑着时不要重复执行本脚本的 ocr 步骤（会重复占用 CPU）。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(os.environ.get("FIREKB_ROOT") or Path(__file__).resolve().parents[1])
TOOLS = ROOT / "tools"
PY = sys.executable

STEPS: list[tuple[str, str, str, bool]] = [
    # (key, 标题, 脚本参数, 耗时步)
    ("parse", "解析课件/教材（PDF/PPTX/EPUB）", "parse_docs.py", False),
    ("ocr", "扫描页 OCR（影印版教材）", "ocr_pages.py", True),
    ("skeleton", "构建知识骨架（章节结构）", "build_skeleton.py", False),
    ("skeleton_review", "AI 复核骨架层级（修正二级内容被当成一级章）",
     "review_skeleton.py --apply", False),
    ("extract", "抽取知识点与证据引用（消耗 API）", "extract_kp.py", False),
    ("verify", "校验知识点并生成审核队列", "verify_kp.py", False),
    ("review", "生成复习产物（枝干/背诵卡/对照表）", "make_review.py", False),
]
ORDER = [s[0] for s in STEPS]


def run_step(key: str, title: str, script: str, extra: list[str]) -> tuple[bool, float]:
    # script 允许带参数（如 "review_skeleton.py --apply"），便于把「复核并写回」串成一步
    parts = script.split()
    path = TOOLS / parts[0]
    if not path.exists():
        print(f"\n[跳过] 找不到脚本：{path}")
        return False, 0.0
    print("\n" + "=" * 76)
    print(f"▶ {title}")
    print(f"  脚本：{script} {' '.join(extra)}")
    print("=" * 76)
    t0 = time.time()
    try:
        proc = subprocess.run([PY, str(path), *parts[1:], *extra], cwd=str(ROOT))
        ok = proc.returncode == 0
    except KeyboardInterrupt:
        print("\n[中断] 用户取消")
        raise
    except Exception as exc:
        print(f"[FAIL] 启动失败：{type(exc).__name__}: {exc}")
        return False, time.time() - t0
    dt = time.time() - t0
    status = "完成" if ok else f"失败（退出码 {proc.returncode}）"
    print(f"\n◀ {title}  {status}　用时 {dt:.1f} 秒")
    return ok, dt


def main() -> int:
    ap = argparse.ArgumentParser(description="FireKB 流水线编排")
    ap.add_argument("--skip-ocr", action="store_true", help="跳过 OCR 步骤")
    ap.add_argument("--only", choices=ORDER, help="只运行某一步")
    ap.add_argument("--from", dest="start", choices=ORDER, help="从某一步开始（含该步）")
    ap.add_argument("--dry-run", action="store_true", help="只显示计划，不执行")
    ap.add_argument("--extract-limit", type=int, help="传给 extract_kp 的 --limit（试跑用）")
    args = ap.parse_args()

    if not TOOLS.exists():
        print(f"[FAIL] 工具目录不存在：{TOOLS}")
        return 2

    todo = [s for s in STEPS]
    if args.only:
        todo = [s for s in todo if s[0] == args.only]
    else:
        if args.start:
            i = ORDER.index(args.start)
            todo = [s for s in todo if ORDER.index(s[0]) >= i]
        if args.skip_ocr:
            todo = [s for s in todo if s[0] != "ocr"]

    print("=" * 76)
    print(" FireKB 流水线")
    print(f" 数据根目录：{ROOT}")
    print(f" 计划执行 {len(todo)} 步：" + " -> ".join(s[0] for s in todo))
    print("=" * 76)
    if args.dry_run:
        for key, title, script, heavy in todo:
            print(f"  [{key:<8}] {title}{'   (耗时步)' if heavy else ''}")
        return 0

    results: list[tuple[str, bool, float]] = []
    t_all = time.time()
    for key, title, script, _heavy in todo:
        extra: list[str] = []
        if key == "extract" and args.extract_limit:
            extra = ["--limit", str(args.extract_limit)]
        ok, dt = run_step(key, title, script, extra)
        results.append((key, ok, dt))
        if not ok and key in ("parse", "extract"):
            # 关键步骤失败则中止，避免后续步骤基于错误数据继续
            print(f"\n[中止] 关键步骤 {key} 失败，后续步骤不再执行。")
            break

    print("\n" + "=" * 76)
    print(" 流水线结果汇总")
    print("=" * 76)
    for key, ok, dt in results:
        print(f"  {'✔' if ok else '✘'} {key:<10}{dt:>8.1f} 秒")
    print(f"\n总用时 {(time.time() - t_all) / 60:.1f} 分钟")
    print(f"\n查看整体状态：python tools\\status.py")
    if any(k == "review" and ok for k, ok, _ in results):
        print(f"复习产物目录：{ROOT / '40_复习产物'}")
    print("=" * 76)
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
