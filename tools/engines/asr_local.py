# -*- coding: utf-8 -*-
"""
本地语音转写引擎（faster-whisper + CTranslate2）
=================================================
**实现不搬**：模型加载与转写循环仍在 `transcribe.py`（transcribe_one），本层做装配与声明。

为什么把 ffmpeg 列为强依赖：faster-whisper 解码音频依赖 ffmpeg/ffprobe。
**注意**：常见的 ffmpeg 发行版是 GPLv3 构建（`--enable-gpl`），
随包分发它会触发 GPL 义务 —— 因此它是"运行时依赖"，**不随项目分发**。
"""
from __future__ import annotations

from engines.base import BaseEngine, EngineSpec


class LocalFasterWhisperAsr(BaseEngine):
    spec = EngineSpec(
        kind="asr",
        name="local_faster_whisper",
        label="本地转写：faster-whisper + CTranslate2（有 GPU 用 GPU）",
        python_pkgs=("faster_whisper", "ctranslate2"),
        binaries=("ffmpeg",),
        needs_gpu=False,          # 无 GPU 可退 --device cpu（很慢，但可用）
        egress="none",
        note="faster-whisper/ctranslate2 为 MIT；ffmpeg 为 GPLv3 构建（本项目不分发）；"
             "模型权重许可须到来源页核实后方可对外表述。",
    )

    def load(self, model: str = "large-v3", device: str = "cuda",
             compute_type: str = "int8_float16"):
        from faster_whisper import WhisperModel
        return WhisperModel(model, device=device, compute_type=compute_type)

    def transcribe(self, model_obj, path, rel: str, course: str, digest: str,
                   language=None, beam_size: int = 5, use_vad: bool = True):
        """→ (rows, meta, elapsed)；契约与 transcribe.transcribe_one 完全一致。"""
        import transcribe as impl          # 函数级惰性导入：避免循环依赖
        return impl.transcribe_one(model_obj, path, rel, course, digest,
                                   language, beam_size, use_vad=use_vad)
