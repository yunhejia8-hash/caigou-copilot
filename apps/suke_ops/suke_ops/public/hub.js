/* 原生 Desk 对话与面板，业务单据继续使用 ERPNext 表单。 */
frappe.SupplyHub = class {
    constructor(page, view = 'chat', module = 'control', options = {}) {
        this.options = options;
        this.view = view;
        this.page = page;
        this.module = module;
        this.names = {control: '总控', buying: '采购', selling: '销售', stock: '库存', manufacturing: '生产'};
        this.stages = {'buying-demand': '物料需求单', 'buying-inquiry': '询价', 'buying-comparison': '比价', 'buying-approval': '审批', 'buying-order': '采购', 'buying-receipt': '入库'};
        const mainModules = {...this.names};
        Object.assign(this.names, this.stages);
        this.key = 'supply-hub:' + frappe.session.user + ':' + module;
        this.conversation = sessionStorage.getItem(this.key) || crypto.randomUUID();
        sessionStorage.setItem(this.key, this.conversation);
        this.turns = [];
        this.busy = false;
        this.restoring = true;
        this.host = $('<div class="supply-hub"></div>').appendTo(page.main);
        const navigation = view === 'data' || options.embedded ? {} : module === 'control' ? mainModules : module === 'buying' || this.stages[module] ? {buying: '概览', ...this.stages} : {};
        const tabs = Object.keys(navigation).length ? `<nav class="hub-tabs" aria-label="${module.startsWith('buying') ? '采购环节' : '模块工作台'}">${Object.entries(navigation).map(([key,label]) => `<a href="${this.moduleUrl(key)}" aria-current="${key === module ? 'page' : 'false'}">${key === 'control' ? '总控主页' : label}</a>`).join('')}</nav>` : '';
        this.host.addClass(view === 'chat' ? 'hub-chat-page' : 'hub-data-page');
        const inputId = 'hub-question-' + crypto.randomUUID();
        this.host.html(view === 'chat' ? `${tabs}<div class="hub-workspace"><section class="hub-chat">
            <div class="hub-chat-head"><div><b class="hub-agent-name">${this.names[module]} Agent</b><span class="hub-state"></span></div><button class="hub-new" type="button">新对话</button></div>
            <div class="hub-messages" aria-live="polite"></div><div class="hub-prompts"></div>
            <form class="hub-composer"><label for="${inputId}" class="sr-only">描述业务问题</label><textarea id="${inputId}" rows="2" maxlength="2000" placeholder="输入${this.names[module]}问题，Enter 发送，Shift+Enter 换行"></textarea><div><span>Shift+Enter 换行</span><button type="submit" class="hub-send">发送 ↑</button></div></form>
            <p class="hub-model-note" role="status"></p></section><aside class="hub-module-board"></aside></div>` :
            `<div class="hub-data-toolbar"><span class="hub-scope">${module === 'control' ? '采购 · 销售 · 库存 · 生产' : '仅显示' + this.names[module] + '相关数据'}</span><div class="hub-toolbar-actions">${module !== 'control' ? `<a class="hub-new" href="${this.moduleUrl(module)}">与${this.names[module]} Agent 讨论 →</a>` : ''}<button class="hub-refresh" type="button">刷新</button></div></div><div class="hub-summary" aria-live="polite"></div><div class="hub-data-panels"><section class="hub-panel hub-risks"></section><section class="hub-panel hub-approvals"></section></div><section class="hub-panel hub-detail"></section><div class="hub-updated"></div>`);
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
        this.host.on('click', '[data-chat-agent]', (event) => {
            this.options.selectAgent?.(event.currentTarget.dataset.chatAgent);
        });
        this.host.on('click', '[data-doctype]', (event) => {
            const {doctype, docname} = event.currentTarget.dataset;
            frappe.set_route(docname ? ['Form', doctype, docname] : ['List', doctype]);
        });
        this.host.on('click', '[data-draft]', async (event) => {
            const button = $(event.currentTarget);
            if (button.prop('disabled')) return;
            button.prop('disabled', true);
            try {
                const doc = await this.call('open_draft', {name: button.attr('data-draft'), index: button.attr('data-index')});
                const synced = frappe.model.sync(doc);
                frappe.set_route('Form', synced[0].doctype, synced[0].name);
            } catch (_) { /* frappe.call 显示服务端错误，保留重试入口。 */ }
            finally { button.prop('disabled', false); }
        });
        this.refresh();
        if (view === 'chat') this.restore();
    }
    moduleUrl(module) {
        if (this.stages[module]) return '/desk/buying/operations-' + module;
        return module === 'control' ? '/desk/主页/operations-hub' : '/desk/' + module + '/operations-' + module;
    }
    dataUrl(module) {
        return module === 'control' ? '/desk/主页/operations-data' : '/desk/' + module + '/operations-data-' + module;
    }
    agentLink(module, label, className = '') {
        return this.options.selectAgent ? `<button type="button" class="${className}" data-chat-agent="${module}">${label}</button>` : `<a class="${className}" href="${this.moduleUrl(module)}">${label}</a>`;
    }
    renderBoard() {
        const board = [...this.data.modules, ...(this.data.stages || [])].find(row => row.module === this.module);
        const reports = (this.data.reports || []).filter(row => this.module === 'control' || row.module === this.module || this.module === 'buying' && this.stages[row.module]);
        const signals = (board ? [board] : this.data.modules).flatMap(row => (row.signals || []).map(text => `<li>${this.agentLink(row.module, this.esc(row.label), 'hub-link')} · ${this.esc(text)}</li>`));
        const overview = board ? `<div class="hub-summary">${board.metrics.map(metric => `<div class="hub-metric"><span>${this.esc(metric.label)}</span><strong>${metric.value ?? '—'}<small>${this.esc(metric.unit)}</small></strong></div>`).join('')}</div>` : this.data.modules.map(row => this.agentLink(row.module, `<b>${this.esc(row.label)} Agent →</b><span>${this.esc(row.metrics[0].label)}：${row.metrics[0].value ?? '—'} ${this.esc(row.metrics[0].unit)}</span>`, 'hub-module-card')).join('');
        const stages = this.module === 'buying' && !this.options.embedded ? `<details class="hub-reports"><summary>各环节进度</summary><div class="hub-stage-overview">${(this.data.stages || []).map(row => `<a class="hub-stage-card" href="${this.moduleUrl(row.module)}"><b>${this.esc(row.label)} →</b><span>${row.metrics.map(metric => `${this.esc(metric.label)} ${metric.value ?? '—'}`).join(' · ')}</span></a>`).join('')}</div></details>` : '';
        this.host.find('.hub-module-board').html(`<div class="hub-data-toolbar"><b>${board ? board.label + '数据看板' : '模块上报与决策'}</b><button type="button" class="hub-reload">刷新</button></div>${overview}<h3>待跟进问题</h3>${signals.length ? '<ul>' + signals.join('') + '</ul>' : '<p>当前可见数据未发现规则预警。</p>'}${stages}${board ? this.renderTable(board) + (board.sections || []).map(section => this.renderSection(section)).join('') : ''}<details class="hub-reports"><summary>最新分析记录${this.module === 'control' ? '与上报' : ''}</summary>${reports.length ? reports.map(row => `<details><summary>${this.esc(this.names[row.module])} · ${this.esc(row.at)}</summary><div class="hub-markdown">${this.answer(row.answer)}</div></details>`).join('') : '<p>分析完成后，结果自动汇入总控。</p>'}</details>${this.module === 'control' ? '<button class="hub-new" type="button" data-prompt="请读取各模块最新上报和当前数据，汇总问题、影响及优先处理建议，明确需要负责人批准的事项。">汇总上报并研判</button>' : ''}<p class="hub-table-note">更新于 ${this.esc(this.data.at)} · ${this.esc(board?.note || '指标按权限统计，明细显示最近12笔。')}</p>`);
        this.host.find('.hub-reload').on('click', () => this.refresh());
        this.renderBusy();
    }
    renderSection(section) {
        return `<h3>${this.link(section.doctype, null, section.label)}</h3>${!section.access ? '<p>无读取权限</p>' : !section.rows.length ? '<p>暂无记录</p>' : `<div class="hub-table-scroll"><table><thead><tr>${section.columns.map(([,label]) => `<th>${this.esc(label)}</th>`).join('')}</tr></thead><tbody>${section.rows.map(row => `<tr>${section.columns.map(([key]) => `<td>${key === 'name' ? this.link(section.doctype,row.name) : this.esc(row[key] ?? '—')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`}`;
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
            this.data = await this.call('dashboard', {module: this.module, view: this.view});
            this.render();
        } catch (_) {
            this.host.find(this.view === 'chat' ? '.hub-model-note' : '.hub-summary').text('加载失败，请刷新重试。');
        }
    }
    async restore() {
        const conversation = this.conversation;
        try {
            const turns = await this.call('history', {conversation, module: this.module});
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
            const prompts = this.module === 'control' ? ['今天有哪些未完成订单，需要哪个模块跟进？', '请分别向采购、销售、库存和生产 Agent 获取摘要，列出需负责人处理的事项。'] : {
                buying: ['检查待收货采购订单，列出延期风险。', '查看采购申请，帮我准备采购订单草稿。'],
                selling: ['哪些销售订单尚未交付？给出跟进建议。'],
                stock: ['分别说明现存库存、已锁定和预计缺料情况。'],
                manufacturing: ['列出未完成工单，检查生产供料风险。'],
                'buying-demand': ['检查采购物料需求单，列出尚未采购的需求及优先级。'],
                'buying-inquiry': ['检查询价和已收到报价的进度，列出需要催复的问题。'],
                'buying-comparison': ['检查当前供应商报价，核对有效期、币种和物料单位，说明比价还需哪些条件。'],
                'buying-approval': ['审查待审批采购事项，列出业务依据、风险及审批建议。'],
                'buying-order': ['核对采购订单交期与收货进度，列出需要催交的订单。'],
                'buying-receipt': ['检查待到货订单、收货记录及入库质检，指出不能放行的问题。']
            }[this.module];
            const labels = this.module === 'control' ? ['待跟进订单', '跨模块摘要'] : this.module === 'buying' ? ['采购延期风险', '准备采购草稿'] : [this.names[this.module] + '待办'];
            this.host.find('.hub-prompts').html(prompts.map((prompt, index) => `<button type="button" data-prompt="${this.esc(prompt)}">${labels[index]}</button>`).join(''));
            this.renderMessages();
            this.renderBusy();
            this.renderBoard();
            return;
        }
        const selected = this.data.modules.find(module => module.module === this.module);
        const cards = selected ? selected.metrics.map(metric => ({...metric, module: selected.module})) : this.data.modules.map(module => ({...module.metrics[0], module: module.module, prefix: module.label}));
        this.host.find('.hub-summary').html(cards.map(card => `${selected ? '<div class="hub-metric">' : `<a class="hub-metric" href="${this.dataUrl(card.module)}">`}<span>${this.esc(card.prefix ? card.prefix + ' · ' + card.label : card.label)}</span><strong>${card.value === null ? '—' : this.esc(card.value)}<small>${this.esc(card.unit)}</small></strong>${!selected ? '<em>查看模块数据 →</em>' : card.value === null ? '<em>当前账号无读取权限</em>' : ''}${selected ? '</div>' : '</a>'}`).join(''));
        const signals = this.data.modules.flatMap(board => (board.signals || []).map(text => `<li>${selected ? '' : this.esc(board.label) + ' · '}${this.esc(text)}</li>`));
        this.host.find('.hub-risks').html(`<h3>${selected ? this.esc(selected.label) + '待跟进' : '待关注事项'}</h3>${signals.length ? '<ul class="hub-signals">' + signals.join('') + '</ul>' : '<p>当前可见数据未发现规则预警。</p>'}${this.data.cases.length ? `<details class="hub-case-list"><summary>供需异常案例 · ${this.data.cases.length}</summary>${this.data.cases.map(row => `<div class="hub-risk">${this.link('Supply Case', row.name, row.title)}</div>`).join('')}</details>` : ''}`);
        const approvals = [...this.data.approvals.map(row => ({...row, type: 'Operations Approval'})), ...this.data.decisions.map(row => ({...row, type: 'Supply Decision'}))];
        this.host.find('.hub-approvals').html(`<h3>${selected ? this.esc(selected.label) : ''}待审批与已驳回</h3>${approvals.length ? approvals.map(row => `<div class="hub-risk">${this.link(row.type, row.name)}<small>${this.esc(row.source_name || '')} ${this.esc(row.workflow_state)}</small></div>`).join('') : '<p>当前没有可见的待处理审批。</p>'}`);
        this.host.find('.hub-detail').html((selected ? [selected] : this.data.modules).map(board => selected ? `<section class="hub-data-section">${this.renderTable(board)}${(board.sections || []).map(section => this.renderSection(section)).join('')}</section>` : `<details class="hub-data-section"><summary>${this.esc(board.label)}明细 <span>${this.esc(board.metrics[0].label)} · ${board.metrics[0].value ?? '—'} ${this.esc(board.metrics[0].unit)}</span></summary>${this.renderTable(board)}<a class="hub-detail-link" href="${this.dataUrl(board.module)}">查看${this.esc(board.label)}完整数据 →</a></details>`).join(''));
        this.host.find('.hub-updated').text('更新于 ' + this.data.at + ' · 指标按当前账号权限统计，表格最多显示最近 12 笔。不同物料的数量不汇总相加。');
    }
    renderTable(board) {
        if (board.columns) return this.renderSection({...board, label: '当前进度与明细'});
        const stock = board.module === 'stock';
        const columns = stock ? [['item_code', '物料'], ['warehouse', '仓库'], ['actual_qty', '现存量'], ['reserved_stock', '实际预留'], ['projected_qty', '预计数量']] :
            board.module === 'manufacturing' ? [['name', '工单'], ['production_item', '产品'], ['qty', '计划数量'], ['produced_qty', '已完工'], ['planned_start_date', '计划开工'], ['status', '状态']] :
            [['name', '订单'], [board.module === 'buying' ? 'supplier' : 'customer', board.module === 'buying' ? '供应商' : '客户'], [board.module === 'buying' ? 'schedule_date' : 'delivery_date', '交期'], [board.module === 'buying' ? 'per_received' : 'per_delivered', '完成 %'], ['status', '状态']];
        return `<div class="hub-section-label">${this.esc(board.label)}数据面板</div><h3>${stock ? '现存库存明细' : '未完成业务单据'}</h3>${!board.access ? '<p>当前账号没有读取权限。</p>' : !board.rows.length ? '<p>当前没有符合条件的数据。</p>' : `<div class="hub-table-scroll"><table><thead><tr>${columns.map(([, label]) => `<th>${label}</th>`).join('')}</tr></thead><tbody>${board.rows.map(row => `<tr>${columns.map(([key]) => `<td>${key === 'name' ? this.link(board.doctype, row.name) : this.esc(row[key] ?? '—')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`}${stock ? '<p class="hub-table-note">现存量不等于可分配量；预计数量未区分交期，不能据此判断按时交付。请进入异常方案核对交期、冻结和预留归属。</p>' : ''}`;
    }
    renderMessages() {
        const messages = this.host.find('.hub-messages');
        messages.html(this.turns.length ? this.turns.map(turn => `<article class="hub-message hub-user"><small>你</small><div>${this.esc(turn.question)}</div></article><article class="hub-message hub-assistant"><small>${this.esc(this.names[turn.agent_module] || '总控')} Agent</small><div class="hub-markdown">${this.answer(turn.answer || (turn.state === 'failed' ? '分析未完成，请重试。' : '正在读取业务数据并协调相关模块…'))}</div>${(turn.offers || []).map((offer, index) => `<button type="button" class="hub-draft" data-draft="${this.esc(turn.name)}" data-index="${index}">${this.esc(offer.label)} ↗</button>`).join('')}${turn.offers?.length ? '<p class="hub-table-note">将打开未保存的原生表单，请核对数量、仓库和必填项后保存。</p>' : ''}</article>`).join('') : `<div class="hub-empty"><h2>${this.module === 'control' ? '查看模块上报，协调下一步' : this.names[this.module] + '有哪些问题需要处理？'}</h2></div>`);
        messages.scrollTop(messages[0].scrollHeight);
    }
    renderBusy() {
        this.host.find('.hub-send').prop('disabled', this.restoring || this.busy || !this.data?.model_ready).text(this.busy ? '分析中…' : '发送 ↑');
        this.host.find('.hub-new').prop('disabled', this.restoring || this.busy);
        this.host.find('[data-prompt]').prop('disabled', this.restoring || this.busy || !this.data?.model_ready);
    }
    async send(prompt) {
        const draft = this.host.find('textarea').val();
        const question = (typeof prompt === 'string' ? prompt : draft).trim();
        const module = this.module;
        if (!question || this.restoring || this.busy || !this.data?.model_ready) return;
        this.busy = true;
        this.renderBusy();
        try {
            const response = await this.call('start', {question, module, conversation: this.conversation, request_id: crypto.randomUUID()});
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
                if (['done', 'failed'].includes(turn.state)) break;
                await new Promise(resolve => setTimeout(resolve, 2200));
            }
            if (!['done', 'failed'].includes(turn.state)) turn.answer = '分析仍未结束，可稍后刷新页面查看。';
        } catch (_) { turn.answer = '状态读取失败，刷新页面可恢复对话。'; }
        finally { this.busy = false; this.renderMessages(); this.renderBusy(); this.refresh(); }
    }
};


// 主页各对话使用独立实例；后台分析、草稿和历史不会因切换对象而串到另一会话。
frappe.SupplyHome = class {
    constructor(page) {
        this.page = page;
        this.names = {control: '总控', buying: '采购', selling: '销售', stock: '库存', manufacturing: '生产'};
        this.agents = new Map();
        this.panes = new Map();
        this.host = $('<div class="hub-home"></div>').appendTo(page.main);
        this.host.html(`<nav class="hub-tabs hub-home-tabs" aria-label="选择对话对象">${Object.entries(this.names).map(([key,label]) => `<button type="button" data-chat-agent="${key}" aria-pressed="false">${label}</button>`).join('')}</nav><div class="hub-home-content"></div>`);
        this.host.find('.hub-home-tabs').on('click', '[data-chat-agent]', event => this.selectAgent(event.currentTarget.dataset.chatAgent));
        this.selectAgent('control');
    }
    selectAgent(module) {
        if (!Object.hasOwn(this.names, module)) return;
        if (!this.agents.has(module)) {
            const pane = $(`<div class="hub-home-pane" data-home-agent="${module}"></div>`).appendTo(this.host.find('.hub-home-content'));
            this.panes.set(module, pane);
            this.agents.set(module, new frappe.SupplyHub({main:pane}, 'chat', module, {embedded:true, selectAgent:key => this.selectAgent(key)}));
        }
        for (const [key,pane] of this.panes) pane.prop('hidden', key !== module);
        this.module = module;
        this.host.find('.hub-home-tabs [data-chat-agent]').each((_, element) => element.setAttribute('aria-pressed', element.dataset.chatAgent === module));
        this.agents.get(module).refresh();
    }
    refresh() { this.agents.get(this.module)?.refresh(); }
};
