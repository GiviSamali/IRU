// ── STATE ────────────────────────────────────────────
const state = {
  user: null,        // {id, name, token}
  chats: [],         // [{id, title, created_at, updated_at}]
  currentChatId: null,
  messages: [],      // messages of current chat (from server)
  devices: {},
  selectedDevice: null,
  sendTarget: 'single', // 'single' = selected device, 'all' = all devices
  modes: { pipeline: false, autonomous: false }, // флаги режимов для следующего запроса
  explorerOpen: false,
  explorerPath: null,
  explorerHistory: [],
  pendingTasks: [],  // [{task_id, msgIndex}]
  devModeOpen: false,
  userPlan: 'free',
};

const API = window.location.origin;

// ── JWT TOKEN MANAGEMENT ────────────────────────────────

let _accessToken = localStorage.getItem('iru_access_token') || '';
let _refreshToken = localStorage.getItem('iru_refresh_token') || '';
let _refreshTimer = null;

function _saveTokens(access, refresh) {
  _accessToken = access || '';
  _refreshToken = refresh || '';
  if (access) localStorage.setItem('iru_access_token', access);
  else localStorage.removeItem('iru_access_token');
  if (refresh) localStorage.setItem('iru_refresh_token', refresh);
  else localStorage.removeItem('iru_refresh_token');
  _scheduleRefresh();
}

function _clearTokens() {
  _accessToken = '';
  _refreshToken = '';
  localStorage.removeItem('iru_access_token');
  localStorage.removeItem('iru_refresh_token');
  localStorage.removeItem('iru_token');
  localStorage.removeItem('iru_data_consent');
  if (_refreshTimer) { clearTimeout(_refreshTimer); _refreshTimer = null; }
}

function _scheduleRefresh() {
  if (_refreshTimer) clearTimeout(_refreshTimer);
  if (!_accessToken) return;
  // Обновляем за 5 минут до истечения (access = 8ч, обновляем каждые 7ч4мин55с)
  try {
    const payload = JSON.parse(atob(_accessToken.split('.')[1]));
    const expiresIn = (payload.exp * 1000) - Date.now() - 300000; // 5 мин запас
    if (expiresIn > 0) {
      _refreshTimer = setTimeout(_doRefresh, expiresIn);
    } else {
      _doRefresh();
    }
  } catch { /* невалидный токен */ }
}

async function _doRefresh() {
  if (!_refreshToken) return false;
  try {
    const r = await fetch(`${API}/api/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: _refreshToken }),
    });
    const data = await r.json();
    if (data.status === 'ok' && data.access_token) {
      _accessToken = data.access_token;
      localStorage.setItem('iru_access_token', data.access_token);
      _scheduleRefresh();
      return true;
    }
  } catch {}
  // Refresh не удался — вылогинить
  doLogout();
  return false;
}

function authHeaders() {
  const h = { 'Content-Type': 'application/json' };
  if (_accessToken) {
    h['Authorization'] = 'Bearer ' + _accessToken;
  } else if (state.user?.token) {
    h['X-Token'] = state.user.token;  // fallback для обратной совместимости
  }
  return h;
}

// Обёртка fetch с автообновлением токена
async function apiFetch(url, opts = {}) {
  if (!opts.headers) opts.headers = authHeaders();
  let r = await fetch(url, opts);
  if (r.status === 401 && _refreshToken) {
    const ok = await _doRefresh();
    if (ok) {
      opts.headers = authHeaders();
      r = await fetch(url, opts);
    }
  }
  return r;
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
        if (data.user && data.user.data_consent) localStorage.setItem('iru_data_consent', '1');
      localStorage.setItem('iru_token', token);
      _saveTokens(data.access_token, data.refresh_token);
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
  // Отзыв refresh token на сервере
  if (_refreshToken) {
    fetch(`${API}/api/logout`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ refresh_token: _refreshToken }),
    }).catch(() => {});
  }
  _clearTokens();
  state.user = null;
  state.chats = [];
  state.currentChatId = null;
  state.messages = [];
  document.getElementById('authScreen').style.display = 'flex';
  document.getElementById('appRoot').classList.remove('active');
}

async function tryAutoLogin() {
  // Сначала пробуем JWT refresh
  if (_refreshToken) {
    const ok = await _doRefresh();
    if (ok && _accessToken) {
      // Получим user info из токена
      try {
        const payload = JSON.parse(atob(_accessToken.split('.')[1]));
        state.user = { id: parseInt(payload.sub), name: payload.name, token: localStorage.getItem('iru_token') || '', data_consent: localStorage.getItem('iru_data_consent') === '1' };
        showApp();
        return;
      } catch {}
    }
  }
  // Fallback: старый токен
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
        if (data.user && data.user.data_consent) localStorage.setItem('iru_data_consent', '1');
      _saveTokens(data.access_token, data.refresh_token);
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
  checkTermsStatus();
  fetchUserInfo();
  // Показать кнопку админки для admin-пользователя
  if (state.user.name === 'admin') {
    document.getElementById('btnAdmin').style.display = 'flex';
  }
}

// ── CHATS ────────────────────────────────────────────
async function loadChats() {
  try {
    const r = await apiFetch(`${API}/api/chats`, { headers: authHeaders() });
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
    const r = await apiFetch(`${API}/api/chats`, {
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
    const r = await apiFetch(`${API}/api/chats/${chatId}/messages`, { headers: authHeaders() });
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
    await apiFetch(`${API}/api/chats/${chatId}`, { method: 'DELETE', headers: authHeaders() });
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

    // Блок задач (конвейер)
    bodyHTML += renderTaskBlock(m.tasks);

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
      const stepText = escapeHTML(m.currentStep || 'ИРУ думает...');
      const liveTasksHTML = renderTaskBlock(m.liveTasks);
      const taskBlockAttr = (m.liveTasks && m.liveTasks.length > 0) ? '' : ' hidden';
      html += `<div class="msg assistant msg-thinking"><div class="msg-role">иру</div><div class="msg-body"><div class="live-status"><span class="live-dot"></span><span class="live-text">${stepText}</span></div><div class="task-block-live"${taskBlockAttr}>${liveTasksHTML}</div></div></div>`;
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
  // Добавить placeholder для ответа (live-статус вместо точек загрузки)
  const msgIndex = state.messages.length;
  state.messages.push({ role: 'assistant', content: '', loading: true, currentStep: 'ИРУ думает...', liveTasks: [] });
  renderMessages();

  try {
    const isBroadcast = !isOnboarding && state.sendTarget === 'all';
    const body = {
      device_id: isOnboarding ? '' : (state.selectedDevice || ids[0]),
      message: text,
      chat_id: state.currentChatId,
      broadcast: isBroadcast,
      modes: { ...state.modes },
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
  const MAX_POLL_MS = 600000; // 10 минут макс (для длинных конвейеров)
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
      const r = await apiFetch(`${API}/api/tasks/${taskId}`, { headers: authHeaders() });
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
          tasks: task.tasks || [],
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
          tasks: task.tasks || [],
        };
        state.pendingTasks = state.pendingTasks.filter(t => t.task_id !== taskId);
        renderMessages();
        loadChats();
        return;
      }
      // Ещё выполняется — обновить live-статус
      const msg = state.messages[msgIndex];
      if (msg && msg.loading) {
        let needRender = false;
        if (task.current_step && msg.currentStep !== task.current_step) {
          msg.currentStep = task.current_step;
          needRender = true;
        }
        if (task.tasks && task.tasks.length > 0) {
          msg.liveTasks = task.tasks;
          needRender = true;
        }
        if (needRender) renderMessages();
      }
      // Повторить через 800мс пока задача running
      if (!stopped) setTimeout(poll, 800);
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
  if (state.modes.pipeline)   active.push('Конвейер');
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
function renderTaskBlock(tasks) {
  if (!tasks || tasks.length === 0) return '';
  let html = '';
  for (const t of tasks) {
    const st = t.status || 'running';
    const statusLabel = st === 'completed' ? 'завершено'
      : st === 'failed' ? 'ошибка'
      : st === 'cancelled' ? 'отменено'
      : 'выполняется';
    html += `<div class="task-block task-${st}">`;
    html += `<div class="task-goal"><span class="task-goal-label">Задача:</span> ${escapeHTML(t.goal || '')} <span class="task-badge task-badge-${st}">${statusLabel}</span></div>`;
    const steps = t.steps || [];
    if (steps.length > 0) {
      html += '<ul class="task-steps">';
      for (const s of steps) {
        const sst = s.status || 'pending';
        const icon = sst === 'done' ? '\u2713'
          : sst === 'failed' ? '\u2717'
          : sst === 'running' ? '\u25b8'
          : sst === 'skipped' ? '\u2014'
          : '\u25cb';
        const summary = s.summary ? `<div class="step-summary">${escapeHTML(s.summary)}</div>` : '';
        html += `<li class="task-step step-${sst}"><span class="step-icon">${icon}</span><span class="step-desc">${escapeHTML(s.description || '')}</span>${summary}</li>`;
      }
      html += '</ul>';
    }
    html += '</div>';
  }
  return html;
}

function sendHint(el) {
  document.getElementById('chatInput').value = el.textContent;
  sendMessage();
}

function downloadAgent() {
  // НЕ задаём a.download — браузер возьмёт имя из Content-Disposition сервера.
  // Это работает и для agent.zip, и для agent.exe (обратная совместимость).
  const a = document.createElement('a');
  a.href = `${API}/api/agent/download`;
  document.body.appendChild(a);
  a.click();
  a.remove();
}
// ── CONFIRM / DENY ───────────────────────────────────────────
async function confirmTask(taskId, msgIndex) {
  try {
    await apiFetch(`${API}/api/tasks/${taskId}/confirm`, {
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
    await apiFetch(`${API}/api/tasks/${taskId}/deny`, {
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
  if (state.explorerOpen) {
    if (state.devModeOpen) toggleDevMode();
    if (state.selectedDevice) explorerNavigate(state.explorerPath);
  }
}

async function explorerNavigate(path) {
  if (!state.selectedDevice) {
    document.getElementById('explorerList').innerHTML = '<div class="explorer-empty">Нет устройства</div>';
    return;
  }
  try {
    const params = path ? { path } : {};
    const r = await apiFetch(`${API}/command`, {
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
    await apiFetch(`${API}/command`, {
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
    const r = await apiFetch(`${API}/api/download_request`, {
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
    await apiFetch(`${API}/api/consent`, {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify({ consent: value }),
    });
    state.user.data_consent = value;
    localStorage.setItem('iru_data_consent', value ? '1' : '0');
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
    const r = await apiFetch(`${API}/api/admin/users`, { headers: authHeaders() });
    const data = await r.json();
    if (data.status !== 'ok') return;
    renderAdminUsers(data.users);
  } catch (e) { console.error('loadAdminUsers:', e); }
}

let _allAdminUsers = [];

function renderAdminUsers(users) {
  _allAdminUsers = users || [];
  filterAdminUsers();
}

function filterAdminUsers() {
  const query = (document.getElementById('adminSearch') || {}).value || '';
  const q = query.toLowerCase().trim();
  const filtered = q ? _allAdminUsers.filter(u => u.name.toLowerCase().includes(q) || (u.token || '').toLowerCase().includes(q)) : _allAdminUsers;

  const list = document.getElementById('adminList');
  if (!filtered.length) {
    list.innerHTML = '<div class="admin-empty">' + (q ? 'Ничего не найдено' : 'Нет пользователей') + '</div>';
    document.getElementById('adminStats').textContent = '';
    return;
  }

  // Группировка по планам
  const groups = { pro: [], business: [], free: [] };
  filtered.forEach(u => {
    const plan = u.plan || 'free';
    if (!groups[plan]) groups[plan] = [];
    groups[plan].push(u);
  });

  const planLabels = { pro: 'Pro', business: 'Business', free: 'Free' };
  let html = '';

  for (const plan of ['pro', 'business', 'free']) {
    const arr = groups[plan];
    if (!arr || !arr.length) continue;
    html += '<div class="admin-group-header plan-' + plan + '">' + planLabels[plan] + ' <span class="admin-group-count">' + arr.length + '</span></div>';
    html += arr.map(u => renderAdminUserItem(u)).join('');
  }

  list.innerHTML = html;
  document.getElementById('adminStats').textContent = 'Всего: ' + _allAdminUsers.length + (q ? ' (показано: ' + filtered.length + ')' : '');
}

function renderAdminUserItem(u) {
  const isAdmin = u.id === 1;
  const deleteBtn = isAdmin ? '' : `
    <button class="admin-user-delete" onclick="adminDeleteUser(${u.id}, '${escapeAttr(u.name)}')" title="Удалить">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
    </button>`;
  const badge = isAdmin ? '<span class="admin-badge">admin</span>' : '';
  const plan = u.plan || 'free';
  const planClass = 'plan-' + plan;
  const planSelect = isAdmin ? '' : `
    <select class="admin-plan-select ${planClass}" onchange="adminSetPlan(${u.id}, this.value)">
      <option value="free"${plan === 'free' ? ' selected' : ''}>free</option>
      <option value="pro"${plan === 'pro' ? ' selected' : ''}>pro</option>
      <option value="business"${plan === 'business' ? ' selected' : ''}>business</option>
    </select>`;
  return `<div class="admin-user-item">
    <div class="admin-user-info">
      <div class="admin-user-name">${escapeHTML(u.name)}${badge}</div>
      <div class="admin-user-meta">
        <span class="admin-user-token" title="Нажмите чтобы скопировать" onclick="navigator.clipboard.writeText('${escapeAttr(u.token)}');showToast('Токен скопирован')">${u.token}</span>
        ${planSelect}
      </div>
    </div>
    ${deleteBtn}
  </div>`;
}

async function adminSetPlan(userId, plan) {
  try {
    const r = await apiFetch(`${API}/api/admin/users/${userId}/plan`, {
      method: 'PATCH', headers: authHeaders(),
      body: JSON.stringify({ plan }),
    });
    const data = await r.json();
    if (data.status === 'ok') {
      showToast(`План изменён: ${plan}`);
      loadAdminUsers();
    } else {
      showToast(data.error || 'Ошибка', true);
      loadAdminUsers();
    }
  } catch (e) { showToast('Ошибка: ' + e.message, true); }
}

async function adminCreateUser() {
  const input = document.getElementById('adminNewName');
  const name = input.value.trim();
  if (!name) return;
  try {
    const r = await apiFetch(`${API}/api/admin/users`, {
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
    const r = await apiFetch(`${API}/api/admin/users/${userId}`, {
      method: 'DELETE', headers: authHeaders(),
    });
    const data = await r.json();
    if (data.status === 'ok') {
      showToast(`Удалён: ${userName}`);
      loadAdminUsers();
    }
  } catch (e) { showToast('Ошибка: ' + e.message, true); }
}

// ── AUDIT LOG (ADMIN) ─────────────────────────────────
async function loadAuditLog(offset = 0) {
  try {
    const r = await apiFetch(`${API}/api/admin/audit?limit=50&offset=${offset}`, { headers: authHeaders() });
    const data = await r.json();
    if (data.status !== 'ok') return;
    renderAuditLog(data.logs, data.total, offset);
  } catch (e) { console.error('loadAuditLog:', e); }
}

function renderAuditLog(logs, total, offset) {
  const container = document.getElementById('auditLogList');
  if (!container) return;
  if (!logs || logs.length === 0) {
    container.innerHTML = '<div class="admin-empty">Нет записей</div>';
    return;
  }
  const actionColors = {
    login: '#4caf50', login_failed: '#f44336', logout: '#ff9800',
    token_refresh: '#607d8b', agent_connect: '#00d4ff', agent_disconnect: '#ff5722',
    raw_command: '#ab47bc', admin_create_user: '#2196f3', admin_delete_user: '#f44336',
    admin_set_plan: '#ff9800', agent_upload: '#00bcd4',
  };
  const rows = logs.map(l => {
    const dt = new Date(l.created_at * 1000);
    const ts = dt.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const action = escapeHTML(l.action);
    const color = actionColors[l.action] || 'var(--text-muted)';
    const who = escapeHTML(l.user_name || '?');
    const detail = l.detail ? escapeHTML(l.detail) : '';
    const ip = l.ip ? escapeHTML(l.ip) : '';
    return `<div class="audit-row">
      <span class="audit-ts">${ts}</span>
      <span class="audit-action" style="color:${color}">${action}</span>
      <span class="audit-who">${who}</span>
      <span class="audit-detail">${detail}</span>
      <span class="audit-ip">${ip}</span>
    </div>`;
  }).join('');
  let nav = '';
  if (offset > 0 || offset + 50 < total) {
    const prevBtn = offset > 0 ? `<button onclick="loadAuditLog(${Math.max(0, offset - 50)})">&larr;</button>` : '';
    const nextBtn = offset + 50 < total ? `<button onclick="loadAuditLog(${offset + 50})">&rarr;</button>` : '';
    nav = `<div class="audit-nav">${prevBtn} <span>${offset + 1}-${Math.min(offset + 50, total)} / ${total}</span> ${nextBtn}</div>`;
  }
  container.innerHTML = rows + nav;
}

function switchAdminTab(tab) {
  const tabs = {
    users: { el: document.getElementById('adminUsersTab'), btn: document.getElementById('tabBtnUsers') },
    devices: { el: document.getElementById('adminDevicesTab'), btn: document.getElementById('tabBtnDevices') },
    audit: { el: document.getElementById('adminAuditTab'), btn: document.getElementById('tabBtnAudit') },
  };
  for (const [key, t] of Object.entries(tabs)) {
    if (!t.el || !t.btn) continue;
    if (key === tab) {
      t.el.style.display = 'block';
      t.btn.classList.add('active');
    } else {
      t.el.style.display = 'none';
      t.btn.classList.remove('active');
    }
  }
  if (tab === 'audit') loadAuditLog();
  if (tab === 'devices') loadDeviceProfiles();
}

// compat alias
function toggleAuditTab(tab) { switchAdminTab(tab); }

// ── DEVICE PROFILES (ADMIN) ───────────────────────────────

async function loadDeviceProfiles() {
  try {
    const r = await apiFetch(`${API}/api/device_profiles`, { headers: authHeaders() });
    const data = await r.json();
    if (data.status !== 'ok') return;
    renderDeviceProfiles(data.profiles);
  } catch (e) { console.error('loadDeviceProfiles:', e); }
}

function renderDeviceProfiles(profiles) {
  const container = document.getElementById('adminDevicesList');
  if (!container) return;
  if (!profiles || profiles.length === 0) {
    container.innerHTML = '<div class="admin-empty">Нет профилей устройств</div>';
    return;
  }
  const cards = profiles.map(p => {
    const updated = p.updated_at ? new Date(p.updated_at * 1000).toLocaleString('ru-RU', {
      day: '2-digit', month: '2-digit', year: '2-digit',
      hour: '2-digit', minute: '2-digit'
    }) : '?';
    const disks = (p.disks && Array.isArray(p.disks)) ? p.disks.map(d =>
      `${d.drive || '?'} ${d.total_gb || 0}ГБ / ${d.free_gb || 0}ГБ своб.`
    ).join(', ') : '—';
    const ver = p.agent_version ? `v${escapeHTML(p.agent_version)}` : '?';
    return `<div class="device-card">
      <div class="device-card-header">
        <span class="device-card-name">${escapeHTML(p.hostname || '?')}</span>
        <span class="device-card-ver">${ver}</span>
      </div>
      <div class="device-card-id" title="Нажмите чтобы скопировать" onclick="navigator.clipboard.writeText('${escapeAttr(p.device_id || '')}');showToast('ID скопирован')">${escapeHTML(p.device_id || '?')}</div>
      <div class="device-card-grid">
        <div class="device-card-label">ОС</div><div class="device-card-value">${escapeHTML(p.os || '?')} ${escapeHTML(p.os_version || '')}</div>
        <div class="device-card-label">Пользователь</div><div class="device-card-value">${escapeHTML(p.username || '—')}</div>
        <div class="device-card-label">Раб. стол</div><div class="device-card-value">${escapeHTML(p.desktop_path || '—')}</div>
        <div class="device-card-label">CPU</div><div class="device-card-value">${escapeHTML(p.cpu || '—')}</div>
        <div class="device-card-label">GPU</div><div class="device-card-value">${escapeHTML(p.gpu || '—')}</div>
        <div class="device-card-label">RAM</div><div class="device-card-value">${p.ram_gb ? p.ram_gb + ' ГБ' : '—'}</div>
        <div class="device-card-label">Диски</div><div class="device-card-value">${escapeHTML(disks)}</div>
        <div class="device-card-label">GUID</div><div class="device-card-value device-card-guid">${escapeHTML(p.machine_guid || '—')}</div>
      </div>
      <div class="device-card-footer">Обновлено: ${updated}</div>
    </div>`;
  }).join('');
  container.innerHTML = `<div class="device-cards-grid">${cards}</div>`;
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

// ── TERMS AGREEMENT ─────────────────────────────────────────
async function checkTermsStatus() {
  try {
    const r = await apiFetch(`${API}/api/terms_status`, { headers: authHeaders() });
    const data = await r.json();
    if (data.status === 'ok' && !data.accepted) {
      document.getElementById('termsModal').classList.add('show');
    }
  } catch (e) { console.error('checkTermsStatus:', e); }
}

async function acceptTerms() {
  try {
    await apiFetch(`${API}/api/accept_terms`, {
      method: 'POST', headers: authHeaders(),
    });
  } catch (e) { console.error('acceptTerms:', e); }
  document.getElementById('termsModal').classList.remove('show');
}

// ── USER INFO & DEV MODE ────────────────────────────────────
async function fetchUserInfo() {
  try {
    const r = await apiFetch(`${API}/api/user_info`, { headers: authHeaders() });
    const data = await r.json();
    if (data.status === 'ok') {
      state.userPlan = data.user.plan || 'free';
      const limits = data.user.limits || {};
      if (limits.dev_mode || state.user.name === 'admin') {
        document.getElementById('devModeToggle').style.display = 'flex';
      }
    }
  } catch (e) { console.error('fetchUserInfo:', e); }
}

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
        '<button class="devmode-accordion-toggle" onclick="this.closest(\'.devmode-accordion\').classList.toggle(\'.open\')">' +
          '<svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor"><path d="M2 3.5L5 6.5L8 3.5"/></svg>' +
        '</button>' +
      '</div>' +
      '<div class="devmode-accordion-body">' + escapeHTML(item.text) + '</div>';
    // Toggle on button click
    acc.querySelector('.devmode-accordion-toggle').onclick = function(e) {
      e.stopPropagation();
      acc.classList.toggle('open');
    };
    container.appendChild(acc);
  }
  const output = document.getElementById('devModeOutput');
  output.scrollTop = output.scrollHeight;
}

function handleDevModeKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendDevCommand();
  }
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
