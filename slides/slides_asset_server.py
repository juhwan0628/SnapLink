"""Small authenticated image store for the presentation."""
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from PIL import Image

ROOT = Path(os.environ.get('SLIDES_DATA', '/var/lib/snaplink-slides'))
KEYS = {'group-record','map-result','past-photos','memory-crop','reason-crop','photo-import','activity-confirm','group-list','family-record','service-icon'}
MAX_BYTES = 25 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 20_000_000
LOCK = threading.Lock()

def atomic_write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        name = f.name
        try:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        except Exception:
            os.unlink(name)
            raise
    os.chmod(name, 0o644)
    os.replace(name, path)

def assets():
    p = ROOT / 'assets.json'
    return json.loads(p.read_text()) if p.exists() else {}

class Handler(BaseHTTPRequestHandler):
    def reply(self, status, value):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if urlsplit(self.path).path != '/slides/api/assets':
            return self.reply(404, {'error':'없는 주소입니다.'})
        with LOCK:
            self.reply(200, assets())

    def do_POST(self):
        key = urlsplit(self.path).path.removeprefix('/slides/api/assets/')
        if key not in KEYS or not self.path.startswith('/slides/api/assets/'):
            return self.reply(404, {'error':'없는 이미지 자리입니다.'})
        expected = 'Bearer ' + (ROOT / 'upload.key').read_text().strip()
        if not hmac.compare_digest(self.headers.get('Authorization',''), expected):
            return self.reply(401, {'error':'이미지 편집 링크에서 열어주세요.'})
        origin = self.headers.get('Origin')
        if origin and origin not in {'https://juhwan.ing','https://www.juhwan.ing'}:
            return self.reply(403, {'error':'허용되지 않은 요청입니다.'})
        try:
            size = int(self.headers.get('Content-Length','0'))
        except ValueError:
            return self.reply(400, {'error':'잘못된 파일 크기입니다.'})
        if not 0 < size <= MAX_BYTES:
            return self.reply(413, {'error':'25MB 이하의 이미지를 선택해주세요.'})
        self.connection.settimeout(60)
        try:
            data = self.rfile.read(size)
            if len(data) != size:
                return self.reply(400, {'error':'파일 전송이 완료되지 않았습니다.'})
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    ext = {'PNG':'png','JPEG':'jpg','WEBP':'webp'}.get(image.format)
                    if ext is None:
                        return self.reply(415, {'error':'PNG, JPG, WebP만 사용할 수 있습니다.'})
                    image.verify()
            filename = f'{key}-{hashlib.sha256(data).hexdigest()[:20]}.{ext}'
            with LOCK:
                atomic_write(ROOT / 'media' / filename, data)
                saved = assets()
                saved[key] = '/slides/media/' + filename
                atomic_write(ROOT / 'assets.json', json.dumps(saved).encode())
            self.reply(200, {'key':key,'url':saved[key]})
        except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            self.reply(400, {'error':'이미지를 읽거나 저장하지 못했습니다. 다시 시도해주세요.'})

if __name__ == '__main__':
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / 'media').mkdir(exist_ok=True)
    ThreadingHTTPServer(('127.0.0.1',8766),Handler).serve_forever()
