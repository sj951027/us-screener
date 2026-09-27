"""v21 회귀 테스트 — Codex 검토(research/REVIEW_v11_v19_20260927.md)의 경계 사례 7건이 고쳐졌는지(네트워크 0).
재현 스크립트 research/review_v19_probes_20260927.py 는 '결함이 있음'을 assert 하므로 이 파일이 그 반대 기대값을 담는다.
`python research/test_review_fixes_20260927.py`"""
import contextlib
import io
import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import us_calendar as cal  # noqa: E402
import us_integrity as integrity  # noqa: E402
import us_notify_test as notify  # noqa: E402
import us_page_data as page  # noqa: E402
import us_price_repair as price  # noqa: E402


def mem(days):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE daily_ohlcv(symbol TEXT,date TEXT,close REAL)")
    for d, n in days.items():
        c.executemany("INSERT INTO daily_ohlcv VALUES (?,?,10)", [(str(i), d) for i in range(n)])
    return c


def synth(root, last_n=6, periods=270):
    """페이지·알림 계산이 돌아가는 최소 합성 DB(270 거래일, 100종목, 마지막 날 last_n 종목)."""
    db = root / "us_ohlcv.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE daily_ohlcv(symbol TEXT,date TEXT,close REAL,adj_close REAL,volume REAL)")
    dates = pd.bdate_range(end="2026-09-22", periods=periods).strftime("%Y%m%d").tolist()
    rows = []
    for i, d in enumerate(dates):
        for s in range(last_n if i == periods - 1 else 100):
            v = 20 + i * .01 * (1 + s / 25) + s * .05 + np.sin(i + s) * .5
            rows.append((f"S{s:03}", d, v, v, 1e6))
    c.executemany("INSERT INTO daily_ohlcv VALUES (?,?,?,?,?)", rows)
    c.execute("CREATE TABLE score_daily(model TEXT NOT NULL, date TEXT NOT NULL, symbol TEXT NOT NULL, rank INTEGER, score REAL, "
              "mom12 REAL, upratio63 REAL, size_amt REAL, PRIMARY KEY (model, date, symbol))")   # 운영 스키마와 동일
    c.commit()
    return db, dates, c


class ReviewFixes(unittest.TestCase):
    def test_1_zero_row_session_detected(self):
        c = mem({"20260921": 100, "20260923": 100})
        idx = {"20260921", "20260922", "20260923"}
        td, cnt = cal.trading_dates(c, index_dates=idx)
        self.assertEqual(cal.missing_sessions(cnt, idx), ["20260922"])
        self.assertIn(("20260922", 0, 100), price.gaps(c, cnt, idx))

    def test_3_index_gap_keeps_full_session(self):
        c = mem({"20260921": 100, "20260922": 100, "20260923": 100})
        td, _ = cal.trading_dates(c, index_dates={"20260921", "20260923"})
        self.assertIn("20260922", td)

    def test_3_holiday_stray_still_excluded(self):
        c = mem({"20260904": 100, "20260907": 1, "20260908": 100})
        td, _ = cal.trading_dates(c, index_dates={"20260904", "20260908"})
        self.assertNotIn("20260907", td)

    def test_4_notify_uses_same_session_as_page(self):
        with tempfile.TemporaryDirectory() as f:
            root = pathlib.Path(f)
            db, dates, c = synth(root)
            c.execute("INSERT INTO score_daily (model,date,symbol,rank,score) VALUES ('us_mus_v0',?,'S000',1,3)", (dates[-2],))
            c.commit(); c.close()
            with patch.object(notify, "OHLCV_DB", db), patch.object(notify, "SEED_DB", root / "absent.db"), \
                    patch.object(notify, "index_dates_for", return_value=set(dates)), \
                    patch.object(notify, "db_health", return_value=("", [])):
                _, day, partial = notify.build_message()
            self.assertEqual(day, dates[-1])      # 페이지와 같은 날(6% 부분 수집일)
            self.assertTrue(partial)               # 순위 대신 부분 수집 경고

    def test_1_notify_zero_row_latest_is_partial(self):
        with tempfile.TemporaryDirectory() as f:
            root = pathlib.Path(f)
            db, dates, c = synth(root, last_n=100)
            c.close()
            nxt = (pd.Timestamp(dates[-1]) + pd.offsets.BDay(1)).strftime("%Y%m%d")   # 지수엔 있고 시세는 0행
            with patch.object(notify, "OHLCV_DB", db), patch.object(notify, "SEED_DB", root / "absent.db"), \
                    patch.object(notify, "index_dates_for", return_value=set(dates) | {nxt}), \
                    patch.object(notify, "db_health", return_value=("", [])):
                _, day, partial = notify.build_message()
            self.assertEqual((day, partial), (nxt, True))

    def _restore(self, stored, extra_env=True):
        """점수는 저장돼 있고 페이지 CSV 가 없는 상태에서 main() — 복원된 CSV(순위·종목)와 score_daily 행수 전후."""
        with tempfile.TemporaryDirectory() as f:
            root = pathlib.Path(f)
            db, dates, c = synth(root, last_n=100)
            c.executemany("INSERT INTO score_daily (model,date,symbol,rank,score) VALUES ('us_mus_v0',?,?,?,1)", [(dates[-1], sym, r) for sym, r in stored])
            c.commit()
            before = c.execute("SELECT COUNT(*) FROM score_daily").fetchone()[0]
            c.close()
            out = root / "docs/data/us_latest.csv"
            out.parent.mkdir(parents=True)
            env = {"GITHUB_ACTIONS": "true"} if extra_env else {}
            with patch.dict("os.environ", env), patch.object(page, "OHLCV_DB", db), patch.object(page, "OUT", out),                     patch.object(page, "HERE", root), patch.object(page, "HISTORY_OUT", root / "docs/data/us_history.json"),                     patch.object(page, "index_dates_for", return_value=set(dates)), contextlib.redirect_stdout(io.StringIO()) as log:
                page.main()
            c = sqlite3.connect(db)
            after = c.execute("SELECT COUNT(*) FROM score_daily").fetchone()[0]
            c.close()
            csv = pd.read_csv(out, encoding="utf-8-sig") if out.exists() else None
            return csv, before, after, dates, log.getvalue()

    def test_5_existing_score_missing_csv_recreated(self):
        # 저장 순위 = S000 이 1위(자연 재계산은 기울기가 큰 S099 가 위) — CSV 가 저장 순위를 따라야 한다
        stored = [(f"S{s:03}", s + 1) for s in range(100)]
        csv, before, after, dates, _ = self._restore(stored)
        self.assertIsNotNone(csv)
        self.assertEqual(str(csv["date"].iloc[0]), dates[-1])
        self.assertEqual(list(csv["symbol"][:3]), ["S000", "S001", "S002"])
        self.assertEqual(list(csv["rank"][:3]), [1, 2, 3])
        self.assertEqual(before, after)   # 기록은 그대로

    def test_5b_restore_follows_stored_rank_when_sets_differ(self):
        stored = [("ZZZ", 1)] + [(f"S{s:03}", s + 2) for s in range(100)]   # 재계산에 없는 종목이 1위로 저장돼 있음
        csv, _, _, _, log = self._restore(stored)
        self.assertNotIn("ZZZ", set(csv["symbol"]))
        self.assertEqual(list(csv["rank"][:2]), [2, 3])   # 저장된 순위 번호 그대로(비어도)
        self.assertIn("공통", log)

    def test_5c_local_run_does_not_overwrite_page(self):
        csv, _, _, _, log = self._restore([(f"S{s:03}", s + 1) for s in range(100)], extra_env=False)
        self.assertIsNone(csv)
        self.assertIn("로컬 실행", log)

    def test_2b_indmom_only_backfill(self):
        with tempfile.TemporaryDirectory() as f:
            root = pathlib.Path(f)
            db, dates, c = synth(root, last_n=100)
            c.execute("CREATE TABLE sector_cache(symbol TEXT PRIMARY KEY, sector TEXT, industry TEXT, updated TEXT)")
            c.executemany("INSERT INTO sector_cache VALUES (?,?,?,?)",
                          [(f"S{s:03}", "Tech", f"Ind{s // 20}", "") for s in range(100)])
            d = dates[-2]
            c.executemany("INSERT INTO score_daily (model,date,symbol,rank,score) VALUES ('us_mus_v0',?,?,?,1)", [(d, f"S{s:03}", s + 1) for s in range(100)])
            c.commit(); c.close()
            with patch.object(page, "OHLCV_DB", db), patch.object(page, "HERE", root), patch.object(page, "IND_START", dates[0]),                     patch.object(page, "index_dates_for", return_value=set(dates)), contextlib.redirect_stdout(io.StringIO()):
                self.assertIn(d, page.pending_catchup())
                page.main(asof=d, catchup=True)
            c = sqlite3.connect(db)
            n_ind = c.execute("SELECT COUNT(*) FROM score_daily WHERE model='us_mus_v1_ind' AND date=?", (d,)).fetchone()[0]
            n_v0 = c.execute("SELECT COUNT(*) FROM score_daily WHERE model='us_mus_v0' AND date=?", (d,)).fetchone()[0]
            c.close()
            self.assertGreater(n_ind, 0)       # indmom 만 채워짐
            self.assertEqual(n_v0, 100)        # v0 는 손대지 않음

    def test_2_repair_exempts_only_listed_dates(self):
        with tempfile.TemporaryDirectory() as f:
            root = pathlib.Path(f)
            c = sqlite3.connect(root / "us_ohlcv.db")
            c.execute("CREATE TABLE score_daily(model TEXT,date TEXT,symbol TEXT)")
            c.executemany("INSERT INTO score_daily (model,date,symbol) VALUES ('us_mus_v0',?,?)",
                          [(d, f"S{i}") for d in ("20260921", "20260922") for i in range(5)])
            c.commit()
            snap = root / "before.json"
            snap.write_text(json.dumps(integrity.counts(root)), encoding="utf-8")
            c.execute("DELETE FROM score_daily")
            c.commit(); c.close()
            with patch.object(integrity, "DATA_DIR", root), patch.dict("os.environ", {"REPAIR_DATES": "20260922"}), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = integrity.main(["check", str(snap)])
            self.assertEqual(code, 1)

    def test_6_self_test_failure_propagates(self):
        probe = ('import ast,pathlib,sys\np=pathlib.Path(sys.argv[1]); tree=ast.parse(p.read_text(encoding="utf-8"))\n'
                 'f=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="self_test")\n'
                 'f.body=ast.parse("raise SystemExit(1)").body\n'
                 'ast.fix_missing_locations(tree); sys.argv=[str(p),"--self-test"]\n'
                 'exec(compile(tree,str(p),"exec"),{"__name__":"__main__","__file__":str(p)})\n')
        child = subprocess.run([sys.executable, "-c", probe, str(ROOT / "us_ohlcv_collector.py")], capture_output=True, text=True)
        self.assertNotEqual(child.returncode, 0)

    def test_7_failure_alert_covers_soft_steps(self):
        # PyYAML 없이(CI 의 requirements 에 없음) 텍스트로 스텝을 나눠 검사
        text = (ROOT / ".github/workflows/collect-data.yml").read_text(encoding="utf-8")
        steps = text.split("\n      - name:")
        alert = next(b for b in steps if b.lstrip().startswith("Failure alert"))
        import re
        soft = [m.group(1) for b in steps if "continue-on-error: true" in b
                for m in [re.search(r"\n\s+id: (\w+)", b)] if m and m.group(1) != "price_repair"]
        self.assertGreaterEqual(len(soft), 5)
        for sid in soft:   # price_repair 는 'Repair result gate' 가 job 을 실패시키므로 failure() 로 잡힌다
            self.assertIn(f"steps.{sid}.outcome == 'failure'", alert, sid)
        for tag in ("PAGE", "NOTIFY", "ANALYST"):   # 스크립트가 예외를 삼키고 exit 0 인 경우도
            self.assertIn(f"env.SOFT_FAIL_{tag} == '1'", alert, tag)


if __name__ == "__main__":
    unittest.main()
