"""用 pyJianYingDraft 写一份可继续改的剪映草稿。写不成就只保留时间轴包。"""

import json
import uuid
from pathlib import Path

from shotdesk.errors import DeskError


def safe_name(name: str) -> str:
    cleaned = "".join(char for char in name if char not in '<>:"/\\|?*').strip()
    return (cleaned[:40] or "未命名")


def suggest_jianying_dir(home: Path | None = None) -> str:
    """本机如果装着剪映，返回草稿目录。没找到就给空字符串。"""
    base = home or Path.home()
    candidates = [
        base / "AppData" / "Local" / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft",
        base / "Videos" / "JianyingPro Drafts",
        base / "Movies" / "JianyingPro Drafts",
        base / "Documents" / "JianyingPro Drafts",
    ]
    for path in candidates:
        if path.is_dir():
            return str(path)
    return ""


def write_draft(draft_root: Path, name: str, width: int, height: int, shots: list[dict]) -> Path:
    if not draft_root.is_dir():
        raise DeskError("剪映草稿目录不存在")
    try:
        import pyJianYingDraft as draft
    except ImportError as exc:
        raise DeskError("没有安装 pyJianYingDraft") from exc

    folder = draft.DraftFolder(str(draft_root))
    script = folder.create_draft(name, width, height, fps=25, allow_replace=True)
    script.append_track(draft.TrackSpec(draft.TrackType.video, "画面"))
    script.append_track(draft.TrackSpec(draft.TrackType.text, "字幕"))
    cursor = 0
    for shot in shots:
        material = draft.VideoMaterial(shot["video"])
        duration = material.duration
        if duration <= 0:
            raise DeskError("有一条镜头没有时长，写不进剪映")
        slot = draft.trange(cursor, duration)
        script.add_segment(draft.VideoSegment(material, slot), "画面")
        dialogue = " ".join((shot.get("dialogue") or "").split())
        if dialogue:
            script.add_segment(
                draft.TextSegment(
                    dialogue,
                    slot,
                    style=draft.TextStyle(size=8, align=1, auto_wrapping=True),
                    clip_settings=draft.ClipSettings(transform_y=-0.72),
                ),
                "字幕",
            )
        cursor += duration
    script.save()
    draft_dir = draft_root / name
    meta_path = draft_dir / "draft_meta_info.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["draft_name"] = name
        meta["draft_id"] = str(uuid.uuid4()).upper()
        meta["draft_fold_path"] = str(draft_dir)
        meta["draft_root_path"] = str(draft_root)
        meta["tm_duration"] = cursor
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return draft_dir
