const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function environment() {
    const elements = new Map(), handlers = new Map(), requests = [];
    function element(key) {
        if (elements.has(key)) return elements.get(key);
        const e = {value: '', markup: '', content: '', length: 0,
            find: selector => element(selector), addClass: () => e,
            on: (event, selector, fn) => {handlers.set(typeof selector === 'string' ? selector : event, fn || selector); return e;},
            html: text => {e.markup = text; return e;}, text: text => {e.content = text; return e;},
            val: value => value === undefined ? e.value : ((e.value = value), e),
            empty: () => {e.markup = ''; return e;}, toggle: () => e, hide: () => e,
            prop: () => e, trigger: () => e, closest: () => e};
        elements.set(key, e); return e;
    }
    const directory = {entries: [{id: 'type:Item', label: '物料', kind: 'type', module: 'stock', modules: ['stock'], description: '完整数据'}], modules: {stock: '库存'}};
    const context = {document: {}, window: {location: {search: ''}}, URLSearchParams, setTimeout,
        $: () => element('root'), frappe: {
            utils: {escape_html: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'), debounce: fn => fn},
            ui: {Dialog: class {constructor() {this.fields_dict = {search: {$wrapper: element('host')}}; this.$wrapper = element('dialog');} show() {} hide() {handlers.get('hidden.bs.modal')?.();}}},
            call: ({method, args}) => method.endsWith('.directory') ? Promise.resolve({message: directory}) : new Promise(resolve => requests.push({args, resolve: message => resolve({message})})),
            after_ajax: () => {}, router: {on: () => {}}, set_route: () => {}}};
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../apps/suke_ops/suke_ops/public/business_search.js'), 'utf8'), context);
    return {context, elements, handlers, requests};
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const result = title => ({at: '2026-10-31', scope: '当前可见记录', groups: [{entry: 'type:Item', label: '物料', total: 1, start: 0, page_length: 20, has_more: false,
    rows: [{doctype: 'Item', name: 'ITEM-1', title, status: '', modified: '2026-10-31 12:00:00'}]}]});

test('record title is escaped and selected scope reaches the server', async () => {
    const e = environment(); await e.context.frappe.business_search.open({entry: 'type:Item'});
    assert.equal(e.requests[0].args.entry, 'type:Item');
    e.requests[0].resolve(result('<img src=x onerror=alert(1)>')); await flush();
    const html = e.elements.get('.biz-records').markup;
    assert.ok(html.includes('&lt;img')); assert.ok(!html.includes('<img'));
});

test('slow old query cannot replace a newer search result', async () => {
    const e = environment(); await e.context.frappe.business_search.open({entry: 'type:Item'});
    e.elements.get('.biz-query').val('new'); e.handlers.get('.biz-query')();
    e.requests[1].resolve(result('new result')); await flush();
    e.requests[0].resolve(result('old result')); await flush();
    assert.ok(e.elements.get('.biz-records').markup.includes('new result'));
    assert.ok(!e.elements.get('.biz-records').markup.includes('old result'));
});

test('closing the dialog discards pending records', async () => {
    const e = environment(); await e.context.frappe.business_search.open({entry: 'type:Item'});
    e.handlers.get('hidden.bs.modal')(); e.requests[0].resolve(result('late result')); await flush();
    assert.equal(e.elements.get('.biz-records').markup, '');
});
