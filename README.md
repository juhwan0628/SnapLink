# 스냅링크 · SnapLink

사진 속 함께한 경험을 그룹별로 기록하고 다음 활동과 실제 장소 코스를 추천하는 해커톤 MVP입니다. 실제 서비스와 10장짜리 발표 슬라이드를 하나의 저장소에서 관리합니다.

- [발표 슬라이드](https://juhwan.ing/snaplink-slides/)

## 구조

```text
snaplink-app/ 실제 서비스: 사진 분석, 그룹 기록, 추천, 지도 UI
slides/       발표 HTML 및 이미지 업로드 서버
deploy/      Nginx 및 systemd 배포 예시
requirements.txt  두 서비스 공통 Python 의존성
```

## 로컬 실행

Python 3.11 이상을 사용합니다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp snaplink-app/.env.example snaplink-app/.env
.venv/bin/python snaplink-app/server.py
```

필요한 API 키는 `snaplink-app/.env`에 입력합니다. http://127.0.0.1:8765/snaplink/ 에서 서비스를 확인할 수 있습니다. 자세한 기능, 설정 및 제한은 [서비스 README](snaplink-app/README.md)에 있습니다.

발표는 `slides/index.html`을 브라우저로 열어 확인합니다. 캔버스는 1280×720이며 화면에 맞춰 비율을 유지합니다. 방향키로 이동, F로 전체화면, N으로 발표 메모를 엽니다. 로컬 파일은 발표 확인용이며 이미지 업로드는 웹 배포 후 사용합니다.

## 서버 배포

Ubuntu, Nginx, systemd 기준입니다. 아래 설정은 저장소가 `/home/ubuntu/workspace/02_hackathon`에 있고 서비스 계정이 `ubuntu`라는 예시입니다. 환경에 맞게 경로와 계정을 변경하세요.

```sh
cd /home/ubuntu/workspace/02_hackathon
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp snaplink-app/.env.example snaplink-app/.env
# snaplink-app/.env의 API 키를 설정한 뒤:
chmod 600 snaplink-app/.env
sudo install -d -o ubuntu -g ubuntu /var/lib/snaplink-app
sudo install -d -o ubuntu -g ubuntu /var/lib/snaplink-slides/media
sudo install -d /var/www/html/slides
sudo install -m 644 slides/index.html /var/www/html/snaplink-slides/index.html
```

최초 설치 때만 편집 키를 생성합니다. 재배포 시 기존 키와 데이터를 유지합니다.

```sh
sudo -u ubuntu .venv/bin/python - <<'PYCODE'
import os, secrets
from pathlib import Path
p = Path('/var/lib/snaplink-slides/upload.key')
if not p.exists():
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(secrets.token_urlsafe(32))
PYCODE
sudo cp deploy/snaplink.service deploy/snaplink-slides.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now snaplink snaplink-slides
```

기존 도메인의 HTTPS `server` 블록에 [Nginx 설정](deploy/nginx.conf)을 추가하고 적용합니다. TLS 인증서는 호스팅 환경에서 설정하며 저장소에 포함하지 않습니다.

```sh
sudo nginx -t
sudo systemctl reload nginx
```

- 서비스: `/snaplink/` → 내부 서버 `127.0.0.1:8765`
- 슬라이드: `/snaplink-slides/` → HTML 파일
- 이미지 API: `/snaplink-slides/api/` → 내부 서버 `127.0.0.1:8766`
- 업로드 이미지: `/snaplink-slides/media/` → 서버 파일

슬라이드 업로드 서버의 허용 Origin은 현재 `juhwan.ing`과 `www.juhwan.ing`입니다. 다른 도메인으로 옮길 때 `slides/slides_asset_server.py`의 Origin 목록을 함께 변경하세요. **GitHub Pages만으로는 Python 서버 및 이미지 업로드를 실행할 수 없습니다.** GitHub에는 소스를 관리하고 웹 실행은 위 서버에서 합니다.

## 발표 이미지 편집

서버의 `upload.key` 값을 사용해 다음 주소를 본인 브라우저에서 엽니다. 아래 `YOUR_KEY`는 실제 값으로 바꾸며 편집 링크는 공개 README나 이슈에 남기지 않습니다.

```text
https://juhwan.ing/snaplink-slides/#edit=YOUR_KEY&page=1
```

이미지 영역을 누르면 PNG·JPG·WebP를 최대 25MB까지 업로드할 수 있습니다. 이미지는 서버에 저장되며 새로고침하거나 다른 기기에서 발표를 열어도 유지됩니다. 같은 이미지 자리를 쓰는 슬라이드는 함께 갱신됩니다. 일반 발표 링크는 업로드 권한이 없습니다.

업로드 데이터와 편집 키는 `/var/lib/snaplink-slides/`, 서비스 사진과 SQLite는 `/var/lib/snaplink-app/`에 보관합니다. 배포와 별도로 백업하고 재배포 시 삭제하지 않습니다. 이 앱은 해커톤 시연용으로 사용자 계정 인증이 없는 상태이므로 다중 사용자 운영 전 접근 제어를 추가해야 합니다.

## 검증

```sh
cd snaplink-app
../.venv/bin/python -m unittest discover -s tests -v
node --check static/app.js
cd ..
.venv/bin/python slides/check_asset_server.py
```

## GitHub에 올리기

`.env`, 편집 키, 업로드 사진, SQLite, 가상환경은 `.gitignore`로 제외합니다. 실제 API 키나 편집 링크를 커밋하지 마세요. 저장소는 https://github.com/juhwan0628/SnapLink 입니다. 다음 순서로 게시합니다.

```sh
git add .
git commit -m "Prepare SnapLink app and slides for deployment"
git remote add origin https://github.com/juhwan0628/SnapLink.git
git push -u origin main
```

원격 저장소가 이미 파일을 가진 경우 먼저 해당 저장소를 가져와 변경을 적용하세요. 강제 푸시는 사용하지 않습니다.

## 공동 참여자

- [sseuniiill-dot](https://github.com/sseuniiill-dot)
- [GiveMeKite](https://github.com/GiveMeKite)
- [MrDoLab](https://github.com/MrDoLab)
