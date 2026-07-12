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

    names = {}
    if SEED_DB.exists():
        s = sqlite3.connect(f"file:{SEED_DB}?mode=ro", uri=True)
        names = {sym.replace(".", "-"): (nm or "") for sym, nm in s.execute(
            "SELECT symbol,name FROM listing_daily WHERE date=(SELECT MAX(date) FROM listing_daily)")}
        s.close()

    out = pd.DataFrame({
        "rank": range(1, len(score) + 1),
        "symbol": idx,
        "name": [names.get(s, "").replace(",", " ")[:40] for s in idx],
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


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ 실패(비치명): {e}")
        sys.exit(0)
