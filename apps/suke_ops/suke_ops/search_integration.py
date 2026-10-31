"""搜索目录、完整分页、业务筛选与真实普通账号权限验收；所有测试数据回滚。"""
import time
import uuid

import frappe

from suke_ops import business_search as search, catalog, hub


def run():
    original = frappe.session.user
    frappe.set_user('Administrator')
    checks = []
    started = time.monotonic()
    suffix = uuid.uuid4().hex[:8]
    baseline = {dt: frappe.db.count(dt) for dt in ['User', 'Item', 'User Permission']}
    try:
        directory = search.directory()['entries']
        assert len({e['id'] for e in directory}) == len(directory)
        assert sum(e['kind'] == 'type' for e in directory) == 25
        for word in ['采购', '入库', '审批', '缺料', '需求单']:
            assert search.directory(word)['entries'], word
        checks.append('25类数据与业务目录齐全，中文别名命中且目录ID唯一')
        for module in [*catalog.MODULE_DOCS, *catalog.STAGE_DOCS]:
            board = hub.module_board(module)
            for metric in board['metrics']:
                actual = search.records(entry=metric['search_entry'])['groups'][0]
                assert actual['total'] == metric['value'], (module, metric['label'], actual['total'], metric['value'])
            all_rows = search.records(entry=board['search_entry'], page_length=50)['groups'][0]
            assert {row.name for row in board['rows']} <= {r['name'] for r in all_rows['rows']}
        checks.append('四模块及六采购环节的指标计数和主表筛选与搜索一致')
        # 按真实数据类型调用全部查询分支，包括子表物料关联。
        all_groups = search.records('SUKE')['groups']
        assert len({(r['doctype'], r['name']) for g in all_groups for r in g['rows']}) == sum(len(g['rows']) for g in all_groups)
        for g in all_groups:
            assert all(set(r) <= {'name', 'doctype', 'label', 'title', 'status', 'modified'} for r in g['rows'])
        assert search.awesomebar('采购')
        checks.append('全类型实时查询与快捷搜索扩展可用，记录去重且摘要不含金额和审计正文')
        case_doc = frappe.get_doc('Supply Case', 'SUP-CASE-00001')
        source_po = frappe.get_doc('Purchase Order', case_doc.purchase_order)
        related_marker = 'RELATED-' + suffix
        source_po.items[0].db_set('item_name', related_marker, commit=False)
        associated = search.records(related_marker, 'type:Purchase Order')['groups'][0]
        assert associated['total'] == 1 and associated['rows'][0]['name'] == source_po.name
        checks.append('通过物料行名称可定位父订单，父单权限与去重计数继续生效')
        group = frappe.get_all('Item Group', filters={'is_group': 0}, pluck='name', limit=1)[0]
        uom = frappe.get_all('UOM', pluck='name', limit=1)[0]
        marker = 'BIZSEARCH-' + suffix
        item_names = []
        for i in range(27):
            item = frappe.get_doc({'doctype': 'Item', 'item_code': f'{marker}-{i:02}', 'item_name': f'{marker}物料{i:02}',
                                   'item_group': group, 'stock_uom': uom, 'is_stock_item': 0}).insert()
            item_names.append(item.name)
        first = search.records(marker, 'type:Item')['groups'][0]
        second = search.records(marker, 'type:Item', start=20)['groups'][0]
        assert first['total'] == second['total'] == 27 and len(first['rows']) == 20 and len(second['rows']) == 7
        assert len({r['name'] for r in first['rows'] + second['rows']}) == 27
        assert first['has_more'] and not second['has_more']
        assert search.records(marker, 'type:Item', page_length=999)['groups'][0]['page_length'] == 50
        checks.append('27条合成物料全部可分页检索，超过面板12条限制，无重复或漏页')
        frappe.get_doc('Item', item_names[0]).db_set('item_name', 'UPDATED-' + marker, update_modified=True, commit=False)
        assert search.records('UPDATED-' + marker, 'type:Item')['groups'][0]['total'] == 1
        assert search.records("%' OR 1=1 --", 'type:Item')['groups'][0]['total'] == 0
        checks.append('修改后立即可搜，搜索特殊字符作为文本处理')
        users = {}
        for role in ['Desk User', 'Purchase User', 'Supply Planner', 'Stock User']:
            name = 'bizsearch-' + suffix + '-' + role.replace(' ', '-').lower() + '@example.invalid'
            frappe.get_doc({'doctype': 'User', 'email': name, 'first_name': '搜索权限验收', 'send_welcome_email': 0,
                            'user_type': 'System User', 'roles': [{'role': role}]}).insert()
            users[role] = name
        def denied(call):
            try:
                call()
            except frappe.PermissionError:
                frappe.clear_messages()
                return
            raise AssertionError('应拒绝的查询被允许')
        frappe.set_user(users['Desk User'])
        ids = {e['id'] for e in search.directory()['entries']}
        assert 'type:Purchase Order' not in ids and 'type:Supply Case' not in ids
        denied(lambda: search.records(entry='type:Purchase Order'))
        denied(lambda: search.records(entry='type:Agent Turn'))
        assert not search.records('SUKE')['groups']
        checks.append('真实基础账号无法查询采购数据、会话记录或获知其计数')
        frappe.set_user(users['Purchase User'])
        ids = {e['id'] for e in search.directory()['entries']}
        assert 'type:Purchase Order' in ids and 'type:Sales Order' not in ids
        denied(lambda: search.records(entry='type:Sales Order'))
        assert search.records(marker, 'type:Item')['groups'][0]['total'] == 27
        po = search.records(entry='type:Purchase Order')['groups'][0]
        assert po['total'] == hub.count('Purchase Order', {})
        # 用户限制必须同时影响全局记录、分页和计数。
        frappe.set_user('Administrator')
        permission = frappe.get_doc({'doctype': 'User Permission', 'user': users['Purchase User'],
                                     'allow': 'Item', 'for_value': item_names[0], 'apply_to_all_doctypes': 1}).insert()
        frappe.set_user(users['Purchase User'])
        restricted = search.records(marker, 'type:Item')['groups'][0]
        assert restricted['total'] == 1 and restricted['rows'][0]['name'] == item_names[0]
        denied(lambda: search.open_record('Item', item_names[1]))
        checks.append('真实采购账号按角色隔离销售数据，User Permission同时约束记录、计数和打开')
        frappe.set_user('Administrator')
        frappe.get_doc({'doctype': 'Property Setter', 'doctype_or_field': 'DocField', 'doc_type': 'Item',
                        'field_name': 'item_name', 'property': 'permlevel', 'property_type': 'Int', 'value': '1'}).insert()
        frappe.clear_cache(doctype='Item')
        frappe.set_user(users['Purchase User'])
        assert search.records('UPDATED-' + marker, 'type:Item')['groups'][0]['total'] == 0
        safe_title = search.records(item_names[0], 'type:Item')['groups'][0]['rows'][0]['title']
        assert safe_title == item_names[0]
        checks.append('字段提升到受限层级后，普通账号不能通过匹配、计数或摘要推断字段内容')
        frappe.set_user(users['Supply Planner'])
        assert frappe.has_permission('Supply Case', 'read')
        assert search.records(entry='type:Supply Case')['groups'][0]['total'] == 0
        case = frappe.get_all('Supply Case', pluck='name', limit=1)[0]
        denied(lambda: search.open_record('Supply Case', case))
        assert not hub.records('Supply Case')
        checks.append('协同角色无来源单据权限时，搜索和面板均不返回案例及其计数')
        frappe.set_user(users['Stock User'])
        raw = search.records(entry='type:Purchase Order')['groups'][0]
        assert raw['total'] >= 1 and all('grand_total' not in row for row in raw['rows'])
        checks.append('已有库存角色授权继续生效，但搜索摘要不返回订单金额')
        frappe.set_user('Guest')
        denied(search.directory)
        denied(lambda: search.records('SUKE'))
        denied(lambda: search.open_record('Item', item_names[0]))
        checks.append('Guest无法访问目录、记录查询或打开接口')
        frappe.set_user('Administrator')
        denied(lambda: search.records(entry='type:User'))
        denied(lambda: search.records(entry='query:unknown:0'))
        checks.append('拒绝非目录数据类型和伪造查询ID')
        return {'status': 'PASS', 'checks': checks, 'count': len(checks), 'directory_entries': len(directory),
                'seconds': round(time.monotonic() - started, 2), 'synthetic_data_rolled_back': True}
    finally:
        frappe.db.rollback()
        frappe.set_user('Administrator')
        frappe.clear_cache(doctype='Item')
        assert {dt: frappe.db.count(dt) for dt in baseline} == baseline
        frappe.set_user(original)
