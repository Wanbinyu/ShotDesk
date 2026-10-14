"""外部视频接口。默认占位，不访问网络。http 模式按提交后轮询的约定取片。"""

import json
import urllib.error
import urllib.request
from pathlib import Path

from shotdesk.errors import DeskError

TIMEOUT = 120


def _request(url: str, key: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise DeskError(f"外部接口返回 {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise DeskError(f"外部接口连不上：{exc.reason}") from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise DeskError("外部接口没有返回 JSON") from exc
    if not isinstance(parsed, dict):
        raise DeskError("外部接口没有返回 JSON 对象")
    return parsed


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(url, dest)
    except urllib.error.URLError as exc:
        raise DeskError(f"外部视频下载失败：{exc.reason}") from exc
    if not dest.is_file() or dest.stat().st_size == 0:
        raise DeskError("外部视频是空文件")


def fetch_external(settings: dict, prompt: str, duration_ms: int, dest: Path) -> str:
    """下载一家供应商的成片。返回对方的任务号。"""
    if settings.get("external_mode") != "http":
        raise DeskError("外部接口还是占位模式")
    base = str(settings.get("external_base_url") or "").rstrip("/")
    if not base:
        raise DeskError("先在设置里填写外部接口地址")
    created = _request(
        base + "/v1/videos",
        settings.get("external_api_key") or "",
        {
            "model": settings.get("external_model") or "external-video",
            "prompt": prompt,
            "duration_ms": duration_ms,
            "aspect": "9:16",
        },
    )
    task_id = str(created.get("task_id") or "")
    status = str(created.get("status") or "")
    url = str(created.get("url") or "")
    error = str(created.get("error") or "")
    if status == "failed":
        raise DeskError(error or "外部接口生成失败")
    if url and status in ("", "ready"):
        _download(url, dest)
        return task_id
    if not task_id:
        raise DeskError("外部接口没有返回任务号")

    import time

    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        polled = _request(f"{base}/v1/videos/{task_id}", settings.get("external_api_key") or "")
        status = str(polled.get("status") or "")
        if status == "failed":
            raise DeskError(str(polled.get("error") or "外部接口生成失败"))
        if status == "ready" and polled.get("url"):
            _download(str(polled["url"]), dest)
            return task_id
        time.sleep(0.4)
    raise DeskError("外部接口超时，没有等到成片")
