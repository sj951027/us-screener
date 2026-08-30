# -*- coding: utf-8 -*-
"""
us_quality_scan_20260830.py — 질(quality)·발생액·재무모멘텀 스캔 (in-sample)
=============================================================================
질문: 재무 데이터의 '이익의 질' 각도(발생액·현금수익성·수익성 개선 추세)와
      섹터중립 밸류에 새 알파가 있는가. 8/30 SEC 1차·SUE 스캔의 3탄.
팩터 6종(전부 10-K 연간, filed<앵커일 PIT · 사전 부호 등록):
  accr(−)  발생액 = (NI − CFO)/Assets   — Sloan 발생액 anomaly
  cfo_at(+) 현금수익성 = CFO/Assets
  roa(+)   수익성 = NI/Assets
  droa(+)  재무모멘텀 = ROA(fy) − ROA(fy−1)
  dopm(+)  영업마진 개선 = OpInc/Rev(fy) − OpInc/Rev(fy−1)
  snbm(+)  섹터중립 B/M = bm 을 섹터(sector_cache) 안에서 백분위 순위화
정직성: in-sample · 생존편향 미보정 · 주간앵커 108 · h20 중첩 → 블록 부트스트랩(블록=4)
· 6팩터 Bonferroni α=0.05/6 병기 · 결과는 '기움'이지 채택 아님.
"""
import sqlite3
import numpy as np, pandas as pd
from scipy.stats import spearmanr

DB  = "us-screener-data/us_ohlcv.db"
FDB = "us-screener-data/us_fundamentals.db"
RNG = np.random.default_rng(20260830)
NBOOT = 10000
ALPHAS = (0.05, 0.05/6)

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
print("loading ohlcv ...", flush=True)
df = pd.read_sql("select symbol,date,close,adj_close,volume from daily_ohlcv", con)
C  = df.pivot(index="symbol", columns="date", values="adj_close")
RC = df.pivot(index="symbol", columns="date", values="close")
V  = df.pivot(index="symbol", columns="date", values="volume")
del df
ds = sorted(C.columns)
sec = pd.read_sql("select symbol, sector from sector_cache", con).set_index("symbol")["sector"]

fc = sqlite3.connect(f"file:{FDB}?mode=ro", uri=True)
ct = pd.read_sql("select cik, ticker from cik_ticker", fc)
ct["ticker"] = ct.ticker.str.upper()
ct = ct[ct.ticker.isin(C.index)]
cik2syms = ct.groupby("cik")["ticker"].apply(list).to_dict()

# ── 연간(10-K FY) 태그 테이블: (cik, fy)당 최초 filed 값
xb = pd.read_sql("""select cik, tag, val, fy, filed from xbrl_facts
                    where fp='FY' and form like '10-K%'
                      and tag in ('NetIncomeLoss','NetCashProvidedByUsedInOperatingActivities',
                                  'Assets','OperatingIncomeLoss','Revenues',
                                  'RevenueFromContractWithCustomerExcludingAssessedTax')""", fc)
xb["filed8"] = xb.filed.str.replace("-", "", regex=False)
xb = xb.sort_values("filed8").drop_duplicates(["cik", "fy", "tag"], keep="first")
A = xb.pivot_table(index=["cik", "fy"], columns="tag", values="val", aggfunc="first")
A.columns = [{"NetIncomeLoss": "ni", "NetCashProvidedByUsedInOperatingActivities": "cfo",
              "Assets": "at", "OperatingIncomeLoss": "oi", "Revenues": "rev1",
              "RevenueFromContractWithCustomerExcludingAssessedTax": "rev2"}[c] for c in A.columns]
A["rev"] = A.get("rev2").combine_first(A.get("rev1"))
F8 = xb[xb.tag.isin(["NetIncomeLoss", "NetCashProvidedByUsedInOperatingActivities", "Assets"])] \
      .groupby(["cik", "fy"])["filed8"].max()   # 보수적 PIT: 핵심 3태그 중 가장 늦은 filed
A = A.join(F8.rename("filed8")).reset_index()
A = A[A.at_.notna() if "at_" in A.columns else A["at"].notna()]
A = A[A["at"] > 0]
# 전년 값 조인
prev = A[["cik", "fy", "ni", "at", "oi", "rev"]].copy()
prev["fy"] = prev.fy + 1
A = A.merge(prev, on=["cik", "fy"], how="left", suffixes=("", "_p"))
A["accr"]   = (A.ni - A.cfo) / A["at"]
A["cfo_at"] = A.cfo / A["at"]
A["roa"]    = A.ni / A["at"]
A["droa"]   = A.roa - (A.ni_p / A.at_p)
A["dopm"]   = (A.oi / A.rev.replace(0, np.nan)) - (A.oi_p / A.rev_p.replace(0, np.nan))
A = A[A.filed8.notna()].sort_values("filed8")
print("연간 프레임:", len(A), "| filed", A.filed8.min(), "~", A.filed8.max(), flush=True)

# bm 재료(섹터중립용): 분기 StockholdersEquity + 보고 주식수
xq = pd.read_sql("""select cik, tag, end, val, filed from xbrl_facts
                    where tag in ('StockholdersEquity','EntityCommonStockSharesOutstanding',
                                  'CommonStockSharesOutstanding')""", fc)
xq["filed8"] = xq.filed.str.replace("-", "", regex=False)
xq["end8"] = xq["end"].str.replace("-", "", regex=False)
xq = xq.sort_values("filed8")
se_q = xq[xq.tag == "StockholdersEquity"]
sh_a = xq[xq.tag == "EntityCommonStockSharesOutstanding"]
sh_b = xq[xq.tag == "CommonStockSharesOutstanding"]

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
    for j, f in enumerate(["mom12", "upratio63", "size_amt"]):
        rk = F[f].rank(pct=True)
        if j == 0: core = rk.notna()
        sc = rk if sc is None else sc + rk.fillna(0.5)
    return sc.where(core).dropna()

FACTORS = ["accr", "cfo_at", "roa", "droa", "dopm", "snbm"]
SIGN = {"accr": -1, "cfo_at": 1, "roa": 1, "droa": 1, "dopm": 1, "snbm": 1}

def calc_factors(i, idx):
    t8 = ds[i]
    # 연간 프레임: filed<t 인 것 중 cik별 최신(fy 최대 = filed 최대와 동일 가정, 정렬 filed8)
    cut = (pd.Timestamp(t8) - pd.Timedelta(days=455)).strftime("%Y%m%d")
    sub = A[(A.filed8 < t8) & (A.filed8 >= cut)]
    sub = sub.drop_duplicates("cik", keep="last").set_index("cik")
    out = pd.DataFrame(index=idx)
    for f in ("accr", "cfo_at", "roa", "droa", "dopm"):
        out[f] = sym_from_cik(sub[f].dropna(), idx)
    # 섹터중립 bm
    sh = latest_before(sh_a, t8, None).combine_first(latest_before(sh_b, t8, None))
    mcap = (sym_from_cik(sh, idx) * RC.loc[idx, t8]).where(lambda x: x > 0)
    bm = sym_from_cik(latest_before(se_q, t8), idx) / mcap
    g = pd.DataFrame({"bm": bm, "sec": sec.reindex(idx)})
    out["snbm"] = g.groupby("sec")["bm"].rank(pct=True)
    return out

def block_boot_ci(x, alpha_list=ALPHAS, block=4):
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

anchors = [i for i in range(252, len(ds) - 20, 5)]
print("anchors:", len(anchors), ds[anchors[0]], "→", ds[anchors[-1]], flush=True)

ics = {f: [] for f in FACTORS}
cov = {f: [] for f in FACTORS}
cache = []
for k, i in enumerate(anchors):
    idx, amt20 = guard_universe(i)
    fwd = (C.loc[idx, ds[i+20]] / C.loc[idx, ds[i]] - 1)
    fac = calc_factors(i, idx)
    ms = mus_score(i, idx, amt20)
    cache.append((fac, ms, fwd))
    for f in FACTORS:
        m = fac[f].notna() & fwd.notna() & np.isfinite(fac[f])
        cov[f].append(m.mean())
        ics[f].append(spearmanr(fac.loc[m, f], fwd[m]).statistic if m.sum() > 100 else np.nan)
    if k % 20 == 0: print(f"  anchor {k}/{len(anchors)}", flush=True)

print(f"\n== 프레임1: day-IC h20 (주간앵커 · Bonferroni α={0.05/6:.5f}) · 사전부호 병기")
rows1 = []
for f in FACTORS:
    cis, m = block_boot_ci(ics[f])
    lo95, hi95 = cis[0.05]; lob, hib = cis[0.05/6]
    sig95 = "*" if (np.isfinite(lo95) and (lo95 > 0 or hi95 < 0)) else " "
    sigB  = "**" if (np.isfinite(lob) and (lob > 0 or hib < 0)) else "  "
    agree = "부호일치" if (np.isfinite(m) and np.sign(m) == SIGN[f]) else "부호불일치"
    n_eff = int(sum(np.isfinite(v) for v in ics[f]))
    rows1.append((f, SIGN[f], n_eff, m, lo95, hi95, lob, hib, float(np.mean(cov[f]))))
    print(f"  {f:7s}({'+' if SIGN[f]>0 else '-'}) n={n_eff:3d} IC={m:+.4f} "
          f"95%[{lo95:+.4f},{hi95:+.4f}]{sig95} Bonf[{lob:+.4f},{hib:+.4f}]{sigB} "
          f"{agree} cover={np.mean(cov[f]):.0%}")
pd.DataFrame(rows1, columns=["factor","sign","n","meanIC","ci95_lo","ci95_hi",
                             "bonf_lo","bonf_hi","coverage"]) \
  .to_csv("us_quality_scan_frame1.csv", index=False)

print("\n== 프레임2: us_mus_v0 top50 + 4번째 항 (95% 유의 & 부호일치 팩터만)")
rows2 = []
for f, sgn, n_eff, mic, lo, hi, lob, hib, c in rows1:
    if not (np.isfinite(lo) and (lo > 0 or hi < 0) and np.sign(mic) == sgn): continue
    diffs, othr = [], []
    for (fac, ms, fwd) in cache:
        if not fac[f].notna().any(): continue
        rk4 = fac[f].rank(pct=True)
        if sgn < 0: rk4 = 1 - rk4
        sc4 = ms + rk4.reindex(ms.index).fillna(0.5)
        t_new = sc4.sort_values(ascending=False).index[:50]
        t_old = ms.sort_values(ascending=False).index[:50]
        diffs.append((fwd[t_new].mean() - fwd[t_old].mean()) * 100)
        othr.append(len(set(t_new) & set(t_old)) / 50)
    cis, m = block_boot_ci(diffs, alpha_list=(0.05,))
    lo2, hi2 = cis[0.05]
    rows2.append((f, m, lo2, hi2, float(np.mean(othr)), len(diffs)))
    print(f"  +{f:7s} Δtop50 h20 = {m:+.3f}%p 95%CI[{lo2:+.3f},{hi2:+.3f}] "
          f"overlap={np.mean(othr):.0%} n={len(diffs)}")
if rows2:
    pd.DataFrame(rows2, columns=["factor","d_pp","ci_lo","ci_hi","overlap","n"]) \
      .to_csv("us_quality_scan_frame2.csv", index=False)
print("\ndone.")
