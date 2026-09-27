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


if __name__ == "__main__":
    # 워크플로용: ISO 요일(월=1 … 일=7) 출력 — 주간 백업 요일(RUN_DOW) 판정
    print(session_weekday() + 1)
