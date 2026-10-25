/* Voice lifecycle, independent of DOM/audio/network for deterministic tests. */
(function (root) {
  // STT may punctuate the name or spell out the acronym. Match whole words only.
  const wakeName = String.raw`(?:иру|и[\s.]+р[\s.]+у|iru|i[\s.]+r[\s.]+u)`;
  const wake = new RegExp(String.raw`(^|[^\p{L}\p{N}])${wakeName}(?=$|[^\p{L}\p{N}])`, 'iu');
  const separators = /^[\s.,!?…:;—–«»“”"']+/u;
  const wakePrefix = new RegExp(String.raw`^[\s.,!?…:;—–«»“”"']*${wakeName}(?=$|[^\p{L}\p{N}])[\s.,!?…:;—–«»“”"']*`, 'iu');
  const hasWords = text => /[\p{L}\p{N}]/u.test(text);
  const decisionWords = text => text.replace(wakePrefix, '').toLocaleLowerCase('ru').replace(/[.!?,…]+$/u, '').trim();
  function createVoiceSession(io) {
    let enabled = false, epoch = 0, phase = 'off', activeUntil = 0;
    let utterance = '', silenceTimer = null, wakeTimer = null, playback = null;
    let lastHeardAt = null;
    const MAX_SILENCE_AGE_MS = 5000;
    const requests = new Set(), tasks = new Set(), finished = new Set(), backgroundTasks = new Set();
    let continuous=false, humanUntil=0, reportTimer=null;
    let queue = [];
    let commandConfirmation = null, commandHeard = false, standby = false;
    const now = io.now || Date.now, later = io.setTimeout || setTimeout, clear = io.clearTimeout || clearTimeout;
    const readyPhases = new Set(['listening', 'awaiting_command']);
    function setPhase(next) {
      const previous = phase; phase = next; io.state(next);
      if (previous === next) return;
      if (readyPhases.has(next) && !readyPhases.has(previous) || previous === 'off' && next === 'idle') io.cue?.('on');
      else if (readyPhases.has(previous) && !readyPhases.has(next) || next === 'off' && previous !== 'off') io.cue?.('off');
    }
    function clearUtterance() { utterance = ''; lastHeardAt = null; clear(silenceTimer); silenceTimer = null; }
    function stopPlayback() { if (playback) playback.abort(); playback = null; io.stopAudio(); }
    function busy() { return requests.size > 0 || [...tasks].some(id=>!continuous || !backgroundTasks.has(id)); }
    function pause() { clearUtterance(); clear(wakeTimer); stopPlayback(); io.listen(false); setPhase('working'); }
    function resume(fresh = false) {
      if (!enabled || busy() || playback) return;
      if (standby) { setPhase('idle'); io.listen(true); clear(wakeTimer); return; }
      if (commandConfirmation) {
        setPhase(commandHeard ? 'awaiting_command' : 'confirming');
        io.listen(commandHeard); clear(wakeTimer); return;
      }
      if (fresh) activeUntil = now() + 10000;
      setPhase(now() < activeUntil ? 'listening' : 'idle'); io.listen(true); clear(wakeTimer);
      if (phase === 'listening') wakeTimer = later(() => {
        if (enabled && !busy() && phase === 'listening' && !utterance) { activeUntil = 0; setPhase('idle'); }
      }, Math.max(0, activeUntil - now()));
    }
    async function drain() {
      if (!enabled || busy() || playback) return;
      if (continuous && (utterance || now()<humanUntil)) {
        if (!reportTimer) reportTimer=later(()=>{reportTimer=null;drain();},Math.max(100,humanUntil-now()));
        return;
      }
      if (standby) { resume(); return; }
      if (!queue.length) { resume(!continuous); return; }
      const item = queue.shift(), taskId = item.id, savedEpoch = epoch;
      const controller = new AbortController(); playback = controller;
      let heard = false;
      clearUtterance(); clear(wakeTimer);
      setPhase('synthesizing'); io.listen(true); // Control words only; never dictate TTS into chat.
      try {
        await io.speak(taskId, controller.signal, () => {
          if (!controller.signal.aborted && enabled && epoch === savedEpoch) { heard = true; setPhase('speaking'); io.listen(true); }
        }, item.confirmation || item.question);
        if (item.confirmation && !controller.signal.aborted && enabled && epoch === savedEpoch) commandHeard = heard;
      } catch (error) {
        if (!controller.signal.aborted && enabled && epoch === savedEpoch) io.error(error);
      } finally {
        if (playback === controller && enabled && epoch === savedEpoch) {
          playback = null; io.listen(false);
          if (queue.length) drain(); else resume(!continuous || !item.background);
        }
      }
    }
    function stopSpeech() {
      if (!enabled || !['speaking', 'synthesizing'].includes(phase)) return;
      queue = []; stopPlayback(); io.listen(false); resume(true);
    }
    function disable() {
      enabled = false; epoch++; requests.clear(); tasks.clear(); backgroundTasks.clear(); finished.clear(); queue = []; clear(reportTimer); reportTimer=null; humanUntil=0;
      commandConfirmation = null; commandHeard = false; standby = false;
      clearUtterance(); clear(wakeTimer); stopPlayback(); io.listen(false); setPhase('off');
    }
    function enable(pending = [], options = {}) {
      disable(); continuous=options.continuous === true; enabled=true; activeUntil=0;
      pending.forEach(item=>{const id=typeof item==='string'?item:item.id;tasks.add(id);if(continuous && (typeof item==='string'||item.background)) backgroundTasks.add(id);});
      if (busy()) pause(); else resume();
    }
    function beginRequest() {
      if (!enabled) return null;
      if (!continuous) queue = [];
      commandConfirmation = null; commandHeard = false; if(!continuous)standby=false;
      const ticket={epoch};requests.add(ticket);if (!continuous || !playback) pause();return ticket;
    }
    function endRequest(ticket, taskId) {
      if (!enabled || !ticket || ticket.epoch !== epoch || !requests.delete(ticket)) return;
      if (taskId) {tasks.add(taskId);if (!continuous || !backgroundTasks.has(taskId)) {if(!playback)pause();}else drain();} else drain();
    }
    function requestLost(ticket) {
      if (enabled && ticket?.epoch===epoch && requests.has(ticket)) {if(continuous){requests.delete(ticket);resume(true);}else disable();}
    }
    function watchTask(taskId, ticket, background = false) {
      if (!enabled || (ticket !== undefined && (!ticket || ticket.epoch !== epoch)) || finished.has(taskId)) return;
      if (tasks.has(taskId) && (!continuous || backgroundTasks.has(taskId) === background)) return;
      tasks.add(taskId);if(continuous && background){backgroundTasks.add(taskId);drain();}else pause();
    }
    const heardQuestions = new Set();
    function taskInputRequired(taskId, task) {
      const questionId=task?.input_required?.question_id;
      if (!enabled || !questionId || heardQuestions.has(questionId)) return;
      heardQuestions.add(questionId);
      tasks.delete(taskId);
      const background=backgroundTasks.delete(taskId);
      queue.push({id:taskId,background,question:{questionId}});
      drain();
    }
    function taskFinished(taskId, task) {
      if (!enabled || !tasks.delete(taskId)) return;
      const background=backgroundTasks.delete(taskId);
      if (commandConfirmation?.taskId === taskId) commandConfirmation = null;
      finished.add(taskId);
      if (typeof task?.answer === 'string' && task.answer.trim()) {
        queue.push({id:taskId,background,order:task?.created_at || now()});
      }
      if(continuous)queue.sort((a,b)=>Number(a.background)-Number(b.background)||(a.order||0)-(b.order||0));
      drain();
    }
    function taskLost(taskId) {
      if(continuous && backgroundTasks.has(taskId)){tasks.delete(taskId);backgroundTasks.delete(taskId);finished.add(taskId);io.error(new Error('Связь с задачей потеряна; диалог продолжается.'));drain();return;}
      if (tasks.has(taskId) || commandConfirmation?.taskId === taskId) { disable(); io.error(new Error('Связь с задачей потеряна. Проверьте её состояние перед включением голоса.')); }
    }
    function taskPaused(taskId, confirmation) {
      if (!enabled) return;
      if (confirmation?.kind === 'command' && confirmation.voice_allowed === true && confirmation.confirmation_id) {
        if (commandConfirmation?.taskId === taskId && commandConfirmation.confirmationId === confirmation.confirmation_id) return;
        tasks.delete(taskId); if(!continuous){clearUtterance();clear(wakeTimer);stopPlayback();}
        commandHeard = false;
        commandConfirmation = { taskId, confirmationId: confirmation.confirmation_id };
        if(continuous)queue.push({id:taskId,confirmation:commandConfirmation});else queue=[{id:taskId,confirmation:commandConfirmation}];drain();
      } else if(tasks.has(taskId)){if(continuous){resume(true);}else{pause();setPhase('confirming');}}
    }
    function commandDecisionResolved(taskId) {
      if (!enabled) return;
      if (commandConfirmation?.taskId === taskId) commandConfirmation = null;
      if(!continuous)queue=[];tasks.add(taskId);if(continuous && backgroundTasks.has(taskId))drain();else pause();
    }
    function transcript(text, final) {
      if(!enabled)return;
      const clean=text.trim();
      if (['speaking','synthesizing'].includes(phase)) {
        if (decisionWords(clean) === 'стоп') stopSpeech();
        else if (final && decisionWords(clean) === 'усни') {
          clearUtterance(); clear(wakeTimer); queue=[]; standby=true; activeUntil=0;
          stopPlayback(); io.listen(false); resume();
        }
        return;
      }
      if(busy())return;
      if(continuous && clean && !['speaking','synthesizing'].includes(phase))humanUntil=now()+1200;
      if (final && decisionWords(clean) === 'усни') {
        clearUtterance(); clear(wakeTimer); queue = [];
        standby = true; activeUntil = 0; stopPlayback(); io.listen(false); resume(); return;
      }
      if (standby) {
        if (!final || !wake.test(clean)) return;
        standby = false;
        if (commandConfirmation) {
          commandHeard = false;
          queue = [{id:commandConfirmation.taskId, confirmation:commandConfirmation}];
          drain(); return;
        }
        resume(true);
      }
      if (phase === 'awaiting_command') {
        if (!final) return;
        const words = decisionWords(clean);
        const accepted = /^(да|подтверждаю|да выполни|да, выполни|выполняй)$/u.test(words);
        if(!accepted && !/^(нет|не выполняй|отмена)$/u.test(words)){
          if(!continuous)return;commandConfirmation=null;commandHeard=false;resume(true);
        }else{
        const offer = commandConfirmation, savedEpoch = epoch;
        tasks.add(offer.taskId); pause();
        Promise.resolve().then(() => {
          if (enabled && epoch === savedEpoch) return io.chooseCommand(offer, accepted);
        }).catch(error => {
          if (enabled && epoch === savedEpoch) { disable(); io.error(error); }
        });
        return;
        }
      }
      if (!['idle', 'listening'].includes(phase)) return;
      if (lastHeardAt !== null && now()-lastHeardAt > MAX_SILENCE_AGE_MS) {
        clearUtterance(); activeUntil=0; resume();
      }
      const woke = wake.test(clean);
      if (!woke && now() >= activeUntil && !utterance) return;
      if (woke) activeUntil = now() + 10000;
      setPhase('listening'); clear(wakeTimer);
      lastHeardAt=now();
      if (final) {
        const words = (woke ? clean.replace(wake, '$1').replace(separators, '') : clean).trim();
        if (hasWords(words)) utterance = [utterance, words].filter(Boolean).join(' ');
      }
      clear(silenceTimer);
      silenceTimer = later(() => {
        const expired=lastHeardAt !== null && now()-lastHeardAt > MAX_SILENCE_AGE_MS;
        const textToSend = utterance.trim(); clearUtterance();
        if (!enabled || busy()) return;
        if (expired) { activeUntil=0; resume(); return; }
        if (!hasWords(textToSend) || decisionWords(textToSend) === 'стоп') { resume(); return; }
        io.listen(false); setPhase('working');
        const savedEpoch = epoch;
        Promise.resolve(io.submit(textToSend)).then(() => {
          if (enabled && epoch === savedEpoch && !busy() && phase === 'working') resume(true);
        }).catch(error => { if (enabled && epoch === savedEpoch) { io.error(error); resume(true); } });
      }, 1000);
    }
    return { enable, disable, beginRequest, endRequest, requestLost, watchTask, taskFinished, taskInputRequired, taskLost,
      taskPaused, commandDecisionResolved, transcript, stopSpeech, get enabled() { return enabled; }, get phase() { return phase; } };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { createVoiceSession };
  else root.createVoiceSession = createVoiceSession;
})(globalThis);
