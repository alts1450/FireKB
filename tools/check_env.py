# -*- coding: utf-8 -*-
"""
环境自检 —— 跑任何模块之前先跑这个
====================================
用法：python tools\\check_env.py

检查：Python 版本与架构、必需库、目录结构、磁盘空间、API 配置状态。
不修改任何东西，纯只读检查。
"""

from __future__ import annotations

import importlib
import json
import os
import platform
import shutil
import sys
from pathlib import Path

try:  # 保证中文输出在 Windows 控制台/管道下不崩
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(os.environ.get("FIREKB_ROOT") or Path(__file__).resolve().parents[1])
REQUIRED_DIRS = [
    "00_配置",
    "10_原始归档",
    "10_原始归档/课件",
    "10_原始归档/规范教材",
    "10_原始归档/录音",
    "20_文本库",
    "30_知识点",
    "40_复习产物",
    "90_日志",
    "tools",
]
# 解析依赖**跟随配置**，不写死。
#
# 为什么必须跟着配置走：默认引擎 `local_permissive` 用的是 pypdf + pypdfium2，
# 根本不需要 pymupdf。早先把 pymupdf 写成"必需"，后果是三重的：
#   ① 照 README 装完依赖的用户，`doctor` 的 env 硬门槛**永远失败**，README 承诺的
#      「0 = 全部通过」不可能达成；
#   ② 报错提示会把人推向安装 AGPL-3.0 组件，与 README「默认依赖栈全部宽松许可 ⇒
#      打包分发自由」的承诺直接冲突 —— 用户陷入"照做也不对、不做也不对"；
#   ③ 索引（引擎选择）与体检（依赖清单）分了家，以后换引擎还会再漂一次。
#   ⇒ **体检清单从索引推出来**，两边就不可能不一致。
PARSER_REQUIRED = {
    "local_permissive": [
        ("pypdf", "PDF 文字层（默认引擎 local_permissive）"),
        ("pypdfium2", "PDF 渲染（默认引擎 local_permissive）"),
    ],
    "local_pymupdf": [
        ("pymupdf", "PDF 解析（**可选**引擎 local_pymupdf · AGPL-3.0，需显式启用）"),
    ],
}
DEFAULT_PARSER = "local_permissive"
BASE_REQUIRED = [
    ("pptx", "PPTX 解析（M2）"),
    ("zipfile", "EPUB 解析（标准库）"),
    ("html.parser", "EPUB 正文抽取（标准库）"),
    ("json", "契约文件读写（标准库）"),
    ("urllib.request", "调用云端 API（标准库）"),
    ("sqlite3", "本地全文索引（标准库）"),
    ("hashlib", "文件校验（标准库）"),
]


def parser_engine_name() -> str:
    """当前选中的解析引擎（与 `00_配置/engines.json` 同源；缺文件/缺项用默认值）。"""
    p = ROOT / "00_配置" / "engines.json"
    if p.exists():
        try:
            v = (json.loads(p.read_text(encoding="utf-8")) or {}).get("parser")
            if isinstance(v, str) and v.strip():
                return v.strip()
        except Exception:
            pass
    return DEFAULT_PARSER


def required_modules() -> tuple[list[tuple[str, str]], str]:
    name = parser_engine_name()
    mods = PARSER_REQUIRED.get(name)
    if mods is None:                      # 自定义引擎：不替它猜依赖，只报基础项
        mods = []
    return mods + BASE_REQUIRED, name

OK = "[ OK ]"
BAD = "[FAIL]"
WARN = "[WARN]"
failures: list[str] = []
warnings: list[str] = []


def section(title: str) -> None:
    print("\n" + "-" * 62)
    print(title)
    print("-" * 62)


def main() -> int:
    print("=" * 62)
    print(" 消防工程 AI 电子课本 · 环境自检")
    print("=" * 62)

    # ---------- Python ----------
    section("1. Python 运行时")
    ver = sys.version_info
    print(f"版本   : {platform.python_version()}")
    print(f"架构   : {platform.machine()}")
    print(f"解释器 : {sys.executable}")
    if ver < (3, 10):
        failures.append("Python 版本过低（需要 3.10+）")
        print(f"{BAD} 需要 Python 3.10 及以上")
    else:
        print(f"{OK} 版本满足要求")
    if "ARM" in platform.machine().upper():
        print(f"{WARN} 检测到 ARM64：请勿在本机安装 torch 等重型库（wheel 常缺失），")
        print("       本机定位是「解析 + 调 API」，重算力放到另一台机器。")
        warnings.append("ARM64 平台，避免重型依赖")

    # ---------- 依赖 ----------
    section("2. 依赖库")
    mods, pname = required_modules()
    print(f"  解析引擎：{pname}（取自 00_配置/engines.json）—— 下面按它校验对应依赖")
    for mod, purpose in mods:
        try:
            importlib.import_module(mod)
            print(f"{OK} {mod:<16} {purpose}")
        except Exception as exc:
            print(f"{BAD} {mod:<16} {purpose}  -> {type(exc).__name__}: {exc}")
            failures.append(f"缺少依赖 {mod}（{purpose}）")

    # ---------- 可选依赖 ----------
    # 只提示、不计失败：这些依赖各自只服务某一个环节（扫描页 OCR / 录音转写 / 合成样例），
    # 而各环节的 preflight 只会校验**自己用到的**引擎 —— 所以缺了它们不影响别的环节开工。
    print()
    for mod, purpose, hint in (
        ("rapidocr_onnxruntime", "扫描页 OCR（M2，仅影印版教材需要）", "rapidocr-onnxruntime"),
        ("numpy", "OCR 图像处理（M2 依赖它）", "numpy"),
        ("faster_whisper", "录音转写（M3）", "faster-whisper"),
        ("ctranslate2", "录音转写（M3 依赖它）", "ctranslate2"),
        ("reportlab", "生成合成样例（firekb testdata）", "reportlab"),
    ):
        try:
            importlib.import_module(mod)
            print(f"{OK} {mod:<20} {purpose}")
        except Exception:
            print(f"{WARN} {mod:<20} {purpose} —— 未安装（不影响其它环节）")
            print(f"       需要时装：python -m pip install {hint}")
    if shutil.which("ffmpeg"):
        print(f"{OK} {'ffmpeg':<20} 录音转写需要（**必须在 PATH 里**）")
    else:
        print(f"{WARN} {'ffmpeg':<20} 未在 PATH 中找到 —— 只有 M3 转写需要它")
        print("       注意：它必须以命令形式可用（装完记得让 PATH 生效），否则转写无法开工")

    # ---------- 目录 ----------
    section("3. 目录结构")
    for rel in REQUIRED_DIRS:
        p = ROOT / rel
        if p.is_dir():
            print(f"{OK} {p}")
        else:
            print(f"{BAD} 缺失：{p}")
            failures.append(f"目录缺失：{p}")

    # ---------- 磁盘 ----------
    section("4. 磁盘空间")
    try:
        usage = shutil.disk_usage(str(ROOT.drive + "\\") if ROOT.drive else str(ROOT))
        free_gb = usage.free / 1024**3
        print(f"{ROOT.drive} 可用空间：{free_gb:.1f} GB")
        if free_gb < 5:
            print(f"{BAD} 空间不足。注意：音频原始文件请勿放在本机！")
            failures.append("本机磁盘空间不足")
        elif free_gb < 20:
            print(f"{WARN} 空间偏紧。本机只应存放文本与产出，音频另行存放。")
            warnings.append("磁盘空间偏紧")
        else:
            print(f"{OK} 空间充足（本机只存文本与产出）")
    except Exception as exc:
        print(f"{WARN} 无法读取磁盘信息：{exc}")

    # ---------- API 配置 ----------
    section("5. API 配置")
    env_file = ROOT / "00_配置" / ".env"
    if not env_file.exists():
        print(f"{BAD} 配置文件不存在：{env_file}")
        print("       请复制 00_配置\\.env.example 为 00_配置\\.env 并填写 Key")
        warnings.append("尚未创建 .env")
    else:
        print(f"{OK} 配置文件存在：{env_file}")
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            import llm  # type: ignore

            cfg = llm.load_env(env_file)
            if llm.has_valid_key(cfg):
                key = cfg.get("DEEPSEEK_API_KEY", "")
                print(f"{OK} API Key 已配置（{key[:6]}...{key[-4:]}，长度 {len(key)}）")
                print(f"     BASE_URL : {cfg.get('DEEPSEEK_BASE_URL') or '(默认)'}")
                print(f"     MODEL    : {cfg.get('DEEPSEEK_MODEL') or '(默认)'}")
            else:
                print(f"{WARN} API Key 未填写或仍是占位符 -> 需要调用大模型的模块暂不可用")
                print("       （M2 解析模块不需要 Key，可以先跑起来）")
                warnings.append("API Key 未配置")
        except Exception as exc:
            print(f"{WARN} 无法读取配置：{type(exc).__name__}: {exc}")

    # ---------- 数据现状 ----------
    section("6. 当前数据现状")
    for label, d, exts in (
        ("课件", ROOT / "10_原始归档" / "课件", {".pdf", ".pptx", ".epub"}),
        ("规范教材", ROOT / "10_原始归档" / "规范教材", {".pdf", ".pptx", ".epub"}),
        ("录音", ROOT / "10_原始归档" / "录音", {".m4a", ".mp3", ".wav", ".aac", ".opus", ".amr"}),
    ):
        cnt = 0
        if d.exists():
            cnt = sum(1 for p in d.rglob("*") if p.is_file() and p.suffix.lower() in exts)
        print(f"{label:<8}: {cnt} 个文件   ({d})")

    pages = ROOT / "20_文本库" / "pages.jsonl"
    if pages.exists():
        lines = sum(1 for _ in pages.open("r", encoding="utf-8"))
        print(f"文本库   : {lines} 条页记录  ({pages})")
    else:
        print(f"文本库   : 尚未生成（运行 parse_docs.py 后产生）")

    # ---------- 结论 ----------
    # 「尚未初始化」是**独立的一种状态**，不是失败。
    #
    # 为什么必须分开：刚从仓库 clone 下来时只有代码与模板 —— 数据目录没建、`.env` 没配，
    # 这是**预期状态**。若把它报成"环境失败"，第一次使用的人看到的就是红字，
    # 会以为"这项目是坏的"。**体检不该对"还没配"报警报。**
    # （不分开的话，doctor 会把未初始化报成"硬门槛失败：env"。）
    uninit_dirs = [d for d in REQUIRED_DIRS if not (ROOT / d).exists()]
    if uninit_dirs and not (ROOT / "20_文本库").exists() and not (ROOT / "10_原始归档").exists():
        print("\n" + "=" * 62)
        print("结论：**项目尚未初始化**（这是刚 clone 下来的正常状态，不是故障）")
        print("=" * 62)
        print(f"  缺少 {len(uninit_dirs)} 个目录，且没有找到任何素材/派生数据。")
        print("  初始化：")
        print("      python -m firekb init")
        print("  它会建目录、把 *.example.* 模板复制成真名（幂等，不覆盖已有文件）。")
        print("=" * 62)
        return 7

    print("\n" + "=" * 62)
    if failures:
        print(f"结论：存在 {len(failures)} 项必须修复的问题")
        for f in failures:
            print(f"  - {f}")
    else:
        print("结论：环境就绪，可以开始运行")
    if warnings:
        print(f"\n提示（{len(warnings)} 项，不阻塞）：")
        for w in warnings:
            print(f"  - {w}")
    print("=" * 62)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
