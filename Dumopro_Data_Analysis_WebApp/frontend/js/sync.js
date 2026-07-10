// Stations sync widget — [Sync now] button + conflict modal.
// Polls /api/stations/conflicts every 30s; rerenders the grid after
// a successful sync or conflict resolution.

import {
  triggerStationSync,
  getStationConflicts,
  resolveStationConflict,
} from './api.js';

const POLL_MS = 30_000;

function $(sel) { return document.querySelector(sel); }
function id8(s) { return s ? String(s).slice(0, 8) : ''; }
function clockNow() { return new Date().toTimeString().slice(0, 8); }
function fmtTs(s) {
  if (!s) return '';
  return String(s).replace('T', ' ').slice(0, 19);
}

function reloadGrid() {
  document.dispatchEvent(new CustomEvent('reload-grid'));
}

// ---------- Conflict banner / count ----------

let _lastConflictCount = 0;

async function refreshConflictBanner() {
  let count = 0;
  let conflicts = [];
  try {
    const data = await getStationConflicts();
    conflicts = data.conflicts || [];
    count = conflicts.length;
  } catch (err) {
    console.warn('conflicts fetch failed:', err);
    return;
  }
  const banner = $('#conflict-banner');
  const link = $('#conflict-link');
  if (!banner || !link) return;
  if (count > 0) {
    banner.style.display = 'block';
    link.textContent = `⚠ ${count} 개소가 결정 대기 중`;
  } else {
    banner.style.display = 'none';
  }
  // If a new conflict appeared, auto-open the modal so the operator
  // doesn't miss it.
  if (count > _lastConflictCount && count > 0) {
    openModal(conflicts);
  }
  _lastConflictCount = count;
}

// ---------- Modal ----------

function openModal(conflicts) {
  const list = $('#conflict-list');
  list.innerHTML = '';
  if (!conflicts.length) {
    const empty = document.createElement('div');
    empty.className = 'muted';
    empty.textContent = '결정 대기 중인 개소가 없습니다.';
    list.appendChild(empty);
  } else {
    for (const c of conflicts) {
      list.appendChild(renderConflictCard(c));
    }
  }
  $('#conflict-modal').classList.add('open');
}

function closeModal() {
  $('#conflict-modal').classList.remove('open');
}

function renderConflictCard(c) {
  const div = document.createElement('div');
  div.className = 'conflict-card';

  const name = document.createElement('div');
  name.style.fontWeight = 'bold';
  name.textContent = c.station_name;
  div.appendChild(name);

  const meta = document.createElement('div');
  meta.className = 'meta';
  meta.textContent =
    `old=${id8(c.old_id)} → new=${id8(c.new_id)}` +
    (c.detected_at ? ` · detected=${fmtTs(c.detected_at)}` : '');
  div.appendChild(meta);

  const actions = document.createElement('div');
  actions.className = 'actions';

  const carry = document.createElement('button');
  carry.textContent = '이어서 보기';
  carry.title = '이전 차트 데이터를 보존하고 새 station_id로 이어서 polling';
  carry.addEventListener('click', () => onResolve(c.station_name, 'carry_over'));
  actions.appendChild(carry);

  const fresh = document.createElement('button');
  fresh.textContent = '새로 시작';
  fresh.className = 'danger';
  fresh.title = '이전 Redis 데이터를 모두 삭제하고 cold-start';
  fresh.addEventListener('click', () => {
    if (!confirm(`'${c.station_name}'의 이전 차트·캔들 데이터를 모두 삭제합니다. 계속?`)) return;
    onResolve(c.station_name, 'start_fresh');
  });
  actions.appendChild(fresh);

  div.appendChild(actions);
  return div;
}

async function onResolve(stationName, action) {
  try {
    await resolveStationConflict(stationName, action);
  } catch (err) {
    alert('실패: ' + (err.detail || err.message));
    return;
  }
  // Refresh the modal contents (might have other pending conflicts left)
  // and the grid (the resolved station should appear in ~1s).
  setTimeout(refreshConflictBanner, 200);
  setTimeout(async () => {
    try {
      const data = await getStationConflicts();
      if ((data.conflicts || []).length === 0) {
        closeModal();
      } else {
        openModal(data.conflicts);
      }
    } catch (_) { /* ignore */ }
  }, 400);
  setTimeout(reloadGrid, 1500);
}

// ---------- [Sync now] button ----------

function bindSyncButton() {
  const btn = $('#btn-sync-now');
  const status = $('#sync-status');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    status.textContent = 'syncing...';
    try {
      await triggerStationSync();
      status.textContent = `last: ${clockNow()}`;
    } catch (err) {
      status.textContent = 'failed';
      console.error('sync failed:', err);
    } finally {
      btn.disabled = false;
    }
    // Give the poller a moment to reconcile before re-querying.
    setTimeout(refreshConflictBanner, 1500);
    setTimeout(reloadGrid, 1500);
  });

  const link = $('#conflict-link');
  if (link) {
    link.addEventListener('click', async (e) => {
      e.preventDefault();
      try {
        const data = await getStationConflicts();
        openModal(data.conflicts || []);
      } catch (err) {
        alert('충돌 목록 로드 실패: ' + err.message);
      }
    });
  }

  // Modal close (delegated to data-modal-close)
  document.addEventListener('click', (e) => {
    const closer = e.target.closest('[data-modal-close]');
    if (closer && closer.getAttribute('data-modal-close') === 'conflict-modal') {
      closeModal();
    }
  });
}

// ---------- Boot ----------

export function initStationSync() {
  bindSyncButton();
  refreshConflictBanner();
  setInterval(refreshConflictBanner, POLL_MS);
}
