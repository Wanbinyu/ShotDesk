"""镜头生成、导入、拼接和导出。时间轴只认每个镜头已经落盘的那条视频。"""

import json
import shutil
import threading
from pathlib import Path

from shotdesk.db import (
    add_export,
    claim_shot,
    get_project_row,
    get_shot,
    list_assets,
    list_shots,
    mark_shot,
    now,
    project_view,
    replace_shots,
)
from shotdesk.errors import DeskError
from shotdesk.jianying import safe_name, write_draft
from shotdesk.media import concat, ken_burns, mix_bgm, mux_fit, normalize, probe_duration_ms, render_card, write_srt
from shotdesk.provider import fetch_external
from shotdesk.script import parse_script
from shotdesk.voice import synthesize

GENERATED_MAX_MS = 15000


def project_dir(root: Path, project_id: int) -> Path:
    return root / "data" / "media" / str(project_id)


def inside(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise DeskError("路径越界")
    return resolved


def clamp_generated(duration_ms: int) -> int:
    if duration_ms < 1000:
        raise DeskError("生成的镜头至少 1 秒")
    if duration_ms > GENERATED_MAX_MS:
        raise DeskError("自带和外部镜头最长 15 秒。更长的片子请导入视频。")
    return duration_ms


def require_script(project: dict) -> None:
    if project["script_status"] != "approved":
        raise DeskError("先通过剧本，再生成或导入画面。")


def require_assets(project: dict) -> None:
    if not project["assets_locked"]:
        raise DeskError("先锁定角色和场景，再生成画面。导入视频不用锁定。")


def split_script(conn, project_id: int, sources: list[str] | None = None) -> dict:
    project = get_project_row(conn, project_id)
    shots = parse_script(project["script"])
    for index, shot in enumerate(shots):
        if sources and index < len(sources):
            shot["source"] = sources[index]
    replace_shots(conn, project_id, shots)
    return project_view(conn, project_id)


def _portraits(root: Path, assets: list[dict], cast: str, prompt: str) -> list[Path]:
    text = f"{cast} {prompt}"
    found = []
    for asset in assets:
        if asset["kind"] != "character" or not asset["image_path"]:
            continue
        if asset["name"] and asset["name"] in text:
            found.append(root / asset["image_path"])
    return found


def _render_card_video(root: Path, project: dict, shot: dict, assets: list[dict], source: str, still: Path, silent: Path) -> None:
    render_card(
        still,
        index=shot["position"] + 1,
        title=shot["title"],
        prompt=shot["visual_prompt"],
        dialogue=shot["dialogue"],
        cast=shot["cast"],
        source=source,
        portraits=_portraits(root, assets, shot["cast"], shot["visual_prompt"]),
    )
    ken_burns(still, silent, shot["duration_ms"])


def _fail_shot(conn_factory, lock: threading.Lock, shot_id: int, message: str, task_id: str = "") -> None:
    with lock:
        conn = conn_factory()
        try:
            mark_shot(conn, shot_id, "failed", error=message[:500], provider_task_id=task_id)
            conn.commit()
        finally:
            conn.close()


def produce_shot(conn_factory, root: Path, shot_id: int, settings: dict, lock: threading.Lock) -> None:
    """生成一条自带或外部镜头。数据库锁只包住读写，不包住 ffmpeg。"""
    try:
        with lock:
            conn = conn_factory()
            try:
                shot = get_shot(conn, shot_id)
                project = get_project_row(conn, shot["project_id"])
                require_script(project)
                if shot["source"] == "import":
                    raise DeskError("这一条是导入镜头，请上传视频。")
                require_assets(project)
                duration_ms = clamp_generated(int(shot["duration_ms"]))
                mark_shot(conn, shot_id, "running", error="", duration_ms=duration_ms)
                assets = list_assets(conn, project["id"])
                conn.commit()
                snapshot = {"project": project, "shot": get_shot(conn, shot_id), "assets": assets}
            finally:
                conn.close()
    except Exception as exc:
        _fail_shot(conn_factory, lock, shot_id, str(exc) or "生成失败")
        return

    shot = snapshot["shot"]
    project = snapshot["project"]
    media = project_dir(root, project["id"])
    silent = media / "silent" / f"shot-{shot_id}.mp4"
    final = media / "shots" / f"shot-{shot_id}.mp4"
    voice_stem = media / "voice" / f"shot-{shot_id}"
    task_id = ""
    try:
        if shot["source"] == "builtin":
            _render_card_video(root, project, shot, snapshot["assets"], "builtin", media / "cards" / f"shot-{shot_id}.png", silent)
            cost = 0
            cost_note = ""
        elif shot["source"] == "external" and settings.get("external_mode") == "http":
            downloaded = media / "remote" / f"shot-{shot_id}.mp4"
            prompt = "；".join(part for part in (shot["visual_prompt"], shot["camera"], shot["cast"]) if part)
            task_id = fetch_external(settings, prompt or shot["title"], shot["duration_ms"], downloaded)
            normalize(downloaded, silent)
            cents = int(settings.get("external_cents_per_second") or 0)
            cost = int(round(cents * shot["duration_ms"] / 1000))
            cost_note = "估价"
        else:
            _render_card_video(root, project, shot, snapshot["assets"], "external", media / "cards" / f"shot-{shot_id}.png", silent)
            cents = int(settings.get("external_cents_per_second") or 0)
            cost = int(round(cents * shot["duration_ms"] / 1000))
            cost_note = "占位估价"
        voice, voice_note = synthesize(
            shot["dialogue"],
            voice_stem,
            settings.get("tts_voice") or "",
            bool(settings.get("tts_enabled")),
            shot["duration_ms"],
        )
        mux_fit(silent, voice, final)
        relative_video = final.relative_to(root).as_posix()
        relative_voice = voice.relative_to(root).as_posix()
    except Exception as exc:
        message = str(exc) if str(exc) else "生成失败"
        with lock:
            conn = conn_factory()
            try:
                mark_shot(conn, shot_id, "failed", error=message[:500], provider_task_id=task_id)
                conn.commit()
            finally:
                conn.close()
        return

    with lock:
        conn = conn_factory()
        try:
            mark_shot(
                conn,
                shot_id,
                "ready",
                file_path=relative_video,
                voice_path=relative_voice,
                error="",
                voice_note=voice_note,
                cost_cents=cost,
                cost_note=cost_note,
                provider_task_id=task_id,
                generated_at=now(),
                duration_ms=probe_duration_ms(final),
            )
            conn.commit()
        finally:
            conn.close()


def import_shot(conn, root: Path, shot_id: int, upload: Path) -> dict:
    shot = get_shot(conn, shot_id)
    project = get_project_row(conn, shot["project_id"])
    require_script(project)
    media = project_dir(root, project["id"])
    final = media / "shots" / f"shot-{shot_id}.mp4"
    duration_ms = normalize(upload, final)
    relative = final.relative_to(root).as_posix()
    mark_shot(
        conn,
        shot_id,
        "ready",
        file_path=relative,
        voice_path="",
        error="",
        voice_note="导入的视频保留原声。",
        cost_cents=0,
        cost_note="",
        provider_task_id="",
        generated_at=now(),
        duration_ms=duration_ms,
    )
    conn.execute("UPDATE shots SET source = 'import' WHERE id = ?", (shot_id,))
    return project_view(conn, project["id"])


def _ready_clips(conn, root: Path, project_id: int) -> tuple[dict, list[dict], list[Path]]:
    project = get_project_row(conn, project_id)
    shots = list_shots(conn, project_id)
    if not shots:
        raise DeskError("还没有镜头")
    missing = [shot["position"] + 1 for shot in shots if shot["status"] != "ready" or not shot["file_path"]]
    if missing:
        names = "、".join(str(number) for number in missing)
        raise DeskError(f"镜头 {names} 还没有画面")
    clips = []
    for shot in shots:
        path = inside(root, root / shot["file_path"])
        if not path.is_file():
            raise DeskError(f"镜头 {shot['position'] + 1} 的文件不在了")
        clips.append(path)
    return project, shots, clips


def compose(conn, root: Path, project_id: int) -> dict:
    project, shots, clips = _ready_clips(conn, root, project_id)
    export_dir = root / "data" / "exports" / str(project_id)
    if export_dir.exists():
        shutil.rmtree(export_dir)
    export_dir.mkdir(parents=True)
    clip_dir = export_dir / "clips"
    clip_dir.mkdir()
    copied = []
    for index, clip in enumerate(clips, start=1):
        target = clip_dir / f"{index:02d}.mp4"
        shutil.copy2(clip, target)
        copied.append(target)
    preview = export_dir / "preview.mp4"
    concat(copied, preview)
    bgm = project["bgm_path"]
    if bgm:
        bgm_path = inside(root, root / bgm)
        if bgm_path.is_file():
            mixed = export_dir / "preview-bgm.mp4"
            mix_bgm(preview, bgm_path, mixed)
            mixed.replace(preview)
    cues = []
    cursor = 0
    for shot in shots:
        start = cursor
        cursor += int(shot["duration_ms"])
        cues.append((start, cursor, shot["dialogue"]))
    srt = export_dir / "subtitles.srt"
    write_srt(cues, srt)
    timeline = {
        "name": project["name"],
        "width": 1080,
        "height": 1920,
        "fps": 25,
        "shots": [
            {
                "index": shot["position"] + 1,
                "source": shot["source"],
                "title": shot["title"],
                "visual_prompt": shot["visual_prompt"],
                "dialogue": shot["dialogue"],
                "camera": shot["camera"],
                "cast": shot["cast"],
                "file": f"clips/{index:02d}.mp4",
                "start_ms": sum(int(item["duration_ms"]) for item in shots[:index - 1]),
                "duration_ms": int(shot["duration_ms"]),
                "cost_cents": shot["cost_cents"],
                "cost_note": shot["cost_note"],
            }
            for index, shot in enumerate(shots, start=1)
        ],
    }
    (export_dir / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    (export_dir / "README.txt").write_text(
        "这是一条可预览的成片和剪辑时间轴。\n"
        "preview.mp4 是拼接结果。\n"
        "subtitles.srt 是对白字幕。\n"
        "clips 里每个文件对应一个镜头，来源写在 timeline.json。\n"
        "精剪、转场和调色放到剪映草稿里做。\n",
        encoding="utf-8",
    )
    relative_preview = preview.relative_to(root).as_posix()
    relative_srt = srt.relative_to(root).as_posix()
    relative_dir = export_dir.relative_to(root).as_posix()
    return {
        "preview_path": relative_preview,
        "srt_path": relative_srt,
        "package_dir": relative_dir,
        "preview_file": preview,
        "shots": shots,
        "project": project,
    }


def export_project(conn, root: Path, project_id: int, settings: dict) -> dict:
    package = compose(conn, root, project_id)
    message = "时间轴包已写出。"
    jianying_dir = ""
    draft_setting = (settings.get("jianying_draft_dir") or "").strip()
    if not draft_setting:
        message += "还没填剪映草稿目录，所以没有写草稿。"
    else:
        try:
            draft_root = Path(draft_setting)
            name = safe_name(f"分镜成片台-{project_id}-{package['project']['name']}")
            clips = sorted((root / package["package_dir"] / "clips").glob("*.mp4"))
            shots = []
            for clip, shot in zip(clips, package["shots"]):
                shots.append({"video": str(clip), "dialogue": shot["dialogue"]})
            draft_dir = write_draft(draft_root, name, 1080, 1920, shots)
            jianying_dir = str(draft_dir)
            message += f"剪映草稿已写到 {draft_dir}。"
        except Exception as exc:
            message += "剪映草稿没有写成：" + str(exc)
    add_export(
        conn,
        project_id,
        {
            "preview_path": package["preview_path"],
            "srt_path": package["srt_path"],
            "package_dir": package["package_dir"],
            "jianying_dir": jianying_dir,
            "message": message,
        },
    )
    view = project_view(conn, project_id)
    view["message"] = message
    return view


def begin_generate(conn, shot_id: int) -> dict:
    shot = get_shot(conn, shot_id)
    project = get_project_row(conn, shot["project_id"])
    require_script(project)
    if shot["source"] == "import":
        raise DeskError("这一条是导入镜头，请上传视频。")
    require_assets(project)
    clamp_generated(int(shot["duration_ms"]))
    claim_shot(conn, shot_id)
    return project_view(conn, project["id"])


def store_upload(root: Path, project_id: int, filename: str, content: bytes) -> Path:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}:
        raise DeskError("请上传 mp4、mov、mkv、webm 或 avi")
    if len(content) > 200 * 1024 * 1024:
        raise DeskError("视频超过 200MB")
    if not content:
        raise DeskError("文件是空的")
    folder = project_dir(root, project_id) / "inbox"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"upload{suffix}"
    target.write_bytes(content)
    return target


def store_bgm(conn, root: Path, project_id: int, filename: str, content: bytes) -> dict:
    get_project_row(conn, project_id)
    suffix = Path(filename).suffix.lower()
    if suffix not in {".mp3", ".wav", ".m4a", ".aac"}:
        raise DeskError("背景音乐请用 mp3、wav、m4a 或 aac")
    if not content or len(content) > 30 * 1024 * 1024:
        raise DeskError("背景音乐为空，或超过 30MB")
    folder = project_dir(root, project_id)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"bgm{suffix}"
    target.write_bytes(content)
    from shotdesk.db import set_bgm

    set_bgm(conn, project_id, target.relative_to(root).as_posix())
    return project_view(conn, project_id)


def clear_bgm(conn, root: Path, project_id: int) -> dict:
    from shotdesk.db import set_bgm

    project = get_project_row(conn, project_id)
    relative = project.get("bgm_path") or ""
    if relative:
        path = inside(root, root / relative)
        if path.is_file():
            path.unlink()
    set_bgm(conn, project_id, "")
    return project_view(conn, project_id)


def store_asset_image(conn, root: Path, asset_id: int, content: bytes) -> dict:
    from shotdesk.db import set_asset_image

    row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if row is None:
        raise DeskError("没有这个资产")
    if not content or len(content) > 15 * 1024 * 1024:
        raise DeskError("图片为空，或超过 15MB")
    from PIL import Image
    import io

    try:
        image = Image.open(io.BytesIO(content)).convert("RGB")
    except OSError as exc:
        raise DeskError("这张图打不开") from exc
    folder = project_dir(root, row["project_id"]) / "assets"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"asset-{asset_id}.jpg"
    image.thumbnail((960, 960))
    image.save(target, "JPEG", quality=90)
    set_asset_image(conn, asset_id, target.relative_to(root).as_posix())
    return project_view(conn, row["project_id"])
