/* 企业数据连接：显式查询，合成标记与过期状态始终可见。 */
frappe.ERPConnections = class {
    constructor(page) {
        this.page = page;
        this.host = $('<div class="erp-connections"></div>').appendTo(page.main);
        this.offset = 0;
        this.page.add_inner_button('刷新连接', () => this.load());
        this.host.on('change', '[data-connection]', e => {
            this.connection = e.target.value; this.offset = 0; this.queryText = ''; this.render(); this.search();
        });
        this.host.on('change', '[data-entity]', e => { this.entity = e.target.value; this.offset = 0; this.search(); });
        this.host.on('click', '[data-search]', () => {
            this.queryText = this.host.find('[data-query]').val(); this.offset = 0; this.search();
        });
        this.host.on('keydown', '[data-query]', e => { if (e.key === 'Enter') this.host.find('[data-search]').trigger('click'); });
        this.host.on('click', '[data-page]', e => { this.offset = Number(e.currentTarget.dataset.page); this.search(); });
        this.host.on('click', '[data-detail]', e => this.detail(e.currentTarget.dataset.detail));
        this.host.on('click', '[data-sync]', () => this.sync(0));
        this.host.on('click', '[data-full]', () => this.sync(1));
        this.host.on('click', '[data-settings]', () => frappe.set_route('Form', 'ERP Connection', this.connection));
        this.host.on('click', '[data-runs]', () => this.runs());
        this.load();
    }
    esc(value) { return frappe.utils.escape_html(String(value ?? '')); }
    async call(method, args = {}) {
        return (await frappe.call({method: 'suke_ops.finance_sync.api.' + method, args})).message;
    }
    async load() {
        const seq = this.sequence = (this.sequence || 0) + 1;
        try {
            const data = await this.call('connections');
            if (seq !== this.sequence) return;
            this.data = data;
            if (!data.connections.some(c => c.name === this.connection)) this.connection = data.connections[0]?.name;
            if (data.can_manage && !this.manageButtons) {
                this.manageButtons = true;
                this.page.add_inner_button('新建真实连接（默认关闭）', () => frappe.new_doc('ERP Connection', {vendor: 'kingdee', mode: 'live', enabled: 0}));
                this.page.add_inner_button('创建模拟连接', () => frappe.prompt([
                    {fieldname: 'company', label: '本地公司', fieldtype: 'Link', options: 'Company', reqd: 1}
                ], async v => {
                    const created = await this.call('create_demo', v);
                    this.connection = created.name; await this.load();
                }, '创建合成演示连接', '创建'));
            }
            this.render();
            if (this.connection) await this.search();
        } catch (_) {
            if (seq === this.sequence) this.host.html('<p class="alert alert-danger">连接读取失败，请刷新重试；未取得的数据不能视作零余额。</p>');
        }
    }
    current() { return this.data?.connections.find(c => c.name === this.connection); }
    stateLabel(value) { return ({active: '有效', draft: '草稿', closed: '已关闭', cancelled: '已取消', deleted: '已删除'})[value] || value; }
    render() {
        const d = this.current(), esc = v => this.esc(v);
        if (!d) {
            this.host.html('<div class="alert alert-info">暂无获准查询的连接。管理员可以创建模拟连接，普通用户需由管理员授权。真实接口当前尚未联调。</div>');
            return;
        }
        const entities = d.entities;
        if (!entities.some(e => e.entity === this.entity)) this.entity = entities.some(e => e.entity === 'payable') ? 'payable' : entities[0]?.entity;
        this.host.html(`<div class="alert ${d.synthetic ? 'alert-warning' : 'alert-info'}">${d.synthetic ? '合成演示数据 · 不是真实金蝶业务记录' : '外部只读快照 · 以金蝶原系统账务为准'}。查询不会在本系统记账或执行付款。</div>
        <div style="display:flex;flex-wrap:wrap;gap:10px;margin:16px 0;align-items:center">
        <label>连接 <select class="form-control" data-connection>${this.data.connections.map(c => `<option value="${esc(c.name)}" ${c.name === d.name ? 'selected' : ''}>${esc(c.title)} · ${esc(c.company)}</option>`).join('')}</select></label>
        <label>数据对象 <select class="form-control" data-entity>${entities.map(e => `<option value="${esc(e.entity)}" ${e.entity === this.entity ? 'selected' : ''}>${esc(e.label)}</option>`).join('')}</select></label>
        <label>单号／供应商／物料编码 <input class="form-control" data-query maxlength="100" value="${esc(this.queryText || '')}"></label>
        <button class="btn btn-primary" data-search>查询</button></div>
        <p>账套 ${esc(d.account_id)} · 来源组织 ${esc(d.source_org)} · 源时区 UTC${esc(d.source_timezone)} · ${d.enabled ? '连接启用' : '连接停用'} · ${d.auto_sync ? '每15分钟同步' : '手动同步'} · ${d.allow_ai ? '模型使用需逐用户授权' : '未授权模型使用'}</p>
        ${this.data.can_manage ? '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px"><button class="btn btn-default" data-settings>配置与用户授权</button><button class="btn btn-default" data-sync>增量同步</button><button class="btn btn-default" data-full>全量重扫</button><button class="btn btn-default" data-runs>同步记录</button></div>' : ''}
        <div data-result aria-live="polite"></div>`);
    }
    async search() {
        const seq = this.sequence = (this.sequence || 0) + 1;
        if (!this.connection || !this.entity) return;
        const result = this.host.find('[data-result]');
        result.attr('aria-busy', 'true').html('<p>正在按当前账号权限查询…</p>');
        try {
            const d = await this.call('records', {connection: this.connection, entity: this.entity,
                q: this.queryText || '', offset: this.offset, page_length: 20});
            if (seq !== this.sequence) return;
            this.showResult(d);
        } catch (_) {
            if (seq === this.sequence) result.html('<p class="alert alert-danger">查询失败或无权限。未取得有效结果，不能按零金额处理。</p>');
        } finally { if (seq === this.sequence) result.attr('aria-busy', 'false'); }
    }
    showResult(d) {
        const esc = v => this.esc(v);
        const sums = Object.entries(d.summary_by_currency).map(([currency, s]) => `${esc(currency)}：有效记录 ${s.count} 笔，金额 ${esc(s.amount)}，未结 ${s.outstanding === null ? '未提供' : esc(s.outstanding)}`).join('<br>');
        const cols = [['number','来源单号'],['supplier_code','供应商'],['item_code','物料'],['currency','币种'],['amount','金额'],['outstanding','未结'],['state','状态'],['modified_at','源更新时间UTC']];
        this.host.find('[data-result]').html(`<p class="${d.verified_current ? 'text-muted' : 'alert alert-warning'}">${d.verified_current ? '最近同步有效' : '尚未同步、已过期、停用或存在同步错误，需核实'} · 最近成功 ${esc(d.last_success || '未同步')} · 源窗口截至 ${esc(d.source_window_until || '未提供')}</p>
        ${d.last_error ? `<p class="alert alert-danger">同步错误：${esc(d.last_error)}</p>` : ''}
        <p>${sums || '暂无可用的有效记录金额汇总。'}${!d.summary_complete ? '<br><b>汇总未完成，不代表零余额；可缩小搜索范围或先完成同步。</b>' : ''}</p><p class="text-muted">${esc(d.summary_note)}</p>
        <div style="overflow-x:auto"><table class="table table-bordered" style="min-width:1000px;white-space:nowrap"><thead><tr>${cols.map(c => `<th>${c[1]}</th>`).join('')}<th>依据</th></tr></thead><tbody>${d.rows.map(r => `<tr>${cols.map(c => `<td>${esc((c[0] === 'state' ? this.stateLabel(r.state) : r[c[0]]) ?? '未提供')}</td>`).join('')}<td><button class="btn btn-xs btn-default" data-detail="${esc(r.name)}">查看</button></td></tr>`).join('') || '<tr><td colspan="9">此查询范围没有已同步记录。</td></tr>'}</tbody></table></div>
        <p>匹配 ${d.total} 条 · 当前 ${d.total ? d.offset + 1 : 0}–${Math.min(d.offset + d.rows.length, d.total)} 条
        <button class="btn btn-default btn-sm" data-page="${Math.max(0,d.offset-20)}" ${d.offset === 0 ? 'disabled' : ''}>上一页</button>
        <button class="btn btn-default btn-sm" data-page="${d.offset+20}" ${d.offset+d.rows.length >= d.total ? 'disabled' : ''}>下一页</button></p>`);
    }
    async detail(name) {
        const d = await this.call('detail', {name});
        const fields = [['number','来源单号'],['title','来源说明'],['company','本地公司'],['source_org','来源组织'],
            ['source_id','来源内码'],['line_id','源明细内码'],['supplier_code','供应商编码'],['item_code','物料编码'],
            ['currency','币种'],['amount','金额'],['outstanding','未结金额'],['qty','数量'],['uom','单位'],
            ['rate','单价'],['reference','关联源单号'],['due_date','到期日'],['modified_at','源更新时间UTC'],['synced_at','读取时间']];
        frappe.msgprint({title: '来源记录（只读）', message: `<p>${d.synthetic ? '合成演示' : '外部快照'} · ${this.esc(this.stateLabel(d.state) || '状态未提供')}</p><table class="table">${fields.map(([key,label]) => `<tr><th>${label}</th><td>${this.esc(d[key] || '未提供')}</td></tr>`).join('')}</table>`, wide: true});
    }
    async sync(full) {
        if (this.syncing) return;
        this.syncing = true;
        try {
            const r = await this.call('sync_now', {connection: this.connection, full_scan: full});
            frappe.msgprint({title: '同步已排队', message: `任务 ${this.esc(r.run)}。请通过同步记录检查最终结果；排队不代表数据已同步。`});
        } finally { this.syncing = false; }
    }
    async runs() {
        const rows = await this.call('runs', {connection: this.connection});
        frappe.msgprint({title: '最近20次同步', message: `<pre style="white-space:pre-wrap">${this.esc(JSON.stringify(rows, null, 2))}</pre>`, wide: true});
    }
};
