# -*- coding: utf-8 -*-
"""
us_short_collector.py — FINRA 공매도 잔고(격주) → us_ohlcv.db `short_interest`
==============================================================================
소스: https://cdn.finra.org/equity/otcmarket/biweekly/shrt{YYYYMMDD}.csv
  (공식 공개 파일 · 격주 결제일 기준 · 2021-06부터 거래소 상장 포함 · 지연 ~T+8영업일)
왜 지금부터: 과거 아카이브가 있지만 제공 정책은 영구 보장이 아님(Jin 지적) —
  파일 단위로 가볍게 미리 확보. 백필도 같은 URL 패턴으로 가능.

파일 날짜는 '결제일'(대략 매월 15일·말일 부근, 영업일 조정)이라 정확한 날짜를 모름
→ **탐침(probe)**: 후보 날짜(매월 10~18일·24~말일+익월 1~3일)에 GET 을 시도해
  200+CSV 면 적재, 404 면 통과. 파일 존재가 확인된 날짜만 files_done 에 기록.

포맷 방어: 구분자(|,)와 컬럼명을 첫 줄에서 자동 감지. symbol·공매도수량을 못 찾으면
  **추측하지 않고** ../us-screener-data/raw_finra/ 에 원본 저장 + 경고(다음 세션에서 매핑).

사용:
    python us_short_collector.py                      # 최근 35일 창 탐침(일일 증분)
    python us_short_collector.py --backfill-from 2023-06-01   # 과거 일괄(1회)
원칙: idempotent(PK 결제일+심볼)·비치명·조회 전용. run_us_seed.bat 가 매일 호출.
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
DB = DATA_DIR / "us_ohlcv.db"
RAW_DIR = DATA_DIR / "raw_finra"
URL = "https://cdn.finra.org/equity/otcmarket/biweekly/shrt{d}.csv"
UA = {"User-Agent": "Mozilla/5.0 (personal research; contact via github)"}

DDL = [
    """CREATE TABLE IF NOT EXISTS short_interest (
        settlement_date TEXT NOT NULL, symbol TEXT NOT NULL,
        short_qty REAL, avg_daily_vol REAL, days_to_cover REAL, market TEXT,
        PRIMARY KEY (settlement_date, symbol))""",
    "CREATE TABLE IF NOT EXISTS short_files_done (date TEXT PRIMARY KEY, rows INTEGER)",
]


def candidate_dates(start, end):
    """격주 결제일 후보: 매월 10~18일 + 24~말일 + 익월 1~3일 (영업일만)."""
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and (10 <= d.day <= 18 or d.day >= 24 or d.day <= 3):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def detect_columns(header_line):
    """구분자·컬럼 인덱스 자동 감지. 실패 시 None."""
    delim = "|" if header_line.count("|") >= header_line.count(",") else ","
    cols = [c.strip().lower().replace(" ", "").replace("_", "") for c in header_line.split(delim)]
    def find(*keys):
        for i, c in enumerate(cols):
            if any(k in c for k in keys):
                return i
        return None
    m = {
        "symbol": find("symbolcode", "symbol", "ticker"),
        "settle": find("settlementdate", "settledate"),
        "qty": find("currentshortposition", "shortposition", "shortinterest", "shortqty"),
        "adv": find("averagedailyvolume", "avgdailyvolume"),
        "dtc": find("daystocover"),
        "mkt": find("marketclass", "market"),
    }
    if m["symbol"] is None or m["qty"] is None:
        return None, None
    return delim, m


def parse_file(text, fallback_date):
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        return None
    delim, m = detect_columns(lines[0])
    if delim is None:
        return None
    rows = []
    for line in lines[1:]:
        f = line.split(delim)
        if len(f) <= m["qty"]:
            continue
        def g(key, cast=str):
            i = m.get(key)
            if i is None or i >= len(f):
                return None
            v = f[i].strip()
            if v == "":
                return None
            try:
                return cast(v)
            except Exception:
                return None
        sd = g("settle") or fallback_date
        sd = sd.replace("-", "")[:8]
        sym = g("symbol")
        if not sym:
            continue
        rows.append((sd, sym, g("qty", float), g("adv", float),
                     g("dtc", float), g("mkt")))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill-from", default=None, help="예: 2023-06-01 (1회)")
    args = ap.parse_args()
    import requests
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    for d in DDL:
        con.execute(d)
    done = {d for (d,) in con.execute("SELECT date FROM short_files_done")}
    today = dt.date.today()
    if args.backfill_from:
        start = dt.date.fromisoformat(args.backfill_from)
    else:
        start = today - dt.timedelta(days=35)
    cands = [d for d in candidate_dates(start, today)
             if d.strftime("%Y%m%d") not in done]
    print(f"[탐침] {start}~{today} 후보 {len(cands)}일 (이미 확보 {len(done)}파일)")
    got = 0
    for d in cands:
        ds = d.strftime("%Y%m%d")
        try:
            r = requests.get(URL.format(d=ds), headers=UA, timeout=30)
        except Exception as e:
            print(f"  ⚠️ {ds} 요청 실패(다음 실행 재시도): {e}")
            time.sleep(3)
            continue
        if r.status_code != 200 or len(r.content) < 5000:
            time.sleep(0.3)
            continue
        text = r.content.decode("utf-8", errors="replace")
        rows = parse_file(text, ds)
        if rows is None:
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            (RAW_DIR / f"shrt{ds}.csv").write_bytes(r.content)
            print(f"  ⚠️ {ds} 포맷 감지 실패 — raw_finra/ 에 원본 보존(매핑은 수동 확인)")
            continue
        cur = con.executemany(
            "INSERT OR IGNORE INTO short_interest VALUES (?,?,?,?,?,?)", rows)
        con.execute("INSERT OR REPLACE INTO short_files_done VALUES (?,?)",
                    (ds, cur.rowcount))
        con.commit()
        got += 1
        print(f"  ✓ {ds}: {cur.rowcount:,}행")
        time.sleep(1.0)
    n, nd = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT settlement_date) FROM short_interest").fetchone()
    con.close()
    print(f"완료: 신규 {got}파일. 누적 {n:,}행 · {nd}개 결제일.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ 실패(비치명): {e}")
        sys.exit(0)
