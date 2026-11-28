# -*- coding: utf-8 -*-
"""
us_event_scan_20261128.py — 이벤트형 새 재료 첫 스캔: 8-K 항목 · 애널리스트 등급/목표가 변경 (in-sample, 관측 전용)
=====================================================================================================
질문(둘 다 처음 보는 자료 — 가격 재조합 아님):
  A. 8-K 항목별: 어떤 항목의 공시가 나온 종목이 그 뒤 20거래일 동안 가드 유니버스 평균보다 낫거나 못한가.
     자료 = us_fundamentals.db earnings_events 의 form='8-K' · is_earnings=0(실적 공표 2.02 동반 건 제외) · items · accepted(PIT).
  B. 애널리스트: 등급 상향/하향, 목표가 상향/하향, 목표가 변화율 순위(IC).
     자료 = us_analyst.db rating_changes(ts = 이벤트 시각, action up/down, pt_current·pt_prior).
프레임(CLAUDE.md '이벤트형 신호는 조건부 그룹 초과'):
  주간 앵커(5거래일 간격) t 마다 '직전 앵커일 ≤ 이벤트일 < t' 인 종목을 그룹으로 묶어
  x_t = 그룹 동일가중 h20 수익 − 가드 유니버스 동일가중 h20 수익. 평균과 블록 부트스트랩 CI(블록 4 = h20/주간, 10,000회).
  이벤트일은 UTC 날짜를 쓰고 앵커 당일 건은 다음 앵커로 넘긴다(장 마감 뒤 공시가 당일 종가에 섞이지 않게 — 보수적).
정직성:
- in-sample · 종가 체결 · 비용 0. 시세 DB 는 2026-07 상장목록에서 출발해 그 전에 사라진 종목이 없다(생존편향 미보정).
  창 안에서 사라진 종목은 마지막 종가로 청산(ffill ≤20일).
- cik→티커는 현재 매핑(과거 티커 변경 미반영). 등급 이력은 야후가 지금 주는 소급분이고, 이력 수집이 끝난 종목만 있다.
- 검정 수 = (평균 그룹 크기 ≥ MIN_AVG_N 인 8-K 항목 수) + 애널리스트 5개 → Bonferroni 병기. 결과는 '기움'이지 채택이 아니다.

    python research/us_event_scan_20261128.py --data-dir <정본 tar 를 푼 폴더>
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
H = 20                 # 전방 수익 구간(프로젝트 표준 h20)
STEP = 5               # 주간 앵커
BLOCK = H // STEP      # 겹치는 창 길이만큼의 블록(기존 스캔과 같은 4)
NBOOT = 10_000
SEED = 20261128
MIN_AVG_N = 10         # 그룹 평균이 한 종목에 좌우되지 않는 최소 평균 구성 수(us_page_data IND_MIN_N 과 같은 취지)
COVER_DAYS = 365       # '애널리스트가 다루는 종목' = 직전 1년에 등급 이벤트가 1건 이상
SKIP_ITEMS = {"9.01", "2.02"}   # 9.01 = 첨부(거의 모든 8-K 에 동반), 2.02 = 실적 공표(SUE·pead 스캔에서 이미 봄)
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:5.0f}s]", *a, flush=True)


def ro(path):
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def boot_ci(x, alphas, rng):
    """원형 블록 부트스트랩(블록 BLOCK) 평균의 percentile CI. 반환 {alpha: (lo, hi)}."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 2 * BLOCK:
        return {a: (np.nan, np.nan) for a in alphas}
    nblk = int(np.ceil(n / BLOCK))
    starts = rng.integers(0, n, (NBOOT, nblk))
    sel = (starts[:, :, None] + np.arange(BLOCK)[None, None, :]).reshape(NBOOT, -1)[:, :n] % n
    means = x[sel].mean(axis=1)
    return {a: (float(np.quantile(means, a / 2)), float(np.quantile(means, 1 - a / 2))) for a in alphas}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    args = ap.parse_args()
    D = Path(args.data_dir)

    log("시세 적재 …")
    con = ro(D / "us_ohlcv.db")
    df = pd.read_sql_query("SELECT symbol, date, close, adj_close, volume FROM daily_ohlcv", con)
    C = df.pivot(index="symbol", columns="date", values="adj_close")
    RC = df.pivot(index="symbol", columns="date", values="close")
    V = df.pivot(index="symbol", columns="date", values="volume")
    del df
    cnt = C.notna().sum()
    C, RC, V = C.loc[:, cnt >= 0.1 * cnt.max()], RC.loc[:, cnt >= 0.1 * cnt.max()], V.loc[:, cnt >= 0.1 * cnt.max()]   # 휴장일 잔행 제외
    ds = sorted(C.columns)
    Cf = C.ffill(axis=1, limit=H)          # 창 안에서 사라지면 마지막 종가로 청산
    log("시세", C.shape, ds[0], "→", ds[-1])
    anchors = list(range(63, len(ds) - H, STEP))
    log("앵커", len(anchors), ds[anchors[0]], "→", ds[anchors[-1]])

    # ── A. 8-K 항목 ──
    fc = ro(D / "us_fundamentals.db")
    ct = pd.read_sql_query("SELECT cik, ticker FROM cik_ticker", fc)
    ct["ticker"] = ct["ticker"].str.upper()
    ct = ct[ct["ticker"].isin(C.index)]
    ev = pd.read_sql_query("SELECT cik, accepted, filed, items FROM earnings_events "
                           "WHERE form='8-K' AND is_earnings=0 AND items IS NOT NULL AND items!=''", fc)
    ev["d8"] = ev["accepted"].where(ev["accepted"].fillna("") != "", ev["filed"]).str.slice(0, 10).str.replace("-", "", regex=False)
    ev = ev.merge(ct, on="cik")
    ev["item"] = ev["items"].str.split(",")
    ev = ev.explode("item")
    ev["item"] = ev["item"].str.strip()
    ev = ev[(ev["item"] != "") & ~ev["item"].isin(SKIP_ITEMS) & (ev["d8"] >= ds[0])][["ticker", "d8", "item"]]
    ev = ev.drop_duplicates().sort_values("d8").reset_index(drop=True)
    log("8-K (종목, 날짜, 항목) 행", len(ev), "· 항목 종류", ev["item"].nunique())

    # ── B. 애널리스트 ──
    ac = ro(D / "us_analyst.db")
    rc = pd.read_sql_query("SELECT symbol, ts, action, pt_current, pt_prior FROM rating_changes", ac)
    fetched = {s for (s,) in ac.execute("SELECT symbol FROM history_done")}
    rc["d8"] = rc["ts"].str.slice(0, 10).str.replace("-", "", regex=False)
    rc = rc[rc["symbol"].isin(C.index)].sort_values("d8").reset_index(drop=True)
    ok_pt = (rc["pt_current"] > 0) & (rc["pt_prior"] > 0)
    rc["pt_chg"] = np.where(ok_pt, np.log(rc["pt_current"].where(ok_pt, 1) / rc["pt_prior"].where(ok_pt, 1)), np.nan)
    log("등급 이벤트", len(rc), "· 종목", rc["symbol"].nunique(), "· 이력 수집 끝난 종목", len(fetched), "·", rc["d8"].min(), "→", rc["d8"].max())
    ev_d, rc_d = ev["d8"].to_numpy(), rc["d8"].to_numpy()

    series = {}      # 검정 이름 → 앵커별 초과수익 리스트(없으면 NaN)
    sizes = {}       # 검정 이름 → 앵커별 그룹 크기
    alt = {}         # 애널리스트: '다루는 종목' 평균 대비 초과
    ic_pt = []
    years = []
    for i in anchors:
        t, lo = ds[i], ds[i - STEP]
        c63 = C[ds[i - 62:i + 1]]
        amt20 = (RC[ds[i - 19:i + 1]] * V[ds[i - 19:i + 1]]).mean(axis=1)
        ok = (RC[t] >= 5) & (amt20 >= 1e6) & (c63.notna().sum(axis=1) >= 60) & (c63.std(axis=1, ddof=1) > 0)
        idx = ok[ok].index
        fwd = (Cf.loc[idx, ds[i + H]] / C.loc[idx, t] - 1).dropna()
        u = fwd.mean()
        years.append(t[:4])

        e = ev.iloc[np.searchsorted(ev_d, lo, "left"):np.searchsorted(ev_d, t, "left")]
        for item, g in e.groupby("item"):
            mem = fwd.reindex(g["ticker"].unique()).dropna()
            series.setdefault(f"8K {item}", {})[t] = mem.mean() - u if len(mem) else np.nan
            sizes.setdefault(f"8K {item}", {})[t] = len(mem)

        r = rc.iloc[np.searchsorted(rc_d, lo, "left"):np.searchsorted(rc_d, t, "left")]
        lo_cov = (pd.Timestamp(t) - pd.Timedelta(days=COVER_DAYS)).strftime("%Y%m%d")
        cov = set(rc.iloc[np.searchsorted(rc_d, lo_cov, "left"):np.searchsorted(rc_d, t, "left")]["symbol"].unique())
        u_cov = fwd.reindex([s for s in idx if s in cov]).dropna().mean()
        ups, downs = set(r[r["action"] == "up"]["symbol"]), set(r[r["action"] == "down"]["symbol"])
        pt = r.groupby("symbol")["pt_chg"].mean().dropna()
        groups = {"AN 등급 상향": ups - downs, "AN 등급 하향": downs - ups,
                  "AN 목표가 상향": set(pt[pt > 0].index), "AN 목표가 하향": set(pt[pt < 0].index)}
        for name, syms in groups.items():
            mem = fwd.reindex(list(syms)).dropna()
            series.setdefault(name, {})[t] = mem.mean() - u if len(mem) else np.nan
            alt.setdefault(name, {})[t] = mem.mean() - u_cov if len(mem) else np.nan
            sizes.setdefault(name, {})[t] = len(mem)
        j = pd.concat([pt, fwd], axis=1, join="inner").dropna()
        ic_pt.append(j.iloc[:, 0].rank().corr(j.iloc[:, 1].rank()) if len(j) >= 2 * MIN_AVG_N else np.nan)
        sizes.setdefault("AN 목표가 변화율 IC", {})[t] = len(j)
    ts = [ds[i] for i in anchors]
    series["AN 목표가 변화율 IC"] = dict(zip(ts, ic_pt))
    log("앵커 루프 끝")

    X = pd.DataFrame(series).reindex(ts)
    N = pd.DataFrame(sizes).reindex(ts).fillna(0)
    ALT = pd.DataFrame(alt).reindex(ts)
    tests = [c for c in X.columns if N[c].mean() >= MIN_AVG_N]
    dropped = [(c, round(float(N[c].mean()), 1)) for c in X.columns if c not in tests]
    ntest = len(tests)
    alphas = (0.05, 0.05 / ntest)
    rng = np.random.default_rng(SEED)
    yr = pd.Series(years, index=ts)
    rows = []
    for c in tests:
        x = X[c]
        ci = boot_ci(x.to_numpy(), alphas, rng)
        half = len(ts) // 2
        rec = {"test": c, "mean": x.mean(), "lo95": ci[0.05][0], "hi95": ci[0.05][1],
               "lo_bonf": ci[alphas[1]][0], "hi_bonf": ci[alphas[1]][1],
               "n_anchor": int(x.notna().sum()), "avg_n": float(N[c].mean()), "hit": float((x.dropna() > 0).mean()),
               "first_half": x.iloc[:half].mean(), "second_half": x.iloc[half:].mean()}
        if c in ALT:
            rec["mean_vs_covered"] = ALT[c].mean()
        for y, g in x.groupby(yr):
            rec[f"y{y}"] = g.mean()
        rows.append(rec)
    R = pd.DataFrame(rows).sort_values("mean")
    out = HERE / "us_event_scan_frame_20261128.csv"
    R.to_csv(out, index=False, encoding="utf-8-sig", float_format="%.5f")
    X[tests].to_csv(HERE / "us_event_scan_anchors_20261128.csv", encoding="utf-8-sig", float_format="%.5f")

    ycols = sorted(c for c in R.columns if c.startswith("y2"))
    print(f"\n검정 {ntest}개 (Bonferroni α = 0.05/{ntest}) · 앵커 {len(ts)}개 {ts[0]}→{ts[-1]} · h{H} · 단위: 초과수익 %p(IC 행은 상관계수)")
    print(f"{'검정':<22}{'평균':>8} {'95% CI':>19} {'Bonf CI':>19} {'앵커':>5}{'평균n':>7}{'적중':>6} {'전반':>7}{'후반':>7}  " + " ".join(f"{y[1:]:>6}" for y in ycols))
    for _, r in R.iterrows():
        k = 1 if "IC" in r["test"] else 100
        s95 = "*" if (r["lo95"] > 0 or r["hi95"] < 0) else " "
        sb = "**" if (r["lo_bonf"] > 0 or r["hi_bonf"] < 0) else "  "
        print(f"{r['test']:<22}{r['mean'] * k:>+8.3f} [{r['lo95'] * k:>+7.3f},{r['hi95'] * k:>+7.3f}]{s95} [{r['lo_bonf'] * k:>+7.3f},{r['hi_bonf'] * k:>+7.3f}]{sb}"
              f"{int(r['n_anchor']):>5}{r['avg_n']:>7.1f}{r['hit']:>6.0%} {r['first_half'] * k:>+7.2f}{r['second_half'] * k:>+7.2f}  "
              + " ".join(f"{r[y] * k:>+6.2f}" for y in ycols)
              + (f"   (다루는 종목 대비 {r['mean_vs_covered'] * 100:+.2f})" if pd.notna(r.get("mean_vs_covered")) else ""))
    print(f"\n평균 그룹 크기 < {MIN_AVG_N} 이라 검정에서 뺀 항목: {dropped}")
    print(f"저장: {out.name} · us_event_scan_anchors_20261128.csv")


if __name__ == "__main__":
    main()
