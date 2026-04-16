// ── STATE ────────────────────────────────────────────
const state = {
  user: null,        // {id, name, token}
  chats: [],         // [{id, title, created_at, updated_at}]
  currentChatId: null,
  messages: [],      // messages of current chat (from server)
  devices: {},
  selectedDevice: null,
  sendTarget: 'single', // 'single' = selected device, 'all' = all devices
  explorerOpen: false,
  explorerPath: null,
  explorerHistory: [],
  pendingTasks: [],  // [{task_id, msgIndex}] — задачи в процессе
};

const API = window.location.origin;

function authHeaders() {
  return { 'Content-Type': 'application/json', 'X-Token': state.user?.token || '' };
}

// ── AUTH ─────────────────────────────────────────────
async function doAuth() {
  const input = document.getElementById('authInput');
  const token = input.value.trim();
  if (!token) return;

  document.getElementById('authBtn').disabled = true;
  document.getElementById('authError').textContent = '';

  try {
    const r = await fetch(`${API}/api/auth`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    const data = await r.json();
    if (data.status === 'ok') {
      state.user = data.user;
      localStorage.setItem('iru_token', token);
      showApp();
    } else {
      document.getElementById('authError').textContent = data.error || 'Ошибка авторизации';
    }
  } catch (e) {
    document.getElementById('authError').textContent = 'Ошибка сети: ' + e.message;
  }
  document.getElementById('authBtn').disabled = false;
}

function doLogout() {
  localStorage.removeItem('iru_token');
  state.user = null;
  state.chats = [];
  state.currentChatId = null;
  state.messages = [];
  document.getElementById('authScreen').style.display = 'flex';
  document.getElementById('appRoot').classList.remove('active');
}

async function tryAutoLogin() {
  const token = localStorage.getItem('iru_token');
  if (!token) return;
  try {
    const r = await fetch(`${API}/api/auth`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    const data = await r.json();
    if (data.status === 'ok') {
      state.user = data.user;
      showApp();
    }
  } catch {}
}

function showApp() {
  document.getElementById('authScreen').style.display = 'none';
  document.getElementById('appRoot').classList.add('active');
  document.getElementById('userName').textContent = state.user.name;
  loadChats();
  fetchDevices();
  setInterval(fetchDevices, 5000);
  checkConsent();
  // Показать кнопку админки для admin-пользователя
  if (state.user.name === 'admin') {
    document.getElementById('btnAdmin').style.display = 'flex';
  }
}

// ── CHATS ────────────────────────────────────────────
async function loadChats() {
  try {
    const r = await fetch(`${API}/api/chats`, { headers: authHeaders() });
    const data = await r.json();
    state.chats = data.chats || [];
    renderChatList();

    // Если есть чаты и нет активного — открыть первый
    if (state.chats.length > 0 && !state.currentChatId) {
      openChat(state.chats[0].id);
    } else if (state.chats.length === 0) {
      state.currentChatId = null;
      state.messages = [];
      renderMessages();
      document.getElementById('headerTitle').textContent = 'Новый чат';
    }
  } catch (e) { console.error('loadChats:', e); }
}

async function createNewChat() {
  try {
    const r = await fetch(`${API}/api/chats`, {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify({ title: '' }),
    });
    const data = await r.json();
    if (data.status === 'ok') {
      await loadChats();
      openChat(data.chat.id);
    }
  } catch (e) { showToast('Ошибка: ' + e.message, true); }
}

async function openChat(chatId) {
  state.currentChatId = chatId;
  if (window.innerWidth <= 768) closeMobileSidebar();
  renderChatList();

  const chat = state.chats.find(c => c.id === chatId);
  document.getElementById('headerTitle').textContent = chat ? chat.title : 'Чат';

  // Загрузить сообщения
  try {
    const r = await fetch(`${API}/api/chats/${chatId}/messages`, { headers: authHeaders() });
    const data = await r.json();
    state.messages = data.messages || [];
    renderMessages();
  } catch (e) {
    state.messages = [];
    renderMessages();
  }
}

async function deleteChat(chatId, event) {
  event.stopPropagation();
  try {
    await fetch(`${API}/api/chats/${chatId}`, { method: 'DELETE', headers: authHeaders() });
    if (state.currentChatId === chatId) {
      state.currentChatId = null;
      state.messages = [];
      renderMessages();
    }
    await loadChats();
  } catch (e) { showToast('Ошибка: ' + e.message, true); }
}

function renderChatList() {
  const list = document.getElementById('chatList');
  if (state.chats.length === 0) {
    list.innerHTML = '<div class="sidebar-empty">Нет чатов</div>';
    return;
  }
  list.innerHTML = state.chats.map(c => {
    const active = c.id === state.currentChatId ? ' active' : '';
    return `<div class="chat-item${active}" onclick="openChat(${c.id})">
      <svg class="chat-item-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
      <span class="chat-item-text">${escapeHTML(c.title)}</span>
      <button class="chat-item-delete" onclick="deleteChat(${c.id}, event)" title="Удалить">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>`;
  }).join('');
}

// ── DEVICES ──────────────────────────────────────────
async function fetchDevices() {
  if (!state.user) return;
  try {
    const r = await fetch(`${API}/api/devices`, { headers: authHeaders() });
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
function renderMessages() {
  const container = document.getElementById('chatMessages');

  if (state.messages.length === 0) {
    const hasDevices = Object.keys(state.devices).length > 0;
    const subtitle = hasDevices
      ? 'Опиши задачу на естественном языке — ИРУ выполнит на твоём устройстве.'
      : 'Нет подключённых устройств. Напиши сообщение — я помогу настроить подключение.';
    const hints = hasDevices
      ? `<div class="hint-chip" onclick="sendHint(this)">Открой браузер</div>
          <div class="hint-chip" onclick="sendHint(this)">Покажи IP адрес</div>
          <div class="hint-chip" onclick="sendHint(this)">Свободное место на диске</div>
          <div class="hint-chip" onclick="sendHint(this)">Запущенные процессы</div>`
      : `<div class="hint-chip" onclick="downloadAgent()">⬇ Скачать агент</div>
          <div class="hint-chip" onclick="sendHint(this)">Как подключить компьютер?</div>
          <div class="hint-chip" onclick="sendHint(this)">Что ты умеешь?</div>`;
    container.innerHTML = `
      <div class="chat-welcome">
        <img src="/static/IruIcon.ico" alt="ИРУ">
        <h2>ИРУ — Интеллектуальный Режим Управления</h2>
        <p>${subtitle}</p>
        <div class="hints">${hints}</div>
      </div>`;
    return;
  }

  let html = '';
  for (let mi = 0; mi < state.messages.length; mi++) {
    const m = state.messages[mi];
    const roleLabel = m.role === 'user' ? 'вы' : 'иру';
    let bodyHTML = linkify(escapeHTML(m.content || m.text || ''));

    const commands = m.commands;
    if (commands && commands.length > 0) {
      bodyHTML += '<div class="cmd-log">';
      for (let i = 0; i < commands.length; i++) {
        const c = commands[i];
        const stdout = c.result?.stdout || '';
        const stderr = c.result?.stderr || '';
        const url = c.result?.url || '';
        const errMsg = c.result?.error || '';
        const output = stdout || stderr || errMsg || '(нет вывода)';
        const isOk = !errMsg && (c.result?.returncode === 0 || c.result?.returncode == null);
        const statusCls = isOk ? 'ok' : 'err';
        const statusTxt = isOk ? '\u2713' : '\u2717';
        const deviceTag = c.device_id ? `<span class="cmd-device">${escapeHTML(c.device_id)}</span>` : '';
        const cmdText = escapeHTML(c.command || '');
        bodyHTML += `
          <div class="cmd-entry" onclick="this.classList.toggle('open')">
            <div class="cmd-summary">
              <span class="cmd-icon">\u25b8</span>
              <span class="cmd-text">${cmdText}</span>
              ${deviceTag}
              <span class="cmd-status ${statusCls}">${statusTxt}</span>
            </div>
            <div class="cmd-details">${escapeHTML(output)}</div>
          </div>`;
      }
      bodyHTML += '</div>';
    }
    // Кнопки подтверждения
    let confirmBtns = '';
    if (m.confirmTaskId) {
      confirmBtns = `<div class="confirm-actions">
        <button class="btn-confirm-yes" onclick="confirmTask('${m.confirmTaskId}', ${mi})">\u2713 Выполнить</button>
        <button class="btn-confirm-no" onclick="denyTask('${m.confirmTaskId}', ${mi})">✗ Отменить</button>
      </div>`;
    }

    if (m.loading) {
      html += `<div class="msg assistant"><div class="msg-role">иру</div><div class="msg-body"><div class="typing"><span></span><span></span><span></span></div></div></div>`;
    } else {
      html += `<div class="msg ${m.role}"><div class="msg-role">${roleLabel}</div><div class="msg-body">${bodyHTML}${confirmBtns}</div></div>`;
    }
  }

  container.innerHTML = html;
  container.scrollTop = container.scrollHeight;
}

const MAX_INPUT_LENGTH = 500;

async function sendMessage() {
  const input = document.getElementById('chatInput');
  const text = input.value.trim();
  if (!text) return;
  if (text.length > MAX_INPUT_LENGTH) {
    showToast(`Максимум ${MAX_INPUT_LENGTH} символов`, true);
    return;
  }
  const ids = Object.keys(state.devices);
  const isOnboarding = ids.length === 0;

  input.value = '';
  autoGrow(input);

  // Добавить сообщение пользователя в UI сразу
  state.messages.push({ role: 'user', content: text });
  // Добавить placeholder для ответа (с индикатором загрузки)
  const msgIndex = state.messages.length;
  state.messages.push({ role: 'assistant', content: '', loading: true });
  renderMessages();

  try {
    const isBroadcast = !isOnboarding && state.sendTarget === 'all';
    const body = {
      device_id: isOnboarding ? '' : (state.selectedDevice || ids[0]),
      message: text,
      chat_id: state.currentChatId,
      broadcast: isBroadcast,
    };
    const r = await fetch(`${API}/nl_command`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(body),
    });
    const data = await r.json();

    if (data.chat_id && data.chat_id !== state.currentChatId) {
      state.currentChatId = data.chat_id;
      loadChats();
    }

    if (data.status === 'ok' && data.task_id) {
      // Задача запущена в фоне — начинаем polling
      state.pendingTasks.push({ task_id: data.task_id, msgIndex });
      pollTask(data.task_id, msgIndex);
    } else {
      // Ошибка до запуска задачи
      state.messages[msgIndex] = {
        role: 'assistant',
        content: `Ошибка: ${data.error || 'Неизвестная ошибка'}`,
      };
      renderMessages();
    }
  } catch (e) {
    state.messages[msgIndex] = {
      role: 'assistant',
      content: `Ошибка сети: ${e.message}`,
    };
    renderMessages();
  }
}

async function pollTask(taskId, msgIndex) {
  const startTime = Date.now();
  const MAX_POLL_MS = 120000; // 2 минуты макс
  let stopped = false;
  const poll = async () => {
    if (stopped) return;
    if (Date.now() - startTime > MAX_POLL_MS) {
      state.messages[msgIndex] = { role: 'assistant', content: 'Истекло время ожидания ответа.' };
      state.pendingTasks = state.pendingTasks.filter(t => t.task_id !== taskId);
      renderMessages();
      return;
    }
    try {
      const r = await fetch(`${API}/api/tasks/${taskId}`, { headers: authHeaders() });
      if (!r.ok) {
        stopped = true;
        state.messages[msgIndex] = { role: 'assistant', content: 'Задача не найдена.' };
        state.pendingTasks = state.pendingTasks.filter(t => t.task_id !== taskId);
        renderMessages();
        return;
      }
      const data = await r.json();
      const task = data.task;

      if (task.status === 'confirm') {
        stopped = true;
        const cd = task.confirm_data || {};
        const cmdText = cd.command || '';
        state.messages[msgIndex] = {
          role: 'assistant',
          content: `Команда требует подтверждения:\n${cmdText}`,
          commands: task.commands,
          confirmTaskId: taskId,
        };
        renderMessages();
        return;
      }
      if (task.status === 'done' || task.status === 'error') {
        stopped = true;
        state.messages[msgIndex] = {
          role: 'assistant',
          content: task.answer || 'Готово.',
          commands: task.commands,
        };
        state.pendingTasks = state.pendingTasks.filter(t => t.task_id !== taskId);
        renderMessages();
        loadChats();
        return;
      }
      // Ещё выполняется — повторить через 1с
      if (!stopped) setTimeout(poll, 1000);
    } catch (e) {
      if (stopped) return;
      if (!poll._retries) poll._retries = 0;
      poll._retries++;
      if (poll._retries > 30) {
        stopped = true;
        state.messages[msgIndex] = { role: 'assistant', content: 'Задача не найдена или истекла.' };
        state.pendingTasks = state.pendingTasks.filter(t => t.task_id !== taskId);
        renderMessages();
        return;
      }
      setTimeout(poll, 2000);
    }
  };
  setTimeout(poll, 800);
}

// ── INPUT DEVICE SELECTOR ──────────────────────────────
function toggleInputDeviceDropdown() {
  document.getElementById('inputDeviceDropdown').classList.toggle('show');
}
function closeInputDeviceDropdown() {
  document.getElementById('inputDeviceDropdown').classList.remove('show');
}
document.addEventListener('click', e => {
  if (!e.target.closest('.input-device-select')) closeInputDeviceDropdown();
});

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

function sendHint(el) {
  document.getElementById('chatInput').value = el.textContent;
  sendMessage();
}

function downloadAgent() {
  const token = state.user?.token || '';
  if (!token) { showToast('Сначала войдите в систему', true); return; }
  const a = document.createElement('a');
  a.href = `${API}/api/download_agent?token=${encodeURIComponent(token)}`;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
}
// ── CONFIRM / DENY ───────────────────────────────────────────
async function confirmTask(taskId, msgIndex) {
  try {
    await fetch(`${API}/api/tasks/${taskId}/confirm`, {
      method: 'POST', headers: authHeaders(),
    });
    // Убираем кнопки, показываем лоадер
    state.messages[msgIndex].confirmTaskId = null;
    state.messages[msgIndex].loading = true;
    state.messages[msgIndex].content = '';
    renderMessages();
    // Поллим задачу до завершения
    pollTask(taskId, msgIndex);
  } catch (e) { showToast('Ошибка подтверждения', true); }
}

async function denyTask(taskId, msgIndex) {
  try {
    await fetch(`${API}/api/tasks/${taskId}/deny`, {
      method: 'POST', headers: authHeaders(),
    });
    state.messages[msgIndex].confirmTaskId = null;
    state.messages[msgIndex].content = 'Команда отменена.';
    state.pendingTasks = state.pendingTasks.filter(t => t.task_id !== taskId);
    renderMessages();
  } catch (e) { showToast('Ошибка', true); }
}

function handleInputKey(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } }
function autoGrow(el) { el.style.height = '18px'; el.style.height = Math.min(el.scrollHeight, 100) + 'px'; }
function updateCharCount() {
  const input = document.getElementById('chatInput');
  const counter = document.getElementById('charCount');
  if (!counter) return;
  const len = input.value.length;
  counter.textContent = `${len}/${MAX_INPUT_LENGTH}`;
  counter.classList.toggle('over', len > MAX_INPUT_LENGTH);
}

// ── EXPLORER ─────────────────────────────────────────
function toggleExplorer() {
  state.explorerOpen = !state.explorerOpen;
  document.getElementById('explorerPanel').classList.toggle('open', state.explorerOpen);
  document.getElementById('explorerToggle').classList.toggle('active', state.explorerOpen);
  if (state.explorerOpen && state.selectedDevice) explorerNavigate(state.explorerPath);
}

async function explorerNavigate(path) {
  if (!state.selectedDevice) {
    document.getElementById('explorerList').innerHTML = '<div class="explorer-empty">Нет устройства</div>';
    return;
  }
  try {
    const params = path ? { path } : {};
    const r = await fetch(`${API}/command`, {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify({ device_id: state.selectedDevice, action: 'list_dir', params }),
    });
    const data = await r.json();
    if (data.status === 'error') {
      document.getElementById('explorerList').innerHTML = `<div class="explorer-empty">${escapeHTML(data.error)}</div>`;
      return;
    }
    const result = data.result;
    if (result.error) {
      document.getElementById('explorerList').innerHTML = `<div class="explorer-empty">${escapeHTML(result.error)}</div>`;
      return;
    }
    if (state.explorerPath && state.explorerPath !== result.path) {
      state.explorerHistory.push(state.explorerPath);
    }
    state.explorerPath = result.path;
    renderExplorerPath(result.path);
    renderExplorerList(result.dirs, result.files);
  } catch (e) {
    document.getElementById('explorerList').innerHTML = `<div class="explorer-empty">Ошибка: ${e.message}</div>`;
  }
}

function renderExplorerPath(pathStr) {
  const container = document.getElementById('explorerPath');
  const sep = pathStr.includes('\\') ? '\\' : '/';
  const parts = pathStr.split(sep).filter(Boolean);
  let html = '', accumulated = pathStr.startsWith('/') ? '/' : '';
  for (let i = 0; i < parts.length; i++) {
    accumulated += parts[i] + sep;
    const target = accumulated;
    html += `<span class="path-segment" onclick="explorerNavigate('${escapeAttr(target)}')">${escapeHTML(parts[i])}</span>`;
    if (i < parts.length - 1) html += '<span class="path-sep">\u203a</span>';
  }
  container.innerHTML = html;
}

function renderExplorerList(dirs, files) {
  const container = document.getElementById('explorerList');
  if ((!dirs || !dirs.length) && (!files || !files.length)) {
    container.innerHTML = '<div class="explorer-empty">Пустая директория</div>';
    return;
  }
  let html = '';
  for (const d of (dirs || [])) {
    html += `<div class="explorer-item dir" onclick="explorerNavigate('${escapeAttr(d.path)}')">
      <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d="M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>
      <span class="name">${escapeHTML(d.name)}</span>
      <div class="file-actions">
        <div class="file-action-btn" onclick="event.stopPropagation(); openOnDevice('${escapeAttr(d.path)}')" title="Открыть на ПК">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
        </div>
      </div>
    </div>`;
  }
  for (const f of (files || [])) {
    const size = formatSize(f.size);
    html += `<div class="explorer-item file">
      <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>
      <span class="name">${escapeHTML(f.name)}</span>
      <span class="size">${size}</span>
      <div class="file-actions">
        <div class="file-action-btn" onclick="event.stopPropagation(); openOnDevice('${escapeAttr(f.path)}')" title="Открыть на ПК">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
        </div>
        <div class="file-action-btn" onclick="event.stopPropagation(); downloadFile('${escapeAttr(f.path)}')" title="Скачать">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        </div>
      </div>
    </div>`;
  }
  container.innerHTML = html;
}

function explorerBack() {
  if (state.explorerHistory.length > 0) {
    const prev = state.explorerHistory.pop();
    state.explorerPath = null;
    explorerNavigate(prev);
  }
}
function explorerUp() {
  if (!state.explorerPath) return;
  const sep = state.explorerPath.includes('\\') ? '\\' : '/';
  const parts = state.explorerPath.split(sep).filter(Boolean);
  if (parts.length <= 1) return;
  parts.pop();
  const parent = (state.explorerPath.startsWith('/') ? '/' : '') + parts.join(sep) + sep;
  explorerNavigate(parent);
}
function explorerRefresh() { explorerNavigate(state.explorerPath); }

// ── FILE ACTIONS ─────────────────────────────────────
async function openOnDevice(filePath) {
  if (!state.selectedDevice) return;
  try {
    await fetch(`${API}/command`, {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify({
        device_id: state.selectedDevice,
        action: 'execute_cmd',
        params: { command: `Start-Process "${filePath}"`, timeout: 10 }
      }),
    });
    showToast('Открываю...');
  } catch (e) { showToast('Ошибка: ' + e.message, true); }
}

async function downloadFile(filePath) {
  if (!state.selectedDevice) return;
  showToast('Подготовка к скачиванию...');
  try {
    const r = await fetch(`${API}/api/download_request`, {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify({ device_id: state.selectedDevice, file_path: filePath }),
    });
    const data = await r.json();
    if (data.status === 'ok' && data.url) {
      const a = document.createElement('a');
      a.href = data.url;
      a.download = '';
      document.body.appendChild(a);
      a.click();
      a.remove();
    } else {
      showToast(data.error || 'Ошибка', true);
    }
  } catch (e) { showToast('Ошибка: ' + e.message, true); }
}

// ── UTILS ────────────────────────────────────────────
function escapeHTML(s) { return s ? s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') : ''; }
function escapeAttr(s) { return s.replace(/\\/g, '\\\\').replace(/'/g, "\\'"); }
function formatSize(b) {
  if (b == null) return '';
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b/1024).toFixed(1) + ' KB';
  if (b < 1073741824) return (b/1048576).toFixed(1) + ' MB';
  return (b/1073741824).toFixed(1) + ' GB';
}
function showToast(msg, isError) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show' + (isError ? ' error' : '');
  setTimeout(() => { t.className = 'toast'; }, 3000);
}
function linkify(text) {
  return text.replace(/(\/api\/download\/[a-f0-9-]+)/g, '<a href="$1" target="_blank">\ud83d\udce5 Скачать файл</a>');
}

// ── MOBILE SIDEBAR ─────────────────────────────────
function toggleMobileSidebar() {
  const sidebar = document.querySelector('.sidebar');
  const overlay = document.getElementById('sidebarOverlay');
  sidebar.classList.toggle('open');
  overlay.classList.toggle('show');
}
function closeMobileSidebar() {
  document.querySelector('.sidebar').classList.remove('open');
  document.getElementById('sidebarOverlay').classList.remove('show');
}

// ── CONSENT ────────────────────────────────────────────
function checkConsent() {
  if (state.user && !state.user.data_consent) {
    document.getElementById('consentModal').classList.add('show');
  }
}

async function setConsent(value) {
  try {
    await fetch(`${API}/api/consent`, {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify({ consent: value }),
    });
    state.user.data_consent = value;
  } catch (e) { console.error('consent error:', e); }
  document.getElementById('consentModal').classList.remove('show');
}

// ── ADMIN PANEL ─────────────────────────────────────────
function toggleAdmin() {
  const panel = document.getElementById('adminPanel');
  const isOpen = panel.classList.toggle('open');
  document.getElementById('btnAdmin').classList.toggle('active', isOpen);
  // Close explorer if open
  if (isOpen && state.explorerOpen) toggleExplorer();
  if (isOpen) loadAdminUsers();
}

async function loadAdminUsers() {
  try {
    const r = await fetch(`${API}/api/admin/users`, { headers: authHeaders() });
    const data = await r.json();
    if (data.status !== 'ok') return;
    renderAdminUsers(data.users);
  } catch (e) { console.error('loadAdminUsers:', e); }
}

function renderAdminUsers(users) {
  const list = document.getElementById('adminList');
  if (!users || users.length === 0) {
    list.innerHTML = '<div class="admin-empty">Нет пользователей</div>';
    return;
  }
  list.innerHTML = users.map(u => {
    const isAdmin = u.name === 'admin';
    const deleteBtn = isAdmin ? '' : `
      <button class="admin-user-delete" onclick="adminDeleteUser(${u.id}, '${escapeAttr(u.name)}')" title="Удалить">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>`;
    const badge = isAdmin ? '<span class="admin-badge">admin</span>' : '';
    return `<div class="admin-user-item">
      <div class="admin-user-info">
        <div class="admin-user-name">${escapeHTML(u.name)}${badge}</div>
        <div class="admin-user-token" title="Токен скрыт">${u.token}</div>
      </div>
      ${deleteBtn}
    </div>`;
  }).join('');
  document.getElementById('adminStats').textContent = `Всего пользователей: ${users.length}`;
}

async function adminCreateUser() {
  const input = document.getElementById('adminNewName');
  const name = input.value.trim();
  if (!name) return;
  try {
    const r = await fetch(`${API}/api/admin/users`, {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify({ name }),
    });
    const data = await r.json();
    if (data.status === 'ok') {
      input.value = '';
      const tok = data.user.token || '';
      showToast(`Создан: ${data.user.name}`);
      if (tok) {
        prompt('Токен для ' + data.user.name + ' (скопируйте, показывается один раз):', tok);
      }
      loadAdminUsers();
    } else {
      showToast(data.detail || 'Ошибка', true);
    }
  } catch (e) { showToast('Ошибка: ' + e.message, true); }
}

async function adminDeleteUser(userId, userName) {
  if (!confirm(`Удалить пользователя "${userName}"? Все его чаты и данные будут удалены.`)) return;
  try {
    const r = await fetch(`${API}/api/admin/users/${userId}`, {
      method: 'DELETE', headers: authHeaders(),
    });
    const data = await r.json();
    if (data.status === 'ok') {
      showToast(`Удалён: ${userName}`);
      loadAdminUsers();
    }
  } catch (e) { showToast('Ошибка: ' + e.message, true); }
}

function copyToken(token) {
  navigator.clipboard.writeText(token).then(() => {
    showToast('Токен скопирован');
  }).catch(() => {
    // Fallback
    const ta = document.createElement('textarea');
    ta.value = token; document.body.appendChild(ta);
    ta.select(); document.execCommand('copy'); ta.remove();
    showToast('Токен скопирован');
  });
}

// ── INIT ───────────────────────────────────────────────────
// Mobile keyboard fix — resize app when virtual keyboard opens
if (window.visualViewport) {
  const resizeApp = () => {
    const app = document.querySelector('.app');
    if (!app) return;
    const vh = window.visualViewport.height;
    app.style.height = vh + 'px';
    // Прокрутить страницу вверх, чтобы клавиатура не сдвигала viewport
    window.scrollTo(0, 0);
    document.documentElement.scrollTop = 0;
  };
  window.visualViewport.addEventListener('resize', resizeApp);
  window.visualViewport.addEventListener('scroll', resizeApp);
  // Также при фокусе на input — прокрутить к нему
  document.addEventListener('focusin', (e) => {
    if (e.target.matches('.chat-input')) {
      setTimeout(() => {
        e.target.scrollIntoView({ block: 'end', behavior: 'smooth' });
        window.scrollTo(0, 0);
      }, 300);
    }
  });
}

tryAutoLogin();
