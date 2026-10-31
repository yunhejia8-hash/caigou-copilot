"""登录用户的小熊陪聊入口；业务仍由原有模块接口执行。"""
import json
import frappe
from frappe.rate_limiter import rate_limit
from suke_ops.hub import require_user
from suke_ops.bear_persona import decide


@frappe.whitelist(methods=['POST'])
@rate_limit(limit=12, seconds=60, ip_based=False, user_based=True)
def chat(message, history=None):
    require_user()
    try:
        history = json.loads(history) if isinstance(history, str) else history
        return decide(message, history if history is not None else [])
    except (ValueError, RuntimeError) as error:
        # 只回传本模块和模型适配层的安全错误，不返回供应商原始响应。
        frappe.throw(str(error))
