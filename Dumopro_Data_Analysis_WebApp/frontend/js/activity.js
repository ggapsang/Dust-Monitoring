// Tracks which station is "currently receiving data" based on wall-clock timing
// of SSE candle_update events. Mock round-robins per station for 30s at 1Hz,
// poll interval 1.5s → active station gets an SSE event every ~1.5s while its turn.

const ACTIVE_THRESHOLD_MS = 3000;   // no update for 3s → inactive
const CHECK_INTERVAL_MS = 500;      // 탐지 주기

const lastSeen = new Map();         // station -> wall-clock ms
const subscribers = new Set();      // fn(newActive, prevActive)
let currentActive = null;
let timer = null;

function pickActive() {
  let best = null;
  let bestTs = 0;
  const now = Date.now();
  for (const [station, ts] of lastSeen) {
    if (now - ts <= ACTIVE_THRESHOLD_MS && ts > bestTs) {
      best = station;
      bestTs = ts;
    }
  }
  return best;
}

function tick() {
  const next = pickActive();
  if (next !== currentActive) {
    const prev = currentActive;
    currentActive = next;
    for (const fn of subscribers) {
      try { fn(next, prev); } catch (e) { console.warn(e); }
    }
  }
}

function ensureTimer() {
  if (timer != null) return;
  timer = setInterval(tick, CHECK_INTERVAL_MS);
}

export function recordActivity(station) {
  lastSeen.set(station, Date.now());
  // Fast path: if we have no active and this is a fresh event, pick it up now.
  if (currentActive == null) tick();
}

export function getActiveStation() {
  return currentActive;
}

export function subscribeActive(fn) {
  subscribers.add(fn);
  ensureTimer();
  // Fire once with current state so new subscribers sync immediately.
  try { fn(currentActive, null); } catch (e) { console.warn(e); }
  return () => {
    subscribers.delete(fn);
  };
}
