"""Activity vocabulary and experience patterns shared by storage, inference, and ranking."""

from __future__ import annotations

ACTIVITIES: list[tuple[str, str]] = [
    ("meal", "식사"),
    ("cafe", "카페"),
    ("exhibition", "전시"),
    ("workshop", "공방"),
    ("performance", "공연"),
    ("walk", "산책"),
    ("activity", "액티비티"),
    ("market", "시장"),
    ("shopping", "쇼핑"),
    ("nature", "자연"),
    ("bar", "술"),
]

ACTIVITY_LABELS = {key: label for key, label in ACTIVITIES}
ACTIVITY_ORDER = {key: index for index, (key, _label) in enumerate(ACTIVITIES)}

# What a person can confirm. Market is a place type and nature is an environment.
USER_ACTIVITIES: list[tuple[str, str]] = [
    ("meal", "식사"),
    ("cafe", "카페"),
    ("exhibition", "전시"),
    ("workshop", "공방"),
    ("performance", "공연"),
    ("walk", "산책"),
    ("shopping", "쇼핑"),
    ("activity", "액티비티"),
    ("bar", "술"),
]
USER_ACTIVITY_IDS = {key for key, _label in USER_ACTIVITIES}

TAGS = ["조용함", "북적임", "실내", "야외", "밤", "여유"]

# Hard-coded MVP candidate set for Group Experience Memory and novelty ranking.
# These patterns are not a live venue catalog and are not final place recommendations.
EXPERIENCES: list[dict] = [
    {"id": "meal-cafe", "pattern": "식사 후 카페", "activities": ["meal", "cafe"]},
    {"id": "walk-cafe", "pattern": "산책 후 카페", "activities": ["walk", "cafe"]},
    {"id": "exhibition-cafe", "pattern": "전시 후 카페", "activities": ["exhibition", "cafe"]},
    {"id": "workshop-performance", "pattern": "공방 후 소규모 공연", "activities": ["workshop", "performance"]},
    {"id": "workshop-cafe", "pattern": "공방 후 카페", "activities": ["workshop", "cafe"]},
    {"id": "market-meal", "pattern": "시장을 보고 식사", "activities": ["market", "meal"]},
    {"id": "performance-meal", "pattern": "공연 후 식사", "activities": ["performance", "meal"]},
    {"id": "activity-meal", "pattern": "액티비티 후 식사", "activities": ["activity", "meal"]},
    {"id": "exhibition-walk", "pattern": "전시와 동네 산책", "activities": ["exhibition", "walk"]},
    {"id": "nature-walk", "pattern": "자연에서 걷기", "activities": ["nature", "walk"]},
    {"id": "nature-meal", "pattern": "야외에서 보내고 식사", "activities": ["nature", "meal"]},
    {"id": "walk-bar", "pattern": "산책 후 한잔", "activities": ["walk", "bar"]},
    {"id": "performance-bar", "pattern": "공연 후 한잔", "activities": ["performance", "bar"]},
    {"id": "activity-cafe", "pattern": "액티비티 후 카페", "activities": ["activity", "cafe"]},
]

UNKNOWN_ACTIVITY = "unknown"

CANDIDATE_SET_PURPOSE = (
    "이 목록은 코드에 고정된 MVP candidate set입니다. "
    "Group Experience Memory와 novelty ranking을 검증하기 위한 후보 집합이며, "
    "최종 실제 장소 추천이 아닙니다."
)


def candidate_set_info() -> dict:
    return {
        "kind": "mvp-hardcoded",
        "count": len(EXPERIENCES),
        "label": "MVP candidate set",
        "purpose": CANDIDATE_SET_PURPOSE,
    }


def activity_label(activity_id: str) -> str:
    return ACTIVITY_LABELS.get(activity_id, activity_id)


def sort_activity_ids(activity_ids: list[str]) -> list[str]:
    unique: list[str] = []
    for activity_id in activity_ids:
        if activity_id in ACTIVITY_LABELS and activity_id not in unique:
            unique.append(activity_id)
    return sorted(unique, key=lambda item: ACTIVITY_ORDER[item])


def arrow_labels(activity_ids: list[str]) -> str:
    return " → ".join(activity_label(item) for item in sort_activity_ids(activity_ids))


def normalize_activities(items) -> list[dict]:
    cleaned = []
    seen = set()
    for item in items or []:
        if isinstance(item, str):
            activity_id = item
            confidence = 1.0
            source = "user"
        elif isinstance(item, dict):
            activity_id = item.get("type")
            confidence = item.get("confidence", 0.5)
            source = item.get("source") or "vlm"
        else:
            continue
        if activity_id == UNKNOWN_ACTIVITY:
            if source == "none" and UNKNOWN_ACTIVITY not in seen:
                seen.add(UNKNOWN_ACTIVITY)
                cleaned.append({"type": UNKNOWN_ACTIVITY, "confidence": 0.0, "source": "none"})
            continue
        if activity_id not in ACTIVITY_LABELS or activity_id in seen:
            continue
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.5
        if 1 < confidence <= 100:
            confidence /= 100
        confidence = max(0.0, min(1.0, confidence))
        if source not in ("time", "vlm", "user"):
            source = "vlm"
        seen.add(activity_id)
        cleaned.append({"type": activity_id, "confidence": confidence, "source": source})
    real = [item for item in cleaned if item["type"] != UNKNOWN_ACTIVITY]
    if real:
        real.sort(key=lambda item: ACTIVITY_ORDER[item["type"]])
        return real
    return [item for item in cleaned if item["type"] == UNKNOWN_ACTIVITY]


def normalize_tags(tags) -> list[str]:
    cleaned = []
    for tag in tags or []:
        text = str(tag).strip()
        if text in TAGS and text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= 8:
            break
    return cleaned


def activity_catalog() -> list[dict]:
    return [{"id": key, "label": label} for key, label in ACTIVITIES]


def user_activity_catalog() -> list[dict]:
    return [{"id": key, "label": label} for key, label in USER_ACTIVITIES]
