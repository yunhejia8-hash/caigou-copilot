const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function client() {
    const elements = new Map();
    const escaped = v => String(v).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
    const context = {frappe: {utils: {escape_html: escaped}, msgprint() {}}, console};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../apps/suke_ops/suke_ops/public/finance_connections.js'), 'utf8'), context);
    const c = Object.create(context.frappe.ERPConnections.prototype);
    c.host = {find(selector) {
        if (!elements.has(selector)) elements.set(selector, {value: '', html(s) {this.value = s; return this;}, attr() {return this;}});
        return elements.get(selector);
    }};
    c.connection = 'mock-connection'; c.entity = 'payable'; c.offset = 0; c.sequence = 0;
    return {c, elements, frappe: context.frappe};
}

test('query sends only scoped arguments and an older response cannot replace a newer one', async () => {
    const {c} = client(), pending = [], shown = [];
    c.call = (method, args) => {assert.equal(method, 'records'); pending.push({args}); return new Promise(resolve => pending.at(-1).resolve = resolve);};
    c.showResult = d => shown.push(d.id);
    const first = c.search();
    c.offset = 20; c.queryText = 'S001';
    const second = c.search();
    assert.deepEqual(Object.keys(pending[0].args).sort(), ['connection','entity','offset','page_length','q']);
    assert.equal(pending[1].args.q, 'S001'); assert.equal(pending[1].args.offset, 20);
    pending[1].resolve({id: 'new'}); await second;
    pending[0].resolve({id: 'old'}); await first;
    assert.deepEqual(shown, ['new']);
});

test('query failure is explicit and cannot leave old totals visible', async () => {
    const {c, elements} = client();
    c.call = async () => {throw new Error('network');};
    await c.search();
    assert.match(elements.get('[data-result]').value, /查询失败或无权限/);
    assert.match(elements.get('[data-result]').value, /不能按零金额/);
});

test('source content is escaped and stale or incomplete totals stay visibly unverified', () => {
    const {c, elements} = client();
    c.showResult({verified_current: false, last_error: '<img src=x onerror=alert(1)>', last_success: '',
        source_window_until: '', summary_by_currency: {}, summary_complete: false, summary_note: '仅有效记录',
        rows: [{name: 's1', number: '<script>alert(1)</script>', state: 'cancelled'}], total: 1, offset: 0});
    const html = elements.get('[data-result]').value;
    assert.ok(!html.includes('<script>')); assert.ok(!html.includes('<img'));
    assert.match(html, /&lt;script&gt;/); assert.match(html, /汇总未完成/); assert.match(html, /需核实/);
});

test('details preserve synthetic attribution and never render source HTML', async () => {
    const {c, frappe} = client(); let message;
    c.call = async (method, args) => {
        assert.equal(method, 'detail'); assert.equal(args.name, 's1');
        return {synthetic: true, title: '<script>source</script>'};
    };
    frappe.msgprint = d => message = d.message;
    await c.detail('s1');
    assert.match(message, /合成演示/); assert.ok(!message.includes('<script>'));
});

test('repeated sync clicks enqueue once and report queued without claiming success', async () => {
    const {c, frappe} = client(); let resolve, count = 0, message;
    c.call = (method, args) => {
        assert.equal(method, 'sync_now'); assert.equal(args.full_scan, 1); count++;
        return new Promise(r => resolve = r);
    };
    frappe.msgprint = d => message = d.message;
    const a = c.sync(1); await c.sync(1);
    resolve({run: 'run1'}); await a;
    assert.equal(count, 1); assert.match(message, /排队不代表数据已同步/);
    assert.equal(c.syncing, false);
});
