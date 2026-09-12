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

[v08 2026-09-06 리뷰 반영 — patch_note/v08]
  ① 큐 굶음 수정: 배당 이벤트를 날짜 없이 매일 재등록해(한 배당당 ~5회 전체 재수집) div 만으로
     회당 상한 200 을 소진 → cliff 775건이 2주간 attempts=0(MNST 절벽 미수정 실측). 이벤트에
     날짜를 붙여 event_checked 로 1회만 처리, 순서 split→cliff→div, 진짜 split 이 오면 승격.
  ② 배치 fetch 예외 시에도 attempts 증가(무한 재시도 방지), 포기는 6회.
  ③ 증분 창: 마지막 저장일 기준(MAX(date)−7d) — 7일 넘는 장애도 자동 치유.
  ④ 평일 증분 0행이면 ⚠️ 명시(휴장일로 위장되던 조용한 실패).
  ⑤ 단일 심볼 청크의 MultiIndex(yfinance 1.x) 처리, 0값 행(close/adj_close≤0) 저장 안 함.

[v09 2026-09-11 2차 수집 — patch_note/v09] ※ 아래 v10 에서 제거됨(효과 0행). 경위 기록으로만 남긴다.
  실측(09-11 러너 로그): '배치 실패 0'인데 최신일 20260910 행이 5,226/6,558 심볼에만 있었고, 정렬 순서 뒤쪽
  두 분위(S~Z)에서 86~89% 결측(TSLA·TSM·UNH·XOM 포함). 직후 다른 수집기에서 야후 429/401(크럼 거부).
  즉 예외 없이 '조용히' 당일 행이 빠지는 형태라 배치 실패 카운터로는 안 잡힌다. 다음날 7일 창이 전날을 채우므로
  DB 는 결국 완결되지만 그날의 점수 적재가 비게 된다(us_page_data v08 게이트). 원인(스로틀 vs 캐시)은 미확정 →
  둘 다에 걸리도록: 증분 뒤 최신일 결측 심볼만 RETRY_WAIT_S 쉬고 start= 명시(range=7d 와 다른 URL)로 작은 배치 재요청.

[v10 2026-09-12 계측 — patch_note/v10] v09 2차 수집은 **무효로 판명돼 제거**한다.
  실측(러너 #55, 2026-09-12 00:00 UTC): 최신일 20260911 수집 1,055/6,551(16%) → 90s 뒤 결측 5,504심볼을
  20개씩 276배치로 재요청 → **+0행**(배치 예외 0). 같은 실행=같은 출구 IP 라 곧바로 다시 물어봐야 소용이 없다.
  같은 날 노트북에서 러너와 동일 버전(yfinance 1.7.0 + pandas 3.0.5)으로 같은 코드 경로를 태우면 30종목 중 29개가
  20260911 행을 정상 반환 → 데이터는 야후에 있고, 라이브러리 버전 문제도 아니다.
  결측 분포도 특이하다: 알파벳 10분위 전부 11~21% 로 균일(= 배치 순서·누적 요청량 탓이 아님)인데
  하이픈 심볼(우선주·클래스주) 93% vs 일반 심볼 11%. 러너 지역도 다르다(#53·#54 westcentralus 정상 → #55 eastus2 16%).
  **원인 미특정.** 지금까지 못 좁힌 이유는 수집기가 심볼별 실패를 `except Exception: pass` 로 통째로 삼켜
  러너에서 무슨 응답이 왔는지 기록이 남지 않아서다. → 추측 기반 수정 대신 계측을 넣는다:
  ① 러너 지문(공인 IP·라이브러리 버전)을 로그 첫 줄에 ② yfinance 로거(WARNING+)를 잡아 사유별 집계
  (HTTP 본문 포함) ③ 심볼별 결측을 빈 프레임/키 없음/예외로 분류. 다음 실행 로그 1회로 원인을 좁힌다.
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
GIVE_UP_ATTEMPTS = 6  # v08: 재조정 시도 상한(배치 예외도 세므로 3→6)
LOW_YIELD_FRAC = 0.9  # v10: 최신일 수집 심볼 / 대상 — 이 미만이면 ⚠️ + 사유 집계 상세 출력(page_data 게이트와 같은 기준)
STRAY_FRAC = 0.1      # 심볼 수가 최근 최대의 이 비율 미만인 날짜 = 잔행(휴장일에 흘린 1~2행) → 거래일로 안 침
# v10 계측 출력 크기 — 로그가 길어지지 않게 상한을 둔다
MSG_TOP = 8           # 사유별 집계에서 보여줄 상위 메시지 수
MSG_KEEP = 180        # 메시지 1건 보관 길이(문자)
SAMPLE_SYMS = 5       # 예시로 찍을 심볼 수
REASON_PRI = {"split": 0, "cliff": 1, "div": 2}   # v08 처리 순서(분할 > 절벽 > 배당)
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
    # v08: 처리 완료한 분할/배당 이벤트(심볼·이벤트일·종류) — 7일 창에 남아 있어도 재등록 안 함
    """CREATE TABLE IF NOT EXISTS event_checked (
        symbol TEXT NOT NULL, date TEXT NOT NULL, kind TEXT NOT NULL, PRIMARY KEY (symbol, date, kind))""",
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
            if c is None or c <= 0:
                continue
            ac = float(r["Adj Close"]) if ("Adj Close" in df.columns and r["Adj Close"] == r["Adj Close"]) else c
            if ac <= 0:      # v08: 0값 행은 수익률 inf 를 만든다(실측 101행) — 저장 안 함
                continue
            rows.append((symbol, idx.strftime("%Y%m%d"),
                         float(r["Open"]), float(r["High"]), float(r["Low"]), c, ac,
                         int(r["Volume"]) if r["Volume"] == r["Volume"] else 0))
        except Exception:
            continue
    if not rows:
        return 0
    verb = "REPLACE" if replace else "IGNORE"
    cur = con.executemany(
        f"INSERT OR {verb} INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?)", rows)
    return cur.rowcount


def runtime_banner():
    """v10: 실행 환경 지문 한 줄. 러너마다 다른 출구 IP·지역을 기록해 '어떤 러너에서 수집이 망하는가'를
    사후에 맞춰볼 수 있게 한다(워크플로가 RUNNER_IP 를 넣어준다). 버전은 requirements 고정 후에도
    실제 설치본을 확인하려고 찍는다."""
    vers = []
    for mod in ("yfinance", "pandas", "curl_cffi"):
        try:
            vers.append(f"{mod} {__import__(mod).__version__}")
        except Exception:
            vers.append(f"{mod} ?")
    ip = os.environ.get("RUNNER_IP", "").strip() or "?"
    print(f"[환경] 출구IP {ip} · " + " · ".join(vers))


def _norm_msg(m):
    """v10: yfinance 경고/오류 메시지에서 심볼 부분을 지워 사유별로 묶는다."""
    import re
    m = " ".join(str(m).split())
    m = re.sub(r"\$[A-Za-z0-9.\-]{1,12}:", "$SYM:", m)            # '$AAPL: ...' → '$SYM: ...'
    m = re.sub(r"\[[^\]]*\]:", "[SYMS]:", m)                       # "['A', 'B']: ..." → '[SYMS]: ...'
    m = re.sub(r"symbol: ?[A-Za-z0-9.\-]{1,12}", "symbol: SYM", m)
    m = re.sub(r"\b\d{1,3}(\.\d+)? Failed downloads?", "N Failed downloads", m)
    return m[:MSG_KEEP]


class YFLogCapture:
    """v10: yfinance 로거의 WARNING 이상을 모아 사유별로 센다. 실측(로컬)에서 '$SYM: No data found',
    'HTTP Error 404: {...}' 같은 본문까지 잡히는 것을 확인했다. 개별 심볼 예외를 조용히 삼키던 구멍의 대체물."""

    def __init__(self):
        import collections
        self.counts = collections.Counter()
        self.samples = {}
        self._h = None

    def __enter__(self):
        import logging
        cap = self

        class _H(logging.Handler):
            def emit(self, r):
                try:
                    cap.add(r.getMessage())
                except Exception:
                    pass

        self._h = _H()
        self._h.setLevel(logging.WARNING)
        lg = logging.getLogger("yfinance")
        self._prev_level = lg.level
        lg.addHandler(self._h)
        if lg.level == logging.NOTSET or lg.level > logging.WARNING:
            lg.setLevel(logging.WARNING)
        return self

    def __exit__(self, *a):
        import logging
        lg = logging.getLogger("yfinance")
        if self._h is not None:
            lg.removeHandler(self._h)
        lg.setLevel(self._prev_level)
        return False

    def add(self, msg):
        import re
        key = _norm_msg(msg)
        self.counts[key] += 1
        if key not in self.samples:
            m = re.search(r"\$([A-Za-z0-9.\-]{1,12}):", str(msg))
            self.samples[key] = m.group(1) if m else ""

    def report(self, label="수집"):
        if not self.counts:
            return f"[{label} 사유] yfinance 경고 없음"
        lines = [f"[{label} 사유] 경고/오류 {sum(self.counts.values())}건 · 종류 {len(self.counts)}"]
        for msg, n in self.counts.most_common(MSG_TOP):
            eg = self.samples.get(msg, "")
            lines.append(f"    {n:6d}회 | {msg}" + (f"  (예: {eg})" if eg else ""))
        return "\n".join(lines)


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

def sub_frame(df, sym, n_chunk):
    """yf.download 결과에서 한 심볼의 DataFrame. v08: yfinance 1.x 는 티커 1개여도 MultiIndex 를
    유지하므로 '청크 길이'가 아니라 '컬럼 구조'로 판단(리뷰 M3 — 1심볼 청크가 0행 무음이었음)."""
    import pandas as pd
    if isinstance(df.columns, pd.MultiIndex):
        return df[sym].dropna(how="all")
    return df.dropna(how="all")


def detect_events(sub):
    """7일 창 DataFrame 에서 분할/배당 이벤트 감지 → ('split'|'div', 'YYYYMMDD') | None.
    v08: 이벤트 '날짜'를 함께 돌려줘 같은 이벤트가 창에 남아 있는 동안 재등록되지 않게 한다.
    (auto_adjust=False + actions=True 일 때만 컬럼 존재 — 없으면 None)"""
    try:
        for col, kind in (("Stock Splits", "split"), ("Dividends", "div")):
            if col in sub.columns:
                hit = sub[col].fillna(0) != 0
                if hit.any():
                    return kind, sub.index[hit][-1].strftime("%Y%m%d")
    except Exception:
        pass
    return None


def register_event(con, sym, kind, date8, now):
    """v08 이벤트 등록: 이미 처리한 (sym,date,kind) 는 무시. 큐에 있으면 우선순위가 높을 때만 승격
    (cliff 로 대기 중인 심볼에 진짜 split 이 오면 split 로). 반환: 등록/승격 여부."""
    if con.execute("SELECT 1 FROM event_checked WHERE symbol=? AND date=? AND kind=?",
                   (sym, date8, kind)).fetchone():
        return False
    row = con.execute("SELECT reason, detail FROM adjust_queue WHERE symbol=?", (sym,)).fetchone()
    if row is None:
        con.execute("INSERT INTO adjust_queue VALUES (?,?,?,?,0)", (sym, kind, date8, now))
        return True
    if REASON_PRI[kind] < REASON_PRI.get(row[0], 9):
        # 승격: 기존 detail(절벽 날짜 등)은 잃어도 됨 — 전체 재수집이 절벽도 함께 고친다
        con.execute("UPDATE adjust_queue SET reason=?, detail=?, queued_at=? WHERE symbol=?",
                    (kind, date8, now, sym))
        return True
    return False


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


def cliff_still(con, sym, d8):
    """v08: 재수집 뒤에도 (sym, d8) 절벽이 남아 있는가. 야후는 분할 직후 1~2일간 과거 행을
    아직 조정하지 않은 채 돌려주기도 한다(실측 APH 2:1 20260901 — 재수집 성공 처리됐지만
    절벽 그대로). 이때 cliff_checked 로 봉인하면 영구 오염 → 남아 있으면 봉인하지 않는다."""
    rows = con.execute("SELECT adj_close FROM daily_ohlcv WHERE symbol=? AND date<=? AND adj_close>0 "
                       "ORDER BY date DESC LIMIT 2", (sym, d8)).fetchall()
    if len(rows) < 2 or not rows[1][0]:
        return False
    r = rows[0][0] / rows[1][0]
    return any(abs(r - t) / t < 0.06 for k in SPLIT_RATIOS for t in (1.0 / k, float(k)))


def missing_latest(con, symbols):
    """v09: (최신 거래일, 전일, 결측 심볼 목록, 대상 수). 결측 = 전일 행은 있는데 최신일 행이 없는 유니버스 심볼.
    심볼 수가 최근 25일 최대의 STRAY_FRAC 미만인 잔행 날짜(휴장일에 흘린 1~2행)는 거래일로 치지 않는다 —
    최신 행 날짜가 잔행이면 그 앞의 진짜 거래일을 최신일로 본다."""
    rows = con.execute(
        "SELECT date, COUNT(*) FROM daily_ohlcv GROUP BY date ORDER BY date DESC LIMIT 25").fetchall()
    if len(rows) < 2:
        return None, None, [], 0
    peak = max(n for _, n in rows)
    real = [d for d, n in rows if n >= STRAY_FRAC * peak]
    if len(real) < 2:
        return None, None, [], 0
    d_t, d_p = real[0], real[1]
    have = {s for (s,) in con.execute("SELECT symbol FROM daily_ohlcv WHERE date=?", (d_t,))}
    prev = {s for (s,) in con.execute("SELECT symbol FROM daily_ohlcv WHERE date=?", (d_p,))}
    want = set(symbols) & prev
    return d_t, d_p, sorted(want - have), len(want)


def _queue_fail(con, sym, reason, detail, max_attempts=GIVE_UP_ATTEMPTS):
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
        "ORDER BY CASE reason WHEN 'split' THEN 0 WHEN 'cliff' THEN 1 ELSE 2 END, queued_at "
        "LIMIT ?",   # v08: 분할 > 절벽(50% 오염) > 배당(~1% 드리프트) — 배당이 절벽을 굶기지 않게
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
            # v08: 배치 예외도 시도 횟수에 센다 — 안 세면 영구 무한 재시도(리뷰 M4)
            con.executemany("UPDATE adjust_queue SET attempts = attempts + 1 WHERE symbol=?",
                            [(t[0],) for t in batch])
            con.commit()
            time.sleep(10)
            continue
        for sym, reason, detail in batch:
            try:
                sub = sub_frame(df, sym, len(syms))
                n = store(con, sub, sym, replace=True)
                if n > 0:
                    dates = [dd for dd in (detail or "").split(",") if dd]
                    if reason == "cliff" and any(cliff_still(con, sym, dd) for dd in dates):
                        # v08: 재수집했는데 절벽이 그대로(야후 미조정 지연) — 봉인하지 않고 재시도 대기
                        _queue_fail(con, sym, reason, detail)
                        print(f"  ↻ {sym} 재수집 후에도 절벽 잔존 — 봉인 안 함, 다음 실행 재시도")
                        continue
                    con.execute("DELETE FROM adjust_queue WHERE symbol=?", (sym,))
                    for dd in dates:
                        if reason == "cliff":
                            con.execute("INSERT OR IGNORE INTO cliff_checked VALUES (?,?)", (sym, dd))
                        else:   # v08: 처리한 split/div 이벤트 — 7일 창에 남아 있어도 재등록 안 함
                            con.execute("INSERT OR IGNORE INTO event_checked VALUES (?,?,?)", (sym, dd, reason))
                    fixed += 1
                else:
                    _queue_fail(con, sym, reason, detail)
            except Exception:
                _queue_fail(con, sym, reason, detail)  # GIVE_UP_ATTEMPTS 회 후 포기(상폐 등)
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
    check("detect: split (종류, 이벤트일)", detect_events(sub_split) == ("split", "20260811"))
    check("detect: div", detect_events(sub_div) == ("div", "20260811"))
    check("detect: actions 컬럼 없으면 None(구버전 호환)", detect_events(sub_none) is None)
    # v08 sub_frame: MultiIndex 면 심볼로, 단일이면 그대로 (1심볼 청크 0행 무음 결함)
    mi = pd.concat({"AAA": sub_none, "BBB": sub_none}, axis=1)
    check("sub_frame: MultiIndex → 심볼 프레임", list(sub_frame(mi, "AAA", 2).columns) == list(sub_none.columns))
    check("sub_frame: 단일 컬럼 프레임(1심볼) → 그대로", sub_frame(sub_none, "AAA", 1).shape == sub_none.shape)
    mi1 = pd.concat({"AAA": sub_none}, axis=1)
    check("sub_frame: 1심볼인데 MultiIndex(yfinance 1.x) → 심볼 프레임", sub_frame(mi1, "AAA", 1).shape == sub_none.shape)

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
    # v08 cliff_still: 재수집 뒤 절벽 잔존 판정
    check("cliff_still: 2:1 절벽 잔존 → True", cliff_still(con, "MNSX", "20260811") is True)
    check("cliff_still: 정상 -30% → False", cliff_still(con, "CRSH", "20260811") is False)

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

    # store: 0값 행 저장 안 함(v08)
    zero = pd.DataFrame({**base, "Close": [0.0, 45.53], "Adj Close": [0.0, 45.53]}, index=idx)
    check("store: close/adj_close 0 행은 건너뜀", store(con, zero, "ZERO") == 1)

    # 큐 등록 idempotent
    for _ in range(2):
        con.execute("INSERT OR IGNORE INTO adjust_queue VALUES ('MNSX','cliff','20260811','t',0)")
    check("queue: 중복 등록 차단", con.execute("SELECT COUNT(*) FROM adjust_queue").fetchone()[0] == 1)
    # v08 이벤트 등록: 같은 배당 2회 감지 → 1회만, cliff 대기 심볼에 split → 승격, 처리 완료 이벤트는 무시
    check("event: div 첫 등록", register_event(con, "DIVX", "div", "20260811", "t") is True)
    check("event: 같은 div 재감지는 등록 안 함(큐에 이미 있음)", register_event(con, "DIVX", "div", "20260811", "t") is False)
    check("event: cliff 대기 심볼에 split 승격", register_event(con, "MNSX", "split", "20260811", "t") is True
          and con.execute("SELECT reason FROM adjust_queue WHERE symbol='MNSX'").fetchone()[0] == "split")
    check("event: split 대기 심볼에 div 는 강등 안 함", register_event(con, "MNSX", "div", "20260812", "t") is False)
    con.execute("INSERT INTO event_checked VALUES ('DONE','20260811','div')")
    check("event: 처리 완료한 이벤트는 재등록 안 함", register_event(con, "DONE", "div", "20260811", "t") is False)
    order = [r[0] for r in con.execute(
        "SELECT reason FROM adjust_queue ORDER BY CASE reason WHEN 'split' THEN 0 WHEN 'cliff' THEN 1 ELSE 2 END, queued_at")]
    check("queue: 처리 순서 split → cliff → div", order == sorted(order, key=lambda r: REASON_PRI[r]))
    # 실패 GIVE_UP_ATTEMPTS 회 → 포기(큐 제거 + checked 표기, 다중 절벽 detail 전부)
    con.execute("UPDATE adjust_queue SET reason='cliff', detail='20260811,20260812' WHERE symbol='MNSX'")
    for _ in range(GIVE_UP_ATTEMPTS):
        _queue_fail(con, "MNSX", "cliff", "20260811,20260812")
    check(f"queue: {GIVE_UP_ATTEMPTS}회 실패 시 포기·큐 제거", con.execute(
        "SELECT COUNT(*) FROM adjust_queue WHERE symbol='MNSX'").fetchone()[0] == 0)
    check("queue: 포기한 절벽 checked 표기(다중 날짜 전부)", con.execute(
        "SELECT COUNT(*) FROM cliff_checked WHERE symbol='MNSX'").fetchone()[0] == 2)
    # v09 2차 수집: 최신일 결측 판정(잔행 날짜 제외) + 가짜 fetch 로 보충
    con9 = sqlite3.connect(":memory:")
    for d in DDL:
        con9.execute(d)
    syms9 = [f"S{i:02d}" for i in range(12)]
    rows9 = [(s, "20260909", 0, 0, 0, 10.0, 10.0, 1) for s in syms9]
    rows9 += [(s, "20260910", 0, 0, 0, 11.0, 11.0, 1) for s in syms9[:8]]      # 당일 8/12 만 수집
    rows9 += [("S00", "20260911", 0, 0, 0, 12.0, 12.0, 1)]                      # 잔행(휴장일 1행)
    con9.executemany("INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?)", rows9)
    d_t, d_p, miss, n_want = missing_latest(con9, syms9)
    check("missing: 잔행 날짜(1행) 를 최신일로 안 침", (d_t, d_p) == ("20260910", "20260909"))
    check("missing: 전일엔 있고 최신일에 없는 심볼", (miss, n_want) == (["S08", "S09", "S10", "S11"], 12))
    check("missing: 유니버스 밖 심볼 제외", missing_latest(con9, syms9[:10])[2] == ["S08", "S09"])
    only_stray = sqlite3.connect(":memory:")
    for d in DDL:
        only_stray.execute(d)
    only_stray.executemany("INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?)",
                           [(s, d9, 0, 0, 0, 10.0, 10.0, 1) for d9 in ("20260908", "20260909") for s in syms9]
                           + [("S00", "20260910", 0, 0, 0, 1, 1, 1)])
    check("missing: 최신 행 날짜가 잔행(휴장일 실행)이면 직전 거래일 기준·결측 없음",
          missing_latest(only_stray, syms9)[:3] == ("20260909", "20260908", []))
    # v10 계측: 메시지 정규화 + 로거 캡처
    check("norm: 심볼 접두어를 묶는다",
          _norm_msg("$AAPL: No data found, symbol may be delisted")
          == _norm_msg("$TSLA: No data found, symbol may be delisted"))
    check("norm: 심볼 목록을 묶는다",
          _norm_msg("['A', 'B']: No data found") == _norm_msg("['C']: No data found"))
    check("norm: 서로 다른 사유는 안 묶는다",
          _norm_msg("$A: No data found") != _norm_msg("$A: HTTP Error 404"))
    check("norm: 길이 상한", len(_norm_msg("x" * 500)) == MSG_KEEP)
    import logging as _lg
    cap9 = YFLogCapture()
    with cap9:
        _lg.getLogger("yfinance").error("$AAPL: No data found, symbol may be delisted")
        _lg.getLogger("yfinance").error("$TSLA: No data found, symbol may be delisted")
        _lg.getLogger("yfinance").error('HTTP Error 404: {"code":"Not Found"}')
        _lg.getLogger("yfinance").info("무시되는 INFO")
    check("capture: WARNING+ 만 · 사유별 집계", sum(cap9.counts.values()) == 3 and len(cap9.counts) == 2)
    check("capture: 같은 사유 2건 묶임", max(cap9.counts.values()) == 2)
    check("capture: 예시 심볼 기록", cap9.samples.get(_norm_msg("$AAPL: No data found, symbol may be delisted")) == "AAPL")
    check("capture: 보고 문자열", "종류 2" in cap9.report())
    check("capture: 빠져나온 뒤 핸들러 제거",
          not any(type(h).__name__ == "_H" for h in _lg.getLogger("yfinance").handlers))
    cap0 = YFLogCapture()
    check("capture: 경고 0건이면 그 사실을 찍는다", "경고 없음" in cap0.report())
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

    runtime_banner()   # v10: 출구 IP·라이브러리 버전을 로그 맨 앞에(러너별 수집 실패 대조용)
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
                    sub = sub_frame(df, s, len(chunk))
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
        total, n_evt, n_batch_fail = 0, 0, 0
        n_empty, n_nokey, n_exc = 0, 0, 0   # v10: 심볼별 결측 분류(빈 프레임 / 응답에 심볼 없음 / 그 외 예외)
        now = dt.datetime.now().isoformat(timespec="seconds")
        # v08 ③: 창 시작 = 마지막 저장일 − 7d (마지막 저장일이 7일 넘게 오래됐으면 그만큼 넓어짐 —
        #   Actions 장기 중단·연속 실패 뒤에도 자동 치유). 평소엔 기존 7일 창과 동일.
        last = con.execute("SELECT MAX(date) FROM daily_ohlcv").fetchone()[0]
        fetch_kw = {"period": "7d"}
        if last:
            last_d = dt.date(int(last[:4]), int(last[4:6]), int(last[6:]))
            if (dt.date.today() - last_d).days > 7:
                fetch_kw = {"start": (last_d - dt.timedelta(days=7)).isoformat()}
                print(f"  ↺ 마지막 저장일 {last} 이 7일 넘게 오래됨 — 창을 {fetch_kw['start']} 부터로 넓힘")
        cap = YFLogCapture()
        with cap:   # v10: yfinance 경고/오류를 사유별로 집계(개별 심볼 예외를 삼키던 구멍의 대체)
            for i in range(0, len(symbols), CHUNK):
                chunk = symbols[i:i + CHUNK]
                try:
                    df = fetch_chunk(chunk, actions=True, **fetch_kw)
                except Exception as e:
                    print(f"  ⚠️ 배치 {i//CHUNK} 실패 건너뜀: {e}")
                    n_batch_fail += 1
                    time.sleep(10)
                    continue
                for s in chunk:
                    try:
                        sub = sub_frame(df, s, len(chunk))
                        if sub is None or sub.empty:   # v10: 응답은 왔는데 이 심볼 행이 0
                            n_empty += 1
                            continue
                        total += store(con, sub, s)
                        ev = detect_events(sub)
                        if ev and register_event(con, s, ev[0], ev[1], now):
                            n_evt += 1
                    except KeyError:                   # v10: 응답 컬럼에 심볼 자체가 없음
                        n_nokey += 1
                    except Exception:
                        n_exc += 1
                con.commit()
                time.sleep(SLEEP_BETWEEN)
        print(f"증분 완료: 신규 {total}행 · 이벤트 신규등록 {n_evt}심볼 · 배치 실패 {n_batch_fail}")
        print(f"[증분 결측] 빈 프레임 {n_empty} · 응답에 심볼 없음 {n_nokey} · 예외 {n_exc} "
              f"(대상 {len(symbols)})")
        print(cap.report("증분"))
        if total == 0 and dt.date.today().weekday() < 5:
            print("⚠️ 평일인데 증분 0행 — 휴장일이 아니면 수집 실패(rate limit/네트워크). "
                  "텔레그램 '시세 없음' 알림·건강줄 확인. 다음 실행이 창을 넓혀 자동 보충함")
        if n_batch_fail:
            print(f"⚠️ 배치 실패 {n_batch_fail}건 — 오늘 유니버스 일부 결측 가능(page_data 완전성 게이트가 걸러줌)")

        # v10 수확량 점검 — 낮으면 결측 심볼의 모양(하이픈 포함 비율·알파벳 분포)까지 찍어
        #   다음 실행 로그 1회로 원인을 좁힌다. 재요청은 하지 않는다(v09 2차 수집 +0행 실측 → 제거).
        try:
            d_t, d_p, miss, n_want = missing_latest(con, symbols)
            n_have = n_want - len(miss)
            if d_t and n_want and n_have / n_want < LOW_YIELD_FRAC:
                print(f"⚠️ 최신일 {d_t} 수집 {n_have:,}/{n_want:,}심볼({n_have / n_want:.0%} < {LOW_YIELD_FRAC:.0%}) "
                      f"— 결측 {len(miss):,}. 오늘 적재는 page_data 게이트가 막고, 다음 실행 7일 창·자동 보충이 채운다.")
                dash = [s for s in miss if "-" in s]
                want_dash = [s for s in symbols if "-" in s]
                if want_dash:
                    print(f"    결측 중 하이픈 심볼 {len(dash):,}/{len(want_dash):,}"
                          f"({len(dash)/len(want_dash):.0%}) · 일반 심볼 "
                          f"{len(miss)-len(dash):,}/{len(symbols)-len(want_dash):,}"
                          f"({(len(miss)-len(dash))/max(1, len(symbols)-len(want_dash)):.0%})")
                q = sorted(miss)
                print(f"    결측 예시: {', '.join(q[:SAMPLE_SYMS])} … {', '.join(q[-SAMPLE_SYMS:])}")
            elif d_t:
                print(f"최신일 {d_t} 수집 {n_have:,}/{n_want:,}심볼 — 정상")
        except Exception as e:
            print(f"  ⚠️ 수확량 점검 실패(비치명): {e}")

        # 절벽 스캔(기존 오염 자가치유 — 오탐 무해, cliff_checked 로 반복 차단)
        try:
            cliffs = scan_cliffs(con)
            by_sym = {}
            for sym, d, r in cliffs:
                by_sym.setdefault(sym, []).append(d)
            for sym, dates in by_sym.items():   # 심볼당 1행, 절벽 전부 detail 에(결함 수정 v2)
                cur = con.execute("INSERT OR IGNORE INTO adjust_queue VALUES (?,?,?,?,0)",
                                  (sym, "cliff", ",".join(sorted(dates)), now))
                if cur.rowcount == 0:   # v08: 'div' 로 대기 중이면 cliff 로 승격(처리 순서상 앞으로)
                    con.execute("UPDATE adjust_queue SET reason='cliff', detail=?, queued_at=? "
                                "WHERE symbol=? AND reason='div'",
                                (",".join(sorted(dates)), now, sym))
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
