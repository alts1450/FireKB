# -*- coding: utf-8 -*-
"""
LLM 调用封装（零依赖，只用标准库 urllib）
==========================================
供 M4 结构化 / M5 校验 / M6 复习产物 等模块共用。

配置文件：<项目根>\\00_配置\\.env
  DEEPSEEK_API_KEY=sk-xxxx
  DEEPSEEK_BASE_URL=https://api.deepseek.com
  DEEPSEEK_MODEL=deepseek-v4-flash

命令行自检：
  python tools\\llm.py --selftest
  python tools\\llm.py --ask "防火分区的定义是什么"

注意事项（先读这几条，能省下大量排查时间）：
  * 思考型模型的 reasoning 内容同样占用 max_tokens。若预算给小了，会出现
    HTTP 200 但 content 为空、finish_reason=length 的现象——看起来像接口故障，
    实际是预算被思考吃光。本模块默认 max_tokens=8000，并对该情况抛出明确错误。
  * 批量结构化任务建议关闭思考模式（thinking=disabled），速度与成本都显著更优。
  * 模型返回 JSON 时可能带 markdown 围栏，使用 extract_json() 统一处理；
    解析失败时把原始返回前 300 字一并抛出，避免"不是合法 JSON"这类无法定位的报错。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("FIREKB_ROOT") or Path(__file__).resolve().parents[1])
ENV_FILE = ROOT / "00_配置" / ".env"
LOG_DIR = ROOT / "90_日志"

DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_MAX_TOKENS = 8000

PLACEHOLDER_HINTS = ("sk-替换", "sk-xxx", "your", "填入", "TODO")


# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------
def load_env(path: Path = ENV_FILE) -> dict[str, str]:
    """读取 .env（KEY=VALUE，支持 # 注释）。进程环境变量优先于文件。"""
    cfg: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


def has_valid_key(cfg: dict[str, str] | None = None) -> bool:
    cfg = cfg or load_env()
    key = (cfg.get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        return False
    return not any(h in key for h in PLACEHOLDER_HINTS)


# ----------------------------------------------------------------------------
# JSON 容错
# ----------------------------------------------------------------------------
def _loads_tolerant(s: str):
    """
    先严格解析，失败时容忍**字符串内的裸控制字符**。

    触发形态：返回体 `finish_reason=stop`、内容完整（JSON 头尾都正常），
    但 `json.loads` 报 `Invalid control character at: line 17 column 21 (char 688)`。
    原因是 evidence 的 quote **从 PDF 原文逐字抄来**，原文里夹带的制表符/换行
    被模型原样写进 JSON 字符串（未转义成 \\t / \\n）。这不是模型错误，也不是输出被
    截断（completion_tokens 远低于上限），但严格解析会把整块 10-15 个知识点一起丢掉 ——
    代价与原因完全不成比例。

    `json.loads(..., strict=False)` 允许字符串内出现控制字符，正好覆盖这种形态；
    对正常响应不改变行为（严格解析先试，通过就走原路径）。
    """
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return json.loads(s, strict=False)


def extract_json(text: str):
    """
    从模型返回中提取 JSON。容忍 markdown 围栏与前后废话。
    解析失败抛 ValueError，并附带原始返回前 300 字。
    """
    if text is None:
        raise ValueError("模型返回为空（content 为 None）")
    s = text.strip()
    if not s:
        raise ValueError("模型返回为空字符串")
    fence = re.search(r"```(?:json)?\s*(.+?)```", s, flags=re.S | re.I)
    if fence:
        s = fence.group(1).strip()
    try:
        return _loads_tolerant(s)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = s.find(opener), s.rfind(closer)
        if i != -1 and j != -1 and j > i:
            try:
                return _loads_tolerant(s[i : j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"无法解析为 JSON。原始返回前 300 字：{s[:300]!r}")


# ----------------------------------------------------------------------------
# 核心调用
# ----------------------------------------------------------------------------
def chat(
    messages: list[dict],
    model: str | None = None,
    json_mode: bool = False,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 0.3,
    timeout: int = 300,
    retries: int = 3,
    disable_thinking: bool = False,
    verbose: bool = False,
) -> dict:
    """
    调用兼容 OpenAI 协议的服务，返回 {"content", "usage", "model", "finish_reason"}。
    失败自动重试（指数退避），最终失败抛 RuntimeError 并带上服务端错误正文。
    """
    cfg = load_env()
    key = (cfg.get("DEEPSEEK_API_KEY") or "").strip()
    if not has_valid_key(cfg):
        raise RuntimeError(
            f"未配置可用的 API Key。请编辑 {ENV_FILE}，把 DEEPSEEK_API_KEY 填成真实值。\n"
            f'当前值：{"(空)" if not key else key[:8] + "..."}'
        )
    base = (cfg.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE).rstrip("/")
    model = model or cfg.get("DEEPSEEK_MODEL") or DEFAULT_MODEL
    url = f"{base}/chat/completions"

    payload: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": int(max_tokens),
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if disable_thinking:
        # DeepSeek 思考模式开关；关闭后更快更省，适合批量结构化任务
        payload["thinking"] = {"type": "disabled"}

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_err: Exception | None = None

    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            if verbose:
                print(f"    [llm] attempt {attempt} -> {url} model={model}")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            content = msg.get("content")
            finish = choice.get("finish_reason")
            usage = data.get("usage") or {}

            if (content is None or not str(content).strip()) and finish == "length":
                raise RuntimeError(
                    "返回内容为空且 finish_reason=length：max_tokens 被思考内容吃光。"
                    f"请提高 max_tokens（当前 {max_tokens}）或设置 disable_thinking=True。"
                )
            return {
                "content": content or "",
                "usage": usage,
                "model": data.get("model", model),
                "finish_reason": finish,
            }
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "ignore")[:600]
            except Exception:
                pass
            last_err = RuntimeError(f"HTTP {exc.code}: {detail}")
            if exc.code in (400, 401, 403, 404):
                break  # 参数/鉴权类错误，重试无意义
        except Exception as exc:
            last_err = exc
        if attempt < retries:
            time.sleep(min(2 ** attempt, 15))

    raise RuntimeError(f"调用失败（已重试 {retries} 次）：{last_err}")


def ask(prompt: str, system: str | None = None, **kw) -> str:
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    return chat(msgs, **kw)["content"]


# ----------------------------------------------------------------------------
# 自检
# ----------------------------------------------------------------------------
def selftest() -> int:
    print("=" * 62)
    print("LLM 配置自检")
    print("=" * 62)
    cfg = load_env()
    print(f"配置文件      : {ENV_FILE}  {'存在' if ENV_FILE.exists() else '不存在'}")
    print(f"BASE_URL      : {cfg.get('DEEPSEEK_BASE_URL') or DEFAULT_BASE + ' (默认)'}")
    print(f"MODEL         : {cfg.get('DEEPSEEK_MODEL') or DEFAULT_MODEL + ' (默认)'}")
    ok = has_valid_key(cfg)
    print(f"API Key       : {'已配置' if ok else '未配置或仍是占位符'}")
    if not ok:
        print("\n[!] 请先编辑配置文件并填入真实 Key，然后重新运行本自检。")
        print(f"    文件位置：{ENV_FILE}")
        return 2

    print("\n正在发起一次最小测试调用 ...")
    try:
        out = ask("只回答两个字：就绪", max_tokens=200, temperature=0, disable_thinking=True)
        print(f"[OK] 调用成功，模型返回：{out.strip()[:50]}")
    except Exception as exc:
        print(f"[FAIL] {exc}")
        return 3

    print("\n正在测试 JSON 输出模式 ...")
    try:
        out = ask(
            '只输出 JSON，不要任何解释。格式：{"ok": true, "n": 3}',
            json_mode=True,
            max_tokens=300,
            temperature=0,
            disable_thinking=True,
        )
        print(f"[OK] JSON 模式返回：{extract_json(out)}")
    except Exception as exc:
        print(f"[FAIL] {exc}")
        return 4

    print("\n全部通过。可以开始运行 M4/M5/M6 模块。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 调用封装与自检")
    ap.add_argument("--selftest", action="store_true", help="检查配置并发起最小测试调用")
    ap.add_argument("--ask", metavar="问题", help="直接问一句（用于快速验证）")
    ap.add_argument("--model", help="临时覆盖模型名")
    ap.add_argument("--json", action="store_true", help="开启 JSON 输出模式")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.ask:
        try:
            print(ask(args.ask, model=args.model, json_mode=args.json))
            return 0
        except Exception as exc:
            print(f"[FAIL] {exc}")
            return 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
