# -*- coding: utf-8 -*-
"""
引擎层自动化测试（零第三方依赖，直接 python 运行）
==================================================
    python tools\\selftest\\test_engines.py

覆盖：
  1. 注册表：非法引擎名必须**报错并列出可用项**（不允许静默回退到默认）
  2. 配置：engines.json 缺项时用 DEFAULTS 补齐；缺文件也能工作
  3. 能力声明：preflight() 返回缺失项列表（当前环境缺 GPU 依赖时应如实报出）
  4. 出境把关：有出境引擎且未同意 → 必须 fail-loud；同意后放行
  5. 出境同意文件写在本地配置目录，不进版本库（应被 .gitignore 排除）
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import kb  # noqa: E402
import engines  # noqa: E402
from engines import egress as eg  # noqa: E402
from engines.base import BaseEngine, EngineSpec  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [%s] %-34s %s" % ("PASS" if ok else "FAIL", name, detail))


class _FakeCloud(BaseEngine):
    spec = EngineSpec(kind="parser", name="fake_cloud", label="假云端（测试用）",
                      python_pkgs=("json",), needs_network=True, needs_api_key=("FAKE_KEY",),
                      egress="document", note="测试：会把原始文件发往第三方")


def main() -> int:
    print("=" * 84)
    print("引擎层测试")
    print("=" * 84)

    # 1) 非法引擎名 → 必须报错并列出可用项
    print("\n1) 注册表与非法名")
    try:
        engines.get_engine("parser", "does_not_exist")
        check("invalid_name_raises", False, "竟然没报错")
    except SystemExit as exc:
        msg = str(exc)
        check("invalid_name_raises", True, "已拒开工")
        check("invalid_name_lists_available", "local_pymupdf" in msg, "错误信息含可用项")
    try:
        engines.get_engine("bogus_kind")
        check("invalid_kind_raises", False, "竟然没报错")
    except SystemExit:
        check("invalid_kind_raises", True)

    # 2) 配置
    print("\n2) 配置加载")
    cfg = engines.load_config()
    check("config_has_all_kinds", all(k in cfg for k in engines.KINDS), str(cfg))
    check("config_file_exists", engines.config_path().exists(), str(engines.config_path()))

    # 3) 能力声明
    print("\n3) 能力声明与自检")
    rows = engines.preflight_report()
    check("preflight_returns_three", len(rows) == 3, str([(k, n) for k, n, _ in rows]))
    engs = engines.all_engines()
    for kind, eng in engs.items():
        spec = eng.spec
        ok = bool(spec.kind == kind and spec.name and spec.egress in eg.EGRESS_LEVELS)
        check("spec_valid_%s" % kind, ok, eng.describe())

    # 3b) 解析引擎：宽松许可版必须"默认可用、接口与 PyMuPDF 版一致"
    #     这条守的是**分发自由**：默认引擎含 AGPL 组件的话，打包分发应用就会受 AGPL 约束。
    print("\n3b) 解析引擎：默认必须是宽松许可实现，且接口与 PyMuPDF 版一致")
    check("默认 parser 是 local_permissive（宽松许可）",
          engines.DEFAULTS["parser"] == "local_permissive", engines.DEFAULTS["parser"])
    check("配置里的 parser 也是宽松实现",
          engines.load_config().get("parser") == "local_permissive",
          str(engines.load_config().get("parser")))
    _avail = engines.available("parser")
    check("两个 parser 实现都在注册表里", set(_avail) >= {"local_permissive", "local_pymupdf"},
          str(_avail))
    for _n in ("local_permissive", "local_pymupdf"):
        _e = engines.get_engine("parser", _n)
        _spec = _e.spec
        check(f"{_n} 自我声明合规", _spec.kind == "parser" and _spec.egress == "none",
              _spec.describe())
        # 句柄接口必须一致：上层组装逻辑（parse_docs.parse_pdf）只认这四个成员
        for _m in ("supports", "parse", "open_handle", "close_handle"):
            check(f"{_n} 有 {_m}()", callable(getattr(_e, _m, None)))
    # 宽松实现不得依赖 AGPL 组件 —— 这是它存在的全部理由
    _pm_specs = " ".join(engines.get_engine("parser", "local_permissive").spec.python_pkgs)
    check("宽松实现不依赖 pymupdf", "pymupdf" not in _pm_specs, _pm_specs)

    fake = {"parser": _FakeCloud()}
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        try:
            eg.require_consent(fake, td)
            check("egress_without_consent_stops", False, "竟然放行了")
        except SystemExit as exc:
            m = str(exc)
            check("egress_without_consent_stops", True, "已拒开工")
            check("egress_prints_manifest", "document" in m and "有权上传" in m, "清单+风险提示都在")
        eg.grant(td, fake, "测试用同意")
        try:
            eg.require_consent(fake, td)
            check("egress_after_grant_passes", True, str(eg.consent_file(td).name))
        except SystemExit:
            check("egress_after_grant_passes", False, "同意后仍被拦")
        data = json.loads(eg.consent_file(td).read_text(encoding="utf-8"))
        check("consent_recorded_with_note", bool(data.get("granted")), str(list(data.get("granted", {}))))

    # 5) 同意文件位置与忽略规则
    print("\n5) 同意文件必须落在仓库外、且被忽略规则覆盖")
    p = eg.consent_file(kb.CONFIG_DIR)
    check("consent_in_config_dir", str(p).endswith(".egress-consent.json"), str(p))
    gi_path = ROOT / ".gitignore"
    if not gi_path.exists():
        # 在一个尚未初始化版本库的工作副本里，.gitignore 本来就该由同步清单送过来；
        # 文件缺失时**不能直接崩**（崩了只会看到 FileNotFoundError，看不出这是"同步清单漏了两个文件"）。
        check("consent_gitignored", False,
              f"★ {gi_path} 不存在：同步清单 GENERAL_GLOBS 必须包含 .gitignore/.gitattributes"
              f"（否则另一台机器永远收不到忽略规则）")
    else:
        gi = gi_path.read_text(encoding="utf-8")
        check("consent_gitignored", ".egress-consent.json" in gi,
              "（.gitignore 必须显式覆盖该文件；写宽断言会被别的规则蒙过去）")

    # 6) 当前配置不出境
    print("\n6) 当前配置的出境状态")
    check("local_config_has_no_egress", not eg.any_egress(engs),
          "全部本地引擎 → 无数据出境" if not eg.any_egress(engs) else "★当前配置含出境引擎")

    # 7) 同步清单完整性
    #    第 5) 节只盯 .gitignore 一个文件；这里把要求**通用化**成"每台协作机器都需要的
    #    根级文件必须被清单覆盖"—— 以后新增任何根级文件（LICENSE、说明文档……）漏加清单
    #    都会在这里变红，而不是等到另一台机器上以 FileNotFoundError 的面目出现。
    print("\n7) 同步清单必须覆盖「每台协作机器都需要的根级文件」")
    import importlib.util
    spec = importlib.util.spec_from_file_location("firekb_sync", ROOT / "tools" / "sync.py")
    sync_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync_mod)
    covered = set()
    for g in sync_mod.GENERAL_GLOBS:
        for p in ROOT.glob(g):
            if p.is_file():
                covered.add(p.relative_to(ROOT).as_posix())
    required = ["firekb.py", ".gitignore", ".gitattributes", "LICENSE",
                "THIRD-PARTY-NOTICES.md", "CHANGELOG.md", "README.md"]
    missing = [r for r in required if r not in covered]
    check("sync_covers_required_root_files", not missing,
          "全部覆盖（%d 个必需文件）" % len(required) if not missing
          else "★ GENERAL_GLOBS 漏了：%s —— 另一台机器永远收不到这些文件" % missing)

    # 8) 个性化数据绝不出境
    #    "同意"回答的是"有没有权利上传"（版权），与个性化数据是两回事 ——
    #    个性化数据（录音/转写/学习痕迹）必须走**硬规则**：同意也无效。
    #    这条测试的关键是**把同意与临时标志都给了，仍必须被拒**。
    print("\n8) 个性化数据绝不出境（硬规则，同意也不放行）")
    check("NEVER 集合含 audio/transcript/personal",
          eg.NEVER_EGRESS == {"audio", "transcript", "personal"}, str(sorted(eg.NEVER_EGRESS)))
    check("is_never：audio", eg.is_never("audio"))
    check("is_never：transcript", eg.is_never("transcript"))
    check("is_never：personal", eg.is_never("personal"))
    check("is_never：document 不是 NEVER（第三方出版内容可经同意出境）", not eg.is_never("document"))
    check("is_never：none 不是 NEVER", not eg.is_never("none"))
    check("个性化级别在 EGRESS_LEVELS 里有说明", "personal" in eg.EGRESS_LEVELS)

    class _FakeAsr(BaseEngine):
        spec = EngineSpec(kind="asr", name="fake_cloud_asr", label="假云端转写（测试用）",
                          python_pkgs=("json",), needs_network=True, needs_api_key=("FAKE_KEY",),
                          egress="audio", note="测试：会把课堂录音发往第三方")

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        fake_asr = _FakeAsr()
        # 先给足"放行条件"：写入同意文件 + allow_flag=True —— 两者都不该救得回来
        eg.grant(tdp, {"asr": fake_asr}, "测试：我确实有权上传")
        consent = eg.load_consent(tdp).get("granted", {})
        check("grant() 拒绝为个性化引擎记录同意", not consent, str(consent))
        try:
            eg.require_consent({"asr": fake_asr}, tdp, allow_flag=True)
            check("个性化引擎：有同意+--allow-egress 仍必须拒跑", False, "竟然放行了")
        except SystemExit as exc:
            msg = str(exc)
            check("个性化引擎：有同意+--allow-egress 仍必须拒跑", True, "已拒开工")
            check("拒绝信息点明这是硬规则（不是授权问题）", "硬规则" in msg)
            check("拒绝信息给出处置办法", "engines.json" in msg)
        check("never_engines 能识别出该引擎",
              [r["engine"] for r in eg.never_engines({"asr": fake_asr})] == ["fake_cloud_asr"])
        # 对照组：第三方出版内容仍走原同意流程（未同意 → 拒；同意 → 放行）
        fake_doc = _FakeCloud()
        try:
            eg.require_consent({"parser": fake_doc}, tdp)
            check("对照：第三方出版内容未同意 → 仍拒跑", False, "竟然放行")
        except SystemExit:
            check("对照：第三方出版内容未同意 → 仍拒跑", True, "已拒开工")
        eg.grant(tdp, {"parser": fake_doc}, "测试：有权上传教材")
        try:
            eg.require_consent({"parser": fake_doc}, tdp)
            check("对照：第三方出版内容同意后 → 放行", True, "已放行")
        except SystemExit:
            check("对照：第三方出版内容同意后 → 放行", False, "同意后仍被拦")
    check("当前配置里没有个性化出境引擎（应为空）", not eg.never_engines(engs),
          str([r["engine"] for r in eg.never_engines(engs)]))

    # 9) 第三方版权书目的**书面禁令**
    #    这条要求的是"书面表示禁止 + 实际不做限制"，所以测试有两面：
    #    ① 放行提示里必须出现禁令原文（否则等于没写）；
    #    ② **不能**因此拦命令（同意后仍要放行，否则就变成技术限制了）。
    print("\n9) 第三方版权书目：书面禁令必须在场，且**不做技术限制**")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        doc = _FakeCloud()
        eg.grant(tdp, {"parser": doc}, "测试：有权上传教材")
        try:
            eg.require_consent({"parser": doc}, tdp)
            check("第三方出版内容同意后仍放行（不做技术限制）", True, "已放行")
        except SystemExit as exc:
            check("第三方出版内容同意后仍放行（不做技术限制）", False, "被拦了 —— 那就是技术限制")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        doc = _FakeCloud()
        try:
            eg.require_consent({"parser": doc}, tdp)
            check("未同意时会给出禁令人读文本", False, "竟然放行")
        except SystemExit as exc:
            msg = str(exc)
            check("未同意时会给出禁令人读文本", True, "已拒开工")
            check("禁令含「未获得版权」", "未获得版权" in msg, )
            check("禁令含「后果自负」", "后果自负" in msg)
            check("明示**不做技术限制**（避免给人'已合规'的错觉）", "不做技术限制" in msg)
    # 豁免路径：--allow-egress 照常有效（这也是"不做技术限制"的一部分）
    with tempfile.TemporaryDirectory() as td:
        try:
            eg.require_consent({"parser": _FakeCloud()}, Path(td), allow_flag=True)
            check("--allow-egress 对版权书目照常有效（非技术限制）", True, "已放行")
        except SystemExit:
            check("--allow-egress 对版权书目照常有效（非技术限制）", False, "被拦了")
    # 对照：同一开关对**个性化数据**必须无效（两类数据的处理必须不同）
    with tempfile.TemporaryDirectory() as td:
        try:
            eg.require_consent({"asr": _FakeAsr()}, Path(td), allow_flag=True)
            check("对照：--allow-egress 对个性化数据**无效**", False, "竟然放行")
        except SystemExit:
            check("对照：--allow-egress 对个性化数据**无效**", True, "已拒开工")

    # 10) 版本声明的一致性
    #     为什么值得单测：版本号散落在**四个地方**（代码常量 / 更新日志标题 /
    #     公开 README 的提示条 / `--version` 输出）。三处写 0.1α、一处忘改，
    #     使用者看到的就自相矛盾 —— 这种漂移不会报错，只会让"到底哪个版本"变成悬案。
    #     而且 alpha 阶段的**不承诺向后兼容**必须**同时**出现在代码与面向使用者的
    #     文档里：只写在代码注释里，等于没告诉使用者。
    print("\n10) 版本声明一致（代码 / 更新日志 / 公开 README）")
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("firekb_entry", ROOT / "firekb.py")
    fk = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(fk)
    check("__version__ 是 PEP 440 形态", bool(re.fullmatch(r"\d+\.\d+\.\d+(-[a-z0-9.]+)?", fk.__version__)),
          fk.__version__)
    check("__version__ 与 __version_display__ 同源（0.1.0-alpha ↔ 0.1α）",
          fk.__version__ == "0.1.0-alpha" and fk.__version_display__ == "0.1α",
          "%s / %s" % (fk.__version__, fk.__version_display__))
    check("__release_status__ 明示 alpha", fk.__release_status__ == "alpha", fk.__release_status__)
    check("__compat_policy__ 明示不承诺向后兼容",
          fk.__compat_policy__ == "no-backward-compatibility-guarantee", fk.__compat_policy__)

    _cl = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    check("更新日志有该版本的条目标题", ("## [%s]" % fk.__version_display__) in _cl,
          "## [%s]" % fk.__version_display__)
    check("更新日志写明不承诺向后兼容", "不承诺向后兼容" in _cl)
    check("更新日志逐类列出可能破坏的东西",
          all(k in _cl for k in ("配置格式", "产物契约", "CLI 参数", "目录结构", "状态文件")))

    # 公开 README 在两个地方以**不同的名字**存在，两个都得认：
    #   私有仓   docs/README_public.md（发布源）
    #   公开快照 README.md            （发布时按 DOC_MAP 改名，见 tools/publish_public.py）
    # 早先只认前者 ⇒ 公开仓库里必然 FileNotFoundError ⇒ 整个自测中断 ⇒ doctor 的
    # selftest 硬门槛永远失败（用户还无法修复：文件在仓库里根本没有）。
    # **自测不该因为"换了个同样合法的布局"就炸掉。**
    _pub_path = ROOT / "docs" / "README_public.md"
    if not _pub_path.exists():
        _pub_path = ROOT / "README.md"
    if not _pub_path.exists():
        check("公开 README 存在（docs/README_public.md 或 README.md）", False,
              "两处都没有 —— 发布快照缺公开文档")
    else:
        _pub = _pub_path.read_text(encoding="utf-8")
        check("公开 README 带版本提示", fk.__version_display__ in _pub or "0.1.0-alpha" in _pub)
        check("公开 README 告知这是测试版", "测试版" in _pub)
        check("公开 README 告知可能有破坏兼容的更新", "破坏兼容" in _pub)

    print("\n" + "=" * 84)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：" + "、".join(FAIL))
    print("=" * 84)
    return 1 if FAIL else 0


if __name__ == "__main__":
    # 自测**自己崩了**时也必须给出汇总：原来异常直接冒泡，用户只看到一段 traceback，
    # 不知道"究竟已经通过了多少项、是从哪一项开始不对劲的"（实测：公开仓库里因为缺一个
    # 文档文件排在后面的 3 项，报告读起来像是"自测有 60 项失败"，其实是**没跑到**）。
    try:
        code = main()
    except BaseException as exc:
        print("\n" + "=" * 84)
        print(f"[FAIL] 自测未跑完：{type(exc).__name__}: {exc}")
        print(f"       已通过 {len(PASS)} 项，失败 {len(FAIL)} 项（其后的检查没有执行）")
        print("=" * 84)
        raise
    raise SystemExit(code)
