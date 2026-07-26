# -*- coding: utf-8 -*-
"""
us_insider_collector.py — SEC Form 4 내부자 매매(분기 구조화 데이터셋) → insider_tx
==============================================================================
[2026-07-26 신설 · 데이터 확장 3차] 내부자 순매수 신호(문헌상 유의 — 특히 하락 후
임원 장내매수)의 재료. 관측 전용 — 점수 미투입, 검증은 본구축 PREREGISTER 후.

소스: https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/
  {YYYY}q{Q}_form345.zip  (SEC 공식 분기 구조화 TSV — XML 파싱 불필요,
  발행사 티커 포함. 분기 마감 후 수주 지연으로 공개)

무엇을 뽑나 (NONDERIV_TRANS × SUBMISSION × REPORTINGOWNER 조인):
  - 보통주 비파생 거래 중 **TRANS_CODE P(장내매수)·S(장내매도)** 만 — 내부자 신호
    문헌의 핵심은 P. 수여(A)·행사(M) 등은 신호가 흐려 제외.
  - 신고인 관계(임원/이사/10%주주), 거래일·주수·단가·거래후 보유주수.
  - **FILING_DATE(공시일) = PIT 키** — 거래일이 아니라 공시일 이후에만 알 수 있는
    정보로 취급(백테스트 정직성).

무게 관리:
  - 분기 파일 idempotent: 같은 분기 재적재 = 기존 분기 행 삭제 후 재삽입.
  - **점진 백필**: 회당 최대 N분기(기본 4)만 처리 → 2019~현재 ~30분기를 며칠에 걸쳐
    자동 완성(Actions 일일 실행이 이어받음). 7일 가드로 완료 후엔 새 분기만 탐침.
  - 컬럼은 헤더명으로 탐지(하드코딩 금지) — 필수 컬럼을 못 찾으면 **추측하지 않고**
    경고 후 그 분기를 건너뜀(us_short_collector 방어 철학).

비치명: 실패=경고 후 exit 0. 가드는 '새 분기 탐침까지 성공'했을 때만 갱신.

사용:
    python us_insider_collector.py                # 가드 하에 백필/증분(회당 4분기)
    python us_insider_collector.py --max-files 8  # 회당 분기 수 조정
    python us_insider_collector.py --force        # 7일 가드 무시
    python us_insider_collector.py --self-test    # 오프라인 파서 검증(네트워크 0)
"""
import argparse
import csv
import datetime as dt
import io
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
URL = ("https://www.sec.gov/files/structureddata/data/"
       "insider-transactions-data-sets/{q}_form345.zip")
UA = {"User-Agent": os.environ.get(
    "SEC_USER_AGENT", "us-screener research (github.com/sj951027; seok5139@gmail.com)")}

GUARD_DAYS = 7
START_Q = (2019, 1)
CODES = {"P", "S"}

DDL = [
    """CREATE TABLE IF NOT EXISTS insider_tx (
        quarter TEXT NOT NULL, accn TEXT NOT NULL,
        issuer_cik INTEGER, symbol TEXT, owner_cik INTEGER,
        is_officer INTEGER, is_director INTEGER, is_tenpct INTEGER,
        trans_date TEXT, code TEXT, shares REAL, price REAL, shares_after REAL,
        filed TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_ins_sym_filed ON insider_tx(symbol, filed)",
    "CREATE INDEX IF NOT EXISTS ix_ins_quarter ON insider_tx(quarter)",
    "CREATE TABLE IF NOT EXISTS insider_files_done (quarter TEXT PRIMARY KEY, rows INTEGER, done_at TEXT)",
    "CREATE TABLE IF NOT EXISTS xbrl_meta (key TEXT PRIMARY KEY, value TEXT)",
]

_MON = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def norm_date(s):
    """SEC 구조화셋 날짜('28-FEB-2025' 또는 ISO)를 ISO 로. 실패 시 원문 유지."""
    s = (s or "").strip()
    if not s:
        return ""
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    try:
        d, mon, y = s.split("-")
        return f"{int(y):04d}-{_MON[mon.upper()[:3]]:02d}-{int(d):02d}"
    except Exception:
        return s


def read_tsv(raw):
    """TSV bytes → (헤더 소문자 리스트, 행 이터레이터). 구분자 탭 고정(SEC 스펙)."""
    text = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8", errors="replace")
    rd = csv.reader(text, delimiter="\t")
    header = [h.strip().lower() for h in next(rd, [])]
    return header, rd


def _col(header, *cands):
    """후보 이름(부분일치 허용)으로 컬럼 인덱스 탐지. 없으면 None — 추측 금지."""
    for c in cands:
        c = c.lower()
        for i, h in enumerate(header):
            if h == c:
                return i
    for c in cands:
        c = c.lower()
        for i, h in enumerate(header):
            if c in h:
                return i
    return None


def parse_quarter_zip(zbytes, quarter):
    """분기 zip bytes → insider_tx 행 리스트. 필수 컬럼 미탐지 시 (None, 사유)."""
    zf = zipfile.ZipFile(io.BytesIO(zbytes))
    names = {n.lower(): n for n in zf.namelist()}

    def find(part):
        for low, orig in names.items():
            if part in low:
                return orig
        return None

    sub_n, tr_n, ro_n = find("submission"), find("nonderiv_trans"), find("reportingowner")
    if not (sub_n and tr_n and ro_n):
        return None, f"필수 TSV 누락: {sorted(names)[:5]}..."

    # SUBMISSION: accn → (filed, issuer_cik, symbol)
    h, rows = read_tsv(zf.read(sub_n))
    i_acc = _col(h, "accession_number")
    i_fil = _col(h, "filing_date")
    i_cik = _col(h, "issuercik", "issuer_cik")
    i_sym = _col(h, "issuertradingsymbol", "issuer_trading_symbol")
    if None in (i_acc, i_fil, i_cik):
        return None, f"SUBMISSION 컬럼 미탐지: {h[:8]}"
    sub = {}
    for r in rows:
        try:
            sub[r[i_acc].strip()] = (norm_date(r[i_fil]),
                                     int(float(r[i_cik])) if r[i_cik].strip() else None,
                                     (r[i_sym].strip().upper() if i_sym is not None
                                      and i_sym < len(r) else ""))
        except Exception:
            continue

    # REPORTINGOWNER: accn → (owner_cik, 관계 플래그)
    h, rows = read_tsv(zf.read(ro_n))
    i_acc = _col(h, "accession_number")
    i_ocik = _col(h, "rptownercik", "rptowner_cik")
    i_off = _col(h, "rptowner_isofficer", "isofficer", "officer")
    i_dir = _col(h, "rptowner_isdirector", "isdirector", "director")
    i_ten = _col(h, "rptowner_istenpercentowner", "istenpercent", "tenpercent")
    if None in (i_acc, i_ocik):
        return None, f"REPORTINGOWNER 컬럼 미탐지: {h[:8]}"

    def flag(r, i):
        if i is None or i >= len(r):
            return 0
        return 1 if r[i].strip().lower() in ("1", "true", "yes", "y") else 0

    own = {}
    for r in rows:
        try:
            own.setdefault(r[i_acc].strip(),
                           (int(float(r[i_ocik])) if r[i_ocik].strip() else None,
                            flag(r, i_off), flag(r, i_dir), flag(r, i_ten)))
        except Exception:
            continue

    # NONDERIV_TRANS: 거래 행 (P/S 만)
    h, rows = read_tsv(zf.read(tr_n))
    i_acc = _col(h, "accession_number")
    i_dt = _col(h, "trans_date")
    i_cd = _col(h, "trans_code")
    i_sh = _col(h, "trans_shares")
    i_pr = _col(h, "trans_pricepershare", "trans_price")
    i_af = _col(h, "shrs_ownd_folwng_trans", "shares_owned_following")
    if None in (i_acc, i_dt, i_cd, i_sh):
        return None, f"NONDERIV_TRANS 컬럼 미탐지: {h[:8]}"

    def num(r, i):
        if i is None or i >= len(r) or not r[i].strip():
            return None
        try:
            return float(r[i])
        except ValueError:
            return None

    out = []
    for r in rows:
        try:
            code = r[i_cd].strip().upper()
            if code not in CODES:
                continue
            acc = r[i_acc].strip()
            filed, icik, sym = sub.get(acc, ("", None, ""))
            ocik, f_off, f_dir, f_ten = own.get(acc, (None, 0, 0, 0))
            out.append((quarter, acc, icik, sym, ocik, f_off, f_dir, f_ten,
                        norm_date(r[i_dt]), code, num(r, i_sh), num(r, i_pr),
                        num(r, i_af), filed))
        except Exception:
            continue
    return out, None


def quarters_until_today():
    out, (y, q) = [], START_Q
    today = dt.date.today()
    while (y, q) <= (today.year, (today.month - 1) // 3 + 1):
        out.append(f"{y}q{q}")
        q += 1
        if q == 5:
            y, q = y + 1, 1
    return out


def ensure_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    for ddl in DDL:
        con.execute(ddl)
    con.commit()
    return con


def load_quarter(con, session, quarter):
    r = session.get(URL.format(q=quarter), headers=UA, timeout=180)
    if r.status_code == 404:
        return None            # 아직 미공개 분기(정상)
    r.raise_for_status()
    rows, err = parse_quarter_zip(r.content, quarter)
    if rows is None:
        print(f"  ⚠️ {quarter}: {err} — 건너뜀(다음 세션에서 매핑)")
        return 0
    con.execute("DELETE FROM insider_tx WHERE quarter=?", (quarter,))   # idempotent
    con.executemany("INSERT INTO insider_tx VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.execute("INSERT OR REPLACE INTO insider_files_done VALUES (?,?,?)",
                (quarter, len(rows), dt.date.today().isoformat()))
    con.commit()
    print(f"  ✓ {quarter}: {len(rows):,}행 (P/S 거래)")
    return len(rows)


def run(con, force=False, max_files=4):
    import requests
    done = {r[0] for r in con.execute("SELECT quarter FROM insider_files_done")}
    todo = [q for q in quarters_until_today() if q not in done]
    if not todo:
        last = con.execute(
            "SELECT value FROM xbrl_meta WHERE key='insider_last_probe'").fetchone()
        if last and not force:
            gap = (dt.date.today() - dt.date.fromisoformat(last[0])).days
            if gap < GUARD_DAYS:
                print(f"⏭  내부자 스킵 — 마지막 탐침 {last[0]} 이후 {gap}일 < {GUARD_DAYS}일")
                return
        todo = quarters_until_today()[-1:]      # 최신 분기 재탐침(신규 공개 확인)
        todo = [q for q in todo if q not in done] or []
        if not todo:
            print("  백필 완료 상태 — 새 분기 없음")
    s = requests.Session()
    print(f"▶ 내부자(Form 4) 분기 데이터셋 — 남은 {len(todo)}분기 중 최대 {max_files}개 처리")
    for q in todo[:max_files]:
        res = load_quarter(con, s, q)
        if res is None:
            print(f"  {q}: 아직 미공개(404) — 다음 주 재탐침")
            break
    con.execute("INSERT OR REPLACE INTO xbrl_meta VALUES ('insider_last_probe', ?)",
                (dt.date.today().isoformat(),))
    con.commit()
    tot, nq = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT quarter) FROM insider_tx").fetchone()
    print(f"💾 insider_tx 누적 {tot:,}행 · {nq}분기 (전체 {len(quarters_until_today())}분기 목표)")
    print("✅ 내부자 적재 — 관측 전용. PIT 은 filed(공시일) 기준으로만 사용.")


def _tsv(*lines):
    return ("\n".join(lines)).encode("utf-8")


def _fixture_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SUBMISSION.tsv", _tsv(
            "ACCESSION_NUMBER\tFILING_DATE\tPERIOD_OF_REPORT\tISSUERCIK\tISSUERNAME\tISSUERTRADINGSYMBOL",
            "acc-1\t28-FEB-2025\t26-FEB-2025\t320193\tApple Inc\tAAPL",
            "acc-2\t05-MAR-2025\t03-MAR-2025\t789019\tMicrosoft\tMSFT"))
        zf.writestr("REPORTINGOWNER.tsv", _tsv(
            "ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNER_ISOFFICER\tRPTOWNER_ISDIRECTOR\tRPTOWNER_ISTENPERCENTOWNER",
            "acc-1\t111\t1\t0\t0",
            "acc-2\t222\t0\t1\t0"))
        zf.writestr("NONDERIV_TRANS.tsv", _tsv(
            "ACCESSION_NUMBER\tNONDERIV_TRANS_SK\tTRANS_DATE\tTRANS_CODE\tTRANS_SHARES\tTRANS_PRICEPERSHARE\tSHRS_OWND_FOLWNG_TRANS",
            "acc-1\t1\t26-FEB-2025\tP\t1000\t185.5\t51000",
            "acc-1\t2\t26-FEB-2025\tM\t500\t0\t51500",      # 행사(M) → 제외
            "acc-2\t3\t03-MAR-2025\tS\t2000\t410.2\t8000"))
    return buf.getvalue()


def self_test():
    print("== self-test (오프라인) ==")
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  {'OK ' if cond else 'FAIL'} {label}")
        ok &= cond

    rows, err = parse_quarter_zip(_fixture_zip(), "2025q1")
    check("파싱 성공(에러 없음)", err is None)
    check("P/S 만 2행 (M 제외)", len(rows) == 2 and {r[9] for r in rows} == {"P", "S"})
    p = [r for r in rows if r[9] == "P"][0]
    check("매수행: AAPL·임원플래그·주수/단가", p[3] == "AAPL" and p[5] == 1
          and p[10] == 1000 and p[11] == 185.5)
    check("날짜 정규화(28-FEB-2025→ISO) · filed=공시일", p[13] == "2025-02-28"
          and p[8] == "2025-02-26")
    s_ = [r for r in rows if r[9] == "S"][0]
    check("매도행: MSFT·이사플래그", s_[3] == "MSFT" and s_[6] == 1)

    con = sqlite3.connect(":memory:")
    for ddl in DDL:
        con.execute(ddl)
    for _ in range(2):     # 같은 분기 2회 = delete-then-insert 로 불변
        con.execute("DELETE FROM insider_tx WHERE quarter=?", ("2025q1",))
        con.executemany("INSERT INTO insider_tx VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    n = con.execute("SELECT COUNT(*) FROM insider_tx").fetchone()[0]
    check("idempotent: 재적재 후에도 2행", n == 2)
    # 컬럼 누락 방어: 필수 컬럼 빠지면 추측하지 않고 None
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("SUBMISSION.tsv", _tsv("FOO\tBAR", "x\ty"))
        zf.writestr("NONDERIV_TRANS.tsv", _tsv("FOO", "x"))
        zf.writestr("REPORTINGOWNER.tsv", _tsv("FOO", "x"))
    r2, e2 = parse_quarter_zip(bad.getvalue(), "2025q1")
    check("필수 컬럼 미탐지 → 추측 없이 거부", r2 is None and bool(e2))
    print("✅ self-test 통과" if ok else "❌ self-test 실패")
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(description="SEC Form 4 내부자 분기 수집(관측 전용)")
    ap.add_argument("--force", action="store_true", help="7일 가드 무시")
    ap.add_argument("--max-files", type=int, default=4, help="회당 처리 분기 수(점진 백필)")
    ap.add_argument("--self-test", action="store_true", help="오프라인 파서 검증")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return
    con = ensure_db()
    try:
        run(con, force=args.force, max_files=args.max_files)
    except Exception as e:
        print(f"⚠️  내부자 수집 실패(비치명 — 다음 실행에서 재시도): {e}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
