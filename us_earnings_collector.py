# -*- coding: utf-8 -*-
"""
us_earnings_collector.py — SEC 제출이력(submissions 벌크) → 실적 발표일 이벤트
==============================================================================
[2026-07-26 신설 · 데이터 확장 2차] PEAD(실적 발표 후 드리프트) 검증의 이벤트 날짜.
어닝 서프라이즈 크기(SUE)는 1차 XBRL(us_fundamentals.db)에서 계산하고, 이 수집기는
"언제 발표했나"를 정밀하게 잡는다. 관측 전용 — 점수 미투입, 검증은 본구축 PREREGISTER 후.

소스: https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip
  (전 filer 제출 이력 벌크, 매일 갱신, ~1.3GB — 1차 companyfacts 와 같은 체계)

무엇을 뽑나:
  - **8-K 중 Item 2.02**(Results of Operations) = 실적 공표 이벤트(미국 표준 관행).
  - 10-K/10-Q 제출일 = 발표일 폴백(8-K 없는 소형주) + 공시지연 연구용.
  - **acceptanceDateTime(접수 시각)** 저장 — 장전/장후 발표 구분이 이벤트 스터디의
    T일 정렬을 좌우한다(장후 발표 반응은 다음 거래일).

포인트-인-타임: (cik, accn) PK append-only. 정정(8-K/A)은 별도 행. filed>=2019.
무게 관리: 주간 가드(6일)·상장 CIK 만 파싱 — 1차와 동일. 비치명(실패=경고 후 exit 0,
  성공 시에만 가드 갱신 → 다음날 재시도).

사용:
    python us_earnings_collector.py               # 주간 가드 하에 벌크 적재
    python us_earnings_collector.py --force       # 가드 무시
    python us_earnings_collector.py --self-test   # 오프라인 파서 검증(네트워크 0)
"""
import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("US_DATA_DIR", "").strip() or (HERE / ".." / "us-screener-data"))
DB = DATA_DIR / "us_fundamentals.db"      # 1차와 같은 DB(재무·이벤트 한 곳)
ZIP_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
UA = {"User-Agent": os.environ.get(
    "SEC_USER_AGENT", "us-screener research (github.com/sj951027; seok5139@gmail.com)")}

GUARD_DAYS = 6
MIN_FILED = "2019-01-01"
FORMS = {"8-K", "8-K/A", "10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "6-K"}

DDL = [
    """CREATE TABLE IF NOT EXISTS earnings_events (
        cik INTEGER NOT NULL, accn TEXT NOT NULL,
        form TEXT, filed TEXT, accepted TEXT, items TEXT,
        report_date TEXT, is_earnings INTEGER,
        PRIMARY KEY (cik, accn))""",
    "CREATE INDEX IF NOT EXISTS ix_earn_cik_filed ON earnings_events(cik, filed)",
    "CREATE TABLE IF NOT EXISTS xbrl_meta (key TEXT PRIMARY KEY, value TEXT)",
]


def _cols(d):
    """submissions JSON 의 두 형태를 흡수: 최근분은 {"filings":{"recent":{...}}},
    페이지 파일(CIK…-submissions-001.json)은 열 배열이 최상위에 그대로 온다."""
    if "filings" in d:
        return (d.get("filings") or {}).get("recent") or {}
    if "form" in d:
        return d
    return {}


def extract_rows(cik, d):
    """한 filer JSON → earnings_events 행 리스트.
    (cik, accn, form, filed, accepted, items, report_date, is_earnings)"""
    c = _cols(d)
    forms = c.get("form") or []
    n = len(forms)

    def col(name):
        v = c.get(name) or []
        return v + [""] * (n - len(v))     # 열 길이 방어(짧으면 빈값 패딩)

    filed = col("filingDate")
    accepted = col("acceptanceDateTime")
    items = col("items")
    accn = col("accessionNumber")
    report = col("reportDate")
    rows = []
    for i in range(n):
        f = forms[i]
        if f not in FORMS or (filed[i] or "") < MIN_FILED or not accn[i]:
            continue
        it = items[i] or ""
        is_earn = 1 if (f.startswith("8-K") and "2.02" in it) else 0
        # 무게 관리: 8-K 는 실적공표(2.02)만 저장 — 그 외 8-K(M&A·기타)는 행수만
        # 불리고 PEAD 목적엔 불필요. (본보고서 10-K/Q 등은 전부 보존)
        if f.startswith("8-K") and not is_earn:
            continue
        rows.append((cik, accn[i], f, filed[i], accepted[i] or "", it,
                     report[i] or "", is_earn))
    return rows


def ensure_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    for ddl in DDL:
        con.execute(ddl)
    con.commit()
    return con


def run_bulk(con, force=False):
    import requests
    last = con.execute(
        "SELECT value FROM xbrl_meta WHERE key='earnings_last_success'").fetchone()
    if last and not force:
        gap = (dt.date.today() - dt.date.fromisoformat(last[0])).days
        if gap < GUARD_DAYS:
            print(f"⏭  실적일 스킵 — 마지막 성공 {last[0]} 이후 {gap}일 < {GUARD_DAYS}일 (주간 가드)")
            return
    # 상장 CIK 만 파싱 — 1차(us_xbrl_collector)가 만든 cik_ticker 재사용. 없으면 전체.
    try:
        ciks = {r[0] for r in con.execute("SELECT DISTINCT cik FROM cik_ticker")}
    except Exception:
        ciks = set()
    if not ciks:
        print("  ⚠️ cik_ticker 비어 있음(1차 미실행?) — 전체 filer 파싱(느림)")

    s = requests.Session()
    # ⚠️ 임시파일은 데이터 폴더 밖(시스템 temp) — 실패 잔재의 tar/Release 오염 방지.
    import tempfile
    tmp = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()) / "submissions.zip.tmp"
    print("▶ submissions.zip 다운로드 (~1.3GB) ...")
    try:
        with s.get(ZIP_URL, headers=UA, timeout=120, stream=True) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        print(f"  다운로드 완료 {tmp.stat().st_size/1e9:.2f}GB")
        _parse_zip(con, tmp, ciks)
    finally:
        tmp.unlink(missing_ok=True)


def _parse_zip(con, tmp, ciks):
    n_parsed = 0
    with zipfile.ZipFile(tmp) as zf:
        names = zf.namelist()
        print(f"  zip 내 파일 {len(names):,}개")
        for name in names:
            if not name.startswith("CIK") or not name.endswith(".json"):
                continue
            base = name[3:].split("-")[0].replace(".json", "")
            try:
                cik = int(base)
            except ValueError:
                continue
            if ciks and cik not in ciks:
                continue
            try:
                rows = extract_rows(cik, json.loads(zf.read(name)))
            except Exception:
                continue           # 개별 filer 파싱 실패는 건너뜀(비치명)
            if rows:
                con.executemany(
                    "INSERT OR IGNORE INTO earnings_events VALUES (?,?,?,?,?,?,?,?)", rows)
                n_parsed += 1
            if n_parsed and n_parsed % 2000 == 0:
                con.commit()
                print(f"  [{n_parsed:,} filer 파싱]")
    con.commit()
    tmp.unlink(missing_ok=True)
    tot, earn = con.execute(
        "SELECT COUNT(*), SUM(is_earnings) FROM earnings_events").fetchone()
    con.execute("INSERT OR REPLACE INTO xbrl_meta VALUES ('earnings_last_success', ?)",
                (dt.date.today().isoformat(),))
    con.commit()
    print(f"💾 earnings_events 누적 {tot:,}행 · 실적공표(8-K 2.02) {earn:,}건")
    print("✅ 실적일 적재 완료 — 관측 전용. 이벤트 정렬은 accepted(접수시각)로 장전/장후 구분.")


FIXTURE_RECENT = {
    "cik": 320193,
    "filings": {"recent": {
        "form": ["8-K", "8-K", "10-Q", "4", "8-K"],
        "filingDate": ["2026-01-30", "2026-03-10", "2026-02-02", "2026-02-05", "2018-05-01"],
        "acceptanceDateTime": ["2026-01-30T21:30:12.000Z", "2026-03-10T13:05:00.000Z",
                               "2026-02-02T16:31:00.000Z", "", ""],
        "items": ["2.02,9.01", "8.01", "", "", "2.02"],
        "accessionNumber": ["a-1", "a-2", "a-3", "a-4", "a-5"],
        "reportDate": ["2025-12-27", "2026-03-09", "2025-12-27", "", ""],
    }},
}
FIXTURE_PAGED = {          # 페이지 파일 형태(열 배열이 최상위)
    "form": ["10-K"],
    "filingDate": ["2025-11-01"],
    "acceptanceDateTime": ["2025-11-01T20:01:00.000Z"],
    "items": [""],
    "accessionNumber": ["b-1"],
    "reportDate": ["2025-09-27"],
}


def self_test():
    print("== self-test (오프라인) ==")
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  {'OK ' if cond else 'FAIL'} {label}")
        ok &= cond

    rows = extract_rows(320193, FIXTURE_RECENT)
    check("Form 4·filed<2019·비실적 8-K 제외 → 2행", len(rows) == 2)
    earn = [r for r in rows if r[7] == 1]
    check("실적공표 판별: 8-K+Item2.02 만 1건", len(earn) == 1 and earn[0][1] == "a-1")
    check("접수시각(장후 21:30 UTC) 보존", earn[0][4].startswith("2026-01-30T21:30"))
    check("비실적 8-K(Item 8.01)는 저장 안 함(무게 관리)",
          not any(r[1] == "a-2" for r in rows))
    rows2 = extract_rows(320193, FIXTURE_PAGED)
    check("페이지 파일 형태 파싱(10-K 1행)", len(rows2) == 1 and rows2[0][2] == "10-K")

    con = sqlite3.connect(":memory:")
    for ddl in DDL:
        con.execute(ddl)
    allr = rows + rows2
    con.executemany("INSERT OR IGNORE INTO earnings_events VALUES (?,?,?,?,?,?,?,?)", allr)
    con.executemany("INSERT OR IGNORE INTO earnings_events VALUES (?,?,?,?,?,?,?,?)", allr)
    n = con.execute("SELECT COUNT(*) FROM earnings_events").fetchone()[0]
    check("idempotent: 2회 적재 후에도 3행", n == 3)
    print("✅ self-test 통과" if ok else "❌ self-test 실패")
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(description="SEC 실적 발표일 벌크 수집(관측 전용)")
    ap.add_argument("--force", action="store_true", help="주간 가드 무시")
    ap.add_argument("--self-test", action="store_true", help="오프라인 파서 검증")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return
    con = ensure_db()
    try:
        run_bulk(con, force=args.force)
    except Exception as e:
        print(f"⚠️  실적일 수집 실패(비치명 — 다음 실행에서 재시도): {e}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
