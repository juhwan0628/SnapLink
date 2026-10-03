"""Activity labels for one outing.

A vision model judges the shared experience of the photos in one outing.
Capture time is not turned into a meal, cafe, or bar. A missing key, a failed
request, an unreadable response, and a model choice of unknown stay distinct.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import urllib.error
import urllib.request

from domain import ACTIVITIES, TAGS, UNKNOWN_ACTIVITY, normalize_tags

logger = logging.getLogger("memory.infer")

MAX_IMAGE_BYTES = 1_200_000
MAX_EDGE = 1280
# Only an explicit, very low score is discarded. A draft such as shopping at 0.75 stays.
LOW_CONFIDENCE = 0.2

SCENE_TO_TAG = {
    "indoor": "실내",
    "outdoor": "야외",
    "night": "밤",
    "quiet": "조용함",
    "crowded": "북적임",
    "busy": "북적임",
    "relaxed": "여유",
    "실내": "실내",
    "야외": "야외",
    "밤": "밤",
    "조용함": "조용함",
    "북적임": "북적임",
    "여유": "여유",
}

FALLBACK_NOTES = {
    "images_skipped": "사진을 모델에 전달하지 못했습니다.",
    "api_error": "이미지 분석 요청에 실패했습니다.",
    "parse_error": "이미지 분석 결과를 읽지 못했습니다.",
    "invalid_activity": "이미지 분석이 허용된 활동 이름을 반환하지 않았습니다.",
    "low_confidence": "이미지 분석의 확신이 너무 낮아 활동을 unknown으로 두었습니다.",
    "genuinely_unknown": "이미지 분석 결과 활동을 특정하지 못했습니다.",
}


def activity_enum() -> list[str]:
    return [key for key, _label in ACTIVITIES] + [UNKNOWN_ACTIVITY]


def vision_prompt() -> str:
    allowed = ", ".join(activity_enum())
    scenes = "indoor, outdoor, night, quiet, crowded, relaxed"
    return f"""당신은 한 외출의 사진을 보고, 관찰 가능한 장면으로 경험의 종류를 고르는 보조자입니다.
정확한 상호나 장소 이름은 만들지 마세요. place_type은 bakery, restaurant, landmark처럼 일반적인 유형만 적으세요.

These images belong to the same outing. Infer the shared experience from all images together.

순서:
1. 여러 사진에서 직접 보이는 장면과 사물을 파악합니다.
2. 사진들이 같은 외출의 일부라는 점을 고려합니다.
3. 그 증거로 가장 적합한 activity 하나를 고릅니다.
4. 사진 전체를 봐도 활동 범주를 판단할 근거가 부족할 때만 unknown을 고릅니다. 애매하지 않은 장면은 unknown으로 보내지 마세요.
unknown이어도 보이는 place_type, scene_tags, evidence는 비우지 마세요.

activity는 반드시 다음 중 하나만 사용합니다: {allowed}
scene_tags는 다음만 사용합니다: {scenes}
진열된 빵이나 상품을 둘러보는 매장 내부는 shopping입니다. 먹는 장면이나 식사 중인 상이 없으면 meal로 두지 마세요.
상에 차려진 음식이나 식사 중인 장면은 meal입니다.
보행로, 광장, 야외 구조물만 보인다고 walk로 두지 마세요. 함께 걷는 행동이 분명할 때만 walk입니다.
밤의 조형물이나 광장처럼 행동보다 장소가 주로 보이면 activity는 unknown으로 두고, scene_tags에는 보이는 outdoor, night를 남기세요.
보이지 않는 행동을 추측하지 마세요.
confidence는 0부터 1 사이의 숫자입니다. 예시 문장을 그대로 복사하지 마세요.

Example A
빵과 페이스트리가 진열된 매대, 베이커리 내부, 상품을 둘러보는 장면:
{{"activity":"shopping","confidence":0.90,"place_type":"bakery","scene_tags":["indoor"],"evidence":"Multiple photos show bread and pastries displayed for sale inside a bakery."}}

Example B
식탁 위 완성된 음식, 식사 중인 접시, 음식점 내부:
{{"activity":"meal","confidence":0.90,"place_type":"restaurant","scene_tags":["indoor"],"evidence":"Photos show served dishes on a table inside a restaurant."}}

Example C
밤의 야외 구조물, 보행 공간, 실제 행동은 확인되지 않음:
{{"activity":"unknown","confidence":0.35,"place_type":"landmark","scene_tags":["outdoor","night"],"evidence":"Night photos show an outdoor structure and walking space, without a clear activity."}}

JSON만 반환하세요.
"""


def unknown_activity(note: str, *, tags=None, place_hint=None, inference_source: str = "unknown") -> dict:
    return {
        "activities": [{"type": UNKNOWN_ACTIVITY, "confidence": 0, "source": "none"}],
        "tags": normalize_tags(tags or []),
        "placeHint": _place_hint(place_hint),
        "source": inference_source,
        "note": note[:300],
        "fallbackReason": None,
    }


def vision_enabled() -> bool:
    return bool(os.environ.get("VLM_API_KEY"))


def infer_cluster(photos: list[dict], image_loader=None, request_fn=None) -> dict:
    if not vision_enabled() and request_fn is None:
        logger.info("VLM fallback_reason=missing_key photos=%s", len(photos))
        return unknown_activity(
            "VLM_API_KEY가 없어 이미지 분석을 하지 않았습니다. "
            "촬영 시각으로 활동을 추정하지 않습니다. 활동은 unknown입니다. 직접 선택해 주세요.",
        )
    if image_loader is None:
        logger.warning("VLM fallback_reason=images_skipped reason=no_loader photos=%s", len(photos))
        return _fallback("images_skipped", "이미지 파일을 넘기지 못했습니다.")

    content, sent = _build_content(photos, image_loader)
    if sent == 0:
        logger.info("VLM fallback_reason=images_skipped sent=0 photos=%s", len(photos))
        return _fallback("images_skipped", "보낼 수 있는 사진이 없었습니다.")
    caller = request_fn or _request_vlm
    try:
        raw = caller(content)
    except Exception as exc:
        logger.exception("VLM fallback_reason=api_error")
        logger.info("VLM api_error detail=%s", _safe_error(exc))
        return _fallback("api_error", _safe_error(exc))
    logger.info("VLM raw result images=%s raw=%s", sent, _clip(raw))
    try:
        parsed = _extract_json(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.info("VLM fallback_reason=parse_error raw=%s", _clip(raw))
        return _fallback("parse_error")
    if not isinstance(parsed, dict):
        logger.info("VLM fallback_reason=parse_error raw=%s", _clip(raw))
        return _fallback("parse_error")
    result = interpret_vlm(parsed)
    logger.info(
        "VLM parsed activity=%s confidence=%s place_type=%s scene_tags=%s fallback_reason=%s",
        result.get("parsedActivity"),
        result.get("parsedConfidence"),
        result.get("placeHint"),
        result.get("tags"),
        result.get("fallbackReason"),
    )
    return result


def interpret_vlm(parsed: dict) -> dict:
    token, confidence, explicit = _activity_token(parsed)
    place_type = _place_hint(parsed.get("place_type") or parsed.get("placeHint"))
    tags = _scene_tags(parsed.get("scene_tags") if "scene_tags" in parsed else parsed.get("tags"))
    evidence = str(parsed.get("evidence") or parsed.get("note") or "").strip()
    if not explicit:
        return _fallback(
            "invalid_activity",
            tags=tags,
            place_hint=place_type,
            evidence=evidence,
            parsed_activity=token,
            parsed_confidence=confidence,
        )
    if token is None or token in ("", UNKNOWN_ACTIVITY):
        return _fallback(
            "genuinely_unknown",
            tags=tags,
            place_hint=place_type,
            evidence=evidence,
            parsed_activity=UNKNOWN_ACTIVITY,
            parsed_confidence=confidence,
            inference_source="vlm",
        )
    if token not in activity_enum():
        return _fallback(
            "invalid_activity",
            tags=tags,
            place_hint=place_type,
            evidence=evidence,
            parsed_activity=token,
            parsed_confidence=confidence,
        )
    score = _confidence(confidence)
    if score < LOW_CONFIDENCE:
        return _fallback(
            "low_confidence",
            tags=tags,
            place_hint=place_type,
            evidence=evidence,
            parsed_activity=token,
            parsed_confidence=score,
        )
    note = evidence or "사진 내용을 보고 추정했습니다. 맞는지 확인해 주세요."
    if place_type and place_type not in note:
        note = f"{note} 장소 유형: {place_type}."
    return {
        "activities": [{"type": token, "confidence": score, "source": "vlm"}],
        "tags": tags,
        "placeHint": place_type,
        "source": "vlm",
        "note": note[:300],
        "fallbackReason": None,
        "parsedActivity": token,
        "parsedConfidence": score,
    }


def prepare_image(data: bytes, mime: str) -> tuple[bytes, str] | None:
    if not data or mime == "image/heic":
        return None
    if len(data) <= MAX_IMAGE_BYTES and mime in ("image/jpeg", "image/png", "image/webp"):
        return data, mime
    try:
        from PIL import Image, ImageOps

        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        image = image.convert("RGB")
        image.thumbnail((MAX_EDGE, MAX_EDGE))
        for quality in (80, 60, 45):
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality)
            prepared = buffer.getvalue()
            if len(prepared) <= MAX_IMAGE_BYTES:
                return prepared, "image/jpeg"
    except Exception:
        logger.exception("VLM image prepare failed bytes=%s mime=%s", len(data), mime)
    return None


def _build_content(photos: list[dict], image_loader) -> tuple[list[dict], int]:
    content: list[dict] = [{"type": "text", "text": vision_prompt()}]
    sent = 0
    for photo in _spread(photos, 3):
        loaded = image_loader(photo)
        if not loaded:
            logger.info("VLM image skipped reason=unreadable")
            continue
        data, mime = loaded
        original = len(data or b"")
        prepared = prepare_image(data, mime)
        if not prepared:
            logger.info("VLM image skipped reason=unusable bytes=%s mime=%s", original, mime)
            continue
        encoded_bytes, encoded_mime = prepared
        logger.info(
            "VLM image prepared bytes_in=%s bytes_out=%s mime=%s",
            original,
            len(encoded_bytes),
            encoded_mime,
        )
        encoded = base64.b64encode(encoded_bytes).decode("ascii")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{encoded_mime};base64,{encoded}"},
        })
        sent += 1
    return content, sent


def _request_vlm(content: list[dict]) -> str:
    base = os.environ.get("VLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("VLM_MODEL", "gpt-4o-mini")
    body = {
        "model": model,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": content}],
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
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code} {_clip(detail, 240)}") from exc
    return payload["choices"][0]["message"]["content"]


def _fallback(
    reason: str,
    detail: str = "",
    *,
    tags=None,
    place_hint=None,
    evidence: str = "",
    parsed_activity=None,
    parsed_confidence=None,
    inference_source: str = "unknown",
) -> dict:
    parts = [FALLBACK_NOTES[reason]]
    if detail:
        parts.append(detail)
    if evidence and evidence not in parts:
        parts.append(evidence)
    if place_hint:
        parts.append(f"장소 유형: {place_hint}.")
    if tags:
        parts.append("관찰: " + ", ".join(tags) + ".")
    parts.append("직접 선택해 주세요.")
    result = unknown_activity(
        " ".join(part for part in parts if part),
        tags=tags,
        place_hint=place_hint,
        inference_source=inference_source,
    )
    result["fallbackReason"] = reason
    result["parsedActivity"] = parsed_activity
    result["parsedConfidence"] = parsed_confidence
    return result


def _activity_token(parsed: dict) -> tuple[str | None, object, bool]:
    if "activity" in parsed:
        value = parsed.get("activity")
        confidence = parsed.get("confidence")
        if isinstance(value, dict):
            confidence = value.get("confidence", confidence)
            value = value.get("type") or value.get("activity")
        if value is None:
            return None, confidence, True
        return str(value).strip().lower(), confidence, True
    items = parsed.get("activities")
    if isinstance(items, list) and items:
        item = items[0]
        if isinstance(item, str):
            return item.strip().lower(), None, True
        if isinstance(item, dict):
            value = item.get("type") or item.get("activity")
            if value is None:
                return None, item.get("confidence"), True
            return str(value).strip().lower(), item.get("confidence", parsed.get("confidence")), True
    return None, None, False


def _confidence(value) -> float:
    if value is None or value == "":
        return 0.5
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.5
    if 1 < score <= 100:
        score /= 100
    return max(0.0, min(1.0, score))


def _scene_tags(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",")]
    mapped = []
    for item in value:
        key = str(item).strip().lower()
        tag = SCENE_TO_TAG.get(key) or SCENE_TO_TAG.get(str(item).strip())
        if tag and tag not in mapped:
            mapped.append(tag)
    return normalize_tags(mapped)


def _place_hint(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("null", "none"):
        return None
    return text[:40]


def _spread(photos: list[dict], limit: int) -> list[dict]:
    if len(photos) <= limit:
        return list(photos)
    if limit == 1:
        return [photos[len(photos) // 2]]
    last = len(photos) - 1
    indexes = {round(index * last / (limit - 1)) for index in range(limit)}
    return [photos[index] for index in sorted(indexes)]


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip().strip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def _clip(text: str, limit: int = 800) -> str:
    cleaned = _safe_error(text).replace("\n", " ")
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "..."


def _safe_error(value) -> str:
    text = str(value)
    secret = os.environ.get("VLM_API_KEY")
    if secret:
        text = text.replace(secret, "[redacted]")
    return text
