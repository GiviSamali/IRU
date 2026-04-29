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

// в”Ђв”Ђ MEMORY BADGE & POPOVER (Point 10) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
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
  const fWord = f > 20 ? 'С„Р°РєС‚РѕРІ' : plural(f, 'С„Р°РєС‚', 'С„Р°РєС‚Р°', 'С„Р°РєС‚РѕРІ');
  const cWord = c > 20 ? 'РєРѕРјР°РЅРґ' : plural(c, 'РєРѕРјР°РЅРґР°', 'РєРѕРјР°РЅРґС‹', 'РєРѕРјР°РЅРґ');
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
    list.innerHTML = '<div style="font-size:11px;color:var(--text-muted);padding:8px 0">РќРµС‚ Р·Р°РєСЂРµРїР»С‘РЅРЅС‹С… С„Р°РєС‚РѕРІ</div>';
    return;
  }
  list.innerHTML = facts.map(f => `<div class="memory-popover-item">
    <span class="fact-text">${escapeHTML(f.text || f.fact || '')}</span>
    <span class="fact-cat">${escapeHTML(f.category || '')}</span>
  </div>`).join('');
}

// в”Ђв”Ђ CHAT RENAME (Point 11) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
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
      } catch (e) { showToast('РћС€РёР±РєР° РїРµСЂРµРёРјРµРЅРѕРІР°РЅРёСЏ', true); }
    }
    renderChatList();
  };

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    if (e.key === 'Escape') { finish(false); }
  });
  input.addEventListener('blur', () => finish(true));
}

// в”Ђв”Ђ SUGGEST MEMORY (Point 12) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
async function acceptSuggestedFact(taskId, text, category, el) {
  try {
    const resp = await apiFetch(`${API}/api/tasks/${taskId}/remember`, {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify({ text, category }),
    });
    const data = await resp.json();
    // РћР±РЅРѕРІРёС‚СЊ Р»РѕРєР°Р»СЊРЅС‹Р№ _memoryStats С‡С‚РѕР±С‹ Р±РµР№РґР¶ Рё popover Р±С‹Р»Рё СЃРёРЅС…СЂРѕРЅРЅС‹
    if (data.status === 'ok') {
      _memoryStats.facts_list = _memoryStats.facts_list || [];
      _memoryStats.facts_list.push({ id: data.fact_id, text: text, category: category || '' });
      _memoryStats.facts = _memoryStats.facts_list.length;
      updateMemoryBadge(_memoryStats);
    }
    el.innerHTML = '<span class="suggest-fact-done">Р—Р°РїРѕРјРЅРµРЅРѕ</span>';
    setTimeout(() => { if (el.parentNode) el.remove(); }, 2000);
  } catch (e) { showToast('РћС€РёР±РєР° СЃРѕС…СЂР°РЅРµРЅРёСЏ С„Р°РєС‚Р°', true); }
}

function declineSuggestedFact(el) {
  el.remove();
}

// в”Ђв”Ђ PLAN SUGGESTION в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
async function runPlan(chatId, originalRequest) {
  try {
    const resp = await apiFetch(`${API}/api/run_plan/${chatId}`, {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify({ original_request: originalRequest, confirmed: true }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      showToast(data.detail || 'РћС€РёР±РєР° Р·Р°РїСѓСЃРєР° РїР»Р°РЅР°', true);
      return;
    }
    if (data.task_id) {
      const msgIndex = state.messages.length;
      state.messages.push({ role: 'assistant', loading: true, currentStep: 'Р—Р°РїСѓСЃРє РїР»Р°РЅР°...' });
      state.pendingTasks.push({ task_id: data.task_id, msgIndex });
      renderMessages();
      pollTask(data.task_id, msgIndex);
    }
  } catch (e) { showToast('РћС€РёР±РєР° Р·Р°РїСѓСЃРєР° РїР»Р°РЅР°', true); }
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
  state.messages.push({ role: 'assistant', loading: true, currentStep: 'РР РЈ РґСѓРјР°РµС‚...' });
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
    state.messages[msgIndex] = { role: 'assistant', content: 'РћС€РёР±РєР°: ' + (e.message || e) };
    renderMessages();
  }
}

// в”Ђв”Ђ INIT в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
// Mobile keyboard fix вЂ” resize app when virtual keyboard opens
if (window.visualViewport) {
  const resizeApp = () => {
    const app = document.querySelector('.app');
    if (!app) return;
    const vh = window.visualViewport.height;
    app.style.height = vh + 'px';
    // РџСЂРѕРєСЂСѓС‚РёС‚СЊ СЃС‚СЂР°РЅРёС†Сѓ РІРІРµСЂС…, С‡С‚РѕР±С‹ РєР»Р°РІРёР°С‚СѓСЂР° РЅРµ СЃРґРІРёРіР°Р»Р° viewport
    window.scrollTo(0, 0);
    document.documentElement.scrollTop = 0;
  };
  window.visualViewport.addEventListener('resize', resizeApp);
  window.visualViewport.addEventListener('scroll', resizeApp);
  // РўР°РєР¶Рµ РїСЂРё С„РѕРєСѓСЃРµ РЅР° input вЂ” РїСЂРѕРєСЂСѓС‚РёС‚СЊ Рє РЅРµРјСѓ
  document.addEventListener('focusin', (e) => {
    if (e.target.matches('.chat-input')) {
      setTimeout(() => {
        e.target.scrollIntoView({ block: 'end', behavior: 'smooth' });
        window.scrollTo(0, 0);
      }, 300);
    }
  });
}
