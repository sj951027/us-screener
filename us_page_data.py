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
환경: US_DATA_DIR (기본 ../us-screener-data)
원칙: 비치명(데이터 없으면 생략) · CSV 이름의 콤마는 공백 치환(JS 단순 파서 호환)
"""
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

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


def main():
    if not OHLCV_DB.exists():
        print(f"us_ohlcv.db 없음({OHLCV_DB}) — 생략(비치명).")
        return
    con = sqlite3.connect(f"file:{OHLCV_DB}?mode=ro", uri=True)
    dates = [d for (d,) in con.execute(
        "SELECT DISTINCT date FROM daily_ohlcv ORDER BY date")][-LOOKBACK:]
    raw = pd.read_sql(
        "SELECT symbol,date,close,adj_close,volume FROM daily_ohlcv WHERE date>=?",
        con, params=(dates[0],))
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
        names = {sym.replace(".", "-"): (nm or "") for sym, nm in s.execute(
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
    out["top10pct"] = (out["rank"] <= max(1, len(out) // 10)).astype(int)
    out["n_universe"] = len(out)
    out["date"] = ds[i]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"저장: {OUT} · {len(out):,}종목 · 기준일 {ds[i]}")

    # ── 관측 적재: score_daily (가중치 0 — 기록만) ─────────────────────
    # 왜: CSV 는 덮어쓰기라, 본구축(9월~) OOS 판정 때 '그날 점수 → 이후 수익'
    # 매칭이 필요하다. 한국판 history.db 역할. INSERT OR IGNORE = idempotent.
    wcon = sqlite3.connect(OHLCV_DB)
    wcon.execute("""CREATE TABLE IF NOT EXISTS score_daily (
        model TEXT NOT NULL, date TEXT NOT NULL, symbol TEXT NOT NULL,
        rank INTEGER, score REAL, mom12 REAL, upratio63 REAL, size_amt REAL,
        PRIMARY KEY (model, date, symbol))""")
    rows_sd = []
    for rk, (sym, sc) in enumerate(score.items(), 1):
        rows_sd.append((MODEL_ID, ds[i], sym, rk, float(sc),
                        None if pd.isna(F.at[sym, "mom12"]) else float(F.at[sym, "mom12"]),
                        None if pd.isna(F.at[sym, "upratio63"]) else float(F.at[sym, "upratio63"]),
                        None if pd.isna(F.at[sym, "size_amt"]) else float(F.at[sym, "size_amt"])))
    cur = wcon.executemany(
        "INSERT OR IGNORE INTO score_daily VALUES (?,?,?,?,?,?,?,?)", rows_sd)
    wcon.commit()
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
    TILT_MODEL_ID = "us_rvdtc_a"
    TILT_POOL, TILT_TOP, SHORT_LAG_D = 50, 10, 14
    try:
        short_db = DATA_DIR / "us_short.db"
        tilt, settle = None, None
        if short_db.exists():
            import datetime as _dt
            lim = (_dt.datetime.strptime(ds[i], "%Y%m%d")
                   - _dt.timedelta(days=SHORT_LAG_D)).strftime("%Y%m%d")
            scon = sqlite3.connect(f"file:{short_db}?mode=ro", uri=True)
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
                tf["dtc"] = pd.Series({s2: dtc_map.get(s2) for s2 in pool}, dtype=float)
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
            t_out.to_csv(HERE / "docs" / "data" / "us_tilt.csv",
                         index=False, encoding="utf-8-sig")
            # score_daily 관측 적재(별도 model id — 풀 50 순위 기록, 본구축 판정 매칭용)
            wc2 = sqlite3.connect(OHLCV_DB)
            recs2 = [(TILT_MODEL_ID, ds[i], s2, rk2, float(tilt.at[s2, "combo"]),
                      None, None, None) for rk2, s2 in enumerate(tilt.index, 1)]
            c2 = wc2.executemany(
                "INSERT OR IGNORE INTO score_daily VALUES (?,?,?,?,?,?,?,?)", recs2)
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


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ 실패(비치명): {e}")
        sys.exit(0)
