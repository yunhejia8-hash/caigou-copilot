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
    assert.equal(h.calls[0].args.supply_case,'CASE-001');
    assert.ok(h.calls[0].args.question.includes('CASE-001')); assert.ok(h.calls[0].args.question.endsWith('现在缺多少料？'));
    assert.match(h.calls[0].args.question,/在结果卡选择方案、提交审批/);assert.match(h.calls[0].args.question,/合成场景只比较方案/);
});
test('missing model, empty input, restoring, and busy do not send', async () => {
    const {Hub} = environment();
    for (const options of [{ready:false},{text:'  '}]) {const h=sender(Hub,options);await h.send();assert.equal(h.calls.length,0);}
    for (const flag of ['busy','restoring']) {const h=sender(Hub);h[flag]=true;await h.send();assert.equal(h.calls.length,0);}
});
test('context counts toward server limit; original homepage questions are unchanged', async () => {
    const {Hub,messages} = environment(), h = sender(Hub,{text:'问'.repeat(2000)}); await h.send();
    assert.equal(h.calls.length,0); assert.equal(messages.length,1);
    const home=sender(Hub,{caseName:'',text:'查看采购申请'});await home.send();assert.equal(home.calls[0].args.question,'查看采购申请');assert.equal(home.calls[0].args.supply_case,undefined);
});

test('procurement context is opt-in and isolated from the original conversation', async () => {
    const {Hub} = environment(); Hub.prototype.refresh = Hub.prototype.restore = () => {};
    const home = new Hub({main:{}}), flow = new Hub({main:{}}, 'chat', 'buying', {procurementProcess:'PROC-A'});
    assert.notEqual(home.key,flow.key);
    const normal=sender(Hub,{caseName:'',text:'查看采购进度'});await normal.send();
    assert.equal('procurement_process' in normal.calls[0].args,false);
    const scoped=sender(Hub,{caseName:'',text:'核实这笔采购'});
    scoped.procurementProcess='PROC-A';scoped.options={procurementStage:'receipt'};
    await scoped.send();
    assert.equal(scoped.calls[0].args.procurement_process,'PROC-A');
    assert.equal(scoped.calls[0].args.procurement_stage,'receipt');
});

test('procurement navigation is separate from the new-conversation handler', () => {
    const source=fs.readFileSync(path.join(publicDir,'hub.js'),'utf8');
    const entry=source.match(/<a class="([^"]+)"[^>]*href="\/desk\/buying\/procurement-workspace"/);
    assert.ok(entry);assert.equal(entry[1].split(/\s+/).includes('hub-new'),false);
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

test('resource loading never opens a stale form dialog after navigation', async () => {
    for (const leave of [false, true]) {
        const handlers = {}, buttons = {}; let resolveLoad, opened = 0;
        let route = ['Form', 'Purchase Order', 'PO-001'];
        const frappe = {utils: {escape_html: String}, get_route: () => route,
            ui: {form: {on: (type, h) => {if (!handlers[type]) handlers[type] = h;}}, Dialog: class {
                constructor() {this.fields_dict = {chat: {$wrapper: {}}};} show() {opened++;}
            }}, require: () => new Promise(resolve => {resolveLoad = resolve;}),
            SupplyHub: class {}, msgprint: () => {}};
        const frm = {doc: {doctype: 'Purchase Order', name: 'PO-001'}, is_new: () => false,
            is_dirty: () => false, add_custom_button: (name, fn) => {buttons[name] = fn;}};
        const context = {frappe, cur_frm: frm}; vm.createContext(context);
        vm.runInContext(fs.readFileSync(path.join(publicDir, 'ops.js'), 'utf8'), context);
        handlers['Purchase Order'].refresh(frm);
        const loading = buttons['到货跟踪与采购对话']();
        if (leave) {route = ['Form', 'Purchase Order', 'PO-002']; context.cur_frm = {doc: {name: 'PO-002'}};}
        resolveLoad(); await loading;
        assert.equal(opened, leave ? 0 : 1);
    }
});

test('failed discussions restore saved evidence and synthetic boundary', async () => {
    const {Hub} = environment(), h = sender(Hub);
    h.calls = []; h.call = async method => {
        h.calls.push(method);
        assert.equal(method, 'history');
        return [{name: 'FAILED', state: 'failed', synthetic: true,
            events: [{kind: 'failed', summary: '查询未完成'}]}];
    };
    await h.restore();
    assert.deepEqual(h.calls, ['history']);
    assert.equal(h.turns[0].synthetic, true);
    assert.equal(h.turns[0].events.length, 1);
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
test('audit defaults to compact evidence with escaped source and real attribution',()=>{
 const {Hub}=environment(),h=Object.create(Hub.prototype);
 h.names={stock:'库存'};h.esc=v=>String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');h.answer=h.esc;
 const markup=h.auditTrail({name:'TURN',events:[{id:1,module:'stock',kind:'evidence',at:'2026-10-31T05:00:00+00:00',summary:'读取成功',source_ids:['H1']}],sources:[{id:'H1',label:'补充',text:'<script>unsafe</script>',reported_by:'buyer@example.invalid',reported_at:'2026-10-31 13:00:00',source_type:'登录用户补充',source_path:'prior',version:'abc',verification:'待核实'}]});
 assert.ok(markup.includes('buyer@example.invalid'));assert.ok(markup.includes('&lt;script&gt;'));
 assert.ok(!markup.includes('<script>'));assert.ok(!markup.includes('<details open'));
 assert.ok(markup.includes('核对 1 条来源'));
});
test('business provenance card links actual source and keeps account meaning explicit',()=>{
 const {Hub}=environment(),h=Object.create(Hub.prototype);h.esc=v=>String(v??'').replace(/</g,'&lt;').replace(/"/g,'&quot;');
 const html=h.recordSource({result_excerpt:JSON.stringify({_provenance:{doctype:'Purchase Order',name:'PO-1',created_by:'creator',created_at:'2026-10-30',modified_by:'editor',modified_at:'2026-10-31',read_at:'2026-10-31 15:00',note:'账号不等于承诺人'}})});
 assert.ok(html.includes('data-docname="PO-1"'));assert.ok(html.includes('editor'));assert.ok(html.includes('账号不等于承诺人'));
 assert.equal(h.recordSource({result_excerpt:'{truncated',truncated:true}),'');
});
test('history restores evidence in one request instead of polling each old turn', async () => {
 const {Hub} = environment(), h = Object.create(Hub.prototype), calls=[];
 h.conversation='history';h.module='control';h.turns=[];
 h.renderMessages=h.renderBusy=()=>{};
 h.call=async(method,args)=>{calls.push(method);return [{name:'old',state:'done',events:[{kind:'evidence'}]}];};
 h.host={find:()=>({text:()=>{}})};
 await h.restore();
 assert.deepEqual(calls,['history']);assert.equal(h.turns[0].events.length,1);assert.equal(h.restoring,false);
});
test('supplier source separates simulated speaker from actual uploader and escapes both', () => {
 const {Hub}=environment(), h=Object.create(Hub.prototype);
 h.esc=v=>String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
 const html=h.sourceCard({id:'E11',actor:'供应商（演示角色）',reported_at:'2026-11-02T10:18:00+08:00',channel:'群聊',text:'只有120件 <script>bad</script>',related_records:['PO-1286'],status:'未发出',version:'new',supersedes:'E05',ingestion:{recorded_by:'<buyer>',recorded_at:'2026-10-31 20:00:00'}});
 assert.match(html,/供应商（演示角色）/); assert.match(html,/场景时间/); assert.match(html,/实际录入账号：&lt;buyer&gt;/);
 assert.match(html,/不是供应商真实上传/); assert.match(html,/原消息保留/); assert.ok(!html.includes('<script>'));
 assert.equal(h.plainSummary('## 当前结论\n**待确认**：见[原始消息](https://example.invalid)。'),'当前结论\n待确认：见原始消息。');
});
test('completed discussion collapses audit but running discussion shows incremental evidence', () => {
 const {Hub}=environment(), h=Object.create(Hub.prototype); let html='';
 const node={scrollTop:0,scrollHeight:300,clientHeight:300,querySelectorAll:()=>[]};
 h.host={find:()=>({0:node,html:v=>{html=v;},scrollTop:()=>{}})};
 h.esc=String;h.names={control:'总控'};h.scenarioBrief=h.scenarioActions=()=>'';h.auditTrail=()=>'<div>逐步查询与反馈</div>';
 h.turns=[{name:'T',question:'核对供给',state:'running',answer:'',events:[{}]}];
 h.renderMessages(); assert.match(html,/逐步查询与反馈/); assert.ok(!html.includes('data-audit-key="process-'));
 h.turns[0].state='done';h.turns[0].answer='当前自由量200件；确定量缺口360件。';h.renderMessages();
 assert.match(html,/<details data-audit-key="process-T">/);assert.ok(html.indexOf('当前自由量')<html.indexOf('逐步查询与反馈'));assert.ok(!html.includes('<details open'));
});

function priorityHub(Hub) {
 const h=Object.create(Hub.prototype);
 h.esc=v=>String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
 h.answer=h.esc;
 return h;
}
function prioritySnapshot(manual=false) {
 return {scenario_key:'connector-1286',snapshot_version:'test',calculation:{free_stock:200,gross_demand:560,free_quantity_gap:360},rich_sources:[],
  priority_policy:{mode:manual?'manual':'due_time',order:manual?['WO-B','WO-A']:['WO-A','WO-B'],reason:manual?'B 有客户验收窗口':'默认按需料时点先后',recorded_by:'buyer@example.invalid',recorded_at:'2026-11-02 09:20:00',version:'policy-v1'},
  priority_assessment:{status:'assessed',summary:manual?'先保 B；条件成立后 A 还缺 80 件。':'默认 A 先用料，仍需校验。',tradeoff:manual?'A 的齐料受到影响。':'B 的齐料受到影响。',
   ranked_orders:[{id:'WO-B',sales_order:'SO-B',quantity:320,material_required_at:'2026-11-03 13:00'},{id:'WO-A',sales_order:'SO-A',quantity:240,material_required_at:'2026-11-03 09:00'}],
   allocations:[{work_order:'WO-B',sales_order:'SO-B',required:320,confirmed_allocated:200,confirmed_gap:120,conditional_allocated:320,conditional_gap:0,priority:1},{work_order:'WO-A',sales_order:'SO-A',required:240,confirmed_allocated:0,confirmed_gap:240,conditional_allocated:160,conditional_gap:80,priority:2}],
   totals:{confirmed_supply:200,demand:560,confirmed_gap:360,conditional_supply:480,conditional_gap:80},conditions:['C 预留调整仍须批准'],rules:[{id:'stock-bound',label:'可用库存',kind:'hard',description:'待检不计入确定供给',source_title:'规则依据',source_url:'https://example.invalid/rule'}],execution_authorized:false}};
}
test('priority preview requires a reason and uses server assessment before starting',async()=>{
 const {Hub,context,messages}=environment(),h=sender(Hub,{caseName:''});let prompt;
 h.tracePreview=prioritySnapshot();
 context.frappe.prompt=(fields,callback)=>{prompt={fields,callback};};
 h.priorityDialog();assert.match(prompt.fields[0].default,/WO-A/);
 await prompt.callback({first_order:prompt.fields[0].options[1],reason:'  '});
 await prompt.callback({first_order:prompt.fields[0].options[1],reason:'字'.repeat(301)});
 assert.equal(h.calls.length,0);assert.equal(messages.length,2);
 h.call=async(method,args)=>{h.calls.push({method,args});return prioritySnapshot(true);};
 await prompt.callback({first_order:prompt.fields[0].options[1],reason:' B 有客户验收窗口 '});
 assert.equal(h.calls[0].method,'scenario_preview');
 assert.equal(h.calls[0].args.priority_order,'["WO-B","WO-A"]');
 assert.equal(h.tracePrioritySelection.reason,'B 有客户验收窗口');
 h.call=async(method,args)=>{h.calls.push({method,args});return {name:'TURN-2'};};
 await h.runTracePreview();
 assert.equal(h.calls[1].method,'start');assert.equal(h.calls[1].args.scenario_id,6);
 assert.equal(h.calls[1].args.priority_order,'["WO-B","WO-A"]');
 assert.equal(h.calls[1].args.priority_reason,'B 有客户验收窗口');
 assert.equal(h.calls[1].args.recorded_by,undefined);assert.equal(h.calls[1].args.priority_assessment,undefined);
 assert.equal(h.tracePreview,null);assert.equal(h.tracePrioritySelection,null);
});
test('priority preview stays available if start fails; default run is not labeled manual',async()=>{
 const {Hub}=environment(),h=sender(Hub,{caseName:''});
 h.tracePreview=prioritySnapshot();h.tracePrioritySelection=null;
 h.call=async(method,args)=>{h.calls.push({method,args});throw new Error('offline');};
 await h.runTracePreview();
 assert.equal(h.calls[0].args.priority_order,undefined);assert.equal(h.calls[0].args.priority_reason,undefined);
 assert.equal(h.tracePreview.scenario_key,'connector-1286');
});
test('completed connector priority review sends previous turn and choices without forging attribution',async()=>{
 const {Hub,context}=environment(),h=sender(Hub,{caseName:''});let prompt;
 h.turns=[{name:'OLD',state:'done',verified_evidence:prioritySnapshot(true)}];
 context.frappe.prompt=(fields,callback)=>{prompt={fields,callback};};
 h.priorityDialog('OLD');assert.match(prompt.fields[0].default,/WO-B/);
 await prompt.callback({first_order:prompt.fields[0].options[0],reason:'A 的整批交付窗口已确认'});
 assert.equal(h.calls[0].args.previous_turn,'OLD');assert.equal(h.calls[0].args.priority_order,'["WO-A","WO-B"]');
 assert.equal(h.calls[0].args.scenario_id,undefined);assert.equal(h.calls[0].args.priority_policy,undefined);
});
test('priority panel separates default policy, real attribution, conditional allocation and rule sources',()=>{
 const {Hub}=environment(),h=priorityHub(Hub),snapshot=prioritySnapshot();
 const initial=h.priorityPanel(snapshot,'preview');
 assert.match(initial,/默认按需料时点/);assert.match(initial,/非人工指定/);assert.match(initial,/仅预览/);
 snapshot.priority_policy=prioritySnapshot(true).priority_policy;
 snapshot.priority_policy.reason='<script>reason</script>';
 snapshot.priority_assessment.rules.push({id:'unsafe',label:'<rule>',kind:'hard',description:'test',source_title:'text',source_url:'javascript:alert(1)'});
 const html=h.priorityPanel(snapshot,'TURN');
 assert.match(html,/WO-B → WO-A/);assert.match(html,/buyer@example.invalid/);assert.match(html,/版本 policy-v1/);
 assert.match(html,/条件全部落实后/);assert.match(html,/数量覆盖不等于已按时齐料/);assert.match(html,/未授权执行/);
 assert.match(html,/https:\/\/example.invalid\/rule/);assert.ok(!html.includes('href="javascript:'));
 assert.match(html,/&lt;script&gt;reason/);assert.ok(!html.includes('<script>'));
 assert.ok(!html.includes('<details open'));
});
test('unverified human changes suppress all allocations even if old numbers remain in payload',()=>{
 const {Hub}=environment(),h=priorityHub(Hub),snapshot=prioritySnapshot(true);
 snapshot.priority_assessment.status='needs_verification';snapshot.priority_assessment.summary='新增信息尚未核实';
 const html=h.priorityPanel(snapshot,'TURN');
 assert.match(html,/信息待核实，暂不显示分配/);assert.match(html,/新增信息尚未核实/);
 assert.ok(!html.includes('hub-priority-table'));assert.ok(!html.includes('确定供给 200'));
});
test('changed supplier quantity uses current priority outcome and labels old A-first plan historical',()=>{
 const {Hub}=environment(),h=priorityHub(Hub),snapshot=prioritySnapshot(true);
 snapshot.change={from_quantity:200,to_quantity:120,impact:'旧影响：B 必缺 80',affected_plan:'OLD',updated_plan:'NEW',affected_records:['WO-A','WO-B'],unchanged_constraints:'不得动待检'};
 snapshot.plan={id:'OLD',status:'候选',conditions:[]};
 const html=h.scenarioBrief(snapshot,'TURN');
 assert.match(html,/先保 B；条件成立后 A 还缺 80 件/);assert.match(html,/具体影响哪张工单，见下方本次排序/);
 assert.match(html,/历史基线候选方案（非本次排序结果）/);assert.ok(!html.includes('旧影响：B 必缺 80'));
});
test('current priority calculation precedes true coordinator response without replacing discussion history',()=>{
 const {Hub}=environment(),h=priorityHub(Hub);let html='';
 const node={scrollTop:0,scrollHeight:300,clientHeight:300,querySelectorAll:()=>[]};
 h.host={find:()=>({0:node,html:v=>{html=v;},scrollTop:()=>{}})};
 h.names={control:'总控'};h.auditTrail=()=>'<div>原有四阶段协作过程</div>';
 h.turns=[{name:'T',question:'先保B',state:'done',answer:'模型总控原始建议：需再核实到仓时间。',events:[{}],synthetic:true,verified_evidence:prioritySnapshot(true)}];
 h.renderMessages();
 assert.match(html,/模型总控原始建议/);assert.match(html,/原有四阶段协作过程/);assert.match(html,/data-priority-review="T"/);
 assert.ok(html.indexOf('按本次排序的数量校验')<html.indexOf('模型总控原始建议'));
 assert.match(html,/总控建议（待复核）/);assert.match(html,/<details data-audit-key="process-T">/);
});

test('pending verification labels ranked rows as historical and hides stale snapshot metrics',()=>{
 const {Hub}=environment(),h=priorityHub(Hub),snapshot=prioritySnapshot(true);
 snapshot.priority_assessment.status='needs_verification';
 snapshot.priority_assessment.basis='历史需求待核验；排序表不代表当前需求已确认。';
 const html=h.scenarioBrief(snapshot,'TURN');
 assert.match(html,/历史需求记录 · 待核验/);assert.match(html,/排序表不代表当前需求已确认/);
 assert.ok(!html.includes('hub-case-metrics'));assert.ok(!html.includes('hub-priority-table'));
 assert.match(html,/WO-B \/ SO-B/); // Original demand stays traceable but explicitly qualified.
});
test('current turn policy overrides older snapshot defaults and keeps Chinese rule classifications',()=>{
 const {Hub}=environment(),h=priorityHub(Hub),snapshot=prioritySnapshot(),current=prioritySnapshot(true);
 current.priority_assessment.rules=[{id:'r1',kind:'企业策略',label:'订单先后',description:'人工选择'},
  {id:'r2',kind:'本案例硬约束',label:'预留控制',description:'未批准不可动'},
  {id:'r3',kind:'本案例口径',label:'关联需求',description:'不能重复计算'}];
 const html=h.priorityPanel(snapshot,'TURN',current);
 assert.match(html,/WO-B → WO-A/);assert.ok(!html.includes('默认按需料时点'));
 for(const kind of ['企业策略','本案例硬约束','本案例口径']) assert.ok(html.includes('>'+kind+'</span>'));
 assert.match(html,/先保 B；条件成立后 A 还缺 80 件/);
});
test('full coordinator text remains available independently of arithmetic and old deterministic conclusion',()=>{
 const {Hub}=environment(),h=priorityHub(Hub);let html='';
 const node={scrollTop:0,scrollHeight:300,clientHeight:300,querySelectorAll:()=>[]};
 h.host={find:()=>({0:node,html:v=>{html=v;},scrollTop:()=>{}})};h.names={control:'总控'};
 const snapshot=prioritySnapshot(true);snapshot.deterministic_conclusion='STALE_A_PRIORITY_CONCLUSION';
 const original='总控真实回应。'+'核实到仓和批准条件。'.repeat(25)+'完整回应末尾。';
 h.turns=[{name:'RAW',question:'B优先',state:'done',answer:original,synthetic:true,verified_evidence:snapshot}];
 h.renderMessages();
 assert.ok(html.includes(original));assert.ok(!html.includes(snapshot.deterministic_conclusion));
 assert.match(html,/data-audit-key="outcome-RAW"/);assert.equal(h.turns[0].answer,original);
});

test('decision checks distinguish structured matches from unverified prose and show conflicts before advice',()=>{
 const {Hub}=environment(),h=priorityHub(Hub);let html='';
 assert.equal(h.decisionStatus(), '');
 const matched=h.decisionStatus({status:'matched',summary:'数量、排序和执行字段一致'});
 assert.match(matched,/结构化字段一致/);assert.match(matched,/文字建议仍待复核/);assert.match(matched,/不认证模型自然语言/);
 const pending=h.decisionStatus({status:'unverified',reason:'缺少结构化字段',issues:['<script>问题</script>']});
 assert.match(pending,/总控建议尚未核验/);assert.match(pending,/缺少结构化字段/);assert.match(pending,/&lt;script&gt;/);assert.ok(!pending.includes('<script>'));
 const node={scrollTop:0,scrollHeight:300,clientHeight:300,querySelectorAll:()=>[]};
 h.host={find:()=>({0:node,html:v=>{html=v;},scrollTop:()=>{}})};h.names={control:'总控'};
 h.turns=[{name:'CHECK',question:'核查',state:'done',answer:'保留模型实际答复',synthetic:true,verified_evidence:prioritySnapshot(true),decision_check:{status:'mismatch',summary:'工单顺序与本次设置不一致',issues:['请按 B 优先重新复核']}}];
 h.renderMessages();assert.match(html,/不能作为已核验方案/);assert.match(html,/请按 B 优先重新复核/);
 assert.ok(html.indexOf('总控建议存在冲突')<html.indexOf('保留模型实际答复'));
 assert.equal(h.turns[0].answer,'保留模型实际答复');
});

test('structured action separates real document counts, reason and next steps without implying execution',()=>{
 const {Hub}=environment(),h=priorityHub(Hub);
 const html=h.renderActionResult({name:'ACTION',action:{status:'blocked',reason:'供应商尚未确认可用时间。',next_step:'1. 采购核实到仓时间。 2. 仓库核实预留归属。'},documents:[{doctype:'Material Request',name:'MR-1',reused:true}],offers:[]});
 assert.match(html,/hub-action-result.*data-status="blocked"/);assert.match(html,/待处理阻碍/);
 assert.match(html,/本轮关联单据 <strong>1<\/strong> 张/);assert.match(html,/可打开草稿入口 <strong>0<\/strong> 个/);
 assert.match(html,/hub-action-section hub-reason/);assert.match(html,/data-section="next"/);
 assert.match(html,/<ol class="hub-next-steps"><li>采购核实到仓时间。<\/li><li>仓库核实预留归属。<\/li><\/ol>/);
 assert.ok(!html.includes('执行完成'));assert.ok(!html.includes('已保存业务单据'));
});
test('reported created or draft status needs matching records; missing and malformed action stays safe',()=>{
 const {Hub}=environment(),h=priorityHub(Hub);
 for(const action of [undefined,null,'text',[],{}, {status:['document_created'],reason:4,next_step:{text:'bad'}}]) assert.equal(h.renderActionResult({action}),'');
 const created=h.renderActionResult({action:{status:'document_created',reason:'模型声称创建'},documents:[null,{name:'no-type'}]});
 assert.match(created,/单据状态待核对/);assert.ok(!created.includes('已登记物料需求单'));
 assert.match(created,/尚未提供下一步/);assert.match(created,/本轮关联单据 <strong>0<\/strong>/);
 const draft=h.renderActionResult({action:{status:'draft_ready'},offers:[{},null]});
 assert.match(draft,/草稿入口待核对/);assert.match(draft,/尚未提供具体原因/);
 const partial=h.renderActionResult({action:{reason:'待补供应确认'}});
 assert.match(partial,/行动结果待核实/);assert.match(partial,/待补供应确认/);
 const ready=h.renderActionResult({action:{status:'draft_ready'},offers:[{label:'询价草稿',action:'rfq',source:'MR-1'}]});
 assert.match(ready,/已提供草稿入口/);assert.match(ready,/可打开草稿入口 <strong>1<\/strong>/);
});
test('long action reasons preserve the complete original behind a stable escaped disclosure',()=>{
 const {Hub}=environment(),h=priorityHub(Hub),reason='库存可用时间待核实。'.repeat(30)+'完整原因末尾<script>bad</script>';
 const turn={name:'LONG"bad',action:{status:'blocked',reason,next_step:'请采购继续核实。'}};
 const html=h.renderActionResult(turn);
 assert.match(html,/data-audit-key="reason-LONG&quot;bad"/);assert.match(html,/展开完整原因/);
 assert.ok(html.includes(h.esc(reason)));assert.ok(!html.includes('<script>'));assert.ok(!html.includes('<details open'));
 assert.equal(turn.action.reason,reason);assert.ok(html.indexOf('原因与影响')<html.indexOf('下一步'));
});
test('action lists preserve decimals, dates and unsafe text; ambiguous numbering stays as prose',()=>{
 const {Hub}=environment(),h=priorityHub(Hub);
 const html=h.actionSteps('1. 预算 1.5 万元，11.04 核实。 2. 联系 <img src=x onerror=bad()>，单价 2.50 元。');
 assert.equal((html.match(/<li>/g)||[]).length,2);assert.match(html,/1\.5 万元，11\.04/);assert.match(html,/2\.50 元/);
 assert.match(html,/&lt;img/);assert.ok(!html.includes('<img'));
 for(const value of ['预算 1.5 万元，2026.11.04 到货。','先核查。 1. 联系采购。 2. 联系仓库。','1. 采购确认。 3. 仓库确认。','1. 2. 信息待核实。','1. 采购确认。 2、 仓库确认。']) {
  const output=h.actionSteps(value);assert.ok(!output.includes('<ol'));assert.ok(output.includes(h.esc(value)));
 }
 assert.equal(h.actionSteps('采购核实。\n\n仓库复核。'),'<p>采购核实。</p><p>仓库复核。</p>');
});
test('consecutive action markers after Chinese sentence endings form steps without losing punctuation',()=>{
 const {Hub}=environment(),h=priorityHub(Hub);
 const html=h.actionSteps('1. 采购核实 11.04 到货，费用 1.5 万元。2. 运维排查工具；3. 销售核实需求。4. 仓库核对预留。5. 信息补齐后重新评估。');
 assert.equal((html.match(/<li>/g)||[]).length,5);
 assert.match(html,/费用 1\.5 万元。<\/li><li>运维排查工具；<\/li><li>销售核实需求。/);
 assert.match(html,/11\.04 到货/);assert.match(html,/重新评估。<\/li><\/ol>/);
 for(const value of ['费用待确认。1.5 万元，11.04 到货。','1. 采购核实。3. 仓库核实。']) {
  const output=h.actionSteps(value);assert.ok(!output.includes('<ol'));assert.ok(output.includes(value));
 }
});
function renderedActionTurn(h,turn) {
 let html='';const node={scrollTop:0,scrollHeight:300,clientHeight:300,querySelectorAll:()=>[]};
 h.host={find:()=>({0:node,html:v=>{html=v;},scrollTop:()=>{}})};h.names={control:'总控'};
 h.turns=[{name:'ACTION',question:'核对供给',state:'done',...turn}];h.renderMessages();return html;
}
test('action trailer is absent from compact summary but complete answer and original turn are preserved',()=>{
 const {Hub}=environment(),h=priorityHub(Hub);
 const original='销售模块未完成核查。\n\n**本轮行动结果：待处理阻碍**\n\n已保存业务单据：0 张。\n原因：供应未确认\n\n下一步：请采购联系供应商。';
 const turn={answer:original,action:{status:'blocked',reason:'供应未确认',next_step:'请采购联系供应商。'}};
 const html=renderedActionTurn(h,turn),compact=html.split('data-audit-key="outcome-ACTION"')[0];
 assert.match(compact,/当前结果/);assert.match(compact,/销售模块未完成核查/);assert.ok(!compact.includes('本轮行动结果'));
 assert.ok(html.includes(h.esc(original)));assert.equal(h.turns[0].answer,original);assert.match(html,/hub-action-result/);
});
test('summary only removes the exact server trailer when structured action can be rendered',()=>{
 const {Hub}=environment();
 for(const turn of [
  {answer:'原话 **本轮行动结果：待核查**',action:{status:'blocked'}},
  {answer:'摘要\n\n**本轮行动结果：待核查**'},
  {answer:'摘要\n\n**本轮行动结果：待核查**',action:{}}
 ]) {
  const h=priorityHub(Hub),html=renderedActionTurn(h,turn),compact=html.split('data-audit-key="outcome-ACTION"')[0];
  assert.match(compact,/本轮行动结果/);
 }
});
