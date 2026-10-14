"""本机设置。密钥只留在 data/settings.json，接口不回传原文。"""

import json
from pathlib import Path

DEFAULTS = {
    "external_mode": "stub",
    "external_base_url": "",
    "external_api_key": "",
    "external_model": "external-video",
    "external_cents_per_second": 10,
    "jianying_draft_dir": "",
    "tts_enabled": True,
    "tts_voice": "zh-CN-XiaoxiaoNeural",
}


def settings_path(db_path: Path) -> Path:
    return db_path.parent / "settings.json"


def load_settings(db_path: Path) -> dict:
    path = settings_path(db_path)
    data = dict(DEFAULTS)
    if path.is_file():
        stored = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            data.update({key: stored[key] for key in DEFAULTS if key in stored})
    data["external_mode"] = "http" if data["external_mode"] == "http" else "stub"
    data["external_cents_per_second"] = max(0, int(data["external_cents_per_second"] or 0))
    data["tts_enabled"] = bool(data["tts_enabled"])
    return data


def save_settings(db_path: Path, incoming: dict, current: dict) -> dict:
    merged = dict(current)
    for key in DEFAULTS:
        if key not in incoming:
            continue
        if key == "external_api_key" and incoming[key] in ("", None, "••••"):
            continue
        merged[key] = incoming[key]
    merged = load_settings_from_dict(merged)
    path = settings_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def load_settings_from_dict(raw: dict) -> dict:
    data = dict(DEFAULTS)
    data.update({key: raw[key] for key in DEFAULTS if key in raw})
    data["external_mode"] = "http" if data["external_mode"] == "http" else "stub"
    data["external_cents_per_second"] = max(0, int(data["external_cents_per_second"] or 0))
    data["tts_enabled"] = bool(data["tts_enabled"])
    data["external_base_url"] = str(data["external_base_url"] or "").strip()
    data["external_model"] = str(data["external_model"] or "external-video").strip()
    data["external_api_key"] = str(data["external_api_key"] or "")
    data["jianying_draft_dir"] = str(data["jianying_draft_dir"] or "").strip()
    data["tts_voice"] = str(data["tts_voice"] or DEFAULTS["tts_voice"]).strip()
    return data


def public_settings(settings: dict) -> dict:
    shown = dict(settings)
    shown["external_api_key_set"] = bool(settings.get("external_api_key"))
    shown["external_api_key"] = ""
    return shown
