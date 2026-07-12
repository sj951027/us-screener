# -*- coding: utf-8 -*-
"""
us_market_series.py — 시장 레벨 시계열 → us_market.db `market_daily`
==============================================================================
베타/노출/레짐 레이어의 재료(설계 §2). 백필 가능하지만 비용이 미미해 지금부터 수집.
시리즈: S&P500·NASDAQ100·나스닥종합·VIX·달러인덱스·미10년물·원달러.
최초 실행 = 3년 백필, 이후 = 증분(최근 14일 창, 중복 IGNORE). 비치명.
사용: python us_market_series.py   (run_us_seed.bat 가 매일 호출)
"""
import datetime as dt
import os
import sqlite3
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("US_DATA_DIR", "").strip() or (HERE / ".." / "us-screener-data"))
DB = DATA_DIR / "us_market.db"

SERIES = {"SPX": "^GSPC", "NDX": "^NDX", "COMP": "^IXIC", "VIX": "^VIX",
          "DXY": "DX-Y.NYB", "US10Y": "^TNX", "USDKRW": "KRW=X"}

DDL = """CREATE TABLE IF NOT EXISTS market_daily (
    series TEXT NOT NULL, date TEXT NOT NULL, close REAL,
    PRIMARY KEY (series, date))"""


def main():
    try:
        import yfinance as yf
    except ImportError:
        print("⚠️ yfinance 없음 — 생략(비치명). pip install yfinance")
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute(DDL)
    total = 0
    for name, code in SERIES.items():
        last = con.execute(
            "SELECT MAX(date) FROM market_daily WHERE series=?", (name,)).fetchone()[0]
        kw = {"period": "14d"} if last else \
             {"start": (dt.date.today() - dt.timedelta(days=365 * 3)).isoformat()}
        try:
            df = yf.download(code, interval="1d", auto_adjust=False,
                             progress=False, **kw)
        except Exception as e:
            print(f"  ⚠️ {name}({code}) 실패 건너뜀: {e}")
            continue
        if df is None or df.empty:
            print(f"  ⚠️ {name}({code}) 데이터 없음 — 건너뜀")
            continue
        close = df["Close"]
        if hasattr(close, "columns"):        # 멀티컬럼 방어(yf 버전차)
            close = close.iloc[:, 0]
        rows = [(name, idx.strftime("%Y%m%d"), float(v))
                for idx, v in close.dropna().items()]
        cur = con.executemany(
            "INSERT OR IGNORE INTO market_daily VALUES (?,?,?)", rows)
        con.commit()
        total += cur.rowcount
        print(f"  ✓ {name}: 신규 {cur.rowcount}행 (~{rows[-1][1] if rows else '-'})")
        time.sleep(0.5)
    n = con.execute(
        "SELECT series, COUNT(*) FROM market_daily GROUP BY series").fetchall()
    con.close()
    print(f"완료: 신규 {total}행. 누적: " + " · ".join(f"{s} {c}" for s, c in n))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ 실패(비치명): {e}")
        sys.exit(0)
