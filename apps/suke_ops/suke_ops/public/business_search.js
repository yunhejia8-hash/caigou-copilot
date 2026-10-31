/* 搜索目录与完整记录查询；客户端只传目录ID，不接受任意筛选或字段。 */
(() => {
    const esc = value => frappe.utils.escape_html(String(value ?? ''));
    const api = async (method, args = {}) => (await frappe.call({method: 'suke_ops.business_search.' + method, args})).message;
    const kinds = {page: '功能', query: '常用查询', type: '数据'};
    let dialog, host, entries = [], modules = {}, sequence = 0, selected = '', group = '', browsing = false;

    function navigate(route) {
        dialog.hide();
        frappe.set_route(route);
    }
    function entryCard(entry) {
        const selectedClass = entry.id === selected ? ' is-selected' : '';
        return `<button type="button" class="biz-entry${selectedClass}" data-biz-entry="${esc(entry.id)}"><span><b>${esc(entry.label)}</b><small>${esc(kinds[entry.kind])} · ${esc(modules[entry.module])}</small></span><small>${esc(entry.description)}</small></button>`;
    }
    function renderDirectory(list) {
        const common = ['query:buying:0', 'query:buying:1', 'query:selling:0', 'query:selling:1', 'query:stock:2', 'query:manufacturing:0'];
        const showCommon = !browsing && !group && !host.find('.biz-query').val().trim();
        const visible = showCommon ? list.filter(e => e.kind === 'page' && e.id.split(':').length === 3 || common.includes(e.id)) : list;
        const groups = Object.keys(modules).map(key => {
            const items = visible.filter(entry => entry.module === key);
            return items.length ? `<section><h4>${esc(modules[key])}<small>${items.length}个入口</small></h4><div class="biz-entries">${items.map(entryCard).join('')}</div></section>` : '';
        }).join('');
        host.find('.biz-directory').html(groups || '<p class="biz-empty">没有匹配的可访问入口。</p>');
        host.find('.biz-browse-all').toggle(!group && !host.find('.biz-query').val().trim())
            .text(browsing ? '收起，显示常用入口' : `浏览全部目录（${list.length}个入口）`);
    }
    function rowHTML(row) {
        return `<button type="button" class="biz-record" data-biz-type="${esc(row.doctype)}" data-biz-name="${esc(row.name)}"><span><b>${esc(row.name)}</b><span>${esc(row.title)}</span></span><small>${esc(row.status)}<br>${esc(row.modified.slice(0, 16))}</small></button>`;
    }
    function renderRecords(data) {
        host.find('.biz-records').html(data.groups.length ? data.groups.map(g => `<section class="biz-result-group"><h4>${esc(g.label)}<small>共${esc(g.total)}条可见记录</small></h4><div class="biz-record-list">${g.rows.map(rowHTML).join('') || '<p class="biz-empty">当前没有符合条件的记录。</p>'}</div><div class="biz-pagination"><small>${g.rows.length ? `第${g.start + 1}–${g.start + g.rows.length}条 / 共${g.total}条` : '0条'} · 按修改时间排序</small><div>${selected ? `${g.start > 0 ? `<button type="button" data-biz-page="${Math.max(0, g.start - g.page_length)}">上一页</button>` : ''}${g.has_more ? `<button type="button" data-biz-page="${g.start + g.page_length}">下一页</button>` : ''}` : `<button type="button" data-biz-scope="${esc(g.entry)}">在此类数据中搜索 / 查看全部</button>`}</div></div></section>`).join('') : '<p class="biz-empty">没有符合条件的可见记录。可以换用单号、名称或物料编码。</p>');
        host.find('.biz-updated').text('查询于 ' + data.at + ' · ' + data.scope);
    }
    async function refresh(start = 0) {
        const current = ++sequence;
        const q = host.find('.biz-query').val().trim();
        host.find('.biz-status').text('正在查询…');
        host.find('.biz-records').empty();
        try {
            if (!selected) {
                const data = await api('directory', {q, module: group});
                if (current !== sequence) return;
                renderDirectory(data.entries);
            }
            host.find('.biz-directory').toggle(!selected);
            if (selected) host.find('.biz-browse-all').hide();
            host.find('.biz-results').toggle(Boolean(q || selected));
            if (q || selected) {
                const data = await api('records', {q, entry: selected, start, page_length: 20, module: group});
                if (current !== sequence) return;
                // 模块选择同时约束全局记录结果；按服务端目录过滤类型。
                if (group && !selected) {
                    const permitted = new Set(entries.filter(e => e.kind === 'type' && (e.modules || [e.module]).includes(group)).map(e => e.id));
                    data.groups = data.groups.filter(g => permitted.has('type:' + g.doctype));
                }
                renderRecords(data);
            } else host.find('.biz-updated').text('目录显示当前账号可访问的功能和数据类型，记录按需实时查询。');
            host.find('.biz-status').text('');
        } catch (_) {
            if (current === sequence) host.find('.biz-status').text('查询未完成，请重试或检查当前账号权限。');
        }
    }
    function setScope(entry, resetQuery = false) {
        selected = entry || '';
        if (resetQuery) host.find('.biz-query').val('');
        const e = entries.find(item => item.id === selected);
        host.find('.biz-scope').text(e ? e.label : selected ? '指定数据范围' : '全部可见业务数据');
        host.find('.biz-clear-scope').toggle(Boolean(selected));
        host.find('.biz-group').prop('disabled', Boolean(selected));
        refresh();
    }
    function setup() {
        dialog = new frappe.ui.Dialog({title: '业务搜索与目录', size: 'extra-large', fields: [{fieldname: 'search', fieldtype: 'HTML'}]});
        host = dialog.fields_dict.search.$wrapper;
        host.addClass('biz-search').html(`<div class="biz-form" role="search"><label class="sr-only" for="biz-query">搜索业务入口、单号、名称或物料</label><input id="biz-query" class="form-control biz-query" maxlength="120" placeholder="搜索功能、单号、名称或物料编码…"><button type="button" class="btn btn-primary biz-submit">搜索</button><select class="form-control biz-group" aria-label="按业务模块浏览"><option value="">全部模块</option></select></div><div class="biz-search-scope"><b class="biz-scope">全部可见业务数据</b><button type="button" class="btn btn-default btn-xs biz-clear-scope">返回全部目录</button><span class="biz-status" role="status" aria-live="polite"></span></div><p class="biz-hint">目录帮你找到功能；记录查询覆盖完整授权数据。采购申请也可搜“需求单”，收货也可搜“入库”。</p><button type="button" class="btn btn-default btn-xs biz-browse-all">浏览全部目录</button><div class="biz-directory"></div><div class="biz-results"><h3>业务记录</h3><p class="biz-field-note">支持单号、名称、物料编码、客户／供应商及状态；具体字段依数据类型与权限而定。摘要不显示金额、议价和对话内容。</p><div class="biz-records"></div></div><p class="biz-updated"></p>`);
        host.on('click', '.biz-submit', () => refresh());
        host.on('keydown', '.biz-query', event => {if (event.key === 'Enter' && !event.isComposing) {event.preventDefault(); refresh();}});
        host.on('input', '.biz-query', frappe.utils.debounce(() => refresh(), 300));
        host.on('change', '.biz-group', event => {group = event.target.value; refresh();});
        host.on('click', '.biz-clear-scope', () => setScope('', true));
        host.on('click', '.biz-browse-all', () => {browsing = !browsing; refresh();});
        host.on('click', '[data-biz-entry]', event => {
            const entry = entries.find(item => item.id === event.currentTarget.dataset.bizEntry);
            if (!entry) return;
            if (entry.kind === 'page') navigate(entry.route);
            else setScope(entry.id, true);
        });
        host.on('click', '[data-biz-scope]', event => setScope(event.currentTarget.dataset.bizScope));
        host.on('click', '[data-biz-page]', event => refresh(Number(event.currentTarget.dataset.bizPage)));
        host.on('click', '[data-biz-type]', async event => {
            const {bizType, bizName} = event.currentTarget.dataset;
            const button = $(event.currentTarget).prop('disabled', true);
            try {navigate(await api('open_record', {doctype: bizType, name: bizName}));}
            finally {button.prop('disabled', false);}
        });
        dialog.$wrapper.on('hidden.bs.modal', () => {sequence++;});
    }
    frappe.business_search = {
        async open(options = {}) {
            if (!dialog) setup();
            // 每次打开重新取得目录，以反映权限调整；不跨账号缓存业务记录。
            const opening = ++sequence;
            host.find('.biz-directory,.biz-records').empty();
            entries = [];
            browsing = false;
            dialog.show();
            host.find('.biz-status').text('正在加载目录…');
            try {
                const data = await api('directory');
                if (opening !== sequence) return;
                entries = data.entries; modules = data.modules;
                host.find('.biz-group').html('<option value="">全部模块</option>' + Object.entries(modules).map(([key, title]) => `<option value="${esc(key)}">${esc(title)}</option>`).join(''));
                group = options.module || '';
                host.find('.biz-group').val(group);
                host.find('.biz-query').val(options.q || '');
                setScope(options.entry || '');
                host.find('.biz-query').trigger('focus');
            } catch (_) {host.find('.biz-status').text('目录加载失败，请重试。');}
        }
    };
    function installEntry() {
        const modal = $('#navbar-search').closest('.modal');
        if (modal.length && !modal.find('.biz-search-launch').length) {
            $('<button type="button" class="btn btn-default biz-search-launch">业务搜索与目录 · 浏览全部</button>').prependTo(modal.find('.modal-body')).on('click', () => {
                const q = modal.find('#navbar-search').val() || '';
                modal.modal('hide');
                frappe.business_search.open({q});
            });
        }
        const params = new URLSearchParams(window.location.search);
        if (params.get('business_search') === '1') {
            const options = {q: params.get('q') || '', entry: params.get('entry') || ''};
            for (const key of ['business_search', 'q', 'entry']) params.delete(key);
            const url = window.location.pathname + (params.size ? '?' + params.toString() : '') + window.location.hash;
            window.history.replaceState(window.history.state, '', url);
            frappe.business_search.open(options);
        }
    }
    $(document).on('app_ready', installEntry);
    // 原生搜索弹窗可能在首次点击时才创建，显示后再挂载目录入口。
    $(document).on('shown.bs.modal', '.modal', installEntry);
    frappe.after_ajax(installEntry);
    frappe.router.on('change', () => setTimeout(installEntry, 0));
})();
