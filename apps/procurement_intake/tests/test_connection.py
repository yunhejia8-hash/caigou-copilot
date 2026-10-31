"""验证事务结束后关闭连接，并保留提交和异常回滚语义。"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app


class ConnectionTest(unittest.TestCase):
    def test_connection_closes_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(app, 'DB', Path(directory) / 'connection.db'):
                with app.connect() as connection:
                    connection.execute('CREATE TABLE probe (value INTEGER)')
                    connection.execute('INSERT INTO probe VALUES (1)')
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute('SELECT 1')
                with self.assertRaisesRegex(ValueError, '回滚'):
                    with app.connect() as connection:
                        connection.execute('INSERT INTO probe VALUES (2)')
                        raise ValueError('回滚')
                with app.connect() as connection:
                    self.assertEqual(connection.execute('SELECT value FROM probe').fetchall()[0][0], 1)
                    self.assertEqual(connection.execute('SELECT count(*) FROM probe').fetchone()[0], 1)
                app.DB.rename(Path(directory) / 'closed.db')
