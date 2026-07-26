# -*- coding: utf-8 -*-
"""
us_xbrl_collector.py — SEC XBRL 재무(companyfacts 벌크) → us_fundamentals.db
==============================================================================
[2026-07-26 신설 · 데이터 확장 1차] 저평가/리레이팅·PEAD(SUE)·재무 모멘텀 검증의
재료. 지금은 **수집만**(관측 원칙) — 어떤 점수에도 넣지 않는다. 검증은 9월 본구축
때 PREREGISTER 후.

소스(전부 무료·공식):
  - https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip
    (전 filer 의 XBRL 사실관계 벌크, 매일 갱신, ~1.2GB)
  - https://www.sec.gov/files/company_tickers.json  (CIK↔티커 매핑)

포인트-인-타임 원칙(한국판 §계승 — 백테스트 정직성의 핵심):
  filed(공시일)를 함께 저장하고 **절대 덮어쓰지 않는다**(INSERT OR IGNORE, append).
  재무 정정(10-K/A 등)은 새 filed 행으로 쌓임 → 분석 시 "filed ≤ 기준일 중 최신"
  으로 조회하면 룩어헤드가 원천 차단된다.

무게 관리:
  - 주간 가드: 마지막 성공 후 6일 미만이면 스킵(--force 로 무시). 재무는 분기 단위라
    주 1회면 충분 — 매일 1.2GB 받을 이유 없음.
  - 태그 화이트리스트(가치·수익성·현금흐름·EPS·주식수 ~12종)·end>=2019·
    본보고서 form 만 적재 → DB 수백MB 선에서 억제.
  - 상장 티커 매핑에 있는 CIK 만 파싱(펀드·SPV 등 비상장 filer 스킵).

비치명: 네트워크 실패 시 경고 후 exit 0 (일일 파이프라인을 막지 않음). 가드 시각은
  '성공 시에만' 갱신 → 실패하면 다음날 자동 재시도.

사용:
    python us_xbrl_collector.py               # 주간 가드 하에 벌크 적재
    python us_xbrl_collector.py --force       # 가드 무시
    python us_xbrl_collector.py --self-test   # 오프라인 파서 검증(네트워크 0)
"""
import argparse
import datetime as dt
import io
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
DB = DATA_DIR / "us_fundamentals.db"
ZIP_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
# SEC 요구사항: 식별 가능한 UA(이름/연락처). 필요 시 SEC_USER_AGENT 환경변수로 교체.
UA = {"User-Agent": os.environ.get(
    "SEC_USER_AGENT", "us-screener research (github.com/sj951027; seok5139@gmail.com)")}

GUARD_DAYS = 6
MIN_END = "2019-01-01"
MAX_FILED_LAG_DAYS = 400   # filed ≤ end+400일 만 적재 — 후속 보고서의 '전년동기 비교재수록'
                           # 중복을 걸러 DB 를 1/3 수준으로 다이어트(원공시+근접 정정은 보존).
FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "40-F", "6-K"}
UNITS = {"USD", "USD/shares", "shares"}
# 가치(EQ·Assets)·수익성(REV·OP·NI)·현금흐름(OCF)·EPS·주식수 — 분석 시 동의어 통합.
GAAP_TAGS = {
    "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
    "NetIncomeLoss", "OperatingIncomeLoss",
    "StockholdersEquity", "Assets",
    "NetCashProvidedByUsedInOperatingActivities",
    "EarningsPerShareDiluted", "EarningsPerShareBasic",
    "CommonStockSharesOutstanding",
}
DEI_TAGS = {"EntityCommonStockSharesOutstanding"}

DDL = [
    """CREATE TABLE IF NOT EXISTS xbrl_facts (
        cik INTEGER NOT NULL, tag TEXT NOT NULL, unit TEXT NOT NULL,
        end TEXT NOT NULL, val REAL, fy INTEGER, fp TEXT, form TEXT,
        filed TEXT NOT NULL, accn TEXT,
        PRIMARY KEY (cik, tag, unit, end, filed, form))""",
    """CREATE TABLE IF NOT EXISTS cik_ticker (
        cik INTEGER NOT NULL, ticker TEXT NOT NULL, name TEXT, updated TEXT,
        PRIMARY KEY (cik, ticker))""",
    "CREATE TABLE IF NOT EXISTS xbrl_meta (key TEXT PRIMARY KEY, value TEXT)",
    "CREATE INDEX IF NOT EXISTS ix_xbrl_cik_end ON xbrl_facts(cik, end)",
]


def extract_rows(cik, facts):
    """companyfacts 한 회사 JSON → 화이트리스트 행 리스트.
    (cik, tag, unit, end, val, fy, fp, form, filed, accn)"""
    rows = []
    for taxonomy, tags in (("us-gaap", GAAP_TAGS), ("dei", DEI_TAGS)):
        src = (facts.get("facts") or {}).get(taxonomy) or {}
        for tag in tags:
            units = (src.get(tag) or {}).get("units") or {}
            for unit, items in units.items():
                if unit not in UNITS:
                    continue
                for it in items:
                    end = it.get("end") or ""
                    form = it.get("form") or ""
                    filed = it.get("filed") or ""
                    val = it.get("val")
                    if end < MIN_END or form not in FORMS or not filed or val is None:
                        continue
                    try:   # 비교재수록 컷: 원공시·근접 정정만 (PIT 는 그대로 성립)
                        lag = (dt.date.fromisoformat(filed) - dt.date.fromisoformat(end)).days
                        if lag > MAX_FILED_LAG_DAYS or lag < 0:
                            continue
                    except ValueError:
                        continue
                    rows.append((cik, tag, unit, end, float(val),
                                 it.get("fy"), it.get("fp"), form, filed,
                                 it.get("accn")))
    return rows


def ensure_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    for ddl in DDL:
        con.execute(ddl)
    con.commit()
    return con


def load_ticker_map(con, session):
    r = session.get(TICKERS_URL, headers=UA, timeout=60)
    r.raise_for_status()
    data = r.json()
    today = dt.date.today().isoformat()
    rows = [(int(v["cik_str"]), str(v["ticker"]).upper(), v.get("title", ""), today)
            for v in data.values()]
    con.executemany("INSERT OR REPLACE INTO cik_ticker VALUES (?,?,?,?)", rows)
    con.commit()
    print(f"  CIK↔티커 매핑 {len(rows):,}건 갱신")
    return {r[0] for r in rows}


def run_bulk(con, force=False):
    import requests
    last = con.execute("SELECT value FROM xbrl_meta WHERE key='last_success'").fetchone()
    if last and not force:
        gap = (dt.date.today() - dt.date.fromisoformat(last[0])).days
        if gap < GUARD_DAYS:
            print(f"⏭  XBRL 스킵 — 마지막 성공 {last[0]} 이후 {gap}일 < {GUARD_DAYS}일 (주간 가드)")
            return
    s = requests.Session()
    ciks = load_ticker_map(con, s)

    # ⚠️ 임시파일은 데이터 폴더 밖(시스템 temp)에 — 실패 잔재가 tar/Release 에
    #    섞여 자산 한도(2GB)를 위협하는 사고 방지. finally 에서 반드시 정리.
    import tempfile
    tmp = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()) / "companyfacts.zip.tmp"
    print(f"▶ companyfacts.zip 다운로드 (~1.2GB) ...")
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
    n_ent = n_parsed = 0
    with zipfile.ZipFile(tmp) as zf:
        names = zf.namelist()
        print(f"  zip 내 filer {len(names):,}개 · 상장 매핑 {len(ciks):,}개만 파싱")
        for name in names:
            n_ent += 1
            if not name.startswith("CIK") or not name.endswith(".json"):
                continue
            try:
                cik = int(name[3:-5])
            except ValueError:
                continue
            if cik not in ciks:
                continue
            try:
                facts = json.loads(zf.read(name))
                rows = extract_rows(cik, facts)
            except Exception:
                continue           # 개별 filer 파싱 실패는 건너뜀(비치명)
            if rows:
                con.executemany(
                    "INSERT OR IGNORE INTO xbrl_facts VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
                n_parsed += 1
            if n_parsed and n_parsed % 1000 == 0:
                con.commit()
                print(f"  [{n_parsed:,} 상장사 파싱] zip {n_ent:,}/{len(names):,}")
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM xbrl_facts").fetchone()[0]
    n_cik = con.execute("SELECT COUNT(DISTINCT cik) FROM xbrl_facts").fetchone()[0]
    con.execute("INSERT OR REPLACE INTO xbrl_meta VALUES ('last_success', ?)",
                (dt.date.today().isoformat(),))
    con.commit()
    print(f"💾 xbrl_facts 누적 {total:,}행 · {n_cik:,}개 회사 (파싱 {n_parsed:,}개)")
    print("✅ XBRL 벌크 적재 완료 — 관측 전용(점수 미투입). PIT 조회는 filed ≤ 기준일.")


FIXTURE = {
    "cik": 320193,
    "facts": {
        "us-gaap": {
            "NetIncomeLoss": {"units": {"USD": [
                {"end": "2025-12-27", "val": 1e9, "fy": 2026, "fp": "Q1",
                 "form": "10-Q", "filed": "2026-01-30", "accn": "a1"},
                {"end": "2025-12-27", "val": 1.1e9, "fy": 2026, "fp": "Q1",
                 "form": "10-Q/A", "filed": "2026-03-02", "accn": "a2"},   # 정정 = 새 filed 행
                {"end": "2018-09-29", "val": 5e8, "fy": 2018, "fp": "FY",
                 "form": "10-K", "filed": "2018-11-05", "accn": "a3"},     # end<2019 → 제외
                {"end": "2025-12-27", "val": 9e8, "fy": 2026, "fp": "Q1",
                 "form": "8-K", "filed": "2026-01-28", "accn": "a4"},      # form 비대상 → 제외
                {"end": "2023-12-30", "val": 8e8, "fy": 2026, "fp": "Q1",
                 "form": "10-Q", "filed": "2026-01-30", "accn": "a5"},     # 비교재수록(lag>400d) → 제외
            ]}},
            "EarningsPerShareDiluted": {"units": {"USD/shares": [
                {"end": "2025-12-27", "val": 2.4, "fy": 2026, "fp": "Q1",
                 "form": "10-Q", "filed": "2026-01-30", "accn": "a1"}]}},
            "NotWhitelisted": {"units": {"USD": [
                {"end": "2025-12-27", "val": 1, "form": "10-Q", "filed": "2026-01-30"}]}},
        },
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"end": "2026-01-15", "val": 1.5e10, "fy": 2026, "fp": "Q1",
             "form": "10-Q", "filed": "2026-01-30", "accn": "a1"}]}}},
    },
}


def self_test():
    print("== self-test (오프라인) ==")
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  {'OK ' if cond else 'FAIL'} {label}")
        ok &= cond

    rows = extract_rows(320193, FIXTURE)
    tags = sorted({r[1] for r in rows})
    check("화이트리스트만 추출 (비대상 태그 제외)", "NotWhitelisted" not in tags)
    check("end<2019 제외 · form 비대상(8-K) 제외",
          all(r[3] >= MIN_END and r[7] in FORMS for r in rows))
    ni = [r for r in rows if r[1] == "NetIncomeLoss"]
    check("정정 재공시 = 별도 filed 행 2개(PIT 보존)", len(ni) == 2
          and {r[8] for r in ni} == {"2026-01-30", "2026-03-02"})
    check("비교재수록(filed>end+400일) 제외", not any(r[3] == "2023-12-30" for r in rows))
    check("EPS·주식수(dei) 포함 총 4행", len(rows) == 4)

    con = sqlite3.connect(":memory:")
    for ddl in DDL:
        con.execute(ddl)
    con.executemany("INSERT OR IGNORE INTO xbrl_facts VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT OR IGNORE INTO xbrl_facts VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    n = con.execute("SELECT COUNT(*) FROM xbrl_facts").fetchone()[0]
    check("idempotent: 2회 적재 후에도 4행", n == 4)
    pit = con.execute(
        "SELECT val FROM xbrl_facts WHERE tag='NetIncomeLoss' AND end='2025-12-27' "
        "AND filed <= '2026-02-15' ORDER BY filed DESC LIMIT 1").fetchone()[0]
    check("PIT 조회: 2/15 기준 정정 전 값(1.0e9)", pit == 1e9)
    print("✅ self-test 통과" if ok else "❌ self-test 실패")
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(description="SEC XBRL 재무 벌크 수집(관측 전용)")
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
        print(f"⚠️  XBRL 수집 실패(비치명 — 다음 실행에서 재시도): {e}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
