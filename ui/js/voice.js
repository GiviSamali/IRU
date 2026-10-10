/* DeepTalk voice interaction using IRU's chat/task/auth flow. */
(() => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const singleUtterance = /Android/i.test(navigator.userAgent);
  const button = document.getElementById('voiceBtn');
  const status = document.getElementById('voiceStatus');
  const stopButton = document.getElementById('voiceStopSpeech');
  let recognition = null, wantListening = false, restartTimer = null, healthTimer = null;
  let retryAttempt = 0, healthDeadline = 0, reconnecting = false, voicePhase = 'off';
  const START_TIMEOUT_MS = 10000, IDLE_RENEW_MS = 90000, MAX_RETRY_MS = 15000;
  let audioContext = null, source = null, activation = 0, starting = false;
  const labels = { off: '', idle: 'Голос включён · скажите «Иру»', listening: 'Слушаю… · «усни» — ожидание «Иру»',
    awaiting_plan: 'Запустить План? Скажите «да», «запускай» или «нет»',
    awaiting_plan_review: 'Изменить план? «Нет» — выполнить, «да» — продиктовать изменения',
    awaiting_command: 'Выполнить действие? «Да» — выполнить, «нет» — отменить',
    editing_plan: 'Слушаю изменения плана…',
    working: 'Выполняю · микрофон выключен', confirming: 'Нужно подтверждение в чате · микрофон выключен',
    synthesizing: 'Готовлю озвучку…', speaking: 'Отвечаю · «стоп» остановит озвучку' };
  function playMicCue(kind) {
    if (!audioContext || audioContext.state !== 'running') return;
    try {
      const oscillator = audioContext.createOscillator(), gain = audioContext.createGain();
      const start = audioContext.currentTime, duration = 0.12;
      oscillator.type = 'sine';
      oscillator.frequency.setValueAtTime(kind === 'on' ? 520 : 880, start);
      oscillator.frequency.exponentialRampToValueAtTime(kind === 'on' ? 880 : 440, start + duration);
      gain.gain.setValueAtTime(0.001, start);
      gain.gain.exponentialRampToValueAtTime(0.045, start + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.001, start + duration);
      oscillator.connect(gain); gain.connect(audioContext.destination);
      oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
      oscillator.start(start); oscillator.stop(start + duration);
    } catch (_) { /* Cue failure must not interrupt voice/task control. */ }
  }
  function stopAudio() {
    if (source) { try { source.stop(); } catch (_) {} source = null; }
  }
  function renderVoiceStatus() {
    status.textContent = reconnecting && wantListening
      ? 'Восстанавливаю микрофон… · ожидание «Иру»'
      : labels[voicePhase];
    status.hidden = voicePhase === 'off';
  }
  function releaseRecognition(rec) {
    if (recognition !== rec) return;
    recognition = null; clearTimeout(healthTimer); healthTimer = null; healthDeadline = 0;
    // Detach before abort: a late callback cannot retire the replacement run.
    rec.onend = rec.onerror = rec.onresult = rec.onstart = rec.onaudiostart = rec.onspeechstart = rec.onspeechend = null;
    try { rec.abort(); } catch (_) {}
  }
  function scheduleRestart(delay = 250) {
    if (!wantListening || restartTimer !== null) return;
    restartTimer = setTimeout(() => {
      restartTimer = null;
      if (wantListening && !recognition) startRecognition();
    }, delay);
  }
  function reconnect(rec, backoff = false) {
    if (recognition !== rec || !wantListening) return;
    releaseRecognition(rec); reconnecting = true; renderVoiceStatus();
    const delay = backoff ? Math.min(MAX_RETRY_MS, 1000 * 2 ** Math.min(retryAttempt++, 4)) : 250;
    scheduleRestart(delay);
  }
  function armHealth(rec, delay) {
    clearTimeout(healthTimer); healthDeadline = Date.now() + delay;
    healthTimer = setTimeout(() => {
      healthTimer = null;
      // Silence is not a reason to turn voice off. Renew even if end/error was lost.
      if (recognition === rec && wantListening) reconnect(rec, delay === START_TIMEOUT_MS);
    }, delay);
  }
  function listen(wanted) {
    if (!wanted) {
      wantListening = false; clearTimeout(restartTimer); restartTimer = null;
      clearTimeout(healthTimer); healthTimer = null; healthDeadline = 0;
      retryAttempt = 0; reconnecting = false;
      if (recognition) releaseRecognition(recognition);
      renderVoiceStatus(); return;
    }
    wantListening = true;
    // Repeated resume/status updates must not bypass retry backoff or start twice.
    if (recognition || restartTimer !== null || !SR) return;
    startRecognition();
  }
  function startRecognition() {
    if (!wantListening || recognition || !SR) return;
    let rec;
    try { rec = new SR(); } catch (_) {
      reset(); showToast('Распознавание недоступно в этом браузере.', true); return;
    }
    recognition = rec;
    rec.lang = 'ru-RU'; rec.continuous = !singleUtterance; rec.interimResults = true;
    const startedAt = Date.now(), deliveredFinals = new Set();
    rec.onstart = rec.onaudiostart = () => {
      if (recognition !== rec || !wantListening) return;
      reconnecting = false; renderVoiceStatus(); armHealth(rec, IDLE_RENEW_MS);
    };
    rec.onspeechstart = rec.onspeechend = () => {
      if (recognition === rec && wantListening) armHealth(rec, IDLE_RENEW_MS);
    };
    rec.onresult = event => {
      if (recognition !== rec || !wantListening) return;
      retryAttempt = 0; reconnecting = false; renderVoiceStatus(); armHealth(rec, IDLE_RENEW_MS);
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (recognition !== rec) break;
        if (deliveredFinals.has(i)) continue;
        const result = event.results[i];
        if (result.isFinal) deliveredFinals.add(i);
        // Android cumulative finals are accepted once per run, without deduping words.
        if (singleUtterance && result.isFinal) rec.onresult = null;
        session.transcript(result[0].transcript, result.isFinal);
        if (singleUtterance && result.isFinal) {
          if (recognition === rec) {
            releaseRecognition(rec);
            scheduleRestart();
          }
          break;
        }
      }
    };
    rec.onerror = event => {
      if (recognition !== rec || !wantListening) return;
      if (['no-speech', 'aborted', 'network'].includes(event.error)) {
        // Transient provider errors do not revoke the user's enabled voice session.
        reconnect(rec, event.error === 'network'); return;
      }
      reset();
      const message = {
        'not-allowed': 'Разрешите доступ к микрофону.',
        'service-not-allowed': 'Браузер запретил сервис распознавания речи.',
        'audio-capture': 'Микрофон недоступен. Проверьте его подключение и разрешения.',
        'language-not-supported': 'Сервис не поддерживает выбранный язык распознавания.',
      }[event.error] || 'Распознавание недоступно. Голосовой режим выключен.';
      showToast(message, true);
    };
    rec.onend = () => {
      if (recognition !== rec || !wantListening) return;
      if (Date.now() - startedAt >= START_TIMEOUT_MS) retryAttempt = 0;
      reconnect(rec);
    };
    armHealth(rec, START_TIMEOUT_MS);
    try { rec.start(); } catch (error) {
      if (error?.name === 'InvalidStateError' || error?.name === 'NetworkError') reconnect(rec, true);
      else { reset(); showToast('Не удалось включить микрофон. Проверьте доступ к нему.', true); }
    }
  }
  function recoverOnReturn() {
    if (!wantListening) return;
    if (recognition && healthDeadline && Date.now() >= healthDeadline) releaseRecognition(recognition);
    if (!recognition) {
      clearTimeout(restartTimer); restartTimer = null;
      scheduleRestart(0);
    }
  }
  async function speak(taskId, signal, onSpeaking, review) {
    let count = 1;
    for (let part = 0; part < count; part++) {
      const consentQuery = review?.confirmationId ? `&confirmation=${encodeURIComponent(review.confirmationId)}` : review?.revision ? `&revision=${encodeURIComponent(review.revision)}` : '';
      const response = await apiFetch(`${API}/api/voice/tasks/${encodeURIComponent(taskId)}/speech?part=${part}${consentQuery}`, {
        method: 'POST', headers: authHeaders(), signal,
      });
      if (response.status === 204) return;
      if (!response.ok) throw new Error('Озвучка недоступна. Ответ сохранён в чате.');
      count = Number(response.headers.get('X-Voice-Parts')) || 1;
      const bytes = await response.arrayBuffer();
      if (signal.aborted) return;
      const buffer = await audioContext.decodeAudioData(bytes);
      if (signal.aborted) return;
      if (audioContext.state !== 'running') throw new Error('Звук приостановлен браузером. Включите голосовой режим снова.');
      await new Promise(resolve => {
        const node = audioContext.createBufferSource(); source = node;
        node.buffer = buffer; node.connect(audioContext.destination);
        let done = false;
        const finish = () => {
          if (done) return; done = true;
          signal.removeEventListener('abort', cancel);
          node.disconnect(); if (source === node) source = null; resolve();
        };
        const cancel = () => { try { node.stop(); } catch (_) {} finish(); };
        node.onended = finish; signal.addEventListener('abort', cancel, { once: true });
        if (signal.aborted) { cancel(); return; }
        node.start(); onSpeaking();
      });
      if (signal.aborted) return;
    }
  }
  const session = createVoiceSession({ listen, speak, stopAudio, cue: playMicCue,
    choosePlan: (offer, accepted) => chooseVoicePlan(offer, accepted),
    reviewPlan: (review, changes) => submitPlanReview(review, changes),
    chooseCommand: (offer, accepted) => chooseVoiceCommand(offer, accepted, true),
    submit: text => sendMessage({ voiceText: text }),
    error: error => showToast(error.message, true),
    state: phase => {
      voicePhase = phase; renderVoiceStatus();
      stopButton.hidden = !['speaking', 'synthesizing'].includes(phase);
      button.classList.toggle('recording', phase !== 'off');
      button.setAttribute('aria-pressed', String(phase !== 'off'));
      button.setAttribute('aria-label', phase === 'off' ? 'Включить голосовой режим' : 'Выключить голосовой режим');
    },
  });
  function reset() { activation++; starting = false; session.disable(); }
  async function toggle() {
    if (session.enabled || starting) { reset(); return; }
    if (!state.user) return;
    const id = ++activation; starting = true;
    try {
      audioContext ||= new (window.AudioContext || window.webkitAudioContext)();
      await audioContext.resume();
      const response = await apiFetch(`${API}/api/voice/config`, { headers: authHeaders() });
      if (!response.ok) throw new Error('Не удалось включить голосовой режим.');
      const config = await response.json();
      if (id !== activation) return;
      if (!config.available) throw new Error('Озвучка не настроена на сервере.');
      session.enable(state.pendingTasks.map(task=>({id:task.task_id,background:task.kind !== 'orchestrator'})),{continuous:true});
      const reviewMessage = state.messages.find(message => message.planReview);
      if (reviewMessage) session.taskPlanReview(reviewMessage._taskId, reviewMessage.planReview);
      const confirmationMessage = state.messages.find(message => message.confirmTaskId && message.commandConfirmation?.voice_allowed === true);
      if (confirmationMessage) session.taskPaused(confirmationMessage._taskId, confirmationMessage.commandConfirmation);
    } catch (error) { if (id === activation) { reset(); showToast(error.message, true); } }
    finally { if (id === activation) starting = false; }
  }
  window.iruVoice = session;
  window.stopVoice = reset;
  button.title = 'Голосовой режим (Ctrl+Shift+M)';
  if (!SR) { button.disabled = true; button.title = 'Голосовой режим требует поддержки распознавания речи в браузере'; }
  else button.addEventListener('click', toggle);
  stopButton.addEventListener('click', () => session.stopSpeech());
  document.addEventListener('keydown', event => {
    if (SR && event.ctrlKey && event.shiftKey && event.code === 'KeyM') { event.preventDefault(); toggle(); }
  });
  window.addEventListener('online', recoverOnReturn);
  window.addEventListener('focus', recoverOnReturn);
  document.addEventListener('resume', recoverOnReturn);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) recoverOnReturn(); });
  window.addEventListener('pagehide', reset);
})();
