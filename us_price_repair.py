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


def baseline(counts, day):
    prior = [counts[d] for d in sorted(counts) if d < day][-WINDOW:]
    return max(prior, default=counts.get(day, 0))


def gaps(con):
    counts = dict(con.execute('SELECT date, COUNT(*) FROM daily_ohlcv WHERE close > 0 GROUP BY date'))
    # Observed weekdays only: holidays/zero-row sessions require a calendar review.
    return [(d, counts[d], baseline(counts, d)) for d in sorted(counts)[-AUDIT_DAYS:]
            if dt.datetime.strptime(d, '%Y%m%d').weekday() < 5
            and counts[d] < MIN_COVERAGE * baseline(counts, d)]


def missing(con, day):
    dates = [r[0] for r in con.execute('SELECT DISTINCT date FROM daily_ohlcv ORDER BY date')]
    before = [d for d in dates if d < day]
    after = [d for d in dates if d > day]
    if not before or not after:
        raise ValueError('복구일 앞뒤 시세가 있어야 합니다')
    def symbols(d):
        return {r[0] for r in con.execute('SELECT symbol FROM daily_ohlcv WHERE date=? AND close>0', (d,))}
    expected = symbols(before[-1]) & symbols(after[0])
    if not expected:
        raise ValueError('복구 기준 종목이 없습니다')
    return sorted(expected - symbols(day)), len(expected)


def repair(con, day, fetch, store, sub_frame, chunk=50, pause=time.sleep):
    todo, expected = missing(con, day)
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
    remaining, _ = missing(con, day)
    print(f'[시세 복구] {day}: +{added}행, 기준 {expected}, 잔여 {len(remaining)}', flush=True)
    return not remaining


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
            completed = [repair(con, d, fetch, store, sub_frame) for d in dates]
            if not all(completed):
                raise RuntimeError('복구 미완료: 점수 재계산/정본 업로드 중단. 로그의 잔여 종목 수 확인')
        for day, have, expected in gaps(con):
            print(f'::warning::시세 점검 {day}: {have}/{expected} ({have/expected:.1%}); 휴장 잔행 여부 확인 필요')


if __name__ == '__main__':
    main()
