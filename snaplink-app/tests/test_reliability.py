import io
import json
import sys
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from PIL import Image
from pillow_heif import register_heif_opener
from test_core import make_jpeg


def heic_photo():
    register_heif_opener()
    buf = io.BytesIO()
    image = Image.new('RGB', (32, 24), 'red')
    image.save(buf, format='HEIF', exif=b'Exif\0\0' + make_jpeg()[12:-2])
    return buf.getvalue()


class ReliabilityTests(unittest.TestCase):
    def test_prefixed_assets_and_api_work_without_proxy(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{httpd.server_address[1]}'
            for path in ('/snaplink/', '/snaplink/static/app.js', '/snaplink/api/meta', '/api/meta'):
                try:
                    response = urllib.request.urlopen(base + path)
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    self.assertEqual(response.status, 200, path)
        finally:
            httpd.shutdown()
            thread.join()
            httpd.server_close()

    def test_heic_conversion_preserves_capture_time_and_gps(self):
        from exif_parse import parse_image_exif
        convert = getattr(server, 'convert_heic', lambda data: data)
        jpeg = convert(heic_photo())
        self.assertTrue(jpeg.startswith(b'\xff\xd8'))
        meta = parse_image_exif(jpeg)
        self.assertEqual(meta['taken_at'], '2024-05-11T14:30:00')
        self.assertAlmostEqual(meta['lat'], 37.5445, places=4)


class CourseValidationTests(unittest.TestCase):
    def test_unconfigured_checks_keep_course_explicitly_unverified(self):
        from datetime import datetime
        import os
        from unittest.mock import patch
        import importlib.util
        spec = importlib.util.find_spec('validation')
        self.assertIsNotNone(spec, 'course validation is missing')
        from validation import validate_courses
        course = {'stops': [dict(order=1, place={'name': '카페', 'lat': 37.5, 'lng': 127})]}
        with patch.dict(os.environ, {'TMAP_API_KEY': '', 'GOOGLE_PLACES_API_KEY': ''}):
            kept = validate_courses([course], {'lat': 37.5, 'lng': 127}, datetime(2026, 10, 3, 18), 3)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]['validation']['route'], 'unverified')
        self.assertEqual(kept[0]['validation']['hours'], 'unverified')

    def test_overnight_hours_cover_visit_but_not_after_closing(self):
        from datetime import datetime
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('validation'))
        from validation import hours_cover
        hours = {'periods': [{'open': {'day': 6, 'hour': 22}, 'close': {'day': 0, 'hour': 2}}]}
        self.assertTrue(hours_cover(hours, datetime(2026, 10, 3, 23), datetime(2026, 10, 4, 1)))
        self.assertFalse(hours_cover(hours, datetime(2026, 10, 4, 1), datetime(2026, 10, 4, 3)))
        self.assertIsNone(hours_cover({}, datetime(2026, 10, 3, 23), datetime(2026, 10, 4, 1)))

    def test_closed_and_time_infeasible_courses_are_removed(self):
        from datetime import datetime
        import os
        from unittest.mock import patch
        from validation import validate_courses
        def course():
            return {'stops': [dict(order=1, place={'name': '카페', 'lat': 37.5, 'lng': 127})]}
        leg = {'meters': 600, 'durationMinutes': 10, 'path': [[37.5, 127]], 'source': 'tmap-walking'}
        closed = {'periods': [{'open': {'day': 6, 'hour': 9}, 'close': {'day': 6, 'hour': 17}}]}
        with patch.dict(os.environ, {'TMAP_API_KEY': 'test', 'GOOGLE_PLACES_API_KEY': 'test'}), patch('validation.walking_route', return_value=leg), patch('validation.reported_hours', return_value=closed):
            self.assertEqual(validate_courses([course()], {'lat': 37.5, 'lng': 127}, datetime(2026, 10, 3, 18), 3), [])
            self.assertEqual(validate_courses([course()], {'lat': 37.5, 'lng': 127}, datetime(2026, 10, 3, 18), 0.1), [])

    def test_course_assembly_runs_validation(self):
        from datetime import datetime
        from unittest.mock import patch
        from courses import assemble_recommendation
        from test_places import FakePlaces, nearby_rows, LOCATION, HISTORY
        with patch('validation.validate_courses', return_value=[]) as validate:
            result = assemble_recommendation(HISTORY, novelty_weight=0.5, member_count=2, when=datetime(2026, 10, 3, 18), duration_hours=3, location=LOCATION, place_provider=FakePlaces(nearby_rows()))
        self.assertTrue(validate.called)
        self.assertEqual(result['courses'], [])

    def test_reported_hours_reject_same_name_at_distant_branch(self):
        from unittest.mock import patch
        from validation import reported_hours
        import os
        row = {'displayName': {'text': '카페'}, 'location': {'latitude': 38, 'longitude': 128}, 'regularOpeningHours': {'periods': []}}
        with patch.dict(os.environ, {'GOOGLE_PLACES_API_KEY': 'test'}), patch('validation._post', return_value={'places': [row]}):
            self.assertIsNone(reported_hours({'name': '카페', 'lat': 37.5, 'lng': 127}))

    def test_tmap_route_summary_and_coordinates(self):
        import os
        from unittest.mock import patch
        from validation import walking_route
        payload = {'features': [
            {'properties': {'totalDistance': 900, 'totalTime': 720}, 'geometry': {'type': 'Point', 'coordinates': [127, 37.5]}},
            {'properties': {}, 'geometry': {'type': 'LineString', 'coordinates': [[127, 37.5], [127.01, 37.51]]}}]}
        with patch.dict(os.environ, {'TMAP_API_KEY': 'test'}), patch('validation._post', return_value=payload) as request:
            leg = walking_route({'lat': 37.5, 'lng': 127}, {'lat': 37.51, 'lng': 127.01})
        self.assertEqual(leg['durationMinutes'], 12)
        self.assertEqual(leg['meters'], 900)
        self.assertEqual(leg['path'][0], [37.5, 127])
        self.assertEqual(request.call_args.args[1]['startX'], 127)

    def test_api_timeout_preserves_unverified_course(self):
        import os
        from datetime import datetime
        from unittest.mock import patch
        from validation import validate_courses
        course = {'stops': [dict(order=1, place={'name': '카페', 'lat': 37.5, 'lng': 127})]}
        with patch.dict(os.environ, {'TMAP_API_KEY': 'test', 'GOOGLE_PLACES_API_KEY': 'test'}), patch('validation.walking_route', side_effect=TimeoutError), patch('validation.reported_hours', side_effect=TimeoutError):
            result = validate_courses([course], {'lat': 37.5, 'lng': 127}, datetime(2026, 10, 3, 18), 3)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['validation']['route'], 'unverified')
        self.assertEqual(result[0]['validation']['hours'], 'unverified')

    def test_empty_and_malformed_hours_are_not_marked_open(self):
        from datetime import datetime
        from validation import hours_cover
        when = datetime(2026, 10, 3, 18)
        self.assertFalse(hours_cover({'periods': []}, when, when))
        self.assertIsNone(hours_cover({'periods': [{'open': {'day': 8, 'hour': 99}, 'close': {'day': 0, 'hour': 2}}]}, when, when))
