# -*- coding: utf-8 -*-
"""
us_exit_scan_20260809.py — 매도(청산) 규칙 스캔
==============================================================================
질문: us_mus_v0 top50 을 샀다면 "어떻게 팔았을 때" h20 고정 보유 대비 나았나.
프레임1(20d 창): 익절/손절/트레일링/급등익일/순위이탈 — 전부 20거래일 상한.
프레임2(60d 창): 보유 연장(h40/h60) + 순위유지 보유(이탈 시 매도, 60d 상한).

정직성 명시:
- in-sample · 생존편향 미보정(2026-07-10 백필은 당시 상장종목 기준) · 다중검정
  (프레임1 13규칙 → Bonferroni α=0.05/13). 결과는 '기움'이지 채택이 아님.
- 종가 기준 트리거·종가 체결(장중/갭 미반영) — 실제 체결은 이보다 불리할 수 있음.
- 규칙당 왕복 1회로 동일 → 프레임1 내 비교에서 비용 상쇄. 프레임2는 보유일 상이.
- 선정 로직·가드는 research/us_factor_scan_v0.py 의 combo 'mom+up+size'(=us_mus_v0)
  를 그대로 재현(매직넘버 재사용, 신규 도입 없음).

사용: US_OHLCV_DB=<경로> python us_exit_scan_20260809.py [--out DIR]
"""
import os, sys, sqlite3
import numpy as np, pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = os.environ.get("US_OHLCV_DB", "/tmp/us-screener-data/us_ohlcv.db")
OUT = sys.argv[sys.argv.index("--out")+1] if "--out" in sys.argv else os.path.dirname(os.path.abspath(__file__))

# ── 패널 ─────────────────────────────────────────────────────────────────────
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
raw = pd.read_sql("SELECT symbol,date,close,adj_close,volume FROM daily_ohlcv", con)
for c in ("close", "adj_close", "volume"):
    raw[c] = pd.to_numeric(raw[c], errors="coerce")
piv = lambda v: raw.pivot_table(index="symbol", columns="date", values=v, aggfunc="last").sort_index(axis=1)
C, RAWC, V = piv("adj_close"), piv("close"), piv("volume")
del raw
dates = list(C.columns)
print(f"패널: {C.shape[0]}종목 × {len(dates)}일 ({dates[0]}~{dates[-1]})")

# ── 팩터(전 날짜, 원 스캔과 동일 정의) ────────────────────────────────────────
CT = C.T.astype("float64")                      # 날짜×심볼
RT = CT.pct_change(fill_method=None)
AMTT = (RAWC.T * V.T).astype("float64")
up63 = (RT > 0).rolling(63).sum() / RT.notna().rolling(63).sum()
rv21 = RT.rolling(21).std(ddof=1)
zr21 = (RT == 0).rolling(21).sum() / RT.notna().rolling(21).sum()
amt20 = AMTT.rolling(20).mean()
mom12 = CT.shift(21) / CT.shift(252) - 1
size_amt = np.log10(amt20.where(amt20 > 0))
RAWT = RAWC.T.astype("float64")

def universe_and_score(i):
    """원 스캔 가드 + us_mus_v0 점수. i=날짜 인덱스. (uni, score) 반환."""
    d = dates[i]
    ok = (rv21.loc[d] >= 0.003) & (zr21.loc[d] <= 0.5) & (RAWT.loc[d] >= 5.0) & (amt20.loc[d] >= 1e6)
    uni = ok[ok].index
    rk_m = mom12.loc[d].reindex(uni).rank(pct=True, ascending=True)
    rk_u = up63.loc[d].reindex(uni).rank(pct=True, ascending=True)
    rk_s = size_amt.loc[d].reindex(uni).rank(pct=True, ascending=True)
    score = (rk_m + rk_u.fillna(0.5) + rk_s.fillna(0.5)).where(rk_m.notna())
    return uni, score.dropna()

# 주간 평가 그리드(순위 이탈 판정용): 5거래일 간격
EVAL_STEP = 5
eval_idx = list(range(273, len(dates), EVAL_STEP))
rank_at = {}                                     # i -> Series(symbol -> 순위 1=최상)
for i in eval_idx:
    _, sc = universe_and_score(i)
    rank_at[i] = sc.rank(ascending=False, method="first")
print(f"주간 평가 그리드 {len(eval_idx)}개 산출 완료")

# ── 청산 규칙 시뮬레이션 ─────────────────────────────────────────────────────
H1, H2 = 20, 60
def simulate(path, entry_i, sym):
    """path: 진입일 포함 종가 배열(idx0=진입 종가). 반환: {규칙: (수익%, 보유일)}"""
    cum = path / path[0] - 1.0
    n = len(path) - 1                            # 사용 가능한 후속 일수
    res = {}
    def first(cond_idx, cap):                    # cond_idx: 조건 만족 일자 리스트(1..)
        return min([j for j in cond_idx if j <= cap] or [cap])
    daily = np.diff(path) / path[:-1]
    runmax = np.maximum.accumulate(path)
    dd = path / runmax - 1.0
    cap1 = min(H1, n)
    # 프레임1 (cap 20d)
    res["h20"] = (cum[cap1], cap1)
    for tp in (0.10, 0.15, 0.20, 0.30):
        j = first([k for k in range(1, n+1) if cum[k] >= tp], cap1)
        res[f"TP{int(tp*100)}"] = (cum[j], j)
    for sl in (0.10, 0.15, 0.20):
        j = first([k for k in range(1, n+1) if cum[k] <= -sl], cap1)
        res[f"SL{int(sl*100)}"] = (cum[j], j)
    for tr in (0.10, 0.15):
        j = first([k for k in range(1, n+1) if dd[k] <= -tr], cap1)
        res[f"TR{int(tr*100)}"] = (cum[j], j)
    jt = first([k for k in range(1, n+1) if cum[k] >= 0.20], cap1)
    js = first([k for k in range(1, n+1) if cum[k] <= -0.10], cap1)
    j = min(jt, js)
    res["TP20_SL10"] = (cum[j], j)
    spikes = [k+1 for k in range(min(n, cap1)) if k < len(daily) and daily[k] >= 0.10]
    j = first([min(s+1, cap1) for s in spikes] or [cap1], cap1)
    res["SPIKE10"] = (cum[j], j)
    # 순위 이탈(주간 체크, cap1)
    checks1 = [k for k in eval_idx if entry_i < k <= entry_i + cap1]
    for thr in (100, 300):
        j = cap1
        for k in checks1:
            r = rank_at[k].get(sym, np.inf)
            if r > thr:
                j = k - entry_i; break
        res[f"RANK{thr}_20"] = (cum[j], j)
    # 프레임2 (cap 60d) — 경로가 60일 있을 때만
    if n >= H2:
        res["h40"] = (cum[40], 40)
        res["h60"] = (cum[60], 60)
        checks2 = [k for k in eval_idx if entry_i < k <= entry_i + H2]
        for thr in (100, 300):
            j = H2
            for k in checks2:
                r = rank_at[k].get(sym, np.inf)
                if r > thr:
                    j = k - entry_i; break
            res[f"RANK{thr}_60"] = (cum[j], j)
    return res

anchors = [i for i in eval_idx if i + H1 < len(dates)]
rows = []
for i in anchors:
    uni, sc = universe_and_score(i)
    seg = C.iloc[:, i:i+H2+1]
    rr = seg.pct_change(axis=1, fill_method=None)
    glitch = rr.abs().max(axis=1) > 1.0          # 데이터 결함 가드(원 스캔 동일)
    okf = C[dates[i+H1]].notna() & ~glitch
    cand = sc.index[sc.index.isin(okf[okf].index)]
    if len(cand) < 500:
        continue
    top50 = sc.reindex(cand).nlargest(50).index
    for sym in top50:
        p = seg.loc[sym].to_numpy(dtype="float64")
        p = p[~np.isnan(p)] if np.isnan(p).any() else p   # 중간 결측은 직전가 유지 대신 제거(보수적)
        if len(p) < H1 + 1:
            continue
        for rule, (r, hd) in simulate(p, i, sym).items():
            rows.append((dates[i], sym, rule, r * 100, hd))
df = pd.DataFrame(rows, columns=["date", "symbol", "rule", "ret", "hold"])
df.to_csv(os.path.join(OUT, "us_exit_scan_positions.csv"), index=False)

# ── 집계: 앵커별 평균 → h20 페어드 차이 + 부트스트랩 CI ──────────────────────
def summarize(frame_rules, base="h20"):
    g = df[df["rule"].isin(frame_rules + [base])].groupby(["date", "rule"])["ret"].mean().unstack()
    g = g.dropna(subset=[base])
    hold = df[df["rule"].isin(frame_rules + [base])].groupby("rule")["hold"].mean()
    trig = df[df["rule"].isin(frame_rules)].groupby("rule")["hold"].apply(lambda s: (s < s.max()).mean())
    out = []
    rng = np.random.default_rng(20260809)
    for rule in frame_rules:
        if rule not in g:
            continue
        diff = (g[rule] - g[base]).dropna().to_numpy()
        n = len(diff)
        bs = np.array([rng.choice(diff, n).mean() for _ in range(2000)])
        lo, hi = np.percentile(bs, [2.5, 97.5])
        out.append((rule, n, g[rule].mean(), diff.mean(), lo, hi, hold[rule], trig.get(rule, np.nan)))
    return pd.DataFrame(out, columns=["rule", "n앵커", "평균수익%", "vs기준%p", "CI_lo", "CI_hi", "평균보유일", "발동률"]).round(3)

f1 = ["TP10", "TP15", "TP20", "TP30", "SL10", "SL15", "SL20", "TR10", "TR15",
      "TP20_SL10", "SPIKE10", "RANK100_20", "RANK300_20"]
f2 = ["h40", "h60", "RANK100_60", "RANK300_60"]
s1 = summarize(f1, "h20")
s2 = summarize(f2, "h20")
base_mean = df[df["rule"] == "h20"].groupby("date")["ret"].mean()
print(f"\n기준 h20: 앵커 {len(base_mean)}개 · 평균 {base_mean.mean():+.2f}%/20d · 적중 {(base_mean>0).mean()*100:.0f}%")
print("\n== 프레임1: 20d 창 내 조기청산 vs h20 (페어드, 부트스트랩 95% CI) ==")
print(s1.to_string(index=False))
print("\n== 프레임2: 보유 연장 (60d 창 확보 앵커만) vs h20 ==")
print(s2.to_string(index=False))
s1.to_csv(os.path.join(OUT, "us_exit_scan_frame1.csv"), index=False)
s2.to_csv(os.path.join(OUT, "us_exit_scan_frame2.csv"), index=False)
print("\n주의: in-sample · 생존편향 미보정 · 13규칙 다중검정(Bonferroni α≈0.004) · 종가 체결 가정")
