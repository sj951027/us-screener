# -*- coding: utf-8 -*-
"""
us_sec_scan_followup.py — SEC 스캔 후속: 독립 밸류 모델 프레임 (in-sample)
프레임3: val3(bm+ep+cfoy 순위합) 독립 top50 vs EW — '새 모델 계열' 가설.
프레임4: 연도별 IC 안정성 + val3 초과와 mus 초과의 상관(분산 효과).
프레임5: 교차(밸류 상위40% ∩ mus 상위) 등 결합 방식 3종 — 전부 in-sample 가설,
         결합 3종 추가 검정 → 95% 와 Bonferroni(α=0.05/3) 병기.
"""
import sqlite3
import numpy as np, pandas as pd
from scipy.stats import spearmanr

DB  = "us-screener-data/us_ohlcv.db"
FDB = "us-screener-data/us_fundamentals.db"
RNG = np.random.default_rng(20260830)
NBOOT = 10000

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
print("loading ohlcv ...", flush=True)
df = pd.read_sql("select symbol,date,close,adj_close,volume from daily_ohlcv", con)
C  = df.pivot(index="symbol", columns="date", values="adj_close")
RC = df.pivot(index="symbol", columns="date", values="close")
V  = df.pivot(index="symbol", columns="date", values="volume")
del df
ds = sorted(C.columns)

fc = sqlite3.connect(f"file:{FDB}?mode=ro", uri=True)
ct = pd.read_sql("select cik, ticker from cik_ticker", fc)
ct["ticker"] = ct.ticker.str.upper()
ct = ct[ct.ticker.isin(C.index)]
cik2syms = ct.groupby("cik")["ticker"].apply(list).to_dict()

def ymd(s): return s.replace("-", "")
xb = pd.read_sql("""select cik, tag, end, val, fp, form, filed from xbrl_facts
                    where tag in ('NetIncomeLoss','StockholdersEquity',
                    'NetCashProvidedByUsedInOperatingActivities',
                    'EntityCommonStockSharesOutstanding','CommonStockSharesOutstanding')""", fc)
xb["filed8"] = xb.filed.map(ymd); xb["end8"] = xb["end"].map(ymd)
xb = xb.sort_values("filed8")
ni_fy  = xb[(xb.tag=="NetIncomeLoss")&(xb.fp=="FY")&(xb.form.str.startswith("10-K"))]
cfo_fy = xb[(xb.tag=="NetCashProvidedByUsedInOperatingActivities")&(xb.fp=="FY")&(xb.form.str.startswith("10-K"))]
se_q   = xb[xb.tag=="StockholdersEquity"]
sh_a   = xb[xb.tag=="EntityCommonStockSharesOutstanding"]
sh_b   = xb[xb.tag=="CommonStockSharesOutstanding"]

def latest_before(tbl, t8, max_end_age_days=455):
    sub = tbl[tbl.filed8 < t8]
    if max_end_age_days is not None:
        cut = (pd.Timestamp(t8) - pd.Timedelta(days=max_end_age_days)).strftime("%Y%m%d")
        sub = sub[sub.end8 >= cut]
    return sub.drop_duplicates("cik", keep="last").set_index("cik")["val"]

def sym_from_cik(series_by_cik, idx):
    out = {}
    for cik, val in series_by_cik.items():
        for s in cik2syms.get(cik, ()): out[s] = val
    return pd.Series(out).reindex(idx)

def guard_universe(i):
    t = ds[i]
    c63 = C[ds[i-62:i+1]]
    amt20 = (RC[ds[i-19:i+1]] * V[ds[i-19:i+1]]).mean(axis=1)
    ok = (RC[t] >= 5) & (amt20 >= 1e6) & (c63.notna().sum(axis=1) >= 60) \
         & (c63.std(axis=1, ddof=1) > 0)
    return ok[ok].index, amt20

def mus_score(i, idx, amt20):
    w63 = C[ds[i-62:i+1]].loc[idx].pct_change(axis=1)
    F = pd.DataFrame(index=idx)
    F["mom12"] = C.loc[idx, ds[i-21]] / C.loc[idx, ds[i-252]] - 1
    F["upratio63"] = (w63 > 0).sum(axis=1) / w63.notna().sum(axis=1)
    F["size_amt"] = np.log10(amt20.reindex(idx).where(amt20.reindex(idx) > 0))
    sc = None; core = None
    for j, f in enumerate(["mom12","upratio63","size_amt"]):
        rk = F[f].rank(pct=True)
        if j == 0: core = rk.notna()
        sc = rk if sc is None else sc + rk.fillna(0.5)
    return sc.where(core).dropna()

def val_factors(i, idx):
    t8 = ds[i]
    sh = latest_before(sh_a, t8, None).combine_first(latest_before(sh_b, t8, None))
    mcap = (sym_from_cik(sh, idx) * RC.loc[idx, t8])
    mcap = mcap.where(mcap > 0)
    F = pd.DataFrame(index=idx)
    F["ep"]   = sym_from_cik(latest_before(ni_fy, t8), idx) / mcap
    F["bm"]   = sym_from_cik(latest_before(se_q, t8), idx) / mcap
    F["cfoy"] = sym_from_cik(latest_before(cfo_fy, t8), idx) / mcap
    return F

def val3_score(F):
    """3팩터 전부 있는 심볼만, 백분위 순위합"""
    ok = F.notna().all(axis=1)
    sc = sum(F.loc[ok, f].rank(pct=True) for f in ("ep","bm","cfoy"))
    return sc

anchors = [i for i in range(252, len(ds) - 20, 5)]
print("anchors:", len(anchors), ds[anchors[0]], "→", ds[anchors[-1]], flush=True)

def block_boot_ci(x, alpha_list=(0.05,), block=4):
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

# ── 수집 루프
res = {k: [] for k in ("date","ew","mus50","val50","vm_inter","vm_sum6","vq_srt")}
ic_by_anchor = []  # (date, ic_bm)
for k, i in enumerate(anchors):
    idx, amt20 = guard_universe(i)
    fwd = (C.loc[idx, ds[i+20]] / C.loc[idx, ds[i]] - 1)
    F = val_factors(i, idx)
    vs = val3_score(F)
    ms = mus_score(i, idx, amt20)
    res["date"].append(ds[i])
    res["ew"].append(fwd.mean()*100)
    res["mus50"].append(fwd[ms.sort_values(ascending=False).index[:50]].mean()*100)
    # 독립 밸류 top50
    res["val50"].append(fwd[vs.sort_values(ascending=False).index[:50]].mean()*100 if len(vs)>=50 else np.nan)
    # 결합1: 밸류 상위 40% 교집합에서 mus 순위 top50
    both = vs.index.intersection(ms.index)
    vcut = vs.loc[both] >= vs.loc[both].quantile(0.6)
    pool = both[vcut]
    sel = ms.loc[pool].sort_values(ascending=False).index[:50]
    res["vm_inter"].append(fwd[sel].mean()*100 if len(sel)>=30 else np.nan)
    # 결합2: 6항 순위합 (mus3 + val3, 밸류 결측은 0.5)
    sc6 = ms + sum(F[f].rank(pct=True).reindex(ms.index).fillna(0.5) for f in ("ep","bm","cfoy"))
    res["vm_sum6"].append(fwd[sc6.sort_values(ascending=False).index[:50]].mean()*100)
    # 결합3: 밸류점수 × 꾸준함(upratio63)만 — 모멘텀 제외 이질 모델
    w63 = C[ds[i-62:i+1]].loc[idx].pct_change(axis=1)
    up = ((w63 > 0).sum(axis=1) / w63.notna().sum(axis=1)).reindex(vs.index)
    sc_vq = vs + up.rank(pct=True).fillna(0.5)
    res["vq_srt"].append(fwd[sc_vq.sort_values(ascending=False).index[:50]].mean()*100 if len(sc_vq)>=50 else np.nan)
    m = F["bm"].notna() & fwd.notna()
    ic_by_anchor.append((ds[i], spearmanr(F.loc[m,"bm"], fwd[m]).statistic if m.sum()>100 else np.nan))
    if k % 20 == 0: print(f"  anchor {k}/{len(anchors)}", flush=True)

R = pd.DataFrame(res)
R.to_csv("us_sec_scan_frame3.csv", index=False)

print("\n== 프레임3·5: 독립/결합 모델 top50 초과수익 vs EW (%p/20d, 블록부트스트랩)")
print("   (결합 3종 다중검정: Bonferroni α=0.0167 → 98.33% CI 병기)")
for name in ("mus50","val50","vm_inter","vm_sum6","vq_srt"):
    d = (R[name] - R["ew"]).values
    cis, m = block_boot_ci(d, alpha_list=(0.05, 0.05/3))
    lo, hi = cis[0.05]; lob, hib = cis[0.05/3]
    n = int(np.isfinite(d).sum())
    hit = np.nanmean(d > 0)
    print(f"  {name:9s} n={n:3d} 초과 {np.nanmean(d):+.2f}%p 95%[{lo:+.2f},{hi:+.2f}] "
          f"Bonf3[{lob:+.2f},{hib:+.2f}] 적중 {hit:.0%}")

print("\n== 프레임4a: bm day-IC 연도별")
icd = pd.DataFrame(ic_by_anchor, columns=["date","ic"])
icd["yr"] = icd.date.str.slice(0,4)
for yr, g in icd.groupby("yr"):
    cis, m = block_boot_ci(g.ic.values)
    print(f"  {yr}: n={g.ic.notna().sum():3d} IC={m:+.4f} 95%[{cis[0.05][0]:+.4f},{cis[0.05][1]:+.4f}]")

print("\n== 프레임4b: val50 초과 연도별 + mus 초과와의 상관")
R["yr"] = R.date.str.slice(0,4)
for yr, g in R.groupby("yr"):
    d = (g.val50 - g.ew).values
    cis, m = block_boot_ci(d)
    print(f"  {yr}: n={int(np.isfinite(d).sum()):3d} val50 초과 {np.nanmean(d):+.2f}%p 95%[{cis[0.05][0]:+.2f},{cis[0.05][1]:+.2f}]")
dm = (R.mus50 - R.ew); dv = (R.val50 - R.ew)
msk = np.isfinite(dm) & np.isfinite(dv)
print(f"  corr(mus 초과, val 초과) = {np.corrcoef(dm[msk], dv[msk])[0,1]:+.2f} (n={int(msk.sum())})")
print("\ndone.")
