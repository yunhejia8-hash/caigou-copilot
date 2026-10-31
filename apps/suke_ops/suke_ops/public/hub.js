/* 原生 Desk 对话与面板，业务单据继续使用 ERPNext 表单。 */
frappe.SupplyHub = class {
    constructor(page) {
        this.page = page;
        this.module = 'control';
        this.names = {control: '总控', buying: '采购', selling: '销售', stock: '库存', manufacturing: '生产'};
        this.key = 'supply-hub:' + frappe.session.user;
        this.conversation = sessionStorage.getItem(this.key) || crypto.randomUUID();
        sessionStorage.setItem(this.key, this.conversation);
        this.turns = [];
        this.busy = false;
        this.restoring = true;
        this.host = $('<div class="supply-hub"></div>').appendTo(page.main);
        this.host.html(`
            <header class="hub-heading"><div><div class="hub-eyebrow">SUPPLY OPERATIONS</div><h1>把业务串起来，把决策留给人。</h1><p>采购 · 销售 · 库存 · 生产，在同一份业务数据上协作。</p></div><button class="hub-refresh" type="button">↻ 刷新数据</button></header>
            <nav class="hub-tabs" aria-label="选择业务 Agent">${Object.entries(this.names).map(([key, label]) => `<button type="button" data-module="${key}" aria-pressed="${key === 'control'}">${label}${key === 'control' ? '对话' : ' Agent'}</button>`).join('')}</nav>
            <div class="hub-summary" aria-live="polite">正在读取业务数据…</div>
            <div class="hub-layout"><section class="hub-chat">
                <div class="hub-chat-head"><div><span class="hub-avatar">协</span><b class="hub-agent-name">总控 Agent</b><span class="hub-state">连接中</span></div><button class="hub-new" type="button">新对话</button></div>
                <div class="hub-messages" aria-live="polite"></div>
                <div class="hub-prompts"></div>
                <form class="hub-composer"><label for="hub-question" class="sr-only">描述你的业务问题</label><textarea id="hub-question" rows="3" maxlength="2000" placeholder="例如：连接器延期了，先保重点客户还是明天的生产？"></textarea><div><span>Agent 查询与准备草稿 · 人员选择与审批</span><button type="submit" class="hub-send">发送 ↑</button></div></form>
                <p class="hub-model-note"></p>
            </section><aside class="hub-aside"><section class="hub-panel hub-actions"></section><section class="hub-panel hub-risks"></section><section class="hub-panel hub-approvals"></section></aside></div>
            <section class="hub-panel hub-detail"></section><div class="hub-updated"></div>`);
        this.host.on('click', '[data-module]', (event) => {
            this.module = event.currentTarget.dataset.module;
            this.render();
        });
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
            if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); this.send(); }
        });
        this.host.on('click', '[data-prompt]', (event) => {
            this.host.find('textarea').val(event.currentTarget.dataset.prompt).trigger('focus');
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
        this.restore();
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
            this.host.find('.hub-summary').text('业务数据加载失败，请刷新重试。');
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
        this.host.find('.hub-model-note').text(this.data.model_ready ? '对话使用当前账号权限内的业务摘要。模型建议须结合原始单据核对。' : '尚未配置模型服务。业务面板、异常规则方案和手动操作可以继续使用。');
        const selected = this.data.modules.find(module => module.module === this.module);
        const cards = selected ? selected.metrics.map(metric => ({...metric, module: selected.module})) : this.data.modules.map(module => ({...module.metrics[0], module: module.module, prefix: module.label}));
        this.host.find('.hub-summary').html(cards.map(card => `<button type="button" class="hub-metric" data-module="${card.module}"><span>${this.esc(card.prefix ? card.prefix + ' · ' + card.label : card.label)}</span><strong>${card.value === null ? '—' : this.esc(card.value)}<small>${this.esc(card.unit)}</small></strong><em>${card.value === null ? '当前账号无读取权限' : '查看模块数据 →'}</em></button>`).join(''));
        const prompts = this.module === 'control' ? ['今天有哪些未完成订单，需要哪个模块跟进？', '请分别向采购、销售、库存和生产 Agent 获取摘要，列出需负责人处理的事项。'] : {
            buying: ['检查待收货采购订单，列出延期风险。', '查看采购申请，帮我准备采购订单草稿。'],
            selling: ['哪些销售订单尚未交付？给出跟进建议。', '查看销售订单，帮我准备发货草稿。'],
            stock: ['分别说明现存库存、已锁定和预计缺料情况。', '查看待到货采购单，帮我准备收货草稿。'],
            manufacturing: ['列出未完成工单，检查生产供料风险。', '查看工单，帮我准备完工入库草稿。']
        }[this.module];
        const firstCase = this.data.cases.find(row => (row.title || '').includes('负责人决策现场')) || this.data.cases[0];
        if (firstCase) prompts.push(`分析异常 ${firstCase.name}，比较销售优先与生产优先，交给负责人选择。`);
        this.host.find('.hub-prompts').html(prompts.map(prompt => `<button type="button" data-prompt="${this.esc(prompt)}">${this.esc(prompt)}</button>`).join(''));
        const modules = selected ? [selected] : this.data.modules;
        this.host.find('.hub-actions').html(`<div class="hub-section-label">手动业务入口</div><h3>熟悉的操作，都在这里</h3>${modules.map(module => `<div class="hub-entry-group"><small>${this.esc(module.label)}</small><div>${module.links.slice(0, selected ? 20 : 3).map(link => this.link(link.doctype, null, link.label)).join('')}</div></div>`).join('')}`);
        this.host.find('.hub-risks').html(`<div class="hub-section-label">异常协同</div><h3>待关注的供需问题</h3>${this.data.cases.length ? this.data.cases.map(row => `<div class="hub-risk">${this.link('Supply Case', row.name, row.title)}<small>查看确定性方案 · 模拟供应商议价</small></div>`).join('') : '<p>当前没有可见的异常案例。</p>'}`);
        const approvals = [...this.data.approvals.map(row => ({...row, type: 'Operations Approval'})), ...this.data.decisions.map(row => ({...row, type: 'Supply Decision'}))];
        this.host.find('.hub-approvals').html(`<div class="hub-section-label">人工决策</div><h3>待审批与已驳回</h3>${approvals.length ? approvals.map(row => `<div class="hub-risk">${this.link(row.type, row.name)}<small>${this.esc(row.workflow_state)}</small></div>`).join('') : '<p>当前没有可见的待处理审批。</p>'}`);
        this.host.find('.hub-detail').html(selected ? this.renderTable(selected) : `<div class="hub-section-label">协同方式</div><div class="hub-flow"><span>销售提出交付需求</span><b>→</b><span>库存核对可用与锁定</span><b>→</b><span>采购补货 / 生产履约</span><b>→</b><span>负责人审批与执行</span></div>`);
        this.host.find('.hub-updated').text('更新于 ' + this.data.at + ' · 指标按当前账号权限统计，表格最多显示最近 12 笔。不同物料的数量不汇总相加。');
        this.renderMessages();
        this.renderBusy();
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
        messages.html(this.turns.length ? this.turns.map(turn => `<article class="hub-message hub-user"><small>你</small><div>${this.esc(turn.question)}</div></article><article class="hub-message hub-assistant"><small>${this.esc(this.names[turn.agent_module] || '总控')} Agent</small><div class="hub-markdown">${this.answer(turn.answer || (turn.state === 'failed' ? '分析未完成，请重试。' : '正在读取业务数据并协调相关模块…'))}</div>${(turn.offers || []).map((offer, index) => `<button type="button" class="hub-draft" data-draft="${this.esc(turn.name)}" data-index="${index}">${this.esc(offer.label)} ↗</button>`).join('')}${turn.offers?.length ? '<p class="hub-table-note">将打开未保存的原生表单，请核对数量、仓库和必填项后保存。</p>' : ''}${turn.trace?.length ? `<details><summary>查看协作记录 · ${turn.trace.length} 次工具调用</summary>${turn.trace.map(row => `<p>${this.esc(this.names[row.agent])} → ${this.esc({ask_module: '委派模块分析', read_module_board: '读取业务面板', list_records: '查询单据', read_record: '核对单据', compare_supply_case: '比较供需方案', offer_draft: '准备草稿入口'}[row.tool] || row.tool)}</p>`).join('')}</details>` : ''}</article>`).join('') : `<div class="hub-empty"><div class="hub-empty-mark">协</div><h2>今天，先处理哪件事？</h2><p>说出目标或单号，${this.module === 'control' ? '总控会协调相关模块，整理事实与可选方案。' : this.names[this.module] + ' Agent 会读取相关业务并协助下一步操作。'}</p><span>查进度 · 找缺口 · 比方案 · 准备草稿</span></div>`);
        messages.scrollTop(messages[0].scrollHeight);
    }
    renderBusy() {
        this.host.find('.hub-send').prop('disabled', this.restoring || this.busy || !this.data?.model_ready).text(this.busy ? '分析中…' : '发送 ↑');
        this.host.find('.hub-new').prop('disabled', this.restoring || this.busy);
    }
    async send() {
        const question = this.host.find('textarea').val().trim();
        if (!question || this.restoring || this.busy || !this.data?.model_ready) return;
        this.busy = true;
        this.renderBusy();
        try {
            const response = await this.call('start', {question, module: this.module, conversation: this.conversation, request_id: crypto.randomUUID()});
            const turn = {...response, question, agent_module: this.module, state: 'queued'};
            this.turns.push(turn);
            this.host.find('textarea').val('');
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
        finally { this.busy = false; this.renderMessages(); this.renderBusy(); }
    }
};
