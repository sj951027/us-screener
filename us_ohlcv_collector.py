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
                                            #   + 분할·배당 소급 재조정(v2026-08-21, 아래)
  python us_ohlcv_collector.py --self-test  # 오프라인 검증(네트워크 0)

[v2026-08-21 분할 소급 재조정] 증분 창(7일)은 그 이전 행을 건드리지 않으므로,
백필 이후 발생한 분할이 과거 adj_close 에 소급되지 않아 가짜 절벽이 생겼다
(실측: MNST 2:1 20260811, US_PROJECT_KNOWLEDGE.md §7). 대응 3중:
  ① 증분 fetch 에 actions=True — 창 안의 Stock Splits/Dividends 이벤트 감지 시
     해당 심볼을 adjust_queue 에 등록.
  ② 매 실행 절벽 스캔 — 인접일 adj_close 비율이 정수분할비(±6%) 근처인 심볼을
     큐에 등록. **오탐(실제 급등락)이어도 재수집은 같은 값을 덮어쓸 뿐이라 무해.**
     같은 절벽의 반복 등록은 cliff_checked 로 차단.
  ③ 큐 처리 — 심볼 전체 3년 이력 재수집 후 INSERT OR REPLACE(소급 덮어쓰기).
     회당 REPAIR_CAP 심볼 상한(rate limit 보호), 남으면 다음 실행이 이어감.
한계(기록): 과거 배당의 소급 드리프트(연 1~2% 수준)는 이벤트가 증분 창을 벗어난
경우 감지 불가 — 절벽 스캔에도 안 걸리는 소폭이라 미해결로 남김(§7).

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
REPAIR_CAP = 200      # 회당 재조정(전체 재수집) 심볼 상한 — 남은 건 다음 실행이 처리
# 절벽 스캔이 보는 정수 분할비 후보(정방향=액면병합, 역방향=액면분할). ±6% 허용.
SPLIT_RATIOS = [2, 3, 4, 5, 6, 8, 10, 15, 20, 25, 40, 50]

DDL = [
    """CREATE TABLE IF NOT EXISTS daily_ohlcv (
        symbol TEXT NOT NULL, date TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL, adj_close REAL, volume INTEGER,
        PRIMARY KEY (symbol, date))""",
    """CREATE TABLE IF NOT EXISTS backfill_done (
        symbol TEXT PRIMARY KEY, done_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_us_ohlcv_date ON daily_ohlcv(date)",
    # v2026-08-21 분할 소급 재조정(헤더 설명): 재수집 대기 큐 + 절벽 재등록 차단 기록
    """CREATE TABLE IF NOT EXISTS adjust_queue (
        symbol TEXT PRIMARY KEY, reason TEXT, detail TEXT, queued_at TEXT,
        attempts INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS cliff_checked (
        symbol TEXT NOT NULL, date TEXT NOT NULL, PRIMARY KEY (symbol, date))""",
]


def load_symbols():
    """us_seed.db 최신 상장 목록에서 ETF 제외 심볼. (us_seed_collector 선행 필요)"""
    if not SEED_DB.exists():
        raise SystemExit(f"us_seed.db 없음({SEED_DB}) — 먼저 python us_seed_collector.py")
    con = sqlite3.connect(f"file:{SEED_DB}?mode=ro", uri=True)
    last = con.execute("SELECT MAX(date) FROM listing_daily").fetchone()[0]
    rows = con.execute(
        "SELECT symbol, name FROM listing_daily WHERE date=? AND (etf IS NULL OR etf!='Y')",
        (last,)).fetchall()
    con.close()
    # 워런트·유닛·라이츠 제외 — 야후에 시세 없음(2026-07-12 백필 실측: -U/-W/-R 404).
    #   심볼 접미사로 자르면 BRK-B 같은 정상 클래스주가 다치므로 '증권명 키워드'로 거른다.
    BAD = ("WARRANT", " UNIT", "UNITS", " RIGHT", "RIGHTS")
    syms = [s for s, n in rows
            if not any(b in (n or "").upper() for b in BAD)]
    # yfinance 표기: 우선주 등 '$'·'.' 계열 → '-' (예: BRK.B → BRK-B)
    return sorted({s.replace(".", "-").replace("$", "-P") for s in syms if s.isascii()})


def store(con, df, symbol, replace=False):
    """yf.download 단일심볼 DataFrame → INSERT OR IGNORE(기본) / REPLACE(재조정용).
    replace=True 는 분할 소급 재수집에서만 — 과거 행을 조정된 값으로 덮어쓴다."""
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
    verb = "REPLACE" if replace else "IGNORE"
    cur = con.executemany(
        f"INSERT OR {verb} INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?)", rows)
    return cur.rowcount


def fetch_chunk(symbols, start=None, period=None, actions=False):
    import yfinance as yf
    kw = dict(interval="1d", auto_adjust=False, actions=actions,
              group_by="ticker", threads=True, progress=False)
    if period:
        kw["period"] = period
    else:
        kw["start"] = start
    return yf.download(symbols, **kw)


# ── v2026-08-21 분할 소급 재조정 ──────────────────────────────────────

def detect_events(sub):
    """7일 창 DataFrame 에서 분할/배당 이벤트 감지 → 'split'|'div'|None.
    (auto_adjust=False + actions=True 일 때만 컬럼 존재 — 없으면 None)"""
    try:
        if "Stock Splits" in sub.columns and (sub["Stock Splits"].fillna(0) != 0).any():
            return "split"
        if "Dividends" in sub.columns and (sub["Dividends"].fillna(0) != 0).any():
            return "div"
    except Exception:
        pass
    return None


def scan_cliffs(con):
    """DB의 인접일 adj_close 비율이 정수분할비(±6%) 근처인 (symbol, date) 탐지.
    cliff_checked 에 있는 것은 제외. 반환: [(symbol, date, ratio)].
    주의: 실제 급등락도 걸릴 수 있으나 재수집은 같은 값을 덮어쓸 뿐이라 무해 —
    성공 시 cliff_checked 에 기록돼 반복 등록되지 않는다."""
    targets = []
    for k in SPLIT_RATIOS:
        targets += [1.0 / k, float(k)]
    checked = set(con.execute("SELECT symbol, date FROM cliff_checked"))
    out = []
    cur = con.execute(
        "SELECT symbol, date, adj_close FROM daily_ohlcv "
        "WHERE adj_close > 0 ORDER BY symbol, date")
    prev_sym, prev_px = None, None
    for sym, d, px in cur:
        if sym == prev_sym and prev_px and px:
            r = px / prev_px
            for t in targets:
                if abs(r - t) / t < 0.06:
                    if (sym, d) not in checked:
                        out.append((sym, d, round(r, 4)))
                    break
        prev_sym, prev_px = sym, px
    return out


def _queue_fail(con, sym, reason, detail, max_attempts=3):
    """재수집 실패(0행·예외) 처리: 시도 횟수 증가, 3회째엔 포기 —
    큐에서 제거하고 cliff 는 checked 표기(상폐 심볼이 큐를 영구 점유하는 것 방지.
    데이터는 원래 값 그대로 남는다 — 잘못 덮어쓰는 일은 없음)."""
    con.execute("UPDATE adjust_queue SET attempts = attempts + 1 WHERE symbol=?", (sym,))
    a = con.execute("SELECT attempts FROM adjust_queue WHERE symbol=?", (sym,)).fetchone()
    if a and a[0] >= max_attempts:
        con.execute("DELETE FROM adjust_queue WHERE symbol=?", (sym,))
        if reason == "cliff" and detail:
            for dd in detail.split(","):
                con.execute("INSERT OR IGNORE INTO cliff_checked VALUES (?,?)", (sym, dd))
        print(f"  [포기] {sym} — {max_attempts}회 실패(상폐 추정), 원본 유지")


def process_queue(con, cap=REPAIR_CAP):
    """adjust_queue 심볼의 전체 3년 이력 재수집(REPLACE). 성공 시 큐에서 제거,
    reason='cliff' 는 cliff_checked 에 기록. 실패는 큐에 남아 다음 실행이 재시도."""
    todo = con.execute(
        "SELECT symbol, reason, detail FROM adjust_queue "
        "ORDER BY reason DESC, queued_at LIMIT ?",   # 'split'>'div'>'cliff' — 분할 최우선
        (cap,)).fetchall()  # attempts 는 _queue_fail 이 관리
    if not todo:
        return 0
    n_left = con.execute("SELECT COUNT(*) FROM adjust_queue").fetchone()[0]
    print(f"[재조정] 큐 {n_left}심볼 중 {len(todo)}개 처리(상한 {cap}) — 전체 이력 재수집")
    # 시작일 = 배치 내 심볼들의 '최고(最古) 저장일' 최솟값 − 7일 여유.
    #   오늘−3년으로 하면 원백필보다 늦게 시작해 경계에 새 가짜 절벽이 남는다(결함 수정 v2).
    fixed = 0
    for i in range(0, len(todo), CHUNK):
        batch = todo[i:i + CHUNK]
        syms = [t[0] for t in batch]
        d0 = con.execute(
            "SELECT MIN(date) FROM daily_ohlcv WHERE symbol IN (%s)"
            % ",".join("?" * len(syms)), syms).fetchone()[0]
        if d0:
            start = (dt.date(int(d0[:4]), int(d0[4:6]), int(d0[6:]))
                     - dt.timedelta(days=7)).isoformat()
        else:
            start = (dt.date.today() - dt.timedelta(days=365 * BACKFILL_YEARS)).isoformat()
        try:
            df = fetch_chunk(syms, start=start)
        except Exception as e:
            print(f"  ⚠️ 재조정 배치 실패(다음 실행 재시도): {e}")
            time.sleep(10)
            continue
        for sym, reason, detail in batch:
            try:
                sub = df[sym].dropna(how="all") if len(syms) > 1 else df
                n = store(con, sub, sym, replace=True)
                if n > 0:
                    con.execute("DELETE FROM adjust_queue WHERE symbol=?", (sym,))
                    if reason == "cliff" and detail:
                        for dd in detail.split(","):
                            con.execute(
                                "INSERT OR IGNORE INTO cliff_checked VALUES (?,?)",
                                (sym, dd))
                    fixed += 1
                else:
                    _queue_fail(con, sym, reason, detail)
            except Exception:
                _queue_fail(con, sym, reason, detail)  # 3회 후 포기(상폐 등)
        con.commit()
        time.sleep(SLEEP_BETWEEN)
    print(f"[재조정] {fixed}심볼 완료 · 잔여 {n_left - fixed}")
    return fixed


def self_test():
    """오프라인 검증 — 네트워크 0. 픽스처로 감지·스캔·저장·큐 수명주기 확인."""
    import pandas as pd
    print("== self-test (오프라인) ==")
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  {'OK ' if cond else 'FAIL'} {label}")
        ok &= cond

    idx = pd.to_datetime(["2026-08-10", "2026-08-11"])
    base = dict(Open=[1.0, 1.0], High=[1.0, 1.0], Low=[1.0, 1.0],
                Close=[91.43, 45.53], Volume=[100, 200])
    sub_split = pd.DataFrame({**base, "Adj Close": [91.43, 45.53],
                              "Dividends": [0.0, 0.0], "Stock Splits": [0.0, 2.0]}, index=idx)
    sub_div = pd.DataFrame({**base, "Adj Close": [91.43, 45.53],
                            "Dividends": [0.0, 0.5], "Stock Splits": [0.0, 0.0]}, index=idx)
    sub_none = pd.DataFrame({**base, "Adj Close": [91.43, 45.53]}, index=idx)
    check("detect: split", detect_events(sub_split) == "split")
    check("detect: div", detect_events(sub_div) == "div")
    check("detect: actions 컬럼 없으면 None(구버전 호환)", detect_events(sub_none) is None)

    con = sqlite3.connect(":memory:")
    for d in DDL:
        con.execute(d)
    # MNST형 가짜 절벽(2:1) · 정상 -30% 급락 · 사전 검사완료 절벽
    rows = [("MNSX", "20260810", 0,0,0, 91.43, 91.43, 1),
            ("MNSX", "20260811", 0,0,0, 45.53, 45.53, 1),
            ("CRSH", "20260810", 0,0,0, 100.0, 100.0, 1),
            ("CRSH", "20260811", 0,0,0, 70.0, 70.0, 1),
            ("SEEN", "20260810", 0,0,0, 50.0, 50.0, 1),
            ("SEEN", "20260811", 0,0,0, 25.0, 25.0, 1)]
    con.executemany("INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?)", rows)
    con.execute("INSERT INTO cliff_checked VALUES ('SEEN','20260811')")
    cl = scan_cliffs(con)
    check("scan: 2:1 절벽 검출", ("MNSX", "20260811", 0.498) in cl)
    check("scan: 정상 -30%는 미검출", not any(c[0] == "CRSH" for c in cl))
    check("scan: 검사완료 절벽 재등록 안 함", not any(c[0] == "SEEN" for c in cl))

    # store: IGNORE 는 기존 행 보존, REPLACE 는 덮어씀
    fixed = pd.DataFrame({**base, "Adj Close": [45.715, 45.53]}, index=idx)
    store(con, fixed, "MNSX")                      # IGNORE — 변화 없어야
    v = con.execute("SELECT adj_close FROM daily_ohlcv WHERE symbol='MNSX' AND date='20260810'").fetchone()[0]
    check("store IGNORE: 기존 행 보존(0-diff)", abs(v - 91.43) < 1e-9)
    store(con, fixed, "MNSX", replace=True)        # REPLACE — 소급 덮어쓰기
    v = con.execute("SELECT adj_close FROM daily_ohlcv WHERE symbol='MNSX' AND date='20260810'").fetchone()[0]
    check("store REPLACE: 소급 조정 반영", abs(v - 45.715) < 1e-9)
    n = con.execute("SELECT COUNT(*) FROM daily_ohlcv WHERE symbol='MNSX'").fetchone()[0]
    check("REPLACE 후 행수 불변(중복 없음)", n == 2)

    # 큐 등록 idempotent
    for _ in range(2):
        con.execute("INSERT OR IGNORE INTO adjust_queue VALUES ('MNSX','cliff','20260811','t',0)")
    check("queue: 중복 등록 차단", con.execute("SELECT COUNT(*) FROM adjust_queue").fetchone()[0] == 1)
    # 실패 3회 → 포기(큐 제거 + checked 표기, 다중 절벽 detail 전부)
    for _ in range(3):
        _queue_fail(con, "MNSX", "cliff", "20260811,20260812")
    check("queue: 3회 실패 시 포기·큐 제거", con.execute(
        "SELECT COUNT(*) FROM adjust_queue WHERE symbol='MNSX'").fetchone()[0] == 0)
    check("queue: 포기한 절벽 checked 표기(다중 날짜 전부)", con.execute(
        "SELECT COUNT(*) FROM cliff_checked WHERE symbol='MNSX'").fetchone()[0] == 2)
    print("✅ self-test 통과" if ok else "❌ self-test 실패")
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true", help="3년 백필(재개 가능)")
    ap.add_argument("--retry-empty", action="store_true",
                    help="완료표시됐지만 데이터 0인 심볼 재시도(rate limit 구멍 메움)")
    ap.add_argument("--limit", type=int, default=0, help="이번 실행 최대 심볼 수(테스트/분할용)")
    ap.add_argument("--self-test", action="store_true", help="오프라인 검증(네트워크 0)")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return
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

    if args.backfill or args.retry_empty:
        done = {s for (s,) in con.execute("SELECT symbol FROM backfill_done")}
        if args.retry_empty:
            # rate limit 등으로 '완료 표시됐지만 데이터 0'인 심볼 재시도
            #   (2026-07-12 백필 실측: PTCT 가 rate limit 에 걸린 채 done 처리됨)
            have = {s for (s,) in con.execute("SELECT DISTINCT symbol FROM daily_ohlcv")}
            todo = [s for s in symbols if s in done and s not in have]
        else:
            todo = [s for s in symbols if s not in done]
        if args.limit:
            todo = todo[:args.limit]
        print(f"[백필{'·재시도' if args.retry_empty else ''}] 남은 {len(todo)}심볼 "
              f"(완료 {len(done)}). 중단돼도 재실행하면 이어받음.")
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
        #   + actions=True 로 창 안의 분할/배당 이벤트 감지(v2026-08-21 — 헤더 참조)
        total, n_evt = 0, 0
        now = dt.datetime.now().isoformat(timespec="seconds")
        for i in range(0, len(symbols), CHUNK):
            chunk = symbols[i:i + CHUNK]
            try:
                df = fetch_chunk(chunk, period="7d", actions=True)
            except Exception as e:
                print(f"  ⚠️ 배치 {i//CHUNK} 실패 건너뜀: {e}")
                time.sleep(10)
                continue
            for s in chunk:
                try:
                    sub = df[s].dropna(how="all") if len(chunk) > 1 else df
                    total += store(con, sub, s)
                    ev = detect_events(sub)
                    if ev:
                        con.execute(
                            "INSERT OR IGNORE INTO adjust_queue VALUES (?,?,?,?,0)",
                            (s, ev, "", now))
                        n_evt += 1
                except Exception:
                    pass
            con.commit()
            time.sleep(SLEEP_BETWEEN)
        print(f"증분 완료: 신규 {total}행 · 이벤트 감지 {n_evt}심볼")

        # 절벽 스캔(기존 오염 자가치유 — 오탐 무해, cliff_checked 로 반복 차단)
        try:
            cliffs = scan_cliffs(con)
            by_sym = {}
            for sym, d, r in cliffs:
                by_sym.setdefault(sym, []).append(d)
            for sym, dates in by_sym.items():   # 심볼당 1행, 절벽 전부 detail 에(결함 수정 v2)
                con.execute("INSERT OR IGNORE INTO adjust_queue VALUES (?,?,?,?,0)",
                            (sym, "cliff", ",".join(sorted(dates)), now))
            con.commit()
            if cliffs:
                print(f"[절벽 스캔] 의심 {len(cliffs)}건 큐 등록 "
                      f"(예: {', '.join(f'{c[0]}@{c[1]}' for c in cliffs[:5])})")
        except Exception as e:
            print(f"  ⚠️ 절벽 스캔 실패(비치명): {e}")

        # 큐 처리 — 심볼 전체 이력 재수집(REPLACE), 회당 상한
        try:
            process_queue(con)
        except Exception as e:
            print(f"  ⚠️ 재조정 처리 실패(비치명 — 큐 잔류, 다음 실행 재시도): {e}")

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
