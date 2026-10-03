"""Turn extracted photo metadata into draft outings. Does not write the database."""

from __future__ import annotations

import uuid

from cluster import cluster_photos
from domain import normalize_activities, normalize_tags
from geo import centroid


def build_drafts(photos: list[dict], infer_fn, geocode_fn) -> list[dict]:
    by_id = {photo["id"]: photo for photo in photos}
    drafts = []
    for cluster in cluster_photos(photos):
        members = [by_id[photo_id] for photo_id in cluster["photo_ids"]]
        try:
            inference = infer_fn(members) or {}
        except Exception:
            inference = {
                "activities": [],
                "tags": [],
                "placeHint": None,
                "source": "none",
                "note": "활동 추정에 실패해 비워 두었습니다.",
            }
        times = sorted(photo["takenAt"] for photo in members if photo.get("takenAt"))
        points = [
            (photo["lat"], photo["lng"])
            for photo in members
            if photo.get("lat") is not None and photo.get("lng") is not None
        ]
        center = centroid(points)
        area_name = ""
        area_source = "none"
        precision = "unknown"
        lat = lng = None
        if center:
            lat, lng = center
            name = None
            if geocode_fn:
                try:
                    name = geocode_fn(lat, lng)
                except Exception:
                    name = None
            if name:
                area_name = str(name).strip()[:40]
                area_source = "gps"
                precision = "area"
        hint = inference.get("placeHint")
        places = []
        if hint and str(hint).strip():
            places.append({
                "name": str(hint).strip()[:40],
                "lat": None,
                "lng": None,
                "precision": "hint",
            })
        drafts.append({
            "id": str(uuid.uuid4()),
            "startTime": times[0] if times else None,
            "endTime": times[-1] if times else None,
            "areaName": area_name,
            "areaLat": lat,
            "areaLng": lng,
            "areaPrecision": precision,
            "areaSource": area_source,
            "tags": normalize_tags(inference.get("tags")),
            "inferenceSource": inference.get("source") or "none",
            "inferenceNote": str(inference.get("note") or "")[:300],
            "splitReason": cluster["split_reason"],
            "activities": normalize_activities(inference.get("activities")),
            "places": places,
            "photos": members,
        })
    return drafts
