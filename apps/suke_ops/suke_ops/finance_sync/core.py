"""与框架无关的数据契约、校验和有界分页；事务由存储层负责。"""
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation

ENTITIES = {'supplier': '供应商', 'item': '物料', 'purchase_order': '采购订单',
            'payable': '应付', 'payment': '付款／核销'}
STATES = ('active', 'draft', 'closed', 'cancelled', 'deleted')
TEXT = ('source_id', 'line_id', 'source_org', 'number', 'supplier_code', 'item_code', 'title',
        'currency', 'uom', 'reference', 'source_status')
NUMBERS = ('amount', 'outstanding', 'qty', 'rate')
FIELDS = {*TEXT, *NUMBERS, 'modified_at', 'due_date'}
REQUIRED = {'source_id', 'source_org', 'number', 'modified_at', 'source_status'}
IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_.]*\Z')


class SyncError(ValueError):
    """可以安全展示的本模块错误，不包含厂商原始响应或凭据。"""


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, default=str)


def fingerprint(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def source_zone(value):
    if value == 'UTC':
        return timezone.utc
    if not isinstance(value, str) or not re.fullmatch(r'[+-](?:0\d|1[0-3]):[0-5]\d|[+-]14:00', value):
        raise SyncError('源时区须为明确UTC偏移，如+08:00')
    minutes = int(value[1:3]) * 60 + int(value[4:6])
    return timezone(timedelta(minutes=minutes if value[0] == '+' else -minutes))


def timestamp(value, zone='UTC'):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=source_zone(zone))
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, KeyError):
        raise SyncError('源更新时间无效') from None


def number(value):
    if value is None or value == '':
        return None
    try:
        d = Decimal(str(value))
        if not d.is_finite() or abs(d) >= Decimal('1e20') or d.as_tuple().exponent < -10:
            raise ValueError
        return format(d, 'f')
    except (ValueError, InvalidOperation):
        raise SyncError('金额或数量格式、精度无效') from None


def validate_binding(entity, binding):
    if entity not in ENTITIES or not isinstance(binding, dict):
        raise SyncError('无效同步实体或字段映射')
    fields = binding.get('fields')
    statuses = binding.get('status_map')
    if (not isinstance(fields, dict) or not REQUIRED <= fields.keys() or not fields.keys() <= FIELDS
            or not isinstance(statuses, dict) or not statuses
            or any(not isinstance(k, str) or v not in STATES for k, v in statuses.items())):
        raise SyncError('字段或状态映射不完整')
    if any(not isinstance(s, str) or not IDENTIFIER.fullmatch(s)
           for s in [binding.get('form_id'), *fields.values()]):
        raise SyncError('表单和字段只允许标识符，不允许查询表达式')
    grain = binding.get('grain')
    if grain not in ('document', 'line') or (entity in ('payable', 'payment') and grain != 'document'):
        raise SyncError('应付和结算第一版按整单同步，不能重复累加表头金额')
    if grain == 'line' and 'line_id' not in fields:
        raise SyncError('明细粒度需要源明细内码')
    if grain == 'document' and 'line_id' in fields:
        raise SyncError('整单粒度不能映射明细内码，请按源单据内码去重')
    if entity in ('payable', 'payment', 'purchase_order') and not {'currency', 'amount'} <= fields.keys():
        raise SyncError('业务单据需要币种和同粒度金额')
    if entity == 'payable' and 'outstanding' not in fields:
        raise SyncError('应付需映射源端权威未结金额')
    return binding


def normalize(entity, row, org, zone):
    if entity not in ENTITIES or not isinstance(row, dict):
        raise SyncError('无效同步实体或记录')
    result = {}
    for key in TEXT:
        value = row.get(key)
        value = '' if value is None else str(value).strip()
        if len(value) > (500 if key == 'title' else 140) or '\x00' in value:
            raise SyncError('源标识或文本超长')
        result[key] = value
    if not result['source_id'] or not result['number'] or result['source_org'] != str(org):
        raise SyncError('源主键缺失或组织越权')
    if entity in ('payable', 'payment') and result['line_id']:
        raise SyncError('整单财务快照不接受明细内码，避免重复累计表头金额')
    result['modified_at'] = timestamp(row.get('modified_at'), zone)
    result['state'] = row.get('state')
    if result['state'] not in STATES:
        raise SyncError('源状态未映射')
    for key in NUMBERS:
        result[key] = number(row.get(key))
    if entity in ('purchase_order', 'payable', 'payment') and (not result['currency'] or result['amount'] is None):
        raise SyncError('缺少币种或金额')
    if entity == 'payable' and result['outstanding'] is None:
        raise SyncError('缺少权威未结金额，不能当作零')
    due = row.get('due_date')
    try:
        result['due_date'] = datetime.fromisoformat(str(due)).date().isoformat() if due else None
    except ValueError:
        raise SyncError('到期日无效') from None
    result['entity'] = entity
    return result


def synchronize(connector, entity, org, zone, since, until, write, page_size=200, max_pages=200):
    """写入和游标在调用方同一事务内提交；任何一页失败均不能推进游标。"""
    if entity not in ENTITIES or not 1 <= page_size <= 500 or max_pages < 1:
        raise SyncError('同步范围无效')
    until = timestamp(until)
    since = timestamp(since) if since else None
    if since and since > until:
        raise SyncError('同步时间窗倒置')
    count, seen = 0, set()
    for page in range(max_pages):
        rows = connector.fetch(entity, since, until, page * page_size, page_size)
        if not isinstance(rows, list) or len(rows) > page_size:
            raise SyncError('接口分页响应无效')
        for raw in rows:
            row = normalize(entity, raw, org, zone)
            if row['modified_at'] > until or (since and row['modified_at'] < since):
                raise SyncError('源记录超出固定读取窗口')
            key = (row['source_id'], row['line_id'])
            if key in seen:
                raise SyncError('源分页出现重复主键，请核对粒度或分页稳定性')
            seen.add(key)
            write(row)
            count += 1
        if len(rows) < page_size:
            return {'read': count, 'pages': page + 1, 'checkpoint': until}
    raise SyncError('同步页数达到上限，保留游标；请缩小范围或调整批次')
