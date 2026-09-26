# -*- coding: utf-8 -*-
"""
M3 音频转写模块（本地 Whisper）
==================================
在有 NVIDIA GPU 的机器上把课堂录音转成带时间戳的文本，输出统一契约文件 transcript.jsonl。

依赖（运行前需装好）：
    pip install faster-whisper
    ffmpeg 需在 PATH 中

用法：
  python transcribe.py --check                    # 只做环境/显存自检，不转写
  python transcribe.py                           # 转写 10_原始归档\\录音 下全部新文件
  python transcribe.py --course 建筑防火          # 只处理某门课
  python transcribe.py --model large-v3 --force   # 换模型 / 强制重跑
  python transcribe.py --device cpu               # 无 GPU 时退回 CPU（很慢，仅应急）
  python transcribe.py --no-vad                   # 关闭 VAD（见下方「VAD 已知缺陷」）

🔻 用途与权重：
  语音转写内容**可靠性远低于课件与教材**，在本项目中只作**参考层**使用：
    * 允许：梳理教学脉络、判断讲授覆盖度与侧重、给人工复习提供线索；
    * 禁止：作为知识点（kp）与证据引用（evidence）的来源、进入数字对照表、
      作为任何「结论性内容」的依据。
  本模块产出的每条记录都带 `usage: "advisory_only"` 与 `weight: 3` 标记供下游过滤；
  `20_文本库/transcript.jsonl` **不进 pages.jsonl**，因此天然不会进入 kp 抽取。

设计要点：
  * 断点续跑：已转写过的文件（按 SHA1 判断）默认跳过，中断后可接着跑。
  * 显存约束：8GB 显存下不要同时跑大模型（与本地 Qwen llama-server 争显存）；
    本脚本默认 int8_float16 量化。
  * 时间戳一律相对该音频文件起点（秒，float）。这是溯源精度的基础，
    不要改成绝对时钟时间。
  * 置信度分级：由 faster-whisper 的 avg_logprob 换算为 A/B/C 三级，
    C 级段落后续会被 M5 挡在背诵材料之外。

⚠️ VAD 已知缺陷（务必先读）：
  默认开启的 Silero VAD 在**真实课堂录音**上会大面积误判为静音：整条录音可能只切出 0~1 段，
  而同一段音频**关掉 VAD 可正常转出数百段**。
  已排除的原因：电平过低（峰值/RMS 归一、+12 dB 全部无效）、左右声道反相（相关性 +0.42~+0.83）。
  干净的合成 TTS 音频则 100% 通过 VAD —— 即**用合成素材自检会掩盖这个缺陷**，必须拿真实录音验证。
  因此本模块：① 段数过少时**自动关掉 VAD 重试一次**；② 仍为 0 段则**判失败且不写索引**
  （否则下次重跑会把它当成处理过的文件跳过，等于静默丢课）；③ 提供 `--no-vad` 供强制关闭。
  尚存疑：对电平极低、实录内容稀薄的录音（远场/空场），判 0 段**可能是正确的**，
  需人工听一段再决定是否纳入，不要只看段数下结论。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 路径常量统一向共享层 kb 取（避免每个脚本各写一遍 FIREKB_ROOT 解析）。
# ⚠️ 本文件的 `OUT_DIR` **就是** 20_文本库（kb 里叫 TEXT_DIR）；`INDEX_FILE` 是转写专用索引，
#    与 `kb.STATE_FILE` / `ocr_pages.STATE_FILE` 都不同名同指 —— 别按名字猜含义。
sys.path.insert(0, str(Path(__file__).resolve().parent))

import kb  # noqa: E402  共享层（路径常量唯一来源）

ROOT = kb.ROOT
AUDIO_DIR = kb.ARCHIVE / "录音"
OUT_DIR = kb.TEXT_DIR
LOG_DIR = kb.LOG_DIR
TRANSCRIPT_FILE = kb.TRANSCRIPT_FILE
INDEX_FILE = OUT_DIR / "transcript_index.json"

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".opus", ".amr", ".flac", ".wma", ".ogg", ".mp4"}

# ---- 素材权重与用途 ----
# 1 = 可作引用来源（教材/规范/课件）；3 = 仅参考（录音转写）。
# 录音转写可靠性远低于课件与教材，只作教学脉络/覆盖度参考，禁止作为知识点与引用来源。
WEIGHT_ADVISORY = 3
USAGE_POLICY = "advisory_only"
MIN_SEGMENTS = 3          # 少于此段数即视为异常（一堂课的录音不可能只有几段）

# avg_logprob 阈值 -> A/B/C 置信度
CONF_A = -0.35
CONF_B = -0.75


def sha1_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def guess_course(rel_parts: list[str]) -> str:
    return rel_parts[-2] if len(rel_parts) >= 3 else "未分类"


def load_index() -> dict:
    if INDEX_FILE.exists():
        try:
            return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_index(idx: dict) -> None:
    INDEX_FILE.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")


def grade(avg_logprob: float | None) -> str:
    if avg_logprob is None:
        return "B"
    if avg_logprob >= CONF_A:
        return "A"
    if avg_logprob >= CONF_B:
        return "B"
    return "C"


def check_env() -> int:
    print("=" * 66)
    print("M3 转写模块 · 环境自检")
    print("=" * 66)

    print(f"Python : {sys.version.split()[0]} ({sys.executable})")
    if sys.version_info[:2] >= (3, 14):
        print("  [!] 警告：Python 3.14+ 上部分推理库的 wheel 可能缺失。")
        print("      建议使用 Python 3.11 或 3.12。")

    ff = shutil.which("ffmpeg")
    print(f"ffmpeg : {ff if ff else '[缺失] —— faster-whisper 解码音频需要它，请先安装并加入 PATH'}")
    ffp = shutil.which("ffprobe")
    print(f"ffprobe: {ffp if ffp else '[缺失]'}")

    try:
        import faster_whisper  # noqa: F401

        print("faster-whisper : 已安装")
    except Exception as exc:
        print(f"faster-whisper : [缺失] {type(exc).__name__} —— 请执行 pip install faster-whisper")
        return 2

    gpu_ok = False
    try:
        import ctranslate2

        n = ctranslate2.get_cuda_device_count()
        if n > 0:
            gpu_ok = True
            print(f"CUDA 设备      : {n} 个（可用）")
        else:
            print("CUDA 设备      : 0 个 —— 将只能使用 CPU，速度会非常慢")
        try:
            print(f"计算类型支持   : {ctranslate2.get_supported_compute_types('cuda')}")
        except Exception:
            pass
    except Exception as exc:
        print(f"CTranslate2    : 检查失败 {type(exc).__name__}: {exc}")

    if shutil.which("nvidia-smi"):
        os.system("nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv")

    print(f"\n录音目录: {AUDIO_DIR}")
    if AUDIO_DIR.exists():
        files = [p for p in AUDIO_DIR.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
        print(f"待处理音频: {len(files)} 个")
    else:
        print("[!] 录音目录不存在，请先创建并放入录音文件")

    print(f"\n输出文件: {TRANSCRIPT_FILE}")
    print("\n[!] VAD 提示：Silero VAD 在**真实课堂录音**上可能大面积误判为静音")
    print("    （3 个真实文件里 2 个只切出 0~1 段；而干净的合成音频 100% 通过 —— 合成素材会掩盖该缺陷）。")
    print("    若段数远小于预期，请加 --no-vad 重跑，并人工抽听一段确认录音有效性。")
    print("    另外：本项目语音内容仅作**参考层**（usage=advisory_only），不得作为知识点与引用来源。")
    print("=" * 66)
    print("结论：" + ("可以开始转写" if (ff and gpu_ok) else "存在不足，请先按提示处理"))
    return 0 if (ff and gpu_ok) else 1


def transcribe_one(model, path: Path, rel: str, course: str, digest: str,
                   language: str | None, beam_size: int,
                   use_vad: bool = True) -> tuple[list[dict], dict, float]:
    t0 = time.time()
    kw = dict(
        language=language,          # 中文课堂建议显式指定 "zh"，可避免语种误判
        beam_size=beam_size,
        condition_on_previous_text=False,  # 避免长音频错误累积
        word_timestamps=False,
    )
    if use_vad:
        # VAD 只是加速手段，不是正确性前提；真实录音上会误判，见模块 docstring 的「VAD 已知问题」
        kw["vad_filter"] = True
        kw["vad_parameters"] = {"min_silence_duration_ms": 700}
    segments, info = model.transcribe(str(path), **kw)

    rows: list[dict] = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        rows.append(
            {
                "lecture_id": Path(rel).stem,
                "course": course,
                "source_file": rel,
                "source_sha1": digest,
                "start": round(float(seg.start), 2),
                "end": round(float(seg.end), 2),
                "duration": round(float(seg.end) - float(seg.start), 2),
                "speaker": "unknown",
                "text": text,
                "seg_type": "unknown",
                "avg_logprob": round(float(getattr(seg, "avg_logprob", 0.0) or 0.0), 4),
                "confidence": grade(getattr(seg, "avg_logprob", None)),
                "transcribed_at": datetime.now().isoformat(timespec="seconds"),
                # 参考层标记：供下游过滤，语音内容不得作为引用来源
                "usage": USAGE_POLICY,
                "weight": WEIGHT_ADVISORY,
            }
        )
    meta = {
        "lecture_id": Path(rel).stem,
        "course": course,
        "source_file": rel,
        "source_sha1": digest,
        "language": getattr(info, "language", language),
        "language_probability": round(float(getattr(info, "language_probability", 0.0) or 0.0), 4),
        "duration": round(float(getattr(info, "duration", 0.0) or 0.0), 2),
        "segments": len(rows),
        "elapsed_sec": round(time.time() - t0, 1),
        "transcribed_at": datetime.now().isoformat(timespec="seconds"),
        "vad": bool(use_vad),
        "usage": USAGE_POLICY,
        "weight": WEIGHT_ADVISORY,
    }
    return rows, meta, time.time() - t0


def main() -> int:
    ap = argparse.ArgumentParser(description="M3 课堂录音转写")
    ap.add_argument("--check", action="store_true", help="只做环境自检")
    ap.add_argument("--model", default="large-v3", help="模型名（默认 large-v3；显存紧张可换 medium）")
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    ap.add_argument("--compute-type", default="int8_float16",
                    help="cuda 默认 int8_float16；cpu 请用 int8")
    ap.add_argument("--language", default="zh", help="语言，中文课堂用 zh；不确定填 auto")
    ap.add_argument("--beam-size", type=int, default=5)
    ap.add_argument("--course", help="只处理某门课（按文件夹名过滤）")
    ap.add_argument("--force", action="store_true", help="忽略已完成记录，全部重跑")
    ap.add_argument("--no-vad", action="store_true",
                    help="关闭 VAD 静音过滤（真实课堂录音上 VAD 会大面积误判为静音，见模块 docstring）")
    ap.add_argument("--allow-egress", action="store_true",
                    help="放行「数据出境」引擎（仅当你确认有权上传录音；默认拒绝）")
    ap.add_argument("--limit", type=int, help="只处理前 N 个文件（试跑用）")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if args.check:
        return check_env()

    # 引擎层：转写实现由 00_配置/engines.json 选择（本地 faster-whisper / 云端 ASR）
    #   只取 **asr**：解析/OCR 的依赖缺不缺与 M3 无关（反之亦然）。
    from engines import config_path, get_engines, require_ready
    from engines import egress as _egress
    import kb  # 共享层：批次标识与引擎指纹（P2）
    _engines = get_engines("asr")
    require_ready(_engines)
    _egress.require_consent(_engines, config_path().parent, allow_flag=args.allow_egress)
    asr = _engines["asr"]

    if not AUDIO_DIR.exists():
        print(f"[FAIL] 录音目录不存在：{AUDIO_DIR}")
        return 2

    files = sorted(p for p in AUDIO_DIR.rglob("*")
                   if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
    if args.course:
        files = [p for p in files if args.course in p.parts]
    if args.limit:
        files = files[: args.limit]
    if not files:
        print(f"[!] 未找到音频文件（支持：{', '.join(sorted(AUDIO_EXTS))}）")
        return 1

    index = load_index()
    todo: list[tuple[Path, str, str, str]] = []
    for p in files:
        rel = p.relative_to(ROOT / "10_原始归档").as_posix()
        digest = sha1_file(p)
        rec = index.get(rel)
        if rec and rec.get("sha1") == digest and not args.force:
            continue
        todo.append((p, rel, guess_course(rel.split("/")), digest))

    print("=" * 66)
    print(f"M3 转写 · 模型={args.model} 设备={args.device} 量化={args.compute_type} 语言={args.language}")
    print(f"音频总数 {len(files)}，本次待处理 {len(todo)}（已完成 {len(files) - len(todo)}）")
    print("=" * 66)
    if not todo:
        print("全部已转写，无需处理。")
        return 0

    print("正在加载模型（首次会下载权重，请耐心等待）...")
    try:
        model = asr.load(model=args.model, device=args.device, compute_type=args.compute_type)
    except Exception as exc:
        print(f"[FAIL] 模型加载失败：{type(exc).__name__}: {exc}")
        print("       显存不足时可尝试：--model medium  或  --compute-type int8")
        return 3
    print("模型就绪。\n")

    total_audio = 0.0
    total_elapsed = 0.0
    failed: list[dict] = []

    # 批次标识：本批的每一行都带 run_id，索引里也记一份。
    # 它是「安全清理旧批次」的唯一可靠依据 —— 若拿逐段生成的 `transcribed_at` 当批次 id，
    # 清理时会连带删掉不属于该批次的正常行。
    run_id = kb.new_run_id("ASR")
    engine_fp = kb.engine_fingerprint()
    print(f"批次标识 {run_id}  引擎 {engine_fp.get('asr')}  "
          f"模型 {args.model}/{args.compute_type}/{args.device}")
    print("=" * 66)

    for i, (p, rel, course, digest) in enumerate(todo, start=1):
        print(f"[{i}/{len(todo)}] {rel}")
        use_vad = not args.no_vad
        try:
            rows, meta, elapsed = asr.transcribe(
                model, p, rel, course, digest,
                None if args.language == "auto" else args.language,
                args.beam_size, use_vad=use_vad,
            )
            # VAD 误判兜底：段数过少而音频很长 → 关掉 VAD 重试一次
            if use_vad and meta["segments"] < MIN_SEGMENTS and meta["duration"] > 60:
                print(f"      [!] 仅 {meta['segments']} 段 / 音频 {meta['duration']:.0f}s"
                      f" —— VAD 可能把整条录音判成静音，关闭 VAD 重试一次")
                rows, meta, elapsed = asr.transcribe(
                    model, p, rel, course, digest,
                    None if args.language == "auto" else args.language,
                    args.beam_size, use_vad=False,
                )
        except Exception as exc:
            print(f"      失败：{type(exc).__name__}: {exc}")
            failed.append({"file": rel, "reason": f"{type(exc).__name__}: {exc}"})
            continue

        # fail-loud：0 段绝不写索引 —— 否则会被误记为「已处理」而在下次重跑时被跳过，
        # 造成静默丢课（真实课堂录音上 VAD 大面积误判时，正是这样整条录音被吞掉的）
        if meta["segments"] == 0:
            print("      [FAIL] 0 段：不写入索引、不落盘（判为失败；请人工听一段确认录音是否有效）")
            failed.append({"file": rel,
                           "reason": "0 段（关闭 VAD 后仍无内容；需人工确认录音有效性）"})
            continue

        # 批次与引擎指纹由**业务层**注入，不塞进引擎接口：引擎只管转写，
        # 批次/复算是项目自己的账（换引擎实现时接口不必跟着变形）。
        for r in rows:
            r["run_id"] = run_id
        meta["run_id"] = run_id
        meta["engine"] = engine_fp.get("asr") or "unknown"

        with TRANSCRIPT_FILE.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        index[rel] = {"sha1": digest, **meta}
        save_index(index)  # 每个文件都落盘，中断不丢进度

        total_audio += meta["duration"]
        total_elapsed += elapsed
        speed = meta["duration"] / elapsed if elapsed > 0 else 0
        conf = {}
        for r in rows:
            conf[r["confidence"]] = conf.get(r["confidence"], 0) + 1
        print(f"      完成：{meta['segments']} 段 / 音频 {meta['duration']:.0f}s / "
              f"耗时 {elapsed:.0f}s / {speed:.1f}x 实时")
        print(f"      置信度分布 A={conf.get('A', 0)} B={conf.get('B', 0)} C={conf.get('C', 0)}")

    print("\n" + "=" * 66)
    if total_elapsed > 0:
        print(f"总音频 {total_audio / 3600:.2f} 小时，总耗时 {total_elapsed / 3600:.2f} 小时，"
              f"平均 {total_audio / total_elapsed:.1f}x 实时")
    print(f"失败 {len(failed)} 个")
    for f in failed:
        print(f"  - {f['file']}  ({f['reason']})")
    print(f"输出：{TRANSCRIPT_FILE}")
    print(f"索引：{INDEX_FILE}")
    print(f"本次批次 run_id：{run_id}（已写入本批次每段与索引条目；清理旧批次只认它）")
    print("=" * 66)
    return 0 if not failed else 4


if __name__ == "__main__":
    sys.exit(main())
