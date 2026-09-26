# -*- coding: utf-8 -*-
"""
M2b · 扫描页 OCR 模块
======================
把影印版 PDF（无文字层）的页面渲染成图片后做 OCR，把文字补回 pages.jsonl。

背景：教材里经常混有纯影印版 PDF —— 整页没有文字层，解析阶段只能得到空文本。
没有本模块，这类教材的正文完全不可用。

依赖：rapidocr-onnxruntime（识别）+ **解析引擎**（渲染页面，见 00_配置/engines.json）
     速度参考：引擎加载约 3.5 秒，单页 OCR 约 10.5 秒（ARM64 上以 x64 模拟运行，无硬件加速）

用法：
  python tools\\ocr_pages.py --check                  # 自检与规模估算
  python tools\\ocr_pages.py --limit 5                # 先试跑 5 页
  python tools\\ocr_pages.py --course 防排烟工程        # 只处理某课程
  python tools\\ocr_pages.py                          # 处理全部待 OCR 页
  python tools\\ocr_pages.py --workers 2              # 多进程加速（默认 1）

设计要点：
  * 断点续跑：处理过的页记录在 30_知识点/state_ocr.json，中断后直接重跑即可接着做。
  * 不改原始文件；OCR 结果写回 pages.jsonl 对应记录，并保留原始标记：
      is_scan=true 且 text_source="ocr"，同时保留 ocr_* 元数据便于追溯。
  * 重写前自动备份 pages.jsonl（90_日志/pages_backup_<时间>.jsonl）。
  * 置信度低的段落会写入 ocr_low_conf 字段，供 M5 校验时优先复核。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 路径常量统一向共享层 kb 取（不要在本地各写一遍 FIREKB_ROOT 解析）。
# ⚠️ 本文件的 `STATE_FILE` 是 **state_ocr.json**（OCR 专用进度），
#    **不是** `kb.STATE_FILE`（那是 `state.json`）—— 名字撞车、指向不同，故用 kb.KP_DIR 派生。
import kb  # noqa: E402  共享层（路径常量唯一来源）

ROOT = kb.ROOT
ARCHIVE = kb.ARCHIVE
TEXT_DIR = kb.TEXT_DIR
KP_DIR = kb.KP_DIR
LOG_DIR = kb.LOG_DIR
PAGES_FILE = kb.PAGES_FILE
OCR_RESULTS_FILE = TEXT_DIR / "ocr_results.jsonl"
STATE_FILE = KP_DIR / "state_ocr.json"

LOW_CONF_THRESHOLD = 0.70   # 段落平均置信度低于此值 -> 记入 ocr_low_conf
MIN_SEG_CHARS = 1


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------
_engine = None


def get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
    return _engine


def parse_ocr_output(out):
    """兼容 rapidocr 不同版本的返回结构，统一成 [(box, text, score), ...]。"""
    if isinstance(out, tuple):
        res = out[0]
        if res is None and len(out) > 1 and isinstance(out[1], list):
            res = out[1]
    else:
        txts = getattr(out, "txts", None)
        if txts is not None:
            boxes = getattr(out, "boxes", []) or []
            scores = getattr(out, "scores", []) or []
            res = list(zip(boxes, txts, scores))
        else:
            res = getattr(out, "boxes", None)
    items = []
    for it in res or []:
        try:
            box, text, score = it[0], it[1], it[2]
            if not text or not str(text).strip():
                continue
            items.append((box, str(text).strip(), float(score) if score is not None else 0.0))
        except Exception:
            continue
    return items


def lines_from_items(items) -> list[dict]:
    """把 OCR 的文本框按视觉行聚合并排序。"""
    boxes = []
    for box, text, score in items:
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
        except Exception:
            continue
        boxes.append({"x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys),
                      "text": text, "score": score})
    boxes.sort(key=lambda d: (d["y0"], d["x0"]))

    lines: list[dict] = []
    for b in boxes:
        cy = (b["y0"] + b["y1"]) / 2
        h = max(b["y1"] - b["y0"], 1)
        placed = False
        for ln in lines:
            lcy = (ln["y0"] + ln["y1"]) / 2
            if abs(cy - lcy) < max(8.0, h * 0.6):
                ln["parts"].append(b)
                ln["y0"] = min(ln["y0"], b["y0"])
                ln["y1"] = max(ln["y1"], b["y1"])
                placed = True
                break
        if not placed:
            lines.append({"y0": b["y0"], "y1": b["y1"], "parts": [b]})

    for ln in lines:
        ln["parts"].sort(key=lambda d: d["x0"])
        ln["text"] = " ".join(p["text"] for p in ln["parts"]).strip()
        ln["score"] = sum(p["score"] for p in ln["parts"]) / max(len(ln["parts"]), 1)
    lines.sort(key=lambda d: d["y0"])
    return lines


def paragraphs_from_lines(lines: list[dict]) -> tuple[str, list[float]]:
    """
    按行间距合并段落。中文同段直接拼接（不加空格），段间用换行。
    返回 (正文文本, 各段置信度列表)。
    """
    paras: list[dict] = []
    cur: dict | None = None
    for ln in lines:
        if not ln["text"]:
            continue
        h = max(ln["y1"] - ln["y0"], 1.0)
        if cur is None:
            cur = {"text": ln["text"], "scores": [ln["score"]], "y0": ln["y0"], "y1": ln["y1"]}
            continue
        gap = ln["y0"] - cur["y1"]
        if gap < h * 0.75:
            cur["text"] += ln["text"]
            cur["scores"].append(ln["score"])
            cur["y1"] = ln["y1"]
        else:
            paras.append(cur)
            cur = {"text": ln["text"], "scores": [ln["score"]], "y0": ln["y0"], "y1": ln["y1"]}
    if cur:
        paras.append(cur)

    texts = []
    confs = []
    for p in paras:
        t = p["text"].strip()
        if len(t) >= MIN_SEG_CHARS:
            texts.append(t)
            confs.append(sum(p["scores"]) / max(len(p["scores"]), 1))
    return "\n".join(texts), confs


def ocr_image_array(np_img) -> tuple[str, float, int]:
    """对一张图做 OCR，返回 (文本, 平均置信度, 行数)。"""
    engine = get_engine()
    out = engine(np_img)
    items = parse_ocr_output(out)
    if not items:
        return "", 0.0, 0
    lines = lines_from_items(items)
    text, confs = paragraphs_from_lines(lines)
    avg = sum(confs) / len(confs) if confs else 0.0
    return text, avg, len(lines)


# 注：这里原本有一个 render_page_array()，自己 import PDF 库把页面渲染成图像。
# 该职责已归**解析引擎**（只有解析侧才知道怎么打开文档），调用方走
# `_parser().open_handle(src)` → `handle.render_array(page_no, dpi)`。
# 函数已无调用者，故删除 —— 留一个内部还在 import PDF 库的空壳，
# 会把"渲染能力属于引擎层"这件事重新说糊。


# ---------------------------------------------------------------------------
# 状态 / 数据
# ---------------------------------------------------------------------------
def load_ocr_results() -> dict:
    """
    加载磁盘上已有的 OCR 结果（key -> record）。
    这是断点续跑的权威依据：结果逐页追加落盘，中断只损失当前这一页。
    """
    out: dict[str, dict] = {}
    if OCR_RESULTS_FILE.exists():
        with OCR_RESULTS_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                k = rec.get("key")
                if k:
                    out[k] = rec
    return out


def append_ocr_result(rec: dict) -> None:
    """追加一条 OCR 结果并立即刷盘（OCR 很贵，绝不允许白跑）。"""
    OCR_RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OCR_RESULTS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def page_key(r: dict) -> str:
    return f"{r.get('source_file')}#{r.get('page_no')}"


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    KP_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_pages() -> list[dict]:
    rows = []
    with PAGES_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def source_of_page(rel: str) -> Path:
    return ARCHIVE / rel


def pending_pages(rows: list[dict], course: str | None, force: bool,
                  done_keys: set[str] | None = None) -> list[dict]:
    done_keys = done_keys or set()
    todo = []
    for r in rows:
        if course and r.get("course") != course:
            continue
        if r.get("source_kind") != "pdf":
            continue
        # 需要 OCR 的判定：扫描页，或文字层极少（<30 字）
        needs = r.get("is_scan") or r.get("char_count", 0) < 30
        if not needs:
            continue
        if not force and (r.get("text_source") == "ocr" or page_key(r) in done_keys):
            continue
        todo.append(r)
    return todo


def check(args) -> int:
    print("=" * 70)
    print("M2b 扫描页 OCR · 自检")
    print("=" * 70)
    try:
        import numpy  # noqa: F401
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401
    except Exception as exc:
        print(f"[FAIL] 依赖缺失：{type(exc).__name__}: {exc}")
        print("       安装：python -m pip install rapidocr-onnxruntime")
        return 2
    # 渲染页面这一能力来自**解析引擎**（不在这里直接 import 某个 PDF 库）：
    # 换引擎是本项目的常规操作，自检必须跟着配置走，否则会检验一个根本没被使用的库。
    _pet = _parser()
    _miss = _pet.preflight()
    if _miss:
        print(f"[FAIL] 解析引擎 {_pet.spec.name} 不可用（OCR 需要它渲染页面）：")
        for m in _miss:
            print(f"       - {m}")
        return 2
    print(f"[ OK ] numpy / rapidocr-onnxruntime 均可导入；"
          f"解析引擎 {_pet.spec.name} 可用（负责渲染页面）")

    if not PAGES_FILE.exists():
        print(f"[FAIL] 文本库不存在：{PAGES_FILE}")
        print("       请先运行：python tools\\parse_docs.py")
        return 2

    rows = load_pages()
    todo = pending_pages(rows, args.course, args.force, set(load_ocr_results()))
    by_course: dict[str, int] = {}
    by_file: dict[str, int] = {}
    for r in todo:
        by_course[r.get("course", "?")] = by_course.get(r.get("course", "?"), 0) + 1
        by_file[r.get("source_file", "?")] = by_file.get(r.get("source_file", "?"), 0) + 1

    print(f"\n文本库总页数 : {len(rows)}")
    print(f"待 OCR 页数  : {len(todo)}")
    est = len(todo) * 10.5
    print(f"预计耗时     : 约 {est / 3600:.1f} 小时（按约 10.5 秒/页、单进程估算）")
    if by_course:
        print("\n按课程：")
        for c, n in sorted(by_course.items(), key=lambda x: -x[1]):
            print(f"  {c:<28}{n:>5} 页   约 {n * 10.5 / 60:>6.1f} 分钟")
    print("\n建议：先用 --limit 5 试跑，确认质量后再全量运行（可随时 Ctrl+C，支持断点续跑）。")
    print("=" * 70)
    return 0


def _parser():
    """解析引擎（负责打开文档与渲染页面）——按 00_配置/engines.json 选择。"""
    from engines import get_engine
    return get_engine("parser")


def _ocr():
    """OCR 引擎（负责把图像识别成文本）——按 00_配置/engines.json 选择。"""
    from engines import get_engine
    return get_engine("ocr")


def main() -> int:
    ap = argparse.ArgumentParser(description="扫描页 OCR（把影印版 PDF 变成可用文本）")
    ap.add_argument("--check", action="store_true", help="只做自检与规模估算")
    ap.add_argument("--course", help="只处理某课程")
    ap.add_argument("--limit", type=int, help="最多处理 N 页（试跑用）")
    ap.add_argument("--dpi", type=int, default=150, help="渲染分辨率，默认 150（越高越慢）")
    ap.add_argument("--force", action="store_true", help="忽略已完成记录，重新 OCR")
    ap.add_argument("--workers", type=int, default=1, help="并行进程数（默认 1，最稳）")
    ap.add_argument("--dry-run", action="store_true", help="只列出将处理的页，不实际执行")
    ap.add_argument("--allow-egress", action="store_true",
                    help="放行「数据出境」引擎（仅当你确认有权上传素材；默认拒绝）")
    args = ap.parse_args()

    if args.check:
        return check(args)

    # 引擎层：依赖自检 + 数据出境把关
    #   M2 要的是 **parser（打开文档、渲染页面）+ ocr（识别）**，与转写无关。
    from engines import config_path, get_engines, require_ready
    from engines import egress as _egress
    _engines = get_engines("parser", "ocr")
    require_ready(_engines)
    _egress.require_consent(_engines, config_path().parent, allow_flag=args.allow_egress)

    if not PAGES_FILE.exists():
        print(f"[FAIL] 文本库不存在：{PAGES_FILE}")
        return 2

    rows = load_pages()
    results = load_ocr_results()
    todo = pending_pages(rows, args.course, args.force, set(results))
    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        print("没有需要 OCR 的页面（若确实有扫描页，试试 --force 或先跑 parse_docs.py）")
        return 0

    if args.dry_run:
        print(f"将处理 {len(todo)} 页：")
        for r in todo[:50]:
            print(f"  {r.get('source_file')}  {r.get('page_label')}")
        if len(todo) > 50:
            print(f"  ... 其余 {len(todo) - 50} 页")
        return 0

    # 备份
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    backup = LOG_DIR / f"pages_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    shutil.copy2(PAGES_FILE, backup)
    print(f"已备份文本库 -> {backup.name}")

    # 按源文件分组，避免反复开关 PDF
    by_file: dict[str, list[dict]] = {}
    for r in todo:
        by_file.setdefault(r["source_file"], []).append(r)

    state = load_state()
    done_key = "done"
    state.setdefault(done_key, {})
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")

    total = len(todo)
    processed = 0
    ocr_ok = 0
    ocr_empty = 0
    failed: list[str] = []
    low_conf_pages = 0
    t_start = time.time()

    print(f"共 {total} 页待处理，来自 {len(by_file)} 个文件\n")

    # 引擎层：页面渲染由**解析引擎**提供（只有解析侧知道怎么开文档），
    # 识别由 **OCR 引擎**提供。本脚本只负责调度、断点与落盘。
    _parser_eng = _parser()
    _ocr_eng = _ocr()

    for rel, pages in sorted(by_file.items()):
        src = source_of_page(rel)
        if not src.exists():
            print(f"[跳过] 源文件不存在：{rel}")
            failed.append(rel)
            continue
        try:
            _handle = _parser_eng.open_handle(src)
        except Exception as exc:
            print(f"[跳过] 无法打开 {rel}：{type(exc).__name__}: {exc}")
            failed.append(rel)
            continue

        # 同一份 PDF 内的待处理页按页码排序
        pages.sort(key=lambda r: r.get("page_no", 0))
        for r in pages:
            pno = r.get("page_no")
            key = f"{rel}#{pno}"
            tag = f"{rel.split('/')[-1][:28]} {r.get('page_label')}"
            if not args.force and key in state[done_key]:
                processed += 1
                continue
            try:
                t0 = time.time()
                arr = _handle.render_array(pno, args.dpi)
                text, avg, nlines = _ocr_eng.recognize(arr)
                dt = time.time() - t0
            except Exception as exc:
                print(f"  [FAIL] {tag}  {type(exc).__name__}: {exc}")
                failed.append(key)
                processed += 1
                continue

            processed += 1
            if not text.strip():
                ocr_empty += 1
            else:
                ocr_ok += 1

            low = ""
            if avg and avg < LOW_CONF_THRESHOLD:
                low_conf_pages += 1
                low = " [低置信]"

            # 立即落盘：OCR 很贵，中断绝不允许丢结果
            rec = {
                "key": key,
                "source_file": r.get("source_file"),
                "page_no": pno,
                "text": text,
                "confidence": round(avg, 3),
                "lines": nlines,
                "at": datetime.now().isoformat(timespec="seconds"),
            }
            append_ocr_result(rec)
            results[key] = rec
            state[done_key][key] = {"conf": rec["confidence"], "chars": len(text),
                                    "at": rec["at"]}
            if processed % 20 == 0:
                state["updated_at"] = rec["at"]
                save_state(state)

            elapsed = time.time() - t_start
            eta = (elapsed / processed) * (total - processed) if processed else 0
            print(f"  [{processed}/{total}] {tag}  {len(text)}字 置信{avg:.2f} "
                  f"{dt:.1f}s  ETA {eta / 60:.0f}min{low}")
        # 句柄归**解析引擎**所有（只有它知道文档是怎么打开的），不能直接调底层库的 close
        try:
            _parser_eng.close_handle(_handle)
        except Exception as exc:
            print(f"  [警告] 关闭 {rel} 的句柄失败：{type(exc).__name__}: {exc}")

    save_state(state)

    # 合并：把 ocr_results.jsonl 中的全部结果应用到 pages 记录（不分是哪一次跑出来的）
    applied = 0
    for r in rows:
        rec = results.get(page_key(r))
        if not rec:
            continue
        r["text"] = rec["text"]
        r["text_flat"] = " ".join(rec["text"].split())
        r["char_count"] = len(rec["text"])
        r["text_source"] = "ocr"
        r["ocr_engine"] = "rapidocr-onnxruntime"
        r["ocr_confidence"] = rec.get("confidence")
        r["ocr_lines"] = rec.get("lines")
        r["ocr_at"] = rec.get("at")
        r["is_scan"] = True  # 保留「来源是扫描件」这一事实
        applied += 1

    with PAGES_FILE.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"已合并 OCR 结果 {applied} 页到 pages.jsonl")

    total_elapsed = time.time() - t_start
    print("\n" + "=" * 70)
    print(f"处理完成：{processed} 页，成功 {ocr_ok}，空结果 {ocr_empty}，失败 {len(failed)}")
    print(f"低置信页（<{LOW_CONF_THRESHOLD}）：{low_conf_pages}  —— 这些页的内容建议人工复核")
    print(f"总耗时 {total_elapsed / 60:.1f} 分钟，平均 {total_elapsed / max(processed, 1):.1f} 秒/页")
    print(f"文本库已更新：{PAGES_FILE}")
    print(f"备份保留在  ：{backup}")
    if failed:
        print(f"\n失败清单（{len(failed)}）：")
        for x in failed[:20]:
            print(f"  - {x}")
        print("  重跑本脚本即可只处理失败与未完成的部分。")
    print("=" * 70)
    print("\n下一步：python tools\\status.py  查看进度，随后可运行 build_skeleton.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
