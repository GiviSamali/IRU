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
    list.innerHTML = '<div class="admin-empty">' + (q ? 'РќРёС‡РµРіРѕ РЅРµ РЅР°Р№РґРµРЅРѕ' : 'РќРµС‚ РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№') + '</div>';
    document.getElementById('adminStats').textContent = '';
    return;
  }

  // Р“СЂСѓРїРїРёСЂРѕРІРєР° РїРѕ РїР»Р°РЅР°Рј
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
  document.getElementById('adminStats').textContent = 'Р’СЃРµРіРѕ: ' + _allAdminUsers.length + (q ? ' (РїРѕРєР°Р·Р°РЅРѕ: ' + filtered.length + ')' : '');
}

function renderAdminUserItem(u) {
  const isAdmin = u.id === 1;
  const deleteBtn = isAdmin ? '' : `
    <button class="admin-user-delete" onclick="adminDeleteUser(${u.id}, '${escapeAttr(u.name)}')" title="РЈРґР°Р»РёС‚СЊ">
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
        <span class="admin-user-token" title="РќР°Р¶РјРёС‚Рµ С‡С‚РѕР±С‹ СЃРєРѕРїРёСЂРѕРІР°С‚СЊ" onclick="navigator.clipboard.writeText('${escapeAttr(u.token)}');showToast('РўРѕРєРµРЅ СЃРєРѕРїРёСЂРѕРІР°РЅ')">${u.token}</span>
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
      showToast(`РџР»Р°РЅ РёР·РјРµРЅС‘РЅ: ${plan}`);
      loadAdminUsers();
    } else {
      showToast(data.error || 'РћС€РёР±РєР°', true);
      loadAdminUsers();
    }
  } catch (e) { showToast('РћС€РёР±РєР°: ' + e.message, true); }
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
      showToast(`РЎРѕР·РґР°РЅ: ${data.user.name}`);
      if (tok) {
        prompt('РўРѕРєРµРЅ РґР»СЏ ' + data.user.name + ' (СЃРєРѕРїРёСЂСѓР№С‚Рµ, РїРѕРєР°Р·С‹РІР°РµС‚СЃСЏ РѕРґРёРЅ СЂР°Р·):', tok);
      }
      loadAdminUsers();
    } else {
      showToast(data.detail || 'РћС€РёР±РєР°', true);
    }
  } catch (e) { showToast('РћС€РёР±РєР°: ' + e.message, true); }
}

async function adminDeleteUser(userId, userName) {
  if (!confirm(`РЈРґР°Р»РёС‚СЊ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ "${userName}"? Р’СЃРµ РµРіРѕ С‡Р°С‚С‹ Рё РґР°РЅРЅС‹Рµ Р±СѓРґСѓС‚ СѓРґР°Р»РµРЅС‹.`)) return;
  try {
    const r = await apiFetch(`${API}/api/admin/users/${userId}`, {
      method: 'DELETE', headers: authHeaders(),
    });
    const data = await r.json();
    if (data.status === 'ok') {
      showToast(`РЈРґР°Р»С‘РЅ: ${userName}`);
      loadAdminUsers();
    }
  } catch (e) { showToast('РћС€РёР±РєР°: ' + e.message, true); }
}

// в”Ђв”Ђ AUDIT LOG (ADMIN) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
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
    container.innerHTML = '<div class="admin-empty">РќРµС‚ Р·Р°РїРёСЃРµР№</div>';
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

// в”Ђв”Ђ DEVICE PROFILES (ADMIN) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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
    container.innerHTML = '<div class="admin-empty">РќРµС‚ РїСЂРѕС„РёР»РµР№ СѓСЃС‚СЂРѕР№СЃС‚РІ</div>';
    return;
  }
  const cards = profiles.map(p => {
    const updated = p.updated_at ? new Date(p.updated_at * 1000).toLocaleString('ru-RU', {
      day: '2-digit', month: '2-digit', year: '2-digit',
      hour: '2-digit', minute: '2-digit'
    }) : '?';
    const disks = (p.disks && Array.isArray(p.disks)) ? p.disks.map(d =>
      `${d.drive || '?'} ${d.total_gb || 0}Р“Р‘ / ${d.free_gb || 0}Р“Р‘ СЃРІРѕР±.`
    ).join(', ') : 'вЂ”';
    const ver = p.agent_version ? `v${escapeHTML(p.agent_version)}` : '?';
    return `<div class="device-card">
      <div class="device-card-header">
        <span class="device-card-name">${escapeHTML(p.hostname || '?')}</span>
        <span class="device-card-ver">${ver}</span>
      </div>
      <div class="device-card-id" title="РќР°Р¶РјРёС‚Рµ С‡С‚РѕР±С‹ СЃРєРѕРїРёСЂРѕРІР°С‚СЊ" onclick="navigator.clipboard.writeText('${escapeAttr(p.device_id || '')}');showToast('ID СЃРєРѕРїРёСЂРѕРІР°РЅ')">${escapeHTML(p.device_id || '?')}</div>
      <div class="device-card-grid">
        <div class="device-card-label">РћРЎ</div><div class="device-card-value">${escapeHTML(p.os || '?')} ${escapeHTML(p.os_version || '')}</div>
        <div class="device-card-label">РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ</div><div class="device-card-value">${escapeHTML(p.username || 'вЂ”')}</div>
        <div class="device-card-label">Р Р°Р±. СЃС‚РѕР»</div><div class="device-card-value">${escapeHTML(p.desktop_path || 'вЂ”')}</div>
        <div class="device-card-label">CPU</div><div class="device-card-value">${escapeHTML(p.cpu || 'вЂ”')}</div>
        <div class="device-card-label">GPU</div><div class="device-card-value">${escapeHTML(p.gpu || 'вЂ”')}</div>
        <div class="device-card-label">RAM</div><div class="device-card-value">${p.ram_gb ? p.ram_gb + ' Р“Р‘' : 'вЂ”'}</div>
        <div class="device-card-label">Р”РёСЃРєРё</div><div class="device-card-value">${escapeHTML(disks)}</div>
        <div class="device-card-label">GUID</div><div class="device-card-value device-card-guid">${escapeHTML(p.machine_guid || 'вЂ”')}</div>
      </div>
      <div class="device-card-footer">РћР±РЅРѕРІР»РµРЅРѕ: ${updated}</div>
    </div>`;
  }).join('');
  container.innerHTML = `<div class="device-cards-grid">${cards}</div>`;
}

function copyToken(token) {
  navigator.clipboard.writeText(token).then(() => {
    showToast('РўРѕРєРµРЅ СЃРєРѕРїРёСЂРѕРІР°РЅ');
  }).catch(() => {
    // Fallback
    const ta = document.createElement('textarea');
    ta.value = token; document.body.appendChild(ta);
    ta.select(); document.execCommand('copy'); ta.remove();
    showToast('РўРѕРєРµРЅ СЃРєРѕРїРёСЂРѕРІР°РЅ');
  });
}

// в”Ђв”Ђ TERMS AGREEMENT в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
