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

# v2026-08-09: 직전 ohlcv 수집 직후라 첫 시리즈(SPX)가 YFRateLimitError 로 반복 결손
# (7/30~8/7 Actions 로그 2회 실측). 같은 실행 내 수 초 뒤 다른 시리즈는 성공했으므로
# 짧은 대기 후 1회 재시도로 자기치유한다(ohlcv 의 --retry-empty 와 같은 관용구).
RETRY_WAIT_SEC = 30

DDL = """CREATE TABLE IF NOT EXISTS market_daily (
    series TEXT NOT NULL, date TEXT NOT NULL, close REAL,
    PRIMARY KEY (series, date))"""


def _fetch_one(yf, con, name, code):
    """한 시리즈 수집·적재. 성공=신규 행수, 실패/빈 결과=None (비치명)."""
    last = con.execute(
        "SELECT MAX(date) FROM market_daily WHERE series=?", (name,)).fetchone()[0]
    kw = {"period": "14d"} if last else \
         {"start": (dt.date.today() - dt.timedelta(days=365 * 3)).isoformat()}
    try:
        df = yf.download(code, interval="1d", auto_adjust=False,
                         progress=False, **kw)
    except Exception as e:
        print(f"  ⚠️ {name}({code}) 실패 건너뜀: {e}")
        return None
    if df is None or df.empty:
        print(f"  ⚠️ {name}({code}) 데이터 없음 — 건너뜀")
        return None
    close = df["Close"]
    if hasattr(close, "columns"):        # 멀티컬럼 방어(yf 버전차)
        close = close.iloc[:, 0]
    rows = [(name, idx.strftime("%Y%m%d"), float(v))
            for idx, v in close.dropna().items()]
    cur = con.executemany(
        "INSERT OR IGNORE INTO market_daily VALUES (?,?,?)", rows)
    con.commit()
    print(f"  ✓ {name}: 신규 {cur.rowcount}행 (~{rows[-1][1] if rows else '-'})")
    return cur.rowcount


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
    failed = []
    for name, code in SERIES.items():
        n = _fetch_one(yf, con, name, code)
        if n is None:
            failed.append(name)
        else:
            total += n
        time.sleep(0.5)
    if failed:                            # v2026-08-09 재시도 패스(파일 상단 주석)
        print(f"  ↻ {RETRY_WAIT_SEC}s 대기 후 1회 재시도: {', '.join(failed)}")
        time.sleep(RETRY_WAIT_SEC)
        for name in failed:
            n = _fetch_one(yf, con, name, SERIES[name])
            if n is not None:
                total += n
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
