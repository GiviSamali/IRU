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
  expandedStepDetails: new Set(),
  devModeOpen: false,
  userPlan: 'free',
  pollers: { devices: null },
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
  stopDevicePolling();
  state.user = null;
  state.chats = [];
  state.currentChatId = null;
  state.messages = [];
  state.devices = {};
  state.selectedDevice = null;
  state.sendTarget = 'single';
  state.pendingTasks = [];
  state.explorerOpen = false;
  state.devModeOpen = false;
  document.getElementById('authScreen').style.display = 'flex';
  document.getElementById('appRoot').classList.remove('active');
  const btnAdmin = document.getElementById('btnAdmin');
  const devModeToggle = document.getElementById('devModeToggle');
  if (btnAdmin) btnAdmin.style.display = 'none';
  if (devModeToggle) devModeToggle.style.display = 'none';
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

function stopDevicePolling() {
  if (!state.pollers.devices) return;
  clearInterval(state.pollers.devices);
  state.pollers.devices = null;
}

function startDevicePolling() {
  stopDevicePolling();
  state.pollers.devices = setInterval(fetchDevices, 5000);
}

function showApp() {
  document.getElementById('authScreen').style.display = 'none';
  document.getElementById('appRoot').classList.add('active');
  document.getElementById('userName').textContent = state.user.name;
  loadChats();
  fetchDevices();
  startDevicePolling();
  checkConsent();
  checkTermsStatus();
  fetchUserInfo();
  if (typeof renderInputModeBtn === 'function') renderInputModeBtn();
  if (typeof updateCharCount === 'function') updateCharCount();
}

