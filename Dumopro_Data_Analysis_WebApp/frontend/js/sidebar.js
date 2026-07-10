import { getSettings, putSettings } from './api.js';

const FIELDS = [
  { key: 'poll_interval_sec', label: 'Poll interval (sec)', type: 'number', step: '0.1', min: '0.1', group: 'Poller (restart required)' },
  { key: 'poll_batch_limit', label: 'Batch limit', type: 'number', step: '10', min: '1', group: 'Poller (restart required)' },
  { key: 'restart_wait_sec', label: 'Restart wait (sec)', type: 'number', step: '1', min: '1', group: 'Poller (restart required)' },
  { key: 'consecutive_failure_cap', label: 'Consecutive failure cap', type: 'number', step: '1', min: '1', group: 'Poller (restart required)' },
  { key: 'grace_period_sec', label: 'Grace period (sec)', type: 'number', step: '1', min: '0', group: 'Poller (restart required)' },
  { key: 'regression_degree', label: 'Poly degree', type: 'number', step: '1', min: '1', max: '5', group: 'Regression (next run)' },
  { key: 'regression_band_n', label: 'Band N × RMSE', type: 'number', step: '0.1', min: '0.5', max: '5', group: 'Regression (next run)' },
  { key: 'regression_percentile', label: 'Residual percentile', type: 'number', step: '0.5', min: '50', max: '99.9', group: 'Regression (next run)' },
  { key: 'regression_default_target', label: 'Default target', type: 'select', options: ['median','max','q3'], group: 'Regression (next run)' },
  { key: 'chart_initial_unit', label: 'Initial unit', type: 'select', options: ['day','week','month'], group: 'Chart (immediate)' },
  { key: 'chart_default_ma', label: 'Default MA (csv)', type: 'text', group: 'Chart (immediate)' },
  { key: 'residual_cap', label: 'Residual cap', type: 'number', step: '100', min: '100', group: 'Chart (immediate)' },
];

function renderField(f, value) {
  if (f.type === 'select') {
    return `<label style="display:block;margin:2px 0">
      <span style="display:inline-block;min-width:150px">${f.label}</span>
      <select data-setting="${f.key}">
        ${f.options.map(o => `<option value="${o}"${o === String(value) ? ' selected' : ''}>${o}</option>`).join('')}
      </select>
    </label>`;
  }
  const extra = [];
  if (f.step) extra.push(`step="${f.step}"`);
  if (f.min) extra.push(`min="${f.min}"`);
  if (f.max) extra.push(`max="${f.max}"`);
  return `<label style="display:block;margin:2px 0">
    <span style="display:inline-block;min-width:150px">${f.label}</span>
    <input type="${f.type}" data-setting="${f.key}" value="${value == null ? '' : value}" ${extra.join(' ')} style="width:90px">
  </label>`;
}

export async function renderSidebarSettings(root) {
  const s = await getSettings();
  const vals = s.values || {};
  const groups = {};
  for (const f of FIELDS) (groups[f.group] ||= []).push(f);
  let html = '';
  for (const [group, fields] of Object.entries(groups)) {
    html += `<div class="sidebar-section"><b>${group}</b>`;
    for (const f of fields) html += renderField(f, vals[f.key]);
    html += `</div>`;
  }
  html += `<div style="margin-top:6px"><button data-save-settings>저장</button> <span data-settings-status class="muted"></span></div>`;
  root.innerHTML = html;

  root.querySelector('[data-save-settings]').addEventListener('click', async () => {
    const values = {};
    root.querySelectorAll('[data-setting]').forEach(el => {
      const v = el.value;
      values[el.dataset.setting] = v;
    });
    const statusEl = root.querySelector('[data-settings-status]');
    statusEl.textContent = '저장 중...';
    try {
      await putSettings(values);
      statusEl.textContent = '저장됨';
      setTimeout(() => { statusEl.textContent = ''; }, 2000);
    } catch (err) {
      statusEl.textContent = '실패';
      statusEl.className = 'err';
      console.error(err);
    }
  });
}
