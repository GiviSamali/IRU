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

function plural(n, one, few, many) {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
  return many;
}

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
      <button class="chat-item-rename" onclick="startRenameChat(${c.id}, event)" title="Переименовать">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M17 3a2.83 2.83 0 114 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>
      </button>
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
      if (commands.length === 1) {
        // Одна команда — обычная плашка, но с stripUtfPrefix
        const c = commands[0];
        const stdout = c.result?.stdout || '';
        const stderr = c.result?.stderr || '';
        const errMsg = c.result?.error || '';
        const output = stdout || stderr || errMsg || '(нет вывода)';
        const isOk = !errMsg && (c.result?.returncode === 0 || c.result?.returncode == null);
        const statusCls = isOk ? 'ok' : 'err';
        const statusTxt = isOk ? '\u2713' : '\u2717';
        const deviceTag = c.device_id ? `<span class="cmd-device">${escapeHTML(c.device_id)}</span>` : '';
        const cmdText = escapeHTML(stripUtfPrefix(c.command || ''));
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
      } else {
        // Группа команд — свёрнутая плашка
        const groupId = 'cmdgrp-' + mi;
        const lastCmd = commands[commands.length - 1];
        const lastClean = stripUtfPrefix(lastCmd.command || '');
        const lastTrunc = lastClean.length > 120 ? lastClean.slice(0, 120) + '\u2026' : lastClean;
        const lastIsOk = !(lastCmd.result?.error) && (lastCmd.result?.returncode === 0 || lastCmd.result?.returncode == null);
        const lastStatusCls = lastIsOk ? 'ok' : 'err';
        const lastStatusTxt = lastIsOk ? '\u2713' : '\u2717';
        const lastDevice = lastCmd.device_id ? `<span class="cmd-device">${escapeHTML(lastCmd.device_id)}</span>` : '';
        const extra = commands.length - 1;
        bodyHTML += `
          <div class="cmd-group" id="${groupId}">
            <div class="cmd-group-header" onclick="document.getElementById('${groupId}').classList.toggle('open')">
              <span class="cmd-group-arrow">\u25be</span>
              <span class="cmd-group-text">${escapeHTML(lastTrunc)}</span>
              ${lastDevice}
              <span class="cmd-status ${lastStatusCls}">${lastStatusTxt}</span>
              <span class="cmd-group-extra">(+${extra} ещё)</span>
            </div>
            <div class="cmd-group-body">`;
        for (let i = 0; i < commands.length; i++) {
          const c = commands[i];
          const stdout = c.result?.stdout || '';
          const stderr = c.result?.stderr || '';
          const errMsg = c.result?.error || '';
          const output = stdout || stderr || errMsg || '(нет вывода)';
          const isOk = !errMsg && (c.result?.returncode === 0 || c.result?.returncode == null);
          const statusCls = isOk ? 'ok' : 'err';
          const statusTxt = isOk ? '\u2713' : '\u2717';
          const deviceTag = c.device_id ? `<span class="cmd-device">${escapeHTML(c.device_id)}</span>` : '';
          const cmdText = escapeHTML(stripUtfPrefix(c.command || ''));
          bodyHTML += `
              <div class="cmd-entry" onclick="event.stopPropagation(); this.classList.toggle('open')">
                <div class="cmd-summary">
                  <span class="cmd-icon">\u25b8</span>
                  <span class="cmd-text">${cmdText}</span>
                  ${deviceTag}
                  <span class="cmd-status ${statusCls}">${statusTxt}</span>
                </div>
                <div class="cmd-details">${escapeHTML(output)}</div>
              </div>`;
        }
        bodyHTML += `
            </div>
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

    // Suggest memory block (Point 12)
    let suggestHTML = '';
    if (m.suggestedFact && m.suggestedFact.text) {
      const sf = m.suggestedFact;
      const tid = m._taskId || '';
      suggestHTML = `<div class="suggest-fact-block" id="sf-${mi}">
        <div class="suggest-fact-label">ИРУ предлагает запомнить:</div>
        <div class="suggest-fact-text">${escapeHTML(sf.text)}</div>
        <div class="suggest-fact-actions">
          <button class="suggest-fact-accept" onclick="acceptSuggestedFact('${tid}','${escapeAttr(sf.text)}','${escapeAttr(sf.category || '')}',document.getElementById('sf-${mi}'))">Запомнить</button>
          <button class="suggest-fact-decline" onclick="declineSuggestedFact(document.getElementById('sf-${mi}'))">Не надо</button>
        </div>
      </div>`;
    }

    // Plan suggestion banner
    let planHTML = '';
    // TODO: persist planDismissed/planDeclined на сервере, чтобы после F5 плашка не возвращалась
    if (m.planSuggestion && !m.planDismissed && !m.planDeclined) {
      if (m.planTrialUsed) {
        planHTML = `<div class="plan-suggest-block" id="ps-${mi}">
          <div class="plan-suggest-text" style="color:#888;">Режим План доступен на Pro-тарифе. Вы уже использовали пробный запуск.</div>
        </div>`;
      } else {
        const desc = escapeHTML(m.planSuggestion);
        const origReq = escapeAttr(m.planOriginalRequest || '');
        planHTML = `<div class="plan-suggest-block" id="ps-${mi}" data-chat-id="${state.currentChatId}" data-orig-req="${origReq}">
          <div class="plan-suggest-text">Задача непростая: ${desc}. В режиме План ИРУ составит и выполнит пошаговое решение.</div>
          <div class="plan-suggest-actions">
            <button class="plan-suggest-accept" onclick="acceptPlanSuggestion(document.getElementById('ps-${mi}'))">Запустить план</button>
            <button class="plan-suggest-decline" onclick="declinePlanSuggestion(document.getElementById('ps-${mi}'))">Без плана</button>
          </div>
          <div class="plan-suggest-warning" style="font-size:11px;color:#888;margin-top:6px;">Команды плана будут выполнены без отдельного подтверждения. Нажимайте, только если доверяете задаче.</div>
        </div>`;
      }
    }

    if (m.loading) {
      const stepText = escapeHTML(m.currentStep || 'ИРУ думает...');
      const liveTasksHTML = renderTaskBlock(m.liveTasks);
      const taskBlockAttr = (m.liveTasks && m.liveTasks.length > 0) ? '' : ' hidden';
      html += `<div class="msg assistant msg-thinking"><div class="msg-role">иру</div><div class="msg-body"><div class="live-status"><span class="live-dot"></span><span class="live-text">${stepText}</span></div><div class="task-block-live"${taskBlockAttr}>${liveTasksHTML}</div></div></div>`;
    } else {
      html += `<div class="msg ${m.role}"><div class="msg-role">${roleLabel}</div><div class="msg-body">${bodyHTML}${confirmBtns}${suggestHTML}${planHTML}</div></div>`;
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

  const messageToSend = buildMessageWithAttachments(text);

  input.value = '';
  autoGrow(input);
  clearAttachments();

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
      message: messageToSend,
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
        const msg = {
          role: 'assistant',
          content: task.answer || 'Готово.',
          commands: task.commands,
          tasks: task.tasks || [],
        };
        if (task.suggested_fact) msg.suggestedFact = task.suggested_fact;
        if (task.plan_suggestion) msg.planSuggestion = task.plan_suggestion;
        if (task.plan_original_request) msg.planOriginalRequest = task.plan_original_request;
        if (task.plan_trial_used) msg.planTrialUsed = true;
        if (task.auto_plan) msg.autoPlan = true;
        msg._taskId = taskId;
        state.messages[msgIndex] = msg;
        state.pendingTasks = state.pendingTasks.filter(t => t.task_id !== taskId);
        // Update memory badge (Point 10)
        if (task.memory_stats) updateMemoryBadge(task.memory_stats);
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
  // Это работает и для IruAgent.zip, и для IruAgent.exe.
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
function stripUtfPrefix(cmd) {
  return (cmd || '').replace(/^\s*\[Console\]::OutputEncoding\s*=\s*\[System\.Text\.Encoding\]::UTF8;\s*\$OutputEncoding\s*=\s*\[System\.Text\.Encoding\]::UTF8;\s*/i, '');
}
function escapeHTML(s) { return s ? s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') : ''; }
function escapeAttr(s) { if (s == null) return ''; return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
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

// ── Голосовой ввод (Web Speech API) ──────────────────────────
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let isRecording = false;

const voiceBtn = document.getElementById('voiceBtn');
if (!SpeechRecognition) {
  if (voiceBtn) {
    voiceBtn.disabled = true;
    voiceBtn.title = 'Голосовой ввод не поддерживается этим браузером. Откройте в Chrome или Edge';
  }
} else if (voiceBtn) {
  voiceBtn.addEventListener('click', toggleVoice);
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.shiftKey && (e.key === 'M' || e.key === 'm')) {
      e.preventDefault();
      toggleVoice();
    }
  });
}

function toggleVoice() {
  if (isRecording) { stopVoice(); } else { startVoice(); }
}

function startVoice() {
  if (!SpeechRecognition) return;
  recognition = new SpeechRecognition();
  recognition.lang = 'ru-RU';
  recognition.interimResults = true;
  recognition.continuous = true;

  const input = document.getElementById('chatInput');
  const initialValue = input.value;
  let finalTranscript = '';

  recognition.onstart = () => {
    isRecording = true;
    voiceBtn.classList.add('recording');
    voiceBtn.setAttribute('aria-label', 'Остановить запись');
  };

  recognition.onresult = (event) => {
    let interim = '';
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const transcript = event.results[i][0].transcript;
      if (event.results[i].isFinal) {
        finalTranscript += transcript;
      } else {
        interim += transcript;
      }
    }
    const sep = initialValue && !initialValue.endsWith(' ') ? ' ' : '';
    input.value = initialValue + sep + finalTranscript + interim;
    if (input.tagName === 'TEXTAREA') {
      input.style.height = 'auto';
      input.style.height = input.scrollHeight + 'px';
    }
    updateCharCount();
  };

  recognition.onerror = (event) => {
    console.warn('Speech recognition error:', event.error);
    if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
      alert('Доступ к микрофону запрещён. Разрешите в настройках браузера.');
    }
    stopVoice();
  };

  recognition.onend = () => { stopVoice(); };

  try { recognition.start(); } catch (err) {
    console.warn('Не удалось запустить распознавание:', err);
    stopVoice();
  }
}

function stopVoice() {
  isRecording = false;
  if (voiceBtn) {
    voiceBtn.classList.remove('recording');
    voiceBtn.setAttribute('aria-label', 'Включить микрофон');
  }
  if (recognition) {
    try { recognition.stop(); } catch (_) {}
    recognition = null;
  }
}

// ── Прикрепление текстовых файлов ───────────────────────────
const ALLOWED_EXT = new Set([
  'txt','md','csv','json','xml','yml','yaml','py','js','ts','html','css',
  'sql','log','ini','conf','sh','ps1','bat','go','rs','java','cpp','c','h'
]);
const MAX_FILE_SIZE = 500 * 1024;
const MAX_TOTAL_SIZE = 2 * 1024 * 1024;
const MAX_FILES = 5;

let attachedFiles = [];

const attachBtn = document.getElementById('attachBtn');
const fileInput = document.getElementById('fileInput');
const attachmentsBar = document.getElementById('attachmentsBar');

if (attachBtn && fileInput) {
  attachBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', (e) => {
    handleFiles(Array.from(e.target.files));
    fileInput.value = '';
  });
}

// Drag & drop
const _dragTarget = document.body;
['dragenter', 'dragover'].forEach(evt => {
  _dragTarget.addEventListener(evt, (e) => {
    if (e.dataTransfer && e.dataTransfer.types.includes('Files')) {
      e.preventDefault();
      _dragTarget.classList.add('drag-over');
    }
  });
});
['dragleave', 'drop'].forEach(evt => {
  _dragTarget.addEventListener(evt, (e) => {
    if (evt === 'drop') {
      e.preventDefault();
      if (e.dataTransfer && e.dataTransfer.files) {
        handleFiles(Array.from(e.dataTransfer.files));
      }
    }
    _dragTarget.classList.remove('drag-over');
  });
});

// Paste файлов в поле ввода
(function() {
  const ci = document.getElementById('chatInput');
  if (ci) {
    ci.addEventListener('paste', (e) => {
      if (e.clipboardData && e.clipboardData.files && e.clipboardData.files.length) {
        e.preventDefault();
        handleFiles(Array.from(e.clipboardData.files));
      }
    });
  }
})();

function handleFiles(files) {
  for (const file of files) {
    const ext = (file.name.split('.').pop() || '').toLowerCase();
    if (!ALLOWED_EXT.has(ext)) {
      alert(`Файл "${file.name}" не поддерживается. Принимаются только текстовые файлы (.txt, .md, .csv, .json, .py и т.п.)`);
      continue;
    }
    if (file.size > MAX_FILE_SIZE) {
      alert(`Файл "${file.name}" слишком большой (${Math.round(file.size/1024)} КБ). Максимум 500 КБ.`);
      continue;
    }
    if (attachedFiles.length >= MAX_FILES) {
      alert(`Можно прикрепить максимум ${MAX_FILES} файлов.`);
      return;
    }
    const totalSize = attachedFiles.reduce((s, f) => s + f.size, 0);
    if (totalSize + file.size > MAX_TOTAL_SIZE) {
      alert('Суммарный размер файлов превысит 2 МБ. Удалите ненужные.');
      return;
    }
    const reader = new FileReader();
    reader.onload = (ev) => {
      attachedFiles.push({ name: file.name, size: file.size, content: ev.target.result });
      renderAttachments();
    };
    reader.onerror = () => { alert(`Не удалось прочитать файл "${file.name}"`); };
    reader.readAsText(file, 'UTF-8');
  }
}

function renderAttachments() {
  if (!attachmentsBar) return;
  if (attachedFiles.length === 0) {
    attachmentsBar.hidden = true;
    attachmentsBar.innerHTML = '';
    return;
  }
  attachmentsBar.hidden = false;
  attachmentsBar.innerHTML = attachedFiles.map((f, idx) => `
    <div class="attachment-chip">
      <span class="chip-name" title="${escapeHTML(f.name)}">${escapeHTML(f.name)}</span>
      <span class="chip-size">${formatAttachSize(f.size)}</span>
      <button class="chip-remove" data-idx="${idx}" aria-label="Удалить">\u00d7</button>
    </div>
  `).join('');
  attachmentsBar.querySelectorAll('.chip-remove').forEach(btn => {
    btn.addEventListener('click', (e) => {
      attachedFiles.splice(parseInt(e.target.dataset.idx, 10), 1);
      renderAttachments();
    });
  });
}

function formatAttachSize(bytes) {
  if (bytes < 1024) return bytes + ' Б';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' КБ';
  return (bytes / 1024 / 1024).toFixed(2) + ' МБ';
}

function buildMessageWithAttachments(userText) {
  if (attachedFiles.length === 0) return userText;
  const parts = ['=== Прикреплённые файлы ==='];
  for (const f of attachedFiles) {
    parts.push(`\n[${f.name}, ${f.size} байт]`);
    parts.push(f.content);
  }
  parts.push('\n=== Сообщение ===');
  parts.push(userText);
  return parts.join('\n');
}

function clearAttachments() {
  attachedFiles = [];
  renderAttachments();
}

// ── MOBILE PLUS POPOVER (Point 6) ─────────────────────────────
function toggleMobilePlusPopover() {
  document.getElementById('mobilePlusPopover').classList.toggle('show');
}
function closeMobilePlusPopover() {
  document.getElementById('mobilePlusPopover').classList.remove('show');
}
document.addEventListener('click', e => {
  if (!e.target.closest('.mobile-plus-btn') && !e.target.closest('.mobile-plus-popover')) {
    closeMobilePlusPopover();
  }
});

// Mobile voice/send toggle: if text present show send, else show voice
function updateMobileSendVoice() {
  if (window.innerWidth > 768) return;
  const input = document.getElementById('chatInput');
  const voice = document.getElementById('voiceBtn');
  const send = document.getElementById('btnSend');
  if (!input || !voice || !send) return;
  const hasText = input.value.trim().length > 0;
  voice.classList.toggle('hidden-mobile', hasText);
  send.classList.toggle('hidden-mobile', !hasText);
}
// Hook into input events
(function() {
  const ci = document.getElementById('chatInput');
  if (ci) {
    ci.addEventListener('input', updateMobileSendVoice);
    // Initial state
    setTimeout(updateMobileSendVoice, 100);
  }
  window.addEventListener('resize', updateMobileSendVoice);
})();

// ── MEMORY BADGE & POPOVER (Point 10) ──────────────────────────
let _memoryStats = { facts: 0, commands: 0, facts_list: [] };

function updateMemoryBadge(stats) {
  if (!stats) return;
  _memoryStats = stats;
  const badge = document.getElementById('memoryBadge');
  const text = document.getElementById('memoryBadgeText');
  if (!badge || !text) return;
  const f = stats.facts || 0;
  const c = stats.commands || 0;
  if (f === 0 && c === 0) { badge.style.display = 'none'; return; }
  badge.style.display = 'inline-flex';
  const cLabel = c > 20 ? '20+' : c;
  const fLabel = f > 20 ? '20+' : f;
  const fWord = f > 20 ? 'фактов' : plural(f, 'факт', 'факта', 'фактов');
  const cWord = c > 20 ? 'команд' : plural(c, 'команда', 'команды', 'команд');
  text.textContent = `${fLabel} ${fWord}, ${cLabel} ${cWord}`;
}

function toggleMemoryPopover() {
  const pop = document.getElementById('memoryPopover');
  pop.classList.toggle('show');
  if (pop.classList.contains('show')) renderMemoryPopover();
}
document.addEventListener('click', e => {
  if (!e.target.closest('.memory-badge')) {
    const pop = document.getElementById('memoryPopover');
    if (pop) pop.classList.remove('show');
  }
});

function renderMemoryPopover() {
  const list = document.getElementById('memoryPopoverList');
  if (!list) return;
  const facts = _memoryStats.facts_list || [];
  if (facts.length === 0) {
    list.innerHTML = '<div style="font-size:11px;color:var(--text-muted);padding:8px 0">Нет закреплённых фактов</div>';
    return;
  }
  list.innerHTML = facts.map(f => `<div class="memory-popover-item">
    <span class="fact-text">${escapeHTML(f.text || f.fact || '')}</span>
    <span class="fact-cat">${escapeHTML(f.category || '')}</span>
  </div>`).join('');
}

// ── CHAT RENAME (Point 11) ──────────────────────────────────────
function startRenameChat(chatId, event) {
  event.stopPropagation();
  const item = event.target.closest('.chat-item');
  if (!item) return;
  const textEl = item.querySelector('.chat-item-text');
  if (!textEl) return;
  const currentTitle = textEl.textContent;
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'chat-item-edit';
  input.value = currentTitle;
  input.maxLength = 80;
  textEl.replaceWith(input);
  input.focus();
  input.select();

  const finish = async (save) => {
    if (input._done) return;
    input._done = true;
    const newTitle = input.value.trim();
    if (save && newTitle && newTitle !== currentTitle && newTitle.length <= 80) {
      try {
        await apiFetch(`${API}/api/chats/${chatId}`, {
          method: 'PATCH', headers: authHeaders(),
          body: JSON.stringify({ title: newTitle }),
        });
        const chat = state.chats.find(c => c.id === chatId);
        if (chat) chat.title = newTitle;
        if (state.currentChatId === chatId) {
          document.getElementById('headerTitle').textContent = newTitle;
        }
      } catch (e) { showToast('Ошибка переименования', true); }
    }
    renderChatList();
  };

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    if (e.key === 'Escape') { finish(false); }
  });
  input.addEventListener('blur', () => finish(true));
}

// ── SUGGEST MEMORY (Point 12) ───────────────────────────────────
async function acceptSuggestedFact(taskId, text, category, el) {
  try {
    const resp = await apiFetch(`${API}/api/tasks/${taskId}/remember`, {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify({ text, category }),
    });
    const data = await resp.json();
    // Обновить локальный _memoryStats чтобы бейдж и popover были синхронны
    if (data.status === 'ok') {
      _memoryStats.facts_list = _memoryStats.facts_list || [];
      _memoryStats.facts_list.push({ id: data.fact_id, text: text, category: category || '' });
      _memoryStats.facts = _memoryStats.facts_list.length;
      updateMemoryBadge(_memoryStats);
    }
    el.innerHTML = '<span class="suggest-fact-done">Запомнено</span>';
    setTimeout(() => { if (el.parentNode) el.remove(); }, 2000);
  } catch (e) { showToast('Ошибка сохранения факта', true); }
}

function declineSuggestedFact(el) {
  el.remove();
}

// ── PLAN SUGGESTION ───────────────────────────────────────
async function runPlan(chatId, originalRequest) {
  try {
    const resp = await apiFetch(`${API}/api/run_plan/${chatId}`, {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify({ original_request: originalRequest, confirmed: true }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      showToast(data.detail || 'Ошибка запуска плана', true);
      return;
    }
    if (data.task_id) {
      const msgIndex = state.messages.length;
      state.messages.push({ role: 'assistant', loading: true, currentStep: 'Запуск плана...' });
      state.pendingTasks.push({ task_id: data.task_id, msgIndex });
      renderMessages();
      pollTask(data.task_id, msgIndex);
    }
  } catch (e) { showToast('Ошибка запуска плана', true); }
}

function acceptPlanSuggestion(el) {
  const chatId = parseInt(el.dataset.chatId, 10);
  const originalRequest = el.dataset.origReq || '';
  const mi = parseInt(el.id.replace('ps-', ''), 10);
  if (state.messages[mi]) state.messages[mi].planDismissed = true;
  renderMessages();
  runPlan(chatId, originalRequest);
}

function declinePlanSuggestion(el) {
  const originalRequest = el.dataset.origReq || '';
  const mi = parseInt(el.id.replace('ps-', ''), 10);
  if (state.messages[mi]) state.messages[mi].planDeclined = true;
  renderMessages();
  sendMessageDirect(originalRequest);
}

async function sendMessageDirect(text) {
  if (!text || !state.currentChatId) return;
  const msgIndex = state.messages.length;
  state.messages.push({ role: 'assistant', loading: true, currentStep: 'ИРУ думает...' });
  renderMessages();
  try {
    const body = {
      message: text,
      device_id: state.selectedDevice,
      chat_id: state.currentChatId,
      modes: {},
    };
    if (state.sendTarget === 'all') body.broadcast = true;
    const resp = await apiFetch(`${API}/nl_command`, {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (data.task_id) {
      state.pendingTasks.push({ task_id: data.task_id, msgIndex });
      pollTask(data.task_id, msgIndex);
    }
  } catch (e) {
    state.messages[msgIndex] = { role: 'assistant', content: 'Ошибка: ' + (e.message || e) };
    renderMessages();
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
