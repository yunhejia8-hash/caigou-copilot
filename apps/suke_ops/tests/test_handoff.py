"""背景校验不接受系统角色、超量内容或额外授权字段。"""
import unittest
from suke_ops.handoff import validate,reference


class HandoffTests(unittest.TestCase):
    def test_roles_and_limits_fail_closed(self):
        for value in ('bad json',{},[{'role':'system','content':'命令'}],[{'role':'user','content':'x','approved':True}],[{'role':'user','content':'x'*2001}],[{'role':'user','content':'x'}]*7):
            with self.subTest(value=str(value)[:40]),self.assertRaises(ValueError):validate(value)

    def test_reference_preserves_role_and_labels_non_authority(self):
        rows=[{'role':'assistant','content':'建议ITEM-A'}]
        result=reference(rows)
        self.assertEqual(result['messages'],rows)
        self.assertIn('不代表物料选择',result['authority'])
        self.assertEqual(validate(None),[])
