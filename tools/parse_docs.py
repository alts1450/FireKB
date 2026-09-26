# -*- coding: utf-8 -*-
"""
M2 解析模块 —— 把课件 / 规范 / 教材解析成「可定位到页」的文本
=================================================================
输入：<项目根>\\10_原始归档\\课件\\** 与 10_原始归档\\规范教材\\**
      支持 .pdf / .pptx / .epub
输出：<项目根>\\20_文本库\\pages.jsonl      每页一行（核心契约文件）
      <项目根>\\20_文本库\\page_map.json   原文件页码 <-> 合并后全局页码
      <项目根>\\20_文本库\\parse_report.json 解析统计与告警

设计原则（务必遵守）
  1. 只读原始文件，绝不修改、绝不移动（M1 归档原则）。
  2. 所有页码统一为「该文件内 1-based 页码」，合并页码只出现在 page_map.json，
     引用展示时一律换算回原文件页码。
  3. 文本过少的页显式标记 is_scan=true（扫描页/图片页），交给 OCR 或人工，不静默丢弃。
  4. 零第三方依赖：PDF 用 pymupdf；PPTX 用 python-pptx；EPUB 用标准库 zipfile。
     在 ARM64 等 wheel 不齐的平台上，重型依赖经常根本装不上，能省的依赖就省。

用法：
  python tools\\parse_docs.py              # 增量解析（已解析过的跳过）
  python tools\\parse_docs.py --force      # 全部重新解析
  python tools\\parse_docs.py --only pdf   # 只处理某类文件
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

try:  # 保证中文输出在 Windows 控制台/管道下不乱码
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ----------------------------------------------------------------------------
# 路径约定
# ----------------------------------------------------------------------------
# 路径常量统一向共享层 kb 取 —— 不要在本地各写一遍
# `Path(os.environ.get("FIREKB_ROOT") or Path(__file__).resolve().parents[1])`：
# 路径规则以后若要改，散在各处就得改很多遍，漏掉一处两个脚本看到的就是不同的项目根。
# ⚠️ 本文件的 `OUT_DIR` **就是** 20_文本库（kb 里叫 TEXT_DIR）：别名保留是为了不动调用点，
#    但它和 transcribe.py 的 `OUT_DIR` 同义、与 ocr_pages 的 `STATE_FILE` 不同义 —— 别按名字猜。
import kb  # noqa: E402  共享层（路径常量唯一来源）

ROOT = kb.ROOT
ARCHIVE = kb.ARCHIVE
SRC_DIRS = [ARCHIVE / "课件", ARCHIVE / "规范教材"]
OUT_DIR = kb.TEXT_DIR
LOG_DIR = kb.LOG_DIR

PAGES_FILE = kb.PAGES_FILE
PAGEMAP_FILE = OUT_DIR / "page_map.json"
REPORT_FILE = OUT_DIR / "parse_report.json"

SUPPORTED = {".pdf", ".pptx", ".epub"}
SCAN_CHAR_THRESHOLD = 30  # 单页有效字符少于此值 -> 判为扫描页/图片页


# ----------------------------------------------------------------------------
# 通用工具
# ----------------------------------------------------------------------------
def normalize_text(s: str) -> str:
    """规范化文本：统一空白、去行首尾空格、压缩连续空行。保留段落结构。"""
    if not s:
        return ""
    s = s.replace("\u00a0", " ").replace("\u3000", " ")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    blank = 0
    for raw_line in s.split("\n"):
        line = raw_line.strip()
        if not line:
            blank += 1
            if blank <= 1:
                out.append("")
            continue
        blank = 0
        out.append(line)
    return "\n".join(out).strip()


def flatten_text(s: str) -> str:
    """把文本压成单行，用于关键词检索与相似度比对。"""
    return re.sub(r"\s+", " ", s or "").strip()


def file_id_of(rel_posix: str) -> str:
    """用相对路径生成稳定 file_id（可读 + 短哈希防歧义）。"""
    h = hashlib.sha1(rel_posix.encode("utf-8")).hexdigest()[:8]
    stem = Path(rel_posix).stem
    safe = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", stem)[:40]
    return f"{safe}__{h}"


def guess_course(rel_parts: list[str]) -> str:
    """
    从相对路径推断课程/分类。
    课件/建筑防火/第01讲.pdf  ->  建筑防火
    课件/第01讲.pdf           ->  未分类
    """
    # rel_parts 形如 ['课件', '建筑防火', '第01讲.pdf']
    if len(rel_parts) >= 3:
        return rel_parts[-2]
    return "未分类"


def sha1_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ----------------------------------------------------------------------------
# PDF
# ----------------------------------------------------------------------------
def parse_pdf(path: Path) -> tuple[list[dict], list[str]]:
    """
    PDF → 页级记录。

    **本函数不 import 任何 PDF 库**：打开文档、逐页取文字都走解析引擎，
    具体实现由 `00_配置/engines.json` 决定（pypdfium2 / PyMuPDF / 云端…）。
    这里只负责一件事 —— 组装页记录契约
    （`page_no` / `page_label` / `text` / `text_flat` / `char_count` / `is_scan` …）。

    这样切的好处：契约只有一处实现，换引擎时**页记录结构必然一致**，
    差别只落在文字本身；也避免"某个引擎偷偷绕过契约自己造一种页格式"。
    """
    eng = _parser()
    pages: list[dict] = []
    warnings: list[str] = []

    try:
        handle = eng.open_handle(path)
    except Exception as exc:               # 加密 / 损坏 / 缺依赖 —— 都要如实报出，不静默吞
        warnings.append(f"无法打开 PDF（{type(exc).__name__}: {str(exc)[:80]}）")
        return pages, warnings

    try:
        if getattr(handle, "encrypted", False):
            warnings.append("PDF 已加密，无法解析")
            return pages, warnings
        for idx in range(1, len(handle) + 1):
            try:
                raw = handle.text_of(idx)
            except Exception as exc:       # 单页失败不影响整册
                raw = ""
                warnings.append(f"第 {idx} 页抽取异常：{type(exc).__name__}")
            txt = normalize_text(raw)
            pages.append(
                {
                    "page_no": idx,
                    "page_label": f"第{idx}页",
                    "text": txt,
                    "text_flat": flatten_text(txt),
                    "char_count": len(txt),
                    "is_scan": len(txt) < SCAN_CHAR_THRESHOLD,
                    "has_table": False,
                    "warnings": [],
                }
            )
    finally:
        eng.close_handle(handle)
    return pages, warnings


# ----------------------------------------------------------------------------
# PPTX
# ----------------------------------------------------------------------------
def _shape_sort_key(shp):
    top = getattr(shp, "top", None) or 0
    left = getattr(shp, "left", None) or 0
    return (int(top), int(left))


def _table_to_text(table) -> str:
    lines = []
    for row in table.rows:
        cells = []
        for cell in row.cells:
            cells.append(re.sub(r"\s+", " ", (cell.text or "").strip()))
        lines.append(" | ".join(cells))
    return "\n".join(lines)


def parse_pptx(path: Path) -> tuple[list[dict], list[str]]:
    from pptx import Presentation

    pages: list[dict] = []
    warnings: list[str] = []
    prs = Presentation(str(path))
    for idx, slide in enumerate(prs.slides, start=1):
        blocks: list[tuple[int, int, str]] = []
        note = ""
        has_table = False
        for shp in slide.shapes:
            try:
                if getattr(shp, "has_table", False) and shp.has_table:
                    has_table = True
                    blocks.append((*_shape_sort_key(shp), _table_to_text(shp.table)))
                    continue
                if getattr(shp, "has_text_frame", False) and shp.has_text_frame:
                    t = shp.text_frame.text
                    if t and t.strip():
                        blocks.append((*_shape_sort_key(shp), t))
            except Exception as exc:
                warnings.append(f"第 {idx} 页某个形状解析异常：{type(exc).__name__}")
        try:
            if slide.has_notes_slide:
                note = slide.notes_slide.notes_text_frame.text or ""
        except Exception:
            note = ""
        blocks.sort(key=lambda x: (x[0], x[1]))
        body = "\n".join(b[2] for b in blocks)
        if note.strip():
            body = body + "\n\n[备注] " + note.strip()
        txt = normalize_text(body)
        pages.append(
            {
                "page_no": idx,
                "page_label": f"第{idx}张",
                "text": txt,
                "text_flat": flatten_text(txt),
                "char_count": len(txt),
                "is_scan": len(txt) < SCAN_CHAR_THRESHOLD,
                "has_table": has_table,
                "warnings": [],
            }
        )
    return pages, warnings


# ----------------------------------------------------------------------------
# EPUB（标准库实现：EPUB = ZIP + XHTML）
# ----------------------------------------------------------------------------
class _HtmlTextExtractor(HTMLParser):
    SKIP = {"script", "style", "head", "title"}
    BREAK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
             "blockquote", "section", "article", "figcaption", "td"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in self.BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0 and data:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def _epub_opf_path(z: zipfile.ZipFile) -> str | None:
    container = "META-INF/container.xml"
    if container in z.namelist():
        xml = z.read(container).decode("utf-8", "ignore")
        m = re.search(r'full-path\s*=\s*"([^"]+)"', xml)
        if m:
            return m.group(1)
    return next((n for n in z.namelist() if n.lower().endswith(".opf")), None)


def _epub_manifest_spine(opf_xml: str) -> tuple[dict[str, str], list[str]]:
    manifest: dict[str, str] = {}
    for tag in re.findall(r"<item\b[^>]*>", opf_xml, flags=re.I):
        idm = re.search(r'\bid\s*=\s*"([^"]+)"', tag, flags=re.I)
        hrefm = re.search(r'\bhref\s*=\s*"([^"]+)"', tag, flags=re.I)
        if idm and hrefm:
            manifest[idm.group(1)] = hrefm.group(1)
    spine = re.findall(r'<itemref\b[^>]*\bidref\s*=\s*"([^"]+)"', opf_xml, flags=re.I)
    return manifest, spine


def _epub_title(opf_xml: str) -> str:
    m = re.search(r"<dc:title[^>]*>(.*?)</dc:title>", opf_xml, flags=re.I | re.S)
    return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""


def parse_epub(path: Path) -> tuple[list[dict], list[str]]:
    """
    EPUB 没有固定页码概念，以 spine 顺序的「文档」为单位，page_no = 阅读顺序序号。
    引用时显示为「第N节」，并附章节标题，避免与 PDF 页码混淆。
    """
    pages: list[dict] = []
    warnings: list[str] = []
    with zipfile.ZipFile(str(path)) as z:
        opf_path = _epub_opf_path(z)
        if not opf_path:
            warnings.append("EPUB 中找不到 .opf，无法解析")
            return pages, warnings
        opf_xml = z.read(opf_path).decode("utf-8", "ignore")
        manifest, spine = _epub_manifest_spine(opf_xml)
        base = str(Path(opf_path).parent)
        if base == ".":
            base = ""
        names = set(z.namelist())
        seq = 0
        for idref in spine:
            href = manifest.get(idref)
            if not href:
                continue
            inner = href.split("#")[0]
            full = f"{base}/{inner}" if base else inner
            full = str(Path(full).as_posix())
            if full not in names:
                # 有些 EPUB 用 URL 编码路径
                alt = next((n for n in names if n.endswith(inner)), None)
                if not alt:
                    warnings.append(f"spine 项缺失：{href}")
                    continue
                full = alt
            try:
                html = z.read(full).decode("utf-8", "ignore")
            except Exception as exc:
                warnings.append(f"读取 {full} 失败：{type(exc).__name__}")
                continue
            seq += 1
            ex = _HtmlTextExtractor()
            ex.feed(html)
            txt = normalize_text(ex.text())
            heading = txt.split("\n", 1)[0][:60] if txt else ""
            pages.append(
                {
                    "page_no": seq,
                    "page_label": f"第{seq}节",
                    "text": txt,
                    "text_flat": flatten_text(txt),
                    "char_count": len(txt),
                    "is_scan": len(txt) < SCAN_CHAR_THRESHOLD,
                    "has_table": "<table" in html.lower(),
                    "section_title": heading,
                    "warnings": [],
                }
            )
    return pages, warnings


# ----------------------------------------------------------------------------
# 调度
# ----------------------------------------------------------------------------
PARSERS = {".pdf": parse_pdf, ".pptx": parse_pptx, ".epub": parse_epub}
# ⚠️ 上面这张表与三个 parse_* 函数是**引擎层的实现体**（被 engines/parse_local.py 惰性调用），
#    流水线不再直接使用 PARSERS —— 由 00_配置/engines.json 决定实际用哪台引擎。


def _parser():
    """取解析引擎（按配置）；函数级导入以避免 engines ↔ parse_docs 的循环依赖。"""
    from engines import get_engine
    return get_engine("parser")


def collect_sources(only: str | None = None) -> list[Path]:
    found: list[Path] = []
    for d in SRC_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.rglob("*")):
            if p.is_file() and p.suffix.lower() in SUPPORTED:
                if only and p.suffix.lower() != f".{only.lower()}":
                    continue
                found.append(p)
    return found


def load_existing() -> dict[str, dict]:
    """读取已有的 pages.jsonl，按 file_id 归组，供增量解析判断。"""
    done: dict[str, dict] = {}
    if not PAGES_FILE.exists():
        return done
    with PAGES_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            fid = rec.get("file_id")
            if fid:
                done.setdefault(fid, {"sha1": rec.get("source_sha1"), "pages": 0})["pages"] += 1
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description="M2 课件/规范解析器")
    ap.add_argument("--force", action="store_true", help="忽略已有结果，全部重新解析")
    ap.add_argument("--only", choices=["pdf", "pptx", "epub"], help="只解析某类文件")
    ap.add_argument("--allow-egress", action="store_true",
                    help="放行「数据出境」引擎（仅当你确认有权上传素材；默认拒绝）")
    args = ap.parse_args()

    # 引擎层：先自检依赖、再把关数据出境 —— 缺件/未同意都当场拒开工
    #   只取 **parser**：OCR 与转写是别的环节的事，它们的依赖缺不缺不该挡住 M1
    #   （文档里那两者标的就是「可选」；早先取全量，导致只装了解析依赖的用户
    #    跑 parse 直接被拒，且报的是他根本没打算用的 OCR/ASR 清单）。
    from engines import config_path, get_engines, require_ready
    from engines import egress as _egress
    _engines = get_engines("parser")
    require_ready(_engines)
    _egress.require_consent(_engines, config_path().parent, allow_flag=args.allow_egress)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    sources = collect_sources(args.only)
    if not sources:
        print(f"[!] 在以下目录没有找到可解析文件（.pdf/.pptx/.epub）：")
        for d in SRC_DIRS:
            print(f"    {d}")
        print("    请先把课件/规范/教材放进上述目录。")
        return 1

    existing = {} if args.force else load_existing()
    print(f"[*] 待处理文件 {len(sources)} 个；已有记录 {len(existing)} 个 file_id")

    all_records: list[dict] = []
    files_meta: list[dict] = []
    report = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "total_files": len(sources),
        "parsed_files": 0,
        "skipped_files": 0,
        "failed_files": [],
        "total_pages": 0,
        "scan_pages": 0,
        "empty_pages": 0,
        "file_warnings": {},
    }

    global_page = 0  # 合并后的全局页码游标（仅在 page_map 中体现，不写进 pages）

    for idx, path in enumerate(sources, start=1):
        rel = path.relative_to(ARCHIVE).as_posix()
        rel_parts = rel.split("/")
        fid = file_id_of(rel)
        kind = path.suffix.lower().lstrip(".")
        print(f"[{idx}/{len(sources)}] ({kind}) {rel}")

        try:
            digest = sha1_file(path)
        except Exception as exc:
            report["failed_files"].append({"file": rel, "reason": f"读取失败 {type(exc).__name__}"})
            continue

        prev = existing.get(fid)
        if prev and prev.get("sha1") == digest and not args.force:
            print("      跳过（未变更）")
            report["skipped_files"] += 1
            continue

        try:
            # 引擎层：解析实现由 00_配置/engines.json 选择，本脚本不认识具体实现
            pages, warnings = _parser().parse(path)
        except Exception as exc:
            report["failed_files"].append({"file": rel, "reason": f"{type(exc).__name__}: {exc}"})
            print(f"      失败：{type(exc).__name__}: {exc}")
            continue

        if not pages:
            report["failed_files"].append({"file": rel, "reason": "无可解析内容"})
            continue

        course = guess_course(rel_parts)
        start_g = global_page + 1
        for pg in pages:
            rec = {
                "file_id": fid,
                "course": course,
                "source_file": rel,
                "source_kind": kind,
                "source_sha1": digest,
                "page_no": pg["page_no"],
                "page_label": pg["page_label"],
                "text": pg["text"],
                "text_flat": pg["text_flat"],
                "char_count": pg["char_count"],
                "is_scan": pg["is_scan"],
                "has_table": pg.get("has_table", False),
                "section_title": pg.get("section_title", ""),
            }
            all_records.append(rec)
            if pg["is_scan"]:
                report["scan_pages"] += 1
            if pg["char_count"] == 0:
                report["empty_pages"] += 1
                # 记录空页，便于后续 OCR
                with (LOG_DIR / "empty_pages.txt").open("a", encoding="utf-8") as lg:
                    lg.write(f"{rel}\t{pg['page_label']}\n")
        global_page += len(pages)

        files_meta.append(
            {
                "file_id": fid,
                "course": course,
                "source_file": rel,
                "source_kind": kind,
                "source_sha1": digest,
                "pages": len(pages),
                "merged_start": start_g,
                "merged_end": global_page,
                "scan_pages": sum(1 for p in pages if p["is_scan"]),
            }
        )
        report["parsed_files"] += 1
        report["total_pages"] += len(pages)
        if warnings:
            report["file_warnings"][rel] = warnings[:20]

    if all_records:
        mode = "w" if args.force else "a"
        with PAGES_FILE.open(mode, encoding="utf-8") as f:
            for rec in all_records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # page_map：合并页码映射（合并成单一 PDF 时按 merged_start/end 顺序拼接即可对齐）
    pageroot = {
        "generated_at": report["run_at"],
        "note": "merged_* 为「按 source_file 排序合并成单一 PDF」时的全局页码区间；"
                "对外展示引用时请一律换算回原文件的 page_no / page_label。",
        "files": files_meta,
    }
    PAGEMAP_FILE.write_text(json.dumps(pageroot, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 62)
    print(f"新增/更新文件 : {report['parsed_files']}")
    print(f"跳过（未变更）: {report['skipped_files']}")
    print(f"失败         : {len(report['failed_files'])}")
    print(f"总页数       : {report['total_pages']}（其中扫描页 {report['scan_pages']}、全空页 {report['empty_pages']}）")
    print(f"输出         : {PAGES_FILE}")
    print(f"             {PAGEMAP_FILE}")
    print(f"             {REPORT_FILE}")
    if report["failed_files"]:
        print("\n失败清单：")
        for item in report["failed_files"]:
            print(f"  - {item['file']}  ({item['reason']})")
    if report["scan_pages"] or report["empty_pages"]:
        print(f"\n提示：扫描页/空页清单见 {LOG_DIR / 'empty_pages.txt'}，这些页需要 OCR 或人工补录。")
    print("=" * 62)
    # 有失败文件就返回非 0：本项目一贯 fail-loud，而"打印了失败清单、退出码却是 0"
    # 是这套哲学里最别扭的一处 —— 脚本与 CI 无法据此判断，只能靠人读输出。
    if report["failed_files"]:
        print(f"\n[FAIL] {len(report['failed_files'])} 个文件没能解析（清单见上）；"
              f"其余文件的结果已正常写入。")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
