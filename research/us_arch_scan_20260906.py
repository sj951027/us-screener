# -*- coding: utf-8 -*-
"""
us_arch_scan_20260906.py — 모델 '고르기 방식' 아키텍처 스캔 (in-sample, 관측 전용)
=================================================================================
질문: us_mus_v0 에 항을 더하는 대신, 처음부터 다르게 고르는 방식 중 EW 유니버스·SPX 를
      더 크게·더 꾸준히 이기는 것이 있는가. 9월 PREREGISTER 등록 후보를 고르기 위한 스캔.
잣대(전 후보 동일): 주간앵커(5거래일 간격) · h20 · 종가체결 · 초과 = 모델 EW 20일 수익 −
  가드 유니버스 EW 20일 수익 · 블록 부트스트랩(블록=4, 10,000회) · 후보 N개 → Bonferroni α=0.05/N.
  보조: SPX 대비, 적중률, 앵커간 회전(20d 전 구성 대비 교체 비율), 왕복 0.2%p × 교체율 비용
  차감 초과, 비중첩 체인(20거래일 간격, 오프셋 4개 평균) 누적수익·최대낙폭.
정직성: in-sample · 생존편향 미보정 · 상승장 한 구간(2024-05~2026-08) · 매직넘버 최소화
  (모든 순위합은 동일가중, 문턱은 '0'·'중앙값'·'상위 20%' 같은 정의상 값만).
  임원/이사 판정은 2/3 프록시(9/6 orth 스캔 참조). 결과는 기움이지 채택 아님.
"""
import sqlite3, time
import numpy as np, pandas as pd

DB  = "us-screener-data/us_ohlcv.db"
FDB = "us-screener-data/us_fundamentals.db"
MDB = "us-screener-data/us_market.db"
RNG = np.random.default_rng(20260906)
NBOOT = 10000
COST_RT = 0.2   # %p 왕복
T0 = time.time()
def log(*a): print(f"[{time.time()-T0:5.0f}s]", *a, flush=True)

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
df = pd.read_sql("select symbol,date,close,adj_close,volume from daily_ohlcv", con)
C  = df.pivot(index="symbol", columns="date", values="adj_close")
RC = df.pivot(index="symbol", columns="date", values="close")
V  = df.pivot(index="symbol", columns="date", values="volume")
del df
ds = sorted(C.columns); C = C[ds]; RC = RC[ds]; V = V[ds]
R = C.pct_change(axis=1)
sec = pd.read_sql("select symbol, sector, industry from sector_cache", con).set_index("symbol")
log("ohlcv", C.shape)

mc = sqlite3.connect(f"file:{MDB}?mode=ro", uri=True)
spx = pd.read_sql("select date,close from market_daily where series='SPX'", mc).set_index("date").close
spx = spx.reindex(ds).ffill()
spx_r = spx.pct_change()

fc = sqlite3.connect(f"file:{FDB}?mode=ro", uri=True)
ct = pd.read_sql("select cik, ticker from cik_ticker", fc); ct["ticker"] = ct.ticker.str.upper()
ct = ct[ct.ticker.isin(C.index)]
cik2syms = ct.groupby("cik")["ticker"].apply(list).to_dict()

def expand(dfc, valcol):
    rows = []
    for cik, f8, v in dfc[["cik", "filed8", valcol]].itertuples(index=False):
        for s in cik2syms.get(cik, ()): rows.append((s, f8, v))
    return pd.DataFrame(rows, columns=["symbol", "filed8", "val"]).sort_values("filed8")

def latest_before(tab, t8, days=400):
    lo = (pd.Timestamp(t8) - pd.Timedelta(days=days)).strftime("%Y%m%d")
    sub = tab[(tab.filed8 < t8) & (tab.filed8 >= lo)]
    return sub.drop_duplicates("symbol", keep="last").set_index("symbol")["val"]

# ── XBRL: 주식수·자본·순이익(FY)·영업CF(FY)·총자산
xb = pd.read_sql("""select cik, tag, end, val, fy, fp, form, filed from xbrl_facts
    where tag in ('EntityCommonStockSharesOutstanding','CommonStockSharesOutstanding',
    'StockholdersEquity','NetIncomeLoss','NetCashProvidedByUsedInOperatingActivities','Assets',
    'EarningsPerShareDiluted','EarningsPerShareBasic')""", fc)
xb["filed8"] = xb.filed.str.replace("-", "", regex=False)
xb["end8"] = xb["end"].str.replace("-", "", regex=False)
xb = xb.sort_values("filed8")
sh = xb[xb.tag.isin(["EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"]) & (xb.val > 0)]
sh = sh.assign(pri=(sh.tag != "EntityCommonStockSharesOutstanding").astype(int)).sort_values(["filed8", "pri"])
SH  = expand(sh, "val")
EQ  = expand(xb[xb.tag == "StockholdersEquity"], "val")
AT  = expand(xb[(xb.tag == "Assets") & (xb.val > 0)], "val")
NI  = expand(xb[(xb.tag == "NetIncomeLoss") & (xb.fp == "FY") & xb.form.str.startswith("10-K")], "val")
CFO = expand(xb[(xb.tag == "NetCashProvidedByUsedInOperatingActivities") & (xb.fp == "FY") & xb.form.str.startswith("10-K")], "val")
log("xbrl tables", len(SH), len(EQ), len(AT), len(NI), len(CFO))

# ── SUE (8/30 스캔과 동일 구성)
eps = xb[xb.tag.isin(["EarningsPerShareDiluted", "EarningsPerShareBasic"]) &
         (((xb.form.str.startswith("10-Q")) & xb.fp.isin(["Q1", "Q2", "Q3"])) |
          ((xb.form.str.startswith("10-K")) & (xb.fp == "FY")))]
dil = eps[eps.tag == "EarningsPerShareDiluted"].drop_duplicates(["cik", "end8"], keep="first")
bas = eps[eps.tag == "EarningsPerShareBasic"].drop_duplicates(["cik", "end8"], keep="first")
have = set(zip(dil.cik, dil.end8))
bas = bas[[(c, e) not in have for c, e in zip(bas.cik, bas.end8)]]
E = pd.concat([dil, bas]); E["qn"] = E.fp.map({"Q1": 1, "Q2": 2, "Q3": 3, "FY": 4})
rows = []
for (cik, fy), g in E.groupby(["cik", "fy"]):
    g = g.sort_values("qn").drop_duplicates("qn", keep="first")
    ytd = dict(zip(g.qn, g.val)); meta = {q: (e, f) for q, e, f in zip(g.qn, g.end8, g.filed8)}
    for qn in g.qn:
        if qn == 1: q = ytd[1]
        elif (qn - 1) in ytd: q = ytd[qn] - ytd[qn - 1]
        else: continue
        rows.append((cik, fy, qn, *meta[qn], q))
Q = pd.DataFrame(rows, columns=["cik", "fy", "qn", "end8", "filed8", "q_eps"]).sort_values(["cik", "end8"])
prev = Q[["cik", "fy", "qn", "q_eps"]].rename(columns={"q_eps": "q_prev"}); prev["fy"] += 1
Q = Q.merge(prev, on=["cik", "fy", "qn"], how="left"); Q["dq"] = Q.q_eps - Q.q_prev
Q = Q.sort_values(["cik", "end8"])
Q["sd8"] = Q.groupby("cik")["dq"].transform(lambda s: s.shift(1).rolling(8, min_periods=4).std(ddof=1))
Q["sue"] = (Q.dq / Q.sd8.replace(0, np.nan)).clip(-10, 10)
Q = Q[np.isfinite(Q.sue)]
SUE = expand(Q.rename(columns={"sue": "val"}), "val")
log("SUE rows", len(SUE))

# ── 내부자 클러스터 (9/6 orth 스캔과 동일 · 프록시)
ins = pd.read_sql("""select quarter, symbol, issuer_cik, owner_cik, is_officer, is_director, is_tenpct,
                     code, shares, price, filed from insider_tx where code in ('P','S')""", fc)
ins["symbol"] = ins.symbol.str.upper(); ins = ins[ins.symbol.isin(C.index)]
ins["filed8"] = ins.filed.str.replace("-", "", regex=False)
fq = set(ins[(ins.is_officer + ins.is_director + ins.is_tenpct) > 0].quarter.unique())
fl = ins[ins.quarter.isin(fq)]
od_pairs = set(zip(fl[(fl.is_officer == 1) | (fl.is_director == 1)].issuer_cik,
                   fl[(fl.is_officer == 1) | (fl.is_director == 1)].owner_cik))
ins["od"] = np.where(ins.quarter.isin(fq), ((ins.is_officer == 1) | (ins.is_director == 1)).astype(int),
                     pd.Series(list(zip(ins.issuer_cik, ins.owner_cik))).isin(od_pairs).astype(int).values)
ins["usd"] = (ins.shares.abs() * ins.price.abs()).fillna(0)
ins = ins[(ins.usd > 0) & (ins.od == 1)].sort_values("filed8")
IP = ins[ins.code == "P"]; IS = ins[ins.code == "S"]
def cluster_set(t8, idx):
    lo = (pd.Timestamp(t8) - pd.Timedelta(days=90)).strftime("%Y%m%d")
    p = IP[(IP.filed8 < t8) & (IP.filed8 >= lo)]; s = IS[(IS.filed8 < t8) & (IS.filed8 >= lo)]
    nb = p.groupby("symbol").owner_cik.nunique().reindex(idx).fillna(0)
    pu = p.groupby("symbol").usd.sum().reindex(idx).fillna(0)
    su = s.groupby("symbol").usd.sum().reindex(idx).fillna(0)
    return idx[(nb >= 2) & (pu > su)]

# ── 공통
def guard(i):
    t = ds[i]; c63 = C[ds[i-62:i+1]]
    amt20 = (RC[ds[i-19:i+1]] * V[ds[i-19:i+1]]).mean(axis=1)
    ok = (RC[t] >= 5) & (amt20 >= 1e6) & (c63.notna().sum(axis=1) >= 60) & (c63.std(axis=1, ddof=1) > 0)
    return ok[ok].index, amt20

def rk(s): return s.rank(pct=True)
def ranksum(F, cols, core=None):
    sc = None
    for c in cols:
        r = rk(F[c]); sc = r if sc is None else sc + r.fillna(0.5)
    core = F[cols[0]].notna() if core is None else core
    return sc.where(core).dropna()

def feats(i, idx, amt20):
    t8 = ds[i]
    F = pd.DataFrame(index=idx)
    w63 = R[ds[i-62:i+1]].loc[idx]
    F["mom12"] = C.loc[idx, ds[i-21]] / C.loc[idx, ds[i-252]] - 1
    F["upratio63"] = (w63 > 0).sum(axis=1) / w63.notna().sum(axis=1)
    F["size_amt"] = np.log10(amt20.reindex(idx).where(amt20.reindex(idx) > 0))
    F["prox52"] = C.loc[idx, t8] / C.loc[idx, ds[i-251:i+1]].max(axis=1)
    F["rv63"] = w63.std(axis=1, ddof=1)
    F["ma200"] = C.loc[idx, ds[i-199:i+1]].mean(axis=1)
    # 잔차 모멘텀: [i-252, i-21] 일간수익을 SPX 에 회귀, 잔차 합
    W = R[ds[i-252:i-20]].loc[idx]; m = spx_r[ds[i-252:i-20]]
    mm = m - m.mean(); Wc = W.sub(W.mean(axis=1), axis=0)
    beta = (Wc * mm).sum(axis=1) / (mm ** 2).sum()
    resid = W.sub(np.outer(beta, m), axis=0)
    F["resmom"] = resid.sum(axis=1).where(W.notna().sum(axis=1) >= 200)
    # XBRL
    shr = latest_before(SH, t8); mcap = (shr.reindex(idx) * RC.loc[idx, t8])
    F["mcap"] = mcap.where(mcap > 0)
    F["bm"] = (latest_before(EQ, t8).reindex(idx) / F.mcap)
    F["roa"] = (latest_before(NI, t8).reindex(idx) / latest_before(AT, t8).reindex(idx))
    F["cfoy"] = (latest_before(CFO, t8).reindex(idx) / F.mcap)
    F["sue"] = latest_before(SUE, t8, days=120).reindex(idx)
    F["ind"] = sec.industry.reindex(idx)
    return F

# ── 후보 모델 (전부 상위 50, 순위합 동일가중)
def m_base(F, idx):    return ranksum(F, ["mom12", "upratio63", "size_amt"]).sort_values(ascending=False).index[:50]
def m_base10(F, idx):  return m_base(F, idx)[:10]
def m_emom(F, idx):    # 실적 모멘텀 주신호: sue(최근 120일 공시) + mom12 + upratio63
    return ranksum(F, ["sue", "mom12", "upratio63"]).sort_values(ascending=False).index[:50]
def m_qvm(F, idx):     # 가치+질+추세
    return ranksum(F, ["bm", "roa", "mom12"]).sort_values(ascending=False).index[:50]
def m_hi52(F, idx):    return ranksum(F, ["prox52", "upratio63", "size_amt"]).sort_values(ascending=False).index[:50]
def m_resmom(F, idx):  return ranksum(F, ["resmom", "upratio63", "size_amt"]).sort_values(ascending=False).index[:50]
def m_insclu(F, idx):  return F.attrs["clu"]
def m_insclu_tr(F, idx):
    g = F.attrs["clu"]; return g[(C.loc[g, F.attrs["t8"]] > F.ma200[g])]
def m_small(F, idx):   # 거래대금 하위半(≥$1M 가드는 유지) 안에서 mom12+upratio63
    m = F.size_amt < F.size_amt.median()
    return ranksum(F[m], ["mom12", "upratio63"]).sort_values(ascending=False).index[:50]
def m_indmom(F, idx):  # 업종 EW mom12 상위 20% 업종(구성 ≥10) 안에서 base 점수 상위 50
    g = F.groupby("ind").mom12.agg(["mean", "count"]); g = g[g["count"] >= 10]
    top_ind = g[g["mean"] >= g["mean"].quantile(0.8)].index
    sub = F[F.ind.isin(top_ind)]
    return ranksum(sub, ["mom12", "upratio63", "size_amt"]).sort_values(ascending=False).index[:50]
def m_qlv(F, idx):     # 방어형: 수익성 + 현금흐름수익률 + 저변동
    G = F.assign(nrv=-F.rv63)
    return ranksum(G, ["roa", "cfoy", "nrv"]).sort_values(ascending=False).index[:50]

MODELS = {
    "base(mus_v0 top50)": m_base, "base top10": m_base10, "emom(sue+mom+up)": m_emom,
    "qvm(bm+roa+mom)": m_qvm, "hi52(prox52+up+size)": m_hi52, "resmom(잔차mom+up+size)": m_resmom,
    "insclu(임원클러스터 EW)": m_insclu, "insclu∩>200MA": m_insclu_tr,
    "small(하위半 mom+up)": m_small, "indmom(상위업종 내 base)": m_indmom, "qlv(roa+cfoy+저변동)": m_qlv,
}
N = len(MODELS)
ALPHA_B = 0.05 / N

anchors = [i for i in range(252, len(ds) - 20, 5)]
log("anchors", len(anchors), ds[anchors[0]], ds[anchors[-1]], "| models", N)
recs = {k: [] for k in MODELS}; sets = {k: {} for k in MODELS}
uni_ret, spx_ret, adates = [], [], []
for k_, i in enumerate(anchors):
    t8 = ds[i]; idx, amt20 = guard(i)
    F = feats(i, idx, amt20); F.attrs["clu"] = cluster_set(t8, idx); F.attrs["t8"] = t8
    fwd = C.loc[idx, ds[i+20]] / C.loc[idx, ds[i]] - 1
    u = fwd.mean(); s = spx[ds[i+20]] / spx[ds[i]] - 1
    uni_ret.append(u); spx_ret.append(s); adates.append(t8)
    for name, fn in MODELS.items():
        try: g = pd.Index(fn(F, idx))
        except Exception as e: g = pd.Index([])
        sets[name][i] = set(g)
        recs[name].append(fwd[g].mean() if len(g) >= 10 else np.nan)
    if k_ % 20 == 0: log(f"  anchor {k_}/{len(anchors)} univ={len(idx)} clu={len(F.attrs['clu'])}")

def bb(x, alphas=(0.05, ALPHA_B), block=4):
    x = np.asarray(x, float); ok = np.isfinite(x); x = x[ok]; n = len(x)
    if n < 8: return np.nan, {a: (np.nan, np.nan) for a in alphas}, n
    nblk = int(np.ceil(n / block)); means = np.empty(NBOOT)
    for b in range(NBOOT):
        st = RNG.integers(0, n, nblk); sel = (st[:, None] + np.arange(block)[None, :]).ravel() % n
        means[b] = x[sel[:n]].mean()
    return x.mean(), {a: (np.quantile(means, a/2), np.quantile(means, 1-a/2)) for a in alphas}, n

uni_ret = np.array(uni_ret); spx_ret = np.array(spx_ret)
print(f"\n== 아키텍처 스캔 결과 (h20, n앵커={len(anchors)}, 후보 {N}개 → Bonferroni α={ALPHA_B:.4f})")
print(f"  유니버스 EW 20일 평균 {uni_ret.mean()*100:+.2f}% · SPX {spx_ret.mean()*100:+.2f}%")
hdr = f"  {'모델':26s} {'초과vsEW':>9s} {'95%CI':>17s} {'BonfCI':>17s} {'vsSPX':>7s} {'적중':>5s} {'교체':>5s} {'비용후':>7s} {'종목':>5s} {'누적(체인)':>10s} {'MDD':>6s}"
print(hdr)
out_rows = []
for name in MODELS:
    r = np.array(recs[name], float)
    ex = (r - uni_ret) * 100; exs = (r - spx_ret) * 100
    m, cis, n = bb(ex)
    lo, hi = cis[0.05]; lb, hb = cis[ALPHA_B]
    s1 = "*" if lo > 0 else " "; s2 = "**" if lb > 0 else "  "
    # 교체율: 4앵커(20거래일) 전 구성 대비
    turns = []
    keys = sorted(sets[name])
    for a, b_ in zip(keys[:-4], keys[4:]):
        A, B = sets[name][a], sets[name][b_]
        if A and B: turns.append(1 - len(A & B) / len(B))
    turn = np.nanmean(turns) if turns else np.nan
    cost = COST_RT * turn
    nstk = np.mean([len(sets[name][k]) for k in keys if sets[name][k]])
    # 비중첩 체인 누적 (오프셋 0~3, 4앵커 간격) — 비용 차감 전, 유니버스 EW 대비 아님(절대)
    cums, mdds = [], []
    for off in range(4):
        chain = r[off::4]; chain = chain[np.isfinite(chain)]
        if len(chain) < 5: continue
        eq = np.cumprod(1 + chain); cums.append(eq[-1] - 1)
        mdds.append((eq / np.maximum.accumulate(eq) - 1).min())
    cum = np.mean(cums) * 100 if cums else np.nan; mdd = np.mean(mdds) * 100 if mdds else np.nan
    hit = np.nanmean(ex > 0)
    print(f"  {name:26s} {m:+8.2f}%p [{lo:+6.2f},{hi:+6.2f}]{s1} [{lb:+6.2f},{hb:+6.2f}]{s2} "
          f"{np.nanmean(exs):+6.2f} {hit:5.0%} {turn:5.0%} {m-cost:+6.2f} {nstk:5.0f} {cum:+9.1f}% {mdd:5.1f}%")
    out_rows.append(dict(model=name, excess=m, lo95=lo, hi95=hi, lob=lb, hib=hb, vs_spx=np.nanmean(exs),
                         hit=hit, turnover=turn, excess_net=m-cost, n_stocks=nstk, chain_cum=cum, mdd=mdd, n=n))
# 유니버스·SPX 체인 참고
for lab, arr in [("유니버스 EW", uni_ret), ("SPX", spx_ret)]:
    cums = [np.cumprod(1 + arr[off::4])[-1] - 1 for off in range(4)]
    mdd = np.mean([(np.cumprod(1+arr[off::4]) / np.maximum.accumulate(np.cumprod(1+arr[off::4])) - 1).min() for off in range(4)])
    print(f"  (참고) {lab:20s} 체인 누적 {np.mean(cums)*100:+.1f}% MDD {mdd*100:.1f}%")

# 짝비교: 각 모델 − base (같은 앵커)
print("\n== base 대비 짝차이 (모델 − base, %p/20d, 95% CI)")
rb = np.array(recs["base(mus_v0 top50)"], float)
for name in MODELS:
    if name.startswith("base("): continue
    d = (np.array(recs[name], float) - rb) * 100
    m, cis, n = bb(d, alphas=(0.05,)); lo, hi = cis[0.05]
    print(f"  {name:26s} Δ={m:+.2f}%p [{lo:+.2f},{hi:+.2f}]{'*' if (lo>0 or hi<0) else ''} n={n}")

# 연도별 초과 (형태 확인)
print("\n== 연도별 초과 vs EW (%p/20d, 평균만)")
yrs = np.array([d[:4] for d in adates])
print("  " + "모델".ljust(26) + "".join(f"{y:>8s}" for y in ["2024", "2025", "2026"]))
for name in MODELS:
    ex = (np.array(recs[name], float) - uni_ret) * 100
    print("  " + name.ljust(26) + "".join(f"{np.nanmean(ex[yrs==y]):+8.2f}" for y in ["2024", "2025", "2026"]))

# 상관 (분산 재료)
print("\n== 모델 초과수익 상관 (base 와)")
exb = (rb - uni_ret)
for name in MODELS:
    if name.startswith("base("): continue
    e = (np.array(recs[name], float) - uni_ret); ok = np.isfinite(e) & np.isfinite(exb)
    print(f"  {name:26s} corr={np.corrcoef(e[ok], exb[ok])[0,1]:+.2f}")

pd.DataFrame(out_rows).to_csv("us_arch_scan_summary.csv", index=False)
pd.DataFrame({"anchor": adates, "uni": uni_ret, "spx": spx_ret, **{k: recs[k] for k in MODELS}}).to_csv("us_arch_scan_frame.csv", index=False)
log("done")
