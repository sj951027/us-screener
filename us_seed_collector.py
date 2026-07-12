# -*- coding: utf-8 -*-
"""
us_seed_collector.py — 미국판 '씨앗' 수집기 (백필 불가 데이터만, 2026-07-11)
==============================================================================
us-screener repo 의 첫 번째 수집기. "지금 안 찍으면 영영 없는" 데이터만 매일 적재한다.
   시세(OHLCV)·과거 팩터는 yfinance 백필 가능이라 여기서 안 모음 — 본구축(9월) 때 일괄.

지금부터 쌓는 것 (전부 백필 불가):
  1. listing_daily  — 미국 전체 상장 목록(NASDAQ+NYSE/AMEX, ~9천) 일일 스냅샷.
     소스: NASDAQ Trader 공식 심볼 디렉터리(안정적 공개 텍스트, 인증 불필요).
  2. listing_events — 목록 diff: NEW(신규상장)·DISAPPEARED(상폐/이전) — 생존편향 전진 차단.
  3. index_membership — S&P500·NASDAQ100 구성종목 일일 기록(위키피디아) + 편출입 diff.
     lxml 미설치 등으로 실패하면 그 부분만 건너뜀(비치명 단위 분리).

저장: ../us-screener-data/us_seed.db (repo 밖 격리 — 한국판 dh-q7m3k-data 패턴).
원칙: 증분·idempotent(PK date+symbol)·비치명(실패해도 한국판 파이프라인 안 막음)·조회 전용.
사용:
    python us_seed_collector.py            # 오늘(ET 기준) 스냅샷 적재
실행: run_us_seed.bat (매일 아무 때나 1회 — 목록/멤버십 스냅샷이라 시점 민감도 낮음.
정식 시세 수집은 본구축 때 아침 07:30 스케줄로).
"""
import datetime as dt
import io
import os
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
DB = Path(os.environ.get("US_SEED_DB", "").strip()
          or (HERE / ".." / "us-screener-data" / "us_seed.db"))

NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
WIKI = {"SP500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "NDX100": "https://en.wikipedia.org/wiki/Nasdaq-100"}

DDL = [
    """CREATE TABLE IF NOT EXISTS listing_daily (
        date TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT, exchange TEXT,
        etf TEXT, financial_status TEXT, PRIMARY KEY (date, symbol))""",
    """CREATE TABLE IF NOT EXISTS listing_events (
        date TEXT NOT NULL, symbol TEXT NOT NULL, event TEXT NOT NULL,
        exchange TEXT, PRIMARY KEY (date, symbol, event))""",
    """CREATE TABLE IF NOT EXISTS index_membership (
        date TEXT NOT NULL, idx TEXT NOT NULL, symbol TEXT NOT NULL,
        PRIMARY KEY (date, idx, symbol))""",
    """CREATE TABLE IF NOT EXISTS membership_events (
        date TEXT NOT NULL, idx TEXT NOT NULL, symbol TEXT NOT NULL,
        event TEXT NOT NULL, PRIMARY KEY (date, idx, symbol, event))""",
]


def et_today():
    """미국 동부 기준 오늘(YYYYMMDD). zoneinfo 실패 시 UTC-5 근사."""
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York")).strftime("%Y%m%d")
    except Exception:
        return (dt.datetime.utcnow() - dt.timedelta(hours=5)).strftime("%Y%m%d")


def parse_symdir(text, is_nasdaq):
    """NASDAQ Trader 심볼 디렉터리 파싱 → [(symbol,name,exchange,etf,finstat)].
    포맷: 파이프 구분, 첫 줄 헤더, 마지막 'File Creation Time' 푸터. Test Issue=Y 제외."""
    rows = []
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return rows
    header = lines[0].split("|")
    idx = {h: i for i, h in enumerate(header)}
    sym_col = "Symbol" if is_nasdaq else "ACT Symbol"
    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            break
        f = line.split("|")
        if len(f) < len(header):
            continue
        if f[idx.get("Test Issue", 3)].strip() == "Y":
            continue
        symbol = f[idx[sym_col]].strip()
        if not symbol:
            continue
        name = f[idx["Security Name"]].strip()
        etf = f[idx["ETF"]].strip() if "ETF" in idx else ""
        finstat = f[idx["Financial Status"]].strip() if "Financial Status" in idx else ""
        exch = "NASDAQ" if is_nasdaq else \
            {"N": "NYSE", "A": "AMEX", "P": "ARCA", "Z": "BATS", "V": "IEX"}.get(
                f[idx["Exchange"]].strip() if "Exchange" in idx else "", "OTHER")
        rows.append((symbol, name, exch, etf, finstat))
    return rows


def fetch_listings():
    import requests
    out = []
    for url, is_nq in ((NASDAQ_URL, True), (OTHER_URL, False)):
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        out += parse_symdir(r.text, is_nq)
    # 심볼 중복(복수 거래소) 제거 — 첫 항목 유지
    seen, uniq = set(), []
    for row in out:
        if row[0] not in seen:
            seen.add(row[0])
            uniq.append(row)
    return uniq


def fetch_membership():
    """위키피디아에서 S&P500·NDX100 구성종목. 실패 시 빈 dict(비치명)."""
    import requests
    import pandas as pd
    res = {}
    for idx_name, url in WIKI.items():
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            tables = pd.read_html(io.StringIO(r.text))
            syms = None
            for t in tables:
                cols = [str(c).lower() for c in t.columns]
                for cand in ("symbol", "ticker"):
                    if any(cand in c for c in cols):
                        col = t.columns[[cand in c for c in cols].index(True)]
                        s = t[col].astype(str).str.strip()
                        if 80 < len(s) < 600:      # 구성종목 표 크기 검증
                            syms = sorted(set(s))
                            break
                if syms:
                    break
            if syms:
                res[idx_name] = syms
        except Exception as e:
            print(f"  ⚠️ {idx_name} 멤버십 수집 실패 건너뜀: {e}")
    return res


def main():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    for d in DDL:
        con.execute(d)
    today = et_today()

    # 1) 상장 목록 스냅샷 + diff 이벤트
    if con.execute("SELECT 1 FROM listing_daily WHERE date=? LIMIT 1", (today,)).fetchone():
        print(f"listing {today} 이미 적재 — 건너뜀(idempotent).")
    else:
        rows = fetch_listings()
        prev_date = con.execute(
            "SELECT MAX(date) FROM listing_daily WHERE date<?", (today,)).fetchone()[0]
        con.executemany(
            "INSERT OR IGNORE INTO listing_daily VALUES (?,?,?,?,?,?)",
            [(today, *r) for r in rows])
        n_ev = 0
        if prev_date:
            prev = {s: e for s, e in con.execute(
                "SELECT symbol, exchange FROM listing_daily WHERE date=?", (prev_date,))}
            now = {r[0]: r[2] for r in rows}
            ev = [(today, s, "DISAPPEARED", prev[s]) for s in prev.keys() - now.keys()]
            ev += [(today, s, "NEW", now[s]) for s in now.keys() - prev.keys()]
            cur = con.executemany(
                "INSERT OR IGNORE INTO listing_events VALUES (?,?,?,?)", ev)
            n_ev = cur.rowcount
        con.commit()
        print(f"  ✓ listing {today}: {len(rows)}종목 스냅샷, 이벤트 {n_ev}건")

    # 2) 지수 멤버십 + diff (실패해도 listing은 이미 저장됨)
    mem = fetch_membership()
    for idx_name, syms in mem.items():
        if con.execute("SELECT 1 FROM index_membership WHERE date=? AND idx=? LIMIT 1",
                       (today, idx_name)).fetchone():
            print(f"  {idx_name} {today} 이미 적재 — 건너뜀.")
            continue
        prev_date = con.execute(
            "SELECT MAX(date) FROM index_membership WHERE idx=? AND date<?",
            (idx_name, today)).fetchone()[0]
        con.executemany("INSERT OR IGNORE INTO index_membership VALUES (?,?,?)",
                        [(today, idx_name, s) for s in syms])
        n_ev = 0
        if prev_date:
            prev = {s for (s,) in con.execute(
                "SELECT symbol FROM index_membership WHERE date=? AND idx=?",
                (prev_date, idx_name))}
            now = set(syms)
            ev = [(today, idx_name, s, "REMOVED") for s in prev - now]
            ev += [(today, idx_name, s, "ADDED") for s in now - prev]
            cur = con.executemany(
                "INSERT OR IGNORE INTO membership_events VALUES (?,?,?,?)", ev)
            n_ev = cur.rowcount
        con.commit()
        print(f"  ✓ {idx_name} {today}: {len(syms)}종목, 편출입 {n_ev}건")

    n = con.execute("SELECT COUNT(DISTINCT date) FROM listing_daily").fetchone()[0]
    con.close()
    print(f"완료: us_seed.db 누적 {n}일. (9월 us-screener 독립 시 이사)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ 실패(비치명 — 파이프라인 계속): {e}")
        sys.exit(0)
