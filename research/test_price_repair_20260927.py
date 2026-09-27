"""Offline behavioral regression checks; no external API calls."""
import pathlib
import sys
import sqlite3
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pandas as pd
from us_price_repair import anchors, baseline, bulk_gap, gaps, missing, repair, repair_dates
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
        self.assertEqual(repair(self.con,'20260922',fetch,store,sub_frame,pause=lambda _:None),(2,1,[]))
        self.assertEqual(calls,[(['B'],{'start':'2026-09-22','end':'2026-09-23'})])
        self.assertEqual(self.con.execute("SELECT close FROM daily_ohlcv WHERE symbol='B' AND date='20260923'").fetchone()[0],10)
        self.assertEqual(repair(self.con,'20260922',fetch,store,sub_frame,pause=lambda _:None),(2,0,[]))
        self.assertEqual(len(calls),1)

    def test_empty_response_leaves_residual(self):
        # v13: 빈 응답이면 잔여로 남는다(성공/실패 판정은 main 이 비율로 한다)
        expected, added, remaining = repair(self.con,'20260922',lambda *a,**k:pd.DataFrame(),store,sub_frame,pause=lambda _:None)
        self.assertEqual((expected, added, remaining),(2,0,['B']))

    def test_residual_threshold_splits_bulk_from_source_gap(self):
        # v14: 실패선 = 점수 게이트(90%)와 동일. 09-22 잔여 2/6,566 통과 · 부분 수집일 5,498/6,557 실패
        self.assertFalse(bulk_gap(6566, ['PCG-PI','PCG-PG']))          # 실측 09-22 복구 뒤
        self.assertTrue(bulk_gap(6557, ['S%d' % i for i in range(5498)]))  # 실측 부분 수집일
        self.assertFalse(bulk_gap(6566, ['S%d' % i for i in range(600)]))  # 9.1% — 게이트 통과 구간 = 경고
        self.assertTrue(bulk_gap(6566, ['S%d' % i for i in range(700)]))   # 10.7% — 게이트도 막는 구간 = 실패
        self.assertFalse(bulk_gap(1000, ['S%d' % i for i in range(100)]))  # 정확히 10%: 게이트(0.9 ≥ 0.9 통과)와 같은 판정
        self.assertFalse(bulk_gap(0, ['A']))                            # 기준 없음 → 판정 보류

    def test_repair_dates_reports_only_bulk_gaps(self):
        # main 의 판정 루프: 빈 응답이면 잔여 1/2 = 50% → 대량 결손 목록에 오른다
        bulk, counts = repair_dates(self.con, ['20260922'], lambda *a, **k: pd.DataFrame(), store, sub_frame)
        self.assertEqual(bulk, ['20260922 잔여 1/2'])
        self.assertEqual(counts['20260922'], 1)
        # 채워 주면 목록이 비고, 공유 counts 에 복구분이 반영된다
        def fetch(symbols, **kw):
            return pd.DataFrame({'Open':[20], 'High':[20], 'Low':[20], 'Close':[20], 'Adj Close':[20], 'Volume':[100]}, index=pd.to_datetime(['20260922']))
        bulk, counts = repair_dates(self.con, ['20260922'], fetch, store, sub_frame)
        self.assertEqual(bulk, [])
        self.assertEqual(counts['20260922'], 2)

    def test_anchor_skips_stray_and_partial_days(self):
        # v14 실측 재현: 0904 온전 / 0907 잔행 1행 / 0908 부분 / 0909 온전 → 기준은 0904·0909 여야 한다
        for day, symbols in [('20260904', ['A','B']), ('20260907', ['A']), ('20260908', ['A']), ('20260909', ['A','B'])]:
            for symbol in symbols:
                self.con.execute('INSERT INTO daily_ohlcv VALUES (?,?,10,10,10,10,10,100)', (symbol, day))
        self.assertEqual(anchors(self.con, '20260908'), ('20260904', '20260909'))
        self.assertEqual(missing(self.con, '20260908'), (['B'], 2))   # v12 는 기준 1종목·잔여 0 으로 허위 성공
        with self.assertRaises(ValueError):
            anchors(self.con, '20260901')   # 앞쪽에 온전한 날이 없음

    def test_fetch_error_propagates(self):
        def fail(*a,**kw):
            raise RuntimeError('network')
        with self.assertRaises(RuntimeError):
            repair(self.con,'20260922',fail,store,sub_frame,pause=lambda _:None)


if __name__ == '__main__':
    unittest.main()
