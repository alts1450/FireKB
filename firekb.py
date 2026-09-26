# -*- coding: utf-8 -*-
"""
firekb —— FireKB 统一入口
===========================

用法（在项目根目录下）：

    python -m firekb --list                # 列出全部环节
    python -m firekb env                   # 环境自检
    python -m firekb doctor                # 一键跑全部自检（推荐先用它）
    python -m firekb extract --limit 3     # 其余参数原样透传给对应脚本
    python -m firekb runs --tail 20        # 查看结构化运行日志

为什么要它：
    每个环节当然可以单独调用（`python tools\\extract_kp.py` / `python tools\\ocr_pages.py --check`
    …），但分散调用**回答不了「这个项目有哪些环节、现在该跑哪个」**；运行痕迹也只 `print` 到终端，
    终端一关就没有证据。本入口做三件事，且**只做这三件**：
      1. 环节名 → 脚本 的映射与参数透传（不复制任何业务逻辑，脚本仍是唯一实现）；
      2. 每次运行自动写 `90_日志/runs.jsonl`（起止时间、耗时、退出码、成本、模型/引擎指纹）；
      3. `doctor` 一键自检 + `runs` 查日志，把"当前状态"变成一条命令。

退出码约定（`doctor` 特有）：
    0 = 硬门槛（env / schema / consistency / selftest）与软环节（engines）全过；
    5 = 硬门槛失败（环境/契约/一致性/自测有问题，必须处理）；
    6 = 硬门槛全过，但软环节未就绪（典型：当前机器不承担转写环节、不装 faster-whisper 与 ffmpeg
        —— 这时 6 是"正常但要知道"，不是故障）。
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

# ---------------------------------------------------------------------------
# 版本与发布状态（**单一来源**：别处要版本就从这里引用，不要再写一份）
# ---------------------------------------------------------------------------
# 为什么同时有"机器版"和"显示版"：
#   · `__version__` 给**工具**吃（打包、依赖声明、CI、比大小）——
#     这类场合要 ASCII、且能被 PEP 440 / semver 解析，所以写 `0.1.0-alpha`；
#   · `__version_display__` 给人看，用 `0.1α`。
#   两者语义相同、受众不同；只留一个总有一侧别扭。
__version__ = "0.1.0-alpha"
__version_display__ = "0.1α"

# 发布状态：本版本**明确为测试版**，且**不承诺向后兼容**。
# 这不是客套话，是当前的工程事实 —— 配置格式、产物契约、CLI 参数都还可能变。
__release_status__ = "alpha"
__compat_policy__ = "no-backward-compatibility-guarantee"

import kb  # noqa: E402  共享层（路径常量 / 运行日志）

# stage -> (模块名, 一句话说明)。模块名 None = 由本入口自己实现的元环节。
STAGES: dict[str, tuple[str | None, str]] = {
    "init":        ("init_project",    "首次初始化：建目录 + 从模板生成配置（幂等，不覆盖）"),
    "parse":       ("parse_docs",      "M1 解析：PDF/PPTX/EPUB → 页级文本"),
    "ocr":         ("ocr_pages",       "M2 扫描页 OCR（无文字层的影印页）"),
    "transcribe":  ("transcribe",      "M3 课堂录音转写（权重 3，仅供参考）"),
    "skeleton":    ("build_skeleton",  "M4a 知识骨架（章/节结构）"),
    "review-skel": ("review_skeleton", "M4a′ 骨架 AI 复核（--apply 才回写）"),
    "extract":     ("extract_kp",      "M4b 知识点抽取（花钱最多的一步）"),
    "verify":      ("verify_kp",       "M5 校验 + 人工复核队列"),
    "queue":       ("review_queue",    "M5′ 人工复核工作表（导出同屏对照 / --apply 回写裁决）"),
    "offsets":     ("page_offsets",    "页码双轨：探测/登记 印刷页 ↔ PDF 物理页 偏移"),
    "dedupe":      ("dedupe_sources",  "按 references.json 声明清理重复来源（--apply --yes 才写盘）"),
    "review":      ("make_review",     "M6 复习产物（卡片/背诵单/思维导图）"),
    "pipeline":    ("pipeline",        "整条流水线编排（按顺序调上面各步）"),
    "status":      ("status",          "状态汇总"),
    "consistency": ("consistency",     "配置一致性：课表 × 骨架 × 素材 × 产物"),
    "schema":      ("schema_check",    "产物契约校验（13 项）"),
    "fingerprint": ("fingerprint",     "产物/脚本指纹；--json 导出、--diff 跨机对账"),
    "engines":     ("engines_check",   "引擎层自检（解析/OCR/转写）"),
    "env":         ("check_env",       "环境自检（依赖/密钥/路径）"),
    "sync":        ("sync",            "双机同步（默认只推通用文件，危险操作需显式 --data）"),
    "search":      ("search_pages",    "页文本检索"),
    "peek":        ("peek",            "产物抽查"),
    "testdata":    ("make_testdata",   "合成测试数据"),
    "runs":        (None,              "查看结构化运行日志 90_日志/runs.jsonl"),
    "selftest":    (None,              "全部单元自测（kb 原语 + 引擎层）"),
    "doctor":      (None,              "一键跑全部自检（env + engines + schema + consistency + selftest）"),
}

# doctor 依次执行的元环节（顺序即依赖顺序：环境 → 引擎 → 契约 → 一致性 → 单元自测）
DOCTOR: list[str] = ["env", "engines", "schema", "consistency", "selftest"]
# 硬门槛：失败就是真问题；软环节（engines）失败**可能只是当前机器不承担该环节**
# （例：机器上不装 faster-whisper/ffmpeg，转写环节交给另一台机器跑）。
DOCTOR_HARD: set[str] = {"env", "schema", "consistency", "selftest"}
DOCTOR_SOFT: set[str] = {"engines"}

# selftest 环节执行的测试文件（顺序无关，全部跑完再汇总）
SELFTEST_FILES: list[str] = [
    "selftest/test_kb.py",
    "selftest/test_engines.py",
]


def _print_stages() -> None:
    print(f"FireKB {__version_display__}（测试版 · 不承诺向后兼容）")
    print("统一入口 —— 可用环节：\n")
    print(f"  {'环节':<14}{'说明'}")
    print("  " + "-" * 68)
    for name, (mod, desc) in STAGES.items():
        print(f"  {name:<14}{desc}")
    print("\n用法： python -m firekb <环节> [该环节自己的参数...]")
    print("      python -m firekb doctor          # 一键自检，先跑这个")
    print("      python -m firekb extract --check # 参数原样透传")
    print(f"\n项目根：{ROOT}")
    print(f"运行日志：{kb.RUN_LOG_FILE}")


def _tail_runs(limit: int, stage: str | None) -> int:
    p = kb.RUN_LOG_FILE
    if not p.exists():
        print(f"[!] 还没有运行日志：{p}\n    （用 `python -m firekb doctor` 跑一次就会产生）")
        return 0
    rows = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if stage and rec.get("stage") != stage:
            continue
        rows.append(rec)
    if not rows:
        print(f"[!] 日志里没有 stage={stage} 的记录")
        return 0
    print(f"最近 {min(limit, len(rows))} 条运行记录（共 {len(rows)} 条）：\n")
    print(f"  {'时间':<21}{'环节':<13}{'状态':<9}{'耗时':>8}  说明")
    print("  " + "-" * 84)
    for rec in rows[-limit:]:
        dur = rec.get("duration_sec")
        cost = (rec.get("cost") or {})
        extra = ""
        if cost.get("calls"):
            extra = f"  API {cost['calls']} 次 / {cost.get('failed', 0)} 失败"
        elif rec.get("error"):
            extra = f"  {str(rec['error'])[:60]}"
        elif rec.get("argv"):
            extra = f"  argv={rec['argv']}"
        print(f"  {rec.get('at', ''):<21}{rec.get('stage', ''):<13}"
              f"{rec.get('status', ''):<9}{(f'{dur:.1f}s' if dur is not None else '-'):>8}{extra}")
    return 0


def _selftest() -> int:
    """跑 tools/selftest 下的全部测试文件，汇总退出码（不因某一个失败就跳过其余）。"""
    import runpy
    results: list[tuple[str, int]] = []
    for rel in SELFTEST_FILES:
        p = TOOLS / rel
        print("\n" + "=" * 78)
        print(f"selftest → {rel}")
        print("=" * 78)
        if not p.exists():
            print(f"[FAIL] 测试文件不存在：{p}")
            results.append((rel, 2))
            continue
        try:
            runpy.run_path(str(p), run_name="__main__")
            results.append((rel, 0))
        except SystemExit as exc:
            results.append((rel, exc.code if isinstance(exc.code, int) else 1))
        except Exception as exc:
            print(f"[FAIL] {rel} 抛异常：{type(exc).__name__}: {exc}")
            results.append((rel, 1))
    print("\n" + "=" * 78)
    print("selftest 汇总")
    print("=" * 78)
    for rel, rc in results:
        print(f"  [{'OK  ' if rc == 0 else 'FAIL'}] {rel:<28} 退出码 {rc}")
    bad = [r for r, rc in results if rc != 0]
    return 0 if not bad else 4


def _run_stage(stage: str, rest: list[str]) -> int:
    modname, _desc = STAGES[stage]
    module = importlib.import_module(modname)
    if not hasattr(module, "main"):
        print(f"[FAIL] {modname}.py 没有 main()，无法作为环节调用")
        return 2
    sys.argv = [f"firekb {stage}"] + rest     # 脚本自己解析 sys.argv
    code = 0
    with kb.run_record(stage, argv=rest) as rec:
        rc = module.main()
        code = rc if isinstance(rc, int) else 0
        rec["status"] = "ok" if code == 0 else f"exit-{code}"
        rec["exit_code"] = code
    # ⚠️ 环节脚本可能**在 main() 内部** sys.exit(N)（argparse 报错、脚本自己的硬退出）：
    #    这时 kb.run_record 会把它拦成一条记录而不向上抛，上面三行**不会执行**。
    #    因此退出码必须以 rec 为准 —— 否则 `return code` 会 UnboundLocalError，
    #    而报出来的入口异常会盖掉脚本原本要说的那句原因
    #    （`firekb skeleton --check`、`firekb engines --check` 这类带参数错误的调用正走这条路径）。
    return int(rec.get("exit_code") or code or 0)


def _dispatch(stage: str, rest: list[str]) -> int:
    """
    环节分发。**元环节（模块名为 None）必须走这里**：直接 `importlib.import_module(None)`
    会抛 `AttributeError: 'NoneType' object has no attribute 'startswith'`（doctor 正是走这条路径的）。
    """
    modname, _desc = STAGES[stage]
    if modname is None:
        if stage == "selftest":
            return _selftest()
        if stage == "runs":
            return _tail_runs(20, None)
        if stage == "doctor":
            return _doctor()
        print(f"[FAIL] 元环节 {stage} 没有实现")
        return 2
    return _run_stage(stage, rest)


def _doctor() -> int:
    print(f"\nFireKB {__version_display__}（测试版 · 不承诺向后兼容）")
    results: list[tuple[str, int]] = []
    for stage in DOCTOR:
        print("\n" + "#" * 78)
        print(f"# doctor → {stage}：{STAGES[stage][1]}")
        print("#" * 78)
        try:
            rc = _dispatch(stage, [])
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
        except Exception as exc:
            print(f"[FAIL] {stage} 抛异常：{type(exc).__name__}: {exc}")
            rc = 1
        results.append((stage, rc))
    print("\n" + "=" * 78)
    print("doctor 汇总")
    print("=" * 78)
    for stage, rc in results:
        gate = "硬" if stage in DOCTOR_HARD else "软"
        mark = "OK  " if rc == 0 else ("未初始化" if rc == 7 else "FAIL")
        print(f"  [{mark}] {gate}门槛 {stage:<12} 退出码 {rc}")

    # 「尚未初始化」（env 返回 7）是**独立状态**，不是故障 —— 刚 clone 下来必然如此。
    # 单独一档退出码，免得陌生人把"还没配"读成"项目坏了"。
    if any(rc == 7 for _, rc in results):
        print("\n结论：**项目尚未初始化**（新克隆仓库的正常状态）")
        print("      先运行： python -m firekb init")
        print("      它会建目录并把 *.example.* 模板复制成真名（幂等，不覆盖已有）。")
        return 7

    hard_bad = [s for s, rc in results if rc != 0 and s in DOCTOR_HARD]
    soft_bad = [s for s, rc in results if rc != 0 and s in DOCTOR_SOFT]
    if hard_bad:
        print(f"\n结论：硬门槛失败：{', '.join(hard_bad)}")
        return 5
    if soft_bad:
        print(f"\n结论：硬门槛全过；软环节 {', '.join(soft_bad)} 未就绪"
              f"（若本机不承担该环节属正常，如本机不跑转写；否则按上面提示装依赖）")
        return 6
    print("\n结论：全部通过")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="python -m firekb", add_help=True,
                                 description="FireKB 统一入口（统一调度 + 结构化运行日志）")
    ap.add_argument("stage", nargs="?", help="环节名（见 --list）")
    ap.add_argument("--list", action="store_true", help="列出全部环节")
    ap.add_argument("--version", action="store_true",
                    help="显示版本与发布状态（机器版 / 显示版 / 兼容性承诺）")
    ap.add_argument("--tail", type=int, default=20, help="runs 环节：显示最近 N 条")
    ap.add_argument("--stage-filter", help="runs 环节：只看某个环节")
    args, rest = ap.parse_known_args(argv)

    if args.version:
        # 同时给出机器版与显示版：脚本读第一行、人看第二行都方便
        print(__version__)
        print(f"FireKB {__version_display__}（{__release_status__}）")
        print("发布状态：测试版 —— 仍在演进，**后续版本可能包含破坏兼容性的改动**")
        print("兼容性承诺：无（配置格式 / 产物契约 / CLI 参数 / 目录结构都可能变）")
        print(f"项目根：{ROOT}")
        return 0

    if args.list or not args.stage:
        _print_stages()
        return 0 if args.list else 1

    stage = args.stage
    if stage not in STAGES:
        close = [s for s in STAGES if s.startswith(stage[:3])]
        print(f"[FAIL] 未知环节：{stage}"
              + (f"（是不是想用：{', '.join(close)}？）" if close else ""))
        print("       用 `python -m firekb --list` 查看全部环节")
        return 2

    if stage == "runs":
        return _tail_runs(args.tail, args.stage_filter)

    return _dispatch(stage, rest)


if __name__ == "__main__":
    sys.exit(main())
