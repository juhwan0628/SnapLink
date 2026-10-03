"""Optional real-world checks on shortlisted courses, without invented hours/routes."""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
import urllib.request
from datetime import timedelta

from geo import haversine_m

logger = logging.getLogger('memory.validation')


def _post(url, payload, headers):
    request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                    headers={'Content-Type': 'application/json', **headers}, method='POST')
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read())


def walking_route(start, end):
    data = _post('https://apis.openapi.sk.com/tmap/routes/pedestrian?version=1', {
        'startX': start['lng'], 'startY': start['lat'],
        'endX': end['lng'], 'endY': end['lat'],
        'startName': 'start', 'endName': 'end',
        'reqCoordType': 'WGS84GEO', 'resCoordType': 'WGS84GEO',
    }, {'appKey': os.environ['TMAP_API_KEY']})
    features = data['features']
    summary = next(f['properties'] for f in features if 'totalDistance' in f.get('properties', {}))
    meters, seconds = float(summary['totalDistance']), float(summary['totalTime'])
    if not all(math.isfinite(value) and value >= 0 for value in (meters, seconds)):
        raise ValueError('invalid route summary')
    path = []
    for feature in features:
        geometry = feature.get('geometry', {})
        if geometry.get('type') == 'LineString':
            for lng, lat, *_ in geometry['coordinates']:
                if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                    raise ValueError('invalid route coordinates')
                path.append([lat, lng])
    if not path:
        raise ValueError('route geometry missing')
    return {'meters': round(meters), 'durationMinutes': seconds / 60,
            'source': 'tmap-walking', 'path': path}


def reported_hours(place):
    data = _post('https://places.googleapis.com/v1/places:searchText', {
        'textQuery': place['name'] + ' ' + place.get('address', ''), 'languageCode': 'ko',
        'locationBias': {'circle': {'center': {'latitude': place['lat'], 'longitude': place['lng']}, 'radius': 100.0}},
    }, {'X-Goog-Api-Key': os.environ['GOOGLE_PLACES_API_KEY'],
        'X-Goog-FieldMask': 'places.displayName,places.location,places.regularOpeningHours,places.googleMapsUri'})
    compact = lambda name: re.sub(r'\W', '', name).casefold()
    matches = []
    for row in data.get('places', []):
        location = row.get('location', {})
        if compact(row.get('displayName', {}).get('text', '')) != compact(place['name']):
            continue
        if 'latitude' not in location or 'longitude' not in location:
            continue
        if haversine_m(place['lat'], place['lng'], location['latitude'], location['longitude']) <= 100:
            matches.append(row)
    if len(matches) != 1:
        return None
    row = matches[0]
    hours = row.get('regularOpeningHours')
    if not hours:
        return None
    return {**hours, 'sourceUrl': row.get('googleMapsUri', ''), 'provider': 'google-places'}


def hours_cover(hours, start, end):
    """Check the entire estimated visit against regular weekly hours; holidays unknown."""
    periods = hours.get('periods')
    if periods is None or end < start:
        return None
    if periods == []:
        return False
    midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)
    for period in periods:
        opening = period.get('open', {})
        closing = period.get('close')
        for point in (opening, closing):
            if point is None:
                continue
            if not isinstance(point, dict) or 'day' not in point:
                return None
            for field, maximum in (('day', 6), ('hour', 23), ('minute', 59)):
                value = point.get(field, 0)
                if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum:
                    return None
        if opening.get('day') == 0 and opening.get('hour', 0) == 0 and opening.get('minute', 0) == 0 and closing is None:
            return True  # Google's documented 24/7 representation.
        if closing is None or 'day' not in opening or 'day' not in closing:
            return None
        for offset in range(-7, 1):
            day = midnight + timedelta(days=offset)
            if (day.weekday() + 1) % 7 != opening['day']:
                continue
            opened = day + timedelta(hours=opening.get('hour', 0), minutes=opening.get('minute', 0))
            closed = day + timedelta(days=(closing['day'] - opening['day']) % 7,
                                     hours=closing.get('hour', 0), minutes=closing.get('minute', 0))
            if closed <= opened:
                closed += timedelta(days=7)
            if opened <= start < closed and end <= closed:
                return True
    return False


def validate_courses(courses, center, when, duration_hours):
    route_cache, hours_cache = {}, {}
    kept = []
    deadline = time.monotonic() + 20
    routes_enabled = bool(os.environ.get("TMAP_API_KEY"))
    hours_enabled = bool(os.environ.get("GOOGLE_PLACES_API_KEY"))
    for course in courses:
        check = {'route': 'unverified', 'hours': 'unverified', 'hoursBasis': 'regular',
                 'note': '영업시간·보행 경로 미확인'}
        course['validation'] = check
        stops = course['stops']
        legs = []
        origin = center
        for stop in stops:
            place = stop['place']
            if routes_enabled and time.monotonic() < deadline:
                key = (origin['lat'], origin['lng'], place['lat'], place['lng'])
                if key not in route_cache:
                    try:
                        route_cache[key] = walking_route(origin, place)
                    except Exception as exc:
                        logger.info('walking check unavailable: %s', type(exc).__name__)
                        route_cache[key] = None
                        routes_enabled = False
                legs.append(route_cache[key])
            else:
                legs.append(None)
            if hours_enabled and time.monotonic() < deadline:
                key = (place['name'], place['lat'], place['lng'])
                if key not in hours_cache:
                    try:
                        hours_cache[key] = reported_hours(place)
                    except Exception as exc:
                        logger.info('hours check unavailable: %s', type(exc).__name__)
                        hours_cache[key] = None
                        hours_enabled = False
                stop['openingHours'] = hours_cache[key]
            origin = place
        if stops and all(legs):
            travel = sum(leg['durationMinutes'] for leg in legs)
            if travel >= duration_hours * 60 or any(leg['meters'] > 3000 for leg in legs[1:]):
                continue
            check['route'] = 'verified'
            check['walkingMinutes'] = round(travel, 1)
            course['distanceSource'] = 'tmap-walking'
            course['routePath'] = [point for leg in legs for point in leg['path']]
            # ponytail: divide remaining time equally; per-activity dwell times when itinerary planning expands.
            dwell = (duration_hours * 60 - travel) / len(stops)
            arrival = when
            states = []
            for stop, leg in zip(stops, legs):
                arrival += timedelta(minutes=leg['durationMinutes'])
                departure = arrival + timedelta(minutes=dwell)
                stop.update(distanceFromPreviousM=leg['meters'], distanceSource=leg['source'],
                            durationMinutes=leg['durationMinutes'], arrivalTime=arrival.isoformat(), departureTime=departure.isoformat())
                stop['place']['distanceM'] = leg['meters']
                hours = stop.get('openingHours')
                states.append(hours_cover(hours, arrival, departure) if hours else None)
                arrival = departure
            if False in states:
                continue
            check['hours'] = 'verified' if all(state is True for state in states) else 'unverified'
        check['note'] = ('보행 경로 확인' if check['route'] == 'verified' else '직선거리 · 보행 경로 미확인')
        check['note'] += (' · 예상 체류시간이 정기 영업시간 내에 있음 (임시휴무·예약 미확인)'
                          if check['hours'] == 'verified' else ' · 방문 시각 영업 여부 미확인')
        kept.append(course)
    return kept
