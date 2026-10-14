"""SQLite 里的项目、资产、镜头和导出记录。"""

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from shotdesk.errors import DeskError

SOURCES = ("builtin", "external", "import")
ASSET_KINDS = ("character", "location", "prop")


def now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            logline TEXT NOT NULL DEFAULT '',
            script TEXT NOT NULL DEFAULT '',
            aspect TEXT NOT NULL DEFAULT '9:16',
            script_status TEXT NOT NULL DEFAULT 'draft',
            assets_locked INTEGER NOT NULL DEFAULT 0,
            bgm_path TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            image_path TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shots (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            visual_prompt TEXT NOT NULL DEFAULT '',
            dialogue TEXT NOT NULL DEFAULT '',
            camera TEXT NOT NULL DEFAULT '',
            cast TEXT NOT NULL DEFAULT '',
            duration_ms INTEGER NOT NULL DEFAULT 5000,
            source TEXT NOT NULL DEFAULT 'builtin',
            status TEXT NOT NULL DEFAULT 'empty',
            file_path TEXT NOT NULL DEFAULT '',
            voice_path TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            voice_note TEXT NOT NULL DEFAULT '',
            cost_cents INTEGER NOT NULL DEFAULT 0,
            cost_note TEXT NOT NULL DEFAULT '',
            provider_task_id TEXT NOT NULL DEFAULT '',
            generated_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS exports (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            preview_path TEXT NOT NULL DEFAULT '',
            srt_path TEXT NOT NULL DEFAULT '',
            package_dir TEXT NOT NULL DEFAULT '',
            jianying_dir TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        """
    )


def row_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def create_project(conn: sqlite3.Connection, name: str, logline: str = "", script: str = "") -> int:
    stamp = now()
    title = (name or "").strip() or "未命名项目"
    cur = conn.execute(
        """
        INSERT INTO projects (name, logline, script, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (title, logline.strip(), script.strip(), stamp, stamp),
    )
    return int(cur.lastrowid)


def list_projects(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT p.*,
               (SELECT COUNT(*) FROM shots s WHERE s.project_id = p.id) AS shot_count,
               (SELECT COUNT(*) FROM shots s WHERE s.project_id = p.id AND s.status = 'ready') AS ready_count
        FROM projects p
        ORDER BY p.updated_at DESC, p.id DESC
        """
    ).fetchall()
    return [row_dict(row) for row in rows]


def get_project_row(conn: sqlite3.Connection, project_id: int) -> dict:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise DeskError("没有这个项目")
    return row_dict(row)


def touch(conn: sqlite3.Connection, project_id: int) -> None:
    conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now(), project_id))


def _unlock_assets(conn: sqlite3.Connection, project_id: int) -> None:
    conn.execute(
        "UPDATE projects SET assets_locked = 0, updated_at = ? WHERE id = ?",
        (now(), project_id),
    )


def update_project(conn: sqlite3.Connection, project_id: int, fields: dict) -> None:
    get_project_row(conn, project_id)
    allowed = {"name", "logline", "script", "aspect"}
    sets = []
    values = []
    for key in allowed:
        if key in fields:
            sets.append(f"{key} = ?")
            values.append(str(fields[key] or "").strip())
    if not sets:
        return
    if "name" in fields and not str(fields["name"]).strip():
        raise DeskError("项目名不能为空")
    if "script" in fields:
        sets.append("script_status = 'draft'")
    sets.append("updated_at = ?")
    values.append(now())
    values.append(project_id)
    conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", values)


def set_script_status(conn: sqlite3.Connection, project_id: int, status: str) -> None:
    if status not in ("draft", "approved"):
        raise DeskError("剧本状态不对")
    get_project_row(conn, project_id)
    conn.execute(
        "UPDATE projects SET script_status = ?, updated_at = ? WHERE id = ?",
        (status, now(), project_id),
    )


def set_assets_locked(conn: sqlite3.Connection, project_id: int, locked: bool) -> None:
    get_project_row(conn, project_id)
    conn.execute(
        "UPDATE projects SET assets_locked = ?, updated_at = ? WHERE id = ?",
        (1 if locked else 0, now(), project_id),
    )


def set_bgm(conn: sqlite3.Connection, project_id: int, relative: str) -> None:
    get_project_row(conn, project_id)
    conn.execute(
        "UPDATE projects SET bgm_path = ?, updated_at = ? WHERE id = ?",
        (relative, now(), project_id),
    )


def delete_project(conn: sqlite3.Connection, project_id: int, root: Path) -> None:
    get_project_row(conn, project_id)
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    for folder in (root / "data" / "media" / str(project_id), root / "data" / "exports" / str(project_id)):
        if folder.exists():
            shutil.rmtree(folder)


def add_asset(conn: sqlite3.Connection, project_id: int, kind: str, name: str, note: str = "") -> int:
    get_project_row(conn, project_id)
    if kind not in ASSET_KINDS:
        raise DeskError("资产类型只能是角色、场景或道具")
    title = name.strip()
    if not title:
        raise DeskError("资产要有名字")
    cur = conn.execute(
        """
        INSERT INTO assets (project_id, kind, name, note, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (project_id, kind, title, note.strip(), now()),
    )
    _unlock_assets(conn, project_id)
    return int(cur.lastrowid)


def update_asset(conn: sqlite3.Connection, asset_id: int, fields: dict) -> None:
    row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if row is None:
        raise DeskError("没有这个资产")
    kind = fields.get("kind", row["kind"])
    name = str(fields.get("name", row["name"])).strip()
    note = str(fields.get("note", row["note"])).strip()
    if kind not in ASSET_KINDS:
        raise DeskError("资产类型只能是角色、场景或道具")
    if not name:
        raise DeskError("资产要有名字")
    conn.execute(
        "UPDATE assets SET kind = ?, name = ?, note = ? WHERE id = ?",
        (kind, name, note, asset_id),
    )
    _unlock_assets(conn, row["project_id"])


def set_asset_image(conn: sqlite3.Connection, asset_id: int, relative: str) -> None:
    row = conn.execute("SELECT project_id FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if row is None:
        raise DeskError("没有这个资产")
    conn.execute("UPDATE assets SET image_path = ? WHERE id = ?", (relative, asset_id))
    _unlock_assets(conn, row["project_id"])


def delete_asset(conn: sqlite3.Connection, asset_id: int) -> None:
    row = conn.execute("SELECT project_id FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if row is None:
        raise DeskError("没有这个资产")
    conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    _unlock_assets(conn, row["project_id"])


def list_assets(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM assets WHERE project_id = ? ORDER BY id",
        (project_id,),
    ).fetchall()
    return [row_dict(row) for row in rows]


def add_shot(conn: sqlite3.Connection, project_id: int, fields: dict | None = None) -> int:
    get_project_row(conn, project_id)
    fields = fields or {}
    position = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM shots WHERE project_id = ?",
        (project_id,),
    ).fetchone()["next_pos"]
    source = fields.get("source") or "builtin"
    if source not in SOURCES:
        raise DeskError("画面来源只能是自带、外部或导入")
    stamp = now()
    cur = conn.execute(
        """
        INSERT INTO shots (
            project_id, position, title, visual_prompt, dialogue, camera, cast,
            duration_ms, source, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            position,
            str(fields.get("title") or "").strip(),
            str(fields.get("visual_prompt") or "").strip(),
            str(fields.get("dialogue") or "").strip(),
            str(fields.get("camera") or "").strip(),
            str(fields.get("cast") or "").strip(),
            int(fields.get("duration_ms") or 5000),
            source,
            stamp,
        ),
    )
    touch(conn, project_id)
    return int(cur.lastrowid)


def replace_shots(conn: sqlite3.Connection, project_id: int, shots: list[dict]) -> None:
    get_project_row(conn, project_id)
    conn.execute("DELETE FROM shots WHERE project_id = ?", (project_id,))
    for index, fields in enumerate(shots):
        fields = dict(fields)
        source = fields.get("source") or "builtin"
        if source not in SOURCES:
            raise DeskError("画面来源只能是自带、外部或导入")
        conn.execute(
            """
            INSERT INTO shots (
                project_id, position, title, visual_prompt, dialogue, camera, cast,
                duration_ms, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                index,
                fields.get("title") or "",
                fields.get("visual_prompt") or "",
                fields.get("dialogue") or "",
                fields.get("camera") or "",
                fields.get("cast") or "",
                int(fields.get("duration_ms") or 5000),
                source,
                now(),
            ),
        )
    touch(conn, project_id)


def get_shot(conn: sqlite3.Connection, shot_id: int) -> dict:
    row = conn.execute("SELECT * FROM shots WHERE id = ?", (shot_id,)).fetchone()
    if row is None:
        raise DeskError("没有这个镜头")
    return row_dict(row)


def list_shots(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM shots WHERE project_id = ? ORDER BY position, id",
        (project_id,),
    ).fetchall()
    return [row_dict(row) for row in rows]


def update_shot(conn: sqlite3.Connection, shot_id: int, fields: dict) -> None:
    shot = get_shot(conn, shot_id)
    source = fields.get("source", shot["source"])
    if source not in SOURCES:
        raise DeskError("画面来源只能是自带、外部或导入")
    duration = int(fields.get("duration_ms", shot["duration_ms"]))
    if duration < 1000 or duration > 180000:
        raise DeskError("镜头时长要在 1 秒到 180 秒之间")
    conn.execute(
        """
        UPDATE shots
        SET title = ?, visual_prompt = ?, dialogue = ?, camera = ?, cast = ?,
            duration_ms = ?, source = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            str(fields.get("title", shot["title"])).strip(),
            str(fields.get("visual_prompt", shot["visual_prompt"])).strip(),
            str(fields.get("dialogue", shot["dialogue"])).strip(),
            str(fields.get("camera", shot["camera"])).strip(),
            str(fields.get("cast", shot["cast"])).strip(),
            duration,
            source,
            now(),
            shot_id,
        ),
    )
    touch(conn, shot["project_id"])


def move_shot(conn: sqlite3.Connection, shot_id: int, direction: str) -> None:
    shot = get_shot(conn, shot_id)
    shots = list_shots(conn, shot["project_id"])
    index = next(i for i, item in enumerate(shots) if item["id"] == shot_id)
    swap = index - 1 if direction == "up" else index + 1
    if swap < 0 or swap >= len(shots):
        return
    other = shots[swap]
    conn.execute("UPDATE shots SET position = ? WHERE id = ?", (other["position"], shot["id"]))
    conn.execute("UPDATE shots SET position = ? WHERE id = ?", (shot["position"], other["id"]))
    touch(conn, shot["project_id"])


def delete_shot(conn: sqlite3.Connection, shot_id: int) -> None:
    shot = get_shot(conn, shot_id)
    conn.execute("DELETE FROM shots WHERE id = ?", (shot_id,))
    shots = list_shots(conn, shot["project_id"])
    for index, item in enumerate(shots):
        conn.execute("UPDATE shots SET position = ? WHERE id = ?", (index, item["id"]))
    touch(conn, shot["project_id"])


def mark_shot(conn: sqlite3.Connection, shot_id: int, status: str, **fields) -> None:
    shot = get_shot(conn, shot_id)
    allowed = {
        "file_path",
        "voice_path",
        "error",
        "voice_note",
        "cost_cents",
        "cost_note",
        "provider_task_id",
        "generated_at",
        "duration_ms",
    }
    # 刚生成完时，更新时间和成片时间用同一个戳，避免界面误报「文案已经改过」。
    stamp = fields.get("generated_at") or now()
    sets = ["status = ?", "updated_at = ?"]
    values: list = [status, stamp]
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        values.append(value)
    values.append(shot_id)
    conn.execute(f"UPDATE shots SET {', '.join(sets)} WHERE id = ?", values)
    touch(conn, shot["project_id"])


def claim_shot(conn: sqlite3.Connection, shot_id: int) -> dict:
    """把空闲镜头标成排队。已经在跑就拒绝，避免重复出片。"""
    shot = get_shot(conn, shot_id)
    if shot["status"] in ("queued", "running"):
        raise DeskError("这个镜头正在生成")
    mark_shot(conn, shot_id, "queued", error="", provider_task_id="")
    return get_shot(conn, shot_id)


def add_export(conn: sqlite3.Connection, project_id: int, fields: dict) -> None:
    conn.execute(
        """
        INSERT INTO exports (project_id, preview_path, srt_path, package_dir, jianying_dir, message, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            fields.get("preview_path") or "",
            fields.get("srt_path") or "",
            fields.get("package_dir") or "",
            fields.get("jianying_dir") or "",
            fields.get("message") or "",
            now(),
        ),
    )
    touch(conn, project_id)


def latest_export(conn: sqlite3.Connection, project_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM exports WHERE project_id = ? ORDER BY id DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    return row_dict(row)


def project_view(conn: sqlite3.Connection, project_id: int) -> dict:
    project = get_project_row(conn, project_id)
    shots = list_shots(conn, project_id)
    ready = sum(1 for shot in shots if shot["status"] == "ready" and shot["file_path"])
    return {
        "project": project,
        "assets": list_assets(conn, project_id),
        "shots": shots,
        "export": latest_export(conn, project_id),
        "gates": {
            "script_approved": project["script_status"] == "approved",
            "assets_locked": bool(project["assets_locked"]),
            "ready": ready,
            "total": len(shots),
            "cost_cents": sum(shot["cost_cents"] or 0 for shot in shots),
        },
    }
