const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../ui/js/chat.js'), 'utf8');
const helper = source.slice(source.indexOf('function getPlanLogOutcome('), source.indexOf('function renderStepDetailSection('));
const outcome = vm.runInNewContext(helper + '; getPlanLogOutcome');
test('completed PLAN overrides historical command errors without removing details', () => {
  for (const status of ['done', 'completed', 'completed_with_recovery']) {
    assert.equal(outcome([{ status }]), 'completed');
  }
  assert.ok(source.includes('data-plan-outcome="${getPlanLogOutcome(m.tasks)}"'));
  const css = fs.readFileSync(require.resolve('../ui/css/product-v2-tuning.css'), 'utf8');
  assert.ok(css.indexOf('.cmd-log[data-plan-outcome="completed"]::after') > css.indexOf('.cmd-log:has(.cmd-status.stopped)::after'));
  assert.ok(source.includes('getCommandDetailsText(c, output)'));
});
test('failed, partial, cancelled or unfinished tasks cannot appear successful', () => {
  for (const status of ['failed', 'error', 'blocked', 'partial', 'partial_failure']) {
    assert.equal(outcome([{ status: 'completed' }, { status }]), 'failed');
  }
  assert.equal(outcome([{ status: 'cancelled' }]), 'cancelled');
  for (const status of ['running', 'pending', 'confirm', 'cancelling', '']) {
    assert.equal(outcome([{ status: 'completed' }, { status }]), '');
  }
});
test('ordinary messages and missing task metadata retain existing log behavior', () => {
  for (const tasks of [undefined, null, [], [{ }]]) assert.equal(outcome(tasks), '');
});
