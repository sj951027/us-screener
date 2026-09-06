import numpy as np, pandas as pd
src=open('us_arch_scan_20260906.py',encoding='utf-8').read()
head=src.split('MODELS = {')[0]
exec(head)
NBOOT=10000
def bb(x, alphas=(0.05,0.005), block=4):
    x=np.asarray(x,float); x=x[np.isfinite(x)]; n=len(x)
    nblk=int(np.ceil(n/block)); means=np.empty(NBOOT)
    for b in range(NBOOT):
        st=RNG.integers(0,n,nblk); sel=(st[:,None]+np.arange(block)[None,:]).ravel()%n
        means[b]=x[sel[:n]].mean()
    return x.mean(), {a:(np.quantile(means,a/2),np.quantile(means,1-a/2)) for a in alphas}, n
def m_ind(F, q, mincnt, base_cols=("mom12","upratio63","size_amt"), k=50):
    g=F.groupby("ind").mom12.agg(["mean","count"]); g=g[g["count"]>=mincnt]
    top=g[g["mean"]>=g["mean"].quantile(q)].index
    sub=F[F.ind.isin(top)]
    return ranksum(sub, list(base_cols)).sort_values(ascending=False).index[:k], top
anchors=[i for i in range(252,len(ds)-20,5)]
configs=[(0.8,10),(0.7,10),(0.9,10),(0.8,5),(0.8,20),(0.8,10,'nosize'),(0.8,10,'top10')]
recs={c:[] for c in configs}; base=[]; uni=[]; indhist=[]; ncov=[]
for k_,i in enumerate(anchors):
    t8=ds[i]; idx,amt20=guard(i); F=feats(i,idx,amt20)
    fwd=C.loc[idx,ds[i+20]]/C.loc[idx,ds[i]]-1; uni.append(fwd.mean())
    b=ranksum(F,["mom12","upratio63","size_amt"]).sort_values(ascending=False).index[:50]; base.append(fwd[b].mean())
    for c in configs:
        if len(c)==2: g,top=m_ind(F,c[0],c[1])
        elif c[2]=='nosize': g,top=m_ind(F,c[0],c[1],("mom12","upratio63"))
        else: g,top=m_ind(F,c[0],c[1],k=10)
        recs[c].append(fwd[g].mean() if len(g)>=5 else np.nan)
        if c==(0.8,10): indhist.append(list(top)); ncov.append(len(top))
    if k_%30==0: log("anchor",k_)
uni=np.array(uni); base=np.array(base)
print(f"\n== indmom 민감도 (초과 vs EW · Δ vs base, %p/20d; CI 95% / 99.5%=Bonf10)")
for c in configs:
    r=np.array(recs[c],float); ex=(r-uni)*100; d=(r-base)*100
    m,ci,n=bb(ex); md,cid,_=bb(d)
    print(f"  q={c[0]} min={c[1]} {c[2] if len(c)>2 else '':7s} 초과 {m:+.2f} 95[{ci[0.05][0]:+.2f},{ci[0.05][1]:+.2f}] B[{ci[0.005][0]:+.2f},{ci[0.005][1]:+.2f}] | Δ {md:+.2f} 95[{cid[0.05][0]:+.2f},{cid[0.05][1]:+.2f}] B[{cid[0.005][0]:+.2f},{cid[0.005][1]:+.2f}] n={n}")
print(f"\n선택 업종 수 평균 {np.mean(ncov):.1f}")
from collections import Counter
cnt=Counter(x for l in indhist for x in l)
print("자주 선택된 업종 (앵커 114개 중):", cnt.most_common(15))
# 반도체 제외 시
recs_ex=[]
for k_,i in enumerate(anchors):
    pass
