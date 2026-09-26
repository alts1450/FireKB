# -*- coding: utf-8 -*-
"""
page_offsets.py —— 页码双轨：PDF 物理页 ↔ 书上印刷页
====================================================

## 问题

`pages.jsonl` 里的 `page_no` / `page_label` 是 **PDF 物理页**（第几张），
而书上印的是**印刷页码**。这个偏移量**每本书都不一样**：同一门课程下
合订的多本规范各有各的正文起始位，offset 自然不同。
⇒ 若不加换算，用"第 17 页"这种说法去引用，人和机器会指到不同的地方。

## 本工具的方向约定（**必须写清楚，否则又是一个两套口径**）

    印刷页 = 物理页 + offset        ⇒  offset = 印刷页 - 物理页

以 `防排烟工程` 为例：物理第 311 页上印着 "294"，所以它的 offset = **-17**。
（"物理 - 印刷"得到的是 +17，方向相反 —— 两套口径混用会让引用整篇指错页。
  本工具与配置一律用上面这条。）

## 两条来源，人工优先

  1. **自动探测**（`--detect`）：读每页文本的首行/末行里的**裸数字**，
     对每页得到若干候选 (候选值 - 物理页号)，取出现最多的那个作为该文件的 offset，
     并要求**一致性比例**过阈值才采纳（否则报"测不准"，交人工）。
  2. **人工登记**（直接编辑 `00_配置/page-offsets.json`，把 `source` 改成 `manual`）：
     `--write` **永远不会覆盖 manual 条目** —— 人的判断优先于探测。

## 用法

    python tools\\page_offsets.py --detect          # 只探测并报告（只读）
    python tools\\page_offsets.py --write           # 把探测结果写入配置（保留 manual）
    python tools\\page_offsets.py --show            # 显示当前生效的偏移
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(os.environ.get("FIREKB_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "tools"))

import kb  # noqa: E402

CONFIG_FILE = kb.CONFIG_DIR / "page-offsets.json"

# 一行里的裸数字（允许 "294 防排烟工程" / "防排烟工程 294" 这类首末行形态）
_INT_RE = re.compile(r"(?<![0-9.])([0-9]{1,4})(?![0-9.])")
# 明显不是页码的数字：年份、章号（第N章）、表号图号
_NOISE_RE = re.compile(r"(19|20)[0-9]{2}")


def _candidates(page: dict) -> list[int]:
    """从该页文本的**首行与末行**里取候选印刷页码。"""
    text = (page.get("text") or "").strip()
    if not text:
        return []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out: list[int] = []
    for ln in (lines[:1] + lines[-1:]):
        if len(ln) > 60:            # 首末行不该是一大段正文
            continue
        for m in _INT_RE.finditer(ln):
            v = int(m.group(1))
            if 1 <= v <= 2000 and not _NOISE_RE.search(m.group(1)):
                out.append(v)
    return out


def detect() -> dict:
    """对每个 (课程, 文件) 探测 offset。返回结构见 _empty_result 注释。"""
    pages = kb.load_jsonl(kb.PAGES_FILE)
    by_file: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for p in pages:
        if str(p.get("source_kind") or "").lower() != "pdf":
            continue
        by_file[(p.get("course") or "", p.get("file_id") or "")].append(p)

    results: dict = {}
    for (course, fid), pgs in sorted(by_file.items()):
        deltas: collections.Counter = collections.Counter()
        npage = 0
        for pg in pgs:
            pn = pg.get("page_no")
            if not isinstance(pn, int):
                continue
            npage += 1
            for cand in _candidates(pg):
                deltas[cand - pn] += 1
        if not npage:
            continue
        best, bestn = (None, 0)
        if deltas:
            best, bestn = deltas.most_common(1)[0]
        ratio = bestn / npage if npage else 0.0
        tier = ("high" if ratio >= 0.75 else "medium" if ratio >= 0.50 else "low")
        results.setdefault(course, {"files": [], "pages": 0})
        results[course]["files"].append({
            "file_id": fid,
            "source_file": (pgs[0].get("source_file") or "").split("/")[-1],
            "pages": npage,
            "offset": best,
            "hits": bestn,
            "ratio": round(ratio, 3),
            "tier": tier,
            "runners_up": deltas.most_common(4)[1:],
            "confident": bool(best is not None and ratio >= 0.30 and bestn >= 5),
        })
        results[course]["pages"] += npage

    # 课程级 offset 只在一个条件下给出：**所有自信的文件都同意**
    # ⚠️ 多本书合订成一门课的目录（如 `通用规范` 收录 4 本规范）里，各书 offset
    #    可能分别是 -14 / -8 / -4 / 0（书不同、正文起始位就不同）——
    #    取加权众数会得到一个"45% 一致度"的假数字，用它去引用任何一本都会指错页。
    #    ⇒ 多本书的课**不给课程级 offset**，一律按文件取。
    for course, info in results.items():
        conf = [f for f in info["files"] if f["confident"]]
        offs = {f["offset"] for f in conf}
        info["confident_files"] = len(conf)
        info["total_files"] = len(info["files"])
        if len(offs) == 1 and conf:
            info["offset"] = conf[0]["offset"]
            info["confident"] = True
            # 一致度 = 同意的文件里较弱那个的命中率（不掩盖"其中一本只有 50% 命中"）
            info["agree"] = round(min(f["ratio"] for f in conf), 3)
        else:
            info["offset"] = None
            info["confident"] = False
            info["agree"] = 0.0
        info["mixed"] = len(offs) > 1
    return results


def show(results: dict | None = None) -> None:
    results = results if results is not None else detect()
    print("=" * 96)
    print("页码双轨探测（印刷页 = 物理页 + offset，即 offset = 印刷页 - 物理页）")
    print("=" * 96)
    for course in sorted(results):
        info = results[course]
        if info["confident"]:
            head = f"课程 offset = {info['offset']}  ✓（全部自信文件同意，最低命中率 {info['agree']:.0%}）"
        elif info["mixed"]:
            head = "✗ **各文件 offset 不同 ⇒ 不给课程级 offset**（按文件取，见下）"
        else:
            head = "✗ 测不准（需人工登记）"
        print(f"\n▍{course}  {head}")
        print(f"    文件 {info['total_files']} 个，其中自信 {info['confident_files']} 个")
        for f in sorted(info["files"], key=lambda x: -x["pages"]):
            mark = "✓" if f["confident"] else "?"
            print(f"    [{mark}] offset {str(f['offset']):>5}  命中 {f['hits']:>4}/{f['pages']:<4}"
                  f" ({f['ratio']:>4.0%}, {f['tier']:<6})  {f['source_file'][:48]}")
            if not f["confident"] and f["runners_up"]:
                print(f"          次选：{f['runners_up']}")
    print()


def _empty_config() -> dict:
    return {
        "_说明": ("页码双轨配置。offset 方向：**印刷页 = 物理页 + offset**，"
                  "即 offset = 印刷页 - 物理页。例：某书物理 p311 印着 294 ⇒ offset = -17。"),
        "_为什么以「文件」为第一公民": [
            "同一门课若挂了多本书，各书的正文起始位不同 —— 例如某个规范目录下 4 本书的 offset",
            "分别是 -14 / -8 / -4 / 0。此时若只存课程级 offset，取众数会得到一个「45% 一致度」的假数字，",
            "拿它去引用任何一本都会指错页 —— 这正是「一个数字两种口径」的典型后果。",
            "⇒ 配置里 **files 是事实来源**；课程级 offset 只在「全部自信文件都同意」时才给出。"
        ],
        "_人工登记（优先于探测）": [
            "① 课程级：把该课程 source 改成 manual 并填 offset —— 覆盖它所有文件；",
            "② 文件级：在该课程 files.{file_id} 里把 source 改成 manual —— 只覆盖那一个文件，优先级最高。",
            "`--write` 永远不会覆盖任何 manual 条目。"
        ],
        "generated_at": None,
        "courses": {},
    }


def write_config(results: dict) -> Path:
    cfg = kb.load_json(CONFIG_FILE, None) or _empty_config()
    cfg.setdefault("courses", {})
    cfg["generated_at"] = datetime.now().isoformat(timespec="seconds")
    kept, skipped, wrote = [], [], []
    for course, info in sorted(results.items()):
        old = cfg["courses"].get(course) or {}
        old_files = old.get("files") or {}
        if old.get("source") == "manual":
            kept.append(course)                 # 课程级人工登记：整门跳过，不碰它的 files
            continue
        entry = {
            "offset": info["offset"] if info["confident"] else None,
            "source": "auto" if info["confident"] else None,
            "mixed": info["mixed"],
            "agree": info["agree"],
            "detected_at": cfg["generated_at"],
            "files": {},
        }
        seen = set()
        for f in info["files"]:
            prev = old_files.get(f["file_id"]) or {}
            seen.add(f["file_id"])
            if prev.get("source") == "manual" or prev.get("anchors"):
                entry["files"][f["file_id"]] = prev       # 人工登记：原样保留
                continue
            entry["files"][f["file_id"]] = {
                "offset": f["offset"] if f["confident"] else None,
                "source": "auto" if f["confident"] else None,
                "ratio": f["ratio"],
                "tier": f["tier"],
                "pages": f["pages"],
                "name": f["source_file"],
            }
        # ⚠️ 关键：**探测不覆盖的文件必须原样带过去**。
        # 本工具只探测 source_kind == pdf 的页。EPUB 教材的 page_no 是**节序号**，
        # 与书的印刷页并不等长（一节可能跨若干屏），单一 offset 对它不成立 ——
        # 所以 EPUB 的页码靠**锚点**锚定原文手工登记，也不会出现在探测结果里。
        # 这里若不搬运，`--write` 会把人工登记的 EPUB 锚点整条丢掉 ——
        # 这类"工具静默删除人工数据"比不写更糟。
        carried = []
        for fid, rec in old_files.items():
            if fid in seen:
                continue
            if rec.get("source") == "manual" or rec.get("anchors"):
                entry["files"][fid] = rec
                carried.append(rec.get("name") or fid)
        cfg["courses"][course] = entry
        (wrote if info["confident"] else skipped).append(course)
        if carried:
            print(f"  [{course}] 保留 {len(carried)} 个非 PDF 文件的人工登记：{'、'.join(carried)}")
    kb.write_json(CONFIG_FILE, cfg)
    print(f"已写入：{CONFIG_FILE}")
    print(f"  课程级 offset 已确定 {len(wrote)} 门：{', '.join(wrote) or '-'}")
    if kept:
        print(f"  保留人工登记（整门未覆盖）：{', '.join(kept)}")
    if skipped:
        print(f"  **无课程级 offset**（多文件不一致或测不准，按 files 取）：{', '.join(skipped)}")
    return CONFIG_FILE


def main() -> int:
    ap = argparse.ArgumentParser(description="页码双轨：PDF 物理页 ↔ 书上印刷页")
    ap.add_argument("--detect", action="store_true", help="只探测并报告（只读，默认行为）")
    ap.add_argument("--write", action="store_true", help="把探测结果写入配置（保留人工登记）")
    ap.add_argument("--show", action="store_true", help="显示当前生效的偏移")
    args = ap.parse_args()

    if args.show:
        cfg = kb.load_json(CONFIG_FILE, None) or {}
        print("=" * 78)
        print("当前生效的页码偏移（印刷页 = 物理页 + offset）")
        print("=" * 78)
        cs = cfg.get("courses") or {}
        if not cs:
            print("  （配置为空：先跑 --detect --write，或手工登记）")
        for c, v in sorted(cs.items()):
            print(f"  {c:<26} offset {str(v.get('offset')):>5}  来源 {v.get('source')}"
                  f"  一致度 {v.get('agree')}")
        print(f"\n配置文件：{CONFIG_FILE}")
        return 0

    res = detect()
    show(res)
    if args.write:
        write_config(res)
    else:
        print("（只读模式。写入请加 --write；人工登记见配置文件 _人工登记 说明）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
