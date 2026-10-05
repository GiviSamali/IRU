/* Actual computed-style regression: a missing CSS brace must not hide Settings. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright');
const ui = path.resolve(__dirname, '../ui');
let browser, server, origin;
test.before(async () => {
  server = http.createServer((request, response) => {
    const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
    const file = path.resolve(ui, '.' + (pathname === '/' ? '/index.html' : pathname));
    if (!file.startsWith(ui + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      response.writeHead(404); response.end(); return;
    }
    const types = {'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.png':'image/png'};
    response.setHeader('Content-Type', types[path.extname(file)] || 'application/octet-stream');
    response.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel:'msedge', headless:true});
});
test.after(async () => { await browser?.close(); await new Promise(resolve => server?.close(resolve)); });
for (const viewport of [{width:1440,height:1000},{width:390,height:844}]) {
  test(`Settings has visible labels, styled links and spaced sections at ${viewport.width}px`, async () => {
    const context = await browser.newContext({viewport});
    try {
      const page = await context.newPage(), errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => localStorage.setItem('iru_token', 'settings-ui-fixture'));
      await page.route('**/api/**', async route => {
        const pathname = new URL(route.request().url()).pathname;
        let data = {status:'ok'};
        const user = {id:2,name:'demo',is_admin:false,data_consent:true,plan:'free',limits:{}};
        if (pathname === '/api/auth' || pathname === '/api/user_info') data = {status:'ok',user};
        if (pathname === '/api/chats') data = {status:'ok',chats:[]};
        if (pathname === '/api/devices') data = {status:'ok',devices:{}};
        if (pathname === '/api/terms_status') data = {status:'ok',accepted:true};
        if (pathname === '/api/memory/stats') data = {status:'ok',memory_stats:{facts:1,commands:0,facts_list:[]}};
        if (pathname === '/api/memory/facts') data = {status:'ok',facts:[{id:1,text:'Факт без подключённого устройства',source:'user',category:'general'}]};
        await route.fulfill({json:data});
      });
      await page.goto(origin, {waitUntil:'networkidle'});
      if (viewport.width < 768) await page.locator('#mobileHeaderToggle').click();
      const label = page.locator('#settingsToggleText');
      assert.equal(await label.isVisible(), true);
      assert.equal(await label.innerText(), 'Настройки');
      assert.ok((await page.locator('#settingsToggle').boundingBox()).width >= 100);
      await page.locator('#settingsToggle').click();
      await page.locator('#settingsPanel.open').waitFor();
      assert.equal(await page.locator('#settingsPanel #memoryFactInput').count(), 0);
      assert.equal(await page.locator('#memoryPanel').evaluate(el=>el.classList.contains('open')), false);
      const styles = await page.evaluate(() => {
        const css = selector => getComputedStyle(document.querySelector(selector));
        const links = css('.settings-links'), link = css('.settings-links a'), section = css('.settings-browser');
        return {display:links.display,gap:links.gap,padding:links.paddingTop,
          decoration:link.textDecorationLine,border:link.borderTopWidth,
          sectionPadding:section.paddingLeft,sectionGap:section.gap,
          hintSize:css('.settings-hint').fontSize,iconDisplay:css('#settingsToggle > span').display};
      });
      assert.equal(styles.display, 'grid');
      assert.equal(styles.gap, '8px');
      assert.equal(styles.padding, '14px');
      assert.equal(styles.decoration, 'none');
      assert.equal(styles.border, '1px');
      assert.equal(styles.sectionPadding, '14px');
      assert.equal(styles.sectionGap, '10px');
      assert.equal(styles.hintSize, '12px');
      assert.notEqual(styles.iconDisplay, 'none');
      await page.locator('#settingsPanelCloseBtn').click();
      assert.equal(await page.locator('#settingsToggle').getAttribute('aria-expanded'), 'false');
      if (viewport.width < 768) await page.locator('#mobileHeaderToggle').click();
      assert.equal(await page.locator('#memoryBadgeText').innerText(), 'Факты');
      await page.locator('#memoryBadge').click();
      await page.locator('#memoryPanel.open').waitFor();
      await page.getByText('Факт без подключённого устройства', {exact:true}).waitFor();
      assert.equal(await page.locator('#settingsPanel').evaluate(el=>el.classList.contains('open')), false);
      assert.equal(await page.locator('#memoryPanel .settings-links').count(), 0);
      assert.equal(await page.locator('#memoryBadge').getAttribute('aria-expanded'), 'true');
      if (viewport.width < 768) assert.equal(await page.locator('#headerActions').evaluate(el=>el.classList.contains('mobile-open')), false);
      assert.equal(await page.locator('#usageBadge').isVisible(), false);
      assert.deepEqual(errors, []);
      if (process.env.IRU_SCREENSHOT_DIR) {
        await page.screenshot({path:path.join(process.env.IRU_SCREENSHOT_DIR, `settings-${viewport.width}.png`)});
      }
    } finally { await context.close(); }
  });
}
