# -*- coding: utf-8 -*-
"""
us_orth_scan_followup.py — 내부자 클러스터 매수 조건부 신호의 민감도/정직성 후속 (in-sample)
=========================================================================================
1차(us_orth_scan_20260906.py)에서 "임원/이사 ≥2명 매수 & 순매수비>0.5 (90d)" 그룹의
EW 대비 초과가 Bonf6 통과. 이 파일은 그 결과가 **매직넘버·소형주·프록시·중첩**의
산물인지 확인한다. 여기의 검정 수는 많으므로(민감도) 개별 CI 는 선택 근거가 아니라
'형태 확인'용이다. 결과는 기움이지 채택 아님.
"""
import sqlite3, time
import numpy as np, pandas as pd

DB  = "us-screener-data/us_ohlcv.db"
FDB = "us-screener-data/us_fundamentals.db"
RNG = np.random.default_rng(20260906)
NBOOT = 5000
T0 = time.time()
def log(*a): print(f"[{time.time()-T0:5.0f}s]", *a, flush=True)

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
df = pd.read_sql("select symbol,date,close,adj_close,volume from daily_ohlcv", con)
C  = df.pivot(index="symbol", columns="date", values="adj_close")
RC = df.pivot(index="symbol", columns="date", values="close")
V  = df.pivot(index="symbol", columns="date", values="volume")
del df
ds = sorted(C.columns)
fc = sqlite3.connect(f"file:{FDB}?mode=ro", uri=True)

ins = pd.read_sql("""select quarter, symbol, issuer_cik, owner_cik, is_officer, is_director,
                     is_tenpct, trans_date, code, shares, price, filed from insider_tx
                     where code in ('P','S')""", fc)
ins["symbol"] = ins.symbol.str.upper()
ins = ins[ins.symbol.isin(C.index)]
ins["filed8"] = ins.filed.str.replace("-", "", regex=False)
flagged_q = set(ins[(ins.is_officer + ins.is_director + ins.is_tenpct) > 0].quarter.unique())
fl = ins[ins.quarter.isin(flagged_q)]
od_pairs = set(zip(fl[(fl.is_officer == 1) | (fl.is_director == 1)].issuer_cik,
                   fl[(fl.is_officer == 1) | (fl.is_director == 1)].owner_cik))
ins["pair"] = list(zip(ins.issuer_cik, ins.owner_cik))
ins["od"] = np.where(ins.quarter.isin(flagged_q),
                     ((ins.is_officer == 1) | (ins.is_director == 1)).astype(int),
                     ins.pair.isin(od_pairs).astype(int))
ins["usd"] = (ins.shares.abs() * ins.price.abs()).fillna(0)
ins = ins[ins.usd > 0].sort_values("filed8")
# 루틴 매수자(Cohen-Malloy-Pomorski 근사): 같은 (issuer,owner) 가 직전 3년 중 2년 이상
# 같은 달에 매수(P) 기록 → 루틴. 앵커 시점보다 과거 기록만 쓰므로 PIT.
P = ins[ins.code == "P"].copy(); S = ins[ins.code == "S"].copy()
P["ym"] = P.filed8.str[:6]; P["yr"] = P.filed8.str[:4].astype(int); P["mo"] = P.filed8.str[4:6]
buy_months = set(zip(P.pair, P.yr, P.mo))
def is_routine(pair, yr, mo):
    return sum(((pair, yr - k, mo) in buy_months) for k in (1, 2, 3)) >= 2
P["routine"] = [is_routine(p, y, m) for p, y, m in zip(P.pair, P.yr, P.mo)]
log("P rows", len(P), "routine share", round(P.routine.mean(), 3))

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
    return sc.where(core).dropna(), F

def bb(x, alpha=0.05, block=4):
    x = np.asarray([v for v in x if np.isfinite(v)]); n = len(x)
    if n < 8: return np.nan, np.nan, np.nan, n
    nblk = int(np.ceil(n / block)); means = np.empty(NBOOT)
    for b in range(NBOOT):
        st = RNG.integers(0, n, nblk)
        sel = (st[:, None] + np.arange(block)[None, :]).ravel() % n
        means[b] = x[sel[:n]].mean()
    return x.mean(), np.quantile(means, alpha/2), np.quantile(means, 1-alpha/2), n

def feats(t8, idx, win, exclude_routine=False):
    lo = (pd.Timestamp(t8) - pd.Timedelta(days=win)).strftime("%Y%m%d")
    p = P[(P.filed8 < t8) & (P.filed8 >= lo) & (P.od == 1)]
    if exclude_routine: p = p[~p.routine]
    s = S[(S.filed8 < t8) & (S.filed8 >= lo) & (S.od == 1)]
    nb = p.groupby("symbol").owner_cik.nunique().reindex(idx).fillna(0)
    pu = p.groupby("symbol").usd.sum().reindex(idx).fillna(0)
    su = s.groupby("symbol").usd.sum().reindex(idx).fillna(0)
    npr = ((pu - su) / (pu + su)).where((pu + su) > 0)
    return nb, npr, pu

anchors = [i for i in range(252, len(ds) - 40, 5)]
log("anchors", len(anchors), ds[anchors[0]], ds[anchors[-1]])
cache = []
for i in anchors:
    t8 = ds[i]; idx, amt20 = guard_universe(i)
    fwd = {h: (C.loc[idx, ds[i+h]] / C.loc[idx, ds[i]] - 1) for h in (5, 10, 20, 40)}
    ms, F = mus_score(i, idx, amt20)
    f = {}
    for win in (30, 60, 90, 180):
        f[win] = feats(t8, idx, win)
    f["90nr"] = feats(t8, idx, 90, exclude_routine=True)
    cache.append((t8, idx, fwd, ms, F, amt20.reindex(idx), f))
log("frames built")

def cond(label, pick, h=20, show_n=True):
    xs, ns = [], []
    for t8, idx, fwd, ms, F, amt, f in cache:
        g = pick(idx, fwd, ms, F, amt, f)
        if g is None or len(g) < 10: xs.append(np.nan); ns.append(0); continue
        xs.append((fwd[h][g].mean() - fwd[h].mean()) * 100); ns.append(len(g))
    m, lo, hi, n = bb(xs)
    star = "*" if (lo > 0 or hi < 0) else " "
    print(f"  {label:52s} {m:+.2f}%p 95%[{lo:+.2f},{hi:+.2f}]{star} n={n} "
          f"적중={np.nanmean(np.array(xs)>0):.0%} 종목={np.nanmean([v for v in ns if v>0]):.0f}")
    return xs

def cl(win, nmin=2, thr=0.0):
    def pick(idx, fwd, ms, F, amt, f):
        nb, npr, pu = f[win]
        return idx[(nb >= nmin) & (npr > thr)]
    return pick

print("\n== S1. 창·문턱 민감도 (h20, 그룹 − EW)")
for win in (30, 60, 90, 180):
    for thr in (0.0, 0.5):
        cond(f"win={win}d ≥2명 npr>{thr}", cl(win, 2, thr))
cond("win=90d ≥1명 npr>0", cl(90, 1, 0.0))
cond("win=90d ≥2명 npr>0 · 루틴 매수 제외", lambda idx, fwd, ms, F, amt, f: idx[(f['90nr'][0] >= 2) & (f['90nr'][1] > 0)])

print("\n== S2. 보유기간별 (90d ≥2명 npr>0)")
for h in (5, 10, 20, 40):
    xs = cond(f"h={h}", cl(90, 2, 0.0), h=h)

print("\n== S3. 유동성 분할 (90d ≥2명 npr>0)")
def liq(half):
    def pick(idx, fwd, ms, F, amt, f):
        nb, npr, pu = f[90]
        med = amt.median()
        m = (amt >= med) if half == "hi" else (amt < med)
        return idx[(nb >= 2) & (npr > 0) & m]
    return pick
cond("유동성 상위半", liq("hi")); cond("유동성 하위半", liq("lo"))
def liq_ew(half):
    # 같은 半의 EW 를 벤치마크로 (소형주 프리미엄과 분리)
    xs = []
    for t8, idx, fwd, ms, F, amt, f in cache:
        nb, npr, pu = f[90]; med = amt.median()
        m = (amt >= med) if half == "hi" else (amt < med)
        g = idx[(nb >= 2) & (npr > 0) & m]
        xs.append((fwd[20][g].mean() - fwd[20][idx[m]].mean()) * 100 if len(g) >= 10 else np.nan)
    mm, lo, hi, n = bb(xs)
    print(f"  {half} 半 − 같은 半 EW: {mm:+.2f}%p 95%[{lo:+.2f},{hi:+.2f}] n={n}")
liq_ew("hi"); liq_ew("lo")

print("\n== S4. mus 와의 교차 (90d ≥2명 npr>0)")
cond("클러스터 ∩ mus 상위半", lambda idx, fwd, ms, F, amt, f:
     idx[(f[90][0] >= 2) & (f[90][1] > 0) & (ms.reindex(idx) >= ms.median())])
cond("클러스터 ∩ mus 하위半", lambda idx, fwd, ms, F, amt, f:
     idx[(f[90][0] >= 2) & (f[90][1] > 0) & (ms.reindex(idx) < ms.median())])
cond("클러스터 ∩ mom12>0", lambda idx, fwd, ms, F, amt, f:
     idx[(f[90][0] >= 2) & (f[90][1] > 0) & (F.mom12 > 0)])
cond("클러스터 ∩ mom12≤0 (하락 후 매수)", lambda idx, fwd, ms, F, amt, f:
     idx[(f[90][0] >= 2) & (f[90][1] > 0) & (F.mom12 <= 0)])
def top_by_mus(k):
    def pick(idx, fwd, ms, F, amt, f):
        g = idx[(f[90][0] >= 2) & (f[90][1] > 0)]
        return ms.reindex(g).dropna().sort_values(ascending=False).index[:k]
    return pick
cond("클러스터 중 mus 상위 20", top_by_mus(20)); cond("클러스터 중 mus 상위 30", top_by_mus(30))
def top_by_nb(k):
    def pick(idx, fwd, ms, F, amt, f):
        nb, npr, pu = f[90]
        g = idx[(nb >= 2) & (npr > 0)]
        return (nb[g] * 1000 + pu[g].rank(pct=True)).sort_values(ascending=False).index[:k]
    return pick
cond("클러스터 중 매수자수·금액 상위 20", top_by_nb(20))

print("\n== S5. 연도별·회전율 (90d ≥2명 npr>0)")
for yr in ("2024", "2025", "2026"):
    xs = []
    for t8, idx, fwd, ms, F, amt, f in cache:
        if not t8.startswith(yr): continue
        g = idx[(f[90][0] >= 2) & (f[90][1] > 0)]
        xs.append((fwd[20][g].mean() - fwd[20].mean()) * 100 if len(g) >= 10 else np.nan)
    m, lo, hi, n = bb(xs)
    print(f"  {yr}: {m:+.2f}%p 95%[{lo:+.2f},{hi:+.2f}] n={n}")
prev = None; ov = []
for t8, idx, fwd, ms, F, amt, f in cache:
    g = set(idx[(f[90][0] >= 2) & (f[90][1] > 0)])
    if prev: ov.append(len(g & prev) / max(1, len(g)))
    prev = g
print(f"  주간 앵커 간 구성 중복 평균 {np.mean(ov):.0%} (→ 20일 보유 시 회전 추정 {1-np.mean(ov)**4:.0%}, 추정)")

print("\n== S6. 대조: 10%주주만(임원/이사 아님) ≥2명 npr>0, 90d")
def ten_only(idx, fwd, ms, F, amt, f):
    t8 = None
    return None
xs = []
for t8, idx, fwd, ms, F, amt, f in cache:
    lo_ = (pd.Timestamp(t8) - pd.Timedelta(days=90)).strftime("%Y%m%d")
    p = P[(P.filed8 < t8) & (P.filed8 >= lo_) & (P.od == 0)]
    s = S[(S.filed8 < t8) & (S.filed8 >= lo_) & (S.od == 0)]
    nb = p.groupby("symbol").owner_cik.nunique().reindex(idx).fillna(0)
    pu = p.groupby("symbol").usd.sum().reindex(idx).fillna(0)
    su = s.groupby("symbol").usd.sum().reindex(idx).fillna(0)
    npr = ((pu - su) / (pu + su)).where((pu + su) > 0)
    g = idx[(nb >= 2) & (npr > 0)]
    xs.append((fwd[20][g].mean() - fwd[20].mean()) * 100 if len(g) >= 10 else np.nan)
m, lo, hi, n = bb(xs)
print(f"  비임원 클러스터: {m:+.2f}%p 95%[{lo:+.2f},{hi:+.2f}] n={n}")
log("done")
