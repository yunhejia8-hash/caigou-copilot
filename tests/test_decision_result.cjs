const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function environment() {
    const messages = [], prompts = [];
    const context = {frappe: {utils: {escape_html: value => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')},
        markdown: value => value, msgprint: value => messages.push(value), prompt: (fields, callback, title, label) => {prompts.push({fields, callback, title, label});}}, crypto: {randomUUID: () => 'test-request-id'}, console};
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../apps/suke_ops/suke_ops/public/hub.js'), 'utf8'), context);
    const h = Object.create(context.frappe.SupplyHub.prototype);
    h.conversation = 'original-conversation'; h.module = 'control'; h.turns = []; h.busy = h.restoring = false;
    h.renderMessages = h.renderBusy = () => {}; h.calls = [];
    h.call = async (method, args) => {h.calls.push({method, args}); return {...h.turns[0], resolution: {...h.turns[0].resolution, state: 'pending_approval'}};};
    return {h, messages, prompts, context};
}
function turn(overrides = {}) {
    return {name: 'TURN-1', state: 'done', answer: '完整模型原文', question: '先保谁', resolution: {
        kind: 'erp', state: 'needs_choice', title: '连接器缺料处置', summary: '200 件自由库存，先选方案再审批。',
        case: {name: 'CASE-1', item_code: 'CONN-1', warehouse: '原料仓', is_simulation: false}, fingerprint: 'version-1',
        choices: ['sales', 'production'].map(id => ({id, title: id === 'sales' ? '先保销售' : '先保生产', summary: '尚需确认到料时间。',
            allocations: [{document: 'SO-A', document_type: 'Sales Order', qty: 300, allocated_qty: 200, shortage_qty: 100, current_new_qty: 120}],
            shortage_qty: 100, current_free_qty: 200, cost: null, cost_note: '加急费用尚未报价', conditions: ['到料时间待确认']})),
        selected_plan: null, approved_orders: [], requirements: ['采购核实供方时间'],
        can: {choose: true, approve: false, reject: false, execute: false, submit_orders: false}, ...overrides}};
}
test('completed resolution compares allocations and unknown costs before the original collapsed response', () => {
    const {h, context} = environment(); let html = '';
    const t = turn(); h.turns = [t]; h.names = {control: '总控'};
    const node = {scrollTop: 0, scrollHeight: 300, clientHeight: 300, querySelectorAll: () => []};
    h.host = {find: () => ({0: node, html: value => {html = value;}, scrollTop() {}})};
    context.frappe.SupplyHub.prototype.renderMessages.call(h);
    assert.match(html, /采购待你决定/); assert.match(html, /先保销售/); assert.match(html, /先保生产/);
    assert.match(html, /当前自由库存/); assert.match(html, /本次新增 120/); assert.match(html, /费用<\/b> 待核实/);
    assert.match(html, /data-audit-key="resolution-context-TURN-1"/);
    assert.ok(html.indexOf('采购待你决定') < html.indexOf('完整模型原文'));
    assert.ok(!html.includes('<details open')); assert.equal(t.answer, '完整模型原文');
});
test('state labels and permitted buttons distinguish allocation approval from purchase approval', () => {
    const {h} = environment();
    for (const [state, label, action] of [['pending_approval', '分配方案待审批', 'approve'], ['approved', '分配已批准 · 待执行', 'execute'], ['executed', '方案执行已完成', null], ['blocked', '先补齐条件', null], ['stale', '数据已变化', null]]) {
        const t = turn({state, can: action ? {[action]: true} : {}, selected_plan: 'sales'});
        const html = h.resolutionCard(t); assert.ok(html.includes(label)); assert.match(html, /已选方案/);
        if (action) assert.ok(html.includes('data-resolution-action="' + action + '"'));
        assert.match(html, /分配批准不等于采购订单已批准/);
    }
});
test('synthetic sources cannot bind, approve, reserve or submit even if supplied capabilities say yes', async () => {
    for (const flags of [{kind: 'synthetic'}, {case: {is_simulation: true}}, {}]) {
        const {h} = environment(), t = turn({...flags, can: {choose: true, approve: true, reject: true, execute: true, submit_orders: true}});
        if (!flags.kind && !flags.case) t.synthetic = true;
        h.turns = [t]; const html = h.resolutionCard(t);
        assert.match(html, /合成演示 · 不可执行/); assert.match(html, /data-resolution-action="choose"[^>]* disabled/);
        assert.match(html, /data-resolution-action="execute"[^>]* disabled/);
        for (const action of ['bind_case', 'choose', 'approve', 'reject', 'execute', 'submit_order']) await h.resolutionRequest(t, action, {plan_id: 'sales'});
        assert.equal(h.calls.length, 0); assert.ok(!html.includes('data-resolution-action="bind_case"'));
    }
});
test('missing resolution leaves existing messages unchanged and running turns have no decision actions', () => {
    const {h} = environment(); assert.equal(h.resolutionCard({state: 'done'}), '');
    const t = turn(); t.state = 'running'; assert.equal(h.resolutionCard(t), '');
    assert.equal(h.resolutionAllowed(t, 'choose', {plan_id: 'sales'}), false);
});
test('resolution texts, attributes and callbacks escape HTML while null and nonfinite costs stay unknown', () => {
    const {h} = environment(), t = turn();
    t.name = 'T" onclick="bad'; t.resolution.title = '<script>unsafe</script>';
    t.resolution.choices[0].id = 'plan" onclick="bad'; t.resolution.choices[0].conditions = ['<img src=x onerror=bad>'];
    t.resolution.choices[0].cost = NaN; t.resolution.choices[1].cost = Infinity;
    t.resolution.requirements = ['<script>unsafe</script>'];
    const html = h.resolutionCard(t); assert.match(html, /&lt;script&gt;/); assert.match(html, /&lt;img/);
    assert.ok(!html.includes('<script>')); assert.ok(!html.includes('<img')); assert.ok(!html.includes(' onclick="'));
    assert.equal((html.match(/费用<\/b> 待核实/g) || []).length, 2);
});
test('approved purchase orders have a separate table and submit requires the exact current draft record', async () => {
    const {h, prompts} = environment(), t = turn({state: 'approved', can: {execute: true, submit_orders: true}, approved_orders: [
        {name: 'PO-1', supplier: 'Supplier', currency: 'CNY', grand_total: 1200, approval: {workflow_state: '已批准'}, source_hash: 'po-v1', docstatus: 0},
        {name: 'PO-2', supplier: 'Supplier', currency: 'CNY', grand_total: null, approval: '已批准', source_hash: 'po-v2', docstatus: 1}]});
    h.turns = [t]; const html = h.resolutionCard(t);
    assert.match(html, /已审批采购订单/); assert.match(html, /CNY 1200/); assert.match(html, /CNY 待核实/);
    assert.equal((html.match(/data-resolution-action="submit_order"/g) || []).length, 1);
    assert.equal(h.resolutionAllowed(t, 'submit_order', {order_name: 'PO-2'}), false);
    h.resolutionDialog(t.name, 'submit_order', {order_name: 'PO-1'});
    assert.match(prompts[0].fields[0].options, /真实采购订单/);
    await prompts[0].callback({reason: '按已批准采购单提交'});
    assert.equal(h.calls[0].args.action, 'submit_order'); assert.equal(h.calls[0].args.source_hash, 'po-v1'); assert.equal(h.calls[0].args.order_name, 'PO-1');
});
test('individual purchase permission and approval provenance are respected; execution receipt links actual records', () => {
    const {h} = environment(), t = turn({state: 'executed', can: {submit_orders: true},
        approved_orders: [{name: 'PO-NO', approval: 'APPROVAL-1', supplier: 'S', docstatus: 0, can_submit: false, source_hash: 'v1', items: [{item_code: '<item>', qty: 600, uom: '件'}]}],
        execution: {reservations: ['SRE-1'], tasks: ['TODO-1'], executed_by: '<buyer>', executed_at: '2026-11-01 09:00'}});
    assert.equal(h.resolutionAllowed(t, 'submit_order', {order_name: 'PO-NO'}), false);
    const html = h.resolutionCard(t);
    assert.match(html, /data-doctype="Operations Approval" data-docname="APPROVAL-1"/);
    assert.match(html, /data-resolution-action="submit_order"[^>]* disabled/);
    assert.match(html, /库存预留 1 条 · 跟进待办 1 项/); assert.match(html, /data-doctype="Stock Reservation Entry"/);
    assert.match(html, /data-doctype="ToDo"/); assert.match(html, /&lt;buyer&gt;/); assert.match(html, /&lt;item&gt; · 600 件/);
});
test('selection captures a reason and fingerprint, uses the action API and never reruns the model', async () => {
    const {h, prompts, messages} = environment(), t = turn(); h.turns = [t];
    h.resolutionDialog(t.name, 'choose', {plan_id: 'sales'});
    assert.equal(prompts[0].label, '选择并提交审批');
    await prompts[0].callback({reason: '   '}); assert.equal(h.calls.length, 0); assert.equal(messages.length, 1);
    t.resolution.fingerprint = 'changed-after-dialog';
    await prompts[0].callback({reason: ' 客户验收窗口已确认 '});
    assert.equal(h.calls.length, 1); assert.equal(h.calls[0].method, 'resolution_action');
    assert.equal(h.calls[0].args.fingerprint, 'version-1'); assert.equal(h.calls[0].args.reason, '客户验收窗口已确认');
    assert.equal(h.turns[0].resolution.state, 'pending_approval');
});
test('unbound result links a Supply Case without executing business operations', async () => {
    const {h, prompts} = environment(), t = turn({kind: 'unbound', choices: [], case: null, can: {}}); h.turns = [t];
    assert.match(h.resolutionCard(t), /关联异常并生成方案/);
    h.resolutionDialog(t.name, 'bind_case');
    assert.equal(prompts[0].fields.find(field => field.fieldname === 'case_name').options, 'Supply Case');
    await prompts[0].callback({reason: '关联当前缺料异常', case_name: 'CASE-BOUND'});
    assert.equal(h.calls[0].args.action, 'bind_case'); assert.equal(h.calls[0].args.case_name, 'CASE-BOUND');
});
test('duplicate clicks share one pending action and failed requests keep a retryable result', async () => {
    const {h, messages} = environment(), t = turn(); h.turns = [t]; let reject;
    h.call = (method, args) => {h.calls.push({method, args}); return new Promise((_, fail) => {reject = fail;});};
    const pending = h.resolutionRequest(t, 'choose', {plan_id: 'sales', reason: '窗口'});
    assert.match(h.resolutionCard(t), /data-resolution-action="choose"[^>]* disabled/);
    await h.resolutionRequest(t, 'choose', {plan_id: 'sales'}); assert.equal(h.calls.length, 1);
    reject(new Error('offline')); await pending; assert.equal(h.turns[0], t); assert.equal(h.resolutionPending.size, 0); assert.equal(messages.length, 1);
    h.call = async () => ({...t, resolution: {...t.resolution, state: 'pending_approval'}});
    await h.resolutionRequest(t, 'choose', {plan_id: 'sales'}); assert.equal(h.turns[0].resolution.state, 'pending_approval');
});
test('switching conversations during an action does not insert the old result into the new conversation', async () => {
    const {h} = environment(), t = turn(); h.turns = [t]; let resolve;
    h.call = () => new Promise(done => {resolve = done;});
    const pending = h.resolutionRequest(t, 'choose', {plan_id: 'sales'});
    const other = {...turn(), name: 'TURN-OTHER'}; h.conversation = 'other-conversation'; h.turns = [other];
    resolve({...t, resolution: {...t.resolution, state: 'approved'}}); await pending;
    assert.equal(h.turns.length, 1); assert.equal(h.turns[0], other); assert.equal(h.resolutionPending.size, 0);
});
test('a prompt from an old conversation or replaced result cannot execute after context changes', async () => {
    for (const change of ['conversation', 'module', 'result']) {
        const {h, prompts, messages} = environment(), t = turn(); h.turns = [t];
        h.resolutionDialog(t.name, 'choose', {plan_id: 'sales'}); h.resolutionDialog(t.name, 'choose', {plan_id: 'sales'});
        assert.equal(prompts.length, 1);
        if (change === 'result') h.turns = [{...t}]; else h[change] = 'other';
        await prompts[0].callback({reason: '确认选择'}); assert.equal(h.calls.length, 0); assert.equal(messages.length, 1);
    }
});
test('refresh uses the read-only status endpoint and response identity is verified', async () => {
    const {h, messages} = environment(), t = turn(); h.turns = [t];
    await h.resolutionDialog(t.name, 'refresh');
    assert.equal(h.calls[0].method, 'resolution_refresh'); assert.deepEqual(Object.keys(h.calls[0].args), ['name']);
    const current = h.turns[0]; h.call = async () => ({name: 'OTHER'});
    await h.resolutionRequest(current, 'refresh'); assert.equal(h.turns[0], current); assert.equal(messages.length, 1);
});
test('allocation units come from each row, plan or case; missing units never default to pieces', () => {
    const {h} = environment(), t = turn(); t.resolution.case.stock_uom = '千克';
    t.resolution.choices[0].stock_uom = '米'; t.resolution.choices[0].allocations[0].stock_uom = '<箱>';
    const html = h.resolutionCard(t);
    assert.match(html, /当前自由库存 · 米/); assert.match(html, /方案后仍缺 · 千克/);
    assert.match(html, /销售订单 · &lt;箱&gt;/); assert.match(html, /本次新增 120 &lt;箱&gt;/);
    const missing = h.resolutionCard(turn()); assert.match(missing, /当前自由库存 · 单位待核实/);
    assert.match(missing, /销售订单 · 单位待核实/); assert.ok(!missing.includes('120 件'));
});
test('selected policy and subsequent actions retain escaped actors, timestamps and complete long reasons', () => {
    const {h} = environment(), t = turn({selected_plan: 'sales', selected_by: '<buyer>', selected_at: '2026-11-01 09:00', reason: '客户验收窗口'});
    const reason = '保留完整审批依据。'.repeat(30) + '<script>bad</script>';
    t.resolution_actions = [{action: 'choose', by: '<buyer>', at: '2026-11-01 09:00', reason: '客户验收窗口'},
        {action: 'approve', by: 'approver', at: '2026-11-01 09:05', reason}, {action: 'submit_order', by: 'buyer', at: '2026-11-01 09:10', order: 'PO-1'}];
    const html = h.resolutionCard(t);
    assert.match(html, /选择依据与操作留痕 · 3 条/); assert.match(html, /本次选择依据/); assert.match(html, /&lt;buyer&gt;/);
    assert.match(html, /2026-11-01 09:05/); assert.match(html, /批准分配/); assert.match(html, /采购订单：PO-1/);
    assert.ok(html.includes(h.esc(reason))); assert.ok(!html.includes('<script>')); assert.ok(!html.includes('<details open'));
});
test('synthetic conditional candidates are separate from executable current stock and cannot be chosen', () => {
    const {h} = environment(), t = turn({kind: 'synthetic'});
    for (const choice of t.resolution.choices) {
        choice.stock_uom = '件'; choice.conditional_supply_qty = 560; choice.conditional_shortage_qty = 0;
        choice.allocations[0].conditional_allocated_qty = 300; choice.allocations[0].conditional_shortage_qty = 0;
    }
    const html = h.resolutionCard(t);
    assert.match(html, /当前自由库存 · 件<\/small><strong>200/);
    assert.match(html, /条件落实后候选 · 不计入当前可执行量/); assert.match(html, /候选供给 560 件；仍缺 0 件/);
    assert.match(html, /不构成齐料承诺/); assert.match(html, /data-resolution-action="choose"[^>]* disabled/);
    assert.ok(!html.includes('<details open'));
    t.resolution.kind = 'erp'; assert.ok(!h.resolutionCard(t).includes('条件落实后候选'));
});
function practiceTurn() {
    return {...turn({kind:'sandbox', sandbox:{registered:true,dataset_id:'TEST-DATASET',scope:'isolated_erp',label:'独立测试数据'},case:{name:'CASE-1',is_simulation:1,stock_uom:'件'}}),
        mode:'程序核算 · 本地虚拟数据', practice:{dataset_id:'TEST-DATASET',case_name:'CASE-1',label:'独立测试数据',
            metrics:[{label:'合格仓现存',value:612,unit:'件'},{label:'自由量',value:192,unit:'件'}],
            sources:[{doctype:'Supply Case',name:'CASE-1',label:'原始异常'}]}};
}
test('only the exact registered isolated sandbox may execute; legacy synthetic evidence never gains permission', () => {
    const {h}=environment(), t=practiceTurn();
    assert.equal(h.resolutionAllowed(t,'choose',{plan_id:'sales'}),true);
    t.resolution.can.execute=true;assert.equal(h.resolutionAllowed(t,'execute'),true);
    for(const change of [x=>x.resolution.sandbox.registered=false,x=>x.resolution.sandbox.registered=1,x=>x.resolution.sandbox.scope='global',x=>x.resolution.sandbox.dataset_id='',x=>x.resolution.kind='synthetic',x=>x.synthetic=true,x=>x.verified_evidence={}]) {
        const blocked=practiceTurn();change(blocked);assert.equal(h.resolutionAllowed(blocked,'choose',{plan_id:'sales'}),false);
    }
});
test('sandbox card labels actual writes, displays current source metrics and only model review depends on a model', () => {
    const {h}=environment(), t=practiceTurn();h.data={model_ready:false};
    const html=h.resolutionCard(t);
    assert.match(html,/虚拟数据 · 实际写入本地测试单据/);assert.match(html,/采购待你决定/);
    assert.match(html,/已读取业务单据；可直接选择，或继续四部门核查/);assert.match(html,/>612 /);assert.match(html,/>192 /);
    assert.ok(!html.includes('620'));assert.match(html,/data-doctype="Supply Case" data-docname="CASE-1"/);
    assert.match(html,/data-review-supply-practice="TURN-1" disabled/);
    assert.ok(!/data-resolution-action="choose"[^>]* disabled/.test(html));
    assert.match(html,/仍可直接选择方案、审批和执行/);
});
test('practice entry reads availability independently from dashboard model state and renders without a model', async () => {
    const {h}=environment();h.view='chat';h.data={};const rendered=[];h.render=()=>rendered.push(h.data);
    h.call=async(method,args)=>{h.calls.push({method,args});return method==='chat_status'?{practice_available:true}:{model_ready:false};};
    await h.refresh();assert.equal(h.data.practice_available,true);assert.equal(h.data.model_ready,false);
    assert.deepEqual(h.calls.map(row=>row.method),['dashboard','chat_status']);assert.equal(rendered.length,1);
});
test('opening practice uses no model API, prevents duplicate requests and inserts the completed result', async () => {
    const {h}=environment();h.data={practice_available:true,model_ready:false};let resolve;
    h.call=(method,args)=>{h.calls.push({method,args});return new Promise(done=>{resolve=done;});};
    const first=h.openSupplyPractice();await h.openSupplyPractice();assert.equal(h.calls.length,1);
    assert.equal(h.calls[0].method,'open_supply_practice');assert.equal(h.calls[0].args.conversation,'original-conversation');
    assert.equal(h.calls[0].args.request_id,'test-request-id');assert.equal(h.practiceOpening,true);
    const t=practiceTurn();resolve(t);await first;assert.equal(h.turns[0],t);assert.equal(h.practiceOpening,false);assert.equal(h.practiceRequest,null);
});
test('practice response from a previous conversation is discarded and unavailable entry does not send', async () => {
    const {h}=environment();h.data={practice_available:false};await h.openSupplyPractice();assert.equal(h.calls.length,0);
    h.data.practice_available=true;let resolve;h.call=()=>new Promise(done=>{resolve=done;});
    const pending=h.openSupplyPractice();h.conversation='new-conversation';h.turns=[{name:'NEW'}];resolve(practiceTurn());await pending;
    assert.equal(h.turns.length,1);assert.equal(h.turns[0].name,'NEW');assert.equal(h.practiceOpening,false);
});
test('failed practice reads keep the same request id for retry without adding a phantom message', async () => {
    const {h,messages}=environment();h.data={practice_available:true};
    h.call=async(method,args)=>{h.calls.push({method,args});if(h.calls.length===1)throw new Error('offline');return practiceTurn();};
    await h.openSupplyPractice();assert.equal(h.turns.length,0);assert.equal(h.practiceOpening,false);assert.equal(messages.length,1);
    await h.openSupplyPractice();assert.equal(h.calls[0].args.request_id,h.calls[1].args.request_id);assert.equal(h.turns.length,1);
});
test('four-department review explicitly sends the case identity to the existing start API and requires a model', async () => {
    const {h}=environment(), t=practiceTurn();h.turns=[t];h.data={model_ready:false};
    await h.reviewSupplyPractice(t.name);assert.equal(h.calls.length,0);
    h.data.model_ready=true;h.host={find:()=>({val:()=>''})};h.watch=async()=>{h.busy=false;};
    h.call=async(method,args)=>{h.calls.push({method,args});return {name:'REVIEW-TURN'};};
    await h.reviewSupplyPractice(t.name);
    assert.equal(h.calls.length,1);assert.equal(h.calls[0].method,'start');assert.equal(h.calls[0].args.supply_case,'CASE-1');
    assert.match(h.calls[0].args.question,/采购、销售、库存、生产四部门/);assert.match(h.calls[0].args.question,/CASE-1/);
    assert.equal(h.calls[0].args.scenario_id,undefined);assert.equal(h.calls[0].args.synthetic,undefined);
});
test('program-calculated turns do not claim model discussion and retain actual query audit', () => {
    const {h,context}=environment();let html='';const t=practiceTurn();t.events=[{id:1}];h.turns=[t];h.names={control:'总控'};h.data={model_ready:false};
    const node={scrollTop:0,scrollHeight:300,clientHeight:300,querySelectorAll:()=>[]};h.host={find:()=>({0:node,html:value=>{html=value;},scrollTop(){}})};
    h.auditTrail=()=>'<div>实际单据查询</div>';context.frappe.SupplyHub.prototype.renderMessages.call(h);
    assert.match(html,/程序核算 · 本地虚拟数据/);assert.match(html,/查看单据读取留痕/);assert.match(html,/实际单据查询/);
    assert.ok(!html.includes('合成场景 · 真实协作'));assert.ok(!html.includes('/10项反馈'));
});
test('practice quick entry is shown only for an available control dataset and stays enabled without a model', () => {
    const {h,context}=environment(),nodes=new Map();
    h.view='chat';h.names={control:'总控'};h.data={model_ready:false,practice_available:true};h.renderBoard=()=>{};
    h.host={find:selector=>{if(!nodes.has(selector))nodes.set(selector,{markup:'',each(){},html(value){this.markup=value;return this;},append(value){this.markup+=value;return this;},text(value){this.textValue=value;return this;},prop(name,value){this[name]=value;return this;}});return nodes.get(selector);}};
    context.frappe.SupplyHub.prototype.render.call(h);context.frappe.SupplyHub.prototype.renderBusy.call(h);
    assert.match(nodes.get('.hub-prompts').markup,/data-open-supply-practice/);assert.equal(nodes.get('[data-open-supply-practice]').disabled,false);
    assert.equal(nodes.get('[data-review-supply-practice]').disabled,true);assert.match(nodes.get('.hub-model-note').textValue,/仍可处理连接器延期/);
    h.data.practice_available=false;context.frappe.SupplyHub.prototype.render.call(h);
    assert.ok(!nodes.get('.hub-prompts').markup.includes('data-open-supply-practice'));
});
test('executed practice separates current free stock from the approved historical allocation snapshot', () => {
    const {h}=environment(),t=practiceTurn();h.data={model_ready:false};
    t.resolution.state='executed';t.resolution.can={};t.resolution.selected_plan='sales';
    t.resolution.choices=t.resolution.choices.slice(0,1);
    t.resolution.choices[0].stock_uom='件';t.resolution.choices[0].summary='本次可分配现存自由库存 200 件';
    t.resolution.choices[0].allocations[0].current_new_qty=200;
    t.practice.metrics=[{label:'当前自由库存',value:0,unit:'件'}];
    const html=h.resolutionCard(t),snapshot=html.slice(html.indexOf('执行时获批方案'));
    assert.match(html,/当前自由库存<\/span><strong>0 /);
    assert.match(html,/已执行；本区展示当前库存，下方为当时获批方案与回执/);
    assert.match(html,/执行前可用量 200 件/);assert.match(html,/执行前可用量 · 件<\/small><strong>200/);
    assert.match(html,/本次已预留 200 件/);assert.match(html,/当时需求/);assert.match(html,/获批分配/);
    assert.ok(!snapshot.includes('当前自由库存'));assert.ok(!snapshot.includes('本次新增'));assert.ok(!snapshot.includes('本次可分配现存自由库存'));
    assert.ok(!html.includes('可直接选择'));assert.match(html,/仍可查看实际库存、审批记录和执行回执/);
    assert.equal(t.resolution.choices[0].summary,'本次可分配现存自由库存 200 件');
});
