async function fetchDevices() {
  if (!state.user) return;
  try {
    const r = await apiFetch(`${API}/api/devices`, { headers: authHeaders() });
    const data = await r.json();
    state.devices = data.devices || {};
    renderDevices();
  } catch (e) {}
}

function renderDevices() {
  const ids = Object.keys(state.devices);
  const list = document.getElementById('deviceList');
  const empty = document.getElementById('deviceListEmpty');
  const dot = document.getElementById('deviceDot');
  const label = document.getElementById('deviceLabel');

  if (ids.length === 0) {
    empty.style.display = 'block';
    list.innerHTML = '';
    dot.className = 'device-dot';
    label.textContent = 'Нет устройств';
    state.selectedDevice = null;
    renderInputDeviceSelector();
    renderInputModeBtn();
    renderDevicePassport();
    return;
  }
  empty.style.display = 'none';
  if (!state.selectedDevice || !state.devices[state.selectedDevice]) {
    state.selectedDevice = ids[0];
  }
  const dev = state.devices[state.selectedDevice];
  dot.className = 'device-dot online';
  label.textContent = dev.info?.hostname || state.selectedDevice;
  list.innerHTML = ids.map(id => {
    const d = state.devices[id];
    const sel = id === state.selectedDevice ? ' selected' : '';
    const info = d.info || {};
    return `<div class="device-dropdown-item${sel}" data-action="select-device" data-device-id="${escapeAttr(encodeURIComponent(id))}">
      <span class="device-dot online"></span>
      <div><div>${escapeHTML(info.hostname || id)}</div><div class="device-os">${escapeHTML(info.os || '?')} — ${escapeHTML(id)}</div></div>
    </div>`;
  }).join('');
  renderInputDeviceSelector();
  renderDevicePassport();
}

function bindDeviceListActions() {
  const list = document.getElementById('deviceList');
  if (!list || list.dataset.delegated === '1') return;
  list.dataset.delegated = '1';
  list.addEventListener('click', (event) => {
    const target = event.target.closest('[data-action="select-device"]');
    if (!target || !list.contains(target)) return;
    selectDevice(decodeURIComponent(target.dataset.deviceId || ''));
  });
}

function selectDevice(id) {
  state.selectedDevice = id;
  state.sendTarget = 'single';
  renderDevices();
  closeDeviceDropdown();
  if (state.explorerOpen) explorerNavigate(state.explorerPath);
}

function deviceStatusLabel(value) {
  const labels = {
    activated: 'Активировано',
    activation_required: 'Нужна активация',
    degraded: 'Требует repair',
    activation_failed: 'Ошибка активации',
    ok: 'OK',
    warning: 'Внимание',
    critical: 'Критично',
    unavailable: 'Недоступно',
    unknown: 'Неизвестно',
    install_required: 'Нужен runtime',
    missing: 'Нужен runtime',
    broken: 'Runtime сломан',
  };
  return labels[value] || value || 'Неизвестно';
}

function deviceStatusClass(value) {
  if (['activated', 'ok'].includes(value)) return 'ok';
  if (['warning', 'degraded', 'install_required', 'missing', 'activation_required'].includes(value)) return 'warning';
  if (['critical', 'activation_failed', 'unavailable', 'broken'].includes(value)) return 'critical';
  return 'unknown';
}

function formatSnapshotTime(value) {
  if (!value) return 'Снимок ещё не собирался';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function metricValue(value, suffix) {
  if (value === null || value === undefined || value === '') return '—';
  return `${value}${suffix || ''}`;
}

function wait(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function renderDevicePassport() {
  const root = document.getElementById('devicePassport');
  if (!root) return;
  const ids = Object.keys(state.devices);
  if (!ids.length || !state.selectedDevice || !state.devices[state.selectedDevice]) {
    root.innerHTML = '<div class="device-passport-empty">Нет подключённых устройств</div>';
    return;
  }
  const id = state.selectedDevice;
  const dev = state.devices[id] || {};
  const info = dev.info || {};
  const busy = state.devicePanelBusy;
  const activationStatus = dev.activation_status || 'unknown';
  const runtimeStatus = dev.runtime_status || 'unknown';
  const pythonRuntimeStatus = dev.python_runtime_status || runtimeStatus;
  const healthStatus = dev.health_status || 'unknown';
  const identityStatus = dev.identity_status || 'unknown';
  const caps = dev.capabilities_summary || {};
  const capsList = Array.isArray(caps) ? caps : Object.keys(caps);
  const disconnectAvailable = capsList.includes('agent.disconnect') || caps.agent_disconnect === 'available';
  const activationAction = ['activation_required', 'unknown'].includes(activationStatus)
    ? `<button class="device-passport-btn" data-action="passport-activate" data-mode="soft" ${busy ? 'disabled' : ''}>Активировать</button>`
    : '';
  const repairAction = ['degraded', 'activation_failed'].includes(activationStatus)
    ? `<button class="device-passport-btn" data-action="passport-activate" data-mode="repair" ${busy ? 'disabled' : ''}>Repair</button>`
    : '';
  const runtimeNotice = runtimeStatus !== 'ok'
    ? `<div class="device-passport-notice">Runtime не готов. Managed Python не подготовлен.</div>`
    : '';
  const runtimeActions = ['missing', 'install_required', 'unknown'].includes(pythonRuntimeStatus)
    ? `<button class="device-passport-btn" data-action="passport-runtime" data-mode="prepare" ${busy ? 'disabled' : ''}>${busy === 'runtime' ? 'Подготовка...' : 'Подготовить Python'}</button>`
    : pythonRuntimeStatus === 'ok'
      ? `<button class="device-passport-btn" data-action="passport-runtime" data-mode="check" ${busy ? 'disabled' : ''}>${busy === 'runtime' ? 'Проверка...' : 'Проверить runtime'}</button>`
      : `<button class="device-passport-btn" data-action="passport-runtime" data-mode="repair" ${busy ? 'disabled' : ''}>${busy === 'runtime' ? 'Repair...' : 'Repair runtime'}</button>`;
  const error = state.devicePanelError ? `<div class="device-passport-error">${escapeHTML(state.devicePanelError)}</div>` : '';
  root.innerHTML = `
    <div class="device-passport-head">
      <div>
        <div class="device-passport-title">${escapeHTML(info.hostname || id)}</div>
        <div class="device-passport-subtitle">${escapeHTML(id)} · ${dev.connected ? 'online' : 'offline'}</div>
      </div>
      <span class="device-passport-dot ${dev.connected ? 'online' : ''}"></span>
    </div>
    <div class="device-passport-actions">
      <button class="device-passport-btn primary" data-action="passport-state" ${busy ? 'disabled' : ''}>${busy === 'state' ? 'Проверка...' : 'Проверить состояние'}</button>
      ${activationAction}
      ${repairAction}
      ${runtimeActions}
      <button class="device-passport-btn" data-action="passport-disconnect" ${busy ? 'disabled' : ''}>Отключить агент</button>
      <button class="device-passport-btn danger" data-action="passport-shutdown" ${busy ? 'disabled' : ''}>Выключить агент</button>
    </div>
    ${error}
    <div class="device-passport-section">
      <div class="device-passport-section-title">Паспорт</div>
      <div class="device-passport-grid">
        <div>OS</div><strong>${escapeHTML(info.os || info.os_caption || '—')}</strong>
        <div>Activation</div><span class="device-passport-badge ${deviceStatusClass(activationStatus)}">${escapeHTML(deviceStatusLabel(activationStatus))}</span>
        <div>Runtime</div><span class="device-passport-badge ${deviceStatusClass(runtimeStatus)}">${escapeHTML(deviceStatusLabel(runtimeStatus))}</span>
        <div>Python</div><span class="device-passport-badge ${deviceStatusClass(pythonRuntimeStatus)}">${escapeHTML(dev.python_version || deviceStatusLabel(pythonRuntimeStatus))}</span>
        <div>pip</div><span class="device-passport-badge ${deviceStatusClass(dev.pip_status || 'unknown')}">${escapeHTML(deviceStatusLabel(dev.pip_status || 'unknown'))}</span>
        <div>Health</div><span class="device-passport-badge ${deviceStatusClass(healthStatus)}">${escapeHTML(deviceStatusLabel(healthStatus))}</span>
        <div>Identity</div><span class="device-passport-badge ${deviceStatusClass(identityStatus)}">${escapeHTML(deviceStatusLabel(identityStatus))}</span>
        <div>Snapshot</div><strong>${escapeHTML(formatSnapshotTime(dev.last_snapshot_at))}</strong>
      </div>
    </div>
    <div class="device-passport-section">
      <div class="device-passport-section-title">Состояние</div>
      <div class="device-passport-metrics">
        <div><span>CPU</span><strong>${escapeHTML(metricValue(dev.cpu_load, '%'))}</strong></div>
        <div><span>RAM</span><strong>${escapeHTML(metricValue(dev.ram_used_pct, '%'))}</strong></div>
        <div><span>Disk</span><strong>${escapeHTML(metricValue(dev.disk_used_pct, '%'))}</strong></div>
        <div><span>Processes</span><strong>${escapeHTML(metricValue(dev.process_count))}</strong></div>
      </div>
      <div class="device-passport-uptime">Uptime: ${escapeHTML(metricValue(dev.uptime))}</div>
    </div>
    <details class="device-passport-details">
      <summary>Технические детали</summary>
      <pre>${escapeHTML(JSON.stringify({ info, capabilities: capsList, python_runtime: { status: pythonRuntimeStatus, version: dev.python_version, pip_status: dev.pip_status, venv_python: dev.venv_python, last_runtime_check: dev.last_runtime_check } }, null, 2))}</pre>
    </details>
  `;
  if (runtimeNotice) {
    root.querySelector('.device-passport-actions')?.insertAdjacentHTML('afterend', runtimeNotice);
  }
  if (!disconnectAvailable) {
    const disconnectBtn = root.querySelector('[data-action="passport-disconnect"]');
    if (disconnectBtn) {
      disconnectBtn.disabled = true;
      disconnectBtn.title = 'Скоро';
      disconnectBtn.textContent = 'Отключить агент · Скоро';
      disconnectBtn.removeAttribute('data-action');
    }
  }
}

async function runDevicePassportAction(action, mode) {
  const id = state.selectedDevice;
  if (!id) return;
  state.devicePanelBusy = action;
  state.devicePanelError = '';
  renderDevicePassport();
  try {
    let endpoint = `${API}/api/devices/${encodeURIComponent(id)}/state`;
    let body = { mode: 'snapshot' };
    if (action === 'activate') {
      endpoint = `${API}/api/devices/${encodeURIComponent(id)}/activate`;
      body = { mode: mode || 'soft' };
    } else if (action === 'disconnect') {
      endpoint = `${API}/api/devices/${encodeURIComponent(id)}/disconnect`;
      body = {};
    } else if (action === 'shutdown') {
      if (!confirm('Выключить агент ИРУ на выбранном устройстве? Компьютер не будет выключен.')) {
        return;
      }
      endpoint = `${API}/api/devices/${encodeURIComponent(id)}/shutdown`;
      body = {};
    } else if (action === 'runtime') {
      endpoint = `${API}/api/devices/${encodeURIComponent(id)}/runtime`;
      body = { mode: mode || 'check', packages: [] };
    }
    const r = await apiFetch(endpoint, {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || 'Команда не выполнена');
    if (data.health_summary && state.devices[id]) {
      Object.assign(state.devices[id], {
        health_status: data.health_summary.health_status,
        identity_status: data.health_summary.identity_status,
        cpu_load: data.health_summary.cpu_load,
        ram_used_pct: data.health_summary.ram_used_pct,
        disk_used_pct: data.health_summary.disk_used_pct,
        process_count: data.health_summary.process_count,
        uptime: data.health_summary.uptime,
        last_snapshot_at: data.last_state_snapshot?.collected_at,
      });
    }
    if (data.summary && state.devices[id]) {
      Object.assign(state.devices[id], {
        runtime_status: data.summary.runtime_status,
        python_runtime_status: data.summary.runtime_status,
        python_version: data.summary.python_version,
        pip_status: data.summary.pip_status,
        last_runtime_check: data.summary.last_runtime_check,
        venv_python: data.summary.venv_python,
      });
    }
    if (action === 'shutdown') {
      showToast('Агент выключается');
      await wait(1500);
      await fetchDevices();
      return;
    }
    await fetchDevices();
    if (action === 'state') {
      showToast('Использован инструмент: device.refresh_state');
    } else if (action === 'activate' && (mode || 'soft') === 'repair') {
      showToast('Использован инструмент: device.repair_activation');
    } else if (action === 'activate') {
      showToast('Использован инструмент: device.activate');
    } else if (action === 'runtime' && (mode || 'check') === 'prepare') {
      showToast('Использован инструмент: device.prepare_runtime');
    } else if (action === 'runtime' && (mode || 'check') === 'repair') {
      showToast('Использован инструмент: device.repair_runtime');
    } else if (action === 'runtime') {
      showToast('Использован инструмент: device.check_runtime');
    } else {
      showToast('Команда отправлена');
    }
  } catch (e) {
    const message = e.message || String(e);
    state.devicePanelError = message.includes('runtime_prepare_interrupted')
      ? 'Подготовка прервана: агент переподключился. Нажмите Проверить runtime.'
      : message;
    renderDevicePassport();
    showToast(state.devicePanelError, true);
  } finally {
    state.devicePanelBusy = null;
    renderDevicePassport();
  }
}

function bindDevicePassportActions() {
  const root = document.getElementById('devicePassport');
  if (!root || root.dataset.delegated === '1') return;
  root.dataset.delegated = '1';
  root.addEventListener('click', (event) => {
    const target = event.target.closest('[data-action]');
    if (!target || !root.contains(target)) return;
    if (target.dataset.action === 'passport-state') runDevicePassportAction('state');
    if (target.dataset.action === 'passport-activate') runDevicePassportAction('activate', target.dataset.mode || 'soft');
    if (target.dataset.action === 'passport-runtime') runDevicePassportAction('runtime', target.dataset.mode || 'check');
    if (target.dataset.action === 'passport-disconnect') runDevicePassportAction('disconnect');
    if (target.dataset.action === 'passport-shutdown') runDevicePassportAction('shutdown');
  });
}
function toggleDeviceDropdown() {
  document.getElementById('deviceDropdown').classList.toggle('show');
  fetchDevices();
}
function closeDeviceDropdown() { document.getElementById('deviceDropdown').classList.remove('show'); }
document.addEventListener('click', e => { if (!e.target.closest('.device-select')) closeDeviceDropdown(); });

// ── CHAT MESSAGES ────────────────────────────────────

function toggleInputDeviceDropdown() {
  document.getElementById('inputDeviceDropdown').classList.toggle('show');
}
function closeInputDeviceDropdown() {
  document.getElementById('inputDeviceDropdown').classList.remove('show');
}
document.addEventListener('click', e => {
  if (!e.target.closest('.input-device-select')) closeInputDeviceDropdown();
  if (!e.target.closest('.input-mode-select')) closeInputModeDropdown();
});

// Кнопка режимов (конвейер / автономный)
function toggleInputModeDropdown() {
  document.getElementById('inputModeDropdown').classList.toggle('show');
}
function closeInputModeDropdown() {
  const el = document.getElementById('inputModeDropdown');
  if (el) el.classList.remove('show');
}
function setMode(name, on) {
  state.modes[name] = !!on;
  renderInputModeBtn();
}
function renderInputModeBtn() {
  const btn = document.getElementById('inputModeBtn');
  const badges = document.getElementById('inputModeBadges');
  if (!btn || !badges) return;
  const active = [];
  if (state.modes.pipeline)   active.push('План');
  if (state.modes.autonomous) active.push('Авто');
  btn.classList.toggle('active', active.length > 0);
  badges.textContent = active.join(' · ');
  // Синхронизируем чекбоксы с state (на случай внешнего изменения)
  const p = document.getElementById('modePipeline');
  const a = document.getElementById('modeAutonomous');
  if (p) p.checked = !!state.modes.pipeline;
  if (a) a.checked = !!state.modes.autonomous;
}

function selectInputDevice(mode, deviceId) {
  if (mode === 'all') {
    state.sendTarget = 'all';
  } else {
    state.sendTarget = 'single';
    state.selectedDevice = deviceId;
  }
  renderInputDeviceSelector();
  renderDevices();
  closeInputDeviceDropdown();
  // Update placeholder
  const input = document.getElementById('chatInput');
  input.placeholder = state.sendTarget === 'all' ? 'Опиши задачу (все устройства)...' : 'Опиши задачу...';
  if (state.explorerOpen && mode !== 'all') explorerNavigate(state.explorerPath);
}

function renderInputDeviceSelector() {
  const ids = Object.keys(state.devices);
  const dot = document.getElementById('inputDeviceDot');
  const label = document.getElementById('inputDeviceLabel');
  const dropdown = document.getElementById('inputDeviceDropdown');

  if (ids.length === 0) {
    dot.className = 'dot';
    dot.style.background = 'var(--text-muted)';
    dot.style.boxShadow = 'none';
    label.textContent = 'Нет устройств';
    dropdown.innerHTML = '<div class="input-device-dropdown-item" style="color:var(--text-muted);cursor:default">Ожидание подключения...</div>';
    // Онбординг-плейсхолдер
    const chatInput = document.getElementById('chatInput');
    if (chatInput) chatInput.placeholder = 'Спроси, как подключить устройство...';
    return;
  }

  // Current selection display
  if (state.sendTarget === 'all') {
    dot.className = 'dot all';
    dot.style.background = '';
    dot.style.boxShadow = '';
    label.textContent = 'Все устройства (' + ids.length + ')';
  } else {
    dot.className = 'dot';
    dot.style.background = '';
    dot.style.boxShadow = '';
    const dev = state.devices[state.selectedDevice];
    label.textContent = dev ? (dev.info?.hostname || state.selectedDevice) : 'Выберите';
  }

  // Dropdown items
  let html = '';
  // "All devices" option
  const allSel = state.sendTarget === 'all' ? ' selected' : '';
  html += `<div class="input-device-dropdown-item${allSel}" data-action="select-input-device" data-mode="all">
    <span class="dot all" style="width:5px;height:5px;border-radius:50%;background:var(--accent);box-shadow:0 0 4px var(--accent)"></span>
    <div>Все устройства (${ids.length})</div>
  </div>`;
  // Individual devices
  for (const id of ids) {
    const d = state.devices[id];
    const sel = (state.sendTarget === 'single' && id === state.selectedDevice) ? ' selected' : '';
    const info = d.info || {};
    html += `<div class="input-device-dropdown-item${sel}" data-action="select-input-device" data-mode="single" data-device-id="${escapeAttr(encodeURIComponent(id))}">
      <span style="width:5px;height:5px;border-radius:50%;background:var(--success);box-shadow:0 0 4px var(--success);flex-shrink:0"></span>
      <div><div>${escapeHTML(info.hostname || id)}</div><div class="dev-os">${escapeHTML(info.os || '?')} — ${escapeHTML(id)}</div></div>
    </div>`;
  }
  dropdown.innerHTML = html;
}

function bindInputDeviceActions() {
  const dropdown = document.getElementById('inputDeviceDropdown');
  if (!dropdown || dropdown.dataset.delegated === '1') return;
  dropdown.dataset.delegated = '1';
  dropdown.addEventListener('click', (event) => {
    const target = event.target.closest('[data-action="select-input-device"]');
    if (!target || !dropdown.contains(target)) return;
    const mode = target.dataset.mode || 'single';
    const deviceId = mode === 'all' ? undefined : decodeURIComponent(target.dataset.deviceId || '');
    selectInputDevice(mode, deviceId);
  });
}

bindDeviceListActions();
bindInputDeviceActions();
bindDevicePassportActions();

// ── LIVE PROGRESS ─────────────────────────────────────

