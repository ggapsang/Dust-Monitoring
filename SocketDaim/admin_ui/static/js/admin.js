// SocketDaim Admin UI — LOAS-only frontend.
// Legacy standard-mode UI (station register/edit/delete, sample seed, request triage)
// 는 이 빌드에서 제거됨.  backend 라우트는 그대로 살아있지만 UI 에서 호출 안 함.

'use strict';

const REFRESH_MS = 5000;          // status + waypoints + activity
const ERRLOG_MS  = 10000;         // ingestion 에러 로그
let _data = { waypoints: [] };
let _refreshTimer = null;
let _errlogTimer = null;

// ---------------------------------------------------------------------------
// Tiny helpers
// ---------------------------------------------------------------------------

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(url, opts);
  if (!r.ok) {
    let detail = r.statusText;
    try { const j = await r.json(); detail = j.detail || JSON.stringify(j); } catch (_) {}
    throw new Error(method + ' ' + url + ' → ' + r.status + ' ' + detail);
  }
  if (r.status === 204) return null;
  return await r.json();
}

function toast(msg, kind) {
  const c = document.getElementById('toast-container');
  if (!c) return;
  const t = document.createElement('div');
  t.className = 'toast ' + (kind || 'info');
  t.textContent = msg;
  c.appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 3000);
}

function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add('open');
}
function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove('open');
}

function fmtTime(iso) {
  if (!iso) return '-';
  try {
    const d = new Date(iso);
    return d.toLocaleString('ko-KR', { hour12: false });
  } catch (_) { return iso; }
}

function fmtXyz(x, y, z) {
  const f = (n) => (n === null || n === undefined) ? '·' : Number(n).toFixed(3);
  return f(x) + ', ' + f(y) + ', ' + f(z);
}

function fmtPtl(p, t, l) {
  const f = (n) => (n === null || n === undefined) ? '·' : String(n);
  return f(p) + '/' + f(t) + '/' + f(l);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]
  ));
}

// ---------------------------------------------------------------------------
// Status / sidebar
// ---------------------------------------------------------------------------

async function loadStatus() {
  let s;
  try {
    s = await api('GET', '/admin/api/status');
  } catch (e) {
    const db = document.getElementById('st-db');
    if (db) { db.textContent = 'ERROR'; db.className = 'status-badge bad'; }
    return;
  }

  const db = document.getElementById('st-db');
  if (db) {
    db.textContent = s.db_ok ? 'OK' : 'ERROR';
    db.className = 'status-badge ' + (s.db_ok ? 'ok' : 'bad');
  }
  const refr = document.getElementById('st-refreshed');
  if (refr) {
    refr.textContent = 'refreshed: ' +
      new Date(s.now || Date.now()).toLocaleTimeString('ko-KR', { hour12: false });
  }

  // Disk
  if (s.disk && s.disk.ok) {
    document.getElementById('stat-disk').textContent =
        s.disk.used_gb + ' / ' + s.disk.total_gb + ' GB';
    document.getElementById('stat-disk-pct').textContent = s.disk.percent + '%';
    const fill = document.getElementById('stat-disk-fill');
    fill.style.width = Math.min(100, s.disk.percent) + '%';
    fill.className = 'disk-fill';
    if (s.disk.percent >= (s.disk_critical_percent || 90)) fill.classList.add('crit');
    else if (s.disk.percent >= (s.disk_warn_percent || 80)) fill.classList.add('warn');

    const banner = document.getElementById('disk-banner');
    if (s.disk.percent >= (s.disk_critical_percent || 90)) {
      banner.style.display = 'block';
      banner.className = 'disk-banner crit';
      banner.textContent = '⚠ 디스크 위험 — 즉시 정리 필요';
    } else if (s.disk.percent >= (s.disk_warn_percent || 80)) {
      banner.style.display = 'block';
      banner.className = 'disk-banner warn';
      banner.textContent = '⚠ 디스크 경고';
    } else {
      banner.style.display = 'none';
    }
  }
  document.getElementById('stat-db-mb').textContent = (s.database_mb || 0) + ' MB';
}

// ---------------------------------------------------------------------------
// Waypoints (main table)
// ---------------------------------------------------------------------------

async function loadWaypoints() {
  let rows;
  try {
    rows = await api('GET', '/admin/api/waypoints');
  } catch (e) {
    document.getElementById('tbl-body').innerHTML =
        '<tr><td colspan="8" class="empty err">로드 실패: ' + escapeHtml(e.message) + '</td></tr>';
    return;
  }
  _data.waypoints = Array.isArray(rows) ? rows : [];
  renderWaypoints();
  updateWaypointCounters();
}

function renderWaypoints() {
  const tbody = document.getElementById('tbl-body');
  if (!_data.waypoints.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">' +
        '아직 발견된 관측 개소가 없습니다. AMR(13310 DUST) 송신 대기 중...</td></tr>';
    return;
  }
  const html = _data.waypoints.map((r, i) => {
    const labeled = r.label && String(r.label).trim();
    const defaultName = 'TGT-' + r.target_id;
    const nameCell = labeled
      ? '<span class="strong">' + escapeHtml(r.label) + '</span>'
      : '<span class="muted mono">' + escapeHtml(defaultName) + '</span>';
    return '' +
      '<tr data-idx="' + i + '">' +
      '  <td class="mono">' + r.target_id + '</td>' +
      '  <td>' + nameCell + '</td>' +
      '  <td>' + (r.location ? escapeHtml(r.location) : '<span class="muted">-</span>') + '</td>' +
      '  <td class="num mono">' + (r.sample_count || 0) + '</td>' +
      '  <td class="mono">' + fmtTime(r.last_seen_at) + '</td>' +
      '  <td><button class="btn btn-small" data-edit-idx="' + i + '">' +
            (labeled ? '수정' : '라벨') + '</button></td>' +
      '</tr>';
  }).join('');
  tbody.innerHTML = html;

  // Attach row-level handlers
  tbody.querySelectorAll('button[data-edit-idx]').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.getAttribute('data-edit-idx'), 10);
      openWaypointModal(_data.waypoints[idx]);
    });
  });
}

function updateWaypointCounters() {
  const total = _data.waypoints.length;
  const labeled = _data.waypoints.filter(r => r.label && String(r.label).trim()).length;
  const unlabeled = total - labeled;
  document.getElementById('stat-wp-total').textContent = total;
  document.getElementById('stat-wp-labeled').textContent = labeled;
  document.getElementById('stat-wp-unlabeled').textContent = unlabeled;
  document.getElementById('st-wp').textContent = 'waypoints: ' + total;
  document.getElementById('st-labeled').textContent = 'labeled: ' + labeled;
  document.getElementById('tab-meta').textContent =
      total + ' total · ' + labeled + ' labeled · ' + unlabeled + ' unlabeled';
}

// ---------------------------------------------------------------------------
// Waypoint label modal
// ---------------------------------------------------------------------------

let _wpEditing = null;

function openWaypointModal(row) {
  _wpEditing = row;
  document.getElementById('wp-id').textContent = 'target_id = ' + row.target_id;
  document.getElementById('wp-label').value    = row.label    || '';
  document.getElementById('wp-location').value = row.location || '';
  document.getElementById('wp-notes').value    = row.notes    || '';
  document.getElementById('wp-delete').style.display =
      (row.label && String(row.label).trim()) ? '' : 'none';
  openModal('modal-waypoint');
  setTimeout(() => document.getElementById('wp-label').focus(), 50);
}

async function saveWaypointLabel() {
  if (!_wpEditing) return;
  const label = document.getElementById('wp-label').value.trim();
  if (!label) { toast('이름은 필수입니다', 'err'); return; }
  const body = {
    label,
    location: document.getElementById('wp-location').value.trim() || null,
    notes:    document.getElementById('wp-notes').value.trim()    || null,
    target_id: _wpEditing.target_id,
  };
  try {
    await api('PUT', '/admin/api/waypoints/' + encodeURIComponent(_wpEditing.station_id), body);
    closeModal('modal-waypoint');
    toast('라벨 저장됨', 'success');
    _wpEditing = null;
    await loadWaypoints();
  } catch (e) {
    toast('저장 실패: ' + e.message, 'err');
  }
}

async function deleteWaypointLabel() {
  if (!_wpEditing) return;
  if (!confirm('라벨을 삭제할까요?\n(원본 측정 데이터는 남고 사람 친화 이름만 사라집니다)')) return;
  try {
    await api('DELETE', '/admin/api/waypoints/' + encodeURIComponent(_wpEditing.station_id));
    closeModal('modal-waypoint');
    toast('라벨 삭제됨', 'success');
    _wpEditing = null;
    await loadWaypoints();
  } catch (e) {
    toast('삭제 실패: ' + e.message, 'err');
  }
}

// ---------------------------------------------------------------------------
// Errlog + recent activity
// ---------------------------------------------------------------------------

async function loadErrlog() {
  let rows;
  try { rows = await api('GET', '/admin/api/ingestion-errors?limit=20'); }
  catch (_) { rows = []; }
  const ul = document.getElementById('errlog');
  if (!ul) return;
  if (!rows || !rows.length) {
    ul.innerHTML = '<li class="empty">에러 없음</li>';
  } else {
    ul.innerHTML = rows.map(r =>
      '<li><span class="mono small">' + fmtTime(r.created_at) + '</span> ' +
      '<span class="badge">' + escapeHtml(r.message_type || '?') + '</span> ' +
      escapeHtml(r.error_message || '') + '</li>'
    ).join('');
  }
}

async function loadActivity() {
  let rows;
  try { rows = await api('GET', '/admin/api/recent-activity?limit=30'); }
  catch (_) { rows = []; }
  const ul = document.getElementById('activity-log');
  if (!ul) return;
  if (!rows || !rows.length) {
    ul.innerHTML = '<li class="empty">수신 대기 중...</li>';
  } else {
    // recent-activity API 응답 필드: received_at, kind('DUST'/'CCTV'), ref, detail.
    // detail 은 서버가 완성한 문자열(예: "V640p · 4000 bytes", "dust=35.4 alarm=3").
    ul.innerHTML = rows.map(r => {
      const kind = (r.kind || '').toLowerCase();   // 'cctv' / 'dust'
      const ts = fmtTime(r.received_at);
      return '<li><span class="mono small">' + ts + '</span> ' +
             '<span class="badge ' + kind + '">' + kind + '</span> ' +
             escapeHtml(r.detail || '') + '</li>';
    }).join('');
  }
}

// ---------------------------------------------------------------------------
// DUST XML viewer (toggle, default off)
// ---------------------------------------------------------------------------

let _dustXmlTimer = null;

async function loadDustXml() {
  let rows;
  try { rows = await api('GET', '/admin/api/recent-dust-xml?limit=5'); }
  catch (_) { rows = []; }
  const ul = document.getElementById('dust-xml-log');
  if (!ul) return;
  if (!rows || !rows.length) {
    ul.innerHTML = '<li class="empty">수신 대기 중...</li>';
    return;
  }
  ul.innerHTML = rows.map(r => {
    const ts = fmtTime(r.received_at);
    const tuple = 'wp=' + (r.waypoint_id ?? '·') +
                  ' xyz=' + (r.waypoint_x ?? '·') + ',' + (r.waypoint_y ?? '·') + ',' + (r.waypoint_z ?? '·') +
                  ' ptl=' + (r.inspection_pan ?? '·') + ',' + (r.inspection_tilt ?? '·') + ',' + (r.inspection_lift ?? '·');
    return '<li><span class="mono small">' + ts + '</span> ' +
           '<span class="badge dust">DUST</span> ' +
           '<span class="mono small">' + escapeHtml(tuple) + '</span>' +
           '<pre class="mono small" style="margin:4px 0 0 0; white-space:pre-wrap; background:#f6f8fa; padding:4px; border-radius:3px;">' +
             escapeHtml(r.raw_xml || '') +
           '</pre></li>';
  }).join('');
}

function setDustXmlEnabled(on) {
  const ul = document.getElementById('dust-xml-log');
  if (on) {
    if (_dustXmlTimer) return;
    loadDustXml();
    _dustXmlTimer = setInterval(loadDustXml, 3000);
    try { localStorage.setItem('dustXmlEnabled', '1'); } catch (_) {}
  } else {
    if (_dustXmlTimer) { clearInterval(_dustXmlTimer); _dustXmlTimer = null; }
    if (ul) ul.innerHTML = '<li class="empty">disabled</li>';
    try { localStorage.setItem('dustXmlEnabled', '0'); } catch (_) {}
  }
}

// ---------------------------------------------------------------------------
// Cleanup button
// ---------------------------------------------------------------------------

async function triggerCleanup() {
  const status = document.getElementById('cleanup-status');
  if (status) status.textContent = '요청 중...';
  try {
    await api('POST', '/admin/api/cleanup/trigger');
    if (status) status.textContent = '신호 전송됨';
    toast('cleanup 신호 전송', 'success');
    setTimeout(() => { if (status) status.textContent = ''; }, 5000);
  } catch (e) {
    if (status) status.textContent = '실패';
    toast('cleanup 실패: ' + e.message, 'err');
  }
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

async function refreshAll() {
  await Promise.all([loadStatus(), loadWaypoints(), loadActivity()]);
}

function attachUi() {
  const r = document.getElementById('btn-refresh');
  if (r) r.addEventListener('click', refreshAll);
  const c = document.getElementById('btn-cleanup-now');
  if (c) c.addEventListener('click', triggerCleanup);
  const ws = document.getElementById('wp-save');
  if (ws) ws.addEventListener('click', saveWaypointLabel);
  const wd = document.getElementById('wp-delete');
  if (wd) wd.addEventListener('click', deleteWaypointLabel);

  const dx = document.getElementById('dust-xml-toggle');
  if (dx) {
    // Restore last user preference (default off — checkbox starts unchecked).
    let on = false;
    try { on = localStorage.getItem('dustXmlEnabled') === '1'; } catch (_) {}
    dx.checked = on;
    setDustXmlEnabled(on);
    dx.addEventListener('change', () => setDustXmlEnabled(dx.checked));
  }

  document.querySelectorAll('[data-modal-close]').forEach(el => {
    el.addEventListener('click', () => closeModal(el.getAttribute('data-modal-close')));
  });
  document.querySelectorAll('.modal-overlay').forEach(ov => {
    ov.addEventListener('click', (e) => { if (e.target === ov) ov.classList.remove('open'); });
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      document.querySelectorAll('.modal-overlay.open').forEach(o => o.classList.remove('open'));
    }
  });
}

window.addEventListener('DOMContentLoaded', async () => {
  attachUi();
  await refreshAll();
  await loadErrlog();
  _refreshTimer = setInterval(refreshAll, REFRESH_MS);
  _errlogTimer  = setInterval(loadErrlog,  ERRLOG_MS);
});
