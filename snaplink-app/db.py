"""SQLite storage for groups, outings, and photos. Local file, no server process."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

from cluster import split_note
from domain import USER_ACTIVITY_IDS, activity_label, normalize_activities, normalize_tags, sort_activity_ids

ROOT = Path(__file__).resolve().parent
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
  id TEXT PRIMARY KEY,
  group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  position INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS outings (
  id TEXT PRIMARY KEY,
  group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  status TEXT NOT NULL,
  start_time TEXT,
  end_time TEXT,
  area_name TEXT NOT NULL DEFAULT '',
  area_lat REAL,
  area_lng REAL,
  area_precision TEXT NOT NULL DEFAULT 'unknown',
  area_source TEXT NOT NULL DEFAULT 'none',
  tags TEXT NOT NULL DEFAULT '[]',
  feedback TEXT NOT NULL DEFAULT '',
  rating INTEGER,
  liked INTEGER,
  inference_source TEXT NOT NULL DEFAULT 'none',
  inference_note TEXT NOT NULL DEFAULT '',
  split_reason TEXT NOT NULL DEFAULT 'first',
  import_batch TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS places (
  id TEXT PRIMARY KEY,
  outing_id TEXT NOT NULL REFERENCES outings(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  lat REAL,
  lng REAL,
  precision TEXT NOT NULL,
  position INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS activities (
  id TEXT PRIMARY KEY,
  outing_id TEXT NOT NULL REFERENCES outings(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  confidence REAL NOT NULL,
  source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS photos (
  id TEXT PRIMARY KEY,
  group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  outing_id TEXT REFERENCES outings(id) ON DELETE CASCADE,
  filename TEXT NOT NULL,
  stored_name TEXT NOT NULL,
  taken_at TEXT,
  lat REAL,
  lng REAL,
  content_hash TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS geocode_cache (
  key TEXT PRIMARY KEY,
  area_name TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outings_group ON outings(group_id);
CREATE INDEX IF NOT EXISTS idx_photos_outing ON photos(outing_id);
"""


class RequestError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def data_paths() -> tuple[Path, Path, Path]:
    root = Path(os.environ.get("MEMORY_DATA_DIR", ROOT / "data"))
    return root, root / "memory.db", root / "uploads"


def uploads_dir() -> Path:
    init_db()
    return data_paths()[2]


def init_db() -> None:
    global _conn
    if _conn is not None:
        return
    with _lock:
        if _conn is not None:
            return
        root, db_path, uploads = data_paths()
        root.mkdir(parents=True, exist_ok=True)
        uploads.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
        _conn = conn


def _migrate(conn: sqlite3.Connection) -> None:
    outing_cols = {row[1] for row in conn.execute("PRAGMA table_info(outings)")}
    if "import_batch" not in outing_cols:
        conn.execute("ALTER TABLE outings ADD COLUMN import_batch TEXT")
    if "liked" not in outing_cols:
        conn.execute("ALTER TABLE outings ADD COLUMN liked INTEGER")
    photo_cols = {row[1] for row in conn.execute("PRAGMA table_info(photos)")}
    if "content_hash" not in photo_cols:
        conn.execute("ALTER TABLE photos ADD COLUMN content_hash TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_group_hash ON photos(group_id, content_hash)")
    known = conn.execute(
        """SELECT DISTINCT outing_id FROM activities
           WHERE type IN ({})""".format(",".join("?" for _ in USER_ACTIVITY_IDS)),
        tuple(USER_ACTIVITY_IDS),
    ).fetchall()
    for row in known:
        conn.execute(
            "UPDATE outings SET status = 'confirmed' WHERE id = ? AND status = 'draft'",
            (row["outing_id"],),
        )
    uploads = data_paths()[2]
    rows = conn.execute(
        "SELECT id, stored_name FROM photos WHERE content_hash IS NULL OR content_hash = ''"
    ).fetchall()
    for row in rows:
        path = uploads / row["stored_name"]
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        conn.execute("UPDATE photos SET content_hash = ? WHERE id = ?", (digest, row["id"]))


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_id() -> str:
    return str(uuid.uuid4())


def _call(fn):
    init_db()
    with _lock:
        assert _conn is not None
        try:
            result = fn(_conn)
            _conn.commit()
            return result
        except Exception:
            _conn.rollback()
            raise


def _place_names(value) -> list[str]:
    parts = value.split(",") if isinstance(value, str) else (value or [])
    names: list[str] = []
    for part in parts:
        name = str(part).strip()[:40]
        if name and name not in names:
            names.append(name)
        if len(names) >= 8:
            break
    return names


def _outing_public(row: sqlite3.Row, activities, places, photos) -> dict:
    by_type = {item["type"]: item for item in activities}
    ordered = sort_activity_ids([item["type"] for item in activities if item["type"] != "unknown"])
    if not ordered and "unknown" in by_type:
        ordered = ["unknown"]
    try:
        tags = json.loads(row["tags"] or "[]")
    except json.JSONDecodeError:
        tags = []
    return {
        "id": row["id"],
        "status": row["status"],
        "startTime": row["start_time"],
        "endTime": row["end_time"],
        "areaName": row["area_name"] or "",
        "areaLat": row["area_lat"],
        "areaLng": row["area_lng"],
        "areaPrecision": row["area_precision"],
        "areaSource": row["area_source"],
        "tags": tags if isinstance(tags, list) else [],
        "feedback": row["feedback"] or "",
        "rating": row["rating"],
        "liked": _outing_liked(row),
        "inferenceSource": row["inference_source"],
        "inferenceNote": row["inference_note"] or "",
        "splitReason": row["split_reason"],
        "splitNote": split_note(row["split_reason"]),
        "importBatch": row["import_batch"] or "",
        "createdAt": row["created_at"],
        "activities": [
            {
                "type": activity_id,
                "label": "unknown" if activity_id == "unknown" else activity_label(activity_id),
                "confidence": by_type[activity_id]["confidence"],
                "source": by_type[activity_id]["source"],
            }
            for activity_id in ordered
            if activity_id in by_type
        ],
        "places": [
            {
                "id": place["id"],
                "name": place["name"],
                "lat": place["lat"],
                "lng": place["lng"],
                "precision": place["precision"],
            }
            for place in places
        ],
        "photos": [_photo_public(photo) for photo in photos],
    }


def _photo_public(row: sqlite3.Row) -> dict:
    suffix = Path(row["stored_name"]).suffix.lower().lstrip(".")
    return {
        "id": row["id"],
        "filename": row["filename"],
        "takenAt": row["taken_at"],
        "lat": row["lat"],
        "lng": row["lng"],
        "hasTime": row["taken_at"] is not None,
        "hasGps": row["lat"] is not None and row["lng"] is not None,
        "format": suffix,
        "url": f"/snaplink/api/photos/{row['id']}",
    }


def _get_group(conn: sqlite3.Connection, group_id: str) -> dict | None:
    group = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    if not group:
        return None
    members = conn.execute(
        "SELECT id, name FROM members WHERE group_id = ? ORDER BY position",
        (group_id,),
    ).fetchall()
    outings = conn.execute(
        "SELECT * FROM outings WHERE group_id = ? ORDER BY start_time IS NULL, start_time",
        (group_id,),
    ).fetchall()
    public_outings = []
    for outing in outings:
        activities = conn.execute(
            "SELECT type, confidence, source FROM activities WHERE outing_id = ?",
            (outing["id"],),
        ).fetchall()
        places = conn.execute(
            "SELECT * FROM places WHERE outing_id = ? ORDER BY position",
            (outing["id"],),
        ).fetchall()
        photos = conn.execute(
            """SELECT * FROM photos WHERE outing_id = ?
               ORDER BY taken_at IS NULL, taken_at, filename""",
            (outing["id"],),
        ).fetchall()
        public_outings.append(_outing_public(outing, activities, places, photos))
    return {
        "id": group["id"],
        "name": group["name"],
        "createdAt": group["created_at"],
        "members": [{"id": member["id"], "name": member["name"]} for member in members],
        "outings": public_outings,
    }


def _require_group(conn: sqlite3.Connection, group_id: str) -> dict:
    group = _get_group(conn, group_id)
    if group is None:
        raise RequestError("그룹을 찾지 못했습니다.", 404)
    return group


def list_groups() -> list[dict]:
    def run(conn):
        rows = conn.execute(
            """
            SELECT g.id, g.name,
              (SELECT COUNT(*) FROM members m WHERE m.group_id = g.id) AS member_count,
              (SELECT COUNT(*) FROM outings o WHERE o.group_id = g.id AND o.status = 'confirmed') AS confirmed_count,
              (SELECT COUNT(*) FROM outings o WHERE o.group_id = g.id AND o.status = 'draft') AS draft_count
            FROM groups g
            ORDER BY g.created_at DESC
            """
        ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "memberCount": row["member_count"],
                "confirmedCount": row["confirmed_count"],
                "draftCount": row["draft_count"],
            }
            for row in rows
        ]

    return _call(run)


def create_group(name: str, member_names: list[str]) -> dict:
    name = name.strip()
    if not name or len(name) > 40:
        raise RequestError("그룹 이름은 1자에서 40자 사이입니다.")
    if not member_names:
        raise RequestError("함께한 사람 이름을 적어 주세요.")

    def run(conn):
        group_id = _new_id()
        conn.execute(
            "INSERT INTO groups (id, name, created_at) VALUES (?, ?, ?)",
            (group_id, name, _now()),
        )
        for index, member_name in enumerate(member_names):
            conn.execute(
                "INSERT INTO members (id, group_id, name, position) VALUES (?, ?, ?, ?)",
                (_new_id(), group_id, member_name, index),
            )
        return _require_group(conn, group_id)

    return _call(run)


def get_group(group_id: str) -> dict:
    def run(conn):
        return _require_group(conn, group_id)

    return _call(run)


def photo_hashes(group_id: str) -> set[str]:
    def run(conn):
        rows = conn.execute(
            """SELECT content_hash FROM photos
               WHERE group_id = ? AND content_hash IS NOT NULL AND content_hash != ''""",
            (group_id,),
        ).fetchall()
        return {row["content_hash"] for row in rows}

    return _call(run)


def _outing_liked(row) -> bool:
    liked = row["liked"] if "liked" in row.keys() else None
    if liked is None:
        rating = row["rating"]
        return isinstance(rating, int) and rating >= 4
    return bool(liked)


def _known_activity(draft: dict) -> bool:
    return any(
        isinstance(activity, dict) and activity.get("type") in USER_ACTIVITY_IDS
        for activity in (draft.get("activities") or [])
    )


def save_import(group_id: str, drafts: list[dict], import_batch: str) -> dict:
    def run(conn):
        _require_group(conn, group_id)
        created = _now()
        for draft in drafts:
            conn.execute(
                """
                INSERT INTO outings (
                  id, group_id, status, start_time, end_time, area_name, area_lat, area_lng,
                  area_precision, area_source, tags, feedback, rating, inference_source,
                  inference_note, split_reason, import_batch, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', NULL, ?, ?, ?, ?, ?)
                """,
                (
                    draft["id"],
                    group_id,
                    "confirmed" if _known_activity(draft) else "draft",
                    draft.get("startTime"),
                    draft.get("endTime"),
                    draft.get("areaName") or "",
                    draft.get("areaLat"),
                    draft.get("areaLng"),
                    draft.get("areaPrecision") or "unknown",
                    draft.get("areaSource") or "none",
                    json.dumps(draft.get("tags") or [], ensure_ascii=False),
                    draft.get("inferenceSource") or "none",
                    draft.get("inferenceNote") or "",
                    draft.get("splitReason") or "first",
                    import_batch,
                    created,
                ),
            )
            for activity in draft.get("activities") or []:
                conn.execute(
                    "INSERT INTO activities (id, outing_id, type, confidence, source) VALUES (?, ?, ?, ?, ?)",
                    (_new_id(), draft["id"], activity["type"], activity["confidence"], activity["source"]),
                )
            for index, place in enumerate(draft.get("places") or []):
                conn.execute(
                    """INSERT INTO places (id, outing_id, name, lat, lng, precision, position)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _new_id(),
                        draft["id"],
                        place["name"],
                        place.get("lat"),
                        place.get("lng"),
                        place.get("precision") or "hint",
                        index,
                    ),
                )
            for photo in draft.get("photos") or []:
                conn.execute(
                    """INSERT INTO photos (
                         id, group_id, outing_id, filename, stored_name, taken_at, lat, lng,
                         content_hash, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        photo["id"],
                        group_id,
                        draft["id"],
                        photo["filename"],
                        photo["storedName"],
                        photo.get("takenAt"),
                        photo.get("lat"),
                        photo.get("lng"),
                        photo.get("contentHash"),
                        created,
                    ),
                )
        return _require_group(conn, group_id)

    return _call(run)


def update_outing(outing_id: str, payload: dict) -> dict:
    def run(conn):
        row = conn.execute("SELECT * FROM outings WHERE id = ?", (outing_id,)).fetchone()
        if not row:
            raise RequestError("외출을 찾지 못했습니다.", 404)
        activities = None
        if "activities" in payload:
            activities = [
                item for item in normalize_activities(payload["activities"])
                if item["type"] in USER_ACTIVITY_IDS
            ]
        _apply_outing_fields(conn, row, payload, activities)
        return _require_group(conn, row["group_id"])

    return _call(run)


def save_outings(group_id: str, payloads: list[dict]) -> dict:
    """Update draft fields and confirm them in one transaction."""

    def run(conn):
        _require_group(conn, group_id)
        if not payloads:
            raise RequestError("저장할 경험이 없습니다.")
        for payload in payloads:
            if not isinstance(payload, dict):
                raise RequestError("저장할 경험 형식이 아닙니다.")
            outing_id = str(payload.get("id") or "")
            row = conn.execute("SELECT * FROM outings WHERE id = ?", (outing_id,)).fetchone()
            if not row or row["group_id"] != group_id:
                raise RequestError("외출을 찾지 못했습니다.", 404)
            if row["status"] != "draft":
                raise RequestError("이미 저장된 경험입니다.")
            activities = [
                item for item in normalize_activities(payload.get("activities"))
                if item["type"] in USER_ACTIVITY_IDS
            ]
            if not activities:
                raise RequestError("활동을 확인해야 저장할 수 있습니다.")
            _apply_outing_fields(conn, row, payload, activities)
            conn.execute("UPDATE outings SET status = 'confirmed' WHERE id = ?", (outing_id,))
        return _require_group(conn, group_id)

    return _call(run)


def _apply_outing_fields(conn, row, payload: dict, activities: list[dict] | None = None) -> None:
    outing_id = row["id"]
    area_name = str(payload.get("areaName", row["area_name"]) or "").strip()[:40]
    area_source = row["area_source"]
    if area_name != (row["area_name"] or ""):
        area_source = "user" if area_name else row["area_source"]
    feedback = str(payload.get("feedback", row["feedback"]) or "")[:1000]
    rating = row["rating"]
    if "rating" in payload:
        rating = payload["rating"]
        if rating is not None and rating != "":
            rating = int(rating)
            if rating < 1 or rating > 5:
                raise RequestError("만족도는 1에서 5 사이입니다.")
        else:
            rating = None
    tags = normalize_tags(payload["tags"]) if "tags" in payload else json.loads(row["tags"] or "[]")
    conn.execute(
        """UPDATE outings
           SET area_name = ?, area_source = ?, feedback = ?, rating = ?, tags = ?
           WHERE id = ?""",
        (area_name, area_source, feedback, rating, json.dumps(tags, ensure_ascii=False), outing_id),
    )
    if activities is not None:
        for item in activities:
            item["source"] = "user"
            item["confidence"] = 1
        conn.execute("DELETE FROM activities WHERE outing_id = ?", (outing_id,))
        for item in activities:
            conn.execute(
                "INSERT INTO activities (id, outing_id, type, confidence, source) VALUES (?, ?, ?, ?, ?)",
                (_new_id(), outing_id, item["type"], item["confidence"], item["source"]),
            )
    if "places" in payload:
        conn.execute("DELETE FROM places WHERE outing_id = ?", (outing_id,))
        for index, name in enumerate(_place_names(payload["places"])):
            conn.execute(
                """INSERT INTO places (id, outing_id, name, lat, lng, precision, position)
                   VALUES (?, ?, ?, NULL, NULL, 'user', ?)""",
                (_new_id(), outing_id, name, index),
            )


def choose_activity(outing_id: str, activity_id: str) -> dict:
    if activity_id not in USER_ACTIVITY_IDS:
        raise RequestError("고를 수 있는 활동이 아닙니다.")

    def run(conn):
        row = conn.execute("SELECT * FROM outings WHERE id = ?", (outing_id,)).fetchone()
        if not row:
            raise RequestError("외출을 찾지 못했습니다.", 404)
        conn.execute("DELETE FROM activities WHERE outing_id = ?", (outing_id,))
        conn.execute(
            "INSERT INTO activities (id, outing_id, type, confidence, source) VALUES (?, ?, ?, 1, 'user')",
            (_new_id(), outing_id, activity_id),
        )
        conn.execute("UPDATE outings SET status = 'confirmed' WHERE id = ?", (outing_id,))
        return _require_group(conn, row["group_id"])

    return _call(run)


def set_liked(outing_id: str, liked: bool) -> dict:
    def run(conn):
        row = conn.execute("SELECT group_id FROM outings WHERE id = ?", (outing_id,)).fetchone()
        if not row:
            raise RequestError("외출을 찾지 못했습니다.", 404)
        conn.execute("UPDATE outings SET liked = ? WHERE id = ?", (1 if liked else 0, outing_id))
        return _require_group(conn, row["group_id"])

    return _call(run)


def confirm_outing(outing_id: str) -> dict:
    def run(conn):
        row = conn.execute("SELECT group_id FROM outings WHERE id = ?", (outing_id,)).fetchone()
        if not row:
            raise RequestError("외출을 찾지 못했습니다.", 404)
        conn.execute("UPDATE outings SET status = 'confirmed' WHERE id = ?", (outing_id,))
        return _require_group(conn, row["group_id"])

    return _call(run)


def _recompute_span(conn: sqlite3.Connection, outing_id: str) -> None:
    photos = conn.execute(
        "SELECT taken_at, lat, lng FROM photos WHERE outing_id = ?",
        (outing_id,),
    ).fetchall()
    times = sorted(photo["taken_at"] for photo in photos if photo["taken_at"])
    points = [(photo["lat"], photo["lng"]) for photo in photos if photo["lat"] is not None and photo["lng"] is not None]
    lat = lng = None
    if points:
        lat = sum(point[0] for point in points) / len(points)
        lng = sum(point[1] for point in points) / len(points)
    conn.execute(
        "UPDATE outings SET start_time = ?, end_time = ?, area_lat = ?, area_lng = ? WHERE id = ?",
        (times[0] if times else None, times[-1] if times else None, lat, lng, outing_id),
    )


def merge_outings(target_id: str, source_id: str) -> dict:
    if target_id == source_id:
        raise RequestError("서로 다른 기록을 선택해 주세요.")

    def run(conn):
        target = conn.execute("SELECT * FROM outings WHERE id = ?", (target_id,)).fetchone()
        source = conn.execute("SELECT * FROM outings WHERE id = ?", (source_id,)).fetchone()
        if not target or not source:
            raise RequestError("외출을 찾지 못했습니다.", 404)
        if target["group_id"] != source["group_id"]:
            raise RequestError("같은 그룹의 기록만 합칠 수 있습니다.")
        conn.execute("UPDATE photos SET outing_id = ? WHERE outing_id = ?", (target_id, source_id))
        activities = conn.execute(
            "SELECT type, confidence, source FROM activities WHERE outing_id IN (?, ?)",
            (target_id, source_id),
        ).fetchall()
        merged: dict[str, sqlite3.Row] = {}
        for activity in activities:
            previous = merged.get(activity["type"])
            if previous is None or activity["source"] == "user" or (
                previous["source"] != "user" and activity["confidence"] > previous["confidence"]
            ):
                merged[activity["type"]] = activity
        conn.execute("DELETE FROM activities WHERE outing_id = ?", (target_id,))
        for activity in merged.values():
            conn.execute(
                "INSERT INTO activities (id, outing_id, type, confidence, source) VALUES (?, ?, ?, ?, ?)",
                (_new_id(), target_id, activity["type"], activity["confidence"], activity["source"]),
            )
        place_rows = conn.execute(
            "SELECT name FROM places WHERE outing_id IN (?, ?) ORDER BY position",
            (target_id, source_id),
        ).fetchall()
        names: list[str] = []
        for place in place_rows:
            if place["name"] not in names:
                names.append(place["name"])
        conn.execute("DELETE FROM places WHERE outing_id = ?", (target_id,))
        for index, name in enumerate(names):
            conn.execute(
                """INSERT INTO places (id, outing_id, name, lat, lng, precision, position)
                   VALUES (?, ?, ?, NULL, NULL, 'user', ?)""",
                (_new_id(), target_id, name, index),
            )
        area_name = target["area_name"] or ""
        area_source = target["area_source"]
        area_precision = target["area_precision"]
        if not area_name.strip() and (source["area_name"] or "").strip():
            area_name = source["area_name"]
            area_source = source["area_source"]
            area_precision = source["area_precision"]
        note = target["inference_note"] or ""
        if "합쳤습니다" not in note:
            extra = "두 묶음을 한 외출로 합쳤습니다. 활동과 지역을 다시 확인해 주세요."
            note = f"{note} {extra}".strip()
        status = "confirmed" if "confirmed" in (target["status"], source["status"]) else "draft"
        try:
            target_tags = json.loads(target["tags"] or "[]")
            source_tags = json.loads(source["tags"] or "[]")
        except json.JSONDecodeError:
            target_tags, source_tags = [], []
        tags = normalize_tags(list(target_tags) + list(source_tags))
        feedback = target["feedback"] or source["feedback"] or ""
        rating = target["rating"] if target["rating"] is not None else source["rating"]
        conn.execute(
            """UPDATE outings
               SET status = ?, area_name = ?, area_source = ?, area_precision = ?,
                   tags = ?, feedback = ?, rating = ?, inference_note = ?
               WHERE id = ?""",
            (
                status,
                area_name,
                area_source,
                area_precision,
                json.dumps(tags, ensure_ascii=False),
                feedback,
                rating,
                note[:300],
                target_id,
            ),
        )
        conn.execute("DELETE FROM outings WHERE id = ?", (source_id,))
        _recompute_span(conn, target_id)
        return _require_group(conn, target["group_id"])

    return _call(run)


def delete_outing(outing_id: str) -> tuple[dict, list[str]]:
    def run(conn):
        row = conn.execute("SELECT group_id FROM outings WHERE id = ?", (outing_id,)).fetchone()
        if not row:
            raise RequestError("외출을 찾지 못했습니다.", 404)
        names = [
            item["stored_name"]
            for item in conn.execute(
                "SELECT stored_name FROM photos WHERE outing_id = ?",
                (outing_id,),
            ).fetchall()
        ]
        conn.execute("DELETE FROM outings WHERE id = ?", (outing_id,))
        return _require_group(conn, row["group_id"]), names

    return _call(run)


def get_photo(photo_id: str) -> dict | None:
    init_db()
    with _lock:
        assert _conn is not None
        row = _conn.execute("SELECT stored_name, filename FROM photos WHERE id = ?", (photo_id,)).fetchone()
        _conn.commit()
        if not row:
            return None
        return {"storedName": row["stored_name"], "filename": row["filename"]}


def get_geocode(key: str) -> str | None:
    init_db()
    with _lock:
        assert _conn is not None
        row = _conn.execute("SELECT area_name FROM geocode_cache WHERE key = ?", (key,)).fetchone()
        _conn.commit()
        if not row:
            return None
        return row["area_name"]


def set_geocode(key: str, area_name: str) -> None:
    def run(conn):
        conn.execute(
            """
            INSERT INTO geocode_cache (key, area_name, fetched_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET area_name = excluded.area_name, fetched_at = excluded.fetched_at
            """,
            (key, area_name, _now()),
        )

    _call(run)
