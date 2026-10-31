/* 原生 Desk 对话与面板，业务单据继续使用 ERPNext 表单。 */
frappe.SupplyHub = class {
    constructor(page, view = 'chat', module = 'control', options = {}) {
        options = typeof view === 'object' ? view : options;
        this.options = options;
        if (typeof view === 'object') view = 'chat';
        this.caseName = options.caseName || null;
        this.orderName = options.orderName || null;
        this.onNavigate = options.onNavigate || (() => {});
        if (this.caseName || this.orderName) module = 'buying';
        this.view = view;
        this.page = page;
        this.module = module;
        this.names = {control: '总控', buying: '采购', selling: '销售', stock: '库存', manufacturing: '生产'};
        this.stages = {'buying-demand': '物料需求单', 'buying-inquiry': '询价', 'buying-comparison': '比价', 'buying-approval': '审批', 'buying-order': '采购', 'buying-receipt': '入库'};
        const mainModules = {...this.names};
        Object.assign(this.names, this.stages);
        this.key = 'supply-hub:' + frappe.session.user + ':' + module + (this.caseName ? ':case:' + encodeURIComponent(this.caseName) : this.orderName ? ':order:' + encodeURIComponent(this.orderName) : '');
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
        this.host.on('click', '[data-add-evidence]', event => {
            if (this.busy || this.restoring) return;
            frappe.prompt([{fieldname: 'note', fieldtype: 'Small Text', label: '补充合成信息（系统记录当前登录账号和时间；不等于审批）', reqd: 1}], values => {
                this.send('请核实新增信息，只说明变化、影响和待确认事项。', {previous_turn: event.currentTarget.dataset.addEvidence, human_note: values.note});
            }, '补充信息并复核 · 保留上一版', '记录并复核');
        });
        this.host.on('click', '[data-chat-agent]', (event) => {
            this.options.selectAgent?.(event.currentTarget.dataset.chatAgent);
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
        if (['control', 'buying'].includes(this.module)) this.host.find('.hub-module-board').append('<a class="hub-module-card" href="/assets/suke_ops/scenario-demo/index.html" target="_blank" rel="noopener">采购异常协同演示 · 六类合成场景 →</a>');
        this.host.find('.hub-module-board').append('<section class="hub-activity"><h3>本账号协同足迹</h3><p>正在读取…</p></section>');
        this.activityMarkup = null;
        this.refreshActivity();
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
                manufacturing: ['列出未完成工单，检查生产供料风险。'],
                'buying-demand': ['检查采购物料需求单，列出尚未采购的需求及优先级。'],
                'buying-inquiry': ['检查询价和已收到报价的进度，列出需要催复的问题。'],
                'buying-comparison': ['检查当前供应商报价，核对有效期、币种和物料单位，说明比价还需哪些条件。'],
                'buying-approval': ['审查待审批采购事项，列出业务依据、风险及审批建议。'],
                'buying-order': ['核对采购订单交期与收货进度，列出需要催交的订单。'],
                'buying-receipt': ['检查待到货订单、收货记录及入库质检，指出不能放行的问题。']
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
    sourceCard(source) {
        return `<div class="hub-source"><b>${this.esc(source.id)} · ${this.esc(source.label)}</b><p>${this.esc(source.text)}</p><small>来源：${this.esc(source.source_type)} / ${this.esc(source.source_path)}<br>提交账号：${this.esc(source.reported_by || '未提供（合成设定）')} · 提供时间：${this.esc(source.reported_at || '未提供')}<br>版本：${this.esc(source.version)} · ${this.esc(source.verification)}</small></div>`;
    }
    recordSource(event) {
        if (!event.result_excerpt || event.truncated) return '';
        try {
            const result = JSON.parse(event.result_excerpt), source = result._provenance;
            if (!source) return '';
            return `<div class="hub-source"><b>${this.link(source.doctype,source.name,'打开原单据 ' + source.name)}</b><p>创建账号：${this.esc(source.created_by)} · ${this.esc(source.created_at)}<br>修改账号：${this.esc(source.modified_by)} · ${this.esc(source.modified_at)}<br>本次读取：${this.esc(source.read_at)}</p><small>${this.esc(source.note)}</small></div>`;
        } catch (_) { return ''; }
    }
    auditTrail(turn, prefix = '') {
        const groups = new Map();
        for (const event of turn.events || []) {
            const groupKey = event.module + ':' + event.phase;
            if (!groups.has(groupKey)) groups.set(groupKey, []);
            groups.get(groupKey).push(event);
        }
        if (!groups.size) return '';
        return `<div class="hub-audit">${Array.from(groups.values()).map(events => {
            const latest = events[events.length-1];
            const ids = new Set(events.flatMap(event => event.source_ids || []));
            const sources = (turn.sources || []).filter(row => ids.has(row.id));
            const businessSources = events.map(event => this.recordSource(event)).join('');
            const reply = latest.reply_index === undefined ? null : (turn.discussion || [])[latest.reply_index];
            const key = prefix + turn.name + ':' + events[0].id;
            const time = latest.at.includes('T') ? new Date(latest.at).toLocaleTimeString('zh-CN', {hour12:false}) : latest.at.slice(11,19);
            return `<div class="hub-audit-event hub-event-${this.esc(latest.kind)}"><div class="hub-audit-meta"><b>${this.esc(this.names[latest.module] || latest.module)}</b><span>${this.esc(latest.phase || '')}${reply ? ' · 待核实' : ''}</span><time title="${this.esc(latest.at)}">${this.esc(time)}</time></div><p>${this.esc(latest.summary)}</p>
            ${sources.length ? `<details data-audit-key="sources-${this.esc(key)}"><summary>核对 ${sources.length} 条来源</summary>${sources.map(row => this.sourceCard(row)).join('')}</details>` : ''}
            ${businessSources ? `<details data-audit-key="records-${this.esc(key)}"><summary>查看单据来源与修改记录</summary>${businessSources}</details>` : ''}
            <details data-audit-key="steps-${this.esc(key)}"><summary>${events.length}步操作留痕${reply ? ' · 完整反馈' : ''}</summary>${events.map(event => `<div class="hub-source"><small>${this.esc(event.at)} · ${this.esc(event.summary)}</small>${event.query?.doctype && event.query?.name ? this.link(event.query.doctype,event.query.name,'打开原单据') : ''}${event.query ? '<pre>' + this.esc(JSON.stringify(event.query,null,2)) + '</pre>' : ''}${event.result_excerpt ? '<pre>' + this.esc(event.result_excerpt) + '</pre>' + (event.truncated ? '<small>仅保留前12000字符，完整数据请查原单据。</small>' : '') : ''}</div>`).join('')}${reply ? '<div class="hub-markdown">' + this.answer(reply.answer) + '</div>' : ''}</details></div>`;
        }).join('')}</div>`;
    }
    async refreshActivity() {
        clearTimeout(this.activityTimer);
        if (this.host[0]?.closest?.('[hidden]')) return;
        try {
            const rows = await this.call('discussion_activity', {module: this.module});
            if (!this.host[0]?.isConnected) return;
            const area = this.host.find('.hub-activity');
            const opened = new Set(Array.from(area[0]?.querySelectorAll('details[open]') || []).map(el => el.dataset.auditKey));
            const markup = '<h3>本账号协同足迹</h3><p class="hub-table-note">展示实际工具调用及反馈。合成证据不是ERP查询；模型发言不是人员承诺。</p>' + (rows.length ? rows.map(row => `<details data-audit-key="activity-${this.esc(row.name)}"><summary>${this.esc(row.synthetic ? '合成场景' : '业务查询')} · ${row.state === 'running' ? '进行中' : '记录'} · ${this.esc(row.question.slice(0,50))} · ${row.events.length}条</summary>${this.auditTrail(row, 'activity-')}</details>`).join('') : '<p>暂无本账号的查询留痕。发起分析后，可在对应部门跟踪。</p>');
            if (this.activityMarkup !== markup) {
                area.html(markup); this.activityMarkup = markup;
                area[0]?.querySelectorAll('details').forEach(el => {if (opened.has(el.dataset.auditKey)) el.open = true;});
            }
            if (this.busy || rows.some(row => ['running','queued'].includes(row.state))) this.activityTimer = setTimeout(() => {
                if (this.host[0]?.isConnected && !this.host[0]?.closest?.('[hidden]')) this.refreshActivity();
            }, 4000);
        } catch (_) { this.host.find('.hub-activity').html('<p>协同足迹读取失败，点击刷新可重试。</p>'); }
    }
    renderMessages() {
        const messages = this.host.find('.hub-messages');
        const node = messages[0];
        const previousScroll = node.scrollTop || 0;
        const follow = node.scrollHeight - previousScroll - (node.clientHeight || 0) < 100;
        const opened = new Set(Array.from(node.querySelectorAll?.('details[open]') || []).map(el => el.dataset.auditKey));
        const markup = this.turns.length ? this.turns.map(turn => {
            const completed = ['done', 'failed'].includes(turn.state);
            const replies = (turn.discussion || []).length;
            const summary = turn.answer || (turn.state === 'failed' ? '本次分析未完成，请检查记录后重试。' : '正在核对依据，反馈将逐步显示。');
            const timing = turn.timing ? ` · ${(turn.timing.total_ms / 1000).toFixed(1)}秒 · ${turn.timing.model_calls}次模型请求` : '';
            return `<article class="hub-message hub-user"><small>你</small><div>${this.esc(turn.question)}</div></article><article class="hub-message hub-assistant"><div class="hub-run-head"><b>${turn.synthetic ? '合成场景 · 真实协作' : this.esc(this.names[turn.agent_module] || '总控') + ' Agent'}</b><span>${completed ? '本轮结束' : '正在协同'}${replies ? ' · ' + replies + '/10项反馈' : ''}</span></div>${turn.events?.length ? this.auditTrail(turn) : (turn.discussion || []).map((row,i) => `<details data-audit-key="legacy-${this.esc(turn.name)}-${i}"><summary>${this.esc(this.names[row.module])} · ${this.esc(row.phase)} · 待核实</summary><div class="hub-markdown">${this.answer(row.answer)}</div></details>`).join('')}
            <div class="hub-outcome"><b>${completed ? '待你处理' : '当前状态'}</b><small>${this.esc(timing)}</small><p>${this.esc(summary.length > 220 ? summary.slice(0,220) + '…' : summary)}</p>${summary.length > 220 ? `<details data-audit-key="outcome-${this.esc(turn.name)}"><summary>完整结论与边界</summary><div class="hub-markdown">${this.answer(summary)}</div></details>` : ''}</div>
            ${turn.synthetic ? '<p class="hub-table-note">模型反馈待核实；不自动采购、不释放预留。来源没有提供人时明确留空。</p>' : ''}
            ${turn.synthetic && turn.state === 'done' ? `<button type="button" data-add-evidence="${this.esc(turn.name)}">补充信息并复核</button>` : ''}
            ${(turn.documents || []).map(doc => `<button type="button" class="hub-draft" data-doctype="${this.esc(doc.doctype)}" data-docname="${this.esc(doc.name)}">打开物料需求单 ${this.esc(doc.name)} ↗</button>`).join('')}${turn.documents?.length ? '<p class="hub-table-note">已登记物料需求单。请打开核对当前状态；草稿提交后进入询价、比价和采购审批。</p>' : ''}${(turn.offers || []).map((offer,index) => `<button type="button" class="hub-draft" data-draft="${this.esc(turn.name)}" data-index="${index}">${this.esc(offer.label)} ↗</button>`).join('')}${turn.offers?.length ? '<p class="hub-table-note">打开未保存草稿，仍需核对、审批。</p>' : ''}</article>`;
        }).join('') : `<div class="hub-empty"><h2>${this.module === 'control' ? '查看模块上报，协调下一步' : this.names[this.module] + '有哪些问题需要处理？'}</h2></div>`;
        if (this.messageMarkup === markup) return;
        messages.html(markup); this.messageMarkup = markup;
        node.querySelectorAll?.('details').forEach(el => {if (opened.has(el.dataset.auditKey)) el.open = true;});
        messages.scrollTop(follow ? node.scrollHeight : previousScroll);
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
            if (this.host[0]?.isConnected) this.refreshActivity();
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
                await new Promise(resolve => setTimeout(resolve, 1200));
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
