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
      state.user = { ...(state.user || {}), ...(data.user || {}) };
      state.userPlan = data.user.plan || 'free';
      const limits = data.user.limits || {};
      const isAdmin = !!(data.user?.is_admin || data.user?.role === 'admin' || limits.admin_panel || limits.admin || state.user?.name === 'admin');
      document.getElementById('btnAdmin').style.display = isAdmin ? 'flex' : 'none';
      document.getElementById('devModeToggle').style.display = (limits.dev_mode || isAdmin) ? 'flex' : 'none';
    }
  } catch (e) { console.error('fetchUserInfo:', e); }
}

