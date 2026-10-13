# -*- coding: utf-8 -*-
"""数据默认留在本机；离开本机须手动批准，授权按类别、接收方和用途绑定。"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

EGRESS_LEVELS = {
    "none": "不出本机（全部在本机完成）",
    "page_text": "把页级文本发往第三方服务（不含原始文件）",
    "transcript": "把转写文本发往第三方服务（不含原始音频）",
    "personal": "把个性化数据发往第三方服务（复习产物/笔记/错题/作答记录等）",
    "page_images": "把页面图像发往第三方服务（含版面与图表信息）",
    "audio": "把音频发往第三方服务（含讲课人声音，可能含学生声音）",
    "document": "把原始素材文件整体发往第三方服务（教材/规范原文）",
}
MANUAL_APPROVAL = {"audio", "transcript", "personal"}
# Compatibility for callers inspecting the former unconditional prohibition.
NEVER_EGRESS: set[str] = set()
COPYRIGHT_NOTICE = "禁止直接将未获得版权的书目发往云端。违反者后果自负。工具不做技术限制：素材权利由使用人判断。"


def is_never(level: str) -> bool:
    return level in NEVER_EGRESS


def never_engines(engines: dict) -> list[dict]:
    return [r for r in manifest(engines) if is_never(r["egress"])]


def normalize_recipient(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("接口必须为 http(s) 地址，且不得在 URL 中放置凭据")
    if parts.query or parts.fragment:
        raise ValueError("接口地址不得包含查询参数或片段")
    host = parts.hostname.lower()
    port = parts.port
    if port == (443 if parts.scheme == "https" else 80):
        port = None
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc += f":{port}"
    return urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", ""))


def is_local_recipient(value: str) -> bool:
    host = urlsplit(normalize_recipient(value)).hostname
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def manifest(engines: dict) -> list[dict]:
    return [{"kind": k, "engine": e.spec.name, "label": e.spec.label,
             "egress": e.spec.egress, "meaning": EGRESS_LEVELS.get(e.spec.egress, "未知类别"),
             "needs_network": e.spec.needs_network, "note": e.spec.note,
             "recipient": getattr(e.spec, "recipient", "")}
            for k, e in engines.items()]


def any_egress(engines: dict) -> bool:
    return any(e.spec.egress != "none" for e in engines.values())


def consent_file(config_dir) -> Path:
    return Path(config_dir) / ".egress-consent.json"


def load_consent(config_dir) -> dict:
    try:
        data = json.loads(consent_file(config_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(config_dir, data: dict, action: str, scope: dict) -> Path:
    p = consent_file(config_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    data["schema_version"] = 2
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = p.with_name(p.name + ".tmp-" + uuid.uuid4().hex)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    # Only authorization metadata: no prompts, filenames, audio, or keys.
    config = Path(config_dir)
    audit_root = config.parent if config.name == "00_配置" else config
    audit = audit_root / "90_日志" / "egress-approvals.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    with audit.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": data["updated_at"], "action": action, **scope}, ensure_ascii=False) + "\n")
    return p


def _scope_key(category: str, recipient: str, purpose: str) -> str:
    return hashlib.sha256(json.dumps([category, recipient, purpose], ensure_ascii=False).encode()).hexdigest()


def grant_recipient(config_dir, categories: list[str], recipient: str, purpose: str, note: str) -> Path:
    if not note.strip() or not purpose.strip() or not categories:
        raise ValueError("手动批准需填写用途、确认备注并选择数据类别")
    recipient = normalize_recipient(recipient)
    if not is_local_recipient(recipient) and not recipient.startswith("https://"):
        raise ValueError("云端接口必须使用 HTTPS")
    if any(c not in EGRESS_LEVELS or c == "none" for c in categories):
        raise ValueError("未知数据类别")
    data = load_consent(config_dir)
    scopes = data.setdefault("scopes", {})
    for category in categories:
        scopes[_scope_key(category, recipient, purpose)] = {
            "category": category, "recipient": recipient, "purpose": purpose,
            "manual": True, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "note": note.strip()}
    return _save(config_dir, data, "grant", {"categories": categories, "recipient": recipient, "purpose": purpose})


def revoke(config_dir) -> Path:
    return _save(config_dir, {"granted": {}, "scopes": {}}, "revoke_all", {})


def approved_categories(config_dir, recipient: str, purpose: str) -> list[str]:
    recipient = normalize_recipient(recipient)
    scopes = load_consent(config_dir).get("scopes", {})
    return [c for c in EGRESS_LEVELS if c != "none" and
            scopes.get(_scope_key(c, recipient, purpose), {}).get("manual") is True]


def require_recipient(config_dir, category: str, recipient: str, purpose: str) -> None:
    recipient = normalize_recipient(recipient)
    if category not in EGRESS_LEVELS:
        raise ValueError("未知数据类别，拒绝发送")
    if is_local_recipient(recipient) or category == "none":
        return
    if not recipient.startswith("https://"):
        raise PermissionError("云端接口须使用 HTTPS")
    if category not in approved_categories(config_dir, recipient, purpose):
        raise PermissionError(f"尚未手动批准 {category} → {recipient}，用途：{purpose}。请在桌面端模型配置中批准，或保留本地推理。{COPYRIGHT_NOTICE}")


def grant(config_dir, engines: dict, note: str = "") -> Path:
    if not note.strip():
        raise ValueError("手动批准必须填写确认备注")
    data = load_consent(config_dir)
    grants = data.setdefault("granted", {})
    for row in manifest(engines):
        if row["egress"] == "none":
            continue
        if row["egress"] not in EGRESS_LEVELS:
            raise ValueError("未知数据类别")
        grants[f"{row['kind']}:{row['engine']}"] = {
            "egress": row["egress"], "recipient": row["recipient"], "manual": True,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "note": note.strip()}
    return _save(config_dir, data, "grant_engines", {"engines": list(grants)})


def require_consent(engines: dict, config_dir, allow_flag: bool = False) -> None:
    """CLI --allow-egress 为人工明确指定的本次授权；硬件路由不得自行添加。"""
    granted = load_consent(config_dir).get("granted", {})
    pending = []
    for row in manifest(engines):
        category = row["egress"]
        if category not in EGRESS_LEVELS:
            raise SystemExit("未知出站类别，拒绝运行")
        if category == "none":
            continue
        old = granted.get(f"{row['kind']}:{row['engine']}", {})
        match = old.get("egress") == category and old.get("recipient", "") == row["recipient"]
        # Old grants cannot silently authorize the newly permitted personal categories.
        if category in MANUAL_APPROVAL:
            match = match and old.get("manual") is True
        if not match:
            pending.append(row)
    if allow_flag and any_egress(engines):
        _save(config_dir, load_consent(config_dir), "grant_single_run", {"engines": [r["engine"] for r in manifest(engines)], "persistent": False})
    if pending and not allow_flag:
        raise SystemExit("[STOP] 数据离开本机前必须手动批准，有权上传后才能继续。\n" +
                         "\n".join(f"{r['kind']}:{r['engine']} → {r['egress']} {r['meaning']}" for r in pending) +
                         f"\n{COPYRIGHT_NOTICE}\n本次明确授权加 --allow-egress；记录授权运行 python tools/engines/egress.py --grant <确认备注>。")


def main() -> int:
    import argparse
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import kb
    from engines import all_engines
    ap = argparse.ArgumentParser(description="手动出站批准")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--grant", metavar="备注")
    ap.add_argument("--revoke", action="store_true")
    args = ap.parse_args()
    engs = all_engines()
    if args.revoke:
        print(revoke(kb.CONFIG_DIR))
    elif args.grant:
        print(grant(kb.CONFIG_DIR, engs, args.grant))
    print(json.dumps(manifest(engs), ensure_ascii=False, indent=2))
    print("所有非本地数据发送须手动批准。" + COPYRIGHT_NOTICE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
