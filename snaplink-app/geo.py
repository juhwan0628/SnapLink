"""Distance helpers. Coordinates are WGS84 degrees."""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_000


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = radians(lat2 - lat1)
    d_lng = radians(lng2 - lng1)
    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lng / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def route_distance_m(lat1: float, lng1: float, lat2: float, lng2: float, router=None) -> dict:
    """Straight-line distance until a routing function is passed in."""
    if router is not None:
        routed = router(lat1, lng1, lat2, lng2)
        routed.setdefault("source", "router")
        return routed
    return {
        "meters": round(haversine_m(lat1, lng1, lat2, lng2)),
        "source": "haversine",
        "durationMinutes": None,
    }


def centroid(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    if not points:
        return None
    lat = sum(point[0] for point in points) / len(points)
    lng = sum(point[1] for point in points) / len(points)
    return lat, lng
