import os
import re
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.pop("KAKAO_REST_API_KEY", None)
os.environ.pop("VLM_API_KEY", None)

from candidates import patterns_from_payload
from courses import (
    arrange_courses,
    assemble_recommendation,
    is_child_only_place,
    map_markers,
    parse_search_location,
    patterns_for_retrieval,
    same_visit,
)
from providers import KakaoPlaceProvider, normalize_kakao_document
from queries import ACTIVITY_QUERIES


WHEN = datetime(2026, 10, 3, 18, 0)
HISTORY = [
    {"startTime": "2024-05-01T13:00:00", "areaName": "성수", "activities": ["meal", "cafe"], "rating": 4},
    {"startTime": "2024-06-01T13:00:00", "areaName": "연남", "activities": ["meal", "cafe"], "rating": 5},
    {"startTime": "2024-07-01T13:00:00", "areaName": "한남", "activities": ["meal", "cafe"], "rating": 3},
]
LOCATION = {
    "locationMode": "coordinates",
    "areaName": "",
    "latitude": 37.5563,
    "longitude": 126.9236,
    "searchRadiusKm": 5,
}


def place(provider_id, name, lat, lng, queries, category="음식점", address=None):
    return {
        "provider": "kakao",
        "providerId": provider_id,
        "name": name,
        "category": category,
        "address": address or f"서울 테스트로 {provider_id}",
        "lat": lat,
        "lng": lng,
        "distanceM": None,
        "detailUrl": "https://place.example/test",
        "retrievedAt": "2026-10-03T09:00:00+00:00",
        "queries": queries,
    }


class FakePlaces:
    name = "kakao"

    def __init__(self, rows, fail=False):
        self.rows = rows
        self.fail = fail
        self.queries = []

    def enabled(self):
        return True

    def resolve_area(self, area_name):
        return {"lat": 37.5563, "lng": 126.9236, "name": area_name}

    def search_places(self, query, lat, lng, radius_m):
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("HTTP 503")
        return [row for row in self.rows if query in row["queries"]]


def nearby_rows():
    return [
        place("meal-1", "테스트식당", 37.557, 126.924, ["음식점", "식당"]),
        place("meal-1", "테스트식당", 37.557, 126.924, ["음식점", "식당"]),
        place("meal-far", "먼식당", 37.70, 127.20, ["음식점"]),
        place("cafe-1", "테스트카페", 37.558, 126.925, ["카페", "디저트 카페"], "카페"),
        place("cafe-far", "먼카페", 37.62, 126.92, ["카페"], "카페"),
    ]


class PlaceRetrievalTests(unittest.TestCase):
    def test_kakao_document_normalizes_without_rating(self):
        parsed = normalize_kakao_document({
            "id": "123",
            "place_name": "테스트카페",
            "category_name": "음식점 > 카페",
            "road_address_name": "서울 어딘가 1",
            "x": "126.923600",
            "y": "37.556300",
            "distance": "420",
            "place_url": "https://place.map.kakao.com/123",
        }, "2026-10-03T09:00:00+00:00")
        self.assertEqual(parsed["provider"], "kakao")
        self.assertEqual(parsed["providerId"], "123")
        self.assertEqual(parsed["name"], "테스트카페")
        self.assertEqual(parsed["address"], "서울 어딘가 1")
        self.assertAlmostEqual(parsed["lat"], 37.5563)
        self.assertNotIn("rating", parsed)

    def test_missing_key_falls_back_to_experience_only(self):
        provider = KakaoPlaceProvider()
        self.assertFalse(provider.enabled())
        result = assemble_recommendation(
            HISTORY,
            novelty_weight=0.05,
            member_count=3,
            when=WHEN,
            duration_hours=3,
            location=LOCATION,
            place_provider=provider,
        )
        self.assertEqual(result["grounding"]["places"], "fallback")
        self.assertIn("KAKAO_REST_API_KEY", result["grounding"]["reason"])
        self.assertEqual(result["courses"], [])
        self.assertGreaterEqual(len(result["items"]), 1)
        self.assertEqual(result["travelCost"], "omitted")

    def test_provider_error_keeps_experience_ranking(self):
        result = assemble_recommendation(
            HISTORY,
            novelty_weight=0.05,
            member_count=3,
            when=WHEN,
            duration_hours=3,
            location=LOCATION,
            place_provider=FakePlaces(nearby_rows(), fail=True),
        )
        self.assertEqual(result["courses"], [])
        self.assertIn("실패", result["grounding"]["reason"])
        self.assertEqual(result["items"][0]["experienceId"], "meal-cafe")
        self.assertNotIn("travel", result["items"][0]["scores"])

    def test_radius_duplicate_and_leg_limits(self):
        provider = FakePlaces(nearby_rows())
        result = assemble_recommendation(
            HISTORY,
            novelty_weight=0.05,
            member_count=3,
            when=WHEN,
            duration_hours=3,
            location=LOCATION,
            place_provider=provider,
        )
        self.assertTrue(result["courses"])
        names = [stop["place"]["name"] for course in result["courses"] for stop in course["stops"]]
        self.assertIn("테스트식당", names)
        self.assertIn("테스트카페", names)
        self.assertNotIn("먼식당", names)
        self.assertNotIn("먼카페", names)
        ids = [stop["place"]["providerId"] for course in result["courses"] for stop in course["stops"]]
        self.assertEqual(ids.count("meal-1"), sum(1 for course in result["courses"] if course["stops"][0]["place"]["providerId"] == "meal-1"))
        for course in result["courses"]:
            self.assertLessEqual(course["stops"][1]["distanceFromPreviousM"], 3000)
            self.assertEqual(course["scores"]["preference"], result["items"][0]["scores"]["preference"])
            self.assertIsNone(course["scores"]["schedule"])
            blob = str(course)
            self.assertNotIn("별점", blob)
            self.assertNotIn("인기", blob)
            for evidence in course["realWorldEvidence"]:
                self.assertIsNone(evidence["rating"])
                self.assertEqual(evidence["hours"], "미확인")
        allowed = {query for queries in ACTIVITY_QUERIES.values() for query in queries}
        self.assertTrue(set(provider.queries) <= allowed)
        markers = map_markers(result["courses"][0])
        self.assertEqual(markers[0]["order"], 1)
        self.assertEqual(markers[0]["name"], result["courses"][0]["stops"][0]["place"]["name"])
        self.assertTrue(markers[0]["address"])
        self.assertIsNotNone(markers[0]["lat"])

    def test_no_location_does_not_invent_an_area(self):
        provider = FakePlaces(nearby_rows())
        result = assemble_recommendation(
            HISTORY,
            novelty_weight=0.7,
            member_count=3,
            when=WHEN,
            duration_hours=3,
            location=None,
            place_provider=provider,
        )
        self.assertEqual(provider.queries, [])
        self.assertEqual(result["courses"], [])
        self.assertIn("위치를 주지 않아", result["grounding"]["reason"])
        with self.assertRaises(ValueError):
            parse_search_location({"locationMode": "area", "areaName": "", "searchRadiusKm": "3"})

    def test_llm_failure_uses_static_patterns(self):
        def broken():
            raise RuntimeError("timeout")

        result = assemble_recommendation(
            HISTORY,
            novelty_weight=0.05,
            member_count=3,
            when=WHEN,
            duration_hours=3,
            location=LOCATION,
            place_provider=FakePlaces(nearby_rows()),
            pattern_fn=broken,
        )
        self.assertEqual(result["candidateGeneration"]["source"], "static")
        self.assertTrue(result["courses"])

    def test_ungrounded_explanation_is_dropped(self):
        result = assemble_recommendation(
            HISTORY,
            novelty_weight=0.05,
            member_count=3,
            when=WHEN,
            duration_hours=3,
            location=LOCATION,
            place_provider=FakePlaces(nearby_rows()),
            explain_fn=lambda _evidence: "별점 4.8 인기 맛집이라 아마 열려 있을 것입니다.",
        )
        self.assertIsNone(result["courses"][0]["explanation"])

    def test_event_pattern_is_not_a_concrete_place(self):
        result = assemble_recommendation(
            [],
            novelty_weight=0.9,
            member_count=3,
            when=WHEN,
            duration_hours=3,
            location=LOCATION,
            place_provider=FakePlaces([]),
        )
        for item in result["items"]:
            types = {activity["type"] for activity in item["activities"]}
            if types & {"exhibition", "performance"}:
                self.assertEqual(item["eventSchedule"], "실제 행사 일정 미확인")
        for course in result["courses"]:
            types = {activity["type"] for activity in course["activities"]}
            self.assertFalse(types & {"exhibition", "performance"})

    def test_child_only_places_are_removed_for_an_adult_group(self):
        rows = nearby_rows() + [
            place("kids", "와룡공원 유아숲체험장", 37.5572, 126.9242, ["음식점", "식당"], "관광,명소"),
            place("play", "와룡공원 숲속놀이터", 37.5573, 126.9243, ["카페"], "관광,명소"),
        ]
        result = assemble_recommendation(
            HISTORY,
            novelty_weight=0.05,
            member_count=3,
            when=WHEN,
            duration_hours=3,
            location=LOCATION,
            place_provider=FakePlaces(rows),
        )
        names = [stop["place"]["name"] for course in result["courses"] for stop in course["stops"]]
        self.assertNotIn("와룡공원 유아숲체험장", names)
        self.assertNotIn("와룡공원 숲속놀이터", names)
        self.assertTrue(is_child_only_place({"name": "와룡공원 숲속놀이터", "category": ""}))

    def test_reversed_place_pair_is_one_course(self):
        rows = [
            place("meal-1", "테스트식당", 37.557, 126.924, ["음식점", "식당", "카페"]),
            place("cafe-1", "테스트카페", 37.558, 126.925, ["카페", "디저트 카페", "음식점", "식당"], "카페"),
        ]

        def patterns():
            return [
                {"id": "meal-cafe", "pattern": "식사 후 카페", "activities": ["meal", "cafe"]},
                {"id": "cafe-meal", "pattern": "카페 후 식사", "activities": ["cafe", "meal"]},
            ]

        result = assemble_recommendation(
            HISTORY,
            novelty_weight=0.05,
            member_count=3,
            when=WHEN,
            duration_hours=3,
            location=LOCATION,
            place_provider=FakePlaces(rows),
            pattern_fn=patterns,
        )
        pairs = {
            tuple(sorted(stop["place"]["providerId"] for stop in course["stops"]))
            for course in result["courses"]
        }
        self.assertEqual(len(pairs), len(result["courses"]))
        self.assertEqual(sum(1 for course in result["courses"] if {stop["place"]["providerId"] for stop in course["stops"]} == {"meal-1", "cafe-1"}), 1)

    def test_nearby_same_facility_is_not_a_two_stop_course(self):
        gate = place("gate", "와룡공원 입구", 37.5570, 126.9240, ["공원", "산책로", "카페"])
        trail = place("trail", "와룡공원 산책로", 37.5571, 126.9241, ["공원", "산책로", "카페"])
        cafe = place("cafe-1", "테스트카페", 37.560, 126.928, ["카페", "디저트 카페"], "카페")
        self.assertTrue(same_visit(gate, trail))
        result = assemble_recommendation(
            [],
            novelty_weight=0.9,
            member_count=3,
            when=WHEN,
            duration_hours=3,
            location=LOCATION,
            place_provider=FakePlaces([gate, trail, cafe]),
            pattern_fn=lambda: [{"id": "walk-cafe", "pattern": "산책 후 카페", "activities": ["walk", "cafe"]}],
        )
        for course in result["courses"]:
            ids = {stop["place"]["providerId"] for stop in course["stops"]}
            self.assertNotEqual(ids, {"gate", "trail"})

    def test_nature_is_not_shown_as_an_activity_stop(self):
        result = assemble_recommendation(
            HISTORY,
            novelty_weight=0.05,
            member_count=3,
            when=WHEN,
            duration_hours=3,
            location=LOCATION,
            place_provider=FakePlaces(nearby_rows()),
            pattern_fn=lambda: [
                {"id": "walk-nature", "pattern": "산책 후 자연", "activities": ["walk", "nature"]},
                {"id": "walk-cafe", "pattern": "산책 후 카페", "activities": ["walk", "cafe"]},
            ],
        )
        for course in result["courses"]:
            self.assertNotIn("nature", [activity["type"] for activity in course["activities"]])
            self.assertNotIn("자연", course["arrow"])

    def test_llm_payload_keeps_activity_ids_only(self):
        patterns = patterns_from_payload({
            "experiences": [
                {"activities": ["workshop", "meal"], "name": "없는공방"},
                {"activities": ["bakery", "cafe"]},
                {"activities": ["walk", "bar"]},
            ]
        })
        self.assertEqual(patterns[0]["activities"], ["workshop", "meal"])
        self.assertNotIn("없는공방", str(patterns))
        self.assertEqual(patterns[1]["activities"], ["walk", "bar"])


def course_with(sequence, score, suffix):
    first, second = sequence
    return {
        "id": f"{first}-{second}-{suffix}",
        "scores": {"courseTotal": score},
        "activities": [{"type": first}, {"type": second}],
        "stops": [
            {"activity": first, "place": {"providerId": f"{first}-{suffix}"}},
            {"activity": second, "place": {"providerId": f"{second}-{suffix}"}},
        ],
    }


def scored_pattern(experience_id, activities, total):
    return {
        "experienceId": experience_id,
        "pattern": experience_id,
        "scores": {"total": total},
        "activities": [{"type": activity} for activity in activities],
    }


class CourseDiversityTests(unittest.TestCase):
    def test_top_three_keeps_one_course_per_experience(self):
        shown, alternatives = arrange_courses([
            course_with(("market", "meal"), 1.5, "a"),
            course_with(("market", "meal"), 1.4, "b"),
            course_with(("market", "meal"), 1.3, "c"),
            course_with(("walk", "cafe"), 1.2, "d"),
            course_with(("exhibition", "cafe"), 1.1, "e"),
        ])
        self.assertEqual(
            [course["id"] for course in shown[:3]],
            ["market-meal-a", "walk-cafe-d", "exhibition-cafe-e"],
        )
        self.assertEqual(
            {course["id"] for course in alternatives},
            {"market-meal-b", "market-meal-c"},
        )

    def test_close_scores_prefer_low_activity_overlap(self):
        shown, _alternatives = arrange_courses([
            course_with(("market", "meal"), 1.40, "a"),
            course_with(("shopping", "meal"), 1.38, "b"),
            course_with(("walk", "cafe"), 1.35, "c"),
        ])
        self.assertEqual(shown[0]["id"], "market-meal-a")
        self.assertEqual(shown[1]["id"], "walk-cafe-c")

        dominant, _alternatives = arrange_courses([
            course_with(("market", "meal"), 1.50, "a"),
            course_with(("shopping", "meal"), 1.48, "b"),
            course_with(("walk", "cafe"), 1.10, "c"),
        ])
        self.assertEqual(dominant[1]["id"], "shopping-meal-b")

        picked = patterns_for_retrieval([
            scored_pattern("market-meal", ["market", "meal"], 1.40),
            scored_pattern("shopping-meal", ["shopping", "meal"], 1.38),
            scored_pattern("walk-cafe", ["walk", "cafe"], 1.35),
        ], limit=2)
        self.assertEqual(
            [item["experienceId"] for item in picked],
            ["market-meal", "walk-cafe"],
        )


class PlaceListMarkupTests(unittest.TestCase):
    def test_ordered_list_does_not_repeat_stop_numbers(self):
        source = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(encoding="utf-8")
        match = re.search(r"const places = stops\.map\(\(stop\) => `(<li>.*?</li>)`\)", source)
        self.assertIsNotNone(match)
        template = match.group(1)
        visible = []
        for order, name in ((1, "혜화필리핀마켓"), (2, "노을지다")):
            item = template.replace("${stop.order}", str(order)).replace("${esc(stop.place.name)}", name)
            text = re.sub(r"<[^>]+>", "", item).strip()
            visible.append(f"{order}. {text}")
        self.assertEqual(visible, ["1. 혜화필리핀마켓", "2. 노을지다"])
        self.assertNotIn("1. 1.", visible[0])
        self.assertNotIn("2. 2.", visible[1])
        self.assertIn('<ol class="reasons">', source)
