// Thin REST wrapper.
export async function getStations() {
  const r = await fetch('/api/stations');
  if (!r.ok) throw new Error('stations: ' + r.status);
  return r.json();
}

export async function getChart(station, unit = 'day', range = 'all') {
  const r = await fetch(`/api/chart/${encodeURIComponent(station)}?unit=${unit}&range=${range}`);
  if (!r.ok) throw new Error('chart: ' + r.status);
  return r.json();
}

export async function getHealth() {
  const r = await fetch('/api/health');
  return r.json();
}

export async function getRawSamples(station, limit = 500) {
  const r = await fetch(`/api/raw/${encodeURIComponent(station)}?limit=${limit}`);
  if (!r.ok) throw new Error('raw: ' + r.status);
  return r.json();
}

export async function getSettings() {
  const r = await fetch('/api/settings');
  if (!r.ok) throw new Error('settings: ' + r.status);
  return r.json();
}

export async function putSettings(values) {
  const r = await fetch('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ values }),
  });
  if (!r.ok) throw new Error('settings: ' + r.status);
  return r.json();
}

export async function runRegression(station, body) {
  const r = await fetch(`/api/regression/${encodeURIComponent(station)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = null;
    try { detail = await r.json(); } catch (_) {}
    const err = new Error('regression: ' + r.status);
    err.status = r.status;
    err.detail = detail?.detail;
    throw err;
  }
  return r.json();
}

// --- Station sync (live reconcile from SocketDaim DB) -----------------

export async function triggerStationSync() {
  const r = await fetch('/api/stations/sync', { method: 'POST' });
  if (!r.ok) throw new Error('sync: ' + r.status);
  return r.json();
}

export async function getStationConflicts() {
  const r = await fetch('/api/stations/conflicts');
  if (!r.ok) throw new Error('conflicts: ' + r.status);
  return r.json();
}

export async function resolveStationConflict(stationName, action) {
  const r = await fetch('/api/stations/conflicts/resolve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ station_name: stationName, action }),
  });
  if (!r.ok) {
    let detail = null;
    try { detail = await r.json(); } catch (_) {}
    const err = new Error('resolve: ' + r.status);
    err.status = r.status;
    err.detail = detail?.detail;
    throw err;
  }
  return r.json();
}
