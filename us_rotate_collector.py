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

from us_ohlcv_collector import STRAY_FRAC   # v16: 휴장일 잔행 판정 기준(수집기와 같은 값)
from us_calendar import session_date as et_today   # v17: 시세 DB 가 비었을 때의 대체 날짜(공용 세션 규칙)

ROTATE_MAX_STALL = 3   # v16: 배치 전부 실패가 이 횟수 연속이면 그 구간을 건너뛰고 전진(순환 전체가 멈추지 않게)
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
    # 섹터 캐시(2026-07-12) — 페이지 판단축용. 섹터는 거의 안 변하므로 캐시에 없는
    # 심볼만 1회 조회(첫 바퀴 ~12일에 채워짐, 이후 요청 0 수렴). 점수 미포함 관측.
    """CREATE TABLE IF NOT EXISTS sector_cache (
        symbol TEXT PRIMARY KEY, sector TEXT, industry TEXT, updated TEXT)""",
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


def session_anchor(con):
    """v16: 스냅샷 날짜 = 시세 DB 의 최근 미국 거래일. CLAUDE.md '미국 거래일(ET) 앵커' — 주말·휴장일 수동 실행이
    비거래일 라벨을 찍지 않게(실측 09-27 일요일 러너 #68 이 20260926(토) 에 542행). 휴장일 잔행(1~2행)은 건너뛴다."""
    since = (dt.date.today() - dt.timedelta(days=14)).strftime("%Y%m%d")
    try:
        counts = con.execute("SELECT date, COUNT(*) FROM daily_ohlcv WHERE date >= ? GROUP BY date ORDER BY date",
                             (since,)).fetchall()
    except sqlite3.OperationalError:
        counts = []
    if counts:
        top = max(n for _, n in counts)
        for d, n in reversed(counts):
            if n >= STRAY_FRAC * top:
                return d
    return et_today()


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
    today = session_anchor(con)   # v16: 최근 거래일 앵커. 2026-09-17~26 행은 러너 UTC 날짜라 +1일 라벨, 09-26(토) 1,066행 잔존
    print(f"[순환] 위치 {pos}/{len(symbols)} 부터 {len(batch)}종목 "
          f"(전체 한 바퀴 ≈ {len(symbols)//args.batch + 1}일)")

    have_sector = {s for (s,) in con.execute(
        "SELECT symbol FROM sector_cache WHERE sector IS NOT NULL")}
    ok = fail = n_sec = 0
    rows, sec_rows = [], []
    for s in batch:
        try:
            t = yf.Ticker(s)
            fi = t.fast_info
            mc = getattr(fi, "market_cap", None)
            sh = getattr(fi, "shares", None)
            if mc or sh:
                rows.append((s, today,
                             float(mc) if mc else None,
                             float(sh) if sh else None))
                ok += 1
            else:
                fail += 1
            # 섹터: 캐시에 없는 심볼만 1회 조회 (실패는 다음 바퀴 재시도)
            if s not in have_sector:
                try:
                    info = t.info
                    sec = (info.get("sector") or "").strip()
                    ind = (info.get("industry") or "").strip()
                    if sec:
                        sec_rows.append((s, sec, ind, today))
                        n_sec += 1
                except Exception:
                    pass
        except Exception:
            fail += 1
        if (ok + fail) % 100 == 0:
            print(f"  … {ok+fail}/{len(batch)} (성공 {ok} · 섹터 +{n_sec})")
            time.sleep(1.0)
    if rows:
        con.executemany(
            "INSERT OR IGNORE INTO valuation_rotate VALUES (?,?,?,?)", rows)
    if sec_rows:
        con.executemany(
            "INSERT OR REPLACE INTO sector_cache VALUES (?,?,?,?)", sec_rows)
    stall_row = con.execute("SELECT v FROM rotate_state WHERE k='stall'").fetchone()
    stall = stall_row[0] if stall_row else 0
    if batch and ok == 0 and stall + 1 < ROTATE_MAX_STALL:
        # v15: 전부 실패(레이트리밋 등)면 위치를 옮기지 않는다 — 스냅샷은 소급 불가라 그 구간이 한 바퀴 빈다
        con.execute("INSERT OR REPLACE INTO rotate_state VALUES ('stall', ?)", (stall + 1,))
        print(f"⚠️ 배치 {len(batch)}종목 전부 실패 — 순환 위치 {pos} 유지(연속 {stall + 1}/{ROTATE_MAX_STALL}, 다음 실행 재시도)")
    else:
        if batch and ok == 0:   # v16: 연속 상한 도달 — 이 구간만 비우고 나머지 순환은 계속
            print(f"⚠️ {ROTATE_MAX_STALL}회 연속 전부 실패 — 이 구간을 건너뛰고 전진(순환 전체 정지 방지)")
        con.execute("INSERT OR REPLACE INTO rotate_state VALUES ('pos', ?)",
                    ((pos + len(batch)) % len(symbols),))
        con.execute("INSERT OR REPLACE INTO rotate_state VALUES ('stall', 0)")
        if 0 < ok < fail:
            print(f"⚠️ 실패 {fail} > 성공 {ok} — 이 구간 시총 결손 큼(다음 바퀴 재시도)")
    con.commit()
    n, nd = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT symbol) FROM valuation_rotate").fetchone()
    nsec = con.execute("SELECT COUNT(*) FROM sector_cache").fetchone()[0]
    con.close()
    print(f"완료: 성공 {ok} · 실패 {fail}(다음 바퀴 재시도). "
          f"누적 {n:,}행/{nd:,}심볼 · 섹터 캐시 {nsec:,}(+{n_sec}).")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        print(e)
    except Exception as e:
        print(f"❌ 실패(비치명): {e}")
        sys.exit(0)
