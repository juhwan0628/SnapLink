"""Experience combinations for retrieval.

A text model may propose activity ids. Place names in that response are ignored.
If the call fails or the list is empty, the caller keeps the static scored patterns.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request

from domain import ACTIVITY_LABELS, activity_label

logger = logging.getLogger("memory.candidates")


def patterns_from_payload(payload: dict) -> list[dict]:
    rows = payload.get("experiences") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    cleaned = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        activities = row.get("activities")
        if not isinstance(activities, list) or not 1 <= len(activities) <= 3:
            continue
        ids = []
        for activity in activities:
            activity_id = str(activity).strip()
            if activity_id not in ACTIVITY_LABELS or activity_id in ids:
                continue
            ids.append(activity_id)
        if len(ids) < 2:
            continue
        key = tuple(ids)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({
            "id": "llm-" + "-".join(ids),
            "pattern": " → ".join(activity_label(activity) for activity in ids),
            "activities": ids,
        })
        if len(cleaned) >= 8:
            break
    return cleaned


def propose_patterns(history: list[dict], *, when, duration_hours: float, member_count: int, novelty_weight: float) -> list[dict]:
    if not os.environ.get("VLM_API_KEY"):
        raise RuntimeError("text model key is unset")
    counts: dict[str, int] = {}
    for item in history:
        for activity in item.get("activities") or []:
            if activity in ACTIVITY_LABELS:
                counts[activity] = counts.get(activity, 0) + 1
    done = ", ".join(f"{activity}:{count}" for activity, count in counts.items()) or "none"
    allowed = ", ".join(ACTIVITY_LABELS)
    prompt = (
        "Propose 6 next experience combinations for one group. "
        "Use only these activity ids: " + allowed + ". "
        "Each combination has 2 activities. Do not invent place names, addresses, ratings, hours, or event schedules. "
        f"Past activity counts: {done}. "
        f"Start {when.strftime('%Y-%m-%d %H:%M')}, duration {duration_hours} hours, "
        f"group size {member_count}, novelty {novelty_weight}. "
        'Return JSON: {"experiences":[{"activities":["exhibition","cafe"]}]}'
    )
    base = os.environ.get("VLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("VLM_MODEL", "gpt-4o-mini")
    body = {
        "model": model,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {os.environ['VLM_API_KEY']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        payload = json.loads(response.read().decode("utf-8"))
    content = payload["choices"][0]["message"]["content"]
    return patterns_from_payload(json.loads(content))


def explain_evidence(evidence: dict) -> str:
    if not os.environ.get("VLM_API_KEY"):
        raise RuntimeError("text model key is unset")
    prompt = (
        "아래 JSON에 있는 사실만 한국어 한 문단으로 적으세요. "
        "JSON에 없는 장소 이름, 주소, 영업시간, 가격, 별점, 공연 일정, 이동시간은 만들지 마세요. "
        "열려 있을 것이라고 추측하지 마세요. "
        + json.dumps(evidence, ensure_ascii=False)
    )
    base = os.environ.get("VLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("VLM_MODEL", "gpt-4o-mini")
    body = {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {os.environ['VLM_API_KEY']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        payload = json.loads(response.read().decode("utf-8"))
    content = json.loads(payload["choices"][0]["message"]["content"])
    return str(content.get("text") or "").strip()
