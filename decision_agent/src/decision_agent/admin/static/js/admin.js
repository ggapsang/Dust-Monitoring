// Decision Agent Admin — vanilla JS, no framework, no transitions.
// Concerns:
//   1. role_mapping panel  (load + edit modal + reload)
//   2. alarm_mapping panel (load + edit modal + reload)
//   3. decisions browser   (3 tabs + pager + force-decide modal)
//   4. status bar          (5s polling)

(function () {
  'use strict';

  // -------------------------------------------------------------------- helpers
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  function el(tag, props, children) {
    const node = document.createElement(tag);
    if (props) for (const k in props) {
      if (k === 'class') node.className = props[k];
      else if (k === 'text') node.textContent = props[k];
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), props[k]);
      else if (k === 'data') for (const dk in props.data) node.dataset[dk] = props.data[dk];
      else node.setAttribute(k, props[k]);
    }
    if (children) for (const c of children) node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    return node;
  }

  function fmtTs(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const pad = n => String(n).padStart(2, '0');
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  function fmtHM(iso) {
    if (!iso) return '-';
    const d = new Date(iso);
    if (isNaN(d)) return '-';
    const pad = n => String(n).padStart(2, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function shortId(id) { return id ? String(id).slice(0, 8) : ''; }

  async function api(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    let data = null;
    try { data = await res.json(); } catch (_) { /* may be empty */ }
    if (!res.ok) {
      const msg = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
      throw new Error(msg);
    }
    return data;
  }

  // -------------------------------------------------------------------- toast
  function toast(msg, kind) {
    const c = $('#toast-container');
    const t = el('div', { class: 'toast ' + (kind || '') , text: msg });
    c.appendChild(t);
    setTimeout(() => t.remove(), 3000);
  }

  // -------------------------------------------------------------------- modals
  function openModal(id) { $('#' + id).classList.add('open'); }
  function closeModal(id) { $('#' + id).classList.remove('open'); }

  document.addEventListener('click', ev => {
    const tgt = ev.target.closest('[data-modal-close]');
    if (tgt) closeModal(tgt.dataset.modalClose);
  });

  // -------------------------------------------------------------------- role_mapping
  let _roleEditing = null;

  async function loadRoleMapping() {
    const rows = await api('GET', '/admin/api/role-mapping');
    const tbody = $('#tbl-roles tbody');
    tbody.innerHTML = '';
    rows.forEach(r => {
      const tr = el('tr', null, [
        el('td', { class: 'mono', text: r.detection_role }),
        el('td', { class: 'mono', text: r.component_name }),
        el('td', { class: 'muted', text: fmtTs(r.updated_at) }),
        el('td', { class: 'right' }, [
          el('button', {
            class: 'btn',
            text: 'Edit',
            onclick: () => {
              _roleEditing = r.detection_role;
              $('#mr-role').textContent = r.detection_role;
              $('#mr-component').value = r.component_name;
              openModal('modal-role');
            }
          })
        ])
      ]);
      tbody.appendChild(tr);
    });
  }

  $('#mr-save').addEventListener('click', async () => {
    if (!_roleEditing) return;
    try {
      await api('PATCH', '/admin/api/role-mapping/' + encodeURIComponent(_roleEditing), {
        component_name: $('#mr-component').value
      });
      closeModal('modal-role');
      toast('role_mapping updated', 'success');
      await loadRoleMapping();
      await loadStatus();
    } catch (e) { toast('Failed: ' + e.message, 'error'); }
  });

  $('#btn-reload-roles').addEventListener('click', async () => {
    try {
      await api('POST', '/admin/api/reload/role-mapping');
      toast('role_mapping reloaded', 'success');
      await loadRoleMapping();
      await loadStatus();
    } catch (e) { toast('Failed: ' + e.message, 'error'); }
  });

  // -------------------------------------------------------------------- alarm_mapping
  let _alarmEditing = null;

  async function loadAlarmMapping() {
    const rows = await api('GET', '/admin/api/alarm-mapping');
    const tbody = $('#tbl-alarms tbody');
    tbody.innerHTML = '';
    rows.forEach(r => {
      const finalCell = el('td', null, [
        el('span', { class: 'judgment-badge ' + r.final_decision, text: r.final_decision })
      ]);
      const tr = el('tr', null, [
        el('td', { class: 'mono', text: r.iot_sensor_level }),
        el('td', { class: 'mono', text: r.static_model_result }),
        el('td', { class: 'mono', text: r.dynamic_model_result }),
        finalCell,
        el('td', { class: 'right' }, [
          el('button', {
            class: 'btn',
            text: 'Edit',
            onclick: () => {
              _alarmEditing = r.id;
              $('#ma-id').textContent = r.id;
              $('#ma-iot').textContent = r.iot_sensor_level;
              $('#ma-static').textContent = r.static_model_result;
              $('#ma-dynamic').textContent = r.dynamic_model_result;
              $('#ma-final').value = r.final_decision;
              openModal('modal-alarm');
            }
          })
        ])
      ]);
      tbody.appendChild(tr);
    });
  }

  $('#ma-save').addEventListener('click', async () => {
    if (_alarmEditing == null) return;
    try {
      await api('PATCH', '/admin/api/alarm-mapping/' + _alarmEditing, {
        final_decision: $('#ma-final').value
      });
      closeModal('modal-alarm');
      toast('alarm_mapping updated', 'success');
      await loadAlarmMapping();
      await loadStatus();
    } catch (e) { toast('Failed: ' + e.message, 'error'); }
  });

  $('#btn-reload-alarms').addEventListener('click', async () => {
    try {
      await api('POST', '/admin/api/reload/alarm-mapping');
      toast('alarm_mapping reloaded', 'success');
      await loadAlarmMapping();
      await loadStatus();
    } catch (e) { toast('Failed: ' + e.message, 'error'); }
  });

  // -------------------------------------------------------------------- decisions
  const PAGE_SIZE = 100;
  let _tab = 'recent';
  let _page = 1;
  let _forceTarget = null;

  $$('.tab-btn').forEach(b => b.addEventListener('click', () => {
    $$('.tab-btn').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    _tab = b.dataset.tab;
    _page = 1;
    loadDecisions();
  }));

  $('#btn-prev').addEventListener('click', () => { if (_page > 1) { _page--; loadDecisions(); } });
  $('#btn-next').addEventListener('click', () => { _page++; loadDecisions(); });

  async function loadDecisions() {
    const url = `/admin/api/decisions?tab=${_tab}&page=${_page}&page_size=${PAGE_SIZE}`;
    let data;
    try {
      data = await api('GET', url);
    } catch (e) {
      toast('Load failed: ' + e.message, 'error');
      return;
    }
    const tbody = $('#tbl-decisions tbody');
    tbody.innerHTML = '';
    data.rows.forEach(r => {
      const tr = el('tr', null, [
        el('td', { class: 'mono', text: shortId(r.id) }),
        el('td', { text: r.station_label || r.station_id }),
        el('td', { class: 'muted', text: fmtTs(r.observation_timestamp) }),
        el('td', { class: 'mono ' + channelClass(r.anomaly_detection_result), text: r.anomaly_detection_result }),
        el('td', { class: 'mono ' + channelClass(r.object_detection_result), text: r.object_detection_result }),
        el('td', { class: 'mono ' + channelClass(r.sensor_analysis_result), text: r.sensor_analysis_result }),
        el('td', null, [el('span', { class: 'judgment-badge ' + r.final_decision, text: r.final_decision })]),
        el('td', { class: 'muted', text: fmtTs(r.decided_at) }),
        el('td', { class: 'muted', text: fmtTs(r.sent_at) }),
        el('td', { class: 'right' }, _tab === 'stuck' ? [
          el('button', {
            class: 'btn btn-danger',
            text: 'Force Decide',
            onclick: () => openForceModal(r)
          })
        ] : [])
      ]);
      tbody.appendChild(tr);
    });
    // pager + meta
    const totalPages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
    $('#pager-info').textContent = `page ${data.page} / ${totalPages}`;
    $('#btn-prev').disabled = data.page <= 1;
    $('#btn-next').disabled = data.page >= totalPages;
    $('#tab-meta').textContent = `total: ${data.total}`;
  }

  function channelClass(v) {
    if (v === 'warning') return 'danger';
    if (v === 'caution') return 'warn';
    if (v === 'pending') return 'muted';
    return '';
  }

  function openForceModal(r) {
    _forceTarget = r.id;
    $('#mf-id').textContent = r.id;
    $('#mf-station').textContent = r.station_label || r.station_id;
    $('#mf-anomaly').textContent = r.anomaly_detection_result;
    $('#mf-object').textContent = r.object_detection_result;
    $('#mf-sensor').textContent = r.sensor_analysis_result;
    $('#mf-final').value = 'caution';
    openModal('modal-force');
  }

  $('#mf-save').addEventListener('click', async () => {
    if (!_forceTarget) return;
    try {
      await api('POST', '/admin/api/decisions/' + _forceTarget + '/force', {
        final_decision: $('#mf-final').value
      });
      closeModal('modal-force');
      toast('record force-decided', 'success');
      await loadDecisions();
      await loadStatus();
    } catch (e) { toast('Failed: ' + e.message, 'error'); }
  });

  // -------------------------------------------------------------------- status bar
  async function loadStatus() {
    try {
      const s = await api('GET', '/admin/api/status');
      $('#st-pending').textContent = `pending: ${s.pending}`;
      $('#st-decided').textContent = `decided/h: ${s.decided_last_hour}`;
      const stuckEl = $('#st-stuck');
      stuckEl.textContent = `stuck: ${s.stuck}`;
      stuckEl.classList.toggle('warn', s.stuck > 0);
      $('#st-cache').textContent = `cache: alarm=${fmtHM(s.alarm_mapping_loaded_at)} role=${fmtHM(s.role_mapping_loaded_at)}`;
      const db = $('#st-db');
      db.textContent = s.db_ok ? 'OK' : 'DOWN';
      db.classList.toggle('down', !s.db_ok);
      db.classList.toggle('ok', s.db_ok);
    } catch (e) {
      const db = $('#st-db');
      db.textContent = 'DOWN';
      db.classList.add('down');
      db.classList.remove('ok');
    }
  }

  // -------------------------------------------------------------------- resizers
  // Two splitters:
  //   #rsx — between .side column and .main column (horizontal drag)
  //   #rsy — between top panel and bottom panel inside .side (vertical drag)
  // Persist user-chosen sizes in localStorage so they survive reloads.

  const LS_SIDE_W = 'da-admin.side-w';
  const LS_TOP_H  = 'da-admin.top-h';
  const MIN_SIDE_W = 240;
  const MAX_SIDE_W = 800;
  const MIN_PANEL_H = 80;

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  // restore persisted sizes
  const savedSideW = parseInt(localStorage.getItem(LS_SIDE_W), 10);
  if (savedSideW && !isNaN(savedSideW)) {
    document.documentElement.style.setProperty('--side-w', clamp(savedSideW, MIN_SIDE_W, MAX_SIDE_W) + 'px');
  }
  const savedTopH = parseInt(localStorage.getItem(LS_TOP_H), 10);
  if (savedTopH && !isNaN(savedTopH)) {
    document.documentElement.style.setProperty('--top-h', savedTopH + 'px');
  }

  function startDrag(handle, axis, getStart, applyDelta) {
    handle.addEventListener('mousedown', (ev) => {
      ev.preventDefault();
      const startPos = axis === 'x' ? ev.clientX : ev.clientY;
      const startVal = getStart();
      document.body.classList.add('resizing');
      if (axis === 'y') document.body.classList.add('row');

      const onMove = (e) => {
        const cur = axis === 'x' ? e.clientX : e.clientY;
        applyDelta(startVal + (cur - startPos));
      };
      const onUp = () => {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        document.body.classList.remove('resizing', 'row');
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  const sideEl = $('.side');
  const topPanelEl = $('#panel-roles');

  startDrag(
    $('#rsx'),
    'x',
    () => sideEl.getBoundingClientRect().width,
    (w) => {
      const v = clamp(w, MIN_SIDE_W, MAX_SIDE_W);
      document.documentElement.style.setProperty('--side-w', v + 'px');
      localStorage.setItem(LS_SIDE_W, String(Math.round(v)));
    }
  );

  startDrag(
    $('#rsy'),
    'y',
    () => topPanelEl.getBoundingClientRect().height,
    (h) => {
      // Ensure both top and bottom panels keep at least MIN_PANEL_H px.
      const sideH = sideEl.getBoundingClientRect().height;
      const maxTop = sideH - MIN_PANEL_H - 4; // 4 = resizer height
      const v = clamp(h, MIN_PANEL_H, maxTop);
      document.documentElement.style.setProperty('--top-h', v + 'px');
      localStorage.setItem(LS_TOP_H, String(Math.round(v)));
    }
  );

  // -------------------------------------------------------------------- classification_threshold
  // key 'dust'(IOT 센서) / 'static'(정적분진) / 'dynamic'(동적분진) ↔ 입력 필드.
  const TH_KEYS = { dust: '#th-dust', static: '#th-static', dynamic: '#th-dynamic' };

  async function loadThresholds() {
    let rows;
    try { rows = await api('GET', '/admin/api/classification-threshold'); }
    catch (e) { return; }
    for (const r of rows) {
      const sel = TH_KEYS[r.key];
      if (sel && $(sel)) $(sel).value = r.threshold;
    }
  }

  $('#th-save').addEventListener('click', async () => {
    try {
      for (const k in TH_KEYS) {
        const v = parseFloat($(TH_KEYS[k]).value);
        if (Number.isNaN(v)) { toast('숫자를 입력하세요: ' + k, 'error'); return; }
        await api('PATCH', '/admin/api/classification-threshold/' + k, { threshold: v });
      }
      toast('분류 임계값 저장됨', 'success');
      await loadThresholds();
    } catch (e) {
      toast('Failed: ' + e.message, 'error');
    }
  });

  // -------------------------------------------------------------------- boot
  async function boot() {
    await Promise.all([loadRoleMapping(), loadAlarmMapping(), loadDecisions(), loadStatus(), loadThresholds()]);
    setInterval(loadStatus, 5000);
  }
  boot();
})();
