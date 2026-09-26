// Static UI on port 8769; no production accounts or backend calls.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    for (const [width, height] of [[320, 568], [390, 844], [430, 932], [740, 390], [1440, 900]]) {
      const page = await browser.newPage({ viewport: { width, height }, isMobile: width <= 768, hasTouch: width <= 768 });
      const errors = []; page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'http://127.0.0.1:8769') return route.abort();
        if (url.pathname.startsWith('/api/')) return route.fulfill({ json: { status: 'ok', devices: [], facts: [] } });
        if (url.pathname.startsWith('/static/')) return route.continue({ url: url.origin + url.pathname.replace('/static', '') });
        return route.continue();
      });
      await page.goto('http://127.0.0.1:8769');
      await page.evaluate(() => document.fonts.ready);
      if (width === 390) await page.screenshot({ path: path.join(process.env.TEMP, 'iru-mobile-auth.png') });
      await page.evaluate(() => {
        state.user = { id: 1, token: 'test-only' }; state.currentChatId = 1;
        document.getElementById('authScreen').style.display = 'none';
        document.getElementById('appRoot').classList.add('active'); renderMessages();
        document.getElementById('headerTitle').textContent = 'Новый чат';
      });
      await page.waitForTimeout(200);
      if (width === 390) await page.screenshot({ path: path.join(process.env.TEMP, 'iru-mobile-welcome.png') });
      async function within(selector, minSize = 0) {
        const box = await page.locator(selector).boundingBox();
        assert.ok(box, `${width}: ${selector} visible`);
        assert.ok(box.x >= -1 && box.x + box.width <= width + 1, `${width}: ${selector} horizontal bounds`);
        assert.ok(box.y >= -1 && box.y + box.height <= (await page.viewportSize()).height + 1, `${width}: ${selector} vertical bounds`);
        assert.ok(box.width >= minSize && box.height >= minSize, `${width}: ${selector} tap target`);
      }
      if (width <= 768) {
        for (const selector of ['#mobilePlusBtn', '#voiceBtn', '#btnMenuMobile']) await within(selector, 44);
        await page.locator('#mobilePlusBtn').click();
        await within('#mobilePlusPopover');
        await page.locator('#mobilePlusModeAction').click();
        await within('#inputModeDropdown');
        await page.locator('#modePipeline').check();
        assert.equal(await page.locator('#modePipeline').isChecked(), true);
        await page.locator('#headerTitle').click();
        assert.equal(await page.locator('#inputModeDropdown').isVisible(), false);
        await page.locator('#mobilePlusBtn').click();
        await page.locator('#mobilePlusDeviceAction').click();
        await within('#inputDeviceDropdown');
        await page.locator('#headerTitle').click();
        await page.locator('#btnMenuMobile').click();
        await page.waitForFunction(() => Math.abs(document.querySelector('.sidebar').getBoundingClientRect().x) < 1);
        await within('.sidebar');
        await page.locator('#sidebarOverlay').click({ position: { x: width - 8, y: 20 } });
        await page.waitForFunction(() => document.querySelector('.sidebar').getBoundingClientRect().right <= 1);
      }
      await page.evaluate(() => {
        state.messages = [
          { role: 'user', content: 'Собери материалы о локальных моделях и подготовь документы.' },
          { role: 'assistant', content: 'Материалы готовы.\n\nВ папке собраны презентация, документ и таблица сравнения. Вы можете проверить результат и уточнить, что изменить.\n\n' + 'ОченьДлинноеИмяФайла'.repeat(12) },
        ]; renderMessages();
      });
      await page.locator('#chatInput').fill('Добавь сравнение стоимости');
      await within('#btnSend', width <= 768 ? 44 : 0);
      await within('#chatInput');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      if (width === 390) {
        await page.evaluate(() => { state.messages[1].content = 'Материалы готовы.\n\nВ папке собраны:\n- презентация с основными выводами;\n- документ с подробным описанием;\n- таблица для сравнения моделей.\n\nВы можете проверить результат и уточнить, что изменить.'; renderMessages(); });
        await page.screenshot({ path: path.join(process.env.TEMP, 'iru-mobile-chat.png') });
        await page.setViewportSize({ width, height: 420 });
        await page.waitForTimeout(400); await within('#chatInput'); await within('#btnSend', 44);
      }
      assert.deepEqual(errors, []);
      console.log(`PASS mobile layout ${width}x${height}`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
