"""Transactional ERP acceptance for ordering, private PDF and original preflight."""
import json
import uuid
from pathlib import Path
from unittest.mock import patch
import frappe
from suke_ops import procurement_workflow as flow

TEXT='申请人李明，生产部，需要500个五层瓦楞包装箱，规格400×300×250mm，用于500件产品出货，每件一个。预算2000元，2026年11月15日前送到测试一号仓。'
QUOTE='供应商：A测试厂，物料：五层瓦楞包装箱，规格：400×300×250mm\n数量：500个，单价：3.5元/个，含税，运费100元\n到货：2026-11-15，有效期：2026-11-14，付款：验收后30天\n\n供应商：B测试厂，物料：五层瓦楞包装箱，规格：400×300×250mm\n数量：500个，单价：4元/个，含税，包邮\n到货：2026-11-15，有效期：2026-11-14，付款：验收后30天'


def run():
    frappe.set_user('Administrator')
    conversation=str(uuid.uuid4())
    checks=[]
    counts={dt:frappe.db.count(dt) for dt in ('Material Request','Purchase Order','Purchase Receipt','Stock Ledger Entry')}
    def step(text):
        doc=frappe.get_doc({'doctype':'Agent Turn','conversation':conversation,'agent_module':'buying',
                            'question':text,'state':'queued'}).insert(ignore_permissions=True)
        result=flow.process(doc)
        frappe.db.set_value('Agent Turn',doc.name,{'state':'done','answer':result['answer'],'result':json.dumps(result,ensure_ascii=False,default=str)})
        return doc,result
    try:
        _,r=step('需要500个五层瓦楞包装箱')
        assert r['workflow']['phase']=='needs_info' and not r['application']['preview_url']
        checks.append('缺失字段时停在预审补充，不生成PDF或询价')
        with patch.object(flow.procurement_bridge,'run',side_effect=AssertionError('不能提前比价')):
            application,r=step(TEXT.replace('测试一号仓','流程验收仓-'+conversation[:8])+'\n\n'+QUOTE)
            wf=r['workflow'];assert wf['phase']=='awaiting_confirmation'
            assert wf['preflight']['state']=='review' and wf['preflight']['unit_price'] is None
            assert wf['pdf'] and r['application']['download_url']
            _,early=step(QUOTE);assert early['workflow']['phase']=='awaiting_confirmation'
        checks.append('需求和报价一次输入也先预审，生成待复核PDF后等待确认')
        response=flow.application_pdf(application.name)
        content=response.get_data();assert content.startswith(b'%PDF')
        assert response.headers['Content-Disposition'].startswith('inline')
        assert flow.application_pdf(application.name,1).headers['Content-Disposition'].startswith('attachment')
        Path('/tmp/procurement-stage-proof.pdf').write_bytes(content)
        checks.append('同一PDF支持inline预览与attachment下载，中文字体已嵌入')
        _,r=step(f"确认申请 {wf['id']} 第{wf['revision']}版")
        assert r['workflow']['phase']=='inquiry'
        with patch.object(flow.procurement_bridge.quotes,'model_enabled',return_value=False):
            _,r=step(QUOTE);assert r['workflow']['phase']=='quotations_ready'
        checks.append('确认后才进入询价，保存报价后等待独立比价动作')
        with patch.object(flow.procurement_bridge.quotes,'model_enabled',return_value=False):
            _,r=step('开始比价')
        assert r['workflow']['phase']=='compared' and r['procurement'][0]['result']['lowest_total']==1850
        assert r['workflow']['approval']['status']=='approved' and r['application']['approval']['amount']==1850
        checks.append('显式比价得到1850/2000元，最终1850元方案审批自动通过并持久化')
        _,r=step('预算改为3000元')
        assert r['workflow']['phase']=='awaiting_confirmation' and r['workflow']['revision']==wf['revision']+1
        _,r=step(f"确认申请 {wf['id']} 第{wf['revision']}版")
        assert r['workflow']['phase']=='awaiting_confirmation'
        assert flow.application_pdf(application.name).get_data()==content
        checks.append('需求修改生成新版本、清除原确认；旧确认被拒绝，旧PDF保持快照')
        frappe.set_user('Guest')
        try:
            flow.application_pdf(application.name)
            raise AssertionError('Guest读取申请PDF')
        except frappe.PermissionError:
            checks.append('未授权用户不能读取申请PDF')
        finally:
            frappe.set_user('Administrator')
        assert all(frappe.db.count(dt)==count for dt,count in counts.items())
        checks.append('未新增正式ERP申请/采购/收货/库存流水')
        return {'passed':len(checks),'checks':checks,'pdf_path':'/tmp/procurement-stage-proof.pdf'}
    finally:
        frappe.db.rollback()
