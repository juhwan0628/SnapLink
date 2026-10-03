"""Ground ranked experience patterns in retrieved places.

The memory score stays the one from recommend.py. Distance and data completeness
are added only after a place has coordinates from a provider.
"""

from __future__ import annotations

import logging

import validation

from domain import activity_label
from geo import haversine_m, route_distance_m
from queries import is_environment, is_place_activity, needs_event_schedule, queries_for
from recommend import best_experience_score, is_positive_preference, recommend_for_group

logger = logging.getLogger("memory.courses")

TOP_PLACES = 5
MAX_PAIRS = 25
MAX_COURSES = 5
MAX_PATTERNS = 4
SAME_VISIT_M = 250
CHILD_ONLY_TERMS = ("유아", "어린이", "키즈", "놀이시설", "놀이터", "어린이집")
COURSE_MAX_LEG_M = 3000
W_DISTANCE = 0.2
W_COMPLETE = 0.1
# Small enough that a clearly higher score stays ahead, large enough that a
# close score loses to a course with little activity overlap.
DIVERSITY_WEIGHT = 0.2
REQUIRED_FIELDS = ("name", "address", "lat", "lng", "provider", "providerId")


def parse_search_location(data: dict) -> dict | None:
    mode = str(data.get("locationMode") or "").strip()
    area_name = str(data.get("areaName") or "").strip()
    lat_raw = str(data.get("latitude") or "").strip()
    lng_raw = str(data.get("longitude") or "").strip()
    radius_raw = str(data.get("searchRadiusKm") or "").strip()
    if not mode and not area_name and not lat_raw and not lng_raw:
        return None
    if mode not in ("area", "coordinates"):
        raise ValueError("위치 방식은 area 또는 coordinates 여야 합니다.")
    if not radius_raw:
        raise ValueError("장소 검색에는 반경(searchRadiusKm)이 필요합니다.")
    try:
        radius_km = float(radius_raw)
    except ValueError:
        raise ValueError("검색 반경은 숫자여야 합니다.")
    if radius_km < 0.5 or radius_km > 20:
        raise ValueError("검색 반경은 0.5km 이상 20km 이하여야 합니다.")
    if mode == "area":
        if not area_name:
            raise ValueError("지역 검색에는 지역 이름이 필요합니다. 위치를 임의로 정하지 않습니다.")
        return {
            "locationMode": "area",
            "areaName": area_name[:40],
            "latitude": None,
            "longitude": None,
            "searchRadiusKm": radius_km,
        }
    if not lat_raw or not lng_raw:
        raise ValueError("좌표 검색에는 latitude와 longitude가 필요합니다.")
    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except ValueError:
        raise ValueError("위도와 경도는 숫자여야 합니다.")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("위도 또는 경도 범위가 아닙니다.")
    return {
        "locationMode": "coordinates",
        "areaName": "",
        "latitude": lat,
        "longitude": lng,
        "searchRadiusKm": radius_km,
    }


def assemble_recommendation(
    history,
    *,
    novelty_weight: float,
    member_count: int,
    when,
    duration_hours: float,
    location: dict | None,
    place_provider,
    event_provider=None,
    explain_fn=None,
    pattern_fn=None,
) -> dict:
    ranked = recommend_for_group(
        history,
        novelty_weight=novelty_weight,
        member_count=member_count,
        when=when,
        duration_hours=duration_hours,
        limit=14,
    )
    result = dict(ranked)
    result["items"] = []
    for item in ranked["items"][:5]:
        shown = dict(item)
        types = [activity["type"] for activity in shown["activities"]]
        if any(needs_event_schedule(activity) for activity in types):
            shown["eventSchedule"] = "실제 행사 일정 미확인"
        result["items"] = result["items"] + [shown]
    result["courses"] = []
    result["unconfirmedEvents"] = []
    result["grounding"] = {"places": "skipped", "reason": "위치를 주지 않아 실제 장소는 검색하지 않았습니다."}
    result["candidateGeneration"] = {"source": "static", "count": ranked["candidateSet"]["count"]}
    if location is None:
        return result
    if place_provider is None or not place_provider.enabled():
        result["grounding"] = {
            "places": "fallback",
            "reason": "KAKAO_REST_API_KEY가 없어 경험 패턴만 추천합니다.",
        }
        return result

    try:
        center = _center(location, place_provider)
    except Exception as exc:
        logger.info("retrieval failure provider=%s query=%s reason=%s", getattr(place_provider, "name", "place"), location.get("areaName") or "coordinates", _clip(exc))
        result["grounding"] = {"places": "fallback", "reason": "장소 검색에 실패해 경험 패턴만 추천합니다."}
        return result
    if center is None:
        result["grounding"] = {"places": "fallback", "reason": "검색 중심 좌표를 찾지 못했습니다. 지역을 임의로 정하지 않습니다."}
        return result

    radius_m = int(location["searchRadiusKm"] * 1000)
    pool, generation = _pattern_pool(
        ranked["items"],
        pattern_fn,
        history,
        novelty_weight=novelty_weight,
        member_count=member_count,
        when=when,
        duration_hours=duration_hours,
    )
    result["candidateGeneration"] = generation
    place_patterns = patterns_for_retrieval(pool, MAX_PATTERNS)
    event_patterns = [
        item for item in result["items"]
        if item.get("eventSchedule")
    ]
    courses = []
    stats = {"failures": 0, "results": 0}
    for pattern in place_patterns:
        before = len(courses)
        try:
            courses.extend(_courses_for_pattern(pattern, history, center, radius_m, place_provider, stats))
        except Exception as exc:
            logger.info(
                "retrieval failure provider=%s query=%s reason=%s",
                getattr(place_provider, "name", "place"),
                pattern["pattern"],
                _clip(exc),
            )
            result["grounding"] = {"places": "fallback", "reason": "장소 검색에 실패해 경험 패턴만 추천합니다."}
            result["courses"] = []
            return result
        logger.info(
            "course filter pattern=%s before=%s after=%s",
            pattern["experienceId"],
            before,
            len(courses),
        )
    shown, alternatives = arrange_courses(courses)
    shown = validation.validate_courses(shown, center, when, duration_hours)
    for course in shown:
        if course["validation"]["route"] == "verified":
            for evidence, stop in zip(course["realWorldEvidence"], course["stops"]):
                evidence["distanceM"] = stop["distanceFromPreviousM"]
                evidence["hours"] = "정기 영업시간 기준" if course["validation"]["hours"] == "verified" else "미확인"
    for course in shown:
        course["memoryEvidence"] = memory_evidence(
            [activity["type"] for activity in course["activities"]],
            history,
        )
        course["explanation"] = _explanation(course, explain_fn)
    for course in alternatives:
        course["memoryEvidence"] = memory_evidence(
            [activity["type"] for activity in course["activities"]],
            history,
        )
    result["courses"] = shown
    result["courseAlternatives"] = alternatives
    result["unconfirmedEvents"] = [
        {
            "experienceId": item["experienceId"],
            "pattern": item["pattern"],
            "arrow": item["arrow"],
            "eventSchedule": "실제 행사 일정 미확인",
        }
        for item in event_patterns
    ]
    result["grounding"] = {
        "places": (getattr(place_provider, "name", "place") if shown else "empty"),
        "reason": (
            "" if shown
            else "장소 검색에 실패해 경험 패턴만 추천합니다."
            if stats["failures"] and stats["results"] == 0
            else "방문 조건에 맞는 두 장소 코스를 찾지 못했습니다."
        ),
        "center": center,
        "searchRadiusKm": location["searchRadiusKm"],
        "distanceSource": "haversine",
    }
    if event_provider is not None:
        result["grounding"]["events"] = "unavailable" if not event_provider.enabled() else event_provider.name
    return result


def memory_evidence(activity_ids: list[str], history: list[dict]) -> list[str]:
    """At most two sentences grounded in saved outings. No score language."""
    activities = [activity for activity in activity_ids if not is_environment(activity)]
    if not history or not activities:
        return []
    lines = []
    fresh = [activity for activity in activities if not _seen(activity, history)]
    known = [activity for activity in activities if _seen(activity, history)]
    if fresh and known:
        lines.append(
            f"이 그룹은 아직 '{activity_label(fresh[0])}' 기록이 없고, "
            f"'{activity_label(known[0])}'은 이전에 했던 활동이에요."
        )
    elif fresh:
        lines.append(f"이 그룹은 아직 '{activity_label(fresh[0])}'을 함께 한 기록이 없어요.")
    for activity in known:
        if any(
            activity in (item.get("activities") or []) and is_positive_preference(item)
            for item in history
        ) and len(lines) < 2:
            lines.append(f"'{activity_label(activity)}'은 좋았던 경험으로 남아 있어요.")
            break
    recent = sorted(history, key=lambda item: item.get("startTime") or "", reverse=True)[:2]
    if len(lines) < 2 and len(recent) == 2:
        repeated = [
            activity for activity in activities
            if activity in (recent[0].get("activities") or []) and activity in (recent[1].get("activities") or [])
        ]
        if repeated:
            lines.append(f"최근 외출에도 '{activity_label(repeated[0])}'이 들어 있어요.")
    return lines[:2]


def is_child_only_place(place: dict) -> bool:
    text = f"{place.get('name') or ''} {place.get('category') or ''}"
    return any(term in text for term in CHILD_ONLY_TERMS)


def same_visit(left: dict, right: dict) -> bool:
    if left.get("provider") == right.get("provider") and left.get("providerId") and left.get("providerId") == right.get("providerId"):
        return True
    address_left = _compact(left.get("address"))
    address_right = _compact(right.get("address"))
    if address_left and address_left == address_right:
        return True
    if left.get("lat") is None or right.get("lat") is None:
        return False
    meters = haversine_m(left["lat"], left["lng"], right["lat"], right["lng"])
    return meters <= SAME_VISIT_M and _names_overlap(left.get("name") or "", right.get("name") or "")


def course_pair_key(course: dict) -> tuple:
    ids = tuple(sorted(
        stop["place"]["providerId"] for stop in course.get("stops") or [] if stop.get("place")
    ))
    return ids


def map_markers(course: dict) -> list[dict]:
    markers = []
    for stop in course.get("stops") or []:
        place = stop.get("place") or {}
        if place.get("lat") is None or place.get("lng") is None:
            continue
        markers.append({
            "courseId": course.get("id"),
            "order": stop["order"],
            "name": place.get("name") or "",
            "category": place.get("category") or stop.get("activityLabel") or "",
            "address": place.get("address") or "",
            "lat": place["lat"],
            "lng": place["lng"],
        })
    return markers


def _pattern_pool(ranked_items, pattern_fn, history, *, novelty_weight, member_count, when, duration_hours):
    if pattern_fn is None:
        return ranked_items, {"source": "static", "count": len(ranked_items)}
    try:
        proposed = pattern_fn() or []
    except Exception as exc:
        logger.info("candidate generation fallback reason=%s", _clip(exc))
        return ranked_items, {"source": "static", "count": len(ranked_items)}
    scored = []
    for exp in proposed:
        if not exp.get("activities"):
            continue
        scored.append(best_experience_score(
            exp,
            history,
            novelty_weight=novelty_weight,
            member_count=member_count,
            when=when,
            duration_hours=duration_hours,
        ))
    placeable = [
        item for item in scored
        if _concrete_pattern(item)
    ]
    if not placeable:
        logger.info("candidate generation fallback reason=empty")
        return ranked_items, {"source": "static", "count": len(ranked_items)}
    scored.sort(key=lambda item: -item["scores"]["total"])
    return scored, {"source": "llm", "count": len(scored)}


def _center(location: dict, provider) -> dict | None:
    if location["locationMode"] == "coordinates":
        return {
            "lat": location["latitude"],
            "lng": location["longitude"],
            "name": "현재 위치",
            "source": "user",
        }
    resolved = provider.resolve_area(location["areaName"])
    if not resolved:
        logger.info(
            "retrieval provider=%s query=%s result_count=0 reason=area_not_found",
            getattr(provider, "name", "place"),
            location["areaName"],
        )
        return None
    logger.info(
        "retrieval provider=%s query=%s result_count=1",
        getattr(provider, "name", "place"),
        location["areaName"],
    )
    return {
        "lat": resolved["lat"],
        "lng": resolved["lng"],
        "name": resolved["name"],
        "source": getattr(provider, "name", "place"),
    }


def _courses_for_pattern(pattern: dict, history, center: dict, radius_m: int, provider, stats) -> list[dict]:
    activities = [activity["type"] for activity in pattern["activities"]]
    if len(activities) != 2:
        return []
    first = _retrieve(provider, activities[0], center["lat"], center["lng"], radius_m, stats)
    pairs = []
    for origin_place in first:
        if len(pairs) >= MAX_PAIRS:
            break
        nearby_radius = min(radius_m, COURSE_MAX_LEG_M)
        second = _retrieve(
            provider,
            activities[1],
            origin_place["lat"],
            origin_place["lng"],
            nearby_radius,
            stats,
        )
        for place in second:
            if len(pairs) >= MAX_PAIRS:
                break
            if same_visit(origin_place, place):
                continue
            leg = route_distance_m(origin_place["lat"], origin_place["lng"], place["lat"], place["lng"])
            from_user = haversine_m(center["lat"], center["lng"], place["lat"], place["lng"])
            if leg["meters"] > COURSE_MAX_LEG_M or from_user > radius_m:
                continue
            pairs.append((origin_place, place, leg))
    logger.info(
        "course pairs pattern=%s before=%s after=%s",
        pattern["experienceId"],
        len(first),
        len(pairs),
    )
    courses = []
    for first_place, second_place, leg in pairs:
        courses.append(_course(pattern, history, center, radius_m, first_place, second_place, leg, activities))
    return courses


def _retrieve(provider, activity: str, lat: float, lng: float, radius_m: int, stats: dict) -> list[dict]:
    found = []
    seen = set()
    before = 0
    for query in queries_for(activity):
        try:
            rows = provider.search_places(query, lat, lng, radius_m)
        except Exception as exc:
            logger.info(
                "retrieval failure provider=%s query=%s reason=%s",
                getattr(provider, "name", "place"),
                query,
                _clip(exc),
            )
            stats["failures"] += 1
            continue
        stats["results"] += len(rows)
        before += len(rows)
        logger.info(
            "retrieval provider=%s query=%s result_count=%s",
            getattr(provider, "name", "place"),
            query,
            len(rows),
        )
        for place in rows:
            key = (place.get("provider"), place.get("providerId"))
            if not key[1] or key in seen:
                continue
            if place.get("lat") is None or place.get("lng") is None or not place.get("name"):
                continue
            meters = round(haversine_m(lat, lng, place["lat"], place["lng"]))
            if meters > radius_m:
                continue
            if is_child_only_place(place):
                logger.info("candidate skipped reason=child_only name=%s", place.get("name"))
                continue
            seen.add(key)
            kept = dict(place)
            kept["distanceM"] = meters
            found.append(kept)
            if len(found) >= TOP_PLACES:
                logger.info("candidate filter activity=%s before=%s after=%s", activity, before, len(found))
                return found
    logger.info("candidate filter activity=%s before=%s after=%s", activity, before, len(found))
    return found


def _course(pattern, history, center, radius_m, first, second, leg, activities) -> dict:
    first_leg = route_distance_m(center["lat"], center["lng"], first["lat"], first["lng"])
    distance_fit = _distance_fit(first_leg["meters"], leg["meters"], radius_m)
    completeness = (_completeness(first) + _completeness(second)) / 2
    memory = pattern["scores"]
    scores = dict(memory)
    scores["distance"] = round(W_DISTANCE * distance_fit, 3)
    scores["completeness"] = round(W_COMPLETE * completeness, 3)
    scores["schedule"] = None
    scores["courseTotal"] = round(memory["total"] + scores["distance"] + scores["completeness"], 3)
    stops = [
        _stop(1, activities[0], first, first_leg, center),
        _stop(2, activities[1], second, leg, first),
    ]
    return {
        "id": f"{pattern['experienceId']}:{first['providerId']}:{second['providerId']}",
        "experienceId": pattern["experienceId"],
        "pattern": pattern["pattern"],
        "arrow": pattern["arrow"],
        "activities": pattern["activities"],
        "mode": pattern["mode"],
        "modeLabel": pattern["modeLabel"],
        "scores": scores,
        "memoryReasons": pattern["reasons"],
        "stops": stops,
        "schedule": "미확인",
        "distanceSource": "haversine",
        "realWorldEvidence": [_evidence(stop) for stop in stops],
    }


def _stop(order: int, activity: str, place: dict, leg: dict, previous: dict) -> dict:
    return {
        "order": order,
        "activity": activity,
        "activityLabel": activity_label(activity),
        "place": {
            "provider": place["provider"],
            "providerId": place["providerId"],
            "name": place["name"],
            "category": place.get("category") or "",
            "address": place.get("address") or "",
            "lat": place["lat"],
            "lng": place["lng"],
            "distanceM": leg["meters"],
            "detailUrl": place.get("detailUrl") or "",
            "retrievedAt": place.get("retrievedAt") or "",
        },
        "distanceFromPreviousM": leg["meters"],
        "distanceSource": leg["source"],
        "previousName": previous.get("name") or "기준 위치",
    }


def _evidence(stop: dict) -> dict:
    place = stop["place"]
    return {
        "order": stop["order"],
        "name": place["name"],
        "address": place["address"] or "주소 정보 없음",
        "distanceM": place["distanceM"],
        "distanceFrom": stop["previousName"],
        "provider": place["provider"],
        "retrievedAt": place["retrievedAt"] or "정보 없음",
        "detailUrl": place["detailUrl"],
        "hours": "미확인",
        "rating": None,
    }


def _distance_fit(origin_m: float, leg_m: float, radius_m: int) -> float:
    origin_part = max(0.0, 1 - (origin_m / radius_m if radius_m else 1))
    leg_part = max(0.0, 1 - (leg_m / COURSE_MAX_LEG_M))
    return (origin_part + leg_part) / 2


def _completeness(place: dict) -> float:
    present = 0
    for field in REQUIRED_FIELDS:
        value = place.get(field)
        if value is not None and str(value).strip():
            present += 1
    return present / len(REQUIRED_FIELDS)


def activity_sequence(item: dict) -> tuple:
    stops = item.get("stops") or []
    if stops and all(stop.get("activity") for stop in stops):
        return tuple(stop["activity"] for stop in stops)
    activities = item.get("activities") or []
    if activities and isinstance(activities[0], dict):
        return tuple(activity["type"] for activity in activities)
    return tuple(activities)


def activity_jaccard(left, right) -> float:
    union = set(left) | set(right)
    if not union:
        return 0.0
    return len(set(left) & set(right)) / len(union)


def overlap_penalty(sequence, selected: list) -> float:
    if not selected:
        return 0.0
    return DIVERSITY_WEIGHT * max(activity_jaccard(sequence, other) for other in selected)


def select_diverse(items: list[dict], score_of, limit: int) -> list[dict]:
    """Greedy rerank. The raw score stays; overlap only breaks close calls."""
    remaining = list(items)
    selected = []
    while remaining and len(selected) < limit:
        selected_acts = [activity_sequence(item) for item in selected]
        best_index = 0
        best_key = None
        for index, item in enumerate(remaining):
            adjusted = score_of(item) - overlap_penalty(activity_sequence(item), selected_acts)
            key = (round(adjusted, 6), score_of(item), -index)
            if best_key is None or key > best_key:
                best_key = key
                best_index = index
        selected.append(remaining.pop(best_index))
    return selected


def patterns_for_retrieval(pool: list[dict], limit: int = MAX_PATTERNS) -> list[dict]:
    """Pick different experiences before searching places for each one."""
    best = {}
    for item in pool:
        if not _concrete_pattern(item):
            continue
        key = activity_sequence(item)
        current = best.get(key)
        if current is None or item["scores"]["total"] > current["scores"]["total"]:
            best[key] = item
    return select_diverse(list(best.values()), lambda item: item["scores"]["total"], limit)


def arrange_courses(courses: list[dict], limit: int = MAX_COURSES) -> tuple[list[dict], list[dict]]:
    """One concrete course per activity sequence for the user list.

    Other place pairs for the same experience stay in the second list.
    """
    ordered = sorted(courses, key=lambda item: -item["scores"]["courseTotal"])
    unique = []
    seen_pairs = set()
    for course in ordered:
        pair = course_pair_key(course)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        unique.append(course)
    groups: dict[tuple, list] = {}
    for course in unique:
        groups.setdefault(activity_sequence(course), []).append(course)
    representatives = [group[0] for group in groups.values()]
    shown = select_diverse(representatives, lambda item: item["scores"]["courseTotal"], limit)
    shown_ids = {course["id"] for course in shown}
    alternatives = [course for course in unique if course["id"] not in shown_ids]
    return shown, alternatives


def _concrete_pattern(item: dict) -> bool:
    activities = [activity["type"] for activity in item.get("activities") or []]
    return len(activities) == 2 and all(is_place_activity(activity) for activity in activities)


def _seen(activity: str, history: list[dict]) -> bool:
    return any(activity in (item.get("activities") or []) for item in history)


def _compact(value) -> str:
    return "".join(str(value or "").split())


def _names_overlap(left: str, right: str) -> bool:
    compact_left = _compact(left)
    compact_right = _compact(right)
    if len(compact_left) < 2 or len(compact_right) < 2:
        return False
    if compact_left in compact_right or compact_right in compact_left:
        return True
    shared = 0
    for char_left, char_right in zip(compact_left, compact_right):
        if char_left != char_right:
            break
        shared += 1
    return shared >= 4


def _explanation(course: dict, explain_fn) -> str | None:
    if explain_fn is None:
        return None
    try:
        text = explain_fn({
            "pattern": course["pattern"],
            "memoryEvidence": course["memoryEvidence"],
            "realWorldEvidence": course["realWorldEvidence"],
        })
    except Exception as exc:
        logger.info("explanation failed reason=%s", _clip(exc))
        return None
    if not text or not isinstance(text, str):
        return None
    cleaned = " ".join(text.split())
    if any(token in cleaned for token in ("별점", "영업", "인기 맛집", "아마", "열려 있을")):
        logger.info("explanation dropped reason=ungrounded")
        return None
    return cleaned[:300]


def _clip(exc) -> str:
    return str(exc).replace("\n", " ")[:240]
