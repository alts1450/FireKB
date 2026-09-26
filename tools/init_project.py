# -*- coding: utf-8 -*-
"""
init_project.py —— 首次初始化（把仓库变成可运行状态）
=====================================================

刚 clone 下来的仓库**只有代码、模板与文档**（素材、派生数据、密钥都不在版本库里，
这是有意为之）。所以第一次使用要先跑一次初始化：建目录、把 `*.example.*` 复制成真名。

    python -m firekb init

## 为什么单独做成一个环节，而不是让 `doctor` 顺手做

`doctor` 是**体检**，它必须如实报告"还没初始化"，而不是悄悄替人做写操作。
把"写"和"查"分开，用户才能分清"我还没配"和"我配错了" ——
这也是这个项目反复强调的：**别让工具静默替你做决定**。

## 行为

  · 幂等：已存在的文件**绝不覆盖**（重复跑安全）；
  · 只创建目录与复制模板，不写任何内容进 `.env`（密钥必须你自己填）；
  · 跑完打印"还差什么"（通常是填 API Key 与放素材）。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(os.environ.get("FIREKB_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "tools"))

import kb  # noqa: E402

DIRS = ["00_配置", "10_原始归档", "10_原始归档/规范教材", "10_原始归档/课件",
        "10_原始归档/录音", "20_文本库", "30_知识点", "40_复习产物", "90_日志"]

# 模板 -> 真名（模板必须存在；目标已存在则跳过）
COPIES = [
    ("00_配置/courses.example.json", "00_配置/courses.json"),
    ("00_配置/references.example.json", "00_配置/references.json"),
    ("00_配置/page-offsets.example.json", "00_配置/page-offsets.json"),
    ("00_配置/experimental-batches.example.json", "00_配置/experimental-batches.json"),
    ("00_配置/.env.example", "00_配置/.env"),
]


def main() -> int:
    print("=" * 74)
    print("FireKB 首次初始化")
    print("=" * 74)
    print(f"项目根：{ROOT}\n")

    made, kept = [], []
    for d in DIRS:
        p = ROOT / d
        if p.exists():
            kept.append(d)
        else:
            p.mkdir(parents=True, exist_ok=True)
            made.append(d)
    print(f"目录：新建 {len(made)} 个，已存在 {len(kept)} 个")
    for d in made:
        print(f"    + {d}")

    print()
    copied, skipped, missing = [], [], []
    for src_rel, dst_rel in COPIES:
        src, dst = ROOT / src_rel, ROOT / dst_rel
        if dst.exists():
            skipped.append(dst_rel)
            continue
        if not src.exists():
            missing.append(src_rel)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(dst_rel)

    print(f"配置：从模板生成 {len(copied)} 个，已存在跳过 {len(skipped)} 个")
    for c in copied:
        print(f"    + {c}")
    for s in skipped:
        print(f"    = {s}（已存在，未覆盖）")
    if missing:
        print("\n[!] 以下模板缺失（仓库不完整？）：")
        for m in missing:
            print(f"    - {m}")

    # ---- 还差什么 ----
    print("\n" + "-" * 74)
    todo = []
    env = ROOT / "00_配置/.env"
    if env.exists():
        txt = env.read_text(encoding="utf-8", errors="replace")
        if "替换为你的真实Key" in txt or "sk-替换" in txt:
            todo.append("填 API Key：编辑 00_配置\\.env，把 DEEPSEEK_API_KEY 换成你的真实 Key")
    else:
        todo.append("创建 00_配置\\.env（本机没有 .env.example 模板）")

    mat = [p for p in (ROOT / "10_原始归档").rglob("*") if p.is_file()]
    if not mat:
        todo.append("放素材：把教材/课件放进 10_原始归档\\规范教材\\<课程名>\\ 与 10_原始归档\\课件\\<课程名>\\")
    courses = kb.load_courses()
    if not courses or all(str(c.get("id", "")).startswith("DEMO") for c in courses):
        todo.append("改课表：编辑 00_配置\\courses.json，把示例课程换成你自己的真实课表")

    if todo:
        print("还差这些才能跑通：")
        for t in todo:
            print(f"    · {t}")
    else:
        print("看起来齐了。先跑一次体检：python -m firekb doctor")
    print("-" * 74)
    print("\n下一步：")
    print("    python -m firekb doctor      # 体检（0=全过）")
    print("    python -m firekb --list      # 看有哪些环节")
    print("    python -m firekb parse       # 从解析素材开始")
    return 0


if __name__ == "__main__":
    sys.exit(main())
