// 无额外依赖，验证真实事件处理、发送防重和草稿保护。
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const nodes = new Map();
function node(selector) {
    if (!nodes.has(selector)) nodes.set(selector, {
        handlers: {}, value: '', props: {}, 0: {scrollHeight: 0},
        appendTo() { return this; }, addClass() { return this; }, html(value) { this.markup=value; return this; },
        find(selector) { return node(selector); }, on(event, selector, handler) { this.handlers[event + (typeof selector === 'string' ? ':' + selector : '')] = handler || selector; return this; },
        val(value) { if (value === undefined) return this.value; this.value = value; return this; },
        prop(key,value) { this.props[key] = value; return this; }, text() { return this; }, scrollTop() { return this; }, each() {return this;}
    });
    return nodes.get(selector);
}
const context = {frappe:{session:{user:'synthetic-test'},utils:{escape_html:String}}, crypto:require('node:crypto').webcrypto,
    sessionStorage:{getItem(){return null;},setItem(){}}, $:node};
vm.createContext(context);
vm.runInContext(fs.readFileSync('apps/suke_ops/suke_ops/public/hub.js','utf8'),context);
const Hub=context.frappe.SupplyHub;
Hub.prototype.refresh=function(){};
Hub.prototype.restore=function(){};
const home = new context.frappe.SupplyHome({main:{}});
const control = home.agents.get('control');
assert.ok(!control.host.markup.includes('class="hub-tabs"'));
assert.ok(home.host.markup.includes('data-chat-agent="buying"'));
assert.ok(!home.host.markup.includes('href='), '主页切换不使用页面链接');
control.turns.push({question:'总控未完成分析',state:'running'});
control.busy=true;
home.selectAgent('buying');
const procurement=home.agents.get('buying');
assert.notEqual(procurement, control);
assert.ok(procurement.key.endsWith(':buying'));
assert.equal(home.panes.get('control').props.hidden,true);
assert.equal(home.panes.get('buying').props.hidden,false);
home.selectAgent('control');
assert.equal(home.agents.get('control'),control);
assert.equal(control.busy,true);
assert.equal(control.turns.length,1);
assert.equal(home.panes.get('buying').props.hidden,true);
assert.ok(control.agentLink('buying','采购').includes('data-chat-agent="buying"'));
assert.ok(!control.agentLink('buying','采购').includes('href='));
let hub;
for (const module of ['buying','selling','stock','manufacturing']) {
    const item = new Hub({main:{}}, 'chat', module);
    assert.equal(item.module,module);
    assert.equal(item.moduleUrl(module),'/desk/'+module+'/operations-'+module);
    assert.ok(item.key.endsWith(':'+module));
    if (module !== 'buying') {
        assert.ok(!item.host.markup.includes('class="hub-tabs"'), '非采购模块不展示其他Agent导航');
    }
}
for (const stage of ['demand','inquiry','comparison','approval','order','receipt']) {
    const item = new Hub({main:{}}, 'chat', 'buying-'+stage);
    assert.equal(item.moduleUrl(item.module),'/desk/buying/operations-buying-'+stage);
    assert.ok(item.key.endsWith(':buying-'+stage));
    assert.ok(item.host.markup.includes('采购环节'));
    assert.ok(!item.host.markup.includes('/desk/selling/'));
}
const board = {module:'stock',label:'库存',metrics:[{label:'库存数量',value:3,unit:'项'}],rows:[],access:true,signals:[],sections:[{doctype:'Quality Inspection',label:'质量检验',rows:[],access:true,columns:[]}]};
const dataHub = new Hub({main:{}},'data','stock');
assert.ok(!dataHub.host.markup.includes('class="hub-tabs"'));
assert.ok(dataHub.host.markup.includes('仅显示库存相关数据'));
dataHub.data={modules:[board],cases:[],approvals:[],decisions:[],at:'test'};
dataHub.render();
assert.ok(node('.hub-detail').markup.includes('质量检验'));
assert.ok(!node('.hub-summary').markup.includes('<a '), '单模块指标应为静态数据，不重复跳转对话');
const overviewHub = new Hub({main:{}},'data');
overviewHub.data=dataHub.data;
overviewHub.render();
assert.ok(node('.hub-summary').markup.includes('/desk/stock/operations-data-stock'), '总览卡片进入模块数据');
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
    procurement.restoring=false; procurement.data={model_ready:true}; procurement.renderMessages=()=>{};
    let target;
    procurement.call=async(method,args)=>{target={method,args};return {name:'procurement-turn'};};
    procurement.watch=async()=>{};
    await procurement.send('主页内询问采购');
    assert.equal(target.method,'start'); assert.equal(target.args.module,'buying');
    assert.equal(target.args.conversation,procurement.conversation);
    console.log('PASS: 发送与输入法、数据下钻、主页原地切换、独立会话保留、采购请求发送到正确Agent');
})().catch(error=>{console.error(error);process.exitCode=1;});
