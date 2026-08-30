# -*- coding: utf-8 -*-
"""
us_sue_scan_20260830.py — SUE(계절 랜덤워크 실적 서프라이즈) 스캔 (in-sample)
==============================================================================
질문: 진짜 SUE(분기 EPS YoY 변화 / 과거 8분기 변화 표준편차)가 발표반응수익
      프록시(pead_ann, 8/30 1차 스캔)보다 나은 신호인가.
구성: xbrl EPS는 YTD 누계(fp Q1=3mo, Q2=6mo, Q3=9mo, FY=12mo — AAPL 실측 확인)
  → 같은 fy 안에서 차분해 분기값 복원(q4 = FY − Q3ytd), (fy,fp) 키로 전년동기 대비.
정직성:
- PIT: 분기값 복원에 쓰인 마지막 공시의 filed < 앵커일. (cik,end) 중복은
  최초 filed 행만 사용(재공시 소급 배제).
- in-sample · 생존편향 미보정 · 주간앵커 h20 중첩 → 블록 부트스트랩(블록=4).
- 단일 신규 팩터지만 8/30 스캔까지 누적 7팩터 검정 맥락 → Bonferroni α=0.05/7 병기.
- 결과는 '기움'이지 채택 아님.
"""
import sqlite3
import numpy as np, pandas as pd
from scipy.stats import spearmanr

DB  = "us-screener-data/us_ohlcv.db"
FDB = "us-screener-data/us_fundamentals.db"
RNG = np.random.default_rng(20260830)
NBOOT = 10000
ALPHAS = (0.05, 0.05/7)

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

# ── EPS YTD 행: diluted 우선, (cik,end) 결측만 basic 보충. 최초 filed 만(재공시 배제)
eps = pd.read_sql("""select cik, tag, end, val, fy, fp, form, filed from xbrl_facts
                     where tag in ('EarningsPerShareDiluted','EarningsPerShareBasic')
                       and ((form like '10-Q%' and fp in ('Q1','Q2','Q3'))
                         or (form like '10-K%' and fp='FY'))""", fc)
eps["filed8"] = eps.filed.str.replace("-", "", regex=False)
eps["end8"]   = eps["end"].str.replace("-", "", regex=False)
eps = eps.sort_values("filed8")
dil = eps[eps.tag == "EarningsPerShareDiluted"].drop_duplicates(["cik", "end8"], keep="first")
bas = eps[eps.tag == "EarningsPerShareBasic"].drop_duplicates(["cik", "end8"], keep="first")
have = set(zip(dil.cik, dil.end8))
bas = bas[~bas.apply(lambda r: (r.cik, r.end8) in have, axis=1)]
E = pd.concat([dil, bas], ignore_index=True)
print("EPS ytd rows:", len(E), flush=True)

# ── 분기값 복원: (cik, fy) 안에서 Q1/Q2−Q1/Q3−Q2/FY−Q3
Q_ORDER = {"Q1": 1, "Q2": 2, "Q3": 3, "FY": 4}
E["qn"] = E.fp.map(Q_ORDER)
rows = []  # (cik, fy, qn, end8, filed8, q_eps)
for (cik, fy), g in E.groupby(["cik", "fy"]):
    g = g.sort_values("qn").drop_duplicates("qn", keep="first")
    ytd = dict(zip(g.qn, g.val))
    meta = {qn: (e8, f8) for qn, e8, f8 in zip(g.qn, g.end8, g.filed8)}
    for qn in g.qn:
        if qn == 1:
            q = ytd[1]
        elif (qn - 1) in ytd:
            q = ytd[qn] - ytd[qn - 1]
        else:
            continue
        e8, f8 = meta[qn]
        rows.append((cik, fy, qn, e8, f8, q))
Q = pd.DataFrame(rows, columns=["cik", "fy", "qn", "end8", "filed8", "q_eps"])
print("복원 분기값:", len(Q), flush=True)

# ── SUE: (fy,qn) vs (fy-1,qn) 차분, 과거 8개 차분 std 로 표준화 (min 4)
Q = Q.sort_values(["cik", "end8"])
prev = Q.rename(columns={"fy": "fy1", "q_eps": "q_prev"})[["cik", "fy1", "qn", "q_prev"]]
Q = Q.merge(prev.assign(fy=lambda d: d.fy1 + 1)[["cik", "fy", "qn", "q_prev"]],
            on=["cik", "fy", "qn"], how="left")
Q["dq"] = Q.q_eps - Q.q_prev
Q = Q.sort_values(["cik", "end8"])
Q["sd8"] = Q.groupby("cik")["dq"].transform(
    lambda s: s.shift(1).rolling(8, min_periods=4).std(ddof=1))
Q["sue"] = Q.dq / Q.sd8.replace(0, np.nan)
Q = Q[np.isfinite(Q.sue)]
Q["sue"] = Q.sue.clip(-10, 10)
print("SUE 값:", len(Q), "| filed", Q.filed8.min(), "~", Q.filed8.max(), flush=True)

# symbol 확장, filed 정렬
srows = []
for cik, f8, e8, sue in Q[["cik", "filed8", "end8", "sue"]].itertuples(index=False):
    for s in cik2syms.get(cik, ()):
        srows.append((s, f8, e8, sue))
S = pd.DataFrame(srows, columns=["symbol", "filed8", "end8", "sue"]).sort_values("filed8")
print("symbol 단위 SUE:", len(S), flush=True)

# ── 프레임 공통 (8/30 스캔과 동일식)
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

ics, cov, cache = [], [], []
for k, i in enumerate(anchors):
    t8 = ds[i]
    idx, amt20 = guard_universe(i)
    fwd = (C.loc[idx, ds[i+20]] / C.loc[idx, ds[i]] - 1)
    lo = (pd.Timestamp(t8) - pd.Timedelta(days=120)).strftime("%Y%m%d")
    sub = S[(S.filed8 < t8) & (S.filed8 >= lo)]
    f = sub.drop_duplicates("symbol", keep="last").set_index("symbol")["sue"].reindex(idx)
    ms = mus_score(i, idx, amt20)
    cache.append((f, ms, fwd))
    m = f.notna() & fwd.notna()
    cov.append(m.mean())
    ics.append(spearmanr(f[m], fwd[m]).statistic if m.sum() > 100 else np.nan)
    if k % 20 == 0: print(f"  anchor {k}/{len(anchors)}", flush=True)

print(f"\n== 프레임1: sue day-IC h20 (n앵커={sum(np.isfinite(v) for v in ics)})")
cis, m = block_boot_ci(ics)
lo95, hi95 = cis[0.05]; lob, hib = cis[0.05/7]
print(f"  sue IC={m:+.4f} 95%[{lo95:+.4f},{hi95:+.4f}]"
      f"{'*' if lo95>0 or hi95<0 else ' '} Bonf7[{lob:+.4f},{hib:+.4f}]"
      f"{'**' if lob>0 or hib<0 else ''} cover={np.mean(cov):.0%}")

print("\n== 프레임1b: 십분위 스프레드 (Q10−Q1, %p/20d)")
sprd = []
for f, ms, fwd in cache:
    m = f.notna() & fwd.notna()
    if m.sum() < 200: sprd.append(np.nan); continue
    q = pd.qcut(f[m].rank(method="first"), 10, labels=False)
    sprd.append((fwd[m][q == 9].mean() - fwd[m][q == 0].mean()) * 100)
cis, mm = block_boot_ci(sprd, alpha_list=(0.05,))
print(f"  Q10−Q1 = {mm:+.2f}%p 95%[{cis[0.05][0]:+.2f},{cis[0.05][1]:+.2f}] "
      f"n={sum(np.isfinite(v) for v in sprd)}")

print("\n== 프레임2: us_mus_v0 top50 + sue 4번째 항 (짝비교)")
diffs, othr = [], []
for f, ms, fwd in cache:
    if not f.notna().any(): continue
    rk4 = f.rank(pct=True)
    sc4 = ms + rk4.reindex(ms.index).fillna(0.5)
    t_new = sc4.sort_values(ascending=False).index[:50]
    t_old = ms.sort_values(ascending=False).index[:50]
    diffs.append((fwd[t_new].mean() - fwd[t_old].mean()) * 100)
    othr.append(len(set(t_new) & set(t_old)) / 50)
cis, mm = block_boot_ci(diffs, alpha_list=(0.05,))
print(f"  +sue Δtop50 h20 = {mm:+.3f}%p 95%CI[{cis[0.05][0]:+.3f},{cis[0.05][1]:+.3f}] "
      f"overlap={np.mean(othr):.0%} n={len(diffs)}")

pd.DataFrame({"anchor": [ds[i] for i in anchors], "ic": ics, "q10_q1_pp": sprd}) \
  .to_csv("us_sue_scan_frame.csv", index=False)
print("\ndone.")
