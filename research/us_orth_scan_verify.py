import re,sys
src=open('us_orth_scan_followup.py',encoding='utf-8').read()
# 캐시 빌드까지만 재사용: 'print("\n== S1' 이전까지 실행
head=src.split('print("\\n== S1')[0]
exec(head)
xs_mean, xs_med, xs_clip, xs_ex = [], [], [], []
for t8, idx, fwd, ms, F, amt, f in cache:
    g = idx[(f[90][0] >= 2) & (f[90][1] > 0)]
    if len(g) < 10: continue
    r = fwd[20][g]; u = fwd[20]
    xs_mean.append((r.mean()-u.mean())*100)
    xs_med.append((r.median()-u.median())*100)
    xs_clip.append((r.clip(-0.5,0.5).mean()-u.clip(-0.5,0.5).mean())*100)
    xs_ex.append((r.drop(r.idxmax()).mean()-u.mean())*100)   # 최대 1종목 제외
for lab, xs in [("mean",xs_mean),("median",xs_med),("clip±50%",xs_clip),("최대1종목 제외",xs_ex)]:
    m,lo,hi,n = bb(xs); print(f"{lab:14s} {m:+.2f}%p [{lo:+.2f},{hi:+.2f}] n={n} 적중={np.mean(np.array(xs)>0):.0%}")
