# Snaplink

사진에서 그룹 경험을 기록하고 다음 활동·장소 코스를 추천하는 해커톤 MVP.

## 실행

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python server.py
```

http://127.0.0.1:8765/ 또는 http://127.0.0.1:8765/snaplink/.
`/snaplink` 접두사를 유지하거나 제거하는 프록시 모두 지원.

## 설정

앱 폴더 `.env` 사용. 키는 브라우저로 보내지 않음.

| 변수 | 용도 |
|---|---|
| `VLM_API_KEY`, `VLM_BASE_URL`, `VLM_MODEL` | 사진 활동 추정·추천 후보 생성 |
| `KAKAO_REST_API_KEY` | 실제 장소 검색 |
| `TMAP_API_KEY` | 선택: 실제 보행 거리·시간·지도 경로 |
| `GOOGLE_PLACES_API_KEY` | 선택: Places API (New) 정기 영업시간 |
| `MEMORY_DATA_DIR` | 기본 `data/`, SQLite·사진 저장 |
| `PORT` | 기본 `8765` |
| `MEMORY_GEOCODE` | `0`이면 역지오코딩 끔 |

추가 키는 기본적으로 비활성. 각 API 사용 설정·요금·쿼터는 제공자 계정에서 관리.
키를 추가한 뒤 서버 재시작.

HEIC 기본 이미지를 JPEG로 변환해 저장·미리보기·분석. 촬영 시각·GPS EXIF 유지.
원본 바이트 해시로 중복 판정. 손상된 HEIC는 이유를 표시하고 제외.

실제 경로·영업시간 검증은 상위 최대 5개 코스에 적용. 요청 내 중복 호출 재사용,
8초 HTTP 타임아웃, 추가 검증은 20초 경과 후 새 호출 중단(진행 중 호출은 최대 8초 추가).
API 실패·키 누락·시간 제한 시 미확인으로 표시. 실제 보행 경로는 실선,
확인되지 않은 두 장소 연결은 점선·직선거리로 표시.

Google Places는 상호명 일치·좌표 100m 내·단일 일치 시에만 정기 영업시간 사용.
Google Maps 출처 링크 표시. 실제 보행 시간 확인 시 남은 시간을 장소에 균등 배분해
예상 체류시간 전체를 정기 영업시간과 대조. 정기 시간 밖이거나 보행만으로
사용 가능한 시간을 소진하는 코스, 장소 간 보행 거리 3km 초과 코스는 제외.
예약·임시휴무·공휴일 특례·실제 체류시간은 확인하지 않음. 방문 가능성을 보장하지 않음.
실제 경로가 없으면 방문 시각 영업 여부도 미확인으로 유지하며 정기 시간표만 표시.
추천 점수의 거리 항목은 기존 직선거리 기준을 유지하고 결과에서 실제 보행 거리로 보완.
추가 검증에서 탈락한 대표 코스의 대체 장소 쌍은 이번 요청에서 자동 재검증하지 않음.

## 검증

```sh
.venv/bin/python -m unittest discover -s tests -v
node --check static/app.js
```

## API 문서

- [pillow-heif Pillow plugin](https://pillow-heif.readthedocs.io/en/stable/pillow-plugin.html)
- [TMAP 보행자 경로안내](https://tmap-skopenapi.readme.io/reference/보행자-경로안내)
- [Google Places Text Search](https://developers.google.com/maps/documentation/places/web-service/text-search)
- [Google Places 영업시간 데이터](https://developers.google.com/maps/documentation/places/web-service/reference/rest/v1/places#OpeningHours)

## 운영 링크와 통합 배포

- [서비스](https://juhwan.ing/snaplink/)
- [발표 슬라이드](https://juhwan.ing/slides/)

발표와 서비스는 하나의 저장소에서 관리합니다. 환경 변수 예시는 [.env.example](.env.example), 통합 서버 배포 방법은 [최상위 README](../README.md)를 확인하세요. 실데이터·API 키는 저장소에 포함하지 않습니다. 현재는 계정 인증이 없는 해커톤 시연용 MVP입니다.
