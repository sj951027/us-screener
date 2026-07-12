# 1단계: DB → 패널 → npz 저장 (이후 콜들이 재사용)
import sqlite3, numpy as np, pandas as pd
con=sqlite3.connect('file:/tmp/us_snap.db?mode=ro',uri=True)
raw=pd.read_sql("SELECT symbol,date,close,adj_close,volume FROM daily_ohlcv",con)
for c in ('close','adj_close','volume'): raw[c]=pd.to_numeric(raw[c],errors='coerce')
piv=lambda v: raw.pivot_table(index='symbol',columns='date',values=v,aggfunc='last').sort_index(axis=1)
C=piv('adj_close'); RAWC=piv('close'); V=piv('volume')
np.savez_compressed('/tmp/uswf/panels.npz',
    adj=C.values.astype('float32'), rawc=RAWC.values.astype('float32'),
    vol=V.values.astype('float32'),
    symbols=np.array(C.index, dtype=object), dates=np.array(C.columns, dtype=object))
print("패널 저장:", C.shape)
# 2단계: 앵커 청크 처리 (호출당 ANCHORS_PER_CALL개) — 결과는 CSV append
import sys, os, numpy as np, pandas as pd
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
z=np.load('/tmp/uswf/panels.npz',allow_pickle=True)
C=pd.DataFrame(z['adj'],index=z['symbols'],columns=z['dates']).astype('float64')
RAWC=pd.DataFrame(z['rawc'],index=z['symbols'],columns=z['dates']).astype('float64')
V=pd.DataFrame(z['vol'],index=z['symbols'],columns=z['dates']).astype('float64')
dates=list(C.columns); R=C.pct_change(axis=1,fill_method=None); AMT=RAWC*V
H=20; STEP=20
anchors=list(range(273,len(dates)-H,STEP))
state='/tmp/uswf/state.txt'
done=int(open(state).read()) if os.path.exists(state) else 0
todo=anchors[done:done+int(sys.argv[1])]
if not todo: print("ALL_DONE"); sys.exit(0)

DIR={'lv63':False,'nh252':True,'mom12':True,'mom_1m':True,'upratio63':True,
     'max5':False,'fip':True,'size_amt':True}
rows=[]; dec=[]
for i in todo:
    w=R[dates[max(0,i-20):i+1]]; n=w.notna().sum(axis=1)
    rv=w.std(axis=1,ddof=1)
    amt20=AMT[dates[max(0,i-19):i+1]].mean(axis=1)
    ok=(rv>=0.003)&((w==0).sum(axis=1)/n.where(n>0)<=0.5)&(RAWC[dates[i]]>=5.0)&(amt20>=1e6)
    seg=C[dates[i:i+H+1]]; rr=seg.pct_change(axis=1,fill_method=None)
    fwd=((seg[dates[i+H]]/seg[dates[i]]-1)*100).where(~(rr.abs().max(axis=1)>1.0))
    uni=C.index[ok&fwd.notna()]
    if len(uni)<500: continue
    ru=fwd.reindex(uni); ex=ru-ru.median()
    F=pd.DataFrame(index=C.index)
    c_now=C[dates[i]]
    w63=R[dates[max(0,i-62):i+1]]
    F['lv63']=w63.std(axis=1,ddof=1).where(w63.notna().sum(axis=1)>=30)
    F['nh252']=c_now/C[dates[max(0,i-251):i+1]].max(axis=1)-1
    mom12=C[dates[i-21]]/C[dates[i-252]]-1
    F['mom12']=mom12
    F['mom_1m']=(c_now/C[dates[max(0,i-22)]]-1)*100
    F['upratio63']=(w63>0).sum(axis=1)/w63.notna().sum(axis=1)
    F['max5']=pd.DataFrame(np.sort(w.values,axis=1)[:,-5:],index=w.index).mean(axis=1)
    w231=R[dates[i-252:i-21]]
    pos=(w231>0).sum(axis=1); neg=(w231<0).sum(axis=1); tot=w231.notna().sum(axis=1)
    F['fip']=np.sign(mom12)*((pos-neg)/tot.where(tot>=100))
    F['size_amt']=np.log10(amt20.where(amt20>0))
    yr=dates[i][:4]
    for f,d in DIR.items():
        x=F.loc[uni,f]; m=x.notna()
        if m.sum()<300: continue
        ic=x[m].rank().corr(ru[m].rank())
        rows.append((dates[i],yr,f,ic if d else -ic))
        q=x[m].rank(pct=True,ascending=d)
        top=q[q>=0.9].index
        dec.append((yr,f,float(ru.reindex(top).mean()),float(ex.reindex(top).mean())))
pd.DataFrame(rows,columns=['date','yr','f','sic']).to_csv('/tmp/uswf/ic.csv',mode='a',header=not os.path.exists('/tmp/uswf/ic.csv'),index=False)
pd.DataFrame(dec,columns=['yr','f','abs','exc']).to_csv('/tmp/uswf/dec.csv',mode='a',header=not os.path.exists('/tmp/uswf/dec.csv'),index=False)
open(state,'w').write(str(done+len(todo)))
print(f"청크 완료: {done+len(todo)}/{len(anchors)} 앵커")
# 3단계: 연도 재집계 + 조합 대결 + 최근 60일 상승 상위(실명)
import sys, os, sqlite3, numpy as np, pandas as pd
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ic=pd.read_csv('/tmp/uswf/ic.csv',dtype={'yr':str})
print("== 연도 안정성 (signed IC 평균) ==")
print(ic.pivot_table(index='f',columns='yr',values='sic',aggfunc='mean').round(3).to_string())

z=np.load('/tmp/uswf/panels.npz',allow_pickle=True)
C=pd.DataFrame(z['adj'],index=z['symbols'],columns=z['dates']).astype('float64')
RAWC=pd.DataFrame(z['rawc'],index=z['symbols'],columns=z['dates']).astype('float64')
V=pd.DataFrame(z['vol'],index=z['symbols'],columns=z['dates']).astype('float64')
dates=list(C.columns); R=C.pct_change(axis=1,fill_method=None); AMT=RAWC*V
# 이름 맵
s=sqlite3.connect('file:/sessions/beautiful-confident-goodall/mnt/uploads/us_seed.db?mode=ro',uri=True)
names={sym.replace('.','-'):(n or '')[:38] for sym,n in s.execute(
 "SELECT symbol,name FROM listing_daily WHERE date=(SELECT MAX(date) FROM listing_daily)")}

# 최근 60거래일 상승 상위 (가드 적용, 앵커시점 팩터 병기)
i1=len(dates)-1; i0=i1-60
w=R[dates[i0-20:i0+1]]; n=w.notna().sum(axis=1); rv=w.std(axis=1,ddof=1)
amt20=AMT[dates[i0-19:i0+1]].mean(axis=1)
ok=(rv>=0.003)&((w==0).sum(axis=1)/n.where(n>0)<=0.5)&(RAWC[dates[i0]]>=5.0)&(amt20>=1e6)
seg=C[dates[i0:i1+1]]; rr=seg.pct_change(axis=1,fill_method=None)
ret=((seg[dates[i1]]/seg[dates[i0]]-1)*100).where(~(rr.abs().max(axis=1)>1.0))
uni=C.index[ok&ret.notna()]
top=ret.reindex(uni).sort_values(ascending=False).head(15)
mom12=(C[dates[i0-21]]/C[dates[i0-252]]-1)
nh=(C[dates[i0]]/C[dates[i0-251:i0+1]].max(axis=1)-1)
w63=R[dates[i0-62:i0+1]]; up63=(w63>0).sum(axis=1)/w63.notna().sum(axis=1)
print(f"\n== 최근 60거래일({dates[i0]}→{dates[i1]}) 상승 상위 15 · 유니버스 {len(uni)} · 중앙값 {ret.reindex(uni).median():+.1f}% ==")
print(f"{'sym':7} {'수익%':>7} {'52주고가':>8} {'mom12':>7} {'up63':>6}  이름")
for sym,v in top.items():
    print(f"{sym:7} {v:>+7.1f} {nh.get(sym,np.nan):>+8.2f} {mom12.get(sym,np.nan):>+7.2f} {up63.get(sym,np.nan):>6.2f}  {names.get(sym,'?')}")
prof=pd.DataFrame({'nh252':nh,'mom12':mom12,'up63':up63}).loc[uni].rank(pct=True)
print("승자(top15) 팩터 백분위:", prof.loc[top.index].median().round(2).to_dict())

# 조합 대결 (top50 동일가중 h20, 23앵커 복리, 비용 0.5%p)
H=20; anchors=list(range(273,len(dates)-H,20))
COMBOS={'mom12+nh':[('mom12',True),('nh252',True)],
 'c3미국(mom+nh+size+up)':[('mom12',True),('nh252',True),('size_amt',True),('upratio63',True)],
 'mom+up+size':[('mom12',True),('upratio63',True),('size_amt',True)],
 'lv+mom+nh+size':[('lv63',False),('mom12',True),('nh252',True),('size_amt',True)]}
rows=[]; mkt=[]
for i in anchors:
    w=R[dates[max(0,i-20):i+1]]; n=w.notna().sum(axis=1)
    rv=w.std(axis=1,ddof=1); amt20=AMT[dates[max(0,i-19):i+1]].mean(axis=1)
    ok=(rv>=0.003)&((w==0).sum(axis=1)/n.where(n>0)<=0.5)&(RAWC[dates[i]]>=5.0)&(amt20>=1e6)
    seg=C[dates[i:i+H+1]]; rr=seg.pct_change(axis=1,fill_method=None)
    fwd=((seg[dates[i+H]]/seg[dates[i]]-1)*100).where(~(rr.abs().max(axis=1)>1.0))
    uni=C.index[ok&fwd.notna()]
    if len(uni)<500: continue
    ru=fwd.reindex(uni); mkt.append(ru.median())
    F=pd.DataFrame(index=uni)
    c_now=C[dates[i]]
    w63=R[dates[max(0,i-62):i+1]]
    F['lv63']=w63.std(axis=1,ddof=1).reindex(uni)
    F['nh252']=(c_now/C[dates[max(0,i-251):i+1]].max(axis=1)-1).reindex(uni)
    F['mom12']=(C[dates[i-21]]/C[dates[i-252]]-1).reindex(uni)
    F['upratio63']=((w63>0).sum(axis=1)/w63.notna().sum(axis=1)).reindex(uni)
    F['size_amt']=np.log10(amt20.where(amt20>0)).reindex(uni)
    for name,fac in COMBOS.items():
        sc=None
        for j,(f,d) in enumerate(fac):
            rk=F[f].rank(pct=True,ascending=d)
            filled=rk if j==0 else rk.fillna(0.5)
            if j==0: core=rk.notna()
            sc=filled if sc is None else sc+filled
        sc=sc.where(core)
        top50=sc.nlargest(50).index
        rows.append((dates[i],name,float(ru.reindex(top50).mean()),float((ru.reindex(top50)-ru.median()).mean())))
df=pd.DataFrame(rows,columns=['date','combo','abs','exc'])
print(f"\n== 조합 top50 h20 (23앵커 복리·비용 0.5%p) · 시장 EW중앙값 누적 {(np.prod(1+np.array(mkt)/100)-1)*100:+.1f}% ==")
for name,g in df.groupby('combo',sort=False):
    cum=(np.prod(1+(g['abs'].values-0.5)/100)-1)*100
    print(f"{name:24} 평균 {g['abs'].mean():+.2f}%/20d · 초과 {g['exc'].mean():+.2f} · 누적 {cum:+.1f}% · 적중 {(g['abs']>0).mean()*100:.0f}%")
df.to_csv('/tmp/uswf/us_combos.csv',index=False)
