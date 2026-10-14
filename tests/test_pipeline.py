"""分镜成片台：关卡、三种画面来源、拼接和剪映草稿。"""

import json
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from shotdesk.db import connect, create_project, init_db, set_assets_locked, set_script_status
from shotdesk.media import ffmpeg
from shotdesk.pipeline import begin_generate, export_project, import_shot, produce_shot
from shotdesk.script import parse_script
from shotdesk.server import load_sample, make_server, prepare_db
from shotdesk.settings import DEFAULTS


ROOT = Path(__file__).resolve().parents[1]


def request(base, path, method="GET", body=None, content_type="application/json"):
    data = None if body is None else body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {}
    if body is not None and not isinstance(body, bytes):
        headers["Content-Type"] = content_type
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            raw = response.read()
            if response.headers.get_content_type() == "application/json":
                return response.status, json.loads(raw.decode("utf-8"))
            return response.status, raw
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8")
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            pass
        return exc.code, payload


class PathTest(unittest.TestCase):
    def test_suggest_jianying_dir(self):
        from shotdesk.jianying import suggest_jianying_dir

        tmp = tempfile.TemporaryDirectory()
        home = Path(tmp.name)
        draft = home / "AppData" / "Local" / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"
        draft.mkdir(parents=True)
        self.assertEqual(suggest_jianying_dir(home), str(draft))
        self.assertEqual(suggest_jianying_dir(home / "empty"), "")
        tmp.cleanup()

    def test_clear_bgm(self):
        from shotdesk.pipeline import clear_bgm, store_bgm

        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        db = root / "shotdesk.db"
        conn = connect(db)
        init_db(conn)
        project_id = create_project(conn, "音乐", "", "画面：巷口")
        view = store_bgm(conn, root, project_id, "bed.wav", b"RIFFxxxx")
        conn.commit()
        path = root / view["project"]["bgm_path"]
        self.assertTrue(path.is_file())
        view = clear_bgm(conn, root, project_id)
        conn.commit()
        conn.close()
        self.assertEqual(view["project"]["bgm_path"], "")
        self.assertFalse(path.exists())
        tmp.cleanup()


class ScriptTest(unittest.TestCase):
    def test_split_paragraphs(self):
        shots = parse_script("画面：巷口\n对白：伞还在。\n时长：2秒\n\n画面：撑开伞\n对白：先用我的。")
        self.assertEqual(len(shots), 2)
        self.assertEqual(shots[0]["duration_ms"], 2000)
        self.assertEqual(shots[0]["dialogue"], "伞还在。")
        self.assertEqual(shots[1]["visual_prompt"], "撑开伞")


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "shotdesk.db"
        self.conn = connect(self.db)
        init_db(self.conn)
        self.conn.commit()
        self.lock = threading.Lock()
        self.settings = dict(DEFAULTS)
        self.settings["tts_enabled"] = False
        self.settings["external_cents_per_second"] = 10

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def factory(self):
        def make():
            conn = connect(self.db)
            init_db(conn)
            return conn

        return make

    def test_gates_block_generation(self):
        self.conn.execute("INSERT INTO projects (name, script, script_status, assets_locked, created_at, updated_at) VALUES ('测', '画面：巷口', 'draft', 0, 't', 't')")
        project_id = self.conn.execute("SELECT id FROM projects").fetchone()["id"]
        self.conn.execute(
            "INSERT INTO shots (project_id, position, visual_prompt, duration_ms, source, updated_at) VALUES (?, 0, '巷口', 2000, 'builtin', 't')",
            (project_id,),
        )
        self.conn.commit()
        shot_id = self.conn.execute("SELECT id FROM shots").fetchone()["id"]
        with self.assertRaises(Exception) as raised:
            begin_generate(self.conn, shot_id)
        self.assertIn("剧本", str(raised.exception))

    def test_three_sources_compose_and_jianying(self):
        project_id = create_project(self.conn, "雨夜", "", "画面：巷口\n对白：伞还在。\n时长：2秒\n\n画面：撑伞\n对白：先用我的。\n时长：2秒\n\n画面：并肩\n对白：明天还。\n时长：2秒")
        from shotdesk.pipeline import split_script

        split_script(self.conn, project_id, ["builtin", "external", "import"])
        set_script_status(self.conn, project_id, "approved")
        set_assets_locked(self.conn, project_id, True)
        self.conn.commit()
        shots = self.conn.execute("SELECT id, source FROM shots ORDER BY position").fetchall()
        builtin_id = shots[0]["id"]
        external_id = shots[1]["id"]
        import_id = shots[2]["id"]

        produce_shot(self.factory(), self.root, builtin_id, self.settings, self.lock)
        produce_shot(self.factory(), self.root, external_id, self.settings, self.lock)
        builtin = self.conn.execute("SELECT * FROM shots WHERE id = ?", (builtin_id,)).fetchone()
        external = self.conn.execute("SELECT * FROM shots WHERE id = ?", (external_id,)).fetchone()
        self.assertEqual(builtin["status"], "ready", builtin["error"])
        self.assertEqual(builtin["updated_at"], builtin["generated_at"])
        self.assertEqual(external["status"], "ready", external["error"])
        self.assertEqual(external["cost_cents"], 20)
        self.assertNotEqual(builtin["file_path"], external["file_path"])

        upload = self.root / "incoming.mp4"
        subprocess_testsrc(upload)
        import_shot(self.conn, self.root, import_id, upload)
        self.conn.commit()
        imported = self.conn.execute("SELECT * FROM shots WHERE id = ?", (import_id,)).fetchone()
        self.assertEqual(imported["status"], "ready", imported["error"])
        self.assertEqual(imported["source"], "import")
        self.assertEqual(imported["cost_cents"], 0)

        first_file = builtin["file_path"]
        produce_shot(self.factory(), self.root, external_id, self.settings, self.lock)
        builtin_again = self.conn.execute("SELECT file_path FROM shots WHERE id = ?", (builtin_id,)).fetchone()
        self.assertEqual(builtin_again["file_path"], first_file)

        draft_root = self.root / "jianying"
        draft_root.mkdir()
        self.settings["jianying_draft_dir"] = str(draft_root)
        view = export_project(self.conn, self.root, project_id, self.settings)
        self.conn.commit()
        self.assertIn("剪映草稿已写到", view["message"])
        preview = self.root / view["export"]["preview_path"]
        self.assertGreater(preview.stat().st_size, 1000)
        from shotdesk.media import probe_duration_ms

        self.assertGreater(probe_duration_ms(preview), 4000)
        srt = (self.root / view["export"]["srt_path"]).read_text(encoding="utf-8")
        self.assertIn("伞还在", srt)
        drafts = list(draft_root.iterdir())
        self.assertTrue((drafts[0] / "draft_content.json").is_file())
        self.assertTrue((drafts[0] / "draft_meta_info.json").is_file())


class HttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "shotdesk.db"
        (Path(self.tmp.name) / "settings.json").write_text(
            json.dumps({"tts_enabled": False}, ensure_ascii=False),
            encoding="utf-8",
        )
        prepare_db(self.db)
        self.server = make_server(self.db, "127.0.0.1", 0, Path(self.tmp.name))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def test_sample_page_and_import(self):
        status, health = request(self.base, "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["ffmpeg"])
        status, page = request(self.base, "/")
        self.assertEqual(status, 200)
        self.assertIn("分镜成片台".encode("utf-8"), page)
        status, project = request(self.base, "/api/sample", "POST", {})
        self.assertEqual(status, 200, project)
        self.assertEqual([shot["source"] for shot in project["shots"]], ["builtin", "external", "import"])
        project_id = project["project"]["id"]
        status, blocked = request(self.base, f"/api/projects/{project_id}/generate", "POST", {})
        self.assertEqual(status, 200)
        self.assertEqual(blocked["started"], [])
        self.assertTrue(blocked["skipped"])
        status, project = request(self.base, f"/api/projects/{project_id}/approve", "POST", {})
        self.assertTrue(project["gates"]["script_approved"])
        status, project = request(self.base, f"/api/projects/{project_id}/lock", "POST", {"locked": True})
        self.assertTrue(project["gates"]["assets_locked"])
        builtin = project["shots"][0]["id"]
        status, project = request(self.base, f"/api/shots/{builtin}", "PUT", {"duration_ms": 2000, "source": "builtin", "visual_prompt": "巷口", "dialogue": "伞"})
        self.assertEqual(status, 200, project)
        status, project = request(self.base, f"/api/shots/{builtin}/generate", "POST", {})
        self.assertEqual(status, 200, project)
        self.assertIn(project["shots"][0]["status"], ("queued", "running"))
        ready = None
        for _ in range(80):
            status, project = request(self.base, f"/api/projects/{project_id}")
            shot = project["shots"][0]
            if shot["status"] in ("ready", "failed"):
                ready = shot
                break
            time.sleep(0.5)
        self.assertIsNotNone(ready)
        self.assertEqual(ready["status"], "ready", ready["error"])
        status, media = request(self.base, "/files/" + ready["file_path"])
        self.assertEqual(status, 200)
        self.assertGreater(len(media), 1000)
        self.assertTrue((Path(self.tmp.name) / ready["file_path"]).is_file())
        status, settings = request(self.base, "/api/settings")
        self.assertEqual(status, 200)
        self.assertIn("jianying_suggested", settings)
        self.assertEqual(settings["external_api_key"], "")


class ExternalHttpTest(unittest.TestCase):
    def test_submit_and_download(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        clip = root / "remote.mp4"
        subprocess_testsrc(clip)
        holder = {"clip": clip}

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def do_POST(self):
                body = json.dumps({"task_id": "job-1", "status": "ready", "url": f"http://127.0.0.1:{self.server.server_address[1]}/clip.mp4"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                data = holder["clip"].read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            db = root / "desk.db"
            conn = connect(db)
            init_db(conn)
            project_id = create_project(conn, "外部", "", "画面：海边\n对白：看。\n时长：2秒")
            from shotdesk.pipeline import split_script

            split_script(conn, project_id, ["external"])
            set_script_status(conn, project_id, "approved")
            set_assets_locked(conn, project_id, True)
            conn.commit()
            shot_id = conn.execute("SELECT id FROM shots").fetchone()["id"]
            conn.close()
            settings = dict(DEFAULTS)
            settings.update({
                "tts_enabled": False,
                "external_mode": "http",
                "external_base_url": f"http://127.0.0.1:{server.server_address[1]}",
                "external_cents_per_second": 8,
            })
            produce_shot(lambda: _open(db), root, shot_id, settings, threading.Lock())
            conn = connect(db)
            shot = conn.execute("SELECT * FROM shots").fetchone()
            conn.close()
            self.assertEqual(shot["status"], "ready", shot["error"])
            self.assertEqual(shot["provider_task_id"], "job-1")
            self.assertGreater(shot["cost_cents"], 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            tmp.cleanup()


def _open(db):
    conn = connect(db)
    init_db(conn)
    return conn


def subprocess_testsrc(path: Path) -> None:
    import subprocess

    proc = subprocess.run(
        [ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x568:rate=25", "-t", "2", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)


if __name__ == "__main__":
    unittest.main()
