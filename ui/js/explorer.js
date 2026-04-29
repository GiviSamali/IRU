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
    document.getElementById('explorerList').innerHTML = '<div class="explorer-empty">РќРµС‚ СѓСЃС‚СЂРѕР№СЃС‚РІР°</div>';
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
    document.getElementById('explorerList').innerHTML = `<div class="explorer-empty">РћС€РёР±РєР°: ${e.message}</div>`;
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
    container.innerHTML = '<div class="explorer-empty">РџСѓСЃС‚Р°СЏ РґРёСЂРµРєС‚РѕСЂРёСЏ</div>';
    return;
  }
  let html = '';
  for (const d of (dirs || [])) {
    html += `<div class="explorer-item dir" onclick="explorerNavigate('${escapeAttr(d.path)}')">
      <svg class="icon" viewBox="0 0 24 24" fill="currentColor"><path d="M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>
      <span class="name">${escapeHTML(d.name)}</span>
      <div class="file-actions">
        <div class="file-action-btn" onclick="event.stopPropagation(); openOnDevice('${escapeAttr(d.path)}')" title="РћС‚РєСЂС‹С‚СЊ РЅР° РџРљ">
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
        <div class="file-action-btn" onclick="event.stopPropagation(); openOnDevice('${escapeAttr(f.path)}')" title="РћС‚РєСЂС‹С‚СЊ РЅР° РџРљ">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
        </div>
        <div class="file-action-btn" onclick="event.stopPropagation(); downloadFile('${escapeAttr(f.path)}')" title="РЎРєР°С‡Р°С‚СЊ">
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

// в”Ђв”Ђ FILE ACTIONS в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
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
    showToast('РћС‚РєСЂС‹РІР°СЋ...');
  } catch (e) { showToast('РћС€РёР±РєР°: ' + e.message, true); }
}

async function downloadFile(filePath) {
  if (!state.selectedDevice) return;
  showToast('РџРѕРґРіРѕС‚РѕРІРєР° Рє СЃРєР°С‡РёРІР°РЅРёСЋ...');
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
      showToast(data.error || 'РћС€РёР±РєР°', true);
    }
  } catch (e) { showToast('РћС€РёР±РєР°: ' + e.message, true); }
}

// в”Ђв”Ђ UTILS в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
