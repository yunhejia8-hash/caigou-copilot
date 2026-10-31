/* 仅用于127.0.0.1:8093的finance-*合成站点，不读取现有部署凭据。 */
const {chromium} = require(process.env.FINANCE_PLAYWRIGHT);
const assert = require('node:assert/strict');
const fs = require('node:fs');

(async () => {
    const base = 'http://127.0.0.1:8093';
    const browser = await chromium.launch({executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true});
    try {
        const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        const login = await page.request.post(base + '/api/method/login', {form: {usr: 'Administrator', pwd: 'finance-synthetic-admin-only'}});
        assert.equal(login.status(), 200);
        await page.goto(base + '/desk/主页/erp-connections');
        await page.waitForFunction(() => !!window.frappe?.boot, {timeout: 30000});
        assert.match(await page.evaluate(() => frappe.boot.sitename), /^finance-/);
        await page.locator('[data-connection]').waitFor();
        const company = await page.evaluate(async () => (await frappe.db.get_list('Company', {fields: ['name'], limit: 1}))[0].name);
        const previousConnection = await page.locator('[data-connection]').inputValue();
        await page.getByRole('button', {name: '创建模拟连接', exact: true}).click();
        await page.locator('.modal:visible input[data-fieldname="company"]').fill(company);
        await page.locator('.modal:visible').getByRole('button', {name: '创建', exact: true}).click();
        await page.waitForFunction(old => document.querySelector('[data-connection]')?.value !== old
            && document.querySelector('[data-result]')?.textContent.includes('匹配 0 条'), previousConnection);
        const connection = await page.locator('[data-connection]').inputValue();
        const queuedResponse = page.waitForResponse(r => r.url().includes('suke_ops.finance_sync.api.sync_now'));
        await page.locator('[data-full]').click();
        const queued = (await (await queuedResponse).json()).message;
        await page.locator('.modal:visible').getByText(/排队不代表数据已同步/).waitFor();
        await page.locator('.modal:visible .btn-modal-close').click();
        let finalState;
        for (let i = 0; i < 40; i++) {
            const response = await page.request.get(base + '/api/method/suke_ops.finance_sync.api.runs', {params: {connection}});
            const jobs = (await response.json()).message;
            finalState = jobs.find(r => r.name === queued.run)?.state;
            if (['success', 'partial', 'failed', 'skipped'].includes(finalState)) break;
            await new Promise(resolve => setTimeout(resolve, 750));
        }
        assert.equal(finalState, 'success', '页面排队任务应由worker成功执行');
        await page.locator('[data-search]').click();
        await page.waitForFunction(() => document.querySelector('[data-result]')?.textContent.includes('0.30'));
        assert.match(await page.locator('.erp-connections').innerText(), /合成演示数据/);
        assert.match(await page.locator('[data-result]').innerText(), /最近同步有效/);
        await page.locator('[data-query]').fill('MOCK-PAYABLE-1');
        await page.locator('[data-search]').click();
        await page.waitForFunction(() => document.querySelector('[data-result]')?.textContent.includes('匹配 1 条'));
        await page.locator('[data-detail]').first().click();
        await page.locator('.modal:visible').getByText('来源记录（只读）').waitFor();
        assert.match(await page.locator('.modal:visible').innerText(), /MOCK-PAYABLE-1/);
        await page.locator('.modal:visible .btn-modal-close').click();
        await page.locator('[data-query]').fill('');
        await page.locator('[data-search]').click();
        await page.waitForFunction(() => document.querySelector('[data-result]')?.textContent.includes('匹配 3 条'));
        await page.screenshot({path: 'verification/finance-desktop-20261101.png', fullPage: true});
        await page.setViewportSize({width: 390, height: 844});
        await page.screenshot({path: 'verification/finance-mobile-20261101.png', fullPage: true});
        assert.deepEqual(errors, []);
        const report = {status: 'PASS', site: 'finance-v1.local', base_url: base, connection,
            checks: ['真实页面加载与合成标记', '页面创建模拟连接', 'POST排队并经独立worker执行成功',
                '查询分币种0.30精确汇总', '搜索过滤和只读详情', '桌面与移动视口截图', '无页面脚本异常'],
            real_kingdee_contacted: false, model_calls: 0};
        fs.writeFileSync('verification/finance-ui-20261101.json', JSON.stringify(report, null, 2));
        console.log(JSON.stringify(report));
    } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
