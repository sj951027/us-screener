# -*- coding: utf-8 -*-
"""
us_nsi_scan_20260906.py — 순발행(주식수 변화) 스캔 (in-sample, 관측 전용)
=========================================================================
질문: 보고 주식수의 12개월 변화(순발행 nsi12)가 h20 전방수익을 예측하는가.
  문헌(Pontiff-Woodgate 2008 등): 발행↑ → 이후 부진, 자사주 매입(발행↓) → 우위. 사전 부호 −.
구성: xbrl EntityCommonStockSharesOutstanding(폴백 CommonStockSharesOutstanding), filed<앵커 PIT.
  nsi12 = shares(최신, filed<t) / shares(최신, filed<t−365d) − 1.
  분할 보정: 두 보고일 사이 분할계수 = (close비/adj_close비) 로 나눔(분할은 발행이 아님).
  잔여 이상치(|비율−1|>0.9)는 제외(역분할·데이터 오류).
정직성: in-sample · 생존편향 미보정 · 주간앵커 h20 중첩 → 블록 부트스트랩(블록=4).
  이번 신규 검정 3개(IC·십분위·Δtop50) → Bonferroni α=0.05/3. 결과는 기움이지 채택 아님.
"""
import sqlite3, time
import numpy as np, pandas as pd
from scipy.stats import spearmanr

DB  = "us-screener-data/us_ohlcv.db"; FDB = "us-screener-data/us_fundamentals.db"
RNG = np.random.default_rng(20260906); NBOOT = 10000; ALPHAS = (0.05, 0.05/3)
T0 = time.time()
def log(*a): print(f"[{time.time()-T0:5.0f}s]", *a, flush=True)

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
df = pd.read_sql("select symbol,date,close,adj_close,volume from daily_ohlcv", con)
C = df.pivot(index="symbol", columns="date", values="adj_close")
RC = df.pivot(index="symbol", columns="date", values="close")
V = df.pivot(index="symbol", columns="date", values="volume"); del df
ds = sorted(C.columns); C, RC, V = C[ds], RC[ds], V[ds]
fc = sqlite3.connect(f"file:{FDB}?mode=ro", uri=True)
ct = pd.read_sql("select cik, ticker from cik_ticker", fc); ct["ticker"] = ct.ticker.str.upper()
ct = ct[ct.ticker.isin(C.index)]; cik2syms = ct.groupby("cik")["ticker"].apply(list).to_dict()
sh = pd.read_sql("""select cik, tag, val, filed from xbrl_facts
    where tag in ('EntityCommonStockSharesOutstanding','CommonStockSharesOutstanding') and val>0""", fc)
sh["filed8"] = sh.filed.str.replace("-", "", regex=False)
sh["pri"] = (sh.tag != "EntityCommonStockSharesOutstanding").astype(int)
sh = sh.sort_values(["filed8", "pri"])
rows = [(s, f8, v) for cik, f8, v in sh[["cik", "filed8", "val"]].itertuples(index=False) for s in cik2syms.get(cik, ())]
SH = pd.DataFrame(rows, columns=["symbol", "filed8", "val"]).sort_values("filed8")
log("shares rows", len(SH))
dsi = {d: k for k, d in enumerate(ds)}
def nearest_idx(d8):  # d8 이하 가장 가까운 거래일 index
    import bisect; k = bisect.bisect_right(ds, d8) - 1; return max(k, 0)

def latest(t8, lookback=400):
    lo = (pd.Timestamp(t8) - pd.Timedelta(days=lookback)).strftime("%Y%m%d")
    sub = SH[(SH.filed8 < t8) & (SH.filed8 >= lo)]
    sub = sub.drop_duplicates("symbol", keep="last").set_index("symbol")
    return sub["val"], sub["filed8"]

def guard(i):
    t = ds[i]; c63 = C[ds[i-62:i+1]]
    amt20 = (RC[ds[i-19:i+1]] * V[ds[i-19:i+1]]).mean(axis=1)
    ok = (RC[t] >= 5) & (amt20 >= 1e6) & (c63.notna().sum(axis=1) >= 60) & (c63.std(axis=1, ddof=1) > 0)
    return ok[ok].index, amt20
def mus(i, idx, amt20):
    w63 = C[ds[i-62:i+1]].loc[idx].pct_change(axis=1)
    F = pd.DataFrame(index=idx)
    F["mom12"] = C.loc[idx, ds[i-21]] / C.loc[idx, ds[i-252]] - 1
    F["upratio63"] = (w63 > 0).sum(axis=1) / w63.notna().sum(axis=1)
    F["size_amt"] = np.log10(amt20.reindex(idx).where(amt20.reindex(idx) > 0))
    sc = None
    for j, f in enumerate(["mom12", "upratio63", "size_amt"]):
        r = F[f].rank(pct=True); core = r.notna() if j == 0 else core; sc = r if sc is None else sc + r.fillna(0.5)
    return sc.where(core).dropna()
def bb(x, alphas=ALPHAS, block=4):
    x = np.asarray(x, float); x = x[np.isfinite(x)]; n = len(x)
    if n < 8: return np.nan, {a: (np.nan, np.nan) for a in alphas}, n
    nblk = int(np.ceil(n / block)); means = np.empty(NBOOT)
    for b in range(NBOOT):
        st = RNG.integers(0, n, nblk); sel = (st[:, None] + np.arange(block)[None, :]).ravel() % n
        means[b] = x[sel[:n]].mean()
    return x.mean(), {a: (np.quantile(means, a/2), np.quantile(means, 1-a/2)) for a in alphas}, n
def fmt(m, cis, n, d=3, u=""):
    lo, hi = cis[0.05]; lb, hb = cis[ALPHAS[1]]
    return (f"{m:+.{d}f}{u} 95%[{lo:+.{d}f},{hi:+.{d}f}]{'*' if (lo>0 or hi<0) else ' '} "
            f"Bonf3[{lb:+.{d}f},{hb:+.{d}f}]{'**' if (lb>0 or hb<0) else '  '} n={n}")

anchors = [i for i in range(252, len(ds) - 20, 5)]
cache = []
for k_, i in enumerate(anchors):
    t8 = ds[i]; idx, amt20 = guard(i)
    fwd = C.loc[idx, ds[i+20]] / C.loc[idx, ds[i]] - 1
    s_now, f_now = latest(t8)
    t_prev = (pd.Timestamp(t8) - pd.Timedelta(days=365)).strftime("%Y%m%d")
    s_prev, f_prev = latest(t_prev)
    F = pd.DataFrame(index=idx)
    sn = s_now.reindex(idx); sp = s_prev.reindex(idx); fn = f_now.reindex(idx); fp = f_prev.reindex(idx)
    ok = sn.notna() & sp.notna() & (fn != fp)
    # 분할계수: 두 보고일(거래일 근사) 사이 (close비 / adj비)
    split = pd.Series(np.nan, index=idx)
    for s in idx[ok]:
        a, b = nearest_idx(fp[s]), nearest_idx(fn[s])
        try:
            rr = RC.at[s, ds[b]] / RC.at[s, ds[a]]; ar = C.at[s, ds[b]] / C.at[s, ds[a]]
            f_ = (ar / rr) if (rr > 0 and ar > 0) else np.nan
            split[s] = f_ if (np.isfinite(f_) and abs(f_ - 1) > 0.2) else 1.0   # 배당조정(수%)은 무시, 분할만
        except Exception: pass
    ratio = (sn / sp) / split
    ratio = ratio.where((ratio > 0.1) & (np.abs(ratio - 1) <= 0.9))
    F["nsi12"] = ratio - 1
    F["mcap"] = sn * RC.loc[idx, t8]
    ms = mus(i, idx, amt20)
    cache.append((t8, idx, fwd, F, ms, amt20.reindex(idx)))
    if k_ % 20 == 0: log(f"anchor {k_}/{len(anchors)} cover={F.nsi12.notna().mean():.0%} "
                         f"median nsi={F.nsi12.median():+.3f} 발행>10%: {(F.nsi12>0.1).mean():.1%} 감소: {(F.nsi12<0).mean():.1%}")

print("\n== 프레임1: nsi12 day-IC h20 (사전 부호 −)")
ics = []
for t8, idx, fwd, F, ms, amt in cache:
    m = F.nsi12.notna() & fwd.notna(); ics.append(spearmanr(F.nsi12[m], fwd[m]).statistic if m.sum() > 100 else np.nan)
m, cis, n = bb(ics); print("  nsi12 IC =", fmt(m, cis, n, d=4))
print("\n== 프레임1b: 십분위 (Q1 최소발행/자사주 − Q10 최대발행, %p/20d)")
sp_ = []
for t8, idx, fwd, F, ms, amt in cache:
    m = F.nsi12.notna() & fwd.notna()
    if m.sum() < 200: sp_.append(np.nan); continue
    q = pd.qcut(F.nsi12[m].rank(method="first"), 10, labels=False)
    sp_.append((fwd[m][q == 0].mean() - fwd[m][q == 9].mean()) * 100)
m, cis, n = bb(sp_); print("  Q1−Q10 =", fmt(m, cis, n, d=2, u="%p"))
print("\n== 프레임1c: 조건부 그룹 초과 (그룹 EW − 유니버스 EW, %p/20d)")
def cond(label, fn):
    xs, ns = [], []
    for t8, idx, fwd, F, ms, amt in cache:
        g = fn(F, ms, amt); g = g[g].index if hasattr(g, "index") else g
        if len(g) < 10: xs.append(np.nan); ns.append(0); continue
        xs.append((fwd[g].mean() - fwd.mean()) * 100); ns.append(len(g))
    m, cis, n = bb(xs, alphas=(0.05,)); lo, hi = cis[0.05]
    print(f"  {label:40s} {m:+.2f}%p 95%[{lo:+.2f},{hi:+.2f}]{'*' if (lo>0 or hi<0) else ' '} n={n} 종목={np.nanmean([v for v in ns if v>0]):.0f}")
cond("주식수 감소(자사주 순매입, nsi<0)", lambda F, ms, amt: F.nsi12 < 0)
cond("주식수 감소 ≥5%", lambda F, ms, amt: F.nsi12 <= -0.05)
cond("변화 ±1% 이내", lambda F, ms, amt: F.nsi12.abs() <= 0.01)
cond("발행 >10%", lambda F, ms, amt: F.nsi12 > 0.10)
cond("발행 상위 10%(십분위 Q10)", lambda F, ms, amt: F.nsi12 >= F.nsi12.quantile(0.9))
cond("감소 & 유동성 상위半", lambda F, ms, amt: (F.nsi12 < 0) & (amt >= amt.median()))
cond("감소 & 유동성 하위半", lambda F, ms, amt: (F.nsi12 < 0) & (amt < amt.median()))
print("\n== 프레임2: us_mus_v0 top50 변형 짝비교 (Δ = 변형 − base)")
def pair(label, fn):
    d, ov = [], []
    for t8, idx, fwd, F, ms, amt in cache:
        old = ms.sort_values(ascending=False).index[:50]; new = fn(F, ms)
        if len(new) < 30: d.append(np.nan); ov.append(np.nan); continue
        d.append((fwd[new].mean() - fwd[old].mean()) * 100); ov.append(len(set(new) & set(old)) / 50)
    m, cis, n = bb(d); print(f"  {label:40s} Δ=", fmt(m, cis, n, d=3, u="%p"), f"overlap={np.nanmean(ov):.0%}")
pair("+(−nsi12) 4번째 항(순위)", lambda F, ms: (ms + (-F.nsi12).rank(pct=True).reindex(ms.index).fillna(0.5)).sort_values(ascending=False).index[:50])
pair("발행 상위 십분위(Q10) 제외", lambda F, ms: ms[~(F.nsi12.reindex(ms.index) >= F.nsi12.quantile(0.9)).fillna(False)].sort_values(ascending=False).index[:50])
pair("발행 >10% 제외", lambda F, ms: ms[~(F.nsi12.reindex(ms.index) > 0.10).fillna(False)].sort_values(ascending=False).index[:50])
pair("주식수 감소 종목만(nsi<0)", lambda F, ms: ms[(F.nsi12.reindex(ms.index) < 0).fillna(False)].sort_values(ascending=False).index[:50])
print("\n== 참고: base top50 의 nsi12 분포 (발행 >10% 비율)")
print("  ", np.mean([(F.nsi12.reindex(ms.sort_values(ascending=False).index[:50]) > 0.1).mean() for t8, idx, fwd, F, ms, amt in cache]).round(3),
      "| 유니버스", np.mean([(F.nsi12 > 0.1).mean() for t8, idx, fwd, F, ms, amt in cache]).round(3))
print("\n== 연도별 십분위 스프레드")
yrs = np.array([c[0][:4] for c in cache]); sp_ = np.array(sp_)
for y in ("2024", "2025", "2026"): print(f"  {y}: {np.nanmean(sp_[yrs==y]):+.2f}%p n={np.isfinite(sp_[yrs==y]).sum()}")
pd.DataFrame({"anchor": [c[0] for c in cache], "ic": ics, "q1_q10": sp_}).to_csv("us_nsi_scan_frame.csv", index=False)
log("done")
