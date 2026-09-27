# -*- coding: utf-8 -*-
"""
us_page_data.py — GitHub Pages 표 데이터 생성 → docs/data/us_latest.csv
==============================================================================
us_notify_test.py 와 **동일한 점수 로직**(mom12+upratio63+size 순위합, 가드 $5·$1M)
으로 가드 통과 전 종목의 순위표를 만들어 docs/us.html 이 읽을 CSV 로 저장한다.

⚠️ 규율: 이 점수는 in-sample 가설(생존편향 미보정) — 페이지·CSV 전체를
'테스트·관측·매수신호 아님'으로 표시한다. vol_cv 등 추가 컬럼은 **점수 미포함
관측 컬럼**(관측 우선 원칙 — 검증 전 가중 금지).

사용: python us_page_data.py            (GitHub Actions 가 매일 호출)
      python us_page_data.py --asof 20260902            # 그날 기준으로만 계산해 출력(쓰기 없음, 점검용)
      python us_page_data.py --repair 20260902,20260903 # 부분 수집일 점수 재계산(감사 로그) — 수동 전용
환경: US_DATA_DIR (기본 ../us-screener-data)
원칙: 비치명(데이터 없으면 생략) · CSV 이름의 콤마는 공백 치환(JS 단순 파서 호환)

[v08 2026-09-06 완전성 게이트] 시세 수집이 일부 배치를 놓친 날(실측: 20260902 유니버스 1,766 =
전일의 53%, J~Z 결측) 점수가 반토막 유니버스로 박제됐다(INSERT OR IGNORE 라 다음날 시세가
채워져도 복구 불가). → 당일 시세 심볼 수가 전일의 90% 미만이면 CSV·score_daily·틸트 전부
생략하고 ⚠️ 출력. 그런 날은 --repair 로 사람이 재계산한다(스케줄 실행은 절대 안 씀).

[v09 2026-09-11 게이트 보완·자동 보충] 실측(patch_note/v09): 09-02 부터 실행 당일 시세가 44~80%만
들어오고(정렬 뒤쪽 S~Z 심볼 결측 86~89%, 직후 야후 429/401) 다음날 7일 창이 전날을 채운다. v08 게이트는
'최신일'만 보고 조기 반환하므로 채워진 전날을 영영 계산하지 않았고(09-09·10 score_daily 공백), 휴장일
20260907 에 야후가 흘린 CREG 1행이 분모가 돼 20260908 부분 수집(1,466)이 통과했다. →
  ① 심볼 수가 최근 최대의 STRAY_FRAC 미만인 '잔행 날짜'는 거래일에서 제외(게이트 분모·계산 창 모두).
  ② 스케줄 실행은 최신일 앞 CATCHUP_DAYS 거래일 중 score_daily 가 비어 있고 완전성을 채운 날을 먼저
     자동 보충(INSERT OR IGNORE, CSV 는 최신일만). 부분 유니버스로 이미 적재된 날은 감사 로그가 남는
     --repair 로만 고친다(REPAIR 권고를 출력).
"""
import argparse
import datetime as _dt
import json
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from us_calendar import baseline, index_dates_for, missing_sessions, trading_dates   # v18: 거래일·완전성 분모 정의는 us_calendar 한 곳

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("US_DATA_DIR", "").strip() or (HERE / ".." / "us-screener-data"))
OHLCV_DB = DATA_DIR / "us_ohlcv.db"
SEED_DB = DATA_DIR / "us_seed.db"
OUT = HERE / "docs" / "data" / "us_latest.csv"
LOOKBACK = 260  # mom12(252) + 여유
# 관측 적재용 모델 id — 점수식이 바뀌면 새 id 로 (기존 기록 불변, 매직넘버·소급수정 금지)
MODEL_ID = "us_mus_v0"  # mom12 + upratio63 + size_amt 순위합 (2026-07-12 첫 배선)
TILT_MODEL_ID = "us_rvdtc_a"
COMPLETENESS_MIN = 0.9  # 당일 시세 심볼 수 / 전일 — 이 아래면 부분 수집으로 보고 적재 생략(v08)
CATCHUP_DAYS = 10       # v09: 최신일 앞 이 거래일 범위에서 score_daily 공백일을 자동 보충
# ── v20 관측 모델 us_mus_v1_ind — RESEARCH_us_arch_scan_20260906 의 m_indmom 과 같은 식(사전등록 후보, 가중치 0·기록만) ──
IND_MODEL_ID = "us_mus_v1_ind"
IND_TOP_Q = 0.8       # 업종 EW mom12 상위 20%(5분위 최상위) — 정의상 값, 최적화 아님(민감도 10~30% 같은 부호)
IND_MIN_N = 10        # 업종 평균이 한 종목에 좌우되지 않는 최소 구성 수(민감도 5·20 같은 부호)
IND_TOP_N = 50        # 게이트 통과 업종 안에서 base 점수 상위 50(부족하면 있는 만큼)
IND_START = "20260928"   # 관측 시작 세션 — 이보다 이른 날짜(repair·자동 보충)엔 기록 안 함(사후 재구성 방지)
HISTORY_OUT = HERE / "docs" / "data" / "us_history.json"
HISTORY_SHOW = {"us_mus_v0": 50, "us_rvdtc_a": 10, IND_MODEL_ID: IND_TOP_N}   # 페이지에 보일 모델별 목록 길이
GUARD_PASS_FRAC = 0.5   # v15: 가드 통과 유니버스 ≈ 시세 심볼의 절반(실측 3,36x/6,56x = 51%) — 부분 적재 의심 판정의 기준


def finra_key(sym):
    """daily_ohlcv 표기 → FINRA 격주 잔고 파일 표기(BRK-B→BRKB, BAC-PB→BACPRB). v08 리뷰 M2:
    표기 불일치로 클래스주·우선주 77종목의 dtc 가 조용히 결측이었다."""
    return sym.replace("-P", "PR").replace("-", "")


def completeness(counts, d_today, d_prev):
    """(당일 심볼 수, 전일 심볼 수, 비율). 전일 없으면 비율 1. v09: trading_dates 의 counts 를 받음."""
    n_t = counts.get(d_today, 0)
    n_p = baseline(counts, d_today) if d_prev else 0
    return n_t, n_p, (n_t / n_p if n_p else 1.0)


def main(asof=None, repair=False, catchup=False):
    """catchup=True(v09): 최신일이 아닌 asof 에 대해 score_daily 를 INSERT OR IGNORE 로 적재(CSV 는 안 씀)."""
    if not OHLCV_DB.exists():
        print(f"us_ohlcv.db 없음({OHLCV_DB}) — 생략(비치명).")
        return
    con = sqlite3.connect(f"file:{OHLCV_DB}?mode=ro", uri=True)
    idx_dates = index_dates_for(OHLCV_DB)
    all_dates, counts = trading_dates(con, index_dates=idx_dates)   # v09 잔행 제외 · v19 지수 봉 기준
    latest = all_dates[-1]
    outputs_only, stored_rank = False, {}
    if asof is None and not repair:
        # v21: 지수 봉은 있는데 시세 0행인 최신 세션 = 전면 수집 실패(0%). 이전엔 거래일 목록에서 빠져 '새 세션 없음'으로 보였다(Codex 검토 1)
        gone = [d for d in missing_sessions(counts, idx_dates) if d > latest]
        if gone:
            print(f"::warning::⚠️ 시세 부분 수집 의심: {gone[-1]} 세션 시세 0행(지수 봉은 있음) = 0% — CSV·score_daily·틸트 적재 생략. "
                  f"다음 실행 7일 창이 채우면 자동 보충(v09)")
            con.close()
            return
    if asof is None and not repair and not catchup:
        # v16: 최신 거래일이 이미 적재돼 있으면(새 세션 없음) CSV·점수·틸트를 통째로 다시 계산하지 않는다. v15 는 적재만 막아
        #   페이지 CSV(두 번째 계산)와 score_daily·틸트(첫 번째 계산)가 같은 날짜에 서로 다른 순위를 갖게 됐다.
        try:
            n_have = con.execute("SELECT COUNT(*) FROM score_daily WHERE model=? AND date=?",
                                 (MODEL_ID, latest)).fetchone()[0]
        except sqlite3.OperationalError:   # 최초 실행(테이블 없음)
            n_have = 0
        csv_date = _csv_date(OUT)
        if n_have and csv_date == latest:
            print(f"⏭ {latest} 이미 적재({n_have:,}행) — 새 세션 없음, 페이지·점수·틸트 재계산 생략. 재계산은 --repair")
            con.close()
            return
        if n_have and not os.environ.get("GITHUB_ACTIONS"):
            print(f"⏭ {latest} 이미 적재 — 로컬 실행이라 페이지 파일(CSV 기준일 {csv_date or '없음'})은 덮어쓰지 않는다(복원은 Actions 에서만)")
            con.close()
            return
        if n_have:
            # v21: 점수는 있는데 페이지 CSV 가 없거나 뒤처짐(페이지 푸시 실패 뒤 정본만 올라간 경우 — Codex 검토 5).
            #   저장된 점수는 건드리지 않고, 출력물(CSV·틸트 CSV·히스토리)만 저장된 순위대로 다시 만든다.
            outputs_only = True
            stored_rank = dict(con.execute("SELECT symbol, rank FROM score_daily WHERE model=? AND date=?", (MODEL_ID, latest)))
            print(f"::warning::{latest} 점수는 있으나 페이지 CSV 기준일 {csv_date or '없음'} — 저장된 점수는 두고 출력물만 다시 만든다")
    if asof:
        if asof not in all_dates:
            print(f"⚠️ {asof} 는 daily_ohlcv 에 없는 날짜 — 생략"); con.close(); return
        all_dates = [d for d in all_dates if d <= asof]      # PIT: 그날 이후 열은 아예 안 읽음
    dates = all_dates[-LOOKBACK:]
    # ── v08 완전성 게이트 ───────────────────────────────────────────────
    n_t, n_p, ratio = completeness(counts, dates[-1], dates[-2] if len(dates) > 1 else None)
    if ratio < COMPLETENESS_MIN:
        print(f"::warning::⚠️ 시세 부분 수집 의심: {dates[-1]} 심볼 {n_t:,} / 전일 {n_p:,} = {ratio:.0%} < {COMPLETENESS_MIN:.0%} "
              + ("— 아직 시세가 채워지지 않아 REPAIR 거부(다음날 재시도)." if repair else
                 "— CSV·score_daily·틸트 적재 생략(반토막 유니버스 박제 방지). "
                 "다음 실행에서 시세가 채워지면 자동 보충(v09)."))
        con.close(); return
    write_csv = (asof is None) or (asof == latest)   # 과거 날짜 재계산은 CSV(현재 표시)를 건드리지 않음
    verb = "REPAIR" if repair else "IGNORE"
    if repair:
        print(f"🛠 REPAIR {dates[-1]}: 완전성 {n_t:,}/{n_p:,}={ratio:.0%} · 기존 score_daily 행 삭제 후 재계산(감사 로그)")
    raw = pd.read_sql(
        "SELECT symbol,date,close,adj_close,volume FROM daily_ohlcv WHERE date>=? AND date<=?",
        con, params=(dates[0], dates[-1]))   # 상한 = asof (PIT: 그날 이후 행은 읽지 않음)
    raw = raw[raw["date"].isin(set(dates))]   # v09: 잔행 날짜 행 제외(창 밀림 방지)
    # 시총(참고) — valuation_rotate 는 순환 수집이라 심볼별 최신값(최대 ~2주 전)
    try:
        mcap = dict(con.execute(
            "SELECT symbol, market_cap FROM valuation_rotate v "
            "WHERE date=(SELECT MAX(date) FROM valuation_rotate w WHERE w.symbol=v.symbol) "
            "AND market_cap IS NOT NULL"))
    except sqlite3.OperationalError:
        mcap = {}
    # 섹터(순환 수집 캐시 — 첫 바퀴 동안은 일부 결측 정상)
    try:
        sectors = dict(con.execute(
            "SELECT symbol, sector FROM sector_cache WHERE sector IS NOT NULL"))
    except sqlite3.OperationalError:
        sectors = {}
    try:   # v20: 업종(industry) — indmom 게이트와 score_daily 업종 기록용(sector_cache 는 현재 분류, 오늘부터 기록하면 그날 기준)
        industries = dict(con.execute(
            "SELECT symbol, industry FROM sector_cache WHERE industry IS NOT NULL AND industry != ''"))
    except sqlite3.OperationalError:
        industries = {}
    con.close()
    for c in ("close", "adj_close", "volume"):
        raw[c] = pd.to_numeric(raw[c], errors="coerce")
    C = raw.pivot_table(index="symbol", columns="date", values="adj_close",
                        aggfunc="last").sort_index(axis=1)
    RAWC = raw.pivot_table(index="symbol", columns="date", values="close",
                           aggfunc="last").reindex(C.index).sort_index(axis=1)
    V = raw.pivot_table(index="symbol", columns="date", values="volume",
                        aggfunc="last").reindex(C.index).sort_index(axis=1)
    ds = list(C.columns)
    i = len(ds) - 1
    R = C.pct_change(axis=1, fill_method=None)
    AMT = RAWC * V

    # ── 가드 (us_notify_test·스캔과 동일) ─────────────────────────────
    w = R[ds[i - 20: i + 1]]
    n = w.notna().sum(axis=1)
    rv = w.std(axis=1, ddof=1)
    amt20 = AMT[ds[i - 19: i + 1]].mean(axis=1)
    ok = (rv >= 0.003) & ((w == 0).sum(axis=1) / n.where(n > 0) <= 0.5) & \
         (RAWC[ds[i]] >= 5.0) & (amt20 >= 1e6)

    # ── 점수 팩터 (동일 로직 — mom12 core) ────────────────────────────
    w63 = R[ds[i - 62: i + 1]]
    F = pd.DataFrame(index=C.index)
    F["mom12"] = C[ds[i - 21]] / C[ds[i - 252]] - 1
    F["upratio63"] = (w63 > 0).sum(axis=1) / w63.notna().sum(axis=1)
    F["size_amt"] = np.log10(amt20.where(amt20 > 0))
    F = F[ok.reindex(F.index).fillna(False)]
    score = None
    for j, f in enumerate(["mom12", "upratio63", "size_amt"]):
        rk = F[f].rank(pct=True, ascending=True)
        filled = rk if j == 0 else rk.fillna(0.5)
        if j == 0:
            core = rk.notna()
        score = filled if score is None else score + filled
    score = score.where(core).dropna().sort_values(ascending=False)

    if outputs_only:   # v21: 출력물 복원 — 페이지 순위는 항상 저장된 기록을 따른다(같은 날짜에 기록과 페이지가 두 순위를 갖지 않게)
        keep = [s for s in sorted(stored_rank, key=stored_rank.get) if s in score.index]
        if len(keep) != len(stored_rank) or len(keep) != len(score):
            print(f"::warning::저장 {len(stored_rank):,}종목 · 재계산 {len(score):,}종목 · 공통 {len(keep):,} — 페이지는 저장 순위의 공통 종목만")
        score = score.reindex(keep)

    # ── v20 관측 모델 us_mus_v1_ind: 업종 EW mom12 상위 20% 업종(구성≥10) 안에서 base 점수 상위 50 ──
    #   순위는 게이트 통과 부분집합 안에서 다시 매긴다(스캔의 ranksum(sub, …) 과 동일). 업종 미보유 심볼은 제외.
    ind_score, n_ind = None, 0
    ind_of = pd.Series({s: industries.get(s) for s in F.index}, dtype=object).dropna()
    if len(ind_of):
        Fi = F.loc[ind_of.index].assign(ind=ind_of)
        g = Fi.groupby("ind")["mom12"].agg(["mean", "count"])
        g = g[g["count"] >= IND_MIN_N]
        if len(g):
            top_ind = g[g["mean"] >= g["mean"].quantile(IND_TOP_Q)].index
            n_ind = len(top_ind)
            sub = Fi[Fi["ind"].isin(top_ind)]
            s2 = None
            for j, f in enumerate(["mom12", "upratio63", "size_amt"]):
                rk2 = sub[f].rank(pct=True, ascending=True)
                if j == 0:
                    core2 = rk2.notna()
                s2 = rk2 if s2 is None else s2 + rk2.fillna(0.5)
            ind_score = s2.where(core2).dropna().sort_values(ascending=False).head(IND_TOP_N)
    if ind_score is None and ds[i] >= IND_START:   # v21: 판정 후보가 빠지는 날 — 조용히 넘어가지 않게
        print(f"::warning::{IND_MODEL_ID} {ds[i]}: 계산 불가(업종 {len(industries):,}개 · 구성≥{IND_MIN_N} 업종 없음) — 미적재(판정 빈 날)")

    # ── 관측 컬럼 (점수 미포함 — 참고 전용) ────────────────────────────
    idx = score.index
    v63 = V[ds[i - 62: i + 1]].loc[idx]
    vol_cv = v63.std(axis=1, ddof=1) / v63.mean(axis=1)   # 낮을수록 꾸준(관측)
    ret_1w = (C[ds[i]] / C[ds[i - 5]] - 1).loc[idx] * 100
    ret_1m = (C[ds[i]] / C[ds[i - 21]] - 1).loc[idx] * 100
    hi252 = C.loc[idx, ds[i - 251]: ds[i]].max(axis=1)
    dd52 = (C.loc[idx, ds[i]] / hi252 - 1) * 100

    names, member = {}, {}
    if SEED_DB.exists():
        s = sqlite3.connect(f"file:{SEED_DB}?mode=ro", uri=True)
        names = {sym.replace(".", "-").replace("$", "-P"): (nm or "") for sym, nm in s.execute(
            "SELECT symbol,name FROM listing_daily WHERE date=(SELECT MAX(date) FROM listing_daily)")}
        # 지수 소속 뱃지 (S&P500·NDX100 — 판단축, 점수 미포함)
        try:
            for idx_name, sym in s.execute(
                    "SELECT idx, symbol FROM index_membership "
                    "WHERE date=(SELECT MAX(date) FROM index_membership)"):
                member.setdefault(sym.replace(".", "-"), set()).add(idx_name)
        except sqlite3.OperationalError:
            pass
        s.close()

    def badge(sym):
        m = member.get(sym, set())
        parts = (["SP500"] if "SP500" in m else []) + (["NDX"] if "NDX100" in m else [])
        return "·".join(parts)

    out = pd.DataFrame({
        "rank": range(1, len(score) + 1),
        "symbol": idx,
        "name": [names.get(s, "").replace(",", " ")[:40] for s in idx],
        "sector": [sectors.get(s, "").replace(",", " ") for s in idx],
        "idx": [badge(s) for s in idx],
        "score": score.round(3).values,
        "mom12_pct": (F.loc[idx, "mom12"] * 100).round(1).values,
        "upratio63_pct": (F.loc[idx, "upratio63"] * 100).round(1).values,
        "amt20_musd": (amt20.loc[idx] / 1e6).round(1).values,
        "vol_cv": vol_cv.round(2).values,
        "ret_1w_pct": ret_1w.round(1).values,
        "ret_1m_pct": ret_1m.round(1).values,
        "dd52w_pct": dd52.round(1).values,
        "close": RAWC.loc[idx, ds[i]].round(2).values,
        "mktcap_busd": [round(mcap[s] / 1e9, 2) if s in mcap else "" for s in idx],
    })
    if outputs_only:   # v21: 순위 열도 저장된 값 그대로(공통 종목만 남아 번호가 비어도 기록과 같게)
        out["rank"] = [stored_rank[s] for s in idx]
    out["top10pct"] = (out["rank"] <= max(1, len(out) // 10)).astype(int)
    out["n_universe"] = len(out)
    out["date"] = ds[i]
    if write_csv:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(OUT, index=False, encoding="utf-8-sig")
        print(f"저장: {OUT} · {len(out):,}종목 · 기준일 {ds[i]}")
    else:
        print(f"({'자동 보충' if catchup else '--asof'} {ds[i]}: CSV 미저장 · {len(out):,}종목 · top10 {list(idx[:10])})")

    # ── 관측 적재: score_daily (가중치 0 — 기록만) ─────────────────────
    # 왜: CSV 는 덮어쓰기라, 본구축(9월~) OOS 판정 때 '그날 점수 → 이후 수익'
    # 매칭이 필요하다. 한국판 history.db 역할. INSERT OR IGNORE = idempotent.
    wcon = sqlite3.connect(OHLCV_DB)
    wcon.execute("""CREATE TABLE IF NOT EXISTS score_daily (
        model TEXT NOT NULL, date TEXT NOT NULL, symbol TEXT NOT NULL,
        rank INTEGER, score REAL, mom12 REAL, upratio63 REAL, size_amt REAL,
        PRIMARY KEY (model, date, symbol))""")
    try:   # v20: 그날 업종을 함께 기록 — OOS 때 업종 게이트를 시점 정보로 재구성(CLAUDE.md 9월 작업 8)
        wcon.execute("ALTER TABLE score_daily ADD COLUMN industry TEXT")
    except sqlite3.OperationalError:
        pass   # 이미 있음

    def _f(sym, col):
        v = F.at[sym, col]
        return None if pd.isna(v) else float(v)

    ind_rows = [] if ind_score is None or ds[i] < IND_START else [
        (IND_MODEL_ID, ds[i], s, k, float(v), _f(s, "mom12"), _f(s, "upratio63"), _f(s, "size_amt"), industries.get(s))
        for k, (s, v) in enumerate(ind_score.items(), 1)]
    rows_sd = []
    for rk, (sym, sc) in enumerate(score.items(), 1):
        rows_sd.append((MODEL_ID, ds[i], sym, rk, float(sc),
                        _f(sym, "mom12"), _f(sym, "upratio63"), _f(sym, "size_amt"), industries.get(sym)))
    if repair:
        # v18: 틸트(us_rvdtc_a)는 여기서 지우지 않는다 — 새 틸트가 계산될 때만 아래에서 교체(v08 은 먼저 지워서, 공매도
        #   커버리지 부족 등으로 틸트 계산이 생략되면 그날 관측이 사라졌다. 소급 불가)
        n_del = wcon.execute("DELETE FROM score_daily WHERE model=? AND date=?",
                             (MODEL_ID, ds[i])).rowcount
        print(f"🛠 REPAIR {ds[i]}: score_daily({MODEL_ID}) 기존 {n_del}행 삭제 → 재적재 {len(rows_sd)}행 (감사 로그)")
    elif asof and asof != latest and not catchup:
        print(f"(--asof {ds[i]}: score_daily 미적재 — --repair 로만 씀)")
        wcon.close()
        return
    if outputs_only:
        wcon.close()
        print(f"score_daily {ds[i]}: 저장된 점수 유지(출력물만 복원) — 적재 생략")
    elif not repair:
        # v15: 같은 날짜를 두 번 계산하면(다음 세션 미수집으로 최신일이 이틀 연속 같을 때) INSERT OR IGNORE 가 새 종목만
        #   덧붙여 두 계산의 순위가 섞인다(실측 20260921: rank 중복 52 · 최대 rank 3,373 < 3,385행). 이미 있으면 손대지 않는다.
        n_have = wcon.execute("SELECT COUNT(*) FROM score_daily WHERE model=? AND date=?",
                              (MODEL_ID, ds[i])).fetchone()[0]
        if n_have:
            has_ind = wcon.execute("SELECT 1 FROM score_daily WHERE model=? AND date=? LIMIT 1", (IND_MODEL_ID, ds[i])).fetchone()
            if ind_rows and not has_ind:   # v21: indmom 만 빠진 날(다른 모델이라 순위가 섞이지 않음) — 판정 빈 날 방지
                c3 = wcon.executemany(SD_INSERT, ind_rows)
                wcon.commit()
                print(f"score_daily {ds[i]}: {IND_MODEL_ID} 만 보충 {c3.rowcount}행")
            print(f"score_daily {ds[i]}: 이미 {n_have:,}행 적재 — 재적재 생략(재계산은 --repair). 틸트 적재도 생략")
            wcon.close()
            return
    if not outputs_only:
        cur = wcon.executemany(SD_INSERT, rows_sd)   # v20: 컬럼명 명시(업종 컬럼 추가 뒤에도 순서 의존 없음)
        if ind_rows:
            if repair:
                wcon.execute("DELETE FROM score_daily WHERE model=? AND date=?", (IND_MODEL_ID, ds[i]))
            c3 = wcon.executemany(SD_INSERT, ind_rows)
            print(f"score_daily 적재: {IND_MODEL_ID} 신규 {c3.rowcount}행 (업종 게이트 통과 {n_ind}개)")
        wcon.commit()   # v21 참고: repair 의 DELETE 와 INSERT 는 이 commit 까지 한 트랜잭션(중간 예외 시 롤백)
        n_total = wcon.execute("SELECT COUNT(*), COUNT(DISTINCT date) FROM score_daily "
                               "WHERE model=?", (MODEL_ID,)).fetchone()
        wcon.close()
        print(f"score_daily 적재: 신규 {cur.rowcount}행 · 누적 {n_total[0]:,}행/{n_total[1]}일 "
              f"(model={MODEL_ID})")

    # ── 급등형 틸트 관측 (2026-07-18): top50 중 고변동(rv63↑)+저공매도(dtc↓) 10 ──
    # 발견(post-hoc, 94주간앵커 in-sample — outputs us_scan2/us_tail 2026-07-18):
    #   day-IC h20: rv63 +0.063 CI[+0.004,+0.122] · dtc −0.041 CI[−0.082,−0.001].
    #   +20%/20d 급등 적중 21.1%(유니버스 6.6%의 3.2배) — 단 −20% 급락도 12.8%(2배),
    #   평균수익은 기본 top10과 차이 없음 → '복권형'(변동 증폭) 관측. 문헌: vol anomaly.
    # ⚠️ 표시·기록 전용(가중치 0). 본구축(9월) PREREGISTER 전 판정·매수 근거 금지.
    # dtc = FINRA days_to_cover. 결제일+14일 지연 적용(PIT 보수 — 공표 지연 반영).
    TILT_POOL, TILT_TOP, SHORT_LAG_D = 50, 10, 14
    try:
        # ⚠️ 2026-07-18 버그픽스: 지식문서 §3의 'us_short.db'를 믿고 별도 파일을 찾았으나
        #   실제 수집기(us_short_collector.py)는 us_ohlcv.db `short_interest`에 쓴다 →
        #   runner에서 파일이 없어 조용히 '생략'됐음. us_ohlcv 우선, 구본 파일 폴백.
        def _short_con():
            c_ = sqlite3.connect(f"file:{OHLCV_DB}?mode=ro", uri=True)
            try:
                c_.execute("SELECT 1 FROM short_interest LIMIT 1")
                return c_
            except sqlite3.OperationalError:
                c_.close()
            alt = DATA_DIR / "us_short.db"
            if alt.exists():
                return sqlite3.connect(f"file:{alt}?mode=ro", uri=True)
            return None

        tilt, settle = None, None
        scon = _short_con()
        if scon is not None:
            import datetime as _dt
            lim = (_dt.datetime.strptime(ds[i], "%Y%m%d")
                   - _dt.timedelta(days=SHORT_LAG_D)).strftime("%Y%m%d")
            row = scon.execute("SELECT MAX(settlement_date) FROM short_interest "
                               "WHERE settlement_date<=?", (lim,)).fetchone()
            if row and row[0]:
                settle = str(row[0])
                dtc_map = dict(scon.execute(
                    "SELECT symbol, days_to_cover FROM short_interest "
                    "WHERE settlement_date=? AND days_to_cover IS NOT NULL", (settle,)))
                pool = list(score.index[:TILT_POOL])
                tf = pd.DataFrame(index=pool)
                tf["rv63"] = w63.loc[pool].std(axis=1, ddof=1)
                tf["dtc"] = pd.Series({s2: dtc_map.get(finra_key(s2)) for s2 in pool}, dtype=float)  # v08 표기 정규화
                tf = tf.dropna()
                if len(tf) >= 25:   # 커버리지 절반 미만이면 순위 무의미 → 생략
                    tf["combo"] = tf["rv63"].rank(pct=True) + (1 - tf["dtc"].rank(pct=True))
                    tilt = tf.sort_values("combo", ascending=False)
            scon.close()
        if tilt is not None:
            tsy = list(tilt.index[:TILT_TOP])
            t_out = pd.DataFrame({
                "rank": range(1, len(tsy) + 1),
                "symbol": tsy,
                "name": [names.get(s2, "").replace(",", " ")[:40] for s2 in tsy],
                "sector": [sectors.get(s2, "").replace(",", " ") for s2 in tsy],
                "mus_rank": [int(score.index.get_loc(s2)) + 1 for s2 in tsy],
                "rv63": tilt.loc[tsy, "rv63"].round(4).values,
                "dtc": tilt.loc[tsy, "dtc"].round(2).values,
                "ret_1w_pct": ret_1w.loc[tsy].round(1).values,
                "ret_1m_pct": ret_1m.loc[tsy].round(1).values,
                "close": RAWC.loc[tsy, ds[i]].round(2).values,
            })
            t_out["settle"] = settle
            t_out["n_pool"] = len(tilt)
            t_out["date"] = ds[i]
            if write_csv:
                t_out.to_csv(HERE / "docs" / "data" / "us_tilt.csv",
                             index=False, encoding="utf-8-sig")
            # score_daily 관측 적재(별도 model id — 풀 50 순위 기록, 본구축 판정 매칭용)
            if outputs_only:   # v21: 출력물 복원 모드 — 틸트 CSV 만 다시 쓰고 기록은 건드리지 않는다
                print(f"틸트: us_tilt.csv 만 복원(score_daily {TILT_MODEL_ID} 기록 유지)")
            else:
                wc2 = sqlite3.connect(OHLCV_DB)
                if repair:   # v18: 새 틸트가 있을 때만 교체
                    nd2 = wc2.execute("DELETE FROM score_daily WHERE model=? AND date=?",
                                      (TILT_MODEL_ID, ds[i])).rowcount
                    print(f"🛠 REPAIR {ds[i]}: {TILT_MODEL_ID} 기존 {nd2}행 교체")
                recs2 = [(TILT_MODEL_ID, ds[i], s2, rk2, float(tilt.at[s2, "combo"]),
                          None, None, None) for rk2, s2 in enumerate(tilt.index, 1)]
                c2 = wc2.executemany(SD_INSERT, [r + (None,) for r in recs2])   # v20: 컬럼명 명시(업종 없음)
                wc2.commit()
                nt = wc2.execute("SELECT COUNT(DISTINCT date) FROM score_daily WHERE model=?",
                                 (TILT_MODEL_ID,)).fetchone()[0]
                wc2.close()
                print(f"틸트 저장: us_tilt.csv {len(tsy)}종목(결제일 {settle}) · "
                      f"score_daily {TILT_MODEL_ID} 신규 {c2.rowcount}행 · 누적 {nt}일")
        else:
            print("틸트 생략: 공매도 데이터 없음/커버리지 부족 (비치명)")
    except Exception as e:
        print(f"틸트 실패(비치명): {e}")
    if write_csv:
        try:
            write_history(C, ds[i])
        except Exception as e:
            print(f"히스토리 저장 실패(비치명): {e}")
    return True   # v19: 끝까지 계산·적재함 — --repair 결과 판정용(조기 반환은 None)


def _soft_fail(tag):
    """v21: 비치명으로 삼킨 실패를 워크플로 실패 알림에 알린다(GITHUB_ENV 에 SOFT_FAIL_<tag>=1). 로컬 실행에선 아무것도 안 함.
    스크립트가 예외를 삼키고 exit 0 으로 끝나면 continue-on-error 스텝의 outcome 이 success 라 알림 조건이 못 잡았다."""
    p = os.environ.get("GITHUB_ENV")
    if p:
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(f"SOFT_FAIL_{tag}=1\n")


def _csv_date(path):
    """v21: 페이지 CSV 의 기준일(첫 데이터 행 date 열). 없거나 읽을 수 없으면 None."""
    try:
        import csv
        with open(path, encoding="utf-8-sig", newline="") as fh:
            row = next(csv.DictReader(fh), None)
        return (row or {}).get("date") or None
    except (OSError, StopIteration):
        return None


SD_INSERT = ("INSERT OR IGNORE INTO score_daily "
             "(model, date, symbol, rank, score, mom12, upratio63, size_amt, industry) VALUES (?,?,?,?,?,?,?,?,?)")


def write_history(C, latest):
    """v20: 모델별 과거 기록 목록 + '기록일 종가 → 최신 기준일 종가' 등락(adj_close, 분할·배당 반영) → docs/data/us_history.json.
    벤치마크를 같이 둔다: 그날 us_mus_v0 가 점수 매긴 가드 유니버스 전체의 동일가중 평균(EW)과 SPX.
    관측 기록일 뿐 — 비용 0 · 종가 기준 · 상장폐지 종목은 최신 종가가 없어 평균에서 빠진다(생존편향 방향)."""
    con = sqlite3.connect(f"file:{OHLCV_DB}?mode=ro", uri=True)
    spx = {}
    mdb = OHLCV_DB.with_name("us_market.db")
    if mdb.exists():
        mc = sqlite3.connect(f"file:{mdb.as_posix()}?mode=ro", uri=True)
        spx = dict(mc.execute("SELECT date, close FROM market_daily WHERE series='SPX'"))
        mc.close()
    cols = list(C.columns)
    last = C[latest]
    out = {"generated_utc": _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M"), "latest": latest, "models": {}}
    for m, top_n in HISTORY_SHOW.items():
        rows = []
        for (d,) in con.execute("SELECT DISTINCT date FROM score_daily WHERE model=? ORDER BY date DESC", (m,)):
            if d not in C.columns:
                continue
            ret = (last / C[d] - 1) * 100
            picks = con.execute("SELECT symbol, rank FROM score_daily WHERE model=? AND date=? AND rank<=? "
                                "ORDER BY rank", (m, d, top_n)).fetchall()
            uni = [s for (s,) in con.execute("SELECT symbol FROM score_daily WHERE model=? AND date=?", (MODEL_ID, d))]
            items = [[s, r, (round(float(ret[s]), 2) if s in ret.index and pd.notna(ret[s]) else None)] for s, r in picks]
            vals = [x[2] for x in items if x[2] is not None]
            v10 = [x[2] for x in items[:10] if x[2] is not None]
            ew = ret.reindex(uni).dropna() if uni else pd.Series(dtype=float)
            rows.append({
                "date": d, "td": cols.index(latest) - cols.index(d), "n": len(items),
                "avg": round(float(np.mean(vals)), 2) if vals else None,
                "avg10": round(float(np.mean(v10)), 2) if v10 else None,
                "hit": round(100 * sum(v > 0 for v in vals) / len(vals)) if vals else None,
                "ew": round(float(ew.mean()), 2) if len(ew) else None,
                "spx": round((spx[latest] / spx[d] - 1) * 100, 2) if latest in spx and d in spx else None,
                "items": items})
        out["models"][m] = {"top_n": top_n, "rows": rows}
    con.close()
    HISTORY_OUT.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"저장: {HISTORY_OUT} · " + " · ".join(f"{m} {len(v['rows'])}일" for m, v in out["models"].items()))


def pending_catchup():
    """v09 자동 보충 대상: 최신일 앞 CATCHUP_DAYS 거래일 중 score_daily(MODEL_ID) 가 비어 있고 완전성(전일 대비
    COMPLETENESS_MIN)을 채운 날짜. 게이트에 걸려 건너뛴 날을 다음 실행의 7일 창이 채운 경우(실측 2026-09-09·10).
    부분 유니버스로 이미 적재된 날(실측 2026-09-02·03·08)은 건드리지 않고 REPAIR 권고만 출력 — 감사 로그가
    남는 --repair(수동)로 고친다."""
    if not OHLCV_DB.exists():
        return []
    con = sqlite3.connect(f"file:{OHLCV_DB}?mode=ro", uri=True)
    dates, counts = trading_dates(con, index_dates=index_dates_for(OHLCV_DB))
    try:
        scored = dict(con.execute("SELECT date, COUNT(*) FROM score_daily WHERE model=? GROUP BY date", (MODEL_ID,)))
        scored_ind = {d for (d,) in con.execute("SELECT DISTINCT date FROM score_daily WHERE model=?", (IND_MODEL_ID,))}
    except sqlite3.OperationalError:
        scored, scored_ind = {}, set()
    con.close()
    window = dates[-(CATCHUP_DAYS + 1):-1]   # 최신일 제외(최신일은 본 실행이 계산)
    todo, thin = [], []
    for d in window:
        k = dates.index(d)
        ratio = completeness(counts, d, dates[k - 1] if k > 0 else None)[2]
        if d in scored:
            # 가드 통과 유니버스(GUARD_PASS_FRAC)의 90% 미만이면 부분 적재 의심
            if ratio >= COMPLETENESS_MIN and scored[d] < COMPLETENESS_MIN * GUARD_PASS_FRAC * counts[d]:
                thin.append(f"{d}({scored[d]:,})")
        elif ratio >= COMPLETENESS_MIN:
            todo.append(d)
        if d in scored and d >= IND_START and d not in scored_ind and ratio >= COMPLETENESS_MIN:
            todo.append(d)   # v21: v0 는 있는데 indmom 만 빠진 날 — main(catchup) 이 indmom 만 채운다
    if todo:
        print(f"↺ 자동 보충 대상 {len(todo)}일: {', '.join(todo)} (score_daily 공백 + 시세 완전성 충족)")
    if thin:
        print(f"::warning::🛠 REPAIR 권고(부분 유니버스로 적재된 의심일 — 수동 repair_dates): {', '.join(thin)}")
    return todo


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=None, help="YYYYMMDD — 그날 기준 계산만(쓰기 없음)")
    ap.add_argument("--repair", default=None, help="YYYYMMDD[,YYYYMMDD…] — 부분 수집일 재계산(삭제 후 적재, 수동 전용)")
    args = ap.parse_args()
    try:
        if args.repair:
            # v19: 날짜별 결과를 모아 하나라도 재계산 못 했으면 exit 1(수동 경로 전용 — 워크플로가 업로드 뒤 판정).
            #   이전엔 없는 날짜·완전성 미달·예외가 모두 로그 한 줄 뒤 녹색이었다.
            failed = []
            for d_ in [x.strip() for x in args.repair.split(",") if x.strip()]:
                try:
                    if not main(asof=d_, repair=True):
                        failed.append(d_)
                except Exception as e:
                    print(f"❌ REPAIR {d_} 예외: {e}")
                    failed.append(d_)
            if failed:
                print(f"::error::REPAIR 미완료: {', '.join(failed)} — 로그의 '🛠 REPAIR'·'⚠️' 줄 확인")
                sys.exit(1)
        elif args.asof:
            main(asof=args.asof)
        else:
            for d_ in pending_catchup():   # v09: 게이트에 걸렸다가 채워진 날 먼저 보충
                main(asof=d_, catchup=True)
            main()
    except Exception as e:
        print(f"::error::❌ us_page_data 실패(비치명 — 업로드는 계속): {e}")
        _soft_fail("PAGE")
        sys.exit(0)
