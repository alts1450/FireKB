# -*- coding: utf-8 -*-
"""
本地 OCR 引擎（RapidOCR / onnxruntime）
=======================================
**实现不搬**：识别与后处理仍在 `ocr_pages.py`（get_engine / parse_ocr_output /
lines_from_items / paragraphs_from_lines / ocr_image_array），本层只做装配与声明。

输入契约：**图像数组**（由解析引擎渲染而来）——本引擎不碰 PDF，也不碰文件系统。
"""
from __future__ import annotations

from engines.base import BaseEngine, EngineSpec


class LocalRapidOcrEngine(BaseEngine):
    spec = EngineSpec(
        kind="ocr",
        name="local_rapidocr",
        label="本地 OCR：RapidOCR（onnxruntime，CPU）",
        python_pkgs=("rapidocr_onnxruntime", "numpy"),
        egress="none",
        note="rapidocr-onnxruntime 为 Apache-2.0；内置 PP-OCR 模型随包分发（Apache-2.0）。",
    )

    def recognize(self, image, **kw):
        """
        → (text, avg_confidence, n_lines)
        与 ocr_pages.ocr_image_array 的返回契约一致（含其段落合并逻辑）。
        """
        import ocr_pages as impl           # 函数级惰性导入：避免循环依赖
        return impl.ocr_image_array(image)
