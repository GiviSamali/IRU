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
    restoreActiveChatTasks(chatId);
  } catch (e) {
    state.messages = [];
    renderMessages();
    restoreActiveChatTasks(chatId);
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

const SAFE_TASK_STATUS_LABELS = Object.freeze({
  thinking: 'ИРУ думает...',
  running: 'Выполняю задачу...',
  running_tool: 'Выполняю инструмент...',
  waiting_agent: 'Жду ответ агента...',
  preparing_runtime: 'Подготавливаю runtime...',
  refreshing_state: 'Обновляю состояние устройства...',
  writing_file: 'Создаю файл...',
  launching_app: 'Запускаю приложение...',
  restoring: 'Восстанавливаю статус операции...',
  cancelling: 'Остановка запрошена...',
  cancelled: 'Остановлено пользователем',
  done: 'Готово',
  failed: 'Ошибка',
});

const SAFE_TASK_STATUS_KEYS = new Set(Object.keys(SAFE_TASK_STATUS_LABELS));

function normalizeTaskStatusKey(status) {
  const key = String(status || '').trim().toLowerCase();
  return SAFE_TASK_STATUS_KEYS.has(key) ? key : '';
}

function normalizeTaskStatusLabel(status) {
  return SAFE_TASK_STATUS_LABELS[normalizeTaskStatusKey(status)] || SAFE_TASK_STATUS_LABELS.running;
}

function deriveLiveTaskStatus(task, currentMessage) {
  const explicitStatus = task?.current_status ?? task?.currentStatus;
  if (explicitStatus != null) {
    return normalizeTaskStatusKey(explicitStatus) || 'running';
  }

  const taskStatus = String(task?.status || '').trim().toLowerCase();
  if (taskStatus === 'done' || taskStatus === 'completed' || taskStatus === 'completed_with_recovery') return 'done';
  if (taskStatus === 'cancelling') return 'cancelling';
  if (taskStatus === 'cancelled') return 'cancelled';
  if (taskStatus === 'error' || taskStatus === 'failed') return 'failed';
  if (taskStatus === 'confirm') return 'waiting_agent';
  if (taskStatus && taskStatus !== 'running' && taskStatus !== 'pending') return 'running';

  const commands = Array.isArray(task?.commands) ? task.commands : [];
  if (commands.some(command => getCommandStatus(command) === 'running')) return 'running_tool';

  const tasks = Array.isArray(task?.tasks) ? task.tasks : [];
  const hasRunningStep = tasks.some(item => (item.steps || []).some(step => normalizeStepStateKey(step?.status) === 'running'));
  if (hasRunningStep) return 'running_tool';

  return normalizeTaskStatusKey(currentMessage?.currentStatus) || 'thinking';
}

const TERMINAL_TASK_STATUSES = new Set([
  'done',
  'error',
  'completed',
  'completed_with_recovery',
  'failed',
  'cancelled',
  'blocked',
]);

function isTaskTerminalStatus(status) {
  return TERMINAL_TASK_STATUSES.has(String(status || '').trim().toLowerCase());
}

function getActivePendingTask() {
  if (!Array.isArray(state.pendingTasks) || state.pendingTasks.length === 0) return null;
  for (let i = state.pendingTasks.length - 1; i >= 0; i--) {
    const pending = state.pendingTasks[i];
    if (!pending?.task_id) continue;
    const msg = state.messages[pending.msgIndex];
    if (msg && msg.currentStatus === 'cancelled') continue;
    return pending;
  }
  return null;
}

function updateStopButton() {
  const btn = document.getElementById('btnStopTask');
  if (!btn) return;
  const active = getActivePendingTask();
  if (!active) {
    btn.hidden = true;
    btn.disabled = true;
    btn.textContent = 'Стоп';
    return;
  }
  btn.hidden = false;
  btn.disabled = Boolean(active.cancelRequested);
  btn.textContent = active.cancelRequested ? 'Остановка...' : 'Стоп';
}

async function cancelActiveTask() {
  const active = getActivePendingTask();
  if (!active?.task_id || active.cancelRequested) return;
  active.cancelRequested = true;
  const msg = state.messages[active.msgIndex];
  const wasConfirm = Boolean(msg?.confirmTaskId);
  if (msg) {
    msg.loading = true;
    msg.currentStatus = 'cancelling';
    msg.cancelRequested = true;
  }
  renderMessages();
  updateStopButton();

  try {
    const resp = await apiFetch(`${API}/api/tasks/${encodeURIComponent(active.task_id)}/cancel`, {
      method: 'POST',
      headers: authHeaders(),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.status !== 'ok') {
      throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
    }
    showToast(data.message || 'Остановка запрошена. Текущий инструмент может завершиться с задержкой.');
    if (wasConfirm) pollTask(active.task_id, active.msgIndex);
  } catch (e) {
    active.cancelRequested = false;
    if (msg) {
      msg.currentStatus = 'running';
      msg.cancelRequested = false;
    }
    updateStopButton();
    renderMessages();
    showToast(e.message || 'Не удалось запросить остановку', true);
  }
}

const SAFE_TASK_STATE_CLASSES = new Set([
  'pending',
  'running',
  'cancelling',
  'completed',
  'completed_with_recovery',
  'done',
  'recovered',
  'failed',
  'error',
  'cancelled',
  'blocked',
  'skipped',
]);

function normalizeTaskStateKey(status, fallback = 'running') {
  const key = String(status || '').trim().toLowerCase();
  return SAFE_TASK_STATE_CLASSES.has(key) ? key : fallback;
}

function normalizeTaskBadgeLabel(status) {
  const key = normalizeTaskStateKey(status);
  if (key === 'completed' || key === 'done') return 'завершено';
  if (key === 'completed_with_recovery' || key === 'recovered') return 'завершено с recovery';
  if (key === 'failed' || key === 'error') return 'ошибка';
  if (key === 'cancelling') return 'остановка';
  if (key === 'cancelled') return 'отменено';
  if (key === 'blocked') return 'заблокировано';
  if (key === 'skipped') return 'пропущено';
  if (key === 'pending') return 'ожидает';
  return 'выполняется';
}

function normalizeStepStateKey(status) {
  return normalizeTaskStateKey(status, 'pending');
}

function normalizeStepStatusLabel(status) {
  const key = normalizeStepStateKey(status);
  if (key === 'done' || key === 'completed') return 'готово';
  if (key === 'recovered' || key === 'completed_with_recovery') return 'исправлено';
  if (key === 'failed' || key === 'error') return 'ошибка';
  if (key === 'running') return 'выполняется';
  if (key === 'blocked') return 'блокировано';
  if (key === 'skipped') return 'пропущено';
  if (key === 'cancelled') return 'отменено';
  return 'ожидает';
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
    updateStopButton();
    return;
  }

  let html = '';
  for (let mi = 0; mi < state.messages.length; mi++) {
    const m = state.messages[mi];
    if (m.hideAfterPlanChoice) continue;
    const roleLabel = m.role === 'user' ? 'вы' : 'иру';
    const linkified = linkifyMessageContent(m.content || m.text || '', m.commands || []);
    let bodyHTML = linkified.html;

    // Блок задач (конвейер)
    bodyHTML += renderTaskBlock(m.tasks, m.commands || [], m._taskId || `msg-${mi}`);

    const commands = m.commands;
    bodyHTML += renderUsedToolsLine(commands);
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
        const cmdText = escapeHTML(getCommandDisplayText(c));
        const detailsText = getCommandDetailsText(c, output);
        const entryClass = isBudgetStop ? 'cmd-entry cmd-entry-budget' : 'cmd-entry';
        bodyHTML += `
          <div class="${entryClass}" data-action="toggle-cmd-entry">
            <div class="cmd-summary">
              <span class="cmd-icon">\u25b8</span>
              <span class="cmd-text">${cmdText}</span>
              ${deviceTag}
              <span class="cmd-status ${statusCls}">${statusTxt}</span>
            </div>
            <div class="cmd-details">${escapeHTML(detailsText)}</div>
          </div>`;
      } else {
        // Группа команд — свёрнутая плашка
        const groupId = 'cmdgrp-' + mi;
        const lastCmd = commands[commands.length - 1];
        const lastClean = getCommandDisplayText(lastCmd);
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
          const cmdText = escapeHTML(getCommandDisplayText(c));
          const detailsText = getCommandDetailsText(c, output);
          const entryClass = isBudgetStop ? 'cmd-entry cmd-entry-budget' : 'cmd-entry';
          bodyHTML += `
              <div class="${entryClass}" data-action="toggle-cmd-entry">
                <div class="cmd-summary">
                  <span class="cmd-icon">\u25b8</span>
                  <span class="cmd-text">${cmdText}</span>
                  ${deviceTag}
                  <span class="cmd-status ${statusCls}">${statusTxt}</span>
                </div>
                <div class="cmd-details">${escapeHTML(detailsText)}</div>
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
      const stepText = escapeHTML(normalizeTaskStatusLabel(m.currentStatus || 'thinking'));
      const liveTasksHTML = renderTaskBlock(m.liveTasks, m.liveCommands || [], m._taskId || `live-${mi}`);
      const taskBlockAttr = liveTasksHTML ? '' : ' hidden';
      html += `<div class="msg assistant msg-thinking"><div class="msg-role">иру</div><div class="msg-body"><div class="live-status"><span class="live-dot"></span><span class="live-text">${stepText}</span></div><div class="task-block-live"${taskBlockAttr}>${liveTasksHTML}</div></div></div>`;
    } else {
      html += `<div class="msg ${m.role}"><div class="msg-role">${roleLabel}</div><div class="msg-body">${bodyHTML}${confirmBtns}${suggestHTML}${planHTML}</div></div>`;
    }
  }

  container.innerHTML = html;
  container.scrollTop = container.scrollHeight;
  updateStopButton();
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
    if (action === 'toggle-step-commands') {
      const stepEl = target.closest('.task-step');
      if (!stepEl) return;
      const taskId = target.dataset.taskId || stepEl.dataset.taskId || '';
      const stepIndex = target.dataset.stepIndex || stepEl.dataset.stepIndex || '';
      const key = `${taskId}:${stepIndex}`;
      const willOpen = !stepEl.classList.contains('open');
      stepEl.classList.toggle('open', willOpen);
      target.setAttribute('aria-expanded', String(willOpen));
      const arrow = target.querySelector('.step-details-arrow');
      if (arrow) arrow.textContent = willOpen ? '\u25be' : '\u25b8';
      const details = stepEl.querySelector('.step-details');
      if (details) details.hidden = !willOpen;
      if (state.expandedStepCommands) {
        if (willOpen) state.expandedStepCommands.add(key);
        else state.expandedStepCommands.delete(key);
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
  state.messages.push({ role: 'assistant', content: '', loading: true, currentStatus: 'thinking', liveTasks: [], liveCommands: [] });
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
      state.messages[msgIndex]._taskId = data.task_id;
      state.pendingTasks.push({ task_id: data.task_id, msgIndex });
      updateStopButton();
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
  rememberActiveTask(taskId, state.currentChatId);
  if (state.messages[msgIndex]) state.messages[msgIndex]._taskId = taskId;
  updateStopButton();
  const poll = async () => {
    if (stopped) return;
    if (Date.now() - startTime > MAX_POLL_MS) {
      state.messages[msgIndex] = { role: 'assistant', content: 'Истекло время ожидания ответа.' };
      state.pendingTasks = state.pendingTasks.filter(t => t.task_id !== taskId);
      forgetActiveTask(taskId);
      renderMessages();
      return;
    }
    try {
      const r = await apiFetch(`${API}/api/tasks/${taskId}`, { headers: authHeaders() });
      if (!r.ok) {
        stopped = true;
        state.messages[msgIndex] = { role: 'assistant', content: 'Задача не найдена.' };
        state.pendingTasks = state.pendingTasks.filter(t => t.task_id !== taskId);
        forgetActiveTask(taskId);
        renderMessages();
        return;
      }
      const data = await r.json();
      const task = data.task;
      const pendingTask = state.pendingTasks.find(t => t.task_id === taskId);
      if (pendingTask && String(task.status || '').trim().toLowerCase() === 'cancelling') {
        pendingTask.cancelRequested = true;
      }

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
          _taskId: taskId,
        };
        renderMessages();
        return;
      }
      if (isTaskTerminalStatus(task.status)) {
        stopped = true;
        const isCancelled = String(task.status || '').trim().toLowerCase() === 'cancelled';
        const fallbackAnswer = isCancelled ? 'Остановлено пользователем.' : (task.plan_suggestion ? '' : 'ИРУ завершила задачу без текстового ответа.');
        const msg = {
          role: 'assistant',
          content: task.answer || fallbackAnswer,
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
        forgetActiveTask(taskId);
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
        const liveStatus = deriveLiveTaskStatus(task, msg);
        if (msg.currentStatus !== liveStatus) {
          msg.currentStatus = liveStatus;
          needRender = true;
        }
        if (task.tasks && task.tasks.length > 0) {
          msg.liveTasks = task.tasks;
          needRender = true;
        }
        if (task.commands) {
          msg.liveCommands = task.commands;
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
        forgetActiveTask(taskId);
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

const ACTIVE_TASKS_STORAGE_KEY = 'iru_active_tasks';

function readActiveTasks() {
  try {
    return JSON.parse(sessionStorage.getItem(ACTIVE_TASKS_STORAGE_KEY) || '[]');
  } catch {
    return [];
  }
}

function writeActiveTasks(items) {
  try {
    sessionStorage.setItem(ACTIVE_TASKS_STORAGE_KEY, JSON.stringify(items));
  } catch {}
}

function rememberActiveTask(taskId, chatId) {
  if (!taskId || !chatId) return;
  const items = readActiveTasks().filter(item => item && item.taskId);
  if (items.some(item => item.taskId === taskId)) return;
  items.push({ taskId, chatId });
  writeActiveTasks(items);
}

function forgetActiveTask(taskId) {
  if (!taskId) return;
  writeActiveTasks(readActiveTasks().filter(item => item && item.taskId !== taskId));
}

function restoreActiveChatTasks(chatId) {
  if (!chatId) return;
  const tasksToRestore = readActiveTasks().filter(item => Number(item?.chatId) === Number(chatId));
  if (!tasksToRestore.length) return;

  let added = false;
  for (const item of tasksToRestore) {
    if (!item?.taskId) continue;
    if (state.pendingTasks.some(task => task.task_id === item.taskId)) continue;
    const msgIndex = state.messages.length;
    state.messages.push({
      role: 'assistant',
      content: '',
      loading: true,
      currentStatus: 'restoring',
      liveTasks: [],
      liveCommands: [],
      _taskId: item.taskId,
    });
    state.pendingTasks.push({ task_id: item.taskId, msgIndex });
    added = true;
  }

  if (!added) return;
  renderMessages();
  updateStopButton();
  for (const item of tasksToRestore) {
    const pending = state.pendingTasks.find(task => task.task_id === item.taskId);
    if (pending) pollTask(pending.task_id, pending.msgIndex);
  }
}

function getStepIndex(step, stepIndex) {
  const raw = step?.idx ?? step?.index ?? step?.step_index ?? stepIndex;
  const numeric = Number(raw);
  return Number.isFinite(numeric) ? numeric : stepIndex;
}

function getCommandStepIndex(command) {
  if (command?.step_index == null) return null;
  const numeric = Number(command.step_index);
  return Number.isFinite(numeric) ? numeric : null;
}

function isPipelineCommandLog(commands) {
  return Array.isArray(commands) && commands.some(command => getCommandStepIndex(command) !== null);
}

function getCommandStatus(command) {
  if (command?.status) return command.status;
  const result = command?.result || {};
  if (command?.action === 'budget_guard' || stripUtfPrefix(command?.command || '') === '[budget_guard]') return 'blocked';
  if (result?.error) return 'error';
  if (result?.returncode != null && result.returncode !== 0) return 'error';
  return 'success';
}

function getToolEntries(commands) {
  return Array.isArray(commands) ? commands.filter(command => command && command.tool_name) : [];
}

function getToolNameList(commands, type) {
  const names = getToolEntries(commands)
    .filter(command => !type || command.tool_type === type)
    .map(command => command.tool_name)
    .filter(Boolean);
  return [...new Set(names)];
}

function renderUsedToolsLine(commands) {
  const entries = getToolEntries(commands);
  const typed = entries
    .filter(command => command.tool_type !== 'fallback' && command.tool_type !== 'answer')
    .map(command => command.tool_name)
    .filter(Boolean);
  const fallback = getToolNameList(commands, 'fallback');
  const answer = entries
    .filter(command => command.tool_type === 'answer')
    .map(command => command.tool_name)
    .filter(Boolean);
  const typedNames = [...new Set(typed)];
  const answerNames = [...new Set(answer)];
  const parts = [];
  if (typedNames.length === 1) {
    parts.push(`Использован инструмент: ${escapeHTML(typedNames[0])}`);
  } else if (typedNames.length > 1) {
    parts.push(`Использованы инструменты: ${escapeHTML(typedNames.join(', '))}`);
  }
  if (fallback.length === 1) {
    parts.push(`Использован fallback: ${escapeHTML(fallback[0])}`);
  } else if (fallback.length > 1) {
    parts.push(`Использованы fallback: ${escapeHTML(fallback.join(', '))}`);
  }
  if (!typedNames.length && !fallback.length && answerNames.length) parts.push('Ответ');
  return parts.length ? `<div class="tool-usage-line">${parts.join(' · ')}</div>` : '';
}

function getCommandDetailsText(command, fallbackOutput) {
  if (!command?.tool_name) return fallbackOutput;
  const lines = [
    `tool_name: ${command.tool_name}`,
    `status: ${command.tool_status || getCommandStatus(command)}`,
  ];
  if (command.target_device_id || command.device_id) lines.push(`target_device: ${command.target_device_id || command.device_id}`);
  if (command.summary) lines.push(`summary: ${command.summary}`);
  const result = command.result || {};
  if (command.tool_type === 'answer') {
    if (result.answer_type) lines.push(`answer_type: ${result.answer_type}`);
    if (Array.isArray(result.basis)) lines.push(`basis: ${result.basis.join(', ')}`);
    if (result.self_check) lines.push(`self_check: ${JSON.stringify(result.self_check)}`);
  }
  const windowInfo = result.window || result.match || {};
  const detailFields = {
    pid: result.pid || windowInfo.pid,
    verified: result.verified,
    process_alive: result.process_alive,
    window_visible: result.window_visible ?? windowInfo.visible,
    window_title: result.window_title || windowInfo.title,
    class_name: windowInfo.class_name,
    process_name: windowInfo.process_name,
  };
  Object.entries(detailFields).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') lines.push(`${key}: ${value}`);
  });
  if (fallbackOutput && fallbackOutput !== '(нет вывода)') lines.push('', fallbackOutput);
  return lines.join('\n');
}

function getCommandDisplayText(command) {
  if (command?.tool_name) {
    if (command.tool_type === 'answer') return `Ответ: ${command.tool_name}`;
    const prefix = command.tool_type === 'fallback' ? 'Fallback' : 'Инструмент';
    return `${prefix}: ${command.tool_name}`;
  }
  return stripUtfPrefix(command?.command || command?.action || 'command');
}

function getCommandOutputPreview(command) {
  if (command?.summary) return String(command.summary);
  const result = command?.result;
  if (!result) return '';
  if (typeof result === 'string') return result;
  if (result.error) return String(result.error);
  if (result.stderr) return String(result.stderr);
  if (result.stdout) return String(result.stdout);
  if (result.result) return String(result.result);
  if (result.answer) return String(result.answer);
  if (Array.isArray(result.results) && result.results.length) {
    return result.results.map(item => item?.title || item?.url || '').filter(Boolean).join(' | ');
  }
  if (result.file_path) return String(result.file_path);
  return '';
}

function shortenText(text, limit = 140) {
  if (!text) return '';
  const compact = String(text).replace(/\s+/g, ' ').trim();
  return compact.length > limit ? `${compact.slice(0, limit - 1)}…` : compact;
}

function synthesizePipelineTasks(commands, fallbackTaskId) {
  if (!isPipelineCommandLog(commands)) return [];
  const stepMap = new Map();

  for (const command of commands) {
    const stepIndex = getCommandStepIndex(command);
    if (stepIndex == null) continue;
    const current = stepMap.get(stepIndex) || {
      idx: stepIndex,
      description: command.step_title || `Step ${stepIndex + 1}`,
      status: 'pending',
      summary: '',
    };
    const commandStatus = getCommandStatus(command);
    if (commandStatus === 'error' || commandStatus === 'blocked') current.status = 'failed';
    else if (commandStatus === 'running') current.status = 'running';
    else if (current.status === 'failed') current.status = 'recovered';
    else current.status = 'done';
    if (!current.summary) current.summary = shortenText(getCommandOutputPreview(command), 180);
    stepMap.set(stepIndex, current);
  }

  const steps = [...stepMap.entries()]
    .sort((left, right) => left[0] - right[0])
    .map(([, step]) => step);
  if (!steps.length) return [];

  const hasFailures = steps.some(step => step.status === 'failed');
  const hasRecovered = steps.some(step => step.status === 'recovered');
  const hasRunning = steps.some(step => step.status === 'running');
  return [{
    id: fallbackTaskId || 'pipeline-message',
    goal: 'Pipeline',
    status: hasRunning ? 'running' : (hasFailures ? 'failed' : (hasRecovered ? 'completed_with_recovery' : 'completed')),
    steps,
  }];
}

function getStepCommands(commands, step, stepIndex) {
  if (!Array.isArray(commands) || !commands.length) return [];
  const resolvedStepIndex = getStepIndex(step, stepIndex);
  return commands.filter(command => getCommandStepIndex(command) === resolvedStepIndex);
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

function getStepCommandStatusIcon(status) {
  if (status === 'error') return '\u2717';
  if (status === 'blocked') return '\u25a0';
  if (status === 'running') return '\u23f3';
  return '\u2713';
}

function getStepStatusLine(stepStatus, stepCommands, step) {
  const lastCommand = stepCommands[stepCommands.length - 1];
  if (!lastCommand) {
    return step.summary ? shortenText(step.summary, 140) : 'Команд пока нет';
  }

  const commandStatus = getCommandStatus(lastCommand);
  if (commandStatus === 'error' || commandStatus === 'blocked') {
    return `Ошибка: ${shortenText(getCommandOutputPreview(lastCommand) || getCommandDisplayText(lastCommand), 140)}`;
  }
  if (stepStatus === 'running' || commandStatus === 'running') {
    return `Сейчас: ${shortenText(getCommandDisplayText(lastCommand), 140)}`;
  }
  if (lastCommand.tool_name) {
    const label = lastCommand.tool_type === 'fallback' ? 'Последний fallback' : 'Последний инструмент';
    return `${label}: ${shortenText(lastCommand.tool_name, 140)}`;
  }
  return `Последняя команда: ${shortenText(getCommandDisplayText(lastCommand), 140)}`;
}

function renderStepCommands(stepCommands) {
  if (!stepCommands.length) return '';
  const items = stepCommands.map((command) => {
    const status = getCommandStatus(command);
    const output = shortenText(getCommandOutputPreview(command), 320) || '(нет вывода)';
    const device = command.device_name || command.device_id || '';
    const toolDetails = command.tool_name ? `<div class="step-command-tool-details">${escapeHTML([
      `tool_name: ${command.tool_name}`,
      `status: ${command.tool_status || status}`,
      `target_device: ${command.target_device_id || command.device_id || ''}`,
      `summary: ${command.summary || ''}`,
      `pid: ${command.result?.pid || command.result?.window?.pid || command.result?.match?.pid || ''}`,
      `verified: ${command.result?.verified ?? ''}`,
      `window_title: ${command.result?.window_title || command.result?.window?.title || command.result?.match?.title || ''}`,
      `process_name: ${command.result?.window?.process_name || command.result?.match?.process_name || ''}`,
    ].join('\n'))}</div>` : '';
    return `<div class="step-command-entry step-command-${escapeAttr(status)}">
      <div class="step-command-header">
        <span class="step-command-icon">${getStepCommandStatusIcon(status)}</span>
        <span class="step-command-text">${escapeHTML(getCommandDisplayText(command))}</span>
        ${device ? `<span class="step-command-device">${escapeHTML(device)}</span>` : ''}
      </div>
      <div class="step-command-output">${escapeHTML(output)}</div>
      ${toolDetails}
    </div>`;
  });
  return `<div class="step-command-list">${items.join('')}</div>`;
}

function calculatePipelineProgress(task) {
  const steps = Array.isArray(task?.steps) ? task.steps : [];
  const status = normalizeTaskStateKey(task?.status || 'running');
  if (!steps.length) {
    return {
      total: 0,
      completed: 0,
      percent: status === 'completed' || status === 'done' ? 100 : 0,
      status,
      currentStep: '',
      indeterminate: status === 'running' || status === 'cancelling',
    };
  }
  const completeStates = new Set(['done', 'completed', 'recovered', 'completed_with_recovery']);
  const completed = steps.filter(step => completeStates.has(normalizeStepStateKey(step.status))).length;
  const runningIndex = steps.findIndex(step => normalizeStepStateKey(step.status) === 'running');
  const currentIndex = runningIndex >= 0 ? runningIndex : Math.min(completed, steps.length - 1);
  const current = steps[currentIndex] || {};
  const percent = Math.max(0, Math.min(100, Math.round((completed / steps.length) * 100)));
  return {
    total: steps.length,
    completed,
    percent: status === 'completed' || status === 'done' ? 100 : percent,
    status,
    currentStep: current.title || current.description || '',
    currentIndex,
    indeterminate: false,
  };
}

function renderPipelineProgress(task) {
  const progress = calculatePipelineProgress(task);
  const statusLabel = normalizeTaskBadgeLabel(progress.status);
  if (progress.total === 0) {
    return `<div class="pipeline-progress pipeline-progress-indeterminate">
      <div class="pipeline-progress-head"><span>Pipeline выполняется...</span><span>${escapeHTML(statusLabel)}</span></div>
      <div class="pipeline-progress-bar"><span style="width: 38%"></span></div>
    </div>`;
  }
  const stepNumber = Math.min(progress.total, (progress.currentIndex ?? progress.completed) + 1);
  return `<div class="pipeline-progress">
    <div class="pipeline-progress-head">
      <span>Pipeline: шаг ${stepNumber} из ${progress.total} · ${progress.percent}%</span>
      <span>${escapeHTML(statusLabel)}</span>
    </div>
    <div class="pipeline-progress-bar"><span style="width: ${progress.percent}%"></span></div>
    ${progress.currentStep ? `<div class="pipeline-progress-current">Сейчас: ${escapeHTML(progress.currentStep)}</div>` : ''}
  </div>`;
}

function renderTaskBlock(tasks, commands = [], fallbackTaskId = '') {
  const normalizedTasks = (tasks && tasks.length > 0) ? tasks : synthesizePipelineTasks(commands, fallbackTaskId);
  if (!normalizedTasks || normalizedTasks.length === 0) return '';
  let html = '';
  for (let ti = 0; ti < normalizedTasks.length; ti++) {
    const t = normalizedTasks[ti];
    const st = normalizeTaskStateKey(t.status);
    const statusLabel = normalizeTaskBadgeLabel(t.status);
    html += `<div class="task-block task-${escapeAttr(st)}">`;
    html += `<div class="task-goal"><span class="task-goal-label">\u0417\u0430\u0434\u0430\u0447\u0430:</span> ${escapeHTML(t.goal || '')} <span class="task-badge task-badge-${escapeAttr(st)}">${escapeHTML(statusLabel)}</span></div>`;
    html += renderPipelineProgress(t);
    const steps = t.steps || [];
    if (steps.length > 0) {
      html += '<ul class="task-steps">';
      for (let si = 0; si < steps.length; si++) {
        const s = steps[si];
        const sst = normalizeStepStateKey(s.status);
        const stepStatusLabel = normalizeStepStatusLabel(s.status);
        const resolvedStepIndex = getStepIndex(s, si);
        const stepCommands = getStepCommands(commands, s, si);
        const taskUiId = t.id || t.task_id || t.taskId || fallbackTaskId || `task-${ti}`;
        const commandsKey = `${taskUiId}:${resolvedStepIndex}`;
        const hasStepCommands = stepCommands.length > 0;
        const isCommandsOpen = hasStepCommands && state.expandedStepCommands && state.expandedStepCommands.has(commandsKey);
        const icon = sst === 'done' ? '\u2713'
          : sst === 'recovered' ? '!'
          : sst === 'failed' ? '\u2717'
          : sst === 'running' ? '\u23f3'
          : sst === 'blocked' ? '\u25a0'
          : sst === 'skipped' ? '\u2014'
          : sst === 'cancelled' ? '\u2014'
          : '\u25cb';
        const title = s.title || s.description || `Step ${si + 1}`;
        const description = s.title && s.description && s.description !== s.title
          ? `<span class="step-subdesc">${escapeHTML(s.description)}</span>`
          : '';
        const detailsHTML = renderStepDetails(s);
        const commandsHTML = renderStepCommands(stepCommands);
        const hasDetails = detailsHTML.length > 0 || commandsHTML.length > 0;
        const stepKey = getTaskStepKey(t, s, ti, si);
        const showDetailsByDefault = !hasStepCommands && detailsHTML.length > 0;
        const isOpen = hasStepCommands
          ? isCommandsOpen
          : (showDetailsByDefault || (hasDetails && state.expandedStepDetails && state.expandedStepDetails.has(stepKey)));
        const toggle = hasStepCommands
          ? `<button type="button" class="step-details-toggle" data-action="toggle-step-commands" data-task-id="${escapeAttr(taskUiId)}" data-step-index="${escapeAttr(resolvedStepIndex)}" aria-expanded="${isOpen ? 'true' : 'false'}" title="\u041f\u043e\u043a\u0430\u0437\u0430\u0442\u044c \u043a\u043e\u043c\u0430\u043d\u0434\u044b \u0448\u0430\u0433\u0430"><span class="step-details-arrow">${isOpen ? '\u25be' : '\u25b8'}</span></button>`
          : '';
        const stepStatusLine = getStepStatusLine(sst, stepCommands, s);
        html += `<li class="task-step step-${escapeAttr(sst)}${isOpen ? ' open' : ''}" data-step-key="${escapeAttr(stepKey)}" data-task-id="${escapeAttr(taskUiId)}" data-step-index="${escapeAttr(resolvedStepIndex)}"><div class="step-row"><span class="step-icon">${icon}</span><span class="step-desc">${escapeHTML(title)}${description}<span class="step-command-line">${escapeHTML(stepStatusLine)}</span></span><span class="step-status">${escapeHTML(stepStatusLabel)}</span>${toggle}</div>${hasDetails ? `<div class="step-details"${(isOpen || showDetailsByDefault) ? '' : ' hidden'}>${detailsHTML}${commandsHTML}</div>` : ''}</li>`;
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
    forgetActiveTask(taskId);
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

