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
    return `<div class="device-dropdown-item${sel}" onclick="selectDevice('${id}')">
      <span class="device-dot online"></span>
      <div><div>${info.hostname || id}</div><div class="device-os">${info.os || '?'} — ${id}</div></div>
    </div>`;
  }).join('');
  renderInputDeviceSelector();
}

function selectDevice(id) {
  state.selectedDevice = id;
  state.sendTarget = 'single';
  renderDevices();
  closeDeviceDropdown();
  if (state.explorerOpen) explorerNavigate(state.explorerPath);
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
  html += `<div class="input-device-dropdown-item${allSel}" onclick="selectInputDevice('all')">
    <span class="dot all" style="width:5px;height:5px;border-radius:50%;background:var(--accent);box-shadow:0 0 4px var(--accent)"></span>
    <div>Все устройства (${ids.length})</div>
  </div>`;
  // Individual devices
  for (const id of ids) {
    const d = state.devices[id];
    const sel = (state.sendTarget === 'single' && id === state.selectedDevice) ? ' selected' : '';
    const info = d.info || {};
    html += `<div class="input-device-dropdown-item${sel}" onclick="selectInputDevice('single','${id}')">
      <span style="width:5px;height:5px;border-radius:50%;background:var(--success);box-shadow:0 0 4px var(--success);flex-shrink:0"></span>
      <div><div>${info.hostname || id}</div><div class="dev-os">${info.os || '?'} — ${id}</div></div>
    </div>`;
  }
  dropdown.innerHTML = html;
}

// ── LIVE PROGRESS ─────────────────────────────────────

