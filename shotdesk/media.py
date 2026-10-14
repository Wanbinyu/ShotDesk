"""静帧、推拉视频、导入归一和预览拼接。画面尺寸统一成 1080×1920、25 帧。"""

import shutil
import subprocess
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from shotdesk.errors import DeskError

WIDTH = 1080
HEIGHT = 1920
FPS = 25

PALETTES = {
    "builtin": ((18, 42, 36), (232, 196, 126), "自带生成"),
    "external": ((48, 28, 22), (244, 162, 124), "外部接口"),
    "import": ((24, 28, 42), (168, 196, 232), "导入"),
}


def ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise DeskError("没有找到 ffmpeg。先安装 ffmpeg，再生成画面。")
    return path


def ffprobe() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise DeskError("没有找到 ffprobe。")
    return path


def run_ffmpeg(args: list[str], timeout: int = 180) -> None:
    proc = subprocess.run(
        [ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "未知错误").strip()
        raise DeskError("ffmpeg 失败：" + detail[-500:])


def probe_duration_ms(path: Path) -> int:
    proc = subprocess.run(
        [
            ffprobe(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if proc.returncode != 0:
        raise DeskError("读不到视频时长")
    try:
        return max(1, int(float(proc.stdout.strip()) * 1000))
    except ValueError as exc:
        raise DeskError("读不到视频时长") from exc


def has_audio(path: Path) -> bool:
    proc = subprocess.run(
        [
            ffprobe(),
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    return "audio" in (proc.stdout or "")


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    names = ["msyhbd.ttc", "msyh.ttc"] if bold else ["msyh.ttc", "msyhbd.ttc"]
    folder = Path(r"C:\Windows\Fonts")
    for name in names:
        path = folder / name
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int, limit: int) -> list[str]:
    value = " ".join((text or "").split())
    if not value:
        return []
    lines = []
    current = ""
    for char in value:
        trial = current + char
        if draw.textlength(trial, font=font) <= width:
            current = trial
            continue
        if current:
            lines.append(current)
        current = char
        if len(lines) >= limit:
            break
    if current and len(lines) < limit:
        lines.append(current)
    if len(lines) == limit and len("".join(lines)) < len(value):
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def render_card(
    path: Path,
    *,
    index: int,
    title: str,
    prompt: str,
    dialogue: str,
    cast: str,
    source: str,
    portraits: list[Path],
) -> None:
    ink, accent, label = PALETTES.get(source, PALETTES["builtin"])
    image = Image.new("RGB", (WIDTH, HEIGHT), ink)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 18), fill=accent)
    badge = _font(36, bold=True)
    title_font = _font(72, bold=True)
    body = _font(42)
    small = _font(32)
    draw.text((80, 90), f"{label}   镜头 {index}", font=badge, fill=accent)
    y = 190
    for line in _wrap(draw, title or "未命名镜头", title_font, WIDTH - 160, 3):
        draw.text((80, y), line, font=title_font, fill=(245, 242, 234))
        y += 92
    y += 24
    draw.text((80, y), "画面", font=small, fill=accent)
    y += 56
    for line in _wrap(draw, prompt or "没有画面说明", body, WIDTH - 160, 7):
        draw.text((80, y), line, font=body, fill=(226, 222, 210))
        y += 64
    if cast:
        y += 20
        draw.text((80, y), "角色  " + cast, font=small, fill=accent)
        y += 56
    face_x = 80
    for portrait in portraits[:3]:
        if not portrait.is_file():
            continue
        try:
            face = Image.open(portrait).convert("RGB")
        except OSError:
            continue
        face.thumbnail((180, 180))
        image.paste(face, (face_x, min(y, HEIGHT - 620)))
        face_x += 200
    draw.rectangle((64, HEIGHT - 460, WIDTH - 64, HEIGHT - 120), outline=accent, width=3)
    draw.text((96, HEIGHT - 420), "对白", font=small, fill=accent)
    line_y = HEIGHT - 350
    for line in _wrap(draw, dialogue or "（无对白）", body, WIDTH - 200, 3):
        draw.text((96, line_y), line, font=body, fill=(245, 242, 234))
        line_y += 64
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")


def write_silence(path: Path, duration_ms: int) -> None:
    frames = int(44100 * max(duration_ms, 1) / 1000)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(b"\x00\x00" * frames)


def ken_burns(still: Path, dest: Path, duration_ms: int) -> None:
    frames = max(FPS, int(round(duration_ms / 1000 * FPS)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-loop",
            "1",
            "-framerate",
            str(FPS),
            "-i",
            str(still),
            "-vf",
            (
                f"scale=2160:3840,zoompan=z='min(zoom+0.0012,1.12)':"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS}"
            ),
            "-frames:v",
            str(frames),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(dest),
        ]
    )


def mux_fit(video: Path, audio: Path, dest: Path) -> None:
    """把配音贴进画面。时长以视频为准，配音短了补静音，长了截断。"""
    duration = probe_duration_ms(video) / 1000
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-i",
            str(video),
            "-i",
            str(audio),
            "-filter_complex",
            f"[1:a]aformat=sample_rates=44100:channel_layouts=stereo,apad,atrim=0:{duration:.3f}[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-t",
            f"{duration:.3f}",
            str(dest),
        ]
    )


def normalize(source: Path, dest: Path, limit_ms: int = 180000) -> int:
    """把导入视频收成和自带镜头一样的画幅、帧率和音频，后面才能拼。"""
    if not source.is_file():
        raise DeskError("导入的文件不在了")
    duration_ms = probe_duration_ms(source)
    if duration_ms < 1000:
        raise DeskError("导入的视频短于 1 秒")
    if duration_ms > limit_ms:
        raise DeskError("导入的视频长于 180 秒")
    dest.parent.mkdir(parents=True, exist_ok=True)
    video_filter = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,fps={FPS},setsar=1"
    )
    if has_audio(source):
        run_ffmpeg(
            [
                "-i",
                str(source),
                "-vf",
                video_filter,
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-ar",
                "44100",
                "-ac",
                "2",
                "-b:a",
                "160k",
                str(dest),
            ]
        )
    else:
        run_ffmpeg(
            [
                "-i",
                str(source),
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=stereo",
                "-filter_complex",
                f"[0:v]{video_filter}[v]",
                "-map",
                "[v]",
                "-map",
                "1:a",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(dest),
            ]
        )
    return probe_duration_ms(dest)


def concat(clips: list[Path], dest: Path) -> None:
    if not clips:
        raise DeskError("没有可以拼接的镜头")
    list_path = dest.with_suffix(".txt")
    lines = []
    for clip in clips:
        escaped = str(clip.resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_path.write_text("\n".join(lines), encoding="utf-8")
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(dest)])


def mix_bgm(video: Path, bgm: Path, dest: Path) -> None:
    duration = probe_duration_ms(video) / 1000
    run_ffmpeg(
        [
            "-i",
            str(video),
            "-i",
            str(bgm),
            "-filter_complex",
            (
                f"[1:a]aformat=sample_rates=44100:channel_layouts=stereo,volume=0.18,"
                f"apad,atrim=0:{duration:.3f}[b];"
                f"[0:a][b]amix=inputs=2:duration=first:dropout_transition=0[a]"
            ),
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            str(dest),
        ]
    )


def write_srt(cues: list[tuple[int, int, str]], dest: Path) -> None:
    def stamp(ms: int) -> str:
        ms = max(0, ms)
        hours, rem = divmod(ms, 3_600_000)
        minutes, rem = divmod(rem, 60_000)
        seconds, millis = divmod(rem, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    blocks = []
    index = 1
    for start, end, text in cues:
        line = " ".join((text or "").split())
        if not line:
            continue
        blocks.append(f"{index}\n{stamp(start)} --> {stamp(end)}\n{line}\n")
        index += 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(blocks), encoding="utf-8")
