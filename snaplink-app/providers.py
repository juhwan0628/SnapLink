"""External place and event search.

Scoring code receives the normalized place dict only. Provider payloads stay here.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger("memory.providers")

KAKAO_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"


def places_enabled() -> bool:
    return bool(os.environ.get("KAKAO_REST_API_KEY"))


def _redact(text: str) -> str:
    secret = os.environ.get("KAKAO_REST_API_KEY") or ""
    if secret:
        text = text.replace(secret, "[redacted]")
    return text[:300]


def normalize_place(raw: dict, *, provider: str, retrieved_at: str, origin=None) -> dict | None:
    name = str(raw.get("name") or "").strip()
    provider_id = str(raw.get("provider_id") or raw.get("providerId") or "").strip()
    try:
        lat = float(raw["lat"])
        lng = float(raw["lng"])
    except (KeyError, TypeError, ValueError):
        return None
    if not name or not provider_id:
        return None
    distance = raw.get("distance_m", raw.get("distanceM"))
    try:
        distance_m = int(distance) if distance is not None and distance != "" else None
    except (TypeError, ValueError):
        distance_m = None
    return {
        "provider": provider,
        "providerId": provider_id,
        "name": name[:80],
        "category": str(raw.get("category") or "").strip()[:80],
        "address": str(raw.get("address") or "").strip()[:120],
        "lat": lat,
        "lng": lng,
        "distanceM": distance_m,
        "detailUrl": str(raw.get("detail_url") or raw.get("detailUrl") or "").strip()[:300],
        "retrievedAt": retrieved_at,
    }


def normalize_kakao_document(document: dict, retrieved_at: str) -> dict | None:
    return normalize_place(
        {
            "provider_id": document.get("id"),
            "name": document.get("place_name"),
            "category": document.get("category_name"),
            "address": document.get("road_address_name") or document.get("address_name"),
            "lat": document.get("y"),
            "lng": document.get("x"),
            "distance_m": document.get("distance"),
            "detail_url": document.get("place_url"),
        },
        provider="kakao",
        retrieved_at=retrieved_at,
    )


class PlaceProvider:
    name = "none"

    def enabled(self) -> bool:
        return False

    def resolve_area(self, area_name: str) -> dict | None:
        return None

    def search_places(self, query: str, lat: float, lng: float, radius_m: int) -> list[dict]:
        return []


class KakaoPlaceProvider(PlaceProvider):
    name = "kakao"

    def __init__(self, opener=None):
        self._opener = opener or urllib.request.urlopen

    def enabled(self) -> bool:
        return places_enabled()

    def resolve_area(self, area_name: str) -> dict | None:
        documents = self._keyword(area_name, lat=None, lng=None, radius_m=None, size=1)
        if not documents:
            return None
        place = documents[0]
        return {"lat": place["lat"], "lng": place["lng"], "name": place["name"]}

    def search_places(self, query: str, lat: float, lng: float, radius_m: int) -> list[dict]:
        if not self.enabled():
            raise RuntimeError("place provider disabled")
        return self._keyword(query, lat=lat, lng=lng, radius_m=radius_m, size=5)

    def _keyword(self, query: str, *, lat, lng, radius_m, size: int) -> list[dict]:
        params = {"query": query, "size": size}
        if lat is not None and lng is not None:
            params["y"] = f"{lat:.6f}"
            params["x"] = f"{lng:.6f}"
            params["sort"] = "distance"
        if radius_m:
            params["radius"] = str(min(int(radius_m), 20000))
        url = KAKAO_KEYWORD_URL + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"KakaoAK {os.environ['KAKAO_REST_API_KEY']}"},
        )
        try:
            with self._opener(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"HTTP {exc.code} {_redact(detail)}") from exc
        retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        places = []
        for document in payload.get("documents") or []:
            place = normalize_kakao_document(document, retrieved_at)
            if place:
                places.append(place)
        return places


class EventProvider:
    """Dated culture events. This build does not call a live event API."""

    name = "none"

    def enabled(self) -> bool:
        return False

    def search_events(self, date: str, area: str, category: str) -> list[dict]:
        logger.info(
            "event retrieval provider=%s category=%s result_count=0 reason=not_configured",
            self.name,
            category,
        )
        return []


class UnavailableEventProvider(EventProvider):
    name = "seoul-culture"


def kakao_provider() -> KakaoPlaceProvider:
    return KakaoPlaceProvider()


def event_provider() -> EventProvider:
    return UnavailableEventProvider()
