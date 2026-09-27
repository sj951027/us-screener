"""
us_analyst_collector.py — 애널리스트 추정치 스냅샷 + 등급·목표가 변경 이력 (v20, 2026-09-27, 관측 전용 · 점수 미투입)

왜: 가격 재조합으로는 개선이 안 나왔고(3회), 새 알파는 가격과 직교한 데이터에서만 기대된다(CLAUDE.md "반복 확인된 결론").
  애널리스트 추정치·등급 변화는 이 프로젝트에서 아직 안 본 재료다. 외부 문헌상 강한 신호로 알려져 있으나 여기서는 가설.

두 갈래(yfinance, 실측 2026-09-27 AAPL·LITE):
  ① eps_snapshot — eps_trend(현재/7·30·60·90일 전 EPS 추정) + eps_revisions(최근 7·30일 상향/하향 수) + 애널리스트 수.
     **현재값만 온다 → 소급 불가.** 오늘부터 매 세션 쌓아야 1년 뒤 쓸 수 있다(옵션 스냅샷과 같은 성격). 유동성 상위 + 관측 모델 종목.
  ② rating_changes — upgrades_downgrades: 증권사별 등급·목표가 변경 **이력 전체가 시각과 함께 온다**(AAPL 2012~ 972건).
     → 소급 가능. 회당 HIST_PER_RUN 종목씩 순환하며 받고, REFRESH_DAYS 가 지나면 다시 받아 새 이벤트를 추가(INSERT OR IGNORE).
PIT: rating_changes.ts = 이벤트 시각(그대로 사용). eps_snapshot.date = 세션 날짜(us_calendar, 실제 수집은 다음 날 새벽 ET) —
  연구에서는 **그 다음 거래일 앵커부터** 쓴다(스냅샷에 마감 뒤 변경이 섞일 수 있음).
비치명: 종목별 예외는 건너뛰고 세고, 연속 실패가 STOP_AFTER_FAILS 에 닿으면 중단(차단 의심). 시간 예산 BUDGET_SEC.

사용:
    python us_analyst_collector.py                 # 스냅샷(휴장일이면 생략) + 이력 순환
    python us_analyst_collector.py --self-test     # 오프라인 파서·저장 검증(네트워크 0)
"""
import argparse
import datetime as dt
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("US_DATA_DIR", "").strip() or (HERE / ".." / "us-screener-data"))
DB = DATA_DIR / "us_analyst.db"
OHLCV_DB = DATA_DIR / "us_ohlcv.db"

SNAP_TOP_N = 500          # 스냅샷 대상: 유동성 상위 500 ∪ 관측 모델 종목(옵션 첫 적재와 같은 보수적 시작 — 실측 뒤 확대)
HIST_PER_RUN = 300        # 이력 순환: 회당 종목 수(가드 유니버스 ~3,400 → 약 12회에 한 바퀴)
REFRESH_DAYS = 30         # 이력을 다시 받는 주기(새 등급 변경 추가) — 월 1회면 이벤트 누락 없이 요청량을 줄인다
BUDGET_SEC = 1200         # 전체 시간 예산(20분) — 스냅샷 먼저(소급 불가), 남는 시간에 이력
STOP_AFTER_FAILS = 40     # 연속 실패가 이만큼이면 차단 의심으로 중단(나머지는 다음 실행)
PAUSE_SEC = 0.3           # 종목 간 대기

DDL = [
    """CREATE TABLE IF NOT EXISTS eps_snapshot (
        date TEXT NOT NULL, symbol TEXT NOT NULL, period TEXT NOT NULL,
        eps_cur REAL, eps_7d REAL, eps_30d REAL, eps_60d REAL, eps_90d REAL,
        up7 INTEGER, up30 INTEGER, down7 INTEGER, down30 INTEGER, n_analysts INTEGER,
        PRIMARY KEY (date, symbol, period))""",
    """CREATE TABLE IF NOT EXISTS rating_changes (
        symbol TEXT NOT NULL, ts TEXT NOT NULL, firm TEXT NOT NULL,
        to_grade TEXT, from_grade TEXT, action TEXT,
        pt_action TEXT, pt_current REAL, pt_prior REAL,
        PRIMARY KEY (symbol, ts, firm))""",
    "CREATE INDEX IF NOT EXISTS ix_rating_ts ON rating_changes(ts)",
    "CREATE TABLE IF NOT EXISTS history_done (symbol TEXT PRIMARY KEY, fetched TEXT, n INTEGER)",
    """CREATE TABLE IF NOT EXISTS run_log (
        date TEXT PRIMARY KEY, snap_ok INTEGER, snap_fail INTEGER, hist_ok INTEGER, hist_fail INTEGER, seconds REAL)""",
]


def _num(v):
    try:
        v = float(v)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def parse_eps(trend, revisions, estimate):
    """(period, eps_cur, eps_7d, eps_30d, eps_60d, eps_90d, up7, up30, down7, down30, n_analysts) 목록.
    세 표 모두 index = 기간('0q','+1q','0y','+1y'). 열 이름은 yfinance 1.x 실측(대소문자 섞임 — downLast7Days)."""
    out = []
    if trend is None or not hasattr(trend, "index") or len(trend) == 0:
        return out
    for per in trend.index:
        t = trend.loc[per]
        r = revisions.loc[per] if revisions is not None and hasattr(revisions, "index") and per in revisions.index else {}
        e = estimate.loc[per] if estimate is not None and hasattr(estimate, "index") and per in estimate.index else {}
        g = (lambda row, *ks: next((_num(row.get(k)) for k in ks if hasattr(row, "get") and k in row), None))
        n_an = g(e, "numberOfAnalysts")
        out.append((str(per), g(t, "current"), g(t, "7daysAgo"), g(t, "30daysAgo"), g(t, "60daysAgo"), g(t, "90daysAgo"),
                    g(r, "upLast7days"), g(r, "upLast30days"), g(r, "downLast7Days", "downLast7days"), g(r, "downLast30days"),
                    int(n_an) if n_an is not None else None))
    return out


def parse_ud(df, now=None):
    """upgrades_downgrades(index=시각) → (ts ISO, firm, to, from, action, pt_action, pt_current, pt_prior) 목록.
    수집 시각보다 뒤의 이벤트는 버린다 — 실측 AMR 2026-10-05(수집 09-27) 같은 미래 시각이 섞이면 연구에 미래 정보가 된다."""
    out = []
    if df is None or not hasattr(df, "iterrows") or len(df) == 0:
        return out
    limit = (now or dt.datetime.utcnow()).strftime("%Y-%m-%dT%H:%M:%S")
    for ts, r in df.iterrows():
        firm = str(r.get("Firm") or "").strip()
        iso = str(ts)[:19].replace(" ", "T")
        if not firm or iso > limit:
            continue
        out.append((iso, firm, r.get("ToGrade") or None, r.get("FromGrade") or None,
                    r.get("Action") or None, r.get("priceTargetAction") or None,
                    _num(r.get("currentPriceTarget")), _num(r.get("priorPriceTarget"))))
    return out


def _soft_fail(tag):
    """v21: 비치명으로 삼킨 실패를 워크플로 실패 알림에 알린다(GITHUB_ENV 에 SOFT_FAIL_<tag>=1). 로컬 실행에선 아무것도 안 함.
    스크립트가 예외를 삼키고 exit 0 으로 끝나면 continue-on-error 스텝의 outcome 이 success 라 알림 조건이 못 잡았다."""
    p = os.environ.get("GITHUB_ENV")
    if p:
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(f"SOFT_FAIL_{tag}=1\n")


def history_queue(con, symbols, today, limit=None):
    """이력을 받을 종목: 한 번도 안 받은 것 → 오래전에 받은 것 순. REFRESH_DAYS 안에 받은 것은 제외."""
    limit = HIST_PER_RUN if limit is None else limit
    done = dict(con.execute("SELECT symbol, fetched FROM history_done"))
    cutoff = (today - dt.timedelta(days=REFRESH_DAYS)).isoformat()
    todo = [s for s in symbols if done.get(s, "") < cutoff]
    todo.sort(key=lambda s: (s in done, done.get(s, ""), s))
    return todo[:limit]


def guard_universe():
    """가드 통과 유니버스 = 최신 us_mus_v0 기록 종목(점수 매겨진 종목)."""
    if not OHLCV_DB.exists():
        return []
    c = sqlite3.connect(f"file:{OHLCV_DB.as_posix()}?mode=ro", uri=True)
    try:
        return [s for (s,) in c.execute(
            "SELECT symbol FROM score_daily WHERE model='us_mus_v0' AND date=(SELECT MAX(date) FROM score_daily WHERE model='us_mus_v0') ORDER BY rank")]
    except sqlite3.OperationalError:
        return []
    finally:
        c.close()


def run(force=False):
    try:
        import yfinance as yf
    except ImportError:
        print("⚠️ yfinance 없음 — 생략(비치명)")
        return
    from us_calendar import session_date
    from us_options_collector import pick_universe   # 유동성 상위 ∪ 관측 모델(거래일 정의 공용)
    # ETF·우선주 등 '자료 없음(404)' 경고가 종목마다 찍혀 로그를 덮는다(실측) — 결과는 성공/없음 카운트로 요약
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    for d in DDL:
        con.execute(d)
    t0, today8 = time.time(), session_date()
    ok_s = fail_s = ok_h = fail_h = 0

    # ① 스냅샷 — 소급 불가라 먼저. 휴장일(시세 최신 거래일 ≠ 세션 날짜)이면 생략
    oc = sqlite3.connect(f"file:{OHLCV_DB.as_posix()}?mode=ro", uri=True) if OHLCV_DB.exists() else None
    snap_syms, last = pick_universe(oc, SNAP_TOP_N) if oc else ([], None)
    if oc:
        oc.close()
    if last != today8 and not force:
        print(f"⏭ 애널리스트 스냅샷 생략 — 시세 최신 거래일 {last} ≠ 세션 {today8}(휴장일/수집 전)")
        snap_syms = []
    elif con.execute("SELECT 1 FROM eps_snapshot WHERE date=? LIMIT 1", (today8,)).fetchone():
        print(f"⏭ 애널리스트 스냅샷 {today8} 이미 있음(idempotent)")
        snap_syms = []
    streak = 0
    for s in snap_syms:
        if time.time() - t0 > BUDGET_SEC or streak >= STOP_AFTER_FAILS:
            break
        try:
            t = yf.Ticker(s)
            rows = parse_eps(t.eps_trend, t.eps_revisions, t.earnings_estimate)
            if rows:
                con.executemany("INSERT OR IGNORE INTO eps_snapshot VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                [(today8, s) + r for r in rows])
                ok_s += 1
                streak = 0
            else:
                fail_s += 1   # 추정치 없는 종목(소형주)도 있다 — 연속 실패로는 세지 않음
        except Exception:
            fail_s += 1
            streak += 1
        time.sleep(PAUSE_SEC)
    con.commit()
    if snap_syms:
        print(f"💾 eps_snapshot {today8}: 성공 {ok_s} · 없음/실패 {fail_s} (대상 {len(snap_syms)})")
        if ok_s == 0:   # v21: 유동성 상위 종목이 전부 빈 응답 = 차단·형식 변경 의심(빈 표는 예외가 아니라 연속 실패로 안 잡혔다)
            print(f"::error::❌ 애널리스트 스냅샷 {today8} 전부 빈 응답({len(snap_syms)}종목) — 야후 차단/형식 변경 의심")
            _soft_fail("ANALYST")

    # ② 이력 순환 — 남는 시간에
    streak, n_new, done_rows = 0, 0, []
    queue = history_queue(con, guard_universe(), dt.date.today())
    now = dt.date.today().isoformat()
    for s in queue:
        if time.time() - t0 > BUDGET_SEC or streak >= STOP_AFTER_FAILS:
            break
        try:
            rows = parse_ud(yf.Ticker(s).upgrades_downgrades)
            con.executemany("INSERT OR IGNORE INTO rating_changes VALUES (?,?,?,?,?,?,?,?,?)", [(s,) + r for r in rows])
            done_rows.append((s, now, len(rows)))
            n_new += len(rows)
            ok_h += 1
            streak = 0
        except Exception:
            fail_h += 1
            streak += 1
        time.sleep(PAUSE_SEC)
    # v21: 이번에 받은 이력이 전부 0건이면(차단 때 빈 표) '받음'으로 기록하지 않는다 — 기록하면 30일간 다시 안 받는다
    all_empty = ok_h >= 20 and n_new == 0
    if all_empty:
        print(f"::error::❌ 등급 이력 {ok_h}종목 전부 0건 — 야후 차단/형식 변경 의심, history_done 미기록(다음 실행 재시도)")
        _soft_fail("ANALYST")
    else:
        con.executemany("INSERT OR REPLACE INTO history_done VALUES (?,?,?)", done_rows)
    secs = time.time() - t0
    con.execute("INSERT OR REPLACE INTO run_log VALUES (?,?,?,?,?,?)", (today8, ok_s, fail_s, ok_h, fail_h, round(secs, 1)))
    con.commit()
    n_r, n_s = con.execute("SELECT COUNT(*), COUNT(DISTINCT symbol) FROM rating_changes").fetchone()
    n_done = con.execute("SELECT COUNT(*) FROM history_done").fetchone()[0]
    con.close()
    print(f"💾 rating_changes: 이번 {ok_h}종목(실패 {fail_h}) · 누적 {n_r:,}건/{n_s:,}종목 · 이력 받은 종목 {n_done:,} ({secs:.0f}s)")
    if streak >= STOP_AFTER_FAILS:
        print(f"::error::❌ 연속 {STOP_AFTER_FAILS}회 실패로 중단 — 야후 차단/크럼 의심(비치명, 다음 실행 재시도)")
        _soft_fail("ANALYST")
    print("✅ 애널리스트 — 관측 전용(점수 미투입). 스냅샷은 다음 거래일 앵커부터 사용.")


def self_test():
    import pandas as pd
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  {'OK ' if cond else 'FAIL'} {label}")
        ok &= bool(cond)

    print("== self-test (오프라인) ==")
    idx = ["0q", "+1q", "0y", "+1y"]
    trend = pd.DataFrame({"current": [1.5, 1.7, 7.4, 8.2], "7daysAgo": [1.5, 1.7, 7.3, 8.1], "30daysAgo": [1.4, 1.6, 7.2, 8.0],
                          "60daysAgo": [1.4, 1.6, 7.1, 7.9], "90daysAgo": [1.3, 1.5, 7.0, 7.8], "currency": "USD"}, index=idx)
    rev = pd.DataFrame({"upLast7days": [2, 1, 3, 2], "upLast30days": [5, 4, 8, 6], "downLast30days": [1, 0, 1, 2],
                        "downLast7Days": [0, 0, 1, 0], "currency": "USD"}, index=idx)
    est = pd.DataFrame({"avg": [1.5, 1.7, 7.4, 8.2], "numberOfAnalysts": [30, 28, 40, 38]}, index=idx)
    rows = parse_eps(trend, rev, est)
    check("eps: 기간 4행 · 열 11개", len(rows) == 4 and all(len(r) == 11 for r in rows))
    check("eps: 0y 현재·30일전·상향30·하향7·애널리스트수", rows[2] == ("0y", 7.4, 7.3, 7.2, 7.1, 7.0, 3, 8, 1, 1, 40))
    check("eps: 추정치 없으면 빈 목록", parse_eps(pd.DataFrame(), None, None) == [])
    ud = pd.DataFrame({"Firm": ["Morgan Stanley", "Wedbush", ""], "ToGrade": ["Overweight", "Outperform", "x"],
                       "FromGrade": ["Overweight", "Neutral", ""], "Action": ["main", "up", "x"],
                       "priceTargetAction": ["Raises", "Raises", ""], "currentPriceTarget": [305.0, 290.0, 1],
                       "priorPriceTarget": [300.0, float("nan"), 1]},
                      index=pd.to_datetime(["2026-09-23 17:25:04", "2026-09-18 11:45:30", "2026-09-01 00:00:00"]))
    u = parse_ud(ud, now=dt.datetime(2026, 9, 27))
    check("등급: 증권사명 빈 행 제외 · 시각 ISO", len(u) == 2 and u[0][0] == "2026-09-23T17:25:04")
    check("등급: 목표가 NaN → None", u[1][7] is None and u[0][6] == 305.0)
    fut = pd.DataFrame({"Firm": ["X"], "ToGrade": ["Buy"], "FromGrade": [""], "Action": ["init"], "priceTargetAction": [""],
                        "currentPriceTarget": [1.0], "priorPriceTarget": [1.0]}, index=pd.to_datetime(["2026-10-05 18:26:09"]))
    check("등급: 수집 시각보다 뒤(미래) 이벤트는 버림(실측 AMR 2026-10-05)", parse_ud(fut, now=dt.datetime(2026, 9, 27)) == [])
    con = sqlite3.connect(":memory:")
    for d in DDL:
        con.execute(d)
    for _ in range(2):
        con.executemany("INSERT OR IGNORE INTO rating_changes VALUES (?,?,?,?,?,?,?,?,?)", [("AAPL",) + r for r in u])
    check("등급: 재적재 idempotent(2건)", con.execute("SELECT COUNT(*) FROM rating_changes").fetchone()[0] == 2)
    con.executemany("INSERT INTO history_done VALUES (?,?,?)", [("A", "2026-09-20", 1), ("B", "2026-07-01", 1)])
    q = history_queue(con, ["A", "B", "C"], dt.date(2026, 9, 27))
    check("이력 순환: 안 받은 C → 오래된 B, 최근 A 제외", q == ["C", "B"])
    print("✅ self-test 통과" if ok else "❌ self-test 실패")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="휴장일 가드 무시")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        sys.exit(self_test())
    try:
        run(force=a.force)
    except Exception as e:   # 비치명 — 수집 스텝 전체를 멈추지 않는다
        print(f"::error::⚠️ 애널리스트 수집 실패(비치명): {e}")
        _soft_fail("ANALYST")
