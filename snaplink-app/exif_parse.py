"""Read capture time and GPS from JPEG, PNG, and WEBP.

HEIC is decoded to JPEG at upload before metadata extraction. Missing fields stay empty;
this does not guess a place name.
"""

from __future__ import annotations

import re

TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}
_CLOCK = re.compile(r"(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")


def parse_image_exif(data: bytes) -> dict:
    tiff = _extract_tiff(data)
    if not tiff:
        return {}
    try:
        return _parse_tiff(tiff)
    except Exception:
        return {}


def sniff_image(data: bytes) -> tuple[str, str] | None:
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg", "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"):
            return ".heic", "image/heic"
    return None


def _extract_tiff(data: bytes) -> bytes | None:
    if data[:2] == b"\xff\xd8":
        return _jpeg_tiff(data)
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _png_tiff(data)
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp_tiff(data)
    return None


def _jpeg_tiff(data: bytes) -> bytes | None:
    index = 2
    size = len(data)
    while index + 1 < size:
        if data[index] != 0xFF:
            return None
        while index < size and data[index] == 0xFF:
            index += 1
        if index >= size:
            return None
        marker = data[index]
        index += 1
        if marker in (0xD9, 0xDA):
            return None
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > size:
            return None
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > size:
            return None
        payload = data[index + 2 : index + segment_length]
        if marker == 0xE1 and payload.startswith(b"Exif\x00\x00"):
            return payload[6:]
        index += segment_length
    return None


def _png_tiff(data: bytes) -> bytes | None:
    index = 8
    size = len(data)
    while index + 8 <= size:
        length = int.from_bytes(data[index : index + 4], "big")
        kind = data[index + 4 : index + 8]
        if index + 12 + length > size:
            return None
        if kind == b"eXIf":
            return data[index + 8 : index + 8 + length]
        if kind == b"IEND":
            return None
        index += 12 + length
    return None


def _webp_tiff(data: bytes) -> bytes | None:
    index = 12
    size = len(data)
    while index + 8 <= size:
        kind = data[index : index + 4]
        length = int.from_bytes(data[index + 4 : index + 8], "little")
        start = index + 8
        if start + length > size:
            return None
        if kind == b"EXIF":
            return data[start : start + length]
        index = start + length + (length % 2)
    return None


def _parse_tiff(tiff: bytes) -> dict:
    if len(tiff) < 8:
        return {}
    if tiff[:2] == b"II":
        order = "little"
    elif tiff[:2] == b"MM":
        order = "big"
    else:
        return {}

    def u16(offset: int) -> int:
        return int.from_bytes(tiff[offset : offset + 2], order)

    def u32(offset: int) -> int:
        return int.from_bytes(tiff[offset : offset + 4], order)

    if u16(2) != 42:
        return {}

    def read_ifd(offset: int) -> list[tuple[int, int, int, bytes]]:
        if offset < 0 or offset + 2 > len(tiff):
            return []
        count = u16(offset)
        if count > 200 or offset + 2 + count * 12 > len(tiff):
            return []
        entries = []
        for index in range(count):
            cursor = offset + 2 + index * 12
            entries.append((u16(cursor), u16(cursor + 2), u32(cursor + 4), tiff[cursor + 8 : cursor + 12]))
        return entries

    def payload(type_id: int, count: int, field: bytes) -> bytes:
        unit = TYPE_SIZE.get(type_id)
        if unit is None or count <= 0 or count > 1024:
            return b""
        size = unit * count
        if size <= 4:
            return field[:size]
        offset = int.from_bytes(field, order)
        if offset < 0 or offset + size > len(tiff):
            return b""
        return tiff[offset : offset + size]

    def ascii_value(type_id: int, count: int, field: bytes) -> str:
        raw = payload(type_id, count, field)
        return raw.split(b"\x00", 1)[0].decode("latin1", "replace").strip()

    def rationals(type_id: int, count: int, field: bytes) -> list[float]:
        raw = payload(type_id, count, field)
        values = []
        for index in range(0, len(raw) - 7, 8):
            numerator = int.from_bytes(raw[index : index + 4], order)
            denominator = int.from_bytes(raw[index + 4 : index + 8], order)
            values.append(numerator / denominator if denominator else 0.0)
        return values

    def clock(text: str) -> str | None:
        match = _CLOCK.fullmatch(text)
        if not match:
            return None
        year, month, day, hour, minute, second = match.groups()
        return f"{year}-{month}-{day}T{hour}:{minute}:{second}"

    taken = None
    ifd0 = read_ifd(u32(4))
    exif_offset = None
    gps_offset = None
    for tag, type_id, count, field in ifd0:
        if tag == 0x0132:
            taken = clock(ascii_value(type_id, count, field)) or taken
        elif tag == 0x8769 and type_id == 4 and count == 1:
            exif_offset = int.from_bytes(field, order)
        elif tag == 0x8825 and type_id == 4 and count == 1:
            gps_offset = int.from_bytes(field, order)

    if exif_offset is not None:
        for tag, type_id, count, field in read_ifd(exif_offset):
            if tag == 0x9003:
                original = clock(ascii_value(type_id, count, field))
                if original:
                    taken = original

    lat = None
    lng = None
    if gps_offset is not None:
        refs: dict[int, str] = {}
        coords: dict[int, list[float]] = {}
        for tag, type_id, count, field in read_ifd(gps_offset):
            if tag in (1, 3):
                refs[tag] = ascii_value(type_id, count, field).upper()[:1]
            elif tag in (2, 4):
                coords[tag] = rationals(type_id, count, field)

        def degrees(values: list[float], ref: str, negative: str) -> float | None:
            if len(values) < 3:
                return None
            value = values[0] + values[1] / 60 + values[2] / 3600
            if ref == negative:
                value = -value
            return value

        lat = degrees(coords.get(2, []), refs.get(1, "N"), "S")
        lng = degrees(coords.get(4, []), refs.get(3, "E"), "W")
        if lat is None or lng is None or abs(lat) > 90 or abs(lng) > 180:
            lat, lng = None, None

    if taken is None and lat is None:
        return {}
    return {"taken_at": taken, "lat": lat, "lng": lng}
