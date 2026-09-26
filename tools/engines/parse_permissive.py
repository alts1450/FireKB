# -*- coding: utf-8 -*-
"""
本地解析引擎（宽松许可）—— PDF 的**无 AGPL**实现
================================================

与 `local_pymupdf` 提供完全相同的接口（打开文档 / 逐页取文字 / 渲染成图像），
但底层全部换成宽松许可的组件：

    · **取文字**：pypdf（BSD-3，纯 Python，无二进制 wheel）
    · **渲染页面**：pypdfium2（Apache-2.0 / BSD-3，随包分发 PDFium 二进制）

## 为什么要有一个不含 AGPL 的实现

PDF 解析这一环决定了**你能否自由分发打包好的应用**：

  · `PyMuPDF` 是 **AGPL-3.0**（或 Artifex 商业许可）。把内含它的程序分发出去，
    整个组合就要按 AGPL 履行义务 —— 想装壳做成免安装应用分发时会撞上这一条。
  · 本引擎用的两个包都是宽松许可，装壳分发不受影响。

它是**默认引擎**，所以"装壳分发"这条路默认就是通的。`local_pymupdf` 仍保留为可选项。

## 为什么是两个库而不是一个（这是量出来的，不是拍的）

拿本项目的**核心指标**——引用核验档位——实测同一本书的 1808 条证据：

    | 取文字实现   | exact | 相对 pymupdf |
    |---|---:|---:|
    | PyMuPDF      | 1687  | 基准         |
    | **pypdf**    | **1688** | **+1（等效）** |
    | pdfminer.six | 1509  | −178         |
    | pypdfium2    | 1367  | −320         |

结论很清楚：**pypdfium2 的取文字质量不足以承载"引用逐字可核验"这个核心要求**
（它会把文字顺序/内容改掉，大量原本 exact 的引用掉成 partial）——
但它的**渲染**又快又好。所以分工：文字交给 pypdf，渲染交给 pypdfium2。

> 换实现时请用同一把尺子复量：`python -m firekb selftest` 只保证接口没坏，
> **指标要靠在真实语料上跑引用核验档位**（字符数差 ±2% 看不出这种退化）。

## 平台说明

pypdf 是纯 Python（任何平台都装得上）；pypdfium2 随包分发 PDFium 二进制，
Windows 官方 wheel 只有 `win32` / `win_amd64`（没有 `win_arm64`）——
ARM64 的 Windows 会装 amd64 版并在模拟下运行，实测可用。
缺件时 `preflight()` 会如实报出，而不是跑到一半才崩。
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from engines.base import BaseEngine, EngineSpec

# PDFium 以 72 dpi 为基准，pypdfium2 的 scale 是"放大倍数"
_BASE_DPI = 72


class PdfHandle:
    """对"已打开的 PDF"的最小抽象：页数 / 取文本 / 渲染成图像 / 是否加密。"""

    def __init__(self, reader, pdfium_doc):
        self._reader = reader          # pypdf.PdfReader —— 取文字
        self._pdfium = pdfium_doc      # pypdfium2.PdfDocument —— 渲染（惰性打开）

    def __len__(self) -> int:
        return len(self._reader.pages)

    @property
    def encrypted(self) -> bool:
        try:
            return bool(self._reader.is_encrypted)
        except Exception:
            return False

    def text_of(self, page_no: int) -> str:
        """
        取该页文字（pypdf）。

        页级异常**不吞**：交给上层 `${parse_docs.parse_pdf}` 记成一条警告并继续下一页 ——
        单页抽不出来不应该让整本书解析失败，但也不能静默当成"这页没字"。
        """
        return self._reader.pages[page_no - 1].extract_text() or ""

    def _render_doc(self):
        """惰性打开渲染用的文档：只解析文字的场景（如纯文本 PDF）不必付这份开销。"""
        if self._pdfium is None:
            import pypdfium2 as pdfium
            self._pdfium = pdfium.PdfDocument(str(self._path))
        return self._pdfium

    def render_array(self, page_no: int, dpi: int = 200):
        """
        渲染成 RGB 的 numpy 数组（h, w, 3）—— OCR 侧的输入。

        走 PIL 中转（而不是直接取 PDFium 原始缓冲）：PDFium 内部是 BGRA，
        经 PIL 统一成 RGB，与 `local_pymupdf` 那条路径的通道顺序一致 ——
        OCR 引擎拿到的是同一种颜色约定，换引擎不必调参数。
        """
        import numpy as np
        doc = self._render_doc()
        page = doc[page_no - 1]
        bitmap = page.render(scale=dpi / _BASE_DPI)
        try:
            return np.ascontiguousarray(np.asarray(bitmap.to_pil().convert("RGB")))
        finally:
            bitmap.close()

    def close(self) -> None:
        if self._pdfium is not None:
            try:
                self._pdfium.close()
            except Exception:
                pass
        self._pdfium = None


class LocalPermissiveParser(BaseEngine):
    spec = EngineSpec(
        kind="parser",
        name="local_permissive",
        label="本地解析（宽松许可）：pypdf 取文字 + pypdfium2 渲染 + python-pptx + 标准库 zip",
        python_pkgs=("pypdf", "pypdfium2", "pptx"),
        egress="none",
        note="pypdf 为 BSD-3、pypdfium2 为 Apache-2.0/BSD-3 —— 均宽松许可，"
             "打包分发应用不受 AGPL 约束。取文字质量经引用核验档位实测与 PyMuPDF 等效。",
    )

    # 与 local_pymupdf 同一张后缀表：PDF 走本引擎，PPTX/EPUB 走各自的宽松实现
    # （python-pptx = MIT，EPUB 用标准库 zipfile）
    SUFFIX_FN = {".pdf": "parse_pdf", ".pptx": "parse_pptx", ".epub": "parse_epub"}

    def supports(self, suffix: str) -> bool:
        return suffix.lower() in self.SUFFIX_FN

    def parse(self, path, **kw):
        """
        → (pages, warnings)，与 `parse_docs.parse_*` 的返回契约完全一致。

        这里**不重写组装逻辑**：页记录的字段契约只允许有一处实现，
        `parse_docs.parse_pdf` 负责组装并回头调用本引擎的 `open_handle()` 取文字 ——
        因此两条引擎产出的页记录结构必然一致，差别只落在文字本身。
        """
        suffix = Path(path).suffix.lower()
        fn_name = self.SUFFIX_FN.get(suffix)
        if not fn_name:
            raise ValueError(f"解析引擎不支持的后缀：{suffix}")
        import parse_docs as impl          # 函数级惰性导入：避免循环依赖，且保证实现唯一
        return getattr(impl, fn_name)(Path(path))

    def _open(self, path):
        from pypdf import PdfReader
        try:
            reader = PdfReader(str(path))
        except Exception as exc:
            msg = str(exc).lower()
            if "password" in msg or "encrypt" in msg:
                raise ValueError("PDF 已加密，需要密码才能打开") from exc
            raise
        handle = PdfHandle(reader, None)
        handle._path = str(path)           # 渲染时惰性打开用
        return handle

    @contextmanager
    def open(self, path):
        """打开 PDF 供 OCR 流程逐页取图/取文本；非 PDF 抛错。"""
        if Path(path).suffix.lower() != ".pdf":
            raise ValueError("open() 只支持 PDF（OCR 只处理 PDF 的扫描页）")
        handle = self._open(path)
        try:
            yield handle
        finally:
            handle.close()

    def open_handle(self, path):
        """打开文档并返回句柄；调用方负责调用 close_handle()。"""
        if Path(path).suffix.lower() != ".pdf":
            raise ValueError("open_handle() 只支持 PDF")
        return self._open(path)

    def close_handle(self, handle) -> None:
        try:
            handle.close()
        except Exception:
            pass
