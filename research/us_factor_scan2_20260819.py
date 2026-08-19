# -*- coding: utf-8 -*-
"""
us_factor_scan2_20260819.py — 2차 팩터 스캔 (in-sample · 관측 전용)
====================================================================
질문: 8/18 기준 DB(가격·거래량·FINRA 공매도)에서 us_mus_v0 를 넘어설
      재료가 있는가.
프레임1: 전 유니버스 day-IC(스피어만, h20) — 팩터 8종, 주간 앵커.
프레임2: 유망 팩터를 us_mus_v0 순위합에 4번째 항으로 추가 → top50 짝비교.
프레임3: 레짐 게이트(SPX 200MA) — mus top50 초과수익이 레짐에 따라 다른가.

정직성:
- 전부 in-sample · 생존편향 미보정(백필=2026-07 상장목록) · 상승장 편중.
- 주간 앵커의 h20 창 중첩(≈4배) → 순진한 CI는 과소. 블록 부트스트랩(블록=4앵커)로 완화.
- 다중검정: 프레임1은 8팩터 → Bonferroni α=0.05/8, 99.375% CI 병기.
- 공매도 PIT: settlement_date + 14일(달력) 이후에만 사용(프로젝트 규칙).
- 결과는 '기움'이지 채택 아님. 채택은 PREREGISTER + OOS 로만.
"""
import sqlite3, sys
import numpy as np, pandas as pd
from scipy.stats import spearmanr
import datetime as dt

DB = "/tmp/us-screener-data/us_ohlcv.db"
MKT = "/tmp/us-screener-data/us_market.db"
RNG = np.random.default_rng(20260819)
NBOOT = 10000

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
print("loading ohlcv ...", flush=True)
df = pd.read_sql("select symbol,date,close,adj_close,volume from daily_ohlcv", con)
C  = df.pivot(index="symbol", columns="date", values="adj_close")
RC = df.pivot(index="symbol", columns="date", values="close")
V  = df.pivot(index="symbol", columns="date", values="volume")
del df
ds = sorted(C.columns)
print("matrix", C.shape, ds[0], ds[-1], flush=True)

si = pd.read_sql("select settlement_date,symbol,days_to_cover from short_interest", con)
settles = sorted(si.settlement_date.unique())
si_piv = si.pivot(index="symbol", columns="settlement_date", values="days_to_cover")
def d2dt(s): return dt.date(int(s[:4]), int(s[4:6]), int(s[6:]))
settle_ok = {}  # anchor date -> (latest usable settle, prev settle)
for t in ds:
    td = d2dt(t)
    usable = [s for s in settles if (td - d2dt(s)).days >= 14]
    settle_ok[t] = (usable[-1] if usable else None,
                    usable[-2] if len(usable) > 1 else None)

# ── 앵커: 주간(5거래일 간격), mom12 창 확보(i>=252) · h20 확보(i<=last-20)
anchors = [i for i in range(252, len(ds) - 20, 5)]
print("anchors:", len(anchors), ds[anchors[0]], "→", ds[anchors[-1]], flush=True)

FACTORS = ["vol_cv", "dd63", "dd252", "rv63", "dtc", "dtc_chg", "mom6", "upratio126"]

def guard_universe(i):
    """가드: raw close>=5 · amt20>=1M · 63일 이력 · 무변동 컷"""
    t = ds[i]
    c63 = C[ds[i-62:i+1]]
    amt20 = (RC[ds[i-19:i+1]] * V[ds[i-19:i+1]]).mean(axis=1)
    ok = (RC[t] >= 5) & (amt20 >= 1e6) & (c63.notna().sum(axis=1) >= 60) \
         & (c63.std(axis=1, ddof=1) > 0)
    return ok[ok].index, amt20

def calc_factors(i, idx):
    t = ds[i]
    w63  = C[ds[i-62:i+1]].loc[idx].pct_change(axis=1)
    v63  = V[ds[i-62:i+1]].loc[idx]
    out = pd.DataFrame(index=idx)
    out["vol_cv"] = v63.std(axis=1, ddof=1) / v63.mean(axis=1)
    out["dd63"]   = C.loc[idx, t] / C.loc[idx, ds[i-62:i+1]].max(axis=1) - 1
    out["dd252"]  = C.loc[idx, t] / C.loc[idx, ds[i-251:i+1]].max(axis=1) - 1
    out["rv63"]   = w63.std(axis=1, ddof=1)
    s_now, s_prev = settle_ok[t]
    if s_now and s_now in si_piv.columns:
        out["dtc"] = si_piv[s_now].reindex(idx)
        if s_prev and s_prev in si_piv.columns:
            out["dtc_chg"] = si_piv[s_now].reindex(idx) - si_piv[s_prev].reindex(idx)
        else:
            out["dtc_chg"] = np.nan
    else:
        out["dtc"] = np.nan; out["dtc_chg"] = np.nan
    out["mom6"] = C.loc[idx, ds[i-21]] / C.loc[idx, ds[i-126]] - 1
    w126 = C[ds[i-125:i+1]].loc[idx].pct_change(axis=1)
    out["upratio126"] = (w126 > 0).sum(axis=1) / w126.notna().sum(axis=1)
    return out

def mus_score(i, idx, amt20):
    """us_mus_v0 재현: mom12(12-1) + upratio63 + size_amt 백분위합"""
    w63 = C[ds[i-62:i+1]].loc[idx].pct_change(axis=1)
    F = pd.DataFrame(index=idx)
    F["mom12"] = C.loc[idx, ds[i-21]] / C.loc[idx, ds[i-252]] - 1
    F["upratio63"] = (w63 > 0).sum(axis=1) / w63.notna().sum(axis=1)
    F["size_amt"] = np.log10(amt20.reindex(idx).where(amt20.reindex(idx) > 0))
    sc = None; core = None
    for j, f in enumerate(["mom12", "upratio63", "size_amt"]):
        rk = F[f].rank(pct=True)
        if j == 0: core = rk.notna()
        sc = rk if sc is None else sc + rk.fillna(0.5)
    return sc.where(core).dropna()

def block_boot_ci(x, alpha_list=(0.05, 0.00625), block=4):
    """circular block bootstrap of mean"""
    x = np.asarray([v for v in x if np.isfinite(v)])
    n = len(x)
    if n < 8: return {a: (np.nan, np.nan) for a in alpha_list}, np.nan
    nblk = int(np.ceil(n / block))
    means = np.empty(NBOOT)
    for b in range(NBOOT):
        starts = RNG.integers(0, n, nblk)
        sel = (starts[:, None] + np.arange(block)[None, :]).ravel() % n
        means[b] = x[sel[:n]].mean()
    return {a: (np.quantile(means, a/2), np.quantile(means, 1-a/2))
            for a in alpha_list}, x.mean()

# ══ 프레임 1 · 3 동시 수집 ══════════════════════════════════════════
ics = {f: [] for f in FACTORS}
cov = {f: [] for f in FACTORS}
base_top50, base_univ = [], []          # 프레임3 재료
frame2_cache = []                       # (i, idx, factors, musscore, fwd)
for k, i in enumerate(anchors):
    idx, amt20 = guard_universe(i)
    fwd = (C.loc[idx, ds[i+20]] / C.loc[idx, ds[i]] - 1)
    fac = calc_factors(i, idx)
    ms  = mus_score(i, idx, amt20)
    frame2_cache.append((i, fac, ms, fwd))
    for f in FACTORS:
        m = fac[f].notna() & fwd.notna()
        cov[f].append(m.mean())
        if m.sum() > 100:
            ics[f].append(spearmanr(fac.loc[m, f], fwd[m]).statistic)
        else:
            ics[f].append(np.nan)
    top = ms.sort_values(ascending=False).index[:50]
    base_top50.append(fwd[top].mean() * 100)
    base_univ.append(fwd.mean() * 100)   # v2 교정: EW평균(원래 중앙값 오류 — followup에서 발견)
    if k % 20 == 0: print(f"  anchor {k}/{len(anchors)}", flush=True)

print("\n══ 프레임1: day-IC h20 (주간앵커 n=%d · 블록부트스트랩 · Bonferroni α=0.00625)" % len(anchors))
rows1 = []
for f in FACTORS:
    cis, m = block_boot_ci(ics[f])
    lo95, hi95 = cis[0.05]; lob, hib = cis[0.00625]
    sig95 = "*" if (lo95 > 0 or hi95 < 0) else " "
    sigB  = "**" if (lob > 0 or hib < 0) else "  "
    n_eff = sum(np.isfinite(v) for v in ics[f])
    rows1.append((f, n_eff, m, lo95, hi95, lob, hib, np.mean(cov[f])))
    print(f"  {f:11s} n={n_eff:3d} IC={m:+.4f} 95%[{lo95:+.4f},{hi95:+.4f}]{sig95} "
          f"Bonf[{lob:+.4f},{hib:+.4f}]{sigB} cover={np.mean(cov[f]):.0%}")

pd.DataFrame(rows1, columns=["factor","n","meanIC","ci95_lo","ci95_hi","bonf_lo","bonf_hi","coverage"]) \
  .to_csv("/tmp/work/research/us_scan2_frame1.csv", index=False)

# ══ 프레임 2: 95% 유의 팩터를 4번째 항으로 top50 짝비교 ═════════════
print("\n══ 프레임2: us_mus_v0 top50 + 4번째 팩터 (짝비교, %p)")
cand = [(f, m) for (f, n, m, lo, hi, lob, hib, c) in rows1 if (lo > 0 or hi < 0)]
rows2 = []
for f, mic in cand:
    sign = 1 if mic > 0 else -1
    diffs, othr = [], []
    for (i, fac, ms, fwd) in frame2_cache:
        rk4 = fac[f].rank(pct=True)
        if sign < 0: rk4 = 1 - rk4
        sc4 = ms + rk4.reindex(ms.index).fillna(0.5)
        t_new = sc4.sort_values(ascending=False).index[:50]
        t_old = ms.sort_values(ascending=False).index[:50]
        diffs.append((fwd[t_new].mean() - fwd[t_old].mean()) * 100)
        othr.append(len(set(t_new) & set(t_old)) / 50)
    cis, m = block_boot_ci(diffs, alpha_list=(0.05,))
    lo, hi = cis[0.05]
    rows2.append((f, m, lo, hi, np.mean(othr)))
    print(f"  +{f:11s} Δtop50 h20 = {m:+.3f}%p 95%CI[{lo:+.3f},{hi:+.3f}] overlap={np.mean(othr):.0%}")
if rows2:
    pd.DataFrame(rows2, columns=["factor","d_pp","ci_lo","ci_hi","overlap"]) \
      .to_csv("/tmp/work/research/us_scan2_frame2.csv", index=False)

# ══ 프레임 3: 레짐 게이트 (SPX 200MA) ═══════════════════════════════
print("\n══ 프레임3: 레짐(SPX>200MA) 별 mus top50 초과수익 (%p / 20d)")
mk = sqlite3.connect(f"file:{MKT}?mode=ro", uri=True)
spx = pd.read_sql("select date, close from market_daily where series='SPX' order by date", mk, index_col="date")["close"]
spx200 = spx.rolling(200, min_periods=100).mean()
exc = np.array(base_top50) - np.array(base_univ)
regime = []
for i in anchors:
    t = ds[i]
    s = spx.reindex([t]).iloc[0] if t in spx.index else np.nan
    m2 = spx200.reindex([t]).iloc[0] if t in spx200.index else np.nan
    regime.append(1 if (np.isfinite(s) and np.isfinite(m2) and s > m2) else 0 if np.isfinite(s) else np.nan)
regime = np.array(regime, dtype=float)
for lab, msk in [("SPX>200MA", regime == 1), ("SPX<=200MA", regime == 0)]:
    x = exc[msk & np.isfinite(exc)]
    if len(x) >= 5:
        cis, m = block_boot_ci(x, alpha_list=(0.05,))
        print(f"  {lab:11s} n={len(x):3d} 초과 {m:+.2f}%p 95%CI[{cis[0.05][0]:+.2f},{cis[0.05][1]:+.2f}]")
    else:
        print(f"  {lab:11s} n={len(x):3d} — 표본 부족(측정 불가)")
cis, m = block_boot_ci(exc, alpha_list=(0.05,))
print(f"  전체         n={np.isfinite(exc).sum():3d} 초과 {m:+.2f}%p 95%CI[{cis[0.05][0]:+.2f},{cis[0.05][1]:+.2f}]")
print("\ndone.")
