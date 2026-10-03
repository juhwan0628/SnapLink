const state = {
  meta: null,
  groups: [],
  group: null,
  edits: {},
  view: "memory",
  novelty: 0.6,
  plan: { date: "", startTime: "", durationHours: "3", locationChoice: "here", radiusKm: "5" },
  conditionsOpen: false,
  planTouched: false,
  moreCourses: false,
  selectedCourse: null,
  rec: null,
  pendingFiles: [],
  skipped: [],
  duplicateCount: 0,
  savedNote: "",
  importBatch: "",
  importPhase: "",
  importCount: 0,
  phaseTimer: null,
  previousOpen: false,
  menuOpen: null,
  activityEdit: null,
  detailEdit: null,
  toast: "",
  toastTimer: null,
  busy: false,
  error: "",
  openEdit: null,
  groupMenuOpen: false,
  createOpen: false,
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  let body = options.body;
  if (body && !(body instanceof FormData) && typeof body !== "string") {
    body = JSON.stringify(body);
    headers.set("Content-Type", "application/json");
  }
  let response;
  try {
    response = await fetch(path, { method: options.method || "GET", headers, body });
  } catch (error) {
    throw new Error("서버에 연결하지 못했습니다. memory 폴더에서 py -3 server.py 가 실행 중인지 확인해 주세요. " + error.message);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `서버가 HTTP ${response.status}을 반환했고, 원인 문구는 없습니다.`);
  }
  return data;
}

function editsFrom(group) {
  const edits = {};
  for (const outing of group.outings) {
    edits[outing.id] = {
      areaName: outing.areaName || "",
      placesText: outing.places.map((place) => place.name).join(", "),
      activities: outing.activities.map((activity) => activity.type).filter((type) => isUserActivity(type)),
      tags: outing.tags.slice(),
      feedback: outing.feedback || "",
      rating: outing.rating,
    };
  }
  return edits;
}

function remember(group) {
  state.group = group;
  state.edits = editsFrom(group);
  state.openEdit = null;
  state.menuOpen = null;
  state.activityEdit = null;
  state.detailEdit = null;
  state.importBatch = localStorage.getItem("gem.batch." + group.id) || "";
  localStorage.setItem("gem.group", group.id);
}

async function refreshGroups() {
  state.groups = await api("/snaplink/api/groups");
}

function destroyMap() {
  if (state.map) {
    state.map.remove();
    state.map = null;
  }
}

function renderApp(keepScroll) {
  const y = keepScroll ? window.scrollY : 0;
  destroyMap();
  document.getElementById("app").innerHTML = `<div class="app">${appHeader()}${main()}${bottomNav()}${createModal()}</div>`;
  window.scrollTo(0, y);
  mountMap();
  if (state.createOpen) {
    const field = document.querySelector('#create-group [name="name"]');
    if (field) field.focus();
  }
}

function icon(name) {
  const paths = {
    book: '<path d="M5 5.5A2.5 2.5 0 0 1 7.5 3H19v14H7.5A2.5 2.5 0 0 0 5 19.5z"/><path d="M5 19.5A2.5 2.5 0 0 1 7.5 17H19"/>',
    image: '<rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="9" cy="10" r="1.4"/><path d="M20 15.5 15.2 11 8 18"/>',
    compass: '<circle cx="12" cy="12" r="8"/><path d="m15.2 8.8-1.7 5-5 1.7 1.7-5z"/>',
    users: '<path d="M16 19.5v-1.2a3 3 0 0 0-3-3H7a3 3 0 0 0-3 3v1.2"/><circle cx="10" cy="8" r="2.6"/><path d="M20 19.5v-1.1a2.8 2.8 0 0 0-2.1-2.7"/><path d="M16 5.6a2.4 2.4 0 0 1 0 4.6"/>',
    chevron: '<path d="m14 6-6 6 6 6"/>',
    chevronDown: '<path d="m6 9 6 6 6-6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
  };
  return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${paths[name]}</svg>`;
}

function appHeader() {
  const current = state.group;
  const switchName = current ? current.name : (state.groups.length ? "그룹 선택" : "그룹 만들기");
  const items = state.groups.map((group) => `
    <button type="button" role="menuitem" class="menu-item${current && current.id === group.id ? " active" : ""}" data-action="open-group" data-id="${group.id}">
      ${esc(group.name)}
    </button>`).join("");
  return `
    <header class="app-header">
      <div class="header-inner">
        <div class="brand"><span class="brand-mark" aria-hidden="true">${icon("image")}</span><span class="brand-name">SnapLink</span></div>
        <div class="header-actions">
          <div class="group-switch">
            <button type="button" class="switch-btn" data-action="toggle-groups" aria-label="그룹 선택" aria-haspopup="menu" aria-expanded="${state.groupMenuOpen}">
              ${icon("users")}<span class="switch-name">${esc(switchName)}</span><span class="switch-caret">${icon("chevronDown")}</span>
            </button>
            ${state.groupMenuOpen ? `<div class="group-menu" role="menu">${items || `<p class="menu-empty">아직 그룹이 없어요</p>`}<div class="sep"></div><button type="button" role="menuitem" class="menu-item" data-action="open-create">${icon("plus")}새 그룹 만들기</button></div>` : ""}
          </div>
          <button type="button" class="new-group icon-btn" data-action="open-create" title="새 그룹" aria-label="새 그룹">${icon("plus")}</button>
        </div>
      </div>
    </header>`;
}

function bottomNav() {
  return `<nav class="bottom-nav" aria-label="주요 메뉴"><div class="bottom-nav-inner">
    ${navItem("memory", "book", "기록")}
    ${navItem("import", "image", "사진 가져오기", draftCount())}
    ${navItem("next", "compass", "다음 경험")}
  </div></nav>`;
}

function navItem(view, glyph, label, count = 0) {
  const active = state.group && state.view === view;
  return `<button type="button" class="nav-btn${active ? " active" : ""}" data-action="view" data-view="${view}" aria-label="${label}" ${active ? 'aria-current="page"' : ""} ${!state.group ? "disabled" : ""}>
    <span class="nav-icon">${icon(glyph)}${count ? `<span class="badge" aria-label="초안 ${count}개">${count}</span>` : ""}</span>
    <span class="nav-label">${label}</span>
  </button>`;
}

function createModal() {
  if (!state.createOpen) return "";
  const err = state.error ? `<p class="error">${esc(state.error)}</p>` : "";
  return `
    <div class="modal-back">
      <form id="create-group" class="modal" role="dialog" aria-modal="true" aria-labelledby="create-title">
        <h2 id="create-title">새 그룹 만들기</h2>
        ${err}
        <label class="field"><span>그룹 이름</span><input name="name" required maxlength="40" autocomplete="off"></label>
        <label class="field"><span>함께하는 사람</span><input name="members" placeholder="지수, 민재, 하준" required autocomplete="off"></label>
        <div class="modal-actions">
          <button type="button" class="ghost" data-action="close-create">취소</button>
          <button class="primary">만들기</button>
        </div>
      </form>
    </div>`;
}

function draftCount() {
  if (!state.group) return 0;
  return state.group.outings.filter((outing) => outing.status === "draft").length;
}

function main() {
  if (!state.group) return `<main>${banner()}${landingHead()}${landing()}</main>`;
  const body = state.view === "import" ? importView() : state.view === "next" ? nextView() : memoryView();
  return `<main>${banner()}${toast()}${pageHead()}${body}</main>`;
}

function banner() {
  if (!state.error || state.createOpen) return "";
  return `<p class="error"><strong>이 단계에서 멈추었습니다.</strong> ${esc(state.error)}</p>`;
}

function pageCopy() {
  if (state.view === "import") return { kicker: "사진 가져오기", sub: "지난 경험 불러오기" };
  if (state.view === "next") return { kicker: "다음 경험", sub: "이번엔 뭐 하지?" };
  return { kicker: "기록", sub: "함께한 경험" };
}

function profileLabel() {
  const names = state.group.members.map((member) => member.name).filter(Boolean);
  if (!names.length) return { shown: "", full: "" };
  const shown = names.length > 2 ? `${names[0]} 외 ${names.length - 1}명` : names.join(" · ");
  return { shown, full: names.join(" · ") };
}

function pageHead() {
  const copy = pageCopy();
  const profile = profileLabel();
  return `
    <header class="page-head">
      <div class="page-titles">
        <p class="kicker">${esc(copy.kicker)}</p>
        <h2>${esc(copy.sub)}</h2>
        <p class="page-sub">${esc(state.group.name)}의 함께한 순간</p>
      </div>
      ${profile.shown ? `<p class="profile" title="${esc(profile.full)}">${esc(profile.shown)}</p>` : ""}
    </header>`;
}

function landingHead() {
  return `
    <header class="page-head">
      <div class="page-titles">
        <p class="eyebrow">기억에서 시작하는 다음 경험</p>
        <h2>우리, 다음엔<br><span class="accent-text">뭐 하지?</span></h2>
        <p class="page-sub">사진 속 함께한 기억으로, 다음 만남을 찾아요.</p>
      </div>
    </header>`;
}

function landing() {
  return `
    <section class="prose landing-copy">
      <button type="button" class="primary" data-action="open-create">첫 그룹 만들기 ${icon("plus")}</button>
      <p>사진을 올리면 촬영 시각과 위치로 같은 외출을 묶습니다. 각 외출에서 식사, 카페, 전시처럼 무엇을 했는지를 남깁니다.</p>
      <p>그 기록이 쌓이면, 같은 사람들이 아직 함께 해보지 않은 활동 조합을 제안합니다. 가지 않은 가게 목록이 아니라, 해보지 않은 경험입니다.</p>
      <p class="muted">장소는 좌표가 있으면 동네 이름까지 두고, 활동은 확인한 뒤에 저장합니다. 얼굴로 동행자를 찾지는 않습니다.</p>
    </section>`;
}

function memoryView() {
  const confirmed = state.group.outings.filter((outing) => outing.status === "confirmed").sort(byStartDesc);
  const points = mapPoints();
  const mapBlock = points.length
    ? `<section class="map-block"><p class="section-label">기록된 외출의 위치</p><p class="muted">좌표가 있는 외출의 중심점입니다. 가게 위치를 확정한 값은 아닙니다.</p><div id="map"></div></section>`
    : `<p class="muted">위치 좌표가 있는 확정 기록이나 초안이 생기면 여기에 지도가 납니다.</p>`;
  const list = confirmed.length
    ? confirmed.map((outing) => experienceCard(outing, null)).join("")
    : `<p>아직 기록된 경험이 없습니다. 사진을 가져오면 여기에 쌓입니다.</p>`;
  return `${mapBlock}<section><h3>확정된 경험</h3>${list}</section>`;
}

function importView() {
  const screenTitle = "지난 경험 불러오기";
  if (state.busy && state.importPhase) return importProgress();
  const selected = state.pendingFiles.length;
  const { current, previous } = splitDrafts();
  const analyze = selected
    ? `<button class="primary" ${state.busy ? "disabled" : ""}>사진 분석하기</button>`
    : `<button class="primary" type="button" disabled>사진 분석하기</button>`;
  const notice = importNotices();
  const cards = current.map((outing, index) => experienceCard(outing, current[index + 1])).join("");
  const older = previous.length ? previousDrafts(previous) : "";
  return `
    <section class="prose" aria-label="${esc(screenTitle)}">
      <p>사진을 선택하면 날짜·위치·사진 내용을 바탕으로 함께한 경험을 자동으로 정리해드려요.</p>
      <form id="import-form" class="stack">
        <input id="photos" class="file-input" type="file" accept="image/*" multiple ${state.busy ? "disabled" : ""}>
        <div class="row">
          <button type="button" class="secondary" data-action="pick-photos" ${state.busy ? "disabled" : ""}>사진 선택하기</button>
          ${analyze}
        </div>
        ${selected ? `<p class="muted">사진 ${selected}장을 선택했어요.</p>` : ""}
      </form>
      ${notice}
      ${debugMode() ? importDebug() : ""}
    </section>
    ${current.length ? `<section><p>${esc(reviewSummary(current))}</p>${cards}</section>` : ""}
    ${older}`;
}

function nextView() {
  const percent = Math.round(state.novelty * 100);
  const conditions = state.conditionsOpen ? `
    <div class="plan">
      <label class="field"><span>날짜</span><input name="date" data-plan="date" type="date" value="${esc(state.plan.date)}" required></label>
      <label class="field"><span>시작 시각</span><input name="startTime" data-plan="startTime" type="time" value="${esc(state.plan.startTime)}" required></label>
      <label class="field"><span>소요 시간</span><input name="durationHours" data-plan="durationHours" type="number" min="0.5" max="24" step="0.5" value="${esc(state.plan.durationHours)}" required></label>
      <label class="field"><span>위치</span>
        <select name="locationChoice" data-plan="locationChoice">
          <option value="here" ${state.plan.locationChoice === "here" ? "selected" : ""}>현재 위치</option>
          <option value="hongdae" ${state.plan.locationChoice === "hongdae" ? "selected" : ""}>홍대 근처</option>
          <option value="seongsu" ${state.plan.locationChoice === "seongsu" ? "selected" : ""}>성수 근처</option>
        </select>
      </label>
      <label class="field"><span>반경 km</span><input name="radiusKm" data-plan="radiusKm" type="number" min="0.5" max="20" step="0.5" value="${esc(state.plan.radiusKm)}"></label>
    </div>
    <div class="slider-row">
      <span>익숙한 경험</span>
      <input id="novelty" name="novelty" type="range" min="0" max="1" step="0.05" value="${state.novelty}" aria-label="익숙한 경험과 새로운 경험 사이">
      <span>새로운 경험</span>
    </div>
    <p id="novelty-label">새로움 ${percent}%</p>` : "";
  return `
    <form id="recommend-form" class="stack ask">
      <div class="row">
        <button class="primary" ${state.busy ? "disabled" : ""}>${state.busy ? "찾는 중" : "추천받기"}</button>
        <button type="button" class="secondary" data-action="toggle-conditions" ${state.busy ? "disabled" : ""}>${state.conditionsOpen ? "조건 닫기" : "조건 바꾸기"}</button>
      </div>
      ${conditions}
    </form>
    ${state.rec ? recommendationResults() : `<p class="muted">${state.busy ? "주변 장소를 찾고 있습니다." : "그룹 인원, 지금 시각, 현재 위치 5km를 기본으로 찾습니다."}</p>`}`;
}

function experienceCard(outing, next) {
  const edit = state.edits[outing.id] || {};
  const area = edit.areaName || outing.areaName;
  const needs = needsActivity(outing.id);
  const showChoices = needs || state.activityEdit === outing.id;
  const line = experienceLine(outing);
  const mood = atmosphere(outing);
  const choices = showChoices ? `
    <p>어떤 활동이었나요?</p>
    ${needs ? `<p class="muted">사진만으로 활동을 확인하기 어려워요.</p>` : ""}
    <div class="chips">${userActivities().map((activity) => chip(
      outing.id,
      "pick-activity",
      activity.id,
      activity.label,
      (edit.activities || []).includes(activity.id),
    )).join("")}</div>` : `
    <div class="card-line">
      <div>
        ${line ? `<p>${esc(line)}</p>` : ""}
        ${mood ? `<p class="muted">${esc(mood)}</p>` : ""}
      </div>
      <button type="button" class="heart" data-action="like" data-id="${outing.id}" aria-label="${outing.liked ? "좋았던 경험" : "선호 표시 없음"}" ${state.busy ? "disabled" : ""}>${outing.liked ? "♥" : "♡"}</button>
    </div>`;
  return `
    <article class="draft">
      <div class="draft-head">
        <h3>${esc(dayLabel(outing))}${area ? ` · ${esc(area)}` : ""}</h3>
        <button type="button" class="icon-btn" data-action="menu" data-id="${outing.id}" aria-label="더보기" ${state.busy ? "disabled" : ""}>⋯</button>
      </div>
      ${state.menuOpen === outing.id ? draftMenu(outing, next) : ""}
      ${reviewPhotos(outing.photos)}
      ${choices}
      ${state.detailEdit === outing.id ? detailEditor(outing) : ""}
      ${debugMode() ? draftDebug(outing) : ""}
    </article>`;
}

function draftMenu(outing, next) {
  const merge = next
    ? `<button type="button" data-action="merge" data-id="${next.id}" data-into="${outing.id}">다음 경험과 합치기</button>`
    : "";
  return `
    <div class="menu">
      <button type="button" data-action="edit-activity" data-id="${outing.id}">활동 수정</button>
      <button type="button" data-action="edit-detail" data-id="${outing.id}">상세 수정</button>
      ${merge}
      <button type="button" data-action="delete" data-id="${outing.id}">이 경험 제외</button>
    </div>`;
}

function importProgress() {
  const phases = [
    ["reading", "날짜와 위치 확인"],
    ["seeing", "사진 내용 분석"],
    ["grouping", "함께한 경험 정리"],
  ];
  const items = phases.map(([id, label]) => `<li class="${state.importPhase === id ? "on" : ""}">${label}</li>`).join("");
  return `
    <section class="prose">
      <p>사진 ${state.importCount}장을 정리하고 있어요...</p>
      <ul class="progress-list">${items}</ul>
    </section>`;
}

function importNotices() {
  const lines = [];
  if (state.savedNote) lines.push(`<p>${esc(state.savedNote)}</p>`);
  if (state.duplicateCount) lines.push(`<p>이미 가져온 사진 ${state.duplicateCount}장은 제외했어요.</p>`);
  if (state.skipped.length) lines.push(`<p class="muted">가져오지 못한 사진 ${state.skipped.length}장이 있어요.</p>`);
  if (debugMode()) {
    lines.push(state.skipped.map((item) => `<p class="muted">${esc(item.filename)}: ${esc(item.reason)}</p>`).join(""));
  }
  return lines.join("");
}

function importDebug() {
  const cluster = state.meta.cluster || {};
  return `<p class="muted">debug · ${esc(cluster.timeSplitHours)}시간 / ${esc(cluster.distanceGapMinutes)}분 / ${esc(cluster.distanceSplitKm)}km · vision ${state.meta.visionEnabled ? "on" : "off"}</p>`;
}

function splitDrafts() {
  const outings = state.group.outings.slice().sort(byStartAsc);
  const batch = state.importBatch;
  return {
    current: batch ? outings.filter((outing) => outing.importBatch === batch) : [],
    previous: outings.filter((outing) => outing.status === "draft" && outing.importBatch !== batch),
  };
}

function previousDrafts(previous) {
  const label = state.previousOpen ? "접기" : `이전에 확인하지 않은 경험 ${previous.length}개`;
  const cards = state.previousOpen
    ? previous.map((outing, index) => experienceCard(outing, previous[index + 1])).join("")
    : "";
  return `
    <section>
      <button type="button" class="ghost" data-action="toggle-previous">${label}</button>
      ${cards}
    </section>`;
}

function reviewSummary(current) {
  const needs = current.filter((outing) => needsActivity(outing.id)).length;
  const found = `${current.length}개의 지난 경험을 찾았어요.`;
  if (!needs) return found;
  if (needs === current.length) return `${found} 활동을 하나 선택하면 기록에 남아요.`;
  return `${found} ${needs}개는 활동을 하나 선택하면 돼요.`;
}

function userActivities() {
  const listed = (state.meta && state.meta.userActivities) || [];
  if (listed.length) return listed;
  return ((state.meta && state.meta.activities) || []).filter((activity) => activity.id !== "market" && activity.id !== "nature");
}

function isUserActivity(type) {
  return userActivities().some((activity) => activity.id === type);
}

function needsActivity(id) {
  const edit = state.edits[id];
  if (!edit) return true;
  return !edit.activities.some((type) => isUserActivity(type));
}

function activityLabel(type) {
  const found = userActivities().concat((state.meta && state.meta.activities) || []).find((activity) => activity.id === type);
  return found ? found.label : type;
}

function placeLabel(outing) {
  const name = outing.places && outing.places[0] ? String(outing.places[0].name).trim() : "";
  if (!name) return "";
  const labels = {
    bakery: "베이커리",
    restaurant: "음식점",
    cafe: "카페",
    market: "시장",
    park: "공원",
    landmark: "장소",
    museum: "미술관",
    gallery: "갤러리",
    shop: "상점",
    bar: "바",
  };
  return labels[name.toLowerCase()] || name;
}

function experienceLine(outing) {
  const edit = state.edits[outing.id];
  const labels = ((edit && edit.activities) || []).filter(isUserActivity).map(activityLabel);
  const place = placeLabel(outing);
  if (place && !labels.includes(place)) labels.push(place);
  return labels.join(" · ");
}

function atmosphere(outing) {
  const edit = state.edits[outing.id];
  return ((edit && edit.tags) || outing.tags || []).slice(0, 2).join(" · ");
}

function dayLabel(outing) {
  if (!outing.startTime) return "날짜 없음";
  const [year, month, day] = outing.startTime.slice(0, 10).split("-");
  return `${year}.${month}.${day}`;
}

function reviewPhotos(photos) {
  const shown = (photos || []).slice(0, 4);
  if (!shown.length) return "";
  return `<div class="film">${shown.map((photo) => `
    <figure>
      <img src="${esc(photo.url)}" alt="">
      ${photo.takenAt ? `<figcaption>${esc(photo.takenAt.slice(11, 16))}</figcaption>` : ""}
    </figure>`).join("")}</div>`;
}

function draftDebug(outing) {
  const photos = (outing.photos || []).map((photo) => `${photo.filename || ""} ${photo.hasGps ? "gps" : "no-gps"}`).join(", ");
  const activities = (outing.activities || []).map((activity) => `${activity.type}:${activity.source}:${activity.confidence}`).join(", ");
  return `<p class="muted">debug · ${esc(outing.status || "")} · ${esc(outing.inferenceSource || "")} · ${esc(outing.inferenceNote || "")} · ${esc(outing.splitNote || "")} · ${esc(activities)} · ${esc(photos)}</p>`;
}

function detailEditor(outing) {
  const edit = state.edits[outing.id];
  const dis = state.busy ? "disabled" : "";
  const tags = state.meta.tags.map((tag) => chip(outing.id, "toggle-tag", tag, tag, edit.tags.includes(tag))).join("");
  return `
    <div class="detail">
      <div class="field"><span>지역</span><input data-field="areaName" data-id="${outing.id}" value="${esc(edit.areaName)}" ${dis}></div>
      <div class="field"><span>장소</span><input data-field="placesText" data-id="${outing.id}" value="${esc(edit.placesText)}" ${dis}></div>
      <div class="field"><span>분위기</span><div class="chips">${tags}</div></div>
      <div class="field"><span>메모</span><textarea data-field="feedback" data-id="${outing.id}" ${dis}>${esc(edit.feedback)}</textarea></div>
      ${coordLink(outing)}
      <button type="button" class="secondary" data-action="close-detail" data-id="${outing.id}" ${dis}>닫기</button>
    </div>`;
}

function toast() {
  if (!state.toast) return "";
  return `<p class="toast" role="status">${esc(state.toast)}</p>`;
}

function showToast(message) {
  state.toast = message;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => {
    state.toast = "";
    renderApp(true);
  }, 1400);
}

function chip(id, action, value, label, on) {
  return `<button class="chip${on ? " on" : ""}" data-action="${action}" data-id="${id}" data-value="${esc(value)}" ${state.busy ? "disabled" : ""}>${esc(label)}</button>`;
}

function signed(value) {
  const number = Number(value);
  const text = number.toFixed(2);
  return number > 0 ? `+${text}` : text;
}

function recCard(item, index) {
  const scores = [
    ["preference", "선호"],
    ["novelty", "새로움"],
    ["context", "상황"],
    ["group", "그룹"],
    ["repetition", "반복"],
    ["total", "합"],
  ].map(([key, label]) => `
    <div class="${key === "total" ? "total" : ""}"><dt>${label}</dt><dd>${signed(item.scores[key])}</dd></div>`).join("");
  const reasons = item.reasons.map((reason) => `<li>${esc(reason)}</li>`).join("");
  return `
    <article class="rec">
      <p class="rank">${index + 1}</p>
      <div>
        <span class="mode${item.novelCombo ? " novel" : ""}">${esc(item.modeLabel)}</span>
        <h3>${esc(item.title)}</h3>
        <p>${esc(item.arrow)}</p>
        <ul class="reasons">${reasons}</ul>
        ${item.eventSchedule ? `<p class="warn">${esc(item.eventSchedule)}</p>` : ""}
        <dl class="scores">${scores}</dl>
      </div>
    </article>`;
}

function notice(outing) {
  const text = [outing.splitNote, outing.inferenceNote].filter(Boolean).join(" ");
  return text ? `<div class="notice">${esc(text)}</div>` : "";
}

function photosHtml(photos) {
  if (!photos.length) return "";
  return `<div class="film">${photos.map((photo) => `
    <figure>
      <img src="${esc(photo.url)}" alt="${esc(photo.filename)}">
      <figcaption>${photo.takenAt ? esc(photo.takenAt.slice(11, 16)) : "시각 없음"} · ${photo.hasGps ? "위치 있음" : "위치 없음"}${photo.format === "heic" ? " · 형식 미지원" : ""}</figcaption>
    </figure>`).join("")}</div>`;
}

function placeLine(outing) {
  if (!outing.places.length) return "";
  return `<p class="meta">${esc(outing.places.map((place) => place.name).join(", "))}</p>`;
}

function coordLink(outing) {
  if (outing.areaLat == null || outing.areaLng == null) return "";
  const href = `https://www.openstreetmap.org/?mlat=${outing.areaLat}&mlon=${outing.areaLng}#map=16/${outing.areaLat}/${outing.areaLng}`;
  return `<p class="meta"><a href="${href}" target="_blank" rel="noreferrer">좌표를 지도에서 보기</a></p>`;
}

function realActivities(outing) {
  return (outing.activities || []).filter((activity) => activity.type !== "unknown");
}

function analyzedActivity(outing) {
  const real = realActivities(outing);
  if (!real.length) return "unknown";
  return real.map((activity) => `${activity.label} (${activity.source})`).join(", ");
}

function arrow(outing) {
  const real = realActivities(outing);
  if (!real.length) return "활동 unknown";
  return real.map((activity) => activity.label).join(" → ");
}

function whenLabel(outing) {
  if (!outing.startTime) return "날짜 없음";
  const end = outing.endTime && outing.endTime !== outing.startTime ? `–${outing.endTime.slice(11, 16)}` : "";
  return `${outing.startTime.slice(0, 16).replace("T", " ")}${end}`;
}

function byStartAsc(a, b) {
  return (a.startTime || "9999").localeCompare(b.startTime || "9999");
}
function byStartDesc(a, b) {
  return byStartAsc(b, a);
}

function mapPoints() {
  return state.group.outings
    .filter((outing) => outing.areaLat != null && outing.areaLng != null)
    .map((outing) => ({
      lat: outing.areaLat,
      lng: outing.areaLng,
      title: `${outing.areaName || "이름 없는 위치"} · ${arrow(outing)}`,
      draft: outing.status === "draft",
    }));
}

function courseHeading() {
  const today = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  const stamp = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
  if (state.rec.plan && state.rec.plan.date === stamp) return "오늘 이 그룹에게 추천하는 코스";
  return "이 그룹에게 추천하는 코스";
}

function debugMode() {
  return new URLSearchParams(window.location.search).get("debug") === "1";
}

function recommendationResults() {
  const courses = state.rec.courses || [];
  if (courses.length) {
    const visible = state.moreCourses ? courses.slice(0, 5) : courses.slice(0, 3);
    const more = !state.moreCourses && courses.length > 3
      ? `<button type="button" class="secondary" data-action="more-courses">다른 추천 보기</button>`
      : "";
    return `
      <section class="wide">
        <h3>${esc(courseHeading())}</h3>
        <div id="rec-map"></div>
        ${visible.map((course, index) => courseCard(course, index)).join("")}
        ${more}
        ${debugBlock()}
      </section>`;
  }
  return `
    <section>
      <p>주변 장소를 찾지 못해, 이번엔 경험만 제안합니다.</p>
      ${(state.rec.items || []).slice(0, 3).map((item) => `
        <article class="rec">
          <div>
            <h3>${esc(item.arrow)}</h3>
            ${item.eventSchedule ? `<p class="muted">행사 일정은 확인하지 못했어요.</p>` : ""}
          </div>
        </article>`).join("")}
      ${debugBlock()}
    </section>`;
}

function courseCard(course, index) {
  const selected = state.selectedCourse === course.id;
  const stops = course.stops || [];
  const labels = stops.map((stop) => stop.activityLabel).join(" → ");
  const places = stops.map((stop) => `<li><strong>${esc(stop.place.name)}</strong></li>`).join("");
  const scheduleDetails = stops.map((stop) => `
    ${stop.arrivalTime ? `<p class="muted">${esc(stop.place.name)} · 예상 도착 ${esc(stop.arrivalTime.slice(11, 16))} · 도보 약 ${esc(Math.ceil(stop.durationMinutes))}분</p>` : ""}
    ${stop.openingHours ? `<details><summary>${esc(stop.place.name)} 정기 영업시간 · Google Maps</summary>${(stop.openingHours.weekdayDescriptions || []).map((line) => `<p>${esc(line)}</p>`).join("")}${stop.openingHours.sourceUrl ? `<a href="${esc(stop.openingHours.sourceUrl)}" target="_blank" rel="noreferrer">영업시간 출처</a>` : ""}</details>` : ""}`).join("");
  const reasons = (course.memoryEvidence || []).map((line) => `<p>${esc(line)}</p>`).join("");
  const between = stops[1] ? distanceText(stops[1].distanceFromPreviousM) : "";
  const fromHere = stops[0] ? distanceText(stops[0].place.distanceM) : "";
  const where = state.plan.locationChoice === "here" ? "현재 위치" : "검색 위치";
  const link = mapLink(stops[0] && stops[0].place);
  return `
    <article class="rec course${selected ? " selected" : ""}">
      <p class="rank">${index + 1}</p>
      <div>
        <button class="text" data-action="select-course" data-id="${esc(course.id)}"><h3>${esc(labels || course.arrow)}</h3></button>
        <p class="muted">${esc(durationText(state.rec.plan.durationHours))} · ${esc(where)}에서 ${esc(fromHere)}${between ? ` · 두 장소 사이 ${esc(between)}` : ""}</p>
        <ol class="reasons">${places}</ol>
        ${scheduleDetails}
        ${reasons}
        ${link}
        <p class="muted">${esc(course.validation ? course.validation.note : "직선거리 · 영업시간·보행 경로 미확인")}</p>
        ${debugMode() ? debugScores(course.scores) : ""}
      </div>
    </article>`;
}

function distanceText(meters) {
  const value = Number(meters);
  if (!Number.isFinite(value)) return "";
  if (value < 1000) return `약 ${Math.max(1, Math.round(value / 10) * 10)}m`;
  return `약 ${(value / 1000).toFixed(1)}km`;
}

function durationText(hours) {
  const value = Number(hours);
  if (!Number.isFinite(value) || value <= 0) return "";
  const whole = Math.floor(value);
  const minutes = Math.round((value - whole) * 60);
  if (!minutes) return `약 ${whole}시간`;
  if (!whole) return `약 ${minutes}분`;
  return `약 ${whole}시간 ${minutes}분`;
}

function mapLink(place) {
  if (!place) return "";
  const href = place.detailUrl || `https://map.kakao.com/link/map/${encodeURIComponent(place.name)},${place.lat},${place.lng}`;
  return `<p><a href="${esc(href)}" target="_blank" rel="noreferrer">지도에서 보기</a></p>`;
}

function debugBlock() {
  if (!debugMode() || !state.rec) return "";
  return `<details><summary>추천 계산</summary><p class="formula">${esc(state.rec.formula || "")}</p><p class="muted">${esc(state.rec.weightNote || "")}</p></details>`;
}

function debugScores(scores) {
  if (!scores) return "";
  const rows = Object.entries(scores).map(([key, value]) => `<div><dt>${esc(key)}</dt><dd>${esc(value)}</dd></div>`).join("");
  return `<dl class="scores scores-course">${rows}</dl>`;
}

function mountMap() {
  mountRecordMap();
  mountCourseMap();
}

function mountRecordMap() {
  const el = document.getElementById("map");
  if (!el || !state.group) return;
  const points = mapPoints();
  if (!points.length) return;
  if (!window.L) {
    el.className = "map-fallback";
    el.innerHTML = "<p>지도 라이브러리를 불러오지 못했습니다. 각 기록의 좌표 링크는 사용할 수 있습니다.</p>";
    return;
  }
  const map = L.map(el);
  state.map = map;
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap",
    maxZoom: 18,
  }).addTo(map);
  const latlngs = [];
  for (const point of points) {
    latlngs.push([point.lat, point.lng]);
    L.circleMarker([point.lat, point.lng], {
      radius: 8,
      color: point.draft ? "#8a4b12" : "#285bc5",
      weight: 2,
      fillColor: "#ffffff",
      fillOpacity: 1,
    }).bindPopup(esc(point.title)).addTo(map);
  }
  if (latlngs.length === 1) map.setView(latlngs[0], 14);
  else map.fitBounds(latlngs, { padding: [28, 28], maxZoom: 15 });
  setTimeout(() => map.invalidateSize(), 0);
}

function mountCourseMap() {
  const el = document.getElementById("rec-map");
  if (!el || !state.rec) return;
  const course = (state.rec.courses || []).find((item) => item.id === state.selectedCourse) || (state.rec.courses || [])[0];
  const markers = course ? course.stops.map((stop) => ({
    order: stop.order,
    name: stop.place.name,
    category: stop.place.category || stop.activityLabel,
    address: stop.place.address || "주소 정보 없음",
    lat: stop.place.lat,
    lng: stop.place.lng,
  })).filter((marker) => marker.lat != null && marker.lng != null) : [];
  if (!markers.length) return;
  if (!window.L) {
    el.className = "map-fallback";
    el.innerHTML = `<p>지도 라이브러리를 불러오지 못했습니다.</p><ul>${markers.map((marker) => `<li>${esc(marker.order)}. ${esc(marker.name)} · ${esc(marker.category)} · ${esc(marker.address)}</li>`).join("")}</ul>`;
    return;
  }
  const map = L.map(el);
  state.map = map;
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap",
    maxZoom: 18,
  }).addTo(map);
  const latlngs = [];
  for (const marker of markers) {
    const latlng = [marker.lat, marker.lng];
    latlngs.push(latlng);
    const icon = L.divIcon({
      className: "pin",
      html: `<span>${esc(marker.order)}</span>`,
      iconSize: [28, 28],
      iconAnchor: [14, 14],
    });
    L.marker(latlng, { icon, title: marker.name }).bindPopup(
      `<strong>${esc(marker.order)}. ${esc(marker.name)}</strong><br>${esc(marker.category)}<br>${esc(marker.address)}`
    ).addTo(map);
  }
  if (latlngs.length > 1) {
    const routePath = course.routePath && course.routePath.length ? course.routePath : latlngs;
    L.polyline(routePath, { color: "#285bc5", weight: 3, opacity: 0.8, dashArray: course.routePath ? null : "6 6" }).addTo(map);
    map.fitBounds(routePath, { padding: [28, 28], maxZoom: 16 });
  } else {
    map.setView(latlngs[0], 15);
  }
  setTimeout(() => map.invalidateSize(), 0);
}

function flushEdits() {
  document.querySelectorAll("[data-field][data-id]").forEach((field) => {
    const edit = state.edits[field.dataset.id];
    if (edit) edit[field.dataset.field] = field.value;
  });
}

function detailPayload(id) {
  const edit = state.edits[id];
  return {
    areaName: edit.areaName,
    places: edit.placesText.split(",").map((part) => part.trim()).filter(Boolean),
    tags: edit.tags,
    feedback: edit.feedback,
  };
}

function payload(id) {
  const edit = state.edits[id];
  return {
    areaName: edit.areaName,
    places: edit.placesText.split(",").map((part) => part.trim()).filter(Boolean),
    activities: edit.activities.filter((type) => isUserActivity(type)),
    tags: edit.tags,
    feedback: edit.feedback,
    rating: edit.rating,
  };
}

async function onClick(event) {
  if (event.target.classList && event.target.classList.contains("modal-back")) {
    state.createOpen = false;
    state.error = "";
    renderApp(true);
    return;
  }
  const button = event.target.closest("[data-action]");
  const inSwitch = event.target.closest(".group-switch");
  let closedMenu = false;
  if (state.groupMenuOpen && !inSwitch) {
    state.groupMenuOpen = false;
    closedMenu = true;
    if (!button || button.disabled) {
      renderApp(true);
      return;
    }
  }
  if (!button || button.disabled) return;
  const action = button.dataset.action;
  const id = button.dataset.id;
  try {
    if (action === "toggle-groups") {
      state.groupMenuOpen = !state.groupMenuOpen;
      renderApp(true);
      return;
    }
    if (action === "open-create") {
      state.createOpen = true;
      state.groupMenuOpen = false;
      state.error = "";
      renderApp(true);
      return;
    }
    if (action === "close-create") {
      state.createOpen = false;
      state.error = "";
      renderApp(true);
      return;
    }
    if (action === "select-course") {
      state.selectedCourse = id;
      renderApp(true);
      return;
    }
    if (action === "toggle-conditions") {
      state.conditionsOpen = !state.conditionsOpen;
      if (state.conditionsOpen) fillPlanDefaults();
      renderApp(true);
      return;
    }
    if (action === "more-courses") {
      state.moreCourses = true;
      renderApp(true);
      return;
    }
    if (action === "pick-photos") {
      if (closedMenu) renderApp(true);
      const input = document.getElementById("photos");
      if (input) input.click();
      return;
    }
    if (action === "menu") {
      state.menuOpen = state.menuOpen === id ? null : id;
      renderApp(true);
      return;
    }
    if (action === "toggle-previous") {
      state.previousOpen = !state.previousOpen;
      renderApp(true);
      return;
    }
    if (action === "pick-activity") {
      state.busy = true;
      state.error = "";
      renderApp(true);
      const group = await api("/snaplink/api/outings/" + id + "/choose", {
        method: "POST",
        body: { activity: button.dataset.value },
      });
      remember(group);
      await refreshGroups();
      state.busy = false;
      showToast("저장했어요");
      renderApp(true);
      return;
    }
    if (action === "like") {
      const outing = state.group.outings.find((item) => item.id === id);
      const group = await api("/snaplink/api/outings/" + id + "/like", {
        method: "POST",
        body: { liked: !(outing && outing.liked) },
      });
      remember(group);
      await refreshGroups();
      renderApp(true);
      return;
    }
    if (action === "edit-activity") {
      state.activityEdit = state.activityEdit === id ? null : id;
      state.detailEdit = null;
      state.menuOpen = null;
      renderApp(true);
      return;
    }
    if (action === "edit-detail") {
      state.detailEdit = state.detailEdit === id ? null : id;
      state.activityEdit = null;
      state.menuOpen = null;
      renderApp(true);
      return;
    }
    if (action === "close-detail") {
      flushEdits();
      state.busy = true;
      state.error = "";
      renderApp(true);
      const group = await api("/snaplink/api/outings/" + id, { method: "PATCH", body: detailPayload(id) });
      remember(group);
      await refreshGroups();
      state.busy = false;
      renderApp(true);
      return;
    }
    if (action === "view") {
      state.view = button.dataset.view;
      state.error = "";
      state.groupMenuOpen = false;
      renderApp(false);
      return;
    }
    if (action === "open-group") {
      remember(await api("/snaplink/api/groups/" + id));
      state.view = "memory";
      state.error = "";
      state.groupMenuOpen = false;
      renderApp(false);
      return;
    }
    if (action === "toggle-activity" || action === "toggle-tag") {
      const edit = state.edits[id];
      const key = action === "toggle-activity" ? "activities" : "tags";
      const index = edit[key].indexOf(button.dataset.value);
      if (index >= 0) edit[key].splice(index, 1);
      else edit[key].push(button.dataset.value);
      renderApp(true);
      return;
    }
    if (action === "rate") {
      const edit = state.edits[id];
      const value = Number(button.dataset.value);
      edit.rating = edit.rating === value ? null : value;
      renderApp(true);
      return;
    }
    if (action === "edit") {
      state.openEdit = state.openEdit === id ? null : id;
      renderApp(true);
      return;
    }
    if (action === "save" || action === "confirm") {
      flushEdits();
      if (action === "confirm" && needsActivity(id)) {
        state.error = "활동을 선택하면 기록에 저장할 수 있어요.";
        renderApp(true);
        return;
      }
      state.busy = true;
      state.error = "";
      renderApp(true);
      let group = await api("/snaplink/api/outings/" + id, { method: "PATCH", body: payload(id) });
      if (action === "confirm") group = await api("/snaplink/api/outings/" + id + "/confirm", { method: "POST", body: {} });
      remember(group);
      await refreshGroups();
      state.busy = false;
      if (action === "confirm") state.savedNote = "이 경험을 기록에 저장했어요. 기록 탭에서 볼 수 있어요.";
      renderApp(true);
      return;
    }
    if (action === "merge") {
      state.busy = true;
      renderApp(true);
      const group = await api("/snaplink/api/outings/" + button.dataset.into + "/merge", {
        method: "POST",
        body: { sourceId: id },
      });
      remember(group);
      await refreshGroups();
      state.busy = false;
      renderApp(true);
      return;
    }
    if (action === "delete") {
      const draft = state.group.outings.some((outing) => outing.id === id && outing.status === "draft");
      const message = draft ? "이 경험을 제외할까요?" : "이 외출 기록과 사진을 삭제할까요?";
      if (!window.confirm(message)) return;
      const group = await api("/snaplink/api/outings/" + id, { method: "DELETE" });
      remember(group);
      await refreshGroups();
      renderApp(true);
    }
  } catch (error) {
    state.busy = false;
    state.error = error.message;
    renderApp(true);
  }
}

async function onSubmit(event) {
  const form = event.target;
  if (form.id !== "create-group" && form.id !== "import-form" && form.id !== "recommend-form") return;
  event.preventDefault();
  try {
    if (form.id === "recommend-form") {
      if (state.conditionsOpen && form.date) {
        state.plan.date = form.date.value;
        state.plan.startTime = form.startTime.value;
        state.plan.durationHours = form.durationHours.value;
        state.plan.locationChoice = form.locationChoice.value;
        state.plan.radiusKm = form.radiusKm.value;
        state.novelty = Number(form.novelty.value);
        state.planTouched = true;
      }
      state.busy = true;
      state.error = "";
      renderApp(true);
      await loadRecommend();
      state.busy = false;
      renderApp(true);
      return;
    }
    if (form.id === "create-group") {
      if (state.busy) return;
      state.busy = true;
      const data = new FormData(form);
      const group = await api("/snaplink/api/groups", {
        method: "POST",
        body: { name: data.get("name"), members: data.get("members") },
      });
      remember(group);
      await refreshGroups();
      state.busy = false;
      state.createOpen = false;
      state.groupMenuOpen = false;
      state.view = "import";
      state.error = "";
      renderApp(false);
      return;
    }
    if (!state.pendingFiles.length) {
      state.error = "사진을 선택해 주세요.";
      renderApp(true);
      return;
    }
    const body = new FormData();
    for (const file of state.pendingFiles) body.append("photos", file, file.name);
    state.importCount = state.pendingFiles.length;
    state.busy = true;
    state.error = "";
    state.duplicateCount = 0;
    state.savedNote = "";
    state.skipped = [];
    startImportPhases();
    renderApp(true);
    const result = await api(`/snaplink/api/groups/${state.group.id}/import`, { method: "POST", body });
    clearImportPhases();
    state.pendingFiles = [];
    state.skipped = result.skipped || [];
    state.duplicateCount = result.duplicateCount || 0;
    if (result.batchId) localStorage.setItem("gem.batch." + state.group.id, result.batchId);
    remember(result.group);
    await refreshGroups();
    state.busy = false;
    state.view = "import";
    state.previousOpen = false;
    renderApp(false);
  } catch (error) {
    clearImportPhases();
    state.busy = false;
    state.error = error.message;
    renderApp(true);
  }
}

function startImportPhases() {
  clearImportPhases();
  state.importPhase = "reading";
  state.phaseTimer = setTimeout(() => {
    state.importPhase = "seeing";
    if (state.busy) renderApp(true);
    state.phaseTimer = setTimeout(() => {
      state.importPhase = "grouping";
      if (state.busy) renderApp(true);
    }, 900);
  }, 700);
}

function clearImportPhases() {
  clearTimeout(state.phaseTimer);
  state.phaseTimer = null;
  state.importPhase = "";
}

function onInput(event) {
  const field = event.target.dataset.field;
  if (field && state.edits[event.target.dataset.id]) {
    state.edits[event.target.dataset.id][field] = event.target.value;
    return;
  }
  if (event.target.dataset.plan) {
    state.plan[event.target.dataset.plan] = event.target.value;
    state.planTouched = true;
  }
  if (event.target.id === "novelty") {
    state.novelty = Number(event.target.value);
    const label = document.getElementById("novelty-label");
    if (label) label.textContent = `새로움 ${Math.round(state.novelty * 100)}%`;
  }
}

async function onChange(event) {
  if (event.target.id === "photos") {
    state.pendingFiles = [...event.target.files];
    renderApp(true);
    return;
  }
}

function fillPlanDefaults() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  if (!state.plan.date) state.plan.date = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  if (!state.plan.startTime) state.plan.startTime = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
  if (!state.plan.durationHours) state.plan.durationHours = "3";
  if (!state.plan.radiusKm) state.plan.radiusKm = "5";
  if (!state.plan.locationChoice) state.plan.locationChoice = "here";
}

function planForRequest() {
  if (state.planTouched) return { ...state.plan, novelty: state.novelty };
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return {
    date: `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`,
    startTime: `${pad(now.getHours())}:${pad(now.getMinutes())}`,
    durationHours: "3",
    locationChoice: state.plan.locationChoice || "here",
    radiusKm: "5",
    novelty: 0.6,
  };
}

async function loadRecommend() {
  if (!state.group) throw new Error("그룹을 먼저 선택해 주세요.");
  const plan = planForRequest();
  const params = new URLSearchParams({
    novelty: String(plan.novelty),
    date: plan.date,
    startTime: plan.startTime,
    durationHours: String(plan.durationHours),
  });
  if (plan.locationChoice === "hongdae" || plan.locationChoice === "seongsu") {
    params.set("locationMode", "area");
    params.set("areaName", plan.locationChoice === "hongdae" ? "홍대" : "성수");
    params.set("searchRadiusKm", String(plan.radiusKm || "5"));
  } else {
    if (!navigator.geolocation) {
      state.conditionsOpen = true;
      fillPlanDefaults();
      throw new Error("현재 위치를 읽을 수 없습니다. 조건에서 홍대나 성수를 고른 뒤 다시 추천받으세요.");
    }
    let position;
    try {
      position = await new Promise((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, { timeout: 8000 });
      });
    } catch (error) {
      state.conditionsOpen = true;
      fillPlanDefaults();
      throw new Error("현재 위치를 허용하지 않았습니다. 조건에서 홍대나 성수를 고른 뒤 다시 추천받으세요.");
    }
    params.set("locationMode", "coordinates");
    params.set("latitude", String(position.coords.latitude));
    params.set("longitude", String(position.coords.longitude));
    params.set("searchRadiusKm", String(plan.radiusKm || "5"));
  }
  state.rec = await api(`/snaplink/api/groups/${state.group.id}/recommend?${params}`);
  state.moreCourses = false;
  state.selectedCourse = state.rec.courses && state.rec.courses[0] ? state.rec.courses[0].id : null;
}

async function init() {
  state.meta = await api("/snaplink/api/meta");
  await refreshGroups();
  const saved = localStorage.getItem("gem.group");
  if (saved && state.groups.some((group) => group.id === saved)) {
    remember(await api("/snaplink/api/groups/" + saved));
  }
  renderApp(false);
}

document.addEventListener("click", onClick);
document.addEventListener("submit", onSubmit);
document.addEventListener("input", onInput);
document.addEventListener("change", onChange);
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (state.createOpen) {
    state.createOpen = false;
    state.error = "";
    renderApp(true);
    return;
  }
  if (state.groupMenuOpen) {
    state.groupMenuOpen = false;
    renderApp(true);
    return;
  }

});
init().catch((error) => {
  document.getElementById("app").textContent = error.message;
});
