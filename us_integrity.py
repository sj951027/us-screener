"""us_integrity.py — 업로드 전 정본 보호 점검(v19, 2026-09-27).

정본(Release us-data.tar.gz)은 매일 통째로 덮어써진다. 기존 방어는 두 가지였다:
  ① PRAGMA quick_check(파일 손상) ② 새 tar 가 직전의 50% 미만이면 중단(대량 소실).
그런데 score_daily 처럼 작은 테이블이 통째로 비어도 ①은 ok, ②도 통과한다(daily_ohlcv 가 크기를 지배).
이 모듈은 ③ '추가만 되는 테이블'의 행수가 실행 전보다 줄면 업로드를 막는다.

추가 전용 테이블(운영 코드에 DELETE 경로 없음 — 2026-09-27 전수 grep): 아래 TABLES.
  예외: score_daily 는 수동 --repair(REPAIR_DATES 입력)일 때만 지웠다 다시 넣으므로 그때만 감소 허용.
  제외: insider_tx(분기 단위 교체), adjust_queue·*_done·snapshot_log 등 상태 테이블.

사용(워크플로):
    python us_integrity.py snapshot OUT.json      # 다운로드 직후
    python us_integrity.py check OUT.json         # 업로드 직전 — quick_check + 행수 감소 검사, 문제면 exit 1
    python us_integrity.py --self-test            # 오프라인 검증(네트워크 0)
환경: US_DATA_DIR(기본 ../us-screener-data), REPAIR_DATES(수동 재계산 입력).
"""
import glob
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("US_DATA_DIR", "").strip() or (HERE / ".." / "us-screener-data"))
TABLES = {
    "us_ohlcv.db": ["daily_ohlcv", "score_daily", "valuation_rotate", "short_interest"],
    "us_market.db": ["market_daily"],
    "us_seed.db": ["listing_daily", "index_membership"],
    "us_options.db": ["option_daily"],
    "us_shortvol.db": ["short_volume_daily"],
    "us_fundamentals.db": ["xbrl_facts", "earnings_events"],
}
REPAIR_EXEMPT = {"us_ohlcv.db:score_daily"}


def counts(data_dir):
    """{'db:table': 행수} — 파일·테이블이 없으면 뺀다(최초 실행·신규 테이블)."""
    out = {}
    for db, tables in TABLES.items():
        p = Path(data_dir) / db
        if not p.exists():
            continue
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
        for t in tables:
            try:
                out[f"{db}:{t}"] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                pass
        con.close()
    return out


def quick_check(data_dir):
    bad = []
    for db in sorted(glob.glob(str(Path(data_dir) / "*.db"))):
        try:
            r = sqlite3.connect(db).execute("PRAGMA quick_check(1)").fetchone()[0]
        except Exception as e:
            r = f"error: {e}"
        print(f"{Path(db).name}: {r}")
        if r != "ok":
            bad.append(Path(db).name)
    return bad


def shrunk(before, after, repair=False):
    """행수가 줄어든 테이블 [(키, 전, 후)]. 전에 있던 테이블이 사라져도 감소로 본다."""
    return [(k, b, after.get(k, 0)) for k, b in sorted(before.items())
            if after.get(k, 0) < b and not (repair and k in REPAIR_EXEMPT)]


def main(argv):
    if argv[:1] == ["--self-test"]:
        return self_test()
    if len(argv) != 2 or argv[0] not in ("snapshot", "check"):
        print(__doc__)
        return 2
    cmd, path = argv
    if cmd == "snapshot":
        c = counts(DATA_DIR)
        Path(path).write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"행수 스냅샷 {len(c)}개 테이블 → {path}")
        return 0
    bad = quick_check(DATA_DIR)
    before = json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}
    after = counts(DATA_DIR)
    repair = bool(os.environ.get("REPAIR_DATES", "").strip())
    lost = shrunk(before, after, repair)
    for k in sorted(after):
        b = before.get(k)
        print(f"  {k}: {b if b is not None else '-'} → {after[k]:,}" + (f" (+{after[k] - b:,})" if b is not None else ""))
    if not before:
        print("::warning::실행 전 행수 스냅샷 없음(최초 실행?) — 감소 검사 생략")
    if bad:
        print(f"❌ DB 손상: {', '.join(bad)} — 업로드 중단")
    if lost:
        print("❌ 추가 전용 테이블 행수 감소 — 정본 훼손 의심, 업로드 중단: "
              + " · ".join(f"{k} {b:,}→{a:,}" for k, b, a in lost))
    return 1 if (bad or lost) else 0


def self_test():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  {'OK ' if cond else 'FAIL'} {label}")
        ok &= bool(cond)

    print("== self-test (오프라인) ==")
    d = Path(tempfile.mkdtemp())
    con = sqlite3.connect(d / "us_ohlcv.db")
    con.execute("CREATE TABLE daily_ohlcv(symbol TEXT, date TEXT)")
    con.execute("CREATE TABLE score_daily(model TEXT, date TEXT, symbol TEXT)")
    con.executemany("INSERT INTO daily_ohlcv VALUES (?,?)", [(f"S{i}", "20260925") for i in range(10)])
    con.executemany("INSERT INTO score_daily VALUES ('m','20260925',?)", [(f"S{i}",) for i in range(5)])
    con.commit()
    before = counts(d)
    check("스냅샷: 있는 테이블만(없는 DB·테이블은 뺌)", before == {"us_ohlcv.db:daily_ohlcv": 10, "us_ohlcv.db:score_daily": 5})
    con.execute("INSERT INTO daily_ohlcv VALUES ('S99','20260926')")
    con.commit()
    check("증가는 통과", shrunk(before, counts(d)) == [])
    con.execute("DELETE FROM score_daily")
    con.commit()
    check("score_daily 소실은 차단", shrunk(before, counts(d)) == [("us_ohlcv.db:score_daily", 5, 0)])
    check("수동 --repair 일 때 score_daily 감소는 허용", shrunk(before, counts(d), repair=True) == [])
    con.execute("DROP TABLE daily_ohlcv")
    con.commit()
    check("테이블이 사라지면 감소로 본다", ("us_ohlcv.db:daily_ohlcv", 10, 0) in shrunk(before, counts(d), repair=True))
    con.close()
    check("quick_check 정상 DB", quick_check(d) == [])
    print("✅ self-test 통과" if ok else "❌ self-test 실패")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
