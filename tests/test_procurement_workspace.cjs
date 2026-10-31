const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function workspace() {
    const context = {frappe:{utils:{escape_html:v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}};
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname,'../apps/suke_ops/suke_ops/public/procurement.js'),'utf8'),context);
    const w=Object.create(context.frappe.ProcurementWorkspace.prototype);
    let html='';w.host={html:v=>{html=v;},attr:()=>{},find:()=>({html:v=>{html=v;}})};
    w.stages={demand:'需求',stock:'库存核对',inquiry:'询价',comparison:'比价',approval:'审批',order:'下单与催交',receipt:'收货质检',finance:'对账付款',review:'结案复盘'};
    w.labels={};w.stage='demand';w.process='A';
    w.list={can_edit:false,can_review:false,processes:[{name:'A',title:'<img src=x onerror=alert(1)>',company:'测试'}]};
    w.library={experiences:[{name:'E',title:'测试经验',stage:'demand',outcome:'unknown',situation:'<script>alert(1)</script>',recommendation:'<b>判断</b>',sources:[],conditions:[],verification:{}}],rules:[]};
    w.data={name:'A',at:'测试',documents:[],warnings:[],stock:[],quotations:[],reconciliation:[],review:{},stages:Object.entries(w.stages).map(([id,title])=>({id,title,state:'pending',next_step:'核实'}))};
    return {w,html:()=>html};
}

test('all nine procurement stages render and imported text cannot execute HTML',()=>{
    const {w,html}=workspace();w.show();
    for(const title of Object.values(w.stages))assert.ok(html().includes(title));
    assert.ok(html().includes('&lt;script&gt;'));assert.ok(!html().includes('<script>'));
    assert.ok(!html().includes('<img src=x'));assert.ok(html().includes('待核实'));
});

test('slow knowledge from another process cannot replace the current experience panel',async()=>{
    const {w,html}=workspace();let finish;
    w.call=()=>new Promise(resolve=>finish=resolve);
    const waiting=w.act('knowledge');w.process='B';
    finish({rules:[{name:'old',recommendation:'旧采购经验'}],experiences:[]});await waiting;
    assert.ok(!html().includes('旧采购经验'));
});

test('knowledge and verification remain human readable and escaped',async()=>{
    const {w,html}=workspace();
    w.call=async()=>({rules:[{title:'适用规则',recommendation:'<svg onload=alert(1)>',conditions:[{field:'late_days',op:'gte',value:1}]}],experiences:[]});
    await w.act('knowledge');assert.ok(html().includes('延期天数 大于等于 1'));
    assert.ok(!html().includes('<svg'));assert.ok(html().includes('&lt;svg'));
});

test('changed business facts invalidate the current savings label',()=>{
    const {w,html}=workspace();w.stage='review';
    w.data.business_fingerprint='NEW';
    w.data.review={summary:'旧复盘',business_fingerprint:'OLD',realized_savings:20,currency:'CNY'};
    w.show();assert.ok(html().includes('历史复盘已失效'));assert.ok(!html().includes('已核实节省：20'));
    w.data.review.business_fingerprint='NEW';w.show();assert.ok(html().includes('已核实节省：20 CNY'));
});
