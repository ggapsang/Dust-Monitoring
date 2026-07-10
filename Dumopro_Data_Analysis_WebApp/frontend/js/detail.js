import { getChart, getRawSamples, runRegression } from './api.js';
import { renderCandleChart, renderLineChart } from './chart.js';
import { subscribe } from './sse.js';
import { subscribeActive } from './activity.js';

const RAW_DEFAULT_LIMIT = 500;

// Per-station detail state.
const states = new Map(); // station -> {root, unsub, data, opts, canvas, tooltip, cleanupFns[]}

const MA_OPTIONS = [7, 30, 120, 365];

export async function renderDetail(root, station) {
  const st = {
    root,
    unsub: null,
    unsubActive: null,
    data: null,
    opts: { unit: 'day', range: 'all', ma: [7, 30], rawLimit: RAW_DEFAULT_LIMIT },
    canvas: null,
    tooltip: null,
    cleanupFns: [],
    regression: null,
    regressionParams: { target: 'median', degree: 2, band_n: 2.0, percentile: 95.0 },
    candlesFieldset: null,
  };
  states.set(station, st);

  root.innerHTML = `
    <fieldset>
      <legend>${station}</legend>
      <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center">
        <label>단위
          <select data-unit>
            <option value="raw">초(raw)</option>
            <option value="hour">시간</option>
            <option value="day" selected>일</option>
            <option value="week">주</option>
            <option value="month">월</option>
          </select>
        </label>
        <label data-range-wrap>기간
          <select data-range>
            <option value="90">90일</option>
            <option value="180">180일</option>
            <option value="365">365일</option>
            <option value="all" selected>전체</option>
          </select>
        </label>
        <label data-raw-limit-wrap style="display:none">샘플 수
          <input type="number" data-raw-limit value="${RAW_DEFAULT_LIMIT}" min="10" max="5000" step="100" style="width:80px">
        </label>
        <span data-ma-wrap>MA:
          ${MA_OPTIONS.map(w =>
            `<label style="margin-left:6px"><input type="checkbox" data-ma value="${w}"${[7,30].includes(w)?' checked':''}> ${w}</label>`
          ).join('')}
        </span>
      </div>
    </fieldset>
    <fieldset data-candles-fs style="flex:1;display:flex;flex-direction:column;min-height:460px">
      <legend>Candles</legend>
      <div class="canvas-wrap" style="flex:1;position:relative;min-height:420px">
        <canvas></canvas>
        <div class="tooltip" style="display:none;position:absolute;background:#ffffe0;border:1px solid #000;padding:4px 6px;font:11px 'Courier New';pointer-events:none;white-space:pre"></div>
      </div>
    </fieldset>
    <fieldset id="regression-panel">
      <legend>Regression</legend>
      <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center">
        <label>Target
          <select data-reg-target>
            <option value="median" selected>median</option>
            <option value="max">max</option>
            <option value="q3">q3</option>
          </select>
        </label>
        <span>Extra (OR):
          <label><input type="checkbox" data-reg-extra value="median"> median</label>
          <label><input type="checkbox" data-reg-extra value="max"> max</label>
          <label><input type="checkbox" data-reg-extra value="q3"> q3</label>
        </span>
        <label>Degree
          <input type="number" data-reg-degree value="2" min="1" max="5" style="width:48px">
        </label>
        <label>Band N
          <input type="number" data-reg-band value="2.0" step="0.5" min="0.5" max="5" style="width:56px">
        </label>
        <label>Percentile
          <input type="number" data-reg-pct value="95" min="50" max="99.9" step="0.5" style="width:56px">
        </label>
        <button data-run-regression>분석 실행</button>
        <button data-clear-regression>초기화</button>
        <span data-reg-status class="muted"></span>
      </div>
      <div data-reg-result style="margin-top:6px"></div>
    </fieldset>
  `;

  st.canvas = root.querySelector('canvas');
  st.tooltip = root.querySelector('.tooltip');
  st.candlesFieldset = root.querySelector('[data-candles-fs]');

  // Wire controls
  root.querySelector('[data-unit]').addEventListener('change', async (e) => {
    st.opts.unit = e.target.value;
    updateControlVisibility(station);
    await loadAndRender(station);
  });
  root.querySelector('[data-range]').addEventListener('change', async (e) => {
    st.opts.range = e.target.value;
    await loadAndRender(station);
  });
  root.querySelector('[data-raw-limit]').addEventListener('change', async (e) => {
    const v = Number(e.target.value);
    if (!isNaN(v) && v >= 10) {
      st.opts.rawLimit = v;
      if (st.opts.unit === 'raw') await loadAndRender(station);
    }
  });
  root.querySelectorAll('[data-ma]').forEach(cb => {
    cb.addEventListener('change', () => {
      st.opts.ma = Array.from(root.querySelectorAll('[data-ma]:checked')).map(x => Number(x.value));
      draw(station);
    });
  });

  // Regression controls
  root.querySelector('[data-run-regression]').addEventListener('click', () => runRegressionFor(station));
  root.querySelector('[data-clear-regression]').addEventListener('click', () => {
    const s = states.get(station);
    s.regression = null;
    root.querySelector('[data-reg-result]').innerHTML = '';
    root.querySelector('[data-reg-status]').textContent = '';
    draw(station);
  });
  ['[data-reg-target]','[data-reg-degree]','[data-reg-band]','[data-reg-pct]'].forEach(sel => {
    root.querySelector(sel).addEventListener('change', (e) => {
      const s = states.get(station);
      if (sel === '[data-reg-target]') s.regressionParams.target = e.target.value;
      if (sel === '[data-reg-degree]') s.regressionParams.degree = Number(e.target.value);
      if (sel === '[data-reg-band]') s.regressionParams.band_n = Number(e.target.value);
      if (sel === '[data-reg-pct]') s.regressionParams.percentile = Number(e.target.value);
    });
  });

  // Hover tooltip
  st.canvas.addEventListener('mousemove', (e) => showTooltipFor(station, e));
  st.canvas.addEventListener('mouseleave', () => hideTooltip(station));

  // Resize redraw
  const onResize = () => draw(station);
  window.addEventListener('resize', onResize, { passive: true });
  st.cleanupFns.push(() => window.removeEventListener('resize', onResize));

  await loadAndRender(station);

  // SSE live updates
  st.unsub = subscribe(station, (evt) => applyLiveUpdate(station, evt));

  // Active station border toggle on the Candles fieldset
  st.unsubActive = subscribeActive((active) => {
    const fs = st.candlesFieldset;
    if (!fs) return;
    if (active === station) fs.classList.add('active');
    else fs.classList.remove('active');
  });
}

function updateControlVisibility(station) {
  const st = states.get(station);
  if (!st) return;
  const isRaw = st.opts.unit === 'raw';
  const show = (sel, visible, display = 'inline-block') => {
    const el = st.root.querySelector(sel);
    if (el) el.style.display = visible ? display : 'none';
  };
  show('[data-range-wrap]', !isRaw);
  show('[data-raw-limit-wrap]', isRaw);
  show('[data-ma-wrap]', !isRaw);
  // Regression panel: only meaningful for boxplot units
  const regPanel = st.root.querySelector('#regression-panel');
  if (regPanel) regPanel.style.display = isRaw ? 'none' : '';
}

async function loadAndRender(station) {
  const st = states.get(station);
  if (!st) return;

  if (st.opts.unit === 'raw') {
    const res = await getRawSamples(station, st.opts.rawLimit);
    st.data = { mode: 'raw', samples: res.samples };
    st.regression = null;
    drawRaw(station);
    return;
  }

  const c = await getChart(station, st.opts.unit, st.opts.range);
  st.data = { mode: 'candle', ...c };
  // Filter MA options whose window > candles count
  const total = (c.frozen?.length || 0) + (c.live ? 1 : 0);
  st.root.querySelectorAll('[data-ma]').forEach(cb => {
    const w = Number(cb.value);
    cb.disabled = w > total;
    if (cb.disabled) cb.checked = false;
  });
  st.opts.ma = Array.from(st.root.querySelectorAll('[data-ma]:checked')).map(x => Number(x.value));
  draw(station);
}

function draw(station) {
  const st = states.get(station);
  if (!st || !st.canvas || !st.data) return;
  if (st.data.mode === 'raw') return drawRaw(station);
  const regression = st.regression && st.regression.unit === st.opts.unit ? st.regression : null;
  renderCandleChart(st.canvas, st.data, {
    ma: st.opts.ma,
    regression,
    unit: st.opts.unit,
    scroll: true,  // 상세 탭은 캔들이 많아지면 가로 스크롤
  });
  // Record candle hit regions for tooltip lookup
  computeHitRegions(station);
}

function drawRaw(station) {
  const st = states.get(station);
  if (!st || !st.canvas || !st.data) return;
  renderLineChart(st.canvas, st.data.samples || [], { scroll: true });
  // Hide tooltip (line-chart tooltip is a future enhancement)
  if (st.tooltip) st.tooltip.style.display = 'none';
  st.regions = null;
}

async function runRegressionFor(station) {
  const st = states.get(station);
  if (!st) return;
  const statusEl = st.root.querySelector('[data-reg-status]');
  const resultEl = st.root.querySelector('[data-reg-result]');
  const btn = st.root.querySelector('[data-run-regression]');
  btn.disabled = true;
  statusEl.textContent = '실행 중...';
  resultEl.innerHTML = '';
  try {
    const extraTargets = Array.from(st.root.querySelectorAll('[data-reg-extra]:checked'))
      .map(x => x.value)
      .filter(t => t !== st.regressionParams.target);
    const body = {
      unit: st.opts.unit,
      range: st.opts.range,
      target: st.regressionParams.target,
      extra_targets: extraTargets,
      degree: st.regressionParams.degree,
      band_n: st.regressionParams.band_n,
      percentile: st.regressionParams.percentile,
    };
    const res = await runRegression(station, body);
    st.regression = { ...res, unit: st.opts.unit };
    statusEl.textContent = `n=${res.n}  RMSE=${res.rmse.toFixed(4)}  threshold=${res.threshold.toFixed(4)}`;
    resultEl.innerHTML = `
      <div style="font:11px 'Courier New'">
        Highlighted buckets: ${(res.highlighted_bucket_keys || []).length === 0 ? '(none)' : res.highlighted_bucket_keys.join(', ')}
      </div>
    `;
    draw(station);
  } catch (err) {
    statusEl.textContent = '';
    if (err.status === 422 && err.detail?.error === 'insufficient_candles') {
      alert(err.detail.message);
    } else {
      resultEl.innerHTML = `<div class="err">Error: ${err.message}</div>`;
      console.error(err);
    }
  } finally {
    btn.disabled = false;
  }
}

function applyLiveUpdate(station, payload) {
  const st = states.get(station);
  if (!st || !st.data) return;
  if (st.data.mode === 'raw') return;  // raw 모드는 SSE candle 이벤트 무시 (직접 재조회로 갱신)
  if (payload.unit !== st.opts.unit) return;  // Ignore units not currently viewed
  if (payload.type === 'candle_update') {
    st.data.live = { bucket_key: payload.bucket_key, stats: payload.stats };
    st.data.last_sampled_at = payload.updated_at || st.data.last_sampled_at;
  } else if (payload.type === 'candle_frozen') {
    st.data.frozen = st.data.frozen.filter(f => f.bucket_key !== payload.bucket_key);
    st.data.frozen.push({ bucket_key: payload.bucket_key, stats: payload.stats });
    st.data.frozen.sort((a, b) => a.bucket_key < b.bucket_key ? -1 : 1);
    if (st.data.live && st.data.live.bucket_key === payload.bucket_key) st.data.live = null;
  }
  draw(station);
}

// Tooltip via hit-testing. Reuse the same geometry as chart.js.
function computeHitRegions(station) {
  const st = states.get(station);
  if (!st || !st.data) return;
  const r = st.canvas.getBoundingClientRect();
  const candles = [];
  (st.data.frozen || []).forEach(f => candles.push({ key: f.bucket_key, stats: f.stats, live: false }));
  if (st.data.live) candles.push({ key: st.data.live.bucket_key, stats: st.data.live.stats, live: true });
  const padL = 46, padR = 8;
  const plotW = Math.max(1, r.width - padL - padR);
  const n = candles.length;
  st.regions = candles.map((c, i) => {
    const cx = padL + (plotW / n) * (i + 0.5);
    return { x: cx, width: plotW / n, candle: c };
  });
}

function showTooltipFor(station, e) {
  const st = states.get(station);
  if (!st || !st.regions) return;
  const rect = st.canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;
  const hit = st.regions.find(r => Math.abs(r.x - mx) <= r.width / 2);
  if (!hit) { hideTooltip(station); return; }
  const s = hit.candle.stats;
  const lines = [
    `${hit.candle.key}${hit.candle.live ? '  (live)' : ''}`,
    `median : ${num(s.median)}`,
    `Q1     : ${num(s.q1)}`,
    `Q3     : ${num(s.q3)}`,
    `whisker: ${num(s.whisker_low)} ~ ${num(s.whisker_high)}`,
    `outlier: ${(s.outliers||[]).length}`,
    `extreme: ${(s.extremes||[]).length}`,
    `count  : ${s.count}`,
  ];
  const reg = st.regression && st.regression.unit === st.opts.unit ? st.regression : null;
  if (reg) {
    const idx = (reg.bucket_keys || []).indexOf(hit.candle.key);
    if (idx >= 0) {
      const r = reg.residuals[idx];
      lines.push(`residual: ${num(r)}  thr=${num(reg.threshold)}`);
      if ((reg.highlighted_bucket_keys || []).includes(hit.candle.key)) {
        lines.push(`         > threshold (highlighted)`);
      }
    }
  }
  st.tooltip.style.display = 'block';
  st.tooltip.textContent = lines.join('\n');
  const tw = st.tooltip.offsetWidth;
  const th = st.tooltip.offsetHeight;
  let left = mx + 12;
  let top = my + 12;
  if (left + tw > rect.width) left = Math.max(0, mx - tw - 12);
  if (top + th > rect.height) top = Math.max(0, my - th - 12);
  st.tooltip.style.left = left + 'px';
  st.tooltip.style.top = top + 'px';
}

function hideTooltip(station) {
  const st = states.get(station);
  if (st?.tooltip) st.tooltip.style.display = 'none';
}

function num(v) { return v == null ? '—' : Number(v).toFixed(4); }

export function teardownDetail(station) {
  const st = states.get(station);
  if (!st) return;
  if (st.unsub) st.unsub();
  if (st.unsubActive) st.unsubActive();
  st.cleanupFns.forEach(fn => fn());
  states.delete(station);
}
