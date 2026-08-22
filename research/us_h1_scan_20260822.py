# -*- coding: utf-8 -*-
"""
us_h1_scan_20260822.py — us_mus_v0 top50 내부, '다음날(h1)' 수익 예측 스캔 (in-sample)
=====================================================================================
질문: top50 안에서 내일 상대수익이 높을 종목을 미리 가려내는 특징이 있는가.
방법: 매일 top50 재현(일일 앵커 ~480일, h1 창 비중첩) → 특징 8종의 당일 단면
      스피어만 IC(다음날 top50 내 상대수익 대비) → 평균 IC, 블록 부트스트랩 CI,
      Bonferroni α=0.05/8. 5분위 스프레드 병기.
정직성: in-sample · 생존편향 미보정 · 종가체결 가정 · 거래비용 0 —
      h1 왕복비용(스프레드 포함 ~0.1-0.5%)이 어떤 기움도 잡아먹을 수 있음을 명시.
"""
import sqlite3, datetime as dt
import numpy as np, pandas as pd
from scipy.stats import spearmanr

DB="/tmp/us-screener-data/us_ohlcv.db"
RNG=np.random.default_rng(20260822); NBOOT=10000
con=sqlite3.connect(f"file:{DB}?mode=ro",uri=True)
print("loading...",flush=True)
df=pd.read_sql("select symbol,date,close,adj_close,volume from daily_ohlcv",con)
C=df.pivot(index="symbol",columns="date",values="adj_close").sort_index(axis=1)
RC=df.pivot(index="symbol",columns="date",values="close").sort_index(axis=1)
V=df.pivot(index="symbol",columns="date",values="volume").sort_index(axis=1); del df
ds=list(C.columns)
R=C.T.pct_change(fill_method=None).T

# 롤링 통계(열=날짜 → 전치 롤링)
def roll(m,w,fn):
    return getattr(m.T.rolling(w),fn)().T
AMT=RC*V
amt20=roll(AMT,20,"mean")
up63=(roll((R>0).astype(float),63,"sum")/roll(R.notna().astype(float),63,"sum"))
mom12=C.T.shift(21).T/C.T.shift(252).T-1
rv20=roll(R,20,"std")
v20=roll(V,20,"mean")
hi63=roll(C,63,"max")
hi252=roll(C,252,"max")

# 공매도 dtc (settle+14d PIT)
si=pd.read_sql("select settlement_date,symbol,days_to_cover from short_interest",con)
sp=si.pivot(index="symbol",columns="settlement_date",values="days_to_cover")
settles=sorted(sp.columns)
def d2(s): return dt.date(int(s[:4]),int(s[4:6]),int(s[6:]))
usable={t:[x for x in settles if (d2(t)-d2(x)).days>=14] for t in ds}

start=ds.index("20240715") if "20240715" in ds else 252
anchors=list(range(start,len(ds)-1))  # h1 → 마지막-1
print("일일 앵커:",len(anchors),ds[anchors[0]],"→",ds[anchors[-1]],flush=True)

FEATS=["r1","r5","dist63hi","dist52hi","rv20","volspike","scorerank","dtc"]
ics={f:[] for f in FEATS}
q_spread={f:[] for f in FEATS}  # (최고5분위 − 최저5분위) 다음날 수익 %p
for k,i in enumerate(anchors):
    t=ds[i]
    okm=(RC[t]>=5)&(amt20[t]>=1e6)&(rv20[t]>0)
    idx=okm[okm.fillna(False)].index
    F=pd.DataFrame(index=idx)
    F["mom12"]=mom12.loc[idx,t]; F["up63"]=up63.loc[idx,t]
    F["size"]=np.log10(amt20.loc[idx,t].where(amt20.loc[idx,t]>0))
    sc=None;core=None
    for j,f in enumerate(["mom12","up63","size"]):
        rk=F[f].rank(pct=True)
        if j==0: core=rk.notna()
        sc=rk if sc is None else sc+rk.fillna(0.5)
    sc=sc.where(core).dropna().sort_values(ascending=False)
    top=sc.index[:50]
    y=R.loc[top,ds[i+1]]
    y=y-y.mean()  # top50 내 상대수익
    X=pd.DataFrame(index=top)
    X["r1"]=R.loc[top,t]
    X["r5"]=C.loc[top,t]/C.loc[top,ds[i-5]]-1
    X["dist63hi"]=C.loc[top,t]/hi63.loc[top,t]-1
    X["dist52hi"]=C.loc[top,t]/hi252.loc[top,t]-1
    X["rv20"]=rv20.loc[top,t]
    X["volspike"]=V.loc[top,t]/v20.loc[top,t]
    X["scorerank"]=np.arange(1,len(top)+1)
    u=usable[t]
    X["dtc"]=sp[u[-1]].reindex(top) if u and u[-1] in sp.columns else np.nan
    for f in FEATS:
        m=X[f].notna()&y.notna()
        if m.sum()>=30:
            ics[f].append(spearmanr(X.loc[m,f],y[m]).statistic)
            qq=pd.qcut(X.loc[m,f].rank(method="first"),5,labels=False)
            q_spread[f].append((y[m][qq==4].mean()-y[m][qq==0].mean())*100)
        else:
            ics[f].append(np.nan); q_spread[f].append(np.nan)
    if k%100==0: print(f"  {k}/{len(anchors)}",flush=True)

def boot(x,block=5):
    x=np.asarray([v for v in x if np.isfinite(v)]); n=len(x)
    nb=int(np.ceil(n/block)); ms=np.empty(NBOOT)
    for b in range(NBOOT):
        st=RNG.integers(0,n,nb); sel=(st[:,None]+np.arange(block)[None,:]).ravel()%n
        ms[b]=x[sel[:n]].mean()
    return x.mean(),np.quantile(ms,.025),np.quantile(ms,.975),np.quantile(ms,.003125),np.quantile(ms,1-.003125),n

print("\n== top50 내 h1 단면 IC (일일앵커, Bonferroni α=0.00625)")
rows=[]
for f in FEATS:
    m,lo,hi,lob,hib,n=boot(ics[f])
    qs=np.nanmean(q_spread[f])
    s95="*" if lo>0 or hi<0 else " "; sB="**" if lob>0 or hib<0 else "  "
    rows.append((f,n,m,lo,hi,lob,hib,qs))
    print(f"  {f:10s} n={n:3d} IC={m:+.4f} 95%[{lo:+.4f},{hi:+.4f}]{s95} Bonf[{lob:+.4f},{hib:+.4f}]{sB} Q5-Q1={qs:+.3f}%p/일")
pd.DataFrame(rows,columns=["feat","n","IC","lo95","hi95","loB","hiB","q5q1_pp"]).to_csv(
    "research/us_h1_scan_frame.csv",index=False)
print("\n참고: top50 h1 평균 일수익 분산(단면 std) 및 비용 스케일")
