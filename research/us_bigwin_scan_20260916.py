# -*- coding: utf-8 -*-
"""
us_bigwin_scan_20260916.py — "시장을 크게 이기는 종목"의 사전 특징 + 미탐색 직교 재료 스캔 (in-sample, 관측 전용)
=======================================================================================================
질문(사용자 2026-09-16: "시장을 많이 이기고 상승률이 높을 수 있는 모든 걸 연구"):
  F1. 큰 승자 해부 — 60거래일 +50% / 120거래일 +100% 를 달성한 종목은 사전에 어떤 관찰 가능한 특징이
      있었나. 가격·거래량·재무(XBRL)·내부자(진짜 플래그)·공매도(격주 잔고·일별 비율)·상장연차·업종을
      한 잣대(앵커별 조건부 확률 − 기준확률, 블록 부트스트랩)로 비교. 같은 특징의 −40% 급락 확률도 병기
      (복권형인지 기대수익 우위인지 구분).
  F2. 미탐색 직교 팩터 — 매출성장(분기 YoY·가속)·매출/시총·영업마진·자산성장(−)·상장 1년 미만: 전 유니버스
      day-IC h20 + us_mus_v0 top50 4번째 항 짝비교 (기존 스캔에서 다루지 않은 재료만).
  F3. 성장+추세 복합 아키텍처 — 새 재료를 넣은 고르기 방식 4개를 base 와 같은 잣대로 비교.
  F4. 레짐 오버레이 — base top50 의 초과·절대수익이 SPX 200MA·VIX·시장폭(유니버스 200MA 상회 비율)에 따라
      다른가. 비중첩 체인으로 '레짐 밖 현금' 타이밍 비교 (2025-04 급락 1회 포함 — 표본 극소, 관측만).
  F5. 라이브 관측 갱신 — score_daily 실기록(us_mus_v0 07-13~, us_rvdtc_a 07-17~) 일일 리밸런스 EW vs 가드 EW vs SPX.
잣대: 주간앵커(5거래일) · 종가체결 · 비용 0 · 초과 = 가드 유니버스 EW 대비 · 블록 부트스트랩(h20 블록 4 /
  h60 블록 12 / h120 블록 24, 5,000회) · 프레임별 Bonferroni(검정 수 명시).
정직성: in-sample · 생존편향 미보정(현재 시세 DB 심볼·상폐 미반영 → 급락 확률 과소 가능) · 상승장 편중
  (2024-03~2026-08) · sector_cache 는 현재 분류(PIT 아님) · S&P500 소속은 현재 스냅샷(PIT 아님, 참고만).
  XBRL 은 filed<앵커일(PIT), 분기값은 YTD 차분(start 컬럼 부재, M5). 내부자는 filed<앵커일, 구조화셋
  적재 2026-03-31 까지 → 2026 4~8월 앵커는 내부자 피처 결측 처리. 결과는 전부 '기움'이지 채택 아님.
실행: cwd 에 us-screener-data/ 가 있어야 함. 출력: 콘솔 + us_bigwin_*.csv
"""
import sqlite3, time, warnings
import numpy as np, pandas as pd
from scipy.stats import spearmanr
warnings.filterwarnings("ignore")

DB, FDB, MDB, SDB = ("us-screener-data/us_ohlcv.db", "us-screener-data/us_fundamentals.db",
                     "us-screener-data/us_market.db", "us-screener-data/us_shortvol.db")
RNG = np.random.default_rng(20260916)
NBOOT = 5000
T0 = time.time()
def log(*a): print(f"[{time.time()-T0:5.0f}s]", *a, flush=True)

# ───────────────────────── 데이터 적재
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
df = pd.read_sql("select symbol,date,close,adj_close,volume from daily_ohlcv", con)
C  = df.pivot(index="symbol", columns="date", values="adj_close")
RC = df.pivot(index="symbol", columns="date", values="close")
V  = df.pivot(index="symbol", columns="date", values="volume")
del df
# 휴장일 잔행 제외 (심볼 수 < 최근 최대의 10%)
cnt = C.notna().sum(); ds = [d for d in sorted(C.columns) if cnt[d] >= 0.1 * cnt.max()]
C, RC, V = C[ds], RC[ds], V[ds]
R = C.pct_change(axis=1, fill_method=None)
sec = pd.read_sql("select symbol, sector, industry from sector_cache", con).set_index("symbol")
first_date = C.notna().idxmax(axis=1)          # 심볼별 첫 시세일 (2023-03 이후 상장분만 의미)
log("ohlcv", C.shape, ds[0], ds[-1])

si = pd.read_sql("select settlement_date, symbol, days_to_cover from short_interest where days_to_cover is not null", con)
si = si[si.symbol.isin(C.index)]
DTC = si.pivot_table(index="symbol", columns="settlement_date", values="days_to_cover", aggfunc="last")
sdates = sorted(DTC.columns)
log("short_interest", DTC.shape)

sc_ = sqlite3.connect(f"file:{SDB}?mode=ro", uri=True)
sv = pd.read_sql("select date, symbol, short_vol, total_vol from short_volume_daily where total_vol > 0", sc_)
sv = sv[sv.symbol.isin(C.index)]
sv["svr"] = sv.short_vol / sv.total_vol
SVR = sv.pivot_table(index="symbol", columns="date", values="svr", aggfunc="mean").reindex(index=C.index, columns=ds)
del sv
log("shortvol", SVR.shape)

mc = sqlite3.connect(f"file:{MDB}?mode=ro", uri=True)
mk = pd.read_sql("select series, date, close from market_daily", mc)
spx = mk[mk.series == "SPX"].set_index("date").close.reindex(ds).ffill()
vix = mk[mk.series == "VIX"].set_index("date").close.reindex(ds).ffill()
spx_r = spx.pct_change()
spx_ma200 = spx.rolling(200, min_periods=150).mean()

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

xb = pd.read_sql("""select cik, tag, end, val, fy, fp, form, filed from xbrl_facts
    where tag in ('EntityCommonStockSharesOutstanding','CommonStockSharesOutstanding','StockholdersEquity',
    'NetIncomeLoss','Assets','OperatingIncomeLoss','Revenues','RevenueFromContractWithCustomerExcludingAssessedTax',
    'SalesRevenueNet','EarningsPerShareDiluted','EarningsPerShareBasic')""", fc)
xb["filed8"] = xb.filed.str.replace("-", "", regex=False)
xb["end8"] = xb["end"].str.replace("-", "", regex=False)
xb = xb.sort_values("filed8")
sh = xb[xb.tag.isin(["EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"]) & (xb.val > 0)]
sh = sh.assign(pri=(sh.tag != "EntityCommonStockSharesOutstanding").astype(int)).sort_values(["filed8", "pri"])
SH = expand(sh, "val")
EQ = expand(xb[xb.tag == "StockholdersEquity"], "val")
NI = expand(xb[(xb.tag == "NetIncomeLoss") & (xb.fp == "FY") & xb.form.str.startswith("10-K")], "val")

# 분기 시계열(YTD 차분): 매출·영업이익. 같은 (cik,end8,fp) 는 최초 공시(filed 최소)만 = PIT
def quarterly(tags, form_prefix=("10-Q", "10-K")):
    e = xb[xb.tag.isin(tags) & xb.form.str.startswith(form_prefix) & xb.fp.isin(["Q1", "Q2", "Q3", "FY"])].copy()
    e["pri"] = e.tag.map({t: k for k, t in enumerate(tags)})
    e = e.sort_values(["filed8", "pri"]).drop_duplicates(["cik", "end8", "fp"], keep="first")
    e["qn"] = e.fp.map({"Q1": 1, "Q2": 2, "Q3": 3, "FY": 4})
    rows = []
    for (cik, fy), g in e.groupby(["cik", "fy"]):
        g = g.sort_values("qn").drop_duplicates("qn", keep="first")
        ytd = dict(zip(g.qn, g.val)); meta = {q: (en, f) for q, en, f in zip(g.qn, g.end8, g.filed8)}
        for qn in g.qn:
            if qn == 1: q = ytd[1]
            elif (qn - 1) in ytd: q = ytd[qn] - ytd[qn - 1]
            else: continue
            rows.append((cik, fy, qn, *meta[qn], q, ytd[qn]))
    return pd.DataFrame(rows, columns=["cik", "fy", "qn", "end8", "filed8", "q", "ytd"]).sort_values(["cik", "end8"])

REV_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]
QR = quarterly(REV_TAGS)
prev = QR[["cik", "fy", "qn", "q"]].rename(columns={"q": "q_prev"}); prev["fy"] += 1
QR = QR.merge(prev, on=["cik", "fy", "qn"], how="left")
QR["rev_g"] = np.where(QR.q_prev > 0, QR.q / QR.q_prev - 1, np.nan)
QR = QR.sort_values(["cik", "end8"])
QR["rev_acc"] = QR.rev_g - QR.groupby("cik").rev_g.shift(1)
QR["rev_ttm"] = QR.groupby("cik").q.transform(lambda s: s.rolling(4, min_periods=4).sum())
QR = QR[np.isfinite(QR.rev_g)]
REVG = expand(QR.rename(columns={"rev_g": "val"}), "val")
REVA = expand(QR[np.isfinite(QR.rev_acc)].rename(columns={"rev_acc": "val"}), "val")
REVT = expand(QR[np.isfinite(QR.rev_ttm) & (QR.rev_ttm > 0)].rename(columns={"rev_ttm": "val"}), "val")
QO = quarterly(["OperatingIncomeLoss"])
QO["oi_ttm"] = QO.groupby("cik").q.transform(lambda s: s.rolling(4, min_periods=4).sum())
OM = QO.merge(QR[["cik", "end8", "rev_ttm"]], on=["cik", "end8"], how="inner")
OM = OM[np.isfinite(OM.oi_ttm) & (OM.rev_ttm > 0)]; OM["opm"] = (OM.oi_ttm / OM.rev_ttm).clip(-5, 1)
OPM = expand(OM.rename(columns={"opm": "val"}), "val")
# 자산성장: 10-K FY 총자산 YoY (같은 fy 안에서 end8 최대 = 당기)
at = xb[(xb.tag == "Assets") & (xb.val > 0) & xb.form.str.startswith("10-K") & (xb.fp == "FY")]
at = at.sort_values("filed8").drop_duplicates(["cik", "end8"], keep="first")
at = at.sort_values(["cik", "end8"]).copy()
at["prev_val"] = at.groupby("cik").val.shift(1); at["prev_end"] = at.groupby("cik").end8.shift(1)
gap = (pd.to_datetime(at.end8) - pd.to_datetime(at.prev_end)).dt.days
at = at[(gap >= 300) & (gap <= 430)]; at["asset_g"] = at.val / at.prev_val - 1
ASG = expand(at.drop(columns="val").rename(columns={"asset_g": "val"}), "val")
AT = expand(xb[(xb.tag == "Assets") & (xb.val > 0)], "val")
log("xbrl", dict(SH=len(SH), REVG=len(REVG), REVA=len(REVA), OPM=len(OPM), ASG=len(ASG)))

# SUE (8/30 스캔과 동일 구성)
QE = quarterly(["EarningsPerShareDiluted", "EarningsPerShareBasic"])
prev = QE[["cik", "fy", "qn", "q"]].rename(columns={"q": "q_prev"}); prev["fy"] += 1
QE = QE.merge(prev, on=["cik", "fy", "qn"], how="left"); QE["dq"] = QE.q - QE.q_prev
QE = QE.sort_values(["cik", "end8"])
QE["sd8"] = QE.groupby("cik")["dq"].transform(lambda s: s.shift(1).rolling(8, min_periods=4).std(ddof=1))
QE["sue"] = (QE.dq / QE.sd8.replace(0, np.nan)).clip(-10, 10)
SUE = expand(QE[np.isfinite(QE.sue)].rename(columns={"sue": "val"}), "val")

# 내부자 — 진짜 플래그(is_officer/is_director) · filed<앵커 · 적재 상한 확인
ins = pd.read_sql("""select symbol, owner_cik, is_officer, is_director, code, shares, price, filed
                     from insider_tx where code in ('P','S') and (is_officer=1 or is_director=1)""", fc)
ins["symbol"] = ins.symbol.str.upper(); ins = ins[ins.symbol.isin(C.index)]
ins["filed8"] = ins.filed.str.replace("-", "", regex=False)
ins["usd"] = (ins.shares.abs() * ins.price.abs()).fillna(0)
ins = ins[ins.usd > 0].sort_values("filed8")
INS_MAX = ins.filed8.max()
IP = ins[ins.code == "P"]; IS = ins[ins.code == "S"]
log("insider rows", len(ins), "max filed", INS_MAX)

def insider_feats(t8, idx):
    if t8 > INS_MAX: return pd.Series(np.nan, index=idx), pd.Series(np.nan, index=idx)
    lo = (pd.Timestamp(t8) - pd.Timedelta(days=90)).strftime("%Y%m%d")
    p = IP[(IP.filed8 < t8) & (IP.filed8 >= lo)]; s = IS[(IS.filed8 < t8) & (IS.filed8 >= lo)]
    nb = p.groupby("symbol").owner_cik.nunique().reindex(idx).fillna(0)
    pu = p.groupby("symbol").usd.sum().reindex(idx).fillna(0); su = s.groupby("symbol").usd.sum().reindex(idx).fillna(0)
    clu = ((nb >= 2) & (pu > su)).astype(float)
    npr = ((pu - su) / (pu + su)).where((pu + su) > 0)      # 순매수비 (거래 없으면 NaN)
    return clu, npr

# 실적발표일: 마지막 발표 후 경과일
ea = pd.read_sql("select cik, filed from earnings_events where is_earnings=1", fc)
ea["filed8"] = ea.filed.str.replace("-", "", regex=False)
EA = expand(ea.assign(val=ea.filed8), "val")

# S&P500 현재 소속 (PIT 아님 — 참고)
sd = sqlite3.connect("file:us-screener-data/us_seed.db?mode=ro", uri=True)
sp500 = set(pd.read_sql("select symbol from index_membership where idx='SP500' and date=(select max(date) from index_membership)", sd).symbol)

# ───────────────────────── 공통
def guard(i):
    t = ds[i]; c63 = C[ds[i-62:i+1]]
    amt20 = (RC[ds[i-19:i+1]] * V[ds[i-19:i+1]]).mean(axis=1)
    ok = (RC[t] >= 5) & (amt20 >= 1e6) & (c63.notna().sum(axis=1) >= 60) & (c63.std(axis=1, ddof=1) > 0)
    return ok[ok].index, amt20

def rk(s): return s.rank(pct=True)
def ranksum(F, cols):
    sc = None
    for c in cols:
        r = rk(F[c]); sc = r if sc is None else sc + r.fillna(0.5)
    return sc.where(F[cols[0]].notna()).dropna()

def feats(i, idx, amt20):
    t8 = ds[i]; F = pd.DataFrame(index=idx)
    w63 = R[ds[i-62:i+1]].loc[idx]; v63 = V[ds[i-62:i+1]].loc[idx]
    F["mom12"] = C.loc[idx, ds[i-21]] / C.loc[idx, ds[i-252]] - 1
    F["mom1"] = C.loc[idx, t8] / C.loc[idx, ds[i-21]] - 1
    F["upratio63"] = (w63 > 0).sum(axis=1) / w63.notna().sum(axis=1)
    F["size_amt"] = np.log10(amt20.reindex(idx).where(amt20.reindex(idx) > 0))
    F["rv63"] = w63.std(axis=1, ddof=1)
    F["prox52"] = C.loc[idx, t8] / C.loc[idx, ds[i-251:i+1]].max(axis=1)
    F["vol_cv"] = v63.std(axis=1, ddof=1) / v63.mean(axis=1)
    F["price"] = RC.loc[idx, t8]
    F["ma200_up"] = (C.loc[idx, t8] > C.loc[idx, ds[i-199:i+1]].mean(axis=1)).astype(float)
    F["age_d"] = (pd.Timestamp(t8) - pd.to_datetime(first_date.reindex(idx))).dt.days
    F["ipo1y"] = ((F.age_d < 365) & (first_date.reindex(idx) > ds[60])).astype(float)   # 데이터 시작점 이후 상장분만
    shr = latest_before(SH, t8); mcap = shr.reindex(idx) * RC.loc[idx, t8]
    F["mcap"] = mcap.where(mcap > 0)
    F["bm"] = latest_before(EQ, t8).reindex(idx) / F.mcap
    F["roa"] = latest_before(NI, t8).reindex(idx) / latest_before(AT, t8).reindex(idx)
    F["rev_g"] = latest_before(REVG, t8, days=120).reindex(idx)
    F["rev_acc"] = latest_before(REVA, t8, days=120).reindex(idx)
    F["sp"] = latest_before(REVT, t8, days=120).reindex(idx) / F.mcap
    F["opm"] = latest_before(OPM, t8, days=120).reindex(idx)
    F["asset_g"] = latest_before(ASG, t8).reindex(idx)
    F["sue"] = latest_before(SUE, t8, days=120).reindex(idx)
    clu, npr = insider_feats(t8, idx); F["ins_clu"] = clu; F["ins_npr"] = npr
    # 공매도 잔고 dtc: 결제일 +14일 PIT
    cut = (pd.Timestamp(t8) - pd.Timedelta(days=14)).strftime("%Y%m%d")
    sd_ = [d for d in sdates if d <= cut]
    F["dtc"] = DTC[sd_[-1]].reindex(idx) if sd_ else np.nan
    F["svr20"] = SVR[ds[i-20:i]].loc[idx].mean(axis=1)
    last_ea = latest_before(EA, t8, days=200).reindex(idx)
    F["days_since_ea"] = (pd.Timestamp(t8) - pd.to_datetime(last_ea, format="%Y%m%d", errors="coerce")).dt.days
    F["sp500_now"] = pd.Series(idx.isin(sp500).astype(float), index=idx)
    F["sector"] = sec.sector.reindex(idx); F["industry"] = sec.industry.reindex(idx)
    return F

def fwd(i, h, idx):
    if i + h >= len(ds): return pd.Series(np.nan, index=idx)
    return C.loc[idx, ds[i+h]] / C.loc[idx, ds[i]] - 1

# 블록 부트스트랩 (행렬 일괄): X (k×n) 각 행의 평균 CI
def bb_mat(X, block, alphas=(0.05,)):
    X = np.asarray(X, float); k, n = X.shape
    nblk = int(np.ceil(n / block))
    st = RNG.integers(0, n, (NBOOT, nblk))
    sel = ((st[:, :, None] + np.arange(block)[None, None, :]).reshape(NBOOT, -1) % n)[:, :n]
    out = {}
    means = np.empty((k, NBOOT))
    for r in range(k):
        x = X[r]; ok = np.isfinite(x)
        if ok.sum() < 8: means[r] = np.nan; continue
        xs = np.where(ok, x, np.nan)[sel]
        means[r] = np.nanmean(xs, axis=1)
    for a in alphas: out[a] = (np.nanquantile(means, a/2, axis=1), np.nanquantile(means, 1-a/2, axis=1))
    return np.nanmean(X, axis=1), out, np.isfinite(X).sum(axis=1)

# ───────────────────────── 앵커 루프
anchors = [i for i in range(252, len(ds) - 20, 5)]
log("anchors", len(anchors), ds[anchors[0]], ds[anchors[-1]])
FR = {}   # anchor -> 피처+전방수익 프레임
base_rec, uni_rec, spx_rec, regime = [], [], [], []
for k_, i in enumerate(anchors):
    t8 = ds[i]; idx, amt20 = guard(i)
    F = feats(i, idx, amt20)
    F["f20"] = fwd(i, 20, idx); F["f60"] = fwd(i, 60, idx); F["f120"] = fwd(i, 120, idx)
    F["base_sc"] = ranksum(F, ["mom12", "upratio63", "size_amt"]).reindex(idx)
    F.attrs["t8"] = t8; FR[i] = F
    top = F.base_sc.sort_values(ascending=False).index[:50]
    base_rec.append(F.f20[top].mean()); uni_rec.append(F.f20.mean())
    spx_rec.append(spx[ds[i+20]] / spx[ds[i]] - 1)
    regime.append(dict(t8=t8, spx_above=float(spx[t8] > spx_ma200[t8]), vix=vix[t8],
                       breadth=F.ma200_up.mean(), spx_r20=spx[t8] / spx[ds[i-20]] - 1))
    if k_ % 25 == 0: log(f"  anchor {k_}/{len(anchors)} {t8} univ={len(idx)} revg_cover={F.rev_g.notna().mean():.0%} ins={'ok' if t8<=INS_MAX else 'NA'}")
base_rec, uni_rec, spx_rec = map(np.array, (base_rec, uni_rec, spx_rec))
pd.to_pickle(dict(FR=FR, anchors=anchors, ds=ds, base=base_rec, uni=uni_rec, spx=spx_rec, regime=regime), "us_bigwin_frames.pkl")   # 후속 스크립트 재료
REG = pd.DataFrame(regime)
adates = [ds[i] for i in anchors]; yrs = np.array([d[:4] for d in adates])

# ═════════════════════════ F1. 큰 승자 해부
print("\n" + "═" * 100)
print("F1. 큰 승자 해부 — 사전 특징별 조건부 확률 (앵커별 P(그룹) − P(유니버스), %p · 블록 부트스트랩)")
TARGETS = {"W60: 60일 +50%↑": ("f60", 0.5, 1, 12), "W120: 120일 +100%↑": ("f120", 1.0, 1, 24),
           "L60: 60일 −40%↓": ("f60", -0.4, -1, 12)}
QF = ["mom12", "mom1", "upratio63", "size_amt", "rv63", "prox52", "vol_cv", "price", "mcap", "bm", "roa",
      "rev_g", "rev_acc", "sp", "opm", "asset_g", "sue", "ins_npr", "dtc", "svr20", "days_since_ea"]
FLAGS = ["ins_clu", "ipo1y", "ma200_up", "sp500_now"]
groups = []   # (label, fn(F)->bool mask)
for f in QF:
    groups.append((f"{f} Q5(상위20%)", lambda F, f=f: F[f].rank(pct=True) > 0.8))
    groups.append((f"{f} Q1(하위20%)", lambda F, f=f: F[f].rank(pct=True) <= 0.2))
for f in FLAGS:
    groups.append((f"{f}=1", lambda F, f=f: F[f] == 1))
groups.append(("base top50", lambda F: pd.Series(F.index.isin(F.base_sc.sort_values(ascending=False).index[:50]), index=F.index)))
groups.append(("base top10", lambda F: pd.Series(F.index.isin(F.base_sc.sort_values(ascending=False).index[:10]), index=F.index)))
groups.append(("rv63 Q5 ∩ mom12 Q5", lambda F: (F.rv63.rank(pct=True) > 0.8) & (F.mom12.rank(pct=True) > 0.8)))
groups.append(("rev_g Q5 ∩ mom12 Q5", lambda F: (F.rev_g.rank(pct=True) > 0.8) & (F.mom12.rank(pct=True) > 0.8)))
groups.append(("rev_g Q5 ∩ prox52 Q5", lambda F: (F.rev_g.rank(pct=True) > 0.8) & (F.prox52.rank(pct=True) > 0.8)))
groups.append(("size_amt Q1 ∩ mom12 Q5", lambda F: (F.size_amt.rank(pct=True) <= 0.2) & (F.mom12.rank(pct=True) > 0.8)))
NG = len(groups)
f1_rows = []
for tname, (col, thr, sign, block) in TARGETS.items():
    hit_rows, base_rate, mean_rows, sizes = [], [], [], []
    for i in anchors:
        F = FR[i]; y = F[col]
        if y.notna().mean() < 0.5: continue
        hit = (y >= thr) if sign > 0 else (y <= thr)
        hit = hit.where(y.notna())
        base_rate.append(hit.mean())
        hr, mr, sz = [], [], []
        for lab, fn in groups:
            m = fn(F).fillna(False) & y.notna()
            hr.append(hit[m].mean() if m.sum() >= 10 else np.nan)
            mr.append(y[m].mean() if m.sum() >= 10 else np.nan); sz.append(m.sum())
        hit_rows.append(hr); mean_rows.append(mr); sizes.append(sz)
    H = np.array(hit_rows).T; B = np.array(base_rate); M = np.array(mean_rows).T; SZ = np.array(sizes).T
    D = (H - B[None, :]) * 100
    alpha_b = 0.05 / NG
    m, cis, n = bb_mat(D, block, alphas=(0.05, alpha_b))
    lo, hi = cis[0.05]; lb, hb = cis[alpha_b]
    print(f"\n── {tname}   기준확률(유니버스) {B.mean()*100:.2f}%  n앵커={len(B)}  그룹 {NG}개 → Bonf α={alpha_b:.4f}")
    print(f"  {'그룹':28s} {'P(그룹)':>7s} {'Δ%p':>7s} {'95%CI':>17s} {'Bonf':>3s} {'배율':>5s} {'평균수익':>8s} {'평균n':>6s}")
    for g, (lab, _) in enumerate(groups):
        if not np.isfinite(m[g]): continue
        star = "**" if (lb[g] > 0 or hb[g] < 0) else ("*" if (lo[g] > 0 or hi[g] < 0) else "")
        pg = np.nanmean(H[g]) * 100
        print(f"  {lab:28s} {pg:6.2f}% {m[g]:+6.2f} [{lo[g]:+6.2f},{hi[g]:+6.2f}] {star:3s} {pg/(B.mean()*100):5.2f}x {np.nanmean(M[g])*100:+7.2f}% {np.nanmean(SZ[g]):6.0f}")
        f1_rows.append(dict(target=tname, group=lab, p_group=pg, base=B.mean()*100, delta=m[g], lo95=lo[g], hi95=hi[g],
                            lob=lb[g], hib=hb[g], ratio=pg/(B.mean()*100), mean_ret=np.nanmean(M[g])*100, n_avg=np.nanmean(SZ[g]), n_anchor=n[g]))
pd.DataFrame(f1_rows).to_csv("us_bigwin_f1.csv", index=False)

# 섹터별 큰 승자 확률 (W60, 기록용)
print("\n── (참고) 섹터별 W60 확률 · 평균 60일 수익 (앵커 평균, 현재 분류)")
rows = []
for i in anchors:
    F = FR[i]
    if F.f60.notna().mean() < 0.5: continue
    g = F.groupby("sector").agg(w=("f60", lambda s: (s >= 0.5).mean()), r=("f60", "mean"), n=("f60", "size"))
    g["t8"] = F.attrs["t8"]; rows.append(g.reset_index())
S = pd.concat(rows).groupby("sector").agg(w=("w", "mean"), r=("r", "mean"), n=("n", "mean")).sort_values("w", ascending=False)
for s_, r_ in S.iterrows(): print(f"  {str(s_):24s} P(W60)={r_.w*100:5.2f}%  평균60d {r_.r*100:+6.2f}%  n≈{r_.n:.0f}")

# ═════════════════════════ F2. 미탐색 직교 팩터 IC + top50 증분
print("\n" + "═" * 100)
NEWF = {"rev_g": +1, "rev_acc": +1, "sp": +1, "opm": +1, "asset_g": -1, "ipo1y": +1}
NT2 = len(NEWF) * 2; ab2 = 0.05 / NT2
print(f"F2. 미탐색 직교 팩터 — 전 유니버스 day-IC h20 (스피어만) + us_mus_v0 top50 4번째 항 짝비교 · 검정 {NT2}개 → Bonf α={ab2:.4f}")
ic_rows = {f: [] for f in NEWF}; d_rows = {f: [] for f in NEWF}; cov = {f: [] for f in NEWF}; ovl = {f: [] for f in NEWF}
for i in anchors:
    F = FR[i]; y = F.f20
    top = F.base_sc.sort_values(ascending=False).index[:50]
    for f, sgn in NEWF.items():
        ok = F[f].notna() & y.notna(); cov[f].append(F[f].notna().mean())
        ic_rows[f].append(spearmanr(F.loc[ok, f] * sgn, y[ok])[0] if ok.sum() >= 200 else np.nan)
        G = F.assign(x=F[f] * sgn); alt = ranksum(G, ["mom12", "upratio63", "size_amt", "x"]).sort_values(ascending=False).index[:50]
        d_rows[f].append((y[alt].mean() - y[top].mean()) * 100); ovl[f].append(len(set(alt) & set(top)) / 50)
X = np.array([ic_rows[f] for f in NEWF]); m, cis, n = bb_mat(X, 4, alphas=(0.05, ab2))
print(f"  {'팩터(부호)':14s} {'IC':>8s} {'95%CI':>19s} {'BonfCI':>19s} {'n':>4s} {'cover':>6s}  연도별 2024/2025/2026")
f2_rows = []
for g, (f, sgn) in enumerate(NEWF.items()):
    lo, hi = cis[0.05][0][g], cis[0.05][1][g]; lb, hb = cis[ab2][0][g], cis[ab2][1][g]
    star = "**" if lb > 0 else ("*" if lo > 0 else "")
    yr = "/".join(f"{np.nanmean(X[g][yrs == y]):+.3f}" for y in ["2024", "2025", "2026"])
    print(f"  {f+'('+('+' if sgn>0 else '−')+')':14s} {m[g]:+8.4f} [{lo:+8.4f},{hi:+8.4f}] [{lb:+8.4f},{hb:+8.4f}]{star:2s} {n[g]:4d} {np.nanmean(cov[f]):6.0%}  {yr}")
    f2_rows.append(dict(factor=f, sign=sgn, ic=m[g], lo95=lo, hi95=hi, lob=lb, hib=hb, n=n[g], cover=np.nanmean(cov[f])))
X = np.array([d_rows[f] for f in NEWF]); m, cis, n = bb_mat(X, 4, alphas=(0.05, ab2))
print(f"\n  top50 + 4번째 항 짝차이 (Δ = 변형 − base, %p/20d)")
for g, f in enumerate(NEWF):
    lo, hi = cis[0.05][0][g], cis[0.05][1][g]; lb, hb = cis[ab2][0][g], cis[ab2][1][g]
    star = "**" if (lb > 0 or hb < 0) else ("*" if (lo > 0 or hi < 0) else "")
    print(f"  +{f:12s} Δ={m[g]:+6.2f}%p [{lo:+6.2f},{hi:+6.2f}] Bonf[{lb:+6.2f},{hb:+6.2f}]{star:2s} n={n[g]} overlap={np.nanmean(ovl[f]):.0%}")
    f2_rows[g].update(dict(d_top50=m[g], d_lo95=lo, d_hi95=hi, d_lob=lb, d_hib=hb, overlap=np.nanmean(ovl[f])))
pd.DataFrame(f2_rows).to_csv("us_bigwin_f2.csv", index=False)

# ═════════════════════════ F3. 성장+추세 복합 아키텍처
print("\n" + "═" * 100)
def m_base(F):   return F.base_sc.sort_values(ascending=False).index[:50]
def m_gm(F):     return ranksum(F, ["rev_g", "mom12", "upratio63"]).sort_values(ascending=False).index[:50]
def m_canslim(F):
    G = F.copy(); return ranksum(G, ["rev_g", "sue", "prox52", "upratio63"]).sort_values(ascending=False).index[:50]
def m_base_growth(F):   # base 점수 상위 안에서 매출성장 중앙값 이상만 → 50
    g = F[F.rev_g > F.rev_g.median()]; return g.base_sc.sort_values(ascending=False).index[:50]
def m_base_prof(F):     # 영업이익 흑자(opm>0) 종목만
    g = F[F.opm > 0]; return g.base_sc.sort_values(ascending=False).index[:50]
def m_gm_small(F):      # 거래대금 하위半 + 매출성장 + 추세 (성장 소형주)
    g = F[F.size_amt < F.size_amt.median()]; return ranksum(g, ["rev_g", "mom12", "upratio63"]).sort_values(ascending=False).index[:50]
MODELS = {"base(mus_v0 top50)": m_base, "gm(rev_g+mom+up)": m_gm, "canslim(rev_g+sue+prox52+up)": m_canslim,
          "base∩rev_g>중앙값": m_base_growth, "base∩opm>0": m_base_prof, "gm_small(하위半)": m_gm_small}
NM = len(MODELS); ab3 = 0.05 / (NM - 1)
print(f"F3. 성장+추세 복합 아키텍처 — h20 EW 대비 초과 · base 대비 짝차이 · 모델 {NM-1}개 → Bonf α={ab3:.4f}")
recs = {k: [] for k in MODELS}; sets = {k: [] for k in MODELS}
for i in anchors:
    F = FR[i]
    for name, fn in MODELS.items():
        try: g = pd.Index(fn(F))
        except Exception: g = pd.Index([])
        sets[name].append(set(g)); recs[name].append(F.f20[g].mean() if len(g) >= 10 else np.nan)
EX = np.array([(np.array(recs[k]) - uni_rec) * 100 for k in MODELS]); m, cis, n = bb_mat(EX, 4, alphas=(0.05, ab3))
print(f"  유니버스 EW 20일 평균 {uni_rec.mean()*100:+.2f}% · SPX {spx_rec.mean()*100:+.2f}% · n앵커={len(anchors)}")
print(f"  {'모델':30s} {'초과vsEW':>9s} {'95%CI':>17s} {'BonfCI':>17s} {'vsSPX':>7s} {'적중':>5s} {'교체':>5s} {'누적(체인)':>10s} {'MDD':>6s} 2024/2025/2026")
f3_rows = []
for g, k in enumerate(MODELS):
    lo, hi = cis[0.05][0][g], cis[0.05][1][g]; lb, hb = cis[ab3][0][g], cis[ab3][1][g]
    r = np.array(recs[k]); exs = (r - spx_rec) * 100
    turns = [1 - len(a & b) / len(b) for a, b in zip(sets[k][:-4], sets[k][4:]) if a and b]
    cums, mdds = [], []
    for off in range(4):
        ch = r[off::4]; ch = ch[np.isfinite(ch)]
        if len(ch) < 5: continue
        eq = np.cumprod(1 + ch); cums.append(eq[-1] - 1); mdds.append((eq / np.maximum.accumulate(eq) - 1).min())
    yr = "/".join(f"{np.nanmean(EX[g][yrs == y]):+.2f}" for y in ["2024", "2025", "2026"])
    s1 = "*" if lo > 0 else " "; s2 = "**" if lb > 0 else "  "
    print(f"  {k:30s} {m[g]:+8.2f}%p [{lo:+6.2f},{hi:+6.2f}]{s1} [{lb:+6.2f},{hb:+6.2f}]{s2} {np.nanmean(exs):+6.2f} {np.nanmean(EX[g]>0):5.0%} {np.nanmean(turns):5.0%} {np.mean(cums)*100:+9.1f}% {np.mean(mdds)*100:5.1f}%  {yr}")
    f3_rows.append(dict(model=k, excess=m[g], lo95=lo, hi95=hi, lob=lb, hib=hb, vs_spx=np.nanmean(exs), hit=np.nanmean(EX[g] > 0),
                        turnover=np.nanmean(turns), chain_cum=np.mean(cums)*100, mdd=np.mean(mdds)*100, n=n[g]))
for lab, arr in [("유니버스 EW", uni_rec), ("SPX", spx_rec)]:
    cums = [np.cumprod(1 + arr[off::4])[-1] - 1 for off in range(4)]
    mdd = np.mean([(np.cumprod(1+arr[off::4]) / np.maximum.accumulate(np.cumprod(1+arr[off::4])) - 1).min() for off in range(4)])
    print(f"  (참고) {lab:22s} 체인 누적 {np.mean(cums)*100:+.1f}% MDD {mdd*100:.1f}%")
rb = np.array(recs["base(mus_v0 top50)"])
DD = np.array([(np.array(recs[k]) - rb) * 100 for k in MODELS if not k.startswith("base(")]); m, cis, n = bb_mat(DD, 4, alphas=(0.05, ab3))
print("\n  base 대비 짝차이 (모델 − base, %p/20d)")
for g, k in enumerate([k for k in MODELS if not k.startswith("base(")]):
    lo, hi = cis[0.05][0][g], cis[0.05][1][g]; lb, hb = cis[ab3][0][g], cis[ab3][1][g]
    star = "**" if (lb > 0 or hb < 0) else ("*" if (lo > 0 or hi < 0) else "")
    e = EX[list(MODELS).index(k)]; ok = np.isfinite(e) & np.isfinite(EX[0])
    print(f"  {k:30s} Δ={m[g]:+6.2f}%p [{lo:+6.2f},{hi:+6.2f}] Bonf[{lb:+6.2f},{hb:+6.2f}]{star:2s} n={n[g]} corr(base)={np.corrcoef(e[ok], EX[0][ok])[0,1]:+.2f}")
    f3_rows[list(MODELS).index(k)].update(dict(d_base=m[g], d_lo95=lo, d_hi95=hi, d_lob=lb, d_hib=hb))
pd.DataFrame(f3_rows).to_csv("us_bigwin_f3.csv", index=False)

# ═════════════════════════ F4. 레짐 오버레이
print("\n" + "═" * 100)
print("F4. 레짐 오버레이 — base top50 의 h20 절대수익·EW 대비 초과 (앵커 시점 레짐별 · 표본수 주의)")
ex_b = (base_rec - uni_rec) * 100; ab_b = base_rec * 100
def show(mask, lab):
    k = mask.sum()
    if k < 5: print(f"  {lab:34s} n={k:3d}  (표본 부족)"); return
    m1, c1, _ = bb_mat(ex_b[mask][None, :], 4); m2, c2, _ = bb_mat(ab_b[mask][None, :], 4)
    print(f"  {lab:34s} n={k:3d}  초과 {m1[0]:+6.2f}%p [{c1[0.05][0][0]:+6.2f},{c1[0.05][1][0]:+6.2f}]  절대 {m2[0]:+6.2f}% [{c2[0.05][0][0]:+6.2f},{c2[0.05][1][0]:+6.2f}]  SPX {spx_rec[mask].mean()*100:+5.2f}%  EW {uni_rec[mask].mean()*100:+5.2f}%")
show(REG.spx_above.values == 1, "SPX > 200MA"); show(REG.spx_above.values == 0, "SPX ≤ 200MA")
for lab, lo_, hi_ in [("VIX 하위 1/3", 0, 1/3), ("VIX 중위 1/3", 1/3, 2/3), ("VIX 상위 1/3", 2/3, 1.01)]:
    q = REG.vix.rank(pct=True).values; show((q > lo_) & (q <= hi_), f"{lab} (평균 {REG.vix[(q>lo_)&(q<=hi_)].mean():.1f})")
for lab, lo_, hi_ in [("시장폭(>200MA 비율) 하위 1/3", 0, 1/3), ("시장폭 중위 1/3", 1/3, 2/3), ("시장폭 상위 1/3", 2/3, 1.01)]:
    q = REG.breadth.rank(pct=True).values; show((q > lo_) & (q <= hi_), f"{lab} ({REG.breadth[(q>lo_)&(q<=hi_)].mean():.0%})")
show(REG.spx_r20.values > 0, "SPX 직전 20일 +"); show(REG.spx_r20.values <= 0, "SPX 직전 20일 −")
print("\n  비중첩 체인(4오프셋 평균) — 레짐 밖이면 현금(0%) 가정 · 비용 0 · in-sample")
def chain(r, mask=None):
    cums, mdds = [], []
    for off in range(4):
        ch = r[off::4].copy()
        if mask is not None: ch = np.where(mask[off::4], ch, 0.0)
        eq = np.cumprod(1 + ch); cums.append(eq[-1] - 1); mdds.append((eq / np.maximum.accumulate(eq) - 1).min())
    return np.mean(cums) * 100, np.mean(mdds) * 100
for lab, mk_ in [("항상 보유", None), ("SPX>200MA 일 때만", REG.spx_above.values == 1),
                 ("시장폭 상위 2/3 일 때만", REG.breadth.rank(pct=True).values > 1/3), ("VIX 하위 2/3 일 때만", REG.vix.rank(pct=True).values <= 2/3)]:
    c, d = chain(base_rec, mk_); cs, dsx = chain(spx_rec, mk_)
    print(f"  {lab:26s} base 누적 {c:+7.1f}% MDD {d:6.1f}%   | 같은 규칙 SPX 누적 {cs:+6.1f}% MDD {dsx:6.1f}%   보유비율 {100 if mk_ is None else mk_.mean()*100:.0f}%")
REG.assign(base=base_rec, uni=uni_rec, spx=spx_rec).to_csv("us_bigwin_f4_regime.csv", index=False)

# ═════════════════════════ F5. 라이브 관측 갱신
print("\n" + "═" * 100)
print("F5. 라이브 관측 — score_daily 실기록 · 일일 리밸런스 EW · 종가체결 · 비용 0 (판정 재료 아님)")
sdly = pd.read_sql("select model, date, symbol, rank from score_daily", con)
cnt_d = sdly[sdly.model == "us_mus_v0"].groupby("date").size()
good = cnt_d[cnt_d >= 0.9 * cnt_d.max()].index          # 부분 유니버스 박제일 제외(v08 이전 잔존분)
for model, topn in [("us_mus_v0", 50), ("us_rvdtc_a", 10)]:
    sub = sdly[(sdly.model == model) & sdly.date.isin(good)]
    full = set(cnt[cnt >= 0.9 * cnt.max()].index)          # 다음날 시세가 90% 이상 수집된 날만(부분 수집일 제외)
    dates = sorted(sub.date.unique()); dates = [d for d in dates if d in ds and ds.index(d) + 1 < len(ds) and ds[ds.index(d) + 1] in full]
    pr, ur, sr = [], [], []
    for d in dates:
        i = ds.index(d); nxt = ds[i+1]
        g = sub[sub.date == d]; top = g[g["rank"] <= topn].symbol; uni = sdly[(sdly.model == "us_mus_v0") & (sdly.date == d)].symbol
        r = C[nxt] / C[d] - 1
        pr.append(r.reindex(top).mean()); ur.append(r.reindex(uni).mean()); sr.append(spx[nxt] / spx[d] - 1)
    pr, ur, sr = map(np.array, (pr, ur, sr))
    cp, cu, cs = (np.prod(1 + pr) - 1) * 100, (np.prod(1 + ur) - 1) * 100, (np.prod(1 + sr) - 1) * 100
    print(f"  {model:10s} top{topn}: {dates[0]}→{ds[ds.index(dates[-1])+1]} ({len(dates)}거래일, 부분수집일 제외 {len(sub.date.unique())-len(dates)})  모델 {cp:+6.2f}%  가드EW {cu:+6.2f}%  SPX {cs:+6.2f}%  초과(vsEW) {cp-cu:+6.2f}%p  일별승률(vsEW) {np.mean(pr>ur):.0%}  제외일 {len(cnt_d)-len(good)}")
log("done")
