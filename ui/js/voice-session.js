/* Voice lifecycle, independent of DOM/audio/network for deterministic tests. */
(function (root) {
  function createVoiceSession(io) {
    let enabled = false, epoch = 0, phase = 'off', activeUntil = 0;
    let utterance = '', silenceTimer = null, wakeTimer = null, playback = null;
    const requests = new Set(), tasks = new Set(), finished = new Set();
    let queue = [], planOffer = null, planReview = null, editingPlan = false, reviewHeard = false;
    let commandConfirmation = null, commandHeard = false;
    const now = io.now || Date.now, later = io.setTimeout || setTimeout, clear = io.clearTimeout || clearTimeout;
    function setPhase(next) { phase = next; io.state(next); }
    function clearUtterance() { utterance = ''; clear(silenceTimer); silenceTimer = null; }
    function stopPlayback() { if (playback) playback.abort(); playback = null; io.stopAudio(); }
    function busy() { return tasks.size > 0 || requests.size > 0; }
    function pause() { clearUtterance(); clear(wakeTimer); stopPlayback(); io.listen(false); setPhase('working'); }
    function resume(fresh = false) {
      if (!enabled || busy()) return;
      if (commandConfirmation) {
        setPhase(commandHeard ? 'awaiting_deletion' : 'confirming');
        io.listen(commandHeard); clear(wakeTimer); return;
      }
      if (planReview) {
        if (!editingPlan && !reviewHeard) { setPhase('confirming'); io.listen(false); return; }
        setPhase(editingPlan ? 'editing_plan' : 'awaiting_plan_review');
        io.listen(true); clear(wakeTimer); return;
      }
      if (planOffer) {
        setPhase('awaiting_plan'); io.listen(true); clear(wakeTimer);
        wakeTimer = later(() => { planOffer = null; resume(true); }, 30000);
        return;
      }
      if (fresh) activeUntil = now() + 10000;
      setPhase(now() < activeUntil ? 'listening' : 'idle'); io.listen(true); clear(wakeTimer);
      if (phase === 'listening') wakeTimer = later(() => {
        if (enabled && !busy() && phase === 'listening' && !utterance) { activeUntil = 0; setPhase('idle'); }
      }, Math.max(0, activeUntil - now()));
    }
    async function drain() {
      if (!enabled || busy() || playback) return;
      if (!queue.length) { resume(true); return; }
      const item = queue.shift(), taskId = item.id, savedEpoch = epoch;
      const controller = new AbortController(); playback = controller;
      let heard = false;
      setPhase('synthesizing'); io.listen(false);
      try {
        await io.speak(taskId, controller.signal, () => {
          if (!controller.signal.aborted && enabled && epoch === savedEpoch) { heard = true; setPhase('speaking'); io.listen(true); }
        }, item.review || item.confirmation);
        if (!controller.signal.aborted && enabled && epoch === savedEpoch) planOffer = heard ? item.plan : null;
        if (item.review && !controller.signal.aborted && enabled && epoch === savedEpoch) reviewHeard = heard;
        if (item.confirmation && !controller.signal.aborted && enabled && epoch === savedEpoch) commandHeard = heard;
      } catch (error) {
        if (!controller.signal.aborted && enabled && epoch === savedEpoch) io.error(error);
      } finally {
        if (playback === controller && enabled && epoch === savedEpoch) { playback = null; io.listen(false); drain(); }
      }
    }
    function stopSpeech() {
      if (!enabled || !['speaking', 'synthesizing'].includes(phase)) return;
      queue = []; planOffer = null; stopPlayback(); io.listen(false); resume(true);
    }
    function disable() {
      enabled = false; epoch++; requests.clear(); tasks.clear(); finished.clear(); queue = []; planOffer = null; planReview = null; editingPlan = false;
      commandConfirmation = null; commandHeard = false;
      clearUtterance(); clear(wakeTimer); stopPlayback(); io.listen(false); setPhase('off');
    }
    function enable(pending = []) {
      disable(); enabled = true; activeUntil = 0; pending.forEach(id => tasks.add(id));
      if (busy()) pause(); else resume();
    }
    function beginRequest() {
      if (!enabled) return null;
      planOffer = null; planReview = null; editingPlan = false; queue = [];
      commandConfirmation = null; commandHeard = false;
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
      if (commandConfirmation?.taskId === taskId) commandConfirmation = null;
      finished.add(taskId);
      if (task?.plan_suggestion || (typeof task?.answer === 'string' && task.answer.trim())) {
        queue.push({ id: taskId, plan: task.plan_suggestion && !task.plan_trial_used
          ? { taskId, chatId: task.chat_id, originalRequest: task.plan_original_request } : null });
      }
      drain();
    }
    function taskLost(taskId) {
      if (tasks.has(taskId) || planReview?.taskId === taskId || commandConfirmation?.taskId === taskId) { disable(); io.error(new Error('Связь с задачей потеряна. Проверьте её состояние перед включением голоса.')); }
    }
    function taskPaused(taskId, confirmation) {
      if (!enabled) return;
      if (confirmation?.kind === 'deletion' && confirmation.confirmation_id) {
        if (commandConfirmation?.taskId === taskId && commandConfirmation.confirmationId === confirmation.confirmation_id) return;
        tasks.delete(taskId); clearUtterance(); clear(wakeTimer); stopPlayback();
        planOffer = null; planReview = null; editingPlan = false; commandHeard = false;
        commandConfirmation = { taskId, confirmationId: confirmation.confirmation_id };
        queue = [{ id: taskId, confirmation: commandConfirmation }]; drain();
      } else if (tasks.has(taskId)) { pause(); setPhase('confirming'); }
    }
    function commandDecisionResolved(taskId) {
      if (!enabled) return;
      if (commandConfirmation?.taskId === taskId) commandConfirmation = null;
      queue = []; tasks.add(taskId); pause();
    }
    function taskPlanReview(taskId, review) {
      if (!enabled || (planReview?.taskId === taskId && planReview.revision === review.revision)) return;
      tasks.delete(taskId); clearUtterance(); clear(wakeTimer); stopPlayback();
      planOffer = null; editingPlan = false;
      reviewHeard = false;
      planReview = { taskId, revision: review.revision };
      queue = [{ id: taskId, review: planReview }]; drain();
    }
    function planReviewResolved(taskId) {
      if (!enabled) return;
      if (planReview?.taskId === taskId) { planReview = null; editingPlan = false; }
      queue = []; tasks.add(taskId); pause();
    }
    function editPlan(taskId) {
      if (!enabled || planReview?.taskId !== taskId) return;
      queue = []; stopPlayback(); clearUtterance(); editingPlan = true; resume();
    }
    function submitReview(changes) {
      const review = planReview, savedEpoch = epoch;
      if (!review) return;
      clearUtterance(); tasks.add(review.taskId); pause();
      Promise.resolve().then(() => {
        if (enabled && epoch === savedEpoch) return io.reviewPlan(review, changes);
      }).catch(error => {
        if (enabled && epoch === savedEpoch) {
          // A lost HTTP response may still mean the server accepted execution.
          disable(); io.error(error);
        }
      });
    }
    function transcript(text, final) {
      if (!enabled || busy()) return;
      const clean = text.trim();
      if (phase === 'awaiting_deletion') {
        if (!final) return;
        const words = clean.toLocaleLowerCase('ru').replace(/^иру[\s,]*/u, '').replace(/[.!?,]+$/u, '').trim();
        const accepted = /^(да|подтверждаю|да удаляй|да, удаляй|удаляй)$/u.test(words);
        if (!accepted && !/^(нет|не удаляй|отмена)$/u.test(words)) return;
        const offer = commandConfirmation, savedEpoch = epoch;
        tasks.add(offer.taskId); pause();
        Promise.resolve().then(() => {
          if (enabled && epoch === savedEpoch) return io.chooseCommand(offer, accepted);
        }).catch(error => {
          if (enabled && epoch === savedEpoch) { disable(); io.error(error); }
        });
        return;
      }
      if (phase === 'awaiting_plan_review') {
        if (!final) return;
        const words = clean.toLocaleLowerCase('ru').replace(/^иру[\s,]*/u, '').replace(/[.!?,]+$/u, '').trim();
        if (/^(нет|нет изменений|ничего не менять|всё устраивает|все устраивает|нет запускай|нет, запускай)$/u.test(words)) submitReview('');
        else if (/^(да|да изменить|да, изменить|хочу изменить|изменить)$/u.test(words)) editPlan(planReview.taskId);
        return;
      }
      if (phase === 'editing_plan') {
        if (final && clean) utterance = [utterance, clean].filter(Boolean).join(' ');
        clear(silenceTimer);
        silenceTimer = later(() => {
          if (enabled && phase === 'editing_plan' && utterance.trim()) submitReview(utterance.trim());
        }, 1200);
        return;
      }
      if (phase === 'awaiting_plan') {
        if (!final) return;
        const words = clean.toLocaleLowerCase('ru').replace(/^иру[\s,]*/u, '').replace(/[.!?,]+$/u, '').trim();
        const accepted = /^(да|запускай|запустить|да запускай|да, запускай)$/u.test(words);
        if (!accepted && !/^(нет|не надо|без плана|отмена)$/u.test(words)) return;
        const offer = planOffer, savedEpoch = epoch;
        planOffer = null; pause();
        Promise.resolve().then(() => {
          if (enabled && epoch === savedEpoch) return io.choosePlan(offer, accepted);
        }).catch(error => { if (enabled && epoch === savedEpoch) io.error(error); })
          .finally(() => { if (enabled && epoch === savedEpoch && !busy()) resume(true); });
        return;
      }
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
      }, 1000);
    }
    return { enable, disable, beginRequest, endRequest, requestLost, watchTask, taskFinished, taskLost,
      taskPaused, commandDecisionResolved, taskPlanReview, planReviewResolved, editPlan, transcript, stopSpeech, get enabled() { return enabled; }, get phase() { return phase; } };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { createVoiceSession };
  else root.createVoiceSession = createVoiceSession;
})(globalThis);
