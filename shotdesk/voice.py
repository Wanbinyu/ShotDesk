"""对白配音。开了就用 Edge TTS，失败时改成等长静音，镜头仍然能出。"""

import asyncio
from pathlib import Path

from shotdesk.media import write_silence


def synthesize(text: str, dest_stem: Path, voice: str, enabled: bool, duration_ms: int) -> tuple[Path, str]:
    """写出 mp3 或 wav，并返回给界面看的一句说明。说明为空表示配音成功。"""
    line = " ".join((text or "").split())
    dest_stem.parent.mkdir(parents=True, exist_ok=True)
    wav = dest_stem.with_suffix(".wav")
    mp3 = dest_stem.with_suffix(".mp3")

    def silent(note: str) -> tuple[Path, str]:
        write_silence(wav, duration_ms)
        if mp3.exists():
            mp3.unlink()
        return wav, note

    if not line:
        return silent("这句没有对白，用了静音。")
    if not enabled:
        return silent("配音已关闭，用了静音。")
    try:
        import edge_tts
    except ImportError:
        return silent("没有安装 edge-tts，用了静音。")

    async def _run() -> None:
        communicate = edge_tts.Communicate(line, voice or "zh-CN-XiaoxiaoNeural")
        await communicate.save(str(mp3))

    try:
        asyncio.run(_run())
    except Exception:
        return silent("配音没有连上，用了静音。画面已经生成。")
    if not mp3.is_file() or mp3.stat().st_size == 0:
        return silent("配音没有连上，用了静音。画面已经生成。")
    if wav.exists():
        wav.unlink()
    return mp3, ""
