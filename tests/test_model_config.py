"""确保填写模型 Key 不覆盖数据库口令，非法输入不写入配置。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from configure_model import configure, load_model_env


class ModelConfigTests(unittest.TestCase):
    def test_preserves_existing_settings_and_literal_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('DB_PASSWORD=keep-db\nADMIN_PASSWORD=keep-admin\nHTTP_PORT=8082\n# keep comment\nANTHROPIC_MODEL=old\n', encoding='utf-8')
            configure(path, 'test-$literal#key')
            result = path.read_text(encoding='utf-8')
            self.assertIn('DB_PASSWORD=keep-db\nADMIN_PASSWORD=keep-admin\nHTTP_PORT=8082\n# keep comment\n', result)
            self.assertEqual(result.count('ANTHROPIC_MODEL='), 1)
            with patch.dict(os.environ, {}, clear=True):
                load_model_env(path)
                self.assertEqual(os.environ['ANTHROPIC_AUTH_TOKEN'], 'test-$literal#key')
                self.assertEqual(os.environ['ANTHROPIC_MODEL'], 'gpt-6.1-sol')
                self.assertNotIn('DB_PASSWORD', os.environ)
                os.environ['ANTHROPIC_MODEL'] = 'explicit-model'
                load_model_env(path)
                self.assertEqual(os.environ['ANTHROPIC_MODEL'], 'explicit-model')

    def test_invalid_input_does_not_modify_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            original = 'DB_PASSWORD=keep\n'
            path.write_text(original, encoding='utf-8')
            for token, url in [('', 'https://example.com'), ('key\nNEW=value', 'https://example.com'), ('key', 'http://example.com')]:
                with self.assertRaises(ValueError):
                    configure(path, token, base_url=url)
                self.assertEqual(path.read_text(encoding='utf-8'), original)


if __name__ == '__main__':
    unittest.main()
