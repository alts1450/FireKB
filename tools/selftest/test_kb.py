# -*- coding: utf-8 -*-
"""
test_kb.py —— 共享原语层单元自测
================================

为什么必须有它：本项目的多数错误**不是崩溃，而是静默算错**——
  · 引用核验的「头真尾假」漏判（防幻觉闸门上的洞）；
  · chunk 主键撞车导致断点续跑失效、已抽取的块被重抽（知识点会翻倍）；
  · 键与指纹混用（键管身份、指纹管版本）导致"该重抽的没重抽"；
  · 把布尔命中标记喂给只认档位字符串的聚合函数 → 新知识点全被标成 C 且**不报错**。
这些原语是所有环节的公共地基，**它们错了不会有异常，只会一路静默到产物里**。
本文件只测原语（不碰网络、不写项目产物），跑一遍 < 1 秒。

用法： python -m firekb selftest        （或 python tools\\selftest\\test_kb.py）
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import kb  # noqa: E402

PASS = 0
FAIL = 0
# 自测过程中建的临时目录，统一在入口的 finally 里回收（见文件末尾）。
_TMP_DIRS: list[Path] = []


def check(name: str, cond, extra="") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}" + (f"  {extra}" if extra else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f"  {extra}" if extra else ""))


def check_or_skip(name: str, ready: bool, extra="", skip="") -> None:
    """
    依赖**本项目真实配置/数据**的断言：未初始化时跳过，而不是判失败。

    为什么要这样：刚 clone 下来的仓库只有 `*.example` 模板、
    没有素材与派生数据 —— 那些断言必然失败，于是 `doctor` 一片红，
    第一次上手的人会以为"这项目是坏的"。**体检不该对"还没配"报警报。**
    """
    if ready:
        check(name, True, extra)
    else:
        check(f"{name}（跳过）", True, skip or "尚未初始化 —— 运行 `firekb init` 后重跑")


def main() -> int:
    print("=" * 78)
    print("kb 共享原语自测")
    print("=" * 78)

    # ---------------------------------------------------------------- 主键往返
    print("\n1) chunk 主键：往返不变式（解析顺序一旦错位，下游引用会全部错位）")
    tag = kb.make_file_tag("规范教材/防排烟工程/教材.pdf")
    k_full = kb.make_chunk_key("防排烟工程", 30, 31, tag)
    k_mid = kb.make_chunk_key("防排烟工程", 30, 31)
    check("带标签键形态", k_full == f"防排烟工程::f{tag}::p30-31", k_full)
    check("无标签键形态", k_mid == "防排烟工程::p30-31", k_mid)
    check("往返 make(*parse_full(k)) == k", kb.make_chunk_key(*kb.parse_chunk_key_full(k_full)) == k_full)
    check("parse_full 顺序 = (course, ps, pe, tag)",
          kb.parse_chunk_key_full(k_full) == ("防排烟工程", 30, 31, tag))
    check("parse（三元素）忽略标签", kb.parse_chunk_key(k_full) == ("防排烟工程", 30, 31))
    check("消歧后缀不被误当标签",
          kb.parse_chunk_key_full("防排烟工程::p30-31::h1a2b") == ("防排烟工程", 30, 31, None))
    check("含 f 开头的章名不被误当标签",
          kb.parse_chunk_key_full("enclosure::fFire Science::p10-12") == ("enclosure", 10, 12, None))
    check("无法解析返回 None", kb.parse_chunk_key_full("垃圾键") is None)

    # ---------------------------------------------------------------- 旧键识别
    print("\n2) 旧格式键识别（判据盯死末段 4 位数字，不能用 :: 计数）")
    check("旧键（末段序号）", kb.is_legacy_chunk_key("课::第一章::第一节::p30-31::0007"))
    check("新键不带标签", not kb.is_legacy_chunk_key("课::p30-31"))
    check("新键带标签", not kb.is_legacy_chunk_key(k_full))
    check("新键带消歧后缀（含 2 个 ::，假阳性陷阱）",
          not kb.is_legacy_chunk_key("课::p30-31::h1a2b"))

    # ---------------------------------------------------------------- 内容指纹
    print("\n3) 内容指纹：键管身份、指纹管版本")
    c1 = {"page_start": 30, "page_end": 31, "chars": 100, "text": "烟气层高度与温度的关系" * 5}
    c2 = dict(c1)
    check("同内容同指纹", kb.chunk_fingerprint(c1) == kb.chunk_fingerprint(c2))
    c3 = dict(c1, chars=101)
    check("字符数变 → 指纹变", kb.chunk_fingerprint(c3) != kb.chunk_fingerprint(c1))
    c4 = dict(c1, text="完全不同的开头内容" + "x" * 50)
    check("开头变 → 指纹变", kb.chunk_fingerprint(c4) != kb.chunk_fingerprint(c1))
    c5 = dict(c1, text="\n\n  烟气层高度与温度的关系" * 5)   # 只加空白
    check("仅空白差异 → 指纹不变（先归一化空白）",
          kb.chunk_fingerprint(c5) == kb.chunk_fingerprint(c1))
    check("指纹含页范围", kb.chunk_fingerprint(c1).startswith("p30-31|"), kb.chunk_fingerprint(c1))

    # ---------------------------------------------------------------- 归一化
    # PDF 文本层里的 `ﬁ` 合字会让英文引用逐字比对必然失败，被判 fabricated/not_found ——
    # 而它并非编造。补上 NFKC 归一化后这类引用才能命中，且不得带来任何降级
    # （原本能判过的引用不能变得判不过）。归一化只做「同形字折叠」，
    # 不许放宽数字的逐字严格性。
    print("\n3b) 归一化：合字/兼容字符折叠，但**不许动数字的严格性**")
    _n = kb.normalize_for_match
    check("PDF 合字 ﬁ → fi（英文教材失配的主因）", _n("speciﬁc ﬁre") == _n("specific fire"),
          f"{_n('speciﬁc ﬁre')!r}")
    check("合字 ﬂ → fl", _n("ﬂame") == _n("flame"), f"{_n('ﬂame')!r}")
    check("合字 ﬃ → ffi", _n("suﬃcient") == _n("sufficient"))
    check("全角数字/字母折叠", _n("ＧＢ５５０３７") == _n("gb55037"), f"{_n('ＧＢ５５０３７')!r}")
    check("上标折叠", _n("m²") == _n("m2"), f"{_n('m²')!r}")
    check("组合附加符号折叠（公式 Q̇ 与 Q 同形）", _n("Q\u0307c") == _n("Qc"), f"{_n('Q\u0307c')!r}")
    check("合字不影响中文", _n("《建筑防火通用规范》") == _n("《建筑防火通用规范》"))
    # ⚠️ 反向锁：明确**不做**"去掉所有标点"那一档归一化 —— 那会让 1.5 与 15 同形。
    #    数字逐字命中是本项目红线，不能用更漂亮的通过率去换。
    check("小数点位必须保留（1.5 ≠ 15）", _n("1.5") != _n("15"), f"{_n('1.5')!r} vs {_n('15')!r}")
    check("千分位/连字符不得被吞掉成另一个数", _n("1.5mm") != _n("15mm"))

    # ---------------------------------------------------------------- 引用核验
    print("\n4) 引用核验四档 + 数字严格通道（头真尾假必须拦下）")
    body = ("烟气层高度是火灾中最重要的参数之一。挡烟垂壁的最小高度不应小于500mm，"
            "且储烟仓厚度不应小于空间净高的10%。排烟口的最大允许排烟量应按规范计算。")
    q_exact = "挡烟垂壁的最小高度不应小于500mm"
    tier, matched, note = kb.verify_quote(q_exact, body)
    check("逐字命中 → exact", tier == kb.VERIFY_EXACT, tier)
    check("exact 返回命中片段", matched == kb.normalize_for_match(q_exact))
    q_num_bad = "烟气层高度是火灾中最重要的参数之一。挡烟垂壁的最小高度不应小于1500mm"   # 500 → 1500
    tier, matched, note = kb.verify_quote(q_num_bad, body)
    check("数字被改（真前缀≥24 字）→ fabricated（不走前缀宽容）", tier == kb.VERIFY_FABRICATED, tier)
    check("数字未命中时说明里点名数字", "1500" in note, note)
    # 短引用（<24 字）里改数字：头尾都等于整句、两处都不命中 → not_found（无可靠锚点，属合理判定）
    tier_short = kb.verify_quote("挡烟垂壁的最小高度不应小于1500mm", body)[0]
    check("短引用改数字 → not_found（头尾都无锚点，不硬凑 fabricated）",
          tier_short == kb.VERIFY_NOT_FOUND, tier_short)
    tier, _, _ = kb.verify_quote("这句话在原文里完全不存在" * 2, body)
    check("完全不命中 → not_found", tier == kb.VERIFY_NOT_FOUND, tier)
    q_head = ("烟气层高度是火灾中最重要的参数之一。挡烟垂壁的最小高度不应小于500mm，"
              "而这一段是我编出来的原文根本没有这句话")
    tier, _, note = kb.verify_quote(q_head, body)
    check("头真尾假（真前缀≥24 字）→ fabricated（防幻觉闸门必须拦下）",
          tier == kb.VERIFY_FABRICATED, tier)
    check("空引用 → not_found", kb.verify_quote("", body)[0] == kb.VERIFY_NOT_FOUND)
    check("空原文 → not_found", kb.verify_quote(q_exact, "")[0] == kb.VERIFY_NOT_FOUND)
    check("只有 exact 算通过",
          kb.quote_passes(kb.VERIFY_EXACT) and not kb.quote_passes(kb.VERIFY_PARTIAL))

    print("\n5) 置信度聚合：两种入参形态必须都正确（布尔当档位会全变 C）")
    check("全 exact → A", kb.confidence_from_tiers(["exact", "exact"]) == "A")
    check("部分 exact → B", kb.confidence_from_tiers(["exact", "partial"]) == "B")
    check("无 exact → C", kb.confidence_from_tiers(["partial", "not_found"]) == "C")
    check("空 → C", kb.confidence_from_tiers([]) == "C")
    check("布尔 True → A（抽取侧传的是 verified_flags）",
          kb.confidence_from_tiers([True, True]) == "A")
    check("布尔 [True, False] → B", kb.confidence_from_tiers([True, False]) == "B")

    # ---------------------------------------------------------------- state 语义
    print("\n6) 断点续跑：键管身份、指纹管版本（含防误重抽垫片）")
    st = kb.State("__selftest__")
    # 临时目录**建了必须回收**：早先只 mkdtemp 不清理，每跑一次自测就在系统临时目录里
    # 留一个 `firekb_selftest_*`（实测攒到 123 个）。回收统一放在入口的 finally 里 ——
    # 不用 `with TemporaryDirectory()` 是因为 `tmp` 后面几段还要复用，整个包起来会让
    # 好几段测试变成嵌套块，可读性反而更差。
    tmp = Path(tempfile.mkdtemp(prefix="firekb_selftest_"))
    _TMP_DIRS.append(tmp)
    st.path = tmp / "state.json"
    st.done = {}
    st.mark("课::p1-2", fingerprint="fp1", chars=10, kps=2)
    check("已标记 + 指纹相同 → 视为完成（不重抽）", st.is_done("课::p1-2", "fp1"))
    check("指纹不同 → 不视为完成（必须重抽）", not st.is_done("课::p1-2", "fp2"))
    check("调用方不传指纹 → 退化为键匹配放行（防误重抽垫片）", st.is_done("课::p1-2"))
    st.mark("课::p3-4", fingerprint=None, chars=10, kps=1)
    check("存量指纹为 None → 也放行", st.is_done("课::p3-4", "whatever"))
    check("未标记的键 → 不视为完成", not st.is_done("课::p9-9", "fp1"))
    disk = json.loads(st.path.read_text(encoding="utf-8"))
    check("落盘结构是 {done, meta, updated_at}", set(disk) == {"done", "meta", "updated_at"}, list(disk))

    # ---------------------------------------------------------------- 迁移幂等
    print("\n7) state 主键迁移：幂等、可补指纹、无法解析的键保留并计入 unmatched")
    st2 = kb.State("__selftest2__")
    st2.path = tmp / "state2.json"
    st2.done = {
        "课::第一章::第一节::p1-2::0000": {"fp": None, "at": "x", "chars": "10", "kps": "1"},
        "课::p5-6": {"fp": "fp5", "at": "y"},          # 已是新键
        "坏键": {"fp": "z", "at": "y"},                 # 无法解析
    }
    chunk = {"page_start": 1, "page_end": 2, "chars": 10, "text": "abcdefghij"}
    out = kb.migrate_state_keys(st2, chunk_by_key={"课::p1-2": chunk},
                                page_index={("课", 1, 2, None): chunk})
    check("旧键被迁移", out["migrated"] == 1, out["migrated"])
    check("补上了指纹", out["fp_filled"] == 1, out["fp_filled"])
    check("新键直通", "课::p5-6" in st2.done)
    check("坏键保留并计入 unmatched", "坏键" in st2.done and "坏键" in out["unmatched"])
    check("迁移后键形态正确", "课::p1-2" in st2.done and st2.done["课::p1-2"]["fp"])
    out2 = kb.migrate_state_keys(st2, chunk_by_key={"课::p1-2": chunk})
    check("第二次迁移不再变动（幂等）", out2["migrated"] == 0, out2["migrated"])

    # ---------------------------------------------------------------- 主键唯一断言
    print("\n8) 赋键唯一性：碰撞消歧 + 仍碰撞则 fail loudly")
    chs = [{"course": "课", "page_start": 1, "page_end": 2, "file_id": "a.pdf", "text": "AAA" * 20},
           {"course": "课", "page_start": 1, "page_end": 2, "file_id": "b.pdf", "text": "BBB" * 20}]
    kb.assign_chunk_ids(chs)
    check("不同文件同页范围 → 键不同（标签生效）", chs[0]["chunk_id"] != chs[1]["chunk_id"],
          chs[0]["chunk_id"])
    same = [{"course": "课", "page_start": 1, "page_end": 2, "text": "AAA" * 20},
            {"course": "课", "page_start": 1, "page_end": 2, "text": "BBB" * 20}]
    kb.assign_chunk_ids(same)
    check("同课程同页范围不同内容 → 内容派生后缀消歧",
          same[0]["chunk_id"] != same[1]["chunk_id"], same[1]["chunk_id"])
    try:
        kb.assign_chunk_ids([{"course": "课", "page_start": 1, "page_end": 2, "text": "同" * 40},
                             {"course": "课", "page_start": 1, "page_end": 2, "text": "同" * 40}])
        check("完全同内容同页范围 → 抛错（fail loudly）", False, "没有抛错")
    except RuntimeError as exc:
        check("完全同内容同页范围 → 抛错（fail loudly）", True, str(exc)[:40])

    # ---------------------------------------------------------------- 运行日志
    print("\n9) 运行日志与批次标识：写盘、退出码、指纹都在")
    old_log, old_dir = kb.RUN_LOG_FILE, kb.LOG_DIR
    kb.LOG_DIR = tmp
    kb.RUN_LOG_FILE = tmp / "runs.jsonl"
    try:
        rid = kb.new_run_id("TEST")
        check("run_id 形如 TEST20260923-120000-ab12",
              rid.startswith("TEST") and len(rid) == len("TEST20260923-120000-ab12"), rid)
        check("两次 run_id 不同", kb.new_run_id("TEST") != kb.new_run_id("TEST"))
        with kb.run_record("__selftest_stage__", argv=["--demo"]) as rec:
            kb._cost["calls"] = 3
            rec["status"] = "ok"
        rows = [json.loads(x) for x in kb.RUN_LOG_FILE.read_text(encoding="utf-8").splitlines() if x.strip()]
        check("写了 start + 结束两条", [r["status"] for r in rows] == ["start", "ok"], [r["status"] for r in rows])
        check("结束记录含耗时", isinstance(rows[-1].get("duration_sec"), float))
        check("结束记录含成本", (rows[-1].get("cost") or {}).get("calls") == 3, rows[-1].get("cost"))
        check("结束记录含模型指纹", "model" in (rows[-1].get("llm") or {}), rows[-1].get("llm"))
        check("结束记录含引擎指纹", "asr" in (rows[-1].get("engines") or {}), rows[-1].get("engines"))
        check("argv 被记下", rows[-1].get("argv") == ["--demo"])

        def _boom():
            with kb.run_record("__selftest_boom__"):
                raise ValueError("炸")
        try:
            _boom()
            check("异常被记录并向上抛", False, "没有抛")
        except ValueError:
            rows2 = [json.loads(x) for x in kb.RUN_LOG_FILE.read_text(encoding="utf-8").splitlines() if x.strip()]
            check("异常被记录并向上抛",
                  [r["status"] for r in rows2][-1] == "error", [r["status"] for r in rows2][-1])
    finally:
        kb.RUN_LOG_FILE, kb.LOG_DIR = old_log, old_dir

    # ---------------------------------------------------------------- 规范条文库
    # 「规范条文库」与课表是两种口径：规范不按课程组织，而是逐本规范独立成条目，
    # source_file 必须能唯一落到某一本规范上。
    #
    # ⚠️ 本节的断言里混了**两类**：
    #   ① **通用机制**（无法匹配返回 None、空值不炸）—— 任何环境都该过；
    #   ② **本项目的真实配置**（规范本数、具体 ID）—— 只有填了自己的 references.json 才有意义。
    #   刚 clone 下来的仓库只有 `references.example.json`（模板），硬断言真实配置会让
    #   `doctor` 一片红、让人以为"项目是坏的"。所以第 ② 类在未配置时**跳过**并说明原因。
    print("\n10) 规范条文库：source_file → 某一本规范")
    lib = kb.reference_library()
    works = kb.reference_works()
    ready = bool(lib) and len(works) >= 1
    check_or_skip("读取 references.json", ready, f"库={lib.get('name')!r} 规范 {len(works)} 本",
                  skip="尚未配置 references.json（只有 .example 模板）—— 运行 `firekb init` 后重跑")
    if ready:
        check("库口径是 reference 而非 course", lib.get("role") == "reference", str(lib.get("role")))
        check("逐本规范 ID 互不相同", len({w.get("id") for w in works}) == len(works),
              str(sorted(w.get("id") for w in works)))
        # 关键机制：短 match 会抢走长 match 的文件 —— 必须按 match 长度降序匹配（用本仓配置验证）
        w1 = kb.resolve_reference("规范教材/通用规范/《建筑防火通用规范》GB 55037-2022 (国标).pdf")
        w2 = kb.resolve_reference("规范教材/通用规范/建筑设计防火规范(2018年版) (住建部).pdf")
        if w1 and w2:
            check("较长的 match 优先（不被短串抢占）",
                  w1.get("id") != w2.get("id"), f"{w1.get('id')} / {w2.get('id')}")
    check("无法匹配的文件返回 None", kb.resolve_reference("规范教材/某课/某教材.pdf") is None)
    check("空值不炸", kb.resolve_reference(None) is None and kb.resolve_reference("") is None)

    # ---------------------------------------------------------------- 块不跨文件
    # 为什么单独立这条：每本书 page_no 各自从 1 重编，块一旦跨文件，
    # page_start..page_end 就是假的（会出现 p561-5 这种倒挂范围），
    # 而 chunk_id 的文件标签只指向第一本 ⇒ 引用落点对到错误的文件。
    print("\n11) 块绝不跨文件（页码各书重编 ⇒ 跨文件的范围无意义）")
    mk = lambda fid, no, txt: {"file_id": fid, "source_file": fid + ".pdf", "page_no": no,
                               "page_label": f"第{no}页", "course": "课", "text": txt,
                               "char_count": len(txt)}
    body = "正文" * 400                      # 足够长，确保不会低于 CHUNK_MIN_CHARS
    mixed = [mk("A", 1, body), mk("A", 2, body), mk("B", 1, body), mk("B", 2, body)]
    ch = kb.build_chunks(mixed)
    by_file = {}
    for c in ch:
        by_file.setdefault(c["file_id"], []).append(c)
    cross = [c for c in ch if not (c["page_start"] <= c["page_end"])]
    check("两本书混喂 → 切成多块而不是一块", len(ch) >= 2, f"{len(ch)} 块")
    check("书 A / 书 B 各自成块（不串）", set(by_file) == {"A", "B"}, str(sorted(by_file)))
    check("没有倒挂页范围（start > end 即跨文件信号）", not cross, f"{len(cross)} 个倒挂")
    check("内部字段 _file 不进产物", all("_file" not in c for c in ch))

    # 生产路径事实锁：用真实数据复算。断言的是**不变式**，不是某个固定块数 ——
    # 写死块总数只会锁住"当前状态"，计划一变就变红，那不是发现了问题。真正该钉住的是两件事：
    #   ① 块绝不跨作品（引用落点才唯一）；
    #   ② state_extract 记录过的块 id **必须仍然存在**（否则是幽灵标记 ⇒ 静默以为抽过了）。
    # 数据不在（尚未解析）时**不判失败**，而是明确说明跳过原因。
    print("\n12) 生产切块路径复算（锁定「块不跨作品」这条不变式）")
    if not (kb.PAGES_FILE.exists() and kb.SKELETON_FILE.exists()):
        check("生产路径复算（跳过）", True, "当前环境无 pages.jsonl / skeleton.json —— 未判失败，仅未验证")
    else:
        import extract_kp
        pages = kb.load_jsonl(kb.PAGES_FILE)
        skel = kb.load_json(kb.SKELETON_FILE, {}) or {}
        groups = {}
        for p in pages:
            groups.setdefault(p.get("course") or "未分类", []).append(p)
        bad, ids = [], set()
        for course, pgs in groups.items():
            for c in extract_kp.chunks_from_skeleton(course, pgs, skel, max_chars=extract_kp.MAX_CHARS):
                ids.add(c["chunk_id"])
                fps = c.get("pages") or []
                if len({p.get("file_id") for p in fps}) > 1:
                    bad.append(c["chunk_id"])
        st = kb.load_json(kb.KP_DIR / "state_extract.json", {}) or {}
        done = set((st.get("done") or {}).keys())
        pending = ids - done
        check("零个跨作品块", not bad, f"跨作品 {len(bad)} 个" + (f"：{bad[:2]}" if bad else ""))
        check("state_extract 的块标记全部仍然存在（无幽灵标记）", done <= ids,
              f"记录 {len(done)} ｜ 幽灵 {len(done - ids)} 个"
              + (f"：{sorted(done - ids)[:2]}" if done - ids else ""))
        check("计划块 = 已抽取 + 待抽（账目自洽）", len(ids) == len(done) + len(pending),
              f"计划 {len(ids)} ｜ 已抽取 {len(done)} ｜ **待抽 {len(pending)}**"
              + (f"（含待抽的附录块）" if pending and len(pending) <= 10 else ""))

    # ---------------------------------------------------------------- 实验批次
    # 探测性抽取留下的知识点**保留数据、如实标注**（不删）。
    # 为什么必须标出来：它们 status 同样是 verified，会静默流进背诵卡产物，
    # 用户会把"试跑留下的样本"当成整门课的考点背下来。
    print("\n13) 实验批次标注：按 (course + kp_id_prefix + created_at) 命中")
    batches = kb.load_experimental_batches()
    check_or_skip("读取 experimental-batches.json", bool(batches), f"{len(batches)} 个批次",
                  skip="本仓尚未登记任何实验批次（未配置或批次为空）—— 机制测试见下")
    # 机制用**合成批次**测：把配置指向临时目录，走真实的 kb 读取路径
    with tempfile.TemporaryDirectory() as _td:
        _old_cfg = kb.CONFIG_DIR
        kb.CONFIG_DIR = Path(_td)
        (Path(_td) / "experimental-batches.json").write_text(json.dumps({
            "batches": [{"id": "EXP-SYNTH", "course": "示例课", "kp_id_prefix": "DEMO-",
                         "created_at": "2026-01-02T03:04:05", "expected_kp": 2}]
        }, ensure_ascii=False), encoding="utf-8")
        try:
            b = kb.load_experimental_batches()[0]
            def _kp(course, kpid, created):
                return {"kp_id": kpid, "course": course, "created_at": created,
                        "status": "verified", "name": "x"}
            hit = kb.is_experimental_kp(_kp("示例课", "DEMO-0001", "2026-01-02T03:04:05"))
            check("登记批次内的行 → 命中", hit is not None and hit.get("id") == "EXP-SYNTH",
                  str((hit or {}).get("id")))
            check("同课但不同批次时间 → 不误伤",
                  kb.is_experimental_kp(_kp("示例课", "DEMO-0001", "2026-01-01T00:00:00")) is None)
            check("同批次时间但别的课 → 不误伤",
                  kb.is_experimental_kp(_kp("别的课", "DEMO-0001", "2026-01-02T03:04:05")) is None)
            check("正式知识点（无登记）→ 不误伤",
                  kb.is_experimental_kp(_kp("示例课", "OTHER-0001", "2026-01-02T03:04:05")) is None)
            check("前缀不对 → 不误伤",
                  kb.is_experimental_kp(_kp("示例课", "DEMO-0001X", "2026-01-02T03:04:05")) is not None)
        finally:
            kb.CONFIG_DIR = _old_cfg
    if batches and kb.KP_FILE.exists():
        ids = kb.experimental_kp_ids()
        # ⚠️ 必须用**真实**批次（batches[0]），不是上面合成的那个（变量 b 已被覆盖）
        check("命中数 = 登记的 expected_kp", len(ids) == batches[0].get("expected_kp"),
              f"命中 {len(ids)} / 登记 {batches[0].get('expected_kp')}")
    else:
        check("命中数（跳过）", True, "无登记批次或当前环境无 kp.jsonl —— 未判失败，仅未验证")

    # ---------------------------------------------------------------- 复核工作表
    # 复核工作表把 audit 队列转成"引用 vs 原文同屏"，供人工裁决。
    # 为什么必须钉住定位：`_locate` 若定位不到引用，窗口会退化成"从头截 900 字"，
    # 而引用其实在中段 —— 人看到右边没有那句话，会**误判成引用不存在**。
    # 假阴性会直接污染人工裁决。
    print("\n14) 复核工作表：引用定位窗口（假阴性会污染人工裁决）")
    import importlib.util as _ilu
    _tools_dir = Path(__file__).resolve().parent.parent
    _spec = _ilu.spec_from_file_location("_rq", _tools_dir / "review_queue.py")
    _rq = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_rq)
    # 中段引用 + 原文用合字/换行，引用用普通写法 —— 正是英文教材 PDF 的真实情形。
    # ⚠️ 样例必须是**合成句子**，不要抄真实教材的句子：本项目的公开闸门会把
    #    "与 pages.jsonl 逐字重合 ≥24 字"判为教材原文泄露。
    filler = "铺垫文字。" * 200
    # 样例中刻意保留一个**合字**（ﬂ）以覆盖 NFKC 路径；句子本身是编造的，
    # 以免被"与教材逐字重合 ≥24 字"的泄露闸门判成抄录原文。
    body = filler + "Zephyr ﬂow quux corge\ngrault garply waldo." + filler
    win, hit = _rq._locate(body, "Zephyr flow quux corge grault garply waldo.")
    check("合字/换行差异下仍能定位", hit)
    check("窗口落在引用附近（不是从头截）", "Zephyr" in win,
          f"窗口首 40 字：{win[:40]!r}")
    win2, hit2 = _rq._locate(body, "这句话原文里根本没有")
    check("确实不存在时如实报未命中", not hit2)
    check("未命中时给出开头窗口（有内容可看）", bool(win2))
    check("空正文不炸", _rq._locate("", "x") == ("", False))
    check("裁决取值只认三种", _rq.VERDICTS == {"ok", "bad", "skip"}, str(sorted(_rq.VERDICTS)))

    # ---------------------------------------------------------------- 附录归类
    # 附录/附表是正文内容，不是"非正文材料"—— 整章跳过会让数值表从产物里消失。
    # 这条测试守两件事：判据只有一处实现（build_skeleton.classify_kind），且附录必须"保留"。
    print("\n15) 附录归类：是内容，不是非正文材料")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import build_skeleton as _bs
    import review_skeleton as _rs
    check("「附 录」→ appendix", _bs.classify_kind("附 录") == "appendix",
          _bs.classify_kind("附 录"))
    check("「续附表」→ appendix（易被误判成 matter 的一类）",
          _bs.classify_kind("续附表") == "appendix", _bs.classify_kind("续附表"))
    check("「附录A 钢板圆形通风管道计算表」→ appendix",
          _bs.classify_kind("附录A 钢板圆形通风管道计算表（摘录)") == "appendix")
    check("「Appendix A」→ appendix", _bs.classify_kind("Appendix A") == "appendix")
    check("Index → 仍是 matter（索引确实是非正文材料）",
          _bs.classify_kind("Index") == "matter", _bs.classify_kind("Index"))
    check("Contents → matter", _bs.classify_kind("Contents") == "matter")
    check("符号表 → matter", _bs.classify_kind("符号表") == "matter")
    check("普通章 → body", _bs.classify_kind("第1章火灾烟气的产生及危害") == "body")
    check("空标题不炸", _bs.classify_kind("") == "body")
    # 规则预判必须把附录判成 chapter（保留），而不是 matter
    v, why = _rs.rule_verdict("附 录")
    check("review_skeleton 规则预判：附录 → chapter（保留抽取）", v == "chapter", f"{v} / {why}")
    v2, _ = _rs.rule_verdict("Index")
    check("review_skeleton 规则预判：Index 仍 → matter", v2 == "matter", str(v2))

    # ---------------------------------------------------------------- JSONL 容错
    # 全项目只留一份 load_jsonl（在 kb 里），避免"同名不同源"的实现各自漂移。
    # 这条测试守住的静默缺陷：带 BOM 的文件用 utf-8 读，首行解析失败会被
    # "坏行跳过"逻辑吞掉 ⇒ 单行文件读出来是 [] ⇒ 工具显示"0 条"且不报错。
    print("\n16) JSONL 容错：坏行跳过、以及 BOM 不许静默丢数据")
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _d:
        d = Path(_d)
        f = d / "x.jsonl"
        f.write_text('{"a":1}\n\nbad\n{"b":2}\n', encoding="utf-8")
        check("坏行跳过、好行保留", kb.load_jsonl(f) == [{"a": 1}, {"b": 2}], str(kb.load_jsonl(f)))
        f.write_text("", encoding="utf-8")
        check("空文件 → []", kb.load_jsonl(f) == [])
        check("不存在 → []", kb.load_jsonl(d / "nope.jsonl") == [])
        # BOM：这是关键回归项（BOM 的现实来源：PowerShell Set-Content -Encoding UTF8）
        f.write_bytes(b"\xef\xbb\xbf" + '{"a":1}\n{"b":2}\n'.encode("utf-8"))
        got = kb.load_jsonl(f)
        check("带 BOM 的 JSONL 不被静默丢行（首行也要读出来）",
              got == [{"a": 1}, {"b": 2}], f"{got}")
        # 单行 + BOM：旧实现会读成 []（工具显示"0 条"且不报错）
        f.write_bytes(b"\xef\xbb\xbf" + '{"only":1}\n'.encode("utf-8"))
        check("单行+BOM 不读成空表", kb.load_jsonl(f) == [{"only": 1}], str(kb.load_jsonl(f)))
        # 无 BOM 行为必须与从前一致（纯增益）
        f.write_text('{"a":1}\n', encoding="utf-8")
        check("无 BOM 行为不变", kb.load_jsonl(f) == [{"a": 1}])
        # JSON 配置同理
        j = d / "c.json"
        j.write_bytes(b"\xef\xbb\xbf" + '{"k":1}'.encode("utf-8"))
        check("带 BOM 的 JSON 配置读得出来（load_json 不静默返回 default）",
              kb.load_json(j, {}) == {"k": 1}, str(kb.load_json(j, {})))

    # ---------------------------------------------------------------- 页码双轨
    # 页码映射支持两种登记方式：自动探测的偏移量，以及人工登记的锚点。
    # 这条测试守的三个不变式：
    #   ① 方向必须只有一种 —— "印刷 = 物理 + offset"；写反了会把 294 指到 311；
    #   ② **没有映射规则时不许退回物理页号** —— 那会让"PDF p8"被当成"书上第 8 页"，
    #      是比"报错"更坏的静默错；
    #   ③ EPUB 的 page_no 是**节序号**、与书页不等长 ⇒ 必须支持锚点分段插值。
    #
    # ⚠️ 机制部分**用合成配置**测（把 kb.CONFIG_DIR 指向临时目录，走真实读取路径）——
    #    这样公开仓 clone 下来也能验证机制，而本项目真实偏移值另立一节（未配置则跳过）。
    print("\n17) 页码双轨：偏移与锚点（合成配置测机制）")
    with tempfile.TemporaryDirectory() as _td:
        _old_cfg = kb.CONFIG_DIR
        kb.CONFIG_DIR = Path(_td)
        (Path(_td) / "page-offsets.json").write_text(json.dumps({
            "courses": {
                "某课": {"offset": -17, "source": "auto", "files": {}},
                "多书课": {"offset": None, "source": None, "mixed": True, "files": {
                    "fA": {"offset": -14, "source": "auto"},
                    "fB": {"offset": -8, "source": "auto"},
                }},
                "锚点课": {"offset": None, "source": None, "files": {
                    "fE": {"source": "manual", "anchors": [
                        {"index": 4, "printed": 11}, {"index": 7, "printed": 21}]},
                }},
            }
        }, ensure_ascii=False), encoding="utf-8")
        try:
            check("offset 方向：印刷 = 物理 + offset（311 → 294）",
                  kb.printed_page_no("某课", 311) == 294, str(kb.printed_page_no("某课", 311)))
            check("page_ref 双轨形态", kb.page_ref("某课", 311) == "书p294（PDF p311）",
                  kb.page_ref("某课", 311))
            # 多本书：课程级不给数字（否则取众数得到假数字，引用任何一本都指错页）
            check("多书的课课程级 offset 为 None", kb.page_offset("多书课") is None,
                  str(kb.page_offset("多书课")))
            check("标为 mixed", bool(kb.load_page_offsets()["courses"]["多书课"].get("mixed")))
            check("文件级 offset 可用（fA = -14）", kb.page_offset("多书课", "fA") == -14)
            check("文件级差异被保留（fB = -8）", kb.page_offset("多书课", "fB") == -8)
            # 锚点：锚点本身必须精确，区间内插值，区间外标推算
            check("锚点模式被识别", (kb.page_map("锚点课", "fE") or {}).get("kind") == "anchors")
            check("锚点值精确（第4节 → 书p11）", kb.printed_page_no("锚点课", 4, "fE") == 11)
            check("锚点值精确（第7节 → 书p21）", kb.printed_page_no("锚点课", 7, "fE") == 21)
            check("区间内插值（第5节 → 书p14）", kb.printed_page_no("锚点课", 5, "fE") == 14)
            check("区间内不标推算", not kb.is_extrapolated("锚点课", 5, "fE"))
            check("区间外标推算", kb.is_extrapolated("锚点课", 11, "fE"))
            check("区间外的引用带「推算」字样",
                  "推算" in kb.page_ref("锚点课", 11, "fE", label="第11节"),
                  kb.page_ref("锚点课", 11, "fE", label="第11节"))
            # 反向锁：没有映射规则时**绝不**冒充印刷页
            check("无映射的课 printed_page_no 返回 None（不假装算出来了）",
                  kb.printed_page_no("不存在的课", 8) is None,
                  str(kb.printed_page_no("不存在的课", 8)))
            check("无映射时 page_ref 退化为「PDF pN」（不冒充印刷页）",
                  kb.page_ref("不存在的课", 8) == "PDF p8", kb.page_ref("不存在的课", 8))
            check("锚点课里另一个文件不被套用", kb.page_ref("锚点课", 8, "fX") == "PDF p8",
                  kb.page_ref("锚点课", 8, "fX"))
        finally:
            kb.CONFIG_DIR = _old_cfg
    check("未知课程不炸", kb.page_ref("不存在的课", 5) == "PDF p5", kb.page_ref("不存在的课", 5))
    check("page_no 非整数不炸", kb.printed_page_no("防排烟工程", "第8页") is None)

    # 本项目真实偏移值（只有填了自己的 page-offsets.json 才有意义 —— 未配置则跳过）
    print("\n18) 本项目真实偏移值（未配置时跳过）")
    _real = (kb.load_page_offsets().get("courses") or {})
    check_or_skip("已配置真实页码偏移", bool(_real), f"{len(_real)} 门课",
                  skip="尚未配置 page-offsets.json（只有 .example 模板）—— 机制已在第 17 节验证")
    if _real:
        _c = next((c for c, v in _real.items() if isinstance(v.get("offset"), int)), None)
        if _c:
            off = _real[_c]["offset"]
            check(f"「{_c}」offset 生效（印刷 = 物理 + {off}）",
                  kb.printed_page_no(_c, 100 + 0) == 100 + off,
                  f"物理100 → 书p{kb.printed_page_no(_c, 100)}")
        _mixed = [c for c, v in _real.items() if v.get("mixed")]
        if _mixed:
            check(f"多书课不给课程级 offset（{_mixed[0]}）",
                  kb.page_offset(_mixed[0]) is None, str(kb.page_offset(_mixed[0])))

    print("\n" + "=" * 78)
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    # 自测自己崩了也要给汇总：只看 traceback 无法判断"已过多少项"，
    # 容易把"没跑到"误读成"失败了"。
    try:
        _code = main()
    except BaseException as exc:
        print("\n" + "=" * 78)
        print(f"[FAIL] 自测未跑完：{type(exc).__name__}: {exc}")
        print(f"       已通过 {PASS} 项，失败 {FAIL} 项（其后的检查没有执行）")
        print("=" * 78)
        raise
    finally:
        # 临时目录一律回收（成功、失败、异常都走这里）
        for _d in _TMP_DIRS:
            shutil.rmtree(_d, ignore_errors=True)
    sys.exit(_code)
