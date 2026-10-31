/* 原生 Desk 对话与面板，业务单据继续使用 ERPNext 表单。 */
frappe.SupplyHub = class {
    constructor(page, view = 'chat', module = 'control') {
        const options = typeof view === 'object' ? view : {};
        if (typeof view === 'object') view = 'chat';
        this.caseName = options.caseName || null;
        this.orderName = options.orderName || null;
        this.onNavigate = options.onNavigate || (() => {});
        if (this.caseName || this.orderName) module = 'buying';
        const inputId = 'hub-question-' + crypto.randomUUID();
        this.view = view;
        this.page = page;
        this.module = module;
        this.names = {control: '总控', buying: '采购', selling: '销售', stock: '库存', manufacturing: '生产'};
        this.key = 'supply-hub:' + frappe.session.user + ':' + module + (this.caseName ? ':case:' + encodeURIComponent(this.caseName) : this.orderName ? ':order:' + encodeURIComponent(this.orderName) : '');
        this.conversation = sessionStorage.getItem(this.key) || crypto.randomUUID();
        sessionStorage.setItem(this.key, this.conversation);
        this.turns = [];
        this.busy = false;
        this.restoring = true;
        this.host = $('<div class="supply-hub"></div>').appendTo(page.main);
        const tabs = `<nav class="hub-tabs" aria-label="模块工作台">${Object.entries(this.names).map(([key,label]) => `<a href="${this.moduleUrl(key)}" aria-current="${key === module ? 'page' : 'false'}">${key === 'control' ? '总控主页' : label + ' Agent'}</a>`).join('')}</nav>`;
        this.host.addClass(view === 'chat' ? 'hub-chat-page' : 'hub-data-page');
        this.host.html(view === 'chat' ? `${tabs}<div class="hub-workspace"><section class="hub-chat">
            <div class="hub-chat-head"><div><b class="hub-agent-name">总控 Agent</b><span class="hub-state"></span></div><button class="hub-new" type="button">新对话</button></div>
            <div class="hub-messages" aria-live="polite"></div><div class="hub-prompts"></div>
            <form class="hub-composer"><label for="${inputId}" class="sr-only">描述业务问题</label><textarea id="${inputId}" rows="2" maxlength="2000" placeholder="输入${this.names[module]}问题，Enter 发送，Shift+Enter 换行"></textarea><div><span>Shift+Enter 换行</span><button type="submit" class="hub-send">发送 ↑</button></div></form>
            <p class="hub-model-note" role="status"></p></section><aside class="hub-module-board"></aside></div>` :
            `<div class="hub-data-toolbar"><span>决策数据</span><button class="hub-refresh" type="button">刷新</button></div>${tabs}<div class="hub-summary" aria-live="polite"></div><div class="hub-data-panels"><section class="hub-panel hub-risks"></section><section class="hub-panel hub-approvals"></section></div><section class="hub-panel hub-detail"></section><div class="hub-updated"></div>`);
        this.host.find('.hub-refresh').on('click', () => this.refresh());
        this.host.find('.hub-new').on('click', () => {
            if (this.busy || this.restoring) return frappe.msgprint('请等待当前对话加载或分析完成。');
            this.conversation = crypto.randomUUID();
            sessionStorage.setItem(this.key, this.conversation);
            this.turns = [];
            this.renderMessages();
        });
        this.host.find('form').on('submit', (event) => { event.preventDefault(); this.send(); });
        this.host.find('textarea').on('keydown', (event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && !event.originalEvent?.isComposing && event.keyCode !== 229) { event.preventDefault(); this.send(); }
        });
        this.host.on('click', '[data-prompt]', (event) => {
            this.send(event.currentTarget.dataset.prompt);
        });
        this.host.on('click', '[data-live-discussion]', () => {
            if (this.busy || this.restoring || !this.data?.model_ready) return;
            frappe.prompt([
                {fieldname: 'scenario', fieldtype: 'Select', label: '合成场景', options: ['0 延期争料', '1 替代料未批准', '2 客户批次限制', '3 短收与质检冻结', '4 减单超采', '5 重复需求'], default: '0 延期争料', reqd: 1},
                {fieldname: 'updated', fieldtype: 'Check', label: '首例加入已确认提前批与备用供方报价', default: 0},
                {fieldname: 'question', fieldtype: 'Small Text', label: '讨论目标（只发送合成场景问题）', default: '请各部门核实缺口、相互复核，再由总控提出方案、费用依据和待审批事项。', reqd: 1}
            ], values => {
                const id = Number(values.scenario.split(' ')[0]);
                if (values.updated && id !== 0) return frappe.msgprint('只有首例支持补充证据。');
                this.send(values.question, {scenario_id: id, scenario_updated: Boolean(values.updated)});
            }, '真实模型讨论 · 合成证据 · 不执行', '开始两轮讨论');
        });
        this.host.on('click', '[data-doctype]', (event) => {
            const {doctype, docname} = event.currentTarget.dataset;
            this.onNavigate();
            frappe.set_route(docname ? ['Form', doctype, docname] : ['List', doctype]);
        });
        this.host.on('click', '[data-draft]', async (event) => {
            const button = $(event.currentTarget);
            if (button.prop('disabled')) return;
            button.prop('disabled', true);
            try {
                const doc = await this.call('open_draft', {name: button.attr('data-draft'), index: button.attr('data-index')});
                const synced = frappe.model.sync(doc);
                this.onNavigate();
                frappe.set_route('Form', synced[0].doctype, synced[0].name);
            } catch (_) { /* frappe.call 显示服务端错误，保留重试入口。 */ }
            finally { button.prop('disabled', false); }
        });
        if (this.caseName || this.orderName) {
            this.host.addClass('hub-case-chat');
            this.host.find('textarea').attr('maxlength', Math.max(1, 2000 - this.contextPrefix().length));
        }
        this.refresh();
        if (view === 'chat') this.restore();
    }
    moduleUrl(module) {
        return module === 'control' ? '/desk/buying/operations-hub' : '/desk/' + module + '/operations-' + module;
    }
    renderBoard() {
        const board = this.data.modules.find(row => row.module === this.module);
        const reports = (this.data.reports || []).filter(row => this.module === 'control' || row.module === this.module);
        const signals = (board ? [board] : this.data.modules).flatMap(row => (row.signals || []).map(text => `<li><a href="${this.moduleUrl(row.module)}">${this.esc(row.label)}</a> · ${this.esc(text)}</li>`));
        const content = board ? `<div class="hub-summary">${board.metrics.map(metric => `<div class="hub-metric"><span>${this.esc(metric.label)}</span><strong>${metric.value ?? '—'}<small>${this.esc(metric.unit)}</small></strong></div>`).join('')}</div>${this.renderTable(board)}${(board.sections || []).map(section => this.renderSection(section)).join('')}` : this.data.modules.map(row => `<a class="hub-module-card" href="${this.moduleUrl(row.module)}"><b>${this.esc(row.label)} Agent →</b><span>${this.esc(row.metrics[0].label)}：${row.metrics[0].value ?? '—'} ${this.esc(row.metrics[0].unit)}</span></a>`).join('');
        this.host.find('.hub-module-board').html(`<div class="hub-data-toolbar"><b>${board ? board.label + '数据看板' : '模块上报与决策'}</b><button type="button" class="hub-reload">刷新</button></div>${content}${['control', 'buying'].includes(this.module) ? '<a class="hub-module-card" href="/assets/suke_ops/scenario-demo/index.html" target="_blank" rel="noopener"><b>采购异常协同演示 →</b><span>六类合成场景 · 预设部门反馈与成本试算</span></a>' : ''}<h3>待跟进问题</h3>${signals.length ? '<ul>' + signals.join('') + '</ul>' : '<p>当前可见数据未发现规则预警。</p>'}<h3>Agent 最新汇报</h3>${reports.length ? reports.map(row => `<details><summary>${this.esc(this.names[row.module])} · ${this.esc(row.at)}</summary><div class="hub-markdown">${this.answer(row.answer)}</div></details>`).join('') : '<p>模块完成分析后，结果自动汇入总控。</p>'}${board ? `<a class="hub-module-card" href="${this.moduleUrl('control')}">前往总控研判 →</a>` : '<button type="button" data-prompt="请读取各模块最新上报和当前数据，汇总问题、影响及优先处理建议，明确需要负责人批准的事项。">汇总上报并研判</button>'}`);
        this.host.find('.hub-reload').on('click', () => this.refresh());
        this.renderBusy();
    }
    renderSection(section) {
        return `<h3>${this.link(section.doctype, null, section.label)}</h3>${!section.access ? '<p>无读取权限</p>' : !section.rows.length ? '<p>暂无记录</p>' : `<div class="hub-table-scroll"><table><thead><tr>${section.columns.map(([,label]) => `<th>${this.esc(label)}</th>`).join('')}</tr></thead><tbody>${section.rows.map(row => `<tr>${section.columns.map(([key]) => `<td>${key === 'name' ? this.link(section.doctype,row.name) : this.esc(row[key] ?? '—')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`}`;
    }
    contextPrefix() {
        if (this.orderName) return `当前采购订单号：${JSON.stringify(this.orderName)}。请先读取该订单最新明细及关联收货，核对交期、未收数量、供应确认和关联需求；现存不等于可用，不能把未确认到货当作库存。说明信息缺口与费用依据，不把预计收益当成实际节省。只提供建议或未保存草稿，审批与执行由负责人确认。\n采购员问题：`;
        return this.caseName ? `当前供应异常单号：${JSON.stringify(this.caseName)}。请先读取该单最新数据，保持已有预留，不将未确认供应计入可用；涉及方案选择、审批与执行时引导用户回原异常页面确认。\n采购员问题：` : '';
    }
    esc(value) { return frappe.utils.escape_html(String(value ?? '')); }
    answer(value) {
        // 原始HTML先转义，再使用Frappe自带净化器；回答不加载外链图片。
        return frappe.markdown(this.esc(value)).replace(/<img\b[^>]*>/gi, '');
    }
    async call(method, args = {}) {
        const result = await frappe.call({method: 'suke_ops.hub.' + method, args});
        return result.message;
    }
    link(doctype, name, label) {
        return `<button type="button" class="hub-link" data-doctype="${this.esc(doctype)}" ${name ? `data-docname="${this.esc(name)}"` : ''}>${this.esc(label || name || doctype)} <span>↗</span></button>`;
    }
    async refresh() {
        try {
            this.data = await this.call('dashboard');
            this.render();
        } catch (_) {
            this.host.find(this.view === 'chat' ? '.hub-model-note' : '.hub-summary').text('加载失败，请刷新重试。');
        }
    }
    async restore() {
        const conversation = this.conversation;
        try {
            const turns = await this.call('history', {conversation});
            if (conversation !== this.conversation) return;
            this.turns = turns;
            for (const turn of this.turns) {
                if (turn.state === 'done') Object.assign(turn, await this.call('poll', {name: turn.name}));
            }
            this.renderMessages();
            const active = this.turns.find(turn => ['queued', 'running'].includes(turn.state));
            if (active) { this.busy = true; this.renderBusy(); this.watch(active); }
        } catch (_) { this.host.find('.hub-model-note').text('历史对话读取失败，可新建对话后继续。'); }
        finally { this.restoring = false; this.renderBusy(); }
    }
    render() {
        if (!this.data) return;
        this.host.find('[data-module]').each((_, element) => element.setAttribute('aria-pressed', element.dataset.module === this.module));
        this.host.find('.hub-agent-name').text(this.names[this.module] + ' Agent');
        this.host.find('.hub-state').text(this.data.model_ready ? '已连接模型' : '模型未配置');
        if (this.view === 'chat') {
            this.host.find('.hub-model-note').text(this.data.model_ready ? '' : '模型尚未配置，请联系管理员。');
            const prompts = this.orderName ? ['检查当前订单到货进度和延期风险。', '请核对关联需求和库存，比较延期处置方案并说明未知条件。', '哪些增量费用已确认，还缺哪些降本增效证据？'] : this.caseName ? ['现在缺多少料？哪些需求受到影响？', '比较销售优先与生产优先，说明各自影响。', '有哪些到货条件还需要向供应商确认？'] : this.module === 'control' ? ['今天有哪些未完成订单，需要哪个模块跟进？', '请分别向采购、销售、库存和生产 Agent 获取摘要，列出需负责人处理的事项。'] : {
                buying: ['检查待收货采购订单，列出延期风险。', '查看采购申请，帮我准备采购订单草稿。'],
                selling: ['哪些销售订单尚未交付？给出跟进建议。'],
                stock: ['分别说明现存库存、已锁定和预计缺料情况。'],
                manufacturing: ['列出未完成工单，检查生产供料风险。']
            }[this.module];
            const labels = this.orderName ? ['到货跟踪', '延期处置', '费用与收益依据'] : this.caseName ? ['核对缺料', '比较方案', '确认到货条件'] : this.module === 'control' ? ['待跟进订单', '跨模块摘要'] : this.module === 'buying' ? ['采购延期风险', '准备采购草稿'] : [this.names[this.module] + '待办'];
            this.host.find('.hub-prompts').html(prompts.map((prompt, index) => `<button type="button" data-prompt="${this.esc(prompt)}">${labels[index]}</button>`).join(''));
            if (this.module === 'control') this.host.find('.hub-prompts').append('<button type="button" data-live-discussion>真实多 Agent 场景讨论</button>');
            this.renderMessages();
            this.renderBusy();
            this.renderBoard();
            return;
        }
        const selected = this.data.modules.find(module => module.module === this.module);
        const cards = selected ? selected.metrics.map(metric => ({...metric, module: selected.module})) : this.data.modules.map(module => ({...module.metrics[0], module: module.module, prefix: module.label}));
        this.host.find('.hub-summary').html(cards.map(card => `<a class="hub-metric" href="${this.moduleUrl(card.module)}"><span>${this.esc(card.prefix ? card.prefix + ' · ' + card.label : card.label)}</span><strong>${card.value === null ? '—' : this.esc(card.value)}<small>${this.esc(card.unit)}</small></strong><em>${card.value === null ? '当前账号无读取权限' : '查看模块数据 →'}</em></a>`).join(''));
        this.host.find('.hub-risks').html(`<div class="hub-section-label">异常协同</div><h3>待关注的供需问题</h3>${this.data.cases.length ? this.data.cases.map(row => `<div class="hub-risk">${this.link('Supply Case', row.name, row.title)}<small>查看确定性方案 · 模拟供应商议价</small></div>`).join('') : '<p>当前没有可见的异常案例。</p>'}`);
        const approvals = [...this.data.approvals.map(row => ({...row, type: 'Operations Approval'})), ...this.data.decisions.map(row => ({...row, type: 'Supply Decision'}))];
        this.host.find('.hub-approvals').html(`<div class="hub-section-label">人工决策</div><h3>待审批与已驳回</h3>${approvals.length ? approvals.map(row => `<div class="hub-risk">${this.link(row.type, row.name)}<small>${this.esc(row.workflow_state)}</small></div>`).join('') : '<p>当前没有可见的待处理审批。</p>'}`);
        this.host.find('.hub-detail').html((selected ? [selected] : this.data.modules).map(board => this.renderTable(board)).join(''));
        this.host.find('.hub-updated').text('更新于 ' + this.data.at + ' · 指标按当前账号权限统计，表格最多显示最近 12 笔。不同物料的数量不汇总相加。');
    }
    renderTable(board) {
        const stock = board.module === 'stock';
        const columns = stock ? [['item_code', '物料'], ['warehouse', '仓库'], ['actual_qty', '现存量'], ['reserved_stock', '实际预留'], ['projected_qty', '预计数量']] :
            board.module === 'manufacturing' ? [['name', '工单'], ['production_item', '产品'], ['qty', '计划数量'], ['produced_qty', '已完工'], ['planned_start_date', '计划开工'], ['status', '状态']] :
            [['name', '订单'], [board.module === 'buying' ? 'supplier' : 'customer', board.module === 'buying' ? '供应商' : '客户'], [board.module === 'buying' ? 'schedule_date' : 'delivery_date', '交期'], [board.module === 'buying' ? 'per_received' : 'per_delivered', '完成 %'], ['status', '状态']];
        return `<div class="hub-section-label">${this.esc(board.label)}数据面板</div><h3>${stock ? '现存库存明细' : '未完成业务单据'}</h3>${!board.access ? '<p>当前账号没有读取权限。</p>' : !board.rows.length ? '<p>当前没有符合条件的数据。</p>' : `<div class="hub-table-scroll"><table><thead><tr>${columns.map(([, label]) => `<th>${label}</th>`).join('')}</tr></thead><tbody>${board.rows.map(row => `<tr>${columns.map(([key]) => `<td>${key === 'name' ? this.link(board.doctype, row.name) : this.esc(row[key] ?? '—')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`}${stock ? '<p class="hub-table-note">现存量不等于可分配量；预计数量未区分交期，不能据此判断按时交付。请进入异常方案核对交期、冻结和预留归属。</p>' : ''}`;
    }
    renderMessages() {
        const messages = this.host.find('.hub-messages');
        messages.html(this.turns.length ? this.turns.map(turn => `<article class="hub-message hub-user"><small>你</small><div>${this.esc(turn.question)}</div></article><article class="hub-message hub-assistant"><small>${this.esc(this.names[turn.agent_module] || '总控')} Agent</small><div class="hub-markdown">${this.answer(turn.answer || (turn.state === 'failed' ? '分析未完成，请重试。' : '正在读取业务数据并协调相关模块…'))}</div>${turn.verified_evidence?.verified_plan?.allocation ? `<div class="hub-table-note"><b>程序核算（优先于模型解释）</b><p>${this.esc(turn.verified_evidence.verified_plan.allocation)}</p><p>${this.esc(turn.verified_evidence.verified_plan.constraints)}</p><p>增量费用：${this.esc(turn.verified_evidence.calculation.expedite_incremental_cost_if_approved)}元；实际节省未验证。</p></div>` : ''}${(turn.discussion || []).map(row => `<details open><summary>${this.esc(this.names[row.module])} · ${this.esc(row.phase)} · ${row.status === 'done' ? '已反馈' : '未核实'}</summary><div class="hub-markdown">${this.answer(row.answer)}</div></details>`).join('')}${turn.synthetic ? '<p class="hub-table-note">真实模型讨论 / 合成证据 / 未执行业务操作</p>' : ''}${(turn.offers || []).map((offer, index) => `<button type="button" class="hub-draft" data-draft="${this.esc(turn.name)}" data-index="${index}">${this.esc(offer.label)} ↗</button>`).join('')}${turn.offers?.length ? '<p class="hub-table-note">将打开未保存的原生表单，请核对数量、仓库和必填项后保存。</p>' : ''}</article>`).join('') : `<div class="hub-empty"><h2>${this.module === 'control' ? '查看模块上报，协调下一步' : this.names[this.module] + '有哪些问题需要处理？'}</h2></div>`);
        messages.scrollTop(messages[0].scrollHeight);
    }
    renderBusy() {
        this.host.find('.hub-send').prop('disabled', this.restoring || this.busy || !this.data?.model_ready).text(this.busy ? '分析中…' : '发送 ↑');
        this.host.find('.hub-new').prop('disabled', this.restoring || this.busy);
        this.host.find('[data-prompt], [data-live-discussion]').prop('disabled', this.restoring || this.busy || !this.data?.model_ready);
    }
    async send(prompt, scenario = {}) {
        const draft = this.host.find('textarea').val();
        const input = (typeof prompt === 'string' ? prompt : draft).trim();
        const question = this.contextPrefix() + input;
        const module = this.module;
        if (!input || this.restoring || this.busy || !this.data?.model_ready) return;
        if (question.length > 2000) return frappe.msgprint('问题过长，请缩短后重试。');
        this.busy = true;
        this.renderBusy();
        try {
            const response = await this.call('start', {question, module, conversation: this.conversation, request_id: crypto.randomUUID(), ...scenario});
            const turn = {...response, question, agent_module: module, state: 'queued'};
            this.turns.push(turn);
            if (typeof prompt !== 'string' && this.host.find('textarea').val() === draft) this.host.find('textarea').val('');
            this.renderMessages();
            await this.watch(turn);
        } catch (_) { this.busy = false; this.renderBusy(); }
    }
    async watch(turn) {
        try {
            const deadline = Date.now() + 960000;
            while (Date.now() < deadline) {
                const result = await this.call('poll', {name: turn.name});
                Object.assign(turn, result);
                this.renderMessages();
                if (['done', 'failed'].includes(turn.state)) break;
                await new Promise(resolve => setTimeout(resolve, 2200));
            }
            if (!['done', 'failed'].includes(turn.state)) turn.answer = '分析仍未结束，可稍后刷新页面查看。';
        } catch (_) { turn.answer = '状态读取失败，刷新页面可恢复对话。'; }
        finally { this.busy = false; this.renderMessages(); this.renderBusy(); this.refresh(); }
    }
};
