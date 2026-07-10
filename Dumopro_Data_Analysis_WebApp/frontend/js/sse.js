// Single multiplexed EventSource for ALL stations.
//
// One `/api/stream` connection carries every station's events (each payload has
// a `station` field). We demux client-side and fan out to per-station
// subscribers. This avoids opening one EventSource per station, which would
// exhaust the browser's HTTP/1.1 ~6-connections-per-origin limit and make page
// reloads (F5) slow.
//
// Public API is unchanged: subscribe(station, onEvent) -> unsubscribe fn.

import { recordActivity } from './activity.js';

const subsByStation = new Map(); // station -> Set<fn>
let es = null;
let refcount = 0;

function ensureStream() {
  if (es) return;
  es = new EventSource('/api/stream');

  const dispatch = (type, ev) => {
    let data;
    try {
      data = JSON.parse(ev.data);
    } catch (_) {
      return;
    }
    const station = data && data.station;
    if (!station) return;
    if (type === 'candle_update') recordActivity(station);
    const subs = subsByStation.get(station);
    if (subs) subs.forEach(fn => { try { fn(data); } catch (e) { console.warn(e); } });
  };

  es.addEventListener('candle_update', (ev) => dispatch('candle_update', ev));
  es.addEventListener('candle_frozen', (ev) => dispatch('candle_frozen', ev));
  es.addEventListener('station_stalled', (ev) => dispatch('station_stalled', ev));
  es.onerror = () => { /* EventSource auto-reconnects */ };
}

function closeStreamIfIdle() {
  if (refcount === 0 && es) {
    es.close();
    es = null;
  }
}

export function subscribe(station, onEvent) {
  ensureStream();
  let subs = subsByStation.get(station);
  if (!subs) {
    subs = new Set();
    subsByStation.set(station, subs);
  }
  subs.add(onEvent);
  refcount++;

  return () => {
    const set = subsByStation.get(station);
    if (set) {
      set.delete(onEvent);
      if (set.size === 0) subsByStation.delete(station);
    }
    refcount = Math.max(0, refcount - 1);
    closeStreamIfIdle();
  };
}

// Close the shared stream promptly on navigation so the server releases the
// connection before the browser reload re-opens it.
window.addEventListener('beforeunload', () => {
  if (es) { es.close(); es = null; }
});
