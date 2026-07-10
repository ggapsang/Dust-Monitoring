import { renderGrid, teardownGrid } from './grid.js';
import { renderDetail, teardownDetail } from './detail.js';
import { renderSidebarSettings } from './sidebar.js';
import { subscribeActive } from './activity.js';
import { initStationSync } from './sync.js';

const tabs = new Map(); // id -> {label, kind, station?, el, contentRenderer, contentTeardown}
let activeId = null;

function $tabbar() { return document.getElementById('tabbar'); }
function $content() { return document.getElementById('tabcontent'); }

function renderTabChrome() {
  const bar = $tabbar();
  bar.innerHTML = '';
  for (const [id, tab] of tabs) {
    const el = document.createElement('div');
    el.className = 'tab' + (id === activeId ? ' active' : '');
    el.textContent = tab.label;
    el.addEventListener('click', () => setActive(id));
    if (tab.kind === 'detail') {
      const close = document.createElement('span');
      close.className = 'close';
      close.textContent = '×';
      close.title = '닫기';
      close.addEventListener('click', (e) => { e.stopPropagation(); closeTab(id); });
      el.appendChild(close);
    }
    bar.appendChild(el);
    tab.el = el;
  }
}

async function setActive(id) {
  if (activeId === id) return;
  const prev = tabs.get(activeId);
  if (prev && prev.contentTeardown) prev.contentTeardown();
  activeId = id;
  renderTabChrome();
  const curr = tabs.get(id);
  if (!curr) return;
  $content().innerHTML = '';
  try {
    await curr.contentRenderer($content());
  } catch (err) {
    $content().innerHTML = `<p class="err">Error: ${err.message}</p>`;
    console.error(err);
  }
}

function closeTab(id) {
  const tab = tabs.get(id);
  if (!tab || tab.kind === 'grid') return;
  if (tab.contentTeardown && id === activeId) tab.contentTeardown();
  tabs.delete(id);
  if (id === activeId) activeId = 'grid';
  renderTabChrome();
  if (activeId === 'grid') {
    const g = tabs.get('grid');
    $content().innerHTML = '';
    g.contentRenderer($content());
  }
}

function openDetail(stationName) {
  const id = 'detail:' + stationName;
  if (tabs.has(id)) {
    setActive(id);
    return;
  }
  tabs.set(id, {
    label: stationName,
    kind: 'detail',
    station: stationName,
    contentRenderer: (root) => renderDetail(root, stationName),
    contentTeardown: () => teardownDetail(stationName),
  });
  renderTabChrome();
  setActive(id);
}

(async function main() {
  tabs.set('grid', {
    label: 'Grid',
    kind: 'grid',
    contentRenderer: (root) => renderGrid(root),
    contentTeardown: () => teardownGrid(),
  });
  renderTabChrome();
  await setActive('grid');

  // Sidebar settings
  const settingsRoot = document.getElementById('settings-root');
  if (settingsRoot) {
    try {
      await renderSidebarSettings(settingsRoot);
    } catch (err) {
      settingsRoot.innerHTML = `<span class="err">Settings load failed</span>`;
      console.error(err);
    }
  }

  // Active station indicator (bottom of sidebar)
  const activeEl = document.getElementById('active-station');
  if (activeEl) {
    subscribeActive((next) => {
      if (next) {
        activeEl.textContent = next;
        activeEl.classList.remove('idle');
      } else {
        activeEl.textContent = '(없음)';
        activeEl.classList.add('idle');
      }
    });
  }

  document.addEventListener('open-detail', (e) => openDetail(e.detail.station));

  // After a station sync, the grid tab needs to refetch /api/stations.
  // We re-render whichever tab is active if it's the grid; detail tabs
  // for stations that disappeared will simply show no live updates.
  document.addEventListener('reload-grid', () => {
    if (activeId !== 'grid') return;
    const g = tabs.get('grid');
    if (!g) return;
    if (g.contentTeardown) g.contentTeardown();
    $content().innerHTML = '';
    g.contentRenderer($content());
  });

  // Stations sync widget (sidebar [Sync now] + conflict modal).
  initStationSync();
})();
