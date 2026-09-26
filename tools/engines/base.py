# -*- coding: utf-8 -*-
"""
FireKB 引擎抽象层 · 基类与能力声明
==================================
把「依赖重、许可敏感、可云端化」的三类能力抽成接口：

    parser  —— 把素材文件（PDF/PPTX/EPUB）解成页级文本          （现用 pymupdf = AGPL / python-pptx）
    ocr     —— 把「页面图像」识别成文本                        （现用 rapidocr-onnxruntime）
    asr     —— 把「音频」转成带时间戳的文本                     （现用 faster-whisper + ffmpeg）

为什么要有这一层：
  1. **合规**：现用实现里有 AGPL（pymupdf）、GPL（捆绑 ffmpeg）、专有库（NVIDIA）。
     把实现做成"可替换的 provider"后，用户可自选云端 API（自带 key），
     项目本身不必分发这些依赖 —— 分发义务与数据处理者身份都随之下移给使用者。
  2. **可装配的形态**：单机全流程 / 多机分工 / 便携版 等部署形态，
     差别只在 engines.json 选哪个 provider，不需要改流水线代码。
  3. **诚实的能力声明**：每个引擎必须**自己声明需要什么**
     （python 包 / 外部二进制 / GPU / 网络 / 密钥），由 `preflight()` 逐项核对。
     缺什么必须当场说清，而不是跑到一半崩掉（fail-loud 纪律）。

⚠️ 本层只做「装配与声明」，**不重写算法**：本地 provider 通过函数级惰性导入调用既有实现，
   保证行为与既有产物逐字节一致（要改口径，必须另做对比实验）。
"""
from __future__ import annotations

import importlib
import os
import shutil
from dataclasses import dataclass, field

ROOT = None  # 由 __init__.py 注入（避免与 kb 循环导入）


@dataclass
class EngineSpec:
    """一台引擎的自我声明。缺项由 preflight() 报出，不靠文档承诺。"""

    kind: str                      # parser | ocr | asr
    name: str                      # 唯一标识，写进 engines.json
    label: str = ""                # 人读名字
    python_pkgs: tuple = ()        # 需要可导入的 python 包
    binaries: tuple = ()           # 需要在 PATH（或项目内）找到的可执行文件
    needs_gpu: bool = False        # 是否必须 GPU（False = 有则用、无则降级）
    needs_network: bool = False    # 是否必须联网
    needs_api_key: tuple = ()      # 需要哪些密钥（从 00_配置/.env 读）
    egress: str = "none"           # 数据出境级别，见 egress.py
    note: str = ""                 # 许可/来源等提示，会打印给用户

    def describe(self) -> str:
        bits = [f"{self.kind}:{self.name}"]
        if self.label:
            bits.append(self.label)
        caps = []
        if self.python_pkgs:
            caps.append("包=" + ",".join(self.python_pkgs))
        if self.binaries:
            caps.append("二进制=" + ",".join(self.binaries))
        if self.needs_gpu:
            caps.append("需GPU")
        if self.needs_network:
            caps.append("需联网")
        if self.needs_api_key:
            caps.append("密钥=" + ",".join(self.needs_api_key))
        caps.append("出境=" + self.egress)
        bits.append("[" + " ".join(caps) + "]")
        return " ".join(bits)


class BaseEngine:
    """所有引擎的基类：只要求自报家门 + 自检 + 说明。"""

    spec: EngineSpec

    # ---- 自检 -------------------------------------------------------------
    def preflight(self, root=None) -> list[str]:
        """返回**缺失项**列表；空列表 = 可用。调用方必须把它当硬条件（fail-loud）。"""
        miss: list[str] = []
        for pkg in self.spec.python_pkgs:
            try:
                importlib.import_module(pkg)
            except Exception as exc:
                miss.append(f"python 包 {pkg} 不可导入（{type(exc).__name__}）")
        for b in self.spec.binaries:
            if find_binary(b, root) is None:
                miss.append(f"找不到可执行文件 {b}")
        for key in self.spec.needs_api_key:
            if not os.environ.get(key):
                miss.append(f"未配置密钥 {key}（应放在 00_配置/.env）")
        return miss

    def describe(self) -> str:
        return self.spec.describe()

    # ---- 各角色实现 -------------------------------------------------------
    def parse(self, path, **kw):                       # parser
        raise NotImplementedError

    def recognize(self, image, **kw):                  # ocr
        raise NotImplementedError

    def transcribe(self, path, **kw):                  # asr
        raise NotImplementedError


def find_binary(name: str, root=None) -> str | None:
    """先找项目内 `tools/<name>/bin/`（把 ffmpeg 之类装在这里可免去配 PATH），再找 PATH。"""
    root = root or ROOT
    if root:
        cands = [
            os.path.join(str(root), "tools", name, "bin", name + ".exe"),
            os.path.join(str(root), "tools", name, "bin", name),
        ]
        for c in cands:
            if os.path.exists(c):
                return c
    return shutil.which(name)
