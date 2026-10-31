const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function environment(search = '') {
    const elements = new Map(), handlers = new Map(), requests = [];
    let shown = 0;
    function element(key) {
        if (elements.has(key)) return elements.get(key);
        const e = {value: '', markup: '', content: '', length: 0,
            find: selector => element(selector), addClass: () => e,
            on: (event, selector, fn) => {handlers.set(typeof selector === 'string' ? selector : event, fn || selector); return e;},
            html: text => {e.markup = text; return e;}, text: text => {e.content = text; return e;},
            val: value => value === undefined ? e.value : ((e.value = value), e),
            empty: () => {e.markup = ''; return e;}, toggle: () => e, hide: () => e,
            prop: () => e, trigger: () => e, closest: () => e, modal: () => e};
        elements.set(key, e); return e;
    }
    const directory = {entries: [{id: 'type:Item', label: '物料', kind: 'type', module: 'stock', modules: ['stock'], description: '完整数据'}], modules: {stock: '库存'}};
    const location = {search, origin: 'http://127.0.0.1:8081', pathname: '/desk/buying/operations-buying', hash: ''};
    const context = {document: {addEventListener: (name, fn) => handlers.set('capture:' + name, fn)},
        window: {location, history: {replaceState: (_, __, url) => {location.search = new URL(url, location.origin).search;}}}, URLSearchParams, URL, setTimeout,
        $: () => element('root'), frappe: {
            utils: {escape_html: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'), debounce: fn => fn},
            ui: {Dialog: class {constructor() {this.fields_dict = {search: {$wrapper: element('host')}}; this.$wrapper = element('dialog');} show() {shown++;} hide() {handlers.get('hidden.bs.modal')?.();}}},
            call: ({method, args}) => method.endsWith('.directory') ? Promise.resolve({message: directory}) : new Promise(resolve => requests.push({args, resolve: message => resolve({message})})),
            route_options: {business_search: '1', q: '旧搜索', unrelated: 'keep'},
            after_ajax: fn => fn(), router: {on: (event, fn) => handlers.set('route:' + event, fn)}, set_route: () => {}}};
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../apps/suke_ops/suke_ops/public/business_search.js'), 'utf8'), context);
    return {context, elements, handlers, requests, shown: () => shown};
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

test('legacy search URL and repeated module changes never open the dialog', async () => {
    const e = environment('?business_search=1&q=old&entry=type%3AItem&unrelated=keep');
    for (let i = 0; i < 5; i++) {e.handlers.get('route:change')(); await flush();}
    assert.equal(e.shown(), 0);
    assert.equal(e.context.window.location.search, '?unrelated=keep');
    assert.equal(e.context.frappe.route_options.business_search, undefined);
    assert.equal(e.context.frappe.route_options.unrelated, 'keep');
});

test('explicit native search selection opens once without navigating', async () => {
    const e = environment(); let prevented = false, stopped = false;
    const event = {target: {id: 'navbar-search', awesomplete: {get_item: () => ({route: ['/desk/主页/operations-data?business_search=1&entry=type%3AItem']})}},
        text: {value: '物料'}, preventDefault: () => {prevented = true;}, stopImmediatePropagation: () => {stopped = true;}};
    e.handlers.get('capture:awesomplete-select')(event); await flush();
    assert.equal(e.shown(), 1); assert.ok(prevented && stopped);
    assert.equal(e.requests[0].args.entry, 'type:Item');
    e.handlers.get('route:change')(); await flush();
    assert.equal(e.shown(), 1);
});

test('clicking a search result cancels the link default navigation', async () => {
    const e = environment(); let prevented = false, stopped = false;
    const link = {getAttribute: () => '/desk/主页/operations-data?business_search=1&entry=type%3AItem',
        closest: () => ({querySelector: () => ({id: 'navbar-search'})})};
    e.handlers.get('capture:click')({target: {closest: () => link},
        preventDefault: () => {prevented = true;}, stopImmediatePropagation: () => {stopped = true;}});
    await flush();
    assert.ok(prevented && stopped); assert.equal(e.shown(), 1);
});
