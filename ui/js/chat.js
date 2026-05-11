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
    return `<div class="chat-item${active}" data-action="open-chat" data-chat-id="${escapeAttr(c.id)}">
      <svg class="chat-item-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
      <span class="chat-item-text">${escapeHTML(c.title)}</span>
      <button class="chat-item-rename" data-action="rename-chat" data-chat-id="${escapeAttr(c.id)}" title="Переименовать">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M17 3a2.83 2.83 0 114 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>
      </button>
      <button class="chat-item-delete" data-action="delete-chat" data-chat-id="${escapeAttr(c.id)}" title="Удалить">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>`;
  }).join('');
}

function bindChatListActions() {
  const list = document.getElementById('chatList');
  if (!list || list.dataset.delegated === '1') return;
  list.dataset.delegated = '1';
  list.addEventListener('click', (event) => {
    const target = event.target.closest('[data-action]');
    if (!target || !list.contains(target)) return;
    const chatId = Number(target.dataset.chatId);
    if (!chatId) return;
    if (target.dataset.action === 'open-chat') openChat(chatId);
    if (target.dataset.action === 'rename-chat') startRenameChat(chatId, event);
    if (target.dataset.action === 'delete-chat') deleteChat(chatId, event);
  });
}

// ── DEVICES ──────────────────────────────────────────

function buildMessageDownloadMap(commands) {
  const map = new Map();
  if (!commands || !commands.length) return map;

  for (const cmd of commands) {
    const result = cmd?.result;
    const url = result?.url;
    const filePath = result?.file_path;
    const deviceId = cmd?.device_id;
    if (!url || !filePath || !deviceId) continue;
    map.set(url, { deviceId, filePath });
  }

  return map;
}

function linkifyMessageContent(text, commands) {
  const safeText = escapeHTML(text || '');
  const downloadMap = buildMessageDownloadMap(commands);
  const usedDownloads = new Set();

  const html = safeText.replace(/(\/api\/download\/[a-f0-9-]+)/g, (match) => {
    const meta = downloadMap.get(match);
    if (!meta) {
      return `<a href="${escapeAttr(match)}" rel="noopener noreferrer" download>Скачать файл</a>`;
    }

    usedDownloads.add(`${meta.deviceId}::${meta.filePath}`);
    const deviceId = encodeURIComponent(meta.deviceId);
    const filePath = encodeURIComponent(meta.filePath);
    return `<button class="msg-download-link" data-action="download-message-file" data-device-id="${escapeAttr(deviceId)}" data-file-path="${escapeAttr(filePath)}">Скачать файл</button>`;
  });

  return { html, usedDownloads };
}

function renderCommandDownloadButtons(commands, usedDownloads = new Set()) {
  if (!commands || !commands.length) return '';

  const seen = new Set();
  const buttons = [];

  for (const cmd of commands) {
    const result = cmd?.result;
    const filePath = result?.file_path;
    const deviceId = cmd?.device_id;
    if (!filePath || !deviceId) continue;

    const key = `${deviceId}::${filePath}`;
    if (seen.has(key) || usedDownloads.has(key)) continue;
    seen.add(key);

    const encodedDeviceId = encodeURIComponent(deviceId);
    const encodedFilePath = encodeURIComponent(filePath);
    const label = filePath.split(/[\\\\/]/).pop() || 'Скачать файл';
    buttons.push(
      `<button class="msg-download-link" data-action="download-message-file" data-device-id="${escapeAttr(encodedDeviceId)}" data-file-path="${escapeAttr(encodedFilePath)}">Скачать: ${escapeHTML(label)}</button>`
    );
  }

  if (!buttons.length) return '';
  return `<div class="msg-download-actions">${buttons.join('')}</div>`;
}

async function downloadMessageFile(deviceIdEncoded, filePathEncoded, btn) {
  const deviceId = decodeURIComponent(deviceIdEncoded);
  const filePath = decodeURIComponent(filePathEncoded);
  const button = btn || null;
  const originalText = button ? button.textContent : '';

  if (button) {
    button.disabled = true;
    button.textContent = '...';
  }

  try {
    const response = await apiFetch(`${API}/api/download_request`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ device_id: deviceId, file_path: filePath }),
    });
    const data = await response.json();

    if (!response.ok || data.status !== 'ok' || !data.url) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }

    const anchor = document.createElement('a');
    anchor.href = data.url;
    anchor.download = '';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } catch (e) {
    showToast(e.message || 'Download failed', true);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = originalText || 'Скачать файл';
    }
  }
}

function renderMessages() {
  const container = document.getElementById('chatMessages');

  if (state.messages.length === 0) {
    const hasDevices = Object.keys(state.devices).length > 0;
    const subtitle = hasDevices
      ? 'Опиши задачу на естественном языке — ИРУ выполнит на твоём устройстве.'
      : 'Нет подключённых устройств. Напиши сообщение — я помогу настроить подключение.';
    const hints = hasDevices
      ? `<div class="hint-chip" data-action="send-hint">Открой браузер</div>
          <div class="hint-chip" data-action="send-hint">Покажи IP адрес</div>
          <div class="hint-chip" data-action="send-hint">Свободное место на диске</div>
          <div class="hint-chip" data-action="send-hint">Запущенные процессы</div>`
      : `<div class="hint-chip" data-action="download-agent">⬇ Скачать агент</div>
          <div class="hint-chip" data-action="send-hint">Как подключить компьютер?</div>
          <div class="hint-chip" data-action="send-hint">Что ты умеешь?</div>`;
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
    const linkified = linkifyMessageContent(m.content || m.text || '', m.commands || []);
    let bodyHTML = linkified.html;

    // Блок задач (конвейер)
    bodyHTML += renderTaskBlock(m.tasks);

    const commands = m.commands;
    bodyHTML += renderCommandDownloadButtons(commands, linkified.usedDownloads);
    if (commands && commands.length > 0) {
      bodyHTML += '<div class="cmd-log">';
      if (commands.length === 1) {
        // Одна команда — обычная плашка, но с stripUtfPrefix
        const c = commands[0];
        const stdout = c.result?.stdout || '';
        const stderr = c.result?.stderr || '';
        const errMsg = c.result?.error || '';
        const output = stdout || stderr || errMsg || '(нет вывода)';
        const isBudgetStop = c.action === 'budget_guard' || stripUtfPrefix(c.command || '') === '[budget_guard]';
        const isOk = !isBudgetStop && !errMsg && (c.result?.returncode === 0 || c.result?.returncode == null);
        const statusCls = isBudgetStop ? 'stopped' : (isOk ? 'ok' : 'err');
        const statusTxt = isBudgetStop ? '\u25a0' : (isOk ? '\u2713' : '\u2717');
        const deviceTag = c.device_id ? `<span class="cmd-device">${escapeHTML(c.device_id)}</span>` : '';
        const cmdText = escapeHTML(stripUtfPrefix(c.command || ''));
        const entryClass = isBudgetStop ? 'cmd-entry cmd-entry-budget' : 'cmd-entry';
        bodyHTML += `
          <div class="${entryClass}" data-action="toggle-cmd-entry">
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
        const hasBudgetStop = commands.some((cmd) => cmd?.action === 'budget_guard' || stripUtfPrefix(cmd.command || '') === '[budget_guard]');
        const lastIsOk = !hasBudgetStop && !(lastCmd.result?.error) && (lastCmd.result?.returncode === 0 || lastCmd.result?.returncode == null);
        const lastStatusCls = hasBudgetStop ? 'stopped' : (lastIsOk ? 'ok' : 'err');
        const lastStatusTxt = hasBudgetStop ? '\u25a0' : (lastIsOk ? '\u2713' : '\u2717');
        const lastDevice = lastCmd.device_id ? `<span class="cmd-device">${escapeHTML(lastCmd.device_id)}</span>` : '';
        const extra = commands.length - 1;
        const groupClass = hasBudgetStop ? 'cmd-group cmd-group-budget' : 'cmd-group';
        bodyHTML += `
          <div class="${groupClass}" id="${groupId}">
            <div class="cmd-group-header" data-action="toggle-cmd-group">
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
          const isBudgetStop = c.action === 'budget_guard' || stripUtfPrefix(c.command || '') === '[budget_guard]';
          const isOk = !isBudgetStop && !errMsg && (c.result?.returncode === 0 || c.result?.returncode == null);
          const statusCls = isBudgetStop ? 'stopped' : (isOk ? 'ok' : 'err');
          const statusTxt = isBudgetStop ? '\u25a0' : (isOk ? '\u2713' : '\u2717');
          const deviceTag = c.device_id ? `<span class="cmd-device">${escapeHTML(c.device_id)}</span>` : '';
          const cmdText = escapeHTML(stripUtfPrefix(c.command || ''));
          const entryClass = isBudgetStop ? 'cmd-entry cmd-entry-budget' : 'cmd-entry';
          bodyHTML += `
              <div class="${entryClass}" data-action="toggle-cmd-entry">
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
        <button class="btn-confirm-yes" data-action="confirm-task" data-task-id="${escapeAttr(m.confirmTaskId)}" data-index="${mi}">\u2713 Выполнить</button>
        <button class="btn-confirm-no" data-action="deny-task" data-task-id="${escapeAttr(m.confirmTaskId)}" data-index="${mi}">✗ Отменить</button>
      </div>`;
    }

    // Suggest memory block (Point 12)
    let suggestHTML = '';
    if (m.suggestedFact && m.suggestedFact.text && !m.suggestedFactDeclined) {
      const sf = m.suggestedFact;
      suggestHTML = `<div class="suggest-fact-block" id="sf-${mi}">
        <div class="suggest-fact-label">ИРУ предлагает запомнить:</div>
        <div class="suggest-fact-text">${escapeHTML(sf.text)}</div>
        <div class="suggest-fact-actions">
          <button class="suggest-fact-accept" data-action="accept-suggested-fact" data-index="${mi}">Запомнить</button>
          <button class="suggest-fact-decline" data-action="decline-suggested-fact" data-index="${mi}">Не надо</button>
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
        planHTML = `<div class="plan-suggest-block" id="ps-${mi}" data-chat-id="${escapeAttr(state.currentChatId)}" data-index="${mi}">
          <div class="plan-suggest-text">Задача непростая: ${desc}. В режиме План ИРУ составит и выполнит пошаговое решение.</div>
          <div class="plan-suggest-actions">
            <button class="plan-suggest-accept" data-action="accept-plan-suggestion" data-index="${mi}">Запустить план</button>
            <button class="plan-suggest-decline" data-action="decline-plan-suggestion" data-index="${mi}">Без плана</button>
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

function bindChatMessageActions() {
  const container = document.getElementById('chatMessages');
  if (!container || container.dataset.delegated === '1') return;
  container.dataset.delegated = '1';
  container.addEventListener('click', (event) => {
    const target = event.target.closest('[data-action]');
    if (!target || !container.contains(target)) return;
    const action = target.dataset.action;

    if (action === 'download-message-file') {
      downloadMessageFile(target.dataset.deviceId || '', target.dataset.filePath || '', target);
      return;
    }
    if (action === 'send-hint') {
      sendHint(target);
      return;
    }
    if (action === 'download-agent') {
      downloadAgent();
      return;
    }
    if (action === 'toggle-cmd-entry') {
      target.classList.toggle('open');
      return;
    }
    if (action === 'toggle-cmd-group') {
      const group = target.closest('.cmd-group');
      if (group) group.classList.toggle('open');
      return;
    }
    if (action === 'toggle-step-details') {
      const stepEl = target.closest('.task-step');
      if (!stepEl) return;
      const willOpen = !stepEl.classList.contains('open');
      stepEl.classList.toggle('open', willOpen);
      target.setAttribute('aria-expanded', String(willOpen));
      const arrow = target.querySelector('.step-details-arrow');
      if (arrow) arrow.textContent = willOpen ? '\u25be' : '\u25b8';
      const details = stepEl.querySelector('.step-details');
      if (details) details.hidden = !willOpen;
      const key = target.dataset.stepKey || stepEl.dataset.stepKey;
      if (key && state.expandedStepDetails) {
        if (willOpen) state.expandedStepDetails.add(key);
        else state.expandedStepDetails.delete(key);
      }
      return;
    }
    if (action === 'confirm-task' || action === 'deny-task') {
      const msgIndex = Number(target.dataset.index);
      const taskId = target.dataset.taskId || '';
      if (!taskId || Number.isNaN(msgIndex)) return;
      if (action === 'confirm-task') confirmTask(taskId, msgIndex);
      else denyTask(taskId, msgIndex);
      return;
    }
    if (action === 'accept-suggested-fact' || action === 'decline-suggested-fact') {
      const msgIndex = Number(target.dataset.index);
      const block = document.getElementById(`sf-${msgIndex}`);
      if (!block || Number.isNaN(msgIndex)) return;
      if (action === 'decline-suggested-fact') {
        const message = state.messages[msgIndex] || {};
        declineSuggestedFact(message._taskId || '', block, msgIndex);
        return;
      }
      const message = state.messages[msgIndex] || {};
      const fact = message.suggestedFact || {};
      acceptSuggestedFact(message._taskId || '', fact.text || '', fact.category || '', block, msgIndex);
      return;
    }
    if (action === 'accept-plan-suggestion' || action === 'decline-plan-suggestion') {
      const msgIndex = Number(target.dataset.index);
      const block = document.getElementById(`ps-${msgIndex}`);
      if (!block) return;
      if (action === 'accept-plan-suggestion') acceptPlanSuggestion(block);
      else declinePlanSuggestion(block);
    }
  });
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

// ── LIVE PROGRESS ─────────────────────────────────────
function getTaskStepKey(task, step, taskIndex, stepIndex) {
  const taskKey = task.id || task.task_id || task.taskId || `task-${taskIndex}`;
  const stepKey = step.id || step.step_id || step.index || step.idx || stepIndex;
  return `${taskKey}:${stepKey}`;
}

function getStepDetailText(value) {
  if (value == null || value === '') return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function renderStepDetailSection(label, value) {
  const text = getStepDetailText(value);
  if (!text) return '';
  return `<div class="step-detail-section"><div class="step-detail-label">${label}</div><div class="step-detail-text">${escapeHTML(text)}</div></div>`;
}

function renderStepDetails(step) {
  const sections = [
    renderStepDetailSection('Summary', step.summary),
    renderStepDetailSection('Error', step.error),
    renderStepDetailSection('Details', step.detail || step.details || step.raw_detail || step.raw),
  ].filter(Boolean);
  return sections.join('');
}

function renderTaskBlock(tasks) {
  if (!tasks || tasks.length === 0) return '';
  let html = '';
  for (let ti = 0; ti < tasks.length; ti++) {
    const t = tasks[ti];
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
      for (let si = 0; si < steps.length; si++) {
        const s = steps[si];
        const sst = s.status || 'pending';
        const icon = sst === 'done' ? '\u2713'
          : sst === 'failed' ? '\u2717'
          : sst === 'running' ? '\u23f3'
          : sst === 'skipped' ? '\u2014'
          : '\u25cb';
        const title = s.title || s.description || `Step ${si + 1}`;
        const description = s.title && s.description && s.description !== s.title
          ? `<span class="step-subdesc">${escapeHTML(s.description)}</span>`
          : '';
        const detailsHTML = renderStepDetails(s);
        const hasDetails = detailsHTML.length > 0;
        const stepKey = getTaskStepKey(t, s, ti, si);
        const isOpen = hasDetails && state.expandedStepDetails && state.expandedStepDetails.has(stepKey);
        const toggle = hasDetails
          ? `<button type="button" class="step-details-toggle" data-action="toggle-step-details" data-step-key="${escapeAttr(stepKey)}" aria-expanded="${isOpen ? 'true' : 'false'}" title="Toggle step details"><span class="step-details-arrow">${isOpen ? '\u25be' : '\u25b8'}</span></button>`
          : '';
        html += `<li class="task-step step-${sst}${isOpen ? ' open' : ''}" data-step-key="${escapeAttr(stepKey)}"><div class="step-row"><span class="step-icon">${icon}</span><span class="step-desc">${escapeHTML(title)}${description}</span><span class="step-status">${escapeHTML(sst)}</span>${toggle}</div>${hasDetails ? `<div class="step-details"${isOpen ? '' : ' hidden'}>${detailsHTML}</div>` : ''}</li>`;
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

bindChatListActions();
bindChatMessageActions();

