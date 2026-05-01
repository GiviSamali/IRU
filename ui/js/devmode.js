function toggleDevMode() {
  state.devModeOpen = !state.devModeOpen;
  const panel = document.getElementById('devModePanel');
  panel.classList.toggle('open', state.devModeOpen);
  document.getElementById('devModeToggle').classList.toggle('active', state.devModeOpen);
  if (state.devModeOpen) {
    if (state.explorerOpen) toggleExplorer();
    updateDevModeDevices();
    document.getElementById('devModeInput').focus();
  }
}

function updateDevModeDevices() {
  const sel = document.getElementById('devModeDeviceSelect');
  const ids = Object.keys(state.devices);
  sel.innerHTML = '';
  if (ids.length === 0) {
    sel.innerHTML = '<option value="">Нет устройств</option>';
    return;
  }
  for (const id of ids) {
    const d = state.devices[id];
    const name = d.info?.hostname || id;
    const opt = document.createElement('option');
    opt.value = id;
    opt.textContent = name;
    sel.appendChild(opt);
  }
  if (state.selectedDevice && state.devices[state.selectedDevice]) {
    sel.value = state.selectedDevice;
  }
}

function toggleDevBroadcast() {
  const cb = document.getElementById('devModeBroadcast');
  const sel = document.getElementById('devModeDeviceSelect');
  sel.disabled = cb.checked;
}

async function sendDevCommand() {
  const input = document.getElementById('devModeInput');
  const cmd = input.value.trim();
  if (!cmd) return;

  const output = document.getElementById('devModeOutput');
  const isBroadcast = document.getElementById('devModeBroadcast').checked;
  const deviceId = document.getElementById('devModeDeviceSelect').value;

  if (!isBroadcast && !deviceId) {
    appendDevEntry(cmd, [{ device: '---', text: 'Выберите устройство', status: 'error' }]);
    return;
  }

  input.value = '';
  autoGrow(input);

  // Placeholder entry while loading
  const entryEl = appendDevEntry(cmd, null);

  try {
    const r = await apiFetch(`${API}/api/raw_command`, {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify({ command: cmd, device_id: deviceId, broadcast: isBroadcast }),
    });
    const data = await r.json();

    if (data.status === 'error') {
      fillDevEntry(entryEl, [{ device: '---', text: data.error, status: 'error' }]);
      return;
    }

    const results = data.results || {};
    const items = [];
    for (const [did, res] of Object.entries(results)) {
      const devName = state.devices[did]?.info?.hostname || did;
      let text, status;
      if (res.status === 'ok') {
        const r = res.result || {};
        text = r.stdout || r.stderr || r.error || '(нет вывода)';
        status = (r.returncode === 0 || r.returncode == null) ? 'ok' : 'err';
      } else if (res.status === 'blocked') {
        text = res.error || 'Заблокировано';
        status = 'blocked';
      } else if (res.status === 'confirm_required') {
        text = res.error || 'Требуется подтверждение';
        status = 'confirm';
      } else {
        text = res.error || 'Неизвестная ошибка';
        status = 'error';
      }
      items.push({ device: devName, text: text.trim(), status });
    }
    fillDevEntry(entryEl, items);
  } catch (e) {
    fillDevEntry(entryEl, [{ device: '---', text: 'Ошибка сети: ' + e.message, status: 'error' }]);
  }
}

function appendDevEntry(cmd, items) {
  const output = document.getElementById('devModeOutput');
  const placeholder = output.querySelector('.devmode-placeholder');
  if (placeholder) placeholder.remove();

  const entry = document.createElement('div');
  entry.className = 'devmode-entry';
  entry.innerHTML = '<div class="devmode-cmd">' + escapeHTML('> ' + cmd) + '</div>' +
    '<div class="devmode-devices"></div>';

  if (items) {
    fillDevEntry(entry, items);
  } else {
    entry.querySelector('.devmode-devices').innerHTML =
      '<div class="devmode-loading">Выполняется...</div>';
  }

  output.appendChild(entry);
  output.scrollTop = output.scrollHeight;
  return entry;
}

function fillDevEntry(entryEl, items) {
  const container = entryEl.querySelector('.devmode-devices');
  container.innerHTML = '';
  for (const item of items) {
    const statusIcon = item.status === 'ok' ? '\u2713' :
      item.status === 'blocked' ? '\u26D4' :
      item.status === 'confirm' ? '\u26A0' : '\u2717';
    const statusCls = item.status === 'ok' ? 'status-ok' :
      item.status === 'blocked' ? 'status-blocked' :
      item.status === 'confirm' ? 'status-confirm' : 'status-err';
    const acc = document.createElement('div');
    acc.className = 'devmode-accordion';
    acc.innerHTML =
      '<div class="devmode-accordion-head">' +
        '<span class="devmode-accordion-name">' + escapeHTML(item.device) + '</span>' +
        '<span class="devmode-accordion-status ' + statusCls + '">' + statusIcon + '</span>' +
        '<button class="devmode-accordion-toggle" data-action="toggle-devmode-accordion">' +
          '<svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor"><path d="M2 3.5L5 6.5L8 3.5"/></svg>' +
        '</button>' +
      '</div>' +
      '<div class="devmode-accordion-body">' + escapeHTML(item.text) + '</div>';
    container.appendChild(acc);
  }
  const output = document.getElementById('devModeOutput');
  output.scrollTop = output.scrollHeight;
}

function bindDevModeDelegatedActions() {
  const output = document.getElementById('devModeOutput');
  if (!output || output.dataset.delegated === '1') return;
  output.dataset.delegated = '1';
  output.addEventListener('click', (event) => {
    const target = event.target.closest('[data-action="toggle-devmode-accordion"]');
    if (!target || !output.contains(target)) return;
    event.stopPropagation();
    const accordion = target.closest('.devmode-accordion');
    if (accordion) accordion.classList.toggle('open');
  });
}

bindDevModeDelegatedActions();

function handleDevModeKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendDevCommand();
  }
}

// ── Голосовой ввод (Web Speech API) ──────────────────────────
