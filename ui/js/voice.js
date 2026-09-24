/* DeepTalk voice interaction using IRU's chat/task/auth flow. */
(() => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const button = document.getElementById('voiceBtn');
  const status = document.getElementById('voiceStatus');
  const stopButton = document.getElementById('voiceStopSpeech');
  let recognition = null, wantListening = false, restartTimer = null;
  let audioContext = null, source = null, activation = 0, starting = false;
  const labels = { off: '', idle: 'Голос включён · скажите «Иру»', listening: 'Слушаю…',
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
  function listen(wanted) {
    wantListening = wanted; clearTimeout(restartTimer);
    if (!wanted) {
      const old = recognition; recognition = null;
      if (old) { old.onend = null; old.onresult = null; try { old.abort(); } catch (_) {} }
      return;
    }
    if (recognition || !SR) return;
    const rec = new SR(); recognition = rec;
    rec.lang = 'ru-RU'; rec.continuous = true; rec.interimResults = true;
    rec.onresult = event => {
      if (recognition !== rec || !wantListening) return;
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (recognition !== rec) break;
        session.transcript(event.results[i][0].transcript, event.results[i].isFinal);
      }
    };
    rec.onerror = event => {
      if (recognition !== rec) return;
      if (event.error !== 'no-speech' && event.error !== 'aborted') {
        reset(); showToast(event.error === 'not-allowed' ? 'Разрешите доступ к микрофону.' : 'Распознавание недоступно. Голосовой режим выключен.', true);
      }
    };
    rec.onend = () => {
      if (recognition !== rec) return;
      recognition = null;
      if (wantListening) restartTimer = setTimeout(() => listen(true), 250);
    };
    try { rec.start(); } catch (_) { recognition = null; reset(); showToast('Не удалось включить микрофон.', true); }
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
      status.textContent = labels[phase]; status.hidden = phase === 'off';
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
      session.enable(state.pendingTasks.map(task => task.task_id));
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
  window.addEventListener('pagehide', reset);
})();
