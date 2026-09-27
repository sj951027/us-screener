"""Offline review reproductions at 9b61ae2; intentionally confirm defects.
No network, production DB writes, collector runs, or Telegram sends.
"""
import contextlib
import io
import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import us_calendar as cal
import us_integrity as integrity
import us_notify_test as notify
import us_page_data as page
import us_price_repair as price


def run():
    result = {}
    c = sqlite3.connect(':memory:')
    c.execute('CREATE TABLE daily_ohlcv(symbol TEXT,date TEXT,close REAL)')
    c.executemany('INSERT INTO daily_ohlcv VALUES (?,?,10)',
                  [(str(i),d) for d in ('20260921','20260923') for i in range(100)])
    td, cnt = cal.trading_dates(c, index_dates={'20260921','20260922','20260923'})
    result['zero_row_session'] = {'trading_dates':td,'audit_gaps':price.gaps(c)}
    assert '20260922' not in td and not price.gaps(c)
    c.executemany('INSERT INTO daily_ohlcv VALUES (?, ?, 10)',[(str(i),'20260922') for i in range(100)])
    td, _ = cal.trading_dates(c, index_dates={'20260921','20260923'})
    result['missing_spx_hides_full_session'] = td
    assert '20260922' not in td
    c.close()

    with tempfile.TemporaryDirectory(prefix='us_v19_review_') as folder:
        root = pathlib.Path(folder)
        db = root/'us_ohlcv.db'
        c = sqlite3.connect(db)
        c.execute('CREATE TABLE daily_ohlcv(symbol TEXT,date TEXT,close REAL,adj_close REAL,volume REAL)')
        dates = pd.bdate_range(end='2026-09-22', periods=270).strftime('%Y%m%d').tolist()
        rows = []
        for i,d in enumerate(dates):
            for s in range(6 if i==269 else 100):
                value=20+i*.01+s*.05+np.sin(i)*.5
                rows.append((f'S{s:03}',d,value,value,1e6))
        c.executemany('INSERT INTO daily_ohlcv VALUES (?,?,?,?,?)',rows)
        c.execute('CREATE TABLE score_daily(model TEXT,date TEXT,symbol TEXT,rank INTEGER,score REAL)')
        c.execute("INSERT INTO score_daily VALUES ('us_mus_v0',?,'S000',1,3)",(dates[-2],))
        c.commit()
        with patch.object(notify,'OHLCV_DB',db), patch.object(notify,'SEED_DB',root/'absent.db'), patch.object(notify,'db_health',return_value=('',[])):
            _, day, partial = notify.build_message()
        page_day = cal.trading_dates(c,index_dates=set(dates))[0][-1]
        result['notify_calendar_mismatch']={'page_date':page_day,'notify_date':day,'notify_partial':partial}
        assert page_day==dates[-1] and day==dates[-2] and not partial

        # A prior successful DB upload and failed CSV push leaves an existing score but missing/stale CSV.
        c.executemany('INSERT INTO daily_ohlcv VALUES (?,?,?,?,?)',
                      [(f'S{s:03}',dates[-1],30,30,1e6) for s in range(6,100)])
        c.execute("INSERT INTO score_daily VALUES ('us_mus_v0',?,'S000',1,3)",(dates[-1],))
        c.commit()
        output=root/'missing_latest.csv'
        with patch.object(page,'OHLCV_DB',db),patch.object(page,'OUT',output),patch.object(page,'index_dates_for',return_value=set(dates)),contextlib.redirect_stdout(io.StringIO()):
            page.main()
        result['existing_score_missing_csv']={'csv_recreated':output.exists()}
        assert not output.exists()

        snapshot=root/'before.json'
        snapshot.write_text(json.dumps(integrity.counts(root)),encoding='utf-8')
        c.execute('DELETE FROM score_daily')
        c.commit()
        c.close()
        with patch.object(integrity,'DATA_DIR',root),patch.dict('os.environ',{'REPAIR_DATES':'20260922'}),contextlib.redirect_stdout(io.StringIO()):
            code=integrity.main(['check',str(snapshot)])
        result['repair_exempts_entire_score_table']={'gate_exit':code,'score_rows':0}
        assert code==0
        # quick_check currently leaves its local connection for garbage collection.
        import gc
        gc.collect()

    probe = '''import ast,pathlib,sys
p=pathlib.Path(sys.argv[1]); tree=ast.parse(p.read_text(encoding="utf-8"))
f=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="self_test")
f.body=ast.parse("raise SystemExit(1)").body
ast.fix_missing_locations(tree); sys.argv=[str(p),"--self-test"]
exec(compile(tree,str(p),"exec"),{"__name__":"__main__","__file__":str(p)})
'''
    child=subprocess.run([sys.executable,'-c',probe,str(ROOT/'us_ohlcv_collector.py')],capture_output=True,text=True)
    result['forced_self_test_failure']={'process_exit':child.returncode,'stdout':child.stdout.strip()}
    assert child.returncode==0
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    run()
