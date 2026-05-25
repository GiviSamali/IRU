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
    <button class="fact-delete" title="Удалить" data-action="delete-memory-fact" data-id="${escapeAttr(f.id)}" data-source="${escapeAttr(f.source || 'user')}">&times;</button>
  </div>`).join('');
}

async function refreshMemoryStats() {
  const deviceParam = state.selectedDevice ? `?device_id=${encodeURIComponent(state.selectedDevice)}` : '';
  const resp = await apiFetch(`${API}/api/memory/stats${deviceParam}`, { headers: authHeaders() });
  const data = await resp.json();
  if (!resp.ok || data.status !== 'ok') {
    throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
  }
  updateMemoryBadge(data.memory_stats);
  renderMemoryPopover();
  return data.memory_stats;
}

async function deleteMemoryFact(id, source, btn) {
  if (!id || !source) return;
  if (btn) btn.disabled = true;
  try {
    const resp = await apiFetch(`${API}/api/memory/facts/delete`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ id: Number(id), source, device_id: state.selectedDevice }),
    });
    const data = await resp.json();
    if (!resp.ok || data.status !== 'ok') {
      throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
    }
    updateMemoryBadge(data.memory_stats);
    await refreshMemoryStats();
  } catch (e) {
    showToast(e.message || 'Ошибка удаления факта', true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

document.addEventListener('click', e => {
  const target = e.target.closest('[data-action="delete-memory-fact"]');
  if (!target) return;
  e.preventDefault();
  e.stopPropagation();
  deleteMemoryFact(target.dataset.id, target.dataset.source, target);
});

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
async function acceptSuggestedFact(taskId, text, category, el, msgIndex = null) {
  try {
    const resp = await apiFetch(`${API}/api/tasks/${taskId}/remember`, {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify({ text, category }),
    });
    const data = await resp.json();
    if (!resp.ok || data.status !== 'ok') {
      throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
    }
    if (data.memory_stats) {
      updateMemoryBadge(data.memory_stats);
    }
    await refreshMemoryStats();
    if (msgIndex !== null && state.messages[msgIndex]) {
      state.messages[msgIndex].suggestedFactDeclined = true;
    }
    el.innerHTML = '<span class="suggest-fact-done">Запомнено</span>';
    setTimeout(() => { if (el.parentNode) el.remove(); }, 2000);
  } catch (e) { showToast('Ошибка сохранения факта', true); }
}

async function declineSuggestedFact(taskId, el, msgIndex = null) {
  if (msgIndex !== null && state.messages[msgIndex]) {
    state.messages[msgIndex].suggestedFactDeclined = true;
  }
  if (!taskId) {
    el.remove();
    return;
  }
  try {
    const resp = await apiFetch(`${API}/api/tasks/${taskId}/decline_fact`, {
      method: 'POST',
      headers: authHeaders(),
    });
    const data = await resp.json();
    if (!resp.ok || data.status !== 'ok') {
      throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
    }
    if (data.memory_stats) updateMemoryBadge(data.memory_stats);
    await refreshMemoryStats();
    el.remove();
  } catch (e) {
    if (msgIndex !== null && state.messages[msgIndex]) {
      state.messages[msgIndex].suggestedFactDeclined = false;
    }
    showToast(e.message || 'Ошибка отказа от факта', true);
  }
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
  const mi = parseInt(el.dataset.index || el.id.replace('ps-', ''), 10);
  const originalRequest = state.messages[mi]?.planOriginalRequest || el.dataset.origReq || '';
  if (state.messages[mi]) {
    state.messages[mi].planDismissed = true;
    state.messages[mi].hideAfterPlanChoice = true;
  }
  renderMessages();
  runPlan(chatId, originalRequest);
}

function declinePlanSuggestion(el) {
  const mi = parseInt(el.dataset.index || el.id.replace('ps-', ''), 10);
  const originalRequest = state.messages[mi]?.planOriginalRequest || el.dataset.origReq || '';
  if (state.messages[mi]) {
    state.messages[mi].planDeclined = true;
    state.messages[mi].hideAfterPlanChoice = true;
  }
  renderMessages();
  const taskId = state.messages[mi]?._taskId || '';
  declinePlanAndContinue(taskId, originalRequest);
}

async function declinePlanAndContinue(taskId, originalRequest) {
  if (!taskId) {
    await sendMessageDirect(originalRequest, { plan_declined: true });
    return;
  }
  try {
    const resp = await apiFetch(`${API}/api/tasks/${taskId}/decline_plan`, {
      method: 'POST',
      headers: authHeaders(),
    });
    const data = await resp.json();
    if (!resp.ok || data.status !== 'ok') {
      throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
    }
  } catch (e) {
    showToast(e.message || 'Ошибка отказа от плана', true);
    return;
  }
  await sendMessageDirect(originalRequest, { plan_declined: true });
}

async function sendMessageDirect(text, extraModes = {}) {
  if (!text || !state.currentChatId) return;
  const msgIndex = state.messages.length;
  state.messages.push({ role: 'assistant', loading: true, currentStep: 'ИРУ думает...' });
  renderMessages();
  try {
    const body = {
      message: text,
      device_id: state.selectedDevice,
      chat_id: state.currentChatId,
      modes: { ...extraModes },
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
