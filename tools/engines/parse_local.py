# -*- coding: utf-8 -*-
"""
本地解析引擎（PDF / PPTX / EPUB）
=================================
**实现不搬**：解析核心仍在 `parse_docs.py`（本层只建立接口与装配，
以保证与既有产物逐字节一致；把实现物理搬过来属于纯整理，可留待以后再做）。

本引擎同时负责"把页面渲染成图像"这一能力（原 `ocr_pages.render_page_array` 的职责）——
因为只有解析侧才知道怎么打开文档，OCR 侧应只吃图像。
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from engines.base import BaseEngine, EngineSpec


class PdfHandle:
    """对"已打开的 PDF"的最小抽象：页数 / 取文本 / 渲染成图像。"""

    def __init__(self, doc):
        self._doc = doc

    def __len__(self) -> int:
        return self._doc.page_count

    @property
    def encrypted(self) -> bool:
        """文档是否需要密码（上层据此决定是"报一条警告并跳过"还是"继续抽"）。"""
        try:
            return bool(self._doc.needs_pass)
        except Exception:
            return False

    def text_of(self, page_no: int) -> str:
        return self._doc[page_no - 1].get_text("text") or ""

    def render_array(self, page_no: int, dpi: int = 200):
        """渲染成 RGB 的 numpy 数组（h, w, 3）——OCR 侧的输入。"""
        import numpy as np
        import pymupdf
        pix = self._doc[page_no - 1].get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8)
        return arr.reshape(pix.height, pix.width, pix.n)[:, :, :3]


class LocalPymupdfParser(BaseEngine):
    spec = EngineSpec(
        kind="parser",
        name="local_pymupdf",
        label="本地解析：PyMuPDF + python-pptx + 标准库 zip（EPUB）",
        python_pkgs=("pymupdf", "pptx"),
        egress="none",
        note="pymupdf 为 AGPL-3.0 双许可（Artifex 商业许可可选）；本项目不随包分发它。",
    )

    SUFFIX_FN = {".pdf": "parse_pdf", ".pptx": "parse_pptx", ".epub": "parse_epub"}

    def supports(self, suffix: str) -> bool:
        return suffix.lower() in self.SUFFIX_FN

    def parse(self, path, **kw):
        """→ (pages, warnings)，与 parse_docs.parse_* 的返回契约完全一致。"""
        suffix = Path(path).suffix.lower()
        fn_name = self.SUFFIX_FN.get(suffix)
        if not fn_name:
            raise ValueError(f"本地解析引擎不支持的后缀：{suffix}")
        import parse_docs as impl          # 函数级惰性导入：避免循环依赖，且保证实现唯一
        return getattr(impl, fn_name)(Path(path))

    @contextmanager
    def open(self, path):
        """打开 PDF 供 OCR 流程逐页取图/取文本；非 PDF 抛错。"""
        if Path(path).suffix.lower() != ".pdf":
            raise ValueError("open() 只支持 PDF（OCR 只处理 PDF 的扫描页）")
        import pymupdf
        with pymupdf.open(str(path)) as doc:
            yield PdfHandle(doc)

    # ---- 非 with 语法的调用方（既有 ocr_pages 循环体较长，避免大改）----
    def open_handle(self, path) -> PdfHandle:
        """打开文档并返回句柄；调用方负责调用 close_handle()。"""
        if Path(path).suffix.lower() != ".pdf":
            raise ValueError("open_handle() 只支持 PDF")
        import pymupdf
        return PdfHandle(pymupdf.open(str(path)))

    def close_handle(self, handle) -> None:
        try:
            handle._doc.close()
        except Exception:
            pass
