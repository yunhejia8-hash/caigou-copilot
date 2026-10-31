"""只读连接器契约：fetch(entity, since, until, offset, limit) 返回标准记录。"""
import json
from datetime import datetime
from decimal import Decimal

from .core import validate_binding, timestamp, source_zone, SyncError


class KingdeeConnector:
    def __init__(self, sdk, binding, org, zone):
        self.sdk, self.binding, self.org, self.zone = sdk, binding, str(org), zone

    def fetch(self, entity, since, until, offset, limit):
        b = validate_binding(entity, self.binding)
        fields = b['fields']
        def local(value):
            dt = datetime.fromisoformat(timestamp(value)).astimezone(source_zone(self.zone))
            return dt.replace(tzinfo=None).isoformat(sep=' ', timespec='microseconds' if dt.microsecond else 'seconds')
        # 标识符经过白名单校验；组织值单引号转义，不接受客户端 FilterString。
        filters = [fields['source_org'] + "='" + self.org.replace("'", "''") + "'",
                   fields['modified_at'] + "<='" + local(until) + "'"]
        if since:
            filters.append(fields['modified_at'] + ">='" + local(since) + "'")
        order = [fields['modified_at'], fields['source_id']]
        if fields.get('line_id'):
            order.append(fields['line_id'])
        query = {'FormId': b['form_id'], 'FieldKeys': ','.join(fields.values()),
                 'FilterString': ' AND '.join(filters), 'OrderString': ','.join(k + ' ASC' for k in order),
                 'StartRow': offset, 'Limit': limit, 'TopRowCount': 0}
        try:
            result = self.sdk.ExecuteBillQuery(json.dumps(query, ensure_ascii=False))
            if isinstance(result, str):
                result = json.loads(result, parse_float=Decimal)
        except Exception:
            # 不记录SDK原始异常，避免回显签名、密钥或源端敏感响应。
            raise SyncError('金蝶查询失败，请检查网络、授权或接口限流后重试') from None
        if not isinstance(result, list):
            raise SyncError('金蝶返回业务错误或非列表数据，未推进游标')
        records = []
        for values in result:
            if not isinstance(values, list) or len(values) != len(fields) or any(isinstance(v, (dict, list)) for v in values):
                raise SyncError('金蝶响应字段不匹配或返回业务错误')
            row = dict(zip(fields, values))
            status = str(row.get('source_status'))
            if status not in b['status_map']:
                raise SyncError('金蝶状态未映射，未推进游标')
            row['state'] = b['status_map'][status]
            if b['grain'] == 'line' and not row.get('line_id'):
                raise SyncError('金蝶缺少源明细内码')
            records.append(row)
        return records


class MockConnector:
    """固定合成数据，不访问网络；合成标记由服务端连接模式决定。"""
    def __init__(self, org):
        self.org = str(org)

    def fetch(self, entity, since, until, offset, limit):
        rows = []
        for i in range(1, 4):
            row = {'source_id': str(i), 'line_id': '', 'source_org': self.org,
                   'number': 'MOCK-' + entity.upper() + '-' + str(i), 'title': '合成演示记录',
                   'supplier_code': 'MOCK-S001', 'item_code': 'MOCK-I001', 'currency': 'CNY',
                   'modified_at': '2026-10-30T00:00:00+00:00', 'due_date': '2026-11-15',
                   'state': 'cancelled' if i == 3 else 'active', 'source_status': 'Z' if i == 3 else 'C',
                   'amount': '0.10' if i == 1 else '0.20', 'outstanding': '0.10' if i == 1 else '0.20',
                   'qty': '1', 'uom': '件', 'rate': '0.10', 'reference': 'MOCK-PO-1'}
            if entity in ('supplier', 'item'):
                for key in ('amount', 'outstanding', 'qty', 'rate'):
                    row[key] = None
            if row['modified_at'] <= until and (not since or row['modified_at'] >= since):
                rows.append(row)
        return rows[offset:offset + limit]


def connector_for(vendor, mode, org, zone, *, sdk=None, binding=None):
    # 用户已要求多厂商扩展；新厂商实现同一fetch契约后在此注册，不伪装为已支持。
    if vendor != 'kingdee':
        raise SyncError('该厂商连接器尚未实现；用友和 SAP 仅预留扩展')
    if mode == 'mock':
        return MockConnector(org)
    if mode != 'live' or sdk is None:
        raise SyncError('真实接口尚未配置SDK或授权')
    return KingdeeConnector(sdk, binding, org, zone)
