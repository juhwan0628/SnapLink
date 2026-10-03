"""Neighborhood name from a coordinate. This is not a venue lookup."""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request

_lock = threading.Lock()
_last_call = 0.0
_AREA_KEYS = (
    "suburb",
    "neighbourhood",
    "quarter",
    "borough",
    "city_district",
    "town",
    "village",
    "city",
)


def nominatim_area(lat: float, lng: float) -> str | None:
    """Return a neighborhood name, '' if the response has none, or None on failure."""
    global _last_call
    query = urllib.parse.urlencode({
        "format": "jsonv2",
        "lat": f"{lat:.6f}",
        "lon": f"{lng:.6f}",
        "zoom": "14",
        "accept-language": "ko",
    })
    request = urllib.request.Request(
        f"https://nominatim.openstreetmap.org/reverse?{query}",
        headers={"User-Agent": "group-experience-memory/0.1 (local study app)"},
    )
    with _lock:
        wait = 1.05 - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            _last_call = time.time()
            return None
        _last_call = time.time()
    address = payload.get("address") or {}
    for key in _AREA_KEYS:
        value = address.get(key)
        if value:
            return str(value).strip()[:40]
    return ""
