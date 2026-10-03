"""Group photos into outings by capture time and, when present, distance.

A meal and a cafe a couple of hours apart stay one outing.
Splits are recoverable: the review screen can merge two drafts.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from geo import haversine_m

TIME_SPLIT = timedelta(hours=6)
SPACE_SPLIT_M = 20_000
SPACE_GAP = timedelta(minutes=45)


def public_config() -> dict:
    return {
        "timeSplitHours": TIME_SPLIT.total_seconds() / 3600,
        "distanceSplitKm": SPACE_SPLIT_M / 1000,
        "distanceGapMinutes": SPACE_GAP.total_seconds() / 60,
    }


def split_note(reason: str) -> str:
    hours = TIME_SPLIT.total_seconds() / 3600
    kilometers = SPACE_SPLIT_M / 1000
    minutes = int(SPACE_GAP.total_seconds() / 60)
    if reason == "time":
        return f"이전 사진과 {hours:g}시간 넘게 떨어져 다른 외출로 나눴습니다."
    if reason == "distance":
        return f"이전 사진과 {minutes}분 이상 벌어지고 {kilometers:g}km 넘게 멀어 다른 외출로 나눴습니다."
    if reason == "no_timestamp":
        return "촬영 시각이 없어 다른 사진과 자동으로 합치지 않았습니다."
    return ""


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")


def _has_gps(photo: dict) -> bool:
    return photo.get("lat") is not None and photo.get("lng") is not None


def cluster_photos(photos: list[dict]) -> list[dict]:
    timed = [photo for photo in photos if photo.get("takenAt")]
    untimed = [photo for photo in photos if not photo.get("takenAt")]
    timed.sort(key=lambda photo: (photo["takenAt"], photo.get("id", "")))

    clusters: list[dict] = []
    for photo in timed:
        if not clusters:
            clusters.append({"photo_ids": [photo["id"]], "split_reason": "first", "_photos": [photo]})
            continue
        previous = clusters[-1]["_photos"][-1]
        gap = _parse_time(photo["takenAt"]) - _parse_time(previous["takenAt"])
        reason = None
        if gap > TIME_SPLIT:
            reason = "time"
        elif gap >= SPACE_GAP and _has_gps(photo) and _has_gps(previous):
            distance = haversine_m(previous["lat"], previous["lng"], photo["lat"], photo["lng"])
            if distance >= SPACE_SPLIT_M:
                reason = "distance"
        if reason:
            clusters.append({"photo_ids": [photo["id"]], "split_reason": reason, "_photos": [photo]})
        else:
            clusters[-1]["photo_ids"].append(photo["id"])
            clusters[-1]["_photos"].append(photo)

    for photo in untimed:
        clusters.append(
            {"photo_ids": [photo["id"]], "split_reason": "no_timestamp", "_photos": [photo]}
        )

    for cluster in clusters:
        cluster.pop("_photos", None)
    return clusters
