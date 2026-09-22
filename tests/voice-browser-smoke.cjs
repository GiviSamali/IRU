// Run against a local static UI server; all backend/audio/speech traffic is mocked.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage();
    const errors = [], requests = [];
    page.on('pageerror', error => errors.push(error.message));
    let completed = false, counter = 0;
    await page.addInitScript(() => {
      window.testRecognition = null;
      class Recognition {
        start() { this.active = true; window.testRecognition = this; }
        abort() { this.active = false; }
        emit(text) { const result = [{ transcript: text }]; result.isFinal = true; this.onresult?.({ resultIndex: 0, results: [result] }); }
      }
      window.SpeechRecognition = Recognition;
      window.AudioContext = class {
        constructor() { this.state = 'running'; this.destination = {}; }
        async resume() {}
        async decodeAudioData() { return {}; }
        createBufferSource() { return { connect() {}, disconnect() {}, start() {}, stop() { this.onended?.(); } }; }
      };
    });
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'http://127.0.0.1:8769') { await route.abort(); return; }
      if (!url.pathname.startsWith('/api/') && url.pathname !== '/nl_command') { await route.continue(); return; }
      requests.push({ path: url.pathname, body: route.request().postDataJSON() });
      let data = { status: 'ok' };
      if (url.pathname === '/api/voice/config') data.available = true;
      else if (url.pathname === '/nl_command') data = { status: 'ok', task_id: `task-${++counter}`, chat_id: 1 };
      else if (url.pathname.endsWith('/speech')) {
        await route.fulfill({ body: 'mock-ogg', contentType: 'audio/ogg', headers: { 'X-Voice-Parts': '1' } }); return;
      } else if (/^\/api\/tasks\/task-\d+$/.test(url.pathname)) data.task = {
        status: completed ? 'done' : 'running', answer: completed ? 'Готово, сэр.' : '',
        commands: [{ tool_name: 'execute_cmd', command: 'PRIVATE COMMAND', result: { stdout: 'PRIVATE OUTPUT' } }], tasks: [],
      };
      else if (url.pathname === '/api/chats') data.chats = [{ id: 1, title: 'Проверка голоса' }];
      else if (url.pathname.endsWith('/messages')) data.messages = [];
      await route.fulfill({ json: data });
    });
    await page.goto('http://127.0.0.1:8769');
    await page.evaluate(() => {
      state.user = { id: 1, token: 'test-only' }; state.currentChatId = 1;
      state.chats = [{ id: 1, title: 'Проверка голоса' }];
      document.getElementById('authScreen').style.display = 'none';
      document.getElementById('appRoot').classList.add('active'); renderMessages();
    });
    await page.locator('#chatInput').fill('Черновик не отправлять');
    await page.locator('#voiceBtn').click();
    await page.waitForFunction(() => iruVoice.phase === 'idle');
    await page.evaluate(() => testRecognition.emit('Иру открой блокнот'));
    await page.waitForFunction(() => iruVoice.phase === 'working');
    await page.waitForFunction(() => state.pendingTasks.length === 1);
    assert.equal(await page.evaluate(() => testRecognition.active), false);
    assert.equal(await page.locator('#chatInput').inputValue(), 'Черновик не отправлять');
    assert.equal(requests.find(r => r.path === '/nl_command').body.message, 'открой блокнот');
    completed = true;
    await page.waitForFunction(() => iruVoice.phase === 'speaking');
    assert.equal(requests.filter(r => r.path.endsWith('/speech')).length, 1);
    assert.equal(requests.find(r => r.path.endsWith('/speech')).body, null);
    await page.evaluate(() => testRecognition.emit('открой браузер'));
    await page.evaluate(() => testRecognition.emit('стоп'));
    await page.waitForFunction(() => iruVoice.phase === 'listening');
    assert.equal(requests.filter(r => r.path === '/nl_command').length, 1);
    assert.equal(requests.some(r => r.path.endsWith('/cancel')), false);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('#chatInput').dispatchEvent('input');
    assert.equal(await page.locator('#voiceBtn').isVisible(), true);
    await page.waitForTimeout(350);
    const bounds = await page.locator('#voiceStatus').boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390 && bounds.y + bounds.height <= 844);
    await page.screenshot({ path: process.env.VOICE_SCREENSHOT || require('node:path').join(require('node:os').tmpdir(), 'iru-voice-smoke.png') });
    await page.evaluate(() => openChat(1));
    assert.equal(await page.evaluate(() => iruVoice.enabled), false);
    assert.equal(await page.evaluate(() => testRecognition.active), false);
    // Ordinary typed send still uses the shared existing task flow.
    await page.locator('#chatInput').fill('Текстовый запрос');
    await page.locator('#btnSend').click();
    await page.waitForFunction(() => state.pendingTasks.length === 0);
    assert.equal(requests.filter(r => r.path === '/nl_command').length, 2);
    assert.equal(requests.filter(r => r.path.endsWith('/speech')).length, 1);
    assert.deepEqual(errors, []);
    console.log('PASS: browser voice cycle, primary-answer endpoint, stop-only audio, draft preservation, mobile controls, chat reset, typed chat');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
