# -*- coding: utf-8 -*-
"""
us_rotate_collector.py — 시총·주식수 '순환 스냅샷' → us_ohlcv.db `valuation_rotate`
==============================================================================
왜: 시총·주식수는 **백필 불가**(yfinance는 현재값만) + 시총 팩터(한국판 big)의 재료.
전 종목 매일은 무리(티커당 1요청)라 **하루 BATCH(기본 600)종목씩 순환** — 전체
~7천 종목을 약 12일에 한 바퀴. 포인트-인-타임 시총 히스토리가 2주 해상도로 쌓인다.
(주가×주식수로 일별 시총 보간은 분석 단계에서 — 주식수는 천천히 변하므로 유효.)

저장: valuation_rotate(symbol, date, market_cap, shares) PK(symbol,date)
상태: rotate_state(k='pos') — 유니버스 내 다음 시작 위치(재실행 안전).
사용: python us_rotate_collector.py [--batch 600]   (run_us_seed.bat 가 매일 호출)
원칙: 심볼별 실패 무시(다음 바퀴 재시도)·idempotent·비치명.
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
DB = DATA_DIR / "us_ohlcv.db"

DDL = [
    """CREATE TABLE IF NOT EXISTS valuation_rotate (
        symbol TEXT NOT NULL, date TEXT NOT NULL,
        market_cap REAL, shares REAL, PRIMARY KEY (symbol, date))""",
    "CREATE TABLE IF NOT EXISTS rotate_state (k TEXT PRIMARY KEY, v INTEGER)",
]


def load_symbols():
    if not SEED_DB.exists():
        raise SystemExit(f"us_seed.db 없음({SEED_DB}) — 먼저 python us_seed_collector.py")
    con = sqlite3.connect(f"file:{SEED_DB}?mode=ro", uri=True)
    last = con.execute("SELECT MAX(date) FROM listing_daily").fetchone()[0]
    rows = con.execute(
        "SELECT symbol, name FROM listing_daily WHERE date=? AND (etf IS NULL OR etf!='Y')",
        (last,)).fetchall()
    con.close()
    BAD = ("WARRANT", " UNIT", "UNITS", " RIGHT", "RIGHTS")  # 야후 시세 없음(실측)
    syms = [s for s, n in rows
            if not any(b in (n or "").upper() for b in BAD)]
    return sorted({s.replace(".", "-").replace("$", "-P") for s in syms if s.isascii()})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=600)
    args = ap.parse_args()
    try:
        import yfinance as yf
    except ImportError:
        print("⚠️ yfinance 없음 — 생략(비치명). pip install yfinance")
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    for d in DDL:
        con.execute(d)
    symbols = load_symbols()
    pos_row = con.execute("SELECT v FROM rotate_state WHERE k='pos'").fetchone()
    pos = pos_row[0] % len(symbols) if pos_row else 0
    batch = [symbols[(pos + i) % len(symbols)] for i in range(min(args.batch, len(symbols)))]
    today = dt.date.today().strftime("%Y%m%d")
    print(f"[순환] 위치 {pos}/{len(symbols)} 부터 {len(batch)}종목 "
          f"(전체 한 바퀴 ≈ {len(symbols)//args.batch + 1}일)")

    ok = fail = 0
    rows = []
    for s in batch:
        try:
            fi = yf.Ticker(s).fast_info
            mc = getattr(fi, "market_cap", None)
            sh = getattr(fi, "shares", None)
            if mc or sh:
                rows.append((s, today,
                             float(mc) if mc else None,
                             float(sh) if sh else None))
                ok += 1
            else:
                fail += 1
        except Exception:
            fail += 1
        if (ok + fail) % 100 == 0:
            print(f"  … {ok+fail}/{len(batch)} (성공 {ok})")
            time.sleep(1.0)
    if rows:
        con.executemany(
            "INSERT OR IGNORE INTO valuation_rotate VALUES (?,?,?,?)", rows)
    con.execute("INSERT OR REPLACE INTO rotate_state VALUES ('pos', ?)",
                ((pos + len(batch)) % len(symbols),))
    con.commit()
    n, nd = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT symbol) FROM valuation_rotate").fetchone()
    con.close()
    print(f"완료: 성공 {ok} · 실패 {fail}(다음 바퀴 재시도). 누적 {n:,}행/{nd:,}심볼.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        print(e)
    except Exception as e:
        print(f"❌ 실패(비치명): {e}")
        sys.exit(0)
