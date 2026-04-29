async function loadChats() {
  try {
    const r = await apiFetch(`${API}/api/chats`, { headers: authHeaders() });
    const data = await r.json();
    state.chats = data.chats || [];
    renderChatList();

    // Р•СЃР»Рё РµСЃС‚СЊ С‡Р°С‚С‹ Рё РЅРµС‚ Р°РєС‚РёРІРЅРѕРіРѕ вЂ” РѕС‚РєСЂС‹С‚СЊ РїРµСЂРІС‹Р№
    if (state.chats.length > 0 && !state.currentChatId) {
      openChat(state.chats[0].id);
    } else if (state.chats.length === 0) {
      state.currentChatId = null;
      state.messages = [];
      renderMessages();
      document.getElementById('headerTitle').textContent = 'РќРѕРІС‹Р№ С‡Р°С‚';
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
  } catch (e) { showToast('РћС€РёР±РєР°: ' + e.message, true); }
}

async function openChat(chatId) {
  state.currentChatId = chatId;
  if (window.innerWidth <= 768) closeMobileSidebar();
  renderChatList();

  const chat = state.chats.find(c => c.id === chatId);
  document.getElementById('headerTitle').textContent = chat ? chat.title : 'Р§Р°С‚';

  // Р—Р°РіСЂСѓР·РёС‚СЊ СЃРѕРѕР±С‰РµРЅРёСЏ
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
  } catch (e) { showToast('РћС€РёР±РєР°: ' + e.message, true); }
}

function renderChatList() {
  const list = document.getElementById('chatList');
  if (state.chats.length === 0) {
    list.innerHTML = '<div class="sidebar-empty">РќРµС‚ С‡Р°С‚РѕРІ</div>';
    return;
  }
  list.innerHTML = state.chats.map(c => {
    const active = c.id === state.currentChatId ? ' active' : '';
    return `<div class="chat-item${active}" onclick="openChat(${c.id})">
      <svg class="chat-item-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
      <span class="chat-item-text">${escapeHTML(c.title)}</span>
      <button class="chat-item-rename" onclick="startRenameChat(${c.id}, event)" title="РџРµСЂРµРёРјРµРЅРѕРІР°С‚СЊ">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M17 3a2.83 2.83 0 114 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>
      </button>
      <button class="chat-item-delete" onclick="deleteChat(${c.id}, event)" title="РЈРґР°Р»РёС‚СЊ">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>`;
  }).join('');
}

// в”Ђв”Ђ DEVICES в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

function renderMessages() {
  const container = document.getElementById('chatMessages');

  if (state.messages.length === 0) {
    const hasDevices = Object.keys(state.devices).length > 0;
    const subtitle = hasDevices
      ? 'РћРїРёС€Рё Р·Р°РґР°С‡Сѓ РЅР° РµСЃС‚РµСЃС‚РІРµРЅРЅРѕРј СЏР·С‹РєРµ вЂ” РР РЈ РІС‹РїРѕР»РЅРёС‚ РЅР° С‚РІРѕС‘Рј СѓСЃС‚СЂРѕР№СЃС‚РІРµ.'
      : 'РќРµС‚ РїРѕРґРєР»СЋС‡С‘РЅРЅС‹С… СѓСЃС‚СЂРѕР№СЃС‚РІ. РќР°РїРёС€Рё СЃРѕРѕР±С‰РµРЅРёРµ вЂ” СЏ РїРѕРјРѕРіСѓ РЅР°СЃС‚СЂРѕРёС‚СЊ РїРѕРґРєР»СЋС‡РµРЅРёРµ.';
    const hints = hasDevices
      ? `<div class="hint-chip" onclick="sendHint(this)">РћС‚РєСЂРѕР№ Р±СЂР°СѓР·РµСЂ</div>
          <div class="hint-chip" onclick="sendHint(this)">РџРѕРєР°Р¶Рё IP Р°РґСЂРµСЃ</div>
          <div class="hint-chip" onclick="sendHint(this)">РЎРІРѕР±РѕРґРЅРѕРµ РјРµСЃС‚Рѕ РЅР° РґРёСЃРєРµ</div>
          <div class="hint-chip" onclick="sendHint(this)">Р—Р°РїСѓС‰РµРЅРЅС‹Рµ РїСЂРѕС†РµСЃСЃС‹</div>`
      : `<div class="hint-chip" onclick="downloadAgent()">в¬‡ РЎРєР°С‡Р°С‚СЊ Р°РіРµРЅС‚</div>
          <div class="hint-chip" onclick="sendHint(this)">РљР°Рє РїРѕРґРєР»СЋС‡РёС‚СЊ РєРѕРјРїСЊСЋС‚РµСЂ?</div>
          <div class="hint-chip" onclick="sendHint(this)">Р§С‚Рѕ С‚С‹ СѓРјРµРµС€СЊ?</div>`;
    container.innerHTML = `
      <div class="chat-welcome">
        <img src="/static/IruIcon.ico" alt="РР РЈ">
        <h2>РР РЈ вЂ” РРЅС‚РµР»Р»РµРєС‚СѓР°Р»СЊРЅС‹Р№ Р РµР¶РёРј РЈРїСЂР°РІР»РµРЅРёСЏ</h2>
        <p>${subtitle}</p>
        <div class="hints">${hints}</div>
      </div>`;
    return;
  }

  let html = '';
  for (let mi = 0; mi < state.messages.length; mi++) {
    const m = state.messages[mi];
    const roleLabel = m.role === 'user' ? 'РІС‹' : 'РёСЂСѓ';
    let bodyHTML = linkify(escapeHTML(m.content || m.text || ''));

    // Р‘Р»РѕРє Р·Р°РґР°С‡ (РєРѕРЅРІРµР№РµСЂ)
    bodyHTML += renderTaskBlock(m.tasks);

    const commands = m.commands;
    if (commands && commands.length > 0) {
      bodyHTML += '<div class="cmd-log">';
      if (commands.length === 1) {
        // РћРґРЅР° РєРѕРјР°РЅРґР° вЂ” РѕР±С‹С‡РЅР°СЏ РїР»Р°С€РєР°, РЅРѕ СЃ stripUtfPrefix
        const c = commands[0];
        const stdout = c.result?.stdout || '';
        const stderr = c.result?.stderr || '';
        const errMsg = c.result?.error || '';
        const output = stdout || stderr || errMsg || '(РЅРµС‚ РІС‹РІРѕРґР°)';
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
        // Р“СЂСѓРїРїР° РєРѕРјР°РЅРґ вЂ” СЃРІС‘СЂРЅСѓС‚Р°СЏ РїР»Р°С€РєР°
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
              <span class="cmd-group-extra">(+${extra} РµС‰С‘)</span>
            </div>
            <div class="cmd-group-body">`;
        for (let i = 0; i < commands.length; i++) {
          const c = commands[i];
          const stdout = c.result?.stdout || '';
          const stderr = c.result?.stderr || '';
          const errMsg = c.result?.error || '';
          const output = stdout || stderr || errMsg || '(РЅРµС‚ РІС‹РІРѕРґР°)';
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
    // РљРЅРѕРїРєРё РїРѕРґС‚РІРµСЂР¶РґРµРЅРёСЏ
    let confirmBtns = '';
    if (m.confirmTaskId) {
      confirmBtns = `<div class="confirm-actions">
        <button class="btn-confirm-yes" onclick="confirmTask('${m.confirmTaskId}', ${mi})">\u2713 Р’С‹РїРѕР»РЅРёС‚СЊ</button>
        <button class="btn-confirm-no" onclick="denyTask('${m.confirmTaskId}', ${mi})">вњ— РћС‚РјРµРЅРёС‚СЊ</button>
      </div>`;
    }

    // Suggest memory block (Point 12)
    let suggestHTML = '';
    if (m.suggestedFact && m.suggestedFact.text) {
      const sf = m.suggestedFact;
      const tid = m._taskId || '';
      suggestHTML = `<div class="suggest-fact-block" id="sf-${mi}">
        <div class="suggest-fact-label">РР РЈ РїСЂРµРґР»Р°РіР°РµС‚ Р·Р°РїРѕРјРЅРёС‚СЊ:</div>
        <div class="suggest-fact-text">${escapeHTML(sf.text)}</div>
        <div class="suggest-fact-actions">
          <button class="suggest-fact-accept" onclick="acceptSuggestedFact('${tid}','${escapeAttr(sf.text)}','${escapeAttr(sf.category || '')}',document.getElementById('sf-${mi}'))">Р—Р°РїРѕРјРЅРёС‚СЊ</button>
          <button class="suggest-fact-decline" onclick="declineSuggestedFact(document.getElementById('sf-${mi}'))">РќРµ РЅР°РґРѕ</button>
        </div>
      </div>`;
    }

    // Plan suggestion banner
    let planHTML = '';
    // TODO: persist planDismissed/planDeclined РЅР° СЃРµСЂРІРµСЂРµ, С‡С‚РѕР±С‹ РїРѕСЃР»Рµ F5 РїР»Р°С€РєР° РЅРµ РІРѕР·РІСЂР°С‰Р°Р»Р°СЃСЊ
    if (m.planSuggestion && !m.planDismissed && !m.planDeclined) {
      if (m.planTrialUsed) {
        planHTML = `<div class="plan-suggest-block" id="ps-${mi}">
          <div class="plan-suggest-text" style="color:#888;">Р РµР¶РёРј РџР»Р°РЅ РґРѕСЃС‚СѓРїРµРЅ РЅР° Pro-С‚Р°СЂРёС„Рµ. Р’С‹ СѓР¶Рµ РёСЃРїРѕР»СЊР·РѕРІР°Р»Рё РїСЂРѕР±РЅС‹Р№ Р·Р°РїСѓСЃРє.</div>
        </div>`;
      } else {
        const desc = escapeHTML(m.planSuggestion);
        const origReq = escapeAttr(m.planOriginalRequest || '');
        planHTML = `<div class="plan-suggest-block" id="ps-${mi}" data-chat-id="${state.currentChatId}" data-orig-req="${origReq}">
          <div class="plan-suggest-text">Р—Р°РґР°С‡Р° РЅРµРїСЂРѕСЃС‚Р°СЏ: ${desc}. Р’ СЂРµР¶РёРјРµ РџР»Р°РЅ РР РЈ СЃРѕСЃС‚Р°РІРёС‚ Рё РІС‹РїРѕР»РЅРёС‚ РїРѕС€Р°РіРѕРІРѕРµ СЂРµС€РµРЅРёРµ.</div>
          <div class="plan-suggest-actions">
            <button class="plan-suggest-accept" onclick="acceptPlanSuggestion(document.getElementById('ps-${mi}'))">Р—Р°РїСѓСЃС‚РёС‚СЊ РїР»Р°РЅ</button>
            <button class="plan-suggest-decline" onclick="declinePlanSuggestion(document.getElementById('ps-${mi}'))">Р‘РµР· РїР»Р°РЅР°</button>
          </div>
          <div class="plan-suggest-warning" style="font-size:11px;color:#888;margin-top:6px;">РљРѕРјР°РЅРґС‹ РїР»Р°РЅР° Р±СѓРґСѓС‚ РІС‹РїРѕР»РЅРµРЅС‹ Р±РµР· РѕС‚РґРµР»СЊРЅРѕРіРѕ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёСЏ. РќР°Р¶РёРјР°Р№С‚Рµ, С‚РѕР»СЊРєРѕ РµСЃР»Рё РґРѕРІРµСЂСЏРµС‚Рµ Р·Р°РґР°С‡Рµ.</div>
        </div>`;
      }
    }

    if (m.loading) {
      const stepText = escapeHTML(m.currentStep || 'РР РЈ РґСѓРјР°РµС‚...');
      const liveTasksHTML = renderTaskBlock(m.liveTasks);
      const taskBlockAttr = (m.liveTasks && m.liveTasks.length > 0) ? '' : ' hidden';
      html += `<div class="msg assistant msg-thinking"><div class="msg-role">РёСЂСѓ</div><div class="msg-body"><div class="live-status"><span class="live-dot"></span><span class="live-text">${stepText}</span></div><div class="task-block-live"${taskBlockAttr}>${liveTasksHTML}</div></div></div>`;
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
    showToast(`РњР°РєСЃРёРјСѓРј ${MAX_INPUT_LENGTH} СЃРёРјРІРѕР»РѕРІ`, true);
    return;
  }
  const ids = Object.keys(state.devices);
  const isOnboarding = ids.length === 0;

  const messageToSend = buildMessageWithAttachments(text);

  input.value = '';
  autoGrow(input);
  clearAttachments();

  // Р”РѕР±Р°РІРёС‚СЊ СЃРѕРѕР±С‰РµРЅРёРµ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ РІ UI СЃСЂР°Р·Сѓ
  state.messages.push({ role: 'user', content: text });
  // Р”РѕР±Р°РІРёС‚СЊ placeholder РґР»СЏ РѕС‚РІРµС‚Р° (live-СЃС‚Р°С‚СѓСЃ РІРјРµСЃС‚Рѕ С‚РѕС‡РµРє Р·Р°РіСЂСѓР·РєРё)
  const msgIndex = state.messages.length;
  state.messages.push({ role: 'assistant', content: '', loading: true, currentStep: 'РР РЈ РґСѓРјР°РµС‚...', liveTasks: [] });
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
      // Р—Р°РґР°С‡Р° Р·Р°РїСѓС‰РµРЅР° РІ С„РѕРЅРµ вЂ” РЅР°С‡РёРЅР°РµРј polling
      state.pendingTasks.push({ task_id: data.task_id, msgIndex });
      pollTask(data.task_id, msgIndex);
    } else {
      // РћС€РёР±РєР° РґРѕ Р·Р°РїСѓСЃРєР° Р·Р°РґР°С‡Рё
      state.messages[msgIndex] = {
        role: 'assistant',
        content: `РћС€РёР±РєР°: ${data.error || 'РќРµРёР·РІРµСЃС‚РЅР°СЏ РѕС€РёР±РєР°'}`,
      };
      renderMessages();
    }
  } catch (e) {
    state.messages[msgIndex] = {
      role: 'assistant',
      content: `РћС€РёР±РєР° СЃРµС‚Рё: ${e.message}`,
    };
    renderMessages();
  }
}

async function pollTask(taskId, msgIndex) {
  const startTime = Date.now();
  const MAX_POLL_MS = 600000; // 10 РјРёРЅСѓС‚ РјР°РєСЃ (РґР»СЏ РґР»РёРЅРЅС‹С… РєРѕРЅРІРµР№РµСЂРѕРІ)
  let stopped = false;
  const poll = async () => {
    if (stopped) return;
    if (Date.now() - startTime > MAX_POLL_MS) {
      state.messages[msgIndex] = { role: 'assistant', content: 'РСЃС‚РµРєР»Рѕ РІСЂРµРјСЏ РѕР¶РёРґР°РЅРёСЏ РѕС‚РІРµС‚Р°.' };
      state.pendingTasks = state.pendingTasks.filter(t => t.task_id !== taskId);
      renderMessages();
      return;
    }
    try {
      const r = await apiFetch(`${API}/api/tasks/${taskId}`, { headers: authHeaders() });
      if (!r.ok) {
        stopped = true;
        state.messages[msgIndex] = { role: 'assistant', content: 'Р—Р°РґР°С‡Р° РЅРµ РЅР°Р№РґРµРЅР°.' };
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
          content: `РљРѕРјР°РЅРґР° С‚СЂРµР±СѓРµС‚ РїРѕРґС‚РІРµСЂР¶РґРµРЅРёСЏ:\n${cmdText}`,
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
          content: task.answer || 'Р“РѕС‚РѕРІРѕ.',
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
      // Р•С‰С‘ РІС‹РїРѕР»РЅСЏРµС‚СЃСЏ вЂ” РѕР±РЅРѕРІРёС‚СЊ live-СЃС‚Р°С‚СѓСЃ
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
      // РџРѕРІС‚РѕСЂРёС‚СЊ С‡РµСЂРµР· 800РјСЃ РїРѕРєР° Р·Р°РґР°С‡Р° running
      if (!stopped) setTimeout(poll, 800);
    } catch (e) {
      if (stopped) return;
      if (!poll._retries) poll._retries = 0;
      poll._retries++;
      if (poll._retries > 30) {
        stopped = true;
        state.messages[msgIndex] = { role: 'assistant', content: 'Р—Р°РґР°С‡Р° РЅРµ РЅР°Р№РґРµРЅР° РёР»Рё РёСЃС‚РµРєР»Р°.' };
        state.pendingTasks = state.pendingTasks.filter(t => t.task_id !== taskId);
        renderMessages();
        return;
      }
      setTimeout(poll, 2000);
    }
  };
  setTimeout(poll, 800);
}

// в”Ђв”Ђ INPUT DEVICE SELECTOR в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
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

// РљРЅРѕРїРєР° СЂРµР¶РёРјРѕРІ (РєРѕРЅРІРµР№РµСЂ / Р°РІС‚РѕРЅРѕРјРЅС‹Р№)
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
  if (state.modes.pipeline)   active.push('РџР»Р°РЅ');
  if (state.modes.autonomous) active.push('РђРІС‚Рѕ');
  btn.classList.toggle('active', active.length > 0);
  badges.textContent = active.join(' В· ');
  // РЎРёРЅС…СЂРѕРЅРёР·РёСЂСѓРµРј С‡РµРєР±РѕРєСЃС‹ СЃ state (РЅР° СЃР»СѓС‡Р°Р№ РІРЅРµС€РЅРµРіРѕ РёР·РјРµРЅРµРЅРёСЏ)
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
  input.placeholder = state.sendTarget === 'all' ? 'РћРїРёС€Рё Р·Р°РґР°С‡Сѓ (РІСЃРµ СѓСЃС‚СЂРѕР№СЃС‚РІР°)...' : 'РћРїРёС€Рё Р·Р°РґР°С‡Сѓ...';
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
    label.textContent = 'РќРµС‚ СѓСЃС‚СЂРѕР№СЃС‚РІ';
    dropdown.innerHTML = '<div class="input-device-dropdown-item" style="color:var(--text-muted);cursor:default">РћР¶РёРґР°РЅРёРµ РїРѕРґРєР»СЋС‡РµРЅРёСЏ...</div>';
    // РћРЅР±РѕСЂРґРёРЅРі-РїР»РµР№СЃС…РѕР»РґРµСЂ
    const chatInput = document.getElementById('chatInput');
    if (chatInput) chatInput.placeholder = 'РЎРїСЂРѕСЃРё, РєР°Рє РїРѕРґРєР»СЋС‡РёС‚СЊ СѓСЃС‚СЂРѕР№СЃС‚РІРѕ...';
    return;
  }

  // Current selection display
  if (state.sendTarget === 'all') {
    dot.className = 'dot all';
    dot.style.background = '';
    dot.style.boxShadow = '';
    label.textContent = 'Р’СЃРµ СѓСЃС‚СЂРѕР№СЃС‚РІР° (' + ids.length + ')';
  } else {
    dot.className = 'dot';
    dot.style.background = '';
    dot.style.boxShadow = '';
    const dev = state.devices[state.selectedDevice];
    label.textContent = dev ? (dev.info?.hostname || state.selectedDevice) : 'Р’С‹Р±РµСЂРёС‚Рµ';
  }

  // Dropdown items
  let html = '';
  // "All devices" option
  const allSel = state.sendTarget === 'all' ? ' selected' : '';
  html += `<div class="input-device-dropdown-item${allSel}" onclick="selectInputDevice('all')">
    <span class="dot all" style="width:5px;height:5px;border-radius:50%;background:var(--accent);box-shadow:0 0 4px var(--accent)"></span>
    <div>Р’СЃРµ СѓСЃС‚СЂРѕР№СЃС‚РІР° (${ids.length})</div>
  </div>`;
  // Individual devices
  for (const id of ids) {
    const d = state.devices[id];
    const sel = (state.sendTarget === 'single' && id === state.selectedDevice) ? ' selected' : '';
    const info = d.info || {};
    html += `<div class="input-device-dropdown-item${sel}" onclick="selectInputDevice('single','${id}')">
      <span style="width:5px;height:5px;border-radius:50%;background:var(--success);box-shadow:0 0 4px var(--success);flex-shrink:0"></span>
      <div><div>${info.hostname || id}</div><div class="dev-os">${info.os || '?'} вЂ” ${id}</div></div>
    </div>`;
  }
  dropdown.innerHTML = html;
}

// в”Ђв”Ђ LIVE PROGRESS в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
function renderTaskBlock(tasks) {
  if (!tasks || tasks.length === 0) return '';
  let html = '';
  for (const t of tasks) {
    const st = t.status || 'running';
    const statusLabel = st === 'completed' ? 'Р·Р°РІРµСЂС€РµРЅРѕ'
      : st === 'failed' ? 'РѕС€РёР±РєР°'
      : st === 'cancelled' ? 'РѕС‚РјРµРЅРµРЅРѕ'
      : 'РІС‹РїРѕР»РЅСЏРµС‚СЃСЏ';
    html += `<div class="task-block task-${st}">`;
    html += `<div class="task-goal"><span class="task-goal-label">Р—Р°РґР°С‡Р°:</span> ${escapeHTML(t.goal || '')} <span class="task-badge task-badge-${st}">${statusLabel}</span></div>`;
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
  // РќР• Р·Р°РґР°С‘Рј a.download вЂ” Р±СЂР°СѓР·РµСЂ РІРѕР·СЊРјС‘С‚ РёРјСЏ РёР· Content-Disposition СЃРµСЂРІРµСЂР°.
  // Р­С‚Рѕ СЂР°Р±РѕС‚Р°РµС‚ Рё РґР»СЏ IruAgent.zip, Рё РґР»СЏ IruAgent.exe.
  const a = document.createElement('a');
  a.href = `${API}/api/agent/download`;
  document.body.appendChild(a);
  a.click();
  a.remove();
}
// в”Ђв”Ђ CONFIRM / DENY в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
async function confirmTask(taskId, msgIndex) {
  try {
    await apiFetch(`${API}/api/tasks/${taskId}/confirm`, {
      method: 'POST', headers: authHeaders(),
    });
    // РЈР±РёСЂР°РµРј РєРЅРѕРїРєРё, РїРѕРєР°Р·С‹РІР°РµРј Р»РѕР°РґРµСЂ
    state.messages[msgIndex].confirmTaskId = null;
    state.messages[msgIndex].loading = true;
    state.messages[msgIndex].content = '';
    renderMessages();
    // РџРѕР»Р»РёРј Р·Р°РґР°С‡Сѓ РґРѕ Р·Р°РІРµСЂС€РµРЅРёСЏ
    pollTask(taskId, msgIndex);
  } catch (e) { showToast('РћС€РёР±РєР° РїРѕРґС‚РІРµСЂР¶РґРµРЅРёСЏ', true); }
}

async function denyTask(taskId, msgIndex) {
  try {
    await apiFetch(`${API}/api/tasks/${taskId}/deny`, {
      method: 'POST', headers: authHeaders(),
    });
    state.messages[msgIndex].confirmTaskId = null;
    state.messages[msgIndex].content = 'РљРѕРјР°РЅРґР° РѕС‚РјРµРЅРµРЅР°.';
    state.pendingTasks = state.pendingTasks.filter(t => t.task_id !== taskId);
    renderMessages();
  } catch (e) { showToast('РћС€РёР±РєР°', true); }
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

