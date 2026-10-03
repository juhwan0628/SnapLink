import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from domain import ACTIVITIES
from infer import (
    LOW_CONFIDENCE,
    MAX_IMAGE_BYTES,
    activity_enum,
    infer_cluster,
    interpret_vlm,
    prepare_image,
    vision_prompt,
)
from pipeline import build_drafts


def _large_jpeg() -> bytes:
    from PIL import Image

    image = Image.effect_noise((2200, 1600), 100).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


class PromptTests(unittest.TestCase):
    def test_prompt_uses_domain_enum_and_one_outing(self):
        prompt = vision_prompt()
        for activity_id, _label in ACTIVITIES:
            self.assertIn(activity_id, prompt)
        self.assertIn("unknown", prompt)
        self.assertIn("same outing", prompt)
        self.assertIn("shopping", prompt)
        self.assertNotIn("확신이 낮으면 activities를 비우세요", prompt)
        self.assertNotIn("drink", activity_enum())
        self.assertIn("정확한 상호", prompt)
        self.assertIn("walk로 두지 마세요", prompt)


class InterpretTests(unittest.TestCase):
    def test_bakery_display_is_shopping_not_unknown(self):
        result = interpret_vlm({
            "activity": "shopping",
            "confidence": 0.9,
            "place_type": "bakery",
            "scene_tags": ["indoor"],
            "evidence": "Multiple photos show bread and pastries displayed for sale inside a bakery.",
        })
        self.assertIsNone(result["fallbackReason"])
        self.assertEqual(result["activities"][0]["type"], "shopping")
        self.assertEqual(result["activities"][0]["source"], "vlm")
        self.assertGreaterEqual(result["activities"][0]["confidence"], 0.75)
        self.assertEqual(result["placeHint"], "bakery")
        self.assertEqual(result["tags"], ["실내"])
        self.assertNotIn("meal", result["note"])

    def test_served_meal_stays_meal(self):
        result = interpret_vlm({
            "activity": "meal",
            "confidence": 0.9,
            "place_type": "restaurant",
            "scene_tags": ["indoor"],
            "evidence": "Photos show served dishes on a table.",
        })
        self.assertEqual(result["activities"][0]["type"], "meal")
        self.assertIsNone(result["fallbackReason"])

    def test_night_landmark_stays_unknown_and_keeps_scene(self):
        result = interpret_vlm({
            "activity": "unknown",
            "confidence": 0.35,
            "place_type": "landmark",
            "scene_tags": ["outdoor", "night"],
            "evidence": "Night photos show an outdoor structure without a clear activity.",
        })
        self.assertEqual(result["fallbackReason"], "genuinely_unknown")
        self.assertEqual(result["activities"][0]["type"], "unknown")
        self.assertEqual(result["activities"][0]["source"], "none")
        self.assertEqual(result["source"], "vlm")
        self.assertEqual(result["tags"], ["야외", "밤"])
        self.assertEqual(result["placeHint"], "landmark")
        self.assertIn("활동을 특정하지 못했습니다", result["note"])
        self.assertIn("야외", result["note"])

    def test_shopping_at_075_is_kept(self):
        result = interpret_vlm({"activity": "shopping", "confidence": 0.75, "place_type": "bakery"})
        self.assertEqual(result["activities"][0]["type"], "shopping")
        self.assertEqual(result["activities"][0]["confidence"], 0.75)
        self.assertGreater(result["activities"][0]["confidence"], LOW_CONFIDENCE)

    def test_copied_zero_confidence_is_not_stored_as_cafe(self):
        result = interpret_vlm({
            "activities": [{"type": "cafe", "confidence": 0.0}],
            "tags": [],
            "placeHint": None,
            "note": "",
        })
        self.assertEqual(result["fallbackReason"], "low_confidence")
        self.assertEqual(result["parsedActivity"], "cafe")
        self.assertEqual(result["activities"][0]["type"], "unknown")
        self.assertIn("확신이 너무 낮아", result["note"])

    def test_invalid_activity_is_not_called_a_model_unknown(self):
        result = interpret_vlm({"activity": "bakery", "confidence": 0.9, "place_type": "bakery", "scene_tags": ["indoor"]})
        self.assertEqual(result["fallbackReason"], "invalid_activity")
        self.assertEqual(result["parsedActivity"], "bakery")
        self.assertEqual(result["activities"][0]["type"], "unknown")
        self.assertIn("허용된 활동 이름", result["note"])
        self.assertNotIn("특정하지 못했습니다", result["note"])
        self.assertEqual(result["tags"], ["실내"])

    def test_unreadable_json_is_a_parse_error(self):
        seen = {}

        def request_fn(content):
            seen["called"] = True
            return "this is not json"

        result = infer_cluster([{"id": "a"}], lambda _photo: (b"\xff\xd8\xff\xd9", "image/jpeg"), request_fn)
        self.assertTrue(seen["called"])
        self.assertEqual(result["fallbackReason"], "parse_error")
        self.assertIn("결과를 읽지 못했습니다", result["note"])
        self.assertNotIn("요청에 실패", result["note"])

    def test_api_error_is_not_a_model_unknown(self):
        def request_fn(_content):
            raise TimeoutError("timed out")

        result = infer_cluster([{"id": "a"}], lambda _photo: (b"\xff\xd8\xff\xd9", "image/jpeg"), request_fn)
        self.assertEqual(result["fallbackReason"], "api_error")
        self.assertIn("요청에 실패했습니다", result["note"])
        self.assertNotIn("특정하지 못했습니다", result["note"])
        self.assertNotIn("sk-", result["note"])


class ClusterImageTests(unittest.TestCase):
    def test_three_large_photos_are_one_request(self):
        blob = _large_jpeg()
        self.assertGreater(len(blob), MAX_IMAGE_BYTES)
        calls = []

        def request_fn(content):
            calls.append(content)
            return json.dumps({
                "activity": "shopping",
                "confidence": 0.9,
                "place_type": "bakery",
                "scene_tags": ["indoor"],
                "evidence": "Bread is displayed for sale.",
            })

        photos = [{"id": str(index)} for index in range(3)]
        result = infer_cluster(photos, lambda _photo: (blob, "image/jpeg"), request_fn)
        self.assertEqual(len(calls), 1)
        images = [part for part in calls[0] if part["type"] == "image_url"]
        self.assertEqual(len(images), 3)
        self.assertIn("same outing", calls[0][0]["text"])
        self.assertEqual(result["activities"][0]["type"], "shopping")
        self.assertEqual(result["placeHint"], "bakery")

    def test_oversized_photo_is_resized_under_the_send_limit(self):
        prepared = prepare_image(_large_jpeg(), "image/jpeg")
        self.assertIsNotNone(prepared)
        data, mime = prepared
        self.assertEqual(mime, "image/jpeg")
        self.assertLessEqual(len(data), MAX_IMAGE_BYTES)
        self.assertGreater(len(data), 1000)

    def test_pipeline_keeps_scene_tags_without_a_new_column(self):
        photos = [{
            "id": "a",
            "filename": "a.jpg",
            "storedName": "a.jpg",
            "takenAt": "2024-05-11T21:30:00",
            "lat": None,
            "lng": None,
        }]
        drafts = build_drafts(
            photos,
            lambda _photos: interpret_vlm({
                "activity": "unknown",
                "confidence": 0.35,
                "place_type": "landmark",
                "scene_tags": ["outdoor", "night"],
                "evidence": "Outdoor structure at night.",
            }),
            None,
        )
        self.assertEqual(drafts[0]["activities"][0]["type"], "unknown")
        self.assertEqual(drafts[0]["tags"], ["야외", "밤"])
        self.assertEqual(drafts[0]["places"][0]["name"], "landmark")
        self.assertEqual(drafts[0]["places"][0]["precision"], "hint")
