const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createVoiceSession } = require('../ui/js/voice-session.js');

function setup() {
  let time = 100, nextId = 0, listening = false;
  const timers = new Map(), submitted = [], spoken = [], errors = [];
  const session = createVoiceSession({
    now: () => time,
    setTimeout: (fn, delay) => { const id = ++nextId; timers.set(id, { fn, at: time + delay }); return id; },
    clearTimeout: id => timers.delete(id),
    state() {}, stopAudio() {}, listen: value => { listening = value; },
    error: error => errors.push(error),
    submit: text => { submitted.push(text); session.beginRequest(); },
    speak: (id, signal, onSpeaking) => new Promise(resolve => { spoken.push({ id, signal, onSpeaking, resolve }); }),
  });
  function advance(ms) {
    time += ms;
    for (const [id, timer] of [...timers]) if (timer.at <= time && timers.delete(id)) timer.fn();
  }
  session.enable();
  return { session, submitted, spoken, errors, advance, get listening() { return listening; } };
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
