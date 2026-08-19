# -*- coding: utf-8 -*-
"""후속 검증: ① 프레임3 벤치마크를 EW평균으로 교정(원 스크립트는 중앙값 사용 오류)
② vol_cv '안정재' 주장 검증 — 평균이 아닌 적중률(top50이 유니버스 EW를 이긴 앵커 비율)
   두 형태: (a) 4번째 순위항 (b) top50 중 vol_cv 하위 25 서브셋"""
import sqlite3
import numpy as np, pandas as pd
import datetime as dt

DB="/tmp/us-screener-data/us_ohlcv.db"; MKT="/tmp/us-screener-data/us_market.db"
RNG=np.random.default_rng(20260819); NBOOT=10000
con=sqlite3.connect(f"file:{DB}?mode=ro",uri=True)
df=pd.read_sql("select symbol,date,close,adj_close,volume from daily_ohlcv",con)
C=df.pivot(index="symbol",columns="date",values="adj_close")
RC=df.pivot(index="symbol",columns="date",values="close")
V=df.pivot(index="symbol",columns="date",values="volume"); del df
ds=sorted(C.columns)
anchors=[i for i in range(252,len(ds)-20,5)]

def boot(x,block=4):
    x=np.asarray([v for v in x if np.isfinite(v)]); n=len(x)
    nblk=int(np.ceil(n/block)); ms=np.empty(NBOOT)
    for b in range(NBOOT):
        st=RNG.integers(0,n,nblk)
        sel=(st[:,None]+np.arange(block)[None,:]).ravel()%n
        ms[b]=x[sel[:n]].mean()
    return x.mean(), np.quantile(ms,.025), np.quantile(ms,.975), n

res={"base":[],"cv4":[],"cvsub":[],"univ":[]}
for i in anchors:
    t=ds[i]; c63=C[ds[i-62:i+1]]
    amt20=(RC[ds[i-19:i+1]]*V[ds[i-19:i+1]]).mean(axis=1)
    ok=(RC[t]>=5)&(amt20>=1e6)&(c63.notna().sum(axis=1)>=60)&(c63.std(axis=1,ddof=1)>0)
    idx=ok[ok].index
    fwd=(C.loc[idx,ds[i+20]]/C.loc[idx,ds[i]]-1)*100
    w63=C[ds[i-62:i+1]].loc[idx].pct_change(axis=1)
    F=pd.DataFrame(index=idx)
    F["mom12"]=C.loc[idx,ds[i-21]]/C.loc[idx,ds[i-252]]-1
    F["up63"]=(w63>0).sum(axis=1)/w63.notna().sum(axis=1)
    F["size"]=np.log10(amt20.reindex(idx).where(amt20.reindex(idx)>0))
    sc=None;core=None
    for j,f in enumerate(["mom12","up63","size"]):
        rk=F[f].rank(pct=True)
        if j==0: core=rk.notna()
        sc=rk if sc is None else sc+rk.fillna(0.5)
    ms=sc.where(core).dropna()
    v63=V[ds[i-62:i+1]].loc[idx]
    cv=v63.std(axis=1,ddof=1)/v63.mean(axis=1)
    top=ms.sort_values(ascending=False).index[:50]
    sc4=ms+(1-cv.rank(pct=True)).reindex(ms.index).fillna(0.5)
    top4=sc4.sort_values(ascending=False).index[:50]
    sub=cv[top].sort_values().index[:25]
    res["base"].append(fwd[top].mean()); res["cv4"].append(fwd[top4].mean())
    res["cvsub"].append(fwd[sub].mean()); res["univ"].append(fwd.mean())

b=np.array(res["base"]); u=np.array(res["univ"])
c4=np.array(res["cv4"]); cs=np.array(res["cvsub"])
print("── 프레임3 교정: 벤치마크 = 가드 유니버스 EW '평균' (n=%d 주간앵커)"%len(b))
for lab,x in [("top50-EW초과(전체)",b-u)]:
    m,lo,hi,n=boot(x); print(f"  {lab:20s} {m:+.2f}%p 95%CI[{lo:+.2f},{hi:+.2f}]")
mk=sqlite3.connect(f"file:{MKT}?mode=ro",uri=True)
spx=pd.read_sql("select date,close from market_daily where series='SPX' order by date",mk,index_col="date")["close"]
m200=spx.rolling(200,min_periods=100).mean()
reg=np.array([1 if (ds[i] in spx.index and spx[ds[i]]>m200[ds[i]]) else 0 for i in anchors],dtype=float)
for lab,msk in [("SPX>200MA",reg==1),("SPX<=200MA",reg==0)]:
    x=(b-u)[msk]
    if len(x)>=5:
        m,lo,hi,n=boot(x); print(f"  {lab:20s} n={n:3d} {m:+.2f}%p 95%CI[{lo:+.2f},{hi:+.2f}]")
print()
print("── vol_cv 안정재 검증 (기준: 유니버스 EW평균 초과의 적중률·평균)")
hit=lambda x: (x>0).mean()*100
for lab,x in [("base top50",b-u),("(a) cv 4번째항",c4-u),("(b) top50→cv하위25",cs-u)]:
    m,lo,hi,n=boot(x)
    print(f"  {lab:18s} 평균 {m:+.2f}%p CI[{lo:+.2f},{hi:+.2f}] · 적중 {hit(x):.0f}%")
print()
print("── 짝차이 (변형 − base, 같은 앵커)")
for lab,x in [("(a)−base",c4-b),("(b)−base",cs-b)]:
    m,lo,hi,n=boot(x)
    d_hit=hit(np.array(x)+0*x)  # placeholder
    print(f"  {lab:10s} 평균 {m:+.3f}%p CI[{lo:+.3f},{hi:+.3f}] · 적중차 {hit(c4-u)-hit(b-u) if 'a' in lab else hit(cs-u)-hit(b-u):+.0f}%p")
