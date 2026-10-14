"""本机工作台。只监听 127.0.0.1。"""

import json
import mimetypes
import os
import threading
import traceback
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from shotdesk.db import (
    add_asset,
    add_shot,
    connect,
    create_project,
    delete_asset,
    delete_project,
    delete_shot,
    get_shot,
    init_db,
    list_projects,
    move_shot,
    project_view,
    set_assets_locked,
    set_script_status,
    update_asset,
    update_project,
    update_shot,
)
from shotdesk.errors import DeskError
from shotdesk.jianying import suggest_jianying_dir
from shotdesk.pipeline import (
    begin_generate,
    clear_bgm,
    export_project,
    import_shot,
    produce_shot,
    split_script,
    store_asset_image,
    store_bgm,
    store_upload,
)
from shotdesk.sample import SAMPLE_ASSETS, SAMPLE_LOGLINE, SAMPLE_NAME, SAMPLE_SCRIPT
from shotdesk.settings import load_settings, public_settings, save_settings

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


class App:
    def __init__(self, db_path: Path, work_root: Path):
        self.db_path = db_path
        self.work_root = work_root
        self.lock = threading.Lock()

    def call(self, fn):
        with self.lock:
            conn = connect(self.db_path)
            try:
                init_db(conn)
                result = fn(conn)
                conn.commit()
                return result
            finally:
                conn.close()

    def factory(self):
        def make():
            conn = connect(self.db_path)
            init_db(conn)
            return conn

        return make

    def start_shot(self, shot_id: int) -> None:
        settings = load_settings(self.db_path)

        def work() -> None:
            produce_shot(self.factory(), self.work_root, shot_id, settings, self.lock)

        threading.Thread(target=work, daemon=True).start()

    def start_batch(self, shot_ids: list[int]) -> None:
        settings = load_settings(self.db_path)

        def work() -> None:
            for shot_id in shot_ids:
                produce_shot(self.factory(), self.work_root, shot_id, settings, self.lock)

        threading.Thread(target=work, daemon=True).start()


def load_sample(conn) -> dict:
    project_id = create_project(conn, SAMPLE_NAME, SAMPLE_LOGLINE, SAMPLE_SCRIPT)
    for kind, name, note in SAMPLE_ASSETS:
        add_asset(conn, project_id, kind, name, note)
    return split_script(conn, project_id, ["builtin", "external", "import"])


def prepare_db(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        init_db(conn)
        conn.commit()
    finally:
        conn.close()


class Handler(BaseHTTPRequestHandler):
    app: App
    static_dir: Path

    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/health":
                from shutil import which

                self._json({"ok": True, "ffmpeg": bool(which("ffmpeg"))})
                return
            if path == "/api/settings":
                self._json(self._settings_view())
                return
            if path == "/api/projects":
                self._json({"projects": self.app.call(list_projects)})
                return
            if path in ("/", "/index.html"):
                self._file(self.static_dir / "index.html")
                return
            if path in ("/app.js", "/app.css"):
                self._file(self.static_dir / path.lstrip("/"))
                return
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if path.startswith("/files/"):
                self._data_file(unquote(path[len("/files/") :]))
                return
            parts = _parts(path)
            if len(parts) == 3 and parts[:2] == ["api", "projects"]:
                self._json(self.app.call(lambda conn: project_view(conn, int(parts[2]))))
                return
            self._error(404, "没有这个页面")
        except DeskError as exc:
            self._error(400, str(exc))
        except Exception:
            traceback.print_exc()
            self._error(500, "服务出错了")

    def do_POST(self) -> None:
        self._route("POST")

    def do_PUT(self) -> None:
        self._route("PUT")

    def do_DELETE(self) -> None:
        self._route("DELETE")

    def _route(self, method: str) -> None:
        path = urlparse(self.path).path
        parts = _parts(path)
        try:
            if method == "POST" and path == "/api/projects":
                body = self._json_body()
                project_id = self.app.call(lambda conn: create_project(conn, body.get("name", "")))
                self._json(self.app.call(lambda conn: project_view(conn, project_id)))
                return
            if method == "POST" and path == "/api/sample":
                self._json(self.app.call(load_sample))
                return
            if method == "PUT" and path == "/api/settings":
                current = load_settings(self.app.db_path)
                save_settings(self.app.db_path, self._json_body(), current)
                self._json(self._settings_view())
                return
            if len(parts) == 2 and parts[0] == "api" and parts[1] == "projects" and method == "GET":
                self._error(404, "没有这个页面")
                return
            if len(parts) >= 3 and parts[:2] == ["api", "projects"]:
                self._project(method, int(parts[2]), parts[3:])
                return
            if len(parts) >= 3 and parts[:2] == ["api", "assets"]:
                self._asset(method, int(parts[2]), parts[3:])
                return
            if len(parts) >= 3 and parts[:2] == ["api", "shots"]:
                self._shot(method, int(parts[2]), parts[3:])
                return
            self._error(404, "没有这个页面")
        except DeskError as exc:
            self._error(400, str(exc))
        except json.JSONDecodeError:
            self._error(400, "请求不是 JSON")
        except Exception:
            traceback.print_exc()
            self._error(500, "服务出错了")

    def _project(self, method: str, project_id: int, rest: list[str]) -> None:
        if not rest and method == "PUT":
            body = self._json_body()
            self.app.call(lambda conn: update_project(conn, project_id, body))
            self._json(self.app.call(lambda conn: project_view(conn, project_id)))
            return
        if not rest and method == "DELETE":
            self.app.call(lambda conn: delete_project(conn, project_id, self.app.work_root))
            self._json({"ok": True})
            return
        if rest == ["approve"] and method == "POST":
            self.app.call(lambda conn: set_script_status(conn, project_id, "approved"))
            self._json(self.app.call(lambda conn: project_view(conn, project_id)))
            return
        if rest == ["reopen"] and method == "POST":
            self.app.call(lambda conn: set_script_status(conn, project_id, "draft"))
            self._json(self.app.call(lambda conn: project_view(conn, project_id)))
            return
        if rest == ["lock"] and method == "POST":
            locked = bool(self._json_body().get("locked"))
            self.app.call(lambda conn: set_assets_locked(conn, project_id, locked))
            self._json(self.app.call(lambda conn: project_view(conn, project_id)))
            return
        if rest == ["split"] and method == "POST":
            self._json(self.app.call(lambda conn: split_script(conn, project_id)))
            return
        if rest == ["assets"] and method == "POST":
            body = self._json_body()
            self.app.call(lambda conn: add_asset(conn, project_id, body.get("kind", "character"), body.get("name", ""), body.get("note", "")))
            self._json(self.app.call(lambda conn: project_view(conn, project_id)))
            return
        if rest == ["shots"] and method == "POST":
            body = self._json_body()
            self.app.call(lambda conn: add_shot(conn, project_id, body))
            self._json(self.app.call(lambda conn: project_view(conn, project_id)))
            return
        if rest == ["generate"] and method == "POST":
            started, skipped = self.app.call(lambda conn: _queue_open_shots(conn, project_id))
            self.app.start_batch(started)
            view = self.app.call(lambda conn: project_view(conn, project_id))
            view["started"] = started
            view["skipped"] = skipped
            self._json(view)
            return
        if rest == ["export"] and method == "POST":
            settings = load_settings(self.app.db_path)
            self._json(self.app.call(lambda conn: export_project(conn, self.app.work_root, project_id, settings)))
            return
        if rest == ["reveal"] and method == "POST":
            self._reveal(project_id)
            return
        if rest == ["bgm"] and method == "POST":
            filename, content = self._upload()
            self._json(self.app.call(lambda conn: store_bgm(conn, self.app.work_root, project_id, filename, content)))
            return
        if rest == ["bgm"] and method == "DELETE":
            self._json(self.app.call(lambda conn: clear_bgm(conn, self.app.work_root, project_id)))
            return
        self._error(404, "没有这个页面")

    def _asset(self, method: str, asset_id: int, rest: list[str]) -> None:
        if not rest and method == "PUT":
            body = self._json_body()

            def run(conn):
                update_asset(conn, asset_id, body)
                row = conn.execute("SELECT project_id FROM assets WHERE id = ?", (asset_id,)).fetchone()
                return project_view(conn, row["project_id"])

            self._json(self.app.call(run))
            return
        if not rest and method == "DELETE":
            def run(conn):
                row = conn.execute("SELECT project_id FROM assets WHERE id = ?", (asset_id,)).fetchone()
                if row is None:
                    raise DeskError("没有这个资产")
                project_id = row["project_id"]
                delete_asset(conn, asset_id)
                return project_view(conn, project_id)

            self._json(self.app.call(run))
            return
        if rest == ["image"] and method == "POST":
            _filename, content = self._upload()
            self._json(self.app.call(lambda conn: store_asset_image(conn, self.app.work_root, asset_id, content)))
            return
        self._error(404, "没有这个页面")

    def _shot(self, method: str, shot_id: int, rest: list[str]) -> None:
        if not rest and method == "PUT":
            body = self._json_body()

            def run(conn):
                update_shot(conn, shot_id, body)
                return project_view(conn, get_shot(conn, shot_id)["project_id"])

            self._json(self.app.call(run))
            return
        if not rest and method == "DELETE":
            def run(conn):
                project_id = get_shot(conn, shot_id)["project_id"]
                delete_shot(conn, shot_id)
                return project_view(conn, project_id)

            self._json(self.app.call(run))
            return
        if rest == ["move"] and method == "POST":
            direction = self._json_body().get("direction", "")
            if direction not in ("up", "down"):
                raise DeskError("只能上移或下移")

            def run(conn):
                move_shot(conn, shot_id, direction)
                return project_view(conn, get_shot(conn, shot_id)["project_id"])

            self._json(self.app.call(run))
            return
        if rest == ["generate"] and method == "POST":
            self.app.call(lambda conn: begin_generate(conn, shot_id))
            self.app.start_shot(shot_id)
            shot = self.app.call(lambda conn: get_shot(conn, shot_id))
            self._json(self.app.call(lambda conn: project_view(conn, shot["project_id"])))
            return
        if rest == ["import"] and method == "POST":
            filename, content = self._upload()

            def run(conn):
                shot = get_shot(conn, shot_id)
                upload = store_upload(self.app.work_root, shot["project_id"], filename, content)
                return import_shot(conn, self.app.work_root, shot_id, upload)

            self._json(self.app.call(run))
            return
        self._error(404, "没有这个页面")

    def _reveal(self, project_id: int) -> None:
        view = self.app.call(lambda conn: project_view(conn, project_id))
        export = view.get("export") or {}
        folder = export.get("package_dir") or ""
        if not folder:
            raise DeskError("还没有导出")
        path = (self.app.work_root / folder).resolve()
        exports = (self.app.work_root / "data" / "exports").resolve()
        if not path.is_relative_to(exports) or not path.is_dir():
            raise DeskError("导出目录不在工作台里")
        os.startfile(path)  # noqa: S606  只打开本机导出目录
        self._json({"ok": True})

    def _data_file(self, relative: str) -> None:
        path = (self.app.work_root / relative).resolve()
        data_root = (self.app.work_root / "data").resolve()
        allowed = path.is_relative_to(data_root / "media") or path.is_relative_to(data_root / "exports")
        if not allowed or not path.is_file():
            self._error(404, "没有这个文件")
            return
        self._file(path)

    def _upload(self) -> tuple[str, bytes]:
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 210 * 1024 * 1024:
            raise DeskError("文件为空或超过 200MB")
        body = self.rfile.read(length)
        if "multipart/form-data" not in content_type:
            raise DeskError("需要上传文件")
        message = BytesParser(policy=default).parsebytes(
            b"MIME-Version: 1.0\r\nContent-Type: " + content_type.encode("utf-8") + b"\r\n\r\n" + body
        )
        for part in message.iter_parts():
            filename = part.get_filename()
            if not filename:
                continue
            payload = part.get_payload(decode=True)
            if payload:
                return filename, payload
        raise DeskError("没有找到文件")

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 2 * 1024 * 1024:
            raise DeskError("请求太大")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise DeskError("请求不是 JSON 对象")
        return data

    def _settings_view(self) -> dict:
        shown = public_settings(load_settings(self.app.db_path))
        shown["jianying_suggested"] = suggest_jianying_dir()
        return shown

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self._error(404, "没有这个文件")
            return
        data = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix in {".html", ".css", ".js"}:
            mime = {"html": "text/html", "css": "text/css", "js": "text/javascript"}[path.suffix[1:]]
            mime += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def _parts(path: str) -> list[str]:
    return [part for part in path.split("/") if part]


def _queue_open_shots(conn, project_id: int) -> tuple[list[int], list[dict]]:
    view = project_view(conn, project_id)
    started = []
    skipped = []
    for shot in view["shots"]:
        number = shot["position"] + 1
        if shot["status"] in ("queued", "running", "ready"):
            continue
        if shot["source"] == "import":
            skipped.append({"id": shot["id"], "shot": number, "reason": "需要导入视频"})
            continue
        try:
            begin_generate(conn, shot["id"])
            started.append(shot["id"])
        except DeskError as exc:
            skipped.append({"id": shot["id"], "shot": number, "reason": str(exc)})
    return started, skipped


def make_server(db_path: Path, host: str, port: int, work_root: Path | None = None) -> ThreadingHTTPServer:
    app = App(db_path, work_root or ROOT)
    handler = type("BoundHandler", (Handler,), {"app": app, "static_dir": STATIC})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server
