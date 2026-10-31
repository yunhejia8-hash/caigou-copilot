/* 采购流程与经验：业务状态来自服务端，提交、审批和付款仍使用原生单据。 */
frappe.ProcurementWorkspace = class {
    constructor(wrapper) {
        this.page = frappe.ui.make_app_page({parent: wrapper, title: '采购流程与经验', single_column: true});
        this.host = $('<div class="procurement-workspace"></div>').appendTo(this.page.main);
        this.stage = 'demand';
        this.stages = {demand:'需求',stock:'库存核对',inquiry:'询价',comparison:'比价',approval:'审批',order:'下单与催交',receipt:'收货质检',finance:'对账付款',review:'结案复盘'};
        this.types = ['Material Request','Request for Quotation','Supplier Quotation','Purchase Order','Purchase Receipt','Quality Inspection','Purchase Invoice','Payment Entry','Operations Approval','Supply Case','Supply Decision'];
        this.labels = {'Material Request':'物料需求单','Request for Quotation':'询价单','Supplier Quotation':'供应商报价','Purchase Order':'采购订单','Purchase Receipt':'收货单','Quality Inspection':'质检单','Purchase Invoice':'采购发票','Payment Entry':'付款单','Operations Approval':'协同审批','Supply Case':'供应异常','Supply Decision':'异常决策'};
        this.page.set_primary_action('从需求单开始', () => this.create());
        this.page.add_inner_button('刷新', () => this.load());
        this.page.add_inner_button('原采购工作台', () => frappe.set_route('buying/operations-buying'));
        this.host.on('change', '[data-process]', e => { this.process = e.target.value; this.load(); });
        this.host.on('click', '[data-stage]', e => { this.stage = e.currentTarget.dataset.stage; this.show(); });
        this.host.on('click', '[data-action]', e => this.act(e.currentTarget.dataset.action));
        this.host.on('click', '[data-verify]', e => this.verify(e.currentTarget.dataset.verify));
        this.host.on('click', '[data-rule]', e => this.publish(e.currentTarget.dataset.rule, e.currentTarget.dataset.operation));
        this.host.on('click', '[data-native]', e => { const d=e.currentTarget.dataset; frappe.set_route('Form',d.native,d.name); });
        this.load();
    }
    esc(value) { return frappe.utils.escape_html(String(value ?? '')); }
    conditions(rows=[]) {
        const fields={late_days:'延期天数',quote_count:'有效报价数量',supplier:'供应商',item_group:'物料类别',quality_rejected:'存在质检拒收'};
        const ops={eq:'等于',ne:'不等于',gte:'大于等于',lte:'小于等于',gt:'大于',lt:'小于'};
        return rows.map(r=>`${fields[r.field]||r.field} ${ops[r.op]||r.op} ${typeof r.value==='boolean'?(r.value?'是':'否'):r.value}`).join('；');
    }
    sources(rows=[]) {
        return rows.map(r=>`<button class="btn btn-xs btn-default" data-native="${this.esc(r.doctype)}" data-name="${this.esc(r.name)}">${this.esc(this.labels[r.doctype]||r.doctype)} · ${this.esc(r.name)} ↗</button><small>来源版本：${this.esc(r.modified)}</small>`).join('');
    }
    async call(method, args={}) {
        return (await frappe.call({method:'suke_ops.procurement_api.'+method,args,freeze: !['overview','listing','library','knowledge'].includes(method)})).message;
    }
    prompt(fields, callback, title) {
        frappe.prompt(fields, async values => {
            try { await callback(values); await this.load(); }
            catch (_) { /* 服务端显示具体错误，保留原工作台供重试。 */ }
        }, title, '保存');
    }
    async load() {
        const seq = this.sequence = (this.sequence || 0) + 1;
        this.host.attr('aria-busy','true');
        try {
            const list = await this.call('listing');
            if (seq !== this.sequence) return;
            this.list = list;
            this.process = this.process || list.processes[0]?.name;
            if (!this.process) {
                this.host.html('<div class="pw-empty"><h3>把一笔采购从需求跟进到复盘</h3><p>选择已有物料需求单开始，或先在原采购工作台登记需求。已有单据和对话继续使用。</p><button class="btn btn-primary" data-action="create">从需求单开始</button></div>');
                return;
            }
            const [data, library] = await Promise.all([this.call('overview',{process:this.process}),this.call('library',{process:this.process})]);
            if (seq !== this.sequence) return;
            this.data = data; this.library = library;
            this.show();
        } catch (_) {
            if (seq === this.sequence) this.host.html('<div class="pw-empty">采购闭环当前不可用。原采购工作台和普通问答仍可使用；管理员可检查采购设置。<p><a href="/desk/buying/operations-buying">打开原采购工作台</a></p></div>');
        } finally { if (seq === this.sequence) this.host.attr('aria-busy','false'); }
    }
    table(columns, rows) {
        return rows.length ? `<div class="pw-table"><table><thead><tr>${columns.map(c=>`<th>${this.esc(c[1])}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${columns.map(c=>`<td>${this.esc(r[c[0]])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : '<p class="text-muted">暂无关联记录。请关联已有单据或准备下一步草稿。</p>';
    }
    show() {
        const d = this.data;
        if (!d) return;
        const reviewCurrent = !!d.business_fingerprint && d.review.business_fingerprint === d.business_fingerprint;
        const reviewSavings = !Object.keys(d.review).length ? '尚未核算节省' : !reviewCurrent ? '历史复盘已失效，节省须重新核实' :
            `已核实节省：${d.review.realized_savings == null ? '暂无法核算' : d.review.realized_savings + ' ' + d.review.currency}`;
        const state = {pending:'待处理',partial:'进行中 / 待核实',done:'已记录完成'};
        const current = d.stages.find(s=>s.id===this.stage);
        const stageDocs = {demand:['Material Request'],stock:['Material Request'],inquiry:['Request for Quotation'],comparison:['Supplier Quotation'],approval:['Operations Approval'],order:['Purchase Order','Supply Case','Supply Decision'],receipt:['Purchase Receipt','Quality Inspection'],finance:['Purchase Invoice','Payment Entry'],review:[]};
        const docs = d.documents.filter(r=>stageDocs[this.stage].includes(r.doctype));
        const buttons = this.list.can_edit ? '<button data-action="attach" class="btn btn-default">关联单据</button><button data-action="discover" class="btn btn-default">同步后续单据</button><button data-action="experience" class="btn btn-default">记录经验</button><button data-action="import" class="btn btn-default">导入历史判断</button>' : '';
        const actions = {demand:'核对需求单后在原生表单提交；数量和需求日期须有依据。',stock:'现存、预留、冻结和按时在途分开核实；不把历史销量当作库存。',inquiry:'打开已提交需求单准备询价草稿。询价对象、外发消息和报价录入由采购人员确认。',comparison:'逐物料核对币种、计量单位、报价有效期、税费、运费、质量和交期。原生报价表单保留完整条件。',approval:'打开采购订单草稿，使用原有协同审批；修改关键内容后重新申请。',order:'跟踪物料行交期和分批交货；异常可关联Supply Case处理。',receipt:'按实收和质检结果入库；拒收、退货不能算合格交付。',finance:'发票核对订单和收货，付款核对到期条件及差异；由财务角色人工处理。',review:'实际结果与预计收益分开；没有可比基准时不计算节省。'};
        this.host.html(`<div class="pw-toolbar"><label>采购流程 <select data-process>${this.list.processes.map(p=>`<option value="${this.esc(p.name)}" ${p.name===this.process?'selected':''}>${this.esc(p.title)} · ${this.esc(p.company)}${p.synthetic?' · 合成':''}</option>`).join('')}</select></label><span>${this.esc(d.synthetic?'合成演示':'业务流程')} · ${this.esc(d.at)}</span></div>
        <nav class="pw-stages" aria-label="采购全流程">${d.stages.map(s=>`<button data-stage="${s.id}" aria-current="${s.id===this.stage?'step':'false'}" class="${s.state}"><b>${this.esc(s.title)}</b><small>${state[s.state]}</small></button>`).join('')}</nav>
        <div class="pw-columns"><section class="pw-card"><h3>${this.esc(current.title)}</h3><p>${this.esc(actions[this.stage])}</p><p class="pw-next">下一步：${this.esc(current.next_step)}</p>
        <div class="pw-actions">${buttons}<button data-action="draft" class="btn btn-default">准备下游草稿</button><button data-action="chat" class="btn btn-default">与采购 Agent 讨论</button></div>
        ${this.stage==='stock'?this.table([['item_code','物料'],['warehouse','仓库'],['qty','需求总量'],['uom','单位'],['actual_qty','现存'],['reserved_stock','已锁定'],['ordered_qty','已订购']],d.stock)+'<button class="btn btn-default" data-action="stock">记录库存及按时在途核对</button>':''}
        ${this.stage==='comparison'?this.table([['name','报价单'],['supplier','供应商'],['item_code','物料'],['qty','报价数量'],['uom','单位'],['rate','单价'],['currency','币种'],['valid_till','有效至']],d.quotations):''}
        ${this.stage==='finance'?this.table([['invoice','采购发票'],['item_code','物料'],['qty','开票数量'],['uom','单位'],['rate','单价'],['currency','币种'],['outstanding','整张发票未付'],['note','待核对']],d.reconciliation):''}
        ${docs.map(r=>`<button class="pw-document" data-native="${this.esc(r.doctype)}" data-name="${this.esc(r.name)}"><b>${this.esc(this.labels[r.doctype])} · ${this.esc(r.name)}</b><span>${this.esc(r.docstatus===2?'已取消':r.workflow_state||r.status||(r.docstatus===0?'草稿':'已提交'))} ↗</span></button>`).join('')}
        ${this.stage==='review'?`<p>${this.esc(d.review.summary||'尚未完成结果复盘')}</p><p>${this.esc(reviewSavings)}</p>${this.list.can_review?'<button class="btn btn-default" data-action="review">核实结果与成本口径</button><button class="btn btn-default" data-action="reopen">重新打开复盘</button>':''}`:''}
        ${d.warnings.map(w=>`<p class="pw-warning">${this.esc(w)}</p>`).join('')}</section>
        <aside><section class="pw-card"><h3>本环节经验参考</h3><p class="text-muted">当前事实和企业制度优先；历史经验不替代审批。</p><button data-action="knowledge" class="btn btn-default">查找适用经验</button><div class="pw-knowledge"></div></section>
        <section class="pw-card"><h3>本流程经验</h3>${this.library.experiences.map(r=>`<div class="pw-record"><b>${this.esc(r.title)}</b><small>${this.esc(this.stages[r.stage])} · ${r.verified?'已核实':'待核实'} · ${this.esc({unknown:'结果尚未验证',positive:'有效',negative:'反例'}[r.outcome])}${r.synthetic?' · 合成':''}</small><details><summary>判断依据与来源</summary><p>${this.esc(r.situation)}</p><p>建议及例外：${this.esc(r.recommendation)}</p><p>适用条件：${this.esc(this.conditions(r.conditions))}</p>${this.sources(r.sources)}<p>${this.esc(r.verification?.note||'尚无核实记录')}</p></details>${this.list.can_review?`<button data-verify="${this.esc(r.name)}" class="btn btn-xs btn-default">核实</button>`:''}</div>`).join('')||'<p class="text-muted">暂无经验</p>'}</section>
        <section class="pw-card"><h3>规则持续改进</h3><p class="text-muted">从至少5笔不同流程的已核实经验归纳，保留独立验证和反例。</p>${this.list.can_edit?'<button data-action="evolve" class="btn btn-default">总结候选规则</button>':''}${this.library.rules.map(r=>`<div class="pw-record"><b>${this.esc(r.title)}</b><p>${this.esc(r.recommendation)}</p><small>${this.esc(r.state)} · 留出验证 ${r.evaluation.tested} 笔 / 反例 ${r.evaluation.negative} 笔</small>${this.list.can_review?`<button data-rule="${this.esc(r.name)}" data-operation="${r.state==='active'?'disable':'activate'}" class="btn btn-xs btn-default">${r.state==='active'?'停用':'审核启用 / 回退至此版'}</button>`:''}</div>`).join('')}</section></aside></div>
        <details class="pw-card"><summary>整条单据链 · ${d.documents.length} 张</summary>${d.documents.map(r=>`<button class="pw-document" data-native="${this.esc(r.doctype)}" data-name="${this.esc(r.name)}">${this.esc(this.labels[r.doctype])} · ${this.esc(r.name)} ↗</button>`).join('')}</details>`);
    }
    create() {
        this.prompt([{fieldname:'material_request',fieldtype:'Link',options:'Material Request',label:'采购类型物料需求单',reqd:1},{fieldname:'title',fieldtype:'Data',label:'采购主题'}],async v=>{this.process=await this.call('create',v);},'从已有需求单建立采购流程');
    }
    attach() {
        this.prompt([{fieldname:'document_type',fieldtype:'Select',options:this.types,label:'单据类型',reqd:1},{fieldname:'document_name',fieldtype:'Dynamic Link',options:'document_type',label:'引用当前上游的单据',reqd:1}],v=>this.call('attach',{process:this.process,...v}),'关联采购单据');
    }
    experience() {
        const fields={'延期天数':'late_days','有效报价数量':'quote_count','供应商':'supplier','物料类别':'item_group','存在质检拒收':'quality_rejected'};
        const operators={'等于':'eq','不等于':'ne','大于等于':'gte','小于等于':'lte','大于':'gt','小于':'lt'};
        this.prompt([{fieldname:'stage',fieldtype:'Select',options:Object.values(this.stages),label:'环节',default:this.stages[this.stage],reqd:1},
            {fieldname:'title',fieldtype:'Data',label:'经验主题',reqd:1},{fieldname:'kind',fieldtype:'Select',options:['案例','专家经验','企业制度'],label:'经验类型',default:'案例',reqd:1},
            {fieldname:'situation',fieldtype:'Long Text',label:'场景、来源、判断依据',reqd:1},{fieldname:'recommendation',fieldtype:'Small Text',label:'建议行动及不适用情况',reqd:1},
            {fieldname:'condition_field',fieldtype:'Select',options:Object.keys(fields),label:'适用条件',default:'延期天数',reqd:1},
            {fieldname:'condition_op',fieldtype:'Select',options:Object.keys(operators),label:'比较方式',default:'大于等于',reqd:1},
            {fieldname:'condition_value',fieldtype:'Data',label:'条件值（天数/数量填数字；存在拒收填“是”或“否”）',default:'1',reqd:1}],v=>{
                const field=fields[v.condition_field], op=operators[v.condition_op];
                let value=v.condition_value;
                if(['late_days','quote_count'].includes(field)) {value=Number(value);if(!Number.isFinite(value))throw new Error('请填写数字');}
                if(field==='quality_rejected'){if(!['是','否'].includes(value))return frappe.msgprint('是否拒收请填写“是”或“否”');value=value==='是';}
                return this.call('record_experience',{process:this.process,stage:Object.keys(this.stages).find(k=>this.stages[k]===v.stage),title:v.title,situation:v.situation,recommendation:v.recommendation,
                    kind:{'案例':'case','专家经验':'expert','企业制度':'policy'}[v.kind],conditions:JSON.stringify([{field,op,value}])});
            },'记录判断，稍后核实实际效果');
    }
    verify(name) {
        this.prompt([{fieldname:'outcome',fieldtype:'Select',options:['尚未验证','有效','反例'],label:'实际结果',reqd:1},{fieldname:'note',fieldtype:'Small Text',label:'核实依据及适用限制',reqd:1}],v=>this.call('verify_experience',{name,note:v.note,outcome:{'尚未验证':'unknown','有效':'positive','反例':'negative'}[v.outcome]}),'核实经验');
    }
    publish(name, action) {
        this.prompt([{fieldname:'reason',fieldtype:'Small Text',label:'审核或停用原因',reqd:1},...(action==='activate'?[{fieldname:'valid_until',fieldtype:'Date',label:'有效至（一年内）',reqd:1}]:[])],v=>this.call('publish_rule',{name,action,...v}),'审核规则版本');
    }
    async act(action) {
        if (['create','attach','experience'].includes(action)) return this[action]();
        if (action==='discover') { const r=await this.call('discover',{process:this.process}); frappe.show_alert(`已关联 ${r.added.length} 张后续单据`); return this.load(); }
        if (action==='knowledge') {
            const process=this.process,stage=this.stage;
            const r=await this.call('knowledge',{process,stage});
            if(process!==this.process||stage!==this.stage)return;
            this.host.find('.pw-knowledge').html(`<p>${this.esc(r.note||'历史参考须核对适用条件')}</p>`+[...r.rules,...r.experiences].map(k=>`<div class="pw-record"><b>${this.esc(k.title||k.name)}${k.version?' · v'+this.esc(k.version):''}</b>${k.outcome==='negative'?'<p class="pw-warning">历史反例，不宜照搬</p>':''}${k.legacy?`<p>历史判断原文：${this.esc(k.source_excerpt)}${k.excerpt_truncated?'（已截断，完整内容请查看来源）':''}</p>`:''}<p>${this.esc(k.recommendation)}</p><small>条件：${this.esc(this.conditions(k.conditions))}</small>${this.sources(k.sources)}</div>`).join('')); return;
        }
        if(action==='evolve'){const r=await this.call('evolve',{company:this.data.company});frappe.msgprint(`${r.note}；本次候选 ${r.candidates.length} 条。`);return this.load();}
        if(action==='stock')return this.prompt([{fieldname:'note',fieldtype:'Small Text',label:'逐物料核对可用库存、冻结/待检、按时在途及来源',reqd:1}],v=>this.call('confirm_stock',{process:this.process,fingerprint:this.data.fingerprint,...v}),'记录人工核对依据');
        if(action==='reopen')return this.prompt([{fieldname:'reason',fieldtype:'Small Text',label:'重新打开原因',reqd:1}],v=>this.call('reopen',{process:this.process,...v}),'重新打开复盘');
        if(action==='review')return this.prompt([{fieldname:'summary',fieldtype:'Long Text',label:'实际结果、差异、成本构成及可比口径',reqd:1},{fieldname:'evidence_type',fieldtype:'Select',options:['Purchase Receipt','Quality Inspection','Purchase Invoice','Payment Entry'],label:'结果证据类型',reqd:1},{fieldname:'evidence_name',fieldtype:'Dynamic Link',options:'evidence_type',label:'已关联结果单据',reqd:1},{fieldname:'baseline',fieldtype:'Currency',label:'可比基准总成本（无依据可留空）'},{fieldname:'actual',fieldtype:'Currency',label:'实际总成本（含相关费用）'},{fieldname:'comparable',fieldtype:'Check',label:'已核实同规格、数量、币种、税费和交期口径；不重复计入收益'}],v=>this.call('review',{process:this.process,...v}),'结果复盘');
        if(action==='import')return this.prompt([{fieldname:'source_type',fieldtype:'Select',options:['Supply Decision','Operations Approval','Supply Case'],label:'已关联历史来源',reqd:1},{fieldname:'source_name',fieldtype:'Dynamic Link',options:'source_type',label:'来源单号',reqd:1},{fieldname:'stage',fieldtype:'Select',options:Object.values(this.stages),label:'环节',default:this.stages[this.stage],reqd:1}],v=>this.call('import_judgement',{process:this.process,...v,stage:Object.keys(this.stages).find(k=>this.stages[k]===v.stage)}),'导入旧判断（默认待核实）');
        if(action==='draft'){
            const actions={'准备询价单':'inquiry_from_request','依据报价准备采购单':'purchase_from_quotation','准备收货单':'receive_purchase','依据收货准备采购发票':'invoice_from_receipt','依据发票准备付款单':'payment_from_invoice'};
            return this.prompt([{fieldname:'source_type',fieldtype:'Select',options:['Material Request','Supplier Quotation','Purchase Order','Purchase Receipt','Purchase Invoice'],label:'已关联来源类型',reqd:1},{fieldname:'source_name',fieldtype:'Dynamic Link',options:'source_type',label:'来源单号',reqd:1},{fieldname:'action',fieldtype:'Select',options:Object.keys(actions),label:'下一步',reqd:1}],async v=>{const draft=await this.call('prepare_draft',{process:this.process,...v,action:actions[v.action]});const docs=frappe.model.sync(draft);frappe.set_route('Form',docs[0].doctype,docs[0].name);},'准备原生草稿（不保存、不提交）');
        }
        if(action==='chat') {
            const context=this.data.documents.filter(d=>d.docstatus!==2).map(d=>d.name).join('、');
            const dialog=new frappe.ui.Dialog({title:'采购流程讨论',size:'extra-large'});
            await frappe.require(['/assets/suke_ops/hub.js?v=supply-practice-v1.js','/assets/suke_ops/hub.css?v=supply-practice-v1.css']);
            dialog.show();
            const chat=new frappe.SupplyHub({main:dialog.$body},'chat','buying',{embedded:true,procurementProcess:this.process,procurementStage:this.stage});
            chat.host.find('textarea').val(`请核对采购流程 ${this.data.title} 的${this.stages[this.stage]}环节，相关单据：${context}。`);
        }
    }
};
