# -*- coding: utf-8 -*-
"""
us_shortvol_collector.py — FINRA 일별 공매도 거래량(Reg SHO daily) → us_shortvol.db `short_volume_daily`
=====================================================================================================
[2026-09-06 신설 · 데이터 확장 5차 · patch_note/v07] 관측 전용.

소스: https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt  (FINRA 통합 NMS)
  - 거래일 당일 18:00 ET 이전 게시(FINRA 안내). 파일 이력 2018-08-01~. 무료·비상업.
  - 형식(FINRA 포맷 가이드): 파이프 구분, 헤더 Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market,
    마지막 줄은 레코드 수(트레일러). 헤더는 자동 감지·트레일러는 필드 수로 걸러냄.
무엇이 새로운가: 기존 short_interest 는 격주 '잔고(포지션)'. 이것은 매일 '그날 거래 중
  공매도 체결 비율'(short_vol/total_vol) — 다른 정보(문헌: 일별 공매도 비율↑ → 이후 부진).
  스캔 시 신호 후보: svr5/svr20(5·20일 평균 비율), 그 변화. 관측 전용·점수 미투입.
PIT 주의: 러너(22:00 UTC = 18:00 ET)와 게시 시각이 겹친다 → 당일 파일은 다음 실행에서
  잡히는 것이 정상. 스캔에서는 **거래일 D 의 파일을 D+1 이후 앵커에서만 사용**한다.
용량(추정): 파일당 ~1.2만 행, 우리 유니버스(daily_ohlcv 심볼)로 제한 시 ~7~8천 행/일.
  2023-05~ 백필 3.3년 ≈ 6M 행·~250MB(별도 DB). 2018-08 부터 전부 받으면 ~1GB → 2GB 한도
  캘린더를 앞당기므로 **백필 시작은 2023-05-01(ohlcv 창과 동일)** 로 제한. 연 ~80MB 증가.
사용:
    python us_shortvol_collector.py                         # 최근 10일 창 탐침(일일 증분)
    python us_shortvol_collector.py --backfill-from 2023-05-01   # 회당 --max-files(기본 150) 씩 점진
    python us_shortvol_collector.py --self-test             # 오프라인 검증(네트워크 0)
원칙: idempotent(PK date+symbol)·비치명·조회 전용. 파일 존재 확인된 날짜만 files_done.
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
DB = DATA_DIR / "us_shortvol.db"
OHLCV_DB = DATA_DIR / "us_ohlcv.db"
RAW_DIR = DATA_DIR / "raw_finra"
URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d}.txt"
UA = {"User-Agent": "Mozilla/5.0 (personal research; contact via github)"}
MAX_FILES_DEFAULT = 150
SLEEP = 0.3

DDL = [
    """CREATE TABLE IF NOT EXISTS short_volume_daily (
        date TEXT NOT NULL, symbol TEXT NOT NULL,
        short_vol REAL, short_exempt_vol REAL, total_vol REAL, market TEXT,
        PRIMARY KEY (date, symbol))""",
    "CREATE TABLE IF NOT EXISTS shortvol_files_done (date TEXT PRIMARY KEY, rows INTEGER, fetched_at TEXT)",
]


def detect_columns(header_line):
    delim = "|" if header_line.count("|") >= header_line.count(",") else ","
    cols = [c.strip().lower().replace(" ", "").replace("_", "") for c in header_line.split(delim)]
    def find(*keys):
        for i, c in enumerate(cols):
            if c in keys:
                return i
        for i, c in enumerate(cols):
            if any(k in c for k in keys):
                return i
        return None
    m = {"date": find("date"), "symbol": find("symbol", "ticker"),
         "short": find("shortvolume"), "exempt": find("shortexemptvolume"),
         "total": find("totalvolume"), "mkt": find("market")}
    # 'shortvolume' 이 'shortexemptvolume' 에 부분일치하지 않도록 정확일치 우선(위 find 순서)
    if m["symbol"] is None or m["short"] is None or m["total"] is None:
        return None, None
    return delim, m


def parse_file(text, fallback_date, keep=None):
    """rows [(date8, symbol, short, exempt, total, market)]. keep=심볼 집합(None 이면 전부).
    트레일러(필드 수 부족·숫자 아님)는 건너뜀. 포맷 감지 실패 시 None."""
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        return None
    delim, m = detect_columns(lines[0])
    if delim is None:
        return None
    need = max(v for v in m.values() if v is not None)
    rows = []
    for line in lines[1:]:
        f = line.split(delim)
        if len(f) <= need:
            continue  # 트레일러/불량 행
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
        sym = g("symbol")
        if not sym:
            continue
        sym = norm_symbol(sym)   # FINRA 표기 → yfinance/daily_ohlcv 표기
        if sym is None:
            continue
        if keep is not None and sym not in keep:
            continue
        d8 = (g("date") or fallback_date).replace("-", "")[:8]
        sv, tv = g("short", float), g("total", float)
        if sv is None or tv is None:
            continue
        rows.append((d8, sym, sv, g("exempt", float), tv, g("mkt")))
    return rows


def norm_symbol(sym):
    """FINRA 표기 → daily_ohlcv(yfinance) 표기. 실파일 실측(2026-09-06, CNMSshvol20260904):
    클래스 'BRK/B'→'BRK-B', 우선주 'ABRpD'→'ABR-PD', 워런트 'AAC/WS'→'AAC-WS'(유니버스 밖),
    '.' 표기도 방어. 규칙 밖 심볼은 그대로(유니버스 제한에서 자연 제외)."""
    import re
    s = sym.strip()
    m = re.match(r"^([A-Z]+)p([A-Z])?$", s)          # 우선주: ABRpD→ABR-PD, TYp→TY-P
    if m:
        return f"{m.group(1)}-P{m.group(2) or ''}"
    if re.search(r"[a-z]", s):                       # 그 밖의 소문자 접미(권리 r·when-issued w 등)는 버림
        return None                                  #   — .upper() 하면 정규 5자 티커와 충돌 위험
    return s.replace("/", "-").replace(".", "-")


def universe_symbols():
    """daily_ohlcv 심볼 집합(용량 제한용). 없으면 None(전부 저장)."""
    try:
        oc = sqlite3.connect(f"file:{OHLCV_DB}?mode=ro", uri=True)
        s = {r[0] for r in oc.execute("SELECT DISTINCT symbol FROM daily_ohlcv")}
        oc.close()
        return s or None
    except Exception:
        return None


def trading_days(start, end):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


def self_test():
    # 픽스처 = 실파일 형식 복제(2026-09-06 실측: 파이프·소수 거래량·클래스 '/'·우선주 'p'·트레일러 레코드수)
    fx = ("Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n"
          "20260904|AAPL|7632509.075240|27953|14537792.715407|B,Q,N\n"
          "20260904|BRK/B|268632.653992|2672|1249876.120945|B,Q,N\n"
          "20260904|ABRpD|5000|0|20000|Q\n"
          "20260904|AAC/WS|10|0|100|Q\n"
          "20260904|ZZZZ|10|0|100|N\n"
          "20260904|BAD|x|0|100|N\n"
          "12215\n")
    rows = parse_file(fx, "20260904", keep={"AAPL", "BRK-B", "ABR-PD"})
    ok = True
    def check(label, cond):
        nonlocal ok
        print(("  ✓ " if cond else "  ✗ ") + label); ok = ok and cond
    check("행 수(유니버스 제한·트레일러·불량 제외) == 3", rows is not None and len(rows) == 3)
    check("클래스 표기 BRK/B→BRK-B", any(r[1] == "BRK-B" for r in rows))
    check("우선주 표기 ABRpD→ABR-PD", any(r[1] == "ABR-PD" for r in rows))
    check("소수 거래량 파싱", any(r[1] == "AAPL" and abs(r[2] - 7632509.07524) < 1e-3 and abs(r[4] - 14537792.715407) < 1e-3 for r in rows))
    check("ShortVolume 과 ShortExemptVolume 열 혼동 없음", any(r[1] == "AAPL" and r[3] == 27953 for r in rows))
    check("norm_symbol 규칙", norm_symbol("AKO/A") == "AKO-A" and norm_symbol("BRK.B") == "BRK-B" and norm_symbol("AAPL") == "AAPL")
    check("시리즈 없는 우선주 TYp→TY-P · 소문자 접미(ABCr)는 버림", norm_symbol("TYp") == "TY-P" and norm_symbol("ABCr") is None)
    check("유니버스 매칭 0행 → None 아닌 빈 리스트(done 미표기 경로)", parse_file(fx, "20260904", keep={"NOPE"}) == [])
    check("헤더 감지 실패 → None", parse_file("foo,bar\n1,2\n", "20260904") is None)
    con = sqlite3.connect(":memory:")
    for d in DDL: con.execute(d)
    con.executemany("INSERT OR IGNORE INTO short_volume_daily VALUES (?,?,?,?,?,?)", rows)
    con.executemany("INSERT OR IGNORE INTO short_volume_daily VALUES (?,?,?,?,?,?)", rows)
    check("idempotent(PK date+symbol)", con.execute("SELECT COUNT(*) FROM short_volume_daily").fetchone()[0] == 3)
    print("self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill-from", default=None, help="예: 2023-05-01 (점진, 회당 --max-files)")
    ap.add_argument("--max-files", type=int, default=MAX_FILES_DEFAULT)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        sys.exit(self_test())
    import requests
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    for d in DDL:
        con.execute(d)
    done = {d for (d,) in con.execute("SELECT date FROM shortvol_files_done")}
    today = dt.date.today()
    start = dt.date.fromisoformat(args.backfill_from) if args.backfill_from else today - dt.timedelta(days=10)
    cands = [d for d in trading_days(start, today) if d.strftime("%Y%m%d") not in done]
    cands = cands[-args.max_files:] if not args.backfill_from else cands[:args.max_files]
    keep = universe_symbols()
    print(f"[일별 공매도 거래량] 후보 {len(cands)}일 (확보 {len(done)}파일, 유니버스 제한 {'있음' if keep else '없음'})")
    got = 0
    codes = {}   # 상태코드 집계 — 404 는 휴장/미게시(정상), 403/5xx 가 연속이면 차단 의심
    now = dt.datetime.now().isoformat(timespec="seconds")
    for d in cands:
        ds = d.strftime("%Y%m%d")
        try:
            r = requests.get(URL.format(d=ds), headers=UA, timeout=30)
        except Exception as e:
            print(f"  ⚠️ {ds} 요청 실패(다음 실행 재시도): {e}")
            codes["exc"] = codes.get("exc", 0) + 1
            time.sleep(3)
            continue
        codes[r.status_code] = codes.get(r.status_code, 0) + 1
        if r.status_code != 200 or len(r.content) < 2000:
            time.sleep(SLEEP)
            continue  # 휴장일/미게시(404) — 그 외 코드는 아래 집계로 드러남
        rows = parse_file(r.content.decode("utf-8", errors="replace"), ds, keep)
        if rows is None:
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            (RAW_DIR / f"CNMSshvol{ds}.txt").write_bytes(r.content)
            print(f"  ⚠️ {ds} 포맷 감지 실패 — raw_finra/ 원본 보존")
            continue
        if not rows:
            print(f"  ⚠️ {ds} 헤더는 정상이나 유니버스 매칭 0행 — done 미표기(다음 실행 재시도)")
            time.sleep(SLEEP)
            continue
        con.executemany("INSERT OR IGNORE INTO short_volume_daily VALUES (?,?,?,?,?,?)", rows)
        con.execute("INSERT OR REPLACE INTO shortvol_files_done VALUES (?,?,?)", (ds, len(rows), now))
        con.commit()
        got += 1
        time.sleep(SLEEP)
    bad = {k: v for k, v in codes.items() if k not in (200, 404)}
    if cands and got == 0 and bad and sum(bad.values()) >= min(5, len(cands)):
        print(f"❌ 일별 공매도 거래량 — 후보 {len(cands)}일 전부 실패, 상태코드 {bad} (차단/UA 의심). "
              f"비치명이지만 데이터는 0 — 텔레그램 '일별공매도' 최신일이 멈추면 이 줄을 확인할 것")
    n, nd, dmin, dmax = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT date), MIN(date), MAX(date) FROM short_volume_daily").fetchone()
    con.close()
    print(f"💾 short_volume_daily 신규 {got}파일 · 누적 {n:,}행 · {nd}일 ({dmin}~{dmax})")
    print("✅ 일별 공매도 거래량 — 관측 전용. 당일 파일은 게시 시각 때문에 다음 실행에서 잡히는 것이 정상.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"❌ 실패(비치명): {e}")
        sys.exit(0)
