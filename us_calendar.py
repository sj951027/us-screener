"""미국 세션 날짜 규칙 — 한 곳에서만 정의한다(v17).

규칙: 러너가 미국 동부(ET) 기준 SESSION_ROLLOVER_H 시 이전에 돌면 '전날 세션'으로 본다.
  v11(2026-09-17)에서 수집을 03:17 UTC(화~토)로 옮기면서 GitHub 지연(실측 ~5시간 13분, 시작 08:30 UTC = 04:30 ET)이
  ET 자정을 넘겨도 같은 거래일로 보게 하려고 도입했다. 그 뒤 같은 규칙이 us_seed·us_options·us_notify_test·
  us_ohlcv(UTC−11 근사)·워크플로(`date -d '-6 hours'`) 다섯 곳에 복제돼 있던 것을 여기로 모았다.
  롤오버 시각을 바꿀 때는 이 파일만 고친다.

주의: 이것은 '달력상 세션 날짜'이지 거래일 앵커가 아니다. 주말·휴장일 실행에서는 비거래일을 돌려준다.
  스냅샷 라벨처럼 거래일이 필요한 곳은 시세 DB 의 최근 거래일을 쓴다(us_rotate_collector.session_anchor).
"""
import datetime as dt

SESSION_ROLLOVER_H = 6   # ET 06시 전 실행 = 전날 세션. 실제 시작 04:30 ET 무렵이라 여유 1.5시간(지연이 6시간 45분을 넘으면 어긋남)


def session_now():
    """ET 현재 시각에서 롤오버만큼 뺀 시각. zoneinfo 가 없으면 UTC−5 근사."""
    back = dt.timedelta(hours=SESSION_ROLLOVER_H)
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York")) - back
    except Exception:
        return dt.datetime.utcnow() - dt.timedelta(hours=5) - back


def session_date():
    """세션 날짜 'YYYYMMDD'."""
    return session_now().strftime("%Y%m%d")


def session_weekday():
    """세션 요일(월=0 … 일=6)."""
    return session_now().weekday()


# ── 거래일 판정(시세 DB 기준) — v18: us_page_data·us_notify_test·us_price_repair·us_ohlcv·us_rotate 에 흩어져 있던 정의 ──
STRAY_FRAC = 0.1        # v09: 행수가 직전 STRAY_WINDOW 관측일 최대의 이 비율 미만인 날 = 휴장일 잔행(실측 20260907 CREG 1행)
STRAY_WINDOW = 20       # v09: 잔행 판정 기준 최대값을 구하는 직전 관측일 수
BASELINE_WINDOW = 20    # v12: 완전성 분모 = 직전 이 관측일 수의 최대 행수


def date_counts(con):
    """날짜별 유효 종가(close>0) 행수, 날짜 오름차순. v18: close>0(us_price_repair)과 close IS NOT NULL(us_page_data·
    us_notify_test) 두 정의를 하나로 — store() 가 v08 부터 close≤0 을 저장하지 않아 차이는 v08 이전 3행(20240614)뿐."""
    return dict(con.execute("SELECT date, COUNT(*) FROM daily_ohlcv WHERE close > 0 GROUP BY date ORDER BY date"))


def index_dates_for(ohlcv_db):
    """v19: 같은 폴더 us_market.db 의 SPX 일봉 날짜 집합 — 거래일 판정용. 파일·테이블이 없거나 실패하면 None."""
    import sqlite3
    from pathlib import Path
    p = Path(str(ohlcv_db)).with_name("us_market.db")
    if not p.exists():
        return None
    try:
        c = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
        s = {d for (d,) in c.execute("SELECT date FROM market_daily WHERE series='SPX'")}
        c.close()
        return s or None
    except Exception:
        return None


def is_trading_day(d, n, recent_max, index_dates=None):
    """v19: 거래일인가. 지수(SPX) 일봉 기간 안이면 '지수 봉이 있는 날'로 판정한다 — 휴장일 잔행(지수 봉 없음)과
    심한 부분 수집일(지수 봉 있음)을 매직넘버 없이 가른다. 실측: 09-23 실행 때 20260922 가 ~400행(6%)이라 v09 10% 규칙이
    잔행으로 오분류 → 최신일이 9/21 로 남아 9/21 점수를 두 번 계산했다. 지수 기간 밖(수집 실패·초기)은 v09 10% 규칙."""
    if index_dates and min(index_dates) <= d <= max(index_dates):
        return d in index_dates
    return not recent_max or n >= STRAY_FRAC * recent_max


def trading_dates(con, counts=None, index_dates=None):
    """(거래일 목록, {날짜: 행수}) — 휴장일 잔행 날짜 제외(v09, v19 지수 봉 기준)."""
    counts = counts if counts is not None else date_counts(con)
    dates, recent = [], []
    for d, n in counts.items():
        if not is_trading_day(d, n, max(recent) if recent else 0, index_dates):
            continue
        dates.append(d)
        recent = (recent + [n])[-STRAY_WINDOW:]
    return dates, counts


MARKET_SETTLE_ET = dt.time(16, 30)   # v19: 정규장 16:00 마감 + 30분 — 이보다 이르면 오늘(ET) 봉은 미완성으로 본다


def last_complete_date():
    """v19: 저장해도 되는 마지막 봉 날짜(ET 달력, 'YYYYMMDD') — ET 16:30 이후면 오늘, 아니면 어제.
    장중 수동 실행이 미완성 당일 봉을, 새벽 정기 실행(04:30 ET)이 24시간 시리즈(USDKRW·DXY)의 진행 중인 봉을
    INSERT OR IGNORE 로 굳히지 않게(실측: 정본 USDKRW 에 20260927(일) 행)."""
    try:
        from zoneinfo import ZoneInfo
        now = dt.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now = dt.datetime.utcnow() - dt.timedelta(hours=5)
    d = now.date() if now.time() >= MARKET_SETTLE_ET else now.date() - dt.timedelta(days=1)
    return d.strftime("%Y%m%d")


def baseline(counts, day):
    """완전성 분모(v12): day 이전 BASELINE_WINDOW 관측일의 최대 행수. 앞선 날이 없으면 그날 행수."""
    prior = [counts[d] for d in sorted(counts) if d < day][-BASELINE_WINDOW:]
    return max(prior, default=counts.get(day, 0))


if __name__ == "__main__":
    # 워크플로용: ISO 요일(월=1 … 일=7) 출력 — 주간 백업 요일(RUN_DOW) 판정
    print(session_weekday() + 1)
