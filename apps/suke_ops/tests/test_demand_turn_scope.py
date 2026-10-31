"""需求登记应使用当前指令，历史轮次限制不能覆盖最新请求。"""
import unittest
from suke_ops import agent_runtime as runtime


class DemandTurnScopeTests(unittest.TestCase):
    def test_history_is_scoped_and_latest_request_identified(self):
        seen={}
        def provider(system,messages,tools):
            seen.update(system=system,messages=messages)
            return {'stop_reason':'end_turn','content':[{'type':'text','text':'已核对当前请求'}]}
        runtime.respond('control','已批准采购30 Nos的CABLE，请登记需求单',[
            {'role':'user','content':'仅核算，不登记需求'},
            {'role':'assistant','content':'请确认CABLE还是KIT'}],
            [runtime.schema('ask_module','委派',{})],lambda *a:None,[],provider=provider,initial_evidence={'synthetic':True})
        self.assertIn('历史轮次',seen['messages'][0]['content'])
        self.assertIn('本轮最新用户请求',seen['messages'][2]['content'])
        self.assertIn('不得重复追问已明确',seen['system'])
        self.assertIn('供应商',seen['system'])

    def test_delegation_keeps_original_fields_and_authorization(self):
        original='批准CABLE采购30 Nos，2026-11-01，公司甲，仓库乙。请登记需求单。'
        task=runtime.delegation_question(original,'只核算，询问CABLE还是KIT')
        self.assertIn(original,task)
        self.assertIn('只核算，询问CABLE还是KIT',task)
        self.assertIn('本轮原始用户请求',task)
        self.assertIn('委派说明不能撤销',task)

    def test_analysis_only_request_is_not_rewritten_as_authorization(self):
        original='只分析缺口，不创建任何需求单'
        task=runtime.delegation_question(original,'请创建需求单')
        self.assertIn(original,task)
        self.assertIn('不得扩大',task)


if __name__=='__main__':
    unittest.main()
