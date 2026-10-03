import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cluster import cluster_photos
from exif_parse import parse_image_exif
from infer import unknown_activity
from pipeline import build_drafts
from recommend import _preference, candidate_mode, recommend_for_group


def _u16(value: int) -> bytes:
    return value.to_bytes(2, "little")


def _u32(value: int) -> bytes:
    return value.to_bytes(4, "little")


def _rats(pairs) -> bytes:
    out = bytearray()
    for numerator, denominator in pairs:
        out += _u32(numerator) + _u32(denominator)
    return bytes(out)


def make_jpeg(taken="2024:05:11 14:30:00", lat_ref=b"N", lng_ref=b"E") -> bytes:
    clock = taken.encode("ascii") + b"\x00"
    assert len(clock) == 20
    buf = bytearray(b"II" + _u16(42) + _u32(0))

    def append(data: bytes) -> int:
        offset = len(buf)
        buf.extend(data)
        return offset

    def add_ifd(entries) -> int:
        offset = len(buf)
        buf.extend(_u16(len(entries)))
        slots = []
        for tag, type_id, count, payload in entries:
            buf.extend(_u16(tag) + _u16(type_id) + _u32(count))
            slots.append(len(buf))
            buf.extend(b"\x00\x00\x00\x00")
        buf.extend(_u32(0))
        for slot, (_tag, _type_id, _count, payload) in zip(slots, entries):
            if len(payload) <= 4:
                buf[slot : slot + 4] = payload + b"\x00" * (4 - len(payload))
            else:
                buf[slot : slot + 4] = _u32(append(payload))
        return offset

    exif_off = add_ifd([(0x9003, 2, 20, clock)])
    gps_off = add_ifd([
        (0x0001, 2, 2, lat_ref + b"\x00"),
        (0x0002, 5, 3, _rats([(37, 1), (32, 1), (402, 10)])),
        (0x0003, 2, 2, lng_ref + b"\x00"),
        (0x0004, 5, 3, _rats([(127, 1), (3, 1), (2052, 100)])),
    ])
    ifd0 = add_ifd([
        (0x0132, 2, 20, clock),
        (0x8769, 4, 1, _u32(exif_off)),
        (0x8825, 4, 1, _u32(gps_off)),
    ])
    buf[4:8] = _u32(ifd0)
    payload = b"Exif\x00\x00" + bytes(buf)
    segment = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    return b"\xff\xd8" + segment + b"\xff\xd9"


def _photo(photo_id, taken, lat=37.5445, lng=127.0557):
    return {"id": photo_id, "takenAt": taken, "lat": lat, "lng": lng}


class ExifTests(unittest.TestCase):
    def test_reads_time_and_seongsu_coordinates(self):
        parsed = parse_image_exif(make_jpeg())
        self.assertEqual(parsed["taken_at"], "2024-05-11T14:30:00")
        self.assertAlmostEqual(parsed["lat"], 37.5445, places=4)
        self.assertAlmostEqual(parsed["lng"], 127.0557, places=4)

    def test_southern_and_western_signs(self):
        parsed = parse_image_exif(make_jpeg(lat_ref=b"S", lng_ref=b"W"))
        self.assertAlmostEqual(parsed["lat"], -37.5445, places=4)
        self.assertAlmostEqual(parsed["lng"], -127.0557, places=4)

    def test_jpeg_without_exif(self):
        self.assertEqual(parse_image_exif(b"\xff\xd8\xff\xd9"), {})


class ClusterTests(unittest.TestCase):
    def test_meal_and_cafe_stay_together(self):
        photos = [
            _photo("a", "2024-05-11T14:30:00"),
            _photo("b", "2024-05-11T16:10:00"),
        ]
        clusters = cluster_photos(photos)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["photo_ids"], ["a", "b"])

    def test_long_gap_splits(self):
        photos = [
            _photo("a", "2024-05-11T10:00:00"),
            _photo("b", "2024-05-11T17:30:00"),
        ]
        clusters = cluster_photos(photos)
        self.assertEqual([item["split_reason"] for item in clusters], ["first", "time"])

    def test_far_move_splits(self):
        photos = [
            _photo("a", "2024-05-11T14:00:00", 37.54, 127.05),
            _photo("b", "2024-05-11T15:00:00", 37.80, 127.05),
        ]
        clusters = cluster_photos(photos)
        self.assertEqual(clusters[1]["split_reason"], "distance")

    def test_missing_time_is_not_merged(self):
        photos = [
            _photo("a", "2024-05-11T14:00:00"),
            {"id": "b", "takenAt": None, "lat": None, "lng": None},
        ]
        clusters = cluster_photos(photos)
        self.assertEqual(clusters[1]["split_reason"], "no_timestamp")
        self.assertEqual(clusters[1]["photo_ids"], ["b"])


class PipelineTests(unittest.TestCase):
    def test_names_area_and_leaves_activity_unknown(self):
        photos = [
            {
                "id": "a",
                "filename": "a.jpg",
                "storedName": "a.jpg",
                "takenAt": "2024-05-11T14:30:00",
                "lat": 37.5445,
                "lng": 127.0557,
            },
            {
                "id": "b",
                "filename": "b.jpg",
                "storedName": "b.jpg",
                "takenAt": "2024-05-11T16:00:00",
                "lat": 37.545,
                "lng": 127.056,
            },
        ]
        drafts = build_drafts(
            photos,
            lambda _photos: unknown_activity("활동은 unknown입니다. 직접 선택해 주세요."),
            lambda _lat, _lng: "성수",
        )
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["areaName"], "성수")
        self.assertEqual(drafts[0]["areaPrecision"], "area")
        self.assertEqual(drafts[0]["activities"][0]["type"], "unknown")
        self.assertEqual(drafts[0]["activities"][0]["source"], "none")
        self.assertEqual(drafts[0]["inferenceSource"], "unknown")
        self.assertIn("unknown", drafts[0]["inferenceNote"])


def _history():
    return [
        {"startTime": "2024-05-01T13:00:00", "areaName": "성수", "activities": ["meal", "cafe"], "rating": 4},
        {"startTime": "2024-06-01T13:00:00", "areaName": "연남", "activities": ["meal", "cafe"], "rating": 4},
        {"startTime": "2024-07-01T13:00:00", "areaName": "한남", "activities": ["meal", "cafe"], "rating": 3},
    ]


class RecommendTests(unittest.TestCase):
    def test_same_combo_in_another_neighborhood_is_not_a_new_experience(self):
        history = _history()
        self.assertEqual(candidate_mode(["meal", "cafe"], "연남", True, history), "repeat")
        self.assertEqual(candidate_mode(["meal", "cafe"], "홍대", True, history), "known_area_familiar_combo")
        self.assertEqual(
            candidate_mode(["workshop", "performance"], "성수", True, history),
            "known_area_new_combo",
        )

    def test_slider_changes_what_wins(self):
        now = datetime(2026, 10, 3, 18, 0)
        low = recommend_for_group(
            _history(), novelty_weight=0.05, member_count=3, when=now, duration_hours=3
        )
        high = recommend_for_group(
            _history(), novelty_weight=0.9, member_count=3, when=now, duration_hours=3
        )
        self.assertEqual(low["items"][0]["experienceId"], "meal-cafe", low["items"][0]["title"])
        self.assertEqual(low["items"][0]["mode"], "repeat")
        self.assertEqual(low["items"][0]["areaName"], "한남")
        self.assertNotEqual(high["items"][0]["experienceId"], "meal-cafe")
        self.assertIn(high["items"][0]["mode"], {"known_area_new_combo", "new_area_new_combo"})
        self.assertIn("아직 안 해본", high["items"][0]["title"])
        self.assertEqual(high["candidateSet"]["kind"], "mvp-hardcoded")
        self.assertEqual(high["candidateSet"]["count"], 14)
        self.assertEqual(high["travelCost"], "omitted")
        self.assertEqual(high["plan"]["date"], "2026-10-03")
        self.assertEqual(high["plan"]["startTime"], "18:00")
        self.assertNotIn("travel", high["items"][0]["scores"])
        for item in high["items"]:
            if item["experienceId"] == "meal-cafe":
                self.assertNotIn("아직 함께 안 해본", item["title"])
            scores = item["scores"]
            summed = round(
                sum(scores[key] for key in ("preference", "novelty", "context", "group", "repetition")),
                3,
            )
            self.assertAlmostEqual(summed, scores["total"], places=3)

    def test_heart_adds_smoothed_preference_and_history_stays_without_it(self):
        liked = [{"startTime": "2024-08-01T13:00:00", "areaName": "성수", "activities": ["cafe"], "liked": True}]
        plain = [{"startTime": "2024-08-01T13:00:00", "areaName": "성수", "activities": ["walk"], "liked": False}]
        history = liked + plain
        self.assertGreater(_preference(["cafe"], history), _preference(["walk"], history))
        self.assertLess(_preference(["cafe"], history), 0.9)
        self.assertEqual(candidate_mode(["walk"], "성수", True, history), "repeat")
        legacy = [{"startTime": "2024-05-01T13:00:00", "areaName": "성수", "activities": ["meal"], "rating": 5}]
        neutral = [{"startTime": "2024-05-01T13:00:00", "areaName": "성수", "activities": ["meal"], "rating": 3}]
        self.assertGreater(_preference(["meal"], legacy), _preference(["meal"], neutral))


if __name__ == "__main__":
    unittest.main()
