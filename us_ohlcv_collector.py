# -*- coding: utf-8 -*-
"""
us_ohlcv_collector.py — 미국 전체 상장 일봉 수집 (백필 + 일일 증분)
==============================================================================
설계(US_SCREENER_DESIGN.md §2)의 1단계 데이터 토대를 앞당겨 가동(2026-07-12 결정).
이유: yfinance 는 비공식 소스라 소스 리스크 헤지 + 수집기 실전 검증을 미리.

유니버스: us_seed.db 의 최신 listing_daily 에서 ETF 제외 보통주(~7천).
저장: ../us-screener-data/us_ohlcv.db `daily_ohlcv`
  (symbol, date, open, high, low, close, adj_close, volume) PK(symbol,date)
  - close = 비조정 종가, adj_close = 분할·배당 조정 종가 (auto_adjust=False)
  - 조정계수 = adj_close/close 로 파생 가능 — 분할 감지용

모드:
  python us_ohlcv_collector.py --backfill   # 3년 백필. **재개 가능** — 중단돼도
                                            #   다시 실행하면 안 받은 심볼만 이어받음.
                                            #   rate limit 시 여러 번 나눠 실행.
  python us_ohlcv_collector.py              # 일일 증분(최근 7일 창, 중복 IGNORE)

원칙: 증분·idempotent·비치명(개별 심볼 실패는 건너뛰고 pending — 다음 실행이 재시도).
⚠️ 네트워크(yfinance) 필요: pip install yfinance
"""
import argparse
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
SEED_DB = DATA_DIR / "us_seed.db"
OHLCV_DB = DATA_DIR / "us_ohlcv.db"
BACKFILL_YEARS = 3
CHUNK = 50            # yf.download 배치 크기 (보수적 — rate limit 대비)
SLEEP_BETWEEN = 1.0   # 배치 간 대기(초)

DDL = [
    """CREATE TABLE IF NOT EXISTS daily_ohlcv (
        symbol TEXT NOT NULL, date TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL, adj_close REAL, volume INTEGER,
        PRIMARY KEY (symbol, date))""",
    """CREATE TABLE IF NOT EXISTS backfill_done (
        symbol TEXT PRIMARY KEY, done_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_us_ohlcv_date ON daily_ohlcv(date)",
]


def load_symbols():
    """us_seed.db 최신 상장 목록에서 ETF 제외 심볼. (us_seed_collector 선행 필요)"""
    if not SEED_DB.exists():
        raise SystemExit(f"us_seed.db 없음({SEED_DB}) — 먼저 python us_seed_collector.py")
    con = sqlite3.connect(f"file:{SEED_DB}?mode=ro", uri=True)
    last = con.execute("SELECT MAX(date) FROM listing_daily").fetchone()[0]
    syms = [s for (s,) in con.execute(
        "SELECT symbol FROM listing_daily WHERE date=? AND (etf IS NULL OR etf!='Y')",
        (last,))]
    con.close()
    # yfinance 표기: 우선주 등 '$'·'.' 계열 → '-' (예: BRK.B → BRK-B)
    return sorted({s.replace(".", "-").replace("$", "-P") for s in syms if s.isascii()})


def store(con, df, symbol):
    """yf.download 단일심볼 DataFrame → INSERT OR IGNORE. 반환: 삽입 행수."""
    if df is None or df.empty:
        return 0
    rows = []
    for idx, r in df.iterrows():
        try:
            c = float(r["Close"]) if r["Close"] == r["Close"] else None
            if c is None:
                continue
            rows.append((symbol, idx.strftime("%Y%m%d"),
                         float(r["Open"]), float(r["High"]), float(r["Low"]), c,
                         float(r["Adj Close"]) if "Adj Close" in df.columns else c,
                         int(r["Volume"]) if r["Volume"] == r["Volume"] else 0))
        except Exception:
            continue
    if not rows:
        return 0
    cur = con.executemany(
        "INSERT OR IGNORE INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?)", rows)
    return cur.rowcount


def fetch_chunk(symbols, start=None, period=None):
    import yfinance as yf
    kw = dict(interval="1d", auto_adjust=False, actions=False,
              group_by="ticker", threads=True, progress=False)
    if period:
        kw["period"] = period
    else:
        kw["start"] = start
    return yf.download(symbols, **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true", help="3년 백필(재개 가능)")
    ap.add_argument("--limit", type=int, default=0, help="이번 실행 최대 심볼 수(테스트/분할용)")
    args = ap.parse_args()
    try:
        import yfinance  # noqa: F401
    except ImportError:
        print("⚠️ yfinance 없음 — pip install yfinance  (비치명 종료)")
        return
    import pandas as pd  # noqa: F401

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(OHLCV_DB)
    for d in DDL:
        con.execute(d)
    symbols = load_symbols()
    print(f"[유니버스] ETF 제외 {len(symbols)}심볼 (us_seed 최신 목록)")

    if args.backfill:
        done = {s for (s,) in con.execute("SELECT symbol FROM backfill_done")}
        todo = [s for s in symbols if s not in done]
        if args.limit:
            todo = todo[:args.limit]
        print(f"[백필] 남은 {len(todo)}심볼 (완료 {len(done)}). 중단돼도 재실행하면 이어받음.")
        start = (dt.date.today() - dt.timedelta(days=365 * BACKFILL_YEARS)).isoformat()
        total = 0
        for i in range(0, len(todo), CHUNK):
            chunk = todo[i:i + CHUNK]
            try:
                df = fetch_chunk(chunk, start=start)
            except Exception as e:
                print(f"  ⚠️ 배치 {i//CHUNK} 실패(다음 실행 때 재시도): {e}")
                time.sleep(10)
                continue
            n = 0
            for s in chunk:
                try:
                    sub = df[s].dropna(how="all") if len(chunk) > 1 else df
                    n += store(con, sub, s)
                    con.execute("INSERT OR REPLACE INTO backfill_done VALUES (?,?)",
                                (s, dt.datetime.now().isoformat(timespec='seconds')))
                except Exception:
                    pass  # 미기록 → 다음 실행 재시도
            con.commit()
            total += n
            print(f"  {min(i+CHUNK, len(todo))}/{len(todo)} … +{n}행 (누적 {total})")
            time.sleep(SLEEP_BETWEEN)
        print(f"백필 배치 종료: 이번 실행 {total}행.")
    else:
        # 일일 증분: 최근 7일 창(휴장·누락 자동 보완, 중복 IGNORE)
        total = 0
        for i in range(0, len(symbols), CHUNK):
            chunk = symbols[i:i + CHUNK]
            try:
                df = fetch_chunk(chunk, period="7d")
            except Exception as e:
                print(f"  ⚠️ 배치 {i//CHUNK} 실패 건너뜀: {e}")
                time.sleep(10)
                continue
            for s in chunk:
                try:
                    sub = df[s].dropna(how="all") if len(chunk) > 1 else df
                    total += store(con, sub, s)
                except Exception:
                    pass
            con.commit()
            time.sleep(SLEEP_BETWEEN)
        print(f"증분 완료: 신규 {total}행.")

    n, d1, d2 = con.execute(
        "SELECT COUNT(*), MIN(date), MAX(date) FROM daily_ohlcv").fetchone()
    nb = con.execute("SELECT COUNT(*) FROM backfill_done").fetchone()[0]
    con.close()
    print(f"누적: {n:,}행 ({d1}~{d2}) · 백필 완료 {nb}심볼")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        print(e)
    except Exception as e:
        print(f"❌ 실패(비치명): {e}")
        sys.exit(0)
