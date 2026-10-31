"""Read an authorized workbook into a separate historical dataset, never demo stock."""
import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
import chat_service as chat

EXPECTED=['月份','物料编码','实际销量(件)','促销标识','单价(元)','在途量(件)']

def import_history(path,price_kind='purchase'):
    import openpyxl
    path=Path(path)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    book=openpyxl.load_workbook(path,read_only=True,data_only=True)
    records=[]
    try:
        for sheet in book:
            rows=sheet.iter_rows(values_only=True)
            header=list(next(rows,[]))
            if header!=EXPECTED: raise ValueError(f'{sheet.title} 的列结构不符合需求历史格式')
            for rowno,row in enumerate(rows,2):
                if not any(x is not None for x in row): continue
                month,code,sales,promotion,price,incoming=row
                if not isinstance(month,str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month): raise ValueError(f'第{rowno}行月份无效')
                if not isinstance(code,str) or not code.strip(): raise ValueError(f'第{rowno}行缺少物料编码')
                for value in (sales,incoming):
                    if not isinstance(value,(float,int)) or isinstance(value,bool) or value<0 or value!=int(value): raise ValueError(f'第{rowno}行数量无效')
                if not isinstance(price,(float,int)) or isinstance(price,bool) or not 0<price<1e9: raise ValueError(f'第{rowno}行单价无效')
                records.append((code.strip(),month,int(sales),str(promotion or '无'),float(price),int(incoming),sheet.title,rowno))
    finally: book.close()
    if not records: raise ValueError('没有可导入的历史记录')
    if len({(r[0],r[1]) for r in records})!=len(records): raise ValueError('存在重复物料月份，未导入')
    chat.init_tables()
    with app.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        prior=c.execute('SELECT id FROM history_sources WHERE sha256=?',(digest,)).fetchone()
        if prior:
            c.execute('UPDATE history_sources SET active=0')
            c.execute('UPDATE history_sources SET active=1,price_kind=? WHERE id=?',(price_kind,prior['id']))
            return {'source_id':prior['id'],'rows':len(records),'already_imported':True}
        c.execute('UPDATE history_sources SET active=0')
        source_id=c.execute('INSERT INTO history_sources(filename,sha256,price_kind,imported,active) VALUES (?,?,?,?,1)',(path.name,digest,price_kind,chat.now())).lastrowid
        c.executemany('INSERT INTO demand_history(source_id,material_id,month,sales,promotion,unit_price,historical_incoming,sheet,source_row) VALUES (?,?,?,?,?,?,?,?,?)',[(source_id,*r) for r in records])
    return {'source_id':source_id,'rows':len(records),'materials':len({r[0] for r in records}),'range':[min(r[1] for r in records),max(r[1] for r in records)]}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('path');parser.add_argument('--price-kind',choices=['purchase','sale','unknown'],default='unknown')
    args=parser.parse_args();print(json.dumps(import_history(args.path,args.price_kind),ensure_ascii=False))
