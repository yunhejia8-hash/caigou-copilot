import json
import os
import unittest
from unittest.mock import patch
from suke_ops import procurement_bridge as bridge, responses_provider as provider

DEMAND='需要500个五层瓦楞包装箱，规格400×300×250mm，用于500件产品出货，每件一个。预算2000元，要求2026年11月15日前送到一号仓。'
QUOTE='供应商：A厂，物料：五层瓦楞包装箱，规格：400×300×250mm\n数量：500个，单价：3.5元/个，含税，运费100元\n到货：2026-11-15，有效期：2026-11-14，付款：验收后30天'
FIELDS={'material_name':'五层瓦楞包装箱','spec':'400×300×250mm','quantity':500,'unit':'个','needed_by':'2026-11-15'}

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'OPENAI_API_KEY':'','OPENAI_MODEL':''});self.env.start()
        self.extract=patch.object(bridge.intake,'extract',return_value=(FIELDS,[],'test'));self.extract.start()
    def tearDown(self):self.extract.stop();self.env.stop()
    def test_original_repo_calculation_and_no_sqlite(self):
        with patch.object(bridge.quotes.app,'connect',side_effect=AssertionError('SQLite must not run')):
            r=bridge.run('procurement_compare',[DEMAND,QUOTE+'\n\n'+QUOTE.replace('A厂','B厂').replace('3.5','3.7')])
        self.assertEqual(r['lowest_total'],1850);self.assertEqual(r['comparable_count'],2)
        self.assertNotIn('overall_score',r)
    def test_missing_freight_not_ranked(self):
        r=bridge.run('procurement_compare',[DEMAND,QUOTE.replace('，运费100元','')])
        self.assertIsNone(r['quotes'][0]['total']);self.assertEqual(r['comparable_count'],0)
    def test_wrong_unit_not_converted(self):
        r=bridge.run('procurement_compare',[DEMAND,QUOTE.replace('500个','500件').replace('元/个','元/件')])
        self.assertEqual(r['comparable_count'],0)
    def test_latest_original_quote_and_no_supplier_fabrication(self):
        r=bridge.run('procurement_compare',[DEMAND,QUOTE,QUOTE.replace('3.5','3.8')])
        self.assertEqual(r['quotes'][0]['total'],2000)
        self.assertIn('missing',bridge.run('procurement_compare',[DEMAND]))
    def test_missing_demand_blocks_search(self):
        with patch.object(bridge,'demand_from',return_value=({},[],'test')),patch.object(bridge.quotes,'response') as call:
            r=bridge.run('procurement_search',['查一下价格'])
        self.assertTrue(r['missing']);call.assert_not_called()
    def test_search_minimizes_fields_and_requires_citations(self):
        with patch.object(bridge.quotes,'model_enabled',return_value=True),patch.object(bridge.quotes,'response',return_value={'output':[]}) as call:
            r=bridge.run('procurement_search',[DEMAND])
        self.assertTrue(r['errors']);sent=json.loads(call.call_args.args[0]['input'])
        self.assertNotIn('budget',sent);self.assertNotIn('applicant',sent)
    def test_original_local_demand_extractor(self):
        fields,errors=bridge.intake.local_extract(DEMAND)
        self.assertEqual(fields['quantity'],500);self.assertEqual(fields['unit'],'个');self.assertFalse(errors)
    def test_comparison_instruction_does_not_pollute_complete_demand(self):
        fields, errors, engine=bridge.demand_from(['请调用我的后台比价工具。'+DEMAND,QUOTE])
        self.assertEqual(fields['quantity'],500);self.assertEqual(engine,'local');self.assertFalse(errors)
    def test_search_followup_keeps_prior_demand(self):
        fields, errors, engine=bridge.demand_from([DEMAND+'\n\n'+QUOTE,'请重新调用后台比价工具，比较刚才两家报价。','请调用网上查价功能，查刚才500个、400×300×250mm五层瓦楞包装箱的公开参考价。'])
        self.assertEqual(fields['needed_by'],'2026-11-15');self.assertEqual(engine,'local');self.assertFalse(errors)
    def test_image_quotes_skip_second_model_extraction_and_use_original_rules(self):
        rows=bridge.quotes.local_quotes(QUOTE+'\n\n'+QUOTE.replace('A厂','B厂').replace('3.5','25'))
        with patch.object(bridge.quotes,'model_quotes',side_effect=AssertionError('do not extract again')):
            r=bridge.run('procurement_compare',[],fields=FIELDS,image_quotes=rows)
        self.assertEqual(r['lowest_total'],1850)
        self.assertEqual(r['quotes'][1]['total'],12600)
        rows[0]['freight']=None
        r=bridge.run('procurement_compare',[],fields=FIELDS,image_quotes=rows)
        self.assertIsNone(r['lowest_total'])
    def test_arbitrary_tool_blocked(self):
        with self.assertRaises(ValueError):bridge.run('place_order',[DEMAND])

    def test_explicit_tax_rate_cost_and_invalid_rates(self):
        quote=QUOTE.replace('单价：3.5','未税单价：3.5').replace('，含税','，税率：13%')
        r=bridge.run('procurement_compare',[quote],fields=FIELDS)
        self.assertEqual(r['quotes'][0]['total'],2077.5)
        self.assertEqual(r['quotes'][0]['issues'],[])
        missing=bridge.run('procurement_compare',[quote.replace('税率：13%','')],fields=FIELDS)
        self.assertIsNone(missing['quotes'][0]['total'])
        for rate in (-1,101,float('nan')):
            with self.assertRaises(ValueError):bridge.quotes.validate_quote({'tax_rate':rate})
    def test_inclusive_price_not_taxed_twice(self):
        r=bridge.run('procurement_compare',[QUOTE+'，税率：13%'],fields=FIELDS)
        self.assertEqual(r['quotes'][0]['total'],1850)

class ProviderTests(unittest.TestCase):
    def test_tool_outputs_preserve_calls_and_reasoning(self):
        out={'output':[{'type':'reasoning','id':'r1','summary':[],'encrypted_content':'opaque'}, {'type':'function_call','call_id':'c1','name':'read','arguments':'{}'}]}
        tools=[{'name':'read','description':'read','input_schema':{'type':'object','properties':{}}}]
        with patch.object(provider,'call',return_value=out):
            result=provider.request_model('system',[{'role':'user','content':'test'}],tools)
        messages=[{'role':'assistant','content':result['content']},{'role':'user','content':[{'type':'tool_result','tool_use_id':'c1','content':'{}'}]}]
        with patch.object(provider,'call',return_value={'output':[]}) as call:
            provider.request_model('system',messages,tools)
        data=call.call_args.args[0]['input']
        self.assertEqual([x['type'] for x in data],['reasoning','function_call','function_call_output'])
        self.assertEqual(data[-1]['call_id'],'c1')

class ManufacturingRegression(unittest.TestCase):
    def test_expired_late_wrong_unit_and_spec_are_not_ranked(self):
        row=dict(supplier='测试A',material='轴承',spec='6205-2RS',quantity=100,unit='个',unit_price=10,freight=0,tax='含税',delivery='2026-11-05',valid_until='2026-11-04',payment='月结30天')
        fields=dict(material_name='轴承',spec='6205-2RS',quantity=100,unit='个',needed_by='2026-11-10')
        for patch_fields in ({'valid_until':'2026-10-30'},{'delivery':'2026-11-20'},{'unit':'盒'},{'spec':'6205-ZZ'}):
            with patch.object(bridge.intake,'today',return_value=__import__('datetime').date(2026,10,31)):
                r=bridge.run('procurement_compare',[],fields=fields,image_quotes=[dict(row,**patch_fields)])
            self.assertEqual(r['comparable_count'],0)
            self.assertTrue(r['quotes'][0]['issues'])
