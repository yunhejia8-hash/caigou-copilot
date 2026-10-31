/* 供应协同工作台：所有数量与审批校验在服务端执行。 */
(() => {
  const esc = value => frappe.utils.escape_html(String(value ?? ''));
  const call = async (method, args = {}) => (await frappe.call({method: `suke_ops.api.${method}`, args, freeze: true, freeze_message: '正在核对业务单据…'})).message;
  const link = (type, name) => `<a href="/app/${frappe.router.slug(type)}/${encodeURIComponent(name)}">${esc(name)}</a>`;
  const num = value => Number(value || 0).toLocaleString('zh-CN', {maximumFractionDigits: 2});
  const table = (heads, rows) => `<div style="overflow:auto"><table class="table table-bordered"><thead><tr>${heads.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  async function openPurchaseChat(frm) {
    if (frm.is_new() || frm.is_dirty()) {
      frappe.msgprint('请先保存当前单据，再打开采购对话。对话只读取已保存的数据。');
      return;
    }
    const name = frm.doc.name;
    if (frm.purchase_chat?.caseName === name) {
      frm.purchase_chat.dialog.show();
      frm.purchase_chat.hub.refresh();
      return;
    }
    if (frm.purchase_chat_loading) return;
    frm.purchase_chat_loading = true;
    try {
      await frappe.require(['/assets/suke_ops/hub.js?v=supply-decision-v1.js', '/assets/suke_ops/hub.css?v=supply-decision-v1.css']);
      // 资源加载期间可能切换或修改单据，不将旧上下文带到新页面。
      const route = frappe.get_route();
      if (frm.doc.name !== name || frm.is_dirty() ||
          route[0] !== 'Form' || route[1] !== frm.doc.doctype || route[2] !== name ||
          typeof cur_frm === 'undefined' || cur_frm !== frm) return;
      const dialog = new frappe.ui.Dialog({title: '采购对话 · ' + name, size: 'extra-large', fields: [{fieldname: 'chat', fieldtype: 'HTML'}]});
      const hub = new frappe.SupplyHub({main: dialog.fields_dict.chat.$wrapper}, {[frm.doc.doctype === 'Purchase Order' ? 'orderName' : 'caseName']: name, onNavigate: () => dialog.hide()});
      frm.purchase_chat = {caseName: name, dialog, hub};
      dialog.show();
    } catch (_) {
      frappe.msgprint('采购对话加载失败，请重试或进入协同主页。');
    } finally { frm.purchase_chat_loading = false; }
  }
  frappe.ui.form.on('Purchase Order', {
    refresh(frm) {
      if (!frm.is_new()) frm.add_custom_button('到货跟踪与采购对话', () => openPurchaseChat(frm));
    }
  });
  async function render(frm) {
    const host = frm.fields_dict.dashboard.$wrapper;
    host.html('<p class="text-muted">正在读取库存、销售与生产需求…</p>');
    try {
      const data = await call('get_analysis', {name: frm.doc.name});
      frm.supply_analysis = data;
      const ctx = data.context, first = data.plans[0];
      const stats = [['仓库现存', ctx.actual_qty], ['已有预留', first.locked_qty], ['可自由分配', first.current_free_qty], ['未确认先发', first.unconfirmed_qty], ['需货时点缺口', first.shortage_qty]];
      host.html(`<style>.supply-panel{border:1px solid var(--border-color);border-radius:12px;padding:20px;margin:16px 0}.supply-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px}.supply-stat{background:var(--subtle-fg);padding:15px;border-radius:10px}.supply-stat strong{display:block;font-size:26px;margin-top:6px}.supply-plans{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:16px}.supply-panel th,.supply-panel td{font-size:12px}.supply-agent{white-space:pre-wrap;line-height:1.7}.supply-actions{display:flex;gap:10px;flex-wrap:wrap}</style>
        <div class="supply-panel"><div class="supply-actions"><span class="indicator-pill orange">${first.shortage_qty ? '需要负责人决策' : '当前数量已覆盖'}</span><span class="text-muted">${frm.doc.is_simulation ? '合成业务 · 模拟供应商' : '业务数据'}</span></div><h3 style="margin-top:15px">${esc(frm.doc.event_message || frm.doc.title)}</h3><p class="text-muted">${esc(ctx.date_assumption)}</p><div class="supply-metrics">${stats.map(([name, value]) => `<div class="supply-stat">${name}<strong>${num(value)}</strong>件</div>`).join('')}</div></div>
        <div class="supply-panel"><h4>需求与已有占用</h4>${table(['来源', '需求类型', '需要数量', '需用时间', '关联销售'], ctx.demands.map(r => [link(r.document_type, r.document), r.kind === 'sales' ? esc(r.priority) : '生产领料', num(r.qty), esc(r.need_by.replace('T', ' ')), r.root_sales_order ? link('Sales Order', r.root_sales_order) : '直接销售']))}<p>已有预留：${ctx.reservations.map(r => `${esc(r.document)} · ${num(r.qty)}件`).join('；') || '无'}。重新分配前须处理原占用，本工作台默认保持现有预留。</p></div>
        <div class="supply-panel"><h4>供货与模拟议价</h4>${table(['来源', '数量', '预计到仓', '确认状态'], ctx.supplies.map(r => [esc(r.purchase_order || r.id), num(r.qty), esc(r.eta.replace('T', ' ')), r.confirmed ? '已确认' : '可能先发，不计入可用']))}<div class="supply-actions"><button class="btn btn-default" data-op="negotiate">与模拟供应商议价</button><button class="btn btn-default" data-op="refresh">刷新实时数据</button><button class="btn btn-primary" data-op="agent">运行异常决策 Agent</button></div>${data.supplier.rounds ? `<details style="margin-top:15px"><summary>查看模拟议价记录 · ${esc(data.supplier.status)}</summary>${data.supplier.rounds.map(r => `<p>第${r.round}轮：${esc(r.buyer)}<br>${esc(r.supplier)}</p>`).join('')}<p>新增成本：${num(data.supplier.extra_cost)} ${esc(frm.doc.company ? '（公司币种）' : '')}；${esc(data.supplier.note)}</p></details>` : ''}</div>
        <div class="supply-plans">${data.plans.map(p => `<div class="supply-panel"><h4>${esc(p.title)}</h4><p class="text-muted">${esc(p.note)}</p>${table(['需求单据', '分配合计', '其中已锁定', '未覆盖', '可开工成品'], p.allocations.map(r => [link(r.document_type, r.document), num(r.allocated_qty), num(r.locked_qty), num(r.shortage_qty), r.kind === 'production' ? num(r.production_units) : '—']))}<p><b>${p.fully_feasible ? '全部需求在时间范围内覆盖' : `仍有${num(p.shortage_qty)}件缺口，需要后续处置`}</b></p><button class="btn btn-primary" data-plan="${p.id}">选择此方案并提交审批</button></div>`).join('')}</div>
        <div class="supply-panel"><h4>跨模块审批与执行</h4>${data.decisions.length ? table(['方案单', '选择', '状态', '说明'], data.decisions.map(d => [link('Supply Decision', d.name), d.selected_plan === 'sales_first' ? '销售优先' : '生产优先', esc(d.workflow_state), esc(d.reason)])) : '<p class="text-muted">等待负责人选择方案。系统不会自动决定销售与生产的优先顺序。</p>'}</div>
        <div class="supply-panel"><h4>Agent 分析</h4>${data.agent.answer ? `<p class="text-muted">真实模型：${esc(data.agent.model)} · ${esc(data.agent.at)} ${data.agent.fingerprint !== data.fingerprint ? '· 数据已变化，请重新运行分析' : ''}</p><div class="supply-agent">${esc(data.agent.answer)}</div><details><summary>工具调用证据</summary><pre>${esc(JSON.stringify(data.agent.trace, null, 2))}</pre></details>` : '<p class="text-muted">点击“运行异常决策 Agent”生成有单据依据的方案解释。上方数量由程序实时计算。</p>'}</div>`);
      if (data.supplier.supplier_quotation) host.find('[data-op="negotiate"]').parent().after(`<p style="margin-top:12px">议价报价草稿：${link('Supplier Quotation', data.supplier.supplier_quotation)}；原采购单商业条款变更仍需审批。</p>`);
      host.find('[data-op="refresh"]').on('click', () => render(frm));
      host.find('[data-op="agent"]').on('click', async () => { await call('run_agent', {name: frm.doc.name}); await frm.reload_doc(); });
      host.find('[data-op="negotiate"]').on('click', () => frappe.prompt([
        {fieldname: 'ceiling', fieldtype: 'Currency', label: '授权单价上限（演示最高23）', default: 23, reqd: 1},
        {fieldname: 'max_rounds', fieldtype: 'Int', label: '最多议价轮数', default: 3, reqd: 1}
      ], async values => { await call('negotiate', {name: frm.doc.name, ...values}); await frm.reload_doc(); }, '模拟供应商议价', '开始模拟'));
      host.find('[data-plan]').on('click', function () {
        const selected = this.dataset.plan;
        frappe.prompt([{fieldname: 'reason', fieldtype: 'Small Text', label: '选择依据与可接受影响', reqd: 1}], async values => {
          const decision = await call('propose', {name: frm.doc.name, selected_plan: selected, fingerprint: data.fingerprint, reason: values.reason});
          const doc = await frappe.db.get_doc('Supply Decision', decision);
          if (doc.workflow_state === '草稿') await call('transition', {name: decision, action: '提交审批'});
          frappe.set_route('Form', 'Supply Decision', decision);
        }, '负责人选择方案', '提交审批');
      });
    } catch (error) { host.html('<p class="text-danger">实时分析失败，请检查提示后重试。</p>'); throw error; }
  }
  frappe.ui.form.on('Supply Case', {refresh(frm) { if (!frm.is_new()) { render(frm); frm.add_custom_button('实时刷新', () => render(frm)); frm.add_custom_button('采购对话', () => openPurchaseChat(frm)); } }});
  frappe.ui.form.on('Supply Decision', {refresh(frm) {
    if (frm.is_new()) return;
    const state = frm.doc.workflow_state;
    frm.add_custom_button('查看关联异常', () => frappe.set_route('Form', 'Supply Case', frm.doc.supply_case));
    const actions = {'草稿': ['提交审批'], '待审批': ['批准', '驳回'], '已驳回': ['修正重提']}[state] || [];
    const approver = frappe.user.has_role('Supply Approver') || frappe.user.has_role('System Manager') || frappe.session.user === 'Administrator';
    actions.filter(action => !['批准', '驳回'].includes(action) || approver).forEach(action => frm.add_custom_button(action, () => {
      const send = async reason => { await call('transition', {name: frm.doc.name, action, reason}); await frm.reload_doc(); };
      if (['驳回', '修正重提'].includes(action)) frappe.prompt([{fieldname: 'reason', fieldtype: 'Small Text', label: '原因／修正说明', reqd: 1}], v => send(v.reason), action, action);
      else send();
    }));
    if (state === '已批准' && approver) frm.add_custom_button('执行已批准方案', async () => { await call('execute', {name: frm.doc.name}); await frm.reload_doc(); });
  }});
  ['Purchase Order', 'Sales Order', 'Work Order', 'Stock Entry'].forEach(type => frappe.ui.form.on(type, {refresh(frm) {
    if (frm.is_new() || frm.doc.docstatus !== 0) return;
    frm.add_custom_button('申请协同审批', () => {
      if (frm.is_dirty()) { frappe.msgprint('请先保存单据，再申请审批。'); return; }
      frappe.prompt([{fieldname: 'reason', fieldtype: 'Small Text', label: '审批说明', reqd: 1}], async values => {
        const response = await frappe.call({method: 'suke_ops.approval.request', args: {document_type: type, document_name: frm.doc.name, reason: values.reason}, freeze: true});
        frappe.set_route('Form', 'Operations Approval', response.message);
      }, '四模块业务单据审批', '创建审批申请');
    });
  }}));
  frappe.ui.form.on('Operations Approval', {refresh(frm) {
    if (frm.is_new()) return;
    frm.add_custom_button('查看业务单据', () => frappe.set_route('Form', frm.doc.source_type, frm.doc.source_name));
    const actions = {'草稿': ['提交审批'], '待审批': ['批准', '驳回'], '已驳回': ['修正重提']}[frm.doc.workflow_state] || [];
    actions.forEach(action => frm.add_custom_button(action, () => {
      const send = async reason => { await frappe.call({method: 'suke_ops.approval.transition', args: {name: frm.doc.name, action, reason}, freeze: true}); await frm.reload_doc(); };
      if (['驳回', '修正重提'].includes(action)) frappe.prompt([{fieldname: 'reason', fieldtype: 'Small Text', label: '原因／修正说明', reqd: 1}], v => send(v.reason), action, action);
      else send();
    }));
  }});
})();
