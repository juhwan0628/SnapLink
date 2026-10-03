import json
import os
import sys
import tempfile
import threading
import unittest
import uuid
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["MEMORY_DATA_DIR"] = tempfile.mkdtemp(prefix="gem-")
os.environ["MEMORY_GEOCODE"] = "0"
os.environ.pop("VLM_API_KEY", None)

import db
import server
from test_core import make_jpeg


def _multipart(files):
    boundary = "----gemtest"
    chunks = []
    for filename, data, mime in files:
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photos"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        )
        chunks.append(head.encode("utf-8") + data + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(chunks)
    return body, f"multipart/form-data; boundary={boundary}"


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=3)

    def request(self, method, path, body=None, headers=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            headers=headers or {},
            method=method,
        )
        try:
            with urllib.request.urlopen(req) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
                if "json" in content_type:
                    return response.status, json.loads(raw.decode("utf-8"))
                return response.status, raw
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                payload = raw
            return exc.code, payload

    def test_heic_upload_preview_and_original_byte_deduplication(self):
        from test_reliability import heic_photo
        photo_bytes = heic_photo()
        _, group = self.request("POST", "/snaplink/api/groups",
            json.dumps({"name": "HEIC", "members": ["테스터"]}).encode(),
            {"Content-Type": "application/json"})
        body, content_type = _multipart([("iphone.heic", photo_bytes, "image/heic")])
        path = f"/snaplink/api/groups/{group['id']}/import"
        status, imported = self.request("POST", path, body, {"Content-Type": content_type})
        self.assertEqual(status, 200, imported)
        self.assertEqual(imported["importedPhotos"], 1)
        outing = imported["group"]["outings"][0]
        self.assertEqual(outing["startTime"], "2024-05-11T14:30:00")
        photo = outing["photos"][0]
        self.assertTrue(photo["url"].startswith("/snaplink/api/photos/"), photo["url"])
        status, preview = self.request("GET", f"/snaplink/api/photos/{photo['id']}")
        self.assertEqual(status, 200)
        self.assertTrue(preview.startswith(b"\xff\xd8"))
        status, repeated = self.request("POST", path, body, {"Content-Type": content_type})
        self.assertEqual(status, 200)
        self.assertEqual(repeated["duplicateCount"], 1)
        self.assertEqual(repeated["importedPhotos"], 0)

    def test_page_and_import_flow(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("함께한 경험".encode("utf-8"), page)

        status, group = self.request(
            "POST",
            "/api/groups",
            json.dumps({"name": "토요일", "members": "지수, 민재, 하준"}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual([member["name"] for member in group["members"]], ["지수", "민재", "하준"])

        body, content_type = _multipart([
            ("noon.jpg", make_jpeg("2024:05:11 14:30:00"), "image/jpeg"),
            ("later.jpg", make_jpeg("2024:05:11 16:10:00"), "image/jpeg"),
            ("notes.txt", b"hello", "text/plain"),
        ])
        status, imported = self.request(
            "POST",
            f"/api/groups/{group['id']}/import",
            body,
            {"Content-Type": content_type},
        )
        self.assertEqual(status, 200, imported)
        self.assertEqual(imported["skipped"], [{"filename": "notes.txt", "reason": "지원하지 않는 형식입니다."}])
        drafts = [outing for outing in imported["group"]["outings"] if outing["status"] == "draft"]
        self.assertEqual(len(drafts), 1)
        outing = drafts[0]
        self.assertEqual(len(outing["photos"]), 2)
        self.assertEqual(outing["photos"][0]["takenAt"], "2024-05-11T14:30:00")
        self.assertAlmostEqual(outing["areaLat"], 37.5445, places=3)
        self.assertEqual(outing["activities"][0]["type"], "unknown")
        self.assertEqual(outing["activities"][0]["source"], "none")
        self.assertEqual(outing["inferenceSource"], "unknown")
        self.assertIn("unknown", outing["inferenceNote"])
        self.assertEqual(outing["areaName"], "")

        photo_status, photo = self.request("GET", outing["photos"][0]["url"])
        self.assertEqual(photo_status, 200)
        self.assertTrue(photo.startswith(b"\xff\xd8"))

        status, updated = self.request(
            "PATCH",
            f"/api/outings/{outing['id']}",
            json.dumps({
                "areaName": "성수",
                "places": ["공방 골목"],
                "activities": ["workshop", "performance"],
                "tags": ["실내"],
                "feedback": "처음 해본 조합",
                "rating": 5,
            }).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200, updated)
        status, confirmed = self.request("POST", f"/api/outings/{outing['id']}/confirm", b"{}")
        saved = next(item for item in confirmed["outings"] if item["id"] == outing["id"])
        self.assertEqual(saved["status"], "confirmed")
        self.assertEqual(saved["areaName"], "성수")
        self.assertEqual([activity["type"] for activity in saved["activities"]], ["workshop", "performance"])
        self.assertTrue(all(activity["source"] == "user" for activity in saved["activities"]))

        status, missing = self.request("GET", f"/api/groups/{group['id']}/recommend?novelty=0.9")
        self.assertEqual(status, 400, missing)
        self.assertIn("date", missing["error"])
        self.assertIn("서버의 현재 시각", missing["error"])

        status, recommendation = self.request(
            "GET",
            f"/api/groups/{group['id']}/recommend?novelty=0.9&date=2026-10-03&startTime=18:00&durationHours=3",
        )
        self.assertEqual(status, 200, recommendation)
        self.assertEqual(recommendation["historyCount"], 1)
        self.assertGreaterEqual(len(recommendation["items"]), 1)
        self.assertEqual(recommendation["candidateSet"]["kind"], "mvp-hardcoded")
        self.assertEqual(recommendation["candidateSet"]["count"], 14)
        self.assertEqual(recommendation["travelCost"], "omitted")
        self.assertNotIn("travel", recommendation["items"][0]["scores"])
        self.assertEqual(recommendation["plan"]["startTime"], "18:00")
        self.assertTrue(recommendation["weightNote"])

    def test_merge_split_outings(self):
        _status, group = self.request(
            "POST",
            "/api/groups",
            json.dumps({"name": "하루", "members": ["민재"]}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        body, content_type = _multipart([
            ("early.jpg", make_jpeg("2024:05:11 14:30:00"), "image/jpeg"),
            ("night.jpg", make_jpeg("2024:05:11 22:40:00"), "image/jpeg"),
        ])
        status, imported = self.request(
            "POST",
            f"/api/groups/{group['id']}/import",
            body,
            {"Content-Type": content_type},
        )
        self.assertEqual(status, 200, imported)
        drafts = sorted(
            [outing for outing in imported["group"]["outings"] if outing["status"] == "draft"],
            key=lambda outing: outing["startTime"],
        )
        self.assertEqual(len(drafts), 2)
        self.assertEqual(drafts[1]["splitReason"], "time")
        status, merged = self.request(
            "POST",
            f"/api/outings/{drafts[0]['id']}/merge",
            json.dumps({"sourceId": drafts[1]["id"]}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200, merged)
        remaining = [outing for outing in merged["outings"] if outing["status"] == "draft"]
        self.assertEqual(len(remaining), 1)
        self.assertEqual(len(remaining[0]["photos"]), 2)
        self.assertEqual(remaining[0]["endTime"], "2024-05-11T22:40:00")

    def test_same_photo_bytes_are_not_imported_twice(self):
        _status, group = self.request(
            "POST",
            "/api/groups",
            json.dumps({"name": "중복", "members": ["민재"]}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        jpeg = make_jpeg("2024:07:01 12:00:00")
        body, content_type = _multipart([
            ("first.jpg", jpeg, "image/jpeg"),
            ("renamed.jpg", jpeg, "image/jpeg"),
        ])
        status, imported = self.request(
            "POST",
            f"/api/groups/{group['id']}/import",
            body,
            {"Content-Type": content_type},
        )
        self.assertEqual(status, 200, imported)
        self.assertEqual(imported["duplicateCount"], 1)
        self.assertEqual(imported["importedPhotos"], 1)
        drafts = [outing for outing in imported["group"]["outings"] if outing["status"] == "draft"]
        self.assertEqual(len(drafts), 1)
        self.assertEqual(len(drafts[0]["photos"]), 1)
        self.assertTrue(drafts[0]["importBatch"])

        again, content_type = _multipart([("other-name.jpg", jpeg, "image/jpeg")])
        status, repeated = self.request(
            "POST",
            f"/api/groups/{group['id']}/import",
            again,
            {"Content-Type": content_type},
        )
        self.assertEqual(status, 200, repeated)
        self.assertEqual(repeated["duplicateCount"], 1)
        self.assertEqual(repeated["importedPhotos"], 0)
        self.assertIsNone(repeated["batchId"])
        still = [outing for outing in repeated["group"]["outings"] if outing["status"] == "draft"]
        self.assertEqual(len(still), 1)
        self.assertEqual(len(still[0]["photos"]), 1)

    def test_save_outings_confirms_a_batch_and_rejects_a_missing_activity(self):
        _status, group = self.request(
            "POST",
            "/api/groups",
            json.dumps({"name": "저장", "members": ["민재"]}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        body, content_type = _multipart([
            ("morning.jpg", make_jpeg("2024:08:02 10:00:00"), "image/jpeg"),
            ("evening.jpg", make_jpeg("2024:08:02 19:00:00"), "image/jpeg"),
        ])
        status, imported = self.request(
            "POST",
            f"/api/groups/{group['id']}/import",
            body,
            {"Content-Type": content_type},
        )
        self.assertEqual(status, 200, imported)
        drafts = sorted(
            [outing for outing in imported["group"]["outings"] if outing["status"] == "draft"],
            key=lambda outing: outing["startTime"],
        )
        self.assertEqual(len(drafts), 2)
        self.assertEqual(drafts[0]["importBatch"], drafts[1]["importBatch"])
        status, rejected = self.request(
            "POST",
            f"/api/groups/{group['id']}/save-outings",
            json.dumps({"outings": [{"id": drafts[0]["id"], "activities": ["market", "nature"]}]}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400, rejected)
        self.assertIn("활동", rejected["error"])
        status, saved = self.request(
            "POST",
            f"/api/groups/{group['id']}/save-outings",
            json.dumps({"outings": [
                {"id": drafts[0]["id"], "activities": ["shopping"], "rating": None},
                {"id": drafts[1]["id"], "activities": ["walk"]},
            ]}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200, saved)
        confirmed = [outing for outing in saved["outings"] if outing["status"] == "confirmed"]
        self.assertEqual(
            {outing["id"]: [activity["type"] for activity in outing["activities"]] for outing in confirmed},
            {drafts[0]["id"]: ["shopping"], drafts[1]["id"]: ["walk"]},
        )
        self.assertTrue(all(outing["rating"] is None or outing["id"] != drafts[0]["id"] for outing in confirmed))

    def test_user_activity_list_omits_place_and_environment(self):
        status, meta = self.request("GET", "/api/meta")
        self.assertEqual(status, 200, meta)
        ids = [activity["id"] for activity in meta["userActivities"]]
        self.assertEqual(ids, ["meal", "cafe", "exhibition", "workshop", "performance", "walk", "shopping", "activity", "bar"])
        self.assertIn("market", [activity["id"] for activity in meta["activities"]])

    def test_import_screen_hides_debug_copy(self):
        source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        view = source[source.index("function importView"):source.index("function nextView")]
        card = source[source.index("function experienceCard"):source.index("function draftMenu")]
        detail = source[source.index("function detailEditor"):source.index("function toast")]
        photos = source[source.index("function reviewPhotos"):source.index("function draftDebug")]
        self.assertIn("지난 경험 불러오기", view)
        self.assertIn("사진 선택하기", view)
        self.assertIn("사진 분석하기", view)
        self.assertNotIn("모두 저장하기", view)
        self.assertNotIn("VLM_API_KEY", view)
        self.assertNotIn("timeSplitHours", view)
        self.assertNotIn("외출로 묶기", view)
        self.assertIn("어떤 활동이었나요?", card)
        self.assertIn("♡", card)
        self.assertNotIn("분석된 활동", card)
        self.assertNotIn("기록에 저장", detail)
        self.assertNotIn("만족도", detail)
        self.assertNotIn("위치 있음", photos)
        self.assertNotIn("filename", photos)

    def test_known_activity_confirms_and_one_tap_saves_the_unknown(self):
        _status, group = self.request(
            "POST",
            "/api/groups",
            json.dumps({"name": "한 번", "members": ["민재"]}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        known_id = str(uuid.uuid4())
        unknown_id = str(uuid.uuid4())
        batch = str(uuid.uuid4())

        def draft(outing_id, activity):
            return {
                "id": outing_id,
                "startTime": "2024-08-12T09:21:00",
                "endTime": "2024-08-12T10:00:00",
                "areaName": "신성동",
                "activities": [activity],
                "tags": ["실내"],
                "places": [{"name": "bakery", "precision": "hint"}],
                "photos": [],
            }

        db.save_import(group["id"], [
            draft(known_id, {"type": "shopping", "confidence": 0.9, "source": "vlm"}),
            draft(unknown_id, {"type": "unknown", "confidence": 0, "source": "none"}),
        ], batch)
        _status, loaded = self.request("GET", f"/api/groups/{group['id']}")
        by_id = {outing["id"]: outing for outing in loaded["outings"]}
        self.assertEqual(by_id[known_id]["status"], "confirmed")
        self.assertEqual(by_id[unknown_id]["status"], "draft")
        self.assertFalse(by_id[known_id]["liked"])
        status, chosen = self.request(
            "POST",
            f"/api/outings/{unknown_id}/choose",
            json.dumps({"activity": "cafe"}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200, chosen)
        saved = next(outing for outing in chosen["outings"] if outing["id"] == unknown_id)
        self.assertEqual(saved["status"], "confirmed")
        self.assertEqual(saved["activities"][0]["type"], "cafe")
        self.assertEqual(saved["activities"][0]["source"], "user")
        status, heart = self.request(
            "POST",
            f"/api/outings/{known_id}/like",
            json.dumps({"liked": True}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200, heart)
        liked = next(outing for outing in heart["outings"] if outing["id"] == known_id)
        self.assertTrue(liked["liked"])
        self.assertIsNone(liked["rating"])
        status, cleared = self.request(
            "POST",
            f"/api/outings/{known_id}/like",
            json.dumps({"liked": False}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        self.assertFalse(next(outing for outing in cleared["outings"] if outing["id"] == known_id)["liked"])


if __name__ == "__main__":
    unittest.main()
