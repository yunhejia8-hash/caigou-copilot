/* 安装本地验收依赖后运行：npm install --prefix .local-bear-qa playwright --no-save */
const {chromium} = require('../.local-bear-qa/node_modules/playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
(async () => {
    const browser = await chromium.launch({executablePath: process.env.BEAR_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true});
    try {
        const results = [];
        for (const mobile of [false, true]) {
            const page = await browser.newPage({viewport: mobile ? {width: 390, height: 844} : {width: 1440, height: 1000}, hasTouch: mobile});
            await page.route('https://bear.test/**', route => route.fulfill({contentType: 'text/html', body: '<html></html>'}));
            await page.goto('https://bear.test');
            await page.setContent('<html lang="zh"><body><h1>总控工作台 · 小熊交互验收</h1></body></html>');
            await page.evaluate(() => {
                window.$ = () => ({ready: fn => fn()});
                window.calls = [];
                window.ready = true;
                window.frappe = {session: {user: 'ui-test'}, call: async req => {
                    calls.push(req);
                    if (req.method.endsWith('chat_status')) return {message: {model_ready: window.ready}};
                    if (req.method.endsWith('start')) return {message: {name: 'TEST-TURN'}};
                    return {message: {state: 'done', answer: '<script>window.injected=true</script>总控已核查（模拟接口）'}};
                }};
            });
            for (const file of ['bear-pet.js', 'bear-control.js']) await page.addScriptTag({path: path.resolve('apps/suke_ops/suke_ops/public', file)});
            assert.equal(await page.evaluate(() => supplyBear.getSnapshot().connected), true);
            await page.locator('.chat-trigger').click();
            await page.locator('textarea').fill('检查库存');
            await page.locator('button.send:not(.stop)').click();
            await page.waitForFunction(() => document.querySelector('[data-bear-pet]')?.shadowRoot?.textContent.includes('总控已核查') || [...document.querySelectorAll('*')].some(e => e.shadowRoot?.textContent.includes('总控已核查')));
            assert.equal(await page.evaluate(() => window.injected), undefined);
            assert.equal(await page.evaluate(() => calls.find(x => x.method.endsWith('start')).args.module), 'control');
            await page.evaluate(() => { window.ready = false; });
            await page.locator('textarea').fill('再检查');
            await page.locator('button.send:not(.stop)').click();
            await page.locator('.is-error').filter({hasText: '尚未配置模型服务'}).waitFor();
            const box = await page.locator('.chat').boundingBox();
            assert.ok(box.x >= 0 && box.x + box.width <= (mobile ? 390 : 1440));
            await page.screenshot({path: `verification/bear-${mobile ? 'mobile' : 'desktop'}.png`});
            await page.evaluate(() => {
                supplyBear.closeChat(); supplyBear.feed(); supplyBear.dance(); supplyBear.sleep();
                if (supplyBear.getSnapshot().state !== 'sleep') throw Error('睡眠状态错误');
                supplyBear.wake(); supplyBear.minimize(); supplyBear.restore();
                window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}));
                if (!window.supplyBear) throw Error('返回缓存时实例丢失');
                supplyBear.destroy();
            });
            results.push({viewport: mobile ? '390x844 touch' : '1440x1000', status: 'PASS'});
            await page.close();
        }
        fs.writeFileSync('verification/bear-ui.json', JSON.stringify({status: 'PASS', backend: 'mock，仅验证界面与接口契约，不证明线上模型连通', results}, null, 2));
        console.log(JSON.stringify(results));
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
