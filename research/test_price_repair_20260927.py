"""Offline behavioral regression checks; no external API calls."""
import pathlib
import sys
import sqlite3
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pandas as pd
from us_price_repair import baseline, gaps, missing, repair
from us_ohlcv_collector import store, sub_frame


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:')
        self.con.execute('CREATE TABLE daily_ohlcv(symbol TEXT,date TEXT,open REAL,high REAL,low REAL,close REAL,adj_close REAL,volume INTEGER,PRIMARY KEY(symbol,date))')
        for day, symbols in [('20260921', ['A','B']), ('20260922',['A']), ('20260923',['A','B','NEW'])]:
            for symbol in symbols:
                self.con.execute('INSERT INTO daily_ohlcv VALUES (?,?,10,10,10,10,10,100)', (symbol, day))

    def tearDown(self):
        self.con.close()

    def test_baseline_and_past_gap(self):
        self.assertEqual(baseline({'20260921':6567,'20260922':843,'20260923':6564}, '20260923'),6567)
        self.assertEqual(gaps(self.con), [('20260922',1,2)])

    def test_missing_excludes_new_listing(self):
        self.assertEqual(missing(self.con,'20260922'), (['B'],2))

    def test_repair_target_only_and_idempotence(self):
        calls=[]
        def fetch(symbols, **kw):
            calls.append((symbols,kw))
            return pd.DataFrame({'Open':[20,99], 'High':[20,99], 'Low':[20,99], 'Close':[20,99], 'Adj Close':[20,99], 'Volume':[100,100]},index=pd.to_datetime(['20260922','20260923']))
        self.assertTrue(repair(self.con,'20260922',fetch,store,sub_frame,pause=lambda _:None))
        self.assertEqual(calls,[(['B'],{'start':'2026-09-22','end':'2026-09-23'})])
        self.assertEqual(self.con.execute("SELECT close FROM daily_ohlcv WHERE symbol='B' AND date='20260923'").fetchone()[0],10)
        self.assertTrue(repair(self.con,'20260922',fetch,store,sub_frame,pause=lambda _:None))
        self.assertEqual(len(calls),1)

    def test_empty_response_not_success(self):
        self.assertFalse(repair(self.con,'20260922',lambda *a,**k:pd.DataFrame(),store,sub_frame,pause=lambda _:None))

    def test_fetch_error_propagates(self):
        def fail(*a,**kw):
            raise RuntimeError('network')
        with self.assertRaises(RuntimeError):
            repair(self.con,'20260922',fail,store,sub_frame,pause=lambda _:None)


if __name__ == '__main__':
    unittest.main()
