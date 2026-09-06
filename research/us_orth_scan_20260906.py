# -*- coding: utf-8 -*-
"""
us_orth_scan_20260906.py — 직교 데이터 3차 스캔 (in-sample, 관측 전용)
=====================================================================
질문 (8/30 스캔에서 아직 안 본 것만):
  A. 내부자 — 임원/이사 한정 매수 신호(클러스터 매수·순매수비). v04 파서로 2019~2023
     분기는 플래그 복구됨. 2024q1~2026q1 은 아직 플래그 0 → 같은 (issuer,owner) 쌍이
     2019~2023 에서 임원/이사로 확인된 경우를 프록시로 사용(추정 표기).
  B. 실적발표 일정 — 전년 동분기 발표일 +364d 로 '예상 발표일'을 만들고
     (Barber et al. 2013 규칙, PIT), h20 창 안 발표 예정 종목의 제외/편향 효과.
  C. size_amt → 실시총(xbrl 보고 주식수 × close, filed<앵커 PIT) 교체 (PREREGISTER §6-5).
  D. 레짐 관측 — SPX 200MA · SPX 24개월 수익 부호 · VIX 로 앵커를 나눠 base 초과 분포 기록.
정직성:
- 전부 filed < 앵커일 PIT. in-sample · 생존편향 미보정 · 종가체결 · 비용 0.
- 주간앵커 h20 중첩 → 블록 부트스트랩(블록=4, 10,000회).
- 이번 스캔 신규 검정 6개(ins 3 + earn 2 + size 1) → Bonferroni α=0.05/6 병기.
  누적 맥락(8/30 까지 14+ 팩터)은 문서에 명시.
- 결과는 '기움'이지 채택 아님.
"""
import sqlite3, sys, time
import numpy as np, pandas as pd
from scipy.stats import spearmanr

DB  = "us-screener-data/us_ohlcv.db"
FDB = "us-screener-data/us_fundamentals.db"
MDB = "us-screener-data/us_market.db"
RNG = np.random.default_rng(20260906)
NBOOT = 10000
NTEST = 6
ALPHAS = (0.05, 0.05/NTEST)
T0 = time.time()
def log(*a):
    print(f"[{time.time()-T0:6.0f}s]", *a, flush=True)

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
log("loading ohlcv ...")
df = pd.read_sql("select symbol,date,close,adj_close,volume from daily_ohlcv", con)
C  = df.pivot(index="symbol", columns="date", values="adj_close")
RC = df.pivot(index="symbol", columns="date", values="close")
V  = df.pivot(index="symbol", columns="date", values="volume")
del df
ds = sorted(C.columns)
log("ohlcv", C.shape, ds[0], ds[-1])

fc = sqlite3.connect(f"file:{FDB}?mode=ro", uri=True)
ct = pd.read_sql("select cik, ticker from cik_ticker", fc)
ct["ticker"] = ct.ticker.str.upper()
ct = ct[ct.ticker.isin(C.index)]
cik2syms = ct.groupby("cik")["ticker"].apply(list).to_dict()
sym2cik = dict(zip(ct.ticker, ct.cik))

# ── A. 내부자 ────────────────────────────────────────────────────────────
ins = pd.read_sql("""select quarter, symbol, issuer_cik, owner_cik, is_officer, is_director,
                     is_tenpct, trans_date, code, shares, price, filed from insider_tx
                     where code in ('P','S')""", fc)
ins["symbol"] = ins.symbol.str.upper()
ins = ins[ins.symbol.isin(C.index)]
ins["filed8"] = ins.filed.str.replace("-", "", regex=False)
flagged_q = set(ins[(ins.is_officer + ins.is_director + ins.is_tenpct) > 0].quarter.unique())
log("insider rows", len(ins), "flagged quarters", sorted(flagged_q))
# 관계 맵: 플래그 복구된 분기에서 (issuer, owner) → officer/director 여부
fl = ins[ins.quarter.isin(flagged_q)]
od_pairs = set(zip(fl[(fl.is_officer == 1) | (fl.is_director == 1)].issuer_cik,
                   fl[(fl.is_officer == 1) | (fl.is_director == 1)].owner_cik))
ten_only = set(zip(fl[(fl.is_tenpct == 1) & (fl.is_officer == 0) & (fl.is_director == 0)].issuer_cik,
                   fl[(fl.is_tenpct == 1) & (fl.is_officer == 0) & (fl.is_director == 0)].owner_cik))
ins["pair"] = list(zip(ins.issuer_cik, ins.owner_cik))
ins["od_flag"] = ((ins.is_officer == 1) | (ins.is_director == 1)).astype(int)
ins["od_proxy"] = ins.pair.isin(od_pairs).astype(int)
ins["od"] = np.where(ins.quarter.isin(flagged_q), ins.od_flag, ins.od_proxy)
unfl = ins[~ins.quarter.isin(flagged_q)]
log(f"미플래그 분기 행 {len(unfl)} 중 프록시로 임원/이사 판정 {unfl.od.mean():.1%} "
    f"(플래그 분기 실제 비율 {ins[ins.quarter.isin(flagged_q)].od_flag.mean():.1%})")
ins["usd"] = (ins.shares.abs() * ins.price.abs()).fillna(0)
ins = ins[ins.usd > 0]
ins = ins.sort_values("filed8")
P = ins[ins.code == "P"]; S = ins[ins.code == "S"]

def insider_feats(t8, idx):
    lo = (pd.Timestamp(t8) - pd.Timedelta(days=90)).strftime("%Y%m%d")
    p = P[(P.filed8 < t8) & (P.filed8 >= lo)]
    s = S[(S.filed8 < t8) & (S.filed8 >= lo)]
    F = pd.DataFrame(index=idx)
    # 전체 내부자 (8/30 재현용 대조)
    F["ins_all_nbuy"] = p.groupby("symbol").owner_cik.nunique().reindex(idx).fillna(0)
    # 임원/이사 한정
    po = p[p.od == 1]; so = s[s.od == 1]
    F["ins_od_nbuy"] = po.groupby("symbol").owner_cik.nunique().reindex(idx).fillna(0)
    pu = po.groupby("symbol").usd.sum().reindex(idx).fillna(0)
    su = so.groupby("symbol").usd.sum().reindex(idx).fillna(0)
    F["ins_od_npr"] = ((pu - su) / (pu + su)).where((pu + su) > 0)
    F["ins_od_buyusd"] = np.log10(pu.where(pu > 0))
    return F

# ── B. 실적발표 예상일 ───────────────────────────────────────────────────
ev = pd.read_sql("""select cik, filed, report_date from earnings_events where is_earnings=1""", fc)
ev["d"] = pd.to_datetime(ev.report_date.fillna(ev.filed), errors="coerce")
ev = ev.dropna(subset=["d"]).drop_duplicates(["cik", "d"])
ev_by_cik = {c: np.sort(g.d.values) for c, g in ev.groupby("cik")}
log("earnings events", len(ev), "ciks", len(ev_by_cik))

def earn_feats(t8, idx):
    t = pd.Timestamp(t8); tn = np.datetime64(t)
    hi = np.datetime64(t + pd.Timedelta(days=30))
    lo_prev = np.datetime64(t - pd.Timedelta(days=364))
    hi_prev = np.datetime64(t - pd.Timedelta(days=364) + pd.Timedelta(days=30))
    recent = np.datetime64(t - pd.Timedelta(days=45))
    out = {}
    for s in idx:
        c = sym2cik.get(s)
        if c is None or c not in ev_by_cik: continue
        a = ev_by_cik[c]
        a = a[a < tn]                       # PIT: 앵커 전 관측된 발표만
        if len(a) == 0: continue
        pred = ((a >= lo_prev) & (a < hi_prev)).any()   # 전년 같은 창에 발표 有
        just = (a >= recent).any()                         # 최근 45일 내 이미 발표
        out[s] = 1 if (pred and not just) else 0
    return pd.Series(out).reindex(idx)

# ── C. 실시총 (xbrl 보고 주식수 PIT × close) ──────────────────────────────
sh = pd.read_sql("""select cik, tag, end, val, filed from xbrl_facts
                    where tag in ('EntityCommonStockSharesOutstanding','CommonStockSharesOutstanding')
                      and val > 0""", fc)
sh["filed8"] = sh.filed.str.replace("-", "", regex=False)
sh["pri"] = (sh.tag != "EntityCommonStockSharesOutstanding").astype(int)
sh = sh.sort_values(["filed8", "pri"])
srows = []
for cik, f8, v in sh[["cik", "filed8", "val"]].itertuples(index=False):
    for s in cik2syms.get(cik, ()):
        srows.append((s, f8, v))
SH = pd.DataFrame(srows, columns=["symbol", "filed8", "shares"]).sort_values("filed8")
log("shares rows", len(SH))

def mcap_feat(t8, idx):
    sub = SH[SH.filed8 < t8]
    sub = sub[sub.filed8 >= (pd.Timestamp(t8) - pd.Timedelta(days=400)).strftime("%Y%m%d")]
    shr = sub.drop_duplicates("symbol", keep="last").set_index("symbol")["shares"].reindex(idx)
    return np.log10((shr * RC.loc[idx, t8]).where(lambda x: x > 0))

# ── D. 레짐 ──────────────────────────────────────────────────────────────
mc = sqlite3.connect(f"file:{MDB}?mode=ro", uri=True)
mk = pd.read_sql("select series,date,close from market_daily", mc)
spx = mk[mk.series == "SPX"].set_index("date").close.sort_index()
vix = mk[mk.series == "VIX"].set_index("date").close.sort_index()
spx200 = spx.rolling(200, min_periods=150).mean()

# ── 공통 프레임 ──────────────────────────────────────────────────────────
def guard_universe(i):
    t = ds[i]
    c63 = C[ds[i-62:i+1]]
    amt20 = (RC[ds[i-19:i+1]] * V[ds[i-19:i+1]]).mean(axis=1)
    ok = (RC[t] >= 5) & (amt20 >= 1e6) & (c63.notna().sum(axis=1) >= 60) \
         & (c63.std(axis=1, ddof=1) > 0)
    return ok[ok].index, amt20

def mus_score(i, idx, amt20, size_override=None):
    w63 = C[ds[i-62:i+1]].loc[idx].pct_change(axis=1)
    F = pd.DataFrame(index=idx)
    F["mom12"] = C.loc[idx, ds[i-21]] / C.loc[idx, ds[i-252]] - 1
    F["upratio63"] = (w63 > 0).sum(axis=1) / w63.notna().sum(axis=1)
    F["size_amt"] = np.log10(amt20.reindex(idx).where(amt20.reindex(idx) > 0))
    if size_override is not None:
        F["size_amt"] = size_override.reindex(idx)
    sc = None; core = None
    for j, f in enumerate(["mom12", "upratio63", "size_amt"]):
        rk = F[f].rank(pct=True)
        if j == 0: core = rk.notna()
        sc = rk if sc is None else sc + rk.fillna(0.5)
    return sc.where(core).dropna()

def block_boot_ci(x, alpha_list=ALPHAS, block=4):
    x = np.asarray([v for v in x if np.isfinite(v)])
    n = len(x)
    if n < 8: return {a: (np.nan, np.nan) for a in alpha_list}, np.nan, n
    nblk = int(np.ceil(n / block))
    means = np.empty(NBOOT)
    for b in range(NBOOT):
        starts = RNG.integers(0, n, nblk)
        sel = (starts[:, None] + np.arange(block)[None, :]).ravel() % n
        means[b] = x[sel[:n]].mean()
    return {a: (np.quantile(means, a/2), np.quantile(means, 1-a/2))
            for a in alpha_list}, x.mean(), n

def fmt(x, cis, n, unit="", d=3):
    lo, hi = cis[0.05]; lb, hb = cis[ALPHAS[1]]
    s1 = "*" if (lo > 0 or hi < 0) else " "
    s2 = "**" if (lb > 0 or hb < 0) else "  "
    return (f"{x:+.{d}f}{unit} 95%[{lo:+.{d}f},{hi:+.{d}f}]{s1} "
            f"Bonf{NTEST}[{lb:+.{d}f},{hb:+.{d}f}]{s2} n={n}")

anchors = [i for i in range(252, len(ds) - 20, 5)]
log("anchors:", len(anchors), ds[anchors[0]], "→", ds[anchors[-1]])

rows = []
cache = []
for k, i in enumerate(anchors):
    t8 = ds[i]
    idx, amt20 = guard_universe(i)
    fwd = (C.loc[idx, ds[i+20]] / C.loc[idx, ds[i]] - 1)
    ms = mus_score(i, idx, amt20)
    F = insider_feats(t8, idx)
    F["earn_pred"] = earn_feats(t8, idx)
    F["mcap"] = mcap_feat(t8, idx)
    ms_mc = mus_score(i, idx, amt20, size_override=F["mcap"])
    cache.append((t8, idx, fwd, ms, ms_mc, F))
    if k % 20 == 0: log(f"  anchor {k}/{len(anchors)} univ={len(idx)} "
                        f"od_nbuy>0={int((F.ins_od_nbuy>0).sum())} earn_pred={F.earn_pred.mean():.2f} "
                        f"mcap_cover={F.mcap.notna().mean():.0%}")

# 커버리지 & 프록시 정직성: 앵커 시기별 od 원천 (플래그 vs 프록시)
log("\n== 커버리지")
for name in ["ins_all_nbuy", "ins_od_nbuy", "ins_od_npr", "earn_pred", "mcap"]:
    cov = np.mean([(F[name].notna() & (F[name] != 0)).mean() if name.startswith("ins") else F[name].notna().mean()
                   for *_, F in cache])
    print(f"  {name}: 비영/유효 비율 평균 {cov:.1%}")

# ── 프레임1: day-IC h20 ──────────────────────────────────────────────────
print("\n== 프레임1: 전 유니버스 day-IC h20 (스피어만)")
ic_tab = {}
for name in ["ins_all_nbuy", "ins_od_nbuy", "ins_od_npr", "ins_od_buyusd", "earn_pred", "mcap"]:
    ics = []
    for t8, idx, fwd, ms, ms_mc, F in cache:
        f = F[name]; m = f.notna() & fwd.notna()
        if name in ("ins_od_npr", "ins_od_buyusd"):
            pass  # 결측=거래 없음 → 제외(정의상)
        ics.append(spearmanr(f[m], fwd[m]).statistic if (m.sum() > 100 and f[m].nunique() > 1) else np.nan)
    cis, m, n = block_boot_ci(ics)
    ic_tab[name] = ics
    print(f"  {name:14s} IC={fmt(m, cis, n, d=4)}")

# ── 프레임1b: 이벤트형 — 조건부 평균초과 (임원/이사 매수 有 vs 유니버스 EW) ──
print("\n== 프레임1b: 조건부 평균 초과수익 (%p/20d, 그룹평균 − 유니버스 EW)")
def cond_excess(cond_fn, label):
    xs, ns = [], []
    for t8, idx, fwd, ms, ms_mc, F in cache:
        g = cond_fn(F)
        g = g[g].index
        if len(g) < 10: xs.append(np.nan); ns.append(0); continue
        xs.append((fwd[g].mean() - fwd.mean()) * 100); ns.append(len(g))
    cis, m, n = block_boot_ci(xs)
    print(f"  {label:40s} {fmt(m, cis, n, unit='%p', d=2)} 평균종목수={np.nanmean([v for v in ns if v>0]):.0f}")
    return xs
cond_excess(lambda F: F.ins_od_nbuy >= 1, "임원/이사 매수 ≥1명 (90d)")
cond_excess(lambda F: F.ins_od_nbuy >= 2, "임원/이사 매수 ≥2명 = 클러스터 (90d)")
cond_excess(lambda F: F.ins_od_nbuy >= 3, "임원/이사 매수 ≥3명 (90d)")
cond_excess(lambda F: (F.ins_od_nbuy >= 2) & (F.ins_od_npr > 0.5), "클러스터 & 순매수비>0.5")
cond_excess(lambda F: (F.ins_all_nbuy >= 2) & (F.ins_od_nbuy == 0), "대조: 비임원(10%주주 등)만 ≥2명 매수")
cond_excess(lambda F: F.earn_pred == 1, "예상 실적발표 h20 창 내 (전년동창 규칙)")
cond_excess(lambda F: F.earn_pred == 0, "예상 실적발표 없음")

# ── 프레임2: us_mus_v0 top50 짝비교 ──────────────────────────────────────
print("\n== 프레임2: us_mus_v0 top50 변형 짝비교 (Δ = 변형 − base, %p/20d)")
def pair(label, pick_fn):
    diffs, ov, base_x = [], [], []
    for t8, idx, fwd, ms, ms_mc, F in cache:
        t_old = ms.sort_values(ascending=False).index[:50]
        t_new = pick_fn(ms, ms_mc, F)
        if t_new is None or len(t_new) < 30: diffs.append(np.nan); ov.append(np.nan); continue
        diffs.append((fwd[t_new].mean() - fwd[t_old].mean()) * 100)
        ov.append(len(set(t_new) & set(t_old)) / 50)
    cis, m, n = block_boot_ci(diffs)
    print(f"  {label:44s} Δ={fmt(m, cis, n, unit='%p', d=3)} overlap={np.nanmean(ov):.0%}")
    return diffs

def tilt(ms, F, col, w=1.0):
    rk = F[col].rank(pct=True).reindex(ms.index).fillna(0.5)
    return (ms + w * rk).sort_values(ascending=False).index[:50]
def tilt_binary(ms, F, col):
    rk = F[col].reindex(ms.index).fillna(0)  # 0/1 항: 매수 있으면 +1
    return (ms + rk).sort_values(ascending=False).index[:50]
def excl(ms, F, mask):
    ok = ~mask.reindex(ms.index).fillna(False).astype(bool)
    return ms[ok].sort_values(ascending=False).index[:50]

res = {}
res["ins_od_nbuy_tilt"] = pair("+임원/이사 매수자수 4번째 항(순위)", lambda ms, mm, F: tilt(ms, F, "ins_od_nbuy"))
res["ins_od_npr_tilt"]  = pair("+임원/이사 순매수비 4번째 항", lambda ms, mm, F: tilt(ms, F, "ins_od_npr"))
res["ins_cluster_bin"]  = pair("+클러스터 매수(≥2명) 이진 가산 +1", lambda ms, mm, F: tilt_binary(ms, F.assign(cl=(F.ins_od_nbuy>=2).astype(int)), "cl"))
res["ins_sell_excl"]    = pair("임원/이사 순매도(npr<−0.5) 종목 제외", lambda ms, mm, F: excl(ms, F, F.ins_od_npr < -0.5))
res["earn_excl"]        = pair("예상 발표 h20 내 종목 제외", lambda ms, mm, F: excl(ms, F, F.earn_pred == 1))
res["earn_tilt"]        = pair("+예상 발표 h20 내 이진 가산 +1", lambda ms, mm, F: tilt_binary(ms, F, "earn_pred"))
res["size_mcap"]        = pair("size_amt → 실시총(log mcap) 교체", lambda ms, mm, F: mm.sort_values(ascending=False).index[:50])

# base top50 초과 (맥락)
print("\n== 맥락: base top50 − 유니버스 EW (%p/20d)")
bx = []
for t8, idx, fwd, ms, ms_mc, F in cache:
    t_old = ms.sort_values(ascending=False).index[:50]
    bx.append((fwd[t_old].mean() - fwd.mean()) * 100)
cis, m, n = block_boot_ci(bx)
print(f"  base top50 초과 {fmt(m, cis, n, unit='%p', d=2)} 적중={np.mean(np.array(bx)>0):.0%}")

# ── 프레임D: 레짐별 base 초과 ─────────────────────────────────────────────
print("\n== 프레임D: 레짐별 base top50 초과 (기록만, n 작음)")
reg = []
for (t8, *_), x in zip(cache, bx):
    s = spx.get(t8, np.nan); m200 = spx200.get(t8, np.nan); v = vix.get(t8, np.nan)
    # 24개월 지수수익 (거래일 504 → 데이터 부족 시 가능한 만큼)
    pos = spx.index.get_loc(t8) if t8 in spx.index else None
    r24 = (spx.iloc[pos] / spx.iloc[max(0, pos-504)] - 1) if pos is not None and pos >= 250 else np.nan
    reg.append(dict(anchor=t8, excess=x, spx_above_200=int(s > m200) if np.isfinite(m200) else np.nan,
                    vix=v, r24=r24))
R = pd.DataFrame(reg)
for lab, msk in [("SPX>200MA", R.spx_above_200 == 1), ("SPX≤200MA", R.spx_above_200 == 0),
                 ("VIX<20", R.vix < 20), ("VIX≥20", R.vix >= 20)]:
    x = R.excess[msk]
    print(f"  {lab:10s} n={len(x):3d} mean={x.mean():+.2f}%p 적중={np.mean(x>0):.0%} sd={x.std(ddof=1):.2f}")
print("  (SPX 24개월 수익 부호는 데이터 3년이라 전 앵커 양수 — 베어 레짐 표본 없음)" if (R.r24.dropna() > 0).all() else "")

# ── 프레임E: 앵커 시기 분할 — 플래그 원천별 (2024 앵커는 프록시 의존도 높음) ──
print("\n== 프레임E: 클러스터 조건부 초과, 앵커 연도별 (프록시 정직성 확인)")
for yr in ["2024", "2025", "2026"]:
    xs = []
    for t8, idx, fwd, ms, ms_mc, F in cache:
        if not t8.startswith(yr): continue
        g = F.index[F.ins_od_nbuy >= 2]
        xs.append((fwd[g].mean() - fwd.mean()) * 100 if len(g) >= 10 else np.nan)
    cis, m, n = block_boot_ci(xs, alpha_list=(0.05,))
    lo, hi = cis[0.05]
    print(f"  {yr}: {m:+.2f}%p 95%[{lo:+.2f},{hi:+.2f}] n={n}")

out = pd.DataFrame({"anchor": [c[0] for c in cache], "base_excess": bx, **ic_tab,
                    **{f"d_{k}": v for k, v in res.items()}})
out = out.merge(R[["anchor", "spx_above_200", "vix"]], on="anchor")
out.to_csv("us_orth_scan_frame.csv", index=False)
log("done.")
