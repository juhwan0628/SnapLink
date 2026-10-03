"""Turn an activity id into place-search phrases.

The phrases are categories, not business names. A model must not supply the query.
"""

from __future__ import annotations

# Activities that need a dated event, not only a venue that exists on a map.
EVENT_ACTIVITIES = {"exhibition", "performance"}

# Nature is a place context for a walk, not a second activity to retrieve.
ENVIRONMENT_ACTIVITIES = {"nature"}

ACTIVITY_QUERIES: dict[str, list[str]] = {
    "meal": ["음식점", "식당"],
    "cafe": ["카페", "디저트 카페"],
    "exhibition": ["전시", "미술관", "갤러리"],
    "workshop": ["공방", "원데이클래스"],
    "performance": ["공연", "연극", "라이브"],
    "walk": ["공원", "산책로", "한강공원"],
    "activity": ["체험", "액티비티"],
    "market": ["전통시장", "시장"],
    "shopping": ["편집샵", "소품샵", "시장"],
    "nature": ["공원", "숲", "한강"],
    "bar": ["바", "와인바", "펍"],
}


def queries_for(activity: str) -> list[str]:
    return list(ACTIVITY_QUERIES.get(activity, []))


def needs_event_schedule(activity: str) -> bool:
    return activity in EVENT_ACTIVITIES


def is_environment(activity: str) -> bool:
    return activity in ENVIRONMENT_ACTIVITIES


def is_place_activity(activity: str) -> bool:
    return bool(activity) and not needs_event_schedule(activity) and not is_environment(activity)
