# -*- coding: utf-8 -*-
"""
FireKB 引擎层 · 注册表与配置
============================
用法（流水线脚本内部）：

    from engines import get_engine, all_engines
    parser = get_engine("parser")          # 由 00_配置/engines.json 决定用哪台
    pages, warnings = parser.parse(path)

命令行自检：

    python tools\\engines_check.py          # 列出配置 / 能力 / 出境情况，并逐台 preflight

设计要点：
  · **配置驱动**：三台引擎（parser/ocr/asr）各由 engines.json 选一台实现，
    流水线代码不认识具体实现 —— 这正是不同部署形态只差配置的原因。
  · **能力自报**：每台引擎声明自己需要什么（包/二进制/GPU/网络/密钥），preflight() 逐项核对，
    缺什么当场报出来（fail-loud）。未注册的名字 **直接报错并列出可用项**，不静默回退。
  · **不重复定义路径常量**：ROOT/CONFIG_DIR 一律向共享层 `kb` 取。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 让 `import engines.*` 能在 tools/ 已被加入 sys.path 的环境里工作（各脚本都会加）
_THIS = Path(__file__).resolve().parent
if str(_THIS.parent) not in sys.path:
    sys.path.insert(0, str(_THIS.parent))

import kb  # noqa: E402  共享层：只提供路径常量与 IO，无重依赖

KINDS = ("parser", "ocr", "asr")

# 默认实现（engines.json 缺项时使用）
#
# parser 默认选 `local_pdfium`（pypdfium2，宽松许可）而不是 `local_pymupdf`（AGPL）：
# 默认值决定了**打包分发应用时是否受 AGPL 约束** —— 默认走宽松实现，
# 这条最常用的路径才是通的。要用 PyMuPDF 就在 engines.json 里显式选它。
DEFAULTS = {
    "parser": "local_permissive",
    "ocr": "local_rapidocr",
    "asr": "local_faster_whisper",
}

# (kind, name) -> (模块, 类名)
REGISTRY = {
    ("parser", "local_permissive"): ("engines.parse_permissive", "LocalPermissiveParser"),
    ("parser", "local_pymupdf"): ("engines.parse_local", "LocalPymupdfParser"),
    ("ocr", "local_rapidocr"): ("engines.ocr_local", "LocalRapidOcrEngine"),
    ("asr", "local_faster_whisper"): ("engines.asr_local", "LocalFasterWhisperAsr"),
}

CONFIG_NAME = "engines.json"

# 供 base.find_binary 定位项目内二进制（如 tools/ffmpeg/bin/ffmpeg.exe）
import engines.base as _base  # noqa: E402
_base.ROOT = kb.ROOT


def config_path() -> Path:
    return Path(kb.CONFIG_DIR) / CONFIG_NAME


def load_config() -> dict:
    """读 00_配置/engines.json；缺文件/缺项一律用 DEFAULTS 补齐（并在自检里提示）。"""
    cfg = dict(DEFAULTS)
    p = config_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for k in KINDS:
                v = (data or {}).get(k)
                if isinstance(v, str) and v.strip():
                    cfg[k] = v.strip()
        except Exception as exc:
            raise SystemExit(f"[FAIL] 引擎配置无法解析：{p}（{type(exc).__name__}: {exc}）")
    return cfg


def available(kind: str) -> list[str]:
    return sorted(n for (k, n) in REGISTRY if k == kind)


def get_engine(kind: str, name: str | None = None):
    """按 kind 取引擎实例。名字非法**立即报错并列出可用项**（不静默回退到默认）。"""
    if kind not in KINDS:
        raise SystemExit(f"[FAIL] 未知引擎类别：{kind}（可用：{'/'.join(KINDS)}）")
    name = name or load_config().get(kind) or DEFAULTS[kind]
    key = (kind, name)
    if key not in REGISTRY:
        raise SystemExit(
            f"[FAIL] 未注册的 {kind} 引擎：{name}\n"
            f"       可用：{'、'.join(available(kind))}\n"
            f"       配置文件：{config_path()}")
    mod_name, cls_name = REGISTRY[key]
    mod = __import__(mod_name, fromlist=[cls_name])
    return getattr(mod, cls_name)()


def all_engines() -> dict:
    return {k: get_engine(k) for k in KINDS}


def get_engines(*kinds: str) -> dict:
    """
    只取**本环节真正会用到**的引擎（不取全量）。

    为什么必须按环节取：`all_engines()` 会把解析 / OCR / 转写三类一起拿到，
    再交给 `require_ready()` 就成了"任一缺件即拒绝开工"。于是只装了解析依赖的
    用户跑 `parse`（**根本用不到 OCR 与转写**）也会被拒 —— 而那些依赖在文档里
    标的是「可选」。**可选的东西不该成为别的环节的硬前置。**
    """
    if not kinds:
        raise SystemExit("[FAIL] get_engines() 必须显式指定引擎类别（不传就是全量，见 all_engines()）")
    return {k: get_engine(k) for k in kinds}


def preflight_report(engines: dict | None = None) -> list[tuple[str, str, list[str]]]:
    """→ [(kind, engine_name, 缺失项列表)]；缺失项为空即该引擎可用。"""
    engs = engines or all_engines()
    out = []
    for kind, eng in engs.items():
        out.append((kind, eng.spec.name, eng.preflight()))
    return out


def require_ready(engines: dict | None = None) -> None:
    """开工前硬检查：任一引擎缺件 → 打印清单并退出（fail-loud，不半路崩）。

    传进来的 `engines` 应当是**本环节真正用到的子集**（用 `get_engines("parser")` 取），
    不要图省事传 `all_engines()` —— 那会把别的环节的可选依赖变成这里的硬门槛。
    """
    rows = preflight_report(engines)
    bad = [(k, n, m) for (k, n, m) in rows if m]
    if not bad:
        return
    lines = ["", "=" * 84, "[FAIL] 引擎依赖不满足，拒绝开工：", "=" * 84]
    for kind, name, miss in bad:
        lines.append(f"  · {kind}:{name}")
        for m in miss:
            lines.append(f"      - {m}")
    lines += ["",
              "  处理方式：按提示安装依赖，或改 00_配置/engines.json 换成可用的引擎",
              "  查看全部引擎与出境情况：python tools\\engines_check.py",
              "  注：这里列出的只是**本环节用到的**引擎；别的环节（如转写/OCR）的依赖"
              "不影响本环节。", "=" * 84, ""]
    raise SystemExit("\n".join(lines))


__all__ = ["KINDS", "DEFAULTS", "REGISTRY", "config_path", "load_config", "available",
           "get_engine", "all_engines", "preflight_report", "require_ready"]
