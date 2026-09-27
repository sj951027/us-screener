"""us_calendar 오프라인 회귀 테스트(v17~v19) — 네트워크 0. `python research/test_calendar_20260927.py`"""
import datetime as dt
import pathlib
import sqlite3
import sys
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import us_calendar as cal  # noqa: E402

ET = ZoneInfo("America/New_York")


def frozen(et_dt):
    class F(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return et_dt.astimezone(tz) if tz else et_dt.replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return et_dt.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return mock.patch.object(cal.dt, "datetime", F)


class CalendarTests(unittest.TestCase):
    def db(self, days):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE daily_ohlcv(symbol TEXT, date TEXT, close REAL)")
        for d, n in days.items():
            con.executemany("INSERT INTO daily_ohlcv VALUES (?,?,1)", [(f"S{i}", d) for i in range(n)])
        return con

    def test_session_rollover(self):
        with frozen(dt.datetime(2026, 9, 29, 4, 30, tzinfo=ET)):      # 화 04:30 ET = 러너 실제 시작 무렵
            self.assertEqual(cal.session_date(), "20260928")
        with frozen(dt.datetime(2026, 9, 29, 7, 0, tzinfo=ET)):
            self.assertEqual(cal.session_date(), "20260929")

    def test_last_complete_date(self):
        with frozen(dt.datetime(2026, 9, 29, 4, 30, tzinfo=ET)):      # 새벽: 어제까지만 완결
            self.assertEqual(cal.last_complete_date(), "20260928")
        with frozen(dt.datetime(2026, 9, 29, 11, 0, tzinfo=ET)):      # 장중: 오늘 봉은 미완성
            self.assertEqual(cal.last_complete_date(), "20260928")
        with frozen(dt.datetime(2026, 9, 29, 17, 0, tzinfo=ET)):      # 마감 뒤: 오늘까지
            self.assertEqual(cal.last_complete_date(), "20260929")

    def test_stray_vs_partial_with_index(self):
        # 실측 재현: 0904 온전 · 0907(노동절) 잔행 1행 · 0922 부분 수집 6%
        days = {"20260904": 100, "20260907": 1, "20260908": 100, "20260921": 100, "20260922": 6}
        con = self.db(days)
        no_idx, _ = cal.trading_dates(con)
        self.assertNotIn("20260922", no_idx)                           # v18 까지: 부분 수집일이 잔행으로 오분류
        idx = {"20260904", "20260908", "20260921", "20260922"}          # SPX 봉: 노동절 없음
        with_idx, _ = cal.trading_dates(con, index_dates=idx)
        self.assertEqual(with_idx, ["20260904", "20260908", "20260921", "20260922"])

    def test_outside_index_range_falls_back(self):
        con = self.db({"20230323": 100, "20230324": 1, "20230327": 100})
        dates, _ = cal.trading_dates(con, index_dates={"20230718"})     # 지수 수집 시작 전 기간 → v09 10% 규칙
        self.assertEqual(dates, ["20230323", "20230327"])

    def test_baseline_and_counts(self):
        con = self.db({"20260921": 100, "20260922": 50})
        con.execute("INSERT INTO daily_ohlcv VALUES ('Z','20260921',0)")  # close=0 은 세지 않음
        cnt = cal.date_counts(con)
        self.assertEqual(cnt, {"20260921": 100, "20260922": 50})
        self.assertEqual(cal.baseline(cnt, "20260922"), 100)
        self.assertEqual(cal.baseline(cnt, "20260921"), 100)          # 앞선 날 없으면 그날 행수


if __name__ == "__main__":
    unittest.main()
