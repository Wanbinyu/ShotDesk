"""把剧本文本拆成镜头。不调用模型，空行分段，行首标签写画面和对白。"""

import re

from shotdesk.errors import DeskError

LABELS = {
    "画面": "visual_prompt",
    "对白": "dialogue",
    "角色": "cast",
    "运镜": "camera",
    "时长": "duration",
}


def parse_duration(value: str) -> int:
    text = value.strip().lower().replace("毫秒", "ms")
    if text.endswith("ms"):
        number = float(text[:-2])
        return int(number)
    if text.endswith("秒"):
        return int(float(text[:-1]) * 1000)
    if text.endswith("s"):
        return int(float(text[:-1]) * 1000)
    number = float(text)
    if number < 100:
        return int(number * 1000)
    return int(number)


def _take(line: str) -> tuple[str, str] | None:
    for label, key in LABELS.items():
        for mark in ("：", ":"):
            prefix = label + mark
            if line.startswith(prefix):
                return key, line[len(prefix):].strip()
    return None


def parse_script(text: str) -> list[dict]:
    body = (text or "").strip()
    if not body:
        raise DeskError("剧本是空的")
    shots = []
    for block in re.split(r"\n\s*\n", body):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        item = {
            "title": "",
            "visual_prompt": "",
            "dialogue": "",
            "camera": "",
            "cast": "",
            "duration_ms": 5000,
            "source": "builtin",
        }
        loose = []
        for line in lines:
            taken = _take(line)
            if taken is None:
                loose.append(line)
                continue
            key, value = taken
            if key == "duration":
                try:
                    item["duration_ms"] = parse_duration(value)
                except ValueError as exc:
                    raise DeskError(f"时长看不懂：{value}") from exc
            else:
                item[key] = value
        if not item["visual_prompt"] and loose:
            item["visual_prompt"] = loose[0]
            loose = loose[1:]
        if not item["dialogue"] and loose:
            item["dialogue"] = " ".join(loose)
        if not item["visual_prompt"] and not item["dialogue"]:
            continue
        item["title"] = (item["visual_prompt"] or item["dialogue"])[:18]
        shots.append(item)
    if not shots:
        raise DeskError("没有拆出镜头。空行分段，画面：和对白：写在段里。")
    return shots
