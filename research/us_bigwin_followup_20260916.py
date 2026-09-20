# -*- coding: utf-8 -*-
"""
us_bigwin_followup_20260916.py — 본 스캔(us_bigwin_scan_20260916.py) 후속: 매출성장(rev_g) 증분의 선택효과 통제·강건성
                                 + 큰 승자 그룹의 '복권형 vs 기대수익 우위' 분리표
=================================================================================================
재료: us_bigwin_frames.pkl (본 스캔이 저장한 앵커별 피처·전방수익 프레임 109개, 20240617→20260813).
질문:
  A. 본 스캔 F2 의 "+rev_g 4번째 항 Δ +0.64%p [+0.06,+1.25]*" 가 진짜 신호인가, 아니면 rev_g 가 있는 종목
     (XBRL 매출 보고·커버 59%)만 남는 선택효과인가 → base 를 rev_g 보유 유니버스로 제한한 대조군과 짝비교.
  B. 게이트 방식(rev_g 상위 20/30/50% 안에서 base top50)·구성 수(gm top20/30/50)·유동성 상위半 제한 변형.
  C. 연도별·h60 지속성(블록 12).
  D. F1 그룹별 60일 EW 대비 초과수익 CI (블록 12) — W60·L60 배율과 나란히 놓아 복권형(양쪽 확률만 커짐)과
     기대수익 우위(평균도 높음)를 가른다.
정직성: 본 스캔과 동일(in-sample·생존편향 미보정·상승장 편중·비용 0). 짝비교 검정 수 N 을 세어 Bonferroni 병기.
실행: cwd 에 us_bigwin_frames.pkl 이 있어야 함. 출력: 콘솔 + us_bigwin_followup.csv
"""
import time, numpy as np, pandas as pd
RNG = np.random.default_rng(20260916); NBOOT = 5000
T0 = time.time()
def log(*a): print(f"[{time.time()-T0:5.0f}s]", *a, flush=True)

P = pd.read_pickle("us_bigwin_frames.pkl")
FR, anchors, ds, uni_rec, spx_rec = P["FR"], P["anchors"], P["ds"], P["uni"], P["spx"]
adates = [ds[i] for i in anchors]; yrs = np.array([d[:4] for d in adates])
log("frames", len(FR))

def rk(s): return s.rank(pct=True)
def ranksum(F, cols):
    sc = None
    for c in cols:
        r = rk(F[c]); sc = r if sc is None else sc + r.fillna(0.5)
    return sc.where(F[cols[0]].notna()).dropna()
def bb_mat(X, block, alphas=(0.05,)):
    X = np.asarray(X, float); k, n = X.shape
    nblk = int(np.ceil(n / block)); st = RNG.integers(0, n, (NBOOT, nblk))
    sel = ((st[:, :, None] + np.arange(block)[None, None, :]).reshape(NBOOT, -1) % n)[:, :n]
    means = np.empty((k, NBOOT))
    for r in range(k):
        x = X[r]; ok = np.isfinite(x)
        if ok.sum() < 8: means[r] = np.nan; continue
        means[r] = np.nanmean(np.where(ok, x, np.nan)[sel], axis=1)
    out = {a: (np.nanquantile(means, a/2, axis=1), np.nanquantile(means, 1-a/2, axis=1)) for a in alphas}
    return np.nanmean(X, axis=1), out, np.isfinite(X).sum(axis=1)

# ── 모델 정의 (전부 동일가중 순위합 · 문턱은 분위수만)
def base(F, n=50):            return F.base_sc.sort_values(ascending=False).index[:n]
def base_cov(F, n=50):        return F.base_sc[F.rev_g.notna()].sort_values(ascending=False).index[:n]        # 대조군: rev_g 보유 종목만
def base_plus_revg(F, n=50):  return ranksum(F, ["mom12", "upratio63", "size_amt", "rev_g"]).sort_values(ascending=False).index[:n]
def gm(F, n=50):              return ranksum(F, ["rev_g", "mom12", "upratio63"]).sort_values(ascending=False).index[:n]
def gate(q):
    def f(F, n=50):
        g = F[F.rev_g.rank(pct=True) > 1 - q]; return g.base_sc.sort_values(ascending=False).index[:n]
    return f
def gm_liq(F, n=50):          # 유동성 상위半 안에서 gm
    g = F[F.size_amt >= F.size_amt.median()]; return ranksum(g, ["rev_g", "mom12", "upratio63"]).sort_values(ascending=False).index[:n]
def base_liq(F, n=50):
    g = F[F.size_amt >= F.size_amt.median()]; return g.base_sc.sort_values(ascending=False).index[:n]
def revg_only(F, n=50):       return F.rev_g.sort_values(ascending=False).index[:n]                         # 매출성장 단독 top50
def gm_wo_size(F, n=50):      return ranksum(F, ["rev_g", "mom12", "upratio63", "size_amt"]).sort_values(ascending=False).index[:n]  # = base+rev_g (동일) 자리표시 아님: 4항 순위합
MODELS = {
    "base": base, "base_cov(rev_g 보유만)": base_cov, "base+rev_g(4항)": base_plus_revg, "gm(rev_g+mom+up)": gm,
    "gate20(rev_g 상위20%→base50)": gate(0.2), "gate30": gate(0.3), "gate50": gate(0.5),
    "base_liq(상위半)": base_liq, "gm_liq(상위半)": gm_liq, "revg_only top50": revg_only,
}
SIZES = {"gm top20": (gm, 20), "gm top30": (gm, 30), "base top20": (base, 20), "base top30": (base, 30)}

rec20 = {k: [] for k in list(MODELS) + list(SIZES)}; rec60 = {k: [] for k in rec20}; sets = {k: [] for k in rec20}
for i in anchors:
    F = FR[i]
    for k, fn in MODELS.items():
        g = pd.Index(fn(F)); sets[k].append(set(g))
        rec20[k].append(F.f20[g].mean() if len(g) >= 10 else np.nan); rec60[k].append(F.f60[g].mean() if len(g) >= 10 else np.nan)
    for k, (fn, n) in SIZES.items():
        g = pd.Index(fn(F, n)); sets[k].append(set(g))
        rec20[k].append(F.f20[g].mean()); rec60[k].append(F.f60[g].mean())
for k in rec20: rec20[k] = np.array(rec20[k]); rec60[k] = np.array(rec60[k])
uni60 = np.array([FR[i].f60.mean() for i in anchors])
log("models done")

PAIRS = [("base+rev_g(4항)", "base"), ("base_cov(rev_g 보유만)", "base"), ("base+rev_g(4항)", "base_cov(rev_g 보유만)"),
         ("gm(rev_g+mom+up)", "base"), ("gm(rev_g+mom+up)", "base_cov(rev_g 보유만)"),
         ("gate20(rev_g 상위20%→base50)", "base_cov(rev_g 보유만)"), ("gate30", "base_cov(rev_g 보유만)"), ("gate50", "base_cov(rev_g 보유만)"),
         ("gm_liq(상위半)", "base_liq(상위半)"), ("revg_only top50", "base"), ("gm top20", "base top20"), ("gm top30", "base top30")]
NT = len(PAIRS); ab = 0.05 / NT
print("\n" + "═" * 100)
print(f"A/B. 짝비교 (Δ = 모델 − 대조, %p) · 검정 {NT}개 → Bonf α={ab:.4f} · h20 블록4 / h60 블록12")
D20 = np.array([(rec20[a] - rec20[b]) * 100 for a, b in PAIRS]); D60 = np.array([(rec60[a] - rec60[b]) * 100 for a, b in PAIRS])
m20, c20, n20 = bb_mat(D20, 4, (0.05, ab)); m60, c60, n60 = bb_mat(D60, 12, (0.05, ab))
print(f"  {'모델 − 대조':50s} {'Δh20':>7s} {'95%CI':>17s} {'Bonf':>4s} {'2024/2025/2026':>20s} | {'Δh60':>7s} {'95%CI':>17s} {'Bonf':>4s} {'overlap':>7s}")
rows = []
for g, (a, b) in enumerate(PAIRS):
    lo, hi = c20[0.05][0][g], c20[0.05][1][g]; lb, hb = c20[ab][0][g], c20[ab][1][g]
    s20 = "**" if (lb > 0 or hb < 0) else ("*" if (lo > 0 or hi < 0) else "")
    lo6, hi6 = c60[0.05][0][g], c60[0.05][1][g]; lb6, hb6 = c60[ab][0][g], c60[ab][1][g]
    s60 = "**" if (lb6 > 0 or hb6 < 0) else ("*" if (lo6 > 0 or hi6 < 0) else "")
    yr = "/".join(f"{np.nanmean(D20[g][yrs == y]):+.2f}" for y in ["2024", "2025", "2026"])
    ov = np.nanmean([len(x & y) / max(len(y), 1) for x, y in zip(sets[a], sets[b]) if x and y])
    print(f"  {a+' − '+b:50s} {m20[g]:+7.2f} [{lo:+6.2f},{hi:+6.2f}] {s20:4s} {yr:>20s} | {m60[g]:+7.2f} [{lo6:+6.2f},{hi6:+6.2f}] {s60:4s} {ov:7.0%}")
    rows.append(dict(model=a, control=b, d20=m20[g], lo20=lo, hi20=hi, lob20=lb, hib20=hb, d60=m60[g], lo60=lo6, hi60=hi6, overlap=ov, n=n20[g]))
pd.DataFrame(rows).to_csv("us_bigwin_followup.csv", index=False)

print("\n  (참고) 각 모델의 EW 대비 초과 h20 / h60 (%p, 95% CI) · 비중첩 체인 누적(h20 기준) · MDD")
EX20 = np.array([(rec20[k] - uni_rec) * 100 for k in rec20]); EX60 = np.array([(rec60[k] - uni60) * 100 for k in rec20])
m1, c1, _ = bb_mat(EX20, 4); m2, c2, _ = bb_mat(EX60, 12)
for g, k in enumerate(rec20):
    r = rec20[k]; cums, mdds = [], []
    for off in range(4):
        ch = r[off::4]; ch = ch[np.isfinite(ch)]; eq = np.cumprod(1 + ch); cums.append(eq[-1] - 1); mdds.append((eq / np.maximum.accumulate(eq) - 1).min())
    turns = [1 - len(x & y) / len(y) for x, y in zip(sets[k][:-4], sets[k][4:]) if x and y]
    print(f"  {k:30s} h20 {m1[g]:+6.2f} [{c1[0.05][0][g]:+6.2f},{c1[0.05][1][g]:+6.2f}]  h60 {m2[g]:+6.2f} [{c2[0.05][0][g]:+6.2f},{c2[0.05][1][g]:+6.2f}]  체인 {np.mean(cums)*100:+7.1f}% MDD {np.mean(mdds)*100:6.1f}%  교체 {np.nanmean(turns):4.0%}  적중 {np.nanmean(EX20[g]>0):4.0%}")
print(f"  {'(참고) SPX':30s} 체인 {np.mean([np.cumprod(1+spx_rec[o::4])[-1]-1 for o in range(4)])*100:+7.1f}%")

# ── rev_g 커버리지의 성격: 누가 빠지나 (앵커 평균)
print("\n  rev_g 결측 종목의 성격 (앵커 평균) — 선택효과 해석용")
rows = []
for i in anchors:
    F = FR[i]; m = F.rev_g.isna()
    rows.append(dict(cover=1 - m.mean(), f20_na=F.f20[m].mean(), f20_ok=F.f20[~m].mean(), size_na=F.size_amt[m].mean(), size_ok=F.size_amt[~m].mean(),
                     mcap_na_frac=F.mcap[m].notna().mean(), top50_na_frac=m[F.base_sc.sort_values(ascending=False).index[:50]].mean()))
X = pd.DataFrame(rows).mean()
print(f"  커버 {X.cover:.0%} · 결측군 20일수익 {X.f20_na*100:+.2f}% vs 보유군 {X.f20_ok*100:+.2f}% · 거래대금 log10 결측 {X.size_na:.2f} vs 보유 {X.size_ok:.2f} · 결측군 중 XBRL 주식수 보유 {X.mcap_na_frac:.0%} · base top50 중 rev_g 결측 {X.top50_na_frac:.0%}")

# ── D. 복권형 vs 기대수익 우위: 그룹별 60일 EW 대비 초과 (블록12) + W60/L60 배율
print("\n" + "═" * 100)
QF = ["mom12", "mom1", "upratio63", "size_amt", "rv63", "prox52", "vol_cv", "price", "mcap", "bm", "roa",
      "rev_g", "rev_acc", "sp", "opm", "asset_g", "sue", "ins_npr", "dtc", "svr20", "days_since_ea"]
groups = []
for f in QF:
    groups.append((f"{f} Q5", lambda F, f=f: F[f].rank(pct=True) > 0.8)); groups.append((f"{f} Q1", lambda F, f=f: F[f].rank(pct=True) <= 0.2))
for f in ["ins_clu", "ipo1y", "ma200_up", "sp500_now"]: groups.append((f"{f}=1", lambda F, f=f: F[f] == 1))
groups += [("base top50", lambda F: pd.Series(F.index.isin(base(F)), index=F.index)), ("base top10", lambda F: pd.Series(F.index.isin(base(F, 10)), index=F.index)),
           ("gm top50", lambda F: pd.Series(F.index.isin(gm(F)), index=F.index)),
           ("rv63 Q5 ∩ mom12 Q5", lambda F: (F.rv63.rank(pct=True) > 0.8) & (F.mom12.rank(pct=True) > 0.8)),
           ("rev_g Q5 ∩ mom12 Q5", lambda F: (F.rev_g.rank(pct=True) > 0.8) & (F.mom12.rank(pct=True) > 0.8)),
           ("rev_g Q5 ∩ upratio63 Q5", lambda F: (F.rev_g.rank(pct=True) > 0.8) & (F.upratio63.rank(pct=True) > 0.8)),
           ("rev_g Q5 ∩ opm Q5", lambda F: (F.rev_g.rank(pct=True) > 0.8) & (F.opm.rank(pct=True) > 0.8)),
           ("mom12 Q5 ∩ sp500_now", lambda F: (F.mom12.rank(pct=True) > 0.8) & (F.sp500_now == 1)),
           ("size_amt Q1 ∩ mom12 Q5", lambda F: (F.size_amt.rank(pct=True) <= 0.2) & (F.mom12.rank(pct=True) > 0.8))]
NG = len(groups); abg = 0.05 / NG
print(f"D. 그룹별 60일 EW 대비 초과수익 (%p/60d · 블록12) 와 W60(+50%)·L60(−40%) 배율 · 그룹 {NG}개 → Bonf α={abg:.4f}")
ex_rows, w_rows, l_rows, n_rows = [], [], [], []
for i in anchors:
    F = FR[i]; y = F.f60
    if y.notna().mean() < 0.5: continue
    ub = y.mean(); wb = (y >= 0.5).mean(); lb_ = (y <= -0.4).mean()
    ex, w, l, nn = [], [], [], []
    for lab, fn in groups:
        m = fn(F).fillna(False) & y.notna()
        if m.sum() < 10: ex.append(np.nan); w.append(np.nan); l.append(np.nan); nn.append(0); continue
        ex.append((y[m].mean() - ub) * 100); w.append((y[m] >= 0.5).mean() / wb); l.append((y[m] <= -0.4).mean() / lb_); nn.append(m.sum())
    ex_rows.append(ex); w_rows.append(w); l_rows.append(l); n_rows.append(nn)
EXG = np.array(ex_rows).T; W = np.nanmean(np.array(w_rows).T, axis=1); L = np.nanmean(np.array(l_rows).T, axis=1); NN = np.nanmean(np.array(n_rows).T, axis=1)
m, cis, n = bb_mat(EXG, 12, (0.05, abg))
order = np.argsort(-np.nan_to_num(m, nan=-99))
print(f"  {'그룹':28s} {'초과60d':>8s} {'95%CI':>17s} {'Bonf':>4s} {'W60배율':>7s} {'L60배율':>7s} {'W/L':>5s} {'평균n':>6s}  판독")
d_rows = []
for g in order:
    if not np.isfinite(m[g]): continue
    lo, hi = cis[0.05][0][g], cis[0.05][1][g]; lb, hb = cis[abg][0][g], cis[abg][1][g]
    star = "**" if (lb > 0 or hb < 0) else ("*" if (lo > 0 or hi < 0) else "")
    verdict = ("기대수익 우위" if (lo > 0 and W[g] > 1.2 and L[g] < 1.2) else "복권형(양쪽 꼬리↑)" if (W[g] > 1.3 and L[g] > 1.3) else
               "방어형(양쪽 꼬리↓)" if (W[g] < 0.8 and L[g] < 0.8) else "열위" if hi < 0 else "")
    print(f"  {groups[g][0]:28s} {m[g]:+8.2f} [{lo:+6.2f},{hi:+6.2f}] {star:4s} {W[g]:7.2f} {L[g]:7.2f} {W[g]/L[g]:5.2f} {NN[g]:6.0f}  {verdict}")
    d_rows.append(dict(group=groups[g][0], ex60=m[g], lo95=lo, hi95=hi, lob=lb, hib=hb, w60_ratio=W[g], l60_ratio=L[g], n_avg=NN[g], verdict=verdict))
pd.DataFrame(d_rows).to_csv("us_bigwin_followup_groups.csv", index=False)
log("done")
