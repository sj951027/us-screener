# -*- coding: utf-8 -*-
"""
us_options_collector.py — 옵션 체인 요약 스냅샷 → us_options.db `option_daily`
==============================================================================
[2026-08-30 신설 · 데이터 확장 4차] 옵션 시장 지표(풋/콜 · ATM 내재변동성 · 스큐)는
'큰돈의 방향 베팅' 전조 후보 재료다. 관측 전용 — 점수 미투입, 검증은 PREREGISTER 후.

왜 지금부터: 옵션 데이터는 **과거 이력을 무료로 소급할 수 없다** — 오늘부터 쌓아야
  1년 뒤에나 시험 가능(2026-08-30 결정, 사용자 승인). 원본 체인이 아니라 요약
  지표만 저장(하루 ~1,000행 · 행당 ~100B → 연 ~10MB 수준, 용량 부담 미미).

무엇을: 유동성 상위 N(기본 500, 가드 close≥$5) ∪ 관측 모델 종목(us_mus_v0 top50 ·
  us_rvdtc_a 풀)의 근월 1~2개 만기(5≤DTE≤75)에 대해
  call/put OI·거래량, ATM IV(콜·풋), 스큐(0.9×현물가 풋 IV − ATM 풋 IV).

정직성·한계(문서화):
  - PIT 은 스냅샷 시각 기준(재작성 없음). 단 **OI 는 전 거래일 마감분**(OCC 익일
    아침 갱신)이고 **IV 는 마감후 호가 기반이라 잡음** — 지표는 절대값이 아니라
    횡단면 랭킹용으로만 쓸 것.
  - 커버리지가 유동성 상위 편중 — 전 유니버스 팩터가 아니라 top 풀 대상 틸트 재료.

무게 관리: 휴장일 가드(us_ohlcv 최신일 ≠ 오늘 ET → 생략, --force 무시) ·
  심볼당 비치명(실패 집계만) · 시간예산(기본 35분). **스냅샷은 소급 불가라 예산에
  잘린 꼬리는 그 날짜분이 영구 결손이다** — 같은 날 재실행만 이어받고(PK REPLACE),
  다음 날은 다른 날짜다. 만성 결손 방지: 매 실행 snapshot_log 에 실측 속도를 남기고
  다음 실행이 그 속도로 계획을 예산 안에 자동 축소(모델 종목 우선이라 안 잘림) —
  잘리는 일이 반복되지 않고, 커버리지는 '유동성 상위 일관 프리픽스'로 유지된다.

사용:
    python us_options_collector.py               # 가드 하에 스냅샷
    python us_options_collector.py --force       # 휴장일 가드 무시
    python us_options_collector.py --max-symbols 200
    python us_options_collector.py --self-test   # 오프라인 검증(네트워크 0)
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("US_DATA_DIR", "").strip() or (HERE / ".." / "us-screener-data"))
DB = DATA_DIR / "us_options.db"
OHLCV_DB = DATA_DIR / "us_ohlcv.db"

TOP_N = 500
DTE_MIN, DTE_MAX = 5, 75
MAX_EXPIRIES = 2
BUDGET_SEC = 2100
SLEEP_SEC = 0.2

DDL = [
    """CREATE TABLE IF NOT EXISTS option_daily (
        date TEXT NOT NULL, symbol TEXT NOT NULL, expiry TEXT NOT NULL,
        dte INTEGER, spot REAL,
        call_oi REAL, put_oi REAL, call_vol REAL, put_vol REAL,
        atm_iv_call REAL, atm_iv_put REAL, skew_iv REAL, n_strikes INTEGER,
        PRIMARY KEY (date, symbol, expiry))""",
    "CREATE INDEX IF NOT EXISTS ix_opt_sym_date ON option_daily(symbol, date)",
    """CREATE TABLE IF NOT EXISTS snapshot_log (
        date TEXT PRIMARY KEY, planned INTEGER, done INTEGER, fail INTEGER,
        seconds REAL, cut INTEGER)""",
]


def et_today():
    return dt.datetime.now(ZoneInfo("America/New_York")).strftime("%Y%m%d")


def pick_universe(ohlcv_con, top_n=TOP_N):
    """유동성 상위 top_n(가드 close≥$5) ∪ 관측 모델 종목. 실패 시 빈 리스트(비치명)."""
    last = ohlcv_con.execute("SELECT MAX(date) FROM daily_ohlcv").fetchone()[0]
    if not last:
        return [], None
    d20 = [r[0] for r in ohlcv_con.execute(
        "SELECT DISTINCT date FROM daily_ohlcv WHERE date<=? ORDER BY date DESC LIMIT 20",
        (last,))]
    top = [r[0] for r in ohlcv_con.execute(f"""
        SELECT symbol FROM daily_ohlcv
        WHERE date IN ({",".join("?" * len(d20))})
        GROUP BY symbol
        HAVING MAX(CASE WHEN date=? THEN close END) >= 5
        ORDER BY AVG(close * volume) DESC LIMIT ?""", (*d20, last, top_n))]
    models = [r[0] for r in ohlcv_con.execute("""
        SELECT DISTINCT symbol FROM score_daily s
        WHERE date = (SELECT MAX(date) FROM score_daily WHERE model=s.model)
          AND ((model='us_mus_v0' AND rank<=50) OR model='us_rvdtc_a')""")]
    seen, out = set(), []
    # 시간예산 부분 종료 시에도 관측 모델 종목이 잘리지 않게 모델 먼저(v05 리뷰에서 발견·수정)
    for s in models + top:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out, last


def compute_metrics(spot, calls, puts):
    """체인 DataFrame(strike/openInterest/volume/impliedVolatility) → 요약 dict.
    빈 체인이나 현물가 불량이면 None. IV 는 0.01~5 범위만 신뢰(호가 잡음 방어)."""
    import pandas as pd  # noqa: F401 (호출측 의존 명시)
    if calls is None or puts is None or len(calls) == 0 or len(puts) == 0:
        return None
    if not (spot and spot > 0):
        return None

    def col(df, name):
        return df[name] if name in df.columns else None

    def iv_at(df, target):
        st, iv = col(df, "strike"), col(df, "impliedVolatility")
        if st is None or iv is None or len(df) == 0:
            return None
        j = (st - target).abs().idxmin()
        v = iv.loc[j]
        return float(v) if (v == v and 0.01 < v < 5) else None

    def tot(df, name):
        c = col(df, name)
        return float(c.fillna(0).sum()) if c is not None else 0.0

    atm_c = iv_at(calls, spot)
    atm_p = iv_at(puts, spot)
    otm_p = iv_at(puts, spot * 0.9)
    return {
        "call_oi": tot(calls, "openInterest"), "put_oi": tot(puts, "openInterest"),
        "call_vol": tot(calls, "volume"), "put_vol": tot(puts, "volume"),
        "atm_iv_call": atm_c, "atm_iv_put": atm_p,
        "skew_iv": (otm_p - atm_p) if (otm_p is not None and atm_p is not None) else None,
        "n_strikes": int(len(calls) + len(puts)),
    }


def snapshot_symbol(yf, con, sym, today8):
    """한 심볼의 근월 만기 요약 적재. 성공 만기 수 반환(실패=0, 비치명)."""
    t = yf.Ticker(sym)
    exps = list(t.options or [])
    todo = []
    td = dt.date(int(today8[:4]), int(today8[4:6]), int(today8[6:]))
    for e in exps:
        try:
            ed = dt.date.fromisoformat(e)
        except ValueError:
            continue
        dte = (ed - td).days
        if DTE_MIN <= dte <= DTE_MAX:
            todo.append((e, dte))
        if len(todo) >= MAX_EXPIRIES:
            break
    n = 0
    for e, dte in todo:
        ch = t.option_chain(e)
        hist = t.fast_info
        spot = None
        try:
            spot = float(hist["lastPrice"])
        except Exception:
            pass
        if not spot:
            try:
                spot = float(ch.underlying.get("regularMarketPrice"))
            except Exception:
                continue
        m = compute_metrics(spot, ch.calls, ch.puts)
        if m is None:
            continue
        con.execute("""INSERT OR REPLACE INTO option_daily VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (today8, sym, e, dte, spot, m["call_oi"], m["put_oi"],
                     m["call_vol"], m["put_vol"], m["atm_iv_call"], m["atm_iv_put"],
                     m["skew_iv"], m["n_strikes"]))
        n += 1
    return n


def plan_capacity(attempted, seconds, budget=BUDGET_SEC, floor=100):
    """직전 실측 처리량 → 오늘 예산 안에서 완주 가능한 심볼 수(10% 마진).
    실측 정보가 없거나 표본이 작으면 None(축소 없음)."""
    if not attempted or not seconds or seconds <= 0 or attempted < 10:
        return None
    return max(floor, int(attempted / seconds * budget * 0.9))


def run(force=False, max_symbols=TOP_N):
    try:
        import yfinance as yf
    except ImportError:
        print("⚠️ yfinance 없음 — 생략(비치명). pip install yfinance")
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ocon = sqlite3.connect(f"file:{OHLCV_DB}?mode=ro", uri=True)
    universe, last_ohlcv = pick_universe(ocon, max_symbols)
    ocon.close()
    today8 = et_today()
    if not universe:
        print("⚠️ 유니버스 산출 실패(us_ohlcv 없음?) — 생략(비치명)")
        return
    if last_ohlcv != today8 and not force:
        print(f"⏭  옵션 스냅샷 생략 — ohlcv 최신일 {last_ohlcv} ≠ 오늘 ET {today8}"
              f"(휴장일/수집 전). --force 로 무시 가능")
        return
    con = sqlite3.connect(DB)
    for ddl in DDL:
        con.execute(ddl)
    prev = con.execute("""SELECT planned-cut, seconds FROM snapshot_log
                          WHERE date<? ORDER BY date DESC LIMIT 1""", (today8,)).fetchone()
    cap = plan_capacity(prev[0], prev[1]) if prev else None
    if cap and cap < len(universe):
        print(f"  ↘ 직전 실측 속도로 계획 축소 {len(universe)} → {cap}심볼"
              f"(예산 내 완주 우선 · 모델 종목은 목록 앞이라 유지)")
        universe = universe[:cap]
    print(f"▶ 옵션 스냅샷 {today8} — 대상 {len(universe)}심볼"
          f"(유동성 top{max_symbols} ∪ 관측 모델), 만기 {DTE_MIN}~{DTE_MAX}일 최대 {MAX_EXPIRIES}개")
    t0 = time.time()
    ok = fail = 0
    cut = 0
    for i, sym in enumerate(universe):
        if time.time() - t0 > BUDGET_SEC:
            cut = len(universe) - i
            print(f"  ⚠️ 시간예산 {BUDGET_SEC}s 초과 — {cut}심볼 미수집. 스냅샷은 소급"
                  f" 불가라 이 날짜분 꼬리는 영구 결손. 다음 실행 계획이 실측 속도로 자동 축소됨")
            break
        try:
            n = snapshot_symbol(yf, con, sym, today8)
            ok += 1 if n else 0
        except Exception:
            fail += 1
        if i % 50 == 0:
            con.commit()
        time.sleep(SLEEP_SEC)
    con.commit()
    el = time.time() - t0
    con.execute("INSERT OR REPLACE INTO snapshot_log VALUES (?,?,?,?,?,?)",
                (today8, len(universe), ok, fail, el, cut))
    con.commit()
    rows, days = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT date) FROM option_daily").fetchone()
    print(f"💾 option_daily 오늘 성공 {ok}심볼 · 실패 {fail} · 잘림 {cut} · "
          f"누적 {rows:,}행/{days}일 ({el:.0f}s)")
    print("✅ 옵션 스냅샷 — 관측 전용. OI=전일 마감분, IV=마감후 호가(랭킹용) 한계 유의.")
    con.close()


def self_test():
    import pandas as pd
    print("== self-test (오프라인) ==")
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  {'OK ' if cond else 'FAIL'} {label}")
        ok &= cond

    calls = pd.DataFrame({"strike": [90, 100, 110], "openInterest": [10, 20, None],
                          "volume": [1, 2, 3], "impliedVolatility": [0.35, 0.30, 0.28]})
    puts = pd.DataFrame({"strike": [80, 90, 100], "openInterest": [5, 15, 25],
                         "volume": [4, 5, None], "impliedVolatility": [0.45, 0.38, 0.33]})
    m = compute_metrics(100.0, calls, puts)
    check("합계: OI/거래량 (결측=0)", m["call_oi"] == 30 and m["put_oi"] == 45
          and m["call_vol"] == 6 and m["put_vol"] == 9)
    check("ATM IV: 현물가 최근접 행사가", m["atm_iv_call"] == 0.30 and m["atm_iv_put"] == 0.33)
    check("스큐 = IV(0.9S 풋) − IV(ATM 풋)", abs(m["skew_iv"] - (0.38 - 0.33)) < 1e-9)
    bad = pd.DataFrame({"strike": [100], "openInterest": [1], "volume": [1],
                        "impliedVolatility": [9.9]})   # 범위 밖 IV → 불신
    m2 = compute_metrics(100.0, bad, bad)
    check("IV 범위(0.01~5) 밖 → None (스큐도 None)", m2["atm_iv_call"] is None
          and m2["skew_iv"] is None)
    check("빈 체인 → None", compute_metrics(100.0, pd.DataFrame(), puts) is None)
    check("현물가 불량 → None", compute_metrics(0, calls, puts) is None)

    con = sqlite3.connect(":memory:")
    for ddl in DDL:
        con.execute(ddl)
    row = ("20260830", "TEST", "2026-09-18", 19, 100.0, 30, 45, 6, 9, 0.3, 0.33, 0.05, 6)
    for _ in range(2):
        con.execute("INSERT OR REPLACE INTO option_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
    check("idempotent: PK REPLACE 재적재 후 1행",
          con.execute("SELECT COUNT(*) FROM option_daily").fetchone()[0] == 1)

    check("plan_capacity: 300심볼/1500s → 예산 내 완주 수",
          plan_capacity(300, 1500) == max(100, int(300 / 1500 * BUDGET_SEC * 0.9)))
    check("plan_capacity: 표본 부족/정보 없음 → None",
          plan_capacity(5, 100) is None and plan_capacity(None, None) is None)
    check("plan_capacity: 최저 100 보장", plan_capacity(20, 100000) == 100)
    check("snapshot_log 테이블 생성",
          con.execute("SELECT COUNT(*) FROM snapshot_log").fetchone()[0] == 0)

    # 유니버스: 픽스처 ohlcv/score_daily 로 가드·합집합 확인
    o = sqlite3.connect(":memory:")
    o.execute("CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, close REAL, volume REAL)")
    o.execute("CREATE TABLE score_daily (model TEXT, date TEXT, symbol TEXT, rank INT)")
    for sym, c, v in [("BIG", 100, 1e7), ("MID", 50, 1e6), ("PENNY", 2, 1e9)]:
        o.execute("INSERT INTO daily_ohlcv VALUES (?,?,?,?)", (sym, "20260828", c, v))
    o.execute("INSERT INTO score_daily VALUES ('us_mus_v0','20260828','MODELPICK',10)")
    uni, last = pick_universe(o, top_n=2)
    check("유니버스: $5 가드로 PENNY 제외 · 모델 종목 합집합",
          "PENNY" not in uni and "MODELPICK" in uni and "BIG" in uni and last == "20260828")
    print("✅ self-test 통과" if ok else "❌ self-test 실패")
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(description="옵션 체인 요약 스냅샷(관측 전용)")
    ap.add_argument("--force", action="store_true", help="휴장일 가드 무시")
    ap.add_argument("--max-symbols", type=int, default=TOP_N)
    ap.add_argument("--self-test", action="store_true", help="오프라인 검증")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return
    try:
        run(force=args.force, max_symbols=args.max_symbols)
    except Exception as e:
        print(f"⚠️  옵션 스냅샷 실패(비치명 — 다음 실행에서 재시도): {e}")


if __name__ == "__main__":
    main()
