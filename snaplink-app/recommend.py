"""Rank experience patterns for one group.

The slider is not "newer is always better".
Toward familiar, preference dominates and repetition is a light penalty.
Toward novel, unseen activity combinations rise and recent repeats fall.
The same meal-then-cafe in another neighborhood stays a familiar combo.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from domain import EXPERIENCES, activity_label, arrow_labels, candidate_set_info

W_CONTEXT = 0.35
W_GROUP = 0.25
REP_BASE = 0.05
REP_SLOPE = 0.85

MODE_LABELS = {
    "no_history": "기록 없음",
    "known_area_new_combo": "알던 지역 · 새 조합",
    "new_area_new_combo": "새 지역 · 새 조합",
    "known_area_familiar_combo": "조합은 익숙함",
    "new_area_familiar_combo": "지역만 생소함",
    "repeat": "해본 경험",
}
NOVEL_MODES = {"known_area_new_combo", "new_area_new_combo"}

WEIGHT_NOTE = (
    "익숙한 쪽에 두면 좋았다고 표시한 활동을 크게 보고 반복 감점은 약합니다. "
    "새로운 쪽에 두면 아직 없는 활동 조합이 커지고, 최근과 같은 조합은 더 깎입니다. "
    "새롭다고 항상 위로 올라가지는 않습니다. "
    "다른 동네에서 같은 식사 후 카페를 하는 것은 새 경험으로 크게 치지 않습니다. "
    "실제 목적지가 없는 이 단계에서는 이동 비용을 점수에 넣지 않습니다."
)


def formula_text() -> str:
    return (
        "상대 점수 = 선호×(1−새로움 비중) + 경험의 새로움×새로움 비중"
        f" + 상황×{W_CONTEXT:.2f} + 그룹×{W_GROUP:.2f}"
        f" − 반복×({REP_BASE:.2f}+{REP_SLOPE:.2f}×새로움 비중)"
        " (이동 비용 제외)"
    )


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _norm(activity_ids) -> tuple:
    return tuple(sorted(activity_ids or []))


def candidate_mode(activity_ids, area_name, area_is_known, history) -> str:
    combo = _norm(activity_ids)
    past_combos = {_norm(item.get("activities")) for item in history if item.get("activities")}
    combo_done = combo in past_combos
    if not history:
        return "no_history"
    if not area_is_known:
        return "new_area_familiar_combo" if combo_done else "new_area_new_combo"
    done_here = any(
        _norm(item.get("activities")) == combo and (item.get("areaName") or "").strip() == area_name
        for item in history
    )
    if done_here:
        return "repeat"
    if combo_done:
        return "known_area_familiar_combo"
    return "known_area_new_combo"


def _headline(pattern: str, area_name: str | None, mode: str) -> str:
    if mode == "known_area_new_combo":
        return f"{area_name}에서 아직 함께 안 해본 {pattern}"
    if mode == "repeat":
        return f"익숙한 경험 · {area_name}에서 {pattern}"
    if mode == "known_area_familiar_combo":
        return f"{area_name}에서 장소만 바뀌는 {pattern}"
    if mode == "new_area_familiar_combo":
        return f"새로운 지역이지만 조합은 익숙합니다 · {pattern}"
    if mode == "new_area_new_combo":
        return f"새로운 지역에서 아직 안 해본 {pattern}"
    return f"{pattern} · 기록을 쌓으면 이 그룹에 맞춰 다시 정렬됩니다"


def _recent_areas(history: list[dict]) -> list[str]:
    ordered = sorted(history, key=lambda item: item.get("startTime") or "", reverse=True)
    names: list[str] = []
    for item in ordered:
        name = (item.get("areaName") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _context_fit(activity_ids: list[str], when: datetime, duration_hours: float) -> float:
    score = 0.5
    steps = max(1, int(round(duration_hours * 2)))
    moments = [when + timedelta(minutes=30 * index) for index in range(steps)]
    hours = [moment.hour for moment in moments]
    weekend = any(moment.weekday() >= 5 for moment in moments)
    acts = set(activity_ids)

    def hits(start: int, end: int) -> bool:
        return any(start <= hour <= end for hour in hours)

    if "meal" in acts and (hits(11, 14) or hits(17, 21)):
        score += 0.15
    if "cafe" in acts and hits(13, 17):
        score += 0.08
    if "bar" in acts and hours and all(hour < 16 for hour in hours):
        score -= 0.2
    if "performance" in acts and weekend:
        score += 0.12
    if "nature" in acts and weekend:
        score += 0.15
    if "exhibition" in acts and weekend:
        score += 0.08
    expected = 1.6 * max(1, len(activity_ids))
    ratio = duration_hours / expected
    if ratio < 0.6:
        score -= 0.15
    elif ratio <= 1.8:
        score += 0.08
    return _clamp(score)


def _group_fit(activity_ids: list[str], member_count: int) -> float:
    score = 0.6
    acts = set(activity_ids)
    if member_count >= 5 and "workshop" in acts:
        score -= 0.25
    if member_count >= 5 and ({"performance", "exhibition"} & acts):
        score += 0.15
    if member_count <= 2 and ({"cafe", "walk"} & acts):
        score += 0.12
    return _clamp(score)


def is_positive_preference(item: dict) -> bool:
    """A heart is positive. No heart is neutral, including an old low rating."""
    if item.get("liked") is True:
        return True
    if item.get("liked") is False:
        return False
    rating = item.get("rating")
    return isinstance(rating, (int, float)) and not isinstance(rating, bool) and rating >= 4


def _preference(activity_ids: list[str], history: list[dict]) -> float:
    if not activity_ids or not history:
        return 0.42
    past = {activity for item in history for activity in (item.get("activities") or [])}
    done_ratio = sum(1 for activity in activity_ids if activity in past) / len(activity_ids)
    base = 0.42 + 0.12 * done_ratio
    wanted = set(activity_ids)
    exact = 0.0
    partial = 0.0
    for item in history:
        if not is_positive_preference(item):
            continue
        seen = set(item.get("activities") or [])
        union = wanted | seen
        if not union:
            continue
        overlap = len(wanted & seen) / len(union)
        if overlap >= 0.999:
            exact += 1
        elif overlap > 0:
            partial += overlap
    # The same experience marked good rises more than a pattern that only shares an activity.
    # Extra hearts level off, so one heart is not a full score.
    exact_bonus = (exact / (exact + 1)) * 0.32 if exact else 0.0
    partial_bonus = (partial / (partial + 2)) * 0.12 if partial else 0.0
    return _clamp(base + exact_bonus + partial_bonus)


def _repetition(combo: tuple, history: list[dict]) -> float:
    recent = sorted(history, key=lambda item: item.get("startTime") or "", reverse=True)[:4]
    recent_combos = [_norm(item.get("activities")) for item in recent if item.get("activities")]
    if not recent_combos:
        return 0.0
    score = 0.2 * sum(1 for item in recent_combos if item == combo)
    if recent_combos[0] == combo:
        score += 0.6
    return _clamp(score)


def _area_novelty(mode: str) -> float:
    return {
        "no_history": 0.5,
        "new_area_new_combo": 1.0,
        "new_area_familiar_combo": 0.0,
        "known_area_new_combo": 0.62,
        "known_area_familiar_combo": 0.0,
        "repeat": 0.0,
    }.get(mode, 0.0)


def _novelty(activity_ids: list[str], history: list[dict], mode: str) -> float:
    area_part = _area_novelty(mode)
    if not history:
        return 0.45 + 0.40 + 0.15 * area_part
    past = {activity for item in history for activity in (item.get("activities") or [])}
    if activity_ids:
        activity_novelty = sum(1 for activity in activity_ids if activity not in past) / len(activity_ids)
    else:
        activity_novelty = 0.0
    combo_done = _norm(activity_ids) in {
        _norm(item.get("activities")) for item in history if item.get("activities")
    }
    combo_novelty = 0.0 if combo_done else 1.0
    return 0.45 * activity_novelty + 0.40 * combo_novelty + 0.15 * area_part


def _reasons(exp: dict, area_name: str | None, mode: str, history: list[dict], member_count: int) -> list[str]:
    reasons = []
    pattern = exp["pattern"]
    acts = exp["activities"]
    past = {activity for item in history for activity in (item.get("activities") or [])}
    fresh = [activity_label(activity) for activity in acts if activity not in past]
    if not history:
        reasons.append("확정된 외출이 아직 없어, 이 그룹이 해본 경험을 빼지는 못했습니다.")
    elif mode == "known_area_new_combo":
        reasons.append(f"{area_name}는 기록에 있는 지역이고, {pattern} 조합은 없습니다.")
    elif mode == "repeat":
        reasons.append(f"{area_name}에서 같은 활동 조합이 이미 있습니다.")
    elif mode == "known_area_familiar_combo":
        reasons.append("활동 조합은 이미 했습니다. 동네만 다른 것은 새 경험으로 크게 보지 않습니다.")
    elif mode == "new_area_familiar_combo":
        reasons.append("기록에 없는 지역이지만 활동 조합은 익숙합니다.")
    elif mode == "new_area_new_combo":
        reasons.append("기록에 없는 지역이고 활동 조합도 없습니다.")
    if fresh and history:
        reasons.append("이 그룹 기록에 없는 활동: " + ", ".join(fresh) + ".")
    recent = sorted(history, key=lambda item: item.get("startTime") or "", reverse=True)[:4]
    if recent and recent[0].get("activities") and _norm(recent[0].get("activities")) == _norm(acts):
        reasons.append("가장 최근 외출과 활동 조합이 같습니다.")
    if member_count >= 5 and "workshop" in acts:
        reasons.append("인원이 많아 소규모 공방 점수를 낮췄습니다.")
    return reasons[:4]


def _score_parts(preference, novelty, context, group, repetition, novelty_weight) -> dict:
    rep_weight = REP_BASE + REP_SLOPE * novelty_weight
    parts = {
        "preference": round((1 - novelty_weight) * preference, 3),
        "novelty": round(novelty_weight * novelty, 3),
        "context": round(W_CONTEXT * context, 3),
        "group": round(W_GROUP * group, 3),
        "repetition": round(-rep_weight * repetition, 3),
    }
    parts["total"] = round(sum(parts.values()), 3)
    return parts


def score_experience(
    exp: dict,
    history,
    *,
    novelty_weight: float,
    member_count: int,
    when: datetime,
    duration_hours: float,
    area_name: str | None,
    area_is_known: bool,
) -> dict:
    mode = candidate_mode(exp["activities"], area_name, area_is_known, history)
    scores = _score_parts(
        _preference(exp["activities"], history),
        _novelty(exp["activities"], history, mode),
        _context_fit(exp["activities"], when, duration_hours),
        _group_fit(exp["activities"], member_count),
        _repetition(_norm(exp["activities"]), history),
        novelty_weight,
    )
    return {
        "experienceId": exp["id"],
        "title": _headline(exp["pattern"], area_name, mode),
        "pattern": exp["pattern"],
        "arrow": arrow_labels(exp["activities"]),
        "activities": [
            {"type": activity, "label": activity_label(activity)} for activity in exp["activities"]
        ],
        "areaName": area_name or "",
        "mode": mode,
        "modeLabel": MODE_LABELS[mode],
        "novelCombo": mode in NOVEL_MODES,
        "scores": scores,
        "reasons": _reasons(exp, area_name, mode, history, member_count),
    }


def best_experience_score(
    exp: dict,
    history,
    *,
    novelty_weight: float,
    member_count: int,
    when: datetime,
    duration_hours: float,
) -> dict:
    options = [(name, True) for name in _recent_areas(history)] + [(None, False)]
    best = None
    for area_name, area_is_known in options:
        candidate = score_experience(
            exp,
            history,
            novelty_weight=novelty_weight,
            member_count=member_count,
            when=when,
            duration_hours=duration_hours,
            area_name=area_name,
            area_is_known=area_is_known,
        )
        if best is None or candidate["scores"]["total"] > best["scores"]["total"]:
            best = candidate
    return best


def recommend_for_group(
    history,
    *,
    novelty_weight: float,
    member_count: int,
    when: datetime,
    duration_hours: float,
    limit: int = 5,
) -> dict:
    if not isinstance(when, datetime):
        raise ValueError("추천 기준 시각이 없습니다. 서버의 현재 시각은 사용하지 않습니다.")
    duration_hours = float(duration_hours)
    if duration_hours <= 0 or duration_hours > 24:
        raise ValueError("소요 시간은 0보다 크고 24시간 이하여야 합니다.")
    novelty_weight = _clamp(float(novelty_weight))
    member_count = max(1, int(member_count or 1))
    best: dict[str, dict] = {}
    for exp in EXPERIENCES:
        best[exp["id"]] = best_experience_score(
            exp,
            history,
            novelty_weight=novelty_weight,
            member_count=member_count,
            when=when,
            duration_hours=duration_hours,
        )

    items = sorted(best.values(), key=lambda item: (-item["scores"]["total"], item["pattern"]))[:limit]
    return {
        "noveltyWeight": novelty_weight,
        "historyCount": len(history),
        "memberCount": member_count,
        "formula": formula_text(),
        "weightNote": WEIGHT_NOTE,
        "travelCost": "omitted",
        "travelNote": "실제 목적지가 없는 단계라 이동 비용은 점수에 넣지 않습니다.",
        "candidateSet": candidate_set_info(),
        "plan": {
            "date": when.date().isoformat(),
            "startTime": when.strftime("%H:%M"),
            "durationHours": duration_hours,
        },
        "items": items,
    }
