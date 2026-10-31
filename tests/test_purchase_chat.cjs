const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const publicDir = path.join(__dirname, '../apps/suke_ops/suke_ops/public');
function environment() {
    const messages = [], storage = new Map();
    const context = {frappe: {session: {user: 'buyer@example.invalid'}, msgprint: text => messages.push(text)},
        crypto: {randomUUID: () => 'test-uuid'}, sessionStorage: {getItem: k => storage.get(k), setItem: (k,v) => storage.set(k,v)},
        $: () => element(), console};
    function element() {const e = {}; for (const method of ['appendTo','html','on','addClass','attr']) e[method] = () => e; e.find = () => element(); return e;}
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(publicDir, 'hub.js'), 'utf8'), context);
    return {Hub: context.frappe.SupplyHub, messages, context};
}
function sender(Hub, options = {}) {
    const h = Object.create(Hub.prototype); let text = options.text ?? '现在缺多少料？';
    h.caseName = options.caseName ?? 'CASE-001'; h.module = h.caseName ? 'buying' : 'control';
    h.conversation = 'conversation'; h.turns = []; h.busy = false; h.restoring = false;
    h.data = {model_ready: options.ready ?? true};
    h.host = {find: () => ({val: value => value === undefined ? text : (text = value)})};
    h.renderBusy = h.renderMessages = () => {};
    h.watch = async () => {h.busy = false;};
    h.calls = []; h.call = async (method,args) => {h.calls.push({method,args}); return {name:'TURN-1'};};
    return h;
}
test('case conversations are separated from homepage and from other cases', () => {
    const {Hub} = environment(); Hub.prototype.refresh = Hub.prototype.restore = () => {};
    const a = new Hub({main:{}},{caseName:'CASE-A'}), b = new Hub({main:{}},{caseName:'CASE-B'}), home = new Hub({main:{}});
    assert.notEqual(a.key,b.key); assert.notEqual(a.key,home.key);
    assert.equal(a.module,'buying'); assert.equal(home.module,'control'); assert.equal(home.contextPrefix(),'');
});
test('natural language goes to existing buying API with case context; no business mutation endpoint', async () => {
    const {Hub} = environment(), h = sender(Hub); await h.send();
    assert.equal(h.calls.length,1); assert.equal(h.calls[0].method,'start');
    assert.equal(h.calls[0].args.module,'buying');
    assert.ok(h.calls[0].args.question.includes('CASE-001')); assert.ok(h.calls[0].args.question.endsWith('现在缺多少料？'));
});
test('missing model, empty input, restoring, and busy do not send', async () => {
    const {Hub} = environment();
    for (const options of [{ready:false},{text:'  '}]) {const h=sender(Hub,options);await h.send();assert.equal(h.calls.length,0);}
    for (const flag of ['busy','restoring']) {const h=sender(Hub);h[flag]=true;await h.send();assert.equal(h.calls.length,0);}
});
test('context counts toward server limit; original homepage questions are unchanged', async () => {
    const {Hub,messages} = environment(), h = sender(Hub,{text:'问'.repeat(2000)}); await h.send();
    assert.equal(h.calls.length,0); assert.equal(messages.length,1);
    const home=sender(Hub,{caseName:'',text:'查看采购申请'});await home.send();assert.equal(home.calls[0].args.question,'查看采购申请');
});
test('opening dialog refuses unsaved or dirty forms before loading resources', async () => {
    const handlers = {}, messages = []; let loads = 0;
    const context = {frappe:{utils:{escape_html:String},ui:{form:{on:(type,h)=>{handlers[type]=h;}}},
        msgprint:x=>messages.push(x), require:async()=>{loads++;}},console};
    vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(publicDir,'ops.js'),'utf8'),context);
    const buttons={};const wrapper={html:()=>{}};
    context.frappe.call=async()=>new Promise(()=>{}); // analysis does not affect dialog registration
    const frm={doc:{name:'CASE-A'},fields_dict:{dashboard:{$wrapper:wrapper}},is_new:()=>false,is_dirty:()=>true,add_custom_button:(name,fn)=>buttons[name]=fn};
    handlers['Supply Case'].refresh(frm);await buttons['采购对话']();
    assert.equal(loads,0);assert.match(messages[0],/先保存/);
});
test('upstream module constructor and purchase order context stay isolated',()=>{
 const {Hub}=environment();Hub.prototype.refresh=Hub.prototype.restore=()=>{};
 const stock=new Hub({main:{}},'chat','stock'),data=new Hub({main:{}},'data','buying');
 const po=new Hub({main:{}},{orderName:'PO-001'}),other=new Hub({main:{}},{orderName:'PO-002'});
 assert.equal(stock.module,'stock');assert.equal(data.view,'data');assert.equal(po.module,'buying');
 assert.notEqual(po.key,other.key);assert.match(po.contextPrefix(),/PO-001/);assert.match(po.contextPrefix(),/未确认/);
});
test('quick prompts retain typed draft and receive context',async()=>{
 const {Hub}=environment(),h=sender(Hub,{text:'未写完的问题'});await h.send('核对到货');
 assert.ok(h.calls[0].args.question.endsWith('核对到货'));assert.equal(h.host.find('textarea').val(),'未写完的问题');
});
test('live discussion sends a server scenario selector without client facts',async()=>{
 const {Hub}=environment(),h=sender(Hub,{caseName:'',text:'草稿保留'});
 await h.send('核对冲突',{scenario_id:0,scenario_updated:true});
 assert.equal(h.calls[0].args.scenario_id,0);assert.equal(h.calls[0].args.scenario_updated,true);
 assert.equal(h.calls[0].args.module,'control');assert.equal(h.host.find('textarea').val(),'草稿保留');
 assert.equal(h.calls[0].args.facts,undefined);
});
