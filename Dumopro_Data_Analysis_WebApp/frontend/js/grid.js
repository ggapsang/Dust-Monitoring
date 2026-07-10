import { getChart, getStations } from './api.js';
import { renderCandleChart } from './chart.js';
import { subscribe } from './sse.js';
import { subscribeActive } from './activity.js';

const state = {
  stations: [],
  chartDataByStation: new Map(),  // station -> {frozen,live,last_sampled_at}
  unsubFns: [],
  idleTimer: null,
  unsubActive: null,
};

const GRID_UNIT = 'day';  // grid is fixed to day view in v0.1

function fmtIdle(seconds) {
  if (seconds == null) return '—';
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}초 전`;
  if (s < 3600) return `${Math.floor(s / 60)}분 전`;
  if (s < 86400) return `${Math.floor(s / 3600)}시간 전`;
  return `${Math.floor(s / 86400)}일 전`;
}

function cellId(name) { return 'cell-' + name.replace(/[^A-Za-z0-9_-]/g, '_'); }

function renderCellShell(container, station) {
  const el = document.createElement('div');
  el.className = 'cell';
  el.id = cellId(station.station_name);
  el.dataset.station = station.station_name;
  el.innerHTML = `
    <div class="cell-header">
      <span>${station.station_name}</span>
      <span class="muted" data-idle>—</span>
    </div>
    <div class="cell-stats">
      <span data-median>median: —</span>
      <span data-count>n: —</span>
    </div>
    <div class="canvas-wrap"><canvas></canvas></div>
  `;
  el.addEventListener('click', () => {
    const evt = new CustomEvent('open-detail', { detail: { station: station.station_name } });
    document.dispatchEvent(evt);
  });
  container.appendChild(el);
  return el;
}

function refreshCellStats(el, station, data) {
  const idle = document.querySelector(`#${CSS.escape(cellId(station.station_name))} [data-idle]`);
  const lastTs = data.last_sampled_at || station.last_sampled_at;
  if (idle) {
    if (lastTs) {
      const dt = new Date(lastTs);
      const diff = (Date.now() - dt.getTime()) / 1000;
      idle.textContent = fmtIdle(diff);
    } else {
      idle.textContent = '—';
    }
  }
  const medianEl = el.querySelector('[data-median]');
  const countEl = el.querySelector('[data-count]');
  const liveStats = data.live?.stats;
  if (liveStats) {
    if (medianEl) medianEl.textContent = `median: ${Number(liveStats.median).toFixed(4)}`;
    if (countEl) countEl.textContent = `n: ${liveStats.count}`;
  }
}

function redrawCell(station) {
  const el = document.getElementById(cellId(station.station_name));
  if (!el) return;
  const data = state.chartDataByStation.get(station.station_name);
  if (!data) return;
  const canvas = el.querySelector('canvas');
  renderCandleChart(canvas, data, { ma: [7], unit: GRID_UNIT });
  refreshCellStats(el, station, data);
}

function applyActive(next, prev) {
  if (prev) {
    const prevEl = document.getElementById(cellId(prev));
    if (prevEl) prevEl.classList.remove('active');
  }
  if (next) {
    const nextEl = document.getElementById(cellId(next));
    if (nextEl) nextEl.classList.add('active');
  }
}

function applyLiveUpdate(stationName, payload) {
  const data = state.chartDataByStation.get(stationName);
  if (!data) return;
  if (payload.type === 'candle_update') {
    data.live = { bucket_key: payload.bucket_key, stats: payload.stats };
    data.last_sampled_at = payload.updated_at || data.last_sampled_at;
  } else if (payload.type === 'candle_frozen') {
    data.frozen = data.frozen.filter(f => f.bucket_key !== payload.bucket_key);
    data.frozen.push({ bucket_key: payload.bucket_key, stats: payload.stats });
    data.frozen.sort((a, b) => a.bucket_key < b.bucket_key ? -1 : 1);
    if (data.live && data.live.bucket_key === payload.bucket_key) {
      data.live = null;
    }
  } else if (payload.type === 'station_stalled') {
    const el = document.getElementById(cellId(stationName));
    if (el) el.classList.add('offline');
  }
  const station = state.stations.find(s => s.station_name === stationName);
  if (station) redrawCell(station);
}

function restartIdleTimer() {
  if (state.idleTimer) clearInterval(state.idleTimer);
  state.idleTimer = setInterval(() => {
    state.stations.forEach(s => {
      const el = document.getElementById(cellId(s.station_name));
      const data = state.chartDataByStation.get(s.station_name);
      if (el && data) refreshCellStats(el, s, data);
    });
  }, 1000);
}

export async function renderGrid(root) {
  root.innerHTML = '';
  const gridEl = document.createElement('div');
  gridEl.className = 'grid';
  root.appendChild(gridEl);

  const data = await getStations();
  state.stations = data.stations;

  for (const s of state.stations) renderCellShell(gridEl, s);

  // Initial chart fetches in parallel, then render.
  await Promise.all(state.stations.map(async (s) => {
    try {
      const c = await getChart(s.station_name, 'day', 'all');
      state.chartDataByStation.set(s.station_name, c);
      redrawCell(s);
    } catch (err) {
      console.warn('chart fetch failed', s.station_name, err);
    }
  }));

  // Subscribe to SSE per station.
  state.unsubFns.forEach(fn => fn());
  state.unsubFns = state.stations.map(s =>
    subscribe(s.station_name, (evt) => applyLiveUpdate(s.station_name, evt))
  );

  restartIdleTimer();

  // Active station border toggle
  if (state.unsubActive) state.unsubActive();
  state.unsubActive = subscribeActive(applyActive);

  // Re-render on window resize.
  window.addEventListener('resize', () => state.stations.forEach(redrawCell), { passive: true });
}

export function teardownGrid() {
  state.unsubFns.forEach(fn => fn());
  state.unsubFns = [];
  if (state.idleTimer) { clearInterval(state.idleTimer); state.idleTimer = null; }
  if (state.unsubActive) { state.unsubActive(); state.unsubActive = null; }
}
