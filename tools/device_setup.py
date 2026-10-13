# -*- coding: utf-8 -*-
"""Shared device inspection, deployment recommendations and portable Ollama setup.
Only standard library; desktop and CLI use exactly the same plan.
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

ROOT = Path(os.environ.get("FIREKB_ROOT") or Path(__file__).resolve().parents[1])
RUNTIME = ROOT / ".firekb-runtime"
HOST = "http://127.0.0.1:11435"
VERSION = "v0.40.3"
ARCHIVE_SHA = "e5ecff02dfc7cae8ddf6d6b7a7ac5f727ffeedf4491f44ae75ff5b0820dbbbd5"
ARCHIVE_URL = f"https://github.com/ollama/ollama/releases/download/{VERSION}/ollama-windows-amd64.zip"
PURPOSE = "firekb_knowledge_processing"
PROFILES = [
    {"id": "speed", "title": "速度优先", "model": "Qwen3.5 4B", "tag": "qwen3.5:4b-q4_K_M", "quant": "Q4_K_M", "vram": 4.5, "ram": 6, "disk": 18, "context": 4096,
     "description": "轻量抽取、整理和草稿；数字与引用仍需本地核验。"},
    {"id": "balanced", "title": "均衡", "model": "Qwen3.5 4B", "tag": "qwen3.5:4b-q8_0", "quant": "Q8_0", "vram": 6.5, "ram": 8, "disk": 20, "context": 4096,
     "description": "较高精度量化，兼顾资源占用与文本质量。"},
    {"id": "quality", "title": "质量优先", "model": "Qwen3.5 9B", "tag": "qwen3.5:9b-q4_K_M", "quant": "Q4_K_M", "vram": 7.5, "ram": 9, "disk": 24, "context": 2048,
     "description": "短文本质量试用；8GB 显存余量紧张，长材料推荐云端。"},
]


def run(args: list[str], timeout: int = 20, env=None) -> str:
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=timeout, env=env, creationflags=0x08000000 if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError(f"命令失败（{Path(args[0]).name}，退出码 {result.returncode}）")
    return result.stdout.strip().lstrip("\ufeff")


def inspect_device() -> dict:
    warnings = []
    ram_total = ram_free = 0.0
    cpu = {"name": platform.processor() or "未知 CPU", "cores": None, "threads": os.cpu_count() or 0}
    gpus = []
    if os.name == "nt":
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in
                ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]
        mem = Memory()
        mem.length = ctypes.sizeof(mem)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
            ram_total, ram_free = mem.total / 2**30, mem.available / 2**30
        try:
            command = "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors | ConvertTo-Json -Compress"
            info = json.loads(run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]))
            if isinstance(info, list):
                info = info[0]
            cpu = {"name": info["Name"], "cores": info["NumberOfCores"], "threads": info["NumberOfLogicalProcessors"]}
        except Exception:
            warnings.append("CPU 型号探测不完整，按云端推荐处理。")
    else:
        warnings.append("自动便携部署目前支持 Windows x64 / NVIDIA；其他平台提供手动指令。")
    nvsmi = shutil.which("nvidia-smi") or str(Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/nvidia-smi.exe")
    try:
        raw = run([nvsmi, "--query-gpu=index,name,memory.total,memory.free,driver_version", "--format=csv,noheader,nounits"])
        for row in csv.reader(raw.splitlines()):
            index, name, total, free, driver = [v.strip() for v in row]
            gpus.append({"index": int(index), "name": name, "vramGb": round(float(total) / 1024, 2),
                         "freeVramGb": round(float(free) / 1024, 2), "driver": driver})
    except Exception:
        warnings.append("未能获取 NVIDIA 专用显存；不采用易截断的 WMI AdapterRAM 作为显存依据。")
    disk_free = shutil.disk_usage(ROOT).free / 2**30
    return {"os": platform.system(), "arch": platform.machine(), "cpu": cpu, "gpus": gpus,
            "ramGb": round(ram_total, 2), "freeRamGb": round(ram_free, 2), "freeDiskGb": round(disk_free, 2),
            "python": sys.executable, "detectedAt": time.strftime("%Y-%m-%dT%H:%M:%S"), "warnings": warnings}


def cpu_supported(cpu: dict) -> bool:
    # Conservative known desktop equivalents, not arbitrary name-string sorting.
    name = cpu["name"]
    intel = re.search(r"\bi[79][ -](?:13[79]00|14[79]00)(?:K|F|KF)?\b", name, re.I)
    amd = re.search(r"Ryzen\s+9\s+(?:7950X3D|7950X|9950X3D|9950X)\b", name, re.I)
    return bool(intel or amd) and cpu.get("threads", 0) >= 24


def gpu_supported(gpu: dict) -> bool:
    return not re.search(r"laptop|mobile", gpu["name"], re.I) and bool(re.search(r"RTX\s+(?:40[6789]0|50[6789]0)\b", gpu["name"], re.I)) and gpu["vramGb"] >= 7.8


def recommend(device: dict) -> dict:
    reasons = []
    if device["os"] != "Windows" or device["arch"].lower() not in {"amd64", "x86_64"}:
        reasons.append("一键本地部署需 Windows x64")
    if not cpu_supported(device["cpu"]):
        reasons.append("CPU 未达到保守型号门槛或属于待实测的等效型号")
    if device["ramGb"] < 15.0:
        reasons.append("系统内存低于 16GB 门槛")
    supported_gpus = [g for g in device["gpus"] if gpu_supported(g)]
    gpu = max(supported_gpus, key=lambda g: g["freeVramGb"], default=None)
    if gpu is None:
        reasons.append("未检出 RTX 4060 8GB 或已列明的更高档 NVIDIA GPU")
    plans = []
    for spec in PROFILES:
        blockers = list(reasons)
        if gpu and gpu["freeVramGb"] < spec["vram"]:
            blockers.append(f"当前可用显存需至少 {spec['vram']}GB")
        if device["freeRamGb"] < spec["ram"]:
            blockers.append(f"当前可用内存需至少 {spec['ram']}GB")
        if device["freeDiskGb"] < spec["disk"]:
            blockers.append(f"项目磁盘剩余空间需至少 {spec['disk']}GB")
        plans.append({**spec, "eligible": not blockers, "route": "local" if not blockers else "cloud", "reasons": blockers,
                      "gpuIndex": gpu["index"] if gpu else None,
                      "command": f'python tools/device_setup.py --install {spec["id"]}',
                      "runCommand": f'.\\.firekb-runtime\\ollama\\{VERSION}\\ollama.exe run firekb-{spec["id"]}'})
    return {"device": device, "baselineMet": not reasons, "baselineReasons": reasons, "profiles": plans,
            "runtimeVersion": VERSION, "endpoint": HOST + "/v1", "resourceEstimates": True,
            "scope": "本地文本生成；解析/OCR/ASR 保持现有引擎。资源门槛为保守估算，仍须本机验收。"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("本地推理服务不接受重定向")


def local_api(route: str, body=None, timeout: int = 30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(HOST + route, data=data, headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(req, timeout=timeout) as response:
        return json.load(response)


def atomic_json(file: Path, value: dict):
    file.parent.mkdir(parents=True, exist_ok=True)
    temp = file.with_name(file.name + ".tmp-" + uuid.uuid4().hex)
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, file)


def emit(message: str):
    print(message, flush=True)


def ensure_runtime() -> Path:
    folder = RUNTIME / "ollama" / VERSION
    exe = folder / "ollama.exe"
    marker = folder / ".verified.json"
    if exe.exists() and marker.exists() and json.loads(marker.read_text())["archiveSha256"] == ARCHIVE_SHA:
        return exe
    folder.parent.mkdir(parents=True, exist_ok=True)
    archive = folder.parent / f"{VERSION}.zip"
    emit(f"下载官方 Ollama {VERSION}（约 1.47GB），仅写入项目 .firekb-runtime")
    if not archive.exists():
        partial = archive.with_suffix(".part")
        try:
            with urllib.request.urlopen(ARCHIVE_URL, timeout=60) as response, partial.open("wb") as target:
                size = 0
                last = time.monotonic()
                while chunk := response.read(4 * 1024**2):
                    target.write(chunk)
                    size += len(chunk)
                    if time.monotonic() - last > 2:
                        emit(f"下载运行环境：{size / 1024**2:.0f} MB")
                        last = time.monotonic()
            os.replace(partial, archive)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != ARCHIVE_SHA:
        archive.unlink()
        raise RuntimeError("官方安装包 SHA-256 校验失败，未解压或执行")
    staging = folder.with_name(folder.name + ".stage-" + uuid.uuid4().hex)
    staging.mkdir()
    try:
        with zipfile.ZipFile(archive) as z:
            for member in z.infolist():
                target = (staging / member.filename).resolve()
                if not target.is_relative_to(staging.resolve()) or (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise RuntimeError("安装包包含越界路径或符号链接，拒绝解压")
            z.extractall(staging)
        if not (staging / "ollama.exe").exists():
            raise RuntimeError("安装包不含 Ollama 主程序")
        if folder.exists():
            # Preserve failed or old installation instead of recursive deletion.
            folder.rename(folder.with_name(folder.name + ".previous-" + uuid.uuid4().hex))
        staging.rename(folder)
        atomic_json(marker, {"archiveSha256": ARCHIVE_SHA, "version": VERSION})
    except Exception:
        emit(f"安装未完成，诊断目录：{staging}")
        raise
    return exe


def runtime_env() -> dict:
    env = {**os.environ, "OLLAMA_HOST": "127.0.0.1:11435", "OLLAMA_MODELS": str(RUNTIME / "models"),
           "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1", "OLLAMA_CONTEXT_LENGTH": "4096"}
    try:
        state = json.loads((ROOT / "00_配置" / ".deployment.json").read_text(encoding="utf-8"))
        if state.get("gpuIndex") is not None:
            env["CUDA_VISIBLE_DEVICES"] = str(state["gpuIndex"])
    except (OSError, ValueError):
        pass
    return env


def ensure_server(exe: Path, env: dict):
    owner = RUNTIME / "server-owner.json"
    try:
        local_api("/api/version", timeout=2)
        info = json.loads(owner.read_text())
        if info.get("exe") != str(exe) or info.get("gpu") != env.get("CUDA_VISIBLE_DEVICES"):
            raise RuntimeError("当前服务的 GPU 或运行环境与所选方案不同，请重启该项目服务后重试")
        if os.name == "nt":
            pid = int(info["pid"])
            actual = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                          f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').ExecutablePath"], timeout=10)
            if Path(actual).resolve() != exe.resolve():
                raise RuntimeError("本地端口不属于该项目的已记录服务，拒绝复用")
        return
    except (OSError, ValueError):
        pass
    RUNTIME.mkdir(parents=True, exist_ok=True)
    log = (RUNTIME / "ollama-server.log").open("ab")
    child = subprocess.Popen([str(exe), "serve"], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             creationflags=(0x08000000 | 0x00000200) if os.name == "nt" else 0)
    log.close()
    for _ in range(60):
        if child.poll() is not None:
            raise RuntimeError("本地服务启动失败，请查看 .firekb-runtime/ollama-server.log")
        try:
            local_api("/api/version", timeout=1)
            atomic_json(owner, {"pid": child.pid, "exe": str(exe), "gpu": env.get("CUDA_VISIBLE_DEVICES")})
            return
        except OSError:
            time.sleep(1)
    child.terminate()
    raise RuntimeError("本地服务在 60 秒内未就绪")


def install(profile_id: str):
    spec = next(p for p in recommend(inspect_device())["profiles"] if p["id"] == profile_id)
    if not spec["eligible"]:
        raise RuntimeError("不满足部署需求：" + "；".join(spec["reasons"]))
    RUNTIME.mkdir(parents=True, exist_ok=True)
    lock = RUNTIME / "setup.lock"
    # Exclusive lock also protects CLI against simultaneous desktop installation.
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("已有配置任务，等待完成后重试；异常中断的锁需确认无任务后手动清理")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        exe = ensure_runtime()
        env = runtime_env()
        env["CUDA_VISIBLE_DEVICES"] = str(spec["gpuIndex"])
        ensure_server(exe, env)
        emit(f"拉取 {spec['tag']}（可重试，Ollama 复用已完成下载）")
        # Explicit tag, never 'latest'; stream status without buffering the whole download.
        req = urllib.request.Request(HOST + "/api/pull", data=json.dumps({"model": spec["tag"], "stream": True}).encode(),
                                     headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        last = 0
        with opener.open(req, timeout=1800) as response:
            for line in response:
                entry = json.loads(line)
                if "error" in entry:
                    raise RuntimeError(entry["error"])
                if time.monotonic() - last > 2 or entry.get("status") == "success":
                    percent = f" {100 * entry.get('completed', 0) / entry['total']:.0f}%" if entry.get("total") else ""
                    emit(entry.get("status", "下载中") + percent)
                    last = time.monotonic()
        model_name = "firekb-" + profile_id
        modelfile = RUNTIME / f"{profile_id}.Modelfile"
        modelfile.write_text(f"FROM {spec['tag']}\nPARAMETER num_ctx {spec['context']}\nPARAMETER num_predict 1024\n", encoding="utf-8")
        run([str(exe), "create", model_name, "-f", str(modelfile)], timeout=600, env=env)
        emit("检查加载与 GPU 占用（使用固定测试句，不读取素材）")
        result = local_api("/api/generate", {"model": model_name, "prompt": "回答：就绪", "think": False,
                           "stream": False, "keep_alive": "5m", "options": {"num_predict": 16}}, timeout=600)
        if not result.get("response"):
            raise RuntimeError("本地模型未返回正文；配置未切换")
        loaded = local_api("/api/ps").get("models", [])
        if not any(m.get("name", "").split(":")[0] == model_name and m.get("size_vram", 0) > 0 for m in loaded):
            raise RuntimeError("模型未使用 GPU，配置未切换；请检查驱动或采用云端")
        # Write env only after successful installation and warmup, preserving the cloud backup.
        set_env({"DEEPSEEK_BASE_URL": HOST + "/v1", "DEEPSEEK_MODEL": model_name,
                 "DEEPSEEK_MODEL_STRONG": model_name, "FIREKB_PROVIDER": "ollama", "FIREKB_LOCAL_CONTEXT": str(spec["context"])})
        atomic_json(ROOT / "00_配置" / ".deployment.json", {"profile": profile_id, "tag": spec["tag"],
                    "context": spec["context"], "runtimeVersion": VERSION, "endpoint": HOST + "/v1", "gpuIndex": spec["gpuIndex"], "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        emit("配置完成：运行环境、模型及 GPU 加载验证通过；已切换到本地文本接口。")
    finally:
        lock.unlink(missing_ok=True)


def set_env(changes: dict):
    file = ROOT / "00_配置" / ".env"
    file.parent.mkdir(parents=True, exist_ok=True)
    original = file.read_text(encoding="utf-8") if file.exists() else ""
    if original:
        backup = file.with_name(file.name + f".setup-{time.time_ns()}.bak")
        backup.write_text(original, encoding="utf-8")
    pending = dict(changes)
    lines = []
    for line in original.splitlines():
        key = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and key in changes:
            if key in pending:
                lines.append(key + "=" + pending.pop(key))
        else:
            lines.append(line)
    lines += [f"{k}={v}" for k, v in pending.items()]
    temp = file.with_name(file.name + ".tmp-" + uuid.uuid4().hex)
    temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temp, file)


def start_configured():
    from llm import load_env
    cfg = load_env()
    if cfg.get("FIREKB_PROVIDER") != "ollama":
        return
    exe = RUNTIME / "ollama" / VERSION / "ollama.exe"
    if not exe.exists():
        raise RuntimeError("本地环境尚未安装，请先执行一键配置")
    ensure_server(exe, runtime_env())


def main() -> int:
    ap = argparse.ArgumentParser(description="检测设备、推荐模型并一键部署本地文本推理")
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--install", choices=[p["id"] for p in PROFILES])
    ap.add_argument("--start", action="store_true")
    ap.add_argument("--consent-status", action="store_true")
    ap.add_argument("--approve", nargs="+")
    ap.add_argument("--note")
    ap.add_argument("--expected-recipient")
    ap.add_argument("--revoke", action="store_true")
    args = ap.parse_args()
    from engines import egress as eg
    from llm import load_env, DEFAULT_BASE
    cfg = load_env()
    recipient = cfg.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE
    if args.install:
        install(args.install)
    elif args.start:
        start_configured()
    elif args.approve:
        if args.expected_recipient and eg.normalize_recipient(recipient) != args.expected_recipient:
            raise RuntimeError("接口发生变化，拒绝写入批准，请重新核对")
        eg.grant_recipient(ROOT / "00_配置", args.approve, recipient, PURPOSE, args.note or "")
        emit("已记录手动批准；只适用于当前接收方、所选类别与用途。")
    elif args.revoke:
        eg.revoke(ROOT / "00_配置")
        emit("已撤销全部授权；之后的新请求需重新批准。")
    elif args.consent_status:
        print(json.dumps({"recipient": eg.normalize_recipient(recipient), "purpose": PURPOSE,
                          "local": eg.is_local_recipient(recipient),
                          "approved": eg.approved_categories(ROOT / "00_配置", recipient, PURPOSE)}, ensure_ascii=False))
    else:
        print(json.dumps(recommend(inspect_device()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"配置失败：{exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
