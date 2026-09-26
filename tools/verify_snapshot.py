# -*- coding: utf-8 -*-
"""
陌生用户路径验收 —— 在一个**干净副本**上把「照文档从零跑通」走一遍
=================================================================
用法：
    python tools\\verify_snapshot.py --dir D:\\FireKB_public
    python tools\\verify_snapshot.py --dir D:\\FireKB_public --keep   # 保留临时副本便于排查

为什么需要它：发布快照与开发工作区**不是同一套东西**（真实数据、内部文档、
双机配置都不进快照）。于是"在我机器上好好的"完全不能证明"陌生人拿到能跑"——
实际发生过的形态是：快照里少一个文件、少一行依赖或用到一个只在开发机上存在的
路径，用户第一步就卡住，而作者本地一切正常。**这类问题只有"真的在没有数据的
副本上跑一遍"才会暴露**，靠读代码或靠自测都盖不住（自测只测函数，不测上手路径）。

它做八件事（每一步都断言**退出码**，不是看输出好不好看）：
  1. 在干净副本里 doctor          → 必须 7（尚未初始化，是正常状态）
  2. init                        → 必须 0
  3. doctor（初始化后）           → 必须 0 或 6（**绝不能是 5**：那意味着硬门槛失败）
  4. selftest                    → 必须 0
  5. testdata（有 reportlab 时）  → 必须 0
  6. parse                       → 必须 0
  7. 放一个坏文件再 parse         → 必须 4（跑完了但有失败项）
  8. 打印结论（哪一步失败、原始输出落在哪）

不涉及任何 LLM 环节（那需要密钥与费用），因此不覆盖 M4b/M5/M6 的产出质量。
"""

from __future__ import annotations

import argparse
import atexit
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def run(args: list[str], cwd: Path) -> tuple[int, str]:
    r = subprocess.run([sys.executable, *args], cwd=str(cwd), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser(description="在干净副本上验收「陌生用户上手路径」")
    ap.add_argument("--dir", required=True, help="待验收的快照目录")
    ap.add_argument("--keep", action="store_true", help="保留临时副本（排查用）")
    args = ap.parse_args()

    src = Path(args.dir).expanduser().resolve()
    if not (src / "firekb.py").exists():
        print(f"[FAIL] {src} 里没有 firekb.py —— 这不像是快照目录")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="firekb_verify_"))
    # 正常路径在末尾回收；**异常路径（脚本自己崩了）也回收**，否则每次失败都留一份副本
    if not args.keep:
        atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    work = tmp / "repo"
    shutil.copytree(src, work, ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv"))
    print("=" * 78)
    print(f"陌生用户路径验收：{src}")
    print(f"干净副本        ：{work}")
    print("=" * 78)

    # 第 7 步的夹具目录先准备好，并清掉**上一次验收留下的**坏文件 ——
    # 否则第 6 步会先撞上它（parse 退出码 4）而误报"第 6 步失败"。
    sample = work / "10_原始归档" / "课件" / "_样例验证"
    (sample / "坏文件.pdf").unlink(missing_ok=True)

    results: list[tuple[str, bool, str]] = []
    logs: dict[str, str] = {}

    def step(name: str, cmd: list[str], expect: tuple[int, ...]) -> int:
        code, out = run(cmd, work)
        ok = code in expect
        results.append((name, ok, f"退出码 {code}（期望 {'/'.join(map(str, expect))}）"))
        logs[name] = out
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<34} 退出码 {code}")
        return code

    step("1) doctor（未初始化）", ["-m", "firekb", "doctor"], (7,))
    step("2) init", ["-m", "firekb", "init"], (0,))
    step("3) doctor（初始化后）", ["-m", "firekb", "doctor"], (0, 6))
    step("4) selftest", ["-m", "firekb", "selftest"], (0,))

    need_reportlab = True
    try:
        import reportlab  # noqa: F401
    except Exception:
        need_reportlab = False
    if need_reportlab:
        step("5) testdata（合成样例）", ["-m", "firekb", "testdata"], (0,))
    else:
        print("  [SKIP] 5) testdata                                本机没有 reportlab")

    step("6) parse（样例数据）", ["-m", "firekb", "parse"], (0,))

    # 第 7 步：故意放一个坏文件 —— 验证"有失败项必须返回非 0"
    sample.mkdir(parents=True, exist_ok=True)
    (sample / "坏文件.pdf").write_bytes(b"")
    step("7) parse（含坏文件）", ["-m", "firekb", "parse"], (4,))

    bad = [r for r in results if not r[1]]
    print("\n" + "=" * 78)
    if bad:
        print(f"结论：**未通过** —— {len(bad)} 步不符合预期")
        for name, _, why in bad:
            print(f"  · {name}：{why}")
        print("\n以下是失败步骤的原始输出：")
        for name, ok, _ in bad:
            print("\n" + "-" * 78)
            print(f"[{name}]")
            print("-" * 78)
            print(logs.get(name, "")[-2000:])
    else:
        print(f"结论：通过 —— {len(results)} 步全部符合文档承诺")
        print("      （未覆盖需要 LLM 密钥的环节：M4b 抽取 / M5 校验 / M6 产物）")
    print("=" * 78)

    if args.keep or bad:
        print(f"\n干净副本保留在：{work}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
