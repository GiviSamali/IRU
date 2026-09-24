const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createVoiceSession } = require('../ui/js/voice-session.js');

function setup(overrides = {}) {
  let time = 100, nextId = 0, listening = false;
  const timers = new Map(), submitted = [], spoken = [], errors = [], choices = [], reviews = [], commandChoices = [], cues = [];
  const session = createVoiceSession({
    now: () => time,
    setTimeout: (fn, delay) => { const id = ++nextId; timers.set(id, { fn, at: time + delay }); return id; },
    clearTimeout: id => timers.delete(id),
    cue: kind => cues.push(kind),
    state() {}, stopAudio() {}, listen: value => { listening = value; },
    error: error => errors.push(error),
    choosePlan: (offer, accepted) => choices.push({ offer, accepted }),
    reviewPlan: (review, changes) => { reviews.push({ review, changes }); session.planReviewResolved(review.taskId); },
    chooseCommand: (offer, accepted) => { commandChoices.push({ offer, accepted }); session.commandDecisionResolved(offer.taskId); },
    submit: text => { submitted.push(text); session.beginRequest(); },
    speak: (id, signal, onSpeaking) => new Promise(resolve => { spoken.push({ id, signal, onSpeaking, resolve }); }),
    ...overrides,
  });
  function advance(ms) {
    time += ms;
    for (const [id, timer] of [...timers]) if (timer.at <= time && timers.delete(id)) timer.fn();
  }
  session.enable();
  return { session, submitted, spoken, errors, choices, reviews, commandChoices, cues, advance, get listening() { return listening; } };
}

test('wake word sends only final text after silence, ignoring background speech', () => {
  const h = setup();
  h.session.transcript('случайный разговор', true); h.advance(2000);
  assert.deepEqual(h.submitted, []);
  h.session.transcript('Иру открой', false); h.advance(500);
  h.session.transcript('Иру открой блокнот', true); h.advance(1500);
  assert.deepEqual(h.submitted, ['открой блокнот']);
  assert.equal(h.listening, false);
  h.session.transcript('ещё команда', true); h.advance(2000);
  assert.equal(h.submitted.length, 1);
});

test('only tracked terminal answer is spoken; duplicate polling is silent', async () => {
  const h = setup(), ticket = h.session.beginRequest();
  h.session.endRequest(ticket, 'task');
  h.session.taskFinished('foreign', { answer: 'Не надо' });
  assert.equal(h.spoken.length, 0);
  h.session.taskFinished('task', { answer: 'Готово', commands: ['private'] });
  assert.equal(h.spoken[0].id, 'task');
  h.session.taskFinished('task', { answer: 'Готово' });
  assert.equal(h.spoken.length, 1);
  h.spoken[0].resolve(); await Promise.resolve();
  assert.equal(h.session.phase, 'listening');
});

test('stop during speech aborts audio and never submits or cancels a task', () => {
  const h = setup(); h.session.watchTask('task'); h.session.taskFinished('task', { answer: 'Готово' });
  h.spoken[0].onSpeaking();
  h.session.transcript('открой браузер', true); h.advance(2000);
  assert.deepEqual(h.submitted, []);
  h.session.transcript('стоп', false);
  assert.equal(h.spoken[0].signal.aborted, true);
  assert.equal(h.session.phase, 'listening');
  assert.deepEqual(h.submitted, []);
});

test('confirm state never listens; decline can resume without reading command', () => {
  const h = setup(); h.session.watchTask('task'); h.session.taskPaused('task');
  assert.equal(h.session.phase, 'confirming'); assert.equal(h.listening, false);
  h.session.transcript('да выполни', true); h.advance(2000);
  assert.deepEqual(h.submitted, []);
  h.session.taskFinished('task', {});
  assert.equal(h.spoken.length, 0); assert.equal(h.listening, true);
});

test('switching chat/logout discards pending synthesis and late requests', async () => {
  const h = setup(), ticket = h.session.beginRequest();
  h.session.endRequest(ticket, 'task'); h.session.taskFinished('task', { answer: 'Старый ответ' });
  h.session.disable(); h.session.enable();
  h.spoken[0].onSpeaking(); h.spoken[0].resolve(); await Promise.resolve();
  assert.equal(h.session.phase, 'idle');
  h.session.watchTask('late', ticket); h.session.taskFinished('late', { answer: 'Поздно' });
  assert.equal(h.spoken.length, 1);
});

test('continuation expires and requires a new wake word', () => {
  const h = setup(); h.session.watchTask('task'); h.session.taskFinished('task', {});
  h.advance(10001); assert.equal(h.session.phase, 'idle');
  h.session.transcript('открой блокнот', true); h.advance(2000);
  assert.deepEqual(h.submitted, []);
});

test('work started during speech stops playback and suspends recognition', () => {
  const h = setup(); h.session.watchTask('one'); h.session.taskFinished('one', { answer: 'Готово' });
  h.spoken[0].onSpeaking(); h.session.beginRequest();
  assert.equal(h.spoken[0].signal.aborted, true); assert.equal(h.listening, false);
});

test('multiple tasks keep microphone off until all have completed', () => {
  const h = setup(); h.session.watchTask('one'); h.session.watchTask('two');
  h.session.taskFinished('one', { answer: 'Первый ответ' });
  assert.equal(h.spoken.length, 0); assert.equal(h.listening, false);
  h.session.taskFinished('two', { answer: 'Второй ответ' });
  assert.equal(h.spoken.length, 1);
});

test('lost task disables voice instead of accepting another command', () => {
  const h = setup(); h.session.watchTask('task'); h.session.taskLost('task');
  assert.equal(h.session.enabled, false); assert.equal(h.listening, false);
  assert.equal(h.errors.length, 1);
});

test('interim recognition alone cannot execute a command', () => {
  const h = setup(); h.session.transcript('Иру удали файл', false); h.advance(1500);
  assert.deepEqual(h.submitted, []);
});

test('a stale network failure cannot disable a new voice session', () => {
  const h = setup(), ticket = h.session.beginRequest();
  h.session.disable(); h.session.enable(); h.session.requestLost(ticket);
  assert.equal(h.session.enabled, true);
  const current = h.session.beginRequest(); h.session.requestLost(current);
  assert.equal(h.session.enabled, false);
});


async function offerPlan(h, extra = {}) {
  h.session.watchTask('plan');
  h.session.taskFinished('plan', { plan_suggestion: 'Сложная задача', chat_id: 1,
    plan_original_request: 'Сделай отчёт', ...extra });
  h.spoken[0].onSpeaking();
  h.session.transcript('да', true);
  assert.equal(h.choices.length, 0);
  h.spoken[0].resolve(); await Promise.resolve();
}
for (const [phrase, accepted] of [['да', true], ['Иру запускай', true], ['нет', false]]) {
  test(`plan offer accepts explicit final choice once: ${phrase}`, async () => {
    const h = setup(); await offerPlan(h);
    assert.equal(h.session.phase, 'awaiting_plan');
    h.session.transcript(phrase, false); assert.equal(h.choices.length, 0);
    h.session.transcript('да наверное потом', true); assert.equal(h.choices.length, 0);
    h.session.transcript(phrase, true); h.session.transcript(phrase, true);
    await Promise.resolve();
    assert.deepEqual(h.choices, [{ offer: { taskId: 'plan', chatId: 1, originalRequest: 'Сделай отчёт' }, accepted }]);
    assert.deepEqual(h.submitted, []);
  });
}
test('plan confirmation expires and cannot survive chat reset', async () => {
  const h = setup(); await offerPlan(h); h.advance(30000);
  h.session.transcript('да', true); await Promise.resolve();
  assert.equal(h.choices.length, 0);
  h.session.disable(); h.session.enable(); h.session.transcript('да', true);
  assert.equal(h.choices.length, 0);
});
test('exhausted plan trial is spoken but never accepts voice consent', async () => {
  const h = setup(); await offerPlan(h, { plan_trial_used: true });
  assert.equal(h.session.phase, 'listening'); assert.equal(h.choices.length, 0);
});
test('voice dispatch waits one second of silence', () => {
  const h = setup(); h.session.transcript('Иру привет', true);
  h.advance(999); assert.equal(h.submitted.length, 0);
  h.advance(1); assert.deepEqual(h.submitted, ['привет']);
});

test('empty speech response cannot arm plan consent', async () => {
  const h = setup(); h.session.watchTask('plan');
  h.session.taskFinished('plan', { plan_suggestion: 'Plan', chat_id: 1, plan_original_request: 'Request' });
  h.spoken[0].resolve(); await Promise.resolve();
  assert.equal(h.session.phase, 'listening'); assert.equal(h.choices.length, 0);
});

test('draft review says yes to edit and no to execute after repeated revisions', async () => {
  const h = setup(); h.session.watchTask('plan');
  h.session.taskPlanReview('plan', { revision: 'v1' });
  h.spoken[0].onSpeaking();
  h.session.transcript('нет', true); // Echo during TTS must not approve.
  assert.equal(h.reviews.length, 0);
  h.spoken[0].resolve(); await Promise.resolve();
  assert.equal(h.session.phase, 'awaiting_plan_review');
  h.advance(60000); assert.equal(h.session.phase, 'awaiting_plan_review');
  h.session.transcript('да', false); assert.equal(h.session.phase, 'awaiting_plan_review');
  h.session.transcript('да', true);
  assert.equal(h.session.phase, 'editing_plan'); assert.equal(h.listening, true);
  assert.equal(h.reviews.length, 0);
  h.session.transcript('Добавь сравнение стоимости', true); h.advance(1200);
  await Promise.resolve();
  assert.equal(h.reviews[0].changes, 'Добавь сравнение стоимости');
  assert.equal(h.reviews[0].review.revision, 'v1');
  assert.equal(h.listening, false);
  h.session.taskPlanReview('plan', { revision: 'v2' });
  h.session.taskPlanReview('plan', { revision: 'v2' });
  assert.equal(h.spoken.length, 2);
  h.spoken[1].onSpeaking(); h.spoken[1].resolve(); await Promise.resolve();
  h.session.transcript('да', true);
  h.session.transcript('Убери Excel', true); h.advance(1200); await Promise.resolve();
  h.session.taskPlanReview('plan', { revision: 'v3' });
  h.spoken[2].onSpeaking(); h.spoken[2].resolve(); await Promise.resolve();
  h.session.transcript('нет', true); h.session.transcript('нет', true); await Promise.resolve();
  assert.equal(h.reviews.length, 3);
  assert.equal(h.reviews[2].review.revision, 'v3'); assert.equal(h.reviews[2].changes, '');
  assert.equal(h.listening, false);
  h.session.transcript('другая команда', true); h.advance(2000);
  assert.deepEqual(h.submitted, []);
});

test('missing review audio keeps confirmation in chat and cannot arm voice approval', async () => {
  const h = setup(); h.session.watchTask('plan'); h.session.taskPlanReview('plan', { revision: 'v1' });
  h.spoken[0].resolve(); await Promise.resolve();
  assert.equal(h.session.phase, 'confirming'); assert.equal(h.listening, false);
  h.session.transcript('нет', true); await Promise.resolve();
  assert.deepEqual(h.reviews, []);
});

test('stop review speech does not execute and chat reset drops edit dictation', async () => {
  const h = setup(); h.session.watchTask('plan'); h.session.taskPlanReview('plan', { revision: 'v1' });
  h.spoken[0].onSpeaking(); h.session.transcript('стоп', true);
  assert.equal(h.spoken[0].signal.aborted, true); assert.deepEqual(h.reviews, []);
  h.session.editPlan('plan'); h.session.transcript('Изменение', true);
  h.session.disable(); h.advance(2000); await Promise.resolve();
  assert.deepEqual(h.reviews, []); assert.equal(h.listening, false);
});

test('uncertain approval response disables voice rather than listening during possible work', async () => {
  const h = setup({ reviewPlan: () => Promise.reject(new Error('network lost')) });
  h.session.watchTask('plan'); h.session.taskPlanReview('plan', { revision: 'v1' });
  h.spoken[0].onSpeaking(); h.spoken[0].resolve(); await Promise.resolve();
  h.session.transcript('нет', true);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.session.enabled, false); assert.equal(h.listening, false);
  assert.equal(h.errors.length, 1);
});

for (const [answer, accepted] of [['да', true], ['нет', false]]) {
  test(`ordinary confirmation handles ${answer} once, independently of PLAN answers`, async () => {
    const h = setup(); h.session.watchTask('task');
    h.session.taskPaused('task', { kind: 'command', voice_allowed: true, confirmation_id: 'delete-1' });
    h.session.taskPaused('task', { kind: 'command', voice_allowed: true, confirmation_id: 'delete-1' });
    assert.equal(h.spoken.length, 1);
    h.spoken[0].onSpeaking(); h.session.transcript(answer, true);
    assert.deepEqual(h.commandChoices, []);
    h.spoken[0].resolve(); await Promise.resolve();
    assert.equal(h.session.phase, 'awaiting_command'); assert.equal(h.listening, true);
    h.session.transcript(answer, false); assert.deepEqual(h.commandChoices, []);
    h.session.transcript(answer, true); h.session.transcript(answer, true); await Promise.resolve();
    assert.deepEqual(h.commandChoices, [{ offer: { taskId: 'task', confirmationId: 'delete-1' }, accepted }]);
    assert.deepEqual(h.reviews, []); assert.deepEqual(h.choices, []); assert.deepEqual(h.submitted, []);
    assert.equal(h.listening, false);
    h.session.taskPaused('task', { kind: 'command', voice_allowed: true, confirmation_id: 'delete-2' });
    assert.equal(h.spoken.length, 2);
    h.spoken[1].onSpeaking(); h.spoken[1].resolve(); await Promise.resolve();
    assert.equal(h.session.phase, 'awaiting_command');
    h.advance(60000); assert.equal(h.commandChoices.length, 1);
  });
}

test('silent or stopped ordinary question never arms approval; reset discards consent', async () => {
  const h = setup(); h.session.watchTask('task');
  h.session.taskPaused('task', { kind: 'command', voice_allowed: true, confirmation_id: 'd1' });
  h.spoken[0].resolve(); await Promise.resolve();
  h.session.transcript('да', true); assert.deepEqual(h.commandChoices, []);
  assert.equal(h.session.phase, 'confirming'); assert.equal(h.listening, false);
  h.session.taskPaused('task', { kind: 'command', voice_allowed: true, confirmation_id: 'd2' });
  h.spoken[1].onSpeaking(); h.session.stopSpeech();
  h.session.transcript('да', true); assert.deepEqual(h.commandChoices, []);
  h.session.disable(); h.spoken[1].resolve(); await Promise.resolve();
  assert.equal(h.session.phase, 'off');
});

for (const kind of ['deletion', 'dangerous']) {
  test(`${kind} accepts buttons only and never arms a voice decision`, async () => {
    const h = setup(); h.session.watchTask('task');
    h.session.taskPaused('task', {kind, confirmation_id: 'risk', voice_allowed: false});
    assert.equal(h.session.phase, 'confirming'); assert.equal(h.listening, false);
    h.session.transcript('да', true); await Promise.resolve();
    assert.deepEqual(h.commandChoices, []); assert.deepEqual(h.spoken, []);
  });
}

test('sleep clears dictation and requires wake word without sending a command', () => {
  const h = setup(); h.session.transcript('Иру', true);
  h.session.transcript('ещё не отправлено', true); h.session.transcript('усни', true); h.advance(2000);
  assert.equal(h.session.phase, 'idle'); assert.equal(h.session.enabled, true);
  h.session.transcript('сделай файл', true); h.advance(2000); assert.deepEqual(h.submitted, []);
  h.session.transcript('Иру создай файл', true); h.advance(2000);
  assert.deepEqual(h.submitted, ['создай файл']);
});

test('sleep during speech stops audio; pending PLAN requires wake and fresh review', async () => {
  const h = setup(); h.session.watchTask('plan'); h.session.taskPlanReview('plan', {revision: 'v1'});
  h.spoken[0].onSpeaking(); h.session.transcript('Иру, усни', true);
  assert.equal(h.spoken[0].signal.aborted, true); assert.equal(h.session.phase, 'idle');
  h.session.transcript('нет', true); await Promise.resolve(); assert.deepEqual(h.reviews, []);
  h.session.transcript('Иру', true); assert.equal(h.spoken.length, 2);
  h.spoken[1].onSpeaking(); h.spoken[1].resolve(); await Promise.resolve();
  assert.equal(h.session.phase, 'awaiting_plan_review');
});

test('microphone cues track readiness, silence and sleep, not repeated phases or TTS stop listening', async () => {
  const h = setup(); assert.deepEqual(h.cues, ['on']);
  h.session.transcript('Иру', true); h.session.transcript('Иру', true);
  assert.deepEqual(h.cues, ['on', 'on']);
  h.session.transcript('усни', true); assert.equal(h.cues.at(-1), 'off');
  const count = h.cues.length; h.session.transcript('фон', true); assert.equal(h.cues.length, count);
  h.session.transcript('Иру', true); assert.equal(h.cues.at(-1), 'on');
  h.session.watchTask('t'); assert.equal(h.cues.at(-1), 'off');
  const beforeSpeech = h.cues.length; h.session.taskFinished('t', {answer: 'Ответ'});
  h.spoken[0].onSpeaking(); assert.equal(h.cues.length, beforeSpeech);
  h.spoken[0].resolve(); await Promise.resolve(); assert.equal(h.cues.at(-1), 'on');
  h.advance(10001); assert.equal(h.cues.at(-1), 'off');
  h.session.disable(); assert.equal(h.cues.at(-1), 'off');
});

test('sleep word clears pending speech and requires wake word again', () => {
  const h = setup();
  h.session.transcript('Иру открой блокнот', true); h.advance(300);
  h.session.transcript('усни', true); h.advance(2000);
  assert.equal(h.session.enabled, true); assert.equal(h.session.phase, 'idle');
  assert.deepEqual(h.submitted, []); assert.equal(h.listening, true);
  h.session.transcript('открой браузер', true); h.advance(2000);
  assert.deepEqual(h.submitted, []);
  h.session.transcript('ИРУ привет', true); h.advance(1000);
  assert.deepEqual(h.submitted, ['привет']);
});

test('sleep interrupts audio without late completion waking session', async () => {
  const h = setup(); h.session.watchTask('task'); h.session.taskFinished('task', { answer: 'Ответ' });
  h.spoken[0].onSpeaking(); h.session.transcript('Иру, усни!', true);
  assert.equal(h.spoken[0].signal.aborted, true);
  h.spoken[0].resolve(); await Promise.resolve();
  assert.equal(h.session.phase, 'idle'); assert.deepEqual(h.submitted, []);
});

test('sleep dismisses voice plan consent without accepting or declining plan', async () => {
  const h = setup(); await offerPlan(h); h.session.transcript('усни', true);
  h.session.transcript('да', true); h.advance(31000); await Promise.resolve();
  assert.equal(h.session.phase, 'idle'); assert.deepEqual(h.choices, []);
});

test('sleep requires a complete standalone phrase and is ignored during work', () => {
  const h = setup(); h.session.transcript('Иру', true);
  h.session.transcript('усни', false); assert.equal(h.session.phase, 'listening');
  h.session.transcript('напиши слово усни', true); h.advance(1000);
  assert.deepEqual(h.submitted, ['напиши слово усни']);
  h.session.transcript('усни', true); assert.equal(h.session.phase, 'working');
});
