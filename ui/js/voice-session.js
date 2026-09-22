/* Voice lifecycle, independent of DOM/audio/network for deterministic tests. */
(function (root) {
  function createVoiceSession(io) {
    let enabled = false, epoch = 0, phase = 'off', activeUntil = 0;
    let utterance = '', silenceTimer = null, wakeTimer = null, playback = null;
    const requests = new Set(), tasks = new Set(), finished = new Set();
    let queue = [];
    const now = io.now || Date.now, later = io.setTimeout || setTimeout, clear = io.clearTimeout || clearTimeout;
    function setPhase(next) { phase = next; io.state(next); }
    function clearUtterance() { utterance = ''; clear(silenceTimer); silenceTimer = null; }
    function stopPlayback() { if (playback) playback.abort(); playback = null; io.stopAudio(); }
    function busy() { return tasks.size > 0 || requests.size > 0; }
    function pause() { clearUtterance(); clear(wakeTimer); stopPlayback(); io.listen(false); setPhase('working'); }
    function resume(fresh = false) {
      if (!enabled || busy()) return;
      if (fresh) activeUntil = now() + 10000;
      setPhase(now() < activeUntil ? 'listening' : 'idle'); io.listen(true); clear(wakeTimer);
      if (phase === 'listening') wakeTimer = later(() => {
        if (enabled && !busy() && phase === 'listening' && !utterance) { activeUntil = 0; setPhase('idle'); }
      }, Math.max(0, activeUntil - now()));
    }
    async function drain() {
      if (!enabled || busy() || playback) return;
      if (!queue.length) { resume(true); return; }
      const taskId = queue.shift(), savedEpoch = epoch;
      const controller = new AbortController(); playback = controller;
      setPhase('synthesizing'); io.listen(false);
      try {
        await io.speak(taskId, controller.signal, () => {
          if (!controller.signal.aborted && enabled && epoch === savedEpoch) { setPhase('speaking'); io.listen(true); }
        });
      } catch (error) {
        if (!controller.signal.aborted && enabled && epoch === savedEpoch) io.error(error);
      } finally {
        if (playback === controller && enabled && epoch === savedEpoch) { playback = null; io.listen(false); drain(); }
      }
    }
    function stopSpeech() {
      if (!enabled || !['speaking', 'synthesizing'].includes(phase)) return;
      queue = []; stopPlayback(); io.listen(false); resume(true);
    }
    function disable() {
      enabled = false; epoch++; requests.clear(); tasks.clear(); finished.clear(); queue = [];
      clearUtterance(); clear(wakeTimer); stopPlayback(); io.listen(false); setPhase('off');
    }
    function enable(pending = []) {
      disable(); enabled = true; activeUntil = 0; pending.forEach(id => tasks.add(id));
      if (busy()) pause(); else resume();
    }
    function beginRequest() {
      if (!enabled) return null;
      const ticket = { epoch }; requests.add(ticket); pause(); return ticket;
    }
    function endRequest(ticket, taskId) {
      if (!enabled || !ticket || ticket.epoch !== epoch || !requests.delete(ticket)) return;
      if (taskId) { tasks.add(taskId); pause(); } else drain();
    }
    function requestLost(ticket) {
      if (enabled && ticket?.epoch === epoch && requests.has(ticket)) disable();
    }
    function watchTask(taskId, ticket) {
      if (!enabled || (ticket !== undefined && (!ticket || ticket.epoch !== epoch)) || finished.has(taskId)) return;
      tasks.add(taskId); pause();
    }
    function taskFinished(taskId, task) {
      if (!enabled || !tasks.delete(taskId)) return;
      finished.add(taskId);
      if (typeof task?.answer === 'string' && task.answer.trim()) queue.push(taskId);
      drain();
    }
    function taskLost(taskId) {
      if (tasks.has(taskId)) { disable(); io.error(new Error('Связь с задачей потеряна. Проверьте её состояние перед включением голоса.')); }
    }
    function taskPaused(taskId) { if (enabled && tasks.has(taskId)) { pause(); setPhase('confirming'); } }
    function transcript(text, final) {
      if (!enabled || busy()) return;
      const clean = text.trim();
      if (phase === 'speaking') {
        if (/^(?:иру[\s,]*)?стоп[.!?,]*$/iu.test(clean)) stopSpeech();
        return;
      }
      if (!['idle', 'listening'].includes(phase)) return;
      const wake = /(^|[^\p{L}\p{N}])иру(?=$|[^\p{L}\p{N}])/iu;
      const woke = wake.test(clean);
      if (!woke && now() >= activeUntil && !utterance) return;
      if (woke) activeUntil = now() + 10000;
      setPhase('listening'); clear(wakeTimer);
      if (final) {
        const words = clean.replace(wake, '$1').trim();
        if (words) utterance = [utterance, words].filter(Boolean).join(' ');
      }
      clear(silenceTimer);
      silenceTimer = later(() => {
        const textToSend = utterance.trim(); clearUtterance();
        if (!enabled || busy()) return;
        if (!textToSend || /^стоп[.!?,]*$/iu.test(textToSend)) { resume(true); return; }
        io.listen(false); setPhase('working');
        const savedEpoch = epoch;
        Promise.resolve(io.submit(textToSend)).then(() => {
          if (enabled && epoch === savedEpoch && !busy() && phase === 'working') resume(true);
        }).catch(error => { if (enabled && epoch === savedEpoch) { io.error(error); resume(true); } });
      }, 1500);
    }
    return { enable, disable, beginRequest, endRequest, requestLost, watchTask, taskFinished, taskLost,
      taskPaused, transcript, stopSpeech, get enabled() { return enabled; }, get phase() { return phase; } };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { createVoiceSession };
  else root.createVoiceSession = createVoiceSession;
})(globalThis);
