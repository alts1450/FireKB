# -*- coding: utf-8 -*-
"""
引擎层自检入口
==============
    python tools\\engines_check.py                 # 配置 / 能力 / 出境情况 + 逐台 preflight
    python tools\\engines_check.py --grant "备注"  # 记录「有权上传」的同意（出境前必做）
    python tools\\engines_check.py --json          # 机器可读输出（便于自动化）

这是「不同部署形态（单机全流程 / 多机分工 / 便携版）各需要什么」的唯一权威答案：
换了机器、换了形态，先跑这一条，缺什么它会直接列出来。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kb  # noqa: E402
from engines import all_engines, config_path, load_config, preflight_report  # noqa: E402
from engines import egress as eg  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="FireKB 引擎层自检")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--grant", metavar="备注", help="记录数据出境同意（需你确认有权上传）")
    args = ap.parse_args()

    cfg = load_config()
    engs = all_engines()
    rows = preflight_report(engs)
    manifests = eg.manifest(engs)
    out = {
        "config_path": str(config_path()),
        "config": cfg,
        "engines": [{"kind": k, "name": n, "missing": m,
                     "spec": {"label": engs[k].spec.label,
                              "python_pkgs": list(engs[k].spec.python_pkgs),
                              "binaries": list(engs[k].spec.binaries),
                              "needs_gpu": engs[k].spec.needs_gpu,
                              "needs_network": engs[k].spec.needs_network,
                              "egress": engs[k].spec.egress,
                              "note": engs[k].spec.note}}
                    for (k, n, m) in rows],
        "egress": manifests,
        "any_egress": eg.any_egress(engs),
        "consent_file": str(eg.consent_file(kb.CONFIG_DIR)),
    }

    if args.grant:
        p = eg.grant(kb.CONFIG_DIR, engs, args.grant)
        out["granted_to"] = str(p)

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if all(not m for (_, _, m) in rows) else 1

    print("=" * 88)
    print("FireKB 引擎层自检")
    print("=" * 88)
    print(f"配置文件：{config_path()}")
    print()
    ok_all = True
    for kind, name, miss in rows:
        eng = engs[kind]
        flag = "[OK]" if not miss else "[FAIL]"
        ok_all &= not miss
        print(f"{flag} {kind:<7} {name}")
        print(f"       {eng.spec.label}")
        caps = []
        if eng.spec.python_pkgs:
            caps.append("python 包：" + "、".join(eng.spec.python_pkgs))
        if eng.spec.binaries:
            caps.append("可执行文件：" + "、".join(eng.spec.binaries))
        caps.append("GPU：" + ("必需" if eng.spec.needs_gpu else "可选"))
        caps.append("联网：" + ("必需" if eng.spec.needs_network else "不需要"))
        caps.append("数据出境：" + eng.spec.egress)
        for c in caps:
            print(f"       · {c}")
        if eng.spec.note:
            print(f"       · 说明：{eng.spec.note}")
        for m in miss:
            print(f"       ★ 缺失：{m}")
        print()

    print("-" * 88)
    if out["any_egress"]:
        print("⚠️ 当前配置含**数据出境**引擎，开工前需要显式同意：")
        for r in manifests:
            if r["egress"] != "none":
                print(f"   · {r['kind']}:{r['engine']} → {r['meaning']}")
        got = eg.load_consent(kb.CONFIG_DIR).get("granted", {})
        print(f"   同意文件：{eg.consent_file(kb.CONFIG_DIR)}（已记录 {len(got)} 条）")
    else:
        print("结论：全部引擎在**本机**执行，没有数据出境。")
    print("=" * 88)
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
