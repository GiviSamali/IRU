const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
function setup(android = true, options = {}) {
  const runs = [], received = [], submitted = [], notices = [], timers = new Map(), windowEvents = new Map(), documentEvents = new Map();
  let adapter, timer = 0, time = 100, enabled = true;
  const later = (fn, delay = 0) => { const id = ++timer; timers.set(id, { fn, at: time + delay, delay }); return id; };
  const clear = id => timers.delete(id);
  class Recognition {
    constructor() { runs.push(this); }
    start() {
      if (options.throwStarts > 0) { options.throwStarts--; throw Object.assign(new Error('busy'), { name: 'InvalidStateError' }); }
      if (!options.missingStart) this.onstart?.();
    }
    abort() { this.aborted = true; }
    emit(text, final = true, index) {
      this.results ||= [];
      index ??= this.results.length;
      const result = [{ transcript: text }]; result.isFinal = final;
      this.results[index] = result;
      this.onresult?.({ resultIndex: index, results: this.results });
    }
  }
  const element = { addEventListener() {}, classList: { toggle() {} }, setAttribute() {} };
  const window = { SpeechRecognition: Recognition, addEventListener: (name, fn) => windowEvents.set(name, fn) };
  const document = { hidden: false, getElementById: () => ({ ...element }), addEventListener: (name, fn) => documentEvents.set(name, fn) };
  vm.runInNewContext(fs.readFileSync(require.resolve('../ui/js/voice.js'), 'utf8'), {
    navigator: { userAgent: android ? 'Android Chrome/140' : 'Windows Chrome/140' }, window, document,
    Date: { now: () => time }, setTimeout: later, clearTimeout: clear,
    showToast: (...args) => notices.push(args),
    createVoiceSession(io) {
      adapter = io;
      if (options.realSession) {
        const { createVoiceSession } = require('../ui/js/voice-session.js');
        return createVoiceSession({ ...io, now: () => time, setTimeout: later, clearTimeout: clear, submit: text => submitted.push(text) });
      }
      return { transcript: (...args) => received.push(args), disable() { enabled = false; io.listen(false); }, get enabled() { return enabled; } };
    },
  });
  if (options.realSession) window.iruVoice.enable([], { continuous: true }); else adapter.listen(true);
  function advance(ms = 250) {
    const end = time + ms;
    while (true) {
      const due = [...timers].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      time = due[1].at; timers.delete(due[0]); due[1].fn();
    }
    time = end;
  }
  return { runs, received, submitted, notices, adapter, advance, timers, window, document,
    returnToPage() { documentEvents.get('visibilitychange')?.(); },
    resume() { documentEvents.get('resume')?.(); },
    online() { windowEvents.get('online')?.(); },
    jump(ms) { time += ms; },
  };
}

test('Android accepts one final per cycle, ignoring cumulative finals at new indices', () => {
  const h = setup(), first = h.runs[0];
  assert.equal(first.continuous, false);
  first.emit('Иру на первом открой Comet', false);
  first.emit('Иру на первом открой Comet');
  first.emit('Иру на первом открой Comet на втором открой Яндекс', true, 1);
  assert.equal(first.aborted, true);
  assert.deepEqual(h.received.filter(x => x[1]), [['Иру на первом открой Comet', true]]);
  h.advance(); h.runs[1].emit('на втором открой Яндекс');
  assert.deepEqual(h.received.filter(x => x[1]), [['Иру на первом открой Comet', true], ['на втором открой Яндекс', true]]);
});
test('intentional repetitions survive inside a phrase and across cycles', () => {
  const h = setup(); h.runs[0].emit('Иру скажи да да'); h.advance(); h.runs[1].emit('скажи да да');
  assert.deepEqual(h.received, [['Иру скажи да да', true], ['скажи да да', true]]);
});
test('pause cancels restart and rejects stale callbacks', () => {
  const h = setup(), first = h.runs[0], stale = first.onresult;
  first.emit('Иру задача'); h.adapter.listen(false); h.advance();
  assert.equal(h.runs.length, 1);
  stale({ resultIndex: 0, results: first.results });
  assert.equal(h.received.length, 1);
});
test('desktop keeps incremental fragments and rejects repeated indices', () => {
  const h = setup(false), first = h.runs[0];
  assert.equal(first.continuous, true);
  first.emit('Иру повтори'); first.emit('да', true, 1); first.emit('да', true, 2);
  first.onresult({ resultIndex: 0, results: first.results });
  assert.deepEqual(h.received, [['Иру повтори', true], ['да', true], ['да', true]]);
});
test('natural recognition end restarts Android listening', () => {
  const h = setup(); h.runs[0].onend(); h.advance(); assert.equal(h.runs.length, 2);
});


for (const android of [false, true]) {
  test(`wake word survives 30 minutes of silence without provider end: ${android ? 'Android' : 'desktop'}`, async () => {
    const h = setup(android, { realSession: true });
    h.advance(30 * 60 * 1000);
    assert.equal(h.window.iruVoice.enabled, true);
    assert.equal(h.window.iruVoice.phase, 'idle');
    assert.deepEqual(h.submitted, []);
    assert.ok(h.runs.length >= 15 && h.runs.length < 30);
    assert.equal(h.runs.filter(run => !run.aborted).length, 1);
    h.runs.at(-1).emit('случайный разговор'); h.advance(2000); assert.deepEqual(h.submitted, []);
    h.runs.at(-1).emit('ИРУ.'); h.advance(1100); assert.deepEqual(h.submitted, []);
    h.runs.at(-1).emit('Иру проверка связи'); h.advance(1100); await Promise.resolve();
    assert.deepEqual(h.submitted, ['проверка связи']);
    assert.equal(h.notices.length, 0);
  });
}

for (const error of ['network', 'no-speech', 'aborted']) {
  test(`transient ${error} retries without onend and ignores late callbacks`, () => {
    const h = setup(false), old = h.runs[0], lateEnd = old.onend, lateError = old.onerror;
    old.onerror({ error });
    assert.equal(h.window.iruVoice.enabled, true); assert.equal(old.aborted, true);
    h.advance(error === 'network' ? 1000 : 250); assert.equal(h.runs.length, 2);
    lateEnd(); lateError({ error: 'not-allowed' }); h.advance(500);
    assert.equal(h.runs.length, 2); assert.equal(h.window.iruVoice.enabled, true);
    assert.equal(h.notices.length, 0);
  });
}

test('persistent network failures back off to 15 seconds without exhausting wake standby', () => {
  const h = setup(false);
  for (let attempt = 0; attempt < 12; attempt++) {
    h.runs.at(-1).onerror({ error: 'network' });
    h.adapter.listen(true); h.adapter.listen(true);
    assert.equal(h.timers.size, 1);
    const delay = [...h.timers.values()][0].delay;
    assert.equal(delay, Math.min(15000, 1000 * 2 ** Math.min(attempt, 4)));
    h.advance(delay - 1); assert.equal(h.runs.length, attempt + 1);
    h.advance(1); assert.equal(h.runs.length, attempt + 2);
  }
  assert.equal(h.window.iruVoice.enabled, true); assert.equal(h.runs.filter(run => !run.aborted).length, 1);
  h.adapter.listen(false); const count = h.runs.length; h.advance(5 * 60 * 1000);
  assert.equal(h.runs.length, count); assert.equal(h.timers.size, 0);
});

for (const error of ['not-allowed', 'service-not-allowed', 'audio-capture', 'language-not-supported']) {
  test(`fatal ${error} requires user action and never auto-enables microphone`, () => {
    const h = setup(false); h.runs[0].onerror({ error }); h.online(); h.returnToPage(); h.advance(10 * 60 * 1000);
    assert.equal(h.window.iruVoice.enabled, false); assert.equal(h.runs.length, 1); assert.equal(h.notices.length, 1);
    assert.equal(h.timers.size, 0);
  });
}

test('missing start event and transient start exception recover without duplicate capture', () => {
  const missing = setup(false, { missingStart: true }); missing.advance(11000);
  assert.equal(missing.runs.length, 2); assert.equal(missing.runs[0].aborted, true);
  missing.adapter.listen(false);
  const busy = setup(false, { throwStarts: 1 }); busy.advance(1000);
  assert.equal(busy.runs.length, 2); assert.equal(busy.window.iruVoice.enabled, true);
  busy.adapter.listen(false);
});

test('return after suspended timers replaces stale recognition; online speeds retry but not permission denial', () => {
  const h = setup(false); h.jump(5 * 60 * 1000); h.returnToPage(); h.advance(0);
  assert.equal(h.runs.length, 2); assert.equal(h.runs[0].aborted, true);
  h.returnToPage(); h.online(); assert.equal(h.runs.length, 2);
  h.runs[1].onerror({ error: 'network' }); h.online(); h.advance(0);
  assert.equal(h.runs.length, 3);
  h.adapter.listen(false); h.returnToPage(); h.online(); h.advance(5 * 60 * 1000);
  assert.equal(h.runs.length, 3);
});

test('standby transport renewal does not reset sleep or re-arm a confirmation', async () => {
  const h = setup(false, { realSession: true }); h.runs[0].emit('усни');
  h.advance(10 * 60 * 1000); assert.equal(h.window.iruVoice.phase, 'idle');
  h.runs.at(-1).emit('да'); h.advance(1500); assert.deepEqual(h.submitted, []);
  h.runs.at(-1).emit('Иру проверка'); h.advance(1500); await Promise.resolve();
  assert.deepEqual(h.submitted, ['проверка']);
});


test('interim speech progress renews health deadline without cutting active dictation', () => {
  const h = setup(false), first = h.runs[0];
  for (let i=0;i<6;i++) { h.advance(30000); first.emit('ongoing speech', false, 0); }
  assert.equal(h.runs.length,1);
  first.emit('completed sentence',true,0);
  assert.deepEqual(h.received.filter(result=>result[1]),[['completed sentence',true]]);
});


test('browser resume renews an expired capture even while its document is hidden', () => {
  const h=setup(false);h.document.hidden=true;h.jump(5*60*1000);h.resume();h.advance(0);
  assert.equal(h.runs.length,2);assert.equal(h.runs[0].aborted,true);
  h.adapter.listen(false);h.resume();h.advance(5*60*1000);assert.equal(h.runs.length,2);
});
