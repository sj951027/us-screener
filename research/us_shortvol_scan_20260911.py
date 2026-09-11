# -*- coding: utf-8 -*-
"""
us_shortvol_scan_20260911.py — FINRA 일별 공매도 거래량 첫 스캔 (in-sample, 관측 전용)
=====================================================================
재료: us_shortvol.db short_volume_daily (Reg SHO daily, 2023-05-01 ~ 2025-07-02 적재분 = 545거래일).
  svr(D) = short_vol / total_vol — '그날 체결 중 공매도 비율'. 격주 잔고(short_interest, dtc)와 다른 정보.
질문 (DESIGN_us_shortvol_collector_20260906.md 스캔 계획 그대로):
  svr5·svr20(비율 5·20일 평균)·Δsvr(svr5 − svr20) 의 전 유니버스 day-IC h20(사전 부호 −: 문헌
  Boehmer-Jones-Zhang 계열 — 공매도 비율 높으면 이후 단기 부진) · 십분위 · us_mus_v0 top50 4번째 항/제외 짝비교.
정직성:
- PIT: 거래일 D 파일은 D+1 이후 앵커에서만 사용(게시 시각 18:00 ET) → 앵커 i 의 피처는 ds[i-1] 까지.
- in-sample · 생존편향 미보정(현재 시세 DB 심볼) · 종가체결 · 비용 0.
- 주간앵커 h20 중첩 → 블록 부트스트랩(블록=4, 10,000회). 신규 검정 6개(IC 3 + 짝비교 3) → Bonferroni α=0.05/6.
- 앵커 범위가 적재분에 갇혀 n≈60(짝비교)·≈95(IC) — 8/30·9/06 스캔(n=108~114)보다 작다. 결과는 '기움'이지 채택 아님.
실행: cwd 에 us-screener-data/ 가 있어야 함. 출력: 콘솔 + us_shortvol_scan_frame.csv
"""
import sqlite3, sys, time
import numpy as np, pandas as pd
from scipy.stats import spearmanr

DB  = "us-screener-data/us_ohlcv.db"
SDB = "us-screener-data/us_shortvol.db"
RNG = np.random.default_rng(20260911)
NBOOT = 10000
NTEST = 6
ALPHAS = (0.05, 0.05 / NTEST)
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

sc = sqlite3.connect(f"file:{SDB}?mode=ro", uri=True)
sv = pd.read_sql("select date, symbol, short_vol, total_vol from short_volume_daily where total_vol > 0", sc)
sv["svr"] = sv.short_vol / sv.total_vol
SVR = sv.pivot(index="symbol", columns="date", values="svr").reindex(C.index)
SVR = SVR.reindex(columns=[d for d in ds if d in SVR.columns])
sv_dates = list(SVR.columns)
log("shortvol", SVR.shape, sv_dates[0], sv_dates[-1], f"svr 중앙값 {np.nanmedian(SVR.values):.3f}")
del sv

# ── 공통 프레임 (us_orth_scan_20260906 과 동일 프로토콜) ─────────────────
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
    s = None; core = None
    for j, f in enumerate(["mom12", "upratio63", "size_amt"]):
        rk = F[f].rank(pct=True)
        if j == 0: core = rk.notna()
        s = rk if s is None else s + rk.fillna(0.5)
    return s.where(core).dropna()

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

# ── 피처: 앵커 i 에서 ds[i-1] 까지의 svr (PIT D+1) ────────────────────────
def sv_feats(i, idx):
    end = ds[i-1]                                  # D 파일은 D+1 앵커부터
    cols20 = [d for d in ds[i-20:i] if d in SVR.columns]
    cols5 = cols20[-5:]
    F = pd.DataFrame(index=idx)
    if len(cols20) < 15 or len(cols5) < 4:
        F["svr5"] = np.nan; F["svr20"] = np.nan; F["dsvr"] = np.nan
        return F
    w20 = SVR.loc[idx, cols20]; w5 = SVR.loc[idx, cols5]
    F["svr20"] = w20.mean(axis=1).where(w20.notna().sum(axis=1) >= 15)
    F["svr5"] = w5.mean(axis=1).where(w5.notna().sum(axis=1) >= 4)
    F["dsvr"] = F.svr5 - F.svr20
    return F

# 앵커: 주간(5거래일), h20 이 적재분 안에 들어오도록. IC 는 svr20 만 있으면 되므로 i≥80,
#       짝비교(mus_score 는 252일 필요)는 i≥252.
last_sv = sv_dates[-1]
i_last = max(i for i in range(len(ds)) if ds[i] <= last_sv) - 20
i_first = max(80, ds.index(sv_dates[0]) + 21)
anchors_ic = list(range(i_first, i_last + 1, 5))
anchors_pair = [i for i in anchors_ic if i >= 252]
log("anchors IC:", len(anchors_ic), ds[anchors_ic[0]], "→", ds[anchors_ic[-1]],
    "| pair:", len(anchors_pair), ds[anchors_pair[0]] if anchors_pair else None)

cache = []
for k, i in enumerate(anchors_ic):
    t8 = ds[i]
    idx, amt20 = guard_universe(i)
    fwd = (C.loc[idx, ds[i+20]] / C.loc[idx, ds[i]] - 1)
    F = sv_feats(i, idx)
    ms = mus_score(i, idx, amt20) if i >= 252 else None
    cache.append((t8, i, idx, fwd, ms, F))
    if k % 20 == 0: log(f"  anchor {k}/{len(anchors_ic)} {t8} univ={len(idx)} svr5_cover={F.svr5.notna().mean():.0%}")

print("\n== 커버리지 (가드 유니버스 내 svr5 유효 비율, 앵커 평균)")
print(f"  {np.mean([F.svr5.notna().mean() for *_, F in cache]):.1%}")

# ── 프레임1: 전 유니버스 day-IC h20 (사전 부호 −) ──────────────────────────
print("\n== 프레임1: 전 유니버스 day-IC h20 (스피어만) — 사전 부호 −")
ic_tab = {}
for name in ["svr5", "svr20", "dsvr"]:
    ics = []
    for t8, i, idx, fwd, ms, F in cache:
        f = F[name]; m = f.notna() & fwd.notna()
        ics.append(spearmanr(f[m], fwd[m]).statistic if (m.sum() > 100 and f[m].nunique() > 1) else np.nan)
    cis, m, n = block_boot_ci(ics)
    ic_tab[name] = ics
    print(f"  {name:6s} IC={fmt(m, cis, n, d=4)} 부호일치(−)={np.mean(np.array([v for v in ics if np.isfinite(v)])<0):.0%}")

# ── 프레임1b: 십분위 초과 (D1=svr 최저 … D10=최고, 그룹 EW − 유니버스 EW) ──
print("\n== 프레임1b: svr5 십분위 초과수익 (%p/20d, 기록용)")
dec = {q: [] for q in range(1, 11)}
for t8, i, idx, fwd, ms, F in cache:
    f = F.svr5.dropna(); f = f[fwd.reindex(f.index).notna()]
    if len(f) < 200:
        for q in dec: dec[q].append(np.nan)
        continue
    r = pd.qcut(f.rank(method="first"), 10, labels=False) + 1
    base = fwd.mean()
    for q in dec:
        g = r.index[r == q]
        dec[q].append((fwd[g].mean() - base) * 100)
for q in dec:
    x = np.array([v for v in dec[q] if np.isfinite(v)])
    print(f"  D{q:<2d} mean={x.mean():+.2f}%p 적중(>0)={np.mean(x>0):.0%} n={len(x)}")

# ── 프레임2: us_mus_v0 top50 짝비교 ──────────────────────────────────────
print("\n== 프레임2: us_mus_v0 top50 변형 짝비교 (Δ = 변형 − base, %p/20d)")
def pair(label, pick_fn):
    diffs, ov = [], []
    for t8, i, idx, fwd, ms, F in cache:
        if ms is None: continue
        t_old = ms.sort_values(ascending=False).index[:50]
        t_new = pick_fn(ms, F)
        if t_new is None or len(t_new) < 30: diffs.append(np.nan); ov.append(np.nan); continue
        diffs.append((fwd[t_new].mean() - fwd[t_old].mean()) * 100)
        ov.append(len(set(t_new) & set(t_old)) / 50)
    cis, m, n = block_boot_ci(diffs)
    print(f"  {label:44s} Δ={fmt(m, cis, n, unit='%p', d=3)} overlap={np.nanmean(ov):.0%}")
    return diffs

def tilt_low(ms, F, col):        # 낮은 svr 을 선호(사전 부호 −) → (1 − 순위) 를 4번째 항으로
    rk = (1 - F[col].rank(pct=True)).reindex(ms.index).fillna(0.5)
    return (ms + rk).sort_values(ascending=False).index[:50]
def excl_top(ms, F, col, q=0.9):  # 상위 10% svr 제외
    thr = F[col].quantile(q)
    ok = ~(F[col].reindex(ms.index) > thr).fillna(False)
    return ms[ok].sort_values(ascending=False).index[:50]

res = {}
res["svr5_tilt"] = pair("+저svr5 4번째 항(순위)", lambda ms, F: tilt_low(ms, F, "svr5"))
res["dsvr_tilt"] = pair("+저Δsvr 4번째 항(순위)", lambda ms, F: tilt_low(ms, F, "dsvr"))
res["svr5_excl"] = pair("svr5 상위 10% 제외", lambda ms, F: excl_top(ms, F, "svr5"))

print("\n== 맥락: base top50 − 유니버스 EW (%p/20d, 같은 앵커)")
bx = []
for t8, i, idx, fwd, ms, F in cache:
    if ms is None: continue
    t_old = ms.sort_values(ascending=False).index[:50]
    bx.append((fwd[t_old].mean() - fwd.mean()) * 100)
cis, m, n = block_boot_ci(bx)
print(f"  base top50 초과 {fmt(m, cis, n, unit='%p', d=2)} 적중={np.mean(np.array(bx)>0):.0%}")

# 참고: top50 안에서 svr5 분포 (신호가 top50 에 얼마나 걸리는지)
print("\n== 참고: base top50 의 svr5 vs 유니버스 (앵커 평균)")
a, b = [], []
for t8, i, idx, fwd, ms, F in cache:
    if ms is None: continue
    t_old = ms.sort_values(ascending=False).index[:50]
    a.append(F.svr5.reindex(t_old).mean()); b.append(F.svr5.mean())
print(f"  top50 svr5 평균 {np.nanmean(a):.3f} · 유니버스 {np.nanmean(b):.3f}")

out = pd.DataFrame({"anchor": [c[0] for c in cache], **ic_tab})
pair_df = pd.DataFrame({"anchor": [c[0] for c in cache if c[4] is not None], "base_excess": bx,
                        **{f"d_{k}": v for k, v in res.items()}})
out = out.merge(pair_df, on="anchor", how="left")
out.to_csv("us_shortvol_scan_frame.csv", index=False)
log("done.")
