const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
function setup(android = true) {
  const runs = [], received = [], timers = new Map();
  let adapter, timer = 0;
  class Recognition {
    constructor() { runs.push(this); }
    start() {}
    abort() { this.aborted = true; }
    emit(text, final = true, index = 0) {
      this.results ||= [];
      const result = [{ transcript: text }]; result.isFinal = final;
      this.results[index] = result;
      this.onresult?.({ resultIndex: index, results: this.results });
    }
  }
  const element = { addEventListener() {}, classList: { toggle() {} }, setAttribute() {} };
  vm.runInNewContext(fs.readFileSync(require.resolve('../ui/js/voice.js'), 'utf8'), {
    navigator: { userAgent: android ? 'Android Chrome/140' : 'Windows Chrome/140' },
    window: { SpeechRecognition: Recognition, addEventListener() {} },
    document: { getElementById: () => ({ ...element }), addEventListener() {} },
    setTimeout(fn) { const id = ++timer; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
    createVoiceSession(io) {
      adapter = io;
      return { transcript: (...args) => received.push(args), disable() { io.listen(false); } };
    },
  });
  adapter.listen(true);
  return { runs, received, adapter, advance() { for (const [id, fn] of [...timers]) { timers.delete(id); fn(); } } };
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
