# -*- coding: utf-8 -*-
"""
kb.py —— FireKB 共享核心库
============================
被结构化抽取、引用校验、复习产物、状态查看等模块共用。
只提供能力，不产生副作用（写操作集中在各模块脚本里）。

核心能力：
  1. 路径与目录常量（可用 FIREKB_ROOT 指向另一处数据根）
  2. 课程配置读取与课程名归一（教材目录名 / 文件名 -> 课程）
  3. 契约文件读写（JSONL 追加 / 读取 / 重写）
  4. 章节结构识别（中英文教材都支持）
  5. 文本切块（带页码引用，控制单次调用 token 量）
  6. 引用核验（归一化 + 三档判定，抽取与复核共用同一套实现）
  7. 断点续跑状态（state.json，中断后接着跑；键管身份、指纹管版本）
  8. LLM 统一调用封装（含重试、JSON 容错、成本统计）
  9. 运行标识与结构化运行日志（run_id / runs.jsonl）
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(os.environ.get("FIREKB_ROOT") or Path(__file__).resolve().parents[1])
CONFIG_DIR = ROOT / "00_配置"
ARCHIVE = ROOT / "10_原始归档"
TEXT_DIR = ROOT / "20_文本库"
KP_DIR = ROOT / "30_知识点"
REVIEW_DIR = ROOT / "40_复习产物"
LOG_DIR = ROOT / "90_日志"

PAGES_FILE = TEXT_DIR / "pages.jsonl"
TRANSCRIPT_FILE = TEXT_DIR / "transcript.jsonl"
KP_FILE = KP_DIR / "kp.jsonl"
EVIDENCE_FILE = KP_DIR / "evidence.jsonl"
AUDIT_FILE = KP_DIR / "audit.jsonl"
SKELETON_FILE = KP_DIR / "skeleton.json"
STATE_FILE = KP_DIR / "state.json"
CARDS_FILE = REVIEW_DIR / "cards.jsonl"

# ---- 切块长度常量（**两个用途，名字必须说清用在哪条路径**）----
# 这里有两条切块路径，默认长度不同、名字相近但值不一样：
#   · 无骨架课程的退化路径用 `CHUNK_MAX_CHARS`（见 kb.build_chunks）；
#   · 生产抽取路径用 `CHUNK_EXTRACT_MAX_CHARS`（见 extract_kp.chunks_from_skeleton）。
# 复算时必须按被复算的那条路径取数：拿错常量会算出不同的块数，从而得出
# 「文档里的块数对不上、不可复现」这类**错误结论**。
# 因此两条路径的默认值都放在这里，名字里带 EXTRACT 的那个才是抽取路径用的。
CHUNK_MAX_CHARS = 12000          # kb.build_chunks 的默认值 —— **无骨架课程的退化路径**
CHUNK_EXTRACT_MAX_CHARS = 11000  # extract_kp.chunks_from_skeleton 的默认值 —— **生产抽取路径**
CHUNK_MIN_CHARS = 250            # 太小的块直接并入上一块


# ---------------------------------------------------------------------------
# 基础 IO
# ---------------------------------------------------------------------------
def ensure_dirs() -> None:
    for d in (CONFIG_DIR, ARCHIVE, TEXT_DIR, KP_DIR, REVIEW_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_jsonl(path: Path) -> list[dict]:
    """
    读 JSONL。容错：缺失/空行/坏行都跳过，不抛异常。

    ⚠️ 编码用 `utf-8-sig` 而不是 `utf-8`：
    带 BOM 的文件用 `utf-8` 读时，**首行**会变成 `\\ufeff{...}` ⇒ `json.loads` 失败 ⇒
    该行被"坏行跳过"逻辑**静默丢掉**；若整份文件只有一行（常见于配置/小样本产物），
    读出来就是 `[]` —— 工具会显示"0 条"，**不报错**。这种静默失败最难排查。
    BOM 的现实来源：Windows PowerShell 5.1 的 `Set-Content -Encoding UTF8` 会写 BOM。
    `utf-8-sig` 对**无 BOM** 文件的行为与 `utf-8` 完全一致，所以这是纯增益的选择。
    """
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def append_jsonl(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def rewrite_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path, default=None):
    """读 JSON 配置。⚠️ 同样用 `utf-8-sig`：带 BOM 的配置文件用 `utf-8` 读会解析失败，
    然后被这里的 `except` **静默吞掉并返回 default** —— 表现是"配置没生效"而不是报错。"""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


# ---------------------------------------------------------------------------
# 课程
# ---------------------------------------------------------------------------
def load_courses() -> list[dict]:
    data = load_json(CONFIG_DIR / "courses.json", {})
    return data.get("courses", []) if isinstance(data, dict) else []


def course_index() -> dict[str, dict]:
    """课程名（含常见变体）-> 课程配置。用于把目录名映射到课程。"""
    idx: dict[str, dict] = {}
    for c in load_courses():
        name = c.get("name", "")
        if not name:
            continue
        idx[name] = c
        idx[name.lower()] = c
        idx[re.sub(r"\s+", "", name)] = c
        idx[re.sub(r"\s+", "", name).lower()] = c
        if c.get("id"):
            idx[c["id"]] = c
    return idx


def match_course(*candidates: str) -> dict | None:
    """
    按候选字符串依次匹配课程配置。
    先精确匹配，再包含匹配（处理「消防给水排水工程」这类教材全名与课程名不完全一致的情况）。
    """
    idx = course_index()
    for cand in candidates:
        if not cand:
            continue
        key = re.sub(r"\s+", "", cand)
        if key in idx:
            return idx[key]
        if key.lower() in idx:
            return idx[key.lower()]
    for cand in candidates:
        if not cand:
            continue
        key = re.sub(r"\s+", "", cand).lower()
        for name, cfg in idx.items():
            n = re.sub(r"\s+", "", name).lower()
            if len(n) >= 3 and (n in key or key in n):
                return cfg
    return None


# ---------------------------------------------------------------------------
# 规范条文库
# ---------------------------------------------------------------------------
# 为什么要有这一层：`通用规范` 若以「一门课」的身份存在，它其实是 **4 本规范的合集**
# （建筑防火通用规范 / 消防设施通用规范实施指南 / 建筑设计防火规范 / 消防法）。
# 后果有二：
#   ① 口径上它不在 courses.json，于是 consistency 只能报「course_id 靠猜」；
#   ② 每本书的页码都从 1 重编，合成一个页空间时页范围是无意义的 —— 页空间一旦合并，
#      大量页会被静默丢弃（见 `kb.build_chunks` 的跨文件边界守卫）。
# 所以这里的口径是拆开的：课表只谈课，规范条文库只谈规范，逐本独立成条目。
#
# ⚠️ 复算须知：**生产切块路径是逐文件分组的**
#    （`extract_kp.chunks_from_skeleton` 的 `by_file`）。用整门课直接调 `kb.build_chunks`
#    会得到若干"跨作品块"（页范围倒挂如 `p561-5`），那是**探针口径错误造成的假阳性**，
#    不是生产路径的真实产出。
#    见 selftest 的 `no_chunk_spans_two_files`（用生产路径复算，锁住这条事实）。
def load_references() -> dict:
    """读 00_配置/references.json；缺文件时返回空结构（不报错，保持向后兼容）。"""
    data = load_json(CONFIG_DIR / "references.json", {})
    return data if isinstance(data, dict) else {}


def reference_works() -> list[dict]:
    return [w for w in (load_references().get("works") or []) if isinstance(w, dict)]


def reference_library() -> dict:
    return load_references().get("library") or {}


def reference_duplicates() -> list[dict]:
    return [d for d in (load_references().get("duplicates") or []) if isinstance(d, dict)]


def reference_course_dirs() -> set[str]:
    """
    规范条文库占用的「素材目录名」集合（= 这些目录名**不是课程**）。

    用途：`通用规范` 在 pages.jsonl 里仍然以 course='通用规范' 出现（数据本身不动），
    课表口径要把这些名字当成「已归入条文库」而不是「身份不明的课」。
    """
    lib = reference_library()
    out = {str(lib.get("course_dir"))} if lib.get("course_dir") else set()
    return out


def resolve_reference(source_file: str | None) -> dict | None:
    """
    把一条页记录的 `source_file` 归到某一本规范（失败返回 None）。

    匹配规则：works[].match 是 source_file 的子串。**按 match 长度降序**试，
    避免短关键词抢走长关键词（例：`建筑防火通用规范` 与 `建筑设计防火规范` 都含"防火规范"，
    若按声明顺序匹配，泛化程度不同的两条会互相截胡）。
    """
    if not source_file:
        return None
    rel = str(source_file)
    for w in sorted(reference_works(), key=lambda x: -len(str(x.get("match") or ""))):
        m = str(w.get("match") or "")
        if m and m in rel:
            return w
    return None


def is_reference_source(source_file: str | None) -> bool:
    """该 source_file 是否属于规范条文库（比 resolve_reference 宽松：用于口径归类）。"""
    if resolve_reference(source_file):
        return True
    rel = str(source_file or "")
    for d in reference_course_dirs():
        if rel.startswith(f"规范教材/{d}/") or rel.startswith(f"课件/{d}/"):
            return True
    return False


# ---------------------------------------------------------------------------
# 实验批次登记
# ---------------------------------------------------------------------------
# 有些知识点来自**探测性抽取**（试跑一个块看链路通不通），不是正式产出。
# 它们混在 kp.jsonl 里、status 也是 verified ⇒ 会静默流进复习产物（例如流进背诵卡）。
# 处理方式是**保留数据、如实标注**，而不是删数据：
# 这里提供登记读取与命中判定，供 make_review / consistency 用。
#
# 按 (course AND kp_id_prefix AND created_at) 匹配，不逐个写死 kp_id：
# 重跑会生成新 kp_id，写死清单立刻失效。
def load_experimental_batches() -> list[dict]:
    data = load_json(CONFIG_DIR / "experimental-batches.json", {})
    batches = (data or {}).get("batches") if isinstance(data, dict) else None
    return [b for b in (batches or []) if isinstance(b, dict)]


def is_experimental_kp(row: dict) -> dict | None:
    """该知识点是否属于某个已登记的实验批次；返回命中的批次（None = 不是）。"""
    for b in load_experimental_batches():
        course = b.get("course")
        if course and (row.get("course") or "") != course:
            continue
        pref = b.get("kp_id_prefix")
        if pref and not str(row.get("kp_id") or "").startswith(str(pref)):
            continue
        created = b.get("created_at")
        if created and (row.get("created_at") or "") != created:
            continue
        return b
    return None


def experimental_kp_ids(rows: list[dict] | None = None) -> set[str]:
    """已登记实验批次的 kp_id 集合（rows 省略时读 kp.jsonl）。"""
    rows = load_jsonl(KP_FILE) if rows is None else rows
    return {r.get("kp_id") for r in rows if is_experimental_kp(r) and r.get("kp_id")}


# ---------------------------------------------------------------------------
# 页码双轨：PDF 物理页 ↔ 书上印刷页
# ---------------------------------------------------------------------------
# 为什么需要：`page_no` / `page_label` 是 **PDF 物理页**（第几张），而书上印的是**印刷页码**。
# 同一门课的偏移通常不为 0，且**每门课不同**（各书的封面、前言、目录页数不一样）。
# 直接拿物理页号当印刷页引用，人和机器会指到不同的地方 —— 这正是「结论要落到文件+页码」的漏洞。
#
# 方向约定（**只此一种，勿再引入第二种**）：印刷页 = 物理页 + offset，即 offset = 印刷页 - 物理页。
# 配置真相在 `00_配置/page-offsets.json`，由 `tools/page_offsets.py` 探测/人工登记。
def load_page_offsets() -> dict:
    return load_json(CONFIG_DIR / "page-offsets.json", {}) or {}


def page_offset(course: str, file_id: str | None = None) -> int | None:
    """
    取 offset（印刷 - 物理）。找不到返回 None（**不要当 0 用** —— 那等于假装算出来了）。

    优先级：文件级 manual > 文件级 auto > 课程级 manual > 课程级 auto。
    文件级优先是因为同一课程目录下各本书的 offset 可能各不相同，课程级数字对它们无意义。
    """
    info = ((load_page_offsets().get("courses") or {}).get(course)) or {}
    files = info.get("files") or {}
    f = files.get(file_id) if file_id else None
    if f and f.get("source") == "manual" and isinstance(f.get("offset"), int):
        return f["offset"]
    if f and f.get("source") == "auto" and isinstance(f.get("offset"), int):
        return f["offset"]
    if info.get("source") in ("manual", "auto") and isinstance(info.get("offset"), int):
        return info["offset"]
    return None


def page_map(course: str, file_id: str | None = None) -> dict | None:
    """
    取该 (课程, 文件) 的页码映射规则。两种形态：

      {"kind": "offset",  "offset": -17}                    ← 线性：印刷 = 物理 + offset
      {"kind": "anchors", "anchors": [(4,11),(7,21)], ...}   ← 锚点：分段线性插值

    为什么需要「锚点」这一种：EPUB 教材的 page_no 是**节序号**（标签为「第N节」），
    而**节与书页不是等长**的 —— 例如某书的绪论（第 4 节）在书 p11、
    模块一（第 7 节）在书 p21，节号差 3 而书页差 10。
    ⇒ 单一 offset 在数学上不成立，必须用锚点插值。

    返回 None = 没有该文件的映射规则（**不要退回物理页号**）。
    """
    info = ((load_page_offsets().get("courses") or {}).get(course)) or {}
    files = info.get("files") or {}
    f = files.get(file_id) if file_id else None
    for cand in (f, info):
        if not cand:
            continue
        a = cand.get("anchors")
        if a:
            pts = sorted((int(x["index"]), int(x["printed"])) for x in a
                         if x.get("index") is not None and x.get("printed") is not None)
            if len(pts) >= 2:
                return {"kind": "anchors", "anchors": pts,
                        "lo": pts[0][0], "hi": pts[-1][0],
                        "note": cand.get("note")}
        if isinstance(cand.get("offset"), int) and cand.get("source") in ("manual", "auto"):
            return {"kind": "offset", "offset": cand["offset"]}
    return None


def printed_page_no(course: str, page_no, file_id: str | None = None):
    """
    页内位置（PDF 物理页 / EPUB 节序号）→ 书上印刷页。

    没有映射规则时返回 None（**不要退回物理页号**，那会被误当成印刷页）。
    锚点模式在锚点区间**外**会外推 —— 调用方应配合 `is_extrapolated()` 标注"推算"。
    """
    if not isinstance(page_no, int):
        return None
    m = page_map(course, file_id)
    if not m:
        return None
    if m["kind"] == "offset":
        return page_no + m["offset"]
    pts = m["anchors"]
    for (i0, p0), (i1, p1) in zip(pts, pts[1:]):
        if i0 <= page_no <= i1:
            if i1 == i0:
                return p1
            frac = (page_no - i0) / (i1 - i0)
            return round(p0 + frac * (p1 - p0))
    # 区间外：按最近的一段斜率外推
    if page_no < pts[0][0]:
        (i0, p0), (i1, p1) = pts[0], pts[1]
    else:
        (i0, p0), (i1, p1) = pts[-2], pts[-1]
    frac = (page_no - i0) / (i1 - i0)
    return round(p0 + frac * (p1 - p0))


def is_extrapolated(course: str, page_no, file_id: str | None = None) -> bool:
    """该查询是否落在锚点区间之外（= 印刷页是**推算**的，不在锚点覆盖范围内）。"""
    m = page_map(course, file_id)
    if not m or m["kind"] != "anchors" or not isinstance(page_no, int):
        return False
    return not (m["lo"] <= page_no <= m["hi"])


def page_ref(course: str, page_no, file_id: str | None = None, label: str | None = None) -> str:
    """
    给人看的双轨页码引用，例如 `书p294（PDF p311）`。

    三种形态（**都显式标注，不让人猜**）：
      · 有 offset         → `书p294（PDF p311）`
      · 锚点区间内         → `书p11（PDF 第4节）`
      · 锚点区间外（外推） → `书p≈18（PDF 第10节，推算）`
      · 无映射规则         → `PDF p8`（**绝不冒充印刷页**）
    """
    src = label or (f"p{page_no}" if page_no is not None else "?")
    printed = printed_page_no(course, page_no, file_id)
    if printed is None:
        return f"PDF {src}"
    if is_extrapolated(course, page_no, file_id):
        return f"书p≈{printed}（PDF {src}，推算）"
    return f"书p{printed}（PDF {src}）"


# ---------------------------------------------------------------------------
# 章节识别（中英文教材通用）
# ---------------------------------------------------------------------------
_CN = "零一二三四五六七八九十百千0-9"
_SECTION_PATTERNS = [
    (re.compile(rf"^第\s*([{_CN}]{{1,5}})\s*章[\s　:：\.]*(.{{0,45}})$"), "章"),
    (re.compile(rf"^第\s*([{_CN}]{{1,5}})\s*节[\s　:：\.]*(.{{0,45}})$"), "节"),
    (re.compile(r"^Chapter\s+([0-9]{1,2})\b[\s:：\.]*(.{0,45})$", re.I), "章"),
    (re.compile(r"^([0-9]{1,2}\.[0-9]{1,2})[\s　]+(\S.{0,45})$"), "节"),
    (re.compile(rf"^([{_CN}]{{1,5}})\s*[、\.]\s*(.{{2,40}})$"), "小节"),
]


def detect_section(text: str) -> tuple[str, str] | None:
    """
    从一页文本里识别章节标题。
    返回 (级别, 标题原文)，识别不到返回 None。
    只看每页前 400 字符，避免正文里的引用被误判。
    """
    if not text:
        return None
    head = "\n".join(text.split("\n")[:12])[:400]
    for pat, level in _SECTION_PATTERNS:
        for line in head.split("\n"):
            line = line.strip()
            if not line or len(line) > 60:
                continue
            m = pat.match(line)
            if m:
                title = line.strip()
                if level == "章":
                    return "章", title
                if level == "节":
                    return "节", title
                return "小节", title
    return None


# ---------------------------------------------------------------------------
# 切块（带页码引用）
# ---------------------------------------------------------------------------
def build_chunks(pages: list[dict], source_type: str = "textbook",
                 max_chars: int = CHUNK_MAX_CHARS) -> list[dict]:
    """
    把页级记录按「章节 + 长度」聚合成块。
    每个块保留原始页码范围，这是后续所有引用的根基。

    ⚠️ **一个块绝不跨文件**（边界守卫）。
    为什么必须写死这条：每本书的 `page_no` **各自从 1 重编**，所以一旦一个块吃了两本书的页，
    它的 `page_start..page_end` 就是无意义的（会产出 `p561-5` 这种倒挂范围），
    而 chunk_id 里的文件标签只指向**第一本** ⇒ 引用落点会对到错误的文件。
    把多本书的页空间合并成一个键空间，也会以同样的方式静默产出坏引用。

    ⚠️ 生产抽取路径（`extract_kp.chunks_from_skeleton`）**本就是逐文件**调本函数的
    （见其 `by_file` 分组），所以这条守卫在生产路径上是**空操作**、不改变任何既有 chunk_id。
    它防的是**将来有人**图省事把整门课的页一次性喂进来 —— 那时它会静默产出坏引用，
    而不是报错。宁可在这里多切一刀。
    """
    chunks: list[dict] = []
    cur: dict | None = None

    def _file_of(pg: dict):
        # file_id 优先（同一本书改名不失联），缺失时退回 source_file
        return pg.get("file_id") or pg.get("source_file")

    for pg in pages:
        if not (pg.get("text") or "").strip():
            continue
        sec = detect_section(pg.get("text", ""))
        sec_level, sec_title = sec if sec else (None, None)

        need_new = (
            cur is None
            or (sec_level == "章" and sec_title != cur.get("chapter"))
            or cur["chars"] + pg.get("char_count", 0) > max_chars
            or _file_of(pg) != cur.get("_file")
        )
        if need_new:
            if cur and cur["chars"] >= CHUNK_MIN_CHARS:
                chunks.append(cur)
            cur = {
                "course": pg.get("course", "未分类"),
                "source_type": source_type,
                "source_file": pg.get("source_file"),
                "file_id": pg.get("file_id"),
                "_file": _file_of(pg),   # 仅供边界守卫比对，输出前丢弃（见下）
                "chapter": sec_title if sec_level == "章" else (cur.get("chapter") if cur else None),
                "section": sec_title if sec_level in ("节", "小节") else None,
                "page_start": pg.get("page_no"),
                "page_end": pg.get("page_no"),
                "page_label_start": pg.get("page_label"),
                "page_label_end": pg.get("page_label"),
                "chars": 0,
                "text_parts": [],
            }
        elif sec_title and sec_level in ("节", "小节"):
            cur["section"] = sec_title

        cur["page_end"] = pg.get("page_no")
        cur["page_label_end"] = pg.get("page_label")
        cur["chars"] += pg.get("char_count", 0)
        cur["text_parts"].append(f"【{pg.get('page_label')}】\n{pg.get('text', '').strip()}")

    if cur and cur["chars"] >= CHUNK_MIN_CHARS:
        chunks.append(cur)

    out: list[dict] = []
    for c in chunks:
        text = "\n\n".join(c.pop("text_parts"))
        c.pop("_file", None)             # 内部字段，不进产物
        c["chars"] = len(text)
        c["text"] = text
        out.append(c)
    return assign_chunk_ids(out)


def pages_of_course(course_name: str) -> list[dict]:
    pages = load_jsonl(PAGES_FILE)
    return [p for p in pages if p.get("course") == course_name]


def source_type_of(rel_path: str) -> str:
    if rel_path.startswith("课件"):
        return "courseware"
    if rel_path.startswith("规范教材"):
        return "textbook"
    return "other"


def group_pages_by_course() -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for p in load_jsonl(PAGES_FILE):
        groups.setdefault(p.get("course", "未分类"), []).append(p)
    for c in groups:
        groups[c].sort(key=lambda r: (r.get("source_file", ""), r.get("page_no", 0)))
    return groups


# ---------------------------------------------------------------------------
# 引用核验（共享实现，extract_kp / verify_kp 都走这里，避免两套漂移）
# ---------------------------------------------------------------------------
# 档位语义（三档 + 未命中）：
#   exact      完整片段逐字命中 —— **唯一算「通过」的档位**
#   partial    头部与尾部都命中、中段有落差 —— 落人工复核队列，**不计入通过率**
#   fabricated 仅前缀（或仅尾部）命中 —— 疑似「头真尾假」，必须拦下并回传 matchedText
#   not_found  连前缀都找不到
VERIFY_EXACT = "exact"
VERIFY_PARTIAL = "partial"
VERIFY_FABRICATED = "fabricated"
VERIFY_NOT_FOUND = "not_found"

_NUM_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?")
_PUA_RE = re.compile(r"[\ue000-\uf8ff]")
_LINEBREAK_HYPHEN_RE = re.compile(r"(?<=[a-z0-9])-(?=[a-z0-9])")
_PUNCT_MAP = (("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'"),
              ("—", "-"), ("–", "-"), ("−", "-"), ("．", "."),
              ("，", ","), ("；", ";"), ("：", ":"), ("（", "("), ("）", ")"))


def normalize_for_match(s: str) -> str:
    """
    归一化用于引用校验：去空白、统一大小写、统一常见标点、折叠合字与兼容字符。

    为对抗 OCR 噪声，这里对文本做四处处理：
      ① PUA 私有区字符（U+E000–U+F8FF）：OCR 把无法映射的字形塞进私有区，
         不剔除则引用永远匹配不上。
      ② 行末断词连字符：OCR 把行末断行的英文单词写成 `convec-\\ntion`，
         去空白后留下 `convec-tion`；去掉「字母/数字之间的连字符」使两侧重新拼合。
    ⚠️ 顺序：先剔 PUA 再拆连字符。这样 `abc<U+E000>-def` 这类「PUA 顶掉连字符」
    的写法也能拼回 `abcdef`；反过来（先拆连字符）则拼不回。
    两种顺序在多数样本上结果相同，差异只在上述边界写法上体现；
    这里取更强的顺序，以免边界样本漏拼。

    ③ NFKC + ④ 去组合符号 —— 这两步针对一类**系统性误判**：
      症状：整门课的引用判定精确率明显偏低，大量知识点卡在待复核队列，
            一度被怀疑是"英文分词 / 公式层 / OCR 噪声"。
      真相：**PDF 合字**：某些教材的 PDF 文本层用 `ﬁ`（U+FB01）、`ﬂ`（U+FB02）等**单字符合字**，
            而模型在引用里写成普通 `fi`/`fl` ⇒ 逐字比对必然失败 ⇒ 被判 fabricated/not_found。
            **不是编造，是归一化缺了一步。**
      调参对比（对全库 evidence 用 `verify_kp.recheck_evidence` 只判定、不写盘复算；
            基线一档与磁盘现存分布逐档相同，说明两次比较的口径一致）：

        | 归一化                        | exact | 升级 | 降级 |
        |---|---|---:|---:|---:|
        | 仅 ①②（不含 NFKC）           | 8328 |    0 |    0 |
        | +NFKC                        | 8553 |  225 |    0 |
        | +NFKC+去组合符号（**采纳**）   | 8565 |  237 |    0 |
        | +再去掉所有标点（**未采纳**）  | 8708 |  380 |    0 |

      ⚠️ **为什么明确不采纳"去掉所有标点"那一档**（虽然它的通过数更高）：
        本项目的红线是**数字必须逐字命中**（见 verify_quote 的严格通道：`1.5` 与 `15`
        必须区分）。把标点全部丢掉会让 `1.5` 与 `15` 归一化成同一个串 ——
        用"看起来更准的通过率"换掉"数字不能被蒙过去"，是拿红线换指标，不做。
      ⚠️ 该函数**不参与 `chunk_fingerprint`**（后者自己 `re.sub(r"\\s+","",text)`），
        所以改这里**不会**让块的内容指纹变化、**不会**触发重抽。
    """
    if not s:
        return ""
    # ③ NFKC：合字 ﬁ→fi、全角→半角、上标/兼容字符折叠
    s = unicodedata.normalize("NFKC", s)
    # ④ 去掉组合附加符号：公式里的 Q̇（Q + U+0307）与 Q 应视为同一字符
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = re.sub(r"\s+", "", s)
    s = _PUA_RE.sub("", s)                      # ① OCR PUA 私有区字符
    s = _LINEBREAK_HYPHEN_RE.sub("", s)         # ② OCR 行末断词连字符
    for a, b in _PUNCT_MAP:
        s = s.replace(a, b)
    return s


def _longest_prefix_hit(q: str, b: str) -> str:
    """返回在原文中命中的最长前缀（24/16/12 字符），都没有则回退到尾部命中片段。"""
    for n in (24, 16, 12):
        if len(q) >= n and q[:n] in b:
            return q[:n]
    for n in (24, 16, 12):
        if len(q) >= n and q[-n:] in b:
            return q[-n:]
    return ""


def verify_quote(quote: str, body: str, strict_numbers: bool = True) -> tuple[str, str, str]:
    """
    校验引用是否真的存在于原文中。

    返回 (tier, matched_text, note)：
      tier         —— 见上方档位语义（exact / partial / fabricated / not_found）
      matched_text —— 命中片段（供人工比对），未命中为空串
      note         —— 判定依据（可直接落盘给人看）

    三条设计要点：
      · **尾部校验**在 exact 之后、前缀回退之前执行：要求 q[-24:] 也命中原文。
        只查头不查尾（`q in b` 失败就退 `q[:24]`/`q[:16]`），
        无法发现「头部真实 + 尾部虚构」——那是防幻觉闸门上的一个洞。
      · 返回值用**三档**而非布尔：exact 才算通过；partial 进人工复核队列
        （计档位、不计通过率）；fabricated 必须拦下。
      · **含数字的引用走独立严格通道**：数字片段必须逐字命中原文，
        任一数字对不上即判 fabricated，**禁止退到前缀宽容路径**
        （消防专业里「0.15h」被改成「1.50h」是头真尾假最危险的形态）。
    """
    q = normalize_for_match(quote)
    b = normalize_for_match(body)
    if not q:
        return VERIFY_NOT_FOUND, "", "空引用"
    if not b:
        return VERIFY_NOT_FOUND, "", "原文为空"
    if q in b:
        return VERIFY_EXACT, q, "exact"

    # ③ 尾部校验：头尾都命中才算 partial
    head_hit = len(q) >= 12 and q[:24] in b
    tail_hit = len(q) >= 12 and q[-24:] in b

    # ⑤ 含数字引用：严格通道，不走前缀宽容
    if strict_numbers:
        missing = [n for n in _NUM_TOKEN_RE.findall(q) if n not in b]
        if missing:
            where = "、".join(missing[:4])
            if head_hit or tail_hit:
                return (VERIFY_FABRICATED, _longest_prefix_hit(q, b),
                        f"数字未逐字命中原文（{where}）——按严格通道拦下，不走前缀宽容")
            return VERIFY_NOT_FOUND, "", f"数字与文本均未命中原文（数字：{where}）"

    if head_hit and tail_hit:
        return VERIFY_PARTIAL, _longest_prefix_hit(q, b), "头尾均命中，中段有落差"
    if head_hit:
        return (VERIFY_FABRICATED, _longest_prefix_hit(q, b),
                "仅头部命中、尾部未命中（疑似头真尾假）")
    if tail_hit:
        return (VERIFY_FABRICATED, _longest_prefix_hit(q, b),
                "仅尾部命中、头部未命中（疑似拼接）")
    return VERIFY_NOT_FOUND, "", "not_found"


def quote_passes(tier: str) -> bool:
    """唯一算「通过」的档位：exact。partial 只进人工复核队列，不计通过率。"""
    return tier == VERIFY_EXACT


def confidence_from_tiers(tiers) -> str:
    """
    由证据档位聚合出知识点的 `confidence`（与抽取时的规则同源，供 `--recheck` 复用）。

    规则：全部 `exact` → `A`；部分 `exact` → `B`；无 `exact`（含 0 条证据）→ `C`。

    入参**两种都接受**（两个调用点的数据形态本就不同，不做兼容会静默算错）：
      * 档位字符串序列（如 `["exact", "partial"]`）—— `verify_kp --recheck` 回填侧；
      * 布尔命中标记序列（如 `[True, False]`）—— `extract_kp.extract_chunk` 抽取侧传的是
        `verified_flags`（每条形如 `ok = quote_passes(tier)`）。
    ⚠️ 只按 `t == VERIFY_EXACT` 判定会把布尔 `True` 一律算作未命中（`True == "exact"` 为 False），
       新抽的知识点会**全部被标成 C** —— 且不报错。这里必须显式分流。

    为什么必须能重算：归一化规则一旦增强，「本该 exact 的引用」会从回退档升为 `exact`，
    若不重算，知识点会一直挂着**抽取时刻**的旧档，
    成为「字段存在 ≠ 语义成立」型的陈旧字段。
    """
    tiers = list(tiers or [])
    if not tiers:
        return "C"

    def _hit(t) -> bool:
        return quote_passes(t) if isinstance(t, str) else bool(t)

    passed = sum(1 for t in tiers if _hit(t))
    if passed == len(tiers):
        return "A"
    if passed:
        return "B"
    return "C"


# ---------------------------------------------------------------------------
# 主键与内容指纹
# ---------------------------------------------------------------------------
def make_file_tag(file_id) -> str:
    """
    文件的稳定短标签，用于 chunk 主键标识「这是哪一本书的页」。

    为什么必须进主键：同一门课下可以挂多本教材（页码各自从 1 重编），
    只按页号做键必然撞车。

    形态 `{可读前缀28字}-{file_id 的 md5 前 8 位}`：
      * 可读前缀便于人肉排障；
      * 末尾 8 位哈希保证「前缀被截断 / 文件名相似」时仍唯一。
    判据（`parse_chunk_key_full` 的 tag 正则）盯死末尾 `-[0-9a-f]{8}`，
    因此旧格式里以 f 开头的章名（如 `Fire Science`）不会被误认成标签。
    """
    if not file_id:
        return ""
    raw = str(file_id)
    slug = re.sub(r"[^0-9A-Za-z_.\-]+", "_", raw).strip("_") or "f"
    return f"{slug[:28]}-{hashlib.md5(raw.encode('utf-8')).hexdigest()[:8]}"


def make_chunk_key(course: str, page_start, page_end, file_tag: str | None = None) -> str:
    """
    chunk 主键 = `{course}::f{file_tag}::p{page_start}-{page_end}`（内容/位置派生，
    **不含位置序号**）；`file_tag` 缺省时退化为中格式 `{course}::pA-B`。

    为什么不含末段的位置序号 `{i:04d}`：位置序号随骨架重建整体漂移，
    会让全部 state 键一次性失效、断点续跑形同虚设，并导致已抽过的块被重抽
    （知识点数量与调用成本成倍放大）。

    键里的 `f{file_tag}` 段（见 `make_file_tag`）不可省：一门课多本书时页号不再唯一，
    键必须能区分「哪本书的第 30-31 页」。
    """
    if file_tag:
        return f"{course}::f{file_tag}::p{page_start}-{page_end}"
    return f"{course}::p{page_start}-{page_end}"


def parse_chunk_key(key: str) -> tuple[str, int, int] | None:
    """
    解析 chunk 主键，返回 (course, page_start, page_end)（**忽略文件标签**，兼容旧调用方）。
    能解析 `course::fTAG::p30-31`、`course::p30-31`、`course::p30-31::h1a2b`
    与旧格式 `course::章::节::p30-31::0000`。解析不出返回 None。
    """
    full = parse_chunk_key_full(key)
    if full is None:
        return None
    course, ps, pe, _tag = full
    return course, ps, pe


def parse_chunk_key_full(key: str) -> tuple[str, int, int, str | None] | None:
    """
    解析 chunk 主键，返回 **(course, page_start, page_end, file_tag)**。

    ⚠️ 返回顺序**刻意与 `make_chunk_key(course, page_start, page_end, file_tag)` 一致**，
    这样 `make_chunk_key(*parse_chunk_key_full(k)) == k` 成立（往返不变式）。
    （顺序不一致时，`make_chunk_key(*parsed)` 会把标签错位成页码 ——
    返回顺序与构造函数参数顺序对齐，才算不会走火的接口。）

    file_tag 判据：段形如 `f{...}-[0-9a-f]{8}`（见 `make_file_tag`）；
    因此旧格式里的章/节名（如 `Fire Science`）不会被误认成标签。
    """
    parts = str(key or "").split("::")
    if len(parts) < 2:
        return None
    tag: str | None = None
    for seg in parts[1:]:
        s = seg.strip()
        if tag is None:
            mtag = re.fullmatch(r"f([0-9A-Za-z_.\-]+-[0-9a-f]{8})", s)
            if mtag:
                tag = mtag.group(1)
                continue
        m = re.fullmatch(r"p(\d+)-(\d+)", s)
        if m:
            return parts[0], int(m.group(1)), int(m.group(2)), tag
    return None


def is_legacy_chunk_key(key: str) -> bool:
    """
    旧格式 = 以**位置序号末段**结尾，即 `{course}::章::节::pA-B::0000`。

    ⚠️ 不能用 `count("::") > 1` 判断：新格式的消歧后缀
    `{course}::p30-31::h1a2b` 恰好也含 2 个 `::`，会被误判成旧键（假阳性）。
    所以判据盯死「末段是否为 4 位纯数字」。
    """
    return bool(re.fullmatch(r".+::p\d+-\d+::\d{4}", str(key or "")))


def chunk_fingerprint(chunk: dict) -> str:
    """
    内容指纹 = **页范围 + 字符数 + 首 32 字哈希**。

    键与指纹职责分离（不互相替代）：
      * 键（chunk_id）管**身份**——它变了（如骨架重建改了章名）仍能靠指纹认出同一段内容；
      * 指纹管**版本**——内容变了（页范围 / 长度 / 开头变了）指纹就不同，必须重抽。
    """
    text = chunk.get("text") or ""
    flat = re.sub(r"\s+", "", text)
    head = flat[:32]
    h = hashlib.md5(head.encode("utf-8")).hexdigest()[:8]
    chars = chunk.get("chars")
    if chars is None:
        chars = len(text)
    return f"p{chunk.get('page_start')}-{chunk.get('page_end')}|c{chars}|h{h}"


def assign_chunk_ids(chunks: list[dict]) -> list[dict]:
    """
    给块赋内容派生主键，并**硬断言主键全局唯一**。

    碰撞时先用内容派生后缀 `::h{md5(首32字)[:4]}` 消歧（loud 告警）；
    消歧后仍碰撞则直接抛错——**必须 fail loudly**，因为重复主键会让断点续跑与
    知识点去重同时失效，且是「不报错的静默错误」。

    赋键时带上**文件标签**（`make_file_tag`）：生成侧
    （`extract_kp.chunks_from_skeleton`）已保证同一文件内页码归属唯一，
    正常情况下本函数不会再触发消歧分支；保留它作为兜底。
    """
    for c in chunks:
        tag = make_file_tag(c.get("file_id")) or None
        c["chunk_id"] = make_chunk_key(c.get("course"), c.get("page_start"), c.get("page_end"), tag)

    def _groups() -> dict[str, list[dict]]:
        g: dict[str, list[dict]] = {}
        for c in chunks:
            g.setdefault(c["chunk_id"], []).append(c)
        return g

    collide = {k: v for k, v in _groups().items() if len(v) > 1}
    if collide:
        print(f"[WARN] chunk 主键碰撞 {len(collide)} 组（课程+页范围相同），"
              f"按内容派生后缀消歧：{list(collide)[:3]}", file=sys.stderr)
        for key, group in collide.items():
            for c in group:
                head = re.sub(r"\s+", "", c.get("text") or "")[:32]
                h = hashlib.md5(head.encode("utf-8")).hexdigest()[:4]
                c["chunk_id"] = f"{key}::h{h}"
        still = {k: len(v) for k, v in _groups().items() if len(v) > 1}
        if still:
            raise RuntimeError(
                f"chunk 主键消歧后仍不唯一，拒绝继续（重复主键会污染断点续跑与去重）：{still}")
    return chunks


def migrate_state_keys(state: "State", chunk_by_key: dict | None = None,
                       page_index: dict | None = None) -> dict:
    """
    把 state 里的**旧格式键**迁移为新格式主键，并补算内容指纹。幂等。

    参数：
      chunk_by_key —— {"course::pA-B": chunk}，用于补算 fp（由 extract_kp 提供）
      page_index   —— {(course, page_start, page_end): chunk}，同上，按页范围索引
    返回 {"migrated", "dropped_dup", "unmatched", "fp_filled", "total", "rows"}；
    rows 是「新旧键 1:1 映射表」的原始行：(旧键, 新键, fp, at, chars, kps, 标记)。

    本函数**不落盘**（kb 只提供能力，写操作由调用方决定），调用方迁移完自行 state.save()。
    """
    chunk_by_key = chunk_by_key or {}
    page_index = page_index or {}
    out = {"migrated": 0, "dropped_dup": 0, "unmatched": [], "fp_filled": 0,
           "total": len(state.done), "rows": []}
    new_done: dict = {}
    for key, rec in state.done.items():
        rec = dict(rec)
        parsed = parse_chunk_key_full(key)
        if parsed is None:
            new_done[key] = rec
            out["unmatched"].append(key)
            out["rows"].append((key, "-", str(rec.get("fp")), str(rec.get("at")),
                                str(rec.get("chars")), str(rec.get("kps")), "无法解析"))
            continue
        course, ps, pe, ftag = parsed
        new_key = make_chunk_key(course, ps, pe, ftag)
        # page_index 按 (course, ftag, ps, pe) 索引；
        # 同时兼容调用方仍传 (course, ps, pe) 的情形。
        chunk = (chunk_by_key.get(new_key)
                 or page_index.get(parsed)
                 or page_index.get((course, ps, pe)))
        if chunk is None:
            # 解析成功但对应块不存在：键照迁，但补不到 fp（调用方可据此判定 unresolved）
            if (new_key not in chunk_by_key
                    and parsed not in page_index
                    and (course, ps, pe) not in page_index):
                out["unmatched"].append(key)
        if chunk is not None:
            fp = chunk_fingerprint(chunk)
            if rec.get("fp") is None:
                out["fp_filled"] += 1
            rec["fp"] = fp
        if new_key != key:
            rec["migrated_from"] = key
            out["migrated"] += 1
        tag = "已迁移" if new_key != key else "已是新键，补 fp"
        out["rows"].append((key, new_key, str(rec.get("fp")), str(rec.get("at")),
                            str(rec.get("chars")), str(rec.get("kps")), tag))
        prev = new_done.get(new_key)
        if prev is not None:
            # 新键已存在：保留信息更全的一条（指纹非空优先）
            if prev.get("fp") is None and rec.get("fp") is not None:
                new_done[new_key] = rec
            out["dropped_dup"] += 1
            continue
        new_done[new_key] = rec
    state.done = new_done
    return out


# ---------------------------------------------------------------------------
# 断点续跑
# ---------------------------------------------------------------------------
class State:
    """记录已跑过的任务，支持中断后接着跑。

    `is_done()` 接通**内容指纹**（键管身份、指纹管版本），并保留一道防呆垫片——
      指纹未知（调用方没传 / 存的是 None）→ 退化为**键匹配放行**，绝不误重抽。
      这是防回归垫片，**不是迁移手段**。
    ⚠️ 旧格式键（含位置序号）**故意不做键匹配放行**：否则「迁移有没有生效」就不可观测了。
       旧键的落地办法是 `python tools\\extract_kp.py --migrate-state`（或
       `kb.migrate_state_keys()`）；载入时若仍有旧键会 loud 告警，
       `extract_kp.py` 另有一道「陈旧主键守卫」会直接拒跑（返回码 3）。
    """

    def __init__(self, name: str):
        self.path = KP_DIR / f"state_{name}.json"
        data = load_json(self.path, {}) or {}
        self.done: dict = data.get("done", {})
        self.meta: dict = data.get("meta", {})
        legacy = [k for k in self.done if is_legacy_chunk_key(k)]
        if legacy:
            print(f"[WARN] {self.path.name} 含 {len(legacy)} 个旧格式键（末段是位置序号）："
                  f"断点续跑会失效、已完成块会被重抽；请运行 "
                  f"python tools\\extract_kp.py --migrate-state 迁移"
                  f"（extract_kp.py 的陈旧主键守卫也会直接拒跑）",
                  file=sys.stderr)

    def is_done(self, key: str, fingerprint: str | None = None) -> bool:
        rec = self.done.get(key)
        if rec is None:
            return False
        if fingerprint is None or rec.get("fp") is None:
            # 垫片：指纹未知 → 视为键匹配放行
            return True
        return rec.get("fp") == fingerprint

    def mark(self, key: str, fingerprint: str | None = None, **info) -> None:
        self.done[key] = {"fp": fingerprint, "at": datetime.now().isoformat(timespec="seconds"), **info}
        self.save()

    def save(self) -> None:
        write_json(self.path, {"done": self.done, "meta": self.meta,
                               "updated_at": datetime.now().isoformat(timespec="seconds")})

    def count(self) -> int:
        return len(self.done)


# ---------------------------------------------------------------------------
# LLM 调用（统一入口 + 成本统计）
# ---------------------------------------------------------------------------
_cost = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "failed": 0}


def reset_cost() -> None:
    for k in _cost:
        _cost[k] = 0


def cost_summary() -> dict:
    return dict(_cost)


def _llm_module():
    sys.path.insert(0, str(ROOT / "tools"))
    import llm  # type: ignore
    return llm


def ask_json(prompt: str, system: str | None = None, retries: int = 3,
             max_tokens: int = 8000, model: str | None = None,
             temperature: float = 0.2, verbose: bool = False):
    """
    调用模型并要求返回 JSON。内置：重试、JSON 容错、token 统计。
    失败抛 RuntimeError（调用方决定是跳过还是中断）。
    """
    llm = _llm_module()
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            res = llm.chat(msgs, model=model, json_mode=True, max_tokens=max_tokens,
                           temperature=temperature, disable_thinking=True, verbose=verbose)
            usage = res.get("usage") or {}
            _cost["prompt_tokens"] += usage.get("prompt_tokens", 0)
            _cost["completion_tokens"] += usage.get("completion_tokens", 0)
            _cost["calls"] += 1
            return llm.extract_json(res.get("content", ""))
        except Exception as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(min(2 ** attempt, 10))
    _cost["failed"] += 1
    raise RuntimeError(f"LLM 调用失败（{retries} 次）：{last_err}")


def ask_text(prompt: str, system: str | None = None, max_tokens: int = 8000,
             model: str | None = None, temperature: float = 0.3) -> str:
    llm = _llm_module()
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    res = llm.chat(msgs, model=model, max_tokens=max_tokens,
                   temperature=temperature, disable_thinking=True)
    usage = res.get("usage") or {}
    _cost["prompt_tokens"] += usage.get("prompt_tokens", 0)
    _cost["completion_tokens"] += usage.get("completion_tokens", 0)
    _cost["calls"] += 1
    return res.get("content", "")


def estimate_cost_yuan() -> float:
    """
    按现行峰谷定价的空闲时段估算（Flash：输入未命中 1.5 元/M，输出 4.5 元/M）。
    仅用于给用户一个量级感，不是账单。
    """
    return (_cost["prompt_tokens"] / 1_000_000 * 1.5
            + _cost["completion_tokens"] / 1_000_000 * 4.5)


# ---------------------------------------------------------------------------
# 运行标识与引擎指纹
# ---------------------------------------------------------------------------
def new_run_id(prefix: str = "R") -> str:
    """
    给一次跑批生成唯一标识，**写进这一批产生的每一行**。

    为什么需要：append 型产物（kp / evidence / transcript）在多次跑批之后会**批次混杂**，
    没有可靠的批次标识就无法安全清理旧批次。清理时若把**逐条生成**的时间戳字段
    （如 `transcribed_at`，它本身不具备批次语义）当成批次标识，就会误删不该删的行 ——
    因此批次标识必须由本函数显式生成、显式落盘。
    """
    return f"{prefix}{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(2).hex()}"


def llm_fingerprint() -> dict:
    """
    记录「这一次跑批用哪个模型、走哪个端点」——**不含密钥**。

    为什么需要：云端模型会静默升级/下线，没有指纹就无法复算历史结论
    （同一条录音/同一段正文，今天与下月可能给出不同结果）。
    """
    cfg: dict = {}
    env = CONFIG_DIR / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    base = cfg.get("DEEPSEEK_BASE_URL") or cfg.get("BASE_URL") or ""
    return {"base_url": base, "model": cfg.get("DEEPSEEK_MODEL") or "默认"}


def engine_fingerprint() -> dict:
    """记录一次跑批里三类引擎各用哪台实现（本地/云端）——用于复算与合规追溯。"""
    p = CONFIG_DIR / "engines.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {k: data.get(k) for k in ("parser", "ocr", "asr") if data.get(k)}


# ---------------------------------------------------------------------------
# 结构化运行日志
# ---------------------------------------------------------------------------
RUN_LOG_FILE = LOG_DIR / "runs.jsonl"


def run_log(stage: str, status: str, **fields) -> dict:
    """
    向 `90_日志/runs.jsonl` 追加一条结构化运行记录（append + 逐条落盘）。

    为什么需要：进度若只 `print` 到 stdout，跑批一多，"哪次跑了什么、花了多久、
    退出码多少、用的哪个模型"就只能靠滚动终端回看 —— 终端一关证据就没了。
    本函数是**唯一的运行记录出口**，被 `firekb.py` 统一入口调用。
    """
    rec = {"at": now_iso(), "stage": stage, "status": status, **fields}
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


@contextmanager
def run_record(stage: str, **fields):
    """
    上下文管理器：给一次运行自动记 start / 结束两条记录（含耗时、退出码、成本、指纹）。

    退出码的非 0 情况**不算异常**：各阶段脚本用返回码表达"[STOP] 守卫拒跑"这类正常结局，
    因此 SystemExit 被拦下并写成 `exit-<code>` 记录，不向上抛（否则统一入口会被 SystemExit 打断，
    日志里只剩一条 start 记录）。
    """
    t0 = time.time()
    reset_cost()
    run_log(stage, "start", **fields)
    rec: dict = {"status": "ok"}
    try:
        yield rec
    except SystemExit as exc:                 # 脚本内部 sys.exit(N)：正常结局，记下来即可
        # `SystemExit("消息")` 是引擎层与各守卫的 **fail-loud 写法**：把"为什么拒跑"
        # 放进退出消息里。而 Python 只在异常**传播到顶层**时才打印它 —— 这里把它拦下了，
        # 消息就会**彻底消失**。后果极隐蔽：统一入口 `python -m firekb parse` 变成
        # 「stdout 0 字节 + stderr 0 字节 + 退出码 1」，用户拿不到任何可排查的线索
        # （而直接运行 `python tools\parse_docs.py` 是能看到的 —— 同一件事两种可见性）。
        # 所以拦下之后必须**把它补印出来**：控制流照旧，不许静默。
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            if not exc.code.endswith("\n"):
                print(file=sys.stderr)
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        rec["status"] = "ok" if code == 0 else f"exit-{code}"
        rec["exit_code"] = code
    except BaseException as exc:              # 真异常：记满证据再抛给调用方
        run_log(stage, "error", duration_sec=round(time.time() - t0, 1),
                error=f"{type(exc).__name__}: {exc}", **fields)
        raise
    finally:
        if "exit_code" not in rec:
            rec["exit_code"] = 0 if rec["status"] == "ok" else 3
    run_log(stage, rec["status"], duration_sec=round(time.time() - t0, 1),
            cost=dict(_cost), llm=llm_fingerprint(), engines=engine_fingerprint(), **fields)
    return



def progress(i: int, total: int, prefix: str = "") -> str:
    pct = 100.0 * i / total if total else 0
    return f"{prefix}[{i}/{total}] {pct:5.1f}%"

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
