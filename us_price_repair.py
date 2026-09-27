"""Explicit missing-row repair (Actions only); --audit is read-only.

Never replaces prices. Reference universe is symbols present on both adjacent
observed sessions. This excludes new listings/delistings and is not a calendar.
"""
import argparse
import datetime as dt
import sqlite3
import time

WINDOW = 20
MIN_COVERAGE = .9
AUDIT_DAYS = 20
# v14: 실패선은 점수 게이트와 같은 식(커버리지 < MIN_COVERAGE) — 복구가 '성공'이라 한 날은 page_data 도 적재하고,
#   '실패'라 한 날은 게이트도 막는다(v13 의 1% 선은 게이트와 어긋나 90~99% 구간이 애매했다).
#   잔여가 RESIDUAL_WARN_FRAC(1%)를 넘으면 경고만 남긴다. 실측 20260922: 잔여 2/6,566 = 0.03%(PCG 우선주, 야후 미제공).
RESIDUAL_WARN_FRAC = .01


def baseline(counts, day):
    prior = [counts[d] for d in sorted(counts) if d < day][-WINDOW:]
    return max(prior, default=counts.get(day, 0))


def gaps(con, counts=None):
    counts = counts if counts is not None else date_counts(con)
    # Observed weekdays only: holidays/zero-row sessions require a calendar review.
    return [(d, counts[d], baseline(counts, d)) for d in sorted(counts)[-AUDIT_DAYS:]
            if dt.datetime.strptime(d, '%Y%m%d').weekday() < 5
            and counts[d] < MIN_COVERAGE * baseline(counts, d)]


def date_counts(con):
    return dict(con.execute('SELECT date, COUNT(*) FROM daily_ohlcv WHERE close > 0 GROUP BY date'))


def anchors(con, day, counts=None):
    """v14: 복구일 앞뒤에서 '온전한' 관측일만 기준으로 쓴다(행수 ≥ 직전 20관측일 최대의 MIN_COVERAGE).
    실측: 20260907 노동절 잔행(1행)이 앞 기준일로 잡히면 기준 종목이 1개가 되어 20260908 복구가
    '+0행 잔여 0' 으로 허위 성공했다(v12·v13). 부분 수집일도 같은 이유로 기준에서 뺀다."""
    counts = counts if counts is not None else date_counts(con)   # 491만행 전수 집계라 호출측이 재사용
    whole = [d for d in sorted(counts) if counts[d] >= MIN_COVERAGE * baseline(counts, d)]
    before = [d for d in whole if d < day]
    after = [d for d in whole if d > day]
    if not before or not after:
        raise ValueError(f'{day}: 앞뒤에 온전한 관측일이 있어야 복구할 수 있습니다 — '
                         '최신 세션이면 다음 정기 실행의 7일 창이 채웁니다(수동 복구 불필요)')
    return before[-1], after[0]


def missing(con, day, counts=None):
    prev_day, next_day = anchors(con, day, counts)
    def symbols(d):
        return {r[0] for r in con.execute('SELECT symbol FROM daily_ohlcv WHERE date=? AND close>0', (d,))}
    expected = symbols(prev_day) & symbols(next_day)
    if not expected:
        raise ValueError('복구 기준 종목이 없습니다')
    return sorted(expected - symbols(day)), len(expected)


def repair(con, day, fetch, store, sub_frame, chunk=50, pause=time.sleep, counts=None):
    counts = counts if counts is not None else date_counts(con)
    todo, expected = missing(con, day, counts)
    start = dt.datetime.strptime(day, '%Y%m%d').date()
    added = 0
    for pos in range(0, len(todo), chunk):
        batch = todo[pos:pos+chunk]
        frame = fetch(batch, start=start.isoformat(), end=(start+dt.timedelta(days=1)).isoformat())
        for symbol in batch:
            sub = sub_frame(frame, symbol, len(batch))
            if sub is None or sub.empty:
                continue
            sub = sub[sub.index.strftime('%Y%m%d') == day]
            added += store(con, sub, symbol)  # INSERT OR IGNORE: preserve all existing rows
        con.commit()
        pause(1)
    counts[day] = counts.get(day, 0) + added   # 같은 실행에서 뒤에 오는 날짜의 기준일 판정에 반영(호출측이 counts 공유)
    remaining, _ = missing(con, day, counts)
    frac = len(remaining) / expected if expected else 0
    print(f'[시세 복구] {day}: +{added}행, 기준 {expected}, 잔여 {len(remaining)}({frac:.2%})', flush=True)
    if remaining:
        # v13: 잔여 = 요청했는데 야후가 그 날짜를 안 준 종목. 재요청해도 같으므로 목록만 남긴다.
        print(f'  ↳ 야후 미제공 {len(remaining)}종목 (예: {", ".join(remaining[:5])})', flush=True)
        if frac > RESIDUAL_WARN_FRAC and not bulk_gap(expected, remaining):   # v14: 게이트는 통과하나 결손이 큰 구간
            print(f'::warning::시세 복구 {day}: 잔여 {frac:.1%} — 점수 게이트({MIN_COVERAGE:.0%})는 통과하지만 결손이 큼', flush=True)
    return expected, added, remaining


def bulk_gap(expected, remaining):
    """v13: 재요청 뒤 잔여가 '대량 결손'인가. 소수 잔여(야후 미제공)는 실패로 보지 않는다.
    v14: page_data 게이트와 같은 식(커버리지 < MIN_COVERAGE)으로 써서 경계(정확히 10%)에서도 두 판정이 같다."""
    return bool(expected) and (expected - len(remaining)) / expected < MIN_COVERAGE


def repair_dates(con, dates, fetch, store, sub_frame):
    """날짜들을 차례로 복구하고 '대량 결손'으로 남은 날짜 설명 목록을 돌려준다(비면 성공). 행수 집계는 1회."""
    counts = date_counts(con)
    bulk = []
    for day in dates:
        expected, _added, rest = repair(con, day, fetch, store, sub_frame, counts=counts)
        if bulk_gap(expected, rest):
            bulk.append(f'{day} 잔여 {len(rest)}/{expected}')
    return bulk, counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dates', default='')
    ap.add_argument('--audit', action='store_true')
    args = ap.parse_args()
    dates = sorted(set(d.strip() for d in args.dates.split(',') if d.strip()))
    for day in dates:
        if len(day) != 8 or dt.datetime.strptime(day, '%Y%m%d').strftime('%Y%m%d') != day:
            raise ValueError('YYYYMMDD 날짜를 입력하세요')
    if args.audit and dates:
        ap.error('--audit 과 --dates 는 함께 사용하지 않습니다')
    if not args.audit and not dates:
        ap.error('--dates 또는 --audit 필요')
    from us_ohlcv_collector import OHLCV_DB, store, sub_frame
    mode = 'ro' if args.audit else 'rw'
    with sqlite3.connect(f'file:{OHLCV_DB.as_posix()}?mode={mode}', uri=True) as con:
        if dates:
            import yfinance as yf
            def fetch(symbols, **kw):
                return yf.download(symbols, interval='1d', auto_adjust=False,
                                   group_by='ticker', threads=True, progress=False, **kw)
            # v13: 실패는 '대량 결손'일 때만. 실측 09-22 는 재요청 뒤 잔여 2/6,566(PCG 우선주 2종,
            #   야후가 그날 자료를 아예 안 줌) 인데 v12 규칙이 이를 실패로 보고 실행 전체를 멈췄다.
            bulk, counts = repair_dates(con, dates, fetch, store, sub_frame)
            if bulk:
                raise RuntimeError(f"복구 미완료({' · '.join(bulk)}) — 차단·원천 미제공 의심. "
                                   '로그의 [시세 복구] 줄 확인')
        else:
            counts = date_counts(con)
        for day, have, expected in gaps(con, counts):
            print(f'::warning::시세 점검 {day}: {have}/{expected} ({have/expected:.1%}); 휴장 잔행 여부 확인 필요')


if __name__ == '__main__':
    main()
