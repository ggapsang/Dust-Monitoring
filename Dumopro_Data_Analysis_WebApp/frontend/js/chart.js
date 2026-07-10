// Pure Canvas boxplot renderer.

const CANDLE_WIDTH_RATIO = 0.78;   // body/slot 비율 — 간격 좁게
const CANDLE_WIDTH_MAX = 30;       // body 최대 픽셀 폭
const CANDLE_WIDTH_MIN = 4;
const MIN_SLOT_PX = 14;            // 캔들이 많을 때 최소 slot 폭 → 이보다 좁아지면 가로 스크롤
const X_LABEL_GAP_PX = 6;          // 라벨 사이 최소 여백

const Y_BREAK_RATIO = 1.3;         // absMax > normalUpper * 이 값이면 break 활성화
const Y_BREAK_MIN_GAP = 0.02;      // normalUpper와 absMax 차이가 이 값 이상일 때만 (작은 스케일 안정화)
const Y_BREAK_BAND_RATIO = 0.12;   // 플롯 높이의 12%를 극단치 압축 밴드로 할당
const Y_BREAK_GAP_PX = 10;         // 끊김 마커(~~) 영역

// Refined Classic v2.0 — design_guideline.md §7
// 데이터 색상은 표준 팔레트 유지, UI 크롬(axis/grid/stroke/label/font)만 모노톤 톤다운.
const COLORS = {
  body: '#6b96c8',
  bodyStroke: '#1a1a1a',
  median: '#1a1a1a',
  whisker: '#1a1a1a',
  outlier: '#c89040',
  extreme: '#a00000',
  ma: '#1a7a3a',
  liveBody: '#c6c0a0',
  axis: '#999999',
  grid: '#e2e2e2',
  trend: '#7030a0',
  band: 'rgba(112,48,160,0.15)',
  residualHighlight: '#ff6600',
  breakMarker: '#1a1a1a',
};

// Refined Classic v2.0 canvas fonts.
const FONT_LABEL_MONO = '10.5px "JetBrains Mono", "IBM Plex Mono", Menlo, "Courier New", monospace';
const FONT_EMPTY_BODY = '11.5px "Inter", "Pretendard", system-ui, -apple-system, sans-serif';
const LABEL_COLOR_MUTED  = '#555555';
const LABEL_COLOR_SUBTLE = '#777777';

function dpr() { return window.devicePixelRatio || 1; }

function prepareCanvas(canvas, candleCount, padL, padR, scroll) {
  const wrap = canvas.parentElement;
  const wrapRect = wrap ? wrap.getBoundingClientRect() : { width: 600, height: 300 };
  const wrapW = wrapRect.width || 1;
  let logicalWidth;
  if (scroll) {
    // 캔들이 많으면 wrap보다 넓게 → 부모 .canvas-wrap 의 overflow-x 로 스크롤
    const minContentW = padL + Math.max(1, candleCount) * MIN_SLOT_PX + padR;
    logicalWidth = Math.max(wrapW, minContentW);
  } else {
    // 항상 wrap 너비에 맞춤 (가로 스크롤 없음)
    logicalWidth = wrapW;
  }
  const logicalHeight = Math.max(wrapRect.height || 1, 120);

  canvas.style.width = logicalWidth + 'px';
  canvas.style.height = logicalHeight + 'px';

  const ratio = dpr();
  canvas.width = Math.max(1, Math.round(logicalWidth * ratio));
  canvas.height = Math.max(1, Math.round(logicalHeight * ratio));
  const ctx = canvas.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { ctx, width: logicalWidth, height: logicalHeight };
}

function ma(values, window) {
  const out = new Array(values.length).fill(null);
  if (window <= 1 || values.length < window) return out;
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= window) sum -= values[i - window];
    if (i >= window - 1) out[i] = sum / window;
  }
  return out;
}

function niceScale(min, max) {
  if (!isFinite(min) || !isFinite(max)) return { min: 0, max: 1 };
  if (min === max) {
    const pad = Math.abs(min) > 0 ? Math.abs(min) * 0.1 : 0.01;
    return { min: min - pad, max: max + pad };
  }
  const pad = (max - min) * 0.08;
  return { min: min - pad, max: max + pad };
}

function computeYPlan(candles, opts) {
  // 1) Scan all values
  //    - normalMax: whisker_high만 (캔들의 정상 범위)
  //    - absMax: outliers, extremes, regression 밴드 포함 모든 값
  let ymin = +Infinity;
  let normalMax = -Infinity;
  let absMax = -Infinity;
  candles.forEach(c => {
    const s = c.stats;
    const lo = s.whisker_low ?? s.q1;
    if (lo < ymin) ymin = lo;
    const hi = s.whisker_high ?? s.q3;
    if (hi > normalMax) normalMax = hi;
    if (hi > absMax) absMax = hi;
    (s.outliers || []).forEach(v => {
      if (v < ymin) ymin = v;
      if (v > absMax) absMax = v;
    });
    (s.extremes || []).forEach(v => {
      if (v < ymin) ymin = v;
      if (v > absMax) absMax = v;
    });
  });
  if (opts.regression) {
    (opts.regression.band_upper || []).forEach(v => {
      if (v > absMax) absMax = v;
    });
    (opts.regression.band_lower || []).forEach(v => {
      if (v < ymin) ymin = v;
    });
  }

  // 2) Decide break mode — 박스 수염 위로 극단치가 튀어나오면 압축 밴드 사용
  const hasOverflow = absMax > normalMax;
  const rangeOk = (absMax - normalMax) > Y_BREAK_MIN_GAP;
  const breakEnabled =
    hasOverflow && rangeOk && normalMax > 0 && absMax > normalMax * Y_BREAK_RATIO;

  if (!breakEnabled) {
    const ys = niceScale(ymin, absMax);
    return { break: false, ymin: ys.min, ymax: ys.max };
  }

  // Main region은 모든 캔들의 수염까지 포함하도록 함
  const mainUpper = normalMax + (normalMax - ymin) * 0.08;
  const absMaxPad = absMax + (absMax - mainUpper) * 0.05;
  const mainMin = ymin - (normalMax - ymin) * 0.05;
  return {
    break: true,
    ymin: mainMin,
    mainUpper,
    absMax: absMaxPad,
  };
}

function makeYTo(plan, plotTop, plotBottom) {
  const plotH = plotBottom - plotTop;
  if (!plan.break) {
    const range = plan.ymax - plan.ymin;
    return v => plotBottom - (v - plan.ymin) / range * plotH;
  }
  const breakBandH = plotH * Y_BREAK_BAND_RATIO;
  const gap = Y_BREAK_GAP_PX;
  const mainTop = plotTop + breakBandH + gap;
  const mainH = plotBottom - mainTop;
  const { ymin, mainUpper, absMax } = plan;
  const mainRange = mainUpper - ymin;
  const extremeRange = absMax - mainUpper;
  return v => {
    if (v <= mainUpper) {
      return plotBottom - (v - ymin) / mainRange * mainH;
    }
    const t = Math.max(0, Math.min(1, (v - mainUpper) / (extremeRange || 1)));
    return plotTop + breakBandH - t * breakBandH;
  };
}

function drawBreakMarker(ctx, x1, x2, y) {
  ctx.save();
  ctx.strokeStyle = COLORS.breakMarker;
  ctx.lineWidth = 1;
  ctx.fillStyle = '#ffffff';
  // 배경: 끊김 영역을 흰색으로 깔끔하게 덮음
  ctx.fillRect(x1, y - 5, x2 - x1, 10);
  // 두 개의 지그재그 (~~) — Y축에 "잘라냄"을 표시
  const step = 5;
  for (const yy of [y - 3, y + 3]) {
    ctx.beginPath();
    for (let x = x1; x <= x2; x += step) {
      const dy = ((x - x1) / step) % 2 === 0 ? 0 : -2;
      if (x === x1) ctx.moveTo(x, yy + dy);
      else ctx.lineTo(x, yy + dy);
    }
    ctx.stroke();
  }
  ctx.restore();
}

export function renderCandleChart(canvas, chartData, opts = {}) {
  const { frozen = [], live = null } = chartData;

  const candles = [];
  frozen.forEach(f => candles.push({ key: f.bucket_key, stats: f.stats, live: false }));
  if (live) candles.push({ key: live.bucket_key, stats: live.stats, live: true });

  const padL = 46, padR = 8, padT = 8, padB = 22;
  const { ctx, width, height } = prepareCanvas(canvas, candles.length, padL, padR, !!opts.scroll);

  if (candles.length === 0) {
    ctx.fillStyle = LABEL_COLOR_SUBTLE;
    ctx.font = FONT_EMPTY_BODY;
    ctx.fillText('(no data)', 8, 18);
    return;
  }

  const plotW = Math.max(1, width - padL - padR);
  const plotH = Math.max(1, height - padT - padB);
  const plotTop = padT;
  const plotBottom = padT + plotH;

  const plan = computeYPlan(candles, opts);
  const yTo = makeYTo(plan, plotTop, plotBottom);

  // Gridlines + Y labels
  ctx.strokeStyle = COLORS.grid;
  ctx.lineWidth = 1;
  ctx.font = FONT_LABEL_MONO;
  ctx.fillStyle = LABEL_COLOR_MUTED;
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';

  if (!plan.break) {
    const gridSteps = 4;
    for (let i = 0; i <= gridSteps; i++) {
      const v = plan.ymin + (plan.ymax - plan.ymin) * i / gridSteps;
      const y = yTo(v);
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(padL + plotW, y);
      ctx.stroke();
      ctx.fillText(v.toFixed(3), padL - 3, y);
    }
  } else {
    // Main region gridlines (4 steps from ymin to mainUpper)
    const gridSteps = 4;
    for (let i = 0; i <= gridSteps; i++) {
      const v = plan.ymin + (plan.mainUpper - plan.ymin) * i / gridSteps;
      const y = yTo(v);
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(padL + plotW, y);
      ctx.stroke();
      ctx.fillText(v.toFixed(3), padL - 3, y);
    }
    // Extreme band: single label at absMax
    const yAbs = yTo(plan.absMax);
    ctx.beginPath();
    ctx.moveTo(padL, yAbs);
    ctx.lineTo(padL + plotW, yAbs);
    ctx.stroke();
    ctx.fillText(plan.absMax.toFixed(3), padL - 3, yAbs);
  }

  // Axis line
  ctx.strokeStyle = COLORS.axis;
  ctx.beginPath();
  ctx.moveTo(padL, plotTop);
  ctx.lineTo(padL, plotBottom);
  ctx.lineTo(padL + plotW, plotBottom);
  ctx.stroke();

  // X slots
  const n = candles.length;
  const slotW = plotW / n;
  const bodyW = Math.max(
    CANDLE_WIDTH_MIN,
    Math.min(CANDLE_WIDTH_MAX, slotW * CANDLE_WIDTH_RATIO)
  );

  // Prediction band (drawn under candles)
  if (opts.regression) {
    const bu = opts.regression.band_upper || [];
    const bl = opts.regression.band_lower || [];
    if (bu.length === candles.length && bl.length === candles.length) {
      ctx.fillStyle = COLORS.band;
      ctx.beginPath();
      for (let i = 0; i < candles.length; i++) {
        const x = padL + slotW * (i + 0.5);
        const y = yTo(bu[i]);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      for (let i = candles.length - 1; i >= 0; i--) {
        const x = padL + slotW * (i + 0.5);
        ctx.lineTo(x, yTo(bl[i]));
      }
      ctx.closePath();
      ctx.fill();
    }
  }

  const highlighted = new Set(opts.regression?.highlighted_bucket_keys || []);

  // Candles
  candles.forEach((c, i) => {
    const x = padL + slotW * (i + 0.5);
    const s = c.stats;
    const yQ1 = yTo(s.q1);
    const yQ3 = yTo(s.q3);
    const yMed = yTo(s.median);
    const yHigh = yTo(s.whisker_high);
    const yLow = yTo(s.whisker_low);

    // Whisker vertical + caps
    ctx.strokeStyle = COLORS.whisker;
    ctx.beginPath();
    ctx.moveTo(x, yHigh);
    ctx.lineTo(x, yLow);
    ctx.moveTo(x - bodyW / 3, yHigh);
    ctx.lineTo(x + bodyW / 3, yHigh);
    ctx.moveTo(x - bodyW / 3, yLow);
    ctx.lineTo(x + bodyW / 3, yLow);
    ctx.stroke();

    // Box body
    ctx.fillStyle = c.live ? COLORS.liveBody : COLORS.body;
    ctx.fillRect(x - bodyW / 2, yQ3, bodyW, Math.max(1, yQ1 - yQ3));
    ctx.strokeStyle = highlighted.has(c.key) ? COLORS.residualHighlight : COLORS.bodyStroke;
    ctx.lineWidth = highlighted.has(c.key) ? 2 : 1;
    ctx.strokeRect(x - bodyW / 2, yQ3, bodyW, Math.max(1, yQ1 - yQ3));
    ctx.lineWidth = 1;

    // Median
    ctx.strokeStyle = COLORS.median;
    ctx.beginPath();
    ctx.moveTo(x - bodyW / 2, yMed);
    ctx.lineTo(x + bodyW / 2, yMed);
    ctx.stroke();

    // Outliers & extremes
    ctx.fillStyle = COLORS.outlier;
    (s.outliers || []).forEach(v => {
      const yv = yTo(v);
      ctx.beginPath();
      ctx.arc(x, yv, 2, 0, Math.PI * 2);
      ctx.fill();
    });
    ctx.strokeStyle = COLORS.extreme;
    (s.extremes || []).forEach(v => {
      const yv = yTo(v);
      ctx.beginPath();
      ctx.moveTo(x - 3, yv);
      ctx.lineTo(x + 3, yv);
      ctx.moveTo(x, yv - 3);
      ctx.lineTo(x, yv + 3);
      ctx.stroke();
    });
  });

  // Trend line
  if (opts.regression && (opts.regression.trend || []).length === candles.length) {
    ctx.strokeStyle = COLORS.trend;
    ctx.lineWidth = 2;
    ctx.beginPath();
    opts.regression.trend.forEach((v, i) => {
      const x = padL + slotW * (i + 0.5);
      const y = yTo(v);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.lineWidth = 1;
  }

  // Moving averages over median sequence
  const medians = candles.map(c => c.stats.median);
  (opts.ma || [7]).forEach(w => {
    const line = ma(medians, w);
    ctx.strokeStyle = COLORS.ma;
    ctx.lineWidth = 1;
    ctx.beginPath();
    let started = false;
    line.forEach((v, i) => {
      if (v == null) return;
      const x = padL + slotW * (i + 0.5);
      const y = yTo(v);
      if (!started) { ctx.moveTo(x, y); started = true; }
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });

  // Break marker (drawn on top, at gap between main and extreme band)
  if (plan.break) {
    const breakBandH = plotH * Y_BREAK_BAND_RATIO;
    const gapMidY = plotTop + breakBandH + Y_BREAK_GAP_PX / 2;
    drawBreakMarker(ctx, padL + 1, padL + plotW, gapMidY);
  }

  // X labels
  ctx.fillStyle = LABEL_COLOR_MUTED;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  ctx.font = FONT_LABEL_MONO;
  const unit = opts.unit || 'day';
  const { format, stride } = pickLabelPlan(ctx, candles, slotW, unit);
  for (let i = 0; i < n; i++) {
    const forced = (i === 0 || i === n - 1);
    if (!forced && i % stride !== 0) continue;
    const label = format(candles[i].key);
    ctx.fillText(label, padL + slotW * (i + 0.5), padT + plotH + 4);
  }
}

// ---------- Line chart (raw samples) ----------

// Refined Classic v2.0 — UI 액센트(#6b96c8)와 통일.
const LINE_COLORS = {
  line: '#6b96c8',
  point: '#2c4a6b',
  axis: '#999999',
  grid: '#e2e2e2',
  label: '#555555',
};

/**
 * Render a time-series line chart for raw samples.
 * @param {HTMLCanvasElement} canvas
 * @param {Array<{id:number, sampled_at:string, value:number}>} samples - chronological order
 * @param {object} opts - { scroll?: boolean, showPoints?: boolean }
 */
export function renderLineChart(canvas, samples, opts = {}) {
  const padL = 60, padR = 12, padT = 8, padB = 32;
  const { ctx, width, height } = prepareCanvas(canvas, samples.length, padL, padR, !!opts.scroll);

  if (!samples || samples.length === 0) {
    ctx.fillStyle = LABEL_COLOR_SUBTLE;
    ctx.font = FONT_EMPTY_BODY;
    ctx.fillText('(no samples)', 8, 18);
    return;
  }

  const times = samples.map(s => new Date(s.sampled_at).getTime());
  const values = samples.map(s => Number(s.value));
  const xMin = times[0];
  const xMax = times[times.length - 1];
  const xRange = Math.max(1, xMax - xMin);

  let ymin = +Infinity, ymax = -Infinity;
  for (const v of values) {
    if (v < ymin) ymin = v;
    if (v > ymax) ymax = v;
  }
  const ys = niceScale(ymin, ymax);

  const plotW = Math.max(1, width - padL - padR);
  const plotH = Math.max(1, height - padT - padB);
  const plotTop = padT;
  const plotBottom = padT + plotH;
  const xTo = t => padL + (t - xMin) / xRange * plotW;
  const yTo = v => plotBottom - (v - ys.min) / (ys.max - ys.min) * plotH;

  // Y grid + labels
  ctx.strokeStyle = LINE_COLORS.grid;
  ctx.lineWidth = 1;
  ctx.font = FONT_LABEL_MONO;
  ctx.fillStyle = LINE_COLORS.label;
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  const gridSteps = 4;
  for (let i = 0; i <= gridSteps; i++) {
    const v = ys.min + (ys.max - ys.min) * i / gridSteps;
    const y = yTo(v);
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(padL + plotW, y);
    ctx.stroke();
    ctx.fillText(v.toFixed(3), padL - 3, y);
  }

  // Axes
  ctx.strokeStyle = LINE_COLORS.axis;
  ctx.beginPath();
  ctx.moveTo(padL, plotTop);
  ctx.lineTo(padL, plotBottom);
  ctx.lineTo(padL + plotW, plotBottom);
  ctx.stroke();

  // Line segments
  ctx.strokeStyle = LINE_COLORS.line;
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  for (let i = 0; i < samples.length; i++) {
    const x = xTo(times[i]);
    const y = yTo(values[i]);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();
  ctx.lineWidth = 1;

  // Point markers — skip if too many to avoid clutter
  if (opts.showPoints !== false && samples.length <= 300) {
    ctx.fillStyle = LINE_COLORS.point;
    for (let i = 0; i < samples.length; i++) {
      ctx.beginPath();
      ctx.arc(xTo(times[i]), yTo(values[i]), 1.5, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // X labels — 5~8 evenly spaced ticks, with sampled_at formatted
  const tickCount = Math.min(8, Math.max(2, Math.floor(plotW / 90)));
  ctx.fillStyle = LINE_COLORS.label;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  const sameDay = sameUtcDay(new Date(xMin), new Date(xMax));
  for (let i = 0; i <= tickCount; i++) {
    const t = xMin + (xRange * i) / tickCount;
    const x = padL + plotW * i / tickCount;
    const dt = new Date(t);
    const label = sameDay ? fmtHMS(dt) : fmtMMDDHM(dt);
    ctx.fillText(label, x, plotBottom + 4);
  }
  // Secondary label: show date range at bottom-left
  if (!sameDay) {
    ctx.textAlign = 'left';
    ctx.fillStyle = LABEL_COLOR_SUBTLE;
    const d1 = fmtYMD(new Date(xMin));
    const d2 = fmtYMD(new Date(xMax));
    ctx.fillText(`${d1} ~ ${d2}  (n=${samples.length})`, padL, plotBottom + 18);
  } else {
    ctx.textAlign = 'left';
    ctx.fillStyle = LABEL_COLOR_SUBTLE;
    ctx.fillText(`${fmtYMD(new Date(xMin))}  (n=${samples.length})`, padL, plotBottom + 18);
  }
}

function pad2(n) { return n < 10 ? '0' + n : '' + n; }
function fmtYMD(d) { return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}`; }
function fmtHMS(d) { return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}:${pad2(d.getUTCSeconds())}`; }
function fmtMMDDHM(d) { return `${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`; }
function sameUtcDay(a, b) {
  return a.getUTCFullYear() === b.getUTCFullYear()
    && a.getUTCMonth() === b.getUTCMonth()
    && a.getUTCDate() === b.getUTCDate();
}

// ---------- Boxplot X label helpers ----------

function fmtFull(key) { return key; }
function fmtShort(key, unit) {
  if (unit === 'hour') {
    // "YYYY-MM-DDTHH" → "MM-DD HH"
    if (key.length >= 13) return key.slice(5, 10) + ' ' + key.slice(11, 13);
    return key;
  }
  if (unit === 'day') return key.length >= 10 ? key.slice(5) : key;
  if (unit === 'week') { const i = key.indexOf('-W'); return i >= 0 ? key.slice(i + 1) : key; }
  if (unit === 'month') return key.length >= 7 ? key.slice(5) : key;
  return key;
}

function pickLabelPlan(ctx, candles, slotW, unit) {
  if (candles.length === 0) return { format: fmtFull, stride: 1 };
  const sample = candles[Math.floor(candles.length / 2)].key;
  const fullW = ctx.measureText(fmtFull(sample)).width;
  const shortW = ctx.measureText(fmtShort(sample, unit)).width;
  const strideFull = Math.max(1, Math.ceil((fullW + X_LABEL_GAP_PX) / slotW));
  if (strideFull === 1) return { format: fmtFull, stride: 1 };
  const strideShort = Math.max(1, Math.ceil((shortW + X_LABEL_GAP_PX) / slotW));
  if (strideShort <= strideFull) return { format: (k) => fmtShort(k, unit), stride: strideShort };
  return { format: fmtFull, stride: strideFull };
}
