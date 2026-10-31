// 无额外依赖，验证真实事件处理、发送防重和草稿保护。
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const nodes = new Map();
function node(selector) {
    if (!nodes.has(selector)) nodes.set(selector, {
        handlers: {}, value: '', props: {}, 0: {scrollHeight: 0},
        appendTo() { return this; }, addClass() { return this; }, html() { return this; },
        find(selector) { return node(selector); }, on(event, selector, handler) { this.handlers[event + (typeof selector === 'string' ? ':' + selector : '')] = handler || selector; return this; },
        val(value) { if (value === undefined) return this.value; this.value = value; return this; },
        prop(key,value) { this.props[key] = value; return this; }, text() { return this; }, scrollTop() { return this; }
    });
    return nodes.get(selector);
}
const context = {frappe:{session:{user:'synthetic-test'}}, crypto:require('node:crypto').webcrypto,
    sessionStorage:{getItem(){return null;},setItem(){}}, $:node};
vm.createContext(context);
vm.runInContext(fs.readFileSync('apps/suke_ops/suke_ops/public/hub.js','utf8'),context);
const Hub=context.frappe.SupplyHub;
Hub.prototype.refresh=function(){};
Hub.prototype.restore=function(){};
let hub;
for (const module of ['buying','selling','stock','manufacturing']) {
    const item = new Hub({main:{}}, 'chat', module);
    assert.equal(item.module,module);
    assert.equal(item.moduleUrl(module),'/desk/'+module+'/operations-'+module);
    assert.ok(item.key.endsWith(':'+module));
}
hub=new Hub({main:{}});
hub.renderMessages=function(){};
hub.data={model_ready:true}; hub.restoring=false;
let calls=[];
hub.call=async(method,args)=>{calls.push({method,args}); return {name:'synthetic-turn'};};
hub.watch=async()=>{hub.busy=false;hub.renderBusy();};
const textarea=node('textarea');
const keydown=textarea.handlers.keydown;
(async()=>{
    textarea.val('合成查询');
    keydown({key:'Enter',shiftKey:true,preventDefault(){assert.fail('换行被阻止');}});
    keydown({key:'Enter',isComposing:true,preventDefault(){assert.fail('中文选字被阻止');}});
    keydown({key:'Enter',originalEvent:{isComposing:true},preventDefault(){assert.fail('中文选字被阻止');}});
    assert.equal(calls.length,0);
    let prevented=false;
    keydown({key:'Enter',preventDefault(){prevented=true;}});
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(prevented,true); assert.equal(calls.length,1); assert.equal(textarea.val(),'');
    textarea.val('未完成草稿');
    hub.host.handlers['click:[data-prompt]']({currentTarget:{dataset:{prompt:'快捷查询'}}});
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(calls.at(-1).args.question,'快捷查询'); assert.equal(textarea.val(),'未完成草稿');
    let release;hub.call=()=>new Promise(resolve=>{release=resolve;});
    const pending=hub.send('第一次'); const count=hub.turns.length;
    await hub.send('重复'); assert.equal(hub.turns.length,count);
    release({name:'last-turn'}); await pending;
    console.log('PASS: Enter发送、Shift+Enter换行、中文选字保护、快捷问题直发、草稿保留、发送防重');
})().catch(error=>{console.error(error);process.exitCode=1;});
