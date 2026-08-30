# -*- coding: utf-8 -*-
"""
us_sec_scan_20260830.py — SEC 직교 데이터 1차 스캔 (in-sample · 관측 전용)
==========================================================================
질문: SEC 3종(earnings_events·insider_tx·xbrl_facts)에 us_mus_v0 를 넘어설
      새 알파가 있는가 (PREREGISTER 초안 §1 조건부 편입 조건 검증).
프레임1: 전 유니버스 day-IC(스피어만, h20) — SEC 팩터 6종, 주간 앵커.
프레임2: 95% 유의 팩터를 us_mus_v0 순위합 4번째 항으로 → top50 짝비교.

정직성:
- 전부 in-sample · 생존편향 미보정. 주간앵커 h20 중첩 → 블록 부트스트랩(블록=4).
- 다중검정: 6팩터 → Bonferroni α=0.05/6≈0.00833, 99.17% CI 병기.
- PIT: 모든 SEC 자료는 filed(공시일) < 앵커일 조건으로만 사용.
- insider_tx 는 filed 2026-03-31 까지만 백필됨 → 이후 앵커는 결측 처리.
- PEAD 프록시: 애널리스트 추정치가 없으므로 발표반응수익(ann_ret)을 서프라이즈
  대용으로 사용(문헌 표준 대용). accepted(UTC) 21시 이후 접수는 익영업일 반응일.
- 결과는 '기움'이지 채택 아님. 채택은 PREREGISTER + OOS 로만.
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
dpos = {d: i for i, d in enumerate(ds)}
print("matrix", C.shape, ds[0], ds[-1], flush=True)

fc = sqlite3.connect(f"file:{FDB}?mode=ro", uri=True)

# ── cik → symbols 매핑 (한 cik 여러 클래스 허용, ohlcv 에 있는 심볼만)
ct = pd.read_sql("select cik, ticker from cik_ticker", fc)
ct["ticker"] = ct.ticker.str.upper()
ct = ct[ct.ticker.isin(C.index)]
cik2syms = ct.groupby("cik")["ticker"].apply(list).to_dict()
print("cik matched to ohlcv symbols:", len(cik2syms), flush=True)

def ymd(s):  # 'YYYY-MM-DD' -> 'YYYYMMDD'
    return s.replace("-", "")

# ── PEAD 재료: 발표 이벤트별 반응수익 ann_ret = C[r0+1]/C[r0-1]-1
ev = pd.read_sql("select cik, filed, accepted from earnings_events where is_earnings=1", fc)
ev["filed8"] = ev.filed.map(ymd)
ev["hour"] = ev.accepted.str.slice(11, 13).astype(int)
ev = ev[ev.filed8 >= ds[0]]
ds_arr = np.array(ds)
def next_td(d8, after=False):
    """d8 당일 이상(또는 초과)의 첫 거래일 인덱스"""
    i = np.searchsorted(ds_arr, d8, side="right" if after else "left")
    return i if i < len(ds_arr) else None
pead_rows = []  # (symbol, ridx1(반응창 끝 인덱스), ann_ret)
for cik, filed8, hour in ev[["cik", "filed8", "hour"]].itertuples(index=False):
    syms = cik2syms.get(cik)
    if not syms: continue
    after_close = hour >= 21  # UTC 21시 ≈ ET 16~17시 이후
    i0 = next_td(filed8, after=after_close)
    if i0 is None or i0 < 1 or i0 + 1 >= len(ds): continue
    for s in syms:
        p_pre = C.at[s, ds[i0-1]]; p_post = C.at[s, ds[i0+1]]
        if np.isfinite(p_pre) and np.isfinite(p_post) and p_pre > 0:
            pead_rows.append((s, i0 + 1, p_post / p_pre - 1))
pead = pd.DataFrame(pead_rows, columns=["symbol", "ridx1", "ann_ret"]).sort_values("ridx1")
print("pead events with prices:", len(pead), flush=True)

# ── insider 재료
ins = pd.read_sql("""select symbol, filed, code, owner_cik, is_officer, is_director
                     from insider_tx""", fc)
# 실측: is_officer/is_director/is_tenpct 가 전 행 0 (수집기 플래그 미적재) →
# 관계 필터 불가. 전체 내부자 P/S 로 스캔하고 한계로 기록한다.
ins["filed8"] = ins.filed.map(ymd)
ins = ins[ins.filed8 >= ds[0]].sort_values("filed8")
INS_MAX_FILED = ins.filed8.max()
print("insider rows in window:", len(ins), "filed to", INS_MAX_FILED, flush=True)

# ── xbrl 재료: 태그별 filed 정렬 테이블
xb = pd.read_sql("""select cik, tag, end, val, fy, fp, form, filed from xbrl_facts
                    where tag in ('NetIncomeLoss','StockholdersEquity',
                    'NetCashProvidedByUsedInOperatingActivities',
                    'EntityCommonStockSharesOutstanding','CommonStockSharesOutstanding')""", fc)
xb["filed8"] = xb.filed.map(ymd)
xb["end8"] = xb["end"].map(ymd)
xb = xb.sort_values("filed8")
ni_fy  = xb[(xb.tag == "NetIncomeLoss") & (xb.fp == "FY") & (xb.form.str.startswith("10-K"))]
cfo_fy = xb[(xb.tag == "NetCashProvidedByUsedInOperatingActivities") & (xb.fp == "FY")
            & (xb.form.str.startswith("10-K"))]
se_q   = xb[xb.tag == "StockholdersEquity"]
sh_a   = xb[xb.tag == "EntityCommonStockSharesOutstanding"]
sh_b   = xb[xb.tag == "CommonStockSharesOutstanding"]
print("xbrl slices:", {k: len(v) for k, v in
      dict(ni_fy=ni_fy, cfo_fy=cfo_fy, se_q=se_q, sh=sh_a).items()}, flush=True)

def latest_before(tbl, t8, max_end_age_days=455):
    """filed < t8 인 것 중 cik별 최신 filed 값. end 가 너무 오래된 것 제외."""
    sub = tbl[tbl.filed8 < t8]
    if max_end_age_days is not None:
        cut = (pd.Timestamp(t8) - pd.Timedelta(days=max_end_age_days)).strftime("%Y%m%d")
        sub = sub[sub.end8 >= cut]
    return sub.drop_duplicates("cik", keep="last").set_index("cik")["val"]

# ── 가드·mus 점수 (scan2 와 동일식)
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

def sym_from_cik(series_by_cik, idx):
    """cik 인덱스 시리즈를 symbol 인덱스로 확장"""
    out = {}
    for cik, val in series_by_cik.items():
        for s in cik2syms.get(cik, ()):
            out[s] = val
    return pd.Series(out).reindex(idx)

FACTORS = ["pead_ann", "ins_npr", "ins_clbuy", "ep", "bm", "cfoy"]

def calc_sec_factors(i, idx):
    t8 = ds[i]
    out = pd.DataFrame(index=idx)
    # PEAD: 반응창이 앵커 전에 닫힌(ridx1 < i) 최근 75일 내 이벤트
    lo = (pd.Timestamp(t8) - pd.Timedelta(days=75)).strftime("%Y%m%d")
    sub = pead[(pead.ridx1 < i)]
    sub = sub[ds_arr[sub.ridx1.values] >= lo]
    out["pead_ann"] = sub.drop_duplicates("symbol", keep="last") \
                         .set_index("symbol")["ann_ret"].reindex(idx)
    # insider 90일 창 (백필 범위 밖 앵커는 전부 NaN 처리)
    lo90 = (pd.Timestamp(t8) - pd.Timedelta(days=90)).strftime("%Y%m%d")
    if lo90 <= INS_MAX_FILED:
        w = ins[(ins.filed8 < t8) & (ins.filed8 >= lo90)]
        if t8 > INS_MAX_FILED:  # 창이 백필 경계에 걸림 → 사용하지 않음
            out["ins_npr"] = np.nan; out["ins_clbuy"] = np.nan
        else:
            b = w[w.code == "P"].groupby("symbol").size()
            s = w[w.code == "S"].groupby("symbol").size()
            act = b.index.union(s.index)
            npr = (b.reindex(act).fillna(0) - s.reindex(act).fillna(0)) / \
                  (b.reindex(act).fillna(0) + s.reindex(act).fillna(0))
            out["ins_npr"] = npr.reindex(idx)
            cl = w[w.code == "P"].groupby("symbol")["owner_cik"].nunique()
            out["ins_clbuy"] = cl.reindex(idx).fillna(0)
    else:
        out["ins_npr"] = np.nan; out["ins_clbuy"] = np.nan
    # 밸류에이션: mktcap = 최신 보고 주식수 × 당일 close(비조정)
    sh = latest_before(sh_a, t8, None)
    shb = latest_before(sh_b, t8, None)
    sh = sh.combine_first(shb)
    sh_sym = sym_from_cik(sh, idx)
    mcap = sh_sym * RC.loc[idx, t8]
    mcap = mcap.where(mcap > 0)
    out["ep"]   = sym_from_cik(latest_before(ni_fy, t8), idx) / mcap
    out["bm"]   = sym_from_cik(latest_before(se_q, t8), idx) / mcap
    out["cfoy"] = sym_from_cik(latest_before(cfo_fy, t8), idx) / mcap
    return out

# ── 앵커 (scan2 와 동일 간격)
anchors = [i for i in range(252, len(ds) - 20, 5)]
print("anchors:", len(anchors), ds[anchors[0]], "→", ds[anchors[-1]], flush=True)

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

ics = {f: [] for f in FACTORS}
cov = {f: [] for f in FACTORS}
cache = []
for k, i in enumerate(anchors):
    idx, amt20 = guard_universe(i)
    fwd = (C.loc[idx, ds[i+20]] / C.loc[idx, ds[i]] - 1)
    fac = calc_sec_factors(i, idx)
    ms  = mus_score(i, idx, amt20)
    cache.append((i, fac, ms, fwd))
    for f in FACTORS:
        m = fac[f].notna() & fwd.notna() & np.isfinite(fac[f])
        cov[f].append(m.mean())
        ics[f].append(spearmanr(fac.loc[m, f], fwd[m]).statistic if m.sum() > 100 else np.nan)
    if k % 20 == 0: print(f"  anchor {k}/{len(anchors)} univ={len(idx)}", flush=True)

print(f"\n== 프레임1: day-IC h20 (주간앵커 · 블록부트스트랩 · Bonferroni α={0.05/6:.5f})")
rows1 = []
for f in FACTORS:
    cis, m = block_boot_ci(ics[f])
    lo95, hi95 = cis[0.05]; lob, hib = cis[0.05/6]
    sig95 = "*" if (np.isfinite(lo95) and (lo95 > 0 or hi95 < 0)) else " "
    sigB  = "**" if (np.isfinite(lob) and (lob > 0 or hib < 0)) else "  "
    n_eff = int(sum(np.isfinite(v) for v in ics[f]))
    rows1.append((f, n_eff, m, lo95, hi95, lob, hib, float(np.mean(cov[f]))))
    print(f"  {f:10s} n={n_eff:3d} IC={m:+.4f} 95%[{lo95:+.4f},{hi95:+.4f}]{sig95} "
          f"Bonf[{lob:+.4f},{hib:+.4f}]{sigB} cover={np.mean(cov[f]):.0%}")
pd.DataFrame(rows1, columns=["factor","n","meanIC","ci95_lo","ci95_hi","bonf_lo","bonf_hi","coverage"]) \
  .to_csv("us_sec_scan_frame1.csv", index=False)

print("\n== 프레임2: us_mus_v0 top50 + 4번째 팩터 (짝비교, %p)")
rows2 = []
for f, n_eff, mic, lo, hi, lob, hib, c in rows1:
    if not (np.isfinite(lo) and (lo > 0 or hi < 0)): continue
    sign = 1 if mic > 0 else -1
    diffs, othr = [], []
    for (i, fac, ms, fwd) in cache:
        if not fac[f].notna().any(): continue
        rk4 = fac[f].rank(pct=True)
        if sign < 0: rk4 = 1 - rk4
        sc4 = ms + rk4.reindex(ms.index).fillna(0.5)
        t_new = sc4.sort_values(ascending=False).index[:50]
        t_old = ms.sort_values(ascending=False).index[:50]
        diffs.append((fwd[t_new].mean() - fwd[t_old].mean()) * 100)
        othr.append(len(set(t_new) & set(t_old)) / 50)
    cis, m = block_boot_ci(diffs, alpha_list=(0.05,))
    lo2, hi2 = cis[0.05]
    rows2.append((f, m, lo2, hi2, float(np.mean(othr)), len(diffs)))
    print(f"  +{f:10s} Δtop50 h20 = {m:+.3f}%p 95%CI[{lo2:+.3f},{hi2:+.3f}] "
          f"overlap={np.mean(othr):.0%} n={len(diffs)}")
if rows2:
    pd.DataFrame(rows2, columns=["factor","d_pp","ci_lo","ci_hi","overlap","n"]) \
      .to_csv("us_sec_scan_frame2.csv", index=False)
print("\ndone.")
