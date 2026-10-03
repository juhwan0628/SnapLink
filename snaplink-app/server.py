"""Local Group Experience Memory server.

Run from this directory:
    py -3 server.py
Then open http://127.0.0.1:8765
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import db
from cluster import public_config
from domain import TAGS, activity_catalog, candidate_set_info, user_activity_catalog
from exif_parse import parse_image_exif, sniff_image
from geocode import nominatim_area
from candidates import explain_evidence, propose_patterns
from courses import assemble_recommendation, parse_search_location
from infer import infer_cluster, vision_enabled
from pipeline import build_drafts
from providers import event_provider, kakao_provider, places_enabled

STATIC_DIR = Path(__file__).resolve().parent / "static"
UUID = re.compile(r"^[0-9a-fA-F-]{36}$")
MAX_UPLOAD = 40 * 1024 * 1024
MAX_PHOTOS = 40
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
}
logger = logging.getLogger("memory.server")

PHOTO_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
}


def convert_heic(data: bytes) -> bytes:
    """Decode the primary HEIC image; keep EXIF for capture-time/GPS extraction."""
    from PIL import Image, ImageOps
    from pillow_heif import register_heif_opener

    register_heif_opener()
    with Image.open(io.BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image)
        exif = image.info.get("exif", b"")
        output = io.BytesIO()
        image.convert("RGB").save(output, format="JPEG", quality=90, exif=exif)
        return output.getvalue()


def parse_members(value) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[,，]", value)
    elif isinstance(value, list):
        parts = value
    else:
        parts = []
    names = []
    for part in parts:
        name = str(part).strip()
        if not name:
            continue
        if len(name) > 30:
            raise db.RequestError("이름은 30자 이하로 적어 주세요.")
        names.append(name)
        if len(names) > 12:
            raise db.RequestError("멤버는 12명까지입니다.")
    if not names:
        raise db.RequestError("함께한 사람 이름을 적어 주세요.")
    return names


def geocoding_enabled() -> bool:
    return os.environ.get("MEMORY_GEOCODE", "1") != "0"


def area_name_for(lat: float, lng: float) -> str | None:
    if not geocoding_enabled():
        return None
    key = f"{lat:.3f},{lng:.3f}"
    cached = db.get_geocode(key)
    if cached is not None:
        return cached or None
    name = nominatim_area(lat, lng)
    if name is None:
        return None
    db.set_geocode(key, name)
    return name or None


def meta_payload() -> dict:
    return {
        "activities": activity_catalog(),
        "userActivities": user_activity_catalog(),
        "tags": TAGS,
        "cluster": public_config(),
        "visionEnabled": vision_enabled(),
        "placesEnabled": places_enabled(),
        "candidateSet": candidate_set_info(),
    }


def history_from_group(group: dict) -> list[dict]:
    history = []
    for outing in group["outings"]:
        if outing["status"] != "confirmed":
            continue
        history.append({
            "startTime": outing["startTime"],
            "areaName": outing["areaName"],
            "areaLat": outing["areaLat"],
            "areaLng": outing["areaLng"],
            "activities": [
                activity["type"] for activity in outing["activities"] if activity["type"] != "unknown"
            ],
            "rating": outing["rating"],
            "liked": outing.get("liked"),
        })
    return history


def parse_multipart(body: bytes, content_type: str) -> list[dict]:
    match = re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))', content_type)
    if not match:
        raise db.RequestError("업로드 형식을 읽지 못했습니다.")
    boundary = (match.group(1) or match.group(2)).encode("utf-8", "replace")
    parts = []
    for chunk in body.split(b"--" + boundary):
        if not chunk or chunk.startswith(b"--"):
            continue
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        if chunk.endswith(b"\r\n"):
            chunk = chunk[:-2]
        header_blob, separator, content = chunk.partition(b"\r\n\r\n")
        if not separator:
            continue
        headers = {}
        for line in header_blob.decode("utf-8", "replace").split("\r\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.lower().strip()] = value.strip()
        disposition = headers.get("content-disposition", "")
        name_match = re.search(r'name="([^"]*)"', disposition)
        file_match = re.search(r'filename="([^"]*)"', disposition)
        parts.append({
            "name": name_match.group(1) if name_match else "",
            "filename": file_match.group(1) if file_match else None,
            "data": content,
        })
    return parts


class Handler(BaseHTTPRequestHandler):
    server_version = "ExperienceMemory/0.1"

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PATCH(self):
        self._route("PATCH")

    def do_DELETE(self):
        self._route("DELETE")

    def _route(self, method: str) -> None:
        parsed = urlparse(self.path)
        try:
            self._handle(method, parsed)
        except db.RequestError as exc:
            logger.warning("%s %s -> %s %s", method, parsed.path, exc.status, exc)
            self._json(exc.status, {"error": str(exc)})
        except Exception as exc:
            logger.exception("unhandled %s %s", method, parsed.path)
            self._json(500, {"error": f"처리하지 못했습니다. {type(exc).__name__}: {exc}"})

    def _handle(self, method: str, parsed) -> None:
        path = parsed.path
        if path == "/snaplink" or path.startswith("/snaplink/"):
            path = path[len("/snaplink"):] or "/"
        if method == "GET" and path in ("/", "/index.html"):
            self._file(STATIC_DIR / "index.html")
            return
        if method == "GET" and path.startswith("/static/"):
            relative = path[len("/static/") :]
            self._file(STATIC_DIR / relative)
            return
        if method == "GET" and path == "/api/meta":
            self._json(200, meta_payload())
            return
        if path == "/api/groups" and method == "GET":
            self._json(200, db.list_groups())
            return
        if path == "/api/groups" and method == "POST":
            payload = self._read_json()
            group = db.create_group(str(payload.get("name") or ""), parse_members(payload.get("members")))
            self._json(200, group)
            return

        group_match = re.fullmatch(r"/api/groups/([0-9a-fA-F-]{36})", path)
        if group_match and method == "GET":
            self._json(200, db.get_group(group_match.group(1)))
            return
        import_match = re.fullmatch(r"/api/groups/([0-9a-fA-F-]{36})/import", path)
        if import_match and method == "POST":
            self._json(200, self._import_photos(import_match.group(1)))
            return
        save_match = re.fullmatch(r"/api/groups/([0-9a-fA-F-]{36})/save-outings", path)
        if save_match and method == "POST":
            payload = self._read_json()
            outings = payload.get("outings")
            if not isinstance(outings, list):
                raise db.RequestError("저장할 경험 목록이 필요합니다.")
            self._json(200, db.save_outings(save_match.group(1), outings))
            return
        recommend_match = re.fullmatch(r"/api/groups/([0-9a-fA-F-]{36})/recommend", path)
        if recommend_match and method == "GET":
            self._json(200, self._recommend(recommend_match.group(1), parsed.query))
            return
        photo_match = re.fullmatch(r"/api/photos/([0-9a-fA-F-]{36})", path)
        if photo_match and method == "GET":
            self._photo(photo_match.group(1))
            return

        outing_match = re.fullmatch(r"/api/outings/([0-9a-fA-F-]{36})", path)
        if outing_match and method == "PATCH":
            self._json(200, db.update_outing(outing_match.group(1), self._read_json()))
            return
        if outing_match and method == "DELETE":
            group, names = db.delete_outing(outing_match.group(1))
            self._unlink(names)
            self._json(200, group)
            return
        choose_match = re.fullmatch(r"/api/outings/([0-9a-fA-F-]{36})/choose", path)
        if choose_match and method == "POST":
            payload = self._read_json()
            self._json(200, db.choose_activity(choose_match.group(1), str(payload.get("activity") or "")))
            return
        like_match = re.fullmatch(r"/api/outings/([0-9a-fA-F-]{36})/like", path)
        if like_match and method == "POST":
            payload = self._read_json()
            self._json(200, db.set_liked(like_match.group(1), bool(payload.get("liked"))))
            return
        confirm_match = re.fullmatch(r"/api/outings/([0-9a-fA-F-]{36})/confirm", path)
        if confirm_match and method == "POST":
            self._json(200, db.confirm_outing(confirm_match.group(1)))
            return
        merge_match = re.fullmatch(r"/api/outings/([0-9a-fA-F-]{36})/merge", path)
        if merge_match and method == "POST":
            payload = self._read_json()
            source_id = str(payload.get("sourceId") or "")
            if not UUID.fullmatch(source_id):
                raise db.RequestError("합칠 기록을 찾지 못했습니다.")
            self._json(200, db.merge_outings(merge_match.group(1), source_id))
            return
        self._json(404, {"error": "주소를 찾지 못했습니다."})

    def _recommend(self, group_id: str, query: str) -> dict:
        params = parse_qs(query)
        raw = params.get("novelty", ["0.55"])[0]
        try:
            novelty = float(raw)
        except ValueError:
            raise db.RequestError("새로움 비중이 숫자가 아닙니다.")
        when, duration_hours = parse_plan(params)
        try:
            location = parse_search_location({
                "locationMode": (params.get("locationMode") or [""])[0],
                "areaName": (params.get("areaName") or [""])[0],
                "latitude": (params.get("latitude") or [""])[0],
                "longitude": (params.get("longitude") or [""])[0],
                "searchRadiusKm": (params.get("searchRadiusKm") or [""])[0],
            })
        except ValueError as exc:
            raise db.RequestError(str(exc))
        group = db.get_group(group_id)
        member_count = len(group["members"])
        group_raw = (params.get("groupSize") or [""])[0].strip()
        if group_raw:
            try:
                member_count = int(group_raw)
            except ValueError:
                raise db.RequestError("인원(groupSize)은 숫자여야 합니다.")
            if member_count < 1 or member_count > 20:
                raise db.RequestError("인원은 1명 이상 20명 이하여야 합니다.")
        history = history_from_group(group)
        logger.info(
            "recommend group=%s date=%s start=%s duration=%s novelty=%s location=%s",
            group_id,
            when.date().isoformat(),
            when.strftime("%H:%M"),
            duration_hours,
            novelty,
            location["locationMode"] if location else "none",
        )
        pattern_fn = None
        explain_fn = None
        if os.environ.get("VLM_API_KEY"):
            def pattern_fn():
                return propose_patterns(
                    history,
                    when=when,
                    duration_hours=duration_hours,
                    member_count=member_count,
                    novelty_weight=novelty,
                )

            def explain_fn(evidence):
                return explain_evidence(evidence)
        return assemble_recommendation(
            history,
            novelty_weight=novelty,
            member_count=member_count,
            when=when,
            duration_hours=duration_hours,
            location=location,
            place_provider=kakao_provider(),
            event_provider=event_provider(),
            explain_fn=explain_fn,
            pattern_fn=pattern_fn,
        )

    def _import_photos(self, group_id: str) -> dict:
        db.get_group(group_id)
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            raise db.RequestError("사진을 선택해 주세요.")
        if length > MAX_UPLOAD:
            raise db.RequestError("한 번에 올릴 수 있는 용량은 40MB입니다.")
        body = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")
        files = [part for part in parse_multipart(body, content_type) if part.get("filename")]
        if not files:
            raise db.RequestError("사진을 선택해 주세요.")
        if len(files) > MAX_PHOTOS:
            raise db.RequestError(f"사진은 한 번에 {MAX_PHOTOS}장까지입니다.")

        written: list[Path] = []
        metas = []
        skipped = []
        duplicate_count = 0
        seen = set(db.photo_hashes(group_id))
        try:
            for part in files:
                filename = Path(part["filename"]).name or "photo"
                digest = hashlib.sha256(part["data"]).hexdigest()
                detected = sniff_image(part["data"])
                if not detected:
                    skipped.append({"filename": filename, "reason": "지원하지 않는 형식입니다."})
                    continue
                if digest in seen:
                    duplicate_count += 1
                    logger.info("import duplicate group=%s filename=%s", group_id, filename)
                    continue
                seen.add(digest)
                ext, _mime = detected
                image_data = part["data"]
                if ext == ".heic":
                    try:
                        image_data = convert_heic(image_data)
                    except Exception:
                        logger.exception("HEIC decode failed")
                        skipped.append({"filename": filename, "reason": "HEIC 사진을 읽지 못했습니다. JPEG로 변환해 다시 올려 주세요."})
                        continue
                    ext = ".jpg"
                photo_id = db._new_id()
                stored = photo_id + ext
                path = db.uploads_dir() / stored
                path.write_bytes(image_data)
                written.append(path)
                exif = parse_image_exif(image_data)
                metas.append({
                    "id": photo_id,
                    "filename": filename[:120],
                    "storedName": stored,
                    "takenAt": exif.get("taken_at"),
                    "lat": exif.get("lat"),
                    "lng": exif.get("lng"),
                    "contentHash": digest,
                })
            if not metas:
                if duplicate_count:
                    logger.info("import group=%s duplicates=%s skipped=%s", group_id, duplicate_count, skipped)
                    return {
                        "group": db.get_group(group_id),
                        "skipped": skipped,
                        "duplicateCount": duplicate_count,
                        "importedPhotos": 0,
                        "batchId": None,
                    }
                logger.warning("import group=%s rejected: no readable photos skipped=%s", group_id, skipped)
                raise db.RequestError("읽을 수 있는 사진이 없습니다. JPEG, PNG, WEBP, HEIC만 올릴 수 있습니다.")

            def loader(photo):
                file_path = db.uploads_dir() / photo["storedName"]
                if not file_path.is_file():
                    return None
                suffix = file_path.suffix.lower()
                return file_path.read_bytes(), PHOTO_MIME.get(suffix, "application/octet-stream")

            def infer(members):
                return infer_cluster(members, loader)

            drafts = build_drafts(metas, infer, area_name_for)
            batch_id = db._new_id()
            group = db.save_import(group_id, drafts, batch_id)
        except db.RequestError:
            for path in written:
                path.unlink(missing_ok=True)
            raise
        except Exception:
            logger.exception("import failed group=%s", group_id)
            for path in written:
                path.unlink(missing_ok=True)
            raise
        logger.info(
            "import ok group=%s photos=%s drafts=%s skipped=%s duplicates=%s batch=%s vision=%s",
            group_id,
            len(metas),
            sum(1 for outing in group["outings"] if outing["status"] == "draft"),
            skipped,
            duplicate_count,
            batch_id,
            vision_enabled(),
        )
        for outing in group["outings"]:
            if outing["status"] != "draft":
                continue
            logger.info(
                "draft outing=%s source=%s activities=%s note=%s",
                outing["id"],
                outing["inferenceSource"],
                [activity["type"] for activity in outing["activities"]],
                outing["inferenceNote"],
            )
        return {
            "group": group,
            "skipped": skipped,
            "duplicateCount": duplicate_count,
            "importedPhotos": len(metas),
            "batchId": batch_id,
        }

    def _photo(self, photo_id: str) -> None:
        photo = db.get_photo(photo_id)
        if not photo:
            self._json(404, {"error": "사진을 찾지 못했습니다."})
            return
        stored = Path(photo["storedName"]).name
        path = db.uploads_dir() / stored
        if not path.is_file():
            self._json(404, {"error": "사진 파일이 없습니다."})
            return
        data = path.read_bytes()
        self._bytes(200, data, PHOTO_MIME.get(path.suffix.lower(), "application/octet-stream"))

    def _unlink(self, stored_names: list[str]) -> None:
        uploads = db.uploads_dir()
        for name in stored_names:
            target = uploads / Path(name).name
            target.unlink(missing_ok=True)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 1_000_000:
            raise db.RequestError("본문이 너무 큽니다.")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise db.RequestError("JSON 형식이 아닙니다.")
        if not isinstance(payload, dict):
            raise db.RequestError("JSON 객체가 아닙니다.")
        return payload

    def _file(self, path: Path) -> None:
        root = STATIC_DIR.resolve()
        try:
            target = path.resolve()
        except OSError:
            self._json(404, {"error": "파일을 찾지 못했습니다."})
            return
        if not target.is_relative_to(root) or not target.is_file():
            self._json(404, {"error": "파일을 찾지 못했습니다."})
            return
        data = target.read_bytes()
        self._bytes(200, data, MIME.get(target.suffix.lower(), "application/octet-stream"))

    def _json(self, status: int, payload) -> None:
        self._bytes(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _bytes(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:
        print("%s - %s" % (self.address_string(), fmt % args))


def parse_plan(params: dict) -> tuple[datetime, float]:
    date = (params.get("date") or [""])[0].strip()
    start = (params.get("startTime") or [""])[0].strip()
    duration_raw = (params.get("durationHours") or [""])[0].strip()
    if not date or not start or not duration_raw:
        raise db.RequestError(
            "추천에는 날짜(date), 시작 시각(startTime), 소요 시간(durationHours)이 필요합니다. "
            "서버의 현재 시각은 사용하지 않습니다."
        )
    when = None
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            when = datetime.strptime(f"{date} {start}", pattern)
            break
        except ValueError:
            continue
    if when is None:
        raise db.RequestError("날짜는 YYYY-MM-DD, 시작 시각은 HH:MM 형식이어야 합니다.")
    try:
        duration = float(duration_raw)
    except ValueError:
        raise db.RequestError("소요 시간(durationHours)은 숫자여야 합니다.")
    if duration <= 0 or duration > 24:
        raise db.RequestError("소요 시간은 0보다 크고 24시간 이하여야 합니다.")
    return when, duration


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return
    loaded = []
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not key or not value or key in os.environ:
            continue
        os.environ[key] = value
        loaded.append(key)
    if loaded:
        logger.info("loaded .env keys: %s", ", ".join(loaded))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    load_dotenv()
    if os.environ.get("VLM_API_KEY"):
        logger.info("VLM_API_KEY is set")
    else:
        logger.info("VLM_API_KEY is empty; activities stay unknown")
    if places_enabled():
        logger.info("KAKAO_REST_API_KEY is set")
    else:
        logger.info("KAKAO_REST_API_KEY is empty; place search stays off")
    db.init_db()
    port = int(os.environ.get("PORT", "8765"))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"http://127.0.0.1:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
